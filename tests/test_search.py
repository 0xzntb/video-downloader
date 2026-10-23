"""A name typed instead of a link (F14).

The rule under test is small and has to hold in three places at once — the
command line, ``--plain`` and the app — so it lives in one module and every test
here asks the same question of it: is this text a link, a name, or neither?
"""

from __future__ import annotations

import pytest

from viddl import search
from viddl.formats import MediaInfo, parse_info
from viddl.util import safe_video_link

URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_a_name_becomes_the_engines_own_search_question():
    assert search.target_for("some song") == "ytsearch1:some song"


def test_a_name_is_collapsed_into_one_line():
    """A title pasted from a page arrives wrapped; it is still one name."""
    assert search.target_for("  some \n  song  ") == "ytsearch1:some song"


def test_punctuation_a_name_really_has_is_kept():
    assert search.target_for("AC/DC: live at Donington") == (
        "ytsearch1:AC/DC: live at Donington"
    )


def test_a_link_is_left_exactly_as_it_was():
    assert search.target_for(URL) == URL
    assert search.target_for("www.example.com/watch?v=x") == (
        "https://www.example.com/watch?v=x"
    )


def test_an_option_shaped_name_is_refused():
    """A dash would be a flag to the downloader, so it is never a name."""
    assert search.target_for("--exec=calc.exe") is None
    assert search.target_for(" -x") is None


def test_a_path_is_a_mistake_rather_than_a_name():
    assert search.target_for("/tmp/a video.mp4") is None
    assert search.target_for("~/Videos/a.mp4") is None
    assert search.target_for("./a.mp4") is None


def test_something_with_a_scheme_is_not_a_name():
    assert search.target_for("file:///etc/passwd") is None
    assert search.target_for("javascript:alert(1)") is None
    assert search.target_for("http://") is None


def test_a_control_character_is_refused():
    assert search.target_for("some\x00song") is None
    assert search.target_for("some\x07song") is None


def test_punctuation_alone_is_not_a_name():
    for text in ("", "   ", "...", "???", "-"):
        assert search.target_for(text) is None


def test_an_enormous_paste_is_not_a_name():
    assert search.target_for("a" * search.MAX_QUERY) is not None
    assert search.target_for("a" * (search.MAX_QUERY + 1)) is None


def test_a_target_that_is_already_a_search_is_not_prefix_twice():
    target = "ytsearch1:some song"
    assert search.target_for(target) == target
    assert search.target_for("YTSEARCH1:some song") == "ytsearch1:some song"


def test_an_empty_search_is_refused():
    assert search.target_for("ytsearch1:") is None
    assert search.target_for("ytsearch1:   ") is None


def test_is_search_and_query_from_read_a_target_back():
    assert search.is_search("ytsearch1:some song") is True
    assert search.is_search(URL) is False
    assert search.query_from("ytsearch1:some song") == "some song"
    assert search.query_from(URL) == URL


def test_display_text_puts_the_name_back_in_the_box():
    assert search.display_text("ytsearch1:some song") == "some song"
    assert search.display_text(URL) == URL


def test_a_query_is_never_more_than_one_argument():
    """The safety story, in one assertion: no dash, no space-splitting."""
    target = search.target_for("some --exec=calc song")
    assert target is not None
    assert target.startswith(search.PREFIX)
    assert " " in target  # one string, spaces and all
    assert not target.startswith("-")


def test_a_search_that_answered_is_downloaded_as_the_video_it_found():
    info = parse_info({"title": "Found", "webpage_url": URL, "formats": []})
    assert search.resolved(info, "ytsearch1:some song") == URL


def test_a_search_with_no_link_of_its_own_stays_the_query():
    info = MediaInfo(url="", title="Found")
    assert search.resolved(info, "ytsearch1:some song") == "ytsearch1:some song"


def test_a_link_is_never_rewritten_by_a_lookup():
    info = parse_info({"title": "Found", "webpage_url": URL, "formats": []})
    assert search.resolved(info, "https://example.com/a") == "https://example.com/a"


def test_the_plain_line_says_what_was_asked_and_that_one_answer_is_used():
    line = search.looking_note("ytsearch1:some song")
    assert line == 'Searching for "some song" - the first result is used.'
    line.encode("cp1252")  # the plain path promises this


def test_a_link_is_still_looked_up_by_its_link():
    assert search.looking_note(URL) == f"Looking up {URL}"


@pytest.mark.parametrize("name", ["some song", "AC/DC", "Café del Mar"])
def test_a_name_only_ever_holds_what_safe_video_link_would_refuse(name):
    """The two rules do not overlap: a name is never something a link could be."""
    assert safe_video_link(name) is None
    assert search.target_for(name) is not None
