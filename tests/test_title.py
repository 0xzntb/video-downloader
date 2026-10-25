"""The window title: what it says, what it refuses to carry, and putting it back (U2).

The interesting cases are not "42% appears in a string". They are the ones a
title bar makes dangerous or useless: a name chosen by a website ending the
escape sequence early, a percentage hidden behind a long name by a task bar,
yt-dlp reporting a thousand times a second, and a terminal left saying "42%"
forever after the app has gone.
"""

from __future__ import annotations

import pytest

from viddl.tui import title

PUSH = title.PUSH
POP = title.POP


def recorder():
    """A stand-in for the terminal, keeping every sequence sent to it."""
    sent: list = []
    return sent, sent.append


# ---------------------------------------------------------------------------
# what it says
# ---------------------------------------------------------------------------


def test_a_download_reads_like_a_task_bar() -> None:
    assert (
        title.compose(42, "Killing Time.mp4") == "Viddl — 42% — Killing Time.mp4"
    )


def test_a_playlist_says_which_video_it_is_on() -> None:
    assert (
        title.compose(42, "Killing Time.mp4", position=3, total=9)
        == "Viddl — 42% — 3 of 9 — Killing Time.mp4"
    )


def test_the_number_comes_before_the_name() -> None:
    """A narrow title bar truncates the tail; the percentage is what matters."""
    text = title.compose(42, "x" * 200)

    assert text.index("42%") < text.index("x")
    assert len(text) <= title.TITLE_LIMIT
    assert text.endswith("…")


def test_a_recording_with_no_percentage_still_says_what_it_is() -> None:
    # A live stream has no total, so it has no percentage to show (F12).
    assert title.compose(None, "Killing Time") == "Viddl — Killing Time"


def test_nothing_to_say_is_the_apps_name() -> None:
    assert title.compose() == "Viddl"
    assert title.compose(None, "   ") == "Viddl"


def test_a_percentage_is_whole_and_inside_the_bar() -> None:
    assert title.compose(41.6).endswith("42%")
    assert title.compose(-20).endswith("0%")
    assert title.compose(140).endswith("100%")
    # A number that is not a number is no number at all: "nan%" would be a bug
    # the person reading it cannot do anything about.
    assert title.compose(float("nan")) == "Viddl"
    assert title.compose("half") == "Viddl"


# ---------------------------------------------------------------------------
# what it refuses to carry
# ---------------------------------------------------------------------------


def test_the_title_is_one_escape_sequence() -> None:
    assert title.sequence("Viddl") == "\x1b]0;Viddl\x07"


def test_a_name_from_a_site_cannot_end_the_sequence_early() -> None:
    """ESC and BEL are the sequence's own punctuation, so they never survive."""
    hostile = "Killing\x1b]0;pwned\x07 Time"

    data = title.sequence(hostile)

    assert "\x1b" not in data[1:-1]
    assert data.count("\x1b]0;") == 1
    assert data.count("\x07") == 1
    assert data.endswith(title.BEL)


def test_a_newline_becomes_a_space_rather_than_vanishing() -> None:
    assert title.clean("Killing\nTime") == "Killing Time"
    assert title.clean("Killing\t\tTime") == "Killing Time"


def test_the_characters_that_reorder_text_are_dropped() -> None:
    # Right-to-left override: "gnp\u202eexe" draws as "gnpexе"-reversed, which
    # is how a title bar can claim a file extension it does not have.
    assert "\u202e" not in title.clean("gnp\u202eexe.mp4")
    assert "\u2066" not in title.clean("a\u2066b")
    # A zero-width joiner is not one of them: an emoji family needs it.
    assert "\u200d" in title.clean("👨\u200d👩\u200d👧")


def test_a_long_name_is_shortened_rather_than_cut_mid_character() -> None:
    shortened = title.clean("é" * 400)

    assert shortened.endswith("…")
    assert len(shortened) == title.NAME_LIMIT


