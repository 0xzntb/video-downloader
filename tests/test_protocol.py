"""The viddl:// hand-off and its operating-system registration."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from viddl import URL_SCHEME
from viddl import protocol
from viddl.protocol import (
    SchemeRequest,
    build_scheme_url,
    extension_source_dir,
    install_extension,
    launch_command,
    parse_scheme,
    parse_scheme_url,
    register,
    unregister,
)


@pytest.mark.parametrize(
    "raw, url, audio",
    [
        (
            "viddl://download?url=https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3Dabc",
            "https://www.youtube.com/watch?v=abc",
            False,
        ),
        (
            "viddl://download?url=https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3Dabc&audio=1",
            "https://www.youtube.com/watch?v=abc",
            True,
        ),
        ("viddl://download/https://vimeo.com/12345", "https://vimeo.com/12345", False),
        ("viddl://https://vimeo.com/12345", "https://vimeo.com/12345", False),
        ("viddl:https://vimeo.com/12345", "https://vimeo.com/12345", False),
        ("viddl://www.vimeo.com/12345", "https://www.vimeo.com/12345", False),
        ("viddl://download/https://vimeo.com/12345&audio=1", "https://vimeo.com/12345", True),
        ("viddl:https://vimeo.com/12345?audio=1", "https://vimeo.com/12345", True),
    ],
)
def test_understands_every_link_shape(raw, url, audio):
    request = parse_scheme(raw)
    assert request == SchemeRequest(url=url, audio=audio)
    assert parse_scheme_url(raw) == url


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "https://www.youtube.com/watch?v=abc",
        "viddl://",
        "viddl://download",
        "not-a-link",
    ],
)
def test_rejects_anything_that_is_not_a_hand_off(raw):
    assert parse_scheme(raw) is None


def test_build_scheme_url_round_trips():
    target = build_scheme_url("https://x.com/user/status/1", audio=True)
    assert target.startswith("viddl://download?url=")
    request = parse_scheme(target)
    assert request.url == "https://x.com/user/status/1"
    assert request.audio is True


def test_launch_command_points_at_something_real():
    command = launch_command()
    assert command
    assert isinstance(command[0], str)


def test_bundled_extension_is_complete():
    source = extension_source_dir()
    assert source.is_dir()
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 3
    for relative in (
        manifest["background"]["service_worker"],
        manifest["action"]["default_popup"],
        *manifest["icons"].values(),
        *[entry for block in manifest["content_scripts"] for entry in block.get("js", [])],
        *[entry for block in manifest["content_scripts"] for entry in block.get("css", [])],
    ):
        assert (source / relative).is_file(), relative


def test_install_extension_copies_to_a_loadable_folder(tmp_path):
    target = install_extension(tmp_path / "out")
    assert (target / "manifest.json").is_file()
    assert (target / "icons" / "icon128.png").is_file()


def test_install_extension_replaces_an_older_copy(tmp_path):
    target = install_extension(tmp_path / "out")
    stale = target / "leftover-from-an-old-version.js"
    stale.write_text("old", encoding="utf-8")
    again = install_extension(tmp_path / "out")
    assert again == target
    assert not stale.exists(), "a stale file would confuse the browser"
    assert (target / "manifest.json").is_file()


def test_install_extension_reports_a_missing_source(tmp_path, monkeypatch):
    import viddl.protocol as protocol

    monkeypatch.setattr(protocol, "extension_source_dir", lambda: tmp_path / "nowhere")
    with pytest.raises(FileNotFoundError):
        protocol.install_extension(tmp_path / "out")


# ---------------------------------------------------------------------------
# macOS and Windows registration
#
# These paths cannot be exercised by hand on this machine, so the platform
# branch is simulated and the artifacts it produces are inspected directly.
# ---------------------------------------------------------------------------


class _FakeKey:
    def __init__(self, registry, path):
        self._registry = registry
        self.path = path

    def __enter__(self):
        return self.path

    def __exit__(self, *exc_info):
        return False


class FakeWinreg:
    """Just enough of the registry to record what Viddl would write."""

    HKEY_CURRENT_USER = "HKEY_CURRENT_USER"
    REG_SZ = "REG_SZ"

    def __init__(self):
        self.values = {}
        self.created = []
        self.deleted = []

    def CreateKey(self, parent, subkey=None):
        path = parent if subkey is None else f"{parent}\\{subkey}"
        self.created.append(path)
        return _FakeKey(self, path)

    def SetValueEx(self, key, name, reserved, kind, value):
        self.values[(str(key), name)] = value

    def DeleteKey(self, parent, subkey=None):
        self.deleted.append(parent if subkey is None else f"{parent}\\{subkey}")


def _macos_bundle() -> Path:
    return Path.home() / "Applications" / "Viddl URL Handler.app"


def test_macos_registration_writes_a_url_handler_bundle(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    result = register()

    bundle = _macos_bundle()
    plist = bundle / "Contents" / "Info.plist"
    runner = bundle / "Contents" / "MacOS" / "viddl-open"
    assert plist.is_file() and runner.is_file()
    assert os.access(runner, os.X_OK), "macOS only runs it if it is executable"

    plist_text = plist.read_text(encoding="utf-8")
    assert "CFBundleURLSchemes" in plist_text
    assert f"<string>{URL_SCHEME}</string>" in plist_text
    assert result.ok is True
    assert result.steps


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX shell")
def test_the_macos_runner_script_is_valid_shell(monkeypatch):
    """It cannot be run here, but it can at least be parsed."""
    monkeypatch.setattr(sys, "platform", "darwin")
    register()
    runner = _macos_bundle() / "Contents" / "MacOS" / "viddl-open"
    completed = subprocess.run(["sh", "-n", str(runner)], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    body = runner.read_text(encoding="utf-8")
    assert "--uri" in body
    # The link is single-quoted for the generated command file.
    assert "--uri '" in body


def test_macos_unregistration_removes_the_bundle(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    register()
    assert _macos_bundle().exists()
    unregister()
    assert not _macos_bundle().exists()


def test_windows_registration_writes_the_registry_entry(monkeypatch):
    fake = FakeWinreg()
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(sys, "platform", "win32")
    # launch_command() has its own test; pinning it here keeps the standard
    # library out of the way (shutil.which reaches for _winapi on win32).
    monkeypatch.setattr(
        protocol, "launch_command", lambda: [r"C:\Program Files\Viddl\viddl.exe"]
    )
    result = register()

    root = rf"HKEY_CURRENT_USER\Software\Classes\{URL_SCHEME}"
    assert result.ok is True
    assert root in fake.created
    assert fake.values[(root, "")] == f"URL:{URL_SCHEME} protocol"
    assert fake.values[(root, "URL Protocol")] == ""

    command = fake.values[(rf"{root}\shell\open\command", "")]
    assert "viddl-open.cmd" in command
    assert "%1" in command

    launcher = Path(os.environ["VIDDL_HOME"]) / "viddl-open.cmd"
    assert launcher.is_file()
    body = launcher.read_text(encoding="utf-8")
    assert "--uri %1" in body
    assert body.startswith("@echo off")
    # A path with a space in it has to stay quoted or Windows will split it.
    assert r'"C:\Program Files\Viddl\viddl.exe"' in body


def test_windows_unregistration_removes_the_keys(monkeypatch):
    fake = FakeWinreg()
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(sys, "platform", "win32")
    unregister()
    assert fake.deleted == [
        rf"HKEY_CURRENT_USER\Software\Classes\{URL_SCHEME}\shell\open\command",
        rf"HKEY_CURRENT_USER\Software\Classes\{URL_SCHEME}\shell\open",
        rf"HKEY_CURRENT_USER\Software\Classes\{URL_SCHEME}\shell",
        rf"HKEY_CURRENT_USER\Software\Classes\{URL_SCHEME}",
    ]


def test_windows_without_registry_access_says_so(monkeypatch):
    monkeypatch.setitem(sys.modules, "winreg", None)  # import fails
    monkeypatch.setattr(sys, "platform", "win32")
    result = register()
    assert result.ok is False
    assert "registry" in result.message.lower()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux desktop entry")
def test_register_writes_a_desktop_handler(tmp_path, monkeypatch):
    result = register()
    desktop = Path.home() / ".local" / "share" / "applications" / "viddl-handler.desktop"
    assert desktop.is_file(), "the desktop entry must be written"
    body = desktop.read_text(encoding="utf-8")
    assert "MimeType=x-scheme-handler/viddl;" in body
    assert "Terminal=true" in body
    assert "--uri %u" in body
    # Whether the default could be set depends on xdg-mime being installed, but
    # either way the user is told exactly what happened.
    assert result.message
    assert result.steps

    unregister()
    assert not desktop.exists()
