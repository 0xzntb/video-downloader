"""What the window title says while a download runs (U2).

A download is the one thing Viddl does that takes minutes, and it does it in a
window people have usually moved away from. The screen already says where it
is — a bar, a percentage, the speed, the time left — but the *title bar* said
"Viddl" from start to finish, so the task bar, the tab strip and the
window switcher (the places somebody looks to find a long job again) knew
nothing at all.

So the title carries the two things worth a glance: how far along it is, and
what it is::

    Viddl — 42% — Killing Time.mp4
    Viddl — 42% — 3 of 9 — Killing Time.mp4      a playlist or a batch of links

The percentage comes *before* the name on purpose: a task bar or a narrow title
bar truncates the tail, and the number is the part that has to survive that.
The rest of the time the title is the app's name again.

**One escape sequence.** ``ESC ] 0 ; … BEL`` (OSC 0) is what every terminal
that has a title understands, and Textual does not send it — ``App.title`` is
the in-app header, which this app never draws. The sequence goes out through
the app's driver, the way Textual's own clipboard write does, so nothing is
written when the app is running without a terminal.

**The name comes from the web, and a title is an escape sequence.** A video's
name was chosen by a site, and an ESC or a BEL inside one would end the
sequence early and start something else in the terminal. :func:`clean` therefore
drops control characters — and the bidirectional overrides, which would let a
title render ``exe.png`` for a file called ``gnp.exe`` — collapses whitespace
and caps the length, so the sequence this module writes cannot be ended early
or reordered by a name a site chose.

**Putting it back.** xterm's title stack (``CSI 22;0t`` / ``CSI 23;0t``) is
pushed when the app starts and popped when it exits, which brings the shell's
own title back. A terminal that ignores the stack would otherwise be left
holding the last thing Viddl said, so the app's name is written *before* the
pop: a terminal that honours neither is left with "Viddl", never "42%".

**Only when it changes.** yt-dlp reports several times a second, and many
terminals redraw their title bar for every write. The title is composed from
whole percentages only and sent when the text differs, which is at most about a
hundred writes per download rather than thousands.

**What it deliberately does not touch.** ``--plain`` prints for scripts and is
often piped into one, so it sets no title; the ``--web`` page has a browser tab
title, which is that page's business; and suspending the app with ``Ctrl+Z``
leaves the last title in place until the next progress update or the exit.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Callable, Optional

from .. import APP_TITLE

#: The sequence that names the window. ``ESC ] 0 ;`` is "set icon name and
#: title", which every terminal with a title bar reads; BEL ends it, which is
#: the terminator terminals actually parse (ST, ``ESC \``, is the standard's).
OSC = "\x1b]0;"
BEL = "\x07"

#: xterm's title stack. Ignored by a terminal that has no stack, which is why
#: the app's own name is written before the pop.
PUSH = "\x1b[22;0t"
POP = "\x1b[23;0t"

#: What separates the parts: an em dash, as in "Viddl — 42% — …".
SEPARATOR = " — "

#: How much of the video's name the title keeps. Long enough for a real title,
#: short enough that the percentage before it cannot be pushed out of sight.
NAME_LIMIT = 48

#: The whole title, so one very long name cannot fill a window switcher.
TITLE_LIMIT = 96

#: Characters that change the order text is drawn in rather than being text.
#: A name that reorders itself can show one file extension while being another,
#: and a title bar is far too short to notice the trick. Contrast with the
#: zero-width joiner, which is left alone because a family emoji needs it.
#
# They are spelled as code points rather than as characters because no such
# character belongs in a string literal in this project: everything Viddl says
# has to survive a cp1252 console (see ``plain.ascii_text``), and a literal here
# would be a claim that it does.
_SPOOF = frozenset(
    [chr(0x061C), chr(0x200E), chr(0x200F)]
    + [chr(code) for code in range(0x202A, 0x202F)]
    + [chr(code) for code in range(0x2066, 0x206A)]
)

#: Whitespace a name may carry that is really a line break. NEL and the two
#: Unicode line separators are here because they end a line on a terminal.
_BREAKS = "\n\r\t\v\f" + chr(0x85) + chr(0x2028) + chr(0x2029)


def clean(text: object, limit: int = NAME_LIMIT) -> str:
    """Text that is safe inside a title, or ``""`` when nothing is left.

    Control characters go: ESC and BEL could end the sequence early, and the
    rest draw nothing. A line break becomes a space so that ``a\\nb`` reads as
    two words rather than one. Whitespace is collapsed, the result is trimmed,
    and anything longer than ``limit`` is shortened with an ellipsis.
    """
    # NFC first: a decomposed accent is two code points for one character, and
    # the length check should count characters, not pieces.
    raw = unicodedata.normalize("NFC", str(text if text is not None else ""))
    kept = []
    for char in raw:
        if char in _SPOOF:
            continue
        if unicodedata.category(char) in ("Cc", "Cs"):
            # A break is a word boundary; everything else (ESC, DEL, the C1
            # block) is a character whose only job is to be obeyed.
            kept.append(" " if char in _BREAKS else "")
            continue
        kept.append(char)
    tidy = re.sub(r"\s+", " ", "".join(kept)).strip()
    if limit > 0 and len(tidy) > limit:
        tidy = tidy[: limit - 1].rstrip() + "…"
    return tidy


def percent_text(percent: Optional[float]) -> str:
    """``42%`` for a position on the bar, or ``""`` when there is no number.

    Rounded rather than floored, clamped to the bar's own 0–100, because a
    title reading "127%" would be a bug the person cannot do anything about.
    """
    if percent is None:
        return ""
    try:
        value = float(percent)
    except (TypeError, ValueError):
        return ""
    if value != value:  # NaN: a number that is not one
        return ""
    return f"{max(0, min(100, round(value)))}%"


def compose(
    percent: Optional[float] = None,
    name: str = "",
    position: Optional[int] = None,
    total: Optional[int] = None,
    app: str = APP_TITLE,
) -> str:
    """The title for this moment: ``Viddl — 42% — 3 of 9 — Killing Time.mp4``.

    Every part is optional, so the same function says "no percentage yet" (a
    live recording) and "nothing to name" (a link that has not been looked up)
    without a second spelling of the format.
    """
    parts = [clean(app, 24) or APP_TITLE]
    number = percent_text(percent)
    if number:
        parts.append(number)
    if position is not None and total:
        parts.append(f"{int(position)} of {int(total)}")
    title = clean(name, NAME_LIMIT)
    if title:
        parts.append(title)
    return clean(SEPARATOR.join(parts), TITLE_LIMIT)


def sequence(text: str) -> str:
    """The escape sequence that puts ``text`` on the title bar."""
    return f"{OSC}{clean(text, TITLE_LIMIT)}{BEL}"


class TerminalTitle:
    """The window title, and the stack that puts the previous one back.

    ``write`` is how the sequence reaches the terminal — the app passes its
    driver's writer, so a test can pass a list and watch what would have been
    sent. Every write is one whole sequence: the title is never assembled from
    pieces on the way out.
    """

    def __init__(self, write: Callable[[str], None], app: str = APP_TITLE) -> None:
        self.write = write
        self.app = app
        #: What the title bar is believed to say. A repeated value is not sent
        #: again, which is what keeps thousands of progress lines down to a
        #: hundred writes.
        self.current = ""
        #: True once the stack has been pushed, so a second mount cannot push
        #: twice and leave the shell's title buried under two entries.
        self.started = False
        #: True once the pop has been sent: closing twice must not pop a title
        #: the shell pushed for somebody else.
        self.restored = False

    # -- what the app calls -------------------------------------------------
    def start(self) -> None:
        """Take the title bar: remember the shell's, and put ours up."""
        if self.started:
            return
        self.started = True
        self._send(PUSH)
        # Forced, so the app's name is written even though the field starts
        # empty and the composed default is the same idea.
        self.current = ""
        self._set(compose(app=self.app))

    def show(
        self,
        percent: Optional[float] = None,
        name: str = "",
        position: Optional[int] = None,
        total: Optional[int] = None,
    ) -> None:
        """Say how far along a download is, and what it is."""
        self._set(
            compose(
                percent=percent,
                name=name,
                position=position,
                total=total,
                app=self.app,
            )
        )

    def reset(self) -> None:
        """Back to the app's own name: a download has ended or been left."""
        self._set(compose(app=self.app))

    def close(self) -> None:
        """Give the title bar back — after making sure it says something sane."""
        if not self.started or self.restored:
            return
        self.reset()
        self.restored = True
        self._send(POP)

    # -- the wire -----------------------------------------------------------
    def _set(self, text: str) -> None:
        # ``restored`` is checked here rather than in each caller: once the
        # title bar belongs to the shell again, a screen being torn down during
        # shutdown must not paint "Viddl" over the title it just got back.
        if self.restored or text == self.current:
            return
        self.current = text
        self._send(sequence(text))

    def _send(self, data: str) -> None:
        self.write(data)
