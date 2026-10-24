"""An optional settings file, and the rules for what wins (F13).

Viddl works with nothing configured. No file is read, none is written, and
every choice is the one the app shipped with — which is worth keeping, because
"it just works" is a feature and a settings screen is not.

When a preference *is* changed, it is written to one small file:

* Linux and macOS ``$XDG_CONFIG_HOME/viddl/config.toml``, or
  ``~/.config/viddl/config.toml``
* Windows ``%APPDATA%\\viddl\\config.toml``

It is written by ``viddl --set`` and by nothing else, so a machine that never
ran that command has no file at all. What it holds is a preference, not an
instruction: a flag on the command line beats an environment variable, an
environment variable beats the file, and the file beats the default. That order
is the thing this module exists to keep straight, because a setting that
silently does not apply is worse than no setting.

The file is deliberately a small TOML subset — ``key = "value"`` and
``key = true`` — because Viddl reads it with the standard library on every
Python it supports (Python 3.9 has no ``tomllib``), and writing a file it
cannot read back would be the one bug worth avoiding here. A hand-written
comment or a key from a newer version is left alone; a value that is not one
Viddl recognises is ignored and said so in ``viddl --doctor``.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import APP_NAME
from . import theme as theme_mod
from .formats import AUDIO_FORMATS, ORIGINAL_AUDIO_KEY

#: The environment variable that outranks the file for each setting (F13). The
#: other two already existed; ``VIDDL_QUALITY`` was added so that every setting
#: can be overridden the same way, which is half of what precedence means.
ENV_DOWNLOAD_DIR = "VIDDL_DOWNLOAD_DIR"
ENV_QUALITY = "VIDDL_QUALITY"
ENV_ARCHIVE = "VIDDL_ARCHIVE"
ENV_THEME = theme_mod.ENV_THEME

#: The file, inside a folder named after the app in the platform's config place.
FILE_NAME = "config.toml"

#: What the file may call a setting. Hyphens because the flags have them, so
#: ``--set download-dir`` names the same thing as ``--download-dir``.
@dataclass(frozen=True)
class Setting:
    name: str
    env: str
    flag: str
    summary: str


SETTINGS: Tuple[Setting, ...] = (
    Setting(
        "download-dir",
        ENV_DOWNLOAD_DIR,
        "--download-dir",
        "where finished files go",
    ),
    Setting(
        "quality",
        ENV_QUALITY,
        "--quality",
        "what --plain picks, and the row the app starts on",
    ),
    Setting(
        "archive",
        ENV_ARCHIVE,
        "--download-archive",
        "remember downloads and skip videos you already have",
    ),
    Setting(
        "theme",
        ENV_THEME,
        "--theme",
        "the colours the app draws in",
    ),
)

SETTING_BY_NAME = {setting.name: setting for setting in SETTINGS}

#: The words ``quality`` accepts, before the format presets below. They are the
#: same words ``--quality`` takes (see ``plain.choose_option``); a test asserts
#: the two lists have not drifted apart, because a setting that the download
#: path then refuses would be a trap.
QUALITY_WORDS: Tuple[str, ...] = (
    "any",
    "audio",
    "a",
    "best",
    "no-reencode",
    "no_reencode",
    "orig",
    "original",
    ORIGINAL_AUDIO_KEY,
)


def quality_words() -> Tuple[str, ...]:
    """Every spelling ``quality`` accepts: heights aside, the flags' own list."""
    words = list(QUALITY_WORDS)
    for audio_format in AUDIO_FORMATS:
        words.append(audio_format.key)
        for preset in audio_format.presets:
            words.append(f"{audio_format.key}-{preset.bitrate}")
    # De-duplicated but in a stable order, so an error message does not shuffle.
    return tuple(dict.fromkeys(words))


#: What the file may hold for a yes/no setting, and what it is written as.
_ARCHIVE_ON = ("1", "on", "yes", "true")
_ARCHIVE_OFF = ("0", "off", "no", "false")

