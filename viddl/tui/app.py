"""The Viddl terminal app.

Four screens, in the order you meet them:

1. **Home** - paste a link (or arrive with one from the browser extension)
2. **Qualities** - pick a video quality or an audio format, with estimated sizes
3. **Download** - progress, speed and ETA, cancellable at any time
4. **Done** - the full path of the saved file

And one beside that order: **Recent** (F6), reached from the home screen,
listing what has been saved lately with a way to open it, copy its path or
take it again. Several links pasted at once open **Links** (F7) instead — a
list to read, add to and cut down before any of it is downloaded.

The home screen also offers the link the clipboard is already holding (F8).
It offers it and stops there: nothing is filled in, and nothing is looked up,
until the person says yes.

Everything is driveable with the mouse (click a quality, click a button) and
with the keyboard (arrows, Enter, Tab, Escape).
"""

from __future__ import annotations

import inspect
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from rich.markup import escape
from typing import Any, Callable, List, Optional, Tuple

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Input, ProgressBar, Static

from .. import APP_TITLE, __version__
from .. import appdata
from .. import clipboard as clipboard_mod
from .. import cookies as cookies_mod
from .. import download as download_mod
from .. import engine as engine_mod
from .. import errors
from .. import history as history_mod
from .. import search
from ..download import (
    DownloadCancelled,
    DownloadJob,
    ProbeCancelled,
    Progress,
    probe_playlist,
    probe_video,
)
from ..engine import Engine, Tool, ToolError
from ..extras import EXTRAS, Extras
from ..formats import MediaInfo, MediaOption, Playlist, PlaylistEntry
from ..network import NetworkOptions
from ..queue import QueueItem, QueueJob, QueueSummary
from ..util import human_duration, human_size, redact, safe_video_link, shorten
from .widgets import ChoiceList, ExtraToggle

_ARP = "·"
LOGGER = logging.getLogger("viddl")

#: How long a look-up runs before the screen says so out loud and offers a way
#: out (R9). Five seconds is the point at which a screen that has not changed
#: looks stuck rather than busy.
_PROBE_HINT_SECONDS = 5.0

#: How often the elapsed seconds on screen are refreshed.
_PROBE_TICK_SECONDS = 0.5


def _update_engine(engine: Engine, **kwargs: Any) -> Tuple[Optional[Tool], Tool]:
    """Fetch the newest yt-dlp; the seam a test uses instead of the network (F3)."""
    return engine.update_ytdlp(**kwargs)


@dataclass
class Services:
    """Everything the UI needs from the outside world, swappable for tests."""

    engine: Engine
    probe: Callable[..., MediaInfo] = probe_video
    make_job: Callable[..., DownloadJob] = DownloadJob
    downloads_dir: Callable[[], Path] = appdata.downloads_dir
    #: Which browsers are on this computer, asked when the qualities screen is
    #: drawn (F1). Swappable, so a test says what is installed instead of
    #: depending on the machine running it.
    browsers: Callable[[], List[str]] = cookies_mod.installed_keys
    #: Lists a playlist's videos when the checklist screen opens (F2).
    fetch_playlist: Callable[..., Playlist] = probe_playlist
    #: Fetches the newest engine, returning what was there before and after
    #: (F3). Swappable so an update can be tested without downloading 3 MB.
    update_engine: Callable[..., Tuple[Optional[Tool], Tool]] = _update_engine
    #: What the recent list holds (F6). Swappable, so a test says what was
    #: downloaded instead of reading a file from the machine running it.
    recent: Callable[[], List[history_mod.Entry]] = history_mod.entries
    #: What is on the clipboard, asked once when the home screen opens (F8).
    #: Swappable for the same reason: a test says what was copied instead of
    #: reading the clipboard of the machine running it.
    clipboard: Callable[[], str] = clipboard_mod.read_text


@dataclass
class State:
    """What the user has done so far."""

    url: str = ""
    prefer_audio: bool = False
    #: The quality a remembered preference asks for, from the settings file or
    #: ``VIDDL_QUALITY`` (F13). Empty — every run that did not ask — means the
    #: best row, which is what the app did before there was a settings file.
    prefer_quality: str = ""
    info: Optional[MediaInfo] = None
    option: Optional[MediaOption] = None
    job: Optional[DownloadJob] = None
    saved_path: Optional[Path] = None
    engine_note: str = ""
    engine_error: Optional[str] = None
    #: True once the engine is old enough that updating it is the likeliest fix
    #: for a link that stopped working (F3). It is what puts the "Update
    #: engine" button on screen, so the advice can be acted on where it is read.
    engine_stale: bool = False
    # Set once, at startup, from what appdata could not create (R7). It is a
    # warning rather than a stop: downloads can still work with an engine that
    # was already installed, and the folder may only be un-writable for now.
    folder_problem: str = ""
    #: The browser whose cookies the look-up and download may read (F1). Empty
    #: means none: nothing is read from a browser unless it was asked for.
    cookies_browser: str = ""
    #: The playlist being taken, once the user asked for more than its first
    #: video, and the videos they ticked (F2). Empty means one video.
    playlist: Optional[Playlist] = None
    playlist_entries: List[PlaylistEntry] = field(default_factory=list)
    queue: Optional[QueueJob] = None
    ffmpeg_available: bool = False
    # How big the one-time ffmpeg download would be, once the mirror has been
    # asked (R8). ``None`` means "not known" — never "zero".
    ffmpeg_size: Optional[int] = None
    ffmpeg_size_asked: bool = False
    #: What to add to the download besides the media (F5): nothing, until the
    #: row on the qualities screen is clicked. Kept here rather than on the
    #: screen so going to the playlist and back, or resuming a stopped
    #: download, keeps the same answer.
    extras: Extras = field(default_factory=Extras)
    #: Where the download archive lives, when this run asked for one (F6).
    #: ``None`` is every run that did not: skipping a download is a promise
    #: to keep, not a surprise to spring.
    archive: Optional[Path] = None
    #: How every download in this session uses the network (F11): a speed
    #: limit, a proxy, a fragment count, a throttle. All defaults unless the
    #: command line said otherwise — there is no settings screen to ask.
    network: NetworkOptions = field(default_factory=NetworkOptions)
    #: Record a live stream from its beginning rather than from now (F12).
    #: False is the default, because it is the one a site cannot always
    #: serve — the button on the qualities screen is how it is chosen, and
    #: the command line can set it up front.
    live_from_start: bool = False
    #: Several links collected on the links screen, in the order they will be
    #: taken (F7). One decision — the quality — is asked of the first of them
    #: and used for all; empty means the ordinary one-video flow.
    links: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SignIn:
    """A row that signs a download in, rather than downloading something (F1).

    The qualities list holds these beside the qualities because that is where
    the decision belongs — the row is what turns an unusable link into a usable
    one — but a row is not a quality: choosing it re-asks the site with the
    browser's cookies instead of starting a download.
    """

    browser: str
    label: str
    #: True for the row that turns cookies off again.
    stopping: bool = False


def _plain(value: Any) -> str:
    """Escape text that came from the user or the web.

    Static widgets read their content as Rich markup, so a video title or a
    file path containing square brackets — like the ``[id]`` every downloaded
    file has — would silently lose that part, or fail to render at all.
    """
    return escape(str(value))


def _open_folder(path: Path) -> Optional[str]:
    """Open a folder in the desktop's file manager. Returns an error, if any."""
    import subprocess
    import sys

    target = path if path.is_dir() else path.parent
    if sys.platform.startswith("win"):
        command = ["explorer", str(target)]
    elif sys.platform == "darwin":
        command = ["open", str(target)]
    else:
        command = ["xdg-open", str(target)]
    try:
        subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
    except OSError as exc:
        return f"Could not open the folder automatically ({exc})."
    return None


