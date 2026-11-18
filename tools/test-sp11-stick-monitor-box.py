#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests that the time box is a parameter of the record of the monitor entry run (docs/SP11-STICK-MONITOR.md section 19): every statement that depends on the box
comes from its seconds, for any box from 1 to 120 s: the wait after the MON CALL line and the end of the film, the runaway case, the exposure, the ticks of row e,
D5 and D8 against the 5 s that the operator decided, and the cap that the text names, which is the builder's (tools/sp11_stick_bundle_m1.py BOX_MAX_S), not 5.

    python3 tools/test-sp11-stick-monitor-box.py

The texts are those of the fabricated real build of tools/test-sp11-stick-monitor-text.py, with the builder's cap raised to 120 s as the builder's own work will
raise it. Every check has a faulty case that it rejects: a text that kept a figure of the 5 s box, a change where the box does not matter, a cap that is not the
builder's. No toolchain, no hardware: NOT RUN."""
import importlib.util
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import sp11_stick_bundle_m1 as m1  # noqa: E402

spec = importlib.util.spec_from_file_location('sp11_monitor_text_tests', TOOLS / 'test-sp11-stick-monitor-text.py')
TEXT = importlib.util.module_from_spec(spec)
spec.loader.exec_module(TEXT)
text_of, flat, sha = TEXT.text_of, TEXT.flat, TEXT.sha
# SHA-256 of the text of the fabricated real build for a box of 30 s and one of 120 s, the builder's cap at 120 s: a changed byte of either is another record.
BOX_GOLDEN = {30: 'cb792f08cee47edc68c9ba2442fc06467d688aac3c02b7c038193b0594b67128', 120: 'de7293035d2c1f706b035d1b39cb3bca6a62788d8660d7070f0fc9db0678a5dc'}
# The places where the box matters: a sentence of a record that is not the same in a record of another box must hold one of these.
BOX_ANCHORS = ('LONGER THAN THE 5 s THAT D5 AND D8 DECIDED', 'The rest of D5 and D8 holds', 'D8 the thermal exception', 'D5 the time box is 5 s at most', 'one CPU only, for up to',
               'No thermal guard exists on this path', 'does without them for up to', 'Time box: ', 'the known parts, low sum', 'The plan\'s budget is a run well under 20 s',
               'RELAXES THE 20 s PRECAUTION OF D5 (the time box of', 'D8 was decided on 2026-11-17', 'If the stop timer never fires', 'No page change for',
               'operator-safe wait and not a measurement', 'after the STAGE=DONE line: a lit page that has stopped there', 'the stop came inside the time box',
               'TICKS= counts the whole call', 'does not change for', 'MON IMAGE OFF=', 'MON CALL X0=', 'Film 30 frames a second or more', 'time of one CPU, about')


def sentences(text):
    """The sentences of a text whatever its line breaks: a sentence ends at a full stop and a space."""
    return re.split(r'(?<=\.) ', flat(text))


def expected_box(box, guest_bytes=118_870_336):
    """What a record of `box` seconds must carry, from the box alone: the wait after the MON CALL line (the 60 s approved for a box of 5 s or less, the box plus 60 s
    beyond it), the runaway case, the exposure (the wrapper's 2.9 s scaled to the GUEST.DAT, 8 s of GIC wait, the box, 0.04 s observed, or the bound of 2.9 s for the
    clean) and the ticks of row e."""
    scaled = round(2.923 * guest_bytes / 144.7e6, 1)
    wait = 60 if box <= 5 else box + 60
    return dict(wait=wait, run=wait + 20, low=round(min(2.9, scaled) + 8 + box + 0.04, 1), high=round(max(2.9, scaled) + 8 + box + 2.9, 1), ticks=f'{box * 19_200_000:,}')


