"""The viddl:// hand-off and its operating-system registration."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from viddl import URL_SCHEME, appdata, protocol
from viddl.protocol import (
    CHROMIUM,
    FIREFOX,
    SchemeRequest,
    build_scheme_url,
    extension_source_dir,
    install_extension,
    launch_command,
    package_extension,
    parse_scheme,
    parse_scheme_url,
    register,
    unregister,
)


@pytest.mark.parametrize(
    "raw, url, audio",
    [
        (
            "viddl://download?url=https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3Dabc",
            "https://www.youtube.com/watch?v=abc",
            False,
        ),
        (
            "viddl://download?url=https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3Dabc&audio=1",
            "https://www.youtube.com/watch?v=abc",
            True,
        ),
        ("viddl://download/https://vimeo.com/12345", "https://vimeo.com/12345", False),
        ("viddl://https://vimeo.com/12345", "https://vimeo.com/12345", False),
        ("viddl:https://vimeo.com/12345", "https://vimeo.com/12345", False),
        ("viddl://www.vimeo.com/12345", "https://www.vimeo.com/12345", False),
        ("viddl://download/https://vimeo.com/12345&audio=1", "https://vimeo.com/12345", True),
        ("viddl:https://vimeo.com/12345?audio=1", "https://vimeo.com/12345", True),
    ],
)
def test_understands_every_link_shape(raw, url, audio):
    request = parse_scheme(raw)
    assert request == SchemeRequest(url=url, audio=audio)
    assert parse_scheme_url(raw) == url


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "https://www.youtube.com/watch?v=abc",
        "viddl://",
        "viddl://download",
        "not-a-link",
    ],
)
def test_rejects_anything_that_is_not_a_hand_off(raw):
    assert parse_scheme(raw) is None


@pytest.mark.parametrize(
    "raw",
    [
        # A page chooses every one of these, so none of them may come back as
        # something the downloader would read as an option.
        "viddl://download?url=--exec%3Dcalc.exe",
        "viddl://download?url=--config-locations%3D.%2Fevil.conf",
        "viddl://download?url=--paths%3D.%2Fout&audio=1",
        "viddl://download?url=javascript%3Aalert(1)",
        "viddl://download?url=file%3A%2F%2F%2Fetc%2Fpasswd",
        "viddl://--paths=./out",
        "viddl:--download-archive=./a.txt",
        "viddl://download/--exec=calc.exe",
    ],
)
def test_a_hand_off_carrying_an_option_is_refused(raw):
    assert parse_scheme(raw) is None
    assert parse_scheme_url(raw) is None


def test_a_hand_off_still_upgrades_a_bare_site():
    assert parse_scheme("viddl://www.vimeo.com/12345") == SchemeRequest(
        url="https://www.vimeo.com/12345", audio=False
    )


def test_build_scheme_url_round_trips():
    target = build_scheme_url("https://x.com/user/status/1", audio=True)
    assert target.startswith("viddl://download?url=")
    request = parse_scheme(target)
    assert request.url == "https://x.com/user/status/1"
    assert request.audio is True


# ---------------------------------------------------------------------------
# a page may ask more than once (S5)
# ---------------------------------------------------------------------------


def test_the_first_hand_off_is_never_refused():
    """Nothing to compare against means nothing to throttle."""
    assert protocol.handoff_too_soon(now=1_000_000.0) is False


def test_a_burst_of_hand_offs_counts_as_one():
    cooldown = protocol.HANDOFF_COOLDOWN_SECONDS
    protocol.remember_handoff(now=1_000_000.0)
    assert protocol.handoff_too_soon(now=1_000_000.0)
    assert protocol.handoff_too_soon(now=1_000_000.0 + cooldown - 0.1)
    # Once the cooldown has passed, the next one is a fresh ask again.
    assert not protocol.handoff_too_soon(now=1_000_000.0 + cooldown)


def test_the_stamp_lives_in_the_app_folder():
    protocol.remember_handoff(now=1_000_000.0)
    stamp = protocol._stamp_path()
    assert stamp.parent == appdata.data_dir()
    assert stamp.read_text(encoding="utf-8").strip() == "1000000.000"


def test_a_stamp_that_cannot_be_read_is_not_a_refusal():
    """Failing to keep track is not a reason to drop someone's click."""
    protocol._stamp_path().parent.mkdir(parents=True, exist_ok=True)
    for rubbish in ("not a number\n", "\n", ""):
        protocol._stamp_path().write_text(rubbish, encoding="utf-8")
        assert protocol.handoff_too_soon(now=1_000_000.0) is False


