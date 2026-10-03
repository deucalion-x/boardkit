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
from .gh import BoardError, gh, gh_json
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
    if values:
        place(config, store, 0, made.get("id", ""), title, values)
    sync.pull(config, store)
    remember(store, made.get("id", ""), 0, title, body or "", values)
    return 0


def add(config: Config, store: Store, title: str, body: str,
        values: dict[str, str]) -> int:
    """Create an issue, put it on the board, and set any fields given."""
    if not title.strip():
        raise BoardError("an issue needs a title")
    number = store.state().project_number or config.require("project")

    made = gh("issue", "create", "--repo", config.repo, "--title", title,
              "--body", body or "")
    url = made.strip().splitlines()[-1]
    added = gh_json("project", "item-add", number, "--owner", config.owner,
                    "--url", url, "--format", "json")
    issue = int(url.rstrip("/").rsplit("/", 1)[-1])
    print(f"Created {url}")
    note("created_issue", url=url, title=title, item=added.get("id"))

    if values:
        # Set straight from the id `item-add` returns rather than pulling first.
        # A pull immediately after creating does not reliably see the new item
        # — GitHub takes a moment to index it — and the writes were being
        # rejected for an item that was plainly there.
        place(config, store, issue, added.get("id", ""), title, values)

    from . import sync
    sync.pull(config, store)
    remember(store, added.get("id", ""), issue, title, body or "", values)
    return 0


def remember(store: Store, item_id: str, issue: int, title: str, body: str,
             values: dict[str, str]) -> None:
    """Put the new item in the snapshot when the pull did not see it.

    A pull taken moments after creating one does not reliably return it —
    GitHub takes a moment to index — which left the item invisible to every
    read until the next sync, though it was plainly on the board.
    """
    from .board import ProjectItem

    snapshot = store.state()
    if any(item.id == item_id for item in snapshot.items):
        return
    made = ProjectItem(number=issue, id=item_id, title=title, body=body,
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
