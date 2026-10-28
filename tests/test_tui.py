"""Drive the real terminal app with Textual's test pilot.

Nothing here touches the network or spawns a downloader: the engine, the link
probe and the download job are all stand-ins, so we are testing the interface
itself — typing a link, choosing with the mouse or the keyboard, and the path
that gets shown at the end.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import ClassVar, Optional

import pytest
from textual.containers import VerticalScroll
from textual.widgets import Button, Input, Static

from viddl.download import DownloadCancelled, ProbeCancelled, Progress
from viddl.engine import Tool, ToolError
from viddl.extras import Extras
from viddl.formats import MediaInfo, MediaOption, PlaylistSummary, parse_info
from viddl.history import Entry
from viddl.network import NetworkOptions
from viddl.protocol import build_scheme_url
from viddl.tui import title as title_mod
from viddl.tui.app import (
    _SPINNER,
    DownloadScreen,
    HelpScreen,
    HomeScreen,
    LinksScreen,
    OptionsScreen,
    QueueResultScreen,
    RecentScreen,
    ResultScreen,
    Services,
    SignIn,
    StoppedScreen,
    ViddlApp,
    _failure_details,
    _key_label,
    _links_from_text,
    _open_folder,
    _play_file,
    _reveal_in_folder,
)
from viddl.tui.widgets import CURSOR_MARK, ChoiceList, ChoiceRow, ExtraToggle
from viddl.util import REDACTED

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

    #: What ``ffmpeg_size_hint`` should answer; ``None`` means the mirror did not
    #: say, which the screen has to survive without a number.
    ffmpeg_size: Optional[int] = None

    def ffmpeg_size_hint(self):
        return self.ffmpeg_size


class FakeJob:
    """Writes a real file so the result screen can show its size."""

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
        archive=None,
        title="",
        network=None,
        **kwargs,
    ):
        # Whether this was a live stream, and from where to record it (F12).
        self.is_live = bool(kwargs.get("is_live", False))
        self.live_from_start = bool(kwargs.get("live_from_start", False))
        self.recorded = None
        # What the job was asked to fetch (F7): a queue is checked by the order
        # its links were taken in, not only by what it saved.
        self.url = url
        self.option = option
        self.out_dir = Path(out_dir)
        self.on_progress = on_progress
        # What the job would have asked the browser for (F1).
        self.cookies = cookies
        # What was added to the download besides the media (F5).
        self.extras = extras
        # The download archive this run asked for, and the title the video
        # is recorded under in the recent list (F6).
        self.archive = archive
        self.title = title
        # How the download was told to use the network (F11).
        self.network = network
        self.attachments = []
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


class RetryingJob(ChattyJob):
    """Downloads, hits a temporary failure, and waits before trying again."""

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
            self.on_progress(
                Progress(
                    status="Retrying in 4s (attempt 2 of 3)…",
                    percent=42.0,
                    phase="retry",
                )
            )
        self.release.wait(timeout=10)
        raise DownloadCancelled()


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


class StubProbe:
    """The look-up the screen calls: fixed metadata, and what it was asked.

    It accepts — and records — the cookie choice, because that is the one
    declaration that changes the answer the site gives (F1).
    """

    def __init__(self, info: MediaInfo):
        self.info = info
        self.calls: list = []

    def __call__(self, engine, url, cancel=None, cookies="", **kwargs):
        self.calls.append({"url": url, "cookies": cookies})
        return self.info


def make_services(
    tmp_path: Path,
    engine=None,
    info=None,
    job_class=FakeJob,
    browsers=(),
    recent=None,
    clipboard="",
) -> Services:
    info = info if info is not None else parse_info(INFO_JSON)
    services = Services(
        engine=engine or FakeEngine(),
        probe=StubProbe(info),
        make_job=job_class,
        downloads_dir=lambda: tmp_path / "Downloads",
        # No browser unless a test says so: what is installed on the machine
        # running the suite must not change what the suite sees (F1).
        browsers=lambda: list(browsers),
    )
    if recent is not None:
        # What the recent list holds (F6), said by the test rather than read
        # from a file: the screen is what is being tested, not the file.
        services.recent = recent
    # What is on the clipboard (F8), said by the test rather than read from the
    # machine running the suite — an empty clipboard unless a test fills it.
    services.clipboard = lambda: clipboard
    return services


async def settle(pilot, rounds: int = 6) -> None:
    """Let worker threads finish and their messages be processed."""
    for _ in range(rounds):
        await pilot.pause()
        await asyncio.sleep(0.01)


async def wait_for_screen(pilot, kind, timeout: float = 5.0) -> bool:
    """Wait for a screen to be the visible one.

    A pop is not instantaneous: for a moment the app is between screens, and
    asking which one it is there answers with Textual's placeholder.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if isinstance(pilot.app.screen, kind):
            return True
        await pilot.pause()
        await asyncio.sleep(0.01)
    return isinstance(pilot.app.screen, kind)


async def wait_for(pilot, event: threading.Event, timeout: float = 5.0) -> bool:
    """Wait for a worker's event, keeping the app's own loop running.

    ``Event.wait`` in an async test blocks the event loop, and the loop is what
    lets a screen finish mounting and start its worker in the first place.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if event.is_set():
            return True
        await pilot.pause()
        await asyncio.sleep(0.01)
    return event.is_set()


def shown(screen, selector: str) -> str:
    """The plain text a Static widget is currently displaying."""
    return str(screen.query_one(selector).render())


def row_index_of(screen, key: str) -> int:
    """Where the quality row with this key sits in the list.

    Asked for by key rather than counted out, so adding an audio format (F4)
    does not silently point a test at a different row.
    """
    choices = screen.query_one("#choices", ChoiceList)
    for index in range(choices.row_count):
        value = choices.value_at(index)
        if isinstance(value, MediaOption) and value.key == key:
            return index
    raise AssertionError(f"no quality row for {key}")


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
        # 1080p and 360p, then the original audio file and seven conversions.
        assert choices.row_count == 10
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

    started: ClassVar[list] = []

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
        assert choices.row_count == 10

        # The 128 kbps MP3 row, wherever it is (rows are one line tall, no
        # border, so the row's index is its offset from the top).
        await pilot.click("#choices", offset=(4, row_index_of(app.screen, "mp3-128")))
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
async def test_audio_flag_starts_on_the_audio_rows(tmp_path):
    """And on the best of them: the site's own file, not a re-encode (F4)."""
    app = ViddlApp(initial_url=URL, prefer_audio=True, services=make_services(tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        choices = app.screen.query_one("#choices", ChoiceList)
        assert choices.highlighted_value.kind == "audio"
        assert choices.highlighted_value.key == "orig"
        assert choices.highlighted_value.keeps_source_audio is True


@pytest.mark.asyncio
async def test_a_bad_link_explains_itself(tmp_path):
    def explode(engine, url, cancel=None, cookies=""):
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
        choices.select_index(row_index_of(app.screen, "mp3-128"))
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
        # A current engine has no update worth offering (F3).
        assert app.screen.query_one("#update-engine").has_class("hidden")


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
        assert "update it" in line
        assert len(line) <= 76
        # And the advice has a button beside it, so acting on it does not mean
        # leaving the app (F3).
        assert app.screen.query_one("#update-engine").has_class("hidden") is False


@pytest.mark.asyncio
async def test_what_the_screens_say_survives_a_cp1252_console(tmp_path):
    """R10: a legacy Windows console may only know cp1252.

    Viddl's own punctuation has to fit: a box-drawing or arrow character that
    the console cannot encode would turn a screen into a traceback. (A video's
    own title is the site's text, not ours, and modern terminals show it; the
    ASCII-only path for old consoles is ``viddl --plain``.)
    """
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path))

    async with app.run_test(size=(100, 32)) as pilot:
        # A link given at startup is looked up by itself: no key press here, or
        # the next Enter would pick the highlighted quality and start a job.
        await settle(pilot)
        assert isinstance(app.screen, OptionsScreen)

        texts = [
            str(widget.render())
            for widget in app.screen.query(Static)
        ]
        texts += [str(button.label) for button in app.screen.query(Button)]
        texts += [row.plain for row in app.screen.query(ChoiceRow)]
        assert texts
        for text in texts:
            text.encode("cp1252")


@pytest.mark.asyncio
async def test_the_home_screen_says_when_it_cannot_save_its_own_files(
    tmp_path, monkeypatch
):
    """R7: a read-only home used to surface much later, as a download failure."""
    blocker = tmp_path / "blocked"
    blocker.write_text("not a folder", encoding="utf-8")
    monkeypatch.setenv("VIDDL_HOME", str(blocker / "viddl"))

    app = ViddlApp(services=make_services(tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        line = shown(app.screen, "#engine-status")
        assert "Cannot save Viddl's files" in line
        assert "viddl --doctor" in line
        # The engine still works (it was found, not fetched), so its line stays.
        assert "Ready to download" in line


@pytest.mark.asyncio
async def test_the_folder_problem_stays_on_screen_when_the_engine_fails_too(
    tmp_path, monkeypatch
):
    blocker = tmp_path / "blocked"
    blocker.write_text("not a folder", encoding="utf-8")
    monkeypatch.setenv("VIDDL_HOME", str(blocker / "viddl"))

    app = ViddlApp(services=make_services(tmp_path, engine=FakeEngine(fail=True)))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        assert "Cannot save Viddl's files" in shown(app.screen, "#engine-status")


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

        # A new job, and still the same download: the folder it writes in is
        # named after the link and quality, not after the object (R6).
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
        # Two video qualities and eight audio rows (F4).
        assert len(rows) == 10
        first = rows[0]
        assert first.row_size.startswith("~") or first.row_size.endswith(("MB", "GB"))
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
async def test_a_name_typed_where_a_link_goes_is_searched_for(tmp_path):
    """A sentence is a search now, not a refusal (F14)."""
    probed = []
    services = make_services(tmp_path)
    services.probe = lambda engine, url, cancel=None, cookies="": (
        probed.append(url) or parse_info(INFO_JSON)
    )
    app = ViddlApp(services=services)

    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()
        app.screen.query_one("#url-input").value = "this is just a sentence"
        with captured_notifications(app) as noted:
            await pilot.press("enter")
            await settle(pilot)

        assert isinstance(app.screen, OptionsScreen)
        assert probed == ["ytsearch1:this is just a sentence"]
        # From here on it is the video that answered, not the query (F14).
        assert app.state.url == URL
        note = shown(app.screen, "#options-note")
        assert 'First result for "this is just a sentence".' in note
        # Every word belonged to the name, so nothing was "left out" of it.
        assert not noted.messages, noted.messages


@pytest.mark.asyncio
async def test_the_box_opens_on_the_name_a_search_was_given(tmp_path):
    """Opening ``viddl "some song"`` shows the name, not the plumbing."""
    app = ViddlApp(initial_url="some song", services=make_services(tmp_path))

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)

        assert isinstance(app.screen, OptionsScreen)
        home = next(screen for screen in app.screen_stack if isinstance(screen, HomeScreen))
        assert home.query_one("#url-input", Input).value == "some song"
        # What is downloaded from here is the video the search found.
        assert app.state.url == URL


@pytest.mark.asyncio
async def test_a_search_downloads_the_video_it_found(tmp_path):
    """The archive and the recent list get a video, so the job gets a link."""
    jobs = []

    class WatchingJob(FakeJob):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            jobs.append(self)

    app = ViddlApp(
        initial_url="some song",
        services=make_services(tmp_path, job_class=WatchingJob),
    )

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.click("#download")
        await settle(pilot)

        assert len(jobs) == 1
        assert jobs[0].url == URL


@pytest.mark.asyncio
async def test_an_option_shaped_link_is_refused_before_any_probe(tmp_path):
    """Typing a flag at the app must not turn into a flag for yt-dlp."""
    probed = []
    services = make_services(tmp_path)
    services.probe = lambda engine, url, cancel=None, cookies="": (
        probed.append(url) or parse_info(INFO_JSON)
    )
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
    services.probe = lambda engine, url, cancel=None, cookies="": (
        probed.append(url) or parse_info(INFO_JSON)
    )
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
    services.probe = lambda engine, url, cancel=None, cookies="": (
        probed.append(url) or parse_info(INFO_JSON)
    )
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
async def test_the_audio_rows_explain_what_they_are(tmp_path):
    app = ViddlApp(initial_url=URL, prefer_audio=True, services=make_services(tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        note = shown(app.screen, "#options-note")
        assert "Audio only" in note
        # The first audio row is the site's own file, and the note says why it
        # is the best of them rather than talking about an MP3 (F4).
        assert "site's own M4A file" in note
        assert "without re-encoding" in note


@pytest.mark.asyncio
async def test_a_converted_audio_row_says_it_is_converted(tmp_path):
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        choices = app.screen.query_one("#choices", ChoiceList)
        choices.select_index(row_index_of(app.screen, "flac"))
        await pilot.pause()
        note = shown(app.screen, "#options-note")
        assert "converted to FLAC" in note
        # FLAC of a lossy source is bigger without being better, so the note
        # promises nothing the encoder cannot deliver.
        assert "cannot add quality" in note


@pytest.mark.asyncio
async def test_the_untouched_row_needs_no_ffmpeg_and_says_so(tmp_path):
    engine = FakeEngine(ffmpeg=False)
    app = ViddlApp(
        initial_url=URL, prefer_audio=True, services=make_services(tmp_path, engine=engine)
    )
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        # No ffmpeg on this machine, yet the best audio choice still works.
        assert app.screen.query_one("#get-ffmpeg").has_class("hidden")
        note = shown(app.screen, "#options-note")
        assert "without re-encoding" in note
        assert "needs ffmpeg" not in note


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
async def test_the_ffmpeg_offer_says_how_big_the_download_is(tmp_path):
    """150 MB is a decision, so the size is on the screen before the click."""
    engine = FakeEngine(ffmpeg=False)
    engine.ffmpeg_size = 152 * 1024 * 1024
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path, engine=engine))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        note = shown(app.screen, "#options-note")
        assert "needs ffmpeg" in note
        assert "about 152.0 MB" in note


@pytest.mark.asyncio
async def test_the_ffmpeg_offer_keeps_quiet_when_the_size_is_unknown(tmp_path):
    """A mirror that will not say how big it is gets no invented number."""
    engine = FakeEngine(ffmpeg=False)
    engine.ffmpeg_size = None
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path, engine=engine))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        note = shown(app.screen, "#options-note")
        assert "needs ffmpeg" in note
        assert "about" not in note
        assert "MB" not in note


