#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests that the text of the record of the GIC and stop dry run for the final stage (tools/sp11_stick_bundle_record_gic.py) says, register by register, what the
stage writes and what it reads: the reviewer's request that every memory-mapped store and read appear in the text, which the census cannot answer (it sees
system registers, not stores through computed addresses). The evidence is the host MODEL of the stage (tools/test-sp11-stick-gic.c with
tools/sp11_stick_gic_testlib.py): the real stage, compiled with sanitizers, run in every scenario of the GIC suite (the main run and the put-back,
quarantine and gate cases of pass 6), and the model's account of each register it saw written or read, by name (GICLOG for the writes, GICACC for the reads,
the memory-mapped ones included).

    python3 tools/test-sp11-stick-bundle-record-gic-model.py

Three rules, each with a deliberately faulty case: (A) every register a scenario WRITES is named as a write in the text (the retry writes, PPI 26's own
ICENABLER0 and ICPENDR0, and both DAIF writes included); (B) every register the model only READS (the RWP polls of GICR_CTLR among them) is named as a read; (C)
every register the text claims to write is written in some scenario, except SPSR_EL2 and ELR_EL2, which the assembly of the windows writes and the model replaces
(the census of the binary has them: tools/test-sp11-stick-bundle-record-gic.py). What the model proves is the stage's order and its accesses against the model, and
nothing about the unit. It needs clang and a q1n1 checkout ($Q1N1_SP11_TREE) and SKIPs without them, which is not a pass. No hardware: NOT RUN. Everything is
written below build/sp11-wrapper-test-scratch-record-gic-model/, deleted at the end."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS, THIS = ROOT / 'tools', Path(__file__).resolve()
sys.path.insert(0, str(TOOLS))
import sp11_monitor_census as C  # noqa: E402
import sp11_stick_bundle as sb  # noqa: E402
import sp11_stick_bundle_record as rec  # noqa: E402
import sp11_stick_bundle_record_gic as gq  # noqa: E402
import sp11_stick_gic_checks as GC  # noqa: E402
import sp11_stick_gic_testlib as G  # noqa: E402
import sp11_stick_wrapper_build as wb  # noqa: E402
import sp11_stick_wrapper_testlib as T  # noqa: E402

BOTH = ['NOHOLD GICD_CTLR', 'NOHOLD ICC_IGRPEN1_EL1']                 # the two group 1 gates do not go back after window 1 (pass 6: the last write is withheld)
BOTH_0 = ['NOHOLD GICD_CTLR 0', 'NOHOLD ICC_IGRPEN1_EL1 0']           # the same, from the start of the put-back: also for a run in which no window ran
SPI40 = ['SPI_EN 1 0x100', 'SPI_PEND 1 0x100']                        # INTID 40 enabled and pending: the case the quarantine does not cover
# The scenarios of pass 6 that the main run of the GIC suite does not hold: the gates, a quarantine that holds in part, RWP stuck and lagging, and put-backs that fail
# after a refusal. The main run (GC.CASES: the unit's states, every refusal, every window that ends badly, every register that does not hold) is run as well.
EXTRA = {
    'gates-both': dict(gic=[*GC.UNIT13, *BOTH]),
    'gates-igrpen1': dict(gic=[*GC.UNIT13, 'NOHOLD ICC_IGRPEN1_EL1']),
    'rwp-stuck': dict(gic=[*GC.UNIT13, 'RWP gicr']),
    'rwp-lag': dict(gic=[*GC.UNIT13, 'RWP_LAG gicr 4']),
    'partial-hold': dict(gic=[*GC.UNIT13, 'ICE_HOLD 0x00800001']),
    'partial-hold-putback-fails': dict(gic=[*GC.UNIT13, 'ICE_HOLD 0x00800001', 'NOHOLD GICR_ISENABLER0 0']),
    'foreign-isen-stuck': dict(gic=[*GC.UNIT13, *SPI40, 'NOHOLD GICR_ISENABLER0 0']),
    'foreign-gates-both-stuck': dict(gic=[*GC.UNIT13, *SPI40, *BOTH_0]),
}
# How the text spells what the model calls a register: the redistributor's registers without the frame's name, the stack pointer select as the instruction set has it.
TEXT_NAME = {'GICR_IGROUPR0': 'IGROUPR0', 'GICR_ISENABLER0': 'ISENABLER0', 'GICR_ICENABLER0': 'ICENABLER0', 'GICR_ISPENDR0': 'ISPENDR0',
             'GICR_ICPENDR0': 'ICPENDR0', 'GICR_ISACTIVER0': 'ISACTIVER0', 'GICR_IPRIORITYR': 'IPRIORITYR', 'GICR_ICFGR1': 'ICFGR1', 'SPSEL': 'SPSel'}
