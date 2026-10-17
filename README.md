# boardkit

Query a GitHub Project board from the terminal, and keep its issues reviewable
from git. Extracted from the Places repository, where the same three scripts
had the project number, the custom field names and the docs path edited into
them.

A pull snapshots the project once and reads answer from that snapshot, so they
cost no network and no API quota — bar the first of a burst, because a read
refreshes a snapshot older than `max_read_age_seconds` (300 by default) before
answering. Every scalar setting can also be given as `BOARDKIT_<SETTING>`,
which wins over the file — except which repository and board this is, which
only the committed config says, so a command cannot be aimed at another board
by mistake. A change is sent as
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

In Claude Code, once per machine:

```text
/plugin marketplace add deucalion-x/boardkit
/plugin install boardkit@boardkit
```

That is all. The plugin carries the `board` and `board-admin` skills and the
`board` command they run, at one version. The first `board` sets itself up —
it fetches boardkit at the plugin's version, and `uv` and `gh` if the machine
has neither — in a few seconds, into `~/.cache/boardkit`. It needs only bash
and curl. The plugin updates itself like any other, skills and command
together, so nothing is installed or committed per repository.

The same commands work from a terminal as `claude plugin marketplace add …`
and `claude plugin install …`, for an editor where `/plugin` is not offered.

Without the plugin — Codex, another agent, or a machine without Claude Code —
`install.sh` installs the same two things the same way, for the user:

```bash
git clone --depth 1 https://github.com/deucalion-x/boardkit /tmp/boardkit
bash /tmp/boardkit/install.sh          # the latest release; or give a version
```

It puts the plugin's launcher on PATH as `~/.local/bin/board` and writes the
skills to `$CODEX_HOME/skills` (`~/.codex/skills`), and to `~/.claude/skills`
when Claude Code is there without the plugin. Nothing goes into a repository.
`board self update` installs the latest release again, command and skills
together; `bash ~/.local/share/boardkit/install.sh --uninstall` removes both.

`board doctor` says which `board` runs, where the skills come from, and what
disagrees: two `board` commands on PATH, skills written by another release,
repository copies the plugin already carries. `board` itself mentions these
on stderr once a day, so an agent hears about them too.

## Getting started

From nothing to an agent working a board. Each step is once per repository
unless it says otherwise.

1. **An organisation owns the repository and its board.** GitHub gives apps
   and fine-grained tokens no access to a personal account's board. A free
   organisation is enough.
