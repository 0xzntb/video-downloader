"""Shared fixtures and helpers for the test suite."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from viddl import download as download_mod

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def fresh_probe_cache():
    """A remembered look-up must never leak from one test to the next (R9)."""
    download_mod.clear_probe_cache()
    yield
    download_mod.clear_probe_cache()


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Never let a test read or write the real user's folders."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("VIDDL_HOME", str(tmp_path / "appdata"))
    monkeypatch.setenv("VIDDL_DOWNLOAD_DIR", str(tmp_path / "Downloads"))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.delenv("VIDDL_YTDLP", raising=False)
    monkeypatch.delenv("VIDDL_FFMPEG", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    # Windows reads these, not HOME, and a test must never touch the real
    # profile of the account running it.
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    return home


def write_executable(path: Path, body: str) -> Path:
    """Write a small runnable script for fake-tool tests."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def fake_ytdlp_script(path: Path, version: str = "2099.01.01") -> Path:
    """A stand-in yt-dlp that reports a version."""
    return write_executable(path, f"#!/bin/sh\necho {version}\n")


def make_tool_prefix(script: Path) -> list:
    """Run a fake tool through Python so it works on every platform."""
    return [sys.executable, str(script)] if os.name == "nt" else [str(script)]


# ---------------------------------------------------------------------------
# running the suite's fake tools on Windows (R10)
#
# Almost every fake yt-dlp/ffmpeg in this suite is a two-line shell or Python
# script with a shebang, because that is what a Unix machine executes. Windows
# has no shebang: a file called ``yt-dlp.exe`` that holds Python source is just
# text, and CreateProcess refuses it. Rather than make every test platform
# aware, the two ways the suite starts those files are wrapped here: Python
# scripts go through the same interpreter running the tests, and shell scripts
# through the bash that ships with Git for Windows (present on the runners).
# Nothing in ``viddl`` is changed, and on every other platform this does
# nothing at all.
# ---------------------------------------------------------------------------


_GIT_BASH = (
    r"C:\Program Files\Git\bin\bash.exe",
    r"C:\Program Files\Git\usr\bin\bash.exe",
    r"C:\Program Files (x86)\Git\bin\bash.exe",
)


def _bash() -> str:
    found = shutil.which("bash")
    if found:
        return found
    for candidate in _GIT_BASH:
        if Path(candidate).is_file():
            return candidate
    return ""


def _as_bash_path(path: Path) -> str:
    """``C:\\a b\\x`` as ``/c/a b/x``, which is how bash names it."""
    text = str(path)
    if len(text) > 1 and text[1] == ":":
        drive = text[0].lower()
        rest = text[2:].replace("\\", "/").lstrip("/")
        return f"/{drive}/{rest}"
    return text.replace("\\", "/")


def _script_instead(argv):
    """A command that runs a shebang script through something Windows has."""
    if not isinstance(argv, (list, tuple)) or not argv:
        return None
    script = Path(str(argv[0]))
    try:
        with script.open("rb") as handle:
            if handle.read(2) != b"#!":
                return None
        first_line = script.read_text(encoding="utf-8", errors="replace").splitlines()[0]
    except (OSError, IndexError):
        return None
    rest = [str(part) for part in argv[1:]]
    if "python" in first_line:
        return [sys.executable, str(script), *rest]
    bash = _bash()
    if bash and (first_line.rstrip().endswith("sh") or "/sh" in first_line):
        return [bash, _as_bash_path(script), *rest]
    return None


@pytest.fixture(autouse=True)
def shebang_scripts_run_on_windows(monkeypatch):
    if os.name != "nt":
        yield
        return

    real_popen = subprocess.Popen
    real_run = subprocess.run

    def popen(args, *rest, **kwargs):
        return real_popen(_script_instead(args) or args, *rest, **kwargs)

    def run(args, *rest, **kwargs):
        return real_run(_script_instead(args) or args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(subprocess, "run", run)
    yield
