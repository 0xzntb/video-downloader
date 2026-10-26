"""Machine-readable answers for scripts (F10).

``viddl --doctor`` and ``viddl --list-qualities`` speak in lined-up sentences,
which is what a person reading a terminal wants and what a script cannot parse.
``--json`` asks the same questions and gets the same facts back as data, from
this module rather than from a second implementation: every number here is read
from the same place the sentence is built from, so the two cannot drift apart.

The shapes are deliberately flat and boring — no nesting deeper than the tools
and the quality rows — because the point of a machine format is that a shell
script can read it with ``jq .ytdlp.version`` and never be surprised.

Nothing secret is added here that the human report does not already print: the
environment variables are shown with their values, exactly as ``--doctor`` shows
them, and no cookie, password or token is ever read at all — the cookie section
names browsers, not contents.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from . import APP_TITLE, __version__
from . import appdata
from . import config
from . import cookies as cookies_mod
from . import engine as engine_mod
from . import i18n
from . import theme as theme_mod
from .engine import Engine, Tool
from .formats import MediaInfo, MediaOption


def _tool(tool: Optional[Tool]) -> Optional[Dict[str, Any]]:
    """One helper — yt-dlp or ffmpeg — or ``None`` when it is not installed."""
    if tool is None:
        return None
    return {
        "path": str(tool.path) if tool.path is not None else None,
        "version": tool.version or "",
        # "viddl", "system" or "environment": where the copy came from, which is
        # the thing ``--doctor`` spends a sentence on.
        "origin": tool.origin,
        "origin_text": tool.origin_text,
    }


def doctor_report(
    engine: Optional[Engine] = None, theme_flag: str = "", lang_flag: str = ""
) -> Dict[str, Any]:
    """Everything ``viddl --doctor`` says, as one object (F10).

    The folder problem is reported rather than raised: a doctor that cannot run
    because the app's own folder is unwritable would have nothing to say at the
    moment it matters most.
    """
    engine = engine or Engine()
    problem = appdata.ensure_dirs()
    ytdlp = engine.find_ytdlp()
    stored = config.read()
    # The scheme in force (U1), the same facts as the doctor's Theme line; a
    # value the file or the variable got wrong is named rather than dropped.
    theme: Dict[str, Any]
    try:
        chosen_theme = theme_mod.choose(theme_flag, config.theme_name())
    except ValueError as exc:
        # ``None`` is "no scheme is in force", which is what a refused value
        # means: the app does not start, so naming a default would be a lie.
        theme = {
            "name": None,
            "problem": str(exc),
        }
    else:
        theme = {
            "name": chosen_theme.name,
            "requested": chosen_theme.requested,
            "source": chosen_theme.source,
            "colour": chosen_theme.colour,
            "note": chosen_theme.note,
            # The smallest ratio among the scheme's text pairs, which is the
            # number the tests hold every scheme to (U1).
            "worst_text_contrast": round(
                theme_mod.worst_text_ratio(chosen_theme.name), 2
            ),
        }

    # Which language the explanations are in (U7), the same facts as the
    # doctor's Language line. A value with no catalogue is named rather than
    # dropped, exactly as a theme that cannot be used is.
    language: Dict[str, Any]
    try:
        chosen_language = i18n.choose(lang_flag)
    except ValueError as exc:
        language = {
            "code": None,
            "name": None,
            "source": "",
            "available": i18n.available(),
            "problem": str(exc),
        }
    else:
        language = {
            "code": chosen_language.code,
            "name": chosen_language.name,
            "source": chosen_language.source,
            # What the machine asked for and Viddl does not have, if anything:
            # ``None`` rather than ``""`` so a script can test it directly.
            "unmatched": chosen_language.unmatched or None,
            "available": i18n.available(),
            "problem": None,
        }

    try:
        downloads = str(appdata.downloads_dir())
    except OSError:  # pragma: no cover - a home directory that cannot be read
        downloads = ""

    return {
        "name": APP_TITLE,
        "version": __version__,
        "folders": {
            "app": str(engine.home),
            "downloads": downloads,
            "log": str(appdata.log_path()),
        },
        # The first thing that goes wrong, in the order the doctor says it.
        "problem": problem.line if problem is not None else None,
        "ytdlp": _tool(ytdlp),
        "ytdlp_note": (
            engine_mod.staleness_note(ytdlp.version)
            if ytdlp is not None and ytdlp.version
            else ""
        ),
        "ffmpeg": _tool(engine.find_ffmpeg()),
        # The optional settings file (F13): what it holds, not what is in
        # force — the environment still outranks it and is listed below, and
        # a value the file got wrong is named rather than dropped quietly.
        "settings": {
            "path": str(stored.path),
            "file": stored.path.exists(),
            "values": dict(stored.values),
            "problems": list(stored.problems),
        },
        # What the environment asked for, and what became of it: the same list
        # the doctor prints under the tools (D2).
        "environment": [
            {"name": item.name, "value": item.value, "problem": item.problem}
            for item in appdata.overrides()
        ],
        # ``None`` means "not set", which is not the same as zero (R11).
        "timeout_seconds": appdata.network_timeout(),
        "archive": {
            "enabled": appdata.archive_enabled(),
            "path": str(appdata.archive_path()),
        },
        "recent": {"path": str(appdata.history_path())},
        "theme": theme,
        "language": language,
        "cookies": {
            "browsers": [
                {"key": browser.key, "label": browser.label}
                for browser in cookies_mod.installed()
            ]
        },
        "platform": os.name,
    }


def _quality(option: MediaOption) -> Dict[str, Any]:
    return {
        "key": option.key,
        "label": option.label,
        "kind": option.kind,
        # ``None`` is "unknown", never zero: a script must be able to tell an
        # unmeasured size from an empty file (the size column says "?" the same
        # way).
        "height": option.height,
        "size_bytes": option.size_bytes,
        "size_is_estimate": bool(option.size_is_estimate),
        "needs_ffmpeg": bool(option.needs_ffmpeg),
        "format_selector": option.format_selector,
        "merge_container": option.merge_container,
        "audio_format": option.audio_format,
        "keeps_source_audio": bool(option.keeps_source_audio),
        "note": option.note,
    }


def qualities_report(info: MediaInfo) -> Dict[str, Any]:
    """What ``viddl --list-qualities`` prints, as one object (F10).

    ``qualities`` is in the same order the screen shows them, because the first
    row is what ``--quality`` with no ``--quality`` picks — a script that reads
    ``.qualities[0].key`` gets the same answer a person pressing Enter does.
    """
    playlist: Optional[Dict[str, Any]] = None
    if info.playlist is not None:
        playlist = {
            "title": info.playlist.title,
            "count": info.playlist.count,
            "url": info.playlist.url,
            "note": info.playlist_note,
        }
    return {
        "url": info.url,
        "title": info.title,
        "uploader": info.uploader,
        # Seconds as a float, or ``None`` for a live stream or an unknown length.
        "duration": info.duration,
        "extractor": info.extractor,
        "live": bool(info.is_live),
        "playlist": playlist,
        "qualities": [_quality(option) for option in info.options],
    }


def dumps(payload: Dict[str, Any]) -> str:
    """One object as text: sorted keys, indented, and a newline at the end.

    Sorted so two runs of the same machine produce byte-identical output, which
    is what makes the format testable and diffable.
    """
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
