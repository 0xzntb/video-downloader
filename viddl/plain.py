"""A line-oriented, ASCII-only way to download (R10).

The full-screen app needs a terminal that can draw a box and print Unicode. A
legacy Windows console cannot, and neither can a log file, a screen reader or
some SSH sessions. ``viddl --plain`` is the way out: one line at a time, every
piece of Viddl's own text reduced to ASCII, progress overwritten in place, and
the saved path alone on standard output — which is what a script wants.

The site's own words — a title, an uploader — are reduced too. That is the
point: a console that cannot encode ``é`` must not turn a title into a
traceback. A path is written as it is, because mangling one would make the
answer useless.
"""

from __future__ import annotations

import sys
import unicodedata
from pathlib import Path
from typing import Callable, Optional, TextIO

from . import appdata
from . import cookies as cookies_mod
from . import engine as engine_mod
from .download import (
    DownloadCancelled,
    DownloadJob,
    ProbeCancelled,
    Progress,
    probe_playlist,
    probe_video,
)
from .engine import Engine, ToolError
from .extras import Extras
from .formats import AUDIO_FORMATS, ORIGINAL_AUDIO_KEY, MediaInfo, MediaOption
from .queue import QueueJob
from .util import human_duration, human_size

#: Typography Viddl uses in its own sentences, mapped to what a console with a
#: legacy code page can print: a plain one shows ``·`` and ``…`` as boxes or
#: question marks, and ``→`` is not in cp1252 at all.
_TO_ASCII = str.maketrans(
    {
        "·": "-",
        "•": "*",
        "…": "...",
        "—": "-",
        "–": "-",
        "\u201c": '"',
        "\u201d": '"',
        "\u2018": "'",
        "\u2019": "'",
        "→": "->",
        "←": "<-",
        "✓": "[ok]",
        "✗": "[x]",
        "\u00a0": " ",
    }
)

#: What ``--quality`` accepts for "the sound, not the picture" (F4). Where
#: ``--audio`` used to mean "the best MP3", it now means the best sound there
#: is: the site's own file, which needs no encoder and loses nothing. ``mp3``
#: still means MP3, and every format name works on its own too.
_AUDIO_WORDS = ("audio", "a")

#: Spelled-out names for the row that is not a conversion at all.
_AUDIO_ALIASES = {
    "original": ORIGINAL_AUDIO_KEY,
    "orig": ORIGINAL_AUDIO_KEY,
    "no-reencode": ORIGINAL_AUDIO_KEY,
    "no_reencode": ORIGINAL_AUDIO_KEY,
}

#: The format names that can follow ``--quality``, plus the preset keys built
#: from them (``mp3-192``): the first half of a hyphenated word is enough to
#: know the ask was about sound.
_AUDIO_FORMAT_WORDS = tuple(audio_format.key for audio_format in AUDIO_FORMATS)


def ascii_text(value: object) -> str:
    """``value`` as text any console can print.

    Two steps, because they fix different things. The table above keeps the
    *meaning* of punctuation Viddl uses (``“Stop looking”`` still reads as a
    quotation), and NFKD folds accented letters to their base so ``café`` stays
    readable as ``cafe``. Whatever is left — Chinese, emoji — becomes ``?``:
    losing a character is better than a ``UnicodeEncodeError`` on a console
    that cannot print it, and the plain path exists for exactly that console.
    """
    text = str(value).translate(_TO_ASCII)
    folded = "".join(
        character
        for character in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(character)
    )
    return folded.encode("ascii", "replace").decode("ascii")


def _say(stream: TextIO, text: object = "") -> None:
    stream.write(ascii_text(text) + "\n")
    stream.flush()