def box_problems(text, box, cap):
    """What the text of a record of `box` seconds, for a builder whose cap is `cap`, must say and must not say: each statement here depends on the box."""
    f, e = flat(text), expected_box(box)
    need = (f'No page change for {e["wait"]} seconds after the MON CALL line', f'{e["wait"]} s of silence (section 5) plus about 20 s holding Power, {e["run"]} s or more',
            f'does not change for {e["wait"]} s (no controlled stop: row d)', f'or until {e["wait"]} s after the last page change', f'TICKS <= {e["ticks"]} proves row e',
            f'one CPU only, for up to {box} s', f'one CPU is loaded for about {e["low"]} to {e["high"]} s plus the monitor\'s prepare, {box} s of it by the guest',
            f'the known parts, low sum about {e["low"]} s', f'the known parts, high sum at most {e["high"]} s', f'the known parts alone are about {e["low"]} to {e["high"]} s',
            f'BOX_S={box} ', f'Time box: {box} s of wall time', f'at most {cap} s (the wrapper\'s PARAMS check)',
            *((f'THIS RECORD\'S TIME BOX IS {box} s, LONGER THAN THE 5 s THAT D5 AND D8 DECIDED', f'(this record: {box} s, longer: see above)') if box > 5 else ()))
    forbidden = (f'(this record: {box} s)',) if box > 5 else ('LONGER THAN THE', 'longer: see above')
    return [n for n in need if n not in f] + [n for n in forbidden if n in f]


def budget_problems(text):
    """What section 4 keeps true for the decided box of 5 s: the figures of the table as printed, the sums of those figures, the bound of the clean from the placement and
    the sentences that quote them. Moved here from the auth suite on purpose (it is about the box), which had no room left."""
    parts = {k: float(v) for k, v in re.findall(r'^ {6}(wrapper checks, copy and verify|GIC arm: the announcement and the wait|guest)\s+(\d+(?:\.\d+)?) s', text, re.M)}
    stop = re.search(r'stop: clean of (\d+) MiB, the bound\s+at most (\d+\.\d) s \(INFERRED: the dry run verified', text)
    seen = re.search(r'monitor prepare and stop clean\s+(0\.\d+) s\s+OBSERVED once', text)
    sums = re.search(r'low sum\s+about (\d+\.\d) s \((\d+\.\d) \+ 8 \+ 5 \+ 0\.04\)\s+the known parts, high sum\s+at most (\d+\.\d) s \((\d+\.\d) \+ 8 \+ 5 \+ (\d+\.\d)\), plus the prepare',
                     text)
    place = re.search(r'guest_size (\d+) MiB  reserve (\d+) MiB', text)
    if len(parts) != 3 or not (stop and seen and sums and place):
        return ['a figure of the table is missing']
    low, low_wrapper, high, high_wrapper, bound = (float(x) for x in sums.groups())
    rest = parts['GIC arm: the announcement and the wait'] + parts['guest']
    f = flat(text)
    return [name for name, ok in (
        ('the low sum is not of its figures', round(low_wrapper + rest + float(seen.group(1)), 1) == low),
        ('the high sum is not of its figures', round(high_wrapper + rest + bound, 1) == high),
        ('the high sum does not use the wrapper time and the bound of the table', (high_wrapper, bound) == (parts['wrapper checks, copy and verify'], float(stop.group(2)))),
        ('the stop does not clean the guest range and the reserve', int(stop.group(1)) == int(place.group(1)) + int(place.group(2))),
        ('the bound is not the dry run\'s rate on that size', float(stop.group(2)) == round(0.96 * int(stop.group(1)) / 141.4, 1)),
        ('the sums are not quoted as printed', f'about {low} to {high} s plus the prepare in all' in f and f'the known parts alone are about {low} to {high} s' in f),
        ('the earliest restart datum is not said', '224 s and 268 s' in f and 'The earliest restart datum is about UPTIME 17 s (INFERRED: one boot, in a run that started '
                                                                          'secondary CPUs; the plan, section 10)' in f),
        ('the relaxation of the precaution of D5 is not said', 'APPROVING THIS TEXT RELAXES THE 20 s PRECAUTION OF D5 (the time box of 5 s is kept;' in f)) if not ok]


