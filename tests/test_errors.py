"""Error messages have to be understandable to someone who is not a programmer."""

from __future__ import annotations

import pytest

from viddl.errors import explain


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("ERROR: Video unavailable", "not available"),
        ("ERROR: [youtube] BaW_jenozKc: This video is unavailable null", "not available"),
        ("ERROR: Private video. Sign in if you've been granted access", "private"),
        (
            "ERROR: Sign in to confirm you're not a bot. Use --cookies-from-browser",
            "signed in",
        ),
        ("ERROR: Unsupported URL: https://example.invalid/x", "No video could be found"),
        ("ERROR: HTTP Error 404: Not Found", "404"),
        (
            "ERROR: [youtube] x: Unable to download webpage: <urlopen error "
            "[Errno -3] Temporary failure in name resolution>",
            "internet",
        ),
        ("ERROR: You have requested merging of multiple formats but ffmpeg is not installed", "ffmpeg"),
        (
            "ERROR: Postprocessing: ffmpeg not found. Please install or provide the path "
            "using --ffmpeg-location",
            "ffmpeg",
        ),
        ("OSError: [Errno 28] No space left on device", "disk is full"),
        ("ERROR: Requested format is not available", "quality is no longer offered"),
    ],
)
def test_known_failures_get_a_friendly_message(raw, expected):
    friendly = explain(raw)
    assert expected in friendly.message
    assert friendly.message[0].isupper()


def test_messages_never_start_with_yt_dlp_boilerplate():
    friendly = explain("ERROR: [youtube] dQw4w9WgXcQ: Something odd: trait is broken")
    assert not friendly.message.startswith("ERROR")
    assert "ERROR:" not in friendly.message
    # The fallback text shown to the user is cleaned up too.
    assert not (friendly.hint or "").startswith("ERROR")
    assert "[youtube]" not in (friendly.hint or "")


@pytest.mark.parametrize(
    "raw",
    [
        "ERROR: [twitter] 123: No video could be found in this tweet",
        "ERROR: [Instagram] Unsupported URL: https://www.instagram.com/p/xyz/",
    ],
)
def test_a_video_less_link_says_so(raw):
    friendly = explain(raw)
    assert "No video could be found" in friendly.message
    assert "photo" in (friendly.hint or "")


def test_a_site_changing_its_pages_gets_plain_advice():
    friendly = explain(
        "ERROR: [TikTok] 7106594312: Unexpected response from webpage request"
    )
    assert "did not expect" in friendly.message
    assert "trying again" in (friendly.hint or "").lower()


def test_a_stray_null_from_the_downloader_is_not_shown():
    friendly = explain("ERROR: [somewhere] Something odd happened null")
    assert "null" not in (friendly.hint or "")


def test_unknown_errors_keep_a_readable_summary():
    raw = (
        "[debug] Command-line config: ['-f', 'best']\n"
        "ERROR: [generic] Some site said: trait is broken\n"
    )
    friendly = explain(raw)
    assert friendly.hint is not None
    assert "trait is broken" in (friendly.hint or "")
    # ...plus advice that is actually useful.
    assert "Trying again" in (friendly.hint or "")


def test_empty_output_with_failure_code_is_still_friendly():
    friendly = explain("", exit_code=1)
    assert friendly.message
    assert friendly.hint


def test_a_missing_ffmpeg_is_never_reported_as_a_missing_web_page():
    # "not found" used to match the 404 rule first, which was nonsense.
    friendly = explain(
        "ERROR: Postprocessing: ffmpeg not found. Please install or provide the path "
        "using --ffmpeg-location"
    )
    assert "404" not in friendly.message
    assert "ffmpeg" in friendly.message
    assert friendly.hint is not None and "Set up ffmpeg" in friendly.hint


def test_a_real_404_still_reads_as_a_404():
    friendly = explain("ERROR: HTTP Error 404: Not Found")
    assert "404" in friendly.message


def test_rule_order_prefers_the_specific_explanation():
    # Mentions both "cookies" (bot check) and a generic failure.
    friendly = explain("ERROR: Sign in to confirm you're not a bot (cookies required)")
    assert "signed in" in friendly.message
