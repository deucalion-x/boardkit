"""Start a board without writing configuration first.

`board init` asks GitHub what it can — which repository this is, who owns it —
defaults the rest, and writes the three lines that are genuinely a decision.
Everything else about a board's shape (its columns, its fields, their options)
is discovered on the first sync, so a repo has no reason to restate it.

The models carry the defaults and the vocabulary, so `board schema` can hand an
agent the full option set and let it drive a richer setup without this file
growing a flag per field.
"""
from __future__ import annotations

import json
import pathlib

from .config import CONFIG_FILES, Config, ProjectField
from .gh import BoardError, git, gh_json

TABLE = "[tool.boardkit]"


def detect() -> dict:
    """Owner, repo and a default title, from the repository we are standing in."""
    found = gh_json("repo", "view", "--json", "nameWithOwner,owner,name")
    owner = found["owner"]["login"]
    return {"owner": owner,
            "owner_type": "org" if found["owner"].get("__typename") == "Organization"
            else "user",
            "repo": found["nameWithOwner"],
            "project_name": found["name"]}


def root() -> pathlib.Path:
    return pathlib.Path(git("rev-parse", "--show-toplevel"))


TEMPLATE = pathlib.Path(__file__).with_name("templates") / "board-template.toml"


def board_exists(settings: dict) -> bool:
    """Whether a project by this title is already there, so `init` writes a
    config that reads it rather than one that would create a second."""
    try:
        listed = gh_json("project", "list", "--owner", settings["owner"],
                         "--limit", "100", "--format", "json")
    except BoardError:
        return False
    return any(entry.get("title") == settings["project_name"]
               for entry in listed.get("projects", []))


def rendered(settings: dict, exists: bool = True, nested: bool = False) -> str:
    """The starter config for this repo.

    One template, with the columns commented out when the board already exists
    — its shape is discovered on sync — and left ready to run when it does not.

    `nested` is for `pyproject.toml`, where every table has to sit under
    `[tool.boardkit]`: a bare `[board]` there is a different table that nothing
    reads, which is a silent way to lose the whole section.
    """
    import string
    text = string.Template(TEMPLATE.read_text(encoding="utf-8")).safe_substitute(
        **{key: str(value) for key, value in settings.items()})
    if nested:
        text = text.replace("[board", f"[{TABLE.strip('[]')}.board")
        text = text.replace("[[board", f"[[{TABLE.strip('[]')}.board")
        head, _, rest = text.partition("\n[")
        text = f"{TABLE}\n{head.lstrip()}\n[{rest}"
    return text


def target(where: pathlib.Path) -> pathlib.Path:
    """Where the table goes: pyproject.toml if the repo has one, else its own
    file. A dedicated file is the only option for a repo with no packaging."""
    pyproject = where / "pyproject.toml"
    return pyproject if pyproject.exists() else where / "boardkit.toml"


def init(title: str = "", dry: bool = False) -> int:
    where = root()
    for name in CONFIG_FILES:
        if (where / name).exists() and "boardkit" in (where / name).read_text():
            raise BoardError(
                f"{name} already configures boardkit. Edit it, or delete the "
                f"table to start over.")
    settings = detect()
    if title:
        settings["project_name"] = title
    file = target(where)
    exists = board_exists(settings)
    body = rendered(settings, exists, nested=file.name == "pyproject.toml")

    if dry:
        print(f"Would write to {file.relative_to(where)}:\n\n{body}")
        return 0
    with file.open("a", encoding="utf-8") as handle:
        handle.write(("\n" if file.exists() and file.stat().st_size else "") + body)
    print(f"Wrote {file.relative_to(where)}:\n\n{body}")

    # Carry on into the board itself: the config just written says everything
    # `apply` needs, and stopping here would only ask for one more command.
    # `apply` adds what is missing and changes nothing that exists, so running
    # it against a board that is already there is safe.
    from . import admin, sync
    from .config import load
    from .store import Store
    config = load(where)
    store = Store(config)
    admin.apply(config)
    sync.pull(config, store)
    # From the snapshot, not config: the number is discovered, and config has
    # deliberately never been told it.
    number = store.state().project_number
    kind = "orgs" if config.owner_type == "org" else "users"
    print(f"\nReady: https://github.com/{kind}/{config.owner}/projects/{number}\n"
          f"Add a column or a field by editing [board] and running "
          f"`board admin apply` again; it never removes anything.")
    return 0


def schema() -> int:
    """The configuration's full shape, for an agent to fill in rather than guess.

    Pydantic already describes every setting, its type and its default, so this
    is the model rather than a document that could disagree with it.
    """
    payload = {"config": Config.model_json_schema(),
               "field": ProjectField.model_json_schema()}
    print(json.dumps(payload, indent=2))
    return 0
