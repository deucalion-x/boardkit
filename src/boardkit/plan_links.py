"""Point issue bodies at documents by commit sha, and verify they resolve.

An issue outlives any branch, so a link from an issue into a branch rots when
the branch is deleted. A commit sha does not: once pushed it resolves forever.
The rule that keeps this true is that the branch is pushed before the issue is
filed, and that merges are not squashed — a squash rewrites the sha away.
"""
from __future__ import annotations

import re

from . import issue_mirror
from .config import Config
from .gh import gh, git, git_ok


def patterns(config: Config) -> tuple[re.Pattern, re.Pattern, re.Pattern, str]:
    blob = f"https://github.com/{config.repo}/blob"
    prefix = re.escape(config.docs_prefix)
    # A `docs/...md` path, optionally with an #anchor, in backticks and not
    # already part of a link — neither its target `(`…`)` nor its text `[`…`]`,
    # which nests if missed.
    bare = re.compile(rf"(?<![(\[])`({prefix}[^`\s]+\.md)(#[^`\s]*)?`(?!\]?\()")
    linked = re.compile(rf"{re.escape(blob)}/([0-9a-f]{{7,40}})/(\S+?)(?=[)\s]|$)")
    # A blob link pinned to anything but a sha — `main`, a branch name — resolves
    # today and rots the moment the ref moves or the branch is deleted. That is
    # the failure this module exists to prevent, so it is an error, not a warning.
    unpinned = re.compile(
        rf"{re.escape(blob)}/(?![0-9a-f]{{7,40}}/)([^/\s)]+)/(\S+?)(?=[)\s]|$)")
    return bare, linked, unpinned, blob


def issues(config: Config) -> list[dict]:
    return issue_mirror.open_issues(config, "number,body")


def sha_for(path: str, config: Config) -> str | None:
    """Newest pushed commit in which `path` exists."""
    root = str(config.root)
    for sha in git("-C", root, "rev-list", "@{push}", "--", path).split():
        if git_ok("-C", root, "cat-file", "-e", f"{sha}:{path}"):
            return sha
        parent = git("-C", root, "rev-parse", f"{sha}^")
        if git_ok("-C", root, "cat-file", "-e", f"{parent}:{path}"):
            return parent
    return None


def rewrite(config: Config, dry: bool) -> int:
    bare, _, _, blob = patterns(config)
    changed = 0
    for issue in issues(config):
        body, number = issue["body"] or "", issue["number"]
        misses: list[str] = []

        def replace(match: re.Match[str]) -> str:
            path, anchor = match.group(1), match.group(2) or ""
            sha = sha_for(path, config)
            if sha is None:
                # Late binding is safe: `replace` is consumed by the
                # `sub` below, inside this same iteration.
                misses.append(path)  # noqa: B023
                return match.group(0)
            return f"[`{path}{anchor}`]({blob}/{sha}/{path}{anchor})"

        new = bare.sub(replace, body)
        for path in misses:
            print(f"  #{number}: no pushed commit contains {path}")
        if new == body:
            continue
        changed += 1
        print(f"#{number}: {len(bare.findall(body)) - len(misses)} link(s)")
        if not dry:
            gh("issue", "edit", str(number), "--repo", config.repo, "--body", new)
    print(f"{changed} issue(s) {'would be ' if dry else ''}updated")
    if changed and not dry and config.mirror_path:
        # The mirror travels with the edit, so both land in one commit.
        issue_mirror.write(config)
    return 0


def check(config: Config) -> int:
    _, linked, unpinned, _ = patterns(config)
    root = str(config.root)
    errors: list[str] = []
    for issue in issues(config):
        body = issue["body"] or ""
        for sha, target in linked.findall(body):
            path = target.split("#", 1)[0]
            if not git_ok("-C", root, "cat-file", "-e", f"{sha}:{path}"):
                errors.append(f"#{issue['number']}: {path} missing at {sha[:8]}")
        for ref, target in unpinned.findall(body):
            path = target.split("#", 1)[0]
            errors.append(f"#{issue['number']}: {path} pinned to '{ref}', not a sha")
    for error in errors:
        print(f"- {error}")
    print(f"{'Plan links failed' if errors else 'Plan links resolve'}: {len(errors)} broken")
    return 1 if errors else 0
