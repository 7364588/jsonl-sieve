# Maintenance

Changes should address a reproducible bug, a documented compatibility gap, or a concrete user need.
An unchanged project does not need a new commit or release merely because a week has passed.

Before a release:

1. Reproduce the issue using synthetic input and add an appropriate regression test.
2. Run `python -m unittest discover -s tests -v` after installing the package.
3. Confirm the command-line examples, exit codes, and supported platforms in the README.
4. Review distribution contents for local files, secrets, caches, and accidental personal data.
5. Record user-visible changes in the changelog and choose a version matching the change.

The scheduled CI job checks compatibility. It does not edit files, create commits, or publish packages.
Release timing depends on useful changes and passing checks; no response-time guarantee is made.
