"""The manual page and the version block, checked against the code (D5).

A man page is documentation nobody runs, which is exactly the kind that goes
stale: an option renamed in ``cli.py`` leaves the page describing a flag that no
longer exists, and the person who finds out is a user typing it.  These tests
compare the page with the parser instead of trusting the two to be edited
together, the way ``tests/test_appdata.py`` compares ``README.md`` with the
environment variables the app reads.
"""

from __future__ import annotations

import re
from pathlib import Path

from viddl import appdata
from viddl.cli import build_parser

ROOT = Path(__file__).resolve().parent.parent
MAN_PAGE = ROOT / "man" / "viddl.1"


def man_page() -> str:
    return MAN_PAGE.read_text(encoding="utf-8")


def man_page_text() -> str:
    """The page with roff's escaped hyphens put back, so names read as typed.

    The page writes a flag as ``\\-\\-download\\-archive`` so that roff cannot
    turn the hyphens into dashes; the backslashes are rendering, not part of the
    name, and leaving them in would split every hyphenated flag in two.
    """
    return man_page().replace("\\-", "-")


def documented_options() -> set:
    """Every ``--flag`` the page spells out, as it would be typed."""
    return set(re.findall(r"--([a-z][a-z0-9-]*)", man_page_text()))


def parser_options() -> set:
    names = set()
    for action in build_parser()._actions:
        for option in action.option_strings:
            if option.startswith("--"):
                names.add(option[2:])
    return names


def test_every_option_the_parser_takes_is_in_the_page():
    missing = parser_options() - documented_options()
    assert not missing, f"accepted but undocumented: {sorted(missing)}"


def test_the_page_invents_no_options():
    invented = documented_options() - parser_options()
    assert not invented, f"documented but not accepted: {sorted(invented)}"


def test_every_environment_variable_is_in_the_page():
    page = man_page()
    for name in appdata.ENVIRONMENT_VARIABLES:
        assert name in page, f"{name} is read but not in the man page"


def test_the_page_has_the_sections_a_reader_looks_for():
    page = man_page()
    assert page.startswith('.\\"'), "a comment line first, so roff has nothing to do"
    for section in (
        "NAME",
        "SYNOPSIS",
        "DESCRIPTION",
        "OPTIONS",
        "ENVIRONMENT",
        "FILES",
        "EXIT STATUS",
        "EXAMPLES",
        "SEE ALSO",
    ):
        assert f"\n.SH {section}\n" in page, f"missing the {section} section"
    assert '.TH VIDDL 1 "' in page, "a section-1 page, with a title and a date"
    assert "viddl \\- download" in page, "the NAME line says what it does"


def test_the_package_ships_the_page():
    """A page in a checkout but not in the wheel is a page nobody installing gets."""
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"share/man/man1" = ["man/viddl.1"]' in pyproject


def test_the_readme_shows_the_verbose_version_block():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    marker = "$ viddl --version --verbose"
    assert marker in readme, "the block a bug report needs is not in the README"
    block = readme.split(marker, 1)[1].split("```", 1)[0]
    for opening in ("Viddl ", "Python ", "yt-dlp ", "ffmpeg "):
        assert opening in block, f"the version block does not show {opening.strip()}"
