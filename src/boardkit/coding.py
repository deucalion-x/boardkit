"""The coding identity: what an agent's `git` and `gh` act as.

An agent left to work unattended used the person's own `gh` login, which can
do anything they can: administer the organisation, delete a repository. So an
agent session gets an identity of its own, from a second GitHub App with code
permissions only, and the person's login is not kept on the machine at all
(the README gives the ways). Anything the coding app cannot do fails, and the
agent has to ask.

There are three identities, each with one job:

- the board app, per repository, for `board` (see `login`);
- the coding app, per person, for an agent's `git` and `gh` (this module);
- the person, for everything else, signed in only for the task.

The coding sign-in belongs to the person, not to a repository, so it is kept
once under its own name and works in every repository the app is installed
on. The token lasts eight hours and is renewed silently, like the board's.
An agent session picks it up through `board auth agent-env`, which the
plugin's SessionStart hook writes into the session's environment: the
person's own terminal is left as it was.
"""
from __future__ import annotations

import json
import shlex
import sys

from . import credential, keychain, login

SERVICE = "boardkit-coding"
ACCOUNT = login.HOST
AGAIN = "board auth login --coding <client-id>"

# Code and its review, nothing more: no Administration, no workflow files, no
# organisation permission. Issues are written because an agent comments on
# the issue a commit closes.
PERMISSIONS = {
    "contents": "write", "pull_requests": "write", "issues": "write",
    "checks": "read", "actions": "read", "statuses": "read", "metadata": "read",
}


def manifest(owner: str, redirect: str) -> dict:
    return {
        "name": f"boardkit-code-{owner}"[:34].rstrip("-"),
        "url": "https://github.com/deucalion-x/boardkit",
        "description": f"What a coding agent's git and gh act as in {owner}: code "
                       f"and pull requests only, for the person signed in.",
        "redirect_url": redirect,
        "public": False,
        "hook_attributes": {"url": "https://example.invalid/unused", "active": False},
        "request_oauth_on_install": False,
        "default_permissions": PERMISSIONS,
        "default_events": [],
    }


def stored() -> dict | None:
    """{"client_id", tokens...}, or None when nobody signed in."""
    if not credential.has_keychain():
        return None
    try:
        return json.loads(keychain.get(SERVICE, ACCOUNT) or "null")
    except json.JSONDecodeError:
        return None


def save(record: dict) -> None:
    keychain.put(SERVICE, ACCOUNT, json.dumps(record), "boardkit coding sign-in")


def token() -> str:
    """A live coding token, renewed when due; "" when nobody signed in."""
    record = stored()
    if record is None:
        return ""
    if login.due(record):
        record = {"client_id": record["client_id"],
                  **login.renew(record["client_id"], record, AGAIN)}
        save(record)
    return record["access_token"]


def sign_in(client_id: str) -> int:
    if not client_id:
        record = stored()
        client_id = record["client_id"] if record else ""
    if not client_id:
        print("Name the coding app: `board auth login --coding <client-id>`. "
              "`board auth app create --coding` makes one and prints it.")
        return 1
    if not credential.has_keychain():
        print("No credential store here to keep a sign-in.")
        return 1
    tokens = login.device_flow(client_id, AGAIN)
    if tokens is None:
        return 1
    save({"client_id": client_id, **tokens})
    print("Signed in as your coding identity. Agent sessions' git and gh now act "
          "for you through the coding app, only where it is installed and only "
          "with its permissions. Your own terminal is unchanged.")
    return 0


def sign_out() -> int:
    keychain.delete(SERVICE, ACCOUNT)
    print("Signed out of the coding identity on this machine.")
    return 0


