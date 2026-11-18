#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of the choices of D9 that hand the panel to the guest, for the monitor entry run (monitor port work item 4b, rung 2; docs/SP11-STICK-MONITOR.md section 20):
`real --m1-run` with the panel handed (the second choice) and with the panel as the kernel's console (`--panel console`: serial=2 in the command line), each with the
kernel stock. What the builder reads of the built guest (tools/sp11_stick_bundle_panel.py: PARAMS fb_pa and fb_span, boot_args.Video, the command line), what the
record of each choice says about the panel and where it says it, the labels that tell the three choices apart, the sources the record cites, and the refusals:
PANEL_MISMATCH for a guest that is not what its record says, the options of D9, a facts label that carries a marker, the approved record that is too large.
The record of the first choice, panel withheld, must be what it was byte for byte: its digest is pinned here (GOLDEN) and the suites that pinned the other records
are unchanged.

    python3 tools/test-sp11-stick-monitor-panel.py

The fixtures of tools/test-sp11-stick-monitor-builder.py (which loads those of tools/test-sp11-stick-monitor-auth.py) are loaded here with a scratch directory of their
own (build/sp11-monitor-panel-test-scratch/, deleted at the end). The guest is built by a stand-in for the loader's build() that writes the panel as the loader writes it
(panel_range() into PARAMS, boot_args() into the handoff) over the synthetic filler, so that no Apple input is needed; the struct offsets are compiled against the
monitor's own translation unit and SKIP without the toolchain, which is never a pass; the numbers that the record states of q1n1's runner are held to its source
when $Q1N1_SP11_TREE names it and SKIP without it. The real guest, the J700 26A428 kernelcache, is the end-to-end test of tools/test-sp11-stick-monitor-builder.py.
No hardware: NOT RUN."""
import importlib.util
import os
import re
import shutil
import struct
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))
import sp11_stick_bundle_m1 as m1  # noqa: E402
import sp11_stick_bundle_panel as pn  # noqa: E402
import sp11_stick_bundle_record as rec  # noqa: E402
import sp11_stick_bundle_record_m1_guide as m1g  # noqa: E402

spec = importlib.util.spec_from_file_location('sp11_monitor_builder_tests', TOOLS / 'test-sp11-stick-monitor-builder.py')
BLD = importlib.util.module_from_spec(spec)
spec.loader.exec_module(BLD)
SCRATCH = ROOT / 'build' / 'sp11-monitor-panel-test-scratch'
BLD.SCRATCH = BLD.AUTH.SCRATCH = SCRATCH
AUTH, BT, STATE = BLD.AUTH, BLD.BT, BLD.STATE
bb, ch, sb = BT.bb, BT.ch, BT.sb
MIB, sha = sb.MIB, AUTH.sha
LOADER = bb.xnuboot                 # the loader module factory, kept before a test replaces it
STAND_IN_IMAGE = b'a stand-in for the kernelcache image'
CHOICES = ('withheld', 'handed', 'console')
LABEL = 'panel-test'                # 10 characters: M1RUN-PANEL- and these are 22 of the 23 that a pinned label may have (M1RUN-CONSOLE- and 10 are 24: LABEL_CONSOLE)
LABEL_CONSOLE = 'con-test'          # 8 characters: M1RUN-CONSOLE- and these are 22 of the 23
LABELS = {'withheld': LABEL, 'handed': LABEL, 'console': LABEL_CONSOLE}
# The SHA-256 of the record of each choice for the stand-in guest and the inputs of record() below: the text is what the operator approves, so a changed byte is another
# record. The withheld one is the first choice's text (1e938a7: c452c349...; draft 3, approved: 31e5d4c2...; draft 5 is m1pkg's af7b27a2..., held byte for byte by the
# evidence of the patch: this is its stand-in). The records of the choices that hand the panel are pinned as of the patch that made them.
GOLDEN = {'withheld': '8032785740a03f963d3a5cb60be40534a746468559ae47a8d8637e02bdbccd62', 'handed': '9029110370d8717e19d046fe88e4a8eef543a917b0f2239cf7ab80c0c9bf4a09',
          'console': '9771f687a774a6c2a7e20337bd3c409e94c1b05813965a86077742dafd4cef72'}      # re-pinned on purpose for the cap of 120 s that the text names (before: 4daffdef..., 59e2d2e5..., 6db5ee72...)


def setUpModule():
    shutil.rmtree(SCRATCH, ignore_errors=True)
    BLD.setUpModule()
    BT.write(SCRATCH / 'in/stand-in.kc', b'not a kernelcache: the stand-in loader never reads it\n')
    STATE['panel'] = {}
    # The stand-in pair stands for the first run's: the words that say a record has boot 20's programs (tools/sp11_stick_bundle_panel_rung2.py) are held to the digests of
    # the stand-in payloads here, as they are held to the real pair's in the build of the real record; the other case is tested in test-sp11-stick-monitor-rung2.py.
    code, text, err = record('setup-probe', 'withheld')
    assert code == 0, err
    STATE['first_run_pair'] = (pn.r2.RUNG1_MONITOR, pn.r2.RUNG1_REPORTER)
    pn.r2.RUNG1_MONITOR, pn.r2.RUNG1_REPORTER = (re.search(rf'payload {n}  SNAP{name}.BIN  \d+ bytes  sha256 ([0-9a-f]{{64}})', text).group(1) for n, name in ((0, 'MON'), (1, 'REP')))
    for choice in CHOICES:
        STATE['panel'][choice] = made = []
        with spying(made):
            code, text, err = record(f'setup-{choice}', choice)
        assert code == 0, (choice, err)
        STATE[choice] = text


def tearDownModule():
    pn.r2.RUNG1_MONITOR, pn.r2.RUNG1_REPORTER = STATE['first_run_pair']
    BLD.tearDownModule()
    shutil.rmtree(SCRATCH, ignore_errors=True)


# ---- A stand-in for the loader's build() that writes the panel as the loader writes it -------------------------------------------------------------------------
def stand_in(mutate=None):
    """bb.xnuboot replaced by the real loader module (it loads without Apple input) whose build() and params_blob() are stand-ins over the synthetic filler:
    PARAMS fb_pa and fb_span from panel_range() and the handoff's boot_args from boot_args(), as build() writes them. `mutate(built, xb, lay)` seeds a fault."""
    def factory():
        xb = LOADER()

        def build_(path, **options):
            built = ch.synthetic_built(xb, 2 * MIB, MIB, 4 * MIB, os.environ['SNAP_SEED'], 99)
            lay = ch.layout_of(xb)
            words = list(struct.unpack(f'<{ch.PARAMS_WORDS}Q', built['chunks'][lay.params_pa]))
            words[pn.FB_PA_WORD:pn.FB_SPAN_WORD + 1] = xb.panel_range() or (0, 0)
            built['chunks'][lay.params_pa] = struct.pack(f'<{ch.PARAMS_WORDS}Q', *words)
            page = bytearray(0x1000)
            page[:xb.BOOT_ARGS_SIZE] = xb.boot_args(0xfffffe0007004000, xb.PHYS_BASE, 0x20000000, 0xfffffe0008000000, 0xfffffe0007100000, 0x1d420)
            built['chunks'][lay.handoff_pa + pn.BOOT_ARGS_AT] = bytes(page)
            if mutate:
                mutate(built, xb, lay)
            return {**built, 'image': types.SimpleNamespace(data=STAND_IN_IMAGE), 'guest_profile': 'stand-in', 'dart_apf_slices': []}

        def params_blob(built):
            words = list(struct.unpack(f'<{ch.PARAMS_WORDS}Q', built['chunks'][xb.PARAMS_PA]))
            words[ch.PARAMS_WATCHDOG] = built['watchdog']
            return struct.pack(f'<{ch.PARAMS_WORDS}Q', *words)
        xb.build, xb.params_blob = build_, params_blob
        return xb
    return mock.patch.object(bb, 'xnuboot', factory)


