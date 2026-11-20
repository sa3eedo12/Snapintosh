#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of the mechanism of rung 9, the first root-mount form of the monitor entry run (docs/SP11-STICK-ROOT.md section 9, K17; docs/SP11-M1-GUEST-PROFILES.md section 8; EXPERIMENTAL, hardware
NOT RUN): `real --m1-run --panel console --cpu-start ack --let-panic --boot-arg cpu_boot_timeout_secs=360 --time-box-s 300 --guest-profile j700ap-26A428-m1-quiet-rtc-acm --ramdisk ... --root-hash ...
--trust-cache ... --bigfile` is the fifth and last form of the closed list of the acknowledging monitor, and every other combination of box, word, panel, monitor, profile and root is a named
refusal that writes nothing. Checked here: the closed list as a whole (a product of 22,176 combinations against an independent oracle); the pair of the root carry by form and M1_REPORT_MISMATCH
both ways; the label M1RUN-ACKPANIC-M-RUNG9 among the labels of the ring; the comparison of the finished chunk table with the pinned table of rung 8's own bundle (every seeded fault is PANEL_MISMATCH,
on a small universe that has the same rules); the streaming GUEST.DAT against `pack_guest` at every window and the write-and-read-back of the root form (a seeded wrong byte, a wrong size and a wrong
digest are refused); the builder end to end through the stand-in loader, with the record of rung 9 and each of the nine other approved records refused against each other (AUTH_RECORD_MISMATCH, nothing
written); the sizes and the caps (K17). The words of the record are tools/test-sp11-stick-monitor-rung9-words.py's, which takes its fixtures from here; `Real` needs downloads/extracted/j700ap/ and the
local pair and facts, and SKIPs without them (a skip is never a pass).

    python3 tools/test-sp11-stick-monitor-rung9.py

The regeneration script of the session (MODE=rung9) is the end-to-end test with the real J700 26A428 kernelcache, the real root and the real pair. Nothing here runs the unit: Hardware NOT RUN."""
import contextlib
import hashlib
import importlib.util
import itertools
import os
import struct
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))

spec = importlib.util.spec_from_file_location('sp11_monitor_rung8_mechanism', TOOLS / 'test-sp11-stick-monitor-rung8.py')
M8 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M8)
M7, M6, M5 = M8.M7, M8.M6, M8.M5
SCRATCH = ROOT / 'build' / 'sp11-monitor-rung9-test-scratch'
M5.SCRATCH = M5.PS.SCRATCH = M5.PT.SCRATCH = M5.PT.BLD.SCRATCH = M5.PT.AUTH.SCRATCH = SCRATCH
PS, PT, pn, m1, BT, ch, sb, bb, BLD, AUTH = M5.PS, M5.PT, M5.pn, M5.m1, M5.BT, M5.ch, M5.sb, M5.bb, M5.BLD, M5.AUTH
import guestprofile_m1 as menu  # noqa: E402
import sp11_stick_bundle_profile as gpb  # noqa: E402
import sp11_stick_bundle_root as root  # noqa: E402
import sp11_stick_bundle_rootbundle as rb  # noqa: E402
import sp11_stick_bundle_rung9 as r9b  # noqa: E402
import sp11_stick_bundle_panel_rung9 as r9  # noqa: E402
import sp11_stick_bundle_source as src  # noqa: E402

STATE, OLD, NAMES, F, P, W90, HELD_LIMIT = M5.STATE, M5.OLD, M5.NAMES, M5.F, M5.P, M5.W90, M5.HELD_LIMIT
Q, RTC, ROOTP, BASE = gpb.QUIET, gpb.RTC, gpb.ROOT, gpb.DEFAULT
W360, LABEL9 = 'cpu_boot_timeout_secs=360', 'r9-t'
REAL_PAIRS = M6.REAL_PAIRS
MIB = 1 << 20
ROOT_ARGS = dict(ramdisk='stand-in.apfs', root_hash='stand-in.root-hash', trust_cache='stand-in.trustcache')
# What `compare` returns for the real guest (the numbers of the session's host build, which the regeneration script checks against the real kernelcache and the real root).
REAL_DIFF = dict(count=339, same=r9b.SAME, patched=r9b.PATCHED, resized={35: (25_200, 26_736), 39: (442_368, 6_701_056), 40: (507_904, 3_522_560), 44: (49_152, 98_304)}, removed=r9b.REMOVED,
                 new=r9b.NEW, outside=48, handoff_runs=14, handoff_bytes=38, params_words=9, trust_words=3, dt_bytes=(327_664, 328_080), table_room=685,
                 root=dict(chunks=291, zero=143, data_bytes=1_798_293_088, zero_bytes=63_977_888, dest=r9b.RAMDISK_AT, size=r9b.ROOT_BYTES, end=r9b.RAMDISK_AT + r9b.ROOT_BYTES))


def fake_compare(chunks, table, guest_bytes, nodes):
    """What `compare` returns for the stand-in loader's filler guest (which is not rung 8's: the real comparison has its own tests below): the real numbers, with this build's node count."""
    return dict(REAL_DIFF, nodes=(395, nodes), guest_bytes=(r9b.RUNG8_GUEST_BYTES, 1_926_621_440))


def stand_in_guest(a, xb, place, facts, profile):
    """The root loader replaced by the stand-in loader's filler guest, with the node count of the real tree (352) and the credential manager's argument that the real loader adds to the command line
    of this profile's build."""
    built = bb.real_built(a, xb, place, facts)
    at = ch.layout_of(xb).handoff_pa + pn.BOOT_ARGS_AT
    page = bytearray(built['chunks'][at])
    end = page.index(b'\0', page.index(b'-v debug'))
    page[end:end + len(rb.CMDLINE_EXTRA)] = rb.CMDLINE_EXTRA.encode()
    built['chunks'][at] = bytes(page)
    return built, 352


@contextlib.contextmanager
def patched():
    """The gate, the root loader and the comparison of the finished guest, which need the real kernelcache and the real root, under the stand-in loader."""
    fake = STATE['fake']
    with mock.patch.dict(os.environ, {root.GATE_ENV: '1'}), mock.patch.object(rb, 'guest', stand_in_guest), mock.patch.object(r9b, 'compare', fake_compare), \
            mock.patch.dict(m1.ROOT_CARRY_PAIRS, {AUTH.sha(fake['image']): AUTH.sha(fake['reporter'])}):
        yield


