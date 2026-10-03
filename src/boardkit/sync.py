"""Move state between GitHub and the local store.

Pull replaces the snapshot. Push drains the pending writes, and a write that
lost to a remote change moves to `rejected` instead of being applied: remote is
the source of truth, and nothing is dropped silently.

The parsing is pure functions over payloads, because the shapes `gh` returns
can then be tested without a token or a project.
"""
from __future__ import annotations

from .board import MISSING, normalize
from .config import Config, ProjectField
from .gh import BoardError, RateLimited, Unreachable, gh, gh_json, run_json
from .trail import note
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
        # A single select calls them `options`, a multi select
        # `multiSelectOptions`; both mean the same thing here.
        choices = entry.get("options") or entry.get("multiSelectOptions") or []
        options = tuple(option.get("name", "") for option in choices)
        fields.append(ProjectField(
            # Stored under the name config uses, so a lookup from a snapshot
            # and a lookup from config find the same field.
            name=declared.get(key(name), key(name)),
            data_type=data_type_of(entry),
            options=options,
            id=entry.get("id", ""),
            option_ids={option.get("name", ""): option.get("id", "")
                        for option in choices},
        ))
    return tuple(fields)


def data_type_of(entry: dict) -> str:
    """GraphQL reports a dataType directly; `field-list` only a type name."""
    if entry.get("dataType"):
        return entry["dataType"]
    kind = entry.get("type", "")
    return {
        "ProjectV2SingleSelectField": "SINGLE_SELECT",
        "ProjectV2MultiSelectField": "MULTI_SELECT",
        "ProjectV2IterationField": "ITERATION",
        "ProjectV2Field": "TEXT",
    }.get(kind, "TEXT")


VALUE_FRAGMENTS = """
    ... on ProjectV2ItemFieldTextValue { text field { ...fieldName } }
    ... on ProjectV2ItemFieldNumberValue { number field { ...fieldName } }
    ... on ProjectV2ItemFieldDateValue { date field { ...fieldName } }
    ... on ProjectV2ItemFieldSingleSelectValue { name field { ...fieldName } }
    ... on ProjectV2ItemFieldIterationValue { title field { ...fieldName } }
    ... on ProjectV2ItemFieldMultiSelectValue {
          options { name } field { ...fieldName } }
"""


CONTENT = (
    # `body` is a scalar on a node already being fetched, so it costs no points
    # — only bytes. Worth it: deciding what to work on means reading the
    # acceptance criteria, and without it that is an API call. Labels,
    # assignees and milestone are connections and so the expensive shape in
    # principle; measured, they cost nothing worth saving, and an agent
    # choosing work reads them.
    # 100 is GitHub's maximum for a connection. These are not paged — doing so
    # would mean a follow-up query per item — so instead the page info is read
    # back and truncation is reported rather than passed off as the whole list.
    "content { ... on Issue { number title url state body "
    "    labels(first: 100) { pageInfo { hasNextPage } nodes { name } } "
    "    assignees(first: 100) { pageInfo { hasNextPage } nodes { login } } "
    "    milestone { title } } "
    "  ... on PullRequest { number title url state body "
    "    labels(first: 100) { pageInfo { hasNextPage } nodes { name } } "
    "    assignees(first: 100) { pageInfo { hasNextPage } nodes { login } } } } ")


def items_block(config: Config, after: str | None) -> str:
    cursor = f', after: "{after}"' if after else ""
    return (f"items(first: {config.page_size}{cursor}) {{ "
            "pageInfo { hasNextPage endCursor } nodes { id type "
            + CONTENT
            + f"fieldValues(first: {config.page_size}) "
              f"{{ nodes {{{VALUE_FRAGMENTS}}} }} }} }} ")


