#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests for the SP11 GIC and stop stage of SNAPMON-GIC.BIN (docs/SP11-STICK-GIC.md section 9), monitor port work item 8: the happy path, the
three acknowledged interrupts and the final deadline taken without an acknowledge (contract C4), the interrupt taken from EL1 (contract C6),
every refusal before a store, the windows that end without the interrupt, the stores and put-backs that do not hold, and the seeded-bad stages.
The record, the reporter, the line widths and the authorisation record are tools/test-sp11-stick-gic-record.py; the binary is
tools/test-sp11-stick-gic-build.py; the real runner under QEMU is tools/test-sp11-stick-gic-qemu.py.

    python3 tools/test-sp11-stick-gic.py
    Q1N1_SP11_TREE=/nonexistent python3 tools/test-sp11-stick-gic.py      (the tests that need q1n1's headers and sha256.c SKIP)

The code under test is the REAL wrapper, stage and reporter (probes/sp11-stick-*.c built with SNAP_SP11_STICK_GIC=1 and SP11_STICK_HOST_TEST=1),
linked with the harness tools/test-sp11-stick-gic.c under AddressSanitizer and UndefinedBehaviorSanitizer. The harness is the dry-run wrapper's
(tools/test-sp11-stick-wrapper.c) with a MODEL of the machine added: the distributor, this CPU's redistributor frame, the CPU interface, the EL2
timer with its level-triggered PPI 26, HCR_EL2, VBAR_EL1 and SCTLR_EL1 with the routing of the interrupt by HCR_EL2.IMO, and the two windows that
the vector tables' assembly implements on the unit. What the model proves is the stage's order, bounds, put-back and report against the model;
what it cannot prove is the assembly (tools/test-sp11-stick-gic-build.py reads its disassembly, tools/test-sp11-stick-gic-qemu.py runs it) and
the unit's GIC, which no test here says anything about. The expected values are written from docs/SP11-STICK-GIC.md and
tools/sp11_stick_gic_testlib.py, not from the C. Each seeded-bad stage (a test define) must FAIL the check that matches its fault, and the real
stage must pass that check: that proves the checks can fail.

No hardware, nothing under /Volumes, no network. Everything is written below build/sp11-wrapper-test-scratch-gic-host/, deleted at the end. A
SKIP is never a PASS: without clang or a q1n1 checkout the harness tests report SKIP. EXPERIMENTAL; hardware NOT RUN."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_gic_checks as C  # noqa: E402
import sp11_stick_gic_testlib as G  # noqa: E402
from sp11_stick_gic_checks import (CASES, EDGE26, FAKE_VECTORS, FUTURE_CVAL, HZ, OLD_HCR, OLD_VBAR, OUT, QUARANTINE_SEEDS, RECORD_AT, RUNS, SEEDS,  # noqa: E402
                                   STAGE, Base, Held, after_last_window, batch, cvals, el2_ok, expected_wrote, expected_writes)
from sp11_stick_wrapper_checks import Checks as WC, expected_reporter_lines  # noqa: E402
from sp11_stick_wrapper_testlib import E, sb  # noqa: E402


def setUpModule():
    C.set_up('gic-host', variants=tuple(s for s in SEEDS if s not in QUARANTINE_SEEDS))


def tearDownModule():
    C.tear_down()


def run_of(name):
    return RUNS[name]


# ---- The happy path ---------------------------------------------------------------------------------------------------------------------------
class Happy(Base):
    VARIANTS = {'happy': {}, 'happy-ds1': dict(ctlr=0x50), 'happy-g-on': dict(ctlr=0x13), 'happy-edge': dict(cfg1=EDGE26),
                'happy-eoimode': dict(eoimode=1), 'happy-prio': dict(prio=0x80, pmr=0xF0, bpr1=2, igrpen1=1), 'happy-frame3': {},
                'happy-other-pend': {}, 'happy-delay': {}, 'happy-late': {}, 'happy-kept': {},
                'happy-enabled': dict(isen=1 << 26), 'happy-other-enabled': dict(isen=1 << 30), 'unit-boot12': dict(prio=0xA0, isen=0x4E800001),
                'happy-timer-masked': dict(ctl0=3), 'happy-timer-future': dict(ctl0=1, cval0=FUTURE_CVAL),
                'happy-timer-off': dict(ctl0=0, cval0=0x123456789)}

    def each(self, check):
        for name, kw in self.VARIANTS.items():
            with self.subTest(scenario=name):
                check(run_of(name), kw)

    def test_the_interrupts_arrive_are_ended_and_everything_is_put_back(self):
        def check(run, kw):
            r, mon = run.rec, run.mon
            self.assertEqual((mon.ret, mon.faulted), (0, False))
            self.verdict(run, OUT.IRQ_OK)
            self.assertEqual((r.intid, r.win[0].irq_entries, r.win[0].other_entries, r.win[1].irq_entries, r.win[1].other_entries, r.win[1].hvc_end),
                             (26, 4, 0, 1, 0, 0))
            self.assertEqual((r.irq[0].iar & 0xFFFFFF, r.irq[4].iar & 0xFFFFFF, r.done, r.cycle, r.restore_failed, r.restore_retried), (26, 26, 1, 4, 0, 0))
            self.holds(run, G.Checks.eoi(run.g), G.Checks.timer_off(run.g), G.Checks.restored(run.g), G.Checks.next_before_store(mon, run.g),
                       G.Checks.next_before_el1(mon, run.g), G.Checks.acks(run.g, 4))
            head = mon.head()
            self.assertEqual((head['stage'], head['refusal'], head['pin_code'], head['guest_code']), (STAGE.DONE, 0, 0, 0))
            self.assertEqual(mon.texts()[-1], 'SNAPMON MONITOR NOT CALLED: UNIMPLEMENTED IN THIS BUILD')
            self.assertEqual(sum(1 for t in mon.texts() if ' GIC ' in t or 'NEXT GIC' in t), 10)
            self.assertEqual((run.g.state['acks'], run.g.state['excursions'], run.g.state['eoi']), (4, 1, 1))
        self.each(check)

    def test_the_stores_are_the_documented_ones_in_the_documented_order(self):
        self.each(lambda run, kw: self.assertEqual(run.g.writes(), expected_writes(cvals=cvals(run.rec), **kw)))

    def test_the_record_holds_what_was_found_what_was_written_and_read_back(self):
        def check(run, kw):
            r, d = run.rec, {'ctlr': 0x10, 'prio': 0, 'cfg1': 0, 'igroupr0': 0, 'pmr': 0xFF, 'bpr1': 0, 'igrpen1': 0, 'isen': 0, 'ctl0': 0, 'cval0': 0, **kw}
            istatus = 4 if d['ctl0'] & 1 and d['cval0'] == 0 else 0       # the model derives ISTATUS from the deadline: CVAL 0 is long past
            self.assertEqual(G.Checks.saved(run.g, {'GICD_CTLR': d['ctlr'], 'IPRIORITYR': d['prio'], 'ICFGR1': d['cfg1'], 'IGROUPR0': d['igroupr0'],
                                                    'ICC_PMR': d['pmr'], 'ICC_BPR1': d['bpr1'], 'ICC_IGRPEN1': d['igrpen1'], 'ISENABLER0': d['isen'],
                                                    'HCR_EL2': OLD_HCR, 'SCTLR_EL1': C.OLD_SCTLR1, 'VBAR_EL1': C.OLD_VBAR1,
                                                    'CNTHP_CTL': d['ctl0'] | istatus, 'CNTHP_CVAL': d['cval0']}), [])
            self.assertEqual((r.reg('CNTHP_CTL', 'fin') & 3, r.reg('CNTHP_CVAL', 'fin')), (d['ctl0'] & 3, d['cval0']), 'the timer ends as it was found')
            self.assertEqual(r.reg('ISENABLER0', 'rb') & 1 << 26, 1 << 26)
            self.assertEqual((r.reg('ISENABLER0', 'new'), r.reg('ISENABLER0', 'fin')), (0 if d['isen'] >> 26 & 1 else 1 << 26, d['isen']),
                             'the enable is written only when the PPI was found disabled, and the register ends as it was found')
            self.assertEqual((r.reg('IPRIORITYR', 'rb'), r.reg('ICC_PMR', 'rb'), r.reg('ICC_IGRPEN1', 'rb') & 1, r.reg('CNTHP_CTL', 'rb') & 1), (0x80, 0xF0, 1, 1))
            self.assertEqual((r.reg('VBAR_EL2', 'old'), r.reg('VBAR_EL2', 'new'), r.reg('VBAR_EL2', 'fin')), (OLD_VBAR, FAKE_VECTORS, OLD_VBAR))
            self.assertEqual([(r.reg(n, 'new'), r.reg(n, 'rb'), r.reg(n, 'fin')) for n in ('HCR_EL2', 'SCTLR_EL1', 'VBAR_EL1')],
                             [(G.EL1_HCR, G.EL1_HCR, OLD_HCR), (G.EL1_SCTLR, G.EL1_SCTLR, C.OLD_SCTLR1), (G.FAKE_EL1_VECTORS, G.FAKE_EL1_VECTORS, C.OLD_VBAR1)])
            self.assertEqual(r.planned, 0x3F if not d['ctlr'] & 2 else 0x3E)
            self.assertEqual((r.begun, r.counter_hz, r.timebox_ticks, r.slack_ticks, r.cycle_ticks), (r.planned, HZ, 2 * HZ, HZ // 1000, HZ // 100))
            self.assertEqual((r.irq[0].cval, r.win[0].deadline), (r.win[0].t_arm + 2 * HZ, r.win[0].t_arm + 4 * HZ + 8 * r.cycle_ticks))
            self.assertEqual((r.irq[4].cval, r.win[1].deadline), (r.win[1].t_arm + 2 * HZ, r.win[1].t_arm + 4 * HZ))
            self.assertEqual((r.cnthctl_el2, r.resume_spsr), (3, 0x3C9 | (0x1000 if run.name == 'happy-kept' else 0)))
            self.assertEqual(r.wrote, expected_wrote(**d))
        self.each(check)

    def test_the_latency_is_measured_against_each_programmed_deadline(self):
        for name, delay in (('happy', 0), ('happy-delay', 5000), ('happy-late', 100000)):
            with self.subTest(scenario=name):
                r = run_of(name).rec
                for k, q in enumerate(r.irq):
                    self.assertEqual((q.lat_ticks, q.t_irq - q.cval), (delay, delay), f'interrupt {k}')
                    self.assertEqual(q.lat_us, delay * 10 ** 6 // HZ)
                    self.assertEqual(G.timing(q.lat_ticks, r.slack_ticks), 'ON_TIME' if delay <= r.slack_ticks else 'LATE')
                self.assertEqual(run_of(name).g.state['hang'], 0)

    def test_the_pages_say_what_the_record_says(self):
        self.each(lambda run, kw: self.pages(run))

    def test_nothing_but_the_distributors_control_register_and_this_cpus_ppi_registers_is_written(self):
        for name in self.VARIANTS:
            written = {e[1] for e in run_of(name).g.log if e[0] == 'W'}
            self.assertEqual(written - G.ALLOWED_GIC - G.ALLOWED_EL2, set(), name)
            self.assertEqual(run_of(name).g.viol, [], name)

    def test_this_cpus_frame_is_found_by_affinity_and_the_others_are_only_asked_for_their_type(self):
        run = run_of('happy-frame3')
        self.assertEqual((run.rec.gicr_index, run.rec.gicr_base), (3, 0x17080000 + 3 * 0x40000))
        self.assertEqual(run.g.viol, [], 'the model refuses any read of another frame but GICR_TYPER')
        self.assertEqual(run.mon.ret, 0)

    def test_the_records_page_is_probed_for_writing_before_anything_is_stored_and_the_monitors_window_is_never_stored_to(self):
        for name in self.VARIANTS:
            self.assertIn((RECORD_AT, 1), [(e.a, e.extra) for e in run_of(name).mon.of('AT')], name)
            self.assertEqual(Held.report(run_of(name)), [], name)
        self.assertEqual(RECORD_AT, run_of('happy').w.REPORT + 0x11000, 'REPORT.DAT + 0x11000: after the 64 KiB the monitor may write from its x0')


# ---- The state boot 12 found, the state boot 13 found, the quarantine, the survey and their seeded-bad stages: tools/test-sp11-stick-gic-quarantine.py -------


# ---- The EL2 timer as it may have been left: only one that is asserting right now is refused -----------------------------------------------------
class ForeignTimer(Base):
    """CNTHP_CTL has never been read on the unit, and a firmware that left PPIs enabled probably had a hypervisor once. Nothing is left to own that timer (no
    code runs at EL2 but the stage, IRQ is masked), so only a timer asserting right now (ENABLE and ISTATUS set, IMASK clear: an interrupt is firing) is refused;
    enabled and masked, enabled with its deadline ahead, and disabled are read, overwritten at the arm and put back exactly, the deadline first."""
    FOUND = {'happy': (0x0, 0), 'happy-timer-masked': (0x7, 0), 'happy-timer-future': (0x1, FUTURE_CVAL), 'happy-timer-off': (0x0, 0x123456789)}

    def test_a_timer_that_is_asserting_now_is_refused_before_any_store(self):
        run = run_of('timer-asserted')
        self.refused(run, OUT.TIMER_OWNED, 'COPIED')
        self.assertEqual((run.g.writes(), run.rec.begun, run.rec.wrote, run.rec.reg('CNTHP_CTL', 'old')), ([], 0, 0, 0x5))
        self.assertIn('CTL=0X5', G.render(run.rec, 'CPU'))
        self.pages(run)
        pend = run_of('timer-asserted-pending')
        self.refused(pend, OUT.PPI_OWNED, 'COPIED')
        self.assertEqual((pend.g.writes(), pend.rec.reg('CNTHP_CTL', 'old')), ([], 0), 'the line is normally seen as PPI 26 pending first: the timer is not read then')

    def test_every_other_state_of_the_timer_reaches_irq_ok_and_ends_as_it_was_found(self):
        for name, (read, cval0) in self.FOUND.items():
            run = run_of(name)
            with self.subTest(scenario=name):
                r, g = run.rec, run.g
                self.verdict(run, OUT.IRQ_OK)
                self.assertEqual((r.reg('CNTHP_CTL', 'old'), r.reg('CNTHP_CVAL', 'old')), (read, cval0))
                self.assertEqual((r.reg('CNTHP_CTL', 'fin') & 3, r.reg('CNTHP_CVAL', 'fin'), r.restore_failed, r.restore_retried), (read & 3, cval0, 0, 0))
                self.assertEqual((g.state['ctl'], g.state['cval']), ((read & 3, read & 3), (cval0, cval0)))
                self.holds(run, G.Checks.timer_off(g), G.Checks.restored(g), G.Checks.acks(g, 4), G.Checks.arm_overwrites_deadline(g))
                self.pages(run)

    def test_the_arm_overwrites_the_deadline_it_found_and_the_put_back_writes_the_deadline_before_the_enable(self):
        for name in ('happy-timer-masked', 'happy-timer-future'):
            run = run_of(name)
            with self.subTest(scenario=name):
                timer = [w for w in run.g.writes() if w[0] in ('CNTHP_CVAL_EL2', 'CNTHP_CTL_EL2')]
                found, cval0 = run.g.state['ctl'][0], run.g.state['cval'][0]
                self.assertEqual(timer[:2], [('CNTHP_CVAL_EL2', run.rec.irq[0].cval), ('CNTHP_CTL_EL2', 1)], 'the arm: the deadline, then the enable')
                self.assertEqual(timer[-3:], [('CNTHP_CTL_EL2', 0), ('CNTHP_CVAL_EL2', cval0), ('CNTHP_CTL_EL2', found)], 'the put-back: off, the deadline, the enable bits')

    def test_a_foreign_deadline_that_passes_during_the_run_asserts_the_line_when_it_is_put_back_and_the_verdict_says_so(self):
        run = run_of('timer-expires')
        r = run.rec
        self.assertEqual((G.OUTCOMES[r.first_outcome], G.OUTCOMES[r.outcome], r.restore_failed), ('IRQ_OK', 'NOT_RESTORED', G.REG_BIT['ISPENDR0']))
        self.assertNotEqual(G.Checks.timer_quiet(run.g), [], 'the one timer state the stage cannot make quiet: its own saved deadline is past by then')
        self.assertEqual((run.g.state['ctl'], run.g.state['cval']), ((1, 1), (19201000, 19201000)), 'the timer itself is as it was found')


# ---- C4: three acknowledged interrupts, each re-armed, and the final deadline without an acknowledge -------------------------------------
class Cycles(Base):
    def test_each_of_the_three_cycles_takes_the_interrupt_acknowledges_it_and_leaves_nothing_pending_after_the_end(self):
        r = run_of('happy').rec
        for k in range(3):
            q = r.irq[k]
            self.assertEqual((q.hppir_vec, q.iar & 0xFFFFFF, q.ctl & 4, q.hppir_post, q.flags), (26, 26, 4, 1023, 0x23), f'cycle {k}: ENTERED, ACKED, OK; ISTATUS set; nothing left')
        self.assertEqual(sum(1 for e in run_of('happy').g.log if e[1] == 'ACK' and e[2] == 26), 4)

    def test_each_later_deadline_is_armed_a_short_time_after_the_previous_entry_and_before_the_end_of_interrupt(self):
        run = run_of('happy')
        r = run.rec
        for k in range(1, 4):
            self.assertEqual(r.irq[k].cval, r.irq[k - 1].t_irq + r.cycle_ticks, f'interrupt {k} is armed from the entry of {k - 1}')
        self.assertEqual(r.cycle_ticks, HZ // 100)
        self.holds(run, G.Checks.eoi(run.g))

    def test_the_fourth_deadline_is_taken_without_an_acknowledge_and_the_timer_disabled_leaves_nothing_pending(self):
        run = run_of('happy')
        q = run.rec.irq[3]
        self.assertEqual((q.flags, q.iar, q.hppir_vec, q.ctl & 4, q.hppir_post), (0x31, 0, 26, 4, 1023), 'ENTERED, FINAL, OK; no IAR read; still pending at the vector; clear after')
        names = [(e[1], e[2]) for e in run.g.log]
        at = [i for i, e in enumerate(run.g.log) if e[1] == 'IRQ_TAKEN'][3]
        window = names[at:names.index(('WINDOW_CLOSE', 0))]
        self.assertNotIn('ICC_IAR1_EL1', [n for n, _ in window], 'the stop path never reads IAR (probes/xnuboot/async.c)')
        self.assertIn(('CNTHP_CTL_EL2', 0), window)
        self.assertEqual([n for n, _ in window if n == 'ICC_EOIR1_EL1'], [])

    def test_the_page_shows_four_latencies_and_the_final_state_and_the_reporter_one_line_per_interrupt(self):
        run = run_of('happy')
        self.assertIn('SNAPMON GIC IRQ INTID=26 LAT_US=0/0/0/0 OK=1111 FINAL=CLEAR', run.mon.texts())
        reporter = [t for t in run.rep.texts() if t.startswith('SNAPREP GIC CYC')]
        self.assertEqual(len(reporter), 5)
        self.assertEqual(reporter[3], 'SNAPREP GIC CYC3 IAR=0 HPPIR=26 POST=1023 F=100 TICKS=0 US=0')
        self.pages(run)

    def test_a_deadline_that_does_not_come_is_cycle_lost_and_the_stage_goes_no_further(self):
        for name, acks in (('cycle-lost', 1), ('final-lost', 3)):
            run = run_of(name)
            with self.subTest(scenario=name):
                self.verdict(run, OUT.CYCLE_LOST)
                self.assertEqual((run.g.state['acks'], run.g.state['excursions'], G.classes(run.rec.begun)), (acks, 0, 'GRCTV'))
                self.holds(run, G.Checks.timer_off(run.g), G.Checks.restored(run.g), Held.bounded(run))
                self.pages(run)

    def test_an_interrupt_whose_hppir_is_not_26_or_whose_istatus_is_clear_is_named(self):
        for name, outcome, field in (('hppir-not-26', OUT.HPPIR_NOT_26, 'hppir_vec'), ('no-istatus', OUT.NO_ISTATUS, 'ctl')):
            run = run_of(name)
            with self.subTest(scenario=name):
                self.verdict(run, outcome)
                self.assertEqual(run.g.state['excursions'], 0, 'the EL1 window is only run once window 1 passed')
                self.holds(run, G.Checks.timer_off(run.g), G.Checks.restored(run.g))
                self.assertEqual(getattr(run.rec.irq[0], field) & (4 if field == 'ctl' else ~0), 27 if name == 'hppir-not-26' else 0)
                self.pages(run)

    def test_a_line_that_stays_high_after_the_end_is_not_clean_and_one_that_clears_with_the_timer_leaves_a_clean_restore(self):
        run = run_of('storm-once')
        self.verdict(run, OUT.NOT_CLEAN)
        self.assertEqual((run.rec.irq[0].hppir_post, run.rec.restore_failed, run.g.state['hang']), (26, 0, 0))
        self.holds(run, G.Checks.restored(run.g), G.Checks.timer_off(run.g))
        stuck = run_of('storm')
        self.refused(stuck, OUT.NOT_RESTORED, 'PREPARED', first=OUT.NOT_CLEAN)
        self.assertEqual((stuck.rec.restore_failed, stuck.g.state['hang']), (G.REG_BIT['ISPENDR0'], 0), 'a level interrupt is pending while its line is high: loud')
        self.pages(stuck)

    def test_a_line_that_stays_high_after_the_final_deadline_is_final_pending_and_not_restored_loud(self):
        run = run_of('final-stuck')
        r = run.rec
        self.refused(run, OUT.NOT_RESTORED, 'PREPARED', first=OUT.FINAL_PENDING)
        self.assertEqual((r.irq[3].hppir_post, bool(r.irq[3].flags & G.IRQF['PEND']), r.restore_failed, run.g.state['excursions']), (26, True, G.REG_BIT['ISPENDR0'], 0))
        self.assertEqual(run.g.state['hang'], 0)
        self.pages(run)

    def test_a_second_interrupt_storm_that_comes_before_its_deadline_is_a_storm(self):
        for name in ('storm-after', 'early'):
            run = run_of(name)
            with self.subTest(scenario=name):
                self.assertEqual(G.OUTCOMES[run.rec.first_outcome], 'STORM')
                self.assertLess(min(q.lat_ticks for q in run.rec.irq if G.entered(q)), -run.rec.slack_ticks)
                self.assertEqual((run.g.state['hang'], run.g.state['excursions']), (0, 0))
                self.pages(run)


# ---- C6: the interrupt taken from EL1 at the lower-EL vector ------------------------------------------------------------------------------
class El1Window(Base):
    def test_the_excursion_enters_el1_with_the_documented_registers_and_the_interrupt_is_taken_at_the_lower_el_vector(self):
        run = run_of('happy')
        r, g = run.rec, run.g
        names = [e[1] for e in g.log]
        self.assertEqual((names.count('EXC_OPEN'), names.count('IRQ_EL1'), names.count('SYNC_EL1'), names.count('VISIBLE')), (1, 1, 0, 1))
        self.assertLess(names.index('VISIBLE'), names.index('EXC_OPEN'))
        before = [(e[1], e[2]) for e in g.log[:names.index('EXC_OPEN')] if e[0] == 'W']
        self.assertEqual(before[-5:], [('VBAR_EL1', G.FAKE_EL1_VECTORS), ('SCTLR_EL1', G.EL1_SCTLR), ('HCR_EL2', G.EL1_HCR), ('CNTHP_CVAL_EL2', r.irq[4].cval),
                                       ('CNTHP_CTL_EL2', 1)])
        q = r.irq[4]
        self.assertEqual((q.iar & 0xFFFFFF, q.hppir_vec, q.ctl & 4, q.hppir_post, q.spsr, q.flags), (26, 26, 4, 1023, 0x345, 0x23), 'EL1h, I clear, ISTATUS set, nothing left')
        self.assertEqual((G.EL1_HCR >> 4 & 1, G.EL1_HCR >> 27 & 1, G.EL1_HCR >> 31), (1, 0, 1), 'IMO and RW set, TGE clear')
        self.assertEqual(g.state['hcr'], (OLD_HCR, OLD_HCR), 'HCR_EL2 is put back')
        self.holds(run, G.Checks.next_before_el1(run.mon, g), G.Checks.restored(g))
        self.pages(run)

    def test_the_resume_state_is_el2h_with_everything_masked_and_keeps_the_pstate_bits_the_caller_had(self):
        for name, kept in (('happy', 0), ('happy-kept', 0x1000)):
            self.assertEqual(run_of(name).g.state['resume'], 0x3C9 | kept, name)
            self.assertEqual(run_of(name).rec.irq[0].spsr, 0x349 | kept)

    def test_a_lost_interrupt_ends_at_the_stubs_counter_bound_with_the_diagnosis_registers_and_a_clean_restore(self):
        run = run_of('el1-lost')
        r, w = run.rec, run.rec.win[1]
        self.verdict(run, OUT.NO_IRQ_FROM_EL1)
        self.assertEqual((w.hvc_end, w.irq_entries, w.other_entries, run.g.state['hang']), (1, 0, 0, 0))
        self.assertEqual((w.dg_ctl & 7, w.dg_hppir1 & 0xFFFFFF, w.dg_ispendr0 >> 26 & 1, w.dg_isenabler0 >> 26 & 1, w.dg_gicd_ctlr & 3, w.dg_rpr & 0xFF,
                          w.dg_pmr & 0xFF, w.dg_hcr, w.dg_spsr), (5, 26, 1, 1, 3, 0xFF, 0xF0, G.EL1_HCR, 0x345))
        self.holds(run, Held.bounded_el1(run), G.Checks.timer_off(run.g), G.Checks.restored(run.g), G.Checks.acks(run.g, 3))
        self.assertEqual(r.restore_failed, 0)
        self.assertTrue(any(t.startswith('SNAPMON GIC DIAG1 CTL=0X5 HPPIR=0X1A PEND=1 ISEN=0X4000000') for t in run.mon.texts()))
        self.pages(run)

    def test_any_other_vector_or_class_in_the_window_ends_it_with_a_named_reason(self):
        for name, vector, esr in (('el1-fiq', 10, 0), ('el1-serror', 11, 0), ('el1-sync-class', 8, 0x90000004), ('el1-hvc2', 8, 0x58000002)):
            run = run_of(name)
            w = run.rec.win[1]
            with self.subTest(scenario=name):
                self.verdict(run, OUT.EL1_EXCEPTION)
                self.assertEqual((w.other_entries, w.irq_entries, w.hvc_end, w.exc_vector, w.exc_esr, w.exc_spsr), (1, 0, 0, vector, esr, 0x345))
                self.holds(run, G.Checks.timer_off(run.g), G.Checks.restored(run.g))
                self.assertEqual(run.g.state['hang'], 0)
                self.pages(run)

    def test_an_interrupt_that_is_not_the_ppi_is_acknowledged_ended_and_named(self):
        run = run_of('el1-intid')
        self.verdict(run, OUT.EL1_WRONG_INTID)
        self.assertEqual((run.rec.irq[4].iar & 0xFFFFFF, [v for n, v in run.g.writes() if n == 'ICC_EOIR1_EL1'][-1]), (27, 27))
        self.holds(run, G.Checks.timer_off(run.g), G.Checks.restored(run.g))
        self.pages(run)

    def test_imo_that_does_not_read_back_set_refuses_the_excursion_before_el1_is_entered_and_the_restore_is_loud(self):
        run = run_of('el1-no-imo')
        self.refused(run, OUT.NOT_RESTORED, 'PREPARED', first=OUT.EL1_NOT_HELD)
        self.assertEqual((run.g.state['excursions'], run.rec.restore_failed), (0, G.REG_BIT['HCR_EL2']))
        self.assertEqual(run.rec.reg('HCR_EL2', 'rb') & 0x10, 0)
        self.pages(run)

    def test_el1_registers_that_do_not_hold_refuse_the_excursion_and_are_put_back(self):
        for name in ('ignore-vbar1', 'ignore-sctlr'):
            run = run_of(name)
            with self.subTest(scenario=name):
                self.refused(run, OUT.EL1_NOT_HELD, 'PREPARED')
                self.assertEqual((run.g.state['excursions'], run.rec.restore_failed, G.classes(run.rec.begun)), (0, 0, 'GRCTVE'))
                self.holds(run, G.Checks.restored(run.g), G.Checks.timer_off(run.g))
                self.pages(run)

    def test_the_excursion_is_refused_before_any_store_when_the_counter_is_trapped_or_the_stub_is_not_mapped(self):
        for name, outcome in (('pct-trapped', OUT.EL1_PCT_TRAPPED), ('el1-image', OUT.EL1_IMAGE)):
            run = run_of(name)
            with self.subTest(scenario=name):
                self.refused(run, outcome, 'COPIED')
                self.assertEqual([e for e in run.g.log if e[0] == 'W'], [])
                self.holds(run)
                self.pages(run)
        self.assertEqual(run_of('pct-trapped').rec.cnthctl_el2, 2)

    def test_the_el1_window_is_never_run_when_window_1_failed(self):
        for name in ('no-fire', 'cycle-lost', 'final-lost', 'hppir-not-26', 'no-istatus', 'storm-once', 'intid27', 'sync'):
            run = run_of(name)
            with self.subTest(scenario=name):
                self.assertEqual((run.g.state['excursions'], run.rec.begun & 32, el2_ok(run.rec)), (0, 0, False))
                self.assertNotIn('NEXT GIC EL1 EXCURSION', ' '.join(run.mon.texts()))

    def test_the_second_next_line_and_eight_seconds_come_before_the_first_el1_write(self):
        run = run_of('happy')
        self.assertEqual(G.Checks.next_before_el1(run.mon, run.g), [])
        lines = run.mon.texts()
        self.assertIn(G.next_el1_line(run.rec).join(['SNAPMON ', '']), lines)
        self.assertLess([t.startswith('SNAPMON NEXT GIC WINDOW') for t in lines].index(True), [t.startswith('SNAPMON NEXT GIC EL1') for t in lines].index(True))


# ---- Every refusal before a store --------------------------------------------------------------------------------------------------------------
REFUSALS = {'el4': OUT.NOT_EL2, 'spsel0': OUT.SPSEL_NOT_1, 'daif-i': OUT.DAIF_NOT_MASKED, 'daif-f': OUT.DAIF_NOT_MASKED, 'imo': OUT.IMO_CLEAR,
            'pin-gicd': OUT.PIN_GIC, 'pin-first': OUT.PIN_GIC, 'pin-limit': OUT.PIN_GIC, 'pin-stride': OUT.PIN_GIC, 'pin-intid': OUT.PIN_GIC,
            'pin-count0': OUT.PIN_GIC, 'ctlr-0': OUT.GICD_VIEW, 'ctlr-bit8': OUT.GICD_VIEW, 'ctlr-bit5': OUT.GICD_VIEW, 'ctlr-rwp': OUT.GICD_VIEW,
            'no-frame': OUT.GICR_NOT_FOUND, 'asleep': OUT.GICR_ASLEEP, 'children-asleep': OUT.GICR_ASLEEP, 'owned-pending': OUT.PPI_OWNED,
            'owned-active': OUT.PPI_OWNED, 'unit-pending': OUT.PPI_OWNED, 'unit-active': OUT.PPI_OWNED, 'sre-clear': OUT.SRE_CLEAR,
            'timer-asserted': OUT.TIMER_OWNED, 'timer-asserted-pending': OUT.PPI_OWNED, 'counter': OUT.COUNTER, 'window': OUT.WINDOW_UNWRITABLE,
            'pct-trapped': OUT.EL1_PCT_TRAPPED, 'el1-image': OUT.EL1_IMAGE}
BEFORE_THE_GIC = {OUT.NOT_EL2, OUT.SPSEL_NOT_1, OUT.DAIF_NOT_MASKED, OUT.IMO_CLEAR, OUT.PIN_GIC, OUT.WINDOW_UNWRITABLE, OUT.EL1_PCT_TRAPPED, OUT.EL1_IMAGE}


class Refusals(Base):
    def each(self, check):
        for name, outcome in REFUSALS.items():
            with self.subTest(scenario=name):
                check(run_of(name), outcome)

    def test_each_refusal_stores_nothing_and_is_named_by_the_return_value_the_head_and_the_page(self):
        def check(run, outcome):
            r, g = run.rec, run.g
            self.refused(run, outcome, 'COPIED')
            self.assertEqual([e for e in g.log if e[0] == 'W'], [], 'the model holds no write at all')
            self.assertEqual((r.begun, r.wrote, r.restore_failed, r.win[0].irq_entries, r.win[0].other_entries, r.done), (0,) * 6)
            self.assertEqual(r.substage, G.SUBSTAGES.index('PRE_OK' if outcome == OUT.COUNTER else 'READS'))
            self.holds(run, G.Checks.restored(g))
            self.pages(run)
        self.each(check)

    def test_a_refusal_reads_no_more_of_the_gic_than_it_needs(self):
        for name, outcome in REFUSALS.items():
            reads = {e[1] for e in run_of(name).g.log if e[0] == 'R'}
            with self.subTest(scenario=name):
                if outcome in BEFORE_THE_GIC:
                    self.assertLessEqual(reads, {'SPSEL', 'DAIF', 'CNTHCTL_EL2', 'HCR_EL2'}, 'a CPU or pin condition is refused before any register of the GIC is read')
                if outcome == OUT.GICD_VIEW:
                    self.assertLessEqual(reads, {'SPSEL', 'DAIF', 'GICD_CTLR', 'CNTHCTL_EL2', 'HCR_EL2', 'VBAR_EL1', 'SCTLR_EL1'})
                if outcome == OUT.SRE_CLEAR:
                    self.assertEqual({n for n in reads if n.startswith('ICC_')}, {'ICC_SRE_EL2'}, 'with SRE clear the other ICC registers are UNDEFINED')

    def test_the_decode_of_the_distributors_view_is_what_the_page_says(self):
        for name, ctlr, ds, are, g1a in (('ctlr-0', 0, 0, 0, 0), ('ctlr-bit8', 0x110, 0, 1, 0), ('ctlr-bit5', 0x20, 0, 0, 0), ('ctlr-rwp', 0x80000010, 0, 1, 0)):
            with self.subTest(scenario=name):
                self.assertEqual(run_of(name).rec.reg('GICD_CTLR', 'old'), ctlr)
                self.assertIn(f'SNAPMON GIC GICD CTLR={G.h(ctlr)} ARE={are} DS={ds} G1A={g1a} NEED_G=1 TYPER=0X7F IIDR=0X1143B', run_of(name).mon.texts())

    def test_the_counter_that_does_not_run_is_refused_after_the_next_line_and_before_the_first_store(self):
        run = run_of('counter')
        self.assertIn('SNAPMON NEXT GIC STORES AFTER 8 S G=1 R=1 C=1 T=1 V=1 E=1', run.mon.texts())
        self.assertEqual(run.g.writes(), [])

    def test_a_bundle_that_withholds_the_gic_class_is_refused_by_the_wrapper_before_the_stage(self):
        run = run_of('not-authorised')
        self.assertEqual(WC.refusal(run.mon, run.w, 'STORE_NOT_AUTH', 'PIN_OK', 7), [])
        self.assertEqual([e for e in run.g.log if e[0] == 'W'], [])
        self.assertEqual(run.rep.texts(), expected_reporter_lines(run.mon.head()) + ['SNAPREP GIC NO RECORD: THE STAGE DID NOT RUN'])


# ---- Windows that end without the interrupt, or with another one --------------------------------------------------------------------------------
WINDOWS = {'no-fire': OUT.NO_IRQ, 'no-deliver': OUT.NO_IRQ, 'sync': OUT.EXCEPTION, 'storm-once': OUT.NOT_CLEAN, 'intid27': OUT.WRONG_INTID,
           'intid1023': OUT.WRONG_INTID}


class Windows(Base):
    def test_each_ends_bounded_restored_and_reported_with_a_clean_return(self):
        for name, outcome in WINDOWS.items():
            run = run_of(name)
            with self.subTest(scenario=name):
                self.assertEqual((run.mon.ret, run.mon.faulted), (0, False))
                self.verdict(run, outcome)
                self.holds(run, G.Checks.timer_off(run.g), G.Checks.restored(run.g), G.Checks.next_before_store(run.mon, run.g))
                self.assertEqual((run.g.state['hang'], run.rec.restore_failed), (0, 0))
                head = run.mon.head()
                self.assertEqual((head['stage'], head['refusal']), (STAGE.DONE, 0))
                self.pages(run)

    def test_a_window_that_sees_nothing_runs_to_its_deadline_and_says_which_link_failed(self):
        for name, istatus in (('no-fire', 0), ('no-deliver', 4)):
            run = run_of(name)
            r, w = run.rec, run.rec.win[0]
            with self.subTest(scenario=name):
                self.assertEqual(Held.bounded(run), [])
                self.assertEqual((w.irq_entries, w.other_entries, r.done), (0, 0, 0))
                self.assertEqual((w.dg_ctl & 7, w.dg_hppir1 & 0xFFFFFF, w.dg_ispendr0 >> 26 & 1, w.dg_isenabler0 >> 26 & 1, w.dg_gicd_ctlr & 3, w.dg_daif,
                                  w.dg_rpr & 0xFF), (1 | istatus, 1023, istatus >> 2, 1, 3, 0x3C0, 0xFF))
                self.assertEqual([e for e in run.g.log if e[1] in ('ACK', 'ICC_EOIR1_EL1')], [], 'the vector never ran')

    def test_an_interrupt_of_another_intid_is_recorded_and_ended_as_acknowledged(self):
        run, spurious = run_of('intid27'), run_of('intid1023')
        self.assertEqual((run.rec.irq[0].iar & 0xFFFFFF, run.rec.win[0].irq_entries), (27, 1))
        self.assertEqual([v for n, v in run.g.writes() if n == 'ICC_EOIR1_EL1'], [27])
        self.holds(run, G.Checks.eoi(run.g))
        self.assertEqual((spurious.rec.irq[0].iar & 0xFFFFFF, spurious.rec.win[0].irq_entries), (1023, 1))
        self.assertEqual([v for n, v in spurious.g.writes() if n == 'ICC_EOIR1_EL1'], [], 'a spurious INTID acknowledges nothing, so there is nothing to end')
        self.assertIn(('CNTHP_CTL_EL2', 0), spurious.g.writes())

    def test_an_exception_in_the_window_is_recorded_and_ends_it_without_a_retry(self):
        run = run_of('sync')
        w = run.rec.win[0]
        self.assertEqual((w.other_entries, w.exc_vector, w.exc_esr, w.exc_elr, w.exc_far, w.irq_entries, run.rec.done), (1, 4, 0x96000004, 0xE850, 0xDEAD0000, 0, 1))
        names = [e[1] for e in run.g.log]
        self.assertLess(names.index('SYNC_TAKEN'), names.index('WINDOW_CLOSE'))
        self.assertEqual([e for e in run.g.log if e[1] == 'ICC_EOIR1_EL1'], [])
        self.assertEqual(run.g.state['deliveries'], 0)


# ---- Stores and put-backs that do not hold -------------------------------------------------------------------------------------------------------
# scenario -> (the record's register, the model's state key, the model's write name, writes of it after the last window: the put-back, its one retry and,
# for the timer, the unconditional disable before them)
NOHOLD = {'nohold-gicd': ('GICD_CTLR', 'gicd_ctlr', 'GICD_CTLR', 2), 'nohold-pmr': ('ICC_PMR', 'pmr', 'ICC_PMR_EL1', 2),
          'nohold-igrpen1': ('ICC_IGRPEN1', 'igrpen1', 'ICC_IGRPEN1_EL1', 2), 'nohold-ctl': ('CNTHP_CTL', 'ctl', 'CNTHP_CTL_EL2', 3),
          'nohold-vbar': ('VBAR_EL2', 'vbar', 'VBAR_EL2', 2), 'nohold-enable': ('ISENABLER0', 'isenabler0', 'GICR_ICENABLER0', 2),
          'nohold-prio': ('IPRIORITYR', 'prio', 'GICR_IPRIORITYR', 2), 'nohold-hcr': ('HCR_EL2', 'hcr', 'HCR_EL2', 2),
          'nohold-sctlr': ('SCTLR_EL1', 'sctlr1', 'SCTLR_EL1', 2), 'nohold-vbar1': ('VBAR_EL1', 'vbar1', 'VBAR_EL1', 2)}
STORE_FAILURES = {'ignore-gicd': (OUT.G_NOT_HELD, 'G', 'COPIED'), 'rwp-gicd': (OUT.G_NOT_HELD, 'G', 'COPIED'), 'ignore-enable': (OUT.R_NOT_HELD, 'GR', 'COPIED'),
                  'ns-ppi': (OUT.R_NOT_HELD, 'GR', 'COPIED'), 'rwp-gicr': (OUT.R_NOT_HELD, 'GR', 'COPIED'), 'ignore-igrpen1': (OUT.C_NOT_HELD, 'GRC', 'COPIED'),
                  'ignore-pmr': (OUT.PRIO_NOT_BELOW_PMR, 'GRC', 'COPIED'), 'ignore-ctl': (OUT.T_NOT_HELD, 'GRCT', 'PREPARED')}


class Failures(Base):
    def test_a_put_back_that_does_not_hold_is_tried_once_more_and_named_not_restored(self):
        for name, (reg, key, wname, count) in NOHOLD.items():
            run = run_of(name)
            r, g = run.rec, run.g
            with self.subTest(scenario=name):
                self.refused(run, OUT.NOT_RESTORED, 'PREPARED', first=OUT.NO_IRQ if name == 'nohold-ctl' else OUT.IRQ_OK)
                self.assertEqual((r.restore_failed, r.restore_retried), (G.REG_BIT[reg], G.REG_BIT[reg]))
                self.assertEqual(len(after_last_window(g, wname)), count, 'the put-back, one retry and no third attempt')
                self.assertEqual([k for k, v in g.state.items() if isinstance(v, tuple) and v[0] != v[1]], [key], 'only that register is left changed')
                self.holds(run, G.Checks.next_before_store(run.mon, g))
                self.assertEqual(g.state['hang'], 0)
                self.pages(run)

    def test_a_store_that_does_not_hold_is_named_and_the_classes_after_it_are_never_begun(self):
        for name, (outcome, begun, stage) in STORE_FAILURES.items():
            run = run_of(name)
            r, g = run.rec, run.g
            with self.subTest(scenario=name):
                self.refused(run, outcome, stage)
                self.assertEqual((G.classes(r.begun), r.restore_failed, r.win[0].irq_entries, r.done), (begun, 0, 0, 0))
                self.assertEqual([e for e in g.log if e[1] == 'WINDOW_OPEN'], [], 'the window is never opened after a failed store')
                self.holds(run, G.Checks.restored(g), G.Checks.next_before_store(run.mon, g))
                self.assertEqual(g.state['ctl'][1] & 1, 0, 'the timer was never left on')
                self.pages(run)

    def test_a_failed_g_store_leaves_one_write_and_a_failed_r_store_the_three_that_were_made_and_their_put_backs(self):
        self.assertEqual(run_of('ignore-gicd').g.writes(), [('GICD_CTLR', 0x13)])
        self.assertEqual(run_of('ns-ppi').g.writes(), [('GICD_CTLR', 0x13), ('GICR_IPRIORITYR', 0x80), ('GICR_ISENABLER0', 1 << 26),
                                                       ('GICR_IPRIORITYR', 0), ('GICD_CTLR', 0x10)])

    def test_prepared_is_written_when_the_arm_held_and_not_before(self):
        for name in ('happy', 'no-fire', 'ignore-ctl', 'nohold-ctl', 'el1-lost'):
            self.assertGreaterEqual(run_of(name).mon.head()['stage'], STAGE.PREPARED, name)
        for name, (outcome, begun, stage) in STORE_FAILURES.items():
            self.assertEqual(run_of(name).mon.head()['stage'], getattr(STAGE, stage), name)


# ---- The line budget, the head and the bounds of every scenario ---------------------------------------------------------------------------------
class Budget(Base):
    def test_every_scenario_stays_within_the_wrappers_line_budget_and_prints_only_panel_text(self):
        for name, run in RUNS.items():
            with self.subTest(scenario=name):
                texts, head = run.mon.texts(), run.mon.head()
                self.assertEqual(Held.page_text(run), [])
                self.assertLessEqual(len(texts), sb.WRAPPER_LINES)
                if head['stage'] == STAGE.DONE:
                    self.assertEqual((head['lines_printed'], texts[-1]), (len(texts), 'SNAPMON MONITOR NOT CALLED: UNIMPLEMENTED IN THIS BUILD'))

    def test_the_longest_run_prints_29_lines_of_the_30_allowed_and_the_reporter_at_most_the_lines_it_is_given(self):
        self.assertEqual(max(len(run.mon.texts()) for run in RUNS.values()), 29, 'happy: 19 of the dry run, with its closing line kept, and 10 of the stage')
        self.assertLessEqual(max(len(run.rep.texts()) for run in RUNS.values() if run.rep), sb.REPORTER_LINES)

    def test_every_head_passes_the_interfaces_check_and_the_stage_adds_nothing_to_page_0(self):
        for name, run in RUNS.items():
            with self.subTest(scenario=name):
                self.assertEqual(Held.report(run), [])
                head = run.mon.head()
                for field in ('monitor_out', 'monitor_ret', 'monitor_status', 'monitor_entered', 'ticks_launch', 'ticks_return'):
                    self.assertEqual(head[field], 0, f'{field} belongs to the monitor entry, which this build does not have')

    def test_no_scenario_hangs_or_makes_the_model_refuse_an_access(self):
        for name, run in RUNS.items():
            with self.subTest(scenario=name):
                self.assertEqual((run.g.state['hang'], run.g.nviol, run.g.viol), (0, 0, []))
                self.assertEqual((run.mon.faulted, run.rep.faulted), (False, False))

    def test_every_outcome_has_a_scenario(self):
        seen = {o for run in RUNS.values() if any(run.g.rec[:8]) for o in (run.rec.outcome, run.rec.first_outcome)}
        self.assertEqual(seen, set(G.OUTCOMES) - {OUT.NONE}, 'every outcome of the document is reached by at least one scenario')


# ---- Seeded-bad stages: each must fail the check that matches its fault -------------------------------------------------------------------------
def verdict_is(outcome):
    return lambda run: [] if run.rec.outcome == outcome else [f'the verdict is {G.OUTCOMES[run.rec.outcome]}, not {G.OUTCOMES[outcome]}']


class Seeded(Base):
    DETECTORS = {
        'no-save': ('happy', lambda run: G.Checks.saved(run.g, {'GICD_CTLR': 0x10}) + G.Checks.restored(run.g)),
        'stray-store': ('happy', lambda run: G.Checks.stray_writes(run.g)),
        'no-eoi': ('happy', lambda run: G.Checks.eoi(run.g) + G.Checks.restored(run.g)),
        'timer-on': ('happy', lambda run: verdict_is(OUT.IRQ_OK)(run) + G.Checks.eoi(run.g)),
        'no-irq-mask': ('final-stuck', lambda run: G.Checks.one_interrupt(run.g)),
        'store-first': ('happy', lambda run: G.Checks.next_before_store(run.mon, run.g)),
        'no-next': ('happy', lambda run: G.Checks.next_before_store(run.mon, run.g)),
        'no-restore': ('happy', lambda run: G.Checks.restored(run.g)),
        'final-ack': ('happy', lambda run: G.Checks.acks(run.g, 4)),
        'no-rearm': ('happy', lambda run: G.Checks.eoi(run.g)),
        'no-imo': ('happy', verdict_is(OUT.IRQ_OK)),
        'no-el1-restore': ('happy', lambda run: G.Checks.restored(run.g)),
        'no-next-el1': ('happy', lambda run: G.Checks.next_before_el1(run.mon, run.g)),
        'unbounded-el1': ('el1-lost', lambda run: G.Checks.one_interrupt(run.g)),
        'no-settle': ('happy', lambda run: G.Checks.settled(run.g)),
        'keep-foreign': ('happy-timer-future', lambda run: G.Checks.arm_overwrites_deadline(run.g)),
        'restore-ctl-first': ('happy-timer-future', lambda run: G.Checks.timer_quiet(run.g)),
    }

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.seeded = {seed: batch({n: CASES[n] for n in ('happy', 'final-stuck', 'el1-lost', 'happy-timer-future')}, variant=seed)
                      for seed in SEEDS if seed not in QUARANTINE_SEEDS}

    def test_there_is_a_seeded_stage_for_every_fault_the_brief_names_and_every_one_is_tested(self):
        self.assertEqual(set(self.DETECTORS) | set(QUARANTINE_SEEDS), set(SEEDS), 'the quarantine\'s are tools/test-sp11-stick-gic-quarantine.py\'s')
        self.assertFalse(set(self.DETECTORS) & set(QUARANTINE_SEEDS))
        self.assertEqual(len(SEEDS), 23)

    def test_each_seeded_fault_breaks_the_check_that_matches_it_and_the_real_stage_passes_that_check(self):
        for seed, (scenario, check) in self.DETECTORS.items():
            with self.subTest(seed=seed):
                self.assertEqual(check(run_of(scenario)), [], 'the real stage must pass the check')
                self.assertNotEqual(check(self.seeded[seed][scenario]), [], f'the {seed} stage passed the check that is meant to catch it')

    def test_the_seeded_stages_are_the_real_stage_but_for_their_fault(self):
        for seed in ('stray-store', 'no-restore', 'timer-on', 'no-el1-restore'):
            with self.subTest(seed=seed):
                run = self.seeded[seed]['happy']
                self.assertEqual((run.mon.ret, run.mon.faulted), (0, False))
                self.assertEqual(run.rec.intid, 26)
                self.assertEqual(Held.page_text(run), [])
        run = self.seeded['no-eoi']['happy']
        self.assertEqual((G.OUTCOMES[run.rec.outcome], run.rec.restore_failed), ('NOT_RESTORED', G.REG_BIT['ISACTIVER0'] | G.REG_BIT['ICC_RPR']),
                         'the stage itself sees the interrupt left active, and the running priority that it holds')
        run = self.seeded['no-imo']['happy']
        self.assertEqual((G.OUTCOMES[run.rec.outcome], run.g.state['excursions'], run.rec.win[1].exc_vector), ('EL1_EXCEPTION', 1, 0x105),
                         'with IMO clear the interrupt is taken by EL1 own table: named, not taken for the stop')
        run = self.seeded['unbounded-el1']['el1-lost']
        self.assertEqual(run.g.state['hang'], 1, 'a stub without its counter bound would hold the stage at EL1')
        run = self.seeded['keep-foreign']['happy-timer-future']
        self.assertEqual((G.OUTCOMES[run.rec.outcome], run.rec.restore_failed, run.g.state['deliveries']), ('T_NOT_HELD', 0, 0),
                         'the stage itself sees that the deadline it armed is not the one it wrote, and no window is run')
        run = self.seeded['restore-ctl-first']['happy-timer-future']
        self.assertEqual((G.OUTCOMES[run.rec.outcome], run.rec.restore_failed, G.Checks.restored(run.g)), ('IRQ_OK', 0, []),
                         'the stage cannot see the order of its put-back: only the replay of the writes can, and the end state is the same')


if __name__ == '__main__':
    unittest.main()
