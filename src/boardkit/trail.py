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
import pathlib
import time

NAME = "boardkit.trail"
# Small enough to read, large enough to cover a long session: a few thousand
# actions across three files.
MAX_BYTES = 256 * 1024
KEEP = 2

_path: pathlib.Path | None = None
_opened: pathlib.Path | None = None


def configure(path: pathlib.Path) -> None:
    """Name the file. Nothing is created until there is something to write.

    Deferred because reading a board must leave no trace: a repo that has never
    synced has no `.boardkit/`, and opening a log would quietly make one.
    """
    global _path
    _path = path


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
        logger.setLevel(logging.INFO)
        # Ours alone: a consuming application's logging configuration is not
        # ours to inherit from or to write into.
        logger.propagate = False
        _opened = _path
    return logger


def note(action: str, **detail) -> None:
    """Record one action. Silent when no trail has been named."""
    logger = opened()
    if logger is None:
        return
    entry = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "action": action, **detail}
    logger.info(json.dumps(entry))


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
