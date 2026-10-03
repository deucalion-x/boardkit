"""What boardkit did, kept on disk so a surprise can be read rather than guessed.

One line of JSON per action — queued, pushed, rejected, pulled, created — in
`.boardkit/log.jsonl`, rotated by the standard library so it cannot grow
without bound. It is a debugging and audit record, not state: nothing reads it
back to decide anything, and deleting it loses nothing but the history.

It exists because the alternative is reasoning about a paraphrase. This package
spent an afternoon treating a spent API budget as a request burst, because the
message had been reworded on the way out and the numbers came from an endpoint
that reports a different figure. Refusals now land here verbatim.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import pathlib
import time

# One namespace under the package, so a host application can raise, lower or
# silence everything boardkit records with `logging.getLogger("boardkit")` and
# no boardkit-specific API.
NAME = "boardkit.trail"
# Set to a path to write somewhere else, or to one of these to write nowhere.
# An env var rather than a setting, because turning a log off is a property of
# the run — one CI job, one debugging session — not of the repository.
OFF = ("", "0", "off", "none", "no", "false")
ENV = "BOARDKIT_LOG"
# Small enough to read, large enough to cover a long session: a few thousand
# actions across three files.
MAX_BYTES = 256 * 1024
KEEP = 2

_path: pathlib.Path | None = None
_opened: pathlib.Path | None = None

# A library attaches no handler of its own: without this, a host that has not
# configured logging would see "No handlers could be found" for our records.
# The package logger carries it, so every `boardkit.*` logger inherits it.
logging.getLogger("boardkit").addHandler(logging.NullHandler())
# Our records are INFO; say so here rather than leaving them at the root's
# level, which is WARNING by default and would drop every one of them.
logging.getLogger(NAME).setLevel(logging.INFO)


def configure(path: pathlib.Path) -> None:
    """Name the file. Nothing is created until there is something to write.

    Deferred because reading a board must leave no trace: a repo that has never
    synced has no `.boardkit/`, and opening a log would quietly make one.

    `BOARDKIT_LOG` overrides the choice: unset is this path, a path is that one
    instead, and `off` is no file at all — the records are still emitted, so
    anything that has configured the `boardkit` logger still receives them.
    """
    global _path
    chosen = os.environ.get(ENV)
    if chosen is None:
        _path = path
    elif chosen.strip().lower() in OFF:
        _path = None
    else:
        _path = pathlib.Path(chosen).expanduser()


def opened() -> logging.Logger | None:
    """The logger, attached to its file on first use."""
    global _opened
    logger = logging.getLogger(NAME)
    if _path is None:
        return None
    if _opened != _path:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
        try:
            _path.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(
                _path, maxBytes=MAX_BYTES, backupCount=KEEP, encoding="utf-8")
        except OSError:
            # A trail that cannot be opened is not worth failing a command over.
            return None
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        _opened = _path
    return logger


def note(action: str, **detail) -> None:
    """Record one action.

    Always emitted on the `boardkit.trail` logger, so a host that captures that
    namespace sees it whether or not boardkit is keeping a file. The file is
    attached here when one has been named, which is what makes the record
    survive the process.
    """
    opened()
    entry = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "action": action, **detail}
    logging.getLogger(NAME).info(json.dumps(entry), extra={"boardkit": entry})


def read(limit: int = 50) -> list[dict]:
    """The last few entries, newest last. Only the current file: a rotated one
    is for reading by hand."""
    if _path is None or not _path.exists():
        return []
    lines = _path.read_text(encoding="utf-8").splitlines()
    found = []
    for line in lines[-limit:]:
        try:
            found.append(json.loads(line))
        except ValueError:
            continue
    return found
