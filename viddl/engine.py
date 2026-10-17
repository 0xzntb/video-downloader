"""Finding (or fetching) the tools Viddl needs: yt-dlp and ffmpeg.

Viddl never asks the user to install anything by hand. It looks for the tools in
the places they normally live, and when one is missing it downloads a private
copy into its own folder, so nothing else on the machine is touched.
"""

from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
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
_SHA_CHUNK = 1024 * 1024

_BTBN_BASE = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest"
_BTBN_SUMS = f"{_BTBN_BASE}/checksums.sha256"
_EUGENEWARE_BASE = "https://github.com/eugeneware/ffmpeg-static/releases/latest/download"
# eugeneware publishes no checksums file, so these are pinned to the exact
# "ffmpeg 6.1.1 binaries" release its `latest` link points at — the strongest
# form of the check, and the reason to re-check them when that link moves.
# A pinned mismatch is refused outright rather than retried.
_EUGENEWARE_PINS = {
    "ffmpeg-darwin-arm64": "a90e3db6a3fd35f6074b013f948b1aa45b31c6375489d39e572bea3f18336584",
    "ffmpeg-darwin-x64": "ebdddc936f61e14049a2d4b549a412b8a40deeff6540e58a9f2a2da9e6b18894",
    "ffmpeg-linux-x64": "e7e7fb30477f717e6f55f9180a70386c62677ef8a4d4d1a5d948f4098aa3eb99",
}


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


class ChecksumMismatch(ToolError):
    """The bytes that arrived are not the bytes the source published a hash for.

    A subclass of :class:`ToolError` so every existing handler still shows it the
    same way, and separate so the caller can tell "this download was tampered
    with or broken" apart from "the network hiccuped".
    """


def is_windows() -> bool:
    return sys.platform.startswith("win")


def is_macos() -> bool:
    return sys.platform == "darwin"


# ---------------------------------------------------------------------------
# downloading
# ---------------------------------------------------------------------------


