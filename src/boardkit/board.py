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
    # The issue text, carried so that deciding what to work on — which means
    # reading the acceptance criteria — needs no API call. A scalar on a node
    # already fetched, so it costs bytes and no rate-limit points.
    body: str = ""
    # Issue metadata, not board metadata. Carried because an agent choosing
    # work reads it — labels especially, which is where a repo tends to keep
    # kind and cross-cutting concept.
    labels: tuple[str, ...] = ()
    assignees: tuple[str, ...] = ()
    milestone: str = ""
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
        # The body is deliberately absent: `board json` feeds jq pipelines,
        # and a page of prose per item buries the rows. `board item` shows it.
        row = {"number": self.number, "title": self.title, "url": self.url,
               **self.fields}
        if self.labels:
            row["labels"] = ", ".join(self.labels)
        if self.assignees:
            row["assignees"] = ", ".join(self.assignees)
        if self.milestone:
            row["milestone"] = self.milestone
        return row


def open_numbers(config: Config) -> set[int]:
    """Issue numbers still open. `gh project item-list` does not carry state,
    so a closed issue stays on the board and would be counted as work."""
    issues = gh_json("issue", "list", "--repo", config.repo, "--state", "open",
                     "--limit", str(config.item_limit), "--json", "number")
    return {issue["number"] for issue in issues}


def named(connection, key: str) -> tuple[str, ...]:
    """The names out of a GraphQL connection, which arrives as {"nodes": [...]}."""
    nodes = (connection or {}).get("nodes") or []
    return tuple(node.get(key, "") for node in nodes if node)


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


def normalize(payload: list[dict], still_open: set[int], config: Config,
              names: tuple[str, ...] = ()) -> list[ProjectItem]:
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
        for field in (names or config.synced_names):
            default = UNGROUPED if field == config.grouping_field else MISSING
            fields[field] = as_text(entry.get(field, default))
        items.append(ProjectItem(
            number=number,
            id=entry.get("id", ""),
            type=entry.get("content", {}).get("type", "ISSUE") or "ISSUE",
            title=content.get("title", ""),
            url=content.get("url", ""),
            body=content.get("body") or "",
            labels=named(content.get("labels"), "name"),
            assignees=named(content.get("assignees"), "login"),
            milestone=(content.get("milestone") or {}).get("title", ""),
            fields=fields,
        ))
    return items


class Shape(BaseModel):
    """How to read a board: its columns and the fields worth printing.

    Taken from the snapshot unless config asserts otherwise, so a repo that
    declares nothing still gets its own columns in its own order rather than
    a generic default.
    """
    model_config = ConfigDict(frozen=True)

    status_field: str = "status"
    columns: tuple[str, ...] = ()
    names: tuple[str, ...] = ()
    grouping: str = "status"


def shape(config: Config, fields: tuple) -> Shape:
    status = next((f for f in fields if f.name == config.status_field), None)
    columns = config.status_order or (status.options if status else ())
    # Declared fields print first, in the order they are declared; anything
    # else the board carries follows, so a new field shows up without config
    # having to hear about it.
    discovered = [f.name for f in fields if f.name != config.status_field]
    names = (*config.field_names,
             *(n for n in discovered if n not in config.field_names))
    return Shape(status_field=config.status_field,
                 columns=tuple(columns),
                 names=names,
                 grouping=config.group_by or (names[0] if names else config.status_field))


def rank(status: str, view: Shape) -> int:
    order = view.columns
    return order.index(status) if status in order else len(order)


def show(items: list[ProjectItem], view: Shape) -> None:
    if not items:
        print("No matching items.")
        return
    extras = [f for f in view.names if f != view.status_field]
    widths = {field: max(len(field), *(len(i.field(field)) for i in items))
              for field in extras}
    for item in sorted(items, key=lambda i: (rank(i.field(view.status_field), view),
                                             i.number)):
        extra = "  ".join(f'{item.field(f):{widths[f]}}' for f in extras)
        print(f'{item.field(view.status_field):12} #{item.number:<4} '
              f'{item.title[:50]:50} {extra}  {item.url}')


def children(number: int, config: Config) -> list[int]:
    found = gh_json("issue", "list", "--repo", config.repo,
                    "--search", f"parent-issue:{config.repo}#{number}",
                    "--json", "number", "--limit", "50")
    return [found_item["number"] for found_item in found]


def search(terms: str, config: Config) -> list[int]:
    found = gh_json("issue", "list", "--repo", config.repo, "--search", terms,
                    "--json", "number", "--limit", "30")
    return [found_item["number"] for found_item in found]


def rollup(items: list[ProjectItem], view: Shape) -> None:
    table: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for item in items:
        table[item.field(view.grouping)][item.field(view.status_field)] += 1
    width = max((len(name) for name in table), default=0)
    for name, counts in sorted(table.items()):
        summary = "  ".join(f"{status}={counts[status]}"
                            for status in view.columns if counts[status])
        print(f"{name:{width}}  {summary}")
    print(f"\n{len(items)} items. `board ready` for what is startable.")
