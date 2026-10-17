"""The log file: what it lets in, and who can read it.

Viddl's log is read only after something has gone wrong, which is precisely the
moment it must not be holding anything it should not, and must not have quietly
stopped recording yesterday.
"""

from __future__ import annotations

import logging
import os
import stat
import sys
from pathlib import Path

import pytest

from viddl import appdata, logfile

needs_posix_permissions = pytest.mark.skipif(
    sys.platform.startswith("win"), reason="Windows governs access with ACLs"
)


def mode_of(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def fresh_logger(name: str) -> logging.Logger:
    """A logger of its own, so one test cannot see another test's handler."""
    logger = logging.getLogger(f"viddl-test-{name}")
    logger.handlers.clear()
    logger.propagate = False
    return logger


def text_of(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# who can read it
# ---------------------------------------------------------------------------


@needs_posix_permissions
def test_the_log_is_readable_only_by_its_owner(tmp_path):
    target = tmp_path / "app" / "viddl.log"
    assert logfile.enable(fresh_logger("fresh"), target) == target
    assert mode_of(target) == 0o600


@needs_posix_permissions
def test_a_log_left_loose_by_an_earlier_version_is_tightened(tmp_path, monkeypatch):
    home = tmp_path / "app"
    home.mkdir()
    stale = home / "viddl.log"
    stale.write_text("an older run\n", encoding="utf-8")
    os.chmod(stale, 0o644)
    monkeypatch.setenv("VIDDL_HOME", str(home))

    logfile.enable(fresh_logger("stale"))

    assert mode_of(stale) == 0o600
    # Tightening must not cost the history it was already holding.
    assert text_of(stale).startswith("an older run")


@needs_posix_permissions
def test_a_rotation_leaves_an_owner_only_file_behind_too(tmp_path):
    """The mode has to survive rotation, not just the first file."""
    target = tmp_path / "viddl.log"
    logger = fresh_logger("rotation")
    logfile.enable(logger, target)

    logger.info("x" * 200)
    for _ in range(logfile.LOG_MAX_BYTES // 200 + 5):
        logger.info("y" * 200)

    backup = tmp_path / "viddl.log.1"
    assert backup.is_file(), "the log should have rolled over at its size limit"
    assert mode_of(backup) == 0o600
    assert mode_of(target) == 0o600
    assert target.stat().st_size < logfile.LOG_MAX_BYTES


def test_a_log_that_is_already_too_large_rolls_over_at_once(tmp_path):
    """Otherwise a log from a bad day would simply keep growing."""
    target = tmp_path / "viddl.log"
    target.write_text("old line\n" * 40_000, encoding="utf-8")
    assert target.stat().st_size > logfile.LOG_MAX_BYTES

    logger = fresh_logger("oversized")
    logfile.enable(logger, target)
    logger.info("the first new line")

    assert (tmp_path / "viddl.log.1").is_file()
    assert target.stat().st_size < logfile.LOG_MAX_BYTES
    assert "the first new line" in text_of(target)


def test_only_the_last_few_rolls_are_kept(tmp_path):
    target = tmp_path / "viddl.log"
    logger = fresh_logger("bounded")
    logfile.enable(logger, target)
    for _ in range(logfile.LOG_MAX_BYTES // 100 * 3):
        logger.info("z" * 100)

    rolls = sorted(tmp_path.glob("viddl.log.*"))
    assert len(rolls) == logfile.BACKUP_COUNT


# ---------------------------------------------------------------------------
# what it lets in
# ---------------------------------------------------------------------------


def test_a_token_in_a_link_is_not_written_down(tmp_path):
    target = tmp_path / "viddl.log"
    logger = fresh_logger("token")
    logfile.enable(logger, target)

    logger.error(
        "could not fetch https://cdn.example.com/v?token=SECRET123&v=dQw4w9WgXcQ"
    )

    written = text_of(target)
    assert "SECRET123" not in written
    assert logfile.REDACTED in written
    # Still worth reading: what was fetched, and from where.
    assert "cdn.example.com/v" in written
    assert "v=dQw4w9WgXcQ" in written


def test_a_traceback_carries_the_redaction_too(tmp_path):
    """The usual reason to open the log is a traceback, so it counts as a line."""
    target = tmp_path / "viddl.log"
    logger = fresh_logger("traceback")
    logfile.enable(logger, target)

    try:
        raise RuntimeError("failed for https://x.example.com/v?sig=SIGNED456&v=abc")
    except RuntimeError:
        logger.exception("Viddl could not update the screen")

    written = text_of(target)
    assert "SIGNED456" not in written
    assert "Traceback" in written
    assert logfile.REDACTED in written


def test_writing_happens_once_per_record(tmp_path):
    target = tmp_path / "viddl.log"
    logger = fresh_logger("twice")
    logfile.enable(logger, target)
    logfile.enable(logger, target)  # a second call must not double every line

    logger.info("only once")

    assert text_of(target).count("only once") == 1


# ---------------------------------------------------------------------------
# a log is a nicety, never a requirement
# ---------------------------------------------------------------------------


def test_a_log_that_cannot_be_created_is_left_off(tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("not a folder", encoding="utf-8")
    logger = fresh_logger("blocked")

    assert logfile.enable(logger, blocker / "viddl.log") is None
    assert logger.handlers == []
    logger.warning("Viddl carries on without a log")  # must not raise


def test_no_log_is_started_when_the_app_folder_cannot_be_used(
    tmp_path, monkeypatch
):
    """``appdata`` says where the log goes; when it cannot, there is no log."""

    def nowhere():
        raise OSError("read-only file system")

    monkeypatch.setattr(appdata, "log_path", nowhere)
    logger = fresh_logger("nowhere")

    assert logfile.enable(logger) is None
    assert logger.handlers == []
