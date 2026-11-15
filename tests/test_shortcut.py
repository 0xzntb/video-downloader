"""The Windows one-click icons, checked where they cannot be created.

The real .lnk files are made on Windows (the CI job runs the installer there).
These tests hold what can be read anywhere: where the icons go, what command
they start, and that a shell that refuses is reported rather than raised.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from viddl import shortcut


def fake_run(calls):
    """Record the PowerShell call and leave the file it was asked for behind."""

    def run(command, environment):
        calls.append((command, environment))
        Path(environment["VIDDL_SHORTCUT_PATH"]).write_text("shortcut")
        return True, ""

    return run


def test_the_icons_live_where_windows_looks_for_them(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.delenv("OneDrive", raising=False)
    monkeypatch.setattr(shortcut.Path, "home", classmethod(lambda cls: tmp_path / "me"))
    start_menu, desktop = shortcut.shortcut_paths()
    assert start_menu == (
        tmp_path / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        / "Viddl.lnk"
    )
    assert desktop == tmp_path / "me" / "Desktop" / "Viddl.lnk"


def test_install_is_refused_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    result = shortcut.install()
    assert result.ok is False
    assert "Windows" in result.message


def test_install_writes_both_icons_with_powershell(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    launcher = tmp_path / "viddl.cmd"
    launcher.write_text("@echo off\n")
    monkeypatch.setattr(shortcut, "launcher_path", lambda: launcher)
    monkeypatch.setattr(shortcut, "_windows_terminal", lambda: "")
    monkeypatch.setattr(
        shortcut,
        "shortcut_paths",
        lambda: [tmp_path / "Start Menu" / "Viddl.lnk", tmp_path / "Desktop" / "Viddl.lnk"],
    )
    calls = []
    monkeypatch.setattr(shortcut, "_run", fake_run(calls))

    result = shortcut.install()

    assert result.ok is True
    assert "Start menu" in result.message
    assert len(calls) == 2
    for command, environment in calls:
        assert "powershell" in command[0].lower()
        assert command[-1] == shortcut._CREATE_SCRIPT
        assert environment["VIDDL_SHORTCUT_TARGET"] == str(launcher)
        assert environment["VIDDL_SHORTCUT_ARGS"] == ""
        assert Path(environment["VIDDL_SHORTCUT_PATH"]).is_file()


def test_windows_terminal_is_preferred_when_it_is_there(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    launcher = tmp_path / "viddl.cmd"
    launcher.write_text("@echo off\n")
    monkeypatch.setattr(shortcut, "launcher_path", lambda: launcher)
    monkeypatch.setattr(shortcut, "_windows_terminal", lambda: r"C:\WindowsApps\wt.exe")
    monkeypatch.setattr(
        shortcut, "shortcut_paths", lambda: [tmp_path / "Start Menu" / "Viddl.lnk"]
    )
    calls = []
    monkeypatch.setattr(shortcut, "_run", fake_run(calls))

    result = shortcut.install()

    assert result.ok is True
    environment = calls[0][1]
    assert environment["VIDDL_SHORTCUT_TARGET"] == r"C:\WindowsApps\wt.exe"
    assert environment["VIDDL_SHORTCUT_ARGS"] == shortcut._command_line(
        ["-d", environment["VIDDL_SHORTCUT_DIR"], "cmd", "/c", str(launcher)]
    )
    assert str(launcher) in environment["VIDDL_SHORTCUT_ARGS"]
    assert environment["VIDDL_SHORTCUT_ICON"] == r"C:\WindowsApps\wt.exe"


def test_a_command_line_is_quoted_by_the_one_rule_that_does_it():
    """The launcher and the ``viddl://`` handler must spell a path the same way.

    ``subprocess.list2cmdline`` is what the Windows handler builds its registry
    entry with. A hand-rolled quoter beside it was a second spelling of a rule
    that has to agree with the first, and the one that only looked for a space
    said nothing about a quote or an empty argument.
    """
    parts = [r"C:\Program Files\Viddl\viddl.exe", "-m", "viddl", "a b", ""]
    assert shortcut._command_line(parts) == subprocess.list2cmdline(parts)
    assert shortcut._command_line([r"C:\Program Files\Viddl\viddl.exe"]) == (
        r'"C:\Program Files\Viddl\viddl.exe"'
    )
    # No space, no quotes: quoting an argument that does not need it would make
    # the quotes part of the argument.
    assert shortcut._command_line(["viddl", "--plain"]) == "viddl --plain"


def test_a_wheel_install_gets_a_launcher_of_its_own(monkeypatch, tmp_path):
    """The icon points into Viddl's folder, not at whatever created it."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "viddl.cmd").write_text("@echo off\n")
    launcher = tmp_path / "LocalAppData" / "viddl" / "bin" / "viddl.cmd"
    monkeypatch.setattr(shortcut, "launcher_path", lambda: launcher)
    monkeypatch.setattr(shortcut, "launch_command", lambda: ["viddl.cmd"])
    monkeypatch.setattr(shortcut, "_windows_terminal", lambda: r"C:\WindowsApps\wt.exe")
    monkeypatch.setattr(
        shortcut, "shortcut_paths", lambda: [tmp_path / "Start Menu" / "Viddl.lnk"]
    )
    calls = []
    monkeypatch.setattr(shortcut, "_run", fake_run(calls))

    assert shortcut.install().ok is True

    body = launcher.read_text(encoding="utf-8")
    assert shortcut._LAUNCHER_NOTE in body
    assert str((tmp_path / "viddl.cmd").resolve()) in body
    assert "%*" in body
    assert str(launcher) in calls[0][1]["VIDDL_SHORTCUT_ARGS"]


