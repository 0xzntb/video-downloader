"""Tiny formatting helpers used by both the terminal UI and the CLI."""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlsplit

# Long enough for any real link, short enough to keep an odd paste out of argv.
_MAX_LINK_LENGTH = 2048


def human_size(num_bytes: Optional[float]) -> str:
    """``12345678`` -> ``"12.4 MB"``; unknown sizes become ``"?"``."""
    if num_bytes is None or num_bytes <= 0:
        return "?"
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def human_duration(seconds: Optional[float]) -> str:
    """``3725`` -> ``"1:02:05"``."""
    if not seconds or seconds < 0:
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
