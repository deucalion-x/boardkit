"""Change an issue itself: comment, close, reopen, edit, labels, sub-issues.

`board set` changes what the board holds about an item. These change the
issue behind it, which is how the lifecycle records a result, drops rejected
work and refines a plan. They run under the repository's own credential, so
an agent working its repository never needs the person's `gh` login.

Like creating an issue, they need the network: GitHub closes, comments and
edits, and there is no local half worth queueing. Each pulls afterwards, so
the next read answers with what it did.
"""
from __future__ import annotations

from .config import Config
from .gh import BoardError, gh, quoted, run_json
from .store import Store
from .trail import note

# What `gh issue close --reason` takes, under the names the lifecycle uses.
REASONS = {"completed": "completed", "not-planned": "not planned"}
# Issue metadata that `board set` routes here rather than to a board field.
META = ("labels", "assignees", "parent")


def number(store: Store, ref: str) -> int:
    """The issue behind `ref`, open or closed. A draft card has none."""
    item = store.state().known(ref.lstrip("#"))
    if item is None:
        raise BoardError(f"#{ref.lstrip('#')} is not on the board")
    if not item.number:
        raise BoardError(f"{item.ref} is a draft card, with no issue behind it. "
                         f"Run `board promote {item.ref}` first.")
    return item.number


def done(config: Config, store: Store, what: str, issue: int, **detail) -> int:
    from . import sync
    note(what, issue=issue, **detail)
    sync.pull(config, store)
    return 0


def comment(config: Config, store: Store, ref: str, text: str) -> int:
    """Record something on the issue: a result, a verification, a decision."""
    if not text.strip():
        raise BoardError("a comment needs text")
    issue = number(store, ref)
    gh("issue", "comment", str(issue), "--repo", config.repo, "--body", text)
    print(f"Commented on #{issue}")
    return done(config, store, "commented", issue)


def close(config: Config, store: Store, ref: str, reason: str) -> int:
    """Close as completed, or as not planned for rejected, duplicate or
    abandoned work, which never goes through Done."""
    if reason not in REASONS:
        raise BoardError(f"close takes --completed or --not-planned, not {reason!r}")
    issue = number(store, ref)
    gh("issue", "close", str(issue), "--repo", config.repo, "--reason", REASONS[reason])
    print(f"Closed #{issue} as {REASONS[reason]}")
    return done(config, store, "closed", issue, reason=REASONS[reason])


def reopen(config: Config, store: Store, ref: str) -> int:
    issue = number(store, ref)
    gh("issue", "reopen", str(issue), "--repo", config.repo)
    print(f"Reopened #{issue}")
    return done(config, store, "reopened", issue)


def edit(config: Config, store: Store, ref: str, title: str | None, body: str | None) -> int:
    """Refine the plan: a new title, a new body, or both."""
    if title is None and body is None:
        raise BoardError("edit takes --title, --body, or both")
    issue = number(store, ref)
    args = ["issue", "edit", str(issue), "--repo", config.repo]
    if title is not None:
        args += ["--title", title]
    if body is not None:
        args += ["--body", body]
    gh(*args)
    print(f"Edited #{issue}")
    return done(config, store, "edited", issue,
                title=title is not None, body=body is not None)


def names(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def set_meta(config: Config, store: Store, ref: str, field: str, value: str) -> int:
    """Labels and assignees become exactly the list given; parent makes this
    issue a sub-issue of another, and "" detaches it."""
    issue = number(store, ref)
    item = store.state().known(str(issue))
    assert item is not None
    if field == "parent":
        link(config, issue, int(value.lstrip("#")) if value.strip() else 0, item.parent)
        print(f"#{issue} parent = {value or '(none)'}")
        return done(config, store, "parented", issue, parent=value)
    have = set(item.labels if field == "labels" else item.assignees)
    want = set(names(value))
    flag = "label" if field == "labels" else "assignee"
    args = ["issue", "edit", str(issue), "--repo", config.repo]
    for name in sorted(want - have):
        args += [f"--add-{flag}", name]
    for name in sorted(have - want):
        args += [f"--remove-{flag}", name]
    if len(args) > 5:
        gh(*args)
    print(f"#{issue} {field} = {', '.join(sorted(want)) or '(none)'}")
    return done(config, store, f"set_{field}", issue, value=sorted(want))


def graphql(query: str) -> dict:
    return run_json(["gh", "api", "graphql", "-f", f"query={query}"])


def node_id(config: Config, issue: int) -> str:
    owner, name = config.repo.split("/", 1)
    payload = graphql(f"{{ repository(owner: {quoted(owner)}, name: {quoted(name)}) "
                      f"{{ issue(number: {int(issue)}) {{ id }} }} }}")
    found = ((payload.get("data") or {}).get("repository") or {}).get("issue")
    if not found:
        raise BoardError(f"#{issue} is not an issue in {config.repo}")
    return found["id"]


def link(config: Config, issue: int, parent: int, previous: int) -> None:
    """Sub-issue links, through GitHub's own mutations."""
    child = quoted(node_id(config, issue))
    if previous and previous != parent:
        graphql(f"mutation {{ removeSubIssue(input: {{issueId: "
                f"{quoted(node_id(config, previous))}, subIssueId: {child}}}) "
                f"{{ issue {{ id }} }} }}")
    if parent and parent != previous:
        graphql(f"mutation {{ addSubIssue(input: {{issueId: "
                f"{quoted(node_id(config, parent))}, subIssueId: {child}}}) "
                f"{{ issue {{ id }} }} }}")
