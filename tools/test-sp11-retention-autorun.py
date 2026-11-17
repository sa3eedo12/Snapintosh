#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of the retention extract step of the SP11 stick autorun (tools/sp11_autorun.py, `autorun --stick-first-authorised` of
tools/stage-sp11-boot-media.py): the plan model, the Shell script and its line checker, PLAN.TXT, and staged trees. No hardware, no emulator,
nothing under /Volumes or /dev: every tree is a scratch directory below build/.

    python3 tools/test-sp11-retention-autorun.py

  * PlanModel: the plan `retention-m10` is the payload runner's step and then the extract step, and nothing else; every refusal of the plan rules
    (an extract in another plan, the wrong shape, an unattended plan without the operator's flag, the flag on another plan, a malformed or
    unaligned or repeated page); PLAN.TXT round trip, canonical form and tampering; the reserved result names; old plans unchanged byte for byte
    (their digests were taken from the committed module);
  * ScriptLines: the exact lines of the extract block, that nothing but the dumps stands between its guards and the memory map, and that every
    slipped script (a command before the dump, the marker before the dump, no guard against overwriting, a UCS-2 redirect, a repeated or
    unaligned page, a missing end line or marker check, a dump in an ordinary step ...) is rejected by the line checker;
  * RetentionTree: the tree the stager writes for each plan, its listing, `verify`, `--clear` and `--remove`, the unattended plan refused
    without the flag and recorded with it, and a PLAN.TXT edited to drop the record.

