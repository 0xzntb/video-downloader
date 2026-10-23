"""Turn the downloader's raw error text into something a normal person can act on.

Every explanation also says whether the failure is **temporary** — something a
second attempt may genuinely fix — because that is a decision the downloader
acts on by itself (see :meth:`viddl.download.DownloadJob.run`). Keeping the two
answers in one table is the point: a message that says "try again" and a retry
policy that does not try again are two ways of lying to the same person.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import NamedTuple, Optional


@dataclass
class FriendlyError:
    """A short explanation plus an optional suggestion.

    ``temporary`` is True when trying the same thing again is worth the wait:
    a rate limit, a server having a bad minute, a connection that dropped.
    It is False when the answer will not change on its own — the video is
    private, the page is gone, the disk is full — because retrying those only
    delays a message the user needs now.
    """

    message: str
    hint: Optional[str] = None
    temporary: bool = False

    def __str__(self) -> str:
        if self.hint:
            return f"{self.message}\n{self.hint}"
        return self.message


#: The words the qualities screen uses for the cookie row (F1). The rule below
#: names it, and the app looks for it: on a computer with no browser there is no
#: row to choose, so the advice has to change rather than point at nothing.
SIGN_IN_MARK = "Sign in with my browser"


class _Rule(NamedTuple):
    """One explanation, and whether the failure behind it is worth retrying."""

    pattern: str
    message: str
    hint: Optional[str] = None
    temporary: bool = False


# Ordered: the first pattern that matches wins. Order therefore also decides
# what a mixed message is *called*, which is why the specific says come first.
_RULES: list[_Rule] = [
    _Rule(
        # A cookie read that failed, rather than a site asking for cookies. It
        # comes first because yt-dlp names "cookies" in both, and the sign-in
        # rule below would otherwise swallow this one (F1).
        r"could not (copy|read|open|find).{0,40}cookie|"
        r"unable to (open|read|find).{0,30}cookie|"
        r"cookie.{0,30}(database|store|file).{0,40}(locked|missing|not found|in use)|"
        r"failed to (decrypt|extract).{0,40}(cookie|dpapi|keyring|keychain)|"
        r"(dpapi|keyring|keychain).{0,40}(fail|error|denied)|"
        r"no such file or directory.{0,40}cookie",
        "Viddl could not read your browser's cookies.",
        "Sign in to the site in that browser first, then close the browser and try again — "
        "an open browser can hold the store locked. A different browser works too: "
        "choose “Sign in with my browser” on the qualities screen.",
    ),
    _Rule(
        r"sign in to confirm|not a bot|confirm your age|age-restricted|"
        r"cookies|login required|requested content is not available",
        "The site wants you to be signed in to watch this one.",
        "Age-restricted, bot-checked and members-only videos answer to a signed-in "
        f"session. Choose “{SIGN_IN_MARK}” and Viddl will read the cookies that "
        "browser already has.",
    ),
    _Rule(
        r"private video|this video is private",
        "That video is private.",
        "Only the owner can download it. Is there a public version you meant to open?",
    ),
    _Rule(
        r"video unavailable|video is unavailable|this video is not available|"
        r"no longer available|removed by the uploader|has been removed|"
        r"content isn't available|unavailable video|not made this video available in your country|"
        r"available in your country|geo.?restricted|geo.?blocked",
        "That video is not available any more.",
        "It may have been deleted, made private, or blocked in your region.",
    ),
    _Rule(
        r"members-only|join this channel|subscriber",
        "That video is for channel members only.",
        None,
    ),
    _Rule(
        # Checked before the generic HTTP rules: "ffmpeg not found" would
        # otherwise be reported as a missing web page, which helps nobody.
        r"ffmpeg (is )?not (found|installed)|ffmpeg-location|"
        r"you have requested merging|postprocessing: ffmpeg",
        "Viddl needs ffmpeg for this download, and it is not installed yet.",
        "Viddl can fetch it for you — choose “Set up ffmpeg” on the previous screen.",
    ),
    _Rule(
        # A search that found nothing (F14): the answer is about the words, not
        # the connection, and "no video results" is deliberately checked before
        # the unsupported-URL rule so a search is not reported as a bad link.
        r"no video results|no search results|no results found|"
        r"did not return any results|no results were found",
        "Nothing was found for that search.",
        "Try fewer words, another spelling, or paste the link instead.",
    ),
    _Rule(
        r"unsupported url|no video formats found|unable to extract|"
        r"no suitable formats|no video could be found|no video found",
        "No video could be found at that link.",
        "Check that the link opens a video (not a channel, a search page, a photo, "
        "or a post without video).",
    ),
    _Rule(
        r"unexpected response from webpage|unable to parse|failed to parse|"
        r"unable to extract webpage|unable to extract player",
        "The site sent back something Viddl did not expect.",
        "Sites change their pages often. Waiting a day and trying again usually fixes it.",
    ),
    _Rule(
        r"http error 404|http 404|\b404\b.{0,12}not found",
        "That page could not be found (404).",
        "The link may be mistyped or the page may have been removed.",
    ),
    _Rule(
        # A refusal is often per request (an expired URL, a throttled client),
        # and a fresh extraction asks for a new one — so this is worth one more
        # go. A genuine region block says so in its own words and is caught by
        # the "not available" rule above.
        r"http error 403|forbidden",
        "The site refused the request (403).",
        "This is often a moment's throttling rather than a real block, so it is worth "
        "another try. If it keeps happening, the video may be restricted.",
        temporary=True,
    ),
    _Rule(
        # 5xx: the site is having a bad minute, which is the textbook case for
        # waiting rather than rephrasing the problem to the user.
        r"http error 5\d\d|http 5\d\d|\b50[0-4]\b|internal server error|"
        r"bad gateway|service unavailable|gateway time-?out|server error",
        "The site had a problem of its own.",
        "That is usually brief, so it is worth trying again in a moment.",
        temporary=True,
    ),
    _Rule(
        r"429|too many requests|rate.?limit",
        "The site is rate-limiting right now.",
        "Waiting a moment and trying again usually clears it.",
        temporary=True,
    ),
    _Rule(
        r"temporary failure in name resolution|failed to resolve|"
        r"getaddrinfo|nodename nor servname|name or service not known",
        "Could not reach the internet.",
        "Check your Wi-Fi or ethernet connection, then try again.",
        temporary=True,
    ),
    _Rule(
        r"connection refused|connection reset|timed out|timeout|"
        r"ssl|certificate verify failed|network is unreachable|urlopen error|"
        r"remote end closed connection|incompleteread",
        "The connection to the site failed.",
        "This is usually a flaky network or a firewall. Trying again often works.",
        temporary=True,
    ),
    _Rule(
        r"no space left on device",
        "The disk is full.",
        "Free up some space in your Downloads folder and try again.",
    ),
    _Rule(
        r"permission denied|errno 13",
        "Viddl is not allowed to write that file.",
        "Check the permissions on your Downloads folder.",
    ),
    _Rule(
        r"unable to write data|disk i/o error|input/output error",
        "There was a problem writing the file to disk.",
        None,
    ),
    _Rule(
        r"requested format is not available|format .* not available",
        "That quality is no longer offered for this video.",
        "Go back and pick another quality from the list — the options change per video.",
    ),
    _Rule(
        r"is not a valid url|invalid url",
        "That does not look like a web link.",
        "Copy the address from your browser's address bar and paste it again.",
    ),
    _Rule(
        r"interrupted|terminated|keyboardinterrupt",
        "The download was stopped.",
        None,
    ),
]


def explain(raw: str, exit_code: Optional[int] = None) -> FriendlyError:
    """Map messy tool output to a plain-language explanation."""
    text = (raw or "").strip()
    haystack = text.lower()

    for rule in _RULES:
        if re.search(rule.pattern, haystack, re.IGNORECASE):
            return FriendlyError(rule.message, rule.hint, rule.temporary)

    if not text:
        if exit_code:
            return FriendlyError(
                "The download stopped unexpectedly.",
                "Running it again usually helps. If it keeps failing, try a different quality.",
                temporary=True,
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
            temporary=True,
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
