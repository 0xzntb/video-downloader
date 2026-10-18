"""The Viddl terminal app.

Four screens, in the order you meet them:

1. **Home** - paste a link (or arrive with one from the browser extension)
2. **Qualities** - pick a video quality or an MP3, with estimated sizes
3. **Download** - progress, speed and ETA, cancellable at any time
4. **Done** - the full path of the saved file

Everything is driveable with the mouse (click a quality, click a button) and
with the keyboard (arrows, Enter, Tab, Escape).
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from pathlib import Path

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
from .. import engine as engine_mod
from ..download import DownloadCancelled, DownloadJob, Progress, probe_video
from ..engine import Engine, ToolError
from ..formats import MediaInfo, MediaOption
from ..util import human_size, safe_video_link, shorten
from .widgets import ChoiceList

_ARP = "·"
LOGGER = logging.getLogger("viddl")


@dataclass
class Services:
    """Everything the UI needs from the outside world, swappable for tests."""

    engine: Engine
    probe: Callable[[Engine, str], MediaInfo] = probe_video
    make_job: Callable[..., DownloadJob] = DownloadJob
    downloads_dir: Callable[[], Path] = appdata.downloads_dir


@dataclass
class State:
    """What the user has done so far."""

    url: str = ""
    prefer_audio: bool = False
    info: Optional[MediaInfo] = None
    option: Optional[MediaOption] = None
    job: Optional[DownloadJob] = None
    saved_path: Optional[Path] = None
    engine_note: str = ""
    engine_error: Optional[str] = None
    # Set once, at startup, from what appdata could not create (R7). It is a
    # warning rather than a stop: downloads can still work with an engine that
    # was already installed, and the folder may only be un-writable for now.
    folder_problem: str = ""
    ffmpeg_available: bool = False
    # How big the one-time ffmpeg download would be, once the mirror has been
    # asked (R8). ``None`` means "not known" — never "zero".
    ffmpeg_size: Optional[int] = None
    ffmpeg_size_asked: bool = False


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


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

    #home-panel, #options-panel, #download-panel, #stopped-panel, #result-panel {
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
        services: Optional[Services] = None,
    ) -> None:
        super().__init__()
        self.services = services or Services(engine=Engine())
        # A link arriving from the command line or a browser hand-off is still
        # a link from outside: it is checked here as well as at the CLI.
        self.state = State(url=safe_video_link(initial_url) or "", prefer_audio=prefer_audio)
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
        if age is not None and age >= engine_mod.STALE_DAYS:
            # The likeliest cause of "this link stopped working" is a yt-dlp
            # older than the site, so it is said here rather than only in the
            # doctor — the person clicking a link is not running --doctor.
            state.engine_note = f"Engine {age} days old — run `viddl --update`"
        else:
            state.engine_note = f"Ready to download  {_ARP}  engine {tool.version or 'ok'}"
        ffmpeg = engine.find_ffmpeg()
        state.ffmpeg_available = ffmpeg is not None
        if ffmpeg is not None:
            state.engine_note += f"  {_ARP}  ffmpeg ready"
        else:
            state.engine_note += f"  {_ARP}  ffmpeg comes later, if you need MP3 or 1080p+"
        self._post(self._refresh_engine_status)

    def _refresh_engine_status(self) -> None:
        for screen in self.screen_stack:
            if isinstance(screen, HomeScreen):
                screen.refresh_engine_status()
        for screen in self.screen_stack:
            if isinstance(screen, OptionsScreen):
                screen.refresh_note()

    # -- navigation ---------------------------------------------------------
    def go_home(self) -> None:
        """Return to a fresh home screen, ready for the next link."""
        while len(self.screen_stack) > 2:
            self.pop_screen()
        self.state.url = ""
        self.state.info = None
        self.state.option = None
        self.state.saved_path = None
        for screen in self.screen_stack:
            if isinstance(screen, HomeScreen):
                screen.reset()

    def action_quit_app(self) -> None:
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
        self.state.option = option
        self.push_screen(DownloadScreen())


# ---------------------------------------------------------------------------
# screens
# ---------------------------------------------------------------------------


class HomeScreen(Screen):
    """Screen 1: paste a link."""

    BINDINGS = [
        Binding("escape", "quit_app", "Quit", show=True),
        Binding("tab", "focus_next", "Next", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="home-panel"):
            yield Static(f"{APP_TITLE}", id="brand")
            yield Static("Download a video or a song by pasting a link.", classes="tagline")
            yield Static("Paste a video link", classes="field-label")
            yield Input(
                placeholder="https://www.youtube.com/watch?v=…",
                id="url-input",
            )
            with Horizontal(classes="buttons"):
                yield Button("Find qualities", variant="primary", id="go")
                yield Button("Quit", id="quit")
            yield Static("", id="engine-status")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_engine_status()
        if self.app.state.url:
            self.query_one("#url-input", Input).value = self.app.state.url
            self.call_after_refresh(self.submit_url)
        else:
            self.query_one("#url-input", Input).focus()

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

        lines = [state.folder_problem] if state.folder_problem else []
        if state.engine_note:
            lines.append(shorten(state.engine_note, 96))
        widget.update(_plain("\n".join(lines)))

    def submit_url(self) -> None:
        field = self.query_one("#url-input", Input)
        url = field.value.strip()
        if not url:
            self.notify("Paste a link first.", severity="warning")
            field.focus()
            return
        if url.lower().startswith(("viddl://", "viddl:")):
            url = _url_from_scheme(url)
        # The last gate before a link reaches the downloader: only a plain
        # http(s) link gets through, so nothing option-shaped can ever be
        # handed over as if it were a flag.
        link = safe_video_link(url)
        if link is None:
            self.notify(
                "That looks like text, not a link. Copy the address from your browser."
                if " " in url
                else "That does not look like a video link. Copy the address from your browser.",
                severity="warning",
                timeout=6,
            )
            field.focus()
            return
        self.app.state.url = link
        self.app.push_screen(OptionsScreen())

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.submit_url()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "go":
            self.submit_url()
        elif event.button.id == "quit":
            self.app.action_quit_app()

    def action_quit_app(self) -> None:
        self.app.action_quit_app()


def _url_from_scheme(raw: str) -> str:
    """Pull the target link out of a ``viddl://`` hand-off."""
    from ..protocol import parse_scheme_url

    return parse_scheme_url(raw) or ""


