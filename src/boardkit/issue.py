"""Change an issue itself: comment, close, reopen, edit, labels, sub-issues.

`board set` changes what the board holds about an item. These change the
issue behind it, which is how the lifecycle records a result, drops rejected
work and refines a plan. They run under the repository's own credential, so
an agent working its repository never needs the person's `gh` login.

Each is recorded in the action queue first, then sent at once. One that
cannot be sent (offline, rate limited) stays queued and goes with the next
sync, exactly like a field write; one GitHub refuses is dropped with the
reason recorded. A send that lands pulls, so the next read shows it.
"""
from __future__ import annotations

from .config import Config
from .gh import BoardError, gh, quoted, run_json
from .store import Action, Store
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


def record(config: Config, store: Store, kind: str, ref: str, said: str, **args: str) -> int:
    """Queue one change to an issue, then try to send the queue now."""
    store.queue_action(kind, ref, **args)
    # Every issue the send may touch: earlier queued changes go with this one.
    touched = {action.ref for action in store.actions()}
    sent, why = send(config, store)
    if sent:
        print(said)
        from . import sync
        snapshot = store.state()
        ids = [item.id if item else "" for item in map(snapshot.known, touched)]
        # Only what was touched is re-read. An issue that is not on the board
        # has no item to re-read, so the board is pulled instead.
        if not all(ids) or not sync.refresh(config, store, ids):
            sync.pull(config, store)
    else:
        print(f"{said}: kept, {why}. It goes with the next sync, or `board sync push` now.")
    return 0


def send(config: Config, store: Store) -> tuple[int, str]:
    """Send every queued issue change, oldest first; stop at the first one
    the network stops. Returns how many landed, and why the rest waited."""
    from .gh import RateLimited, Unreachable
    landed = 0
    for action in store.actions():
        try:
            perform(config, store, action)
        except Unreachable:
            return landed, "offline"
        except RateLimited:
            return landed, "rate limited"
        except BoardError as refused:
            # GitHub said no for a reason waiting will not change: drop it,
            # with the reason on record, rather than retry it forever.
            note("action_refused", id=action.id, kind=action.kind, ref=action.ref,
                 said=str(refused)[:300])
            print(f"note: {action.kind} on #{action.ref} was refused: {refused}")
        else:
            landed += 1
            note(action.kind, issue=action.ref, **action.args)
        with store.held():
            store.save_actions([a for a in store.actions() if a.id != action.id])
    return landed, ""


def perform(config: Config, store: Store, action: Action) -> None:
    """Send one queued change to GitHub."""
    issue, args = action.ref, action.args
    repo = ["--repo", config.repo]
    if action.kind == "comment":
        gh("issue", "comment", issue, *repo, "--body", args["text"])
    elif action.kind == "close":
        gh("issue", "close", issue, *repo, "--reason", REASONS[args["reason"]])
    elif action.kind == "reopen":
        gh("issue", "reopen", issue, *repo)
    elif action.kind == "edit":
        extra = [x for key in ("title", "body") if key in args
                 for x in (f"--{key}", args[key])]
        gh("issue", "edit", issue, *repo, *extra)
    elif action.kind in ("labels", "assignees"):
        item = store.state().known(issue)
        have = set(item.labels if action.kind == "labels" else item.assignees) if item else set()
        want = set(names(args["value"]))
        flag = "label" if action.kind == "labels" else "assignee"
        changes = [*(x for n in sorted(want - have) for x in (f"--add-{flag}", n)),
                   *(x for n in sorted(have - want) for x in (f"--remove-{flag}", n))]
        if changes:
            gh("issue", "edit", issue, *repo, *changes)
    elif action.kind == "parent":
        item = store.state().known(issue)
        value = args["value"].lstrip("#")
        link(config, int(issue), int(value) if value else 0, item.parent if item else 0)
    else:
        raise BoardError(f"unknown queued change {action.kind!r}")


def comment(config: Config, store: Store, ref: str, text: str) -> int:
    """Record something on the issue: a result, a verification, a decision."""
    if not text.strip():
        raise BoardError("a comment needs text")
    issue = number(store, ref)
    return record(config, store, "comment", str(issue), f"Commented on #{issue}", text=text)


def close(config: Config, store: Store, ref: str, reason: str) -> int:
    """Close as completed, or as not planned for rejected, duplicate or
    abandoned work, which never goes through Done."""
    if reason not in REASONS:
        raise BoardError(f"close takes --completed or --not-planned, not {reason!r}")
    issue = number(store, ref)
    return record(config, store, "close", str(issue),
                  f"Closed #{issue} as {REASONS[reason]}", reason=reason)


def reopen(config: Config, store: Store, ref: str) -> int:
    issue = number(store, ref)
    return record(config, store, "reopen", str(issue), f"Reopened #{issue}")


def edit(config: Config, store: Store, ref: str, title: str | None, body: str | None) -> int:
    """Refine the plan: a new title, a new body, or both."""
    if title is None and body is None:
        raise BoardError("edit takes --title, --body, or both")
    issue = number(store, ref)
    args = {k: v for k, v in (("title", title), ("body", body)) if v is not None}
    return record(config, store, "edit", str(issue), f"Edited #{issue}", **args)


def names(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def set_meta(config: Config, store: Store, ref: str, field: str, value: str) -> int:
    """Labels and assignees become exactly the list given; parent makes this
    issue a sub-issue of another, and "" detaches it."""
    issue = number(store, ref)
    shown = value if field == "parent" else ", ".join(sorted(names(value)))
    return record(config, store, field, str(issue),
                  f"#{issue} {field} = {shown or '(none)'}", value=value)


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
