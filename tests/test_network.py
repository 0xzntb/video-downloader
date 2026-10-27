"""The network choices a run can make (F11).

Two things are being tested here: that each value is read the way the engine
reads it, and that a value which is not one is refused before it reaches a
command line. The second matters as much as the first — a proxy string that
becomes an option is the same mistake as an option-shaped link.
"""

from __future__ import annotations

import pytest

from viddl import cli, plain
from viddl.network import (
    DEFAULT_FRAGMENTS,
    MAX_FRAGMENTS,
    NetworkOptions,
)
from viddl.tui import app as tui_app

URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


# ---------------------------------------------------------------------------
# reading a value
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "written, read",
    [
        ("2M", "2M"),
        ("2m", "2M"),  # a lowercase unit is the same unit
        ("500k", "500K"),
        ("1.5M", "1.5M"),
        ("800", "800"),  # a bare number is bytes per second, as yt-dlp reads it
        ("2MB", "2M"),  # and a "B" is noise, not a different unit
    ],
)
def test_a_speed_is_read_the_way_the_engine_reads_it(written, read):
    assert NetworkOptions.parse(limit_rate=written).limit_rate == read


@pytest.mark.parametrize("written", ["fast", "2M/s", "1e9", "-1", "2MT", "M", "2 M 3"])
def test_a_speed_that_is_not_one_is_refused(written):
    with pytest.raises(ValueError) as caught:
        NetworkOptions.parse(limit_rate=written)
    assert "--limit-rate" in str(caught.value)
    assert "500K or 2M" in str(caught.value)


def test_an_option_shaped_speed_cannot_become_an_option():
    """The whole reason values are checked: they end up in argv."""
    with pytest.raises(ValueError):
        NetworkOptions.parse(limit_rate="--exec=rm -rf ~")


def test_the_throttle_is_read_the_same_way_and_named_in_its_own_message():
    assert NetworkOptions.parse(throttled_rate="100k").throttled_rate == "100K"
    with pytest.raises(ValueError) as caught:
        NetworkOptions.parse(throttled_rate="slow")
    assert "--throttled-rate" in str(caught.value)


@pytest.mark.parametrize(
    "written, read",
    [
        ("http://127.0.0.1:8080", "http://127.0.0.1:8080"),
        ("socks5://127.0.0.1:1080", "socks5://127.0.0.1:1080"),
        ("SOCKS5H://proxy.example:1080", "socks5h://proxy.example:1080"),
        ("socks4a://10.0.0.1:1080", "socks4a://10.0.0.1:1080"),
        # A proxy that needs a sign-in is a real thing, and the address is
        # kept as written so the engine can use it.
        (
            "http://me:secret@proxy.example:3128",
            "http://me:secret@proxy.example:3128",
        ),
    ],
)
def test_a_proxy_address_is_kept_as_written(written, read):
    assert NetworkOptions.parse(proxy=written).proxy == read


@pytest.mark.parametrize(
    "written",
    [
        "127.0.0.1:8080",  # no scheme: not an address the engine can use
        "ftp://proxy.example:21",  # a scheme it does not speak
        "socks5://",  # no host
        "http://host:0",  # no port
        "http://host:99999",  # not a port
        "http://proxy example:8080",  # a space is two arguments
        "http://host:8080\n--exec=rm",  # a newline is the same trick
        "http://[::1",  # an address that cannot even be read
        "--proxy=http://host:8080",
        "http://" + "a" * 3000 + ":80",
    ],
)
def test_a_proxy_that_is_not_an_address_is_refused(written):
    with pytest.raises(ValueError) as caught:
        NetworkOptions.parse(proxy=written)
    assert "--proxy" in str(caught.value) or "proxy" in str(caught.value)


def test_a_proxy_password_is_not_in_the_sentence_written_down():
    """The summary goes to a log file, so it names the host and stops there."""
    network = NetworkOptions.parse(proxy="http://me:hunter2@proxy.example:3128")

    assert network.proxy_host == "proxy.example:3128"
    assert "hunter2" not in network.summary()
    assert "me@" not in network.summary()
    # But the engine still gets the whole address: it is the one that needs it.
    assert network.args()[network.args().index("--proxy") + 1] == (
        "http://me:hunter2@proxy.example:3128"
    )


def test_an_ipv6_proxy_keeps_its_brackets_in_the_sentence():
    network = NetworkOptions.parse(proxy="socks5://[::1]:1080")
    assert network.proxy_host == "[::1]:1080"


def test_a_run_with_no_proxy_has_no_host_to_name():
    assert NetworkOptions().proxy_host == ""


@pytest.mark.parametrize("written, read", [("8", 8), (8, 8), ("1", 1), (32, MAX_FRAGMENTS)])
def test_a_fragment_count_is_a_whole_number_in_range(written, read):
    assert NetworkOptions.parse(concurrent_fragments=written).concurrent_fragments == read


