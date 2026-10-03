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
from .gh import BoardError, gh, gh_json, run_json
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


def align(wanted: ProjectField, present: ProjectField) -> list[dict] | None:
    """The option list to send so `present` offers everything `wanted` asks.

    Options are matched by name and keep their id, so items stay where they
    are. Nothing is ever dropped: deleting an option unsets every item in it,
    and that is not a side effect `create` should have. An option the board
    has but config does not keeps its place, at the end.
    """
    if wanted.data_type != "SINGLE_SELECT" or not wanted.options:
        return None
    if list(present.options) == list(wanted.options):
        return None
    known = dict(present.option_ids)
    extra = [name for name in present.options if name not in wanted.options]
    ordered = [*wanted.options, *extra]
    return [{"name": name, **({"id": known[name]} if name in known else {})}
            for name in ordered]


DEFAULT_COLOR = "GRAY"


def decorated(config: Config, number: str, field: ProjectField,
              options: list[dict]) -> list[dict]:
    """Options with the colour and description each one already has.

    `updateProjectV2Field` requires both on every entry, and
    `gh project field-list` reports neither, so the current values have to be
    read back or the mutation would repaint the whole field.
    """
    kind = "organization" if config.owner_type == "org" else "user"
    query = (f'{{ {kind}(login: "{config.owner}") {{ projectV2(number: {number}) '
             f'{{ field(name: "{field.display}") {{ '
             "... on ProjectV2SingleSelectField "
             "{ options { id name color description } } } } } }")
    payload = run_json(["gh", "api", "graphql", "-f", f"query={query}"])
    node = payload["data"][kind]["projectV2"]["field"] or {}
    known = {option["id"]: option for option in node.get("options", [])}
    dressed = []
    for option in options:
        current = known.get(option.get("id", ""), {})
        dressed.append({**option,
                        "color": current.get("color") or DEFAULT_COLOR,
                        "description": current.get("description") or ""})
    return dressed


def rendered(options: list[dict]) -> str:
    parts = []
    for option in options:
        identity = f'id: "{option["id"]}", ' if "id" in option else ""
        parts.append("{" + identity + f'name: "{option["name"]}", '
                     f'color: {option["color"]}, '
                     f'description: "{option["description"]}"' + "}")
    return ", ".join(parts)


def apply_options(config: Config, number: str, field: ProjectField,
                  options: list[dict]) -> None:
    """`gh` has no verb for a single select's options, so this is the one
    mutation boardkit sends directly. It carries the full list, which is why
    `align` never omits an option that already exists."""
    full = decorated(config, number, field, options)
    query = (
        "mutation { updateProjectV2Field(input: {"
        f' fieldId: "{field.id}", singleSelectOptions: [{rendered(full)}]'
        " }) { projectV2Field { ... on ProjectV2SingleSelectField { id } } } }")
    run_json(["gh", "api", "graphql", "-f", f"query={query}"])


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
            print(f"  would add {field.display} [{field.data_type}]{detail}")
        if not missing:
            print("  nothing missing.")
        return 0

    if not found:
        made = gh_json("project", "create", "--owner", config.owner,
                       "--title", config.name, "--format", "json")
        number = str(made.get("number", ""))
        print(f"Created project {number}: {made.get('url', '')}")
        # GitHub seeds a new project with its own Status field, so what is
        # missing has to be recomputed against what it actually arrived with.
        found = existing(config, number)
        present = found[1] if found else ()
        missing = plan(config, present)

    standing = {field.name: field for field in present}
    for field in wanted(config):
        current = standing.get(field.name)
        options = align(field, current) if current else None
        if options:
            apply_options(config, number, current, options)
            print(f"  aligned {current.display} options")

    for field in missing:
        args = ["project", "field-create", number, "--owner", config.owner,
                "--name", field.display, "--data-type", field.data_type,
                "--format", "json"]
        if field.data_type == "SINGLE_SELECT":
            if not field.options:
                raise BoardError(f"`{field.name}` is a single select with no options")
            args += ["--single-select-options", ",".join(field.options)]
        gh_json(*args)
        print(f"  added {field.display} [{field.data_type}]")

    if not missing:
        print("  every configured field already exists.")
    return 0


def link(config: Config) -> int:
    """Attach the project to the repository, so issues can reach it."""
    auth.require(config, "admin")
    number = locate(config)
    if not number:
        raise BoardError("no project to link; run `board admin create` first")
    # `link` is the one project verb with no --format flag; it prints a line.
    gh("project", "link", number, "--owner", config.owner, "--repo", config.repo)
    print(f"Linked project {number} to {config.repo}")
    return 0
