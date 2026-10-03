"""One entry point, so every verb shares the same configuration lookup.

Reads answer from the local store; only the sync verbs touch GitHub.

  board                      rollup by grouping field x status
  board ready                startable items only
  board group <name>         one group, by status            (alias: ws)
  board item <n>             one item with its text and children
  board find <terms>         full-text search on GitHub, joined to the board
  board json                 normalized items on stdout, for jq or python

  board set <n> <field> <v>  queue a local change
  board rejected [--clear]   local changes that lost to a remote change
  board log [n]              what boardkit did, for audit and debugging
  board sync [push|status]   pull, drain the queue, or report
  board auth [--refresh]     what this gh login is allowed to do
  board init [-n] [title]    write the three settings a repo must decide
  board schema               the configuration's shape, as JSON schema
  board admin apply [-n]     make the board match what this repo declares
  board admin link           attach the project to the repository

  board mirror write|check   the issue-text mirror in git
  board links check|rewrite  sha-pinned document links in issue bodies
  board install-skill        write the agent skill into .claude/skills/
"""
from __future__ import annotations

import json
import sys

from . import board as board_module
from . import admin, auth, issue_mirror, plan_links, setup, skill, sync
from .config import load
from .gh import BoardError
from .store import Store

BOARD_COMMANDS = {"rollup", "ready", "group", "ws", "item", "find", "json"}
STALE = "No local snapshot. Run `board sync` first."


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    command = argv[0] if argv else "rollup"
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

    # Verbs that never read the board are handled before it is loaded.
    if command == "install-skill":
        return skill.install(config, check="--check" in args)
    if command == "admin":
        verb = args[0] if args else "apply"
        if verb == "apply":
            return admin.apply(config, dry="-n" in args)
        if verb == "link":
            return admin.link(config)
        return usage()
    if command == "auth":
        return auth.report(config, refresh="--refresh" in args)
    if command == "sync":
        verbs = [a for a in args if not a.startswith("-")]
        return run_sync(config, store, verbs[0] if verbs else "pull")
    if command == "set":
        return queue(store, args)
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
    if command == "item" and not (args and args[0].lstrip("#").isdigit()):
        return usage()

    snapshot = store.state()
    if snapshot.empty:
        print(STALE)
        return 1
    items = list(snapshot.items)
    index = {item.number: item for item in items}
    # How to read this board: config where it asserts, the snapshot otherwise.
    view = board_module.shape(config, snapshot.fields)

    if command == "rollup":
        board_module.rollup(items, view)
        report_queue(store)
    elif command == "ready":
        startable = view.columns[0] if view.columns else "Todo"
        board_module.show([i for i in items
                           if i.field(view.status_field) == startable], view)
    elif command in {"group", "ws"}:
        name = " ".join(args).lower()
        board_module.show([i for i in items
                           if name in i.field(view.grouping).lower()], view)
    elif command == "item":
        number = int(args[0].lstrip("#"))
        board_module.show([index[number]] if number in index else [], view)
        found = index.get(number)
        if found is not None:
            about = [f"labels: {', '.join(found.labels)}" if found.labels else "",
                     f"assignees: {', '.join(found.assignees)}" if found.assignees else "",
                     f"milestone: {found.milestone}" if found.milestone else ""]
            said = "  ".join(part for part in about if part)
            if said:
                print(f"  {said}")
        if found is not None and found.body:
            print()
            print(found.body.rstrip())
            print()
        # Parent links are not in the snapshot, so children cost a call.
        kids = [index[n] for n in board_module.children(number, config) if n in index]
        if kids:
            print("  children:")
            board_module.show(kids, view)
    elif command == "find":
        # Issue bodies are not in the snapshot; this is real full-text search.
        found = board_module.search(" ".join(args), config)
        board_module.show([index[n] for n in found if n in index], view)
    elif command == "json":
        json.dump([item.flat() for item in items], sys.stdout, indent=2)
        print()
    return 0


def run_sync(config, store: Store, verb: str) -> int:
    if verb == "pull":
        # Gated before the call, so a missing scope reads as advice rather
        # than as whatever `gh` happens to print.
        auth.require(config, "read")
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
        return report_queue(store)
    return usage()


def queue(store: Store, args: list[str]) -> int:
    if len(args) < 3 or not args[0].lstrip("#").isdigit():
        return usage()
    number, field, value = int(args[0].lstrip("#")), args[1], " ".join(args[2:])
    snapshot = store.state()
    if snapshot.empty:
        print(STALE)
        return 1
    if snapshot.item(number) is None:
        print(f"#{number} is not on the board")
        return 1
    write = store.queue(number, field, value)
    print(f"Queued #{number} {field} = {value} (was {write.was or 'unset'}). "
          f"`board sync push` to apply.")
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
        print(f"#{write.number} {write.field}: wanted {write.value!r} — "
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
