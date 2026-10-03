# Releasing boardkit

Boardkit does not currently publish to a package registry. A version tag builds
both standard Python distribution formats, installs and smoke-tests each in
isolation, generates attestations, and retains the results as GitHub Actions
artifacts. Those verified artifacts can later be sent to PyPI or an internal
registry without coupling the build to either destination.

## Prepare a version

1. Choose the version with `uv version <version>`.
2. Move the relevant entries in `CHANGELOG.md` from `Unreleased` to a heading
   containing that version and the release date.
3. Run the same checks as CI:

   ```bash
   uv sync --locked --dev
   uv run ruff check src tests scripts
   uv run mypy
   uv run pytest -q
   uv build --no-sources
   ```

4. Run the live contract check (below) and record the result.
5. Merge the release change to `main` and confirm CI is green, including the
   `package` job.

## The live contract check

Every test in `tests/` mocks `gh`, so they assert the document boardkit would
send and never that GitHub accepts it. One command closes that gap against a
project it creates and deletes:

```bash
uv run python scripts/contract.py
```

It creates a throwaway project, applies a config declaring one field of every
creatable type, reads the shape back from GitHub, writes and reads every
writable field type on a draft and on a real issue, clears five of them,
asserts the one unwritable type is refused, and deletes the project and the
issue it made. `--keep` leaves them for inspection; `--no-issue` stays out of
the repository entirely and exercises drafts only.

It is not in CI and does not gate a release. Scheduled CI would need a token
that can create and delete projects, and an unmocked run against a live API is
flaky enough that a blocking gate would get ignored — which is worse than not
having one. So it is run by hand, and the result goes in the table below.

| Version | Run | Covered |
|---|---|---|
| unreleased | 2026-10-03, full, pass | Project created and deleted; all eight fields and the view read back from GitHub; six field types written and read back on both a draft and a real issue; five cleared; every value re-written to confirm an already-true write settles; an iteration write refused; the issue deleted. |

Add a row per release. A release whose row says the run failed, or that it was
skipped, is a release whose mutations were never confirmed against GitHub —
which is worth stating rather than leaving blank.

The first run found three things the mocked suite could not. The run that
confirmed their fixes is the row above — it re-writes every value a second
time, which is the assertion that an already-true write settles rather than
sitting in the queue forever.

- `align` keeps the options GitHub seeds a new project with, so the declared
  columns are a prefix of what the board carries rather than the whole list.
  Intended, and now asserted.
- A NUMBER comes back as a float: `3` written reads as `3.0`. Not only ugly in
  a column — a write is compared to the snapshot as text, so `size=3` could
  never be seen as already true and was re-sent on every push. Fixed in
  `board.as_text`, which drops the `.0` from a whole number.
- A write that was already true remotely never left the queue. It is in
  neither the applied nor the rejected list, and the queue was cleared by
  subtracting only those two — so it was retried on every push and counted by
  every listing, with nothing able to clear it. Fixed in `sync.push`.

## Tag

Create an annotated tag whose value exactly matches the version in
`pyproject.toml`, then push it:

```bash
git tag -a v0.1.0 -m "v0.1.0"
git push origin v0.1.0
```

The tag starts the `release artifacts` workflow. Download its retained wheel
and source distribution if they need to be passed to a registry. No job uploads
them to a package index automatically.

Verify a clean installation directly from the tag:

```bash
uv tool install "git+https://github.com/agerardin/boardkit.git@v0.1.0"
board --help
```
