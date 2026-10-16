"""Finding, installing and verifying yt-dlp and ffmpeg."""

from __future__ import annotations

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
from viddl.engine import Engine, Tool, ToolError, _extract_member, download_to

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

    def fake_download(url, dest, on_progress=None, timeout=30):
        requested.append(url)
        write_executable(Path(dest), "#!/usr/bin/env python3\nprint('2099.05.05')\n")
        if on_progress:
            on_progress(10, 10)
        return Path(dest)

    monkeypatch.setattr(engine_mod, "download_to", fake_download)

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
    def fake_download(url, dest, on_progress=None, timeout=30):
        # No shebang: the kernel refuses to exec this directly.
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("print('2024.05.05')\n", encoding="utf-8")
        return dest

    monkeypatch.setattr(engine_mod, "download_to", fake_download)
    engine = Engine(home=tmp_path / "app")
    tool = engine.install_ytdlp()
    assert tool.version == "2024.05.05"
    assert tool.prefix[0] == sys.executable
    assert tool.prefix[1] == str(engine.ytdlp_path)


def test_ensure_only_downloads_once(tmp_path, monkeypatch):
    calls = []

    def fake_download(url, dest, on_progress=None, timeout=30):
        calls.append(url)
        write_executable(Path(dest), "#!/usr/bin/env python3\nprint('1.2.3')\n")
        return Path(dest)

    monkeypatch.setattr(engine_mod, "download_to", fake_download)
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

    def flaky_download(url, dest, on_progress=None, timeout=30):
        attempts.append(url)
        if len(attempts) == 1:
            raise ToolError("mirror is down")
        write_executable(Path(dest), "#!/usr/bin/env python3\nprint('ffmpeg version 7.0-fake')\n")
        return Path(dest)

    monkeypatch.setattr(engine_mod, "download_to", flaky_download)
    engine = Engine(home=tmp_path / "app")
    _patched_sources(
        monkeypatch,
        engine,
        [
            {"url": "https://mirror-one/ffmpeg", "archive": None, "pattern": None},
            {"url": "https://mirror-two/ffmpeg", "archive": None, "pattern": None},
        ],
    )
    tool = engine.install_ffmpeg()
    assert attempts == ["https://mirror-one/ffmpeg", "https://mirror-two/ffmpeg"]
    assert tool.origin == "viddl"
    assert "7.0-fake" in tool.version


def test_install_ffmpeg_survives_an_html_error_page(tmp_path, monkeypatch):
    """A mirror answering with a web page instead of an archive must not crash."""
    attempts: list = []

    def broken_mirror(url, dest, on_progress=None, timeout=30):
        attempts.append(url)
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if len(attempts) == 1:
            dest.write_bytes(b"<html>502 Bad Gateway</html>")
        else:
            write_executable(dest, "#!/usr/bin/env python3\nprint('ffmpeg version 8.0-fake')\n")
        return dest

    monkeypatch.setattr(engine_mod, "download_to", broken_mirror)
    engine = Engine(home=tmp_path / "app")
    _patched_sources(
        monkeypatch,
        engine,
        [
            {
                "url": "https://mirror-one/ffmpeg.tar.xz",
                "archive": "tar.xz",
                "pattern": r"bin/ffmpeg$",
            },
            {"url": "https://mirror-two/ffmpeg", "archive": None, "pattern": None},
        ],
    )
    tool = engine.install_ffmpeg()
    assert len(attempts) == 2
    assert "8.0-fake" in tool.version


def test_install_ffmpeg_gives_up_with_advice(tmp_path, monkeypatch):
    def always_fails(url, dest, on_progress=None, timeout=30):
        raise ToolError("no route to host")

    monkeypatch.setattr(engine_mod, "download_to", always_fails)
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

    def fake_download(url, dest, on_progress=None, timeout=30):
        calls.append(url)
        write_executable(Path(dest), "#!/usr/bin/env python3\nprint('ffmpeg version 7.0-fake')\n")
        return Path(dest)

    monkeypatch.setattr(engine_mod, "download_to", fake_download)
    no_system_tools(monkeypatch)

    engine = Engine(home=tmp_path / "app")
    _patched_sources(
        monkeypatch, engine, [{"url": "https://mirror/ffmpeg", "archive": None, "pattern": None}]
    )
    first = engine.ensure_ffmpeg()
    second = engine.ensure_ffmpeg()
    assert first is second
    assert len(calls) == 1


def test_ensure_ffmpeg_says_what_it_is_doing(tmp_path, monkeypatch):
    messages: list = []

    def fake_download(url, dest, on_progress=None, timeout=30):
        write_executable(Path(dest), "#!/usr/bin/env python3\nprint('ffmpeg version 7.0-fake')\n")
        return Path(dest)

    monkeypatch.setattr(engine_mod, "download_to", fake_download)
    no_system_tools(monkeypatch)

    engine = Engine(home=tmp_path / "app")
    _patched_sources(
        monkeypatch, engine, [{"url": "https://mirror/ffmpeg", "archive": None, "pattern": None}]
    )
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

    def fake_download(url, dest, on_progress=None, timeout=30):
        calls.append(url)
        time.sleep(0.05)
        write_executable(Path(dest), "#!/usr/bin/env python3\nprint('ffmpeg version 7.0-fake')\n")
        return Path(dest)

    monkeypatch.setattr(engine_mod, "download_to", fake_download)
    no_system_tools(monkeypatch)

    engine = Engine(home=tmp_path / "app")
    _patched_sources(
        monkeypatch, engine, [{"url": "https://mirror/ffmpeg", "archive": None, "pattern": None}]
    )
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

    def broken_then_good(url, dest, on_progress=None, timeout=30):
        attempts.append(url)
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if len(attempts) == 1:
            write_executable(dest, "#!/bin/sh\nexit 9\n")
        else:
            write_executable(dest, "#!/usr/bin/env python3\nprint('ffmpeg version 7.1-good')\n")
        return dest

    monkeypatch.setattr(engine_mod, "download_to", broken_then_good)
    engine = Engine(home=tmp_path / "app")
    _patched_sources(
        monkeypatch,
        engine,
        [
            {"url": "https://mirror-one/ffmpeg", "archive": None, "pattern": None},
            {"url": "https://mirror-two/ffmpeg", "archive": None, "pattern": None},
        ],
    )
    tool = engine.install_ffmpeg(on_status=messages.append)
    assert len(attempts) == 2
    assert "7.1-good" in tool.version
    assert any("mirror" in message for message in messages)


def test_install_ffmpeg_keeps_the_reason_it_gave_up(tmp_path, monkeypatch):
    def always_broken(url, dest, on_progress=None, timeout=30):
        write_executable(Path(dest), "#!/bin/sh\nexit 9\n")
        return Path(dest)

    monkeypatch.setattr(engine_mod, "download_to", always_broken)
    engine = Engine(home=tmp_path / "app")
    _patched_sources(
        monkeypatch, engine, [{"url": "https://m/ffmpeg", "archive": None, "pattern": None}]
    )
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
