"""Move state between GitHub and the local store.

Pull replaces the snapshot. Push drains the pending writes, and a write that
lost to a remote change moves to `rejected` instead of being applied: remote is
the source of truth, and nothing is dropped silently.

The parsing is pure functions over payloads, because the shapes `gh` returns
can then be tested without a token or a project.
"""
from __future__ import annotations

from .board import MISSING, normalize
from .config import Config, DataType, ProjectField
from .gh import BoardError, RateLimited, Unreachable, gh_json, quoted, run_json
from .store import Snapshot, Store, Write, now
from .trail import note


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
            # What each option means, as the board itself carries it.
            descriptions={option.get("name", ""): option.get("description", "")
                          for option in choices if option.get("description")},
        ))
    return tuple(fields)


def data_type_of(entry: dict) -> DataType:
    """GraphQL reports a dataType directly; `field-list` only a type name."""
    if entry.get("dataType"):
        return entry["dataType"]
    kind = entry.get("type", "")
    by_name: dict[str, DataType] = {
        "ProjectV2SingleSelectField": "SINGLE_SELECT",
        "ProjectV2MultiSelectField": "MULTI_SELECT",
        "ProjectV2IterationField": "ITERATION",
        "ProjectV2Field": "TEXT",
    }
    return by_name.get(kind, "TEXT")


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
    # One scalar, on a node already being fetched, and it replaces a live
    # search per `board item`. Only the parent is asked for: children are the
    # same relation read the other way, and every item on the board is in this
    # download, so inverting it locally is the whole answer rather than an
    # approximation of it. See `board.children`.
    "    parent { number } "
    "    milestone { title } } "
    "  ... on PullRequest { number title url state body "
    "    labels(first: 100) { pageInfo { hasNextPage } nodes { name } } "
    "    assignees(first: 100) { pageInfo { hasNextPage } nodes { login } } } "
    # A draft has a title and a body and nothing else — no number, no url, no
    # labels. It is the board's own note rather than a repository's issue.
    "  ... on DraftIssue { title body } } ")


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
        f"{{ {kind}(login: {quoted(config.owner)}) "
        f"{{ projectV2(number: {number}) {{ "
        # `url` so a listing can say which board it is answering about — a
        # scalar on a node already fetched, so it is free, and GitHub's own
        # spelling rather than one assembled from owner and number, which
        # would be a wrong link instead of a missing one for an org board.
        "id url "
        # Names only: enough to tell a declared tab that is gone from one
        # that is there, which is all `apply` would act on.
        f"views(first: {config.page_size}) {{ nodes {{ name }} }} "
        f"{fields_block(config, None)}")
    payload = run_json(["gh", "api", "graphql", "-f",
                        f"query={head}{items_block(config, None)}}} }} }}"])
    project = payload["data"][kind]["projectV2"]
    items = [flatten(node) for node in project["items"]["nodes"] if node]
    page, pages = project["items"]["pageInfo"], 1
    fields = [node for node in project["fields"]["nodes"] if node]
    field_page = project["fields"]["pageInfo"]

    while field_page.get("hasNextPage"):
        more = run_json(["gh", "api", "graphql", "-f",
                         ("query="
                          f"{{ {kind}(login: {quoted(config.owner)}) "
                          f"{{ projectV2(number: {number}) {{ "
                          f"{fields_block(config, field_page['endCursor'])}}} }} }}")])
        block = more["data"][kind]["projectV2"]["fields"]
        fields += [node for node in block["nodes"] if node]
        field_page = block["pageInfo"]

    while page.get("hasNextPage"):
        pages += 1
        note("pulling", page=pages, so_far=len(items))
        more = run_json(["gh", "api", "graphql", "-f",
                         ("query="
                          "fragment fieldName on ProjectV2FieldCommon { name } "
                          f"{{ {kind}(login: {quoted(config.owner)}) "
                          f"{{ projectV2(number: {number}) {{ "
                          f"{items_block(config, page['endCursor'])}}} }} }}")])
        block = more["data"][kind]["projectV2"]["items"]
        items += [flatten(node) for node in block["nodes"] if node]
        page = block["pageInfo"]

    views = [node.get("name", "") for node in
             (project.get("views") or {}).get("nodes", []) if node]
    return {"id": project.get("id", ""), "url": project.get("url", ""),
            "fields": fields, "items": items, "views": views}


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
            "... on ProjectV2SingleSelectField { options { id name description } } "
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
    # Every field the board has, declared or not. Narrowing to the declared
    # ones dropped the value of any other on every pull — `blocked` set and
    # then silently emptied by the next sync, with the board still holding it.
    # Declared names come first, because that is the print order config asks
    # for; the rest follow.
    declared = [name for name in config.synced_names
                if any(field.name == name for field in fields)]
    names = (*declared, *(field.name for field in fields if field.name not in declared))
    # The state arrives with each item, so a closed one is dropped without the
    # separate `gh issue list` call this used to need.
    still_open = {row["content"]["number"] for row in rows
                  if row.get("content", {}).get("state") == "OPEN"}
    snapshot = Snapshot(
        project_id=board["id"],
        project_url=board.get("url", ""),
        project_number=target,
        synced_at=now(),
        fields=fields,
        views=tuple(board.get("views", ())),
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
    """Where this repo's config and the board have come apart.

    Boards are edited in the browser, so the two drift by ordinary use rather
    than by mistake. The board wins — every read takes its shape from the
    snapshot — but config is what `admin apply` would build, so a config that
    has become fiction is worth saying out loud once a sync has the facts.
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
            f"missing from status_order; items there sort last")
    warnings.extend(field_drift(snapshot, config))
    warnings.extend(view_drift(snapshot, config))
    return warnings


def view_drift(snapshot: Snapshot, config: Config) -> list[str]:
    """Saved tabs config declares that the board no longer has.

    A tab is deleted in the browser by someone who decided they did not want
    it; `admin apply` would put it straight back, so the disagreement is
    worth a line before that happens.
    """
    if not snapshot.views:
        return []
    gone = [view.name for view in config.views if view.name not in snapshot.views]
    if not gone:
        return []
    return [(f"config declares view(s) {', '.join(repr(n) for n in gone)} the "
             f"board does not have; `board admin apply` would recreate them")]


def field_drift(snapshot: Snapshot, config: Config) -> list[str]:
    """Fields and options config declares that the board does not have.

    Only that direction: a field the board has and config does not is the
    normal case — config declares what to create, not an inventory.
    """
    said = []
    on_board = {field.name: field for field in snapshot.fields}
    for field in config.fields:
        if field.name == config.status_field:
            continue
        present = on_board.get(field.name)
        if present is None:
            said.append(f"config declares a {field.name!r} field the board does "
                        f"not have; `board admin apply` would create it")
            continue
        missing = [name for name in field.options if name not in present.options]
        if missing:
            said.append(f"{field.name} is missing option(s) "
                        f"{', '.join(repr(n) for n in missing)} that config declares")
    return said


def edit_args(write: Write, snapshot: Snapshot, config: Config) -> list[str]:
    """The `gh project item-edit` flags for one write.

    Pure, so the flag choice per field type is testable without a project.
    """
    item = snapshot.item(write.ref)
    field = snapshot.field(write.field)
    if item is None or not item.id:
        raise BoardError(f"#{write.ref} is not in the snapshot; pull first")
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
    item = snapshot.item(write.ref)
    field = snapshot.field(write.field)
    if item is None or not item.id:
        raise BoardError(f"#{write.ref} is not in the snapshot; pull first")
    if field is None or not field.id:
        raise BoardError(f"field `{write.field}` has no id in the snapshot; pull first")
    ids = ", ".join(quoted(option) for option in chosen(write, field))
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
        listed = ", ".join(quoted(identity) for identity in ids)
        return f"{{multiSelectOptionIds: [{listed}]}}"
    if field.data_type == "NUMBER":
        return f"{{number: {float(write.value)}}}"
    if field.data_type == "DATE":
        return f"{{date: {quoted(write.value)}}}"
    if field.data_type == "TEXT":
        return f"{{text: {quoted(write.value)}}}"
    raise BoardError(f"boardkit cannot write a {field.data_type} field yet")


def check(write: Write, snapshot: Snapshot) -> None:
    """Refuse a write that cannot succeed, before it is recorded.

    The queue is for changes that are good and not yet sent. One that local
    knowledge already rejects — a value that is not an option of its field —
    would otherwise sit there being retried forever, because a refusal on
    push is not a rejection and nothing clears it.

    Silent where only GitHub can judge: an unknown field, or an item the
    snapshot has not seen. The push says so in GitHub's own words.
    """
    if not write.value:
        return  # Clearing is allowed for every field type.
    field = snapshot.field(write.field)
    if field is None or not field.id:
        return
    value_literal(write, field)


def contested(write: Write, store: Store) -> None:
    """Refuse a write to a field whose last write lost — once.

    A rejection records that the board moved under a decision. An agent in a
    loop does not read it: it sets the field again, the conflict check compares
    against `was`, which is still the value that lost, and the remote change
    nobody looked at is overwritten. So a standing rejection is a gate and not
    only a log.

    Per field and not per item, because that is what the conflict was about.
    Thirty-nine writes in a batch with one conflict stay thirty-nine landed and
    one recorded; this refuses only the next write to the field that lost.

    One-shot, because refusing forever leaves no way to say "I have seen it and
    I still mean it" without inventing a second verb for it. The refusal
    consumes the rejection, so the same command repeated stands.

    The limit is worth stating plainly: this stops a blind overwrite, not a
    determined one. An agent that mechanically retries the command gets
    through on the second attempt, by design — knowing whether the remote
    value was actually read would need acknowledgement state, and a gate that
    cheap is worth more than a gate that heavy.
    """
    standing = store.rejected()
    found = next((kept for kept in standing
                  if kept.write.ref == write.ref
                  and kept.write.field == write.field), None)
    if found is None:
        return
    store.save_rejected([kept for kept in standing
                         if kept.write.id != found.write.id])
    note("contested", id=write.id, ref=write.ref, field=write.field,
         value=write.value, remote=found.remote)
    raise BoardError(
        f"#{write.ref} {write.field}: the last write here lost — "
        f"`{found.write.value}` was wanted, the board holds `{found.remote}`"
        f"{f' ({found.reason})' if found.reason else ''}. Nothing was written. "
        f"Read the item and decide again, or repeat this command to set "
        f"`{write.value}` anyway.")


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
        item, field = snapshot.item(write.ref), snapshot.field(write.field)
        if item is None or not item.id:
            raise BoardError(f"#{write.ref} is not in the snapshot; pull first")
        if field is None or not field.id:
            raise BoardError(
                f"field `{write.field}` has no id in the snapshot; pull first")
        # An empty value means "unset this", which is its own mutation —
        # `update` has no way to express the absence of a value.
        if not write.value:
            parts.append(
                f"m{index}: clearProjectV2ItemFieldValue(input: {{"
                f' projectId: "{snapshot.project_id}", itemId: "{item.id}",'
                f' fieldId: "{field.id}"'
                " }) { projectV2Item { id } }")
            continue
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
        item = remote.item(write.ref)
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
    changes: dict[str, dict[str, str]] = {}
    for write in writes:
        changes.setdefault(write.ref, {})[write.field] = write.value
    items = tuple(
        item.model_copy(update={"fields": {**item.fields, **changes[item.ref]}})
        if item.ref in changes else item
        for item in snapshot.items)
    return snapshot.model_copy(update={"items": items})


def unresolved(snapshot: Snapshot, writes: list[Write]) -> list[Write]:
    """Writes whose target field the snapshot cannot name an id for."""
    return [write for write in writes
            if (field := snapshot.field(write.field)) is None or not field.id]


def with_fields(config: Config, store: Store, writes: list[Write]) -> None:
    """Re-read field definitions if some write targets a field without an id.

    `check` deliberately lets a write to an unknown field be queued: boards
    are edited in the browser, so a field the snapshot has not seen may well
    exist, and only GitHub can say. Honouring that means asking. Without it
    the write reached `apply_all`, which has no id to send and raises — and
    because it raises before the document is built, one such write took the
    whole push down with it, stranding every other queued change.

    Only when some write needs it, and only the fields: items are re-read
    separately, and an ordinary push asks for nothing extra here.
    """
    snapshot = store.state()
    if not unresolved(snapshot, writes):
        return
    fields = parse_fields(fetch_fields(config, number(config, store)), config)
    if fields:
        store.save_state(snapshot.model_copy(update={"fields": fields}))


def refreshed(config: Config, store: Store, writes: list[Write]) -> Snapshot:
    """The snapshot with current values for the items about to be written.

    Only those items are re-read. Everything else in the snapshot is as stale
    as it was, which is the standing arrangement: boardkit does not claim the
    snapshot is current, it claims a write never silently overwrites — and
    that needs fresh values for the targets and nothing else.
    """
    snapshot = store.state()
    wanted = {write.ref for write in writes}
    ids = [item.id for item in snapshot.items if item.ref in wanted and item.id]
    if not ids:
        return snapshot
    names = tuple(field.name for field in snapshot.fields)
    rows = fetch_some(config, ids)
    still_open = {row["content"]["number"] for row in rows
                  if row.get("content", {}).get("state") == "OPEN"}
    fresh = {item.ref: item for item in normalize(rows, still_open, config, names)}
    items = []
    for item in snapshot.items:
        current = fresh.get(item.ref)
        if item.ref in wanted and current is None:
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
    with_fields(config, store, writes)
    remote = refreshed(config, store, writes)
    applies, lost = plan(writes, remote)
    # A field GitHub turned out not to have after all. `apply_all` would raise
    # on it, and raises before the document is built, so one of these used to
    # strand every other queued write. Rejecting it clears the queue and leaves
    # the change recorded for whoever has to re-decide it.
    missing = {write.id for write in unresolved(remote, applies)}
    lost += [(write, "", f"the board has no field `{write.field}`")
             for write in applies if write.id in missing]
    applies = [write for write in applies if write.id not in missing]
    # Rejections are written before the queue is cleared, so a crash between
    # them repeats work rather than losing it, and recording one twice is a
    # no-op.
    for write, current, why in lost:
        store.reject(write, remote=current, reason=why)
    landed = apply_all(applies, remote)
    for write in landed:
        note("pushed", id=write.id, ref=write.ref, field=write.field,
             value=write.value, was=write.was)
    # Three ways a write is finished with: it landed, it lost, or it was
    # already true remotely and `plan` had nothing to send. The third one
    # has to be named — it is in neither list, and a queue cleared by
    # subtracting only the first two kept it forever, retried on every push
    # and counted by every listing, with nothing able to clear it because it
    # can never land or be rejected.
    settled = ({write.id for write in writes} - {write.id for write in applies}
               - {write.id for write, _, _ in lost})
    done = ({write.id for write in landed} | settled
            | {write.id for write, _, _ in lost})
    with store.held():
        # The queue is re-read under the lock and filtered by id rather than
        # replaced: a change made while the push was in flight is still
        # pending, and must survive it. Anything attempted that did not land
        # stays queued too — a document's mutations run in order and do not
        # roll back, so a partial failure is possible.
        store.save_pending([w for w in store.pending() if w.id not in done])
    if landed:
        store.save_state(patched(remote, landed))
    return len(landed), len(lost)
