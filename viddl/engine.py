"""Finding (or fetching) the tools Viddl needs: yt-dlp and ffmpeg.

Viddl never asks the user to install anything by hand. It looks for the tools in
the places they normally live, and when one is missing it downloads a private
copy into its own folder, so nothing else on the machine is touched.
"""

from __future__ import annotations

import hashlib
import http.client
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import __version__
from . import appdata
from .errors import explain
from .util import human_size

StatusCallback = Callable[[str], None]
ProgressCallback = Callable[[int, Optional[int]], None]

_UA = f"Viddl/{__version__} (+https://github.com/freebuff/viddl)"
_YTDLP_BASE = "https://github.com/yt-dlp/yt-dlp/releases/latest/download"
_TIMEOUT = 30
_PROBE_TIMEOUT = 25
_SHA_CHUNK = 1024 * 1024

#: How long to wait before trying a dropped transfer again; it grows with each
#: attempt, so a network that is down for a moment is not hammered.
_RETRY_PAUSE_SECONDS = 1.5

#: How old yt-dlp may get before Viddl mentions it. Releases come most weeks, and
#: a site that stopped working is usually fixed by the next one — so a month is
#: the point at which "try updating" is the better first guess than "the site
#: changed".
STALE_DAYS = 30

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
    origin: str  # "system" | "viddl" | "python" | "environment"
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
            "environment": "the copy you pointed Viddl at",
        }.get(self.origin, self.origin)


class ToolError(RuntimeError):
    """Raised when a tool is missing or could not be installed.

    ``temporary`` says whether the failure is the kind another attempt may fix.
    A job that has already tried, and still failed, reports it here so the
    caller can tell "the site was busy" apart from "that video is private"
    without reading the raw output again.
    """

    def __init__(
        self,
        message: str,
        hint: Optional[str] = None,
        raw: str = "",
        temporary: bool = False,
    ):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.raw = raw
        self.temporary = temporary

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
# how old is the engine
# ---------------------------------------------------------------------------


def version_date(version: str) -> Optional[date]:
    """The release date a yt-dlp version names, when it names one.

    yt-dlp numbers releases by date — ``2026.08.19``, sometimes
    ``2026.08.19.1`` — so the version is the age. A nightly, a git checkout or a
    pip ``dev`` build names no date, and gets ``None``: Viddl then says nothing
    about its age rather than guessing.
    """
    match = re.match(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})(?:\.|$)", (version or "").strip())
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def engine_age_days(version: str, today: Optional[date] = None) -> Optional[int]:
    """How many days ago this version was released, or ``None`` if unknown."""
    released = version_date(version)
    if released is None:
        return None
    days = ((today or date.today()) - released).days
    # A version dated in the future means the clock is wrong, not that the
    # engine is fresh; saying nothing is the honest answer to both.
    return days if days >= 0 else None


def staleness_note(version: str, today: Optional[date] = None) -> str:
    """A sentence for ``--doctor`` when the engine is old enough to be a suspect."""
    days = engine_age_days(version, today)
    if days is None or days < STALE_DAYS:
        return ""
    return (
        f"{days} days old — run `viddl --update` if a site stopped working"
    )


def version_key(version: str) -> tuple:
    """Order versions the way yt-dlp numbers them: date first, then any suffix.

    A version that carries no number at all (``nightly``, ``dev``) sorts below
    every release, which is deliberate: an undated build never displaces a dated
    one, because nothing here can tell which of them is newer.
    """
    return tuple(int(part) for part in re.findall(r"\d+", version or "")[:4])


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


class _StalePartial(Exception):
    """The server will not continue from the half-file Viddl already has."""


def _size_of(headers) -> Optional[int]:
    """The length a response says it will send, if it says."""
    total = headers.get("Content-Length")
    return int(total) if total and total.isdigit() else None


def download_size(url: str, timeout: int = 10) -> Optional[int]:
    """How many bytes ``url`` would send, without downloading any of them.

    A ``HEAD`` request, so the answer costs a round trip rather than 150 MB.
    ``None`` is an honest answer too: a mirror that will not say how big it is,
    or a network that will not answer, means the prompt says nothing rather
    than a made-up number.
    """
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return _size_of(response.headers)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None


