"""Reading a link's qualities, and running the actual download."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import appdata
from . import history
from .cookies import cookies_args
from .engine import Engine, ToolError, is_windows
from .errors import explain
from .extras import Extras
from .formats import MediaInfo, MediaOption, Playlist, parse_info, parse_playlist

# The line prefix yt-dlp prints our progress on.
_PROGRESS_MARKER = "VIDDLPROGRESS"
_PROGRESS_TEMPLATE = (
    "download:" + _PROGRESS_MARKER + "|"
    "%(progress._percent)s|"
    "%(progress._speed)s|"
    "%(progress._eta)s|"
    "%(progress.downloaded_bytes)s|"
    "%(progress.total_bytes)s|"
    "%(progress.total_bytes_estimate)s|"
    "%(progress.filename)s"
)

# "Video title [dQw4w9WgXcQ].mp4" — readable, and never silently overwrites.
_OUTPUT_TEMPLATE = "%(title)s [%(id)s].%(ext)s"

_DESTINATION_RE = re.compile(r"Destination:\s*(.+?)\s*$")
_MERGE_RE = re.compile(r'Merging formats into "(.+)"')
_ALREADY_RE = re.compile(r"^\[download\]\s+(.+?)\s+has already been downloaded")
#: yt-dlp's words when the download archive already holds this video (F6).
#: Two shapes, both measured against the real tool: after extraction it is
#: ``[download] <id>: <title> has already been recorded in the archive``, and
#: when the id can be read straight from the link it is the same line without
#: the title. The id is the first field either way.
_ARCHIVED_RE = re.compile(
    r"^\[download\] (.+?): (?:.* )?has already been recorded in the archive$"
)
_BAD_SUFFIXES = (".part", ".ytdl", ".temp", ".tmp")
#: Files beside the media that are part of a download but are not it (F5):
#: the cover picture, and the captions before they are embedded. They must
#: never be mistaken for the video — a download that reported its own cover as
#: "the file you asked for" would hand the user a 23 KB JPEG.
_ATTACHMENT_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
_CAPTION_SUFFIXES = (".vtt", ".srt", ".ass", ".ssa", ".ttml", ".json3", ".lrc")
# yt-dlp names the separate streams it merges "Title [id].f137.mp4".
_FRAGMENT_RE = re.compile(r"\.f\d+$|\.temp$", re.IGNORECASE)


class DownloadCancelled(Exception):
    """The user asked to stop."""


#: What yt-dlp leaves beside a file it is still working on, and resumes from.
_PARTIAL_SUFFIXES = (".part", ".ytdl")

#: How long a stopped download gets to put its things down before it is killed.
#: yt-dlp answers a polite stop quickly; a wedged one never does.
_STOP_GRACE_SECONDS = 5.0

#: How many times one download is tried when the failure is the kind a second
#: attempt may fix. Three goes: a rate limit or a dropped connection is usually
#: gone by then, and a fourth would delay the bad news for nothing.
DEFAULT_ATTEMPTS = 3

#: The first wait after a temporary failure; each further wait doubles it, so a
#: site having a bad minute is not hammered by the thing that is failing.
RETRY_BASE_SECONDS = 4.0

#: A wait is slept in slices this long, so a cancel is felt at once instead of
#: whenever the wait happens to end. The user pressed stop; they mean now.
_RETRY_SLICE_SECONDS = 0.1

#: Marks a working folder as being written in right now. Creating it is the
#: test and the claim in one step, which is what keeps two windows apart.
_CLAIM_NAME = ".viddl-busy"

#: yt-dlp's own socket timeout, unless ``VIDDL_TIMEOUT`` says otherwise (R11).
_SOCKET_TIMEOUT = 20

#: How long a look-up may take before Viddl gives up on the site: seven and a
#: half socket waits, so that the bound moves with ``VIDDL_TIMEOUT`` instead of
#: being a number a person set a socket timeout beside and then forgot. The
#: default deck keeps the 150 s this used to be (20 × 7.5).
PROBE_TIMEOUT = 150
_LOOKUP_PER_SOCKET = 7.5


def socket_timeout() -> int:
    """Seconds yt-dlp should wait on a socket, as ``VIDDL_TIMEOUT`` asks."""
    return int(appdata.network_timeout_seconds(_SOCKET_TIMEOUT))


def lookup_timeout() -> float:
    """How long a whole look-up may take, before anything else is said.

    With ``VIDDL_TIMEOUT`` unset this is :data:`PROBE_TIMEOUT` exactly. When it
    is set the bound follows it, because the failure the bound exists for is
    "the site said nothing within the wait Viddl uses" — giving up at 150 s
    while waiting 300 s on each socket would be two answers to one question.
    """
    override = appdata.network_timeout()
    if override is None:
        return float(PROBE_TIMEOUT)
    return override * _LOOKUP_PER_SOCKET

#: How long a look-up's answer is remembered. Re-opening the same link — going
#: back for another quality, or clicking Find qualities twice because the first
#: press looked like nothing happened — must not ask the site all over again.
#: Five minutes: long enough to cover that, short enough to notice a change.
PROBE_CACHE_SECONDS = 300

#: At most this many links are remembered, so a long session cannot grow
#: without bound. The oldest answer goes when a new one arrives.
_PROBE_CACHE_MAX = 20

#: How many videos of a playlist are listed in the checklist (F2). A flat
#: listing is cheap — titles and links, no extraction per video — but a playlist
#: of five thousand is not a menu anybody reads, and building it would still
#: take a while. The count is shown either way, so a cut list says so.
PLAYLIST_LIMIT = 200

#: A stop is felt this often while a look-up runs. The user pressed a button;
#: they mean now, not whenever yt-dlp happens to finish.
_CANCEL_POLL_SECONDS = 0.1


class ProbeCancelled(Exception):
    """The user stopped the look-up. Not a failure: nothing to explain."""


def _is_intermediate(path: Path) -> bool:
    """True for a stream that only exists to be merged into the real file."""
    stem = path.name.rsplit(".", 1)[0]
    return bool(_FRAGMENT_RE.search(stem)) or path.name.endswith(_BAD_SUFFIXES)


def _is_attachment(path: Path) -> bool:
    """True for something downloaded *around* the media, not the media (F5)."""
    return path.suffix.lower() in _ATTACHMENT_SUFFIXES + _CAPTION_SUFFIXES


def _title_from_name(path: Path) -> str:
    """The video's title as the file name spells it: ``Title [id].mp4`` -> ``Title``.

    Used for the recent list when the caller had no title of its own: the
    output template is what named the file, so it is what can name it back.
    """
    stem = path.stem
    if stem.endswith("]") and " [" in stem:
        return stem.rsplit(" [", 1)[0].strip()
    return stem


@dataclass
class Progress:
    """A snapshot of what the downloader is doing right now."""

    status: str = "Starting…"
    percent: Optional[float] = None
    speed: str = ""
    eta: str = ""
    downloaded: int = 0
    total: Optional[int] = None
    phase: str = "download"


def _to_float(value: str) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _to_int(value: str) -> Optional[int]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number >= 0 else None


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


def seconds_text(seconds: float) -> str:
    """"4s", not "4.0s" — this is read in passing, not studied."""
    if abs(seconds - round(seconds)) < 1e-9:
        return f"{int(round(seconds))}s"
    return f"{seconds:.1f}s"


def stop_process_tree(process: subprocess.Popen, force: bool = False) -> None:
    """Ask every process a run started to stop, not only the one Viddl holds.

    On POSIX the child is given a session of its own (``start_new_session``), so
    its group holds yt-dlp and anything yt-dlp started — ffmpeg, while merging.
    Windows has no process groups to signal, so ``taskkill /T`` walks the tree
    instead, which is the same guarantee by another route.
    """
    if is_windows():
        command = ["taskkill", "/PID", str(process.pid), "/T"]
        if force:
            command.insert(1, "/F")
        try:
            subprocess.run(command, capture_output=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            pass
        return
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL if force else signal.SIGTERM)
    except OSError:
        # No group of its own (started by someone else, or already gone):
        # fall back to the process itself.
        try:
            process.kill() if force else process.terminate()
        except OSError:
            pass


@dataclass
class _ProbeAnswer:
    """A look-up that has already been paid for, and when."""

    info: MediaInfo
    at: float


#: Answers to look-ups, in memory only: nothing is written to disk, and quitting
#: Viddl forgets all of it.
_PROBE_CACHE: Dict[str, _ProbeAnswer] = {}
_PROBE_CACHE_LOCK = threading.Lock()

@dataclass
class _PlaylistAnswer:
    """A listed playlist that has already been paid for, and when."""

    playlist: Playlist
    at: float


#: Playlist listings, remembered the same way and for the same reason: going
#: back to the qualities screen and returning must not list the videos again.
_PLAYLIST_CACHE: Dict[str, _PlaylistAnswer] = {}


def clear_probe_cache() -> None:
    """Forget every remembered look-up, so the next one asks the site again."""
    with _PROBE_CACHE_LOCK:
        _PROBE_CACHE.clear()
        _PLAYLIST_CACHE.clear()


def _probe_key(url: str, cookies: str = "") -> str:
    """What an answer depends on: the link, the options the user added, cookies.

    Cookies and the like change what a site hands back, so they are part of the
    key: the same link with a signed-in session is a different question, and
    answering it with what an anonymous look-up saw would be wrong (F1).
    """
    return "|".join((url, cookies, *appdata.ytdlp_arguments()))


def _remembered_probe(key: str) -> Optional[MediaInfo]:
    """The answer to this look-up if it is still fresh, else ``None``."""
    now = time.monotonic()
    with _PROBE_CACHE_LOCK:
        answer = _PROBE_CACHE.get(key)
        if answer is None:
            return None
        if now - answer.at > PROBE_CACHE_SECONDS:
            del _PROBE_CACHE[key]
            return None
        return answer.info


def _remember_probe(key: str, info: MediaInfo) -> None:
    """Keep an answer for a few minutes, dropping the oldest if need be."""
    with _PROBE_CACHE_LOCK:
        _PROBE_CACHE[key] = _ProbeAnswer(info, time.monotonic())
        while len(_PROBE_CACHE) > _PROBE_CACHE_MAX:
            oldest = min(_PROBE_CACHE, key=lambda held: _PROBE_CACHE[held].at)
            del _PROBE_CACHE[oldest]


def _remembered_playlist(key: str) -> Optional[Playlist]:
    """The listing of this playlist if it is still fresh, else ``None``."""
    now = time.monotonic()
    with _PROBE_CACHE_LOCK:
        answer = _PLAYLIST_CACHE.get(key)
        if answer is None:
            return None
        if now - answer.at > PROBE_CACHE_SECONDS:
            del _PLAYLIST_CACHE[key]
            return None
        return answer.playlist


def _remember_playlist(key: str, playlist: Playlist) -> None:
    with _PROBE_CACHE_LOCK:
        _PLAYLIST_CACHE[key] = _PlaylistAnswer(playlist, time.monotonic())
        while len(_PLAYLIST_CACHE) > _PROBE_CACHE_MAX:
            oldest = min(_PLAYLIST_CACHE, key=lambda held: _PLAYLIST_CACHE[held].at)
            del _PLAYLIST_CACHE[oldest]


def _run_lookup(
    args: List[str],
    timeout: float,
    cancel: Optional[threading.Event],
) -> Tuple[int, str, str]:
    """Run a look-up that can be stopped while it runs.

    ``subprocess.run`` cannot be interrupted, which is the whole problem with a
    150-second probe: the screen would sit there with no way out. This starts
    yt-dlp in a session of its own, waits in slices, and kills the tree when the
    user asks it to stop — or when the site has said nothing for ``timeout``.
    """
    if cancel is not None and cancel.is_set():
        # Stopped before it began: starting a process only to kill it would be
        # work for nobody, and the site would be asked for a link the user has
        # already decided not to wait for.
        raise ProbeCancelled()
    options: Dict[str, object] = {}
    if is_windows():
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    try:
        process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            errors="replace",
            **options,
        )
    except OSError as exc:
        raise ToolError(
            "The video engine could not be started.",
            f"Try running Viddl again. Details: {exc}",
        ) from exc

    deadline = time.monotonic() + timeout
    while True:
        try:
            stdout, stderr = process.communicate(timeout=_CANCEL_POLL_SECONDS)
        except subprocess.TimeoutExpired:
            if cancel is not None and cancel.is_set():
                stop_process_tree(process, force=True)
                process.communicate()
                raise ProbeCancelled()
            if time.monotonic() >= deadline:
                stop_process_tree(process, force=True)
                process.communicate()
                raise ToolError(
                    "The site took too long to answer.",
                    "Check your connection, or try the link again in a moment.",
                    raw=f"nothing heard from the site in {timeout:g} seconds",
                    temporary=True,
                )
            continue
        return process.returncode, stdout or "", stderr or ""


def probe_video(
    engine: Engine,
    url: str,
    timeout: Optional[float] = None,
    cancel: Optional[threading.Event] = None,
    cookies: str = "",
) -> MediaInfo:
    """Ask yt-dlp what is behind a link. Raises ``ToolError`` on failure.

    Two things make this survivable on a slow site. An answer that is only
    minutes old is reused, so going back and forth between the link box and the
    qualities screen does not re-ask the site, and the whole look-up can be
    stopped through ``cancel`` — the process tree is killed, and
    :class:`ProbeCancelled` is raised rather than a failure being reported for
    something the user chose.

    ``timeout`` is the "the site is not answering at all" bound, and defaults to
    :func:`lookup_timeout` so that ``VIDDL_TIMEOUT`` moves it with the socket
    waits rather than leaving two unrelated numbers to disagree (R11).

    ``cookies`` names a browser whose own cookie store yt-dlp may read (F1),
    which is what lets a look-up see the qualities of a video that only answers
    a signed-in session. It changes the answer, so it is part of the key the
    answer is remembered under.
    """
    if timeout is None:
        timeout = lookup_timeout()
    key = _probe_key(url, cookies)
    remembered = _remembered_probe(key)
    if remembered is not None:
        return remembered

    tool = engine.ensure_ytdlp()
    args = tool.command(
        "--ignore-config",
        "--dump-single-json",
        "--no-playlist",
        # A playlist link is still read as one video's worth of metadata: the
        # first entry, fully extracted. Without this, a playlist of fifty items
        # is fifty extractions — minutes of waiting, megabytes of JSON — for the
        # one entry the app is willing to use, and a single unavailable video
        # anywhere in the list fails the whole look-up.
        "--playlist-end",
        "1",
        "--no-warnings",
        "--no-progress",
        "--socket-timeout",
        str(socket_timeout()),
        "--no-color",
    )
    # The same extra options as a download: a link that needs cookies to be read
    # would otherwise show an empty quality menu, which is a worse failure than
    # a probe that fails with a reason.
    args += cookies_args(cookies)
    args += appdata.ytdlp_arguments()
    # "--" ends the options: the link can never be read as one.
    args += ["--", url]

    returncode, stdout, stderr = _run_lookup(args, timeout, cancel)

    if returncode != 0 or not (stdout or "").strip():
        raw = (stderr or "") + "\n" + (stdout or "")
        friendly = explain(raw, returncode)
        raise ToolError(
            friendly.message,
            friendly.hint,
            raw=raw.strip(),
            temporary=friendly.temporary,
        )

    try:
        info = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ToolError(
            "The site sent back something Viddl could not read.",
            "This usually means the site changed. Trying again later often fixes it.",
            raw=str(exc),
        ) from exc

    if not isinstance(info, dict):
        raise ToolError("That link did not point to a single video.")
    parsed = parse_info(info)
    # Only a look-up that worked is worth remembering: a failure is the thing
    # most likely to be fixed by trying again, and caching it would take that
    # away for five minutes.
    _remember_probe(key, parsed)
    return parsed


def probe_playlist(
    engine: Engine,
    url: str,
    timeout: Optional[float] = None,
    cancel: Optional[threading.Event] = None,
    cookies: str = "",
    limit: int = PLAYLIST_LIMIT,
) -> Playlist:
    """List a playlist's videos, without reading any of them in full (F2).

    ``--flat-playlist`` is what makes this affordable: yt-dlp asks the site for
    the list and each video's title, not for each video page. A playlist of two
    hundred can therefore be shown in seconds, where a full look-up of every
    entry would take minutes and fail entirely if one video in the middle had
    been deleted.

    What the listing cannot give is a complete video — only enough to choose
    from. Each chosen video is then downloaded on its own, so one bad entry
    costs one item in the queue rather than the whole list.
    """
    if timeout is None:
        timeout = lookup_timeout()
    # Keyed the same way as a look-up — link, cookies, the user's own extra
    # options — plus the list length, because a listing of 5 is not the answer
    # to a question about 200.
    key = f"playlist|{limit}|" + _probe_key(url, cookies)
    remembered = _remembered_playlist(key)
    if remembered is not None:
        return remembered

    tool = engine.ensure_ytdlp()
    args = tool.command(
        "--ignore-config",
        "--flat-playlist",
        "--dump-single-json",
        "--playlist-end",
        str(max(1, int(limit))),
        "--no-warnings",
        "--no-progress",
        "--socket-timeout",
        str(socket_timeout()),
        "--no-color",
    )
    args += cookies_args(cookies)
    args += appdata.ytdlp_arguments()
    args += ["--", url]

    returncode, stdout, stderr = _run_lookup(args, timeout, cancel)

    if returncode != 0 or not (stdout or "").strip():
        raw = (stderr or "") + "\n" + (stdout or "")
        friendly = explain(raw, returncode)
        raise ToolError(
            friendly.message,
            friendly.hint,
            raw=raw.strip(),
            temporary=friendly.temporary,
        )

    try:
        info = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ToolError(
            "The site sent back something Viddl could not read.",
            "This usually means the site changed. Trying again later often fixes it.",
            raw=str(exc),
        ) from exc

    if not isinstance(info, dict):
        raise ToolError("That link did not point to a playlist.")
    playlist = parse_playlist(info)
    if not playlist.entries:
        raise ToolError(
            "This playlist has no videos in it that Viddl can see.",
            "It may be empty or private. Press Escape to try another link.",
        )
    _remember_playlist(key, playlist)
    return playlist


class DownloadJob:
    """One download, run in a background thread with cancel support."""

    def __init__(
        self,
        engine: Engine,
        url: str,
        option: MediaOption,
        out_dir: Path,
        on_progress: Optional[Callable[[Progress], None]] = None,
        on_log: Optional[Callable[[str], None]] = None,
        keep_partial: bool = True,
        attempts: int = DEFAULT_ATTEMPTS,
        retry_delay: float = RETRY_BASE_SECONDS,
        cookies: str = "",
        playlist_item: Optional[int] = None,
        extras: Optional[Extras] = None,
        archive: Optional[Path] = None,
        title: str = "",
    ):
        self.engine = engine
        self.url = url
        self.option = option
        self.out_dir = Path(out_dir)
        # The browser whose cookies this download may use (F1). Empty — the
        # default — means the download runs as nobody in particular, which is
        # how every download ran before the choice existed.
        self.cookies = cookies
        # Which item of the playlist at ``url`` to take (F2). ``None`` is one
        # video, which is every download that is not part of a queue; a number
        # is used when the listing gave an identifier rather than an address,
        # so the playlist itself has to be asked again for that item.
        self.playlist_item = playlist_item
        # What is being added to the media besides the media itself (F5).
        # Nothing, unless it was asked for: ``Extras()`` is the default.
        self.extras = extras or Extras()
        # The download archive, when the run asked for one (F6). yt-dlp
        # records each finished video in it and refuses to fetch one it
        # already holds — which is what makes the same link, or the rest of a
        # playlist, cost nothing the second time. ``None`` is every run that
        # did not ask, which is the default: skipping a download is a promise
        # to keep, not a surprise to spring.
        self.archive = Path(archive) if archive is not None else None
        # The video's own title, for the recent list. It is the one thing the
        # file name cannot always give back cleanly (a title that was itself
        # ``Name [abc].mp4``), so a caller that knows it passes it in.
        self.title = title
        # True once this run turned out to be a video an earlier download had
        # already put on disk (F6). The file is real and where it always was;
        # the flag only stops the app from claiming it just fetched it.
        self.already_had = False
        # Filled in by ``_deliver``: the cover picture is saved beside the
        # finished file, and the screen says so instead of leaving an
        # unexplained image in the folder.
        self.attachments: List[Path] = []
        self.on_progress = on_progress
        self.on_log = on_log
        # Keeping what a stopped download fetched is what makes resuming
        # possible, so it is the default. ``keep_partial=False`` is the old
        # tidy-up behaviour, for a caller that wants it.
        self.keep_partial = keep_partial
        # Retrying is on by default, because a temporary failure that nobody
        # retried is indistinguishable, from where the user sits, from the site
        # being broken. Callers that want one go can ask for one.
        self.attempts = max(1, int(attempts))
        self.retry_delay = max(0.0, float(retry_delay))

        self._process: Optional[subprocess.Popen] = None
        self._escalation: Optional[threading.Timer] = None
        self._cancelled = threading.Event()
        self._work_dir: Optional[Path] = None
        self._claim: Optional[Path] = None
        self._started = False
        self._lines: List[str] = []
        self._paths: List[Path] = []
        self.progress = Progress()
        self.started_at = time.time()

    # -- where the bytes go --------------------------------------------------
    def work_dir(self) -> Path:
        """The folder this download writes in, inside the downloads folder.

        Two Viddl windows downloading the same video used to aim at the same
        output name, which means the same ``.part`` file and the same finished
        file: whoever finished last won, and the other reported a path whose
        bytes had changed underneath it. Each run now writes in a folder of its
        own, and only the finished file is moved out of it — see
        :meth:`_deliver`.

        The name is derived from *what is being downloaded*, not from this
        object, so a download stopped today is found again tomorrow and carries
        on from its ``.part``. Two runs that overlap cannot share it: whoever
        claims it first owns it, and the other takes the next name along (see
        :meth:`_enter_work_dir`).
        """
        if self._work_dir is None:
            self._work_dir = self.out_dir / f".viddl-{self._identity()}"
        return self._work_dir

    def _identity(self) -> str:
        """A stable, short name for this link at this quality.

        The extras are part of it because they change what a run writes: two
        windows taking the same video with different extras are two different
        jobs and must not share a working folder.
        """
        raw = "|".join(
            (
                self.url,
                self.option.format_selector,
                self.option.merge_container or "",
                self.extras.signature(),
            )
        )
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]

    @property
    def needs_ffmpeg(self) -> bool:
        """True when this download cannot finish without ffmpeg.

        Either the quality itself needs it (a merge or a conversion) or the
        extras do (they are written by ffmpeg). One answer from one place, so
        the app, the plain path and the failure message cannot disagree.
        """
        return self.option.needs_ffmpeg or self.extras.needs_ffmpeg_for(self.option)

    def _enter_work_dir(self) -> Path:
        """Claim a working folder for this run, and remember which one.

        ``O_EXCL`` is the whole mechanism: creating the claim file either works
        or tells us somebody else is already writing in that folder, with no
        window in between for both to decide it is free. A folder left by a
        stopped download has no claim, so it is claimed again and its ``.part``
        is resumed; a folder another window is using is left alone and the next
        name along is tried.
        """
        base = self.work_dir()
        for number in range(1, 100):
            candidate = base if number == 1 else base.with_name(f"{base.name}-{number}")
            claim = candidate / _CLAIM_NAME
            try:
                candidate.mkdir(parents=True, exist_ok=True)
                handle = os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                # A file where the folder should be, or a claim somebody else
                # holds: either way this name is not ours, so try the next.
                continue
            except OSError as exc:
                # Not a collision — the folder cannot be written in at all. Say
                # that, rather than looping and blaming too many downloads.
                raise ToolError(
                    "Viddl could not make room for the download.",
                    f"It is not allowed to write in {self.out_dir}: {exc}",
                    raw=str(exc),
                ) from exc
            os.close(handle)
            self._work_dir = candidate
            self._claim = claim
            return candidate
        raise ToolError(
            "Too many copies of this download are already running.",
            f"There are twenty or more working folders in {self.out_dir}. "
            "Wait for the other downloads to finish, or delete the hidden "
            "folders left behind by stopped ones.",
        )

    def _release_claim(self) -> None:
        """Let go of the folder, keeping what it holds; drop it if it is empty.

        A stopped download keeps its ``.part`` so the next run carries on, so
        the folder stays and simply loses its claim. A download that got
        nowhere has nothing worth keeping, and leaving an empty hidden folder in
        somebody's downloads folder is just litter.
        """
        claim = self._claim
        if claim is None:
            return
        self._claim = None
        try:
            claim.unlink()
        except OSError:
            pass
        try:
            empty = not any(self.work_dir().iterdir())
        except OSError:
            return
        if empty:
            shutil.rmtree(self.work_dir(), ignore_errors=True)

    # -- command ------------------------------------------------------------
    def build_args(self) -> List[str]:
        tool = self.engine.ensure_ytdlp()
        args = tool.command(
            # Start from a known state, and never let a local yt-dlp config file
            # add options (or commands) to the run behind the user's back.
            "--ignore-config",
            "--newline",
        )
        if self.playlist_item is None:
            # One video, and the screen said so. ``--no-playlist`` only covers a
            # video link that carries a list; a playlist link of its own would
            # be downloaded whole — six files for a link the menu described as
            # one — so the length is cut to one as well.
            args += ["--no-playlist", "--playlist-end", "1"]
        else:
            # This one video, by its place in the playlist (F2): used when the
            # listing gave the site's own identifier instead of an address, so
            # the playlist has to be asked for the item rather than the item
            # asked for by name.
            args += ["--playlist-items", str(int(self.playlist_item))]
        args += [
            "--no-color",
            "--no-warnings",
            # Carry on from a ".part" file a previous attempt left. yt-dlp does
            # this by default, but the default is the thing being relied on, so
            # it is asked for: that is also what makes the prompt honest, since
            # a user's own config cannot switch it off (`--ignore-config` above)
            # and neither can a future change to yt-dlp's defaults.
            "--continue",
            "--retries",
            "5",
            "--fragment-retries",
            "5",
            "--socket-timeout",
            str(socket_timeout()),
            "--concurrent-fragments",
            "4",
            "--progress",
            "--progress-template",
            _PROGRESS_TEMPLATE,
            "--trim-filenames",
            "150",
        ]
        if self.option.merge_container and self.option.needs_ffmpeg:
            args += ["--merge-output-format", self.option.merge_container]
        args += self.engine.ffmpeg_location_args()
        if self.archive is not None:
            # What has already been fetched is remembered across runs (F6):
            # yt-dlp records a finished video here and skips it next time,
            # which is also what makes re-running a playlist take only the
            # videos it has not seen. It comes before Viddl's own paths so a
            # user's extra options (R3) cannot point it somewhere else.
            args += ["--download-archive", str(self.archive)]
        args += [
            # Down into the job's own folder, not straight into the downloads
            # folder: another window may be writing there.
            "-P",
            str(self.work_dir()),
            "-o",
            _OUTPUT_TEMPLATE,
            "-f",
            self.option.format_selector,
        ]
        args += list(self.option.extra_args)
        # The extras that were switched on for this download (F5). Appended
        # after the quality's own arguments so a conversion happens first and
        # the extras are written into the converted file.
        args += self.extras.args_for(self.option)
        # The browser's own cookies, when the person asked for a signed-in
        # download (F1). Read in place by yt-dlp: nothing is copied, and
        # nothing is written down.
        args += cookies_args(self.cookies)
        # Options the user asked for by name (R3). They come last, so that where
        # they and Viddl's own settings disagree theirs win — the few that would
        # make the app lose track of the file it is watching for are refused
        # before they get here (see ``appdata.ytdlp_arguments``).
        args += appdata.ytdlp_arguments()
        # "--" ends the options, so the link is always read as a link even if
        # it starts with a dash. Without this, a page handing over
        # "--config-locations=./x.conf" would have it treated as a flag.
        args.append("--")
        args.append(self.url)
        return args

    # -- lifecycle ----------------------------------------------------------
    def cancel(self, force: bool = False) -> None:
        """Stop the download — the whole of it, not just the process Viddl holds.

        yt-dlp starts ffmpeg to merge the two streams it downloaded, so the
        process Viddl started is only the top of a tree. Signalling it alone
        leaves the merge running: it keeps writing to the very file the app is
        about to offer back as resumable, and it keeps the pipe Viddl is
        reading open, so the screen can sit on "Stopping…" for ever. The job is
        therefore started in its own session and the whole group is signalled.

        The stop is polite first (so yt-dlp can finish the line it is on), and
        killed ``_STOP_GRACE_SECONDS`` later if it is still there. ``force``
        skips the politeness: quitting the app cannot wait for a watchdog that
        will die with the app.
        """
        self._cancelled.set()
        process = self._process
        if process is None or process.poll() is not None:
            return
        if force:
            self._signal_tree(process, force=True)
            return
        self._signal_tree(process, force=False)
        timer = threading.Timer(_STOP_GRACE_SECONDS, self._escalate)
        timer.daemon = True
        self._escalation = timer
        timer.start()

    def _escalate(self) -> None:
        """The polite stop was ignored: kill the tree outright."""
        process = self._process
        if process is not None and process.poll() is None:
            self._signal_tree(process, force=True)

    def _signal_tree(self, process: subprocess.Popen, force: bool) -> None:
        """Ask every process the download started to stop.

        The same job a look-up does when it is stopped, which is why it is one
        function and not two: yt-dlp starts ffmpeg, and signalling the top of
        that tree alone leaves the merge running.
        """
        stop_process_tree(process, force=force)

    def run(self) -> Path:
        """Download and return the finished file. Raises on failure.

        A failure that might just as easily not have happened — a rate limit, a
        server having a bad minute, a connection that dropped — is tried again
        after a doubling wait, because "it worked the second time" is a better
        outcome than an accurate explanation of why it did not. Nothing else is
        retried: a private video stays private, and trying again would only keep
        the user waiting for the same answer.

        What happened the first time is not thrown away. yt-dlp is asked to
        continue, so a second attempt picks up from the ``.part`` file the first
        one left rather than fetching the same bytes again.
        """
        self.out_dir.mkdir(parents=True, exist_ok=True)
        if self._started:
            # Running again after a stop (Resume) is a fresh attempt: the stop
            # that ended the last one must not be remembered as this one's.
            # The first run does not clear it — a stop that arrived before the
            # engine was even started is a reason not to start it.
            self._cancelled = threading.Event()
            self._process = None
            self._escalation = None
        if self._cancelled.is_set():
            raise DownloadCancelled()
        self._started = True
        self._enter_work_dir()

        try:
            before = self._snapshot()
            args = self.build_args()
            if self._cancelled.is_set():
                # Stopped while the command was being built: never start it. The
                # alternative is a process nobody is left to stop.
                raise DownloadCancelled()
            if self.on_log:
                self.on_log("$ " + " ".join(args))

            for attempt in range(1, self.attempts + 1):
                try:
                    finished = self._attempt(args, before)
                except ToolError as exc:
                    if self._cancelled.is_set():
                        # The attempt was failing as the stop arrived. The user
                        # asked to stop, which is what this is — not a failure
                        # to explain to them.
                        raise DownloadCancelled() from exc
                    if exc.temporary and attempt < self.attempts:
                        wait = self._retry_wait(attempt)
                        self._announce_retry(exc, attempt, wait)
                        if not self._wait(wait):
                            raise DownloadCancelled() from exc
                        continue
                    if exc.temporary:
                        raise self._after_failure(exc, attempt) from exc
                    raise
                else:
                    path, already_had = finished
                    if already_had:
                        return self._deliver_existing(path)
                    return self._deliver(path)
        except DownloadCancelled:
            # Stopped by the user: what was fetched stays, so running this
            # download again carries on from it rather than starting over.
            self._release_claim()
            raise
        except ToolError:
            # Failed: the same kindness applies to whatever it managed to fetch.
            self._release_claim()
            raise
        raise AssertionError("unreachable: every path returns or raises")  # pragma: no cover

    # -- one attempt ---------------------------------------------------------
    def _attempt(self, args: List[str], before: Dict[Path, float]) -> Tuple[Path, bool]:
        """Run the downloader once; the finished file, and whether it was already here.

        The second half is the download archive (F6). A video it already holds
        is not fetched again — but the file from the earlier download is in
        the downloads folder, not in this run's working folder, so "the run
        produced no file" can mean *you already have it* rather than *it
        broke*. The difference is found here, where the only evidence either
        way lives: the line yt-dlp printed, and the folder the first run
        delivered to.
        """
        # A second go happens only after the archive gave up a line for a
        # video whose file has since been deleted from disk.
        skipped_by_archive = False
        for round_number in (0, 1):
            # Each attempt speaks for itself: lines from the attempt that failed are
            # not evidence about this one, and carrying them over would both grow
            # without bound and report the wrong reason if this one fails too.
            self._lines.clear()
            if self.progress.phase == "retry":
                # The wait is over. Saying so matters: the screen decides whether to
                # move the bar from the phase, so leaving it on "retry" would keep
                # the download looking paused for as long as it took to report.
                self._emit(status="Downloading…", speed="", eta="", phase="download")

            code = self._run_once(args)
            if code != 0:
                raw = "\n".join(self._lines[-60:])
                friendly = explain(raw, code)
                # ``temporary`` is what ``run`` decides from: it is the single
                # place that says whether trying again is worth the wait.
                raise ToolError(
                    friendly.message,
                    friendly.hint,
                    raw=raw,
                    temporary=friendly.temporary,
                )

            finished = self._find_output(before)
            if finished is not None:
                return finished, False

            video_id = self._archived_video_id()
            if video_id is None:
                break
            skipped_by_archive = True
            existing = self._existing_download(video_id)
            if existing is not None:
                return existing, True
            if round_number == 0 and history.forget_video(self.archive, video_id):
                # The archive said this video was downloaded, but the file is
                # not there any more. Forget the line and ask once more: a
                # memory that blocks a download the user asked for is worse
                # than no memory at all.
                continue
            break

        if self.needs_ffmpeg and not skipped_by_archive:
            # Measured, not assumed: yt-dlp exits 0 having downloaded both
            # streams when ffmpeg is missing and only the merge fails, so
            # "it finished" is not the same as "there is a file". The
            # streams it left are not the video the menu promised, and
            # reporting one of them would hand the user an audio fragment
            # with a .mp4-shaped name.
            #
            # A run the archive skipped is exempt: nothing was fetched, so
            # ffmpeg never had the chance to fail, and naming it here would
            # send the user to set up a tool that was not the problem.
            raise ToolError(
                "The video and audio could not be joined into one file.",
                "That needs ffmpeg, which is not available. Run "
                "`viddl --update-ffmpeg` (about 150 MB), or choose a quality "
                "that does not need it.",
                raw="\n".join(self._lines[-60:]),
            )
        raise ToolError(
            "The download finished but the file could not be found.",
            f"Look in your Downloads folder: {self.out_dir}",
        )

    def _run_once(self, args: List[str]) -> int:
        """Start the downloader, read what it says, and wait for it to end.

        The exit code is the answer. Split out of :meth:`_attempt` because
        that method may run this twice: once against the archive's answer, and
        once after the archive has been corrected (F6).
        """
        options: Dict[str, object] = {}
        if is_windows():
            # A group of its own, so taskkill can find the whole tree.
            options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            # Its own session: cancel can signal yt-dlp and the ffmpeg it
            # started together. One side effect is that a Ctrl+C in the
            # terminal no longer reaches yt-dlp directly — the app's own stop
            # button does, through the same group signal.
            options["start_new_session"] = True
        try:
            self._process = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                errors="replace",
                **options,
            )
        except OSError as exc:
            raise ToolError(
                "The video engine could not be started.",
                "Try running Viddl again.",
                raw=str(exc),
            ) from exc

        assert self._process.stdout is not None
        for line in self._process.stdout:
            self._handle_line(line.rstrip("\n"))
            if self._cancelled.is_set():
                break
        try:
            self._process.wait(timeout=_STOP_GRACE_SECONDS * 3)
        except subprocess.TimeoutExpired:  # pragma: no cover - a wedged process
            self._signal_tree(self._process, force=True)
            self._process.wait()
        finally:
            self._stop_watchdog()
        code = self._process.returncode

        if self._cancelled.is_set():
            if not self.keep_partial:
                self._cleanup_partial()
            raise DownloadCancelled()

        return code or 0

    # -- what the archive already had (F6) -----------------------------------
    def _archived_video_id(self) -> Optional[str]:
        """The video the download archive says it already holds, if it refused.

        yt-dlp exits 0 for a skip — from the outside it looks like a success —
        so this line is the only thing telling the run that no file was ever
        going to appear.
        """
        if self.archive is None:
            return None
        for line in reversed(self._lines):
            match = _ARCHIVED_RE.match(line.strip())
            if match:
                return match.group(1).strip()
        return None

    def _existing_download(self, video_id: str) -> Optional[Path]:
        """The file an earlier download of this video left in the output folder.

        The output template names every file after its video
        (``Title [id].mp4``), so the bracketed id is the thing to look for —
        and the brackets are what keep a short id from matching the end of a
        longer one. Attachments and half-finished files are skipped, the same
        as anywhere else a file is reported as "the download": a cover image
        is not the video, and a ``.part`` file is not finished (F5).

        Several can exist — the same video at another quality, or the same
        quality downloaded twice — and the newest is the one to show: it is
        what the most recent run would have delivered.
        """
        marker = f"[{video_id}]"
        newest: Optional[Tuple[float, Path]] = None
        try:
            candidates = list(self.out_dir.iterdir())
        except OSError:
            return None
        for entry in candidates:
            try:
                if not entry.is_file() or marker not in entry.name:
                    continue
            except OSError:  # pragma: no cover - a file that vanished mid-scan
                continue
            if _is_intermediate(entry) or _is_attachment(entry):
                continue
            try:
                mtime = entry.stat().st_mtime
            except OSError:  # pragma: no cover - a file that vanished mid-scan
                continue
            if newest is None or mtime > newest[0]:
                newest = (mtime, entry)
        return newest[1] if newest else None

    def _deliver_existing(self, path: Path) -> Path:
        """Report a file an earlier download already put there (F6).

        Nothing is moved: the file is where the user left it, and claiming a
        fresh name for it would litter the folder with a copy of a copy. The
        working folder goes — it held nothing — and the recent list is told,
        so the file floats back to the top of it, where the eye expects the
        thing just asked for.
        """
        self.already_had = True
        self._discard_work()
        self.progress = Progress(
            status="Already downloaded — using the file you have.",
            percent=100.0,
            phase="done",
        )
        if self.on_progress:
            self.on_progress(replace(self.progress))
        self._remember(path)
        return path

    # -- the recent list (F6) ------------------------------------------------
    def _remember(self, path: Path) -> None:
        """Tell the recent list about a file this job is delivering.

        Called for both endings — freshly fetched, and already on disk —
        because both are the user getting the file they asked for, and the
        list remembers what they *have*, not what the network carried.
        ``history.record`` never raises (see that module), so this needs no
        guard: a list that cannot be written is a lost nicety, never a reason
        to fail a download that worked.
        """
        history.record(
            url=self.url,
            path=path,
            title=self.title or _title_from_name(path),
        )

    # -- delivering the result ----------------------------------------------
    def _deliver(self, source: Path) -> Path:
        """Move the finished file into the downloads folder, never onto a file.

        The name is claimed with an exclusive create *before* the move, so two
        windows finishing at the same instant take ``Title.mp4`` and
        ``Title (2).mp4`` rather than one overwriting the other. ``os.replace``
        then puts our bytes where the claim is, atomically: a reader either sees
        the claim (an empty file, for a moment) or the finished file, never half
        of one download and half of another.
        """
        target = self._claim_name(source.name)
        try:
            os.replace(source, target)
        except OSError:
            # Nothing else can be done with a name we could not fill; leaving an
            # empty file behind under a real name would be the worse outcome.
            try:
                target.unlink()
            except OSError:
                pass
            # The finished file is still in the working folder, so let the next
            # run have it back rather than keeping the folder claimed for ever.
            self._release_claim()
            raise
        self._deliver_attachments(source, target)
        self._discard_work()
        self.progress = Progress(status="Done", percent=100.0, phase="done")
        if self.on_progress:
            self.on_progress(self.progress)
        # Told what just landed, while the size and the folder are all here
        # (F6): the recent list is built from this, not from guessing later.
        self._remember(target)
        return target

    def _deliver_attachments(self, source: Path, target: Path) -> None:
        """Move the cover picture beside the finished file (F5).

        The picture is not part of the media and must not replace it, so it is
        moved *after* the media and named after the name the media actually
        got (``Title (2).mp4`` gets ``Title (2).jpg``). A picture that cannot
        be moved is a reason to say so in the log, never to fail a download
        that otherwise worked: the video is what was asked for.
        """
        self.attachments = []
        try:
            siblings = list(self.work_dir().iterdir())
        except OSError:
            return
        for sibling in siblings:
            if sibling == source or sibling.suffix.lower() not in _ATTACHMENT_SUFFIXES:
                continue
            if sibling.stem != source.stem:
                continue
            claimed = self._claim_name(f"{target.stem}{sibling.suffix.lower()}")
            try:
                os.replace(sibling, claimed)
            except OSError as exc:
                try:
                    claimed.unlink()
                except OSError:
                    pass
                if self.on_log:
                    self.on_log(f"Could not save the cover image: {exc}")
                continue
            self.attachments.append(claimed)

    def _claim_name(self, filename: str) -> Path:
        """Reserve the first free name for ``filename`` in the output folder.

        O_EXCL is the whole point: the check and the claim are one step, so no
        two jobs can both decide a name is free and then both write to it.
        """
        stem, suffix = os.path.splitext(filename)
        for number in range(1, 1000):
            name = filename if number == 1 else f"{stem} ({number}){suffix}"
            target = self.out_dir / name
            try:
                handle = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                continue
            os.close(handle)
            return target
        raise ToolError(
            "There are already too many files with that name.",
            f"Tidy up the ones you do not need in {self.out_dir} and try again.",
        )

    # -- internals ----------------------------------------------------------
    def _retry_wait(self, attempt: int) -> float:
        """How long to wait after ``attempt`` failed: the base, then doubled."""
        return self.retry_delay * (2 ** (attempt - 1))

    def _announce_retry(self, exc: ToolError, attempt: int, wait: float) -> None:
        """Say, on the progress screen, that waiting is happening and why.

        A silent pause looks like a hang; a pause with a reason looks like care.
        The bar keeps the place it had reached, because the wait is not progress
        and feeling the work start over would be untrue.
        """
        status = (
            f"Retrying in {seconds_text(wait)} "
            f"(attempt {attempt + 1} of {self.attempts})…"
        )
        self._emit(status=status, speed="", eta="", phase="retry")
        if self.on_log:
            self.on_log(f"{exc.message} {status}")

    def _wait(self, seconds: float) -> bool:
        """Wait between attempts; False if the user stopped while waiting.

        Slept in slices so the stop button is answered at once. A cancel that
        arrives during the wait does not start another attempt, and leaves the
        ``.part`` file alone exactly as stopping mid-download does.
        """
        deadline = time.monotonic() + seconds
        while True:
            if self._cancelled.is_set():
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return True
            time.sleep(min(_RETRY_SLICE_SECONDS, remaining))

    def _after_failure(self, exc: ToolError, attempt: int) -> ToolError:
        """The error to show once trying again is no longer an option.

        Saying how many times Viddl tried matters: "the site is busy" reads
        differently when the user knows it was already asked three times, and
        the next sensible step is to wait rather than press the button again.
        """
        if attempt <= 1:
            # Tried once: there was no wait to explain, and "tried 1 times"
            # would be a strange thing to read.
            return exc
        note = (
            f"Viddl tried {attempt} times, waiting longer between each attempt, and "
            "the site answered the same way every time."
        )
        hint = f"{exc.hint}\n{note}" if exc.hint else note
        return ToolError(exc.message, hint, raw=exc.raw, temporary=exc.temporary)

    def _stop_watchdog(self) -> None:
        timer = self._escalation
        if timer is not None:
            timer.cancel()
            self._escalation = None

    def _snapshot(self) -> Dict[Path, float]:
        snapshot: Dict[Path, float] = {}
        try:
            for entry in self.work_dir().iterdir():
                if entry.is_file():
                    try:
                        snapshot[entry] = entry.stat().st_mtime
                    except OSError:
                        continue
        except OSError:
            pass
        return snapshot

    def partial_paths(self) -> List[Path]:
        """What this job left half-finished, ready to resume or to throw away.

        yt-dlp writes ``<destination>.part`` beside the file it is working on —
        one per stream when it is going to merge them — and both resuming and
        discarding work from those. Only files this job announced are listed:
        searching the folder by pattern could take somebody else's
        half-download with it, which is not a thing a cancel button may do.
        """
        found: List[Path] = []
        for path in self._paths:
            for suffix in _PARTIAL_SUFFIXES:
                candidate = Path(str(path) + suffix)
                if candidate not in found and candidate.exists():
                    found.append(candidate)
        return found

    def discard_partial(self) -> int:
        """Throw the half-finished files away; returns how many were there.

        The whole working folder goes, not only the ``.part`` files: it exists
        for this job alone, and leaving a stream or a stray fragment of it
        behind would be litter in the user's downloads folder.
        """
        removed = 0
        for path in self.partial_paths():
            try:
                path.unlink()
                removed += 1
            except OSError:
                continue
        self._discard_work()
        return removed

    def _discard_work(self) -> None:
        """Remove the working folder altogether, keeping quiet if it cannot go."""
        self._claim = None
        shutil.rmtree(self.work_dir(), ignore_errors=True)

    def _cleanup_partial(self) -> None:
        """What a cancel used to do to the files it had fetched. Still here,
        but only reached when a caller asks for it (``keep_partial=False``)."""
        self.discard_partial()

    def _handle_line(self, line: str) -> None:
        if line:
            self._lines.append(line)
        if line.startswith(_PROGRESS_MARKER):
            self._handle_progress_line(line)
            return

        if line.startswith(("[download]", "[Merger]", "[ExtractAudio]", "[FixupM3u8]")):
            if self.on_log:
                self.on_log(line)
        if line.startswith("[download] Destination:"):
            match = _DESTINATION_RE.search(line)
            if match:
                self._remember_path(match.group(1))
            self._emit(status="Downloading…")
        elif line.startswith("[download] Resuming download at byte"):
            # yt-dlp picked up where the last attempt stopped.
            self._emit(status="Resuming the interrupted download…")
        elif line.startswith("[Merger]"):
            # The merged file is what the user ends up with, so this is the
            # path we want to report, ahead of the separate streams.
            match = _MERGE_RE.search(line)
            if match:
                self._remember_path(match.group(1))
            self._emit(status="Merging video and audio…", percent=None, phase="merge")
        elif line.startswith("[EmbedSubtitle]"):
            self._emit(status="Adding the subtitles…", percent=None, speed="", eta="", phase="extras")
        elif line.startswith("[Metadata]"):
            self._emit(status="Adding the details…", percent=None, speed="", eta="", phase="extras")
        elif line.startswith(("[ThumbnailsConvertor]", "[Thumbnails]")):
            self._emit(status="Saving the cover image…", percent=None, speed="", eta="", phase="extras")
        elif line.startswith("[EmbedThumbnail]"):
            # Not a flag Viddl sets (see ``extras.py``), but a user's own
            # ``VIDDL_YTDLP_ARGS`` can still ask for it.
            self._emit(status="Adding the cover image…", percent=None, speed="", eta="", phase="extras")
        elif line.startswith("[ExtractAudio]"):
            # The container depends on the row that was chosen (F4), so the
            # sentence names it instead of assuming an MP3.
            target = self.option.audio_format or "audio"
            self._emit(status=f"Converting to {target}…", percent=None, phase="convert")
            match = _DESTINATION_RE.search(line)
            if match:
                self._remember_path(match.group(1))
        elif line.startswith("[FixupM3u8]"):
            self._emit(status="Finishing the file…", percent=None, phase="finish")
        elif line.startswith("[hlsnative]") or line.startswith("[download] Fragment"):
            self._emit(status="Downloading…")
        else:
            already = _ALREADY_RE.match(line)
            if already:
                self._remember_path(already.group(1))
                self._emit(status="Already downloaded — using the existing file.", percent=100.0)

    def _handle_progress_line(self, line: str) -> None:
        payload = line[len(_PROGRESS_MARKER):].lstrip("|")
        parts = payload.split("|")
        while len(parts) < 7:
            parts.append("NA")
        percent = _to_float(parts[0])
        speed = _to_float(parts[1])
        eta = _to_float(parts[2])
        downloaded = _to_int(parts[3]) or 0
        total = _to_int(parts[4]) or _to_int(parts[5])
        filename = parts[6].strip()
        if filename and filename != "NA":
            self._remember_path(filename)

        self._emit(
            status="Downloading…",
            percent=percent,
            speed=_fmt_speed(speed),
            eta=_fmt_eta(eta),
            downloaded=downloaded,
            total=total,
            phase="download",
        )

    def _remember_path(self, raw: str) -> None:
        candidate = raw.strip().strip('"').strip("'")
        if not candidate or candidate == "NA":
            return
        path = Path(candidate)
        if path.suffix.lower() in (".part", ".ytdl") or path.name.endswith(".part"):
            return
        if _is_attachment(path):
            # The cover picture and the caption files are announced exactly
            # like the video is, but they are not what was asked for (F5).
            return
        if path not in self._paths:
            self._paths.append(path)

    def _emit(self, **changes) -> None:
        for key, value in changes.items():
            setattr(self.progress, key, value)
        if self.on_progress:
            # Hand over a snapshot: the callback may hop to another thread, and
            # it should not see this object change under its feet.
            self.on_progress(replace(self.progress))

    def _find_output(self, before: Dict[Path, float]) -> Optional[Path]:
        # Prefer a real result over the streams that were merged to make it:
        # an intermediate file left behind (or kept via --keep-video) must not
        # be reported as "the file you just downloaded".
        existing = [path for path in self._paths if path.exists() and path.is_file()]
        for path in reversed(existing):
            if not _is_intermediate(path) and not _is_attachment(path):
                return path
        # Nothing but the streams a merge was supposed to join: that is not a
        # finished download, and returning one here is how a leftover audio
        # fragment used to be reported as the file the user asked for.

        # Only this job's own folder is scanned. The shared downloads folder is
        # not: another window's half-finished or just-finished file is not
        # this download, and reporting it would hand the user a path that can
        # change under them (R6).
        newest: Optional[Tuple[float, Path]] = None
        try:
            for entry in self.work_dir().iterdir():
                if not entry.is_file() or entry.name.endswith(_BAD_SUFFIXES):
                    continue
                if _is_intermediate(entry) or _is_attachment(entry):
                    continue
                try:
                    mtime = entry.stat().st_mtime
                except OSError:
                    continue
                if entry in before and before[entry] == mtime:
                    continue
                if newest is None or mtime > newest[0]:
                    newest = (mtime, entry)
        except OSError:
            return None
        return newest[1] if newest else None
