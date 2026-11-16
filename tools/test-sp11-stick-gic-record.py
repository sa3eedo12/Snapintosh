#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of what the GIC and stop stage leaves to be read (docs/SP11-STICK-GIC.md sections 5 to 7), monitor port work item 8: the record in
REPORT.DAT and its check, the reporter's reading of a record that is missing, damaged or extreme, the width of every line for any record that
passes the check, the line budget of every scenario, and the authorisation record of the run (tools/sp11_stick_bundle_build.py --gic-stop).

    python3 tools/test-sp11-stick-gic-record.py
    Q1N1_SP11_TREE=/nonexistent python3 tools/test-sp11-stick-gic-record.py      (the tests that need q1n1's headers and sha256.c SKIP)

A record is damaged by the harness between the monitor's run and the reporter's (tools/test-sp11-stick-gic.c, `GIC MUTATE offset bytes`), so
that the reporter reads what a damaged REPORT.DAT would hold; the check the reporter must make is written here from the document's section 5 and
compared with what it printed. The width test mutates a real record to the widest values the check lets through and compares every line with
the line rendered independently (a line that the C had to truncate would differ) and with the panel's 100 characters.

No hardware, nothing under /Volumes, no network. Everything is written below build/sp11-wrapper-test-scratch-gic-record/, deleted at the end.
A SKIP is never a PASS. EXPERIMENTAL; hardware NOT RUN."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_gic_checks as C  # noqa: E402
import sp11_stick_gic_testlib as G  # noqa: E402
import sp11_stick_wrapper_testlib as T  # noqa: E402
from sp11_stick_gic_checks import RECORD_AT, Base, prefixed  # noqa: E402
from sp11_stick_wrapper_checks import Checks as WC, expected_reporter_lines  # noqa: E402
from sp11_stick_wrapper_testlib import sb  # noqa: E402

MUTATED = {}


def le(value, size):
    return value.to_bytes(size, 'little', signed=value < 0).hex()


# name -> (offset, little-endian bytes, the code the check must give), written from the document's section 5.
IRQ0, WIN0, WIN1, IRQ_LAST = G.IRQ_AT, G.WIN_AT, G.WIN_AT + G.WIN_SIZE, G.IRQ_AT + (G.IRQ_N - 1) * G.IRQ_SIZE
DAMAGED = {
    'magic': (0x00, le(0x4141414141414141, 8), G.E_MAGIC), 'version': (0x08, le(3, 4), G.E_VERSION), 'size': (0x0C, le(256, 4), G.E_SIZE),
    'reserved0': (0x7C, le(1, 4), G.E_RESERVED), 'gicr-index': (0x68, le(sb.GICR_MAX + 1, 4), G.E_RESERVED), 'win-reserved': (WIN1 + 0x1C, le(1, 4), G.E_RESERVED),
    'outcome': (0x10, le(99, 4), G.E_VALUE), 'first-outcome': (0x2C, le(99, 4), G.E_VALUE), 'substage': (0x14, le(12, 4), G.E_VALUE),
    'planned': (0x18, le(0x40, 4), G.E_VALUE), 'begun': (0x1C, le(0x40, 4), G.E_VALUE), 'wrote': (0x20, le(0x40000, 4), G.E_VALUE),
    'restore-failed': (0x24, le(0x40000, 4), G.E_VALUE), 'restore-retried': (0x28, le(0x40000, 4), G.E_VALUE), 'done': (0x30, le(2, 4), G.E_VALUE),
    'cycle': (0x34, le(5, 4), G.E_VALUE), 'intid-high': (0x6C, le(32, 4), G.E_VALUE), 'intid-low': (0x6C, le(15, 4), G.E_VALUE),
    'timebox': (0x40, le(G.TICKS_LIMIT + 1, 8), G.E_VALUE), 'slack': (0x48, le(G.TICKS_LIMIT + 1, 8), G.E_VALUE),
    'cycle-ticks': (0x50, le(G.TICKS_LIMIT + 1, 8), G.E_VALUE), 'irq-flags': (IRQ0 + 0x34, le(0x40, 4), G.E_VALUE),
    'hppir-vec': (IRQ0 + 0x28, le(0x1000000, 4), G.E_VALUE), 'hppir-post': (IRQ0 + 0x2C, le(0x1000000, 4), G.E_VALUE),
    'lat-ticks-high': (IRQ0 + 0x38, le(G.LATENCY_CLAMP + 1, 8), G.E_VALUE), 'lat-ticks-low': (IRQ0 + 0x38, le(-G.LATENCY_CLAMP - 1, 8), G.E_VALUE),
    'lat-us-high': (IRQ0 + 0x40, le(G.LAT_US_CLAMP + 1, 8), G.E_VALUE), 'lat-us-low': (IRQ0 + 0x40, le(-G.LAT_US_CLAMP - 1, 8), G.E_VALUE),
    'el1-lat-us-high': (IRQ_LAST + 0x40, le(G.LAT_US_CLAMP + 1, 8), G.E_VALUE), 'win-irq-entries': (WIN0 + 0x10, le(0x10000, 4), G.E_VALUE),
    'win-other-entries': (WIN1 + 0x14, le(0x10000, 4), G.E_VALUE), 'win-hvc-end': (WIN1 + 0x18, le(2, 4), G.E_VALUE)}
# The same fields at the last value the check lets through.
LIMITS = {
    'lat-ticks': (IRQ0 + 0x38, le(G.LATENCY_CLAMP, 8)), 'lat-ticks-neg': (IRQ0 + 0x38, le(-G.LATENCY_CLAMP, 8)), 'lat-us': (IRQ0 + 0x40, le(G.LAT_US_CLAMP, 8)),
    'lat-us-neg': (IRQ0 + 0x40, le(-G.LAT_US_CLAMP, 8)), 'timebox': (0x40, le(G.TICKS_LIMIT, 8)), 'slack': (0x48, le(G.TICKS_LIMIT, 8)),
    'cycle-ticks': (0x50, le(G.TICKS_LIMIT, 8)), 'wrote': (0x20, le(0x3FFFF, 4)), 'entries': (WIN0 + 0x10, le(0xFFFF, 4)), 'cycle': (0x34, le(4, 4)),
    'hppir': (IRQ0 + 0x28, le(0xFFFFFF, 4)), 'intid-16': (0x6C, le(16, 4)), 'intid-31': (0x6C, le(31, 4)), 'gicr-index': (0x68, le(sb.GICR_MAX, 4))}


def widest(outcome, first, entries, hvc):
    """Patches that make a record as wide as the check allows: every register as wide as it can be read, the longest names, every count at its
    limit. `entries` is the IRQ entry count of both windows: zero makes the page print the diagnoses, not the IRQ lines; `hvc` is the stub's HVC flag."""
    ones = 'ff' * 8
    patches = [(0x10, le(outcome, 4)), (0x14, le(10, 4)), (0x18, le(0x3F, 4)), (0x1C, le(0x3F, 4)), (0x20, le(0x3FFFF, 4)), (0x24, le(0x3FFFF, 4)),
               (0x28, le(0x3FFFF, 4)), (0x2C, le(first, 4)), (0x30, le(1, 4)), (0x34, le(4, 4)), (0x38, ones), (0x40, le(G.TICKS_LIMIT, 8)),
               (0x48, le(G.TICKS_LIMIT, 8)), (0x50, le(G.TICKS_LIMIT, 8)), (0x58, ones), (0x60, ones), (0x68, le(sb.GICR_MAX, 4)), (0x6C, le(31, 4)),
               (0x70, 'ff' * 4), (0x74, 'ff' * 4), (0x78, 'ff' * 4), (0x80, ones), (0x88, ones), (0x90, ones), (0x98, ones), (0xA0, ones)]
    for i in range(G.IRQ_N):
        at = IRQ0 + i * G.IRQ_SIZE
        patches += [(at, 'ff' * 40), (at + 0x28, le(0xFFFFFF, 4) * 2), (at + 0x30, 'ff' * 4), (at + 0x34, le(0x3F, 4)), (at + 0x38, le(-G.LATENCY_CLAMP, 8)),
                    (at + 0x40, le(-G.LAT_US_CLAMP, 8))]
    for w in range(G.WIN_N):
        at = WIN0 + w * G.WIN_SIZE
        patches += [(at, 'ff' * 16), (at + 0x10, le(entries, 4) + le(0xFFFF, 4)), (at + 0x18, le(hvc, 4)), (at + 0x20, 'ff' * 96)]
    return patches + [(G.REG_TABLE, 'ff' * (32 * len(G.REGS)))]


WIDEST = {'verdict': widest(G.OUT.WINDOW_UNWRITABLE, G.OUT.PRIO_NOT_BELOW_PMR, 0xFFFF, 0), 'diagnosis': widest(G.OUT.NO_IRQ_FROM_EL1, G.OUT.NO_IRQ, 0, 1),
          'not-found': widest(G.OUT.GICR_NOT_FOUND, G.OUT.NOT_RESTORED, 0xFFFF, 0)}


def mutate_lines(patches):
    """`GIC MUTATE` lines of at most 64 bytes each."""
    return [f'MUTATE {off + i // 2:#x} {data[i:i + 128]}' for off, data in patches for i in range(0, len(data), 128)]


def setUpModule():
    C.set_up('gic-record', cases=('happy', 'extreme', 'not-authorised'))
    if C.runnable():
        cases = {f'damaged-{n}': dict(between=mutate_lines([(o, d)])) for n, (o, d, _) in DAMAGED.items()}
        cases.update({f'limit-{n}': dict(between=mutate_lines([(o, d)])) for n, (o, d) in LIMITS.items()})
        cases.update({f'widest-{n}': dict(between=mutate_lines(p)) for n, p in WIDEST.items()})
        cases['junk'] = dict(bundle='plain', extra=[f'PREFILL {RECORD_AT:#x} 0x300 0xff'])
        MUTATED.update(C.batch(cases))


def tearDownModule():
    C.tear_down()


class Tables(unittest.TestCase):
    def test_the_names_the_pages_print_are_the_headers(self):
        outcomes, substages = G.header_enums()
        self.assertEqual((outcomes, substages), (G.OUTCOMES, G.SUBSTAGES))

    def test_the_record_is_172_words_in_its_own_slot_and_every_register_has_a_bit_in_the_written_mask(self):
        self.assertEqual((G.REC_BYTES, G.REC_AT, G.REC_SLOT, G.MONITOR_WINDOW), (1376, 0x11000, 0x1000, 0x10000))
        self.assertEqual((len(G.REGS), G.REG_TABLE + 32 * len(G.REGS)), (18, G.REC_BYTES))
        self.assertEqual((G.IRQ_AT + G.IRQ_N * G.IRQ_SIZE, G.WIN_AT + G.WIN_N * G.WIN_SIZE), (G.WIN_AT, G.REG_TABLE))
        self.assertLessEqual(G.REC_AT + G.REC_SLOT, sb.REPORT_BYTES)
        self.assertEqual(G.REC_AT, sb.REPORT_OUT + 0x10000, 'the first extension slot starts where the monitor window ends')


class Reporter(Base):
    def reads(self, run):
        """What the reporter must print: its ordinary lines and then, for the record the harness left in REPORT.DAT, the lines the document gives,
        or the one line that names a record that cannot be read, or the one that says there is none. Returns None for none, else the check's code."""
        raw, rep = run.g.rec, run.rep
        missing, code = not any(raw[:8]), G.check(raw)
        tail = ['SNAPREP GIC NO RECORD: THE STAGE DID NOT RUN'] if missing else \
            [f'SNAPREP GIC RECORD DAMAGED CODE={code}'] if code else prefixed('SNAPREP', G.expected(G.decode(raw), G.REPORTER_ORDER))
        self.assertEqual(rep.texts(), expected_reporter_lines(run.mon.head()) + tail)
        self.assertEqual(WC.fault(rep) + WC.lines(rep, sb.REPORTER_LINES), [])
        self.assertEqual([f'{k} at {e.a:#x}' for k in ('ST', 'CVAC', 'CIVAC', 'DSB', 'ISB', 'AT', 'MRS') for e in rep.of(k)], [], 'the reporter stores nothing')
        self.assertEqual((rep.ret, rep.faulted), (0, False))
        return None if missing else code

    def test_a_missing_record_is_said_to_be_missing(self):
        run = C.RUNS['not-authorised']
        self.assertIsNone(self.reads(run))
        self.assertEqual(run.rep.texts()[-1], 'SNAPREP GIC NO RECORD: THE STAGE DID NOT RUN')

    def test_a_record_of_junk_is_named_damaged_and_not_printed(self):
        run = MUTATED['junk']
        self.assertEqual(self.reads(run), G.E_MAGIC)
        self.assertEqual(run.rep.texts()[-1], 'SNAPREP GIC RECORD DAMAGED CODE=1')

    def test_each_damaged_field_is_named_by_the_class_of_the_check_that_it_fails(self):
        for name, (_, _, code) in DAMAGED.items():
            with self.subTest(field=name):
                self.assertEqual(self.reads(MUTATED[f'damaged-{name}']), code)

    def test_the_last_value_the_check_lets_through_is_printed_and_the_next_is_not(self):
        for name in LIMITS:
            with self.subTest(field=name):
                self.assertEqual(self.reads(MUTATED[f'limit-{name}']), 0)

    def test_the_check_of_a_real_record_of_every_scenario_is_clean(self):
        for name in ('happy', 'extreme'):
            self.assertEqual(G.check(C.RUNS[name].g.rec), 0, name)
            self.assertEqual(self.reads(C.RUNS[name]), 0, name)


class Widths(Base):
    def test_no_line_of_a_record_that_passes_the_check_is_longer_than_the_panel_and_none_is_cut(self):
        for name in WIDEST:
            run = MUTATED[f'widest-{name}']
            with self.subTest(record=name):
                self.assertEqual(self.reads_clean(run), 0)
                longest = max(line.length for line in run.rep.lines)
                self.assertLessEqual(longest, sb.LINE_MAX)
                self.assertLessEqual(len(run.rep.lines), 9 + 23, 'the dry run reporter prints at most 9 lines and the record at most 23 more')
                self.assertGreaterEqual(longest, 95, 'the widest record must reach the widest lines: this test is vacuous if it does not')

    def reads_clean(self, run):
        raw = run.g.rec
        code = G.check(raw)
        want = expected_reporter_lines(run.mon.head()) + prefixed('SNAPREP', G.expected(G.decode(raw), G.REPORTER_ORDER))
        self.assertEqual(run.rep.texts(), want, 'a line that the C cut short differs from the one rendered here')
        self.assertEqual(WC.lines(run.rep, sb.REPORTER_LINES), [])
        return code

    def test_the_wrappers_own_widest_lines_fit_too(self):
        run = C.RUNS['extreme']
        self.assertEqual(WC.lines(run.mon, sb.WRAPPER_LINES), [])
        self.assertLessEqual(max(line.length for line in run.mon.lines), sb.LINE_MAX)
        self.pages(run)


# ---- The line budget of every scenario of the main run is tools/test-sp11-stick-gic.py's; the authorisation record is here ------------------------
class Authorisation(unittest.TestCase):
    NAMED = ('ICC_PMR_EL1', 'ICC_BPR1_EL1', 'ICC_IGRPEN1_EL1', 'CNTHP_CVAL_EL2', 'CNTHP_CTL_EL2', 'VBAR_EL2', 'DAIF', 'ICC_EOIR1_EL1', 'ICC_DIR_EL1',
             'SPSR_EL2', 'ELR_EL2', 'HCR_EL2', 'VBAR_EL1', 'SCTLR_EL1', 'IC IVAU', 'DC CIVAC', 'HVC', 'ERET', 'GICD_CTLR', 'IPRIORITYR', 'ISENABLER0',
             'ICFGR1', 'IGROUPR0', 'ICENABLER0', 'ICPENDR0', 'INTID 26')

    def opts(self, stores, *extra):
        fx = C.FIX['fixtures']
        return ['--facts', fx.facts, '--base', hex(T.LOW), '--guest-mib', '256', '--monitor', fx.monitor, '--reporter', fx.reporter, '--stores', stores,
                '--time-box-s', '5', *extra, '--image-mib', '8', '--tables-mib', '1', '--sptm-data-mib', '4', *[x for d in fx.dumps for x in ('--dump', d)]]

    def template(self, stores, *extra):
        return T.run_builder(['authorisation-template', 'synthetic', *self.opts(stores, *extra)])

    def test_the_record_of_the_gic_stop_run_names_the_classes_the_registers_and_the_windows(self):
        code, text, err = self.template('ram,el2_sysreg,gic', '--gic-stop')
        self.assertEqual((code, err), (0, ''))
        self.assertTrue(text.isascii())
        section = text.split('3. Store classes authorised')[1].split('   Not authorised:')[0]
        for name in self.NAMED:
            self.assertIn(name, section)
        for cls in ('ram', 'el2_sysreg', 'gic'):
            self.assertEqual(sum(1 for line in section.split('\n') if line.startswith(f'   {cls}: ')), 1, cls)
        for sentence in ('SNAPMON-GIC.BIN', 'no monitor is called', 'at most twice the time box', 'pinned frames only', 'one security'):
            self.assertIn(sentence, section)
        self.assertIn('authorised_stores = 7', text)
        self.assertIn('Hardware: NOT RUN', text)
        self.assertLessEqual(max(len(line) for line in section.split('\n')), 120)

    def test_the_default_record_is_the_one_the_pinned_tests_compare(self):
        _, default, _ = self.template('ram,el2_sysreg,gic')
        for pinned in ('GIC registers for the timer stop (PPI 26). Which registers is UNKNOWN until the wrapper (work item 4) and its',
                       'stores no EL2 system register (docs/SP11-STICK-WRAPPER.md section 4: MRS reads, no MSR)', 'authorised_stores = 7'):
            self.assertIn(pinned, default)
        for new in ('SNAPMON-GIC.BIN', 'CNTHP_CVAL_EL2'):
            self.assertNotIn(new, default)
        _, gic, _ = self.template('ram,el2_sysreg,gic', '--gic-stop')
        self.assertEqual((gic.split('3. Store classes authorised')[0], gic.split('   Not authorised:')[1]),
                         (default.split('3. Store classes authorised')[0], default.split('   Not authorised:')[1]), 'only the classes of section 3 differ')

    def test_the_flag_needs_the_gic_class(self):
        code, text, err = self.template('ram,el2_sysreg', '--gic-stop')
        self.assertEqual(code, 1)
        self.assertIn('REFUSED=OPTIONS_BAD', text + err)

    def test_the_bundle_carries_the_digest_of_the_gic_stop_record_and_the_gic_class(self):
        _, text, _ = self.template('ram,el2_sysreg,gic', '--gic-stop')
        fields = C.FIX['gic'].fields
        self.assertEqual((fields['authorised_stores'], bytes(fields['authorisation_sha256']).hex()), (7, T.sha(text.encode())))
        self.assertEqual(C.FIX['plain'].fields['authorised_stores'], 3)

    def test_a_record_approved_without_the_flag_does_not_approve_the_bundle_built_with_it(self):
        _, default, _ = self.template('ram,el2_sysreg,gic')
        record = T.write(C.FIX['scratch'] / 'default-record.txt', default.encode())
        code, text, err = T.run_builder(['synthetic', *self.opts('ram,el2_sysreg,gic', '--gic-stop'), '--no-manifest', '--out', str(C.FIX['scratch'] / 'refused'),
                                         '--authorisation', record])
        self.assertEqual(code, 1)
        self.assertIn('REFUSED=AUTH_RECORD_MISMATCH', text + err)


if __name__ == '__main__':
    unittest.main()
