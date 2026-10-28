"""``python -m viddl`` — the entry point nothing else reaches (E10).

The console script in ``pyproject.toml`` starts the app through
``viddl.cli.main``; this is the same thing for a checkout nobody installed, and
it is three lines that nothing else runs: a module that imports the command
line, calls it, and hands its exit code to the shell.

They are covered the way the shell covers them — by running the file as
``__main__`` — rather than by importing a constant and checking it is not
``None``, which would pass for a file that never calls anything.
"""

from __future__ import annotations

import runpy

import pytest

import viddl.cli


def _as_python_m_viddl() -> int:
    """Run ``viddl/__main__.py`` exactly as ``python -m viddl`` does."""
    with pytest.raises(SystemExit) as caught:
        runpy.run_module("viddl", run_name="__main__")
    code = caught.value.code
    assert isinstance(code, int), "the shell is handed a number, not a message"
    return code


def test_python_m_viddl_calls_the_command_line(monkeypatch):
    seen = []
    monkeypatch.setattr(viddl.cli, "main", lambda: seen.append(True) or 0)

    assert _as_python_m_viddl() == 0
    assert seen == [True], "the module has to actually call main()"


def test_python_m_viddl_hands_the_exit_code_to_the_shell(monkeypatch):
    """A failed run must fail the command that started it, not exit 0.

    ``--doctor`` and the tests themselves read this number; a wrapper that
    swallowed it would report success for a download that never happened.
    """
    monkeypatch.setattr(viddl.cli, "main", lambda: 2)

    assert _as_python_m_viddl() == 2
