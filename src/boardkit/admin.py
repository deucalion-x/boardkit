"""Create the board a repository is configured for.

Separated from every other verb because it is the only one that makes things
rather than reading or updating them, and because a half-applied run leaves a
project with some of its fields — which nothing here cleans up. So the whole
plan is checked against what already exists before the first call.

Fields are created through the API rather than `gh project field-create`,
which handles only four of the six types GitHub can actually create. Iteration
and multi-select fields work here because the limit was the wrapper's.
"""
from __future__ import annotations

import datetime

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
    # Every data type config can name is creatable today, so this guard is
    # quiet. It stays because the list is GitHub's to change, and a type that
    # cannot be made should be refused before the project is touched.
    unsupported = [f.name for f in missing if not f.creatable]
    if unsupported:
        raise BoardError(
            f"cannot create {', '.join(unsupported)}: boardkit makes only "
            f"{', '.join(CREATABLE)} fields. Add them in the web UI, then re-run.")
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


def creation_query(project_id: str, field: ProjectField) -> str:
    """The `createProjectV2Field` mutation for one field.

    Sent directly rather than through `gh project field-create`, which handles
    only four of the six creatable types. The API itself creates all of them,
    so the limit was the wrapper's and not GitHub's.
    """
    body = (f'projectId: "{project_id}", dataType: {field.data_type}, '
            f'name: "{field.display}"')
    if field.data_type in {"SINGLE_SELECT", "MULTI_SELECT"}:
        if not field.options:
            raise BoardError(f"`{field.name}` is a {field.data_type.lower()} "
                             f"with no options")
        rows = ", ".join(
            "{" + f'name: "{name}", color: {DEFAULT_COLOR}, description: ""' + "}"
            for name in field.options)
        key = "singleSelectOptions" if field.data_type == "SINGLE_SELECT" \
            else "multiSelectOptions"
        body += f", {key}: [{rows}]"
    if field.data_type == "ITERATION":
        body += f", iterationConfiguration: {iterations_for(field)}"
    return ("mutation { createProjectV2Field(input: {" + body +
            " }) { projectV2Field { ... on ProjectV2FieldCommon { id name } } } }")


def iterations_for(field: ProjectField) -> str:
    """`iterations` in config is a count; GitHub wants the dated entries.

    Each one carries its own start, length and title, so a count has to be
    expanded into consecutive periods — which is also what the web UI does
    when you say "create 4 iterations".
    """
    setup = field.iteration
    start = datetime.date.fromisoformat(
        setup.start_date or datetime.date.today().isoformat())
    rows = []
    for index in range(setup.iterations):
        begins = start + datetime.timedelta(days=setup.duration * index)
        rows.append("{" + f'startDate: "{begins.isoformat()}", '
                    f'duration: {setup.duration}, '
                    f'title: "{field.display} {index + 1}"' + "}")
    return ("{" + f'startDate: "{start.isoformat()}", duration: {setup.duration}, '
            f"iterations: [{', '.join(rows)}]" + "}")


def add_field(project_id: str, field: ProjectField) -> None:
    run_json(["gh", "api", "graphql", "-f", f"query={creation_query(project_id, field)}"])


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
    """Bring a single select's options into line. `gh` has no verb for this.

    The mutation carries the full list, which is why `align` never omits an
    option that already exists."""
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

    project_id = found[0] if found else ""
    for field in missing:
        add_field(project_id, field)
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
