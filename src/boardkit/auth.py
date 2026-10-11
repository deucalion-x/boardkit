"""What the current `gh` login is allowed to do, before it is asked to do it.

Two different questions, answered two different ways:

- **Scopes** come from `gh auth status`, which is local and costs no API call.
  They decide whether a tier is permitted at all.
- **Visibility** — can this account see the repository, does the project exist
  — needs a real call, so it is probed once and cached.

The cache is keyed by the token's scope string. A refreshed or revoked token
changes the key, the old entry is never read again, and there is no expiry to
tune.

Reads of the local store are never gated: they touch nothing. Pushes and admin
verbs are, because a half-applied admin step leaves a board in a state nothing
cleans up.
"""
from __future__ import annotations

import getpass
import json
import os
import re
import webbrowser
from urllib.parse import urlencode

from pydantic import BaseModel, ConfigDict

from . import credential
from .config import Config
from .gh import BoardError, probe
from .store import directory

CACHE = "auth.json"

# What each tier needs. `project` is GitHub's read-write project scope and
# covers `read:project`, so either satisfies a read.
TIERS: dict[str, tuple[str, ...]] = {
    "local": (),
    "read": ("repo", "read:project|project"),
    "write": ("repo", "project"),
    "admin": ("project",),
}
ACCOUNT = re.compile(r"Logged in to (\S+) account (\S+)")
SCOPES = re.compile(r"Token scopes:(.*)")
# A fine-grained PAT carries permissions, not scopes, so `gh auth status`
# reports none for it. `Capability.fine_grained` records that, because
# treating it as "no permissions" would refuse the narrowest token available.


class Capability(BaseModel):
    """The answer to "may I", cached under the scopes it was true for."""
    model_config = ConfigDict(extra="forbid")

    account: str = ""
    host: str = "github.com"
    scopes: tuple[str, ...] = ()
    # True for a fine-grained PAT, whose permissions `gh` cannot report. Scope
    # checks are then meaningless and the probe decides instead.
    fine_grained: bool = False
    can_see_repo: bool | None = None
    can_see_project: bool | None = None

    @property
    def logged_in(self) -> bool:
        return bool(self.account)

    @property
    def key(self) -> str:
        """Identity plus scopes: the cache is only valid for this exact pair."""
        return f"{self.host}/{self.account}:{','.join(sorted(self.scopes))}"

    def has(self, requirement: str) -> bool:
        """A requirement is satisfied if any of its alternatives is held."""
        return any(scope in self.scopes for scope in requirement.split("|"))

    def missing(self, tier: str) -> list[str]:
        """What this tier lacks. A token whose permissions cannot be read is
        never refused here: the visibility probe is the honest answer, and a
        wrong `no` blocks work that would have succeeded."""
        if self.fine_grained:
            return []
        return [need for need in TIERS[tier] if not self.has(need)]

    def allows(self, tier: str) -> bool:
        return self.logged_in and not self.missing(tier)


def parse_status(output: str) -> Capability:
    """Read `gh auth status`. Local, so this is the cheap half of the answer.

    It lists every login it knows; only the active one's block is read, or a
    focused token would be reported with the personal login's scopes.
    """
    blocks = re.split(r"(?=^\s*✓ Logged in to )", output, flags=re.MULTILINE)
    active = [b for b in blocks if "Active account: true" in b]
    output = active[0] if active else output
    account = ACCOUNT.search(output)
    scopes = SCOPES.search(output)
    found: tuple[str, ...] = ()
    if scopes:
        found = tuple(sorted(part.strip().strip("'\"")
                             for part in scopes.group(1).split(",") if part.strip()))
    return Capability(
        host=account.group(1) if account else "github.com",
        account=account.group(2) if account else "",
        scopes=found,
        fine_grained=bool(account) and not found,
    )


def status() -> Capability:
    # Asked first, so a missing focused token is named as such rather than
    # read back by the probe as nobody being logged in.
    try:
        credential.environment()
    except credential.Missing as missing:
        raise BoardError(str(missing)) from None
    _, output = probe(["gh", "auth", "status"])
    return parse_status(output)


def visibility(config: Config, capability: Capability) -> Capability:
    """The half that needs the network: can this account see these two things."""
    repo_ok, _ = probe(["gh", "repo", "view", config.repo, "--json", "name"])
    # No number yet means there is nothing to look for, not that it is hidden:
    # the board may simply not have been created. Leave it unknown.
    project_ok = None
    if config.project:
        project_ok, _ = probe(["gh", "project", "view", config.project,
                               "--owner", config.owner, "--format", "json"])
    else:
        listed, _ = probe(["gh", "project", "list", "--owner", config.owner,
                           "--limit", "100", "--format", "json"])
        project_ok = True if listed else None
    return capability.model_copy(update={"can_see_repo": repo_ok,
                                         "can_see_project": project_ok})


def cached(config: Config) -> Capability | None:
    path = config.path(".boardkit") / CACHE
    if not path.exists():
        return None
    stored = json.loads(path.read_text(encoding="utf-8"))
    capability = Capability.model_validate(stored["capability"])
    # The scopes moved, so what was probed under the old ones proves nothing.
    return capability if stored.get("key") == status().key else None


def save(config: Config, capability: Capability) -> None:
    (directory(config) / CACHE).write_text(
        json.dumps({"key": capability.key,
                    "capability": capability.model_dump(mode="json")}, indent=2) + "\n",
        encoding="utf-8")


