"""Read the board, filtered, and print only the items that answer the question.

Every view is a filter over `items()`. For a question the verbs do not cover,
pipe `board json` into jq or python rather than growing a verb.
"""
from __future__ import annotations

from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, field_validator

from .config import Config
from .gh import gh_json

MISSING = "-"


ItemType = Literal["ISSUE", "PULL_REQUEST", "DRAFT_ISSUE", "REDACTED"]


class ProjectItem(BaseModel):
    """One project item under GitHub's names for it.

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

    # Zero for a draft card, which GitHub gives no issue number. `ref` is what
    # names an item in every command, so that drafts can be addressed at all.
    number: int = 0
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
        """The kind arrives as the GraphQL enum, or not at all.

        An unknown spelling is recorded as REDACTED rather than refused: a
        kind nobody can read is still an item on the board, and dropping it
        would make the board quietly smaller than it is.
        """
        if not isinstance(value, str):
            return value
        return value if value in get_args(ItemType) else "REDACTED"

    @property
    def ref(self) -> str:
        """How an item is named: its issue number, or a short handle for a
        draft, which has none. The handle is the tail of the node id, which is
        stable for the life of the card and distinct enough to type."""
        if self.number:
            return str(self.number)
        return f"~{self.id[-6:]}" if self.id else "~?"

    @property
    def draft(self) -> bool:
        return self.type == "DRAFT_ISSUE"

    def field(self, name: str) -> str:
        return self.fields.get(name, MISSING)

    def flat(self) -> dict:
        """One row per item, field values inlined — the shape `board json`
        has always printed, and what a jq pipeline is written against."""
        # The body is deliberately absent: `board json` feeds jq pipelines,
        # and a page of prose per item buries the rows. `board item` shows it.
        # `-` is how an unset field is printed in a listing, where a column
        # has to hold something; it is not a value. A pipeline comparing
        # against "" would otherwise have to know that convention, and know
        # it only applies to some rows.
        values = {name: ("" if value == MISSING else value)
                  for name, value in self.fields.items()}
        row = {"ref": self.ref, "number": self.number, "title": self.title,
               "url": self.url, **values}
        if self.labels:
            row["labels"] = ", ".join(self.labels)
        if self.assignees:
            row["assignees"] = ", ".join(self.assignees)
        if self.milestone:
            row["milestone"] = self.milestone
        return row


def named(connection, key: str) -> tuple[str, ...]:
    """The names out of a GraphQL connection, which arrives as {"nodes": [...]}."""
    nodes = (connection or {}).get("nodes") or []
    return tuple(node.get(key, "") for node in nodes if node)


def as_text(value) -> str:
    """One field value as a string.

    Most values arrive as strings already — `value_of` has already taken the
    one scalar out of each typed shape — but a multi-select arrives as a list
    of names. Joining it here keeps a table column from holding a repr.
    """
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
        kind = entry.get("type") or content.get("type")
        # A draft has no number and so cannot be open or closed; everything
        # else is dropped once it is closed.
        if kind != "DRAFT_ISSUE" and (number is None or number not in still_open):
            continue
        fields = {}
        for field in (names or config.synced_names):
            default = MISSING
            fields[field] = as_text(entry.get(field, default))
        items.append(ProjectItem(
            number=number or 0,
            id=entry.get("id", ""),
            # GraphQL puts the kind on the item node; `gh` put it inside the
            # content. Accept either rather than depend on which produced it.
            type=entry.get("type") or content.get("type") or "ISSUE",
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
                 names=names)


def rank(status: str, view: Shape) -> int:
    order = view.columns
    return order.index(status) if status in order else len(order)


def show(items: list[ProjectItem], view: Shape) -> None:
    if not items:
        # The listing is the only place the board's shape appears, so an empty
        # one used to say nothing at all — leaving a first card to be filed
        # against guessed column and field names.
        print("No matching items.")
        if view.columns:
            print(f"  columns: {', '.join(view.columns)}")
        if view.names:
            print(f"  fields:  {', '.join(view.names)}")
        return
    extras = [f for f in view.names if f != view.status_field]
    widths = {field: max(len(field), *(len(i.field(field)) for i in items))
              for field in extras}
    names = max((len(item.ref) for item in items), default=4)
    # Named, because the extra columns are a board's own fields: without a
    # header the third column of `Core  -` is anybody's guess.
    if extras:
        heads = "  ".join(f"{field:{widths[field]}}" for field in extras)
        print(f'{view.status_field:12} {"ref":<{names + 1}} {"title":50} {heads}')
    for item in sorted(items, key=lambda i: (rank(i.field(view.status_field), view),
                                             i.number, i.ref)):
        extra = "  ".join(f'{item.field(f):{widths[f]}}' for f in extras)
        marker = "" if item.number else " (draft)"
        print(f'{item.field(view.status_field):12} #{item.ref:<{names}} '
              f'{item.title[:50]:50} {extra}  {item.url}{marker}')


def children(number: int, config: Config) -> list[int]:
    found = gh_json("issue", "list", "--repo", config.repo,
                    "--search", f"parent-issue:{config.repo}#{number}",
                    "--json", "number", "--limit", "50")
    return [found_item["number"] for found_item in found]


def matching(items: list[ProjectItem], terms: str) -> list[ProjectItem]:
    """Items whose title or body contains every word given.

    Local, because the snapshot carries the text now. It is a substring match
    and not GitHub's search syntax — `gh issue list --search` is still what
    answers a question about closed work or history, which the snapshot does
    not hold.
    """
    words = [word.lower() for word in terms.split() if word]
    if not words:
        return []
    found = []
    for item in items:
        haystack = f"{item.title}\n{item.body}\n{' '.join(item.labels)}".lower()
        if all(word in haystack for word in words):
            found.append(item)
    return found