2. **Configure the repository:** `board init` writes `owner`, `repo` and the
   board's title. Add `--preset pm` for a board that tracks Initiatives
   along time; see [Presets](#presets).
3. **Create the board**, if it does not exist yet: `board admin apply --as-me`.
   Creating a project is beyond any one repository, so it uses your own `gh`
   login, once.
4. **Give the repository its own GitHub App:** `board auth app create`. It
   opens GitHub with the app already defined (`boardkit-<org>-<repo>`,
   private, no webhook, the permissions an agent needs), you click Create,
   and boardkit records its client ID in the config, keeping none of its
   secrets. Then tick **Enable Device Flow** on the app's page and install it
   on **this repository only**; the command opens both.
5. **Sign in,** once per machine: `board auth login`. boardkit now acts as you,
   via that app, and reaches this repository alone.
6. **Add the Claude Code settings** from [Permissions](#permissions), so your
   own login always asks first. The skills come with the plugin; see
   [Install](#install).
7. **First sync:** `board sync`. From here, reads answer from the cache.

Without an app, step 4 and 5 become `board auth setup`: a token for this
repository alone, regenerated every 90 days.

## Configure

Two files, each with one job.

**`project.toml` — which repository and board this is.** It is the whole
configuration for reading a board:

```toml
[project]
repo = "https://github.com/deucalion-x/places"     # or deucalion-x/places
board = "https://github.com/orgs/deucalion-x/projects/2"
app_client_id = "Iv23li..."   # the GitHub App this repository signs in through
```

boardkit reads those three keys from `[project]` and ignores the rest, so other
tools can keep their own keys in the same file. A key that looks like a
misspelt one of the three is warned about, and `board schema` lists every key
it ignored.

**`boardkit.toml` — the board's shape, only when it has one to state.** Its
columns, fields, options and views are discovered on the first sync, because
GitHub already knows them and a copy here could only drift. Write a
`boardkit.toml` (or `[tool.boardkit]` in `pyproject.toml`) only to declare
what `board admin apply` should create — custom fields, views, labels, the
organisation's issue types, column order — or to name a preset:

```toml
# boardkit.toml
[board]
preset = "pm"
```

The board is named by its URL, as it is copied from the browser: `orgs` or
`users`, the owner and the number are everything boardkit needs to find it.
A board that does not exist yet has no URL; `board admin apply` creates it and
writes `board = …` into `project.toml`.

Repos configured before `project.toml` keep working: identity in
`boardkit.toml` — `[board] url`, or `owner`, `owner_type` and `project` — is
still read, and may stay beside a `project.toml` while the two agree. When
they differ, boardkit refuses and names both files.

`board init` writes a starter `boardkit.toml`; in a repo with a `project.toml`,
it leaves the identity out of it.

### Who owns the board's shape

Neither config nor GitHub is the whole truth; each owns a different thing.

- **The board owns what it is now.** Every read and every agent decision uses
  the shape the last sync found. A column, field, option or view added in the
  browser is picked up and used; nothing has to be copied back into config.
- **Config owns the minimum a board must have:** the columns, the `blocked`
  field and the views the skills rely on. It is what `board admin apply`
  builds, not an inventory of the board.

So a vocabulary that grows with the work — a workstream's options, labels,
milestones — lives on GitHub and is created there as it is needed. Config
declares only what every board built from it should start with.

Issue types belong to the organisation, so every board in it shares them.
GitHub seeds an organisation with Task, Bug and Feature; the `work`, `pm` and
`portfolio` presets also declare Initiative, which their views filter on.
`admin apply` reads the types through the repository and names any missing.
Creating one takes an organisation admin, so it is done with `--as-me`, and
your `gh` login needs the `admin:org` scope once:
`gh auth refresh -h github.com -s admin:org`. A user's board has no types and
skips this.

When someone changes the board by hand:

| Change | What happens |
|---|---|
| Adds a column, field, option or view | Picked up on sync, silently: this is normal |
| Removes or renames something config declares | Every sync warns; `board admin apply` would recreate it, but only when someone runs it |
| Reorders the columns | A warning; reads follow the board's order |

Nothing is reverted on its own. A warning stays until a person decides: the
change was right, so config is updated to match, or it was not, so `board
admin apply` puts the standard back. An agent reports the warning and asks;
it does not settle it.

Phases are not a field: they are the repository's milestones, set per item
with `board set <n> milestone "<title>"`. A milestone needs no due date to
give order and progress; add one when there is a commitment to meet.

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

### Presets

A preset is a board shape boardkit ships, so a repo names it rather than
writing it out. Presets stack: each one adds to the one it builds on.

| Preset | For | Adds |
|---|---|---|
| `work` | Doing the work in the repository. What `board init` writes. | Columns Inbox, Todo, In Progress, In Review, Done; the `blocked` field; **Current** (BOARD, `-type:Initiative`: the work) and **All** (TABLE, unfiltered) |
| `pm` | Planning that work and reporting on it. Builds on `work`. | `start` and `target` (DATE); **Roadmap** (TABLE, `type:Initiative`, with sub-issues progress and milestone); **Timeline** (ROADMAP, `has:parent-issue`: the work along time); **Initiatives** (ROADMAP, `type:Initiative`: the roadmap along time) |
| `portfolio` | One organisation's roadmaps in one place; see [Portfolio](#portfolio). Builds on nothing. | `start` and `target` (DATE), `blocked`; **Roadmap** (ROADMAP: every Initiative along time); **Projects** (TABLE, open, with repository and sub-issues progress); **Blocked** and **Late**; each filtered to `type:Initiative` |

```toml
[board]
preset = "pm"     # work, then pm on top
```

`board init --preset pm` writes that line; plain `board init` writes `work`.
On a board that already exists, `board admin apply --preset pm` adds whatever
is missing and changes nothing else. `roadmap`, pm's first name, still works.

The repo's own `[board]` section wins: a column order, field or view it
declares replaces the preset's by name, and anything else it declares is
added. `board report` reads pm's `start` and `target` fields.

GitHub's API cannot set a view's grouping, sort or date fields, nor a
Project's workflows. So `board init` and `board admin apply` end with numbered
steps to do in the browser, each with a link: for `work`, close an issue when
its item reaches Done and auto-archive work closed as not planned; `pm` adds
grouping Timeline by Parent issue, sorting it by Start, and drawing both
time views from Start to Target. `board admin checklist` prints them again.

### Portfolio

A portfolio is an organisation's board whose items are the Initiatives of
other repositories' boards, so every project's roadmap is read in one place.
Each Initiative is still worked on its own board; the portfolio only shows it.
It lives in a small repository of its own, holding its boardkit config and
one GitHub Action:

```bash
board init --preset portfolio --as-me   # config, the Action, and the board
```

```toml
[board]
preset = "portfolio"

[portfolio]
repos = ["web-app", "data-pipeline"]   # or "org/name"
```

`board portfolio sync` is the only thing that changes it. It adds every
Initiative of the listed repositories, open or closed, and copies Status,
Start, Target and `blocked` from the board each one is on: those are Project
fields, held per board rather than on the issue. Sub-issues progress needs no
copy, since GitHub computes it from the issue. An issue that is no longer an
Initiative of a listed repository is removed; a draft note is left alone.
`-n` prints what it would change. The other verbs that change an item are
refused on a portfolio, since the next sync would undo them.

`board init --preset portfolio` writes `.github/workflows/portfolio-sync.yml`,
which installs the boardkit that wrote it and runs the sync every morning. It
needs a `BOARDKIT_TOKEN` secret: a
fine-grained token owned by the organisation, with Projects read and write and
Issues read on the listed repositories. An organisation's Project only holds
that organisation's issues, so a portfolio covers one organisation, and a
repository of another is refused in config.

`board schema` prints the full shape as JSON Schema — every setting, its type
and its default — so an agent can fill it in rather than guess.

### The rest

| Setting | For |
|---|---|
| `owner`, `owner_type`, `project_name` | a board not created yet; `url` replaces them |
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
| `board report [--json]` | Where each Initiative stands: sub-issues done / total, percent, start → target; the active ones with what is in progress or blocked; open items past their target. |

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
| `board log --usage [days]` | API calls and points spent per day and per command, and the share of reads answered without calling GitHub. |

The rest:

| Command | Does |
|---|---|
| `board init` | Write the three settings a repo must decide. |
| `board schema` | The configuration's shape, as JSON Schema. |
| `board admin apply` / `-n` | Add missing declared board shape without overwriting existing views. |
| `board admin apply --reconcile` | Also make declared view layouts and filters win; combine with `-n` to preview. |
| `board admin apply --prune-views` | Delete saved tabs config does not name. The only deletion; preview with `-n`. |
| `board admin link` | Attach the project to the repository. |
| `board portfolio sync` / `-n` | On a portfolio: add the listed repositories' Initiatives and copy their fields. |
| `board auth` / `--refresh` | Which credential is in use, and what it can see. |
| `board auth app create` | Create this repository's own GitHub App, and record its client ID. |
| `board auth login` / `logout` | Sign in through this repository's GitHub App. |
| `board auth setup` | Or store a token for this repository alone. |
| `--as-me` on any verb | Use your own `gh` login for one command; approved and logged. |
| `board mirror write` / `check` | The issue-text mirror kept in git. |
| `board links check` / `rewrite` | Sha-pinned document links in issue bodies. |
| `board install-skill` / `--check` | In boardkit, render the plugin's skills; elsewhere, name committed copies to delete. |
| `board doctor` | Which `board` runs, where its skills come from, and what disagrees. |
| `board self update` | Reinstall the latest release made by `install.sh`. |

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

What talking to GitHub cost is kept beside it as daily totals in
`.boardkit/usage.json`, for the last 90 days: reads answered from the snapshot
or after a pull, every `gh` call, and the points GitHub said each one cost.
`board log --usage [days]` prints them. The price comes from the calls
themselves — each query also asks for its own `rateLimit { cost }`, and each
`gh api` call returns its rate-limit headers — so measuring costs nothing.
Mutations and `gh` subcommands report no price and are counted as unpriced,
at least a point each. `BOARDKIT_LOG=off` turns the totals off with the log.

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

The simplest way in is to sign in through a GitHub App, named by
`app_client_id` in the repository's boardkit config:

```bash
board auth login    # shows a code; approve it on GitHub
board auth logout
```

boardkit then acts for you, marked "via" the app, on the repositories the
app is installed on, with only the app's permissions. The token lasts eight
hours and renews itself. The app works for organisation-owned boards; GitHub
offers no project permission on a personal account's board.

boardkit looks after the board and its issues, and nothing else. Code,
branches and pull requests are written through your own git and `gh`
credentials, or by a coding agent, never through boardkit. So the app asks
only for what boardkit's own calls use:

| Permission | boardkit uses it for |
|---|---|
| Issues: read and write | create, edit, comment, close, reopen, sub-issues, issue type |
| Organisation projects: read and write | moving cards, `board admin apply` |
| Pull requests: read | which pull request closes an issue, and a pull request card's review state |
| Checks and Commit statuses: read | a pull request card's checks; without them sync leaves that one field empty |
| Metadata: read | required by GitHub for every app |

### One app per repository, or one for the organisation

GitHub limits a sign-in to the repositories its app is installed on, and
boardkit files a sign-in in the keychain under the app's client ID. Which
repositories share an app therefore decides both what a sign-in reaches and
how many sign-ins there are. Two setups follow from that.

**One app per repository.** `board auth app create` makes it, named
`boardkit-<org>-<repo>`, and records its client ID. Install it on that
repository alone. Each sign-in reaches one repository's issues and its
organisation's boards. The cost is an app, an install and a `board auth
login` for every repository and machine.

**One app for the organisation.** Create one app by hand in the
organisation's settings, enable Device Flow on it, and install it on the
repositories that have a board ("Only select repositories", not "All", so a
new repository is reached only once someone adds it). Every repository sets
the same `app_client_id`, which `board init --app <client-id>` writes for a new
one. A new repository then needs one install and that one flag; a machine already signed in for any of them needs no new
sign-in, and `board auth logout` in one signs out of all of them.

The cost is that a sign-in reaches the issues of every repository the app is
installed on. Since the app holds no code permission, that is issues and
boards, not code. Give a shared app the same permissions as above, and no
more.

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
  "permissions": { "ask": ["Bash(gh *)", "Bash(board --as-me*)", "Bash(board * --as-me*)"] }
}
```

Every `--as-me` is recorded as a superuser action; `board log --superuser`
lists them: who used their own login, when, in which repository, for what.

For an organisation's board, `board auth setup` opens a pre-filled
fine-grained token form for one repository's board work: issues read-write,
pull requests read, and the organisation's projects. No code permission, and
nothing outside that repository and its organisation's boards. A **user-owned** board cannot be reached that way — fine-grained
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
- **boardkit's credential does not limit the agent's code work.** Pushes and
  pull requests go through your own git and `gh` credentials, so what stops a
  force-push or a deleted branch is branch protection on `main` (a paid plan
  for private organisation repositories), not boardkit.
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

Two skills: `board` for reading and changing work, and `board-admin` for
changing the board's shape. They ship in the Claude Code plugin (see
[Install](#install)) beside the `board` they run, so the two never disagree,
and they stay quiet in a repository with no board.

Skills are installed per user, never kept in a repository: boardkit runs on
a person's own machine, and a committed copy only drifted from the `board`
installed beside it. A repository that holds copies from an earlier boardkit,
under `.claude/skills/` or `.agents/skills/`, should delete them;
`board install-skill --check` and `board doctor` name them.

The skills' source is `src/boardkit/templates/skills/`; `board install-skill`
in this repository renders the plugin's copies under `plugin/skills/`.