class _Line:
    """One line of progress, overwritten in place.

    A shorter update has to erase the tail of the longer one before it, or the
    screen shows the two glued together — and when the download ends the line
    is cleared so the sentence that follows starts at the left edge.
    """

    def __init__(self, stream: TextIO) -> None:
        self.stream = stream
        self.width = 0

    def show(self, text: str) -> None:
        # The status a merge or a retry reports is Viddl's own sentence, so it
        # goes through the same reduction as everything else: "Merging video and
        # audio…" must not put an ellipsis on a legacy console.
        text = ascii_text(text)
        self.stream.write("\r" + text.ljust(max(self.width, len(text))))
        self.stream.flush()
        self.width = len(text)

    def clear(self) -> None:
        if self.width:
            self.stream.write("\r" + " " * self.width + "\r")
            self.stream.flush()
            self.width = 0


def _engine_advice(engine: Engine) -> str:
    """The "run --update" sentence, when the engine is old enough to be the cause.

    Asked only when something has already failed, which is why the cost of
    finding out which copy is running — a version probe — is paid here and
    nowhere else. A failure with a current engine gets no advice, because none
    of it would be true.
    """
    try:
        tool = engine.find_ytdlp()
    except Exception:  # pragma: no cover - advice must never become the error
        return ""
    if tool is None or not tool.version:
        return ""
    return engine_mod.staleness_advice(tool.version)


def _explain_failure(err: TextIO, exc: ToolError, engine: Engine) -> None:
    """``error: …``, its hint, and the update advice when it applies (F3)."""
    _say(err, f"error: {exc.message}")
    if exc.hint:
        _say(err, f"  {exc.hint}")
    advice = _engine_advice(engine)
    if advice:
        _say(err, f"  {advice}")


def _is_interactive(stream: TextIO) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, OSError):  # pragma: no cover - unusual streams
        return False


def _audio_row(options: list, wanted: str) -> Optional[MediaOption]:
    """The audio row ``wanted`` names, or ``None`` if the link has no such row.

    An exact key wins (``mp3-192``), then a format name (``flac``), then a
    format's best preset (``mp3`` is 320 because the rows are best-first). The
    original row is named by its own format, so on a link whose sound already
    *is* m4a, ``m4a`` gives the untouched file rather than an AAC re-encode of
    it — which is both the better answer and the cheaper one (F4).
    """
    audio = [option for option in options if option.kind == "audio"]
    if wanted in _AUDIO_WORDS:
        # "audio" is not a format: it is whichever row is the best sound there
        # is, and the menu is ordered best-first.
        return audio[0] if audio else None
    key = _AUDIO_ALIASES.get(wanted, wanted)
    for option in audio:
        if option.key == key:
            return option
    for option in audio:
        if option.audio_format.lower() == key:
            return option
    return None


def _asked_for_audio(options: list, wanted: str) -> bool:
    """True when ``wanted`` is a request for sound rather than a height."""
    if wanted in _AUDIO_WORDS or wanted in _AUDIO_ALIASES:
        return True
    if wanted.split("-")[0] in _AUDIO_FORMAT_WORDS:
        return True
    return any(
        option.kind == "audio" and option.audio_format.lower() == wanted
        for option in options
    )


