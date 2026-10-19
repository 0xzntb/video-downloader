"""The command line surface."""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

import pytest

from viddl import appdata, cli
from viddl.cli import main
from viddl.engine import Tool, ToolError
from viddl.formats import MediaInfo, MediaOption
from viddl.protocol import RegistrationResult, build_scheme_url, extension_source_dir

REPO_EXTENSION = extension_source_dir()
URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_doctor_reports_paths_and_tools(capsys):
    assert main(["--doctor"]) == 0
    output = capsys.readouterr().out
    assert "Viddl" in output
    assert "yt-dlp" in output
    assert "ffmpeg" in output
    assert str(appdata.data_dir()) in output
    assert str(appdata.downloads_dir()) in output


def test_doctor_says_when_the_app_folder_cannot_be_made(capsys, monkeypatch, tmp_path):
    """The doctor is where a broken setup is asked about, so it has to answer."""
    blocker = tmp_path / "blocked"
    blocker.write_text("not a folder", encoding="utf-8")
    monkeypatch.setenv("VIDDL_HOME", str(blocker / "viddl"))

    assert main(["--doctor"]) == 0
    output = capsys.readouterr().out

    assert "Problem:" in output
    assert str(blocker / "viddl") in output
    assert "VIDDL_HOME" in output


def test_doctor_has_no_problem_line_when_the_folder_is_fine(capsys):
    assert main(["--doctor"]) == 0
    assert "Problem:" not in capsys.readouterr().out


def test_doctor_says_which_overrides_are_in_effect(capsys, monkeypatch):
    """An environment variable is invisible in the app, so the doctor says it."""
    monkeypatch.setenv("VIDDL_YTDLP", "--exec=calc.exe")

    assert main(["--doctor"]) == 0
    output = capsys.readouterr().out

    assert "Environment:" in output
    assert f"VIDDL_HOME={os.environ['VIDDL_HOME']} (in use)" in output
    assert "VIDDL_YTDLP=--exec=calc.exe (ignored" in output


def test_doctor_shows_the_extra_yt_dlp_options(capsys, monkeypatch):
    """The one deliberate way to add a setting is visible where the others are."""
    monkeypatch.setenv("VIDDL_YTDLP_ARGS", "--limit-rate 2M")

    assert main(["--doctor"]) == 0
    assert "VIDDL_YTDLP_ARGS=--limit-rate 2M (in use)" in capsys.readouterr().out

    monkeypatch.setenv("VIDDL_YTDLP_ARGS", "-f best")
    assert main(["--doctor"]) == 0
    assert "(ignored — sets --format, which Viddl decides)" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# viddl --update (R4)
#
# The engine is the part that goes stale, so the command exists and says what
# changed — a version before and a version after, not "done".
# ---------------------------------------------------------------------------


def tool(version: str, origin: str = "viddl") -> Tool:
    return Tool("yt-dlp", [f"/fake/{version}"], origin, version=version, path=None)


class UpdateEngine:
    """An engine whose download is a version you hand it."""

    def __init__(self, before=None, after=None, in_use=None, ffmpeg=None, fail=False):
        self.before = before
        self.after = after or tool("2099.06.06")
        self.in_use = in_use if in_use is not None else self.after
        self.ffmpeg = ffmpeg
        self.fail = fail
        self.updated = 0

    def update_ytdlp(self, on_status=None, on_progress=None):
        self.updated += 1
        if self.fail:
            raise ToolError("Could not reach the list of checksums.", "Nothing was installed.")
        if on_status:
            on_status("Downloading the video engine (about 3 MB)…")
        if on_progress:
            on_progress(50, 100)
        return self.before, self.after

    def update_ffmpeg(self, on_status=None, on_progress=None):
        return None, Tool("ffmpeg", ["/fake/ffmpeg"], "viddl", version="7.1", path=None)

    def find_ytdlp(self):
        return self.in_use


def run_update(capsys, monkeypatch, engine, args=("--update",)):
    monkeypatch.setattr(cli, "Engine", lambda *a, **k: engine)
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_update_says_what_the_version_was_and_is(capsys, monkeypatch):
    code, out, err = run_update(capsys, monkeypatch, UpdateEngine(before=tool("2099.05.05")))

    assert code == 0
    assert "yt-dlp 2099.05.05 -> 2099.06.06" in out
    assert "Downloading the video engine" in err


def test_update_on_a_first_install_says_so(capsys, monkeypatch):
    code, out, _ = run_update(capsys, monkeypatch, UpdateEngine(before=None))

    assert code == 0
    assert "Installed yt-dlp 2099.06.06" in out


