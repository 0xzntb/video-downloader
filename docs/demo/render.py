#!/usr/bin/env python3
"""Draw the README's GIF and banner from the recording and the app's own icon.

Two jobs, because they share everything that matters: the colours, which are
read from :mod:`viddl.theme` rather than copied, and the font, which is the
monospace one matplotlib already ships.

    python docs/demo/render.py gif       # docs/demo/viddl.gif
    python docs/demo/render.py banner    # docs/banner.png

The GIF is not a screen recording of a screen recording: the ``.cast`` file is
replayed through a terminal emulator (``pyte``), so what is drawn is what the
terminal would have shown, colours included. Frames are only emitted when the
screen actually changes, and each one is held for as long as it was on screen,
so a nine-second demo is about ninety frames rather than five hundred.

Needs ``pillow``, ``pyte`` and ``matplotlib`` (for its fonts) — build tools,
not runtime dependencies of the app.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import pyte
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
CAST = HERE / "viddl.cast"
GIF = HERE / "viddl.gif"
BANNER = REPO / "docs" / "banner.png"
ICON = REPO / "viddl" / "browser_extension" / "icons" / "icon128.png"

#: One character cell, in pixels. Big enough to read on a phone, small enough
#: that 88 columns still fit a README column without scrolling.
FONT_SIZE = 15
LINE_HEIGHT = 21

#: The shortest and longest a frame may be shown, in milliseconds. Short gaps
#: are merged into the frame before them; the last frame is held so the end of
#: the demo can be read. The floor is well above a single repaint: Textual draws
#: a screen in a few chunks, so a state that lasts 60ms is one half-drawn
#: screen, and showing it would look like a flicker rather than a change.
MIN_FRAME_MS = 120
LAST_FRAME_MS = 1800


def palette():
    """The dark scheme, exactly as the app draws it (U1)."""
    sys.path.insert(0, str(REPO))
    from viddl.theme import PALETTES, DARK  # noqa: E402 - after the path is set

    return PALETTES[DARK]


def font_path(bold: bool = False) -> str:
    import matplotlib

    root = Path(matplotlib.__file__).parent / "mpl-data" / "fonts" / "ttf"
    name = "DejaVuSansMono-Bold.ttf" if bold else "DejaVuSansMono.ttf"
    return str(root / name)


def colour(value: str, fallback: str) -> str:
    """pyte's colour name as something Pillow understands."""
    if not value or value == "default":
        return fallback
    if len(value) == 6:
        return "#" + value
    named = {
        "black": "#000000",
        "red": "#cc5555",
        "green": "#55cc88",
        "brown": "#ccaa55",
        "blue": "#5588cc",
        "magenta": "#cc55cc",
        "cyan": "#55cccc",
        "white": "#dddddd",
    }
    if value in named:
        return named[value]
    if value.startswith("bright"):
        return named.get(value[6:], fallback)
    return fallback


def snapshot(screen) -> List[List[Tuple[str, str, str, bool, bool]]]:
    """The screen as it looks right now, copied.

    The terminal object is one mutable screen: keeping it and looking at it
    later would give every frame the last screen the demo showed.
    """
    # Addressed by column rather than iterated: pyte's buffer only holds the
    # cells that have been written, so a blank row would come out empty.
    return [
        [
            (cell.data, cell.fg, cell.bg, bool(cell.bold), bool(cell.reverse))
            for cell in (screen.buffer[row][column] for column in range(screen.columns))
        ]
        for row in range(screen.lines)
    ]


def _from_the_first_key(states: List[Tuple[float, List]]) -> List[Tuple[float, List]]:
    """The states to draw: from the screen the first key was typed into."""
    if not states:
        return states
    before = _first_input_time()
    start = 0
    for index, (moment, _state) in enumerate(states):
        if moment <= before:
            start = index
    return states[start:]


def _first_input_time() -> float:
    for line in CAST.read_text(encoding="utf-8").splitlines():
        try:
            moment, kind, _data = json.loads(line)
        except ValueError:
            continue
        if kind == "i":
            return float(moment)
    return 0.0


#: How long a screen has to stay unchanged to count as one somebody saw, in
#: seconds. Textual repaints in several chunks, so a snapshot taken after any
#: single chunk catches half a screen — a list with no border, a box with no
#: text. Waiting for the output to go quiet is what a person's eyes do.
QUIET_SECONDS = 0.08


def replay() -> Tuple[Dict, List[Tuple[float, List]]]:
    """Feed the cast through a terminal, and keep the screens that settled.

    Each kept state is paired with the moment it stopped being the current
    screen, since that is how long it was on display.
    """
    lines = [json.loads(line) for line in CAST.read_text(encoding="utf-8").splitlines()]
    header, events = lines[0], lines[1:]
    screen = pyte.Screen(header["width"], header["height"])
    stream = pyte.Stream(screen)
    states: List[Tuple[float, List]] = []
    last = None
    for index, (moment, kind, data) in enumerate(events):
        if kind != "o":
            # Input events are what a player draws in its own bar. Feeding them
            # to the terminal would print the typed link on whatever row the
            # cursor happened to sit on, and the screen would show it twice.
            continue
        stream.feed(data)
        following = events[index + 1][0] if index + 1 < len(events) else moment + 1.0
        if following - moment < QUIET_SECONDS:
            continue
        state = snapshot(screen)
        if state == last:
            continue
        last = state
        states.append((float(following), state))
    return header, states


