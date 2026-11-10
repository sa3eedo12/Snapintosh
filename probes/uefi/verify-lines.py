#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Check the CRC-8 on lines transcribed from a photograph of the probe screen.

Type or paste the screen text, one row per line, exactly as shown including the
trailing "|XX" field. Case and spacing do not matter. Each row is reported as
OK or BAD; a BAD row has a misread character or a wrong CRC and should be read
again from the photograph before any fact on it is trusted.

  python3 probes/uefi/verify-lines.py transcription.txt
  python3 probes/uefi/verify-lines.py < transcription.txt

Exit status: 0 if every row checks out, 1 if any row is BAD, 2 if there were
no rows at all, the file could not be read or the arguments were wrong.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "host"))
from lines import check_line  # noqa: E402


def main(argv) -> int:
    # A typed transcription can hold any byte and the terminal need not be UTF-8:
    # a misread must be reported as a BAD row, not end in a traceback.
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    if len(argv) > 1 and argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0
    if len(argv) > 2:
        print("verify-lines: at most one file argument is accepted", file=sys.stderr)
        return 2
    if len(argv) == 2:
        try:
            source = open(argv[1], encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"verify-lines: cannot read {argv[1]!r}: {exc.strerror}", file=sys.stderr)
            return 2
    else:
        source = sys.stdin
    good = bad = 0
    with source:
        for number, raw in enumerate(source, 1):
            if not raw.strip():
                continue
            result = check_line(raw)
            if result.ok:
                good += 1
                print(f"OK   {number:4d}  {result.payload}")
            else:
                bad += 1
                found = "--" if result.found is None else f"{result.found:02X}"
                print(f"BAD  {number:4d}  {result.payload}  "
                      f"[{result.reason}; shown {found}, expected {result.expected:02X}]")
    print(f"{good} OK, {bad} BAD")
    if good + bad == 0:
        return 2
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
