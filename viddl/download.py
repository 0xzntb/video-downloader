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
from .engine import Engine, ToolError, is_windows
from .errors import explain
from .formats import MediaInfo, MediaOption, parse_info

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
_BAD_SUFFIXES = (".part", ".ytdl", ".temp", ".tmp")
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


def _is_intermediate(path: Path) -> bool:
    """True for a stream that only exists to be merged into the real file."""
    stem = path.name.rsplit(".", 1)[0]
    return bool(_FRAGMENT_RE.search(stem)) or path.name.endswith(_BAD_SUFFIXES)


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


def _seconds_text(seconds: float) -> str:
    """"4s", not "4.0s" — this is read in passing, not studied."""
    if abs(seconds - round(seconds)) < 1e-9:
        return f"{int(round(seconds))}s"
    return f"{seconds:.1f}s"


def probe_video(engine: Engine, url: str, timeout: int = 150) -> MediaInfo:
    """Ask yt-dlp what is behind a link. Raises ``ToolError`` on failure."""
    tool = engine.ensure_ytdlp()
    args = tool.command(
        "--ignore-config",
        "--dump-single-json",
        "--no-playlist",
        "--no-warnings",
        "--no-progress",
        "--socket-timeout",
        "20",
        "--no-color",
    )
    # The same extra options as a download: a link that needs cookies to be read
    # would otherwise show an empty quality menu, which is a worse failure than
    # a probe that fails with a reason.
    args += appdata.ytdlp_arguments()
    # "--" ends the options: the link can never be read as one.
    args += ["--", url]
    try:
        completed = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise ToolError(
            "The site took too long to answer.",
            "Check your connection, or try the link again in a moment.",
            raw=str(exc),
            temporary=True,
        ) from exc
    except OSError as exc:
        raise ToolError(
            "The video engine could not be started.",
            f"Try running Viddl again. Details: {exc}",
        ) from exc

    if completed.returncode != 0 or not (completed.stdout or "").strip():
        raw = (completed.stderr or "") + "\n" + (completed.stdout or "")
        friendly = explain(raw, completed.returncode)
        raise ToolError(
            friendly.message,
            friendly.hint,
            raw=raw.strip(),
            temporary=friendly.temporary,
        )

    try:
        info = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ToolError(
            "The site sent back something Viddl could not read.",
            "This usually means the site changed. Trying again later often fixes it.",
            raw=str(exc),
        ) from exc

    if not isinstance(info, dict):
        raise ToolError("That link did not point to a single video.")
    return parse_info(info)


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
    ):
        self.engine = engine
        self.url = url
        self.option = option
        self.out_dir = Path(out_dir)
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
        """A stable, short name for this link at this quality."""
        raw = "|".join(
            (self.url, self.option.format_selector, self.option.merge_container or "")
        )
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]

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
            "--no-playlist",
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
            "20",
            "--concurrent-fragments",
            "4",
            "--progress",
            "--progress-template",
            _PROGRESS_TEMPLATE,
            "--trim-filenames",
            "150",
        )
        if self.option.merge_container and self.option.needs_ffmpeg:
            args += ["--merge-output-format", self.option.merge_container]
        args += self.engine.ffmpeg_location_args()
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

        On POSIX the child is a session of its own (``start_new_session``), so
        its group holds yt-dlp and anything yt-dlp started.
        """
        if is_windows():
            # Windows has no process groups to signal; ``taskkill /T`` walks
            # the tree, which is the same guarantee by another route.
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
                    return self._deliver(finished)
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
    def _attempt(self, args: List[str], before: Dict[Path, float]) -> Path:
        """Run the downloader once, exactly as a single-attempt run would."""
        # Each attempt speaks for itself: lines from the attempt that failed are
        # not evidence about this one, and carrying them over would both grow
        # without bound and report the wrong reason if this one fails too.
        self._lines.clear()
        if self.progress.phase == "retry":
            # The wait is over. Saying so matters: the screen decides whether to
            # move the bar from the phase, so leaving it on "retry" would keep
            # the download looking paused for as long as it took to report.
            self._emit(status="Downloading…", speed="", eta="", phase="download")

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
        if finished is None:
            raise ToolError(
                "The download finished but the file could not be found.",
                f"Look in your Downloads folder: {self.out_dir}",
            )
        return finished

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
        self._discard_work()
        self.progress = Progress(status="Done", percent=100.0, phase="done")
        if self.on_progress:
            self.on_progress(self.progress)
        return target

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
            f"Retrying in {_seconds_text(wait)} "
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
        elif line.startswith("[ExtractAudio]"):
            self._emit(status="Converting to MP3…", percent=None, phase="convert")
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
            if not _is_intermediate(path):
                return path
        if existing:
            return existing[-1]

        # Only this job's own folder is scanned. The shared downloads folder is
        # not: another window's half-finished or just-finished file is not
        # this download, and reporting it would hand the user a path that can
        # change under them (R6).
        newest: Optional[Tuple[float, Path]] = None
        try:
            for entry in self.work_dir().iterdir():
                if not entry.is_file() or entry.name.endswith(_BAD_SUFFIXES):
                    continue
                if _is_intermediate(entry):
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