def spying(seen):
    """pn.read_panel, recording what it returns in `seen`."""
    real = pn.read_panel

    def spy(*args, **kw):
        seen.append(real(*args, **kw))
        return seen[-1]
    return mock.patch.object(pn, 'read_panel', spy)


def real_args(name, choice='handed', facts=None, **over):
    return dict(AUTH.stage(name), kernelcache=SCRATCH / 'in/stand-in.kc', panel=choice, sptm_hvc_calls=0, descriptor=BLD.descriptor('board.json'), guest_mib=384,
                stores='ram,el2_sysreg,gic', m1_efi=BLD.EFI, facts=facts or BLD.facts_with(LABELS[choice]), **over)


def record(name, choice='handed', mutate=None, **over):
    """(code, text, err) of the record of a real build for `choice`, from the stand-in loader."""
    with stand_in(mutate):
        return BT.template('real', **real_args(name, choice, **over))


def build(name, choice='handed', approved=None, mutate=None, **over):
    """(code, text, err, out) of a real build of `choice` with the approved record `approved` (its own record by default)."""
    out, args = SCRATCH / 'out' / name, real_args(name, choice, **over)
    with stand_in(mutate):
        text = approved if approved is not None else BT.template('real', **args)[1]
        argv = ['real', *BT.opts('real', **args), '--out', out, '--no-manifest', '--authorisation', BT.write(SCRATCH / 'records' / f'{name}.txt', text.encode())]
        return (*BT.cli(argv), out)


def chunks_of(choice='handed', mutate=None, **over):
    """(chunks, lay, facts, board) of the guest the builder makes for `choice`, as read_panel is called."""
    got = {}
    real = pn.read_panel

    def keep(chunks, lay, facts, choice, real_, *rest):
        got.update(chunks=chunks, lay=lay, facts=facts, board=rest[0] if rest else None)
        return real(chunks, lay, facts, choice, real_, *rest)
    with mock.patch.object(pn, 'read_panel', keep):
        code, text, err = record('chunks', choice, mutate, **over)
    assert code == 0, err
    return got['chunks'], got['lay'], got['facts'], got['board']


def record_problems(text, choice, panel=None):
    """What the record says about the panel, held to the guest it is for: [problem]. `choice` is withheld, handed or console; `panel` is read_panel's dict for a
    guest that is given the panel."""
    flat = ' '.join(text.split())
    says = [c for c in CHOICES if f'panel {c}: {rec.PANEL_WORDS[c]}' in flat]
    problems = []
    if says != [choice]:
        problems.append('section 1a does not say what the guest is given')
    if choice != 'withheld':
        for claim in ('No disk and no RAM outside the placement and REPORT.DAT', 'DC CIVAC over the guest range and the reserve.', 'the one range the run may store to)',
                      'ram: RAM inside the placement and the tail of REPORT.DAT'):
            if claim in flat:
                problems.append(f'it still says, of the first choice: {claim}')
        header = {'handed': '3c. The panel handed to the guest', 'console': '3c. The panel as the kernel\'s console'}
        line = {'handed': 'serial=3', 'console': 'serial=2'}[choice]
        for need in (header[choice], f'{panel["fb_pa"]:#x}', f'{panel["fb_span"]:#x}', f'panel {panel["fb_pa"]:#x} + {panel["fb_span"]:#x} bytes',
                     f'{panel["gop"]["width"]} x {panel["gop"]["height"]}', line, 'the panel range', 'the whole panel in frame', f'panel_base {panel["fb_pa"]:#x}',
                     'and the panel range of section 3c, as the GIC and stop dry run (which has no panel)', f'command line {pn.CMDLINES[choice]}'):
            if need not in flat:
                problems.append(f'it does not say {need}')
        other = header['console' if choice == 'handed' else 'handed']
        if other in flat:
            problems.append(f'it carries the section 3c of the other choice: {other}')
    elif '3c. ' in text or 'panel range' in flat:
        problems.append('the record of the first choice speaks of a panel range')
    return problems


def sections(text):
    return {head: body for head, body in re.findall(r'(?ms)^(\d[a-z]?)\. (.*?)(?=^\d[a-z]?\. |\Z)', text)}


# ---- (a) the options and the label ------------------------------------------------------------------------------------------------------------------------
class Options(unittest.TestCase):
    def test_d9_admits_each_choice_with_a_stock_kernel_and_nothing_else(self):
        for panel, hvc, admitted in (('withheld', 0, True), ('handed', 0, True), ('console', 0, True), ('withheld', 1, False), ('handed', 1, False), ('console', 1, False),
                                     ('nothing', 0, False), ('Console', 0, False)):
            with self.subTest(panel=panel, hvc=hvc):
                if admitted:
                    m1.check_options(7, False, 5, panel, hvc)
                else:
                    with self.assertRaises(ch.Refused) as caught:
                        m1.check_options(7, False, 5, panel, hvc)
                    self.assertEqual(caught.exception.name, 'OPTIONS_BAD')
                    self.assertIn('D9', caught.exception.detail)
                    self.assertIn(f'--panel {panel} and --sptm-hvc-calls {hvc}', caught.exception.detail)
                    for name in CHOICES:
                        self.assertIn(f'--panel {name}', caught.exception.detail, 'the message names the three choices')

    def test_the_synthetic_kind_has_no_panel_to_choose(self):
        m1.check_options(7, False, 5)

    def test_the_panel_as_the_console_belongs_to_the_monitor_entry_run_only(self):
        self.assertEqual(sorted(m1.PANEL_MARKS), sorted(CHOICES))
        self.assertIn("'console'", (TOOLS / 'sp11_stick_bundle_build.py').read_text().split("p.add_argument('--panel'")[1].split('\n')[0])
        args = real_args('console-dry-run', 'console')
        args.update(m1_run=None, m1_efi=None)         # the kind of the dry run: no monitor is called
        code, text, err = BT.template('real', **args)
        self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=OPTIONS_BAD'), err)
        self.assertIn('--m1-run', err)