# ---------------------------------------------------------------------------
# extras: subtitles, details and a cover (F5)
# ---------------------------------------------------------------------------


def extra_toggle(screen, key: str) -> ExtraToggle:
    return screen.query_one(f"#extra-{key}", ExtraToggle)


def extra_label(toggle: ExtraToggle) -> str:
    return toggle.plain


@pytest.mark.asyncio
async def test_the_extras_start_off_and_are_on_screen(tmp_path):
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)

        assert not app.state.extras
        assert shown(app.screen, "#extras-label") == "Extras"
        assert extra_label(extra_toggle(app.screen, "subs")) == "[ ] Subtitles"
        assert extra_label(extra_toggle(app.screen, "details")) == "[ ] Details"
        assert extra_label(extra_toggle(app.screen, "cover")) == "[ ] Cover image"
        # Off means off: the note says nothing about adding anything.
        assert "Also adding" not in shown(app.screen, "#options-note")


@pytest.mark.asyncio
async def test_the_options_screen_says_how_the_run_was_told_to_use_the_network(tmp_path):
    """A download slower than expected should say why, inside the app (F11)."""
    network = NetworkOptions.parse(limit_rate="2M", concurrent_fragments=8)
    app = ViddlApp(initial_url=URL, network=network, services=make_services(tmp_path))

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)

        note = shown(app.screen, "#options-note")
        assert "Network: no faster than 2M/s, 8 pieces at a time." in note


@pytest.mark.asyncio
async def test_a_run_with_no_network_choices_says_nothing_about_it(tmp_path):
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path))

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)

        assert "Network:" not in shown(app.screen, "#options-note")
        assert app.state.network == NetworkOptions()


@pytest.mark.asyncio
async def test_the_download_gets_the_network_choices_the_run_was_launched_with(tmp_path):
    network = NetworkOptions.parse(proxy="socks5://127.0.0.1:1080", limit_rate="1M")

    class WatchingJob(FakeJob):
        made: ClassVar[list] = []

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            WatchingJob.made.append(self)

    WatchingJob.made.clear()
    app = ViddlApp(
        initial_url=URL,
        network=network,
        services=make_services(tmp_path, job_class=WatchingJob),
    )

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.press("enter")
        await settle(pilot)

        assert isinstance(app.screen, ResultScreen)
        assert len(WatchingJob.made) == 1
        assert WatchingJob.made[0].network == network


@pytest.mark.asyncio
async def test_clicking_an_extra_switches_it_on(tmp_path):
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)

        await pilot.click("#extra-subs")
        await pilot.pause()

        assert app.state.extras.has("subs")
        toggle = extra_toggle(app.screen, "subs")
        assert extra_label(toggle) == "[x] Subtitles"
        # The tick is in the label, so a terminal without colour still says it.
        assert toggle.has_class("on")
        assert "Also adding: subtitles" in shown(app.screen, "#options-note")

        await pilot.click("#extra-subs")
        await pilot.pause()
        assert not app.state.extras
        assert extra_label(toggle) == "[ ] Subtitles"


@pytest.mark.asyncio
async def test_the_keyboard_switches_the_same_extras(tmp_path):
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)

        for key in ("s", "m", "t"):
            await pilot.press(key)
            await pilot.pause()

        assert app.state.extras.keys == frozenset({"subs", "details", "cover"})
        assert extra_label(extra_toggle(app.screen, "details")) == "[x] Details"
        assert "Also adding: subtitles, details and cover image" in shown(
            app.screen, "#options-note"
        )

        await pilot.press("s")
        await pilot.pause()
        assert not app.state.extras.has("subs")


@pytest.mark.asyncio
async def test_a_toggle_can_be_reached_with_tab_and_pressed(tmp_path):
    """One line is clickable; it must also work without a mouse."""

    app = ViddlApp(initial_url=URL, services=make_services(tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)

        extra_toggle(app.screen, "details").focus()
        await pilot.press("enter")
        await pilot.pause()
        assert app.state.extras.has("details")

        await pilot.press("space")
        await pilot.pause()
        assert not app.state.extras.has("details")


@pytest.mark.asyncio
async def test_the_cover_is_named_as_a_file_beside_the_video(tmp_path):
    """The row must not promise a picture inside a file it cannot put there."""

    app = ViddlApp(initial_url=URL, services=make_services(tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)

        await pilot.press("t")
        await pilot.pause()

        note = shown(app.screen, "#options-note")
        assert "cover image" in note
        assert "beside the file" in note
        assert "inside it" in note


@pytest.mark.asyncio
async def test_extras_that_need_ffmpeg_offer_to_set_it_up(tmp_path):
    """A row that needs nothing still needs ffmpeg for details (F5)."""

    engine = FakeEngine(ffmpeg=False)
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path, engine=engine))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        app.screen.query_one("#choices", ChoiceList).select_index(row_index_of(app.screen, "v360"))
        await pilot.pause()
        assert app.screen.query_one("#get-ffmpeg").has_class("hidden")

        await pilot.press("m")
        await pilot.pause()

        note = shown(app.screen, "#options-note")
        assert "The extras you ticked need ffmpeg" in note
        assert not app.screen.query_one("#get-ffmpeg").has_class("hidden")


@pytest.mark.asyncio
async def test_subtitles_say_they_cannot_go_into_an_audio_download(tmp_path):
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        app.screen.query_one("#choices", ChoiceList).select_index(row_index_of(app.screen, "orig"))
        await pilot.pause()

        with captured_notifications(app) as toasts:
            await pilot.press("s")
            await pilot.pause()

        assert app.state.extras.has("subs")
        assert "audio" in shown(app.screen, "#options-note")
        assert any("only go into a video" in message for message in toasts.messages)

        # Details are still useful in an audio file, so they are still offered.
        await pilot.press("m")
        await pilot.pause()
        assert "Also adding: details" in shown(app.screen, "#options-note")


@pytest.mark.asyncio
async def test_the_download_is_given_the_extras_that_were_ticked(tmp_path):
    RecordingJob.started.clear()
    app = ViddlApp(
        initial_url=URL, services=make_services(tmp_path, job_class=RecordingJob)
    )
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)

        await pilot.press("s", "t")
        await pilot.pause()
        await pilot.press("enter")
        await settle(pilot)

        assert len(RecordingJob.started) == 1
        assert RecordingJob.started[0].extras.keys == frozenset({"subs", "cover"})


@pytest.mark.asyncio
async def test_extras_named_on_the_command_line_start_switched_on(tmp_path):
    app = ViddlApp(
        initial_url=URL,
        extras=Extras.parse("subs"),
        services=make_services(tmp_path),
    )
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)

        assert extra_label(extra_toggle(app.screen, "subs")) == "[x] Subtitles"
        assert extra_label(extra_toggle(app.screen, "cover")) == "[ ] Cover image"


class CoverJob(FakeJob):
    """Saves a picture beside the file, as the real job does (F5)."""

    def run(self):
        path = super().run()
        cover = self.out_dir / "A Very Real Video [dQw4w9WgXcQ].jpg"
        cover.write_bytes(b"picture")
        self.attachments = [cover]
        return path


@pytest.mark.asyncio
async def test_the_result_screen_names_the_cover_that_came_with_it(tmp_path):
    app = ViddlApp(
        initial_url=URL, services=make_services(tmp_path, job_class=CoverJob)
    )
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)

        await pilot.press("t")
        await pilot.press("enter")
        await settle(pilot)

        assert isinstance(app.screen, ResultScreen)
        assert "A Very Real Video [dQw4w9WgXcQ].jpg" in shown(app.screen, "#result-sub")


class SlowProbe:
    """A look-up that takes as long as the test needs, and can be stopped."""

    def __init__(self, info: MediaInfo):
        self.info = info
        self.started = threading.Event()
        self.release = threading.Event()
        self.cancel_seen = threading.Event()
        self.calls = 0

    def __call__(self, engine, url, cancel=None, cookies=""):
        self.calls += 1
        self.started.set()
        while not self.release.is_set():
            if cancel is not None and cancel.is_set():
                # What the real probe does: the process is killed, and the user
                # is not shown a failure for something they chose.
                self.cancel_seen.set()
                raise ProbeCancelled()
            time.sleep(0.02)
        return self.info


