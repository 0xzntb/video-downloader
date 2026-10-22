"""The link the user already has copied, read once and never acted on (F8).

The home screen asks people to paste a link even though the link they want is
usually already on the clipboard, put there by the browser a second ago.
Checking costs one subprocess at launch and removes the most common first
friction — but only as an *offer*: what comes back is held by the screen until
the person says yes, and never fills the box or starts a download by itself.

There is no portable way to read a clipboard, so this asks the platform's own
tool: ``Get-Clipboard`` on Windows, ``pbpaste`` on macOS, and on Linux whichever
of ``wl-paste``, ``xclip`` or ``xsel`` is installed. Each is a fixed argument
list run without a shell — a clipboard is somebody else's text, and none of it
may become syntax. Anything that fails, is missing, or takes too long is an
ordinary answer of "nothing", not an error: a computer with no clipboard tool
is a computer where the offer simply does not appear.

What is read here is not written to the log, not shown whole, and not kept:
the caller validates it into a link and shows its host at most. A clipboard can
hold a password as easily as a link, and Viddl has no business writing either
down.
"""

from __future__ import annotations

import subprocess
import sys
from typing import List, Optional

#: How long one clipboard tool may take. A read that takes longer is not a
#: clipboard, it is a hung process — and the user is sitting in front of an app
#: that must not appear stuck because of it. Three seconds rather than two
#: because PowerShell can take a while to start cold on Windows, and a slow
#: answer is still an answer; the cost of waiting is only paid when a tool
#: hangs, and the read happens on a worker thread either way.
TIMEOUT_SECONDS = 3.0


def commands() -> List[List[str]]:
    """The clipboard readers to try, in order, for this platform.

    More than one on Linux because neither X11 nor Wayland is guaranteed: the
    first tool that is installed and answers wins, and the rest are not tried.
    """
    if sys.platform.startswith("win"):
        # ``-Raw`` keeps the newlines Windows puts on a copied line; the caller
        # trims them either way.
        return [["powershell", "-NoProfile", "-NonInteractive", "-Command", "Get-Clipboard -Raw"]]
    if sys.platform == "darwin":
        return [["pbpaste"]]
    return [
        # Wayland first: it is the default on modern desktops, and xclip there
        # either fails or reads a different clipboard than the one being used.
        ["wl-paste", "--no-newline"],
        ["xclip", "-selection", "clipboard", "-o"],
        ["xsel", "--clipboard", "--output"],
    ]


def read_text(timeout: float = TIMEOUT_SECONDS) -> str:
    """What the clipboard holds, or ``""`` when it cannot be read.

    Never raises. The text is returned as-is apart from trimming: deciding
    whether it is a link is :func:`viddl.util.safe_video_link`'s job, which is
    the same gate every other way in goes through.
    """
    for command in commands():
        text = _run(command, timeout)
        if text is not None:
            return _tidy(text)
    return ""


def _run(command: List[str], timeout: float) -> Optional[str]:
    """Run one reader; ``None`` means "try the next one", not "it is empty"."""
    try:
        done = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            text=True,
            errors="replace",
            timeout=timeout,
            # No shell and no check: the argument list is fixed, and a non-zero
            # exit is how a clipboard tool says there is nothing to read.
            shell=False,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        # Not installed, not permitted, or hung past the timeout.
        return None
    if done.returncode != 0 or done.stdout is None:
        return None
    return done.stdout


def _tidy(text: str) -> str:
    """Drop what a clipboard tool adds that a link never has."""
    # ``\\x00`` is what PowerShell's clipboard object can carry into a string,
    # and the trailing newline is what most tools put back.
    return text.replace("\x00", "").strip()
