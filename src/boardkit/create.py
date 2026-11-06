"""Put new work on the board.

An issue rather than a draft card. A draft lives only on the board — no
number, no URL, not in `gh issue list`, unreachable from a commit — which is
fine for a jotted thought and wrong for work that carries a plan and
acceptance criteria. Those want to be reviewable, linkable and closable by a
commit, and that means an issue.

Creating one needs the network: only GitHub can mint the number, and every
other part of boardkit addresses an item by it. So this is the one verb that
cannot be queued for later, and it says so rather than pretending.
"""
from __future__ import annotations

from pydantic import BaseModel

from .config import Config
from .gh import BoardError, gh_json, graphql_partial, quoted, run_json
from .store import Store, Write
from .trail import note


def add_draft(config: Config, store: Store, title: str, body: str,
              values: dict[str, str]) -> int:
    """Put a note on the board without making an issue of it.

    A draft is the board's own card: a title, a body, and no number, URL or
    repository. Right for an idea that has not earned a plan yet; wrong for
    work that wants to be reviewable and closable by a commit, which is what
    an issue is for.
    """
    if not title.strip():
        raise BoardError("a draft needs a title")
    number = store.state().project_number or config.require("project")
    made = gh_json("project", "item-create", number, "--owner", config.owner,
                   "--title", title, "--body", body or "", "--format", "json")
    from .board import ProjectItem

    handle = ProjectItem(id=made.get("id", ""), title=title).ref
    # Printed, because it is how the card is addressed and nothing else knows
    # it: a draft has no number, and finding it meant listing the board again.
    print(f"Created draft {handle} {title!r}")
    note("created_draft", title=title, ref=handle, item=made.get("id"))

    from . import sync
    sync.created(store, made.get("id", ""))
    if values:
        place(config, store, 0, made.get("id", ""), title, values)
    if not sync.refresh(config, store, [made.get("id", "")]):
        sync.pull(config, store)
    remember(store, made.get("id", ""), 0, title, body or "", values)
    return 0


class Asked(BaseModel):
    """One issue to create, with what goes to the issue told apart from the
    board fields."""
    title: str
    body: str = ""
    # Board fields, written once the item is on the board.
    fields: dict[str, str] = {}
    # `parent=<n>` is not a board field: it makes the new issue a sub-issue.
    parent: int = 0
    # Not a board field either: the repository's milestone, which is the phase.
    milestone: str = ""
    # Nor is its type, which the organisation defines.
    kind: str = ""
    labels: tuple[str, ...] = ()
    assignees: tuple[str, ...] = ()


def asked(title: str, body: str, values: dict[str, str]) -> Asked:
    if not title.strip():
        raise BoardError("an issue needs a title")
    values = dict(values)
    parent = values.pop("parent", "").strip().lstrip("#")
    if parent and not parent.isdigit():
        raise BoardError(f"parent={parent!r} is not an issue number")
    # Labels and assignees belong to the issue too, so they are given to the
    # create itself, checked first: a label that does not exist then stops it
    # before anything is made, rather than leaving an issue behind without it.
    labels, assignees = (tuple(one.strip() for one in values.pop(name, "").split(",")
                               if one.strip()) for name in ("labels", "assignees"))
    return Asked(title=title, body=body or "", parent=int(parent or 0),
                 milestone=values.pop("milestone", "").strip(),
                 kind=values.pop("type", "").strip(),
                 labels=labels, assignees=assignees, fields=values)


def add(config: Config, store: Store, title: str, body: str,
        values: dict[str, str]) -> int:
    """Create an issue, put it on the board, and set any fields given.

    `parent=<n>` makes it a sub-issue, `milestone=<title>` puts it in that
    phase, `type=<name>` sets its type as the organisation names it, and
    `labels=` and `assignees=` take names separated by commas.
    """
    return add_many(config, store, [asked(title, body, values)])