# The registers the text may name that the model does not know: the windows' assembly writes them (sp11-stick-gic-vec.c) and the model replaces that assembly.
ASSEMBLY = ('SPSR_EL2', 'ELR_EL2')
ASSEMBLY_FUNCTIONS = {'sw_gic_excursion', 'sw_gic_excursion_exit', 'sw_gic_vec_common'}
# What counts as the name of a register in the text.
REGISTER = re.compile(r'\b(?:[A-Z][A-Z0-9_]*_EL[0-3]|GIC[DR]_[A-Z0-9]+|IGROUPR0|ISENABLER0|ICENABLER0|ISPENDR0|ICPENDR0|ISACTIVER0|IPRIORITYR|ICFGR1|'
                      r'DAIFSet|DAIFClr|DAIF|SPSel|CurrentEL)\b')
WRITE_PARTS, READ_PARTS = (gq.EL2_WRITES, gq.GIC_WRITES), (gq.EL2_READS, gq.GIC_READS, gq.GIC_CHECK)
CLASS_PARTS = {'el2_sysreg': (gq.EL2_WRITES, gq.EL2_OTHER, gq.EL2_READS, gq.EL2_NOT, gq.EL2_TIME),
               'gic': (gq.GIC_HEAD, gq.GIC_READS, gq.GIC_WRITES, gq.GIC_CHECK, gq.GIC_NEVER)}
FIXED = dict(place=sb.placement(0x880000000, 384 * sb.MIB, 48 * sb.MIB), stores=7, time_box_s=5, label='GICDRY-C', monitor=(98236, '1' * 64), reporter=(62000, '2' * 64),
             note=None, gic_stop=True, gic_quarantine=True, guest=dict(size=118870336, sha256='3' * 64, chunks=44, index='4' * 64))


def setUpModule():
    GC.set_up('record-gic-model', cases=None)
    if GC.runnable():
        GC.RUNS.update(GC.batch(EXTRA))


def tearDownModule():
    GC.tear_down()


def text_name(name):
    return TEXT_NAME.get(name, name)


def facts(runs):
    """(written, read, only_read): the register names, as the text spells them, that the scenarios of `runs` wrote and read, with DAIF split into the instruction
    that unmasks IRQ (DAIFClr, written 0) and the one that masks it (DAIFSet, 1); only_read is read and never written."""
    written, read = set(), set()
    for run in runs.values():
        for kind, reg, value, _, _ in run.g.log:
            if kind == 'W' and reg == 'DAIF':
                written.add('DAIFClr' if value == 0 else 'DAIFSet')
        for reg, (reads, writes) in run.g.acc.items():
            if writes and reg != 'DAIF':
                written.add(text_name(reg))
            if reads:
                read.add(text_name(reg))
    return written, read, read - written


def named(lines):
    return set(REGISTER.findall(' '.join(lines)))


