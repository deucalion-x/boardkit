"""What talking to GitHub cost, totalled per day, so the promise can be checked.

boardkit exists so an agent can ask the board as often as it likes without
spending the API budget. That is a claim about real use, and the estimates in
`docs/pull-cost.md` cannot check it. These numbers can: how many reads were
answered from the snapshot, how many calls reached GitHub, and what GitHub
said each of them cost.

`.boardkit/usage.json`, one entry per day for the last `DAYS` days. Unlike the
trail it is read back, by `board log --usage`, and it holds totals rather than
lines so the trail's rotation cannot take the history with it.

What is summed is what boardkit spent, not what the budget has left. Under
`--as-me` the budget is shared with everything else on that login, so its
remaining figure says little about boardkit; the lowest one seen is kept as
headroom, not as a measure.

The counts are best-effort: two processes finishing at the same instant can
lose an increment between them. Taking the store's lock on every read would
prevent it, and a statistic is not worth making reads wait.
"""
from __future__ import annotations

import json
import os
import pathlib
import time

from . import trail

NAME = "usage.json"
# Long enough to compare one month with the last, small enough that the file
# stays a few kilobytes.
DAYS = 90
# How a read was answered. `local` touched nothing; `refreshed` pulled first;
# `stale` tried to pull, could not, and answered from the snapshot anyway.
READS = ("local", "refreshed", "stale")

_path: pathlib.Path | None = None
_verb = "board"


def configure(path: pathlib.Path) -> None:
    """Name the file. Off with the trail: one switch for all record-keeping."""
    global _path
    chosen = os.environ.get(trail.ENV)
    off = chosen is not None and chosen.strip().lower() in trail.OFF
    _path = None if off else path


def begin(verb: str) -> None:
    """The command the calls that follow are charged to."""
    global _verb
    _verb = verb


def load() -> dict:
    if _path is None or not _path.exists():
        return {}
    try:
        days = json.loads(_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Statistics that cannot be read start again rather than fail a command.
        return {}
    return days if isinstance(days, dict) else {}


def day(days: dict, date: str) -> dict:
    entry = days.setdefault(date, {})
    entry.setdefault("reads", dict.fromkeys(READS, 0))
    for key in ("calls", "points", "unpriced"):
        entry.setdefault(key, 0)
    entry.setdefault("lowest", {})
    entry.setdefault("verbs", {})
    return entry


def save(days: dict) -> None:
    if _path is None:
        return
    kept = dict(sorted(days.items())[-DAYS:])
    try:
        _path.parent.mkdir(parents=True, exist_ok=True)
        # Per process, so two writers never share a half-written file.
        temporary = _path.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(kept, indent=1) + "\n", encoding="utf-8")
        temporary.replace(_path)
    except OSError:
        # Like the trail: not worth failing a command over.
        pass


def today() -> str:
    return time.strftime("%Y-%m-%d")


def read(how: str) -> None:
    """One read, and how it was answered."""
    if _path is None:
        return
    days = load()
    reads = day(days, today())["reads"]
    reads[how] = reads.get(how, 0) + 1
    save(days)


def call(command: str, resource: str | None, cost: int | None,
         remaining: int | None) -> None:
    """One `gh` invocation, with what GitHub said it cost when it said.

    `resource` is GitHub's name for the budget the call drew on: `graphql`,
    `core` for REST, or None when `gh` did not show its headers. A REST call
    costs one by definition; a GraphQL query reports its own price; a mutation
    and any `gh` subcommand report nothing and are counted as unpriced.
    """
    if cost is None and resource not in (None, "graphql"):
        cost = 1
    trail.note("called", verb=_verb, command=command, resource=resource,
               cost=cost, remaining=remaining)
    if _path is None:
        return
    days = load()
    entry = day(days, today())
    verb = entry["verbs"].setdefault(_verb, {"calls": 0, "points": 0, "unpriced": 0})
    for totals in (entry, verb):
        totals["calls"] += 1
        if cost is None:
            totals["unpriced"] += 1
        else:
            totals["points"] += cost
    if resource and remaining is not None:
        seen = entry["lowest"].get(resource)
        entry["lowest"][resource] = remaining if seen is None else min(seen, remaining)
    save(days)


def report(span: int = 7) -> int:
    """The last `span` days that saw any use, and their totals."""
    days = load()
    recent = sorted(days.items())[-span:]
    if not recent:
        print("No usage recorded yet.")
        return 0
    print(f"GitHub usage, last {len(recent)} day(s) with activity "
          "(boardkit's own calls; unpriced = mutations and gh subcommands, "
          "at least 1 point each)")
    print()
    print(f"{'day':10}  {'reads':>5}  {'local':>5}  {'pulled':>6}  {'stale':>5}  "
          f"{'calls':>5}  {'points':>6}  {'unpriced':>8}  lowest left")
    total = day({}, "total")
    verbs: dict[str, dict] = {}
    for date, raw in recent:
        entry = day({date: raw}, date)
        print(row(date, entry))
        for how in READS:
            total["reads"][how] += entry["reads"].get(how, 0)
        for key in ("calls", "points", "unpriced"):
            total[key] += entry[key]
        for resource, left in entry["lowest"].items():
            seen = total["lowest"].get(resource)
            total["lowest"][resource] = left if seen is None else min(seen, left)
        for name, counts in entry["verbs"].items():
            into = verbs.setdefault(name, {"calls": 0, "points": 0, "unpriced": 0})
            for key in into:
                into[key] += counts.get(key, 0)
    if len(recent) > 1:
        print(row("total", total))
    reads = sum(total["reads"].values())
    if reads:
        local = total["reads"]["local"] + total["reads"]["stale"]
        print()
        print(f"{local} of {reads} reads ({100 * local // reads}%) answered "
              f"without calling GitHub.")
    if verbs:
        print()
        print("by command:")
        for name, counts in sorted(verbs.items(), key=lambda kv: -kv[1]["calls"]):
            print(f"  {name:14} {counts['calls']:>5} calls  {counts['points']:>6} points"
                  f"  {counts['unpriced']:>5} unpriced")
    return 0


def row(label: str, entry: dict) -> str:
    reads = entry["reads"]
    lowest = ", ".join(f"{resource} {left}"
                       for resource, left in sorted(entry["lowest"].items()))
    return (f"{label:10}  {sum(reads.values()):>5}  {reads.get('local', 0):>5}  "
            f"{reads.get('refreshed', 0):>6}  {reads.get('stale', 0):>5}  "
            f"{entry['calls']:>5}  {entry['points']:>6}  {entry['unpriced']:>8}  "
            f"{lowest or '-'}")
