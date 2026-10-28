"""A tap on the shoulder when a long download ends (F9).

A forty-minute download ends in a terminal the person switched away from twenty
minutes ago, and the only thing Viddl could say was a line of text in a window
nobody was looking at. This module says it where the operating system keeps its
notifications — and rings the terminal's own bell when there is no such thing,
which is the one channel a plain terminal has.

**It is off unless asked for.** A program that pops up a window because it
finished rendering a five-second clip is a program people uninstall. ``notify``
in the settings file, ``VIDDL_NOTIFY`` or ``--notify`` turns it on, and the
progress screen has a key for it so the answer can be given at the moment the
question arises.

**Nothing here can fail a download.** Every path is best effort and swallows its
own errors: a notification is a courtesy, and a download that saved the file has
already done the job. The bell is the fallback, never the second choice — two
pings for one finished download would be a nuisance twice over.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, List, Optional, TextIO

#: A download shorter than this is still on the screen the person was looking
#: at, so there is nothing to call them back for. Twenty seconds is roughly the
#: point where somebody switches windows.
MIN_SECONDS = 20.0

#: What the desktop notifier calls the app.
APP = "Viddl"

#: The icon name freedesktop notifiers know by sight. Ignored where unknown.
_ICON = "video-x-generic"

#: How long a desktop notification stays up, in milliseconds.
_EXPIRE_MS = 8000

#: How long a notifier may take before the attempt is written off. A notifier
#: that hangs must not hold the exit of a download that already finished.
_TIMEOUT = 5.0

#: The Windows toast, in PowerShell, because that is the only way to raise one
#: without shipping a library. It is deliberately tiny: the template does the
#: layout, and the two strings are all Viddl has to say. ``-NonInteractive`` and
#: ``-NoProfile`` keep it from waiting on a prompt or reading a profile script.
_WINDOWS_TOAST = (
    "$ErrorActionPreference='Stop';"
    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
    "ContentType=WindowsRuntime] > $null;"
    "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, "
    "ContentType=WindowsRuntime] > $null;"
    "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument;"
    "$xml.LoadXml('<toast><visual><binding template=\"ToastGeneric\"><text>{title}"
    "</text><text>{body}</text></binding></visual></toast>');"
    "$toast = New-Object Windows.UI.Notifications.ToastNotification $xml;"
    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
    "'Microsoft.Windows.Shell.RunDialog').Show($toast)"
)


def _is_macos(platform: str) -> bool:
    return platform.startswith("darwin")


def _is_windows(platform: str) -> bool:
    return platform.startswith("win")


def _apple_quote(text: str) -> str:
    """``text`` as an AppleScript string literal, quotes and all.

    A file name with a quote in it is perfectly ordinary, and an unescaped one
    would end the literal early and turn the notification into a script error.
    """
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _xml_quote(text: str) -> str:
    """``text`` as XML character data, for the toast's two strings."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _powershell_quote(text: str) -> str:
    """``text`` inside the single-quoted PowerShell string the toast is built in."""
    return text.replace("'", "''")


def command(
    title: str,
    body: str,
    *,
    platform: Optional[str] = None,
    which: Optional[Callable[[str], Optional[str]]] = None,
) -> Optional[List[str]]:
    """The command that raises a desktop notification, or ``None`` for no notifier.

    Linux gets ``notify-send`` when it is installed, macOS gets ``osascript``
    (always present), and Windows gets PowerShell's own toast API. All three are
    looked for rather than assumed, so a bare container or an SSH session falls
    through to the bell instead of failing.
    """
    platform = sys.platform if platform is None else platform
    which = shutil.which if which is None else which

    if _is_macos(platform):
        script = f'display notification "{_apple_quote(body)}" with title "{_apple_quote(title)}"'
        return ["osascript", "-e", script]

    if _is_windows(platform):
        program = which("powershell") or which("pwsh")
        if program is None:
            return None
        # XML first, then PowerShell's quoting: the XML step may introduce an
        # ``&apos;``, and doubling a quote that is no longer there would leave
        # the entity broken.
        script = _WINDOWS_TOAST.replace(
            "{title}", _powershell_quote(_xml_quote(title))
        ).replace("{body}", _powershell_quote(_xml_quote(body)))
        return [program, "-NoProfile", "-NonInteractive", "-Command", script]

    program = which("notify-send")
    if program is None:
        return None
    return [
        program,
        "--app-name",
        APP,
        "--icon",
        _ICON,
        "--expire-time",
        str(_EXPIRE_MS),
        title,
        body,
    ]


