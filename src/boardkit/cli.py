"""One entry point, so every verb shares the same configuration lookup.

Reads answer from the local store; only the sync verbs touch GitHub.

  board                      every open item, by status
  board item <n>             one item with its text and children
  board find <terms>         search titles, bodies and labels in the snapshot
  board json                 normalized items on stdout, for jq or python
  board views                the saved tabs the board has

  board add <title> [f=v]    create an issue and put it on the board
                             --body <text> for its text
                             --draft for a board card with no issue behind it
  board set <n> <field> <v>  change a field; pushed now, queued if it cannot be
                             --later: queue without trying
  board rejected [--clear]   local changes that lost to a remote change
  board log [n]              what boardkit did, for audit and debugging
  board sync [push|status]   pull, drain the queue, or report
  --offline                  on any read: answer from the snapshot as it is
  board auth [--refresh]     what this gh login is allowed to do
  board init [-n] [title]    write the three settings a repo must decide
  board schema               the configuration's shape, as JSON schema
  board admin apply [-n] [--reconcile]
                             add missing shape; optionally update view filters
  board admin link           attach the project to the repository

  board mirror write|check   the issue-text mirror in git
  board links check|rewrite  sha-pinned document links in issue bodies
  board install-skill        write the agent skills for Claude Code and Codex
"""
from __future__ import annotations

import json
import sys

from . import admin, auth, create, issue_mirror, plan_links, setup, skill, sync, trail
from . import board as board_module
from .config import load
from .gh import BoardError, RateLimited, Unreachable
from .store import Snapshot, Store, Write

BOARD_COMMANDS = {"list", "item", "find", "json", "views"}
STALE = "No local snapshot. Run `board sync` first."


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    command = argv[0] if argv else "list"
    try:
        return dispatch(command, argv[1:])
    except BoardError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def dispatch(command: str, args: list[str]) -> int:
    # Help needs no configuration, so it works outside a configured repo.
    if command in {"-h", "--help", "help"}:
        return usage(0)

    # These two run before configuration exists, which is their whole point.
    if command == "init":
        return setup.init(" ".join(a for a in args if a != "-n"), dry="-n" in args)
    if command == "schema":
        return setup.schema()

    config = load()
    store = Store(config)
    # Named once per command, because three files can hold this configuration
    # and the costly confusion is editing one that is not the one being read.
    trail.note("configured", file=str(config.source), command=command,
               project=config.name, declares=config.declares)

    # Verbs that never read the board are handled before it is loaded.
    if command == "install-skill":
        return skill.install(config, check="--check" in args)
    if command == "admin":
        verb = args[0] if args else "apply"
        if verb == "apply":
            return admin.apply(config, dry="-n" in args,
                               reconcile="--reconcile" in args)
        if verb == "link":
            return admin.link(config)
        return usage()
    if command == "auth":
        return auth.report(config, refresh="--refresh" in args)
    if command == "sync":
        verbs = [a for a in args if not a.startswith("-")]
        return run_sync(config, store, verbs[0] if verbs else "pull")
    if command == "add":
        title, body, values, draft = create.parse(args)
        if draft:
            return create.add_draft(config, store, title, body, values)
        return create.add(config, store, title, body, values)
    if command == "set":
        return queue(config, store, args)
    if command == "log":
        return history(store, int(args[0]) if args and args[0].isdigit() else 30)
    if command == "rejected":
        return rejected(store, clear="--clear" in args)
    if command == "mirror":
        verb = args[0] if args else "check"
        if verb == "write":
            return issue_mirror.write(config)
        if verb == "check":
            return issue_mirror.check(config)
        return usage()
    if command == "links":
        verb = args[0] if args else "check"
        if verb == "check":
            return plan_links.check(config)
        if verb == "rewrite":
            return plan_links.rewrite(config, dry="-n" in args)
        return usage()
    if command not in BOARD_COMMANDS:
        return usage()
    if command == "item" and not [a for a in args if a != "--offline"]:
        return usage()

    snapshot = freshen(config, store, offline="--offline" in args)
    # Stripped after the freshness decision so `find` does not search for it
    # and `item` does not take it for a reference.
    args = [a for a in args if a != "--offline"]
    if snapshot.empty:
        print(STALE)
        return 1
    items = list(snapshot.items)
    index = {item.ref: item for item in items}
    # How to read this board: config where it asserts, the snapshot otherwise.
    view = board_module.shape(config, snapshot.fields)
    if command == "views":
        for name in snapshot.views:
            print(name)
        if not snapshot.views:
            print("No views; `board sync` has not seen any.")
    elif command == "list":
        board_module.show(items, view)
        report_queue(store)
    elif command == "item":
        ref = args[0].lstrip("#")
        board_module.show([index[ref]] if ref in index else [], view)
        found = index.get(ref)
        if found is not None:
            about = [f"labels: {', '.join(found.labels)}" if found.labels else "",
                     f"assignees: {', '.join(found.assignees)}" if found.assignees else "",
                     f"milestone: {found.milestone}" if found.milestone else ""]
            said = "  ".join(part for part in about if part)
            if said:
                print(f"  {said}")
        if found is not None and not found.body:
            # Silence read as "the skill lied about showing the full text".
            print("  (no description)")
        if found is not None and found.body:
            print()
            print(found.body.rstrip())
            print()
        # Local, like the rest of the answer: each item carries its parent, so
        # the children are a pass over the snapshot. This was a search per
        # invocation — the one read that always spent quota, and the one that
        # could fail for a reason the question had nothing to do with.
        kids = board_module.children(found.number, items) if found else []
        if kids:
            print("  children:")
            board_module.show(kids, view)
    elif command == "find":
        # The snapshot carries titles, bodies and labels, so this costs
        # nothing. `gh issue list --search` is still what answers a question
        # about closed work or history.
        board_module.show(board_module.matching(items, " ".join(args)), view)
    elif command == "json":
        json.dump([item.flat() for item in items], sys.stdout, indent=2)
        print()
    return 0


