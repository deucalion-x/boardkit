"""Create the board a repository is configured for.

Separated from every other verb because it is the only one that changes the
board's shape rather than its contents, and because a half-applied run leaves
a project with some of its fields — which nothing here cleans up. So the whole
plan is checked against what already exists before the first call.

`apply` converges: run it on nothing and it builds the board, run it again and
it adds what config has gained since. It never deletes, because deleting a
field or an option discards the values every item holds in it.

Fields are created through the API rather than `gh project field-create`,
which handles only four of the six types GitHub can actually create. Iteration
and multi-select fields work here because the limit was the wrapper's.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass

from . import auth, credential
from .config import CREATABLE, Config, ProjectField, preset_file
from .gh import BoardError, gh, gh_json, quoted, run_json
from .sync import BUILT_IN, fetch_fields, find_project, key, parse_fields
from .trail import note


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
    return (project.get("id", ""),
            parse_fields(fetch_fields(config, number), config))


def wanted(config: Config) -> tuple[ProjectField, ...]:
    """The fields config asks for, status first.

    The status field comes from `status_order`, because that is where a repo
    already writes its columns; declaring them twice would let them disagree.
    """
    declared = {field.name: field for field in config.fields}
    status = declared.get(config.status_field) or ProjectField(
        name=config.status_field, data_type="SINGLE_SELECT",
        # A repo that names no columns gets the generic ones rather than
        # whatever GitHub seeds, so every board starts meaning the same thing.
        options=config.status_order or tuple(config.default_status),
        descriptions=config.status_descriptions or config.default_status)
    rest = [f for f in config.fields if f.name != config.status_field]
    if config.blocked_field and not any(f.name == config.blocked_field for f in rest):
        rest.append(ProjectField(name=config.blocked_field, data_type="TEXT"))
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

    Matching ignores case, because GitHub seeds a new project with `In
    Progress` and a config asking for `In progress` was getting a second
    column rather than the one it meant — two columns one keystroke apart,
    with the items split between them. Keeping the id means the option is
    renamed to the spelling config asked for rather than replaced.

    A board that already carries both keeps both: the one config names is
    renamed, and the other stays at the end rather than being deleted under
    the items sitting in it. `duplicates` is what says so out loud.
    """
    if wanted.data_type != "SINGLE_SELECT" or not wanted.options:
        return None
    if list(present.options) == list(wanted.options):
        return None
    ids = dict(present.option_ids)
    # An exactly-spelled option is the one to rename; only when there is none
    # does a differently-cased one stand in for it.
    chosen: dict[str, str] = {}
    for name in wanted.options:
        if name in ids:
            chosen[name] = ids[name]
    for name in wanted.options:
        if name in chosen:
            continue
        match = next((have for have in present.options
                      if have.lower() == name.lower() and ids.get(have) not in chosen.values()),
                     None)
        if match:
            chosen[name] = ids[match]
    taken = set(chosen.values())
    extra = [name for name in present.options if ids.get(name) not in taken]
    ordered = [*wanted.options, *extra]
    return [{"name": name, **({"id": chosen[name]} if name in chosen else
                              {"id": ids[name]} if name in ids else {})}
            for name in ordered]


def describe(present: ProjectField, options: list[dict]) -> list[str]:
    """What sending `options` would do to `present`, one change per line.

    The dry run prints this so it reports the renames and additions a real
    apply makes, rather than staying silent about them.
    """
    names = {identity: name for name, identity in present.option_ids.items()}
    lines = []
    for option in options:
        before = names.get(option.get("id", ""))
        if before is None:
            lines.append(f"add option {option['name']!r}")
        elif before != option["name"]:
            lines.append(f"rename option {before!r} to {option['name']!r}")
    if not lines:
        lines.append("reorder options to " + ", ".join(o["name"] for o in options))
    return lines


def duplicates(present: ProjectField) -> list[tuple[str, ...]]:
    """Options on the board that differ only in case.

    Two such columns split the same work in a way nobody notices from the
    board itself, so `apply` says which they are. They are not merged here:
    moving items between columns is a change to contents, and this verb
    changes shape.
    """
    seen: dict[str, list[str]] = {}
    for name in present.options:
        seen.setdefault(name.lower(), []).append(name)
    return [tuple(names) for names in seen.values() if len(names) > 1]


DEFAULT_COLOR = "GRAY"


