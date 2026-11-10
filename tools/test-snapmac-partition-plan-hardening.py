#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests for how tools/snapmac-partition-plan.py counts readers and what it says about the MBR.

* The X6 gate wants four independent readers. Independence used to be judged by the document JSON, which includes the
  free "reader" label, so four copies of one AFTER survey under four labels gave `four_reader_gate_met: true`. A
  reader's label is now left out when documents are compared: documents that differ only in their label are one
  observation, and the gate needs all but one of its readers to differ in content (the plan's fourth reader, a second
  Windows record after a normal boot, may repeat the first, so a gate of 4 needs 3 different documents).
* `convert_sgdisk` accepted text without the "Partition table scan:" block, so its refusal of a hybrid MBR could not
  trigger, and nothing said so. The block is what states the MBR is protective: only a `gdisk -l` conversion now carries
  "mbr": "protective", every other reader is MBR-unchecked, and the verdict lists both groups.

Synthetic layouts only (the fixtures are those of tools/test-snapmac-partition-plan-sp11.py, which is loaded for them);
nothing reads a device.

    python3 tools/test-snapmac-partition-plan-hardening.py
"""
from pathlib import Path
import copy
import importlib.util
import sys
import unittest

sys.dont_write_bytecode = True
TOOLS = Path(__file__).resolve().parent

spec = importlib.util.spec_from_file_location('partition_plan_fixtures', TOOLS / 'test-snapmac-partition-plan-sp11.py')
T = importlib.util.module_from_spec(spec)
spec.loader.exec_module(T)
pm = T.pm


class Gate(unittest.TestCase):
    bs = 512

    def setUp(self):
        self.rows = T.layout()
        self.before = T.survey_of(self.rows, self.bs)
        self.plan = pm.plan_sp11(self.before)
        self.good = T.applied(self.rows, self.plan)

    def verify(self, readers, **kw):
        notes, verdict = [], {}
        problems = pm.verify_sp11(self.before, readers, self.plan, notes=notes, verdict=verdict, **kw)
        return problems, notes, verdict

    def relabelled(self, doc, label):
        doc = copy.deepcopy(doc)
        doc['reader'] = label
        return label, doc

    def documented_four(self):
        """Windows, a normalized reader, the firmware survey and a Windows record after a normal boot."""
        good, bs = self.good, self.bs
        return [('windows', T.win_record(good, bs)), ('mac-diskutil', T.norm_record(good, 'mac-diskutil', bs)),
                ('q1n1', T.survey_of(good, bs)), ('windows-boot', T.win_record(good, bs, reader='windows-boot'))]


class IndependentReaders(Gate):
    def test_one_survey_under_four_labels_does_not_meet_the_gate(self):
        survey = T.survey_of(self.good, self.bs)
        problems, _, verdict = self.verify([self.relabelled(survey, f'copy-{i}') for i in range(4)])
        readers = verdict['readers']
        self.assertFalse(readers['four_reader_gate_met'])
        self.assertFalse(verdict['ok'])
        self.assertEqual((readers['distinct'], readers['independent']), (4, 1))
        self.assertTrue(any('differ in content' in p for p in problems), problems)

    def test_copies_of_every_kind_of_document_are_one_observation(self):
        good, bs = self.good, self.bs
        for kind, make in (('windows', lambda: T.win_record(good, bs)),
                           ('normalized', lambda: T.norm_record(good, 'x', bs)),
                           ('full', lambda: T.survey_of(good, bs))):
            with self.subTest(kind):
                readers = [self.relabelled(make(), f'{kind}-{i}') for i in range(4)]
                readers.append(('q1n1', T.survey_of(good, bs, eui=None)))      # a full reader, so that one is present
                problems, _, verdict = self.verify(readers)
                self.assertFalse(verdict['readers']['four_reader_gate_met'])
                self.assertTrue(any('differ in content' in p for p in problems), problems)

    def test_three_copies_and_one_other_reader_are_not_enough(self):
        survey = T.survey_of(self.good, self.bs)
        readers = [self.relabelled(survey, f'copy-{i}') for i in range(3)]
        readers.append(('windows', T.win_record(self.good, self.bs)))
        problems, _, verdict = self.verify(readers)
        self.assertEqual(verdict['readers']['independent'], 2)
        self.assertFalse(verdict['readers']['four_reader_gate_met'])
        self.assertTrue(any('2 of the 4 reader documents differ in content' in p for p in problems), problems)

    def test_the_documented_fourth_reader_may_repeat_the_first_windows_record(self):
        problems, notes, verdict = self.verify(self.documented_four())
        readers = verdict['readers']
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual((readers['agreeing'], readers['independent'], readers['four_reader_gate_met']), (4, 3, True))

    def test_the_requirement_follows_the_reader_count_that_is_asked_for(self):
        survey = T.survey_of(self.good, self.bs)
        two_kinds = [('windows', T.win_record(self.good, self.bs)), self.relabelled(T.win_record(self.good, self.bs), 'again'),
                     ('q1n1', survey)]
        problems, _, verdict = self.verify(two_kinds, min_readers=3)
        self.assertEqual((problems, verdict['readers']['independent']), ([], 2))     # 3 readers need 2 different
        self.assertFalse(verdict['readers']['four_reader_gate_met'])
        copies = [self.relabelled(survey, f'copy-{i}') for i in range(3)]
        problems, _, verdict = self.verify(copies, min_readers=3)
        self.assertTrue(any('1 of the 3 reader documents differ in content' in p for p in problems), problems)
        problems, _, verdict = self.verify([('q1n1', survey)], min_readers=1)
        self.assertEqual(problems, [])                                                # one reader needs one

    def test_a_partial_run_cannot_claim_the_four_reader_gate_with_copies(self):
        survey = T.survey_of(self.good, self.bs)
        readers = [self.relabelled(survey, f'copy-{i}') for i in range(2)] + [
            ('windows', T.win_record(self.good, self.bs)), self.relabelled(T.win_record(self.good, self.bs), 'again')]
        problems, notes, verdict = self.verify(readers, min_readers=3)
        self.assertEqual(problems, [])
        self.assertEqual(verdict['readers']['independent'], 2)
        self.assertFalse(verdict['readers']['four_reader_gate_met'])

    def test_the_label_is_ignored_wherever_it_sits(self):
        """The document's own "reader" key and the label given alongside it name a reader; neither adds content."""
        survey = T.survey_of(self.good, self.bs)
        with_key = self.relabelled(survey, 'a')[1]
        without_key = copy.deepcopy(survey)
        problems, _, verdict = self.verify([('a', with_key), ('b', without_key), ('c', T.win_record(self.good, self.bs))],
                                           min_readers=2)
        self.assertEqual(problems, [])
        self.assertEqual(verdict['readers']['independent'], 2)

    def test_disagreeing_content_is_still_reported_per_reader(self):
        wrong = T.survey_of(T.applied(self.rows, self.plan, last=self.plan['new']['last_lba'] - 1), self.bs)
        readers = self.documented_four()
        readers[2] = ('q1n1', wrong)
        problems, _, verdict = self.verify(readers)
        self.assertTrue(any(p.startswith('reader q1n1: ') for p in problems), problems)
        self.assertFalse(verdict['readers']['four_reader_gate_met'])