def capability(config: Config, refresh: bool = False) -> Capability:
    """Scopes always, visibility from cache unless asked to re-probe."""
    if not refresh:
        hit = cached(config)
        if hit is not None:
            return hit
    found = status()
    if not found.logged_in:
        return found
    found = visibility(config, found)
    save(config, found)
    return found


def require(config: Config, tier: str) -> Capability:
    """Refuse before acting, and name the command that fixes it."""
    found = capability(config)
    if not found.logged_in:
        raise BoardError("not logged in to GitHub. Run `gh auth login`.")
    missing = found.missing(tier)
    if missing:
        wanted = ",".join(need.split("|")[-1] for need in missing)
        raise BoardError(
            f"the `{tier}` tier needs scope(s) {wanted}, which this token does "
            f"not have (it has: {', '.join(found.scopes) or 'none'}). Run:\n"
            f"  gh auth refresh -s {wanted}")
    if found.can_see_repo is False:
        raise BoardError(
            f"{found.account} cannot see {config.repo}. Check the active account "
            f"with `gh auth status`, or switch it with `gh auth switch`.")
    if found.can_see_project is False:
        raise BoardError(
            f"{found.account} cannot see the projects of {config.owner}. "
            f"Check `owner` in [tool.boardkit].")
    return found


def report(config: Config, refresh: bool = False) -> int:
    print(f"Using   : {credential.source()}")
    try:
        found = capability(config, refresh=refresh)
    except BoardError as refused:
        print(refused)
        return 1
    if not found.logged_in:
        print("Not logged in. Run `gh auth login`.")
        return 1
    print(f"Account : {found.account} on {found.host}")
    if found.fine_grained:
        print("Token   : fine-grained or env-supplied; permissions are not "
              "reported, so only the probe below can tell you what works")
    else:
        print(f"Scopes  : {', '.join(found.scopes) or 'none'}")
    named = f"project {config.project}" if config.project else "projects"
    print(f"Sees    : {config.repo}={shown(found.can_see_repo)} "
          f"{named}={shown(found.can_see_project)}")
    for tier in TIERS:
        missing = found.missing(tier)
        if missing:
            detail = f"needs {', '.join(missing)}"
        elif found.fine_grained and tier != "local":
            detail = "not refused; try it"
        else:
            detail = "ok"
        print(f"  {tier:6} {detail}")
    return 0


def shown(value: bool | None) -> str:
    return "?" if value is None else ("yes" if value else "no")


# Where each kind of token is made. A classic token's page takes its scopes
# in the URL; a fine-grained one's permissions are picked on the page.
FINE_GRAINED = "https://github.com/settings/personal-access-tokens/new"
CLASSIC = "https://github.com/settings/tokens/new?scopes=project&description=boardkit"


def prefilled(config: Config) -> str:
    """GitHub's fine-grained token form with everything it lets a link set.

    The token is an agent's working credential for one repository: plan on
    the board, write issues and pull requests, push branches. The limit that
    keeps it safe is the one repository and its organisation's board, not a
    crippled agent inside it. The repository itself cannot be pre-selected, so
    that one choice is left to the person.
    """
    return FINE_GRAINED + "?" + urlencode({
        "name": f"boardkit {config.repo}"[:40],
        "description": f"Agent token for {config.repo} only: code, issues, pull "
                       f"requests and the organisation's project board.",
        "target_name": config.owner,
        "expires_in": 90,
        "contents": "write",
        "issues": "write",
        "pull_requests": "write",
        "actions": "read",
        # The GitHub App name for an organisation's Projects permission; the
        # form is documented to validate what it is given.
        "organization_projects": "write",
    })


def setup(config: Config) -> int:
    """Store this repository's focused token, after checking it can see the
    repository and the board."""
    if not credential.has_keychain():
        print(f"No keychain here. Export {credential.ENV} with a focused token instead.")
        return 1
    if config.owner_type == "user":
        # Fine-grained tokens offer no Projects permission on a personal
        # account, so they cannot reach a user-owned board at all. A classic
        # token with only `project` can: every board of the account, but no
        # repository code.
        link = CLASSIC
        print(f"Opening GitHub's classic token form with only the project scope.\n"
              f"A user-owned board cannot be reached by a fine-grained token, so\n"
              f"this one reaches all of {config.owner}'s boards but no code.\n"
              f"Add the repo scope only if {config.repo} is private. Pick an\n"
              f"expiry, generate the token and paste it below.\n\n  {link}\n")
    else:
        link = prefilled(config)
        print(f"Opening GitHub's token form for {config.repo}, filled in except for\n"
              f"one choice: under Repository access pick \"Only select repositories\"\n"
              f"and choose {config.repo}. Check that Organization permissions show\n"
              f"Projects as read and write. Then generate the token and paste it\n"
              f"below.\n\n  {link}\n")
    webbrowser.open(link)
    token = getpass.getpass("Token: ").strip()
    if not token:
        print("No token given; nothing stored.")
        return 1
    # Checked under the new token itself before it replaces anything.
    before = os.environ.get(credential.ENV)
    os.environ[credential.ENV] = token
    try:
        found = visibility(config, status())
    finally:
        if before is None:
            del os.environ[credential.ENV]
        else:
            os.environ[credential.ENV] = before
    if not found.logged_in or not found.can_see_repo:
        print(f"That token cannot see {config.repo}; nothing stored.")
        return 1
    credential.store(config.repo, token)
    print(f"Stored the focused token for {config.repo} in the keychain.")
    if found.can_see_project is False:
        print(f"  warning: it cannot see the projects of {config.owner}, so "
              f"`board sync` will fail. Re-run with a token that can.")
    return 0
