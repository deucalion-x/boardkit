# boardkit

Query a GitHub Project board from the terminal, and keep its issues reviewable
from git. Extracted from the Places repository, where the same three scripts
had the project number, the custom field names and the docs path edited into
them.

A pull snapshots the project once; every read answers from that snapshot, so
reads cost no network and no API quota. A change is sent as it is made, and
queued locally if it cannot be. GitHub stays the real store; `.boardkit/` is a
working copy, and on a conflict the remote value wins.

This file is the manual. [docs/overview.md](docs/overview.md) is why it works
this way, and [docs/local-first.md](docs/local-first.md) is the design of the
store and the sync loop in detail.

Requires `gh`, authenticated. One dependency: pydantic, which is
the configuration schema rather than a validation layer bolted onto it.

## Install

```bash
uv add --dev boardkit            # once published
uv add --dev boardkit --editable ../boardkit    # from a checkout
```

## Configure

Three settings, which `board init` writes for you:

```bash
board init
```

```toml
# boardkit.toml
owner = "agerardin"
repo = "agerardin/places"
project_name = "Places 2"
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
name = "Open work"
layout = "BOARD"
filter = "-status:Done"
```

`fields`, `views` and `status_order` may be written under `[board]`, which is
what says in the file itself that they exist to build a board rather than to
reach one. They also work at the top level, and mean the same thing either
way; declaring one in both places is refused rather than silently resolved.

All six creatable types work. `gh project field-create` handles only four, so
boardkit sends `createProjectV2Field` itself.

Declaring a field does not hide the others: every field the board owns is
still carried, with declared ones printed first.

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
| `board ready` | The `Todo` column, minus anything with `blocked` set. |
| `board group <value>` | Items carrying that field value (`ws` is an alias). |
| `board item <ref>` | One item: fields, labels, full text, sub-issues. |
| `board find <terms>` | Titles, bodies and labels holding every word given. |
| `board json` | Normalized work items, for jq or python. |

The snapshot holds open items only: closed issues stay on a Project board, and
a pull joins against open issues and drops the rest. For closed work, or for
the discussion on an item, ask GitHub — `gh issue list --search "… is:closed"`,
`gh issue view <n> --comments`.

An item is named by its issue number, or — for a draft card, which has no
issue behind it — by the `~handle` the board prints. Either is accepted
wherever a reference is taken.

Changes are recorded locally, then sent:

| Command | Does |
|---|---|
| `board add <title> [field=value]` | Create an issue and put it on the board. `--body <text>` for its text, `--draft` for a card with no issue behind it. |
| `board set <ref> <field> <value>` | Change a field. Sent now; queued if it cannot be, or with `--later`. |
| `board sync` | Pull: replace the snapshot. |
| `board sync push` | Drain the queue against a fresh pull. |
| `board sync status` | Snapshot age, queue depth, rejections. |
| `board rejected` / `--clear` | Changes that lost to a remote edit, with both values. |
| `board log [n]` | What boardkit did, for audit and debugging. |

The rest:

| Command | Does |
|---|---|
| `board init` | Write the three settings a repo must decide. |
| `board schema` | The configuration's shape, as JSON Schema. |
| `board admin apply` / `-n` | Make the board match what this repo declares. |
| `board admin link` | Attach the project to the repository. |
| `board auth` / `--refresh` | What this `gh` login is allowed to do. |
| `board mirror write` / `check` | The issue-text mirror kept in git. |
| `board links check` / `rewrite` | Sha-pinned document links in issue bodies. |
| `board install-skill` / `--check` | Write the agent skill into `.claude/skills/`. |

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
board install-skill          # write .claude/skills/board/ and board-admin/
board install-skill --check  # fail if the committed copy has drifted
```

Two skills are written: `board` for reading and changing work, `board-admin`
for changing the board's shape. The names are the same in every repo.

The files are copied from the package, identical in every repo: they tell an
agent to run `board`, which answers for whichever repo it is standing in.
Commit them, and never edit them — change the template in boardkit and run
`install-skill` again.

See [docs/overview.md](docs/overview.md) for why a skill cannot simply ship
inside the package.
