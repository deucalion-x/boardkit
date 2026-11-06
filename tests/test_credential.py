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


def test_creating_a_board_needs_no_as_me(monkeypatch, capsys):
    """An app with Projects permission may create a board (checked live); only
    linking it is refused, and that is said rather than failing the apply."""
    import pathlib

    from boardkit import admin
    from boardkit.config import Config
    config = Config(root=pathlib.Path("/repo"), owner="octo", repo="octo/widgets")
    made = []
    monkeypatch.setattr(admin, "locate", lambda config: "")
    monkeypatch.setattr(admin.auth, "require", lambda config, tier: None)
    monkeypatch.setattr(admin, "gh_json", lambda *a: made.append(a) or
                        {"number": 7, "id": "PVT_7", "url": ""})
    monkeypatch.setattr(admin, "record_url", lambda *a: None)

    def refused(*args):
        raise gh.BoardError("GitHub refused the link (Resource not accessible by "
                            "integration). … run `board admin link --as-me`")

    monkeypatch.setattr(admin, "attach", refused)
    monkeypatch.setattr(admin, "existing", lambda c, n: None)
    monkeypatch.setattr(admin, "plan", lambda *a: [])
    monkeypatch.setattr(admin, "missing_labels", lambda c: [])
    monkeypatch.setattr(admin, "missing_issue_types", lambda c: [])
    monkeypatch.setattr(admin, "apply_views", lambda *a, **k: None)
    monkeypatch.setattr(admin, "fetch_fields", lambda *a: [])
    credential.configure("octo/widgets")
    admin.apply(config, browser=False)
    assert made and made[0][:2] == ("project", "create")
    out = capsys.readouterr().out
    assert "not linked to octo/widgets" in out and "board admin link --as-me" in out


def test_a_refused_link_says_what_to_run_instead(monkeypatch):
    import pathlib

    from boardkit import admin
    from boardkit.config import Config
    config = Config(root=pathlib.Path("/repo"), owner="octo", repo="octo/widgets")
    answers = iter([{"data": {"repository": {"id": "R_1"}}},
                    {"data": {"linkProjectV2ToRepository": None}, "errors": [
                        {"message": "Resource not accessible by integration"}]}])
    monkeypatch.setattr(gh, "graphql_partial", lambda document: next(answers))
    with pytest.raises(gh.BoardError, match=r"not accessible by integration.*--as-me"):
        admin.attach(config, "PVT_7", "7")


def test_the_token_form_is_prefilled_for_board_work_on_one_repo():
    import pathlib
    from urllib.parse import parse_qs, urlsplit

    from boardkit.auth import prefilled
    from boardkit.config import Config
    link = prefilled(Config(root=pathlib.Path("/repo"), owner="octo", repo="octo/widgets"))
    asked = parse_qs(urlsplit(link).query)
    assert asked["target_name"] == ["octo"]
    assert asked["issues"] == ["write"]
    assert asked["pull_requests"] == ["read"] and "contents" not in asked
    assert asked["organization_projects"] == ["write"]
    assert len(asked["name"][0]) <= 40


def test_an_expired_token_says_how_to_replace_it(monkeypatch):
    monkeypatch.setenv(credential.ENV, "stale")
    monkeypatch.setattr(gh.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: subprocess.CompletedProcess(
        args, 1, "", "HTTP 401: Bad credentials (https://api.github.com/graphql)"))
    credential.configure("octo/widgets")
    with pytest.raises(gh.BoardError, match=r"expired or been revoked.*BOARDKIT_TOKEN"):
        gh.gh("api", "user")


def test_an_expired_app_sign_in_says_to_sign_in_again(monkeypatch):
    from boardkit import login
    monkeypatch.delenv(credential.ENV, raising=False)
    monkeypatch.setattr(login, "access_token", lambda app: "ghu_stale")
    monkeypatch.setattr(login, "signed_in", lambda app: True)
    monkeypatch.setattr(gh.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: subprocess.CompletedProcess(
        args, 1, "", "HTTP 401: Bad credentials"))
    credential.configure("octo/widgets", app_id="Iv1.x")
    with pytest.raises(gh.BoardError, match="board auth login"):
        gh.gh("api", "user")

