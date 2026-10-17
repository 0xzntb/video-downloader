"""Formatting helpers used all over the UI."""

from __future__ import annotations

import pytest

from viddl.util import (
    REDACTED,
    SECRET_PARAMS,
    human_duration,
    human_size,
    is_probably_url,
    redact,
    safe_video_link,
    shorten,
)


def test_human_size():
    assert human_size(None) == "?"
    assert human_size(0) == "?"
    assert human_size(512) == "512 B"
    assert human_size(1024) == "1.0 KB"
    assert human_size(1024 * 1024) == "1.0 MB"
    assert human_size(3_400_000_000) == "3.2 GB"


def test_human_duration():
    assert human_duration(None) == "?"
    assert human_duration(0) == "?"
    assert human_duration(45) == "0:45"
    assert human_duration(212) == "3:32"
    assert human_duration(3725) == "1:02:05"


def test_shorten_collapses_whitespace():
    assert shorten("  a   b  ", 10) == "a b"
    assert shorten("abcdefghij", 5) == "abcd…"
    assert shorten("short", 10) == "short"


def test_is_probably_url_accepts_links_and_rejects_sentences():
    assert is_probably_url("https://www.youtube.com/watch?v=abc")
    assert is_probably_url("http://example.com")
    assert is_probably_url("www.example.com/watch?v=x")
    assert not is_probably_url("")
    assert not is_probably_url("hello there")
    assert not is_probably_url("/usr/local/bin")


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Plain links come back unchanged (and trimmed).
        ("https://www.youtube.com/watch?v=abc", "https://www.youtube.com/watch?v=abc"),
        ("http://example.com/v", "http://example.com/v"),
        ("  https://example.com/v  ", "https://example.com/v"),
        ('"https://example.com/v"', "https://example.com/v"),
        ("https://user:pw@example.com/v", "https://user:pw@example.com/v"),
        # A bare site is upgraded rather than refused: browsers sometimes hand
        # the link over without its scheme.
        ("www.example.com/watch?v=x", "https://www.example.com/watch?v=x"),
        ("example.com:8080/v", "https://example.com:8080/v"),
        # Anything option-shaped must never get through (see the note below).
        ("--exec=calc.exe", None),
        ("--config-locations=./x.conf", None),
        ("--paths=./out", None),
        ("--download-archive=./a.txt", None),
        ("-o/tmp/x.mp4", None),
        # Other schemes, local paths and prose.
        ("javascript:alert(1)", None),
        ("file:///etc/passwd", None),
        ("data:text/html,x", None),
        ("ftp://example.com/v", None),
        ("/usr/local/bin", None),
        ("~/videos", None),
        ("./relative", None),
        ("hello there", None),
        # Control characters must not survive into a command line either.
        ("https://example.com/v\x00evil", None),
        ("https://example.com/v\x7f", None),
        ("https://", None),
        ("", None),
        ("   ", None),
        ("https://example.com/" + "a" * 3000, None),
    ],
)
def test_safe_video_link_returns_only_plain_links(raw, expected):
    assert safe_video_link(raw) == expected


def test_redact_keeps_a_link_worth_reading():
    """Only the credentials go: what was fetched, and from where, stays."""
    line = "could not fetch https://cdn.example.com/v?token=SECRET&v=dQw4w9WgXcQ"
    cleaned = redact(line)
    assert "SECRET" not in cleaned
    assert f"token={REDACTED}" in cleaned
    assert "cdn.example.com/v" in cleaned
    assert "v=dQw4w9WgXcQ" in cleaned


def test_redact_leaves_the_parameters_that_are_not_secrets():
    for line in (
        "https://www.youtube.com/watch?v=abc&list=PL123&t=42",
        "https://example.com/a/b/c.mp4",
        "a sentence with no link in it at all",
        "",
    ):
        assert redact(line) == line


def test_redact_covers_the_names_it_documents():
    assert len(SECRET_PARAMS) == len(set(SECRET_PARAMS))
    for name in SECRET_PARAMS:
        cleaned = redact(f"https://example.com/v?{name}=hunter2")
        assert "hunter2" not in cleaned, name
        assert f"{name}={REDACTED}" in cleaned, name


def test_redact_ignores_case_and_a_percent_encoded_separator():
    """A hand-off link arrives with its inner link percent-encoded."""
    hand_off = "viddl://download?url=https%3A%2F%2Fx%2Fv%3Ftoken%3Dhunter2"
    assert "hunter2" not in redact("https://x/v?TOKEN=hunter2")
    assert "hunter2" not in redact("https://x/v?Sig=hunter2")
    assert "hunter2" not in redact(hand_off)


def test_redact_removes_a_login_from_a_link():
    cleaned = redact("https://someone:hunter2@example.com/v")
    assert "hunter2" not in cleaned
    assert cleaned == f"https://{REDACTED}@example.com/v"


def test_redact_covers_a_bearer_token():
    cleaned = redact("headers={'Authorization': 'Bearer abcdef1234567890'}")
    assert "abcdef1234567890" not in cleaned
    assert f"Bearer {REDACTED}" in cleaned


def test_is_probably_url_refuses_option_shaped_input():
    """The looseness that let a flag through as a "link" is gone.

    Everything here used to be accepted, and the string then travelled all the
    way into the downloader's argument list where it was read as an option.
    """
    for payload in ("--exec=calc.exe", "--config-locations=./x.conf", "--paths=./out"):
        assert not is_probably_url(payload)
        assert safe_video_link(payload) is None