def test_a_name_of_only_junk_leaves_no_stray_separator() -> None:
    assert title.compose(42, "\x1b\x07\x00") == "Viddl — 42%"


def test_the_whole_title_has_a_ceiling() -> None:
    assert len(title.sequence(title.compose(1, "x" * 900))) <= (
        len(title.OSC) + title.TITLE_LIMIT + len(title.BEL)
    )


# ---------------------------------------------------------------------------
# writing it, and not writing it again
# ---------------------------------------------------------------------------


def test_starting_takes_the_title_and_says_the_apps_name() -> None:
    sent, write = recorder()
    site = title.TerminalTitle(write)

    site.start()

    assert sent == [PUSH, title.sequence("Viddl")]
    assert site.started is True
    assert site.current == "Viddl"


def test_starting_twice_does_not_push_twice() -> None:
    """A second push would bury the shell's own title under a second copy."""
    sent, write = recorder()
    site = title.TerminalTitle(write)

    site.start()
    site.start()

    assert sent.count(PUSH) == 1


def test_a_repeated_title_is_not_written_again() -> None:
    sent, write = recorder()
    site = title.TerminalTitle(write)
    site.start()
    sent.clear()

    site.show(42, "Killing Time.mp4")
    site.show(42, "Killing Time.mp4")
    site.show(41.6, "Killing Time.mp4")
    site.show(43, "Killing Time.mp4")

    # Two, not four: the repeated value and the same whole percentage are the
    # same title, and many terminals redraw their title bar for every write.
    assert sent == [
        title.sequence("Viddl — 42% — Killing Time.mp4"),
        title.sequence("Viddl — 43% — Killing Time.mp4"),
    ]


def test_a_fast_download_does_not_write_a_thousand_times() -> None:
    sent, write = recorder()
    site = title.TerminalTitle(write)
    site.start()
    sent.clear()

    for tenth in range(0, 1000):
        site.show(tenth / 10, "A Long Video")

    # yt-dlp reported a thousand times; the title is written once per whole
    # percentage, so 0% to 100% — a hundred and one writes, not a thousand.
    assert len(sent) == 101


def test_reset_puts_the_apps_own_name_back() -> None:
    sent, write = recorder()
    site = title.TerminalTitle(write)
    site.start()
    sent.clear()

    site.show(42, "Killing Time.mp4")
    site.reset()

    assert site.current == "Viddl"
    assert sent[-1] == title.sequence("Viddl")


# ---------------------------------------------------------------------------
# giving the title bar back
# ---------------------------------------------------------------------------


def test_closing_says_something_sane_and_then_pops() -> None:
    """The order matters for terminals that have no title stack."""
    sent, write = recorder()
    site = title.TerminalTitle(write)
    site.start()
    site.show(42, "Killing Time.mp4")
    sent.clear()

    site.close()

    assert sent == [title.sequence("Viddl"), POP]
    assert site.restored is True


def test_closing_twice_pops_only_once() -> None:
    """A second pop would restore a title the shell pushed for somebody else."""
    sent, write = recorder()
    site = title.TerminalTitle(write)
    site.start()
    site.close()
    sent.clear()

    site.close()

    assert sent == []


def test_nothing_is_written_after_the_title_has_been_given_back() -> None:
    """A screen torn down during shutdown must not paint over the restored title."""
    sent, write = recorder()
    site = title.TerminalTitle(write)
    site.start()
    site.close()
    sent.clear()

    site.show(42, "Killing Time.mp4")
    site.reset()
    site.start()

    assert sent == []


def test_a_site_that_was_never_started_is_not_popped() -> None:
    sent, write = recorder()
    site = title.TerminalTitle(write)

    site.close()

    assert sent == []


def test_the_writer_is_the_only_way_out() -> None:
    """Whatever the terminal does with a failed write is the app's business."""
    def broken(data: str) -> None:
        raise OSError("the terminal went away")

    site = title.TerminalTitle(broken)
    with pytest.raises(OSError):
        site.start()