class Label(BT.Case):
    def test_the_label_of_each_choice_has_its_marker_after_the_one_the_wrapper_checks(self):
        self.assertEqual(m1.pinned_label('RUNG2', True, 'handed'), 'M1RUN-PANEL-RUNG2')
        self.assertEqual(m1.pinned_label('RUNG2', True, 'console'), 'M1RUN-CONSOLE-RUNG2')
        self.assertEqual(m1.pinned_label('RUNG1', True), 'M1RUN-RUNG1')
        self.assertEqual(m1.pinned_label('RUNG1', True, 'withheld'), 'M1RUN-RUNG1')
        for choice in CHOICES:
            self.assertEqual(m1.pinned_label('x', False, choice), 'x', 'the other kinds keep the facts label, whatever the panel')
        marks = [m for m in m1.PANEL_MARKS.values() if m]
        self.assertEqual(len(marks), len(set(marks)))
        self.assertFalse([a for a in marks for b in marks if a != b and a.startswith(b)], 'no marker is the start of another: one label, one choice')

    def test_a_facts_label_that_carries_a_marker_is_refused_for_this_kind_under_every_choice(self):
        for choice in CHOICES:
            for mark in (m for m in m1.PANEL_MARKS.values() if m):
                with self.subTest(choice=choice, mark=mark):
                    self.raises('PIN_STRING', m1.pinned_label, f'{mark}x', True, choice)
        for mark in (m for m in m1.PANEL_MARKS.values() if m):
            self.assertEqual(m1.pinned_label(f'{mark}x', False), f'{mark}x', 'no other kind adds a marker, so none can be mistaken for this one')

    def test_the_label_that_fits_is_shorter_by_the_marker(self):
        self.assertEqual(m1.pinned_label('a' * 17, True), 'M1RUN-' + 'a' * 17)
        self.assertEqual(m1.pinned_label('a' * 11, True, 'handed'), 'M1RUN-PANEL-' + 'a' * 11)
        self.assertEqual(m1.pinned_label('a' * 9, True, 'console'), 'M1RUN-CONSOLE-' + 'a' * 9)
        for choice, room in (('withheld', 17), ('handed', 11), ('console', 9)):
            with self.subTest(choice=choice):
                with self.assertRaises(ch.Refused) as caught:
                    m1.pinned_label('a' * (room + 1), True, choice)
                self.assertEqual(caught.exception.name, 'PIN_STRING')
                self.assertIn(f'the facts label holds {room}', caught.exception.detail)

    def test_the_bundles_of_the_three_choices_carry_three_labels_in_pin_dat_and_in_the_record(self):
        labels = {}
        for choice in CHOICES:
            code, text, err, out = build(f'label-{choice}', choice)
            self.assertEqual(code, 0, (text, err))
            labels[choice] = BLD.label_of(BLD.pin_of(out)).decode()
            self.assertIn(f'(format 1, label {labels[choice]})', STATE[choice].splitlines()[0])
            self.assertEqual(sb.check_pin(BLD.pin_of(out)), 0)
        self.assertEqual(labels, {'withheld': f'M1RUN-{LABEL}', 'handed': f'M1RUN-PANEL-{LABEL}', 'console': f'M1RUN-CONSOLE-{LABEL_CONSOLE}'})

    def test_the_pin_dat_of_one_choice_belongs_to_its_own_record_and_to_no_other(self):
        pins = {}
        for choice in CHOICES:
            code, text, err, out = build(f'belongs-{choice}', choice)
            self.assertEqual(code, 0, (text, err))
            pins[choice] = (BLD.pin_of(out), (out / 'bundle' / 'SP11' / 'GUEST.DAT').read_bytes())

        def belongs(record_text, pin):
            fields = sb.unpack(pin, sb.PIN_LAYOUT)
            first = record_text.splitlines()[0]
            return bytes(fields['authorisation_sha256']).hex() == sha(record_text.encode()) and f'label {BLD.label_of(pin).decode()})' in first
        for choice in CHOICES:
            for other in CHOICES:
                with self.subTest(record=choice, pin=other):
                    self.assertEqual(belongs(STATE[choice], pins[other][0]), choice == other, 'the PIN.DAT of one choice belongs to the record of that choice and no other')
        # The interface refuses the files of one with the PIN.DAT of the other: the guest's digests are the PIN.DAT's.
        for choice in CHOICES:
            for other in CHOICES:
                if choice != other:
                    with self.subTest(guest=choice, pin=other):
                        pin, guest = pins[other][0], pins[choice][1]
                        self.assertNotEqual(sb.check_guest(guest, pin) or sb.check_digests(guest, pin, sb.HASH_CHUNKS | sb.HASH_FILE)[0], 0)

    def test_a_build_with_the_approved_record_of_another_choice_is_refused(self):
        for choice in CHOICES:
            for other in CHOICES:
                if choice != other:
                    with self.subTest(choice=choice, approved=other):
                        code, text, err, out = build(f'cross-{choice}-{other}', choice, approved=STATE[other])
                        self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=AUTH_RECORD_MISMATCH'), err)
                        self.assertFalse(out.exists(), 'nothing is written for a record that is not this build\'s')

    def test_a_facts_label_that_carries_a_marker_is_refused_end_to_end(self):
        for choice in CHOICES:
            for mark in (m for m in m1.PANEL_MARKS.values() if m):
                code, text, err = record(f'marked-{choice}', choice, facts=BLD.facts_with(f'{mark}x'))
                self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=PIN_STRING'), (choice, mark, err))
        code, text, err = BT.cli(['authorisation-template', 'synthetic', *BT.opts(stores='ram,el2_sysreg', facts=BLD.facts_with('PANEL-x'))])
        self.assertEqual(code, 0, 'the dry run takes any label that does not start with M1RUN-: ' + err)