def add_from(config: Config, store: Store, source: str) -> int:
    """`board add --from <file.json>`: many issues in one run.

    A list of objects, each with a `title`, an optional `body`, and any other
    key a field as `board add` takes it: `{"title": "…", "type": "Task",
    "parent": 12, "labels": ["dev"], "status": "Inbox"}`. `-` reads stdin.
    """
    import json
    import sys

    try:
        text = sys.stdin.read() if source == "-" else open(source, encoding="utf-8").read()
        rows = json.loads(text)
    except (OSError, ValueError) as unreadable:
        raise BoardError(f"could not read {source}: {unreadable}") from None
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise BoardError(f"{source} must hold a list of objects, one per issue")

    def text_of(value: object) -> str:
        if isinstance(value, list):
            return ", ".join(map(str, value))
        return "" if value is None else str(value)

    cards = [asked(text_of(row.get("title")), text_of(row.get("body")),
                   {key: text_of(value) for key, value in row.items()
                    if key not in ("title", "body")})
             for row in rows]
    return add_many(config, store, cards) if cards else 0


def add_many(config: Config, store: Store, cards: list[Asked]) -> int:
    """Create every card in one request, put each on the board, set fields.

    This was about ten `gh` calls per issue, each a process and a round trip:
    labels, the create, `item-add` (several calls of its own), the fields,
    then the type and the parent at three calls each. GraphQL's `createIssue`
    takes labels, assignees, milestone, type, parent and project at once, so
    it is now one lookup of the ids those need, one document creating every
    card, one putting them on the board, one writing every board field, and
    one reading them all back — five calls however many cards.
    """
    number = store.state().project_number or config.require("project")
    ids = lookup(config, number, cards)
    made, refused = create_all(config, ids, cards)
    from . import sync
    sync.created_all(store, [item for _, _, item in made])

    placed = [(issue["number"], item, card.title, card.fields)
              for card, issue, item in made if card.fields and item]
    landed = place_all(store, placed) if placed else {}
    for card, issue, item in made:
        print(f"Created {issue['url']}")
        note("created_issue", url=issue["url"], title=card.title, item=item)
        if not item:
            print("  not on the board: `board set` on it puts it there")
        elif card.fields:
            print(landed[item])
        if card.kind:
            print(f"  #{issue['number']} is a {card.kind}")
        if card.parent:
            print(f"  #{issue['number']} is a sub-issue of #{card.parent}")

    items = [item for _, _, item in made if item]
    if items and not sync.refresh(config, store, items):
        sync.pull(config, store)
    for card, issue, item in made:
        if item:
            remember(store, item, issue["number"], card.title, card.body,
                     card.fields, parent=card.parent)
    for card, why in refused:
        print(f"Not created: {card.title!r}: {why}")
    return 1 if refused else 0


def lookup(config: Config, number: str, cards: list[Asked]) -> dict:
    """Every id the creates need, in one query, and refused before anything
    is made if one of them does not exist.

    Names are checked here rather than left to the create: REST made up any
    label it did not know, and a typo quietly became a new label.
    """
    owner, name = config.repo.split("/", 1)
    labels = sorted({one for card in cards for one in card.labels})
    people = sorted({one for card in cards for one in card.assignees})
    phases = sorted({card.milestone for card in cards if card.milestone})
    parents = sorted({card.parent for card in cards if card.parent})
    parts = ["id",
             *(f"l{i}: label(name: {quoted(one)}) {{ id name }}"
               for i, one in enumerate(labels)),
             *(f"u{i}: assignableUsers(first: 20, query: {quoted(one)}) "
               "{ nodes { id login } }" for i, one in enumerate(people)),
             *(f"m{i}: milestones(first: 20, query: {quoted(one)}, "
               "states: [OPEN, CLOSED]) { nodes { id title } }"
               for i, one in enumerate(phases)),
             *(f"p{i}: issue(number: {one}) {{ id }}" for i, one in enumerate(parents))]
    if any(card.kind for card in cards):
        parts.append("issueTypes(first: 50) { nodes { id name } }")
    query = (f"{{ repository(owner: {quoted(owner)}, name: {quoted(name)}) "
             f"{{ {' '.join(parts)} }} "
             f"owner: repositoryOwner(login: {quoted(config.owner)}) "
             f"{{ ... on ProjectV2Owner {{ projectV2(number: {int(number)}) {{ id }} }} }} }}")
    # Partial, because a parent that does not exist is an error beside data
    # that says which, and that is worth saying in this repository's words.
    data = graphql_partial(query).get("data") or {}
    repository = data.get("repository") or {}
    project = ((data.get("owner") or {}).get("projectV2") or {}).get("id")
    if not repository.get("id"):
        raise BoardError(f"could not read {config.repo}; nothing was created")
    if not project:
        raise BoardError(f"could not read project {number} of {config.owner}; "
                         "nothing was created")

    def found(key: str, wanted: str, attribute: str) -> str:
        nodes = (repository.get(key) or {}).get("nodes") or []
        return next((node["id"] for node in nodes if node
                     and node[attribute].casefold() == wanted.casefold()), "")

    ids = {"repository": repository["id"], "project": project,
           "labels": {one: (repository.get(f"l{i}") or {}).get("id", "")
                      for i, one in enumerate(labels)},
           "assignees": {one: found(f"u{i}", one, "login") for i, one in enumerate(people)},
           "milestones": {one: found(f"m{i}", one, "title") for i, one in enumerate(phases)},
           "parents": {one: (repository.get(f"p{i}") or {}).get("id", "")
                       for i, one in enumerate(parents)},
           "types": {node["name"].casefold(): node["id"] for node in
                     (repository.get("issueTypes") or {}).get("nodes") or [] if node}}
    missing = [*(f"no label {one!r}" for one, i in ids["labels"].items() if not i),
               *(f"no assignable user {one!r}" for one, i in ids["assignees"].items() if not i),
               *(f"no milestone {one!r}" for one, i in ids["milestones"].items() if not i),
               *(f"no issue #{one} to be a parent" for one, i in ids["parents"].items() if not i)]
    kinds = sorted({card.kind for card in cards if card.kind})
    unknown = [one for one in kinds if one.casefold() not in ids["types"]]
    if unknown:
        have = ", ".join(node["name"] for node in
                         (repository.get("issueTypes") or {}).get("nodes") or [] if node)
        missing.append(f"no issue type {', '.join(map(repr, unknown))} "
                       f"(it has: {have or 'none'})")
    if missing:
        raise BoardError(f"{config.repo} has {'; '.join(missing)}; nothing was created")
    return ids


