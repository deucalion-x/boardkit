# What talking to GitHub costs

This exists so the question is answered once. Every time boardkit considers
fetching more, fetching less, or fetching on a timer, the same arithmetic gets
re-derived from guesses. The numbers below are measured, the method for
re-measuring them is at the bottom, and the conclusions are stated so they can
be disagreed with rather than rediscovered.

## The short answer

A full pull of a board that fits in one page costs **2 rate-limit points and
2.3 seconds**. The budget is 5000 points an hour. Quota does not bind at any
board size boardkit will meet; wall clock is the only cost worth designing
around.

So: pull everything, pull it in one query, pull it when a read needs it.

## The layers

What a pull could ask for, cheapest first. Measured on a 37-item board, one
page, with `rateLimit { cost }` inside the query being measured.

| Layer | What comes back | Points | Wall clock | What it can answer |
|---|---|---|---|---|
| **L0** membership | item ids | 1 | 0.8s | what is on the board; what was added or removed. Not status |
| **L1** the board as a board | + number, title, url, open/closed, every field value | 1 | ~1s | everything `board` prints. Not text search, not acceptance criteria |
| **L2** + classification | + labels, assignees, milestone | 3 | ~1.5s | grouping by label, who holds what |
| **L3** + text | + issue and draft bodies | 3 | 2.3s | `board item` and `board find` without a network call |
| head, once per pull | project ids, field definitions with option ids, view names | 1 | — | required for any write: a single-select push needs the field id and the option id |

Boardkit pulls **head + L3**, and the measured end-to-end is **2 points** — less
than the 1 + 3 the parts cost separately.

## Three rules behind those numbers

**Cost counts the nodes a query could return, not the bytes it does.** This is
the one that matters. A scalar on a node already being fetched is free, so
every issue body rides along for nothing: 76% of the payload and none of the
quota. A connection is what costs — `labels(first: 100)` is priced as a hundred
nodes whether the issue has two labels or none.

So when extending a query: **add a scalar freely, weigh a connection.**

**Combining is cheaper than staging.** The whole query is estimated at once, so
head + items together cost less than head and items apart. A design that probes
first and pulls second can therefore cost *more* than one that just pulls — the
intuition that a cheap check saves an expensive fetch does not survive contact
with the pricing.

**Cost tracks pages, not items.** Measured by shrinking `page_size` on the same
board:

| `page_size` | pages | points |
|---|---|---|
| 100 | 1 | 2 |
| 10 | 4 | 4 |
| 5 | 8 | 10 |

About a point per page plus one. This is why `page_size` stays at 100, which is
GitHub's maximum — `first: 150` is refused outright. Smaller pages buy nothing
and cost a point each.

Extrapolated, with the hourly budget of 5000:

| Board | Points per full pull | Full pulls available per hour |
|---|---|---|
| ≤100 cards | 2 | 2,500 |
| 1,000 | ~11 | 450 |
| 10,000 | ~101 | 49 |

## What follows from it

**Pull everything.** L1 would save 2 points and about 1.3 seconds, and would
cost `board find` its text search and `board item` its offline body — after
which every card studied becomes its own query, so the quota saving reverses.
Fetching less is only worth it when latency is the binding cost, which at 2.3s
it is not.

**Pull on a read, not on a clock.** Work is not continuous: a session asks the
board several questions while picking up a card, then nothing for an hour.
A read-driven pull therefore fires a handful of times a day where a 30-second
poll fires 2,880 times, most of them for a board nobody is asking about. See
[local-first.md](local-first.md#why-reads-are-local) for the freshness floor
this pays for.

**Do not build a conflict check out of a cheap probe.** A single item by node
id is 1 point and 0.7s, flat regardless of board size, which makes it right for
checking one card before writing to it. It is not a freshness oracle: it cannot
see a card that was created, and a card moved between columns leaves the id set
identical. Membership and field staleness are different questions and L0
answers only the first.

## How often a read actually pulls

The objection worth taking seriously is that a read which pulls is barely a
local board at all. Measured over one session's trail — 33 reads, 32 gaps
between them — the answer is that read gaps are bimodal:

| gap between consecutive reads | count |
|---|---|
| 0-17s | 18 |
| 44-199s | 5 |
| 415-5100s | 8 |
| 16 hours | 1 |

Median 14s, and **nothing at all between 17s and 44s**. The bursts are where a
snapshot earns its keep: those eighteen reads inside seventeen seconds would
otherwise be eighteen API calls, which is what having no board would really
cost.

The floor is therefore chosen to land in the empty band above the second
cluster, not the first:

| floor | reads answered from disk | pulls in that session |
|---|---|---|
| 60s | 19/32 | 13 |
| 300s | 24/32 | 8 |

300 is the default. It keeps three reads in four local while bounding staleness
two orders of magnitude below the 16-hour snapshot that once came within one
command of being reported as current. Raising it further buys little: the next
gap up is already 415s, and past that the distribution is hours.

One session is thin evidence and it was a session unusually full of board
poking. Re-derive it from `.boardkit/log.jsonl` before moving the number: the
trail records every command with its timestamp, so the distribution above is a
few lines of jq away.

## When this answer changes

- **A board past roughly 500 cards.** The pull crosses five pages and several
  seconds, and L1-on-read with bodies fetched per card starts to win — paying
  quota to buy latency, which is correct only once latency binds.
- **Many agents against one board.** The read floor assumes bursts from one
  session. Concurrent readers each paying their own floor is still cheap in
  quota, but it is a different calculation.
- **GitHub repricing GraphQL.** The rules above are observed, not documented by
  GitHub. Re-measure before trusting them after an API change.

## How to re-measure

Ask the API what a query costs, inside that query:

```bash
gh api graphql -f query='{ rateLimit { cost remaining }
  user(login: "<owner>") { projectV2(number: <n>) {
    items(first: 100) { nodes { id } } } } }'
```

For a whole command rather than one query, read the budget on either side of it:

```bash
before=$(gh api graphql -f query='{rateLimit{remaining}}' | jq .data.rateLimit.remaining)
board sync
after=$(gh api graphql -f query='{rateLimit{remaining}}' | jq .data.rateLimit.remaining)
echo "cost: $(( before - after - 1 ))"   # less one for the probe itself
```

Both probes cost a point each, so subtract them. `remaining` also refills over
the hour, which shows up as a negative cost on a cheap command — that is the
refill, not a free lunch.
