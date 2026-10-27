"""The local page (F16), exercised over real HTTP on loopback.

Nothing here stubs the socket: the server is the one ``viddl --web`` starts, on
a port the operating system picks, and every request goes through a real
connection. That is the only way to check what matters about a front door — the
token in the path, the Host and Origin checks, the headers that stop another
site reading the answers, the refusals, and the download that comes out the
other end.
"""

from __future__ import annotations

import contextlib
import http.client
import json
import threading
import time
from pathlib import Path
from typing import ClassVar
from urllib.parse import urlsplit

import pytest

from viddl import web
from viddl.cli import main
from viddl.download import DownloadCancelled, Progress
from viddl.engine import ToolError
from viddl.extras import Extras
from viddl.formats import MediaInfo, MediaOption
from viddl.network import NetworkOptions
from viddl.web import MAX_BODY_BYTES, Refused, WebApp

URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
FOUND = "https://www.youtube.com/watch?v=abc123"


# ---------------------------------------------------------------------------
# the pieces the page talks to
# ---------------------------------------------------------------------------


def options() -> list:
    return [
        MediaOption(
            "137+140",
            "1080p",
            size_bytes=149_000_000,
            size_is_estimate=True,
            kind="video",
            needs_ffmpeg=True,
            height=1080,
            note="mp4",
        ),
        MediaOption(
            "original",
            "Audio only - M4A",
            size_bytes=4_100_000,
            kind="audio",
            audio_format="M4A",
        ),
    ]


def info(**changes) -> MediaInfo:
    fields = {
        "url": URL,
        "title": "Killing Time",
        "uploader": "Some Channel",
        "duration": 213.0,
        "options": options(),
    }
    fields.update(changes)
    return MediaInfo(**fields)


class FakeProbe:
    """Stands in for ``probe_video``: records the question, answers with one video."""

    def __init__(self, answer=None, failure=None):
        self.calls = []
        self.answer = answer if answer is not None else info()
        self.failure = failure

    def __call__(self, engine, url, cookies=""):
        self.calls.append((url, cookies))
        if self.failure is not None:
            raise self.failure
        return self.answer


class FakeJob:
    """Stands in for ``DownloadJob``: writes a real file, or waits to be stopped."""

    def __init__(self, engine, url, option, out_dir, **kwargs):
        self.url = url
        self.option = option
        self.out_dir = Path(out_dir)
        self.kwargs = kwargs
        self.on_progress = kwargs.get("on_progress")
        self.is_live = bool(kwargs.get("is_live"))
        self.already_had = False
        self.attachments = []
        self.cancelled = threading.Event()
        self.stopped = False
        self.recorded = None
        self.wait = FakeJob.wait
        self.failure = FakeJob.failure
        FakeJob.instances.append(self)

    instances: ClassVar[list] = []
    wait = False
    failure = None

    def run(self):
        if self.on_progress is not None:
            self.on_progress(
                Progress(percent=40.0, downloaded=4_000, total=10_000, speed="1.2 MB/s")
            )
        if self.wait:
            if not self.cancelled.wait(10):
                raise AssertionError("the test never stopped this download")
            self.stopped = True
            raise DownloadCancelled()
        if self.failure is not None:
            raise self.failure
        target = self.out_dir / "Killing Time [abc123].mp4"
        target.write_bytes(b"x" * 4096)
        if self.on_progress is not None:
            self.on_progress(Progress(percent=100.0, phase="done"))
        return target

    def cancel(self, force=False):
        self.cancelled.set()

    def keep_recording(self):
        self.recorded = self.out_dir / "Killing Time [live].mp4"
        self.recorded.write_bytes(b"y" * 2048)
        return self.recorded


@pytest.fixture(autouse=True)
def clean_fake_jobs():
    FakeJob.instances = []
    FakeJob.wait = False
    FakeJob.failure = None
    yield
    FakeJob.instances = []
    FakeJob.wait = False
    FakeJob.failure = None


@pytest.fixture
def out_dir(tmp_path):
    path = tmp_path / "Downloads"
    path.mkdir()
    return path


# ---------------------------------------------------------------------------
# a client that speaks the protocol, not the library
# ---------------------------------------------------------------------------


