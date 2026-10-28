"""Fuzz and property tests for the parsers (E9).

The other modules show this code the shapes somebody thought of. These run the
same code over generated input and state the rules that must hold for *every*
one of them, because the parsers are where the app reads things it did not
write:

* ``parse_scheme`` is fed by any web page that opens a ``viddl://`` link;
* ``errors.explain`` runs regexes over whatever the engine printed on stderr;
* ``estimate_size`` and friends read a site's own idea of how large its file is;
* ``_find_output`` decides which of several files is the one to hand back.

Every generator here is seeded (``derandomize=True``), so a failure is a failure
again tomorrow rather than a story about a red build on somebody else's laptop,
and hypothesis still prints the smallest input that broke the rule.

The one thing these are *not* trying to do is replace the example-based tests:
a property says "never anything but a link"; the modules next door say which
link, in which words, with which hint.
"""

from __future__ import annotations

import contextlib
import random
import re
import shutil
import tempfile
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from hypothesis import given, settings
from hypothesis import strategies as st

from viddl import errors
from viddl.download import DownloadJob, _is_attachment, _is_intermediate
from viddl.engine import Tool
from viddl.errors import SIGN_IN_MARK
from viddl.formats import MediaOption, estimate_size
from viddl.i18n import t
from viddl.protocol import build_scheme_url, parse_scheme, parse_scheme_url
from viddl.util import human_duration, human_size, safe_video_link

#: One profile for the file. The deadline is hypothesis' own timing check — a
#: regex that backtracks exponentially on a long line fails here as a *timeout*
#: rather than as a test suite that takes four minutes on a machine with the
#: wrong locale. Two seconds is loose enough for a slow runner and far tighter
#: than a ReDoS.
PROPERTY = settings(deadline=2000, derandomize=True, max_examples=200)
#: Fewer, longer examples: a pattern that backtracks badly shows it on the first
#: page of text, and generating megabyte-long strings 200 times would make the
#: suite slower without asking a different question.
LONG_INPUT = settings(deadline=2000, derandomize=True, max_examples=50)
FILESYSTEM = settings(deadline=None, derandomize=True, max_examples=50)


def _assert_is_a_link_we_would_type(text: str, request) -> None:
    """The rules a hand-off must obey once it has been accepted.

    Stated once, used by the generated test and by the fuzz run, so the two
    cannot drift into checking different things.
    """
    assert request.url == safe_video_link(request.url), (
        "a link came back that the URL check itself would refuse"
    )
    assert request.url.lower().startswith(("http://", "https://"))
    assert not request.url.startswith("-"), "an option is not a link"
    assert not any(
        character.isspace() or character < " " or character == "\x7f"
        for character in request.url
    ), "argument-splitting whitespace survived"
    assert len(request.url) <= 2048
    # The host has to be the one the page actually sent. Viddl may add the
    # scheme back, but it must never point the download somewhere else.
    host = urlsplit(request.url).hostname
    assert host and host.lower() in unquote(text).lower()


# ---------------------------------------------------------------------------
# the viddl:// hand-off (F15, S1)
# ---------------------------------------------------------------------------

#: Links a page would realistically send: any host, any query, no spaces.
_HANDED_LINK = st.builds(
    lambda host, path: f"https://{host}/{path}",
    st.from_regex(r"[a-z][a-z0-9.-]{0,12}", fullmatch=True),
    st.text(
        alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        "._~/?=&%-",
        max_size=40,
    ),
)


@given(st.text(max_size=4000))
@PROPERTY
def test_any_text_a_page_can_open_is_answered_and_never_crashes_it(text):
    """A URL from a web page is a string, not a promise. Parsing it must not
    raise — an exception here is the app dying instead of showing a window."""
    request = parse_scheme(text)
    assert request is None or isinstance(request.url, str)
    assert parse_scheme_url(text) == (request.url if request else None)


@given(st.text(max_size=200))
@PROPERTY
def test_an_accepted_handoff_is_a_link_we_would_have_typed_ourselves(text):
    request = parse_scheme(text)
    if request is not None:
        _assert_is_a_link_we_would_type(text, request)


@given(_HANDED_LINK, st.booleans())
@PROPERTY
def test_a_link_we_build_is_the_link_that_comes_back(link, audio):
    """The extension and the app agree on one shape: what one writes, the other
    reads back byte for byte. Audio is the only thing carried separately."""
    request = parse_scheme(build_scheme_url(link, audio=audio))
    assert request is not None
    assert request.url == link
    assert request.audio is audio


