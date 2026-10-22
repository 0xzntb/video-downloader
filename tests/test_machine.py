"""The JSON answers a script reads (F10).

Two promises are held here: the object says the same thing the sentence says —
it is built from the same places, not parsed out of the text — and the shape does
not wobble (a missing size is ``null``, never ``0``; a missing engine is
``null``, never ``""``).
"""

from __future__ import annotations

import json
from pathlib import Path

from viddl import machine
from viddl.engine import Tool
from viddl.formats import PlaylistSummary, parse_info

URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

INFO_JSON = {
    "title": "A Very Real Video",
    "uploader": "Some Channel",
    "duration": 212,
    "webpage_url": URL,
    "extractor_key": "Youtube",
    "formats": [
        {"format_id": "140", "ext": "m4a", "audio_ext": "m4a", "vcodec": "none",
         "acodec": "mp4a", "abr": 128, "filesize": 1600000},
        {"format_id": "137", "ext": "mp4", "vcodec": "avc1", "acodec": "none",
         "height": 1080, "fps": 30, "vbr": 5000, "filesize": 20000000},
        {"format_id": "18", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a",
         "height": 360, "tbr": 700, "filesize": 9000000},
    ],
}


class StubEngine:
    """Just enough of ``Engine`` for a report: a home and two tools."""

    def __init__(self, ytdlp=None, ffmpeg=None):
        self.home = Path("/tmp/viddl-machine-tests")
        self.ytdlp = ytdlp
        self.ffmpeg = ffmpeg

    def find_ytdlp(self):
        return self.ytdlp

    def find_ffmpeg(self):
        return self.ffmpeg


def test_the_qualities_report_is_the_list_the_screen_shows():
    info = parse_info(INFO_JSON)

    report = machine.qualities_report(info)

    assert report["url"] == URL
    assert report["title"] == "A Very Real Video"
    assert report["duration"] == 212
    # Same order as the menu: the first row is what Enter would pick.
    assert [row["key"] for row in report["qualities"]] == [o.key for o in info.options]
    first = report["qualities"][0]
    assert first["label"] == info.options[0].label
    assert set(first) >= {
        "key", "label", "kind", "height", "size_bytes", "size_is_estimate",
        "needs_ffmpeg", "format_selector", "audio_format", "keeps_source_audio", "note",
    }
    assert report["playlist"] is None
    assert report["live"] is False


def test_an_unknown_size_is_null_and_never_zero():
    """``0`` would say "an empty file"; a script must be able to tell."""
    info = parse_info(INFO_JSON)
    info.options[0].size_bytes = None

    row = machine.qualities_report(info)["qualities"][0]

    assert row["size_bytes"] is None
    assert row["size_is_estimate"] is False


def test_a_playlist_link_says_so_and_how_many():
    info = parse_info(INFO_JSON)
    info.playlist = PlaylistSummary(title="A playlist", count=4, url=URL)
    info.playlist_note = "A playlist of 4 videos"

    report = machine.qualities_report(info)

    assert report["playlist"] == {
        "title": "A playlist",
        "count": 4,
        "url": URL,
        "note": "A playlist of 4 videos",
    }


def test_the_doctor_report_carries_what_the_sentences_carry():
    engine = StubEngine(
        ytdlp=Tool("yt-dlp", ["/fake/yt-dlp"], "system", version="2099.01.01"),
        ffmpeg=Tool("ffmpeg", ["/fake/ffmpeg"], "viddl", version="7.0"),
    )

    report = machine.doctor_report(engine)

    assert report["name"] == "Viddl"
    assert report["version"]
    assert report["folders"]["app"] == str(engine.home)
    assert report["folders"]["log"].endswith("viddl.log")
    # The download folder comes from the environment the tests set up.
    assert report["folders"]["downloads"].endswith("Downloads")
    assert report["problem"] is None
    assert report["ytdlp"]["version"] == "2099.01.01"
    assert report["ytdlp"]["origin"] == "system"
    assert report["ffmpeg"]["origin"] == "viddl"
    assert isinstance(report["environment"], list)
    # Not set is null, not zero (R11).
    assert report["timeout_seconds"] is None
    assert report["archive"]["enabled"] is False
    assert report["archive"]["path"].endswith("download-archive.txt")
    assert report["recent"]["path"].endswith("recent.json")
    assert isinstance(report["cookies"]["browsers"], list)


def test_a_missing_engine_is_null_rather_than_an_empty_sentence():
    report = machine.doctor_report(StubEngine())

    assert report["ytdlp"] is None
    assert report["ffmpeg"] is None
    assert report["ytdlp_note"] == ""


def test_dumps_is_sorted_indented_and_ends_with_one_newline():
    text = machine.dumps({"b": 2, "a": {"d": 4, "c": 3}})

    assert text.endswith("\n") and not text.endswith("\n\n")
    assert text.index('"a"') < text.index('"b"')
    assert text.index('"c"') < text.index('"d"')
    assert "\n  " in text
    assert json.loads(text) == {"b": 2, "a": {"d": 4, "c": 3}}


def test_a_title_outside_ascii_survives_the_round_trip():
    """JSON is UTF-8: a title does not need the plain path's '?' here."""
    info = parse_info(INFO_JSON)
    info.title = "日本語のビデオ"

    report = json.loads(machine.dumps(machine.qualities_report(info)))

    assert report["title"] == "日本語のビデオ"