class Answer:
    def __init__(self, status: int, headers: dict, body: bytes) -> None:
        self.status = status
        self.headers = headers
        self.body = body

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.body)


class Client:
    def __init__(self, url: str) -> None:
        parts = urlsplit(url)
        self.port = parts.port
        self.token = parts.path.strip("/")
        self.root = f"/{self.token}"

    def request(self, method, path, body=None, headers=None, host=None) -> Answer:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            if host is None:
                connection.request(method, path, body=body, headers=headers or {})
            else:
                connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
                connection.putheader("Host", host)
                for name, value in (headers or {}).items():
                    connection.putheader(name, value)
                if body is not None:
                    connection.putheader("Content-Length", str(len(body)))
                connection.endheaders(body)
            answer = connection.getresponse()
            data = answer.read()
            return Answer(
                answer.status,
                {name.lower(): value for name, value in answer.getheaders()},
                data,
            )
        finally:
            connection.close()

    def get(self, path, **kwargs) -> Answer:
        return self.request("GET", path, **kwargs)

    def post(self, path, payload, headers=None, **kwargs) -> Answer:
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        merged = {"Content-Type": "application/json"}
        merged.update(headers or {})
        return self.request("POST", path, body=body, headers=merged, **kwargs)

    def page(self, name: str = "") -> str:
        return f"{self.root}/{name}"

    def api(self, name: str) -> str:
        return f"{self.root}/api/{name}"


@contextlib.contextmanager
def serving(**options):
    server, url = web.build_server(**options)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        yield server, Client(url)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def wait_for(client: Client, job_id: str, states=("done", "failed", "stopped"), timeout=5.0):
    deadline = time.monotonic() + timeout
    state = {}
    while time.monotonic() < deadline:
        state = client.get(client.api(f"jobs/{job_id}")).json()
        if state.get("state") in states:
            return state
        time.sleep(0.02)
    raise AssertionError(f"the download never reached {states}: {state}")


# ---------------------------------------------------------------------------
# where it listens, and what it refuses
# ---------------------------------------------------------------------------


def test_the_page_only_ever_listens_on_loopback():
    server, url = web.build_server()
    try:
        assert web.LOOPBACK == "127.0.0.1"
        assert server.server_address[0] == "127.0.0.1"
        assert urlsplit(url).hostname == "127.0.0.1"
        assert urlsplit(url).port == server.server_address[1]
    finally:
        server.server_close()


def test_two_pages_do_not_share_a_token():
    first, first_url = web.build_server()
    second, _second_url = web.build_server()
    try:
        assert first.app.token != second.app.token
        assert len(first.app.token) >= 24
        assert first.app.token in first_url
    finally:
        first.server_close()
        second.server_close()


def test_a_request_without_the_token_is_not_answered(out_dir):
    probe = FakeProbe()
    with serving(probe=probe, make_job=FakeJob, downloads_dir=lambda: out_dir) as (_, client):
        for path in ("/", "/app.js", "/api/lookup"):
            answer = client.get(path)
            assert answer.status == 404, path
            assert answer.json()["error"]["message"] == "Not found."
        assert probe.calls == []


def test_a_guessed_token_is_not_answered(out_dir):
    with serving(probe=FakeProbe(), make_job=FakeJob, downloads_dir=lambda: out_dir) as (_, client):
        answer = client.get("/0123456789abcdef/")
        assert answer.status == 404
        assert "Viddl" not in answer.text


def test_a_foreign_host_header_is_not_answered(out_dir):
    """A name that resolves to 127.0.0.1 from elsewhere is not this page."""
    with serving(probe=FakeProbe(), make_job=FakeJob, downloads_dir=lambda: out_dir) as (_, client):
        answer = client.get(client.page(), host="evil.example")
        assert answer.status == 404
        assert "Killing" not in answer.text


def test_a_foreign_origin_cannot_use_the_page(out_dir):
    probe = FakeProbe()
    with serving(probe=probe, make_job=FakeJob, downloads_dir=lambda: out_dir) as (_, client):
        answer = client.post(
            client.api("lookup"),
            {"url": URL},
            headers={"Origin": "https://evil.example"},
        )
        assert answer.status == 403
        assert probe.calls == [], "a request from another site must not reach the site"