class ViddlApp(App):
    """The application object; screens live on top of it.

    One guarantee holds the whole hand-off together, and it is worth stating in
    the place the links arrive: **a link from a web page is only ever
    pre-filled.** Any page may ask the operating system to open Viddl with a
    link — that is what ``viddl://`` is for — so the link is treated as
    something to show, never as something to act on. Nothing is downloaded, and
    no quality is chosen, until the person at the keyboard picks one; the probe
    on the way there only reads metadata. ``test_a_hand_off_never_starts_a_download``
    holds this in place.
    """

    TITLE = APP_TITLE
    SUB_TITLE = f"v{__version__}"
    ENABLE_COMMAND_PALETTE = False

    CSS = """
    Screen {
        background: $background;
    }

    #home-panel, #options-panel, #download-panel, #stopped-panel, #result-panel,
    #recent-panel {
        width: 86;
        max-width: 98%;
        height: auto;
        border: round $primary;
        padding: 1 2;
        background: $surface;
        align: center middle;
    }

    #brand {
        text-style: bold;
        color: $accent;
        text-align: center;
        width: 100%;
    }

    .tagline, .sub {
        color: $text-muted;
        text-align: center;
        width: 100%;
        padding-bottom: 1;
    }

    .field-label {
        color: $text-muted;
        padding-top: 1;
    }

    Input {
        margin: 1 0 0 0;
    }

    .buttons {
        height: auto;
        align: center middle;
        padding-top: 1;
    }

    .buttons Button {
        margin: 0 1;
    }

    #engine-status {
        color: $text-muted;
        text-align: center;
        width: 100%;
        padding-top: 1;
        height: auto;
    }

    #clipboard-offer {
        color: $accent;
        text-align: center;
        width: 100%;
        padding-top: 1;
        height: auto;
    }

    #options-heading {
        text-style: bold;
        color: $text;
        width: 100%;
        height: auto;
        padding-bottom: 1;
    }

    #options-sub, #options-status, #options-note {
        color: $text-muted;
        width: 100%;
        height: auto;
    }

    #live-note {
        /* Not muted: this is the one sentence that says a download will not
           end by itself, and it is read before the button is pressed (F12). */
        color: $warning;
        text-style: bold;
        width: 100%;
        height: auto;
        padding-top: 1;
    }

    #extras-row {
        height: 1;
        /* A margin, not padding: padding inside a one-row box leaves it no
           room for its own children, and they land on top of the buttons. */
        margin-top: 1;
        align: left middle;
    }

    #extras-label {
        color: $text-muted;
        width: auto;
        height: 1;
        padding: 0 1 0 0;
    }

    .extra-toggle {
        width: auto;
        height: 1;
        padding: 0 1;
        margin: 0 1 0 0;
    }

    .extra-toggle:hover {
        background: $primary 25%;
    }

    .extra-toggle.on {
        background: $primary;
        color: $text;
        text-style: bold;
    }

    .extra-toggle:focus {
        text-style: bold underline;
    }

    #options-note {
        padding-top: 1;
    }

    #choices {
        height: auto;
        max-height: 12;
        margin-top: 1;
        border: none;
        background: transparent;
    }

    .choice-row {
        height: 1;
        padding: 0 1;
        width: 100%;
        /* A long note is faded out rather than silently chopped. */
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }

    .choice-row:hover {
        background: $primary 25%;
    }

    .choice-row.active {
        background: $primary;
        color: $text;
    }

    #download-panel ProgressBar {
        width: 100%;
        padding: 1 0;
    }

    #download-status {
        width: 100%;
        text-align: center;
        height: auto;
    }

    #download-detail {
        color: $text-muted;
        width: 100%;
        text-align: center;
        height: auto;
    }

    #result-path {
        width: 100%;
        height: auto;
        padding: 1 0;
        color: $success;
        text-style: bold;
    }

    #result-message {
        width: 100%;
        height: auto;
    }

    .hidden {
        display: none;
    }
    """

    BINDINGS = [
        Binding("ctrl+q", "quit_app", "Quit", show=True),
        Binding("ctrl+c", "quit_app", "Quit", show=False),
    ]

    def __init__(
        self,
        initial_url: str = "",
        prefer_audio: bool = False,
        prefer_quality: str = "",
        cookies: str = "",
        extras: Optional[Extras] = None,
        archive: Optional[Path] = None,
        network: Optional[NetworkOptions] = None,
        live_from_start: bool = False,
        services: Optional[Services] = None,
    ) -> None:
        super().__init__()
        self.services = services or Services(engine=Engine())
        # A link arriving from the command line or a browser hand-off is still
        # a link from outside: it is checked here as well as at the CLI.
        self.state = State(
            # A link, or a name to search for (F14): the same rule the command
            # line uses, so the two front doors accept the same things.
            url=search.target_for(initial_url) or "",
            prefer_audio=prefer_audio,
            prefer_quality=prefer_quality,
            # Checked against the closed list on the way in, so the value that
            # reaches a command line cannot be something else (F1).
            cookies_browser=cookies_mod.default_for(cookies),
            # Extras named on the command line arrive switched on; the screen
            # can still switch them off, because they are the same toggles (F5).
            extras=extras or Extras(),
            # The download archive for every job this session starts (F6).
            archive=archive,
            # And how it uses the network (F11): the same answer for every
            # video, a playlist included.
            network=network or NetworkOptions(),
            # Whether a live stream is recorded from its beginning (F12).
            live_from_start=bool(live_from_start),
        )
        problem = appdata.ensure_dirs()
        if problem is not None:
            self.state.folder_problem = problem.line

    # -- lifecycle ----------------------------------------------------------
    def on_mount(self) -> None:
        self.push_screen(HomeScreen())
        self.bootstrap_engine()

    def _post(self, callback: Callable[..., None], *args: Any) -> None:
        """Call a UI callback from a worker thread.

        A background worker must not die because a screen went away, but a
        failure here should still be findable, so it goes to the log file.
        """
        try:
            self.call_from_thread(callback, *args)
        except Exception:  # pragma: no cover - shutdown races and UI bugs
            LOGGER.exception("Viddl: could not update the screen")

    @work(thread=True, exclusive=True, group="engine")
    def bootstrap_engine(self) -> None:
        """Find or fetch yt-dlp in the background; look for ffmpeg too."""
        engine = self.services.engine
        state = self.state

        def status(message: str) -> None:
            state.engine_note = message
            self._post(self._refresh_engine_status)

        def progress(downloaded: int, total: Optional[int]) -> None:
            if total:
                state.engine_note = (
                    f"Setting up the video engine… {downloaded * 100 // total}%"
                )
            else:
                state.engine_note = f"Setting up the video engine… {human_size(downloaded)}"
            self._post(self._refresh_engine_status)

        try:
            tool = engine.ensure_ytdlp(on_status=status, on_progress=progress)
        except ToolError as exc:
            state.engine_error = str(exc)
            state.engine_note = exc.message
            self._post(self._refresh_engine_status)
            return
        except Exception as exc:  # pragma: no cover - defensive
            state.engine_error = str(exc)
            state.engine_note = "The video engine could not be set up."
            self._post(self._refresh_engine_status)
            return

        state.engine_error = None
        # Kept short on purpose: the full build strings go in `viddl --doctor`.
        age = engine_mod.engine_age_days(tool.version)
        state.engine_stale = age is not None and age >= engine_mod.STALE_DAYS
        if state.engine_stale:
            # The likeliest cause of "this link stopped working" is a yt-dlp
            # older than the site, so it is said here rather than only in the
            # doctor — the person clicking a link is not running --doctor. The
            # button beside it is what lets them act without leaving the app.
            state.engine_note = f"Engine {age} days old — update it if a link stopped working"
        else:
            state.engine_note = f"Ready to download  {_ARP}  engine {tool.version or 'ok'}"
        ffmpeg = engine.find_ffmpeg()
        state.ffmpeg_available = ffmpeg is not None
        if ffmpeg is not None:
            state.engine_note += f"  {_ARP}  ffmpeg ready"
        else:
            state.engine_note += f"  {_ARP}  ffmpeg comes later, for conversion or 1080p+"
        self._post(self._refresh_engine_status)

    def _refresh_engine_status(self) -> None:
        for screen in self.screen_stack:
            if isinstance(screen, HomeScreen):
                screen.refresh_engine_status()
        for screen in self.screen_stack:
            if isinstance(screen, OptionsScreen):
                screen.refresh_note()

    # -- the engine, from inside the app (F3) ---------------------------------
    def start_engine_update(self, then_retry: bool = False) -> None:
        """Fetch the newest engine; ``then_retry`` re-asks the link afterwards."""
        self.run_engine_update(then_retry)

    @work(thread=True, exclusive=True, group="engine-update")
    def run_engine_update(self, then_retry: bool = False) -> None:
        """Update yt-dlp without leaving the screen that said it was old.

        The work is ``Engine.update_ytdlp``'s, checksum check included: an
        update inside the app is the same install the flag performs, so there
        is one path that can install something and one place to get it right.
        """
        self._post(self.show_update_message, "Updating the video engine…")

        def status(message: str) -> None:
            self._post(self.show_update_message, message)

        def progress(done: int, total: Optional[int]) -> None:
            text = (
                f"Updating the video engine… {done * 100 // total}%"
                if total
                else f"Updating the video engine… {human_size(done)}"
            )
            self._post(self.show_update_message, text)

        try:
            before, after = self.services.update_engine(
                self.services.engine, on_status=status, on_progress=progress
            )
        except ToolError as exc:
            self._post(self.update_failed, exc)
            return
        except Exception as exc:  # pragma: no cover - defensive
            self._post(self.update_failed, ToolError(str(exc)))
            return
        # A new engine gives new answers, so nothing the old one said about a
        # site is reused — that is what makes "ask the link again" worth doing.
        download_mod.clear_probe_cache()
        self._post(self.update_finished, before, after, then_retry)

    def show_update_message(self, message: str) -> None:
        screen = self.screen
        if isinstance(screen, HomeScreen) and screen.is_mounted:
            screen.query_one("#engine-status", Static).update(_plain(shorten(message, 96)))
        elif isinstance(screen, OptionsScreen) and screen.is_mounted:
            screen.query_one("#options-status", Static).update(_plain(message))

    def update_finished(
        self, before: Optional[Tool], after: Tool, then_retry: bool
    ) -> None:
        self.state.engine_stale = False
        # The same sentence bootstrap uses, so an update does not quietly drop
        # the ffmpeg half of what the home screen says.
        note = f"Ready to download  {_ARP}  engine {after.version or 'ok'}"
        note += (
            f"  {_ARP}  ffmpeg ready"
            if self.state.ffmpeg_available
            else f"  {_ARP}  ffmpeg comes later, for conversion or 1080p+"
        )
        self.state.engine_note = note
        changed = before is None or (before.version or "") != (after.version or "")
        if before is None:
            text = f"Installed yt-dlp {after.version or 'ok'}."
        elif changed:
            text = f"yt-dlp {before.version or 'an older copy'} -> {after.version or 'ok'}"
        else:
            text = f"yt-dlp {after.version or 'ok'} — already the newest release."
        self._refresh_engine_status()

        screen = self.screen
        if isinstance(screen, OptionsScreen) and screen.is_mounted:
            screen.engine_updated(text, changed, then_retry)
            return
        self.notify(_plain(text), timeout=8)

    def update_failed(self, error: ToolError) -> None:
        screen = self.screen
        if isinstance(screen, OptionsScreen) and screen.is_mounted:
            screen.update_failed(error)
            return
        if isinstance(screen, HomeScreen) and screen.is_mounted:
            screen.query_one("#update-engine", Button).disabled = False
            screen.refresh_engine_status()
        self.notify(_plain(error.message), severity="error", timeout=10)

    # -- navigation ---------------------------------------------------------
    def go_home(self) -> None:
        """Return to a fresh home screen, ready for the next link."""
        while len(self.screen_stack) > 2:
            self.pop_screen()
        self.state.url = ""
        self.state.info = None
        self.state.option = None
        self.state.saved_path = None
        self.state.playlist = None
        self.state.playlist_entries = []
        self.state.queue = None
        for screen in self.screen_stack:
            if isinstance(screen, HomeScreen):
                screen.reset()

    def action_quit_app(self) -> None:
        # A queue is a job of jobs: stopping only the one inside it would leave
        # the next video starting inside an app that is closing.
        queue = self.state.queue
        if queue is not None:
            queue.cancel(force=True)
        job = self.state.job
        if job is not None:
            # Quitting is not a pause: nothing can be left behind to keep
            # writing, so the tree is stopped outright. A graceful stop needs
            # a grace period, and the watchdog that would provide it dies with
            # the app.
            job.cancel(force=True)
        self.exit()

    # -- flow ---------------------------------------------------------------
    def start_download(self, option: MediaOption) -> None:
        # One video, whatever was ticked earlier: a list left over from a queue
        # that was abandoned must not turn this click into five downloads.
        self.state.playlist_entries = []
        self.state.option = option
        self.push_screen(DownloadScreen())

    def start_queue(self, entries: List[PlaylistEntry]) -> None:
        """Take these videos, in this order, with the quality already chosen."""
        self.state.playlist_entries = list(entries)
        self.push_screen(DownloadScreen())

    def start_links(self, option: MediaOption) -> None:
        """Take the collected links in order, at the chosen quality (F7).

        The queue runner is the playlist one: a batch of links and a list of
        videos are the same job — one at a time, per-item state, a summary that
        names what failed — and the only difference is where the entries came
        from. Their titles are left empty on purpose, so the finished file is
        named by its own title rather than by the row number that stood in for
        it while the link had no name.
        """
        self.state.option = option
        self.state.playlist = None
        self.state.playlist_entries = [
            PlaylistEntry(index=position, title="", url=link)
            for position, link in enumerate(self.state.links, start=1)
        ]
        self.push_screen(DownloadScreen())


# ---------------------------------------------------------------------------
# screens
# ---------------------------------------------------------------------------