def create_all(config: Config, ids: dict, cards: list[Asked]
               ) -> tuple[list[tuple[Asked, dict, str]], list[tuple[Asked, str]]]:
    """Create every card in one document, typed, labelled and parented as it
    is made; then put them all on the board in a second.

    GraphQL rather than REST's create, which needs no Contents permission
    either — the reason `gh issue create` was dropped — and takes everything
    REST took a call apiece for. Mutations in a document run in order and do
    not roll back, so the answer says which cards were made and which not.

    Not `projectV2Ids` on the create, though it takes them: GitHub adds the
    item behind the answer, which then names no item to write fields to, and
    asking to add it again is refused as already there. Adding by id answers
    with the item, so that is the second document.
    """
    def literal(values) -> str:
        return "[" + ", ".join(map(quoted, values)) + "]"

    parts = []
    for index, card in enumerate(cards):
        given = [f"repositoryId: {quoted(ids['repository'])}",
                 f"title: {quoted(card.title)}", f"body: {quoted(card.body)}"]
        if card.labels:
            given.append(f"labelIds: {literal(ids['labels'][one] for one in card.labels)}")
        if card.assignees:
            given.append("assigneeIds: "
                         + literal(ids["assignees"][one] for one in card.assignees))
        if card.milestone:
            given.append(f"milestoneId: {quoted(ids['milestones'][card.milestone])}")
        if card.kind:
            given.append(f"issueTypeId: {quoted(ids['types'][card.kind.casefold()])}")
        if card.parent:
            given.append(f"parentIssueId: {quoted(ids['parents'][card.parent])}")
        parts.append(f"c{index}: createIssue(input: {{ {', '.join(given)} }}) "
                     "{ issue { id number url } }")
    payload = graphql_partial("mutation { " + " ".join(parts) + " }")
    data = payload.get("data") or {}
    said = {str((error.get("path") or [""])[0]): error.get("message", "")
            for error in payload.get("errors") or []}
    made, refused = [], []
    for index, card in enumerate(cards):
        issue = (data.get(f"c{index}") or {}).get("issue")
        if issue and issue.get("number"):
            made.append((card, issue))
        else:
            refused.append((card, said.get(f"c{index}") or "GitHub created no issue"))
    if not made:
        return [], refused
    adds = " ".join(
        f"a{index}: addProjectV2ItemById(input: {{ projectId: {quoted(ids['project'])}, "
        f"contentId: {quoted(issue['id'])} }}) {{ item {{ id }} }}"
        for index, (_, issue) in enumerate(made))
    try:
        answer = graphql_partial("mutation { " + adds + " }").get("data") or {}
    except BoardError as unplaced:
        # The issues exist; raising would strand them unreported.
        print(f"  created, but not put on the board: {unplaced}")
        answer = {}
    items = [((answer.get(f"a{index}") or {}).get("item") or {}).get("id", "")
             for index in range(len(made))]
    missing = [index for index, item in enumerate(items) if not item]
    if answer and missing:
        # Refused as already there: the board put it on itself first — a
        # sub-issue of a card it holds, by its workflow. Read which item.
        asked_for = " ".join(
            f"n{index}: node(id: {quoted(made[index][1]['id'])}) {{ ... on Issue "
            "{ projectItems(first: 20) { nodes { id project { id } } } } }"
            for index in missing)
        found = graphql_partial("{ " + asked_for + " }").get("data") or {}
        for index in missing:
            nodes = ((found.get(f"n{index}") or {}).get("projectItems") or {}).get("nodes") or []
            items[index] = next((node["id"] for node in nodes if node and
                                 (node.get("project") or {}).get("id") == ids["project"]), "")
    return [(card, issue, item) for (card, issue), item in zip(made, items, strict=True)], refused


