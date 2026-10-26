"""One catalogue, more than one language (U7).

The value being tested is not "Spanish exists" but that the two catalogues
cannot drift apart: every sentence has the same key, the same placeholders and
a different text, and a key that only one of them has is a failure rather than
a quietly untranslated line.
"""

from __future__ import annotations

import json
import string
import sys

import pytest

from viddl import cli, i18n, machine
from viddl.cli import main
from viddl.engine import Tool
from viddl.errors import SIGN_IN_MARK, explain
from viddl.i18n import en, es

URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

#: Every key ``errors.explain`` can ask for, including the fallbacks that are
#: not attached to a rule.
FALLBACK_KEYS = (
    "error.stopped_early",
    "error.stopped_early.hint",
    "error.generic",
    "error.reported",
    "error.reported.advice",
    "error.unfinished",
)


def rule_keys():
    from viddl import errors

    keys = []
    for rule in errors._RULES:
        keys.append(rule.message)
        if rule.hint:
            keys.append(rule.hint)
    return keys


def fields(text: str):
    """The ``{...}`` names a sentence fills in, in a stable order."""
    return sorted(
        {name for _, name, _, _ in string.Formatter().parse(text) if name}
    )


# ---------------------------------------------------------------------------
# the catalogues
# ---------------------------------------------------------------------------


def test_a_shipped_language_has_exactly_the_keys_english_has():
    """No missing key (a silent English line) and no invented one (a typo)."""
    english = set(en.MESSAGES)
    for code, catalogue in i18n.CATALOGUES.items():
        assert set(catalogue) == english, code


def test_the_english_catalogue_covers_every_rule_and_every_fallback():
    for key in [*rule_keys(), *FALLBACK_KEYS]:
        assert key in en.MESSAGES, key
        assert en.MESSAGES[key].strip(), key


def test_every_translation_fills_in_the_same_values_as_the_english():
    for code, catalogue in i18n.CATALOGUES.items():
        for key, text in catalogue.items():
            assert fields(text) == fields(en.MESSAGES[key]), (code, key)


def test_a_translation_is_actually_translated():
    """A key copied over from English is a translation nobody finished."""
    same = [key for key, text in es.MESSAGES.items() if text == en.MESSAGES[key]]
    assert same == []


def test_a_key_missing_from_a_translation_falls_back_to_english(monkeypatch):
    """A catalogue still being written shows English, never a key name."""
    partial = dict(es.MESSAGES)
    del partial["error.private"]
    monkeypatch.setitem(i18n.CATALOGUES, "es", partial)
    i18n.set_locale("es")

    assert i18n.t("error.private") == en.MESSAGES["error.private"]
    # The rest of the language is still Spanish.
    assert i18n.t("error.private.hint") == es.MESSAGES["error.private.hint"]


def test_a_key_no_catalogue_has_is_a_mistake():
    with pytest.raises(KeyError):
        i18n.t("error.no_such_thing")


def test_a_placeholder_with_no_value_is_a_mistake():
    with pytest.raises(KeyError):
        i18n.t("error.sign_in.hint", unexpected="value")


# ---------------------------------------------------------------------------
# which language
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected",
    [
        ("es", "es"),
        ("es_ES.UTF-8", "es"),
        ("es-MX", "es"),
        ("ES", "es"),
        ("es_ES.UTF-8@euro", "es"),
        # A GNU list: the first language we have wins.
        ("fr:es", "es"),
        ("en_GB", "en"),
        ("fr_FR.UTF-8", None),
        ("C", None),
        ("POSIX", None),
        ("C.UTF-8", None),
        ("", None),
    ],
)
def test_a_locale_name_is_read_down_to_its_language(value, expected):
    assert i18n.negotiate(value) == expected


def test_english_is_the_default_when_nothing_asks():
    chosen = i18n.choose("", {})
    assert chosen.code == "en"
    assert chosen.source == "the default"
    assert chosen.unmatched == ""


