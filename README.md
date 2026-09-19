# jsonl-sieve

A small Python library and command-line tool for validating UTF-8 JSON Lines
without keeping the whole input in memory. It reports line-numbered errors,
counts top-level JSON kinds, and can count the types associated with each
top-level object key. Record values never appear in reports.

Requires Python 3.11 or newer. Runtime dependencies: Python standard library only.
Building a distribution uses setuptools.

## Run from a checkout

No installation is needed. Set `PYTHONPATH` to `src` from the project root:

```sh
PYTHONPATH=src python -m jsonl_sieve examples/events.jsonl --profile-keys
PYTHONPATH=src python -m jsonl_sieve examples/events.jsonl --format json
```

PowerShell:

```powershell
$env:PYTHONPATH = 'src'
python -m jsonl_sieve examples/events.jsonl --profile-keys
```

To install locally when desired, run `python -m pip install .`. This provides
the equivalent `jsonl-sieve` command. The package is not assumed to be published
on a package index.

Read stdin by omitting the file or passing `-`:

```sh
cat examples/events.jsonl | PYTHONPATH=src python -m jsonl_sieve - --format json
```

The example file contains synthetic records. Human output includes:

```text
Tracked object keys: 3
  "duration": null=1, number=2
  "event": string=3
  "ok": boolean=3
```

## Validation policy

Each physical line must contain exactly one JSON value. Objects, arrays, strings,
numbers, booleans, and null are all valid at the top level. LF and CRLF are accepted;
the last line need not have a newline. An empty input is valid with zero records.
A trailing newline does not create an additional empty line.

The tool rejects invalid UTF-8, invalid JSON, duplicate keys at any nesting level,
and the nonstandard numeric tokens `NaN`, `Infinity`, and `-Infinity`. Standard
JSON numeric syntax is validated without converting numbers to machine floats
or integers, so very large finite literals and exponents do not cause conversion
overflow. Container nesting is limited to 256 levels and excessive nesting is
reported as `nesting-too-deep`. A Python runtime configured with an unusually low
recursion limit can reject nesting earlier.

Blank lines contain only JSON whitespace (space, tab, or carriage return) after
removing the line ending. The default is `--blank-lines ignore`; choose
`--blank-lines reject` to count them as invalid. Unicode whitespace outside JSON's
whitespace grammar is invalid JSON, not a blank line.

A UTF-8 BOM is rejected by default. `--bom allow` permits it only at the very
beginning of the first physical line. A BOM elsewhere outside a JSON string is
invalid. The BOM's bytes count toward the line limit. A BOM-only first line is
then handled by the selected blank-line policy.

```sh
PYTHONPATH=src python -m jsonl_sieve examples/events.jsonl \
  --blank-lines reject --bom reject --max-line-bytes 65536 \
  --profile-keys --max-keys 100 --max-diagnostics 20 --format json
```

Exit codes are `0` for valid input, `1` for input with invalid lines, and `2` for
usage or I/O errors. Help and version requests exit `0`. Input validation errors
are included in the report on stdout; usage and I/O errors go to stderr. An I/O
error produces no partial validation report.

## Library

```python
from io import BytesIO
from jsonl_sieve import Config, profile

stream = BytesIO(b'{"count": 2}\n{"count": null}\n')
report = profile(stream, Config(profile_keys=True, max_keys=20))
assert report.valid
assert report.key_types["count"]["number"] == 1
assert report.key_types["count"]["null"] == 1
data = report.to_dict()  # JSON-serializable aggregate report
```

`profile()` consumes a blocking binary file-like stream with standard
`readline(size)` behavior. It leaves the stream open and propagates I/O errors to
the caller. Invalid configuration raises `ValueError`. A `Report` contains only
counts, optional key names, safe diagnostics, and the configured limits; it does
not return parsed records.

## Report and resource limits

| Option | Default | Meaning |
| --- | ---: | --- |
| `--max-line-bytes` | 1,048,576 | Maximum bytes in one physical line, excluding LF or CRLF; must be positive |
| `--max-keys` | 1,000 | Maximum distinct top-level keys retained in the optional profile; zero is allowed |
| `--max-diagnostics` | 100 | Maximum error descriptions retained; zero is allowed |

Reading never requests more than `max_line_bytes + 2` bytes at once. An oversized
line is drained in bounded chunks and contributes one invalid line; following
lines are still processed. Its JSON syntax and UTF-8 are not inspected. Input
size and line count are not capped: this is a streaming tool, so processing time
still grows with input size. Parsed objects and decoded text add memory overhead
on top of the byte limit. Extremely large user-selected limits can exhaust memory.

Key profiling is opt-in. It includes only top-level objects that passed all
validation, and reports the kind of each immediate value; it does not flatten
nested data. The first `max_keys` distinct keys encountered are retained, with
their counts continuing across later records. Other keys are not retained even
if repeated. `keys_truncated` signals this loss, and `untracked_key_occurrences`
counts occurrences, **not distinct skipped keys**. Output key names are sorted;
selection at the cap depends on input order. Key length is bounded by the line
limit; the report's key storage therefore scales with both limits.

Only one diagnostic is emitted per invalid line. When the diagnostic cap is
reached, validation continues and the exact `invalid_lines` count is preserved.
`diagnostics_omitted` reports how many line errors were not stored. Thus a report
can be invalid while its diagnostics list is empty. `valid_records` and `kinds`
count only valid, nonblank lines. `total_bytes` includes all input bytes, including
line endings, BOMs, and drained data. `total_lines` counts physical lines.

JSON reports have `schema_version: 1`, use deterministic ordering, and contain no
timestamps or file paths. Human output escapes key names to avoid terminal
control characters. Diagnostics use fixed messages, never raw input tokens,
exception messages, or duplicate key names. JSON error columns are one-based
Unicode character positions after removal of an allowed BOM.

Object **keys themselves may contain sensitive information**; leave
`--profile-keys` off if their disclosure is undesirable. The tool does not read
environment-based configuration, access the network, or create output files.
Redirect stdout when a saved report is needed.

## Development

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
```

PowerShell uses the same test command after setting `$env:PYTHONPATH = 'src'`.
Tests use only the standard library and synthetic input. See [CONTRIBUTING.md](CONTRIBUTING.md)
for contribution guidelines and [CHANGELOG.md](CHANGELOG.md) for release notes.
