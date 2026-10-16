"""Formatting helpers used all over the UI."""

from __future__ import annotations

from viddl.util import human_duration, human_size, is_probably_url, shorten


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
