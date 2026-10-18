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


# ---------------------------------------------------------------------------
# temporary, or permanent? (R5)
#
# The answer decides whether the download is tried again by itself, so a wrong
# answer is expensive in both directions: retrying a private video wastes the
# user's time, and calling a rate limit permanent hands them a failure that a
# second attempt would have fixed.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "ERROR: HTTP Error 429: Too Many Requests",
        "ERROR: [youtube] x: 429 Client Error: Too Many Requests",
        "ERROR: HTTP Error 503: Service Unavailable",
        "ERROR: HTTP Error 502: Bad Gateway",
        "ERROR: Unable to download webpage: HTTP Error 500: Internal Server Error",
        "ERROR: The read operation timed out",
        "ERROR: [youtube] x: Unable to download webpage: <urlopen error [Errno 111] Connection refused>",
        "ERROR: Unable to download webpage: <urlopen error [Errno -3] Temporary failure in name resolution>",
        "ERROR: Connection reset by peer",
        "ERROR: HTTP Error 403: Forbidden",
    ],
)
def test_failures_a_second_attempt_may_fix_are_temporary(raw):
    assert explain(raw).temporary is True


@pytest.mark.parametrize(
    "raw",
    [
        "ERROR: Private video. Sign in if you've been granted access",
        "ERROR: Video unavailable",
        "ERROR: The uploader has not made this video available in your country",
        "ERROR: HTTP Error 404: Not Found",
        "ERROR: Unsupported URL: https://example.invalid/x",
        "ERROR: Requested format is not available",
        "OSError: [Errno 28] No space left on device",
        "ERROR: Permission denied: /downloads/x.mp4",
        "ERROR: Postprocessing: ffmpeg not found",
        "ERROR: [youtube] x: This video is members-only",
        "ERROR: Interrupted by user",
    ],
)
def test_failures_that_will_not_change_on_their_own_are_permanent(raw):
    assert explain(raw).temporary is False


def test_a_region_block_is_never_retried_as_a_network_moment():
    friendly = explain(
        "ERROR: [youtube] abc: The uploader has not made this video available in your country"
    )
    assert "not available" in friendly.message
    assert friendly.temporary is False


def test_an_unrecognised_error_is_retried_because_its_advice_says_so():
    # The explanation already tells the user to try again. Retrying once on
    # their behalf is the same advice, only sooner.
    friendly = explain("ERROR: [generic] Some site said: trait is broken")
    assert "Trying again" in (friendly.hint or "")
    assert friendly.temporary is True


def test_a_stopped_download_with_no_output_is_treated_as_temporary():
    assert explain("", exit_code=1).temporary is True
    # With no exit code and no output there is nothing to suggest a retry helps.
    assert explain("").temporary is False
