"""Move state between GitHub and the local store.

Pull replaces the snapshot. Push drains the pending writes, and a write that
lost to a remote change moves to `rejected` instead of being applied: remote is
the source of truth, and nothing is dropped silently.

The parsing is pure functions over payloads, because the shapes `gh` returns
can then be tested without a token or a project.
"""
from __future__ import annotations

from .board import normalize, open_numbers
from .config import Config, ProjectField
from .gh import BoardError, gh, gh_json, run_json
from .store import Snapshot, Store, Write, now

# `item-list` names a field by camelCasing it, while `field-list` reports the
# name as configured. Comparing them stripped and lowercased matches "Status"
# to "status" and "Target date" to "targetDate" without guessing which
# transformation gh applied.
def key(name: str) -> str:
    return "".join(character for character in name if character.isalnum()).lower()


# The fields every project has, projected from the issue and not worth
# carrying: a board that declares nothing should still not end up with eleven
# columns of Created / Updated / Reviewers.
BUILT_IN = {"title", "assignees", "labels", "linkedpullrequests", "milestone",
            "repository", "reviewers", "parentissue", "subissuesprogress",
            "created", "updated", "closed"}


def parse_fields(payload: list[dict], config: Config) -> tuple[ProjectField, ...]:
    """Field configuration, keyed by the name `item-list` will use for it.

    Every field the board owns is carried, whether or not config mentions it:
    declaring one field is a statement about `apply` and about print order, not
    an instruction to forget the rest. Only GitHub's own built-in projections
    are dropped, since a board would otherwise arrive with eleven columns of
    Created / Updated / Reviewers.
    """
    declared = {key(name): name for name in config.synced_names}
    fields = []
    for entry in payload:
        name = entry.get("name", "")
        if key(name) in BUILT_IN and key(name) != key(config.status_field):
            continue
        options = tuple(option.get("name", "") for option in entry.get("options", []))
        fields.append(ProjectField(
            # Stored under the name config uses, so a lookup from a snapshot
            # and a lookup from config find the same field.
            name=declared.get(key(name), key(name)),
            data_type=data_type_of(entry),
            options=options,
            id=entry.get("id", ""),
            option_ids={option.get("name", ""): option.get("id", "")
                        for option in entry.get("options", [])},
        ))
    return tuple(fields)


def data_type_of(entry: dict) -> str:
    """`field-list` reports either a dataType or the GraphQL type name."""
    if entry.get("dataType"):
        return entry["dataType"]
    kind = entry.get("type", "")
    return {
        "ProjectV2SingleSelectField": "SINGLE_SELECT",
        "ProjectV2MultiSelectField": "MULTI_SELECT",
        "ProjectV2IterationField": "ITERATION",
        "ProjectV2Field": "TEXT",
    }.get(kind, "TEXT")


def find_project(payload: list[dict], config: Config) -> str:
    """The number of the project config names, by title.

    A number is a fact about GitHub, so config should not have to carry one.
    Titles are not unique, though, so an ambiguous match is an error rather
    than a guess — `project` in config pins it when that happens.
    """
    matches = [str(entry.get("number", "")) for entry in payload
               if entry.get("title") == config.name]
    if not matches:
        titles = ", ".join(sorted(entry.get("title", "") for entry in payload)) or "none"
        raise BoardError(
            f"{config.owner} has no project titled {config.name!r} (has: {titles}). "
            f"Create it with `board admin apply`, or set `project` in "
            f"[tool.boardkit] to pin one by number.")
    if len(matches) > 1:
        raise BoardError(
            f"{config.owner} has {len(matches)} projects titled {config.name!r} "
            f"(numbers {', '.join(matches)}). Set `project` in [tool.boardkit].")
    return matches[0]


def number(config: Config, store: Store) -> str:
    """Which project to talk to: pinned, remembered, or looked up."""
    if config.project:
        return config.project
    remembered = store.state().project_number
    if remembered:
        return remembered
    listed = gh_json("project", "list", "--owner", config.owner,
                     "--limit", "100", "--format", "json")
    return find_project(listed.get("projects", []), config)


def pull(config: Config, store: Store) -> Snapshot:
    """Replace the snapshot with what GitHub currently holds."""
    target = number(config, store)
    project = gh_json("project", "view", target, "--owner", config.owner,
                      "--format", "json")
    listed = gh_json("project", "field-list", target, "--owner", config.owner,
                     "--limit", "50", "--format", "json")
    items = gh_json("project", "item-list", target, "--owner", config.owner,
                    "--limit", "200", "--format", "json")
    # Fields are parsed first: when config declares none, they are what says
    # which keys an item carries.
    fields = parse_fields(listed.get("fields", []), config)
    names = config.synced_names or tuple(field.name for field in fields)
    snapshot = Snapshot(
        project_id=project.get("id", ""),
        project_number=target,
        synced_at=now(),
        fields=fields,
        items=tuple(normalize(items.get("items", []), open_numbers(config),
                              config, names)),
    )
    store.save_state(snapshot)
    store.ignore_file()
    for warning in drift(snapshot, config):
        print(f"warning: {warning}")
    return snapshot


