"""The repo-specific names, read from one table instead of edited into code.

Config comes from `[tool.boardkit]` in the consuming repository's
`pyproject.toml`, or from a `boardkit.toml` beside it for a repo that has no
Python packaging. Both are found by walking up from the working directory, so
every command works from anywhere inside the repo.

Any scalar setting is also readable as `BOARDKIT_<SETTING>`, which wins over
the file. The file records what a repository decided; the variable records
what this machine or this one run needs instead, which is the narrower claim
and so the louder one.

The models are the schema: required keys, unknown keys and field types are
rejected by pydantic rather than by checks written out here.
"""
from __future__ import annotations

import os
import pathlib
import re
import tomllib
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .gh import BoardError

# Checked in this order within a directory, so a dedicated file wins over a
# table in pyproject.toml: writing one is the more explicit statement, and a
# repo with no Python packaging has nowhere else to put it.
CONFIG_FILES = (".boardkit.toml", "boardkit.toml", "pyproject.toml")
# Every setting is also reachable as BOARDKIT_<NAME>, which wins over the file.
# The file says what a repository decided; the variable says what this machine
# or this run needs instead — a CI job with no checkout to edit, a floor raised
# for one command — so it is the narrower statement and therefore the louder.
# Only scalars: a list or a table in an environment variable is a parser nobody
# asked for, and those settings describe the board rather than the run.
ENV_PREFIX = "BOARDKIT_"
# Everything about the board itself, as opposed to which board it is. They may
# be written under `[board]`, so a config reads as identity at the top and the
# board's own shape in one section below it.
BOARD_SETTINGS = ("url", "fields", "views", "labels", "status_order", "status_descriptions",
                  "status_field", "default_status", "blocked_field", "preset")
# Which board this is, as a person copies it from the browser. It says
# everything `owner`, `owner_type` and `project` say, so it replaces them.
BOARD_URL = re.compile(
    r"https://github\.com/(?P<kind>orgs|users)/(?P<owner>[A-Za-z0-9-]+)"
    r"/projects/(?P<number>[0-9]+)/?")
