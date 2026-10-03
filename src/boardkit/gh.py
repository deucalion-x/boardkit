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
import sys
import time

# GitHub limits requests two ways. The hourly point budget is generous and
# reports itself in headers; the burst limiter is the one that actually bites,
# and `gh` hands us its message without the status code or the `retry-after`
# header that would tell us how long to wait. So the message is the signal.
LIMITED = re.compile(r"rate limit|secondary rate|abuse detection", re.IGNORECASE)
# Each probe against a burst limit can extend it, so the waits are long and
# few rather than short and many — which is also why waiting is not the
# default. A person at a terminal should get their prompt back and be told to
# come back later; only an unattended sync should sit through it. `--wait`
# raises this.
BACKOFF = (30, 90, 240)
RETRIES = 0


class BoardError(RuntimeError):
    """A failure worth printing without a traceback."""


class RateLimited(BoardError):
    """Refused for rate, not for permission. Waiting is the fix."""


def once(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True)


def run(args: list[str], retries: int | None = None) -> str:
    """Run a command and return stdout, turning the usual failures into advice.

    A rate-limited call fails immediately unless waiting was asked for. The
    caller can do nothing but wait, but making them wait at a prompt for
    minutes is worse than telling them to come back: the queue is durable, so
    nothing is lost by stopping. `--wait` is for an unattended sync, where
    there is no one to come back.
    """
    if shutil.which(args[0]) is None:
        raise BoardError(f"{args[0]} is not installed or not on PATH")
    retries = RETRIES if retries is None else retries
    for attempt in range(retries + 1):
        result = once(args)
        if result.returncode == 0:
            return result.stdout
        detail = (result.stderr or result.stdout).strip()
        if not LIMITED.search(detail) or attempt == retries:
            if LIMITED.search(detail):
                waited = f" after {sum(BACKOFF[:retries])}s" if retries else ""
                raise RateLimited(
                    f"GitHub is rate limiting this account{waited}. Nothing was "
                    f"lost — queued changes are kept. Try again in a few "
                    f"minutes, or use `--wait` to sit through it.")
            raise BoardError(f"{' '.join(args)} failed: {detail}")
        pause = BACKOFF[attempt]
        print(f"rate limited by GitHub; waiting {pause}s before retrying "
              f"({attempt + 1} of {retries})", file=sys.stderr)
        time.sleep(pause)
    raise BoardError(f"{' '.join(args)} failed")


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
    result = subprocess.run(args, capture_output=True, text=True)
    return result.returncode == 0, (result.stdout or "") + (result.stderr or "")


def git_ok(*args: str) -> bool:
    """True when the command exits zero. For probes, where failure is an answer."""
    return subprocess.run(["git", *args], capture_output=True).returncode == 0
