"""Create the board a repository is configured for.

Separated from every other verb because it is the only one that makes things
rather than reading or updating them, and because a half-applied run leaves a
project with some of its fields — which nothing here cleans up. So the whole
plan is checked against what already exists before the first call.

`gh project field-create` accepts four of GitHub's data types: TEXT,
SINGLE_SELECT, DATE and NUMBER. An iteration or multi-select field has to be
added in the web UI.
"""
from __future__ import annotations

from . import auth
from .config import CREATABLE, Config, ProjectField
from .gh import BoardError, gh_json
from .store import Store
from .sync import find_project, parse_fields


def locate(config: Config) -> str:
    """The number of this repo's project, or "" if it does not exist yet.

    Looked up by title rather than read from config, so a second `create` finds
    what the first one made instead of making another.
    """
    if config.project:
        return config.project
    listed = gh_json("project", "list", "--owner", config.owner,
                     "--limit", "100", "--format", "json")
    try:
        return find_project(listed.get("projects", []), config)
    except BoardError:
        return ""


def existing(config: Config, number: str) -> tuple[str, tuple[ProjectField, ...]] | None:
    """The project and its configured fields, or None if there is no number."""
    if not number:
        return None
    project = gh_json("project", "view", number, "--owner", config.owner,
                      "--format", "json")
    fields = gh_json("project", "field-list", number, "--owner", config.owner,
                     "--limit", "50", "--format", "json")
    return project.get("id", ""), parse_fields(fields.get("fields", []), config)


def wanted(config: Config) -> tuple[ProjectField, ...]:
    """The fields config asks for, status first.

    The status field comes from `status_order`, because that is where a repo
    already writes its columns; declaring them twice would let them disagree.
    """
    declared = {field.name: field for field in config.fields}
    status = declared.get(config.status_field) or ProjectField(
        name=config.status_field, data_type="SINGLE_SELECT",
        options=config.status_order)
    rest = [f for f in config.fields if f.name != config.status_field]
    return (status, *rest)


def plan(config: Config, present: tuple[ProjectField, ...]) -> list[ProjectField]:
    """What is still missing. Existing fields are left alone, never edited."""
    have = {field.name for field in present}
    missing = [field for field in wanted(config) if field.name not in have]
    unsupported = [f.name for f in missing if not f.creatable]
    if unsupported:
        raise BoardError(
            f"cannot create {', '.join(unsupported)}: `gh project field-create` "
            f"handles only {', '.join(CREATABLE)}. Add them in the web UI, then "
            f"re-run.")
    return missing


def create(config: Config, dry: bool = False) -> int:
    """Create the project if it has no number, then any field it is missing."""
    auth.require(config, "admin")
    number = locate(config)
    found = existing(config, number)
    present = found[1] if found else ()
    missing = plan(config, present)

    if dry:
        if found:
            print(f"Project {number} exists with {len(present)} configured field(s).")
        else:
            print(f"Would create project {config.name!r} for {config.owner}.")
        for field in missing:
            detail = f" ({', '.join(field.options)})" if field.options else ""
            print(f"  would add {field.name} [{field.data_type}]{detail}")
        if not missing:
            print("  nothing missing.")
        return 0

    if not found:
        made = gh_json("project", "create", "--owner", config.owner,
                       "--title", config.name, "--format", "json")
        number = str(made.get("number", ""))
        print(f"Created project {number}: {made.get('url', '')}")

    for field in missing:
        args = ["project", "field-create", number, "--owner", config.owner,
                "--name", field.name, "--data-type", field.data_type]
        if field.data_type == "SINGLE_SELECT":
            if not field.options:
                raise BoardError(f"`{field.name}` is a single select with no options")
            args += ["--single-select-options", ",".join(field.options)]
        gh_json(*args)
        print(f"  added {field.name} [{field.data_type}]")

    if not missing:
        print("  every configured field already exists.")
    return 0


def link(config: Config) -> int:
    """Attach the project to the repository, so issues can reach it."""
    auth.require(config, "admin")
    number = locate(config)
    if not number:
        raise BoardError("no project to link; run `board admin create` first")
    gh_json("project", "link", number, "--owner", config.owner,
            "--repo", config.repo, "--format", "json")
    print(f"Linked project {number} to {config.repo}")
    return 0