@pytest.mark.parametrize("written", ["0", "33", "-4", "four", "4.5"])
def test_a_fragment_count_outside_the_range_is_refused(written):
    with pytest.raises(ValueError) as caught:
        NetworkOptions.parse(concurrent_fragments=written)
    assert "--concurrent-fragments" in str(caught.value)


def test_nothing_asked_for_is_the_emptiest_answer():
    network = NetworkOptions.parse()

    assert network == NetworkOptions()
    assert not network
    assert network.fragments == DEFAULT_FRAGMENTS


# ---------------------------------------------------------------------------
# what reaches the engine
# ---------------------------------------------------------------------------


def test_the_fragment_count_is_always_asked_for_once():
    """Viddl's own default, not the engine's, and never twice."""
    args = NetworkOptions().args()

    assert args == ["--concurrent-fragments", str(DEFAULT_FRAGMENTS)]


def test_every_choice_is_handed_over_in_a_fixed_order():
    network = NetworkOptions.parse(
        limit_rate="2M",
        proxy="socks5://127.0.0.1:1080",
        concurrent_fragments=8,
        throttled_rate="100K",
    )

    assert network.args() == [
        "--concurrent-fragments",
        "8",
        "--limit-rate",
        "2M",
        "--throttled-rate",
        "100K",
        "--proxy",
        "socks5://127.0.0.1:1080",
    ]


def test_only_what_was_asked_for_appears():
    network = NetworkOptions.parse(limit_rate="1M")

    assert "--proxy" not in network.args()
    assert "--throttled-rate" not in network.args()


def test_the_throttle_is_described_rather_than_named():
    network = NetworkOptions.parse(throttled_rate="100K")
    assert network.summary() == (
        "trying another source if this one stays slower than 100K/s"
    )


def test_the_sentence_says_what_was_asked_in_words():
    network = NetworkOptions.parse(
        limit_rate="2M", proxy="socks5://127.0.0.1:1080", concurrent_fragments=8
    )

    assert network.summary() == (
        "through 127.0.0.1:1080, no faster than 2M/s, 8 pieces at a time"
    )


# ---------------------------------------------------------------------------
# the flags
# ---------------------------------------------------------------------------


def test_the_cli_hands_the_network_choices_to_the_plain_path(monkeypatch):
    seen = {}
    monkeypatch.setattr(plain, "run_plain", lambda url, **kwargs: seen.update(kwargs) or 0)

    code = cli.main(
        [
            "--plain",
            "--yes",
            "--limit-rate",
            "2m",
            "--proxy",
            "socks5://127.0.0.1:1080",
            "--concurrent-fragments",
            "8",
            "--throttled-rate",
            "100k",
            URL,
        ]
    )

    assert code == 0
    assert seen["network"] == NetworkOptions(
        limit_rate="2M",
        proxy="socks5://127.0.0.1:1080",
        concurrent_fragments=8,
        throttled_rate="100K",
    )


def test_the_cli_hands_the_network_choices_to_the_app(monkeypatch):
    seen = {}
    # ``cli`` imports the app's entry point at call time, so this is the one
    # that has to be replaced — replacing ``viddl.tui.run`` would leave the
    # real terminal app to start.
    monkeypatch.setattr(tui_app, "run", lambda **kwargs: seen.update(kwargs) or None)

    assert cli.main(["--limit-rate", "2M", URL]) == 0
    assert seen["network"].limit_rate == "2M"


def test_the_app_still_starts_with_no_network_choices_at_all(monkeypatch):
    seen = {}
    monkeypatch.setattr(tui_app, "run", lambda **kwargs: seen.update(kwargs) or None)

    assert cli.main([URL]) == 0
    assert seen["network"] == NetworkOptions()
    assert not seen["network"]


@pytest.mark.parametrize(
    "flags, word",
    [
        (["--limit-rate", "fast"], "--limit-rate"),
        (["--proxy", "127.0.0.1:8080"], "proxy"),
        (["--concurrent-fragments", "0"], "--concurrent-fragments"),
        (["--throttled-rate", "slow"], "--throttled-rate"),
    ],
)
def test_a_value_that_is_not_one_is_refused_before_anything_is_downloaded(
    monkeypatch, capsys, flags, word
):
    started = []
    monkeypatch.setattr(plain, "run_plain", lambda *a, **k: started.append(a) or 0)
    monkeypatch.setattr(tui_app, "run", lambda **k: started.append(k) or None)

    assert cli.main(["--plain", "--yes", *flags, URL]) == 1

    err = capsys.readouterr().err
    assert word in err
    assert "For example: viddl --limit-rate 2M" in err
    assert started == []
