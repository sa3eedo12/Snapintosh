#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests for the check of ICC_HPPIR1_EL1 before the window of the SP11 GIC and stop stage after the third independent review (docs/SP11-STICK-GIC.md sections
4, 10 and 12), monitor port work item 8, pass 6. Whether ICC_HPPIR1_EL1 applies PMR is UNKNOWN on the unit (INFERRED from the model's guess until now), so the
model has both: by default it does (a pending and enabled interrupt whose priority is not below PMR reads 1023), and with HPPIR_IGNORES_PMR it does not. The
stage's decision rule is NOT changed: 1023 or PPI 26 passes, anything else is FOREIGN_PENDING. What the tests pin is what that rule does in each variant: an SPI
at or below PMR passes when HPPIR honours PMR (the over-acceptance case) and is refused when it does not (over-refusal); the special INTIDs 1020, 1021 and 1022
and an LPI's INTID (8192 and up, and the ones that alias PPI 26 or 1023 in their low bits) are refused in both; and the check is ONE read at ONE instant, so a
source that asserts after it ends the first window (WRONG_INTID), as INTID 27 did in boot 13. Each check is shown to fail on mutants of the real stage, text
patches of one source compiled in the scratch directory (tools/sp11_stick_gic_testlib.py, harness).

    python3 tools/test-sp11-stick-gic-hppir.py
    SP11_GIC_MUTANT=<name> python3 tools/test-sp11-stick-gic-hppir.py      (every scenario runs on that mutant: the tests that fail are the ones that kill it)
    Q1N1_SP11_TREE=/nonexistent python3 tools/test-sp11-stick-gic-hppir.py      (the tests that need q1n1's headers and sha256.c SKIP)

The code under test is the REAL wrapper, stage and reporter over the model of the machine of tools/test-sp11-stick-gic.c; the model proves the stage's decision
against the model and says nothing about what the unit's GIC returns. No hardware, nothing under /Volumes, no network. Everything is written below
build/sp11-wrapper-test-scratch-gic-hppir/, deleted at the end. A SKIP is never a PASS. EXPERIMENTAL; hardware NOT RUN."""
import concurrent.futures
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_gic_checks as C  # noqa: E402
import sp11_stick_gic_testlib as G  # noqa: E402
from sp11_stick_gic_checks import OUT, RUNS, UNIT13, Base, batch  # noqa: E402

SPI40 = ['SPI_EN 1 0x100', 'SPI_PEND 1 0x100']          # INTID 40, enabled and pending: PMR on the unit reads 0xF8 (boot 13)
IGNORES = 'HPPIR_IGNORES_PMR'
FP, OK = OUT.FOREIGN_PENDING, OUT.IRQ_OK
# name -> (model lines, (outcome, ICC_HPPIR1_EL1 read by the check) when HPPIR honours PMR, the same when it does not)
ROWS = {
    'spi-at-pmr': ([*SPI40, 'SPI_PRIO 0xF8'], (OK, 1023), (FP, 40)),
    'spi-below-pmr': ([*SPI40, 'SPI_PRIO 0xFC'], (OK, 1023), (FP, 40)),
    'spi-above-pmr': ([*SPI40, 'SPI_PRIO 0xA0'], (FP, 40), (FP, 40)),
    'special-1020': (['HPPIR_EXTRA 1020 0x10'], (FP, 1020), (FP, 1020)),
    'special-1021': (['HPPIR_EXTRA 1021 0x10'], (FP, 1021), (FP, 1021)),
    'special-1022': (['HPPIR_EXTRA 1022 0x10'], (FP, 1022), (FP, 1022)),
    'lpi-8192': (['HPPIR_EXTRA 8192 0x10'], (FP, 8192), (FP, 8192)),
    'lpi-aliases-26': (['HPPIR_EXTRA 8218 0x10'], (FP, 8218), (FP, 8218)),        # 8192 + 26: PPI 26 in its low ten bits
    'lpi-aliases-1023': (['HPPIR_EXTRA 9215 0x10'], (FP, 9215), (FP, 9215)),      # 8192 + 1023: "nothing" in its low ten bits
    'lpi-largest': (['HPPIR_EXTRA 0xFFFFFF 0x10'], (FP, 0xFFFFFF), (FP, 0xFFFFFF)),
    'lpi-masked': (['HPPIR_EXTRA 8192 0xFC'], (OK, 1023), (FP, 8192)),
}
LATE = ['SPI_EN 1 0x100', 'SPI_LATE 1 0x100 160000000']   # INTID 40 gets pending 160,000,000 ticks into the run: after the check (8.000 s), before the timer's deadline (10.0 s)


def case(lines, ignores):
    return dict(gic=[*UNIT13, *lines, *([IGNORES] if ignores else [])])


NEW_CASES = {**{f'{name}-A': case(lines, False) for name, (lines, a, b) in ROWS.items()}, **{f'{name}-B': case(lines, True) for name, (lines, a, b) in ROWS.items()},
             'late-spi-A': case(LATE, False), 'late-spi-B': case(LATE, True)}

GIC_C = 'probes/sp11-stick-gic.c'
_CHECK = 'return id == SG_INTID_NONE || id == g->intid ? 0 : SG_OUT_FOREIGN_PENDING;'
_READ = 'id = (uint32_t)(sp11hw_read_icc_hppir1_el1() & 0xFFFFFF);'
# name -> (patch, the rows that must show it)
PATCHES = {
    'specials-accepted': ((GIC_C, _CHECK, _CHECK.replace('id == g->intid ?', 'id == g->intid || (id >= 1020 && id <= 1022) ?')), ('special-1020', 'special-1021', 'special-1022')),
    'lpis-accepted': ((GIC_C, _CHECK, _CHECK.replace('id == g->intid ?', 'id == g->intid || id > SG_INTID_NONE ?')), ('lpi-8192', 'lpi-largest')),
    'intid-cut-to-ten-bits': ((GIC_C, _READ, _READ.replace('0xFFFFFF', '0x3FF')), ('lpi-aliases-26', 'lpi-aliases-1023')),
}


def setUpModule():
    C.set_up('gic-hppir', cases=('unit-boot13',), variants=())
    mutant = os.environ.get('SP11_GIC_MUTANT')
    RUNS.update(batch(NEW_CASES, variant=mutant, patches=(PATCHES[mutant][0],)) if mutant else batch(NEW_CASES))


def tearDownModule():
    C.tear_down()


def run_of(name):
    return RUNS[name]


def decided(run, expected):
    """What the check decided in a run: the outcome and the INTID it read, against `expected` (outcome, INTID): the violations."""
    r = run.rec
    got = (r.outcome, r.sv.hppir_pre if r.sv.flags & G.SVF['HPPIR'] else None)
    return [] if got == expected else [f'the stage ended {G.OUTCOMES[r.outcome]} with the check reading {got[1]}, expected {G.OUTCOMES[expected[0]]} and {expected[1]}']


def refuses_foreign(rows):
    """For each row (a source the check must refuse in both variants): FOREIGN_PENDING with the INTID, in both."""
    found = []
    for name in rows:
        for variant in ('A', 'B'):
            found += [f'{name}-{variant}: {v}' for v in decided(run_of(f'{name}-{variant}'), ROWS[name][1 if variant == 'A' else 2])]
    return found


# ---- What the decision rule does in each variant of the model -----------------------------------------------------------------------------------------
class Decision(Base):
    def test_a_pending_enabled_spi_at_or_below_pmr_passes_when_hppir_honours_pmr_and_is_refused_when_it_does_not(self):
        for name in ('spi-at-pmr', 'spi-below-pmr'):
            a, b = run_of(f'{name}-A'), run_of(f'{name}-B')
            with self.subTest(row=name):
                self.assertEqual(decided(a, ROWS[name][1]) + decided(b, ROWS[name][2]), [])
                self.assertEqual((a.rec.sv.spi_both, a.rec.sv.spi_first, b.rec.sv.spi_both), (1, 40, 1), 'the survey sees the SPI in both variants')
                self.assertEqual([a.rec.irq[k].iar & 0xFFFFFF for k in (0, 1, 2, 4)], [26] * 4, 'over-acceptance: the masked SPI is never signalled, and the windows take PPI 26 four times')
                self.refused(b, FP, 'COPIED')
                self.assertEqual((a.rec.restore_failed, b.rec.restore_failed), (0, 0))
                self.holds(a, G.Checks.restored(a.g), G.Checks.acks(a.g, 4))
                self.holds(b, G.Checks.restored(b.g), G.Checks.no_window_with_foreign_pending(b.g))
                self.pages(a)
                self.pages(b)

    def test_an_spi_above_pmr_is_refused_in_both_variants(self):
        for variant in ('A', 'B'):
            run = run_of(f'spi-above-pmr-{variant}')
            with self.subTest(variant=variant):
                self.assertEqual(decided(run, (FP, 40)), [])
                self.refused(run, FP, 'COPIED')
                self.assertEqual(run.rec.sv.spi_first, 40)

    def test_the_special_intids_and_every_lpi_intid_are_refused_with_their_number_in_both_variants_and_nothing_is_unmasked(self):
        rows = [n for n in ROWS if n.startswith(('special', 'lpi')) and n != 'lpi-masked']
        self.assertEqual(refuses_foreign(rows), [])
        for name in rows:
            for variant in ('A', 'B'):
                run = run_of(f'{name}-{variant}')
                with self.subTest(row=name, variant=variant):
                    self.refused(run, FP, 'COPIED')
                    self.holds(run, G.Checks.restored(run.g), G.Checks.no_window_with_foreign_pending(run.g))
                    self.assertNotIn(('DAIF', 0), run.g.writes(), 'IRQ was never unmasked')
                    self.assertIn(f'SNAPMON GIC QUAR MASK=0X4A800001 DONE=0X4000000 HPPIR={ROWS[name][1][1]}', run.mon.texts())
                    self.pages(run)

    def test_an_lpi_at_or_below_pmr_follows_the_same_two_variants(self):
        self.assertEqual(decided(run_of('lpi-masked-A'), (OK, 1023)) + decided(run_of('lpi-masked-B'), (FP, 8192)), [])

    def test_the_check_is_one_read_at_one_instant_so_a_source_that_asserts_after_it_ends_the_first_window(self):
        for variant in ('A', 'B'):
            run = run_of(f'late-spi-{variant}')
            r, g = run.rec, run.g
            with self.subTest(variant=variant):
                self.verdict(run, OUT.WRONG_INTID)
                self.assertEqual((r.sv.hppir_pre, r.irq[0].iar & 0xFFFFFF, r.win[0].irq_entries, r.restore_failed), (1023, 40, 1, 0))
                self.assertEqual((r.sv.spi_both, G.classes(r.begun)), (0, 'GRCTV'), 'the survey, read earlier, did not see it either (nothing was pending then), and window 2 never ran')
                self.assertEqual((run.mon.ret, g.state['hang'], g.nviol), (0, 0, 0))
                self.holds(run, G.Checks.restored(g))
                self.pages(run)


# ---- Each check fails on a mutant of the real stage -------------------------------------------------------------------------------------------------
class Mutants(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if os.environ.get('SP11_GIC_MUTANT'):
            raise unittest.SkipTest('a mutation run: the scenarios already run on the mutant')
        with concurrent.futures.ThreadPoolExecutor(3) as pool:
            list(pool.map(lambda name: C.exe_of(name, (PATCHES[name][0],)), PATCHES))
        cls.runs = {name: batch({f'{row}-{v}': NEW_CASES[f'{row}-{v}'] for row in PATCHES[name][1] for v in ('A', 'B')}, variant=name, patches=(PATCHES[name][0],))
                    for name in PATCHES}

    def test_there_is_a_mutant_for_every_way_the_rule_could_let_a_special_or_an_lpi_intid_through(self):
        self.assertEqual(set(PATCHES), {'specials-accepted', 'lpis-accepted', 'intid-cut-to-ten-bits'})
        for name, (patch, rows) in PATCHES.items():
            for row in rows:
                self.assertIn(row, ROWS, name)

    def test_each_mutant_breaks_the_rows_that_match_it_and_the_real_stage_passes_them(self):
        for name, (patch, rows) in PATCHES.items():
            with self.subTest(mutant=name):
                self.assertEqual(refuses_foreign(rows), [], 'the real stage must refuse every row')
                found = []
                for row in rows:
                    for v in ('A', 'B'):
                        found += decided(self.runs[name][f'{row}-{v}'], ROWS[row][1 if v == 'A' else 2])
                self.assertEqual(len(found), 2 * len(rows), f'the mutant {name} must break every one of its rows in both variants: {found}')

    def test_the_mutants_are_the_real_stage_but_for_their_fault(self):
        run = self.runs['specials-accepted']['special-1021-A']
        self.assertEqual((run.rec.sv.hppir_pre, run.rec.outcome == FP), (1021, False), 'accepted at the check, the special INTID goes on to the window')
        run = self.runs['lpis-accepted']['lpi-8192-A']
        self.assertEqual((run.rec.sv.hppir_pre, run.rec.outcome == FP), (8192, False))
        run = self.runs['intid-cut-to-ten-bits']['lpi-aliases-26-A']
        self.assertEqual((run.rec.sv.hppir_pre, G.OUTCOMES[run.rec.first_outcome], run.rec.irq[0].iar & 0xFFFFFF), (26, 'WRONG_INTID', 8218),
                         'cut to ten bits an LPI reads as PPI 26, passes the check, and ends the first window with its own INTID (which the stage then does not end: it treats every '
                         'INTID from 1020 up as spurious, so the put-back finds the running priority raised)')
        run = self.runs['intid-cut-to-ten-bits']['lpi-aliases-1023-A']
        self.assertEqual((run.rec.sv.hppir_pre, run.rec.outcome == FP), (1023, False), 'and one that reads as 1023 passes as nothing')


if __name__ == '__main__':
    unittest.main()
