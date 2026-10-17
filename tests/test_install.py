"""boardkit without the plugin: install.sh, and `board doctor` on what it finds.

Each test runs in a home of its own, with a PATH it sets, so nothing here
reads or changes the machine's own installation.
"""
import os
import pathlib
import shutil
import subprocess

import pytest

from boardkit import doctor, skill

ROOT = pathlib.Path(__file__).resolve().parent.parent
GENERATED = skill.GENERATED.splitlines()[0]


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    for name in ("CODEX_HOME", "BOARDKIT_HOME", "BOARDKIT_BIN", "XDG_CACHE_HOME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", os.environ["PATH"])
    return home


def install(home: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": str(home), "BOARDKIT_SOURCE": str(ROOT)}
    return subprocess.run(["bash", str(ROOT / "install.sh"), *args], env=env,
                          capture_output=True, text=True, check=True)


def shipped(name: str) -> str:
    return (ROOT / "plugin" / "skills" / name / "SKILL.md").read_text(encoding="utf-8")


def test_a_fresh_install_links_board_and_writes_the_skills_for_codex(home):
    install(home)
    board = home / ".local" / "bin" / "board"
    assert f'exec "{home}/.local/share/boardkit/plugin/bin/board"' in board.read_text()
    for name in skill.SKILLS:
        assert (home / ".codex/skills" / name / "SKILL.md").read_text() == shipped(name)
    # No Claude Code on this machine, so no Claude skills either.
    assert not (home / ".claude").exists()


def test_claude_gets_the_skills_unless_the_plugin_already_carries_them(home):
    (home / ".claude").mkdir()
    install(home)
    assert (home / ".claude/skills/board/SKILL.md").is_file()

    shutil.rmtree(home / ".claude/skills")
    (home / ".claude/plugins/cache/boardkit").mkdir(parents=True)
    install(home)
    assert not (home / ".claude/skills").exists()


def test_board_on_path_runs_the_installed_version(home):
    """The launcher reads the version from the manifest beside it."""
    install(home)
    manifest = (home / ".local/share/boardkit/plugin/.claude-plugin/plugin.json").read_text()
    version = manifest.split('"version"')[1].split('"')[1]
    cache = home / "cache"
    fake = cache / "boardkit" / version / "bin" / "board"
    fake.parent.mkdir(parents=True)
    fake.write_text("#!/usr/bin/env bash\necho fake board \"$@\"\n")
    fake.chmod(0o755)
    gh = cache / "boardkit" / "gh" / "bin" / "gh"
    gh.parent.mkdir(parents=True)
    gh.write_text("#!/usr/bin/env bash\n")
    gh.chmod(0o755)
    out = subprocess.run([str(home / ".local/bin/board"), "--version"],
                         env={**os.environ, "HOME": str(home), "XDG_CACHE_HOME": str(cache)},
                         capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "fake board --version"


def test_uninstall_removes_the_command_and_only_our_skills(home):
    (home / ".claude").mkdir()
    install(home)
    mine = home / ".codex/skills/other/SKILL.md"
    mine.parent.mkdir(parents=True)
    mine.write_text("a person's own skill\n")
    install(home, "--uninstall")
    assert not (home / ".local/bin/board").exists()
    assert not (home / ".local/share/boardkit").exists()
    assert not (home / ".codex/skills/board").exists()
    assert not (home / ".claude/skills/board").exists()
    assert mine.is_file()


def on_path(monkeypatch, *directories: pathlib.Path) -> None:
    monkeypatch.setenv("PATH", os.pathsep.join(map(str, directories)))


def executable(path: pathlib.Path) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


def test_doctor_is_clean_after_an_install_of_this_boardkit(home, monkeypatch):
    install(home)
    on_path(monkeypatch, home / ".local/bin")
    assert doctor.findings(None) == []


def test_doctor_names_skills_written_by_another_boardkit(home, monkeypatch):
    install(home)
    on_path(monkeypatch, home / ".local/bin")
    old = home / ".codex/skills/board/SKILL.md"
    old.write_text(old.read_text().replace("Reads are free", "Reads cost"))
    (problem,) = doctor.findings(None)
    assert str(old) in problem and "board self update" in problem


def test_doctor_names_every_board_on_path_and_which_wins(home, monkeypatch):
    install(home)
    uv = executable(home / ".local/share/uv/tools/boardkit/bin/board")
    on_path(monkeypatch, uv.parent, home / ".local/bin")
    (problem,) = doctor.findings(None)
    assert "2 `board` commands" in problem
    assert problem.index("uv tool install") < problem.index("install.sh")


def test_doctor_names_skill_copies_kept_in_a_repository(home, monkeypatch, tmp_path):
    (home / ".claude/plugins/cache/boardkit").mkdir(parents=True)
    on_path(monkeypatch, executable(home / ".claude/plugins/cache/boardkit/bin/board").parent)
    repo = tmp_path / "repo"
    copy = repo / ".claude/skills/board/SKILL.md"
    copy.parent.mkdir(parents=True)
    copy.write_text(skill.render("board"))
    (problem,) = doctor.findings(repo)
    assert str(copy) in problem and "Delete" in problem


def test_doctor_says_when_there_is_no_board_at_all(home, monkeypatch, tmp_path):
    on_path(monkeypatch, tmp_path / "empty")
    (problem,) = doctor.findings(None)
    assert "no `board` on PATH" in problem


def test_board_mentions_problems_once_a_day_on_stderr(home, monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("BOARDKIT_NO_DOCTOR")
    on_path(monkeypatch, tmp_path / "empty")
    doctor.nudge(None)
    assert "no `board` on PATH" in capsys.readouterr().err
    doctor.nudge(None)
    assert capsys.readouterr().err == ""


def test_the_install_script_is_valid_bash():
    subprocess.run(["bash", "-n", str(ROOT / "install.sh")], check=True)
    assert GENERATED in shipped("board")