@given(_HANDED_LINK)
@PROPERTY
def test_a_link_arrives_unchanged_in_both_encoded_shapes(link):
    """The extension's shape, and the same shape with the scheme shouted.

    Both percent-encode the link, so both give it back exactly — including a
    link that already carries an escape of its own, which is where a hand-rolled
    string join would double-encode it.
    """
    assert parse_scheme_url(build_scheme_url(link)) == link
    assert parse_scheme_url(f"VIDDL://download?url={quote(link, safe='')}") == link


@given(_HANDED_LINK)
@PROPERTY
def test_the_unencoded_shapes_never_answer_with_a_different_link(link):
    """``viddl://download/<url>`` and ``viddl://<url>`` put the link in the path,
    and nothing percent-encodes it there, so what comes back is the *decoded*
    link — or a refusal, when what it decodes to is not a link at all (a NUL, a
    space). It is never some third thing."""
    for shape in (f"viddl://download/{link}", f"viddl:{link}"):
        assert parse_scheme_url(shape) in (None, unquote(link))


@given(st.text(min_size=1, max_size=80))
@PROPERTY
def test_an_option_is_never_accepted_as_a_link(remainder):
    """``--exec=…`` typed where a link belongs must not become an argument.

    This is the whole of S1: the value arrives from a page, and a value that
    starts with a dash is an option to every tool that reads it.
    """
    flag = "-" + remainder
    assert parse_scheme(f"viddl://download?url={quote(flag, safe='')}") is None
    assert parse_scheme(f"viddl:{flag}") is None
    assert parse_scheme(f"viddl://download/{flag}") is None
    assert safe_video_link(flag) is None


@given(_HANDED_LINK, st.sampled_from(["0", "2", "false", "no", "off", "mp4", ""]))
@PROPERTY
def test_audio_is_only_true_when_it_was_asked_for(link, value):
    """A quality row rather than an audio row: the flag is absent or says no."""
    request = parse_scheme(f"viddl://download?url={quote(link, safe='')}&audio={value}")
    assert request is not None
    assert request.audio is False


#: Small pieces a hostile page would reach for, plus the ordinary ones, so the
#: fuzz loop below builds strings that are nearly valid rather than noise.
_FRAGMENTS = [
    "viddl:",
    "viddl://",
    "VIDDL:",
    "download",
    "?",
    "&audio=1",
    "url=",
    "https://",
    "http://",
    "example.com",
    "/watch?v=abc123",
    "%",
    "%00",
    "%3D",
    "--exec=calc.exe",
    " ",
    "\t",
    "\n",
    "'",
    '"',
    "-",
    "..",
    "\\",
    "\x00",
    "\x7f",
    "javascript:",
    "file:///etc/passwd",
    "&",
    "=",
    "#",
    "é",
    "🎬",
    ":",
]

#: Fixed so the corpus is the same on every machine and in every run: a fuzz
#: test that finds something different each time is a test nobody can hand back
#: to a bug report.
FUZZ_SEED = 20261028
FUZZ_ROUNDS = 1500


def test_a_small_fuzz_run_over_the_handoff_parser():
    """Generated from a seed, in the two shapes a page can use.

    hypothesis covers structured input well and this covers the rest: strings
    assembled from the pieces a hostile page actually has, and a good link with
    one character changed, which is what a bug in the extension looks like.
    """
    rng = random.Random(FUZZ_SEED)

    for _ in range(FUZZ_ROUNDS):
        text = "".join(rng.choice(_FRAGMENTS) for _ in range(rng.randint(1, 8)))
        request = parse_scheme(text)
        if request is not None:
            _assert_is_a_link_we_would_type(text, request)

    good = build_scheme_url("https://www.youtube.com/watch?v=abc123")
    assert parse_scheme(good) is not None, "the corpus is built from a link that works"
    for _ in range(FUZZ_ROUNDS):
        mutated = list(good)
        for _ in range(rng.randint(1, 3)):
            position = rng.randrange(len(mutated))
            choice = rng.random()
            if choice < 0.4:
                del mutated[position]
            elif choice < 0.7:
                mutated.insert(position, rng.choice(_FRAGMENTS))
            else:
                mutated[position] = rng.choice(" -&?=%\"'\x00")
            if not mutated:
                break
        text = "".join(mutated)
        request = parse_scheme(text)
        if request is not None:
            _assert_is_a_link_we_would_type(text, request)


# ---------------------------------------------------------------------------
# explaining the engine's output (R10, F7)
# ---------------------------------------------------------------------------

#: Every headline the app is allowed to use, taken from the rules themselves
#: rather than written out again: a rule added tomorrow is covered, and one
#: that returns something the user did not pay for fails here.
_HEADLINES = {t(rule.message, sign_in=SIGN_IN_MARK) for rule in errors._RULES}
_HEADLINES |= {
    t("error.stopped_early"),
    t("error.generic"),
    t("error.reported"),
    t("error.unfinished"),
}


