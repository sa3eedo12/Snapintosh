#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Mutation sanity check for the 120 Hz additions to tools/sp11_display_mode.py.

A separate file only because tools/test-sp11-display-120hz.py is near the 50 KiB limit. It seeds one plausible bug at a
time into a copy of tools/sp11_link_budget.py, tools/sp11_edid_ext.py, tools/sp11_cta_vic.py or the glue in
tools/sp11_display_mode.py (under build/), runs the 120 Hz suite against that copy and requires it to fail. A mutant
that the suite does not catch is a hole in the suite, not an equivalent change: each text below was chosen so that the
change alters observable behaviour. Results go to build/test-sp11-display-120hz-mutants/mutants.tsv.

    python3 tools/test-sp11-display-120hz-mutants.py
"""
import os
import re
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / 'build' / 'test-sp11-display-120hz-mutants'
SUITE = ROOT / 'tools' / 'test-sp11-display-120hz.py'
MODULES = ('sp11_cta_vic.py', 'sp11_display_mode.py', 'sp11_edid_ext.py', 'sp11_link_budget.py')
HOME_PREFIXES = ('/' + 'Users' + '/', '/' + 'home' + '/')

# (module, what the bug is, old text, new text). Each old text occurs exactly once in its module.
MUTANTS = (
    ('sp11_link_budget.py', 'the 8b/10b factor dropped', 'PAYLOAD_NUMERATOR, PAYLOAD_DENOMINATOR = 8, 10',
     'PAYLOAD_NUMERATOR, PAYLOAD_DENOMINATOR = 10, 10'),
    ('sp11_link_budget.py', 'zero lanes accepted', 'LANE_COUNTS = (1, 2, 4)', 'LANE_COUNTS = (0, 1, 2, 4)'),
    ('sp11_link_budget.py', 'refresh numerator and denominator swapped',
     'return Fraction(self.pixel_clock_khz * 1000, self.htotal * self.vtotal)',
     'return Fraction(self.htotal * self.vtotal, self.pixel_clock_khz * 1000)'),
    ('sp11_link_budget.py', 'a stream of twice the capacity passes (DSC off passes a case that needs it)',
     'fits=required <= capacity,', 'fits=required <= capacity * 2,'),
    ('sp11_link_budget.py', 'DSC silently assumed when no target is given (DSC off passes a case that needs it)',
     'bits_per_pixel, dsc = Fraction(3 * bpc), False', 'bits_per_pixel, dsc = Fraction(3 * bpc, 2), False'),
    ('sp11_link_budget.py', 'a stream that exactly fills the link is refused', 'fits=required <= capacity,',
     'fits=required < capacity,'),
    ('sp11_link_budget.py', 'negative total blanking accepted', 'if htotal < hactive:', 'if False:'),
    ('sp11_link_budget.py', 'the pixel clock bound removed', 'MAX_PIXEL_CLOCK_KHZ = 1 << 24', 'MAX_PIXEL_CLOCK_KHZ = 1 << 80'),
    ('sp11_link_budget.py', 'an unknown rate is looked up anyway', 'if name not in ALL_RATES_KBPS:\n        raise',
     'if False:\n        raise'),
    ('sp11_link_budget.py', 'the minimum vertical blank changed', 'RB_MIN_VBLANK_US = 460', 'RB_MIN_VBLANK_US = 500'),
    ('sp11_link_budget.py', 'the reduced-blanking horizontal blank halved', 'RB_H_BLANK = 160', 'RB_H_BLANK = 80'),
    ('sp11_link_budget.py', 'a DSC target off the sixteenth grid accepted', 'if bits_per_pixel % DSC_STEP or', 'if'),
    ('sp11_link_budget.py', 'the required rate rounded down', 'required_kbps = math.ceil(required)',
     'required_kbps = math.floor(required)'),
    ('sp11_link_budget.py', 'the pixel clock rounded down', 'pixel_clock_khz = math.ceil(refresh * htotal * vtotal / 1000)',
     'pixel_clock_khz = math.floor(refresh * htotal * vtotal / 1000)'),
    ('sp11_edid_ext.py', 'the DisplayID checksum ignored', 'if sum(block[1:end + 1]) & 0xff:', 'if False:'),
    ('sp11_edid_ext.py', 'a block that overruns the section treated as valid (truncated block)',
     'if pos + 3 + length > end:\n            raise', 'if False:\n            raise'),
    ('sp11_edid_ext.py', 'a truncated block header treated as valid', 'if end - pos < 3:', 'if False:'),
    ('sp11_edid_ext.py', 'refresh numerator and denominator swapped',
     'rate = Fraction(clock * 1000 * (2 if interlaced else 1), htotal * vtotal)',
     'rate = Fraction(htotal * vtotal, clock * 1000 * (2 if interlaced else 1))'),
    ('sp11_edid_ext.py', 'Type VII read in 10 kHz units',
     "modes.extend(_timings(payload, 20, 1, 'displayid2-type7', block_rev, index, tag))",
     "modes.extend(_timings(payload, 20, 10, 'displayid2-type7', block_rev, index, tag))"),
    ('sp11_edid_ext.py', 'the DisplayID pixel clock stored without the +1',
     'clock = ((b[0] | b[1] << 8 | b[2] << 16) + 1) * clock_unit_khz',
     'clock = ((b[0] | b[1] << 8 | b[2] << 16) + 0) * clock_unit_khz'),
    ('sp11_edid_ext.py', 'the preferred flag read from the wrong bit', 'preferred = block_rev < 2 and bool(b[3] & 0x80)',
     'preferred = block_rev < 2 and bool(b[3] & 0x40)'),
    ('sp11_edid_ext.py', 'the interlace bit ignored', 'interlaced, stereo = b[3] & 0x0f, bool(b[3] & 0x10), (b[3] >> 5) & 3',
     'interlaced, stereo = b[3] & 0x0f, False, (b[3] >> 5) & 3'),
    ('sp11_edid_ext.py', 'the base-block limit widened', 'BASE_BLOCK_PIXEL_CLOCK_LIMIT_KHZ = 0xFFFF * 10',
     'BASE_BLOCK_PIXEL_CLOCK_LIMIT_KHZ = 0xFFFF * 100'),
    ('sp11_edid_ext.py', 'a reserved video code accepted', 'if byte in (0, 128) or byte >= 254:', 'if False:'),
    ('sp11_edid_ext.py', 'the padding after the CTA descriptors unchecked', 'if any(block[pos:127]):', 'if False:'),
    ('sp11_edid_ext.py', 'the native timing count unchecked', 'if revision >= 2 and (block[3] & 0x0f) > count:', 'if False:'),
    ('sp11_edid_ext.py', 'a 1.x tag accepted in a 2.x section', 'elif tag in (0x03, 0x09):\n        raise',
     'elif False:\n        raise'),
    ('sp11_edid_ext.py', 'the high bits of the maximum rate dropped', '((payload[8] & 3) << 8 if rev & 7 else 0)', '0'),
    ('sp11_edid_ext.py', 'an empty refresh range accepted in a dynamic block',
     "or not 0 < vmin <= vmax:\n            raise ExtError('edid-displayid', f'block {index}: an inconsistent",
     ":\n            raise ExtError('edid-displayid', f'block {index}: an inconsistent"),
    ('sp11_edid_ext.py', 'standard timings never reported',
     "if any(base[0x26 + 2 * i:0x28 + 2 * i] != b'\\x01\\x01' for i in range(8)):", 'if False:'),
    ('sp11_edid_ext.py', 'an interlaced mode returned as progressive', 'and not m.interlaced\n', '\n'),
    ('sp11_edid_ext.py', 'the refresh tolerance ignored', 'and abs(m.refresh_hz - target) <= tolerance_hz]', ']'),
    ('sp11_cta_vic.py', 'a VIC row altered', '(63, 1920, 1080, 0, 297000, 2200, 1125)', '(63, 1920, 1080, 0, 297000, 2640, 1125)'),
    ('sp11_display_mode.py', 'extension refusals swallowed by the strict flow (modes and direct callers)',
     '        if note is None:\n            raise ModeError(error.code, error.message)\n',
     '        if note is None:\n            return None\n'),
    ('sp11_display_mode.py', 'the GOP flow strict again (a malformed extension rejects a mode)',
     'edid = parse_edid(edid_bytes, lenient_extensions=True)', 'edid = parse_edid(edid_bytes)'),
    ('sp11_display_mode.py', 'a lenient refusal dropped without its reason',
     "        note.append(f'{error.code}: {error.message}')\n", ''),
    ('sp11_display_mode.py', 'leniency leaking into the pre-existing CTA structure checks',
     '            cta = _cta_timings(block, i)\n', '            cta = [] if lenient_extensions else _cta_timings(block, i)\n'),
    ('sp11_display_mode.py', 'a refused extension reported as decoded',
     'result, decoded = edid_ext.ExtResult(), False', 'result, decoded = edid_ext.ExtResult(), True'),
    ('sp11_display_mode.py', 'a partial decode reported as complete by the decision', 'modes_complete=not edid.partial,\n',
     'modes_complete=True,\n'),
    ('sp11_display_mode.py', 'a partial decode reported as complete by the modes subcommand',
     'modes_complete=not edid.partial, partial=list(edid.partial), unknown_vics', 'modes_complete=True, partial=list(edid.partial), unknown_vics'),
    ('sp11_display_mode.py', 'the first base timing always marked preferred', 'declared and k == 0', 'k == 0'),
    ('sp11_display_mode.py', 'a missing required timing exits 0', "if not found:\n            result['verdict']",
     "if False:\n            result['verdict']"),
    ('sp11_display_mode.py', 'link-budget always exits 0', "return 0 if result['fits'] else 1", 'return 0'),
    ('sp11_display_mode.py', 'the modes subcommand opens its operator path directly',
     'return read_regular_file(args.edid, 128 * MAX_EDID_BLOCKS)', "return open(args.edid, 'rb').read()"),
    ('sp11_display_mode.py', 'an empty --edid path read as no EDID', 'if args.edid is not None:', 'if args.edid:'),
)


def run_suite(module_dir, build_dir, failfast=False):
    """The 120 Hz suite against the modules in `module_dir`, writing under its own `build_dir` so that concurrent test
    processes never share a file or a pipe. The generous timeout is for a heavily loaded shared host: a timeout is an
    error here, never a kill."""
    command = [sys.executable, '-B', str(SUITE)] + (['-f'] if failfast else [])
    return subprocess.run(command, capture_output=True, text=True, timeout=900,
                          env={**os.environ, 'SP11_DISPLAY_MODE_DIR': str(module_dir), 'SP11_TEST_BUILD_DIR': str(build_dir),
                               'PYTHONDONTWRITEBYTECODE': '1'})


def run_mutant(index):
    """Seed mutant `index` into a copy of the modules and run the suite against it, stopping at the first failure."""
    name, what, old, new = MUTANTS[index]
    work = BUILD / f'mutant-{index:02d}'
    work.mkdir(parents=True, exist_ok=True)
    for module in MODULES:
        text = (ROOT / 'tools' / module).read_text()
        (work / module).write_text(text.replace(old, new) if module == name else text)
    run = run_suite(work, work / 'run', failfast=True)
    failing = re.findall(r'^(?:FAIL|ERROR): (\S+)', run.stderr, re.M)
    return index, name, what, failing[0] if run.returncode and failing else 'SURVIVED'


class Mutants(unittest.TestCase):
    def test_the_unmutated_suite_passes_first(self):
        run = run_suite(ROOT / 'tools', BUILD / 'unmutated')
        self.assertEqual(run.returncode, 0, run.stderr[-3000:])

    def test_every_seeded_bug_is_caught(self):
        sources = {name: (ROOT / 'tools' / name).read_text() for name in MODULES}
        for name, what, old, _ in MUTANTS:
            self.assertEqual(sources[name].count(old), 1, f'mutant {what!r}: the text to mutate must occur exactly once')
        with ThreadPoolExecutor(max_workers=4) as pool:         # each run has its own directories
            rows = list(pool.map(run_mutant, range(len(MUTANTS))))
        BUILD.mkdir(parents=True, exist_ok=True)
        (BUILD / 'mutants.tsv').write_text('id\tmodule\tmutant\tfirst failing test\n' + ''.join('%d\t%s\t%s\t%s\n' % r for r in rows))
        self.assertEqual([r for r in rows if r[3] == 'SURVIVED'], [])
        self.assertGreaterEqual(len(rows), 30)

    def test_the_mutation_file_is_hygienic(self):
        text = Path(__file__).read_text()
        self.assertIn('SPDX-License-Identifier: MIT', '\n'.join(text.splitlines()[:2]))
        self.assertTrue(text.isascii())
        for prefix in HOME_PREFIXES:
            self.assertNotIn(prefix, text)


if __name__ == '__main__':
    unittest.main(verbosity=1)
