"""Drive the real terminal app with Textual's test pilot.

Nothing here touches the network or spawns a downloader: the engine, the link
probe and the download job are all stand-ins, so we are testing the interface
itself — typing a link, choosing with the mouse or the keyboard, and the path
that gets shown at the end.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from viddl.download import DownloadCancelled, Progress
from viddl.engine import Tool, ToolError
from viddl.formats import MediaInfo, parse_info
from viddl.protocol import build_scheme_url
from viddl.tui.app import (
    DownloadScreen,
    HomeScreen,
    OptionsScreen,
    ResultScreen,
    Services,
    StoppedScreen,
    ViddlApp,
    _open_folder,
)
from viddl.tui.widgets import ChoiceList, ChoiceRow

URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

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
        {"format_id": "18", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a",
         "height": 360, "tbr": 700, "filesize": 9000000},
    ],
}


class FakeEngine:
    """Stands in for the real engine so tests stay offline and instant."""

    def __init__(self, ffmpeg: bool = True, fail: bool = False, version: str = "2099.01.01"):
        self.ffmpeg = ffmpeg
        self.fail = fail
        self.version = version
        self.ytdlp_calls = 0

    def ensure_ytdlp(self, on_status=None, on_progress=None, allow_download=True):
        self.ytdlp_calls += 1
        if self.fail:
            raise ToolError("Could not reach the internet.", "Check your connection.")
        if on_status:
            on_status("Using the video engine found on your computer.")
        return Tool("yt-dlp", ["/fake/yt-dlp"], "system", version=self.version)

    def find_ffmpeg(self):
        if not self.ffmpeg:
            return None
        return Tool("ffmpeg", ["/fake/ffmpeg"], "system", version="7.0")

    def ensure_ffmpeg(self, on_status=None, on_progress=None, allow_download=True):
        if on_status:
            on_status("Installing ffmpeg…")
        if on_progress:
            on_progress(50, 100)
        self.ffmpeg = True
        return Tool("ffmpeg", ["/fake/ffmpeg"], "viddl", version="7.0")

    def ffmpeg_location_args(self):
        return []


class FakeJob:
    """Writes a real file so the result screen can show its size."""

    def __init__(self, engine, url, option, out_dir, on_progress=None, on_log=None):
        self.option = option
        self.out_dir = Path(out_dir)
        self.on_progress = on_progress
        self.cancelled = False

    def run(self):
        self.out_dir.mkdir(parents=True, exist_ok=True)
        target = self.out_dir / "A Very Real Video [dQw4w9WgXcQ].mp4"
        target.write_bytes(b"x" * 2048)
        if self.on_progress:
            self.on_progress(Progress(status="Downloading…", percent=50.0, speed="1.0 MB/s",
                                      eta="0:02", downloaded=1024, total=2048))
            self.on_progress(Progress(status="Done", percent=100.0, phase="done"))
        return target

    def cancel(self, force: bool = False):
        self.cancelled = True
        self.force_cancelled = force


class FailingJob(FakeJob):
    def run(self):
        raise ToolError("That video is not available any more.", "It may have been deleted.")


class BlockingJob(FakeJob):
    """Stays busy until it is cancelled, so cancellation can be exercised."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.release = threading.Event()

    def run(self):
        self.release.wait(timeout=10)
        raise DownloadCancelled()

    def cancel(self, force: bool = False):
        self.cancelled = True
        self.force_cancelled = force
        self.release.set()


class JobWithAPartial(BlockingJob):
    """A stand-in that really leaves a half-finished file behind.

    The real ``DownloadJob`` reports its own leftovers and deletes them on
    request; this one writes a file so the screen's Discard button has
    something real to remove.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.partial = self.out_dir / "A Very Real Video [dQw4w9WgXcQ].mp4.part"

    def run(self):
        self.partial.parent.mkdir(parents=True, exist_ok=True)
        self.partial.write_bytes(b"h" * 2048)
        self.release.wait(timeout=10)
        raise DownloadCancelled()

    def partial_paths(self):
        return [self.partial] if self.partial.exists() else []

    def discard_partial(self):
        existed = self.partial.exists()
        self.partial.unlink(missing_ok=True)
        return 1 if existed else 0


class ChattyJob(FakeJob):
    """Reports real progress and then waits, so the screen can be inspected."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.release = threading.Event()

    def run(self):
        if self.on_progress:
            self.on_progress(
                Progress(
                    status="Downloading…",
                    percent=42.0,
                    speed="1.5 MB/s",
                    eta="0:07",
                    downloaded=1024,
                    total=4096,
                    phase="download",
                )
            )
        self.release.wait(timeout=10)
        raise DownloadCancelled()

    def cancel(self, force: bool = False):
        self.cancelled = True
        self.force_cancelled = force
        self.release.set()