def rule_problems(written, only_read, write_lines, read_lines, assembly=ASSEMBLY):
    """The three rules, each as the problems it finds: [str]."""
    in_writes, in_reads = named(write_lines), named(read_lines)
    problems = [f'the model writes {n} and the text names no write of it' for n in sorted(written - in_writes)]
    problems += [f'the model only reads {n} and the text names no read of it' for n in sorted(only_read - in_reads)]
    problems += [f'the text claims to write {n} and no scenario writes it' for n in sorted(in_writes - written - set(assembly))]
    return problems


def without(lines, name):
    """`lines` with every whole-word occurrence of `name` taken out."""
    return [re.sub(rf'(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])', '', line) for line in lines]


def flat(parts):
    return [line for part in parts for line in part]


def withheld_problems(run):
    """What the text says about pass 6's last write ("NOT made when the put-backs of both ICC_IGRPEN1_EL1 and GICD_CTLR failed ... ISENABLER0 is then only read and
    named in FAILED, the foreign interrupts stay disabled and the run ends NOT_RESTORED"), as the problems a run shows against it: [str]. A run in which it holds
    has none."""
    g = run.g
    r, problems = g.r(), []
    if r.outcome != G.OUT.NOT_RESTORED:
        problems.append(f'the outcome is {G.OUTCOMES[r.outcome]}, not NOT_RESTORED')
    if g.acc['GICR_ISENABLER0'][1]:
        problems.append('ISENABLER0 is written')
    if r.regs['ISENABLER0']['fin'] != 1 << 26:
        problems.append('the foreign interrupts are not left disabled')
    if not r.restore_failed & G.REG_BIT['ISENABLER0']:
        problems.append('ISENABLER0 is not named in FAILED')
    return problems


def header_number(pattern):
    """A number that a header of the stage defines."""
    text = (ROOT / 'probes/sp11-stick-gic.h').read_text() + (ROOT / 'probes/sp11-stick-gic-record.h').read_text()
    return int(re.search(pattern, text).group(1), 0)


def silences(run):
    """[(page line, seconds)] for every NEXT line of `run` whose counter the model recorded: from the line to the last access of the model before the next line was
    printed (the stage's own waits read the counter and make no access, so a wait is in that time). The model steps the counter, not a clock: the seconds are the
    counter's, at the stage's counter frequency."""
    texts, ticks, log, found = run.mon.texts(), run.g.line_ticks, run.g.log, []
    for index, text in enumerate(texts):
        if ' NEXT ' in text and index in ticks:
            end = max((event[4] for event in log if event[3] == index + 1), default=ticks[index])
            found.append((text, (end - ticks[index]) / GC.HZ))
    return found


def silence_problems(rule_s, healthy_s, orderly_s):
    """What is wrong with a stop rule of `rule_s` seconds without a page change, against the longest silence of a healthy run and of any orderly one in the model: it
    must be above the first, and above the second with 2 s to spare: [str]."""
    if rule_s <= healthy_s:
        return [f'{rule_s} s would stop a healthy run, which is silent for {healthy_s:.1f} s']
    return [f'{rule_s} s leaves less than 2 s over the {orderly_s:.1f} s of an orderly run'] * (rule_s < orderly_s + 2)


