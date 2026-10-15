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
import re

from .config import CONFIG_FILES, Config, ProjectField, preset_file
from .gh import BoardError, gh_json, git

TABLE = "[tool.boardkit]"


def detect() -> dict:
    """Owner, repo and a default title, from the repository we are standing in."""
    found = gh_json("repo", "view", "--json", "nameWithOwner,owner,name")
    owner = found["owner"]["login"]
    # `gh repo view` names the owner but not its kind, so ask the users
    # endpoint, which answers for organisations too.
    kind = gh_json("api", f"users/{owner}").get("type")
    return {"owner": owner,
            "owner_type": "org" if kind == "Organization" else "user",
            "repo": found["nameWithOwner"],
            "project_name": found["name"]}


def root() -> pathlib.Path:
    return pathlib.Path(git("rev-parse", "--show-toplevel"))


TEMPLATE = pathlib.Path(__file__).with_name("templates") / "board-template.toml"
# A GitHub App's client ID: `Iv1.` and hex for older apps, `Iv23` and
# alphanumerics for newer ones. Checked so a typo, or a pasted secret, is
# refused before it is written into a committed file.
CLIENT_ID = re.compile(r"Iv[0-9A-Za-z.]{8,30}")
# What a portfolio adds to the starter config: which repositories it covers,
# which no preset can say. Left empty for the person to fill in.
PORTFOLIO = """
# The repositories whose Initiatives this portfolio shows, by name within
# $owner. `board portfolio sync` adds them here and copies their fields; the
# Action in .github/workflows/portfolio-sync.yml runs it every day.
[portfolio]
repos = []
"""
WORKFLOW = pathlib.Path(__file__).with_name("templates") / "portfolio-sync.yml"
WORKFLOW_PATH = pathlib.Path(".github/workflows/portfolio-sync.yml")


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


def rendered(settings: dict, exists: bool = True, nested: bool = False,
             preset: str = "") -> str:
    """The starter config for this repo.

    One template, with the columns commented out when the board already exists
    — its shape is discovered on sync — and left ready to run when it does not.

    `nested` is for `pyproject.toml`, where every table has to sit under
    `[tool.boardkit]`: a bare `[board]` there is a different table that nothing
    reads, which is a silent way to lose the whole section.
    """
    import string
    # Refused before anything is written, rather than by the first load.
    preset_file(preset or "work")
    settings = {"owner_type": "user", **settings, "preset": preset or "work"}
    source = TEMPLATE.read_text(encoding="utf-8")
    if settings["preset"] == "portfolio":
        source += PORTFOLIO
    text = string.Template(source).safe_substitute(
        **{key: str(value) for key, value in settings.items()})
    if settings.get("app_client_id"):
        # Beside `repo`, where `board auth app create` records it too.
        text = re.sub(r"^(repo = .*)$",
                      lambda line: f'{line[1]}\napp_client_id = "{settings["app_client_id"]}"',
                      text, count=1, flags=re.MULTILINE)
    if nested:
        text = text.replace("[board", f"[{TABLE.strip('[]')}.board")
        text = text.replace("[[board", f"[[{TABLE.strip('[]')}.board")
        text = text.replace("\n[portfolio]", f"\n[{TABLE.strip('[]')}.portfolio]")
        head, _, rest = text.partition("\n[")
        text = f"{TABLE}\n{head.lstrip()}\n[{rest}"
    return text


def workflow() -> str:
    """The daily portfolio sync, pinned to the boardkit that wrote it.

    A release installs as itself. A development build has no tag to name, so
    it takes the default branch; pin it to a tag in the file once there is one.
    """
    from . import __version__
    ref = (f"v{__version__}" if re.fullmatch(r"\d+\.\d+\.\d+", __version__)
           else "main")
    return WORKFLOW.read_text(encoding="utf-8").replace("BOARDKIT_REF", ref)


def target(where: pathlib.Path) -> pathlib.Path:
    """Where the table goes: pyproject.toml if the repo has one, else its own
    file. A dedicated file is the only option for a repo with no packaging."""
    pyproject = where / "pyproject.toml"
    return pyproject if pyproject.exists() else where / "boardkit.toml"


def init(title: str = "", dry: bool = False, preset: str = "", app: str = "") -> int:
    """`app` is the client ID of a GitHub App the repository signs in
    through, for an organisation that shares one app across its boards.
    Without it the config names no app: `board auth app create` makes one for
    this repository alone, or `board auth setup` stores a token."""
    if preset:
        preset_file(preset)
    if app and not CLIENT_ID.fullmatch(app):
        raise BoardError(
            f"{app!r} is not a GitHub App client ID, which looks like "
            f"Iv23li… or Iv1.…; it is on the app's settings page. Nothing was written.")
    where = root()
    for name in CONFIG_FILES:
        if (where / name).exists() and "boardkit" in (where / name).read_text():
            raise BoardError(
                f"{name} already configures boardkit. Edit it, or delete the "
                f"table to start over.")
    settings = detect()
    if title:
        settings["project_name"] = title
    if app:
        settings["app_client_id"] = app
    file = target(where)
    exists = board_exists(settings)
    body = rendered(settings, exists, nested=file.name == "pyproject.toml",
                    preset=preset)

    if dry:
        print(f"Would write to {file.relative_to(where)}:\n\n{body}")
        return 0
    with file.open("a", encoding="utf-8") as handle:
        handle.write(("\n" if file.exists() and file.stat().st_size else "") + body)
    print(f"Wrote {file.relative_to(where)}:\n\n{body}")
    if preset == "portfolio":
        action = where / WORKFLOW_PATH
        if action.exists():
            print(f"{WORKFLOW_PATH} exists; left as it is.")
        else:
            action.parent.mkdir(parents=True, exist_ok=True)
            action.write_text(workflow(), encoding="utf-8")
            print(f"Wrote {WORKFLOW_PATH}: `board portfolio sync` every day. "
                  f"It needs the BOARDKIT_TOKEN secret its header describes.")

    # Carry on into the board itself: the config just written says everything
    # `apply` needs, and stopping here would only ask for one more command.
    # `apply` adds what is missing and changes nothing that exists, so running
    # it against a board that is already there is safe.
    from . import admin, sync
    from .config import load
    from .store import Store
    config = load(where)
    store = Store(config)
    admin.apply(config, browser=False)
    sync.pull(config, store)
    # From the snapshot, not config: the number is discovered, and config has
    # deliberately never been told it.
    number = store.state().project_number
    kind = "orgs" if config.owner_type == "org" else "users"
    print(f"\nReady: https://github.com/{kind}/{config.owner}/projects/{number}\n"
          f"Add a column or a field by editing [board] and running "
          f"`board admin apply` again; it never removes anything.")
    if app:
        print(f"Signs in through app {app}: install it on {config.repo}, then "
              f"`board auth login` unless this machine is already signed in to it.")
    if config.portfolio is not None:
        print(f"List the repositories under [portfolio] in {file.name}, then "
              f"`board portfolio sync` fills the board.")
    if config.preset:
        print()
        admin.checklist(config, number)
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