class BrokenFfmpegEngine(FakeEngine):
    """ffmpeg is missing and fetching it fails."""

    def __init__(self):
        super().__init__(ffmpeg=False)

    def ensure_ffmpeg(self, on_status=None, on_progress=None, allow_download=True):
        raise ToolError("Could not reach the internet.", "Check your connection.")


class SlowFfmpegEngine(FakeEngine):
    """Fetching ffmpeg takes a while: it waits until the test lets it finish.

    Blocking rather than sleeping keeps the on-screen progress steady, so the
    test can look at it without racing the worker.
    """

    def __init__(self, done: int = 50, total=None):
        super().__init__(ffmpeg=False)
        self.done = done
        self.total = total
        self.release = threading.Event()

    def ensure_ffmpeg(self, on_status=None, on_progress=None, allow_download=True):
        if on_status:
            on_status("Installing ffmpeg…")
        if on_progress:
            on_progress(self.done, self.total)
        self.release.wait(timeout=10)
        self.ffmpeg = True
        return Tool("ffmpeg", ["/fake/ffmpeg"], "viddl", version="7.0")


def make_services(tmp_path: Path, engine=None, info=None, job_class=FakeJob) -> Services:
    info = info if info is not None else parse_info(INFO_JSON)
    return Services(
        engine=engine or FakeEngine(),
        probe=lambda eng, url: info,
        make_job=job_class,
        downloads_dir=lambda: tmp_path / "Downloads",
    )


async def settle(pilot, rounds: int = 6) -> None:
    """Let worker threads finish and their messages be processed."""
    for _ in range(rounds):
        await pilot.pause()
        await asyncio.sleep(0.01)


def shown(screen, selector: str) -> str:
    """The plain text a Static widget is currently displaying."""
    return str(screen.query_one(selector).render())


class captured_notifications:
    """Collect the toasts the app would have shown instead of showing them."""

    def __init__(self, app: ViddlApp):
        self.app = app
        self.entries: list = []

    def __enter__(self):
        def record(message, *, title="", severity="information", timeout=None, markup=True):
            self.entries.append((str(message), severity))

        self.app.notify = record
        return self

    def __exit__(self, *exc):
        del self.app.notify
        return False

    @property
    def messages(self) -> list:
        return [message for message, _ in self.entries]


async def wait_for_job(app, pilot, rounds: int = 50):
    """Wait for a download worker to register its job."""
    for _ in range(rounds):
        await pilot.pause()
        await asyncio.sleep(0.01)
        if app.state.job is not None:
            return app.state.job
    raise AssertionError("the download never started")


@pytest.mark.asyncio
async def test_typing_a_link_shows_qualities_and_downloads(tmp_path):
    services = make_services(tmp_path)
    app = ViddlApp(services=services)

    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()
        assert isinstance(app.screen, HomeScreen)

        app.screen.query_one("#url-input").value = URL
        await pilot.press("enter")
        await settle(pilot)

        assert isinstance(app.screen, OptionsScreen)
        choices = app.screen.query_one("#choices", ChoiceList)
        assert choices.row_count == 5  # 1080p, 360p and three MP3 options
        assert "A Very Real Video" in shown(app.screen, "#options-heading")

        # Keyboard: Enter on the highlighted (best) option starts the download.
        await pilot.press("enter")
        await settle(pilot)

        assert isinstance(app.screen, ResultScreen)
        assert shown(app.screen, "#result-path") == str(
            tmp_path / "Downloads" / "A Very Real Video [dQw4w9WgXcQ].mp4"
        )
        assert "Saved" in shown(app.screen, "#brand")
        assert app.state.saved_path is not None and app.state.saved_path.exists()


