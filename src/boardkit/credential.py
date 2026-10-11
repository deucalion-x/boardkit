"""Which GitHub credential boardkit's `gh` calls run under.

Never the person's own `gh` login by default: that reaches every repository
and board the account can, and an agent working one repository's board needs
none of it. In order, boardkit uses:

1. `BOARDKIT_TOKEN`, when set, for CI and platforms without a keychain;
2. the sign-in through this repository's own GitHub App (`board auth login`),
   which reaches only the repositories that app is installed on;
3. a token for this repository alone (`board auth setup`), the fallback for
   anyone who cannot use an app.

`--as-me` uses the personal login instead, for one command: the escape hatch,
approved by the person and logged.
"""
from __future__ import annotations

import os

from . import keychain

# The keychain service every repository's token is filed under; the account
# is the repository, so each repo keeps its own.
SERVICE = "boardkit"
ENV = "BOARDKIT_TOKEN"

# Set once per invocation by the CLI. Unset, as in tests and library use,
# `gh` runs with whatever environment it inherits, as it always did.
repo: str | None = None
app = ""
as_me = False


def configure(name: str, elevated: bool = False, app_id: str = "") -> None:
    global repo, as_me, app
    repo, as_me, app = name, elevated, app_id


def has_keychain() -> bool:
    return keychain.available()


def stored(name: str) -> str:
    """This repository's own token (`board auth setup`), or "" if none."""
    if os.environ.get(ENV):
        return os.environ[ENV]
    return keychain.get(SERVICE, name) if has_keychain() else ""


def store(name: str, token: str) -> None:
    """File `token` for `name` in the platform's store, replacing any earlier one."""
    keychain.put(SERVICE, name, token, f"boardkit {name}")


def source() -> str:
    """Which credential the next `gh` call uses, said for a person."""
    from . import login
    if repo is None or as_me:
        return "your gh login" + (" (--as-me)" if as_me else "")
    if os.environ.get(ENV):
        return f"the token in {ENV}"
    if login.signed_in(app):
        return f"your sign-in through {repo}'s app (acts as you, via the app)"
    return ("this repository's token, from the keychain" if stored(repo)
            else "none set up")


def focused() -> bool:
    """True when `gh` runs under this repository's credential, not the login."""
    return repo is not None and not as_me


def renewal() -> str:
    """How to replace the credential in use once GitHub stops accepting it."""
    from . import login
    if os.environ.get(ENV):
        return f"Replace the token in {ENV}."
    if login.signed_in(app):
        return "Run `board auth login` to sign in again."
    return ("Regenerate it at https://github.com/settings/personal-access-tokens "
            "(Regenerate token keeps its permissions), then run `board auth setup` "
            "and paste the new value.")


class Missing(RuntimeError):
    """No credential for this repository, and `--as-me` was not given."""


def elevated(action: str) -> None:
    """Refuse `action` under this repository's credential: it reaches beyond
    one repository and its board, so the person asks for it with --as-me."""
    if repo is not None and not as_me:
        raise Missing(f"{action} needs your own gh login, not this repository's credential. "
                      f"Re-run with --as-me.")


def environment() -> dict[str, str] | None:
    """The environment a `gh` call runs in, or None to inherit as-is."""
    if repo is None or as_me:
        return None
    # An explicit token wins; then the sign-in through this repository's own
    # app; then this repository's own token.
    from . import login
    token = os.environ.get(ENV, "")
    if not token:
        try:
            token = login.access_token(app)
        except login.LoginError as expired:
            raise Missing(str(expired)) from None
    token = token or stored(repo)
    if not token:
        raise Missing(
            f"no GitHub credential for {repo}. Run `board auth login` to sign in "
            f"through the boardkit app, or `board auth setup` for a token for this "
            f"repository alone, or add --as-me to use your own gh login for "
            f"this command.")
    # `GH_TOKEN` outranks the stored login in `gh`, so the personal session
    # is untouched and simply not used.
    return {**os.environ, "GH_TOKEN": token}
