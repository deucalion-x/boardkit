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
- Fix one queued write to a field the snapshot does not know taking the whole
  push down with it. `apply_all` raises before the document is built, so every
  other queued change was stranded. A push now re-reads the field definitions
  when some write needs them, and rejects a write whose field GitHub turns out
  not to have.
- Fix `board item` failing instead of answering when GitHub cannot be reached.
  The sub-issue list is the one read that is not local; it is now skipped with
  a note, after the rest of the item has already printed.
- Fix `board mirror` and `board links` silently looking at only the first 100
  open issues. Both passed `page_size` — a per-request page for the Project
  API — to `gh issue list --limit`, which is a total. They now scan up to 1000
  and say so if that is not all of them.
- Fix a timed-out `gh` or `git` printing a traceback. The 120s timeout was
  being set and never caught, so the one failure it was added to handle was
  the only one without a message. It is now reported as unreachable, and the
  two probes that treat failure as an answer answer no.