@pytest.mark.asyncio
async def test_launching_with_a_url_skips_the_home_screen(tmp_path):
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        assert isinstance(app.screen, OptionsScreen)


class RecordingJob(FakeJob):
    """Notes every download that is started, so a test can prove none was."""

    started: list = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        RecordingJob.started.append(self)


@pytest.mark.asyncio
async def test_a_hand_off_never_starts_a_download(tmp_path):
    """The guarantee a page cannot break: a link is shown, never acted on.

    Any page may open Viddl with a ``viddl://`` link, so the app must not
    download, or choose a quality, without the person at the keyboard saying so.
    """
    RecordingJob.started.clear()
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path, job_class=RecordingJob))

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot, rounds=12)

        assert isinstance(app.screen, OptionsScreen)
        assert RecordingJob.started == [], "a hand-off started a download on its own"
        assert app.state.option is None
        assert app.state.job is None

        # The choice is what starts it, and only the choice.
        await pilot.press("enter")
        await settle(pilot)
        assert len(RecordingJob.started) == 1


@pytest.mark.asyncio
async def test_an_audio_hand_off_never_starts_a_download_either(tmp_path):
    RecordingJob.started.clear()
    app = ViddlApp(
        initial_url=URL,
        prefer_audio=True,
        services=make_services(tmp_path, job_class=RecordingJob),
    )

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot, rounds=12)

        assert isinstance(app.screen, OptionsScreen)
        assert RecordingJob.started == []


@pytest.mark.asyncio
async def test_clicking_a_quality_row_selects_it(tmp_path):
    services = make_services(tmp_path)
    app = ViddlApp(initial_url=URL, services=services)

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        choices = app.screen.query_one("#choices", ChoiceList)
        assert choices.row_count == 5

        # Row 4 is the 128 kbps MP3 (rows are one line tall, no border).
        await pilot.click("#choices", offset=(4, 4))
        await settle(pilot)

        assert app.state.option is not None
        assert app.state.option.key == "mp3-128"
        assert app.state.option.kind == "audio"
        # The click alone picked the row and started it; the fake finishes fast.
        assert isinstance(app.screen, (DownloadScreen, ResultScreen))


@pytest.mark.asyncio
async def test_arrow_keys_move_the_highlight(tmp_path):
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        choices = app.screen.query_one("#choices", ChoiceList)
        assert choices.cursor_index == 0
        await pilot.press("down")
        await pilot.pause()
        assert choices.cursor_index == 1
        assert choices.highlighted_value.key == "v360"
        await pilot.press("up")
        await pilot.pause()
        assert choices.cursor_index == 0
        assert choices.highlighted_value.key == "v1080"
        assert choices.highlighted_value.kind == "video"


@pytest.mark.asyncio
async def test_audio_flag_starts_on_the_mp3_options(tmp_path):
    app = ViddlApp(initial_url=URL, prefer_audio=True, services=make_services(tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        choices = app.screen.query_one("#choices", ChoiceList)
        assert choices.highlighted_value.kind == "audio"
        assert choices.highlighted_value.key == "mp3-320"


@pytest.mark.asyncio
async def test_a_bad_link_explains_itself(tmp_path):
    def explode(engine, url):
        raise ToolError("That video is private.", "Only the owner can download it.")

    services = make_services(tmp_path)
    services.probe = explode
    app = ViddlApp(initial_url=URL, services=services)

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        assert isinstance(app.screen, OptionsScreen)
        assert "private" in shown(app.screen, "#options-status").lower()
        assert app.screen.query_one("#download").disabled is True
        assert "owner" in shown(app.screen, "#options-note").lower()


@pytest.mark.asyncio
async def test_a_failed_download_shows_the_reason(tmp_path):
    services = make_services(tmp_path, job_class=FailingJob)
    app = ViddlApp(initial_url=URL, services=services)

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.press("enter")
        await settle(pilot)

        assert isinstance(app.screen, ResultScreen)
        assert "not available" in shown(app.screen, "#result-path")
        assert "Download failed" in shown(app.screen, "#brand")


@pytest.mark.asyncio
async def test_the_ffmpeg_prompt_appears_and_goes_away(tmp_path):
    engine = FakeEngine(ffmpeg=False)
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path, engine=engine))

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        prompt = app.screen.query_one("#get-ffmpeg")
        assert not prompt.has_class("hidden")

        await pilot.click("#get-ffmpeg")
        await settle(pilot)
        assert engine.ffmpeg is True
        assert prompt.has_class("hidden")

        # Focus went to the button; the list still takes over from there.
        choices = app.screen.query_one("#choices", ChoiceList)
        choices.focus()
        await pilot.press("down", "down", "down", "down")
        await pilot.pause()
        assert choices.highlighted_value.kind == "audio"
        assert choices.highlighted_value.key == "mp3-128"


