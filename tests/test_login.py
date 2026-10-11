"""Signing in through the boardkit app: device flow, silent refresh, precedence."""
import time

import pytest

from boardkit import credential, gh, login

APP = "Iv1.widgets"


@pytest.fixture
def keychain(monkeypatch):
    kept = {}
    monkeypatch.setattr(login, "load", kept.get)
    monkeypatch.setattr(login, "save", kept.__setitem__)
    monkeypatch.setattr(credential, "has_keychain", lambda: True)
    monkeypatch.setattr(login.webbrowser, "open", lambda url: None)
    monkeypatch.setattr(login.time, "sleep", lambda s: None)
    monkeypatch.delenv(credential.ENV, raising=False)
    return kept


def test_the_device_flow_stores_a_token_once_approved(keychain, monkeypatch):
    answers = iter([{"device_code": "d", "user_code": "ABCD-1234", "interval": 1,
                     "verification_uri": "https://github.com/login/device", "expires_in": 900},
                    {"error": "authorization_pending"},
                    {"access_token": "ghu_x", "expires_in": 28800,
                     "refresh_token": "ghr_x", "refresh_token_expires_in": 15897600}])
    sent = []
    monkeypatch.setattr(login, "post", lambda url, fields: sent.append(fields) or next(answers))
    assert login.login(APP, "octo/widgets") == 0
    assert keychain[APP]["access_token"] == "ghu_x"
    # No secret is ever sent: the client ID is all the device flow needs.
    assert not any("client_secret" in fields for fields in sent)


def test_a_due_token_is_refreshed_without_a_secret(keychain, monkeypatch):
    keychain[APP] = {"access_token": "old", "refresh_token": "ghr_x",
                          "expires_at": time.time() + 10, "refresh_expires_at": time.time() + 9999}
    sent = []
    monkeypatch.setattr(login, "post", lambda url, fields: sent.append(fields) or {
        "access_token": "new", "expires_in": 28800, "refresh_token": "ghr_y",
        "refresh_token_expires_in": 15897600})
    assert login.access_token(APP) == "new"
    assert sent[0]["grant_type"] == "refresh_token" and "client_secret" not in sent[0]
    assert keychain[APP]["refresh_token"] == "ghr_y"


def test_a_live_token_is_used_as_it_is(keychain, monkeypatch):
    keychain[APP] = {"access_token": "live", "expires_at": time.time() + 9999}
    monkeypatch.setattr(login, "post", lambda *a: pytest.fail("refreshed"))
    assert login.access_token(APP) == "live"


def test_a_lapsed_sign_in_names_login(keychain):
    keychain[APP] = {"access_token": "old", "refresh_token": "ghr_x",
                          "expires_at": 1, "refresh_expires_at": 1}
    credential.configure("octo/widgets", app_id=APP)
    with pytest.raises(gh.BoardError, match="board auth login"):
        gh.environment(["gh", "api", "user"])


def test_the_sign_in_wins_over_a_repo_token(keychain, monkeypatch):
    keychain[APP] = {"access_token": "app", "expires_at": time.time() + 9999}
    monkeypatch.setattr(credential, "stored", lambda name: "repo-token")
    credential.configure("octo/widgets", app_id=APP)
    assert gh.environment(["gh", "api", "user"])["GH_TOKEN"] == "app"


def test_a_repo_naming_no_app_has_nothing_to_sign_in_to(keychain, capsys):
    assert login.login("", "octo/widgets") == 1
    assert "board auth setup" in capsys.readouterr().out


def test_each_app_keeps_its_own_sign_in(keychain, monkeypatch):
    """One app per repository: the places sign-in is not the boardkit one."""
    keychain[APP] = {"access_token": "for-widgets", "expires_at": time.time() + 9999}
    monkeypatch.setattr(credential, "stored", lambda name: "")
    credential.configure("octo/gadgets", app_id="Iv1.other")
    with pytest.raises(gh.BoardError, match="no GitHub credential for octo/gadgets"):
        gh.environment(["gh", "api", "user"])
