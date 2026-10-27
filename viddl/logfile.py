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
import shlex
import sys
from pathlib import Path
from typing import Optional, Sequence

from . import __version__, appdata
from .util import REDACTED, redact

#: How large the log may grow before it rolls over, and how many rolls are kept.
LOG_MAX_BYTES = 256 * 1024
BACKUP_COUNT = 1

#: How much of a tool's own words one failure record keeps, and how long one
#: line of them may be. The count matches the failure screen's forty lines: the
#: log is read for the same question, by someone with a pager instead of a
#: panel. A line may run longer here, because it is read in a pager — but not
#: without end, or one JSON blob would be the whole record.
DETAIL_LINES = 40
DETAIL_WIDTH = 500

#: The logger the rest of Viddl writes to (``tui.app`` uses the same name).
LOGGER_NAME = "viddl"

__all__ = [
    "BACKUP_COUNT",
    "DETAIL_LINES",
    "DETAIL_WIDTH",
    "LOGGER_NAME",
    "LOG_MAX_BYTES",
    "REDACTED",
    "OwnerOnlyRotatingFileHandler",
    "RedactingFormatter",
    "enable",
    "failure",
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


def _command_line(command: Sequence[str]) -> str:
    """One line that could be pasted into a terminal to try the same thing.

    Quoted the way a shell would want it, which is also how it becomes readable:
    a link with a ``?`` in it is one argument rather than the start of something
    the reader has to reassemble.
    """
    return shlex.join([str(part) for part in command])


def _readable(line: str) -> str:
    """One line of a tool's output, safe to write down and to read back.

    Control characters go, exactly as they do on the failure screen: a tool can
    quote a video's name, and the log is read in a terminal — where an ESC in the
    file is a sequence the terminal obeys rather than four characters it draws.
    """
    cleaned = "".join(
        character if character.isprintable() or character == "\t" else " "
        for character in str(line)
    )
    return cleaned[:DETAIL_WIDTH]


def failure(
    summary: str,
    *,
    command: Sequence[str] = (),
    exit_code: Optional[int] = None,
    detail: str = "",
    logger: Optional[logging.Logger] = None,
) -> None:
    """Write one failure down: what was attempted, how it ended, what it said.

    Called from the places that know the command line and the exit code — the
    download runner, the two look-ups, the engine's own installs — because that
    is the part the screen does not show and a bug report cannot supply. A run
    that worked writes nothing: the log is read after something has gone wrong,
    and a line per download would bury the one that mattered.

    Everything goes through the formatter, so the redaction S4 installed covers
    the command line as well as the output. Nothing here can change what the
    user sees, so a failure while logging is swallowed: the log is a nicety, and
    the run has already failed.
    """
    try:
        lines = [f"{summary} (Viddl {__version__} on {sys.platform})"]
        if command:
            lines.append("  ran: " + _command_line(command))
        if exit_code is not None:
            lines.append(f"  exit code: {exit_code}")
        said = [line for line in str(detail or "").splitlines() if line.strip()]
        if said:
            # The last lines, for the same reason the screen shows the last
            # ones: a tool explains itself when it gives up.
            lines.append("  said:")
            lines.extend("    " + _readable(line) for line in said[-DETAIL_LINES:])
        (logger or logging.getLogger(LOGGER_NAME)).error("\n".join(lines))
    except Exception:  # pragma: no cover - a log must never be the failure
        pass


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