@pytest.mark.asyncio
async def test_the_look_up_counts_the_seconds_while_it_runs(tmp_path):
    """A screen that has not changed for a minute must not look frozen."""
    probe = SlowProbe(parse_info(INFO_JSON))
    services = make_services(tmp_path)
    services.probe = probe
    app = ViddlApp(initial_url=URL, services=services)

    async with app.run_test(size=(100, 32)) as pilot:
        assert await wait_for(pilot, probe.started)
        await asyncio.sleep(1.2)
        await pilot.pause()

        status = shown(app.screen, "#options-status")
        # A mark that turns, then the clock (U8): the line never sits still.
        assert status[0] in _SPINNER, status
        assert "Looking up the link…" in status
        assert status.endswith("s"), f"no elapsed time on screen: {status!r}"
        # Under the five seconds where it stops just counting.
        assert app.screen.query_one("#stop-probe").has_class("hidden")

        probe.release.set()
        await settle(pilot, rounds=40)
        assert "Saves to" in shown(app.screen, "#options-note")


@pytest.mark.asyncio
async def test_a_look_up_that_drags_on_says_so_and_offers_a_way_out(tmp_path):
    """The 150-second call used to be unbreakable, and silent about it."""
    probe = SlowProbe(parse_info(INFO_JSON))
    services = make_services(tmp_path)
    services.probe = probe
    app = ViddlApp(initial_url=URL, services=services)

    async with app.run_test(size=(100, 32)) as pilot:
        assert await wait_for(pilot, probe.started)
        await pilot.pause()

        # Six seconds in: what the ticker would have drawn by then.
        app.screen._started_at -= 6
        app.screen.show_elapsed()
        await pilot.pause()

        assert not app.screen.query_one("#stop-probe").has_class("hidden")
        note = shown(app.screen, "#options-note")
        assert "Still looking" in note
        assert "Stop looking" in note
        # Still one line of an 86-column panel.
        assert len(note) <= 84

        probe.release.set()
        await settle(pilot, rounds=40)


@pytest.mark.asyncio
async def test_stopping_a_look_up_goes_back_to_the_link_box(tmp_path):
    probe = SlowProbe(parse_info(INFO_JSON))
    services = make_services(tmp_path)
    services.probe = probe
    app = ViddlApp(initial_url=URL, services=services)

    async with app.run_test(size=(100, 32)) as pilot:
        assert await wait_for(pilot, probe.started)
        await pilot.pause()
        app.screen._started_at -= 6
        app.screen.show_elapsed()
        await pilot.pause()

        with captured_notifications(app) as noted:
            await pilot.click("#stop-probe")
            for _ in range(60):
                await pilot.pause()
                await asyncio.sleep(0.01)
                if isinstance(app.screen, HomeScreen):
                    break

        assert await wait_for(pilot, probe.cancel_seen), "the look-up was left running"
        assert await wait_for_screen(pilot, HomeScreen)
        assert any("Stopped looking" in message for message in noted.messages)


@pytest.mark.asyncio
async def test_going_back_while_looking_stops_the_look_up(tmp_path):
    """Escape is also a way of saying "not this one"."""
    probe = SlowProbe(parse_info(INFO_JSON))
    services = make_services(tmp_path)
    services.probe = probe
    app = ViddlApp(initial_url=URL, services=services)

    async with app.run_test(size=(100, 32)) as pilot:
        assert await wait_for(pilot, probe.started)
        await pilot.pause()
        await pilot.press("escape")

        assert await wait_for(pilot, probe.cancel_seen), "a stopped look-up ran on"
        assert await wait_for_screen(pilot, HomeScreen), "it did not go back"


@pytest.mark.asyncio
async def test_a_quick_look_up_never_mentions_waiting(tmp_path):
    """The hint is for a slow site, not a decoration on every link."""
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        assert app.screen.query_one("#stop-probe").has_class("hidden")
        assert "Still looking" not in shown(app.screen, "#options-note")


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
        note = shown(app.screen, "#options-note")
        assert "This is a playlist of 2 videos" in note
        assert "Download takes the first one" in note
        # The rest of the list is one click away, and the button only appears
        # for a link that has more to offer (F2).
        assert app.screen.query_one("#playlist").has_class("hidden") is False


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
async def test_the_progress_bar_has_a_row_to_be_drawn_in(tmp_path):
    """The bar was laid out at zero height, so it was never drawn (U9).

    A row of padding above and below on a widget one row tall left nothing for
    the widget itself, and nothing is what every download showed: a percentage,
    a speed, a Cancel button, and an empty row where the bar should be. The
    height is the thing to hold — it can only be zero when the bar is
    invisible, whatever its colours are doing.
    """
    services = make_services(tmp_path, job_class=ChattyJob)
    app = ViddlApp(initial_url=URL, services=services)
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.press("enter")
        await wait_for_job(app, pilot)
        await pilot.pause()

        bar = app.screen.query_one("#download-bar")
        assert bar.size.height == 1
        # And it takes the panel, rather than sitting in Textual's 32 cells.
        assert bar.region.width >= 80

        await pilot.click("#cancel")
        await settle(pilot)
        assert isinstance(app.screen, StoppedScreen)


@pytest.mark.asyncio
async def test_the_screen_says_when_it_is_waiting_to_try_again(tmp_path):
    services = make_services(tmp_path, job_class=RetryingJob)
    app = ViddlApp(initial_url=URL, services=services)
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.press("enter")
        await wait_for_job(app, pilot)
        await pilot.pause()

        assert "Retrying in 4s (attempt 2 of 3)" in shown(app.screen, "#download-status")
        # Waiting is not progress: the bar keeps the place the download reached,
        # and the speed and time-left figures are cleared rather than frozen.
        assert app.screen.query_one("#download-bar").progress == 42.0
        assert shown(app.screen, "#download-detail") == ""

        await pilot.click("#cancel")
        await settle(pilot)
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

# ---------------------------------------------------------------------------
# signing in with the browser's cookies (F1)
#
# The row exists because some links answer only to a signed-in session. It is
# not a quality, so what these tests hold is: it appears for the browsers that
# are installed, choosing it re-asks the site rather than downloading, the
# interface says what is being read, and the download then uses it.
# ---------------------------------------------------------------------------


def sign_in_rows_of(screen) -> list:
    choices = screen.query_one("#choices", ChoiceList)
    return [
        choices.value_at(index)
        for index in range(choices.row_count)
        if isinstance(choices.value_at(index), SignIn)
    ]


def sign_in_index(screen) -> int:
    """Where the sign-in row is: last, after every quality (F1, F4)."""
    return screen.query_one("#choices", ChoiceList).row_count - 1


@pytest.mark.asyncio
async def test_a_browser_that_is_installed_is_offered_as_a_row(tmp_path):
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path, browsers=["chrome"]))

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        choices = app.screen.query_one("#choices", ChoiceList)

        assert choices.row_count == 11, "ten qualities and one sign-in row"
        row = choices.value_at(10)
        assert isinstance(row, SignIn)
        assert row.label == "Sign in with Chrome"
        assert row.browser == "chrome"

        choices.select_index(sign_in_index(app.screen))
        await pilot.pause()
        note = shown(app.screen, "#options-note")
        # The whole sentence, because it is the only place this decision is
        # explained: what is read, how much of it, and that nothing is kept.
        assert "cookies it already has" in note
        assert "for this download only" in note
        assert "password is never read" in note
        assert "nothing is saved" in note


@pytest.mark.asyncio
async def test_every_browser_found_gets_its_own_row(tmp_path):
    """Which session to read is the user's decision, not a guess."""
    services = make_services(tmp_path, browsers=["chrome", "firefox", "safari"])
    app = ViddlApp(initial_url=URL, services=services)

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)

        labels = [row.label for row in sign_in_rows_of(app.screen)]

        assert labels == [
            "Sign in with Chrome",
            "Sign in with Firefox",
            "Sign in with Safari",
        ]


@pytest.mark.asyncio
async def test_no_browser_means_no_sign_in_row(tmp_path):
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path))

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)

        assert app.screen.query_one("#choices", ChoiceList).row_count == 10
        assert sign_in_rows_of(app.screen) == []


@pytest.mark.asyncio
async def test_choosing_the_row_re_asks_the_site_signed_in(tmp_path):
    probe = StubProbe(parse_info(INFO_JSON))
    services = make_services(tmp_path, browsers=["chrome"])
    services.probe = probe
    app = ViddlApp(initial_url=URL, services=services)

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        choices = app.screen.query_one("#choices", ChoiceList)
        choices.select_index(sign_in_index(app.screen))
        await pilot.press("enter")
        await settle(pilot)

        assert app.state.cookies_browser == "chrome"
        assert [call["cookies"] for call in probe.calls] == ["", "chrome"]
        # The state is visible: the row says what it is doing, and so does the
        # heading — a download reading an account's cookies must not look like
        # one that is not.
        assert choices.value_at(sign_in_index(app.screen)).label == "Signing in with Chrome"
        assert "signed in with Chrome" in shown(app.screen, "#options-heading")

        choices.select_index(sign_in_index(app.screen))
        await pilot.pause()
        assert "Using Chrome's cookies" in shown(app.screen, "#options-note")


@pytest.mark.asyncio
async def test_the_cookies_can_be_turned_off_again(tmp_path):
    probe = StubProbe(parse_info(INFO_JSON))
    services = make_services(tmp_path, browsers=["chrome"])
    services.probe = probe
    app = ViddlApp(initial_url=URL, services=services)

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        choices = app.screen.query_one("#choices", ChoiceList)
        for _ in range(2):
            choices.select_index(sign_in_index(app.screen))
            await pilot.press("enter")
            await settle(pilot)

        assert app.state.cookies_browser == ""
        assert [call["cookies"] for call in probe.calls] == ["", "chrome", ""]
        assert choices.value_at(sign_in_index(app.screen)).label == "Sign in with Chrome"


@pytest.mark.asyncio
async def test_the_app_can_start_already_signed_in(tmp_path):
    probe = StubProbe(parse_info(INFO_JSON))
    services = make_services(tmp_path, browsers=["chrome"])
    services.probe = probe
    app = ViddlApp(initial_url=URL, cookies="chrome", services=services)

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)

        assert probe.calls[0]["cookies"] == "chrome"
        assert app.screen.query_one("#choices", ChoiceList).value_at(
            sign_in_index(app.screen)
        ).label == ("Signing in with Chrome")


@pytest.mark.asyncio
async def test_a_name_that_is_not_a_browser_is_not_used(tmp_path):
    """A flag arriving on the command line must not become a cookie read."""
    app = ViddlApp(
        initial_url=URL, cookies="--exec=calc.exe", services=make_services(tmp_path)
    )

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)

        assert app.state.cookies_browser == ""


@pytest.mark.asyncio
async def test_a_download_uses_the_browser_that_signed_it_in(tmp_path):
    RecordingJob.started.clear()
    services = make_services(tmp_path, browsers=["brave"], job_class=RecordingJob)
    app = ViddlApp(initial_url=URL, services=services)

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        choices = app.screen.query_one("#choices", ChoiceList)
        choices.select_index(sign_in_index(app.screen))
        await pilot.press("enter")  # sign in
        await settle(pilot)
        await pilot.press("enter")  # take the first quality
        await settle(pilot)

        assert RecordingJob.started, "no download was started"
        assert RecordingJob.started[0].cookies == "brave"


@pytest.mark.asyncio
async def test_the_download_button_does_not_download_the_sign_in_row(tmp_path):
    """The row is a thing to click, not a thing to download."""
    RecordingJob.started.clear()
    services = make_services(tmp_path, browsers=["chrome"], job_class=RecordingJob)
    app = ViddlApp(initial_url=URL, services=services)

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        app.screen.query_one("#choices", ChoiceList).select_index(
            sign_in_index(app.screen)
        )
        await pilot.pause()

        with captured_notifications(app) as noted:
            await pilot.click("#download")
            await pilot.pause()

        assert RecordingJob.started == []
        assert any("Choose a quality" in message for message in noted.messages)