_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass
class Stored:
    """What the settings file holds right now, and what could not be used."""

    path: Path
    #: Setting name -> the value as Viddl stores it (``720``, ``true``, a path).
    values: Dict[str, str] = field(default_factory=dict)
    #: Keys this version does not know, kept verbatim so a newer version's
    #: setting is not thrown away by an older one writing the file.
    unknown: List[Tuple[str, str]] = field(default_factory=list)
    #: One line per value or line that could not be used, for the doctor.
    problems: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# where the file is
# ---------------------------------------------------------------------------


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def config_dir() -> Path:
    """The folder the settings file lives in, platform by platform."""
    if _is_windows():
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_NAME
    # ``XDG_CONFIG_HOME`` is the Linux convention and is honoured on macOS too:
    # one answer for both POSIX platforms is easier to document than two.
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / APP_NAME


def config_path() -> Path:
    """The settings file. It may not exist — that is the normal case."""
    return config_dir() / FILE_NAME


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------


def _strip_comment(text: str) -> str:
    """``raw`` without a trailing ``# comment``, keeping a ``#`` inside quotes."""
    quote = ""
    escaped = False
    for index, character in enumerate(text):
        if escaped:
            escaped = False
            continue
        if character == "\\" and quote == '"':
            escaped = True
            continue
        if quote:
            if character == quote:
                quote = ""
            continue
        if character in "\"'":
            quote = character
            continue
        if character == "#":
            return text[:index]
    return text


def _unquote(raw: str) -> Tuple[Optional[str], str]:
    """The text inside a TOML string, or ``(None, why not)``.

    Only what Viddl writes is guaranteed here — double quotes with the four
    escapes below — plus single-quoted literal strings, which are common enough
    in hand-written TOML that refusing them would be unhelpful. Anything else
    is a value Viddl cannot be sure it read correctly, so it is refused rather
    than guessed at.
    """
    text = raw.strip()
    if not text:
        return None, "is empty"
    if text.startswith('"'):
        # Walk to the closing quote so an escaped one does not end it early.
        escaped = False
        closing = -1
        for index in range(1, len(text)):
            character = text[index]
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                closing = index
                break
        if closing != len(text) - 1:
            return None, "has an unclosed quote"
        body = text[1:-1]
        out: List[str] = []
        index = 0
        while index < len(body):
            character = body[index]
            if character != "\\":
                out.append(character)
                index += 1
                continue
            index += 1
            if index >= len(body):
                return None, "ends with a lone backslash"
            escape = body[index]
            mapped = {"\\": "\\", '"': '"', "n": "\n", "r": "\r", "t": "\t"}.get(escape)
            if mapped is None:
                return None, f"has an escape Viddl does not write (\\{escape})"
            out.append(mapped)
            index += 1
        return "".join(out), ""
    if text.startswith("'"):
        if len(text) < 2 or not text.endswith("'"):
            return None, "has an unclosed quote"
        return text[1:-1], ""
    if any(character.isspace() for character in text):
        return None, "is not quoted, and has a space in it"
    return text, ""


# ---------------------------------------------------------------------------
# checking and normalising one value
# ---------------------------------------------------------------------------


