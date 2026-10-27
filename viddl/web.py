"""A page on this computer that downloads with Viddl's own tools (F16).

The terminal app is the front door; this is the one for a browser tab. It is
deliberately not a service. The page is served from ``127.0.0.1`` only, on a
port the operating system picks, and every request has to carry a token that is
generated when the page is started and printed only in the URL. Nothing is
reachable from another machine, no other site can read the answers (there are no
CORS headers at all, and a request from a foreign origin is refused), and the
token is not kept anywhere a browser would send it on to someone else.

The downloads are the ones the app and ``--plain`` already make: ``probe_video``
reads the link, ``DownloadJob`` fetches it, and the choices are the rows the
terminal menu shows. What runs is still yt-dlp and ffmpeg on this computer —
which is why this can never be a hosted website, only a local one.
"""

from __future__ import annotations

import contextlib
import json
import logging
import secrets
import sys
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TextIO, Tuple, cast
from urllib.parse import urlsplit

from . import appdata, search
from .download import DownloadCancelled, DownloadJob, Progress, probe_video
from .engine import Engine, ToolError
from .extras import Extras
from .formats import MediaInfo, MediaOption
from .logfile import LOGGER_NAME
from .network import NetworkOptions

#: Only the loopback address is ever bound. ``0.0.0.0`` would put a downloader
#: on the local network, which is the one thing this page must never be.
LOOPBACK = "127.0.0.1"

#: The largest request body the page can legitimately send: a link and an option
#: key. Anything larger is not the page talking, and is refused before it is read.
MAX_BODY_BYTES = 64 * 1024

#: Bytes of randomness behind the token in the URL. 32 characters of URL-safe
#: text: long enough that another page cannot guess it, short enough to read.
TOKEN_BYTES = 24

#: What the page may load. Everything it needs is served from the same origin;
#: no inline script, no remote font, no frame around it.
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'none'"
)

_ASSETS = {
    "index.html": "text/html; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "app.css": "text/css; charset=utf-8",
}


class Refused(Exception):
    """A request the page made that cannot be answered as asked.

    A refusal is a sentence for the person reading the page, never a traceback:
    the page is the only thing that sees it, and it has somewhere to put it.
    """

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


def _asset(name: str) -> Optional[bytes]:
    """One of the page's own files, or ``None`` when the name is not one."""
    if name not in _ASSETS:
        return None
    path = Path(__file__).resolve().parent / "web_ui" / name
    try:
        return path.read_bytes()
    except OSError:  # pragma: no cover - packaging problem
        return None