def over9(over):
    """The arguments of a build of rung 9 over the stand-in loader: rung 5's fixture with the word, the root-carry profile, the root's three inputs and the stand-in facts label; a key that is None is
    dropped, and `bigfile` is a flag (opts9)."""
    merged = {'boot_arg': W360, 'facts': BLD.facts_with(LABEL9), 'guest_profile': ROOTP, 'guest_mib': 6144, 'time_box_s': 300, **ROOT_ARGS, 'bigfile': True, **over}
    return {key: value for key, value in merged.items() if value is not None}


def opts9(args, let_panic=True):
    flag = args.pop('bigfile', False)
    return [*M5.opts5(args, let_panic), *(['--bigfile'] if flag else [])]


def record9(name, let_panic=True, **over):
    """(code, text, err) of the record of rung 9 from the stand-in loader."""
    args = M5.args5(name, 300, **over9(over))
    with patched(), PS.stand_in():
        return BT.cli(['authorisation-template', 'real', *opts9(args, let_panic)])


def build9(name, approved=None, **over):
    """(code, text, err, out) of a build of rung 9 with the approved record `approved` (its own by default)."""
    out, args = SCRATCH / 'out' / name, M5.args5(name, 300, **over9(over))
    with patched(), PS.stand_in():
        text = approved if approved is not None else BT.cli(['authorisation-template', 'real', *opts9(dict(args))])[1]
        argv = ['real', *opts9(dict(args)), '--out', out, '--no-manifest', '--authorisation', BT.write(SCRATCH / 'records' / f'{name}.txt', text.encode())]
        return (*BT.cli(argv), out)


def setUpModule():
    M8.setUpModule()
    code, text, err = record9('setup-r9')
    assert code == 0, err
    STATE['r9'] = text
    made, real_with = [], r9b.with_guest

    def spy(*args):
        made.append(real_with(*args))
        return made[-1]
    with mock.patch.object(r9b, 'with_guest', spy):
        code, text, err = record9('setup-r9-panel')
    assert code == 0, err
    STATE['r9-panel'] = made[0]


def tearDownModule():
    M8.tearDownModule()


def admit(box, panel, hvc, word, let_panic, cpu_start, profile, has_root):
    """What the builder does with the options before any guest is built: m1.check_options, the profile's own check (gpb.check_form) and the root's (gpb.check_root), in that order."""
    m1.check_options(7, False, box, panel, hvc, word, let_panic, cpu_start, profile)
    gpb.check_form(profile, word, let_panic, cpu_start, panel, True, box)
    gpb.check_root(profile, has_root)


ANY_BOX = {('withheld', 0, None, False, 'stop'), ('handed', 0, None, False, 'stop'), ('console', 0, None, False, 'stop'), ('console', 0, P, False, 'stop')}
OLDER = {(5, 'console', 0, P, True, 'stop'), (60, 'console', 0, F, True, 'ack'), (120, 'console', 0, W90, True, 'ack')}
NEW = {(120, 'console', 0, F, True, 'ack', Q), (120, 'console', 0, F, True, 'ack', RTC)}
NEW9 = (300, 'console', 0, W360, True, 'ack', ROOTP)


def oracle(form):
    """True when `form` (box, panel, hvc, word, let_panic, cpu_start, profile, has_root) has a record, from the closed list of the note and independently of the builder's own checks: the older kinds
    with no profile (or the default board named) and no root, rungs 7 and 8 with no root, and rung 9's form with the root and no other."""
    box, rest, profile, has_root = form[0], form[1:6], form[6], form[7]
    if profile == ROOTP:
        return form[:7] == NEW9 and has_root
    return not has_root and 1 <= box <= 120 and ((rest in ANY_BOX and profile in (None, BASE)) or (form[:6] in OLDER and profile in (None, BASE)) or form[:7] in NEW)


