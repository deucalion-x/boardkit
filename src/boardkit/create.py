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

from .config import Config
from .gh import BoardError, gh, gh_json, quoted, run_json
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


def add(config: Config, store: Store, title: str, body: str,
        values: dict[str, str]) -> int:
    """Create an issue, put it on the board, and set any fields given.

    `parent=<n>` is not a board field: it makes the new issue a sub-issue.
    `milestone=<title>` is not one either: it puts the issue in that phase.
    `type=<name>` sets the issue's type, as the organisation names it.
    """
    if not title.strip():
        raise BoardError("an issue needs a title")
    values = dict(values)
    parent = values.pop("parent", "").lstrip("#")
    # Not a board field either: the repository's milestone, which is the phase.
    milestone = values.pop("milestone", "").strip()
    # Nor is its type, which the organisation defines.
    kind = values.pop("type", "").strip()
    # Labels and assignees belong to the issue too, so they are given to the
    # create itself, checked first: a label that does not exist then stops it
    # before anything is made, rather than leaving an issue behind without it.
    labels, assignees = ([one.strip() for one in values.pop(name, "").split(",")
                          if one.strip()] for name in ("labels", "assignees"))
    number = store.state().project_number or config.require("project")

    # The REST endpoint rather than `gh issue create`, which first reads the
    # repository's default branch to look for issue templates. That read needs
    # the Contents permission, which a board's app deliberately lacks, so every
    # create was refused before it began. Creating an issue needs Issues alone.
    fields = [f"title={title}", f"body={body or ''}",
              *(f"labels[]={one}" for one in known_labels(config, labels)),
              *(f"assignees[]={one}" for one in assignees)]
    numbered = ["-F", f"milestone={milestone_number(config, milestone)}"] if milestone else []
    made = gh_json("api", f"repos/{config.repo}/issues", "--method", "POST",
                   *(arg for field in fields for arg in ("-f", field)), *numbered)
    url, issue = made.get("html_url", ""), int(made.get("number") or 0)
    if not issue:
        raise BoardError("GitHub created no issue, or named no number for it")
    added = gh_json("project", "item-add", number, "--owner", config.owner,
                    "--url", url, "--format", "json")
    print(f"Created {url}")
    note("created_issue", url=url, title=title, item=added.get("id"))
    from . import sync
    sync.created(store, added.get("id", ""))

    if values:
        # Set straight from the id `item-add` returns rather than pulling first.
        # A pull immediately after creating does not reliably see the new item
        # — GitHub takes a moment to index it — and the writes were being
        # rejected for an item that was plainly there.
        place(config, store, issue, added.get("id", ""), title, values)

    if kind:
        from .issue import retype
        try:
            retype(config, issue, kind)
            print(f"  #{issue} is a {kind}")
        except BoardError as refused:
            print(f"  the issue was created; its type was not: {refused}")

    if parent:
        from .issue import link
        link(config, issue, int(parent), 0)
        print(f"  #{issue} is a sub-issue of #{parent}")

    if not sync.refresh(config, store, [added.get("id", "")]):
        sync.pull(config, store)
    remember(store, added.get("id", ""), issue, title, body or "", values,
             parent=int(parent) if parent else 0)
    return 0


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


def known_labels(config: Config, labels: list[str]) -> list[str]:
    """The labels, once each is known to exist in the repository.

    The REST endpoint makes up any label it does not know, where `gh issue
    create` refused it; a typo would quietly become a new label. So they are
    checked first, and the create is stopped before anything is made.
    """
    if not labels:
        return labels
    existing = {name.casefold() for name in gh(
        "api", f"repos/{config.repo}/labels", "--paginate", "--jq", ".[].name"
    ).splitlines()}
    missing = [one for one in labels if one.casefold() not in existing]
    if missing:
        raise BoardError(f"no label {', '.join(map(repr, missing))} in {config.repo}; "
                         "nothing was created")
    return labels


def milestone_number(config: Config, title: str) -> int:
    """The number of the milestone with that title, which is what REST takes."""
    found = gh("api", f"repos/{config.repo}/milestones?state=all", "--paginate",
               "--jq", f".[] | select(.title == {quoted(title)}) | .number")
    if not found.strip():
        raise BoardError(f"no milestone {title!r} in {config.repo}; nothing was created")
    return int(found.split()[0])


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
    """
    from .board import ProjectItem

    snapshot = store.state()
    seen = next((item for item in snapshot.items if item.id == item_id), None)
    if seen is not None:
        # The pull saw the item but, for the same reason, maybe not the
        # sub-issue link made a moment before it.
        if parent and seen.parent != parent:
            store.save_state(snapshot.model_copy(update={"items": tuple(
                item.model_copy(update={"parent": parent}) if item is seen else item
                for item in snapshot.items)}))
        return
    made = ProjectItem(number=issue, id=item_id, title=title, body=body, parent=parent,
                       type="ISSUE" if issue else "DRAFT_ISSUE", fields=dict(values))
    store.save_state(snapshot.model_copy(update={"items": (*snapshot.items, made)}))
    note("remembered", ref=made.ref, reason="not in the pull that followed creating it")


def place(config: Config, store: Store, issue: int, item_id: str,
          title: str, values: dict[str, str]) -> None:
    """Set the fields a new issue was created with, using the id just handed
    back rather than waiting for the board to catch up."""
    from . import sync
    from .board import ProjectItem

    snapshot = store.state()
    if not item_id:
        raise BoardError("the board did not return an id for the new item")
    placed = ProjectItem(number=issue, id=item_id, title=title,
                         type="DRAFT_ISSUE" if not issue else "ISSUE")
    fresh = snapshot.model_copy(update={"items": (*snapshot.items, placed)})
    writes = [Write(ref=placed.ref, field=field, value=value)
              for field, value in values.items()]
    try:
        landed = sync.apply_all(writes, fresh)
    except BoardError as refused:
        print(f"  the issue was created; its fields were not: {refused}")
        return
    print(f"  set {', '.join(f'{w.field}={w.value}' for w in landed)}")
    for write in landed:
        note("pushed", ref=placed.ref, field=write.field, value=write.value)


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