def sha256_of(path: Path) -> str:
    """The SHA-256 of a file, read in chunks so a large binary is fine."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(_SHA_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def _fetch_text(url: str, timeout: int = _TIMEOUT) -> str:
    """Fetch a small text file — in practice, a list of checksums — over HTTPS."""
    if not url.lower().startswith("https://"):
        raise ToolError(
            "Viddl only checks downloads over a secure connection.",
            "Nothing was installed.",
            raw=url,
        )
    request = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise ToolError(
            f"Could not fetch the list of checksums (error {exc.code}).",
            "Viddl will not install a file it cannot check. Try again in a moment.",
            raw=str(exc),
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ToolError(
            "Could not reach the list of checksums.",
            "Check your connection and try again — Viddl will not install "
            "a file it cannot check.",
            raw=str(exc),
        ) from exc


def digest_from_sums(text: str, filename: str) -> Optional[str]:
    """Pull one file's SHA-256 out of a published checksums list.

    Both yt-dlp and the ffmpeg builds publish the usual ``<hex>  <name>`` lines,
    sometimes with the ``*`` that marks a binary-mode entry.
    """
    wanted = Path(filename).name
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        digest, name = parts[0].lower(), parts[1].strip().lstrip("*")
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            continue
        if Path(name).name == wanted:
            return digest
    return None


def expected_sha256(source: Dict[str, object]) -> Optional[str]:
    """The hash a source promises for what it serves, if it publishes one.

    Either pinned in the source itself, or looked up in the checksums list that
    the same release publishes. ``None`` means this download cannot be checked,
    and the caller must refuse it rather than run unknown code.
    """
    pinned = source.get("sha256")
    if pinned:
        return str(pinned).lower()
    sums_url = source.get("sums_url")
    if not sums_url:
        return None
    name = str(source.get("sums_name") or str(source["url"]).rsplit("/", 1)[-1])
    digest = digest_from_sums(_fetch_text(str(sums_url)), name)
    if digest is None:
        raise ToolError(
            "The list of checksums did not mention the file being downloaded.",
            "Viddl will not install a file it cannot check. Try again in a minute.",
            raw=f"{sums_url}: no entry for {name}",
        )
    return digest


def checksum_file(path: Path) -> Path:
    """Where the hash of a copy Viddl installed is written down."""
    return path.with_name(path.name + ".sha256")


def recorded_checksum(path: Path) -> Optional[str]:
    """The hash Viddl wrote down when it installed this copy, if there is one."""
    try:
        return checksum_file(path).read_text(encoding="utf-8").strip().lower() or None
    except OSError:
        return None


def record_checksum(path: Path, digest: str) -> None:
    """Remember what was installed, so a later replacement is visible."""
    try:
        checksum_file(path).write_text(digest.strip().lower() + "\n", encoding="utf-8")
    except OSError:
        pass


def download_to(
    url: str,
    dest: Path,
    on_progress: Optional[ProgressCallback] = None,
    timeout: int = _TIMEOUT,
    expected_sha256: Optional[str] = None,
) -> Path:
    """Stream ``url`` to ``dest`` atomically, reporting progress as it goes.

    When ``expected_sha256`` is given the bytes only reach ``dest`` if they match
    it. A download that fails its check is deleted where it stands and reported,
    so an unverified program can never be picked up and run by mistake.
    """
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
        if expected_sha256 is not None:
            actual = sha256_of(tmp_path)
            if actual != expected_sha256.lower():
                tmp_path.unlink(missing_ok=True)
                raise ChecksumMismatch(
                    "The download did not match the checksum published for it.",
                    "Viddl refused to use it. If this keeps happening, the mirror "
                    "may have been rebuilt while downloading — try again later, or "
                    "install the tool yourself.",
                    raw=f"expected {expected_sha256.lower()}\n     got {actual}\n     from {url}",
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


def _verified_download(
    source: Dict[str, object],
    dest: Path,
    on_progress: Optional[ProgressCallback] = None,
) -> Path:
    """Download something Viddl will run, but only if its hash can be checked.

    Three rules, in order: the download must be encrypted, its expected hash must
    be published somewhere (pinned in the source, or in the checksums list the
    same release ships), and the bytes that arrive must match it. A source that
    fails any rule is refused — Viddl does not run a downloaded program it could
    not check.
    """
    url = str(source["url"])
    if not url.lower().startswith("https://"):
        raise ToolError(
            "Viddl only installs its helpers over a secure connection.",
            "Nothing was installed.",
            raw=url,
        )

    pinned = source.get("sha256")
    if pinned:
        # A pinned hash is exact: a mismatch means the wrong bytes, full stop.
        return download_to(url, dest, on_progress, expected_sha256=str(pinned))

    # With a checksums list there is one innocent way to fail: the release is
    # rebuilt while the (large) file is downloading, so the list describes the
    # new build and we hold the old one. Re-read the list once and try again.
    attempts = 0
    while True:
        expected = expected_sha256(source)
        if expected is None:
            raise ToolError(
                "That download publishes no checksum, so Viddl did not install it.",
                "Viddl will not run a downloaded program it cannot check. "
                "Installing it with your system's package manager works just as well.",
                raw=url,
            )
        try:
            return download_to(url, dest, on_progress, expected_sha256=expected)
        except ChecksumMismatch:
            attempts += 1
            if attempts >= 2:
                raise


def _make_runnable(path: Path) -> None:
    """Mark a downloaded helper as runnable by its owner, and nobody else.

    Owner-only on purpose: these are Viddl's own copies, living in a folder only
    their owner can enter, so no other account has any business running them.
    Applying the whole mode rather than only the execute bits is what makes that
    true — and it means a folder whose permissions are later reset (a restore, a
    copied home directory) does not turn two programs world-executable again.
    Windows is skipped: ``chmod`` there only sets the read-only flag, so the bits
    would mean nothing.
    """
    if is_windows():
        return
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def _extract_member(archive: Path, pattern: str, dest: Path, mode: Optional[int] = 0o700) -> None:
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
            os.chmod(dest, mode)
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
        source: Dict[str, object] = {
            "url": f"{_YTDLP_BASE}/{asset}",
            # yt-dlp publishes SHA2-256SUMS beside every release, so the hash is
            # looked up rather than pinned: an updated engine keeps working.
            "sums_url": f"{_YTDLP_BASE}/SHA2-256SUMS",
            "sums_name": asset,
        }
        dest = self.ytdlp_path
        if on_status:
            on_status("Downloading the video engine (about 3 MB)…")
        _verified_download(source, dest, on_progress)
        _make_runnable(dest)

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
        record_checksum(dest, sha256_of(dest))
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
        """Download locations for a static ffmpeg build, most preferred first.

        Every entry carries the means to check what it serves: either the
        checksums list published beside the download, or a hash pinned here. A
        mirror that publishes neither is not listed at all — an extra address is
        not worth installing a build that cannot be checked. (johnvansickle.com
        was dropped for exactly that reason: it publishes only an MD5.)
        """
        machine = platform.machine().lower()
        if is_windows():
            return [
                {
                    "url": f"{_BTBN_BASE}/ffmpeg-master-latest-win64-gpl.zip",
                    "archive": "zip",
                    "pattern": r"bin/ffmpeg\.exe$",
                    "sums_url": _BTBN_SUMS,
                }
            ]
        if is_macos():
            arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
            return [
                {
                    "url": f"{_EUGENEWARE_BASE}/ffmpeg-darwin-{arch}",
                    "archive": None,
                    "pattern": None,
                    "sha256": _EUGENEWARE_PINS[f"ffmpeg-darwin-{arch}"],
                },
                {
                    "url": f"{_EUGENEWARE_BASE}/ffmpeg-darwin-x64",
                    "archive": None,
                    "pattern": None,
                    "sha256": _EUGENEWARE_PINS["ffmpeg-darwin-x64"],
                },
            ]
        return [
            {
                "url": f"{_BTBN_BASE}/ffmpeg-master-latest-linux64-gpl.tar.xz",
                "archive": "tar.xz",
                "pattern": r"bin/ffmpeg$",
                "sums_url": _BTBN_SUMS,
            },
            {
                "url": f"{_EUGENEWARE_BASE}/ffmpeg-linux-x64",
                "archive": None,
                "pattern": None,
                "sha256": _EUGENEWARE_PINS["ffmpeg-linux-x64"],
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
                    _verified_download(source, dest, on_progress)
                else:
                    with tempfile.TemporaryDirectory(prefix="viddl-ffmpeg-") as tmp:
                        archive = Path(tmp) / f"ffmpeg-download.{source['archive'].replace('.', '-')}"
                        # The checksums list covers the bundle, not the program
                        # inside it: check the archive, then unpack it.
                        _verified_download(source, archive, on_progress)
                        _extract_member(archive, source["pattern"], dest)
                _make_runnable(dest)
                ok, output = _run_probe([str(dest)], "-version")
                if ok:
                    version = ""
                    match = re.search(r"ffmpeg version (\S+)", output)
                    if match:
                        version = match.group(1)
                    record_checksum(dest, sha256_of(dest))
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
                lines.append(
                    f"{label + ':':<14}{version} ({tool.origin_text}){self._checksum_note(tool)}"
                )
        return lines

    def _checksum_note(self, tool: Tool) -> str:
        """Whether the copy we installed is still the copy we installed.

        Re-hashing our own copies is cheap next to what downloading them cost,
        and it is the only way a later swap of the binary would ever be noticed.
        """
        if tool.origin != "viddl" or tool.path is None:
            return ""
        recorded = recorded_checksum(tool.path)
        if recorded is None:
            return ""
        try:
            current = sha256_of(tool.path)
        except OSError:
            return ""
        if current != recorded:
            return f", sha256 {recorded[:12]}… (changed since it was installed)"
        return f", sha256 {recorded[:12]}… (matches what was installed)"


def last_error_hint(raw: str) -> str:
    """Convenience wrapper so callers do not need to import errors separately."""
    return str(explain(raw))
