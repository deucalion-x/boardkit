"""Sign in through the boardkit GitHub App, with GitHub's device flow.

The token it gets acts for the person, marked "via boardkit", and reaches only
the repositories the app is installed on, with only the app's permissions. It
lasts eight hours and is renewed from a refresh token, silently, so nobody
fills in a token form or regenerates anything. No secret ships with boardkit:
the device flow needs the app's client ID alone, which is public, and a token
it produced refreshes without the client secret.

Everything is filed in the keychain under one entry per GitHub host. Unlike a
per-repository token, one sign-in covers every repository the app reaches.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.parse
import urllib.request
import webbrowser

from . import credential

# The client ID of boardkit-deucalion, the boardkit app. Public by design;
# overridable for anyone running their own copy of the app.
CLIENT_ID = "Iv23liPclAFWDh1mSceP"
CLIENT_ENV = "BOARDKIT_APP_CLIENT_ID"
HOST = "github.com"
SERVICE = "boardkit-app"
DEVICE = f"https://{HOST}/login/device/code"
TOKEN = f"https://{HOST}/login/oauth/access_token"
# Refreshed this long before it would lapse, so a call never starts on a
# token that dies halfway through it.
MARGIN = 300


class LoginError(RuntimeError):
    """Signing in, or staying signed in, did not work. Says what to do."""


def client_id() -> str:
    return os.environ.get(CLIENT_ENV) or CLIENT_ID


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


def load() -> dict | None:
    if not credential.has_keychain():
        return None
    result = subprocess.run(
        ["security", "find-generic-password", "-s", SERVICE, "-a", HOST, "-w"],
        capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def save(tokens: dict) -> None:
    subprocess.run(
        ["security", "add-generic-password", "-U", "-s", SERVICE, "-a", HOST,
         "-l", "boardkit sign-in", "-w", json.dumps(tokens)],
        capture_output=True, text=True, check=True)


def forget() -> None:
    subprocess.run(["security", "delete-generic-password", "-s", SERVICE, "-a", HOST],
                   capture_output=True, text=True, check=False)


def signed_in() -> bool:
    return load() is not None


def access_token() -> str:
    """A live token from the stored sign-in, refreshed first when it is due.

    "" when nobody has signed in, so the caller can fall back to a
    per-repository token.
    """
    tokens = load()
    if tokens is None:
        return ""
    # A token from an app that does not expire them carries no expiry.
    if not tokens.get("expires_at") or tokens["expires_at"] - MARGIN > time.time():
        return tokens["access_token"]
    if not tokens.get("refresh_token") or tokens.get("refresh_expires_at", 0) <= time.time():
        raise LoginError("your boardkit sign-in has expired. Run `board auth login`.")
    answer = post(TOKEN, {"client_id": client_id(), "grant_type": "refresh_token",
                          "refresh_token": tokens["refresh_token"]})
    if "access_token" not in answer:
        raise LoginError(f"GitHub would not renew your boardkit sign-in "
                         f"({answer.get('error', 'no reason given')}). Run `board auth login`.")
    tokens = stamped(answer)
    save(tokens)
    return tokens["access_token"]


def login() -> int:
    """The device flow: show a code, open the page, wait for approval."""
    if not client_id():
        print("The boardkit GitHub App is not registered yet, so there is nothing "
              f"to sign in to. Set {CLIENT_ENV} to your own app's client ID, or "
              "use `board auth setup` for a per-repository token.")
        return 1
    if not credential.has_keychain():
        print(f"No keychain here to keep a sign-in. Export {credential.ENV} instead.")
        return 1
    started = post(DEVICE, {"client_id": client_id()})
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
            "client_id": client_id(), "device_code": started["device_code"],
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
        save(stamped(answer))
        print("Signed in. boardkit now acts for you, via the boardkit app, on the "
              "repositories it is installed on.")
        return 0
    print("The code expired before it was approved. Run `board auth login` again.")
    return 1


def logout() -> int:
    forget()
    print("Signed out of the boardkit app on this machine.")
    return 0
