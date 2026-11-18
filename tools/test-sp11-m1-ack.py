#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of the builders around the opt-in CPU-start acknowledge of the SP11 M1 monitor (docs/SP11-MONITOR-PROFILE.md section 10), the tool side of
tools/test-cpu-start-ack.py:

  python3 tools/test-sp11-m1-ack.py

ProfileTool: tools/sp11_m1_profile.py --cpu-start (stop is the default and its record, flags and monitor are what they were; ack adds one define before the NV2 header
define and one key to the record). WrapperBuild: tools/sp11_stick_wrapper_build.py --cpu-start (the ack pair has files, a report and a pinned monitor of its own; the
reporter is byte for byte the plain one because nothing in it reads the gate yet; a profile build of the other choice is CPU_START_MISMATCH whichever way round, and so
is every record whose key and flags disagree). BundleBinding: tools/sp11_stick_bundle_m1.py (a record is for one choice and a report of the other is M1_REPORT_MISMATCH; the
facts of an ack record name the ack image's digest; no record is written for ack until the text of the record can say what it is) and the builder's option. Every refusal is
shown to fail on a seeded input. The builds need clang, ld.lld, the llvm tools and a q1n1 checkout ($Q1N1_SP11_TREE) and SKIP without them: a skip is never a pass. The monitors
are built from a LOCAL TEST RECORD of the NV2 gate (synthetic: it names no unit) below build/sp11-m1-ack-test-scratch/, deleted at the end. No hardware; EXPERIMENTAL."""
import argparse
import concurrent.futures
import contextlib
import io
import json
import re
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_wrapper_build as wb  # noqa: E402
import sp11_stick_wrapper_testlib as T  # noqa: E402

ROOT, TOOLS = T.ROOT, T.TOOLS
SCRATCH = ROOT / 'build' / 'sp11-m1-ack-test-scratch'
LLVM = ('clang', 'ld.lld', 'llvm-objcopy', 'llvm-readelf', 'llvm-nm', 'llvm-objdump')
STATE = {}
ACK_DEFINE = '-DSNAP_SP11_CPU_START_ACK=1'


def ready():
    return bool(STATE.get('tree') and all(T.find_tool(t) for t in LLVM))


def quietly(function, *args, **kwargs):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = function(*args, **kwargs)
        except SystemExit as stop:
            code = stop.code
    return code, out.getvalue(), err.getvalue()


def setUpModule():
    shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True)
    STATE['tree'] = T.q1n1_tree()
    if not ready():
        return
    nv2 = T.load('nv2_record', TOOLS / 'nv2_record.py')
    profile = T.load('sp11_m1_profile', TOOLS / 'sp11_m1_profile.py')
    record = nv2.Record(soc=nv2.SOC['x1e80100'], midr_el1=0xFEED4B01, revidr_el1=0, id_aa64mmfr2_el1=0x02001234, id_aa64mmfr4_el1=0,
                        vncr_redirect=nv2.RESULT['pass'], negative_control_trapped=nv2.RESULT['pass'], evidence=nv2.EVIDENCE['OBSERVED'],
                        tool='wi4b-test-only', build_id=0x4B01)
    header = SCRATCH / 'test-record.h'
    text = nv2.emit_header(nv2.pack(record))
    T.write(header, text.encode() if isinstance(text, str) else text)
    STATE.update(profile_tool=profile, header=header)
    for choice in ('stop', 'ack'):
        code, out, err = quietly(profile.main, ['build', '--nv2-record-header', str(header), '--cpu-start', choice, str(SCRATCH / f'profile-{choice}')])
        assert code == 0, (choice, out, err)
        STATE[f'profile-{choice}'] = SCRATCH / f'profile-{choice}'
    default = str(ROOT / 'config/monitor-profile-sp11-m1.json')
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        futures = {choice: pool.submit(wb.build_monitor, str(SCRATCH / f'pair-{choice}'), default, str(STATE[f'profile-{choice}'] / 'xnuboot.bin'),
                                       str(STATE[f'profile-{choice}'] / 'profile-build.json'), False, wb.MONITOR_OFFSET, choice) for choice in ('stop', 'ack')}
        for choice, future in futures.items():
            STATE[f'report-{choice}'] = future.result()
            STATE[f'pair-{choice}'] = SCRATCH / f'pair-{choice}'


def tearDownModule():
    shutil.rmtree(SCRATCH, ignore_errors=True)


def record_of(choice):
    return json.loads((STATE[f'profile-{choice}'] / 'profile-build.json').read_text())


def tampered(choice, name, change):
    """The path of a copy of the profile-build.json of `choice` that `change` (a function on the parsed record) altered, for a refusal that is raised before any file beside it is read."""
    record = record_of(choice)
    change(record)
    path = SCRATCH / 'tampered' / f'{name}.json'
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(record, indent=1))
    return path


@unittest.skipUnless(T.q1n1_tree() and all(T.find_tool(t) for t in LLVM), 'no clang, lld or q1n1 checkout')
class ProfileTool(unittest.TestCase):
    def test_stop_is_the_default_and_leaves_no_trace_in_its_record(self):
        stop, ack = record_of('stop'), record_of('ack')
        self.assertNotIn('cpu_start', stop)
        self.assertFalse([flag for flag in stop['flags'] if 'CPU_START' in flag])
        self.assertEqual(ack['cpu_start'], 'ack')
        self.assertEqual(ack['flags'], [*stop['flags'][:-1], ACK_DEFINE, stop['flags'][-1]])
        self.assertTrue(ack['flags'][-1].startswith('-DSNAP_NV2_RECORD_HEADER='), 'the NV2 header define stays last')
        code, out, err = quietly(STATE['profile_tool'].main, ['flags', '--nv2-record-header', str(STATE['header'])])
        self.assertEqual((code, json.loads(out)['flags']), (0, stop['flags']))

    def test_the_two_monitors_are_two_binaries_and_each_carries_the_record_once(self):
        stop, ack = (STATE[f'profile-{c}'] / 'xnuboot.bin' for c in ('stop', 'ack'))
        self.assertNotEqual(stop.read_bytes(), ack.read_bytes())
        self.assertGreater(len(ack.read_bytes()), len(stop.read_bytes()))
        self.assertEqual(record_of('ack')['monitor']['sha256'], T.sha(ack.read_bytes()))
        self.assertNotEqual(record_of('ack')['monitor']['sha256'], record_of('stop')['monitor']['sha256'])

    def test_the_option_takes_nothing_but_the_two_choices(self):
        code, out, err = quietly(STATE['profile_tool'].main, ['flags', '--nv2-record-header', str(STATE['header']), '--cpu-start', 'maybe'])
        self.assertEqual(code, 2)
        self.assertIn('invalid choice', err)
        with self.assertRaises(STATE['profile_tool'].Refused):
            STATE['profile_tool'].profile_flags({'defines': [], 'required_input': {'define': 'X'}}, [], mock.Mock(path=Path('/x')), 'maybe')

    def test_show_says_what_ack_is_and_the_default_says_nothing(self):
        code, out, err = quietly(STATE['profile_tool'].main, ['show', '--nv2-record-header', str(STATE['header']), '--cpu-start', 'ack'])
        flat = ' '.join(out.split())
        self.assertEqual(code, 0, err)
        for needle in ('cpu start ACK: EXPERIMENTAL diagnostic, not a model of the hardware', ACK_DEFINE):
            self.assertIn(needle, flat)
        code, out, err = quietly(STATE['profile_tool'].main, ['show', '--nv2-record-header', str(STATE['header'])])
        self.assertNotIn('cpu start', out)
        self.assertNotIn('CPU_START', out)


@unittest.skipUnless(T.q1n1_tree() and all(T.find_tool(t) for t in LLVM), 'no clang, lld or q1n1 checkout')
class WrapperBuild(unittest.TestCase):
    def test_the_ack_pair_has_files_a_report_and_a_pinned_monitor_of_its_own(self):
        pair, report = STATE['pair-ack'], STATE['report-ack']
        self.assertEqual(sorted(report['files']), ['SNAPMON-M1-ACK.BIN', 'SNAPREP-M1-ACK.BIN'])
        for name in ('SNAPMON-M1-ACK.BIN', 'SNAPREP-M1-ACK.BIN', 'wrapper-m1-ack.bin', 'snapmon-m1-ack.elf', 'snaprep-m1-ack.elf', 'build-report.json'):
            self.assertTrue((pair / name).is_file(), name)
        self.assertFalse((pair / 'SNAPMON-M1.BIN').exists())
        self.assertEqual(report['cpu_start'], 'ack')
        self.assertEqual(json.loads((pair / 'build-report.json').read_text()), json.loads(json.dumps(report)))
        self.assertTrue(report['variant'].startswith('m1:'))
        self.assertIn('EXPERIMENTAL', report['variant'])
        self.assertEqual(report['added'].count(ACK_DEFINE), 1)
        image, monitor = (pair / 'SNAPMON-M1-ACK.BIN').read_bytes(), (STATE['profile-ack'] / 'xnuboot.bin').read_bytes()
        offset = report['monitor']['offset']
        self.assertEqual(image[offset:], monitor)
        self.assertEqual(image[:report['wrapper']['bytes']], (pair / 'wrapper-m1-ack.bin').read_bytes())
        self.assertFalse(any(image[report['wrapper']['bytes']:offset]))
        self.assertEqual([p for p in report['added'] if p.startswith('-DSP11_MONITOR_')], wb.monitor_defines(monitor, offset), 'the wrapper is pinned to the ack monitor')
        self.assertNotEqual([p for p in report['added'] if p.startswith('-DSP11_MONITOR_')], wb.monitor_defines((STATE['profile-stop'] / 'xnuboot.bin').read_bytes(), offset))

    def test_the_stop_pair_is_what_it_was_and_the_reporter_is_the_same_bytes_in_both(self):
        report = STATE['report-stop']
        self.assertEqual(sorted(report['files']), ['SNAPMON-M1.BIN', 'SNAPREP-M1.BIN'])
        self.assertNotIn('cpu_start', report)
        self.assertFalse([p for p in report['added'] if 'CPU_START' in p])
        self.assertNotIn('EXPERIMENTAL', report['variant'])
        stop, ack = (STATE[f'pair-{c}'] for c in ('stop', 'ack'))
        self.assertEqual((stop / 'SNAPREP-M1.BIN').read_bytes(), (ack / 'SNAPREP-M1-ACK.BIN').read_bytes(), 'nothing in the reporter reads the gate yet')
        self.assertNotEqual((stop / 'SNAPMON-M1.BIN').read_bytes(), (ack / 'SNAPMON-M1-ACK.BIN').read_bytes())
        self.assertEqual(len((stop / 'wrapper-m1.bin').read_bytes()), len((ack / 'wrapper-m1-ack.bin').read_bytes()))

    def test_a_profile_build_of_the_other_choice_is_refused_whichever_way_round(self):
        default = str(ROOT / 'config/monitor-profile-sp11-m1.json')
        for given, build, name in (('stop', 'ack', 'stop build, ack asked'), ('ack', 'stop', 'ack build, stop asked')):
            with self.subTest(name):
                with self.assertRaises(wb.Refused) as caught:
                    wb.build_monitor(str(SCRATCH / f'refused-{given}'), default, str(STATE[f'profile-{given}'] / 'xnuboot.bin'),
                                     str(STATE[f'profile-{given}'] / 'profile-build.json'), False, wb.MONITOR_OFFSET, build)
                self.assertEqual(caught.exception.name, 'CPU_START_MISMATCH')
                self.assertFalse((SCRATCH / f'refused-{given}').exists(), 'nothing was built')

    def test_a_record_whose_key_and_flags_disagree_is_refused_for_either_choice(self):
        cases = (('ack', 'ack', 'no key', lambda r: r.pop('cpu_start')), ('ack', 'ack', 'no define', lambda r: r['flags'].remove(ACK_DEFINE)),
                 ('ack', 'ack', 'twice', lambda r: r['flags'].insert(0, ACK_DEFINE)), ('ack', 'ack', 'another value', lambda r: r['flags'].__setitem__(r['flags'].index(ACK_DEFINE), '-DSNAP_SP11_CPU_START_ACK=2')),
                 ('ack', 'ack', 'a bare define', lambda r: r['flags'].append('-DSNAP_SP11_CPU_START_ACK')), ('ack', 'ack', 'another key', lambda r: r.__setitem__('cpu_start', 'maybe')),
                 ('stop', 'stop', 'a define', lambda r: r['flags'].insert(-1, ACK_DEFINE)), ('stop', 'stop', 'a key', lambda r: r.__setitem__('cpu_start', 'ack')),
                 ('stop', 'stop', 'a define at 0', lambda r: r['flags'].insert(-1, '-DSNAP_SP11_CPU_START_ACK=0')))
        for choice, asked, name, change in cases:
            with self.subTest(f'{choice} record, {name}'):
                path = tampered(choice, name.replace(' ', '-') + choice, change)
                with self.assertRaises(wb.Refused) as caught:
                    wb.read_monitor(str(STATE[f'profile-{choice}'] / 'xnuboot.bin'), str(path), asked)
                self.assertEqual(caught.exception.name, 'CPU_START_MISMATCH')
        for choice in ('stop', 'ack'):       # the untouched records pass, so that every refusal above is the tamper's
            monitor, record, _ = wb.read_monitor(str(STATE[f'profile-{choice}'] / 'xnuboot.bin'), str(STATE[f'profile-{choice}'] / 'profile-build.json'), choice)
            self.assertEqual(len(monitor), record['monitor']['bytes'])

    def test_the_command_line_says_which_files_it_made_and_refuses_the_option_without_a_monitor(self):
        code, out, err = quietly(wb.main, ['--out', 'build/sp11-m1-ack-test-scratch/cli-mismatch', '--monitor', str(STATE['profile-ack'] / 'xnuboot.bin'),
                                           '--monitor-build', str(STATE['profile-ack'] / 'profile-build.json')])
        self.assertEqual(code, 1)
        self.assertIn('REFUSED=CPU_START_MISMATCH', out)
        code, out, err = quietly(wb.main, ['--out', 'build/sp11-m1-ack-test-scratch/cli-alone', '--cpu-start', 'ack'])
        self.assertEqual(code, 2)
        self.assertIn('belongs to the monitor entry variant', err)
        self.assertIn('CPU_START_MISMATCH', wb.MONITOR_REFUSALS)
        self.assertEqual(len(wb.MONITOR_REFUSALS), len(set(wb.MONITOR_REFUSALS)))


@unittest.skipUnless(T.q1n1_tree() and all(T.find_tool(t) for t in LLVM), 'no clang, lld or q1n1 checkout')
class BundleBinding(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m1 = T.load('sp11_stick_bundle_m1_for_ack', TOOLS / 'sp11_stick_bundle_m1.py')

    def facts(self, choice, report=None, image=None, reporter=None, asked=None):
        pair = STATE[f'pair-{choice}']
        names = ('SNAPMON-M1-ACK.BIN', 'SNAPREP-M1-ACK.BIN') if choice == 'ack' else ('SNAPMON-M1.BIN', 'SNAPREP-M1.BIN')
        data = report if report is not None else (pair / 'build-report.json').read_bytes()
        return self.m1.read_report(data, image or (pair / names[0]).read_bytes(), reporter or (pair / names[1]).read_bytes(), None, asked or choice)

    def test_a_record_names_the_digest_of_the_choice_it_is_for(self):
        stop, ack = self.facts('stop'), self.facts('ack')
        self.assertNotIn('cpu_start', stop)
        self.assertEqual(ack['cpu_start'], 'ack')
        self.assertEqual(ack['image']['sha256'], STATE['report-ack']['files']['SNAPMON-M1-ACK.BIN']['sha256'])
        self.assertEqual(stop['image']['sha256'], STATE['report-stop']['files']['SNAPMON-M1.BIN']['sha256'])
        self.assertNotEqual(ack['image']['sha256'], stop['image']['sha256'])
        self.assertNotEqual(ack['monitor']['sha256'], stop['monitor']['sha256'])

    def test_a_report_of_the_other_choice_is_never_a_record(self):
        for choice, asked in (('ack', 'stop'), ('stop', 'ack')):
            with self.subTest(f'a {choice} report, {asked} asked'):
                with self.assertRaises(self.m1.Refused) as caught:
                    self.facts(choice, asked=asked)
                self.assertEqual(caught.exception.name, 'M1_REPORT_MISMATCH')
        pair_ack, pair_stop = STATE['pair-ack'], STATE['pair-stop']
        with self.assertRaises(self.m1.Refused) as caught:       # the ack files with the plain report, and the other way round
            self.m1.read_report((pair_stop / 'build-report.json').read_bytes(), (pair_ack / 'SNAPMON-M1-ACK.BIN').read_bytes(), (pair_ack / 'SNAPREP-M1-ACK.BIN').read_bytes())
        self.assertEqual(caught.exception.name, 'M1_REPORT_MISMATCH')
        with self.assertRaises(self.m1.Refused) as caught:
            self.m1.read_report((pair_ack / 'build-report.json').read_bytes(), (pair_stop / 'SNAPMON-M1.BIN').read_bytes(), (pair_stop / 'SNAPREP-M1.BIN').read_bytes(), None, 'ack')
        self.assertEqual(caught.exception.name, 'M1_REPORT_MISMATCH')

    def test_a_report_whose_key_and_defines_disagree_is_refused(self):
        raw = json.loads((STATE['pair-ack'] / 'build-report.json').read_text())
        for name, change in (('no key', lambda r: r.pop('cpu_start')), ('no define', lambda r: r['added'].remove(ACK_DEFINE)),
                             ('the define twice', lambda r: r['added'].append(ACK_DEFINE)), ('another define', lambda r: r['added'].__setitem__(r['added'].index(ACK_DEFINE), '-DSNAP_SP11_CPU_START_ACK=0'))):
            with self.subTest(name):
                mutated = json.loads(json.dumps(raw))
                change(mutated)
                with self.assertRaises(self.m1.Refused) as caught:
                    self.facts('ack', report=json.dumps(mutated).encode())
                self.assertEqual(caught.exception.name, 'M1_REPORT_MISMATCH')
        plain = json.loads((STATE['pair-stop'] / 'build-report.json').read_text())
        plain['added'].append(ACK_DEFINE)
        with self.assertRaises(self.m1.Refused) as caught:
            self.facts('stop', report=json.dumps(plain).encode())
        self.assertEqual(caught.exception.name, 'M1_REPORT_MISMATCH')
        self.facts('ack')          # the untouched report passes, so that every refusal above is the tamper's

    def test_no_record_is_written_for_ack_unless_the_text_of_the_m1_record_lists_it(self):
        text = T.load('sp11_stick_bundle_record_m1_for_ack', TOOLS / 'sp11_stick_bundle_record_m1.py')
        self.assertEqual(text.CPU_START_TEXTS, ('stop', 'ack'), 'the text says what ack is since rung 5 (docs/SP11-STICK-MONITOR.md section 20, K13)')
        self.m1.check_cpu_start('stop', True)
        self.m1.check_cpu_start('stop', False)
        for asked, is_m1, why in (('ack', False, 'ack needs --m1-run'), ('maybe', True, 'not a choice')):
            with self.subTest(why):
                with self.assertRaises(self.m1.ch.Refused) as caught:
                    self.m1.check_cpu_start(asked, is_m1)
                self.assertEqual(caught.exception.name, 'OPTIONS_BAD')
        with mock.patch.dict(sys.modules, {'sp11_stick_bundle_record_m1': mock.Mock(spec=[])}):      # a text that lists no choice
            with self.assertRaises(self.m1.ch.Refused) as caught:
                self.m1.check_cpu_start('ack', True)
            self.assertEqual(caught.exception.name, 'OPTIONS_BAD')
        with mock.patch.dict(sys.modules, {'sp11_stick_bundle_record_m1': mock.Mock(CPU_START_TEXTS=('stop', 'ack'))}):
            self.m1.check_cpu_start('ack', True)
            with self.assertRaises(self.m1.ch.Refused):
                self.m1.check_cpu_start('ack', False)

    def test_the_builders_option_defaults_to_stop_and_takes_nothing_else(self):
        bb = T.bb
        parser = argparse.ArgumentParser()
        bb.build_opts(parser, 'synthetic', True)
        base = ['--facts', 'f', '--base', '0x1', '--guest-mib', '1', '--monitor', 'm', '--reporter', 'r', '--stores', 'ram', '--time-box-s', '5']
        self.assertEqual(parser.parse_args(base).cpu_start, 'stop')
        self.assertEqual(parser.parse_args([*base, '--cpu-start', 'ack']).cpu_start, 'ack')
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args([*base, '--cpu-start', 'maybe'])


if __name__ == '__main__':
    unittest.main()
