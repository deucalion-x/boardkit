"""The Claude Code plugin: the skills and the `board` that runs them, at one version."""
import json
import os
import pathlib
import re
import subprocess

from boardkit import skill

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugin"


def manifest() -> dict:
    return json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())


def test_the_plugin_is_the_latest_release():
    """`scripts/board` installs the tag the manifest names, and Claude Code only
    updates a plugin when that string changes: so a release bumps it."""
    released = re.search(r"^## (\d+\.\d+\.\d+) ", (ROOT / "CHANGELOG.md").read_text(),
                         re.MULTILINE)
    assert released and manifest()["version"] == released[1]


def test_the_marketplace_points_at_the_plugin():
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    (entry,) = market["plugins"]
    assert entry["name"] == manifest()["name"]
    assert (ROOT / entry["source"] / ".claude-plugin" / "plugin.json").is_file()


def test_the_plugin_carries_the_skills_as_generated():
    for name in skill.SKILLS:
        shipped = PLUGIN / "skills" / name / "SKILL.md"
        assert shipped.read_text(encoding="utf-8") == skill.render(name)


def test_board_is_an_executable_shell_script():
    board = PLUGIN / "scripts" / "board"
    assert os.access(board, os.X_OK)
    subprocess.run(["bash", "-n", str(board)], check=True)


def test_the_plugin_has_no_top_level_bin():
    """claude.ai refuses a plugin with one, on organisation sync and upload:
    a `bin/` is put on PATH without showing on the admin approval surface."""
    assert not (PLUGIN / "bin").exists()


def test_the_session_hook_puts_board_on_path(tmp_path):
    """The hook writes the resolved path: CLAUDE_PLUGIN_ROOT is set for hooks,
    not for the Bash commands that read the env file."""
    (hook,) = manifest_hooks()
    root = tmp_path / "plugin root"   # a space, as a home directory may have
    board = root / "scripts" / "board"
    board.parent.mkdir(parents=True)
    # A stand-in: the real launcher would fetch boardkit on first use.
    board.write_text("#!/bin/sh\n[ \"$1 $2\" = 'auth agent-env' ] && echo 'export BOARDKIT_AGENT=1'\n")
    board.chmod(0o755)
    env_file = tmp_path / "env"
    env = {**os.environ, "CLAUDE_ENV_FILE": str(env_file), "CLAUDE_PLUGIN_ROOT": str(root)}
    subprocess.run(["bash", "-c", hook], env=env, check=True)
    script = f"source '{env_file}'; command -v board; echo $BOARDKIT_AGENT"
    found = subprocess.run(["bash", "-c", script],
                           env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True,
                           check=True)
    assert found.stdout.split("\n")[:2] == [str(board), "1"]


def manifest_hooks() -> list[str]:
    hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text())["hooks"]
    return [h["command"] for group in hooks["SessionStart"] for h in group["hooks"]]
