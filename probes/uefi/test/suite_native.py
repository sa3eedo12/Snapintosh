# SPDX-License-Identifier: MIT
"""NativeTests: the probe's portable C logic, built and run on the host.

tools/test-uefi-probe-logic.c is compiled exactly as its header comment says,
once plain and once under ASan+UBSan, and its output is judged by
native_output_problems(). The same binary renders probe screens to PPM
(--dump-screen) and prints the glyph table (--dump-font); the independent
Python decoder in probes/uefi/host/screen.py reads those back. VERIFIED for
that portable logic only: nothing here runs the EFI image.
"""
from __future__ import annotations

import re

import uefi_testkit as kit
import screen as screenlib
from uefi_testkit import (Base, ROOT, font_dump_text, font_table, native_command, native_exe,
                          native_output_problems, render_page, run_native, tail, text_of)

_GLYPH_LINE = re.compile(r"^G ([0-9A-F]{2}) ([.#]{6}(?:/[.#]{6}){7})$")


def with_pixel(img, x, y, rgb):
    buf = bytearray(img.rgb)
    buf[(y * img.width + x) * 3:(y * img.width + x) * 3 + 3] = bytes(rgb)
    return screenlib.Image(img.width, img.height, bytes(buf))


def cell_sample(scale, col, row, cell, px, py):
    """Pixel the decoder samples for glyph pixel (px, py) of one character cell."""
    margin = screenlib.CELL_W * scale
    column_pitch = (screenlib.LINE_COLS + 2) * screenlib.CELL_W * scale
    row_pitch = (screenlib.CELL_H + 1) * scale
    return (margin + col * column_pitch + (cell * screenlib.CELL_W + px) * scale + scale // 2,
            margin + row * row_pitch + py * scale + scale // 2)


class NativeTests(Base):
    def test_build_line_lists_every_test_unit(self):
        argv = native_command()
        listed = {a for a in argv if a.endswith(".c")}
        on_disk = {str(p.relative_to(ROOT)) for p in (ROOT / "probes/uefi/test").glob("test_*.c")}
        problems = [f"{name} is not in the build line of the C test's header"
                    for name in sorted(on_disk - listed)]
        problems += [f"{name} is on the build line but does not exist"
                     for name in sorted(listed) if not (ROOT / name).is_file()]
        if "tools/test-uefi-probe-logic.c" not in listed:
            problems.append("tools/test-uefi-probe-logic.c is not on its own build line")
        problems += [f"{flag} is missing from the build line"
                     for flag in ("-Wall", "-Wextra", "-Werror") if flag not in argv]
        self.check(problems)

    def test_logic_suite_passes(self):
        rc, out, err = run_native(native_exe("plain"))
        output = text_of(out)
        problems = native_output_problems(rc, output)
        if "sanitizers: none (plain build)" not in output:
            problems.append("the plain build did not say it has no sanitizers")
        self.check(problems, "--- stdout tail ---\n" + tail(output) + "\n--- stderr ---\n"
                   + tail(text_of(err), 5))

    def test_logic_suite_passes_under_asan_and_ubsan(self):
        rc, out, err = run_native(native_exe("asan"))
        output = text_of(out)
        problems = native_output_problems(rc, output)
        if "sanitizers: AddressSanitizer + UndefinedBehaviorSanitizer" not in output:
            problems.append("the sanitized build did not report that it is sanitized")
        self.check(problems, "--- stdout tail ---\n" + tail(output) + "\n--- stderr ---\n"
                   + tail(text_of(err), 30))

    def test_output_checker_rejects_bad_output(self):
        sections = kit.NATIVE_SECTIONS

        def synthetic(names=sections, each=50):
            rows = ["sanitizers: none (plain build)"]
            rows += [f"ok   {name:<26} {each} checks" for name in names]
            rows.append(f"PASS: {each * len(names)} checks, 0 failures")
            return "\n".join(rows) + "\n"

        good = synthetic()
        passing = f"PASS: {50 * len(sections)} checks, 0 failures"
        self.assertIn(passing, good, "the synthetic summary line changed; the failing-summary case would be a no-op")
        cases = [
            ("a clean run", 0, good, []),
            ("an extra section", 0, good.replace("PASS:", "ok   new section              20 checks\nPASS:"), []),
            ("a non-zero exit status", 1, good, ["exit status 1"]),
            ("a failing section", 0, good.replace("ok   crc8 ", "FAIL crc8"), ["failure line", "'crc8'"]),
            ("a C check failure", 0, good + "FAIL line 12 [crc8]: expected 1\n", ["failure line"]),
            ("a missing section", 0, synthetic(sections[:-1]), [f"'{sections[-1]}' did not report"]),
            ("a nearly empty section", 0, synthetic(each=3), ["ran only 3 checks"]),
            ("no summary line", 0, good.replace("PASS:", "DONE:"), ["no 'PASS"]),
            ("a failing summary", 0, good.replace(passing, passing.replace("PASS", "FAIL").replace("0 failures", "2 failures")),
             ["no 'PASS"]),
        ]
        problems = []
        for label, rc, text, wanted in cases:
            found = native_output_problems(rc, text)
            if not wanted and found:
                problems.append(f"{label}: false alarm {found}")
            for fragment in wanted:
                if not any(fragment in f for f in found):
                    problems.append(f"{label}: expected a problem mentioning {fragment!r}, got {found}")
        self.check(problems)

    def test_font_dump_is_complete_and_decodable(self):
        glyphs = {}
        for row in font_dump_text().splitlines():
            m = _GLYPH_LINE.match(row)
            if m:
                glyphs[int(m.group(1), 16)] = m.group(2).split("/")
        required = set(range(0x20, 0x60)) | {0x7C}
        problems = [f"no glyph for {chr(c)!r}" for c in sorted(required - set(glyphs))]
        if len({tuple(g) for g in glyphs.values()}) != len(glyphs):
            problems.append("two characters share one bitmap")
        # The decoder reads each row's background from pixel (5, 7) of its last cell, which
        # always holds a CRC hex digit.
        problems += [f"hex digit {d!r} draws in column 5 or row 7" for d in "0123456789ABCDEF"
                     if ord(d) in glyphs and (any(r[5] == "#" for r in glyphs[ord(d)])
                                              or "#" in glyphs[ord(d)][7])]
        try:
            if len(font_table()) != len(glyphs):
                problems.append("load_font did not keep every glyph")
        except screenlib.ScreenError as exc:
            problems.append(f"load_font refused the dump: {exc}")
        self.check(problems)

    GEOMETRY = (
        (2880, 1920, 85, 0),   # the SP11's expected native size (INFERRED): two columns
        (1366, 768, 85, 0),    # a small panel: one column, four pages
        (1024, 768, 85, 3),    # the last, partly filled page
        (2880, 1920, 400, 0),  # more lines than two columns hold: three columns, scale 3
        (2880, 1920, 38, 0),   # a layout narrower than the decoder's geometry allows
    )

    def test_decoder_reads_every_layout_the_c_code_draws(self):
        problems = []
        for width, height, nbody, page in self.GEOMETRY:
            tag = f"{width}x{height} {nbody} lines page {page}:"
            img, lay = render_page(width, height, nbody, page)
            sc = screenlib.decode(img, font_table())
            if not sc.ok:
                problems.append(f"{tag} the decoder refused the frame: {sc.reason}")
                continue
            if sc.scale != lay.scale:
                problems.append(f"{tag} decoded scale {sc.scale}, C drew scale {lay.scale}")
            if sc.cols < lay.cols:
                problems.append(f"{tag} decoder allows {sc.cols} columns, C drew {lay.cols}")
            if sc.total_rows != lay.total_rows or lay.body_rows != lay.total_rows - 3:
                problems.append(f"{tag} rows: decoder {sc.total_rows}, C {lay.total_rows} "
                                f"with {lay.body_rows} body rows")
            if [r.check.payload for r in sc.headers] != ["HEADER ZERO", "HEADER ONE"]:
                problems.append(f"{tag} header rows are {[r.check.payload for r in sc.headers]}")
            if sc.footer is None or sc.footer.check.payload != "FOOTER":
                problems.append(f"{tag} the footer row is missing or wrong")
            first = page * lay.cols * lay.body_rows
            want = max(0, min(nbody - first, lay.cols * lay.body_rows))
            if len(sc.body) != want:
                problems.append(f"{tag} {len(sc.body)} body rows decoded, {want} drawn")
            for r in sc.body:
                index = first + r.col * lay.body_rows + (r.row - 2)
                if r.check.payload != f"LINE {index:03d} 0123456789 ABCDEFGHIJ":
                    problems.append(f"{tag} row {r.row} column {r.col} reads {r.check.payload!r}")
                    break
            if sc.bad_rows():
                problems.append(f"{tag} {len(sc.bad_rows())} rows failed CRC or had unknown cells")
        self.check(problems)

    def test_decoder_flags_a_single_flipped_pixel(self):
        img, lay = render_page(1366, 768, 85, 0)
        font = font_table()
        self.assertEqual(screenlib.decode(img, font).bad_rows(), [])
        problems = []
        for row, want_lit in ((5, True), (6, False)):
            spot = next((cell_sample(lay.scale, 0, row, 0, px, py)
                         for px in range(5) for py in range(7)
                         if img.lit(*cell_sample(lay.scale, 0, row, 0, px, py)) == want_lit), None)
            if spot is None:
                problems.append(f"row {row}: no {'lit' if want_lit else 'unlit'} sample to flip")
                continue
            damaged = with_pixel(img, *spot, (0, 0, 0) if want_lit else (0, 255, 0))
            found = screenlib.decode(damaged, font)
            if not found.ok or not found.bad_rows():
                problems.append(f"row {row}: a flipped {'lit' if want_lit else 'unlit'} pixel went unnoticed")
        self.check(problems)

    def test_dump_screen_refusals(self):
        exe = native_exe("plain")
        problems = []
        for args, want in ((("100", "80", "85", "0"), 3), (("1",), 2)):
            rc, out, err = run_native(exe, "--dump-screen", *args)
            if rc != want or out:
                problems.append(f"--dump-screen {' '.join(args)}: exit {rc} with {len(out)} bytes "
                                f"on stdout, expected exit {want} and none")
        self.check(problems)
