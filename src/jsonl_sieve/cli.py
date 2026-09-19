"""Command-line entry point."""

from __future__ import annotations

import argparse
from contextlib import nullcontext
import json
import os
import sys

from . import Config, Report, __version__, profile


def _positive(value: str) -> int:
    try:
        result = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("expected a positive integer") from None
    if result <= 0:
        raise argparse.ArgumentTypeError("expected a positive integer")
    return result


def _nonnegative(value: str) -> int:
    try:
        result = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("expected a nonnegative integer") from None
    if result < 0:
        raise argparse.ArgumentTypeError("expected a nonnegative integer")
    return result


def _human(report: Report) -> str:
    lines = [
        "VALID" if report.valid else "INVALID",
        f"Lines: {report.total_lines}; bytes: {report.total_bytes}",
        f"Valid records: {report.valid_records}; invalid lines: {report.invalid_lines}; "
        f"blank lines: {report.blank_lines}",
        "Top-level kinds: " + ", ".join(f"{key}={value}" for key, value in report.kinds.items()),
    ]
    if report.key_profile_enabled:
        lines.append(f"Tracked object keys: {len(report.key_types)}")
        for key, counts in report.key_types.items():
            summary = ", ".join(f"{kind}={count}" for kind, count in counts.items() if count)
            lines.append(f"  {json.dumps(key, ensure_ascii=True)}: {summary}")
        if report.keys_truncated:
            lines.append(f"Key profile truncated; untracked key occurrences: {report.untracked_key_occurrences}")
    for diagnostic in report.diagnostics:
        position = f"line {diagnostic.line}"
        if diagnostic.column is not None:
            position += f", column {diagnostic.column}"
        lines.append(f"{position}: {diagnostic.code}: {diagnostic.message}")
    if report.diagnostics_omitted:
        lines.append(f"Diagnostics omitted: {report.diagnostics_omitted}")
    return "\n".join(lines)


def _discard_failed_stdout() -> None:
    # A failed flush can leave data buffered. Redirect the descriptor so the
    # interpreter's final flush cannot emit another error or change exit 2 to
    # exit 120 after main has already handled the output failure.
    try:
        with open(os.devnull, "wb") as sink:
            os.dup2(sink.fileno(), sys.stdout.fileno())
    except (AttributeError, OSError, ValueError):
        # An embedded caller may supply a stream without a file descriptor.
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="jsonl-sieve", description="Validate UTF-8 JSON Lines and summarize record shapes."
    )
    parser.add_argument("path", nargs="?", default="-", help="input file, or - for binary stdin (default)")
    parser.add_argument("--format", choices=("human", "json"), default="human")
    parser.add_argument("--profile-keys", action="store_true", help="include top-level object key names and type counts")
    parser.add_argument("--blank-lines", choices=("ignore", "reject"), default="ignore")
    parser.add_argument("--bom", choices=("reject", "allow"), default="reject", help="allow permits a UTF-8 BOM only at the beginning of the first line")
    parser.add_argument("--max-line-bytes", type=_positive, default=1_048_576)
    parser.add_argument("--max-keys", type=_nonnegative, default=1_000)
    parser.add_argument("--max-diagnostics", type=_nonnegative, default=100)
    parser.add_argument("--version", action="version", version=f"jsonl-sieve {__version__}")
    args = parser.parse_args(argv)
    config = Config(
        max_line_bytes=args.max_line_bytes,
        max_keys=args.max_keys,
        max_diagnostics=args.max_diagnostics,
        profile_keys=args.profile_keys,
        blank_lines=args.blank_lines,
        bom=args.bom,
    )
    writing_output = False
    try:
        source = nullcontext(sys.stdin.buffer) if args.path == "-" else open(args.path, "rb")
        with source as stream:
            report = profile(stream, config)
        output = json.dumps(report.to_dict(), ensure_ascii=True, sort_keys=True, indent=2) if args.format == "json" else _human(report)
        writing_output = True
        sys.stdout.write(output + "\n")
        sys.stdout.flush()
    except (OSError, OverflowError):
        if writing_output:
            _discard_failed_stdout()
        # Do not print exception messages: they can include paths or input data.
        sys.stderr.write("jsonl-sieve: unable to read input or write output.\n")
        return 2
    return 0 if report.valid else 1
