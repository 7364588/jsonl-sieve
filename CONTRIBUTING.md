# Contributing

Changes should preserve streaming behavior, safe diagnostics, deterministic
output, and the absence of runtime dependencies. Keep fixes focused and explain
which observable behavior changes.

1. Add a small synthetic input that reproduces the problem. Do not submit
   production logs, credentials, private identifiers, or personal data.
2. Add or update a regression test for behavior that could break.
3. Run `python -m unittest discover -s tests -v` with `PYTHONPATH` set to `src`.
4. Update the README for policy or report-format changes and add a changelog entry.

Changes to report fields should consider consumers of `schema_version: 1`.
If a proposal needs an unbounded data structure, describe and implement an
explicit limit before adding it. Diagnostics must not echo input data.

Bug reports should include the Python version, command flags, expected behavior,
and a minimal synthetic reproducer. Contributions are made under the MIT license.