#: Answers already paid for: the qualities screen asks on every arrow key, and
#: GitHub does not need to be asked twice.
_SIZE_HINTS: Dict[str, Optional[int]] = {}
_SIZE_LOCK = threading.Lock()


def _size_hint(url: str, timeout: int = 10) -> Optional[int]:
    """``download_size``, remembered — including the times it could not tell."""
    with _SIZE_LOCK:
        if url in _SIZE_HINTS:
            return _SIZE_HINTS[url]
    size = download_size(url, timeout=timeout)
    with _SIZE_LOCK:
        _SIZE_HINTS[url] = size
    return size


@lru_cache(maxsize=1)
def _rosetta_available() -> bool:
    """Whether this Mac can run an Intel build (Rosetta 2 is installed).

    ``arch -x86_64`` is how Apple's own tools ask, and a Mac without Rosetta
    answers with an error. Only ever called for the Intel fallback on Apple
    silicon: nothing else needs translating.
    """
    try:
        completed = subprocess.run(
            ["arch", "-x86_64", "/usr/bin/true"],
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _fetch(url: str, tmp_path: Path, on_progress, timeout: int) -> None:
    """Stream ``url`` into ``tmp_path``, continuing a partial file if there is one.

    The ``Range`` request is what makes a 150 MB download survivable on a bad
    connection: what arrived last time is kept, and only the rest is asked for.
    A server that ignores the range (or one whose file has changed under it)
    cannot be continued from, which is what :class:`_StalePartial` means — the
    caller throws the part away and starts again rather than gluing two
    different files together.
    """
    already = tmp_path.stat().st_size if tmp_path.exists() else 0
    headers = {"User-Agent": _UA}
    if already:
        headers["Range"] = f"bytes={already}-"
    request = urllib.request.Request(url, headers=headers)

    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and already:
            # The half-file is at least as long as the server's: either it is
            # already whole (the checksum will say) or the build was replaced.
            raise _StalePartial() from exc
        raise

    with response:
        continued = getattr(response, "status", 200) == 206
        done = already if continued else 0
        length = _size_of(response.headers)
        total = done + length if (continued and length is not None) else length
        if on_progress:
            # Say the total up front: a bar that only learns its size once the
            # first chunk lands looks stuck when the connection is slow.
            on_progress(done, total)
        with open(tmp_path, "ab" if continued else "wb") as handle:
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                done += len(chunk)
                if on_progress:
                    on_progress(done, total)

    if total is not None and done != total:
        # A connection that stops early ends the body without an error, so the
        # short file is noticed here rather than renamed into place and run.
        raise http.client.IncompleteRead(b"", total - done)


def download_to(
    url: str,
    dest: Path,
    on_progress: Optional[ProgressCallback] = None,
    timeout: int = _TIMEOUT,
    expected_sha256: Optional[str] = None,
    part_path: Optional[Path] = None,
) -> Path:
    """Stream ``url`` to ``dest`` atomically, reporting progress as it goes.

    When ``expected_sha256`` is given the bytes only reach ``dest`` if they match
    it. A download that fails its check is deleted where it stands and reported,
    so an unverified program can never be picked up and run by mistake.

    A transfer that stops early keeps its ``.part`` file. That is the whole
    point of the range request: the next attempt fetches only the rest instead
    of starting a 150 MB download over — and three attempts are allowed, so a
    connection that drops twice still finishes.

    ``part_path`` names that half-file. The default sits beside ``dest``, which
    is right for a URL downloaded to one place; a caller that may try several
    URLs for the same destination passes a name of its own, so bytes from one
    mirror are never continued onto another's.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = part_path or dest.with_name(dest.name + ".part")
    #: Whether this failure leaves something worth continuing from. A bad
    #: transfer does; a checksum mismatch or an unreadable folder does not.
    keep_part = False

    try:
        for attempt in range(1, 4):
            try:
                _fetch(url, tmp_path, on_progress, timeout)
                break
            except _StalePartial as exc:
                # Not resumable: the bytes we hold cannot be joined to this
                # download. Start again — once, twice at most.
                tmp_path.unlink(missing_ok=True)
                if attempt >= 3:
                    raise ToolError(
                        "The download could not be continued.",
                        "The copy on the server has changed since it started. Try again later.",
                        raw=str(exc),
                    ) from exc
            except urllib.error.HTTPError as exc:
                tmp_path.unlink(missing_ok=True)
                raise ToolError(
                    f"Could not download from the internet (error {exc.code}).",
                    "If you are behind a company or school network, it may be "
                    "blocking the download.",
                    raw=str(exc),
                ) from exc
            except (
                urllib.error.URLError,
                http.client.HTTPException,  # a body that stops early
                TimeoutError,
                OSError,
            ) as exc:
                # The bytes already fetched stay in the ``.part`` file: the next
                # attempt asks for the rest with a Range request, which is what
                # makes a dropped 150 MB transfer a pause rather than a restart.
                if attempt >= 3:
                    # The part is the point of the next attempt, so this one
                    # failure must not clean it up on the way out.
                    keep_part = True
                    raise ToolError(
                        "Could not reach the internet.",
                        "Check your connection and try again — what has been "
                        "downloaded so far is kept, so it carries on from there.",
                        raw=str(exc),
                    ) from exc
                time.sleep(attempt * _RETRY_PAUSE_SECONDS)
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
        # The checksum and empty-file cases delete their own part, and a
        # half-download that a later attempt can resume is kept on purpose.
        if not keep_part:
            tmp_path.unlink(missing_ok=True)
        raise
    return dest


def _already_there(dest: Path, expected: str) -> bool:
    """Whether ``dest`` is already the checked file this source publishes.

    A crash between the download finishing and the archive being unpacked leaves
    a complete 150 MB bundle in the app folder. Re-reading it costs a second of
    hashing and saves the whole download.
    """
    try:
        if not dest.is_file():
            return False
        return sha256_of(dest) == expected.lower()
    except OSError:
        return False


def _verified_download(
    source: Dict[str, object],
    dest: Path,
    on_progress: Optional[ProgressCallback] = None,
    part_path: Optional[Path] = None,
    reuse_existing: bool = False,
) -> Path:
    """Download something Viddl will run, but only if its hash can be checked.

    Three rules, in order: the download must be encrypted, its expected hash must
    be published somewhere (pinned in the source, or in the checksums list the
    same release ships), and the bytes that arrive must match it. A source that
    fails any rule is refused — Viddl does not run a downloaded program it could
    not check.

    ``reuse_existing`` is for a destination that an interrupted run may have
    finished: an archive that already hashes correctly is not fetched again.
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
        expected = str(pinned)
        if reuse_existing and _already_there(dest, expected):
            return dest
        return download_to(url, dest, on_progress, expected_sha256=expected, part_path=part_path)

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
        if reuse_existing and _already_there(dest, expected):
            return dest
        try:
            return download_to(url, dest, on_progress, expected_sha256=expected, part_path=part_path)
        except ChecksumMismatch:
            attempts += 1
            if attempts >= 2:
                raise


def _mirror_tag(url: str) -> str:
    """A short, stable name for one download that a part-file can be filed under.

    Two mirrors of the same program are two different files, so the half of one
    must never be continued onto the other. A hash of the URL says which is
    which, and stays the same between runs, which is what lets an interrupted
    download be found again.
    """
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:8]


