"""Probing a link and running a download, using fake engines (no network)."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest

from viddl import download as download_mod
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

# Announces a destination, writes half the file, then sits there until it is
# cancelled — which is what a stopped download looks like on disk.
PARTIAL_SCRIPT = """\
import pathlib, sys, time
args = sys.argv[1:]
out = pathlib.Path(args[args.index("-P") + 1])
target = out / "Half A Video [abc].mp4"
target.parent.mkdir(parents=True, exist_ok=True)
print("[download] Destination: " + str(target), flush=True)
print("VIDDLPROGRESS|50.0|1048576|5|500|1000|NA|" + str(target), flush=True)
pathlib.Path(str(target) + ".part").write_bytes(b"h" * 500)
print("[download] Resuming download at byte 500", flush=True)
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
    def __init__(self, prefix, ffmpeg_args=None):
        self._tool = Tool("yt-dlp", prefix, "viddl", version="fake")
        self._ffmpeg_args = ffmpeg_args or []

    def ensure_ytdlp(self, **kwargs):
        return self._tool

    def ffmpeg_location_args(self):
        return self._ffmpeg_args

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


def test_probe_hands_the_link_over_as_data(monkeypatch):
    """Probing builds its own command line, so it needs the same guard."""
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        return subprocess.CompletedProcess(
            args, 0, stdout='{"title": "Fake Video"}', stderr=""
        )

    monkeypatch.setattr(download_mod.subprocess, "run", fake_run)
    info = probe_video(FakeEngine(["/fake/yt-dlp"]), "--exec=calc.exe")

    args = captured["args"]
    assert args[-2:] == ["--", "--exec=calc.exe"]
    assert "--ignore-config" in args
    assert info.title == "Fake Video"


def test_probe_reports_unreadable_output(tmp_path):
    engine = engine_for(tmp_path, "fake_junk.py", "print('not json')\n")
    with pytest.raises(ToolError) as caught:
        probe_video(engine, "https://x.invalid/v")
    assert "could not read" in caught.value.message.lower()


def test_probe_reports_a_slow_site(tmp_path, monkeypatch):
    engine = engine_for(tmp_path, "unused.py", "")

    def too_slow(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="yt-dlp", timeout=1)

    monkeypatch.setattr(download_mod.subprocess, "run", too_slow)
    with pytest.raises(ToolError) as caught:
        probe_video(engine, "https://x.invalid/v")
    assert "too long" in caught.value.message


def test_probe_reports_an_engine_that_will_not_start(tmp_path, monkeypatch):
    engine = engine_for(tmp_path, "unused.py", "")

    def cannot_start(*args, **kwargs):
        raise OSError("Exec format error")

    monkeypatch.setattr(download_mod.subprocess, "run", cannot_start)
    with pytest.raises(ToolError) as caught:
        probe_video(engine, "https://x.invalid/v")
    assert "could not be started" in caught.value.message


def test_probe_rejects_output_that_is_not_a_video(tmp_path):
    engine = engine_for(tmp_path, "fake_list.py", "import json; print(json.dumps([1, 2]))\n")
    with pytest.raises(ToolError) as caught:
        probe_video(engine, "https://x.invalid/v")
    assert "single video" in caught.value.message


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


def test_build_args_ends_the_options_before_the_link(tmp_path):
    """The link is separated from the options, so it can never be read as one."""
    job = DownloadJob(FakeEngine(["/fake/yt-dlp"]), "https://x.invalid/v", video_option(), tmp_path)
    args = job.build_args()
    assert args[-2:] == ["--", "https://x.invalid/v"]
    assert "--ignore-config" in args


def test_an_option_shaped_link_cannot_become_an_option(tmp_path):
    """A page can hand over anything; none of it may turn into a flag.

    Without the ``--`` separator this string was passed straight through as an
    argument, and yt-dlp's own parser read it as a real option
    (``--config-locations``, which loads a config file that may itself contain
    an ``--exec``).
    """
    hostile = "--config-locations=./evil.conf"
    job = DownloadJob(FakeEngine(["/fake/yt-dlp"]), hostile, video_option(), tmp_path)
    args = job.build_args()

    separator = args.index("--")
    assert "--config-locations" not in args[:separator]
    # The hostile string appears once, as data, after the separator.
    assert args[separator + 1 :] == [hostile]


def test_the_separator_is_immediately_before_the_link(tmp_path):
    """Only the link follows the end-of-options marker — nothing else."""
    job = DownloadJob(FakeEngine(["/fake/yt-dlp"]), "https://x.invalid/v", video_option(), tmp_path)
    args = job.build_args()
    separator = args.index("--")
    assert separator == len(args) - 2
    assert args[separator + 1 :] == ["https://x.invalid/v"]


