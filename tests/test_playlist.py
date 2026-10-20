"""Playlists: listing them, ticking videos, and downloading them in order (F2).

Nothing here touches the network. The site's listing is a JSON fixture, the
downloads are stand-ins, and the terminal app is driven by Textual's pilot —
so what is tested is the promise: a playlist link is read as its first video
unless the user asks for more, and asking for more downloads exactly what was
ticked, one at a time, with nothing quietly missing from the end.
"""

from __future__ import annotations

import asyncio
import io
import threading
import time
from pathlib import Path

import pytest
from textual.widgets import Button

from viddl import download as download_mod
from viddl import plain
from viddl.download import DownloadCancelled, DownloadJob, probe_playlist
from viddl.engine import Tool, ToolError
from viddl.formats import (
    MediaInfo,
    MediaOption,
    Playlist,
    PlaylistEntry,
    parse_info,
    parse_playlist,
)
from viddl.queue import DONE, FAILED, SKIPPED, STOPPED, QueueJob
from viddl.tui.app import (
    DownloadScreen,
    OptionsScreen,
    PlaylistScreen,
    QueueResultScreen,
    ResultScreen,
    Services,
    ViddlApp,
)
from viddl.tui.widgets import ChoiceList

URL = "https://www.youtube.com/playlist?list=PLtest"
ENTRY_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

INFO_JSON = {
    "title": "A Very Real Video",
    "uploader": "Some Channel",
    "duration": 212,
    "webpage_url": ENTRY_URL,
    "extractor_key": "Youtube",
    "formats": [
        {"format_id": "140", "ext": "m4a", "audio_ext": "m4a", "vcodec": "none",
         "acodec": "mp4a", "abr": 128, "filesize": 1600000},
        {"format_id": "137", "ext": "mp4", "vcodec": "avc1", "acodec": "none",
         "height": 1080, "fps": 30, "vbr": 5000, "filesize": 20000000},
        {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a",
         "abr": 128, "filesize": 1600000},
    ],
}

FLAT = {
    "_type": "playlist",
    "title": "A Playlist",
    "playlist_count": 3,
    "webpage_url": URL,
    "entries": [
        {"_type": "url", "id": "one", "url": "https://example.invalid/watch?v=one",
         "title": "First video", "duration": 61},
        {"_type": "url", "id": "two", "url": "https://example.invalid/watch?v=two",
         "title": "Second video", "duration": 62},
        {"_type": "url", "id": "three", "url": "https://example.invalid/watch?v=three",
         "title": "Third video"},
    ],
}


def flat(**overrides) -> dict:
    data = {**FLAT, "entries": [dict(entry) for entry in FLAT["entries"]]}
    data.update(overrides)
    return data


def media() -> MediaInfo:
    """What a look-up of a three-video playlist link hands back."""
    return parse_info(
        {
            "title": "A Playlist",
            "playlist_count": 3,
            "webpage_url": URL,
            "entries": [dict(INFO_JSON)],
        }
    )


def listing() -> Playlist:
    return parse_playlist(flat())


def video_option() -> MediaOption:
    return MediaOption(
        key="v1080",
        label="1080p",
        kind="video",
        needs_ffmpeg=False,
        format_selector="137+140",
    )


# ---------------------------------------------------------------------------
# the listing itself
# ---------------------------------------------------------------------------


def test_a_flat_listing_becomes_a_checklist_in_site_order():
    playlist = parse_playlist(flat())

    assert playlist.title == "A Playlist"
    assert [entry.title for entry in playlist.entries] == [
        "First video",
        "Second video",
        "Third video",
    ]
    assert [entry.index for entry in playlist.entries] == [1, 2, 3]
    assert playlist.entries[0].url == "https://example.invalid/watch?v=one"
    assert playlist.count == 3
    assert playlist.truncated is False
    assert playlist.heading == "A Playlist  ·  3 videos"


def test_a_long_playlist_says_how_much_of_it_is_on_screen():
    """A cut list must not look like the whole list."""
    data = flat(playlist_count=500)
    playlist = parse_playlist(data)

    assert playlist.count == 500
    assert playlist.truncated is True
    assert playlist.heading == "A Playlist  ·  the first 3 of 500 videos"


