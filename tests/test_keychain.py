"""The platform's credential store: the right command, the secret never on
argv where avoidable, and no store at all rather than a file."""
import subprocess

import pytest

from boardkit import keychain


@pytest.fixture
def ran(monkeypatch):
    calls = []

    def fake(args, **kw):
        calls.append((args, kw.get("input")))
        return subprocess.CompletedProcess(args, 0, "s3cret\n", "")
    monkeypatch.setattr(subprocess, "run", fake)
    return calls


def on(monkeypatch, platform, tool):
    monkeypatch.setattr(keychain.sys, "platform", platform)
    monkeypatch.setattr(keychain.shutil, "which", lambda name: f"/bin/{name}" if name == tool else None)


def test_macos_reads_and_writes_the_login_keychain(monkeypatch, ran):
    on(monkeypatch, "darwin", "security")
    assert keychain.get("boardkit", "octo/widgets") == "s3cret"
    keychain.put("boardkit", "octo/widgets", "tok", "label")
    assert ran[0][0][:2] == ["security", "find-generic-password"]
    assert ran[1][0][:2] == ["security", "add-generic-password"]


def test_linux_passes_the_secret_on_stdin(monkeypatch, ran):
    on(monkeypatch, "linux", "secret-tool")
    assert keychain.get("boardkit", "octo/widgets") == "s3cret"
    keychain.put("boardkit", "octo/widgets", "tok", "label")
    args, given = ran[1]
    assert args[:2] == ["secret-tool", "store"] and "tok" not in args and given == "tok"
    keychain.delete("boardkit", "octo/widgets")
    assert ran[2][0][:2] == ["secret-tool", "clear"]


def test_without_a_store_there_is_no_file_fallback(monkeypatch, ran):
    on(monkeypatch, "linux", None)
    assert not keychain.available() and keychain.get("boardkit", "x") == ""
    with pytest.raises(RuntimeError, match="no credential store"):
        keychain.put("boardkit", "x", "tok", "label")
    assert ran == []