def creation_query(project_id: str, field: ProjectField) -> str:
    """The `createProjectV2Field` mutation for one field.

    Sent directly rather than through `gh project field-create`, which handles
    only four of the six creatable types. The API itself creates all of them,
    so the limit was the wrapper's and not GitHub's.
    """
    body = (f"projectId: {quoted(project_id)}, dataType: {field.data_type}, "
            f"name: {quoted(field.display)}")
    if field.data_type in {"SINGLE_SELECT", "MULTI_SELECT"}:
        if not field.options:
            raise BoardError(f"`{field.name}` is a {field.data_type.lower()} "
                             f"with no options")
        rows = ", ".join(
            "{" + f"name: {quoted(name)}, color: {DEFAULT_COLOR}, "
            'description: ""' + "}"
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
        setup.start_date or datetime.datetime.now(datetime.UTC).date().isoformat())
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
    """Options with their colour and description.

    `updateProjectV2Field` requires both on every entry, and
    `gh project field-list` reports neither, so the current values have to be
    read back or the mutation would repaint the whole field. A description
    config declares wins over the one on the board: that is the point of
    declaring it.
    """
    kind = "organization" if config.owner_type == "org" else "user"
    query = (f"{{ {kind}(login: {quoted(config.owner)}) "
             f"{{ projectV2(number: {number}) "
             f"{{ field(name: {quoted(field.display)}) {{ "
             "... on ProjectV2SingleSelectField "
             "{ options { id name color description } } } } } }")
    payload = run_json(["gh", "api", "graphql", "-f", f"query={query}"])
    node = payload["data"][kind]["projectV2"]["field"] or {}
    known = {option["id"]: option for option in node.get("options", [])}
    dressed = []
    for option in options:
        current = known.get(option.get("id", ""), {})
        declared = field.descriptions.get(option["name"], "")
        dressed.append({**option,
                        "color": current.get("color") or DEFAULT_COLOR,
                        "description": declared or current.get("description") or ""})
    return dressed


def rendered(options: list[dict]) -> str:
    parts = []
    for option in options:
        identity = f"id: {quoted(option['id'])}, " if "id" in option else ""
        parts.append("{" + identity + f"name: {quoted(option['name'])}, "
                     f"color: {option['color']}, "
                     f"description: {quoted(option['description'])}" + "}")
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


def apply(config: Config, dry: bool = False, reconcile: bool = False,
          prune: bool = False, browser: bool = True) -> int:
    """Bring the board into line with what this repo declares.

    Named for what it does rather than for the first time it runs: it creates
    the project when there is none, and on every run after that it adds the
    fields and options that are missing. `reconcile` also makes declared view
    filters and layouts win over an existing view for this run.

    The one thing it deletes, and only when `prune` asks for it, is a saved
    tab config does not name. Fields, options and columns are never deleted:
    see `apply_views` for why a view is the only safe exception.
    """
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
        standing = {field.name: field for field in present}
        realigned = False
        for field in wanted(config):
            current = standing.get(field.name)
            if current is None:
                continue
            options = align(field, current)
            if options:
                realigned = True
                for line in describe(current, options):
                    print(f"  would {line} in {current.display}")
        saved = existing_views(config, number) if number else {}
        for view in config.views:
            if view.name not in saved:
                print(f"  would add view {view.name} [{view.layout}]")
            elif reconcile and (saved[view.name].filter != view.filter or
                                saved[view.name].layout != view.layout):
                print(f"  would reconcile view {view.name} "
                      f"[{view.layout}] filter {view.filter!r}")
        stray = undeclared(config, saved) if prune else []
        for name in stray:
            print(f"  would delete view {name}, which config does not name")
        views_match = not stray and all(
            view.name in saved and (
                not reconcile or (
                    saved[view.name].filter == view.filter and
                    saved[view.name].layout == view.layout))
            for view in config.views)
        if not missing and not realigned and views_match:
            print("  nothing missing.")
        return 0

    created = not found
    if created:
        try:
            credential.elevated(f"creating project {config.name!r}")
        except credential.Missing as refused:
            raise BoardError(str(refused)) from None
        made = gh_json("project", "create", "--owner", config.owner,
                       "--title", config.name, "--format", "json")
        number = str(made.get("number", ""))
        print(f"Created project {number}: {made.get('url', '')}")
        note("created_project", project=number, title=config.name)
        # GitHub seeds a new project with its own Status field, so what is
        # missing has to be recomputed against what it actually arrived with.
        found = existing(config, number)
        present = found[1] if found else ()
        missing = plan(config, present)

    standing = {field.name: field for field in present}
    for field in wanted(config):
        current = standing.get(field.name)
        # A field config declares and the board lacks is in `missing`, and is
        # created below. There is nothing to align here.
        if current is None:
            continue
        options = align(field, current)
        if options:
            apply_options(config, number, current, options)
            print(f"  aligned {current.display} options")
            note("aligned_options", field=current.name,
                    options=[o["name"] for o in options])
        for pair in duplicates(current):
            print(f"  warning: {current.display} has {' and '.join(repr(n) for n in pair)}, "
                  f"which differ only in case. Items are split between them; "
                  f"move them in the browser and delete the empty one.")
            note("duplicate_options", field=current.name, options=list(pair))

    project_id = found[0] if found else ""
    for field in missing:
        add_field(project_id, field)
        print(f"  added {field.display} [{field.data_type}]")
        note("created_field", field=field.display, data_type=field.data_type,
                options=list(field.options))

    if not missing:
        print("  every configured field already exists.")
    raw = fetch_fields(config, number)
    apply_views(config, number, project_id or (found[0] if found else ""),
                parse_fields(raw, config),
                reconcile=reconcile, created=created, prune=prune,
                built_in={key(entry.get("name", "")): entry["id"] for entry in raw
                          if entry.get("id") and key(entry.get("name", "")) in BUILT_IN})
    # Last, so it is the last thing read. `init` asks for it after its own
    # closing lines instead, for the same reason.
    if config.preset and browser:
        print()
        checklist(config, number)
    return 0


