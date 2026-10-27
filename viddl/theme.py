"""The colours the app draws in, and who gets to choose them (U1).

Viddl used to draw the same screen in every window: pale text on a near-black
panel, whether the machine was a dark editor at midnight or a bright office
screen at noon. This module is the answer. It holds three colour schemes, the
order that decides which one is in force, and the arithmetic that says the
result can actually be read.

The schemes are named for what they are rather than what they look like:

* **dark** — light text on a near-black page. The default, and close to what
  the app drew before there was a choice.
* **light** — dark text on a pale page, for a bright room or a terminal whose
  own background is white.
* **high-contrast** — pure black and white with bright edges, every piece of
  text at 7:1 or better. For a projector, a washed-out laptop screen, or eyes
  that want the extra separation.

``auto`` is not a fourth scheme: it is the instruction to look at what the
terminal says about itself (``COLORFGBG``, which most terminals set) and pick
``dark`` or ``light``. A terminal that says nothing gets ``dark``, which is what
the app did before.

**Who wins.** A ``--theme`` flag, then ``VIDDL_THEME``, then the settings file,
then the default — the same order every other preference follows (F13). The
home screen has one row for changing it without leaving the app; that change
lasts for the run, because writing the settings file is ``--set``'s job and a
click should not quietly leave a file behind.

**Readability is measured, not eyeballed.** Every scheme names the pairs that
matter — text on the panel, a muted note, a warning, the chosen row's own text,
the border around the panel — and :func:`contrast` computes the WCAG ratio for
each. The tests require them to clear 4.5:1 (7:1 for high contrast, and 3:1 for
borders and disabled text, which the standard exempts). ``viddl --doctor``
prints the smallest ratio of the scheme in force, so the claim is checkable on
the machine it matters on.

**No colour means no colour.** With ``NO_COLOR`` set, Textual itself turns the
finished screen monochrome (it has done since the release this feature needs),
and the app says so on the home screen rather than pretending the theme did
something. ``TERM=dumb`` cannot draw the app at all; the command line refuses
with the ``--plain`` command to run instead, since a terminal that cannot move
the cursor cannot show a full-screen program.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Tuple

from textual.theme import Theme

#: The names a person types. ``auto`` is a rule rather than a scheme and is
#: resolved before anything is drawn.
DARK = "dark"
LIGHT = "light"
HIGH = "high-contrast"
AUTO = "auto"

SCHEMES: Tuple[str, ...] = (DARK, LIGHT, HIGH)
CHOICES: Tuple[str, ...] = (*SCHEMES, AUTO)

#: Textual theme names are prefixed so they cannot collide with a built-in
#: theme of the same name in a later release.
THEME_PREFIX = "viddl-"

ENV_THEME = "VIDDL_THEME"
ENV_NO_COLOR = "NO_COLOR"

#: WCAG 2 ratios. Text needs 4.5:1 to be readable; large text and interface
#: graphics need 3:1; high contrast asks for 7:1 and 4.5:1.
READABLE = 4.5
GRAPHIC = 3.0
STRICT_READABLE = 7.0
STRICT_GRAPHIC = 4.5


@dataclass(frozen=True)
class Palette:
    """One scheme's colours, all as ``#rrggbb`` so the contrast is checkable."""

    name: str
    #: How the row and the doctor name it: "High contrast", not "high-contrast".
    title: str
    #: True for a scheme whose page is dark, which is what Textual means by it.
    dark: bool
    primary: str
    accent: str
    warning: str
    success: str
    error: str
    background: str
    surface: str
    panel: str
    text: str
    #: Between the text and the page: notes, labels, the engine line.
    muted: str
    #: What a disabled control says — exempt from the text minimum, measured
    #: against the graphics one so it is still visible.
    disabled: str
    #: Text that sits on ``primary``: the chosen row, a primary button.
    on_primary: str
    #: High contrast holds itself to 7:1 rather than 4.5:1.
    strict: bool = False


