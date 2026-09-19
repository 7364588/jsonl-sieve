"""Bounded line reading and strict JSON validation."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import BinaryIO, Literal

_KINDS = ("array", "boolean", "null", "number", "object", "string")
_BOM = b"\xef\xbb\xbf"
_MAX_NESTING = 256


@dataclass(frozen=True)
class Config:
    """Resource limits and validation policy; byte limits exclude LF/CRLF."""

    max_line_bytes: int = 1_048_576
    max_keys: int = 1_000
    max_diagnostics: int = 100
    profile_keys: bool = False
    blank_lines: Literal["ignore", "reject"] = "ignore"
    bom: Literal["reject", "allow"] = "reject"

    def __post_init__(self) -> None:
        for name, minimum in (
            ("max_line_bytes", 1), ("max_keys", 0), ("max_diagnostics", 0)
        ):
            value = getattr(self, name)
            if type(value) is not int or value < minimum:
                raise ValueError(f"{name} must be an integer >= {minimum}")
        if type(self.profile_keys) is not bool:
            raise ValueError("profile_keys must be a boolean")
        if self.blank_lines not in ("ignore", "reject"):
            raise ValueError("blank_lines must be 'ignore' or 'reject'")
        if self.bom not in ("reject", "allow"):
            raise ValueError("bom must be 'reject' or 'allow'")


@dataclass(frozen=True)
class Diagnostic:
    """A safe error description; never contains input tokens or object keys."""

    line: int
    code: str
    message: str
    column: int | None = None


@dataclass(frozen=True)
class Report:
    """Aggregate counts. Key profiles describe valid top-level objects only."""

    total_lines: int
    total_bytes: int
    valid_records: int
    invalid_lines: int
    blank_lines: int
    kinds: dict[str, int]
    key_types: dict[str, dict[str, int]]
    key_profile_enabled: bool
    keys_truncated: bool
    untracked_key_occurrences: int
    diagnostics: tuple[Diagnostic, ...]
    diagnostics_omitted: int
    limits: dict[str, int]

    @property
    def valid(self) -> bool:
        return self.invalid_lines == 0

    def to_dict(self) -> dict:
        """Return a JSON-serializable report with no record values."""
        result = asdict(self)
        result["schema_version"] = 1
        result["valid"] = self.valid
        return result


class _DuplicateKey(Exception):
    pass


class _NonstandardNumber(Exception):
    pass


class _NestingTooDeep(Exception):
    pass


class _JSONNumber:
    # Shape profiling needs only the kind, not a numeric conversion. This also
    # accepts syntactically valid huge integers/exponents without overflow.
    __slots__ = ()

    def __init__(self, _literal: str) -> None:
        pass


def _object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey
        result[key] = value
    return result


def _constant(_literal: str) -> None:
    raise _NonstandardNumber


def _check_depth(text: str) -> None:
    # Bound parser nesting consistently across Python versions. The JSON
    # decoder still validates the grammar; this pass only counts containers
    # outside strings, respecting escaped quotation marks and backslashes.
    depth = 0
    in_string = escaped = False
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
        elif character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > _MAX_NESTING:
                raise _NestingTooDeep
        elif character in "]}":
            depth -= 1


def _kind(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, _JSONNumber):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _read_line(stream: BinaryIO, limit: int) -> tuple[bytes | None, int]:
    """Return (content, bytes read); None marks an oversized line.

    An empty byte count denotes EOF. Never request more than limit + 2 bytes,
    including while draining a line too large to parse.
    """
    chunk = stream.readline(limit + 2)
    count = len(chunk)
    if not chunk:
        return b"", 0
    if chunk.endswith(b"\n"):
        content = chunk[:-1]
        if content.endswith(b"\r"):
            content = content[:-1]
        return (content if len(content) <= limit else None), count
    if len(chunk) <= limit:
        return chunk, count
    while chunk and not chunk.endswith(b"\n"):
        chunk = stream.readline(limit + 2)
        count += len(chunk)
    return None, count


def profile(stream: BinaryIO, config: Config | None = None) -> Report:
    """Consume a blocking binary file-like stream, leaving it open.

    The stream must provide standard ``readline(size)`` semantics. I/O errors
    propagate to the caller. Records are parsed one line at a time and discarded.
    """
    config = config if config is not None else Config()
    total_lines = total_bytes = valid_records = invalid_lines = blank_count = 0
    skipped_keys = 0
    kinds = dict.fromkeys(_KINDS, 0)
    keys: dict[str, dict[str, int]] = {}
    diagnostics: list[Diagnostic] = []

    def issue(code: str, message: str, column: int | None = None) -> None:
        nonlocal invalid_lines
        invalid_lines += 1
        if len(diagnostics) < config.max_diagnostics:
            diagnostics.append(Diagnostic(total_lines, code, message, column))

    while True:
        raw, consumed = _read_line(stream, config.max_line_bytes)
        if consumed == 0:
            break
        total_lines += 1
        total_bytes += consumed
        if raw is None:
            issue("line-too-large", "Line exceeds the configured byte limit.")
            continue
        if raw.startswith(_BOM):
            if total_lines == 1 and config.bom == "allow":
                raw = raw[len(_BOM):]
            else:
                issue("unexpected-bom", "UTF-8 BOM is not allowed at this position.")
                continue
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            issue("invalid-utf8", "Line is not valid UTF-8.")
            continue
        if not text.strip(" \t\r"):
            blank_count += 1
            if config.blank_lines == "reject":
                issue("blank-line", "Blank lines are disallowed by the selected policy.")
            continue
        try:
            _check_depth(text)
            value = json.loads(
                text,
                object_pairs_hook=_object,
                parse_constant=_constant,
                parse_int=_JSONNumber,
                parse_float=_JSONNumber,
            )
        except _DuplicateKey:
            issue("duplicate-key", "An object contains a duplicate key.")
            continue
        except _NonstandardNumber:
            issue("nonstandard-number", "Nonstandard numeric tokens are not allowed.")
            continue
        except json.JSONDecodeError as error:
            issue("invalid-json", "Line is not valid JSON.", error.colno)
            continue
        except (_NestingTooDeep, RecursionError):
            issue("nesting-too-deep", "JSON nesting exceeds the supported depth limit.")
            continue
        valid_records += 1
        kinds[_kind(value)] += 1
        if config.profile_keys and isinstance(value, dict):
            for key, child in value.items():
                if key not in keys:
                    if len(keys) >= config.max_keys:
                        skipped_keys += 1
                        continue
                    keys[key] = dict.fromkeys(_KINDS, 0)
                keys[key][_kind(child)] += 1
        # Do not retain the previous parsed record while reading the next line.
        del value

    return Report(
        total_lines=total_lines,
        total_bytes=total_bytes,
        valid_records=valid_records,
        invalid_lines=invalid_lines,
        blank_lines=blank_count,
        kinds=kinds,
        key_types=dict(sorted(keys.items())),
        key_profile_enabled=config.profile_keys,
        keys_truncated=skipped_keys > 0,
        untracked_key_occurrences=skipped_keys,
        diagnostics=tuple(diagnostics),
        diagnostics_omitted=invalid_lines - len(diagnostics),
        limits={
            "max_line_bytes": config.max_line_bytes,
            "max_keys": config.max_keys,
            "max_diagnostics": config.max_diagnostics,
            "max_nesting": _MAX_NESTING,
        },
    )
