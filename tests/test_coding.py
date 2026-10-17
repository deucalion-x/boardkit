"""The coding identity: an agent's git and gh, and nothing beyond them."""
import subprocess
import time

import pytest

from boardkit import cli, coding, doctor, login

SIGNED_IN = {"client_id": "Iv1code", "access_token": "ghu_live", "refresh_token": "ghr_r",
             "expires_at": 0, "refresh_expires_at": 0}


@pytest.fixture
def signed_in(monkeypatch):
    record = dict(SIGNED_IN)
    monkeypatch.setattr(coding, "stored", lambda: record)
    saved: list[dict] = []
    monkeypatch.setattr(coding, "save", saved.append)
    return saved


def test_the_app_holds_code_permissions_and_nothing_administrative():
    assert coding.PERMISSIONS["contents"] == "write"
    assert coding.PERMISSIONS["pull_requests"] == "write"
    for never in ("administration", "workflows", "members",
                  "organization_administration", "organization_projects"):
        assert never not in coding.PERMISSIONS


def test_git_gets_the_coding_token_for_github_over_https(signed_in):
    answer = coding.git_credential("get", "protocol=https\nhost=github.com\n")
    assert answer == "username=x-access-token\npassword=ghu_live\n"


@pytest.mark.parametrize("request_text", ["protocol=https\nhost=gitlab.com\n",
                                          "protocol=ssh\nhost=github.com\n"])
def test_another_host_or_protocol_is_not_answered(signed_in, request_text):
    assert coding.git_credential("get", request_text) == ""


def test_store_and_erase_are_not_ours(signed_in):
    assert coding.git_credential("store", "protocol=https\nhost=github.com\n") == ""
    assert coding.git_credential("erase", "protocol=https\nhost=github.com\n") == ""


def test_nobody_signed_in_leaves_git_without_an_answer():
    assert coding.git_credential("get", "protocol=https\nhost=github.com\n") == ""


def test_a_lapsing_token_is_renewed_and_kept(signed_in, monkeypatch):
    soon = int(time.time()) + 10
    record = coding.stored()
    record.update(expires_at=soon, refresh_expires_at=soon + 10_000)
    monkeypatch.setattr(login, "post", lambda url, fields: {
        "access_token": "ghu_new", "refresh_token": "ghr_new",
        "expires_in": 28800, "refresh_token_expires_in": 15_000_000})
    assert coding.token() == "ghu_new"
    assert signed_in[-1]["client_id"] == "Iv1code"
    assert signed_in[-1]["access_token"] == "ghu_new"


def test_an_expired_refresh_says_how_to_sign_in_again(signed_in):
    record = coding.stored()
    record.update(expires_at=1, refresh_expires_at=1)
    with pytest.raises(login.LoginError, match="board auth login --coding"):
        coding.token()


def test_agent_env_is_empty_without_a_coding_identity():
    assert coding.agent_env("/x/board") == ""


def test_agent_env_points_git_at_the_helper_alone_and_wraps_gh(signed_in, tmp_path):
    lines = coding.agent_env("/opt/boardkit/bin/board")
    assert "export GIT_CONFIG_VALUE_0=\n" in lines
    assert "'!/opt/boardkit/bin/board auth git-credential'" in lines
    assert "gh() {" in lines and "auth token --coding" in lines
    # It is sourced by bash before each command: it must parse.
    script = tmp_path / "env.sh"
    script.write_text(lines)
    subprocess.run(["bash", "-n", str(script)], check=True)


def test_git_resolves_the_helper_from_the_session_environment(signed_in, tmp_path):
    """What git itself makes of those variables: our helper, after a reset."""
    env_file = tmp_path / "env.sh"
    env_file.write_text(coding.agent_env("/opt/boardkit/bin/board"))
    command = f"source {env_file}; git config --get-all credential.https://github.com.helper"
    out = subprocess.run(
        ["bash", "-c", command],
        capture_output=True, text=True, check=True, cwd=tmp_path).stdout
    assert out.splitlines()[-2:] == ["", "!/opt/boardkit/bin/board auth git-credential"]


def test_token_command_fails_without_a_sign_in(capsys):
    assert cli.dispatch("auth", ["token", "--coding"]) == 1
    assert "board auth login --coding" in capsys.readouterr().err


def test_token_command_prints_the_live_token(signed_in, capsys):
    assert cli.dispatch("auth", ["token", "--coding"]) == 0
    assert capsys.readouterr().out.strip() == "ghu_live"


def test_the_board_login_is_not_taken_for_the_coding_one():
    assert coding.main(["login"]) == -1
    assert coding.main([]) == -1


def test_doctor_warns_when_the_personal_login_is_kept_beside_a_coding_identity(
        signed_in, monkeypatch):
    monkeypatch.setattr(doctor, "personal_login", lambda: True)
    monkeypatch.setenv("PATH", "")
    assert any("gh auth logout" in problem for problem in doctor.findings(None))


def test_doctor_says_nothing_of_the_personal_login_without_a_coding_identity(monkeypatch):
    monkeypatch.setattr(doctor, "personal_login", lambda: True)
    monkeypatch.setenv("PATH", "")
    assert not any("gh auth logout" in problem for problem in doctor.findings(None))
