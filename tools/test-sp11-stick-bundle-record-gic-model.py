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
(the census of the binary has them: tools/test-sp11-stick-bundle-record-gic.py). Beside the rules, the sentences of the text that say what the stage does in a given
case are held to the model, each with a case that fails it: a gate found open is not counted (so the last ISENABLER0 write IS made), a window takes and ends whatever
INTID arrives and writes no SPI register, the bounds that the durations add up (4.08 s, 8 s, 4 s), no store before a refusal of 0X1 to 0XF, no REFUSED line after a
window verdict, a data abort in a window ends it with everything put back, and the registers that are not read first. The class Source holds, with no model and no toolchain, the
sentences that describe the stage's source and the documents (COUNTER, whole-register writes, the end of a foreign interrupt, the verdict return, the heading of section 2, the paragraph
of what the stage never touches, the silence rule for any point of the page, the boot attributions of the reads) and the commit that the note names against the sources of the
pair, with the q1n1 files it names against the build tool's inputs. What the model proves is the stage's order and its accesses against the model, and
nothing about the unit. It needs clang and a q1n1 checkout ($Q1N1_SP11_TREE) and SKIPs without them, which is not a pass. No hardware: NOT RUN. Everything is
written below build/sp11-wrapper-test-scratch-record-gic-model/, deleted at the end."""
import collections
import re
import subprocess
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
    # EnableGrp1A found set (class G is never begun) and ICC_IGRPEN1_EL1 stuck open: a gate found open is not counted, so the last ISENABLER0 write IS made.
    'gates-found-open': dict(gic=[*GC.UNIT13, 'CTLR 0x12', 'NOHOLD ICC_IGRPEN1_EL1']),
    'foreign-spi': dict(gic=['INTID 40']),
    'foreign-spi-eoimode': dict(gic=['INTID 40', 'ICCCTLR 2']),
    'late': dict(gic=['DELAY 9000000']),                  # every delivery 0.375 s late: still a pass, in a run that lasts longer than 20 s
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


def stored(run):
    return any(event[0] == 'W' for event in run.g.log)


def verdict_problems(runs, lowest=0x20, highest=0x2F):
    """The runs whose page has a REFUSED line exactly when the outcome is in lowest..highest: [name]. The text says that no REFUSED line follows a window verdict
    (OUTCOME 0X20 to 0X2F, IRQ_OK 0X20 included), so for the right range there are none; for a narrower one there are."""
    return [name for name, run in runs.items() if any('REFUSED' in text for text in run.mon.texts()) == (lowest <= run.rec.outcome <= highest)]


def window_1_problems(run, limit_s):
    """Where `run` unmasks IRQ (DAIFClr): after the NEXT line of window 1, before the next NEXT line, and masked again (DAIFSet) within `limit_s` seconds: [str]."""
    texts, ticks, log = run.mon.texts(), run.g.line_ticks, run.g.log
    nexts, problems = [ticks[i] for i, text in enumerate(texts) if ' NEXT ' in text and i in ticks], []
    for n, (kind, reg, value, _, tick) in enumerate(log):
        if kind == 'W' and reg == 'DAIF' and value == 0:
            masked = next((e[4] for e in log[n + 1:] if e[0] == 'W' and e[1] == 'DAIF' and e[2] != 0), None)
            if not (len(nexts) >= 2 and nexts[1] <= tick and (len(nexts) < 3 or tick < nexts[2])):
                problems.append('IRQ is unmasked outside window 1')
            if masked is None or masked - tick > limit_s * GC.HZ:
                problems.append(f'IRQ stays unmasked for more than {limit_s} s')
    return problems


def written_never_read(runs, prefix):
    """The registers named `prefix`... that some scenario writes and none reads: {name}."""
    reads, writes = collections.Counter(), collections.Counter()
    for run in runs.values():
        for reg, (read, write) in run.g.acc.items():
            reads[reg] += read
            writes[reg] += write
    return {reg for reg in writes if writes[reg] and not reads[reg] and reg.startswith(prefix)}


def except_names(lines):
    """The registers that the text says are not read first, put back and verified by read-back ("..., except A, B and the handlers' C and D (below)"): {name}."""
    match = re.search(r'each read first, put back and verified by read-back, except (.*?) \(below\)', ' '.join(' '.join(lines).split()))
    return set(REGISTER.findall(match.group(1))) if match else set()


CACHE = {}


def final_census():
    """The census of the ELF of the final wrapper, built once."""
    if 'census' not in CACHE:
        out = T.BUILD / 'elf'
        wb.build(str(out), str(ROOT / 'config/monitor-profile-sp11-m1.json'), False, True)
        CACHE['census'] = C.census(out / 'snapmon-gic.elf')
    return CACHE['census']


def counter_problems(gic, dry):
    """Where the stage returns COUNTER, against the text ("0X1 to 0XF a refusal, before any store except COUNTER at the second 8 s wait"): [str]. `gic` and `dry` are the
    sources probes/sp11-stick-gic.c and probes/sp11-stick-gic-dry.c."""
    arm = gic.split('unsigned sw_gic_arm_stores(', 1)[1].split('\n}\n', 1)[0]
    second = dry.split('static unsigned gic_el1_window(', 1)[1].split('\n}\n', 1)[0]
    problems = [] if 'sw_gic_wait_seconds(g->hz, SW_GIC_WAIT_S)) return SG_OUT_COUNTER;' in second else ['the second 8 s wait does not return COUNTER']
    if not 0 <= arm.find('return SG_OUT_COUNTER') < arm.find('gic_store_q(g)'):
        problems.append('the first 8 s wait does not come before the first store')
    return problems + ['another place returns COUNTER'] * ((gic.count('SG_OUT_COUNTER'), dry.count('SG_OUT_COUNTER')) != (3, 1))


def pair_sources():
    """The files of the repository that the final pair is built from: the sources of both payloads and the headers they include, the build tool and the profile."""
    todo, seen = [s for name in wb.GIC_NAMES for s in wb.SOURCES[name] if s.startswith('probes/')], set()
    while todo:
        path = todo.pop()
        if path not in seen:
            seen.add(path)
            todo += [f'probes/{name}' for name in re.findall(r'#include "([^"]+)"', (ROOT / path).read_text()) if (ROOT / 'probes' / name).is_file()]
    return sorted(seen | {'tools/sp11_stick_wrapper_build.py', 'config/monitor-profile-sp11-m1.json'})


def source_problems(commit, files):
    """The `files` that differ between the tree of `commit` and this one: [str]. SKIPS when git does not have the commit (a shallow clone)."""
    git = lambda *args: subprocess.run(['git', '-C', str(ROOT), *args], capture_output=True, text=True)       # noqa: E731
    if git('cat-file', '-e', f'{commit}^{{commit}}').returncode:
        raise unittest.SkipTest(f'git does not have commit {commit[:7]}')
    return git('diff', '--name-only', commit, '--', *files).stdout.split()


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
        healthy = max(s for name, run in self.runs.items() if name != 'late' and run.rec.outcome == G.OUT.IRQ_OK for _, s in silences(run))       # interrupts on time
        orderly = max(s for run in self.runs.values() for _, s in silences(run))
        self.assertTrue(healthy < max(s for _, s in silences(self.runs['late'])) <= orderly, 'a late healthy run is silent a little longer, and within the 12 s')
        self.assertAlmostEqual(healthy, wait + box, delta=0.1)
        self.assertAlmostEqual(orderly, wait + 2 * box, delta=0.1)       # window 2 at the stub's bound, having seen no interrupt
        text = ' '.join(' '.join(gq.silence_lines()).split())
        rule = int(re.search(r'No page change for (\d+) s at any point, a NEXT line or not', text).group(1))
        self.assertEqual(silence_problems(rule, healthy, orderly), [])
        for says in (f'silent for {wait} s after NEXT GIC STORES AFTER {wait} S', f'about {round(healthy)} s after NEXT GIC EL1 EXCURSION AFTER {wait} S', f'at most {round(orderly)} s: section 4'):
            self.assertIn(says, text)
        self.assertEqual(len(silence_problems(10, healthy, orderly)), 1, 'twice the time box of 5 s, the rule of the other records, would stop a healthy run')
        self.assertEqual(len(silence_problems(12, healthy, orderly)), 1)
        self.assertEqual(len(silence_problems(14, healthy, 13.0)), 1)

    # ---- the sentences about what the stage does in a given case -----------------------------------------------------------------------------
    def test_a_gate_found_open_is_not_counted_so_the_last_isenabler0_write_is_made(self):
        run = self.runs['gates-found-open']
        r = run.g.r()
        self.assertEqual([n for n in G.REGS if r.restore_failed & G.REG_BIT[n]], ['ICC_IGRPEN1'], 'the one gate that the stage set does not go back')
        self.assertNotIn('G', G.classes(r.begun), 'EnableGrp1A was found set: class G is never begun')
        self.assertEqual((run.g.acc['GICD_CTLR'][1], run.g.acc['GICR_ISENABLER0'][1], r.outcome), (0, 1, G.OUT.NOT_RESTORED), 'GICD_CTLR is not written; the write is made')
        self.assertTrue(withheld_problems(run), 'it is not the pass 6 exception: the write is made')
        self.assertEqual(withheld_problems(self.runs['gates-both']), [], 'both gates found closed and left open: the exception')
        says = 'A gate found open is not counted: with EnableGrp1A found set (class G never begun) and ICC_IGRPEN1_EL1 stuck open the write IS made'
        self.assertIn(says, ' '.join(' '.join(gq.GIC_WRITES).split()))
        self.assertIn('so a gate found open is not counted', ' '.join((ROOT / 'docs/SP11-STICK-GIC.md').read_text().split()))

    def test_a_window_takes_and_ends_whatever_intid_arrives_and_no_spi_register_is_written(self):
        for name, eoimode in (('foreign-spi', False), ('foreign-spi-eoimode', True)):
            with self.subTest(name):
                run = self.runs[name]
                self.assertEqual(run.rec.outcome, G.OUT.WRONG_INTID)
                self.assertGreaterEqual(run.g.acc['ICC_IAR1_EL1'][0], 1, 'acknowledged')
                self.assertEqual([w for w in run.g.writes() if w[0] in ('ICC_EOIR1_EL1', 'ICC_DIR_EL1')], [('ICC_EOIR1_EL1', 40), *[('ICC_DIR_EL1', 40)] * eoimode])
        self.assertIn(('ICC_DIR_EL1', 26), self.runs['unit-boot13'].g.writes(), 'EOImode was set in boot 13')
        self.assertEqual({n for n in self.written if n.startswith('GICD_')}, {'GICD_CTLR'}, 'the one distributor register that the stage writes: no SPI register')
        unit = self.runs['unit-boot13'].g.acc
        self.assertEqual((unit['GICR_IGROUPR0'][1], unit['GICR_ICFGR1'][1]), (0, 0), 'on the unit\'s reads (DS 0, ICFGR1 level) neither is written')
        text = ' '.join(' '.join(gq.GIC_NEVER).split())
        for says in ('No SPI register is written (the SPIs are only read)', 'ICC_IAR1_EL1 acknowledges it, ICC_EOIR1_EL1 ends it (ICC_DIR_EL1 too when EOImode is set; it was '
                     'in boot 13), a foreign SPI included', 'GICD_CTLR EnableGrp1A is distributor-wide'):
            self.assertIn(says, text)

    def test_the_bounds_that_the_durations_add_up_are_the_model_s_and_a_late_interrupt_still_passes(self):
        wait, box = header_number(r'SW_GIC_WAIT_S (\d+)u'), header_number(r'SP11_STICK_GIC_TIME_BOX_S (\d+)u')
        extra = 2 * (header_number(r'#define SG_CYCLES (\d+)u') + 1) * header_number(r'SP11_STICK_GIC_CYCLE_US (\d+)u') / 1e6
        seen = [(text, seconds) for run in self.runs.values() for text, seconds in silences(run)]
        window_1 = max(s for text, s in seen if 'WINDOW 2' in text)
        window_2 = max(s for text, s in seen if 'EL1 EXCURSION' in text) - wait
        self.assertAlmostEqual(window_1, 2 * box + extra, delta=0.02)        # window 1 sees no interrupt: 4.08 s
        self.assertAlmostEqual(window_2, 2 * box, delta=0.1)                 # the stub's own bound
        total = round(window_1 + 2 * wait + window_2)
        text = ' '.join(' '.join([*gq.time_lines(5), *gq.EL2_TIME]).split())
        for says in (f'at most about {total} s, the sum of the bounds ({window_1:.2f} s + {wait} s + {wait} s + {window_2:.0f} s)', f'at most about {total} s (section 4)'):
            self.assertIn(says, text)
        late, on_time = self.runs['late'], self.runs['unit-boot13']
        span = lambda run: max(event[4] for event in run.g.log) / GC.HZ       # noqa: E731
        self.assertEqual(late.rec.outcome, G.OUT.IRQ_OK)
        self.assertAlmostEqual(span(on_time), 2 * wait + 2 * box, delta=0.1)
        self.assertTrue(span(on_time) + 1 < span(late) <= window_1 + 2 * wait + window_2 + 0.1, 'late deliveries pass, in a run that lasts longer than 20 s and within the bounds')

    def test_a_refusal_of_0X1_to_0XF_comes_before_any_store_in_the_model(self):
        refusals = [name for name, run in self.runs.items() if 1 <= run.rec.outcome <= 15]
        self.assertGreaterEqual(len(refusals), 10)
        self.assertEqual([name for name in refusals if stored(self.runs[name])], [])
        self.assertTrue([name for name, run in self.runs.items() if 16 <= run.rec.outcome <= 31 and stored(run)], '0X10 to 0X1F come after a store: the check can fail')

    def test_a_window_verdict_prints_no_refused_line_and_every_other_outcome_does(self):
        self.assertEqual(verdict_problems(self.runs), [])
        self.assertTrue(verdict_problems(self.runs, 0x21), 'IRQ_OK (0X20) is a verdict too: a range that leaves it out fails')
        self.assertTrue(verdict_problems(self.runs, 0x20, 0x30), 'NOT_RESTORED (0X30) prints a REFUSED line: a range that holds it fails')

    def test_a_data_abort_in_a_window_ends_it_with_everything_put_back(self):
        for name, outcome in (('sync', 'EXCEPTION'), ('el1-sync-class', 'EL1_EXCEPTION')):
            r = self.runs[name].g.r()
            self.assertEqual((G.OUTCOMES[r.outcome], r.outcome, r.restore_failed), (outcome, header_number(rf'X\({outcome}, (\d+)\)'), 0), name)
        self.assertEqual([self.runs['sync'].g.r().outcome, self.runs['el1-sync-class'].g.r().outcome], [0x23, 0x2C])

    def test_the_registers_that_are_not_read_first_are_the_text_s_and_irq_is_unmasked_only_in_window_1(self):
        self.assertEqual(written_never_read(self.runs, 'ICC_'), {'ICC_EOIR1_EL1', 'ICC_DIR_EL1'})
        self.assertEqual(except_names(self.writes), {'ICC_EOIR1_EL1', 'ICC_DIR_EL1', *ASSEMBLY})
        self.assertNotEqual(except_names([line.replace('ICC_EOIR1_EL1, ICC_DIR_EL1 and', 'ICC_EOIR1_EL1 and') for line in self.writes]), except_names(self.writes),
                            'a text that leaves one out fails')
        limit = 2 * header_number(r'SP11_STICK_GIC_TIME_BOX_S (\d+)u') + 2 * (header_number(r'#define SG_CYCLES (\d+)u') + 1) * header_number(r'SP11_STICK_GIC_CYCLE_US (\d+)u') / 1e6
        self.assertEqual([p for run in self.runs.values() for p in window_1_problems(run, limit + 0.02)], [])
        self.assertTrue([p for run in self.runs.values() for p in window_1_problems(run, 1.0)], 'window 1 that sees no interrupt lasts longer than 1 s')
        flat_writes = ' '.join(' '.join(self.writes).split())
        self.assertIn('only inside window 1, which ends at 4.08 s at the latest', flat_writes)
        self.assertIn('ICC_EOIR1_EL1 (ICC_DIR_EL1 when EOImode is set) is written to end the interrupt that was acknowledged, and is neither read nor put back; SPSR_EL2 and ELR_EL2 '
                      'are read for the page (the entry\'s values) and set for the return, and are not put back', flat_writes)

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
        census = final_census()
        for name in ASSEMBLY:
            self.assertIn(name, census['msr'], name)
            self.assertEqual(set(census['msr'][name]), ASSEMBLY_FUNCTIONS, name)

    def test_the_handlers_read_the_four_registers_the_text_names_for_them(self):
        names = ['ESR_EL2', 'ELR_EL2', 'SPSR_EL2', 'FAR_EL2']
        for name in names:
            self.assertIn('sw_gic_vec_common', final_census()['mrs'][name], name)
        clause = ' '.join(' '.join(gq.EL2_READS).split()).split('in the handlers ', 1)[1].split(' and ICC_IAR1_EL1', 1)[0]
        self.assertEqual(re.findall(r'[A-Z][A-Z0-9_]*_EL2', clause), names)
        self.assertNotEqual(re.findall(r'[A-Z][A-Z0-9_]*_EL2', clause.replace('FAR_EL2', '')), names, 'a text that leaves one out fails')

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


class Source(unittest.TestCase):
    """The sentences of the text that describe the stage's source and the documents, and the commit of the note: no model and no toolchain."""

    @classmethod
    def setUpClass(cls):
        cls.new, cls.old, cls.other = (' '.join(rec.record_text({**FIXED, **over}).split()) for over in ({}, dict(gic_quarantine=False), dict(gic_stop=False, gic_quarantine=False)))

    def test_the_heading_of_section_2_and_the_last_bullet_of_section_5_are_this_kind_s_and_the_other_kinds_keep_theirs(self):
        heading, spinning = '2. Placement (the one range the run may store to)', 'A spinning guest is ended by the operator\'s power cycle'
        for other in (self.old, self.other):
            self.assertIn(heading, other)
            self.assertIn(spinning, other)
            self.assertNotIn('REPORT.DAT (outside the placement)', other)
        self.assertNotIn(heading, self.new)
        self.assertNotIn('spinning guest', self.new)
        for says in (gq.PLACEMENT_HEADER, 'A hung window or stub is ended by the operator\'s power cycle, which writes nothing.',
                     'ram: the placement (the copy of the chunks, zero fills), REPORT.DAT (outside the placement) and the wrapper\'s stack, as the dry-run wrapper'):
            self.assertIn(says, self.new)
        self.assertIn('REPORT.DAT is outside the placement by `PLACE_DECLARED` and `PLACE_NOT_CONV`', ' '.join((ROOT / 'docs/SP11-STICK-BUNDLE.md').read_text().split()))

    def test_a_window_verdict_stops_the_series_in_the_documents_words_and_no_other_kind_says_it(self):
        bullet = ('After any window verdict (OUTCOME 0X20 to 0X2F, IRQ_OK 0X20 included; no REFUSED line follows it) stop: the next work is the M1 stop design (D2), not a retry '
                  '(docs/SP11-STICK-GIC.md section 11).')
        self.assertIn(bullet, self.new)
        self.assertNotIn('window verdict', self.old)
        self.assertIn('the next work is the M1 stop design (D2), not a retry', ' '.join((ROOT / 'docs/SP11-STICK-GIC.md').read_text().split()))
        self.assertIn('return SG_OUT_IS_VERDICT(out) ? 0 : sw_refuse(g->c, SP11SB_E_GIC_NOT_READY, out);', (ROOT / 'probes/sp11-stick-gic-dry.c').read_text())

    def test_the_stage_returns_counter_where_the_text_says_and_writes_whole_registers_only_where_it_says(self):
        gic, dry = [(ROOT / 'probes' / name).read_text() for name in ('sp11-stick-gic.c', 'sp11-stick-gic-dry.c')]
        self.assertEqual(counter_problems(gic, dry), [])
        self.assertEqual(len(counter_problems(gic, dry.replace('return SG_OUT_COUNTER;', 'return 0;'))), 2, 'a second wait that returns nothing, and one place fewer')
        self.assertEqual(counter_problems(gic + ' SG_OUT_COUNTER', dry), ['another place returns COUNTER'])
        for needle in ('if (g->ds && !(grp->old & g->bit)) {', 'grp->new = grp->old | g->bit;', 'if (cfg->old & edge) {', 'cfg->new = cfg->old & ~edge;'):
            self.assertIn(needle, gic)
        for needle in ('sp11hw_write_icc_eoir1_el1(iar);', 'if (rec->icc_ctlr_el1 & SG_ICC_CTLR_EOIMODE) sp11hw_write_icc_dir_el1(iar);', 'if ((iar & 0xFFFFFF) >= SW_GIC_INTID_SPURIOUS) return;'):
            self.assertIn(needle, dry)

    def test_the_commit_of_the_note_is_the_tree_of_the_pair_s_sources_and_a_file_that_changed_since_is_not(self):
        commit = re.search(r"^COMMIT = '([0-9a-f]{40})'", (TOOLS / 'test-sp11-stick-bundle-record-gic.py').read_text(), re.M).group(1)     # the one the note's line is held to
        files = pair_sources()
        self.assertGreaterEqual(len(files), 12)
        self.assertEqual({'probes/sp11-stick-*' if f.startswith('probes/sp11-stick-') else f for f in files},
                         {'probes/sp11-stick-*', 'tools/sp11_stick_wrapper_build.py', 'config/monitor-profile-sp11-m1.json'}, 'what the note names as the sources of the payloads')
        self.assertEqual(source_problems(commit, files), [])
        self.assertEqual(source_problems(commit, [*files, 'tools/sp11_stick_bundle_record_gic.py']), ['tools/sp11_stick_bundle_record_gic.py'])

    def test_the_q1n1_files_the_note_names_are_inputs_of_the_payloads_in_the_build_tool(self):
        named = ('Makefile', 'start.S', 'payload.ld', 'payload-util.h', 'sha256.c')
        self.assertEqual([n for n in named if not any(i.endswith('/' + n) for i in wb.Q1N1_INPUTS)], [])
        self.assertEqual(len(wb.Q1N1_INPUTS), 10, 'the ten INPUT lines of the build report: the note names five of them')
        self.assertTrue([n for n in ('Makefile.in', 'start.c') if not any(i.endswith('/' + n) for i in wb.Q1N1_INPUTS)], 'a file that is no input is found out')

    def test_the_silence_rule_is_for_any_point_of_the_page_and_the_documents_say_it_the_same_way(self):
        rule = 'No page change for 15 s at any point, a NEXT line or not (docs/SP11-STICK-GIC.md section 11): note the last line and power cycle.'
        section = self.new.split(' 5. Refusal and stop rules', 1)[1]
        self.assertIn(rule, section)
        self.assertIn('freeze on a NEXT line or no page change for 15 s', ' '.join((ROOT / 'docs/SP11-STICK-GIC.md').read_text().split()))
        self.assertNotIn('ENTER', section, 'no ENTER line: the wrapper opens with SNAPMON STAGE=ENTERED')
        self.assertEqual([f for f in pair_sources() if f.endswith('.c') and re.search(r'"ENTER\b', (ROOT / f).read_text())], [])
        for name in ('SP11-STICK-BUILD.md', 'SP11-STICK-GIC.md'):
            doc = ' '.join((ROOT / 'docs' / name).read_text().split())
            self.assertIn('15 s at any point, a NEXT line or not', doc)
            self.assertNotIn('after a NEXT line or ENTER', doc)

    def test_the_boot_attributions_of_the_reads_are_the_documents_for_both_boots_and_igrpen1_is_boot_13_only(self):
        doc = ' '.join((ROOT / 'docs/SP11-STICK-GIC.md').read_text().split())
        boot12, boot13 = doc.split('Boot 12 (OBSERVED', 1)[1].split('Boot 13 (OBSERVED', 1)[0], doc.split('Boot 13 (OBSERVED', 1)[1].split('That is pass 5', 1)[0]
        for paragraph in (boot12, boot13):
            self.assertRegex(paragraph, r'GICD_CTLR (read )?0x10')
            self.assertRegex(paragraph, r'ICFGR1 0 \((every PPI )?level\)')
        self.assertIn('The CPU interface and the timer were never read in that boot', boot12)
        self.assertRegex(boot13, r'BPR1 3, IGRPEN1 0')
        for says in ('(GICD_CTLR 0x10: OBSERVED, boots 12 and 13; ICC_IGRPEN1_EL1 0: OBSERVED, boot 13)', '(neither on this unit\'s reads: DS 0 and ICFGR1 level, OBSERVED in boots 12 and 13)'):
            self.assertIn(says, self.new)
        for old in ('(GICD_CTLR 0x10, ICC_IGRPEN1_EL1 0: OBSERVED, boot 13)', 'in boot 13: DS 0, ICFGR1 level', 'ICC_IGRPEN1_EL1 0: OBSERVED, boots 12'):
            self.assertNotIn(old, self.new)

    def test_the_paragraph_that_says_what_the_stage_never_touches_is_as_it_must_be(self):
        says = ('Never the ITS, SMMU, PCIe, USB or the framebuffer. No SPI register is written (the SPIs are only read) and no other redistributor (only its GICR_TYPER is read). '
                'Of the interrupts but PPI 26 only the enable bits that Q cleared are written, by Q and its put-back (SGI 0 and PPIs 23, 25, 27 and 30 where ISENABLER0 read '
                '0x4E800001): no priority or pending state of any of them is written, and IGROUPR0 and ICFGR1 are written whole, the other bits as read, and only in the cases of R '
                'above (neither on this unit\'s reads: DS 0 and ICFGR1 level, OBSERVED in boots 12 and 13). But a window takes whatever INTID arrives: ICC_IAR1_EL1 acknowledges it, '
                'ICC_EOIR1_EL1 ends it (ICC_DIR_EL1 too when EOImode is set; it was in boot 13), a foreign SPI included (INFERRED from the architecture: a level source that still '
                'asserts is pending again, an edge or message interrupt is consumed). GICD_CTLR EnableGrp1A is distributor-wide: while the stage holds it set, any enabled group 1 '
                'interrupt, an SPI included, can be signalled to this CPU. PPI 26\'s own are the stage\'s: it sets them as the writes above say, and undoes them.')
        was = ('another interrupt\'s configuration', 'No SPI is written', 'no priority, group, trigger or pending state', 'group 1 is enabled for every interrupt')
        problems = lambda text: [] if says in text and not [old for old in was if old in text] else ['wrong']       # noqa: E731
        self.assertEqual(problems(self.new), [])
        self.assertTrue(all(problems(f'{self.new} {old}') for old in was), 'a wording of the earlier versions that came back fails')
        self.assertTrue(problems(self.new.replace('an SPI included, can be signalled', 'an SPI included, is signalled')))
        self.assertEqual([i for i in range(32) if 0x4A800001 >> i & 1], [0, 23, 25, 27, 30], 'SGI 0 and PPIs 23, 25, 27 and 30 are the bits of the value Q writes')


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
