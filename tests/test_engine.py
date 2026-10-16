"""Finding, installing and verifying yt-dlp and ffmpeg."""

from __future__ import annotations

import io
import os
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from viddl import engine as engine_mod
from viddl.engine import Engine, ToolError, _extract_member, download_to

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