def create(owner: str, org: bool) -> int:
    from . import app
    if not owner:
        print("Name the organisation: `board auth app create --coding --owner <org>`.")
        return 1
    print(f"Opening GitHub to create boardkit-code-{owner}, the identity coding "
          f"agents act as. Check the name, click Create GitHub App, and come back.")
    made = app.through_browser(owner, org, lambda redirect: manifest(owner, redirect))
    if made is None:
        return 1
    if made["owner"].lower() != owner.lower():
        print(f"{made['slug']} was created under {made['owner']}, not {owner}. Delete it "
              f"from https://github.com/settings/apps and run this again, signed in "
              f"as an account that can administer {owner}.")
        return 1
    settings = (f"https://github.com/organizations/{owner}/settings/apps/{made['slug']}"
                if org else f"https://github.com/settings/apps/{made['slug']}")
    print(f"Created {made['slug']} (client ID {made['client_id']}). Its secrets were "
          f"not kept.\n\nTwo clicks are left, which a manifest cannot make:\n"
          f"  1. Tick \"Enable Device Flow\" on {settings}\n"
          f"  2. Install it on the repositories agents may code in:\n"
          f"     https://github.com/apps/{made['slug']}/installations/new\n"
          f"Then: board auth login --coding {made['client_id']}")
    return 0


def git_credential(operation: str, request: str) -> str:
    """git's credential-helper protocol: answer `get` for github.com over
    https with the coding token. Anything else is not ours to answer, and an
    empty answer lets git ask the next helper — of which an agent session has
    none, so it fails rather than falling back to the person."""
    if operation != "get":
        return ""
    asked = dict(line.split("=", 1) for line in request.splitlines() if "=" in line)
    if asked.get("protocol") != "https" or asked.get("host") != login.HOST:
        return ""
    live = token()
    if not live:
        return ""
    return f"username=x-access-token\npassword={live}\n"


def agent_env(board: str) -> str:
    """Shell lines that make a session's git and gh the coding identity.

    git asks `board auth git-credential` and no other helper: the empty value
    first clears any configured one, the person's keychain helper included.
    `gh` reads GH_TOKEN, fetched on each call so it is never stale. A program
    that starts `gh` itself does not see the function and falls back to the
    person's login, which is not kept, so it fails rather than acting as
    them. Nothing when nobody has signed in: such a session is as before.
    """
    if stored() is None:
        return ""
    run = shlex.quote(board)
    return "\n".join((
        "# boardkit: this session's git and gh act as the coding identity",
        "export BOARDKIT_AGENT=1",
        "export GIT_CONFIG_COUNT=2",
        "export GIT_CONFIG_KEY_0=credential.https://github.com.helper",
        "export GIT_CONFIG_VALUE_0=",
        "export GIT_CONFIG_KEY_1=credential.https://github.com.helper",
        f"export GIT_CONFIG_VALUE_1={shlex.quote('!' + board + ' auth git-credential')}",
        f'gh() {{ GH_TOKEN="$({run} auth token --coding)" command gh "$@"; }}',
        "",
    ))


def main(args: list[str]) -> int:
    """`board auth` verbs that need no repository."""
    coding = "--coding" in args
    rest = [a for a in args if a != "--coding"]
    if rest[:1] == ["git-credential"]:
        sys.stdout.write(git_credential(rest[1] if len(rest) > 1 else "",
                                        sys.stdin.read()))
        return 0
    if rest[:1] == ["agent-env"]:
        sys.stdout.write(agent_env(sys.argv[0] if sys.argv[0].endswith("board")
                                   else "board"))
        return 0
    if rest[:1] == ["token"] and coding:
        live = token()
        if not live:
            print(f"No coding identity on this machine. Run `{AGAIN}`.", file=sys.stderr)
            return 1
        print(live)
        return 0
    if rest[:1] == ["login"] and coding:
        return sign_in(rest[1] if len(rest) > 1 else "")
    if rest[:1] == ["logout"] and coding:
        return sign_out()
    if rest[:2] == ["app", "create"] and coding:
        owner = rest[rest.index("--owner") + 1] if "--owner" in rest[:-1] else ""
        org = "--user" not in rest
        if not owner:
            from .config import load
            from .gh import BoardError
            try:
                found = load()
                owner, org = found.owner, found.owner_type == "org"
            except BoardError:
                pass
        return create(owner, org)
    return -1
