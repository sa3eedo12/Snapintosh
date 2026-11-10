#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Turns the NV2 record rows of a photographed X2 test page back into the
record text that tools/nv2_record.py reads.

    python3 probes/uefi/tests/host/nv2screen.py rows.txt > record.txt
    python3 tools/nv2_record.py ingest record.txt ...

rows.txt holds the "NV2R ..." rows exactly as read off the screen (or as the
screen decoder probes/uefi/host/screen.py prints them), one per line; the
per-row " | hh" check the renderer adds, if present, is ignored. The screen
cannot show lower case and shows a line longer than 38 characters on two rows
(the first 32 characters, then "+" and the rest), so this tool undoes both: it
joins the continuation rows, lower-cases everything except the fields whose
values are upper case in the record (EVIDENCE and TOOL), and then verifies
each line's own "*hh" check against tools/nv2_record.py's CRC-8. A line whose
check does not match is refused, never repaired: retype that row.
"""
from __future__ import annotations

import argparse
import itertools
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "tools"))

import nv2_record  # noqa: E402

UPPER_VALUE_KEYS = ("evidence", "tool")


class ScreenError(Exception):
    pass


def join_rows(rows):
    """Logical lines: a row that starts with '+' continues the row before it."""
    lines = []
    for raw in rows:
        row = raw.strip()
        if not row:
            continue
        if " |" in row:
            row = row.split(" |")[0].rstrip()
        if row.startswith("+"):
            if not lines:
                raise ScreenError("a continuation row with nothing before it")
            lines[-1] += row[1:]
        else:
            lines.append(row)
    return lines


def _lowered(line, tool_case=None):
    """The canonical spelling of one upper-case screen line (before its check)."""
    star = line.rfind("*")
    if star < 0:
        raise ScreenError(f"no '*hh' check in {line!r}")
    body, check = line[:star], line[star + 1:].lower()
    prefix, _, rest = body.partition(" ")
    out = prefix + " "
    key, eq, value = rest.partition("=")
    if eq and key.lower() in UPPER_VALUE_KEYS:
        if key.lower() == "tool" and tool_case is not None:
            value = "".join(c.lower() if bool(m) else c for c, m in zip(value, tool_case))
        out += key.lower() + "=" + value
    else:
        out += rest.lower()
    return out + "*" + check


def _check_ok(line):
    star = line.rfind("*")
    return nv2_record.crc8(line[:star].encode("ascii")) == int(line[star + 1:], 16)


def restore_line(line):
    """The record line behind one screen line, or ScreenError if no spelling matches its check."""
    candidate = _lowered(line)
    if _check_ok(candidate):
        return candidate
    key = line.split(" ", 1)[1].partition("=")[0].lower() if " " in line else ""
    if key == "tool":
        value = line[line.find("=") + 1:line.rfind("*")]
        letters = [i for i, c in enumerate(value) if c.isalpha()]
        if len(letters) <= 12:
            for mask in itertools.product((False, True), repeat=len(letters)):
                case = [False] * len(value)
                for idx, lower in zip(letters, mask):
                    case[idx] = lower
                candidate = _lowered(line, case)
                if _check_ok(candidate):
                    return candidate
    raise ScreenError(f"the check digits do not match {line!r}: retype this row")


def rows_to_text(rows):
    lines = [restore_line(line) for line in join_rows(rows) if line.upper().startswith("NV2R")]
    if not lines:
        raise ScreenError("no NV2R rows found")
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("rows", type=Path, help="text file with the NV2R rows, one per line")
    args = ap.parse_args(argv)
    try:
        sys.stdout.write(rows_to_text(args.rows.read_text(encoding="utf-8").splitlines()))
    except (ScreenError, OSError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
