#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of the mechanism of rung 5 of the monitor entry run (docs/SP11-STICK-MONITOR.md section 20, K13): `real --m1-run --panel console --cpu-start ack --let-panic --boot-arg
cpu_boot_timeout_secs=120 --time-box-s 1..60 --sptm-hvc-calls 0` is the one combination that builds the record of the acknowledging monitor, and every other is a named refusal that writes nothing;
the pair must be the choice of the record whichever way round, and the wrapper cap of a pair that was built at 5 s is known by its digest; the label is M1RUN-ACKPANIC-; the guest is read back as
itself and differs from rung 4's in the command line and the box only; the six kinds of record are refused against each other (AUTH_RECORD_MISMATCH); the size of the record and of the files and the
notes (K13, H19). The words of the record are tools/test-sp11-stick-monitor-rung5-words.py's, which takes the fixtures from here. The fixtures of tools/test-sp11-stick-monitor-panel.py and
of the panic suite run here with a scratch directory of their own (build/sp11-monitor-rung5-test-scratch/, deleted at the end).

    python3 tools/test-sp11-stick-monitor-rung5.py

The regeneration script of the session is the end-to-end test with the real J700 26A428 kernelcache and the acknowledging pair. Nothing here runs the unit: Hardware NOT RUN."""
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))

spec = importlib.util.spec_from_file_location('sp11_monitor_panic_tests', TOOLS / 'test-sp11-stick-monitor-panic.py')
PS = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PS)
PT = PS.PT
SCRATCH = ROOT / 'build' / 'sp11-monitor-rung5-test-scratch'
PS.SCRATCH = PT.SCRATCH = PT.BLD.SCRATCH = PT.AUTH.SCRATCH = SCRATCH
pn, rp, r2, r3, r5, m1, rec, BT, ch, sb, bb, BLD, AUTH = PT.pn, PT.pn.rp, PT.pn.r2, PT.pn.r3, PT.pn.r5, PT.m1, PT.rec, PT.BT, PT.ch, PT.sb, PT.bb, PT.BLD, PT.AUTH
r5d = importlib.import_module('sp11_stick_bundle_panel_rung5d')
m1t, m1g = pn.m1t, rec.m1g
STATE, spying = PT.STATE, PT.spying
flat = lambda text: ' '.join(text.split())    # noqa: E731
LABEL = 'r5-test'               # 7 characters: M1RUN-ACKPANIC- and these are 22 of the 23
P, F, W90 = 'cpus=1', 'cpu_boot_timeout_secs=120', 'cpu_boot_timeout_secs=90'
BOXES = (60, 10)                # the box of the main fixture and a shorter one, for the sentences that follow the box (10 is no form of the builder: the fixture alone is built under it)
OLD = PS.KINDS                  # the five kinds before this one: (D9 choice, word, --let-panic)
NAMES = {**PS.NAMES, 'rung5': 'rung5'}
HELD_LIMIT = bb.AUTHORISATION_CAP - 1024
REAL_PAIRS, REAL_FORMS = dict(m1.ACK_PAIRS), ch.ACK_FORMS      # the builder's own tables, kept before a suite puts the stand-in pair into the first and a shorter box into the second
STAND_IN = {}                   # the patches of setUpModule, stopped by tearDownModule


def ack_report():
    """The fabricated build report of the stand-in pair, as the ack pair's would be: the key, the define and the two file names of the choice."""
    def change(report):
        report['cpu_start'] = 'ack'
        report['added'].insert(-1, m1.ACK_DEFINE)
        files = report['files']
        files['SNAPMON-M1-ACK.BIN'], files['SNAPREP-M1-ACK.BIN'] = files.pop('SNAPMON-M1.BIN'), files.pop('SNAPREP-M1.BIN')
    return AUTH.mutated(STATE['fake']['report'], change)


def args5(name, box=60, **over):
    """The arguments of a real build of rung 5: the ack report and its pair, the word, the box and the choice of the pair."""
    args = PT.real_args(name, 'console', facts=BLD.facts_with(LABEL), boot_arg=F, time_box_s=box)
    paths = AUTH.stage(f'ack-{name}', report=ack_report())
    paths.pop('m1_efi')
    args.update(cpu_start='ack', **paths)
    args.update(over)
    return args


def opts5(args, let_panic=True):
    return [*BT.opts('real', **args), *(['--let-panic'] if let_panic else [])]


def record5(name, box=60, let_panic=True, **over):
    """(code, text, err) of the record of rung 5 from the stand-in loader (which writes word 23 from built['let_panic'])."""
    with PS.stand_in():
        return BT.cli(['authorisation-template', 'real', *opts5(args5(name, box, **over), let_panic)])


def build5(name, box=60, approved=None, **over):
    """(code, text, err, out) of a real build of rung 5 with the approved record `approved` (its own by default)."""
    out, args = SCRATCH / 'out' / name, args5(name, box, **over)
    with PS.stand_in():
        text = approved if approved is not None else BT.cli(['authorisation-template', 'real', *opts5(args)])[1]
        argv = ['real', *opts5(args), '--out', out, '--no-manifest', '--authorisation', BT.write(SCRATCH / 'records' / f'{name}.txt', text.encode())]
        return (*BT.cli(argv), out)


def chunks5(box=60, kind=None, name='chunks'):
    """(chunks, what check_launch was given after them) of the guest the builder makes for rung 5, or for `kind` of the older five."""
    got, real = [], m1.check_launch

    def spy(chunks, *rest):
        got.append((chunks, rest))
        return real(chunks, *rest)
    with mock.patch.object(m1, 'check_launch', spy):
        code, text, err = record5(f'{name}-{box}', box) if kind is None else PS.record(f'{name}-{NAMES[kind]}', kind)
    assert code == 0, err
    return got[0]


def setUpModule():
    PT.setUpModule()
    for kind in OLD:
        code, text, err = PS.record(f'setup-{NAMES[kind]}', kind)
        assert code == 0, err
        STATE[NAMES[kind]] = text
    r2.RUNG1_MONITOR = STATE['first_run_pair'][0]       # the first run's real monitor digest: the stand-in pair is not it, and rung 5 says that its own differs
    fake = STATE['fake']
    STAND_IN['pair'] = mock.patch.dict(m1.ACK_PAIRS, {word: (AUTH.sha(fake['image']), AUTH.sha(fake['reporter'])) for word in REAL_PAIRS})     # the stand-in pair is every acknowledging form's pair
    STAND_IN['pair'].start()
    for box in BOXES:
        made = []
        with spying(made), mock.patch.object(ch, 'ACK_FORMS', (*REAL_FORMS, ('console', F, 10))):
            code, text, err = record5(f'setup-{box}', box)
        assert code == 0, err
        STATE[f'r5-{box}'], STATE[f'r5-{box}-panel'] = text, {**made[0], 'run': r2.facts(box, 'ab' * 32, r2.RUNG1_REPORTER)}


def tearDownModule():
    STAND_IN['pair'].stop()
    PT.tearDownModule()


class Options(unittest.TestCase):
    def raises(self, name, function, *args):
        with self.assertRaises(ch.Refused) as caught:
            function(*args)
        self.assertEqual(caught.exception.name, name)
        return caught.exception

    def test_the_acknowledging_monitor_has_two_records_and_every_other_combination_is_a_named_refusal(self):
        self.assertEqual((ch.RUNG5_FORM, m1.RUNG5_FORM, m1.RUNG5_BOX_MAX_S, m1.DECIDED_BOX_S), (('console', F, 60), ('console', F, 60), 60, 5))
        self.assertEqual((m1.BOOT_ARG_CPU_START, m1.CPU_STARTS), ({P: 'stop', F: 'ack', W90: 'ack', 'cpu_boot_timeout_secs=360': 'ack'}, ('stop', 'ack')))
        self.assertEqual((REAL_FORMS, ch.ACK_FORMS, m1.ACK_FORMS), ((('console', F, 60), ('console', W90, 120), ('console', F, 120), ('console', F, 120, ch.RUNG8_PROFILE), ch.RUNG9_FORM),) * 3,
                         'the closed list: exactly these five forms (rung 8 is rung 7\'s box and word with a profile of its own, rung 9 the root form of K17)')
        for word, box in ((F, 60), (W90, 120)):
            m1.check_options(7, False, box, 'console', 0, word, True, 'ack')
        m1.check_options(7, False, 5, 'console', 0, P, True, 'stop')        # rung 4's form is what it was
        m1.check_options(7, False, 5, 'console', 0, P, False, 'stop')       # and rung 3's
        bad = {'no --let-panic': ((5, 'console', 0, F, False, 'ack'), 'LET_PANIC_REFUSED'), 'no word': ((5, 'console', 0, None, True, 'ack'), 'BOOT_ARG_REFUSED'),
               'cpus=1 with the acknowledge': ((5, 'console', 0, P, True, 'ack'), 'BOOT_ARG_REFUSED'), 'the timeout word with the plain monitor': ((5, 'console', 0, F, True, 'stop'), 'BOOT_ARG_REFUSED'),
               'the other timeout word with the plain monitor': ((5, 'console', 0, W90, True, 'stop'), 'BOOT_ARG_REFUSED'),
               'a box of 61 s': ((61, 'console', 0, F, True, 'ack'), 'LET_PANIC_REFUSED'), 'a box of 1 s': ((1, 'console', 0, F, True, 'ack'), 'LET_PANIC_REFUSED'),
               'a box of 5 s': ((5, 'console', 0, F, True, 'ack'), 'LET_PANIC_REFUSED'), 'a box of 10 s': ((10, 'console', 0, F, True, 'ack'), 'LET_PANIC_REFUSED'),
               'a box of 120 s with the word of rung 5': ((120, 'console', 0, F, True, 'ack'), 'GUEST_PROFILE_REFUSED'), 'a box of 60 s with the word of rung 6': ((60, 'console', 0, W90, True, 'ack'), 'LET_PANIC_REFUSED'),
               'a box of 119 s with the word of rung 6': ((119, 'console', 0, W90, True, 'ack'), 'LET_PANIC_REFUSED'), 'a box of 5 s with the word of rung 6': ((5, 'console', 0, W90, True, 'ack'), 'LET_PANIC_REFUSED'),
               'a box of 121 s': ((121, 'console', 0, W90, True, 'ack'), 'OPTIONS_BAD'), 'a box of 0 s': ((0, 'console', 0, F, True, 'ack'), 'OPTIONS_BAD'),
               'the panel handed': ((5, 'handed', 0, F, True, 'ack'), 'BOOT_ARG_REFUSED'), 'the panel withheld': ((5, 'withheld', 0, W90, True, 'ack'), 'BOOT_ARG_REFUSED'),
               'another value': ((5, 'console', 0, 'cpu_boot_timeout_secs=60', True, 'ack'), 'BOOT_ARG_REFUSED'), 'cpumask': ((5, 'console', 0, 'cpumask=1', True, 'ack'), 'BOOT_ARG_REFUSED'),
               'both words': ((5, 'console', 0, f'{P} {F}', True, 'ack'), 'BOOT_ARG_REFUSED'), 'both timeout words': ((60, 'console', 0, f'{F} {W90}', True, 'ack'), 'BOOT_ARG_REFUSED'),
               'a patched kernel': ((5, 'console', 1, F, True, 'ack'), 'OPTIONS_BAD'),
               'rung 4 with a longer box': ((10, 'console', 0, P, True, 'stop'), 'OPTIONS_BAD'), 'rung 4 with a shorter box': ((4, 'console', 0, P, True, 'stop'), 'LET_PANIC_REFUSED')}
        for what, (args, name) in bad.items():
            with self.subTest(what):
                self.raises(name, m1.check_options, 7, False, *args)
        self.raises('OPTIONS_BAD', m1.check_cpu_start, 'ack', False)
        self.raises('LET_PANIC_REFUSED', m1.check_let_panic, True, 'console', F, 5, False, 'ack')
        self.raises('LET_PANIC_REFUSED', m1.check_let_panic, False, 'console', F, 5, True, 'ack')
        self.assertIsNone(m1.check_let_panic(False, 'console', None, 5, True, 'stop'))
        self.assertEqual([ch.let_panic_form_ok('console', F, 60, 'ack'), ch.let_panic_form_ok('console', F, 61, 'ack'), ch.let_panic_form_ok('console', F, 5, 'stop'),
                          ch.let_panic_form_ok('console', P, 5, 'stop'), ch.let_panic_form_ok('console', W90, 120, 'ack'), ch.let_panic_form_ok('console', W90, 60, 'ack'),
                          ch.let_panic_form_ok('console', F, None, 'ack'), ch.let_panic_form_ok('console', W90, None, 'ack'), ch.let_panic_form_ok('console', 'cpu_boot_timeout_secs=1', None, 'ack')],
                         [True, False, False, True, True, False, True, True, False])

    def test_each_bad_form_is_refused_end_to_end_by_name_and_writes_nothing(self):
        cases = {'no --let-panic': ({}, False, 'LET_PANIC_REFUSED'), 'no word': (dict(boot_arg=None), True, 'BOOT_ARG_REFUSED'), 'cpus=1': (dict(boot_arg=P), True, 'BOOT_ARG_REFUSED'),
                 'a box of 61 s': (dict(time_box_s=61), True, 'LET_PANIC_REFUSED'), 'a box of 121 s': (dict(time_box_s=121), True, 'OPTIONS_BAD'),
                 'the plain choice with the ack report': (dict(cpu_start='stop'), True, 'BOOT_ARG_REFUSED'), 'the panel handed': (dict(panel='handed'), True, 'BOOT_ARG_REFUSED')}
        for what, (over, let_panic, name) in cases.items():
            with self.subTest(what):
                code, text, err = record5('bad-form', let_panic=let_panic, **over)
                self.assertEqual((code, BT.refusal(text)), (1, f'REFUSED={name}'), err)
                if let_panic:
                    code, text, err, out = build5('bad-form-built', approved='not a record', **over)
                    self.assertEqual(code, 1)
                    self.assertFalse(out.exists(), 'nothing is written')

    def test_the_pair_must_be_the_choice_of_the_record_whichever_way_round(self):
        plain = AUTH.stage('plain-pair')
        code, text, err = record5('plain-under-ack', **{k: plain[k] for k in ('monitor', 'reporter', 'm1_run')})
        self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=M1_REPORT_MISMATCH'), err)       # the plain pair under the record of rung 5
        panic = PS.args_of('ack-under-panic', PS.PANIC)
        panic.update({k: v for k, v in args5('ack-under-panic').items() if k in ('monitor', 'reporter', 'm1_run')})
        with PS.stand_in():
            code, text, err = BT.cli(['authorisation-template', 'real', *PS.opts_of(panic, PS.PANIC)])
        self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=M1_REPORT_MISMATCH'), err)       # the ack pair under rung 4's record

    def test_the_wrapper_cap_of_the_pair_is_known_by_its_digest_and_a_longer_box_is_refused_for_it(self):
        frozen, low_ack = STATE['first_run_pair'][0], '54305009c913e74af955d76b83ff99b338541498cf0866d67a33a4c53e0b017a'
        self.assertEqual(m1.LOW_CAP_MONITORS, {frozen: 5, low_ack: 5})
        self.assertEqual([m1.box_cap(frozen), m1.box_cap(low_ack), m1.box_cap('00' * 32)], [5, 5, m1.BOX_MAX_S])
        for digest in (frozen, low_ack):
            m1.check_pair_box(5, digest)
            self.raises('OPTIONS_BAD', m1.check_pair_box, 6, digest)
        m1.check_pair_box(60, '00' * 32)
        self.raises('OPTIONS_BAD', m1.check_pair_box, 121, '00' * 32)
        mon = AUTH.sha(Path(args5('cap-probe')['monitor']).read_bytes())
        with mock.patch.dict(m1.LOW_CAP_MONITORS, {mon: 5}):
            code, text, err = record5('cap-of-the-pair', 60)
            self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=OPTIONS_BAD'), err)
            with mock.patch.object(ch, 'ACK_FORMS', (*REAL_FORMS, ('console', F, 5))):      # a 5 s box is no form of the builder: the text of a pair at the cap of 5 is held under a patched list
                code, text, err = record5('cap-of-the-pair-5', 5)
            self.assertEqual(code, 0, err)
            self.assertIn('at most 5 s (the wrapper\'s PARAMS check)', flat(text))
        self.assertIn('at most 120 s (the wrapper\'s PARAMS check)', flat(STATE['r5-60']))
        self.assertIn('at most 5 s (the wrapper\'s PARAMS check)', flat(' '.join(m1t.gic_lines(cap=5))))


class Label(unittest.TestCase):
    def test_the_label_of_the_record_has_its_own_marker_distinct_from_the_others(self):
        self.assertEqual(m1.pinned_label('RUNG5', True, 'console', F, True, 'ack'), 'M1RUN-ACKPANIC-RUNG5')
        self.assertEqual(len('M1RUN-ACKPANIC-RUNG5'), 20)
        self.assertEqual(m1.pinned_label('RUNG4', True, 'console', P, True), 'M1RUN-LETPANIC-RUNG4', 'rung 4\'s label is what it was')
        self.assertEqual(m1.pinned_label('RUNG3', True, 'console', P), 'M1RUN-CPUS1-RUNG3')
        self.assertEqual(m1.pinned_label('RUNG2', True, 'console'), 'M1RUN-CONSOLE-RUNG2')
        self.assertEqual(m1.pinned_label('x' * 8, True, 'console', F, True, 'ack'), 'M1RUN-ACKPANIC-' + 'x' * 8)
        with self.assertRaises(ch.Refused) as caught:
            m1.pinned_label('x' * 9, True, 'console', F, True, 'ack')
        self.assertEqual(caught.exception.name, 'PIN_STRING')
        self.assertEqual(sorted(m1.BOOT_ARGS.values()), ['ACKPANIC-', 'ACKPANIC-', 'ACKPANIC-', 'CPUS1-'], 'the acknowledging records share the marker (three words), and the facts label (RUNG5, RUNG6, RUNG9) and the profile mark tell them apart')
        marks = [m for m in m1.PANEL_MARKS.values() if m] + sorted(set(m1.BOOT_ARGS.values())) + [m1.LET_PANIC_MARK]
        self.assertEqual(len(marks), len(set(marks)))
        self.assertFalse([a for a in marks for b in marks if a != b and a.startswith(b)], 'no marker is the start of another: one label, one kind of record')

    def test_the_label_is_for_the_record_only_and_a_facts_label_with_a_mark_is_refused(self):
        for choice, word, option, start in (('console', F, False, 'ack'), ('console', P, True, 'ack'), ('console', F, True, 'stop'), ('handed', F, True, 'ack'), ('console', None, True, 'ack')):
            with self.subTest(choice=choice, word=word, option=option, start=start), self.assertRaises(ch.Refused) as caught:
                m1.pinned_label('x', True, choice, word, option, start)
            self.assertIn(caught.exception.name, ('LET_PANIC_REFUSED', 'BOOT_ARG_REFUSED'))
        for mark in [m for m in m1.PANEL_MARKS.values() if m] + list(m1.BOOT_ARGS.values()) + [m1.LET_PANIC_MARK]:
            with self.subTest(mark=mark), self.assertRaises(ch.Refused) as caught:
                m1.pinned_label(f'{mark}x', True, 'console', F, True, 'ack')
            self.assertEqual(caught.exception.name, 'PIN_STRING')
            code, text, err = record5('marked-label', facts=BLD.facts_with(f'{mark}x'))
            self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=PIN_STRING'), (mark, err))
        self.assertEqual(m1.pinned_label('ACKPANIC-x', False), 'ACKPANIC-x', 'the other kinds keep the facts label')

    def test_the_label_is_in_the_record_the_pin_and_the_page_words(self):
        self.assertIn(f'(format 1, label M1RUN-ACKPANIC-{LABEL})', STATE['r5-60'].splitlines()[0])
        code, text, err, out = build5('label-r5')
        self.assertEqual(code, 0, (text, err))
        self.assertEqual(BLD.label_of(BLD.pin_of(out)).decode(), f'M1RUN-ACKPANIC-{LABEL}')
        self.assertEqual(sb.check_pin(BLD.pin_of(out)), 0)


class Reader(unittest.TestCase):
    def test_the_guest_is_read_as_what_it_is_and_the_other_lines_are_refused(self):
        self.assertEqual(r5.LINE, '-v debug=0x8 serial=2 cluster_power=0 wdt=-1 cpu_boot_timeout_secs=120')
        self.assertEqual((pn.command_line('console', F), pn.command_line('console', P)), (r5.LINE, rp.LINE))
        got, real = {}, pn.read_panel

        def keep(chunks_, lay, facts, choice, real_, *more, **kw):
            got.update(chunks=chunks_, lay=lay, facts=facts, board=more[0] if more else None)
            return real(chunks_, lay, facts, choice, real_, *more, **kw)
        with mock.patch.object(pn, 'read_panel', keep):
            self.assertEqual(record5('read-back')[0], 0)
        panel = pn.read_panel(got['chunks'], got['lay'], got['facts'], 'console', True, got['board'], F, True)
        self.assertEqual((panel['boot_arg'], panel['let_panic'], panel['cmdline']), (F, True, r5.LINE))
        for asked in (P, None):
            with self.subTest(asked=asked), self.assertRaises(ch.Refused) as caught:
                pn.read_panel(got['chunks'], got['lay'], got['facts'], 'console', True, got['board'], asked, True)
            self.assertEqual(caught.exception.name, 'PANEL_MISMATCH')

    def test_the_guest_of_rung_5_differs_from_rung_4s_in_boot_args_and_params_and_from_the_same_guest_without_the_word_in_word_23_only(self):
        five, four = chunks5(60), chunks5(kind=PS.PANIC)
        a, b = list(five[0]), list(four[0])
        self.assertEqual([c['dest'] for c in a], [c['dest'] for c in b])
        different = {c['kind'] for c, d in zip(a, b) if c.get('data') != d.get('data')}
        self.assertEqual(different, {sb.KIND_PARAMS, sb.KIND_HANDOFF}, 'the PARAMS chunk (the box) and the chunk of the boot_args page (the command line)')
        params = [c for c in a if c['kind'] == sb.KIND_PARAMS][0]['data']
        words = lambda blob: [int.from_bytes(blob[8 * i:8 * i + 8], 'little') for i in range(len(blob) // 8)]    # noqa: E731
        self.assertEqual(len(five[1]), 1, 'check_launch is given the reference of the same build without the option')
        reference = five[1][0]
        self.assertEqual([i for i, (x, y) in enumerate(zip(words(params), words(reference))) if x != y], [PS.WORD], 'word 23 and no other')
        self.assertEqual((words(params)[PS.WORD], words(reference)[PS.WORD]), (1, 0))
        with mock.patch.object(ch, 'ACK_FORMS', (*REAL_FORMS, ('console', F, 5))):      # a shorter box is no form of the builder: the fixture alone is built under it
            shorter = chunks5(5)[0]
        differing = [c['kind'] for c, d in zip(a, shorter) if c.get('data') != d.get('data')]
        self.assertEqual(differing, [sb.KIND_PARAMS], 'the box is a word of PARAMS and nothing else of the guest changes')

    def test_the_key_of_the_guide_and_the_module_of_each_record(self):
        key = 'console+cpu_boot_timeout_secs=120+let_panic'
        self.assertEqual(pn.kind_key({'panel': 'console', 'boot_arg': F, 'let_panic': True}), key)
        self.assertIn(key, m1g.PANEL)
        panel = STATE['r5-60-panel']
        self.assertTrue(panel['let_panic'] and panel['boot_arg'] == F)
        self.assertEqual([pn._rung(panel), pn._rung({**panel, 'boot_arg': P}), pn._rung({**panel, 'boot_arg': P, 'let_panic': False})], [r5, rp, r3])
        self.assertEqual(sorted(k for k in m1g.PANEL if 'let_panic' in k), ['console+cpu_boot_timeout_secs=120+let_panic', 'console+cpu_boot_timeout_secs=120+let_panic+j700ap-26A428-m1-quiet',
                                                                          'console+cpu_boot_timeout_secs=120+let_panic+j700ap-26A428-m1-quiet-rtc', 'console+cpu_boot_timeout_secs=360+let_panic+j700ap-26A428-m1-quiet-rtc-acm',
                                                                          'console+cpu_boot_timeout_secs=360+let_panic+j700ap-26A428-m1-quiet-rtc-acm+isc',
                                                                          'console+cpu_boot_timeout_secs=90+let_panic',
                                                                          'console+cpus=1+let_panic'])


class Kinds(unittest.TestCase):
    def test_a_build_with_the_approved_record_of_another_kind_is_refused_whichever_is_the_guest(self):
        for kind in OLD:
            with self.subTest(guest='rung5', approved=NAMES[kind]):
                code, text, err, out = build5(f'cross-r5-{NAMES[kind]}', approved=STATE[NAMES[kind]])
                self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=AUTH_RECORD_MISMATCH'), err)
                self.assertFalse(out.exists(), 'nothing is written for a record that is not this build\'s')
            with self.subTest(guest=NAMES[kind], approved='rung5'):
                code, text, err, out = PS.build(f'cross-{NAMES[kind]}-r5', kind, approved=STATE['r5-60'])
                self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=AUTH_RECORD_MISMATCH'), err)
                self.assertFalse(out.exists())
        code, text, err, out = build5('own-r5')
        self.assertEqual(code, 0, (text, err))
        self.assertEqual(len({STATE[NAMES[k]] for k in OLD} | {STATE['r5-60'], STATE['r5-10']}), 7, 'seven records, seven texts')

    def test_the_other_five_records_say_nothing_of_rung_5(self):
        for kind in OLD:
            f = flat(STATE[NAMES[kind]])
            with self.subTest(NAMES[kind]):
                for word in ('ACKPANIC', 'WHY RUNG 5', 'rung 5', 'SNAPMON-M1-ACK', 'CPU-START STORES', 'cpu_boot_timeout_secs', 'boot 24', 'Boot 24', 'for this record (the film of the kernel'):
                    self.assertNotIn(word, f)
                self.assertIn('The same 60 s holds after the STAGE=DONE line: a lit page that has stopped there, with or without the SNAPREP page, is FORCE OFF too (section 5c)', f,
                              'the plain forms keep their 60 s: only the acknowledging form says its own wait there (silence_tail)')


class Hygiene(unittest.TestCase):
    def test_the_record_leaves_one_kib_free_under_the_cap_of_the_approved_file(self):
        for box in BOXES:
            self.assertLessEqual(len(STATE[f'r5-{box}'].encode()), HELD_LIMIT, 'rung 3\'s 15/16 rule (H16, H17) cannot hold: 1 KiB is left free, as for rung 4 (H18); the real record is longer by '
                                 'the facts and the regeneration checks it')
        self.assertGreaterEqual(bb.AUTHORISATION_CAP, 147_456, 'rung 5\'s cap (K13); rung 6 raised it again (K14: tools/test-sp11-stick-monitor-rung6.py pins the number)')

    def test_the_modules_and_the_suites_are_under_the_size_of_a_file_of_this_tool(self):
        for rel in ('tools/sp11_stick_bundle_panel_rung5.py', 'tools/sp11_stick_bundle_panel_rung5d.py', 'tools/test-sp11-stick-monitor-rung5.py', 'tools/test-sp11-stick-monitor-rung5-words.py',
                    'tools/sp11_stick_bundle_panel.py', 'tools/sp11_stick_bundle_build.py', 'tools/sp11_stick_bundle_m1.py', 'tools/sp11_stick_bundle_chunks.py', 'tools/sp11_stick_bundle_record.py',
                    'tools/sp11_stick_bundle_record_m1.py', 'tools/sp11_stick_bundle_record_m1_guide.py', 'tools/test-sp11-stick-monitor-panic.py'):
            text = (ROOT / rel).read_text()
            with self.subTest(rel):
                self.assertLess(len(text.encode()), 51_200)
                self.assertLess(len(text.splitlines()), 1500)
                self.assertEqual(text.splitlines()[1 if text.startswith('#!') else 0].count('SPDX-License-Identifier: MIT'), 1)
                self.assertTrue(text.isascii())

    def test_the_notes_hold_k13_the_row_of_the_suites_the_option_and_the_guest(self):
        texts = {name: (ROOT / f'docs/SP11-STICK-{name}.md').read_text() for name in ('MONITOR', 'REAL', 'BUILD')}
        monitor, real, build_ = (' '.join(texts[name].split()) for name in ('MONITOR', 'REAL', 'BUILD'))
        k13 = ' '.join(texts['MONITOR'].split('**K13, rung 5')[1].split('\n\n')[0].split())
        self.assertLess(monitor.index('**K12, rung 4'), monitor.index('**K13, rung 5'))
        for needle in ('`--cpu-start stop|ack`', '`--let-panic`', '`M1RUN-ACKPANIC-`', '`LET_PANIC_REFUSED`', '`BOOT_ARG_REFUSED`', 'H19', 'Caps raised on purpose, nothing cut', '`AUTH_RECORD_MISMATCH`',
                       'LOW_CAP_MONITORS', 'bb34b970...', '`AUTHORISATION_CAP` 131,072 to 147,456'):
            self.assertIn(needle, k13)
        self.assertNotIn('VERIFIED', k13)
        self.assertIn('| H19 | test-sp11-stick-monitor-rung5.py and test-sp11-stick-monitor-rung5-words.py |', monitor)
        self.assertRegex(real, r'\| GUEST\.DAT, rung 5 \(K13\) \| \d[\d,]* bytes, \d+ chunks')
        self.assertRegex(real, r'\| GUEST\.DAT SHA-256, rung 5, box 60, 0 \| [0-9a-f]{64} \|')
        self.assertIn('`--cpu-start ack` with `--let-panic`, `--boot-arg cpu_boot_timeout_secs=120`', build_)
        self.assertIn('`M1RUN-ACKPANIC-`', build_)


if __name__ == '__main__':
    unittest.main()