#: The three schemes, in the order the home screen cycles through them.
PALETTES: Dict[str, Palette] = {
    DARK: Palette(
        name=DARK,
        title="Dark",
        dark=True,
        primary="#1f6feb",
        accent="#ffa62b",
        warning="#ffb454",
        success="#4ebf71",
        error="#ff6b6b",
        background="#121212",
        surface="#1e1e1e",
        panel="#262626",
        text="#e6e6e6",
        muted="#a8a8a8",
        disabled="#7a7a7a",
        on_primary="#ffffff",
    ),
    LIGHT: Palette(
        name=LIGHT,
        title="Light",
        dark=False,
        primary="#0b5d9e",
        accent="#8a4a00",
        warning="#8a5200",
        success="#1c6b3a",
        error="#a31621",
        background="#f4f4f4",
        surface="#ffffff",
        panel="#ececec",
        text="#161616",
        muted="#4f4f4f",
        disabled="#6b6b6b",
        on_primary="#ffffff",
    ),
    HIGH: Palette(
        name=HIGH,
        title="High contrast",
        dark=True,
        primary="#00d7ff",
        accent="#ffe600",
        warning="#ffcc00",
        success="#3dff7a",
        error="#ff8a8a",
        background="#000000",
        surface="#000000",
        panel="#000000",
        text="#ffffff",
        muted="#dcdcdc",
        disabled="#a6a6a6",
        on_primary="#000000",
        strict=True,
    ),
}

#: What ``--theme`` and the settings file accept, canonicalised. Aliases are
#: here because "high" is what people type for "high-contrast".
_ALIASES: Dict[str, str] = {
    DARK: DARK,
    LIGHT: LIGHT,
    HIGH: HIGH,
    "high": HIGH,
    "contrast": HIGH,
    AUTO: AUTO,
}


@dataclass(frozen=True)
class Pair:
    """One pair of colours that has to be readable, and how badly."""

    what: str
    #: Attribute names on :class:`Palette`.
    foreground: str
    background: str
    #: The ratio a normal scheme must reach.
    minimum: float


#: Everything a person has to be able to read or see. The chosen row is in here
#: because it is the one place text sits on a bright colour rather than next to
#: one — the pair most likely to be unreadable in a hand-picked palette.
PAIRS: Tuple[Pair, ...] = (
    Pair("text on the panel", "text", "surface", READABLE),
    Pair("text on the page", "text", "background", READABLE),
    Pair("a muted note on the panel", "muted", "surface", READABLE),
    Pair("a muted note on the page", "muted", "background", READABLE),
    Pair("the brand on the panel", "accent", "surface", READABLE),
    Pair("a warning on the panel", "warning", "surface", READABLE),
    Pair("a failure on the panel", "error", "surface", READABLE),
    Pair("a saved path on the panel", "success", "surface", READABLE),
    Pair("the chosen row's text", "on_primary", "primary", READABLE),
    Pair("disabled text on the panel", "disabled", "surface", GRAPHIC),
    Pair("the border on the panel", "primary", "surface", GRAPHIC),
    Pair("the border on the page", "primary", "background", GRAPHIC),
)


# ---------------------------------------------------------------------------
# the colour maths
# ---------------------------------------------------------------------------


def _channel(value: int) -> float:
    """One sRGB channel as WCAG's linear-light value."""
    scale = value / 255
    return scale / 12.92 if scale <= 0.03928 else ((scale + 0.055) / 1.055) ** 2.4


def luminance(colour: str) -> float:
    """WCAG relative luminance of ``#rrggbb``: 0 is black, 1 is white."""
    text = str(colour).strip().lstrip("#")
    if len(text) != 6:
        raise ValueError(f"{colour!r} is not a #rrggbb colour")
    try:
        red, green, blue = (int(text[index : index + 2], 16) for index in (0, 2, 4))
    except ValueError:
        raise ValueError(f"{colour!r} is not a #rrggbb colour") from None
    return 0.2126 * _channel(red) + 0.7152 * _channel(green) + 0.0722 * _channel(blue)


def contrast(foreground: str, background: str) -> float:
    """How many times lighter (or darker) ``foreground`` is than ``background``.

    The WCAG 2 definition, which is the number the standard's 4.5:1 and 3:1
    thresholds refer to. 1.0 is the same colour; 21.0 is black on white.
    """
    first, second = luminance(foreground), luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def _minimum(pair: Pair, palette: Palette) -> float:
    if not palette.strict:
        return pair.minimum
    return STRICT_READABLE if pair.minimum >= READABLE else STRICT_GRAPHIC