def onto_board(config: Config, store: Store, issue: int, url: str) -> None:
    """Put an issue of this repository that the board does not list on it.

    An issue opened in the browser or with `gh` never reaches the board on its
    own, and its fields cannot be set until it is there. `item-add` is
    idempotent, so an issue already on it is left as it is.
    """
    from . import sync
    number = store.state().project_number or config.require("project")
    added = gh_json("project", "item-add", number, "--owner", config.owner,
                    "--url", url, "--format", "json")
    print(f"Added #{issue} to the board")
    note("added_issue", url=url, item=added.get("id"))
    sync.created(store, added.get("id", ""))
    if not sync.refresh(config, store, [added.get("id", "")]):
        sync.pull(config, store)


def promote(config: Config, store: Store, ref: str) -> int:
    """Turn a draft card into an issue, in place.

    Step 2 of the lifecycle the skill describes — an idea that has earned a
    plan becomes an issue — and until now the one step with no command behind
    it. An agent following the policy reached it and could only move the draft
    instead, which is why this board's only draft sat in `Done`.

    In place matters: GitHub converts the card rather than replacing it, so the
    item id survives and every field value with it. Status, effort, area and
    whatever else the board carries are still there afterwards, and the card
    does not jump column. What changes is that it gains a number and a URL, so
    it can be linked from a commit, reviewed, and closed by one.

    Needs the network for the same reason `add` does: only GitHub can mint the
    number, and nothing else in boardkit addresses an issue without it.
    """
    snapshot = store.state()
    item = snapshot.item(ref)
    if item is None:
        raise BoardError(f"{ref} is not on the board")
    if not item.draft:
        raise BoardError(
            f"#{item.ref} is already an issue ({item.url or 'no url recorded'})")
    # The repository's node id, which the mutation needs and the snapshot has
    # no reason to carry: this is the only call that wants it.
    owner, name = config.repo.split("/", 1)
    ask = (f"{{ repository(owner: {quoted(owner)}, name: {quoted(name)}) "
           "{ id } }")
    found = run_json(["gh", "api", "graphql", "-f", f"query={ask}"])
    repository = (found.get("data") or {}).get("repository") or {}
    if not repository.get("id"):
        raise BoardError(f"could not read the node id of {config.repo}")
    convert = ("mutation { convertProjectV2DraftIssueItemToIssue(input: { "
               f"itemId: {quoted(item.id)}, "
               f"repositoryId: {quoted(repository['id'])} }}) "
               "{ item { id content { ... on Issue { number url } } } } }")
    done = run_json(["gh", "api", "graphql", "-f", f"query={convert}"])
    content = (((done.get("data") or {})
                .get("convertProjectV2DraftIssueItemToIssue") or {})
               .get("item") or {}).get("content") or {}
    number, url = content.get("number"), content.get("url", "")
    if not number:
        raise BoardError("GitHub converted the card but named no issue number")
    print(f"Promoted {ref} to #{number} {url}")
    print(f"  fields kept: {item.field(config.status_field)}"
          f"{''.join(f', {name}={value}' for name, value in item.fields.items() if name != config.status_field)}")
    note("promoted", was=ref, ref=str(number), url=url, item=item.id)

    from . import sync
    # The card keeps its item id, so it is re-read in place.
    if not sync.refresh(config, store, [item.id]):
        sync.pull(config, store)
    return 0


