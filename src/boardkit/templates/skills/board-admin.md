---
name: board-admin
description: Change the shape of this repository's GitHub Project board itself — add or rename a column, add a field or one of its options, add a saved tab or reconcile its layout and filter — by editing the `[board]` section of this repo's boardkit config and running `board admin apply`. Use when the vocabulary or view a work item needs does not exist yet. Triggers on "add a column", "new status", "add a field", "add an option", "rename a column", "add a view", "change a view", "set up the board", "create the project", and "the board has no".
---

# Changing the board

Work items are moved with `board set`; this is for when the value you need
does not exist. Both go through config: the board's shape is declared in
the repo's boardkit config and applied, never clicked together in the browser, so the next
repo that runs `board admin apply` gets the same board.

## What this board has now

`board` prints the items and the columns they sit in; `board json` carries
every field value. Read that before adding anything, so a field that exists
under another name is not added twice.

## Adding something

Edit the `[board]` section of the repo's boardkit config, then apply:

```toml
[board]
status_order = ["Inbox", "Todo", "In progress", "Done"]   # the columns, in order

[board.fields.area]
data_type = "SINGLE_SELECT"   # or TEXT, NUMBER, DATE, MULTI_SELECT, ITERATION
options = ["Core", "API", "UI", "Infra", "Docs", "Tests"]

[[board.views]]
name = "Current"
layout = "BOARD"
fields = ["status", "area"]

[[board.views]]
name = "All"
layout = "TABLE"
fields = ["status", "area"]
```

```bash
board admin apply -n                  # preview missing shape
board admin apply                     # add it; preserve existing views
board admin apply --reconcile -n      # preview declared view updates
board admin apply --reconcile         # make declared layouts and filters win
board admin apply --prune-views -n    # preview which stray tabs would go
board admin apply --prune-views       # delete tabs config does not name
board sync                            # pull the new shape into the snapshot
```

## What apply will and will not do

It adds: a missing field, a missing option, a missing view. It renames an
option that differs from config only in case, keeping the items in it.

By default, an existing view stays as GitHub holds it. `--reconcile` also
updates layouts and filters on declared views. Use it only when the board's
shape is deliberately managed from config; preview it with `-n` first.

`--prune-views` deletes the saved tabs config does not name, and is the only
deletion boardkit performs — a view holds no items and no field values, so
what is lost is the grouping and sorting config cannot express. It is a
separate flag from `--reconcile` and is never implied by it. A field, an
option and a column are never deleted. Ask before pruning a board you do not
own: a tab you did not declare may be someone's working filter, and GitHub has
no undo. Preview with `-n` and say which tabs would go.

## Native GitHub workflows

GitHub does not expose supported create or update operations for Project
workflows. Configure these once in the Project's **Workflows** settings:

- When Status becomes `Done`, close the issue.
- Auto-archive items matching `is:closed reason:"not planned"`.

These are mechanical consequences of the board shape. Work-item acceptance
still belongs to the `board` skill.

It never deletes. A field, option or view the board has and config does not is
left alone, because deleting an option discards the value every item holds in
it. So removing something is a decision taken in the browser, by someone who
can see what is in it.

Two options differing only in case are reported rather than merged: moving
items between columns is a change to contents, not shape.

## When a sync reports drift

`board sync` says when config declares a field or an option the board does
not have. Boards are edited in the browser, so this is ordinary, not an error.

Default to fixing the config: delete the line from `[board]`. Someone changed
the board deliberately, and recreating what they removed puts back a column
they wanted gone.

Run `board admin apply` instead only when the config line is new — written to
create something and not yet applied.

## After changing the shape

```bash
board sync
board install-skill      # the work skill names these columns; re-render it
```

A status renamed in config and applied leaves items where they were — the
option keeps its id. A status added is empty until something is moved into it
with `board set`.

## What belongs here and what does not

Shape only: columns, fields, options, saved tabs. Which item sits in which
column is work, not shape — that is `board set`, in the work skill.

Creating the project itself is the same verb: with no board at all,
`board init` writes the config and applies it in one go.