class MbrMarking(Gate):
    def convert(self, scan):
        return pm.convert_sgdisk(T.sgdisk_text(self.good, self.bs, scan=scan), 'gdisk')

    def test_only_the_gdisk_scan_block_marks_the_mbr_protective(self):
        self.assertEqual(self.convert(True)['mbr'], 'protective')
        self.assertNotIn('mbr', self.convert(False))

    def test_the_marking_survives_the_reader_loader(self):
        for scan, state in ((True, 'protective'), (False, 'unchecked')):
            with self.subTest(scan=scan):
                record = pm.load_reader(self.convert(scan), 'gdisk', self.before_guid())
                self.assertEqual(record['mbr'], state)

    def before_guid(self):
        return T.DISK_GUID

    def test_a_reader_may_state_either_value_and_nothing_else(self):
        base = T.norm_record(self.good, 'x', self.bs)
        for value in ('protective', 'unchecked'):
            self.assertEqual(pm.load_reader(dict(base, mbr=value), 'x', T.DISK_GUID)['mbr'], value)
        for value in ('hybrid', 'checked', True, 1, None, ['protective']):
            with self.subTest(value=value):
                with self.assertRaises(pm.PlanError):
                    pm.load_reader(dict(base, mbr=value), 'x', T.DISK_GUID)
        self.assertEqual(pm.load_reader(base, 'x', T.DISK_GUID)['mbr'], 'unchecked')

    def test_the_verdict_names_the_readers_that_could_see_a_hybrid_mbr_and_the_ones_that_could_not(self):
        good, bs = self.good, self.bs
        readers = [('windows', T.win_record(good, bs)),
                   ('gdisk', pm.convert_sgdisk(T.sgdisk_text(good, bs, scan=True), 'gdisk')),
                   ('q1n1', T.survey_of(good, bs)),
                   ('sgdisk', pm.convert_sgdisk(T.sgdisk_text(good, bs), 'sgdisk'))]
        problems, _, verdict = self.verify(readers)
        readers = verdict['readers']
        self.assertEqual(problems, [])
        self.assertEqual(readers['mbr_checked'], ['gdisk'])
        self.assertEqual(readers['mbr_unchecked'], ['windows', 'q1n1', 'sgdisk'])

    def test_text_without_the_scan_block_leaves_every_reader_unchecked(self):
        problems, _, verdict = self.verify(self.documented_four())
        self.assertEqual(verdict['readers']['mbr_checked'], [])
        self.assertEqual(verdict['readers']['mbr_unchecked'], ['windows', 'mac-diskutil', 'q1n1', 'windows-boot'])

    def test_the_hybrid_and_incomplete_scan_refusals_still_hold(self):
        scan = T.sgdisk_text(self.good, self.bs, scan=True)
        for text, fragment in ((scan.replace('MBR: protective', 'MBR: hybrid'), 'only a protective MBR'),
                               (scan.replace('  MBR: protective\n', ''), 'block is incomplete or repeated')):
            with self.subTest(fragment=fragment):
                with self.assertRaisesRegex(pm.PlanError, fragment):
                    pm.convert_sgdisk(text, 'gdisk')


class Hygiene(unittest.TestCase):
    def test_spdx_and_sizes(self):
        for name in ('test-snapmac-partition-plan-hardening.py',):
            path = TOOLS / name
            text = path.read_text()
            self.assertIn('SPDX-License-Identifier: MIT', '\n'.join(text.splitlines()[:3]), name)
            self.assertLess(len(text.splitlines()), 1500, name)
            self.assertLess(path.stat().st_size, 50 * 1024, name)


if __name__ == '__main__':
    unittest.main()
