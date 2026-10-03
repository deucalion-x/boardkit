"""Read the board, filtered, and print only the items that answer the question.

Every view is a filter over `items()`. For a question the verbs do not cover,
pipe `board json` into jq or python rather than growing a verb.
"""
from __future__ import annotations

import collections
import re
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, field_validator

from .config import Config
from .gh import gh_json

MISSING = "-"
UNGROUPED = "Unassigned"


ItemType = Literal["ISSUE", "PULL_REQUEST", "DRAFT_ISSUE", "REDACTED"]


class ProjectItem(BaseModel):
    """One `gh project item-list` entry under GitHub's names for it.

        ProjectV2Item.id        -> id        (needed to write a value)
        ProjectV2Item.type      -> type
        content.number/title/url -> number, title, url
        every field value       -> fields[name], including the status field

    GitHub hands field values over as name/value pairs, and which names exist
    is Project configuration, so they stay a dict rather than being promoted
    to attributes this class cannot know. Status is one of them: GitHub has no
    Status concept, only a single select that is conventionally called that.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)

    number: int
    id: str = ""
    type: ItemType = "ISSUE"

    title: str = ""
    url: str = ""
    fields: dict[str, str] = {}

    @field_validator("type", mode="before")
    @classmethod
    def _as_enum(cls, value):
        """`item-list` reports `Issue`; the GraphQL enum is `ISSUE`."""
        if not isinstance(value, str):
            return value
        spelled = re.sub(r"(?<!^)(?=[A-Z])", "_", value).upper()
        return spelled if spelled in get_args(ItemType) else "REDACTED"

    def field(self, name: str) -> str:
        return self.fields.get(name, MISSING)

    def flat(self) -> dict:
        """One row per item, field values inlined — the shape `board json`
        has always printed, and what a jq pipeline is written against."""
        return {"number": self.number, "title": self.title, "url": self.url,
                **self.fields}


def open_numbers(config: Config) -> set[int]:
    """Issue numbers still open. `gh project item-list` does not carry state,
    so a closed issue stays on the board and would be counted as work."""
    issues = gh_json("issue", "list", "--repo", config.repo, "--state", "open",
                     "--limit", "200", "--json", "number")
    return {issue["number"] for issue in issues}


def as_text(value) -> str:
    """One field value as a string.

    Most values arrive as strings already, but an iteration reports an object
    (`title`, `startDate`, `duration`, `iterationId`) and a multi-select a
    list. Stringifying those raw would print a dict into a table column.
    """
    if isinstance(value, dict):
        return str(value.get("title") or value.get("name") or MISSING)
    if isinstance(value, list):
        return ", ".join(as_text(item) for item in value) or MISSING
    return str(value)


def normalize(payload: list[dict], still_open: set[int], config: Config) -> list[ProjectItem]:
    """Project payload to items, dropping anything no longer open.

    Pure, so the shape of an item is testable without touching the network.
    """
    items = []
    for entry in payload:
        content = entry.get("content") or {}
        number = content.get("number")
        if number is None or number not in still_open:
            continue
        fields = {}
        for field in config.synced_names:
            default = UNGROUPED if field == config.grouping_field else MISSING
            fields[field] = as_text(entry.get(field, default))
        items.append(ProjectItem(
            number=number,
            id=entry.get("id", ""),
            type=entry.get("content", {}).get("type", "ISSUE") or "ISSUE",
            title=content.get("title", ""),
            url=content.get("url", ""),
            fields=fields,
        ))
    return items


def status_of(item: ProjectItem, config: Config) -> str:
    """The column an item sits in, under whatever that field is called here."""
    return item.field(config.status_field)


def rank(status: str, config: Config) -> int:
    order = config.status_order
    return order.index(status) if status in order else len(order)


def show(items: list[ProjectItem], config: Config) -> None:
    if not items:
        print("No matching items.")
        return
    extras = [f for f in config.field_names if f != config.status_field]
    widths = {field: max(len(field), *(len(i.field(field)) for i in items))
              for field in extras}
    for item in sorted(items, key=lambda i: (rank(status_of(i, config), config), i.number)):
        extra = "  ".join(f'{item.field(f):{widths[f]}}' for f in extras)
        print(f'{status_of(item, config):12} #{item.number:<4} {item.title[:50]:50} '
              f'{extra}  {item.url}')


def children(number: int, config: Config) -> list[int]:
    found = gh_json("issue", "list", "--repo", config.repo,
                    "--search", f"parent-issue:{config.repo}#{number}",
                    "--json", "number", "--limit", "50")
    return [found_item["number"] for found_item in found]


def search(terms: str, config: Config) -> list[int]:
    found = gh_json("issue", "list", "--repo", config.repo, "--search", terms,
                    "--json", "number", "--limit", "30")
    return [found_item["number"] for found_item in found]


def rollup(items: list[ProjectItem], config: Config) -> None:
    field = config.grouping_field
    table: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for item in items:
        table[item.field(field)][status_of(item, config)] += 1
    width = max((len(name) for name in table), default=0)
    for name, counts in sorted(table.items()):
        summary = "  ".join(f"{status}={counts[status]}"
                            for status in config.status_order if counts[status])
        print(f"{name:{width}}  {summary}")
    print(f"\n{len(items)} items. `board ready` for what is startable.")
