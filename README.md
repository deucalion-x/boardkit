# boardkit

Query a GitHub Project board from the terminal, and keep its issues reviewable
from git. Extracted from the Places repository, where the same three scripts
had the project number, the custom field names and the docs path edited into
them.

A pull snapshots the project once and reads answer from that snapshot, so they
cost no network and no API quota — bar the first of a burst, because a read
refreshes a snapshot older than `max_read_age_seconds` (300 by default) before
answering. Every scalar setting can also be given as `BOARDKIT_<SETTING>`,
which wins over the file. A change is sent as
it is made, and
queued locally if it cannot be. GitHub stays the real store; `.boardkit/` is a
working copy, and on a conflict the remote value wins.

This file is the manual. [docs/overview.md](docs/overview.md) is why it works
this way, and [docs/pull-cost.md](docs/pull-cost.md) is what talking to GitHub
costs and what follows from it. The reasoning behind a particular decision is
in the docstring of whatever implements it, where it cannot drift from the code
it constrains.

Requires `gh`, authenticated. One dependency: pydantic, which is
the configuration schema rather than a validation layer bolted onto it.

## Install

The repository is private, so installing from GitHub needs access to it,
through an SSH key or `gh auth`:

```bash
uv tool install "git+ssh://git@github.com/deucalion-x/boardkit.git"
```

Or install a development checkout:

```bash
uv tool install --editable .
```

## Getting started

From nothing to an agent working a board. Each step is once per repository
unless it says otherwise.

1. **An organisation owns the repository and its board.** GitHub gives apps
   and fine-grained tokens no access to a personal account's board. A free
   organisation is enough.
2. **Configure the repository:** `board init` writes `owner`, `repo` and the
   board's title. Add `owner_type = "org"`.
3. **Create the board**, if it does not exist yet: `board admin apply --as-me`.
   Creating a project is beyond any one repository, so it uses your own `gh`
   login, once.
4. **Give the repository its own GitHub App**, named `boardkit-<org>-<repo>`:
   private, Device Flow enabled, no webhook, with Contents, Issues and Pull
   requests read and write, Actions, Checks and Commit statuses read-only, and
   organisation Projects read and write. Install it on **this repository
   only**, and put its client ID in the config as `app_client_id`. (#30 makes
   this one command.)
5. **Sign in,** once per machine: `board auth login`. boardkit now acts as you,
   via that app, and reaches this repository alone.
