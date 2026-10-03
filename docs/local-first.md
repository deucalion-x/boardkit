# Local-first board with immediate writes

## Goal

Agents do project and task management against a local store. Reads answer from
that store — all of them but one, named below. Writes enter a durable queue and
boardkit immediately tries to push them. GitHub remains the real store; the local copy is a working copy.

## Why reads are local

An agent may ask ten questions while planning one change. Fetching the board for
every question would add latency and spend API quota without making the answer
materially fresher. Boardkit fetches once, answers reads from the snapshot, and
names its sync time so callers can decide when they need a new pull.

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
| An agent answering a question | `state` (plus one remote call for `board item`'s child list) | — |
| A change command | `state`, `pending`, remote target | `pending`, then `state` or `rejected` |
| `board sync` | remote project | `state` |
| `board sync push` | `state`, `pending`, remote targets | all three |

Read commands never call `gh`, with one exception: a board does not record
which issue is a sub-issue of which, so the child list in `board item` has to
ask. It is skipped with a note when GitHub cannot be reached, after the rest of
the item has already answered from the snapshot — the read still works offline,
it just answers less.

Change and sync commands use `gh` behind the boardkit interface.

## Conflict rule

Remote is the source of truth, and nothing is dropped silently.

Before a push, boardkit refreshes the items named by pending writes. If the
remote changed that same field since the snapshot the write was based on, the
write loses — it moves to `rejected` carrying both values, and the agent is
told. Otherwise it is pushed.

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

Boardkit attempts to drain the queue as part of each ordinary change. `--later`
deliberately skips that attempt, and `board sync push` retries whatever remains
after an offline or rate-limited run. There is no background daemon.

## Sync loop

1. Read the durable queue.
2. Fetch current values only for the items named by queued writes.
3. Compare each remote field with the value the write was decided against.
4. Move conflicts to `rejected` and batch the safe mutations into one request.
5. Remove every write that is finished with — the ones GitHub reports as
   successful, the ones that lost, and the ones the board already agreed with,
   which send nothing and so appear in neither list. Then patch `state` with
   the values that landed.

The snapshot carries project, item, field and option ids because GitHub's
mutations require them. A full `board sync` refreshes the complete snapshot;
a push fetches only its targets.

## Transport

Boardkit uses the authenticated `gh` command as its transport. Ordinary issue
and project operations use `gh` subcommands. A full pull and operations the CLI
does not expose—multi-select values, some field types, batched mutations and
saved views—go through `gh api graphql`. Boardkit therefore stores no GitHub
token and shares the account already selected by `gh`.

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
values are project-owned — `TEXT`, `NUMBER`, `DATE`, `SINGLE_SELECT`,
`MULTI_SELECT`, `ITERATION` — and the rest are projections of the issue,
read-only. `gh project field-create` accepts only four of the six (`TEXT`,
`SINGLE_SELECT`, `DATE`, `NUMBER`), so `admin apply` sends
`createProjectV2Field` itself and makes all six.

Creating is not setting: `board set` refuses an `ITERATION` field, so five of
the six can be written. Declaring one therefore gets a column nothing can
fill. Deferred rather than hidden — see
[#20](https://github.com/agerardin/boardkit/issues/20).

## Permission tiers

Each tier is gated before it acts, because a half-applied admin step leaves a
broken board:

| Tier | Needs | Commands |
|---|---|---|
| local | nothing | reads of `state`, appends to `pending`, `install-skill`, `--help` |
| read | `repo`, `read:project` | sync pull |
| write | `repo`, `project` | sync push |
| admin | `project` at the owner | `admin apply`, `field-create` |

Scopes come from `gh auth status`, which is local and needs no API call.
Visibility — can this account see the repo and the project — needs a probe, so
that is what gets cached, keyed by the scope string so a refreshed token
invalidates it with no expiry to tune.

Never run `gh auth login` or `gh auth refresh` automatically: both are
interactive and mutate global state. Print the command.

## Deliberate boundaries

- There is no background process. Commands attempt writes immediately, and
  the explicit push command retries durable work.
- The issue mirror remains separate from the snapshot because its purpose is
  review: issue-text changes should appear in a git diff.
- Repeated pending writes to one item field collapse to the newest value while
  keeping the original comparison value for conflict detection.