@pytest.mark.asyncio
async def test_the_home_screen_says_the_engine_is_ready(tmp_path):
    app = ViddlApp(services=make_services(tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        line = shown(app.screen, "#engine-status")
        assert "Ready to download" in line
        assert "2099.01.01" in line
        assert "ffmpeg ready" in line
        # It has to fit on one line of an 80-column terminal.
        assert len(line) <= 76


@pytest.mark.asyncio
async def test_the_home_screen_says_when_the_engine_is_old(tmp_path):
    """The likeliest reason a link stopped working is worth saying out loud.

    The person clicking a link does not run ``--doctor``, so the hint has to be
    on the screen they are looking at — and still fit on one line.
    """
    app = ViddlApp(services=make_services(tmp_path, engine=FakeEngine(version="2020.01.01")))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        line = shown(app.screen, "#engine-status")
        assert "days old" in line
        assert "viddl --update" in line
        assert len(line) <= 76


@pytest.mark.asyncio
async def test_engine_setup_failure_is_visible_and_kind(tmp_path):
    engine = FakeEngine(fail=True)
    app = ViddlApp(services=make_services(tmp_path, engine=engine))

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        assert app.state.engine_error
        assert "internet" in app.state.engine_note.lower()


@pytest.mark.asyncio
async def test_download_another_returns_to_a_clean_home_screen(tmp_path):
    services = make_services(tmp_path)
    app = ViddlApp(initial_url=URL, services=services)

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.press("enter")
        await settle(pilot)
        assert isinstance(app.screen, ResultScreen)

        await pilot.click("#again")
        await settle(pilot)
        assert isinstance(app.screen, HomeScreen)
        assert len(app.screen_stack) == 2
        assert app.screen.query_one("#url-input").value == ""


@pytest.mark.asyncio
async def test_escape_cancels_a_running_download(tmp_path):
    services = make_services(tmp_path, job_class=BlockingJob)
    app = ViddlApp(initial_url=URL, services=services)

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.press("enter")  # start the (blocking) download

        for _ in range(50):
            await pilot.pause()
            await asyncio.sleep(0.01)
            if app.state.job is not None:
                break
        job = app.state.job
        assert job is not None
        assert isinstance(app.screen, DownloadScreen)

        await pilot.press("escape")
        await settle(pilot, rounds=12)

        assert job.cancelled is True
        assert isinstance(app.screen, StoppedScreen)


# ---------------------------------------------------------------------------
# stopping and resuming (R1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stopping_keeps_the_part_and_offers_the_choice(tmp_path):
    services = make_services(tmp_path, job_class=JobWithAPartial)
    app = ViddlApp(initial_url=URL, services=services)

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.press("enter")
        job = await wait_for_job(app, pilot)
        for _ in range(50):
            await pilot.pause()
            await asyncio.sleep(0.01)
            if job.partial.is_file():
                break

        await pilot.press("escape")
        await settle(pilot, rounds=12)

        assert isinstance(app.screen, StoppedScreen)
        assert "kept" in shown(app.screen, "#stopped-message")
        assert app.screen.query("#resume")
        assert app.screen.query("#discard")
        # Cancelling must not throw away work the user waited for.
        assert job.partial.is_file()


@pytest.mark.asyncio
async def test_resume_runs_the_same_download_again(tmp_path):
    services = make_services(tmp_path, job_class=JobWithAPartial)
    app = ViddlApp(initial_url=URL, services=services)

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.press("enter")
        first = await wait_for_job(app, pilot)
        await pilot.press("escape")
        await settle(pilot, rounds=12)
        assert isinstance(app.screen, StoppedScreen)

        await pilot.press("r")
        second = await wait_for_job(app, pilot)
        await settle(pilot)

        assert second is not first
        assert isinstance(app.screen, DownloadScreen)
        assert second.option.key == first.option.key
        assert first.partial.is_file()


@pytest.mark.asyncio
async def test_discard_deletes_what_was_left_behind(tmp_path):
    services = make_services(tmp_path, job_class=JobWithAPartial)
    app = ViddlApp(initial_url=URL, services=services)

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.press("enter")
        job = await wait_for_job(app, pilot)
        for _ in range(50):
            await pilot.pause()
            await asyncio.sleep(0.01)
            if job.partial.is_file():
                break
        await pilot.press("escape")
        await settle(pilot, rounds=12)

        await pilot.press("d")
        await settle(pilot, rounds=12)

        assert not job.partial.exists()
        assert isinstance(app.screen, HomeScreen)


@pytest.mark.asyncio
async def test_leaving_it_alone_keeps_the_part_for_next_time(tmp_path):
    services = make_services(tmp_path, job_class=JobWithAPartial)
    app = ViddlApp(initial_url=URL, services=services)

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.press("enter")
        job = await wait_for_job(app, pilot)
        await pilot.press("escape")  # stop
        await settle(pilot, rounds=12)
        await pilot.press("escape")  # leave it for later
        await settle(pilot, rounds=12)

        assert isinstance(app.screen, HomeScreen)
        assert job.partial.is_file()


@pytest.mark.asyncio
async def test_a_stop_before_anything_was_written_offers_no_discard(tmp_path):
    services = make_services(tmp_path, job_class=BlockingJob)
    app = ViddlApp(initial_url=URL, services=services)

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.press("enter")
        await wait_for_job(app, pilot)
        await pilot.press("escape")
        await settle(pilot, rounds=12)

        assert isinstance(app.screen, StoppedScreen)
        assert "Nothing had been written" in shown(app.screen, "#stopped-message")
        assert not app.screen.query("#discard")
        assert app.screen.query("#resume")


@pytest.mark.asyncio
async def test_rows_show_the_size_and_render_as_text(tmp_path):
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        rows = list(app.screen.query(ChoiceRow))
        assert len(rows) == 5
        first = rows[0]
        assert first.row_size.startswith("~") or first.row_size.endswith(("MB", "GB"))  # noqa: E501
        assert first.row_label == "1080p"
        assert "MP4" in first.row_note
        assert first.has_class("active") is True
        assert rows[1].has_class("active") is False


# ---------------------------------------------------------------------------
# the link box
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clicking_download_uses_the_link_in_the_box(tmp_path):
    app = ViddlApp(services=make_services(tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()
        app.screen.query_one("#url-input").value = URL
        await pilot.click("#go")
        await settle(pilot)
        assert isinstance(app.screen, OptionsScreen)
        assert app.state.url == URL


@pytest.mark.asyncio
async def test_an_empty_link_box_asks_for_a_link(tmp_path):
    app = ViddlApp(services=make_services(tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()
        with captured_notifications(app) as noted:
            await pilot.click("#go")
            await pilot.pause()
        assert isinstance(app.screen, HomeScreen)
        assert any("Paste a link" in message for message in noted.messages)
        assert app.screen.query_one("#url-input").has_focus


@pytest.mark.asyncio
async def test_a_sentence_is_not_mistaken_for_a_link(tmp_path):
    app = ViddlApp(services=make_services(tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()
        app.screen.query_one("#url-input").value = "this is just a sentence"
        with captured_notifications(app) as noted:
            await pilot.press("enter")
            await pilot.pause()
        assert isinstance(app.screen, HomeScreen)
        assert any("looks like text" in message for message in noted.messages)


@pytest.mark.asyncio
async def test_an_option_shaped_link_is_refused_before_any_probe(tmp_path):
    """Typing a flag at the app must not turn into a flag for yt-dlp."""
    probed = []
    services = make_services(tmp_path)
    services.probe = lambda engine, url: probed.append(url) or parse_info(INFO_JSON)
    app = ViddlApp(services=services)

    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()
        app.screen.query_one("#url-input").value = "--config-locations=./evil.conf"
        with captured_notifications(app) as noted:
            await pilot.press("enter")
            await pilot.pause()

        assert isinstance(app.screen, HomeScreen)
        assert probed == []
        assert any("does not look like a video link" in message for message in noted.messages)


@pytest.mark.asyncio
async def test_a_hand_off_carrying_an_option_is_refused(tmp_path):
    probed = []
    services = make_services(tmp_path)
    services.probe = lambda engine, url: probed.append(url) or parse_info(INFO_JSON)
    app = ViddlApp(services=services)

    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()
        app.screen.query_one("#url-input").value = "viddl://download?url=--exec%3Dcalc.exe"
        with captured_notifications(app) as noted:
            await pilot.press("enter")
            await pilot.pause()

        assert isinstance(app.screen, HomeScreen)
        assert probed == []
        assert any("does not look like a video link" in message for message in noted.messages)


@pytest.mark.asyncio
async def test_an_option_shaped_starting_link_never_reaches_the_probe(tmp_path):
    """A link arriving on the command line is still a link from outside."""
    probed = []
    services = make_services(tmp_path)
    services.probe = lambda engine, url: probed.append(url) or parse_info(INFO_JSON)
    app = ViddlApp(initial_url="--exec=calc.exe", services=services)

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)

        assert app.state.url == ""
        assert isinstance(app.screen, HomeScreen)
        assert probed == []


@pytest.mark.asyncio
async def test_a_hand_off_link_pasted_by_hand_still_works(tmp_path):
    app = ViddlApp(services=make_services(tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()
        app.screen.query_one("#url-input").value = build_scheme_url(URL)
        await pilot.press("enter")
        await settle(pilot)
        assert isinstance(app.screen, OptionsScreen)
        assert app.state.url == URL


# ---------------------------------------------------------------------------
# the quality menu
# ---------------------------------------------------------------------------


def empty_options() -> MediaInfo:
    return MediaInfo(url=URL, title="Nothing here", options=[])


@pytest.mark.asyncio
async def test_the_note_says_where_the_file_will_land(tmp_path):
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        note = shown(app.screen, "#options-note")
        assert "Saves to" in note
        assert str(tmp_path / "Downloads") in note


@pytest.mark.asyncio
async def test_the_mp3_rows_explain_themselves(tmp_path):
    app = ViddlApp(initial_url=URL, prefer_audio=True, services=make_services(tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        note = shown(app.screen, "#options-note")
        assert "Audio only" in note
        assert "MP3" in note


@pytest.mark.asyncio
async def test_a_quality_that_needs_ffmpeg_says_why(tmp_path):
    engine = FakeEngine(ffmpeg=False)
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path, engine=engine))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        note = shown(app.screen, "#options-note")
        assert "needs ffmpeg" in note
        assert not app.screen.query_one("#get-ffmpeg").has_class("hidden")


@pytest.mark.asyncio
async def test_a_playlist_says_it_will_take_the_first_video(tmp_path):
    playlist = {
        "entries": [
            {**INFO_JSON, "playlist_index": 1},
            {**INFO_JSON, "title": "Second video"},
        ]
    }
    services = make_services(tmp_path, info=parse_info(playlist))
    app = ViddlApp(initial_url=URL, services=services)
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        assert "Only the first of 2 videos" in shown(app.screen, "#options-note")


@pytest.mark.asyncio
async def test_a_link_with_nothing_to_download_says_so(tmp_path):
    services = make_services(tmp_path, info=empty_options())
    app = ViddlApp(initial_url=URL, services=services)
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        assert "No downloadable qualities" in shown(app.screen, "#options-status")
        assert app.screen.query_one("#download").disabled is True

        with captured_notifications(app) as noted:
            await pilot.press("d")
            await pilot.pause()
        assert any("Choose a quality first" in message for message in noted.messages)
        assert isinstance(app.screen, OptionsScreen)


@pytest.mark.asyncio
async def test_d_starts_the_highlighted_quality(tmp_path):
    services = make_services(tmp_path)
    app = ViddlApp(initial_url=URL, services=services)
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.press("d")
        await settle(pilot)
        assert app.state.option is not None
        assert app.state.option.key == "v1080"


@pytest.mark.asyncio
async def test_b_goes_back_to_the_link_box(tmp_path):
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        assert isinstance(app.screen, OptionsScreen)
        await pilot.press("b")
        await settle(pilot)
        assert isinstance(app.screen, HomeScreen)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "done, total, expected",
    [(50, 100, "50%"), (2048, None, "2.0 KB")],
    ids=["percent", "no-total-from-the-server"],
)
async def test_setting_up_ffmpeg_shows_how_far_along_it_is(tmp_path, done, total, expected):
    engine = SlowFfmpegEngine(done, total)
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path, engine=engine))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.click("#get-ffmpeg")
        for _ in range(40):
            await pilot.pause()
            await asyncio.sleep(0.01)
            if "Downloading ffmpeg" in shown(app.screen, "#options-note"):
                break

        note = shown(app.screen, "#options-note")
        assert "Downloading ffmpeg" in note
        assert expected in note

        engine.release.set()
        await settle(pilot, rounds=40)
        # Once it is there, the offer goes away and the note goes back to
        # telling the user where the file will be saved.
        assert app.screen.query_one("#get-ffmpeg").has_class("hidden")
        assert "Saves to" in shown(app.screen, "#options-note")
        assert app.state.ffmpeg_available is True


@pytest.mark.asyncio
async def test_setting_up_ffmpeg_that_fails_is_explained(tmp_path):
    engine = BrokenFfmpegEngine()
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path, engine=engine))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.click("#get-ffmpeg")
        await settle(pilot)
        note = shown(app.screen, "#options-note")
        assert "Could not reach the internet." in note
        assert "Check your connection." in note
        # The offer has to come back so the user can try again.
        assert not app.screen.query_one("#get-ffmpeg").has_class("hidden")


# ---------------------------------------------------------------------------
# the progress screen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_merging_quality_fetches_ffmpeg_by_itself(tmp_path):
    engine = FakeEngine(ffmpeg=False)
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path, engine=engine))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        assert app.state.ffmpeg_available is False

        await pilot.press("enter")  # the 1080p row, which has to be merged
        await settle(pilot)

        assert engine.ffmpeg is True
        assert app.state.ffmpeg_available is True
        assert isinstance(app.screen, ResultScreen)


@pytest.mark.asyncio
async def test_the_progress_screen_reports_size_speed_and_time_left(tmp_path):
    services = make_services(tmp_path, job_class=ChattyJob)
    app = ViddlApp(initial_url=URL, services=services)
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.press("enter")
        job = await wait_for_job(app, pilot)
        await pilot.pause()

        heading = shown(app.screen, "#download-heading")
        assert "1080p" in heading
        assert "A Very Real Video" in heading
        detail = shown(app.screen, "#download-detail")
        assert " of " in detail
        assert "1.5 MB/s" in detail
        assert "0:07 left" in detail
        assert "Downloading" in shown(app.screen, "#download-status")
        assert app.screen.query_one("#download-bar").progress == 42.0

        await pilot.click("#cancel")
        await settle(pilot)
        assert job.cancelled is True
        assert isinstance(app.screen, StoppedScreen)


@pytest.mark.asyncio
async def test_cancelling_when_nothing_is_running_just_goes_back(tmp_path):
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        app.state.option = None  # as if the download screen were reopened
        app.push_screen(DownloadScreen())
        await settle(pilot)
        assert isinstance(app.screen, DownloadScreen)
        assert app.state.job is None

        await pilot.press("escape")
        await settle(pilot)
        assert isinstance(app.screen, OptionsScreen)


@pytest.mark.asyncio
async def test_quitting_during_a_download_stops_it(tmp_path):
    services = make_services(tmp_path, job_class=BlockingJob)
    app = ViddlApp(initial_url=URL, services=services)
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.press("enter")
        job = await wait_for_job(app, pilot)

        app.action_quit_app()
        await pilot.pause()
        assert job.cancelled is True
        # Quitting cannot wait for a grace period: nothing may outlive the app.
        assert job.force_cancelled is True


@pytest.mark.asyncio
async def test_a_cancel_button_leaves_the_tree_a_chance_to_stop(tmp_path):
    """The stop button is a stop, not a kill: yt-dlp gets its grace period."""
    services = make_services(tmp_path, job_class=BlockingJob)
    app = ViddlApp(initial_url=URL, services=services)
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.press("enter")
        job = await wait_for_job(app, pilot)

        await pilot.press("escape")
        await settle(pilot, rounds=12)
        assert job.cancelled is True
        assert job.force_cancelled is False


# ---------------------------------------------------------------------------
# the result screen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_result_screen_names_the_quality_and_the_size(tmp_path):
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.press("enter")
        await settle(pilot)
        assert isinstance(app.screen, ResultScreen)
        sub = shown(app.screen, "#result-sub")
        assert "1080p" in sub
        assert "KB" in sub


@pytest.mark.asyncio
async def test_the_full_path_can_be_copied_to_the_clipboard(tmp_path):
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.press("enter")
        await settle(pilot)

        copied: list = []
        app.copy_to_clipboard = lambda text: copied.append(text)
        with captured_notifications(app) as noted:
            await pilot.press("c")
            await settle(pilot, rounds=20)
        assert copied == [str(app.state.saved_path)]
        assert any("copied" in message.lower() for message in noted.messages)


@pytest.mark.asyncio
async def test_the_copy_button_copies_the_same_path(tmp_path):
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.press("enter")
        await settle(pilot)

        copied: list = []
        app.copy_to_clipboard = lambda text: copied.append(text)
        await pilot.click("#copy")
        await settle(pilot, rounds=20)
        assert copied == [str(app.state.saved_path)]


@pytest.mark.asyncio
async def test_pressing_o_opens_the_folder_in_the_file_manager(tmp_path, monkeypatch):
    launched: list = []
    monkeypatch.setattr(subprocess, "Popen", lambda command, **kwargs: launched.append(command))

    app = ViddlApp(initial_url=URL, services=make_services(tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.press("enter")
        await settle(pilot)
        await pilot.press("o")
        await settle(pilot)

    assert launched
    assert str(app.state.saved_path.parent) in launched[0]


@pytest.mark.asyncio
async def test_the_error_screen_shows_the_advice_and_starts_over(tmp_path):
    services = make_services(tmp_path, job_class=FailingJob)
    app = ViddlApp(initial_url=URL, services=services)
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.press("enter")
        await settle(pilot)
        assert "deleted" in shown(app.screen, "#result-message")

        # There is no file to open or copy, so those shortcuts must do nothing.
        await pilot.press("o", "c")
        await settle(pilot)
        assert isinstance(app.screen, ResultScreen)

        await pilot.press("escape")
        await settle(pilot)
        assert isinstance(app.screen, HomeScreen)
        assert len(app.screen_stack) == 2


@pytest.mark.asyncio
async def test_the_quit_button_on_the_error_screen_leaves(tmp_path):
    services = make_services(tmp_path, job_class=FailingJob)
    app = ViddlApp(initial_url=URL, services=services)
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.press("enter")
        await settle(pilot)
        await pilot.click("#quit")
        await pilot.pause()
        assert app.is_running is False


# ---------------------------------------------------------------------------
# opening the folder
# ---------------------------------------------------------------------------


def test_open_folder_picks_the_command_the_desktop_understands(monkeypatch, tmp_path):
    launched: list = []
    monkeypatch.setattr(subprocess, "Popen", lambda command, **kwargs: launched.append(command))
    inside = tmp_path / "Downloads" / "A Video [abc].mp4"

    for platform in ("linux", "darwin", "win32"):
        monkeypatch.setattr(sys, "platform", platform)
        assert _open_folder(inside) is None

    assert launched == [
        ["xdg-open", str(inside.parent)],
        ["open", str(inside.parent)],
        ["explorer", str(inside.parent)],
    ]


def test_open_folder_opens_a_folder_itself(monkeypatch, tmp_path):
    launched: list = []
    monkeypatch.setattr(subprocess, "Popen", lambda command, **kwargs: launched.append(command))
    monkeypatch.setattr(sys, "platform", "linux")
    folder = tmp_path / "Downloads"
    folder.mkdir()

    assert _open_folder(folder) is None
    assert launched == [["xdg-open", str(folder)]]


def test_open_folder_explains_when_it_cannot(monkeypatch, tmp_path):
    def refuse(command, **kwargs):
        raise OSError("no display available")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    problem = _open_folder(tmp_path)
    assert problem is not None
    assert "Could not open the folder" in problem