def test_update_says_when_there_was_nothing_new(capsys, monkeypatch):
    same = tool("2099.06.06")
    code, out, _ = run_update(capsys, monkeypatch, UpdateEngine(before=same, after=same))

    assert code == 0
    assert "already the newest release" in out


def test_update_says_when_another_copy_will_still_be_used(capsys, monkeypatch):
    """Updating Viddl's copy is pointless if a newer one elsewhere still wins."""
    engine = UpdateEngine(before=tool("2099.05.05"), in_use=tool("2099.09.09", "system"))
    code, out, _ = run_update(capsys, monkeypatch, engine)

    assert code == 0
    assert "found on your computer (2099.09.09)" in out
    assert "VIDDL_YTDLP" in out


def test_update_says_when_the_copy_you_pointed_at_still_wins(capsys, monkeypatch):
    """A copy named by the environment wins whatever its age, so it is named."""
    engine = UpdateEngine(before=tool("2099.05.05"), in_use=tool("2020.01.01", "environment"))
    code, out, _ = run_update(capsys, monkeypatch, engine)

    assert code == 0
    assert "you pointed it at (2020.01.01, VIDDL_YTDLP)" in out
    assert "Unset that" in out


def test_update_can_take_ffmpeg_with_it(capsys, monkeypatch):
    engine = UpdateEngine(before=tool("2099.05.05"))
    code, out, _ = run_update(capsys, monkeypatch, engine, args=("--update", "--update-ffmpeg"))

    assert code == 0
    assert "yt-dlp 2099.05.05 -> 2099.06.06" in out
    assert "Installed ffmpeg 7.1" in out


def test_ffmpeg_can_be_updated_on_its_own(capsys, monkeypatch):
    """One flag, one thing fetched: "my merge fails" is not "my links broke"."""
    engine = UpdateEngine()
    code, out, _ = run_update(capsys, monkeypatch, engine, args=("--update-ffmpeg",))

    assert code == 0
    assert engine.updated == 0
    assert "ffmpeg" in out
    assert "yt-dlp" not in out


def test_a_failed_update_is_explained_and_returns_nonzero(capsys, monkeypatch):
    code, out, err = run_update(capsys, monkeypatch, UpdateEngine(fail=True))

    assert code == 1
    assert "Could not reach the list of checksums." in err
    assert "Nothing was installed." in err
    assert out == ""


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as caught:
        main(["--version"])
    assert caught.value.code == 0
    assert "Viddl" in capsys.readouterr().out


def test_download_dir_flag_is_honoured(tmp_path, capsys):
    target = tmp_path / "Elsewhere"
    assert main(["--download-dir", str(target), "--doctor"]) == 0
    assert os.environ["VIDDL_DOWNLOAD_DIR"] == str(target)
    assert str(target) in capsys.readouterr().out


def test_extension_path_prints_a_real_folder(capsys):
    assert main(["--extension-path"]) == 0
    printed = Path(capsys.readouterr().out.strip())
    assert (printed / "manifest.json").is_file()


