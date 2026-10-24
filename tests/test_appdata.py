"""Where Viddl keeps its own files, and how it finds the Downloads folder.

These run with the environment variables cleared on purpose: the point is to
exercise the real per-platform decisions, not the test overrides.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from viddl import appdata

needs_posix_permissions = pytest.mark.skipif(
    sys.platform.startswith("win"), reason="Windows governs access with ACLs"
)

#: The XDG desktop conventions do not exist on Windows, which has its own
#: known-folder locations tested beside these (R10).
needs_posix_desktop = pytest.mark.skipif(
    sys.platform.startswith("win"), reason="XDG desktop conventions are POSIX"
)


def mode_of(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def write_executable(path: Path, body: str = "#!/bin/sh\necho 1.0.0\n") -> Path:
    """A file a test can point an override at."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture
def no_overrides(monkeypatch):
    """Drop the test shortcuts so the platform logic runs for real."""
    monkeypatch.delenv("VIDDL_HOME", raising=False)
    monkeypatch.delenv("VIDDL_DOWNLOAD_DIR", raising=False)


# ---------------------------------------------------------------------------
# app folder
# ---------------------------------------------------------------------------


def test_data_dir_honours_the_environment_override(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDDL_HOME", str(tmp_path / "custom"))
    assert appdata.data_dir() == tmp_path / "custom"


@needs_posix_desktop
def test_data_dir_follows_xdg_on_linux(no_overrides, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert appdata.data_dir() == tmp_path / "xdg" / "viddl"


@needs_posix_desktop
def test_data_dir_defaults_to_local_share_without_xdg(no_overrides, monkeypatch):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert appdata.data_dir() == Path.home() / ".local" / "share" / "viddl"


def test_data_dir_uses_application_support_on_macos(no_overrides, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert appdata.data_dir() == Path.home() / "Library" / "Application Support" / "viddl"


def test_data_dir_uses_local_appdata_on_windows(no_overrides, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    assert appdata.data_dir() == tmp_path / "AppData" / "Local" / "viddl"


def test_bin_dir_and_log_sit_inside_the_app_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDDL_HOME", str(tmp_path / "app"))
    assert appdata.bin_dir() == tmp_path / "app" / "bin"
    assert appdata.log_path() == tmp_path / "app" / "viddl.log"


def test_ensure_dirs_creates_the_folders(tmp_path, monkeypatch):
    home = tmp_path / "app"
    monkeypatch.setenv("VIDDL_HOME", str(home))
    appdata.ensure_dirs()
    assert (home / "bin").is_dir()


def test_ensure_dirs_stays_quiet_when_it_cannot_write(tmp_path, monkeypatch):
    # A path that cannot exist, because a file is in the way.
    blocker = tmp_path / "blocked"
    blocker.write_text("not a folder", encoding="utf-8")
    monkeypatch.setenv("VIDDL_HOME", str(blocker / "viddl"))
    problem = appdata.ensure_dirs()  # must not raise
    assert problem is not None, "the failure is reported, not swallowed"


def test_ensure_dirs_returns_nothing_when_the_folders_are_there(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDDL_HOME", str(tmp_path / "app"))
    assert appdata.ensure_dirs() is None


def test_the_problem_says_which_folder_and_what_to_do_about_it(tmp_path, monkeypatch):
    """R7: a read-only home used to surface later as a downloads failure."""
    blocker = tmp_path / "blocked"
    blocker.write_text("not a folder", encoding="utf-8")
    home = blocker / "viddl"
    monkeypatch.setenv("VIDDL_HOME", str(home))

    problem = appdata.ensure_dirs()

    assert problem is not None
    assert problem.path == home
    assert problem.reason, "a reason is always given, never an empty bracket"
    assert "viddl --doctor" in problem.line
    assert len(problem.line) <= 76  # it has to fit on a terminal line
    assert str(home) in problem.details[0]
    assert "VIDDL_HOME" in problem.details[1]


def test_it_names_the_folder_that_failed_not_one_that_was_fine(tmp_path, monkeypatch):
    home = tmp_path / "app"
    home.mkdir()
    (home / "bin").write_text("not a folder", encoding="utf-8")
    monkeypatch.setenv("VIDDL_HOME", str(home))

    problem = appdata.ensure_dirs()

    assert problem is not None
    assert problem.path == home / "bin"


# ---------------------------------------------------------------------------
# keeping the app folder to ourselves (S3)
#
# The folder holds two downloaded programs. Another account on a shared machine
# should be able to read neither them nor the log beside them, and should not be
# able to drop a replacement in their place.
# ---------------------------------------------------------------------------


@needs_posix_permissions
def test_the_app_folder_and_bin_are_owner_only(tmp_path, monkeypatch):
    home = tmp_path / "app"
    monkeypatch.setenv("VIDDL_HOME", str(home))
    appdata.ensure_dirs()
    assert mode_of(home) == 0o700
    assert mode_of(home / "bin") == 0o700


@needs_posix_permissions
def test_a_folder_left_loose_by_an_earlier_version_is_tightened(tmp_path, monkeypatch):
    """An upgrade has to fix what is already on disk, not only what it creates."""
    home = tmp_path / "app"
    (home / "bin").mkdir(parents=True)
    os.chmod(home, 0o755)
    os.chmod(home / "bin", 0o775)
    monkeypatch.setenv("VIDDL_HOME", str(home))

    appdata.ensure_dirs()

    assert mode_of(home) == 0o700
    assert mode_of(home / "bin") == 0o700


@needs_posix_permissions
def test_a_permissive_umask_cannot_loosen_the_app_folder(tmp_path, monkeypatch):
    """A mode asked for at ``mkdir`` time is the umask's to trim; this one is not."""
    monkeypatch.setenv("VIDDL_HOME", str(tmp_path / "app"))
    previous = os.umask(0)
    try:
        appdata.ensure_dirs()
    finally:
        os.umask(previous)
    assert mode_of(tmp_path / "app") == 0o700


@needs_posix_permissions
def test_a_folder_that_is_not_viddls_is_left_alone(tmp_path, monkeypatch):
    """``VIDDL_HOME`` may point at your home directory; that is not ours to lock."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    os.chmod(home, 0o755)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIDDL_HOME", str(home))

    appdata.ensure_dirs()

    assert mode_of(home) == 0o755
    assert mode_of(home / "bin") == 0o700


def test_the_root_directory_is_recognised_as_not_ours():
    """Asserted through the guard rather than by running ``chmod`` on ``/``."""
    assert appdata._not_ours_to_tighten(Path("/")) is True
    assert appdata._not_ours_to_tighten(Path.home()) is True
    assert appdata._not_ours_to_tighten(Path("/tmp")) is False


def test_windows_keeps_its_own_access_rules(monkeypatch, tmp_path):
    """Rather than pretending a POSIX mode means something there."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("VIDDL_HOME", raising=False)
    changed: list = []
    monkeypatch.setattr(os, "chmod", lambda path, mode: changed.append(path))

    appdata.ensure_dirs()

    assert changed == []
    assert (tmp_path / "viddl" / "bin").is_dir()


def test_a_filesystem_without_permissions_does_not_stop_viddl(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDDL_HOME", str(tmp_path / "app"))

    def refuse(path, mode):
        raise OSError("operation not supported")

    monkeypatch.setattr(os, "chmod", refuse)
    appdata.ensure_dirs()  # must not raise
    assert (tmp_path / "app" / "bin").is_dir()


# ---------------------------------------------------------------------------
# the environment overrides (S6)
#
# An environment variable is not a command line: nobody sees it in the app, and
# in a shared checkout or a CI job it can be set by something other than the
# person using Viddl. So each one is a request that can be refused.
# ---------------------------------------------------------------------------


@pytest.fixture
def no_environment():
    """Clear every variable, so the platform defaults are what gets tested."""
    saved = {name: os.environ.pop(name, None) for name in appdata.ENVIRONMENT_VARIABLES}
    yield
    for name, value in saved.items():
        if value is not None:
            os.environ[name] = value


def test_the_variables_are_the_ones_the_readme_lists(no_environment):
    assert appdata.ENVIRONMENT_VARIABLES == (
        "VIDDL_HOME",
        "VIDDL_DOWNLOAD_DIR",
        "VIDDL_YTDLP",
        "VIDDL_FFMPEG",
        "VIDDL_YTDLP_ARGS",
        "VIDDL_TIMEOUT",
        "VIDDL_ARCHIVE",
        "VIDDL_QUALITY",
        "VIDDL_THEME",
    )
    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text(
        encoding="utf-8"
    )
    for name in appdata.ENVIRONMENT_VARIABLES:
        assert f"`{name}`" in readme, f"{name} is read but not documented"


def test_a_folder_override_is_used_and_made_absolute(no_environment, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VIDDL_HOME", "mine")
    assert appdata.data_dir() == tmp_path / "mine"


def test_a_download_folder_override_is_used(no_environment, tmp_path, monkeypatch):
    monkeypatch.setenv("VIDDL_DOWNLOAD_DIR", str(tmp_path / "Videos"))
    assert appdata.downloads_dir() == tmp_path / "Videos"


@pytest.mark.parametrize(
    "value, reason",
    [
        ("--exec=echo RCE-MARKER", "looks like an option, not a path"),
        ("-o/tmp/elsewhere", "looks like an option, not a path"),
        ("", "empty"),
        ("   ", "empty"),
        ("two\nlines", "contains a control character"),
    ],
)
def test_a_folder_override_that_is_not_a_path_is_refused(
    no_environment, tmp_path, monkeypatch, value, reason
):
    monkeypatch.setenv("VIDDL_HOME", value)
    assert appdata.data_dir() == Path.home() / ".local" / "share" / "viddl"
    assert appdata.overrides() == [appdata.Override("VIDDL_HOME", value.strip(), reason)]


def test_a_folder_override_naming_a_file_is_refused(no_environment, tmp_path, monkeypatch):
    """It would fail later, and a confusing failure is worse than being ignored."""
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("text", encoding="utf-8")
    monkeypatch.setenv("VIDDL_HOME", str(blocker))

    assert appdata.data_dir() != blocker
    assert [entry.problem for entry in appdata.overrides()] == ["not a folder"]


def test_a_program_override_may_be_a_path(no_environment, tmp_path, monkeypatch):
    program = write_executable(tmp_path / "elsewhere" / "yt-dlp", "#!/bin/sh\n")
    monkeypatch.setenv("VIDDL_YTDLP", str(program))
    assert appdata.program_override("VIDDL_YTDLP") == program


def test_a_bare_program_name_comes_from_the_path(no_environment, tmp_path, monkeypatch):
    """``yt-dlp`` means the PATH, not whatever is in the working directory."""
    planted = write_executable(tmp_path / "yt-dlp", "#!/bin/sh\necho PLANTED\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VIDDL_YTDLP", "yt-dlp")

    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert planted.exists(), "the test needs the planted file to be there"
    assert appdata.program_override("VIDDL_YTDLP") is None

    genuine = write_executable(tmp_path / "real" / "yt-dlp", "#!/bin/sh\n")
    monkeypatch.setenv("PATH", str(tmp_path / "real"))
    assert appdata.program_override("VIDDL_YTDLP") == genuine


@needs_posix_permissions
def test_a_program_override_that_cannot_be_run_is_refused(
    no_environment, tmp_path, monkeypatch
):
    program = tmp_path / "yt-dlp"
    program.write_text("#!/bin/sh\n", encoding="utf-8")
    program.chmod(0o644)
    monkeypatch.setenv("VIDDL_YTDLP", str(program))
    assert appdata.program_override("VIDDL_YTDLP") is None


def test_a_program_override_naming_a_folder_is_refused(no_environment, tmp_path, monkeypatch):
    folder = tmp_path / "a-folder"
    folder.mkdir()
    monkeypatch.setenv("VIDDL_FFMPEG", str(folder))
    assert appdata.program_override("VIDDL_FFMPEG") is None


def test_nothing_is_reported_when_nothing_is_set(no_environment):
    assert appdata.overrides() == []
    assert appdata.environment_report() == []


def test_the_report_says_what_became_of_each_variable(no_environment, tmp_path, monkeypatch):
    monkeypatch.setenv("VIDDL_HOME", str(tmp_path / "app"))
    monkeypatch.setenv("VIDDL_YTDLP", "--exec=calc")

    report = appdata.environment_report()

    assert report[0].startswith("Environment:")
    assert "VIDDL_HOME=" in report[0] and "(in use)" in report[0]
    assert "VIDDL_YTDLP=--exec=calc (ignored — looks like an option, not a path)" in report[1]


def test_an_override_of_an_unknown_program_says_so(no_environment, monkeypatch):
    monkeypatch.setenv("VIDDL_FFMPEG", "ffmpeg-on-my-path")  # nothing of that name
    assert [entry.problem for entry in appdata.overrides()] == [
        "not a program Viddl can run"
    ]


# ---------------------------------------------------------------------------
# extra yt-dlp options, deliberately (R3)
#
# yt-dlp's own config file used to change every run behind the user's back. It
# is now ignored, and this is the documented way to ask for something unusual:
# named, visible in --doctor, and refused when it would take over a decision the
# app has already made on the user's behalf.
# ---------------------------------------------------------------------------


def test_no_extra_options_unless_asked_for(no_environment):
    assert appdata.ytdlp_arguments() == []
    assert appdata.ytdlp_arguments_problem() == ""


def test_set_but_empty_is_said_to_be_empty(no_environment, monkeypatch):
    """The other variables are reported when set and useless; so is this one."""
    monkeypatch.setenv("VIDDL_YTDLP_ARGS", "   ")
    assert appdata.ytdlp_arguments() == []
    assert appdata.ytdlp_arguments_problem() == "empty"
    assert appdata.environment_report() == [
        "Environment:  VIDDL_YTDLP_ARGS= (ignored — empty)"
    ]


def test_extra_options_are_split_the_way_a_shell_splits_them(no_environment, monkeypatch):
    monkeypatch.setenv(
        "VIDDL_YTDLP_ARGS", '--limit-rate 2M --cookies-from-browser chrome --user-agent "a b"'
    )
    assert appdata.ytdlp_arguments() == [
        "--limit-rate",
        "2M",
        "--cookies-from-browser",
        "chrome",
        "--user-agent",
        "a b",
    ]
    assert appdata.ytdlp_arguments_problem() == ""


@pytest.mark.parametrize(
    "value, reason",
    [
        ("-o/tmp/elsewhere/%(title)s.%(ext)s", "sets --output, which Viddl decides"),
        ("--output %(ext)s", "sets --output, which Viddl decides"),
        ("-P /tmp", "sets --paths, which Viddl decides"),
        ("--paths=/tmp", "sets --paths, which Viddl decides"),
        ("-f best", "sets --format, which Viddl decides"),
        ("--format=worst", "sets --format, which Viddl decides"),
        ("--config-locations=./c.conf", "sets --config-locations, which Viddl decides"),
        ("--progress-template %(progress)s", "sets --progress-template, which Viddl decides"),
        ("-s", "sets --simulate, which Viddl decides"),
        ("--skip-download", "sets --skip-download, which Viddl decides"),
        # yt-dlp accepts abbreviations, so an abbreviation has to be refused too.
        ("--form best", "sets --format, which Viddl decides"),
        ("--path /tmp", "sets --paths, which Viddl decides"),
        ("--out %(ext)s", "sets --output, which Viddl decides"),
        ("--ski", "sets --skip-download, which Viddl decides"),
        ("--sim", "sets --simulate, which Viddl decides"),
        ("--config-loc ./c.conf", "sets --config-locations, which Viddl decides"),
        ("-- https://x.invalid/v", "would end Viddl's own options early"),
        ("https://x.invalid/v", "contains a link, not an option: https://x.invalid/v"),
        (
            "--limit-rate 2M www.example.invalid/v",
            "contains a link, not an option: www.example.invalid/v",
        ),
        ('--limit-rate "2M', "unbalanced quotes"),
        ("--limit-rate 2M\n--exec echo hi", "contains a control character"),
    ],
)
def test_an_extra_option_that_takes_over_a_decision_is_refused(
    no_environment, monkeypatch, value, reason
):
    """Refused whole: half a command line is a command line nobody wrote."""
    monkeypatch.setenv("VIDDL_YTDLP_ARGS", value)

    assert appdata.ytdlp_arguments() == []
    assert appdata.ytdlp_arguments_problem() == reason


def test_a_second_link_is_refused_but_a_word_that_is_a_value_is_not(
    no_environment, monkeypatch
):
    """``--limit-rate 2M`` has to survive: a value is not a setting."""
    monkeypatch.setenv("VIDDL_YTDLP_ARGS", "--limit-rate 2M")
    assert appdata.ytdlp_arguments() == ["--limit-rate", "2M"]

    monkeypatch.setenv("VIDDL_YTDLP_ARGS", "--limit-rate 2M https://x.invalid/other")
    assert appdata.ytdlp_arguments() == []
    assert "contains a link" in appdata.ytdlp_arguments_problem()


def test_a_windows_path_keeps_its_backslashes(no_environment, monkeypatch):
    """Backslashes separate a path there; POSIX quoting would eat them.

    ``sys.platform`` is faked because the real one cannot be both, and the
    point is that ``C:\\Users\\me\\cookies.txt`` arrives intact rather than as
    ``C:Usersmecookies.txt``.
    """
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv(
        "VIDDL_YTDLP_ARGS", '--cookies "C:\\Users\\me\\cookies.txt" --limit-rate 2M'
    )

    assert appdata.ytdlp_arguments() == [
        "--cookies",
        r"C:\Users\me\cookies.txt",
        "--limit-rate",
        "2M",
    ]


def test_nothing_in_the_value_is_handed_to_a_shell(no_environment, monkeypatch):
    """It is a list of arguments, not a command line: no expansion, no splitting."""
    monkeypatch.setenv("VIDDL_YTDLP_ARGS", "--limit-rate=$(rm -rf /)")
    assert appdata.ytdlp_arguments() == ["--limit-rate=$(rm", "-rf", "/)"]


def test_the_doctor_reports_the_extra_options_like_the_other_variables(
    no_environment, monkeypatch
):
    monkeypatch.setenv("VIDDL_YTDLP_ARGS", "--limit-rate 2M")
    assert appdata.overrides() == [appdata.Override("VIDDL_YTDLP_ARGS", "--limit-rate 2M")]
    assert appdata.environment_report() == [
        "Environment:  VIDDL_YTDLP_ARGS=--limit-rate 2M (in use)"
    ]

    monkeypatch.setenv("VIDDL_YTDLP_ARGS", "-o/tmp/x")
    assert appdata.environment_report() == [
        "Environment:  VIDDL_YTDLP_ARGS=-o/tmp/x "
        "(ignored — sets --output, which Viddl decides)"
    ]


# ---------------------------------------------------------------------------
# downloads folder
# ---------------------------------------------------------------------------


def test_downloads_dir_honours_the_override(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDDL_DOWNLOAD_DIR", str(tmp_path / "Elsewhere"))
    assert appdata.downloads_dir() == tmp_path / "Elsewhere"
    assert (tmp_path / "Elsewhere").is_dir()


@needs_posix_desktop
def test_downloads_dir_reads_the_desktop_settings_file(no_overrides, tmp_path, monkeypatch):
    config = Path(os.environ["XDG_CONFIG_HOME"]) / "user-dirs.dirs"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        "# generated by the desktop\n"
        'XDG_DESKTOP_DIR="$HOME/Desktop"\n'
        'XDG_DOWNLOAD_DIR="$HOME/Videos"\n',
        encoding="utf-8",
    )
    folder = appdata.downloads_dir()
    assert folder == Path.home() / "Videos"
    assert folder.is_dir()


@needs_posix_desktop
def test_downloads_dir_accepts_a_literal_path_in_the_settings_file(
    no_overrides, tmp_path, monkeypatch
):
    target = tmp_path / "Shared" / "Incoming"
    config = Path(os.environ["XDG_CONFIG_HOME"]) / "user-dirs.dirs"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(f'XDG_DOWNLOAD_DIR="{target}"\n', encoding="utf-8")
    assert appdata.downloads_dir() == target


@needs_posix_desktop
def test_downloads_dir_ignores_a_settings_file_without_the_line(
    no_overrides, tmp_path, monkeypatch
):
    config = Path(os.environ["XDG_CONFIG_HOME"]) / "user-dirs.dirs"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("XDG_DESKTOP_DIR=\"$HOME/Desktop\"\n", encoding="utf-8")
    assert appdata.downloads_dir() == Path.home() / "Downloads"


def test_downloads_dir_uses_the_windows_location(no_overrides, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "Users" / "someone"))
    assert appdata.downloads_dir() == tmp_path / "Users" / "someone" / "Downloads"


def test_downloads_dir_falls_back_when_there_is_nowhere_to_write(
    tmp_path, monkeypatch
):
    """No writable Downloads folder: fall back inside Viddl's own folder."""
    blocker = tmp_path / "blocked"
    blocker.write_text("not a folder", encoding="utf-8")
    home = tmp_path / "app"
    monkeypatch.setenv("VIDDL_HOME", str(home))
    monkeypatch.setenv("VIDDL_DOWNLOAD_DIR", str(blocker / "Downloads"))

    assert appdata.downloads_dir() == home / "Downloads"
    assert (home / "Downloads").is_dir()


@needs_posix_permissions
def test_the_fallback_downloads_folder_is_owner_only(tmp_path, monkeypatch):
    """The last resort sits inside our own folder, so it gets the same treatment."""
    blocker = tmp_path / "blocked"
    blocker.write_text("not a folder", encoding="utf-8")
    home = tmp_path / "app"
    monkeypatch.setenv("VIDDL_HOME", str(home))
    monkeypatch.setenv("VIDDL_DOWNLOAD_DIR", str(blocker / "Downloads"))

    assert appdata.downloads_dir() == home / "Downloads"
    assert mode_of(home / "Downloads") == 0o700


# ---------------------------------------------------------------------------
# finding programs
# ---------------------------------------------------------------------------


@needs_posix_permissions
def test_which_uses_the_path_first(tmp_path, monkeypatch):
    shim = tmp_path / "bin"
    shim.mkdir()
    (shim / "mytool").write_text("#!/bin/sh\n", encoding="utf-8")
    (shim / "mytool").chmod(0o755)
    monkeypatch.setenv("PATH", str(shim))
    assert appdata.which("mytool") == shim / "mytool"


def test_which_also_looks_where_gui_apps_do_not(tmp_path, monkeypatch):
    """A launcher started by a browser often has a thin PATH."""
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    local_bin = Path.home() / ".local" / "bin"
    local_bin.mkdir(parents=True, exist_ok=True)
    tool = local_bin / "viddl-fake"
    tool.write_text("#!/bin/sh\n", encoding="utf-8")
    tool.chmod(0o755)
    assert appdata.which("viddl-fake") == tool


@needs_posix_permissions
def test_which_ignores_a_file_that_is_not_executable(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    local_bin = Path.home() / ".local" / "bin"
    local_bin.mkdir(parents=True, exist_ok=True)
    (local_bin / "not-executable").write_text("text", encoding="utf-8")
    assert appdata.which("not-executable") is None


def test_which_returns_nothing_for_an_unknown_program(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert appdata.which("definitely-not-a-real-program-xyz") is None

# ---------------------------------------------------------------------------
# how long to wait for the network (R11)
#
# Every network wait was a constant, so a slow link could not be given more
# patience and a fast failure could not be asked for. One variable now sets
# them all, and it is checked like the others.
# ---------------------------------------------------------------------------


def test_no_timeout_means_the_built_in_waits(no_environment):
    assert appdata.network_timeout() is None
    assert appdata.network_timeout_problem() == ""


def test_a_timeout_is_a_number_of_seconds(no_environment, monkeypatch):
    monkeypatch.setenv("VIDDL_TIMEOUT", "45")
    assert appdata.network_timeout() == 45.0
    monkeypatch.setenv("VIDDL_TIMEOUT", "45s")
    assert appdata.network_timeout() == 45.0
    monkeypatch.setenv("VIDDL_TIMEOUT", " 1.5 ")
    assert appdata.network_timeout() == 1.5


def test_a_wait_with_no_override_keeps_its_own_default(no_environment, monkeypatch):
    assert appdata.network_timeout_seconds(30) == 30
    monkeypatch.setenv("VIDDL_TIMEOUT", "45")
    assert appdata.network_timeout_seconds(30) == 45


@pytest.mark.parametrize(
    "value, reason",
    [
        ("", "empty"),
        ("   ", "empty"),
        ("--limit-rate", "looks like an option, not a number of seconds"),
        ("30\n60", "contains a control character"),
        ("fast", "not a number of seconds"),
        ("30x", "not a number of seconds"),
        ("0", "shorter than 1 second"),
        ("0.5", "shorter than 1 second"),
        ("-5", "looks like an option, not a number of seconds"),
        ("nan", "not a real number of seconds"),
        ("inf", "not a real number of seconds"),
        ("7200", "longer than 3600 seconds"),
    ],
)
def test_a_timeout_that_cannot_be_used_says_why(
    no_environment, monkeypatch, value, reason
):
    monkeypatch.setenv("VIDDL_TIMEOUT", value)
    assert appdata.network_timeout() is None
    assert appdata.network_timeout_problem() == reason
    assert appdata.network_timeout_seconds(30) == 30


def test_the_timeout_is_not_mistaken_for_a_folder(no_environment, monkeypatch):
    """The path check would have called a bare number "not a folder"."""
    monkeypatch.setenv("VIDDL_TIMEOUT", "45")
    assert appdata.overrides() == [appdata.Override("VIDDL_TIMEOUT", "45", "")]


def test_the_report_shows_the_timeout_like_every_other_variable(
    no_environment, monkeypatch
):
    monkeypatch.setenv("VIDDL_TIMEOUT", "45")
    assert appdata.environment_report() == ["Environment:  VIDDL_TIMEOUT=45 (in use)"]

    monkeypatch.setenv("VIDDL_TIMEOUT", "soon")
    assert appdata.environment_report() == [
        "Environment:  VIDDL_TIMEOUT=soon (ignored \u2014 not a number of seconds)"
    ]


def test_the_doctor_note_says_what_is_being_waited_for(no_environment, monkeypatch):
    never_set = appdata.timeout_note()
    assert "built-in waits" in never_set
    assert "VIDDL_TIMEOUT" in never_set

    monkeypatch.setenv("VIDDL_TIMEOUT", "45")
    assert appdata.timeout_note() == (
        "Network:      waiting 45s per network request (VIDDL_TIMEOUT)"
    )
    monkeypatch.setenv("VIDDL_TIMEOUT", "1.5")
    assert "1.5s" in appdata.timeout_note()