# ---------------------------------------------------------------------------
# the schemes as Textual themes
# ---------------------------------------------------------------------------


def _theme(palette: Palette) -> Theme:
    """One palette as the object Textual draws from.

    ``variables`` is what the stylesheet and the widgets ask for by name. The
    first three replace Textual's own generated values so the numbers above are
    the ones on screen; ``on-primary`` and ``size-foreground`` are ours, and
    exist so no colour has to be written into a stylesheet or a widget (U1).
    """
    return Theme(
        name=THEME_PREFIX + palette.name,
        primary=palette.primary,
        accent=palette.accent,
        warning=palette.warning,
        success=palette.success,
        error=palette.error,
        foreground=palette.text,
        background=palette.background,
        surface=palette.surface,
        panel=palette.panel,
        dark=palette.dark,
        variables={
            "text": palette.text,
            "text-muted": palette.muted,
            "text-disabled": palette.disabled,
            "on-primary": palette.on_primary,
            # What a primary button (and the chosen row) puts on top of
            # ``$primary``; Textual's own default is ``$text``, which is not
            # chosen to contrast with the button behind it.
            "button-color-foreground": palette.on_primary,
            # The size column of a quality row: a dim colour of its own rather
            # than a colour literal inside ``widgets.py``.
            "size-foreground": palette.accent,
        },
    )


#: The three, ready to register on an app.
THEMES: Tuple[Theme, ...] = tuple(_theme(PALETTES[name]) for name in SCHEMES)


def default_variables() -> Dict[str, str]:
    """The variables a stylesheet may ask for even before a theme is picked."""
    return dict(THEMES[0].variables)


def clean(name: str) -> str:
    """``name`` as one of :data:`CHOICES`, or ``ValueError`` saying it is not.

    The spelling is not case-sensitive and a hyphen or an underscore is the
    same thing, because a setting typed on a command line is not a password.
    """
    text = str(name or "").strip().lower().replace("_", "-")
    key = _ALIASES.get(text)
    if key is None:
        raise ValueError(
            f"'{name}' is not a theme — use dark, light, high-contrast, or auto"
        )
    return key


def palette(name: str) -> Palette:
    """The colours of ``name``; ``auto`` is already a real scheme by now."""
    key = clean(name)
    return PALETTES[DARK if key == AUTO else key]


def title(name: str) -> str:
    """How the scheme is named to a person: ``High contrast``."""
    return palette(name).title


def checks(name: str) -> List[Tuple[Pair, float, float]]:
    """Every pair in :data:`PAIRS` with the ratio it reaches and the one it needs."""
    chosen = palette(name)
    return [
        (
            pair,
            contrast(getattr(chosen, pair.foreground), getattr(chosen, pair.background)),
            _minimum(pair, chosen),
        )
        for pair in PAIRS
    ]


def problems(name: str) -> List[str]:
    """The pairs that fail their minimum, as sentences; empty when all pass."""
    found = [
        f"{pair.what}: {ratio:.1f}:1, needs {minimum:.1f}:1"
        for pair, ratio, minimum in checks(name)
        if ratio < minimum
    ]
    return found


def worst_text_ratio(name: str) -> float:
    """The smallest ratio among the pairs a person has to *read*.

    The graphics pairs — a border, disabled text — are held to 3:1 on purpose,
    so counting them would make a perfectly readable scheme look marginal on
    the doctor's line. They are still checked; they are just not this number.
    """
    return min(ratio for pair, ratio, _ in checks(name) if pair.minimum >= READABLE)


# ---------------------------------------------------------------------------
# the terminal's own preferences
# ---------------------------------------------------------------------------


def _asked(name: str, environ: Mapping[str, str]) -> Optional[str]:
    if name not in environ:
        return None
    return str(environ.get(name) or "").strip()


