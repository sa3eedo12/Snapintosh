#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of tools/sp11_stick_gatescan.py: a C source or header as it compiles where SNAP_SP11_STICK_MONITOR is 0, and the provenance check of the GIC pair built on it
(tools/test-sp11-stick-bundle-record-gic-model.py holds the sources of the pair to the commit that the approved note names). The reporter, the largest source the pair shares
with the M1 build, may change inside `#if SNAP_SP11_STICK_MONITOR` without changing the pair; an edit anywhere else in it, a comment or a lost `#endif` included, must still be
found. Each of those is a seeded fault of the reporter's text that the check must reject, and a conditional the scan cannot follow must fail closed.

    python3 tools/test-sp11-stick-gatescan.py

SKIPs (which is not a pass) when git does not have the note's commit. No toolchain, no hardware, nothing under /Volumes. EXPERIMENTAL; hardware NOT RUN."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))
import sp11_stick_gatescan as S  # noqa: E402

REPORTER = 'probes/sp11-stick-reporter.c'
COMMIT = re.search(r"^COMMIT = '([0-9a-f]{40})'", (TOOLS / 'test-sp11-stick-bundle-record-gic.py').read_text(), re.M).group(1)     # the one the note's line is held to
G = 'SNAP_SP11_STICK_MONITOR'


class GateOff(unittest.TestCase):
    def test_what_the_gate_leaves_out_is_dropped_and_the_rest_is_kept_as_it_is(self):
        for name, text, want in (('a block of the gate', f'#if {G}\nA\n#endif\nB\n', 'B\n'),
                                 ('the else of a negated gate is what the pair does not compile', f'#if !{G}\nA\n#else\nB\n#endif\n', 'A\n'),
                                 ('the else of the gate is what the pair compiles', f'#if {G}\nA\n#else\nB\n#endif\n', 'B\n'),
                                 ('a conditional in a block of the gate', f'#if {G}\n#if FOO\nA\n#else\nB\n#endif\n#endif\nC\n', 'C\n'),
                                 ('another conditional is kept, both ways, with its directives', '#if FOO\nX\n#else\nY\n#endif\n', '#if FOO\nX\n#else\nY\n#endif\n'),
                                 ('the gate in another conditional', f'#if FOO\n#if {G}\nA\n#endif\nB\n#endif\n', '#if FOO\nB\n#endif\n'),
                                 ('a comment is text', f'/* c */\n#if {G}\nA\n#endif\n', '/* c */\n'),
                                 ('a longer name is another macro', f'#if {G}_X\nA\n#endif\n', f'#if {G}_X\nA\n#endif\n')):
            with self.subTest(name):
                self.assertEqual(S.gate_off(text), want)

    def test_a_conditional_the_scan_cannot_follow_fails_closed(self):
        for name, text in (('an endif without an if', '#endif\n'), ('a block left open', '#if FOO\nA\n'), ('an else without an if', '#else\n'), ('an elif without an if', '#elif 1\n'),
                           ('an elif of the gate', f'#if {G}\nA\n#elif BAR\nB\n#endif\n'), ('an expression with the gate', f'#if {G} && BAR\nA\n#endif\n'),
                           ('an ifdef of the gate', f'#ifdef {G}\nA\n#endif\n'), ('a defined() of the gate', f'#if defined({G})\nA\n#endif\n')):
            with self.subTest(name):
                with self.assertRaises(ValueError):
                    S.gate_off(text)
                self.assertFalse(S.same_where_gate_is_off(text, text), 'a text that cannot be followed is never taken for unchanged')


class Provenance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            S.source_problems(COMMIT, [])
        except LookupError as why:
            raise unittest.SkipTest(str(why)) from why
        cls.text = (ROOT / REPORTER).read_text()

    def edit(self, old, new):
        self.assertEqual(self.text.count(old), 1, f'the seeded fault matches the reporter exactly once: {old!r}')
        return self.text.replace(old, new)

    def problems(self, text, name=REPORTER):
        return S.source_problems(COMMIT, [name], read=lambda _: text)

    def test_the_reporter_as_it_is_is_the_pairs_and_an_edit_inside_the_gate_is_no_change(self):
        self.assertEqual(S.source_problems(COMMIT, [REPORTER]), [])
        self.assertEqual(self.problems(self.edit('#define RP_REG_LINES 8u', '#define RP_REG_LINES 9u')), [], 'the register lines are in the monitor part, inside the gate')

    def test_code_outside_the_gate_in_the_reporter_changed_is_a_change_of_the_pair(self):
        for what, bad in (('a statement in the head', self.edit('pl_text(&l, "VERDICTS PIN=");', 'pl_text(&l, "VERDICTS PIN =");')),
                          ('a comment of the head', self.edit(' * Returns 0 after a decoded report', ' * Returns zero after a decoded report')),
                          ('a line added outside the gate', self.edit('#define RP_RETURN_UNREADABLE UINT64_C(2)', '#define RP_RETURN_UNREADABLE UINT64_C(2)\n#define RP_NEW 1')),
                          ('the gate of the monitor part taken off', self.edit(f'#if {G}\n/* ---- The monitor entry\'s part', '#if 1\n/* ---- The monitor entry\'s part')),
                          ('the end of the monitor part lost', self.edit('#endif\n\nuint64_t payload_entry(const struct q1n1_payload_args *a)\n{', 'uint64_t payload_entry(const struct q1n1_payload_args *a)\n{')),
                          ('an elif of the gate', self.edit('#else\n    for (i = 0; i < sizeof order; i++) {', '#elif 1\n    for (i = 0; i < sizeof order; i++) {'))):
            with self.subTest(what):
                self.assertEqual(self.problems(bad), [REPORTER])

    def test_any_other_file_must_be_identical_and_a_file_that_was_not_there_is_a_problem(self):
        self.assertEqual(self.problems('x', 'tools/sp11_stick_wrapper_build.py'), ['tools/sp11_stick_wrapper_build.py'])
        self.assertEqual(self.problems('x', 'docs/SP11-STICK-REPORT-LINES.md'), ['docs/SP11-STICK-REPORT-LINES.md'], 'it is not in the note\'s commit')
        self.assertEqual(S.source_problems(COMMIT, ['config/monitor-profile-sp11-m1.json']), [], 'the profile is the commit\'s; the wrapper build tool is not (it gained --cpu-start) and is held to a digest by the GIC model')


if __name__ == '__main__':
    unittest.main()
