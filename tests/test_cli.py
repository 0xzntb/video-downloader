"""The command line surface."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from viddl import appdata
from viddl.cli import main
from viddl.protocol import extension_source_dir

REPO_EXTENSION = extension_source_dir()


def test_doctor_reports_paths_and_tools(capsys):
    assert main(["--doctor"]) == 0
    output = capsys.readouterr().out
    assert "Viddl" in output
    assert "yt-dlp" in output
    assert "ffmpeg" in output
    assert str(appdata.data_dir()) in output
    assert str(appdata.downloads_dir()) in output


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