def test_the_pages_own_origin_is_accepted(out_dir):
    probe = FakeProbe()
    with serving(probe=probe, make_job=FakeJob, downloads_dir=lambda: out_dir) as (_, client):
        answer = client.post(
            client.api("lookup"),
            {"url": URL},
            headers={"Origin": f"http://127.0.0.1:{client.port}"},
        )
        assert answer.status == 200
        assert probe.calls


def test_only_the_page_itself_is_served(out_dir):
    with serving(probe=FakeProbe(), make_job=FakeJob, downloads_dir=lambda: out_dir) as (_, client):
        assert client.get(client.page("index.html")).status == 200
        assert client.get(client.page("app.js")).status == 200
        assert client.get(client.page("app.css")).status == 200
        assert client.get(client.page("robot.txt")).status == 404
        assert client.get(f"{client.root}/../secret").status == 404


def test_the_page_is_not_cacheable_and_not_framable(out_dir):
    with serving(probe=FakeProbe(), make_job=FakeJob, downloads_dir=lambda: out_dir) as (_, client):
        answer = client.get(client.page())
        assert answer.status == 200
        assert answer.headers["content-type"].startswith("text/html")
        assert answer.headers["cache-control"] == "no-store"
        assert answer.headers["x-content-type-options"] == "nosniff"
        assert answer.headers["referrer-policy"] == "no-referrer"
        assert answer.headers["x-frame-options"] == "DENY"
        policy = answer.headers["content-security-policy"]
        assert "default-src 'none'" in policy
        assert "frame-ancestors 'none'" in policy
        assert "script-src 'self'" in policy
        # The one thing that would let another site read these answers.
        assert "access-control-allow-origin" not in answer.headers


def test_the_bare_token_redirects_into_its_own_folder(out_dir):
    with serving(probe=FakeProbe(), make_job=FakeJob, downloads_dir=lambda: out_dir) as (_, client):
        answer = client.get(client.root)
        assert answer.status == 308
        assert answer.headers["location"] == f"{client.root}/"


def test_the_page_never_carries_the_token(out_dir):
    """Nothing the browser loads may hold the secret: it is in the URL alone."""
    with serving(probe=FakeProbe(), make_job=FakeJob, downloads_dir=lambda: out_dir) as (_, client):
        for name in ("", "app.js", "app.css"):
            body = client.get(client.page(name)).text
            assert client.token not in body, name


def test_the_page_loads_nothing_from_anywhere_else(out_dir):
    """No remote script, font or picture — and nothing inline to escape the policy."""
    root = Path(web.__file__).resolve().parent / "web_ui"
    html = (root / "index.html").read_text(encoding="utf-8")
    script = (root / "app.js").read_text(encoding="utf-8")
    style = (root / "app.css").read_text(encoding="utf-8")
    # The placeholder says "https://…" to a person, which is not a resource.
    for body in (html, script, style):
        assert 'src="http' not in body
        assert 'href="http' not in body
        assert "@import" not in body
        assert "url(http" not in body
    assert "fetch('http" not in script
    assert "fetch(\"http" not in script
    assert "onclick" not in html.lower()
    assert "<script>" not in html.lower()
    assert 'src="app.js"' in html
    # Only the page's own endpoints, by relative name so the token stays in the URL.
    assert "api/lookup" in script
    assert "api/download" in script
    assert "api/jobs/" in script


def test_the_page_folder_is_not_a_python_package():
    root = Path(web.__file__).resolve().parent / "web_ui"
    assert not (root / "__init__.py").exists()
    pyproject = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    assert '"web_ui/*.js"' in pyproject


# ---------------------------------------------------------------------------
# requests it will not read
# ---------------------------------------------------------------------------