def test_a_listing_with_no_titles_or_links_still_makes_rows():
    data = flat(
        entries=[
            {"_type": "url", "id": "abc123"},
            {"_type": "url", "url": "https://example.invalid/watch?v=xyz"},
        ]
    )
    playlist = parse_playlist(data)

    assert [entry.title for entry in playlist.entries] == ["abc123", "Video 2"]
    assert playlist.entries[0].url == ""
    assert playlist.entries[1].url == "https://example.invalid/watch?v=xyz"


def test_the_playlist_itself_is_reported_by_the_ordinary_look_up():
    info = media()

    assert info.playlist is not None
    assert info.playlist.count == 3
    assert info.playlist.url == URL
    assert info.playlist_note.startswith("This is a playlist of 3 videos")


# ---------------------------------------------------------------------------
# asking the site for the listing
# ---------------------------------------------------------------------------


class FakeProcess:
    def __init__(self, argv, stdout: str, stderr: str, returncode: int):
        self.argv = list(argv)
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.pid = 4242

    def communicate(self, timeout=None):
        return self.stdout, self.stderr

    def poll(self):
        return self.returncode

    def kill(self):
        pass

    def terminate(self):
        pass


def fake_lookup(monkeypatch, stdout: str = "", stderr: str = "", returncode: int = 0) -> list:
    calls: list = []

    def fake_popen(args, **options):
        calls.append(list(args))
        return FakeProcess(args, stdout, stderr, returncode)

    monkeypatch.setattr(download_mod.subprocess, "Popen", fake_popen)
    return calls


class FakeEngine:
    def __init__(self):
        self._tool = Tool("yt-dlp", ["/fake/yt-dlp"], "viddl", version="fake")

    def ensure_ytdlp(self, **kwargs):
        return self._tool

    def ffmpeg_location_args(self):
        return []

    def find_ffmpeg(self):
        # Present, so the plain and app paths never try to fetch it: what is
        # being tested here is the queue, not the setup for it.
        return Tool("ffmpeg", ["/fake/ffmpeg"], "system", version="7.0")


def test_the_listing_is_asked_for_flat_and_bounded(monkeypatch):
    import json

    calls = fake_lookup(monkeypatch, stdout=json.dumps(flat()))
    playlist = probe_playlist(FakeEngine(), URL, limit=7)

    assert len(calls) == 1
    argv = calls[0]
    assert "--flat-playlist" in argv, "a full extraction per video is the cost this avoids"
    assert "--dump-single-json" in argv
    assert argv[argv.index("--playlist-end") + 1] == "7"
    assert argv[-2:] == ["--", URL]
    assert [entry.title for entry in playlist.entries][:2] == ["First video", "Second video"]


def test_the_listing_uses_the_same_session_as_the_look_up(monkeypatch):
    import json

    calls = fake_lookup(monkeypatch, stdout=json.dumps(flat()))
    probe_playlist(FakeEngine(), URL, cookies="chrome")

    argv = calls[0]
    assert argv[argv.index("--cookies-from-browser") + 1] == "chrome"


def test_a_listing_already_paid_for_is_not_asked_for_again(monkeypatch):
    import json

    calls = fake_lookup(monkeypatch, stdout=json.dumps(flat()))
    first = probe_playlist(FakeEngine(), URL)
    second = probe_playlist(FakeEngine(), URL)

    assert len(calls) == 1
    assert first.title == second.title
    # And the videos are the same objects' worth of information, not empty ones.
    assert len(second.entries) == 3


def test_a_playlist_with_nothing_in_it_says_so(monkeypatch):
    import json

    fake_lookup(monkeypatch, stdout=json.dumps(flat(entries=[], playlist_count=0)))
    with pytest.raises(ToolError) as caught:
        probe_playlist(FakeEngine(), URL)

    assert "no videos" in caught.value.message


