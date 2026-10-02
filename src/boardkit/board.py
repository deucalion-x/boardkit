"""Read the board, filtered, and print only the rows that answer the question.

Every view is a filter over `rows()`. For a question the verbs do not cover,
pipe `board json` into jq or python rather than growing a verb.
"""
from __future__ import annotations

import collections

from .config import Config
from .gh import gh_json

MISSING = "-"
UNGROUPED = "Unassigned"


def open_numbers(config: Config) -> set[int]:
    """Issue numbers still open. `gh project item-list` does not carry state,
    so a closed issue stays on the board and would be counted as work."""
    issues = gh_json("issue", "list", "--repo", config.repo, "--state", "open",
                     "--limit", "200", "--json", "number")
    return {issue["number"] for issue in issues}


def normalize(items: list[dict], still_open: set[int], config: Config) -> list[dict]:
    """Project items to flat rows, dropping anything no longer open.

    Pure, so the shape of a row is testable without touching the network.
    """
    rows = []
    for item in items:
        content = item.get("content") or {}
        number = content.get("number")
        if number is None or number not in still_open:
            continue
        row = {
            "number": number,
            "title": content.get("title", ""),
            "url": content.get("url", ""),
            "status": item.get("status", MISSING),
        }
        for field in config.fields:
            default = UNGROUPED if field == config.grouping_field else MISSING
            row[field] = item.get(field, default)
        rows.append(row)
    return rows


def rows(config: Config) -> list[dict]:
    payload = gh_json("project", "item-list", config.project, "--owner", config.owner,
                      "--limit", "200", "--format", "json")
    return normalize(payload.get("items", []), open_numbers(config), config)


def rank(status: str, config: Config) -> int:
    order = config.status_order
    return order.index(status) if status in order else len(order)


def show(items: list[dict], config: Config) -> None:
    if not items:
        print("No matching items.")
        return
    widths = {field: max(len(field), *(len(str(i[field])) for i in items))
              for field in config.fields}
    for item in sorted(items, key=lambda i: (rank(i["status"], config), i["number"])):
        extra = "  ".join(f'{str(item[f]):{widths[f]}}' for f in config.fields)
        print(f'{item["status"]:12} #{item["number"]:<4} {item["title"][:50]:50} '
              f'{extra}  {item["url"]}')


def children(number: int, config: Config) -> list[int]:
    found = gh_json("issue", "list", "--repo", config.repo,
                    "--search", f"parent-issue:{config.repo}#{number}",
                    "--json", "number", "--limit", "50")
    return [row["number"] for row in found]


def search(terms: str, config: Config) -> list[int]:
    found = gh_json("issue", "list", "--repo", config.repo, "--search", terms,
                    "--json", "number", "--limit", "30")
    return [row["number"] for row in found]


def rollup(items: list[dict], config: Config) -> None:
    field = config.grouping_field
    table: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for item in items:
        table[str(item.get(field, UNGROUPED))][item["status"]] += 1
    width = max((len(name) for name in table), default=0)
    for name, counts in sorted(table.items()):
        summary = "  ".join(f"{status}={counts[status]}"
                            for status in config.status_order if counts[status])
        print(f"{name:{width}}  {summary}")
    print(f"\n{len(items)} items. `board ready` for what is startable.")