def test_build_args_points_yt_dlp_at_our_own_ffmpeg(tmp_path):
    engine = FakeEngine(["/fake/yt-dlp"], ffmpeg_args=["--ffmpeg-location", "/home/me/.local/share/viddl/bin"])
    job = DownloadJob(engine, "u", video_option(), tmp_path)
    args = job.build_args()
    assert args[args.index("--ffmpeg-location") + 1].endswith("bin")


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


def test_an_already_downloaded_file_is_reused(tmp_path):
    out = tmp_path / "Downloads"
    out.mkdir()
    existing = out / "Old Video [abc].mp4"
    existing.write_bytes(b"here already")

    job = DownloadJob(FakeEngine(["/fake"]), "u", video_option(), out)
    job._handle_line(f"[download] {existing} has already been downloaded")

    assert job.progress.percent == 100.0
    assert "Already downloaded" in job.progress.status
    assert job._find_output({}) == existing


def test_an_extracted_audio_path_is_remembered(tmp_path):
    out = tmp_path / "Downloads"
    out.mkdir()
    song = out / "A Song [abc].mp3"
    job = DownloadJob(FakeEngine(["/fake"]), "u", video_option(), out)
    job._handle_line(f"[ExtractAudio] Destination: {song}")
    assert job.progress.phase == "convert"
    assert "MP3" in job.progress.status


def test_finishing_steps_are_reported(tmp_path):
    job = DownloadJob(FakeEngine(["/fake"]), "u", video_option(), tmp_path)
    job._handle_line("[FixupM3u8] Fixing MPEG-TS in MP4 container")
    assert "Finishing" in job.progress.status


def test_a_recorded_path_wins_over_the_newest_file(tmp_path):
    out = tmp_path / "Downloads"
    out.mkdir()
    wanted = out / "Wanted [abc].mp4"
    wanted.write_bytes(b"real")
    time.sleep(0.01)
    (out / "Decoy [xyz].mp4").write_bytes(b"other" * 100)

    job = DownloadJob(FakeEngine(["/fake"]), "u", video_option(), out)
    job._handle_line(f'[Merger] Merging formats into "{wanted}"')
    assert job._find_output(job._snapshot()) == wanted


def test_the_merged_file_wins_over_the_streams_it_was_made_from(tmp_path):
    """yt-dlp sometimes leaves the separate streams behind (--keep-video).

    Reporting one of those as the finished download would hand the user a
    path to a half-file, so the merged result has to win.
    """
    out = tmp_path / "Downloads"
    out.mkdir()
    video_only = out / "A Video [abc].f137.mp4"
    audio_only = out / "A Video [abc].f140.m4a"
    merged = out / "A Video [abc].mp4"
    for path in (video_only, audio_only, merged):
        path.write_bytes(b"x" * 100)

    job = DownloadJob(FakeEngine(["/fake"]), "u", video_option(), out)
    job._handle_line(f"[download] Destination: {video_only}")
    job._handle_line(f"[download] Destination: {audio_only}")
    job._handle_line(f'[Merger] Merging formats into "{merged}"')

    assert job._find_output(job._snapshot()) == merged


def test_a_leftover_stream_is_a_last_resort_not_an_error(tmp_path):
    """If the merge never produced its file, the stream on disk still beats
    telling the user nothing was saved."""
    out = tmp_path / "Downloads"
    out.mkdir()
    video_only = out / "A Video [abc].f137.mp4"
    video_only.write_bytes(b"x" * 100)
    job = DownloadJob(FakeEngine(["/fake"]), "u", video_option(), out)
    job._handle_line(f"[download] Destination: {video_only}")
    assert job._find_output(job._snapshot()) == video_only


def test_the_newest_file_scan_skips_leftover_streams(tmp_path):
    out = tmp_path / "Downloads"
    out.mkdir()
    job = DownloadJob(FakeEngine(["/fake"]), "u", video_option(), out)
    before = job._snapshot()
    time.sleep(0.01)
    (out / "A Video [abc].f137.mp4").write_bytes(b"stream")
    (out / "A Video [abc].mp4").write_bytes(b"merged")
    assert job._find_output(before) == out / "A Video [abc].mp4"


def test_the_opt_in_cleanup_still_clears_the_partial_files(tmp_path):
    """Kept for a caller that asks for the old tidy-up; no longer the default."""
    out = tmp_path / "Downloads"
    out.mkdir()
    half = out / "Half [abc].mp4"
    part = out / "Half [abc].mp4.part"
    part.write_bytes(b"half")

    job = DownloadJob(FakeEngine(["/fake"]), "u", video_option(), out)
    job._paths.append(half)
    job._cleanup_partial()
    assert not part.exists()


