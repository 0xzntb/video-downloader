"""A name instead of a link (F14).

Pasting a link is still the precise way to ask for one video, and nothing here
takes that away. But somebody who wants a song knows its name, not its URL, and
``viddl "some song"`` used to answer *that does not look like a video link* — a
refusal that made sense as a safety rule (S1) and stopped making sense as an
answer. This module is the one place that decides what a piece of text is:

* a **link**, when it passes :func:`viddl.util.safe_video_link` — returned as it
  is, because that is what the downloader already understands;
* a **search**, when it is a name rather than a link — returned as
  ``ytsearch1:<the words>``, which is the downloader's own way of asking for the
  first search result;
* **nothing**, when it is neither: option-shaped text, a different scheme, a
  local path, a paragraph, control characters.

That third answer matters. A search target is still handed to yt-dlp as one
argument, after the ``--`` separator every download uses, so an option can never
be read as one — but a query that *starts* with a dash would reach the search
engine as a dash, and a pasted file path is a mistake worth refusing rather than
searching for. Refusing is the safe default; searching is the deliberate one.

The word "search" is honest about what it does: it uses the engine's search,
which is YouTube's. A name is a convenience for the thing people actually mean,
not a second front door to every site yt-dlp can read.
"""

from __future__ import annotations

import re
from typing import Optional

from .formats import MediaInfo
from .util import safe_video_link

#: The downloader's spelling of "search, and give me the first result".
PREFIX = "ytsearch1:"

#: Long enough for a full song title and artist, short enough that a pasted
#: paragraph is refused instead of being searched for a hundred words at a time.
MAX_QUERY = 200

#: ``scheme:`` — how a string announces itself as something other than a name.
#: A search for "javascript:alert(1)" is not what anyone meant, and the answer
#: to a broken link should be "that is not a link", not a search for its text.
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")

#: What a name may not start with. A dash would be an option to the downloader
#: if it ever lost its ``--``; a slash, a dot or a tilde is a path, which is a
#: mistake to report rather than a thing to search the web for.
_NOT_A_NAME_START = ("-", "/", ".", "~")


def query_of(text: str) -> Optional[str]:
    """The words to search for when ``text`` is a name, or ``None``.

    Whitespace is collapsed, so a name pasted from a page arrives as one line
    however it was wrapped. Everything that makes a string *not* a name is
    refused here rather than in the caller, so the command line, the app and
    the tests all get the same answer from the same rule.
    """
    candidate = " ".join((text or "").split())
    if not candidate or len(candidate) > MAX_QUERY:
        return None
    if candidate.startswith(_NOT_A_NAME_START):
        return None
    if any(character < " " or character == "\x7f" for character in candidate):
        return None
    if _SCHEME.match(candidate) or "://" in candidate:
        return None
    # Punctuation alone ("...", "-", "??") is not a name anybody can search for.
    if not any(character.isalnum() for character in candidate):
        return None
    return candidate


def is_search(target: str) -> bool:
    """Whether ``target`` is a search rather than a link."""
    return (target or "").strip().lower().startswith(PREFIX)


def query_from(target: str) -> str:
    """The words inside a search target; anything else comes back unchanged."""
    text = (target or "").strip()
    if text.lower().startswith(PREFIX):
        return text[len(PREFIX) :].strip()
    return text


def target_for(text: str) -> Optional[str]:
    """What to hand the downloader for ``text``: a link, a search, or ``None``.

    A link wins over a search, because a link is the more precise answer and
    ``safe_video_link`` already knows what one looks like. The one exception is
    text that already carries the search prefix: that is a target, not a name,
    and re-prefixing it would search for the plumbing.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    if is_search(raw):
        query = query_of(query_from(raw))
        return f"{PREFIX}{query}" if query else None
    link = safe_video_link(raw)
    if link is not None:
        return link
    query = query_of(raw)
    if query is None:
        return None
    return f"{PREFIX}{query}"


def display_text(target: str) -> str:
    """What to put back in the link box: the name, not the plumbing."""
    return query_from(target) if is_search(target) else target


def resolved(info: MediaInfo, target: str) -> str:
    """The real link a search found, so the download is of a video, not a query.

    Everything downstream assumes a download names a *video*: the archive
    remembers ids, the recent list is a list of videos, and a resume needs the
    same thing twice. A query is none of those, so once a search has an answer
    the answer's own link replaces it. If the site did not say what it found,
    the query stays — the downloader can still fetch it — and nothing is
    invented.
    """
    if not is_search(target):
        return target
    return safe_video_link(info.url or "") or target


def looking_note(target: str) -> str:
    """The line ``--plain`` prints before it asks a site anything.

    It says which question is being asked, because "Looking up ytsearch1:some
    song" is plumbing and "the first result is used" is the thing the person
    needs to know when the wrong song comes back.
    """
    if is_search(target):
        return f'Searching for "{query_from(target)}" - the first result is used.'
    return f"Looking up {target}"