# ---- (b) what the builder reads of the guest --------------------------------------------------------------------------------------------------------------
class Reader(BT.Case):
    def test_the_panel_range_is_the_loaders_for_the_unit_and_for_geometries_that_do_not_fill_a_page(self):
        xb = LOADER()
        for base, width, height, stride in ((0xE4800000, 2880, 1920, 2880), (0xE4800000, 2880, 1920, 2944), (0xE4801000, 1000, 701, 1000), (0xE0000123, 640, 480, 640)):
            facts = dict(fb_base=base, fb_width=width, fb_height=height, fb_stride=stride)
            xb.FRAMEBUFFER = (base, width, height, stride * 4, 32)
            with self.subTest(base=hex(base), stride=stride):
                self.assertEqual(pn.gop_range(facts), xb.panel_range())
        self.assertEqual(pn.gop_range(dict(fb_base=0xE4800000, fb_width=2880, fb_height=1920, fb_stride=2880)), (0xE4800000, 0x1518000))
        self.assertEqual(pn.PAGE_SHIFT, xb.GRANULE)

    def test_the_offsets_are_the_ones_the_loader_writes_boot_args_with(self):
        xb = LOADER()
        xb.FRAMEBUFFER = (0xE4800000, 2880, 1920, 11520, 32)
        args = bytes(xb.boot_args(0xfffffe0007004000, xb.PHYS_BASE, 0x20000000, 0xfffffe0008000000, 0xfffffe0007100000, 0x1d420))
        self.assertEqual(len(args), pn.BOOT_ARGS_BYTES)
        self.assertEqual(struct.unpack_from('<6Q', args, pn.VIDEO_AT), (0xE4800000, pn.TEXT_MODE, 11520, 2880, 1920, pn.DEPTH))
        self.assertEqual(args[pn.CMDLINE_AT:pn.CMDLINE_AT + pn.CMDLINE_BYTES].split(b'\0')[0].decode(), pn.CMDLINES['handed'])
        self.assertEqual(pn.LOADER_CMDLINE, xb.BOOT_CMDLINE, 'the console paragraphs of section 3c are written for the loader\'s command line')
        self.assertEqual(xb.VIDEO_DISPLAY_MODE, pn.TEXT_MODE)
        xb.BOOT_CMDLINE = xb.BOOT_CMDLINE.replace('serial=3', 'serial=2')       # what probes/run.py:1594 does, and real_built for `console`
        again = bytes(xb.boot_args(0xfffffe0007004000, xb.PHYS_BASE, 0x20000000, 0xfffffe0008000000, 0xfffffe0007100000, 0x1d420))
        self.assertEqual(again[pn.CMDLINE_AT:pn.CMDLINE_AT + pn.CMDLINE_BYTES].split(b'\0')[0].decode(), pn.CMDLINES['console'])
        self.assertEqual(struct.unpack_from('<6Q', again, pn.VIDEO_AT), struct.unpack_from('<6Q', args, pn.VIDEO_AT), 'the console route changes nothing of the video fields')
        self.assertEqual([a for a, b in zip(pn.CMDLINES['console'].split(), pn.LOADER_CMDLINE.split()) if a != b], ['serial=2'], 'the one argument that differs')
        xb.FRAMEBUFFER = None
        self.assertEqual(struct.unpack_from('<6Q', bytes(xb.boot_args(0, xb.PHYS_BASE, 0, 0, 0, 0)), pn.VIDEO_AT), (0,) * 6)
        self.assertEqual(pn.BOOT_ARGS_AT, 0x1000)
        self.assertIn('chunks[HANDOFF_PA + 0x1000][:BOOT_ARGS_SIZE] = boot_args(', (ROOT / 'probes/xnuboot.py').read_text(), 'the page that build() puts boot_args in')

    def test_params_names_the_panel_two_words_after_the_memory_range(self):
        source = (ROOT / 'probes/xnuboot/report_layout.c').read_text()
        self.assertRegex(source, r'(?s)uint64_t mem_low, mem_high;\s*/\*.*?\*/\s*uint64_t fb_pa, fb_span;')
        self.assertEqual((pn.FB_PA_WORD, pn.FB_SPAN_WORD), (ch.PARAMS_MEM_HIGH + 1, ch.PARAMS_MEM_HIGH + 2))

    def test_each_guest_is_read_as_what_it_is_and_nothing_else(self):
        for choice in CHOICES:
            chunks, lay, facts, board = chunks_of(choice)
            for read_as in CHOICES:
                with self.subTest(guest=choice, read_as=read_as):
                    if read_as == choice:
                        self.assertEqual(bool(pn.read_panel(chunks, lay, facts, read_as, True, board)), choice != 'withheld')
                    else:
                        self.raises('PANEL_MISMATCH', pn.read_panel, chunks, lay, facts, read_as, True, board)

    def test_the_two_guests_that_have_the_panel_differ_in_the_command_line_only(self):
        (handed, lay, _, _), (console, _, _, _) = chunks_of('handed'), chunks_of('console')
        page = lay.handoff_pa + pn.BOOT_ARGS_AT
        by = {choice: {c['dest']: bytes(c['data']) for c in chunks if c.get('data') is not None} for choice, chunks in (('handed', handed), ('console', console))}
        self.assertEqual(sorted(by['handed']), sorted(by['console']))
        differing = [dest for dest in by['handed'] if by['handed'][dest] != by['console'][dest]]
        self.assertEqual(len(differing), 1, 'one chunk differs: the one that holds the boot_args page')
        dest = differing[0]
        a, b = by['handed'][dest], by['console'][dest]
        self.assertTrue(dest <= page < dest + len(a), 'and it holds the boot_args page')
        index = page - dest + pn.CMDLINE_AT + pn.CMDLINES['handed'].index('serial=3') + len('serial=')
        self.assertEqual([i for i in range(len(a)) if a[i] != b[i]], [index], 'one byte: the 3 of serial=3')

    def test_the_dict_the_text_is_made_from_holds_the_facts_the_range_the_video_fields_and_the_descriptor(self):
        panel = STATE['panel']['handed'][0]
        facts = bb.load_facts(BLD.facts_with(LABEL))
        self.assertEqual((panel['fb_pa'], panel['fb_span']), (facts['fb_base'], 0x1518000))
        self.assertEqual(panel['video'], (facts['fb_base'], 0, facts['fb_stride'] * 4, facts['fb_width'], facts['fb_height'], 32))
        self.assertEqual(panel['gop'], dict(base=facts['fb_base'], bytes=facts['fb_bytes'], width=2880, height=1920, stride=2880, format=1))
        self.assertEqual((panel['choice'], panel['cmdline']), ('handed', pn.CMDLINES['handed']))
        self.assertEqual(sorted(panel['present'] + panel['absent']), sorted(BLD.BT.TBD.synthetic()['devices']))
        self.assertEqual(STATE['panel']['withheld'], [None])
        console = STATE['panel']['console'][0]
        self.assertEqual({k: v for k, v in console.items() if k not in ('choice', 'cmdline')}, {k: v for k, v in panel.items() if k not in ('choice', 'cmdline')})
        self.assertEqual((console['choice'], console['cmdline']), ('console', pn.CMDLINES['console']))

    def test_a_guest_that_is_not_what_its_record_says_is_refused_for_each_seeded_fault(self):
        def word(index, value):
            def fn(built, xb, lay):
                words = list(struct.unpack(f'<{ch.PARAMS_WORDS}Q', built['chunks'][lay.params_pa]))
                words[index] = value
                built['chunks'][lay.params_pa] = struct.pack(f'<{ch.PARAMS_WORDS}Q', *words)
            return fn

        def args(at, value, size=8):
            def fn(built, xb, lay):
                page = bytearray(built['chunks'][lay.handoff_pa + pn.BOOT_ARGS_AT])
                page[at:at + size] = value.to_bytes(size, 'little') if isinstance(value, int) else value
                built['chunks'][lay.handoff_pa + pn.BOOT_ARGS_AT] = bytes(page)
            return fn
        base = 0xE0000000
        cases = {'PARAMS names no panel': ('handed', word(pn.FB_SPAN_WORD, 0)), 'PARAMS fb_pa is another page': ('handed', word(pn.FB_PA_WORD, base + 0x4000)),
                 'PARAMS fb_span is shorter': ('handed', word(pn.FB_SPAN_WORD, 0x1514000)), 'PARAMS fb_span is longer': ('handed', word(pn.FB_SPAN_WORD, 0x1518000 + 0x4000)),
                 'PARAMS hands a panel to the first choice': ('withheld', word(pn.FB_SPAN_WORD, 0x1518000)),
                 'Video base is zero': ('handed', args(pn.VIDEO_AT, 0)), 'graphics mode': ('handed', args(pn.VIDEO_AT + 8, 1)),
                 'another row length': ('handed', args(pn.VIDEO_AT + 16, 11520 + 64)), 'another width': ('handed', args(pn.VIDEO_AT + 24, 2879)),
                 'another height': ('handed', args(pn.VIDEO_AT + 32, 1919)), 'another depth': ('handed', args(pn.VIDEO_AT + 40, 16)),
                 'Video for the first choice': ('withheld', args(pn.VIDEO_AT, base)),
                 'the panel console command line': ('handed', args(pn.CMDLINE_AT, b'-v debug=0x8 serial=2 cluster_power=0 wdt=-1\0')),
                 'the panel console command line for the first choice': ('withheld', args(pn.CMDLINE_AT, b'-v debug=0x8 serial=2 cluster_power=0 wdt=-1\0')),
                 'the loader command line for the panel as the console': ('console', args(pn.CMDLINE_AT, b'-v debug=0x8 serial=3 cluster_power=0 wdt=-1\0')),
                 'a command line with both bits of serial': ('console', args(pn.CMDLINE_AT, b'-v debug=0x8 serial=3 serial=2 cluster_power=0 wdt=-1\0')),
                 'a console command line without -v': ('console', args(pn.CMDLINE_AT, b'debug=0x8 serial=2 cluster_power=0 wdt=-1\0')),
                 'a console command line that goes on': ('console', args(pn.CMDLINE_AT, pn.CMDLINES['console'].encode() + b' extra=1\0')),
                 'PARAMS names no panel for the panel as the console': ('console', word(pn.FB_SPAN_WORD, 0)),
                 'Video base is zero for the panel as the console': ('console', args(pn.VIDEO_AT, 0)),
                 'graphics mode for the panel as the console': ('console', args(pn.VIDEO_AT + 8, 1)),
                 'a command line without -v': ('handed', args(pn.CMDLINE_AT, b'debug=0x8 serial=3 cluster_power=0 wdt=-1\0')),
                 'no boot_args page': ('handed', lambda built, xb, lay: built['chunks'].pop(lay.handoff_pa + pn.BOOT_ARGS_AT)),
                 'no boot_args page for the panel as the console': ('console', lambda built, xb, lay: built['chunks'].pop(lay.handoff_pa + pn.BOOT_ARGS_AT))}
        for name, (choice, fault) in cases.items():
            with self.subTest(name):
                code, text, err = record('seeded', choice, fault)
                self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=PANEL_MISMATCH'), (name, err))

    def test_the_synthetic_kind_is_checked_for_a_panel_it_does_not_have(self):
        real = ch.synthetic_built

        def built(xb, a, b, c, seed, box):
            got = real(xb, a, b, c, seed, box)
            at = ch.layout_of(xb).params_pa
            words = list(struct.unpack(f'<{ch.PARAMS_WORDS}Q', got['chunks'][at]))
            words[pn.FB_SPAN_WORD] = 0x4000
            got['chunks'][at] = struct.pack(f'<{ch.PARAMS_WORDS}Q', *words)
            return got
        with mock.patch.object(ch, 'synthetic_built', built):
            self.refused('PANEL_MISMATCH', AUTH.template(AUTH.stage('synthetic-panel')))
            self.assertEqual(BT.template(stores='ram,el2_sysreg')[0], 0, 'a record of another kind has no such check')