def test_a_clock_that_went_backwards_is_not_a_refusal():
    protocol.remember_handoff(now=1_000_000.0)
    assert protocol.handoff_too_soon(now=999_000.0) is False


def test_remembering_a_hand_off_never_raises(monkeypatch):
    def nowhere():
        raise OSError("read-only file system")

    monkeypatch.setattr(appdata, "data_dir", nowhere)
    protocol.remember_handoff()  # must not raise
    assert protocol.handoff_too_soon() is False


def test_launch_command_points_at_something_real():
    command = launch_command()
    assert command
    assert isinstance(command[0], str)


def test_bundled_extension_is_complete():
    source = extension_source_dir()
    assert source.is_dir()
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 3
    for relative in (
        manifest["background"]["service_worker"],
        manifest["action"]["default_popup"],
        *manifest["icons"].values(),
        *[entry for block in manifest["content_scripts"] for entry in block.get("js", [])],
        *[entry for block in manifest["content_scripts"] for entry in block.get("css", [])],
    ):
        assert (source / relative).is_file(), relative


def test_install_extension_copies_to_a_loadable_folder(tmp_path):
    target = install_extension(tmp_path / "out")
    assert (target / "manifest.json").is_file()
    assert (target / "icons" / "icon128.png").is_file()


def test_install_extension_replaces_an_older_copy(tmp_path):
    target = install_extension(tmp_path / "out")
    stale = target / "leftover-from-an-old-version.js"
    stale.write_text("old", encoding="utf-8")
    again = install_extension(tmp_path / "out")
    assert again == target
    assert not stale.exists(), "a stale file would confuse the browser"
    assert (target / "manifest.json").is_file()


def test_the_firefox_copy_gets_a_manifest_firefox_will_load(tmp_path):
    """Firefox has no extension service worker, so the build says "scripts" (F15)."""
    target = install_extension(tmp_path / "out", browser=FIREFOX)
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 3
    assert manifest["background"] == {"scripts": ["background.js"]}
    assert manifest["browser_specific_settings"]["gecko"]["id"]
    # The Firefox manifest is a build instruction, not a file anyone loads.
    assert not (target / "manifest.firefox.json").exists()
    assert (target / "background.js").is_file()


def test_the_two_builds_are_the_same_code(tmp_path):
    """One set of files, two manifests — nothing else may drift apart (F15)."""
    chromium = install_extension(tmp_path / "chrome", browser=CHROMIUM)
    firefox = install_extension(tmp_path / "firefox", browser=FIREFOX)
    chromium_files = {path.relative_to(chromium) for path in chromium.rglob("*") if path.is_file()}
    firefox_files = {path.relative_to(firefox) for path in firefox.rglob("*") if path.is_file()}
    assert chromium_files == firefox_files
    for relative in chromium_files - {Path("manifest.json")}:
        assert (chromium / relative).read_bytes() == (firefox / relative).read_bytes()
    assert (chromium / "manifest.json").read_bytes() != (firefox / "manifest.json").read_bytes()


def test_installing_one_browser_leaves_the_other_alone():
    chromium = install_extension(browser=CHROMIUM)
    firefox = install_extension(browser=FIREFOX)
    assert chromium != firefox
    assert chromium.is_dir() and firefox.is_dir()
    assert chromium.parent == firefox.parent
    # Installing again replaces only its own folder.
    assert install_extension(browser=FIREFOX) == firefox
    assert chromium.is_dir()


def test_a_browser_viddl_does_not_build_for_is_refused_at_the_source(tmp_path):
    with pytest.raises(ValueError) as caught:
        install_extension(tmp_path / "out", browser="netscape")
    assert "netscape" in str(caught.value)


def test_package_extension_writes_the_zip_a_store_accepts(tmp_path):
    bundle_path = package_extension(FIREFOX, tmp_path / "firefox.zip")
    with zipfile.ZipFile(bundle_path) as bundle:
        names = set(bundle.namelist())
        manifest = json.loads(bundle.read("manifest.json"))
        background = bundle.read("background.js")
    assert bundle_path.is_file()
    # The store installs whatever manifest.json is at the root of the zip.
    assert "manifest.json" in names
    assert "manifest.firefox.json" not in names
    assert "icons/icon128.png" in names
    assert manifest["background"] == {"scripts": ["background.js"]}
    assert b"document.createElement" in background


