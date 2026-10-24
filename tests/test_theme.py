"""The colour schemes, the contrast maths, and who decides which one wins (U1).

Two kinds of test live here. The first checks a claim the item made — that the
colours are chosen by measurement rather than by eye — by computing the WCAG
ratio for every pair each scheme has to be readable in. The second drives the
real app with Textual's pilot: the row on the home screen, the key that cycles
it, ``NO_COLOR``, and the one place a colour is not in the stylesheet (the size
column of a quality row).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pytest
from textual.app import App, ComposeResult

from viddl import theme
from viddl.engine import Tool
from viddl.tui.app import Services, ViddlApp
from viddl.tui.widgets import ChoiceList, ChoiceRow


# ---------------------------------------------------------------------------
# the arithmetic
# ---------------------------------------------------------------------------


def test_contrast_of_black_on_white_is_the_maximum() -> None:
    assert theme.contrast("#000000", "#ffffff") == pytest.approx(21.0, abs=0.01)
    assert theme.contrast("#ffffff", "#000000") == pytest.approx(21.0, abs=0.01)
    # The ratio is symmetric: which one is called the foreground is not a
    # question the standard asks.
    assert theme.contrast("#ffffff", "#000000") == theme.contrast("#000000", "#ffffff")


def test_the_same_colour_has_no_contrast() -> None:
    assert theme.contrast("#777777", "#777777") == pytest.approx(1.0)
    assert theme.contrast("#ffffff", "#ffffff") == pytest.approx(1.0)


def test_luminance_runs_from_zero_to_one() -> None:
    assert theme.luminance("#000000") == pytest.approx(0.0)
    assert theme.luminance("#ffffff") == pytest.approx(1.0)
    # Green is the channel the eye is most sensitive to, so pure green is
    # brighter than pure red or pure blue by the same formula the standard uses.
    assert theme.luminance("#00ff00") > theme.luminance("#ff0000")
    assert theme.luminance("#ff0000") > theme.luminance("#0000ff")


def test_a_colour_that_is_not_hex_is_refused() -> None:
    with pytest.raises(ValueError):
        theme.luminance("#12345")
    with pytest.raises(ValueError):
        theme.luminance("red")
    with pytest.raises(ValueError):
        theme.luminance("#gggggg")


# ---------------------------------------------------------------------------
# the schemes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", theme.SCHEMES)
def test_every_pair_clears_its_minimum(name: str) -> None:
    """The claim of the item: read off the maths, not off the screen."""
    failures = theme.problems(name)
    assert failures == []


@pytest.mark.parametrize("name", theme.SCHEMES)
def test_the_minimums_are_the_ones_the_standard_names(name: str) -> None:
    strict = theme.PALETTES[name].strict
    for pair, ratio, minimum in theme.checks(name):
        expected = (
            theme.STRICT_READABLE
            if strict and pair.minimum >= theme.READABLE
            else theme.STRICT_GRAPHIC
            if strict
            else pair.minimum
        )
        assert minimum == expected
        assert ratio >= minimum


def test_high_contrast_holds_itself_to_seven_to_one() -> None:
    """A scheme called "high contrast" that only reached 4.5:1 would be a lie."""
    text = [
        ratio
        for pair, ratio, _ in theme.checks(theme.HIGH)
        if pair.minimum >= theme.READABLE
    ]
    assert text
    assert min(text) >= theme.STRICT_READABLE


def test_the_dark_scheme_is_the_one_the_app_already_drew() -> None:
    """The default changed the mechanism, not the look: same page and accent."""
    dark = theme.PALETTES[theme.DARK]
    assert dark.dark is True
    assert dark.background == "#121212"
    assert dark.accent == "#ffa62b"


def test_the_worst_text_ratio_ignores_the_graphics_pairs() -> None:
    """A border is allowed 3:1, so it must not be the doctor's headline number."""
    text_only = [
        ratio
        for pair, ratio, _ in theme.checks(theme.DARK)
        if pair.minimum >= theme.READABLE
    ]
    assert theme.worst_text_ratio(theme.DARK) == min(text_only)
    assert theme.worst_text_ratio(theme.DARK) > theme.GRAPHIC


def test_the_schemes_become_textual_themes() -> None:
    names = [one.name for one in theme.THEMES]
    assert names == [theme.THEME_PREFIX + name for name in theme.SCHEMES]
    for one in theme.THEMES:
        assert set(theme.default_variables()) <= set(one.variables)
        # The three the stylesheet uses by name, so a scheme cannot leave the
        # stylesheet with a variable it does not define.
        for variable in ("text", "text-muted", "on-primary", "size-foreground"):
            assert variable in one.variables


def test_the_theme_is_named_by_a_person_not_by_the_stylesheet() -> None:
    assert theme.title("high-contrast") == "High contrast"
    assert theme.title("light") == "Light"


# ---------------------------------------------------------------------------
# picking one
# ---------------------------------------------------------------------------