6. **Install the agent skills:** `board install-skill`, then commit them, and
   add the Claude Code settings from [Permissions](#permissions) so your own
   login always asks first.
7. **First sync:** `board sync`. From here, reads answer from the cache.

Without an app, step 4 and 5 become `board auth setup`: a token for this
repository alone, regenerated every 90 days.

## Configure

Three settings, which `board init` writes for you:

```bash
board init
```

```toml
# boardkit.toml
owner = "deucalion-x"
owner_type = "org"
repo = "deucalion-x/places"
project_name = "Places 2"
app_client_id = "Iv23li..."   # this repository's GitHub App
```

That is the whole configuration for reading a board. Its columns, its fields,
their options, types and order are discovered on the first sync, because
GitHub already knows them and a copy here could only drift. Config is for the
decisions GitHub cannot make: which account, which repository's issues, and
which project by title.

The number is deliberately absent. It is resolved from the title and
remembered in the snapshot, which is what lets `board admin apply` run twice
without making two projects. Set `project = "4"` only to pin an ambiguous
title.

### Declaring a board

Declare a field when `board admin apply` has to create it, or to fix print
order. Nothing else needs saying:

```toml
[board.fields.workstream]
data_type = "SINGLE_SELECT"   # or TEXT, NUMBER, DATE, MULTI_SELECT, ITERATION
options = ["Maps", "Planning", "Infra"]

[board.fields.sprint]
data_type = "ITERATION"
iteration = { start_date = "2026-10-05", duration = 7, iterations = 4 }

[[board.views]]
name = "Current"
layout = "BOARD"

[[board.views]]
name = "All"
layout = "TABLE"
```

`fields`, `views` and `status_order` may be written under `[board]`, which is
what says in the file itself that they exist to build a board rather than to
reach one. They also work at the top level, and mean the same thing either
way; declaring one in both places is refused rather than silently resolved.

All six types can be created — `gh project field-create` handles only four, so
boardkit sends `createProjectV2Field` itself. Five of them can then be set:
`board set` refuses an `ITERATION` field, so declaring one gets you a column
nothing can fill. Tracked as
[#20](https://github.com/deucalion-x/boardkit/issues/20).

Declaring a field does not hide the others: every field the board owns is
still carried, with declared ones printed first.

Ordinary `board admin apply` is additive: it creates a missing declared view
but preserves one that already exists. On a development board deliberately
managed from config, `board admin apply --reconcile` also updates the layout
and filter of declared views. Add `-n` to preview either mode. Neither mode
deletes anything.

`board admin apply --prune-views` is a separate opt-in that deletes the saved
tabs config does not name — the only deletion boardkit performs. It is offered
for views alone: a view holds no items and no field values, so losing one
costs the grouping and sorting that config cannot express, which is real but
is not data. A field or a column is never deleted, because the values and the
items in them would go too. Preview it with `-n` first; GitHub has no undo.

`board schema` prints the full shape as JSON Schema — every setting, its type
and its default — so an agent can fill it in rather than guess.

### The rest

| Setting | For |
|---|---|
| `owner_type` | `"org"` if an organisation owns the project |
| `status_field` | the column field's name, if the board does not call it Status |
| `status_order` | to assert the columns; empty means the board's own |
| `default_status` | the columns `admin apply` creates, with what each means |
| `blocked_field` | the field holding what is in the way; empty means nothing is |
| `mirror_path` | enables `board mirror` |
| `docs_prefix` | which paths `board links` pins to a sha |

Config is read from `.boardkit.toml`, `boardkit.toml` or a `[tool.boardkit]`
table in `pyproject.toml`, in that order — a dedicated file wins, because
writing one is the more explicit statement. The first one found wins outright;
they are not merged. Any of them is found by walking up from the working
directory, so the commands work from anywhere in the repo, and every error
about a setting names the file actually in use.

## Commands

Reads answer from the local snapshot and touch nothing:

| Command | Answers |
|---|---|
| `board` | Every open item, by status. Start here. |
| `board item <ref>` | One item: fields, labels, full text, sub-issues, comments, linked pull requests; whether and why it closed. |
| `board find <terms>` | Titles, bodies, labels and comments holding every word given; open work first, then closed. |
| `board json` | Normalized work items, for jq or python. |
| `board views` | The saved tabs the board has. |

Every one of them answers from the snapshot. A Project board does not record
which issue is a sub-issue of which, so each item carries its own parent
instead, taken from the same download; the sub-issue list is that relation read
the other way, computed over the snapshot. It used to be a search per
invocation — the only read that always spent quota, and the only one that could
fail for a reason the question had nothing to do with.

The snapshot keeps every item on the board, open or closed, and every other
issue of the repository, including cards the board has archived: `board`
lists open work on the board, while `board item` and `board find` answer about
all of it. Each item carries its latest 50 comments and the pull requests that
close it. An issue of this repository that is not in the snapshot does not
exist; only other repositories need plain `gh`, as the person, with approval.

An item is named by its issue number, or — for a draft card, which has no
issue behind it — by the `~handle` the board prints. Either is accepted
wherever a reference is taken.

Changes are recorded locally, then sent; one that cannot be sent waits for
the next sync:

| Command | Does |
|---|---|
| `board add <title> [field=value]` | Create an issue and put it on the board. `--body <text>` for its text, `--draft` for a card with no issue behind it. |
| `board set <ref> <field> <value>` | Change a field. Sent now; queued if it cannot be, or with `--later`. `labels`, `assignees` and `parent` change the issue itself. |
| `board comment <ref> <text>` | Record a result or a decision on the issue. |
| `board close <ref>` / `--not-planned` | Close as completed, or as not planned for dropped work. |
| `board reopen <ref>` | Reopen it. |
| `board edit <ref> --title t --body b` | Refine the plan. |
| `board sync` | Pull: replace the snapshot. |
| `board sync push` | Drain the queue against a fresh pull. |
| `board sync status` | Snapshot age, queue depth, rejections. |
| `board rejected` / `--clear` | Changes that lost to a remote edit, with both values. |
| `board log [n]` / `--superuser` | What boardkit did; `--superuser` lists each use of your own login. |

The rest:

| Command | Does |
|---|---|
| `board init` | Write the three settings a repo must decide. |
| `board schema` | The configuration's shape, as JSON Schema. |
| `board admin apply` / `-n` | Add missing declared board shape without overwriting existing views. |
| `board admin apply --reconcile` | Also make declared view layouts and filters win; combine with `-n` to preview. |
| `board admin apply --prune-views` | Delete saved tabs config does not name. The only deletion; preview with `-n`. |
| `board admin link` | Attach the project to the repository. |
| `board auth` / `--refresh` | Which credential is in use, and what it can see. |
| `board auth login` / `logout` | Sign in through this repository's GitHub App. |
| `board auth setup` | Or store a token for this repository alone. |
| `--as-me` on any verb | Use your own `gh` login for one command; approved and logged. |
| `board mirror write` / `check` | The issue-text mirror kept in git. |
| `board links check` / `rewrite` | Sha-pinned document links in issue bodies. |
| `board install-skill` / `--check` | Write the agent skills for Claude Code and Codex. |

For a question the verbs do not cover, pipe `board json` into jq or python
rather than growing a verb.

## The local store

`.boardkit/` at the repository root, gitignored — it writes its own
`.gitignore`, so the consuming repo needs no change:

```
state.json      last synced snapshot, with the ids a push needs
pending.json    local changes not pushed yet
rejected.json   changes that lost to a remote edit, with both values
auth.json       the cached visibility probe, keyed by token scopes
```

### What a sync costs

Measured with GitHub's dry-run pricing (2026-10-11): a page of 100 board items,
with comments, linked pull requests and field values, costs 5 points; the
repository's issues cost 4 per 100. Those are cached whole: the first sync
reads every one, and each later sync asks only for issues updated since the
last, usually none. A 1,000-item board is about 54 points per sync against an
hourly budget of 5,000, and a read refreshes at most every five minutes. Each page asks for about 36,000 nodes, under GitHub's 500,000
per-query limit.

## The log

Every action — queued, pushed, rejected, pulled, created, refused — is one JSON
object on a line in `.boardkit/log.jsonl`, rotated so it cannot grow without
bound. `board log [n]` prints the tail.

It is standard `logging`: records go to `boardkit.trail`, under the `boardkit`
namespace, at INFO. A host application that imports boardkit can capture or
silence all of it through that namespace alone —
`logging.getLogger("boardkit")` — and boardkit attaches nothing to the root
logger.

The file is a property of the run, not of the repo, so it is an environment
variable rather than a setting:

```bash
BOARDKIT_LOG=off board sync          # emit the records, keep no file
BOARDKIT_LOG=/tmp/audit.jsonl board sync   # keep it somewhere else
```

## Permissions

The simplest way in is to sign in through the repository's own GitHub App,
named by `app_client_id` in its boardkit config. Give each repository its own
app, installed on that repository alone: GitHub limits a sign-in to the repos
its app is installed on, so each sign-in reaches one repository. Once per
repository and machine:

```bash
board auth login    # shows a code; approve it on GitHub
board auth logout
```

boardkit then acts for you, marked "via" the app, on the repositories the
app is installed on, with only the app's permissions. The token lasts eight
hours and renews itself. The app works for organisation-owned boards; GitHub
offers no project permission on a personal account's board.

Without an app, boardkit's `gh` calls run under a **token for this repository
alone**, never under your personal `gh` login, which reaches every repository
and board the account can. Set it up once per repository:

```bash
board auth setup   # create the token on GitHub, paste it; stored in the keychain
board auth         # says which credential is in use
```

`BOARDKIT_TOKEN`, when set, is used instead of the keychain (CI, Linux).
Without any credential, a command that needs GitHub is refused and names the
fix. Reads of the local store never need one.

Anything beyond one repository and its board, such as creating a project,
takes `--as-me`, which uses your own `gh` login for that command only. To make
an agent ask before it uses your login, add this to the repository's
`.claude/settings.json`:

```json
{
  "permissions": { "ask": ["Bash(gh *)", "Bash(board --as-me*)", "Bash(board * --as-me*)"] },
  "hooks": { "PostToolUse": [{ "matcher": "Bash", "hooks": [{ "type": "command",
    "if": "Bash(gh *)",
    "command": "jq -r '.tool_input.command' | board log --record-gh 2>/dev/null || true" }] }] }
}
```

Every `--as-me` is recorded as a superuser action, and the hook records each
approved raw `gh` command the same way. `board log --superuser` lists them:
who used their own login, when, in which repository, for what.

For an organisation's board, `board auth setup` opens a pre-filled
fine-grained token form for agent work on one repository: contents, issues and
pull requests read-write, Actions read, and the organisation's projects. It is
everything an agent needs to plan and do the work there, and nothing outside
that repository and board. A **user-owned** board cannot be reached that way — fine-grained
tokens offer no Projects permission on a personal account — so for one it opens
a classic token form with only the `project` scope (add `repo` for a private
repository). That reaches every board of the account, but no code.

### Tiers

`board auth` reports four tiers. Scopes come from `gh auth status` — local, no
API call — and visibility is probed once and cached, keyed by the scope string
so a refreshed token invalidates it with no expiry to tune.

| Tier | Needs | Covers |
|---|---|---|
| local | nothing | every read, `set`, `install-skill` |
| read | `repo` + `read:project` | `sync` |
| write | `repo` + `project` | `sync push` |
| admin | `project` | creating a project and its fields |

A refused tier prints the `gh auth refresh -s …` line that fixes it. boardkit
never runs `gh auth login` or `refresh` itself: both are interactive and mutate
global state.

### What this does not protect against

- **Projects access is organisation-wide.** GitHub has no per-board scope, so a
  repository's app can reach every board of its organisation. boardkit itself
  only touches the board its config names.
- **Inside its repository, the agent can do a lot**: everything the app's
  permissions allow. Branch protection on `main` (a paid plan for private
  organisation repositories) is what stops a force-push or a deleted branch.
- **The approvals and the log are Claude Code settings.** Another tool, or a
  script on the same machine, can still use your `gh` login or read the
  keychain. They are a guard for agents, not a security boundary.
- **Sign-ins live in the platform's credential store**: the macOS keychain,
  the Linux Secret Service (`secret-tool`), or Windows Credential Manager.
  Without one there is no file fallback; export `BOARDKIT_TOKEN` instead.

## The mirror and the links

`board mirror write` regenerates one Markdown file — `mirror_path` — holding
every open issue's body, so that a reworded acceptance criterion shows up as a
diff. It is regenerated, never edited; `board mirror check` fails when the file
and the issues disagree, which is what catches an edit made in the web UI.

`board links rewrite` turns `docs/…md` paths in issue bodies into blob URLs
pinned to the current sha, for the paths under `docs_prefix`; `check` reports
what is unpinned or stale. The sha has to exist on the remote, so push the
branch before filing the issue, and do not squash the merge.

## The agent skill

```bash
board install-skill          # write skills under .claude/ and .agents/
board install-skill --check  # fail if the committed copy has drifted
```

Two skills are written for each agent: `board` for reading and changing work,
and `board-admin` for changing the board's shape. Claude Code discovers them
under `.claude/skills/`; Codex discovers the identical files under
`.agents/skills/`. The names are the same in every repo.

The files are copied from the package, identical in every repo: they tell an
agent to run `board`, which answers for whichever repo it is standing in.
Commit them, and never edit them — change the template in boardkit and run
`install-skill` again.

See [docs/overview.md](docs/overview.md) for why a skill cannot simply ship
inside the package.
