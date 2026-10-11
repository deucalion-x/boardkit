"""Sign in through the boardkit GitHub App, with GitHub's device flow.

The token it gets acts for the person, marked "via boardkit", and reaches only
the repositories the app is installed on, with only the app's permissions. It
lasts eight hours and is renewed from a refresh token, silently, so nobody
fills in a token form or regenerates anything. No secret ships with boardkit:
the device flow needs the app's client ID alone, which is public, and a token
it produced refreshes without the client secret.

Each repository names its own app in its config, and each app is installed
on that repository alone, so a sign-in reaches one repository: GitHub limits
it to the repos its own app is installed on. Sign-ins are filed in the
keychain under the app's client ID, one per app.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
import webbrowser

from . import credential, keychain

HOST = "github.com"
SERVICE = "boardkit-app"
DEVICE = f"https://{HOST}/login/device/code"
TOKEN = f"https://{HOST}/login/oauth/access_token"
# Refreshed this long before it would lapse, so a call never starts on a
# token that dies halfway through it.
MARGIN = 300


class LoginError(RuntimeError):
    """Signing in, or staying signed in, did not work. Says what to do."""


def post(url: str, fields: dict[str, str]) -> dict:
    """One form POST to GitHub's OAuth endpoints, answered in JSON."""
    request = urllib.request.Request(
        url, data=urllib.parse.urlencode(fields).encode(),
        headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def stamped(answer: dict) -> dict:
    """GitHub's token answer with absolute expiry times instead of durations."""
    now = int(time.time())
    return {"access_token": answer["access_token"],
            "refresh_token": answer.get("refresh_token", ""),
            "expires_at": now + int(answer.get("expires_in") or 0),
            "refresh_expires_at": now + int(answer.get("refresh_token_expires_in") or 0)}


def load(app: str) -> dict | None:
    if not app or not credential.has_keychain():
        return None
    try:
        return json.loads(keychain.get(SERVICE, app) or "null")
    except json.JSONDecodeError:
        return None


def save(app: str, tokens: dict) -> None:
    keychain.put(SERVICE, app, json.dumps(tokens), f"boardkit sign-in {app}")


def forget(app: str) -> None:
    keychain.delete(SERVICE, app)


def signed_in(app: str) -> bool:
    return load(app) is not None


def access_token(app: str) -> str:
    """A live token from the stored sign-in, refreshed first when it is due.

    "" when nobody has signed in, so the caller can fall back to a
    per-repository token.
    """
    tokens = load(app)
    if tokens is None:
        return ""
    # A token from an app that does not expire them carries no expiry.
    if not tokens.get("expires_at") or tokens["expires_at"] - MARGIN > time.time():
        return tokens["access_token"]
    if not tokens.get("refresh_token") or tokens.get("refresh_expires_at", 0) <= time.time():
        raise LoginError("your boardkit sign-in has expired. Run `board auth login`.")
    answer = post(TOKEN, {"client_id": app, "grant_type": "refresh_token",
                          "refresh_token": tokens["refresh_token"]})
    if "access_token" not in answer:
        raise LoginError(f"GitHub would not renew your boardkit sign-in "
                         f"({answer.get('error', 'no reason given')}). Run `board auth login`.")
    tokens = stamped(answer)
    save(app, tokens)
    return tokens["access_token"]


def login(app: str, repo: str) -> int:
    """The device flow: show a code, open the page, wait for approval."""
    if not app:
        print(f"{repo} names no GitHub App to sign in through. Set app_client_id "
              f"in its boardkit config, or use `board auth setup` for a token "
              f"for this repository alone.")
        return 1
    if not credential.has_keychain():
        print("No credential store here (macOS keychain, Linux secret-tool or Windows "
              f"Credential Manager) to keep a sign-in. Export {credential.ENV} instead.")
        return 1
    started = post(DEVICE, {"client_id": app})
    if "device_code" not in started:
        print(f"GitHub refused to start a sign-in: {started.get('error_description') or started}")
        return 1
    print(f"Enter this code on GitHub: {started['user_code']}\n"
          f"  {started['verification_uri']}\n"
          f"Waiting for you to approve it…")
    webbrowser.open(started["verification_uri"])
    interval = int(started.get("interval", 5))
    deadline = time.time() + int(started.get("expires_in", 900))
    while time.time() < deadline:
        time.sleep(interval)
        answer = post(TOKEN, {
            "client_id": app, "device_code": started["device_code"],
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
        error = answer.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval = int(answer.get("interval", interval + 5))
            continue
        if error:
            print(f"Sign-in did not complete: {answer.get('error_description') or error}")
            return 1
        save(app, stamped(answer))
        print(f"Signed in for {repo}. boardkit now acts for you there, via its "
              f"app, and only on the repositories that app is installed on.")
        return 0
    print("The code expired before it was approved. Run `board auth login` again.")
    return 1


def logout(app: str, repo: str) -> int:
    forget(app)
    print(f"Signed out of {repo}'s app on this machine.")
    return 0
