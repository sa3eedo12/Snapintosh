#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of the mechanism of rung 7 of the monitor entry run (docs/SP11-STICK-MONITOR.md section 20, K15; docs/SP11-M1-GUEST-PROFILES.md section 5; EXPERIMENTAL, hardware NOT RUN): `real
--m1-run --panel console --cpu-start ack --let-panic --boot-arg cpu_boot_timeout_secs=120 --time-box-s 120 --guest-profile j700ap-26A428-m1-quiet --sptm-hvc-calls 0` is the third and last form of
the closed list of the acknowledging monitor (rung 5: 60 s and the word 120; rung 6: 120 s and the word 90; rung 7: 120 s, the word 120 and the quiet profile, which no other form carries) and
every other combination of box, word and profile is a named refusal that writes nothing. Checked here: the closed list (a matrix of forms, each a named refusal); the profile outside rung 7's
form is GUEST_PROFILE_REFUSED and rung 7's form without it too; the pair by form, rung 7 on rung 6's pair and M1_REPORT_MISMATCH both ways; the label M1RUN-ACKPANIC-Q-RUNG7 among the labels
of the ring; the comparison of the finished guest with the chunk table of rung 6's own bundle (every seeded fault is PANEL_MISMATCH); the builder end to end through the stand-in loader, with
the record of rung 7 and each of the seven other approved records refused against each other (AUTH_RECORD_MISMATCH, nothing written); the sizes and the cap (K15). The words of the record are
tools/test-sp11-stick-monitor-rung7-words.py's, which takes its fixtures from here; `Real` needs downloads/extracted/j700ap/ and the local pair and facts, and SKIPs without them (a skip is
never a pass).

    python3 tools/test-sp11-stick-monitor-rung7.py

The regeneration script of the session is the end-to-end test with the real J700 26A428 kernelcache and the real pair. Nothing here runs the unit: Hardware NOT RUN."""
import contextlib
import hashlib
import importlib.util
import json
import struct
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))

spec = importlib.util.spec_from_file_location('sp11_monitor_rung6_tests', TOOLS / 'test-sp11-stick-monitor-rung6.py')
M6 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M6)
M5 = M6.M5
SCRATCH = ROOT / 'build' / 'sp11-monitor-rung7-test-scratch'
M5.SCRATCH = M5.PS.SCRATCH = M5.PT.SCRATCH = M5.PT.BLD.SCRATCH = M5.PT.AUTH.SCRATCH = SCRATCH
PS, PT, pn, m1, rec, BT, ch, sb, bb, BLD, AUTH = M5.PS, M5.PT, M5.pn, M5.m1, M5.rec, M5.BT, M5.ch, M5.sb, M5.bb, M5.BLD, M5.AUTH
import sp11_stick_bundle_profile as gpb  # noqa: E402
import sp11_stick_bundle_rung7 as r7b  # noqa: E402
import sp11_stick_bundle_panel_rung7 as r7  # noqa: E402
import sp11_stick_bundle_panel_profile as pp  # noqa: E402

STATE, OLD, NAMES, F, P, W90, HELD_LIMIT = M5.STATE, M5.OLD, M5.NAMES, M5.F, M5.P, M5.W90, M5.HELD_LIMIT
Q, BASE, LABEL7 = gpb.QUIET, gpb.DEFAULT, 'r7-t'
ORIGINAL_RUNG1 = (M5.r2.RUNG1_MONITOR, M5.r2.RUNG1_REPORTER)        # the fixtures of rung 5's suite put stand-in digests here for their own records; the real builds of `Real` need the real ones
REAL_PAIRS = M6.REAL_PAIRS


def fake_compare(chunks, table, guest_bytes, nodes):
    """What `compare` returns for the stand-in loader's filler guest (which is not rung 6's: the real comparison has its own tests): rung 6's pins and this build's numbers."""
    return dict(count=46, differing=(0, 3), params_same=True, dt_bytes=(350_576, 327_664), nodes=(395, nodes), dt_length=(350_572, 327_664), guest_bytes=(118_908_480, guest_bytes))


@contextlib.contextmanager
def patched(nodes=352):
    """The read-back and the comparison of the profile guest, which need the real loader's tree and rung 6's real guest, under the stand-in loader."""
    with mock.patch.object(gpb, 'read_back', return_value=nodes), mock.patch.object(r7b, 'compare', fake_compare):
        yield


def over7(over):
    """The arguments of a real build of rung 7 over the stand-in loader: rung 5's fixture with the word, the quiet profile and the stand-in facts label; a profile of None is dropped."""
    merged = {'boot_arg': F, 'facts': BLD.facts_with(LABEL7), 'guest_profile': Q, **over}
    return {key: value for key, value in merged.items() if key != 'guest_profile' or value is not None}


def record7(name, box=120, let_panic=True, **over):
    """(code, text, err) of the record of rung 7 from the stand-in loader."""
    with patched():
        return M5.record5(name, box, let_panic, **over7(over))


def build7(name, box=120, approved=None, **over):
    """(code, text, err, out) of a real build of rung 7 with the approved record `approved` (its own by default)."""
    with patched():
        return M5.build5(name, box, approved, **over7(over))


def setUpModule():
    M6.setUpModule()
    code, text, err = record7('setup-r7')
    assert code == 0, err
    STATE['r7'] = text
    made = []
    real_with = r7b.with_guest

    def spy(*args):
        made.append(real_with(*args))
        return made[-1]
    with patched(), mock.patch.object(r7b, 'with_guest', spy):
        code, text, err = M5.record5('setup-r7-panel', 120, True, **over7({}))
    assert code == 0, err
    STATE['r7-panel'] = made[0]


def tearDownModule():
    M6.tearDownModule()


def admit(*form):
    """What the builder does with the options before any guest is built: m1.check_options, then the profile's own check (gpb.check_form), in that order."""
    box, panel, hvc, word, let_panic, cpu_start, profile = form
    m1.check_options(7, False, box, panel, hvc, word, let_panic, cpu_start, profile)
    gpb.check_form(profile, word, let_panic, cpu_start, panel, True, box)


class Form(unittest.TestCase):
    def raises(self, name, function, *args):
        with self.assertRaises(ch.Refused) as caught:
            function(*args)
        self.assertEqual(caught.exception.name, name)
        return caught.exception

    def test_the_form_is_the_third_of_a_closed_list_of_three_and_carries_the_one_profile(self):
        self.assertEqual((ch.RUNG7_FORM, m1.RUNG7_FORM, ch.RUNG7_PROFILE, gpb.QUIET, gpb.RUNG7_PROFILES), (('console', F, 120), ('console', F, 120), 'j700ap-26A428-m1-quiet', Q, (Q,)))
        self.assertEqual(ch.ACK_FORMS, (ch.RUNG5_FORM, ch.RUNG6_FORM, ch.RUNG7_FORM), 'rung 7 is the third and there is no fourth')
        self.assertEqual(ch.FORM_PROFILE, {ch.RUNG7_FORM: Q}, 'the profile belongs to rung 7\'s form and to no other')
        self.assertEqual((m1.BOOT_ARGS[F], m1.BOOT_ARG_CPU_START[F], m1.RUNG6_BOX_S, m1.BOX_MAX_S), ('ACKPANIC-', 'ack', 120, 120), 'rung 7 asks rung 6\'s box (D5\'\') and rung 5\'s word')
        self.assertEqual(sorted(m1.BOOT_ARGS), sorted([P, F, W90]), 'no boot argument was added: nested_panic_max is in no form')
        self.assertNotIn('nested_panic_max', ' '.join(m1.BOOT_ARGS) + ch.LET_PANIC_FORMS_TEXT + str(gpb.NAMES))
        self.assertEqual(ch.LET_PANIC_FORMS_TEXT.count('--guest-profile'), 1)
        for form in ((120, 'console', 0, F, True, 'ack', Q), (60, 'console', 0, F, True, 'ack', None), (120, 'console', 0, W90, True, 'ack', None), (5, 'console', 0, P, True, 'stop', None),
                     (5, 'console', 0, P, False, 'stop', None), (5, 'console', 0, None, False, 'stop', None), (5, 'handed', 0, None, False, 'stop', None), (5, 'withheld', 0, None, False, 'stop', None),
                     (60, 'console', 0, F, True, 'ack', BASE)):
            with self.subTest(form):
                admit(*form)
        self.assertEqual([ch.let_panic_form_ok('console', F, 120, 'ack', Q), ch.let_panic_form_ok('console', F, 120, 'ack', None), ch.let_panic_form_ok('console', F, 60, 'ack', None),
                          ch.let_panic_form_ok('console', F, 60, 'ack', Q), ch.let_panic_form_ok('console', W90, 120, 'ack', Q), ch.let_panic_form_ok('console', F, None, 'ack', Q),
                          ch.let_panic_form_ok('console', F, None, 'ack', None), ch.let_panic_form_ok('console', W90, None, 'ack', Q), ch.let_panic_form_ok('console', P, 5, 'stop', Q)],
                         [True, False, True, False, False, True, True, False, False])

    def test_every_other_combination_of_box_word_and_profile_is_a_named_refusal(self):
        menu = [name for name in gpb.NAMES if name not in (BASE, Q)]
        G, L, B, O = 'GUEST_PROFILE_REFUSED', 'LET_PANIC_REFUSED', 'BOOT_ARG_REFUSED', 'OPTIONS_BAD'
        bad = {'rung 7\'s box and word without the profile': ((120, 'console', 0, F, True, 'ack', None), G), 'the same with the default board named': ((120, 'console', 0, F, True, 'ack', BASE), G),
               'rung 5\'s box with the profile': ((60, 'console', 0, F, True, 'ack', Q), G), 'rung 6\'s word and box with the profile': ((120, 'console', 0, W90, True, 'ack', Q), G),
               'rung 6\'s word and rung 5\'s box with the profile': ((60, 'console', 0, W90, True, 'ack', Q), L),
               'a box of 119 s': ((119, 'console', 0, F, True, 'ack', Q), L), 'a box of 121 s': ((121, 'console', 0, F, True, 'ack', Q), O), 'a box of 1 s': ((1, 'console', 0, F, True, 'ack', Q), L),
               'a box of 5 s': ((5, 'console', 0, F, True, 'ack', Q), L), 'a box of 30 s': ((30, 'console', 0, F, True, 'ack', Q), L),
               'no --let-panic': ((120, 'console', 0, F, False, 'ack', Q), L), 'no word': ((120, 'console', 0, None, True, 'ack', Q), B),
               'the plain monitor': ((120, 'console', 0, F, True, 'stop', Q), B), 'the plain monitor with rung 4\'s form': ((5, 'console', 0, P, True, 'stop', Q), G),
               'rung 3\'s form': ((5, 'console', 0, P, False, 'stop', Q), G), 'the first run\'s form': ((5, 'withheld', 0, None, False, 'stop', Q), G),
               'the panel handed': ((120, 'handed', 0, F, True, 'ack', Q), B), 'the panel handed with no word': ((5, 'handed', 0, None, False, 'stop', Q), G),
               'a patched kernel': ((120, 'console', 1, F, True, 'ack', Q), O), 'cpumask': ((120, 'console', 0, 'cpumask=1', True, 'ack', Q), B),
               'both timeout words': ((120, 'console', 0, f'{F} {W90}', True, 'ack', Q), B), 'nested_panic_max=3': ((120, 'console', 0, 'nested_panic_max=3', True, 'ack', Q), B),
               'the word and nested_panic_max=3': ((120, 'console', 0, f'{F} nested_panic_max=3', True, 'ack', Q), B), 'another timeout': ((120, 'console', 0, 'cpu_boot_timeout_secs=91', True, 'ack', Q), B),
               'a profile that is not in the menu': ((120, 'console', 0, F, True, 'ack', 'quiet'), G)}
        for name in menu:
            bad[f'{name[len(gpb.NAMES[0]) + 1:]} in rung 7\'s form'] = ((120, 'console', 0, F, True, 'ack', name), G)
        self.assertGreaterEqual(len(bad), 25)
        for what, (form, name) in bad.items():
            with self.subTest(what):
                self.raises(name, admit, *form)
        for what, (form, name) in {'the builder does not know it': ((120, 'console', 0, F, True, 'ack', 'quiet'), G)}.items():
            self.raises(name, gpb.resolve, type('A', (), {'guest_profile': form[-1]})())

    def test_each_bad_form_is_refused_end_to_end_by_name_and_writes_nothing(self):
        cases = {'no profile': (dict(guest_profile=None), True, 'GUEST_PROFILE_REFUSED'), 'the default board named': (dict(guest_profile=BASE), True, 'GUEST_PROFILE_REFUSED'),
                 'another profile': (dict(guest_profile=gpb.NAMES[2]), True, 'GUEST_PROFILE_REFUSED'), 'an unknown profile': (dict(guest_profile='quiet'), True, 'GUEST_PROFILE_REFUSED'),
                 'rung 6\'s word': (dict(boot_arg=W90), True, 'GUEST_PROFILE_REFUSED'), 'no word': (dict(boot_arg=None), True, 'BOOT_ARG_REFUSED'),
                 'nested_panic_max=3': (dict(boot_arg=f'{F} nested_panic_max=3'), True, 'BOOT_ARG_REFUSED'), 'cpus=1': (dict(boot_arg=P), True, 'BOOT_ARG_REFUSED'),
                 'rung 5\'s box': (dict(time_box_s=60), True, 'GUEST_PROFILE_REFUSED'), 'a box of 119 s': (dict(time_box_s=119), True, 'LET_PANIC_REFUSED'),
                 'a box of 5 s': (dict(time_box_s=5), True, 'LET_PANIC_REFUSED'), 'a box of 121 s': (dict(time_box_s=121), True, 'OPTIONS_BAD'),
                 'the plain choice with the ack report': (dict(cpu_start='stop'), True, 'BOOT_ARG_REFUSED'), 'the panel handed': (dict(panel='handed'), True, 'BOOT_ARG_REFUSED'),
                 'no --let-panic': ({}, False, 'LET_PANIC_REFUSED')}
        for what, (over, let_panic, name) in cases.items():
            with self.subTest(what):
                code, text, err = record7('bad-form', let_panic=let_panic, **over)
                self.assertEqual((code, BT.refusal(text)), (1, f'REFUSED={name}'), err)
                if let_panic:
                    code, text, err, out = build7('bad-form-built', approved='not a record', **over)
                    self.assertEqual(code, 1)
                    self.assertFalse(out.exists(), 'nothing is written')

    def test_the_older_forms_with_the_profile_are_refused_by_name_whichever_the_kind(self):
        for kind in OLD:
            with self.subTest(NAMES[kind]):
                code, text, err = PS.record(f'old-{NAMES[kind]}-profile', kind, guest_profile=Q)
                self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=GUEST_PROFILE_REFUSED'), err)
        code, text, err = M5.record5('r5-with-profile', 60, True, guest_profile=Q)
        self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=GUEST_PROFILE_REFUSED'), err)
        code, text, err = M6.record6('r6-with-profile', guest_profile=Q)
        self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=GUEST_PROFILE_REFUSED'), err)

    def test_the_box_of_a_pair_built_at_the_cap_of_5_is_refused_for_rung_7_as_for_rungs_5_and_6(self):
        mon = AUTH.sha(Path(M5.args5('cap-probe-7')['monitor']).read_bytes())
        with mock.patch.dict(m1.LOW_CAP_MONITORS, {mon: 5}):
            code, text, err = record7('cap-of-the-pair')
            self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=OPTIONS_BAD'), err)


class Pair(unittest.TestCase):
    def test_rung_7_uses_rung_6s_pair_by_digest_and_the_table_is_keyed_by_form(self):
        self.assertEqual(sorted(REAL_PAIRS), [F, W90, Q], 'the pairs are keyed by word, and rung 7\'s by its profile (its word is rung 5\'s)')
        self.assertEqual(REAL_PAIRS[Q], REAL_PAIRS[W90], 'rung 7 uses rung 6\'s pair')
        self.assertNotEqual(REAL_PAIRS[Q], REAL_PAIRS[F], 'and not rung 5\'s')
        self.assertEqual(REAL_PAIRS[Q], (m1.ACK_MONITOR_SHA256, m1.ACK_REPORTER6_SHA256))
        self.assertEqual(REAL_PAIRS[Q][1][:8], '533d004c', 'reporter v3, the one that boot 26 ran')
        self.assertEqual(REAL_PAIRS[F][1][:8], 'ee866760')

    def test_check_pair_form_holds_rung_7_to_its_pair_both_ways(self):
        with mock.patch.dict(m1.ACK_PAIRS, REAL_PAIRS):     # the builder's own table, under the stand-in pair that the fixtures put in its place
            monitor, reporter6 = REAL_PAIRS[Q]
            reporter5 = REAL_PAIRS[F][1]
            self.assertIsNone(m1.check_pair_form('ack', F, monitor, reporter6, Q))
            self.assertIsNone(m1.check_pair_form('ack', F, monitor, reporter5), 'rung 5 on its own pair')
            self.assertIsNone(m1.check_pair_form('ack', W90, monitor, reporter6), 'rung 6 on its own pair')
            for what, args in (('rung 5\'s reporter under rung 7', (F, monitor, reporter5, Q)), ('rung 6\'s pair under rung 5', (F, monitor, reporter6)),
                               ('another monitor', (F, 'ab' * 32, reporter6, Q)), ('another reporter', (F, monitor, 'cd' * 32, Q)), ('rung 5\'s reporter under rung 6', (W90, monitor, reporter5))):
                with self.subTest(what), self.assertRaises(ch.Refused) as caught:
                    m1.check_pair_form('ack', *args)
                self.assertEqual(caught.exception.name, 'M1_REPORT_MISMATCH')
                self.assertNotIn('ab' * 32, caught.exception.detail, 'a digest is quoted by its first eight digits only')
            self.assertIsNone(m1.check_pair_form('stop', P, 'ab' * 32, 'cd' * 32), 'the plain monitor has no table')

    def test_a_pair_that_is_the_other_forms_is_refused_both_ways_and_nothing_is_written(self):
        fake, other = STATE['fake'], b'REP2' * 200
        report = AUTH.mutated(M5.ack_report(), lambda r: r['files'].__setitem__('SNAPREP-M1-ACK.BIN', AUTH.entry(other)))
        mon, rep5, rep6 = AUTH.sha(fake['image']), AUTH.sha(fake['reporter']), AUTH.sha(other)
        with mock.patch.dict(m1.ACK_PAIRS, {F: (mon, rep5), W90: (mon, rep6), Q: (mon, rep6)}):
            own7 = AUTH.stage('pair7', reporter=other, report=report)
            own7.pop('m1_efi')
            code, text, err = record7('own-pair-7', **own7)
            self.assertEqual(code, 0, err)       # rung 7 on rung 6's pair
            code, text, err = record7('rung5-pair-under-7')
            self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=M1_REPORT_MISMATCH'), err)       # rung 5's pair under rung 7
            code, text, err = M5.record5('rung7-pair-under-5', **own7)
            self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=M1_REPORT_MISMATCH'), err)       # rung 6's and 7's pair under rung 5
            for builder, over in ((build7, {}), (M5.build5, own7)):
                code, text, err, out = builder('pair-built', approved='not a record', **over)
                self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=M1_REPORT_MISMATCH'), err)
                self.assertFalse(out.exists(), 'nothing is written')


class Labels(unittest.TestCase):
    def test_the_label_of_rung_7_is_its_own_among_the_ring_and_fits_the_23_characters(self):
        ring = {'panel': m1.pinned_label('RUNG1', True, 'handed'), 'console': m1.pinned_label('RUNG2', True, 'console'), 'rung3': m1.pinned_label('RUNG3', True, 'console', P),
                'rung4': m1.pinned_label('RUNG4', True, 'console', P, True), 'rung5': m1.pinned_label('RUNG5', True, 'console', F, True, 'ack'),
                'rung6': m1.pinned_label('RUNG6', True, 'console', W90, True, 'ack'), 'rung7': m1.pinned_label('RUNG7', True, 'console', F, True, 'ack', profile=Q)}
        self.assertEqual(ring['rung7'], 'M1RUN-ACKPANIC-Q-RUNG7')
        self.assertEqual(len(ring['rung7']), 22)
        self.assertEqual(len(set(ring.values())), 7)
        self.assertEqual((ring['rung6'], ring['rung5'], ring['rung4'], ring['rung3'], ring['console']),
                         ('M1RUN-ACKPANIC-RUNG6', 'M1RUN-ACKPANIC-RUNG5', 'M1RUN-LETPANIC-RUNG4', 'M1RUN-CPUS1-RUNG3', 'M1RUN-CONSOLE-RUNG2'))
        self.assertEqual(m1.pinned_label('RUNG10', True, 'console', F, True, 'ack', profile=Q), 'M1RUN-ACKPANIC-Q-RUNG10')
        with self.assertRaises(ch.Refused) as caught:
            m1.pinned_label('RUNG100', True, 'console', F, True, 'ack', profile=Q)
        self.assertEqual(caught.exception.name, 'PIN_STRING')
        self.assertIn('holds 6', caught.exception.detail)

    def test_a_facts_label_that_starts_with_a_marker_or_a_mark_is_refused_for_every_ack_form(self):
        markers = [m for m in m1.PANEL_MARKS.values() if m] + sorted(set(m1.BOOT_ARGS.values())) + [m1.LET_PANIC_MARK] + [mark for mark in gpb.MARKS.values() if mark]
        for form in (('console', F, True, 'ack', None), ('console', W90, True, 'ack', None), ('console', F, True, 'ack', Q)):
            for mark in markers:
                with self.subTest(form=form[1:], mark=mark), self.assertRaises(ch.Refused) as caught:
                    m1.pinned_label(f'{mark}x', True, *form[:4], profile=form[4])
                self.assertEqual(caught.exception.name, 'PIN_STRING')
        code, text, err = record7('marked-label', facts=BLD.facts_with('Q-x'))
        self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=PIN_STRING'), err)
        self.assertEqual(m1.pinned_label('Q-x', False), 'Q-x', 'the other kinds keep the facts label')

    def test_the_label_is_in_the_record_the_pin_and_the_plan(self):
        text = STATE['r7']
        self.assertIn(f'(format 1, label M1RUN-ACKPANIC-Q-{LABEL7})', text.splitlines()[0])
        code, text, err, out = build7('own-r7', approved=STATE['r7'])
        self.assertEqual(code, 0, (text, err))
        self.assertEqual(BLD.label_of(BLD.pin_of(out)).decode(), f'M1RUN-ACKPANIC-Q-{LABEL7}')
        self.assertEqual(sb.check_pin(BLD.pin_of(out)), 0)


def fabricated():
    """A stand-in for rung 6's bundle and a guest that differs from it as rung 7's does: chunk tables, the HANDOFF page and its two fields."""
    def page(length, line):
        data = bytearray(bytes(range(256)) * 32)
        data[0x1068:0x106C] = struct.pack('<I', length)
        data[0x106C:0x106C + 1024] = line.ljust(1024, b'\0')
        return bytes(data)
    sha = lambda data: hashlib.sha256(data).hexdigest()    # noqa: E731
    six = page(1000, b'rung6 line')
    rows6 = [(3, 0x1000, 8192, sha(six)), (1, 0x3000, 100, sha(b'a')), (5, 0x4000, 64, sha(b'params')), (4, 0x5000, 1000, sha(b'tree6')), (1, 0x9000, 50, sha(b'z'))]
    seven = page(900, b'rung7 line')
    rows7 = [(3, 0x1000, 8192, sha(seven)), rows6[1], rows6[2], (4, 0x5000, 900, sha(b'tree7')), rows6[4]]
    table = lambda rows: [dict(kind=k, dest=d, size=s, sha256=bytes.fromhex(h)) for k, d, s, h in rows]    # noqa: E731
    return rows6, table(rows7), [dict(kind=3, dest=0x1000, data=seven)], six


class Compare(unittest.TestCase):
    def run_compare(self, rows6, table, chunks, **patch):
        with mock.patch.object(r7b, 'CHUNKS6', tuple(rows6)), mock.patch.object(r7b, 'RUNG6_DT_LENGTH', 1000), mock.patch.object(r7b, 'RUNG6_LINE', 'rung6 line'), \
                mock.patch.object(r7b, 'RUNG6_GUEST_BYTES', 5000), mock.patch.object(r7b, 'PARAMS_AT', 2):
            return r7b.compare(chunks, table, patch.get('size', 4900), 352)

    def refused(self, rows6, table, chunks, **patch):
        with self.assertRaises(ch.Refused) as caught:
            self.run_compare(rows6, table, chunks, **patch)
        self.assertEqual(caught.exception.name, 'PANEL_MISMATCH')
        return caught.exception.detail

    def test_the_comparison_reads_the_two_chunks_and_the_two_fields_and_nothing_else(self):
        rows6, table, chunks, _six = fabricated()
        diff = self.run_compare(rows6, table, chunks)
        self.assertEqual(diff, dict(count=5, differing=(0, 3), params_same=True, dt_bytes=(1000, 900), nodes=(395, 352), dt_length=(1000, 900), guest_bytes=(5000, 4900)))

    def test_each_seeded_fault_of_the_guest_is_panel_mismatch_by_name(self):
        rows6, table, chunks, six = fabricated()
        sha = lambda data: hashlib.sha256(data).digest()    # noqa: E731
        other = lambda i, **kw: [{**row, **kw} if n == i else row for n, row in enumerate(table)]    # noqa: E731
        faults = {'PARAMS differs': (other(2, sha256=sha(b'other')), chunks), 'another chunk differs': (other(4, sha256=sha(b'other')), chunks), 'an image chunk is shorter': (other(1, size=99), chunks),
                  'a chunk is elsewhere': (other(1, dest=0x3001), chunks), 'a chunk is missing': (table[:-1], chunks), 'a chunk is added': ([*table, table[-1]], chunks),
                  'the device tree is rung 6\'s': (other(3, sha256=bytes.fromhex(rows6[3][3]), size=1000), chunks), 'the HANDOFF page is rung 6\'s': (other(0, sha256=bytes.fromhex(rows6[0][3])), chunks),
                  'no data for the HANDOFF page': (table, [dict(kind=3, dest=0x1000)]),
                  'a byte of the HANDOFF page outside the two fields': (table, [dict(kind=3, dest=0x1000, data=chunks[0]['data'][:0x500] + b'!' + chunks[0]['data'][0x501:])]),
                  'a short HANDOFF page': (table, [dict(kind=3, dest=0x1000, data=chunks[0]['data'][:4096])])}
        for what, (rows, data) in faults.items():
            with self.subTest(what):
                self.refused(rows6, rows, data)
        self.assertEqual(six[0x1068:0x106C], struct.pack('<I', 1000))

    def test_the_pins_are_rung_6s_own_bundle_46_chunks_and_the_expected_two_differ_in_kind(self):
        self.assertEqual(len(r7b.CHUNKS6), 46)
        self.assertEqual((r7b.CHUNKS6[0][0], r7b.CHUNKS6[2][0], r7b.CHUNKS6[3][0]), (sb.KIND_HANDOFF, sb.KIND_PARAMS, sb.KIND_DEVICE_TREE))
        self.assertEqual((r7b.DIFFERING, r7b.PARAMS_AT, r7b.CHUNKS6[3][2], r7b.RUNG6_GUEST_BYTES, r7b.RUNG6_DT_LENGTH), ((0, 3), 2, 350_576, 118_908_480, 350_572))
        self.assertEqual(r7b.RUNG6_LINE, M6.r6.LINE)
        self.assertEqual(r7b.RUNG6_LINE.encode(), pn.command_line('console', W90).encode())
        self.assertTrue(all(len(h) == 64 and (h == '0' * 64 or h.strip('0123456789abcdef') == '') for _, _, _, h in r7b.CHUNKS6))
        self.assertEqual(r7b.RUNG6_GUEST[:16], '1dd2e9ac94fdb741', 'boot 26\'s INDEX=2 line: SHA=1DD2E9AC94FDB741')
        self.assertEqual(r7b.RUNG6_RECORD[:16], 'd9d79c87cccc8c39')


class Builder(unittest.TestCase):
    def test_the_record_of_rung_7_is_built_with_its_label_and_is_under_the_held_limit(self):
        text = STATE['r7']
        self.assertLessEqual(len(text.encode()), HELD_LIMIT)
        self.assertTrue(text.isascii())
        panel = STATE['r7-panel']
        self.assertEqual((panel['profile'], panel['boot_arg'], panel['let_panic'], sorted(panel['rung7'])), (Q, F, True, ['diff', 'guest_bytes', 'guest_sha16', 'label']))
        self.assertEqual(pn.kind_key({'panel': 'console', 'boot_arg': F, 'let_panic': True, 'panel_facts': panel}), f'console+{F}+let_panic+{Q}')

    def test_the_default_builds_are_what_they_were_and_the_option_names_a_profile_only_for_rung_7(self):
        code, text, err = M5.record5('again')
        self.assertEqual(code, 0, err)
        self.assertEqual(text, STATE['r5-60'], 'rung 5\'s record is the same with the option in the tree')
        code, text, err = M5.record5('again-named', guest_profile=BASE)
        self.assertEqual((code, text), (0, STATE['r5-60']), 'naming the default board is no change')
        code, text, err = M6.record6('again-6')
        self.assertEqual((code, text), (0, STATE['r6-120']), 'rung 6\'s record is the same with the option in the tree')
        for name in ('quiet-aop', 'all-quiet'):
            self.assertNotIn(name, STATE['r7'])

    def test_a_build_with_the_approved_record_of_another_kind_is_refused_whichever_is_the_guest(self):
        others = {**{NAMES[kind]: STATE[NAMES[kind]] for kind in OLD}, 'rung5': STATE['r5-60'], 'rung6': STATE['r6-120']}
        self.assertEqual(len({*others.values(), STATE['r7']}), 8, 'eight records, eight texts')
        for name, approved in others.items():
            with self.subTest(guest='rung7', approved=name):
                code, text, err, out = build7(f'cross-r7-{name}', approved=approved)
                self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=AUTH_RECORD_MISMATCH'), err)
                self.assertFalse(out.exists(), 'nothing is written for a record that is not this build\'s')
        for kind in OLD:
            with self.subTest(guest=NAMES[kind], approved='rung7'):
                code, text, err, out = PS.build(f'cross-{NAMES[kind]}-r7', kind, approved=STATE['r7'])
                self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=AUTH_RECORD_MISMATCH'), err)
                self.assertFalse(out.exists())
        for guest, builder in (('rung5', lambda: M5.build5('cross-r5-r7', approved=STATE['r7'])), ('rung6', lambda: M6.build6('cross-r6-r7', approved=STATE['r7']))):
            with self.subTest(guest=guest, approved='rung7'):
                code, text, err, out = builder()
                self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=AUTH_RECORD_MISMATCH'), err)
                self.assertFalse(out.exists())

    def test_the_read_back_is_told_the_profile_and_the_comparison_is_made_once_per_build(self):
        with mock.patch.object(gpb, 'read_back', return_value=352) as read_back, mock.patch.object(r7b, 'compare', fake_compare):
            code, text, err = M5.record5('read-back', 120, True, **over7({}))
        self.assertEqual(code, 0, err)
        self.assertEqual([c.args[2] for c in read_back.call_args_list], [Q])


PAIR = ROOT / 'build/r11/ackpair/wrap-a'
LOCAL6, LOCAL7 = ROOT / 'build/m1p/sp11-facts-rung6.local.json', ROOT / 'build/m1p/sp11-facts-rung7.local.json'
INPUTS = (ROOT / 'downloads/extracted/j700ap/kernelcache.release.mac17p', PAIR / 'SNAPMON-M1-ACK.BIN', PAIR / 'SNAPREP-M1-ACK.BIN', PAIR / 'build-report.json', ROOT / 'build/sp11-m1-descriptor.json',
          LOCAL6, LOCAL7)
needs_inputs = unittest.skipUnless(all(p.exists() for p in INPUTS), 'no kernelcache, rung 6 pair, descriptor or local facts of rungs 6 and 7 (git-ignored local inputs)')


@needs_inputs
class Real(unittest.TestCase):
    """Rung 6's and rung 7's guest from the real J700 26A428 kernelcache, in this process (about 10 s each): rung 6's guest and record are the pinned ones, rung 7's differs from rung 6's in
    chunks 0 and 3 only, its HANDOFF page in the two fields only, and its PARAMS chunk is rung 6's byte for byte."""
    EFI = ('SLBOUNCE-DEVHE-MARK.EFI=4107c28c194e2f2fd3517b2081f1b2bd9224571a7b037163e9e27c3ba3c8fcf7', 'Q1N1-PAYLOAD.EFI=c20579093ec7b1506faca3c0d33520561716510d7932ac1c9d340f432c88bf6e')

    @classmethod
    def prepare(cls, seven):
        rel = lambda p: str(Path(p).relative_to(ROOT))    # noqa: E731  the record prints the paths as given: the regeneration's are relative to the tree
        argv = ['authorisation-template', 'real', '--facts', rel(LOCAL7 if seven else LOCAL6), '--base', '0x880000000', '--guest-mib', '384', '--monitor', rel(PAIR / 'SNAPMON-M1-ACK.BIN'),
                '--reporter', rel(PAIR / 'SNAPREP-M1-ACK.BIN'), '--m1-run', rel(PAIR / 'build-report.json'), '--stores', 'ram,el2_sysreg,gic', '--time-box-s', '120', '--kernelcache', rel(INPUTS[0]),
                '--descriptor', rel(INPUTS[4]), '--panel', 'console', '--sptm-hvc-calls', '0', '--m1-efi', cls.EFI[0], '--m1-efi', cls.EFI[1], '--let-panic', '--cpu-start', 'ack',
                '--boot-arg', F if seven else W90, *(['--guest-profile', Q] if seven else [])]
        got = []
        panels = []

        def spy(*args):
            got.append(real_compare(*args))
            return got[-1]

        def spy_panel(*args):
            panels.append(real_with(*args))
            return panels[-1]
        real_compare, real_with = r7b.compare, r7b.with_guest
        with contextlib.chdir(ROOT), mock.patch.dict(m1.ACK_PAIRS, REAL_PAIRS), mock.patch.object(r7b, 'compare', spy), mock.patch.object(r7b, 'with_guest', spy_panel), \
                mock.patch.object(M5.r2, 'RUNG1_MONITOR', ORIGINAL_RUNG1[0]), mock.patch.object(M5.r2, 'RUNG1_REPORTER', ORIGINAL_RUNG1[1]):
            made = bb.prepare(bb.build_parser().parse_args(argv), 'real')
        return made, (got[0] if got else None), (panels[0] if panels else None)

    @classmethod
    def setUpClass(cls):
        cls.six, _, _ = cls.prepare(False)
        cls.seven, cls.diff, cls.panel7 = cls.prepare(True)

    def test_rung_6s_record_and_guest_are_the_pinned_ones_and_its_chunk_table_is_the_pinned_table(self):
        self.assertEqual(hashlib.sha256(self.six['record'].encode()).hexdigest(), r7b.RUNG6_RECORD)
        self.assertEqual(hashlib.sha256(self.six['guest']).hexdigest(), r7b.RUNG6_GUEST)
        _, table = sb.unpack_guest(self.six['guest'])
        self.assertEqual([(c['kind'], c['dest'], c['size'], bytes(c['sha256']).hex()) for c in table], list(r7b.CHUNKS6))

    def test_rung_7s_guest_differs_from_rung_6s_in_two_chunks_and_its_parameters_are_rung_6s_byte_for_byte(self):
        self.assertEqual(self.diff, dict(count=46, differing=(0, 3), params_same=True, dt_bytes=(350_576, 327_664), nodes=(395, 352), dt_length=(350_572, 327_664),
                                         guest_bytes=(118_908_480, len(self.seven['guest']))))
        blobs = []
        for made in (self.six, self.seven):
            _, table = sb.unpack_guest(made['guest'])
            params = [c for c in table if c['kind'] == sb.KIND_PARAMS]
            self.assertEqual(len(params), 1)
            blobs.append(made['guest'][params[0]['file_offset']:params[0]['file_offset'] + params[0]['size']])
        self.assertEqual(blobs[0], blobs[1])
        self.assertEqual(len(blobs[0]), 9232)
        self.assertEqual(len(self.seven['guest']), 118_885_568)

    def test_rung_7s_record_is_under_the_held_limit_and_says_what_the_comparison_found(self):
        text = self.seven['record']
        self.assertLessEqual(len(text.encode()), HELD_LIMIT)
        flat = ' '.join(text.split())
        for needle in ('this one differs in 2 chunks', '350,572 bytes in rung 6\'s and 327,664 here', '395 nodes in rung 6\'s, 352 here', 'The other 44 chunks are rung 6\'s byte for byte, PARAMS among them',
                       'GUEST.DAT is 22,912 bytes smaller than rung 6\'s (118,908,480 to 118,885,568)', 'THE BASE BOARD WITH 43 OF ITS 395 NODES WITHDRAWN', 'guest profile j700ap-26A428-m1-quiet',
                       'label M1RUN-ACKPANIC-Q-RUNG7'):
            self.assertIn(needle, flat)
        self.assertEqual(pp.record_problems(text, Q, r7.kind_facts(self.panel7)), [], 'the record says nothing that a profile guest makes false')


class Hygiene(unittest.TestCase):
    def test_the_cap_is_176_kib_and_the_files_are_under_the_size_of_a_file_of_this_tool(self):
        self.assertEqual(bb.AUTHORISATION_CAP, 11 << 14, 'K15 raised it from 160 KiB to 176 KiB: rung 7\'s record is about 170 KB (rung 6\'s 159 KB, boot 26\'s block, the profile\'s paragraphs)')
        self.assertEqual(M5.HELD_LIMIT, 11 * 16384 - 1024)
        for rel in ('tools/sp11_stick_bundle_rung7.py', 'tools/sp11_stick_bundle_panel_rung7.py', 'tools/sp11_stick_bundle_panel_rung7d.py', 'tools/sp11_stick_bundle_panel_rungs.py',
                    'tools/sp11_stick_bundle_panel_profile.py', 'tools/sp11_stick_bundle_profile.py', 'tools/sp11_stick_bundle_build.py', 'tools/sp11_stick_bundle_build_opts.py',
                    'tools/sp11_stick_bundle_panel.py', 'tools/sp11_stick_bundle_m1.py', 'tools/sp11_stick_bundle_chunks.py', 'tools/sp11_stick_bundle_record_m1_guide.py',
                    'tools/test-sp11-stick-monitor-rung7.py', 'tools/test-sp11-stick-monitor-rung7-words.py', 'tools/test-sp11-m1-guest-profile.py', 'tools/test-sp11-stick-monitor-profile-text.py',
                    'tools/test-sp11-stick-monitor-rung6.py', 'tools/test-sp11-stick-monitor-rung5.py', 'probes/guestprofile_m1.py'):
            text = (ROOT / rel).read_text()
            with self.subTest(rel):
                self.assertLess(len(text.encode()), 51_200)
                self.assertLess(len(text.splitlines()), 1500)
                self.assertEqual(text.splitlines()[1 if text.startswith('#!') else 0].count('SPDX-License-Identifier: MIT'), 1)
                self.assertTrue(text.isascii())
        for rel, cap in (('docs/SP11-STICK-BUILD.md', 37_000), ('docs/SP11-STICK-REAL.md', 33_000), ('docs/SP11-STICK-MONITOR.md', 156_500), ('docs/SP11-M1-GUEST-PROFILES.md', 21_000)):
            self.assertLessEqual(len((ROOT / rel).read_bytes()), cap, rel)


if __name__ == '__main__':
    unittest.main()