def draw_screen(state: List, fonts) -> Image.Image:
    """One terminal screen as a picture, drawn a run of cells at a time."""
    scheme = palette()
    columns = max(len(row) for row in state)
    width = columns * fonts["advance"]
    height = len(state) * LINE_HEIGHT
    image = Image.new("RGB", (int(width), int(height)), scheme.background)
    brush = ImageDraw.Draw(image)
    for row_index, cells in enumerate(state):
        column = 0
        while column < len(cells):
            char, fg, bg, bold, reverse = cells[column]
            foreground = colour(fg, scheme.text)
            background = colour(bg, scheme.background)
            if reverse:
                foreground, background = background, foreground
            text = char or " "
            start = column
            column += 1
            # Gather the run that shares this look, so a line of one colour is
            # one triangle rather than eighty.
            while column < len(cells):
                other_char, other_fg_raw, other_bg_raw, other_bold, other_reverse = cells[column]
                other_fg = colour(other_fg_raw, scheme.text)
                other_bg = colour(other_bg_raw, scheme.background)
                if other_reverse:
                    other_fg, other_bg = other_bg, other_fg
                if (other_fg, other_bg, other_bold) != (foreground, background, bold):
                    break
                text += other_char or " "
                column += 1
            left = int(round(start * fonts["advance"]))
            right = int(round(column * fonts["advance"]))
            top = row_index * LINE_HEIGHT
            if background.lower() != scheme.background.lower():
                brush.rectangle([left, top, right - 1, top + LINE_HEIGHT - 1], fill=background)
            face = fonts["bold"] if bold else fonts["regular"]
            brush.text((left, top + fonts["offset"]), text, font=face, fill=foreground)
    return image


def make_gif() -> int:
    header, states = replay()
    regular = ImageFont.truetype(font_path(), FONT_SIZE)
    bold = ImageFont.truetype(font_path(bold=True), FONT_SIZE)
    ascent, _descent = regular.getmetrics()
    fonts = {
        "regular": regular,
        "bold": bold,
        "advance": regular.getlength("M"),
        "offset": (LINE_HEIGHT - ascent) // 2,
    }
    frames: List[Image.Image] = []
    durations: List[int] = []
    moments: List[float] = []

    # The first frame is the one GitHub shows before anybody presses play, and
    # the app paints itself in stages: a frame taken mid-startup is a box with
    # no border and a link box with no text. So the demo starts at the last
    # screen before the first key was pressed — the home screen, drawn whole.
    states = _from_the_first_key(states)
    for moment, state in states:
        frames.append(
            draw_screen(state, fonts).convert("P", palette=Image.ADAPTIVE, colors=32)
        )
        moments.append(moment)

    for index in range(len(moments)):
        end = moments[index + 1] if index + 1 < len(moments) else moments[index] + 0.9
        durations.append(max(0, int(round((end - moments[index]) * 1000))))
    # A frame nobody could see — a repaint caught halfway — is dropped, and the
    # time it would have had goes to the frame before it, which is a screen
    # somebody could actually read. Judged on the frame's own duration: two
    # consecutive 70ms states are two half-drawn screens, not one shown for
    # 140ms.
    merged_frames: List[Image.Image] = []
    merged_durations: List[int] = []
    for frame, duration in zip(frames, durations):
        if merged_durations and duration < MIN_FRAME_MS:
            merged_durations[-1] += duration
            continue
        merged_frames.append(frame)
        merged_durations.append(duration)
    merged_durations[-1] = LAST_FRAME_MS

    merged_frames[0].save(
        GIF,
        save_all=True,
        append_images=merged_frames[1:],
        duration=merged_durations,
        loop=0,
        optimize=True,
    )
    print(
        f"wrote {GIF.relative_to(REPO)}: {len(merged_frames)} frames, "
        f"{GIF.stat().st_size // 1024} KB, "
        f"{sum(merged_durations) / 1000:.1f}s at {header['width']}x{header['height']}"
    )
    return 0


def make_banner() -> int:
    """The icon, the name and one sentence, in the app's own dark colours."""
    scheme = palette()
    width, height = 1280, 300
    image = Image.new("RGB", (width, height), scheme.background)
    brush = ImageDraw.Draw(image)
    brush.rounded_rectangle(
        [0, 0, width - 1, height - 1], radius=24, outline=scheme.panel, width=2
    )
    icon = Image.open(ICON).convert("RGBA").resize((168, 168), Image.LANCZOS)
    image.paste(icon, (76, 66), icon)
    title_font = ImageFont.truetype(font_path(bold=True), 96)
    tagline = "A video or an MP3, from a link you paste into a terminal."
    left = 296
    # Sized to the room that is left rather than guessed: the last two attempts
    # were 30 and 26, and both ran off the right edge of the picture.
    tag_font = None
    for size in range(32, 15, -1):
        candidate = ImageFont.truetype(font_path(), size)
        if candidate.getlength(tagline) <= width - left - 40:
            tag_font = candidate
            break
    if tag_font is None:  # pragma: no cover - 16 is under half the room
        tag_font = ImageFont.truetype(font_path(), 16)
    brush.text((left, 86), "Viddl", font=title_font, fill=scheme.text)
    brush.rectangle([left + 4, 196, left + 100, 200], fill=scheme.accent)
    brush.text((left, 216), tagline, font=tag_font, fill=scheme.muted)
    image.save(BANNER, optimize=True)
    print(f"wrote {BANNER.relative_to(REPO)}: {width}x{height}, "
          f"{BANNER.stat().st_size // 1024} KB")
    return 0


def main(argv: List[str]) -> int:
    what = (argv[0] if argv else "gif").lower()
    if what == "gif":
        return make_gif()
    if what == "banner":
        return make_banner()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
