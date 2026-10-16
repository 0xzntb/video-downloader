"""Drive the real terminal app with Textual's test pilot.

Nothing here touches the network or spawns a downloader: the engine, the link
probe and the download job are all stand-ins, so we are testing the interface
itself — typing a link, choosing with the mouse or the keyboard, and the path
that gets shown at the end.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

from viddl.download import DownloadCancelled, Progress
from viddl.engine import Tool, ToolError
from viddl.formats import parse_info
from viddl.tui.app import (
    DownloadScreen,
    HomeScreen,
    OptionsScreen,
    ResultScreen,
    Services,
    ViddlApp,
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

    def __init__(self, ffmpeg: bool = True, fail: bool = False):
        self.ffmpeg = ffmpeg
        self.fail = fail
        self.ytdlp_calls = 0

    def ensure_ytdlp(self, on_status=None, on_progress=None, allow_download=True):
        self.ytdlp_calls += 1
        if self.fail:
            raise ToolError("Could not reach the internet.", "Check your connection.")
        if on_status:
            on_status("Using the video engine found on your computer.")
        return Tool("yt-dlp", ["/fake/yt-dlp"], "system", version="2099.01.01")

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

    def cancel(self):
        self.cancelled = True


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

    def cancel(self):
        self.cancelled = True
        self.release.set()


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
        assert isinstance(app.screen, OptionsScreen)


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