class SignInThenWork:
    """A look-up that fails the way a private video does, then succeeds.

    This is the whole point of the feature: the anonymous look-up is refused by
    the site, and the same link answers once a browser's cookies are read.
    """

    def __init__(self, info: MediaInfo):
        self.info = info
        self.calls: list = []

    def __call__(self, engine, url, cancel=None, cookies="", **kwargs):
        self.calls.append(cookies)
        if not cookies:
            raise ToolError(
                "The site wants you to be signed in to watch this one.",
                "Choose \u201cSign in with my browser\u201d and Viddl will read the "
                "cookies that browser already has.",
            )
        return self.info


@pytest.mark.asyncio
async def test_a_link_that_needs_a_sign_in_offers_the_row_and_then_works(tmp_path):
    """A hint that says \"choose the sign-in row\" needs a row to choose (F1)."""
    probe = SignInThenWork(parse_info(INFO_JSON))
    services = make_services(tmp_path, browsers=["firefox"])
    services.probe = probe
    app = ViddlApp(initial_url=URL, services=services)

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)

        # The look-up failed, and the way out is in the list rather than only
        # in the sentence above it.
        assert "signed in" in shown(app.screen, "#options-status")
        choices = app.screen.query_one("#choices", ChoiceList)
        assert choices.row_count == 1
        assert choices.value_at(0).label == "Sign in with Firefox"

        await pilot.press("enter")
        await settle(pilot)

        assert probe.calls == ["", "firefox"]
        assert app.state.cookies_browser == "firefox"
        # Same screen, now with the qualities the site only shows signed in.
        assert app.screen.query_one("#choices", ChoiceList).row_count == 11
        assert "signed in with Firefox" in shown(app.screen, "#options-heading")


@pytest.mark.asyncio
async def test_a_failed_look_up_with_no_browser_offers_nothing_to_choose(tmp_path):
    probe = SignInThenWork(parse_info(INFO_JSON))
    services = make_services(tmp_path)
    services.probe = probe
    app = ViddlApp(initial_url=URL, services=services)

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)

        assert isinstance(app.screen, OptionsScreen)
        assert app.screen.query_one("#choices", ChoiceList).row_count == 0
        # Advice that points at a row this computer cannot offer is replaced
        # with what is actually missing.
        note = shown(app.screen, "#options-note")
        assert "needs a browser on this computer" in note
        assert "Sign in with my browser" not in note


# ---------------------------------------------------------------------------
# the recent list (F6)
#
# What was saved lately, with the three things anybody does with it: look at
# where the file is, open its folder or copy the path, and take it again —
# which is the same road a pasted link takes, no more and no less.
# ---------------------------------------------------------------------------


def recent_entries(tmp_path: Path):
    """Two rows, one of them an hour old, standing in for the file on disk."""
    now = time.time()
    return [
        Entry(
            path=tmp_path / "Downloads" / "A Very Real Video [dQw4w9WgXcQ].mp4",
            title="A Very Real Video",
            url=URL,
            size=2048,
            when=now,
        ),
        Entry(
            path=tmp_path / "Downloads" / "Another Video [abc123].mp4",
            title="Another Video",
            url="https://www.youtube.com/watch?v=abc123",
            size=1024,
            when=now - 3600,
        ),
    ]


class AlreadyHadJob(FakeJob):
    """A download the archive recognised: the file is real, no fetch happened."""

    def run(self):
        path = super().run()
        self.already_had = True
        return path


@pytest.mark.asyncio
async def test_the_home_screen_offers_the_recent_list(tmp_path):
    app = ViddlApp(services=make_services(tmp_path))

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        assert isinstance(app.screen, HomeScreen)

        await pilot.click("#recent")
        await settle(pilot)
        assert isinstance(app.screen, RecentScreen)


@pytest.mark.asyncio
async def test_the_recent_key_belongs_to_the_screen_not_the_link_box(tmp_path):
    """`r` opens the list — and while the link box is being typed in, it is
    a letter somebody is typing."""
    app = ViddlApp(services=make_services(tmp_path, recent=lambda: recent_entries(tmp_path)))

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)

        # Typing into the link box is typing into the link box.
        await pilot.press("r")
        assert isinstance(app.screen, HomeScreen)
        assert app.screen.query_one("#url-input", Input).value == "r"

        # Away from the box, the key is the shortcut the footer promises.
        app.screen.query_one("#url-input", Input).value = ""
        app.screen.query_one("#recent", Button).focus()
        await pilot.press("r")
        await settle(pilot)
        assert isinstance(app.screen, RecentScreen)


@pytest.mark.asyncio
async def test_the_recent_screen_lists_what_was_saved(tmp_path):
    entries = recent_entries(tmp_path)
    app = ViddlApp(services=make_services(tmp_path, recent=lambda: entries))

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.click("#recent")
        await settle(pilot)

        rows = list(app.screen.query(".choice-row"))
        assert [row.row_label for row in rows] == ["A Very Real Video", "Another Video"]
        assert rows[0].row_size == "2.0 KB"
        assert "1 h ago" in rows[1].row_note
        assert "2 saved downloads" in shown(app.screen, "#recent-sub")
        # The question this screen answers, answered for the highlighted row.
        assert str(entries[0].path) in shown(app.screen, "#recent-note")


@pytest.mark.asyncio
async def test_a_recent_row_takes_the_link_back_to_the_qualities(tmp_path):
    """The same road a pasted link takes: probed and offered, never fetched."""
    services = make_services(tmp_path, recent=lambda: recent_entries(tmp_path))
    probe = services.probe
    app = ViddlApp(services=services)

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.click("#recent")
        await settle(pilot)

        await pilot.press("enter")
        await settle(pilot)

        assert isinstance(app.screen, OptionsScreen)
        assert app.state.url == URL
        assert probe.calls and probe.calls[0]["url"] == URL
        assert app.state.job is None, "a row started a download of its own"


@pytest.mark.asyncio
async def test_a_recent_row_whose_file_is_gone_is_shown_and_marked(tmp_path):
    entries = [
        Entry(
            path=tmp_path / "Downloads" / "Deleted Film [gone].mp4",
            title="Deleted Film",
            url=URL,
            size=1024,
            when=time.time(),
            missing=True,
        )
    ]
    app = ViddlApp(services=make_services(tmp_path, recent=lambda: entries))

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.click("#recent")
        await settle(pilot)

        rows = list(app.screen.query(".choice-row"))
        assert len(rows) == 1, "a row whose file is gone is history, not nothing"
        assert "file is gone" in rows[0].row_note
        assert "no longer on disk" in shown(app.screen, "#recent-note")


@pytest.mark.asyncio
async def test_the_empty_recent_list_says_so(tmp_path):
    app = ViddlApp(services=make_services(tmp_path, recent=lambda: []))

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.click("#recent")
        await settle(pilot)

        assert shown(app.screen, "#recent-sub") == "Nothing saved yet."
        assert "Download something and it will appear" in shown(app.screen, "#recent-note")
        for button_id in ("redownload", "open", "copy"):
            assert app.screen.query_one(f"#{button_id}", Button).disabled


@pytest.mark.asyncio
async def test_an_entry_without_a_link_says_so_rather_than_opening_an_empty_box(tmp_path):
    """Only reachable through a hand-edited list — and it still must not lie."""
    entries = [
        Entry(path=tmp_path / "Downloads" / "Old [file].mp4", title="Old", url="", size=1)
    ]
    app = ViddlApp(services=make_services(tmp_path, recent=lambda: entries))

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.click("#recent")
        await settle(pilot)

        with captured_notifications(app) as toasts:
            await pilot.press("enter")
            await settle(pilot)

        assert isinstance(app.screen, RecentScreen), "an empty box was opened instead"
        assert app.state.url == ""
        assert any("does not remember its link" in message for message in toasts.messages)


@pytest.mark.asyncio
async def test_a_recent_row_opens_its_folder_and_copies_its_path(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(
        "viddl.tui.app._open_folder", lambda path: opened.append(path) or None
    )
    entries = recent_entries(tmp_path)
    app = ViddlApp(services=make_services(tmp_path, recent=lambda: entries))

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.click("#recent")
        await settle(pilot)

        await pilot.press("o")
        assert opened == [entries[0].path]

        copied = []
        app.copy_to_clipboard = lambda text: copied.append(text)
        await pilot.press("c")
        await settle(pilot)
        assert copied == [str(entries[0].path)]

        # And the second row is the second row's file, not the first's.
        await pilot.press("down")
        await settle(pilot)
        await pilot.press("o")
        assert opened == [entries[0].path, entries[1].path]


@pytest.mark.asyncio
async def test_opening_the_recent_list_never_touches_the_engine(tmp_path):
    """Looking at what you have is not asking the site for anything."""
    engine = FakeEngine()
    app = ViddlApp(
        services=make_services(tmp_path, engine=engine, recent=lambda: recent_entries(tmp_path))
    )

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        calls = engine.ytdlp_calls
        await pilot.click("#recent")
        await settle(pilot)

        assert isinstance(app.screen, RecentScreen)
        assert engine.ytdlp_calls == calls


@pytest.mark.asyncio
async def test_the_result_screen_says_already_had_rather_than_a_quality_it_lacks(tmp_path):
    """The archive handed back a file fetched at some other quality; the row
    would otherwise claim a quality the file never had."""
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path, job_class=AlreadyHadJob))

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.press("enter")
        await settle(pilot)

        assert isinstance(app.screen, ResultScreen)
        sub = shown(app.screen, "#result-sub")
        assert sub.startswith("Already had")
        assert "1080p" not in sub
        assert "2.0 KB" in sub


# ---------------------------------------------------------------------------
# the link the clipboard already holds (F8)
#
# The home screen asks once, offers what it found, and stops. Nothing is filled
# in and nothing is looked up until the person says yes — the same promise the
# app makes to a link arriving from a web page (S5), extended to one arriving
# from the clipboard.
# ---------------------------------------------------------------------------

CLIPBOARD_URL = "https://www.youtube.com/watch?v=clipboard123"


@pytest.mark.asyncio
async def test_a_link_on_the_clipboard_is_offered_and_nothing_else_happens(tmp_path):
    app = ViddlApp(services=make_services(tmp_path, clipboard=CLIPBOARD_URL))

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)

        offer = shown(app.screen, "#clipboard-offer")
        assert "youtube.com" in offer
        assert "Paste link" in offer
        # Never silent, never automatic: the box is still empty, and the app is
        # still on the screen it started on.
        assert app.screen.query_one("#url-input", Input).value == ""
        assert isinstance(app.screen, HomeScreen)
        assert app.screen.query_one("#paste", Button).has_class("hidden") is False


@pytest.mark.asyncio
async def test_the_paste_button_fills_the_box_and_does_not_submit(tmp_path):
    app = ViddlApp(services=make_services(tmp_path, clipboard=CLIPBOARD_URL))

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.click("#paste")
        await settle(pilot)

        assert app.screen.query_one("#url-input", Input).value == CLIPBOARD_URL
        # Filling is the whole action: the qualities are a keypress away, and
        # the keypress belongs to the person.
        assert isinstance(app.screen, HomeScreen)
        # And the offer is spent, so it cannot be pasted twice by accident.
        assert app.screen.clipboard_link == ""
        assert shown(app.screen, "#clipboard-offer") == ""
        assert app.screen.query_one("#paste", Button).has_class("hidden")