class OptionsScreen(Screen):
    """Screen 2: choose a quality."""

    BINDINGS = [
        Binding("escape", "go_back", "Back", show=True),
        Binding("b", "go_back", "Back", show=False),
        Binding("enter", "download", "Download", show=False),
        Binding("d", "download", "Download", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="options-panel"):
            yield Static("", id="options-heading")
            yield Static("Looking up the link…", id="options-status")
            yield ChoiceList(id="choices")
            with Horizontal(classes="buttons"):
                yield Button("Download", variant="primary", id="download")
                yield Button("Set up ffmpeg", id="get-ffmpeg")
                yield Button("Back", id="back")
            yield Static("", id="options-note")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#get-ffmpeg", Button).add_class("hidden")
        self.query_one("#download", Button).disabled = True
        self.load_info(self.app.state.url)

    # -- loading ------------------------------------------------------------
    @work(thread=True, exclusive=True, group="probe")
    def load_info(self, url: str) -> None:
        try:
            info = self.app.services.probe(self.app.services.engine, url)
        except ToolError as exc:
            self.app._post(self.show_error, exc)
            return
        except Exception as exc:  # pragma: no cover - defensive
            self.app._post(self.show_error, ToolError(str(exc)))
            return
        self.app._post(self.show_info, info)

    def show_error(self, error: ToolError) -> None:
        status = self.query_one("#options-status", Static)
        status.update(_plain(error.message))
        self.app.state.info = None
        self.query_one("#options-heading", Static).update("That link did not work")
        note = self.query_one("#options-note", Static)
        note.update(_plain(error.hint or "Press Escape to try another link."))
        self.query_one("#download", Button).disabled = True
        self.notify(_plain(error.message), severity="error", timeout=10)

    def show_info(self, info: MediaInfo) -> None:
        self.app.state.info = info
        heading = self.query_one("#options-heading", Static)
        status = self.query_one("#options-status", Static)
        note = self.query_one("#options-note", Static)

        title = shorten(info.title, 96)
        sub_bits = [info.uploader, info.extractor]
        if info.is_live:
            sub_bits.append("LIVE")
        sub = "  ·  ".join(bit for bit in sub_bits if bit)
        if info.duration:
            from ..util import human_duration

            sub = f"{human_duration(info.duration)}  ·  {sub}" if sub else human_duration(info.duration)
        heading.update(f"{_plain(title)}\n[dim]{_plain(sub)}[/dim]" if sub else _plain(title))
        status.update("")

        choices: List[Tuple[str, str, str, Any]] = [
            (opt.label, opt.size_text, opt.detail, opt) for opt in info.options
        ]
        table = self.query_one("#choices", ChoiceList)
        table.set_choices(choices)
        table.focus()

        if info.playlist_note:
            note.update(_plain(info.playlist_note))

        if not info.options:
            self.query_one("#options-status", Static).update(
                "No downloadable qualities were found for this link."
            )
            return

        self.query_one("#download", Button).disabled = False
        if self.app.state.prefer_audio:
            for index, opt in enumerate(info.options):
                if opt.kind == "audio":
                    table.select_index(index)
                    break
        self.refresh_note()

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

        if option.needs_ffmpeg and not state.ffmpeg_available:
            button.remove_class("hidden")
            note.update(_plain(self.ffmpeg_prompt(state)))
        else:
            button.add_class("hidden")
            info = state.info
            if info is not None and info.playlist_note:
                note.update(_plain(info.playlist_note))
            elif option.kind == "audio":
                note.update(
                    "Audio only — saved as an MP3 in your Downloads folder. "
                    "MP3 sizes are a rough guide: the encoder keeps as much quality as the source has."
                )
            else:
                folder = self.app.services.downloads_dir()
                note.update(_plain(f"Saves to {folder}  ·  {option.size_text}"))

    def ffmpeg_prompt(self, state: State) -> str:
        """What setting ffmpeg up costs, before the user agrees to it.

        150 MB is a decision, so the size is asked for as soon as the button
        appears — and the asking is in a worker, because the answer needs the
        network. Until it arrives the sentence stays true without a number.
        """
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
        if isinstance(message.value, MediaOption):
            self.app.start_download(message.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "download":
            self.action_download()
        elif button_id == "back":
            self.action_go_back()
        elif button_id == "get-ffmpeg":
            self.setup_ffmpeg()

    def action_download(self) -> None:
        table = self.query_one("#choices", ChoiceList)
        option: Optional[MediaOption] = table.highlighted_value
        if option is None:
            self.notify("Choose a quality first.", severity="warning")
            return
        self.app.start_download(option)

    def action_go_back(self) -> None:
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
        heading = self.app.state.url
        if option is not None:
            heading = f"{option.label}  ·  {option.size_text}"
        line = _plain(heading)
        if info is not None and info.title:
            line = f"{_plain(shorten(info.title, 60))}\n[dim]{line}[/dim]"
        self.query_one("#download-heading", Static).update(line)
        self.run_download()

    @work(thread=True, exclusive=True, group="download")
    def run_download(self) -> None:
        app = self.app
        option = app.state.option
        if option is None:  # pragma: no cover - defensive
            return

        try:
            if option.needs_ffmpeg and not app.state.ffmpeg_available:
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

            out_dir = app.services.downloads_dir()
            job = app.services.make_job(
                app.services.engine,
                app.state.url,
                option,
                out_dir,
                on_progress=lambda progress: app._post(self.on_progress, progress),
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
        self.app.state.job = None
        if not self._has_a_screen():
            return
        self.app.push_screen(ResultScreen(saved_path=path))

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
        self.app.switch_screen(StoppedScreen(job=job, option=self.app.state.option))

    def action_cancel(self) -> None:
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
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.job = job
        self.option = option

    def compose(self) -> ComposeResult:
        with Vertical(id="stopped-panel"):
            yield Static("Stopped", id="brand")
            yield Static("", id="stopped-message")
            with Horizontal(classes="buttons"):
                if self.option is not None:
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
        if self.kept_files():
            lines.append(
                "What has been downloaded so far is kept, so Resume carries on "
                "from there. Discard throws it away."
            )
        else:
            lines.append("Nothing had been written to disk yet.")
        self.query_one("#stopped-message", Static).update("\n".join(lines))

    # -- actions ------------------------------------------------------------
    def action_resume(self) -> None:
        if self.app.state.option is None:  # pragma: no cover - defensive
            self.action_later()
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
    """Screen 4: where the file landed, or what went wrong."""

    BINDINGS = [
        Binding("escape", "go_home", "Start over", show=True),
        Binding("o", "open_folder", "Open folder", show=False),
        Binding("c", "copy_path", "Copy path", show=False),
    ]

    def __init__(
        self,
        saved_path: Optional[Path] = None,
        error: Optional[ToolError] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.saved_path = saved_path
        self.error = error

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
                yield Static("Saved", id="brand")
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
        self.query_one("#result-sub", Static).update(_plain(f"{label}  ·  {size}"))
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


def run(url: str = "", audio: bool = False, services: Optional[Services] = None) -> None:
    """Start the terminal app."""
    app = ViddlApp(initial_url=url, prefer_audio=audio, services=services)
    app.run()
