"""Mirror open issue text into git so its changes show up in a diff.

The mirror is never a source. Nothing should read it to learn current state —
that is the board's job. It exists so that a change to an issue's text, and
especially to its acceptance criteria, arrives as a reviewable diff instead of
silently.

It is regenerated, never maintained. Any tool that edits issues refreshes it in
the same commit, so the only way to drift is an edit made in the GitHub web UI
— which `check` then reports.
"""
from __future__ import annotations

from .config import Config
from .gh import gh_json

BANNER = (
    "DO NOT READ THIS FILE TO LEARN CURRENT STATE. It is a mirror of the "
    "GitHub issues, kept in git only so that changes to issue text appear in "
    "a diff. Ask the board instead: `board`. Regenerate with `board mirror write`."
)


def render(config: Config) -> str:
    issues = gh_json("issue", "list", "--repo", config.repo, "--state", "open",
                     "--limit", str(config.item_limit), "--json", "number,title,body,labels")
    issues.sort(key=lambda issue: issue["number"])
    # Markdown, one line per line of body, so a reworded sentence shows up as a
    # one-line diff instead of one enormous escaped string.
    parts = ["# Issue mirror", "", "Role: generated mirror", "", BANNER, ""]
    for issue in issues:
        labels = ", ".join(sorted(label["name"] for label in issue["labels"])) or "none"
        parts += [f"## #{issue['number']} {issue['title']}", "", f"Labels: {labels}", ""]
        parts += (issue["body"] or "").replace("\r\n", "\n").rstrip().split("\n")
        parts += ["", "---", ""]
    return "\n".join(parts).rstrip() + "\n"


def write(config: Config) -> int:
    mirror = config.path(config.require("mirror_path"))
    mirror.parent.mkdir(parents=True, exist_ok=True)
    mirror.write_text(render(config), encoding="utf-8")
    count = mirror.read_text(encoding="utf-8").count("\n## #")
    print(f"Mirrored {count} open issues to {config.mirror_path}")
    return 0


def check(config: Config) -> int:
    mirror = config.path(config.require("mirror_path"))
    if not mirror.exists():
        print(f"{config.mirror_path} is missing; run `board mirror write`")
        return 1
    if mirror.read_text(encoding="utf-8") == render(config):
        print("Issue mirror matches GitHub")
        return 0
    print(f"{config.mirror_path} is stale; run `board mirror write` and review the diff")
    return 1
