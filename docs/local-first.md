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

So a read pulls first when the snapshot is older than `max_read_age_seconds`,
and answers from the snapshot otherwise. The first read of a burst pays for the
pull and the rest are free, which makes the promise an age rather than a
request to remember. `--offline` answers from the snapshot whatever its age,
and a pull that cannot reach GitHub says so and answers from disk rather than
failing the question.

The default is 300 seconds, chosen from measured read gaps rather than taste —
[pull-cost.md](pull-cost.md#how-often-a-read-actually-pulls) has the
distribution. Set it in the config file, or as `BOARDKIT_MAX_READ_AGE_SECONDS`
for one machine or one command; 0 disables it and restores reads that never
touch the network.

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

Measured, not estimated: a full `board sync` is **2 rate-limit points and 2.3s**
on a board that fits in one page, against a budget of 5000 points an hour.
Quota does not bind at any size boardkit will meet, which is why the read floor
above is affordable and why a pull brings back everything rather than a cheaper
subset.

[pull-cost.md](pull-cost.md) holds the layers, the per-page scaling, the three
pricing rules behind them, and how to re-measure — so that the next change to
what a pull fetches argues with numbers instead of re-deriving them.

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

Reads answer from `state`, with `pending` folded in — a write that has not
landed is still a decision this repository made, and an agent that sets a
field and reads it back must not be told the old value. The fold happens on the
way out (`cli.showing`) and is never saved: the file goes on meaning "what
GitHub last held", which is what keeps the conflict check honest and what lets
a pull replace it without consulting anyone. A read says how many queued
changes it is showing, because a value that is not on the board yet must not
look like one that is.

Changes append to `pending` and then land in `state` or `rejected`.

Nothing is exempt. The child list in `board item` was, because a board does not
record which issue is a sub-issue of which — so it asked GitHub on every
invocation, which the read-age window could not bound: there was no stored
value whose age it could check, and the cost grew with how many cards an agent
opened rather than with time. Each item now carries its own `parent`, one
scalar on nodes the pull already fetches, and `board.children` inverts it over
the snapshot. The direction stored is the one GitHub will give in bulk; the
direction asked is the other one, and having all the items makes it free.

## Conflict rule

Remote is the source of truth, and nothing is dropped silently.

Before a push, boardkit refreshes the items named by pending writes. If the
remote changed that same field since the snapshot the write was based on, the
write loses — it moves to `rejected` carrying both values, and the agent is
told. Otherwise it is pushed.

The agent's recourse is to re-decide against reality, not to retry blindly.
A human's edit in the web UI therefore always wins, which is the behaviour we
want: the human saw the board, the agent saw a copy.

A conflict costs one field and nothing else. Writes are judged one at a time,
so a batch of forty with one conflict is thirty-nine landed and one recorded —
a single mismatch must never strand the rest, and once did.

### The rejection is also a gate

Re-deciding is what we want and not what a loop does. Left as a log, a
rejection changes nothing about the next write to that field: the comparison
value is still the one that lost, so the second attempt passes the conflict
check and overwrites the remote change nobody looked at.

So a standing rejection refuses the next write to that same item and field
(`sync.contested`), naming both values. It refuses once: the refusal consumes
the rejection, and the same command repeated stands. That is how an agent says
"I have seen it and I still mean it" without a second verb existing for it.

**The limitation, stated rather than implied.** This stops a blind overwrite,
not a determined one. An agent that mechanically retries the same command gets
through on the second attempt, and that is the intended trade: knowing whether
the remote value was actually *read* would need acknowledgement state, carried
per field and invalidated on every pull, and the gate is not worth that. What
it buys is that the overwrite can no longer happen without the reason being
printed first. Human edits still win every first time; the second time is on
whoever repeated the command.

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
deliberately skips that attempt. Whatever remains after an offline or
rate-limited run is retried by the next refresh past the read-age window, which
pushes before it pulls: a landed change is then in the snapshot the pull
returns, instead of being compared against values that had already moved.
`board sync push` is asking for that sooner. There is no background daemon —
the retry is a step inside a command someone ran, so a queue is only ever
retried by work that was happening anyway.

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
