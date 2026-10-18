"""Probing a link and running a download, using fake engines (no network)."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import types
from dataclasses import replace
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
    work = Path(args[args.index("-P") + 1])
    assert work.parent == tmp_path / "Downloads"
    assert work == job.work_dir()
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
    work = job.work_dir()
    work.mkdir(parents=True)
    before = job._snapshot()

    time.sleep(0.01)
    fresh = work / "fresh.mp3"
    fresh.write_bytes(b"new")
    (work / "half.mp4.part").write_bytes(b"junk")

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
    work = job.work_dir()
    work.mkdir(parents=True)
    before = job._snapshot()
    time.sleep(0.01)
    (work / "A Video [abc].f137.mp4").write_bytes(b"stream")
    (work / "A Video [abc].mp4").write_bytes(b"merged")
    assert job._find_output(before) == work / "A Video [abc].mp4"


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
    work = job.work_dir()
    work.mkdir(parents=True)
    before = job._snapshot()
    (work / "half.mp4.part").write_bytes(b"junk")
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
    child_pid = wait_for_pid(job.work_dir() / "child.pid", job, outcome)
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
    pid = wait_for_pid(job.work_dir() / "started.pid", job, outcome)
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
    pid = wait_for_pid(job.work_dir() / "started.pid", job, outcome)
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


# ---------------------------------------------------------------------------
# options the user asks for by name (R3)
#
# The config file is ignored, so this is the one deliberate way to add an option:
# it has to reach the real command line, and it has to reach the probe as well,
# or a link that needs cookies shows an empty quality menu.
# ---------------------------------------------------------------------------


def test_extra_options_reach_the_download_command(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDDL_YTDLP_ARGS", "--limit-rate 2M --cookies-from-browser chrome")
    job = DownloadJob(FakeEngine(["/fake"]), "https://x.invalid/v", video_option(), tmp_path)

    args = job.build_args()

    assert "--limit-rate" in args and "2M" in args
    assert "--cookies-from-browser" in args and "chrome" in args
    # Last, so a deliberate choice wins over Viddl's own default for anything
    # it has not reserved — and never after the separator, where it would be a
    # link.
    assert args[-1] == "https://x.invalid/v"
    assert args[-2] == "--"
    assert args.index("--limit-rate") < args.index("--")


def test_extra_options_reach_the_probe_too(tmp_path, monkeypatch):
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout='{"title": "Fake"}', stderr="")

    monkeypatch.setattr(download_mod.subprocess, "run", fake_run)
    monkeypatch.setenv("VIDDL_YTDLP_ARGS", "--cookies-from-browser chrome")

    probe_video(FakeEngine(["/fake/yt-dlp"]), "https://x.invalid/v")

    args = captured["args"]
    assert "--cookies-from-browser" in args
    assert args[-2:] == ["--", "https://x.invalid/v"]


def test_a_refused_option_never_reaches_the_command(tmp_path, monkeypatch):
    """An option that would take over a decision the app already made is dropped."""
    monkeypatch.setenv("VIDDL_YTDLP_ARGS", "-o/tmp/elsewhere/%(title)s.%(ext)s")
    job = DownloadJob(FakeEngine(["/fake"]), "https://x.invalid/v", video_option(), tmp_path)

    args = job.build_args()

    assert not any("elsewhere" in token for token in args)
    assert args.count("-o") == 1  # Viddl's own output template, and only it
    assert tmp_path in Path(args[args.index("-P") + 1]).parents


def test_a_stop_before_the_engine_starts_leaves_nothing_running(tmp_path, monkeypatch):
    """Cancel used to land in the gap before ``Popen`` and be forgotten."""
    def must_not_start(*args, **kwargs):  # pragma: no cover - the point
        raise AssertionError("a cancelled job started its process anyway")

    monkeypatch.setattr(download_mod.subprocess, "Popen", must_not_start)
    job = DownloadJob(FakeEngine(["/fake"]), "u", video_option(), tmp_path)
    job.cancel()

    with pytest.raises(DownloadCancelled):
        job.run()


# ---------------------------------------------------------------------------
# trying again (R5)
#
# A failure a second attempt may fix is tried again, with a doubling wait, and
# the wait is visible. Everything else fails once — quickly — because retrying
# a private video only makes the user wait for the same answer.
# ---------------------------------------------------------------------------

# Fails with a rate limit the first N times it is asked, then downloads. Counts
# its own runs in a file, so the tests see how many attempts really happened.
FLAKY_SCRIPT = """\
import os, pathlib, sys
args = sys.argv[1:]
out = pathlib.Path(args[args.index("-P") + 1])
tally = pathlib.Path(os.environ["FLAKY_TALLY"])
runs = int(tally.read_text()) if tally.exists() else 0
tally.write_text(str(runs + 1))
if runs < int(os.environ.get("FLAKY_FAILURES", "1")):
    print("ERROR: HTTP Error 429: Too Many Requests", flush=True)
    sys.exit(1)
