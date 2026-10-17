"""Command line entry point.

The normal way to use Viddl is to run ``viddl`` and paste a link, but every
piece is also reachable from the command line:

* ``viddl <link>``            open the app with the link already loaded
* ``viddl --audio <link>``    open it on the MP3 options
* ``viddl --uri viddl://…``   what the browser extension calls
* ``viddl --doctor``          show where everything lives and whether it works
* ``viddl --update``          fetch the newest video engine (yt-dlp)
* ``viddl --update-ffmpeg``   fetch the newest ffmpeg build
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
from .engine import Engine, Tool, ToolError
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
        "--update",
        action="store_true",
        help="fetch the newest video engine (this is the fix for a site that stopped working)",
    )
    parser.add_argument(
        "--update-ffmpeg",
        action="store_true",
        help="fetch the newest ffmpeg build",
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


def _describe_update(name: str, before: Optional[Tool], after: Tool) -> str:
    """What changed, in one line — or that nothing did."""
    new_version = after.version or "ok"
    if before is None:
        return f"Installed {name} {new_version} ({after.origin_text})."
    if before.version and before.version == after.version:
        return f"{name} {new_version} — already the newest release."
    return f"{name} {before.version or 'an older copy'} → {new_version} ({after.origin_text})."


def _engine_in_use(engine: Engine) -> str:
    """Which copy Viddl will actually run, when it is not the one just fetched.

    Updating a copy nobody will run is worth saying out loud, and so is which
    one wins — a newer one elsewhere, or one the environment named, older or not.
    """
    tool = engine.find_ytdlp()
    if tool is None or tool.origin == "viddl":
        return ""
    if tool.origin == "environment":
        return (
            f"Viddl will still run the copy you pointed it at ({tool.version}, "
            "VIDDL_YTDLP). Unset that to use the one just fetched."
        )
    return (
        f"Viddl will run the copy {tool.origin_text} ({tool.version}), because it is "
        "newer. Set VIDDL_YTDLP if you want a particular one."
    )


def _update(engine: Engine, want_engine: bool, want_ffmpeg: bool) -> int:
    """Fetch the newest engine, saying what changed rather than that it is done.

    Each flag does only what it says: ``--update-ffmpeg`` is the answer to "my
    merge fails", not to "my links stopped working".
    """
    def status(message: str) -> None:
        print(f"  {message}", file=sys.stderr)

    seen = {"percent": -1}

    def progress(done: int, total: Optional[int]) -> None:
        if not total:
            return
        percent = done * 100 // total
        if percent == seen["percent"]:
            return  # one line per per cent, not per chunk
        seen["percent"] = percent
        print(f"\r  {percent}%", end="", file=sys.stderr, flush=True)

    try:
        # The running commentary goes to stderr and the outcome to stdout, so
        # that `viddl --update > log` records the versions and not the chatter.
        if want_engine:
            print("Updating yt-dlp…", file=sys.stderr)
            before, after = engine.update_ytdlp(on_status=status, on_progress=progress)
            print(file=sys.stderr)
            print(_describe_update("yt-dlp", before, after))
            note = _engine_in_use(engine)
            if note:
                print(note)
        if want_ffmpeg:
            print("Updating ffmpeg…", file=sys.stderr)
            old, new = engine.update_ffmpeg(on_status=status, on_progress=progress)
            print(file=sys.stderr)
            print(_describe_update("ffmpeg", old, new))
    except ToolError as exc:
        print(file=sys.stderr)
        return _fail(exc.message, exc.hint)
    return 0


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

    if args.update or args.update_ffmpeg:
        return _update(
            Engine(), want_engine=bool(args.update), want_ffmpeg=bool(args.update_ffmpeg)
        )

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