class Layout(AUTH.Need):
    """The word indexes of struct params against the monitor's own translation unit (the compile of tools/test-sp11-stick-monitor-auth.py, Layout)."""
    compile = AUTH.Layout.compile

    def test_fb_pa_and_fb_span_are_where_the_reader_looks(self):
        asserts = '\n'.join([f'_Static_assert(offsetof(struct params, fb_pa) == {pn.FB_PA_WORD * 8:#x}, "fb_pa");', f'_Static_assert(offsetof(struct params, fb_span) == {pn.FB_SPAN_WORD * 8:#x}, "fb_span");',
                             f'_Static_assert(offsetof(struct params, mem_high) == {ch.PARAMS_MEM_HIGH * 8:#x}, "mem_high");'])
        done = self.compile(asserts, 'panel')
        self.assertEqual(done.returncode, 0, done.stderr[-2500:])

    def test_the_drift_check_can_fail(self):
        self.assertNotEqual(self.compile(f'_Static_assert(offsetof(struct params, fb_span) == {pn.FB_SPAN_WORD * 8 + 8:#x}, "wrong");', 'wrong').returncode, 0)


# ---- (c) the text -----------------------------------------------------------------------------------------------------------------------------------------
class Withheld(BT.Case):
    def test_the_record_of_the_first_choice_is_what_it_was(self):
        self.assertEqual(sha(STATE['withheld'].encode()), GOLDEN['withheld'])

    def test_it_names_no_panel_range_and_has_none_of_the_lines_of_the_choices_that_hand_the_panel(self):
        text = STATE['withheld']
        self.assertEqual(record_problems(text, 'withheld'), [])
        for needle in ('3c.', 'panel range', 'panel_base', 'Panel handed', 'M1RUN-PANEL', 'M1RUN-CONSOLE', 'framebuffer: two reads', 'PANEL_MISMATCH',
                       'RAM inside the placement, the tail of REPORT.DAT', 'serial=2'):
            self.assertNotIn(needle, text)
        self.assertIn('panel withheld: the guest is given no framebuffer', text)
        self.assertIn('No disk and no RAM', ' '.join(text.split()))


