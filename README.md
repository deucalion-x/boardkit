# boardkit

Query a GitHub Project board from the terminal, and keep its issues reviewable
from git. Extracted from the Places repository, where the same three scripts
had the project number, the custom field names and the docs path edited into
them.

The strategy the package exists to enforce is **filter at the server, then
read**. A full board dump buries the two or three rows that answer the
question, so every verb narrows before it prints.

Requires `gh`, authenticated. No Python dependencies.

## Install

```bash
uv add --dev boardkit            # once published
uv add --dev boardkit --editable ../boardkit    # from a checkout
```

## Configure

One table at the root of the consuming repository, in `pyproject.toml`:

```toml
[tool.boardkit]
owner = "agerardin"        # project owner login
project = "4"              # project number
repo = "agerardin/places"  # issues repository
owner_type = "user"        # or "org"; decides the project URL
status_order = ["Ready", "In progress", "Evaluating", "Inbox", "Blocked", "Done"]
fields = ["workstream", "effort", "priority", "horizon"]  # custom Project fields, in print order
group_by = "workstream"    # which field drives the rollup; defaults to the first
mirror_path = "docs/places/issue-mirror.md"   # omit to disable `board mirror`
docs_prefix = "docs/"      # which paths `board links` pins to a sha
project_name = "Places"
skill_name = "places-github"
skill_notes = ".claude/board-notes.md"        # appended to the generated skill
```

A repository with no Python packaging can put the same keys at the top level of
a `boardkit.toml` instead. Either file is found by walking up from the working
directory, so the commands work from anywhere inside the repo.

## Commands

| Command | Answers |
|---|---|
| `board` | Grouping-field-by-status rollup. Start here. |
| `board ready` | What is actually startable now. |
| `board group <name>` | One group, by status (`ws` is an alias). |
| `board item <n>` | One item plus its sub-issues. |
| `board find <terms>` | Full-text search, joined to board status. |
| `board json` | Normalized rows, for jq or python. |
| `board mirror write` / `check` | The issue-text mirror kept in git. |
| `board links check` / `rewrite` | Sha-pinned document links in issue bodies. |
| `board install-skill` / `--check` | Write the agent skill into `.claude/skills/`. |

For a question the verbs do not cover, pipe `board json` into jq or python
rather than growing a verb.

## What the three parts are for

**The board reader** answers "what should I work on" without a dump. Closed
issues stay on a Project board, so every view joins against open issues and
drops the rest.

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
