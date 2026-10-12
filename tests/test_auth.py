import pathlib

import pytest

from boardkit import auth
from boardkit.auth import Capability, parse_status
from boardkit.config import Config
from boardkit.gh import BoardError

LOGGED_IN = """github.com
  ✓ Logged in to github.com account antoine-novatix (keyring)
  - Active account: true
  - Git operations protocol: ssh
  - Token: gho_************************************
  - Token scopes: 'admin:public_key', 'gist', 'read:org', 'repo'
"""
LOGGED_OUT = "You are not logged into any GitHub hosts. To log in, run: gh auth login\n"


def config(tmp_path: pathlib.Path) -> Config:
    return Config(root=tmp_path, owner="octo", project="4", repo="octo/widgets")


def test_scopes_and_account_come_from_a_local_command():
    found = parse_status(LOGGED_IN)
    assert found.account == "antoine-novatix" and found.host == "github.com"
    assert found.scopes == ("admin:public_key", "gist", "read:org", "repo")
    assert found.logged_in


def test_being_logged_out_is_an_answer_not_a_crash():
    assert not parse_status(LOGGED_OUT).logged_in


def test_a_repo_token_without_project_scope_can_read_nothing_of_the_board():
    found = parse_status(LOGGED_IN)
    assert found.allows("local")
    assert not found.allows("read") and found.missing("read") == ["read:project|project"]


def test_the_write_project_scope_also_satisfies_a_read():
    found = Capability(account="octo", scopes=("repo", "project"))
    assert found.allows("read") and found.allows("write") and found.allows("admin")


def test_read_project_alone_does_not_allow_a_push():
    found = Capability(account="octo", scopes=("repo", "read:project"))
    assert found.allows("read") and not found.allows("write")


def test_a_refused_tier_names_the_command_that_fixes_it(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "capability", lambda config, refresh=False: parse_status(LOGGED_IN))
    with pytest.raises(BoardError, match=r"gh auth refresh -s project"):
        auth.require(config(tmp_path), "write")


def test_being_logged_out_points_at_login_rather_than_a_scope(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "capability", lambda config, refresh=False: parse_status(LOGGED_OUT))
    with pytest.raises(BoardError, match="gh auth login"):
        auth.require(config(tmp_path), "read")


def test_a_project_it_cannot_see_blames_the_config_not_the_scopes(tmp_path, monkeypatch):
    allowed = Capability(account="octo", scopes=("repo", "project"),
                         can_see_repo=True, can_see_project=False)
    monkeypatch.setattr(auth, "capability", lambda config, refresh=False: allowed)
    with pytest.raises(BoardError, match=r"\[tool.boardkit\]"):
        auth.require(config(tmp_path), "read")


def test_the_cache_is_dropped_when_the_token_scopes_change(tmp_path, monkeypatch):
    settings = config(tmp_path)
    probed = Capability(account="octo", scopes=("repo", "project"),
                        can_see_repo=True, can_see_project=True)
    monkeypatch.setattr(auth, "fingerprint", lambda: "first")
    auth.save(settings, probed)

    # A refreshed token: same account, different scopes, so the probe is void.
    monkeypatch.setattr(auth, "fingerprint", lambda: "second")
    monkeypatch.setattr(auth, "status", lambda: Capability(account="octo",
                                                           scopes=("repo",)))
    assert auth.cached(settings) is None


def test_the_same_token_is_trusted_without_asking_github(tmp_path, monkeypatch):
    """`gh auth status` asks GitHub about every login, a second or more on
    each command that checked it, though the answer cannot have moved."""
    settings = config(tmp_path)
    monkeypatch.setattr(auth, "fingerprint", lambda: "same")
    auth.save(settings, Capability(account="octo", scopes=("repo", "project"),
                                   can_see_repo=True, can_see_project=True))

    def asked():
        raise AssertionError("gh auth status was asked")

    monkeypatch.setattr(auth, "status", asked)
    assert auth.cached(settings).can_see_project


