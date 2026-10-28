"""Tiny formatting helpers used by both the terminal UI and the CLI."""

from __future__ import annotations

import math
import re
from typing import Optional, TextIO
from urllib.parse import urlsplit

# Long enough for any real link, short enough to keep an odd paste out of argv.
_MAX_LINK_LENGTH = 2048

# Query parameters whose value is a credential rather than content. Redaction
# works from this fixed list rather than from guessing at what looks secret: a
# link that comes out the other side is still a link, and a name missing here is
# a name we chose not to treat as one. Add to it when a site needs it — the cost
# of a miss is a line in a log only its owner can read, whereas the cost of
# over-redacting is a log nobody can debug from.
SECRET_PARAMS = (
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "hdntl",
    "hdnts",
    "id_token",
    "jwt",
    "key",
    "oauth_token",
    "password",
    "passwd",
    "pwd",
    "refresh_token",
    "secret",
    "session",
    "session_id",
    "sessionid",
    "sig",
    "signature",
    "token",
    "x-signature",
)

# What stands in for a value we will not write down. Readable on purpose: a
# redacted line should still show what was being fetched and from where.
REDACTED = "<redacted>"

_NAMES = "|".join(sorted((re.escape(name) for name in SECRET_PARAMS), key=len, reverse=True))
# A name only counts where a query would put one: after ``?``, after ``&``, or
# after either of those once percent-encoded, which is how the inner link of a
# ``viddl://`` hand-off arrives. Matching the separator too is what keeps the
# filter off text that merely looks like ``key=value``.
_SECRET_VALUE = re.compile(
    r"(?i)(^|[?&]|%3F|%26)(" + _NAMES + r")(=|%3D)([^&\s\"'<>#]*)"
)
_CREDENTIALS_IN_LINK = re.compile(r"(?i)(https?://)[^/?#@\s\"'<>]+@")
_BEARER = re.compile(r"(?i)\b(bearer\s+)([A-Za-z0-9._~+/=-]{8,})")


def is_interactive(stream: TextIO) -> bool:
    """Whether ``stream`` is a terminal that can be written over or asked on.

    Asked of the stream that is actually used, rather than of "am I running
    interactively": a download redirected to a log has a terminal on standard
    input and a file on standard error, and it is the file that decides whether
    a progress line may be redrawn (U4). A stream without ``isatty`` — a test
    double, ``--quiet``'s sink — is not one, because the safe answer for
    anything that is not known to be a terminal is "no".
    """
    try:
        return bool(stream.isatty())
    except (AttributeError, OSError):  # pragma: no cover - unusual streams
        return False


def human_size(num_bytes: Optional[float]) -> str:
    """``12345678`` -> ``"12.4 MB"``; unknown sizes become ``"?"``.

    A size the metadata gave as ``nan`` or ``inf`` is unknown just like a
    missing one (E9): those parse as floats, which is why they reached the
    quality list and were shown as ``nan GB``.
    """
    if num_bytes is None:
        return "?"
    size = float(num_bytes)
    if not math.isfinite(size) or size <= 0:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def human_duration(seconds: Optional[float]) -> str:
    """``3725`` -> ``"1:02:05"``; unknown durations become ``"?"``.

    Zero is unknown rather than ``0:00``: a site reports it for a live stream
    whose length nobody knows yet (E9).
    """
    if not seconds or seconds < 0 or not math.isfinite(seconds):
        return "?"
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def shorten(text: str, limit: int) -> str:
    """Cut ``text`` to ``limit`` characters with an ellipsis."""
    text = " ".join(str(text).split())
    if limit <= 1 or len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def safe_video_link(text: str) -> Optional[str]:
    """The link we are willing to hand to the downloader, or ``None``.

    This is a safety boundary rather than a formatting nicety. Whatever comes
    back from here ends up in a subprocess argument list, and a string that
    starts with a dash would be read as an *option* by the tool on the other
    end — ``--exec=…``, ``--config-locations=…`` — instead of as a link. So
    only plain ``http(s)`` links are returned: a bare site such as
    ``www.example.com/watch?v=x`` is upgraded to ``https://`` (Chrome sometimes
    hands the link over with the scheme stripped), and everything else — a
    different scheme, a local path, a sentence, an option-shaped string — is
    refused.
    """
    candidate = (text or "").strip().strip('"').strip("'")
    if not candidate or len(candidate) > _MAX_LINK_LENGTH:
        return None
    if candidate.startswith("-"):
        # The downloader's own argument parser would consume this as a flag.
        return None
    if any(character.isspace() for character in candidate):
        return None
    if any(character < " " or character == "\x7f" for character in candidate):
        return None

    lowered = candidate.lower()
    if lowered.startswith(("http://", "https://")):
        return candidate if urlsplit(candidate).hostname else None

    if candidate.startswith(("/", ".", "~")):
        return None

    # A bare site: no scheme was handed over, so decide from the host alone.
    host, _, port = candidate.split("/")[0].partition(":")
    if port and not port.isdigit():
        # Something like "file:", "javascript:" or "data:".
        return None
    if "." not in host:
        return None
    return f"https://{candidate}"


def is_probably_url(text: str) -> bool:
    """Whether ``text`` is a link we are willing to hand to the downloader."""
    return safe_video_link(text) is not None


def redact(text: str) -> str:
    """Take the credentials out of something about to be written down.

    Three shapes are covered: the login in a ``https://user:pw@host`` link, the
    value of any parameter named in :data:`SECRET_PARAMS` — whether it arrives
    as ``?token=…``, as ``&sig=…``, or percent-encoded inside a ``viddl://``
    hand-off — and a ``Bearer`` token quoted out of a header. Everything else is
    left exactly as it was, so a redacted line still says what happened.

    This is a safety net rather than a guarantee: a credential sent in some
    other shape, such as a whole cookie header, is not recognised.
    """
    if not text:
        return text
    text = _CREDENTIALS_IN_LINK.sub(r"\1" + REDACTED + "@", text)
    text = _BEARER.sub(r"\1" + REDACTED, text)
    return _SECRET_VALUE.sub(
        lambda found: "".join(found.groups()[:3]) + REDACTED, text
    )
