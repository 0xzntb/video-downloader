"""Command line entry point.

The normal way to use Viddl is to run ``viddl`` and paste a link, but every
piece is also reachable from the command line:

* ``viddl <link>``            open the app with the link already loaded
* ``viddl --audio <link>``    open it on the MP3 options
* ``viddl --uri viddl://…``   what the browser extension calls
* ``viddl --doctor``          show where everything lives and whether it works
* ``viddl --register-protocol``          wire up the ``viddl://`` hand-off
* ``viddl --install-browser-extension``  copy the extension out for loading
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Optional, Sequence

from . import APP_TITLE, __version__
from . import appdata, logfile
from .engine import Engine, ToolError
from .formats import format_rows
from . import protocol
from .protocol import (
    extension_source_dir,
    install_extension,
    parse_scheme,
    register,
    unregister,
)
from .util import safe_video_link

_DESCRIPTION = "Download videos and MP3s by pasting a link."


def _enable_log_file() -> None:
    """Keep a small log next to the app's own files.

    Nothing here is required for Viddl to work, but when something does go
    wrong on a machine we cannot see, ``viddl --doctor`` prints this path and
    the last run is already written down. ``logfile`` owns what a line may
    contain and how large the file is allowed to grow.
    """
    appdata.ensure_dirs()
    logfile.enable(logging.getLogger(logfile.LOGGER_NAME))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="viddl",
        description=_DESCRIPTION,
        epilog="Works with YouTube, X, Instagram, Threads, TikTok and plenty more.",
    )
    parser.add_argument(
        "target",
        nargs="?",
        help="a video link to open straight away (or a viddl:// link)",
    )
    parser.add_argument(
        "--uri",
        metavar="URL",
        help="a viddl:// link handed over by the browser extension",
    )
    parser.add_argument(
        "--audio",
        action="store_true",
        help="start on the MP3 options",
    )
    parser.add_argument(
        "--list-qualities",
        metavar="URL",
        help="print the available qualities for a link and exit",
    )
    parser.add_argument(
        "--download-dir",
        metavar="DIR",
        help="save downloads here instead of your Downloads folder",
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="show where Viddl keeps things and whether the tools are ready",
    )
    parser.add_argument(
        "--register-protocol",
        action="store_true",
        help="make viddl:// links open the app (used by the browser extension)",
    )
    parser.add_argument(
        "--unregister-protocol",
        action="store_true",
        help="stop handling viddl:// links",
    )
    parser.add_argument(
        "--install-browser-extension",
        action="store_true",
        help="copy the browser extension to a folder you can load in Chrome",
    )
    parser.add_argument(
        "--extension-path",
        action="store_true",
        help="print the folder holding the bundled browser extension",
    )
    parser.add_argument("--version", action="version", version=f"{APP_TITLE} {__version__}")
    return parser


def _fail(message: str, hint: Optional[str] = None) -> int:
    print(f"{APP_TITLE}: {message}", file=sys.stderr)
    if hint:
        for line in str(hint).splitlines():
            print(f"  {line}", file=sys.stderr)
    return 1


def _print_result(result) -> int:
    stream = sys.stdout if result.ok else sys.stderr
    print(result.message, file=stream)
    for step in result.steps:
        print(f"  · {step}", file=stream)
    return 0 if result.ok else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    _enable_log_file()

    if args.download_dir:
        os.environ["VIDDL_DOWNLOAD_DIR"] = str(Path(args.download_dir).expanduser())

    if args.doctor:
        for line in Engine().report():
            print(line)
        return 0

    if args.register_protocol:
        return _print_result(register())
    if args.unregister_protocol:
        return _print_result(unregister())

    if args.extension_path:
        print(extension_source_dir())
        return 0

    if args.install_browser_extension:
        try:
            folder = install_extension()
        except OSError as exc:
            return _fail(f"Could not copy the extension ({exc}).")
        print(f"Extension copied to:\n  {folder}\n")
        print("Load it once in your browser:")
        print("  1. Open chrome://extensions (or edge://extensions, brave://extensions)")
        print("  2. Turn on Developer mode (top right)")
        print("  3. Click “Load unpacked” and choose the folder above")
        print("\nThen run: viddl --register-protocol")
        return 0

    if args.list_qualities:
        url = safe_video_link(args.list_qualities)
        if url is None:
            return _fail("That does not look like a link.")
        from .download import probe_video

        try:
            info = probe_video(Engine(), url)
        except ToolError as exc:
            return _fail(exc.message, exc.hint)
        print(info.heading)
        if info.uploader:
            print(info.uploader)
        print()
        for row in format_rows(info.options):
            print(row)
        return 0

    # Anything else opens the app.
    url = ""
    audio = bool(args.audio)

    raw = args.uri or args.target or ""
    if raw.lower().startswith("viddl:"):
        # The one path a web page can start, which is why it is the one path
        # that is rate limited: a hand-off arriving hard on the heels of another
        # is taken to be the same ask, and the app opens with no link rather
        # than acting on a second one. It neither closes the window nor pretends
        # to — only the desktop environment can refuse to open those — so what
        # it limits is how many links a page can have acted on, not how many
        # windows appear. A link typed on the command line is never limited this
        # way: that intent can only come from the person typing it.
        request = parse_scheme(raw)
        if request is None:
            return _fail(
                "That viddl:// link could not be understood.",
                "Open the app with no arguments and paste the link instead.",
            )
        if protocol.handoff_too_soon():
            url = ""
        else:
            protocol.remember_handoff()
            url = request.url
            audio = audio or request.audio
    else:
        url = raw.strip()
        if url:
            # Only a plain http(s) link goes through: anything option-shaped
            # would be read as a flag by the downloader we hand it to.
            link = safe_video_link(url)
            if link is None:
                return _fail(
                    "That does not look like a video link.",
                    "Example: viddl https://www.youtube.com/watch?v=…",
                )
            url = link

    from .tui.app import run

    try:
        run(url=url, audio=audio)
    except KeyboardInterrupt:  # pragma: no cover - interactive
        return 130
    except OSError as exc:
        return _fail(
            "Viddl needs a real terminal window to run.",
            f"Start it from your terminal app rather than by double-clicking. ({exc})",
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