def remember(store: Store, item_id: str, issue: int, title: str, body: str,
             values: dict[str, str], parent: int = 0) -> None:
    """Put the new item in the snapshot when the pull did not see it.

    A pull taken moments after creating one does not reliably return it —
    GitHub takes a moment to index — which left the item invisible to every
    read until the next sync, though it was plainly on the board.

    Under the lock, on the snapshot as it is then: several `board add` at once
    each read, changed and wrote it back, and the last one dropped the others'
    items until a sync.
    """
    from .board import ProjectItem

    added: list[ProjectItem] = []

    def change(snapshot):
        seen = next((item for item in snapshot.items if item.id == item_id), None)
        if seen is not None:
            # The pull saw the item but, for the same reason, maybe not the
            # sub-issue link made a moment before it.
            if parent and seen.parent != parent:
                return snapshot.model_copy(update={"items": tuple(
                    item.model_copy(update={"parent": parent}) if item is seen else item
                    for item in snapshot.items)})
            return snapshot
        made = ProjectItem(number=issue, id=item_id, title=title, body=body,
                           parent=parent, type="ISSUE" if issue else "DRAFT_ISSUE",
                           fields=dict(values))
        added.append(made)
        return snapshot.model_copy(update={"items": (*snapshot.items, made)})

    store.update_state(change)
    if added:
        note("remembered", ref=added[0].ref,
             reason="not in the pull that followed creating it")


def place(config: Config, store: Store, issue: int, item_id: str,
          title: str, values: dict[str, str]) -> None:
    """Set the fields a new item was created with, using the id just handed
    back rather than waiting for the board to catch up."""
    print(place_all(store, [(issue, item_id, title, values)])[item_id])


def place_all(store: Store, placed: list[tuple[int, str, str, dict[str, str]]]
              ) -> dict[str, str]:
    """Set every new item's fields in one request; what to say about each.

    By the id just handed back rather than after a pull: a pull straight
    after creating does not reliably list the new item, and the writes were
    rejected for an item that was plainly there.
    """
    from . import sync
    from .board import ProjectItem

    if any(not item_id for _, item_id, _, _ in placed):
        raise BoardError("the board did not return an id for the new item")
    snapshot = store.state()
    items = [ProjectItem(number=issue, id=item_id, title=title,
                         type="DRAFT_ISSUE" if not issue else "ISSUE")
             for issue, item_id, title, _ in placed]
    fresh = snapshot.model_copy(update={"items": (*snapshot.items, *items)})
    writes = {item.id: [Write(ref=item.ref, field=field, value=value)
                        for field, value in values.items()]
              for item, (_, _, _, values) in zip(items, placed, strict=True)}
    kind = "issue" if all(issue for issue, *_ in placed) else "item"
    try:
        landed = {write.id for write in
                  sync.apply_all([w for each in writes.values() for w in each], fresh)}
    except BoardError as refused:
        return {item.id: f"  the {kind} was created; its fields were not: {refused}"
                for item in items}
    said = {}
    for item in items:
        done = [write for write in writes[item.id] if write.id in landed]
        missed = [write for write in writes[item.id] if write.id not in landed]
        for write in done:
            note("pushed", ref=item.ref, field=write.field, value=write.value)
        said[item.id] = "\n".join(filter(None, [
            f"  set {', '.join(f'{w.field}={w.value}' for w in done)}" if done else "",
            f"  not set: {', '.join(f'{w.field}={w.value}' for w in missed)}"
            if missed else ""]))
    return said


def parse(args: list[str]) -> tuple[str, str, dict[str, str], bool]:
    """`board add [--draft] <title> [--body text] [field=value ...]`.

    Fields as `name=value` rather than flags, because which fields exist is
    the board's business and not something this parser can know.
    """
    title_words, body, values, draft = [], "", {}, False
    rest = list(args)
    while rest:
        word = rest.pop(0)
        if word == "--draft":
            draft = True
        elif word == "--body":
            body = rest.pop(0) if rest else ""
        elif "=" in word and not word.startswith("-"):
            name, _, value = word.partition("=")
            values[name] = value
        else:
            title_words.append(word)
    return " ".join(title_words), body, values, draft