def test_a_packaged_zip_lands_beside_the_install_folders():
    bundle_path = package_extension(CHROMIUM)
    assert bundle_path.parent == appdata.data_dir() / "browser-extension"
    assert bundle_path.name.startswith("viddl-chromium-")
    assert bundle_path.suffix == ".zip"


def test_install_extension_reports_a_missing_source(tmp_path, monkeypatch):
    import viddl.protocol as protocol

    monkeypatch.setattr(protocol, "extension_source_dir", lambda: tmp_path / "nowhere")
    with pytest.raises(FileNotFoundError):
        protocol.install_extension(tmp_path / "out")


# ---------------------------------------------------------------------------
# macOS and Windows registration
#
# These paths cannot be exercised by hand on this machine, so the platform
# branch is simulated and the artifacts it produces are inspected directly.
# ---------------------------------------------------------------------------


class _FakeKey:
    def __init__(self, registry, path):
        self._registry = registry
        self.path = path

    def __enter__(self):
        return self.path

    def __exit__(self, *exc_info):
        return False


class FakeWinreg:
    """Just enough of the registry to record what Viddl would write."""

    HKEY_CURRENT_USER = "HKEY_CURRENT_USER"
    REG_SZ = "REG_SZ"

    def __init__(self):
        self.values = {}
        self.created = []
        self.deleted = []

    def CreateKey(self, parent, subkey=None):
        path = parent if subkey is None else f"{parent}\\{subkey}"
        self.created.append(path)
        return _FakeKey(self, path)

    def SetValueEx(self, key, name, reserved, kind, value):
        self.values[(str(key), name)] = value

    def DeleteKey(self, parent, subkey=None):
        self.deleted.append(parent if subkey is None else f"{parent}\\{subkey}")


def _macos_bundle() -> Path:
    return Path.home() / "Applications" / "Viddl URL Handler.app"