def fetch_board(config: Config, number: str) -> dict:
    """The project, its fields and all of its items.

    Through GraphQL rather than `gh project ...` because `gh` cannot serialise
    a multi-select: both the field and every value of it come back empty, so
    the field is invisible and any write to it fails for want of an id. The
    same query carries each issue's open/closed state and its body, neither of
    which `item-list` returns, and the project's own node id, which used to be
    a third call to `gh project view`.

    Paged until the board is exhausted, because a snapshot that is quietly
    missing its tail is worse than a slow one: an agent cannot tell an item
    that is absent from one that was never fetched, and would report the
    second as the first. The whole board, or nothing.

    A full pull is the one-time cost. It is three points on a board of this
    size and would be a couple of hundred on a very large one, against five
    thousand an hour — not worth trading completeness for.
    """
    kind = "organization" if config.owner_type == "org" else "user"
    head = (
        "fragment fieldName on ProjectV2FieldCommon { name } "
        f'{{ {kind}(login: "{config.owner}") {{ projectV2(number: {number}) {{ '
        "id "
        f"{fields_block(config, None)}")
    payload = run_json(["gh", "api", "graphql", "-f",
                        f"query={head}{items_block(config, None)}}} }} }}"])
    project = payload["data"][kind]["projectV2"]
    items = [flatten(node) for node in project["items"]["nodes"] if node]
    page, pages = project["items"]["pageInfo"], 1
    fields = [node for node in project["fields"]["nodes"] if node]
    field_page = project["fields"]["pageInfo"]

    while field_page.get("hasNextPage"):
        more = run_json(["gh", "api", "graphql", "-f", "query="
                         f'{{ {kind}(login: "{config.owner}") '
                         f"{{ projectV2(number: {number}) {{ "
                         f"{fields_block(config, field_page['endCursor'])}}} }} }}"])
        block = more["data"][kind]["projectV2"]["fields"]
        fields += [node for node in block["nodes"] if node]
        field_page = block["pageInfo"]

    while page.get("hasNextPage"):
        pages += 1
        note("pulling", page=pages, so_far=len(items))
        more = run_json(["gh", "api", "graphql", "-f", f"query="
                         "fragment fieldName on ProjectV2FieldCommon { name } "
                         f'{{ {kind}(login: "{config.owner}") '
                         f"{{ projectV2(number: {number}) {{ "
                         f"{items_block(config, page['endCursor'])}}} }} }}"])
        block = more["data"][kind]["projectV2"]["items"]
        items += [flatten(node) for node in block["nodes"] if node]
        page = block["pageInfo"]

    return {"id": project.get("id", ""), "fields": fields, "items": items}


def truncated(rows: list[dict]) -> list[str]:
    """Items whose labels or assignees ran past what one request can hold.

    These are the one place boardkit cannot page: doing so would mean a
    follow-up query per item. So the cut is reported rather than passed off as
    the whole list — an agent that cannot tell a short list from a truncated
    one will read the truncated one as complete.
    """
    cut = []
    for row in rows:
        content = row.get("content") or {}
        for part in ("labels", "assignees"):
            if ((content.get(part) or {}).get("pageInfo") or {}).get("hasNextPage"):
                cut.append(f'#{content.get("number")} has more than 100 {part}')
    return cut


def fields_block(config: Config, after: str | None) -> str:
    cursor = f', after: "{after}"' if after else ""
    return (f"fields(first: {config.page_size}{cursor}) {{ "
            "pageInfo { hasNextPage endCursor } nodes { "
            "... on ProjectV2FieldCommon { id name dataType } "
            "... on ProjectV2SingleSelectField { options { id name } } "
            "... on ProjectV2MultiSelectField { multiSelectOptions { id name } } "
            "} } ")


def flatten(node: dict) -> dict:
    """One GraphQL item as the flat mapping the rest of the package expects."""
    content = node.get("content") or {}
    flat = {"id": node.get("id", ""), "content": {**content, "type": node.get("type")}}
    for value in (node.get("fieldValues") or {}).get("nodes") or []:
        name = ((value.get("field") or {}).get("name") or "").strip()
        if not name:
            continue
        flat[key(name)] = value_of(value)
    return flat


def value_of(value: dict):
    """Whichever of the typed shapes this value arrived in."""
    for shape in ("name", "text", "title", "date"):
        if value.get(shape) is not None:
            return value[shape]
    if value.get("number") is not None:
        return value["number"]
    if value.get("options") is not None:
        return [option.get("name", "") for option in value["options"]]
    return MISSING


def fetch_some(config: Config, ids: list[str]) -> list[dict]:
    """Current values for named items only, by node id.

    A push does not need the board. It needs to know whether the handful of
    items it is about to write to still hold what their writes were decided
    against, and `nodes(ids:)` answers exactly that — a few nodes rather than
    every item, every field and every body on the board.

    A full pull still has its place: it is what discovers items that are new,
    and it is what `board sync` does.
    """
    if not ids:
        return []
    listed = ", ".join(f'"{identity}"' for identity in ids)
    query = (
        "fragment fieldName on ProjectV2FieldCommon { name } "
        f"{{ nodes(ids: [{listed}]) {{ ... on ProjectV2Item {{ id type "
        "content { ... on Issue { number state } "
        "          ... on PullRequest { number state } } "
        f"fieldValues(first: {config.page_size}) {{ nodes {{{VALUE_FRAGMENTS}}} }} "
        "} } }")
    payload = run_json(["gh", "api", "graphql", "-f", f"query={query}"])
    return [flatten(node) for node in payload["data"]["nodes"] if node]


