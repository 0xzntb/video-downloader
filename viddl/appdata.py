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
import sys
from pathlib import Path
from typing import Optional

from . import APP_NAME


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


def ensure_dirs() -> None:
    for path in (data_dir(), bin_dir()):
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            # A read-only home is better handled later with a clear message
            # than by crashing every code path that only wants a path.
            pass


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
        fallback = data_dir() / "Downloads"
        fallback.mkdir(parents=True, exist_ok=True)
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
