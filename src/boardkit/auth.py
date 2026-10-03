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

import json
import re

from pydantic import BaseModel, ConfigDict

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
    """Read `gh auth status`. Local, so this is the cheap half of the answer."""
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
    found = capability(config, refresh=refresh)
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