def test_a_new_token_with_the_same_scopes_keeps_the_probe_and_is_remembered(
        tmp_path, monkeypatch):
    """An app sign-in renews its token every few hours. The scopes do not move,
    so the visibility probed under the old one still holds."""
    settings = config(tmp_path)
    scopes = ("repo", "project")
    monkeypatch.setattr(auth, "fingerprint", lambda: "old")
    auth.save(settings, Capability(account="octo", scopes=scopes,
                                   can_see_repo=True, can_see_project=True))

    asked = []
    monkeypatch.setattr(auth, "fingerprint", lambda: "renewed")
    monkeypatch.setattr(auth, "status",
                        lambda: asked.append(1) or Capability(account="octo", scopes=scopes))
    assert auth.cached(settings).can_see_project
    assert auth.cached(settings).can_see_project
    assert len(asked) == 1


def test_an_unknown_token_always_takes_the_slow_check(tmp_path, monkeypatch):
    settings = config(tmp_path)
    monkeypatch.setattr(auth, "fingerprint", lambda: "")
    auth.save(settings, Capability(account="octo", scopes=("repo",)))
    monkeypatch.setattr(auth, "status", lambda: Capability(account="octo",
                                                           scopes=("project",)))
    assert auth.cached(settings) is None


def test_visibility_is_probed_once_and_then_read_from_the_cache(tmp_path, monkeypatch):
    settings = config(tmp_path)
    calls = []
    monkeypatch.setattr(auth, "status",
                        lambda: Capability(account="octo", scopes=("repo", "project")))

    def probe(config, capability):
        calls.append(1)
        return capability.model_copy(update={"can_see_repo": True, "can_see_project": True})

    monkeypatch.setattr(auth, "visibility", probe)
    assert auth.capability(settings).can_see_project
    assert auth.capability(settings).can_see_project
    assert len(calls) == 1
    assert auth.capability(settings, refresh=True) and len(calls) == 2


FINE_GRAINED = """github.com
  ✓ Logged in to github.com account agerardin (keyring)
  - Active account: true
  - Git operations protocol: ssh
  - Token: github_pat_************************************
"""


def test_a_fine_grained_token_is_not_refused_for_reporting_no_scopes():
    found = parse_status(FINE_GRAINED)
    assert found.logged_in and found.fine_grained and found.scopes == ()
    # Refusing here would block the narrowest token available.
    assert found.allows("read") and found.allows("write") and found.allows("admin")


def test_an_oauth_token_is_still_gated_on_its_scopes():
    assert not parse_status(LOGGED_IN).fine_grained
    assert not parse_status(LOGGED_IN).allows("read")


def test_a_fine_grained_token_still_fails_on_what_it_cannot_see(tmp_path, monkeypatch):
    blind = parse_status(FINE_GRAINED).model_copy(
        update={"can_see_repo": True, "can_see_project": False})
    monkeypatch.setattr(auth, "capability", lambda config, refresh=False: blind)
    with pytest.raises(BoardError, match="cannot see the projects"):
        auth.require(config(tmp_path), "read")


def test_only_the_active_account_is_read():
    """A focused token in GH_TOKEN must not borrow the stored login's scopes."""
    from boardkit.auth import parse_status
    output = ("github.com\n"
              "  ✓ Logged in to github.com account octo (GH_TOKEN)\n"
              "  - Active account: true\n"
              "  - Token: github_pat_***\n"
              "  ✓ Logged in to github.com account octo (keyring)\n"
              "  - Active account: false\n"
              "  - Token: gho_***\n"
              "  - Token scopes: 'project', 'repo'\n")
    found = parse_status(output)
    assert found.account == "octo" and found.scopes == () and found.fine_grained


def test_a_failed_visibility_probe_is_asked_again_not_trusted(tmp_path, monkeypatch):
    """One failed call reads the same as being refused. Cached, it reported
    "cannot see the projects" on every command until the token changed."""
    settings = config(tmp_path)
    monkeypatch.setattr(auth, "fingerprint", lambda: "same")
    auth.save(settings, Capability(account="octo", scopes=("repo", "project"),
                                   can_see_repo=True, can_see_project=False))
    assert auth.cached(settings) is None
