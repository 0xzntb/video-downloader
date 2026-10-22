"""Reading the clipboard, once, without trusting it (F8).

Nothing here touches a real clipboard: the platform's tool is stood in for, so
what is tested is which tool is asked, how its answer is treated, and what
happens when there is no tool at all.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from viddl import clipboard


class Ran:
    """A ``subprocess.run`` stand-in: answers per program name, and records."""

    def __init__(self, answers=None):
        #: program -> (returncode, stdout) or an exception to raise.
        self.answers = answers or {}
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append(list(command))
        answer = self.answers.get(command[0])
        if isinstance(answer, Exception):
            raise answer
        if answer is None:
            raise FileNotFoundError(2, "No such file or directory")
        code, out = answer
        return subprocess.CompletedProcess(command, code, out, "")


@pytest.fixture
def ran(monkeypatch):
    fake = Ran()
    monkeypatch.setattr(clipboard.subprocess, "run", fake)
    return fake


def test_linux_asks_wayland_first_then_x11(monkeypatch, ran):
    monkeypatch.setattr(clipboard.sys, "platform", "linux")
    ran.answers = {"xclip": (0, "https://example.com/v\n")}

    assert clipboard.read_text() == "https://example.com/v"
    # wl-paste is not installed, so it was tried and skipped; xclip answered.
    assert [call[0] for call in ran.calls] == ["wl-paste", "xclip"]


def test_macos_uses_pbpaste_and_windows_uses_powershell(monkeypatch, ran):
    monkeypatch.setattr(clipboard.sys, "platform", "darwin")
    ran.answers = {"pbpaste": (0, "https://example.com/a")}
    assert clipboard.read_text() == "https://example.com/a"
    assert ran.calls == [["pbpaste"]]

    ran.calls.clear()
    monkeypatch.setattr(clipboard.sys, "platform", "win32")
    ran.answers = {"powershell": (0, "https://example.com/b\r\n")}
    assert clipboard.read_text() == "https://example.com/b"
    assert ran.calls[0][0] == "powershell"
    # A fixed argument list, and no shell: a clipboard is not syntax.
    assert "-Command" in ran.calls[0]
    assert "Get-Clipboard -Raw" in ran.calls[0]


def test_nothing_to_read_is_an_empty_string_not_an_error(monkeypatch, ran):
    monkeypatch.setattr(clipboard.sys, "platform", "linux")
    assert clipboard.read_text() == ""
    assert len(ran.calls) == 3  # every reader was tried and failed


def test_a_reader_that_fails_does_not_stop_the_next(monkeypatch, ran):
    monkeypatch.setattr(clipboard.sys, "platform", "linux")
    ran.answers = {
        "wl-paste": (1, ""),
        "xclip": (0, "anything at all"),
    }

    assert clipboard.read_text() == "anything at all"


def test_a_reader_that_hangs_gives_up_instead_of_waiting(monkeypatch, ran):
    monkeypatch.setattr(clipboard.sys, "platform", "linux")
    ran.answers = {
        "wl-paste": subprocess.TimeoutExpired(["wl-paste"], 2.0),
        "xclip": (0, "https://example.com/late"),
    }

    assert clipboard.read_text() == "https://example.com/late"


def test_the_timeout_is_passed_to_every_reader(monkeypatch, ran):
    monkeypatch.setattr(clipboard.sys, "platform", "linux")
    recorded = []

    def run(command, **kwargs):
        recorded.append(kwargs.get("timeout"))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(clipboard.subprocess, "run", run)
    clipboard.read_text(timeout=0.5)

    assert recorded == [0.5]


def test_a_null_byte_and_trailing_space_are_stripped(monkeypatch, ran):
    monkeypatch.setattr(clipboard.sys, "platform", "linux")
    ran.answers = {"wl-paste": (0, "\x00 https://example.com/x \r\n\x00")}

    assert clipboard.read_text() == "https://example.com/x"


def test_a_broken_reader_module_never_raises(monkeypatch, ran):
    """Whatever the platform does, the answer is text or nothing."""
    monkeypatch.setattr(clipboard.sys, "platform", "linux")
    ran.answers = {
        "wl-paste": OSError(13, "Permission denied"),
        "xclip": subprocess.SubprocessError("boom"),
    }

    assert clipboard.read_text() == ""


def test_every_platform_uses_a_fixed_argument_list(monkeypatch):
    """No reader may depend on a value from the environment to be safe."""
    for platform in ("linux", "darwin", "win32"):
        monkeypatch.setattr(sys, "platform", platform)
        for command in clipboard.commands():
            assert isinstance(command, list)
            assert command and all(isinstance(part, str) for part in command)
            assert not any(part in ("|", "&&", ";") for part in command)