class Boxes(unittest.TestCase):
    def texts(self, *boxes, cap=120):
        with mock.patch.object(m1, 'BOX_MAX_S', cap):
            return {box: text_of(time_box_s=box) for box in boxes}

    def test_every_number_that_depends_on_the_box_comes_from_it_for_any_box_from_1_to_120_s(self):
        for box, text in self.texts(1, 2, 4, 5, 6, 30, 59, 60, 61, 119, 120).items():
            with self.subTest(box):
                self.assertEqual(box_problems(text, box, 120), [])
                self.assertTrue(text.isascii())
                for name, part in (('0 and 0b', text.split('0. What approving')[1].split('1. What may run')[0]), ('4', text.split('4. Time box')[1].split('5. Refusal')[0]),
                                   ('5 to 5c', text.split('5. Refusal')[1])):
                    self.assertLessEqual(max(map(len, part.splitlines())), 120, f'section {name}')
        self.assertEqual(box_problems(text_of(), 5, m1.BOX_MAX_S), [], 'the text of the decided box, under the cap of the builder as it is')

    def test_the_time_budget_has_its_numbers_and_they_add_up(self):
        text = text_of()
        self.assertEqual(budget_problems(text), [])
        for name, damaged in (('a low sum that is not of its figures', re.sub(r'(low sum\s+about )15\.4', r'\g<1>15.5', text)),
                              ('a bound that is not the rate on that size', re.sub(r'(clean of 432 MiB, the bound\s+at most )2\.9', r'\g<1>3.1', text)),
                              ('a guest time that is not the box', re.sub(r'(\n {6}guest\s+)5 s', r'\g<1>6 s', text)),
                              ('a relaxation that is not said', text.replace('APPROVING THIS TEXT RELAXES', 'APPROVING THIS TEXT KEEPS')),
                              ('a restart datum that is not said', text.replace('224 s and 268 s', '224 s'))):
            with self.subTest(name):
                self.assertNotEqual(damaged, text)
                self.assertTrue(budget_problems(damaged), 'rejected')

    def test_the_known_parts_are_a_bracket_for_a_guest_smaller_and_for_one_larger_than_the_dry_runs(self):
        for size, branch in ((118_870_336, 'that figure is conservative: it goes into the high sum'), (200_000_000, 'the low sum keeps that figure and the high sum uses the')):
            for box in (5, 30):
                with self.subTest(size=size, box=box), mock.patch.object(m1, 'BOX_MAX_S', 120):
                    text = text_of(time_box_s=box, guest={**TEXT.AUTH.FIXED['guest'], 'size': size})
                    e = expected_box(box, size)
                    parts = re.search(r'the known parts, low sum\s+about (\d+\.\d) s \((\d+\.\d) \+ 8 \+ ' + str(box) + r' \+ 0\.04\)\s+the known parts, high sum\s+at most (\d+\.\d) s '
                                      r'\((\d+\.\d) \+ 8 \+ ' + str(box) + r' \+ 2\.9\), plus the prepare', text)
                    low, low_wrapper, high, high_wrapper = map(float, parts.groups())
                    self.assertEqual((low, high), (e['low'], e['high']), 'the known parts from the box and the size alone')
                    self.assertEqual((low_wrapper, high_wrapper), (min(2.9, round(2.923 * size / 144.7e6, 1)), max(2.9, round(2.923 * size / 144.7e6, 1))), 'the smaller in the low sum')
                    self.assertEqual((round(low_wrapper + 8 + box + 0.04, 1), round(high_wrapper + 8 + box + 2.9, 1)), (low, high), 'the sums are of the figures as printed')
                    self.assertLessEqual(low, high)
                    self.assertIn(branch, flat(text))

    def test_a_text_that_kept_a_figure_of_the_five_second_box_is_found(self):
        f = flat(self.texts(30)[30])
        for name, old, new in (('the wait after the call', 'No page change for 90 seconds', 'No page change for 60 seconds'), ('the runaway case', '110 s or more', '80 s or more'),
                               ('the failed page', 'does not change for 90 s (no controlled', 'does not change for 60 s (no controlled'),
                               ('the end of the film', 'or until 90 s after the last page change', 'or until 60 s after the last page change'),
                               ('the ticks of row e', 'TICKS <= 576,000,000', 'TICKS <= 96,000,000'), ('the exposure', 'about 40.4 to 43.8 s plus the monitor', 'about 15.4 to 18.8 s plus the monitor'),
                               ('the paragraph of a longer box', 'LONGER THAN THE 5 s THAT D5 AND D8 DECIDED', 'DAMAGED'),
                               ('D5 and D8 against a longer box', '(this record: 30 s, longer: see above)', '(this record: 30 s)'),
                               ('the cap of the builder', 'at most 120 s (the wrapper\'s PARAMS check)', 'at most 5 s (the wrapper\'s PARAMS check)')):
            with self.subTest(name):
                self.assertIn(old, f)
                self.assertTrue(box_problems(f.replace(old, new), 30, 120), 'rejected')
        self.assertTrue(box_problems(flat(text_of()), 5, 120), 'the cap is the builder\'s: a text that says 5 under a cap of 120 is rejected')

    def test_a_longer_box_changes_the_text_only_where_the_box_matters_and_each_number_is_pinned(self):
        texts = self.texts(5, 30, 120)
        for box, golden in BOX_GOLDEN.items():
            with self.subTest(box):
                self.assertEqual(sha(texts[box].encode()), golden, 'the text of this box is pinned')
                changed = set(sentences(texts[box])) ^ set(sentences(texts[5]))
                self.assertEqual([s for s in changed if not any(a in s for a in BOX_ANCHORS)], [], 'a sentence changed where the box does not matter')
                self.assertTrue(all(any(a in s for s in changed) for a in BOX_ANCHORS), 'each place where the box matters changed')
                e = expected_box(box)
                for number in (f'{e["wait"]} seconds', f'{e["run"]} s or more', f'{e["low"]} to {e["high"]} s', f'{e["ticks"]} proves', f'BOX_S={box} '):
                    self.assertIn(number, flat(texts[box]))
                    self.assertNotIn(number, flat(texts[5]))
        faulty = flat(texts[30]).replace('FORCE OFF', 'power cycle', 1)
        self.assertTrue([s for s in set(sentences(faulty)) ^ set(sentences(texts[5])) if not any(a in s for a in BOX_ANCHORS)], 'a change elsewhere is found')

    def test_the_cap_the_text_names_is_the_builders(self):
        for cap in (5, 10, 120):
            with self.subTest(cap), mock.patch.object(m1, 'BOX_MAX_S', cap):
                self.assertIn(f'at most {cap} s (the wrapper\'s PARAMS check)', flat(text_of(time_box_s=min(cap, 5))))
                self.assertEqual(box_problems(text_of(time_box_s=min(cap, 5)), min(cap, 5), cap), [])

    def test_a_longer_box_than_the_decided_one_is_decided_by_approving_the_text_and_d5_and_d8_are_said_as_they_were(self):
        f = flat(self.texts(30)[30])
        for needle in ('approving this text decides this box and the exposure that section 4 gives, which D8 accepted for a box of 5 s only. The rest of D5 and D8 holds: '
                       'one CPU, AC power REQUIRED.',
                       'D8 the thermal exception: accepted for ONE CPU, a time box of 5 s (this record: 30 s, longer: see above) and AC power REQUIRED.',
                       'D5 the time box is 5 s at most (this record: 30 s, longer: see above), and approving this text relaxes the 20 s UPTIME precaution that D5 left open (section 4)',
                       'D8 was decided on 2026-11-17 for ONE CPU, a time box of 5 s (this record: 30 s, longer: see above) and AC power REQUIRED; approving this text accepts '
                       'the exception for the box of 30 s (section 0)',
                       'the time box of 30 s is this record\'s own: section 0',
                       'The 90 s is the time box of 30 s plus 60 s, an operator-safe wait and not a measurement',
                       'The wait is 60 s after the STAGE=DONE line'):
            with self.subTest(needle[:40]):
                self.assertIn(needle, f)
                self.assertNotIn(needle, flat(text_of()))
        short = flat(self.texts(3)[3])
        self.assertIn('a time box of 5 s at most (this record: 3 s)', short)
        self.assertNotIn('LONGER THAN THE', short, 'a shorter box needs no new decision')


if __name__ == '__main__':
    unittest.main()