def test_the_flag_beats_the_machine():
    chosen = i18n.choose("es", {"LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8"})
    assert (chosen.code, chosen.source) == ("es", "--lang")


def test_the_flag_takes_a_full_locale_name_too():
    assert i18n.choose("es_MX.UTF-8", {}).code == "es"


def test_the_machine_is_read_in_posix_order():
    both = {"LC_ALL": "es_ES.UTF-8", "LC_MESSAGES": "en_GB.UTF-8", "LANG": "en_US.UTF-8"}
    assert i18n.choose("", both).code == "es"
    messages = {"LC_MESSAGES": "es_ES.UTF-8", "LANG": "en_US.UTF-8"}
    chosen = i18n.choose("", messages)
    assert (chosen.code, chosen.source) == ("es", "LC_MESSAGES=es_ES.UTF-8")
    # An empty setting is unset, not "the empty language".
    empty = {"LC_ALL": "", "LANG": "es_ES.UTF-8"}
    assert i18n.choose("", empty).code == "es"


def test_a_language_the_machine_asks_for_that_viddl_lacks_still_works():
    chosen = i18n.choose("", {"LANG": "fr_FR.UTF-8"})
    assert (chosen.code, chosen.source) == ("en", "LANG=fr_FR.UTF-8")
    assert chosen.unmatched == "fr_FR.UTF-8"
    assert "no translation for fr_FR.UTF-8 yet" in chosen.summary


def test_the_flag_refuses_a_language_viddl_does_not_speak():
    """Typed by a person, so it is answered rather than silently ignored."""
    with pytest.raises(ValueError) as caught:
        i18n.choose("fr", {})
    message = str(caught.value)
    assert "'fr'" in message
    assert "not a language Viddl speaks yet" in message
    assert "en, es" in message


def test_setting_a_language_takes_a_full_locale_name():
    assert i18n.set_locale("es_ES.UTF-8") == "es"
    assert i18n.current_locale() == "es"


def test_setting_a_language_there_is_no_catalogue_for_is_refused():
    with pytest.raises(ValueError):
        i18n.set_locale("fr")
    # The refusal did not change the language in force.
    assert i18n.current_locale() == "en"


# ---------------------------------------------------------------------------
# the explanations
# ---------------------------------------------------------------------------


def test_the_explanation_changes_language():
    english = explain("ERROR: Private video. Sign in if you've been granted access")
    assert english.message == "That video is private."

    i18n.set_locale("es")
    spanish = explain("ERROR: Private video. Sign in if you've been granted access")
    assert spanish.message == "Ese vídeo es privado."
    assert spanish.hint == es.MESSAGES["error.private.hint"]
    assert spanish.temporary is False


def test_the_retry_decision_does_not_change_with_the_language():
    """The rule's answer about retrying is not prose, so it must not move."""
    raw = "ERROR: HTTP Error 429: Too Many Requests"
    english = explain(raw)
    i18n.set_locale("es")
    spanish = explain(raw)
    assert english.temporary is True
    assert spanish.temporary is True
    assert spanish.message != english.message


def test_the_advice_keeps_naming_the_row_that_signs_in():
    """The mark is English because the screen is; the advice still quotes it."""
    i18n.set_locale("es")
    friendly = explain("ERROR: Sign in to confirm you're not a bot")
    assert SIGN_IN_MARK in (friendly.hint or "")


def test_the_fallbacks_speak_the_language_too():
    i18n.set_locale("es")
    assert explain("").message == es.MESSAGES["error.generic"]
    assert explain("", exit_code=1).message == es.MESSAGES["error.stopped_early"]

    friendly = explain("ERROR: something odd happened")
    assert friendly.message == es.MESSAGES["error.reported"]
    assert es.MESSAGES["error.reported.advice"] in (friendly.hint or "")


def test_the_doctor_line_names_the_language_and_where_it_came_from():
    assert "English (en, from the default)" in i18n.doctor_line("", {})
    assert "Español (es, from --lang)" in i18n.doctor_line("es", {})
    assert "Language:" in i18n.doctor_line("es", {})


