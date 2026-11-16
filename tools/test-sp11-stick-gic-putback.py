#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests for the put-back of the SP11 GIC and stop stage after the third independent review (docs/SP11-STICK-GIC.md sections 4, 9, 10 and 12), monitor port
work item 8, pass 6: the last write of the put-back is not made when both group 1 gates failed to go back (and is made in every other case), the RWP waits
after the quarantine's two writes, a quarantine that holds in part, and a put-back that fails after a refusal (outcomes 22 and 23 and the stores that did not
hold), which the model could not do before: its NOHOLD only bit after a window. Each check is shown to fail on MUTANTS of the real stage, text patches made
in memory in a copy of the source and compiled in the scratch directory (tools/sp11_stick_gic_testlib.py, harness), which the tests below run next to the
real stage; a patch that no longer matches the source exactly once fails loudly.

    python3 tools/test-sp11-stick-gic-putback.py
    SP11_GIC_MUTANT=<name> python3 tools/test-sp11-stick-gic-putback.py      (every scenario runs on that mutant: the tests that fail are the ones that kill it)
    Q1N1_SP11_TREE=/nonexistent python3 tools/test-sp11-stick-gic-putback.py      (the tests that need q1n1's headers and sha256.c SKIP)

The code under test is the REAL wrapper, stage and reporter over the model of the machine of tools/test-sp11-stick-gic.c; what the model proves is the stage's
order, bounds, put-back and report against the model, and nothing about the unit's GIC. The expected values are written from the document and from the
reviewer's findings, not from the C. No hardware, nothing under /Volumes, no network. Everything is written below build/sp11-wrapper-test-scratch-gic-putback/,
deleted at the end. A SKIP is never a PASS. EXPERIMENTAL; hardware NOT RUN."""
import concurrent.futures
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_gic_checks as C  # noqa: E402
import sp11_stick_gic_testlib as G  # noqa: E402
from sp11_stick_gic_checks import CASES, OUT, RUNS, UNIT13, Base, after_last_window, batch  # noqa: E402

FOUND, QUAR = 0x4E800001, 0x4A800001        # ISENABLER0 as boot 12 and 13 read it, and the bits the quarantine clears (everything but PPI 26)
PPI26 = 1 << 26
GICD, IGRPEN1, ISEN = G.REG_BIT['GICD_CTLR'], G.REG_BIT['ICC_IGRPEN1'], G.REG_BIT['ISENABLER0']
BOTH = ['NOHOLD GICD_CTLR', 'NOHOLD ICC_IGRPEN1_EL1']                 # the two group 1 gates do not go back after window 1
BOTH_0 = ['NOHOLD GICD_CTLR 0', 'NOHOLD ICC_IGRPEN1_EL1 0']           # the same, from the start of the put-back: also for a run in which no window ran
SPI40 = ['SPI_EN 1 0x100', 'SPI_PEND 1 0x100']                        # INTID 40 enabled and pending: the case the quarantine does not cover

NEW_CASES = {
    'gates-both': dict(gic=[*UNIT13, *BOTH]),
    'gates-igrpen1': dict(gic=[*UNIT13, 'NOHOLD ICC_IGRPEN1_EL1']),
    'gates-gicd': dict(gic=[*UNIT13, 'NOHOLD GICD_CTLR']),
    'gates-found-open': dict(gic=[*UNIT13, 'CTLR 0x12', 'NOHOLD ICC_IGRPEN1_EL1']),      # EnableGrp1A found set (the stage never writes GICD_CTLR) and the CPU interface's gate stuck
    'rwp-stuck': dict(gic=[*UNIT13, 'RWP gicr']),
    'rwp-lag': dict(gic=[*UNIT13, 'RWP_LAG gicr 4']),
    'partial-hold': dict(gic=[*UNIT13, 'ICE_HOLD 0x00800001']),
    'partial-hold-putback-fails': dict(gic=[*UNIT13, 'ICE_HOLD 0x00800001', 'NOHOLD GICR_ISENABLER0 0']),
    'foreign-isen-stuck': dict(gic=[*UNIT13, *SPI40, 'NOHOLD GICR_ISENABLER0 0']),
    'foreign-igrpen1-stuck': dict(gic=[*UNIT13, *SPI40, 'NOHOLD ICC_IGRPEN1_EL1 0']),
    'foreign-gates-both-stuck': dict(gic=[*UNIT13, *SPI40, *BOTH_0]),
    'c-not-held-gicd-stuck': dict(gic=[*UNIT13, 'IGNORE ICC_IGRPEN1_EL1', 'NOHOLD GICD_CTLR 0']),
}
NEEDED = ('unit-boot13', 'foreign-spi')

GIC_C, DRY_C = 'probes/sp11-stick-gic.c', 'probes/sp11-stick-gic-dry.c'
_Q_RETURN = 'return rwp && s->quar_rb == ((uint32_t)r->reg[SG_R_ISENABLER0].old & g->bit) ? 0 : SG_OUT_Q_NOT_HELD;'
_FINAL_WRITE = 'if (r->begun & SG_CLASS_R) gic_put_back_bits(g, SG_R_ISENABLER0, gic_mask(g, SG_R_ISENABLER0), 1, !gic_both_gates_open(r));'
# name -> (patches, the scenario that must show it, the check that must fail on it)
PATCHES = {
    'q-without-rwp': ((GIC_C, _Q_RETURN, _Q_RETURN.replace('return rwp &&', 'return (void)rwp,')), 'rwp-stuck'),
    'q-without-rwp-wait': ((GIC_C, 'rwp = gic_wait_clear32(g, g->rd + SG_GICR_CTLR, SG_GICR_CTLR_RWP);\n    s->quar_rb', 'rwp = 1;\n    s->quar_rb'), 'rwp-lag'),
    'q-weak-readback': ((GIC_C, _Q_RETURN, _Q_RETURN.replace('== ((uint32_t)r->reg[SG_R_ISENABLER0].old & g->bit)', '!= (uint32_t)r->reg[SG_R_ISENABLER0].old')), 'partial-hold'),
    'final-without-rwp-wait': ((GIC_C, 'sp11hw_mmio_write32(g->sgi + SG_ISENABLER0, back);\n            sw_gic_sync();\n            (void)gic_wait_clear32(g, g->rd + SG_GICR_CTLR, SG_GICR_CTLR_RWP);',
                                'sp11hw_mmio_write32(g->sgi + SG_ISENABLER0, back);\n            sw_gic_sync();'), 'rwp-lag'),
    'gates-rule-removed': ((GIC_C, '!gic_both_gates_open(r)', '((void)gic_both_gates_open(r), 1)'), 'gates-both'),
    'gates-either': ((GIC_C, 'return (r->restore_failed & gates) == gates;', 'return (r->restore_failed & gates) != 0;'), 'gates-igrpen1'),
    'gates-not-reported': ((GIC_C, _FINAL_WRITE, 'if ((r->begun & SG_CLASS_R) && !gic_both_gates_open(r)) sw_gic_put_back_reg(g, SG_R_ISENABLER0);'), 'gates-both'),
    'refusal-hides-not-restored': ((DRY_C, 'uint32_t out = put ? SG_OUT_NOT_RESTORED : first;', 'uint32_t out = put && SG_OUT_IS_VERDICT(first) ? SG_OUT_NOT_RESTORED : first;'),
                                   'foreign-isen-stuck'),
}


def setUpModule():
    C.set_up('gic-putback', cases=NEEDED, variants=())
    mutant = os.environ.get('SP11_GIC_MUTANT')
    if mutant:                              # the mutation run: every scenario on that mutant; the tests that fail are the ones that kill it
        RUNS.update(batch({**{n: CASES[n] for n in NEEDED}, **NEW_CASES}, variant=mutant, patches=(PATCHES[mutant][0],)))
    else:
        RUNS.update(batch(NEW_CASES))


def tearDownModule():
    C.tear_down()


def run_of(name):
    return RUNS[name]


def writes_of(run, register):
    return [v for n, v in run.g.writes() if n == register]


# ---- What each check holds a run to, written as functions of a run so that a mutant can be shown to break it -----------------------------------------
def q_stops(run):
    """(a) The redistributor's RWP never clears after the quarantine's write: the stage does not know the write completed, so it stops at Q before group 1 is
    enabled (its read-back is not enough), and the put-back enables the cleared bits again."""
    r, g, found = run.rec, run.g, []
    if (r.outcome, r.first_outcome) != (OUT.Q_NOT_HELD, OUT.Q_NOT_HELD):
        found.append(f'the verdict is {G.OUTCOMES[r.outcome]} (first {G.OUTCOMES[r.first_outcome]}), not Q_NOT_HELD')
    if g.writes() != [('GICR_ICENABLER0', QUAR), ('GICR_ISENABLER0', QUAR)]:
        found.append(f'the writes are {[(n, hex(v)) for n, v in g.writes()]}: after a quarantine that did not complete nothing but its put-back may be written')
    return found


def waits_for_rwp(run):
    """(b) With every write of an enable register pending until RWP clears, the quarantine and its put-back wait for it: the run is clean, with each enable
    register written once and no retry, as with no lag at all."""
    r, g, found = run.rec, run.g, []
    if (r.outcome, r.restore_failed, r.restore_retried) != (OUT.IRQ_OK, 0, 0):
        found.append(f'{G.OUTCOMES[r.outcome]}, restore_failed {r.restore_failed:#x}, restore_retried {r.restore_retried:#x}')
    def stores(x):                      # the deadlines written to CNTHP_CVAL_EL2 are the counter's, which the polls of RWP advance: not part of the comparison
        return [(n, v) for n, v in x.g.writes() if n != 'CNTHP_CVAL_EL2']
    if stores(run) != stores(run_of('unit-boot13')):
        found.append('the writes differ from the run with no lag')
    return found


def gates_both_stuck(run):
    """(c) Both group 1 gates stay open: ISENABLER0 is NOT written, is read and is named in restore_failed, and the run ends NOT_RESTORED."""
    r, g, found = run.rec, run.g, []
    if writes_of(run, 'GICR_ISENABLER0'):
        found.append(f'ISENABLER0 was written with {[hex(v) for v in writes_of(run, "GICR_ISENABLER0")]} behind two open gates')
    if (r.outcome, r.first_outcome, r.restore_failed) != (OUT.NOT_RESTORED, OUT.IRQ_OK, GICD | IGRPEN1 | ISEN):
        found.append(f'{G.OUTCOMES[r.outcome]} (first {G.OUTCOMES[r.first_outcome]}), restore_failed {r.restore_failed:#x}, expected {GICD | IGRPEN1 | ISEN:#x}')
    if r.reg('ISENABLER0', 'fin') != PPI26 or g.state['isenabler0'] != (FOUND, PPI26):
        found.append(f'ISENABLER0 ends {g.state["isenabler0"][1]:#x} with fin {r.reg("ISENABLER0", "fin"):#x}, expected {PPI26:#x} for both')
    return found


def one_gate_stuck(run, gate):
    """(c) One gate open and the other closed: the write stays, as the last write of the run, and only that gate is named."""
    r, g, found = run.rec, run.g, []
    if g.writes()[-1:] != [('GICR_ISENABLER0', QUAR)]:
        found.append(f'the last write is {g.writes()[-1:]}, not ISENABLER0 {QUAR:#x}')
    if (r.outcome, r.restore_failed, g.state['isenabler0']) != (OUT.NOT_RESTORED, gate, (FOUND, FOUND)):
        found.append(f'{G.OUTCOMES[r.outcome]}, restore_failed {r.restore_failed:#x} (only {gate:#x}), ISENABLER0 {g.state["isenabler0"]}')
    return found


def partial_is_not_held(run):
    """A quarantine that clears only some of its bits did not hold: Q_NOT_HELD with the register as read back, nothing stored after it, and the put-back
    enables again exactly the bits that went clear."""
    r, g, found = run.rec, run.g, []
    if (r.outcome, r.sv.quar_rb) != (OUT.Q_NOT_HELD, 0x4E000000):
        found.append(f'{G.OUTCOMES[r.outcome]} with the read-back {r.sv.quar_rb:#x}, expected Q_NOT_HELD and 0x4E000000')
    if g.writes() != [('GICR_ICENABLER0', QUAR), ('GICR_ISENABLER0', 0x800001)]:
        found.append(f'the writes are {[(n, hex(v)) for n, v in g.writes()]}')
    return found


def refusal_put_back_is_named(run):
    """The put-back of a refused arm does not hold: the verdict is NOT_RESTORED, with the refusal as the first outcome."""
    r = run.rec
    return [] if (r.outcome, r.first_outcome) == (OUT.NOT_RESTORED, OUT.FOREIGN_PENDING) else [f'{G.OUTCOMES[r.outcome]} (first {G.OUTCOMES[r.first_outcome]})']


DETECTORS = {
    'rwp-stuck': q_stops, 'rwp-lag': waits_for_rwp, 'gates-both': gates_both_stuck, 'gates-igrpen1': lambda run: one_gate_stuck(run, IGRPEN1),
    'partial-hold': partial_is_not_held, 'foreign-isen-stuck': refusal_put_back_is_named,
}


# ---- P6-1: the last write of the put-back and the two group 1 gates --------------------------------------------------------------------------------
class Gates(Base):
    def test_with_both_gates_left_open_the_last_write_is_not_made_isenabler0_is_named_and_the_run_ends_not_restored(self):
        run = run_of('gates-both')
        r, g = run.rec, run.g
        self.assertEqual(gates_both_stuck(run), [])
        self.refused(run, OUT.NOT_RESTORED, 'PREPARED', first=OUT.IRQ_OK)
        self.assertEqual((r.restore_retried, g.state['igrpen1'], g.state['gicd_ctlr']), (GICD | IGRPEN1, (0, 1), (0x10, 0x12)), 'both gates are left as the stage set them')
        self.assertEqual([n for n, _ in g.writes() if n in ('GICR_ICENABLER0', 'GICR_ISENABLER0')], ['GICR_ICENABLER0'], 'the quarantine\'s write is the only write of either register')
        self.assertEqual(len(after_last_window(g, 'ICC_IGRPEN1_EL1')), 2, 'each gate was put back and retried once, and not a third time')
        self.assertEqual(len(after_last_window(g, 'GICD_CTLR')), 2)
        self.assertEqual(g.writes()[-1], ('GICD_CTLR', 0x10), 'the last write of the run is the distributor\'s retry')
        self.assertIn('SNAPMON GIC RESTORE CLEAN=NO FAILED=0X205 RETRIED=0X201 BEGUN=GRCTVE VBAR=OK EL1=OK', run.mon.texts())
        self.assertTrue(any(t.startswith('SNAPREP GIC FINAL GICD=0X12 ISEN=0X4000000 ') and ' IGRPEN1=1 ' in t for t in run.rep.texts()), run.rep.texts())
        self.holds(run)
        self.pages(run)

    def test_with_one_gate_left_open_the_write_is_made_as_the_last_write_of_the_run_whichever_gate_it_is(self):
        for name, gate in (('gates-igrpen1', IGRPEN1), ('gates-gicd', GICD)):
            run = run_of(name)
            with self.subTest(scenario=name):
                self.assertEqual(one_gate_stuck(run, gate), [])
                self.refused(run, OUT.NOT_RESTORED, 'PREPARED', first=OUT.IRQ_OK)
                self.assertEqual(run.rec.restore_retried, gate)
                self.assertEqual((run.rec.reg('ISENABLER0', 'fin'), writes_of(run, 'GICR_ISENABLER0')), (FOUND, [QUAR]), 'ISENABLER0 is back whole, by one write')
                self.holds(run)
                self.pages(run)

    def test_a_gate_found_open_is_not_a_failed_put_back_so_with_the_other_gate_stuck_the_write_is_made_which_is_the_limit_the_document_states(self):
        run = run_of('gates-found-open')
        self.assertEqual(one_gate_stuck(run, IGRPEN1), [])
        self.assertNotIn('G', G.classes(run.rec.begun), 'EnableGrp1A was found set: class G was never begun and GICD_CTLR never written')
        self.assertEqual((writes_of(run, 'GICD_CTLR'), run.g.state['gicd_ctlr']), ([], (0x12, 0x12)))
        self.refused(run, OUT.NOT_RESTORED, 'PREPARED', first=OUT.IRQ_OK)
        self.holds(run)
        self.pages(run)

    def test_the_gate_rule_holds_in_a_refused_run_too_and_a_gate_that_never_opened_is_no_gate_left_open(self):
        run = run_of('foreign-gates-both-stuck')
        r, g = run.rec, run.g
        self.refused(run, OUT.NOT_RESTORED, 'COPIED', first=OUT.FOREIGN_PENDING)
        self.assertEqual((r.restore_failed, writes_of(run, 'GICR_ISENABLER0'), g.state['isenabler0']), (GICD | IGRPEN1 | ISEN, [], (FOUND, PPI26)))
        self.assertEqual([e for e in g.log if e[1] == 'WINDOW_OPEN'], [], 'no window opened')
        self.holds(run)
        self.pages(run)
        run = run_of('foreign-igrpen1-stuck')
        self.refused(run, OUT.NOT_RESTORED, 'COPIED', first=OUT.FOREIGN_PENDING)
        self.assertEqual((run.rec.restore_failed, writes_of(run, 'GICR_ISENABLER0'), run.g.state['isenabler0']), (IGRPEN1, [QUAR], (FOUND, FOUND)))
        self.pages(run)
        run = run_of('c-not-held-gicd-stuck')       # the CPU interface's enable never held (so it never opened) and the distributor's did not go back: one gate open
        self.refused(run, OUT.NOT_RESTORED, 'COPIED', first=OUT.C_NOT_HELD)
        self.assertEqual((run.rec.restore_failed, writes_of(run, 'GICR_ISENABLER0'), run.g.state['isenabler0']), (GICD, [QUAR], (FOUND, FOUND)))
        self.pages(run)


# ---- P6-2 (a), (b): the RWP waits of the quarantine and of its put-back; a partial hold; a put-back that fails after a refusal ----------------------------
class Quarantine(Base):
    def test_with_rwp_stuck_after_the_quarantines_write_the_stage_stops_at_q_before_group_1_is_enabled(self):
        run = run_of('rwp-stuck')
        r, g = run.rec, run.g
        self.assertEqual(q_stops(run), [])
        self.refused(run, OUT.Q_NOT_HELD, 'COPIED')
        self.assertEqual((G.classes(r.begun), r.restore_failed, r.sv.quar_rb), ('R', 0, PPI26), 'the write took effect: the read-back alone would have said it held')
        self.assertEqual([e for e in g.log if e[1] == 'WINDOW_OPEN'], [])
        self.assertIn('SNAPMON GIC QUAR MASK=0X4A800001 DONE=0X4000000 HPPIR=-', run.mon.texts())
        self.assertEqual(g.state['isenabler0'], (FOUND, FOUND))
        self.holds(run)
        self.pages(run)

    def test_with_every_write_of_the_enable_registers_pending_until_rwp_clears_the_run_is_the_run_with_no_lag(self):
        run = run_of('rwp-lag')
        self.assertEqual(waits_for_rwp(run), [])
        self.verdict(run, OUT.IRQ_OK)
        self.assertEqual((writes_of(run, 'GICR_ICENABLER0'), writes_of(run, 'GICR_ISENABLER0')), ([QUAR], [QUAR]), 'each register written once: nothing was retried')
        self.assertEqual(run.g.state['isenabler0'], (FOUND, FOUND))
        self.holds(run, G.Checks.quarantine(run.g, FOUND), G.Checks.quarantine_first(run.g, FOUND), G.Checks.quarantine_last(run.g, FOUND), G.Checks.restored(run.g))
        self.pages(run)

    def test_a_quarantine_that_clears_only_some_of_its_bits_did_not_hold_and_the_put_back_enables_again_exactly_those(self):
        run = run_of('partial-hold')
        r, g = run.rec, run.g
        self.assertEqual(partial_is_not_held(run), [])
        self.refused(run, OUT.Q_NOT_HELD, 'COPIED')
        self.assertEqual((G.classes(r.begun), r.restore_failed, r.restore_retried, g.state['isenabler0']), ('R', 0, 0, (FOUND, FOUND)))
        self.assertIn('SNAPMON GIC QUAR MASK=0X4A800001 DONE=0X4E000000 HPPIR=-', run.mon.texts())
        self.holds(run, G.Checks.restored(g))
        self.pages(run)

    def test_a_put_back_that_fails_after_a_refusal_is_tried_once_more_and_named_not_restored_with_the_refusal_as_the_first_outcome(self):
        for name, first, failed, stage in (('foreign-isen-stuck', OUT.FOREIGN_PENDING, ISEN, 'COPIED'), ('partial-hold-putback-fails', OUT.Q_NOT_HELD, ISEN, 'COPIED')):
            run = run_of(name)
            r, g = run.rec, run.g
            with self.subTest(scenario=name):
                self.refused(run, OUT.NOT_RESTORED, stage, first=first)
                self.assertEqual((r.restore_failed, r.restore_retried), (failed, failed))
                self.assertEqual(len(writes_of(run, 'GICR_ISENABLER0')), 2, 'the put-back, one retry and no third attempt')
                self.assertEqual((r.reg('ISENABLER0', 'fin'), g.state['hang'], g.nviol), (g.state['isenabler0'][1], 0, 0))
                self.assertNotEqual(g.state['isenabler0'][0], g.state['isenabler0'][1], 'what is left is not what was found, and the page says so')
                self.assertIn(f'CLEAN=NO FAILED={G.h(failed)} RETRIED={G.h(failed)}', ' '.join(run.mon.texts()))
                self.holds(run)
                self.pages(run)


# ---- Each check fails on a mutant of the real stage ------------------------------------------------------------------------------------------------
class Mutants(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if os.environ.get('SP11_GIC_MUTANT'):
            raise unittest.SkipTest('a mutation run: the scenarios already run on the mutant')
        with concurrent.futures.ThreadPoolExecutor(3) as pool:
            list(pool.map(lambda name: C.exe_of(name, (PATCHES[name][0],)), PATCHES))
        cls.runs = {name: batch({PATCHES[name][1]: NEW_CASES[PATCHES[name][1]]}, variant=name, patches=(PATCHES[name][0],))[PATCHES[name][1]] for name in PATCHES}

    def test_there_is_a_mutant_for_every_fault_the_review_named_and_every_scenario_it_uses_exists(self):
        self.assertEqual(set(PATCHES), {'q-without-rwp', 'q-without-rwp-wait', 'q-weak-readback', 'final-without-rwp-wait', 'gates-rule-removed', 'gates-either',
                                        'gates-not-reported', 'refusal-hides-not-restored'})
        for name, (patch, scenario) in PATCHES.items():
            self.assertIn(scenario, DETECTORS, name)
            self.assertIn(scenario, NEW_CASES, name)

    def test_each_mutant_breaks_the_check_that_matches_it_and_the_real_stage_passes_that_check(self):
        for name, (patch, scenario) in PATCHES.items():
            with self.subTest(mutant=name, scenario=scenario):
                self.assertEqual(DETECTORS[scenario](run_of(scenario)), [], 'the real stage must pass the check')
                self.assertNotEqual(DETECTORS[scenario](self.runs[name]), [], f'the mutant {name} passed the check that is meant to catch it')

    def test_the_mutants_are_the_real_stage_but_for_their_fault(self):
        r = self.runs['q-without-rwp'].rec
        self.assertEqual((G.OUTCOMES[r.outcome], G.classes(r.begun)), ('R_NOT_HELD', 'GR'), 'without the RWP condition the stage stores G and R before it notices')
        self.assertIn(('GICD_CTLR', 0x13), self.runs['q-without-rwp'].g.writes(), 'group 1 was enabled although the quarantine had not completed')
        self.assertEqual(G.OUTCOMES[self.runs['q-weak-readback'].rec.outcome], 'R_NOT_HELD', 'a partial hold goes unseen at Q and is caught by R, after group 1 is enabled')
        self.assertIn(G.OUTCOMES[self.runs['q-without-rwp-wait'].rec.outcome], ('Q_NOT_HELD',), 'without the wait the read-back is stale')
        run = self.runs['final-without-rwp-wait']
        self.assertEqual((run.rec.restore_retried & ISEN, writes_of(run, 'GICR_ISENABLER0')), (ISEN, [QUAR, QUAR]), 'the stale read-back sends the stage to a retry that the wait makes needless')
        run = self.runs['gates-rule-removed']
        self.assertEqual(writes_of(run, 'GICR_ISENABLER0'), [QUAR], 'the unconditional write re-arms the pending interrupt behind two open gates')
        run = self.runs['gates-either']
        self.assertEqual(writes_of(run, 'GICR_ISENABLER0'), [], 'widened to either gate the rule suppresses a write that was inert')
        run = self.runs['gates-not-reported']
        self.assertEqual((writes_of(run, 'GICR_ISENABLER0'), run.rec.restore_failed & ISEN), ([], 0), 'suppressed and not named: the page would not say it')
        run = self.runs['refusal-hides-not-restored']
        self.assertEqual((G.OUTCOMES[run.rec.outcome], run.rec.restore_failed), ('FOREIGN_PENDING', ISEN), 'the failed put-back is in the record and not in the verdict')


if __name__ == '__main__':
    unittest.main()
