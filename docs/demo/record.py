#!/usr/bin/env python3
"""Record ``viddl.cast``: the real app, driven the way a person drives it.

The point of this file is that the recording is not a mock-up. It starts the
actual ``viddl`` command in a pseudo-terminal, types a link into the real link
box, watches the real qualities screen answer, presses the key a person would
press to choose 720p, and lets the real progress and done screens do their
work. Only the engine is a stand-in (``fake_ytdlp.py``), because the demo has
to be reproducible and run without a network.

Keys are sent when the screen shows the thing that invites them — the join
below — rather than after fixed sleeps, so a slow machine records the same
demo, only slower. If a screen never arrives, the script fails instead of
quietly recording a stuck one.

    python docs/demo/record.py            # writes docs/demo/viddl.cast

The result is an asciinema v2 recording: ``asciinema play docs/demo/viddl.cast``
replays it, and ``docs/demo/render.py`` renders it as the README's GIF.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import select
import shutil
import struct
import subprocess
import sys
import tempfile
import termios
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
CAST = HERE / "viddl.cast"

COLS, ROWS = 88, 26
URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

#: The join, step by step: what has to be on screen first, what is typed then,
#: how long to wait before typing it, and how long to let the next screen be
#: read. The waits are what a person would do — a link is not entered the same
#: instant the window appears, and a done screen is worth a look.
JOIN = [
    ("Download a video or a song", URL.encode(), 0.7, 0.0),
    (URL, b"\r", 0.8, 0.0),  # the link is in the box: ask about it
    ("Look at this cat", b"\x1b[B", 0.9, 0.0),  # down: 1080p -> 720p
    ("\u203a", b"\r", 0.6, 0.0),  # the mark the cursor carries (U6)
    ("Look at this cat [dQw4w9WgXcQ].mp4", b"\x11", 2.8, 0.0),  # done, Ctrl+Q
]

MAX_SECONDS = 25.0


def log(message: str) -> None:
    print(f"record: {message}", file=sys.stderr)


def sandbox() -> Path:
    """A throwaway home, so the recording never touches the real one.

    A fixed path rather than a random one, because the done screen shows the
    file's full path and a directory called ``viddl-demo-25_x7a73`` in the GIF
    would look like an accident.
    """
    root = Path(tempfile.gettempdir()) / "viddl-demo"
    shutil.rmtree(root, ignore_errors=True)
    (root / "home").mkdir(parents=True)
    (root / "Downloads").mkdir()
    return root


def main() -> int:
    root = sandbox()
    env = dict(os.environ)
    env.update(
        TERM="xterm-256color",
        HOME=str(root / "home"),
        VIDDL_HOME=str(root / "home" / ".local" / "share" / "viddl"),
        VIDDL_DOWNLOAD_DIR=str(root / "Downloads"),
        VIDDL_YTDLP=str(HERE / "fake_ytdlp.py"),
        # The colours are the point: a demo recorded through somebody's own
        # NO_COLOR would show a flat screen, and an empty NO_COLOR still counts
        # as set.
        COLORFGBG="15;0",
    )
    for name in ("NO_COLOR", "LC_ALL", "LC_MESSAGES"):
        env.pop(name, None)
    env["LANG"] = "en_US.UTF-8"

    master, slave = os.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
    # No echo: a pseudo-terminal echoes what is written to its master, so the
    # link would be drawn twice — once by the line discipline at whatever
    # position the cursor happened to be in, and once by the app's own link box.
    attributes = termios.tcgetattr(slave)
    attributes[3] &= ~termios.ECHO
    termios.tcsetattr(slave, termios.TCSANOW, attributes)
    child = subprocess.Popen(
        [sys.executable, "-m", "viddl"],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=env,
        cwd=str(REPO),
        start_new_session=True,
    )
    os.close(slave)

    started = time.monotonic()
    events: list = []
    seen = ""
    steps = list(JOIN)
    index = 0
    armed_at: float | None = None
    send_at = 0.0
    step_started = started
    output = b""

    try:
        while time.monotonic() - started < MAX_SECONDS:
            ready, _, _ = select.select([master], [], [], 0.05)
            if ready:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    chunk = b""
                if not chunk:
                    break
                output += chunk
                text = chunk.decode("utf-8", "replace")
                events.append([round(time.monotonic() - started, 3), "o", text])
                seen = (seen + text)[-6000:]
            now = time.monotonic() - started
            if index >= len(steps):
                # Everything has been sent: wait for the app to close itself.
                if child.poll() is not None:
                    break
                continue
            expect, keys, before, after = steps[index]
            if armed_at is None:
                if expect in seen:
                    armed_at = now
                    send_at = now + before
                elif now - step_started > 8.0:
                    raise SystemExit(
                        f"record: waited 8s for {expect!r} and never saw it"
                    )
                continue
            if now >= send_at:
                os.write(master, keys)
                # Recorded as asciinema's own input event, so a player can show
                # what was typed and the renderer knows where the demo begins.
                events.append(
                    [round(now, 3), "i", keys.decode("utf-8", "backslashreplace")]
                )
                log(f"typed {keys!r} at {now:.1f}s")
                index += 1
                armed_at = None
                step_started = now + after
                seen = "" if after else seen

    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover - stubborn child
                child.kill()
        os.close(master)
        shutil.rmtree(root, ignore_errors=True)

    text = output.decode("utf-8", "replace")
    for wanted in ("720p", "Look at this cat [dQw4w9WgXcQ].mp4"):
        if wanted not in text:
            # What was on screen, for whoever has to fix the join.
            plain = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", text)
            (HERE / "last-screen.txt").write_text(plain[-6000:], encoding="utf-8")
            raise SystemExit(
                f"record: the demo never showed {wanted!r} — the screen is in "
                f"{(HERE / 'last-screen.txt').relative_to(REPO)}"
            )

    lines = [
        json.dumps(
            {
                "version": 2,
                "width": COLS,
                "height": ROWS,
                "timestamp": int(time.time()),
                "env": {"SHELL": "/bin/sh", "TERM": "xterm-256color"},
            }
        )
    ]
    lines += [json.dumps(event) for event in events]
    CAST.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"wrote {CAST.relative_to(REPO)} — {len(events)} events, "
        f"{events[-1][0] if events else 0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
