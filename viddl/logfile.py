"""The one file Viddl writes for the sake of the next bug report.

A log is only read after something has gone wrong, which is exactly when what
it holds matters. Two rules follow, and both are enforced here rather than left
to whoever writes the next ``LOGGER.error``:

* **It is the user's business alone.** The file is ``0o600`` when Viddl makes
  it, an older file is tightened, and every rotation comes out the same way
  instead of inheriting the umask.
* **It does not keep what it does not need.** Every line is passed through
  :func:`viddl.util.redact` as it is written, so links lose the query
  parameters that carry credentials and signatures.

Nothing here is required to download a video. A folder that cannot be written
to turns logging off rather than failing the run — a nicety must not become a
requirement.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path
from typing import Optional

from . import appdata
from .util import REDACTED, redact

#: How large the log may grow before it rolls over, and how many rolls are kept.
LOG_MAX_BYTES = 256 * 1024
BACKUP_COUNT = 1

#: The logger the rest of Viddl writes to (``tui.app`` uses the same name).
LOGGER_NAME = "viddl"

__all__ = [
    "BACKUP_COUNT",
    "LOG_MAX_BYTES",
    "LOGGER_NAME",
    "OwnerOnlyRotatingFileHandler",
    "REDACTED",
    "RedactingFormatter",
    "enable",
]

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class OwnerOnlyRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """A rotating log that is owner-only from the first byte to the last.

    The standard handler opens each new file the way ``open`` does, which leaves
    the mode to the umask — so setting ``0o600`` once at start-up would be
    quietly undone by the first rotation, and the second log file would be
    world-readable. This creates the file itself, at the mode it should have.
    """

    def _open(self):
        descriptor = os.open(
            self.baseFilename, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
        )
        stream = os.fdopen(descriptor, "a", encoding=self.encoding)
        # Rotation is decided from the stream's position, so an existing log has
        # to be measured from its end — otherwise a file that was already too
        # large would never roll over and would just keep growing.
        stream.seek(0, os.SEEK_END)
        return stream


class RedactingFormatter(logging.Formatter):
    """Format a record and then take the secrets back out of it.

    Redacting the finished line, rather than the message and its arguments
    separately, is what covers the traceback: an exception raised while fetching
    a link carries that link in its text, and a traceback is the line a log is
    usually asked for.
    """

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def enable(
    logger: Optional[logging.Logger] = None,
    path: Optional[Path] = None,
) -> Optional[Path]:
    """Start writing ``logger`` to the log file; return where, or ``None``.

    ``None`` means the log is not available — a read-only folder, a missing
    permission — and the caller should carry on regardless. Setting up twice is
    a no-op, so a second call cannot end up writing every line twice.
    """
    logger = logger or logging.getLogger(LOGGER_NAME)
    if logger.handlers:
        return None

    target = path or appdata.secure_log_file()
    if target is None:
        return None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        handler = OwnerOnlyRotatingFileHandler(
            target,
            maxBytes=LOG_MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
    except OSError:
        return None

    handler.setFormatter(RedactingFormatter(_LOG_FORMAT))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return target
