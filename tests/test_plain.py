"""The line-oriented path, and text a legacy console can print (R10).

``viddl --plain`` exists because a Windows console is not always a modern
terminal. These tests hold two promises: the app's own words are ASCII, so no
console encoding can turn them into a traceback, and the saved path is the only
thing on stdout, so a script can use it.
"""

from __future__ import annotations

import ast
import io
import shutil
import tempfile
from pathlib import Path

import pytest

from viddl import cli, plain
from viddl.download import DownloadCancelled, Progress
from viddl.engine import Tool, ToolError
from viddl.extras import Extras
from viddl.formats import MediaInfo, MediaOption, parse_info

REPO = Path(__file__).resolve().parent.parent
URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

#: Where the fake download writes. A real folder in the system temp area: the
#: fake job creates a real file so the result screen can report a real size.
DEST = Path(tempfile.gettempdir()) / "viddl-plain-tests"

INFO_JSON = {
    "title": "A Very Real Video",
    "uploader": "Some Channel",
    "duration": 212,
    "webpage_url": URL,
    "extractor_key": "Youtube",
    "formats": [
        {"format_id": "140", "ext": "m4a", "audio_ext": "m4a", "vcodec": "none",
         "acodec": "mp4a", "abr": 128, "filesize": 1600000},
        {"format_id": "137", "ext": "mp4", "vcodec": "avc1", "acodec": "none",
         "height": 1080, "fps": 30, "vbr": 5000, "filesize": 20000000},
        {"format_id": "22", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a",
         "height": 720, "tbr": 2000, "filesize": 12000000},
        {"format_id": "18", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a",
         "height": 360, "tbr": 700, "filesize": 9000000},
    ],
}


def media() -> MediaInfo:
    return parse_info(INFO_JSON)


class FakeEngine:
    """Stands in for the real engine, so no test can depend on the machine.

    Whether ffmpeg is installed on the computer running the suite must not
    change the answer here — GitHub's runners have it, a fresh container does
    not.
    """

    def __init__(self, ffmpeg: bool = True):
        self.ffmpeg = ffmpeg
        self.fetched = 0

    def find_ffmpeg(self):
        if not self.ffmpeg:
            return None
        return Tool("ffmpeg", ["/fake/ffmpeg"], "system", version="7.0")

    def ensure_ffmpeg(self, on_status=None, on_progress=None, allow_download=True):
        self.fetched += 1
        if on_status:
            on_status("Downloading ffmpeg (one time)...")
        if on_progress:
            on_progress(50, 100)
        self.ffmpeg = True
        return Tool("ffmpeg", ["/fake/ffmpeg"], "viddl", version="7.0")

    def ffmpeg_size_hint(self):
        return 150 * 1024 * 1024


class BrokenFfmpegEngine(FakeEngine):
    def __init__(self):
        super().__init__(ffmpeg=False)

    def ensure_ffmpeg(self, on_status=None, on_progress=None, allow_download=True):
        raise ToolError("Could not reach the internet.", "Check your connection.")


class FakeJob:
    """Writes a real file, reporting progress like a real download would."""

    made: list = []

    def __init__(
        self,
        engine,
        url,
        option,
        out_dir,
        on_progress=None,
        on_log=None,
        cookies="",
        extras=None,
    ):
        self.option = option
        self.out_dir = Path(out_dir)
        self.on_progress = on_progress
        # The browser the download was told to read, if any (F1).
        self.cookies = cookies
        # What was added to the download besides the media (F5).
        self.extras = extras
        self.attachments = []
        self.cancelled = False
        self.force_cancelled = False
        FakeJob.made.append(self)

    def run(self):
        self.on_progress(
            Progress(
                status="Downloading…",
                percent=50.0,
                speed="1.0 MB/s",
                eta="0:02",
                downloaded=1024,
                total=2048,
            )
        )
        self.out_dir.mkdir(parents=True, exist_ok=True)
        target = self.out_dir / "A Very Real Video [dQw4w9WgXcQ].mp4"
        target.write_bytes(b"x" * 2048)
        self.on_progress(Progress(status="Done", percent=100.0, phase="done"))
        return target

    def cancel(self, force: bool = False):
        self.cancelled = True
        self.force_cancelled = force