class HomeScreen(Screen):
    """Screen 1: paste a link."""

    BINDINGS = [
        Binding("escape", "quit_app", "Quit", show=True),
        Binding("tab", "focus_next", "Next", show=False),
        # The recent list is one key away, because "where did that file go?"
        # is asked with the app already open (F6). It cannot clash with
        # typing: while the link box has the focus, every letter goes there.
        Binding("r", "recent", "Recent", show=True),
        # The link already on the clipboard is one key away too (F8), for the
        # same reason and with the same caveat: with the box focused, ``p`` is
        # a letter. The button beside it is what the mouse reaches for.
        Binding("p", "paste_clipboard", "Paste link", show=True),
    ]

    #: The link the clipboard offered, waiting for a yes (F8). Empty until the
    #: read comes back with something that is really a link.
    clipboard_link = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="home-panel"):
            yield Static(f"{APP_TITLE}", id="brand")
            yield Static(
                "Download a video or a song by pasting a link or typing its name.",
                classes="tagline",
            )
            yield Static(
                "Paste a link (or several, separated by spaces) — or type a name to "
                "search for",
                classes="field-label",
            )
            yield Input(
                placeholder="https://www.youtube.com/watch?v=…",
                id="url-input",
            )
            yield Static("", id="clipboard-offer")
            with Horizontal(classes="buttons"):
                # Offered only when the clipboard really holds a link (F8), and
                # hidden rather than disabled when it does not: a permanently
                # dead button is a question nobody can answer.
                yield Button("Paste link", id="paste", classes="hidden")
                yield Button("Find qualities", variant="primary", id="go")
                yield Button("Recent", id="recent")
                yield Button("Update engine", id="update-engine")
                yield Button("Quit", id="quit")
            yield Static("", id="engine-status")
        yield Footer()

    def on_mount(self) -> None:
        # Only an old engine has an update worth offering (F3): on a current
        # one the button would fetch the same release for nothing.
        self.query_one("#update-engine", Button).add_class("hidden")
        self.refresh_engine_status()
        if self.app.state.url:
            # A search opens with the name in the box, not its plumbing, so it
            # can be edited and tried again (F14).
            self.query_one("#url-input", Input).value = search.display_text(
                self.app.state.url
            )
            self.call_after_refresh(self.submit_url)
            return
        self.query_one("#url-input", Input).focus()
        # Asked once, on the way in, and not again: a clipboard watched all
        # session would be reading somebody else's text on a timer (F8).
        self.read_clipboard()

    # -- the link the clipboard already has (F8) ----------------------------
    @work(thread=True, exclusive=True, group="clipboard")
    def read_clipboard(self) -> None:
        """Look once for a link on the clipboard, and offer it.

        In a worker because the read is a subprocess and a clipboard owner can
        be slow to answer; on the way back only a *link* is passed on, so a
        password, a paragraph or an address never reaches the screen, let
        alone the log.
        """
        if self.app.state.url:
            # The app was opened with a link; an offer would be noise on top of
            # the download it is already asking about.
            return
        try:
            text = self.app.services.clipboard()
        except Exception:  # pragma: no cover - a test's stub, or a broken tool
            return
        link = safe_video_link(text)
        if link is None:
            return
        self.app._post(self.offer_clipboard, link)

    def offer_clipboard(self, link: str) -> None:
        """Say that a link is there; never put it in the box unbidden (F8).

        The host is shown and not the whole link: a copied address can carry a
        signed token, and the screen is read over shoulders. The link itself
        waits in memory until the person asks for it.
        """
        self.clipboard_link = link
        host = urlsplit(link).hostname or "that site"
        # The button is named first because the link box has the focus on a
        # fresh launch, and a letter typed there is a letter (as with `r`).
        self.query_one("#clipboard-offer", Static).update(
            _plain(f"The clipboard has a link to {host} — click Paste link, or press p.")
        )
        self.query_one("#paste", Button).set_class(False, "hidden")

    def hide_clipboard_offer(self) -> None:
        """Take the offer away, and forget the link with it."""
        self.clipboard_link = ""
        self.query_one("#clipboard-offer", Static).update("")
        self.query_one("#paste", Button).add_class("hidden")

    def action_paste_clipboard(self) -> None:
        """Put the offered link in the box — and stop exactly there (F8).

        Filling is the whole action. Nothing is looked up and nothing is
        downloaded: the link is still a link somebody else wrote, and the
        promise made to a link from a web page (S5) is made to the clipboard
        too. Enter is one keystroke away and it belongs to the person.
        """
        link = self.clipboard_link
        if not link:
            self.notify("The clipboard has no link to use.", severity="warning")
            return
        field = self.query_one("#url-input", Input)
        field.value = link
        field.focus()
        self.hide_clipboard_offer()

    def reset(self) -> None:
        """Empty the link box and put the cursor back in it."""
        field = self.query_one("#url-input", Input)
        field.value = ""
        field.focus()

    def refresh_engine_status(self) -> None:
        widget = self.query_one("#engine-status", Static)
        state = self.app.state
        if state.engine_error:
            # A folder that cannot be written in is the usual reason the engine
            # could not be set up, so it stays on screen under the notification.
            widget.update(_plain(state.folder_problem))
            self.notify(
                _plain(state.engine_error.splitlines()[0]),
                title="Viddl could not set itself up",
                severity="error",
                timeout=12,
            )
            return

        button = self.query_one("#update-engine", Button)
        button.set_class(not state.engine_stale, "hidden")
        button.disabled = False

        lines = [state.folder_problem] if state.folder_problem else []
        if state.engine_note:
            lines.append(shorten(state.engine_note, 96))
        widget.update(_plain("\n".join(lines)))

    def submit_url(self) -> None:
        field = self.query_one("#url-input", Input)
        raw = field.value.strip()
        if not raw:
            self.notify("Paste a link first.", severity="warning")
            field.focus()
            return
        if raw.lower().startswith(("viddl://", "viddl:")) and not any(
            character.isspace() for character in raw
        ):
            raw = _url_from_scheme(raw)
        # The last gate before a link reaches the downloader: only plain
        # http(s) links get through here, so nothing option-shaped can ever be
        # handed over as if it were a flag (S1).
        links, left_out = _links_from_text(raw)
        if not links:
            # A name is not a link and is not meant to be one (F14). Whole text
            # only: "some words <link>" is a pasted page with a link in it, and
            # searching it for its own words would ignore the link.
            target = search.target_for(raw)
            if target is not None and search.is_search(target):
                links = [target]
                # Every word belongs to the name: there is nothing left out,
                # and saying otherwise would be about a link that was not there.
                left_out = 0
            else:
                self.notify(
                    "That looks like text, not a link. Copy the address from "
                    "your browser, or type a name to search for."
                    if " " in raw
                    else "That does not look like a video link or a name. Copy "
                    "the address from your browser, or type a name to search for.",
                    severity="warning",
                    timeout=6,
                )
                field.focus()
                return
        if left_out:
            # Said out loud: a word lost between two links is a person wondering
            # later why one of the three never arrived (F7).
            self.notify(
                f"{left_out} of {left_out + len(links)} things pasted did not look "
                "like a link and were left out.",
                severity="warning",
                timeout=6,
            )
        if len(links) > 1:
            # Several links at once is a queue, not a video: it gets a screen
            # where the list can be read, added to and cut down before any of
            # it is downloaded (F7).
            self.app.state.links = links
            self.app.push_screen(LinksScreen(links=links))
            return
        self.app.state.links = []
        link = links[0]
        self.app.state.url = link
        self.app.push_screen(OptionsScreen())

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.submit_url()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "go":
            self.submit_url()
        elif event.button.id == "paste":
            self.action_paste_clipboard()
        elif event.button.id == "recent":
            self.action_recent()
        elif event.button.id == "update-engine":
            self.action_update_engine()
        elif event.button.id == "quit":
            self.app.action_quit_app()

    def action_recent(self) -> None:
        """Show what has been saved lately (F6)."""
        self.app.push_screen(RecentScreen())

    def action_update_engine(self) -> None:
        self.query_one("#update-engine", Button).disabled = True
        self.app.start_engine_update()

    def action_quit_app(self) -> None:
        self.app.action_quit_app()


def _url_from_scheme(raw: str) -> str:
    """Pull the target link out of a ``viddl://`` hand-off."""
    from ..protocol import parse_scheme_url

    return parse_scheme_url(raw) or ""


def _links_from_text(raw: str) -> Tuple[List[str], int]:
    """Every link in a pasted line, and how many words were not one (F7).

    A batch is pasted rather than typed: several links arrive on separate lines
    or separated by spaces, and a stray word from the page they came from
    arrives with them. The valid links are kept in order, the same link twice is
    one link (asking for a file twice is a mistake, not a queue), and the count
    of what was left out goes back so the screen can say so instead of silently
    dropping it.

    A link is never split on anything but whitespace: a comma or a dot is legal
    inside an address, so cutting ``?v=a,b`` at the comma would corrupt one
    link into two words. Trailing punctuation is a different thing — a link
    copied from the end of a sentence arrives as ``…?v=abc,`` — so the end of
    each token is tried without it. The stripped form wins when both are links,
    because a real address ending in a comma is rarer than a sentence is.
    """
    links: List[str] = []
    left_out = 0
    for token in raw.split():
        trimmed = token.rstrip(",;.")
        link = safe_video_link(trimmed) if trimmed != token else None
        if link is None:
            link = safe_video_link(token)
        if link is None:
            left_out += 1
        elif link not in links:
            links.append(link)
    return links, left_out


def _link_parts(link: str) -> Tuple[str, str]:
    """The two halves a queue row has room for: the site, and where on it (F7).

    The site is what tells two links apart at a glance; the rest — path and
    query — is what tells two links to the same site apart. The query is
    redacted the same way the log is (S4), because a link can carry a token and
    a row on screen is read over shoulders.
    """
    split = urlsplit(link)
    host = split.hostname or link
    rest = split.path or ""
    if split.query:
        rest = f"{rest}?{split.query}"
    return host, shorten(redact(rest), 48)


