"""The tap on the shoulder when a long download ends (F9).

Three promises are held here. **Nothing is said unless asked** — notifications
are off by default, and no test below can turn them on by accident. **A short
run stays quiet** — the twenty-second floor is the whole reason a two-second
download does not pop a window over somebody's work. And **the fallback is the
bell** — every platform that has no notifier, or a notifier that fails, still
leaves a signal in the terminal rather than saying nothing at all.
"""

from __future__ import annotations

import io
import subprocess

from viddl import notify

LINUX = "linux"
MACOS = "darwin"
WINDOWS = "win32"


def which_only(*names: str):
    """A ``shutil.which`` that finds exactly these programs."""

    def which(name: str):
        return f"/usr/bin/{name}" if name in names else None

    return which


class Recorder:
    """Stands in for ``subprocess.run``: records the call, returns what it is told."""

    def __init__(self, returncode: int = 0, raises: BaseException | None = None):
        self.returncode = returncode
        self.raises = raises
        self.calls: list = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(argv, self.returncode, "", "")


# ---------------------------------------------------------------------------
# which command a machine gets
# ---------------------------------------------------------------------------


def test_linux_uses_notify_send_when_it_is_installed():
    argv = notify.command("Viddl finished", "Saved a.mp4", platform=LINUX, which=which_only("notify-send"))
    assert argv is not None
    assert argv[0] == "/usr/bin/notify-send"
    # The app name is how the notification is grouped and muted; the body and
    # title are the two strings the person actually reads.
    assert argv[-2:] == ["Viddl finished", "Saved a.mp4"]
    assert notify.APP in argv


def test_a_linux_without_a_notifier_has_only_the_bell():
    assert notify.command("t", "b", platform=LINUX, which=which_only()) is None
    assert notify.mechanism(platform=LINUX, which=which_only()) == "bell"


def test_macos_speaks_applescript():
    argv = notify.command("t", "b", platform=MACOS, which=which_only())
    assert argv is not None and argv[0] == "osascript"
    assert 'display notification "b"' in argv[2]
    assert 'with title "t"' in argv[2]


def test_macos_escapes_quotes_in_a_file_name():
    """A quote in a name is ordinary; an unescaped one ends the literal early."""
    argv = notify.command('title "x"', 'said "hi"', platform=MACOS, which=which_only())
    assert argv is not None
    script = argv[2]
    assert '\\"hi\\"' in script
    assert script.count('"') % 2 == 0, "an odd count means a literal is unclosed"


def test_windows_builds_a_toast_with_powershell():
    argv = notify.command("t", "b", platform=WINDOWS, which=which_only("powershell"))
    assert argv is not None
    assert argv[0] == "/usr/bin/powershell"
    # No profile, no prompt: a notification may not wait on anything.
    assert "-NoProfile" in argv and "-NonInteractive" in argv
    assert "ToastNotificationManager" in argv[-1]
    assert "<text>t</text>" in argv[-1] and "<text>b</text>" in argv[-1]


def test_windows_without_powershell_says_nothing_and_lets_the_bell_speak():
    assert notify.command("t", "b", platform=WINDOWS, which=which_only()) is None
    assert notify.mechanism(platform=WINDOWS, which=which_only()) == "bell"


def test_the_toast_escapes_what_goes_into_its_xml():
    """A title with an ampersand must not turn the toast into a parse error."""
    argv = notify.command("Rock & Roll", "<b>", platform=WINDOWS, which=which_only("powershell"))
    assert argv is not None
    assert "&amp;" in argv[-1]
    assert "&lt;b&gt;" in argv[-1]


def test_the_mechanism_names_what_will_be_used():
    assert notify.mechanism(platform=LINUX, which=which_only("notify-send")) == "notify-send"
    assert notify.mechanism(platform=MACOS, which=which_only()) == "osascript"
    assert notify.mechanism(platform=WINDOWS, which=which_only("pwsh")) == "powershell"


# ---------------------------------------------------------------------------
# sending, and falling back
# ---------------------------------------------------------------------------


