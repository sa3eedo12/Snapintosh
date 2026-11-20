#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of the mechanism of rung 8 of the monitor entry run (docs/SP11-STICK-MONITOR.md section 20, K16; docs/SP11-M1-GUEST-PROFILES.md sections 5 and 7; EXPERIMENTAL, hardware NOT RUN): `real
--m1-run --panel console --cpu-start ack --let-panic --boot-arg cpu_boot_timeout_secs=120 --time-box-s 120 --guest-profile j700ap-26A428-m1-quiet-rtc --sptm-hvc-calls 0` is the fourth and last form of
the closed list of the acknowledging monitor (rung 5: 60 s and the word 120; rung 6: 120 s and the word 90; rung 7: 120 s, the word 120 and the profile quiet; rung 8: rung 7's box and word with
the profile quiet-rtc), and every other combination of box, word, panel, monitor and profile is a named refusal that writes nothing. Checked here: the closed list as a whole (a product of 5,184
combinations against an independent oracle); the pair by form and M1_REPORT_MISMATCH both ways; the label M1RUN-ACKPANIC-R-RUNG8 among the labels of the ring; the comparison of the finished guest
with the chunk table of rung 7's own bundle (every seeded fault is PANEL_MISMATCH: exactly chunk 2, PARAMS, may differ and only in the three words of the clock model); the builder end to end through
the stand-in loader, with the record of rung 8 and each of the eight other approved records refused against each other (AUTH_RECORD_MISMATCH, nothing written); the sizes and the caps (K16). The
words of the record are tools/test-sp11-stick-monitor-rung8-words.py's, which takes its fixtures from here; `Real` needs downloads/extracted/j700ap/ and the local pair and facts, and SKIPs without
them (a skip is never a pass).

    python3 tools/test-sp11-stick-monitor-rung8.py

The regeneration script of the session (MODE=rung8) is the end-to-end test with the real J700 26A428 kernelcache and the real pair. Nothing here runs the unit: Hardware NOT RUN."""
import contextlib
import hashlib
import importlib.util
import itertools
import struct
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))

spec = importlib.util.spec_from_file_location('sp11_monitor_rung7_mechanism', TOOLS / 'test-sp11-stick-monitor-rung7.py')
M7 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M7)
M6, M5 = M7.M6, M7.M5
SCRATCH = ROOT / 'build' / 'sp11-monitor-rung8-test-scratch'
M5.SCRATCH = M5.PS.SCRATCH = M5.PT.SCRATCH = M5.PT.BLD.SCRATCH = M5.PT.AUTH.SCRATCH = SCRATCH
PS, PT, pn, m1, BT, ch, sb, bb, BLD, AUTH = M5.PS, M5.PT, M5.pn, M5.m1, M5.BT, M5.ch, M5.sb, M5.bb, M5.BLD, M5.AUTH
import devicemodel as dm  # noqa: E402
import guestprofile_m1 as menu  # noqa: E402
import sp11_stick_bundle_profile as gpb  # noqa: E402
import sp11_stick_bundle_rung8 as r8b  # noqa: E402
import sp11_stick_bundle_panel_rung8 as r8  # noqa: E402

STATE, OLD, NAMES, F, P, W90, HELD_LIMIT = M5.STATE, M5.OLD, M5.NAMES, M5.F, M5.P, M5.W90, M5.HELD_LIMIT
Q, RTC, BASE, LABEL8 = gpb.QUIET, gpb.RTC, gpb.DEFAULT, 'r8-t'
ORIGINAL_RUNG1 = M7.ORIGINAL_RUNG1
REAL_PAIRS = M6.REAL_PAIRS
CONTRACT = 0xF715F704F802000E       # the clock model's contract word: probes/devicemodel.py PMU_RTC_CONTRACT, held to it below
WORDS8 = (dm.SPMI_PMU_RTC_V1, menu.RTC_EPOCH_US, dm.PMU_RTC_CONTRACT)


def fake_compare(chunks, table, guest_bytes, nodes):
    """What `compare` returns for the stand-in loader's filler guest (which is not rung 7's: the real comparison has its own tests): rung 7's pins and this build's numbers."""
    return dict(count=46, differing=(2,), params_bytes=9232, model=18, model_at=0x758, model_span=0x18000, words=(r8b.EMPTY_BUS_WORDS, WORDS8), seed=r8b.SEED_TEXT, dt_bytes=(327_664, 327_664),
                nodes=(395, nodes), guest_bytes=(118_885_568, guest_bytes))


@contextlib.contextmanager
def patched(nodes=352):
    """The read-back and the comparison of the profile guest, which need the real loader's tree and rung 7's real guest, under the stand-in loader."""
    with mock.patch.object(gpb, 'read_back', return_value=nodes), mock.patch.object(r8b, 'compare', fake_compare):
        yield


def over8(over):
    """The arguments of a real build of rung 8 over the stand-in loader: rung 5's fixture with the word, the clock profile and the stand-in facts label; a profile of None is dropped."""
    merged = {'boot_arg': F, 'facts': BLD.facts_with(LABEL8), 'guest_profile': RTC, **over}
    return {key: value for key, value in merged.items() if key != 'guest_profile' or value is not None}


def record8(name, box=120, let_panic=True, **over):
    """(code, text, err) of the record of rung 8 from the stand-in loader."""
    with patched():
        return M5.record5(name, box, let_panic, **over8(over))


def build8(name, box=120, approved=None, **over):
    """(code, text, err, out) of a real build of rung 8 with the approved record `approved` (its own by default)."""
    with patched():
        return M5.build5(name, box, approved, **over8(over))


def setUpModule():
    M7.setUpModule()
    code, text, err = record8('setup-r8')
    assert code == 0, err
    STATE['r8'] = text
    made, real_with = [], r8b.with_guest

    def spy(*args):
        made.append(real_with(*args))
        return made[-1]
    with patched(), mock.patch.object(r8b, 'with_guest', spy):
        code, text, err = M5.record5('setup-r8-panel', 120, True, **over8({}))
    assert code == 0, err
    STATE['r8-panel'] = made[0]


def tearDownModule():
    M7.tearDownModule()


def admit(box, panel, hvc, word, let_panic, cpu_start, profile):
    """What the builder does with the options before any guest is built: m1.check_options, then the profile's own check (gpb.check_form), in that order."""
    m1.check_options(7, False, box, panel, hvc, word, let_panic, cpu_start, profile)
    gpb.check_form(profile, word, let_panic, cpu_start, panel, True, box)


# The forms of the records, each in the order of admit(): the older kinds that take any box of 1 to 120 s (the plain monitor's pair cap is the builder's other check: m1.check_pair_box), the three
# older forms with a box of their own (rung 4 with the plain monitor, rung 5 and rung 6), and the two new ones: rung 7 (quiet) and rung 8 (quiet-rtc).
ANY_BOX = {('withheld', 0, None, False, 'stop'), ('handed', 0, None, False, 'stop'), ('console', 0, None, False, 'stop'), ('console', 0, P, False, 'stop')}
OLDER = {(5, 'console', 0, P, True, 'stop'), (60, 'console', 0, F, True, 'ack'), (120, 'console', 0, W90, True, 'ack')}
NEW = {(120, 'console', 0, F, True, 'ack', Q), (120, 'console', 0, F, True, 'ack', RTC)}


def oracle(form):
    """True when `form` has a record, from the closed list of this note and independently of the builder's own checks: the default board named or not for the older kinds, a profile for the new."""
    return 1 <= form[0] <= 120 and ((form[1:6] in ANY_BOX and form[6] in (None, BASE)) or (form[:6] in OLDER and form[6] in (None, BASE)) or form in NEW)


class Form(unittest.TestCase):
    def test_the_form_is_the_fourth_of_a_closed_list_of_four_and_carries_the_clock_profile(self):
        self.assertEqual((ch.RUNG8_FORM, m1.RUNG8_FORM, ch.RUNG8_PROFILE, gpb.RTC, gpb.RUNG8_PROFILES), (('console', F, 120, RTC), ('console', F, 120, RTC), 'j700ap-26A428-m1-quiet-rtc', RTC, (RTC,)))
        self.assertEqual(ch.ACK_FORMS, (ch.RUNG5_FORM, ch.RUNG6_FORM, ch.RUNG7_FORM, ch.RUNG8_FORM, ch.RUNG9_FORM), 'rung 8 is the fourth; rung 9 (K17) is the fifth and there is no sixth')
        self.assertEqual(ch.FORM_PROFILE, {ch.RUNG7_FORM: Q, ch.RUNG8_FORM: RTC, ch.RUNG9_FORM: gpb.ROOT})
        self.assertEqual(gpb.FORM_PROFILES, (Q, RTC, gpb.ROOT))
        self.assertEqual(ch.RUNG8_FORM[:3], ch.RUNG7_FORM, 'rung 8 is rung 7\'s box and word: the profile alone tells the two forms apart')
        self.assertEqual((m1.BOOT_ARGS[F], m1.BOOT_ARG_CPU_START[F], m1.RUNG6_BOX_S, m1.BOX_MAX_S), ('ACKPANIC-', 'ack', 120, 120), 'rung 8 asks rung 6\'s box (D5\'\') and rung 5\'s word')
        self.assertEqual(sorted(m1.BOOT_ARGS), sorted([P, F, W90, 'cpu_boot_timeout_secs=360']), 'one boot argument was added, by K17')
        self.assertEqual(ch.LET_PANIC_FORMS_TEXT.count('--guest-profile'), 3)
        self.assertEqual(gpb.mark_of(RTC), 'R-')
        self.assertEqual(ch.let_panic_form_ok('console', F, 120, 'ack', RTC), True)
        self.assertEqual([ch.let_panic_form_ok('console', F, 120, 'ack', None), ch.let_panic_form_ok('console', F, 60, 'ack', RTC), ch.let_panic_form_ok('console', W90, 120, 'ack', RTC),
                          ch.let_panic_form_ok('console', F, None, 'ack', RTC), ch.let_panic_form_ok('console', F, 120, 'stop', RTC), ch.let_panic_form_ok('console', F, 120, 'ack', 'quiet')],
                         [False, False, False, True, False, False])

    def test_every_combination_of_box_word_panel_monitor_and_profile_is_a_named_refusal_unless_it_is_a_form_of_the_list(self):
        refusals = {'GUEST_PROFILE_REFUSED', 'LET_PANIC_REFUSED', 'BOOT_ARG_REFUSED', 'OPTIONS_BAD'}
        admitted, refused = [], {}
        grid = list(itertools.product((1, 5, 60, 119, 120, 121), ('withheld', 'handed', 'console'), (0, 1), (None, P, F, W90, 'cpumask=1', f'{F} {W90}'), (False, True), ('stop', 'ack'),
                                      (None, BASE, Q, RTC, 'quiet', gpb.NAMES[2])))
        for form in grid:
            try:
                admit(*form)
            except ch.Refused as error:
                refused[form] = error.name
                continue
            admitted.append(form)
        self.assertEqual(set(admitted), {form for form in grid if oracle(form)}, 'exactly the forms of the list are admitted, and nothing else')
        self.assertEqual(set(refused.values()) - refusals, set(), 'a refusal is one of the four names, never a bare exception')
        self.assertEqual((len(grid), len(admitted)), (6 * 3 * 2 * 6 * 2 * 2 * 6, len(grid) - len(refused)))
        self.assertGreater(len(refused), 5000)
        self.assertEqual(refused[(120, 'console', 0, F, True, 'ack', gpb.NAMES[2])], 'GUEST_PROFILE_REFUSED', 'another profile of the menu in rung 8\'s form')
        self.assertEqual(refused[(120, 'console', 0, F, True, 'ack', None)], 'GUEST_PROFILE_REFUSED', 'rung 7\'s box and word without any profile')
        self.assertEqual(refused[(60, 'console', 0, F, True, 'ack', RTC)], 'GUEST_PROFILE_REFUSED', 'rung 5\'s box with the clock')
        self.assertEqual(refused[(120, 'console', 0, W90, True, 'ack', RTC)], 'GUEST_PROFILE_REFUSED', 'rung 6\'s word with the clock')
        self.assertEqual(refused[(120, 'console', 1, F, True, 'ack', RTC)], 'OPTIONS_BAD', 'a patched kernel with the clock')
        self.assertEqual(refused[(120, 'console', 0, F, False, 'ack', RTC)], 'LET_PANIC_REFUSED', 'the clock without --let-panic')

    def test_each_bad_form_is_refused_end_to_end_by_name_and_writes_nothing(self):
        cases = {'no profile': (dict(guest_profile=None), True, 'GUEST_PROFILE_REFUSED'), 'the default board named': (dict(guest_profile=BASE), True, 'GUEST_PROFILE_REFUSED'),
                 'another profile': (dict(guest_profile=gpb.NAMES[2]), True, 'GUEST_PROFILE_REFUSED'), 'an unknown profile': (dict(guest_profile='quiet-rtc'), True, 'GUEST_PROFILE_REFUSED'),
                 'rung 6\'s word': (dict(boot_arg=W90), True, 'GUEST_PROFILE_REFUSED'), 'no word': (dict(boot_arg=None), True, 'BOOT_ARG_REFUSED'), 'cpus=1': (dict(boot_arg=P), True, 'BOOT_ARG_REFUSED'),
                 'rung 5\'s box': (dict(time_box_s=60), True, 'GUEST_PROFILE_REFUSED'), 'a box of 119 s': (dict(time_box_s=119), True, 'LET_PANIC_REFUSED'),
                 'a box of 121 s': (dict(time_box_s=121), True, 'OPTIONS_BAD'), 'the plain choice with the ack report': (dict(cpu_start='stop'), True, 'BOOT_ARG_REFUSED'),
                 'the panel handed': (dict(panel='handed'), True, 'BOOT_ARG_REFUSED'), 'no --let-panic': ({}, False, 'LET_PANIC_REFUSED')}
        for what, (over, let_panic, name) in cases.items():
            with self.subTest(what):
                code, text, err = record8('bad-form', let_panic=let_panic, **over)
                self.assertEqual((code, BT.refusal(text)), (1, f'REFUSED={name}'), err)
                if let_panic:
                    code, text, err, out = build8('bad-form-built', approved='not a record', **over)
                    self.assertEqual(code, 1)
                    self.assertFalse(out.exists(), 'nothing is written')

    def test_rung_7s_record_and_the_older_forms_are_what_they_were_with_the_new_form_in_the_tree(self):
        code, text, err = M7.record7('again-7')
        self.assertEqual((code, text), (0, STATE['r7']), err)
        for what, call in (('rung 6', lambda: M6.record6('again-6')), ('rung 5', lambda: M5.record5('again-5'))):
            code, text, err = call()
            self.assertEqual((code, text), (0, STATE['r6-120'] if what == 'rung 6' else STATE['r5-60']), (what, err))
        for kind in OLD:
            code, text, err = PS.record(f'old-{NAMES[kind]}-with-8', kind, guest_profile=RTC)
            self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=GUEST_PROFILE_REFUSED'), err)
        code, text, err = M5.record5('r5-with-clock', 60, True, guest_profile=RTC)
        self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=GUEST_PROFILE_REFUSED'), err)
        code, text, err = M6.record6('r6-with-clock', guest_profile=RTC)
        self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=GUEST_PROFILE_REFUSED'), err)
        code, text, err = M7.record7('r7-with-clock-box-60', box=60, guest_profile=RTC)
        self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=GUEST_PROFILE_REFUSED'), err)


class Pair(unittest.TestCase):
    def test_rung_8_uses_rung_6s_pair_by_digest_and_the_table_is_keyed_by_form(self):
        self.assertEqual(sorted(REAL_PAIRS), [F, W90, Q, RTC, gpb.ROOT])
        self.assertEqual((REAL_PAIRS[RTC], REAL_PAIRS[Q]), (REAL_PAIRS[W90],) * 2, 'rungs 7 and 8 use rung 6\'s pair')
        self.assertEqual(REAL_PAIRS[RTC], (m1.ACK_MONITOR_SHA256, m1.ACK_REPORTER6_SHA256))
        self.assertEqual((m1.ACK_MONITOR_SHA256[:8], m1.ACK_REPORTER6_SHA256[:8]), ('bb34b970', '533d004c'), 'the pair of boots 26 and 27')

    def test_check_pair_form_holds_rung_8_to_its_pair_both_ways(self):
        with mock.patch.dict(m1.ACK_PAIRS, REAL_PAIRS):
            monitor, reporter6 = REAL_PAIRS[RTC]
            reporter5 = REAL_PAIRS[F][1]
            self.assertIsNone(m1.check_pair_form('ack', F, monitor, reporter6, RTC))
            for what, args in (('rung 5\'s reporter under rung 8', (F, monitor, reporter5, RTC)), ('rung 6\'s pair under rung 5', (F, monitor, reporter6)),
                               ('another monitor', (F, 'ab' * 32, reporter6, RTC)), ('another reporter', (F, monitor, 'cd' * 32, RTC))):
                with self.subTest(what), self.assertRaises(ch.Refused) as caught:
                    m1.check_pair_form('ack', *args)
                self.assertEqual(caught.exception.name, 'M1_REPORT_MISMATCH')

    def test_a_pair_that_is_another_forms_is_refused_and_nothing_is_written(self):
        fake, other = STATE['fake'], b'REP2' * 200
        report = AUTH.mutated(M5.ack_report(), lambda r: r['files'].__setitem__('SNAPREP-M1-ACK.BIN', AUTH.entry(other)))
        mon, rep5, rep6 = AUTH.sha(fake['image']), AUTH.sha(fake['reporter']), AUTH.sha(other)
        with mock.patch.dict(m1.ACK_PAIRS, {F: (mon, rep5), W90: (mon, rep6), Q: (mon, rep6), RTC: (mon, rep6)}):
            own8 = AUTH.stage('pair8', reporter=other, report=report)
            own8.pop('m1_efi')
            code, text, err = record8('own-pair-8', **own8)
            self.assertEqual(code, 0, err)
            code, text, err = record8('rung5-pair-under-8')
            self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=M1_REPORT_MISMATCH'), err)
            code, text, err, out = build8('pair-built', approved='not a record')
            self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=M1_REPORT_MISMATCH'), err)
            self.assertFalse(out.exists(), 'nothing is written')


class Labels(unittest.TestCase):
    def test_the_label_of_rung_8_is_its_own_among_the_ring_and_fits_the_23_characters(self):
        ring = {'panel': m1.pinned_label('RUNG1', True, 'handed'), 'console': m1.pinned_label('RUNG2', True, 'console'), 'rung3': m1.pinned_label('RUNG3', True, 'console', P),
                'rung4': m1.pinned_label('RUNG4', True, 'console', P, True), 'rung5': m1.pinned_label('RUNG5', True, 'console', F, True, 'ack'),
                'rung6': m1.pinned_label('RUNG6', True, 'console', W90, True, 'ack'), 'rung7': m1.pinned_label('RUNG7', True, 'console', F, True, 'ack', profile=Q),
                'rung8': m1.pinned_label('RUNG8', True, 'console', F, True, 'ack', profile=RTC)}
        self.assertEqual((ring['rung8'], len(ring['rung8']), len(set(ring.values()))), ('M1RUN-ACKPANIC-R-RUNG8', 22, 8))
        self.assertEqual(m1.pinned_label('RUNG10', True, 'console', F, True, 'ack', profile=RTC), 'M1RUN-ACKPANIC-R-RUNG10')
        with self.assertRaises(ch.Refused) as caught:
            m1.pinned_label('RUNG100', True, 'console', F, True, 'ack', profile=RTC)
        self.assertEqual(caught.exception.name, 'PIN_STRING')

    def test_a_facts_label_that_starts_with_a_marker_or_a_mark_is_refused_for_every_ack_form(self):
        markers = [m for m in m1.PANEL_MARKS.values() if m] + sorted(set(m1.BOOT_ARGS.values())) + [m1.LET_PANIC_MARK] + [mark for mark in gpb.MARKS.values() if mark]
        for form in (('console', F, True, 'ack', None), ('console', W90, True, 'ack', None), ('console', F, True, 'ack', Q), ('console', F, True, 'ack', RTC)):
            for mark in markers:
                with self.subTest(form=form[1:], mark=mark), self.assertRaises(ch.Refused) as caught:
                    m1.pinned_label(f'{mark}x', True, *form[:4], profile=form[4])
                self.assertEqual(caught.exception.name, 'PIN_STRING')
        code, text, err = record8('marked-label', facts=BLD.facts_with('R-x'))
        self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=PIN_STRING'), err)

    def test_the_label_is_in_the_record_the_pin_and_the_plan(self):
        self.assertIn(f'(format 1, label M1RUN-ACKPANIC-R-{LABEL8})', STATE['r8'].splitlines()[0])
        code, text, err, out = build8('own-r8', approved=STATE['r8'])
        self.assertEqual(code, 0, (text, err))
        self.assertEqual(BLD.label_of(BLD.pin_of(out)).decode(), f'M1RUN-ACKPANIC-R-{LABEL8}')
        self.assertEqual(sb.check_pin(BLD.pin_of(out)), 0)


def params(models, size=m1.PARAMS_BYTES):
    """A stand-in PARAMS chunk: the device model list of struct params with `models` ((kind, base, span, config) each), zero elsewhere."""
    data = bytearray(size)
    struct.pack_into('<Q', data, m1.MODEL_COUNT_AT, len(models))
    for i, (kind, base, span, config) in enumerate(models):
        struct.pack_into('<8Q', data, m1.MODELS_AT + i * m1.MODEL_BYTES, kind, base, span, *config)
    return bytes(data)


BUS = (dm.SPMI_GEN3_EMPTY_V1, 0x308700000, 0x18000, (0x14000, 0x4000, 487, 256, 256))
CLOCK = (dm.SPMI_PMU_RTC_V1, 0x308700000, 0x18000, (0x14000, 0x4000, 487, menu.RTC_EPOCH_US, dm.PMU_RTC_CONTRACT))
OTHER = (dm.SPMI_GEN3_EMPTY_V1, 0x308600000, 0x18000, (0x14000, 0x4000, 492, 256, 256))


def fabricated(clock=CLOCK):
    """A stand-in for rung 7's bundle and a guest that differs from it as rung 8's does: the chunk table of five chunks (HANDOFF, image, PARAMS, DEVICE_TREE, image) and the PARAMS chunk of each."""
    sha = lambda data: hashlib.sha256(data).hexdigest()    # noqa: E731
    seven, eight = params([OTHER, BUS]), params([OTHER, clock])
    rows7 = [(3, 0x1000, 8192, sha(b'page')), (1, 0x3000, 100, sha(b'a')), (5, 0x4000, m1.PARAMS_BYTES, sha(seven)), (4, 0x5000, 900, sha(b'tree')), (1, 0x9000, 50, sha(b'z'))]
    rows8 = [*rows7[:2], (5, 0x4000, m1.PARAMS_BYTES, sha(eight)), *rows7[3:]]
    table = lambda rows: [dict(kind=k, dest=d, size=s, sha256=bytes.fromhex(h)) for k, d, s, h in rows]    # noqa: E731
    return rows7, table(rows8), [dict(kind=5, dest=0x4000, data=eight)], seven


class Compare(unittest.TestCase):
    def run_compare(self, rows7, table, chunks, size=4900):
        with mock.patch.object(r8b, 'CHUNKS7', tuple(rows7)), mock.patch.object(r8b, 'RUNG7_GUEST_BYTES', 5000):
            return r8b.compare(chunks, table, size, 352)

    def refused(self, *args):
        with self.assertRaises(ch.Refused) as caught:
            self.run_compare(*args)
        self.assertEqual(caught.exception.name, 'PANEL_MISMATCH')
        return caught.exception.detail

    def test_the_comparison_reads_one_chunk_and_three_words_of_it_and_nothing_else(self):
        rows7, table, chunks, _ = fabricated()
        diff = self.run_compare(rows7, table, chunks)
        self.assertEqual(diff, dict(count=5, differing=(2,), params_bytes=m1.PARAMS_BYTES, model=1, model_at=m1.MODELS_AT + m1.MODEL_BYTES, model_span=0x18000, words=((9, 256, 256), WORDS8),
                                    seed='2026-11-20T00:00:00Z', dt_bytes=(900, 900), nodes=(395, 352), guest_bytes=(5000, 4900)))

    def test_each_seeded_fault_of_the_guest_is_panel_mismatch_by_name(self):
        rows7, table, chunks, seven = fabricated()
        sha = lambda data: hashlib.sha256(data).digest()    # noqa: E731
        other = lambda i, **kw: [{**row, **kw} if n == i else row for n, row in enumerate(table)]    # noqa: E731
        data = lambda models: [dict(kind=5, dest=0x4000, data=params(models))]    # noqa: E731
        reseeded = lambda **kw: fabricated(CLOCK[:3] + ((*CLOCK[3][:3], *kw.get('words', CLOCK[3][3:])),))     # noqa: E731
        faults = {'HANDOFF differs': (other(0, sha256=sha(b'other')), chunks), 'DEVICE_TREE differs': (other(3, sha256=sha(b'other')), chunks), 'an image chunk differs': (other(4, sha256=sha(b'o')), chunks),
                  'a chunk is shorter': (other(1, size=99), chunks), 'a chunk is elsewhere': (other(1, dest=0x3001), chunks), 'a chunk is missing': (table[:-1], chunks),
                  'a chunk is added': ([*table, table[-1]], chunks),
                  'PARAMS is rung 7\'s (no clock at all)': (other(2, sha256=bytes.fromhex(rows7[2][3])), [dict(kind=5, dest=0x4000, data=seven)]),
                  'no data for PARAMS': (table, [dict(kind=5, dest=0x4000)]), 'PARAMS is shorter than its table says': (table, [dict(kind=5, dest=0x4000, data=chunks[0]['data'][:-8])]),
                  'a fourth word of the model differs': (table, data([OTHER, (CLOCK[0], CLOCK[1], CLOCK[2], (0x14001, *CLOCK[3][1:]))])),
                  'another model differs': (table, data([(OTHER[0], OTHER[1], OTHER[2], (*OTHER[3][:2], 493, 256, 256)), CLOCK])),
                  'the clock sits at another base': (table, data([OTHER, (CLOCK[0], 0x308600000, CLOCK[2], CLOCK[3])])),
                  'a second clock': (table, data([(CLOCK[0], OTHER[1], OTHER[2], OTHER[3]), CLOCK])), 'the bus is bound twice': (table, data([BUS, CLOCK])),
                  'the model count is zero': (table, [dict(kind=5, dest=0x4000, data=bytes(m1.PARAMS_BYTES))]),
                  'the model count is past the list': (table, [dict(kind=5, dest=0x4000, data=struct.pack('<Q', 10_000).join([bytes(m1.MODEL_COUNT_AT), bytes(m1.PARAMS_BYTES - m1.MODEL_COUNT_AT - 8)]))])}
        for what, (rows, chunk_data) in faults.items():
            with self.subTest(what):
                self.refused(rows7, rows, chunk_data)
        for what, words in {'another seed': (menu.RTC_EPOCH_US + 1, dm.PMU_RTC_CONTRACT), 'an unseeded clock': (0, dm.PMU_RTC_CONTRACT), 'another contract word': (menu.RTC_EPOCH_US, CONTRACT + 1)}.items():
            with self.subTest(what):
                rows, tab, data_, _ = reseeded(words=words)
                self.refused(rows, tab, data_)
        self.assertEqual(len(seven), m1.PARAMS_BYTES)

    def test_the_pins_are_rung_7s_own_bundle_46_chunks_and_the_three_words_are_the_empty_bus_and_the_clock(self):
        self.assertEqual(len(r8b.CHUNKS7), 46)
        self.assertEqual((r8b.CHUNKS7[0][0], r8b.CHUNKS7[2][0], r8b.CHUNKS7[3][0]), (sb.KIND_HANDOFF, sb.KIND_PARAMS, sb.KIND_DEVICE_TREE))
        self.assertEqual((r8b.DIFFERING, r8b.PARAMS_AT, r8b.CHUNKS7[3][2], r8b.RUNG7_GUEST_BYTES), ((2,), 2, 327_664, 118_885_568))
        self.assertTrue(all(len(h) == 64 and (h == '0' * 64 or h.strip('0123456789abcdef') == '') for _, _, _, h in r8b.CHUNKS7))
        self.assertEqual((r8b.RUNG7_GUEST[:16], r8b.RUNG7_RECORD[:16], r8b.RUNG7_LABEL), ('25f74cb8cedb5e20', 'e42f451de33c2d44', 'M1RUN-ACKPANIC-Q-RUNG7'), 'boot 27\'s guest, record and label')
        self.assertEqual((r8b.EMPTY_BUS_WORDS, WORDS8[0], r8b.CLOCK_BASE, dm.PMU_RTC_CONTRACT), ((9, 256, 256), 48, 0x308700000, CONTRACT))
        self.assertEqual((r8b.WORD_OFFSETS[0][1], r8b.WORD_OFFSETS[1][1], r8b.WORD_OFFSETS[2][1]), (0, 48, 56), 'the kind, config[3] and config[4] of struct device_model {kind, base, span, config[5]}')
        self.assertEqual(r8b.SEED_TEXT, '2026-11-20T00:00:00Z')
        self.assertEqual(menu.RTC_EPOCH_US, 1_795_132_800_000_000)


class Builder(unittest.TestCase):
    def test_the_record_of_rung_8_is_built_with_its_label_and_is_under_the_held_limit(self):
        text, panel = STATE['r8'], STATE['r8-panel']
        self.assertLessEqual(len(text.encode()), HELD_LIMIT)
        self.assertTrue(text.isascii())
        self.assertEqual((panel['profile'], panel['boot_arg'], panel['let_panic'], sorted(panel['rung8'])), (RTC, F, True, ['diff', 'guest_bytes', 'guest_sha16', 'label']))
        self.assertNotIn('rung7', panel, 'the panel of rung 8 has no comparison with rung 6')
        self.assertEqual(pn.kind_key({'panel': 'console', 'boot_arg': F, 'let_panic': True, 'panel_facts': panel}), f'console+{F}+let_panic+{RTC}')
        self.assertIs(pn._rung(panel), r8)

    def test_rung_7s_profile_still_takes_rung_7s_comparison_and_the_dispatch_is_by_the_profile_alone(self):
        calls = []
        with mock.patch.object(r8b, 'with_guest', lambda *a: calls.append('r8') or {'rung8': 1}), mock.patch.object(M7.r7b, 'compare', lambda *a: calls.append('r7') or {}):
            self.assertEqual(M7.r7b.with_guest({}, RTC, {'build_label': 'x'}, [], [], b'', 352), {'rung8': 1})
            M7.r7b.with_guest({}, Q, {'build_label': 'x'}, [], [], b'', 352)
        self.assertEqual(calls, ['r8', 'r7'])

    def test_a_build_with_the_approved_record_of_another_kind_is_refused_whichever_is_the_guest(self):
        others = {**{NAMES[kind]: STATE[NAMES[kind]] for kind in OLD}, 'rung5': STATE['r5-60'], 'rung6': STATE['r6-120'], 'rung7': STATE['r7']}
        self.assertEqual(len({*others.values(), STATE['r8']}), 9, 'nine records, nine texts')
        for name, approved in others.items():
            with self.subTest(guest='rung8', approved=name):
                code, text, err, out = build8(f'cross-r8-{name}', approved=approved)
                self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=AUTH_RECORD_MISMATCH'), err)
                self.assertFalse(out.exists(), 'nothing is written for a record that is not this build\'s')
        for kind in OLD:
            with self.subTest(guest=NAMES[kind], approved='rung8'):
                code, text, err, out = PS.build(f'cross-{NAMES[kind]}-r8', kind, approved=STATE['r8'])
                self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=AUTH_RECORD_MISMATCH'), err)
                self.assertFalse(out.exists())
        for guest, builder in (('rung5', lambda: M5.build5('cross-r5-r8', approved=STATE['r8'])), ('rung6', lambda: M6.build6('cross-r6-r8', approved=STATE['r8'])),
                               ('rung7', lambda: M7.build7('cross-r7-r8', approved=STATE['r8']))):
            with self.subTest(guest=guest, approved='rung8'):
                code, text, err, out = builder()
                self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=AUTH_RECORD_MISMATCH'), err)
                self.assertFalse(out.exists())

    def test_the_approved_record_of_rung_8_builds_its_own_bundle_and_the_box_of_a_low_cap_pair_is_refused(self):
        code, text, err, out = build8('own-bundle', approved=STATE['r8'])
        self.assertEqual(code, 0, (text, err))
        mon = AUTH.sha(Path(M5.args5('cap-probe-8')['monitor']).read_bytes())
        with mock.patch.dict(m1.LOW_CAP_MONITORS, {mon: 5}):
            code, text, err = record8('cap-of-the-pair')
            self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=OPTIONS_BAD'), err)

    def test_the_read_back_is_told_the_clock_profile_once_per_build(self):
        with mock.patch.object(gpb, 'read_back', return_value=352) as read_back, mock.patch.object(r8b, 'compare', fake_compare):
            code, text, err = M5.record5('read-back-8', 120, True, **over8({}))
        self.assertEqual(code, 0, err)
        self.assertEqual([c.args[2] for c in read_back.call_args_list], [RTC])


PAIR = ROOT / 'build/r11/ackpair/wrap-a'
LOCAL7, LOCAL8 = ROOT / 'build/m1p/sp11-facts-rung7.local.json', ROOT / 'build/m1p/sp11-facts-rung8.local.json'
INPUTS = (ROOT / 'downloads/extracted/j700ap/kernelcache.release.mac17p', PAIR / 'SNAPMON-M1-ACK.BIN', PAIR / 'SNAPREP-M1-ACK.BIN', PAIR / 'build-report.json', ROOT / 'build/sp11-m1-descriptor.json',
          LOCAL7, LOCAL8)
needs_inputs = unittest.skipUnless(all(p.exists() for p in INPUTS), 'no kernelcache, rung 6 pair, descriptor or local facts of rungs 7 and 8 (git-ignored local inputs)')


@needs_inputs
class Real(unittest.TestCase):
    """Rung 7's and rung 8's guest from the real J700 26A428 kernelcache, in this process (about 10 s each): rung 7's guest and record are the pinned ones (the guest that boot 27 ran), rung 8's
    differs from rung 7's in chunk 2 only and in three words of one device model, and its record says what the comparison found."""
    EFI = M7.Real.EFI

    @classmethod
    def prepare(cls, eight):
        rel = lambda p: str(Path(p).relative_to(ROOT))    # noqa: E731
        argv = ['authorisation-template', 'real', '--facts', rel(LOCAL8 if eight else LOCAL7), '--base', '0x880000000', '--guest-mib', '384', '--monitor', rel(PAIR / 'SNAPMON-M1-ACK.BIN'),
                '--reporter', rel(PAIR / 'SNAPREP-M1-ACK.BIN'), '--m1-run', rel(PAIR / 'build-report.json'), '--stores', 'ram,el2_sysreg,gic', '--time-box-s', '120', '--kernelcache', rel(INPUTS[0]),
                '--descriptor', rel(INPUTS[4]), '--panel', 'console', '--sptm-hvc-calls', '0', '--m1-efi', cls.EFI[0], '--m1-efi', cls.EFI[1], '--let-panic', '--cpu-start', 'ack',
                '--boot-arg', F, '--guest-profile', RTC if eight else Q]
        got, panels = [], []

        def spy(*args):
            got.append(real_compare(*args))
            return got[-1]

        def spy_panel(*args):
            panels.append(real_with(*args))
            return panels[-1]
        real_compare, real_with = r8b.compare, r8b.with_guest
        with contextlib.chdir(ROOT), mock.patch.dict(m1.ACK_PAIRS, REAL_PAIRS), mock.patch.object(r8b, 'compare', spy), mock.patch.object(r8b, 'with_guest', spy_panel), \
                mock.patch.object(M5.r2, 'RUNG1_MONITOR', ORIGINAL_RUNG1[0]), mock.patch.object(M5.r2, 'RUNG1_REPORTER', ORIGINAL_RUNG1[1]):
            made = bb.prepare(bb.build_parser().parse_args(argv), 'real')
        return made, (got[0] if got else None), (panels[0] if panels else None)

    @classmethod
    def setUpClass(cls):
        cls.seven, _, _ = cls.prepare(False)
        cls.eight, cls.diff, cls.panel8 = cls.prepare(True)

    def test_rung_7s_record_and_guest_are_the_pinned_ones_and_its_chunk_table_is_the_pinned_table(self):
        self.assertEqual(hashlib.sha256(self.seven['record'].encode()).hexdigest(), r8b.RUNG7_RECORD)
        self.assertEqual(hashlib.sha256(self.seven['guest']).hexdigest(), r8b.RUNG7_GUEST)
        _, table = sb.unpack_guest(self.seven['guest'])
        self.assertEqual([(c['kind'], c['dest'], c['size'], bytes(c['sha256']).hex()) for c in table], list(r8b.CHUNKS7))

    def test_rung_8s_guest_differs_from_rung_7s_in_one_chunk_and_three_words_of_the_clock_model(self):
        self.assertEqual(self.diff, dict(count=46, differing=(2,), params_bytes=9232, model=18, model_at=0x758, model_span=0x18000, words=((9, 256, 256), WORDS8), seed='2026-11-20T00:00:00Z',
                                         dt_bytes=(327_664, 327_664), nodes=(395, 352), guest_bytes=(118_885_568, len(self.eight['guest']))))
        blobs = []
        for made in (self.seven, self.eight):
            _, table = sb.unpack_guest(made['guest'])
            params_ = [c for c in table if c['kind'] == sb.KIND_PARAMS]
            self.assertEqual(len(params_), 1)
            blobs.append(made['guest'][params_[0]['file_offset']:params_[0]['file_offset'] + params_[0]['size']])
        at = [i for i in range(len(blobs[0])) if blobs[0][i] != blobs[1][i]]
        self.assertEqual(len(blobs[0]), 9232)
        self.assertEqual({i // 8 for i in at}, {(0x758 + off) // 8 for off in (0, 48, 56)}, 'exactly the three words of the model at index 18, at offsets 0, 48 and 56')
        self.assertEqual(len(self.eight['guest']), 118_885_568)

    def test_rung_8s_record_is_under_the_held_limit_and_says_what_the_comparison_found(self):
        text = self.eight['record']
        self.assertLessEqual(len(text.encode()), HELD_LIMIT)
        flat = ' '.join(text.split())
        for needle in ('this one differs in 1 chunk', 'chunk 2, PARAMS (9,232 bytes), in three words of one of its 120 device models, the one at index 18', 'the kind, 9 (the empty bus) in rung 7\'s and 48 '
                       '(the PMU\'s clock) here', 'config[3], 256 (the queue depth) in rung 7\'s and 1,795,132,800,000,000 here', 'config[4], 256 in rung 7\'s and 0XF715F704F802000E here',
                       'The other 45 chunks are rung 7\'s byte for byte', 'GUEST.DAT is as long as rung 7\'s (118,885,568 bytes)', 'THE CLOCK MODEL REFUSED AN ACCESS: MON STOP UNNAMED with B= in 0X308700000..0X308718000',
                       'label M1RUN-ACKPANIC-R-RUNG8'):
            self.assertIn(needle, flat)
        self.assertEqual(r8.record_problems(text, self.panel8), [], 'the record says nothing that a profile guest makes false')


class Hygiene(unittest.TestCase):
    def test_the_cap_is_176_kib_the_record_fits_it_and_the_files_are_under_the_size_of_a_file_of_this_tool(self):
        self.assertGreaterEqual(bb.AUTHORISATION_CAP, 11 << 14, 'unchanged by K16 (rung 8\'s record is about 178 KB, under the held limit of 175 KiB); K17 raised it for rung 9\'s')
        self.assertEqual(M5.HELD_LIMIT, bb.AUTHORISATION_CAP - 1024)
        self.assertLessEqual(len(STATE['r8'].encode()), HELD_LIMIT)
        for rel in ('tools/sp11_stick_bundle_rung8.py', 'tools/sp11_stick_bundle_panel_rung8.py', 'tools/sp11_stick_bundle_panel_rung8d.py', 'tools/sp11_stick_bundle_panel_rungs.py',
                    'tools/sp11_stick_bundle_profile.py', 'tools/sp11_stick_bundle_build_opts.py', 'tools/sp11_stick_bundle_panel.py', 'tools/sp11_stick_bundle_m1.py', 'tools/sp11_stick_bundle_chunks.py',
                    'tools/sp11_stick_bundle_record_m1_guide.py', 'tools/sp11_stick_bundle_record.py', 'tools/test-sp11-stick-monitor-rung8.py', 'tools/test-sp11-stick-monitor-rung8-words.py',
                    'tools/test-sp11-stick-monitor-rung7.py', 'tools/test-sp11-stick-monitor-rung7-words.py', 'tools/test-sp11-m1-rtc-profile.py'):
            text = (ROOT / rel).read_text()
            with self.subTest(rel):
                self.assertLess(len(text.encode()), 51_200)
                self.assertLess(len(text.splitlines()), 1500)
                self.assertEqual(text.splitlines()[1 if text.startswith('#!') else 0].count('SPDX-License-Identifier: MIT'), 1)
                self.assertTrue(text.isascii())
        for rel, cap in (('docs/SP11-STICK-BUILD.md', 37_000), ('docs/SP11-STICK-REAL.md', 33_000), ('docs/SP11-STICK-MONITOR.md', 166_000), ('docs/SP11-M1-GUEST-PROFILES.md', 24_500)):
            self.assertLessEqual(len((ROOT / rel).read_bytes()), cap, rel)


if __name__ == '__main__':
    unittest.main()
