import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def run_cli(self, *args, content=b""):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            [sys.executable, "-m", "jsonl_sieve", *args],
            input=content, capture_output=True, env=env, cwd=ROOT, check=False,
        )

    def test_stdin_json_success(self):
        result = self.run_cli("--format", "json", content=b'{"sample":1}\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b"")
        report = json.loads(result.stdout)
        self.assertEqual(report["schema_version"], 1)
        self.assertTrue(report["valid"])
        self.assertEqual(report["valid_records"], 1)

    def test_invalid_input_exit_one_with_zero_stored_diagnostics(self):
        result = self.run_cli("--format", "json", "--max-diagnostics", "0", content=b"?\n")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(json.loads(result.stdout)["valid"])

    def test_usage_exit_two(self):
        for args in (("--max-line-bytes", "0"), ("--max-keys", "-1"), ("--unknown",)):
            with self.subTest(args=args):
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 2)
                self.assertIn(b"usage:", result.stderr)

    def test_io_exit_two_without_echoing_path(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            missing = str(Path(temp) / "synthetic-secret-path.jsonl")
            result = self.run_cli(missing)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")
        self.assertNotIn(b"synthetic-secret-path", result.stderr)

    def test_closed_stdout_exits_two_without_shutdown_traceback(self):
        for output_format in ("human", "json"):
            for unbuffered in (False, True):
                with self.subTest(output_format=output_format, unbuffered=unbuffered):
                    result = self.run_with_closed_pipes(
                        "--format", output_format, closed=("stdout",),
                        content=b"{}\n", unbuffered=unbuffered,
                    )
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertEqual(
                        result.stderr.strip(),
                        b"jsonl-sieve: unable to read input or write output.",
                    )

    def run_with_closed_pipes(self, *args, closed, content=b"", unbuffered=False):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONUNBUFFERED"] = "1" if unbuffered else ""
        command = [sys.executable, "-m", "jsonl_sieve", *args]
        with subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=env, cwd=ROOT,
        ) as process:
            for name in closed:
                getattr(process, name).close()
                setattr(process, name, None)
            output, error = process.communicate(input=content, timeout=20)
        return subprocess.CompletedProcess(command, process.returncode, output, error)

    def test_help_and_version_with_closed_stdout_exit_two(self):
        for option in ("--help", "--version"):
            for unbuffered in (False, True):
                with self.subTest(option=option, unbuffered=unbuffered):
                    result = self.run_with_closed_pipes(
                        option, closed=("stdout",), unbuffered=unbuffered,
                    )
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertEqual(
                        result.stderr.strip(),
                        b"jsonl-sieve: unable to read input or write output.",
                    )

    def test_usage_and_io_errors_with_closed_stderr_exit_two(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            missing = str(Path(temp) / "synthetic-missing.jsonl")
            for arguments in (("--unknown",), (missing,)):
                for unbuffered in (False, True):
                    with self.subTest(arguments=arguments, unbuffered=unbuffered):
                        result = self.run_with_closed_pipes(
                            *arguments, closed=("stderr",), unbuffered=unbuffered,
                        )
                        self.assertEqual(result.returncode, 2)
                        self.assertEqual(result.stdout, b"")

    def test_both_output_pipes_closed_exit_two(self):
        for unbuffered in (False, True):
            with self.subTest(unbuffered=unbuffered):
                result = self.run_with_closed_pipes(
                    closed=("stdout", "stderr"), content=b"{}\n", unbuffered=unbuffered,
                )
                self.assertEqual(result.returncode, 2)

    def test_file_input(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            path = Path(temp) / "data.jsonl"
            path.write_bytes(b"{}\r\n[]")
            result = self.run_cli(str(path), "--format", "json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["valid_records"], 2)

    def test_human_output_escapes_control_characters_in_keys(self):
        result = self.run_cli("--profile-keys", content=b'{"a\\u001b[31m":"synthetic-secret"}\n')
        self.assertEqual(result.returncode, 0)
        self.assertNotIn(b"\x1b", result.stdout)
        self.assertIn(b"\\u001b", result.stdout)
        self.assertNotIn(b"synthetic-secret", result.stdout)

    def test_output_is_deterministic(self):
        for output_format in ("human", "json"):
            with self.subTest(output_format=output_format):
                options = ("--format", output_format, "--profile-keys")
                first = self.run_cli(*options, content=b'{"z":1,"a":false}\n?\n')
                second = self.run_cli(*options, content=b'{"z":1,"a":false}\n?\n')
                self.assertEqual(first.stdout, second.stdout)
                self.assertEqual(first.returncode, 1)

    def test_cli_blank_bom_and_line_limit_policies(self):
        result = self.run_cli("--bom", "allow", "--blank-lines", "reject", "--max-line-bytes", "5", "--format", "json", content=b"\xef\xbb\xbf{}\n\n123456\n")
        report = json.loads(result.stdout)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(report["valid_records"], 1)
        self.assertEqual([item["code"] for item in report["diagnostics"]], ["blank-line", "line-too-large"])

    def test_version(self):
        result = self.run_cli("--version")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), b"jsonl-sieve 0.1.0")


if __name__ == "__main__":
    unittest.main()