class LinksScreen(Screen):
    """Screen 1b: several links, in the order they will be taken (F7).

    A queue of links has no playlist behind it, so this screen is where the
    list is collected: one row per link, a box to add another, and a way to cut
    one out. Nothing is looked up and nothing is downloaded here — the qualities
    screen that follows asks once for the whole list, which is the same
    one-decision shape a playlist has (F2).

    The list is the *order*: the queue takes them top to bottom, so a row can be
    moved only by being added again in the position it should have. That is
    enough — a batch is usually pasted in the order it was collected — and it
    keeps this screen out of the reordering business.
    """

    BINDINGS = [
        Binding("escape", "go_back", "Back", show=True),
        # Enter and `d` both mean "take the lot" when the list has the focus;
        # in the box above, Enter adds the link that was just pasted instead,
        # because that is what a person typing in a box expects.
        Binding("enter", "download", "Download all", show=False),
        Binding("d", "download", "Download all", show=False),
        Binding("delete", "remove", "Remove", show=True),
        Binding("x", "remove", "Remove", show=False),
        Binding("c", "clear", "Clear", show=False),
    ]

    def __init__(self, links: Optional[List[str]] = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # A working copy: the app's state is only told what was collected when
        # the list is actually taken, so backing out leaves nothing behind.
        self.links: List[str] = list(links or [])

    def compose(self) -> ComposeResult:
        with Vertical(id="options-panel"):
            yield Static("Several links", id="options-heading")
            yield Static("", id="options-status")
            yield Input(
                placeholder="Paste another link and press Enter",
                id="link-input",
            )
            yield ChoiceList(id="choices")
            with Horizontal(classes="buttons"):
                yield Button("Download all", variant="primary", id="download")
                yield Button("Remove", id="remove")
                yield Button("Clear", id="clear")
                yield Button("Back", id="back")
            yield Static("", id="options-note")
        yield Footer()

    def on_mount(self) -> None:
        self.render_rows()
        if self.links:
            self.query_one("#choices", ChoiceList).focus()
        else:
            self.query_one("#link-input", Input).focus()

    # -- the list -----------------------------------------------------------
    def render_rows(self) -> None:
        table = self.query_one("#choices", ChoiceList)
        cursor = table.cursor_index or 0
        rows: List[Tuple[str, str, str, Any]] = []
        for index, link in enumerate(self.links):
            host, rest = _link_parts(link)
            rows.append((f"{index + 1}. {host}", "", rest, index))
        table.set_choices(rows)
        if rows:
            # Removing the third of five must leave the cursor where the eye is.
            table.select_index(min(cursor, len(rows) - 1))
        self.refresh_note()

    def refresh_note(self) -> None:
        count = len(self.links)
        for button_id in ("download", "remove", "clear"):
            self.query_one(f"#{button_id}", Button).disabled = count == 0
        if count == 0:
            lines = [
                "No links yet.",
                "Paste a link into the box above and press Enter. Several can be "
                "pasted at once on the home screen.",
            ]
        else:
            lines = [
                f"{count} link{'s' if count != 1 else ''}  {_ARP}  they download in order",
                "The quality you choose next is used for every link in the list.",
                "Click a row and press Remove to take one out; Enter takes them all.",
            ]
        self.query_one("#options-note", Static).update(_plain("\n".join(lines)))

    def selected_index(self) -> Optional[int]:
        value = self.query_one("#choices", ChoiceList).highlighted_value
        return value if isinstance(value, int) else None

    # -- adding and removing ------------------------------------------------
    def add_link(self) -> None:
        """Add what is in the box, if it is a link and not already there."""
        field = self.query_one("#link-input", Input)
        raw = field.value.strip()
        if not raw:
            return
        link = safe_video_link(raw)
        if link is None:
            self.notify(
                "That does not look like a video link. Copy the address from your browser.",
                severity="warning",
                timeout=6,
            )
            field.focus()
            return
        if link in self.links:
            self.notify("That link is already in the list.", severity="warning", timeout=4)
            return
        self.links.append(link)
        field.value = ""
        self.render_rows()
        # The box keeps the focus on purpose: a batch is pasted as several
        # links in a row, and making the person click back into the box between
        # every one of them would be the whole friction this screen exists for.
        table = self.query_one("#choices", ChoiceList)
        table.select_index(len(self.links) - 1)
        field.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.add_link()

    def action_remove(self) -> None:
        index = self.selected_index()
        if index is None or not (0 <= index < len(self.links)):
            return
        host, _ = _link_parts(self.links.pop(index))
        self.render_rows()
        self.notify(f"Removed {host}.", timeout=3)

    def action_clear(self) -> None:
        count = len(self.links)
        if not count:
            return
        self.links = []
        self.render_rows()
        self.notify(f"Removed all {count} links.", timeout=3)

    # -- taking it ----------------------------------------------------------
    def action_download(self) -> None:
        """Hand the whole list to the qualities screen (F7).

        The quality is not chosen here because a queue is one decision, not N:
        the first link is the one asked about — its menu is the shape of the
        choice — and the row that is picked is used for every link, in order.
        """
        if not self.links:
            self.notify("Add at least one link first.", severity="warning")
            return
        state = self.app.state
        state.links = list(self.links)
        state.url = self.links[0]
        state.info = None
        state.option = None
        state.playlist = None
        state.playlist_entries = []
        state.saved_path = None
        self.app.push_screen(OptionsScreen())

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def on_choice_list_selected(self, message: ChoiceList.Selected) -> None:
        """Clicking or pressing Enter on a row takes the whole list.

        A row here is not a choice to make — the list has no per-row action but
        Remove (a button and a key of its own) — so the obvious meaning of
        "activate the list" is the one this screen exists for.
        """
        message.stop()
        self.action_download()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "download":
            self.action_download()
        elif button_id == "remove":
            self.action_remove()
        elif button_id == "clear":
            self.action_clear()
        elif button_id == "back":
            self.action_go_back()


class OptionsScreen(Screen):
    """Screen 2: choose a quality."""

    #: The name this look-up searched for, when it was a search rather than a
    #: link (F14). Empty for every link, which is most runs.
    search_query: str = ""

    BINDINGS = [
        Binding("escape", "go_back", "Back", show=True),
        Binding("b", "go_back", "Back", show=False),
        Binding("enter", "download", "Download", show=False),
        Binding("d", "download", "Download", show=False),
        Binding("p", "playlist", "Choose videos", show=False),
        Binding("u", "update_engine", "Update engine", show=False),
        Binding("l", "live_start", "Record from the start", show=False),
        # The extras are buttons first; these keys are the same switches for
        # anyone who would rather not reach for the mouse (F5).
        Binding("s", "extra('subs')", "Subtitles", show=False),
        Binding("m", "extra('details')", "Details", show=False),
        Binding("t", "extra('cover')", "Cover image", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="options-panel"):
            yield Static("", id="options-heading")
            yield Static("Looking up the link…", id="options-status")
            # What a live stream means for this download (F12). Its own line,
            # not the note under the menu: that one describes the row under
            # the cursor, and this is true of every row.
            yield Static("", id="live-note")
            yield ChoiceList(id="choices")
            with Horizontal(id="extras-row"):
                yield Static("Extras", id="extras-label")
                for extra in EXTRAS:
                    yield ExtraToggle(
                        extra.key,
                        extra.label,
                        id=f"extra-{extra.key}",
                        classes="extra-toggle",
                        # What the one-word label actually adds, for a mouse
                        # that stops on it.
                        tooltip=extra.note,
                    )
            with Horizontal(classes="buttons"):
                yield Button("Download", variant="primary", id="download")
                yield Button("Choose videos…", id="playlist")
                yield Button("Update engine", id="update-engine")
                yield Button("Set up ffmpeg", id="get-ffmpeg")
                yield Button("Record from the start", id="live-start")
                yield Button("Stop looking", id="stop-probe")
                yield Button("Back", id="back")
            yield Static("", id="options-note")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#get-ffmpeg", Button).add_class("hidden")
        # Only a playlist link has anything to choose from; on every other link
        # the button would be a promise the screen cannot keep (F2).
        self.query_one("#playlist", Button).add_class("hidden")
        # Updating is offered only where it is the likely fix: a link that just
        # failed, on an engine old enough to be the reason (F3).
        self.query_one("#update-engine", Button).add_class("hidden")
        # Only a live stream has a start to choose (F12), and it cannot be
        # known before the site has answered.
        self.query_one("#live-note", Static).add_class("hidden")
        self.query_one("#live-start", Button).add_class("hidden")
        self.sync_extras()
        self._start_looking()

    # -- extras (F5) ---------------------------------------------------------
    def sync_extras(self) -> None:
        """Make the toggles say what the state actually is.

        The tick is part of the label, not only the colour, because colour is
        the first thing a monochrome terminal and a screen reader lose.
        """
        extras = self.app.state.extras
        for extra in EXTRAS:
            toggle = self.query_one(f"#extra-{extra.key}", ExtraToggle)
            toggle.set_on(extras.has(extra.key))

    def on_extra_toggle_pressed(self, message: ExtraToggle.Pressed) -> None:
        message.stop()
        self.action_extra(message.key)

    def action_extra(self, key: str) -> None:
        """Switch one extra on or off, and say what that means for this row."""
        before = self.app.state.extras.has(key)
        self.app.state.extras = self.app.state.extras.toggled(key)
        self.sync_extras()
        self.refresh_note()
        extra = next((item for item in EXTRAS if item.key == key), None)
        if extra is None:  # pragma: no cover - the binding names a real key
            return
        if not before and extra.video_only and self._audio_highlighted():
            self.notify(
                "Subtitles only go into a video: this download is audio, so it "
                "will not include them.",
                severity="warning",
                timeout=5,
            )

    def _highlighted_option(self) -> Optional[MediaOption]:
        value = self.query_one("#choices", ChoiceList).highlighted_value
        return value if isinstance(value, MediaOption) else None

    def _audio_highlighted(self) -> bool:
        option = self._highlighted_option()
        return option is not None and option.kind == "audio"

    def _start_looking(self) -> None:
        """Begin a look-up: a fresh stop button and a fresh clock (R9, F1).

        Called when the screen mounts and again when the cookie choice changes,
        because a look-up that has already finished left its stop event set —
        reusing that one would report the new question as a cancellation.
        """
        self._stopped = False
        self._hinted = False
        self._cancel = threading.Event()
        self._started_at = time.monotonic()
        self.query_one("#download", Button).disabled = True
        self.query_one("#stop-probe", Button).add_class("hidden")
        # A fresh look-up is a fresh link: the last answer's live note and its
        # start button do not describe it until this one answers (F12).
        self.query_one("#live-note", Static).add_class("hidden")
        self.query_one("#live-start", Button).add_class("hidden")
        self.query_one("#options-status", Static).update("Looking up the link…")
        ticker = getattr(self, "_ticker", None)
        if ticker is not None:
            ticker.stop()
        self._ticker = self.set_interval(_PROBE_TICK_SECONDS, self.show_elapsed)
        self.load_info(self.app.state.url)

    def on_unmount(self) -> None:
        """Leaving the screen ends the look-up: nothing is left running behind."""
        self.stop_looking()

    # -- loading ------------------------------------------------------------
    def show_elapsed(self) -> None:
        """Say how long the link has been taking, and offer a way out.

        A look-up can legitimately take a minute on a slow site, so the screen
        must not look frozen while it does. After a few seconds it stops just
        counting and says plainly that this is normal, with a button — because
        the alternative is a 150-second wait with no way to call it off.
        """
        if self._stopped or not self.is_mounted:
            return
        elapsed = time.monotonic() - self._started_at
        # Whole seconds: this is a clock the user is waiting on, and "2.0s" for
        # something 2.04 s old reads like a stopwatch that has jammed.
        self.query_one("#options-status", Static).update(
            f"Looking up the link… {int(elapsed)}s"
        )
        if elapsed < _PROBE_HINT_SECONDS or self._hinted:
            return
        self._hinted = True
        self.query_one("#stop-probe", Button).remove_class("hidden")
        self.query_one("#options-note", Static).update(
            _plain(
                "Still looking — some sites are slow to answer. "
                "Press “Stop looking” to give up."
            )
        )

    def stop_looking(self) -> None:
        """Ask the look-up to stop, at once: the yt-dlp process is killed."""
        self._stopped = True
        self._cancel.set()
        ticker = getattr(self, "_ticker", None)
        if ticker is not None:
            ticker.stop()

    def action_stop_probe(self) -> None:
        if self._stopped:
            return
        self.stop_looking()
        self.query_one("#options-status", Static).update("Stopping…")

    @work(thread=True, exclusive=True, group="probe")
    def load_info(self, url: str) -> None:
        try:
            info = self.app.services.probe(
                self.app.services.engine,
                url,
                cancel=self._cancel,
                cookies=self.app.state.cookies_browser,
            )
        except ProbeCancelled:
            self.app._post(self.show_stopped)
            return
        except ToolError as exc:
            self.app._post(self.show_error, exc)
            return
        except Exception as exc:  # pragma: no cover - defensive
            self.app._post(self.show_error, ToolError(str(exc)))
            return
        self.app._post(self.show_info, info)

    def show_stopped(self) -> None:
        """Back to the link box, with the link still in it.

        Only if this screen is still the one on top: Back already leaves by
        itself, and popping a second time would take the home screen with it —
        leaving the app on an empty screen with nothing to type in.
        """
        if not self.is_mounted or self.app.screen is not self:
            return
        self.app.pop_screen()
        self.app.notify("Stopped looking up that link.", timeout=4)

    def show_error(self, error: ToolError) -> None:
        if not self.is_mounted:
            return
        self.stop_looking()
        self.query_one("#stop-probe", Button).add_class("hidden")
        self.query_one("#playlist", Button).add_class("hidden")
        self.query_one("#live-note", Static).add_class("hidden")
        self.query_one("#live-start", Button).add_class("hidden")
        update = self.query_one("#update-engine", Button)
        update.set_class(not self.app.state.engine_stale, "hidden")
        update.disabled = False
        status = self.query_one("#options-status", Static)
        status.update(_plain(error.message))
        self.app.state.info = None
        self.query_one("#options-heading", Static).update("That link did not work")
        note = self.query_one("#options-note", Static)
        # A link that answers only to a signed-in session fails exactly here, so
        # the row that fixes it is offered here too: advice that says "choose the
        # sign-in row" with no row to choose is not advice (F1).
        rows = self.sign_in_rows()
        hint = error.hint or "Press Escape to try another link."
        if self.app.state.engine_stale:
            # The single most common cause of "it broke" is an engine older
            # than the site, and the button beside this sentence is the fix
            # (F3). Said only when it is true of the copy being used.
            hint = (
                f"{hint}\nThe video engine is old, which is the usual reason a "
                "link stops working. Press “Update engine”, and the link is "
                "asked again straight afterwards."
            )
        if not rows and errors.SIGN_IN_MARK in hint:
            # No browser was found, so there is no row: say what is actually
            # missing instead of pointing at a choice that is not on screen.
            hint = (
                "Reading a sign-in needs a browser on this computer, and none "
                "was found. Press Escape to try another link."
            )
        note.update(_plain(hint))
        self.query_one("#download", Button).disabled = True
        self.query_one("#update-engine", Button).disabled = False
        if rows:
            table = self.query_one("#choices", ChoiceList)
            table.set_choices(rows)
            table.focus()
        self.notify(_plain(error.message), severity="error", timeout=10)

    def show_info(self, info: MediaInfo) -> None:
        if not self.is_mounted:
            return
        self.stop_looking()
        self.query_one("#stop-probe", Button).add_class("hidden")
        self.query_one("#update-engine", Button).add_class("hidden")
        # A name is answered by a video, and from here on it is that video
        # (F14): a download, an archive entry, a recent entry and a resume all
        # have to name the same thing, and a query is not a thing.
        if search.is_search(self.app.state.url):
            self.search_query = search.query_from(self.app.state.url)
            self.app.state.url = search.resolved(info, self.app.state.url)
        self.app.state.info = info
        heading = self.query_one("#options-heading", Static)
        status = self.query_one("#options-status", Static)
        note = self.query_one("#options-note", Static)

        title = shorten(info.title, 96)
        sub_bits = [info.uploader, info.extractor]
        if info.is_live:
            sub_bits.append("LIVE")
        if self.app.state.cookies_browser:
            # A download that reads an account's cookies must never look like
            # one that does not (F1).
            sub_bits.append(
                f"signed in with {cookies_mod.label_for(self.app.state.cookies_browser)}"
            )
        sub = "  ·  ".join(bit for bit in sub_bits if bit)
        if info.duration:
            sub = f"{human_duration(info.duration)}  ·  {sub}" if sub else human_duration(info.duration)
        heading.update(f"{_plain(title)}\n[dim]{_plain(sub)}[/dim]" if sub else _plain(title))
        status.update("")
        self.sync_live()

        choices: List[Tuple[str, str, str, Any]] = [
            (opt.label, opt.size_text, opt.detail, opt) for opt in info.options
        ]
        choices.extend(self.sign_in_rows())
        table = self.query_one("#choices", ChoiceList)
        table.set_choices(choices)
        table.focus()

        if info.playlist is not None and not self.app.state.links:
            # The rest of the playlist is an offer, not a second screen nobody
            # asked for: the button is there, and pressing it is the asking.
            # Not offered for a batch of links (F7): that is already a list,
            # and replacing it with this link's playlist would quietly throw
            # the rest of the batch away.
            self.query_one("#playlist", Button).remove_class("hidden")
            if info.playlist_note:
                note.update(
                    _plain(
                        f"{info.playlist_note}. Download takes the first one; "
                        "“Choose videos…” picks which of them to take."
                    )
                )

        if not info.options:
            self.query_one("#options-status", Static).update(
                "No downloadable qualities were found for this link."
            )
            return

        self.query_one("#download", Button).disabled = False
        # A remembered quality decides where the cursor starts, not what is
        # downloaded — the person still chooses (F13). Audio is a flag and
        # beats it, which is why it is applied last.
        self.select_preferred_quality(info)
        if self.app.state.prefer_audio:
            for index, opt in enumerate(info.options):
                if opt.kind == "audio":
                    table.select_index(index)
                    break
        self.refresh_note()

    def select_preferred_quality(self, info: MediaInfo) -> None:
        """Put the cursor on the row a remembered quality asks for (F13).

        The matcher is the one ``--quality`` already uses, so ``720`` means the
        same row here and in ``--plain``. A link that does not offer it keeps
        the usual best row: a preference must never turn a working link into a
        failure, and the next thing it could do about it is nothing.
        """
        wanted = self.app.state.prefer_quality
        if not wanted or not info.options:
            return
        # Imported here because ``plain`` pulls in the download machinery, and
        # the app only needs its matcher on the one screen that has a menu.
        from ..plain import choose_option

        try:
            chosen = choose_option(info, quality=wanted)
        except ToolError:
            return
        table = self.query_one("#choices", ChoiceList)
        for index, option in enumerate(info.options):
            if option is chosen:
                table.select_index(index)
                return

    # -- live streams (F12) --------------------------------------------------
    def sync_live(self) -> None:
        """Say what this live link means, and offer the one choice it has.

        A live stream does not end by itself, which is the thing a person
        needs to know before pressing Download: the recording starts now, and
        it stops when they say so. The button is the other half — asking for
        the part the site has already played — and the sentence repeats which
        answer is in force, so the button never has a state nobody can see.
        """
        info = self.app.state.info
        note = self.query_one("#live-note", Static)
        button = self.query_one("#live-start", Button)
        if info is None or not info.is_live:
            note.add_class("hidden")
            button.add_class("hidden")
            return
        note.remove_class("hidden")
        button.remove_class("hidden")
        from_start = self.app.state.live_from_start
        # The text is the state: a tick in a word, not only a colour.
        button.label = "Recording from the start" if from_start else "Record from the start"
        if from_start:
            note.update(
                _plain(
                    "Live stream. Viddl will record from the beginning, if the site "
                    "can still serve it. Stop when you have enough: what has been "
                    "recorded is saved."
                )
            )
        else:
            note.update(
                _plain(
                    "Live stream. Recording starts now, not from the beginning. "
                    "Stop when you have enough: what has been recorded is saved."
                )
            )

    def action_live_start(self) -> None:
        """Switch between recording from now and from the beginning (F12)."""
        button = self.query_one("#live-start", Button)
        if button.has_class("hidden"):
            # Not a live link: there is no start to choose, and `l` must not
            # change a setting nothing on screen is offering.
            return
        self.app.state.live_from_start = not self.app.state.live_from_start
        self.sync_live()

    def sign_in_rows(self) -> List[Tuple[str, str, str, Any]]:
        """The rows that turn browser cookies on or off (F1).

        One row per browser found on this computer, because which session to
        read is the user's decision: somebody signed in to a site in Firefox
        and not in Chrome needs the second row. Once cookies are in use there
        is one row instead, and it turns them off again.
        """
        browser = self.app.state.cookies_browser
        if browser:
            label = f"Signing in with {cookies_mod.label_for(browser)}"
            return [(label, "", "choose to stop", SignIn(browser=browser, label=label, stopping=True))]
        rows: List[Tuple[str, str, str, Any]] = []
        for key in self.app.services.browsers():
            label = f"Sign in with {cookies_mod.label_for(key)}"
            rows.append((label, "", "cookies for this site", SignIn(browser=key, label=label)))
        return rows

    def choose_sign_in(self, row: SignIn) -> None:
        """Re-ask the site with, or without, the browser's cookies (F1).

        The look-up is run again rather than reused: a signed-in session can
        hand back qualities an anonymous one never sees, and the remembered
        answer is keyed on the cookie choice, so the two cannot be confused.
        """
        state = self.app.state
        state.cookies_browser = "" if row.stopping else row.browser
        label = cookies_mod.label_for(row.browser)
        if row.stopping:
            self.app.notify(f"Back to downloading without {label}'s cookies.", timeout=4)
        else:
            self.app.notify(f"Reading {label}'s cookies for this site.", timeout=6)
        self._start_looking()

    # -- interaction --------------------------------------------------------
    def refresh_note(self) -> None:
        table = self.query_one("#choices", ChoiceList)
        option: Optional[MediaOption] = table.highlighted_value
        note = self.query_one("#options-note", Static)
        button = self.query_one("#get-ffmpeg", Button)
        state = self.app.state

        if option is None:
            button.add_class("hidden")
            return

        if isinstance(option, SignIn):
            # The row is the one place a person is told what handing over an
            # account's cookies means, so it says it before the click (F1).
            button.add_class("hidden")
            label = cookies_mod.label_for(option.browser)
            note.update(
                _plain(
                    cookies_mod.active_note(label)
                    if option.stopping
                    else cookies_mod.note(label)
                )
            )
            return

        needs_ffmpeg = option.needs_ffmpeg or state.extras.needs_ffmpeg_for(option)
        if needs_ffmpeg and not state.ffmpeg_available:
            button.remove_class("hidden")
            note.update(_plain(self.ffmpeg_prompt(state, option)))
        else:
            button.add_class("hidden")
            info = state.info
            lines: List[str] = []
            batching = len(state.links) > 1
            if batching:
                # Said before the quality's own note, because it changes what
                # the click means: one decision, several videos (F7).
                lines.append(
                    f"{len(state.links)} links  {_ARP}  this quality is used for "
                    "all of them, in the order they were added."
                )
            if self.search_query:
                # What was found, and that something else could have been: a
                # search is a convenience, and the menu makes the answer visible
                # before anything is downloaded (F14).
                lines.append(
                    f'First result for "{self.search_query}". '
                    "Press Escape to try a different name."
                )
            if not batching and info is not None and info.playlist_note:
                lines.append(
                    f"{info.playlist_note}. Download takes the first one; "
                    "“Choose videos…” picks which of them to take."
                )
            elif option.kind == "audio":
                # Two different promises, so they are worded differently (F4):
                # the original row really is the site's own file, while every
                # other row is an encoder doing its best with what it was given.
                folder = self.app.services.downloads_dir()
                if option.keeps_source_audio:
                    lines.append(
                        f"Audio only — the site's own {option.audio_format} file, "
                        f"saved to {folder} without re-encoding. Nothing is "
                        "converted, so nothing is lost."
                    )
                else:
                    lines.append(
                        f"Audio only — converted to {option.audio_format} in "
                        f"{folder}. Sizes are a rough guide: converting cannot "
                        "add quality the source does not have."
                    )
            else:
                folder = self.app.services.downloads_dir()
                lines.append(f"Saves to {folder}  ·  {option.size_text}")
            extras_note = self.extras_note(option)
            if extras_note:
                lines.append(extras_note)
            network_note = self.network_note()
            if network_note:
                lines.append(network_note)
            note.update(_plain("\n".join(lines)))

    def network_note(self) -> str:
        """How this session was told to use the network, when it was (F11).

        The flags that set it arrive on the command line, before any screen is
        drawn, so the only place they can be read back is here — and a download
        that is slower than expected should say why somewhere other than the
        shell that launched it.
        """
        network = self.app.state.network
        if not network:
            return ""
        return f"Network: {network.summary()}."

    def extras_note(self, option: Optional[MediaOption]) -> str:
        """What the ticked extras will do to this row, in one sentence (F5)."""
        extras = self.app.state.extras
        if not extras:
            return ""
        skipped = extras.skipped(option)
        if skipped and not extras.applicable(option):
            return (
                f"{skipped[0].label} will not be added: an audio download has "
                "nowhere to put them."
            )
        sentence = f"Also adding: {extras.summary(option)}."
        if extras.has("cover"):
            # Said plainly because the button does not embed it: the toolchain
            # Viddl installs cannot, so the picture is a file of its own.
            sentence += " The picture is saved beside the file, not inside it."
        if skipped:
            sentence += f" {skipped[0].label} are skipped for an audio download."
        return sentence

    def ffmpeg_prompt(self, state: State, option: Optional[MediaOption] = None) -> str:
        """What setting ffmpeg up costs, before the user agrees to it.

        150 MB is a decision, so the size is asked for as soon as the button
        appears — and the asking is in a worker, because the answer needs the
        network. Until it arrives the sentence stays true without a number.
        """
        if option is not None and not option.needs_ffmpeg and state.extras:
            text = (
                "The extras you ticked need ffmpeg to be written. "
                "Click “Set up ffmpeg” once and Viddl keeps it for good."
            )
        else:
            text = (
                "This option needs ffmpeg to join or convert the file. "
                "Click “Set up ffmpeg” once and Viddl keeps it for good."
            )
        if state.ffmpeg_size:
            return f"{text} The download is about {human_size(state.ffmpeg_size)}."
        if not state.ffmpeg_size_asked:
            state.ffmpeg_size_asked = True
            self.ask_how_big_ffmpeg_is()
        return text

    @work(thread=True, exclusive=True, group="ffmpeg-size")
    def ask_how_big_ffmpeg_is(self) -> None:
        """Ask the mirror how big ffmpeg is, without downloading any of it."""
        try:
            size = self.app.services.engine.ffmpeg_size_hint()
        except Exception:  # pragma: no cover - a nicety, never a failure
            size = None
        self.app._post(self.show_ffmpeg_size, size)

    def show_ffmpeg_size(self, size: Optional[int]) -> None:
        self.app.state.ffmpeg_size = size
        if not self.is_mounted:
            return
        self.refresh_note()

    def on_choice_list_highlighted(self, message: ChoiceList.Highlighted) -> None:
        self.refresh_note()

    def on_choice_list_selected(self, message: ChoiceList.Selected) -> None:
        message.stop()
        if isinstance(message.value, SignIn):
            self.choose_sign_in(message.value)
        elif isinstance(message.value, MediaOption):
            # Through the action, not straight to the download: choosing a row
            # with Enter and pressing Download must mean the same thing, and
            # for a batch of links that meaning is the queue (F7).
            self.action_download()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "download":
            self.action_download()
        elif button_id == "playlist":
            self.action_playlist()
        elif button_id == "update-engine":
            self.action_update_engine()
        elif button_id == "back":
            self.action_go_back()
        elif button_id == "get-ffmpeg":
            self.setup_ffmpeg()
        elif button_id == "stop-probe":
            self.action_stop_probe()
        elif button_id == "live-start":
            self.action_live_start()


    def action_download(self) -> None:
        table = self.query_one("#choices", ChoiceList)
        option: Optional[MediaOption] = table.highlighted_value
        if not isinstance(option, MediaOption):
            # The sign-in row is a thing to click, not a thing to download.
            self.notify("Choose a quality first.", severity="warning")
            return
        if len(self.app.state.links) > 1:
            # A batch collected on the links screen (F7): the quality under the
            # cursor is one decision for the whole list, exactly as it is for a
            # playlist.
            self.app.start_links(option)
            return
        self.app.start_download(option)

    def action_playlist(self) -> None:
        """Offer the rest of a playlist, at the quality already highlighted.

        The quality is the one under the cursor, not a second question asked
        later: a playlist and a quality are two halves of one decision, and the
        checklist screen repeats the choice back so it is never a surprise.
        """
        info = self.app.state.info
        if info is None or info.playlist is None:
            return
        table = self.query_one("#choices", ChoiceList)
        option = table.highlighted_value
        if not isinstance(option, MediaOption):
            # The cursor is on the sign-in row: the playlist is still on offer,
            # but at the best quality rather than at nothing.
            option = info.options[0] if info.options else None
        if option is None:
            self.notify("There is nothing in this playlist to download.", severity="warning")
            return
        self.stop_looking()
        self.app.push_screen(PlaylistScreen(option=option))

    # -- updating the engine from the failure screen (F3) --------------------
    def action_update_engine(self) -> None:
        """The likely fix, without leaving the screen that reported the problem.

        The link is asked again the moment the new engine is in place: the
        point of updating is to make *this* link work, and asking the person to
        press Find qualities again afterwards would be a second step for
        something they already asked for.
        """
        button = self.query_one("#update-engine", Button)
        # Hidden means it is not offered — the key must respect the same rule as
        # the button, or `u` would update a current engine behind a screen that
        # decided not to suggest it.
        if button.disabled or button.has_class("hidden"):
            return
        button.disabled = True
        note = self.query_one("#options-note", Static)
        note.update(
            _plain(
                "Fetching the newest video engine (about 3 MB). The link is asked "
                "again as soon as it is in place."
            )
        )
        self.query_one("#options-status", Static).update("Updating the video engine…")
        self.app.start_engine_update(then_retry=True)

    def engine_updated(self, message: str, changed: bool, then_retry: bool) -> None:
        if not self.is_mounted:
            return
        self.query_one("#update-engine", Button).disabled = False
        self.app.notify(_plain(message), timeout=8)
        if then_retry and changed:
            # A new engine is a new answer, so the old failure is replaced by a
            # fresh look-up rather than left on screen next to a working link.
            self.query_one("#update-engine", Button).add_class("hidden")
            self._start_looking()
            return
        # Nothing newer: the button has answered its question, so it stops
        # being offered for the rest of the session.
        self.query_one("#update-engine", Button).add_class("hidden")
        self.query_one("#options-status", Static).update(_plain(message))
        self.query_one("#options-note", Static).update(
            _plain(
                "The engine is current, so the link was not asked again: an "
                "update would not change what the site said."
            )
        )

    def update_failed(self, error: ToolError) -> None:
        if not self.is_mounted:
            return
        self.query_one("#update-engine", Button).disabled = False
        self.query_one("#options-status", Static).update(_plain(error.message))
        self.query_one("#options-note", Static).update(
            _plain(error.hint or "Nothing was changed. The link was not asked again.")
        )

    def action_go_back(self) -> None:
        # Going back while the link is being looked up is also a way of saying
        # "not this one": the look-up is stopped rather than left to run for
        # another two minutes in the background.
        self.stop_looking()
        self.app.pop_screen()

    @work(thread=True, exclusive=True, group="ffmpeg")
    def setup_ffmpeg(self) -> None:
        engine = self.app.services.engine
        button = self.query_one("#get-ffmpeg", Button)
        note = self.query_one("#options-note", Static)
        self.app._post(button.set_class, True, "hidden")
        self.app._post(note.update, "Setting up ffmpeg — this is a one-time download…")
        try:
            tool = engine.ensure_ffmpeg(
                on_status=lambda message: self.app._post(note.update, _plain(message)),
                on_progress=lambda done, total: self.app._post(
                    note.update,
                    _plain(
                        "Downloading ffmpeg… "
                        + (f"{done * 100 // total}%" if total else human_size(done))
                    ),
                ),
            )
        except ToolError as exc:
            self.app._post(note.update, _plain(f"{exc.message} {exc.hint or ''}".strip()))
            self.app._post(button.remove_class, "hidden")
            return
        self.app.state.ffmpeg_available = True
        version = tool.version or "ok"
        self.app._post(note.update, _plain(f"ffmpeg is ready ({version}). You can download now."))
        self.app._post(self.refresh_note)


class PlaylistScreen(Screen):
    """Screen 2b: which videos of a playlist to take, at the chosen quality (F2).

    Every video starts ticked, because "I opened a playlist" is a request for
    the playlist; unticking is how a user says "all but these". The listing is
    read when the screen opens — titles only — so a fifty-video playlist opens
    in seconds and nothing is downloaded until the button is pressed.
    """

    BINDINGS = [
        Binding("escape", "go_back", "Back", show=True),
        Binding("space", "toggle", "Tick", show=True),
        Binding("a", "all", "All", show=False),
        Binding("n", "none", "None", show=False),
        Binding("d", "download", "Download", show=False),
    ]

    def __init__(self, option: MediaOption, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.option = option
        self.playlist: Optional[Playlist] = None
        self._checked: set = set()

    def compose(self) -> ComposeResult:
        with Vertical(id="options-panel"):
            yield Static("", id="options-heading")
            yield Static("Looking up the videos in this playlist…", id="options-status")
            yield ChoiceList(id="choices")
            with Horizontal(classes="buttons"):
                yield Button("Download selected", variant="primary", id="download")
                yield Button("All", id="all")
                yield Button("None", id="none")
                yield Button("Back", id="back")
            yield Static("", id="options-note")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#download", Button).disabled = True
        self.query_one("#all", Button).disabled = True
        self.query_one("#none", Button).disabled = True
        self.load_playlist()

    # -- loading ------------------------------------------------------------
    @work(thread=True, exclusive=True, group="playlist")
    def load_playlist(self) -> None:
        """List the playlist without extracting a single video (F2)."""
        try:
            playlist = self.app.services.fetch_playlist(
                self.app.services.engine,
                self.app.state.url,
                cookies=self.app.state.cookies_browser,
            )
        except ProbeCancelled:
            self.app._post(
                self.show_error,
                ToolError("Stopped looking up the playlist.", "Press Escape to go back."),
            )
            return
        except ToolError as exc:
            self.app._post(self.show_error, exc)
            return
        except Exception as exc:  # pragma: no cover - defensive
            self.app._post(self.show_error, ToolError(str(exc)))
            return
        self.app._post(self.show_playlist, playlist)

    def show_playlist(self, playlist: Playlist) -> None:
        if not self.is_mounted:
            return
        self.playlist = playlist
        self.app.state.playlist = playlist
        self._checked = set(range(len(playlist.entries)))
        self.query_one("#options-heading", Static).update(_plain(playlist.heading))
        self.query_one("#options-status", Static).update("")
        for button_id in ("all", "none"):
            self.query_one(f"#{button_id}", Button).disabled = False
        self.render_rows()

    def show_error(self, error: ToolError) -> None:
        if not self.is_mounted:
            return
        self.query_one("#options-heading", Static).update("That playlist could not be read")
        self.query_one("#options-status", Static).update(_plain(error.message))
        self.query_one("#options-note", Static).update(
            _plain(error.hint or "Press Escape to go back.")
        )
        self.query_one("#download", Button).disabled = True

    # -- the list -----------------------------------------------------------
    def render_rows(self) -> None:
        playlist = self.playlist
        if playlist is None:
            return
        table = self.query_one("#choices", ChoiceList)
        cursor = table.cursor_index or 0
        choices: List[Tuple[str, str, str, Any]] = []
        for index, entry in enumerate(playlist.entries):
            tick = "[x]" if index in self._checked else "[ ]"
            length = human_duration(entry.duration) if entry.duration else ""
            choices.append((f"{tick} {entry.title}", length, "", index))
        table.set_choices(choices)
        if choices:
            # Ticking the fifth video must leave the cursor on the fifth video.
            table.select_index(min(cursor, len(choices) - 1))
        self.refresh_note()

    def toggle(self, index: int) -> None:
        playlist = self.playlist
        if playlist is None or not (0 <= index < len(playlist.entries)):
            return
        if index in self._checked:
            self._checked.discard(index)
        else:
            self._checked.add(index)
        table = self.query_one("#choices", ChoiceList)
        tick = "[x]" if index in self._checked else "[ ]"
        # Only this row changes, so the cursor and the scroll position stay put.
        table.set_label(index, f"{tick} {playlist.entries[index].title}")
        self.refresh_note()

    def refresh_note(self) -> None:
        playlist = self.playlist
        if playlist is None:
            return
        total = len(playlist.entries)
        chosen = len(self._checked)
        lines = [f"{chosen} of {total} selected  {_ARP}  {self.option.label} each"]
        extras = self.app.state.extras
        if extras:
            # Chosen on the previous screen, so it is repeated here: a queue
            # of nine downloads should not surprise anyone (F5).
            lines.append(f"Also adding: {extras.summary(self.option)}.")
        network = self.app.state.network
        if network:
            # Likewise for the network terms the run was launched with (F11).
            lines.append(f"Network: {network.summary()}.")
        if playlist.truncated:
            lines.append(
                f"Viddl lists the first {total} of {playlist.count} videos in this "
                "playlist. Split it into shorter playlists to take the rest."
            )
        if chosen == 0:
            lines.append("Tick at least one video to download.")
        else:
            lines.append("Click a video, or press Space, to tick it on or off.")
        self.query_one("#options-note", Static).update(_plain("\n".join(lines)))
        self.query_one("#download", Button).disabled = chosen == 0

    # -- actions ------------------------------------------------------------
    def action_toggle(self) -> None:
        table = self.query_one("#choices", ChoiceList)
        if table.cursor_index is not None:
            self.toggle(table.cursor_index)

    def on_choice_list_selected(self, message: ChoiceList.Selected) -> None:
        message.stop()
        self.toggle(message.index)

    def action_all(self) -> None:
        self._set_all(True)

    def action_none(self) -> None:
        self._set_all(False)

    def _set_all(self, checked: bool) -> None:
        playlist = self.playlist
        if playlist is None:
            return
        self._checked = set(range(len(playlist.entries))) if checked else set()
        self.render_rows()

    def action_download(self) -> None:
        playlist = self.playlist
        if playlist is None:
            return
        if not self._checked:
            self.notify("Tick at least one video first.", severity="warning")
            return
        self.app.state.option = self.option
        self.app.start_queue([playlist.entries[index] for index in sorted(self._checked)])

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "download":
            self.action_download()
        elif button_id == "all":
            self.action_all()
        elif button_id == "none":
            self.action_none()
        elif button_id == "back":
            self.action_go_back()


class DownloadScreen(Screen):
    """Screen 3: progress."""

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=True)]

    def compose(self) -> ComposeResult:
        with Vertical(id="download-panel"):
            yield Static("", id="download-heading")
            yield Static("Getting ready…", id="download-status")
            yield ProgressBar(total=100, show_eta=False, id="download-bar")
            yield Static("", id="download-detail")
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
        yield Footer()

    def on_mount(self) -> None:
        option = self.app.state.option
        info = self.app.state.info
        entries = self.app.state.playlist_entries
        heading = self.app.state.url
        if option is not None:
            heading = f"{option.label}  ·  {option.size_text}"
        line = _plain(heading)
        if entries:
            # A queue is not one video, so the heading says how many; the status
            # line says which one is running.
            count = f"{len(entries)} videos  {_ARP}  "
            heading = count + heading
            line = _plain(heading)
            playlist = self.app.state.playlist
            if playlist is not None and playlist.title:
                line = f"{_plain(shorten(playlist.title, 60))}\n[dim]{line}[/dim]"
        elif info is not None and info.title:
            line = f"{_plain(shorten(info.title, 60))}\n[dim]{line}[/dim]"
        self.query_one("#download-heading", Static).update(line)
        if info is not None and info.is_live:
            # "Cancel" describes a download that was going to end. A live
            # recording is ended by the person, and what it caught is kept
            # (F12) — the button says which of the two it is.
            self.query_one("#cancel", Button).label = "Stop recording"
        self.run_download()

    @work(thread=True, exclusive=True, group="download")
    def run_download(self) -> None:
        app = self.app
        option = app.state.option
        if option is None:  # pragma: no cover - defensive
            return

        try:
            self.prepare_ffmpeg(option)
        except ToolError as exc:
            app._post(self.finish_error, exc)
            return
        except Exception as exc:  # pragma: no cover - defensive
            app._post(self.finish_error, ToolError(str(exc)))
            return

        if app.state.playlist_entries:
            self.run_queue(option)
            return

        live = bool(app.state.info is not None and app.state.info.is_live)
        try:
            out_dir = app.services.downloads_dir()
            job = app.services.make_job(
                app.services.engine,
                app.state.url,
                option,
                out_dir,
                on_progress=lambda progress: app._post(self.on_progress, progress),
                cookies=app.state.cookies_browser,
                extras=app.state.extras,
                # The archive this run asked for, and the video's own title
                # for the recent list (F6): the screen has both, and a job
                # left to guess the title would have only the file name.
                archive=app.state.archive,
                network=app.state.network,
                title=app.state.info.title if app.state.info else "",
                # A live stream is saved when it is stopped, and can be asked
                # for from its beginning (F12). Both only mean something when
                # the link actually is live, so a link that is not never gets
                # the flag.
                is_live=live,
                live_from_start=live and app.state.live_from_start,
            )
            app.state.job = job
            path = job.run()
        except DownloadCancelled:
            app._post(self.finish_cancelled)
            return
        except ToolError as exc:
            app._post(self.finish_error, exc)
            return
        except Exception as exc:  # pragma: no cover - defensive
            app._post(self.finish_error, ToolError(str(exc)))
            return
        app._post(self.finish_ok, path)

    def prepare_ffmpeg(self, option: MediaOption) -> None:
        """Fetch ffmpeg before a download that needs it, once for the whole run."""
        app = self.app
        # The extras are written by ffmpeg too, so a download that only needs
        # it for the cover image still gets the same one-time setup (F5).
        if app.state.ffmpeg_available:
            return
        if not option.needs_ffmpeg and not app.state.extras.needs_ffmpeg_for(option):
            return
        tool = app.services.engine.find_ffmpeg()
        if tool is None:
            app._post(self.set_status, "Setting up ffmpeg (one time)…")
            app.services.engine.ensure_ffmpeg(
                on_status=lambda message: app._post(self.set_status, message),
                on_progress=lambda done, total: app._post(
                    self.set_ffmpeg_progress, done, total
                ),
            )
        app.state.ffmpeg_available = True

    # -- a playlist, one video at a time ------------------------------------
    def run_queue(self, option: MediaOption) -> None:
        """Download the ticked videos in order, reporting each as it starts."""
        app = self.app
        entries = list(app.state.playlist_entries)
        playlist = app.state.playlist
        if playlist is None:  # pragma: no cover - defensive
            playlist = Playlist(title="", url=app.state.url)
        queue = QueueJob(
            app.services.engine,
            playlist,
            entries,
            option,
            app.services.downloads_dir(),
            cookies=app.state.cookies_browser,
            extras=app.state.extras,
            archive=app.state.archive,
            network=app.state.network,
            make_job=app.services.make_job,
            on_start=lambda item: app._post(self.on_item_start, item),
            on_progress=lambda item, progress: app._post(
                self.on_item_progress, item, progress
            ),
            on_end=lambda item: app._post(self.on_item_end, item),
        )
        app.state.queue = queue
        try:
            summary = queue.run()
        except ToolError as exc:
            app.state.queue = None
            app._post(self.finish_error, exc)
            return
        except Exception as exc:  # pragma: no cover - defensive
            app.state.queue = None
            app._post(self.finish_error, ToolError(str(exc)))
            return
        app.state.queue = None
        app._post(self.finish_queue, summary)

    def _overall(self, item: QueueItem, percent: float) -> float:
        """One bar for the whole playlist, so nine videos do not look like nine."""
        done = item.position - 1
        total = max(1, item.total)
        return min(100.0, (done + (percent or 0.0) / 100.0) / total * 100.0)

    def on_item_start(self, item: QueueItem) -> None:
        self.query_one("#download-bar", ProgressBar).update(progress=self._overall(item, 0.0))
        self.set_status(
            f"Video {item.position} of {item.total}  {_ARP}  {shorten(item.title, 48)}"
        )
        self.query_one("#download-detail", Static).update("")

    def on_item_progress(self, item: QueueItem, progress: Progress) -> None:
        self.set_status(f"Video {item.position} of {item.total}  {_ARP}  {progress.status}")
        if progress.percent is not None and progress.phase == "download":
            self.query_one("#download-bar", ProgressBar).update(
                progress=self._overall(item, progress.percent)
            )
        self._show_detail(progress)

    def on_item_end(self, item: QueueItem) -> None:
        if item.path is not None:
            # "Already had" for a file the download archive recognised (F6):
            # the file is real either way, and only the word differs.
            word = "Already had" if item.already_had else "Saved"
            self.query_one("#download-detail", Static).update(
                _plain(f"{word} {Path(item.path).name}")
            )

    def finish_queue(self, summary: QueueSummary) -> None:
        self.app.state.queue = None
        if not self._has_a_screen():
            return
        self.query_one("#download-bar", ProgressBar).update(progress=100.0)
        self.app.push_screen(
            QueueResultScreen(
                summary=summary,
                option=self.app.state.option,
                # A queue with no playlist behind it came from the links
                # screen, and says so (F7).
                batch=self.app.state.playlist is None,
            )
        )

    # -- updates ------------------------------------------------------------
    def set_status(self, message: str) -> None:
        self.query_one("#download-status", Static).update(_plain(message))

    def set_ffmpeg_progress(self, done: int, total: Optional[int]) -> None:
        bar = self.query_one("#download-bar", ProgressBar)
        if total:
            bar.update(progress=done * 100 / total)
            self.query_one("#download-detail", Static).update(
                f"{human_size(done)} of {human_size(total)}"
            )
        else:
            self.query_one("#download-detail", Static).update(_plain(human_size(done)))

    def on_progress(self, progress: Progress) -> None:
        bar = self.query_one("#download-bar", ProgressBar)
        self.set_status(progress.status)
        if progress.percent is not None and progress.phase == "download":
            bar.update(progress=min(100.0, progress.percent))
        if progress.phase == "done":
            bar.update(progress=100.0)
        self._show_detail(progress)

    def _show_detail(self, progress: Progress) -> None:
        detail_bits = []
        if progress.downloaded:
            if progress.total:
                detail_bits.append(f"{human_size(progress.downloaded)} of {human_size(progress.total)}")
            else:
                detail_bits.append(human_size(progress.downloaded))
        if progress.speed:
            detail_bits.append(progress.speed)
        if progress.eta:
            detail_bits.append(f"{progress.eta} left")
        self.query_one("#download-detail", Static).update("  ·  ".join(detail_bits))

    # -- endings ------------------------------------------------------------
    def _has_a_screen(self) -> bool:
        """False once the app is on its way out — there is nothing to show on.

        Stopping the process tree closes its output at once, so the ending can
        arrive while the app is shutting down (the user pressed quit, which is
        what closed it). Bringing up a screen then would raise, and the result
        nobody is waiting for is not worth an error.
        """
        return self.app.is_running and bool(self.app.screen_stack)

    def finish_ok(self, path: Path) -> None:
        self.app.state.saved_path = path
        job = self.app.state.job
        attachments = list(getattr(job, "attachments", []))
        already_had = bool(getattr(job, "already_had", False))
        self.app.state.job = None
        if not self._has_a_screen():
            return
        self.app.push_screen(
            ResultScreen(saved_path=path, attachments=attachments, already_had=already_had)
        )

    def finish_error(self, error: ToolError) -> None:
        self.app.state.job = None
        if not self._has_a_screen():
            return
        self.app.push_screen(ResultScreen(error=error))

    def finish_cancelled(self) -> None:
        # The partial file is kept, so the honest thing is to ask what to do
        # with it rather than throw away work the user waited for.
        job = self.app.state.job
        self.app.state.job = None
        if not self._has_a_screen():
            return
        recorded = getattr(job, "recorded", None)
        if recorded is not None:
            # A live stream has no end, so stopping one delivered what had
            # been recorded as a real file (F12): that is an ending to show,
            # not a question about a half-file.
            self.app.state.saved_path = recorded
            self.app.push_screen(ResultScreen(saved_path=recorded, recorded=True))
            return
        self.app.switch_screen(
            StoppedScreen(
                job=job,
                option=self.app.state.option,
                is_live=bool(getattr(job, "is_live", False)),
            )
        )

    def action_cancel(self) -> None:
        queue = self.app.state.queue
        if queue is not None:
            # The current video stops, and the rest are not started: cancelling
            # a queue means the queue, not just whatever it is on right now.
            queue.cancel()
            self.set_status("Stopping…")
            return
        job = self.app.state.job
        if job is not None:
            job.cancel()
            self.set_status("Stopping…")
        else:
            self.app.pop_screen()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.action_cancel()


class StoppedScreen(Screen):
    """Shown when a download is stopped: carry on, or throw the part away.

    Cancelling keeps what has already been downloaded, which is the whole point
    of resuming — so the choice belongs to the user. **Resume** runs the same
    download again and yt-dlp continues from the ``.part`` file; **Discard**
    deletes that file; leaving with escape keeps it.
    """

    BINDINGS = [
        Binding("escape", "later", "Later", show=True),
        Binding("r", "resume", "Resume", show=False),
        Binding("d", "discard", "Discard", show=False),
    ]

    def __init__(
        self,
        job: Optional[DownloadJob] = None,
        option: Optional[MediaOption] = None,
        is_live: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.job = job
        self.option = option
        # True for a live stream whose recording could not be put in one file
        # (F12): there is nothing to carry on from, and saying "Resume" would
        # promise a continuation that does not exist.
        self.is_live = is_live

    def compose(self) -> ComposeResult:
        with Vertical(id="stopped-panel"):
            yield Static("Stopped", id="brand")
            yield Static("", id="stopped-message")
            with Horizontal(classes="buttons"):
                if self.option is not None and not self.is_live:
                    yield Button("Resume", variant="primary", id="resume")
                if self.kept_files():
                    yield Button("Discard", id="discard")
                yield Button("Start over", id="again")
        yield Footer()

    def kept_files(self) -> list:
        """The half-finished files on disk, if the job knows of any."""
        if self.job is None:
            return []
        finder = getattr(self.job, "partial_paths", None)
        return list(finder()) if callable(finder) else []

    def on_mount(self) -> None:
        lines = []
        if self.option is not None:
            lines.append(_plain(f"{self.option.label}  {_ARP}  {self.option.size_text}"))
        if self.is_live:
            # A live stream has no second half to carry on with: whatever is
            # on disk is what the stop caught, and it stays there (F12).
            lines.append(
                "This was a live stream, so there is nothing to carry on from: "
                "the recording stopped here. What had been recorded could not be "
                "put into one file, and is kept in the folder below."
            )
            folder = getattr(self.job, "work_dir", None)
            if callable(folder):
                lines.append(_plain(str(folder())))
        elif self.kept_files():
            lines.append(
                "What has been downloaded so far is kept, so Resume carries on "
                "from there. Discard throws it away."
            )
        else:
            lines.append("Nothing had been written to disk yet.")
        self.query_one("#stopped-message", Static).update("\n".join(lines))

    # -- actions ------------------------------------------------------------
    def action_resume(self) -> None:
        if self.is_live or self.app.state.option is None:
            # Live: a second run records from the live point, which is a new
            # file rather than a continuation, so the button is not offered
            # and the key does nothing (F12).
            if self.is_live:
                return
            self.action_later()  # pragma: no cover - defensive
            return
        # A fresh download screen runs the same option again. The partial is
        # in the folder that download always uses (R6), so yt-dlp picks the
        # ".part" file up from where the last attempt stopped.
        self.app.switch_screen(DownloadScreen())

    def action_discard(self) -> None:
        if self.job is not None:
            self.job.discard_partial()
        self.app.go_home()

    def action_later(self) -> None:
        self.app.go_home()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "resume":
            self.action_resume()
        elif event.button.id == "discard":
            self.action_discard()
        elif event.button.id == "again":
            self.action_later()


class ResultScreen(Screen):
    """Screen 4: where the file landed, or what went wrong.

    One ending is not a finished download: a live stream that was stopped has
    delivered what it recorded (F12). The file is real and complete up to the
    moment of the stop, so it is shown like any other saved file, with the
    heading saying which kind of ending this was.
    """

    BINDINGS = [
        Binding("escape", "go_home", "Start over", show=True),
        Binding("o", "open_folder", "Open folder", show=False),
        Binding("c", "copy_path", "Copy path", show=False),
    ]

    def __init__(
        self,
        saved_path: Optional[Path] = None,
        error: Optional[ToolError] = None,
        attachments: Optional[List[Path]] = None,
        already_had: bool = False,
        recorded: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.saved_path = saved_path
        self.error = error
        # True when the file is a live recording the user stopped (F12): it is
        # everything up to that moment, and "Saved" alone would not say so.
        self.recorded = recorded
        # Pictures saved beside the file by the extras (F5).
        self.attachments = list(attachments or [])
        # True when the download archive recognised the video and handed back
        # the file from an earlier download (F6). The file is real and where
        # it always was; the word "fetched" would not be.
        self.already_had = already_had

    def compose(self) -> ComposeResult:
        with Vertical(id="result-panel"):
            if self.error is not None:
                yield Static("Download failed", id="brand")
                yield Static("", id="result-path")
                yield Static("", id="result-message")
                with Horizontal(classes="buttons"):
                    yield Button("Try another link", variant="primary", id="again")
                    yield Button("Quit", id="quit")
            else:
                yield Static("Recording stopped" if self.recorded else "Saved", id="brand")
                yield Static("", classes="sub", id="result-sub")
                yield Static("", id="result-path")
                with Horizontal(classes="buttons"):
                    yield Button("Copy path", variant="primary", id="copy")
                    yield Button("Open folder", id="open")
                    yield Button("Download another", id="again")
                    yield Button("Quit", id="quit")
        yield Footer()

    def on_mount(self) -> None:
        if self.error is not None:
            self.query_one("#result-path", Static).update(_plain(self.error.message))
            hint = self.error.hint or "Press Escape to try another link."
            self.query_one("#result-message", Static).update(_plain(hint))
            return
        path = self.saved_path
        assert path is not None
        option = self.app.state.option
        label = option.label if option is not None else "File"
        size = human_size(path.stat().st_size) if path.exists() else "?"
        # "Already had" instead of the quality, when the archive recognised
        # the video (F6): the row would otherwise claim a quality for a file
        # that was fetched at some other one, and this screen does not lie.
        sub = f"{'Already had' if self.already_had else label}  ·  {size}"
        if self.recorded:
            # A live stream has no natural end, so the file is what had been
            # recorded when Stop was pressed — said out loud rather than left
            # to look like a video that finished.
            sub += "  ·  what had been recorded when you stopped"
        if self.attachments:
            # The image is beside the file, so it is named rather than left to
            # be discovered in the folder later (F5).
            names = ", ".join(attachment.name for attachment in self.attachments)
            sub += f"  ·  with {names}"
        self.query_one("#result-sub", Static).update(_plain(sub))
        self.query_one("#result-path", Static).update(_plain(path))

    # -- actions ------------------------------------------------------------
    def action_go_home(self) -> None:
        self.app.go_home()

    def action_open_folder(self) -> None:
        if self.saved_path is None:
            return
        problem = _open_folder(self.saved_path)
        if problem:
            self.notify(problem, severity="warning")

    def action_copy_path(self) -> None:
        if self.saved_path is None:
            return
        # Copying can be asynchronous, so it has to run in a worker: calling
        # the coroutine directly would silently do nothing at all.
        self.run_worker(self._copy(str(self.saved_path)))

    async def _copy(self, text: str) -> None:
        try:
            result = self.app.copy_to_clipboard(text)
            if inspect.isawaitable(result):
                await result
            self.notify("Full path copied to the clipboard.", timeout=3)
        except Exception as exc:  # pragma: no cover - terminal dependent
            self.notify(f"Could not copy automatically ({exc}). The path is shown above.",
                        severity="warning")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "copy":
            self.action_copy_path()
        elif button_id == "open":
            self.action_open_folder()
        elif button_id in ("again", "quit"):
            if button_id == "again":
                self.app.go_home()
            else:
                self.app.action_quit_app()


class RecentScreen(Screen):
    """What Viddl has saved lately (F6): open it, copy the path, take it again.

    The list comes from a small file in the app's own folder, so it survives
    restarts and needs nothing running to keep it up. A row whose file has
    since been deleted still appears — with that said out loud — because
    hiding it would be a second surprise on top of the missing file.

    **Download again** is the one action that leaves this screen, and it puts
    the link back in the box and opens the qualities, exactly as a pasted
    link would. Nothing is downloaded by pressing it: the promise the app
    makes to any link arriving from outside (S5) is kept here too.
    """

    BINDINGS = [
        Binding("escape", "go_back", "Back", show=True),
        Binding("enter", "redownload", "Download again", show=False),
        Binding("o", "open_folder", "Open folder", show=False),
        Binding("c", "copy_path", "Copy path", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="recent-panel"):
            yield Static("Recent downloads", id="brand")
            yield Static("", classes="sub", id="recent-sub")
            yield ChoiceList(id="choices")
            with Horizontal(classes="buttons"):
                yield Button("Download again", variant="primary", id="redownload")
                yield Button("Open folder", id="open")
                yield Button("Copy path", id="copy")
                yield Button("Back", id="back")
            yield Static("", id="recent-note")
        yield Footer()

    def on_mount(self) -> None:
        self.entries = list(self.app.services.recent())
        table = self.query_one("#choices", ChoiceList)
        rows: List[Tuple[str, str, str, Any]] = []
        for entry in self.entries:
            rows.append((entry.title or entry.path.name, entry.size_text, entry.row_note, entry))
        table.set_choices(rows)
        empty = not rows
        for button_id in ("redownload", "open", "copy"):
            self.query_one(f"#{button_id}", Button).disabled = empty
        if empty:
            self.query_one("#recent-sub", Static).update("Nothing saved yet.")
            self.query_one("#recent-note", Static).update(
                _plain(
                    "Files you download with Viddl are listed here, newest first, "
                    "with a way to open the folder, copy the path, or take one "
                    "again. Download something and it will appear."
                )
            )
            return
        count = len(rows)
        self.query_one("#recent-sub", Static).update(
            f"{count} saved download{'s' if count != 1 else ''}  ·  newest first"
        )
        self._show_row(self.entries[0])
        table.focus()

    def _show_row(self, entry: history_mod.Entry) -> None:
        """Say where the highlighted file is — the question this screen answers."""
        lines = [str(entry.path)]
        if entry.missing:
            lines.append("That file is no longer on disk — only this row is left.")
        lines.append("Enter takes it again  ·  o opens its folder  ·  c copies the path")
        self.query_one("#recent-note", Static).update(_plain("\n".join(lines)))

    def _entry(self) -> Optional[history_mod.Entry]:
        value = self.query_one("#choices", ChoiceList).highlighted_value
        return value if isinstance(value, history_mod.Entry) else None

    # -- interaction --------------------------------------------------------
    def on_choice_list_highlighted(self, message: ChoiceList.Highlighted) -> None:
        if isinstance(message.value, history_mod.Entry):
            self._show_row(message.value)

    def on_choice_list_selected(self, message: ChoiceList.Selected) -> None:
        """Clicking a row is the same as the button under it, not a download.

        The list takes Enter and a click while it has the focus, which is the
        road most people will take; the button is for the mouse that is
        already down there. Either way this is one action, and it is only
        ever *asking* for the qualities (S5).
        """
        message.stop()
        self.action_redownload()

    # -- actions ------------------------------------------------------------
    def action_go_back(self) -> None:
        self.app.pop_screen()

    def action_redownload(self) -> None:
        """Put the link back in the box and ask for its qualities (F6).

        The same road a pasted link takes: the qualities screen probes the
        link and offers the rows, and nothing is downloaded until one is
        chosen. A row whose link was never recorded — only reachable through
        a hand-edited list — says so instead of opening an empty box.
        """
        entry = self._entry()
        if entry is None:
            self.notify("Nothing to download again.", severity="warning")
            return
        link = safe_video_link(entry.url)
        if link is None:
            self.notify(
                "That entry does not remember its link, so it cannot be fetched again.",
                severity="warning",
                timeout=6,
            )
            return
        state = self.app.state
        state.url = link
        state.info = None
        state.option = None
        state.saved_path = None
        state.playlist = None
        state.playlist_entries = []
        # One file, taken again: a batch left over from the links screen must
        # not turn this into several downloads (F7).
        state.links = []
        self.app.push_screen(OptionsScreen())

    def action_open_folder(self) -> None:
        entry = self._entry()
        if entry is None:
            return
        problem = _open_folder(entry.path)
        if problem:
            self.notify(problem, severity="warning")

    def action_copy_path(self) -> None:
        entry = self._entry()
        if entry is None:
            return
        # Copying can be asynchronous, so it has to run in a worker: calling
        # the coroutine directly would silently do nothing at all.
        self.run_worker(self._copy(str(entry.path)))

    async def _copy(self, text: str) -> None:
        try:
            result = self.app.copy_to_clipboard(text)
            if inspect.isawaitable(result):
                await result
            self.notify("Full path copied to the clipboard.", timeout=3)
        except Exception as exc:  # pragma: no cover - terminal dependent
            self.notify(
                f"Could not copy automatically ({exc}). The path is shown above.",
                severity="warning",
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "redownload":
            self.action_redownload()
        elif button_id == "open":
            self.action_open_folder()
        elif button_id == "copy":
            self.action_copy_path()
        elif button_id == "back":
            self.action_go_back()


class QueueResultScreen(Screen):
    """Screen 4b: what a whole playlist did, video by video (F2).

    A single "Saved" would hide the two things worth knowing after nine
    downloads: which ones worked, and which did not. Failures are named rather
    than counted, because a title is what a person needs to try again.
    """

    BINDINGS = [
        Binding("escape", "go_home", "Start over", show=True),
        Binding("o", "open_folder", "Open folder", show=False),
    ]

    #: How many saved names are listed before the rest are summed up. The files
    #: are in the folder either way; a screen is not a directory listing.
    _LISTED = 8

    def __init__(
        self,
        summary: QueueSummary,
        option: Optional[MediaOption] = None,
        batch: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.summary = summary
        self.option = option
        # True when the queue came from the links screen rather than a playlist
        # (F7): the two are the same run, and the screen should not call a batch
        # of links a playlist.
        self.batch = batch

    @property
    def saved(self) -> List[Path]:
        return [item.path for item in self.summary.items if item.path is not None]

    def compose(self) -> ComposeResult:
        with Vertical(id="result-panel"):
            yield Static("", id="brand")
            yield Static("", classes="sub", id="result-sub")
            yield Static("", id="result-path")
            yield Static("", id="result-message")
            with Horizontal(classes="buttons"):
                if self.summary.stopped and self.summary.done < self.summary.total:
                    yield Button("Download the rest", variant="primary", id="resume")
                if self.saved:
                    yield Button("Open folder", id="open")
                    yield Button("Copy paths", id="copy")
                yield Button("Done", id="again")
        yield Footer()

    def on_mount(self) -> None:
        summary = self.summary
        if summary.failed:
            title = "Links finished with problems" if self.batch else "Playlist finished with problems"
        elif summary.stopped:
            title = "Links stopped" if self.batch else "Playlist stopped"
        elif self.batch:
            title = "All links saved"
        else:
            title = "Playlist saved"
        self.query_one("#brand", Static).update(title)
        self.query_one("#result-sub", Static).update(_plain(summary.headline))

        saved = self.saved
        names = [path.name for path in saved[: self._LISTED]]
        if len(saved) > self._LISTED:
            names.append(f"and {len(saved) - self._LISTED} more")
        self.query_one("#result-path", Static).update(_plain("\n".join(names)))

        lines: List[str] = []
        failures = [item for item in summary.items if item.error is not None]
        for item in failures[:5]:
            error = item.error
            lines.append(f"{item.position}. {item.title}: {error.message if error else ''}")
        if len(failures) > 5:
            lines.append(f"...and {len(failures) - 5} more that did not work.")
        if summary.stopped and summary.done < summary.total:
            lines.append(
                "The links that were not started are still on the list."
                if self.batch
                else "The videos that were not started are still unticked in the list."
            )
        self.query_one("#result-message", Static).update(_plain("\n".join(lines)))

    # -- actions ------------------------------------------------------------
    def action_go_home(self) -> None:
        self.app.go_home()

    def action_open_folder(self) -> None:
        target = self.saved[0] if self.saved else self.app.services.downloads_dir()
        problem = _open_folder(target)
        if problem:
            self.notify(problem, severity="warning")

    def action_copy_paths(self) -> None:
        paths = "\n".join(str(path) for path in self.saved)
        if not paths:
            return
        self.run_worker(self._copy(paths))

    async def _copy(self, text: str) -> None:
        try:
            result = self.app.copy_to_clipboard(text)
            if inspect.isawaitable(result):
                await result
            self.notify("The saved paths are on the clipboard.", timeout=3)
        except Exception as exc:  # pragma: no cover - terminal dependent
            self.notify(
                f"Could not copy automatically ({exc}). The paths are shown above.",
                severity="warning",
            )

    def action_resume(self) -> None:
        if not self.app.state.playlist_entries:  # pragma: no cover - defensive
            self.action_go_home()
            return
        # The queue runs again from the top. The videos already saved are found
        # again and reported as already downloaded, so this costs a look-up per
        # finished file rather than a download per finished file.
        self.app.switch_screen(DownloadScreen())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "open":
            self.action_open_folder()
        elif button_id == "copy":
            self.action_copy_paths()
        elif button_id == "resume":
            self.action_resume()
        elif button_id == "again":
            self.app.go_home()


def run(
    url: str = "",
    audio: bool = False,
    prefer_quality: str = "",
    cookies: str = "",
    extras: Optional[Extras] = None,
    archive: Optional[Path] = None,
    network: Optional[NetworkOptions] = None,
    live_from_start: bool = False,
    services: Optional[Services] = None,
) -> None:
    """Start the terminal app."""
    app = ViddlApp(
        initial_url=url,
        prefer_audio=audio,
        prefer_quality=prefer_quality,
        cookies=cookies,
        extras=extras,
        archive=archive,
        network=network,
        live_from_start=live_from_start,
        services=services,
    )
    app.run()