def freshen(config, store: Store, offline: bool = False) -> Snapshot:
    """The snapshot a read may answer from, pulled first if it is too old.

    Being told to sync does not work: the agent has no way to notice that the
    instruction applies, and a stale answer looks exactly like a current one.
    So the read spends the pull itself, and the skill can promise an age
    instead of asking anyone to remember.

    Nothing is supervised and nothing polls. A pull that cannot reach GitHub
    leaves the snapshot alone and says so, because answering from a known-old
    copy beats failing the question.

    The window elapsing is also the only regular tick there is, so a queue
    left behind by an offline write is retried here before the pull.
    """
    snapshot = store.state()
    floor = config.max_read_age_seconds
    # No snapshot is not staleness. A first read says to sync rather than
    # fetching one behind the caller's back, so that setting a repo up is
    # something they did and not something that happened to them.
    if snapshot.empty or offline or not floor or snapshot.age <= floor:
        return showing(store, snapshot)
    try:
        auth.require(config, "read")
        drain(config, store)
        return showing(store, sync.pull(config, store))
    except (BoardError, RateLimited, Unreachable) as why:
        print(f"note: answering from a snapshot {int(snapshot.age)}s old; "
              f"could not refresh it ({why})", file=sys.stderr)
        return showing(store, snapshot)


def showing(store: Store, snapshot: Snapshot) -> Snapshot:
    """The snapshot as a read should see it, with queued writes folded in.

    A write that has not landed is still a decision this repository has made.
    Leaving it out of reads means an agent sets a field, reads it back and is
    told the old value — worst offline, which is exactly when the queue is
    doing the job it exists for. The fold is the same operation a landed push
    performs, applied to the copy a read answers from.

    Not saved, on purpose. The file on disk goes on meaning "what GitHub last
    held", which is what keeps the conflict check honest and what lets a pull
    replace it without consulting anyone.

    Said out loud rather than shown silently: a value that is not on the board
    yet must not be indistinguishable from one that is.
    """
    pending = store.pending()
    if not pending:
        return snapshot
    print(f"note: {len(pending)} queued change(s) included below and not yet "
          f"on the board.", file=sys.stderr)
    return sync.patched(snapshot, pending)


def drain(config, store: Store) -> None:
    """Retry whatever the queue is still holding, before a refresh lands.

    A write that could not reach GitHub when it was made waited for someone
    to run `board sync push` — which, in an agent's session, means waiting for
    a person who is not there. The refresh is the retry: the window elapsing
    is the only regular tick boardkit has, it is already spending a call, and
    nothing has to be supervised for it to come around.

    Before the pull rather than after, because the pull replaces the snapshot
    these writes were decided against. Pushing first means a landed change is
    in what the pull returns; pushing after would compare them to values that
    had already moved.

    Best effort throughout. Still being offline is the ordinary case, and a
    read that asked for a refresh must not fail over the queue it inherited.
    """
    if not store.pending():
        return
    try:
        auth.require(config, "write")
    except BoardError:
        return  # A read-only token keeps its reads. The queue keeps waiting.
    try:
        pushed, lost, _why = sync.push_now(config, store)
    except BoardError:
        return
    # On stderr with the staleness note, and for the same reason: stdout is
    # the answer to the question that was asked, and this is not it.
    if pushed:
        print(f"note: pushed {pushed} queued change(s).", file=sys.stderr)
    if lost:
        print(f"note: {lost} queued change(s) rejected. "
              f"`board rejected` to see them.", file=sys.stderr)