@given(st.text(max_size=2000), st.one_of(st.none(), st.integers(-5, 200)))
@PROPERTY
def test_whatever_the_engine_printed_becomes_one_of_our_sentences(text, exit_code):
    error = errors.explain(text, exit_code)
    assert error.message
    assert error.message in _HEADLINES, (
        "the headline has to be a sentence from the catalogue, never the "
        f"engine's own words: {error.message!r}"
    )
    assert isinstance(error.temporary, bool)


@given(st.text(max_size=2000), st.one_of(st.none(), st.integers(-5, 200)))
@PROPERTY
def test_explaining_the_same_output_twice_says_the_same_thing(text, exit_code):
    assert errors.explain(text, exit_code) == errors.explain(text, exit_code)


@given(st.text(min_size=2000, max_size=12000))
@LONG_INPUT
def test_a_huge_wall_of_output_is_explained_quickly(text):
    """The timeout in the profile is the assertion: a pattern that backtracks
    badly turns a crash into the app hanging, which is worse for the person
    looking at it."""
    error = errors.explain(text)
    assert error.message in _HEADLINES


@given(st.text(max_size=3000), st.integers(1, 60))
@PROPERTY
def test_the_detail_quoted_back_is_always_short(text, limit):
    """Raw output is quoted into the advice, so it must be capped — a page that
    printed a megabyte must not be pasted into the window."""
    assert len(errors._clean_raw(text, limit=limit)) <= limit


# ---------------------------------------------------------------------------
# sizes and durations (U4, U8)
# ---------------------------------------------------------------------------

_UNITS = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}
_RENDERED_SIZE = re.compile(r"^(\d+(?:\.\d)?) (B|KB|MB|GB)$")


@given(st.floats(min_value=0.5, max_value=1e18, allow_nan=False, allow_infinity=False,
                 allow_subnormal=False))
@PROPERTY
def test_a_size_is_never_wrong_by_more_than_the_rounding(size):
    """Whatever comes back, it can be read as a number of bytes again and it is
    the number that went in — give or take one decimal place of its unit."""
    shown = human_size(size)
    match = _RENDERED_SIZE.match(shown)
    assert match, f"{size!r} was shown as {shown!r}"
    number, unit = float(match.group(1)), _UNITS[match.group(2)]
    assert abs(number * unit - size) <= max(1.0, 0.05 * unit), shown


@given(
    st.one_of(
        st.none(),
        st.just(float("nan")),
        st.just(float("inf")),
        st.just(float("-inf")),
        st.floats(max_value=0, allow_nan=False),
    )
)
@PROPERTY
def test_a_size_we_cannot_show_is_a_question_mark(size):
    """Zero, a negative, and a number that is not a number all mean the same
    thing on screen — ``?`` — rather than ``nan GB`` next to a title (E9)."""
    assert human_size(size) == "?"


@given(st.floats(max_value=1e18))
@PROPERTY
def test_no_number_at_all_can_make_the_size_formatter_raise(size):
    human_size(size)


@given(
    st.floats(min_value=0.001, max_value=1e9, allow_nan=False, allow_infinity=False),
)
@PROPERTY
def test_a_duration_reads_back_as_the_seconds_that_went_in(seconds):
    shown = human_duration(seconds)
    fields = [int(part) for part in shown.split(":")]
    assert 1 <= len(fields) <= 3
    total = 0
    for field in fields:
        total = total * 60 + field
    assert total == int(seconds), shown
    for field in shown.split(":")[1:]:
        assert len(field) == 2 and 0 <= int(field) < 60, shown


@given(
    st.one_of(
        st.none(),
        st.just(float("nan")),
        st.just(float("inf")),
        st.floats(max_value=0, allow_nan=False),
    )
)
@PROPERTY
def test_a_duration_we_cannot_show_is_a_question_mark(seconds):
    assert human_duration(seconds) == "?"


@given(st.floats(max_value=1e18))
@PROPERTY
def test_no_number_at_all_can_make_the_duration_formatter_raise(seconds):
    human_duration(seconds)


#: Values a site's metadata really carries, and the ones it carries when
#: something upstream has gone wrong: ``"nan"`` and ``"1e400"`` parse as floats
#: and are neither positive nor finite.
_JUNK = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(-(10**9), 10**9),
    st.floats(allow_nan=True, allow_infinity=True),
    st.text(max_size=12),
    st.sampled_from(["nan", "NaN", "inf", "-inf", "1e400", "", " 12 ", "12kbps"]),
)

_FORMAT = st.fixed_dictionaries(
    {},
    optional={
        name: _JUNK
        for name in ("filesize", "filesize_approx", "tbr", "vbr", "abr", "ext", "vcodec")
    },
)


