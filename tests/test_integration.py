"""One download with the real engine, over a socket on this machine (E1).

Every other test in this suite stands in for yt-dlp. That is what keeps them
fast and offline, and it is also why they cannot answer the question that
matters most for a program whose job is to run somebody else's binary: *does the
command line Viddl builds still work with the yt-dlp people actually have?* The
progress template, the output template, the exit codes, the shape of
``Destination:`` — all of it is a contract with a tool that changes every week.

So this file runs the real thing: the app's own :class:`~viddl.download.DownloadJob`,
the real ``yt-dlp``, and files served from ``127.0.0.1`` on a port the kernel
picked. No third-party site, no network, nothing to authenticate, and the same
result every time. It is marked ``integration`` and **skipped**, never passed,
when no yt-dlp is installed — a green run that quietly tested nothing would be
worse than an honest skip.

The third test is the one E7 was written for: two separate streams and a real
``ffmpeg`` merge, which is where the last two bugs that reached users lived.
``yt-dlp`` downloads a video stream and an audio stream, joins them with ffmpeg,
and the app has to report the joined file rather than one of the two halves it
came from. Both halves are generated on the spot from lavfi, so nothing is
checked in and nothing is downloaded.

    python -m pytest tests/test_integration.py -q      # or just: pytest
    python -m pip install --upgrade yt-dlp             # what the downloads need
    viddl --update-ffmpeg                              # what the merge needs

The weekly **engine** workflow runs exactly this against the newest release,
because that is the failure everybody hits first (see ``E1`` and ``E7`` in
``SUGGESTIONS.md``).
"""

from __future__ import annotations

import contextlib
import functools
import http.server
import subprocess
import threading
from pathlib import Path
from typing import List

import pytest

from viddl.download import DownloadJob, Progress
from viddl.engine import Engine
from viddl.formats import MediaOption

pytestmark = pytest.mark.integration

#: The bytes of the "video". yt-dlp's generic extractor takes a link that ends
#: in .mp4 at its word and downloads it, so the file does not have to be a
#: playable one; what it has to be is a size the download can report, and a
#: content that can be compared byte for byte at the other end.
CLIP = b"\x00\x01\x02\x03" * 65_536  # 256 KiB


class _Quiet(http.server.SimpleHTTPRequestHandler):
    """A file server that keeps its access log to itself."""

    def log_message(self, *args) -> None:  # pragma: no cover - never asserted
        pass


@contextlib.contextmanager
def _serving(folder: Path):
    """Serve a folder on ``127.0.0.1``, on a port the kernel picked."""
    handler = functools.partial(_Quiet, directory=str(folder))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture()
def served(tmp_path: Path):
    """The clip, and the URL that serves it."""
    folder = tmp_path / "served"
    folder.mkdir()
    (folder / "clip.mp4").write_bytes(CLIP)
    with _serving(folder) as base:
        yield f"{base}/clip.mp4"


