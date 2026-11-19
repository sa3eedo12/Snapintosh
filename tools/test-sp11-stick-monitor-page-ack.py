#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of what the page of SNAPREP-M1.BIN says about the guest timer and the CPU-start acknowledge of an ack monitor (docs/SP11-STICK-REPORT-LINES.md), on the windows of
tools/sp11_stick_monitor_pagefix.py (the stop of the M1 rung 5 run, boot 25, with made-up values): the four states of the guest timer that its words tell apart (the outcomes
T-OK, T-NODL, T-LOST and T-STUCK of the timer design) are read from the page text alone, WRITES is told from READS, the acknowledge line is the block's count and first stores each
whole (MORE exact; a layout that is not the one the reporter reads is named, a block without the magic is no block), a failed prepare prints none of it, a capture word that is
no class is named and a class or none is not, and the stop's C= is never cut (it goes on a line of its own when it would pass the runner's 100 columns). The words of the
documents about AFTER and LAST are held too. The page against the independent renderer is tools/test-sp11-stick-monitor-page.py and the mutants of the reporter are
tools/test-sp11-stick-monitor-page-mutants.py.

    python3 tools/test-sp11-stick-monitor-page-ack.py

The tests need clang and a q1n1 checkout (Q1N1_SP11_TREE); without them they SKIP, which is not a pass. No hardware, nothing under /Volumes, no network. EXPERIMENTAL; hardware NOT RUN."""
import importlib.util
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
_load = importlib.util.spec_from_file_location('sp11_page_tests', Path(__file__).with_name('test-sp11-stick-monitor-page.py'))
page = importlib.util.module_from_spec(_load)
_load.loader.exec_module(page)
P, ROOT = page.P, page.ROOT
PREFIX = 'SNAPREP '
OUTCOMES = {'timer-ok': 'T-OK', 'timer-no-deadline': 'T-NODL', 'timer-lost': 'T-LOST', 'timer-stuck': 'T-STUCK'}


def setUpModule():
    page.setUpModule()


def tearDownModule():
    page.tearDownModule()


def words(texts, key):
    """The NAME=value tokens of the lines of one kind (`MON TIM`) as numbers: 0X... is hexadecimal."""
    found = {}
    for text in texts:
        if text.startswith(f'{PREFIX}{key} '):
            for token in text[len(PREFIX) + len(key) + 1:].split(' '):
                name, _, value = token.partition('=')
                found[name] = int(value, 16) if value.startswith('0X') else int(value)
    return found


def outcome(w):
    """The reading of the timer's words that the timer design (section 2) gives, in the order in which the readings exclude each other: a deadline that has passed with no tick, a
    virtual FIQ raised and never taken, a timer that was armed and left disabled, a timer that ticked and delivered."""
    if w['CTL'] == 5 and w['IRQS'] == 0:
        return 'T-LOST'
    if w['CTL'] == 5 and w['ASSERTS'] > w['CLEARS'] and w['VECTORS'] < w['ASSERTS']:
        return 'T-STUCK'
    if w['CTL'] in (0, 2) and w['WRITES'] > 0:
        return 'T-NODL'
    if w['IRQS'] > 0 and w['VECTORS'] > 0 or w['CTL'] == 1:
        return 'T-OK'
    return 'UNKNOWN'


class Timer(page.Base):
    def lines(self, name, key):
        return [t for t in self.results[name].texts() if t.startswith(f'{PREFIX}{key}')]

    def test_each_outcome_of_the_timer_design_is_read_from_the_page_text_alone_and_the_page_holds_the_nine_words(self):
        for name, want in OUTCOMES.items():
            texts = self.results[name].texts()
            w = words(texts, 'MON TIM')
            reads, writes, irqs, asserts, clears, vectors, ctl, cval, irq_pc = self.specs[name].timer
            with self.subTest(name):
                self.assertEqual(w, dict(IRQS=irqs, ASSERTS=asserts, CLEARS=clears, VECTORS=vectors, CTL=ctl, CVAL=cval, WRITES=writes, IRQPC=irq_pc, READS=reads))
                self.assertEqual(len(self.lines(name, 'MON TIM')), 2, 'the nine words are on two lines: five-digit counters do not leave one line for all')
                self.assertEqual(outcome(w), want)
                self.assertNotEqual(w['WRITES'], w['READS'], 'the fixture tells the two apart')
        self.assertEqual(len({outcome(words(self.results[name].texts(), 'MON TIM')) for name in OUTCOMES}), 4)

    def test_a_small_cval_reaches_writes_on_the_line_of_a_stage_2_page_and_the_rest_is_counted(self):
        self.assertIn(f'{PREFIX}MON TIM IRQS=3 ASSERTS=2 CLEARS=2 VECTORS=2 CTL=0X5 CVAL=0X1F4 WRITES=7 MORE=2', self.results['ack-loud'].texts(), 'reads 11, writes 7: WRITES is not READS')

    def test_the_timer_is_printed_only_for_a_guest_that_ran_with_a_capture(self):
        for name in ('ack-partial', 'capture-marker-partial', 'capture-4', 'capture-odd', 's2-nocap', 'panic-entry-nocap', 'ack-nocap'):
            with self.subTest(name):
                self.assertEqual(self.lines(name, 'MON TIM'), [], 'a failed prepare left a partial window, and a window with no capture has none of the guest\'s state')


class Ack(page.Base):
    def line(self, name):
        found = [t for t in self.results[name].texts() if t.startswith(f'{PREFIX}MON ACK ')]
        self.assertLessEqual(len(found), 1, name)
        return found[0][len(PREFIX + 'MON ACK '):] if found else None

    def test_the_line_is_the_count_and_the_first_stores_each_whole_and_more_is_exact(self):
        stores = '4=0X2 8=0X2 12=0X0'
        for name, want in (('timer-ok', f'COUNT=3 {stores}'), ('ack-none', 'COUNT=0'), ('ack-eight', f'COUNT=8 {stores} {stores} 4=0X2 8=0X2'),
                           ('ack-many', f'COUNT=1000 {stores} {stores} 4=0X2 8=0X2 MORE=992'), ('ack-nocap', f'COUNT=3 {stores}'), ('ack-loud', f'COUNT=3 {stores}'),
                           ('ack-wide', 'COUNT=18446744073709551615 4294967295=0XFFFFFFFF MORE=18446744073709551614')):
            with self.subTest(name):
                self.assertEqual(self.line(name), want)
                self.assertLessEqual(len(PREFIX + 'MON ACK ' + want), P.WIDE)

    def test_a_layout_the_reporter_does_not_read_is_named_and_a_block_without_its_magic_is_no_block(self):
        self.assertEqual(self.line('ack-layout'), 'LAYOUT=0XC000000001 NOT READ')
        for name in ('ack-badmagic', 'timer-quiet', 'ack-partial', 'capture-marker-partial', 'boot20', 'full'):
            with self.subTest(name):
                self.assertIsNone(self.line(name))

    def test_the_acknowledge_is_the_blocks_and_not_the_captures(self):
        texts = self.results['ack-nocap'].texts()
        self.assertFalse([t for t in texts if t.startswith((f'{PREFIX}MON GX', f'{PREFIX}MON TIM', f'{PREFIX}MON EXIT'))], 'no capture: no registers and no timer')
        self.assertTrue(self.line('ack-nocap'))

    def test_the_page_never_has_more_than_forty_lines_or_loses_a_gic_line_for_the_acknowledge(self):
        for name in ('timer-ok', 'ack-eight', 'ack-many', 'ack-wide', 'ack-layout', 'ack-loud', 'ack-quiet', 'stop-edge-101'):
            texts = self.results[name].texts()
            with self.subTest(name):
                self.assertLessEqual(len(texts), 40)
                self.assertEqual(len([t for t in texts if t.startswith(f'{PREFIX}GIC ')]), 5)
        self.assertEqual(len(self.results['timer-ok'].texts()), 40, 'the page of the next boot is exactly the budget: nothing is left over for another line')


class Stop(page.Base):
    def test_the_c_of_the_stop_is_never_cut(self):
        for name, spec in self.specs.items():
            if not spec.terminal:
                continue
            stop = [t for t in self.results[name].texts() if t.startswith(f'{PREFIX}MON STOP')]
            c = ' C=0X%X' % spec.terminal[4]
            with self.subTest(name):
                self.assertIn(len(stop), (1, 2))
                self.assertLessEqual(max(map(len, stop)), 100)
                if len(stop) == 2:
                    self.assertEqual(stop[1], f'{PREFIX}MON STOP{c}')
                    self.assertGreater(len(stop[0]) + len(c), 100, 'the split is for a value that would have been cut')
                else:
                    self.assertTrue(stop[0].endswith(c), 'C= whole at the end of the line')

    def test_it_goes_on_a_line_of_its_own_from_the_101st_column_and_not_before(self):
        one = [t for t in self.results['stop-edge-100'].texts() if t.startswith(f'{PREFIX}MON STOP')]
        self.assertEqual([len(t) for t in one], [100])
        self.assertTrue(one[0].endswith(' C=0XFFFFFE000BA87E'))
        two = [t for t in self.results['stop-edge-101'].texts() if t.startswith(f'{PREFIX}MON STOP')]
        self.assertEqual(two, [f'{PREFIX}MON STOP WATCHDOG PC=0XFFFFFE000B8716A8 A=0X9600004F B=0XFFFFFECFFBFDC000', f'{PREFIX}MON STOP C=0XFFFFFE000BA87E4'])


class Capture(page.Base):
    def test_a_capture_word_that_is_no_class_is_named_for_a_guest_that_ran_and_a_class_or_none_is_not(self):
        for name, spec in self.specs.items():
            marker = [t for t in self.results[name].texts() if t.startswith(f'{PREFIX}MON CAPTURE ')]
            with self.subTest(name):
                self.assertEqual(marker, [f'{PREFIX}MON CAPTURE WORD=0X%X IS NO CLASS' % spec.capture] if spec.status == 0 and spec.capture not in (0, 1, 2, 3) else [])
        self.assertEqual([t for t in self.results['capture-4'].texts() if 'CAPTURE' in t], [f'{PREFIX}MON CAPTURE WORD=0X4 IS NO CLASS'])


class Documents(unittest.TestCase):
    def test_after_and_last_are_the_monitors_report_time_sample_and_never_the_guests_last_use_of_the_clock(self):
        wrong = re.compile(r"(?<!not )(?<!NOT )the guest'?s? (?:last|latest) (?:use of the clock|clock use|clock sample|read of the clock|clock read)", re.I)
        for rel in ('docs/SP11-STICK-REPORT-LINES.md', 'docs/SP11-STICK-MONITOR.md', 'probes/sp11-stick-reporter.c'):
            self.assertIsNone(wrong.search((ROOT / rel).read_text()), rel)
        doc = (ROOT / 'docs/SP11-STICK-REPORT-LINES.md').read_text()
        self.assertIn("NOT the guest's last use of the clock", doc)
        self.assertRegex(doc, r'LAST[^\n]*report writer')


if __name__ == '__main__':
    unittest.main()
