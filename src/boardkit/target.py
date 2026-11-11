"""Which board a write goes to, and the check that it is this repository's.

A board is named by its owner and number, and a number means nothing without
its owner: an organisation's project 1 is not the user's project 1. boardkit
once put seven issues on another team's board because a snapshot kept from
the repository's old, user-owned board still said "project 1", and the
create path asked the snapshot before the config. GitHub did not stop it,
because the organisation's app may write to every board of the organisation.

So two rules, both here:

- The snapshot is a cache of the board the config names. One taken from
  another board (another owner, number or repository) is discarded before
  anything reads it.
- A write goes only to the board linked to `config.repo`. That is checked
  before any mutation of the board, once, and kept in the snapshot.
"""
from __future__ import annotations

import sys

from .config import Config
from .gh import BoardError, graphql_partial, quoted
from .store import Snapshot, Store
from .trail import note


def snapshot_owner(snapshot: Snapshot) -> str:
    """The owner a snapshot was taken from: recorded, or read off its URL, which
    snapshots carried before the owner was."""
    if snapshot.owner:
        return snapshot.owner
    parts = snapshot.project_url.rstrip("/").split("/")
    return parts[-3] if len(parts) >= 3 and parts[-2] == "projects" else ""


def mismatch(config: Config, snapshot: Snapshot) -> list[str]:
    """How a snapshot's board differs from the one config names; empty when it
    is that board, or when there is no snapshot yet."""
    if not (snapshot.project_id or snapshot.project_number):
        return []
    said = []
    if snapshot.repo and snapshot.repo.casefold() != config.repo.casefold():
        said.append(f"repository {snapshot.repo}, not {config.repo}")
    owner = snapshot_owner(snapshot)
    if owner and owner.casefold() != config.owner.casefold():
        said.append(f"owner {owner}, not {config.owner}")
    if config.project and snapshot.project_number \
            and snapshot.project_number != config.project:
        said.append(f"project {snapshot.project_number}, not {config.project}")
    return said


def forget_other(config: Config, store: Store) -> None:
    """Discard a snapshot of a board other than the one config names.

    Before any read or write, so nothing it holds (item ids, the project id,
    the kept ids `board add` names things by) reaches GitHub on behalf of
    the wrong board. The next read pulls the right one.
    """
    said = mismatch(config, store.state())
    if not said:
        return
    store.update_state(lambda current: Snapshot() if mismatch(config, current) else current)
    print(f"boardkit: the local snapshot was of another board ({'; '.join(said)}); "
          f"discarded it. The next read pulls the board config names.", file=sys.stderr)
    note("snapshot_discarded", said=said)


def key(config: Config) -> str:
    return f"{config.repo}|{config.owner}/{config.project}".casefold()


def linked_id(config: Config) -> tuple[str, list[str]]:
    """The id of the board config names if `config.repo` lists it among its
    linked projects, and every board it does list, for the refusal."""
    owner, name = config.repo.split("/", 1)
    query = (f"{{ repository(owner: {quoted(owner)}, name: {quoted(name)}) "
             "{ projectsV2(first: 100) { nodes { id number url "
             "owner { ... on Organization { login } ... on User { login } } } } } }")
    data = graphql_partial(query).get("data") or {}
    nodes = [node for node in
             (((data.get("repository") or {}).get("projectsV2") or {}).get("nodes") or [])
             if node]
    found = next((node["id"] for node in nodes
                  if str(node.get("number")) == config.project
                  and ((node.get("owner") or {}).get("login") or "").casefold()
                  == config.owner.casefold()), "")
    return found, [node.get("url", "") for node in nodes]


def check(config: Config, store: Store, project_id: str = "") -> str:
    """The id of the board to write to, once it is known to be this
    repository's; `project_id`, when given, must be that board.

    Refused, before anything is written, when config names no board, when
    that board is not linked to `config.repo`, or when the id a write
    carries is another board's. The link is looked up once and kept in the
    snapshot, keyed by repository and board, so later writes cost nothing.
    """
    if not config.project:
        raise BoardError(f"{config.repo} names no board yet; `board admin apply` makes "
                         "one. Nothing was written.")
    snapshot = store.state()
    cached, _, known = snapshot.linked.rpartition("|")
    if cached != key(config) or not known:
        known, listed = linked_id(config)
        if not known:
            board = config.project_url or f"{config.owner}'s project {config.project}"
            others = f" It is linked to: {', '.join(listed)}." if listed else ""
            raise BoardError(
                f"{board} is not linked to {config.repo}, and boardkit only writes to a "
                f"board linked to its repository.{others} Run `board admin link --as-me` "
                "(or link it in the board's settings). Nothing was written.")
        store.update_state(lambda current: current.model_copy(
            update={"linked": f"{key(config)}|{known}"}))
    if project_id and project_id != known:
        note("refused_other_board", project=project_id, linked=known)
        raise BoardError(
            f"refused: this write was for project id {project_id}, not "
            f"{config.project_url or config.project}, the board linked to {config.repo}. "
            "Nothing was written; `board sync` re-reads the board.")
    return known
