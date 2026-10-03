"""The local working copy agents read instead of calling the API.

Three files under `.boardkit/` at the repository root, gitignored because this
is a cache and not a source: committing it would mean merge conflicts on a
generated file and board state mixed into code commits.

    state.json      last synced snapshot of the project
    pending.json    local writes not pushed yet
    rejected.json   writes that lost to a remote change, with both values

Three files rather than one because their lifetimes differ: `state` is replaced
wholesale by a pull, `pending` drains, and `rejected` accumulates until an
agent reads it.

Nothing here talks to GitHub. The sync does that; this module only persists.
"""
from __future__ import annotations

import datetime
import json
import pathlib
import uuid

from pydantic import BaseModel, ConfigDict, Field

from .board import ProjectItem
from .config import Config, ProjectField

DIRECTORY = ".boardkit"
STATE, PENDING, REJECTED = "state.json", "pending.json", "rejected.json"


def now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


def directory(config: Config) -> pathlib.Path:
    """`.boardkit/`, created with a `.gitignore` that excludes it.

    Everything that writes here goes through this, so the cache excludes
    itself and the consuming repository never has to remember to.
    """
    path = config.path(DIRECTORY)
    path.mkdir(parents=True, exist_ok=True)
    ignore = path / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n", encoding="utf-8")
    return path


class Snapshot(BaseModel):
    """What the project looked like at the last pull.

    It carries ids, not just names: `gh project item-edit` needs the item id,
    the field id, and for a single select the option id, so a push that reads
    only this file has to find all three here.
    """
    model_config = ConfigDict(extra="forbid")

    # Discovered, not declared: config names the project by title, and this is
    # what that resolved to. Keeping it here rather than in `pyproject.toml`
    # means nothing hand-maintained holds a value only GitHub can produce.
    project_id: str = ""
    project_number: str = ""
    synced_at: str = ""
    fields: tuple[ProjectField, ...] = ()
    items: tuple[ProjectItem, ...] = ()

    def item(self, number: int) -> ProjectItem | None:
        return next((i for i in self.items if i.number == number), None)

    def field(self, name: str) -> ProjectField | None:
        return next((f for f in self.fields if f.name == name), None)

    @property
    def empty(self) -> bool:
        return not self.synced_at


class Write(BaseModel):
    """One local change, waiting to be pushed.

    `was` is the value the snapshot held when the change was made. The sync
    compares it against the remote value to tell a clean push from a conflict,
    so a write is only ever applied to the state it was decided against.
    """
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    at: str = Field(default_factory=now)
    number: int
    field: str
    value: str
    was: str = ""
    author: str = "agent"


class Rejection(BaseModel):
    """A write that lost, kept with both values so an agent can re-decide.

    Remote is the source of truth and nothing is dropped silently: the local
    change does not land, but it is not thrown away either.
    """
    model_config = ConfigDict(extra="forbid")

    write: Write
    remote: str
    at: str = Field(default_factory=now)
    reason: str = "the field changed on GitHub after this write was made"


class Store:
    """Reads and writes the three files. Creates nothing until first written."""

    def __init__(self, config: Config):
        self.config = config
        self.directory = config.path(DIRECTORY)

    def _read(self, name: str, default: str) -> object:
        path = self.directory / name
        if not path.exists():
            return json.loads(default)
        return json.loads(path.read_text(encoding="utf-8"))

    def _write(self, name: str, payload: object) -> None:
        path = directory(self.config) / name
        # Written beside the target and moved, so a killed sync cannot leave a
        # half-written file that no longer parses.
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    # state ---------------------------------------------------------------
    def state(self) -> Snapshot:
        return Snapshot.model_validate(self._read(STATE, "{}"))

    def save_state(self, snapshot: Snapshot) -> None:
        self._write(STATE, snapshot.model_dump(mode="json"))

    # pending -------------------------------------------------------------
    def pending(self) -> list[Write]:
        return [Write.model_validate(row) for row in self._read(PENDING, "[]")]

    def save_pending(self, writes: list[Write]) -> None:
        self._write(PENDING, [write.model_dump(mode="json") for write in writes])

    def queue(self, number: int, field: str, value: str) -> Write:
        """Record a local change. The snapshot supplies `was`, so a later push
        can tell whether the ground moved underneath it."""
        item = self.state().item(number)
        write = Write(number=number, field=field, value=value,
                      was=item.field(field) if item else "")
        self.save_pending([*self.pending(), write])
        return write

    # rejected ------------------------------------------------------------
    def rejected(self) -> list[Rejection]:
        return [Rejection.model_validate(row) for row in self._read(REJECTED, "[]")]

    def save_rejected(self, rejections: list[Rejection]) -> None:
        self._write(REJECTED, [row.model_dump(mode="json") for row in rejections])

    def reject(self, write: Write, remote: str) -> Rejection:
        rejection = Rejection(write=write, remote=remote)
        self.save_rejected([*self.rejected(), rejection])
        return rejection

    def clear_rejected(self) -> int:
        count = len(self.rejected())
        self.save_rejected([])
        return count

    def ignore_file(self) -> pathlib.Path:
        return directory(self.config) / ".gitignore"
