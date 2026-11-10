# SPDX-License-Identifier: MIT
"""Line format shared by the probe screen and its host-side checkers.

Every row on the probe's display is "<payload padded to 38 columns> |<CRC>",
where CRC is the CRC-8 (polynomial 0x07, initial value 0, no reflection) of the
payload as shown, as two upper-case hex digits. A digit misread from a
photograph therefore shows up as a CRC mismatch instead of a wrong fact.
"""
from __future__ import annotations

from dataclasses import dataclass

PAYLOAD_MAX = 38
LINE_COLS = 42


def crc8(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def normalise(payload: str) -> str:
    """Upper-case, trim and collapse runs of whitespace, as the probe does."""
    return " ".join(payload.upper().split())


@dataclass
class LineCheck:
    ok: bool
    payload: str
    found: int | None
    expected: int
    reason: str


def check_line(line: str) -> LineCheck:
    text = line.rstrip("\r\n")
    head, sep, tail = text.rpartition("|")
    payload = normalise(head if sep else text)
    expected = crc8(payload.encode("ascii", "replace"))
    if not sep:
        return LineCheck(False, payload, None, expected, "no '|' CRC separator")
    digits = tail.strip()
    # int(..., 16) would also accept signs, underscores and non-ASCII digits.
    if not digits or any(ch not in "0123456789abcdefABCDEF" for ch in digits):
        return LineCheck(False, payload, None, expected, f"CRC field {digits!r} is not hex")
    found = int(digits, 16)
    if len(digits) != 2:
        return LineCheck(False, payload, found, expected, "CRC field must be two hex digits")
    if found != expected:
        return LineCheck(False, payload, found, expected, "CRC mismatch")
    return LineCheck(True, payload, found, expected, "")
