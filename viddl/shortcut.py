"""One-click launch on Windows: the Viddl icons in the Start menu and Desktop.

Windows has no menu entry of its own to give a terminal program, so the
installer leaves a shortcut in the two places a person looks for one. The
shortcut runs a launcher in Viddl's own folder — in Windows Terminal where it
is installed, because the full-screen app needs a real terminal for its colours
and characters, and in a console window otherwise.

That launcher matters for a wheel install: ``pipx`` and ``pip`` put a ``viddl``
command in an environment of their own and no copy in Viddl's folder, so
``--install-shortcut`` writes one there and points the icon at it. Re-running
the command refreshes a launcher it wrote; one copied by ``install.ps1`` is
left exactly as it is.

None of this is needed for the app to work, so a shortcut that cannot be
written is reported rather than raised: a missing icon must not turn a working
install into a failed one.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Tuple

from . import appdata
from .protocol import RegistrationResult, launch_command

LINK_NAME = "Viddl.lnk"

# A launcher this module wrote says so, which is how a later --install-shortcut
# knows it may be refreshed. install.ps1's copy carries no marker and is left
# alone, since replacing it could change which environment starts the app.
_LAUNCHER_NOTE = "rem Written by viddl --install-shortcut."
_LAUNCHER_BODY = "@echo off\n{note}\n{command} %*\n"

# The whole of the .lnk writer: Windows' own shell object, reached through
# PowerShell, because the alternative is a COM interface through ctypes and a
# shortcut is not worth that much code. The values arrive as environment
# variables so no path is ever pasted into the script text — a path can contain
# quotes, and a script assembled from one could not be trusted with them.
_CREATE_SCRIPT = (
    "$ErrorActionPreference = 'Stop'; "
    "$shell = New-Object -ComObject WScript.Shell; "
    "$link = $shell.CreateShortcut($env:VIDDL_SHORTCUT_PATH); "
    "$link.TargetPath = $env:VIDDL_SHORTCUT_TARGET; "
    "$link.Arguments = $env:VIDDL_SHORTCUT_ARGS; "
    "$link.WorkingDirectory = $env:VIDDL_SHORTCUT_DIR; "
    "$link.Description = 'Viddl'; "
    "if ($env:VIDDL_SHORTCUT_ICON) { $link.IconLocation = $env:VIDDL_SHORTCUT_ICON }; "
    "$link.Save()"
)


def supported() -> bool:
    """Whether this is a Windows machine, the only place a ``.lnk`` means anything."""
    return sys.platform.startswith("win")


def _start_menu_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def _desktop_dir() -> Path:
    # Windows spells this variable `OneDrive`; the all-capitals spelling ruff
    # prefers would work there too, but not on a case-sensitive filesystem.
    redirected = os.environ.get("OneDrive")  # noqa: SIM112
    if redirected:
        candidate = Path(redirected) / "Desktop"
        if candidate.is_dir():
            return candidate
    return Path.home() / "Desktop"


def shortcut_paths() -> List[Path]:
    """The two icons one click can land on."""
    return [_start_menu_dir() / LINK_NAME, _desktop_dir() / LINK_NAME]


def launcher_path() -> Path:
    """The command the installer puts on PATH — the one typing ``viddl`` runs."""
    return appdata.data_dir() / "bin" / "viddl.cmd"


def _windows_terminal() -> str:
    return shutil.which("wt.exe") or shutil.which("wt") or ""


def _command_line(parts: List[str]) -> str:
    """The command line Windows splits back into ``parts``.

    ``subprocess.list2cmdline`` is the one home for the rule, and it is the same
    function the ``viddl://`` handler builds its registry entry with. A local
    quoter that only reacted to a space was a second spelling of it, and one
    that missed everything else Windows treats specially.
    """
    return subprocess.list2cmdline(parts)


def _ensure_launcher() -> Tuple[Path, bool]:
    """A launcher in Viddl's own folder, written when nothing put one there.

    A checkout or a wheel install keeps its ``viddl`` command somewhere else —
    a console script, a virtual environment, a folder that may move. The icon
    points here instead, so it works for an install made with ``pipx`` as well
    as for one made by ``install.ps1``.
    """
    path = launcher_path()
    if path.is_file():
        try:
            existing = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            existing = ""
        if _LAUNCHER_NOTE not in existing:
            return path, False

    parts = launch_command()
    if parts:
        found = Path(parts[0])
        if found.is_file():
            # What PATH answered can be a relative name; a shortcut is started
            # from its own working folder, so the path has to be the one on
            # disk rather than the one this shell resolved.
            parts = [str(found.resolve()), *parts[1:]]
    body = _LAUNCHER_BODY.format(note=_LAUNCHER_NOTE, command=_command_line(parts))
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\r\n") as handle:
        handle.write(body)
    return path, True


def _run_target(launcher: Path) -> Tuple[str, str, str]:
    """The target, arguments and working folder the shortcut starts."""
    folder = str(appdata.data_dir())
    terminal = _windows_terminal()
    if terminal:
        # Windows Terminal's own arguments, then ``cmd`` running the launcher:
        # one command line, quoted once, by the rule above.
        arguments = _command_line(["-d", folder, "cmd", "/c", str(launcher)])
        return terminal, arguments, folder
    return str(launcher), "", folder


def _icon(target: str) -> str:
    # A shortcut to a .cmd gets the generic shell icon; pointing it at a terminal
    # that runs it at least gives the icon a picture.
    return target if target.lower().endswith(".exe") else ""


def _run(command: List[str], environment: Dict[str, str]) -> Tuple[bool, str]:
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=30, env=environment
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    if completed.returncode != 0:
        return False, (completed.stderr or completed.stdout or "").strip()
    return True, ""


def _write_link(
    path: Path, target: str, arguments: str, folder: str, icon: str
) -> Tuple[bool, str]:
    environment: Dict[str, str] = dict(os.environ)
    environment.update(
        {
            "VIDDL_SHORTCUT_PATH": str(path),
            "VIDDL_SHORTCUT_TARGET": target,
            "VIDDL_SHORTCUT_ARGS": arguments,
            "VIDDL_SHORTCUT_DIR": folder,
            "VIDDL_SHORTCUT_ICON": icon,
        }
    )
    powershell = shutil.which("powershell") or "powershell"
    command = [
        powershell,
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        _CREATE_SCRIPT,
    ]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, str(exc)
    ok, detail = _run(command, environment)
    if not ok:
        return False, detail
    if not path.is_file():
        return False, "PowerShell finished but wrote no shortcut"
    return True, ""


def install() -> RegistrationResult:
    """Put the two icons in place, or say why not."""
    if not supported():
        return RegistrationResult(
            False,
            "One-click shortcuts are a Windows feature: on Linux and macOS the "
            "installer already adds Viddl to the applications menu.",
        )

    try:
        launcher, made = _ensure_launcher()
    except OSError as exc:
        return RegistrationResult(
            False,
            f"Could not write the launcher in {launcher_path().parent} ({exc}).",
        )

    target, arguments, folder = _run_target(launcher)
    icon = _icon(target)
    paths = shortcut_paths()
    written: List[str] = []
    steps: List[str] = [f"Wrote {launcher}"] if made else []
    for path in paths:
        ok, detail = _write_link(path, target, arguments, folder, icon)
        if ok:
            written.append(str(path))
            steps.append(f"Wrote {path}")
        else:
            steps.append(f"Could not write {path}: {detail}")

    if not written:
        return RegistrationResult(
            False, "Windows would not create the Viddl shortcut.", steps
        )
    if len(written) == len(paths):
        message = (
            "Done — Viddl can be started with one click from the Start menu "
            "and the Desktop."
        )
    else:
        message = "Partly done — one shortcut could not be written; the other is in place."
    return RegistrationResult(True, message, steps)


def remove() -> RegistrationResult:
    """Take the icons away again, leaving anything else where it is."""
    if not supported():
        return RegistrationResult(True, "No Windows shortcuts on this machine.")

    removed = 0
    steps: List[str] = []
    failed = False
    for path in shortcut_paths():
        try:
            if path.is_file():
                path.unlink()
                removed += 1
                steps.append(f"Removed {path}")
        except OSError as exc:
            failed = True
            steps.append(f"Could not remove {path}: {exc}")

    if failed:
        return RegistrationResult(False, "Could not remove every Viddl shortcut.", steps)
    if not removed:
        return RegistrationResult(True, "No Viddl shortcuts to remove.", steps)
    return RegistrationResult(True, "Removed the Viddl shortcut icons.", steps)
