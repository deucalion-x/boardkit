"""The repo-specific names, read from one table instead of edited into code.

Config comes from `[tool.boardkit]` in the consuming repository's
`pyproject.toml`, or from a `boardkit.toml` beside it for a repo that has no
Python packaging. Both are found by walking up from the working directory, so
every command works from anywhere inside the repo.

The models are the schema: required keys, unknown keys and field types are
rejected by pydantic rather than by checks written out here.
"""
from __future__ import annotations

import pathlib
import tomllib
from typing import Literal

from pydantic import (BaseModel, ConfigDict, Field, ValidationError, field_validator,
                      model_validator)

from .gh import BoardError

# Checked in this order within a directory, so a dedicated file wins over a
# table in pyproject.toml: writing one is the more explicit statement, and a
# repo with no Python packaging has nowhere else to put it.
CONFIG_FILES = (".boardkit.toml", "boardkit.toml", "pyproject.toml")
# Everything about the board itself, as opposed to which board it is. They may
# be written under `[board]`, so a config reads as identity at the top and the
# board's own shape in one section below it.
BOARD_SETTINGS = ("fields", "views", "status_order", "status_descriptions",
                  "status_field", "default_status", "blocked_field")
# What the generated skills are called, in every repo: an agent should find
# the same names wherever it is working. The admin one takes a suffix.
SKILL_NAME = "board"
# The columns a board gets when config names none, with what each one means.
# They say how far along an item is, which is true of any project. Being
# blocked is not a stage — a blocked item is still wherever it was — so it is
# not one of these.
# GitHub carries the description on the option itself, so the meaning is
# visible on the board.
# The one column that means startable, in the model every board gets.
READY_STATUS = "Todo"
# Being blocked is not a stage: it can happen at any of them, and the card
# does not move when it does. So it is a field holding why, empty when the
# card can move, and `board ready` leaves out whatever carries one.
BLOCKED_FIELD = "blocked"
DEFAULT_STATUS = {
    "Inbox": "captured, not yet refined",
    "Todo": "refined enough to pick up now",
    "In progress": "started",
    "In review": "finished and waiting on review",
    "Done": "reviewed and closed; stays on the board",
}

# GitHub's ProjectV2FieldType, limited to the values a project owns rather than
# projects from the issue. All six are creatable through the API; the four that
# `gh project field-create` handles are a limit of that wrapper, not of GitHub,
# so boardkit sends the mutation itself.
DataType = Literal["TEXT", "NUMBER", "DATE", "SINGLE_SELECT", "MULTI_SELECT", "ITERATION"]
CREATABLE = ("TEXT", "NUMBER", "DATE", "SINGLE_SELECT", "MULTI_SELECT", "ITERATION")


class Iteration(BaseModel):
    """How an iteration field is laid out: when it starts, how long each
    iteration runs, and how many to create up front."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    start_date: str = ""
    duration: int = 14
    iterations: int = 6


class ProjectField(BaseModel):
    """A Project field, under GitHub's own name for it.

    Declared in config it has no `id` — the project may not exist yet. After a
    sync it does, and writing a value needs it: `gh project item-edit` takes
    `--field-id`, and a single select also takes `--single-select-option-id`.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    # What the field is called on GitHub. Config names a field by the key
    # `item-list` emits — lowercased — so creating one has to turn that back
    # into something a person reads in a column header.
    title: str = ""
    data_type: DataType = "SINGLE_SELECT"
    options: tuple[str, ...] = ()
    # Option name -> what it means. GitHub carries a description on every
    # single-select option, so the meaning of a column belongs on the board
    # where everyone reading it can see it, rather than in a file beside it.
    # Declared as `{ name = "Ready", description = "..." }` entries in
    # `options`, which also accepts plain strings.
    descriptions: dict[str, str] = {}
    # Only read for an ITERATION field.
    iteration: Iteration = Iteration()
    id: str = ""
    # Option name -> option id, filled by a sync. Empty until then.
    option_ids: dict[str, str] = {}

    @model_validator(mode="before")
    @classmethod
    def _options_either_form(cls, data):
        """`options = ["Ready"]`, or entries carrying a description."""
        if not isinstance(data, dict) or not isinstance(data.get("options"), (list, tuple)):
            return data
        names, meanings = [], dict(data.get("descriptions") or {})
        for option in data["options"]:
            if isinstance(option, dict):
                names.append(option["name"])
                if option.get("description"):
                    meanings[option["name"]] = option["description"]
            else:
                names.append(option)
        return {**data, "options": tuple(names), "descriptions": meanings}

    @field_validator("data_type", mode="before")
    @classmethod
    def _accept_either_case(cls, value):
        """TOML reads better lowercase; GitHub's spelling is upper."""
        return value.upper().replace(" ", "_") if isinstance(value, str) else value

    @property
    def creatable(self) -> bool:
        return self.data_type in CREATABLE

    @property
    def display(self) -> str:
        return self.title or self.name.replace("_", " ").title()


