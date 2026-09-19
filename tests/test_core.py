import io
import json
import unittest

from jsonl_sieve import Config, profile


class GuardedStream(io.BytesIO):
    def __init__(self, content: bytes, largest_read: int):
        super().__init__(content)
        self.largest_read = largest_read
        self.calls = 0

    def readline(self, size: int = -1) -> bytes:
        if not 0 < size <= self.largest_read:
            raise AssertionError("unbounded read")
        self.calls += 1
        return super().readline(size)


class ProfileTests(unittest.TestCase):
    def scan(self, content: bytes, **options):
        return profile(io.BytesIO(content), Config(**options))

    def test_empty_input(self):
        report = self.scan(b"")
        self.assertTrue(report.valid)
        self.assertEqual((report.total_lines, report.total_bytes, report.valid_records), (0, 0, 0))

    def test_all_top_level_kinds(self):
        content = b'{}\n[]\n"sample"\n2\ntrue\nnull\n'
        report = self.scan(content)
        self.assertEqual(report.kinds, dict.fromkeys(("array", "boolean", "null", "number", "object", "string"), 1))
        self.assertEqual(report.total_bytes, len(content))
        self.assertEqual(report.valid_records, 6)

    def test_crlf_and_final_line_without_newline(self):
        report = self.scan(b"{}\r\n[]\r\n42", max_line_bytes=2)
        self.assertTrue(report.valid)
        self.assertEqual(report.total_lines, 3)
        self.assertEqual(report.total_bytes, 10)

    def test_invalid_utf8_then_valid_line(self):
        report = self.scan(b'"\xff"\n{}\n')
        self.assertFalse(report.valid)
        self.assertEqual(report.valid_records, 1)
        self.assertEqual(report.diagnostics[0].code, "invalid-utf8")
        self.assertEqual(report.diagnostics[0].line, 1)

    def test_invalid_json_and_multiple_values(self):
        report = self.scan(b'{"a":}\n1 2\n')
        self.assertEqual(report.invalid_lines, 2)
        self.assertEqual([item.line for item in report.diagnostics], [1, 2])
        self.assertEqual(report.diagnostics[0].column, 6)

    def test_duplicate_keys_at_any_depth(self):
        report = self.scan(b'{"a":1,"a":2}\n{"outer":{"b":1,"b":2}}\n')
        self.assertEqual(report.invalid_lines, 2)
        self.assertTrue(all(item.code == "duplicate-key" for item in report.diagnostics))
        self.assertEqual(report.kinds["object"], 0)

    def test_escaped_duplicate_key(self):
        report = self.scan(b'{"a":1,"\\u0061":2}\n')
        self.assertEqual(report.diagnostics[0].code, "duplicate-key")

    def test_nonstandard_numbers_at_any_depth(self):
        report = self.scan(b'NaN\nInfinity\n-Infinity\n{"a":[NaN]}\n')
        self.assertEqual(report.invalid_lines, 4)
        self.assertTrue(all(item.code == "nonstandard-number" for item in report.diagnostics))

    def test_large_standard_numbers_do_not_overflow(self):
        report = self.scan(b"9" * 5_000 + b"\n1e99999999999999999999999999\n")
        self.assertTrue(report.valid)
        self.assertEqual(report.kinds["number"], 2)

    def test_blank_policy_is_explicit(self):
        content = b"\n \t\r\n{}\n"
        ignored = self.scan(content)
        rejected = self.scan(content, blank_lines="reject")
        self.assertEqual(ignored.blank_lines, 2)
        self.assertTrue(ignored.valid)
        self.assertEqual(rejected.invalid_lines, 2)
        self.assertEqual(rejected.valid_records, 1)

    def test_unicode_space_is_not_a_blank_json_line(self):
        report = self.scan("\u00a0\n".encode())
        self.assertEqual(report.diagnostics[0].code, "invalid-json")

    def test_bom_default_reject_and_opt_in(self):
        content = b"\xef\xbb\xbf{}\n"
        self.assertEqual(self.scan(content).diagnostics[0].code, "unexpected-bom")
        self.assertTrue(self.scan(content, bom="allow").valid)

    def test_bom_is_only_allowed_at_start(self):
        report = self.scan(b"{}\n\xef\xbb\xbf{}\n", bom="allow")
        self.assertEqual(report.diagnostics[0].line, 2)
        self.assertEqual(report.diagnostics[0].code, "unexpected-bom")

    def test_bom_counts_toward_line_limit(self):
        report = self.scan(b"\xef\xbb\xbf{}\n", bom="allow", max_line_bytes=2)
        self.assertEqual(report.diagnostics[0].code, "line-too-large")

    def test_huge_line_is_drained_with_bounded_reads(self):
        content = b"x" * 2_000_000 + b"\n{}\n"
        stream = GuardedStream(content, largest_read=66)
        report = profile(stream, Config(max_line_bytes=64))
        self.assertEqual(report.total_lines, 2)
        self.assertEqual(report.total_bytes, len(content))
        self.assertEqual(report.invalid_lines, 1)
        self.assertEqual(report.valid_records, 1)
        self.assertEqual(report.diagnostics[0].code, "line-too-large")
        self.assertGreater(stream.calls, 10)

    def test_oversized_final_line(self):
        report = self.scan(b" " * 100, max_line_bytes=8)
        self.assertEqual(report.total_lines, 1)
        self.assertEqual(report.total_bytes, 100)
        self.assertEqual(report.invalid_lines, 1)

    def test_line_limit_boundary_lf_and_crlf(self):
        for ending in (b"", b"\n", b"\r\n"):
            with self.subTest(ending=ending):
                self.assertTrue(self.scan(b"{}" + ending, max_line_bytes=2).valid)
                self.assertFalse(self.scan(b"{} " + ending, max_line_bytes=2).valid)

    def test_key_types_are_shallow_and_only_valid_records_count(self):
        report = self.scan(
            b'{"b":null,"a":1,"inner":{"hidden":true}}\n'
            b'{"a":"text","b":false}\n{"x":1,"x":2}\n',
            profile_keys=True,
        )
        self.assertEqual(list(report.key_types), ["a", "b", "inner"])
        self.assertEqual(report.key_types["a"]["number"], 1)
        self.assertEqual(report.key_types["a"]["string"], 1)
        self.assertNotIn("hidden", report.key_types)
        self.assertNotIn("x", report.key_types)

    def test_key_limit_retains_first_seen_keys_and_counts_skips(self):
        report = self.scan(b'{"a":1,"b":2}\n{"b":3,"a":false,"c":4}\n', profile_keys=True, max_keys=1)
        self.assertEqual(list(report.key_types), ["a"])
        self.assertTrue(report.keys_truncated)
        self.assertEqual(report.untracked_key_occurrences, 3)
        self.assertEqual(report.key_types["a"]["boolean"], 1)

    def test_zero_key_limit(self):
        report = self.scan(b'{"a":1}\n', profile_keys=True, max_keys=0)
        self.assertEqual(report.key_types, {})
        self.assertEqual(report.untracked_key_occurrences, 1)

    def test_diagnostics_are_capped_but_invalid_count_is_exact(self):
        report = self.scan(b"?\n" * 100, max_diagnostics=2)
        self.assertEqual(len(report.diagnostics), 2)
        self.assertEqual(report.diagnostics_omitted, 98)
        self.assertEqual(report.invalid_lines, 100)
        zero = self.scan(b"?", max_diagnostics=0)
        self.assertFalse(zero.valid)
        self.assertEqual(zero.diagnostics, ())
        self.assertEqual(zero.diagnostics_omitted, 1)

    def test_report_does_not_disclose_values_or_error_tokens(self):
        secret = "synthetic-secret-value-4927"
        content = (
            '{"token":"' + secret + '"}\n'
            '{"a":' + secret + '}\n'
            '{"' + secret + '":1,"' + secret + '":2}\n'
        ).encode()
        report = self.scan(content, profile_keys=True)
        serialized = json.dumps(report.to_dict())
        self.assertNotIn(secret, serialized)
        self.assertEqual(report.invalid_lines, 2)

    def test_keys_are_not_collected_by_default(self):
        report = self.scan(b'{"synthetic-private-key":1}\n')
        self.assertEqual(report.key_types, {})
        self.assertFalse(report.key_profile_enabled)

    def test_deep_nesting_is_reported_without_a_crash(self):
        report = self.scan(b"[" * 2_000 + b"0" + b"]" * 2_000 + b"\n{}\n")
        self.assertEqual(report.diagnostics[0].code, "nesting-too-deep")
        self.assertEqual(report.valid_records, 1)

    def test_depth_boundary_and_brackets_in_strings(self):
        self.assertTrue(self.scan(b"[" * 256 + b"0" + b"]" * 256).valid)
        self.assertFalse(self.scan(b"[" * 257 + b"0" + b"]" * 257).valid)
        text = json.dumps({"sample": '["\\' * 1_000}).encode()
        self.assertTrue(self.scan(text).valid)

    def test_library_leaves_stream_open(self):
        stream = io.BytesIO(b"{}\n")
        profile(stream)
        self.assertFalse(stream.closed)

    def test_configuration_rejects_invalid_values(self):
        for options in (
            {"max_line_bytes": 0}, {"max_line_bytes": True},
            {"max_keys": -1}, {"max_diagnostics": -1},
            {"blank_lines": "guess"}, {"bom": "guess"}, {"profile_keys": 1},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                Config(**options)

    def test_library_propagates_io_errors(self):
        class BrokenStream:
            def readline(self, size):
                raise OSError("synthetic failure")
        with self.assertRaises(OSError):
            profile(BrokenStream())


if __name__ == "__main__":
    unittest.main()