target = out / "A Real Video [dQw4w9WgXcQ].mp4"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_bytes(b"v" * 1024)
print("[download] Destination: " + str(target), flush=True)
sys.exit(0)
"""

# The same, but it leaves a half-finished file behind before failing — and the
# second attempt records whether that file was still there to carry on from.
RESUMED_SCRIPT = """\
import os, pathlib, sys
args = sys.argv[1:]
out = pathlib.Path(args[args.index("-P") + 1])
tally = pathlib.Path(os.environ["FLAKY_TALLY"])
runs = int(tally.read_text()) if tally.exists() else 0
tally.write_text(str(runs + 1))
target = out / "Half A Video [abc].mp4"
if runs == 0:
    pathlib.Path(str(target) + ".part").write_bytes(b"half")
    print("ERROR: Unable to download webpage: <urlopen error [Errno 111] Connection refused>",
          flush=True)
    sys.exit(1)
pathlib.Path(os.environ["FLAKY_FLAG"]).write_text(
    "kept" if pathlib.Path(str(target) + ".part").exists() else "gone"
)
target.parent.mkdir(parents=True, exist_ok=True)
target.write_bytes(b"v")
print("[download] Destination: " + str(target), flush=True)
sys.exit(0)
"""

# A failure that will not change no matter how often it is asked.
PERMANENT_SCRIPT = """\
import os, pathlib, sys
tally = pathlib.Path(os.environ["FLAKY_TALLY"])
runs = int(tally.read_text()) if tally.exists() else 0
tally.write_text(str(runs + 1))
print("ERROR: Private video. Sign in if you've been granted access")
sys.exit(1)
"""


def flaky_job(tmp_path, monkeypatch, body, **kwargs) -> DownloadJob:
    """A job whose downloader counts its attempts, with no real waiting."""
    monkeypatch.setenv("FLAKY_TALLY", str(tmp_path / "runs"))
    kwargs.setdefault("retry_delay", 0)
    return DownloadJob(
        engine_for(tmp_path, "flaky.py", body),
        "https://x.invalid/v",
        video_option(),
        tmp_path / "Downloads",
        **kwargs,
    )


def test_a_rate_limit_is_tried_again_and_then_works(tmp_path, monkeypatch):
    seen = []
    logged = []
    job = flaky_job(
        tmp_path, monkeypatch, FLAKY_SCRIPT, on_progress=seen.append, on_log=logged.append
    )

    finished = job.run()

    assert finished.is_file()
    assert (tmp_path / "runs").read_text() == "2"
    assert any(item.phase == "retry" for item in seen)
    assert any("attempt 2 of 3" in item.status for item in seen)
    assert seen[-1].phase == "done"
    # Once the wait is over the screen must stop saying it is waiting — the
    # second attempt looked paused for its whole duration without this.
    retry_at = next(i for i, item in enumerate(seen) if item.phase == "retry")
    assert seen[retry_at + 1].phase == "download"
    # The log is what someone sends when they report a problem, so the retry and
    # its reason have to be in it — not only on a screen that has since closed.
    assert logged[0].startswith("$ ")
    assert any("rate-limiting" in line and "Retrying in" in line for line in logged)


def test_trying_again_waits_and_says_how_long_and_which_attempt(tmp_path, monkeypatch):
    seen = []
    job = flaky_job(tmp_path, monkeypatch, FLAKY_SCRIPT, retry_delay=0.2,
                    on_progress=seen.append)

    started = time.monotonic()
    job.run()
    elapsed = time.monotonic() - started

    retry = next(item for item in seen if item.phase == "retry")
    assert "Retrying in 0.2s" in retry.status
    assert "(attempt 2 of 3)" in retry.status
    assert elapsed >= 0.2


def test_each_wait_is_longer_than_the_one_before(tmp_path):
    job = DownloadJob(FakeEngine(["/fake"]), "u", video_option(), tmp_path)
    assert job.attempts == 3
    assert [job._retry_wait(n) for n in (1, 2)] == [4.0, 8.0]


def test_a_permanent_failure_is_reported_after_one_attempt(tmp_path, monkeypatch):
    monkeypatch.setenv("FLAKY_TALLY", str(tmp_path / "runs"))
    job = DownloadJob(
        engine_for(tmp_path, "permanent.py", PERMANENT_SCRIPT),
        "https://x.invalid/v",
        video_option(),
        tmp_path / "Downloads",
        retry_delay=0,
    )

    started = time.monotonic()
    with pytest.raises(ToolError) as caught:
        job.run()
    elapsed = time.monotonic() - started

    assert (tmp_path / "runs").read_text() == "1"
    assert caught.value.temporary is False
    assert "private" in caught.value.message
    assert "tried" not in (caught.value.hint or "")
    assert elapsed < 5


def test_a_temporary_failure_that_keeps_failing_says_how_many_tries(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("FLAKY_FAILURES", "99")
    job = flaky_job(tmp_path, monkeypatch, FLAKY_SCRIPT)

    with pytest.raises(ToolError) as caught:
        job.run()

    assert (tmp_path / "runs").read_text() == "3"
    assert caught.value.temporary is True
    assert "Viddl tried 3 times" in (caught.value.hint or "")
    # The friendly explanation is still the one shown, not a retry report.
    assert "rate-limiting" in caught.value.message


def test_one_attempt_can_be_asked_for(tmp_path, monkeypatch):
    job = flaky_job(tmp_path, monkeypatch, FLAKY_SCRIPT, attempts=1)

    with pytest.raises(ToolError) as caught:
        job.run()

    assert (tmp_path / "runs").read_text() == "1"
    assert "tried" not in (caught.value.hint or "")


def test_a_retry_keeps_what_the_failed_attempt_fetched(tmp_path, monkeypatch):
    monkeypatch.setenv("FLAKY_FLAG", str(tmp_path / "flag"))
    job = flaky_job(tmp_path, monkeypatch, RESUMED_SCRIPT)

    finished = job.run()

    assert finished.is_file()
    assert (tmp_path / "flag").read_text() == "kept"


def test_stopping_during_the_wait_ends_at_once_and_starts_nothing(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("FLAKY_FAILURES", "99")
    job = flaky_job(tmp_path, monkeypatch, FLAKY_SCRIPT, retry_delay=30)
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
    deadline = time.monotonic() + 10
    while job.progress.phase != "retry" and time.monotonic() < deadline:
        time.sleep(0.02)
    assert job.progress.phase == "retry", "the job never reached its wait"

    started = time.monotonic()
    job.cancel()
    worker.join(timeout=10)
    elapsed = time.monotonic() - started

    assert outcome.get("cancelled") is True
    assert outcome.get("error") is None
    assert elapsed < 5, "a cancel waited for the retry delay instead of being felt"
    assert (tmp_path / "runs").read_text() == "1", "a stopped job started another attempt"


# ---------------------------------------------------------------------------
# two windows, one downloads folder (R6)
#
# Two downloads of the same video used to aim at the same file: whoever
# finished last won, and the other reported a path whose bytes had changed
# underneath it. Each job now writes in a folder of its own and only the
# finished file is moved out, under a name reserved before the move.
# ---------------------------------------------------------------------------

# Both instances write the same file name inside their own folders, and wait
# until both have started before writing — so the runs really do overlap.
PARALLEL_SCRIPT = """\
import os, pathlib, sys, time
args = sys.argv[1:]
out = pathlib.Path(args[args.index("-P") + 1])
marker = pathlib.Path(os.environ["PARALLEL_MARKER"])
with open(marker, "a", encoding="utf-8") as handle:
    handle.write(out.name + "\\n")
