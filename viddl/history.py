"""What Viddl remembers between runs: what it saved, and what it fetched (F6).

Two different memories live in the app's own folder:

* **The recent list** — a small JSON file describing the last few things
  saved: the link, where the file went, what it is called, how big it was and
  when. It is what the Recent screen is built from, and it is the only memory
  a person reads.
* **The download archive** — the file yt-dlp itself writes, one line per video
  it has finished (``youtube dQw4w9WgXcQ``). Asking again for a link that is
  in it is answered with the file already on disk rather than a second copy
  of the same bytes — and re-running a playlist takes only the videos it has
  not seen yet, which is the same promise kept over a whole list.

Both hold things worth keeping private: a link can carry a signed token, and
a list of what somebody downloads says something about them. The recent list
is Viddl's own file and is written for its owner only (``0600``), on the same
reasoning that gives the log its mode (S4). The archive is kept at whatever
mode yt-dlp gives it — measured, ``0644`` — because yt-dlp creates and
rewrites it and Viddl only ever corrects it; what makes that read-only to
everybody else is the folder both live in, which only the owner can open (S3).
The one time Viddl does rewrite the archive it uses ``0600`` too.

Nothing here raises. A history that cannot be written is a lost nicety, never
a reason to fail a download that otherwise worked — so every write and read
swallows its own ``OSError``, and a corrupt file is replaced rather than
complained about.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from . import appdata
from .util import human_size

#: How many downloads the recent list keeps. A list is not an archive: the
#: file is read into a screen with one glance's worth of room, and fifty rows
#: already reach further back than anybody scrolls.
HISTORY_LIMIT = 50

#: The mode the file Viddl writes is kept at. A link can carry a signed token,
#: and the folder is already the app's own (0700) — this is the second lock,
#: the same one the log has. (The archive is yt-dlp's to create; see the module
#: docstring for what mode it ends up at.)
_OWNER_PRIVATE = 0o600

#: One writer at a time. Two downloads finishing in the same second would
#: otherwise read the same list, each write their own line, and one of the two
#: would silently disappear.
_LOCK = threading.Lock()


@dataclass(frozen=True)
class Entry:
    """One finished download, as the recent list remembers it."""

    path: Path
    title: str = ""
    url: str = ""
    size: int = 0
    when: float = 0.0
    #: Filled in by :func:`entries` for rows whose file has since gone: the
    #: row stays (it is history, and hiding it would be a second surprise) but
    #: the screen says the file is not there any more.
    missing: bool = field(default=False, compare=False)

    @property
    def size_text(self) -> str:
        return human_size(self.size) if self.size else "?"

    @property
    def when_text(self) -> str:
        """How long ago, in the few words a passing eye needs."""
        if not self.when:
            return ""
        seconds = max(0.0, time.time() - self.when)
        if seconds < 60:
            return "just now"
        if seconds < 3600:
            return f"{int(seconds // 60)} min ago"
        if seconds < 86400:
            return f"{int(seconds // 3600)} h ago"
        if seconds < 7 * 86400:
            return f"{int(seconds // 86400)} days ago"
        return time.strftime("%d %b %Y", time.localtime(self.when))

    @property
    def row_note(self) -> str:
        """What the Recent screen puts beside the size."""
        bits = [self.when_text] if self.when_text else []
        if self.missing:
            bits.append("file is gone")
        return "  ·  ".join(bits)


# The two files live where appdata says every file of Viddl's lives; the
# paths are named there (``appdata.history_path`` and ``appdata.archive_path``)
# so the doctor, the CLI and this module can never disagree about them.


# --------------------------------------------------------------------------- writing


def _write_file(path: Path, text: str) -> bool:
    """Replace one of these files whole, for its owner only; False if it could not be.

    Written through a temporary name and moved into place, so a screen reading
    the list at the same moment sees the old one or the new one, never half of
    each. The mode is set on the temporary file, which is the one that
    survives: a reader must never see these lines at anyone else's expense,
    not even for the length of a write.
    """
    temp = path.with_name(path.name + ".tmp")
    try:
        # The folder is made here as well as by ``ensure_dirs``: the first
        # download of a fresh install arrives before anything else has needed
        # the folder to exist, and losing that download's row over a missing
        # directory would be the wrong trade.
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _OWNER_PRIVATE
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temp, path)
        if not appdata._is_windows() and path.exists():
            os.chmod(path, _OWNER_PRIVATE)
        return True
    except OSError:
        try:
            temp.unlink()
        except OSError:
            pass
        return False


def record(url: str, path: Path, title: str = "") -> Optional[Entry]:
    """Remember one finished download; ``None`` means it could not be written.

    The same file recorded twice is one row moved to the top, not two rows:
    re-saving over a file, or the archive handing back a file from an earlier
    run, must not fill the list with the same name.
    """
    entry = Entry(
        path=Path(path),
        title=title,
        url=url,
        size=_size_of(path),
        when=time.time(),
    )
    with _LOCK:
        kept = [old for old in _read() if old.path != entry.path]
        kept.insert(0, entry)
        kept = kept[:HISTORY_LIMIT]
        if not _write_file(appdata.history_path(), _as_json(kept)):
            return None
    return entry


def entries(limit: int = HISTORY_LIMIT) -> List[Entry]:
    """The recent list, newest first; nothing remembered is left out silently.

    A row whose file has gone stays in the list with ``missing`` set: the
    Recent screen says so out loud rather than pretending the download never
    happened.
    """
    with _LOCK:
        found = _read()[:limit]
    return [
        Entry(
            path=entry.path,
            title=entry.title,
            url=entry.url,
            size=entry.size,
            when=entry.when,
            missing=not _exists(entry.path),
        )
        for entry in found
    ]


def _size_of(path: Path) -> int:
    try:
        return int(Path(path).stat().st_size)
    except OSError:
        return 0


def _exists(path: Path) -> bool:
    try:
        return Path(path).is_file()
    except OSError:
        return False


def _as_json(items: List[Entry]) -> str:
    payload = [
        {
            "path": str(entry.path),
            "title": entry.title,
            "url": entry.url,
            "size": int(entry.size),
            "when": float(entry.when),
        }
        for entry in items
    ]
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def _read() -> List[Entry]:
    """What the file holds, newest first; an unreadable file is simply nothing.

    Every field is coerced rather than trusted: the file is edited by hand at
    least once in its life (somebody deleting rows they do not want), and a
    hand-edited file is a reason for the list to skip a line, not for the
    screen to fail.
    """
    try:
        raw = appdata.history_path().read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    try:
        payload = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(payload, list):
        return []
    found: List[Entry] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        if not isinstance(path, str) or not path:
            continue
        found.append(
            Entry(
                path=Path(path),
                title=str(item.get("title") or ""),
                url=str(item.get("url") or ""),
                size=_as_int(item.get("size")),
                when=_as_float(item.get("when")),
            )
        )
    return found


def _as_int(value: object) -> int:
    try:
        return max(0, int(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _as_float(value: object) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return number if number > 0 else 0.0


# --------------------------------------------------------------------------- the archive


def forget_video(archive: Path, video_id: str) -> bool:
    """Delete this video from the download archive; True when a line went.

    This is the way out of a skip that turned out to be wrong. The archive
    only knows a video's *name*, not whether the file is still on disk, so a
    user who tidied their Downloads folder can be told "you already have this"
    about a file that no longer exists. Removing the line means the next run
    downloads it again — the archive catching up with the disk, rather than
    the disk being asked to prove itself to the archive.
    """
    if not video_id:
        return False
    try:
        lines = archive.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    kept = [
        line
        for line in lines
        if line.strip() and line.rsplit(" ", 1)[-1] != video_id
    ]
    if len(kept) == len(lines):
        return False
    text = "".join(f"{line}\n" for line in kept)
    with _LOCK:
        _write_file(archive, text)
    return True