Layout = Literal["BOARD", "TABLE", "ROADMAP"]


class View(BaseModel):
    """A saved tab on the board.

    `createProjectV2View` takes a name, a layout and which fields are visible;
    `updateProjectV2View` adds a filter. Neither takes a grouping, so which
    field a board view groups by is still a thing to set in the web UI — a
    board layout groups by Status on its own, which covers the usual case.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    layout: Layout = "TABLE"
    # Field names as config spells them; resolved to ids when the view is made.
    fields: tuple[str, ...] = ()
    # GitHub's Projects filter syntax, e.g. "-status:Done".
    filter: str = ""

    @field_validator("layout", mode="before")
    @classmethod
    def _accept_either_case(cls, value):
        return value.upper() if isinstance(value, str) else value


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, coerce_numbers_to_str=True)

    root: pathlib.Path
    # Which file this was read from. Set by `load`; an error that tells someone
    # to edit a file has to name the one actually in use, and three are
    # possible.
    source: pathlib.Path | None = None
    owner: str
    repo: str
    # Empty until the board exists: `admin create` is what produces the number,
    # so a repo setting itself up cannot be made to name one first. Every verb
    # that queries a board calls `require("project")` instead.
    project: str = ""
    owner_type: Literal["user", "org"] = "user"
    # Empty means "whatever the board says", in the board's own order. A repo
    # only names its columns to assert an order, or to create them.
    # The columns to create when `status_order` names none, and what each
    # means. A field rather than a constant, so `board schema` shows it and a
    # repo can replace it; reads never use it, because an empty `status_order`
    # means "whatever the board has".
    default_status: dict[str, str] = DEFAULT_STATUS
    # Created alongside the columns: what is in the way, at whatever stage the
    # card has reached. Empty means nothing is.
    blocked_field: str = BLOCKED_FIELD
    status_order: tuple[str, ...] = ()
    # Column name -> what it means, for the status field. Same form as a
    # field's `options`: a list of strings, or entries with a description.
    status_descriptions: dict[str, str] = {}
    # Which field holds the columns. GitHub has no Status concept: it is a
    # single select the default template happens to name "Status", so a project
    # that calls it "Stage" only has to say so here.
    status_field: str = "status"
    # Project fields beyond the status one, in the order they are printed.
    fields: tuple[ProjectField, ...] = ()
    # Saved tabs. Declared for the same reason a field is: `admin apply` has to
    # create them, and nothing else needs them stated.
    views: tuple[View, ...] = ()
    # `mirror` and `links` are optional; a repo may want the board reader alone.
    # Records per request, not a cap: a pull pages until the board is
    # exhausted. 100 is GitHub's own ceiling — `first: 150` is refused with
    # EXCESSIVE_PAGINATION — so this is the largest page there is, and lower
    # only trades more requests for nothing.
    page_size: int = Field(default=100, ge=1, le=100)
    mirror_path: str = ""
    docs_prefix: str = "docs/"
    project_name: str = ""
    project_url: str = ""

    @model_validator(mode="before")
    @classmethod
    def _status_order_either_form(cls, data):
        """`status_order` carries the columns, with or without meanings."""
        board = data.get("board") if isinstance(data, dict) else None
        table = board if isinstance(board, dict) and "status_order" in board else data
        if not isinstance(table, dict) or not isinstance(
                table.get("status_order"), (list, tuple)):
            return data
        names, meanings = [], dict(table.get("status_descriptions") or {})
        for column in table["status_order"]:
            if isinstance(column, dict):
                names.append(column["name"])
                if column.get("description"):
                    meanings[column["name"]] = column["description"]
            else:
                names.append(column)
        table = {**table, "status_order": tuple(names), "status_descriptions": meanings}
        return {**data, "board": table} if table is board else table

    @model_validator(mode="before")
    @classmethod
    def _lift_board_table(cls, data):
        """`[board]` is an optional home for everything about the board's own
        shape. The values are the same ones; grouping them changes nothing but
        where they are read."""
        if not isinstance(data, dict) or "board" not in data:
            return data
        data = dict(data)
        board = data.pop("board") or {}
        if not isinstance(board, dict):
            raise ValueError("[board] has to be a table")
        stray = sorted(key for key in board if key not in BOARD_SETTINGS)
        if stray:
            raise ValueError(
                f"{', '.join(stray)} cannot go under [board]; it holds "
                f"{', '.join(BOARD_SETTINGS)}")
        both = sorted(set(board) & set(data))
        if both:
            raise ValueError(
                f"{', '.join(both)} is declared twice, at the top level and "
                "under [board]; keep one")
        return data | board

    @field_validator("views", mode="before")
    @classmethod
    def _views_either_form(cls, value):
        """A table per view, or a bare list of names taking the defaults."""
        if isinstance(value, dict):
            return [{"name": name, **spec} for name, spec in value.items()]
        return [{"name": item} if isinstance(item, str) else item for item in value]

    @field_validator("fields", mode="before")
    @classmethod
    def _accept_both_forms(cls, value):
        """`fields = ["a", "b"]` for reading; a table per field for creating."""
        if isinstance(value, dict):
            return [{"name": name, **spec} for name, spec in value.items()]
        return [{"name": item} if isinstance(item, str) else item for item in value]

    @property
    def field_names(self) -> tuple[str, ...]:
        return tuple(field.name for field in self.fields)

    @property
    def declares(self) -> bool:
        """Whether this repo states its board's shape, or lets sync find it."""
        return bool(self.fields)

    @property
    def synced_names(self) -> tuple[str, ...]:
        """The fields a snapshot carries, or none to mean "discover them"."""
        if not self.declares:
            return ()
        return (self.status_field, *(n for n in self.field_names if n != self.status_field))

    def field(self, name: str) -> ProjectField | None:
        return next((f for f in self.fields if f.name == name), None)


    @property
    def skill_name(self) -> str:
        return SKILL_NAME

    @property
    def name(self) -> str:
        return self.project_name or self.repo.split("/")[-1].replace("-", " ").title()

    @property
    def url(self) -> str:
        if self.project_url:
            return self.project_url
        kind = "orgs" if self.owner_type == "org" else "users"
        return f"https://github.com/{kind}/{self.owner}/projects/{self.project}"

    def path(self, relative: str) -> pathlib.Path:
        return self.root / relative

    def require(self, setting: str) -> str:
        value = getattr(self, setting)
        if not value:
            raise BoardError(f"{setting} is not set; add it to {self.where}")
        return value

    @property
    def where(self) -> str:
        """The config file to edit, named as a person would find it."""
        if self.source is None:
            return f"the boardkit configuration in {self.root}"
        if self.source.name == "pyproject.toml":
            return f"[tool.boardkit] in {self.source}"
        return str(self.source)