@pytest.mark.asyncio
async def test_the_paste_key_belongs_to_the_screen_not_the_link_box(tmp_path):
    """`p` pastes — and while the link box is being typed in, it is a letter."""
    app = ViddlApp(services=make_services(tmp_path, clipboard=CLIPBOARD_URL))

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)

        await pilot.press("p")
        assert app.screen.query_one("#url-input", Input).value == "p"
        assert isinstance(app.screen, HomeScreen)

        app.screen.query_one("#url-input", Input).value = ""
        app.screen.query_one("#recent", Button).focus()
        await pilot.press("p")
        await settle(pilot)

        assert app.screen.query_one("#url-input", Input).value == CLIPBOARD_URL
        assert isinstance(app.screen, HomeScreen)


@pytest.mark.asyncio
async def test_a_clipboard_without_a_link_offers_nothing(tmp_path):
    app = ViddlApp(services=make_services(tmp_path, clipboard="a shopping list"))

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)

        assert shown(app.screen, "#clipboard-offer") == ""
        assert app.screen.query_one("#paste", Button).has_class("hidden")
        with captured_notifications(app) as toast:
            app.screen.query_one("#recent", Button).focus()
            await pilot.press("p")
            await settle(pilot)
        assert "The clipboard has no link to use." in toast.messages


@pytest.mark.asyncio
async def test_the_offer_shows_the_host_and_not_the_whole_link(tmp_path):
    """A copied address can carry a signed token; the screen is read over shoulders."""
    link = "https://example.com/watch?v=abc&token=SUPERSECRET"
    app = ViddlApp(services=make_services(tmp_path, clipboard=link))

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)

        offer = shown(app.screen, "#clipboard-offer")
        assert "example.com" in offer
        assert "SUPERSECRET" not in offer

        # The link itself is kept whole for the paste that was asked for.
        app.screen.query_one("#recent", Button).focus()
        await pilot.press("p")
        assert app.screen.query_one("#url-input", Input).value == link


@pytest.mark.asyncio
async def test_opening_with_a_link_does_not_ask_the_clipboard_at_all(tmp_path):
    asked = []

    def clipboard():
        asked.append(True)
        return CLIPBOARD_URL

    services = make_services(tmp_path)
    services.clipboard = clipboard
    app = ViddlApp(initial_url=URL, services=services)

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)

        # The app was opened with a link; an offer on top of it would be noise,
        # and reading a clipboard nobody asked about is not free.
        assert asked == []
        assert isinstance(app.screen, OptionsScreen)


@pytest.mark.asyncio
async def test_a_clipboard_that_cannot_be_read_is_not_an_error(tmp_path):
    def broken():
        raise OSError("no clipboard here")

    services = make_services(tmp_path)
    services.clipboard = broken
    app = ViddlApp(services=services)

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)

        assert isinstance(app.screen, HomeScreen)
        assert shown(app.screen, "#clipboard-offer") == ""
        # The app still works, which is the whole point of failing quietly.
        app.screen.query_one("#url-input", Input).value = URL
        await pilot.press("enter")
        assert await wait_for_screen(pilot, OptionsScreen)


# ---------------------------------------------------------------------------
# several links at once (F7)
#
# A batch is pasted rather than typed, and it lands on a screen where the list
# can be read, added to and cut down before anything is asked of a site. The
# quality is one decision for the whole list, in the order the links were added.
# ---------------------------------------------------------------------------

BATCH = [
    "https://www.youtube.com/watch?v=one111",
    "https://www.youtube.com/watch?v=two222",
    "https://vimeo.com/987654",
]


async def open_queue_screen(pilot, app, links) -> LinksScreen:
    """Paste a batch on the home screen and land on the queue screen."""
    app.screen.query_one("#url-input", Input).value = "  ".join(links)
    await pilot.press("enter")
    await settle(pilot)
    assert isinstance(app.screen, LinksScreen), type(app.screen)
    return app.screen


@pytest.mark.asyncio
async def test_several_links_pasted_at_once_open_the_queue_screen(tmp_path):
    """Nothing is looked up on the way there: the list comes first."""
    probed = []
    services = make_services(tmp_path)
    services.probe = lambda engine, url, cancel=None, cookies="": (
        probed.append(url) or parse_info(INFO_JSON)
    )
    app = ViddlApp(services=services)

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        screen = await open_queue_screen(pilot, app, BATCH)

        assert app.state.links == BATCH
        assert probed == []
        assert screen.query_one("#choices", ChoiceList).row_count == 3
        assert "3 links" in shown(screen, "#options-note")
        assert "in order" in shown(screen, "#options-note")
        # One host per row, and the part that tells two links to the same host
        # apart, so a batch of three YouTube links is not three identical rows.
        labels = [row.plain for row in screen.query(ChoiceRow)]
        assert "1. www.youtube.com" in labels[0]
        assert labels[0] != labels[1]  # same site, different video


@pytest.mark.asyncio
async def test_a_stray_word_between_links_is_left_out_and_said_so(tmp_path):
    app = ViddlApp(services=make_services(tmp_path))

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        with captured_notifications(app) as toast:
            # A comma after a link, and a word that is not one: the comma goes,
            # the word is counted, both links survive.
            await open_queue_screen(pilot, app, [BATCH[0] + ",", "and", BATCH[1]])

        assert app.state.links == BATCH[:2]
        assert any("did not look like a link" in message for message in toast.messages)
        assert any("1 of 3" in message for message in toast.messages)


@pytest.mark.asyncio
async def test_the_same_link_twice_is_one_download(tmp_path):
    app = ViddlApp(services=make_services(tmp_path))

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        app.screen.query_one("#url-input", Input).value = f"{URL} {URL}"
        await pilot.press("enter")
        await settle(pilot)

        # One link is one video, whatever it was pasted twice.
        assert isinstance(app.screen, OptionsScreen)
        assert app.state.links == []
        assert app.state.url == URL


@pytest.mark.asyncio
async def test_a_link_can_be_added_and_taken_out_of_the_queue(tmp_path):
    app = ViddlApp(services=make_services(tmp_path))

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        screen = await open_queue_screen(pilot, app, BATCH[:2])

        # Adding keeps the focus in the box, so a batch can be pasted in a row.
        field = screen.query_one("#link-input", Input)
        field.focus()
        field.value = BATCH[2]
        await pilot.press("enter")
        await settle(pilot)
        assert screen.links == BATCH
        assert screen.query_one("#choices", ChoiceList).row_count == 3
        assert app.focused is field
        assert field.value == ""

        # The same link again is refused rather than queued twice.
        with captured_notifications(app) as toast:
            field.value = BATCH[0]
            await pilot.press("enter")
            await settle(pilot)
        assert any("already in the list" in message for message in toast.messages)
        assert screen.links == BATCH

        # Taking one out leaves the rest, in order.
        screen.query_one("#choices", ChoiceList).focus()
        screen.query_one("#choices", ChoiceList).select_index(1)
        await pilot.press("delete")
        await settle(pilot)
        assert screen.links == [BATCH[0], BATCH[2]]
        assert "2 links" in shown(screen, "#options-note")


@pytest.mark.asyncio
async def test_clearing_the_queue_leaves_nothing_to_download(tmp_path):
    app = ViddlApp(services=make_services(tmp_path))

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        screen = await open_queue_screen(pilot, app, BATCH)

        screen.query_one("#choices", ChoiceList).focus()
        await pilot.press("c")
        await settle(pilot)

        assert screen.links == []
        assert "No links yet." in shown(screen, "#options-note")
        for button_id in ("download", "remove", "clear"):
            assert screen.query_one(f"#{button_id}", Button).disabled

        with captured_notifications(app) as toast:
            await pilot.press("d")
            await settle(pilot)
        assert "Add at least one link first." in toast.messages
        assert isinstance(app.screen, LinksScreen)


@pytest.mark.asyncio
async def test_the_queue_takes_every_link_in_order_at_one_quality(tmp_path):
    RecordingJob.started.clear()
    services = make_services(tmp_path, job_class=RecordingJob)
    app = ViddlApp(services=services)

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await open_queue_screen(pilot, app, BATCH[:2])

        # The list has the focus; Enter on it takes the whole list.
        await pilot.press("enter")
        assert await wait_for_screen(pilot, OptionsScreen)
        await settle(pilot)

        assert "2 links" in shown(app.screen, "#options-note")
        assert "this quality is used for all of them" in shown(app.screen, "#options-note")

        options = app.screen.query_one("#choices", ChoiceList)
        options.focus()
        await pilot.press("enter")
        assert await wait_for_screen(pilot, QueueResultScreen)
        await settle(pilot, rounds=12)

        assert [job.url for job in RecordingJob.started] == BATCH[:2]
        # A batch of links is not a playlist, and the summary says so.
        assert shown(app.screen, "#brand") == "All links saved"
        assert "Saved all 2 videos." in shown(app.screen, "#result-sub")


@pytest.mark.asyncio
async def test_choose_videos_is_not_offered_for_a_batch_of_links(tmp_path):
    """A batch is already a list; a link's playlist must not replace it."""
    info = parse_info(INFO_JSON)
    info.playlist = PlaylistSummary(title="A playlist", count=4, url=URL)
    info.playlist_note = "A playlist, 4 videos"
    services = make_services(tmp_path, info=info)
    app = ViddlApp(services=services)

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await open_queue_screen(pilot, app, BATCH[:2])
        await pilot.press("d")
        assert await wait_for_screen(pilot, OptionsScreen)
        await settle(pilot)

        assert app.screen.query_one("#playlist", Button).has_class("hidden")
        assert "Choose videos" not in shown(app.screen, "#options-note")


def test_a_batch_is_split_on_whitespace_and_nothing_else():
    """A comma or a dot is legal inside an address, so neither may split one."""
    links, left_out = _links_from_text(
        "https://example.com/clip.mp4  https://example.com/watch?v=a,b"
    )

    assert links == [
        "https://example.com/clip.mp4",
        "https://example.com/watch?v=a,b",
    ]
    assert left_out == 0


def test_punctuation_after_a_link_is_dropped_but_the_link_is_not():
    """A link copied from the end of a sentence arrives with the sentence's comma."""
    links, left_out = _links_from_text("see https://example.com/clip.mp4, then")

    assert links == ["https://example.com/clip.mp4"]
    assert left_out == 2  # "see" and "then"


# ---------------------------------------------------------------------------
# live streams (F12)
# ---------------------------------------------------------------------------


def live_info() -> MediaInfo:
    info = parse_info(INFO_JSON)
    info.is_live = True
    return info


