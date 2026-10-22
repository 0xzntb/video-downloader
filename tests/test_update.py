"""Making a broken link work again from inside the app (F3).

``viddl --update`` already existed (R4); what was missing was the place the
advice is read. These tests hold the loop that closes: a failure on an old
engine says so, offers the update, runs the same checked install the flag does,
and asks the link again by itself — while a current engine is told nothing,
because updating it would change nothing.
"""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
from typing import Optional

import pytest
from textual.widgets import Button

from viddl import cli, download, plain
from viddl import engine as engine_mod
from viddl.download import DownloadCancelled
from viddl.engine import Tool, ToolError
from viddl.formats import MediaInfo, Playlist, parse_info, parse_playlist
from viddl.tui.app import HomeScreen, OptionsScreen, Services, ViddlApp
from viddl.tui.widgets import ChoiceList

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
    ],
}


def media() -> MediaInfo:
    return parse_info(INFO_JSON)


# ---------------------------------------------------------------------------
# the sentence itself
# ---------------------------------------------------------------------------


def test_a_current_engine_gets_no_advice():
    assert engine_mod.staleness_advice("2026.10.01", date(2026, 10, 17)) == ""
    # An undated build cannot be called old, so it is not given advice either.
    assert engine_mod.staleness_advice("nightly", date(2026, 10, 17)) == ""


def test_an_old_engine_is_told_how_old_it_is_and_what_to_run():
    advice = engine_mod.staleness_advice("2026.08.19", date(2026, 10, 17))

    assert "59 days old" in advice
    assert "viddl --update" in advice
    assert "usual reason" in advice


# ---------------------------------------------------------------------------
# --plain
# ---------------------------------------------------------------------------


class FakeEngine:
    def __init__(self, version: str = "2020.01.01"):
        self.version = version
        self.tool = Tool("yt-dlp", ["/fake/yt-dlp"], "system", version=version)

    def ensure_ytdlp(self, **kwargs):
        return self.tool

    def find_ytdlp(self):
        return self.tool

    def find_ffmpeg(self):
        return Tool("ffmpeg", ["/fake/ffmpeg"], "system", version="7.0")

    def ffmpeg_location_args(self):
        return []

    def ffmpeg_size_hint(self):
        return None


def run_plain(*, engine=None, probe=None, job=None, playlist=False):
    import io

    out, err = io.StringIO(), io.StringIO()
    code = plain.run_plain(
        URL,
        assume_yes=True,
        playlist=playlist,
        engine=engine or FakeEngine(),
        probe=probe or (lambda *a, **k: media()),
        make_job=job or (lambda *a, **k: None),
        downloads_dir=lambda: Path("/tmp/viddl-update-tests"),
        stdout=out,
        stderr=err,
    )
    return code, out.getvalue(), err.getvalue()


def failing_probe(*args, **kwargs):
    raise ToolError(
        "The site did not answer in a way Viddl understood.",
        "This usually means the site changed.",
    )


def test_a_failed_look_up_on_an_old_engine_says_what_to_run():
    code, _, err = run_plain(probe=failing_probe)

    assert code == 1
    assert "This usually means the site changed." in err
    assert "video engine is" in err
    assert "viddl --update" in err


def test_a_failed_look_up_on_a_current_engine_does_not_send_anyone_updating():
    code, _, err = run_plain(engine=FakeEngine("2099.01.01"), probe=failing_probe)

    assert code == 1
    assert "viddl --update" not in err


def test_a_failed_download_on_an_old_engine_says_what_to_run():
    class BrokenJob:
        def __init__(self, *args, **kwargs):
            pass

        def run(self):
            raise ToolError("That video is not available any more.", "It may be private.")

    code, _, err = run_plain(job=BrokenJob)

    assert code == 1
    assert "not available any more" in err
    assert "viddl --update" in err


def test_a_playlist_says_it_once_rather_than_once_per_video(monkeypatch):
    listing = parse_playlist(
        {
            "title": "A Playlist",
            "playlist_count": 3,
            "webpage_url": URL,
            "entries": [
                {"title": "One", "url": "https://example.invalid/one"},
                {"title": "Two", "url": "https://example.invalid/two"},
                {"title": "Three", "url": "https://example.invalid/three"},
            ],
        }
    )
    info = parse_info(
        {
            "title": "A Playlist",
            "playlist_count": 3,
            "webpage_url": URL,
            "entries": [dict(INFO_JSON)],
        }
    )

    class BrokenJob:
        def __init__(self, *args, **kwargs):
            pass

        def run(self):
            raise ToolError("That video is not available any more.", "It may be private.")

    monkeypatch.setattr(plain, "probe_playlist", lambda *a, **k: listing)
    code, _, err = run_plain(probe=lambda *a, **k: info, job=BrokenJob, playlist=True)

    assert code == 1
    assert err.count("video engine is") == 1
    assert err.count("not available any more") == 3