def mechanism(
    *,
    platform: Optional[str] = None,
    which: Optional[Callable[[str], Optional[str]]] = None,
) -> str:
    """Which notifier this machine has: ``notify-send``, ``osascript``, ``powershell``
    or ``bell`` — the last meaning "nothing but the terminal's own bell".
    """
    argv = command(APP, "", platform=platform, which=which)
    if argv is None:
        return "bell"
    name = Path(argv[0]).name
    return "powershell" if name in ("powershell", "pwsh") else name


def ring(stream: Optional[TextIO] = None) -> None:
    """Ring the terminal's bell, if there is one to ring.

    The one signal a terminal has that does not need the person to be looking at
    it. Written and nothing else: a stream that has been closed — a pipe whose
    reader went away — must not turn a finished download into an error.
    """
    stream = sys.stderr if stream is None else stream
    try:
        stream.write("\a")
        stream.flush()
    except (OSError, ValueError):  # pragma: no cover - a stream that is gone
        pass


def send(
    title: str,
    body: str,
    *,
    stream: Optional[TextIO] = None,
    runner: Optional[Callable[..., subprocess.CompletedProcess]] = None,
    platform: Optional[str] = None,
    which: Optional[Callable[[str], Optional[str]]] = None,
    bell: bool = True,
) -> str:
    """Show one notification; return what was used, ``bell`` when nothing was.

    A notifier that is installed but fails — no session bus, a D-Bus timeout, a
    missing Windows runtime — falls back to the bell rather than saying nothing,
    because the whole point is that the person is elsewhere.
    """
    runner = subprocess.run if runner is None else runner
    argv = command(title, body, platform=platform, which=which)
    if argv is not None:
        try:
            completed = runner(
                argv,
                timeout=_TIMEOUT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            completed = None
        if completed is not None and completed.returncode == 0:
            name = Path(argv[0]).name
            return "powershell" if name in ("powershell", "pwsh") else name
    # A caller that has already rung the bell — the accessible mode does, at the
    # end of every run — asks for the notification only.
    if bell:
        ring(stream)
    return "bell"


def message(
    *,
    saved: Optional[str] = None,
    failed: bool = False,
    stopped: bool = False,
    count: int = 0,
) -> str:
    """What the notification says, given how the run ended.

    The file's own name rather than its whole path: a notification is a line in
    a corner of the screen, and the folder is the one thing the person already
    knows. A playlist says how many instead, because forty names is not a title.
    """
    if failed:
        return "The download failed. The reason is in the terminal."
    if stopped:
        return "Download stopped. What arrived was kept."
    if count:
        return f"Saved {count} {'video' if count == 1 else 'videos'}."
    if saved:
        return f"Saved {Path(saved).name}"
    return "Finished."


def title(*, failed: bool = False) -> str:
    """The notification's bold first line."""
    return "Viddl could not download that" if failed else "Viddl finished"


def tell(
    *,
    saved: Optional[str] = None,
    failed: bool = False,
    stopped: bool = False,
    count: int = 0,
    elapsed: float = 0.0,
    stream: Optional[TextIO] = None,
    runner: Optional[Callable[..., subprocess.CompletedProcess]] = None,
    platform: Optional[str] = None,
    which: Optional[Callable[[str], Optional[str]]] = None,
    bell: bool = True,
) -> str:
    """Say how a run ended, if it took long enough to be worth saying.

    Returns what was used — ``notify-send``, ``osascript``, ``powershell``,
    ``bell`` — or ``""`` when the run was too quick to bother the person about,
    which is the answer for every download that finished while they watched.
    """
    if elapsed < MIN_SECONDS:
        return ""
    return send(
        title(failed=failed),
        message(saved=saved, failed=failed, stopped=stopped, count=count),
        stream=stream,
        runner=runner,
        platform=platform,
        which=which,
        bell=bell,
    )
