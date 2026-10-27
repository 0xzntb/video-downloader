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

from viddl import __version__, appdata, logfile

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


# ---------------------------------------------------------------------------
# what a failure is written down as (E3)
# ---------------------------------------------------------------------------


def a_log(name: str, tmp_path: Path):
    logger = fresh_logger(name)
    target = tmp_path / "viddl.log"
    assert logfile.enable(logger, target) == target
    return logger, target


def test_a_failure_keeps_the_command_the_code_and_what_was_said(tmp_path):
    """The three things a bug report cannot supply and the screen does not show."""
    logger, target = a_log("failure", tmp_path)

    logfile.failure(
        "the download failed",
        command=["/app/bin/yt-dlp", "--ignore-config", "--", "https://x.invalid/v"],
        exit_code=2,
        detail="ERROR: Video unavailable",
        logger=logger,
    )

    written = text_of(target)
    assert "the download failed" in written
    # Which Viddl, on what — the first question asked of a pasted log.
    assert f"Viddl {__version__} on {sys.platform}" in written
    assert "  ran: /app/bin/yt-dlp --ignore-config -- https://x.invalid/v" in written
    assert "  exit code: 2" in written
    assert "  said:\n    ERROR: Video unavailable" in written


def test_the_command_line_is_redacted_like_every_other_line(tmp_path):
    """A log of failures is exactly where a signed link would end up."""
    logger, target = a_log("failure-token", tmp_path)

    logfile.failure(
        "the download failed",
        command=["/bin/yt-dlp", "--", "https://cdn.example/v?token=SECRET123&v=abc"],
        exit_code=1,
        detail="ERROR: https://cdn.example/seg?sig=SIGNED456 failed",
        logger=logger,
    )

    written = text_of(target)
    assert "SECRET123" not in written
    assert "SIGNED456" not in written
    assert logfile.REDACTED in written
    # Still worth reading: what was fetched, from where.
    assert "cdn.example/v" in written


def test_only_the_last_lines_are_kept_and_control_characters_go(tmp_path):
    """A tool explains itself when it gives up, and a name can carry an ESC."""
    logger, target = a_log("failure-tail", tmp_path)
    detail = "\n".join(f"line {number}" for number in range(1, 101))
    detail += "\n\x1b[31mred\x1b[0m"

    logfile.failure("the download failed", detail=detail, logger=logger)

    written = text_of(target)
    assert "line 100" in written
    # Forty entries are kept: lines 62 to 100, and the one with the escape in it.
    assert f"line {100 - logfile.DETAIL_LINES + 2}" in written
    assert f"line {100 - logfile.DETAIL_LINES + 1}" not in written
    assert "\x1b" not in written


def test_one_enormous_line_is_cut_short(tmp_path):
    """A tool can print a page on one line; the file has a limit too."""
    logger, target = a_log("failure-long", tmp_path)

    logfile.failure("the download failed", detail="x" * 5000, logger=logger)

    written = text_of(target)
    assert "x" * logfile.DETAIL_WIDTH in written
    assert "x" * (logfile.DETAIL_WIDTH + 1) not in written


def test_a_log_that_cannot_be_written_does_not_become_the_failure(tmp_path):
    """The download has already failed; the log is a nicety on top."""

    class Broken(logging.Handler):
        def emit(self, record):
            raise RuntimeError("the disk is full")

    logger = fresh_logger("failure-broken")
    logger.addHandler(Broken())

    # Must not raise, and must not stop the caller.
    logfile.failure("the download failed", command=["x"], detail="y", logger=logger)
