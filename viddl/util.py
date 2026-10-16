"""Tiny formatting helpers used by both the terminal UI and the CLI."""

from __future__ import annotations

from typing import Optional


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


def is_probably_url(text: str) -> bool:
    """Loose check that keeps obviously-wrong input out of the downloader."""
    candidate = (text or "").strip()
    if not candidate or " " in candidate:
        return False
    if candidate.lower().startswith(("http://", "https://")):
        return True
    # Chrome sometimes strips the scheme when a custom handler is involved.
    return "." in candidate.split("/")[0] and not candidate.startswith(("/", ".", "~"))