# ---------------------------------------------------------------------------
# the command line
# ---------------------------------------------------------------------------


def test_the_command_line_refuses_a_language_viddl_does_not_speak(capsys):
    assert main(["--lang", "fr", "--doctor"]) == 1

    err = capsys.readouterr().err
    assert "Viddl: 'fr' is not a language Viddl speaks yet" in err
    assert "en, es" in err


def test_the_doctor_says_which_language_is_in_force(capsys):
    assert main(["--lang", "es", "--doctor"]) == 0

    out = capsys.readouterr().out
    assert "Language:" in out
    assert "Español (es, from --lang)" in out


def test_the_doctor_as_json_carries_the_language(capsys):
    assert main(["--lang", "es", "--doctor", "--json"]) == 0

    report = json.loads(capsys.readouterr().out)
    assert report["language"]["code"] == "es"
    assert report["language"]["name"] == "Español"
    assert report["language"]["source"] == "--lang"
    assert report["language"]["problem"] is None
    assert report["language"]["available"] == ["en", "es"]


class _FakeEngine:
    """Enough engine to let the real look-up run and fail.

    The version is old on purpose: a failure gets the staleness advice appended
    to it (F3), and that advice is part of the answer, so the test can hold the
    whole answer to one language (U7).
    """

    def ensure_ytdlp(self):
        return Tool("yt-dlp", [sys.executable, "-c", "pass"], "viddl", version="2020.01.01")

    def find_ytdlp(self):
        return self.ensure_ytdlp()


def _private_video(monkeypatch):
    """A look-up whose yt-dlp said the video is private."""
    monkeypatch.setattr(cli, "Engine", _FakeEngine)
    monkeypatch.setattr(
        "viddl.download._run_lookup",
        lambda *args, **kwargs: (
            1,
            "",
            "ERROR: Private video. Sign in if you've been granted access",
        ),
    )


def test_a_spanish_run_explains_a_failure_in_spanish(monkeypatch, capsys):
    """The whole path: the flag, the engine's words, the sentence on stderr."""
    _private_video(monkeypatch)

    assert main(["--lang", "es", "--list-qualities", URL]) == 1

    err = capsys.readouterr().err
    assert "Ese vídeo es privado." in err
    assert "Solo puede descargarlo su dueño" in err
    # The advice the app adds by itself is part of the same answer.
    assert "El motor de vídeo tiene" in err
    assert "That video is private" not in err
    assert "The video engine is" not in err


def test_a_spanish_machine_needs_no_flag(monkeypatch, capsys):
    """LC_ALL/LANG is what a Spanish computer already has set (U7)."""
    monkeypatch.setenv("LANG", "es_ES.UTF-8")
    _private_video(monkeypatch)

    assert main(["--list-qualities", URL]) == 1

    err = capsys.readouterr().err
    assert "Ese vídeo es privado." in err


def test_the_flag_beats_a_spanish_machine(monkeypatch, capsys):
    monkeypatch.setenv("LANG", "es_ES.UTF-8")
    _private_video(monkeypatch)

    assert main(["--lang", "en", "--list-qualities", URL]) == 1

    err = capsys.readouterr().err
    assert "That video is private." in err
    assert "The video engine is" in err


def test_an_old_engine_is_advised_in_the_language_in_force():
    from datetime import date

    from viddl import engine

    english = engine.staleness_advice("2020.01.01", date(2026, 1, 1))
    assert english.startswith("The video engine is")

    i18n.set_locale("es")
    spanish = engine.staleness_advice("2020.01.01", date(2026, 1, 1))
    assert spanish.startswith("El motor de vídeo tiene")
    assert "--update" in spanish


def test_the_machine_report_reads_the_language_from_a_variable(monkeypatch):
    monkeypatch.setenv("LC_ALL", "en_GB.UTF-8")
    assert machine.doctor_report()["language"]["code"] == "en"
    assert machine.doctor_report()["language"]["source"] == "LC_ALL=en_GB.UTF-8"
