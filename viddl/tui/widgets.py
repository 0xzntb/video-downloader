"""A list you can pick from with the mouse or the keyboard.

Textual's built-in option list only *highlights* on a single click, which makes
a terminal app feel broken to anyone using a mouse. This widget selects on a
plain click, on Enter, and on double click, and keeps arrow keys working.
"""

from __future__ import annotations

from typing import Any, ClassVar, List, Optional, Sequence, Tuple

from rich.text import Text
from textual.binding import BindingType
from textual.containers import VerticalScroll
from textual.events import Click
from textual.message import Message
from textual.widgets import Static

LABEL_WIDTH = 26
SIZE_WIDTH = 11

#: What the row under the cursor is marked with (U6). A character rather
#: than a colour: ``NO_COLOR``, a monochrome terminal and a screen reader
#: all get the same information from it, and — like every other character
#: Viddl draws — it has to exist in cp1252 for the old console the plain
#: path exists for. U+203A does; the arrow U+25B8 does not.
CURSOR_MARK = "›"


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

    def _theme_colour(self, name: str) -> str:
        """A colour the current theme gives a name, or ``""`` before there is one (U1).

        A Rich ``Text`` carries its colour with it, so it cannot be written in
        the stylesheet like the rest of the screen — but it is still read from
        the theme instead of being a literal called ``cyan``.
        """
        try:
            variables = self.app.theme_variables
        except Exception:
            # Built before it has an app: the first real paint happens after
            # ``on_mount``, which is where the colours are filled in.
            return ""
        return str(variables.get(name, ""))

    def _refresh_content(self) -> None:
        text = Text()
        # The cursor is a mark, not only a background: the row under it has
        # to be findable with colour turned off, which is what NO_COLOR and
        # a monochrome terminal both do (U6).
        text.append(f"{CURSOR_MARK} " if self.is_active else "  ")
        label = self.row_label
        if len(label) > LABEL_WIDTH:
            label = label[: LABEL_WIDTH - 1].rstrip() + "…"
        text.append(label.ljust(LABEL_WIDTH), style="bold" if self.is_active else "")
        # On the active row the background is $primary, and the theme's own
        # on-primary colour is the readable choice there; elsewhere the size
        # is the scheme's accent (U1).
        size_style = "" if self.is_active else self._theme_colour("size-foreground")
        text.append(self.row_size.rjust(SIZE_WIDTH), style=size_style)
        if self.row_note:
            muted = self._theme_colour("text-muted")
            note_style = "italic" if self.is_active or not muted else f"italic {muted}"
            text.append("   " + self.row_note, style=note_style)
        self.update(text)

    def on_mount(self) -> None:
        # The theme is only reachable once the row is on screen, and the choice
        # is made on the home screen, so every row here is built after it: this
        # is where its colours are read (U1).
        self._refresh_content()

    def set_active(self, active: bool) -> None:
        if active != self.is_active:
            self.is_active = active
            self._refresh_content()
        self.set_class(active, "active")

    def set_label(self, label: str) -> None:
        """Change what the row says without rebuilding the list (F2).

        A checklist ticks a row in place. Replacing the whole list would jump
        the cursor back to the top on every tick, which makes ticking the fifth
        of nine videos an exercise in scrolling.
        """
        if label != self.row_label:
            self.row_label = label
            self._refresh_content()

    def on_click(self, event: Click) -> None:
        event.stop()
        self.post_message(self.Chosen(self.row_index))


class ExtraToggle(Static, can_focus=True):
    """A one-line switch for an extra: ``[ ] Subtitles`` (F5).

    A button is three rows tall with its border, and three of them would have
    pushed the qualities screen past the height of an ordinary terminal. This
    is a row of chips instead: one line, clickable, reachable with Tab, and with
    the tick written into the label so colour is not the only thing saying it.
    """

    BINDINGS: ClassVar[List[BindingType]] = [
        ("enter", "flip", "Toggle"),
        ("space", "flip", "Toggle"),
    ]

    class Pressed(Message):
        """Posted when the switch is clicked or pressed with the keyboard."""

        def __init__(self, key: str) -> None:
            super().__init__()
            self.key = key

    def __init__(
        self, key: str, label: str, tooltip: str = "", **kwargs: Any
    ) -> None:
        # ``tooltip`` is taken here rather than passed on: not every Textual
        # release accepts it in ``Static.__init__``, though every one has the
        # attribute.
        super().__init__(**kwargs)
        self.extra_key = key
        self.extra_label = label
        self.is_on = False
        self._refresh_content()
        if tooltip:
            self.tooltip = tooltip

    @property
    def plain(self) -> str:
        return f"[{'x' if self.is_on else ' '}] {self.extra_label}"

    def _refresh_content(self) -> None:
        # A Text, not a markup string: "[ ]" is a tag to Rich's markup parser,
        # which would draw the tick as nothing at all.
        self.update(Text(self.plain, style="bold" if self.is_on else ""))

    def set_on(self, on: bool) -> None:
        if on != self.is_on:
            self.is_on = on
            self._refresh_content()
        self.set_class(on, "on")

    def action_flip(self) -> None:
        self.post_message(self.Pressed(self.extra_key))

    def on_click(self, event: Click) -> None:
        event.stop()
        self.focus()
        self.post_message(self.Pressed(self.extra_key))


class ThemeRow(Static, can_focus=True):
    """The one settings row the app has: which colours it draws in (U1).

    A setting that only exists as a command-line flag is a setting the person
    who needs it will never find, and the light theme is exactly the one
    somebody wants *because* the screen they are looking at is hard to read.
    So it is a row: one line, in words rather than a colour swatch, clickable
    and reachable with the keyboard, and it says which scheme is in force.

    The label is the screen's to write — it knows about ``NO_COLOR`` and this
    widget does not — which is why the row carries no text of its own.
    """

    BINDINGS: ClassVar[List[BindingType]] = [
        ("enter", "turn", "Change theme"),
        ("space", "turn", "Change theme"),
    ]

    class Turned(Message):
        """Posted when the row is clicked or pressed with the keyboard."""

    def action_turn(self) -> None:
        self.post_message(self.Turned())

    def on_click(self, event: Click) -> None:
        event.stop()
        self.focus()
        self.post_message(self.Turned())


class ChoiceList(VerticalScroll):
    """A vertical list of :class:`ChoiceRow` widgets."""

    can_focus = True

    BINDINGS: ClassVar[List[BindingType]] = [
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

    def set_label(self, index: int, label: str) -> None:
        """Rename the row at ``index``, keeping the cursor where it is."""
        rows = self._rows()
        if 0 <= index < len(rows):
            rows[index].set_label(label)

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
