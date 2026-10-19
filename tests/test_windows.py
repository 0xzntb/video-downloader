"""The Windows installer, checked where it cannot be run (R10).

The real check is the Windows job in CI: it runs ``install.ps1`` and the
launcher it writes. These tests hold the parts that can be read anywhere — the
files are pure ASCII (Windows PowerShell 5.1 reads a BOM-less script as ANSI),
the launcher finds the environment next door, and removing it removes the same
things that were put there.
"""

from __future__ import annotations

import sys
from pathlib import Path

import conftest

REPO = Path(__file__).resolve().parent.parent


def text(name: str) -> str:
    return (REPO / name).read_text(encoding="utf-8")


def test_the_installer_is_pure_ascii_for_old_powershell():
    """PowerShell 5.1 reads a script without a BOM as ANSI, so non-ASCII rots."""
    for name in ("install.ps1", "uninstall.ps1", "viddl.cmd"):
        (REPO / name).read_bytes().decode("ascii")


def test_the_launcher_starts_the_environment_the_installer_creates():
    body = text("viddl.cmd")
    assert r"%HERE%..\venv\Scripts\viddl.exe" in body
    assert r"%HERE%.venv\Scripts\viddl.exe" in body


def test_the_launcher_passes_every_argument_through():
    body = text("viddl.cmd")
    assert '"%VIDDL_EXE%" %*' in body
    # The exit code has to come from the app, not from the batch file. Using
    # %ERRORLEVEL% inside a parenthesised block would expand too early, which is
    # why the launcher uses labels.
    assert "exit /b %ERRORLEVEL%" in body


def test_the_launcher_says_what_to_do_when_nothing_is_installed():
    body = text("viddl.cmd")
    assert "install.ps1" in body
    assert "9009" in body


def test_the_installer_builds_a_private_environment_and_registers_the_handler():
    body = text("install.ps1")
    assert "-m', 'venv'" in body
    assert "$VenvPython" in body and "-m pip install" in body
    assert "--register-protocol" in body
    assert "SetEnvironmentVariable('Path'" in body
    assert "$env:VIDDL_HOME" in body, "the documented override has to be honoured"


def test_the_installer_can_be_run_without_touching_path_or_the_registry():
    body = text("install.ps1")
    assert "[switch]$NoPath" in body
    assert "[switch]$NoProtocol" in body
    # Both switches have to be honoured, not merely declared.
    assert "if (-not $NoPath)" in body
    assert "if (-not $NoProtocol)" in body


def test_the_uninstaller_undoes_what_the_installer_did():
    body = text("uninstall.ps1")
    assert "--unregister-protocol" in body
    assert "viddl.cmd" in body
    assert "Remove-Item" in body
    assert "Path'" in body, "the folder the installer added has to come off PATH"
    assert "[switch]$Purge" in body


def test_the_readme_no_longer_says_there_is_no_windows_installer():
    readme = text("README.md")
    assert "There is no\n  `install.ps1` yet" not in readme
    assert "install.ps1" in readme


# ---------------------------------------------------------------------------
# the test-only shim that lets the suite's fake tools run on Windows
#
# It cannot run here (it is a no-op off Windows), but the translation it does
# can be checked anywhere: a shebang script becomes a command Windows has an
# interpreter for, and a real program is left exactly as it was.
# ---------------------------------------------------------------------------


def shebang_script(tmp_path, name: str, line: str) -> Path:
    path = tmp_path / name
    path.write_text(line + "\n", encoding="utf-8")
    return path


def test_a_fake_python_tool_runs_through_this_interpreter(tmp_path):
    script = shebang_script(tmp_path, "yt-dlp.exe", "#!/usr/bin/env python3\nprint('1')")
    assert conftest._script_instead([str(script), "--version"]) == [
        sys.executable,
        str(script),
        "--version",
    ]


def test_a_fake_shell_tool_runs_through_bash(tmp_path, monkeypatch):
    monkeypatch.setattr(conftest, "_bash", lambda: r"C:\Program Files\Git\bin\bash.exe")
    script = shebang_script(tmp_path, "ffmpeg.exe", "#!/bin/sh\necho ready")
    assert conftest._script_instead([str(script), "-version"]) == [
        r"C:\Program Files\Git\bin\bash.exe",
        conftest._as_bash_path(script),
        "-version",
    ]


def test_a_real_program_is_left_alone(tmp_path):
    binary = tmp_path / "yt-dlp.exe"
    binary.write_bytes(b"MZ\x90\x00fake")
    assert conftest._script_instead([str(binary), "--version"]) is None


def test_a_plain_file_without_a_shebang_is_left_alone(tmp_path):
    text_file = shebang_script(tmp_path, "notes.txt", "not a script")
    assert conftest._script_instead([str(text_file)]) is None


def test_without_bash_a_shell_script_is_not_guessed_at(tmp_path, monkeypatch):
    monkeypatch.setattr(conftest, "_bash", lambda: "")
    script = shebang_script(tmp_path, "ffmpeg.exe", "#!/bin/sh\necho ready")
    assert conftest._script_instead([str(script)]) is None


def test_a_windows_path_becomes_the_one_bash_understands():
    assert conftest._as_bash_path(Path(r"C:\Users\me\a b\yt-dlp")) == "/c/Users/me/a b/yt-dlp"
    assert conftest._as_bash_path(Path("/tmp/plain")) == "/tmp/plain"
