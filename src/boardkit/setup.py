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


# Commented out, because a repo whose board already exists needs none of it:
# its columns and fields are discovered on the first sync. It is written all
# the same, so that making a board from scratch does not mean finding the
# shape of these tables in the documentation first.
EXAMPLE = """
# To create the board rather than reach one that exists, uncomment and edit,
# then run `board admin apply`. Everything under [admin] is read by that verb
# alone; a board that already exists needs none of it.
# [admin]
# status_order = ["Inbox", "Todo", "In progress", "Done"]
#
# [admin.fields.area]
# data_type = "SINGLE_SELECT"   # or TEXT, NUMBER, DATE, MULTI_SELECT, ITERATION
# options = ["Maps", "Planning", "Infra"]
#
# [[admin.views]]
# name = "Open work"
# layout = "BOARD"
# fields = ["status", "area"]
# filter = "-status:Done"
"""


def rendered(settings: dict) -> str:
    lines = [TABLE]
    for key, value in settings.items():
        lines.append(f'{key} = "{value}"')
    return "\n".join(lines) + "\n" + EXAMPLE


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
    block = rendered(settings)
    # A dedicated file holds the keys at the top level; only pyproject needs
    # the table header.
    body = block if file.name == "pyproject.toml" else block.replace(TABLE + "\n", "")

    if dry:
        print(f"Would write to {file.relative_to(where)}:\n\n{body}")
        return 0
    with file.open("a", encoding="utf-8") as handle:
        handle.write(("\n" if file.exists() and file.stat().st_size else "") + body)
    print(f"Wrote {file.relative_to(where)}:\n\n{body}")
    print("Next: `board admin apply` to make the board, or `board sync` if it "
          "already exists.")
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
