#!/usr/bin/env python3
"""Generate the extension's PNG icons.

Pure standard library on purpose: contributors should not need Pillow just to
rebuild three small images. Run it from the project root:

    python3 tools/make_icons.py
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "viddl" / "browser_extension" / "icons"
SIZES = (16, 48, 128)
SUPERSAMPLE = 4

TOP_LEFT = (0x8A, 0x5C, 0xF6)
BOTTOM_RIGHT = (0xFF, 0x2D, 0x78)
WHITE = (0xFF, 0xFF, 0xFF)


def _rounded(x: float, y: float, left: float, right: float, top: float, bottom: float, radius: float) -> bool:
    if not (left <= x <= right and top <= y <= bottom):
        return False
    for cx, cy in (
        (left + radius, top + radius),
        (right - radius, top + radius),
        (left + radius, bottom - radius),
        (right - radius, bottom - radius),
    ):
        inner = (left + radius <= x <= right - radius) or (top + radius <= y <= bottom - radius)
        if not inner and (x - cx) ** 2 + (y - cy) ** 2 > radius**2:
            return False
    return True


def _arrow(x: float, y: float) -> bool:
    """White download arrow: a stem, a head and a tray line."""
    if abs(x - 0.5) <= 0.075 and 0.22 <= y <= 0.52:
        return True
    if 0.46 <= y <= 0.66:
        progress = (y - 0.46) / 0.20
        if abs(x - 0.5) <= 0.22 * (1 - progress):
            return True
    if 0.27 <= x <= 0.73 and 0.76 <= y <= 0.86:
        return True
    return False


def _pixel(x: float, y: float):
    """Colour for one (supersampled) sample point in 0..1 space."""
    if _arrow(x, y):
        return WHITE + (255,)
    if _rounded(x, y, 0.0, 1.0, 0.0, 1.0, 0.24):
        blend = min(1.0, max(0.0, (x + y) / 2))
        colour = tuple(
            int(TOP_LEFT[i] + (BOTTOM_RIGHT[i] - TOP_LEFT[i]) * blend) for i in range(3)
        )
        return colour + (255,)
    return (0, 0, 0, 0)


def render(size: int) -> bytes:
    rows = []
    samples = SUPERSAMPLE * SUPERSAMPLE
    for py in range(size):
        row = bytearray()
        for px in range(size):
            r = g = b = a = 0
            for sy in range(SUPERSAMPLE):
                for sx in range(SUPERSAMPLE):
                    fx = (px + (sx + 0.5) / SUPERSAMPLE) / size
                    fy = (py + (sy + 0.5) / SUPERSAMPLE) / size
                    pr, pg, pb, pa = _pixel(fx, fy)
                    r += pr * pa
                    g += pg * pa
                    b += pb * pa
                    a += pa
            if a == 0:
                row += bytes((0, 0, 0, 0))
            else:
                row += bytes((r // a, g // a, b // a, a // samples))
        rows.append(bytes(row))
    return b"".join(b"\x00" + row for row in rows)


def write_png(path: Path, size: int, raw: bytes) -> None:
    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for size in SIZES:
        target = OUT_DIR / f"icon{size}.png"
        write_png(target, size, render(size))
        print(f"wrote {target.relative_to(OUT_DIR.parent.parent.parent)}")


if __name__ == "__main__":
    main()