class Form(unittest.TestCase):
    def test_the_form_is_the_fifth_of_a_closed_list_of_five_and_carries_the_root_profile(self):
        self.assertEqual((ch.RUNG9_FORM, ch.RUNG9_PROFILE, gpb.ROOT, gpb.RUNG9_PROFILES, ch.RUNG9_RAM_MIB),
                         (('console', W360, 300, ROOTP), 'j700ap-26A428-m1-quiet-rtc-acm', ROOTP, (ROOTP,), 6144))
        self.assertEqual(ch.ACK_FORMS, (ch.RUNG5_FORM, ch.RUNG6_FORM, ch.RUNG7_FORM, ch.RUNG8_FORM, ch.RUNG9_FORM), 'rung 9 is the fifth and there is no sixth')
        self.assertEqual(ch.FORM_PROFILE, {ch.RUNG7_FORM: Q, ch.RUNG8_FORM: RTC, ch.RUNG9_FORM: ROOTP})
        self.assertEqual(gpb.FORM_PROFILES, (Q, RTC, ROOTP))
        self.assertEqual((m1.BOOT_ARGS[W360], m1.BOOT_ARG_CPU_START[W360]), ('ACKPANIC-', 'ack'))
        self.assertEqual(sorted(m1.BOOT_ARGS), sorted([P, F, W90, W360]), 'one word was added')
        self.assertEqual((gpb.mark_of(ROOTP), gpb.RUNG_OF[ROOTP]), ('M-', 9))
        self.assertEqual(ch.let_panic_form_ok('console', W360, 300, 'ack', ROOTP), True)
        self.assertEqual([ch.let_panic_form_ok('console', W360, 300, 'ack', None), ch.let_panic_form_ok('console', W360, 120, 'ack', ROOTP), ch.let_panic_form_ok('console', F, 300, 'ack', ROOTP),
                          ch.let_panic_form_ok('console', W360, 300, 'ack', RTC), ch.let_panic_form_ok('console', W360, 300, 'stop', ROOTP), ch.let_panic_form_ok('console', W360, 300, 'ack', 'quiet')],
                         [False] * 6)

    def test_every_combination_of_box_word_panel_monitor_profile_and_root_is_a_named_refusal_unless_it_is_a_form_of_the_list(self):
        refusals = {'GUEST_PROFILE_REFUSED', 'LET_PANIC_REFUSED', 'BOOT_ARG_REFUSED', 'OPTIONS_BAD'}
        admitted, refused = [], {}
        grid = list(itertools.product((1, 60, 120, 121, 299, 300, 301, 1200, 1201), ('withheld', 'handed', 'console'), (0, 1), (None, P, F, W90, W360, 'cpu_boot_timeout_secs=361', 'cpumask=1'),
                                      (False, True), ('stop', 'ack'), (None, BASE, Q, RTC, ROOTP, 'quiet', gpb.NAMES[2]), (False, True)))
        for form in grid:
            try:
                admit(*form)
            except ch.Refused as error:
                refused[form] = error.name
                continue
            admitted.append(form)
        self.assertEqual(set(admitted), {form for form in grid if oracle(form)}, 'exactly the forms of the list are admitted, and nothing else')
        self.assertEqual(set(refused.values()) - refusals, set(), 'a refusal is one of the four names, never a bare exception')
        self.assertEqual((len(grid), len(admitted)), (9 * 3 * 2 * 7 * 2 * 2 * 7 * 2, len(grid) - len(refused)))
        self.assertGreater(len(refused), 10_000)
        for what, form, name in (('rung 9 without its root', (*NEW9, False), 'GUEST_PROFILE_REFUSED'), ('a root with rung 8\'s profile', (120, 'console', 0, F, True, 'ack', RTC, True), 'GUEST_PROFILE_REFUSED'),
                                 ('a root with no profile', (120, 'console', 0, F, True, 'ack', None, True), 'GUEST_PROFILE_REFUSED'),
                                 ('rung 9 with rung 8\'s box', (120, 'console', 0, W360, True, 'ack', ROOTP, True), 'LET_PANIC_REFUSED'),
                                 ('rung 9 with rung 8\'s word', (300, 'console', 0, F, True, 'ack', ROOTP, True), 'LET_PANIC_REFUSED'),
                                 ('rung 9 with a box over the carry pair\'s 1200 s', (1201, 'console', 0, W360, True, 'ack', ROOTP, True), 'OPTIONS_BAD'),
                                 ('rung 8 with a box of 300 s', (300, 'console', 0, F, True, 'ack', RTC, False), 'OPTIONS_BAD'),
                                 ('rung 9 with a patched kernel', (300, 'console', 1, W360, True, 'ack', ROOTP, True), 'OPTIONS_BAD'),
                                 ('rung 9 without --let-panic', (300, 'console', 0, W360, False, 'ack', ROOTP, True), 'LET_PANIC_REFUSED')):
            with self.subTest(what):
                self.assertEqual(refused[form], name)

    def test_each_bad_form_is_refused_end_to_end_by_name_and_writes_nothing(self):
        cases = {'no profile': (dict(guest_profile=None), True, 'OPTIONS_BAD'), 'rung 8\'s profile with the root': (dict(guest_profile=RTC), True, 'OPTIONS_BAD'),
                 'another profile': (dict(guest_profile=gpb.NAMES[2]), True, 'OPTIONS_BAD'), 'an unknown profile': (dict(guest_profile='quiet-rtc-acm'), True, 'GUEST_PROFILE_REFUSED'),
                 'rung 8\'s word': (dict(boot_arg=F), True, 'LET_PANIC_REFUSED'), 'no word': (dict(boot_arg=None), True, 'LET_PANIC_REFUSED'), 'cpus=1': (dict(boot_arg=P), True, 'BOOT_ARG_REFUSED'),
                 'a box of 120 s': (dict(time_box_s=120), True, 'LET_PANIC_REFUSED'), 'a box of 1201 s': (dict(time_box_s=1201), True, 'OPTIONS_BAD'),
                 'the plain choice with the ack report': (dict(cpu_start='stop'), True, 'BOOT_ARG_REFUSED'), 'the panel handed': (dict(panel='handed'), True, 'BOOT_ARG_REFUSED'),
                 'no --let-panic': ({}, False, 'LET_PANIC_REFUSED'), 'no root at all': (dict(ramdisk=None, root_hash=None, trust_cache=None), True, 'GUEST_PROFILE_REFUSED'),
                 'a root without its hash': (dict(root_hash=None), True, 'ROOT_INPUT_BAD'), 'a root without its trust cache': (dict(trust_cache=None), True, 'ROOT_INPUT_BAD'),
                 'no --bigfile': (dict(bigfile=None), True, 'OPTIONS_BAD'), 'another RAM': (dict(guest_mib=4096), True, 'OPTIONS_BAD')}
        for what, (over, let_panic, name) in cases.items():
            with self.subTest(what):
                code, text, err = record9('bad-form', let_panic=let_panic, **over)
                self.assertEqual((code, BT.refusal(text)), (1, f'REFUSED={name}'), err)
                if let_panic:
                    code, text, err, out = build9('bad-form-built', approved='not a record', **over)
                    self.assertEqual(code, 1)
                    self.assertFalse(out.exists(), 'nothing is written')

    def test_a_seeded_wrong_credential_layer_in_the_command_line_is_refused_by_the_read_back_and_writes_nothing(self):
        """The layer is the one word that the loader adds last (rb.CMDLINE_EXTRA): a guest of this profile without it, with it twice or with another value is not the profile's guest, and the builder's read-back of
        the command line (PANEL_MISMATCH) says so before a byte is written."""
        original = stand_in_guest

        def seeded(replacement):
            def guest(a, xb, place, facts, profile):
                built, nodes = original(a, xb, place, facts, profile)
                at = ch.layout_of(xb).handoff_pa + pn.BOOT_ARGS_AT
                page = bytearray(built['chunks'][at])
                start = page.index(rb.CMDLINE_EXTRA.encode())
                span = max(len(replacement), len(rb.CMDLINE_EXTRA))       # overwritten in place: the command line's field is 608 bytes and the rest of it is zero
                page[start:start + span] = replacement.ljust(span, b'\0')
                built['chunks'][at] = bytes(page)
                return built, nodes
            return guest
        for what, replacement in (('the layer missing', b''), ('another value', b' acm_fastsim=0'), ('the word twice', rb.CMDLINE_EXTRA.encode() * 2), ('another word', b' acm_fastsim=1x')):
            with self.subTest(what), mock.patch.dict(globals(), {'stand_in_guest': seeded(replacement)}):
                code, text, err = record9('seeded-layer')
                self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=PANEL_MISMATCH'), err)
                code, text, err, out = build9('seeded-layer-built', approved='not a record')
                self.assertEqual(code, 1)
                self.assertFalse(out.exists(), 'nothing is written')

    def test_the_gate_is_explicit_and_without_it_nothing_of_rung_9_is_built(self):
        args = M5.args5('no-gate', 300, **over9({}))
        with mock.patch.dict(os.environ, {root.GATE_ENV: ''}), mock.patch.object(rb, 'guest', stand_in_guest), PS.stand_in():
            code, text, err = BT.cli(['authorisation-template', 'real', *opts9(args)])
        self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=ROOT_GATE_OFF'), err)

    def test_the_older_records_are_what_they_were_with_the_new_form_in_the_tree(self):
        for what, call, key in (('rung 8', lambda: M8.record8('again-8'), 'r8'), ('rung 7', lambda: M7.record7('again-7'), 'r7'), ('rung 6', lambda: M6.record6('again-6'), 'r6-120'),
                                ('rung 5', lambda: M5.record5('again-5'), 'r5-60')):
            code, text, err = call()
            self.assertEqual((code, text), (0, STATE[key]), (what, err))
        for kind in OLD:
            code, text, err = PS.record(f'old-{NAMES[kind]}-with-9', kind, guest_profile=ROOTP)
            self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=GUEST_PROFILE_REFUSED'), err)