# The keys a URL replaces, kept working for configs written before it.
IDENTITY = ("owner", "owner_type", "project")
# Settings that are lists of named entries, merged with a preset's by name.
NAMED_SETTINGS = ("fields", "views", "labels")
# A board shape shipped with boardkit, named by `preset` under [board]. The
# repo's own settings are merged over it, so a preset is where a board starts
# rather than a cage.
PRESETS = pathlib.Path(__file__).with_name("templates") / "presets"
# Earlier names for a preset, still accepted. `pm` was first called `roadmap`,
# which named one of its views rather than what the board is for.
PRESET_ALIASES = {"roadmap": "pm"}
# What the generated skills are called, in every repo: an agent should find
# the same names wherever it is working. The admin one takes a suffix.
SKILL_NAME = "board"
# The columns a board gets when config names none, with what each one means.
# They say how far along an item is, which is true of any project. Being
# blocked is not a stage — a blocked item is still wherever it was — so it is
# not one of these.
# GitHub carries the description on the option itself, so the meaning is
# visible on the board.
# Being blocked is not a stage: it can happen at any of them, and the card
# does not move when it does. So it is a field holding why, empty when the
# card can move, and a listing shows it beside whatever else the item holds.
BLOCKED_FIELD = "blocked"
DEFAULT_STATUS = {
    "Inbox": "captured, not yet refined",
    "Todo": "refined enough to pick up now",
    "In Progress": "started",
    "In Review": "finished and waiting on review",
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


class Label(BaseModel):
    """A label the repository's issues are classified by.

    Labels belong to the repository, not to the board, but the skill relies on
    some of them — `dev` decides what an agent may pick up — so a board that
    lacks them is one the skill cannot be followed on. `admin apply` creates
    the missing ones and leaves every existing label as it is.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    # Hex, without the `#`, as GitHub's API takes it.
    color: str = "ededed"
    description: str = ""

    @field_validator("color", mode="before")
    @classmethod
    def _without_hash(cls, value):
        return value.lstrip("#") if isinstance(value, str) else value


class Portfolio(BaseModel):
    """Which repositories a portfolio board shows the Initiatives of.

    Not part of any preset: a preset is a shape every board of its kind
    shares, and which repositories a portfolio covers is this one's alone.
    Names are written bare (`"web-app"`) or in full; `load` makes
    them full, under the board's own owner.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)

    repos: tuple[str, ...] = ()


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, coerce_numbers_to_str=True)

    root: pathlib.Path
    # Which file this was read from. Set by `load`; an error that tells someone
    # to edit a file has to name the one actually in use, and three are
    # possible.
    source: pathlib.Path | None = None
    # Derived from `[board] url` when it is set, which is the preferred form:
    # one value, the one a person copies, rather than three to keep in step.
    owner: str
    repo: str
    # Empty until the board exists: `admin create` is what produces the number,
    # so a repo setting itself up cannot be made to name one first. Every verb
    # that queries a board calls `require("project")` instead.
    project: str = ""
    owner_type: Literal["user", "org"] = "user"
    # The client ID of the GitHub App this repository signs in through. GitHub
    # limits a sign-in to the repos its app is installed on, so one app per
    # repository keeps each sign-in to that repository alone, and repositories
    # sharing an organisation's app share one sign-in. Empty means no app,
    # and a per-repository token instead.
    app_client_id: str = ""
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
    # Repository labels the board's work is classified by, created by `admin
    # apply` when missing.
    labels: tuple[Label, ...] = ()
    # Which preset the board shape starts from, already merged into the
    # settings above by `load`. Kept so `admin` can say what the preset leaves
    # to the browser.
    preset: str = ""
    # `mirror` and `links` are optional; a repo may want the board reader alone.
    # Records per request, not a cap: a pull pages until the board is
    # exhausted. 100 is GitHub's own ceiling — `first: 150` is refused with
    # EXCESSIVE_PAGINATION — so this is the largest page there is, and lower
    # only trades more requests for nothing.
    page_size: int = Field(default=100, ge=1, le=100)
    # The oldest a snapshot may be and still answer a read. Past it, the read
    # pulls first, so freshness is a property of the read rather than
    # something an agent has to remember. 0 never syncs on read.
    #
    # 300 because the gaps between real reads are bimodal, measured over one
    # session's trail: eighteen inside 17s, then nothing until 44s, then a
    # cluster at 73-199s, then hours. A floor of 300 answers three reads in
    # four from disk and still bounds staleness two orders of magnitude below
    # the 16-hour snapshot that nearly got reported as current. See
    # docs/pull-cost.md.
    max_read_age_seconds: int = Field(default=300, ge=0)
    mirror_path: str = ""
    docs_prefix: str = "docs/"
    # The title of a board that does not exist yet, for `admin create`. Once
    # `[board] url` is set nothing reads it: the number says which board.
    project_name: str = ""
    # The board's URL, written `[board] url`. `project_url` is its earlier
    # name, from when it was only printed.
    project_url: str = ""
    # Set only on a portfolio: a board of the Initiatives of other
    # repositories, filled by `board portfolio sync` and edited by nothing else.
    portfolio: Portfolio | None = None

    @model_validator(mode="before")
    @classmethod
    def _portfolio_repos_in_full(cls, data):
        """Each repository as owner/name, and only the board's own owner's.

        An organisation's project holds only that organisation's issues, so a
        repository elsewhere could never be added. Refused here, by name,
        rather than by GitHub on the first sync.
        """
        if not isinstance(data, dict) or not isinstance(data.get("portfolio"), dict):
            return data
        owner = str(data.get("owner", ""))
        table = dict(data["portfolio"])
        full = []
        for name in table.get("repos") or ():
            if not isinstance(name, str):
                return data  # Pydantic reports the type.
            whose, _, bare = name.rpartition("/")
            if whose and owner and whose.lower() != owner.lower():
                raise ValueError(
                    f"portfolio repo {name!r} is not {owner}'s; a portfolio "
                    f"only holds the issues of its own organisation")
            full.append(f"{whose or owner}/{bare}")
        table["repos"] = tuple(dict.fromkeys(full))
        return {**data, "portfolio": table}

    # Pydantic runs "before" validators last-defined first, so this one sees
    # [board] already lifted and the portfolio check after it sees the owner.
    @model_validator(mode="before")
    @classmethod
    def _identity_from_url(cls, data):
        """`owner`, `owner_type` and `project` from the board's URL.

        Stated alongside it, they have to agree: two values for one thing is
        a question of which one is wrong, and guessing would hide it.
        """
        if not isinstance(data, dict):
            return data
        data = dict(data)
        url = data.pop("url", "")
        if url and data.get("project_url") and data["project_url"] != url:
            raise ValueError("url and project_url name different boards; keep url")
        url = url or data.get("project_url", "")
        if not url:
            return data
        found = BOARD_URL.fullmatch(str(url).strip())
        if not found:
            raise ValueError(
                f"url {url!r} is not a board URL; it looks like "
                "https://github.com/orgs/<org>/projects/<number> "
                "(or /users/<login>/ for a personal board)")
        derived = {"owner": found["owner"],
                   "owner_type": "org" if found["kind"] == "orgs" else "user",
                   "project": found["number"]}
        for key, value in derived.items():
            stated = data.get(key)
            if stated not in (None, "") and str(stated).lower() != value.lower():
                raise ValueError(f"{key} = {stated!r} disagrees with url {url!r}; "
                                 f"the url alone is enough, so drop {key}")
        whose = str(data.get("repo", "")).partition("/")[0]
        if whose and whose.lower() != found["owner"].lower():
            raise ValueError(
                f"url {url!r} is {found['owner']}'s board, but repo is "
                f"{data['repo']!r}; a board only holds its own owner's issues")
        url = f"https://github.com/{found['kind']}/{found['owner']}/projects/{found['number']}"
        return {**data, **derived, "project_url": url}

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

    @field_validator("views", "labels", mode="before")
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
    def legacy_identity(self) -> bool:
        """Named by `owner` and `project` rather than by `[board] url`."""
        return bool(self.project) and not self.project_url

    @property
    def url(self) -> str:
        if self.project_url:
            return self.project_url
        kind = "orgs" if self.owner_type == "org" else "users"
        return f"https://github.com/{kind}/{self.owner}/projects/{self.project}"

    def path(self, relative: str) -> pathlib.Path:
        """A configured path, resolved and confined to the repository.

        `mirror_path` and its kind come from a file in the repo, so they are
        as trusted as the repo is — but a `../` in one would have boardkit
        write outside it, which no setting here is for. Refused rather than
        clipped: a path that does not mean what it says should be corrected,
        not silently reinterpreted.
        """
        root = self.root.resolve()
        full = (root / relative).resolve()
        if full != root and root not in full.parents:
            raise BoardError(
                f"{relative!r} resolves outside the repository ({full}); "
                f"paths in {self.where} have to stay under {root}")
        return full

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