def test_the_usual_spellings_are_the_same_scheme() -> None:
    assert theme.clean("light") == theme.LIGHT
    assert theme.clean(" Light ") == theme.LIGHT
    assert theme.clean("HIGH") == theme.HIGH
    assert theme.clean("high") == theme.HIGH
    assert theme.clean("high_contrast") == theme.HIGH
    assert theme.clean("auto") == theme.AUTO


def test_a_theme_that_does_not_exist_is_refused_with_the_list() -> None:
    with pytest.raises(ValueError) as caught:
        theme.clean("banana")
    message = str(caught.value)
    assert "'banana'" in message
    for name in ("dark", "light", "high-contrast", "auto"):
        assert name in message


def test_the_flag_beats_the_variable_beats_the_file() -> None:
    env = {"VIDDL_THEME": "light"}
    assert theme.choose("high-contrast", "dark", env).name == theme.HIGH
    assert theme.choose("", "dark", env).name == theme.LIGHT
    assert theme.choose("", "", {"VIDDL_THEME": ""}).name == theme.DARK
    # Set but empty is still an answer: it means auto, and the file does not
    # get a say after it.
    assert theme.choose("", "light", {"VIDDL_THEME": ""}).name == theme.DARK
    assert theme.choose("", "light", {}).name == theme.LIGHT


def test_each_answer_says_where_it_came_from() -> None:
    assert theme.choose("light").source == "--theme"
    assert theme.choose("", "", {"VIDDL_THEME": "light"}).source == "VIDDL_THEME"
    assert theme.choose("", "light", {}).source == "the settings file"
    assert theme.choose("", "", {}).source == "the default"


def test_auto_follows_the_terminal_background() -> None:
    # COLORFGBG is foreground;background, so 0;15 is black on white.
    assert theme.choose("auto", "", {"COLORFGBG": "0;15"}).name == theme.LIGHT
    assert theme.choose("auto", "", {"COLORFGBG": "15;0"}).name == theme.DARK
    assert theme.choose("", "", {"COLORFGBG": "15;0"}).name == theme.DARK
    # A background number of 8 or more is one of the light ANSI colours.
    assert theme.choose("auto", "", {"COLORFGBG": "0;8"}).name == theme.LIGHT
    assert theme.choose("auto", "", {"COLORFGBG": "0;7"}).name == theme.DARK
    assert "the terminal's background" in theme.choose(
        "auto", "", {"COLORFGBG": "0;15"}
    ).summary


def test_a_terminal_that_says_nothing_gets_the_default() -> None:
    assert theme.choose("", "", {}).summary.startswith("dark (the default")
    assert "the terminal's background" not in theme.choose("auto", "", {}).summary
    # Rubbish in COLORFGBG is not a hint, and must not become a crash.
    assert theme.choose("auto", "", {"COLORFGBG": "yes"}).name == theme.DARK
    assert theme.choose("auto", "", {"COLORFGBG": ""}).name == theme.DARK
    assert theme.choose("auto", "", {"COLORFGBG": "1;2;3"}).name == theme.DARK


def test_no_color_is_reported_rather_than_pretended_away() -> None:
    for value in ("1", "0", ""):
        chosen = theme.choose("light", "", {"NO_COLOR": value})
        assert chosen.name == theme.LIGHT
        assert chosen.colour is False
        assert chosen.note == "NO_COLOR is set"
        assert "colour is off" in chosen.summary
    assert theme.choose("light", "", {}).colour is True
    assert theme.choose("light", "", {}).note == ""


def test_term_dumb_is_recognised() -> None:
    assert theme.terminal_is_dumb({"TERM": "dumb"}) is True
    assert theme.terminal_is_dumb({"TERM": "DUMB"}) is True
    assert theme.terminal_is_dumb({"TERM": "xterm-256color"}) is False
    assert theme.terminal_is_dumb({}) is False


def test_the_doctor_line_survives_a_bad_value() -> None:
    line = theme.doctor_line("light", "", {})
    assert line.startswith("Theme:")
    assert "light" in line and "--theme" in line
    assert "smallest text contrast" in line

    bad = theme.doctor_line("", "banana", {})
    assert bad.startswith("Theme:")
    assert "not usable" in bad and "not a theme" in bad

    dumb = theme.doctor_line("", "", {"TERM": "dumb"})
    assert "TERM=dumb" in dumb and "--plain" in dumb


# ---------------------------------------------------------------------------
# the app
# ---------------------------------------------------------------------------


class FakeEngine:
    """Just enough engine to let the home screen finish setting itself up."""

    def ensure_ytdlp(self, on_status=None, on_progress=None, allow_download=True):
        if on_status:
            on_status("Ready")
        return Tool("yt-dlp", ["/fake/yt-dlp"], "system", version="2099.01.01")

    def find_ffmpeg(self):
        return None


def home_app(theme_name: str = "") -> ViddlApp:
    services = Services(
        engine=FakeEngine(),
        downloads_dir=Path,
        clipboard=lambda: "",
    )
    return ViddlApp(theme_name=theme_name, services=services)