def checklist(config: Config, number: str = "") -> int:
    """What the preset needs that GitHub's API cannot set, to do in the
    browser, each step with a link to where it is done.

    Printed at the end of every apply, because nothing can tell from the API
    whether it was done; and on request, for whoever does it later. An agent
    that runs apply is not the one who can click, so the wording is meant to
    be handed on as it stands.
    """
    if not config.preset:
        print("This board names no preset, so there is nothing boardkit knows "
              "to be left for the browser.")
        return 0
    steps = preset_file(config.preset).get("checklist", [])
    if not steps:
        return 0
    number = number or config.project
    kind = "orgs" if config.owner_type == "org" else "users"
    base = (f"https://github.com/{kind}/{config.owner}/projects/{number}"
            if number else "")
    views: dict[str, ExistingView] = {}
    if base:
        try:
            views = existing_views(config, number)
        except (BoardError, KeyError, TypeError):
            views = {}  # Links to the views are a help; the steps stand without them.
    print(f"Finish the {config.preset} board in the browser; GitHub's API cannot "
          f"set these:")
    for index, step in enumerate(steps, 1):
        if "view" in step:
            what = f"{step['view']} view"
            found = views.get(step["view"])
            link = f"{base}/views/{found.number}" if base and found and found.number else base
        else:
            what = "Workflows" if step.get("page") == "workflows" else step.get("page", "")
            link = f"{base}/{step['page']}" if base and step.get("page") else base
        print(f"  {index}. {what}: {step['do']}")
        if link:
            print(f"     {link}")
    print("`board admin checklist` prints this again.")
    return 0


@dataclass(frozen=True)
class ExistingView:
    id: str
    filter: str = ""
    layout: str = ""
    # The tab's number in its URL, `.../projects/4/views/2`.
    number: int = 0


def existing_views(config: Config, number: str) -> dict[str, ExistingView]:
    """Saved tabs by name, with the filter explicit reconciliation needs."""
    kind = "organization" if config.owner_type == "org" else "user"
    query = (f"{{ {kind}(login: {quoted(config.owner)}) "
             f"{{ projectV2(number: {number}) "
             f"{{ views(first: {config.page_size}) "
             "{ nodes { id name filter layout number } } } } }")
    payload = run_json(["gh", "api", "graphql", "-f", f"query={query}"])
    nodes = payload["data"][kind]["projectV2"]["views"]["nodes"]
    return {node["name"]: ExistingView(
                node["id"], node.get("filter") or "",
                (node.get("layout") or "").removesuffix("_LAYOUT"),
                node.get("number") or 0)
            for node in nodes if node}


def visible(view, field_ids: dict[str, str]) -> list[str]:
    """The ids of the fields a view shows, by config's name or any spelling
    of GitHub's — "parent issue" finds `Parent issue`. A name that matches
    nothing is said, not dropped: the view would otherwise just lack it."""
    chosen = []
    for name in view.fields:
        found = field_ids.get(name) or field_ids.get(key(name))
        if found:
            chosen.append(found)
        else:
            print(f"  warning: view {view.name} names a field {name!r} the board "
                  f"does not have; it is left out")
    return chosen


def make_view(project_id: str, view, field_ids: dict[str, str]) -> str:
    """Create one saved tab and return its id.

    `createProjectV2View` takes the name, the layout and which fields are
    visible. It does not take a filter — that is a second mutation — and it
    takes no grouping at all, which stays a thing to set in the web UI.
    """
    shown = ""
    chosen = visible(view, field_ids)
    if chosen:
        listed = ", ".join(f'"{identity}"' for identity in chosen)
        shown = f", configuration: {{visibleFieldIds: [{listed}]}}"
    query = ("mutation { createProjectV2View(input: {"
             f" projectId: {quoted(project_id)}, name: {quoted(view.name)},"
             f" layout: {view.layout}_LAYOUT{shown}"
             " }) { projectV2View { id } } }")
    payload = run_json(["gh", "api", "graphql", "-f", f"query={query}"])
    return payload["data"]["createProjectV2View"]["projectV2View"]["id"]


