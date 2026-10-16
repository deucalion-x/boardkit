"""Release boardkit: one command for the mechanical steps of docs/releasing.md.

    uv run python scripts/release.py 0.3.24          # release it
    uv run python scripts/release.py 0.3.24 --dry-run

What stays a person's (or an agent's) decision is written before running it:
the changelog entries under `Unreleased`, and the row for this version in
docs/releasing.md, which records whether the live contract check ran and what
it covered. The script refuses to start without both.

It then moves the changelog heading, sets the plugin's version, runs the CI
checks, commits and pushes, waits for CI on that commit, and only then tags.
Tagging last matters: the plugin's `bin/board` installs the tag its version
names, so a plugin that updated before the tag existed could not install.

Every `gh` call runs under whatever `GH_TOKEN` the caller set.
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import re
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"
RELEASING = ROOT / "docs" / "releasing.md"
PLUGIN = ROOT / "plugin" / ".claude-plugin" / "plugin.json"
REPO = "deucalion-x/boardkit"
VERSION = re.compile(r"\d+\.\d+\.\d+")
# The release commit may carry these two edits uncommitted: they are what the
# person writes for this release. Anything else dirty is refused.
WRITTEN_FOR_THE_RELEASE = ("CHANGELOG.md", "docs/releasing.md")
CHECKS = (["uv", "sync", "--locked", "--dev"],
          ["uv", "run", "ruff", "check", "src", "tests", "scripts"],
          ["uv", "run", "mypy"],
          ["uv", "run", "pytest", "-q"])


class Refused(RuntimeError):
    """A precondition that does not hold. Printed without a traceback."""


def run(*args: str, capture: bool = True) -> str:
    done = subprocess.run(args, cwd=ROOT, text=True, check=True,
                          stdout=subprocess.PIPE if capture else None)
    # Trailing only: `git status --porcelain` starts with a meaningful space.
    return (done.stdout or "").rstrip()


def key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def with_heading(changelog: str, version: str, day: str) -> str:
    """`Unreleased` emptied into a heading for `version`. Refused when there
    is nothing under `Unreleased` to release."""
    head, marker, rest = changelog.partition("## Unreleased\n")
    if not marker:
        raise Refused("CHANGELOG.md has no `## Unreleased` heading")
    pending, _, _ = rest.partition("\n## ")
    if not pending.strip():
        raise Refused("nothing under `## Unreleased` in CHANGELOG.md to release")
    return f"{head}## Unreleased\n\n## {version} — {day}\n{rest}"


def with_version(manifest: str, version: str) -> str:
    data = json.loads(manifest)
    data["version"] = version
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def recorded(releasing: str, version: str) -> bool:
    """Whether docs/releasing.md has this version's contract-check row."""
    return re.search(rf"^\| {re.escape(version)} \|", releasing, re.MULTILINE) is not None


def preconditions(version: str) -> None:
    if not VERSION.fullmatch(version):
        raise Refused(f"{version!r} is not a version like 0.3.24")
    if run("git", "branch", "--show-current") != "main":
        raise Refused("release from main")
    run("git", "fetch", "--quiet", "--tags", "origin")
    if run("git", "rev-parse", "HEAD") != run("git", "rev-parse", "origin/main"):
        raise Refused("main is not origin/main; pull or push first")
    tags = [t.removeprefix("v") for t in run("git", "tag", "--list", "v*").split()
            if VERSION.fullmatch(t.removeprefix("v"))]
    latest = max(tags, key=key, default="0.0.0")
    if key(version) <= key(latest):
        raise Refused(f"{version} is not after the latest tag, v{latest}")
    dirty = [line[3:] for line in run("git", "status", "--porcelain").splitlines()]
    stray = [path for path in dirty if path not in WRITTEN_FOR_THE_RELEASE]
    if stray:
        raise Refused(f"uncommitted changes beyond the release notes: {', '.join(stray)}")
    if not recorded(RELEASING.read_text(encoding="utf-8"), version):
        raise Refused(
            f"docs/releasing.md has no `| {version} |` row. Write it first: whether "
            f"the live contract check ran (needed when a GitHub call changed) and "
            f"what it covered, or why it was skipped.")


def wait_for_ci(sha: str) -> None:
    """Every workflow run on the release commit, to the end, green."""
    for _ in range(60):
        listed = json.loads(run("gh", "run", "list", "-R", REPO, "--commit", sha,
                                "--json", "databaseId"))
        if listed:
            break
        time.sleep(5)
    else:
        raise Refused(f"no CI run appeared for {sha[:7]}; nothing was tagged")
    for entry in listed:
        try:
            run("gh", "run", "watch", str(entry["databaseId"]), "-R", REPO,
                "--exit-status", capture=False)
        except subprocess.CalledProcessError:
            raise Refused(f"CI failed on {sha[:7]}; nothing was tagged. Fix it and "
                          f"tag by hand once green: git tag v… && git push origin v…") from None


def release(version: str, dry: bool) -> None:
    preconditions(version)
    day = datetime.datetime.now().astimezone().date().isoformat()
    changelog = with_heading(CHANGELOG.read_text(encoding="utf-8"), version, day)
    manifest = with_version(PLUGIN.read_text(encoding="utf-8"), version)
    if dry:
        print(f"Would release {version}: changelog heading, plugin version, checks, "
              f"commit, push, wait for CI, tag v{version}.")
        return
    CHANGELOG.write_text(changelog, encoding="utf-8")
    PLUGIN.write_text(manifest, encoding="utf-8")
    for check in CHECKS:
        print(f"$ {' '.join(check)}", flush=True)
        run(*check, capture=False)
    run("git", "add", "CHANGELOG.md", "docs/releasing.md", str(PLUGIN.relative_to(ROOT)))
    run("git", "commit", "--quiet", "-m", f"release: {version}")
    run("git", "push", "--quiet", "origin", "main", capture=False)
    sha = run("git", "rev-parse", "HEAD")
    print(f"Pushed {sha[:7]}; waiting for CI.", flush=True)
    wait_for_ci(sha)
    run("git", "tag", f"v{version}")
    run("git", "push", "--quiet", "origin", f"v{version}", capture=False)
    print(f"\nReleased v{version}. The plugin updates on its next start; for the "
          f"command alone:\n  uv tool install --force "
          f"\"git+https://github.com/{REPO}@v{version}\"")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("version")
    parser.add_argument("--dry-run", action="store_true",
                        help="check everything, change nothing")
    args = parser.parse_args()
    try:
        release(args.version, args.dry_run)
    except Refused as refused:
        print(f"refused: {refused}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