async def test_the_app_opens_in_the_chosen_scheme(monkeypatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("COLORFGBG", raising=False)
    app = home_app("light")
    assert app.theme == "viddl-light"
    assert app.theme_title == "Light"
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        light = theme.PALETTES[theme.LIGHT]
        assert app.theme_variables["text"].lower() == light.text
        assert app.theme_variables["on-primary"].lower() == light.on_primary
        assert app.current_theme.dark is False
        assert "Light" in str(app.screen.query_one("#theme-row").render())


async def test_the_home_row_names_the_scheme_and_cycles(monkeypatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    app = home_app()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        row = app.screen.query_one("#theme-row")
        assert "Dark" in str(row.render())
        await pilot.press("ctrl+t")
        await pilot.pause()
        assert app.theme == "viddl-light"
        assert "Light" in str(row.render())
        await pilot.press("ctrl+t")
        await pilot.pause()
        assert app.theme == "viddl-high-contrast"
        assert "High contrast" in str(row.render())
        await pilot.press("ctrl+t")
        await pilot.pause()
        assert app.theme == "viddl-dark"


async def test_the_row_can_be_pressed_with_the_keyboard(monkeypatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    app = home_app()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        row = app.screen.query_one("#theme-row")
        row.focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.theme == "viddl-light"
        # Focused, the row says it is a control rather than only looking like one.
        assert row.has_focus


async def test_a_click_cycles_the_theme_too(monkeypatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    app = home_app()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        row = app.screen.query_one("#theme-row")
        await pilot.click(row)
        await pilot.pause()
        assert app.theme == "viddl-light"


async def test_no_color_is_said_on_screen(monkeypatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    app = home_app("light")
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        assert app.no_color is True
        text = str(app.screen.query_one("#theme-row").render())
        assert "NO_COLOR is set" in text
        # The scheme is still named: the row is a label, not a swatch.
        assert "Light" in text


def test_an_unknown_scheme_is_refused_when_the_app_is_built() -> None:
    with pytest.raises(ValueError):
        home_app("banana")


# ---------------------------------------------------------------------------
# the one colour that is not in the stylesheet
# ---------------------------------------------------------------------------


class ColourApp(App):
    """A ChoiceList on its own, drawn with the real themes (U1)."""

    CSS = """
    .choice-row {
        height: 1;
        width: 100%;
    }
    """

    def __init__(self, name: str = theme.DARK) -> None:
        super().__init__()
        for scheme in theme.THEMES:
            self.register_theme(scheme)
        self.theme = theme.THEME_PREFIX + name

    def get_theme_variable_defaults(self) -> dict:
        return theme.default_variables()

    def compose(self) -> ComposeResult:
        yield ChoiceList(id="choices")


def row_styles(row: ChoiceRow) -> dict:
    """Every span of a row, by the text it covers, for a colour assertion."""
    text = row.render()
    return {
        text.plain[span.start : span.end].strip(): span.style for span in text.spans
    }


def span_hex(style) -> Optional[str]:
    """The colour a span carries as ``#rrggbb``, or ``None`` when it has none.

    Textual hands a rendered row back with its own ``Style`` (``foreground``)
    while Rich calls the same thing ``color``; both answer ``hex``, so the test
    asks for whichever is there rather than pinning one release.
    """
    colour = getattr(style, "foreground", None) or getattr(style, "color", None)
    if colour is None:
        return None
    return str(getattr(colour, "hex", colour)).lower()


async def test_a_quality_row_takes_its_colour_from_the_theme() -> None:
    app = ColourApp("light")
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        choices = app.query_one("#choices", ChoiceList)
        choices.set_choices(
            [
                ("1080p", "20.0 MB", "best video", "one"),
                ("720p", "9.0 MB", "", "two"),
            ]
        )
        await pilot.pause()
        light = theme.PALETTES[theme.LIGHT]
        rows = list(app.query(ChoiceRow))
        assert len(rows) == 2

        active, other = rows[0], rows[1]
        # The row under the cursor sits on $primary, so its size is the colour
        # chosen to be read there and not the accent.
        active_styles = row_styles(active)
        assert all(
            style is None or light.accent.lower() not in str(style).lower()
            for style in active_styles.values()
        )

        styles = row_styles(other)
        assert span_hex(styles.get("9.0 MB")) == light.accent.lower()
        assert styles.get("720p") is None


async def test_a_note_is_muted_rather_than_dimmed() -> None:
    """`dim` halves a colour, which can drop it below the ratio we measured."""
    app = ColourApp("dark")
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        choices = app.query_one("#choices", ChoiceList)
        # The first row is the active one, whose note is read against $primary
        # and so keeps the row's own colour; a note is only muted elsewhere.
        choices.set_choices(
            [
                ("best", "12.0 MB", "video + audio", "one"),
                ("720p", "9.0 MB", "video + audio", "two"),
            ]
        )
        await pilot.pause()
        dark = theme.PALETTES[theme.DARK]
        note = row_styles(list(app.query(ChoiceRow))[1])["video + audio"]
        assert span_hex(note) == dark.muted.lower()
        assert "dim" not in str(note)