def test_the_sites_own_refusal_is_explained_rather_than_echoed(monkeypatch):
    fake_lookup(
        monkeypatch,
        stderr="ERROR: Private playlist. Sign in if you've been granted access",
        returncode=1,
    )
    with pytest.raises(ToolError) as caught:
        probe_playlist(FakeEngine(), URL)

    assert "ERROR:" not in caught.value.message


# ---------------------------------------------------------------------------
# one item of a playlist, when its listing gave an identifier
# ---------------------------------------------------------------------------


def test_a_download_takes_one_video_unless_it_is_told_which(tmp_path):
    job = DownloadJob(FakeEngine(), ENTRY_URL, video_option(), tmp_path)
    argv = job.build_args()

    assert "--no-playlist" in argv
    assert argv[argv.index("--playlist-end") + 1] == "1"
    assert "--playlist-items" not in argv


def test_a_download_can_be_asked_for_one_place_in_a_playlist(tmp_path):
    job = DownloadJob(FakeEngine(), URL, video_option(), tmp_path, playlist_item=7)
    argv = job.build_args()

    # The two cannot both be given: --no-playlist would make the item number
    # mean nothing at all.
    assert "--no-playlist" not in argv
    assert "--playlist-end" not in argv
    assert argv[argv.index("--playlist-items") + 1] == "7"
    assert argv[-2:] == ["--", URL]


# ---------------------------------------------------------------------------
# the queue
# ---------------------------------------------------------------------------


class RecordingJob:
    """A download that writes a file named after its place in the queue."""

    started: list = []
    fail_on: set = set()

    def __init__(
        self,
        engine,
        url,
        option,
        out_dir,
        on_progress=None,
        on_log=None,
        cookies="",
        playlist_item=None,
    ):
        self.url = url
        self.option = option
        self.on_progress = on_progress
        self.playlist_item = playlist_item
        self.out_dir = Path(out_dir)
        self.cancelled = False
        RecordingJob.started.append(self)

    def run(self):
        if self.url in RecordingJob.fail_on:
            raise ToolError("That video is not available any more.", "It may be private.")
        self.out_dir.mkdir(parents=True, exist_ok=True)
        name = self.url.rsplit("=", 1)[-1]
        target = self.out_dir / f"{name}.mp4"
        target.write_bytes(b"x" * 64)
        if self.on_progress:
            from viddl.download import Progress

            self.on_progress(Progress(percent=50.0, downloaded=32, total=64))
        return target

    def cancel(self, force: bool = False):
        self.cancelled = True


@pytest.fixture(autouse=True)
def clean_records():
    RecordingJob.started.clear()
    RecordingJob.fail_on = set()
    yield
    RecordingJob.started.clear()
    RecordingJob.fail_on = set()


def make_queue(entries=None, option=None, on_start=None, on_progress=None, on_end=None):
    playlist = listing()
    return QueueJob(
        FakeEngine(),
        playlist,
        entries if entries is not None else playlist.entries,
        option or video_option(),
        Path("/tmp/viddl-queue-tests"),
        make_job=RecordingJob,
        on_start=on_start,
        on_progress=on_progress,
        on_end=on_end,
    )


def test_the_queue_downloads_every_video_in_order():
    seen: list = []
    progress: list = []
    finished: list = []
    queue = make_queue(
        on_start=lambda item: seen.append(item.position),
        on_progress=lambda item, snapshot: progress.append((item.position, snapshot.percent)),
        on_end=lambda item: finished.append((item.position, item.state)),
    )

    summary = queue.run()

    assert seen == [1, 2, 3]
    assert [url for url in (job.url for job in RecordingJob.started)] == [
        "https://example.invalid/watch?v=one",
        "https://example.invalid/watch?v=two",
        "https://example.invalid/watch?v=three",
    ]
    assert progress == [(1, 50.0), (2, 50.0), (3, 50.0)]
    assert finished == [(1, DONE), (2, DONE), (3, DONE)]
    assert summary.done == 3 and summary.failed == 0 and summary.stopped is False
    assert summary.headline == "Saved all 3 videos."


