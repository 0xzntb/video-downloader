"""Finding, installing and verifying yt-dlp and ffmpeg."""

from __future__ import annotations

import hashlib
import http.server
import io
import os
import stat
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import zipfile
from datetime import date
from pathlib import Path
from typing import ClassVar

import pytest

from viddl import engine as engine_mod
from viddl.engine import (
    Engine,
    Tool,
    ToolError,
    _extract_member,
    _verified_download,
    digest_from_sums,
    download_to,
    expected_sha256,
    record_checksum,
    recorded_checksum,
    sha256_of,
)

needs_posix_exec = pytest.mark.skipif(
    sys.platform.startswith("win"), reason="POSIX shebang semantics"
)


def write_executable(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def tool_name(name: str) -> str:
    """The file name the engine looks for on this platform.

    Windows has no extensionless executables: Viddl asks for ``yt-dlp.exe`` and
    ``ffmpeg.exe``, so a test that writes ``yt-dlp`` is testing a file nothing
    will look for (R10).
    """
    if sys.platform.startswith("win") and name in ("yt-dlp", "ffmpeg"):
        return f"{name}.exe"
    return name


def make_shim(directory: Path, name: str = "yt-dlp", version: str = "2099.01.01") -> Path:
    return write_executable(directory / tool_name(name), f"#!/bin/sh\necho {version}\n")


# ---------------------------------------------------------------------------
# the checksum layer, stood in for so these tests stay offline
# ---------------------------------------------------------------------------

FAKE_PIN = "1" * 64


def as_download(body):
    """Adapt a fake download body to the real ``download_to`` signature.

    The installer now tells the downloader which checksum it expects, so a
    stand-in has to accept it; the interesting part of a test stays in ``body``.
    """

    def download(
        url, dest, on_progress=None, timeout=30, expected_sha256=None, part_path=None
    ):
        return body(url, dest, on_progress)

    return download


def patch_checksums(monkeypatch, *names, digest=FAKE_PIN) -> None:
    """Stand in for the checksums file an installer reads before downloading."""
    wanted = names or ("yt-dlp", "yt-dlp.exe")
    text = "".join(f"{digest}  {name}\n" for name in wanted)
    monkeypatch.setattr(engine_mod, "_fetch_text", lambda url, timeout=30: text)


def mirror(url: str, archive=None, pattern=None, **extra) -> dict:
    """A source the installer will accept, with a stand-in pinned checksum."""
    return {"url": url, "archive": archive, "pattern": pattern, "sha256": FAKE_PIN, **extra}


class FakeResponse:
    """Just enough of an HTTP response to stream bytes out of."""

    def __init__(self, payload: bytes):
        self._stream = io.BytesIO(payload)
        self.headers = {"Content-Length": str(len(payload))}

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def serving(monkeypatch, *payloads: bytes) -> list:
    """Serve ``payloads`` in turn from a patched ``urlopen``.

    Returns the list of URLs asked for, so a test can count the attempts.
    """
    asked: list = []

    def fake_urlopen(request, timeout=None):
        asked.append(getattr(request, "full_url", str(request)))
        index = min(len(asked) - 1, len(payloads) - 1)
        return FakeResponse(payloads[index])

    monkeypatch.setattr(engine_mod.urllib.request, "urlopen", fake_urlopen)
    return asked


def test_finds_yt_dlp_that_is_already_installed(tmp_path, monkeypatch):
    shim_dir = tmp_path / "bin"
    make_shim(shim_dir)
    monkeypatch.setenv("PATH", str(shim_dir) + os.pathsep + os.environ.get("PATH", ""))

    tool = Engine(home=tmp_path / "app").find_ytdlp()
    assert tool is not None
    assert tool.version == "2099.01.01"
    assert tool.command("--version")[-1] == "--version"


def test_finds_viddls_own_copy_first(tmp_path):
    home = tmp_path / "app"
    make_shim(home / "bin", "yt-dlp", "2000.01.01")
    tool = Engine(home=home).find_ytdlp()
    assert tool is not None
    assert tool.origin == "viddl"
    assert tool.version == "2000.01.01"


def test_broken_copy_is_ignored(tmp_path, monkeypatch):
    home = tmp_path / "app"
    # Exists, but does not work: must not be trusted.
    write_executable(Engine(home=home).ytdlp_path, "#!/bin/sh\nexit 3\n")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert Engine(home=home).find_ytdlp() is None


def test_install_downloads_verifies_and_runs(tmp_path, monkeypatch):
    requested: list = []

    def body(url, dest, on_progress):
        requested.append(url)
        write_executable(Path(dest), "#!/usr/bin/env python3\nprint('2099.05.05')\n")
        if on_progress:
            on_progress(10, 10)
        return Path(dest)

    monkeypatch.setattr(engine_mod, "download_to", as_download(body))
    patch_checksums(monkeypatch)

    engine = Engine(home=tmp_path / "app")
    tool = engine.install_ytdlp()
    assert tool.origin == "viddl"
    assert tool.version == "2099.05.05"
    assert requested[0].startswith("https://github.com/yt-dlp/yt-dlp/releases/")
    assert engine.ytdlp_path.is_file()
    if not sys.platform.startswith("win"):
        assert os.access(engine.ytdlp_path, os.X_OK)


@needs_posix_exec
def test_install_falls_back_to_our_own_python_when_the_shebang_fails(tmp_path, monkeypatch):
    def body(url, dest, on_progress):
        # No shebang: the kernel refuses to exec this directly.
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("print('2024.05.05')\n", encoding="utf-8")
        return dest

    monkeypatch.setattr(engine_mod, "download_to", as_download(body))
    patch_checksums(monkeypatch)
    engine = Engine(home=tmp_path / "app")
    tool = engine.install_ytdlp()
    assert tool.version == "2024.05.05"
    assert tool.prefix[0] == sys.executable
    assert tool.prefix[1] == str(engine.ytdlp_path)


def test_ensure_only_downloads_once(tmp_path, monkeypatch):
    calls = []

    def body(url, dest, on_progress):
        calls.append(url)
        write_executable(Path(dest), "#!/usr/bin/env python3\nprint('1.2.3')\n")
        return Path(dest)

    monkeypatch.setattr(engine_mod, "download_to", as_download(body))
    patch_checksums(monkeypatch)
    engine = Engine(home=tmp_path / "app")
    first = engine.ensure_ytdlp()
    second = engine.ensure_ytdlp()
    assert first is second
    assert len(calls) == 1


def test_ensure_refuses_to_download_when_asked_not_to(tmp_path):
    with pytest.raises(ToolError) as caught:
        Engine(home=tmp_path / "app").ensure_ytdlp(allow_download=False)
    assert "not installed" in caught.value.message


def test_ffmpeg_sources_are_https_and_self_consistent():
    for source in Engine().ffmpeg_sources():
        assert source["url"].startswith("https://")
        if source["archive"]:
            assert source["pattern"]
        # Every mirror has to say how its bytes can be checked: a source with
        # neither a published checksums list nor a pinned hash is refused at
        # install time, so it has no business being listed.
        assert source.get("sums_url") or source.get("sha256")
        if source.get("sums_url"):
            assert source["sums_url"].startswith("https://")


def test_extract_member_from_tar_xz(tmp_path):
    archive = tmp_path / "ffmpeg.tar.xz"
    payload = b"#!fake-ffmpeg-binary"
    with tarfile.open(archive, "w:xz") as tf:
        info = tarfile.TarInfo("ffmpeg-build/bin/ffmpeg")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
        other = tarfile.TarInfo("ffmpeg-build/README.md")
        other.size = 3
        tf.addfile(other, io.BytesIO(b"hi\n"))

    dest = tmp_path / "out" / "ffmpeg"
    _extract_member(archive, r"bin/ffmpeg$", dest)
    assert dest.read_bytes() == payload
    assert os.access(dest, os.X_OK)


def test_extract_member_from_zip(tmp_path):
    archive = tmp_path / "ffmpeg.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("ffmpeg-build/bin/ffmpeg.exe", b"MZ-fake")
        zf.writestr("ffmpeg-build/LICENSE", b"text")
    dest = tmp_path / "out" / "ffmpeg.exe"
    _extract_member(archive, r"bin/ffmpeg\.exe$", dest)
    assert dest.read_bytes() == b"MZ-fake"


def test_extract_member_reports_a_bundle_without_the_program(tmp_path):
    archive = tmp_path / "ffmpeg.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("ffmpeg-build/README", b"nope")
    with pytest.raises(ToolError):
        _extract_member(archive, r"bin/ffmpeg$", tmp_path / "ffmpeg")


def _patched_sources(monkeypatch, engine, sources):
    monkeypatch.setattr(engine, "ffmpeg_sources", lambda: sources)


def test_install_ffmpeg_tries_the_next_mirror(tmp_path, monkeypatch):
    attempts: list = []

    def body(url, dest, on_progress):
        attempts.append(url)
        if len(attempts) == 1:
            raise ToolError("mirror is down")
        write_executable(Path(dest), "#!/usr/bin/env python3\nprint('ffmpeg version 7.0-fake')\n")
        return Path(dest)

    monkeypatch.setattr(engine_mod, "download_to", as_download(body))
    engine = Engine(home=tmp_path / "app")
    _patched_sources(
        monkeypatch,
        engine,
        [mirror("https://mirror-one/ffmpeg"), mirror("https://mirror-two/ffmpeg")],
    )
    tool = engine.install_ffmpeg()
    assert attempts == ["https://mirror-one/ffmpeg", "https://mirror-two/ffmpeg"]
    assert tool.origin == "viddl"
    assert "7.0-fake" in tool.version


def test_install_ffmpeg_survives_an_html_error_page(tmp_path, monkeypatch):
    """A mirror answering with a web page instead of an archive must not crash."""
    attempts: list = []

    def body(url, dest, on_progress):
        attempts.append(url)
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if len(attempts) == 1:
            dest.write_bytes(b"<html>502 Bad Gateway</html>")
        else:
            write_executable(dest, "#!/usr/bin/env python3\nprint('ffmpeg version 8.0-fake')\n")
        return dest

    monkeypatch.setattr(engine_mod, "download_to", as_download(body))
    engine = Engine(home=tmp_path / "app")
    _patched_sources(
        monkeypatch,
        engine,
        [
            mirror("https://mirror-one/ffmpeg.tar.xz", archive="tar.xz", pattern=r"bin/ffmpeg$"),
            mirror("https://mirror-two/ffmpeg"),
        ],
    )
    tool = engine.install_ffmpeg()
    assert len(attempts) == 2
    assert "8.0-fake" in tool.version


def test_install_ffmpeg_gives_up_with_advice(tmp_path, monkeypatch):
    def always_fails(url, dest, on_progress):
        raise ToolError("no route to host")

    monkeypatch.setattr(engine_mod, "download_to", as_download(always_fails))
    patch_checksums(monkeypatch, "ffmpeg-master-latest-linux64-gpl.tar.xz")
    with pytest.raises(ToolError) as caught:
        Engine(home=tmp_path / "app").install_ffmpeg()
    assert "ffmpeg could not be downloaded" in caught.value.message
    assert "apt install ffmpeg" in (caught.value.hint or "")


def test_download_to_streams_and_replaces_the_part_file(tmp_path):
    source = tmp_path / "source.bin"
    source.write_bytes(b"x" * 5000)
    dest = tmp_path / "deep" / "dest.bin"
    seen: list = []

    download_to(source.as_uri(), dest, on_progress=lambda done, total: seen.append((done, total)))

    assert dest.read_bytes() == b"x" * 5000
    assert not (dest.parent / "dest.bin.part").exists()
    assert seen and seen[-1][0] == 5000


def test_download_to_reports_a_missing_file_clearly(tmp_path):
    with pytest.raises(ToolError) as caught:
        download_to((tmp_path / "nope.bin").as_uri(), tmp_path / "dest.bin")
    assert caught.value.friendly
    assert not (tmp_path / "dest.bin.part").exists()


def test_report_lists_the_tools(tmp_path):
    lines = "\n".join(Engine(home=tmp_path / "app").report())
    assert "Viddl" in lines
    assert "yt-dlp" in lines
    assert "ffmpeg" in lines
    assert "Downloads" in lines


def test_ffmpeg_location_args_only_for_viddls_own_copy(tmp_path):
    engine = Engine(home=tmp_path / "app")
    assert engine.ffmpeg_location_args() == []

    write_executable(
        engine.ffmpeg_path,
        "#!/bin/sh\necho 'ffmpeg version 7.0-fake'\n",
    )
    assert engine.ffmpeg_location_args() == ["--ffmpeg-location", str(engine.bin_dir)]


def make_ffmpeg_shim(directory: Path, version: str = "7.0-fake") -> Path:
    return write_executable(
        directory / tool_name("ffmpeg"), f"#!/bin/sh\necho 'ffmpeg version {version}'\n"
    )


def no_system_tools(monkeypatch):
    """Pretend nothing is on PATH or in the usual GUI-only places."""
    monkeypatch.setattr(engine_mod.appdata, "which", lambda name: None)


# ---------------------------------------------------------------------------
# ffmpeg discovery
# ---------------------------------------------------------------------------


def test_find_ffmpeg_prefers_viddls_own_copy(tmp_path, monkeypatch):
    home = tmp_path / "app"
    make_ffmpeg_shim(home / "bin", "9.9-viddl")
    make_ffmpeg_shim(tmp_path / "usr-bin", "1.1-system")
    monkeypatch.setenv("PATH", str(tmp_path / "usr-bin"))

    tool = Engine(home=home).find_ffmpeg()
    assert tool is not None
    assert tool.origin == "viddl"
    assert tool.version == "9.9-viddl"


def test_find_ffmpeg_falls_back_to_the_one_on_the_computer(tmp_path, monkeypatch):
    make_ffmpeg_shim(tmp_path / "usr-bin", "5.5-system")
    monkeypatch.setenv("PATH", str(tmp_path / "usr-bin"))

    tool = Engine(home=tmp_path / "app").find_ffmpeg()
    assert tool is not None
    assert tool.origin == "system"
    assert tool.origin_text == "found on your computer"


def test_find_ffmpeg_honours_the_environment_override(tmp_path, monkeypatch):
    elsewhere = make_ffmpeg_shim(tmp_path / "somewhere-else", "6.6-custom")
    monkeypatch.setenv("VIDDL_FFMPEG", str(elsewhere))
    no_system_tools(monkeypatch)

    tool = Engine(home=tmp_path / "app").find_ffmpeg()
    assert tool is not None
    assert tool.version == "6.6-custom"
    assert tool.path == elsewhere.resolve()


def test_a_tool_from_the_environment_is_not_reported_as_viddls_own(
    tmp_path, monkeypatch
):
    """``--doctor`` has to say where the program really came from (S6)."""
    elsewhere = make_shim(tmp_path / "somewhere-else", "yt-dlp", "4.4-mine")
    monkeypatch.setenv("VIDDL_YTDLP", str(elsewhere))
    no_system_tools(monkeypatch)

    engine = Engine(home=tmp_path / "app")
    tool = engine.find_ytdlp()
    assert tool.origin == "environment"
    assert tool.version == "4.4-mine"

    report = "\n".join(engine.report())
    assert "the copy you pointed Viddl at" in report
    assert "downloaded by Viddl" not in report


def test_an_option_shaped_tool_override_is_refused(tmp_path, monkeypatch):
    """The same lesson as the hand-off: an option is not a program."""
    monkeypatch.setenv("VIDDL_YTDLP", "--exec=calc.exe")
    no_system_tools(monkeypatch)

    engine = Engine(home=tmp_path / "app")
    assert engine.find_ytdlp() is None
    report = "\n".join(engine.report())
    assert "ignored — looks like an option, not a path" in report
    assert "not installed" in report


def test_a_broken_ffmpeg_is_not_trusted(tmp_path, monkeypatch):
    home = tmp_path / "app"
    write_executable(Engine(home=home).ffmpeg_path, "#!/bin/sh\nexit 1\n")
    no_system_tools(monkeypatch)
    assert Engine(home=home).find_ffmpeg() is None


def test_ffmpeg_without_a_version_line_is_still_usable(tmp_path, monkeypatch):
    no_system_tools(monkeypatch)
    write_executable(
        Engine(home=tmp_path / "app").ffmpeg_path, "#!/bin/sh\necho ready\n"
    )
    tool = Engine(home=tmp_path / "app").find_ffmpeg()
    assert tool is not None
    assert tool.version == ""


# ---------------------------------------------------------------------------
# ffmpeg setup
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# updating the engine, and which copy wins (R4)
#
# Viddl used to fetch yt-dlp once and keep whatever it got, in a fixed order with
# its own copy first — so the copy it fetched could shadow a newer one the user
# installed afterwards, and nothing could replace it.
# ---------------------------------------------------------------------------


def test_the_newer_copy_wins_even_when_it_is_not_viddls(tmp_path, monkeypatch):
    home = tmp_path / "app"
    make_shim(home / "bin", "yt-dlp", "2020.01.01")
    shim_dir = tmp_path / "bin"
    make_shim(shim_dir, "yt-dlp", "2099.01.01")
    monkeypatch.setenv("PATH", str(shim_dir) + os.pathsep + os.environ.get("PATH", ""))

    tool = Engine(home=home).find_ytdlp()

    assert tool is not None
    assert tool.origin == "system"
    assert tool.version == "2099.01.01"


def test_an_older_copy_does_not_displace_a_newer_one(tmp_path, monkeypatch):
    home = tmp_path / "app"
    make_shim(home / "bin", "yt-dlp", "2099.01.01")
    shim_dir = tmp_path / "bin"
    make_shim(shim_dir, "yt-dlp", "2020.01.01")
    monkeypatch.setenv("PATH", str(shim_dir) + os.pathsep + os.environ.get("PATH", ""))

    tool = Engine(home=home).find_ytdlp()

    assert tool is not None
    assert tool.origin == "viddl"
    assert tool.version == "2099.01.01"


def test_an_undated_copy_never_displaces_a_release(tmp_path, monkeypatch):
    """Nothing here can tell whether ``nightly`` is newer, so it is not assumed."""
    home = tmp_path / "app"
    make_shim(home / "bin", "yt-dlp", "2099.01.01")
    shim_dir = tmp_path / "bin"
    make_shim(shim_dir, "yt-dlp", "nightly")
    monkeypatch.setenv("PATH", str(shim_dir) + os.pathsep + os.environ.get("PATH", ""))

    tool = Engine(home=home).find_ytdlp()

    assert tool is not None
    assert tool.origin == "viddl"


def test_a_copy_the_environment_names_wins_even_when_it_is_older(tmp_path, monkeypatch):
    """Pointing at one is an instruction, not a default."""
    home = tmp_path / "app"
    make_shim(home / "bin", "yt-dlp", "2099.01.01")
    mine = make_shim(tmp_path / "mine", "yt-dlp", "2000.01.01")
    monkeypatch.setenv("VIDDL_YTDLP", str(mine))
    no_system_tools(monkeypatch)

    tool = Engine(home=home).find_ytdlp()

    assert tool is not None
    assert tool.origin == "environment"
    assert tool.version == "2000.01.01"


def test_update_fetches_the_release_again(tmp_path, monkeypatch):
    versions = iter(["2099.05.05", "2099.06.06"])

    def body(url, dest, on_progress):
        write_executable(Path(dest), f"#!/bin/sh\necho {next(versions)}\n")
        return Path(dest)

    monkeypatch.setattr(engine_mod, "download_to", as_download(body))
    patch_checksums(monkeypatch)
    no_system_tools(monkeypatch)
    engine = Engine(home=tmp_path / "app")

    engine.install_ytdlp()
    before, after = engine.update_ytdlp()

    assert before is not None and before.version == "2099.05.05"
    assert after.version == "2099.06.06"
    assert after.origin == "viddl"
    # The recorded hash follows the bytes that are there now, or the doctor would
    # report every update as a tampered copy.
    assert recorded_checksum(engine.ytdlp_path) == sha256_of(engine.ytdlp_path)


def test_update_reports_the_copy_it_replaces_not_the_one_in_use(tmp_path, monkeypatch):
    """The old copy was 2023; saying "already the newest" about it would be a lie."""
    home = tmp_path / "app"
    make_shim(home / "bin", "yt-dlp", "2023.01.06")
    shim_dir = tmp_path / "bin"
    make_shim(shim_dir, "yt-dlp", "2099.01.01")
    monkeypatch.setenv("PATH", str(shim_dir) + os.pathsep + os.environ.get("PATH", ""))

    def body(url, dest, on_progress):
        write_executable(Path(dest), "#!/bin/sh\necho 2099.06.06\n")
        return Path(dest)

    monkeypatch.setattr(engine_mod, "download_to", as_download(body))
    patch_checksums(monkeypatch)
    engine = Engine(home=home)
    assert engine.find_ytdlp().origin == "system"  # the newer copy runs today

    before, after = engine.update_ytdlp()

    assert before is not None and before.version == "2023.01.06"
    assert after.version == "2099.06.06"


def test_update_on_a_fresh_install_has_nothing_before_it(tmp_path, monkeypatch):
    def body(url, dest, on_progress):
        write_executable(Path(dest), "#!/bin/sh\necho 2099.05.05\n")
        return Path(dest)

    monkeypatch.setattr(engine_mod, "download_to", as_download(body))
    patch_checksums(monkeypatch)
    no_system_tools(monkeypatch)

    before, after = Engine(home=tmp_path / "app").update_ytdlp()

    assert before is None
    assert after.version == "2099.05.05"


def test_update_ffmpeg_fetches_again(tmp_path, monkeypatch):
    calls: list = []

    def body(url, dest, on_progress):
        calls.append(url)
        write_executable(Path(dest), "#!/bin/sh\necho 'ffmpeg version 7.0-fake'\n")
        return Path(dest)

    monkeypatch.setattr(engine_mod, "download_to", as_download(body))
    no_system_tools(monkeypatch)
    engine = Engine(home=tmp_path / "app")
    _patched_sources(monkeypatch, engine, [mirror("https://mirror/ffmpeg")])

    engine.install_ffmpeg()
    before, after = engine.update_ffmpeg()

    assert len(calls) == 2
    assert before is not None and before.version == "7.0-fake"
    assert after.version == "7.0-fake"


def test_ensure_ffmpeg_uses_the_copy_that_is_already_there(tmp_path, monkeypatch):
    home = tmp_path / "app"
    make_ffmpeg_shim(home / "bin", "8.8-viddl")
    no_system_tools(monkeypatch)

    def must_not_download(*args, **kwargs):
        raise AssertionError("ffmpeg was already installed")

    monkeypatch.setattr(engine_mod, "download_to", must_not_download)
    engine = Engine(home=home)
    first = engine.ensure_ffmpeg()
    assert first.version == "8.8-viddl"
    assert engine.ensure_ffmpeg() is first


def test_ensure_ffmpeg_downloads_once_and_then_remembers(tmp_path, monkeypatch):
    calls: list = []

    def body(url, dest, on_progress):
        calls.append(url)
        write_executable(Path(dest), "#!/usr/bin/env python3\nprint('ffmpeg version 7.0-fake')\n")
        return Path(dest)

    monkeypatch.setattr(engine_mod, "download_to", as_download(body))
    no_system_tools(monkeypatch)

    engine = Engine(home=tmp_path / "app")
    _patched_sources(monkeypatch, engine, [mirror("https://mirror/ffmpeg")])
    first = engine.ensure_ffmpeg()
    second = engine.ensure_ffmpeg()
    assert first is second
    assert len(calls) == 1


def test_ensure_ffmpeg_says_what_it_is_doing(tmp_path, monkeypatch):
    messages: list = []

    def body(url, dest, on_progress):
        write_executable(Path(dest), "#!/usr/bin/env python3\nprint('ffmpeg version 7.0-fake')\n")
        return Path(dest)

    monkeypatch.setattr(engine_mod, "download_to", as_download(body))
    no_system_tools(monkeypatch)

    engine = Engine(home=tmp_path / "app")
    _patched_sources(monkeypatch, engine, [mirror("https://mirror/ffmpeg")])
    engine.ensure_ffmpeg(on_status=messages.append)
    assert messages
    assert "ffmpeg" in messages[0]
    assert "one time" in messages[0]


def test_ensure_ffmpeg_refuses_to_download_when_asked_not_to(tmp_path, monkeypatch):
    no_system_tools(monkeypatch)
    with pytest.raises(ToolError) as caught:
        Engine(home=tmp_path / "app").ensure_ffmpeg(allow_download=False)
    assert "ffmpeg is needed" in caught.value.message
    assert "Set up ffmpeg" in (caught.value.hint or "")


def test_two_threads_asking_for_ffmpeg_install_it_once(tmp_path, monkeypatch):
    """A background warm-up and a first download can race for the same file."""
    calls: list = []

    def body(url, dest, on_progress):
        calls.append(url)
        time.sleep(0.05)
        write_executable(Path(dest), "#!/usr/bin/env python3\nprint('ffmpeg version 7.0-fake')\n")
        return Path(dest)

    monkeypatch.setattr(engine_mod, "download_to", as_download(body))
    no_system_tools(monkeypatch)

    engine = Engine(home=tmp_path / "app")
    _patched_sources(monkeypatch, engine, [mirror("https://mirror/ffmpeg")])
    gate = threading.Barrier(2)
    results: list = []

    def ask():
        gate.wait()
        results.append(engine.ensure_ffmpeg())

    threads = [threading.Thread(target=ask) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(calls) == 1
    assert results[0] is results[1]
    assert results[0].version == "7.0-fake"


def test_install_ffmpeg_skips_a_mirror_that_gives_a_broken_program(tmp_path, monkeypatch):
    attempts: list = []
    messages: list = []

    def body(url, dest, on_progress):
        attempts.append(url)
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if len(attempts) == 1:
            write_executable(dest, "#!/bin/sh\nexit 9\n")
        else:
            write_executable(dest, "#!/usr/bin/env python3\nprint('ffmpeg version 7.1-good')\n")
        return dest

    monkeypatch.setattr(engine_mod, "download_to", as_download(body))
    engine = Engine(home=tmp_path / "app")
    _patched_sources(
        monkeypatch,
        engine,
        [mirror("https://mirror-one/ffmpeg"), mirror("https://mirror-two/ffmpeg")],
    )
    tool = engine.install_ffmpeg(on_status=messages.append)
    assert len(attempts) == 2
    assert "7.1-good" in tool.version
    assert any("mirror" in message for message in messages)


def test_install_ffmpeg_keeps_the_reason_it_gave_up(tmp_path, monkeypatch):
    def always_broken(url, dest, on_progress):
        write_executable(Path(dest), "#!/bin/sh\nexit 9\n")
        return Path(dest)

    monkeypatch.setattr(engine_mod, "download_to", as_download(always_broken))
    engine = Engine(home=tmp_path / "app")
    _patched_sources(monkeypatch, engine, [mirror("https://m/ffmpeg")])
    with pytest.raises(ToolError) as caught:
        engine.install_ffmpeg()
    assert "did not run" in caught.value.raw


# ---------------------------------------------------------------------------
# what the doctor screen shows
# ---------------------------------------------------------------------------


def test_report_says_when_nothing_is_installed_yet(tmp_path, monkeypatch):
    no_system_tools(monkeypatch)
    monkeypatch.setattr(engine_mod.appdata, "downloads_dir", lambda: tmp_path)
    lines = "\n".join(Engine(home=tmp_path / "app").report())
    assert lines.count("not installed") == 2


def test_report_survives_a_downloads_folder_it_cannot_work_out(tmp_path, monkeypatch):
    def no_idea():
        raise OSError("no home directory")

    monkeypatch.setattr(engine_mod.appdata, "downloads_dir", no_idea)
    lines = "\n".join(Engine(home=tmp_path / "app").report())
    assert "(unknown)" in lines


def test_report_copes_with_a_tool_that_has_no_version(tmp_path, monkeypatch):
    engine = Engine(home=tmp_path / "app")
    monkeypatch.setattr(engine, "find_ytdlp", lambda: Tool("yt-dlp", ["yt-dlp"], "system"))
    monkeypatch.setattr(engine, "find_ffmpeg", lambda: Tool("ffmpeg", ["ffmpeg"], "system", version="7.0"))
    lines = "\n".join(engine.report())
    assert "yt-dlp:" in lines
    assert "7.0" in lines
    assert "found on your computer" in lines


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# how old the engine is (R4)
# ---------------------------------------------------------------------------


def test_a_release_version_names_its_date():
    assert engine_mod.version_date("2026.08.19") == date(2026, 8, 19)
    assert engine_mod.version_date("2026.8.19.1") == date(2026, 8, 19)
    assert engine_mod.version_date("2026.02.30") is None  # no such day


@pytest.mark.parametrize("version", ["nightly", "dev", "", "master"])

def test_a_build_without_a_date_has_no_age(version):
    """Viddl says nothing rather than guessing at an undated build."""
    assert engine_mod.engine_age_days(version, date(2026, 10, 17)) is None
    assert engine_mod.staleness_note(version, date(2026, 10, 17)) == ""


def test_a_release_is_dated_from_its_version():
    assert engine_mod.engine_age_days("2026.08.19", date(2026, 10, 17)) == 59
    assert engine_mod.engine_age_days("2026.10.01", date(2026, 10, 17)) == 16


def test_a_version_dated_in_the_future_is_not_called_fresh():
    assert engine_mod.engine_age_days("2030.01.01", date(2026, 10, 17)) is None


def test_the_note_appears_only_once_the_engine_is_old():
    assert engine_mod.staleness_note("2026.10.01", date(2026, 10, 17)) == ""
    note = engine_mod.staleness_note("2026.08.19", date(2026, 10, 17))
    assert "59 days old" in note
    assert "viddl --update" in note


def test_the_doctor_says_when_the_engine_is_old(tmp_path, monkeypatch):
    home = tmp_path / "app"
    make_shim(home / "bin", "yt-dlp", "2020.01.01")
    no_system_tools(monkeypatch)

    report = "\n".join(Engine(home=home).report())

    assert "days old — run `viddl --update`" in report


def test_the_doctor_keeps_quiet_while_the_engine_is_current(tmp_path, monkeypatch):
    home = tmp_path / "app"
    make_shim(home / "bin", "yt-dlp", "2099.01.01")
    no_system_tools(monkeypatch)

    report = "\n".join(Engine(home=home).report())

    assert "days old" not in report
    assert "2020" not in report


def test_origin_text_is_plain_language():
    assert Tool("yt-dlp", [], "system").origin_text == "found on your computer"
    # A copy someone else pointed us at is not ours to report as ours (S6).
    assert Tool("yt-dlp", [], "environment").origin_text == (
        "the copy you pointed Viddl at"
    )
    assert Tool("yt-dlp", [], "viddl").origin_text == "downloaded by Viddl"
    assert Tool("yt-dlp", [], "python").origin_text == "installed as a Python package"
    assert Tool("yt-dlp", [], "mystery").origin_text == "mystery"


def test_a_tool_error_without_a_hint_is_just_its_message():
    assert ToolError("boom").friendly == "boom"
    assert ToolError("boom", "try this").friendly == "boom\ntry this"


def test_platform_helpers_follow_sys_platform(monkeypatch):
    monkeypatch.setattr(engine_mod.sys, "platform", "win32")
    assert engine_mod.is_windows() and not engine_mod.is_macos()
    monkeypatch.setattr(engine_mod.sys, "platform", "darwin")
    assert engine_mod.is_macos() and not engine_mod.is_windows()
    monkeypatch.setattr(engine_mod.sys, "platform", "linux")
    assert not engine_mod.is_windows() and not engine_mod.is_macos()


def test_apple_silicon_offers_its_own_build_and_the_intel_one(monkeypatch):
    """The Intel build runs under Rosetta; on Apple silicon it is the fallback."""
    monkeypatch.setattr(engine_mod.sys, "platform", "darwin")
    monkeypatch.setattr(engine_mod.platform, "machine", lambda: "arm64")

    sources = Engine().ffmpeg_sources()

    assert "darwin-arm64" in sources[0]["url"]
    assert not sources[0].get("needs_rosetta")
    assert "darwin-x64" in sources[1]["url"]
    assert sources[1]["needs_rosetta"] is True


def test_an_intel_mac_is_not_offered_the_same_build_twice(monkeypatch):
    monkeypatch.setattr(engine_mod.sys, "platform", "darwin")
    monkeypatch.setattr(engine_mod.platform, "machine", lambda: "x86_64")

    sources = Engine().ffmpeg_sources()

    assert len(sources) == 1
    assert "darwin-x64" in sources[0]["url"]


def test_a_mac_without_rosetta_is_told_how_to_get_it(tmp_path, monkeypatch):
    """An Intel build on Apple silicon fails to start; say so before trying."""
    monkeypatch.setattr(engine_mod.sys, "platform", "darwin")
    monkeypatch.setattr(engine_mod.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(engine_mod, "_rosetta_available", lambda: False)
    tried: list = []

    def body(url, dest, on_progress=None, **kwargs):
        tried.append(url)
        raise ToolError("mirror is down")

    monkeypatch.setattr(engine_mod, "download_to", body)
    with pytest.raises(ToolError) as caught:
        Engine(home=tmp_path / "app").install_ffmpeg()

    assert len(tried) == 1 and "darwin-arm64" in tried[0], "it tried the Intel build"
    assert "install-rosetta" in (caught.value.hint or "")
    # The reason is in the details, not silently swapped for "mirror is down".
    assert "needs Rosetta 2" in (caught.value.raw or "")


def test_rosetta_is_asked_about_by_running_arch(monkeypatch):
    """The check is Apple's own: ``arch`` exits non-zero without the translator."""
    engine_mod._rosetta_available.cache_clear()
    ran: list = []

    def without(command, **kwargs):
        ran.append(command)
        return subprocess.CompletedProcess(command, 1)

    monkeypatch.setattr(engine_mod.subprocess, "run", without)
    assert engine_mod._rosetta_available() is False
    assert ran == [["arch", "-x86_64", "/usr/bin/true"]]

    engine_mod._rosetta_available.cache_clear()
    monkeypatch.setattr(
        engine_mod.subprocess, "run", lambda command, **kwargs: subprocess.CompletedProcess(command, 0)
    )
    assert engine_mod._rosetta_available() is True
    engine_mod._rosetta_available.cache_clear()


def test_rosetta_that_cannot_be_asked_is_taken_as_absent(monkeypatch):
    engine_mod._rosetta_available.cache_clear()

    def boom(command, **kwargs):
        raise FileNotFoundError("no arch here")

    monkeypatch.setattr(engine_mod.subprocess, "run", boom)

    assert engine_mod._rosetta_available() is False
    engine_mod._rosetta_available.cache_clear()


def test_the_installer_says_how_big_the_download_is(tmp_path, monkeypatch):
    """The size goes in the line the user is watching, not only in the note."""
    monkeypatch.setattr(engine_mod, "_size_hint", lambda url, timeout=10: 152 * 1024 * 1024)

    def body(url, dest, on_progress=None, **kwargs):
        write_executable(Path(dest), "#!/bin/sh\necho 'ffmpeg version 7.0-fake'\n")
        return Path(dest)

    monkeypatch.setattr(engine_mod, "download_to", body)
    messages: list = []
    Engine(home=tmp_path / "app").install_ffmpeg(on_status=messages.append)

    assert any("152.0 MB" in message for message in messages)


def test_the_installer_offers_the_size_of_the_first_mirror(tmp_path, monkeypatch):
    """The prompt quotes the download it would actually make, or nothing."""
    engine = Engine(home=tmp_path / "app")
    asked: list = []
    monkeypatch.setattr(
        engine_mod, "_size_hint", lambda url, timeout=10: asked.append(url) or 1234
    )

    assert engine.ffmpeg_size_hint() == 1234
    assert asked == [engine.ffmpeg_sources()[0]["url"]]

    _patched_sources(monkeypatch, engine, [])
    assert engine.ffmpeg_size_hint() is None, "nothing to download is not a size"


def test_a_mac_with_rosetta_does_try_the_intel_build(tmp_path, monkeypatch):
    monkeypatch.setattr(engine_mod.sys, "platform", "darwin")
    monkeypatch.setattr(engine_mod.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(engine_mod, "_rosetta_available", lambda: True)
    tried: list = []

    def body(url, dest, on_progress=None, **kwargs):
        tried.append(url)
        write_executable(Path(dest), "#!/bin/sh\necho 'ffmpeg version 7.0-fake'\n")
        return Path(dest)

    monkeypatch.setattr(engine_mod, "download_to", body)
    tool = Engine(home=tmp_path / "app").install_ffmpeg()

    assert len(tried) == 1 and "darwin-arm64" in tried[0]
    assert "7.0-fake" in tool.version


def test_last_error_hint_is_a_shortcut_to_the_explainer():
    hint = engine_mod.last_error_hint("ERROR: Video unavailable")
    assert hint
    assert "ERROR:" not in hint


# ---------------------------------------------------------------------------
# downloading files from the internet
# ---------------------------------------------------------------------------


def test_download_to_explains_a_refused_download(tmp_path, monkeypatch):
    def refused(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(engine_mod.urllib.request, "urlopen", refused)
    dest = tmp_path / "tool"
    with pytest.raises(ToolError) as caught:
        download_to("https://example.invalid/tool", dest)
    assert "403" in caught.value.message
    assert "blocking" in (caught.value.hint or "")
    assert not (tmp_path / "tool.part").exists()


def test_download_to_explains_being_offline(tmp_path, monkeypatch):
    def offline(request, timeout=None):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(engine_mod.urllib.request, "urlopen", offline)
    dest = tmp_path / "tool"
    with pytest.raises(ToolError) as caught:
        download_to("https://example.invalid/tool", dest)
    assert caught.value.message == "Could not reach the internet."
    assert not dest.exists()
    assert not (tmp_path / "tool.part").exists()


def test_download_to_refuses_an_empty_download(tmp_path):
    source = tmp_path / "empty.bin"
    source.write_bytes(b"")
    dest = tmp_path / "tool"
    with pytest.raises(ToolError) as caught:
        download_to(source.as_uri(), dest)
    assert "empty" in caught.value.message
    assert not dest.exists()
    assert not (tmp_path / "tool.part").exists()


def test_download_to_copes_with_an_unknown_size(tmp_path, monkeypatch):
    """A server that sends no Content-Length still downloads fine."""
    class Response(io.BytesIO):
        headers: ClassVar[dict] = {}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def no_size(request, timeout=None):
        return Response(b"y" * 3000)

    monkeypatch.setattr(engine_mod.urllib.request, "urlopen", no_size)
    seen: list = []
    dest = tmp_path / "tool"
    download_to("https://example.invalid/tool", dest, on_progress=lambda done, total: seen.append(total))
    assert dest.read_bytes() == b"y" * 3000
    assert seen and all(total is None for total in seen)


# ---------------------------------------------------------------------------
# unpacking the ffmpeg bundle
# ---------------------------------------------------------------------------


def test_extract_member_skips_directories_in_a_zip(tmp_path):
    archive = tmp_path / "ffmpeg.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("ffmpeg-build/bin/", b"")
        zf.writestr("ffmpeg-build/bin/ffmpeg", b"ELF")
    dest = tmp_path / "out" / "ffmpeg"
    _extract_member(archive, r"bin/ffmpeg$", dest)
    assert dest.read_bytes() == b"ELF"
    assert os.access(dest, os.X_OK)


@needs_posix_exec
def test_an_unpacked_helper_is_runnable_by_its_owner_and_nobody_else(tmp_path):
    """The folder is locked, and the program inside is too (S3)."""
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("bin/ffmpeg", b"ELF")
    dest = tmp_path / "out" / "ffmpeg"
    _extract_member(archive, r"bin/ffmpeg$", dest)
    assert os.access(dest, os.X_OK)
    assert stat.S_IMODE(dest.stat().st_mode) == 0o700


def test_extract_member_can_leave_the_executable_bit_alone(tmp_path):
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("bin/ffmpeg", b"data")
    dest = tmp_path / "out" / "ffmpeg"
    _extract_member(archive, r"bin/ffmpeg$", dest, mode=None)
    assert dest.read_bytes() == b"data"
    assert not dest.stat().st_mode & stat.S_IXUSR


def test_extract_member_will_not_hand_over_a_directory(tmp_path):
    """A folder named like the program is not the program."""
    archive = tmp_path / "bundle.tar"
    with tarfile.open(archive, "w") as tf:
        folder = tarfile.TarInfo("bin/ffmpeg")
        folder.type = tarfile.DIRTYPE
        tf.addfile(folder)
    dest = tmp_path / "out" / "ffmpeg"
    with pytest.raises(ToolError):
        _extract_member(archive, r"bin/ffmpeg$", dest)
    assert not dest.exists()


# ---------------------------------------------------------------------------
# checking what gets downloaded (S2)
# ---------------------------------------------------------------------------


def test_sha256_of_matches_the_published_vector(tmp_path):
    path = tmp_path / "payload"
    path.write_bytes(b"abc")
    assert sha256_of(path) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_digest_from_sums_reads_the_published_shapes():
    text = (
        "# a comment\n"
        "\n"
        "1fa6733c37ea6fb51c99ad8fe785e7b7e5f3246c9b980230329d4fb72ed8d4d6  yt-dlp\n"
        "66674953fe251b89f4d08c5f0e35e0728679bd67ab3d7d05c0562af101dd3e7a *yt-dlp.exe\n"
        "short  yt-dlp.tar.gz\n"
    )
    assert digest_from_sums(text, "yt-dlp") == (
        "1fa6733c37ea6fb51c99ad8fe785e7b7e5f3246c9b980230329d4fb72ed8d4d6"
    )
    assert digest_from_sums(text, "yt-dlp.exe") == (
        "66674953fe251b89f4d08c5f0e35e0728679bd67ab3d7d05c0562af101dd3e7a"
    )
    assert digest_from_sums(text, "something-else") is None


def test_digest_from_sums_matches_on_the_file_name_only():
    digest = "ab" * 32
    assert digest_from_sums(f"{digest}  some/other/path/ffmpeg\n", "ffmpeg") == digest


def test_expected_sha256_uses_a_pinned_hash_without_fetching(monkeypatch):
    def must_not_fetch(url, timeout=30):
        raise AssertionError("a pinned source needs no checksums file")

    monkeypatch.setattr(engine_mod, "_fetch_text", must_not_fetch)
    assert expected_sha256({"url": "https://x/ffmpeg", "sha256": FAKE_PIN}) == FAKE_PIN


def test_expected_sha256_reads_the_published_list(monkeypatch):
    patch_checksums(monkeypatch, "yt-dlp")
    source = {"url": "https://x/yt-dlp", "sums_url": "https://x/SHA2-256SUMS"}
    assert expected_sha256(source) == FAKE_PIN


def test_expected_sha256_is_none_when_nothing_is_published():
    assert expected_sha256({"url": "https://x/thing"}) is None


def test_expected_sha256_says_when_the_list_does_not_mention_the_file(monkeypatch):
    patch_checksums(monkeypatch, "something-else")
    with pytest.raises(ToolError) as caught:
        expected_sha256({"url": "https://x/yt-dlp", "sums_url": "https://x/SHA2-256SUMS"})
    assert "did not mention" in caught.value.message


def test_download_to_never_leaves_mismatched_bytes_behind(tmp_path, monkeypatch):
    serving(monkeypatch, b"tampered")
    dest = tmp_path / "tool"
    with pytest.raises(ToolError) as caught:
        download_to("https://example.invalid/tool", dest, expected_sha256="0" * 64)
    assert "did not match the checksum" in caught.value.message
    assert "expected" in caught.value.raw and "got" in caught.value.raw
    assert not dest.exists()
    assert not (tmp_path / "tool.part").exists()


def test_download_to_accepts_bytes_that_match(tmp_path, monkeypatch):
    serving(monkeypatch, b"genuine")
    dest = tmp_path / "tool"
    download_to(
        "https://example.invalid/tool",
        dest,
        expected_sha256=hashlib.sha256(b"genuine").hexdigest(),
    )
    assert dest.read_bytes() == b"genuine"


def test_verified_download_refuses_an_unencrypted_source(tmp_path):
    source = {"url": "http://example.invalid/tool", "sha256": FAKE_PIN}
    with pytest.raises(ToolError) as caught:
        _verified_download(source, tmp_path / "tool")
    assert "secure connection" in caught.value.message
    assert not (tmp_path / "tool").exists()


def test_verified_download_refuses_a_source_it_cannot_check(tmp_path):
    with pytest.raises(ToolError) as caught:
        _verified_download({"url": "https://example.invalid/tool"}, tmp_path / "tool")
    assert "publishes no checksum" in caught.value.message
    assert not (tmp_path / "tool").exists()


def test_install_skips_a_mirror_whose_bytes_do_not_match(tmp_path, monkeypatch):
    """The point of the exercise: bad bytes are refused, then the next mirror."""
    tampered = b"#!/bin/sh\necho TAMPERED\n"
    genuine = b"#!/usr/bin/env python3\nprint('ffmpeg version 9.9-good')\n"
    asked = serving(monkeypatch, tampered, genuine)
    patch_checksums(monkeypatch, "ffmpeg")

    engine = Engine(home=tmp_path / "app")
    _patched_sources(
        monkeypatch,
        engine,
        [
            {
                "url": "https://mirror-one/ffmpeg",
                "archive": None,
                "pattern": None,
                "sums_url": "https://mirror-one/checksums.sha256",
            },
            mirror("https://mirror-two/ffmpeg", sha256=hashlib.sha256(genuine).hexdigest()),
        ],
    )
    tool = engine.install_ffmpeg()
    assert "9.9-good" in tool.version
    assert engine.ffmpeg_path.read_bytes() == genuine
    # Mirror one: the attempt, plus the one retry a checksums list allows.
    # Mirror two: one attempt, which matches and installs.
    assert len(asked) == 3


def test_a_rebuilt_release_is_retried_once(tmp_path, monkeypatch):
    """A release rebuilt mid-download leaves the list describing newer bytes."""
    genuine = b"genuine"
    bodies = [f"{'0' * 64}  tool\n", f"{hashlib.sha256(genuine).hexdigest()}  tool\n"]
    monkeypatch.setattr(engine_mod, "_fetch_text", lambda url, timeout=30: bodies.pop(0))
    asked = serving(monkeypatch, genuine)

    dest = tmp_path / "tool"
    _verified_download({"url": "https://x/tool", "sums_url": "https://x/sums"}, dest)
    assert dest.read_bytes() == genuine
    assert len(asked) == 2


def test_a_pinned_mismatch_is_taken_at_face_value(tmp_path, monkeypatch):
    asked = serving(monkeypatch, b"tampered", b"genuine")
    with pytest.raises(ToolError):
        _verified_download({"url": "https://x/tool", "sha256": "0" * 64}, tmp_path / "tool")
    assert len(asked) == 1
    assert not (tmp_path / "tool").exists()


def test_an_archive_a_crash_already_finished_is_not_downloaded_again(tmp_path, monkeypatch):
    """A run killed between download and unpack leaves 150 MB worth keeping."""
    payload = b"#!/bin/sh\necho 'ffmpeg version 7.3-fake'\n"
    archive_bytes = io.BytesIO()
    with tarfile.open(fileobj=archive_bytes, mode="w:xz") as tf:
        info = tarfile.TarInfo("ffmpeg-build/bin/ffmpeg")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
    bundle = archive_bytes.getvalue()
    patch_checksums(monkeypatch, "ffmpeg.tar.xz", digest=hashlib.sha256(bundle).hexdigest())

    def must_not_download(url, dest, on_progress=None, **kwargs):
        raise AssertionError("a complete archive was downloaded all over again")

    monkeypatch.setattr(engine_mod, "download_to", must_not_download)
    engine = Engine(home=tmp_path / "app")
    url = "https://mirror/ffmpeg.tar.xz"
    tag = engine_mod._mirror_tag(url)
    engine.bin_dir.mkdir(parents=True, exist_ok=True)
    (engine.bin_dir / f"ffmpeg-download-{tag}.tar-xz").write_bytes(bundle)
    _patched_sources(
        monkeypatch,
        engine,
        [
            {
                "url": url,
                "archive": "tar.xz",
                "pattern": r"bin/ffmpeg$",
                "sums_url": "https://mirror/checksums.sha256",
            }
        ],
    )

    tool = engine.install_ffmpeg()

    assert "7.3-fake" in tool.version
    assert not list(engine.bin_dir.glob("ffmpeg-download-*"))


def test_an_unfinished_archive_is_not_mistaken_for_a_complete_one(tmp_path, monkeypatch):
    """Only bytes that hash right are reused; anything else is fetched."""
    genuine = b"the real bundle"
    patch_checksums(monkeypatch, "ffmpeg.tar.xz", digest=hashlib.sha256(genuine).hexdigest())

    def body(url, dest, on_progress=None, **kwargs):
        Path(dest).write_bytes(genuine)
        return Path(dest)

    monkeypatch.setattr(engine_mod, "download_to", body)
    engine = Engine(home=tmp_path / "app")
    url = "https://mirror/ffmpeg.tar.xz"
    tag = engine_mod._mirror_tag(url)
    engine.bin_dir.mkdir(parents=True, exist_ok=True)
    (engine.bin_dir / f"ffmpeg-download-{tag}.tar-xz").write_bytes(b"half a bundle")
    _patched_sources(
        monkeypatch,
        engine,
        [
            {
                "url": url,
                "archive": "tar.xz",
                "pattern": r"bin/ffmpeg$",
                "sums_url": "https://mirror/checksums.sha256",
            }
        ],
    )

    download_calls: list = []
    real = engine_mod.download_to

    def counting(url, dest, on_progress=None, **kwargs):
        download_calls.append(url)
        return real(url, dest, on_progress, **kwargs)

    monkeypatch.setattr(engine_mod, "download_to", counting)
    with pytest.raises(ToolError):
        engine.install_ffmpeg()

    assert download_calls == [url], "a half archive was treated as complete"


def test_an_archive_that_fails_its_checksum_is_never_unpacked(tmp_path, monkeypatch):
    serving(monkeypatch, b"<html>502 Bad Gateway</html>")
    patch_checksums(monkeypatch, "ffmpeg-master-latest-linux64-gpl.tar.xz")

    engine = Engine(home=tmp_path / "app")
    _patched_sources(
        monkeypatch,
        engine,
        [
            {
                "url": "https://mirror/ffmpeg-master-latest-linux64-gpl.tar.xz",
                "archive": "tar.xz",
                "pattern": r"bin/ffmpeg$",
                "sums_url": "https://mirror/checksums.sha256",
            }
        ],
    )
    with pytest.raises(ToolError) as caught:
        engine.install_ffmpeg()
    assert "could not be downloaded" in caught.value.message
    assert not engine.ffmpeg_path.exists()


def test_install_ytdlp_will_not_install_when_the_checksums_cannot_be_read(tmp_path, monkeypatch):
    def unreachable(url, timeout=30):
        raise ToolError("Could not reach the list of checksums.")

    monkeypatch.setattr(engine_mod, "_fetch_text", unreachable)

    def must_not_download(url, dest, on_progress):
        raise AssertionError("nothing may be downloaded when the check cannot happen")

    monkeypatch.setattr(engine_mod, "download_to", as_download(must_not_download))
    with pytest.raises(ToolError):
        Engine(home=tmp_path / "app").install_ytdlp()
    assert not (tmp_path / "app" / "bin" / "yt-dlp").exists()


def test_checksums_are_read_from_the_body(monkeypatch):
    serving(monkeypatch, b"aa" + b"b" * 62 + b"  tool\n")
    assert "tool" in engine_mod._fetch_text("https://example.invalid/SHA2-256SUMS")


def test_checksums_are_only_fetched_over_https():
    with pytest.raises(ToolError) as caught:
        engine_mod._fetch_text("http://example.invalid/SHA2-256SUMS")
    assert "secure connection" in caught.value.message


def test_a_checksums_list_that_answers_with_an_error_is_explained(monkeypatch):
    def refused(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", {}, None)

    monkeypatch.setattr(engine_mod.urllib.request, "urlopen", refused)
    with pytest.raises(ToolError) as caught:
        engine_mod._fetch_text("https://example.invalid/SHA2-256SUMS")
    assert "429" in caught.value.message
    assert "cannot check" in (caught.value.hint or "")


def test_a_checksums_list_that_cannot_be_reached_is_explained(monkeypatch):
    def offline(request, timeout=None):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(engine_mod.urllib.request, "urlopen", offline)
    with pytest.raises(ToolError) as caught:
        engine_mod._fetch_text("https://example.invalid/SHA2-256SUMS")
    assert caught.value.message == "Could not reach the list of checksums."


def test_digest_from_sums_skips_the_lines_it_cannot_use():
    digest = "cd" * 32
    text = "# comment\nloose-word\n" + f"{'z' * 64}  wrong\n" + f"{digest}  tool\n"
    assert digest_from_sums(text, "tool") == digest


def test_a_sidecar_that_is_missing_or_unwritable_is_not_fatal(tmp_path):
    assert recorded_checksum(tmp_path / "tool") is None
    # A path that cannot be written to must not take the install down with it.
    record_checksum(tmp_path / "does-not-exist" / "tool", "ab" * 32)


@needs_posix_exec
def test_an_installed_engine_is_not_readable_by_anyone_else(tmp_path, monkeypatch):
    """A downloaded program nobody else can read or run, even outside our folder."""

    def body(url, dest, on_progress):
        write_executable(Path(dest), "#!/usr/bin/env python3\nprint('2099.05.05')\n")
        return Path(dest)

    monkeypatch.setattr(engine_mod, "download_to", as_download(body))
    patch_checksums(monkeypatch)

    engine = Engine(home=tmp_path / "app")
    engine.install_ytdlp()

    assert stat.S_IMODE(engine.ytdlp_path.stat().st_mode) == 0o700


@needs_posix_exec
def test_making_a_helper_runnable_tightens_a_loose_file(tmp_path):
    """What the mode is, not what it used to be: a 0755 file becomes 0700."""
    path = write_executable(tmp_path / "ffmpeg", "#!/bin/sh\n")
    assert stat.S_IMODE(path.stat().st_mode) == 0o755

    engine_mod._make_runnable(path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o700


def test_a_file_chmod_refuses_does_not_take_the_install_down(tmp_path, monkeypatch):
    path = write_executable(tmp_path / "ffmpeg", "#!/bin/sh\n")

    def refuse(target, mode):
        raise OSError("operation not supported")

    monkeypatch.setattr(engine_mod.os, "chmod", refuse)
    engine_mod._make_runnable(path)  # must not raise


def test_the_installed_hash_is_recorded_and_a_swap_is_noticed(tmp_path, monkeypatch):
    def body(url, dest, on_progress):
        write_executable(Path(dest), "#!/usr/bin/env python3\nprint('2099.05.05')\n")
        return Path(dest)

    monkeypatch.setattr(engine_mod, "download_to", as_download(body))
    patch_checksums(monkeypatch)

    engine = Engine(home=tmp_path / "app")
    engine.install_ytdlp()
    assert recorded_checksum(engine.ytdlp_path) == sha256_of(engine.ytdlp_path)
    assert "matches what was installed" in "\n".join(engine.report())

    # Something replaces the binary behind Viddl's back.
    write_executable(engine.ytdlp_path, "#!/usr/bin/env python3\nprint('2000.01.01')\n")
    assert "changed since it was installed" in "\n".join(engine.report())


# ---------------------------------------------------------------------------
# finishing a download that was interrupted (R8)
#
# A 150 MB ffmpeg build used to start again from zero whenever the connection
# dropped. These tests use a real socket on localhost, because a Range request
# is a conversation with a server and a stand-in would only test the stand-in.
# ---------------------------------------------------------------------------


class RangeServer:
    """A local HTTP server that can honour, ignore or refuse Range requests."""

    def __init__(self, payload: bytes, honour_range=True, drop_after=None, drop_each=False):
        self.payload = payload
        self.honour_range = honour_range
        self.drop_after = drop_after
        self.drop_each = drop_each
        self.requests: list = []
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_HEAD(self):
                owner.requests.append(("HEAD", self.headers.get("Range")))
                self.send_response(200)
                self.send_header("Content-Length", str(len(owner.payload)))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()

            def do_GET(self):
                header = self.headers.get("Range")
                owner.requests.append(("GET", header))
                start = 0
                if header and header.startswith("bytes="):
                    start = int(header.split("=", 1)[1].split("-", 1)[0] or 0)
                    if not owner.honour_range:
                        start = 0
                    elif start >= len(owner.payload):
                        self.send_response(416)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                body = owner.payload[start:]
                self.send_response(206 if start else 200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Accept-Ranges", "bytes")
                if start:
                    self.send_header(
                        "Content-Range", f"bytes {start}-{len(owner.payload) - 1}/{len(owner.payload)}"
                    )
                self.end_headers()
                if owner.drop_after is not None and (owner.drop_each or start == 0):
                    # Send part of it and hang up, like a connection that died.
                    self.wfile.write(body[: owner.drop_after])
                    self.wfile.flush()
                    self.close_connection = True
                    return
                self.wfile.write(body)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/ffmpeg.tar.xz"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def ranges(monkeypatch):
    """Servers to close afterwards, and no real waiting between attempts."""
    monkeypatch.setattr(engine_mod, "_RETRY_PAUSE_SECONDS", 0)
    made: list = []
    yield made
    for server in made:
        server.close()


def serve_ranges(ranges, payload: bytes, **kwargs) -> RangeServer:
    server = RangeServer(payload, **kwargs)
    ranges.append(server)
    return server


def test_an_interrupted_download_carries_on_from_where_it_stopped(tmp_path, ranges):
    server = serve_ranges(ranges, b"abcdefghij" * 1000)
    dest = tmp_path / "ffmpeg"
    part = tmp_path / "ffmpeg.part"
    part.write_bytes(server.payload[:4000])
    seen: list = []

    download_to(server.url, dest, on_progress=lambda done, total: seen.append((done, total)))

    assert dest.read_bytes() == server.payload
    assert server.requests == [("GET", "bytes=4000-")], "it did not ask for the rest"
    # Progress counts the bytes already held, and the total is the whole file.
    assert seen[0] == (4000, len(server.payload))


def test_a_server_that_ignores_the_range_gets_a_clean_start(tmp_path, ranges):
    """Appending a whole file to half of one would corrupt it; it starts over."""
    server = serve_ranges(ranges, b"0123456789" * 100, honour_range=False)
    dest = tmp_path / "ffmpeg"
    (tmp_path / "ffmpeg.part").write_bytes(b"junk" * 100)

    download_to(server.url, dest)

    assert dest.read_bytes() == server.payload


def test_a_part_older_than_the_download_is_thrown_away(tmp_path, ranges):
    """A 416 means the half-file is not part of this file. Start again."""
    server = serve_ranges(ranges, b"fresh" * 100)
    dest = tmp_path / "ffmpeg"
    (tmp_path / "ffmpeg.part").write_bytes(b"x" * 5000)  # longer than the file

    download_to(server.url, dest)

    assert dest.read_bytes() == server.payload
    assert [header for _, header in server.requests] == ["bytes=5000-", None]


def test_a_dropped_connection_is_picked_up_again_by_itself(tmp_path, ranges):
    server = serve_ranges(ranges, b"z" * 5000, drop_after=2000)
    dest = tmp_path / "ffmpeg"

    download_to(server.url, dest)

    assert dest.read_bytes() == server.payload
    assert len(server.requests) == 2, "it did not try again"
    assert server.requests[0] == ("GET", None)
    assert server.requests[1] == ("GET", "bytes=2000-")


def test_the_installer_files_its_half_download_where_the_next_run_finds_it(
    tmp_path, monkeypatch
):
    """A rerun must hand the same part-file to the same mirror, or nothing resumes.

    The download itself is the real one in the tests above; this is the installer
    keeping its side of the bargain — a stable name, inside the app folder that
    survives a crash, rather than a temporary directory that does not.
    """
    parts: list = []

    def body(url, dest, on_progress=None, **kwargs):
        part = Path(kwargs["part_path"])
        parts.append(part)
        if len(parts) == 1:
            part.write_bytes(b"half of a large download")
            raise ToolError("connection dropped")
        write_executable(Path(dest), "#!/bin/sh\necho 'ffmpeg version 7.1-fake'\n")
        return Path(dest)

    monkeypatch.setattr(engine_mod, "download_to", body)
    engine = Engine(home=tmp_path / "app")
    source = mirror("https://mirror/ffmpeg")
    _patched_sources(monkeypatch, engine, [source])

    with pytest.raises(ToolError):
        engine.install_ffmpeg()
    kept = parts[0]
    assert kept.parent == engine.bin_dir, "a temporary folder is gone by the next run"
    assert kept.exists()

    # The next run, the same link: the same half-file, which is what resumes.
    tool = engine.install_ffmpeg()

    assert parts[0] == parts[1] == kept
    assert "7.1-fake" in tool.version
    assert not kept.exists(), "150 MB of bytes nobody can use was left behind"


def test_each_mirror_has_its_own_half_download(tmp_path, monkeypatch):
    """Two mirrors are two files; one's half must never be glued to the other's."""
    names: list = []

    def body(url, dest, on_progress=None, **kwargs):
        names.append(Path(kwargs["part_path"]).name)
        if len(names) == 1:
            raise ToolError("connection dropped")
        write_executable(Path(dest), "#!/bin/sh\necho 'ffmpeg version 7.0-fake'\n")
        return Path(dest)

    monkeypatch.setattr(engine_mod, "download_to", body)
    engine = Engine(home=tmp_path / "app")
    _patched_sources(
        monkeypatch,
        engine,
        [mirror("https://mirror-one/ffmpeg"), mirror("https://mirror-two/ffmpeg")],
    )
    # Something an earlier failed run left behind.
    engine.bin_dir.mkdir(parents=True, exist_ok=True)
    stale = engine.bin_dir / "ffmpeg-download-00000000.tar-xz"
    stale.write_bytes(b"old")
    stale_part = engine.bin_dir / "ffmpeg-00000000.part"
    stale_part.write_bytes(b"old half")

    engine.install_ffmpeg()

    assert len(set(names)) == 2, "both mirrors shared one part-file"
    assert not stale.exists() and not stale_part.exists(), (
        "a 150 MB leftover survived a successful install"
    )


def test_a_download_that_keeps_dropping_says_what_is_kept(tmp_path, ranges):
    server = serve_ranges(ranges, b"z" * 5000, drop_after=1500, drop_each=True)
    dest = tmp_path / "ffmpeg"

    with pytest.raises(ToolError) as caught:
        download_to(server.url, dest)

    assert "kept" in (caught.value.hint or "")
    assert not dest.exists()
    part = tmp_path / "ffmpeg.part"
    assert part.is_file() and part.stat().st_size > 0, "the bytes it did get are gone"


def test_a_part_that_can_never_be_continued_is_given_up_on(tmp_path, monkeypatch):
    """A stale part is thrown away once and fetched again; not forever."""
    def always_stale(url, tmp_path, on_progress, timeout):
        raise engine_mod._StalePartial()

    monkeypatch.setattr(engine_mod, "_fetch", always_stale)
    monkeypatch.setattr(engine_mod, "_RETRY_PAUSE_SECONDS", 0)
    dest = tmp_path / "ffmpeg"
    (tmp_path / "ffmpeg.part").write_bytes(b"another build's bytes")

    with pytest.raises(ToolError) as caught:
        download_to("https://example.invalid/ffmpeg", dest)

    assert "could not be continued" in caught.value.message
    assert not dest.exists()
    assert not (tmp_path / "ffmpeg.part").exists(), "bytes that cannot be used stayed"


def test_the_size_is_asked_for_once_and_remembered(monkeypatch):
    """The qualities screen asks on every arrow key; GitHub is not asked twice."""
    calls: list = []

    def size(url, timeout=10):
        calls.append(url)
        return None

    monkeypatch.setattr(engine_mod, "download_size", size)
    monkeypatch.setattr(engine_mod, "_SIZE_HINTS", {})

    assert engine_mod._size_hint("https://x/ffmpeg") is None
    assert engine_mod._size_hint("https://x/ffmpeg") is None

    assert calls == ["https://x/ffmpeg"], "an unknown size was asked for again"


def test_the_size_is_asked_for_without_downloading_anything(tmp_path, ranges):
    payload = b"s" * 12345
    server = serve_ranges(ranges, payload)

    assert engine_mod.download_size(server.url) == len(payload)
    assert server.requests == [("HEAD", None)]


def test_an_unknown_size_is_none_rather_than_a_guess(tmp_path, monkeypatch):
    def unreachable(request, timeout=None):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(engine_mod.urllib.request, "urlopen", unreachable)
    assert engine_mod.download_size("https://example.invalid/file") is None

# ---------------------------------------------------------------------------
# every HTTP wait follows VIDDL_TIMEOUT (R11)
# ---------------------------------------------------------------------------


def test_the_checksum_fetch_waits_as_long_as_asked(monkeypatch):
    waits: list = []

    def fake_urlopen(request, timeout=None):
        waits.append(timeout)
        return FakeResponse(b"hash  ffmpeg\n")

    monkeypatch.setattr(engine_mod.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("VIDDL_TIMEOUT", "45")

    body = engine_mod._fetch_text("https://example.invalid/SHA2-256SUMS")
    assert body == "hash  ffmpeg\n"
    assert waits == [45.0]

    monkeypatch.delenv("VIDDL_TIMEOUT")
    engine_mod._fetch_text("https://example.invalid/SHA2-256SUMS")
    assert waits == [45.0, 30], "unset keeps the wait it was designed with"


def test_the_size_check_waits_as_long_as_asked(monkeypatch):
    waits: list = []

    def fake_urlopen(request, timeout=None):
        waits.append(timeout)
        return FakeResponse(b"")

    monkeypatch.setattr(engine_mod.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("VIDDL_TIMEOUT", "12")

    assert engine_mod.download_size("https://example.invalid/ffmpeg") == 0
    assert waits == [12.0]

    monkeypatch.delenv("VIDDL_TIMEOUT")
    engine_mod.download_size("https://example.invalid/ffmpeg")
    assert waits == [12.0, 10]


def test_the_check_that_a_new_tool_runs_waits_as_long_as_asked(monkeypatch):
    waits: list = []

    def run(command, **kwargs):
        waits.append(kwargs.get("timeout"))
        return subprocess.CompletedProcess(command, 0, "ffmpeg version 7.0", "")

    monkeypatch.setattr(engine_mod.subprocess, "run", run)
    monkeypatch.setenv("VIDDL_TIMEOUT", "40")

    assert engine_mod._run_probe(["ffmpeg"], "-version") == (True, "ffmpeg version 7.0")
    assert waits == [40.0]

    monkeypatch.delenv("VIDDL_TIMEOUT")
    engine_mod._run_probe(["ffmpeg"], "-version")
    assert waits == [40.0, 25]


def test_the_doctor_says_whether_the_log_has_anything_in_it(tmp_path):
    """A path alone answered a question nobody had: the file always exists (E3)."""
    from viddl import appdata

    engine = Engine(home=tmp_path / "app")
    assert "nothing written yet" in "\n".join(engine.report())

    log = appdata.log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("2026-10-27 ERROR viddl: the download failed\n", encoding="utf-8")

    report = "\n".join(engine.report())
    assert "nothing written yet" not in report
    assert str(log) in report
    # The size, so "is what I am looking at the failure or a stale one" is answered.
    assert "last written" in report
