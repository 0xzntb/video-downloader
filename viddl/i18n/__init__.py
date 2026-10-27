"""The app's words, kept in one place so they can be written in another language (U7).

A catalogue is a plain dict of key to sentence, one module per language. There
is no build step and no ``.mo`` file to compile: the keys travel with the code
that uses them, and a missing translation is a fallback to English rather than
an empty label. That is the whole design, and it is deliberately the boring one
— this app ships as a single package with one runtime dependency, and a
translation toolchain would be a second thing to install before anything can be
read.

**What is translated so far.** The explanations in :mod:`viddl.errors` — the
twenty-odd rules that turn an engine's output into a sentence a person can act
on — because that is where somebody who does not read English hits a wall. The
screens are still English, so a hint that points at a control quotes the
control as the screen shows it; translating the screens is what :func:`t` is
here for, not something this module pretends has happened.

**Which language.** ``--lang`` first, then the machine's own setting in the
order POSIX defines (``LC_ALL``, ``LC_MESSAGES``, ``LANG``). Only the primary
subtag is read, so ``es_MX`` and ``es_ES.UTF-8`` are both Spanish; there are no
regional variants yet, and Spanish is a better answer than English.

The language in force is module state rather than an argument. Every sentence
is built at the moment it is shown, from whichever front door is running — the
app, ``--plain`` or the local page — and threading a language through a
download job to reach one hint would be a parameter that exists only to be
passed on.
"""

from __future__ import annotations

import os
from typing import Dict, List, Mapping, NamedTuple, Optional

from . import en, es

__all__ = [
    "CATALOGUES",
    "DEFAULT",
    "Chosen",
    "available",
    "choose",
    "current_locale",
    "doctor_line",
    "negotiate",
    "set_locale",
    "t",
]

#: Adding a language is one import, one entry here, and a module whose
#: ``MESSAGES`` has the keys in ``en.MESSAGES``.
_MODULES = (en, es)

#: Every catalogue, by the code it is asked for.
CATALOGUES: Dict[str, Dict[str, str]] = {
    module.CODE: module.MESSAGES for module in _MODULES
}

#: What each language calls itself: ``Español``, not ``Spanish``.
NAMES: Dict[str, str] = {module.CODE: module.NAME for module in _MODULES}

#: The language the app is written in, and the one every key falls back to.
DEFAULT = en.CODE

_current = DEFAULT


def available() -> List[str]:
    """The codes Viddl can speak: English first, the rest alphabetical."""
    return [DEFAULT, *sorted(code for code in CATALOGUES if code != DEFAULT)]


def current_locale() -> str:
    """The code in force, e.g. ``"es"``."""
    return _current


def set_locale(code: str) -> str:
    """Say everything in ``code`` from now on, and return the code used.

    Raises ``ValueError`` when there is no catalogue for it: a typo caught here
    is a message, while a typo swallowed here would show up much later as an app
    that is mysteriously still English.
    """
    global _current
    key = negotiate(code)
    if key is None:
        raise ValueError(
            f"'{code}' is not a language Viddl speaks yet — try "
            + ", ".join(available())
        )
    _current = key
    return key


def _primary(value: str) -> str:
    """``es_MX.UTF-8@euro`` -> ``es``."""
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.split(".")[0].split("@")[0]
    return text.replace("-", "_").lower().split("_")[0]


def negotiate(value: str) -> Optional[str]:
    """The shipped language a locale name asks for, or ``None`` for none.

    ``C`` and ``POSIX`` ask for no language in particular, so they answer
    ``None`` rather than a guess. A colon-separated list (as GNU's ``LANGUAGE``
    uses) is read left to right, and the first language Viddl has wins.
    """
    for part in str(value or "").replace(",", ":").split(":"):
        code = _primary(part)
        if code in CATALOGUES:
            return code
    return None


class Chosen(NamedTuple):
    """Which language is in force, and what asked for it."""

    code: str
    #: ``--lang``, ``LANG=es_ES.UTF-8``, or ``the default``.
    source: str
    #: What the machine asked for when Viddl does not speak it, else ``""``.
    unmatched: str = ""

    @property
    def name(self) -> str:
        """What the language calls itself, e.g. ``Español``."""
        return NAMES.get(self.code, self.code)

    @property
    def summary(self) -> str:
        """One phrase for ``--doctor``: the language, where from, and why."""
        text = f"{self.name} ({self.code}, from {self.source})"
        if self.unmatched:
            text += f" — no translation for {self.unmatched} yet, so English"
        return text


def choose(flag: str = "", environ: Optional[Mapping[str, str]] = None) -> Chosen:
    """Resolve the language in force: ``--lang``, then the machine's locale.

    A language named on the command line is refused when there is no catalogue
    for it, because the person asked for it and answering in English without
    saying so would be a small lie. A machine whose ``LANG`` names a language
    Viddl does not have is simply answered in English — a computer set to
    French should not stop working because Viddl is not — and the doctor names
    the language that went unanswered.
    """
    env = os.environ if environ is None else environ
    requested = str(flag or "").strip()
    if requested:
        code = negotiate(requested)
        if code is None:
            raise ValueError(
                f"'{requested}' is not a language Viddl speaks yet — try "
                + ", ".join(available())
            )
        return Chosen(code, "--lang")
    # POSIX order: the most specific setting wins, and an empty one means
    # "unset" rather than an empty language.
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = str(env.get(name, "") or "").strip()
        if not value:
            continue
        code = negotiate(value)
        if code is None:
            return Chosen(DEFAULT, f"{name}={value}", unmatched=value)
        return Chosen(code, f"{name}={value}")
    return Chosen(DEFAULT, "the default")


def t(key: str, **values: str) -> str:
    """The sentence for ``key``, in the language in force.

    A translation that has not caught up falls back to English, one key at a
    time, so a partly finished catalogue is still usable. A key no catalogue
    has at all is a ``KeyError``: that is a mistake in the code, and showing
    ``error.sign_in`` to a person would hide it.
    """
    text = CATALOGUES[_current].get(key)
    if text is None:
        text = CATALOGUES[DEFAULT].get(key)
    if text is None:
        raise KeyError(key)
    return text.format(**values) if values else text


def doctor_line(flag: str = "", environ: Optional[Mapping[str, str]] = None) -> str:
    """The ``viddl --doctor`` line, which must not fail on a bad value."""
    label = f"{'Language:':<14}"
    try:
        chosen = choose(flag, environ)
    except ValueError as exc:
        # Nothing is written in a language Viddl does not have: the command
        # line refuses to start with one, so naming a language here would lie.
        return f"{label}not usable — {exc}"
    return label + chosen.summary
