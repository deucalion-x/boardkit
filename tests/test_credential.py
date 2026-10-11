"""`gh` runs under the focused token unless the person asks for their own login."""
import subprocess

import pytest

from boardkit import credential, gh


@pytest.fixture
def ran(monkeypatch):
    calls = []

    def fake(args, **kwargs):
        calls.append((args, kwargs.get("env")))
        return subprocess.CompletedProcess(args, 0, "{}", "")
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(gh.shutil, "which", lambda name: f"/bin/{name}")
    return calls


def test_gh_runs_under_the_focused_token(ran, monkeypatch):
    monkeypatch.setenv(credential.ENV, "narrow")
    credential.configure("octo/widgets")
    gh.gh("api", "user")
    assert ran[-1][1]["GH_TOKEN"] == "narrow"


def test_git_keeps_its_own_credentials(ran, monkeypatch):
    monkeypatch.setenv(credential.ENV, "narrow")
    credential.configure("octo/widgets")
    gh.git("status")
    assert ran[-1][1] is None


def test_as_me_uses_the_personal_login(ran, monkeypatch):
    monkeypatch.setenv(credential.ENV, "narrow")
    credential.configure("octo/widgets", elevated=True)
    gh.gh("api", "user")
    assert ran[-1][1] is None


def test_no_focused_token_is_refused_with_the_fix(ran, monkeypatch):
    monkeypatch.delenv(credential.ENV, raising=False)
    monkeypatch.setattr(credential, "has_keychain", lambda: False)
    credential.configure("octo/widgets")
    with pytest.raises(gh.BoardError, match=r"board auth setup.*--as-me"):
        gh.gh("api", "user")
    assert ran == []


def test_creating_a_board_needs_as_me(monkeypatch):
    import pathlib

    from boardkit import admin
    from boardkit.config import Config
    config = Config(root=pathlib.Path("/repo"), owner="octo", repo="octo/widgets")
    monkeypatch.setattr(admin, "locate", lambda config: "")
    monkeypatch.setattr(admin.auth, "require", lambda config, tier: None)
    monkeypatch.setattr(admin, "gh_json", lambda *a: pytest.fail("created"))
    credential.configure("octo/widgets")
    with pytest.raises(gh.BoardError, match="--as-me"):
        admin.apply(config)


def test_the_token_form_is_prefilled_for_agent_work_on_one_repo():
    import pathlib
    from urllib.parse import parse_qs, urlsplit

    from boardkit.auth import prefilled
    from boardkit.config import Config
    link = prefilled(Config(root=pathlib.Path("/repo"), owner="octo", repo="octo/widgets"))
    asked = parse_qs(urlsplit(link).query)
    assert asked["target_name"] == ["octo"]
    assert asked["issues"] == ["write"]
    assert asked["contents"] == ["write"] and asked["pull_requests"] == ["write"]
    assert asked["organization_projects"] == ["write"]
    assert len(asked["name"][0]) <= 40
