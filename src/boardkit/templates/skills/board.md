---
name: board
description: Read and change work state for this repo — this repository's GitHub Project board and its issues — through the `board` command, which answers from a local snapshot so reads cost nothing. Use whenever the question is what to work on next, what is ready or in flight, where an item stands, what is blocked or awaiting a decision, whether something was already done, or what changed for an item; and whenever work is being created, re-prioritised, or moved between columns. Triggers on "what next", "what should I work on", "what's ready", "status of", "is X done", "has this been discussed", "file an issue", "add a task", "mark it done", "move it to", plus any request to plan, prioritize, or pick up product work here.
---

# Working the board

This repository's GitHub Project
owns execution state. The plans and documents in this repository own intent,
and they stay approved long after their work is finished — so ranking work
from plans alone reliably proposes something already done, already blocked, or
not yet refined enough to start. Ask the board.

## Reads are free after the first, so ask as many as you need

`board` answers from a snapshot on disk, so there is no reason to ration
questions. The snapshot is also never stale by more than a few minutes: a read
past that refreshes it first and then answers, which costs a couple of seconds
once and nothing for the reads that follow. `--offline` answers from the
snapshot as it stands, and `board sync status` names the floor in force.

So you do not have to remember to sync before reporting status to someone —
the read has already done it. Every read is local, the child list in
`board item` included.

Every listing starts with the board's URL. Pass it on when you report status
to a person: they may want to look at what you are describing, and nothing
else in your answer says which board it was.

Calling `gh project` or `gh api` yourself is a wrong answer here: it costs a
round trip and sees a different copy than the rest of your reasoning.

| Command | Answers |
|---|---|
| `board` | Every open item, by status. Start here. |
| | Startable work is the `Todo` column with nothing in `blocked`. |
| `board item <ref>` | One item: its fields, labels, full text, and children. |
| `board find <terms>` | Titles, bodies and labels containing every word. |
| `board json` | Every item as one row, for jq or python. An unset field is `""`. |
| `board views` | The saved tabs the board has. |

The snapshot carries each item's body, so `board item` shows the acceptance
criteria without a call. Do not fetch an issue to read it.

For a question the verbs do not cover, pipe `board json` into jq or python
rather than asking for a new verb.

## Naming an item

An issue is its number: `board item 18`. A draft card has no number and is
named by the handle the board prints, beginning with `~`, which `board add` prints: `board item ~g-ST5Q`.
Either works anywhere a reference is taken.

## Changing the board

A change is sent the moment it is made. It is also recorded first, so nothing
is lost if it cannot be sent.

```bash
board set 18 status "In Progress"         # one field
board set 18 <field> "<value>"
board add "Title" --body "..." status=Inbox   # a new issue, fielded in one go
board add --draft "A thought"             # a board card, no issue behind it
board promote ~a1b2c3                     # that card becomes an issue, in place
```

Create an **issue** for work that carries a plan or acceptance criteria — it
can be reviewed, linked from a commit, and closed by one. Create a **draft**
for an idea that has not earned that yet.

## Work-item lifecycle

The normal path is:

```text
draft in Inbox
  → issue in Inbox
  → Todo
  → In Progress
  → In Review
  → Done and closed
```

1. Draft cards are ideas and are allowed only in `Inbox`.
2. When an idea is selected for refinement or execution, convert it to an
   issue. Only issues move beyond `Inbox`. `board promote <ref>` is the
   conversion: it happens in place, so the fields the card already carries
   survive, and it gains a number, a URL and a place in `gh issue list`.
   Setting a draft's status past the intake column is refused and names this
   command; `--anyway` does it regardless, which is for when someone asks and
   not a way around this step.
3. Move a refined, unblocked issue to `Todo`.
4. Move it to `In Progress` before starting work.
5. Record the result and verification on the issue with `board comment <ref>
   "<text>"`, then move it to `In Review`. This means ready for judgement, not
   done.
