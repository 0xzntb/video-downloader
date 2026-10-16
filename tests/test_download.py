"""Probing a link and running a download, using fake engines (no network)."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

from viddl.download import DownloadCancelled, DownloadJob, probe_video
from viddl.engine import Tool, ToolError
from viddl.formats import MediaOption

SUCCESS_SCRIPT = """\
import os, pathlib, sys
args = sys.argv[1:]
out = pathlib.Path(args[args.index("-P") + 1])
target = out / "A Real Video [dQw4w9WgXcQ].mp4"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_bytes(b"v" * 4096)
print("VIDDLPROGRESS|10.0|1048576|20|409|4096|NA|" + str(target))
print("VIDDLPROGRESS|100.0|2097152|0|4096|4096|NA|" + str(target))
print("[download] Destination: " + str(target))
print('[Merger] Merging formats into "%s"' % target)
sys.exit(0)
"""

FAILURE_SCRIPT = """\
import sys
print("[youtube] dQw4w9WgXcQ: Downloading webpage")
print("ERROR: Video unavailable")
sys.exit(1)
"""

SLOW_SCRIPT = """\
import time
time.sleep(30)
"""

INFO_SCRIPT = """\
import json, sys
print(json.dumps({
    "id": "abc",
    "title": "Fake Video",
    "duration": 100,
    "webpage_url": "https://example.invalid/watch",
    "formats": [
        {"format_id": "18", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a",
         "height": 360, "filesize": 1000000},
        {"format_id": "137", "ext": "mp4", "vcodec": "avc1", "acodec": "none",
         "height": 1080, "filesize": 20000000},
        {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a",
         "abr": 128, "filesize": 1600000},
    ],
}))
"""

INFO_FAILURE_SCRIPT = """\
import sys
sys.stderr.write("ERROR: Private video. Sign in if you've been granted access\\n")
sys.exit(1)
"""


class FakeEngine:
    def __init__(self, prefix):
        self._tool = Tool("yt-dlp", prefix, "viddl", version="fake")

    def ensure_ytdlp(self, **kwargs):
        return self._tool

    def ffmpeg_location_args(self):
        return []

    def find_ffmpeg(self):
        return None


def write_script(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def engine_for(tmp_path: Path, name: str, body: str) -> FakeEngine:
    script = write_script(tmp_path, name, body)
    return FakeEngine([sys.executable, str(script)])


def video_option() -> MediaOption:
    return MediaOption(
        key="v1080",
        label="1080p",
        kind="video",
        needs_ffmpeg=True,
        format_selector="137+140",
        merge_container="mp4",
    )


# ---------------------------------------------------------------------------
# probing
# ---------------------------------------------------------------------------


def test_probe_turns_metadata_into_choices(tmp_path):
    info = probe_video(engine_for(tmp_path, "fake_info.py", INFO_SCRIPT), "https://x.invalid/v")
    assert info.title == "Fake Video"
    labels = [opt.label for opt in info.options]
    assert labels[0] == "1080p"
    assert any(label.startswith("Audio only") for label in labels)


def test_probe_explains_a_private_video(tmp_path):
    engine = engine_for(tmp_path, "fake_fail.py", INFO_FAILURE_SCRIPT)
    with pytest.raises(ToolError) as caught:
        probe_video(engine, "https://x.invalid/v")
    assert "private" in caught.value.message.lower()


def test_probe_reports_unreadable_output(tmp_path):
    engine = engine_for(tmp_path, "fake_junk.py", "print('not json')\n")
    with pytest.raises(ToolError) as caught:
        probe_video(engine, "https://x.invalid/v")
    assert "could not read" in caught.value.message.lower()


# ---------------------------------------------------------------------------
# building the command
# ---------------------------------------------------------------------------


def test_build_args_targets_the_downloads_folder(tmp_path):
    job = DownloadJob(
        FakeEngine(["/fake/yt-dlp"]),
        "https://x.invalid/v",
        video_option(),
        tmp_path / "Downloads",
    )
    args = job.build_args()
    assert args[0] == "/fake/yt-dlp"
    assert args[args.index("-f") + 1] == "137+140"
    assert args[args.index("-P") + 1] == str(tmp_path / "Downloads")
    assert "%(id)s" in args[args.index("-o") + 1]
    assert args[args.index("--merge-output-format") + 1] == "mp4"
    assert "--newline" in args
    assert args[-1] == "https://x.invalid/v"


def test_build_args_adds_mp3_conversion(tmp_path):
    option = MediaOption(
        key="mp3-320",
        label="Audio only · MP3 320 kbps",
        kind="audio",
        needs_ffmpeg=True,
        format_selector="ba/b",
        extra_args=["--extract-audio", "--audio-format", "mp3", "--audio-quality", "0"],
    )
    args = DownloadJob(FakeEngine(["/fake/yt-dlp"]), "u", option, tmp_path).build_args()
    assert "--extract-audio" in args
    assert args[args.index("--audio-format") + 1] == "mp3"
    assert "--merge-output-format" not in args


# ---------------------------------------------------------------------------
# running
# ---------------------------------------------------------------------------


def test_run_returns_the_finished_file_and_reports_progress(tmp_path):
    seen = []
    job = DownloadJob(
        engine_for(tmp_path, "fake_ok.py", SUCCESS_SCRIPT),
        "https://x.invalid/v",
        video_option(),
        tmp_path / "Downloads",
        on_progress=seen.append,
    )
    finished = job.run()

    assert finished.name == "A Real Video [dQw4w9WgXcQ].mp4"
    assert finished.is_file()
    assert finished.parent == tmp_path / "Downloads"
    assert seen[-1].percent == 100.0
    assert seen[-1].phase == "done"
    assert any(item.status == "Merging video and audio…" for item in seen)
    assert any(item.speed.endswith("/s") for item in seen)


def test_run_raises_a_friendly_error(tmp_path):
    job = DownloadJob(
        engine_for(tmp_path, "fake_bad.py", FAILURE_SCRIPT),
        "https://x.invalid/v",
        video_option(),
        tmp_path / "Downloads",
    )
    with pytest.raises(ToolError) as caught:
        job.run()
    assert "not available" in caught.value.message
    assert "deleted, made private" in (caught.value.hint or "")


def test_run_can_be_cancelled_mid_download(tmp_path):
    job = DownloadJob(
        engine_for(tmp_path, "fake_slow.py", SLOW_SCRIPT),
        "https://x.invalid/v",
        video_option(),
        tmp_path / "Downloads",
    )
    outcome = {}

    def run():
        try:
            job.run()
        except DownloadCancelled:
            outcome["cancelled"] = True
        except Exception as exc:  # pragma: no cover - defensive
            outcome["error"] = exc

    worker = threading.Thread(target=run)
    worker.start()
    time.sleep(1.2)
    job.cancel()
    worker.join(timeout=15)

    assert outcome.get("cancelled") is True
    assert outcome.get("error") is None


def test_progress_line_parsing():
    job = DownloadJob(FakeEngine(["/fake"]), "u", video_option(), Path("/tmp"))
    job._handle_progress_line("VIDDLPROGRESS|42.5|1048576|75|500|1000|NA|/tmp/v.mp4")
    assert job.progress.percent == 42.5
    assert job.progress.downloaded == 500
    assert job.progress.total == 1000
    assert job.progress.speed == "1.0 MB/s"
    assert job.progress.eta == "1:15"
    assert job.progress.phase == "download"


def test_progress_line_tolerates_missing_values():
    job = DownloadJob(FakeEngine(["/fake"]), "u", video_option(), Path("/tmp"))
    job._handle_progress_line("VIDDLPROGRESS|NA|NA|NA|NA|NA|NA|NA")
    assert job.progress.percent is None
    assert job.progress.speed == ""


def test_find_output_falls_back_to_the_newest_file(tmp_path):
    out = tmp_path / "Downloads"
    out.mkdir()
    old = out / "old.mp4"
    old.write_bytes(b"old")
    job = DownloadJob(FakeEngine(["/fake"]), "u", video_option(), out)
    before = job._snapshot()

    time.sleep(0.01)
    fresh = out / "fresh.mp3"
    fresh.write_bytes(b"new")
    (out / "half.mp4.part").write_bytes(b"junk")

    assert job._find_output(before) == fresh


def test_find_output_ignores_partial_files(tmp_path):
    out = tmp_path / "Downloads"
    out.mkdir()
    job = DownloadJob(FakeEngine(["/fake"]), "u", video_option(), out)
    before = job._snapshot()
    (out / "half.mp4.part").write_bytes(b"junk")
    assert job._find_output(before) is None
