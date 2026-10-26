"""Turn the downloader's raw error text into something a normal person can act on.

Every explanation also says whether the failure is **temporary** — something a
second attempt may genuinely fix — because that is a decision the downloader
acts on by itself (see :meth:`viddl.download.DownloadJob.run`). Keeping the two
answers in one table is the point: a message that says "try again" and a retry
policy that does not try again are two ways of lying to the same person.

The sentences themselves are not here (U7): each rule names catalogue keys, and
:mod:`viddl.i18n` holds the words in every language Viddl speaks. What stays in
this file is the pattern that recognises a failure, which is English because the
engine's output is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import NamedTuple, Optional

from .i18n import t


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
#:
#: It is quoted into the advice through the ``{sign_in}`` placeholder, in every
#: language (U7), so that the sentence and the screen keep the same words. The
#: screens are still English, which is why the translated hint quotes this
#: English name rather than inventing a Spanish one for a row that does not say
#: it.
SIGN_IN_MARK = "Sign in with my browser"


class _Rule(NamedTuple):
    """One explanation, and whether the failure behind it is worth retrying.

    ``message`` and ``hint`` are catalogue keys, not sentences.
    """

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
        "error.cookies_read",
        "error.cookies_read.hint",
    ),
    _Rule(
        r"sign in to confirm|not a bot|confirm your age|age-restricted|"
        r"cookies|login required|requested content is not available",
        "error.sign_in",
        "error.sign_in.hint",
    ),
    _Rule(
        r"private video|this video is private",
        "error.private",
        "error.private.hint",
    ),
    _Rule(
        r"video unavailable|video is unavailable|this video is not available|"
        r"no longer available|removed by the uploader|has been removed|"
        r"content isn't available|unavailable video|not made this video available in your country|"
        r"available in your country|geo.?restricted|geo.?blocked",
        "error.unavailable",
        "error.unavailable.hint",
    ),
    _Rule(
        r"members-only|join this channel|subscriber",
        "error.members",
    ),
    _Rule(
        # Checked before the generic HTTP rules: "ffmpeg not found" would
        # otherwise be reported as a missing web page, which helps nobody.
        r"ffmpeg (is )?not (found|installed)|ffmpeg-location|"
        r"you have requested merging|postprocessing: ffmpeg",
        "error.ffmpeg",
        "error.ffmpeg.hint",
    ),
    _Rule(
        # A search that found nothing (F14): the answer is about the words, not
        # the connection, and "no video results" is deliberately checked before
        # the unsupported-URL rule so a search is not reported as a bad link.
        r"no video results|no search results|no results found|"
        r"did not return any results|no results were found",
        "error.no_results",
        "error.no_results.hint",
    ),
    _Rule(
        r"unsupported url|no video formats found|unable to extract|"
        r"no suitable formats|no video could be found|no video found",
        "error.not_a_video",
        "error.not_a_video.hint",
    ),
    _Rule(
        r"unexpected response from webpage|unable to parse|failed to parse|"
        r"unable to extract webpage|unable to extract player",
        "error.site_changed",
        "error.site_changed.hint",
    ),
    _Rule(
        r"http error 404|http 404|\b404\b.{0,12}not found",
        "error.not_found",
        "error.not_found.hint",
    ),
    _Rule(
        # A refusal is often per request (an expired URL, a throttled client),
        # and a fresh extraction asks for a new one — so this is worth one more
        # go. A genuine region block says so in its own words and is caught by
        # the "not available" rule above.
        r"http error 403|forbidden",
        "error.refused",
        "error.refused.hint",
        temporary=True,
    ),
    _Rule(
        # 5xx: the site is having a bad minute, which is the textbook case for
        # waiting rather than rephrasing the problem to the user.
        r"http error 5\d\d|http 5\d\d|\b50[0-4]\b|internal server error|"
        r"bad gateway|service unavailable|gateway time-?out|server error",
        "error.site_error",
        "error.site_error.hint",
        temporary=True,
    ),
    _Rule(
        r"429|too many requests|rate.?limit",
        "error.rate_limited",
        "error.rate_limited.hint",
        temporary=True,
    ),
    _Rule(
        r"temporary failure in name resolution|failed to resolve|"
        r"getaddrinfo|nodename nor servname|name or service not known",
        "error.offline",
        "error.offline.hint",
        temporary=True,
    ),
    _Rule(
        r"connection refused|connection reset|timed out|timeout|"
        r"ssl|certificate verify failed|network is unreachable|urlopen error|"
        r"remote end closed connection|incompleteread",
        "error.connection",
        "error.connection.hint",
        temporary=True,
    ),
    _Rule(
        r"no space left on device",
        "error.disk_full",
        "error.disk_full.hint",
    ),
    _Rule(
        r"permission denied|errno 13",
        "error.not_allowed",
        "error.not_allowed.hint",
    ),
    _Rule(
        r"unable to write data|disk i/o error|input/output error",
        "error.write_failed",
    ),
    _Rule(
        r"requested format is not available|format .* not available",
        "error.quality_gone",
        "error.quality_gone.hint",
    ),
    _Rule(
        r"is not a valid url|invalid url",
        "error.bad_link",
        "error.bad_link.hint",
    ),
    _Rule(
        r"interrupted|terminated|keyboardinterrupt",
        "error.stopped",
    ),
]


def explain(raw: str, exit_code: Optional[int] = None) -> FriendlyError:
    """Map messy tool output to a plain-language explanation.

    The language is whichever one is in force (U7), so the same rule speaks
    Spanish to a Spanish machine without a second table of patterns.
    """
    text = (raw or "").strip()
    haystack = text.lower()

    for rule in _RULES:
        if re.search(rule.pattern, haystack, re.IGNORECASE):
            return FriendlyError(
                t(rule.message, sign_in=SIGN_IN_MARK),
                t(rule.hint, sign_in=SIGN_IN_MARK) if rule.hint else None,
                rule.temporary,
            )

    if not text:
        if exit_code:
            return FriendlyError(
                t("error.stopped_early"),
                t("error.stopped_early.hint"),
                temporary=True,
            )
        return FriendlyError(t("error.generic"))

    if re.search(r"\berror\b", haystack):
        # Keep the original text but make it the last resort, trimmed of noise.
        detail = _clean_raw(text)
        advice = t("error.reported.advice")
        return FriendlyError(
            t("error.reported"),
            f"{detail}\n{advice}" if detail else advice,
            temporary=True,
        )

    return FriendlyError(t("error.unfinished"), _clean_raw(text))


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