6. Only a human accepts work. After explicit acceptance, record `Done`; the
   Project automation closes the issue. A person merging the pull request
   that closes an issue is that acceptance: the automation then moves the
   issue to `Done` on its own. Never merge a pull request yourself.

Close rejected, duplicate, or abandoned issues with `board close <ref>
--not-planned`. Never move them through `Done`; the Project automation
archives them.

The issue itself is changed through `board` too, never through `gh`:

```bash
board comment 18 "Verified: …"           # a result, a decision, a plan change
board edit 18 --title "…" --body "…"     # refine the plan
board set 18 labels "bug, ui"            # exactly these labels; also assignees
board set 18 parent 12                   # make it a sub-issue ("" detaches)
board add "Title" parent=12 status=Inbox # a new sub-issue
board close 18 --not-planned             # or --completed; board reopen 18
```

These are queued like `board set`: sent at once when GitHub answers, and
otherwise kept and sent by the next sync. "kept, offline" is not a failure.

An agent may record `Done` after a human accepts the work. It must not make the
acceptance decision.

A parent needs its own review. Accepted children do not accept the parent.

## When a change does not land

GitHub is the source of truth. If someone changed the same field there after
you read it, your change loses:

```bash
board rejected        # what lost, with both values and why
```

That is not an error to retry. Read the item again, decide again, and set it
again — or `board rejected --clear` if the remote value is right after all.

Only the field that lost is affected. Forty changes with one conflict are
thirty-nine applied and one recorded, so there is nothing to redo but the one.

Your next write to that field is refused once, telling you what the board
holds. That refusal is the prompt to go and look. Repeating the same command
sends it anyway — which is yours to mean, not something to do reflexively,
because the value you would be overwriting is a change someone else made.

A read shows your queued changes before they land and says how many it is
showing, so the field you just set reads back as you set it even offline.

If the output says a change was kept because the network or the rate limit was
in the way, it is queued, and the next read that refreshes the snapshot sends
it. Nothing is lost and nothing is yours to remember; `board sync push` is only
how you ask for it now rather than then.

## When the snapshot is stale

Reads keep it fresh on their own, so this is rarely yours to do. `board sync
status` reports its age and the floor it is measured against; `board sync`
refreshes it now — worth it right after someone says they changed something,
rather than waiting out the floor. A read against no snapshot at all says so
instead of fetching one behind your back.

## Done work, and what the snapshot does not hold

The snapshot keeps every item on the board, open or closed, with why it closed
(completed or not planned), and every other issue of the repository, including
cards the board has archived. So "was this already done" is a local question:

```bash
board find <terms>     # open matches first, then the closed ones
board item 29          # a closed item says so, with its full text
```

`board item` also shows an issue's pull requests ("#12 merged"), a pull
request's review, mergeability and checks, and an item's comments, where plans
get discussed and revised; `board find` searches the comments too. If an issue
of this repository is not in the snapshot, it does not exist.

Other repositories are not in it. Reaching them means plain `gh`, which runs as
the person's own full login, asks them first and is logged. That is the escape
hatch, for the person to approve case by case: not part of working the board,
and never a way to reach another repository on your own.

## The limit worth knowing

Status, and every other field the board carries, are Project fields and not
issue fields, so `gh issue list --search` cannot filter on them. That is why
the board is the place to ask about them.

## Reading the columns

Columns are claims about refinement, not priority — `board` prints the ones
this board has. A parent can sit in an in-flight column while every child of
it is still unstartable, so check children before proposing a parent.

Several areas of work may be deliberately in flight at once, and neither an
issue number nor a column assigns priority. When none has been chosen, present
the viable choices and their consequences instead of inventing one.

## Citing an item

Always link to the issue, so it opens from wherever the recommendation is read:

```markdown
[#1](https://github.com/<owner>/<repo>/issues/1) — the url `board` prints
```

A bare `#1` is not acceptable. Give the status alongside the citation when
recommending work, so the reader sees why it was picked.
