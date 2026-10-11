"""Which GitHub credential boardkit's `gh` calls run under.

By default it is a focused token: one repository, its issues and its board,
set up once with `board auth setup`. The personal `gh` login is broad — every
repository and board the account reaches — and an agent working the board
needs none of that, so it is used only when `--as-me` asks for it.

The token comes from `BOARDKIT_TOKEN` when set, so CI and other platforms can
supply one, and otherwise from the macOS keychain, keyed by repository.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

# The keychain service every repository's token is filed under; the account
# is the repository, so each repo keeps its own.
SERVICE = "boardkit"
ENV = "BOARDKIT_TOKEN"

# Set once per invocation by the CLI. Unset, as in tests and library use,
# `gh` runs with whatever environment it inherits, as it always did.
repo: str | None = None
as_me = False


def configure(name: str, elevated: bool = False) -> None:
    global repo, as_me
    repo, as_me = name, elevated


def has_keychain() -> bool:
    return sys.platform == "darwin" and shutil.which("security") is not None


def stored(name: str) -> str:
    """The focused token for `name`, or "" when none is set up."""
    if os.environ.get(ENV):
        return os.environ[ENV]
    if not has_keychain():
        return ""
    result = subprocess.run(
        ["security", "find-generic-password", "-s", SERVICE, "-a", name, "-w"],
        capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def store(name: str, token: str) -> None:
    """File `token` in the keychain for `name`, replacing any earlier one."""
    # `-w` takes the secret on argv, visible to this user's `ps` for the
    # instant the call runs. `security` reads it no other way without a
    # prompt, and the token is narrow by design, so that is the trade.
    subprocess.run(
        ["security", "add-generic-password", "-U", "-s", SERVICE, "-a", name,
         "-l", f"boardkit {name}", "-w", token],
        capture_output=True, text=True, check=True)


def source() -> str:
    """Which credential the next `gh` call uses, said for a person."""
    if repo is None or as_me:
        return "your gh login" + (" (--as-me)" if as_me else "")
    if os.environ.get(ENV):
        return f"focused token from {ENV}"
    return "focused token from the keychain" if stored(repo) else "none set up"


def focused() -> bool:
    """True when `gh` runs under the focused token rather than the login."""
    return repo is not None and not as_me


class Missing(RuntimeError):
    """No focused token for this repository, and `--as-me` was not given."""


def elevated(action: str) -> None:
    """Refuse `action` under the focused token: it reaches beyond one repo and
    its board, so the person asks for it with --as-me."""
    if repo is not None and not as_me:
        raise Missing(f"{action} needs your own gh login, not the focused token. "
                      f"Re-run with --as-me.")


def environment() -> dict[str, str] | None:
    """The environment a `gh` call runs in, or None to inherit as-is."""
    if repo is None or as_me:
        return None
    token = stored(repo)
    if not token:
        raise Missing(
            f"no focused GitHub token for {repo}. Run `board auth setup` once to "
            f"create one, or add --as-me to use your own gh login for this "
            f"command.")
    # `GH_TOKEN` outranks the stored login in `gh`, so the personal session
    # is untouched and simply not used.
    return {**os.environ, "GH_TOKEN": token}
