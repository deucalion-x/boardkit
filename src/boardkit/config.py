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

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from .gh import BoardError

# Checked in this order within a directory, so a dedicated file wins over a
# table in pyproject.toml: writing one is the more explicit statement, and a
# repo with no Python packaging has nowhere else to put it.
CONFIG_FILES = (".boardkit.toml", "boardkit.toml", "pyproject.toml")
# GitHub's own columns on a new Project. A default that fits one project would
# work silently and wrongly everywhere else, so this is deliberately generic.
DEFAULT_STATUS_ORDER = ("Todo", "In Progress", "Done")

# GitHub's ProjectV2FieldType, limited to the values a project owns rather than
# projects from the issue. Of these, `gh project field-create` can create only
# TEXT, SINGLE_SELECT, DATE and NUMBER.
DataType = Literal["TEXT", "NUMBER", "DATE", "SINGLE_SELECT", "MULTI_SELECT", "ITERATION"]
CREATABLE = ("TEXT", "SINGLE_SELECT", "DATE", "NUMBER")


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


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, coerce_numbers_to_str=True)

    root: pathlib.Path
    owner: str
    repo: str
    # Empty until the board exists: `admin create` is what produces the number,
    # so a repo setting itself up cannot be made to name one first. Every verb
    # that queries a board calls `require("project")` instead.
    project: str = ""
    owner_type: Literal["user", "org"] = "user"
    status_order: tuple[str, ...] = DEFAULT_STATUS_ORDER
    # Which field holds the columns. GitHub has no Status concept: it is a
    # single select the default template happens to name "Status", so a project
    # that calls it "Stage" only has to say so here.
    status_field: str = "status"
    # Project fields beyond the status one, in the order they are printed. The
    # first groups the rollup unless `group_by` says otherwise.
    fields: tuple[ProjectField, ...] = ()
    group_by: str = ""
    # `mirror` and `links` are optional; a repo may want the board reader alone.
    mirror_path: str = ""
    docs_prefix: str = "docs/"
    project_name: str = ""
    skill_name: str = "github-board"
    skill_notes: str = ""
    project_url: str = ""

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
    def synced_names(self) -> tuple[str, ...]:
        """Every field a snapshot carries: the status field, then the rest."""
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
            raise BoardError(
                f"{setting} is not set; add it to [tool.boardkit] in "
                f"{self.root / 'pyproject.toml'}"
            )
        return value


def find_config(start: pathlib.Path | None = None) -> tuple[pathlib.Path, dict]:
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
                return directory, table
    raise BoardError(
        "no boardkit configuration found. Add a [tool.boardkit] table to "
        "pyproject.toml, or a boardkit.toml, at the root of the repository."
    )


def load(start: pathlib.Path | None = None) -> Config:
    root, table = find_config(start)
    try:
        return Config(root=root, **table)
    except ValidationError as error:
        raise BoardError(describe(error, root)) from None


def describe(error: ValidationError, root: pathlib.Path) -> str:
    """Pydantic's report as advice: one line per bad setting, no traceback.

    The two common mistakes get their own wording, because "Field required"
    and "Extra inputs are not permitted" do not tell the reader what to type.
    """
    known = ", ".join(sorted(Config.model_fields.keys() - {"root"}))
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
        f"invalid boardkit configuration in {root / 'pyproject.toml'}:\n"
        + "\n".join(lines)
        + f"\nKnown settings: {known}."
    )