def _clean_download_dir(value: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError("needs a folder")
    if text.startswith("-"):
        raise ValueError("looks like an option, not a folder")
    if any(character in text for character in "\x00\n\r"):
        raise ValueError("contains a control character")
    path = Path(text).expanduser()
    try:
        if path.exists() and not path.is_dir():
            raise ValueError(f"{path} is a file, not a folder")
        return str(path.resolve())
    except OSError as exc:
        raise ValueError(f"cannot be used ({exc})") from exc


def _clean_quality(value: str) -> str:
    text = value.strip().lower()
    if text in quality_words():
        return text
    if text.isdigit() and int(text) > 0:
        return text
    if text.endswith("p") and text[:-1].isdigit() and int(text[:-1]) > 0:
        return text
    raise ValueError(
        f"'{value}' is not a quality — use a height such as 720, best, or an "
        "audio format: audio, mp3, m4a, opus, flac or wav (mp3-192 picks a bitrate)"
    )


def _clean_archive(value: str) -> str:
    text = value.strip().lower()
    if text in _ARCHIVE_ON:
        return "true"
    if text in _ARCHIVE_OFF:
        return "false"
    raise ValueError(f"'{value}' is not a yes or no (try on or off)")


def _clean_theme(value: str) -> str:
    # The spelling, the aliases and the refusal live with the palettes, so the
    # flag, the variable and the file cannot accept three different lists (U1).
    return theme_mod.clean(value)


_CLEANERS = {
    "download-dir": _clean_download_dir,
    "quality": _clean_quality,
    "archive": _clean_archive,
    "theme": _clean_theme,
}


def _checked(name: str, value: str) -> str:
    """``value`` as it will be stored, or ``ValueError`` saying why not."""
    return _CLEANERS[name](value)


def read() -> Stored:
    """Read the settings file; a missing file is simply a file with no settings.

    Nothing here raises: a file that cannot be read is a problem to report, not
    a reason for every path that asks about a preference to fail. Invalid
    values are reported and left out, so a typo in the file means the default
    is used and the doctor says why — rather than the default being used
    silently.
    """
    stored = Stored(path=config_path())
    try:
        text = stored.path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return stored
    except OSError as exc:
        stored.problems.append(f"{stored.path} could not be read ({exc})")
        return stored

    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, rest = stripped.partition("=")
        key = key.strip()
        if not separator or not _BARE_KEY.match(key):
            stored.problems.append(f"line {number} is not a setting — ignored")
            continue
        raw = _strip_comment(rest).strip()
        if key not in SETTING_BY_NAME:
            # A key from a newer version (or a hand-written note): kept as it
            # is, so writing the file back does not delete it.
            stored.unknown.append((key, raw))
            continue
        value, problem = _unquote(raw)
        if value is None:
            stored.problems.append(f"{key} {problem} — ignored")
            continue
        try:
            stored.values[key] = _checked(key, value)
        except ValueError as exc:
            stored.problems.append(f"{key}: {exc} — ignored")
    return stored


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------


def _quote(text: str) -> str:
    escaped = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _write(stored: Stored) -> None:
    """Write the file back, or remove it when nothing is left to say.

    Written to a neighbour and moved into place, so a reader never sees half a
    file; owner-only on POSIX, because which folder somebody keeps their videos
    in is theirs. An empty file would say "configured" without configuring
    anything, so it is deleted instead — the zero-config default comes back.
    """
    lines: List[str] = []
    for setting in SETTINGS:
        if setting.name not in stored.values:
            continue
        value = stored.values[setting.name]
        # A yes/no is written as a TOML boolean; everything else as a string.
        rendered = value if setting.name == "archive" else _quote(value)
        lines.append(f"{setting.name} = {rendered}")
    lines.extend(f"{key} = {raw}" for key, raw in stored.unknown)
    path = stored.path
    if not lines:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if not _is_windows():
        try:
            os.chmod(temporary, 0o600)
        except OSError:  # pragma: no cover - a filesystem without permissions
            pass
    os.replace(temporary, path)


def set_value(name: str, value: str) -> str:
    """Store one preference, and return the value as it was stored.

    Refuses a setting Viddl does not know and a value it cannot use, rather than
    writing something a later run would silently ignore: a file that says one
    thing and does another is worse than a file that was never written.
    """
    key = (name or "").strip()
    if key not in SETTING_BY_NAME:
        known = ", ".join(setting.name for setting in SETTINGS)
        raise ValueError(f"Unknown setting '{name}'. The settings are: {known}.")
    cleaned = _checked(key, value)
    stored = read()
    stored.values[key] = cleaned
    _write(stored)
    return cleaned


def unset(name: str) -> bool:
    """Forget one preference; ``False`` when there was nothing to forget."""
    key = (name or "").strip()
    if key not in SETTING_BY_NAME:
        known = ", ".join(setting.name for setting in SETTINGS)
        raise ValueError(f"Unknown setting '{name}'. The settings are: {known}.")
    stored = read()
    if key not in stored.values:
        return False
    del stored.values[key]
    _write(stored)
    return True


# ---------------------------------------------------------------------------
# what the rest of Viddl asks
# ---------------------------------------------------------------------------


def _asked(name: str) -> Optional[str]:
    """The environment's answer, or ``None`` when it has not spoken.

    An empty variable counts as spoken: it means "the default", and must not
    fall through to the file, which would be a second answer to a question
    somebody already answered.
    """
    if name not in os.environ:
        return None
    return (os.environ.get(name) or "").strip()


def download_dir() -> Optional[Path]:
    """The folder the file asks for, unless ``VIDDL_DOWNLOAD_DIR`` spoke (F13)."""
    if _asked(ENV_DOWNLOAD_DIR) is not None:
        return None
    raw = read().values.get("download-dir")
    if not raw:
        return None
    path = Path(raw).expanduser()
    try:
        if path.exists() and not path.is_dir():
            return None
        return path.resolve()
    except OSError:  # pragma: no cover - an unreadable parent
        return None


def quality() -> str:
    """The quality ``--plain`` uses when the flag is silent, or ``""`` (F13)."""
    asked = _asked(ENV_QUALITY)
    if asked is not None:
        return asked.lower()
    return read().values.get("quality", "")


def quality_problem() -> str:
    """Why ``VIDDL_QUALITY`` cannot be used, or ``""`` when it can (or is unset)."""
    asked = _asked(ENV_QUALITY)
    if asked is None or not asked:
        return ""
    try:
        _clean_quality(asked)
    except ValueError:
        return "not a quality Viddl knows (try 720, best or audio)"
    return ""


def archive() -> Optional[bool]:
    """The file's yes or no, unless ``VIDDL_ARCHIVE`` spoke (F13)."""
    if _asked(ENV_ARCHIVE) is not None:
        return None
    raw = read().values.get("archive")
    if raw is None:
        return None
    return raw == "true"


def theme_name() -> str:
    """The scheme the file asks for, unless ``VIDDL_THEME`` spoke (U1)."""
    asked = _asked(ENV_THEME)
    if asked is not None:
        return asked.lower()
    return read().values.get("theme", "")


def theme_problem() -> str:
    """Why ``VIDDL_THEME`` cannot be used, or ``""`` when it can (or is unset)."""
    asked = _asked(ENV_THEME)
    if asked is None or not asked:
        return ""
    try:
        theme_mod.clean(asked)
    except ValueError:
        return "not a theme Viddl knows (try dark, light or high-contrast)"
    return ""


# ---------------------------------------------------------------------------
# saying what is in force
# ---------------------------------------------------------------------------


def example() -> str:
    """The shortest thing that writes a file, for a message with no room."""
    folder = "~/Videos" if not _is_windows() else "%USERPROFILE%\\Videos"
    return f"viddl --set download-dir {folder}"


def report() -> List[str]:
    """``viddl --doctor`` lines: the file, what is in it, and what was ignored."""
    stored = read()
    if stored.values:
        summary = f"{stored.path} ({', '.join(stored.values)})"
    elif stored.path.exists():
        summary = f"{stored.path} (no settings Viddl knows)"
    else:
        summary = f"none — every setting is a default (`{example()}` writes one)"
    lines = [f"{'Settings:':<14}{summary}"]
    for problem in stored.problems:
        lines.append(f"{'':<14}{problem}")
    return lines
