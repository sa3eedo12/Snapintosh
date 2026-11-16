#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests for what boot 13 changed in the SP11 GIC and stop stage (docs/SP11-STICK-GIC.md sections 4, 10 and 12), monitor port work item 8, pass 5: the model
reproduces the page the unit printed (the stage as it ran is the seeded stage `no-quarantine`), the firmware's other enabled interrupts are quarantined for the
run and enabled again as the stage's last write, an interrupt that the quarantine does not cover refuses the arm before IRQ is unmasked, the survey of the
reads, and the six seeded-bad stages of these steps. The happy path, the windows, every other refusal and the other seeded-bad stages are
tools/test-sp11-stick-gic.py; the record and the reporter are tools/test-sp11-stick-gic-record.py; the binary is tools/test-sp11-stick-gic-build.py.

    python3 tools/test-sp11-stick-gic-quarantine.py
    Q1N1_SP11_TREE=/nonexistent python3 tools/test-sp11-stick-gic-quarantine.py      (the tests that need q1n1's headers and sha256.c SKIP)

The code under test is the REAL wrapper, stage and reporter (probes/sp11-stick-*.c built with SNAP_SP11_STICK_GIC=1 and SP11_STICK_HOST_TEST=1) over the model of
the machine of tools/test-sp11-stick-gic.c; what the model proves is the stage's order, bounds, put-back and report against the model, and nothing about the
unit's GIC. The expected values are written from the document and from the unit's page, not from the C. Each seeded-bad stage (a test define) must FAIL
the check that matches its fault, and the real stage must pass that check.

No hardware, nothing under /Volumes, no network. Everything is written below build/sp11-wrapper-test-scratch-gic-quarantine/, deleted at the end. A SKIP is
never a PASS. EXPERIMENTAL; hardware NOT RUN."""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_gic_checks as C  # noqa: E402
import sp11_stick_gic_testlib as G  # noqa: E402
from sp11_stick_gic_checks import (CASES, HZ, OUT, QUARANTINE_SEEDS, RUNS, SEEDS, UNIT13_LINES, UNIT_LINES, Base, Held, after_last_window, batch,  # noqa: E402
                                   cvals, expected_writes)

NEEDED = ('happy', 'happy-enabled', 'happy-other-enabled', 'unit-boot12', 'unit-pending', 'unit-active', 'owned-pending', 'owned-active', 'unit-boot13', 'foreign-spi',
          'quarantine-ignored', 'quarantine-putback-ignored', 'survey-none', 'survey-bound', 'survey-spis', 'survey-ppi-prio', 'timers-read', 'extreme', 'sre-clear',
          'timer-asserted', 'ctlr-0')


def setUpModule():
    C.set_up('gic-quarantine', cases=NEEDED, variants=QUARANTINE_SEEDS)


def tearDownModule():
    C.tear_down()


def run_of(name):
    return RUNS[name]


# ---- The state boot 12 found: the firmware had enabled other PPIs, and PPI 26 itself ------------------------------------------------------------
class UnitState(Base):
    """OBSERVED once, on the unit (boot 12, from a photo of the page): ISENABLER0 0x4E800001 (SGI 0 and PPIs 23, 25, 26, 27 and 30), IPRIORITYR[26] 0xA0, every
    PPI level, PPI 26 neither pending nor active, GICD_CTLR 0x10. The first stage refused PPI_OWNED because the whole register was not clear and stored
    nothing; the second accepted it and left every other bit as found, and boot 13 showed what that costs (INTID 27 ended the first window). Now only PPI 26
    pending or active is refused, and every other enabled bit is disabled for the run by the quarantine and enabled again as the stage's last write."""

    def test_the_model_prints_the_two_lines_the_unit_printed_and_the_reporter_shows_what_was_found(self):
        run = run_of('unit-boot12')
        for line in UNIT_LINES:
            self.assertEqual(run.mon.texts().count(line), 1, line)
        self.assertIn('SNAPREP GIC OLD PRIO=0XA0 ICFGR1=0X0 IGROUPR0=0X0 ISPEND=0X0 ISACT=0X0', run.rep.texts())
        self.assertEqual(run.rep.texts().count(UNIT_LINES[1].replace('SNAPMON', 'SNAPREP')), 1)

    def test_that_state_reaches_irq_ok_with_a_clean_put_back_in_the_documented_order(self):
        run = run_of('unit-boot12')
        self.verdict(run, OUT.IRQ_OK)
        self.assertEqual((run.mon.ret, run.mon.faulted, run.rec.restore_failed, run.rec.restore_retried), (0, False, 0, 0))
        self.holds(run, G.Checks.eoi(run.g), G.Checks.timer_off(run.g), G.Checks.restored(run.g), G.Checks.acks(run.g, 4))
        self.assertEqual(run.g.writes(), expected_writes(cvals=cvals(run.rec), prio=0xA0, isen=0x4E800001))
        self.pages(run)

    def test_ppi_26s_own_enable_is_never_written_when_it_was_found_enabled_and_the_others_are_quarantined_exactly(self):
        for name, found in (('unit-boot12', 0x4E800001), ('happy-enabled', 1 << 26), ('happy-other-enabled', 1 << 30), ('happy', 0)):
            run = run_of(name)
            with self.subTest(scenario=name):
                self.assertEqual((run.g.state['isenabler0'], run.rec.reg('ISENABLER0', 'old'), run.rec.reg('ISENABLER0', 'fin')), ((found, found), found, found))
                self.holds(run, G.Checks.quarantine(run.g, found), G.Checks.bit26_kept(run.g, found), G.Checks.quarantine_first(run.g, found),
                           G.Checks.quarantine_last(run.g, found), G.Checks.foreign_quiet_in_windows(run.g, found), G.Checks.pending_untouched(run.g))
                quar = found & ~(1 << 26)
                self.assertEqual(run.rec.sv.quarantined, quar)
                self.assertEqual((bool(run.rec.sv.flags & G.SVF['QUAR']), run.rec.sv.quar_rb), (bool(quar), found & (1 << 26) if quar else 0),
                                 'the enable register read back right after the quarantine is exactly PPI 26 as found')
                self.assertEqual(bool(run.rec.wrote & G.REG_BIT['ISENABLER0']), bool(quar) or not found & (1 << 26), 'the record claims the writes that were made')

    def test_other_enabled_bits_alone_do_not_refuse_and_are_disabled_for_the_run_and_enabled_again_last(self):
        run = run_of('happy-other-enabled')
        self.verdict(run, OUT.IRQ_OK)
        self.assertEqual(([v for n, v in run.g.writes() if n == 'GICR_ISENABLER0'], [v for n, v in run.g.writes() if n == 'GICR_ICENABLER0']),
                         ([1 << 26, 1 << 30], [1 << 30, 1 << 26]))
        self.assertEqual(run.g.writes()[0], ('GICR_ICENABLER0', 1 << 30))
        self.assertEqual(run.g.writes()[-1], ('GICR_ISENABLER0', 1 << 30))
        self.assertEqual(run.g.state['isenabler0'], (1 << 30, 1 << 30))

    def test_ppi_26_pending_or_active_is_still_refused_before_any_store_and_the_reporter_shows_the_whole_masks(self):
        for name, pend, act in (('unit-pending', 0x4000000, 0), ('unit-active', 0, 0x4000000), ('owned-pending', 0x4000000, 0), ('owned-active', 0, 0x4000000)):
            run = run_of(name)
            with self.subTest(scenario=name):
                self.refused(run, OUT.PPI_OWNED, 'COPIED')
                self.assertEqual(run.g.writes(), [])
                self.assertEqual(run.rec.begun, 0)
                prio = '0XA0' if name.startswith('unit') else '0X0'
                self.assertIn(f'SNAPREP GIC OLD PRIO={prio} ICFGR1=0X0 IGROUPR0=0X0 ISPEND={G.h(pend)} ISACT={G.h(act)}', run.rep.texts())

    def test_the_cpu_interface_and_the_timer_are_not_read_by_a_refusal_in_the_redistributor_read(self):
        reads = {e[1] for e in run_of('unit-pending').g.log if e[0] == 'R'}
        self.assertFalse({n for n in reads if n.startswith('ICC_') or n.startswith('CNTHP_')}, 'as on the unit in boot 12: the zeros of the CPU line are unfilled')


# ---- The state boot 13 found: another interrupt, INTID 27, pending and enabled before any store ------------------------------------------------------
UNIT13_KW = dict(prio=0xA0, isen=0x4E800001, eoimode=1, pmr=0xF8, bpr1=3, igrpen1=0, ctl0=2)     # what boot 13 read, for expected_writes


class UnitBoot13(Base):
    """OBSERVED once, on the unit (boot 13, from a video of the page): the stage got through its reads, stores and window 1 and took INTID 27, not 26, about 2 s
    before the CNTHP deadline; it ended WRONG_INTID and put everything back. A model that does not print that page cannot be trusted for what changes it, so
    the first tests here run the stage as it ran (the seeded stage that does not quarantine, does not check HPPIR1 and puts nothing of that back) and compare
    every line the unit printed; the others run the stage as it is now over the same state. Only the latencies are compared by sign and size: the model's counter
    steps by a fixed amount per read, the unit's did not."""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.as_run = batch({'unit-boot13': CASES['unit-boot13']}, variant='no-quarantine')['unit-boot13']

    def lines_of(self, run):
        return [t[len('SNAPREP '):] for t in run.rep.texts() if t.startswith('SNAPREP GIC ')]

    def check_page(self, run, which=range(len(UNIT13_LINES))):
        shown, found = self.lines_of(run), {}
        for n in which:
            want = UNIT13_LINES[n]
            pattern = re.escape(want).replace('<LAT>', r'(-\d+)')
            hits = [m for m in (re.fullmatch(pattern, line) for line in shown) if m]
            self.assertEqual(len(hits), 1, f'{want!r} is on the reporter page exactly once: {shown}')
            found[n] = [int(v) for v in hits[0].groups()]
        return found

    def test_the_model_prints_the_page_the_unit_printed_line_for_line(self):
        run = self.as_run
        found = self.check_page(run)
        ticks, us = found[7]
        self.assertTrue(-38400000 < ticks <= -38300000, f'the interrupt came about 2 s before the deadline: {ticks} ticks')
        self.assertEqual((found[6], us), ([us], -(-ticks * 10 ** 6 // HZ)), 'the page prints the latency in microseconds, truncated toward zero')
        for n in (0, 1, 2, 6, 9, 10):
            pattern = re.escape('SNAPMON ' + UNIT13_LINES[n]).replace('<LAT>', r'-\d+')
            self.assertEqual(sum(1 for t in run.mon.texts() if re.fullmatch(pattern, t)), 1, f'{UNIT13_LINES[n]!r} is on the wrapper page exactly once')

    def test_the_stage_as_it_ran_took_one_foreign_interrupt_ended_the_window_on_it_and_put_everything_back(self):
        run = self.as_run
        r, g = run.rec, run.g
        self.assertEqual((G.OUTCOMES[r.outcome], G.OUTCOMES[r.first_outcome], r.outcome, r.restore_failed, r.restore_retried), ('WRONG_INTID', 'WRONG_INTID', 0x22, 0, 0))
        self.assertEqual((run.mon.ret, run.mon.faulted, (r.irq[0].iar, r.irq[0].hppir_vec, r.irq[0].hppir_post), r.win[0].irq_entries, r.cycle), (0, False, (27, 27, 27), 1, 0))
        self.assertEqual((g.state['acks'], g.state['excursions'], g.state['hang'], g.nviol), (1, 0, 0, 0))
        self.holds(run, G.Checks.restored(g), G.Checks.next_before_store(run.mon, g))
        self.assertEqual(g.state['gicd_ctlr'], (0x10, 0x10))
        self.assertEqual(g.state['isenabler0'], (0x4E800001, 0x4E800001))
        self.assertEqual([w for w in g.writes() if w[0] in ('GICR_ICENABLER0', 'GICR_ISENABLER0')], [], 'boot 13 wrote neither enable register: no foreign enable was touched')

    def test_with_the_quarantine_the_same_state_reaches_irq_ok_with_every_foreign_enable_off_in_the_windows_and_its_pending_state_untouched(self):
        run = run_of('unit-boot13')
        r, g = run.rec, run.g
        found = 0x4E800001
        self.verdict(run, OUT.IRQ_OK)
        self.assertEqual((run.mon.ret, run.mon.faulted, r.restore_failed, r.restore_retried, g.nviol, g.state['hang']), (0, False, 0, 0, 0, 0))
        self.holds(run, G.Checks.quarantine(g, found), G.Checks.bit26_kept(g, found), G.Checks.quarantine_first(g, found), G.Checks.quarantine_last(g, found),
                   G.Checks.foreign_quiet_in_windows(g, found), G.Checks.pending_untouched(g), G.Checks.eoi(g), G.Checks.acks(g, 4), G.Checks.timer_off(g),
                   G.Checks.restored(g))
        self.assertEqual(([w for w in g.writes() if w[0] == 'GICR_ICENABLER0'], [w for w in g.writes() if w[0] == 'GICR_ISENABLER0']),
                         ([('GICR_ICENABLER0', 0x4A800001)], [('GICR_ISENABLER0', 0x4A800001)]), 'ICENABLER0 once and ISENABLER0 once, and no other write of either')
        want = [w for w in expected_writes(cvals=cvals(r), **UNIT13_KW) if w != ('ICC_PMR_EL1', 0xF8)]       # PMR reads 0xF8 whatever is written: no put-back to make
        self.assertEqual(g.writes(), want)
        self.assertEqual((r.sv.hppir_pre, r.sv.quarantined, r.sv.quar_rb, r.sv.flags & ~G.SVF['TIMERS']), (1023, 0x4A800001, 0x4000000, 0x1E))
        self.assertEqual([r.irq[k].iar & 0xFFFFFF for k in (0, 1, 2, 4)], [26] * 4, 'every interrupt of the windows is PPI 26')
        self.assertEqual((g.state['isenabler0'], r.reg('ISENABLER0', 'fin')), ((found, found), found))
        self.assertEqual(r.reg('ISPENDR0', 'old'), 0x8000000, 'INTID 27 was pending when the stage read it, and the stage never wrote its pending state')
        self.check_page(run, which=(0, 1, 2, 3, 5))
        self.pages(run)


# ---- The quarantine and the survey: what refuses, what does not hold, and what the reads saw -------------------------------------------------------------
class Quarantine(Base):
    FOUND13 = 0x4E800001

    def test_a_foreign_spi_that_is_enabled_and_pending_is_refused_with_its_intid_after_the_stores_and_before_any_window(self):
        run = run_of('foreign-spi')
        r, g = run.rec, run.g
        self.refused(run, OUT.FOREIGN_PENDING, 'COPIED')
        self.assertEqual((r.sv.hppir_pre, r.sv.flags & G.SVF['HPPIR'], r.sv.spi_both, r.sv.spi_first, G.classes(r.begun), r.restore_failed, r.restore_retried),
                         (40, G.SVF['HPPIR'], 1, 40, 'GRC', 0, 0))
        self.assertEqual((g.state['deliveries'], g.state['acks'], g.state['excursions'], g.nviol), (0, 0, 0, 0), 'the vectors were never entered')
        self.assertNotIn(('DAIF', 0), g.writes(), 'IRQ was never unmasked')
        self.holds(run, G.Checks.no_window_with_foreign_pending(g), G.Checks.restored(g), G.Checks.quarantine(g, self.FOUND13), G.Checks.quarantine_first(g, self.FOUND13),
                   G.Checks.quarantine_last(g, self.FOUND13), G.Checks.next_before_store(run.mon, g))
        self.assertIn('SNAPMON GIC QUAR MASK=0X4A800001 DONE=0X4000000 HPPIR=40', run.mon.texts(), 'the INTID is on the wrapper page')
        self.assertIn('SNAPREP GIC SPI LINES=30 EN=1 PEND=1 BOTH=1 FIRST=40 SECOND=0', run.rep.texts())
        self.assertEqual(run.mon.head()['refusal_detail'], OUT.FOREIGN_PENDING)
        self.pages(run)

    def test_a_quarantine_that_does_not_hold_is_named_stores_nothing_else_and_is_put_back(self):
        run = run_of('quarantine-ignored')
        r, g = run.rec, run.g
        self.refused(run, OUT.Q_NOT_HELD, 'COPIED')
        self.assertEqual((G.classes(r.begun), r.sv.quarantined, r.sv.quar_rb, r.restore_failed, r.sv.flags & G.SVF['HPPIR']), ('R', 0x4A800001, self.FOUND13, 0, 0))
        self.assertEqual(g.writes(), [('GICR_ICENABLER0', 0x4A800001)], 'one write, and no store of G, R or C after it')
        self.assertEqual([e for e in g.log if e[1] == 'WINDOW_OPEN'], [])
        self.holds(run, G.Checks.restored(g), G.Checks.next_before_store(run.mon, g))
        self.assertIn('SNAPMON GIC QUAR MASK=0X4A800001 DONE=0X4E800001 HPPIR=-', run.mon.texts())
        self.pages(run)

    def test_a_put_back_that_does_not_restore_the_enable_register_is_tried_once_more_and_named_not_restored(self):
        run = run_of('quarantine-putback-ignored')
        r, g = run.rec, run.g
        self.refused(run, OUT.NOT_RESTORED, 'PREPARED', first=OUT.IRQ_OK)
        self.assertEqual((r.restore_failed, r.restore_retried), (G.REG_BIT['ISENABLER0'], G.REG_BIT['ISENABLER0']))
        self.assertEqual(len(after_last_window(g, 'GICR_ISENABLER0')), 2, 'the put-back, one retry and no third attempt')
        self.assertEqual([k for k, v in g.state.items() if isinstance(v, tuple) and v[0] != v[1]], ['isenabler0'], 'only that register is left changed')
        self.assertEqual((r.reg('ISENABLER0', 'fin'), g.state['isenabler0']), (0x4000000, (self.FOUND13, 0x4000000)))
        self.pages(run)

    def test_the_survey_reads_the_spi_registers_one_to_itlines_once_each_and_no_other_distributor_register(self):
        for name, lines in (('unit-boot13', 30), ('survey-none', 0), ('survey-bound', 31), ('extreme', 31), ('happy', 31)):
            run = run_of(name)
            with self.subTest(scenario=name):
                for reg in ('GICD_ISENABLER', 'GICD_ISPENDR'):
                    self.assertEqual(sorted(e[2] for e in run.g.log if e[1] == reg), list(range(1, lines + 1)), f'{reg} 1 to ITLinesNumber, once each')
                self.assertEqual((run.g.nviol, run.rec.sv.spi_lines, bool(run.rec.sv.flags & G.SVF['SPI'])), (0, lines, True))

    def test_the_survey_counts_the_enabled_and_pending_interrupts_and_names_the_first_two(self):
        run = run_of('survey-spis')
        s = run.rec.sv
        self.verdict(run, OUT.IRQ_OK)
        self.assertEqual((s.spi_lines, s.spi_enabled, s.spi_pending, s.spi_both, s.spi_first, s.spi_second, s.hppir_pre), (31, 3, 4, 3, 48, 49, 1023))
        self.assertIn('SNAPREP GIC SPI LINES=31 EN=3 PEND=4 BOTH=3 FIRST=48 SECOND=49', run.rep.texts())
        run = run_of('survey-bound')
        self.assertEqual((run.rec.sv.spi_lines, run.rec.sv.spi_enabled, run.rec.sv.spi_pending, run.rec.sv.spi_both), (31, 1, 1, 0))

    def test_the_survey_reads_the_priorities_of_the_other_enabled_ppis_and_the_el1_timers_in_the_dry_runs_build(self):
        run = run_of('survey-ppi-prio')
        self.assertEqual(run.rec.sv.ppi_prio, 0x44332211)
        self.assertIn('SNAPREP GIC PPIP P23=0X11 P25=0X22 P27=0X33 P30=0X44', run.rep.texts())
        run = run_of('timers-read')
        s = run.rec.sv
        self.assertEqual((s.cntv_ctl, s.cntv_cval, s.cntp_ctl, s.cntp_cval, s.flags & G.SVF['TIMERS']), (5, 0x1122334455667788, 3, 0xAABBCCDD, G.SVF['TIMERS']))
        self.assertIn('SNAPREP GIC TIMERS VCTL=0X5 VCVAL=0X1122334455667788 PCTL=0X3 PCVAL=0XAABBCCDD', run.rep.texts())
        reads = [e[1] for e in run.g.log if e[0] == 'R']
        self.assertEqual([n for n in reads if n.startswith(('CNTV', 'CNTP'))], ['CNTV_CTL_EL0', 'CNTV_CVAL_EL0', 'CNTP_CTL_EL0', 'CNTP_CVAL_EL0'], 'once each')

    def test_a_refusal_in_the_reads_leaves_the_survey_unread_and_the_reporter_prints_none_of_it(self):
        for name in ('unit-pending', 'sre-clear', 'timer-asserted', 'ctlr-0'):
            run = run_of(name)
            with self.subTest(scenario=name):
                self.assertEqual((run.rec.sv.flags, run.rec.sv.ppi_prio, run.rec.sv.spi_lines), (0, 0, 0))
                self.assertEqual([t for t in run.rep.texts() if t.startswith(('SNAPREP GIC SPI', 'SNAPREP GIC PPIP', 'SNAPREP GIC TIMERS', 'SNAPREP GIC QUAR'))], [])
                self.assertFalse({e[1] for e in run.g.log if e[0] == 'R'} & {'GICD_ISENABLER', 'GICD_ISPENDR', 'CNTV_CTL_EL0'})

    def test_with_nothing_to_quarantine_there_is_no_quarantine_store_and_the_line_says_none(self):
        for name in ('happy', 'happy-enabled'):
            run = run_of(name)
            with self.subTest(scenario=name):
                self.assertEqual((run.rec.sv.quarantined, run.rec.sv.flags & G.SVF['QUAR'], run.rec.sv.hppir_pre), (0, 0, 1023))
                self.assertIn('SNAPREP GIC QUAR MASK=NONE HPPIR=1023', run.rep.texts())
                self.assertEqual([w for w in run.g.writes() if w[0] == 'GICR_ICENABLER0' and w[1] != 1 << 26], [])


# ---- Seeded-bad stages: each must fail the check that matches its fault ----------------------------------------------------------------------------
class Seeded(Base):
    FOUND = 0x4E800001
    DETECTORS = {
        'no-quarantine': ('unit-boot13', lambda run: G.Checks.foreign_quiet_in_windows(run.g, Seeded.FOUND) + G.Checks.quarantine(run.g, Seeded.FOUND)),
        'quarantine-no-putback': ('unit-boot13', lambda run: G.Checks.quarantine_last(run.g, Seeded.FOUND) + G.Checks.restored(run.g)),
        'quarantine-bit26': ('unit-boot13', lambda run: G.Checks.bit26_kept(run.g, Seeded.FOUND)),
        'quarantine-stray-write': ('unit-boot13', lambda run: G.Checks.stray_writes(run.g)),
        'no-hppir-check': ('foreign-spi', lambda run: G.Checks.no_window_with_foreign_pending(run.g)),
        'quarantine-after-g': ('unit-boot13', lambda run: G.Checks.quarantine_first(run.g, Seeded.FOUND)),
    }

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.seeded = {seed: batch({n: CASES[n] for n in ('unit-boot13', 'foreign-spi')}, variant=seed) for seed in QUARANTINE_SEEDS}

    def test_there_is_a_seeded_stage_for_every_fault_of_these_steps_and_the_two_files_hold_every_seed(self):
        self.assertEqual(set(self.DETECTORS), set(QUARANTINE_SEEDS))
        self.assertLessEqual(set(QUARANTINE_SEEDS), set(SEEDS))

    def test_each_seeded_fault_breaks_the_check_that_matches_it_and_the_real_stage_passes_that_check(self):
        for seed, (scenario, check) in self.DETECTORS.items():
            with self.subTest(seed=seed):
                self.assertEqual(check(run_of(scenario)), [], 'the real stage must pass the check')
                self.assertNotEqual(check(self.seeded[seed][scenario]), [], f'the {seed} stage passed the check that is meant to catch it')

    def test_the_seeded_stages_are_the_real_stage_but_for_their_fault(self):
        run = self.seeded['no-quarantine']['unit-boot13']
        self.assertEqual((G.OUTCOMES[run.rec.outcome], run.rec.restore_failed, run.g.state['acks']), ('WRONG_INTID', 0, 1),
                         'boot 13: the foreign interrupt ends the first window and the stage puts everything back')
        run = self.seeded['quarantine-no-putback']['unit-boot13']
        self.assertEqual((G.OUTCOMES[run.rec.outcome], run.rec.restore_failed, run.g.state['isenabler0']), ('IRQ_OK', 0, (0x4E800001, 0x4000000)),
                         'the stage that never verifies the enable register cannot see that it lost the firmware\'s enables')
        run = self.seeded['quarantine-bit26']['unit-boot13']
        self.assertEqual((G.OUTCOMES[run.rec.outcome], run.rec.restore_failed, run.g.state['isenabler0']), ('Q_NOT_HELD', 0, (0x4E800001, 0x4E800001)),
                         'the stage itself sees that PPI 26 is not as found after the quarantine, and puts everything back')
        run = self.seeded['quarantine-stray-write']['unit-boot13']
        self.assertEqual((G.OUTCOMES[run.rec.outcome], run.g.nviol >= 1), ('IRQ_OK', True), 'the model refuses a write to another CPU\'s frame; the stage does not see it')
        run = self.seeded['quarantine-after-g']['unit-boot13']
        self.assertEqual((G.OUTCOMES[run.rec.outcome], run.rec.restore_failed), ('IRQ_OK', 0), 'only the order of the writes shows it')
        run = self.seeded['no-hppir-check']['foreign-spi']
        self.assertEqual((G.OUTCOMES[run.rec.outcome], run.rec.irq[0].iar & 0xFFFFFF, run.g.state['acks']), ('WRONG_INTID', 40, 1),
                         'without the check the window opens and takes the SPI first, as boot 13 took INTID 27')
        for seed in ('no-quarantine', 'quarantine-no-putback', 'quarantine-stray-write', 'quarantine-after-g'):
            with self.subTest(seed=seed):
                self.assertEqual(Held.page_text(self.seeded[seed]['unit-boot13']), [])


if __name__ == '__main__':
    unittest.main()