for _ in range(200):
    if len(marker.read_text(encoding="utf-8").splitlines()) >= 2:
        break
    time.sleep(0.02)
target = out / "A Real Video [dQw4w9WgXcQ].mp4"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_bytes(out.name.encode())
print("[download] Destination: " + str(target), flush=True)
sys.exit(0)
"""

# Leaves half a file behind and waits, like a download stopped by the user.
RESUME_AGAIN_SCRIPT = """\
import os, pathlib, sys, time
args = sys.argv[1:]
out = pathlib.Path(args[args.index("-P") + 1])
target = out / "Half A Video [abc].mp4"
part = pathlib.Path(str(target) + ".part")
flag = pathlib.Path(os.environ["RESUME_FLAG"])
if not part.exists():
    part.write_bytes(b"half")
    print("[download] Destination: " + str(target), flush=True)
    print("[download] Resuming download at byte 0", flush=True)
    time.sleep(30)
flag.write_text("resumed" if part.exists() else "fresh")
target.write_bytes(b"v")
print("[download] Destination: " + str(target), flush=True)
sys.exit(0)
"""


def test_the_same_download_always_uses_the_same_folder(tmp_path):
    """So a download stopped today is found again tomorrow, and resumed."""
    out = tmp_path / "Downloads"
    first = DownloadJob(FakeEngine(["/fake"]), "u", video_option(), out)
    same = DownloadJob(FakeEngine(["/fake"]), "u", video_option(), out)
    other_quality = DownloadJob(
        FakeEngine(["/fake"]), "u", replace(video_option(), format_selector="18"), out
    )
    other_link = DownloadJob(FakeEngine(["/fake"]), "https://x.invalid/other", video_option(), out)

    assert first.work_dir() == same.work_dir()
    assert first.work_dir() == first.work_dir()  # decided once, not per call
    assert first.work_dir().parent == out  # same filesystem as the result
    assert other_quality.work_dir() != first.work_dir()
    assert other_link.work_dir() != first.work_dir()
    args = first.build_args()
    assert Path(args[args.index("-P") + 1]) == first.work_dir()


def test_two_runs_at_once_claim_different_folders(tmp_path):
    """Sharing a name is fine; sharing a folder while writing is not."""
    out = tmp_path / "Downloads"
    out.mkdir()
    jobs = [DownloadJob(FakeEngine(["/fake"]), "u", video_option(), out) for _ in range(3)]

    claimed = [job._enter_work_dir() for job in jobs]

    assert len(set(claimed)) == 3
    assert all(path.parent == out for path in claimed)
    # Each claim is a real marker on disk, inside its own folder.
    assert all((path / ".viddl-busy").is_file() for path in claimed)


def test_a_folder_another_run_is_using_is_not_resumed_into(tmp_path):
    """A claimed folder belongs to the run writing in it, not to a new one."""
    out = tmp_path / "Downloads"
    busy = DownloadJob(FakeEngine(["/fake"]), "u", video_option(), out)._enter_work_dir()
    (busy / "A Video [abc].mp4.part").write_bytes(b"theirs")

    newcomer = DownloadJob(FakeEngine(["/fake"]), "u", video_option(), out)
    claimed = newcomer._enter_work_dir()

    assert claimed != busy
    assert claimed.name.startswith(busy.name + "-")
    assert (busy / "A Video [abc].mp4.part").read_bytes() == b"theirs"


def test_two_downloads_at_once_do_not_write_the_same_file(tmp_path, monkeypatch):
    monkeypatch.setenv("PARALLEL_MARKER", str(tmp_path / "marker"))
    out = tmp_path / "Downloads"
    jobs = [
        DownloadJob(
            engine_for(tmp_path, f"parallel{index}.py", PARALLEL_SCRIPT),
            "https://x.invalid/v",
            video_option(),
            out,
        )
        for index in range(2)
    ]
    results = {}

    def run(index: int) -> None:
        try:
            results[index] = jobs[index].run()
        except Exception as exc:  # pragma: no cover - defensive
            results[index] = exc

    threads = [threading.Thread(target=run, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not any(thread.is_alive() for thread in threads)
    names = sorted(path.name for path in results.values())
    assert names == [
        "A Real Video [dQw4w9WgXcQ] (2).mp4",
        "A Real Video [dQw4w9WgXcQ].mp4",
    ]
    # Each file holds its own download's bytes, so neither was overwritten and
    # neither instance is pointing at the other's work.
    contents = sorted(path.read_bytes() for path in results.values())
    assert contents == sorted(job.work_dir().name.encode() for job in jobs)
    # Nothing half-finished is left behind in the folder the user looks at.
    assert sorted(path.name for path in out.iterdir()) == names


def test_a_download_never_replaces_a_file_that_is_already_there(tmp_path):
    out = tmp_path / "Downloads"
    out.mkdir()
    mine = out / "A Real Video [dQw4w9WgXcQ].mp4"
    mine.write_bytes(b"already here")

    job = DownloadJob(
        engine_for(tmp_path, "fake_ok.py", SUCCESS_SCRIPT),
        "https://x.invalid/v",
        video_option(),
        out,
    )
    finished = job.run()

    assert mine.read_bytes() == b"already here"
    assert finished.name == "A Real Video [dQw4w9WgXcQ] (2).mp4"
    assert finished.read_bytes() == b"v" * 4096


def test_a_claimed_name_is_never_handed_out_twice(tmp_path):
    out = tmp_path / "Downloads"
    out.mkdir()
    first = DownloadJob(FakeEngine(["/fake"]), "u", video_option(), out)
    second = DownloadJob(FakeEngine(["/fake"]), "u", video_option(), out)

    claimed = [first._claim_name("A Video [abc].mp4") for _ in range(3)]
    claimed.append(second._claim_name("A Video [abc].mp4"))

    assert [path.name for path in claimed] == [
        "A Video [abc].mp4",
        "A Video [abc] (2).mp4",
        "A Video [abc] (3).mp4",
        "A Video [abc] (4).mp4",
    ]
    # Every claim is a real, empty reservation on disk.
    assert all(path.is_file() and path.stat().st_size == 0 for path in claimed)


def test_the_working_folder_is_gone_once_the_file_is_delivered(tmp_path):
    out = tmp_path / "Downloads"
    job = DownloadJob(
        engine_for(tmp_path, "fake_ok.py", SUCCESS_SCRIPT),
        "https://x.invalid/v",
        video_option(),
        out,
    )
    work = job.work_dir()

    finished = job.run()

    assert finished.parent == out
    assert not work.exists(), "the working folder is litter once the file is out"
    assert [path.name for path in out.iterdir()] == ["A Real Video [dQw4w9WgXcQ].mp4"]


def test_a_failed_download_takes_its_working_folder_with_it(tmp_path):
    out = tmp_path / "Downloads"
    job = DownloadJob(
        engine_for(tmp_path, "fake_bad.py", FAILURE_SCRIPT),
        "https://x.invalid/v",
        video_option(),
        out,
    )
    with pytest.raises(ToolError):
        job.run()

    assert not job.work_dir().exists()
    assert list(out.iterdir()) == []


def test_a_stopped_download_keeps_its_folder_and_resumes_from_it(tmp_path, monkeypatch):
    monkeypatch.setenv("RESUME_FLAG", str(tmp_path / "flag"))
    out = tmp_path / "Downloads"
    job = DownloadJob(
        engine_for(tmp_path, "resume_again.py", RESUME_AGAIN_SCRIPT),
        "https://x.invalid/v",
        video_option(),
        out,
    )
    work = job.work_dir()

    outcome = run_until_cancelled(job, after=1.0)

    assert outcome.get("cancelled") is True
    assert work.is_dir()
    assert job.partial_paths(), "the part should be kept inside the job's folder"

    # Resuming is the same job run again — which is why the folder has to be
    # remembered by the job, not chosen afresh each time.
    finished = job.run()

    assert (tmp_path / "flag").read_text() == "resumed"
    assert finished.parent == out
    assert not work.exists()


def test_a_new_job_finds_the_part_a_stopped_one_left(tmp_path, monkeypatch):
    """Resuming is not a privilege of the object that was stopped."""
    monkeypatch.setenv("RESUME_FLAG", str(tmp_path / "flag"))
    out = tmp_path / "Downloads"
    engine = engine_for(tmp_path, "resume_again.py", RESUME_AGAIN_SCRIPT)
    first = DownloadJob(engine, "https://x.invalid/v", video_option(), out)

    outcome = run_until_cancelled(first, after=1.0)
    assert outcome.get("cancelled") is True

    # A later download of the same link and quality finds the same folder, so
    # yt-dlp carries on instead of fetching the bytes again.
    second = DownloadJob(engine, "https://x.invalid/v", video_option(), out)
    finished = second.run()

    assert (tmp_path / "flag").read_text() == "resumed"
    assert finished.stat().st_size == 1
    assert not second.work_dir().exists()


def test_another_window_s_file_is_never_reported_as_ours(tmp_path):
    out = tmp_path / "Downloads"
    out.mkdir()
    job = DownloadJob(FakeEngine(["/fake"]), "u", video_option(), out)
    job.work_dir().mkdir()

    # A file that appeared in the shared folder belongs to someone else.
    (out / "Someone Else's Video [xyz].mp4").write_bytes(b"theirs")

    assert job._find_output({}) is None


def test_a_file_that_cannot_be_put_in_place_leaves_no_empty_one_behind(
    tmp_path, monkeypatch
):
    """The name is reserved before the move, so a failed move must free it."""
    out = tmp_path / "Downloads"
    job = DownloadJob(
        engine_for(tmp_path, "fake_ok.py", SUCCESS_SCRIPT),
        "https://x.invalid/v",
        video_option(),
        out,
    )

    def cannot_move(*args, **kwargs):
        raise OSError("the disk went away")

    monkeypatch.setattr(download_mod.os, "replace", cannot_move)
    with pytest.raises(OSError):
        job.run()

    assert not (out / "A Real Video [dQw4w9WgXcQ].mp4").exists()
    assert [path.name for path in out.iterdir()] == [job.work_dir().name]
    # The download is still there to be delivered by a later run.
    assert (job.work_dir() / "A Real Video [dQw4w9WgXcQ].mp4").is_file()
    assert not (job.work_dir() / ".viddl-busy").exists()


def test_a_folder_that_cannot_be_written_in_says_so(tmp_path, monkeypatch):
    """A permissions problem is a permissions problem, not a busy folder."""
    out = tmp_path / "Downloads"
    out.mkdir()
    job = DownloadJob(FakeEngine(["/fake"]), "u", video_option(), out)

    def denied(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(download_mod.os, "open", denied)
    with pytest.raises(ToolError) as caught:
        job.run()

    assert "make room" in caught.value.message
    assert "Permission denied" in (caught.value.hint or "")
