"""Process plumbing shared by every command.

Each command shells out to `gh` or `git` rather than talking to the GitHub API
directly, so authentication is whatever the user already has working in their
terminal and there is no dependency to install.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time

from .trail import note

# GitHub limits requests two ways. The hourly point budget is generous and
# reports itself in headers; the burst limiter is the one that actually bites,
# and `gh` hands us its message without the status code or the `retry-after`
# header that would tell us how long to wait. So the message is the signal.
LIMITED = re.compile(r"rate limit|secondary rate|abuse detection", re.IGNORECASE)



class BoardError(RuntimeError):
    """A failure worth printing without a traceback."""


class RateLimited(BoardError):
    """Refused for rate, not for permission. Waiting is the fix."""


class Unreachable(BoardError):
    """GitHub could not be reached. Waiting is the fix, and it may be a while."""


# What `gh` says when the network is the problem rather than the request.
OFFLINE = re.compile(
    r"no such host|could not resolve|connection refused|network is unreachable|"
    r"connection reset|timeout|timed out|dial tcp|TLS handshake|EOF",
    re.IGNORECASE)


# Long enough for a slow page of a large board, short enough that a wedged
# process fails instead of hanging a terminal — or a CI job — forever.
TIMEOUT = 120


def quoted(value: object) -> str:
    """One value as a GraphQL string literal, escaped.

    GraphQL string syntax is JSON string syntax, so `json.dumps` is the
    encoder rather than a hand-rolled one. Interpolating a bare f-string
    here is how a quote in a field description, a view name or a filter
    turns committed configuration into a malformed — or forged — document.
    """
    return json.dumps("" if value is None else str(value))


def once(args: list[str]) -> subprocess.CompletedProcess:
    # `check=False` throughout: a non-zero exit from `gh` is a result to
    # read, not a crash. The callers turn it into a message.
    try:
        return subprocess.run(args, capture_output=True, text=True, check=False,
                              timeout=TIMEOUT)
    except subprocess.TimeoutExpired as expired:
        # Converted here because this is the one place every call goes
        # through. A timeout is a kind of unreachability — waiting is the
        # fix — and the queue is durable, so it costs nothing to stop. Left
        # to propagate it was the one failure that printed a traceback
        # instead of advice.
        note("unreachable", command=" ".join(args[:3]),
             said=f"no response in {TIMEOUT}s")
        raise Unreachable(
            f"{args[0]} did not respond within {TIMEOUT}s. Queued changes are "
            f"kept; run this again when GitHub is answering.") from expired


REMAINING = re.compile(r"^x-ratelimit-remaining:\s*(\d+)", re.IGNORECASE | re.MULTILINE)
RESET_AT = re.compile(r"^x-ratelimit-reset:\s*(\d+)", re.IGNORECASE | re.MULTILINE)
# One trivial query, costing a single point, to read the headers that carry
# the truth.
PROBE = ["gh", "api", "graphql", "--include", "-f", "query={viewer{login}}"]


def budget() -> tuple[int, int] | None:
    """Points left on the GraphQL budget, and seconds until it refills.

    Read from a real response's headers rather than from `gh api rate_limit`,
    which reports a different and lower figure — it said 210 points used while
    the headers said 4983, and acting on it led to diagnosing a request burst
    where the budget was simply spent.

    The cost is not one point per call: GraphQL charges for the nodes a query
    touches, so `item-list --limit 200` against a small board is expensive and
    a day of syncing can exhaust 5000 without ever making many requests.
    """
    try:
        result = once(PROBE)
    except Unreachable:
        # The budget is a detail on top of another failure's message. Losing
        # it must not replace that failure with this one.
        return None
    headers = (result.stdout or "") + (result.stderr or "")
    left, reset = REMAINING.search(headers), RESET_AT.search(headers)
    if not left or not reset:
        return None
    return int(left.group(1)), max(0, int(reset.group(1)) - int(time.time()))


def record(args: list[str], detail: str, found: tuple[int, int] | None) -> None:
    """A refusal, with the budget as it stood and GitHub's own words.

    Recorded because the alternative is reasoning about our own paraphrase:
    this package spent an afternoon treating a spent budget as a request burst,
    because the message had been reworded and the numbers came from an endpoint
    reporting a different figure.
    """
    left, refill = found if found is not None else (None, None)
    note("refused", command=" ".join(args[:3]), remaining=left,
         reset_in=refill, said=detail)


def run(args: list[str]) -> str:
    """Run a command and return stdout, turning the usual failures into advice.

    A rate-limited call is not retried. The queue is durable, so stopping costs
    nothing, and the one thing worth saying is when the budget comes back —
    which the headers answer exactly.
    """
    if shutil.which(args[0]) is None:
        raise BoardError(f"{args[0]} is not installed or not on PATH")
    result = once(args)
    if result.returncode == 0:
        return result.stdout
    detail = (result.stderr or result.stdout).strip()
    if OFFLINE.search(detail):
        note("unreachable", command=" ".join(args[:3]), said=detail[:200])
        raise Unreachable(f"GitHub could not be reached: {detail}")
    if not LIMITED.search(detail):
        note("failed", command=" ".join(args[:3]), said=detail[:400])
        raise BoardError(f"{' '.join(args)} failed: {detail}")
    found = budget()
    record(args, detail, found)
    when = (f" It refills in {found[1]}s ({found[1] // 60}m)."
            if found is not None else "")
    left = f"{found[0]} of 5000 points left." if found is not None else ""
    raise RateLimited(
        f"GitHub refused this for rate. {left}{when} Queued changes are kept; "
        f"run this again after.\n  GitHub said: {detail}")


def run_json(args: list[str]):
    return json.loads(run(args))


def gh(*args: str) -> str:
    return run(["gh", *args])


def gh_json(*args: str):
    return run_json(["gh", *args])


def git(*args: str) -> str:
    return run(["git", *args]).strip()


def probe(args: list[str]) -> tuple[bool, str]:
    """Run a command where failure is an answer, not an error.

    `gh auth status` exits non-zero when nobody is logged in, and a visibility
    check is asked precisely because it may fail, so neither can go through
    `run`.
    """
    if shutil.which(args[0]) is None:
        return False, f"{args[0]} is not installed or not on PATH"
    try:
        result = subprocess.run(args, capture_output=True, text=True,
                                check=False, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        # Failure being an answer here extends to this one: a probe that never
        # came back has not established what it was asked to establish.
        return False, f"{args[0]} did not respond within {TIMEOUT}s"
    return result.returncode == 0, (result.stdout or "") + (result.stderr or "")


def git_ok(*args: str) -> bool:
    """True when the command exits zero. For probes, where failure is an answer.

    A timeout is one of those answers rather than an exception, for the same
    reason as `probe`: the caller asked whether something holds, and a command
    that never returned has not shown that it does.
    """
    try:
        return subprocess.run(["git", *args], capture_output=True,
                              check=False, timeout=TIMEOUT).returncode == 0
    except subprocess.TimeoutExpired:
        return False
