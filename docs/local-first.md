# Local-first board, with background sync

## Goal

Agents do project and task management against a local store. Reads never hit
the API. Writes are queued locally and pushed by a background sync. GitHub
remains the real store; the local copy is a working copy.

## What this replaces

boardkit's current principle is "filter at the server, then read" — every verb
narrows before it prints, and nothing is cached. That was right for a reader.
It is wrong for an agent that asks ten questions to plan one change: each one
costs a round trip, and `gh project item-list` fetches the whole board anyway.

So the principle inverts: fetch the board once, answer from the copy, sync in
the background. The README and the generated skill both state the old rule and
will need rewriting when this lands.

## The store

`.boardkit/` at the consuming repository's root — the same directory
`find_config` already walks up to. Gitignored: it is a cache, not a source.
Committing it would mean merge conflicts on a generated file and board state
mixed into code commits.

```
.boardkit/
  state.json      last synced snapshot of the project
  pending.json    local writes not yet pushed
  rejected.json   writes that lost to a remote change, with both values
```

Three files rather than one, because they have different lifetimes: `state` is
replaced wholesale on every pull, `pending` drains, `rejected` accumulates
until an agent reads it.

## Who reads what

| Actor | Reads | Writes |
|---|---|---|
| An agent answering a question | `state` | — |
| An agent making a change | `state`, `rejected` | appends to `pending` |
| The sync | `state`, `pending` | all three |

An agent never calls `gh`. That is the point.

## Conflict rule

Remote is the source of truth, and nothing is dropped silently.

On sync, for each pending write: if the remote changed that same field since
the snapshot the write was based on, the write loses — it moves to `rejected`
carrying both values, and the agent is told. Otherwise it is pushed.

The agent's recourse is to re-decide against reality, not to retry blindly.
A human's edit in the web UI therefore always wins, which is the behaviour we
want: the human saw the board, the agent saw a copy.

## Why writes queue at all

Not to spare the API. The GraphQL quota is 5000 points an hour and a mutation
costs about one, so a pull every minute all day spends about 5% of it and the
writes are noise beside that. Delaying a write to save quota would be solving
a problem nobody has.

The queue earns its place twice over instead:

- **Durability.** A change survives no network, a failed call, or a killed
  process. Work done offline pushes when the connection returns.
- **One pull per batch.** The conflict check needs a fresh snapshot, so pushing
  five writes one at a time costs five pulls and pushing them together costs
  one. The saving is in the pull, not the writes.

So the queue should drain promptly — when an agent finishes its changes, not on
a timer tuned for traffic.

## Sync loop

1. Pull the project: items, fields, and their ids.
2. For each entry in `pending`, compare the field's remote value against the
   snapshot it was based on. Changed → `rejected`. Unchanged → push.
3. Write the fresh snapshot to `state` and clear what drained from `pending`.

Push is `gh project item-edit --id … --field-id … --single-select-option-id …`,
so the snapshot must carry item ids, field ids and option ids. That is the one
hard requirement this design puts on the read path.

## Transport

`gh` subcommands, not GraphQL. `gh project` already covers the whole surface
we need: `item-list`, `field-list`, `item-create`, `item-add`, `item-edit`,
`item-archive`, `field-create`, `create`, `link`.

GraphQL is only needed for things `gh` does not expose — view creation
(`createProjectV2View`) and card ordering (`updateProjectV2ItemPosition`).
Neither is required for a usable board, so neither is in scope yet.

## Vocabulary

Mirror GitHub's own nouns rather than inventing parallel ones:

```
ProjectV2            -> Project        id, number, title, url, fields
ProjectV2Field       -> ProjectField   id, name, data_type, options
ProjectV2Item        -> ProjectItem    id, type, content, field values
```

`Status` is not a concept in GitHub's model — it is a `SINGLE_SELECT` field
conventionally named "Status". Config names it (`status_field`) rather than the
code assuming it, so a project whose column field is called "Stage" works.

`data_type` uses GitHub's `ProjectV2FieldType` spelling. Six of its twenty-one
values are project-owned and writable — `TEXT`, `NUMBER`, `DATE`,
`SINGLE_SELECT`, `MULTI_SELECT`, `ITERATION` — and the rest are projections of
the issue, read-only. `gh project field-create` accepts only four of the six
(`TEXT`, `SINGLE_SELECT`, `DATE`, `NUMBER`), so those are what `admin create`
can make; an iteration or multi-select field has to be added in the web UI or
through GraphQL.

## Permission tiers

Each tier is gated before it acts, because a half-applied admin step leaves a
broken board:

| Tier | Needs | Commands |
|---|---|---|
| local | nothing | reads of `state`, appends to `pending`, `install-skill`, `--help` |
| read | `repo`, `read:project` | sync pull |
| write | `repo`, `project` | sync push |
| admin | `project` at the owner | `admin create`, `field-create` |

Scopes come from `gh auth status`, which is local and needs no API call.
Visibility — can this account see the repo and the project — needs a probe, so
that is what gets cached, keyed by the scope string so a refreshed token
invalidates it with no expiry to tune.

Never run `gh auth login` or `gh auth refresh` automatically: both are
interactive and mutate global state. Print the command.

## Open

- What drains the queue: end of an agent's turn seems right, but nothing
  enforces it yet. A timer is the fallback for a process that dies mid-turn.
- `issue_mirror` becomes redundant: `state.json` is a better snapshot, and the
  mirror exists only to make issue-text changes reviewable in a diff. Keep it
  for that one purpose, or drop it?
- `pending` needs an ordering guarantee if two writes touch one field.