def choose_option(
    info: MediaInfo, quality: str = "", audio: bool = False
) -> MediaOption:
    """The row ``--quality`` asks for, or the best one there is.

    Heights are matched the way a person means them: ``720`` takes 720p when it
    exists, the next one down when it does not (a 900 request on a 1080/720/480
    menu gets 720), and the smallest available when the link has nothing that
    low. ``audio`` — the flag or the word — takes the best sound the link has,
    which is the site's own file; a format name (``mp3``, ``flac``) or a preset
    (``mp3-192``) takes exactly that (F4).
    """
    options = info.options
    if not options:
        raise ToolError(
            "No downloadable qualities were found for this link.",
            "It may be a live stream, a page rather than a video, or a site "
            "that needs you to be signed in.",
        )

    wanted = (quality or "").strip().lower()
    if audio and not wanted:
        wanted = "audio"

    if wanted in ("", "best", "any"):
        return options[0]

    if _asked_for_audio(options, wanted):
        chosen = _audio_row(options, wanted)
        if chosen is not None:
            return chosen
        if any(option.kind == "audio" for option in options):
            raise ToolError(
                f"This link has no '{quality}' audio option.",
                "Use 'audio' for the best sound it has, or one of: "
                "mp3, m4a, opus, flac or wav (mp3-192 picks one bitrate).",
            )
        raise ToolError(
            "This link does not offer an audio-only download.",
            "Pick a video quality instead.",
        )

    digits = wanted[:-1] if wanted.endswith("p") else wanted
    if not digits.isdigit():
        raise ToolError(
            f"Unknown quality '{quality}'.",
            "Use a height such as 720 or 1080, 'best', or an audio format: "
            "audio, mp3, m4a, opus, flac or wav.",
        )

    height = int(digits)
    videos = [option for option in options if option.kind == "video"]
    if not videos:
        return options[0]

    # ``height`` is optional on a MediaOption: a handful of extractors hand back
    # a single ready-made file, and "Best available" is the honest name for it.
    measured = [option for option in videos if option.height is not None]
    exact = [option for option in measured if option.height == height]
    if exact:
        return exact[0]
    below = [option for option in measured if (option.height or 0) < height]
    if below:
        return max(below, key=lambda option: option.height or 0)
    if measured:
        return min(measured, key=lambda option: option.height or 0)
    return videos[0]


def _menu(info: MediaInfo, chosen: MediaOption, stream: TextIO) -> None:
    """The link's title and rows, written before anything is downloaded."""
    _say(stream, info.title or "Untitled video")
    if info.uploader:
        _say(stream, info.uploader)
    if info.duration:
        _say(stream, f"Length: {human_duration(info.duration)}")
    if info.playlist_note:
        _say(stream, info.playlist_note)
    _say(stream)
    for number, option in enumerate(info.options, start=1):
        marker = ">" if option is chosen else " "
        detail = f"  ({option.detail})" if option.detail else ""
        _say(stream, f" {marker} {number}. {option.label}  {option.size_text}{detail}")
    _say(stream)


def _progress_line(progress: Progress) -> str:
    """One line describing where the download is.

    A retry or a merge replaces the numbers: "42% 1.2 MB/s" says nothing while
    yt-dlp is waiting four seconds or running ffmpeg, and the status is the one
    thing that explains the pause.
    """
    if progress.phase == "retry":
        return progress.status or "Waiting to try again..."
    if progress.phase not in ("download", "done"):
        return progress.status or "Working..."
    bits = []
    if progress.percent is not None:
        bits.append(f"{progress.percent:3.0f}%")
    if progress.downloaded:
        if progress.total:
            bits.append(
                f"{human_size(progress.downloaded)} of {human_size(progress.total)}"
            )
        else:
            bits.append(human_size(progress.downloaded))
    if progress.speed:
        bits.append(progress.speed)
    if progress.eta:
        bits.append(f"{progress.eta} left")
    if not bits:
        return progress.status or "Working..."
    return "  ".join(bits)


def _size_hint(engine: Engine) -> str:
    """`` (about 149.2 MB)``, or ``""`` when the mirror did not say."""
    try:
        size = engine.ffmpeg_size_hint()
    except Exception:  # pragma: no cover - a nicety, never a failure
        size = None
    return f" (about {human_size(size)})" if size else ""


