"""The repo-specific names, read from one table instead of edited into code.

Config comes from `[tool.boardkit]` in the consuming repository's
`pyproject.toml`, or from a `boardkit.toml` beside it for a repo that has no
Python packaging. Both are found by walking up from the working directory, so
every command works from anywhere inside the repo.
"""
from __future__ import annotations

import dataclasses
import pathlib
import tomllib

from .gh import BoardError

CONFIG_FILES = ("pyproject.toml", "boardkit.toml", ".boardkit.toml")
DEFAULT_STATUS_ORDER = ["Ready", "In progress", "Evaluating", "Inbox", "Blocked", "Done"]


@dataclasses.dataclass(frozen=True)
class Config:
    root: pathlib.Path
    owner: str
    project: str
    repo: str
    owner_type: str = "user"
    status_order: tuple[str, ...] = tuple(DEFAULT_STATUS_ORDER)
    # Project fields beyond Status, in the order they are printed. The first one
    # groups the rollup unless `group_by` says otherwise.
    fields: tuple[str, ...] = ("workstream", "effort")
    group_by: str = ""
    # `mirror` and `links` are optional; a repo may want the board reader alone.
    mirror_path: str = ""
    docs_prefix: str = "docs/"
    project_name: str = ""
    skill_name: str = "github-board"
    skill_notes: str = ""
    project_url: str = ""

    @property
    def grouping_field(self) -> str:
        return self.group_by or (self.fields[0] if self.fields else "status")

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
    known = {field.name for field in dataclasses.fields(Config)} - {"root"}
    unknown = sorted(set(table) - known)
    if unknown:
        raise BoardError(
            f"unknown boardkit setting(s): {', '.join(unknown)}. "
            f"Known settings: {', '.join(sorted(known))}."
        )
    for required in ("owner", "project", "repo"):
        if not table.get(required):
            raise BoardError(f"boardkit configuration is missing `{required}`")
    values = dict(table)
    for name in ("status_order", "fields"):
        if name in values:
            values[name] = tuple(values[name])
    values["project"] = str(values["project"])
    return Config(root=root, **values)