def test_one_bad_video_does_not_end_the_list():
    """A deleted video in the middle is one missing file, not a failed queue."""
    RecordingJob.fail_on = {"https://example.invalid/watch?v=two"}
    queue = make_queue()

    summary = queue.run()

    assert [item.state for item in queue.items] == [DONE, FAILED, DONE]
    assert summary.done == 2
    assert summary.failed == 1
    assert summary.items[1].error is not None
    assert "Saved 2 of 3 videos; 1 could not be downloaded." == summary.headline
    assert len(RecordingJob.started) == 3, "the video after the failure was never tried"


def test_a_queue_that_is_stopped_takes_nothing_after_the_current_video():
    queue_box: list = []

    class StoppingJob(RecordingJob):
        def run(self):
            if self.url.endswith("two"):
                queue_box[0].cancel()
                raise DownloadCancelled()
            return super().run()

    playlist = listing()
    queue = QueueJob(
        FakeEngine(),
        playlist,
        playlist.entries,
        video_option(),
        Path("/tmp/viddl-queue-tests"),
        make_job=StoppingJob,
    )
    queue_box.append(queue)

    summary = queue.run()

    assert [item.state for item in queue.items] == [DONE, STOPPED, SKIPPED]
    assert summary.done == 1
    assert summary.stopped is True
    assert summary.headline == "Stopped after saving 1 of 3 videos."


def test_a_queue_stopped_before_it_began_starts_nothing():
    queue = make_queue()
    queue.cancel()

    summary = queue.run()

    assert [item.state for item in queue.items] == [SKIPPED, SKIPPED, SKIPPED]
    assert summary.stopped is True
    assert summary.headline == "Stopped before saving anything of 3 videos."
    assert RecordingJob.started == []


def test_a_listing_with_an_identifier_asks_the_playlist_for_that_item(tmp_path):
    playlist = Playlist(
        title="Identifiers only",
        url=URL,
        entries=[
            PlaylistEntry(index=1, title="First", url=""),
            PlaylistEntry(index=5, title="Fifth", url=""),
        ],
        count=5,
    )
    queue = QueueJob(
        FakeEngine(),
        playlist,
        playlist.entries,
        video_option(),
        tmp_path,
        make_job=RecordingJob,
    )

    summary = queue.run()

    assert summary.done == 2
    assert [(job.url, job.playlist_item) for job in RecordingJob.started] == [
        (URL, 1),
        (URL, 5),
    ]


def test_every_item_knows_where_it_sits_in_the_queue():
    queue = make_queue()
    labels = [item.label for item in queue.items]

    assert labels[0] == "1/3  First video"
    assert labels[-1] == "3/3  Third video"


# ---------------------------------------------------------------------------
# --plain
# ---------------------------------------------------------------------------


class PlainJob(RecordingJob):
    pass


def run_plain(url=URL, *, playlist: bool = False, assume_yes: bool = True, info=None):
    out, err = io.StringIO(), io.StringIO()
    code = plain.run_plain(
        url,
        assume_yes=assume_yes,
        playlist=playlist,
        engine=FakeEngine(),
        probe=lambda engine, link, cookies="": info if info is not None else media(),
        make_job=PlainJob,
        downloads_dir=lambda: Path("/tmp/viddl-plain-playlist"),
        stdout=out,
        stderr=err,
    )
    return code, out.getvalue(), err.getvalue()


def test_without_the_flag_a_playlist_still_gives_one_file_with_a_way_forward(monkeypatch):
    monkeypatch.setattr(plain, "probe_playlist", lambda *a, **k: listing())
    code, out, err = run_plain()

    assert code == 0
    assert len(PlainJob.started) == 1
    assert "first of the 3 videos" in err
    assert "--playlist" in err
    # Still exactly one path on stdout: the line a script reads.
    assert out.count("\n") == 1


