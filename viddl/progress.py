"""The progress line yt-dlp prints, read as fields instead of as text.

The download asks the engine for one machine-readable line per update
(:data:`TEMPLATE`) and shows what is in it. This module owns that line: the
marker that finds it, the field order, and the rules that turn a field yt-dlp
fills with ``NA`` — or with a number that is not a number — into something a
screen can show without raising.

It is kept apart from the download job because the line is a small, complete
thing: :class:`ProgressParser` can be handed any string and answers with a
:class:`Reading`, with no subprocess, folder or thread involved. That is also
what makes the arithmetic testable on its own — the job only has to decide what
to do with the answer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

#: The word that starts a progress line. yt-dlp prints the template below with
#: the ``download:`` protocol prefix removed, so this is the first thing on the
#: line and nothing else on a normal run looks like it.
MARKER = "VIDDLPROGRESS"

#: The one line per update, in field order. ``NA`` means "not known yet", and
#: the two byte counts are separate so an estimate can be told from a stated
#: size (U8).
TEMPLATE = (
    "download:" + MARKER + "|"
    "%(progress._percent)s|"
    "%(progress._speed)s|"
    "%(progress._eta)s|"
    "%(progress.downloaded_bytes)s|"
    "%(progress.total_bytes)s|"
    "%(progress.total_bytes_estimate)s|"
    "%(progress.filename)s"
)

#: How many fields the line holds, so a short one is padded rather than sliced
#: into the wrong place.
FIELDS = 7


def _to_float(value: str) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    # ``"nan"`` and ``"inf"`` are floats to Python and nothing like a progress
    # figure: ``int(inf)`` raises ``OverflowError``, which used to escape the
    # progress parser and end a download that was otherwise fine. The quality
    # list already rejects them the same way (``formats._num``).
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _to_int(value: str) -> Optional[int]:
    number = _to_float(value)
    return int(number) if number is not None else None


def _fmt_speed(bits_per_second: Optional[float]) -> str:
    from .util import human_size

    if not bits_per_second or bits_per_second <= 0:
        return ""
    return f"{human_size(bits_per_second)}/s"


def _fmt_eta(seconds: Optional[float]) -> str:
    from .util import human_duration

    if seconds is None or seconds <= 0:
        return ""
    return human_duration(seconds)


@dataclass(frozen=True)
class Reading:
    """One progress line, as the words and numbers a screen shows.

    Frozen because it is handed to a callback that may hop to another thread:
    what was reported cannot change under the reader's feet.
    """

    percent: Optional[float] = None
    speed: str = ""
    eta: str = ""
    downloaded: int = 0
    total: Optional[int] = None
    total_is_estimate: bool = False
    filename: str = ""


class ProgressParser:
    """Reads the progress lines this program asked yt-dlp to print."""

    def __init__(self, marker: str = MARKER) -> None:
        self.marker = marker

    def handles(self, line: str) -> bool:
        """Whether this line is one of ours rather than a line to explain."""
        return line.startswith(self.marker)

    def parse(self, line: str) -> Reading:
        """The fields in ``line``, with anything unusable left at its default.

        A field that is missing, ``NA``, or not a finite non-negative number is
        not an error and never raises: the download is still running, and one
        figure it could not report is not a reason to end it.
        """
        payload = line[len(self.marker) :].lstrip("|")
        parts = payload.split("|")
        while len(parts) < FIELDS:
            parts.append("NA")
        known_total = _to_int(parts[4])
        total = known_total if known_total else _to_int(parts[5])
        filename = parts[6].strip()
        return Reading(
            percent=_to_float(parts[0]),
            speed=_fmt_speed(_to_float(parts[1])),
            eta=_fmt_eta(_to_float(parts[2])),
            downloaded=_to_int(parts[3]) or 0,
            total=total,
            # An estimate is the figure that can be replaced halfway through,
            # so which of the two it was is kept (U8).
            total_is_estimate=not known_total and bool(total),
            filename="" if filename == "NA" else filename,
        )
