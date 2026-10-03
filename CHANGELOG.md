# Changelog

All notable changes to boardkit are recorded here. Releases follow
[Semantic Versioning](https://semver.org/) and use tags such as `v0.1.0`.

## Unreleased

- Prepare package metadata, documentation, and registry-neutral release
  artifacts for future public or internal distribution.
- Add `scripts/contract.py`, a live check that exercises every board mutation
  against a throwaway project it creates and deletes. The test suite mocks
  `gh`, so nothing until now confirmed GitHub accepts what boardkit sends.
- Add `python -m boardkit`, so a checkout can be run without being installed
  and without resolving to an installed copy on `PATH`.
- Fix a queued change that was already true on GitHub never leaving the queue.
  It could neither land nor be rejected, so it was retried on every push and
  reported as pending by every listing. Found by the first contract run.
- Fix a whole number field reading back as `3.0`. GitHub stores a NUMBER as a
  float, and a write is compared to the snapshot as text, so `effort=3` could
  never be seen as already true and was re-sent on every push. Found by the
  first contract run.
