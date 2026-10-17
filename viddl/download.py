"""Reading a link's qualities, and running the actual download."""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .engine import Engine, ToolError
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
        # "--" ends the options: the link can never be read as one.
        "--",
        url,
    )
    try:
        completed = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise ToolError(
            "The site took too long to answer.",
            "Check your connection, or try the link again in a moment.",
            raw=str(exc),
        ) from exc
    except OSError as exc:
        raise ToolError(
            "The video engine could not be started.",
            f"Try running Viddl again. Details: {exc}",
        ) from exc

    if completed.returncode != 0 or not (completed.stdout or "").strip():
        raw = (completed.stderr or "") + "\n" + (completed.stdout or "")
        friendly = explain(raw, completed.returncode)
        raise ToolError(friendly.message, friendly.hint, raw=raw.strip())

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

        self._process: Optional[subprocess.Popen] = None
        self._cancelled = threading.Event()
        self._lines: List[str] = []
        self._paths: List[Path] = []
        self.progress = Progress()
        self.started_at = time.time()

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
            "-P",
            str(self.out_dir),
            "-o",
            _OUTPUT_TEMPLATE,
            "-f",
            self.option.format_selector,
        ]
        args += list(self.option.extra_args)
        # "--" ends the options, so the link is always read as a link even if
        # it starts with a dash. Without this, a page handing over
        # "--config-locations=./x.conf" would have it treated as a flag.
        args.append("--")
        args.append(self.url)
        return args

    # -- lifecycle ----------------------------------------------------------
    def cancel(self) -> None:
        self._cancelled.set()
        process = self._process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

    def run(self) -> Path:
        """Download and return the finished file. Raises on failure."""
        self.out_dir.mkdir(parents=True, exist_ok=True)
        before = self._snapshot()
        args = self.build_args()
        if self.on_log:
            self.on_log("$ " + " ".join(args))

        try:
            self._process = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                errors="replace",
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
        self._process.wait()
        code = self._process.returncode

        if self._cancelled.is_set():
            if not self.keep_partial:
                self._cleanup_partial()
            raise DownloadCancelled()

        if code != 0:
            raw = "\n".join(self._lines[-60:])
            friendly = explain(raw, code)
            raise ToolError(friendly.message, friendly.hint, raw=raw)

        finished = self._find_output(before)
        if finished is None:
            raise ToolError(
                "The download finished but the file could not be found.",
                f"Look in your Downloads folder: {self.out_dir}",
            )
        self.progress = Progress(status="Done", percent=100.0, phase="done")
        if self.on_progress:
            self.on_progress(self.progress)
        return finished

    # -- internals ----------------------------------------------------------
    def _snapshot(self) -> Dict[Path, float]:
        snapshot: Dict[Path, float] = {}
        try:
            for entry in self.out_dir.iterdir():
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
        """Delete the half-finished files; returns how many are gone."""
        removed = 0
        for path in self.partial_paths():
            try:
                path.unlink()
                removed += 1
            except OSError:
                continue
        return removed

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

        newest: Optional[Tuple[float, Path]] = None
        try:
            for entry in self.out_dir.iterdir():
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
