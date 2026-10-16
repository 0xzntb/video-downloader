"""Checks the bundled browser extension from the Python test run.

JavaScript syntax and behaviour are verified with Node when it is available, so
``pytest`` on its own covers the whole project.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from viddl.protocol import extension_source_dir

NODE = shutil.which("node")
EXTENSION = extension_source_dir()


def test_manifest_is_valid_mv3():
    manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 3
    assert manifest["background"]["service_worker"] == "background.js"
    assert manifest["action"]["default_popup"] == "popup.html"
    assert "activeTab" in manifest["permissions"]
    assert "contextMenus" in manifest["permissions"]
    # No blanket host permissions: the extension only touches sites you invoke it on.
    assert "host_permissions" not in manifest
    assert manifest["content_scripts"][0]["matches"] == ["*://*.youtube.com/*"]


def test_every_referenced_file_exists():
    manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
    referenced = [
        manifest["background"]["service_worker"],
        manifest["action"]["default_popup"],
        *manifest["icons"].values(),
    ]
    for block in manifest["content_scripts"]:
        referenced += block.get("js", [])
        referenced += block.get("css", [])
    for relative in referenced:
        assert (EXTENSION / relative).is_file(), f"missing {relative}"


def test_the_extension_runs_no_server():
    """The whole design is a custom-scheme hand-off, so nothing listens."""
    for path in EXTENSION.rglob("*"):
        if path.suffix not in (".js", ".json", ".html", ".css"):
            continue
        body = path.read_text(encoding="utf-8")
        assert "localhost" not in body
        assert "127.0.0.1" not in body
        assert "webRequest" not in body
        assert "nativeMessaging" not in body


def test_uses_the_real_custom_scheme():
    body = (EXTENSION / "background.js").read_text(encoding="utf-8")
    assert "const SCHEME = 'viddl'" in body
    assert "${SCHEME}://download?url=" in body


@pytest.mark.skipif(NODE is None, reason="Node is needed to check the JavaScript")
def test_javascript_parses():
    for name in ("background.js", "content-youtube.js", "popup.js"):
        result = subprocess.run(
            [NODE, "--check", str(EXTENSION / name)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"{name}: {result.stderr}"


@pytest.mark.skipif(NODE is None, reason="Node is needed to run the JavaScript tests")
def test_javascript_behaviour():
    test_file = Path(__file__).resolve().parent / "extension.test.js"
    result = subprocess.run(
        [NODE, "--test", str(test_file)],
        capture_output=True,
        text=True,
        cwd=str(test_file.parent.parent),
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_extension_folder_is_not_a_python_package():
    # It ships as package data, not as an importable module.
    assert not (EXTENSION / "__init__.py").exists()
    pyproject = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    assert "browser_extension/icons/*.png" in pyproject
