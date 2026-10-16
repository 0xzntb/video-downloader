"""Shared fixtures and helpers for the test suite."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


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