def _forget_download_leftovers(folder: Path) -> None:
    """Remove archives and part-files an earlier, failed attempt left behind."""
    for leftover in list(folder.glob("ffmpeg-download-*")) + list(folder.glob("ffmpeg*.part")):
        try:
            leftover.unlink()
        except OSError:
            pass


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
        """The newest yt-dlp that works, without downloading anything.

        The order used to be fixed, with Viddl's own copy first — so the copy it
        fetched once kept shadowing a newer one the user installed later, and the
        README's promise ("if you already have it installed, Viddl uses yours")
        stopped being true the day Viddl fetched its own. Every copy that runs is
        now asked its version and the newest wins (R4), with one exception: a
        copy named by ``VIDDL_YTDLP`` wins outright, because pointing at one is
        an instruction rather than a default.
        """
        override = appdata.program_override(appdata.ENV_YTDLP)
        candidates: List[tuple] = []

        if override is not None:
            # Its own origin: a copy someone else chose, which is not the same
            # thing as the copy Viddl downloaded, and must not be reported as it.
            candidates.append(("environment", [str(override)]))
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

        working: List[Tool] = []
        for origin, prefix in candidates:
            if origin != "python" and not Path(prefix[0]).exists():
                continue
            ok, output = _run_probe(prefix, "--version")
            if ok and output:
                path = Path(prefix[0]) if origin != "python" else None
                working.append(
                    Tool("yt-dlp", prefix, origin, version=output.splitlines()[0], path=path)
                )
                if origin == "environment":
                    break
        if not working:
            return None
        # ``max`` keeps the first of equals, and Viddl's own copy is first among
        # the defaults: a tie goes to the copy it manages.
        return max(working, key=lambda tool: version_key(tool.version))

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

    def installed_ytdlp(self) -> Optional[Tool]:
        """The copy in Viddl's own folder — the one an update replaces.

        Not ``find_ytdlp()``: that answers "which copy would run", and when a
        newer one elsewhere wins the answer is not the thing being replaced. An
        update that reported it would say "already the newest release" about a
        copy it had just moved forward by three years.
        """
        path = self.ytdlp_path
        if not path.exists():
            return None
        ok, output = _run_probe([str(path)], "--version")
        prefix = [str(path)]
        if not ok:
            # The same fallback the installer uses for a zipapp without a usable
            # shebang on this machine.
            ok, output = _run_probe([sys.executable, str(path)], "--version")
            prefix = [sys.executable, str(path)]
        if not ok or not output:
            return None
        return Tool("yt-dlp", prefix, "viddl", version=output.splitlines()[0], path=path)

    def update_ytdlp(
        self,
        on_status: Optional[StatusCallback] = None,
        on_progress: Optional[ProgressCallback] = None,
    ) -> Tuple[Optional[Tool], Tool]:
        """Fetch this release again, whatever is installed now.

        Returns what was there before and what is there now, so the caller can
        say what actually changed instead of "done". Downloads and checks are
        ``install_ytdlp``'s job: an update is an install that starts from a
        known state, not a second code path that could skip the checks.
        """
        before = self.installed_ytdlp()
        with self._lock:
            self._ytdlp = None
            tool = self.install_ytdlp(on_status, on_progress)
        return before, tool

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
        override = appdata.program_override(appdata.ENV_FFMPEG)
        candidates: List[tuple] = []
        if override is not None:
            candidates.append(("environment", override))
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
            arm = machine in ("arm64", "aarch64")
            native = "arm64" if arm else "x64"
            sources = [
                {
                    "url": f"{_EUGENEWARE_BASE}/ffmpeg-darwin-{native}",
                    "archive": None,
                    "pattern": None,
                    "sha256": _EUGENEWARE_PINS[f"ffmpeg-darwin-{native}"],
                }
            ]
            if arm:
                # On Apple silicon the only other build is an Intel one, which
                # needs Rosetta 2 to run. Marked rather than dropped: whether it
                # can work is a question for install time, and the answer is
                # worth saying out loud (see ``install_ffmpeg``).
                sources.append(
                    {
                        "url": f"{_EUGENEWARE_BASE}/ffmpeg-darwin-x64",
                        "archive": None,
                        "pattern": None,
                        "sha256": _EUGENEWARE_PINS["ffmpeg-darwin-x64"],
                        "needs_rosetta": True,
                    }
                )
            return sources
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

    def ffmpeg_size_hint(self) -> Optional[int]:
        """Bytes the first ffmpeg download would be, or ``None`` if unknown.

        Asked before the user commits to it: 150 MB is a decision, and the
        qualities screen should not ask for it with a shrug.
        """
        sources = self.ffmpeg_sources()
        if not sources:
            return None
        return _size_hint(str(sources[0]["url"]))

    def install_ffmpeg(
        self,
        on_status: Optional[StatusCallback] = None,
        on_progress: Optional[ProgressCallback] = None,
    ) -> Tool:
        dest = self.ffmpeg_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        errors: List[str] = []
        rosetta_needed = False

        for index, source in enumerate(self.ffmpeg_sources()):
            if source.get("needs_rosetta") and not _rosetta_available():
                # Skipped, and said so: an Intel build on a Mac without Rosetta
                # fails to start with a message nobody can act on.
                rosetta_needed = True
                errors.append(
                    f"{source['url']}: needs Rosetta 2, which is not installed"
                )
                continue
            if on_status:
                size = _size_hint(str(source["url"]))
                label = "Downloading ffmpeg (one time)…"
                if size:
                    label = f"Downloading ffmpeg ({human_size(size)}, one time)…"
                if index:
                    label = f"Trying another download mirror for ffmpeg ({index + 1})…"
                on_status(label)
            tag = _mirror_tag(str(source["url"]))
            archive: Optional[Path] = None
            try:
                if source["archive"] is None:
                    _verified_download(
                        source, dest, on_progress, dest.parent / f"ffmpeg-{tag}.part"
                    )
                else:
                    # In Viddl's own folder, not a temporary one: a part-file
                    # here is what lets a dropped 150 MB download resume, and a
                    # temporary folder is gone by the next run. Named per
                    # mirror, so one mirror's half is never glued to another's.
                    archive = dest.parent / (
                        f"ffmpeg-download-{tag}.{str(source['archive']).replace('.', '-')}"
                    )
                    # The checksums list covers the bundle, not the program
                    # inside it: check the archive, then unpack it.
                    _verified_download(
                        source,
                        archive,
                        on_progress,
                        archive.with_name(archive.name + ".part"),
                        reuse_existing=True,
                    )
                    _extract_member(archive, source["pattern"], dest)
                    archive.unlink(missing_ok=True)
                _make_runnable(dest)
                ok, output = _run_probe([str(dest)], "-version")
                if ok:
                    version = ""
                    match = re.search(r"ffmpeg version (\S+)", output)
                    if match:
                        version = match.group(1)
                    record_checksum(dest, sha256_of(dest))
                    # Whatever the failed attempts left is no longer needed, and
                    # a 150 MB half-file is not something to leave in the app
                    # folder once the program is in place.
                    _forget_download_leftovers(dest.parent)
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

        hint = (
            "You can install it the usual way for your system "
            "(for example “sudo apt install ffmpeg”, “brew install ffmpeg”, "
            "or winget install Gyan.FFmpeg) and Viddl will find it."
        )
        if rosetta_needed:
            hint += (
                "\nOn Apple silicon the other download is an Intel build: run "
                "“softwareupdate --install-rosetta” to make it usable."
            )
        raise ToolError("ffmpeg could not be downloaded automatically.", hint, raw="\n".join(errors))

    def installed_ffmpeg(self) -> Optional[Tool]:
        """The copy in Viddl's own folder, whether or not it is the one in use."""
        path = self.ffmpeg_path
        if not path.exists():
            return None
        ok, output = _run_probe([str(path)], "-version")
        if not ok:
            return None
        version = ""
        match = re.search(r"ffmpeg version (\S+)", output)
        if match:
            version = match.group(1)
        return Tool("ffmpeg", [str(path)], "viddl", version=version, path=path)

    def update_ffmpeg(
        self,
        on_status: Optional[StatusCallback] = None,
        on_progress: Optional[ProgressCallback] = None,
    ) -> Tuple[Optional[Tool], Tool]:
        """Fetch ffmpeg again — same mirrors, same checks, newer build."""
        before = self.installed_ffmpeg()
        with self._lock:
            self._ffmpeg = None
            tool = self.install_ffmpeg(on_status, on_progress)
        return before, tool

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
        # Asked here as well as at startup: a folder that cannot be created is
        # the cause of most of what follows, so it goes above the rest of it.
        problem = appdata.ensure_dirs()
        if problem is not None:
            for index, detail in enumerate(problem.details):
                label = "Problem:" if index == 0 else ""
                lines.append(f"{label:<14}{detail}")
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
                if label == "yt-dlp":
                    note = staleness_note(tool.version)
                    if note:
                        lines.append(f"{'':<14}{note}")
        lines.extend(appdata.environment_report())
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