def from_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Scalar settings overridden for this machine or this run.

    Named `BOARDKIT_<SETTING>`, upper or lower case. Pydantic does the
    coercion and the complaining, so a bad value is reported like a bad line
    in the file rather than silently ignored — being unable to see an
    environment variable is exactly when a silent default is worst.
    """
    source: Mapping[str, str] = os.environ if environ is None else environ
    scalars = {name for name, field in Config.model_fields.items()
               if name not in {"root", "source"}
               and field.annotation in (str, int, bool)}
    found = {}
    for key, value in source.items():
        if not key.startswith(ENV_PREFIX):
            continue
        setting = key[len(ENV_PREFIX):].lower()
        if setting in scalars:
            found[setting] = value
    return found


def presets() -> tuple[str, ...]:
    return tuple(sorted(path.stem for path in PRESETS.glob("*.toml")))


def preset_file(name: str) -> dict:
    """A preset as written: its `[board]` table and its browser checklist."""
    path = PRESETS / f"{PRESET_ALIASES.get(name, name)}.toml"
    if not name or not path.is_file():
        raise BoardError(f"no preset {name!r}; boardkit has "
                         f"{', '.join(presets()) or 'none'}")
    return tomllib.loads(path.read_text(encoding="utf-8"))


def layers(name: str) -> list[dict]:
    """A preset and the ones it extends, the one it builds on first.

    Composition is by `extends`: `pm` names `work`, so a board taking `pm`
    gets work's columns and views and then pm's own on top.
    """
    chain: list[dict] = []
    seen: set[str] = set()
    while name:
        name = PRESET_ALIASES.get(name, name)
        if name in seen:
            raise BoardError(f"preset {name!r} extends itself")
        seen.add(name)
        found = preset_file(name)
        chain.insert(0, found)
        name = found.get("extends", "")
    return chain


def checklist(name: str) -> list[dict]:
    """What the preset and every one under it leave to the browser, in order."""
    return [step for layer in layers(name) for step in layer.get("checklist", [])]


def named_entries(value) -> list[dict]:
    """Fields or views in any of the forms config accepts, as entries with a
    name, so a preset's and a repo's can be matched by it."""
    if isinstance(value, dict):
        return [{"name": name, **spec} for name, spec in value.items()]
    return [{"name": item} if isinstance(item, str) else dict(item)
            for item in value or ()]


def over(base: list[dict], own: list[dict]) -> list[dict]:
    """The preset's entries with the repo's laid over them by name.

    A repo entry that only names something — `fields = ["start"]` — states an
    order, not a shape, so it keeps the preset's definition of it.
    """
    mine = {entry["name"]: entry for entry in own}
    merged = [mine[entry["name"]] if set(mine.get(entry["name"], {})) - {"name"}
              else entry for entry in base]
    return merged + [entry for entry in own
                     if not any(e["name"] == entry["name"] for e in base)]


def with_preset(table: dict, name: str) -> dict:
    """`table` with the named preset's board shape under it, the presets it
    extends included.

    Every setting the repo states wins. Fields and views merge by name; any
    other setting, such as the column order, is the preset's only where the
    repo says nothing. Settings stay wherever the repo wrote them — at the top
    level or under [board] — so lifting [board] never finds one twice.
    """
    shape: dict = {}
    for layer in layers(name):
        for setting, given in layer.get("board", {}).items():
            if setting in NAMED_SETTINGS:
                shape[setting] = over(named_entries(shape.get(setting)),
                                      named_entries(given))
            else:
                shape[setting] = given
    table = dict(table)
    board = dict(table.get("board") or {})
    for setting, given in shape.items():
        home = table if setting in table else board
        if setting in NAMED_SETTINGS:
            home[setting] = over(named_entries(given), named_entries(home.get(setting)))
        elif setting not in home:
            home[setting] = given
    board["preset"] = PRESET_ALIASES.get(name, name)
    table.pop("preset", None)
    table["board"] = board
    return table


def chosen_preset(table: dict, overrides: dict) -> str:
    board = table.get("board")
    if not isinstance(board, dict):
        board = {}
    return overrides.get("preset") or table.get("preset") or board.get("preset") or ""


def load(start: pathlib.Path | None = None, preset: str = "") -> Config:
    """The repo's configuration. `preset` applies one for this run, over
    whatever the file names, as `board admin apply --preset` does."""
    root, table, source = find_config(start)
    overrides = from_env()
    name = preset or chosen_preset(table, overrides)
    if name:
        table = with_preset(table, name)
        overrides.pop("preset", None)
    try:
        return Config(root=root, source=source, **{**table, **overrides})
    except ValidationError as error:
        raise BoardError(describe(error, source, overrides)) from None


def describe(error: ValidationError, source: pathlib.Path,
             overrides: dict[str, str] | None = None) -> str:
    """Pydantic's report as advice: one line per bad setting, no traceback.

    The two common mistakes get their own wording, because "Field required"
    and "Extra inputs are not permitted" do not tell the reader what to type.
    """
    known = ", ".join(sorted(Config.model_fields.keys() - {"root", "source"} | {"url"}))
    overrides = overrides or {}
    lines = []
    for detail in error.errors():
        setting = ".".join(str(part) for part in detail["loc"]) or "<table>"
        # Blaming the file for a value the file does not contain sends the
        # reader to edit the wrong thing, and an environment variable is the
        # harder of the two to notice.
        where = (f" (from {ENV_PREFIX}{setting.upper()})"
                 if setting in overrides else "")
        if detail["type"] == "missing":
            lines.append(f"  missing `{setting}`")
        elif detail["type"] == "extra_forbidden":
            lines.append(f"  unknown boardkit setting `{setting}`")
        elif setting == "<table>":
            # A check across settings, which no one line is to blame for.
            lines.append(f"  {detail['msg'].removeprefix('Value error, ')}")
        else:
            lines.append(f"  {setting}: {detail['msg']}{where}")
    return (
        f"invalid boardkit configuration in {source}:\n"
        + "\n".join(lines)
        + f"\nKnown settings: {known}."
        + f"\nAny of them may be overridden as {ENV_PREFIX}<SETTING>."
    )