class Pair(unittest.TestCase):
    def test_rung_9_uses_the_root_carry_pair_by_digest_and_the_table_is_keyed_by_form(self):
        self.assertEqual(sorted(REAL_PAIRS), [F, W90, Q, RTC, ROOTP])
        self.assertEqual(REAL_PAIRS[ROOTP], (m1.ROOT_CARRY_MONITOR_SHA256, m1.ROOT_CARRY_REPORTER_SHA256))
        self.assertEqual((m1.ROOT_CARRY_MONITOR_SHA256[:8], m1.ROOT_CARRY_REPORTER_SHA256[:8]), ('6af26a84', '562b9b6e'), 'the pair of the root carry')
        self.assertNotEqual(REAL_PAIRS[ROOTP], REAL_PAIRS[RTC])
        self.assertEqual((m1.box_cap(m1.ROOT_CARRY_MONITOR_SHA256), m1.box_cap(m1.ACK_MONITOR_SHA256)), (1200, 120))

    def test_check_pair_form_holds_rung_9_to_its_pair_both_ways(self):
        with mock.patch.dict(m1.ACK_PAIRS, REAL_PAIRS):
            monitor, reporter = REAL_PAIRS[ROOTP]
            old_monitor, old_reporter = REAL_PAIRS[RTC]
            self.assertIsNone(m1.check_pair_form('ack', W360, monitor, reporter, ROOTP))
            for what, args in (('rung 8\'s pair under rung 9', (W360, old_monitor, old_reporter, ROOTP)), ('rung 9\'s pair under rung 8', (F, monitor, reporter, RTC)),
                               ('rung 9\'s pair under rung 5', (F, monitor, reporter)), ('another monitor', (W360, 'ab' * 32, reporter, ROOTP)), ('another reporter', (W360, monitor, 'cd' * 32, ROOTP))):
                with self.subTest(what), self.assertRaises(ch.Refused) as caught:
                    m1.check_pair_form('ack', *args)
                self.assertEqual(caught.exception.name, 'M1_REPORT_MISMATCH')

    def test_a_pair_that_is_another_forms_is_refused_and_nothing_is_written(self):
        fake, other = STATE['fake'], b'REP9' * 200
        report = AUTH.mutated(M5.ack_report(), lambda r: r['files'].__setitem__('SNAPREP-M1-ACK.BIN', AUTH.entry(other)))
        mon, rep5, rep9 = AUTH.sha(fake['image']), AUTH.sha(fake['reporter']), AUTH.sha(other)
        with mock.patch.dict(m1.ACK_PAIRS, {F: (mon, rep5), W90: (mon, rep5), Q: (mon, rep5), RTC: (mon, rep5), ROOTP: (mon, rep9)}):
            own = AUTH.stage('pair9', reporter=other, report=report)
            own.pop('m1_efi')
            code, text, err = record9('own-pair-9', **own)
            self.assertEqual(code, 0, err)
            code, text, err = record9('rung5-pair-under-9')
            self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=M1_REPORT_MISMATCH'), err)
            code, text, err, out = build9('pair-built', approved='not a record')
            self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=M1_REPORT_MISMATCH'), err)
            self.assertFalse(out.exists(), 'nothing is written')


class Labels(unittest.TestCase):
    def test_the_label_of_rung_9_is_its_own_among_the_ring_and_fits_the_23_characters(self):
        ring = {'rung5': m1.pinned_label('RUNG5', True, 'console', F, True, 'ack'), 'rung6': m1.pinned_label('RUNG6', True, 'console', W90, True, 'ack'),
                'rung7': m1.pinned_label('RUNG7', True, 'console', F, True, 'ack', profile=Q), 'rung8': m1.pinned_label('RUNG8', True, 'console', F, True, 'ack', profile=RTC),
                'rung9': m1.pinned_label('RUNG9', True, 'console', W360, True, 'ack', profile=ROOTP)}
        self.assertEqual((ring['rung9'], len(ring['rung9']), len(set(ring.values()))), ('M1RUN-ACKPANIC-M-RUNG9', 22, 5))
        with self.assertRaises(ch.Refused) as caught:
            m1.pinned_label('RUNG100', True, 'console', W360, True, 'ack', profile=ROOTP)
        self.assertEqual(caught.exception.name, 'PIN_STRING')

    def test_a_facts_label_that_starts_with_a_marker_or_a_mark_is_refused_and_the_label_is_in_the_record_and_the_pin(self):
        markers = [m for m in m1.PANEL_MARKS.values() if m] + sorted(set(m1.BOOT_ARGS.values())) + [m1.LET_PANIC_MARK] + [mark for mark in gpb.MARKS.values() if mark]
        for mark in markers:
            with self.subTest(mark=mark), self.assertRaises(ch.Refused) as caught:
                m1.pinned_label(f'{mark}x', True, 'console', W360, True, 'ack', profile=ROOTP)
            self.assertEqual(caught.exception.name, 'PIN_STRING')
        self.assertIn(f'(format 1, label M1RUN-ACKPANIC-M-{LABEL9})', STATE['r9'].splitlines()[0])
        code, text, err, out = build9('own-r9', approved=STATE['r9'])
        self.assertEqual(code, 0, (text, err))
        self.assertEqual(BLD.label_of(BLD.pin_of(out)).decode(), f'M1RUN-ACKPANIC-M-{LABEL9}')
        self.assertEqual(sb.check_pin(BLD.pin_of(out)), 0)


