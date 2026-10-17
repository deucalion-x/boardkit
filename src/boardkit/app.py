"""Create a repository's own GitHub App in one command.

GitHub's app manifest flow: a local page posts the app's definition to the
organisation's "new app" form, the person clicks Create, GitHub redirects back
here with a one-time code, and the code is exchanged for the app. The
exchange also returns a client secret, a private key and a webhook secret.
The device flow needs none of them, so they are dropped without ever being
written down: the safest key is the one nobody holds.

What a manifest cannot set is said instead of guessed: device flow is a
checkbox on the app's page, so the command opens it and names it.
"""
from __future__ import annotations

import html
import http.server
import json
import re
import secrets
import socketserver
import threading
import urllib.request
import webbrowser

from .config import Config
from .gh import BoardError

# What boardkit's own calls use: the board and its issues. Code and pull
# requests are written through the person's git and `gh`, never boardkit, so
# the app holds no write on them. Checks and statuses are read only for a pull
# request card's checks, which a pull tolerates missing.
PERMISSIONS = {
    "issues": "write", "pull_requests": "read",
    "checks": "read", "statuses": "read", "metadata": "read",
    "organization_projects": "write",
}
WAIT_SECONDS = 600


def name_for(config: Config) -> str:
    """`boardkit-<org>-<repo>`, within GitHub's 34-character limit."""
    return f"boardkit-{config.repo.replace('/', '-')}"[:34].rstrip("-")


def manifest(config: Config, redirect: str) -> dict:
    return {
        "name": name_for(config),
        "url": f"https://github.com/{config.repo}",
        "description": f"Lets boardkit read and update the board and issues of "
                       f"{config.repo}, acting for the person signed in.",
        "redirect_url": redirect,
        "public": False,
        "hook_attributes": {"url": "https://example.invalid/unused", "active": False},
        "request_oauth_on_install": False,
        "default_permissions": PERMISSIONS,
        "default_events": [],
    }


def page(owner: str, org: bool, definition: dict, state: str) -> str:
    """The form that posts the manifest, submitted as soon as it loads."""
    action = (f"https://github.com/organizations/{owner}/settings/apps/new"
              f"?state={state}")
    if not org:
        action = f"https://github.com/settings/apps/new?state={state}"
    value = html.escape(json.dumps(definition), quote=True)
    return (f'<form id="f" method="post" action="{html.escape(action, quote=True)}">'
            f'<input type="hidden" name="manifest" value="{value}"></form>'
            "<p>Sending you to GitHub to create the app…</p>"
            "<script>document.getElementById('f').submit()</script>")


def convert(code: str) -> dict:
    """Exchange the one-time code; keep only what is public."""
    request = urllib.request.Request(
        f"https://api.github.com/app-manifests/{code}/conversions", data=b"",
        method="POST", headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        made = json.loads(response.read())
    # Client secret, private key and webhook secret are never kept.
    kept = {key: made.get(key, "") for key in ("client_id", "slug", "html_url")}
    kept["owner"] = (made.get("owner") or {}).get("login", "")
    return kept


def check_owner(config: Config, made: dict) -> None:
    """Refuse an app the browser created under some other account.

    The browser decides who owns the app: signed in as an account outside the
    organisation, GitHub quietly falls back to a personal app. Nothing fails
    until `board admin apply`, so the mismatch is named here, before the
    config records the wrong app.
    """
    if made["owner"].lower() != config.owner.lower():
        raise BoardError(
            f"{made['slug']} was created under {made['owner'] or 'an unknown account'}, "
            f"not {config.owner}, so it cannot reach this board; nothing was "
            f"recorded. Sign into github.com as an account that can administer "
            f"{config.owner}, delete {made['slug']} from "
            f"https://github.com/settings/apps, and run this again.")


def remember(config: Config, client_id: str) -> None:
    """Write `app_client_id` where the repository's identity is kept."""
    file = config.identity_source or config.source
    if file is None or not file.exists():
        raise BoardError(f"no config file to record app_client_id = {client_id!r} in")
    text = file.read_text(encoding="utf-8")
    line = f'app_client_id = "{client_id}"'
    if re.search(r"^app_client_id\s*=", text, re.MULTILINE):
        text = re.sub(r"^app_client_id\s*=.*$", line, text, count=1, flags=re.MULTILINE)
    else:
        text = re.sub(r"^(repo\s*=.*)$", rf"\1\n{line}", text, count=1, flags=re.MULTILINE)
    file.write_text(text, encoding="utf-8")


def create(config: Config) -> int:
    """Run the manifest flow end to end."""
    print(f"Opening GitHub to create {name_for(config)} for {config.repo}.\n"
          "Check the name, click Create GitHub App, and come back here.")
    made = through_browser(config.owner, config.owner_type == "org",
                           lambda redirect: manifest(config, redirect))
    if made is None:
        return 1
    check_owner(config, made)
    remember(config, made["client_id"])
    settings = (f"https://github.com/organizations/{config.owner}/settings/apps/{made['slug']}"
                if config.owner_type == "org" else
                f"https://github.com/settings/apps/{made['slug']}")
    where = config.source.name if config.source else "the config"
    print(f"Created {made['slug']} and recorded app_client_id in {where}.\n"
          f"Its secrets were not kept: boardkit signs in without them.\n\n"
          f"Two clicks are left, which GitHub does not let a manifest make:\n"
          f"  1. Tick \"Enable Device Flow\" on {settings}\n"
          f"  2. Install it on {config.repo} only:\n"
          f"     https://github.com/apps/{made['slug']}/installations/new\n"
          f"Then run `board auth login`.")
    webbrowser.open(settings)
    return 0


def through_browser(owner: str, org: bool, definition) -> dict | None:
    """Post the manifest `definition(redirect)` from a local page, wait for
    GitHub to send the person back, and exchange the code: the app's public
    parts, or None when nothing came back."""
    state = secrets.token_urlsafe(16)
    caught: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):  # quiet
            pass

        def do_GET(self):
            from urllib.parse import parse_qs, urlsplit
            asked = urlsplit(self.path)
            if asked.path == "/done":
                query = parse_qs(asked.query)
                if query.get("state", [""])[0] == state:
                    caught["code"] = query.get("code", [""])[0]
                body = "<p>Done. You can close this tab and return to the terminal.</p>"
            else:
                body = page(owner, org, definition(f"http://127.0.0.1:{port}/done"), state)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode())

    with socketserver.TCPServer(("127.0.0.1", 0), Handler) as server:
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        webbrowser.open(f"http://127.0.0.1:{port}/")
        waited = 0.0
        while "code" not in caught and waited < WAIT_SECONDS:
            threading.Event().wait(0.5)
            waited += 0.5
        server.shutdown()
    if not caught.get("code"):
        print("No app came back from GitHub; nothing was changed.")
        return None
    return convert(caught["code"])