def test_install_browser_extension_copies_out(capsys):
    assert main(["--install-browser-extension"]) == 0
    output = capsys.readouterr().out
    copied = appdata.data_dir() / "browser-extension"
    assert (copied / "manifest.json").is_file()
    assert str(copied) in output
    assert "chrome://extensions" in output
    manifest = json.loads((copied / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 3


def test_a_sentence_is_not_a_link(capsys):
    assert main(["hello there, download this for me"]) == 1
    assert "does not look like a video link" in capsys.readouterr().err


def test_an_empty_hand_off_is_rejected(capsys):
    assert main(["--uri", "viddl://"]) == 1
    assert "could not be understood" in capsys.readouterr().err


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux desktop entry")
def test_register_protocol_reports_honestly(capsys):
    code = main(["--register-protocol"])
    captured = capsys.readouterr()
    desktop = Path.home() / ".local" / "share" / "applications" / "viddl-handler.desktop"
    assert desktop.is_file()
    assert code in (0, 1)
    assert (captured.out or captured.err).strip()


# ---------------------------------------------------------------------------
# viddl:// registration
# ---------------------------------------------------------------------------


def test_unregister_protocol_reports_the_result(monkeypatch, capsys):
    monkeypatch.setattr(
        "viddl.cli.unregister",
        lambda: RegistrationResult(
            ok=True,
            message="viddl:// links no longer open Viddl.",
            steps=["Removed the desktop entry."],
        ),
    )
    assert main(["--unregister-protocol"]) == 0
    out = capsys.readouterr().out
    assert "no longer open Viddl" in out
    assert "Removed the desktop entry." in out


def test_a_registration_that_fails_explains_itself(monkeypatch, capsys):
    monkeypatch.setattr(
        "viddl.cli.register",
        lambda: RegistrationResult(
            ok=False,
            message="Could not write the launcher.",
            steps=["Permission denied."],
        ),
    )
    assert main(["--register-protocol"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Could not write the launcher." in captured.err
    assert "Permission denied." in captured.err


# ---------------------------------------------------------------------------
# --list-qualities
# ---------------------------------------------------------------------------


def test_list_qualities_prints_the_menu(monkeypatch, capsys):
    info = MediaInfo(
        url=URL,
        title="A Very Real Video",
        uploader="Some Channel",
        options=[
            MediaOption(key="1080", label="1080p", size_bytes=20_000_000),
            MediaOption(key="360", label="360p", size_bytes=9_000_000),
        ],
    )
    monkeypatch.setattr("viddl.download.probe_video", lambda engine, url: info)
    assert main(["--list-qualities", URL]) == 0
    out = capsys.readouterr().out
    assert "A Very Real Video" in out
    assert "Some Channel" in out
    assert "1080p" in out
    assert "360p" in out


def test_list_qualities_omits_an_unknown_uploader(monkeypatch, capsys):
    info = MediaInfo(url=URL, title="Untitled upload", uploader="")
    monkeypatch.setattr("viddl.download.probe_video", lambda engine, url: info)
    assert main(["--list-qualities", URL]) == 0
    assert capsys.readouterr().out.startswith("Untitled upload")


def test_list_qualities_rejects_a_sentence(capsys):
    assert main(["--list-qualities", "not a link at all"]) == 1
    assert "does not look like a link" in capsys.readouterr().err


def test_list_qualities_explains_a_failed_probe(monkeypatch, capsys):
    def boom(engine, url):
        raise ToolError(
            "The video engine could not be started.",
            "Try running Viddl again.",
        )

    monkeypatch.setattr("viddl.download.probe_video", boom)
    assert main(["--list-qualities", URL]) == 1
    err = capsys.readouterr().err
    assert "could not be started" in err
    assert "Try running Viddl again." in err


# ---------------------------------------------------------------------------
# installing the extension
# ---------------------------------------------------------------------------


def test_extension_copy_failure_is_explained(monkeypatch, capsys):
    def boom(destination=None):
        raise OSError("No space left on device")

    monkeypatch.setattr("viddl.cli.install_extension", boom)
    assert main(["--install-browser-extension"]) == 1
    err = capsys.readouterr().err
    assert "Could not copy the extension" in err
    assert "No space left on device" in err


# ---------------------------------------------------------------------------
# opening the app
# ---------------------------------------------------------------------------


def _capture_run(monkeypatch) -> dict:
    """Replace the terminal app with a recorder and return where it writes."""
    seen: dict = {}
    monkeypatch.setattr(
        "viddl.tui.app.run",
        lambda *, url, audio: seen.update(url=url, audio=audio),
    )
    return seen


def test_a_browser_hand_off_opens_the_app_on_the_audio_options(monkeypatch):
    seen = _capture_run(monkeypatch)
    assert main(["--uri", build_scheme_url(URL, audio=True)]) == 0
    assert seen == {"url": URL, "audio": True}


def test_a_scheme_link_can_arrive_as_a_plain_argument(monkeypatch):
    seen = _capture_run(monkeypatch)
    assert main([build_scheme_url(URL, audio=False)]) == 0
    assert seen == {"url": URL, "audio": False}


def test_a_plain_link_opens_the_app(monkeypatch):
    seen = _capture_run(monkeypatch)
    assert main(["--audio", URL]) == 0
    assert seen == {"url": URL, "audio": True}


# ---------------------------------------------------------------------------
# a page may ask more than once (S5)
# ---------------------------------------------------------------------------


def test_a_burst_of_hand_offs_opens_the_app_once_with_a_link(monkeypatch):
    """The second hand-off in a burst is not acted on; the app still opens."""
    seen = _capture_run(monkeypatch)
    hand_off = build_scheme_url(URL, audio=False)

    assert main(["--uri", hand_off]) == 0
    assert seen == {"url": URL, "audio": False}

    assert main(["--uri", hand_off]) == 0
    assert seen == {"url": "", "audio": False}


def test_a_link_typed_by_hand_is_never_rate_limited(monkeypatch):
    """Only a page can arrive uninvited; a typed link is deliberate."""
    seen = _capture_run(monkeypatch)
    assert main(["--uri", build_scheme_url(URL)]) == 0
    assert seen == {"url": URL, "audio": False}

    assert main([URL]) == 0
    assert seen == {"url": URL, "audio": False}


def test_the_audio_flag_survives_a_throttled_hand_off(monkeypatch):
    """The flag came from the person typing, not from the page."""
    seen = _capture_run(monkeypatch)
    assert main(["--uri", build_scheme_url(URL, audio=True)]) == 0
    assert seen == {"url": URL, "audio": True}

    assert main(["--audio", "--uri", build_scheme_url(URL, audio=True)]) == 0
    assert seen == {"url": "", "audio": True}


def test_an_audio_hand_off_wins_over_nothing(monkeypatch):
    seen = _capture_run(monkeypatch)
    assert main(["--uri", build_scheme_url(URL, audio=False)]) == 0
    assert seen == {"url": URL, "audio": False}


def test_a_bare_site_is_upgraded_before_it_is_used(monkeypatch):
    seen = _capture_run(monkeypatch)
    assert main(["www.example.com/watch?v=x"]) == 0
    assert seen == {"url": "https://www.example.com/watch?v=x", "audio": False}


# ---------------------------------------------------------------------------
# nothing option-shaped gets through
# ---------------------------------------------------------------------------


def _refuse_to_launch(monkeypatch) -> None:
    """Fail the test if the app is started with a link that should not pass."""

    def explode(*args, **kwargs):
        raise AssertionError("the link should never have reached the app")

    monkeypatch.setattr("viddl.tui.app.run", explode)


def test_an_option_shaped_hand_off_is_refused(monkeypatch, capsys):
    _refuse_to_launch(monkeypatch)
    assert main(["--uri", build_scheme_url("--config-locations=./evil.conf")]) == 1
    assert "could not be understood" in capsys.readouterr().err


def test_an_option_shaped_argument_is_refused(monkeypatch, capsys):
    _refuse_to_launch(monkeypatch)
    assert main(["--uri=--exec=calc.exe"]) == 1
    assert "does not look like a video link" in capsys.readouterr().err


def test_an_option_shaped_list_qualities_target_is_refused(monkeypatch, capsys):
    _refuse_to_launch(monkeypatch)
    assert main(["--list-qualities=--exec=calc.exe"]) == 1
    assert "does not look like a link" in capsys.readouterr().err


def test_a_scheme_shaped_argument_is_refused(monkeypatch, capsys):
    _refuse_to_launch(monkeypatch)
    assert main(["--uri=file:///etc/passwd"]) == 1
    assert "does not look like a video link" in capsys.readouterr().err


def test_no_real_terminal_is_explained(monkeypatch, capsys):
    def no_terminal(*, url, audio):
        raise OSError("Inappropriate ioctl for device")

    monkeypatch.setattr("viddl.tui.app.run", no_terminal)
    assert main([URL]) == 1
    err = capsys.readouterr().err
    assert "needs a real terminal window" in err
    assert "Inappropriate ioctl for device" in err


# ---------------------------------------------------------------------------
# the log file is a nicety, never a requirement
# ---------------------------------------------------------------------------


def test_a_log_file_that_cannot_be_written_is_not_fatal(monkeypatch, capsys):
    logger = logging.getLogger("viddl")
    saved = list(logger.handlers)
    for handler in saved:
        logger.removeHandler(handler)

    def exploding_log_path():
        raise OSError("read-only file system")

    monkeypatch.setattr(appdata, "log_path", exploding_log_path)
    try:
        assert main(["--doctor"]) == 0
        assert "Viddl" in capsys.readouterr().out
    finally:
        for handler in saved:
            logger.addHandler(handler)

def test_the_doctor_says_what_it_waits_for(capsys, monkeypatch):
    """The wait is a number somebody set, so the doctor quotes it (R11)."""
    monkeypatch.delenv("VIDDL_TIMEOUT", raising=False)

    assert main(["--doctor"]) == 0
    assert "Network:      built-in waits" in capsys.readouterr().out

    monkeypatch.setenv("VIDDL_TIMEOUT", "45")
    assert main(["--doctor"]) == 0
    assert "Network:      waiting 45s per network request" in capsys.readouterr().out
