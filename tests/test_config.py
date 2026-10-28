"""The optional settings file, and the order the answers come in (F13).

Three promises are held here: nothing is read or written until a preference is
actually changed, a value that Viddl cannot use is ignored *and said so* rather
than half-applied, and the order is exactly flags > environment > file >
defaults — the last one being the kind of thing that misbehaves quietly.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from viddl import appdata, config, plain
from viddl.formats import AUDIO_FORMATS

URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.fixture
def no_environment(monkeypatch):
    """Every variable Viddl reads, cleared — the file is then the only voice."""
    for name in appdata.ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def write_config(text: str) -> Path:
    path = config.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# zero-config by default
# ---------------------------------------------------------------------------


def test_nothing_is_written_until_a_preference_changes(no_environment, tmp_path):
    """Reading a setting must never leave a file behind (F13)."""
    config.download_dir()
    config.quality()
    config.archive()
    config.report()
    appdata.downloads_dir()

    assert config.config_path() == tmp_path / "home" / ".config" / "viddl" / "config.toml"
    assert not config.config_path().exists()


def test_the_report_says_nothing_is_configured(no_environment):
    lines = config.report()

    assert lines[0].startswith("Settings:")
    assert "no settings" not in lines[0]
    assert "none — every setting is a default" in lines[0]
    assert "viddl --set download-dir" in lines[0]


# ---------------------------------------------------------------------------
# writing and reading back
# ---------------------------------------------------------------------------


def test_a_changed_quality_is_written_and_read_back(no_environment):
    assert config.set_value("quality", "720") == "720"

    assert config.config_path().read_text(encoding="utf-8") == 'quality = "720"\n'
    assert config.quality() == "720"
    assert config.read().values == {"quality": "720"}


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_the_file_is_readable_only_by_its_owner(no_environment):
    config.set_value("quality", "best")

    mode = stat.S_IMODE(config.config_path().stat().st_mode)
    assert mode == 0o600


def test_a_folder_with_a_space_and_a_hash_survives_the_round_trip(no_environment, tmp_path):
    folder = tmp_path / "my # videos"
    folder.mkdir()

    config.set_value("download-dir", str(folder))

    assert config.download_dir() == folder.resolve()
    assert appdata.downloads_dir() == folder.resolve()


def test_a_yes_or_no_is_stored_as_a_boolean(no_environment):
    assert config.set_value("archive", "yes") == "true"
    assert "archive = true" in config.config_path().read_text(encoding="utf-8")
    assert config.archive() is True

    assert config.set_value("archive", "off") == "false"
    assert "archive = false" in config.config_path().read_text(encoding="utf-8")
    assert config.archive() is False


def test_a_comment_and_an_unknown_key_are_read_and_kept(no_environment):
    """A comment is ignored; a key from a newer version is preserved (F13)."""
    path = write_config('# a note\n\nvolume = "loud"   # not ours yet\nquality = "480"\n')

    config.set_value("archive", "on")

    text = path.read_text(encoding="utf-8")
    assert 'volume = "loud"' in text
    assert "archive = true" in text
    assert config.read().values == {"quality": "480", "archive": "true"}


def test_a_hand_written_single_quoted_value_is_understood(no_environment):
    write_config("quality = '480'\n")

    assert config.quality() == "480"


def test_unsetting_removes_the_key_and_then_the_file(no_environment):
    config.set_value("quality", "720")

    assert config.unset("quality") is True
    assert not config.config_path().exists()
    # A second unset is not an error: there is nothing to remove, and that is
    # an honest answer rather than a failure.
    assert config.unset("quality") is False


# ---------------------------------------------------------------------------
# values that cannot be used
# ---------------------------------------------------------------------------


def test_a_value_viddl_cannot_use_is_ignored_and_named(no_environment):
    write_config('quality = "banana"\narchive = "maybe"\n')

    stored = config.read()

    assert stored.values == {}
    assert config.quality() == ""
    assert any("quality" in problem and "banana" in problem for problem in stored.problems)
    assert any("archive" in problem for problem in stored.problems)
    # The doctor says so, rather than the default applying in silence.
    report = config.report()
    assert any("ignored" in line for line in report[1:])


def test_a_line_that_is_not_a_setting_is_reported(no_environment):
    write_config('not a setting\nquality = "720"\n')

    stored = config.read()

    assert stored.values == {"quality": "720"}
    assert any("line 1" in problem for problem in stored.problems)


def test_a_value_with_an_escape_viddl_does_not_write_is_refused(no_environment):
    write_config('quality = "a\\qb"\n')

    stored = config.read()

    assert stored.values == {}
    assert any("escape" in problem for problem in stored.problems)


def test_an_unknown_setting_name_is_refused_with_the_list(no_environment):
    with pytest.raises(ValueError) as caught:
        config.set_value("volume", "loud")

    assert "Unknown setting 'volume'" in str(caught.value)
    assert "download-dir, quality, archive, theme" in str(caught.value)
    assert not config.config_path().exists()

    with pytest.raises(ValueError):
        config.unset("volume")


def test_a_folder_that_is_really_a_file_is_refused(no_environment, tmp_path):
    blocker = tmp_path / "a file"
    blocker.write_text("not a folder", encoding="utf-8")

    with pytest.raises(ValueError) as caught:
        config.set_value("download-dir", str(blocker))

    assert "is a file, not a folder" in str(caught.value)
    assert not config.config_path().exists()


@pytest.mark.parametrize("value", ["", "-rf", "a\nb"])
def test_a_folder_that_could_be_something_else_is_refused(no_environment, value):
    with pytest.raises(ValueError):
        config.set_value("download-dir", value)


def test_a_quality_must_be_one_the_command_line_would_accept(no_environment):
    with pytest.raises(ValueError) as caught:
        config.set_value("quality", "ultra")

    assert "not a quality" in str(caught.value)
    assert "720" in str(caught.value)


def test_every_quality_the_command_line_accepts_can_be_stored(no_environment):
    """A guard against the two lists drifting apart (F13).

    ``quality`` in the file is only useful if ``--quality`` would have accepted
    the same word, so every spelling the download path knows is written here.
    """
    words = set(plain._AUDIO_WORDS) | set(plain._AUDIO_ALIASES)
    words |= set(plain._AUDIO_FORMAT_WORDS) | {"best", "any", "720", "1080p"}
    for audio_format in AUDIO_FORMATS:
        for preset in audio_format.presets:
            words.add(f"{audio_format.key}-{preset.bitrate}")

    for word in sorted(words):
        assert config.set_value("quality", word) == word.lower()


# ---------------------------------------------------------------------------
# the order: flags > environment > file > defaults
# ---------------------------------------------------------------------------


def test_the_environment_beats_the_file(no_environment, monkeypatch):
    config.set_value("quality", "720")
    monkeypatch.setenv("VIDDL_QUALITY", "audio")

    assert config.quality() == "audio"


def test_an_empty_environment_variable_means_the_default_not_the_file(
    no_environment, monkeypatch
):
    """Somebody who emptied it answered the question; the file is not asked."""
    config.set_value("quality", "720")
    monkeypatch.setenv("VIDDL_QUALITY", "")

    assert config.quality() == ""


def test_the_file_beats_the_platform_default(no_environment, tmp_path):
    folder = tmp_path / "Videos"
    folder.mkdir()
    config.set_value("download-dir", str(folder))

    assert appdata.downloads_dir() == folder.resolve()


def test_the_environment_beats_the_download_folder_in_the_file(
    no_environment, monkeypatch, tmp_path
):
    configured = tmp_path / "From the file"
    configured.mkdir()
    config.set_value("download-dir", str(configured))
    chosen = tmp_path / "From the environment"
    monkeypatch.setenv("VIDDL_DOWNLOAD_DIR", str(chosen))

    assert appdata.downloads_dir() == chosen


def test_an_unusable_environment_variable_does_not_fall_back_to_the_file(
    no_environment, monkeypatch, tmp_path
):
    """A run that was told something is not a run that was told nothing."""
    configured = tmp_path / "From the file"
    configured.mkdir()
    config.set_value("download-dir", str(configured))
    monkeypatch.setenv("VIDDL_DOWNLOAD_DIR", "-rf")

    assert appdata.downloads_dir() == Path.home() / "Downloads"
    assert appdata.folder_override(appdata.ENV_DOWNLOAD_DIR) is None


def test_the_archive_follows_the_same_order(no_environment, monkeypatch):
    config.set_value("archive", "on")
    assert appdata.archive_enabled() is True

    monkeypatch.setenv("VIDDL_ARCHIVE", "0")
    assert appdata.archive_enabled() is False


def test_an_unusable_archive_variable_does_not_fall_back_to_the_file(
    no_environment, monkeypatch
):
    config.set_value("archive", "on")
    monkeypatch.setenv("VIDDL_ARCHIVE", "sometimes")

    assert appdata.archive_enabled() is False
    assert appdata.archive_problem()


def test_the_environment_quality_is_checked_before_it_is_used(no_environment, monkeypatch):
    assert config.quality_problem() == ""

    monkeypatch.setenv("VIDDL_QUALITY", "720")
    assert config.quality_problem() == ""

    monkeypatch.setenv("VIDDL_QUALITY", "ultra")
    assert "not a quality" in config.quality_problem()


def test_the_doctor_lists_the_quality_variable_with_its_verdict(no_environment, monkeypatch):
    monkeypatch.setenv("VIDDL_QUALITY", "720")
    assert any(
        "VIDDL_QUALITY=720 (in use)" in line for line in appdata.environment_report()
    )

    monkeypatch.setenv("VIDDL_QUALITY", "ultra")
    assert any(
        "VIDDL_QUALITY=ultra (ignored" in line for line in appdata.environment_report()
    )


def test_a_theme_is_written_and_read_back(no_environment):
    """The fourth preference, and the only one that changes a colour (U1)."""
    assert config.set_value("theme", " Light ") == "light"

    assert config.config_path().read_text(encoding="utf-8") == 'theme = "light"\n'
    assert config.theme_name() == "light"


def test_a_theme_must_be_one_the_app_can_draw(no_environment):
    with pytest.raises(ValueError) as caught:
        config.set_value("theme", "banana")
    assert "not a theme" in str(caught.value)
    assert not config.config_path().exists()


def test_the_theme_variable_beats_the_file(no_environment, monkeypatch):
    config.set_value("theme", "light")

    monkeypatch.setenv("VIDDL_THEME", "high-contrast")
    assert config.theme_name() == "high-contrast"

    # Empty still counts as spoken: it means auto, not "ask the file".
    monkeypatch.setenv("VIDDL_THEME", "")
    assert config.theme_name() == ""
    assert config.theme_problem() == ""


def test_the_theme_variable_is_checked_before_it_is_used(no_environment, monkeypatch):
    assert config.theme_problem() == ""

    monkeypatch.setenv("VIDDL_THEME", "auto")
    assert config.theme_problem() == ""

    monkeypatch.setenv("VIDDL_THEME", "banana")
    assert "not a theme" in config.theme_problem()


def test_the_doctor_lists_the_theme_variable_with_its_verdict(
    no_environment, monkeypatch
):
    monkeypatch.setenv("VIDDL_THEME", "light")
    assert any(
        "VIDDL_THEME=light (in use)" in line for line in appdata.environment_report()
    )

    monkeypatch.setenv("VIDDL_THEME", "banana")
    assert any(
        "VIDDL_THEME=banana (ignored" in line for line in appdata.environment_report()
    )


def test_the_report_names_the_file_and_what_is_in_it(no_environment):
    config.set_value("quality", "720")

    line = config.report()[0]

    assert str(config.config_path()) in line
    assert "quality" in line


# ---------------------------------------------------------------------------
# notifications (F9)
# ---------------------------------------------------------------------------


def test_notify_is_off_until_it_is_asked_for(no_environment):
    assert config.notify_enabled() is False

    assert config.set_value("notify", "on") == "true"
    assert "notify = true" in config.config_path().read_text(encoding="utf-8")
    assert config.notify_enabled() is True

    assert config.set_value("notify", "no") == "false"
    assert config.notify_enabled() is False


def test_the_notify_variable_beats_the_file(no_environment, monkeypatch):
    config.set_value("notify", "true")
    monkeypatch.setenv("VIDDL_NOTIFY", "off")

    assert config.notify_enabled() is False


def test_an_empty_notify_variable_means_the_default_not_the_file(
    no_environment, monkeypatch
):
    """An answered question is not asked again of the file underneath it."""
    config.set_value("notify", "true")
    monkeypatch.setenv("VIDDL_NOTIFY", "")

    assert config.notify_enabled() is False


def test_a_notify_value_that_is_neither_yes_nor_no_is_refused(no_environment):
    with pytest.raises(ValueError):
        config.set_value("notify", "sometimes")
    assert config.config_path().exists() is False