class RecordedJob(BlockingJob):
    """A live recording that is stopped: the job delivered what it caught."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.recorded = self.out_dir / "A Live Stream [abc].mp4"

    def run(self):
        self.release.wait(timeout=10)
        self.recorded.parent.mkdir(parents=True, exist_ok=True)
        self.recorded.write_bytes(b"r" * 100)
        raise DownloadCancelled()

    def keep_recording(self):
        return self.recorded


@pytest.mark.asyncio
async def test_a_live_link_says_so_before_the_download_starts(tmp_path):
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path, info=live_info()))

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)

        note = shown(app.screen, "#live-note")
        assert "Live stream" in note
        assert "starts now, not from the beginning" in note
        assert "what has been recorded is saved" in note
        button = app.screen.query_one("#live-start", Button)
        assert not button.has_class("hidden")
        assert str(button.label) == "Record from the start"


@pytest.mark.asyncio
async def test_an_ordinary_link_says_nothing_about_live_streams(tmp_path):
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path))

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)

        assert app.screen.query_one("#live-note", Static).has_class("hidden")
        assert app.screen.query_one("#live-start", Button).has_class("hidden")


@pytest.mark.asyncio
async def test_the_start_button_switches_which_part_of_the_stream_is_recorded(tmp_path):
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path, info=live_info()))

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)

        await pilot.click("#live-start")
        await pilot.pause()

        assert app.state.live_from_start is True
        button = app.screen.query_one("#live-start", Button)
        assert str(button.label) == "Recording from the start"
        assert "from the beginning" in shown(app.screen, "#live-note")

        await pilot.press("l")
        await pilot.pause()
        assert app.state.live_from_start is False
        assert str(button.label) == "Record from the start"


@pytest.mark.asyncio
async def test_the_key_does_nothing_when_there_is_no_stream_to_start(tmp_path):
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path))

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)

        await pilot.press("l")
        await pilot.pause()

        assert app.state.live_from_start is False


@pytest.mark.asyncio
async def test_the_download_is_told_it_is_live_and_where_to_start(tmp_path):
    class WatchingJob(FakeJob):
        made: ClassVar[list] = []

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            WatchingJob.made.append(self)

    WatchingJob.made.clear()
    app = ViddlApp(
        initial_url=URL,
        services=make_services(tmp_path, info=live_info(), job_class=WatchingJob),
    )

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.click("#live-start")
        await pilot.pause()
        # `d`, not Enter: the click left the focus on the start button, where
        # Enter would switch it back off again.
        await pilot.press("d")
        await settle(pilot)

        assert isinstance(app.screen, ResultScreen)
        assert len(WatchingJob.made) == 1
        assert WatchingJob.made[0].is_live is True
        assert WatchingJob.made[0].live_from_start is True


@pytest.mark.asyncio
async def test_stopping_a_live_recording_shows_what_was_saved(tmp_path):
    app = ViddlApp(
        initial_url=URL,
        services=make_services(tmp_path, info=live_info(), job_class=RecordedJob),
    )

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.press("enter")
        job = await wait_for_job(app, pilot)
        # A live recording is stopped by the person, not finished by the site.
        assert str(app.screen.query_one("#cancel", Button).label) == "Stop recording"

        await pilot.press("escape")
        assert await wait_for_screen(pilot, ResultScreen)

        path = tmp_path / "Downloads" / "A Live Stream [abc].mp4"
        assert shown(app.screen, "#brand") == "Recording stopped"
        assert shown(app.screen, "#result-path") == str(path)
        assert "what had been recorded when you stopped" in shown(app.screen, "#result-sub")
        assert job.recorded == path


@pytest.mark.asyncio
async def test_a_live_recording_that_could_not_be_put_in_one_file_is_explained(tmp_path):
    app = ViddlApp(
        initial_url=URL,
        services=make_services(tmp_path, info=live_info(), job_class=BlockingJob),
    )

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.press("enter")
        await wait_for_job(app, pilot)
        await pilot.press("escape")
        assert await wait_for_screen(pilot, StoppedScreen)
        await settle(pilot)

        message = shown(app.screen, "#stopped-message")
        assert "This was a live stream" in message
        assert "nothing to carry on from" in message
        # Resume would record from the live point, which is a new file: it is
        # not offered, and the key cannot reach it.
        assert not list(app.screen.query("#resume"))
        await pilot.press("r")
        assert isinstance(app.screen, StoppedScreen)


# ---------------------------------------------------------------------------
# a remembered quality (F13)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_qualities_screen_starts_on_the_remembered_row(tmp_path):
    app = ViddlApp(
        initial_url=URL, prefer_quality="360", services=make_services(tmp_path)
    )

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)

        chosen = app.screen.query_one("#choices", ChoiceList).highlighted_value
        assert isinstance(chosen, MediaOption)
        assert chosen.key == "v360"
        # The cursor moved; nothing was decided. Download still takes the row
        # under the cursor, which is now this one.
        assert chosen.label == "360p"


@pytest.mark.asyncio
async def test_a_remembered_quality_this_link_lacks_keeps_the_best_row(tmp_path):
    """A preference must never turn a working link into a failure (F13)."""
    app = ViddlApp(
        initial_url=URL, prefer_quality="ultra", services=make_services(tmp_path)
    )

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)

        chosen = app.screen.query_one("#choices", ChoiceList).highlighted_value
        assert isinstance(chosen, MediaOption)
        assert chosen.height == 1080


@pytest.mark.asyncio
async def test_a_remembered_quality_can_be_audio(tmp_path):
    app = ViddlApp(
        initial_url=URL, prefer_quality="audio", services=make_services(tmp_path)
    )

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)

        chosen = app.screen.query_one("#choices", ChoiceList).highlighted_value
        assert isinstance(chosen, MediaOption)
        assert chosen.kind == "audio"


@pytest.mark.asyncio
async def test_the_audio_flag_beats_a_remembered_quality(tmp_path):
    app = ViddlApp(
        initial_url=URL,
        prefer_audio=True,
        prefer_quality="720",
        services=make_services(tmp_path),
    )

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)

        chosen = app.screen.query_one("#choices", ChoiceList).highlighted_value
        assert isinstance(chosen, MediaOption)
        assert chosen.kind == "audio"


# ---------------------------------------------------------------------------
# the window title (U2)
# ---------------------------------------------------------------------------


def collected(app):
    """Everything the app sends to the terminal, from this moment on.

    The headless driver throws its writes away and renders nothing through it,
    so this is escape sequences rather than screen content — but not only ours:
    Textual writes one of its own (the cursor shape). :func:`titles` is the
    filter, and it is what the assertions below use.
    """
    written: list = []
    app._driver.write = written.append
    return written


def titles(written) -> list:
    """The title texts among the writes, in order."""
    prefix, suffix = title_mod.OSC, title_mod.BEL
    return [
        data[len(prefix) : -len(suffix)]
        for data in written
        if data.startswith(prefix) and data.endswith(suffix)
    ]


@pytest.mark.asyncio
async def test_the_window_says_what_the_download_is_doing(tmp_path):
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path, job_class=ChattyJob))

    async with app.run_test(size=(100, 32)) as pilot:
        written = collected(app)
        await settle(pilot)
        await pilot.press("enter")
        await wait_for_job(app, pilot)
        await settle(pilot)

        # One whole sequence, with the numbers and the name from the link.
        assert "\x1b]0;Viddl — 42% — A Very Real Video\x07" in written
        assert titles(written) == ["Viddl — 42% — A Very Real Video"]

        # Stopping it hands the title back rather than leaving 42% behind.
        await pilot.click("#cancel")
        await settle(pilot)
        assert app.window_title.current == "Viddl"
        assert titles(written)[-1] == "Viddl"


@pytest.mark.asyncio
async def test_the_title_returns_to_the_apps_name_when_the_download_ends(tmp_path):
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path))

    async with app.run_test(size=(100, 32)) as pilot:
        written = collected(app)
        await settle(pilot)
        await pilot.press("enter")
        assert await wait_for_screen(pilot, ResultScreen)
        await settle(pilot)

        assert "Viddl — 50% — A Very Real Video" in titles(written)
        assert app.window_title.current == "Viddl"
        assert titles(written)[-1] == "Viddl"


@pytest.mark.asyncio
async def test_quitting_gives_the_title_bar_back(tmp_path):
    """The app must not leave "42%" in the task bar after it has gone."""
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path, job_class=ChattyJob))
    written: list = []

    async with app.run_test(size=(100, 32)) as pilot:
        written = collected(app)
        await settle(pilot)
        await pilot.press("enter")
        await wait_for_job(app, pilot)
        await settle(pilot)
        assert "42%" in app.window_title.current

        await pilot.press("ctrl+q")
        await settle(pilot, rounds=12)

    # Closing said the app's name, popped the terminal's title stack, and then
    # stopped: a screen torn down during shutdown must not write over the title
    # the shell has just got back.
    assert app.window_title.restored is True
    assert titles(written)[-1] == "Viddl"
    assert written[-1] == title_mod.POP


# ---------------------------------------------------------------------------
# the done screen says more, and the failure screen can say why (U3)
# ---------------------------------------------------------------------------


class VerboseFailingJob(FakeJob):
    """Fails the way a real download does: with the tool's own words attached."""

    raw = (
        "ERROR: unable to download video data: HTTP Error 403: Forbidden\n"
        "WARNING: link has a token: https://x.test/v?token=SECRET123\n"
        "Killing\x1b]0;pwned\x07 Time"
    )

    def run(self):
        raise ToolError(
            "The site refused the request (403).",
            "This is often a moment's throttling rather than a real block.",
            raw=self.raw,
        )


def failed_app(tmp_path, job_class=VerboseFailingJob) -> ViddlApp:
    """An app that will fail the way a real download does."""
    services = make_services(tmp_path, job_class=job_class)
    return ViddlApp(initial_url=URL, services=services)


async def reach_failure(pilot) -> None:
    await settle(pilot)
    await pilot.press("enter")
    await settle(pilot)


@pytest.mark.asyncio
async def test_the_done_screen_names_the_container_and_the_length(tmp_path):
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.press("enter")
        await settle(pilot)

        assert isinstance(app.screen, ResultScreen)
        sub = shown(app.screen, "#result-sub")
        # The quality that was picked, what the file actually is, how long the
        # video is, and how big the download was.
        assert "1080p" in sub
        assert "MP4" in sub
        assert "3:32" in sub
        assert "KB" in sub


class Mp3Job(FakeJob):
    """A download that really lands an .mp3, so the file agrees with the row."""

    def run(self):
        self.out_dir.mkdir(parents=True, exist_ok=True)
        target = self.out_dir / "A Very Real Video [dQw4w9WgXcQ].mp3"
        target.write_bytes(b"x" * 2048)
        return target


@pytest.mark.asyncio
async def test_an_audio_row_does_not_say_its_format_twice(tmp_path):
    """The row already says "MP3 192 kbps"; the file's own suffix adds nothing."""
    services = make_services(tmp_path, job_class=Mp3Job)
    app = ViddlApp(initial_url=URL, prefer_quality="mp3-192", services=services)
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.press("enter")
        await settle(pilot)

        sub = shown(app.screen, "#result-sub")
        assert "MP3 192 kbps" in sub
        assert sub.count("MP3") == 1
        assert "3:32" in sub


