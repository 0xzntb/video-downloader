"""Where Viddl keeps its files and where downloads go.

Everything lives in one folder per user, so there is nothing to configure:

* Linux   ``~/.local/share/viddl`` (or ``$XDG_DATA_HOME/viddl``)
* macOS   ``~/Library/Application Support/viddl``
* Windows ``%LOCALAPPDATA%\\viddl``

``VIDDL_HOME`` overrides that folder and ``VIDDL_DOWNLOAD_DIR`` overrides the
output folder. Both exist mainly so tests never touch a real home directory.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path
from typing import Optional

from . import APP_NAME

# Viddl's own folders hold two downloaded programs, so they are for the user
# who installed them and nobody else on the machine.
_OWNER_ONLY = 0o700

# The log holds links, which can carry tokens and signed query strings, so it is
# closed to everyone but its owner as well.
_OWNER_PRIVATE = 0o600


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _is_macos() -> bool:
    return sys.platform == "darwin"


def data_dir() -> Path:
    """The one folder Viddl owns."""
    override = os.environ.get("VIDDL_HOME")
    if override:
        return Path(override).expanduser()

    if _is_windows():
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    if _is_macos():
        return Path.home() / "Library" / "Application Support" / APP_NAME

    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / APP_NAME


def bin_dir() -> Path:
    """Where the yt-dlp and ffmpeg helpers are kept."""
    return data_dir() / "bin"


def log_path() -> Path:
    """A plain-text log of the last run, handy when something goes wrong."""
    return data_dir() / "viddl.log"


def _not_ours_to_tighten(path: Path) -> bool:
    """Whether this folder belongs to someone else, or to everyone.

    ``VIDDL_HOME`` can point anywhere, and the two places where ``chmod 0700``
    would be a disaster rather than a precaution are the root directory and the
    user's home directory itself. Those are skipped; every other folder is
    Viddl's to lock down.
    """
    try:
        resolved = path.resolve()
    except OSError:
        return True
    if resolved == Path(resolved.anchor):
        return True
    try:
        return resolved == Path.home().resolve()
    except (OSError, RuntimeError):
        return False


def _secure_dir(path: Path) -> None:
    """Create one of Viddl's folders, reachable only by its owner.

    The mode is applied after the folder exists, because ``mkdir``'s is masked
    by the umask and a folder made by an earlier version keeps whatever it was
    given then — so an upgrade tightens what is already on disk. Windows is
    skipped rather than pretended about: it governs access with ACLs, and the
    POSIX bits ``chmod`` accepts there do not mean this.

    A folder that cannot be created raises, as before; a mode that cannot be set
    does not, since a filesystem without permissions is not a reason to refuse
    to run.
    """
    path.mkdir(parents=True, exist_ok=True)
    if _is_windows() or _not_ours_to_tighten(path):
        return
    try:
        if stat.S_IMODE(path.stat().st_mode) != _OWNER_ONLY:
            os.chmod(path, _OWNER_ONLY)
    except OSError:
        pass


def ensure_dirs() -> None:
    for path in (data_dir(), bin_dir()):
        try:
            _secure_dir(path)
        except OSError:
            # A read-only home is better handled later with a clear message
            # than by crashing every code path that only wants a path.
            pass


def secure_log_file() -> Optional[Path]:
    """The log file: created if it is missing, readable only by its owner.

    An existing log left looser by an earlier version is tightened here, on the
    same reasoning as the folders in :func:`ensure_dirs`. ``None`` means there
    is nowhere to put it — a missing log is a lost nicety, not a reason for
    Viddl to refuse to run, so the caller carries on without one.
    """
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, _OWNER_PRIVATE)
        os.close(descriptor)
        if not _is_windows() and stat.S_IMODE(path.stat().st_mode) != _OWNER_PRIVATE:
            os.chmod(path, _OWNER_PRIVATE)
    except OSError:
        return None
    return path


def _xdg_download_dir() -> Optional[Path]:
    config_home = os.environ.get("XDG_CONFIG_HOME")
    config_file = (Path(config_home) if config_home else Path.home() / ".config") / "user-dirs.dirs"
    try:
        text = config_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("XDG_DOWNLOAD_DIR"):
            continue
        _, _, raw = line.partition("=")
        raw = raw.strip().strip('"')
        if not raw:
            continue
        expanded = raw.replace("$HOME", str(Path.home()))
        return Path(expanded)
    return None


def downloads_dir() -> Path:
    """The user's Downloads folder, created if needed."""
    override = os.environ.get("VIDDL_DOWNLOAD_DIR")
    if override:
        path = Path(override).expanduser()
    elif _is_windows():
        home = os.environ.get("USERPROFILE") or str(Path.home())
        path = Path(home) / "Downloads"
    else:
        path = _xdg_download_dir() or (Path.home() / "Downloads")

    try:
        path.mkdir(parents=True, exist_ok=True)
        # Verify we can actually write there before advertising it.
        if not os.access(path, os.W_OK):
            raise OSError("not writable")
    except OSError:
        # Our own folder is the fallback, and it is ours to lock down too: what
        # is downloaded here is nobody else's business.
        fallback = data_dir() / "Downloads"
        _secure_dir(fallback)
        return fallback
    return path


def which(program: str) -> Optional[Path]:
    """``shutil.which`` that also looks in a few places GUI apps often miss."""
    found = shutil.which(program)
    if found:
        return Path(found)

    extra = [
        Path.home() / ".local" / "bin",
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
        Path("/snap/bin"),
    ]
    for folder in extra:
        candidate = folder / program
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None
