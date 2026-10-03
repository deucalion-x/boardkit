# boardkit

Query a GitHub Project board from the terminal, and keep its issues reviewable
from git. Extracted from the Places repository, where the same three scripts
had the project number, the custom field names and the docs path edited into
them.

The strategy the package exists to enforce is **read locally, sync in the
background**. An agent that asks ten questions to plan one change should not
pay ten round trips for them, so a pull snapshots the project once and every
read answers from it. Changes queue locally and a push drains them. GitHub
stays the real store; `.boardkit/` is a working copy.

See [docs/local-first.md](docs/local-first.md) for the design, including the
conflict rule: remote wins, and a local change that loses is handed back
rather than dropped.

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
[fields.workstream]
data_type = "SINGLE_SELECT"   # or TEXT, NUMBER, DATE, MULTI_SELECT, ITERATION
options = ["Maps", "Planning", "Infra"]

[fields.sprint]
data_type = "ITERATION"
iteration = { start_date = "2026-10-05", duration = 7, iterations = 4 }
```

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
| `status_order` | to assert an order; empty means the board's own |
| `group_by` | which field drives the rollup; defaults to the first |
| `mirror_path` | enables `board mirror` |
| `docs_prefix` | which paths `board links` pins to a sha |
| `skill_name`, `skill_notes` | the generated agent skill |

Config is read from `.boardkit.toml`, `boardkit.toml` or a `[tool.boardkit]`
table in `pyproject.toml`, in that order — a dedicated file wins, because
writing one is the more explicit statement. Any of them is found by walking up
from the working directory, so the commands work from anywhere in the repo.

## Commands

Reads answer from the local snapshot and touch nothing:

| Command | Answers |
|---|---|
| `board` | Grouping-field-by-status rollup. Start here. |
| `board ready` | What is actually startable now. |
| `board group <name>` | One group, by status (`ws` is an alias). |
| `board item <n>` | One item plus its sub-issues. |
| `board json` | Normalized work items, for jq or python. |

Changes queue, then push:

| Command | Does |
|---|---|
| `board set <n> <field> <value>` | Queue one local change. |
| `board sync` | Pull: replace the snapshot. |
| `board sync push` | Drain the queue against a fresh pull. |
| `board sync status` | Snapshot age, queue depth, rejections. |
| `board rejected` / `--clear` | Changes that lost to a remote edit. |

The rest:

| Command | Does |
|---|---|
| `board init` | Write the three settings a repo must decide. |
| `board schema` | The configuration's shape, as JSON Schema. |
| `board admin apply` / `-n` | Make the board match what this repo declares. |
| `board admin link` | Attach the project to the repository. |
| `board auth` / `--refresh` | What this `gh` login is allowed to do. |
| `board find <terms>` | Full-text search. Hits GitHub: issue bodies are not in the snapshot. |
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

## What the three parts are for

**The board reader** answers "what should I work on" without a dump, and
without a round trip. Closed issues stay on a Project board, so a pull joins
against open issues and drops the rest.

**The mirror** (`board mirror`) writes open issue bodies into one Markdown file
in the repo. Nothing reads it to learn current state — that is the board's job.
It exists so a reworded acceptance criterion arrives as a reviewable diff. It
is regenerated, never maintained; `check` catches an edit made in the web UI.

**The links** (`board links`) rewrite `docs/...md` paths in issue bodies into
blob URLs pinned to a commit sha. An issue outlives any branch, so a link into
a branch rots when the branch is deleted. This holds only if the branch is
pushed before the issue is filed and merges are not squashed.

## The skill

A Claude Code skill is only loaded from `.claude/skills/`, so it cannot travel
as an importable module. `board install-skill` renders
`src/boardkit/templates/SKILL.md` — the part that is true for any repo — into
the consuming repository, and appends the `skill_notes` file verbatim for what
is true only of that project: its column meanings, label vocabulary, and the
issues to exclude from history. The generated file is committed, and
`board install-skill --check` fails when it has drifted.
