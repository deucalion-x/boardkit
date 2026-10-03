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
import re
import tomllib
from typing import Literal

from pydantic import (BaseModel, ConfigDict, Field, ValidationError, field_validator,
                      model_validator)

from .gh import BoardError

# Checked in this order within a directory, so a dedicated file wins over a
# table in pyproject.toml: writing one is the more explicit statement, and a
# repo with no Python packaging has nowhere else to put it.
CONFIG_FILES = (".boardkit.toml", "boardkit.toml", "pyproject.toml")
# Settings that describe the board this repo wants rather than how to reach the
# one it has. They may be written under `[admin]`, which says so in the file
# itself; `admin apply` is the only command that needs them, apart from the
# print order a declared shape also fixes.
ADMIN_SETTINGS = ("fields", "views", "status_order")
# What a skill is called when a repo does not say. Derived rather than fixed,
# because two repos open at once — a tool and the project using it — would
# otherwise install two skills under one name, each describing a different
# board and neither distinguishable from the other.
SKILL_SUFFIX = "-board"
FALLBACK_SKILL = "github-board"
# GitHub's own columns on a new Project. A default that fits one project would
# work silently and wrongly everywhere else, so this is deliberately generic.
DEFAULT_STATUS_ORDER = ("Todo", "In Progress", "Done")

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
    # Only read for an ITERATION field.
    iteration: Iteration = Iteration()
    id: str = ""
    # Option name -> option id, filled by a sync. Empty until then.
    option_ids: dict[str, str] = {}

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
    status_order: tuple[str, ...] = ()
    # Which field holds the columns. GitHub has no Status concept: it is a
    # single select the default template happens to name "Status", so a project
    # that calls it "Stage" only has to say so here.
    status_field: str = "status"
    # Project fields beyond the status one, in the order they are printed. The
    # first groups the rollup unless `group_by` says otherwise.
    fields: tuple[ProjectField, ...] = ()
    group_by: str = ""
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
    # Empty means derived from the repository name; a repo only states this to
    # be called something the derivation would not produce.
    skill_name: str = ""
    skill_notes: str = ""
    project_url: str = ""

    @model_validator(mode="before")
    @classmethod
    def _name_the_skill(cls, data):
        """Fill in `skill_name` from the repository when it is not stated."""
        if isinstance(data, dict) and not data.get("skill_name"):
            data = dict(data) | {"skill_name": skill_name_for(data.get("repo", ""))}
        return data

    @model_validator(mode="before")
    @classmethod
    def _lift_admin_table(cls, data):
        """`[admin]` is an optional home for the settings that exist to build a
        board, so a file says which of its settings are for that. The values are
        the same ones; grouping them changes nothing but where they are read."""
        if not isinstance(data, dict) or "admin" not in data:
            return data
        data = dict(data)
        admin = data.pop("admin") or {}
        if not isinstance(admin, dict):
            raise ValueError("[admin] has to be a table")
        stray = sorted(key for key in admin if key not in ADMIN_SETTINGS)
        if stray:
            raise ValueError(
                f"{', '.join(stray)} cannot go under [admin]; it holds "
                f"{', '.join(ADMIN_SETTINGS)}")
        both = sorted(set(admin) & set(data))
        if both:
            raise ValueError(
                f"{', '.join(both)} is declared twice, at the top level and "
                "under [admin]; keep one")
        return data | admin

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
    def grouping_field(self) -> str:
        return self.group_by or (self.field_names[0] if self.fields else self.status_field)

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


def skill_name_for(repo: str) -> str:
    """`agerardin/places` becomes `places-board`: the repo's own word, plus
    what the skill is about."""
    slug = re.sub(r"[^a-z0-9]+", "-", repo.split("/")[-1].strip().lower()).strip("-")
    return f"{slug}{SKILL_SUFFIX}" if slug else FALLBACK_SKILL


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
