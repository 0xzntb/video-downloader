"""Where Viddl keeps its own files, and how it finds the Downloads folder.

These run with the environment variables cleared on purpose: the point is to
exercise the real per-platform decisions, not the test overrides.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from viddl import appdata

needs_posix_permissions = pytest.mark.skipif(
    sys.platform.startswith("win"), reason="Windows governs access with ACLs"
)


def mode_of(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.fixture
def no_overrides(monkeypatch):
    """Drop the test shortcuts so the platform logic runs for real."""
    monkeypatch.delenv("VIDDL_HOME", raising=False)
    monkeypatch.delenv("VIDDL_DOWNLOAD_DIR", raising=False)


# ---------------------------------------------------------------------------
# app folder
# ---------------------------------------------------------------------------


def test_data_dir_honours_the_environment_override(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDDL_HOME", str(tmp_path / "custom"))
    assert appdata.data_dir() == tmp_path / "custom"


def test_data_dir_follows_xdg_on_linux(no_overrides, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert appdata.data_dir() == tmp_path / "xdg" / "viddl"


def test_data_dir_defaults_to_local_share_without_xdg(no_overrides, monkeypatch):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert appdata.data_dir() == Path.home() / ".local" / "share" / "viddl"


def test_data_dir_uses_application_support_on_macos(no_overrides, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert appdata.data_dir() == Path.home() / "Library" / "Application Support" / "viddl"


def test_data_dir_uses_local_appdata_on_windows(no_overrides, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    assert appdata.data_dir() == tmp_path / "AppData" / "Local" / "viddl"


def test_bin_dir_and_log_sit_inside_the_app_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDDL_HOME", str(tmp_path / "app"))
    assert appdata.bin_dir() == tmp_path / "app" / "bin"
    assert appdata.log_path() == tmp_path / "app" / "viddl.log"


def test_ensure_dirs_creates_the_folders(tmp_path, monkeypatch):
    home = tmp_path / "app"
    monkeypatch.setenv("VIDDL_HOME", str(home))
    appdata.ensure_dirs()
    assert (home / "bin").is_dir()


def test_ensure_dirs_stays_quiet_when_it_cannot_write(tmp_path, monkeypatch):
    # A path that cannot exist, because a file is in the way.
    blocker = tmp_path / "blocked"
    blocker.write_text("not a folder", encoding="utf-8")
    monkeypatch.setenv("VIDDL_HOME", str(blocker / "viddl"))
    appdata.ensure_dirs()  # must not raise


# ---------------------------------------------------------------------------
# keeping the app folder to ourselves (S3)
#
# The folder holds two downloaded programs. Another account on a shared machine
# should be able to read neither them nor the log beside them, and should not be
# able to drop a replacement in their place.
# ---------------------------------------------------------------------------


@needs_posix_permissions
def test_the_app_folder_and_bin_are_owner_only(tmp_path, monkeypatch):
    home = tmp_path / "app"
    monkeypatch.setenv("VIDDL_HOME", str(home))
    appdata.ensure_dirs()
    assert mode_of(home) == 0o700
    assert mode_of(home / "bin") == 0o700


@needs_posix_permissions
def test_a_folder_left_loose_by_an_earlier_version_is_tightened(tmp_path, monkeypatch):
    """An upgrade has to fix what is already on disk, not only what it creates."""
    home = tmp_path / "app"
    (home / "bin").mkdir(parents=True)
    os.chmod(home, 0o755)
    os.chmod(home / "bin", 0o775)
    monkeypatch.setenv("VIDDL_HOME", str(home))

    appdata.ensure_dirs()

    assert mode_of(home) == 0o700
    assert mode_of(home / "bin") == 0o700


@needs_posix_permissions
def test_a_permissive_umask_cannot_loosen_the_app_folder(tmp_path, monkeypatch):
    """A mode asked for at ``mkdir`` time is the umask's to trim; this one is not."""
    monkeypatch.setenv("VIDDL_HOME", str(tmp_path / "app"))
    previous = os.umask(0)
    try:
        appdata.ensure_dirs()
    finally:
        os.umask(previous)
    assert mode_of(tmp_path / "app") == 0o700


@needs_posix_permissions
def test_a_folder_that_is_not_viddls_is_left_alone(tmp_path, monkeypatch):
    """``VIDDL_HOME`` may point at your home directory; that is not ours to lock."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    os.chmod(home, 0o755)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIDDL_HOME", str(home))

    appdata.ensure_dirs()

    assert mode_of(home) == 0o755
    assert mode_of(home / "bin") == 0o700


def test_the_root_directory_is_recognised_as_not_ours():
    """Asserted through the guard rather than by running ``chmod`` on ``/``."""
    assert appdata._not_ours_to_tighten(Path("/")) is True
    assert appdata._not_ours_to_tighten(Path.home()) is True
    assert appdata._not_ours_to_tighten(Path("/tmp")) is False


def test_windows_keeps_its_own_access_rules(monkeypatch, tmp_path):
    """Rather than pretending a POSIX mode means something there."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("VIDDL_HOME", raising=False)
    changed: list = []
    monkeypatch.setattr(os, "chmod", lambda path, mode: changed.append(path))

    appdata.ensure_dirs()

    assert changed == []
    assert (tmp_path / "viddl" / "bin").is_dir()