The shared fixtures are those of tools/test-sp11-autorun-payload-tree.py, loaded through it so that the mutation runner's copies are the ones
under test (SP11_AUTORUN_DIR, SP11_STAGE_DIR; SP11_RETENTION_DIR for the retention modules)."""
from datetime import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

TOOLS = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(Path(os.environ.get('SP11_RETENTION_DIR') or TOOLS)))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


PT = load('autorun_payload_tree_tests_for_retention', TOOLS / 'test-sp11-autorun-payload-tree.py')
autorun, fx, RUNNER, SLBOUNCE, STEP = PT.autorun, PT.fx, PT.RUNNER, PT.SLBOUNCE, PT.STEP
import sp11_retention as R  # noqa: E402
import sp11_retention_check as C  # noqa: E402

PAGES = (0x9C000000, 0xF8400000, 0xFFFF00000)
EXTRACT = 'RETENTION-EXTRACT 0x9C000000 0xF8400000 0xFFFF00000'
HASHES = {SLBOUNCE: 'a' * 64, RUNNER: '8' * 64}
FILES = {'RETPROBE.BIN': b'\x5f\x24\x03\xd5' + bytes(range(256)) * 2, 'RETPATH.DAT': R.config_bytes(1, 8, PAGES)}
sha = lambda blob: hashlib.sha256(blob).hexdigest()  # noqa: E731
CRLF = '\r\n'
# The script and PLAN.TXT of two plans that predate the extract step, as the committed module wrote them.
GOLDEN_OLD = {
    'two-step': ('0da19c450c8380107b53257af09440a1546319dcb3e5d9325f522c8c9cc3b291', '7a2b595417ed65b0f141e92979268a8b78109cc368b93109c01cffa61f9d7705'),
    'payload': ('9c7da11841496d6c8d141c9160b2ff43fadf88cd04cdbf4c15f419a084b7b8d5', 'd5544cdb2fa3866a6d019827f3f2a1cee1f7f838026f66753be2d51ce1f76816'),
}
EXTRACT_BLOCK = [
    ':step2', 'if exist SP11\\DONE2.TXT then', '  goto done', 'endif',
    'if exist SP11\\RET2.TXT then', '  echo "AUTORUN REFUSED: SP11\\RET2.TXT exists and is never overwritten, nothing was run"', '  goto end', 'endif',
    'dmem 0x9C000000 0x1000 >a SP11\\RET2.TXT', 'dmem 0xF8400000 0x1000 >>a SP11\\RET2.TXT', 'dmem 0xFFFF00000 0x1000 >>a SP11\\RET2.TXT',
    'memmap >>a SP11\\RET2.TXT', 'date >>a SP11\\RET2.TXT', 'time >>a SP11\\RET2.TXT',
    'echo "RETENTION-EXTRACT-END plan=retention-m10 step=2 pages=3" >>a SP11\\RET2.TXT',
    'echo "AUTORUN retention-m10 STEP 2 EXTRACTED" >a SP11\\DONE2.TXT',
    'if not exist SP11\\DONE2.TXT then', '  echo "AUTORUN REFUSED: cannot write SP11\\DONE2.TXT, nothing was run"', '  goto end', 'endif',
    'echo "AUTORUN retention-m10 STEP 2 OF 2: SP11\\RET2.TXT is written, power off and take the stick to the Mac"', 'goto end',
]


def reason_of(call, *args, **kw):
    try:
        call(*args, **kw)
    except autorun.PlanError as error:
        return error.reason
    raise AssertionError('no PlanError')


def plan(name='retention-m10', steps=(STEP, EXTRACT), flag=False, data=True):
    built = autorun.build_plan(name, list(steps), dict(HASHES), flag)
    if not data:
        return built
    staged = [autorun.DataFile(autorun.MANIFEST_NAME, 400, 'd' * 64)] + [autorun.DataFile(n, len(b), sha(b)) for n, b in FILES.items()]
    return autorun.attach_data(built, staged)


class PlanModel(unittest.TestCase):
    def test_the_plan_is_the_runner_step_and_the_extract_step(self):
        built = plan(data=False)
        self.assertEqual([type(step).__name__ for step in built.steps], ['Step', 'ExtractStep'])
        extract = built.steps[1]
        self.assertEqual((extract.pages, extract.run_line, extract.slbounce, extract.app), (PAGES, EXTRACT, '', ''))
        self.assertEqual(built.files(), {SLBOUNCE: 'a' * 64, RUNNER: '8' * 64}, 'the extract step has no file of its own')
        self.assertFalse(built.stick_first_authorised)
        auto = plan('retention-m10-auto', flag=True, data=False)
        self.assertTrue(auto.stick_first_authorised)
        self.assertEqual(auto.steps, built.steps, 'the unattended plan has the same steps')

    def test_every_rule_of_the_plan_is_enforced(self):
        refusals = [
            ('extract_outside_retention_plan', 'payload-a', (STEP, EXTRACT), False),
            ('extract_outside_retention_plan', 'devhe-mark', (EXTRACT,), False),
            ('retention_plan_shape', 'retention-m10', (EXTRACT,), False),
            ('retention_plan_shape', 'retention-m10', (STEP,), False),
            ('retention_plan_shape', 'retention-m10', (EXTRACT, STEP), False),
            ('retention_plan_shape', 'retention-m10', (STEP, EXTRACT, EXTRACT), False),
            ('retention_plan_shape', 'retention-m10', (f'{SLBOUNCE} Q1N1.EFI --el2', EXTRACT), False),
            ('unattended_not_authorised', 'retention-m10-auto', (STEP, EXTRACT), False),
            ('stick_first_flag_not_for_this_plan', 'retention-m10', (STEP, EXTRACT), True),
            ('stick_first_flag_not_for_this_plan', 'payload-a', (STEP,), True),
            ('extract_malformed', 'retention-m10', (STEP, 'RETENTION-EXTRACT 0x9C000000 0xF8400000'), False),
            ('extract_malformed', 'retention-m10', (STEP, EXTRACT + ' 0x10000000'), False),
            ('extract_page_malformed', 'retention-m10', (STEP, 'RETENTION-EXTRACT 0x9c000000 0xF8400000 0xFFFF00000'), False),
            ('extract_page_malformed', 'retention-m10', (STEP, 'RETENTION-EXTRACT 0x09C000000 0xF8400000 0xFFFF00000'), False),
            ('extract_page_malformed', 'retention-m10', (STEP, 'RETENTION-EXTRACT 9C000000 0xF8400000 0xFFFF00000'), False),
            ('extract_page_malformed', 'retention-m10', (STEP, 'RETENTION-EXTRACT 0x9C000000 0xF8400000 0x1' + '0' * 12), False),
            ('extract_page_unaligned', 'retention-m10', (STEP, 'RETENTION-EXTRACT 0x9C000800 0xF8400000 0xFFFF00000'), False),
            ('extract_pages_not_distinct', 'retention-m10', (STEP, 'RETENTION-EXTRACT 0x9C000000 0xF8400000 0x9C000000'), False),
        ]
        for reason, name, steps, flag in refusals:
            with self.subTest(reason=reason, name=name, steps=steps, flag=flag):
                self.assertEqual(reason_of(autorun.build_plan, name, list(steps), dict(HASHES), flag), reason)

    def test_the_hashes_of_an_extract_plan_are_those_of_its_runner_step_alone(self):
        self.assertEqual(reason_of(autorun.build_plan, 'retention-m10', [STEP, EXTRACT], {SLBOUNCE: 'a' * 64}), 'file_hash_missing')
        self.assertEqual(reason_of(autorun.build_plan, 'retention-m10', [STEP, EXTRACT], {**HASHES, 'Q1N1.EFI': 'c' * 64}), 'file_unused')

    def test_result_files_are_reserved_names_for_data(self):
        built = plan(data=False)
        for name in ('RET2.TXT', 'RET1.TXT', 'RET12.TXT'):
            files = [autorun.DataFile(autorun.MANIFEST_NAME, 400, 'd' * 64), autorun.DataFile(name, 10, 'e' * 64)]
            with self.subTest(name):
                self.assertEqual(reason_of(autorun.attach_data, built, files), 'data_name_collides')

    def test_plan_text_round_trips_in_one_canonical_form_for_both_plans(self):
        for name, flag in (('retention-m10', False), ('retention-m10-auto', True)):
            built = plan(name, flag=flag)
            text = autorun.render_plan_text(built)
            self.assertEqual(autorun.parse_plan_text(text), built, name)
            body = text.decode('ascii')
            self.assertIn(f'step 2{CRLF}  extract  {EXTRACT}{CRLF}  marker  SP11\\DONE2.TXT, written after the dump, once it is complete{CRLF}'
                          f'  result  SP11\\RET2.TXT, never overwritten{CRLF}', body)
            self.assertEqual(autorun.UNATTENDED_LINE in body, flag)
            self.assertEqual(body.count('written before the step runs'), 1)

    def test_a_retention_plan_stages_exactly_its_three_data_files(self):
        built = plan(data=False)
        manifest = autorun.DataFile(autorun.MANIFEST_NAME, 400, 'd' * 64)
        probe, config = (autorun.DataFile(name, len(blob), sha(blob)) for name, blob in FILES.items())
        extra = autorun.DataFile('OTHER.BIN', 32, 'f' * 64)
        for what, files in (('an extra OTHER.BIN, which the runner would call', [manifest, probe, config, extra]),
                            ('an extra data file', [manifest, probe, config, autorun.DataFile('SPARE.DAT', 32, 'f' * 64)]),
                            ('no configuration', [manifest, probe]), ('no payload', [manifest, config]),
                            ('another name for the payload', [manifest, autorun.DataFile('PROBE.BIN', probe.size, probe.sha256), config])):
            with self.subTest(what):
                self.assertEqual(reason_of(autorun.attach_data, built, files), 'retention_data_names')
        for name, flag in (('retention-m10', False), ('retention-m10-auto', True)):
            self.assertEqual(len(autorun.attach_data(plan(name, flag=flag, data=False), [manifest, probe, config]).data), 3, name)
        other = autorun.build_plan('payload-a', [STEP], dict(HASHES))
        self.assertEqual(len(autorun.attach_data(other, [manifest, extra]).data), 2, 'the plans that are not retention plans keep their freedom of names')

    def test_plan_text_records_the_staging_time_of_a_retention_plan_and_nobody_elses(self):
        when = datetime(2026, 11, 17, 5, 20, 33)
        for name, flag in (('retention-m10', False), ('retention-m10-auto', True)):
            stamped = autorun.stamp_staging(plan(name, flag=flag), when)
            self.assertEqual(stamped.staged, '2026-11-17 05:20:33')
            text = autorun.render_plan_text(stamped)
            self.assertIn(f'steps: 2{CRLF}' + (autorun.UNATTENDED_LINE + CRLF if flag else '') + f'staged: 2026-11-17 05:20:33{CRLF}{CRLF}', text.decode('ascii'))
            self.assertEqual(autorun.parse_plan_text(text), stamped)
            self.assertEqual(autorun.parse_plan_text(autorun.render_plan_text(plan(name, flag=flag))).staged, '', 'a PLAN.TXT that records no time is still read')
        other = plan('payload-a', steps=(STEP,))
        self.assertIs(autorun.stamp_staging(other, when), other)
        self.assertNotIn(b'\r\nstaged: ', autorun.render_plan_text(other))

    def test_a_staged_line_that_is_no_time_not_the_tools_spelling_or_on_another_plan_is_refused(self):
        text = autorun.render_plan_text(autorun.stamp_staging(plan(), datetime(2026, 11, 17, 5, 20, 33))).decode('ascii')
        for new, reason in (('staged: 2026-13-45 05:20:33', 'plan_text_unparseable'), ('staged: soon', 'plan_text_unparseable'),
                            ('staged: 2026-1-7 5:20:33', 'plan_text_not_canonical')):
            with self.subTest(new):
                self.assertEqual(reason_of(autorun.parse_plan_text, text.replace('staged: 2026-11-17 05:20:33', new).encode('ascii')), reason)
        classic = autorun.render_plan_text(plan('payload-a', steps=(STEP,))).decode('ascii')
        with_line = classic.replace(f'steps: 1{CRLF}', f'steps: 1{CRLF}staged: 2026-11-17 05:20:33{CRLF}')
        self.assertNotEqual(with_line, classic)
        self.assertEqual(reason_of(autorun.parse_plan_text, with_line.encode('ascii')), 'plan_text_not_canonical')

    def test_a_plan_text_that_was_edited_is_refused(self):
        text = autorun.render_plan_text(plan('retention-m10-auto', flag=True)).decode('ascii')
        without = text.replace(autorun.UNATTENDED_LINE + CRLF, '')
        self.assertEqual(reason_of(autorun.parse_plan_text, without.encode('ascii')), 'unattended_not_authorised')
        baseline = autorun.render_plan_text(plan()).decode('ascii')
        with_line = baseline.replace(f'steps: 2{CRLF}', f'steps: 2{CRLF}{autorun.UNATTENDED_LINE}{CRLF}')
        self.assertEqual(reason_of(autorun.parse_plan_text, with_line.encode('ascii')), 'stick_first_flag_not_for_this_plan')
        for old, new in (('0xF8400000', '0xF8400001'), ('step 2\r\n  extract', 'step 3\r\n  extract'), ('SP11\\RET2.TXT, never', 'SP11\\RET3.TXT, never'),
                         ('written after the dump, once it is complete', 'written before the step runs')):
            with self.subTest(old=old):
                self.assertTrue(old in baseline)
                with self.assertRaises(autorun.PlanError):
                    autorun.parse_plan_text(baseline.replace(old, new).encode('ascii'))

    def test_plans_that_predate_the_extract_step_are_written_byte_for_byte_as_before(self):
        classic = {'SLBOUNCE-DEVHE-MARK.EFI': 'a' * 64, 'SLBOUNCE-DEVHE.EFI': 'b' * 64, 'Q1N1.EFI': 'c' * 64}
        two = autorun.build_plan('devhe-mark', ['SLBOUNCE-DEVHE-MARK.EFI Q1N1.EFI --el2', 'SLBOUNCE-DEVHE.EFI Q1N1.EFI --el2'], classic)
        one = autorun.attach_data(autorun.build_plan('payload-a', [STEP], dict(HASHES)),
                                  [autorun.DataFile('PAYLOAD.MAN', 400, 'd' * 64), autorun.DataFile('STATE.BIN', 1024, 'e' * 64)])
        for name, built in (('two-step', two), ('payload', one)):
            with self.subTest(name):
                self.assertEqual((sha(autorun.render_script(built)), sha(autorun.render_plan_text(built))), GOLDEN_OLD[name])
                self.assertEqual(autorun.parse_plan_text(autorun.render_plan_text(built)), built)
                self.assertNotIn(b'extract', autorun.render_plan_text(built))
                self.assertNotIn(b'unattended', autorun.render_plan_text(built))


class ScriptLines(unittest.TestCase):
    def setUp(self):
        self.lines = autorun.script_lines(plan())
        self.first = next(i for i, line in enumerate(self.lines) if line.startswith('dmem '))

    def test_the_extract_block_is_exactly_these_lines(self):
        start = self.lines.index(':step2')
        self.assertEqual(self.lines[start:self.lines.index(':done')], EXTRACT_BLOCK)
        self.assertEqual(autorun.script_problems(CRLF.join(self.lines) + CRLF), [])
        self.assertEqual(autorun.render_script(plan()).decode('ascii'), CRLF.join(self.lines) + CRLF)

    def test_nothing_but_the_guards_stands_between_the_start_of_the_block_and_the_dumps(self):
        block = self.lines[self.lines.index(':step2'):self.first]
        for line in block:
            self.assertTrue(line.strip().startswith(('if ', 'goto ', 'endif', 'echo "AUTORUN REFUSED', ':step2')), line)
        self.assertFalse(any(('stall' in line or 'load ' in line or '>a' in line or 'Q1N1' in line) for line in block))
        self.assertEqual([line.split()[0] for line in self.lines[self.first:self.first + 3]], ['dmem'] * 3)
        before = '\n'.join(self.lines[:self.first])
        self.assertEqual(before.count('stall'), 1, 'the one stall is the pause of step 1, which comes before step 2 exists')

    def test_the_first_dump_starts_the_file_and_the_others_append_to_it(self):
        dumps = [line for line in self.lines if line.startswith('dmem ')]
        self.assertEqual([line.split()[3] for line in dumps], ['>a', '>>a', '>>a'])
        self.assertEqual({line.split()[4] for line in dumps}, {'SP11\\RET2.TXT'})
        self.assertEqual({line.split()[2] for line in dumps}, {'0x1000'})

    def test_every_slipped_script_is_rejected_by_the_line_checker(self):
        i = self.first
        slips = {
            'a stall before the first dump': lambda L: L[:i] + ['stall 5000000'] + L[i:],
            'a load before the first dump': lambda L: L[:i] + ['load SP11\\SLBOUNCE-DEVHE-MARK.EFI'] + L[i:],
            'the application run before the first dump': lambda L: L[:i] + ['SP11\\Q1N1-PAYLOAD.EFI --el2'] + L[i:],
            'the marker written before the first dump': lambda L: L[:i] + ['echo "AUTORUN retention-m10 STEP 2 EXTRACTED" >a SP11\\DONE2.TXT'] + L[i:],
            'a console line before the first dump': lambda L: L[:i] + ['echo "extracting"'] + L[i:],
            'a del': lambda L: L[:i] + ['del SP11\\DONE1.TXT'] + L[i:],
            'a stall between two dumps': lambda L: L[:i + 1] + ['stall 5000000'] + L[i + 1:],
            'the guard against overwriting is gone': lambda L: L[:i - 4] + L[i:],
            'the guard does not stop the script': lambda L: [line if line != '  goto end' or n != i - 2 else '  goto step2' for n, line in enumerate(L)],
            'the first dump is a UCS-2 redirect': lambda L: L[:i] + [L[i].replace(' >a ', ' > ')] + L[i + 1:],
            'every dump overwrites the file': lambda L: [line.replace('>>a', '>a') for line in L],
            'no dump starts the file': lambda L: L[:i] + [L[i].replace('>a', '>>a')] + L[i + 1:],
            'a dump of two pages': lambda L: L[:i + 1] + [L[i + 1].replace('0x1000', '0x2000')] + L[i + 2:],
            'the same page twice': lambda L: L[:i + 2] + [L[i + 1]] + L[i + 3:],
            'an unaligned page': lambda L: L[:i] + [L[i].replace('0x9C000000', '0x9C000800')] + L[i + 1:],
            'another result file': lambda L: L[:i + 1] + [L[i + 1].replace('RET2.TXT', 'RET3.TXT')] + L[i + 2:],
            'only two dumps': lambda L: L[:i + 2] + L[i + 3:],
            'four dumps': lambda L: L[:i + 3] + ['dmem 0xF8500000 0x1000 >>a SP11\\RET2.TXT'] + L[i + 3:],
            'the memory map is not appended': lambda L: L[:i + 3] + [L[i + 3].replace('>>a', '>a')] + L[i + 4:],
            'the memory map is gone': lambda L: L[:i + 3] + L[i + 4:],
            'the memory map comes first': lambda L: L[:i] + [L[i + 3]] + L[i:i + 3] + L[i + 4:],
            'the date is gone': lambda L: L[:i + 4] + L[i + 5:],
            'the time is gone': lambda L: L[:i + 5] + L[i + 6:],
            'the time comes before the date': lambda L: L[:i + 4] + [L[i + 5], L[i + 4]] + L[i + 6:],
            'the date overwrites the file': lambda L: L[:i + 4] + [L[i + 4].replace('>>a', '>a')] + L[i + 5:],
            'the time is a UCS-2 redirect': lambda L: L[:i + 5] + [L[i + 5].replace(' >>a ', ' > ')] + L[i + 6:],
            'the date goes to another result file': lambda L: L[:i + 4] + [L[i + 4].replace('RET2.TXT', 'RET3.TXT')] + L[i + 5:],
            'a clock command comes before the dumps': lambda L: L[:i] + [L[i + 4]] + L[i:i + 4] + L[i + 5:],
            'the clock comes after the end line': lambda L: L[:i + 4] + [L[i + 6]] + L[i + 4:i + 6] + L[i + 7:],
            'the end line is gone': lambda L: L[:i + 6] + L[i + 7:],
            'the end line names another step': lambda L: L[:i + 6] + [L[i + 6].replace('step=2', 'step=1')] + L[i + 7:],
            'the end line comes after the marker': lambda L: L[:i + 6] + [L[i + 7], L[i + 6]] + L[i + 8:],
            'the marker is written before the end line': lambda L: L[:i + 3] + [L[i + 7]] + L[i + 3:i + 7] + L[i + 8:],
            'the marker is gone': lambda L: L[:i + 7] + L[i + 8:],
            'the marker of another step': lambda L: L[:i + 7] + [L[i + 7].replace('DONE2', 'DONE1')] + L[i + 8:],
            'the marker check is gone': lambda L: L[:i + 8] + L[i + 12:],
            'the marker check tests another file': lambda L: L[:i + 8] + [L[i + 8].replace('DONE2', 'DONE1')] + L[i + 9:],
            'the marker check is a positive test': lambda L: L[:i + 8] + [L[i + 8].replace('if not exist', 'if exist')] + L[i + 9:],
            'the marker check does not leave the script': lambda L: L[:i + 10] + ['  goto step1'] + L[i + 11:],
            'the extraction ends by going to the done message': lambda L: L[:L.index(':done') - 1] + ['goto done'] + L[L.index(':done'):],
            'the script runs on after the extraction': lambda L: L[:L.index(':done') - 1] + L[L.index(':done'):],
        }
        for name, slip in slips.items():
            with self.subTest(name):
                slipped = slip(list(self.lines))
                self.assertNotEqual(slipped, self.lines)
                self.assertNotEqual(autorun.script_problems(CRLF.join(slipped) + CRLF), [], 'the line checker accepted it')

    def test_a_slipped_script_cannot_come_out_of_the_generator(self):
        slipped = self.lines[:self.first] + ['stall 5000000'] + self.lines[self.first:]
        with mock.patch.object(autorun, 'script_lines', return_value=slipped):
            self.assertEqual(reason_of(autorun.render_script, plan()), 'script_rejected')

    def test_a_dump_in_an_ordinary_step_and_a_step_block_without_dumps_are_rejected(self):
        ordinary = autorun.script_lines(plan('payload-a', steps=(STEP,)))
        at = ordinary.index('@echo -on')
        slipped = ordinary[:at] + ['dmem 0x9C000000 0x1000 >a SP11\\RET1.TXT'] + ordinary[at:]
        self.assertNotEqual(autorun.script_problems(CRLF.join(slipped) + CRLF), [])
        outside = self.lines[:6] + ['dmem 0x9C000000 0x1000 >a SP11\\RET1.TXT'] + self.lines[6:]
        self.assertTrue(any('outside any step' in p for p in autorun.script_problems(CRLF.join(outside) + CRLF)))
        start = self.lines.index(':step2')
        hollow = self.lines[:start + 8] + self.lines[start + 8 + 3:]       # the three dumps removed from the block
        self.assertNotEqual(autorun.script_problems(CRLF.join(hollow) + CRLF), [])

    def test_the_unattended_plan_has_the_same_script_but_for_its_name(self):
        auto = autorun.script_lines(plan('retention-m10-auto', flag=True))
        base = autorun.script_lines(plan())
        self.assertEqual([line.replace('retention-m10-auto', 'retention-m10') for line in auto], base)


class RetentionTree(PT.PayloadTree):
    def stage_argv(self, name='retention-m10', flag=False, steps=(STEP, EXTRACT), files=FILES):
        argv = self.data_argv(files, steps=steps, name=name)
        return argv + (['--stick-first-authorised'] if flag else [])

    def test_the_baseline_plan_is_staged_listed_verified_cleared_and_removed(self):
        tree = self.default_tree()
        base = self.snapshot(tree)
        code, result = self.run_main(self.stage_argv())
        self.assertEqual((code, result['status'], result['plan']), (0, 'AUTORUN_STAGED', 'retention-m10'), result)
        self.assertEqual([s['step'] for s in result['steps']], [1, 2])
        self.assertEqual(result['steps'][1], {'step': 2, 'load': '', 'run': EXTRACT})
        self.assertEqual(sorted(result['data']), ['PAYLOAD.MAN', 'RETPATH.DAT', 'RETPROBE.BIN'])
        script = (tree / 'EFI' / 'BOOT' / 'startup.nsh').read_bytes().decode('ascii').split(CRLF)[:-1]
        self.assertEqual(script[script.index(':step2'):script.index(':done')], EXTRACT_BLOCK)
        staged = (tree / 'SP11' / 'PLAN.TXT').read_bytes()
        self.assertEqual(autorun.render_plan_text(autorun.parse_plan_text(staged)), staged)
        code, verified = self.verify(tree)
        self.assertEqual((code, verified['status'], verified['autorun']['problems']), (0, 'COMPLETE', []), verified)
        self.assertEqual(verified['autorun']['steps'], 2)
        self.assertEqual(sorted(p.name for p in (tree / 'SP11').iterdir() if p.name.startswith('RET')), ['RETPATH.DAT', 'RETPROBE.BIN'], 'no result file is staged')
        (tree / 'SP11' / 'DONE1.TXT').write_bytes(b'x')
        code, cleared = self.run_main(['autorun', '--dest', tree, '--no-pins', '--clear'])
        self.assertEqual((code, cleared['markers_removed']), (0, ['SP11/DONE1.TXT']))
        code, removed = self.run_main(['autorun', '--dest', tree, '--no-pins', '--remove'])
        self.assertEqual((code, removed['status']), (0, 'AUTORUN_REMOVED'), removed)
        self.assertEqual(self.snapshot(tree).keys(), base.keys(), 'remove leaves the base tree: no startup.nsh, no plan, no data')

    def test_the_unattended_plan_is_refused_without_the_flag_and_recorded_with_it(self):
        tree = self.default_tree()
        self.refused(self.stage_argv('retention-m10-auto'), 'unattended_not_authorised')
        code, result = self.run_main(self.stage_argv('retention-m10-auto', flag=True))
        self.assertEqual((code, result['status'], result['plan']), (0, 'AUTORUN_STAGED', 'retention-m10-auto'), result)
        plan_text = (tree / 'SP11' / 'PLAN.TXT').read_bytes().decode('ascii')
        self.assertIn(autorun.UNATTENDED_LINE + CRLF, plan_text)
        code, verified = self.verify(tree)
        self.assertEqual((code, verified['status']), (0, 'COMPLETE'), verified)
        (tree / 'SP11' / 'PLAN.TXT').write_bytes(plan_text.replace(autorun.UNATTENDED_LINE + CRLF, '').encode('ascii'))
        self.regenerate_manifest(tree)
        code, tampered = self.verify(tree)
        self.assertEqual((code, tampered['status']), (1, 'FAIL'), tampered)
        self.assertIn('unattended_not_authorised', json.dumps(tampered['autorun']['problems']))

    def test_the_other_refusals_leave_the_tree_as_it_was(self):
        self.default_tree()
        self.refused(self.stage_argv(flag=True), 'stick_first_flag_not_for_this_plan')
        self.refused(self.stage_argv('payload-a'), 'extract_outside_retention_plan')
        self.refused(self.stage_argv(steps=(STEP,)), 'retention_plan_shape')
        self.refused(self.stage_argv(steps=(STEP, 'RETENTION-EXTRACT 0x9C000000 0xF8400000 0x9C000000')), 'extract_pages_not_distinct')
        self.refused(self.stage_argv(files={**FILES, 'RET2.TXT': b'a result'}), 'data_name_collides')

    def test_a_file_the_runner_would_call_beside_the_probe_is_refused_by_the_stager_and_nothing_is_staged(self):
        self.default_tree()
        self.refused(self.stage_argv(files={**FILES, 'OTHER.BIN': b'\x1f\x20\x03\xd5' * 8}), 'retention_data_names')
        self.refused(self.stage_argv('retention-m10-auto', flag=True, files={**FILES, 'OTHER.BIN': b'\x1f\x20\x03\xd5' * 8}), 'retention_data_names')
        self.refused(self.stage_argv(files={'RETPROBE.BIN': FILES['RETPROBE.BIN']}), 'retention_data_names')
        self.refused(self.stage_argv(files={'PROBE.BIN': FILES['RETPROBE.BIN'], 'RETPATH.DAT': FILES['RETPATH.DAT']}), 'retention_data_names')

    def test_the_stager_records_when_it_staged_and_the_verdict_tool_reads_that_line(self):
        tree = self.default_tree()
        before = datetime.now().replace(microsecond=0)
        code, result = self.run_main(self.stage_argv())
        after = datetime.now()
        self.assertEqual((code, result['status']), (0, 'AUTORUN_STAGED'), result)
        staged = autorun.parse_plan_text((tree / 'SP11' / 'PLAN.TXT').read_bytes()).staged
        self.assertTrue(before <= datetime.strptime(staged, autorun.STAGED_FORMAT) <= after, staged)
        self.assertEqual(C._staging_time(tree / 'SP11' / 'PLAN.TXT'), datetime.strptime(staged, autorun.STAGED_FORMAT), 'the line the tool reads is the one written')
        code, verified = self.verify(tree)
        self.assertEqual((code, verified['status']), (0, 'COMPLETE'), verified)

    def test_a_tree_whose_script_was_altered_is_not_accepted(self):
        tree = self.default_tree()
        code, _ = self.run_main(self.stage_argv())
        self.assertEqual(code, 0)
        script = tree / 'EFI' / 'BOOT' / 'startup.nsh'
        original = script.read_bytes()
        script.write_bytes(original.replace(b'dmem 0x9C000000 0x1000 >a', b'stall 5000000\r\ndmem 0x9C000000 0x1000 >a'))
        self.regenerate_manifest(tree)
        code, result = self.verify(tree)
        self.assertEqual((code, result['status']), (1, 'FAIL'), result)
        script.write_bytes(original.replace(b'dmem 0xF8400000', b'dmem 0xF8400800'))
        self.regenerate_manifest(tree)
        self.assertEqual(self.verify(tree)[1]['status'], 'FAIL')


if __name__ == '__main__':
    unittest.main()