@pytest.mark.asyncio
async def test_the_play_button_opens_the_file_in_the_desktops_player(tmp_path, monkeypatch):
    launched: list = []
    monkeypatch.setattr(subprocess, "Popen", lambda command, **kwargs: launched.append(command))
    monkeypatch.setattr(sys, "platform", "linux")

    app = ViddlApp(initial_url=URL, services=make_services(tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.press("enter")
        await settle(pilot)
        await pilot.click("#play")
        await settle(pilot)

    assert launched == [["xdg-open", str(app.state.saved_path)]]


@pytest.mark.asyncio
async def test_p_plays_it_too(tmp_path, monkeypatch):
    launched: list = []
    monkeypatch.setattr(subprocess, "Popen", lambda command, **kwargs: launched.append(command))
    monkeypatch.setattr(sys, "platform", "linux")

    app = ViddlApp(initial_url=URL, services=make_services(tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.press("enter")
        await settle(pilot)
        await pilot.press("p")
        await settle(pilot)

    assert launched and launched[0][-1] == str(app.state.saved_path)


def test_play_picks_the_command_the_desktop_understands(monkeypatch, tmp_path):
    launched: list = []
    monkeypatch.setattr(subprocess, "Popen", lambda command, **kwargs: launched.append(command))
    played: list = []
    monkeypatch.setattr(os, "startfile", lambda path: played.append(path), raising=False)
    inside = tmp_path / "A Video [abc].mp4"

    for platform in ("linux", "darwin", "win32"):
        monkeypatch.setattr(sys, "platform", platform)
        assert _play_file(inside) is None

    assert launched == [["xdg-open", str(inside)], ["open", str(inside)]]
    assert played == [str(inside)]


def test_play_explains_when_the_desktop_will_not_open_it(monkeypatch, tmp_path):
    def refuse(command, **kwargs):
        raise OSError("no display available")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(sys, "platform", "linux")
    problem = _play_file(tmp_path / "clip.mp4")

    assert problem is not None
    assert "Could not play the file" in problem


def test_show_in_folder_picks_the_file_out_where_the_desktop_can(monkeypatch, tmp_path):
    """Windows and macOS have "select this file"; Linux has only the folder."""
    launched: list = []
    monkeypatch.setattr(subprocess, "Popen", lambda command, **kwargs: launched.append(command))
    inside = tmp_path / "Downloads" / "A Video [abc].mp4"

    for platform in ("linux", "darwin", "win32"):
        monkeypatch.setattr(sys, "platform", platform)
        assert _reveal_in_folder(inside) is None

    assert launched == [
        ["xdg-open", str(inside.parent)],
        ["open", "-R", str(inside)],
        ["explorer", f"/select,{inside}"],
    ]


@pytest.mark.asyncio
async def test_the_details_are_closed_until_they_are_asked_for(tmp_path):
    app = failed_app(tmp_path)
    async with app.run_test(size=(100, 32)) as pilot:
        await reach_failure(pilot)
        panel = app.screen.query_one("#result-details")
        button = app.screen.query_one("#details", Button)
        assert panel.has_class("hidden")
        assert str(button.label) == "Show details"

        await pilot.press("d")
        await pilot.pause()
        assert not panel.has_class("hidden")
        assert str(button.label) == "Hide details"

        await pilot.press("d")
        await pilot.pause()
        assert panel.has_class("hidden")
        assert str(button.label) == "Show details"


@pytest.mark.asyncio
async def test_the_details_quote_the_downloader_and_the_explanation(tmp_path):
    app = failed_app(tmp_path)
    async with app.run_test(size=(100, 32)) as pilot:
        await reach_failure(pilot)
        await pilot.click("#details")
        await pilot.pause()

        details = shown(app.screen, "#details-text")
        # What Viddl made of it, then what the tool actually said.
        assert "What Viddl made of it" in details
        assert "The site refused the request (403)." in details
        assert "What the downloader said" in details
        assert "HTTP Error 403: Forbidden" in details


@pytest.mark.asyncio
async def test_the_details_cannot_carry_an_escape_sequence_or_a_secret(tmp_path):
    app = failed_app(tmp_path)
    async with app.run_test(size=(100, 32)) as pilot:
        await reach_failure(pilot)
        await pilot.click("#details")
        await pilot.pause()

        details = shown(app.screen, "#details-text")
        # The tool's output can quote a site's name, and a control character
        # in it would be obeyed by the terminal rather than drawn (U2's rule).
        assert "\x1b" not in details
        assert "\x07" not in details
        assert "pwned" in details
        # And the same lines can carry the signed link Viddl was handed.
        assert "SECRET123" not in details
        assert REDACTED in details


@pytest.mark.asyncio
async def test_the_details_can_be_copied_for_a_bug_report(tmp_path):
    app = failed_app(tmp_path)
    async with app.run_test(size=(100, 32)) as pilot:
        await reach_failure(pilot)

        copied: list = []
        app.copy_to_clipboard = lambda text: copied.append(text)
        await pilot.click("#copy-details")
        await settle(pilot, rounds=20)

        assert copied
        assert "HTTP Error 403: Forbidden" in copied[0]
        assert "SECRET123" not in copied[0]


@pytest.mark.asyncio
async def test_c_copies_the_details_when_there_is_no_path(tmp_path):
    app = failed_app(tmp_path)
    async with app.run_test(size=(100, 32)) as pilot:
        await reach_failure(pilot)

        copied: list = []
        app.copy_to_clipboard = lambda text: copied.append(text)
        await pilot.press("c")
        await settle(pilot, rounds=20)

        assert copied and "The site refused the request" in copied[0]


@pytest.mark.asyncio
async def test_a_failure_with_nothing_to_add_still_explains_itself(tmp_path):
    app = failed_app(tmp_path, job_class=FailingJob)
    async with app.run_test(size=(100, 32)) as pilot:
        await reach_failure(pilot)
        await pilot.press("d")
        await pilot.pause()

        details = shown(app.screen, "#details-text")
        assert "That video is not available any more." in details
        assert "It may have been deleted." in details
        assert "What the downloader said" not in details


def test_the_details_point_at_the_log_when_there_is_one(tmp_path):
    from viddl import appdata

    error = ToolError("It did not work.", "Try again.", raw="ERROR: no")
    assert "The log file has the rest" not in _failure_details(error)

    log = appdata.log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("a line\n", encoding="utf-8")

    details = _failure_details(error)
    assert "The log file has the rest" in details
    assert str(log) in details


def test_a_log_that_holds_nothing_is_not_offered_as_having_the_rest(tmp_path):
    """The file exists from start-up, so existence alone promised nothing (E3)."""
    from viddl import appdata

    error = ToolError("It did not work.", "Try again.", raw="ERROR: no")
    log = appdata.log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("", encoding="utf-8")

    assert "The log file has the rest" not in _failure_details(error)


def test_play_on_windows_explains_when_the_shell_will_not_open_it(monkeypatch, tmp_path):
    def refuse(path):
        raise OSError("no associated program")

    monkeypatch.setattr(os, "startfile", refuse, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    problem = _play_file(tmp_path / "clip.mp4")

    assert problem is not None
    assert "Could not play the file" in problem


@pytest.mark.asyncio
async def test_a_refused_play_says_so_rather_than_nothing(tmp_path, monkeypatch):
    def refuse(command, **kwargs):
        raise OSError("no display available")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(sys, "platform", "linux")

    app = ViddlApp(initial_url=URL, services=make_services(tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.press("enter")
        await settle(pilot)
        with captured_notifications(app) as noted:
            await pilot.press("p")
            await settle(pilot)

        assert any("Could not play the file" in message for message in noted.messages)


@pytest.mark.asyncio
async def test_a_refused_reveal_says_so_rather_than_nothing(tmp_path, monkeypatch):
    def refuse(command, **kwargs):
        raise OSError("no display available")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(sys, "platform", "linux")

    app = ViddlApp(initial_url=URL, services=make_services(tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.press("enter")
        await settle(pilot)
        with captured_notifications(app) as noted:
            await pilot.press("o")
            await settle(pilot)

        assert any("Could not show the file" in message for message in noted.messages)


# ---------------------------------------------------------------------------
# the key page, and trying a failure again (U5)
# ---------------------------------------------------------------------------


def rows_of(page: str) -> dict:
    """The key page as {key: what it does}, however wide its columns are."""
    rows = {}
    for line in page.splitlines():
        if line.startswith("  ") and line.strip():
            key, _, what = line.strip().partition("  ")
            rows[key.strip()] = what.strip()
    return rows


def test_a_key_is_written_the_way_a_person_reads_it():
    # Textual's own names, and how they are spelled out: ``question_mark`` is
    # what a question mark becomes when it is bound to (U5).
    assert _key_label("ctrl+q") == "Ctrl+Q"
    assert _key_label("escape") == "Esc"
    assert _key_label("question_mark") == "?"
    assert _key_label("f1") == "F1"
    assert _key_label("shift+tab") == "Shift+Tab"
    assert _key_label("pagedown") == "PgDn"
    # A key on its own is written as it is typed, not shouted.
    assert _key_label("k") == "k"


@pytest.mark.asyncio
async def test_the_keys_page_says_what_travels_and_what_happens_here(tmp_path):
    app = ViddlApp(services=make_services(tmp_path))
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(pilot)
        await pilot.press("f1")
        await settle(pilot)

        assert isinstance(app.screen, HelpScreen)
        page = shown(app.screen, "#help-text")
        assert page.splitlines()[0] == "Anywhere"
        assert "On this screen" in page
        rows = rows_of(page)
        # The app's own, wherever you are.
        assert rows["Ctrl+Q"] == "Quit"
        assert rows["F1"] == "Keys"
        # And the home screen's.
        assert rows["r"] == "Recent"
        assert rows["p"] == "Paste link"
        assert rows["Ctrl+T"] == "Theme"
        # The text box's own editing keys are the box's, not Viddl's: a page of
        # Ctrl+A, Ctrl+X and Shift+Home would bury the five keys that matter.
        assert "Ctrl+X" not in rows


@pytest.mark.asyncio
async def test_the_keys_page_names_the_keys_the_footer_hides(tmp_path):
    """The failure screen keeps Play, Copy and Details off its footer on
    purpose, so the page is the only place they can be found (U5)."""
    app = failed_app(tmp_path)
    async with app.run_test(size=(80, 24)) as pilot:
        await reach_failure(pilot)
        # Nothing here is a text box, so the question mark opens the page
        # itself; F1 is the way in from the home screen (see above).
        await pilot.press("question_mark")
        await settle(pilot)
        assert isinstance(app.screen, HelpScreen)

        rows = rows_of(shown(app.screen, "#help-text"))
        assert rows["o"] == "Show in folder"
        assert rows["p"] == "Play"
        assert rows["c"] == "Copy"
        assert rows["d"] == "Details"
        assert rows["r"] == "Try again"
        assert rows["Esc"] == "Start over"


@pytest.mark.asyncio
async def test_a_key_keeps_the_meaning_the_focused_list_gives_it(tmp_path):
    """Enter on the qualities list chooses a row; on the screen it downloads.

    Both are bound, and only one of them runs — the page has to say which.
    """
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path))
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(pilot)
        assert isinstance(app.screen, OptionsScreen)
        await pilot.press("f1")
        await settle(pilot)

        page = shown(app.screen, "#help-text")
        rows = rows_of(page)
        assert rows["Enter"] == "Choose"
        assert page.count("Enter") == 1, "a key listed under two meanings"
        # `d` still starts the download: nothing shadows it.
        assert rows["d"] == "Download"


@pytest.mark.asyncio
async def test_the_page_opens_and_closes_with_either_key(tmp_path):
    app = ViddlApp(services=make_services(tmp_path))
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(pilot)
        # In the link box a question mark is a character being typed — the rule
        # that keeps `p` and `r` letters while the box has the focus (F8).
        await pilot.press("question_mark")
        await settle(pilot)
        assert isinstance(app.screen, HomeScreen)
        assert app.screen.query_one("#url-input").value == "?"

        await pilot.press("f1")
        await settle(pilot)
        assert isinstance(app.screen, HelpScreen)

        await pilot.press("question_mark")
        await settle(pilot)
        assert isinstance(app.screen, HomeScreen)

        await pilot.press("f1")
        await settle(pilot)
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("escape")
        await settle(pilot)
        assert isinstance(app.screen, HomeScreen)


@pytest.mark.asyncio
async def test_the_page_fits_a_small_terminal_and_scrolls_the_rest(tmp_path):
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path))
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(pilot)
        await pilot.press("f1")
        await settle(pilot)

        panel = app.screen.query_one("#help-panel")
        listing = app.screen.query_one("#help-list", VerticalScroll)
        footer = app.screen.query_one("Footer")
        assert panel.region.width <= 80
        assert panel.region.height <= 24
        # The list is capped and scrolls, so a screen with more keys than fit
        # cannot push the footer off the bottom.
        assert listing.region.height == 14
        assert listing.virtual_size.height > listing.region.height
        assert footer.region.y + footer.region.height <= 24


@pytest.mark.asyncio
async def test_a_failed_download_can_be_tried_again(tmp_path):
    class CountingFailingJob(FailingJob):
        made: ClassVar[list] = []

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            CountingFailingJob.made.append(self)

    CountingFailingJob.made.clear()
    app = failed_app(tmp_path, job_class=CountingFailingJob)
    async with app.run_test(size=(80, 24)) as pilot:
        await reach_failure(pilot)
        assert isinstance(app.screen, ResultScreen)
        option = app.state.option
        assert option is not None

        await pilot.press("r")
        await settle(pilot)
        await wait_for_screen(pilot, ResultScreen)

        # The same link at the same quality, asked for a second time.
        assert len(CountingFailingJob.made) == 2
        assert CountingFailingJob.made[1].option is option
        assert app.state.url == URL
        # One failure screen, not a stack of them: trying again replaces it.
        assert len(app.screen_stack) == 4
        assert sum(isinstance(screen, DownloadScreen) for screen in app.screen_stack) == 0


@pytest.mark.asyncio
async def test_the_try_again_button_does_the_same(tmp_path):
    class CountingFailingJob(FailingJob):
        made: ClassVar[list] = []

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            CountingFailingJob.made.append(self)

    CountingFailingJob.made.clear()
    app = failed_app(tmp_path, job_class=CountingFailingJob)
    async with app.run_test(size=(80, 24)) as pilot:
        await reach_failure(pilot)
        await pilot.click("#retry")
        await settle(pilot)
        await wait_for_screen(pilot, ResultScreen)

        assert len(CountingFailingJob.made) == 2


@pytest.mark.asyncio
async def test_the_failure_buttons_fit_an_eighty_column_terminal(tmp_path):
    app = failed_app(tmp_path)
    async with app.run_test(size=(80, 24)) as pilot:
        await reach_failure(pilot)

        buttons = list(app.screen.query("Button"))
        assert [button.id for button in buttons] == [
            "retry",
            "details",
            "copy-details",
            "again",
            "quit",
        ]
        for button in buttons:
            assert button.region.x >= 0, button.id
            assert button.region.x + button.region.width <= 80, button.id


@pytest.mark.asyncio
async def test_a_saved_download_is_not_tried_again(tmp_path):
    """The key belongs to a failure; on the done screen it does nothing."""
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path))
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(pilot)
        await pilot.press("enter")
        await settle(pilot)
        assert isinstance(app.screen, ResultScreen)
        saved = app.state.saved_path

        await pilot.press("r")
        await settle(pilot)
        assert isinstance(app.screen, ResultScreen)
        assert app.state.saved_path == saved


# ---------------------------------------------------------------------------
# the same screen with the colour turned off (U6)
# ---------------------------------------------------------------------------


def marked_rows(screen) -> list:
    """The rows whose own text says "the cursor is here"."""
    return [row for row in screen.query(ChoiceRow) if str(row.render()).startswith(CURSOR_MARK)]


@pytest.mark.asyncio
async def test_the_row_under_the_cursor_is_marked_with_a_character(tmp_path, monkeypatch):
    """Colour off must not be information off (U6).

    NO_COLOR takes the background away from the chosen row, and the bold that
    is left is easy to miss on a dim screen — so the row carries a character.
    """
    monkeypatch.setenv("NO_COLOR", "1")
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path))
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(pilot)
        rows = list(app.screen.query(ChoiceRow))
        assert marked_rows(app.screen) == [rows[0]]

        await pilot.press("down")
        await settle(pilot)
        assert marked_rows(app.screen) == [rows[1]]

        await pilot.press("end")
        await settle(pilot)
        assert marked_rows(app.screen) == [rows[-1]]


@pytest.mark.asyncio
async def test_only_one_row_is_marked_and_it_fits_the_terminal(tmp_path):
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path))
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(pilot)
        rows = list(app.screen.query(ChoiceRow))
        marked = marked_rows(app.screen)
        assert len(marked) == 1
        # The mark costs two columns, and the row still ends inside an
        # eighty-column terminal: long notes are clipped rather than pushed
        # off the edge.
        for row in rows:
            assert row.region.x + row.region.width <= 80
        assert marked[0].region.x + marked[0].region.width <= 80