def drift(snapshot: Snapshot, config: Config) -> list[str]:
    """Columns configured here that the board does not actually have.

    A misspelled column is silent otherwise: items in it sort last and vanish
    from the rollup, which reads as an empty board rather than a typo.
    """
    # Nothing to disagree with when the repo asserts no order: it is taking
    # the board's columns as given, which is the default.
    if not config.status_order:
        return []
    status = snapshot.field(config.status_field)
    if status is None or not status.options:
        return []
    real = set(status.options)
    unknown = [name for name in config.status_order if name not in real]
    unconfigured = [name for name in status.options if name not in config.status_order]
    warnings = []
    if unknown:
        warnings.append(
            f"status_order names {', '.join(repr(n) for n in unknown)}, which the "
            f"board has no column for (it has: {', '.join(status.options)})")
    if unconfigured:
        warnings.append(
            f"the board has column(s) {', '.join(repr(n) for n in unconfigured)} "
            f"missing from status_order; items there sort last and are left out "
            f"of the rollup")
    return warnings


def edit_args(write: Write, snapshot: Snapshot, config: Config) -> list[str]:
    """The `gh project item-edit` flags for one write.

    Pure, so the flag choice per field type is testable without a project.
    """
    item = snapshot.item(write.number)
    field = snapshot.field(write.field)
    if item is None or not item.id:
        raise BoardError(f"#{write.number} is not in the snapshot; pull first")
    if field is None or not field.id:
        raise BoardError(f"field `{write.field}` has no id in the snapshot; pull first")

    args = ["project", "item-edit", "--id", item.id,
            "--project-id", snapshot.project_id, "--field-id", field.id]
    if field.data_type == "SINGLE_SELECT":
        option = field.option_ids.get(write.value)
        if not option:
            allowed = ", ".join(sorted(field.option_ids)) or "none"
            raise BoardError(
                f"`{write.value}` is not an option of `{write.field}` (has: {allowed})")
        return [*args, "--single-select-option-id", option]
    if field.data_type == "NUMBER":
        return [*args, "--number", write.value]
    if field.data_type == "DATE":
        return [*args, "--date", write.value]
    if field.data_type == "TEXT":
        return [*args, "--text", write.value]
    raise BoardError(f"boardkit cannot write a {field.data_type} field yet")


def chosen(write: Write, field: ProjectField) -> list[str]:
    """Option ids for a multi-select write.

    The value is written as one string — `board set 8 area "config, docs"` —
    because a queued change is one line in a file and a comma reads better
    there than a nested list.
    """
    names = [part.strip() for part in write.value.split(",") if part.strip()]
    ids = []
    for name in names:
        option = field.option_ids.get(name)
        if not option:
            allowed = ", ".join(sorted(field.option_ids)) or "none"
            raise BoardError(
                f"`{name}` is not an option of `{write.field}` (has: {allowed})")
        ids.append(option)
    return ids


def multi_select_query(write: Write, snapshot: Snapshot) -> str:
    """`gh project item-edit` has no multi-select flag, so this one goes direct."""
    item = snapshot.item(write.number)
    field = snapshot.field(write.field)
    if item is None or not item.id:
        raise BoardError(f"#{write.number} is not in the snapshot; pull first")
    if field is None or not field.id:
        raise BoardError(f"field `{write.field}` has no id in the snapshot; pull first")
    ids = ", ".join(f'"{option}"' for option in chosen(write, field))
    return ("mutation { updateProjectV2ItemFieldValue(input: {"
            f' projectId: "{snapshot.project_id}", itemId: "{item.id}",'
            f' fieldId: "{field.id}", value: {{ multiSelectOptionIds: [{ids}] }}'
            " }) { projectV2Item { id } } }")


def apply(write: Write, snapshot: Snapshot, config: Config) -> None:
    """Send one write, by whichever route its field type needs."""
    field = snapshot.field(write.field)
    if field is not None and field.data_type == "MULTI_SELECT":
        run_json(["gh", "api", "graphql", "-f",
                  f"query={multi_select_query(write, snapshot)}"])
        return
    gh(*edit_args(write, snapshot, config))


def plan(writes: list[Write], remote: Snapshot) -> tuple[list[Write], list[tuple[Write, str]]]:
    """Split pending writes into those that still apply and those that lost.

    A write loses when the field it targets changed on GitHub after the write
    was made — compared against `was`, the value the agent decided against.
    """
    applies, lost = [], []
    for write in writes:
        item = remote.item(write.number)
        if item is None:
            lost.append((write, "the item is no longer on the board"))
            continue
        current = item.field(write.field)
        if current == write.value:
            continue  # Already true remotely; nothing to push.
        if current != write.was:
            lost.append((write, current))
            continue
        applies.append(write)
    return applies, lost


def push(config: Config, store: Store) -> tuple[int, int]:
    """Drain pending writes against a freshly pulled snapshot."""
    writes = store.pending()
    if not writes:
        return 0, 0
    remote = pull(config, store)
    applies, lost = plan(writes, remote)
    for write, current in lost:
        store.reject(write, remote=current)
    for write in applies:
        apply(write, remote, config)
    store.save_pending([])
    if applies:
        # The pushed values are not in the snapshot taken before them.
        pull(config, store)
    return len(applies), len(lost)