def _json(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


@dataclass
class _Job:
    """One download as the page sees it: what was asked, and how it went."""

    id: str
    url: str
    title: str
    option: MediaOption
    job: Any
    thread: Optional[threading.Thread] = None
    state: str = "running"
    path: Optional[Path] = None
    error: Optional[ToolError] = None
    already_had: bool = False
    attachments: List[Path] = field(default_factory=list)
    progress: Progress = field(default_factory=Progress)


class WebApp:
    """The state behind the page: one look-up, one download, one answer."""

    def __init__(
        self,
        *,
        token: str,
        engine: Optional[Engine] = None,
        cookies: str = "",
        extras: Optional[Extras] = None,
        archive: Optional[Path] = None,
        network: Optional[NetworkOptions] = None,
        live_from_start: bool = False,
        downloads_dir: Optional[Callable[[], Path]] = None,
        probe: Callable[..., MediaInfo] = probe_video,
        make_job: Callable[..., DownloadJob] = DownloadJob,
    ) -> None:
        self.token = token
        self.engine = engine or Engine()
        # The same session choices the command line already understands (F1,
        # F5, F6, F11, F12). The page is a second front door, not a second app:
        # a link opened there downloads with the settings the run was started
        # with, rather than quietly ignoring them.
        self.cookies = cookies
        self.extras = extras or Extras()
        self.archive = archive
        self.network = network or NetworkOptions()
        self.live_from_start = bool(live_from_start)
        self.downloads_dir = downloads_dir or appdata.downloads_dir
        self._probe = probe
        self._make_job = make_job
        self._jobs: Dict[str, _Job] = {}
        self._lock = threading.Lock()
        #: The one origin the page is served from, filled in by build_server.
        self.origin = ""

    # -- looking a link up ---------------------------------------------------
    def lookup(self, text: str) -> Dict[str, Any]:
        """What this link (or name) offers, in the shape the page renders."""
        target = search.target_for(str(text or ""))
        if target is None:
            raise Refused(
                "That does not look like a link or a name to search for.",
                "Paste a video link, or type the name of one.",
            )
        info = self._probe(self.engine, target, cookies=self.cookies)
        return self._describe(info, target)

    def _describe(self, info: MediaInfo, target: str) -> Dict[str, Any]:
        return {
            # The video a search found, not the words that found it: what the
            # page downloads is what the terminal app would download (F14).
            "url": search.resolved(info, target),
            "title": info.title,
            "uploader": info.uploader,
            "duration": info.duration,
            "live": info.is_live,
            "playlist": info.playlist.count if info.playlist is not None else None,
            "note": info.playlist_note,
            "options": [
                {
                    "key": option.key,
                    "label": option.label,
                    "size": option.size_bytes,
                    "sizeText": option.size_text,
                    "kind": option.kind,
                    "detail": option.detail,
                }
                for option in info.options
            ],
        }

    # -- downloading ---------------------------------------------------------
    def start(self, url: str, key: str) -> Dict[str, Any]:
        """Start one download in a thread of its own and return its id.

        The browser sends back only the link and a row key, never an option
        object: the look-up is asked again (from the remembered answer, so it
        costs nothing) and the row is picked from *that*, so a page cannot
        decide which yt-dlp arguments a download runs with.
        """
        target = search.target_for(str(url or ""))
        if target is None:
            raise Refused("That does not look like a video link.", "Look the link up again.")
        info = self._probe(self.engine, target, cookies=self.cookies)
        option = next((row for row in info.options if row.key == key), None)
        if option is None:
            raise Refused(
                "That quality is not offered for this link.",
                "Look the link up again and pick one of the rows it shows.",
            )

        resolved = search.resolved(info, target)
        live = bool(info.is_live)
        box: Dict[str, Optional[_Job]] = {"record": None}

        def on_progress(progress: Progress) -> None:
            record = box["record"]
            if record is None:
                return
            with self._lock:
                record.progress = progress

        job = self._make_job(
            self.engine,
            resolved,
            option,
            self.downloads_dir(),
            on_progress=on_progress,
            cookies=self.cookies,
            extras=self.extras,
            archive=self.archive,
            network=self.network,
            title=info.title,
            is_live=live,
            # The page has no switch for this, so it follows the command line:
            # `viddl --web --live-from-start` records whole streams, and a plain
            # `viddl --web` records from now, exactly as the app does (F12).
            live_from_start=self.live_from_start and live,
        )
        record = _Job(id=secrets.token_hex(8), url=resolved, title=info.title, option=option, job=job)
        box["record"] = record
        with self._lock:
            self._jobs[record.id] = record
        record.thread = threading.Thread(
            target=self._work, args=(record,), name=f"viddl-web-{record.id}", daemon=True
        )
        record.thread.start()
        return {
            "id": record.id,
            "title": info.title,
            "option": option.label,
            "live": live,
        }

    def _work(self, record: _Job) -> None:
        """Run one download, and turn its ending into something the page shows."""
        state, path, error = "done", None, None
        try:
            path = record.job.run()
        except DownloadCancelled:
            state = "stopped"
            # Stopping a live recording is how it is finished, so what arrived
            # is delivered as a file rather than left as a .part (F12).
            if getattr(record.job, "is_live", False):
                path = record.job.keep_recording()
        except ToolError as exc:
            state, error = "failed", exc
        except Exception as exc:  # pragma: no cover - a thread must not die silently
            logging.getLogger(LOGGER_NAME).exception("the web download failed")
            state = "failed"
            error = ToolError("The download stopped unexpectedly.", raw=str(exc))
        with self._lock:
            record.state = state
            record.path = Path(path) if path is not None else None
            record.error = error
            record.already_had = bool(getattr(record.job, "already_had", False))
            record.attachments = [Path(item) for item in getattr(record.job, "attachments", [])]

    def snapshot(self, job_id: str) -> Dict[str, Any]:
        """Where a download is now, as one object."""
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                raise Refused(
                    "That download is not running any more.",
                    "Look the link up again to start another one.",
                )
            return self._state(record)

    def _state(self, record: _Job) -> Dict[str, Any]:
        progress = record.progress
        path = record.path
        size: Optional[int] = None
        if path is not None:
            try:
                size = path.stat().st_size
            except OSError:  # pragma: no cover - a file that vanished
                size = None
        return {
            "id": record.id,
            "state": record.state,
            "title": record.title,
            "option": record.option.label,
            "percent": progress.percent,
            "downloaded": progress.downloaded,
            "total": progress.total,
            "speed": progress.speed,
            "eta": progress.eta,
            "status": progress.status,
            "phase": progress.phase,
            "path": str(path) if path is not None else None,
            "name": path.name if path is not None else None,
            "folder": str(path.parent) if path is not None else None,
            "size": size,
            "alreadyHad": record.already_had,
            "attachments": [item.name for item in record.attachments],
            "error": (
                {"message": record.error.message, "hint": record.error.hint}
                if record.error is not None
                else None
            ),
        }

    def stop(self, job_id: str) -> Dict[str, Any]:
        """Ask one download to stop; the page then waits for its answer."""
        with self._lock:
            record = self._jobs.get(job_id)
        if record is None:
            raise Refused(
                "That download is not running any more.",
                "Look the link up again to start another one.",
            )
        record.job.cancel()
        return {"id": job_id, "state": "stopping"}

    def stop_all(self, wait: float = 5.0) -> None:
        """Stop every download still running, and give each a moment to let go.

        Called when the page closes. Without it, quitting the terminal would
        leave yt-dlp writing into a folder nothing is watching any more.
        """
        with self._lock:
            records = list(self._jobs.values())
        for record in records:
            if record.state != "running":
                continue
            with contextlib.suppress(Exception):  # pragma: no cover - a stop must not raise
                record.job.cancel()
        deadline = time.monotonic() + max(0.0, wait)
        for record in records:
            thread = record.thread
            if thread is not None and thread.is_alive():
                thread.join(max(0.0, deadline - time.monotonic()))


class _Server(ThreadingHTTPServer):
    """The loopback server: one app, and request threads that cannot hold it open."""

    daemon_threads = True
    allow_reuse_address = True
    #: Set by build_server.
    app: WebApp

    def bound_host(self) -> str:
        """The address it actually bound, not the one it was asked for.

        ``server_address`` is typed as a union with a socket path, because
        ``BaseServer`` serves unix sockets too; this one is always a pair.
        """
        host, _port = cast(Tuple[str, int], self.server_address)
        return str(host)

    def bound_port(self) -> int:
        """The port it bound — what Host and Origin are checked against."""
        _host, port = cast(Tuple[str, int], self.server_address)
        return int(port)


class _Handler(BaseHTTPRequestHandler):
    """Every request is the page's, or it is not answered at all."""

    server_version = "Viddl"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # -- verbs ---------------------------------------------------------------
    def do_GET(self) -> None:
        self._dispatch()

    def do_POST(self) -> None:
        self._dispatch()

    def do_HEAD(self) -> None:
        self._dispatch()

    def do_OPTIONS(self) -> None:
        self._dispatch()

    def do_PUT(self) -> None:
        self._dispatch()

    def do_DELETE(self) -> None:
        self._dispatch()

    def do_PATCH(self) -> None:
        self._dispatch()

    # -- routing -------------------------------------------------------------
    def _dispatch(self) -> None:
        app = cast(_Server, self.server).app
        # A request whose Host is not this loopback server is a name resolving
        # to 127.0.0.1 from somewhere else (DNS rebinding), not this page.
        if not self._host_is_ours(app):
            return self._not_found()
        parts = urlsplit(self.path).path.split("/")
        if len(parts) < 2 or not secrets.compare_digest(parts[1], app.token):
            # The same answer whether the token is missing or wrong: a page
            # that is guessing learns nothing from trying.
            return self._not_found()
        rest = parts[2:]
        if self.command in ("GET", "HEAD"):
            return self._get(app, rest)
        if self.command == "POST":
            return self._post(app, rest)
        return self._fail(405, "Only what the page asks for is answered.")

    def _get(self, app: WebApp, rest: List[str]) -> None:
        if rest == []:
            # Without the slash, the page's relative links would point at the
            # root of the server instead of at the token's folder.
            return self._redirect(f"/{app.token}/")
        if rest == [""]:
            return self._asset("index.html")
        if len(rest) == 1 and rest[0] in _ASSETS:
            return self._asset(rest[0])
        if len(rest) == 3 and rest[:2] == ["api", "jobs"]:
            return self._answer(lambda: app.snapshot(rest[2]))
        return self._not_found()

    def _post(self, app: WebApp, rest: List[str]) -> None:
        # Belt and braces beside the token: a browser sends Origin on every
        # cross-site request, so one from somewhere else is refused here even
        # though it would already need the token to get this far.
        if not self._origin_is_ours():
            return self._fail(403, "That request did not come from the Viddl page.")
        body = self._read_json()
        if body is None:
            return  # _read_json has already answered
        if rest == ["api", "lookup"]:
            return self._answer(lambda: app.lookup(str(body.get("url") or "")))
        if rest == ["api", "download"]:
            return self._answer(
                lambda: app.start(str(body.get("url") or ""), str(body.get("option") or "")),
                status=202,
            )
        if len(rest) == 4 and rest[:2] == ["api", "jobs"] and rest[3] == "stop":
            return self._answer(lambda: app.stop(rest[2]), status=202)
        return self._not_found()

    # -- guards --------------------------------------------------------------
    def _host_is_ours(self, app: WebApp) -> bool:
        host = (self.headers.get("Host") or "").strip().lower()
        port = cast(_Server, self.server).bound_port()
        return host in (f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}")

    def _origin_is_ours(self) -> bool:
        origin = (self.headers.get("Origin") or "").strip().rstrip("/")
        if not origin:
            # No Origin: a same-origin form or an older client. The token and
            # the JSON content type still have to be right, which is what a
            # cross-site page cannot arrange.
            return True
        parts = urlsplit(origin)
        port = cast(_Server, self.server).bound_port()
        return (
            parts.scheme == "http"
            and parts.hostname in ("127.0.0.1", "localhost", "::1")
            and parts.port == port
        )

    def _read_json(self) -> Optional[Dict[str, Any]]:
        kind = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if kind != "application/json":
            # A plain HTML form can only send three content types, none of them
            # this one, so this is also what keeps one from posting here.
            self._fail(415, "The page sends JSON; this request did not.")
            return None
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            self._fail(400, "That request had no length.")
            return None
        if length <= 0 or length > MAX_BODY_BYTES:
            self._fail(413, "That request was too large.")
            return None
        try:
            body = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._fail(400, "That request could not be read.")
            return None
        if not isinstance(body, dict):
            self._fail(400, "That request was not an object.")
            return None
        return body

    # -- answers -------------------------------------------------------------
    def _answer(self, call: Callable[[], Any], status: int = 200) -> None:
        try:
            payload = call()
        except Refused as exc:
            return self._fail(400, exc.message, exc.hint)
        except ToolError as exc:
            # 502: the page is fine, the site or the tool behind it was not.
            return self._fail(502, exc.message, exc.hint)
        except Exception:  # pragma: no cover - an unexpected failure must answer
            logging.getLogger(LOGGER_NAME).exception("the web page failed to answer")
            return self._fail(500, "Viddl could not answer that.", "The log may have more.")
        self._send(status, _json(payload), "application/json")

    def _not_found(self) -> None:
        self._send(404, _json({"error": {"message": "Not found."}}), "application/json")

    def _fail(self, status: int, message: str, hint: Optional[str] = "") -> None:
        error: Dict[str, Any] = {"message": message}
        if hint:
            error["hint"] = hint
        self._send(status, _json({"error": error}), "application/json")

    def _asset(self, name: str) -> None:
        body = _asset(name)
        if body is None:
            return self._not_found()
        self._send(200, body, _ASSETS[name])

    def _redirect(self, location: str) -> None:
        self._send(308, b"", "text/plain; charset=utf-8", extra={"Location": location})

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str,
        extra: Optional[Dict[str, str]] = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # Nothing here is worth remembering: the page changes with every
        # download, and the token must not sit in a cache.
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", CSP)
        # Deliberately no Access-Control-Allow-Origin, ever: another site may
        # not read these answers, so it must not be told it can.
        self.send_header("Connection", "close")
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.close_connection = True
        if self.command != "HEAD":
            self.wfile.write(body)

    # -- logging -------------------------------------------------------------
    def log_request(self, code: Any = "-", size: Any = "-") -> None:
        # The URL is a secret, so the log records the route instead of the path.
        app = getattr(self.server, "app", None)
        path = urlsplit(self.path).path
        if app is not None:
            path = path.replace(app.token, "<token>")
        logging.getLogger(LOGGER_NAME).debug(
            "web: %s %s -> %s", self.command, path, code
        )

    def log_message(self, format: str, *args: Any) -> None:
        logging.getLogger(LOGGER_NAME).debug("web: " + format, *args)


def build_server(
    *,
    port: int = 0,
    token: Optional[str] = None,
    engine: Optional[Engine] = None,
    cookies: str = "",
    extras: Optional[Extras] = None,
    archive: Optional[Path] = None,
    network: Optional[NetworkOptions] = None,
    live_from_start: bool = False,
    downloads_dir: Optional[Callable[[], Path]] = None,
    probe: Callable[..., MediaInfo] = probe_video,
    make_job: Callable[..., DownloadJob] = DownloadJob,
) -> Tuple[_Server, str]:
    """The loopback server and the one URL that opens the page.

    Public because it is what a test drives: the server is an ordinary one, so
    the real HTTP surface — the token, the headers, the refusals — is exercised
    rather than simulated.
    """
    app = WebApp(
        token=token or secrets.token_urlsafe(TOKEN_BYTES),
        engine=engine,
        cookies=cookies,
        extras=extras,
        archive=archive,
        network=network,
        live_from_start=live_from_start,
        downloads_dir=downloads_dir,
        probe=probe,
        make_job=make_job,
    )
    server = _Server((LOOPBACK, port), _Handler)
    server.app = app
    app.origin = f"http://{server.bound_host()}:{server.bound_port()}"
    return server, f"{app.origin}/{app.token}/"


def serve(
    *,
    port: int = 0,
    stream: Optional[TextIO] = None,
    **options: Any,
) -> int:
    """Serve the page until Ctrl+C, then let go of anything still downloading."""
    server, url = build_server(port=port, **options)
    out = stream or sys.stdout
    print(f"Viddl is open at {url}", file=out, flush=True)
    print(
        "This page exists on this computer only; close it with Ctrl+C.",
        file=out,
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("", file=out, flush=True)
        print("Stopped.", file=out, flush=True)
    finally:
        server.app.stop_all()
        server.server_close()
    return 0
