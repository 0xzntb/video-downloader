"""A list you can pick from with the mouse or the keyboard.

Textual's built-in option list only *highlights* on a single click, which makes
a terminal app feel broken to anyone using a mouse. This widget selects on a
plain click, on Enter, and on double click, and keeps arrow keys working.
"""

from __future__ import annotations

from typing import Any, List, Optional, Sequence, Tuple

from rich.text import Text
from textual.containers import VerticalScroll
from textual.events import Click
from textual.message import Message
from textual.widgets import Static

LABEL_WIDTH = 26
SIZE_WIDTH = 11


class ChoiceRow(Static):
    """One selectable line: a label, a right-aligned size, and a note."""

    class Chosen(Message):
        """Posted when the row is clicked."""

        def __init__(self, index: int) -> None:
            super().__init__()
            self.index = index

    # Deliberately prefixed: ``size`` and friends are taken by Textual's Widget.
    def __init__(self, index: int, label: str, size: str, note: str = "", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.row_index = index
        self.row_label = label
        self.row_size = size
        self.row_note = note
        self.is_active = False
        self._refresh_content()

    @property
    def plain(self) -> str:
        parts = [self.row_label, self.row_size]
        if self.row_note:
            parts.append(f"({self.row_note})")
        return "   ".join(part for part in parts if part)

    def _refresh_content(self) -> None:
        text = Text()
        label = self.row_label
        if len(label) > LABEL_WIDTH:
            label = label[: LABEL_WIDTH - 1].rstrip() + "…"
        text.append(label.ljust(LABEL_WIDTH), style="bold" if self.is_active else "")
        text.append(self.row_size.rjust(SIZE_WIDTH), style="cyan")
        if self.row_note:
            text.append("   " + self.row_note, style="dim italic")
        self.update(text)

    def set_active(self, active: bool) -> None:
        if active != self.is_active:
            self.is_active = active
            self._refresh_content()
        self.set_class(active, "active")

    def on_click(self, event: Click) -> None:
        event.stop()
        self.post_message(self.Chosen(self.row_index))


class ChoiceList(VerticalScroll):
    """A vertical list of :class:`ChoiceRow` widgets."""

    can_focus = True

    BINDINGS = [
        ("up", "cursor_up", "Up"),
        ("down", "cursor_down", "Down"),
        ("k", "cursor_up", "Up"),
        ("j", "cursor_down", "Down"),
        ("home", "cursor_first", "First"),
        ("end", "cursor_last", "Last"),
        ("enter", "choose", "Choose"),
    ]

    class Selected(Message):
        """Posted with the chosen row's value."""

        def __init__(self, value: Any, index: int) -> None:
            super().__init__()
            self.value = value
            self.index = index

    class Highlighted(Message):
        """Posted whenever the cursor moves to a different row."""

        def __init__(self, value: Any, index: int) -> None:
            super().__init__()
            self.value = value
            self.index = index

    def __init__(self, *children: Any, **kwargs: Any) -> None:
        super().__init__(*children, **kwargs)
        self._values: List[Any] = []
        self._index: Optional[int] = None

    # -- population ---------------------------------------------------------
    def set_choices(self, choices: Sequence[Tuple[str, str, str, Any]]) -> None:
        """Replace the contents. Each choice is (label, size, note, value)."""
        self.remove_children()
        self._values = []
        self._index = None
        rows: List[ChoiceRow] = []
        for index, (label, size, note, value) in enumerate(choices):
            self._values.append(value)
            rows.append(
                ChoiceRow(index, str(label), str(size), str(note or ""), classes="choice-row")
            )
        if rows:
            self.mount_all(rows)
            self._set_index(0, notify=False)

    # -- accessors ----------------------------------------------------------
    @property
    def row_count(self) -> int:
        return len(self._values)

    @property
    def cursor_index(self) -> Optional[int]:
        return self._index

    @property
    def highlighted_value(self) -> Any:
        if self._index is None or not self._values:
            return None
        return self._values[self._index]

    def value_at(self, index: int) -> Any:
        if 0 <= index < len(self._values):
            return self._values[index]
        return None

    def select_index(self, index: int) -> None:
        self._set_index(index)

    # -- cursor -------------------------------------------------------------
    def _rows(self) -> List[ChoiceRow]:
        return list(self.query(ChoiceRow))

    def _set_index(self, index: int, notify: bool = True) -> None:
        rows = self._rows()
        if not rows:
            self._index = None
            return
        index = max(0, min(index, len(rows) - 1))
        self._index = index
        for row in rows:
            row.set_active(row.row_index == index)
        active = rows[index]
        self.scroll_to_widget(active, animate=False)
        if notify:
            self.post_message(self.Highlighted(self._values[index], index))

    def _move(self, delta: int) -> None:
        if self._index is None:
            self._set_index(0)
            return
        self._set_index(self._index + delta)

    def action_cursor_up(self) -> None:
        self._move(-1)

    def action_cursor_down(self) -> None:
        self._move(1)

    def action_cursor_first(self) -> None:
        self._set_index(0)

    def action_cursor_last(self) -> None:
        self._set_index(len(self._values) - 1)

    def action_choose(self) -> None:
        if self._index is not None:
            self.post_message(self.Selected(self._values[self._index], self._index))

    def on_choice_row_chosen(self, message: ChoiceRow.Chosen) -> None:
        message.stop()
        self._set_index(message.index, notify=False)
        if message.index < len(self._values):
            self.post_message(self.Selected(self._values[message.index], message.index))
