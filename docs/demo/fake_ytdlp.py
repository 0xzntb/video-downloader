#!/usr/bin/env python3
"""A stand-in for yt-dlp, and the only reason the demo needs no network.

It answers the two questions Viddl asks a real engine:

* ``--version``, so the app knows which copy it is using;
* ``--dump-single-json``, so the qualities screen has something to list.

and then it "downloads": it writes the file the app asked for, a step at a
time, using the same ``VIDDLPROGRESS`` line a real yt-dlp produces through the
template Viddl passes it. Every number here is made up; the screens that show
them are the real ones.

Run it by hand to see what the demo would see::

    python docs/demo/fake_ytdlp.py --dump-single-json -- https://example.test/v

It is deliberately an ordinary script with no dependencies, because the
recording runs it exactly the way it would run the real thing.
"""

from __future__ import annotations

import json
import os
import sys
import time

VERSION = "2026.10.24"

TITLE = "Look at this cat"
VIDEO_ID = "dQw4w9WgXcQ"
UPLOADER = "A Channel"
DURATION = 212
PAGE = "https://www.youtube.com/watch?v=" + VIDEO_ID

INFO = {
    "id": VIDEO_ID,
    "title": TITLE,
    "uploader": UPLOADER,
    "duration": DURATION,
    "webpage_url": PAGE,
    "extractor_key": "Youtube",
    "formats": [
        # The audio stream on its own (F4).
        {
            "format_id": "140",
            "ext": "m4a",
            "audio_ext": "m4a",
            "vcodec": "none",
            "acodec": "mp4a",
            "abr": 128,
            "filesize": 1_600_000,
        },
        # A video-only stream: choosing it means ffmpeg and a merge, which is
        # why the demo picks the row below instead.
        {
            "format_id": "137",
            "ext": "mp4",
            "vcodec": "avc1",
            "acodec": "none",
            "height": 1080,
            "fps": 30,
            "vbr": 5000,
            "filesize": 20_000_000,
        },
        # One file with both, so 720p downloads without touching ffmpeg.
        {
            "format_id": "22",
            "ext": "mp4",
            "vcodec": "avc1",
            "acodec": "mp4a",
            "height": 720,
            "tbr": 2400,
            "filesize": 12_400_000,
        },
        {
            "format_id": "18",
            "ext": "mp4",
            "vcodec": "avc1",
            "acodec": "mp4a",
            "height": 360,
            "tbr": 700,
            "filesize": 6_900_000,
        },
    ],
}

#: How the download runs: (seconds to wait, percent done). Ten steps give the
#: bar something to move across without the demo feeling slow.
STEPS = [(0.28, percent) for percent in range(10, 101, 10)]


def _value_after(argv, flag: str, fallback: str = "") -> str:
    """The argument following ``flag``, or ``fallback``."""
    try:
        return argv[argv.index(flag) + 1]
    except (ValueError, IndexError):
        return fallback


def download(argv) -> int:
    """Write the file the app asked for, a tenth at a time."""
    work = _value_after(argv, "-P", ".")
    template = _value_after(argv, "-o", "%(title)s [%(id)s].%(ext)s")
    name = (
        template.replace("%(title)s", TITLE)
        .replace("%(id)s", VIDEO_ID)
        .replace("%(ext)s", "mp4")
    )
    total = 12_400_000
    target = os.path.join(work, name)
    os.makedirs(work, exist_ok=True)
    print(f"[download] Destination: {target}", flush=True)
    # A tiny real file: the size on the done screen is the file's own, so it
    # has to be this big for the screen to say what a 720p download says.
    with open(target, "wb") as handle:
        handle.truncate(total)
    done = 0
    for wait, percent in STEPS:
        time.sleep(wait)
        done = total * percent // 100
        left = max(1, int((100 - percent) * 0.05))
        print(
            "VIDDLPROGRESS|"
            f"{percent}.0|2500000|{left}|{done}|{total}|NA|{target}",
            flush=True,
        )
    print(f"[download] 100% of {total} in 00:05", flush=True)
    return 0


def main(argv) -> int:
    args = list(argv)
    if "--version" in args:
        print(VERSION)
        return 0
    if "--dump-single-json" in args:
        print(json.dumps(INFO))
        return 0
    return download(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