def test_macos_registration_writes_a_url_handler_bundle(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    result = register()

    bundle = _macos_bundle()
    plist = bundle / "Contents" / "Info.plist"
    runner = bundle / "Contents" / "MacOS" / "viddl-open"
    assert plist.is_file() and runner.is_file()
    assert os.access(runner, os.X_OK), "macOS only runs it if it is executable"

    plist_text = plist.read_text(encoding="utf-8")
    assert "CFBundleURLSchemes" in plist_text
    assert f"<string>{URL_SCHEME}</string>" in plist_text
    assert result.ok is True
    assert result.steps


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX shell")
def test_the_macos_runner_script_is_valid_shell(monkeypatch):
    """It cannot be run here, but it can at least be parsed."""
    monkeypatch.setattr(sys, "platform", "darwin")
    register()
    runner = _macos_bundle() / "Contents" / "MacOS" / "viddl-open"
    completed = subprocess.run(["sh", "-n", str(runner)], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    body = runner.read_text(encoding="utf-8")
    assert "--uri" in body
    # The link is single-quoted for the generated command file.
    assert "--uri '" in body
    # A guessable name written with a plain redirect can be pointed at somebody
    # else's file by a symlink, so the scratch script comes from mktemp.
    assert "mktemp" in body
    assert "$$" not in body


@pytest.mark.skipif(
    shutil.which("sh") is None or os.name == "nt",
    reason="needs a POSIX shell that can run the generated handler",
)
def test_a_hostile_uri_cannot_add_commands_to_the_macos_runner(tmp_path, monkeypatch):
    """The URI is data: a line break in it must not become another command.

    The real runner is run, with the real template, because the escaping is the
    thing being tested — a stand-in for the script would only test the stand-in.
    """
    recorder = tmp_path / "record.sh"
    recorder.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$@" > "$RECORD_FILE"\n', encoding="utf-8"
    )
    recorder.chmod(0o755)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(protocol, "launch_command", lambda: [str(recorder)])
    register()

    runner = _macos_bundle() / "Contents" / "MacOS" / "viddl-open"
    hostile = "viddl://download?url=ok\ntouch INJECTED\n"
    environment = dict(
        os.environ,
        TMPDIR=str(tmp_path),
        RECORD_FILE=str(tmp_path / "argv.txt"),
    )
    # /usr/bin/open is not on Linux, so this ends by failing to open Terminal —
    # after the generated script has been written, which is what the test reads.
    subprocess.run(
        ["sh", str(runner), hostile],
        cwd=str(tmp_path),
        env=environment,
        capture_output=True,
        check=False,
    )

    scripts = list(tmp_path.glob("viddl-run-*.command"))
    assert len(scripts) == 1, "the runner writes exactly one script"
    body = scripts[0].read_text(encoding="utf-8")
    # The shebang, the one launch line, and the cleanup: nothing the URI added.
    assert len(body.splitlines()) == 3
    assert "\ntouch" not in body
    assert stat.S_IMODE(scripts[0].stat().st_mode) == 0o700

    subprocess.run(["sh", str(scripts[0])], cwd=str(tmp_path), env=environment, check=False)

    assert not (tmp_path / "INJECTED").exists(), "the URI must not run anything"
    assert (tmp_path / "argv.txt").read_text(encoding="utf-8").splitlines() == [
        "--uri",
        "viddl://download?url=oktouchINJECTED",
    ]


def test_macos_unregistration_removes_the_bundle(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    register()
    assert _macos_bundle().exists()
    unregister()
    assert not _macos_bundle().exists()


def test_windows_registration_writes_the_registry_entry(monkeypatch):
    fake = FakeWinreg()
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(sys, "platform", "win32")
    # launch_command() has its own test; pinning it here keeps the standard
    # library out of the way (shutil.which reaches for _winapi on win32).
    monkeypatch.setattr(
        protocol, "launch_command", lambda: [r"C:\Program Files\Viddl\viddl.exe"]
    )
    result = register()

    root = rf"HKEY_CURRENT_USER\Software\Classes\{URL_SCHEME}"
    assert result.ok is True
    assert root in fake.created
    assert fake.values[(root, "")] == f"URL:{URL_SCHEME} protocol"
    assert fake.values[(root, "URL Protocol")] == ""

    command = fake.values[(rf"{root}\shell\open\command", "")]
    assert command == r'"C:\Program Files\Viddl\viddl.exe" --uri "%1"'
    # Nothing between the OS and Viddl: no batch file, no cmd.exe, and no
    # quoting rules of our own that a URI could walk out of.
    assert ".cmd" not in command.lower()
    assert "cmd.exe" not in command.lower()
    assert not (Path(os.environ["VIDDL_HOME"]) / "viddl-open.cmd").exists()


def test_a_windows_handler_never_registers_a_batch_file(monkeypatch):
    """The installer's launcher is a .cmd, and ShellExecute hands that to cmd.exe."""
    fake = FakeWinreg()
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        protocol,
        "launch_command",
        lambda: [r"C:\Users\me\AppData\Local\viddl\bin\viddl.cmd"],
    )
    register()

    root = rf"HKEY_CURRENT_USER\Software\Classes\{URL_SCHEME}"
    command = fake.values[(rf"{root}\shell\open\command", "")]
    assert ".cmd" not in command.lower()
    assert command == (
        subprocess.list2cmdline([sys.executable, "-m", "viddl", "--uri"]) + ' "%1"'
    )


# ---------------------------------------------------------------------------
# nothing between the operating system and Viddl (S6)
# ---------------------------------------------------------------------------


def _windows_argv(command_line: str) -> list:
    """Split a Windows command line the way the program receiving it does.

    There is no way to ask the standard library to run CreateProcess' rules
    backwards, so this is Microsoft's documented rule written out: a run of
    backslashes before a quote is halved, an odd one escapes the quote, and a
    quote only groups. It is the piece of the test that says what a URI can and
    cannot become once Windows has put it in place of ``%1``.
    """
    argv: list = []
    argument: list = []
    backslashes = 0
    quoted = False
    started = False
    for character in command_line:
        if character == "\\":
            backslashes += 1
            continue
        if character == '"':
            argument.append("\\" * (backslashes // 2))
            if backslashes % 2:
                argument.append('"')
            else:
                quoted = not quoted
            backslashes = 0
            started = True
            continue
        argument.append("\\" * backslashes)
        backslashes = 0
        if character in " \t" and not quoted:
            if started:
                argv.append("".join(argument))
                argument, started = [], False
            continue
        argument.append(character)
        started = True
    argument.append("\\" * backslashes)
    if started:
        argv.append("".join(argument))
    return argv


@pytest.mark.parametrize(
    "launcher",
    [
        [r"C:\Program Files\Viddl\viddl.exe"],
        [r"C:\Users\me\AppData\Local\viddl\bin\viddl.cmd"],
    ],
)
def test_the_windows_handler_leaves_one_quoted_data_position(launcher):
    """``%1`` is the only variable part, and it is quoted data (S6).

    The URI is written by the operating system, not by this program, so the
    handler must not carry a second thing a page could reach: one substitution
    point, quoted, at the end, and no batch syntax anywhere before it.
    """
    handler = protocol._windows_handler_command(launcher)

    prefix, marker, suffix = handler.partition("--uri")
    assert marker == "--uri"
    assert suffix == ' "%1"', "one quoted substitution point, and nothing after it"
    assert "%" not in prefix, "%* and %PATH% would need cmd.exe to expand"
    assert not any(word.lower() in ("cmd", "cmd.exe", "start") for word in prefix.split())


@given(st.text(max_size=80))
def test_a_hostile_uri_only_ever_becomes_arguments_to_viddl(uri):
    """Whatever the page put in the URI, Viddl's own argv is unchanged.

    A quote in the URI ends the quoted argument, so Windows may hand Viddl
    further arguments — but only to Viddl, whose parser refuses what it was not
    given. Nothing sits between the OS and the program to run a command, and
    the program and the option name are the parts substitution cannot reach.
    """
    launcher = r"C:\Program Files\Viddl\viddl.exe"
    handler = protocol._windows_handler_command([launcher])
    argv = _windows_argv(handler.replace("%1", uri))

    assert argv[0] == launcher
    assert argv[1] == "--uri"
    assert argv.count("--uri") == 1
    # Everything the URI added after that is data for that one option: even a
    # URI that ends the quotes cannot change the program being started.
    assert all(argument.lower() != "cmd.exe" for argument in argv[1:])


def test_a_link_is_url_shaped_before_it_crosses_to_the_handler():
    """``build_scheme_url`` encodes what a URL may not carry (S6).

    The extension builds the hand-off from whatever the page's link is, and the
    registry entry on the other side is one quoted argument: a raw quote, a
    line break or a space in that URL would be the end of it. Percent encoding
    is what keeps the hand-off URL-shaped whatever the link held.
    """
    hostile = 'https://example.com/watch?v=1"\r\ncalc.exe & echo hi'
    url = build_scheme_url(hostile)

    assert url.startswith("viddl://download?url=https%3A%2F%2Fexample.com%2Fwatch")
    for character in '"\' \r\n\t&|<>^':
        assert character not in url

    # And what comes back is a link or nothing, never a command.
    request = parse_scheme(url)
    assert request is None or request.url.startswith("https://example.com/watch")


def test_registering_removes_the_batch_file_an_older_version_wrote(monkeypatch):
    """An unreferenced script is still a script, and this one took a URI."""
    fake = FakeWinreg()
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(protocol, "launch_command", lambda: [r"C:\Viddl\viddl.exe"])

    legacy = Path(os.environ["VIDDL_HOME"]) / "viddl-open.cmd"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("@echo off\r\n", encoding="utf-8")

    register()
    assert not legacy.exists(), "the batch handler an older version wrote is gone"

    legacy.write_text("@echo off\r\n", encoding="utf-8")
    unregister()
    assert not legacy.exists()


def test_windows_unregistration_removes_the_keys(monkeypatch):
    fake = FakeWinreg()
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(sys, "platform", "win32")
    unregister()
    assert fake.deleted == [
        rf"HKEY_CURRENT_USER\Software\Classes\{URL_SCHEME}\shell\open\command",
        rf"HKEY_CURRENT_USER\Software\Classes\{URL_SCHEME}\shell\open",
        rf"HKEY_CURRENT_USER\Software\Classes\{URL_SCHEME}\shell",
        rf"HKEY_CURRENT_USER\Software\Classes\{URL_SCHEME}",
    ]


def test_windows_without_registry_access_says_so(monkeypatch):
    monkeypatch.setitem(sys.modules, "winreg", None)  # import fails
    monkeypatch.setattr(sys, "platform", "win32")
    result = register()
    assert result.ok is False
    assert "registry" in result.message.lower()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux desktop entry")
def test_register_writes_a_desktop_handler(tmp_path, monkeypatch):
    result = register()
    desktop = Path.home() / ".local" / "share" / "applications" / "viddl-handler.desktop"
    assert desktop.is_file(), "the desktop entry must be written"
    body = desktop.read_text(encoding="utf-8")
    assert "MimeType=x-scheme-handler/viddl;" in body
    assert "Terminal=true" in body
    assert "--uri %u" in body
    # Whether the default could be set depends on xdg-mime being installed, but
    # either way the user is told exactly what happened.
    assert result.message
    assert result.steps

    unregister()
    assert not desktop.exists()