def test_a_working_notifier_is_not_followed_by_a_bell():
    stream = io.StringIO()
    runner = Recorder()
    used = notify.send(
        "t", "b", stream=stream, runner=runner, platform=LINUX, which=which_only("notify-send")
    )
    assert used == "notify-send"
    assert len(runner.calls) == 1
    _script, kwargs = runner.calls[0]
    # A notifier that hangs must not hold the exit of a finished download.
    assert kwargs["timeout"] == notify._TIMEOUT
    assert kwargs["check"] is False
    assert stream.getvalue() == ""


def test_a_notifier_that_exits_nonzero_falls_back_to_the_bell():
    stream = io.StringIO()
    used = notify.send(
        "t",
        "b",
        stream=stream,
        runner=Recorder(returncode=1),
        platform=LINUX,
        which=which_only("notify-send"),
    )
    assert used == "bell"
    assert stream.getvalue() == "\a"


def test_a_notifier_that_cannot_start_falls_back_to_the_bell():
    stream = io.StringIO()
    used = notify.send(
        "t",
        "b",
        stream=stream,
        runner=Recorder(raises=OSError("no such file")),
        platform=LINUX,
        which=which_only("notify-send"),
    )
    assert used == "bell"
    assert stream.getvalue() == "\a"


def test_a_notifier_that_times_out_falls_back_to_the_bell():
    """``TimeoutExpired`` is a ``SubprocessError``: caught, never raised."""
    stream = io.StringIO()
    used = notify.send(
        "t",
        "b",
        stream=stream,
        runner=Recorder(raises=subprocess.TimeoutExpired("notify-send", 5)),
        platform=LINUX,
        which=which_only("notify-send"),
    )
    assert used == "bell"
    assert stream.getvalue() == "\a"


def test_the_bell_can_be_declined_where_it_has_already_rung():
    """The accessible mode rings once; a notification must not make it twice."""
    stream = io.StringIO()
    used = notify.send(
        "t", "b", stream=stream, bell=False, platform=LINUX, which=which_only()
    )
    assert used == "bell"
    assert stream.getvalue() == ""


def test_a_broken_stream_cannot_turn_a_finished_download_into_an_error():
    class Gone(io.StringIO):
        def write(self, _text):  # pragma: no cover - exercised through ring
            raise OSError("the pipe went away")

    notify.ring(Gone())  # must not raise


# ---------------------------------------------------------------------------
# when it is worth saying
# ---------------------------------------------------------------------------


def test_a_quick_download_says_nothing_at_all():
    """Under twenty seconds the person is still looking at the terminal."""
    runner = Recorder()
    used = notify.tell(elapsed=notify.MIN_SECONDS - 0.1, runner=runner, platform=LINUX, which=which_only("notify-send"))
    assert used == ""
    assert runner.calls == []


def test_a_long_download_names_the_file_it_saved():
    runner = Recorder()
    notify.tell(
        saved="/home/somebody/Videos/Song [abc].mp4",
        elapsed=notify.MIN_SECONDS,
        runner=runner,
        platform=LINUX,
        which=which_only("notify-send"),
    )
    body = runner.calls[0][0][-1]
    assert body == "Saved Song [abc].mp4"
    assert runner.calls[0][0][-2] == "Viddl finished"


def test_a_failure_says_so_without_pretending_it_worked():
    runner = Recorder()
    notify.tell(
        failed=True,
        elapsed=60,
        runner=runner,
        platform=LINUX,
        which=which_only("notify-send"),
    )
    title, body = runner.calls[0][0][-2:]
    assert "could not" in title
    assert "failed" in body
    assert "terminal" in body, "the reason is in the terminal, and the body says where"


def test_a_stopped_download_is_not_reported_as_a_failure():
    runner = Recorder()
    notify.tell(
        stopped=True,
        elapsed=60,
        runner=runner,
        platform=LINUX,
        which=which_only("notify-send"),
    )
    title, body = runner.calls[0][0][-2:]
    assert "could not" not in title
    assert "stopped" in body.lower()


def test_a_playlist_counts_instead_of_naming_forty_files():
    runner = Recorder()
    notify.tell(
        count=9, elapsed=600, runner=runner, platform=LINUX, which=which_only("notify-send")
    )
    assert runner.calls[0][0][-1] == "Saved 9 videos."


def test_one_video_is_still_one_video():
    assert notify.message(count=1) == "Saved 1 video."


def test_a_message_with_nothing_to_report_says_only_that_it_finished():
    assert notify.message() == "Finished."
