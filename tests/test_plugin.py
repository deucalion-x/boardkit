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
    """`bin/board` installs the tag the manifest names, and Claude Code only
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
    board = PLUGIN / "bin" / "board"
    assert os.access(board, os.X_OK)
    subprocess.run(["bash", "-n", str(board)], check=True)
