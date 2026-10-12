# Changelog

All notable changes to boardkit are recorded here. Releases follow
[Semantic Versioning](https://semver.org/) and use tags such as `v0.1.0`.

## 0.3.3 — 2026-10-12

### The roadmap beside the work
- Issue types: `board set <n> type <name>` and `board add ... type=<name>`
  set an issue's type as the organisation names it, and `board json` carries
  it as `issue_type`. A name the organisation does not have is refused with
  the ones it does.
- The starter board has three tabs: `Current`, the work without the
  Initiatives; `All`, everything; and `Roadmap`, the Initiatives alone with
  their sub-issue progress and milestone. An organisation without an
  `Initiative` type loses nothing: the filters hide nothing and show nothing.
- The `board` skill: an `Initiative` is never proposed as next work, its
  startable sub-issues are; `Task` is any work, a meeting with the customer
  included; and only a person decides what goes on the roadmap.

### Fixes
- A view can show GitHub's own fields — parent issue, sub-issues progress —
  which a declared view used to be created without, silently. A field name
  that matches nothing is now said.

## 0.3.2 — 2026-10-12

### Phases and workstreams
- `board set <n> milestone "<title>"` puts an item in a phase, and `""` takes
  it out; `board add ... milestone=<title>` creates it in one. A phase is the
  repository's milestone rather than a field: ordered, one per item, and
  usable without a due date.
- The starter template's example field is `workstream`, the topic an item
  serves, in place of the vaguer `area`.

### Who owns the board's shape
- Written down in the README and the `board-admin` skill: the board owns what
  it is now, and config owns the minimum a board must have. A manual change
  is picked up, or warned about, and never reverted on its own; an agent
  reports drift and asks rather than settling it.

### Fixes
- A change to an issue that GitHub refused — a milestone that does not exist,
  say — was reported as "kept, it goes with the next sync", though it had
  been dropped. It is now reported as refused, and the command exits 1.

## 0.3.1 — 2026-10-12

### Epics and work only a person can do
- The `board` skill keeps "what next" on startable work: an item with open
  children is a container, so its startable children are proposed instead,
  and an item labelled `human` is skipped when an agent picks its own work
  but listed when a person asks. "Where does the roadmap stand" is answered
  from the parents and their children's status.
- `board json` carries each item's `parent`, so both rules are one pass over
  it rather than a `board item` per row.

### Fixes
- `board reopen` takes the item out of the closing column. The Project's
  automation moves a closed issue to `Done` and nothing moved it back, so a
  reopened issue sat there while open. It now goes to the column after
  intake (`Todo` on the starter board), read from config or from the board.
- A failed visibility probe is asked again instead of cached. One failed call
  read as being refused and reported "cannot see the projects" on every
  command until the token changed.

## 0.3.0 — 2026-10-12

### Lighter writes
- A comment, edit, close, reopen, label or sub-issue change re-reads only the
  issue it touched, and so does a new issue, draft or promoted card. Each used
  to pull the whole board — every item and body on it — after landing. A
  write is now one call to make it and one small one to read it back; the
  whole board is pulled only by `board sync` and the read-age refresh, which
  are what find other people's changes.
- A new issue or draft no longer vanishes from the snapshot when a sync
  follows within seconds. GitHub lists a new item on the board a moment after
  adding it; until it does, boardkit reads it by id.
- The credential check no longer runs `gh auth status` on every sync. That
  command asks GitHub about every stored login — a second or more each time —
  so the check is now cached under a fingerprint of the token in use, read
  without a call, and repeated only when the token changes.
- GitHub's "Could not resolve to a node" is reported as the refusal it is,
  instead of as GitHub being unreachable.

### Usage
- `board log --usage [days]`: GitHub calls and points spent per day and per
  command, the lowest budget seen, and the share of reads answered without
  calling GitHub. Totals live in `.boardkit/usage.json` for 90 days.
- Every GraphQL query asks for its own `rateLimit { cost }` and every
  `gh api` call reads its rate-limit headers, so the measurement costs no
  extra request and no extra points.

## 0.2.0 — 2026-10-11

Agents work a repository through `board` alone, under that repository's own
credential; the person's `gh` login is an approved, logged escape hatch.

### Credentials
- boardkit's `gh` calls never use the personal login by default. In order:
  `BOARDKIT_TOKEN`, a sign-in through the repository's own GitHub App, or a
  token for that repository alone.
- `board auth app create` creates the repository's app through GitHub's
  manifest flow and records its client ID; no app secret is kept.
- `board auth login` / `logout`: device-flow sign-in per app, renewed
  silently. `board auth setup`: a per-repository token, as the fallback.
- `--as-me` on any verb uses the personal login for one command; creating a
  board requires it. Every use, and each approved raw `gh` call (through a
  Claude Code hook), is in `board log --superuser`.
- Sign-ins and tokens live in the macOS keychain, the Linux Secret Service or
  Windows Credential Manager, with no file fallback.
- Messages name the credential in use and how to renew it.

### Working the issue
- `board comment`, `board close [--not-planned]`, `board reopen`,
  `board edit --title/--body`, and `board set <ref> labels|assignees|parent`;
  `board add … parent=<n>` makes a sub-issue. They go through the sync queue,
  so they work offline and are sent by the next sync.

### The cache
- Closed items stay in the snapshot, with why and when they closed.
- Every issue of the repository is cached, archived cards included: read
  whole once, then refreshed with only what changed.
- Each item carries its comments and the pull requests that close it; pull
  request cards carry review, mergeability and checks.
- `board find` searches comments too and lists open work before closed.
- A partial answer from GitHub is an error, not an empty board.

### Removed
- `board gh`: agents use `board`; other repositories are the escape hatch.

### Also
- Columns use GitHub's spelling, `In Progress` and `In Review`.
- `admin apply -n` reports option renames; `--help` on a verb prints help.
- The contract test covers the issue verbs on an organisation's board.

## 0.1.3 — 2026-10-11

- Fix `board admin apply` failing while reading saved views: the query carried
  an extra closing brace, so GitHub rejected it.
- `board admin apply --reconcile` updates a view's layout, not only its filter.
- Add `board admin apply --prune-views`, which deletes saved tabs the config
  does not name.
- Give the starter board an unfiltered `Current` board and an unfiltered `All`
  table, showing the same tasks in two layouts.

## 0.1.2 — 2026-10-04

- Add `board promote <ref>`, which converts a draft card into an issue in
  place — keeping every field value and its column — so the lifecycle step the
  skill describes has a command behind it. Moving a draft past the intake
  column is refused by default and names it; `--anyway` does it regardless,
  and a repo declaring no `status_order` is not second-guessed at all.
- Start every listing with the board's URL, so an answer about work says which
  board it came from. Not on `board json`, which feeds jq.

## 0.1.1 — 2026-10-04

- Bound how stale a read may be: a read refreshes a snapshot older than
  `max_read_age_seconds` (300 by default) before answering, so freshness is a
  guarantee rather than something an agent has to remember. `--offline`
  answers whatever the age, 0 disables it, and a refresh that cannot reach
  GitHub answers from disk and says so.
- Take the sub-issue list from the snapshot. Each item now carries its own
  parent, picked up by the pull it was already making, and children are that
  relation inverted locally — removing the one read that always spent API
  quota, grew with how many cards were opened, and could not work offline.
- Retry the queue on the refresh a read was already paying for, pushing before
  it pulls. A write left behind by an offline run no longer waits for someone
  to remember `board sync push`.
- Show queued writes in reads, marked as queued. Setting a field and reading it
  back no longer reports the old value, which was worst offline.
- Make a standing rejection guard its field: the next write to a field whose
  last write lost is refused once, naming both values, and the refusal is
  consumed so a deliberate repeat stands. Stops a blind overwrite, not a
  determined one.
- Read any single-value setting from `BOARDKIT_<SETTING>`, which wins over the
  config file.
- License under MIT, and ship the file in both distributions.
- Derive the version from the git tag with hatch-vcs, so no written-down
  version can disagree with it.
- Fix the saved-view lookup sending an extra closing brace in its GraphQL
  document, which stopped `board admin apply` after fields were created.
- Build the live acceptance board from the same canonical template as
  `board init`, then add only the fields needed to exercise every data type.
- Install the generated `board` and `board-admin` skills for both Claude Code
  and Codex, and check both copies for drift.
- Define the work-item lifecycle in the `board` skill while keeping board
  shape and setup in `board-admin` and `AGENTS.md`.
- Add `board admin apply --reconcile` to update declared view filters
  explicitly. Ordinary apply remains additive and preserves existing views.

## 0.1.0 — 2026-10-03

First tagged version. Boardkit has been in internal use throughout; this is
the point at which its artifacts are built and verified reproducibly.

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
- Correct the documented command list, which still advertised `board ready`
  and `board group` after both were removed, and the claim that all six
  creatable field types can be set — `board set` refuses an `ITERATION`
  field, so declaring one creates a column nothing can fill (#20).
- Remove `docs/seed-issues.md`, a pre-board backlog that had become a second
  list disagreeing with the board. Its two live entries are #21 and #22.
