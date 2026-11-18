#!/usr/bin/env python3
"""Draw the scan-skills pixel-art binoculars logo (stdlib only, no network).

Writes, next to this file:
  logo.svg          36x36 pixel grid on a navy tile (scales crisply)
  logo.png          512x512 on the navy tile
  logo-mark.png     512x512, transparent background
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import Dict, Optional, Tuple

HERE = Path(__file__).resolve().parent
SIZE = 36  # grid; binoculars drawn in a 32x32 box offset by OFF
OFF = 2

PALETTE = {
    "K": "#070B22",  # outline
    "D": "#1A2C78",  # body shadow
    "B": "#2A55D0",  # body
    "L": "#4C8BFF",  # body light
    "H": "#A8CCFF",  # specular
    "t": "#04454F",  # lens depth
    "T": "#00D9C3",  # lens teal
    "U": "#5CFFE4",  # lens light
    "N": "#3DFF8F",  # neon green
    "W": "#F2FFFB",  # glint
    "bg": "#131D4D",
    "dot": "#22337A",
    "edge": "#00B8A6",
}

Grid = Dict[Tuple[int, int], str]


def put(g: Grid, x: int, y: int, c: str) -> None:
    if 0 <= x < 32 and 0 <= y < 32:
        g[(x, y)] = c


def rect(g: Grid, x0: int, y0: int, x1: int, y1: int, c: str) -> None:
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            put(g, x, y, c)


def barrel(g: Grid, cx: int) -> None:
    """One half: eyepiece, neck, barrel, grip, objective lens. cx = barrel centre column."""
    x0, x1 = cx - 5, cx + 5
    # eyepiece cup
    rect(g, cx - 3, 3, cx + 2, 6, "B")
    rect(g, cx - 3, 3, cx - 2, 6, "L")
    rect(g, cx + 2, 3, cx + 2, 6, "D")
    rect(g, cx - 3, 3, cx + 2, 3, "D")  # dark rim on top of the cup
    put(g, cx - 2, 4, "H")
    # neck
    rect(g, cx - 2, 7, cx + 1, 8, "D")
    put(g, cx - 2, 7, "B")
    # barrel body with light from the top-left
    rect(g, x0, 9, x1, 19, "B")
    rect(g, x0, 9, x0 + 1, 19, "L")
    rect(g, x0 + 2, 10, x0 + 2, 13, "H")
    rect(g, x1 - 1, 9, x1, 19, "D")
    rect(g, x0, 9, x1, 9, "L")
    rect(g, x1 - 1, 9, x1, 9, "B")
    # rubber grip band with ridges
    for x in range(x0, x1 + 1):
        put(g, x, 15, "D")
        put(g, x, 16, "K" if (x - x0) % 2 else "D")
        put(g, x, 17, "D")
    # objective housing + lens (circle drawn per pixel)
    ccx, ccy = cx - 0.5 + 0.5, 23.5
    for y in range(17, 31):
        for x in range(cx - 7, cx + 8):
            dx, dy = x - ccx, y - ccy
            r = (dx * dx + dy * dy) ** 0.5
            if r > 6.6:
                continue
            if r > 5.6:
                c = "L" if dx + dy < -3 else ("D" if dx + dy > 2 else "B")
            elif r > 4.7:
                c = "N"
            elif dx + dy > 2.2:
                c = "t"
            elif dx + dy < -3.2:
                c = "U"
            else:
                c = "T"
            put(g, x, y, c)
    # scan line across the lens
    for x in range(cx - 3, cx + 4):
        if g.get((x, 24)) in ("T", "t", "U"):
            g[(x, 24)] = "N" if g[(x, 24)] != "t" else "T"
    # glints
    put(g, cx - 2, 21, "W")
    put(g, cx - 3, 22, "W")
    put(g, cx - 1, 21, "U")


def bridge(g: Grid) -> None:
    # One wide hinge plate joining the barrels from top to the lenses, flush
    # with the barrel tops, so the middle reads as a solid body (no knob on
    # top and no long gap running down between the lenses).
    rect(g, 14, 9, 17, 20, "D")
    rect(g, 14, 9, 17, 9, "B")
    for y in (11, 14, 17):
        rect(g, 14, y, 17, y, "B")


def sparkle(g: Grid, x: int, y: int) -> None:
    put(g, x, y, "W")
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        put(g, x + dx, y + dy, "N")


def outline(g: Grid) -> Grid:
    out = dict(g)
    for y in range(-1, 33):
        for x in range(-1, 33):
            if (x, y) in g:
                continue
            if any((x + dx, y + dy) in g for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                out[(x, y)] = "K"
    return out


def draw() -> Grid:
    g: Grid = {}
    barrel(g, 8)
    barrel(g, 23)
    bridge(g)
    g = outline(g)
    sparkles: Grid = {}
    sparkle(sparkles, 29, 3)
    sparkle(sparkles, 2, 1)
    for k, v in sparkles.items():
        g.setdefault(k, v)
    return {(x + OFF, y + OFF): c for (x, y), c in g.items()}


def tile() -> Dict[Tuple[int, int], Optional[str]]:
    """Navy rounded tile with a faint dot grid (the 'map' the binoculars scan)."""
    t: Dict[Tuple[int, int], Optional[str]] = {}
    cut = {0: 3, 1: 1, 2: 1}
    for y in range(SIZE):
        for x in range(SIZE):
            edge = min(y, SIZE - 1 - y)
            c = cut.get(edge, 0)
            if x < c or x > SIZE - 1 - c:
                t[(x, y)] = None
                continue
            near = min(x, SIZE - 1 - x, y, SIZE - 1 - y)
            if near == 0 or (edge in cut and (x == c or x == SIZE - 1 - c)):
                t[(x, y)] = "edge"
            elif x % 4 == 2 and y % 4 == 2:
                t[(x, y)] = "dot"
            else:
                t[(x, y)] = "bg"
    return t


def rgba(hex_: Optional[str]) -> Tuple[int, int, int, int]:
    if hex_ is None:
        return (0, 0, 0, 0)
    h = hex_.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255)


def compose(with_tile: bool) -> Dict[Tuple[int, int], Optional[str]]:
    px: Dict[Tuple[int, int], Optional[str]] = tile() if with_tile else {}
    for k, c in draw().items():
        if with_tile and px.get(k) is None:
            continue  # keep the tile's rounded corners clean
        px[k] = c
    return px


def write_png(path: Path, px, scale: int) -> None:
    rows = []
    for y in range(SIZE):
        row = bytearray()
        for x in range(SIZE):
            key = px.get((x, y))
            row += bytes(rgba(PALETTE[key] if key else None)) * scale
        rows.append(b"\x00" + bytes(row))
    raw = b"".join(r for r in rows for _ in range(scale))
    w = h = SIZE * scale

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    path.write_bytes(png)


def write_svg(path: Path, px) -> None:
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SIZE} {SIZE}" '
             f'width="512" height="512" shape-rendering="crispEdges">',
             "<title>scan-skills</title>"]
    for y in range(SIZE):
        x = 0
        while x < SIZE:
            key = px.get((x, y))
            run = 1
            while x + run < SIZE and px.get((x + run, y)) == key:
                run += 1
            if key:
                parts.append(f'<rect x="{x}" y="{y}" width="{run}" height="1" fill="{PALETTE[key]}"/>')
            x += run
    parts.append("</svg>")
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")


if __name__ == "__main__":
    scale = 14  # 36 * 14 = 504px
    write_png(HERE / "logo.png", compose(True), scale)
    write_png(HERE / "logo-mark.png", compose(False), scale)
    write_svg(HERE / "logo.svg", compose(True))
    print("wrote logo.png, logo-mark.png, logo.svg")
