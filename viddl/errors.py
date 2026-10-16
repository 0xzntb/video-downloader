"""Turn the downloader's raw error text into something a normal person can act on."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class FriendlyError:
    """A short explanation plus an optional suggestion."""

    message: str
    hint: Optional[str] = None

    def __str__(self) -> str:
        if self.hint:
            return f"{self.message}\n{self.hint}"
        return self.message


# Ordered: the first pattern that matches wins.
_RULES: list[tuple[str, str, Optional[str]]] = [
    (
        r"sign in to confirm|not a bot|confirm your age|age-restricted|"
        r"cookies|login required|requested content is not available",
        "The site wants you to be signed in to watch this one.",
        "Age-restricted or bot-checked videos need a browser login, which Viddl does not do yet. "
        "Try another link.",
    ),
    (
        r"private video|this video is private",
        "That video is private.",
        "Only the owner can download it. Is there a public version you meant to open?",
    ),
    (
        r"video unavailable|video is unavailable|this video is not available|"
        r"no longer available|removed by the uploader|has been removed|"
        r"content isn't available|unavailable video",
        "That video is not available any more.",
        "It may have been deleted, made private, or blocked in your region.",
    ),
    (
        r"members-only|join this channel|subscriber",
        "That video is for channel members only.",
        None,
    ),
    (
        # Checked before the generic HTTP rules: "ffmpeg not found" would
        # otherwise be reported as a missing web page, which helps nobody.
        r"ffmpeg (is )?not (found|installed)|ffmpeg-location|"
        r"you have requested merging|postprocessing: ffmpeg",
        "Viddl needs ffmpeg for this download, and it is not installed yet.",
        "Viddl can fetch it for you — choose “Set up ffmpeg” on the previous screen.",
    ),
    (
        r"unsupported url|no video formats found|unable to extract|"
        r"no suitable formats|no video could be found|no video found",
        "No video could be found at that link.",
        "Check that the link opens a video (not a channel, a search page, a photo, "
        "or a post without video).",
    ),
    (
        r"unexpected response from webpage|unable to parse|failed to parse|"
        r"unable to extract webpage|unable to extract player",
        "The site sent back something Viddl did not expect.",
        "Sites change their pages often. Waiting a day and trying again usually fixes it.",
    ),
    (
        r"http error 404|http 404|\b404\b.{0,12}not found",
        "That page could not be found (404).",
        "The link may be mistyped or the page may have been removed.",
    ),
    (
        r"http error 403|forbidden",
        "The site refused the request (403).",
        "This usually means the video is restricted or the site changed something. "
        "Opening Viddl again later often helps, because it re-checks for updates.",
    ),
    (
        r"429|too many requests|rate.?limit",
        "The site is rate-limiting right now.",
        "Wait a few minutes and try again.",
    ),
    (
        r"temporary failure in name resolution|failed to resolve|"
        r"getaddrinfo|nodename nor servname|name or service not known",
        "Could not reach the internet.",
        "Check your Wi-Fi or ethernet connection and try again.",
    ),
    (
        r"connection refused|connection reset|timed out|timeout|"
        r"ssl|certificate verify failed|network is unreachable",
        "The connection to the site failed.",
        "This is usually a flaky network or a firewall. Trying again often works.",
    ),
    (
        r"no space left on device",
        "The disk is full.",
        "Free up some space in your Downloads folder and try again.",
    ),
    (
        r"permission denied|errno 13",
        "Viddl is not allowed to write that file.",
        "Check the permissions on your Downloads folder.",
    ),
    (
        r"unable to write data|disk i/o error|input/output error",
        "There was a problem writing the file to disk.",
        None,
    ),
    (
        r"requested format is not available|format .* not available",
        "That quality is no longer offered for this video.",
        "Go back and pick another quality from the list — the options change per video.",
    ),
    (
        r"is not a valid url|invalid url",
        "That does not look like a web link.",
        "Copy the address from your browser's address bar and paste it again.",
    ),
    (
        r"interrupted|terminated|keyboardinterrupt",
        "The download was stopped.",
        None,
    ),
]


def explain(raw: str, exit_code: Optional[int] = None) -> FriendlyError:
    """Map messy tool output to a plain-language explanation."""
    text = (raw or "").strip()
    haystack = text.lower()

    for pattern, message, hint in _RULES:
        if re.search(pattern, haystack, re.IGNORECASE):
            return FriendlyError(message, hint)

    if not text:
        if exit_code:
            return FriendlyError(
                "The download stopped unexpectedly.",
                "Running it again usually helps. If it keeps failing, try a different quality.",
            )
        return FriendlyError("Something went wrong.")

    if re.search(r"\berror\b", haystack):
        # Keep the original text but make it the last resort, trimmed of noise.
        detail = _clean_raw(text)
        advice = (
            "This is usually a temporary change at the site's end. Trying again in a "
            "little while, or using another link, often works."
        )
        return FriendlyError(
            "The downloader reported an error.",
            f"{detail}\n{advice}" if detail else advice,
        )

    return FriendlyError("The download did not finish.", _clean_raw(text))


def _clean_raw(text: str, limit: int = 400) -> str:
    """Strip log prefixes so raw output stays readable."""
    lines = []
    for line in text.splitlines():
        line = re.sub(r"^(?:\[[a-z]+\]\s*|ERROR:\s*)+", "", line.strip())
        if not line or line.startswith("--"):
            continue
        lines.append(line)
    joined = " ".join(lines)
    # yt-dlp sometimes appends a stray "null" for a missing reason field.
    joined = re.sub(r"\s+null$", "", joined).strip()
    if len(joined) > limit:
        joined = joined[: limit - 1].rstrip() + "…"
    return joined


def is_fatal_ytdlp_line(line: str) -> bool:
    """True for the ``ERROR:`` lines worth surfacing to the user."""
    return line.strip().startswith("ERROR:")