def test_a_filesystem_without_permissions_does_not_stop_viddl(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDDL_HOME", str(tmp_path / "app"))

    def refuse(path, mode):
        raise OSError("operation not supported")

    monkeypatch.setattr(os, "chmod", refuse)
    appdata.ensure_dirs()  # must not raise
    assert (tmp_path / "app" / "bin").is_dir()


# ---------------------------------------------------------------------------
# downloads folder
# ---------------------------------------------------------------------------


def test_downloads_dir_honours_the_override(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDDL_DOWNLOAD_DIR", str(tmp_path / "Elsewhere"))
    assert appdata.downloads_dir() == tmp_path / "Elsewhere"
    assert (tmp_path / "Elsewhere").is_dir()


def test_downloads_dir_reads_the_desktop_settings_file(no_overrides, tmp_path, monkeypatch):
    config = Path(os.environ["XDG_CONFIG_HOME"]) / "user-dirs.dirs"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        "# generated by the desktop\n"
        'XDG_DESKTOP_DIR="$HOME/Desktop"\n'
        'XDG_DOWNLOAD_DIR="$HOME/Videos"\n',
        encoding="utf-8",
    )
    folder = appdata.downloads_dir()
    assert folder == Path.home() / "Videos"
    assert folder.is_dir()


def test_downloads_dir_accepts_a_literal_path_in_the_settings_file(
    no_overrides, tmp_path, monkeypatch
):
    target = tmp_path / "Shared" / "Incoming"
    config = Path(os.environ["XDG_CONFIG_HOME"]) / "user-dirs.dirs"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(f'XDG_DOWNLOAD_DIR="{target}"\n', encoding="utf-8")
    assert appdata.downloads_dir() == target


def test_downloads_dir_ignores_a_settings_file_without_the_line(
    no_overrides, tmp_path, monkeypatch
):
    config = Path(os.environ["XDG_CONFIG_HOME"]) / "user-dirs.dirs"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("XDG_DESKTOP_DIR=\"$HOME/Desktop\"\n", encoding="utf-8")
    assert appdata.downloads_dir() == Path.home() / "Downloads"


def test_downloads_dir_uses_the_windows_location(no_overrides, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "Users" / "someone"))
    assert appdata.downloads_dir() == tmp_path / "Users" / "someone" / "Downloads"


def test_downloads_dir_falls_back_when_there_is_nowhere_to_write(
    tmp_path, monkeypatch
):
    """No writable Downloads folder: fall back inside Viddl's own folder."""
    blocker = tmp_path / "blocked"
    blocker.write_text("not a folder", encoding="utf-8")
    home = tmp_path / "app"
    monkeypatch.setenv("VIDDL_HOME", str(home))
    monkeypatch.setenv("VIDDL_DOWNLOAD_DIR", str(blocker / "Downloads"))

    assert appdata.downloads_dir() == home / "Downloads"
    assert (home / "Downloads").is_dir()


@needs_posix_permissions
def test_the_fallback_downloads_folder_is_owner_only(tmp_path, monkeypatch):
    """The last resort sits inside our own folder, so it gets the same treatment."""
    blocker = tmp_path / "blocked"
    blocker.write_text("not a folder", encoding="utf-8")
    home = tmp_path / "app"
    monkeypatch.setenv("VIDDL_HOME", str(home))
    monkeypatch.setenv("VIDDL_DOWNLOAD_DIR", str(blocker / "Downloads"))

    assert appdata.downloads_dir() == home / "Downloads"
    assert mode_of(home / "Downloads") == 0o700


# ---------------------------------------------------------------------------
# finding programs
# ---------------------------------------------------------------------------


def test_which_uses_the_path_first(tmp_path, monkeypatch):
    shim = tmp_path / "bin"
    shim.mkdir()
    (shim / "mytool").write_text("#!/bin/sh\n", encoding="utf-8")
    (shim / "mytool").chmod(0o755)
    monkeypatch.setenv("PATH", str(shim))
    assert appdata.which("mytool") == shim / "mytool"


def test_which_also_looks_where_gui_apps_do_not(tmp_path, monkeypatch):
    """A launcher started by a browser often has a thin PATH."""
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    local_bin = Path.home() / ".local" / "bin"
    local_bin.mkdir(parents=True, exist_ok=True)
    tool = local_bin / "viddl-fake"
    tool.write_text("#!/bin/sh\n", encoding="utf-8")
    tool.chmod(0o755)
    assert appdata.which("viddl-fake") == tool


def test_which_ignores_a_file_that_is_not_executable(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    local_bin = Path.home() / ".local" / "bin"
    local_bin.mkdir(parents=True, exist_ok=True)
    (local_bin / "not-executable").write_text("text", encoding="utf-8")
    assert appdata.which("not-executable") is None


def test_which_returns_nothing_for_an_unknown_program(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert appdata.which("definitely-not-a-real-program-xyz") is None