def test_the_flag_downloads_every_video_and_lists_each_path(monkeypatch):
    monkeypatch.setattr(plain, "probe_playlist", lambda *a, **k: listing())
    code, out, err = run_plain(playlist=True)

    assert code == 0
    assert len(PlainJob.started) == 3
    assert "[1/3] First video" in err
    assert "[3/3] Third video" in err
    assert "Saved all 3 videos." in err
    assert out.splitlines() == [
        "/tmp/viddl-plain-playlist/one.mp4",
        "/tmp/viddl-plain-playlist/two.mp4",
        "/tmp/viddl-plain-playlist/three.mp4",
    ]
    assert err.isascii(), "a playlist must not be the one path that breaks a legacy console"


def test_a_failure_in_the_middle_is_reported_and_the_rest_still_download(monkeypatch):
    monkeypatch.setattr(plain, "probe_playlist", lambda *a, **k: listing())
    RecordingJob.fail_on = {"https://example.invalid/watch?v=two"}
    code, out, err = run_plain(playlist=True)

    assert code == 1, "a partial result must not look like a complete one"
    assert "That video is not available any more." in err
    assert "Saved 2 of 3 videos; 1 could not be downloaded." in err
    assert out.splitlines() == [
        "/tmp/viddl-plain-playlist/one.mp4",
        "/tmp/viddl-plain-playlist/three.mp4",
    ]


def test_the_flag_on_a_single_video_is_harmless(monkeypatch):
    called = []
    monkeypatch.setattr(plain, "probe_playlist", lambda *a, **k: called.append(1))
    code, out, err = run_plain(info=parse_info(INFO_JSON), playlist=True)

    assert code == 0
    assert len(PlainJob.started) == 1
    assert "one video, not a playlist" in err
    assert called == []


def test_a_cut_listing_says_so_before_downloading(monkeypatch):
    monkeypatch.setattr(
        plain, "probe_playlist", lambda *a, **k: parse_playlist(flat(playlist_count=500))
    )
    code, _, err = run_plain(playlist=True)

    assert code == 0
    assert "first 3 of 500 videos" in err
    assert "Split it into shorter playlists" in err


# ---------------------------------------------------------------------------
# the terminal app
# ---------------------------------------------------------------------------


class AppJob(RecordingJob):
    def __init__(self, engine, url, option, out_dir, on_progress=None, cookies="", **kwargs):
        super().__init__(
            engine, url, option, out_dir, on_progress=on_progress, cookies=cookies, **kwargs
        )


def app_services(tmp_path: Path, info=None) -> Services:
    return Services(
        engine=FakeEngine(),
        probe=lambda engine, url, cancel=None, cookies="": info if info is not None else media(),
        make_job=AppJob,
        downloads_dir=lambda: tmp_path / "Downloads",
        browsers=lambda: [],
        fetch_playlist=lambda engine, url, cookies="", **kwargs: listing(),
    )


async def settle(pilot, rounds: int = 8) -> None:
    for _ in range(rounds):
        await pilot.pause()
        await asyncio.sleep(0.01)


