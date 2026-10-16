"""The viddl:// hand-off and its operating-system registration."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from viddl.protocol import (
    SchemeRequest,
    build_scheme_url,
    extension_source_dir,
    install_extension,
    launch_command,
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


def test_build_scheme_url_round_trips():
    target = build_scheme_url("https://x.com/user/status/1", audio=True)
    assert target.startswith("viddl://download?url=")
    request = parse_scheme(target)
    assert request.url == "https://x.com/user/status/1"
    assert request.audio is True


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