def _set_up_ffmpeg(
    engine: Engine,
    chosen: MediaOption,
    err: TextIO,
    stdin: TextIO,
    assume_yes: bool,
) -> Optional[int]:
    """Fetch ffmpeg when the chosen quality needs it. ``None`` means carry on.

    The app asks before a download this size, so the plain path does too: it
    says what the quality needs and how big the one-time fetch is, and --yes is
    the way a script agrees in advance. Without a terminal to ask on, it
    refuses rather than opening a 150 MB download nobody agreed to.
    """
    about = _size_hint(engine)
    if chosen.kind == "audio":
        # A conversion, not a join: ffmpeg is what writes the new container, and
        # saying "join" here would describe a different job (F4).
        need = f"{chosen.label} needs ffmpeg to write the {chosen.audio_format} file."
    else:
        need = f"{chosen.label} needs ffmpeg to join the video and audio streams."
    _say(err, need)

    if not assume_yes:
        if not _is_interactive(stdin):
            _say(err, "error: ffmpeg is not set up and there is no terminal to ask on.")
            _say(
                err,
                f"  Pass --yes to fetch it{about} as part of this download, "
                "or run `viddl --update-ffmpeg` first.",
            )
            return 1
        err.write(ascii_text(f"Fetch ffmpeg now{about}? [y/N] "))
        err.flush()
        if stdin.readline().strip().lower() not in ("y", "yes"):
            _say(err, "Nothing was downloaded.")
            return 0

    _say(err, f"Fetching ffmpeg{about} - this is a one-time download...")
    line = _Line(err)

    def on_progress(done: int, total: Optional[int]) -> None:
        if total:
            line.show(f"{done * 100 // total}%  {human_size(done)} of {human_size(total)}")
        else:
            line.show(human_size(done))

    try:
        tool = engine.ensure_ffmpeg(
            on_status=lambda message: _say(err, message), on_progress=on_progress
        )
    except ToolError as exc:
        line.clear()
        _say(err, f"error: {exc.message}")
        if exc.hint:
            _say(err, f"  {exc.hint}")
        return 1
    line.clear()
    _say(err, f"ffmpeg is ready ({tool.version or 'ok'}).")
    return None