def fetch_fields(config: Config, number: str) -> list[dict]:
    """Just the fields, for `admin apply`, which asks before any item matters."""
    return fetch_board(config, number)["fields"]


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
    board = fetch_board(config, target)
    rows = board["items"]
    # Fields are parsed first: when config declares none, they are what says
    # which keys an item carries.
    fields = parse_fields(board["fields"], config)
    names = config.synced_names or tuple(field.name for field in fields)
    # The state arrives with each item, so a closed one is dropped without the
    # separate `gh issue list` call this used to need.
    still_open = {row["content"]["number"] for row in rows
                  if row.get("content", {}).get("state") == "OPEN"}
    snapshot = Snapshot(
        project_id=board["id"],
        project_number=target,
        synced_at=now(),
        fields=fields,
        items=tuple(normalize(rows, still_open, config, names)),
    )
    store.save_state(snapshot)
    store.ignore_file()
    note("pulled", project=target, items=len(snapshot.items),
            fields=len(snapshot.fields))
    for warning in [*truncated(rows), *drift(snapshot, config)]:
        print(f"warning: {warning}")
        note("warned", about=warning)
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


def value_literal(write: Write, field: ProjectField) -> str:
    """One write's value, in the shape `ProjectV2FieldValue` wants it."""
    if field.data_type in {"SINGLE_SELECT", "MULTI_SELECT"}:
        ids = chosen(write, field)
        if field.data_type == "SINGLE_SELECT":
            if len(ids) != 1:
                raise BoardError(
                    f"`{write.field}` takes one value, not {len(ids)}")
            return f'{{singleSelectOptionId: "{ids[0]}"}}'
        listed = ", ".join(f'"{identity}"' for identity in ids)
        return f"{{multiSelectOptionIds: [{listed}]}}"
    if field.data_type == "NUMBER":
        return f"{{number: {float(write.value)}}}"
    if field.data_type == "DATE":
        return f'{{date: "{write.value}"}}'
    if field.data_type == "TEXT":
        escaped = write.value.replace("\\", "\\\\").replace('"', '\\"')
        return f'{{text: "{escaped}"}}'
    raise BoardError(f"boardkit cannot write a {field.data_type} field yet")


def apply_all(writes: list[Write], snapshot: Snapshot) -> list[Write]:
    """Send every write in one request, and return those that landed.

    GitHub takes one field per mutation, so a dozen changes are a dozen
    mutations — but GraphQL carries them in a single document, aliased. That
    turns a dozen round trips into one, and a dozen per-request point charges
    into one.

    Mutations in a document run in order and do not roll back, so a failure
    partway leaves the earlier ones applied. The aliases say which: anything
    GitHub names in an error is reported back as not landed, and stays queued.
    """
    if not writes:
        return []
    parts = []
    for index, write in enumerate(writes):
        item, field = snapshot.item(write.number), snapshot.field(write.field)
        if item is None or not item.id:
            raise BoardError(f"#{write.number} is not in the snapshot; pull first")
        if field is None or not field.id:
            raise BoardError(
                f"field `{write.field}` has no id in the snapshot; pull first")
        parts.append(
            f"m{index}: updateProjectV2ItemFieldValue(input: {{"
            f' projectId: "{snapshot.project_id}", itemId: "{item.id}",'
            f" fieldId: \"{field.id}\", value: {value_literal(write, field)}"
            " }) { projectV2Item { id } }")
    document = "mutation { " + " ".join(parts) + " }"
    payload = run_json(["gh", "api", "graphql", "-f", f"query={document}"])
    failed = {str(error.get("path", [""])[0]) for error in payload.get("errors", [])}
    return [write for index, write in enumerate(writes) if f"m{index}" not in failed]