class MergeJob(FakeJob):
    """Reports the phases the real job does: merging, then a retry wait."""

    def run(self):
        self.on_progress(Progress(status="Merging video and audio…", phase="merge"))
        self.on_progress(
            Progress(status="Retrying in 4s (attempt 2 of 3)…", phase="retry")
        )
        return super().run()


class InterruptedJob(FakeJob):
    def run(self):
        raise KeyboardInterrupt()


class CancelledJob(FakeJob):
    def run(self):
        raise DownloadCancelled()


class Tty(io.StringIO):
    """Standard input that looks like a terminal, with a scripted answer."""

    def __init__(self, answer: str = ""):
        super().__init__(answer)

    def isatty(self) -> bool:
        return True


@pytest.fixture(autouse=True)
def no_jobs_left_over():
    FakeJob.made.clear()
    shutil.rmtree(DEST, ignore_errors=True)
    yield
    FakeJob.made.clear()
    shutil.rmtree(DEST, ignore_errors=True)


def run(url=URL, *, info=None, job_class=FakeJob, probe=None, engine=None, **kwargs):
    out, err = io.StringIO(), io.StringIO()
    if probe is None:
        info = info if info is not None else media()

        def probe(engine, link, cookies=""):
            return info

    code = plain.run_plain(
        url,
        engine=engine if engine is not None else FakeEngine(),
        probe=probe,
        make_job=job_class,
        downloads_dir=lambda: DEST,
        stdout=out,
        stderr=err,
        **kwargs,
    )
    return code, out.getvalue(), err.getvalue()


# ---------------------------------------------------------------------------
# ASCII, and only ASCII
# ---------------------------------------------------------------------------


def test_the_typography_viddl_uses_becomes_something_any_console_can_print():
    line = plain.ascii_text("Audio only · MP3 … — “Stop looking” → done")
    assert line == 'Audio only - MP3 ... - "Stop looking" -> done'
    assert line.isascii()


def test_accents_are_folded_rather_than_lost():
    assert plain.ascii_text("Café Crème") == "Cafe Creme"


def test_a_title_no_console_can_print_does_not_become_a_crash():
    line = plain.ascii_text("日本語のビデオ 🎬")
    assert line.isascii()
    assert line.startswith("?")
    line.encode("cp1252")  # the whole point: this cannot raise


def test_the_plain_menu_is_ascii_even_for_a_title_full_of_typography():
    info = media()
    info.title = "Café — “Live” 日本語"
    info.uploader = "Sömé Channel"
    code, out, err = run(info=info, assume_yes=True)

    assert code == 0
    assert out == f"{DEST / 'A Very Real Video [dQw4w9WgXcQ].mp4'}\n"
    assert err.isascii(), err
    assert "Cafe - \"Live\" ???" in err
    err.encode("cp1252")