def test_an_installer_launcher_is_left_alone(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    launcher = tmp_path / "viddl.cmd"
    launcher.write_text("@echo off\nrem install.ps1 copied me\n", encoding="utf-8")
    monkeypatch.setattr(shortcut, "launcher_path", lambda: launcher)
    monkeypatch.setattr(shortcut, "launch_command", lambda: [sys.executable])
    monkeypatch.setattr(shortcut, "_windows_terminal", lambda: "")
    monkeypatch.setattr(
        shortcut, "shortcut_paths", lambda: [tmp_path / "Start Menu" / "Viddl.lnk"]
    )
    calls = []
    monkeypatch.setattr(shortcut, "_run", fake_run(calls))

    assert shortcut.install().ok is True

    assert launcher.read_text(encoding="utf-8").splitlines() == [
        "@echo off",
        "rem install.ps1 copied me",
    ]
    assert calls[0][1]["VIDDL_SHORTCUT_TARGET"] == str(launcher)


def test_a_launcher_this_module_wrote_is_refreshed(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    launcher = tmp_path / "bin" / "viddl.cmd"
    launcher.parent.mkdir(parents=True)
    launcher.write_text(shortcut._LAUNCHER_NOTE + "\r\nstale\r\n", encoding="utf-8")
    monkeypatch.setattr(shortcut, "launcher_path", lambda: launcher)
    monkeypatch.setattr(shortcut, "launch_command", lambda: [sys.executable, "-m", "viddl"])
    monkeypatch.setattr(shortcut, "_windows_terminal", lambda: "")
    monkeypatch.setattr(
        shortcut, "shortcut_paths", lambda: [tmp_path / "Start Menu" / "Viddl.lnk"]
    )
    monkeypatch.setattr(shortcut, "_run", fake_run([]))

    assert shortcut.install().ok is True

    body = launcher.read_text(encoding="utf-8")
    assert "stale" not in body
    assert "-m viddl" in body


def test_a_shortcut_that_cannot_be_written_is_reported(monkeypatch, tmp_path):
    """A broken shell must not turn a working install into a failed one."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(shortcut, "launcher_path", lambda: tmp_path / "missing.cmd")
    monkeypatch.setattr(shortcut, "launch_command", lambda: [sys.executable])
    monkeypatch.setattr(shortcut, "_windows_terminal", lambda: "")
    monkeypatch.setattr(
        shortcut, "shortcut_paths", lambda: [tmp_path / "Start Menu" / "Viddl.lnk"]
    )
    monkeypatch.setattr(shortcut, "_run", lambda command, environment: (False, "denied"))

    result = shortcut.install()

    assert result.ok is False
    assert "would not create" in result.message
    assert any("denied" in step for step in result.steps)


def test_remove_deletes_both_icons(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    start_menu = tmp_path / "Start Menu" / "Viddl.lnk"
    desktop = tmp_path / "Desktop" / "Viddl.lnk"
    for path in (start_menu, desktop):
        path.parent.mkdir(parents=True)
        path.write_text("shortcut")
    monkeypatch.setattr(shortcut, "shortcut_paths", lambda: [start_menu, desktop])

    result = shortcut.remove()

    assert result.ok is True
    assert not start_menu.exists()
    assert not desktop.exists()


def test_remove_with_nothing_there_is_fine(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        shortcut, "shortcut_paths", lambda: [tmp_path / "Start Menu" / "Viddl.lnk"]
    )
    result = shortcut.remove()
    assert result.ok is True
    assert "Nothing" in result.message or "No Viddl" in result.message