class Handed(BT.Case):
    def setUp(self):
        self.text, self.panel = STATE['handed'], STATE['panel']['handed'][0]
        self.flat = ' '.join(self.text.split())

    def test_the_record_is_consistent_with_its_guest(self):
        self.assertEqual(record_problems(self.text, 'handed', self.panel), [])

    def test_the_first_line_and_section_1a_say_which_choice_it_is(self):
        self.assertIn(f'(format 1, label M1RUN-PANEL-{LABEL})', self.text.splitlines()[0])
        self.assertIn('   D9 as built  panel handed: the guest is given the unit\'s framebuffer\n                SPTM HVC call patch 0: a stock kernel\n', self.text)
        self.assertIn('   panel        0xe0000000 + 0x1518000 bytes, 2880 x 1920, text mode: section 3c\n', self.text)
        self.assertLess(self.text.index('1a. What this build is'), self.text.index('1b. The monitor entry run'))

    def test_it_opens_with_a_summary_that_names_the_range_and_the_expectation(self):
        head = self.flat.split('3c. The panel handed to the guest (D9, second choice: panel handed, stock kernel; docs/SP11-STICK-MONITOR.md section 20) ')[1]
        self.assertTrue(head.startswith(f'In short. This run is the first run with the unit\'s framebuffer, {self.panel["fb_span"]} bytes at {self.panel["fb_pa"]:#x}, handed to the '
                                        'guest to read and write. The kernel\'s text is expected on the MON CON lines of the report and not on the panel'), head[:200])
        self.assertIn('what the panel shows is UNKNOWN and is a result, not a criterion', head.split('This record is the first run again')[0])

    def test_it_says_what_the_guest_receives_in_numbers_read_from_the_built_guest(self):
        for needle in ('base 0xe0000000, 0x1518000 bytes, 2880 x 1920, stride 2880 pixels (11520 bytes), format 1 (BGRX)',
                       'fb_pa 0xe0000000, fb_span 0x1518000 (22118400 bytes, 21.1 MiB): that range on 16 KiB pages (words 32 and 33 of struct params)',
                       'Video: v_baseAddr 0xe0000000, v_display 0 (text mode), v_rowBytes 11520, v_width 2880, v_height 1920, v_depth 32',
                       'command line -v debug=0x8 serial=3 cluster_power=0 wdt=-1', 'PANEL_MISMATCH if it is anything else'):
            self.assertIn(needle, self.flat)

    def test_it_says_how_stage_2_maps_it_and_that_the_guest_can_write_it(self):
        for needle in ('to itself, read-write and never execute, as Normal write-back inner-shareable memory', 'The guest can therefore read and write all of it',
                       'an instruction fetch from it is a stage-2 fault', 'the guest gets the first part of it and nothing else is mapped'):
            self.assertIn(needle, self.flat)

    def test_it_says_that_the_kernels_text_is_expected_on_the_serial_route_and_not_on_the_panel_and_what_the_panel_needs(self):
        for needle in ('serial=3: bit 0 is set', 'goes to the monitor\'s console window and not to the panel', 'MON CON lines', 'at most 6 lines of 64 characters',
                       'with serial=2, whenever it hands the guest a framebuffer (its default, and --console panel', '`real` makes that replacement for --panel console only',
                       'which is another GUEST.DAT and another record, so this record is of the panel handed and not of the panel as the console: for kernel text on the '
                       'panel, take that one', 'glyphs are X5', 'What the panel shows with this one is UNKNOWN'):
            self.assertIn(needle, self.flat)
        self.assertNotIn('`real` has no such argument', self.flat, 'the option exists now')

    def test_it_says_what_stays_withheld_and_what_the_descriptor_lists(self):
        for needle in ('Stage 2 maps no display, GPU or DCP register for the guest', 'its scan-out goes on reading this memory (INFERRED from the map; not shown on the unit)',
                       'it describes the board and does not control what the guest reaches', 'stage 2 does'):
            self.assertIn(needle, self.flat)
        listed = re.search(r'states (.*?) as present and (.*?) as absent: it describes', self.flat)
        self.assertEqual((listed.group(1).split(', '), listed.group(2).split(', ')), (self.panel['present'], self.panel['absent']))
        self.assertIn('display', self.panel['present'] + self.panel['absent'])

    def test_it_says_what_the_monitor_does_with_the_range_and_that_the_page_does_not_show_it(self):
        for needle in ('It reads all of it twice, as 2764800 loads of 8 bytes', 'DC CIVAC on each of its 345600 lines of 64 bytes', 'when the guest\'s SCTLR_EL1 is not zero',
                       'no line of SNAPREP-M1.BIN reads them: the page does not say whether the guest wrote the panel', 'UNKNOWN. Both reads lie outside the time box'):
            self.assertIn(needle, self.flat)

    def test_it_says_that_the_screen_is_drawn_over_and_how_to_film_it(self):
        for needle in ('glyph pixels only and with no background', 'can make it hard or impossible to read',
                       'The film. It is the only record of what the guest paints: the report is drawn over it, and nothing is written to the stick (row f of section 5a is untouched). '
                       'Film as step 2 of section 5c says, with the whole panel in frame. It must show the runner\'s line PAYLOAD CALL 0, which spends the approval and is printed '
                       'before the guest runs (the guest can paint over it, so that only the film shows it: if the film does not show it, treat the approval as spent), the panel '
                       'while the guest runs, and the SNAPREP report with the first UPTIME heartbeat after it. Then stay on the final screen for ten seconds (a margin, not a '
                       'measurement) before step 6, and hand the video back as step 6 says. A photograph taken afterwards is not enough.'):
            self.assertIn(needle, self.flat)

    def test_it_names_the_range_in_the_ram_class_the_placement_and_group_e_and_adds_no_class(self):
        blocks = sections(self.text)
        self.assertIn('2', blocks)
        self.assertIn('2. Placement (the one range the run may store to, and the panel range of section 3c, which the guest may write)', self.text)
        self.assertIn('and the panel range of section 3c (outside the placement: the guest writes it', ' '.join(blocks['3'].split()))
        self.assertIn('ram: RAM inside the placement, the tail of REPORT.DAT and the panel range of section 3c, as the GIC and stop dry run (which has no panel), and what '
                      'the monitor and its guest add.', ' '.join(blocks['3'].split()))
        self.assertIn(f'   panel_base {self.panel["fb_pa"]:#x}  panel_size {self.panel["fb_span"]:#x}  panel_end {self.panel["fb_pa"] + self.panel["fb_span"]:#x}  '
                      '(outside the placement; the guest writes it: section 3c)\n', blocks['2'])
        self.assertIn('No disk and no RAM outside the placement, REPORT.DAT and the panel range', ' '.join(blocks['3'].split()))
        self.assertIn('   range, the reserve and the panel range of section 3c.\n', self.text)
        self.assertIn('Store class. The ram class covers the range', self.flat)
        self.assertIn('PIN.DAT\'s authorised_stores stays 7', self.flat)
        self.assertIn('(PIN.DAT authorised_stores = 7)', self.text)

    def test_the_time_budget_counts_the_panel_in_the_clean_and_names_the_reads_it_cannot_count(self):
        text = self.text
        place = re.search(r'guest_size (\d+) MiB  reserve (\d+) MiB', text)
        stop = re.search(r'stop: clean of (\d+) MiB, the bound\s+at most (\d+\.\d) s \(INFERRED: the dry run verified', text)
        self.assertEqual(int(stop.group(1)), (int(place.group(1)) * MIB + int(place.group(2)) * MIB + self.panel['fb_span']) // MIB)
        self.assertEqual(float(stop.group(2)), round(0.96 * int(stop.group(1)) / 141.4, 1))
        low = re.search(r'the known parts, low sum\s+about (\d+\.\d+) s \((\d+\.\d) \+ 8 \+ 5 \+ 0\.04\)', text)
        high = re.search(r'the known parts, high sum\s+at most (\d+\.\d) s \((\d+\.\d) \+ 8 \+ 5 \+ (\d+\.\d)\), plus the prepare\n {47}and the two reads of the '
                         r'framebuffer, UNKNOWN \(section 3c\)', text)
        self.assertEqual(round(float(low.group(2)) + 8 + 5 + 0.04, 1), float(low.group(1)))
        self.assertEqual(float(high.group(3)), float(stop.group(2)))
        self.assertEqual(round(float(high.group(2)) + 8 + 5 + float(stop.group(2)), 1), float(high.group(1)))
        self.assertRegex(text, r'framebuffer: two reads of the panel\s+UNKNOWN  never run here: in the prepare, and at the stop')
        self.assertIn(f'about {float(low.group(1))} to {high.group(1)} s plus the prepare in all', self.flat)
        self.assertIn('The two reads of the framebuffer (section 3c) are CPU time at EL2 on top of the figures above; their length is UNKNOWN.', self.flat)

    def test_it_adds_the_stop_rule_of_the_panel_and_keeps_the_others(self):
        after = self.text.split('5. Refusal and stop rules')[1]
        for line in (*rec.M1_STOP_RULES, *pn.stop_rules(self.panel, 60)):
            self.assertIn(line, after)
        self.assertIn('a touch outside RAM and outside that range still is', ' '.join(after.split()))
        for line in (*m1g.PRE_CALL_SILENCE, *m1g.RESTART_AND_STAGE2_RULES, *m1g.HARDWARE_RULES):
            self.assertIn(line, after)

    def test_it_is_ascii_within_120_columns_and_carries_no_verified_label(self):
        self.assertTrue(self.text.isascii())
        for name, block in (('3c', self.text.split('\n3c. ')[1].split('\n4. Time box')[0]), ('4', self.text.split('4. Time box')[1].split('5. Refusal')[0]),
                            ('3 ram', '   ram: ' + self.text.split('   ram: ')[1].split('   el2_sysreg:')[0])):
            self.assertLessEqual(max(map(len, block.splitlines())), 120, name)
        self.assertNotIn('VERIFIED', self.text, 'no label of a hardware result, and none of anything the host proves only for a binary')

    def test_the_section_comes_between_3b_and_4_and_the_record_is_deterministic(self):
        self.assertLess(self.text.index('3b. Device code'), self.text.index('3c. The panel handed'))
        self.assertLess(self.text.index('3c. The panel handed'), self.text.index('4. Time box'))
        code, again, err = record('again', 'handed')
        self.assertEqual((code, again), (0, self.text), err)

    def test_the_record_is_the_one_that_was_made(self):
        self.assertEqual(sha(self.text.encode()), GOLDEN['handed'], 'a changed byte of this record is another record: re-pin it on purpose, with the doc')


class Seeded(BT.Case):
    """A record that does not match its guest must fail the check, for its own reason."""

    def test_the_record_of_the_first_choice_for_a_guest_that_is_given_the_panel_fails(self):
        for choice in ('handed', 'console'):
            with self.subTest(choice=choice):
                problems = record_problems(STATE['withheld'], choice, STATE['panel'][choice][0])
                self.assertIn('section 1a does not say what the guest is given', problems)
                self.assertTrue([p for p in problems if 'does not say 3c. The panel' in p], problems)

    def test_the_record_of_a_choice_that_hands_the_panel_for_a_guest_that_is_given_none_fails(self):
        for choice in ('handed', 'console'):
            with self.subTest(choice=choice):
                problems = record_problems(STATE[choice], 'withheld')
                self.assertIn('section 1a does not say what the guest is given', problems)
                self.assertIn('the record of the first choice speaks of a panel range', problems)

    def test_the_record_of_one_panel_choice_for_the_guest_of_the_other_fails(self):
        for choice, other in (('handed', 'console'), ('console', 'handed')):
            with self.subTest(choice=choice, guest=other):
                problems = record_problems(STATE[choice], other, STATE['panel'][other][0])
                self.assertIn('section 1a does not say what the guest is given', problems)
                self.assertTrue([p for p in problems if p.startswith('it carries the section 3c of the other choice') or p.startswith('it does not say')], problems)

    def test_a_record_that_omits_the_range_or_a_part_of_what_the_guest_gets_fails(self):
        for choice in ('handed', 'console'):
            panel, text = STATE['panel'][choice][0], ' '.join(STATE[choice].split())      # a phrase can run over a line break in the record: mutate the text as the reader sees it
            header = {'handed': '3c. The panel handed to the guest', 'console': '3c. The panel as the kernel\'s console'}[choice]
            line = {'handed': 'serial=3', 'console': 'serial=2'}[choice]
            for what, mutated in {'the base': text.replace(f'{panel["fb_pa"]:#x}', 'ADDRESS'), 'the span': text.replace(f'{panel["fb_span"]:#x}', 'SIZE'),
                                  'the geometry': text.replace('2880 x 1920', 'W x H'), 'section 3c': text.replace(header, '3c. Something'),
                                  'the film': text.replace('the whole panel in frame', 'the panel'), 'the console route': text.replace(line, 'serial=X')}.items():
                with self.subTest(choice=choice, what=what):
                    self.assertTrue([p for p in record_problems(mutated, choice, panel) if p.startswith('it does not say')], what)

    def test_a_record_that_still_says_the_first_choice_s_things_fails(self):
        for choice in ('handed', 'console'):
            panel, flat = STATE['panel'][choice][0], ' '.join(STATE[choice].split())
            for old, new in ((f'panel {choice}: {rec.PANEL_WORDS[choice]}', f'panel withheld: {rec.PANEL_WORDS["withheld"]}'),
                             ('No disk and no RAM outside the placement, REPORT.DAT and the panel range', 'No disk and no RAM outside the placement and REPORT.DAT'),
                             ('range, the reserve and the panel range of section 3c.', 'range and the reserve.'),
                             ('RAM inside the placement, the tail of REPORT.DAT and the panel range of section 3c, as the GIC and stop dry run (which has no panel), and what',
                              'RAM inside the placement and the tail of REPORT.DAT, as the GIC and stop dry run, and what'),
                             (f'panel_base {panel["fb_pa"]:#x} panel_size {panel["fb_span"]:#x} panel_end {panel["fb_pa"] + panel["fb_span"]:#x} '
                              '(outside the placement; the guest writes it: section 3c)', ''),
                             ('2. Placement (the one range the run may store to, and the panel range of section 3c, which the guest may write)',
                              '2. Placement (the one range the run may store to)')):
                with self.subTest(choice=choice, old=old[:40]):
                    self.assertIn(old, flat)
                    self.assertTrue(record_problems(flat.replace(old, new), choice, panel))

    def test_the_generator_with_a_line_of_the_panel_left_out_is_another_record_that_fails(self):
        for choice in ('handed', 'console'):
            panel = STATE['panel'][choice][0]
            for what, patch in {'no section 3c': mock.patch.object(pn, 'section_3c', lambda panel, label: ()),
                                'the ram class unchanged': mock.patch.object(pn, 'touches', lambda base, panel: base),
                                'no placement line': mock.patch.object(pn, 'placement_line', lambda panel: ''),
                                'no line in section 1a': mock.patch.object(pn, 'real_lines', lambda panel: [])}.items():
                with self.subTest(choice=choice, what=what), patch:
                    code, text, err = record('mutant', choice)
                    self.assertEqual(code, 0, err)
                    self.assertNotEqual(text, STATE[choice])
                    self.assertTrue(record_problems(text, choice, panel), what)

    def test_a_change_of_the_first_run_s_text_under_the_lines_of_the_panel_does_not_go_unnoticed(self):
        base = {'ram': ('a', 'b'), 'el2_sysreg': ('c',)}
        with self.assertRaises(AssertionError):
            pn.touches(base, {'x': 1})


# ---- (e) hygiene ------------------------------------------------------------------------------------------------------------------------------------------
class Hygiene(unittest.TestCase):
    def test_the_new_files_are_ascii_with_an_spdx_line_within_the_size_limits_and_hold_no_machine_data(self):
        for rel in ('tools/sp11_stick_bundle_panel.py', 'tools/test-sp11-stick-monitor-panel.py', 'tools/test-sp11-stick-monitor-console.py'):
            data = (ROOT / rel).read_bytes()
            with self.subTest(rel):
                self.assertFalse([b for b in data if b > 127], 'ASCII only')
                self.assertIn(b'SPDX-License-Identifier: MIT', data[:400])
                self.assertLess(len(data), 51200)
                self.assertLess(data.count(b'\n'), 1500)
                for needle in BT.MACHINE_PATHS:
                    self.assertNotIn(needle.encode(), data)

    def test_the_refusal_is_named_in_the_note_and_is_none_of_the_other_lists(self):
        self.assertEqual(pn.REFUSALS, ('PANEL_MISMATCH',))
        self.assertTrue(set(pn.REFUSALS) <= ch.KNOWN_REFUSALS)
        self.assertFalse(set(pn.REFUSALS) & (set(m1.REFUSALS) | set(bb.BUILDER_REFUSALS) | {n for _, n, _ in sb.ERRORS}))
        self.assertTrue(all(re.fullmatch(r'[A-Z0-9_]{1,24}', n) for n in pn.REFUSALS))
        note = (ROOT / 'docs/SP11-STICK-MONITOR.md').read_text()
        for name in (*pn.REFUSALS, 'tools/sp11_stick_bundle_panel.py', 'tools/test-sp11-stick-monitor-panel.py', 'tools/test-sp11-stick-monitor-console.py', '--panel handed',
                     '--panel console', 'AUTHORISATION_CAP', '131,072'):
            self.assertIn(name, note)

    def test_the_caps_of_the_notes_were_raised_for_it_and_the_notes_fit(self):
        note = (ROOT / 'docs/SP11-STICK-MONITOR.md').read_bytes()
        self.assertLessEqual(len(note), AUTH.DOC_CAP)
        self.assertGreater(AUTH.DOC_CAP, 98000, 'raised on purpose for the panel as the console, on top of the text of the record (section 19); nothing was cut')
        self.assertLessEqual(len((ROOT / 'docs/SP11-STICK-BUILD.md').read_bytes()), 37000, 'raised from 35,500 on purpose for the console option, to 36,000 for it and to 37,000 for --boot-arg (K10)')
        self.assertLess(len((ROOT / 'docs/SP11-STICK-REAL.md').read_bytes()), 33000, 'raised from 30,000 on purpose for the console rows, to 31,000 for them, to 32,000 for boot 21\'s words and the rows of rung 3, and to 33,000 for rung 5\'s')

    def test_the_note_of_the_real_path_has_the_row_of_the_console_guest_and_the_digest_of_the_other_two_are_what_they_were(self):
        rows = ' '.join((ROOT / 'docs/SP11-STICK-REAL.md').read_text().split())
        for digest in ('9e69e7b5612802f00948465fb620e879d36c9b1ac419be1b9cf079dcda463c15', 'd8a71856becb48df98cfcd8184177be9743d0aae9dfbb3850f5f6439426a6947',
                       'b74826c66352c55737bb1bcb3a9e747c926dd4c9871581dab9a4d77ae9b0ed5c', '5391ea4fc992a3f1ae96e2df8491ea62ac2e3b336dfbf90d3354f1c2b70bdae6'):
            self.assertIn(digest, rows, 'the digests of the guests of the first two choices are not changed by the console choice')
        self.assertRegex(rows, r'\| GUEST\.DAT, panel console \| 118,908,480 bytes, 46 chunks')
        self.assertRegex(rows, r'\| GUEST\.DAT SHA-256, console, 0 \| [0-9a-f]{64} \|')


if __name__ == '__main__':
    unittest.main()
