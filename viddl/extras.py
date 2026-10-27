"""The optional extras a download can carry: subtitles, details, cover image (F5).

Three toggles, not four, because yt-dlp has three behaviours behind the five
flags the suggestion named:

* ``--write-subs`` and ``--embed-subs`` are one decision — fetch the captions,
  then put them in the file — so the user makes it once, under **Subtitles**.
* ``--embed-metadata`` already embeds the chapters: yt-dlp turns
  ``addchapters`` on whenever ``addmetadata`` is set (measured: 3 chapters in a
  file downloaded with only ``--embed-metadata``). A separate chapters switch
  would be a knob that does nothing while details are on, so they are one row.
* ``--embed-thumbnail`` is deliberately *not* used. The yt-dlp release Viddl
  installs carries no mutagen, and the ffmpeg it installs carries no ffprobe;
  measured on a real MP4, embedding the picture then fails the whole download
  with "Unable to embed using ffprobe & ffmpeg". **Cover image** therefore
  writes the site's picture beside the file — a thing that works on every link
  — and the row says so rather than promising something inside the file.

Everything here is off until it is asked for: an extra changes what lands on
disk, and a download should not unexpectedly contain more than it said.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, List, Optional, Set, Tuple

from .formats import MediaOption


@dataclass(frozen=True)
class Extra:
    """One thing that can be added to a download besides the media itself."""

    key: str
    label: str
    note: str
    args: Tuple[str, ...]
    #: What asking yt-dlp for this costs, in words, when the button is hovered
    #: or the extra is named on the command line.
    summary: str = ""
    #: True when the extra is written by ffmpeg, so the app can ask once and
    #: keep it — the same promise a quality row makes.
    needs_ffmpeg: bool = True
    #: Captions in an audio file are a stream no player offers to show; the row
    #: is skipped instead of asking for something that cannot be used.
    video_only: bool = False


#: In the order they are offered. Subtitles first because they are the one an
#: English speaker most often wants, and the cover last because it is the one
#: that does not touch the media file at all.
EXTRAS: Tuple[Extra, ...] = (
    Extra(
        "subs",
        "Subtitles",
        "English, including auto-generated",
        (
            "--write-subs",
            "--write-auto-subs",
            "--embed-subs",
            # Exactly English, not every translation the site offers: measured
            # with "en.*", one 429 on an "English from German" caption ended
            # the whole download before the video was even fetched.
            "--sub-langs",
            "en,en-US,en-orig",
        ),
        summary="English subtitles in the video",
        video_only=True,
    ),
    Extra(
        "details",
        "Details",
        "title, channel, date and chapters",
        ("--embed-metadata",),
        summary="the title, channel, date and chapters",
    ),
    Extra(
        "cover",
        "Cover image",
        "saved beside the file, not inside it",
        ("--write-thumbnail", "--convert-thumbnails", "jpg"),
        summary="a picture beside the file",
    ),
)

#: Every word ``--extras`` accepts, so a script can say what it means and a
#: mistyped one is answered with the list rather than a guess.
_ALIASES = {
    "subs": "subs",
    "sub": "subs",
    "subtitles": "subs",
    "subtitle": "subs",
    "captions": "subs",
    "details": "details",
    "detail": "details",
    "meta": "details",
    "metadata": "details",
    "tags": "details",
    "chapters": "details",
    "chapter": "details",
    "cover": "cover",
    "thumb": "cover",
    "thumbnail": "cover",
    "art": "cover",
    "picture": "cover",
    "image": "cover",
}

_BY_KEY = {extra.key: extra for extra in EXTRAS}


@dataclass(frozen=True)
class Extras:
    """The extras that have been switched on, in a set that cannot be edited.

    Frozen and keyed by the extra's own name, so a screen can hold it in state
    and a download can be handed it without either being able to change what
    the other is doing. ``Extras()`` is the default everywhere: nothing on.
    """

    keys: FrozenSet[str] = frozenset()

    def __bool__(self) -> bool:
        return bool(self.keys)

    def __contains__(self, key: object) -> bool:
        return key in self.keys

    def has(self, key: str) -> bool:
        return key in self.keys

    def toggled(self, key: str) -> Extras:
        """The same set with ``key`` switched the other way."""
        if key not in _BY_KEY:
            raise ValueError(f"No such extra: {key!r}")
        if key in self.keys:
            return Extras(self.keys - {key})
        return Extras(self.keys | {key})

    def enabled(self) -> List[Extra]:
        """The switched-on extras, in the order the app offers them."""
        return [extra for extra in EXTRAS if extra.key in self.keys]

    def applicable(self, option: Optional[MediaOption] = None) -> List[Extra]:
        """The extras that will actually be added to ``option``.

        An audio download has nowhere to show a subtitle track, so the row is
        skipped rather than passed to yt-dlp to fail on. ``option=None`` means
        "everything that is on", which is what a summary before a choice needs.
        """
        chosen = self.enabled()
        if option is not None and option.kind == "audio":
            return [extra for extra in chosen if not extra.video_only]
        return chosen

    def skipped(self, option: Optional[MediaOption] = None) -> List[Extra]:
        """The extras that were asked for but cannot apply to ``option``."""
        chosen = self.enabled()
        if option is None:
            return []
        return [extra for extra in chosen if option.kind == "audio" and extra.video_only]

    def needs_ffmpeg_for(self, option: Optional[MediaOption] = None) -> bool:
        return any(extra.needs_ffmpeg for extra in self.applicable(option))

    def args_for(self, option: Optional[MediaOption] = None) -> List[str]:
        """The yt-dlp arguments for these extras, ready to append."""
        args: List[str] = []
        for extra in self.applicable(option):
            args.extend(extra.args)
        return args

    def names(self, option: Optional[MediaOption] = None) -> List[str]:
        return [extra.label.lower() for extra in self.applicable(option)]

    def summary(self, option: Optional[MediaOption] = None) -> str:
        """``"subtitles and details"`` — the sentence a log line needs."""
        names = self.names(option)
        if not names:
            return "none"
        if len(names) == 1:
            return names[0]
        return ", ".join(names[:-1]) + " and " + names[-1]

    def signature(self) -> str:
        """A stable string for the working folder a run belongs to."""
        return ",".join(sorted(self.keys))

    @classmethod
    def parse(cls, text: str) -> Extras:
        """Read ``--extras subs,cover`` into a set, or explain the mistake.

        Raises ``ValueError`` with a sentence naming every word that works,
        because a script that mistypes one should be told what to type instead
        of being quietly given a download missing what it asked for.
        """
        words = [word for word in str(text).replace(",", " ").split() if word]
        if not words:
            return cls()
        keys: Set[str] = set()
        for word in words:
            lowered = word.strip().lower()
            if lowered in ("all", "everything", "*"):
                keys.update(_BY_KEY)
                continue
            if lowered in ("none", "off", "no"):
                continue
            key = _ALIASES.get(lowered)
            if key is None:
                raise ValueError(
                    f"Unknown extra '{word}'. Use subs, details or cover "
                    "(or all, or none)."
                )
            keys.add(key)
        return cls(frozenset(keys))

