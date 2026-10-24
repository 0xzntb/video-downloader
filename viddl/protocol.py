"""The ``viddl://`` hand-off between the browser extension and this app.

No server, no port, no account: the extension opens a ``viddl://`` URL and the
operating system launches Viddl with that URL as an argument, exactly the way
``mailto:`` opens a mail program.

The security model is written down in ``SECURITY.md``; the short version is that
any page may ask the operating system to launch this app, so nothing about a
hand-off may act on its own. A hand-off only ever *pre-fills* a link: the person
at the keyboard still chooses a quality, and that choice is what starts a
download. Everything that protects the command line lives in
:func:`viddl.util.safe_video_link` and in the ``--`` separator the download job
puts before the link.
"""

from __future__ import annotations

import json
import os
import shutil
import shlex
import stat
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional
from urllib.parse import parse_qs, unquote

from . import URL_SCHEME, __version__
from . import appdata
from .util import safe_video_link

# How long a burst of hand-offs is collapsed into one. A page can ask for a
# hand-off again and again, and one that arrives immediately after the last is
# far more likely to be a page in a loop than a person who meant it twice. This
# is a nuisance limit rather than a security control — every rule that actually
# matters (only ``http(s)`` links, ``--`` before the link, nothing downloaded
# without a chosen quality) is enforced regardless of how often a page asks.
HANDOFF_COOLDOWN_SECONDS = 3
_STAMP = "last-handoff"

_DESKTOP_FILE = "viddl-handler.desktop"
_APP_BUNDLE = "Viddl URL Handler.app"
_MACOS_LSREGISTER = (
    "/System/Library/Frameworks/CoreServices.framework/Frameworks/"
    "LaunchServices.framework/Support/lsregister"
)

# Written into the app bundle as Contents/MacOS/viddl-open. __LAUNCH__ is
# replaced with the already-quoted command that starts the terminal app.
_MACOS_RUNNER = '''#!/bin/sh
# macOS runs this when a viddl:// link is opened.
URL="$1"
[ -z "$URL" ] && exit 0
# The extension percent-encodes URLs, so single-quoting is safe; strip the
# one character that could still break out of the quotes.
URL=$(printf '%s' "$URL" | tr -d "'")
RUNNER="${TMPDIR:-/tmp}/viddl-run-$$.command"
LINE="__LAUNCH__ --uri '$URL'"
printf '#!/bin/sh\n%s\nrm -f "$0"\n' "$LINE" > "$RUNNER"
chmod +x "$RUNNER"
exec /usr/bin/open -a Terminal "$RUNNER"
'''


@dataclass
class SchemeRequest:
    """What a ``viddl://`` URL asked for."""

    url: str
    audio: bool = False


@dataclass
class RegistrationResult:
    ok: bool
    message: str
    steps: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------


def parse_scheme(raw: str) -> Optional[SchemeRequest]:
    """Understand every shape a ``viddl://`` hand-off can arrive in.

    ``viddl://download?url=<encoded>&audio=1``   (what the extension sends)
    ``viddl://download/<url>``
    ``viddl://<url>``
    ``viddl:<url>``
    """
    if not raw:
        return None
    text = raw.strip().strip('"')
    if not text.lower().startswith(f"{URL_SCHEME}:"):
        return None

    # Drop the scheme and any slashes that follow it. Everything after this is
    # either a query string or the target link itself.
    remainder = text[len(URL_SCHEME) + 1 :].lstrip("/")
    lowered = remainder.lower()

    # An optional "download" verb, so viddl://download/<url> works too.
    if lowered.startswith("download"):
        remainder = remainder[len("download") :].lstrip("/")
        lowered = remainder.lower()

    if remainder.startswith("?"):
        query = parse_qs(remainder.lstrip("?"))
        if not query.get("url"):
            return None
        audio = any(
            value.lower() in ("1", "true", "yes", "audio", "mp3")
            for value in query.get("audio", [])
        )
        # parse_qs has already percent-decoded the value for us, but that value
        # is whatever the page that opened the link chose to send. It has to
        # pass the same link check as anything typed by hand before it goes
        # anywhere near a command line.
        url = safe_video_link(query["url"][0])
        if url is None:
            return None
        return SchemeRequest(url=url, audio=audio)

    audio = False
    for marker in ("&audio=1", "?audio=1"):
        if lowered.endswith(marker):
            audio = True
            remainder = remainder[: -len(marker)]
            break

    # A link, or a bare site without its scheme ("www.example.com/watch?v=x"):
    # safe_video_link understands both and refuses everything else.
    url = safe_video_link(unquote(remainder))
    if url is None:
        return None
    return SchemeRequest(url=url, audio=audio)