def filter_view(view_id: str, expression: str) -> None:
    query = ("mutation { updateProjectV2View(input: {"
             f" viewId: {quoted(view_id)}, filter: {quoted(expression)}"
             " }) { projectV2View { id } } }")
    run_json(["gh", "api", "graphql", "-f", f"query={query}"])


def configure_view(view_id: str, view, field_ids: dict[str, str]) -> None:
    """Turn GitHub's empty starter view into the template's first view."""
    chosen = visible(view, field_ids)
    shown = ""
    if chosen:
        listed = ", ".join(f'"{identity}"' for identity in chosen)
        shown = f", configuration: {{visibleFieldIds: [{listed}]}}"
    query = ("mutation { updateProjectV2View(input: {"
             f" viewId: {quoted(view_id)}, name: {quoted(view.name)},"
             f" layout: {view.layout}_LAYOUT, filter: {quoted(view.filter)}"
             f"{shown}"
             " }) { projectV2View { id } } }")
    run_json(["gh", "api", "graphql", "-f", f"query={query}"])


def delete_view(view_id: str) -> None:
    """Remove one saved tab. One way: GitHub has no undo for this."""
    query = ("mutation { deleteProjectV2View(input: {"
             f" viewId: {quoted(view_id)}"
             " }) { clientMutationId } }")
    run_json(["gh", "api", "graphql", "-f", f"query={query}"])


def undeclared(config: Config, standing: dict[str, ExistingView]) -> list[str]:
    """The saved tabs config does not name, in the order the board holds them.

    A board has to keep at least one view, and config is what says which are
    wanted, so pruning is only ever offered when config declares some.
    """
    if not config.views:
        return []
    declared = {view.name for view in config.views}
    return [name for name in standing if name not in declared]


def apply_views(config: Config, number: str, project_id: str,
                fields: tuple[ProjectField, ...], reconcile: bool = False,
                created: bool = False, prune: bool = False,
                built_in: dict[str, str] | None = None) -> None:
    """Create declared views; explicitly reconcile and prune when asked.

    A project created in this call has one empty GitHub-seeded `View 1`; turn
    it into the first declared view instead of leaving a duplicate tab.
    Default application leaves an existing view alone, because a deployed
    board's live shape belongs to GitHub. Reconciliation is an explicit choice
    to make config win for declared filters and layouts.

    `prune` is a second, separate choice: delete the saved tabs config does
    not name. It is offered for views and for nothing else, because a view
    holds no items and no field values — losing one costs the grouping and
    sorting that boardkit cannot express in config, which is real but is not
    data. A field or a column is never deleted at all: the values in them, and
    the items in them, would go with them.
    """
    if not config.views:
        return
    standing = existing_views(config, number)
    # GitHub's own fields — parent issue, sub-issues progress — are not in the
    # snapshot, but a view may still show them.
    field_ids = {**(built_in or {}),
                 **{field.name: field.id for field in fields if field.id}}
    first = config.views[0]
    seeded = standing.get("View 1")
    if created and seeded and first.name not in standing:
        configure_view(seeded.id, first, field_ids)
        standing = {name: current for name, current in standing.items()
                    if name != "View 1"}
        standing[first.name] = ExistingView(seeded.id, first.filter, first.layout)
        print(f"  configured starter view {first.name} [{first.layout}]")
        note("configured_starter_view", name=first.name, layout=first.layout,
             filter=first.filter or None)
    for view in config.views:
        if view.name in standing:
            current = standing[view.name]
            if reconcile and (current.filter != view.filter or
                              current.layout != view.layout):
                configure_view(current.id, view, field_ids)
                print(f"  reconciled view {view.name} [{view.layout}]")
                note("reconciled_view", name=view.name, layout=view.layout,
                     filter=view.filter or None)
            continue
        made = make_view(project_id, view, field_ids)
        if view.filter:
            filter_view(made, view.filter)
        print(f"  added view {view.name} [{view.layout}]")
        standing[view.name] = ExistingView(made, view.filter, view.layout)
        note("created_view", name=view.name, layout=view.layout,
             filter=view.filter or None)
    if not prune:
        return
    # Last, so every declared view already exists: a board must keep one, and
    # deleting before creating could momentarily leave it with none.
    for name in undeclared(config, standing):
        delete_view(standing[name].id)
        print(f"  deleted view {name}, which config does not name")
        note("deleted_view", name=name)


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