# ---------------------------------------------------------------------------
# resuming (R1)
# ---------------------------------------------------------------------------


def test_the_command_asks_yt_dlp_to_carry_on(tmp_path):
    job = DownloadJob(FakeEngine(["/fake"]), "u", video_option(), tmp_path)
    args = job.build_args()
    assert "--continue" in args
    assert "--no-continue" not in args
    # Before the link, and after the separator would be too late.
    assert args.index("--continue") < args.index("--")


def partial_job(tmp_path, **kwargs):
    return DownloadJob(
        engine_for(tmp_path, "fake_partial.py", PARTIAL_SCRIPT),
        "https://x.invalid/v",
        video_option(),
        tmp_path / "Downloads",
        **kwargs,
    )


def run_until_cancelled(job, after=0.0):
    """Start a job, let it get going, stop it, and report what happened."""
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
    deadline = time.time() + 15
    while after and time.time() < deadline:
        if job.partial_paths():
            break
        time.sleep(0.05)
    job.cancel()
    worker.join(timeout=20)
    return outcome


def test_cancelling_keeps_the_part_so_it_can_be_resumed(tmp_path):
    job = partial_job(tmp_path)
    outcome = run_until_cancelled(job, after=1.0)

    assert outcome.get("cancelled") is True
    kept = job.partial_paths()
    assert kept, "the half-finished file should still be there"
    assert kept[0].name == "Half A Video [abc].mp4.part"
    assert kept[0].is_file()


def test_discarding_removes_what_kindness_kept(tmp_path):
    job = partial_job(tmp_path)
    run_until_cancelled(job, after=1.0)

    assert job.discard_partial() == 1
    assert job.partial_paths() == []


def test_a_caller_may_ask_for_the_old_tidy_up(tmp_path):
    job = partial_job(tmp_path, keep_partial=False)
    run_until_cancelled(job, after=1.0)
    assert job.partial_paths() == []


def test_another_downloads_part_is_not_ours_to_delete(tmp_path):
    """Discard works from what this job announced, never from a pattern."""
    out = tmp_path / "Downloads"
    out.mkdir()
    someone_else = out / "Another Video [xyz].mp4.part"
    someone_else.write_bytes(b"not ours")
    job = DownloadJob(FakeEngine(["/fake"]), "u", video_option(), out)

    assert job.partial_paths() == []
    assert job.discard_partial() == 0
    assert someone_else.is_file()


def test_a_partial_that_cannot_be_deleted_is_not_fatal(tmp_path):
    """Discarding is a kindness, so it must not be able to raise."""
    out = tmp_path / "Downloads"
    out.mkdir()
    half = out / "Half [abc].mp4"
    (out / "Half [abc].mp4.part").mkdir()  # a directory where the file should be

    job = DownloadJob(FakeEngine(["/fake"]), "u", video_option(), out)
    job._paths.append(half)

    assert job.discard_partial() == 0
    assert job.partial_paths() == [out / "Half [abc].mp4.part"]


def test_a_resumed_download_says_so(tmp_path):
    job = DownloadJob(FakeEngine(["/fake"]), "u", video_option(), tmp_path)
    job._handle_line("[download] Resuming download at byte 1048576")
    assert "Resuming" in job.progress.status
    assert job.progress.phase == "download"


def test_a_runner_that_cannot_start_is_explained(tmp_path, monkeypatch):
    job = DownloadJob(FakeEngine(["/fake"]), "u", video_option(), tmp_path)

    def cannot_start(*args, **kwargs):
        raise OSError("No such file")

    monkeypatch.setattr(download_mod.subprocess, "Popen", cannot_start)
    with pytest.raises(ToolError) as caught:
        job.run()
    assert "could not be started" in caught.value.message


def test_a_finished_download_with_no_file_is_reported(tmp_path, monkeypatch):
    out = tmp_path / "Downloads"
    out.mkdir()
    engine = engine_for(tmp_path, "fake_nothing.py", "import sys; sys.exit(0)\n")
    job = DownloadJob(engine, "u", video_option(), out)
    with pytest.raises(ToolError) as caught:
        job.run()
    assert "could not be found" in caught.value.message


def test_find_output_ignores_partial_files(tmp_path):
    out = tmp_path / "Downloads"
    out.mkdir()
    job = DownloadJob(FakeEngine(["/fake"]), "u", video_option(), out)
    before = job._snapshot()
    (out / "half.mp4.part").write_bytes(b"junk")
    assert job._find_output(before) is None