class Model(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not GC.runnable() or not GC.RUNS:
            raise unittest.SkipTest('no clang or q1n1 checkout (Q1N1_SP11_TREE): the host model of the stage is not built')
        cls.runs = GC.RUNS
        cls.written, cls.read, cls.only_read = facts(cls.runs)
        cls.writes, cls.reads = flat(WRITE_PARTS), flat(READ_PARTS)

    # ---- what the model says ------------------------------------------------------------------------------------------------------------
    def test_the_interface_agrees_with_the_event_log_and_no_scenario_writes_anything_unmodelled(self):
        for name, run in self.runs.items():
            g = run.g
            with self.subTest(name):
                self.assertNotIn('UNMODELLED', g.acc)
                self.assertEqual(g.nviol, 0, g.viol)
                if len(g.log) < 40000:
                    for reg, (reads, writes) in g.acc.items():
                        logged = [e for e in g.log if e[1] == reg]
                        self.assertEqual(writes, sum(1 for e in logged if e[0] == 'W'), (reg, 'writes'))
                        if any(e[0] == 'R' for e in logged):
                            self.assertEqual(reads, sum(1 for e in logged if e[0] == 'R'), (reg, 'reads'))

    def test_the_scenarios_hold_what_the_rules_are_about(self):
        self.assertGreaterEqual(len(self.runs), 100)
        for reg in ('GICD_CTLR', 'ICENABLER0', 'ISENABLER0', 'ICPENDR0', 'IPRIORITYR', 'ICFGR1', 'IGROUPR0', 'ICC_PMR_EL1', 'ICC_BPR1_EL1', 'ICC_IGRPEN1_EL1', 'ICC_EOIR1_EL1',
                    'ICC_DIR_EL1', 'CNTHP_CVAL_EL2', 'CNTHP_CTL_EL2', 'VBAR_EL2', 'DAIFClr', 'DAIFSet', 'HCR_EL2', 'VBAR_EL1', 'SCTLR_EL1'):
            self.assertIn(reg, self.written, reg)
        for reg in ('GICR_CTLR', 'GICR_TYPER', 'GICR_WAKER', 'GICD_TYPER', 'GICD_IIDR', 'GICD_ISENABLER', 'GICD_ISPENDR', 'ISPENDR0', 'ISACTIVER0', 'ICC_SRE_EL2', 'ICC_RPR_EL1',
                    'ICC_HPPIR1_EL1', 'ICC_IAR1_EL1', 'SPSel', 'CNTHCTL_EL2', 'CNTV_CTL_EL0', 'CNTP_CVAL_EL0'):
            self.assertIn(reg, self.only_read, reg)

    def test_the_log_has_the_retry_writes_pass_6_s_withheld_write_ppi_26_s_own_clear_and_the_rwp_polls(self):
        unit = self.runs['unit-boot13'].g
        self.assertGreaterEqual(unit.acc['GICR_CTLR'][0], 2, 'the RWP polls after the quarantine and after its last write are reads of GICR_CTLR')
        values = [v for n, v in self.runs['happy'].g.writes() if n == 'GICR_ICENABLER0']
        self.assertIn(1 << 26, values, 'PPI 26\'s own enable, set by the stage and cleared again by ICENABLER0 with its bit alone')
        self.assertTrue(any(n == 'GICR_ICPENDR0' for r in self.runs.values() for n, _ in r.g.writes()))
        retry = self.runs['quarantine-putback-ignored'].g.acc['GICR_ISENABLER0'][1]
        clean = self.runs['unit-boot13'].g.acc['GICR_ISENABLER0'][1]
        self.assertGreater(retry, clean, 'a put-back that did not hold is written once more: the retry is in the log')
        both = self.runs['gates-both'].g
        self.assertEqual(self.runs['unit-boot13'].g.acc['GICR_ISENABLER0'][1] - both.acc['GICR_ISENABLER0'][1], 1, 'pass 6: the last ISENABLER0 write is not made')

    def test_pass_6_s_exception_in_the_text_is_what_the_model_does_when_both_gates_stay_open_and_only_then(self):
        for name in ('gates-both', 'foreign-gates-both-stuck'):
            with self.subTest(name):
                self.assertEqual(withheld_problems(self.runs[name]), [])
                self.assertEqual([n for n in G.REGS if self.runs[name].g.r().restore_failed & G.REG_BIT[n]], ['GICD_CTLR', 'ISENABLER0', 'ICC_IGRPEN1'])
        self.assertEqual(hex(G.OUT.NOT_RESTORED), '0x30')
        for name in ('unit-boot13', 'gates-igrpen1', 'partial-hold'):
            with self.subTest(f'not withheld: {name}'):
                self.assertTrue(withheld_problems(self.runs[name]), 'a run in which the write is made, or in which one gate went back, does not show the exception')
        one = self.runs['gates-igrpen1'].g
        self.assertEqual(one.acc['GICR_ISENABLER0'][1], 1, 'one gate that goes back is enough: the write is made')
        text = ' '.join(' '.join(self.writes).split())
        self.assertIn('except that it is NOT made when the put-backs of both ICC_IGRPEN1_EL1 and GICD_CTLR failed', text)
        self.assertIn('the run ends NOT_RESTORED (OUTCOME=0X30)', text)

    def test_the_silence_after_each_next_line_in_the_model_is_what_the_text_says_and_the_documents_15_s_is_above_it(self):
        wait, box = header_number(r'SW_GIC_WAIT_S (\d+)u'), header_number(r'SP11_STICK_GIC_TIME_BOX_S (\d+)u')
        unit = [seconds for _, seconds in silences(self.runs['unit-boot13'])]
        self.assertEqual(len(unit), 3, 'NEXT GIC STORES, NEXT GIC WINDOW 2 S and NEXT GIC EL1 EXCURSION')
        for got, want in zip(unit, (wait, box, wait + box)):
            self.assertAlmostEqual(got, want, delta=0.1)          # the wait; window 1 (about the box); the wait and window 2
        healthy = max(s for run in self.runs.values() if run.rec.outcome == G.OUT.IRQ_OK for _, s in silences(run))
        orderly = max(s for run in self.runs.values() for _, s in silences(run))
        self.assertAlmostEqual(healthy, wait + box, delta=0.1)
        self.assertAlmostEqual(orderly, wait + 2 * box, delta=0.1)       # window 2 at the stub's bound, having seen no interrupt
        text = ' '.join(' '.join(gq.silence_lines()).split())
        rule = int(re.search(r'No page change for (\d+) s after a NEXT line', text).group(1))
        self.assertEqual(silence_problems(rule, healthy, orderly), [])
        for says in (f'silent for {wait} s after NEXT GIC STORES AFTER {wait} S', f'about {round(healthy)} s after NEXT GIC EL1 EXCURSION AFTER {wait} S', f'at most {round(orderly)} s: section 4'):
            self.assertIn(says, text)
        self.assertEqual(len(silence_problems(10, healthy, orderly)), 1, 'twice the time box of 5 s, the rule of the other records, would stop a healthy run')
        self.assertEqual(len(silence_problems(12, healthy, orderly)), 1)
        self.assertEqual(len(silence_problems(14, healthy, 13.0)), 1)

    # ---- the text is its parts ------------------------------------------------------------------------------------------------------------
    def test_every_line_of_the_two_classes_is_in_exactly_one_part_and_the_rendered_record_has_them(self):
        for cls, parts in CLASS_PARTS.items():
            self.assertEqual(gq.TOUCHES[cls], tuple(flat(parts)), cls)
        text = rec.record_text(FIXED)
        for parts in (*CLASS_PARTS.values(), (gq.TOUCHES['ram'],)):
            for line in flat(parts):
                self.assertIn(line, text)

    # ---- the three rules ----------------------------------------------------------------------------------------------------------------------
    def test_every_register_a_scenario_writes_is_named_as_a_write_in_the_text(self):
        self.assertEqual([p for p in rule_problems(self.written, self.only_read, self.writes, self.reads) if 'names no write' in p], [])

    def test_every_register_the_model_only_reads_is_named_as_a_read_in_the_text(self):
        self.assertEqual([p for p in rule_problems(self.written, self.only_read, self.writes, self.reads) if 'names no read' in p], [])

    def test_every_register_the_text_claims_to_write_is_written_in_some_scenario(self):
        self.assertEqual([p for p in rule_problems(self.written, self.only_read, self.writes, self.reads) if 'claims to write' in p], [])

    def test_the_registers_only_the_assembly_writes_are_the_binarys_and_the_model_has_neither(self):
        self.assertFalse(set(ASSEMBLY) & (self.written | self.read))
        out = T.BUILD / 'elf'
        wb.build(str(out), str(ROOT / 'config/monitor-profile-sp11-m1.json'), False, True)
        census = C.census(out / 'snapmon-gic.elf')
        for name in ASSEMBLY:
            self.assertIn(name, census['msr'], name)
            self.assertEqual(set(census['msr'][name]), ASSEMBLY_FUNCTIONS, name)

    # ---- each rule can fail ----------------------------------------------------------------------------------------------------------------
    def test_a_register_written_in_a_scenario_and_missing_from_the_text_fails_naming_it(self):
        for name in sorted(self.written):
            with self.subTest(name):
                problems = rule_problems(self.written, self.only_read, without(self.writes, name), self.reads)
                self.assertEqual([p for p in problems if p.startswith(f'the model writes {name} ')], [f'the model writes {name} and the text names no write of it'])

    def test_a_register_the_model_only_reads_and_missing_from_the_text_fails_naming_it(self):
        for name in sorted(self.only_read):
            with self.subTest(name):
                problems = rule_problems(self.written, self.only_read, self.writes, without(self.reads, name))
                self.assertEqual([p for p in problems if p.startswith(f'the model only reads {name} ')], [f'the model only reads {name} and the text names no read of it'])

    def test_a_text_that_names_a_write_no_scenario_makes_fails_naming_it(self):
        for name in ('GICR_WAKER', 'GICR_TYPER', 'ICC_RPR_EL1', 'ICC_HPPIR1_EL1', 'TPIDR_EL2', 'GICD_ISENABLER', 'CNTHCTL_EL2'):
            with self.subTest(name):
                problems = rule_problems(self.written, self.only_read, [*self.writes, f'and {name}'], self.reads)
                self.assertEqual([p for p in problems if f'claims to write {name} ' in p], [f'the text claims to write {name} and no scenario writes it'])

    def test_a_model_with_fewer_scenarios_fails_the_text_that_names_what_only_the_others_write(self):
        small = {k: self.runs[k] for k in ('happy', 'unit-boot13')}
        written, read, only_read = facts(small)
        problems = rule_problems(written, only_read, self.writes, self.reads)
        for name in ('IGROUPR0', 'ICFGR1', 'ICPENDR0'):
            self.assertIn(f'the text claims to write {name} and no scenario writes it', problems)

    def test_the_assembly_allowance_is_only_for_the_two_registers_and_is_not_a_hole(self):
        problems = rule_problems(self.written, self.only_read, [*self.writes, 'and VBAR_EL3'], self.reads)
        self.assertEqual(problems, ['the text claims to write VBAR_EL3 and no scenario writes it'])
        self.assertEqual(rule_problems(self.written, self.only_read, self.writes, self.reads, assembly=()),
                         [f'the text claims to write {n} and no scenario writes it' for n in sorted(ASSEMBLY)])


class Hygiene(unittest.TestCase):
    def test_the_file_is_ascii_mit_small_and_holds_no_machine_text(self):
        text = THIS.read_text()
        self.assertTrue(text.isascii())
        self.assertIn('SPDX-License-Identifier: MIT', '\n'.join(text.splitlines()[:3]))
        self.assertLess(len(text.splitlines()), 1500)
        self.assertLess(len(text.encode()), 50 * 1024)
        for prefix in ('/' + 'Users' + '/', '/' + 'home' + '/', '/' + 'tmp' + '/', 'mk' + 'temp'):
            self.assertNotIn(prefix, text)
        self.assertIsNone(re.search(r'\b(?:TO' r'DO|FIX' r'ME|X' r'XX|HA' r'CK|W' r'IP)\b', text))

    def test_the_model_files_stay_under_their_caps(self):
        for name in ('test-sp11-stick-gic.c', 'sp11_stick_gic_testlib.py'):
            self.assertLess((TOOLS / name).stat().st_size, 50 * 1024, name)


if __name__ == '__main__':
    unittest.main()
