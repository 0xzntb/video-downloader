"""What Viddl remembers between runs (F6): the recent list and the archive.

The recent list is a file a person reads; the archive is a file a tool writes.
Both live in the app's own folder and both are tested here through the real
module against real files — the same files the app would use, pointed at a
temporary folder the way the suite points everything.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from viddl import appdata
from viddl import history


def make_file(folder: Path, name: str = "A Video [abc].mp4", size: int = 4096) -> Path:
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


# ---------------------------------------------------------------------------
# the recent list
# ---------------------------------------------------------------------------


def test_a_download_is_recorded_and_listed_newest_first(tmp_path):
    older = make_file(tmp_path / "dl", "First [one].mp4")
    newer = make_file(tmp_path / "dl", "Second [two].mp4", size=1024)

    history.record(url="https://x.invalid/1", path=older, title="First")
    history.record(url="https://x.invalid/2", path=newer, title="Second")

    found = history.entries()
    assert [entry.title for entry in found] == ["Second", "First"]
    assert found[0].url == "https://x.invalid/2"
    assert found[0].size == 1024
    assert found[0].size_text == "1.0 KB"
    assert found[0].when <= time.time()
    assert found[0].when_text == "just now"
    assert not found[0].missing


def test_recording_the_same_file_again_moves_it_to_the_top(tmp_path):
    first = make_file(tmp_path / "dl", "First [one].mp4")
    second = make_file(tmp_path / "dl", "Second [two].mp4")
    history.record(url="https://x.invalid/1", path=first, title="First")
    history.record(url="https://x.invalid/2", path=second, title="Second")

    history.record(url="https://x.invalid/1", path=first, title="First")

    found = history.entries()
    assert [entry.title for entry in found] == ["First", "Second"], (
        "the same file twice is one row moved to the top, not two rows"
    )


def test_the_list_keeps_only_the_newest_fifty(tmp_path):
    folder = tmp_path / "dl"
    for number in range(history.HISTORY_LIMIT + 5):
        history.record(
            url=f"https://x.invalid/{number}",
            path=make_file(folder, f"Video {number} [{number}].mp4", size=number + 1),
            title=f"Video {number}",
        )

    found = history.entries()
    assert len(found) == history.HISTORY_LIMIT
    # The newest survived; the oldest five went.
    assert found[0].title == f"Video {history.HISTORY_LIMIT + 4}"
    assert all(entry.title != "Video 0" for entry in found)


def test_a_row_whose_file_is_gone_stays_and_says_so(tmp_path):
    path = make_file(tmp_path / "dl")
    history.record(url="https://x.invalid/v", path=path, title="A Video")
    path.unlink()

    (found,) = history.entries()
    assert found.missing
    assert "file is gone" in found.row_note


def test_a_corrupt_list_reads_as_nothing_and_is_replaced(tmp_path):
    appdata.history_path().parent.mkdir(parents=True, exist_ok=True)
    appdata.history_path().write_text("{ this is not json", encoding="utf-8")

    assert history.entries() == []

    path = make_file(tmp_path / "dl")
    history.record(url="https://x.invalid/v", path=path, title="A Video")
    (found,) = history.entries()
    assert found.title == "A Video"
    # And the file on disk is the list, not the garbage it replaced.
    payload = json.loads(appdata.history_path().read_text(encoding="utf-8"))
    assert isinstance(payload, list) and len(payload) == 1


def test_a_hand_edited_line_that_makes_no_sense_is_skipped(tmp_path):
    appdata.history_path().parent.mkdir(parents=True, exist_ok=True)
    appdata.history_path().write_text(
        json.dumps(
            [
                {"path": str(tmp_path / "good.mp4"), "title": "Good", "size": 10,
                 "when": 1.0, "url": "https://x.invalid/good"},
                "not a row at all",
                {"title": "no path"},
                42,
            ]
        ),
        encoding="utf-8",
    )

    (found,) = history.entries()
    assert found.title == "Good"


def test_the_list_file_is_for_its_owner_only(tmp_path):
    path = make_file(tmp_path / "dl")
    history.record(url="https://x.invalid/v", path=path)

    if os.name == "nt":  # access there is governed by ACLs, not mode bits
        pytest.skip("the mode means nothing on Windows")
    mode = os.stat(appdata.history_path()).st_mode & 0o777
    assert mode == 0o600


def test_a_list_that_cannot_be_written_is_a_lost_nicety_not_a_failure(
    tmp_path, monkeypatch
):
    """No raise, and an honest None: a download must never fail over a diary."""
    blocker = tmp_path / "blocked"
    blocker.write_text("a file where the folder should be", encoding="utf-8")
    monkeypatch.setattr(appdata, "data_dir", lambda: blocker / "viddl")

    assert history.record(url="https://x.invalid/v", path=tmp_path / "gone.mp4") is None
    assert history.entries() == []


# ---------------------------------------------------------------------------
# how old a row says it is
# ---------------------------------------------------------------------------


def test_how_old_a_row_says_it_is(tmp_path):
    now = time.time()

    def age(seconds: float) -> str:
        return history.Entry(path=tmp_path / "x.mp4", when=now - seconds).when_text

    assert age(10) == "just now"
    assert age(5 * 60) == "5 min ago"
    assert age(3 * 3600) == "3 h ago"
    assert age(2 * 86400) == "2 days ago"
    assert age(30 * 86400).count(" ") == 2  # a date, not "30 days ago"


def test_a_row_with_no_when_says_nothing_about_it(tmp_path):
    entry = history.Entry(path=tmp_path / "x.mp4", when=0.0)
    assert entry.when_text == ""
    assert entry.row_note == ""


# ---------------------------------------------------------------------------
# the download archive
# ---------------------------------------------------------------------------


def write_archive(lines) -> Path:
    path = appdata.archive_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    return path


def test_forgetting_a_video_removes_only_its_line():
    archive = write_archive(
        ["youtube dQw4w9WgXcQ", "generic media", "Vimeo 12345"]
    )

    assert history.forget_video(archive, "media") is True

    assert archive.read_text(encoding="utf-8").splitlines() == [
        "youtube dQw4w9WgXcQ",
        "Vimeo 12345",
    ]


def test_forgetting_a_video_that_is_not_there_changes_nothing():
    archive = write_archive(["youtube dQw4w9WgXcQ"])

    assert history.forget_video(archive, "media") is False
    assert archive.read_text(encoding="utf-8").splitlines() == ["youtube dQw4w9WgXcQ"]


def test_forgetting_a_video_survives_an_archive_that_is_not_there(tmp_path):
    assert history.forget_video(tmp_path / "nope.txt", "media") is False


def test_forgetting_nothing_at_all_is_nothing():
    archive = write_archive(["youtube dQw4w9WgXcQ"])
    assert history.forget_video(archive, "") is False
    assert archive.read_text(encoding="utf-8").splitlines() == ["youtube dQw4w9WgXcQ"]


# ---------------------------------------------------------------------------
# the switch (VIDDL_ARCHIVE)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["1", "on", "yes", "true", "YES", " On "])
def test_the_archive_is_off_unless_a_yes_is_asked_for(value, monkeypatch):
    monkeypatch.setenv("VIDDL_ARCHIVE", value)
    assert appdata.archive_enabled()
    assert appdata.archive_problem() == ""


@pytest.mark.parametrize("value", ["0", "off", "no", "false", "", "NO"])
def test_saying_no_is_understood_as_no(monkeypatch, value):
    monkeypatch.setenv("VIDDL_ARCHIVE", value)
    assert not appdata.archive_enabled()
    assert appdata.archive_problem() == ""


def test_unset_means_off_and_is_not_a_problem(monkeypatch):
    monkeypatch.delenv("VIDDL_ARCHIVE", raising=False)
    assert not appdata.archive_enabled()
    assert appdata.archive_problem() == ""


def test_a_value_that_is_neither_yes_nor_no_is_refused_not_guessed(monkeypatch):
    """A typo must not read as \"off\": the person asked for a memory."""
    monkeypatch.setenv("VIDDL_ARCHIVE", "maybe")

    assert not appdata.archive_enabled()
    assert "not a yes or no" in appdata.archive_problem()


def test_the_doctor_says_what_is_remembered_either_way(capsys, monkeypatch):
    from viddl.cli import main

    monkeypatch.delenv("VIDDL_ARCHIVE", raising=False)
    assert main(["--doctor"]) == 0
    output = capsys.readouterr().out
    assert "Archive:      off" in output
    assert "--download-archive" in output
    assert f"Recent list:  {appdata.history_path()}" in output

    monkeypatch.setenv("VIDDL_ARCHIVE", "1")
    assert main(["--doctor"]) == 0
    output = capsys.readouterr().out
    assert f"Archive:      on — {appdata.archive_path()}" in output

    monkeypatch.setenv("VIDDL_ARCHIVE", "perhaps")
    assert main(["--doctor"]) == 0
    output = capsys.readouterr().out
    assert "VIDDL_ARCHIVE=perhaps (ignored — not a yes or no" in output
