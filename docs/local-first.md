# Local-first board with immediate writes

## Goal

Agents do project and task management against a local store. Reads answer from
that store — all of them but one, named below. Writes enter a durable queue and
boardkit immediately tries to push them. GitHub remains the real store; the
local copy is a working copy.

## Why reads are local

An agent may ask ten questions while planning one change. Fetching the board for
every question would add latency without making the answer materially fresher.
Boardkit fetches once and answers the rest from the snapshot.

What it does not do is let the snapshot age without limit. Being told to sync
does not work: a stale answer looks exactly like a current one, so the agent
has no way to notice that the instruction applies. A session once opened
against a 16-hour-old snapshot and came within one command of reporting that
board to a person as current.

So a read pulls first when the snapshot is older than `max_read_age_seconds`
(60 by default; 0 to disable), and answers from the snapshot otherwise. The
first read of a session pays for the pull and the rest are free, which makes
the promise an age rather than a request to remember: no read is more than a
minute out of date. `--offline` answers from the snapshot whatever its age, and
a pull that cannot reach GitHub says so and answers from disk rather than
failing the question.

Nothing polls and nothing is supervised. A background syncer would fetch for a
board nobody is looking at and bring a lifecycle with it — start, stop, crash,
stale lock, orphans after a reboot, two repositories contending for one
database.

The insight that makes the floor cheap rather than expensive: **a pull driven
by a read happens less often than a pull driven by a clock**, because work is
not continuous. A session reads the board in bursts — several questions while
picking up a card, then nothing for an hour while the work is done — so the
floor is reached a handful of times a day, where a 30-second poll fires 2,880
times whether anyone asked or not. Tying the pull to a question also puts it
where it is useful: the one read that mattered is the one that paid for it, and
a board nobody is asking about is never fetched at all.

## What a pull costs

Measured against a 37-item board, with `rateLimit { cost }` on the queries
themselves:

| | points | wall clock |
|---|---|---|
| one item by node id | 1 | 0.7s |
| 100 item ids, nothing else | 1 | 0.8s |
| a full `board sync` — items, bodies, labels, fields, views | **2** | 2.3s |
| the same board at `page_size = 10` (4 pages) | 4 | — |
| the same board at `page_size = 5` (8 pages) | 10 | — |

The budget is 5000 points an hour, so a full pull is 0.04% of it and a 60s
read floor spends at most 2.4% — and in practice far less, for the reason
above. Those last two rows are why `page_size` stays at GitHub's maximum:
smaller pages buy nothing and cost a point each. Cost tracks pages rather than board size, so a
1000-item board is about ten pages and twenty points — still 250 full pulls an
hour. **Quota does not bind at any board size this tool will meet.** Wall clock
is the only real cost, which is what the read floor is measured in.

What a pull brings back, per card: the project item's id, type and every field
value; and for an issue, its number, title, url, open/closed state, body,
labels, assignees and milestone. Once per pull, board-level: the project's ids,
every field definition with its options and their ids — a push needs all three
to write a single select — and the saved view names. One combined query covers
the board head and the first hundred items; each further hundred is another
page at about a point.

The rule behind those numbers is worth knowing before extending a query:
GraphQL cost counts the nodes a query could return, not the bytes it does.
A scalar on a node already being fetched is therefore free — which is why every
issue body comes down in the same pass, at 76% of the payload and none of the
quota. Fetching bodies lazily instead would cost a query per card read and lose
offline `board find`, which searches them. Add a scalar freely; weigh a
connection.

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

Reads answer from `state`. Changes append to `pending` and then land in
`state` or `rejected`. The exception is the child list in `board item`, which
has to ask GitHub because a board does not record which issue is a sub-issue
of which; it is skipped with a note when GitHub cannot be reached.

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

`sync.push` is the loop, and its docstrings carry the steps. The rule worth
stating away from the code is that a write leaves the queue only when it is
finished with — pushed, rejected, or already true on the board. A write that
is in none of those three stays, which is the invariant a bug broke once.

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

`data_type` uses GitHub's `ProjectV2FieldType` spelling. Which types this
repo can create and set is `CREATABLE` in `config.py` and the refusals in
`sync.value_literal` — not repeated here, because that is exactly the kind of
list that goes stale. `board schema` prints the configuration's shape.

## Permission tiers

Each tier is gated before it acts, because a half-applied admin step leaves a
broken board:

`board auth` prints the four tiers, what each needs, and which this login
has. Each is gated before it acts, because a half-applied admin step leaves a
broken board.

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
