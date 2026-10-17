"""Finding, installing and verifying yt-dlp and ffmpeg."""

from __future__ import annotations

import hashlib
import io
import os
import stat
import sys
import tarfile
import threading
import time
import urllib.error
import zipfile
from pathlib import Path

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


def make_shim(directory: Path, name: str = "yt-dlp", version: str = "2099.01.01") -> Path:
    return write_executable(directory / name, f"#!/bin/sh\necho {version}\n")


# ---------------------------------------------------------------------------
# the checksum layer, stood in for so these tests stay offline
# ---------------------------------------------------------------------------

FAKE_PIN = "1" * 64


def as_download(body):
    """Adapt a fake download body to the real ``download_to`` signature.

    The installer now tells the downloader which checksum it expects, so a
    stand-in has to accept it; the interesting part of a test stays in ``body``.
    """

    def download(url, dest, on_progress=None, timeout=30, expected_sha256=None):
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
    write_executable(home / "bin" / "yt-dlp", "#!/bin/sh\nexit 3\n")
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
        engine.bin_dir / "ffmpeg",
        "#!/bin/sh\necho 'ffmpeg version 7.0-fake'\n",
    )
    assert engine.ffmpeg_location_args() == ["--ffmpeg-location", str(engine.bin_dir)]


def make_ffmpeg_shim(directory: Path, version: str = "7.0-fake") -> Path:
    return write_executable(directory / "ffmpeg", f"#!/bin/sh\necho 'ffmpeg version {version}'\n")


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
    assert tool.path == elsewhere


def test_a_broken_ffmpeg_is_not_trusted(tmp_path, monkeypatch):
    home = tmp_path / "app"
    write_executable(home / "bin" / "ffmpeg", "#!/bin/sh\nexit 1\n")
    no_system_tools(monkeypatch)
    assert Engine(home=home).find_ffmpeg() is None


def test_ffmpeg_without_a_version_line_is_still_usable(tmp_path, monkeypatch):
    no_system_tools(monkeypatch)
    write_executable(tmp_path / "app" / "bin" / "ffmpeg", "#!/bin/sh\necho ready\n")
    tool = Engine(home=tmp_path / "app").find_ffmpeg()
    assert tool is not None
    assert tool.version == ""


# ---------------------------------------------------------------------------
# ffmpeg setup
# ---------------------------------------------------------------------------


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


def test_origin_text_is_plain_language():
    assert Tool("yt-dlp", [], "system").origin_text == "found on your computer"
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
        headers: dict = {}

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
    serving(monkeypatch, "aa".encode() + b"b" * 62 + b"  tool\n")
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
