# Seed issues

The work boardkit knows about itself, to file once the board exists.
Format: `area | effort | status` then title, then body.

---
## auth | M | Ready
Replace the OAuth `repo` scope with a fine-grained PAT

The `gh` login currently holds the classic `repo` scope — full read/write on
every private repository of the account and of three organisations. boardkit
needs none of that: it reads issues, reads and writes Project fields, and
never touches repository contents.

A fine-grained PAT limited to this repository removes the blast radius
entirely, because without the Contents permission no token holder can modify
code at all.

Acceptance:
- a PAT scoped to the single repo, Issues RW + Pull requests R + Projects RW
- `GH_TOKEN` exported, `gh auth logout`, and the OAuth grant revoked in
  Settings -> Applications
- `board auth` reports the fine-grained token and `board sync` still works

Note: `board auth` already handles a token that reports no scopes, so this is
a configuration change rather than a code change.

---
## sync | M | Ready
Verify `sync.pull` against a real project

`parse_fields` was written against an assumed shape for
`gh project field-list --format json` and has never run. It is covered by a
fixture, which proves only that it is self-consistent.

Acceptance:
- a real `board sync` completes and `state.json` carries field ids and option
  ids
- the fixture in `tests/test_sync.py` matches the real payload, or is
  corrected to

---
## sync | M | Inbox
Drain the queue without being asked

`board sync push` is manual, so a queued change sits until someone runs it.
The design says the queue should drain when an agent finishes its changes.

Open: a git hook, a timer, or an explicit call at the end of an agent turn.
A timer is the fallback for a process that dies mid-turn.

---
## admin | M | Inbox
Put items on the board

`board set` can change a field on an item that is already there. Nothing adds
an item, so a new issue has to be filed and added by hand.

`gh project item-add` and `gh project item-create` cover it. Queue the
addition locally like any other change.

---
## sync | S | Inbox
`push` pulls twice

`sync.push` pulls once to check for conflicts and once afterwards to pick up
what it just wrote. The second could be avoided by applying the pushed values
to the snapshot in memory.

Worth doing only if a push is slow enough to notice.

---
## docs | S | Inbox
Decide what `issue_mirror` is still for

`state.json` is a better snapshot than the mirror, so the mirror's only
remaining purpose is making a reworded acceptance criterion show up as a diff
in git — which the snapshot cannot do, being gitignored.

Either keep it for that one purpose and say so, or drop it.

---
## tests | S | Inbox
Prune the suite

The suite grew to 84 tests across several reshapes. Some assert behaviour that
has since moved. Read it once as a whole and delete what no longer earns its
place.

---
## config | S | Inbox
Publish boardkit so the lockfile resolves elsewhere

Both consumers point at a local path. Until the package is pushed, no other
machine can resolve it.