# ---------------------------------------------------------------------------
# motion, emptiness and feedback (U8)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_look_up_line_moves_before_it_can_count(tmp_path):
    """A clock that changes once a second is not motion; the mark is (U8)."""
    probe = SlowProbe(parse_info(INFO_JSON))
    services = make_services(tmp_path)
    services.probe = probe
    app = ViddlApp(initial_url=URL, services=services)

    async with app.run_test(size=(80, 24)) as pilot:
        assert await wait_for(pilot, probe.started)
        screen = app.screen
        # Only the calls below turn the mark, whatever the timer is doing.
        screen._ticker.stop()

        frames = []
        for _ in range(len(_SPINNER)):
            screen.show_elapsed()
            frames.append(shown(screen, "#options-status")[0])

        assert sorted(frames) == sorted(_SPINNER), frames
        status = shown(screen, "#options-status")
        assert "Looking up the link…" in status
        assert status.endswith("s")
        # One line, inside the panel at 80 columns.
        assert len(status) <= 74, status

        probe.release.set()
        await settle(pilot, rounds=40)


@pytest.mark.asyncio
async def test_a_size_the_site_only_estimated_is_marked_as_one(tmp_path):
    """The figure that changes halfway through is announced before it does."""
    app = ViddlApp(services=make_services(tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        app.state.option = None  # as if the screen were reopened on its own
        app.push_screen(DownloadScreen())
        await settle(pilot)
        screen = app.screen
        assert isinstance(screen, DownloadScreen)

        screen.on_progress(
            Progress(
                percent=25.0,
                downloaded=1024,
                total=4096,
                total_is_estimate=True,
                phase="download",
            )
        )
        await pilot.pause()
        estimated = shown(screen, "#download-detail")
        assert " of ~" in estimated, estimated

        # The site states the real size: the mark goes, and the number with it.
        screen.on_progress(
            Progress(
                percent=25.0,
                downloaded=1024,
                total=8192,
                total_is_estimate=False,
                phase="download",
            )
        )
        await pilot.pause()
        stated = shown(screen, "#download-detail")
        assert " of ~" not in stated, stated
        assert "8" in stated


@pytest.mark.asyncio
async def test_the_bar_stops_short_of_100_until_the_download_is_finished(tmp_path):
    """An estimate that was too small must not read as "finished" (U8)."""
    app = ViddlApp(services=make_services(tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        app.state.option = None
        app.push_screen(DownloadScreen())
        await settle(pilot)
        screen = app.screen
        bar = screen.query_one("#download-bar")

        screen.on_progress(Progress(percent=100.0, phase="download"))
        await pilot.pause()
        assert bar.progress == 99.0

        screen.on_progress(Progress(percent=100.0, phase="done"))
        await pilot.pause()
        assert bar.progress == 100.0


@pytest.mark.asyncio
async def test_an_ending_that_beats_the_first_frame_does_not_flash(tmp_path):
    """A download that finishes before the screen paints never shows it (U8).

    "Painted" is the screen's own answer, so the two halves of this are the
    same call with the flag it would have set: replaced before the first frame,
    stacked on top of it afterwards.
    """
    path = tmp_path / "A Very Real Video [dQw4w9WgXcQ].mp4"
    path.write_bytes(b"x")

    for painted, stacked in ((False, False), (True, True)):
        app = ViddlApp(services=make_services(tmp_path))
        async with app.run_test(size=(100, 32)) as pilot:
            await settle(pilot)
            app.state.option = None
            app.push_screen(DownloadScreen())
            await settle(pilot)
            screen = app.screen
            assert isinstance(screen, DownloadScreen)

            screen._painted = painted
            screen.finish_ok(path)
            await settle(pilot)

            assert isinstance(app.screen, ResultScreen)
            under = [s for s in app.screen_stack if isinstance(s, DownloadScreen)]
            assert bool(under) is stacked


@pytest.mark.asyncio
async def test_the_home_screen_gives_an_example_while_the_box_is_empty(tmp_path):
    """An empty box is a state, and it says what a link looks like (U8)."""
    app = ViddlApp(services=make_services(tmp_path))
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(pilot)
        assert URL in shown(app.screen, "#field-label")
        assert shown(app.screen, "#field-label").startswith("No link yet")

        # Still the smallest terminal the app claims, with the example in it.
        panel = app.screen.query_one("#home-panel")
        assert panel.region.width <= 80
        assert panel.region.height <= 24

        await pilot.press("h")
        await settle(pilot)
        typed = shown(app.screen, "#field-label")
        assert "No link yet" not in typed, "the example stayed behind the typing"
        assert typed.startswith("Paste a link")

        await pilot.press("backspace")
        await settle(pilot)
        assert shown(app.screen, "#field-label").startswith("No link yet")

        # Going home empties the box, and the example comes back with it.
        await pilot.press("h")
        await settle(pilot)
        app.screen.reset()
        await settle(pilot)
        assert shown(app.screen, "#field-label").startswith("No link yet")


@pytest.mark.asyncio
async def test_a_link_in_the_box_at_the_start_is_not_an_empty_state(tmp_path):
    """The app opened on a link already has an answer to "what do I type"."""
    app = ViddlApp(initial_url=URL, services=make_services(tmp_path))
    async with app.run_test(size=(80, 24)) as pilot:
        home = next(screen for screen in app.screen_stack if isinstance(screen, HomeScreen))
        assert "No link yet" not in shown(home, "#field-label")
        await settle(pilot)


# ---------------------------------------------------------------------------
# saying so when a long download ends (F9)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_progress_screen_can_promise_to_tell_you(tmp_path):
    """The key is where the waiting is, and the answer outlives the window."""
    from viddl import config as config_mod

    app = ViddlApp(initial_url=URL, services=make_services(tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        app.state.option = None
        app.push_screen(DownloadScreen())
        await settle(pilot)
        assert app.state.notify is False

        await pilot.press("n")
        await pilot.pause()
        assert app.state.notify is True
        assert "notify = true" in config_mod.config_path().read_text(encoding="utf-8")

        await pilot.press("n")
        await pilot.pause()
        assert app.state.notify is False
        assert "notify = false" in config_mod.config_path().read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_a_finished_download_tells_the_desktop_when_asked(tmp_path, monkeypatch):
    from viddl.tui import app as app_mod

    told = []
    monkeypatch.setattr(
        app_mod.notify_mod, "tell", lambda **kwargs: told.append(kwargs) or "bell"
    )

    app = ViddlApp(initial_url=URL, services=make_services(tmp_path), notify=True)
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        app.state.option = None
        app.push_screen(DownloadScreen())
        await settle(pilot)
        screen = app.screen
        assert isinstance(screen, DownloadScreen)
        # As if the download had been running for a while.
        screen._started_at = 0.0
        screen.finish_ok(Path("/tmp/clip.mp4"))
        await pilot.pause()

    assert told and told[0]["saved"] == "/tmp/clip.mp4"


@pytest.mark.asyncio
async def test_a_finished_download_stays_quiet_by_default(tmp_path, monkeypatch):
    from viddl.tui import app as app_mod

    told = []
    monkeypatch.setattr(
        app_mod.notify_mod, "tell", lambda **kwargs: told.append(kwargs) or "bell"
    )

    app = ViddlApp(initial_url=URL, services=make_services(tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        app.state.option = None
        app.push_screen(DownloadScreen())
        await settle(pilot)
        app.screen._started_at = 0.0
        app.screen.finish_error(ToolError("The site refused the request."))
        await pilot.pause()

    assert told == []