def universe():
    """A small universe with the rules of rung 9's comparison: rung 8's table of seven chunks (HANDOFF twice, PARAMS, the device tree, one that stays, one that is resized, one that goes), rung 9's
    bytes for the first four (the runs and the words put back give rung 8's), the new chunk and a root of four chunks, two of them ZERO. Returns (the pins for r9b, chunks, table)."""
    h0, h1 = bytes(range(256)) * 32, bytes(reversed(range(256))) + bytes(24)
    runs = (((0x10, h0[0x10:0x12].hex()), (0x1068, h0[0x1068:0x106B].hex())), ((0x4, h1[0x4:0x6].hex()),))
    n0, n1 = bytearray(h0), bytearray(h1)
    for (at, text), blob in ((runs[0][0], n0), (runs[0][1], n0), (runs[1][0], n1)):
        for i in range(len(text) // 2):
            blob[at + i] ^= 0xFF
    words = ((24, 0x78), (25, 0x898000000), (1119, 0), (1120, 0), (1121, 0))
    p8, p9 = bytearray(m1.PARAMS_BYTES), bytearray(m1.PARAMS_BYTES)
    for word, value in words:
        struct.pack_into('<Q', p8, 8 * word, value)
        struct.pack_into('<Q', p9, 8 * word, 0x5A5A5A5A5A5A5A5A)
    tree8, tree9 = b'tree' * 225, b'tree' * 226
    sha = lambda data: hashlib.sha256(bytes(data)).hexdigest()    # noqa: E731
    rows8 = [(3, 0x1000, len(h0), sha(h0)), (3, 0x3000, len(h1), sha(h1)), (5, 0x4000, m1.PARAMS_BYTES, sha(p8)), (4, 0x5000, len(tree8), sha(tree8)), (1, 0x9000, 50, sha(b'same')),
             (1, 0xA000, 100, sha(b'big')), (2, 0xB000, 20, sha(b'gone'))]
    rows9 = [(3, 0x1000, len(h0), sha(n0)), (3, 0x3000, len(h1), sha(n1)), (5, 0x4000, m1.PARAMS_BYTES, sha(p9)), (4, 0x5000, len(tree9), sha(tree9)), (1, 0x9000, 50, sha(b'same')),
             (1, 0xA000, 250, sha(b'bigger')), (7, 0xC000, 30, sha(b'new'))]
    ram, size = 0x100000, 0x40000
    pins = dict(CHUNKS8=tuple(rows8), SAME=(4,), PATCHED=(0, 1, 2, 3), RESIZED=(5,), REMOVED=(6,), NEW=((7, 0xC000, 30),), HANDOFF_AT=(0, 1), HANDOFF_BYTES=runs, PARAMS_AT=2, DT_AT=3,
                PARAMS_WORDS=words, RAMDISK_AT=ram, ROOT_BYTES=size, ROOT_CHUNKS=4, ROOT_ZERO_CHUNKS=2)
    chunks = [dict(kind=3, dest=0x1000, data=bytes(n0)), dict(kind=3, dest=0x3000, data=bytes(n1)), dict(kind=5, dest=0x4000, data=bytes(p9))]
    table = [dict(kind=k, dest=d, size=s, sha256=bytes.fromhex(h), flags=0) for k, d, s, h in rows9]
    for i in range(4):
        zero = i % 2 == 1
        table.append(dict(kind=sb.KIND_OTHER, dest=ram + i * 0x10000, size=0x10000, sha256=bytes(32) if zero else hashlib.sha256(b'root%d' % i).digest(), flags=sb.CHUNK_ZERO if zero else 0))
    return pins, chunks, table


class Compare(unittest.TestCase):
    def run_compare(self, patches, chunks, table):
        with mock.patch.multiple(r9b, **patches), mock.patch.object(r9b, 'RUNG8_GUEST_BYTES', 5000), mock.patch.object(r9b, 'pt', mock.Mock(BASE_NODES=395)):
            return r9b.compare(chunks, table, 4900, 352)

    def refused(self, *args):
        with self.assertRaises(ch.Refused) as caught:
            self.run_compare(*args)
        self.assertEqual(caught.exception.name, 'PANEL_MISMATCH')
        return caught.exception.detail

    def test_the_comparison_reads_the_small_universe_and_says_what_differs(self):
        patches, chunks, table = universe()
        diff = self.run_compare(patches, chunks, table)
        self.assertEqual((diff['count'], diff['outside'], diff['patched'], diff['removed'], diff['resized'], diff['root']['chunks'], diff['root']['zero']), (11, 7, (0, 1, 2, 3), (6,), {5: (100, 250)}, 4, 2))
        self.assertEqual((diff['handoff_runs'], diff['handoff_bytes'], diff['nodes'], diff['guest_bytes'], diff['table_room']), (3, 7, (395, 352), (5000, 4900), sb.CHUNKS_MAX - 11))

    def test_each_seeded_fault_of_the_guest_is_panel_mismatch_by_name(self):
        patches, chunks, table = universe()
        sha = lambda data: hashlib.sha256(data).digest()    # noqa: E731
        other = lambda i, **kw: [{**row, **kw} if n == i else row for n, row in enumerate(table)]    # noqa: E731
        with_data = lambda i, data: [{**c, 'data': data} if n == i else c for n, c in enumerate(chunks)]    # noqa: E731
        zero = lambda i: [{**row, 'flags': row['flags'] ^ sb.CHUNK_ZERO} if n == i else row for n, row in enumerate(table)]    # noqa: E731
        faults = {'a chunk that stays differs': (chunks, other(4, sha256=sha(b'o'))), 'a chunk that stays is shorter': (chunks, other(4, size=49)),
                  'a chunk that stays is elsewhere': (chunks, other(4, dest=0x9001)), 'a chunk that is resized has rung 8\'s size': (chunks, other(5, size=100)),
                  'a chunk that is removed is still there': (chunks, [*table[:7], dict(kind=2, dest=0xB000, size=20, sha256=sha(b'gone'), flags=0), *table[7:]]),
                  'a chunk is missing': (chunks, table[:4] + table[5:]), 'a chunk is added': (chunks, [*table[:7], dict(kind=1, dest=0xD000, size=16, sha256=sha(b'x'), flags=0), *table[7:]]),
                  'the new chunk is another': (chunks, other(6, size=31)), 'a root chunk is missing': (chunks, table[:-1]), 'a root chunk is not ZERO': (chunks, zero(8)),
                  'a root chunk is of another kind': (chunks, [{**row, 'kind': 1} if n == 9 else row for n, row in enumerate(table)]),
                  'a gap in the root': (chunks, [{**row, 'dest': row['dest'] + 16} if n == 9 else row for n, row in enumerate(table)]),
                  'HANDOFF differs in another byte': (with_data(0, chunks[0]['data'][:300] + b'\xff' + chunks[0]['data'][301:]), table),
                  'HANDOFF is shorter': (with_data(1, chunks[1]['data'][:-1]), table), 'no data for HANDOFF': ([c for c in chunks if c['dest'] != 0x1000], table),
                  'PARAMS differs in a word that is not listed': (with_data(2, chunks[2]['data'][:64] + b'\x01' + chunks[2]['data'][65:]), table), 'PARAMS is shorter': (with_data(2, chunks[2]['data'][:-8]), table),
                  'no data for PARAMS': ([c for c in chunks if c['dest'] != 0x4000], table)}
        for what, (chunk_data, rows) in faults.items():
            with self.subTest(what):
                self.refused(patches, chunk_data, rows)

    def test_the_pins_are_rung_8s_own_bundle_46_chunks_and_the_numbers_of_the_root(self):
        self.assertEqual(len(r9b.CHUNKS8), 46)
        self.assertEqual((r9b.CHUNKS8[0][0], r9b.CHUNKS8[2][0], r9b.CHUNKS8[3][0]), (sb.KIND_HANDOFF, sb.KIND_PARAMS, sb.KIND_DEVICE_TREE))
        self.assertTrue(all(len(h) == 64 and h.strip('0123456789abcdef') == '' for _, _, _, h in r9b.CHUNKS8))
        self.assertEqual((r9b.RUNG8_GUEST[:16], r9b.RUNG8_RECORD, r9b.RUNG8_LABEL), ('e2df284b53dff262', '54c1dff710c7faebd962b8a8a8dc1f097d34ec14cfb1aad200e9629e036c717b', 'M1RUN-ACKPANIC-R-RUNG8'), 'rung 8\'s guest, record (v2) and label')
        self.assertEqual((len(r9b.SAME), r9b.PATCHED, r9b.RESIZED, r9b.REMOVED, len(r9b.NEW)), (37, (0, 1, 2, 3), (35, 39, 40, 44), (36,), 3))
        self.assertEqual(sorted(set(range(46)) - set(r9b.SAME)), [0, 1, 2, 3, 35, 36, 39, 40, 44])
        self.assertEqual((r9b.RAMDISK_AT, r9b.ROOT_BYTES, r9b.ROOT_CHUNKS, r9b.ROOT_ZERO_CHUNKS), (0x88ED00000, 1_862_270_976, 291, 143))
        self.assertEqual(r9b.ROOT_BYTES, 0x6F000000, 'the pinned image of the profile, 1,862,270,976 bytes')


class Streaming(unittest.TestCase):
    """GUEST.DAT as a VirtualGuest: the bytes of `pack_guest`, at every window, and the write-and-read-back that refuses a file that is not the record's."""

    def chunks(self):
        """A guest with the five kinds that the interface requires, a zero chunk of the root's kind, a root chunk that is a window of a source and one that is data, in ascending destinations; the
        same guest as data alone for `pack_guest`."""
        blob = os.urandom(3 * MIB + 100)
        sources = src.BufferSource(blob)
        data = [dict(kind=sb.KIND_HANDOFF, dest=0x1000, data=bytes(range(256)) * 32), dict(kind=sb.KIND_PARAMS, dest=0x4000, data=os.urandom(m1.PARAMS_BYTES)),
                dict(kind=sb.KIND_DEVICE_TREE, dest=0x7000, data=os.urandom(4096)), dict(kind=sb.KIND_KERNEL, dest=0x10000, data=os.urandom(0x10000)),
                dict(kind=sb.KIND_TABLES, dest=0x20000, size=0x10000), dict(kind=sb.KIND_OTHER, dest=0x30000, size=2 * MIB + 0x4000, source=sources, offset=0x1000),
                dict(kind=sb.KIND_OTHER, dest=0x240000, size=0x8000), dict(kind=sb.KIND_OTHER, dest=0x250000, size=0x10, data=os.urandom(16))]
        as_data = [c if 'source' not in c else dict(kind=c['kind'], dest=c['dest'], data=blob[c['offset']:c['offset'] + c['size']]) for c in data]
        return data, as_data

    def test_the_virtual_guest_is_the_bytes_of_pack_guest_at_every_window_and_every_check_runs_on_it(self):
        data, as_data = self.chunks()
        virtual = root.VirtualGuest(0x1000, 0x400000, data)
        want = sb.pack_guest(0x1000, 0x400000, as_data)
        self.assertEqual((len(virtual), virtual.sha256()), (len(want), hashlib.sha256(want).hexdigest()))
        self.assertEqual(virtual[:], want)
        for lo, hi in ((0, 1), (0, 4096), (4095, 4100), (len(want) - 1, len(want)), (100, len(want)), (len(want) // 2, len(want) // 2 + 70_000), (0x3000, 0x3000 + MIB)):
            with self.subTest(lo=lo, hi=hi):
                self.assertEqual(virtual[lo:hi], want[lo:hi])
        with self.assertRaises(TypeError):
            virtual[3]
        with self.assertRaises(ValueError):
            virtual[::2]
        with sb.bigfile():
            self.assertEqual(sb._guest_check(virtual, len(virtual), (virtual.load_base, virtual.load_size, len(virtual), virtual.count, virtual.index_sha256)), 0)

    def test_the_writer_reads_the_file_back_and_a_wrong_byte_a_wrong_size_and_a_wrong_digest_are_refused(self):
        data, as_data = self.chunks()
        virtual = root.VirtualGuest(0x1000, 0x400000, data)
        SCRATCH.mkdir(parents=True, exist_ok=True)
        path = SCRATCH / 'stream' / 'GUEST.DAT'
        path.parent.mkdir(parents=True, exist_ok=True)
        rb.write_checked(path, virtual)
        self.assertEqual(path.read_bytes(), sb.pack_guest(0x1000, 0x400000, as_data))
        for what, change, name in (('a wrong byte in the last chunk', lambda b: b.__setitem__(len(b) - 5, b[len(b) - 5] ^ 1), 'CHUNK_DIGEST'),
                                   ('a wrong byte in the head', lambda b: b.__setitem__(70, b[70] ^ 1), 'GUEST_LOAD'),
                                   ('a shorter file', lambda b: b.__delitem__(slice(len(b) - 16, None)), 'GUEST_TRUNCATED')):
            with self.subTest(what):
                good = bytearray(sb.pack_guest(0x1000, 0x400000, as_data))
                change(good)
                bad = SCRATCH / 'stream' / f'bad-{name}.DAT'
                with mock.patch.object(root.VirtualGuest, 'write_to', lambda self_, p, _bad=bytes(good): Path(p).write_bytes(_bad)):
                    with self.assertRaises(ch.Refused) as caught:
                        rb.write_checked(bad, virtual)
                self.assertEqual(caught.exception.name, name)


class Builder(unittest.TestCase):
    def test_the_record_of_rung_9_is_built_with_its_label_and_is_under_the_held_limit(self):
        text, panel = STATE['r9'], STATE['r9-panel']
        self.assertLessEqual(len(text.encode()), HELD_LIMIT)
        self.assertTrue(text.isascii())
        self.assertEqual((panel['profile'], panel['boot_arg'], panel['let_panic'], sorted(panel['rung9'])), (ROOTP, W360, True, ['diff', 'guest_bytes', 'guest_sha16', 'label']))
        self.assertEqual(pn.kind_key({'panel': 'console', 'boot_arg': W360, 'let_panic': True, 'panel_facts': panel}), f'console+{W360}+let_panic+{ROOTP}')
        self.assertIs(pn._rung(panel), r9)

    def test_rung_7_and_8_keep_their_comparisons_and_the_dispatch_is_by_the_profile_alone(self):
        calls = []
        with mock.patch.object(r9b, 'with_guest', lambda *a: calls.append('r9') or {'rung9': 1}), mock.patch.object(M8.r8b, 'with_guest', lambda *a: calls.append('r8') or {}), \
                mock.patch.object(M7.r7b, 'compare', lambda *a: calls.append('r7') or {}):
            self.assertEqual(M7.r7b.with_guest({}, ROOTP, {'build_label': 'x'}, [], [], b'', 352), {'rung9': 1})
            M7.r7b.with_guest({}, RTC, {'build_label': 'x'}, [], [], b'', 352)
            M7.r7b.with_guest({}, Q, {'build_label': 'x'}, [], [], b'', 352)
        self.assertEqual(calls, ['r9', 'r8', 'r7'])

    def test_a_build_with_the_approved_record_of_another_kind_is_refused_whichever_is_the_guest(self):
        others = {**{NAMES[kind]: STATE[NAMES[kind]] for kind in OLD}, 'rung5': STATE['r5-60'], 'rung6': STATE['r6-120'], 'rung7': STATE['r7'], 'rung8': STATE['r8']}
        self.assertEqual(len({*others.values(), STATE['r9']}), 10, 'ten records, ten texts')
        for name, approved in others.items():
            with self.subTest(guest='rung9', approved=name):
                code, text, err, out = build9(f'cross-r9-{name}', approved=approved)
                self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=AUTH_RECORD_MISMATCH'), err)
                self.assertFalse(out.exists(), 'nothing is written for a record that is not this build\'s')
        for kind in OLD:
            with self.subTest(guest=NAMES[kind], approved='rung9'):
                code, text, err, out = PS.build(f'cross-{NAMES[kind]}-r9', kind, approved=STATE['r9'])
                self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=AUTH_RECORD_MISMATCH'), err)
                self.assertFalse(out.exists())
        for guest, builder in (('rung5', lambda: M5.build5('cross-r5-r9', approved=STATE['r9'])), ('rung6', lambda: M6.build6('cross-r6-r9', approved=STATE['r9'])),
                               ('rung7', lambda: M7.build7('cross-r7-r9', approved=STATE['r9'])), ('rung8', lambda: M8.build8('cross-r8-r9', approved=STATE['r9']))):
            with self.subTest(guest=guest, approved='rung9'):
                code, text, err, out = builder()
                self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=AUTH_RECORD_MISMATCH'), err)
                self.assertFalse(out.exists())

    def test_a_record_that_the_operator_approved_for_another_label_or_byte_is_refused_before_a_byte_is_written(self):
        for what, approved in (('a changed byte', STATE['r9'].replace('RUNG 9', 'RUNG 8', 1)), ('a trailing newline', STATE['r9'] + '\n')):
            with self.subTest(what):
                code, text, err, out = build9('mutant-r9', approved=approved)
                self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=AUTH_RECORD_MISMATCH'), err)
                self.assertFalse(out.exists())

    def test_the_approved_record_of_rung_9_builds_its_own_bundle_through_the_stand_in_loader(self):
        code, text, err, out = build9('own-bundle', approved=STATE['r9'])
        self.assertEqual(code, 0, (text, err))
        self.assertTrue((out / 'bundle' / 'SP11' / 'GUEST.DAT').is_file() and (out / 'bundle' / 'SP11' / 'PIN.DAT').is_file())


PAIR = ROOT / 'build/r13/rootpair'
FACTS = ROOT / 'build/m1p/sp11-facts-rung9.local.json'
J700 = ROOT / 'downloads/extracted/j700ap'
INPUTS = (J700 / 'kernelcache.release.mac17p', J700 / 'basesystem-26A428.apfs', J700 / 'basesystem-26A428.root-hash', J700 / 'basesystem-26A428.trustcache', PAIR / 'SNAPMON-M1-ACK.BIN',
          PAIR / 'SNAPREP-M1-ACK.BIN', PAIR / 'build-report.json', ROOT / 'build/sp11-m1-descriptor.json', FACTS)
needs_inputs = unittest.skipUnless(all(p.exists() for p in INPUTS), 'no kernelcache, root image, root-carry pair, descriptor or local facts of rung 9 (git-ignored local inputs)')


@needs_inputs
class Real(unittest.TestCase):
    """Rung 9's guest from the real J700 26A428 kernelcache and the real BaseSystem root, in this process, never held (a VirtualGuest, about 20 s): its table against rung 8's pinned table, and
    its record, which says what the comparison found."""
    EFI = ('SLBOUNCE-DEVHE-MARK.EFI=4107c28c194e2f2fd3517b2081f1b2bd9224571a7b037163e9e27c3ba3c8fcf7', 'Q1N1-PAYLOAD.EFI=af76e0bf726be99ec22aaaf6bb1079dcf8ec6579fe64cf2141cc0cad09181e9f')

    @classmethod
    def setUpClass(cls):
        rel = lambda p: str(Path(p).relative_to(ROOT))    # noqa: E731
        argv = ['authorisation-template', 'real', '--facts', rel(FACTS), '--base', '0x880000000', '--guest-mib', '6144', '--monitor', rel(PAIR / 'SNAPMON-M1-ACK.BIN'),
                '--reporter', rel(PAIR / 'SNAPREP-M1-ACK.BIN'), '--m1-run', rel(PAIR / 'build-report.json'), '--stores', 'ram,el2_sysreg,gic', '--time-box-s', '300', '--kernelcache', rel(INPUTS[0]),
                '--descriptor', rel(INPUTS[7]), '--panel', 'console', '--sptm-hvc-calls', '0', '--m1-efi', cls.EFI[0], '--m1-efi', cls.EFI[1], '--let-panic', '--cpu-start', 'ack', '--boot-arg', W360,
                '--guest-profile', ROOTP, '--bigfile', '--ramdisk', rel(INPUTS[1]), '--root-hash', rel(INPUTS[2]), '--trust-cache', rel(INPUTS[3])]
        got, panels = [], []
        real_compare, real_with = r9b.compare, r9b.with_guest

        def spy(*args):
            got.append(real_compare(*args))
            return got[-1]

        def spy_panel(*args):
            panels.append(real_with(*args))
            return panels[-1]
        with contextlib.chdir(ROOT), mock.patch.dict(os.environ, {root.GATE_ENV: '1'}), mock.patch.dict(m1.ACK_PAIRS, REAL_PAIRS), mock.patch.object(r9b, 'compare', spy), \
                mock.patch.object(r9b, 'with_guest', spy_panel), mock.patch.object(M5.r2, 'RUNG1_MONITOR', M7.ORIGINAL_RUNG1[0]), mock.patch.object(M5.r2, 'RUNG1_REPORTER', M7.ORIGINAL_RUNG1[1]):
            cls.made = bb.prepare(bb.build_parser().parse_args(argv), 'real')
        cls.diff, cls.panel = got[0], panels[0]

    def test_the_guest_is_the_pinned_numbers_and_never_held(self):
        guest = self.made['guest']
        self.assertTrue(r9b.root.VirtualGuest is root.VirtualGuest if hasattr(r9b, 'root') else isinstance(guest, root.VirtualGuest))
        self.assertEqual((len(guest), guest.sha256()), (1_926_621_440, 'b7aeddff38fd6ad8f2d25173238530919fa2ff51ac04c64afb3792d661e23541'))
        self.assertEqual({k: v for k, v in self.diff.items() if k not in ('nodes', 'guest_bytes')}, {k: v for k, v in REAL_DIFF.items()})
        self.assertEqual((self.diff['nodes'], self.diff['guest_bytes']), ((395, 352), (118_885_568, 1_926_621_440)))

    def test_the_record_says_what_the_comparison_found_and_is_under_the_held_limit(self):
        text = self.made['record']
        self.assertLessEqual(len(text.encode()), HELD_LIMIT)
        self.assertEqual(r9.record_problems(text, self.panel), [], 'the record says nothing that this guest makes false')
        flat = ' '.join(text.split())
        for needle in ('GUEST.DAT is 1,926,621,440 bytes (rung 8\'s: 118,885,568) in 339 chunks (rung 8\'s: 46)', 'the root is 291 kind-OTHER chunks (143 of them ZERO, 1,798,293,088 bytes of data '
                       'and 63,977,888 of zeros)', '37 of rung 8\'s 46 chunks are byte for byte rung 8\'s', 'label M1RUN-ACKPANIC-M-RUNG9'):
            self.assertIn(needle, flat)


class Hygiene(unittest.TestCase):
    def test_the_cap_is_208_kib_the_record_fits_it_and_the_files_are_under_the_size_of_a_file_of_this_tool(self):
        self.assertEqual(bb.AUTHORISATION_CAP, 13 << 14, 'raised by K17 from 176 KiB: rung 9\'s record is about 195 KB')
        self.assertEqual(M5.HELD_LIMIT, 13 * 16384 - 1024)
        self.assertLessEqual(len(STATE['r9'].encode()), HELD_LIMIT)
        for rel in ('tools/sp11_stick_bundle_rung9.py', 'tools/sp11_stick_bundle_rootbundle.py', 'tools/sp11_stick_bundle_root.py', 'tools/sp11_stick_bundle_panel_rung9.py',
                    'tools/sp11_stick_bundle_panel_rung9d.py', 'tools/sp11_stick_bundle_panel_rungs.py', 'tools/sp11_stick_bundle_profile.py', 'tools/sp11_stick_bundle_build_opts.py',
                    'tools/sp11_stick_bundle_build.py', 'tools/sp11_stick_bundle_panel.py', 'tools/sp11_stick_bundle_m1.py', 'tools/sp11_stick_bundle_chunks.py', 'tools/sp11_stick_bundle_record.py',
                    'tools/sp11_stick_bundle_record_m1_guide.py', 'tools/sp11_stick_bundle_rung7.py', 'tools/sp11_stage_bigdata.py', 'tools/sp11_autorun.py', 'tools/stage-sp11-boot-media.py',
                    'tools/test-sp11-stick-monitor-rung9.py', 'tools/test-sp11-stick-monitor-rung9-words.py', 'tools/test-sp11-stage-bigdata.py', 'probes/guestprofile_m1.py'):
            text = (ROOT / rel).read_text()
            with self.subTest(rel):
                self.assertLess(len(text.encode()), 51_200)
                self.assertLess(len(text.splitlines()), 1500)
                self.assertEqual(text.splitlines()[1 if text.startswith('#!') else 0].count('SPDX-License-Identifier: MIT'), 1)
                self.assertTrue(text.isascii())
        for rel, cap in (('docs/SP11-STICK-ROOT.md', 22_500), ('docs/SP11-M1-GUEST-PROFILES.md', 24_500), ('docs/SP11-STICK-MONITOR.md', 166_000)):
            self.assertLessEqual(len((ROOT / rel).read_bytes()), cap, rel)

    def test_the_note_has_k17_and_its_test_row_and_the_cap_is_named_in_it(self):
        note = (ROOT / 'docs/SP11-STICK-MONITOR.md').read_text()
        for needle in ('**K17, rung 9: the first root-mount form', '| H23 | test-sp11-stick-monitor-rung9.py', '`AUTHORISATION_CAP` 180,224 to 212,992 (13 << 14', 'the keyed block of boot 28 in section 0a is a placeholder',
                       'pin 160,500 to 166,000 bytes'):
            self.assertEqual(note.count(needle), 1, needle)
        self.assertIn('## 9. Rung 9: the first root-mount form (K17', (ROOT / 'docs/SP11-STICK-ROOT.md').read_text())


if __name__ == '__main__':
    unittest.main()