def run_sync(config, store: Store, verb: str) -> int:
    if verb == "pull":
        # Gated before the call, so a missing scope reads as advice rather
        # than as whatever `gh` happens to print.
        auth.require(config, "read")
        drain(config, store)
        snapshot = sync.pull(config, store)
        print(f"Pulled {len(snapshot.items)} items and {len(snapshot.fields)} fields")
        return report_queue(store)
    if verb == "push":
        auth.require(config, "write")
        pushed, lost = sync.push(config, store)
        print(f"Pushed {pushed} change(s); {lost} rejected")
        return 1 if lost else 0
    if verb == "status":
        snapshot = store.state()
        when = snapshot.synced_at or "never"
        print(f"Snapshot: {len(snapshot.items)} items, synced {when}")
        floor = config.max_read_age_seconds
        if not floor:
            print("Reads answer from it whatever its age "
                  "(max_read_age_seconds = 0)")
        elif not snapshot.empty:
            print(f"{int(snapshot.age)}s old; a read refreshes it past {floor}s")
        return report_queue(store)
    return usage()


def queue(config, store: Store, args: list[str]) -> int:
    words = [a for a in args if a != "--later"]
    if len(words) < 3:
        return usage()
    ref, field, value = words[0].lstrip("#"), words[1], " ".join(words[2:])
    snapshot = store.state()
    if snapshot.empty:
        print(STALE)
        return 1
    if snapshot.item(ref) is None:
        print(f"#{ref} is not on the board")
        return 1
    # Before recording, not after: a write refused here must never enter the
    # queue, where nothing would ever get it out again.
    sync.check(Write(ref=ref, field=field, value=value), snapshot)
    # After `check`, not before: a write that is invalid anyway should fail on
    # its value rather than spend the rejection that was guarding the field.
    sync.contested(Write(ref=ref, field=field, value=value), store)
    write = store.queue(ref, field, value)
    was = write.was or "unset"
    if "--later" in args:
        print(f"Queued #{ref} {field} = {value} (was {was}).")
        return report_queue(store)

    _pushed, lost, why = sync.push_now(config, store)
    if why:
        # No timer and no hook outliving the command: the next refresh past
        # the read-age window retries this (see `drain`), and `board sync
        # push` is what asking for it sooner looks like.
        print(f"#{ref} {field} = {value} (was {was}) — kept, {why}. "
              f"Retried on the next sync, or `board sync push` now.")
        return 0
    if lost:
        return rejected(store, clear=False)
    print(f"#{ref} {field} = {value} (was {was})")
    return 0


def rejected(store: Store, clear: bool) -> int:
    if clear:
        print(f"Cleared {store.clear_rejected()} rejection(s)")
        return 0
    rejections = store.rejected()
    if not rejections:
        print("No rejected changes.")
        return 0
    for rejection in rejections:
        write = rejection.write
        holds = f"GitHub holds {rejection.remote!r}; " if rejection.remote else ""
        print(f"#{write.ref} {write.field}: wanted {write.value!r} — "
              f"{holds}{rejection.reason}")
    print(f"\n{len(rejections)} rejected. Re-decide, then `board set` again; "
          f"`board rejected --clear` to drop them.")
    return 1


def history(store: Store, limit: int) -> int:
    entries = store.history(limit)
    if not entries:
        print("Nothing recorded yet.")
        return 0
    for entry in entries:
        detail = "  ".join(f"{key}={value}" for key, value in entry.items()
                           if key not in {"at", "action"})
        print(f'{entry["at"]}  {entry["action"]:18} {detail}')
    return 0


def report_queue(store: Store) -> int:
    waiting, lost = len(store.pending()), len(store.rejected())
    if waiting:
        print(f"{waiting} change(s) queued. `board sync push` to apply.")
    if lost:
        print(f"{lost} change(s) rejected. `board rejected` to see them.")
    return 0


def usage(code: int = 2) -> int:
    print(__doc__)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