def test_a_form_post_is_refused(out_dir):
    probe = FakeProbe()
    with serving(probe=probe, make_job=FakeJob, downloads_dir=lambda: out_dir) as (_, client):
        answer = client.post(
            client.api("lookup"),
            {"url": URL},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert answer.status == 415
        assert probe.calls == []


def test_an_oversized_request_is_refused(out_dir):
    with serving(probe=FakeProbe(), make_job=FakeJob, downloads_dir=lambda: out_dir) as (_, client):
        big = {"url": "x" * (MAX_BODY_BYTES + 1)}
        answer = client.post(client.api("lookup"), big)
        assert answer.status == 413


def test_a_body_that_is_not_json_is_refused(out_dir):
    with serving(probe=FakeProbe(), make_job=FakeJob, downloads_dir=lambda: out_dir) as (_, client):
        answer = client.post(client.api("lookup"), b"{not json")
        assert answer.status == 400
        assert answer.json()["error"]["message"] == "That request could not be read."


def test_a_body_that_is_not_an_object_is_refused(out_dir):
    with serving(probe=FakeProbe(), make_job=FakeJob, downloads_dir=lambda: out_dir) as (_, client):
        answer = client.post(client.api("lookup"), b"[1, 2, 3]")
        assert answer.status == 400


def test_a_method_the_page_does_not_use_is_refused(out_dir):
    with serving(probe=FakeProbe(), make_job=FakeJob, downloads_dir=lambda: out_dir) as (_, client):
        answer = client.request("PUT", client.api("lookup"), body=b"{}")
        assert answer.status == 405
        assert "access-control-allow-origin" not in answer.headers


def test_a_link_that_is_not_one_never_reaches_the_site(out_dir):
    probe = FakeProbe()
    with serving(probe=probe, make_job=FakeJob, downloads_dir=lambda: out_dir) as (_, client):
        for text in ("--exec=calc", "file:///etc/passwd", "/tmp/a.mp4", "???"):
            answer = client.post(client.api("lookup"), {"url": text})
            assert answer.status == 400, text
            assert "link or a name" in answer.json()["error"]["message"]
        assert probe.calls == []


# ---------------------------------------------------------------------------
# looking a link up
# ---------------------------------------------------------------------------


def test_a_link_is_looked_up_and_its_rows_are_returned(out_dir):
    probe = FakeProbe()
    with serving(probe=probe, make_job=FakeJob, downloads_dir=lambda: out_dir) as (_, client):
        answer = client.post(client.api("lookup"), {"url": URL})
        assert answer.status == 200
        found = answer.json()
        assert probe.calls == [(URL, "")]
        assert found["url"] == URL
        assert found["title"] == "Killing Time"
        assert found["uploader"] == "Some Channel"
        assert found["duration"] == 213.0
        assert found["live"] is False
        assert [row["key"] for row in found["options"]] == ["137+140", "original"]
        assert found["options"][0]["size"] == 149_000_000
        assert found["options"][0]["sizeText"].startswith("~")
        assert found["options"][1]["kind"] == "audio"
        assert found["options"][0]["detail"] == "mp4 · needs ffmpeg"


def test_a_name_is_searched_for_and_the_found_video_is_what_downloads(out_dir):
    probe = FakeProbe(info(url=FOUND, title="Never Gonna Give You Up"))
    with serving(probe=probe, make_job=FakeJob, downloads_dir=lambda: out_dir) as (_, client):
        answer = client.post(client.api("lookup"), {"url": "  never gonna  give you up "})
        assert answer.status == 200
        assert probe.calls[0][0] == "ytsearch1:never gonna give you up"
        # The page downloads a video, not a query (F14).
        assert answer.json()["url"] == FOUND


def test_a_look_up_that_fails_is_explained_in_words(out_dir):
    failure = ToolError("That video is private.", "Only the owner can download it.")
    with serving(probe=FakeProbe(failure=failure), make_job=FakeJob, downloads_dir=lambda: out_dir) as (_, client):
        answer = client.post(client.api("lookup"), {"url": URL})
        assert answer.status == 502
        error = answer.json()["error"]
        assert error["message"] == "That video is private."
        assert error["hint"] == "Only the owner can download it."


# ---------------------------------------------------------------------------
# downloading
# ---------------------------------------------------------------------------


def test_a_download_is_started_with_the_row_the_page_chose(out_dir):
    probe = FakeProbe()
    with serving(probe=probe, make_job=FakeJob, downloads_dir=lambda: out_dir) as (_, client):
        started = client.post(client.api("download"), {"url": URL, "option": "137+140"})
        assert started.status == 202
        job = FakeJob.instances[-1]
        assert job.url == URL
        assert job.option.key == "137+140"
        assert job.out_dir == out_dir
        assert job.kwargs["title"] == "Killing Time"

        state = wait_for(client, started.json()["id"])
        assert state["state"] == "done"
        assert state["percent"] == 100.0
        assert state["path"] == str(out_dir / "Killing Time [abc123].mp4")
        assert state["name"] == "Killing Time [abc123].mp4"
        assert state["size"] == 4096
        assert state["alreadyHad"] is False
        assert state["error"] is None
        assert state["attachments"] == []


def test_the_page_uses_the_settings_the_command_line_was_given(out_dir, tmp_path):
    extras = Extras.parse("subs")
    network = NetworkOptions.parse(limit_rate="2M")
    archive = tmp_path / "archive.txt"
    with serving(
        probe=FakeProbe(),
        make_job=FakeJob,
        downloads_dir=lambda: out_dir,
        cookies="firefox",
        extras=extras,
        archive=archive,
        network=network,
        live_from_start=True,
    ) as (_, client):
        client.post(client.api("download"), {"url": URL, "option": "original"})
        job = FakeJob.instances[-1]
        assert job.kwargs["cookies"] == "firefox"
        assert job.kwargs["extras"] is extras
        assert job.kwargs["archive"] == archive
        assert job.kwargs["network"] is network
        # The page has no switch for a live stream's start, so the session's wins.
        # This link is not live, so there is nothing to start from yet.
        assert job.kwargs["live_from_start"] is False
        assert job.kwargs["is_live"] is False


def test_a_live_stream_takes_the_sessions_start_choice(out_dir):
    with serving(
        probe=FakeProbe(info(is_live=True)),
        make_job=FakeJob,
        downloads_dir=lambda: out_dir,
        live_from_start=True,
    ) as (_, client):
        client.post(client.api("download"), {"url": URL, "option": "original"})
        job = FakeJob.instances[-1]
        assert job.kwargs["is_live"] is True
        assert job.kwargs["live_from_start"] is True


def test_a_quality_the_link_does_not_offer_is_refused(out_dir):
    with serving(probe=FakeProbe(), make_job=FakeJob, downloads_dir=lambda: out_dir) as (_, client):
        answer = client.post(client.api("download"), {"url": URL, "option": "4096"})
        assert answer.status == 400
        assert "not offered" in answer.json()["error"]["message"]
        assert FakeJob.instances == []


def test_a_download_link_that_is_not_one_is_refused(out_dir):
    with serving(probe=FakeProbe(), make_job=FakeJob, downloads_dir=lambda: out_dir) as (_, client):
        answer = client.post(client.api("download"), {"url": "--exec=calc", "option": "137+140"})
        assert answer.status == 400
        assert FakeJob.instances == []


def test_stopping_a_download_says_so(out_dir):
    FakeJob.wait = True
    with serving(probe=FakeProbe(), make_job=FakeJob, downloads_dir=lambda: out_dir) as (_, client):
        started = client.post(client.api("download"), {"url": URL, "option": "original"})
        job_id = started.json()["id"]
        running = client.get(client.api(f"jobs/{job_id}")).json()
        assert running["state"] == "running"
        assert running["percent"] == 40.0
        assert running["speed"] == "1.2 MB/s"

        stopped = client.post(client.api(f"jobs/{job_id}/stop"), {})
        assert stopped.status == 202
        state = wait_for(client, job_id)
        assert state["state"] == "stopped"
        assert state["path"] is None
        assert FakeJob.instances[-1].stopped is True


def test_stopping_a_live_recording_keeps_what_arrived(out_dir):
    FakeJob.wait = True
    with serving(
        probe=FakeProbe(info(is_live=True)),
        make_job=FakeJob,
        downloads_dir=lambda: out_dir,
    ) as (_, client):
        started = client.post(client.api("download"), {"url": URL, "option": "original"})
        job_id = started.json()["id"]
        client.post(client.api(f"jobs/{job_id}/stop"), {})
        state = wait_for(client, job_id)
        assert state["state"] == "stopped"
        assert state["path"] == str(out_dir / "Killing Time [live].mp4")
        assert state["size"] == 2048


def test_a_failed_download_carries_the_reason(out_dir):
    FakeJob.failure = ToolError("The site refused the download.", "Try again in a minute.")
    with serving(probe=FakeProbe(), make_job=FakeJob, downloads_dir=lambda: out_dir) as (_, client):
        started = client.post(client.api("download"), {"url": URL, "option": "original"})
        state = wait_for(client, started.json()["id"])
        assert state["state"] == "failed"
        assert state["error"]["message"] == "The site refused the download."
        assert state["error"]["hint"] == "Try again in a minute."
        assert state["path"] is None


def test_an_unknown_download_is_refused(out_dir):
    with serving(probe=FakeProbe(), make_job=FakeJob, downloads_dir=lambda: out_dir) as (_, client):
        answer = client.get(client.api("jobs/nothing"))
        assert answer.status == 400
        assert "not running" in answer.json()["error"]["message"]
        stopped = client.post(client.api("jobs/nothing/stop"), {})
        assert stopped.status == 400


def test_closing_the_page_stops_its_downloads(out_dir):
    FakeJob.wait = True
    with serving(probe=FakeProbe(), make_job=FakeJob, downloads_dir=lambda: out_dir) as (server, client):
        client.post(client.api("download"), {"url": URL, "option": "original"})
        job = FakeJob.instances[-1]
        server.app.stop_all(wait=5)
        assert job.cancelled.is_set()


# ---------------------------------------------------------------------------
# the pieces behind the page
# ---------------------------------------------------------------------------


def test_a_refusal_is_a_sentence():
    refusal = Refused("Nope.", "Try again.")
    assert refusal.message == "Nope."
    assert refusal.hint == "Try again."


def test_the_app_can_be_used_without_a_socket(out_dir):
    app = WebApp(token="t" * 32, probe=FakeProbe(), make_job=FakeJob, downloads_dir=lambda: out_dir)
    described = app.lookup(URL)
    assert described["title"] == "Killing Time"
    started = app.start(URL, "original")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state = app.snapshot(started["id"])
        if state["state"] == "done":
            break
        time.sleep(0.02)
    assert state["path"] == str(out_dir / "Killing Time [abc123].mp4")


def test_the_app_refuses_a_row_it_was_not_offered(out_dir):
    app = WebApp(token="t" * 32, probe=FakeProbe(), make_job=FakeJob, downloads_dir=lambda: out_dir)
    with pytest.raises(Refused):
        app.start(URL, "no-such-row")


# ---------------------------------------------------------------------------
# the command line
# ---------------------------------------------------------------------------


def test_serve_prints_the_url_and_stops_on_ctrl_c(monkeypatch, capsys):
    server, url = web.build_server()

    def interrupted(poll_interval=0.25):
        raise KeyboardInterrupt

    monkeypatch.setattr(web, "build_server", lambda **options: (server, url))
    monkeypatch.setattr(server, "serve_forever", interrupted)

    assert web.serve() == 0
    output = capsys.readouterr().out
    assert url in output
    assert "Ctrl+C" in output
    assert "Stopped." in output


def test_web_is_refused_alongside_plain(capsys):
    assert main(["--web", "--plain", URL]) == 1
    assert "--web is a page of its own" in capsys.readouterr().err


def test_web_opens_the_page_empty(capsys):
    assert main(["--web", URL]) == 1
    assert "--web opens the page empty" in capsys.readouterr().err


def test_web_does_not_stand_in_for_a_question_about_a_link(capsys):
    assert main(["--web", "--list-qualities", URL]) == 1
    assert "--web is a page" in capsys.readouterr().err


def test_web_hands_the_session_to_the_page(monkeypatch):
    seen = {}

    def fake_serve(**options):
        seen.update(options)
        return 0

    monkeypatch.setattr("viddl.web.serve", fake_serve)
    assert main(["--web", "--extras", "subs", "--limit-rate", "2M", "--live-from-start"]) == 0
    assert "subtitles" in seen["extras"].summary(options()[0]).lower()
    assert "2M" in seen["network"].summary()
    assert seen["live_from_start"] is True
    assert seen["archive"] is None
    assert seen["cookies"] == ""


def test_a_port_that_cannot_be_opened_is_explained(monkeypatch, capsys):
    def fake_serve(**options):
        raise OSError("Address already in use")

    monkeypatch.setattr("viddl.web.serve", fake_serve)
    assert main(["--web"]) == 1
    err = capsys.readouterr().err
    assert "could not open a page" in err
    assert "Address already in use" in err


def test_the_web_flag_is_in_the_help(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    assert "--web" in capsys.readouterr().out