def find_config(start: pathlib.Path | None = None) -> tuple[pathlib.Path, dict, pathlib.Path]:
    """Walk up from `start` for the first file carrying a boardkit table."""
    here = (start or pathlib.Path.cwd()).resolve()
    for directory in [here, *here.parents]:
        for name in CONFIG_FILES:
            candidate = directory / name
            if not candidate.exists():
                continue
            data = tomllib.loads(candidate.read_text(encoding="utf-8"))
            table = data.get("tool", {}).get("boardkit") if name.endswith(
                "pyproject.toml") else data.get("tool", {}).get("boardkit", data)
            if table:
                return directory, table, candidate
    raise BoardError(
        "no boardkit configuration found. Add a [tool.boardkit] table to "
        "pyproject.toml, or a boardkit.toml, at the root of the repository."
    )


def load(start: pathlib.Path | None = None) -> Config:
    root, table, source = find_config(start)
    try:
        return Config(root=root, source=source, **table)
    except ValidationError as error:
        raise BoardError(describe(error, source)) from None


def describe(error: ValidationError, source: pathlib.Path) -> str:
    """Pydantic's report as advice: one line per bad setting, no traceback.

    The two common mistakes get their own wording, because "Field required"
    and "Extra inputs are not permitted" do not tell the reader what to type.
    """
    known = ", ".join(sorted(Config.model_fields.keys() - {"root", "source"}))
    lines = []
    for detail in error.errors():
        setting = ".".join(str(part) for part in detail["loc"]) or "<table>"
        if detail["type"] == "missing":
            lines.append(f"  missing `{setting}`")
        elif detail["type"] == "extra_forbidden":
            lines.append(f"  unknown boardkit setting `{setting}`")
        else:
            lines.append(f"  {setting}: {detail['msg']}")
    return (
        f"invalid boardkit configuration in {source}:\n"
        + "\n".join(lines)
        + f"\nKnown settings: {known}."
    )
