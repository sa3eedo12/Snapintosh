# SPDX-License-Identifier: MIT
"""Decodes screenshots of the probe's display back into text.

This is an independent re-implementation of the page geometry in
probes/uefi/fbtext.c, so a layout bug in the C code shows up as a decode
failure here instead of being agreed with. Glyph shapes are not re-typed:
they come from `tools/test-uefi-probe-logic --dump-font`, which renders each
character through the probe's own drawing routine.

Geometry (scale is an integer 3..12, one font cell is 6x8 pixels):
  margin = 6*scale, column width = 42 cells, column gap = 2 cells,
  row pitch = 9*scale; rows 0-1 are inverse-video headers, the last row is
  the footer, rows in between are body lines.
"""
from __future__ import annotations

import re
import struct
import zlib
from dataclasses import dataclass, field

import lines

CELL_W = 6
CELL_H = 8
LINE_COLS = 42
MIN_SCALE = 3
MAX_SCALE = 12
MAX_COLS = 3
HEADER_ROWS = 2


class ScreenError(Exception):
    pass


@dataclass
class Image:
    width: int
    height: int
    rgb: bytes

    def lit(self, x: int, y: int) -> bool:
        return self.rgb[(y * self.width + x) * 3 + 1] > 127


def parse_ppm(data: bytes) -> Image:
    """Parses the binary PPM (P6) that QEMU's screendump command writes."""
    pos = 0
    tokens = []
    while len(tokens) < 4:
        while pos < len(data) and data[pos:pos + 1].isspace():
            pos += 1
        if pos < len(data) and data[pos:pos + 1] == b"#":
            while pos < len(data) and data[pos:pos + 1] != b"\n":
                pos += 1
            continue
        start = pos
        while pos < len(data) and not data[pos:pos + 1].isspace():
            pos += 1
        if start == pos:
            raise ScreenError("truncated PPM header")
        tokens.append(data[start:pos])
    pos += 1  # the single whitespace byte after maxval
    if tokens[0] != b"P6" or tokens[3] != b"255":
        raise ScreenError("not an 8-bit binary PPM")
    width, height = int(tokens[1]), int(tokens[2])
    if width <= 0 or height <= 0 or width > 16384 or height > 16384:
        raise ScreenError(f"implausible size {width}x{height}")
    if len(data) - pos != width * height * 3:
        raise ScreenError("PPM payload size does not match its header")
    return Image(width, height, data[pos:])


def load_font(dump: str) -> dict:
    """Builds {8-row bitmap tuple: character} from the logic test's font dump.

    Each dump line is `G <hex code> <8 rows of 6 '#'/'.' characters joined by '/'>`.
    """
    font = {}
    for raw in dump.splitlines():
        m = re.fullmatch(r"G ([0-9A-F]{2}) ((?:[.#]{6}/){7}[.#]{6})", raw.strip())
        if not m:
            continue
        bitmap = tuple(m.group(2).split("/"))
        char = chr(int(m.group(1), 16))
        if bitmap in font and font[bitmap] != char:
            raise ScreenError(f"glyphs {font[bitmap]!r} and {char!r} are identical")
        font[bitmap] = char
    if len(font) < 60:
        raise ScreenError(f"font dump has only {len(font)} distinct glyphs")
    return font


@dataclass
class Row:
    col: int
    row: int
    inverse: bool
    text: str
    check: lines.LineCheck
    unknown_cells: int


@dataclass
class Screen:
    ok: bool
    reason: str = ""
    width: int = 0
    height: int = 0
    scale: int = 0
    cols: int = 0
    total_rows: int = 0
    rows: list = field(default_factory=list)

    @property
    def headers(self):
        return [r for r in self.rows if r.inverse]

    @property
    def body(self):
        return [r for r in self.rows
                if not r.inverse and HEADER_ROWS <= r.row < self.total_rows - 1]

    @property
    def footer(self):
        for r in self.rows:
            if r.row == self.total_rows - 1 and not r.inverse and r.col == 0:
                return r
        return None

    def bad_rows(self):
        return [r for r in self.rows if not r.check.ok or r.unknown_cells]

    def payloads(self):
        return [r.check.payload for r in self.rows]


def _scale_of(img: Image):
    """Finds the scale from the first header bar.

    The first row with a lit pixel is the top of that bar, y = margin = 6*scale
    whatever its first glyph looks like, and the strip drawn under the glyph
    cells (fbtext.c draw_line) is lit from x = margin on its full width.
    """
    for y in range(img.height):
        base = y * img.width * 3
        if max(img.rgb[base + 1:base + img.width * 3:3]) <= 127:
            continue
        if y % CELL_W or not MIN_SCALE <= y // CELL_W <= MAX_SCALE:
            return None
        scale = y // CELL_W
        strip = y + CELL_H * scale
        if strip >= img.height:
            return None
        row = img.rgb[strip * img.width * 3 + 1:(strip + 1) * img.width * 3:3]
        x = next((i for i, v in enumerate(row) if v > 127), None)
        return scale if x == y else None
    return None


def decode(img: Image, font: dict) -> Screen:
    scale = _scale_of(img)
    if scale is None:
        return Screen(False, "no header bar at the expected position")
    margin = CELL_W * scale
    col_w = LINE_COLS * CELL_W * scale
    col_gap = 2 * CELL_W * scale
    pitch = (CELL_H + 1) * scale
    if img.width < 2 * margin + col_w:
        return Screen(False, "screen narrower than one column")
    cols = min(MAX_COLS, (img.width - 2 * margin + col_gap) // (col_w + col_gap))
    total_rows = (img.height - 2 * margin) // pitch
    screen = Screen(True, "", img.width, img.height, scale, cols, total_rows)

    half = scale // 2
    for col in range(cols):
        x0 = margin + col * (col_w + col_gap)
        for row in range(total_rows):
            y0 = margin + row * pitch
            # The last cell of a line is a CRC hex digit, and no digit glyph draws in
            # pixel column 5 or pixel row 7, so that pixel is the row's background.
            bg = img.lit(x0 + (LINE_COLS - 1) * CELL_W * scale + 5 * scale + half,
                         y0 + 7 * scale + half)
            chars = []
            unknown = 0
            any_fg = False
            for cell in range(LINE_COLS):
                cx = x0 + cell * CELL_W * scale
                bitmap = tuple(
                    "".join("#" if img.lit(cx + px * scale + half, y0 + py * scale + half) != bg
                            else "." for px in range(CELL_W))
                    for py in range(CELL_H))
                if bitmap != (("......",) * CELL_H):
                    any_fg = True
                ch = font.get(bitmap)
                if ch is None:
                    unknown += 1
                    ch = "\ufffd"
                chars.append(ch)
            if not any_fg:
                continue
            text = "".join(chars)
            screen.rows.append(Row(col, row, bg, text, lines.check_line(text), unknown))
    return screen


def write_png(img: Image, path: str) -> None:
    """Writes a PNG for human inspection of a failed run."""
    raw = bytearray()
    stride = img.width * 3
    for y in range(img.height):
        raw.append(0)
        raw += img.rgb[y * stride:(y + 1) * stride]

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", img.width, img.height, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)
