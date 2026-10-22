"""Command line entry point.

The normal way to use Viddl is to run ``viddl`` and paste a link, but every
piece is also reachable from the command line:

* ``viddl <link>``            open the app with the link already loaded
* ``viddl --audio <link>``    open it on the audio options
* ``viddl --uri viddl://…``   what the browser extension calls  * ``viddl --doctor``          show where everything lives and whether it works
  * ``viddl --json --list-qualities <link>``  the same answers, for a script (F10)

* ``viddl --update``          fetch the newest video engine (yt-dlp)
* ``viddl --update-ffmpeg``   fetch the newest ffmpeg build
* ``viddl --extras subs <link>``  also add subtitles, details or a cover image
* ``viddl --download-archive <link>``  skip videos already downloaded (F6)
* ``viddl --limit-rate 2M <link>``  a speed limit, a proxy, fewer pieces (F11)
* ``viddl --plain <link>``    download a line at a time, in plain ASCII
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
from . import appdata, cookies as cookies_mod, logfile
from .engine import Engine, Tool, ToolError, staleness_advice
from .extras import Extras
from .formats import format_rows
from .network import NetworkOptions
from . import protocol
from .protocol import (
    extension_source_dir,
    install_extension,
    parse_scheme,
    register,
    unregister,
)
from .util import safe_video_link

_DESCRIPTION = "Download videos and audio by pasting a link."


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
        help=(
            "start on the audio options: the site's own file, or MP3, M4A, "
            "Opus, FLAC or WAV"
        ),
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
        "--plain",
        action="store_true",
        help=(
            "download a line at a time instead of opening the full-screen app "
            "(plain ASCII, for scripts and older consoles)"
        ),
    )
    parser.add_argument(
        "--quality",
        metavar="QUALITY",
        help=(
            "with --plain: a height (720, 1080), best, or an audio format — "
            "audio, mp3, m4a, opus, flac, wav, or a preset such as mp3-192"
        ),
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="with --plain: download without asking first (for scripts)",
    )
    parser.add_argument(
        "--extras",
        metavar="LIST",
        help=(
            "also add subtitles, details (title, channel, date and chapters) "
            "or a cover image: subs, details, cover, or all"
        ),
    )
    parser.add_argument(
        "--playlist",
        action="store_true",
        help=(
            "with --plain: download every video of a playlist, one after "
            "another, instead of only the first"
        ),
    )
    parser.add_argument(
        "--cookies-from-browser",
        metavar="BROWSER",
        help=(
            "let the download use the cookies your browser already has, for a "
            "site that asks you to sign in: chrome, firefox, edge, brave, "
            "safari, or auto for whichever is installed"
        ),
    )
    parser.add_argument(
        "--download-archive",
        action="store_true",
        help=(
            "remember what has been downloaded (in Viddl's own folder) and skip "
            "videos you already have; also makes re-running a playlist take "
            "only the videos it has not fetched yet"
        ),
    )
    parser.add_argument(
        "--limit-rate",
        metavar="SPEED",
        help=(
            "download no faster than this, so other things on the connection "
            "still work: 500K, 2M, or a number of bytes per second"
        ),
    )
    parser.add_argument(
        "--proxy",
        metavar="ADDRESS",
        help=(
            "send downloads through a proxy: http://host:port, "
            "socks5://host:port, or with an address that needs a sign-in"
        ),
    )
    parser.add_argument(
        "--concurrent-fragments",
        metavar="N",
        help=(
            "how many pieces of one video to fetch at once, 1 to 32 "
            "(default 4); lower it when a site or a proxy complains"
        ),
    )
    parser.add_argument(
        "--throttled-rate",
        metavar="SPEED",
        help=(
            "give up on a source that stays slower than this and try another: "
            "100K, 1M"
        ),
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="show where Viddl keeps things and whether the tools are ready",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help=(
            "with --doctor or --list-qualities: print one JSON object instead of "
            "sentences, for scripts"
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help=(
            "with --plain: print only the saved path — progress and notes are "
            "left out, failures still go to stderr"
        ),
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


def _prepare_console() -> None:
    """Never let a console's encoding turn a message into a crash (R10).

    A Windows terminal redirected to a file uses the machine's legacy code
    page, and one character outside it would otherwise raise
    ``UnicodeEncodeError`` from the middle of ``--doctor``. Replacing the
    character is worse than printing it and much better than no output at all;
    ``--plain`` goes further and only produces ASCII to begin with.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="backslashreplace")
        except (OSError, ValueError):  # pragma: no cover - a closed or exotic stream
            pass


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
    return f"{name} {before.version or 'an older copy'} -> {new_version} ({after.origin_text})."


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
    _prepare_console()
    _enable_log_file()

    if not args.plain and (args.quality or args.yes or args.playlist or args.quiet):
        hint = "Try: viddl --plain --quality 720 <link>"
        if args.playlist and not (args.quality or args.yes):
            hint = "Try: viddl --plain --playlist <link>"
        if args.quiet and not (args.quality or args.yes or args.playlist):
            hint = "Try: viddl --plain --yes --quiet <link>"
        return _fail(
            "--quality, --yes, --playlist and --quiet only work with --plain.",
            hint,
        )

    # --json is a shape for two read-only answers, not a download mode: the
    # download path already has --quiet for scripts, and progress as JSON lines
    # would be a second format to keep in step with the first (F10).
    if args.json_output and not (args.doctor or args.list_qualities):
        return _fail(
            "--json works with --doctor or --list-qualities.",
            "For a scripted download, use: viddl --plain --yes --quiet <link>",
        )

    # What to add besides the media (F5). Read here rather than in the two
    # download paths, so a typo gets one answer and not two different ones.
    extras = Extras()
    if args.extras:
        try:
            extras = Extras.parse(args.extras)
        except ValueError as exc:
            return _fail(str(exc), "For example: viddl --extras subs,cover <link>")

    # How these downloads use the network (F11). Checked once, here, so that a
    # value that is not a speed or not a proxy is refused with a sentence
    # rather than reaching an engine's command line and being read as an option
    # of its own.
    try:
        network = NetworkOptions.parse(
            limit_rate=args.limit_rate or "",
            proxy=args.proxy or "",
            concurrent_fragments=args.concurrent_fragments,
            throttled_rate=args.throttled_rate or "",
        )
    except ValueError as exc:
        return _fail(
            str(exc),
            "For example: viddl --limit-rate 2M --proxy "
            "socks5://127.0.0.1:1080 <link>",
        )

    if args.download_dir:
        os.environ["VIDDL_DOWNLOAD_DIR"] = str(Path(args.download_dir).expanduser())

    # The download archive is switched on for the whole session the same way
    # the download folder is: both are read where a job is built, deep in the
    # stack, and neither is worth a parameter threaded through every screen
    # (F6). The variable is checked like every other one, so a value that is
    # not a yes or no is refused in ``--doctor`` rather than guessed at.
    if args.download_archive:
        os.environ[appdata.ENV_ARCHIVE] = "1"

    # One browser name, checked before it reaches a command line (F1). What
    # comes back is a key from the closed list, or nothing at all.
    cookies = ""
    if args.cookies_from_browser:
        problem = cookies_mod.problem_with(args.cookies_from_browser)
        if problem:
            return _fail(problem, cookies_mod.hint_for(args.cookies_from_browser))
        cookies = cookies_mod.default_for(args.cookies_from_browser)
        if not cookies:
            return _fail(
                "No browser was found to read cookies from.",
                "Sign in to the site in a browser first, then name it: "
                "--cookies-from-browser firefox.",
            )

    if args.doctor:
        if args.json_output:
            # One object, the same facts, for a script (F10). Built from the
            # places the sentences are built from, not from parsing them.
            from . import machine

            print(machine.dumps(machine.doctor_report()), end="")
            return 0
        # The report says which overrides are set, what Viddl is actually
        # waiting for whether or not one is (R11), what it remembers between
        # runs (F6), and which browsers it could read a sign-in from (F1).
        for line in [
            *Engine().report(),
            appdata.archive_note(),
            appdata.recent_note(),
            appdata.timeout_note(),
            cookies_mod.doctor_note(),
        ]:
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

        engine = Engine()
        try:
            info = probe_video(engine, url, cookies=cookies)
        except ToolError as exc:
            hint = exc.hint or ""
            # "It broke, make it work again" starts with knowing what to run,
            # and the engine's age is the usual reason a link stopped working
            # (F3). Nothing is said while the engine is current.
            tool = engine.find_ytdlp()
            advice = staleness_advice(tool.version) if tool and tool.version else ""
            if advice:
                hint = f"{hint}\n{advice}" if hint else advice
            return _fail(exc.message, hint)
        if args.json_output:
            from . import machine

            print(machine.dumps(machine.qualities_report(info)), end="")
            return 0
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
    # What remembers what has already been downloaded, when the run asked for
    # one (F6). Both front doors get the same answer, so the app and --plain
    # agree about what "already have this" means.
    archive = appdata.archive_path() if appdata.archive_enabled() else None

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

    if args.plain:
        if not url:
            return _fail(
                "--plain needs a link.",
                "Example: viddl --plain --yes https://www.youtube.com/watch?v=…",
            )
        from .plain import run_plain

        try:
            return run_plain(
                url,
                audio=audio,
                quality=args.quality or "",
                assume_yes=bool(args.yes),
                cookies=cookies,
                playlist=bool(args.playlist),
                extras=extras,
                archive=archive,
                network=network,
                # Only the path, for a script (F10). Failures still speak.
                quiet=bool(args.quiet),
            )
        except KeyboardInterrupt:  # pragma: no cover - interactive
            return 130

    from .tui.app import run

    try:
        run(
            url=url,
            audio=audio,
            cookies=cookies,
            extras=extras,
            archive=archive,
            network=network,
        )
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