@given(_FORMAT, st.one_of(st.none(), st.floats(allow_nan=True, allow_infinity=True)))
@PROPERTY
def test_an_estimate_is_either_bytes_or_nothing_at_all(fmt, duration):
    """Metadata is a site's claim, not a measurement. Anything it sends has to
    come out as a positive number of bytes or as "unknown" — never zero, never
    a negative, and never a traceback in the middle of the quality list."""
    size, guess = estimate_size(fmt, duration)
    assert isinstance(guess, bool)
    if size is not None:
        assert isinstance(size, int)
        assert size > 0


@given(st.integers(1, 10**12), _FORMAT)
@PROPERTY
def test_a_size_the_site_stated_is_never_called_a_guess(exact, extra):
    fmt = dict(extra, filesize=exact)
    assert estimate_size(fmt, None) == (exact, False)


# ---------------------------------------------------------------------------
# which file is the one to hand back (R6, R10)
# ---------------------------------------------------------------------------


class _Engine:
    """The least a :class:`DownloadJob` needs in order to exist."""

    def __init__(self) -> None:
        self._tool = Tool("yt-dlp", ["/fake/yt-dlp"], "viddl", version="fake")

    def ensure_ytdlp(self, **kwargs):
        return self._tool

    def ffmpeg_location_args(self):
        return []

    def find_ffmpeg(self):
        return None


def _video_option() -> MediaOption:
    return MediaOption(
        key="v1080",
        label="1080p",
        kind="video",
        needs_ffmpeg=True,
        format_selector="137+140",
        merge_container="mp4",
    )


_FINISHED = "A Video [abc].mp4"
_LEFTOVERS = [
    "A Video [abc].f137.mp4",
    "A Video [abc].f140.m4a",
    "A Video [abc].mp4.part",
    "A Video [abc].mp4.ytdl",
    "A Video [abc].jpg",
    "A Video [abc].en.vtt",
]


@contextlib.contextmanager
def _downloads_folder():
    """A downloads folder of its own for each generated example.

    Held here rather than by a pytest fixture because these examples are not
    fixtures: every one of them needs a folder nothing else has touched, and a
    generated example may be re-run a dozen times inside a single test.
    """
    root = Path(tempfile.mkdtemp(prefix="viddl-property-"))
    try:
        folder = root / "Downloads"
        folder.mkdir()
        yield folder
    finally:
        shutil.rmtree(root, ignore_errors=True)


@FILESYSTEM
@given(st.lists(st.sampled_from([_FINISHED, *_LEFTOVERS]), max_size=6, unique=True))
def test_the_file_handed_back_is_finished_or_there_is_none(names):
    """Every combination of a finished file and the debris around it.

    Two claims in one place: a leftover is never reported as the download, and
    a download that produced nothing says so instead of picking the newest
    thing lying in the folder.
    """
    with _downloads_folder() as out:
        job = DownloadJob(_Engine(), "u", _video_option(), out)
        work = job.work_dir()
        work.mkdir(parents=True)
        before = job._snapshot()

        for name in names:
            (work / name).write_bytes(b"x" * 16)

        found = job._find_output(before)

    if _FINISHED in names:
        assert found == work / _FINISHED
        return
    assert found is None, f"reported {found.name} when nothing was finished"
    for name in names:
        assert _is_intermediate(work / name) or _is_attachment(work / name)


@FILESYSTEM
@given(st.lists(st.sampled_from(_LEFTOVERS), min_size=1, max_size=6, unique=True))
def test_leftovers_are_never_mistaken_for_the_download(names):
    """The same claim from the other side, without a finished file in play."""
    with _downloads_folder() as out:
        job = DownloadJob(_Engine(), "u", _video_option(), out)
        work = job.work_dir()
        work.mkdir(parents=True)
        before = job._snapshot()
        for name in names:
            (work / name).write_bytes(b"x" * 16)

        assert job._find_output(before) is None


def test_a_handoff_never_reaches_the_command_line(monkeypatch):
    """The value that survived the parser is what the download job is given.

    Written as a property of the seam rather than of either side: whatever a
    page sends, the string that ends up in the argument list starts with a
    scheme and is one argument, because that is the shape ``--`` protects.
    """
    for text in (
        "viddl://download?url=" + quote("https://example.com/x?token=abc", safe=""),
        "viddl://https://example.com/x",
        "viddl://download?url=" + quote("--exec=calc.exe", safe=""),
    ):
        request = parse_scheme(text)
        if request is None:
            continue
        argv = DownloadJob(_Engine(), request.url, _video_option(), Path("/tmp")).build_args()
        assert "--" in argv
        assert argv[argv.index("--") + 1] == request.url