def parse_scheme_url(raw: str) -> Optional[str]:
    """Just the video link from a ``viddl://`` URL."""
    request = parse_scheme(raw)
    return request.url if request else None


# ---------------------------------------------------------------------------
# how often a page may hand one over
# ---------------------------------------------------------------------------


def _stamp_path() -> Path:
    return appdata.data_dir() / _STAMP


def handoff_too_soon(
    seconds: int = HANDOFF_COOLDOWN_SECONDS,
    now: Optional[float] = None,
) -> bool:
    """Whether this hand-off followed the last one too closely to act on.

    A clock that jumped backwards, a stamp that cannot be read, or no stamp at
    all all answer "no": failing to keep track is not a reason to ignore the
    link someone clicked. The stamp is a plain timestamp in the app's own
    folder — no port, no socket, nothing a page can reach.
    """
    try:
        last = float(_stamp_path().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    moment = time.time() if now is None else now
    return 0 <= moment - last < seconds


def remember_handoff(now: Optional[float] = None) -> None:
    """Note that a hand-off was acted on, so a burst of them is visible."""
    try:
        path = _stamp_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{time.time() if now is None else now:.3f}\n", encoding="utf-8")
    except OSError:
        pass


def build_scheme_url(video_url: str, audio: bool = False) -> str:
    """Build the URL the browser extension opens."""
    from urllib.parse import quote

    suffix = "&audio=1" if audio else ""
    return f"{URL_SCHEME}://download?url={quote(video_url, safe='')}{suffix}"


# ---------------------------------------------------------------------------
# launching
# ---------------------------------------------------------------------------


def launch_command() -> List[str]:
    """The command that starts the terminal app on this machine."""
    argv0 = sys.argv[0] if sys.argv else ""
    if argv0:
        candidate = Path(argv0)
        if candidate.is_file() and candidate.name.lower().startswith("viddl"):
            return [str(candidate.resolve())]

    on_path = shutil.which("viddl")
    if on_path:
        return [on_path]

    # Last resort: we know we are a Python program, so we can be launched as one.
    return [sys.executable, "-m", "viddl"]


def _application_dir() -> Path:
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    if sys.platform == "darwin":
        return Path.home() / "Applications"
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "applications"


def _run_quietly(command: List[str], timeout: int = 20) -> tuple:
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    if completed.returncode != 0:
        return False, (completed.stderr or completed.stdout or "").strip()
    return True, (completed.stdout or "").strip()


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------


def register() -> RegistrationResult:
    """Teach the operating system that ``viddl://`` means this app."""
    if sys.platform.startswith("win"):
        return _register_windows()
    if sys.platform == "darwin":
        return _register_macos()
    return _register_linux()


def unregister() -> RegistrationResult:
    """Undo :func:`register`."""
    if sys.platform.startswith("win"):
        return _unregister_windows()
    if sys.platform == "darwin":
        return _unregister_macos()
    return _unregister_linux()


def _register_linux() -> RegistrationResult:
    steps: List[str] = []
    command = launch_command()
    applications = _application_dir()
    try:
        applications.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return RegistrationResult(False, f"Could not write to {applications}: {exc}")

    desktop_path = applications / _DESKTOP_FILE
    exec_line = " ".join(shlex.quote(part) for part in command)
    contents = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name=Viddl\n"
        "Comment=Download a video or its audio with Viddl\n"
        f"Exec={exec_line} --uri %u\n"
        "Terminal=true\n"
        "NoDisplay=true\n"
        "Categories=AudioVideo;Utility;Network;\n"
        f"MimeType=x-scheme-handler/{URL_SCHEME};\n"
        "StartupNotify=false\n"
    )
    try:
        desktop_path.write_text(contents, encoding="utf-8")
        os.chmod(desktop_path, 0o644)
    except OSError as exc:
        return RegistrationResult(False, f"Could not write {desktop_path}: {exc}")
    steps.append(f"Wrote {desktop_path}")

    ok, output = _run_quietly(
        ["xdg-mime", "default", _DESKTOP_FILE, f"x-scheme-handler/{URL_SCHEME}"]
    )
    steps.append("Ran xdg-mime default" if ok else f"xdg-mime unavailable: {output}")
    _run_quietly(["update-desktop-database", str(applications)])
    if not ok:
        # gio is the next most common way to set a default handler.
        ok2, output2 = _run_quietly(
            ["gio", "mime", f"x-scheme-handler/{URL_SCHEME}", _DESKTOP_FILE]
        )
        steps.append("Ran gio mime" if ok2 else f"gio unavailable: {output2}")
        ok = ok or ok2

    if not ok:
        return RegistrationResult(
            False,
            "The handler file was written, but this system has no tool to set it as "
            "the default automatically.\nRun this once by hand:\n"
            f"    xdg-mime default {_DESKTOP_FILE} x-scheme-handler/{URL_SCHEME}",
            steps,
        )
    return RegistrationResult(
        True,
        "Done — links from the browser extension will now open Viddl.",
        steps,
    )