@pytest.fixture()
def engine(tmp_path: Path, monkeypatch) -> Engine:
    """The app's engine, pointed at a home of its own — and skipped if bare.

    ``VIDDL_HOME`` is redirected first so that a run here cannot touch the real
    one, then the engine is asked which yt-dlp it can find. When the answer is
    none, this test skips: yt-dlp is not a dependency of the app (it is fetched
    on first run), so a machine without one is not a broken machine.
    """
    monkeypatch.setenv("VIDDL_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("VIDDL_YTDLP", raising=False)
    engine = Engine()
    if engine.find_ytdlp() is None:
        pytest.skip("no yt-dlp here: install one, or run Viddl once")
    return engine


def test_a_real_engine_downloads_a_file_where_the_job_says(
    served: str, engine: Engine, tmp_path: Path
) -> None:
    """The whole promise, with nothing standing in for the engine."""
    seen: List[Progress] = []
    out = tmp_path / "downloads"
    job = DownloadJob(
        engine,
        served,
        MediaOption(key="best", label="Best quality"),
        out,
        on_progress=seen.append,
    )

    path = job.run()

    assert isinstance(path, Path)
    assert path.is_file()
    # What was served is what arrived, in full: a file that exists but is empty
    # (a truncated transfer reported as a success) fails here.
    assert path.read_bytes() == CLIP
    # And it is in the downloads folder the caller named, under a name that came
    # from the output template Viddl passed — not in some temporary corner.
    assert path.parent == out
    assert path.suffix == ".mp4"
    assert "clip" in path.name


def test_a_real_download_reports_progress(served: str, engine: Engine, tmp_path: Path) -> None:
    """The ``VIDDLPROGRESS`` template still matches what yt-dlp prints.

    This is the contract that breaks silently: if yt-dlp renames a progress
    field, the download still works and the bar sits at zero for the whole
    file, which no unit test with a stand-in engine can notice.
    """
    seen: List[Progress] = []
    job = DownloadJob(
        engine,
        served,
        MediaOption(key="best", label="Best quality"),
        tmp_path / "downloads",
        on_progress=seen.append,
    )

    job.run()

    percentages = [p.percent for p in seen if p.phase == "download" and p.percent is not None]
    assert percentages, "no percentage was ever parsed out of yt-dlp's output"
    assert max(percentages) == 100.0
    totals = [p.total for p in seen if p.phase == "download" and p.total]
    assert totals, "no size was ever parsed out of yt-dlp's output"
    assert totals[-1] == len(CLIP)


#: What the DASH manifest is generated from: one second of a test pattern and a
#: sine wave, at a size no screen will ever show. Encoded with ``mpeg4`` rather
#: than x264 because the built-in encoder is in every ffmpeg ever built, while a
#: distribution may leave the H.264 one out — a skipped test would be honest, but
#: a test that fails on somebody's machine because of their codec list is not.
_DASH_INPUT = [
    "-f",
    "lavfi",
    "-i",
    "testsrc2=duration=1:size=128x72:rate=10",
    "-f",
    "lavfi",
    "-i",
    "sine=frequency=440:duration=1",
]


@pytest.fixture()
def two_streams(tmp_path: Path, engine: Engine) -> str:
    """A DASH manifest offering video and audio separately, and its URL.

    yt-dlp merges only when the site hands it two streams, so this is what makes
    the merge path reachable without a third-party site: ffmpeg writes a one
    second manifest with a video representation and an audio one, the folder is
    served over HTTP, and the URL is the manifest itself.
    """
    ffmpeg = engine.find_ffmpeg()
    if ffmpeg is None:
        pytest.skip("no ffmpeg here: a merge needs one (viddl --update-ffmpeg)")
    folder = tmp_path / "dash"
    folder.mkdir()
    command = ffmpeg.command(
        "-y",
        "-loglevel",
        "error",
        *_DASH_INPUT,
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-c:v",
        "mpeg4",
        "-c:a",
        "aac",
        "-b:a",
        "32k",
        "-f",
        "dash",
        "-seg_duration",
        "1",
        "-use_template",
        "0",
        "-use_timeline",
        "0",
        str(folder / "manifest.mpd"),
    )
    made = subprocess.run(command, capture_output=True, text=True, timeout=120)
    assert made.returncode == 0, made.stderr
    with _serving(folder) as base:
        yield f"{base}/manifest.mpd"


def test_a_real_merge_reports_the_file_both_streams_went_into(
    two_streams: str, engine: Engine, tmp_path: Path
) -> None:
    """The seam the last two real bugs lived in: two streams, one file.

    yt-dlp downloads the video and the audio separately, starts ffmpeg to join
    them and says so on a ``[Merger]`` line naming the joined file. The app has
    to notice that line: it is the file the user ends up with, and it is the one
    to report instead of the two halves it came from — which is what it used to
    do, handing people a ``.f0.mp4`` or a ``.m4a`` fragment with a finished name.
    """
    ffmpeg = engine.find_ffmpeg()
    assert ffmpeg is not None  # the fixture skipped otherwise
    option = MediaOption(
        key="bestvideo+bestaudio",
        label="Best quality",
        kind="video",
        needs_ffmpeg=True,
        format_selector="bestvideo+bestaudio",
        merge_container="mp4",
    )
    seen: List[Progress] = []
    out = tmp_path / "downloads"
    job = DownloadJob(engine, two_streams, option, out, on_progress=seen.append)

    path = job.run()

    assert path.is_file()
    # Delivered out of the job's own working folder, under the name the output
    # template asked for, and only that one file: the fragments yt-dlp merged are
    # not handed to anybody as a result.
    assert path.parent == out
    assert path.suffix == ".mp4"
    assert [entry.name for entry in out.iterdir() if entry.is_file()] == [path.name]
    # The merger's line was parsed — without it there is no way to know which
    # file in the folder is the one to report.
    assert [event.phase for event in seen].count("merge") >= 1
    # And it is one file with both streams in it, which is what "merged" means:
    # a download that reported its own video-only fragment would pass a bare
    # "the file exists" check and fail this one.
    probe = subprocess.run(
        ffmpeg.command("-hide_banner", "-i", str(path)),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert "Video:" in probe.stderr
    assert "Audio:" in probe.stderr
