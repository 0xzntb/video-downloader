"""Which browsers Viddl can read from, and what it says before it does (F1).

The site calls that need this are the ones that answer only to a signed-in
session: an age-restricted video, a bot check, Instagram, Threads. yt-dlp can
read the cookies the browser already has, so the feature is real — but it is
also the one thing in Viddl that touches an account's files, which is why the
sentences matter as much as the browser table and are tested with it.
"""

from __future__ import annotations

import pytest

from viddl import cookies


def test_only_the_browsers_yt_dlp_understands_are_offered():
    """The list is closed, because a name here reaches a command line."""
    assert cookies.BROWSER_KEYS == ("chrome", "firefox", "edge", "brave", "safari")
    assert set(cookies.LABELS) == set(cookies.BROWSER_KEYS)


def test_the_arguments_are_the_two_yt_dlp_takes():
    assert cookies.cookies_args("chrome") == ["--cookies-from-browser", "chrome"]
    assert cookies.cookies_args(" Chrome ") == ["--cookies-from-browser", "chrome"]


@pytest.mark.parametrize(
    "value",
    ["", "   ", "netscape", "--exec=calc.exe", "chrome; rm -rf /", "chrome.exe", "-f"],
)
def test_anything_that_is_not_a_browser_becomes_no_arguments(value):
    """The safe reading of "I do not recognise that" is to read nothing."""
    assert cookies.cookies_args(value) == []


def test_a_windows_browser_is_found_where_windows_keeps_it(tmp_path):
    appdata = tmp_path / "AppData" / "Local"
    (appdata / "Google" / "Chrome" / "User Data").mkdir(parents=True)
    assert cookies.installed_keys(
        platform="win32", home=tmp_path, env={"LOCALAPPDATA": str(appdata)}
    ) == ["chrome"]


def test_a_windows_browser_is_not_guessed_at_without_the_variable(tmp_path):
    """``%LOCALAPPDATA%`` missing means the path is unknown, not a bare name."""
    assert cookies.places_for("chrome", platform="win32", home=tmp_path, env={}) == ()
    assert cookies.installed_keys(platform="win32", home=tmp_path, env={}) == []


def test_safari_is_offered_on_a_mac_and_nowhere_else(tmp_path):
    (tmp_path / "Library" / "Cookies").mkdir(parents=True)
    assert cookies.installed_keys(platform="darwin", home=tmp_path, env={}) == ["safari"]
    assert cookies.installed_keys(platform="linux", home=tmp_path, env={}) == []
    assert (
        cookies.problem_with("safari", platform="linux", home=tmp_path, env={})
        == "Safari is not available on this system."
    )


def test_the_browsers_that_are_there_are_offered_in_order(tmp_path):
    (tmp_path / ".mozilla" / "firefox").mkdir(parents=True)
    (tmp_path / ".config" / "google-chrome").mkdir(parents=True)

    found = cookies.installed_keys(platform="linux", home=tmp_path, env={})

    assert found == ["chrome", "firefox"], "the table's order, not the disk's"
    labels = [browser.label for browser in cookies.installed(platform="linux", home=tmp_path, env={})]
    assert labels == ["Chrome", "Firefox"]


def test_a_browser_that_is_not_installed_is_still_a_place_to_look(tmp_path):
    """"Not installed" is worth saying, so the table keeps it (F1)."""
    listed = cookies.browsers(platform="linux", home=tmp_path, env={})
    assert {browser.key for browser in listed} == {
        "chrome",
        "firefox",
        "edge",
        "brave",
    }
    assert not any(browser.installed for browser in listed)


def test_auto_takes_the_first_browser_that_is_installed(tmp_path):
    (tmp_path / ".config" / "BraveSoftware" / "Brave-Browser").mkdir(parents=True)
    (tmp_path / ".mozilla" / "firefox").mkdir(parents=True)

    assert (
        cookies.default_for("auto", platform="linux", home=tmp_path, env={}) == "firefox"
    )
    assert cookies.default_for("AUTO", platform="linux", home=tmp_path, env={}) == "firefox"


def test_auto_with_nothing_installed_chooses_nothing(tmp_path):
    """A machine with no browser must not become a lookup at a guessed path."""
    assert cookies.default_for("auto", platform="linux", home=tmp_path, env={}) == ""
    assert cookies.default_for("", platform="linux", home=tmp_path, env={}) == ""
    assert cookies.cookies_args("") == []


def test_a_named_browser_is_used_without_looking_for_it(tmp_path):
    """A portable install, or a store somewhere unusual, still works."""
    assert (
        cookies.default_for("firefox", platform="linux", home=tmp_path, env={}) == "firefox"
    )


def test_an_unknown_name_says_what_is_known():
    problem = cookies.problem_with("netscape")
    assert "not a browser Viddl knows" in problem
    for key in cookies.BROWSER_KEYS:
        assert key in problem


def test_what_asking_means_is_said_before_the_click():
    note = cookies.note("Chrome")
    assert "Chrome" in note
    assert "cookies it already has" in note
    assert "for this download only" in note
    assert "password is never read" in note
    assert "nothing is saved" in note


def test_what_is_happening_is_said_while_it_happens():
    note = cookies.active_note("Firefox")
    assert "Using Firefox's cookies" in note
    assert "Nothing was stored" in note
    assert "turn it off" in note


def test_a_failed_read_says_what_to_try():
    note = cookies.failed_note("Brave")
    assert "could not read Brave's cookies" in note
    assert "Close Brave" in note
    assert "different" in note


def test_the_doctor_says_which_browsers_could_sign_a_download_in(tmp_path):
    assert "no browser found" in cookies.doctor_note(
        platform="linux", home=tmp_path, env={}
    )

    (tmp_path / ".mozilla" / "firefox").mkdir(parents=True)
    line = cookies.doctor_note(platform="linux", home=tmp_path, env={})
    assert "Firefox" in line
    assert "--cookies-from-browser auto" in line


def test_the_notes_are_ascii():
    """The plain path prints these, on a console that may not have UTF-8."""
    for text in (
        cookies.note("Chrome"),
        cookies.active_note("Chrome"),
        cookies.failed_note("Chrome"),
        cookies.doctor_note(),
    ):
        text.encode("ascii")

def test_the_advice_matches_the_refusal(tmp_path):
    """Telling someone on Linux to name Safari again is not a hint (F1)."""
    assert cookies.hint_for("netscape") == "Try: viddl --cookies-from-browser auto <link>"

    hint = cookies.hint_for("safari", platform="linux", home=tmp_path, env={})
    assert "not available on this system" in hint
    assert "auto" in hint

    # The same browser on the platform that has it is not a refusal at all.
    assert cookies.hint_for("safari", platform="darwin", home=tmp_path, env={}).startswith(
        "Try:"
    )