def test_every_string_viddl_says_can_be_printed_by_a_cp1252_console():
    """A legacy console is the reason --plain exists; no literal may escape it.

    The one exception is the translation table in ``plain.py`` itself: those
    characters exist there in order to be replaced by the line above, so they
    are allowed to be unprintable.
    """
    translated = {chr(codepoint) for codepoint in plain._TO_ASCII}
    bad = []
    for path in sorted((REPO / "viddl").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if path.name == "plain.py" and node.value in translated:
                continue
            try:
                node.value.encode("cp1252")
            except UnicodeEncodeError:
                bad.append(f"{path.relative_to(REPO)}:{node.lineno}: {node.value!r}")
    assert not bad, "these would break a cp1252 console:\n" + "\n".join(bad)


# ---------------------------------------------------------------------------
# choosing a quality without a menu
# ---------------------------------------------------------------------------


def test_the_best_quality_is_the_default():
    assert plain.choose_option(media()).height == 1080


def test_a_height_picks_that_height():
    assert plain.choose_option(media(), "720").height == 720


def test_a_height_between_rows_takes_the_next_one_down():
    """900 is not offered, and 720 is the honest answer to \"at most 900\"."""
    assert plain.choose_option(media(), "900").height == 720


def test_a_height_above_everything_takes_the_best_there_is():
    assert plain.choose_option(media(), "2160").height == 1080


@pytest.mark.parametrize("word", ["audio", "AUDIO", "a"])
def test_the_word_audio_takes_the_best_sound_the_link_has(word):
    """Which, since F4, is the site's own file rather than a re-encode."""
    chosen = plain.choose_option(media(), word)
    assert chosen.kind == "audio"
    assert chosen.key == "orig"


def test_mp3_still_means_mp3():
    """A script that asked for MP3 keeps getting MP3."""
    assert plain.choose_option(media(), "mp3").key == "mp3-320"


def test_a_preset_can_be_named_exactly():
    assert plain.choose_option(media(), "mp3-192").key == "mp3-192"
    assert plain.choose_option(media(), "opus-96").key == "opus-96"


def test_a_lossless_format_is_chosen_by_name():
    assert plain.choose_option(media(), "flac").key == "flac"
    assert plain.choose_option(media(), "wav").key == "wav"


def test_a_format_the_link_already_has_is_not_re_encoded():
    """The sound is already m4a, so `m4a` gives it untouched (F4)."""
    chosen = plain.choose_option(media(), "m4a")
    assert chosen.key == "orig"
    assert chosen.keeps_source_audio is True


def test_a_format_the_link_does_not_have_is_converted_to():
    info = parse_info(
        {
            "title": "opus only",
            "duration": 60,
            "formats": [
                {"format_id": "251", "ext": "webm", "vcodec": "none", "acodec": "opus",
                 "abr": 160, "filesize": 1200000},
            ],
        }
    )
    chosen = plain.choose_option(info, "m4a")
    assert chosen.key == "m4a-256"
    assert chosen.extra_args[-2:] == ["--audio-quality", "256"]


def test_an_audio_preset_the_link_does_not_have_says_what_is_offered():
    with pytest.raises(ToolError) as caught:
        plain.choose_option(media(), "mp3-999")
    assert "no 'mp3-999' audio option" in caught.value.message
    assert "flac" in (caught.value.hint or "")


def test_the_audio_flag_chooses_audio_without_a_quality():
    assert plain.choose_option(media(), "", audio=True).kind == "audio"


def test_an_audio_request_on_a_link_without_audio_says_so():
    info = MediaInfo(url=URL, title="Video only", options=[MediaOption(key="v", label="720p")])
    with pytest.raises(ToolError) as caught:
        plain.choose_option(info, "audio")
    assert "audio-only" in caught.value.message


def test_an_unusable_quality_says_what_is_allowed():
    with pytest.raises(ToolError) as caught:
        plain.choose_option(media(), "medium")
    assert "Unknown quality" in caught.value.message
    assert "720" in (caught.value.hint or "")


def test_a_link_with_no_rows_is_an_error_with_a_reason():
    with pytest.raises(ToolError) as caught:
        plain.choose_option(MediaInfo(url=URL, title="Nothing"))
    assert "No downloadable qualities" in caught.value.message


# ---------------------------------------------------------------------------
# running it
# ---------------------------------------------------------------------------


def test_the_saved_path_is_the_only_thing_on_stdout():
    """``path=$(viddl --plain --yes <link>)`` has to receive exactly a path."""
    code, out, err = run(assume_yes=True)

    assert code == 0
    assert out.splitlines() == [str(DEST / "A Very Real Video [dQw4w9WgXcQ].mp4")]
    assert "Selected: 1080p" in err
    assert "Saved (2.0 KB)" in err
    assert "\r" in err, "progress is written in place, on one line"


def test_progress_never_leaves_a_line_longer_than_the_screen():
    _, _, err = run(assume_yes=True)
    for line in err.splitlines():
        assert len(line) < 120, line


def test_a_merge_or_a_retry_status_is_ascii_too():
    """The statuses come from Viddl, not the site, so they must be reduced."""
    _, _, err = run(assume_yes=True, job_class=MergeJob)
    assert err.isascii(), err
    assert "Merging video and audio..." in err
    assert "Retrying in 4s (attempt 2 of 3)..." in err


def test_a_quality_chosen_on_the_command_line_is_the_one_downloaded():
    _, _, err = run(assume_yes=True, quality="720")
    assert "Selected: 720p" in err
    assert FakeJob.made[0].option.height == 720


def test_audio_only_works_from_the_command_line():
    _, _, err = run(assume_yes=True, quality="audio")
    assert "Selected: Audio only" in err
    assert FakeJob.made[0].option.kind == "audio"
    # The untouched file, not an MP3 made out of it (F4).
    assert FakeJob.made[0].option.key == "orig"


def test_a_conversion_can_be_chosen_from_the_command_line():
    _, _, err = run(assume_yes=True, quality="flac")
    # The separator reaches the console as a hyphen: --plain is ASCII only.
    assert "Selected: Audio only - FLAC" in err
    assert FakeJob.made[0].option.extra_args[-1] == "flac"


def test_a_failed_look_up_is_a_sentence_not_a_traceback():
    def probe(engine, link, cookies=""):
        raise ToolError("That video is private.", "Only the owner can download it.")

    code, out, err = run(probe=probe)
    assert code == 1
    assert out == ""
    assert "error: That video is private." in err
    assert "Only the owner can download it." in err


def test_a_failed_download_is_a_sentence_too():
    class Failing(FakeJob):
        def run(self):
            raise ToolError("The download was interrupted.", "Try again.")

    code, out, err = run(assume_yes=True, job_class=Failing)
    assert code == 1
    assert out == ""
    assert "error: The download was interrupted." in err


def test_without_a_terminal_and_without_yes_it_refuses_before_downloading():
    code, out, err = run(assume_yes=False)
    assert code == 1
    assert out == ""
    assert "Pass --yes" in err
    assert FakeJob.made == []


def test_at_a_terminal_it_asks_and_a_no_downloads_nothing():
    code, out, err = run(assume_yes=False, stdin=Tty("n\n"))
    assert code == 0
    assert out == ""
    assert "Download 1080p? [y/N]" in err
    assert "Nothing was downloaded." in err
    assert FakeJob.made == []


def test_at_a_terminal_a_yes_downloads():
    code, out, _ = run(assume_yes=False, stdin=Tty("y\n"))
    assert code == 0
    assert out.strip().endswith(".mp4")
    assert len(FakeJob.made) == 1


# ---------------------------------------------------------------------------
# ffmpeg, when the chosen quality needs it
# ---------------------------------------------------------------------------


def test_a_quality_that_needs_ffmpeg_fetches_it_when_asked_in_advance():
    engine = FakeEngine(ffmpeg=False)
    code, out, err = run(assume_yes=True, engine=engine)

    assert code == 0
    assert engine.fetched == 1
    assert "Fetching ffmpeg" in err
    assert "ffmpeg is ready" in err
    assert out.strip().endswith(".mp4")


def test_the_ffmpeg_fetch_says_how_big_it_is_before_asking():
    engine = FakeEngine(ffmpeg=False)
    code, _, err = run(assume_yes=False, stdin=Tty("n\n"), engine=engine)

    assert code == 0
    assert "150.0 MB" in err
    assert "Fetch ffmpeg now" in err
    assert "Nothing was downloaded." in err
    assert engine.fetched == 0
    assert FakeJob.made == []


def test_saying_yes_to_ffmpeg_downloads_it_and_then_the_video():
    """Two questions, two answers: ffmpeg, then the download."""
    engine = FakeEngine(ffmpeg=False)
    code, out, _ = run(assume_yes=False, stdin=Tty("y\ny\n"), engine=engine)

    assert code == 0
    assert engine.fetched == 1
    assert out.strip().endswith(".mp4")


def test_the_ffmpeg_prompt_for_an_audio_conversion_says_what_it_is_for():
    """Converting is not joining: the sentence has to match the job (F4)."""
    engine = FakeEngine(ffmpeg=False)
    code, _, err = run(assume_yes=False, stdin=Tty("n\n"), engine=engine, quality="mp3-128")

    assert code == 0
    assert "needs ffmpeg to write the MP3 file" in err
    assert "join the video and audio" not in err


def test_the_untouched_audio_row_needs_no_ffmpeg_at_all():
    engine = FakeEngine(ffmpeg=False)
    code, out, err = run(assume_yes=True, engine=engine, quality="audio")

    assert code == 0
    assert engine.fetched == 0
    # The menu mentions ffmpeg beside the rows that need it; this run never does.
    assert "Fetch ffmpeg now" not in err
    assert "Fetching ffmpeg" not in err
    assert out.strip().endswith(".mp4")


def test_without_a_terminal_ffmpeg_is_not_fetched_behind_the_users_back():
    engine = FakeEngine(ffmpeg=False)
    code, _, err = run(assume_yes=False, engine=engine)

    assert code == 1
    assert "no terminal to ask on" in err
    assert engine.fetched == 0
    assert FakeJob.made == []


def test_a_quality_that_needs_no_ffmpeg_is_not_asked_about_ffmpeg():
    info = MediaInfo(
        url=URL,
        title="Progressive",
        options=[MediaOption(key="v360", label="360p", height=360, needs_ffmpeg=False)],
    )
    engine = FakeEngine(ffmpeg=False)
    code, out, err = run(info=info, assume_yes=True, engine=engine)

    assert code == 0
    assert "ffmpeg" not in err
    assert out.strip()


# ---------------------------------------------------------------------------
# extras: subtitles, details and a cover image (F5)
# ---------------------------------------------------------------------------


def test_the_plain_path_says_what_is_being_added():
    code, out, err = run(assume_yes=True, extras=Extras.parse("subs,details"))

    assert code == 0
    assert "Extras: subtitles and details." in err
    assert FakeJob.made[0].extras.keys == frozenset({"subs", "details"})
    # Named before anything is saved, so a log records what was asked for.
    assert err.index("Extras:") < err.index("Saved (")
    assert out.strip().endswith(".mp4")


def test_without_extras_nothing_is_claimed():
    code, _, err = run(assume_yes=True)

    assert code == 0
    assert "Extras:" not in err


def test_extras_that_need_ffmpeg_are_set_up_first():
    """A progressive row needs nothing; its details still need ffmpeg."""
    engine = FakeEngine(ffmpeg=False)
    code, out, err = run(
        assume_yes=True, engine=engine, quality="720", extras=Extras.parse("details")
    )

    assert code == 0
    assert engine.fetched == 1
    assert "Fetching ffmpeg" in err
    assert out.strip().endswith(".mp4")


def test_an_audio_row_that_only_asked_for_subtitles_stays_ffmpeg_free():
    """Subtitles are the only extra this download asks for, and cannot apply."""
    engine = FakeEngine(ffmpeg=False)
    code, out, err = run(
        assume_yes=True, engine=engine, quality="audio", extras=Extras.parse("subs")
    )

    assert code == 0
    assert engine.fetched == 0
    assert "Skipped: subtitles" in err
    assert "nowhere to put them" in err
    # Not "Extras: none", which would read as if something had failed.
    assert "Extras: none" not in err
    assert FakeJob.made[0].extras.has("subs"), "the choice is still recorded"
    assert out.strip().endswith(".mp4")


def test_a_cover_image_that_was_saved_beside_the_file_is_named():
    class CoverJob(FakeJob):
        def run(self):
            path = super().run()
            cover = self.out_dir / "A Very Real Video [dQw4w9WgXcQ].jpg"
            cover.write_bytes(b"picture")
            self.attachments = [cover]
            return path

    code, out, err = run(assume_yes=True, job_class=CoverJob, extras=Extras.parse("cover"))

    assert code == 0
    assert "Also saved: A Very Real Video [dQw4w9WgXcQ].jpg" in err
    # Standard output stays the media path and nothing else.
    assert ".jpg" not in out


def test_a_failed_ffmpeg_fetch_is_a_sentence_not_a_traceback():
    code, out, err = run(assume_yes=True, engine=BrokenFfmpegEngine())

    assert code == 1
    assert out == ""
    assert "error: Could not reach the internet." in err
    assert FakeJob.made == []


def test_ctrl_c_stops_the_whole_download_and_keeps_what_was_fetched():
    code, out, err = run(assume_yes=True, job_class=InterruptedJob)
    assert code == 130
    assert out == ""
    assert "carry on from here" in err
    assert FakeJob.made[0].cancelled is True
    assert FakeJob.made[0].force_cancelled is True


def test_a_cancelled_download_is_not_reported_as_a_failure():
    code, _, err = run(assume_yes=True, job_class=CancelledJob)
    assert code == 130
    assert "error:" not in err


# ---------------------------------------------------------------------------
# the command line
# ---------------------------------------------------------------------------


def test_plain_without_a_link_says_what_it_needs(capsys):
    assert cli.main(["--plain"]) == 1
    assert "--plain needs a link" in capsys.readouterr().err


def test_quality_and_yes_are_refused_without_plain(capsys):
    assert cli.main(["--quality", "720", URL]) == 1
    assert "only work with --plain" in capsys.readouterr().err


def test_the_cli_reads_the_extras_before_anything_is_downloaded(monkeypatch):
    seen = {}
    monkeypatch.setattr(plain, "run_plain", lambda url, **kwargs: seen.update(kwargs) or 0)

    assert cli.main(["--plain", "--yes", "--extras", "subs, cover", URL]) == 0

    assert seen["extras"].keys == frozenset({"subs", "cover"})


def test_a_mistyped_extra_is_refused_with_the_list(capsys):
    assert cli.main(["--plain", "--extras", "subtitel", URL]) == 1

    err = capsys.readouterr().err
    assert "Unknown extra 'subtitel'" in err
    assert "subs, details or cover" in err
    assert "For example: viddl --extras subs,cover" in err


def test_the_cli_hands_the_choices_to_the_plain_path(monkeypatch):
    seen = {}

    def fake_run_plain(url, **kwargs):
        seen["url"] = url
        seen.update(kwargs)
        return 0

    monkeypatch.setattr(plain, "run_plain", fake_run_plain)

    assert cli.main(["--plain", "--yes", "--quality", "720", URL]) == 0
    assert seen == {
        "url": URL,
        "audio": False,
        "quality": "720",
        "assume_yes": True,
        "cookies": "",
        "playlist": False,
        # Nothing is added to the download unless it was asked for (F5).
        "extras": Extras(),
    }


def test_the_cli_passes_audio_through(monkeypatch):
    seen = {}
    monkeypatch.setattr(plain, "run_plain", lambda url, **kwargs: seen.update(kwargs) or 0)

    assert cli.main(["--plain", "--audio", "--yes", URL]) == 0
    assert seen["audio"] is True

def test_plain_says_what_reading_cookies_means(tmp_path):
    """The sentence goes out before the download, where a log can keep it."""
    seen: dict = {}

    def probe(engine, link, cookies=""):
        seen["probe"] = cookies
        return media()

    code, out, err = run(assume_yes=True, probe=probe, cookies="chrome")

    assert code == 0
    assert seen["probe"] == "chrome"
    assert FakeJob.made[0].cookies == "chrome"
    assert "Using Chrome's cookies" in err
    assert "for this download only" in err
    assert "Nothing was stored" in err
    assert err.isascii()
    # Standard output stays the path and nothing else: a script reads it.
    assert "cookie" not in out.lower()
    assert out.strip().endswith(".mp4")


def test_without_a_cookie_choice_nothing_is_claimed():
    code, out, err = run(assume_yes=True)
    assert code == 0
    assert "cookies" not in err.lower()
    assert FakeJob.made[0].cookies == ""


def test_the_cookie_choice_reaches_the_plain_path(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(plain, "run_plain", lambda url, **kwargs: seen.update(kwargs) or 0)

    assert cli.main(["--cookies-from-browser", "chrome", "--plain", "--yes", URL]) == 0
    assert seen["cookies"] == "chrome"