# ---------------------------------------------------------------------------
# --list-qualities
# ---------------------------------------------------------------------------


def test_list_qualities_adds_the_advice_when_the_engine_is_old(capsys, monkeypatch):
    monkeypatch.setattr(cli, "Engine", lambda *a, **k: FakeEngine())

    def boom(*args, **kwargs):
        raise ToolError("The site did not answer in a way Viddl understood.", "Try again.")

    monkeypatch.setattr(download, "probe_video", boom)

    assert cli.main(["--list-qualities", URL]) == 1
    err = capsys.readouterr().err
    assert "Try again." in err
    assert "viddl --update" in err


def test_list_qualities_keeps_quiet_when_the_engine_is_current(capsys, monkeypatch):
    monkeypatch.setattr(cli, "Engine", lambda *a, **k: FakeEngine("2099.01.01"))

    def boom(*args, **kwargs):
        raise ToolError("The site did not answer in a way Viddl understood.", "Try again.")

    monkeypatch.setattr(download, "probe_video", boom)

    assert cli.main(["--list-qualities", URL]) == 1
    assert "viddl --update" not in capsys.readouterr().err


# ---------------------------------------------------------------------------
# the terminal app
# ---------------------------------------------------------------------------


class FlakyProbe:
    """Fails the first look-up, the way a stale engine does against a new site."""

    def __init__(self, info: MediaInfo, failures: int = 1):
        self.info = info
        self.failures = failures
        self.calls = 0

    def __call__(self, engine, url, cancel=None, cookies="", **kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            raise ToolError(
                "The site did not answer in a way Viddl understood.",
                "This usually means the site changed. Trying again later often fixes it.",
            )
        return self.info


class Updater:
    """Stands in for the 3 MB download, and records how it was asked."""

    def __init__(self, before=None, after=None, error: Optional[ToolError] = None):
        self.before = before
        self.after = after or Tool("yt-dlp", ["/fake/new"], "viddl", version="2099.01.01")
        self.error = error
        self.calls = 0
        self.statuses: list = []

    def __call__(self, engine, on_status=None, on_progress=None):
        self.calls += 1
        if self.error is not None:
            raise self.error
        if on_status:
            self.statuses.append("status")
            on_status("Downloading the video engine (about 3 MB)…")
        if on_progress:
            on_progress(50, 100)
        return self.before, self.after


class UnusedJob:
    def __init__(self, *args, **kwargs):
        pass

    def run(self):  # pragma: no cover - no test here downloads
        raise DownloadCancelled()

    def cancel(self, force: bool = False):
        pass


def services_for(
    tmp_path: Path,
    *,
    engine=None,
    probe=None,
    updater=None,
    info: Optional[MediaInfo] = None,
) -> Services:
    return Services(
        engine=engine or FakeEngine(),
        probe=probe or (lambda engine, url, cancel=None, cookies="": info or media()),
        make_job=UnusedJob,
        downloads_dir=lambda: tmp_path / "Downloads",
        browsers=lambda: [],
        # Nothing on the clipboard: the home screen must not shell out to a
        # clipboard tool in a test, and a real clipboard would change what the
        # screen shows (F8).
        clipboard=lambda: "",
        fetch_playlist=lambda *a, **k: Playlist(title="", url=URL),
        update_engine=updater or Updater(),
    )


async def settle(pilot, rounds: int = 10) -> None:
    for _ in range(rounds):
        await pilot.pause()
        await asyncio.sleep(0.01)


async def wait_until(pilot, condition, timeout: float = 6.0) -> bool:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        await pilot.pause()
        await asyncio.sleep(0.01)
    return condition()


def shown(screen, selector: str) -> str:
    return str(screen.query_one(selector).render())


@pytest.mark.asyncio
async def test_a_broken_link_offers_the_update_that_is_likely_to_fix_it(tmp_path):
    updater = Updater(before=Tool("yt-dlp", ["/old"], "viddl", version="2020.01.01"))
    services = services_for(tmp_path, probe=FlakyProbe(media()), updater=updater)
    app = ViddlApp(initial_url=URL, services=services)

    async with app.run_test(size=(100, 34)) as pilot:
        await settle(pilot)
        assert isinstance(app.screen, OptionsScreen)

        button = app.screen.query_one("#update-engine", Button)
        assert button.has_class("hidden") is False
        note = shown(app.screen, "#options-note")
        assert "engine is old" in note
        assert "Update engine" in note

        await pilot.click("#update-engine")
        assert await wait_until(pilot, lambda: services.probe.calls == 2)
        await settle(pilot)

        assert updater.calls == 1
        # The new engine replaced the failure with a fresh look-up: this is the
        # "make it work again" the item is about.
        assert isinstance(app.screen, OptionsScreen)
        assert shown(app.screen, "#options-status") == ""
        table = app.screen.query_one("#choices", ChoiceList)
        keys = [table.value_at(index).key for index in range(table.row_count)]
        # The qualities are back — the video row, and the audio rows the menu
        # offers (F4) — rather than the failure that prompted the update.
        assert keys[0] == "v1080"
        assert "orig" in keys
        assert "A Very Real Video" in shown(app.screen, "#options-heading")
        assert app.state.engine_stale is False
        assert "Ready to download" in app.state.engine_note


@pytest.mark.asyncio
async def test_an_update_that_changes_nothing_does_not_ask_the_link_again(tmp_path):
    same = Tool("yt-dlp", ["/mine"], "viddl", version="2099.01.01")
    updater = Updater(before=same, after=same)
    probe = FlakyProbe(media())
    app = ViddlApp(initial_url=URL, services=services_for(tmp_path, probe=probe, updater=updater))

    async with app.run_test(size=(100, 34)) as pilot:
        await settle(pilot)
        await pilot.click("#update-engine")
        assert await wait_until(pilot, lambda: updater.calls == 1)
        await settle(pilot, rounds=20)

        assert probe.calls == 1, "the site was asked again for an update that changed nothing"
        assert "already the newest release" in shown(app.screen, "#options-status")
        assert "not asked again" in shown(app.screen, "#options-note")
        # The button has answered, so it is no longer offered (§F3).
        assert app.screen.query_one("#update-engine", Button).has_class("hidden")


@pytest.mark.asyncio
async def test_an_update_that_fails_says_why_and_keeps_the_failure(tmp_path):
    updater = Updater(error=ToolError("Could not reach GitHub.", "Check your connection."))
    probe = FlakyProbe(media())
    app = ViddlApp(initial_url=URL, services=services_for(tmp_path, probe=probe, updater=updater))

    async with app.run_test(size=(100, 34)) as pilot:
        await settle(pilot)
        await pilot.click("#update-engine")
        assert await wait_until(pilot, lambda: updater.calls == 1)
        await settle(pilot, rounds=20)

        assert "Could not reach GitHub." in shown(app.screen, "#options-status")
        assert "Check your connection." in shown(app.screen, "#options-note")
        assert probe.calls == 1
        button = app.screen.query_one("#update-engine", Button)
        assert button.disabled is False and button.has_class("hidden") is False


@pytest.mark.asyncio
async def test_a_current_engine_does_not_offer_an_update_after_a_failure(tmp_path):
    updater = Updater()
    app = ViddlApp(
        initial_url=URL,
        services=services_for(
            tmp_path,
            engine=FakeEngine("2099.01.01"),
            probe=FlakyProbe(media()),
            updater=updater,
        ),
    )

    async with app.run_test(size=(100, 34)) as pilot:
        await settle(pilot)
        assert isinstance(app.screen, OptionsScreen)
        assert app.screen.query_one("#update-engine", Button).has_class("hidden")
        assert "engine is old" not in shown(app.screen, "#options-note")

        # The keyboard must not do what the hidden button refuses to.
        await pilot.press("u")
        await settle(pilot)
        assert updater.calls == 0


@pytest.mark.asyncio
async def test_the_home_screen_can_update_an_old_engine_where_it_says_so(tmp_path):
    updater = Updater(before=Tool("yt-dlp", ["/old"], "viddl", version="2020.01.01"))
    app = ViddlApp(
        services=services_for(tmp_path, engine=FakeEngine("2020.01.01"), updater=updater)
    )

    async with app.run_test(size=(100, 34)) as pilot:
        await settle(pilot)
        assert isinstance(app.screen, HomeScreen)
        button = app.screen.query_one("#update-engine", Button)
        assert button.has_class("hidden") is False

        await pilot.click("#update-engine")
        assert await wait_until(pilot, lambda: "Ready to download" in app.state.engine_note)
        await settle(pilot)

        assert updater.calls == 1
        assert "Ready to download" in shown(app.screen, "#engine-status")
        assert app.screen.query_one("#update-engine", Button).has_class("hidden")
        assert app.state.engine_stale is False