def _unregister_linux() -> RegistrationResult:
    desktop_path = _application_dir() / _DESKTOP_FILE
    try:
        desktop_path.unlink(missing_ok=True)
    except OSError as exc:
        return RegistrationResult(False, f"Could not remove {desktop_path}: {exc}")
    _run_quietly(["update-desktop-database", str(_application_dir())])
    return RegistrationResult(True, "Viddl is no longer the handler for viddl:// links.")


def _register_macos() -> RegistrationResult:
    steps: List[str] = []
    bundle = _application_dir() / _APP_BUNDLE
    command = launch_command()
    try:
        (bundle / "Contents" / "MacOS").mkdir(parents=True, exist_ok=True)
        (bundle / "Contents" / "Resources").mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return RegistrationResult(False, f"Could not create {bundle}: {exc}")

    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>Viddl URL Handler</string>
    <key>CFBundleIdentifier</key><string>app.viddl.urlhandler</string>
    <key>CFBundleVersion</key><string>{__version__}</string>
    <key>CFBundleShortVersionString</key><string>{__version__}</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleExecutable</key><string>viddl-open</string>
    <key>LSUIElement</key><true/>
    <key>CFBundleURLTypes</key>
    <array>
        <dict>
            <key>CFBundleURLName</key><string>Viddl link</string>
            <key>CFBundleURLSchemes</key>
            <array><string>{URL_SCHEME}</string></array>
        </dict>
    </array>