async def wait_for_screen(pilot, kind, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if isinstance(pilot.app.screen, kind):
            return True
        await pilot.pause()
        await asyncio.sleep(0.01)
    return isinstance(pilot.app.screen, kind)


def shown(screen, selector: str) -> str:
    return str(screen.query_one(selector).render())


@pytest.mark.asyncio
async def test_the_checklist_opens_with_everything_ticked(tmp_path):
    app = ViddlApp(initial_url=URL, services=app_services(tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        assert isinstance(app.screen, OptionsScreen)

        await pilot.press("p")
        await settle(pilot)

        assert isinstance(app.screen, PlaylistScreen)
        table = app.screen.query_one("#choices", ChoiceList)
        assert table.row_count == 3
        assert "3 videos" in shown(app.screen, "#options-heading")
        assert "3 of 3 selected" in shown(app.screen, "#options-note")
        labels = [row.row_label for row in table.query("ChoiceRow")]
        assert labels == ["[x] First video", "[x] Second video", "[x] Third video"]
        assert app.screen.query_one("#download", Button).disabled is False


@pytest.mark.asyncio
async def test_ticking_and_unticking_decides_what_is_downloaded(tmp_path):
    app = ViddlApp(initial_url=URL, services=app_services(tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.press("p")
        await settle(pilot)

        # None disables the button rather than starting an empty queue.
        await pilot.press("n")
        await pilot.pause()
        assert app.screen.query_one("#download", Button).disabled is True
        assert "0 of 3 selected" in shown(app.screen, "#options-note")

        await pilot.press("a")
        await pilot.pause()
        table = app.screen.query_one("#choices", ChoiceList)
        table.select_index(1)
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause()
        assert "2 of 3 selected" in shown(app.screen, "#options-note")

        await pilot.press("d")
        ok = await wait_for_screen(pilot, QueueResultScreen)
        assert ok
        await settle(pilot)

        assert len(AppJob.started) == 2
        assert [job.url for job in AppJob.started] == [
            "https://example.invalid/watch?v=one",
            "https://example.invalid/watch?v=three",
        ]
        assert shown(app.screen, "#brand") == "Playlist saved"
        assert "Saved all 2 videos." in shown(app.screen, "#result-sub")
        assert "one.mp4" in shown(app.screen, "#result-path")


@pytest.mark.asyncio
async def test_the_summary_names_the_video_that_did_not_work(tmp_path):
    RecordingJob.fail_on = {"https://example.invalid/watch?v=two"}
    app = ViddlApp(initial_url=URL, services=app_services(tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.press("p")
        await settle(pilot)
        await pilot.press("d")
        assert await wait_for_screen(pilot, QueueResultScreen)
        await settle(pilot)

        assert "problems" in shown(app.screen, "#brand")
        message = shown(app.screen, "#result-message")
        assert "Second video" in message
        assert "not available any more" in message
        assert "Saved 2 of 3" in shown(app.screen, "#result-sub")


@pytest.mark.asyncio
async def test_an_abandoned_checklist_does_not_turn_the_next_click_into_a_queue(tmp_path):
    """Backing out of the list must forget it: the menu downloads one video."""
    app = ViddlApp(initial_url=URL, services=app_services(tmp_path))
    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.press("p")
        await settle(pilot)
        assert isinstance(app.screen, PlaylistScreen)

        await pilot.press("escape")
        await settle(pilot)
        assert isinstance(app.screen, OptionsScreen)

        await pilot.press("enter")
        ok = await wait_for_screen(pilot, ResultScreen)
        assert ok
        await settle(pilot)
        assert len(AppJob.started) == 1
        assert app.state.playlist_entries == []


class BlockingAppJob(RecordingJob):
    """Sits in the first video until it is cancelled, so a screen can be read."""

    release = threading.Event()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def run(self):
        BlockingAppJob.release.wait(timeout=5)
        raise DownloadCancelled()

    def cancel(self, force: bool = False):
        self.cancelled = True
        BlockingAppJob.release.set()


@pytest.mark.asyncio
async def test_the_running_screen_says_how_many_videos_are_going(tmp_path):
    BlockingAppJob.release.clear()
    services = Services(
        engine=FakeEngine(),
        probe=lambda engine, url, cancel=None, cookies="": media(),
        make_job=BlockingAppJob,
        downloads_dir=lambda: tmp_path / "Downloads",
        browsers=lambda: [],
        fetch_playlist=lambda engine, url, cookies="", **kwargs: listing(),
    )
    app = ViddlApp(initial_url=URL, services=services)

    async with app.run_test(size=(100, 32)) as pilot:
        await settle(pilot)
        await pilot.press("p")
        await settle(pilot)
        await pilot.press("d")
        assert await wait_for_screen(pilot, DownloadScreen)
        await settle(pilot)
        assert "3 videos" in shown(app.screen, "#download-heading")
        assert "Video 1 of 3" in shown(app.screen, "#download-status")

        # Cancelling stops the queue, not merely the video it is on.
        await pilot.press("escape")
        assert await wait_for_screen(pilot, QueueResultScreen)
        await settle(pilot)
        assert shown(app.screen, "#brand") == "Playlist stopped"
        assert "Stopped" in shown(app.screen, "#result-sub")
        assert len(RecordingJob.started) == 1
    BlockingAppJob.release.clear()
