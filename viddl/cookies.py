"""Let yt-dlp read the browser's own cookie store, so a sign-in works (F1).

Some links — an age-restricted video, a bot check, Instagram, Threads — answer
only to a signed-in session. yt-dlp can read the cookies the browser already
has, and this module is the part of Viddl that offers it.

Three things live here, and the order they are listed in is the order they
matter:

1. **What is read, said out loud.** :func:`note` and :func:`active_note` are the
   sentences the programme shows the person who is about to hand an account's
   cookies to a download. They are part of the interface, not documentation:
   this is the only feature in Viddl that touches an account's files, and the
   person deciding needs to know before, not after.
2. **Which browser.** :func:`installed` looks for the folders a browser keeps
   its data in, so a machine with one browser offers one choice.
3. **The command line.** :func:`cookies_args` turns a name from the closed list
   in :data:`BROWSER_KEYS` into the two arguments yt-dlp understands. A name
   typed by a person ends up in a command line, so the list is closed on
   purpose: anything else becomes no arguments at all.

What Viddl does *not* do is as important: it never reads a cookie itself, never
asks for a password, never copies a cookie store, and never writes one down. It
passes a browser's own name to yt-dlp, which reads the store in place, for the
one download that asked for it.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

#: The browsers ``--cookies-from-browser`` accepts, in the order they are
#: offered. A closed list, because what is typed here reaches a command line.
BROWSER_KEYS: Tuple[str, ...] = ("chrome", "firefox", "edge", "brave", "safari")

#: What a person reads, per browser. ``chrome`` is not a word to put in a
#: sentence, and Chrome's own folder is called ``google-chrome`` on Linux.
LABELS: Dict[str, str] = {
    "chrome": "Chrome",
    "firefox": "Firefox",
    "edge": "Edge",
    "brave": "Brave",
    "safari": "Safari",
}

#: Where each browser keeps its data, per platform. ``%NAME%`` is a Windows
#: environment variable, ``~/`` is the home folder on macOS and Linux, and
#: anything else is relative to the home folder.
#:
#: A path that exists means the browser has been used on this machine — which
#: is all the detection claims. It is not proof that a signed-in session for
#: any particular site is in there: nobody can know that without reading it,
#: and reading it is not something this program does.
_LOCATIONS: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "chrome": {
        "win": (r"%LOCALAPPDATA%\Google\Chrome\User Data",),
        "mac": ("~/Library/Application Support/Google/Chrome",),
        "linux": ("~/.config/google-chrome", "~/.config/chromium"),
    },
    "firefox": {
        "win": (r"%APPDATA%\Mozilla\Firefox",),
        "mac": ("~/Library/Application Support/Firefox",),
        "linux": ("~/.mozilla/firefox", "~/snap/firefox/common/.mozilla/firefox"),
    },
    "edge": {
        "win": (r"%LOCALAPPDATA%\Microsoft\Edge\User Data",),
        "mac": ("~/Library/Application Support/Microsoft Edge",),
        "linux": ("~/.config/microsoft-edge",),
    },
    "brave": {
        "win": (r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\User Data",),
        "mac": ("~/Library/Application Support/BraveSoftware/Brave-Browser",),
        "linux": ("~/.config/BraveSoftware/Brave-Browser",),
    },
    # Safari exists on macOS only, and yt-dlp reads its cookies through the
    # system keychain, which may ask for permission at download time.
    "safari": {
        "mac": ("~/Library/Cookies", "~/Library/Safari"),
    },
}

#: What ``--cookies-from-browser`` is asked for: "use whichever browser is on
#: this computer". Never reaches yt-dlp — it is resolved here first.
AUTO = "auto"


@dataclass(frozen=True)
class Browser:
    """One browser Viddl can offer, and where it keeps its data."""

    key: str
    label: str
    places: Tuple[Path, ...]

    @property
    def installed(self) -> bool:
        """True when one of the folders this browser uses is there."""
        return any(place.exists() for place in self.places)


def _platform_name(platform: Optional[str] = None) -> str:
    """``sys.platform`` as one of the three words the table uses."""
    name = platform if platform is not None else sys.platform
    if name.startswith("win"):
        return "win"
    if name == "darwin":
        return "mac"
    return "linux"


def _expand(spec: str, home: Path, env: Mapping[str, str]) -> Optional[Path]:
    """One place, as a path. ``None`` when the variable it needs is not set."""
    if spec.startswith("%"):
        name, _, rest = spec[1:].partition("%")
        root = env.get(name)
        if not root:
            return None
        # Split on either separator, so a Windows path in the table is one
        # folder per part on any machine that reads it.
        parts = [piece for piece in rest.replace("\\", "/").split("/") if piece]
        return Path(root).joinpath(*parts)
    if spec.startswith("~/"):
        return home / spec[2:]
    return home / spec


def places_for(
    key: str,
    *,
    platform: Optional[str] = None,
    home: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
) -> Tuple[Path, ...]:
    """Where ``key`` keeps its data on this platform, worth looking at.

    The arguments exist so the platform, the home folder and the environment
    can be stated rather than discovered, which is what makes the detection
    testable on a machine that has none of these browsers.
    """
    name = _platform_name(platform)
    folder = Path(home) if home is not None else Path.home()
    environment = env if env is not None else os.environ
    specs = _LOCATIONS.get(key, {}).get(name, ())
    found: List[Path] = []
    for spec in specs:
        expanded = _expand(spec, folder, environment)
        if expanded is not None:
            found.append(expanded)
    return tuple(found)


def browsers(
    *,
    platform: Optional[str] = None,
    home: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
) -> List[Browser]:
    """Every browser Viddl knows how to offer on this platform, installed or not.

    The whole list rather than :func:`installed`, because "not installed" is
    worth saying: a person who asks for Safari on Windows should be told why
    nothing happened, not given silence.
    """
    found: List[Browser] = []
    for key in BROWSER_KEYS:
        places = places_for(key, platform=platform, home=home, env=env)
        if places:
            found.append(Browser(key, LABELS[key], places))
    return found


def installed(
    *,
    platform: Optional[str] = None,
    home: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
) -> List[Browser]:
    """The browsers that look installed here, best guess first."""
    return [
        browser
        for browser in browsers(platform=platform, home=home, env=env)
        if browser.installed
    ]


def installed_keys(
    *,
    platform: Optional[str] = None,
    home: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
) -> List[str]:
    """The keys of the installed browsers, in the order they are offered."""
    return [browser.key for browser in installed(platform=platform, home=home, env=env)]


def label_for(key: str) -> str:
    """``chrome`` is not a word to put in a sentence; ``Chrome`` is."""
    return LABELS.get(key, key)


def cookies_args(choice: str) -> List[str]:
    """The arguments that ask yt-dlp for ``choice``'s cookies.

    An empty choice, or one that is not a browser from :data:`BROWSER_KEYS`, is
    no arguments at all: this is the last step before a command line, and the
    safe reading of "I do not recognise that" is to read nothing. Callers that
    accept a name from a person use :func:`problem_with` first, so the person is
    told rather than left with a download that silently did not sign in.
    """
    key = (choice or "").strip().lower()
    if key not in BROWSER_KEYS:
        return []
    return ["--cookies-from-browser", key]


def problem_with(
    choice: str,
    *,
    platform: Optional[str] = None,
    home: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
) -> str:
    """Why ``choice`` cannot be used, or ``""`` when it can.

    Both "auto" and the empty string are fine: they mean "decide for me". The
    platform, home folder and environment are arguments for the same reason
    they are in :func:`places_for`: so this can be asked about a Windows
    machine from a Linux one, which is how "Safari is not available here" is
    tested from either.
    """
    key = (choice or "").strip().lower()
    if key in ("", AUTO):
        return ""
    if key not in BROWSER_KEYS:
        listed = ", ".join(BROWSER_KEYS)
        return f"'{choice}' is not a browser Viddl knows. Use {AUTO}, or one of: {listed}."
    if not places_for(key, platform=platform, home=home, env=env):
        label = label_for(key)
        return f"{label} is not available on this system."
    return ""


def default_for(
    choice: str,
    *,
    platform: Optional[str] = None,
    home: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
) -> str:
    """``choice`` as a browser key — resolving ``auto`` and ``""`` here.

    Returns ``""`` when nothing can be chosen, which every caller reads as "no
    cookies": no browser installed, or a name that cannot be used. A machine
    with none of the five browsers must not turn into a cookie lookup at a
    guessed path.
    """
    key = (choice or "").strip().lower()
    if key in ("", AUTO):
        found = installed_keys(platform=platform, home=home, env=env)
        return found[0] if found else ""
    if problem_with(key, platform=platform, home=home, env=env):
        return ""
    return key


def hint_for(
    choice: str,
    *,
    platform: Optional[str] = None,
    home: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
) -> str:
    """What to try instead, for the refusal :func:`problem_with` gave (F1).

    The two refusals need different advice: a name that is not a browser has a
    list to choose from, and a browser this system does not have has nothing to
    choose from at all — telling someone on Linux to name Safari again is not a
    hint, it is a loop.
    """
    key = (choice or "").strip().lower()
    if key in BROWSER_KEYS and not places_for(key, platform=platform, home=home, env=env):
        label = label_for(key)
        return (
            f"{label} is not available on this system. Use auto, or name the "
            "browser you are signed in to the site with."
        )
    return f"Try: viddl --cookies-from-browser {AUTO} <link>"


def doctor_note(
    *,
    platform: Optional[str] = None,
    home: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
) -> str:
    """One ``--doctor`` line saying where a sign-in could come from (F1).

    The doctor is where "it did not work" is answered, and "no browser was
    found" is the answer a surprising number of times — a browser installed in
    a flatpak, or a cookie store somewhere Viddl does not look. Saying which
    browsers were seen turns that into one line instead of a guess.
    """
    found = installed(platform=platform, home=home, env=env)
    if not found:
        return "Cookies:      no browser found (--cookies-from-browser needs one)"
    names = ", ".join(browser.label for browser in found)
    return f"Cookies:      {names} (--cookies-from-browser auto)"


def note(label: str) -> str:
    """What asking for ``label``'s cookies means, said before it happens (F1).

    The person is about to let a download act as their signed-in account for
    one site, so the sentence says what is read, how much of it, and that
    nothing is kept.
    """
    return (
        f"Viddl will ask {label} for the cookies it already has for this site, "
        "and use them for this download only. Your password is never read and "
        "nothing is saved."
    )


def active_note(label: str) -> str:
    """The same promise, in the past tense, once cookies are in use."""
    return (
        f"Using {label}'s cookies for this site, for this download only. "
        "Nothing was stored. Choose the row again to turn it off."
    )


def failed_note(label: str) -> str:
    """What to try when the read itself failed.

    The likeliest reasons are worth naming: a browser that is not the one the
    session is in, a store locked by the browser being open, and a session that
    has simply expired.
    """
    return (
        f"Viddl could not read {label}'s cookies. Close {label} and try again, "
        "sign in to the site in that browser first, or choose a different "
        "browser on this screen."
    )