</dict>
</plist>
"""
    runner = bundle / "Contents" / "MacOS" / "viddl-open"
    app_command = " ".join(shlex.quote(part) for part in command)
    # Terminal gets a throwaway .command file with the concrete command in it,
    # which sidesteps every layer of quoting AppleScript would add.
    runner_script = _MACOS_RUNNER.replace("__LAUNCH__", app_command)
    try:
        (bundle / "Contents" / "Info.plist").write_text(plist, encoding="utf-8")
        runner.write_text(runner_script, encoding="utf-8")
        os.chmod(runner, runner.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError as exc:
        return RegistrationResult(False, f"Could not write the app bundle: {exc}")
    steps.append(f"Created {bundle}")

    if Path(_MACOS_LSREGISTER).exists():
        ok, output = _run_quietly([_MACOS_LSREGISTER, "-f", str(bundle)])
        steps.append("Registered with LaunchServices" if ok else f"lsregister: {output}")
    else:
        steps.append("Open the bundle once so macOS notices it.")

    return RegistrationResult(
        True,
        "Done — links from the browser extension will now open Viddl in Terminal.",
        steps,
    )


def _unregister_macos() -> RegistrationResult:
    bundle = _application_dir() / _APP_BUNDLE
    if bundle.exists():
        # rmtree rather than shelling out, so a missing /bin/rm cannot leave
        # the bundle behind.
        shutil.rmtree(bundle, ignore_errors=True)
    _run_quietly([_MACOS_LSREGISTER, "-kill", "-r", "-d", "user"])
    return RegistrationResult(True, "Removed the Viddl URL handler.")


def _register_windows() -> RegistrationResult:
    try:
        import winreg
    except ImportError:  # pragma: no cover - not on Windows
        return RegistrationResult(False, "Windows registry access is unavailable.")

    command = launch_command()
    launcher = appdata.data_dir() / "viddl-open.cmd"
    quoted = " ".join(f'"{part}"' if " " in part else part for part in command)
    try:
        appdata.data_dir().mkdir(parents=True, exist_ok=True)
        body = (
            "@echo off\r\n"
            "rem Opened by the browser extension via the viddl:// protocol.\r\n"
            f'start "" {quoted} --uri %1\r\n'
        )
        launcher.write_text(body, encoding="utf-8")
    except OSError as exc:
        return RegistrationResult(False, f"Could not write {launcher}: {exc}")

    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{URL_SCHEME}") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f"URL:{URL_SCHEME} protocol")
            winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
            with winreg.CreateKey(key, r"shell\open\command") as command_key:
                winreg.SetValueEx(
                    command_key, "", 0, winreg.REG_SZ, f'"{launcher}" "%1"'
                )
    except OSError as exc:
        return RegistrationResult(False, f"Could not write the registry entry: {exc}")

    return RegistrationResult(
        True,
        "Done — links from the browser extension will now open Viddl.",
        [f"Wrote {launcher}", "Registered viddl:// under HKEY_CURRENT_USER"],
    )


def _unregister_windows() -> RegistrationResult:
    try:
        import winreg
    except ImportError:  # pragma: no cover - not on Windows
        return RegistrationResult(False, "Windows registry access is unavailable.")
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{URL_SCHEME}\shell\open\command")
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{URL_SCHEME}\shell\open")
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{URL_SCHEME}\shell")
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{URL_SCHEME}")
    except OSError as exc:
        return RegistrationResult(False, f"Could not remove the registry entry: {exc}")
    return RegistrationResult(True, "Removed the viddl:// handler.")


# ---------------------------------------------------------------------------
# the bundled browser extension
# ---------------------------------------------------------------------------


def extension_source_dir() -> Path:
    """The extension ships inside the package so it is always available."""
    return Path(__file__).resolve().parent / "browser_extension"


# Two browsers, one set of files. A copy is the same JavaScript with a different
# manifest in it, because that is the only part the two browsers disagree about:
# Chromium insists on a service worker where Firefox refuses one and runs an
# event page instead, and Firefox wants an add-on id where Chromium rejects the
# key that holds it (F15).
CHROMIUM = "chromium"
FIREFOX = "firefox"
BROWSERS = (CHROMIUM, FIREFOX)

_MANIFESTS = {CHROMIUM: "manifest.json", FIREFOX: "manifest.firefox.json"}


def _manifest_for(browser: str) -> str:
    """The manifest file a build of ``browser`` is given, or a refusal."""
    try:
        return _MANIFESTS[browser]
    except KeyError:
        raise ValueError(
            f"Unknown browser '{browser}'. Choose one of: {', '.join(BROWSERS)}."
        ) from None


def _extension_payload(browser: str) -> List[Path]:
    """Every file a build carries, relative to the extension folder.

    The manifests are left out: one of them is added back under the name
    ``manifest.json``, and the other is a build instruction rather than part of
    any build. That is also why the folder can be loaded unpacked as it is.
    """
    _manifest_for(browser)
    source = extension_source_dir()
    payload = []
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        if relative.parts[0] == "__pycache__":
            continue
        if relative.parent == Path(".") and relative.name.startswith("manifest"):
            continue
        payload.append(relative)
    return payload


def install_extension(
    destination: Optional[Path] = None, browser: str = CHROMIUM
) -> Path:
    """Copy one browser's build somewhere stable and return the folder to load."""
    source = extension_source_dir()
    if not source.is_dir():  # pragma: no cover - packaging problem
        raise FileNotFoundError(f"The bundled extension is missing from {source}")
    manifest = source / _manifest_for(browser)
    target = (
        Path(destination)
        if destination
        else appdata.data_dir() / "browser-extension" / browser
    )
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    for relative in _extension_payload(browser):
        copy_to = target / relative
        copy_to.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, copy_to)
    shutil.copyfile(manifest, target / "manifest.json")
    return target


def package_extension(
    browser: str = CHROMIUM, destination: Optional[Path] = None
) -> Path:
    """Write one browser's build as the zip a store accepts, and return it.

    Both stores take the same shape — ``manifest.json`` at the root of a zip —
    so a submission is this command and an upload rather than a folder the
    person has to zip themselves. Signing and review are still theirs to do.
    """
    source = extension_source_dir()
    manifest = source / _manifest_for(browser)
    try:
        version = json.loads(manifest.read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError):  # pragma: no cover - packaging problem
        version = __version__
    target = (
        Path(destination)
        if destination
        else appdata.data_dir()
        / "browser-extension"
        / f"viddl-{browser}-{version}.zip"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.write(manifest, "manifest.json")
        for relative in _extension_payload(browser):
            bundle.write(source / relative, relative.as_posix())
    return target