def test_a_partial_file_is_never_remembered_as_the_result(tmp_path):
    out = tmp_path / "Downloads"
    out.mkdir()
    job = DownloadJob(FakeEngine(["/fake"]), "u", video_option(), out)
    job._handle_line(f'[Merger] Merging formats into "{out / "Half [abc].mp4.part"}"')
    job._handle_line(f"[download] Destination: {out / 'Half [abc].mp4.part'}")
    assert job._paths == []


# ---------------------------------------------------------------------------
# stopping the children too (R2)
# ---------------------------------------------------------------------------

# A stand-in for yt-dlp at the moment it runs ffmpeg to merge: it starts a
# child that would keep writing, says where the file is, and then waits — like
# a merge that is going to take a while.
TREE_SCRIPT = """\
import pathlib, subprocess, sys, time
args = sys.argv[1:]
out = pathlib.Path(args[args.index("-P") + 1])
out.mkdir(parents=True, exist_ok=True)
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
# Written whole, so a reader never sees half a number.
(out / "child.pid.tmp").write_text(str(child.pid))
(out / "child.pid.tmp").replace(out / "child.pid")
print("[download] Destination: " + str(out / "A Video [abc].mp4"), flush=True)
time.sleep(120)
"""

# The same shape, but deaf to the polite stop — a wedged process.
STUBBORN_SCRIPT = """\
import os, pathlib, signal, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
args = sys.argv[1:]
out = pathlib.Path(args[args.index("-P") + 1])
out.mkdir(parents=True, exist_ok=True)
(out / "started.pid.tmp").write_text(str(os.getpid()))
(out / "started.pid.tmp").replace(out / "started.pid")
print("[download] Destination: " + str(out / "A Video [abc].mp4"), flush=True)
time.sleep(120)
"""

needs_process_groups = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="signals a process group and reads /proc to check that it is gone",
)


def still_running(pid: int) -> bool:
    """True while that process is alive; a zombie counts as gone."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # pragma: no cover - nothing here is not ours
        return True
    try:
        state = Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1].split(" ")[0]
    except (OSError, IndexError):
        return True
    return state != "Z"


def wait_until_gone(pid: int, seconds: float = 5.0) -> bool:
    """True once that process has gone; a signal is a request, not a fact."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        if not still_running(pid):
            return True
        time.sleep(0.02)
    return False