def _run_playlist_plain(
    engine: Engine,
    info: MediaInfo,
    chosen: MediaOption,
    *,
    playlist_url: str,
    cookies: str,
    extras: Extras,
    archive: Optional[Path],
    assume_yes: bool,
    make_job: Callable[..., DownloadJob],
    downloads_dir: Callable[[], Path],
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Download every video of a playlist, one at a time (F2).

    One line per video, the same progress line as a single download, and the
    path of each saved file on stdout — so ``viddl --plain --playlist --yes``
    gives a script a list it can act on, in the order the site keeps them.
    """
    _say(stderr, "Looking up the videos in the playlist...")
    try:
        listing = probe_playlist(engine, playlist_url, cookies=cookies)
    except ProbeCancelled:
        _say(stderr, "Stopped before the playlist was read.")
        return 130
    except ToolError as exc:
        _explain_failure(stderr, exc, engine)
        return 1

    entries = listing.entries
    _say(stderr, listing.heading)
    if listing.truncated:
        # Saying so matters: a script that expects 900 files and gets 200 should
        # be able to find out why without reading the source.
        _say(
            stderr,
            f"  Viddl lists the first {len(entries)} videos of a playlist this "
            "long. Split it into shorter playlists to take the rest.",
        )
    _say(stderr, f"Every video will be downloaded at {chosen.label}.")
    if extras and extras.applicable(chosen):
        _say(stderr, f"Extras: {extras.summary(chosen)}.")

    needs_ffmpeg = chosen.needs_ffmpeg or extras.needs_ffmpeg_for(chosen)
    if needs_ffmpeg and engine.find_ffmpeg() is None:
        answer = _set_up_ffmpeg(engine, chosen, stderr, stdin, assume_yes)
        if answer is not None:
            return answer

    if not assume_yes:
        if not _is_interactive(stdin):
            _say(stderr, "error: Viddl will not download without being asked.")
            _say(stderr, "  Pass --yes to download without a question, for scripts.")
            return 1
        question = f"Download all {len(entries)} videos at {chosen.label}? [y/N] "
        stderr.write(ascii_text(question))
        stderr.flush()
        if stdin.readline().strip().lower() not in ("y", "yes"):
            _say(stderr, "Nothing was downloaded.")
            return 0

    line = _Line(stderr)
    # One piece of engine advice per run, not one per failed video: a playlist
    # of forty with a broken extractor must not print the same sentence forty
    # times.
    advised = {"done": False}

    def on_start(item) -> None:
        _say(stderr, f"[{item.position}/{item.total}] {item.title}")

    def on_progress(item, progress: Progress) -> None:
        line.show(_progress_line(progress))

    def on_end(item) -> None:
        line.clear()
        if item.path is not None:
            try:
                size = human_size(Path(item.path).stat().st_size)
            except OSError:  # pragma: no cover - a file that vanished
                size = "?"
            # "Already had" for a file the download archive recognised (F6):
            # it is on disk either way, but a script's log should be able to
            # tell a fetch from a recall.
            word = "Already had" if getattr(item, "already_had", False) else "Saved"
            _say(stderr, f"{word} ({size})")
            for attachment in item.attachments:
                _say(stderr, f"Also saved: {Path(attachment).name}")
            # One path per line, and nothing else: this is what a script reads.
            stdout.write(f"{item.path}\n")
            stdout.flush()
        elif item.error is not None:
            if advised["done"]:
                _say(stderr, f"error: {item.error.message}")
                return
            _explain_failure(stderr, item.error, engine)
            advised["done"] = True

    job = QueueJob(
        engine,
        listing,
        entries,
        chosen,
        downloads_dir(),
        cookies=cookies,
        extras=extras,
        archive=archive,
        make_job=make_job,
        on_start=on_start,
        on_progress=on_progress,
        on_end=on_end,
    )
    try:
        result = job.run()
    except KeyboardInterrupt:
        job.cancel(force=True)
        line.clear()
        _say(stderr, "Stopped. Run the same command again to carry on from here.")
        return 130

    line.clear()
    _say(stderr, result.headline)
    if result.stopped:
        _say(stderr, "Run the same command again to carry on from here.")
        return 130
    return 0 if result.failed == 0 else 1


def run_plain(
    url: str,
    *,
    audio: bool = False,
    quality: str = "",
    assume_yes: bool = False,
    cookies: str = "",
    playlist: bool = False,
    extras: Optional[Extras] = None,
    archive: Optional[Path] = None,
    engine: Optional[Engine] = None,
    probe: Callable[..., MediaInfo] = probe_video,
    make_job: Callable[..., DownloadJob] = DownloadJob,
    downloads_dir: Callable[[], Path] = appdata.downloads_dir,
    stdin: Optional[TextIO] = None,
    stdout: Optional[TextIO] = None,
    stderr: Optional[TextIO] = None,
) -> int:
    """Download one link without the full-screen app. Returns an exit code.

    Everything a person reads goes to stderr; the saved path is the only thing
    on stdout, so ``path=$(viddl --plain --yes <link>)`` works. The exit codes
    match the rest of the CLI: 0 worked, 1 did not, 130 was stopped.

    ``cookies`` names a browser whose own cookie store yt-dlp may read for this
    one download (F1). The sentence saying so is printed before anything is
    fetched, because a script's log should record what was asked of the site.

    ``archive`` names a download archive to record in and skip from (F6):
    ``None`` — the default — is every run that did not ask for one, since
    skipping a download is a promise to keep, not a surprise to spring.
    """
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    stdin = stdin or sys.stdin
    engine = engine or Engine()
    extras = extras or Extras()

    if cookies:
        _say(err, cookies_mod.active_note(cookies_mod.label_for(cookies)))
    _say(err, f"Looking up {url}")
    try:
        info = probe(engine, url, cookies=cookies)
    except ToolError as exc:
        _explain_failure(err, exc, engine)
        return 1

    try:
        chosen = choose_option(info, quality=quality, audio=audio)
    except ToolError as exc:
        _say(err, f"error: {exc.message}")
        if exc.hint:
            _say(err, f"  {exc.hint}")
        return 1

    _menu(info, chosen, err)
    _say(err, f"Selected: {chosen.label}  {chosen.size_text}")
    if extras:
        # What else is going into the file is part of what was asked for, so
        # a log records it before anything is fetched (F5). "none" is not
        # printed: the line below already says what was left out and why.
        if extras.applicable(chosen):
            _say(err, f"Extras: {extras.summary(chosen)}.")
        skipped = extras.skipped(chosen)
        if skipped:
            _say(
                err,
                f"Skipped: {skipped[0].label.lower()} - an audio download has "
                "nowhere to put them.",
            )

    if info.playlist is not None:
        # A playlist link was read as its first video, which is the safe
        # default: one click must not start forty downloads (F2). Saying so
        # twice is deliberate — once above the menu, because the note is about
        # the link, and once here, because this is where the choice is made.
        if playlist:
            return _run_playlist_plain(
                engine,
                info,
                chosen,
                playlist_url=url,
                cookies=cookies,
                extras=extras,
                archive=archive,
                assume_yes=assume_yes,
                make_job=make_job,
                downloads_dir=downloads_dir,
                stdin=stdin,
                stdout=out,
                stderr=err,
            )
        _say(err, f"  Viddl will download the first of the {info.playlist.count} videos.")
        _say(err, "  Add --playlist to download all of them.")
    elif playlist:
        # --playlist on a single video is not an error: a script that always
        # passes it should keep working when a link happens not to be a list.
        _say(err, "This link is one video, not a playlist; downloading it.")

    needs_ffmpeg = chosen.needs_ffmpeg or extras.needs_ffmpeg_for(chosen)
    if needs_ffmpeg and engine.find_ffmpeg() is None:
        # ffmpeg is what joins the two streams; without it yt-dlp downloads both
        # and stops, and there is no finished video to report.
        answer = _set_up_ffmpeg(engine, chosen, err, stdin, assume_yes)
        if answer is not None:
            return answer

    if not assume_yes:
        if not _is_interactive(stdin):
            _say(err, "error: Viddl will not download without being asked.")
            _say(err, "  Pass --yes to download without a question, for scripts.")
            return 1
        err.write(ascii_text(f"Download {chosen.label}? [y/N] "))
        err.flush()
        if stdin.readline().strip().lower() not in ("y", "yes"):
            _say(err, "Nothing was downloaded.")
            return 0

    line = _Line(err)

    def on_progress(progress: Progress) -> None:
        line.show(_progress_line(progress))

    job = None
    try:
        job = make_job(
            engine,
            url,
            chosen,
            downloads_dir(),
            on_progress=on_progress,
            cookies=cookies,
            extras=extras,
            archive=archive,
            title=info.title,
        )
        path = job.run()
    except KeyboardInterrupt:
        if job is not None:
            job.cancel(force=True)
        line.clear()
        _say(err, "Stopped. Run the same command again to carry on from here.")
        return 130
    except DownloadCancelled:
        line.clear()
        _say(err, "Stopped. Run the same command again to carry on from here.")
        return 130
    except ToolError as exc:
        line.clear()
        _explain_failure(err, exc, engine)
        return 1

    line.clear()
    try:
        size = human_size(Path(path).stat().st_size)
    except OSError:  # pragma: no cover - a file that vanished after the move
        size = "?"
    # "Already had" for a file the download archive recognised (F6): the path
    # on stdout is the same either way — it is the file the user asked for —
    # but the log should be able to tell a fetch from a recall.
    if getattr(job, "already_had", False):
        _say(err, f"Already had ({size})")
    else:
        _say(err, f"Saved ({size})")
    for attachment in getattr(job, "attachments", []):
        # Beside the file, never instead of it (F5).
        _say(err, f"Also saved: {Path(attachment).name}")
    # The path goes out exactly as it is: this line is what a script uses, and
    # a transliterated filename would point at a file that is not there.
    out.write(f"{path}\n")
    out.flush()
    return 0
