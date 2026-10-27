"""How a download uses the network (F11).

Four knobs, all of them passed straight to the engine's own flags, and all of
them checked here first: a value that reaches a command line is a value that
can be read as an option, so ``--proxy --exec=rm`` must be refused rather than
handed over. Empty is the default for every one of them — a fresh Viddl uses
the network exactly as it did before this existed.

The proxy is the one that can carry a secret (``http://user:pass@host:port``),
so :meth:`NetworkOptions.summary` names the host and never the credentials: the
sentence is written to a log file, and a password in a log is a password.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urlsplit

# What yt-dlp reads after ``--limit-rate`` and ``--throttled-rate``: a number,
# optionally with a unit. Spelled out rather than passed through, because this
# string ends up in argv and anything option-shaped must not get that far.
_RATE = re.compile(r"^(\d{1,9}(?:\.\d{1,3})?)\s*([KMGkmg])?[Bb]?$")

# The schemes yt-dlp's downloader understands. A value outside this list is not
# a proxy it would use, so it is a mistake worth naming rather than passing on.
PROXY_SCHEMES = ("http", "https", "socks4", "socks4a", "socks5", "socks5h")

# Enough pieces to make a slow host feel faster, few enough not to look like an
# attack from a single connection, and the same range yt-dlp is happy with.
MIN_FRAGMENTS = 1
MAX_FRAGMENTS = 32

# The count Viddl asks for when nothing else was asked. It was written into the
# command line directly before F11; keeping the same value keeps the behaviour
# identical for every run that says nothing about fragments.
DEFAULT_FRAGMENTS = 4

_MAX_PROXY_LENGTH = 2048


def _rate(value: object, option: str) -> str:
    """A speed in the form yt-dlp reads, or ``ValueError`` saying what does.

    ``"2m"`` becomes ``"2M"`` so the sentence in a log matches what was passed
    on, and a bare number is bytes per second, which is what the engine means
    by it too.
    """
    text = str(value or "").strip()
    match = _RATE.match(text)
    if match is None:
        raise ValueError(
            f"{option} needs a speed such as 500K or 2M, not {text or 'nothing'}."
        )
    number, unit = match.group(1), (match.group(2) or "").upper()
    return f"{number}{unit}"


def _proxy(value: object) -> str:
    """A proxy address with a scheme yt-dlp knows, or ``ValueError``.

    Whitespace is refused outright: a proxy with a space in it is either a
    paste accident or two arguments, and neither belongs on a command line.
    """
    text = str(value or "").strip()
    if len(text) > _MAX_PROXY_LENGTH:
        raise ValueError("That proxy address is too long to be one.")
    if any(character.isspace() or ord(character) < 32 for character in text):
        raise ValueError(
            "A proxy address cannot contain spaces. Use http://host:port or "
            "socks5://host:port."
        )
    try:
        parts = urlsplit(text)
    except ValueError as exc:
        raise ValueError(f"That proxy address could not be read: {exc}") from exc
    scheme = parts.scheme.lower()
    if scheme not in PROXY_SCHEMES:
        raise ValueError(
            "A proxy address starts with http://, https://, socks4://, "
            "socks4a://, socks5:// or socks5h://."
        )
    if not parts.hostname:
        raise ValueError(
            "That proxy address has no host in it. Example: "
            "socks5://127.0.0.1:1080"
        )
    try:
        # Reading ``.port`` is what validates it: a port outside 1–65535
        # raises rather than coming back as a number, so it is caught here
        # instead of escaping as a traceback further down.
        port = parts.port
    except ValueError as exc:
        raise ValueError("A proxy port has to be between 1 and 65535.") from exc
    if port is not None and port < 1:
        raise ValueError("A proxy port has to be between 1 and 65535.")
    return f"{scheme}://{parts.netloc}{parts.path or ''}"


def _fragments(value: object) -> int:
    """How many pieces of one video to fetch at once, or ``ValueError``."""
    try:
        count = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "--concurrent-fragments needs a whole number such as 4."
        ) from exc
    if not (MIN_FRAGMENTS <= count <= MAX_FRAGMENTS):
        raise ValueError(
            f"--concurrent-fragments has to be between {MIN_FRAGMENTS} and "
            f"{MAX_FRAGMENTS}."
        )
    return count


@dataclass(frozen=True)
class NetworkOptions:
    """The network choices for one run.

    Frozen, because it is shared by every download a session starts: a queue of
    nine videos all stream through the same proxy at the same limit, and a
    later screen changing one of those numbers by accident would change
    downloads already running.
    """

    #: ``--limit-rate``: an upper bound on how fast a download may go.
    limit_rate: str = ""
    #: ``--proxy``: the address every request goes through.
    proxy: str = ""
    #: ``--concurrent-fragments``: how many pieces of one video at a time.
    concurrent_fragments: Optional[int] = None
    #: ``--throttled-rate``: give up on a source that stays slower than this.
    throttled_rate: str = ""

    @classmethod
    def parse(
        cls,
        *,
        limit_rate: object = "",
        proxy: object = "",
        concurrent_fragments: object = None,
        throttled_rate: object = "",
    ) -> NetworkOptions:
        """Check every value once, at the front door, with one message each."""
        return cls(
            limit_rate=_rate(limit_rate, "--limit-rate") if limit_rate else "",
            proxy=_proxy(proxy) if proxy else "",
            concurrent_fragments=(
                _fragments(concurrent_fragments)
                if concurrent_fragments not in (None, "")
                else None
            ),
            throttled_rate=(
                _rate(throttled_rate, "--throttled-rate") if throttled_rate else ""
            ),
        )

    def __bool__(self) -> bool:
        """True when the run asked for anything at all, as ``Extras`` does."""
        return bool(
            self.limit_rate
            or self.proxy
            or self.concurrent_fragments is not None
            or self.throttled_rate
        )

    @property
    def fragments(self) -> int:
        """The fragment count to pass on, defaulting to Viddl's own."""
        return self.concurrent_fragments or DEFAULT_FRAGMENTS

    @property
    def proxy_host(self) -> str:
        """The proxy's host and port, with any welcome-out credentials dropped.

        This is the form every sentence and every log line uses, and the whole
        reason it exists: a proxy password is a password.
        """
        if not self.proxy:
            return ""
        parts = urlsplit(self.proxy)
        host = parts.hostname or ""
        if ":" in host:  # an IPv6 literal keeps its brackets
            host = f"[{host}]"
        port = f":{parts.port}" if parts.port is not None else ""
        return f"{host}{port}"

    def args(self) -> List[str]:
        """The engine's own flags, in a fixed order.

        ``--concurrent-fragments`` is always there — the default is Viddl's
        choice, not the engine's, and it is the count the two numbers below are
        measured against. The rest appear only when they were asked for.
        """
        args = ["--concurrent-fragments", str(self.fragments)]
        if self.limit_rate:
            args += ["--limit-rate", self.limit_rate]
        if self.throttled_rate:
            args += ["--throttled-rate", self.throttled_rate]
        if self.proxy:
            args += ["--proxy", self.proxy]
        return args

    def summary(self) -> str:
        """What was asked for, in words, with no credentials in it.

        Empty when nothing was asked, so callers can add the sentence only when
        there is one to add.
        """
        parts: List[str] = []
        if self.proxy:
            parts.append(f"through {self.proxy_host}")
        if self.limit_rate:
            parts.append(f"no faster than {self.limit_rate}/s")
        if self.throttled_rate:
            parts.append(
                f"trying another source if this one stays slower than "
                f"{self.throttled_rate}/s"
            )
        if self.concurrent_fragments is not None:
            parts.append(f"{self.concurrent_fragments} pieces at a time")
        return ", ".join(parts)