def wait_for_pid(path: Path, job, outcome: dict, seconds: float = 20.0) -> int:
    """The pid the fake yt-dlp wrote, or an assertion that says what happened."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            text = path.read_text().strip()
        except OSError:
            text = ""
        if text.isdigit():
            return int(text)
        time.sleep(0.05)
    raise AssertionError(
        f"{path.name} never appeared; lines={job._lines[-8:]!r} outcome={outcome}"
    )


def run_in_background(job) -> tuple:
    """Start a job on a thread and hand back the thread and the outcome."""
    outcome = {}

    def run():
        try:
            job.run()
        except DownloadCancelled:
            outcome["cancelled"] = True
        except Exception as exc:  # pragma: no cover - reported by the asserts
            outcome["error"] = exc

    worker = threading.Thread(target=run)
    worker.start()
    return worker, outcome


@needs_process_groups
def test_stopping_a_download_stops_the_process_it_started(tmp_path):
    """yt-dlp's child is the merge, and it is what cancel used to leave behind.

    Signalling yt-dlp alone is not enough: ffmpeg keeps writing to the very
    ``.part`` file the app now promises to keep for resuming — and it keeps the
    pipe open, so the screen never learns the download stopped.
    """
    out = tmp_path / "Downloads"
    job = DownloadJob(
        engine_for(tmp_path, "fake_tree.py", TREE_SCRIPT),
        "https://x.invalid/v",
        video_option(),
        out,
    )
    worker, outcome = run_in_background(job)
    child_pid = wait_for_pid(out / "child.pid", job, outcome)
    try:
        assert still_running(child_pid), "the fake merge should have been running"
        job.cancel()
        worker.join(timeout=30)

        assert not worker.is_alive(), "cancel left the reading loop stuck"
        assert outcome.get("cancelled") is True
        assert wait_until_gone(child_pid), "cancel left the merge running"
    finally:
        if still_running(child_pid):  # pragma: no cover - only on a failure
            try:
                os.kill(child_pid, 9)
            except OSError:  # pragma: no cover - it died in the meantime
                pass


@needs_process_groups
def test_a_process_that_ignores_the_polite_stop_is_killed(tmp_path, monkeypatch):
    """A wedged yt-dlp must not leave "Stopping…" on screen for ever."""
    monkeypatch.setattr(download_mod, "_STOP_GRACE_SECONDS", 0.4)
    out = tmp_path / "Downloads"
    job = DownloadJob(
        engine_for(tmp_path, "fake_stubborn.py", STUBBORN_SCRIPT),
        "https://x.invalid/v",
        video_option(),
        out,
    )
    worker, outcome = run_in_background(job)
    pid = wait_for_pid(out / "started.pid", job, outcome)
    started_at = time.time()
    job.cancel()
    worker.join(timeout=30)

    assert not worker.is_alive()
    assert outcome.get("cancelled") is True
    assert wait_until_gone(pid), "the escalation never arrived"
    assert time.time() - started_at < 10, "the polite stop was waited on for ever"


@needs_process_groups
def test_quitting_stops_the_tree_without_a_grace_period(tmp_path, monkeypatch):
    """Quitting cannot rely on a watchdog that dies with the app."""
    monkeypatch.setattr(download_mod, "_STOP_GRACE_SECONDS", 60.0)
    out = tmp_path / "Downloads"
    job = DownloadJob(
        engine_for(tmp_path, "fake_stubborn2.py", STUBBORN_SCRIPT),
        "https://x.invalid/v",
        video_option(),
        out,
    )
    worker, outcome = run_in_background(job)
    pid = wait_for_pid(out / "started.pid", job, outcome)
    job.cancel(force=True)
    worker.join(timeout=20)

    assert not worker.is_alive()
    assert outcome.get("cancelled") is True
    assert wait_until_gone(pid)


def test_the_download_gets_a_session_of_its_own(tmp_path, monkeypatch):
    """The whole tree can only be signalled if the child leads its own group."""
    seen = {}

    def fake_popen(args, **kwargs):
        seen.update(kwargs)
        raise OSError("stop here")

    monkeypatch.setattr(download_mod.subprocess, "Popen", fake_popen)
    job = DownloadJob(FakeEngine(["/fake"]), "u", video_option(), tmp_path)
    with pytest.raises(ToolError):
        job.run()

    if sys.platform.startswith("win"):
        assert seen.get("creationflags") == subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        assert seen.get("start_new_session") is True


def test_windows_stops_the_tree_with_taskkill(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(download_mod, "is_windows", lambda: True)
    monkeypatch.setattr(download_mod.subprocess, "run", lambda command, **kw: calls.append(command))
    process = types.SimpleNamespace(pid=4242)
    job = DownloadJob(FakeEngine(["/fake"]), "u", video_option(), tmp_path)

    job._signal_tree(process, force=False)
    job._signal_tree(process, force=True)

    assert calls[0] == ["taskkill", "/PID", "4242", "/T"]
    assert calls[1] == ["taskkill", "/F", "/PID", "4242", "/T"]


def test_a_missing_taskkill_is_not_fatal(tmp_path, monkeypatch):
    def no_taskkill(*args, **kwargs):
        raise OSError("taskkill is not on this PATH")

    monkeypatch.setattr(download_mod, "is_windows", lambda: True)
    monkeypatch.setattr(download_mod.subprocess, "run", no_taskkill)
    job = DownloadJob(FakeEngine(["/fake"]), "u", video_option(), tmp_path)
    job._signal_tree(types.SimpleNamespace(pid=4242), force=True)


def test_a_process_without_a_group_of_its_own_is_still_stopped(tmp_path, monkeypatch):
    stopped = []

    def no_group(*args):
        raise OSError("no such process group")

    monkeypatch.setattr(download_mod.os, "killpg", no_group)
    process = types.SimpleNamespace(
        pid=1, terminate=lambda: stopped.append("term"), kill=lambda: stopped.append("kill")
    )
    job = DownloadJob(FakeEngine(["/fake"]), "u", video_option(), tmp_path)

    job._signal_tree(process, force=False)
    job._signal_tree(process, force=True)

    assert stopped == ["term", "kill"]


def test_a_stop_before_the_engine_starts_leaves_nothing_running(tmp_path, monkeypatch):
    """Cancel used to land in the gap before ``Popen`` and be forgotten."""
    def must_not_start(*args, **kwargs):  # pragma: no cover - the point
        raise AssertionError("a cancelled job started its process anyway")

    monkeypatch.setattr(download_mod.subprocess, "Popen", must_not_start)
    job = DownloadJob(FakeEngine(["/fake"]), "u", video_option(), tmp_path)
    job.cancel()

    with pytest.raises(DownloadCancelled):
        job.run()
