#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of the mechanism of rung 6 of the monitor entry run (docs/SP11-STICK-MONITOR.md section 20, K14): `real --m1-run --panel console --cpu-start ack --let-panic --boot-arg
cpu_boot_timeout_secs=90 --time-box-s 120 --sptm-hvc-calls 0` is the second of the two combinations of the acknowledging monitor (the closed list: rung 5's is 120 and 60 s, rung 6's is 90 and
120 s) and every other pair of box and word is a named refusal that writes nothing; each form has its own pair of programs by digest, whichever way round; the label is M1RUN-ACKPANIC-RUNG6; the guest
is read back as itself and differs from rung 5's in four bytes of data (three of the command line, one of the box word of PARAMS); the seven kinds of record are refused against each other
(AUTH_RECORD_MISMATCH); the size of the record, of the files and of the notes (K14, H20). The words of the record are tools/test-sp11-stick-monitor-rung6-words.py's and, for the sentences that the
operator's choice of D5'' and the review of the first text changed, tools/test-sp11-stick-monitor-rung6-edits.py's. The fixtures of
tools/test-sp11-stick-monitor-rung5.py run here, with the stand-in pair as the pair of both forms except where a test says otherwise.

    python3 tools/test-sp11-stick-monitor-rung6.py

The regeneration script of the session is the end-to-end test with the real J700 26A428 kernelcache and the real pair. Nothing here runs the unit: Hardware NOT RUN."""
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))

spec = importlib.util.spec_from_file_location('sp11_monitor_rung5_mechanism', TOOLS / 'test-sp11-stick-monitor-rung5.py')
M5 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M5)
PS, PT, STATE, flat, m1t, m1g = M5.PS, M5.PT, M5.STATE, M5.flat, M5.m1t, M5.m1g
pn, rp, r2, r3, r5, m1, rec, BT, ch, sb, bb, BLD, AUTH = M5.pn, M5.rp, M5.r2, M5.r3, M5.r5, M5.m1, M5.rec, M5.BT, M5.ch, M5.sb, M5.bb, M5.BLD, M5.AUTH
r6 = importlib.import_module('sp11_stick_bundle_panel_rung6')
r6d = importlib.import_module('sp11_stick_bundle_panel_rung6d')
LABEL5, LABEL6 = M5.LABEL, 'r6-test'
P, F, W90, OLD, NAMES, HELD_LIMIT = M5.P, M5.F, M5.W90, M5.OLD, M5.NAMES, M5.HELD_LIMIT
REAL_PAIRS, REAL_FORMS = M5.REAL_PAIRS, M5.REAL_FORMS
SHORTER = ('console', W90, 30)  # a box that no form has, for the sentences that follow the box: the fixture alone is built under it
KINDS = (*(NAMES[k] for k in OLD), 'rung5', 'rung6')


def record6(name, box=120, let_panic=True, **over):
    """(code, text, err) of the record of rung 6 from the stand-in loader and the stand-in pair."""
    return M5.record5(name, box, let_panic, **{'boot_arg': W90, 'facts': BLD.facts_with(LABEL6), **over})


def build6(name, box=120, approved=None, **over):
    """(code, text, err, out) of a real build of rung 6 with the approved record `approved` (its own by default)."""
    return M5.build5(name, box, approved, **{'boot_arg': W90, 'facts': BLD.facts_with(LABEL6), **over})


def chunks6(box=120, name='chunks6'):
    """(chunks, what check_launch was given after them) of the guest the builder makes for rung 6."""
    got, real = [], m1.check_launch

    def spy(chunks, *rest):
        got.append((chunks, rest))
        return real(chunks, *rest)
    with mock.patch.object(m1, 'check_launch', spy):
        code, text, err = record6(f'{name}-{box}', box)
    assert code == 0, err
    return got[0]


def byte_diffs(a, b):
    """[(offset, byte of a, byte of b)] of two byte strings of one length."""
    assert len(a) == len(b), 'one length'
    return [(i, x, y) for i, (x, y) in enumerate(zip(a, b)) if x != y]


def setUpModule():
    M5.setUpModule()
    made = []
    with M5.spying(made):
        code, text, err = record6('setup-120')
    assert code == 0, err
    STATE['r6-120'], STATE['r6-120-panel'] = text, {**made[0], 'run': r2.facts(120, 'ab' * 32, r2.RUNG1_REPORTER)}
    made = []
    with M5.spying(made), mock.patch.object(ch, 'ACK_FORMS', (*REAL_FORMS, SHORTER)):
        code, text, err = record6('setup-30', 30)
    assert code == 0, err
    STATE['r6-30'], STATE['r6-30-panel'] = text, {**made[0], 'run': r2.facts(30, 'ab' * 32, r2.RUNG1_REPORTER)}


def tearDownModule():
    M5.tearDownModule()


class Form(unittest.TestCase):
    def raises(self, name, function, *args):
        with self.assertRaises(ch.Refused) as caught:
            function(*args)
        self.assertEqual(caught.exception.name, name)
        return caught.exception

    def test_the_form_is_the_second_of_a_closed_list_of_two_and_nothing_else_is_admitted(self):
        self.assertEqual((ch.RUNG6_FORM, m1.RUNG6_FORM, m1.RUNG6_BOX_S, m1.BOX_MAX_S), (('console', W90, 120), ('console', W90, 120), 120, 120))
        self.assertEqual(REAL_FORMS, (ch.RUNG5_FORM, ch.RUNG6_FORM), 'rung 5\'s form is the first and rung 6\'s the second, and there is no third')
        self.assertEqual(m1.BOOT_ARG_CPU_START[W90], 'ack')
        self.assertEqual(r6.WORD, W90)
        self.assertEqual(r6.LINE, pn.command_line('console', W90))
        self.assertEqual(r6.LINE, r5.LOADER_LINE + ' ' + W90)
        m1.check_options(7, False, 120, 'console', 0, W90, True, 'ack')
        bad = {'rung 5\'s box': ((60, W90), 'LET_PANIC_REFUSED'), 'a box of 119 s': ((119, W90), 'LET_PANIC_REFUSED'), 'a box of 1 s': ((1, W90), 'LET_PANIC_REFUSED'),
               'a box of 5 s': ((5, W90), 'LET_PANIC_REFUSED'), 'a box of 30 s': ((30, W90), 'LET_PANIC_REFUSED'), 'a box of 121 s': ((121, W90), 'OPTIONS_BAD'),
               'rung 6\'s box with rung 5\'s word': ((120, F), 'LET_PANIC_REFUSED'), 'another timeout': ((120, 'cpu_boot_timeout_secs=91'), 'BOOT_ARG_REFUSED'),
               'a timeout of 9': ((120, 'cpu_boot_timeout_secs=9'), 'BOOT_ARG_REFUSED'), 'cpumask': ((120, 'cpumask=1'), 'BOOT_ARG_REFUSED'), 'cpus=1': ((120, P), 'BOOT_ARG_REFUSED'),
               'both words': ((120, f'{W90} {F}'), 'BOOT_ARG_REFUSED'), 'the word and cpus=1': ((120, f'{W90} {P}'), 'BOOT_ARG_REFUSED'), 'no word': ((120, None), 'BOOT_ARG_REFUSED')}
        for what, ((box, word), name) in bad.items():
            with self.subTest(what):
                self.raises(name, m1.check_options, 7, False, box, 'console', 0, word, True, 'ack')
        for what, (args, name) in {'no --let-panic': ((120, 'console', 0, W90, False, 'ack'), 'LET_PANIC_REFUSED'), 'the plain monitor': ((120, 'console', 0, W90, True, 'stop'), 'BOOT_ARG_REFUSED'),
                                   'the panel handed': ((120, 'handed', 0, W90, True, 'ack'), 'BOOT_ARG_REFUSED'), 'the panel withheld': ((120, 'withheld', 0, W90, True, 'ack'), 'BOOT_ARG_REFUSED'),
                                   'a patched kernel': ((120, 'console', 1, W90, True, 'ack'), 'OPTIONS_BAD'), 'the plain monitor, rung 4\'s word and a long box': ((120, 'console', 0, P, True, 'stop'), 'OPTIONS_BAD')}.items():
            with self.subTest(what):
                self.raises(name, m1.check_options, 7, False, *args)
        for what, (args, ok) in {'the form': (('console', W90, 120, 'ack'), True), 'the form without the box': (('console', W90, None, 'ack'), True), 'the box of rung 5': (('console', W90, 60, 'ack'), False),
                                 'the plain monitor': (('console', W90, 120, 'stop'), False), 'the panel handed': (('handed', W90, 120, 'ack'), False)}.items():
            with self.subTest(what):
                self.assertEqual(ch.let_panic_form_ok(*args), ok)

    def test_each_bad_form_is_refused_end_to_end_by_name_and_writes_nothing(self):
        cases = {'no --let-panic': ({}, False, 'LET_PANIC_REFUSED'), 'no word': (dict(boot_arg=None), True, 'BOOT_ARG_REFUSED'), 'cpus=1': (dict(boot_arg=P), True, 'BOOT_ARG_REFUSED'),
                 'rung 5\'s box': (dict(time_box_s=60), True, 'LET_PANIC_REFUSED'), 'a box of 119 s': (dict(time_box_s=119), True, 'LET_PANIC_REFUSED'),
                 'a box of 5 s': (dict(time_box_s=5), True, 'LET_PANIC_REFUSED'), 'a box of 121 s': (dict(time_box_s=121), True, 'OPTIONS_BAD'),
                 'rung 5\'s word': (dict(boot_arg=F), True, 'LET_PANIC_REFUSED'), 'the plain choice with the ack report': (dict(cpu_start='stop'), True, 'BOOT_ARG_REFUSED'),
                 'the panel handed': (dict(panel='handed'), True, 'BOOT_ARG_REFUSED'), 'cpumask': (dict(boot_arg='cpumask=1'), True, 'BOOT_ARG_REFUSED')}
        for what, (over, let_panic, name) in cases.items():
            with self.subTest(what):
                code, text, err = record6('bad-form', let_panic=let_panic, **over)
                self.assertEqual((code, BT.refusal(text)), (1, f'REFUSED={name}'), err)
                if let_panic:
                    code, text, err, out = build6('bad-form-built', approved='not a record', **over)
                    self.assertEqual(code, 1)
                    self.assertFalse(out.exists(), 'nothing is written')

    def test_the_box_of_a_pair_built_at_the_cap_of_5_is_refused_for_rung_6_as_for_rung_5(self):
        mon = AUTH.sha(Path(M5.args5('cap-probe-6')['monitor']).read_bytes())
        with mock.patch.dict(m1.LOW_CAP_MONITORS, {mon: 5}):
            code, text, err = record6('cap-of-the-pair-6')
            self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=OPTIONS_BAD'), err)
        self.assertIn('at most 120 s (the wrapper\'s PARAMS check)', flat(STATE['r6-120']))


class Pair(unittest.TestCase):
    def test_each_form_has_the_pair_that_its_record_is_written_for_by_digest(self):
        self.assertEqual(sorted(REAL_PAIRS), [F, W90])
        for word, (monitor, reporter) in REAL_PAIRS.items():
            with self.subTest(word):
                self.assertRegex(monitor, r'^[0-9a-f]{64}$')
                self.assertRegex(reporter, r'^[0-9a-f]{64}$')
        self.assertEqual(REAL_PAIRS[F], ('bb34b9701d89302f425138b6229fd334ba9ead54699db2a131fc9a99cb22999c', 'ee86676001d89cf653af7bdddd797fef3c54d31b28ee42eb4a2d7aa692ee4b3b'))
        self.assertEqual(REAL_PAIRS[W90][0], REAL_PAIRS[F][0], 'the monitor is rung 5\'s, byte for byte: the acknowledge, let_panic and the stage 2 map do not change')
        self.assertEqual(m1.ACK_MONITOR_SHA256, REAL_PAIRS[F][0])
        self.assertNotIn(REAL_PAIRS[W90][0], m1.LOW_CAP_MONITORS, 'the box cap of this pair is the builder\'s 120 s')
        if REAL_PAIRS[W90][1] == REAL_PAIRS[F][1]:
            self.skipTest('rung 6\'s reporter is still rung 5\'s in this tree: the table has no digest of a reporter that prints the guest timer\'s words yet')
        self.assertNotEqual(REAL_PAIRS[W90][1], REAL_PAIRS[F][1], 'the reporter of rung 6 is another one')

    def test_check_pair_form_holds_the_acknowledging_forms_to_their_pair_and_leaves_the_plain_monitor_alone(self):
        with mock.patch.dict(m1.ACK_PAIRS, REAL_PAIRS):     # the builder's own table, under the stand-in pair that the fixtures put in its place
            self.check_the_table()

    def check_the_table(self):
        for word, (monitor, reporter) in REAL_PAIRS.items():
            m1.check_pair_form('ack', word, monitor, reporter)
            for what, args in (('another monitor', ('ab' * 32, reporter)), ('another reporter', (monitor, 'cd' * 32)), ('both', ('ab' * 32, 'cd' * 32))):
                with self.subTest(word=word, what=what), self.assertRaises(ch.Refused) as caught:
                    m1.check_pair_form('ack', word, *args)
                self.assertEqual(caught.exception.name, 'M1_REPORT_MISMATCH')
                self.assertNotIn(args[0] + args[1], caught.exception.detail, 'a digest is quoted by its first eight digits only')
        with self.assertRaises(ch.Refused) as caught:
            m1.check_pair_form('ack', 'cpu_boot_timeout_secs=1', 'ab' * 32, 'cd' * 32)
        self.assertEqual(caught.exception.name, 'M1_REPORT_MISMATCH', 'a word that has no pair has none')
        self.assertIsNone(m1.check_pair_form('stop', P, 'ab' * 32, 'cd' * 32), 'the plain monitor has no table: its records name their pair and are held to it by their approved SHA-256')

    def test_a_pair_that_is_the_other_forms_is_refused_both_ways_and_nothing_is_written(self):
        fake, other = STATE['fake'], b'REP2' * 200
        report = AUTH.mutated(M5.ack_report(), lambda r: r['files'].__setitem__('SNAPREP-M1-ACK.BIN', AUTH.entry(other)))
        mon, rep5, rep6 = AUTH.sha(fake['image']), AUTH.sha(fake['reporter']), AUTH.sha(other)
        with mock.patch.dict(m1.ACK_PAIRS, {F: (mon, rep5), W90: (mon, rep6)}):
            own6 = AUTH.stage('pair6', reporter=other, report=report)
            own6.pop('m1_efi')
            code, text, err = record6('own-pair-6', **own6)
            self.assertEqual(code, 0, err)       # rung 6 with its own pair
            code, text, err = record6('rung5-pair-under-6')
            self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=M1_REPORT_MISMATCH'), err)       # rung 5's pair under rung 6
            code, text, err = M5.record5('rung6-pair-under-5', **own6)
            self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=M1_REPORT_MISMATCH'), err)       # rung 6's pair under rung 5
            for builder, over in ((build6, {}), (M5.build5, own6)):
                code, text, err, out = builder('pair-built', approved='not a record', **over)
                self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=M1_REPORT_MISMATCH'), err)
                self.assertFalse(out.exists(), 'nothing is written')

    def test_the_pair_must_be_the_acknowledging_one_whichever_way_round(self):
        plain = AUTH.stage('plain-pair-6')
        code, text, err = record6('plain-under-rung-6', **{k: plain[k] for k in ('monitor', 'reporter', 'm1_run')})
        self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=M1_REPORT_MISMATCH'), err)
        panic = PS.args_of('ack-under-panic-6', PS.PANIC)
        panic.update({k: v for k, v in M5.args5('ack-under-panic-6', 120, boot_arg=W90).items() if k in ('monitor', 'reporter', 'm1_run')})
        with PS.stand_in():
            code, text, err = BT.cli(['authorisation-template', 'real', *PS.opts_of(panic, PS.PANIC)])
        self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=M1_REPORT_MISMATCH'), err)


class Label(unittest.TestCase):
    def test_the_label_of_rung_6_is_distinct_from_every_other_kind_and_fits(self):
        labels = {'withheld': m1.pinned_label('RUNG1', True, 'withheld'), 'handed': m1.pinned_label('RUNG1', True, 'handed'), 'console': m1.pinned_label('RUNG2', True, 'console'),
                  'rung3': m1.pinned_label('RUNG3', True, 'console', P), 'panic': m1.pinned_label('RUNG4', True, 'console', P, True), 'rung5': m1.pinned_label('RUNG5', True, 'console', F, True, 'ack'),
                  'rung6': m1.pinned_label('RUNG6', True, 'console', W90, True, 'ack')}
        self.assertEqual(labels['rung6'], 'M1RUN-ACKPANIC-RUNG6')
        self.assertEqual(len(labels['rung6']), 20)
        self.assertEqual(len(set(labels.values())), 7, 'seven kinds, seven labels')
        self.assertTrue(all(len(label) <= m1.PIN_LABEL_MAX for label in labels.values()))
        self.assertEqual(m1.pinned_label('x' * 8, True, 'console', W90, True, 'ack'), 'M1RUN-ACKPANIC-' + 'x' * 8)
        with self.assertRaises(ch.Refused) as caught:
            m1.pinned_label('x' * 9, True, 'console', W90, True, 'ack')
        self.assertEqual(caught.exception.name, 'PIN_STRING')

    def test_a_facts_label_with_a_mark_and_a_label_of_another_form_are_refused(self):
        for mark in [m for m in m1.PANEL_MARKS.values() if m] + sorted(set(m1.BOOT_ARGS.values())) + [m1.LET_PANIC_MARK]:
            with self.subTest(mark=mark), self.assertRaises(ch.Refused) as caught:
                m1.pinned_label(f'{mark}x', True, 'console', W90, True, 'ack')
            self.assertEqual(caught.exception.name, 'PIN_STRING')
            code, text, err = record6('marked-label-6', facts=BLD.facts_with(f'{mark}x'))
            self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=PIN_STRING'), (mark, err))
        for choice, word, option, start in (('console', W90, False, 'ack'), ('console', P, True, 'ack'), ('console', W90, True, 'stop'), ('handed', W90, True, 'ack'), ('console', None, True, 'ack')):
            with self.subTest(choice=choice, word=word, option=option, start=start), self.assertRaises(ch.Refused) as caught:
                m1.pinned_label('x', True, choice, word, option, start)
            self.assertIn(caught.exception.name, ('LET_PANIC_REFUSED', 'BOOT_ARG_REFUSED'))

    def test_the_label_is_in_the_record_the_pin_and_the_plan(self):
        self.assertIn(f'(format 1, label M1RUN-ACKPANIC-{LABEL6})', STATE['r6-120'].splitlines()[0])
        self.assertIn(f'The plan is named after the label, M1RUN-ACKPANIC-{LABEL6}', flat(STATE['r6-120']))
        code, text, err, out = build6('label-r6')
        self.assertEqual(code, 0, (text, err))
        self.assertEqual(BLD.label_of(BLD.pin_of(out)).decode(), f'M1RUN-ACKPANIC-{LABEL6}')
        self.assertEqual(sb.check_pin(BLD.pin_of(out)), 0)


class Reader(unittest.TestCase):
    def test_the_guest_is_read_as_what_it_is_and_the_other_lines_are_refused(self):
        got, real = {}, pn.read_panel

        def keep(chunks_, lay, facts, choice, real_, *more, **kw):
            got.update(chunks=chunks_, lay=lay, facts=facts, board=more[0] if more else None)
            return real(chunks_, lay, facts, choice, real_, *more, **kw)
        with mock.patch.object(pn, 'read_panel', keep):
            self.assertEqual(record6('read-back-6')[0], 0)
        panel = pn.read_panel(got['chunks'], got['lay'], got['facts'], 'console', True, got['board'], W90, True)
        self.assertEqual((panel['boot_arg'], panel['let_panic'], panel['cmdline']), (W90, True, r6.LINE))
        for asked in (F, P, None):
            with self.subTest(asked=asked), self.assertRaises(ch.Refused) as caught:
                pn.read_panel(got['chunks'], got['lay'], got['facts'], 'console', True, got['board'], asked, True)
            self.assertEqual(caught.exception.name, 'PANEL_MISMATCH', 'the guest of rung 6 is not read as another form\'s')

    def test_the_guest_of_rung_6_differs_from_rung_5s_in_four_bytes_and_from_the_same_guest_without_the_word_in_word_23_only(self):
        six, five = chunks6(120), M5.chunks5(60)
        a, b = list(six[0]), list(five[0])
        self.assertEqual([c['dest'] for c in a], [c['dest'] for c in b])
        different = {c['kind'] for c, d in zip(a, b) if c.get('data') != d.get('data')}
        self.assertEqual(different, {sb.KIND_PARAMS, sb.KIND_HANDOFF}, 'the PARAMS chunk (the box) and the chunk of the boot_args page (the command line), and no other chunk')
        params6 = [c for c in a if c['kind'] == sb.KIND_PARAMS][0]['data']
        params5 = [c for c in b if c['kind'] == sb.KIND_PARAMS][0]['data']
        words = lambda blob: [int.from_bytes(blob[8 * i:8 * i + 8], 'little') for i in range(len(blob) // 8)]    # noqa: E731
        self.assertEqual([i for i, (x, y) in enumerate(zip(words(params6), words(params5))) if x != y], [ch.PARAMS_WATCHDOG], 'the box word and no other')
        self.assertEqual((words(params6)[ch.PARAMS_WATCHDOG], words(params5)[ch.PARAMS_WATCHDOG]), (120, 60))
        self.assertEqual(len(byte_diffs(params6, params5)), 1, 'one byte: 120 and 60 differ in one byte of the little-endian word')
        hand6 = b''.join(bytes(c['data']) for c in a if c['kind'] == sb.KIND_HANDOFF)
        hand5 = b''.join(bytes(c['data']) for c in b if c['kind'] == sb.KIND_HANDOFF)
        diffs = byte_diffs(hand6, hand5)
        self.assertEqual([(x, y) for _, x, y in diffs], [(ord('9'), ord('1')), (ord('0'), ord('2')), (0, ord('0'))], 'the digits 9, 0 and the terminating zero where rung 5 has 1, 2, 0')
        self.assertEqual(diffs[-1][0] - diffs[0][0], 2, 'three bytes in a row')
        self.assertTrue(hand6[diffs[0][0] - len(b'cpu_boot_timeout_secs='):diffs[0][0]] == b'cpu_boot_timeout_secs=')
        self.assertEqual(len(six[1]), 1, 'check_launch is given the reference of the same build without the option')
        self.assertEqual([i for i, (x, y) in enumerate(zip(words(params6), words(six[1][0]))) if x != y], [PS.WORD], 'word 23 and no other')

    def test_the_key_of_the_guide_and_the_module_of_each_record(self):
        key = 'console+cpu_boot_timeout_secs=90+let_panic'
        self.assertEqual(pn.kind_key({'panel': 'console', 'boot_arg': W90, 'let_panic': True}), key)
        self.assertIn(key, m1g.PANEL)
        panel = STATE['r6-120-panel']
        self.assertTrue(panel['let_panic'] and panel['boot_arg'] == W90)
        self.assertEqual([pn._rung(panel), pn._rung({**panel, 'boot_arg': F}), pn._rung({**panel, 'boot_arg': P}), pn._rung({**panel, 'boot_arg': P, 'let_panic': False})], [r6, r5, rp, r3])
        self.assertEqual((r6.NUMBER, r5.NUMBER, pn.ACK), (6, 5, (r5, r6)))
        self.assertIn('counts as a result of rung 6.', flat(STATE['r6-120']))
        self.assertIn('counts as a result of rung 5.', flat(STATE['r5-60']))
        self.assertIn('counts as a result of rung 4.', flat(STATE[NAMES[PS.PANIC]]))


class Kinds(unittest.TestCase):
    def test_a_build_with_the_approved_record_of_another_kind_is_refused_whichever_is_the_guest(self):
        others = {**{NAMES[kind]: STATE[NAMES[kind]] for kind in OLD}, 'rung5': STATE['r5-60']}
        for name, approved in others.items():
            with self.subTest(guest='rung6', approved=name):
                code, text, err, out = build6(f'cross-r6-{name}', approved=approved)
                self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=AUTH_RECORD_MISMATCH'), err)
                self.assertFalse(out.exists(), 'nothing is written for a record that is not this build\'s')
        for kind in OLD:
            with self.subTest(guest=NAMES[kind], approved='rung6'):
                code, text, err, out = PS.build(f'cross-{NAMES[kind]}-r6', kind, approved=STATE['r6-120'])
                self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=AUTH_RECORD_MISMATCH'), err)
                self.assertFalse(out.exists())
        with self.subTest(guest='rung5', approved='rung6'):
            code, text, err, out = M5.build5('cross-r5-r6', approved=STATE['r6-120'])
            self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=AUTH_RECORD_MISMATCH'), err)
            self.assertFalse(out.exists())
        code, text, err, out = build6('own-r6')
        self.assertEqual(code, 0, (text, err))
        self.assertEqual(len({*others.values(), STATE['r6-120'], STATE['r6-30']}), 8, 'seven kinds and a shorter box of rung 6: eight texts')

    def test_the_other_six_records_say_nothing_of_rung_6(self):
        for name, text in {**{NAMES[kind]: STATE[NAMES[kind]] for kind in OLD}, 'rung5': STATE['r5-60']}.items():
            f = flat(text)
            with self.subTest(name):
                for word in ('WHY RUNG 6', 'rung 6', 'RUNG6', 'D5\'\'', 'cpu_boot_timeout_secs=90', 'TIMER PANIC', 'MON TIM', 'MON CLK'):
                    self.assertNotIn(word, f)

    def test_rung_5s_record_is_what_it_was_but_for_the_number_in_its_pointer_to_5d_which_the_regeneration_holds_to_its_approved_digest(self):
        self.assertIn('counts as a result of rung 5.', flat(STATE['r5-60']))
        self.assertIn('The wait is 120 s after the STAGE=DONE line for this record (the film of the kernel\'s text: step 6 of section 5c)', flat(STATE['r5-60']))
        self.assertIn('The wait is 120 s after the STAGE=DONE line for this record (the film of the kernel\'s text: step 6 of section 5c)', flat(STATE['r6-120']))


class Hygiene(unittest.TestCase):
    def test_the_record_leaves_one_kib_free_under_the_cap_of_the_approved_file(self):
        for key in ('r6-120', 'r6-30'):
            self.assertLessEqual(len(STATE[key].encode()), HELD_LIMIT, 'the 15/16 rule of rung 3 cannot hold: 1 KiB is left free (H18); the real record is longer by the facts and the regeneration checks it')
        self.assertEqual(bb.AUTHORISATION_CAP, 163_840)

    def test_the_modules_and_the_suites_are_under_the_size_of_a_file_of_this_tool(self):
        for rel in ('tools/sp11_stick_bundle_panel_rung6.py', 'tools/sp11_stick_bundle_panel_rung6d.py', 'tools/test-sp11-stick-monitor-rung6.py', 'tools/test-sp11-stick-monitor-rung6-words.py',
                    'tools/test-sp11-stick-monitor-rung6-edits.py',
                    'tools/test-sp11-stick-monitor-rung5.py', 'tools/test-sp11-stick-monitor-rung3.py', 'tools/sp11_stick_bundle_panel.py', 'tools/sp11_stick_bundle_build.py',
                    'tools/sp11_stick_bundle_m1.py', 'tools/sp11_stick_bundle_chunks.py', 'tools/sp11_stick_bundle_record.py', 'tools/sp11_stick_bundle_record_m1_guide.py'):
            text = (ROOT / rel).read_text()
            with self.subTest(rel):
                self.assertLess(len(text.encode()), 51_200)
                self.assertLess(len(text.splitlines()), 1500)
                self.assertEqual(text.splitlines()[1 if text.startswith('#!') else 0].count('SPDX-License-Identifier: MIT'), 1)
                self.assertTrue(text.isascii())

    def test_the_notes_hold_k14_the_row_of_the_suites_the_options_and_the_guest(self):
        texts = {name: (ROOT / f'docs/SP11-STICK-{name}.md').read_text() for name in ('MONITOR', 'REAL', 'BUILD')}
        monitor, real, build_ = (' '.join(texts[name].split()) for name in ('MONITOR', 'REAL', 'BUILD'))
        k14 = ' '.join(texts['MONITOR'].split('**K14, rung 6')[1].split('\n\n')[0].split())
        self.assertLess(monitor.index('**K13, rung 5'), monitor.index('**K14, rung 6'))
        for needle in ('--boot-arg cpu_boot_timeout_secs=90', '--time-box-s 120', '`M1RUN-ACKPANIC-RUNG6`', '`m1.ACK_PAIRS`', '`M1_REPORT_MISMATCH`', 'H20', '`AUTH_RECORD_MISMATCH`',
                       '`AUTHORISATION_CAP` 147,456 to 163,840', 'D5\'\'', 'closed list'):
            self.assertIn(needle, k14)
        self.assertNotIn('VERIFIED', k14)
        self.assertIn('| H20 | test-sp11-stick-monitor-rung6.py, test-sp11-stick-monitor-rung6-words.py and test-sp11-stick-monitor-rung6-edits.py |', monitor)
        self.assertRegex(real, r'\| GUEST\.DAT, rung 6 \(K14\) \| \d[\d,]* bytes, \d+ chunks')
        self.assertRegex(real, r'\| GUEST\.DAT SHA-256, rung 6, box 120, 0 \| [0-9a-f]{64} \|')
        self.assertIn('rung 6 (K14) the same with `=90` and `--time-box-s` 120', build_)
        self.assertLess(len(texts['BUILD'].encode()), 37000, 'the cap of the note that rung 3 and the console left: not raised by rung 6')


if __name__ == '__main__':
    unittest.main()
