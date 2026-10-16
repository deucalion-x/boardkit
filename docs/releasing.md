# Releasing boardkit

Boardkit does not currently publish to a package registry. A version tag builds
both standard Python distribution formats, installs and smoke-tests each in
isolation, generates attestations, and retains the results as GitHub Actions
artifacts. Those verified artifacts can later be sent to PyPI or an internal
registry without coupling the build to either destination.

## Prepare a version

The version is not written down. `hatch-vcs` derives it from the git tag, so
the tag is the only place a version exists and nothing can drift from it —
`pyproject.toml` once said 0.1.1 while the only tag was v0.1.0, which is the
failure this removes. A build between tags names its distance and its commit
(`0.1.1.dev9+g5250ad5`) rather than claiming to be a release.

So there is no version to choose here, only a changelog to settle and checks
to pass. Decide the number when you release.

Two things are decided, and written, first:

1. The entries under `Unreleased` in `CHANGELOG.md`.
2. This version's row in the table below: whether the live contract check ran
   (it is needed when a GitHub call changed) and what it covered, or why it
   was skipped.

Then one command does the rest:

```bash
GH_TOKEN=… uv run python scripts/release.py 0.3.24   # --dry-run to check first
```

It refuses without both of the above, off `main`, behind `origin/main`, with
other uncommitted changes, or for a version not after the latest tag. It then
moves the changelog heading, sets the plugin's version (its `bin/board`
installs that tag, and Claude Code updates a plugin only when the string
changes), runs the CI checks, commits and pushes, waits for CI on that commit,
and tags only once it is green — until the tag exists, a plugin that updated
to the new version could not install its `board`.

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
| 0.1.0 | 2026-10-03, full, pass | Project created and deleted; all eight fields and the view read back from GitHub; six field types written and read back on both a draft and a real issue; five cleared; every value re-written to confirm an already-true write settles; an iteration write refused; the issue deleted. |
| 0.3.0 | 2026-10-12, reused project 3, pass (84s) | Shape read back; six field types written and read back, five cleared, sprint refused; one issue created, placed, commented, edited, labelled, parented, closed and reopened, then deleted. The first run of the lighter writes failed: a sync seconds after `board add` did not list the new issue, which is now read by id until the board lists it. One later run failed once on a visibility probe and passed when repeated. |
| 0.3.1 | 2026-10-12, reused project 3, pass (78s) | Shape read back; six field types written and read back, five cleared, sprint refused; one issue created, placed, commented, edited, labelled, parented, closed and reopened, then deleted. Reopen leaving `Done` checked by hand on the sandbox board, whose config states no columns. |
| 0.3.2 | 2026-10-12, reused project 3, pass (85s) | Shape read back; six field types written and read back, five cleared, sprint refused; one issue created, placed, commented, edited, labelled, parented, closed and reopened, then deleted. Setting, removing and refusing a milestone checked by hand on the sandbox board; the contract does not cover milestones. |
| 0.3.3 | 2026-10-12, reused project 3, pass (84s) | As 0.3.2, plus the three template tabs read back with their filters. What the type filters match was checked through `items(query:)`: hiding a type hides only that type's items, keeps their children, and an unknown type hides nothing. |
| 0.3.5 | 2026-10-14, reused project 3, pass (93s) | Shape read back; six field types written and read back, five cleared, sprint refused; one issue created, placed, commented, edited, labelled, parented, closed and reopened, then deleted. Not covered live: `board init`'s owner-type lookup and the app owner check, which need an org repo and a browser; `board add labels=` passes `--label` to `gh issue create`, unit-tested only. |
| 0.3.24 | 2026-10-16, skipped | No GitHub call changed: only the release script was added. This release is its first real run. |
| 0.3.23 | 2026-10-16, fresh project 6 (`--no-issue`), pass | Drafts only. `admin apply` created the project and linked it to deucalion-x/boardkit in the same run; the later `admin link` found it linked. Project deleted. |
| 0.3.22 | 2026-10-16, skipped | No GitHub call changed: identity variables are refused at config load. `BOARDKIT_REPO=… board` refused live, naming the variable. |
| 0.3.21 | 2026-10-16, skipped | No GitHub call changed: the plugin is new packaging. `plugin/bin/board` was run with an empty cache and a PATH holding neither uv nor gh: it fetched both, installed boardkit, and read the live board. `claude plugin validate` passed for the marketplace and the plugin. |
| 0.3.20 | 2026-10-16, skipped; checked live on project 2 | No GitHub call changed: only where identity is read from. boardkit's own repo moved to project.toml plus a shape-only boardkit.toml; `board`, `board auth` and `board schema` read back as before. |
| 0.3.19 | 2026-10-16, reused project 3, pass | As 0.3.5, with the standing board named by `[board] url` in the declaration. Board creation recording its URL is unit-tested, not in this run; `board set` adding an off-board issue was checked live on boardkit #89. |
| 0.3.18 | 2026-10-16, reused project 3, pass | As 0.3.5, with the issue now created through the REST endpoint. The run used a personal token; the app path (Issues without Contents) was checked live on novatix-team/ehl-ai-frontdesk-avatar, where `board add` created #1–#3 through the org app. Labels in `admin apply` are not in the contract; checked on the same repo after release. |
| 0.3.17 | 2026-10-15, skipped; checked live on novatix-team project 18 | Only the portfolio views' filters changed. `board admin apply --reconcile` set all four and they read back as declared; the portfolio Action, with its own fine-grained token, ran green. |
| 0.3.16 | 2026-10-15, skipped | No GitHub call changed: the portfolio Action template drops the source token now that boardkit is public. Installing the tag with no credentials worked. |
| 0.3.15 | 2026-10-15, reused project 3, pass | As 0.3.5. The portfolio was checked live on a new project 5, built with `preset = "portfolio"` over boardkit and places: its four views read back with their filters, and `board portfolio sync` added boardkit's one Initiative and copied its Status. That run found GitHub refusing visible fields on a roadmap view, now left out. GitHub's default "Auto-add sub-issues to project" workflow brought #39's four sub-issues along, and the next sync removed them as not Initiatives; the checklist says to turn that workflow off. A new project listed an added item four minutes later; a second sync in that window adds it again, which GitHub treats as a no-op. |
| 0.3.14 | 2026-10-15, skipped | The app manifest asks for fewer permissions and `board init --app` writes `app_client_id`; neither changes a call the contract makes. Creating an app needs a browser and an org; the manifest and the written config are unit-tested. |
| 0.3.13 | 2026-10-14, skipped; checked live on project 4 | The write path itself is unchanged; it now finds closed cards. On project 4, `board set 10 status "In Review"` then `Done` on a closed issue both landed and read back after a sync. |
| 0.3.12 | 2026-10-14, reused project 3, pass on the third run | The template now names `preset = "work"`. Project 3's Roadmap tab, no longer declared, was pruned first; the shape then read back as Current and All with their filters. The first run failed at a `board sync`, the second at `board edit` on a GitHub "Something went wrong" error; neither touched what changed. |
| 0.3.11 | 2026-10-14, skipped; checked live on project 4 | Preset renamed `roadmap` → `pm`, with the old name kept as an alias. `board admin apply` with `preset = "pm"` on project 4 found every field present and printed the browser steps. |
| 0.3.10 | 2026-10-14, skipped; checked live on project 4 | The view query also reads each view's `number`. `board admin apply` on project 4 printed the four steps with links to views 4 and 5 and to the workflows page. |
| 0.3.9 | 2026-10-14, skipped; checked live on a consuming repository's board | One aliased `totalCount` added to the repository-issues query. Checked against GitHub on that board: the deleted #18 left the snapshot on the next sync, `board item 18` says it does not exist, and `board report` reads #11 as 0/5. |
| 0.3.8 | 2026-10-14, reused project 3, pass (154s); and `--preset roadmap --keep --no-issue`, created project 4, pass | Project 3 as 0.3.5, with the shape now read through `config.load`. Project 4 built from the template, then the roadmap preset applied over it: only Start, Target, Timeline and Initiatives were added, the checklist was printed, the five views read back with their layouts and filters, and a second apply found nothing missing. Kept for a look at the roadmap views. `board report` reads the snapshot only; checked on a consuming repository's board against the card's numbers. |
| 0.3.7 | 2026-10-14, skipped | Field lookup in `normalize` only; unit-tested through `flatten`. No code path that talks to GitHub changed since 0.3.5. |
| 0.3.6 | 2026-10-14, skipped | `board --version`, help text and the dev install's rebuild keys. No code path that talks to GitHub changed since 0.3.5. |
| 0.3.4 | 2026-10-12, skipped | Skill text only: types, the `dev` label, `Feature`. No code path that talks to GitHub changed since 0.3.3. Applied by hand on the TL board (project 14): 50 items typed, `dev` labelled, an unconfigured tab pruned. |

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

The tag is what decides the version, so this step is the release rather than a
record of one. Annotated, `v` plus the number:

```bash
git tag -a v0.1.0 -m "v0.1.0"
git push origin v0.1.0
```

The tag starts the `release artifacts` workflow, which builds and then checks
that the wheel it produced names exactly this tag — a development version there
means the build could not see the tag, and the release fails rather than
shipping something mislabelled. Both build jobs check out with `fetch-depth: 0`
for that reason: a depth-1 checkout carries no tags, and the version would come
out as a dev build of the previous release. Download its retained wheel
and source distribution if they need to be passed to a registry. No job uploads
them to a package index automatically.

Verify a clean installation directly from the tag:

```bash
uv tool install "git+https://github.com/deucalion-x/boardkit.git@v0.1.0"
board --help
```
