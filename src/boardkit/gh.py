"""Process plumbing shared by every command.

Each command shells out to `gh` or `git` rather than talking to the GitHub API
directly, so authentication is whatever the user already has working in their
terminal and there is no dependency to install.
"""
from __future__ import annotations

import json
import shutil
import subprocess


class BoardError(RuntimeError):
    """A failure worth printing without a traceback."""


def run(args: list[str]) -> str:
    """Run a command and return stdout, turning the usual failures into advice."""
    if shutil.which(args[0]) is None:
        raise BoardError(f"{args[0]} is not installed or not on PATH")
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise BoardError(f"{' '.join(args)} failed: {detail}")
    return result.stdout


def run_json(args: list[str]):
    return json.loads(run(args))


def gh(*args: str) -> str:
    return run(["gh", *args])


def gh_json(*args: str):
    return run_json(["gh", *args])


def git(*args: str) -> str:
    return run(["git", *args]).strip()


def git_ok(*args: str) -> bool:
    """True when the command exits zero. For probes, where failure is an answer."""
    return subprocess.run(["git", *args], capture_output=True).returncode == 0
