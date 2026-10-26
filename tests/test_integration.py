"""One download with the real engine, over a socket on this machine (E1).

Every other test in this suite stands in for yt-dlp. That is what keeps them
fast and offline, and it is also why they cannot answer the question that
matters most for a program whose job is to run somebody else's binary: *does the
command line Viddl builds still work with the yt-dlp people actually have?* The
progress template, the output template, the exit codes, the shape of
``Destination:`` — all of it is a contract with a tool that changes every week.

So this file runs the real thing: the app's own :class:`~viddl.download.DownloadJob`,
the real ``yt-dlp``, and a file served from ``127.0.0.1`` on a port the kernel
picked. No third-party site, no network, nothing to authenticate, and the same
result every time. It is marked ``integration`` and **skipped**, never passed,
when no yt-dlp is installed — a green run that quietly tested nothing would be
worse than an honest skip.

    python -m pytest tests/test_integration.py -q      # or just: pytest
    python -m pip install --upgrade yt-dlp             # what it needs

The weekly **engine** workflow runs exactly this against the newest release,
because that is the failure everybody hits first (see ``E1`` and ``E7`` in
``SUGGESTIONS.md``).
"""

from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path
from typing import List

import pytest

from viddl.download import DownloadJob
from viddl.engine import Engine
from viddl.formats import MediaOption
from viddl.download import Progress

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


@pytest.fixture()
def served(tmp_path: Path):
    """The clip, and the URL that serves it, on a port nobody had to choose."""
    folder = tmp_path / "served"
    folder.mkdir()
    (folder / "clip.mp4").write_bytes(CLIP)
    handler = functools.partial(_Quiet, directory=str(folder))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/clip.mp4"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


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