def plan(writes: list[Write],
         remote: Snapshot) -> tuple[list[Write], list[tuple[Write, str, str]]]:
    """Split pending writes into those that still apply and those that lost.

    A write loses when the field it targets changed on GitHub after the write
    was made — compared against `was`, the value the agent decided against —
    or when the item left the board entirely. Each loss carries the remote
    value and why, because "it changed" and "it is gone" call for different
    decisions from whoever re-decides.
    """
    applies, lost = [], []
    for write in writes:
        item = remote.item(write.number)
        if item is None:
            lost.append((write, "", "the item is closed or no longer on the board"))
            continue
        current = item.field(write.field)
        if current == write.value:
            continue  # Already true remotely; nothing to push.
        if current != write.was:
            lost.append((write, current,
                         "the field changed on GitHub after this write was made"))
            continue
        applies.append(write)
    return applies, lost


def patched(snapshot: Snapshot, writes: list[Write]) -> Snapshot:
    """The snapshot with what was just written folded in.

    A second pull would be the thorough way to learn the new values, and it is
    not worth its cost: the snapshot is a cache that is stale the moment any
    pull finishes, and the guarantee boardkit makes — that a write never
    silently overwrites — rests entirely on the pull *before* the push. What
    this records is what was sent rather than what GitHub stored, which differs
    only if GitHub normalised the value; the next pull settles that, as it
    settles everything else here.
    """
    changes: dict[int, dict[str, str]] = {}
    for write in writes:
        changes.setdefault(write.number, {})[write.field] = write.value
    items = tuple(
        item.model_copy(update={"fields": {**item.fields, **changes[item.number]}})
        if item.number in changes else item
        for item in snapshot.items)
    return snapshot.model_copy(update={"items": items})


def refreshed(config: Config, store: Store, writes: list[Write]) -> Snapshot:
    """The snapshot with current values for the items about to be written.

    Only those items are re-read. Everything else in the snapshot is as stale
    as it was, which is the standing arrangement: boardkit does not claim the
    snapshot is current, it claims a write never silently overwrites — and
    that needs fresh values for the targets and nothing else.
    """
    snapshot = store.state()
    wanted = {write.number for write in writes}
    ids = [item.id for item in snapshot.items if item.number in wanted and item.id]
    if not ids:
        return snapshot
    names = tuple(field.name for field in snapshot.fields)
    rows = fetch_some(config, ids)
    still_open = {row["content"]["number"] for row in rows
                  if row.get("content", {}).get("state") == "OPEN"}
    fresh = {item.number: item for item in normalize(rows, still_open, config, names)}
    items = []
    for item in snapshot.items:
        current = fresh.get(item.number)
        if item.number in wanted and current is None:
            continue          # Closed or gone: the write will be rejected.
        items.append(item.model_copy(update={"fields": current.fields})
                     if current else item)
    return snapshot.model_copy(update={"items": tuple(items)})


def push_now(config: Config, store: Store) -> tuple[int, int, str]:
    """Push the queue straight away, and say why if it could not.

    A change is meant to reach GitHub when it is made. It still goes through
    the queue first, so an attempt that fails leaves the work recorded rather
    than lost — the queue is the durability, not the schedule.

    Being unable to push is ordinary, not an error: offline, rate limited, or
    out of budget are all "later", and `board sync push` is what later means.
    """
    try:
        pushed, lost = push(config, store)
    except Unreachable:
        return 0, 0, "offline"
    except RateLimited:
        return 0, 0, "rate limited"
    return pushed, lost, ""


def push(config: Config, store: Store) -> tuple[int, int]:
    """Drain pending writes, re-reading only the items they target."""
    writes = store.pending()
    if not writes:
        return 0, 0
    remote = refreshed(config, store, writes)
    applies, lost = plan(writes, remote)
    # Rejections are written before the queue is cleared, so a crash between
    # them repeats work rather than losing it, and recording one twice is a
    # no-op.
    for write, current, why in lost:
        store.reject(write, remote=current, reason=why)
    landed = apply_all(applies, remote)
    for write in landed:
        note("pushed", id=write.id, number=write.number, field=write.field,
             value=write.value, was=write.was)
    sent = {write.id for write in landed}
    with store.held():
        # Anything that did not land stays queued: a document's mutations run
        # in order and do not roll back, so a partial failure is possible.
        store.save_pending([w for w in store.pending()
                            if w.id not in sent and not any(
                                lost_write.id == w.id for lost_write, _, _ in lost)])
    if landed:
        store.save_state(patched(remote, landed))
    return len(landed), len(lost)
