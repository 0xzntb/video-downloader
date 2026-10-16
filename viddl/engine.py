"""Finding (or fetching) the tools Viddl needs: yt-dlp and ffmpeg.

Viddl never asks the user to install anything by hand. It looks for the tools in
the places they normally live, and when one is missing it downloads a private
copy into its own folder, so nothing else on the machine is touched.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import threading
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import __version__
from . import appdata
from .errors import explain

StatusCallback = Callable[[str], None]
ProgressCallback = Callable[[int, Optional[int]], None]

_UA = f"Viddl/{__version__} (+https://github.com/freebuff/viddl)"
_YTDLP_BASE = "https://github.com/yt-dlp/yt-dlp/releases/latest/download"
_TIMEOUT = 30
_PROBE_TIMEOUT = 25


@dataclass
class Tool:
    """A runnable external program."""

    name: str
    prefix: List[str]  # full command prefix, e.g. ["/path/ffmpeg"] or [python, "-m", "yt_dlp"]
    origin: str  # "system" | "viddl" | "python"
    version: str = ""
    path: Optional[Path] = None
    extra: Dict[str, str] = field(default_factory=dict)

    def command(self, *args: str) -> List[str]:
        return [*self.prefix, *args]

    @property
    def origin_text(self) -> str:
        return {
            "system": "found on your computer",
            "viddl": "downloaded by Viddl",
            "python": "installed as a Python package",
        }.get(self.origin, self.origin)


class ToolError(RuntimeError):
    """Raised when a tool is missing or could not be installed."""

    def __init__(self, message: str, hint: Optional[str] = None, raw: str = ""):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.raw = raw

    @property
    def friendly(self) -> str:
        if self.hint:
            return f"{self.message}\n{self.hint}"
        return self.message


def is_windows() -> bool:
    return sys.platform.startswith("win")


def is_macos() -> bool:
    return sys.platform == "darwin"


# ---------------------------------------------------------------------------
# downloading
# ---------------------------------------------------------------------------


def download_to(
    url: str,
    dest: Path,
    on_progress: Optional[ProgressCallback] = None,
    timeout: int = _TIMEOUT,
) -> Path:
    """Stream ``url`` to ``dest`` atomically, reporting progress as it goes."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": _UA})
    tmp_path = dest.with_name(dest.name + ".part")

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            total = response.headers.get("Content-Length")
            total_bytes = int(total) if total and total.isdigit() else None
            done = 0
            with open(tmp_path, "wb") as handle:
                while True:
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    done += len(chunk)
                    if on_progress:
                        on_progress(done, total_bytes)
        if tmp_path.stat().st_size == 0:
            raise ToolError(
                "The download came back empty.",
                "Check your internet connection and try again.",
            )
        tmp_path.replace(dest)
    except ToolError:
        tmp_path.unlink(missing_ok=True)
        raise
    except urllib.error.HTTPError as exc:
        tmp_path.unlink(missing_ok=True)
        raise ToolError(
            f"Could not download from the internet (error {exc.code}).",
            "If you are behind a company or school network, it may be blocking the download.",
            raw=str(exc),
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        tmp_path.unlink(missing_ok=True)
        raise ToolError(
            "Could not reach the internet.",
            "Check your connection and try again.",
            raw=str(exc),
        ) from exc
    return dest


def _extract_member(archive: Path, pattern: str, dest: Path, mode: Optional[int] = 0o755) -> None:
    """Pull one file out of a ``.zip`` or ``.tar.xz`` without unpacking the rest."""
    name = archive.name.lower()
    dest.parent.mkdir(parents=True, exist_ok=True)
    matcher = re.compile(pattern)

    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                if info.is_dir() or not matcher.search(info.filename):
                    continue
                with zf.open(info) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
                break
            else:
                raise ToolError("The downloaded bundle did not contain the program.")
    else:
        with tarfile.open(archive, "r:*") as tf:
            for member in tf.getmembers():
                if not member.isfile() or not matcher.search(member.name):
                    continue
                src = tf.extractfile(member)
                if src is None:
                    continue
                with src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
                break
            else:
                raise ToolError("The downloaded bundle did not contain the program.")

    if mode is not None:
        try:
            os.chmod(dest, dest.stat().st_mode | mode)
        except OSError:
            pass


def _run_probe(prefix: List[str], *args: str, timeout: int = _PROBE_TIMEOUT):
    """Run a program just to see whether it works. Returns (ok, output)."""
    try:
        completed = subprocess.run(
            [*prefix, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    output = (completed.stdout or "").strip() or (completed.stderr or "").strip()
    return completed.returncode == 0, output


# ---------------------------------------------------------------------------
# engine
# ---------------------------------------------------------------------------


class Engine:
    """Locates (and if necessary installs) yt-dlp and ffmpeg."""

    def __init__(self, home: Optional[Path] = None):
        self.home = Path(home) if home else appdata.data_dir()
        self._ytdlp: Optional[Tool] = None
        self._ffmpeg: Optional[Tool] = None
        # A background warm-up and a first download can race for the same file.
        self._lock = threading.Lock()

    @property
    def bin_dir(self) -> Path:
        return self.home / "bin"

    # -- yt-dlp -------------------------------------------------------------
    @property
    def ytdlp_path(self) -> Path:
        return self.bin_dir / ("yt-dlp.exe" if is_windows() else "yt-dlp")

    def find_ytdlp(self) -> Optional[Tool]:
        """Look for yt-dlp without downloading anything."""
        override = os.environ.get("VIDDL_YTDLP")
        candidates: List[tuple] = []

        if override:
            candidates.append(("viddl", [override]))
        candidates.append(("viddl", [str(self.ytdlp_path)]))
        # A pip-installed yt_dlp module works just as well and needs no download.
        try:
            import importlib.util

            if importlib.util.find_spec("yt_dlp") is not None:
                candidates.append(("python", [sys.executable, "-m", "yt_dlp"]))
        except (ImportError, ValueError):
            pass
        found = appdata.which("yt-dlp")
        if found:
            candidates.append(("system", [str(found)]))

        for origin, prefix in candidates:
            if origin != "python" and not Path(prefix[0]).exists():
                continue
            ok, output = _run_probe(prefix, "--version")
            if ok and output:
                path = Path(prefix[0]) if origin != "python" else None
                return Tool("yt-dlp", prefix, origin, version=output.splitlines()[0], path=path)
        return None

    def install_ytdlp(
        self,
        on_status: Optional[StatusCallback] = None,
        on_progress: Optional[ProgressCallback] = None,
    ) -> Tool:
        asset = "yt-dlp.exe" if is_windows() else "yt-dlp"
        url = f"{_YTDLP_BASE}/{asset}"
        dest = self.ytdlp_path
        if on_status:
            on_status("Downloading the video engine (about 3 MB)…")
        download_to(url, dest, on_progress)
        try:
            os.chmod(dest, dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        except OSError:
            pass

        ok, output = _run_probe([str(dest)], "--version")
        origin = "viddl"
        prefix = [str(dest)]
        if not ok:
            # The standalone build is a Python zipapp; if its shebang is not
            # usable on this machine we can still run it with our own Python.
            ok, output = _run_probe([sys.executable, str(dest)], "--version")
            prefix = [sys.executable, str(dest)]
        if not ok:
            raise ToolError(
                "The downloaded video engine would not start.",
                "Delete the folder below and try again:\n"
                f"    {self.bin_dir}",
                raw=output,
            )
        tool = Tool("yt-dlp", prefix, origin, version=output.splitlines()[0], path=dest)
        self._ytdlp = tool
        return tool

    def ensure_ytdlp(
        self,
        on_status: Optional[StatusCallback] = None,
        on_progress: Optional[ProgressCallback] = None,
        allow_download: bool = True,
    ) -> Tool:
        if self._ytdlp is not None:
            return self._ytdlp
        with self._lock:
            if self._ytdlp is not None:  # another thread finished first
                return self._ytdlp
            tool = self.find_ytdlp()
            if tool is None:
                if not allow_download:
                    raise ToolError(
                        "The video engine is not installed yet.",
                        "Run Viddl again with a working internet connection to set it up.",
                    )
                tool = self.install_ytdlp(on_status, on_progress)
            else:
                if on_status:
                    on_status(f"Using the video engine {tool.origin_text}.")
            self._ytdlp = tool
            return tool

    # -- ffmpeg -------------------------------------------------------------
    def _ffmpeg_filename(self) -> str:
        return "ffmpeg.exe" if is_windows() else "ffmpeg"

    @property
    def ffmpeg_path(self) -> Path:
        return self.bin_dir / self._ffmpeg_filename()

    def find_ffmpeg(self) -> Optional[Tool]:
        """Look for ffmpeg on the system or in Viddl's own folder."""
        override = os.environ.get("VIDDL_FFMPEG")
        candidates: List[tuple] = []
        if override:
            candidates.append(("viddl", Path(override)))
        candidates.append(("viddl", self.ffmpeg_path))
        found = appdata.which("ffmpeg")
        if found:
            candidates.append(("system", found))

        for origin, path in candidates:
            if not Path(path).exists():
                continue
            ok, output = _run_probe([str(path)], "-version")
            if ok:
                version = ""
                match = re.search(r"ffmpeg version (\S+)", output)
                if match:
                    version = match.group(1)
                return Tool("ffmpeg", [str(path)], origin, version=version, path=Path(path))
        return None

    def ffmpeg_sources(self) -> List[dict]:
        """Download locations for a static ffmpeg build, most preferred first."""
        machine = platform.machine().lower()
        if is_windows():
            return [
                {
                    "url": "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
                    "ffmpeg-master-latest-win64-gpl.zip",
                    "archive": "zip",
                    "pattern": r"bin/ffmpeg\.exe$",
                }
            ]
        if is_macos():
            arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
            return [
                {
                    "url": "https://github.com/eugeneware/ffmpeg-static/releases/latest/download/"
                    f"ffmpeg-darwin-{arch}",
                    "archive": None,
                    "pattern": None,
                },
                {
                    "url": "https://github.com/eugeneware/ffmpeg-static/releases/latest/download/"
                    "ffmpeg-darwin-x64",
                    "archive": None,
                    "pattern": None,
                },
            ]
        return [
            {
                "url": "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
                "ffmpeg-master-latest-linux64-gpl.tar.xz",
                "archive": "tar.xz",
                "pattern": r"bin/ffmpeg$",
            },
            {
                "url": "https://johnvansickle.com/ffmpeg/releases/"
                "ffmpeg-release-amd64-static.tar.xz",
                "archive": "tar.xz",
                "pattern": r"/ffmpeg$",
            },
            {
                "url": "https://github.com/eugeneware/ffmpeg-static/releases/latest/download/"
                "ffmpeg-linux-x64",
                "archive": None,
                "pattern": None,
            },
        ]

    def install_ffmpeg(
        self,
        on_status: Optional[StatusCallback] = None,
        on_progress: Optional[ProgressCallback] = None,
    ) -> Tool:
        dest = self.ffmpeg_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        errors: List[str] = []

        for index, source in enumerate(self.ffmpeg_sources()):
            if on_status:
                label = "Downloading ffmpeg (about 40–80 MB, one time)…"
                if index:
                    label = f"Trying another download mirror for ffmpeg ({index + 1})…"
                on_status(label)
            archive: Optional[Path] = None
            try:
                if source["archive"] is None:
                    download_to(source["url"], dest, on_progress)
                else:
                    with tempfile.TemporaryDirectory(prefix="viddl-ffmpeg-") as tmp:
                        archive = Path(tmp) / f"ffmpeg-download.{source['archive'].replace('.', '-')}"
                        download_to(source["url"], archive, on_progress)
                        _extract_member(archive, source["pattern"], dest)
                try:
                    os.chmod(dest, dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                except OSError:
                    pass
                ok, output = _run_probe([str(dest)], "-version")
                if ok:
                    version = ""
                    match = re.search(r"ffmpeg version (\S+)", output)
                    if match:
                        version = match.group(1)
                    tool = Tool("ffmpeg", [str(dest)], "viddl", version=version, path=dest)
                    self._ffmpeg = tool
                    return tool
                errors.append(f"{source['url']}: program did not run")
            except ToolError as exc:
                errors.append(f"{source['url']}: {exc.message}")
            except (OSError, tarfile.TarError, zipfile.BadZipFile, EOFError) as exc:
                # Mirrors sometimes answer with an HTML error page instead of an
                # archive; that must move on to the next mirror, not crash.
                errors.append(f"{source['url']}: {exc}")
            finally:
                if archive is not None:
                    archive.unlink(missing_ok=True)

        raise ToolError(
            "ffmpeg could not be downloaded automatically.",
            "You can install it the usual way for your system "
            "(for example “sudo apt install ffmpeg”, “brew install ffmpeg”, "
            "or winget install Gyan.FFmpeg) and Viddl will find it.",
            raw="\n".join(errors),
        )

    def ensure_ffmpeg(
        self,
        on_status: Optional[StatusCallback] = None,
        on_progress: Optional[ProgressCallback] = None,
        allow_download: bool = True,
    ) -> Tool:
        if self._ffmpeg is not None:
            return self._ffmpeg
        with self._lock:
            if self._ffmpeg is not None:
                return self._ffmpeg
            tool = self.find_ffmpeg()
            if tool is None:
                if not allow_download:
                    raise ToolError(
                        "ffmpeg is needed for that option and is not installed.",
                        "Viddl can download it for you: choose “Set up ffmpeg”.",
                    )
                tool = self.install_ffmpeg(on_status, on_progress)
            self._ffmpeg = tool
            return tool

    def ffmpeg_location_args(self) -> List[str]:
        """Tell yt-dlp where our private ffmpeg lives, if it is not on PATH."""
        tool = self.find_ffmpeg()
        if tool is None or tool.path is None or tool.origin == "system":
            return []
        return ["--ffmpeg-location", str(tool.path.parent)]

    def report(self) -> List[str]:
        """Human-readable status used by ``viddl --doctor`` and the UI."""
        lines: List[str] = [f"Viddl {__version__}"]
        lines.append(f"App folder:   {self.home}")
        lines.append(f"Log file:     {self.home / 'viddl.log'}")
        try:
            lines.append(f"Downloads:    {appdata.downloads_dir()}")
        except OSError:
            lines.append("Downloads:    (unknown)")
        for label, tool in (("yt-dlp", self.find_ytdlp()), ("ffmpeg", self.find_ffmpeg())):
            if tool is None:
                lines.append(f"{label + ':':<14}not installed")
            else:
                version = f" {tool.version}" if tool.version else ""
                lines.append(f"{label + ':':<14}{version} ({tool.origin_text})")
        return lines


def last_error_hint(raw: str) -> str:
    """Convenience wrapper so callers do not need to import errors separately."""
    return str(explain(raw))
