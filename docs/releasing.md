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

4. Merge the release change to `main` and confirm CI is green, including the
   `package` job.

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
