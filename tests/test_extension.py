"""Checks the bundled browser extension from the Python test run.

JavaScript syntax and behaviour are verified with Node when it is available, so
``pytest`` on its own covers the whole project.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from viddl.protocol import extension_source_dir

NODE = shutil.which("node")
EXTENSION = extension_source_dir()
REPO_ROOT = Path(__file__).resolve().parent.parent
#: Playwright is a dev tool for the browser tests only: it is not a dependency of
#: the app, and `npm install` is what puts it here.
PLAYWRIGHT = REPO_ROOT / "node_modules" / "playwright"
#: The home directory of the account running the tests, read before the autouse
#: fixture hides ``HOME`` from each test. Playwright looks for its Chromium in
#: ``~/.cache/ms-playwright``, and a browser is not part of what the app under
#: test is allowed to see — the browser tests are, and they need the real one.
REAL_HOME = Path.home()
BROWSER_TEST = Path(__file__).resolve().parent / "extension.browser.test.js"


def load_manifest(name: str) -> dict:
    return json.loads((EXTENSION / name).read_text(encoding="utf-8"))


def test_manifest_is_valid_mv3():
    manifest = load_manifest("manifest.json")
    assert manifest["manifest_version"] == 3
    assert manifest["background"]["service_worker"] == "background.js"
    assert manifest["action"]["default_popup"] == "popup.html"
    assert "activeTab" in manifest["permissions"]
    assert "contextMenus" in manifest["permissions"]
    # No blanket host permissions: the extension only touches sites you invoke it on.
    assert "host_permissions" not in manifest
    assert manifest["content_scripts"][0]["matches"] == ["*://*.youtube.com/*"]


def test_the_firefox_manifest_is_one_firefox_will_load():
    """Firefox runs an event page and wants an add-on id (F15)."""
    manifest = load_manifest("manifest.firefox.json")
    assert manifest["manifest_version"] == 3
    # Firefox has no extension service worker; a manifest asking for one is
    # refused outright, which is why the two builds differ here.
    assert "service_worker" not in manifest["background"]
    assert manifest["background"]["scripts"] == ["background.js"]
    gecko = manifest["browser_specific_settings"]["gecko"]
    assert "@" in gecko["id"], "a stable add-on id is what keeps updates attached"
    assert gecko["strict_min_version"] >= "109"
    # Since November 2025 Mozilla requires add-ons to declare this; this one
    # collects and sends nothing, which is the declaration for that.
    assert gecko["data_collection_permissions"] == {"required": ["none"]}
    assert {"contextMenus", "activeTab", "scripting"} <= set(manifest["permissions"])


def test_the_two_builds_describe_the_same_extension():
    """One code base, two manifests: only what the browsers disagree on differs."""
    chromium = load_manifest("manifest.json")
    firefox = load_manifest("manifest.firefox.json")
    for key in (
        "manifest_version",
        "name",
        "version",
        "description",
        "permissions",
        "content_scripts",
        "action",
        "icons",
    ):
        assert chromium[key] == firefox[key], key
    assert chromium["background"] != firefox["background"]
    assert set(chromium) ^ set(firefox) == {
        "author",
        "developer",
        "browser_specific_settings",
        "host_permissions",
        # Chromium's own floor; Firefox states its own under
        # ``browser_specific_settings`` instead.
        "minimum_chrome_version",
    }


def test_the_chromium_build_states_the_floor_it_needs():
    """A store listing that runs on 88 would break the hand-off (S7).

    ``chrome.scripting`` itself is Chrome 88+, but the ``args`` the click is
    injected with arrived in 92, and an extension installed on 88 would fail
    silently at the one thing it does.
    """
    manifest = load_manifest("manifest.json")
    assert manifest["minimum_chrome_version"] == "92"
    assert "minimum_chrome_version" not in load_manifest("manifest.firefox.json")


def test_the_background_script_states_and_keeps_its_promise():
    """It hands a URL over and reads nothing else (S7).

    The promise is written in the comment at the top of the file, so the
    assertions below are made against the code with the comments removed — a
    promise that its own words could satisfy would prove nothing.
    """
    source = (EXTENSION / "background.js").read_text(encoding="utf-8")
    assert "never reads a page" in source
    body = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    body = "\n".join(
        line for line in body.splitlines() if not line.strip().startswith("//")
    )
    for forbidden in ("storage", "tabs.query", "webRequest", "cookies", "nativeMessaging"):
        assert forbidden not in body, forbidden


def test_every_referenced_file_exists():
    for name in ("manifest.json", "manifest.firefox.json"):
        manifest = load_manifest(name)
        background = manifest["background"]
        referenced = [
            manifest["action"]["default_popup"],
            *manifest["icons"].values(),
            *background.get("scripts", []),
        ]
        if background.get("service_worker"):
            referenced.append(background["service_worker"])
        for block in manifest["content_scripts"]:
            referenced += block.get("js", [])
            referenced += block.get("css", [])
        for relative in referenced:
            assert (EXTENSION / relative).is_file(), f"{name}: missing {relative}"


def test_no_script_assumes_the_api_is_called_chrome():
    """A callback handed to Firefox's ``browser.*`` is ignored, not queued (F15)."""
    for name in ("background.js", "content-youtube.js", "popup.js"):
        lines = (EXTENSION / name).read_text(encoding="utf-8").splitlines()
        code = [
            line
            for line in lines
            if not line.strip().startswith(("//", "*", "/*"))
        ]
        body = "\n".join(code)
        shims = [line for line in code if "typeof browser !== 'undefined'" in line]
        assert len(shims) == 1, f"{name} needs exactly one browser/chrome choice"
        assert "chrome." not in body.replace(shims[0], ""), name
        assert "api." in body, name
        # ``lastError`` only exists for the callback style this file avoids.
        assert "lastError" not in body, name


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


@pytest.mark.skipif(NODE is None, reason="Node is needed to run the extension in a browser")
def test_the_extension_in_a_real_browser():
    """The popup, the YouTube button and the ``viddl://`` link, in Chromium (E8).

    Skipped, never passed, without Playwright and its browser: 120 MB of Chromium
    is not something every checkout has, and a green run that tested nothing is
    worse than an honest skip. The ``extension`` job in CI installs both and runs
    the file directly, where a skip is a failure.
    """
    if not PLAYWRIGHT.exists():
        pytest.skip(
            "Playwright is not installed: `npm install` and "
            "`npx playwright install chromium`"
        )
    environment = dict(os.environ)
    environment["HOME"] = str(REAL_HOME)
    environment.setdefault(
        "PLAYWRIGHT_BROWSERS_PATH", str(REAL_HOME / ".cache" / "ms-playwright")
    )
    result = subprocess.run(
        [NODE, "--test", str(BROWSER_TEST)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=environment,
        timeout=600,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_extension_folder_is_not_a_python_package():
    # It ships as package data, not as an importable module.
    assert not (EXTENSION / "__init__.py").exists()
    pyproject = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    assert "browser_extension/icons/*.png" in pyproject
