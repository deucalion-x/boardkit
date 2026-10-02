"""One entry point, so every verb shares the same configuration lookup.

  board                      rollup by grouping field x status
  board ready                startable items only
  board group <name>         one group, by status            (alias: ws)
  board item <n>             one item with its children
  board find <terms>         full-text search, joined to board status
  board json                 normalized rows on stdout, for jq or python
  board mirror write|check   the issue-text mirror in git
  board links check|rewrite  sha-pinned document links in issue bodies
  board install-skill        write the agent skill into .claude/skills/
"""
from __future__ import annotations

import json
import sys

from . import board as board_module
from . import issue_mirror, plan_links, skill
from .config import load
from .gh import BoardError


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    command = argv[0] if argv else "rollup"
    try:
        return dispatch(command, argv[1:])
    except BoardError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def dispatch(command: str, args: list[str]) -> int:
    config = load()

    # Verbs that never touch the board are handled before it is fetched.
    if command == "install-skill":
        return skill.install(config, check="--check" in args)
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
    if command in {"-h", "--help", "help"}:
        return usage(0)

    items = board_module.rows(config)
    index = {item["number"]: item for item in items}

    if command == "rollup":
        board_module.rollup(items, config)
    elif command == "ready":
        board_module.show([i for i in items if i["status"] == "Ready"], config)
    elif command in {"group", "ws"}:
        field, name = config.grouping_field, " ".join(args).lower()
        board_module.show([i for i in items if name in str(i[field]).lower()], config)
    elif command == "item":
        if not args or not args[0].lstrip("#").isdigit():
            return usage()
        number = int(args[0].lstrip("#"))
        board_module.show([index[number]] if number in index else [], config)
        kids = [index[n] for n in board_module.children(number, config) if n in index]
        if kids:
            print("  children:")
            board_module.show(kids, config)
    elif command == "find":
        found = board_module.search(" ".join(args), config)
        board_module.show([index[n] for n in found if n in index], config)
    elif command == "json":
        json.dump(items, sys.stdout, indent=2)
        print()
    else:
        return usage()
    return 0


def usage(code: int = 2) -> int:
    print(__doc__)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