def terminal_is_light(environ: Optional[Mapping[str, str]] = None) -> bool:
    """Whether the terminal says it has a light background.

    ``COLORFGBG`` is ``foreground;background`` with ANSI numbers, so ``0;15``
    is black on white and ``15;0`` is white on black. Anything else — unset,
    empty, a word where a number should be — is not a hint, and the guess falls
    back to dark.
    """
    env = os.environ if environ is None else environ
    value = _asked("COLORFGBG", env) or ""
    if not value:
        return False
    last = value.split(";")[-1].strip()
    try:
        number = int(last)
    except ValueError:
        return False
    return number >= 8


def terminal_is_dumb(environ: Optional[Mapping[str, str]] = None) -> bool:
    """True when ``TERM=dumb``: a terminal that cannot move the cursor."""
    env = os.environ if environ is None else environ
    return (_asked("TERM", env) or "").lower() == "dumb"


def no_colour_reason(environ: Optional[Mapping[str, str]] = None) -> str:
    """Why colour is off, or ``""`` when it is on.

    Presence is enough, empty or not, which is how Textual reads it and how
    Rich did before: a variable somebody set is not a variable to second-guess.
    """
    env = os.environ if environ is None else environ
    return f"{ENV_NO_COLOR} is set" if ENV_NO_COLOR in env else ""


@dataclass(frozen=True)
class Chosen:
    """Which scheme is in force, where that came from, and why."""

    name: str
    #: What was asked for, ``auto`` included.
    requested: str
    #: The flag, the variable, the file, the terminal, or the default.
    source: str
    colour: bool = True
    #: Why colour is off; empty when it is on.
    note: str = ""
    #: Where ``auto`` found its answer, when it found one.
    guess: str = ""

    @property
    def summary(self) -> str:
        """One phrase for ``--doctor``: the scheme, where from, how readable."""
        parts = [self.source]
        if self.guess:
            parts.append(self.guess)
        text = (
            f"{self.name} ({'; '.join(parts)}; "
            f"smallest text contrast {worst_text_ratio(self.name):.1f}:1)"
        )
        if not self.colour:
            text += f" — {self.note}, so colour is off"
        return text


def _guess(environ: Mapping[str, str]) -> Tuple[str, str]:
    """The scheme ``auto`` picks, and the hint it used (``""`` for none)."""
    if not _asked("COLORFGBG", environ):
        return DARK, ""
    return (LIGHT if terminal_is_light(environ) else DARK), "the terminal's background"


def choose(
    flag: str = "",
    stored: str = "",
    environ: Optional[Mapping[str, str]] = None,
) -> Chosen:
    """Resolve the scheme in force: flag, variable, file, terminal, default.

    Raises ``ValueError`` for a value that is not a theme, so the command line
    can refuse it the way it refuses a quality it does not recognise, rather
    than quietly drawing in the default.
    """
    env = os.environ if environ is None else environ
    requested = ""
    source = ""
    if str(flag).strip():
        requested, source = str(flag).strip(), "--theme"
    elif _asked(ENV_THEME, env) is not None:
        # Set but empty means "auto", and still means "the file does not get a
        # say": the same rule the other variables follow (F13).
        requested, source = _asked(ENV_THEME, env) or AUTO, ENV_THEME
    elif str(stored).strip():
        requested, source = str(stored).strip(), "the settings file"

    key = clean(requested or AUTO)
    guess = ""
    if key == AUTO:
        name, guess = _guess(env)
    else:
        name = key
    note = no_colour_reason(env)
    return Chosen(
        name=name,
        requested=key,
        source=source or "the default",
        colour=not note,
        note=note,
        guess=guess,
    )


def doctor_line(
    flag: str = "",
    stored: str = "",
    environ: Optional[Mapping[str, str]] = None,
) -> str:
    """The ``viddl --doctor`` line, which must not fail on a bad value."""
    env = os.environ if environ is None else environ
    label = f"{'Theme:':<14}"
    try:
        chosen = choose(flag, stored, env)
    except ValueError as exc:
        # Nothing is drawn: the command line refuses to start the app with a
        # theme it cannot use, so naming a default here would be a lie.
        return f"{label}not usable — {exc}"
    line = label + chosen.summary
    if terminal_is_dumb(env):
        line += "; TERM=dumb cannot draw the app — use --plain"
    return line
