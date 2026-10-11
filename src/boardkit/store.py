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

import contextlib
import datetime
import json
import os
import pathlib
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from . import trail
from .board import ProjectItem
from .config import Config, ProjectField
from .gh import BoardError
from .trail import note

DIRECTORY = ".boardkit"
STATE, PENDING, REJECTED = "state.json", "pending.json", "rejected.json"


def _now() -> float:
    return datetime.datetime.now(datetime.UTC).timestamp()


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
    # Where a person goes to look at this board. Empty on a snapshot taken
    # before it was carried, which prints nothing rather than a wrong link.
    project_url: str = ""
    project_number: str = ""
    synced_at: str = ""
    fields: tuple[ProjectField, ...] = ()
    # Saved tab names, so a tab deleted in the browser is noticed. Only the
    # names: what a tab shows is a UI setting boardkit does not read back.
    views: tuple[str, ...] = ()
    items: tuple[ProjectItem, ...] = ()
    # Items on the board whose issue is closed. Apart from `items` so that
    # listing, counting and writing keep meaning open work; reads about done
    # work look here too.
    closed: tuple[ProjectItem, ...] = ()

    def known(self, ref: str | int) -> ProjectItem | None:
        """Open or closed: for reading about an item, never for writing."""
        wanted = str(ref)
        return self.item(ref) or next((i for i in self.closed if i.ref == wanted), None)

    def item(self, ref: str | int) -> ProjectItem | None:
        """By issue number or by a draft's handle; both arrive as text from a
        command line, and an issue number is accepted either way."""
        wanted = str(ref)
        return next((i for i in self.items if i.ref == wanted), None)

    def field(self, name: str) -> ProjectField | None:
        return next((f for f in self.fields if f.name == name), None)

    @property
    def empty(self) -> bool:
        return not self.synced_at

    @property
    def age(self) -> float:
        """Seconds since the board last confirmed this, or `inf` if never.

        One number for the whole snapshot, because one pull is what produced
        it. Refreshing a single item into it would make this a half-truth, so
        nothing does.
        """
        if not self.synced_at:
            return float("inf")
        try:
            stamped = datetime.datetime.fromisoformat(self.synced_at)
        except ValueError:
            # A hand-edited or older file should go stale, not crash a read.
            return float("inf")
        if stamped.tzinfo is None:
            stamped = stamped.replace(tzinfo=datetime.UTC)
        return max(0.0, datetime.datetime.now(datetime.UTC).timestamp()
                   - stamped.timestamp())


class Write(BaseModel):
    """One local change, waiting to be pushed.

    `was` is the value the snapshot held when the change was made. The sync
    compares it against the remote value to tell a clean push from a conflict,
    so a write is only ever applied to the state it was decided against.
    """
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    at: str = Field(default_factory=now)
    # The item's ref — an issue number as text, or a draft's handle. Drafts
    # have no number, so a number cannot be what names a write.
    ref: str
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
    reason: str = ""


# A lock held longer than this is assumed to belong to a process that died.
# Generous, because a slow pull of a large board is minutes of legitimate work.
STALE_LOCK = 15 * 60


class Busy(BoardError):
    """Another boardkit process holds the lock."""


class Store:
    """Reads and writes the three files. Creates nothing until first written."""

    def __init__(self, config: Config):
        self.config = config
        self.directory = config.path(DIRECTORY)
        # Refusals are recorded beside the snapshot rather than printed and
        # lost, so an unexpected limit is noticed rather than reasoned about.
        trail.configure(self.directory / "log.jsonl")

    def _read(self, name: str, default: str) -> Any:
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

    @contextlib.contextmanager
    def held(self):
        """Exclusive access for the length of a change.

        Agents are the intended caller, so two processes at once is the normal
        case rather than the exotic one — and `pending.json` is read, extended
        and written back, which interleaves badly. A lock file created with
        O_EXCL is enough: it is one machine and one directory.
        """
        lock = directory(self.config) / "lock"
        try:
            handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            age = _now() - lock.stat().st_mtime if lock.exists() else 0
            if age < STALE_LOCK:
                raise Busy(
                    f"another boardkit process is working (lock held {int(age)}s). "
                    f"If nothing else is running, delete {lock}.") from None
            # Older than any real run: whoever held it is gone.
            lock.unlink(missing_ok=True)
            handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(handle, str(os.getpid()).encode())
            os.close(handle)
            yield
        finally:
            lock.unlink(missing_ok=True)

    # pending -------------------------------------------------------------
    def pending(self) -> list[Write]:
        return [Write.model_validate(row) for row in self._read(PENDING, "[]")]

    def save_pending(self, writes: list[Write]) -> None:
        self._write(PENDING, [write.model_dump(mode="json") for write in writes])

    def queue(self, ref: str, field: str, value: str) -> Write:
        """Record a local change, replacing any still-pending write to the
        same field.

        Setting a field twice before anything is pushed means one change, not
        two: the second is what the agent decided, and pushing both would send
        a value nobody wants followed by the one they do. The earlier write's
        `was` is kept, because that is still the value GitHub last showed us
        and so the one a conflict must be judged against.
        """
        item = self.state().item(ref)
        with self.held():
            standing = self.pending()
            earlier = next((w for w in standing
                            if w.ref == ref and w.field == field), None)
            write = Write(ref=ref, field=field, value=value,
                          was=earlier.was if earlier else
                          (item.field(field) if item else ""))
            self.save_pending([*(w for w in standing if w is not earlier), write])
        note("queued", id=write.id, ref=ref, field=field,
             value=value, was=write.was, replaced=earlier.id if earlier else None)
        return write

    # rejected ------------------------------------------------------------
    def rejected(self) -> list[Rejection]:
        return [Rejection.model_validate(row) for row in self._read(REJECTED, "[]")]

    def save_rejected(self, rejections: list[Rejection]) -> None:
        self._write(REJECTED, [row.model_dump(mode="json") for row in rejections])

    def reject(self, write: Write, remote: str, reason: str = "") -> Rejection:
        """Record a loss. Recording the same write twice is a no-op, so a crash
        between writing rejections and clearing the queue costs a repeat of the
        work and not a duplicate entry."""
        rejection = Rejection(write=write, remote=remote, reason=reason)
        standing = self.rejected()
        if any(kept.write.id == write.id for kept in standing):
            return rejection
        self.save_rejected([*standing, rejection])
        note("rejected", id=write.id, ref=write.ref, field=write.field,
             wanted=write.value, was=write.was, remote=remote, reason=reason)
        return rejection

    def clear_rejected(self) -> int:
        count = len(self.rejected())
        self.save_rejected([])
        note("cleared_rejections", count=count)
        return count

    def history(self, limit: int = 50) -> list[dict]:
        """The last few things boardkit did, newest last."""
        return trail.read(limit)

    def ignore_file(self) -> pathlib.Path:
        return directory(self.config) / ".gitignore"
