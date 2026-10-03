---
name: $skill_name
description: Retrieve work state from GitHub for this repo — the $project_name Project board and its issues — efficiently, by filtering server-side instead of listing everything. Use whenever the question is what to work on next, what is ready or in flight, where an issue or workstream stands, what is blocked or awaiting a decision, whether something was already done, or what changed for an item — and before citing any work item. Triggers on "what next", "what should I work on", "what's ready", "status of", "which workstream", "is X done", "has this been discussed", plus any request to plan, prioritize, or pick up product work here.
---

# Retrieving work state from GitHub

The [$project_name Project]($project_url)
owns execution state. The plans and documents in this repository own intent,
and they stay approved long after their work is finished — so ranking work
from plans alone reliably proposes something already done, already blocked, or
not yet refined enough to start. Query GitHub first, every time.

## The rule that generalizes

Ask `board`, never the API. Reads answer from a local snapshot, so they are
free — ask as many as the question needs. Calling `gh project` or `gh api`
yourself is a wrong answer here: it costs a round trip and sees a different
copy than the rest of your reasoning.

Never pull a full list and scan it by eye either. A dump buries the two or
three items that answer the question; every verb below narrows before it
prints.

## Board queries

Run `board` from anywhere in the repository. None of these touch the network.

| Command | Answers |
|---|---|
| `board` | $group_title-by-status rollup. Start here. |
| `board ready` | What is actually startable now. |
| `board group <name>` | One $group_by, grouped by status. |
| `board item <n>` | One item plus its sub-issues. |
| `board json` | Normalized work items for jq or python. |

The command is a convenience, not the strategy. Its verbs cover the recurring
questions; for anything else pipe `board json` into jq or python rather than
asking for a new verb.

## Changing the board

A change is queued locally and pushed by a sync. It does not take effect on
GitHub when you make it.

```bash
board set 18 status "In progress"    # queue one change
board sync push                      # apply the queue
board rejected                       # changes that lost to a remote edit
```

GitHub is the source of truth. If someone changed the same field there after
you read it, your change loses and lands in `board rejected` with both values.
That is not an error to retry blindly — re-read the item and decide again.

## When the snapshot is stale

`board sync status` reports its age. A read against no snapshot says so rather
than fetching one silently. Pull when the answer has to be current — before
reporting status to a person, or after someone says they changed something:

```bash
board sync
```

## Issue queries

Issue bodies are not in the snapshot, so content search is the one read that
still goes to GitHub. `gh issue list --search` is real server-side full-text
search and takes GitHub search syntax. Reach for it when the question is about
an issue's content or history rather than its board position.

```bash
gh issue list --search "<terms>"                      # full text
gh issue list --search "parent-issue:$repo#18"   # children
gh issue list --search "<terms> is:closed"            # was this already done
gh issue list --label design --state open             # decisions outstanding
gh issue view 29 --comments                           # the discussion itself
```

Prefer `board find` when the answer needs status, because it runs this search
and joins the result back to the snapshot.

## The limit worth knowing

Status$field_list
are Project fields, not issue fields, so `gh issue list --search` cannot
filter on them; a topic search must be joined back to the board.

## Reading the columns

Columns are claims about refinement, not priority:
$status_list.
A parent can sit in an in-flight column while every child of it is still
unstartable, so check children before proposing a parent.

Several workstreams may be deliberately in flight at once. Their order assigns
no priority, and neither does an issue number. When none has been chosen,
present the viable choices and their consequences instead of inventing a global
priority.

## Citing an item

Always link to the issue, so it opens from wherever the recommendation is read:

```markdown
[#1](https://github.com/$repo/issues/1)
```

A bare `#1` is not acceptable. Give the status alongside the citation when
recommending work, so the reader sees why it was picked.
