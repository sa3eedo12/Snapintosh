#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of the one case in which the arm half's put-back does not make its last write, as the monitor entry of SNAPMON-M1.BIN meets it (monitor port work item
4b, Phase E part 2; docs/SP11-STICK-MONITOR.md section 16). Pass 6 of the GIC stage (probes/sp11-stick-gic.c, gic_both_gates_open) withholds the write that enables
again the interrupts that the quarantine cleared (ISENABLER0) when the put-backs of BOTH group 1 gates, ICC_IGRPEN1_EL1 and GICD_CTLR, failed: ISENABLER0 is then
only read and named in FAILED, those interrupts stay disabled and the run ends NOT_RESTORED (48, OUTCOME=0X30). One gate stuck and the other closed is not that case
(the write is made, inert behind the closed gate), and a gate found open is not counted as failed. Here: (a) a refused arm (FOREIGN_PENDING, INTID 40) whose put-back
leaves both gates open, and the same after the call; (b) one gate stuck, either, and the gate found open; (c) the M1 record's gic text, in particular its sentence of
pass 6, compared claim by claim with what those runs show. Every check is shown to fail on a MUTANT of the real stage (a text patch made in memory in a scratch copy of
probes/sp11-stick-gic.c: `patches` of `harness()` in tools/sp11_stick_monitor_testlib.py) and on a faulty text: the stage without the rule, with the rule widened to either
gate, with the withheld write not named, and with a gate found open counted; the text without its sentence, with the write claimed unconditional, with 'either' for 'both'.

    python3 tools/test-sp11-stick-monitor-gates.py
    Q1N1_SP11_TREE=/nonexistent python3 tools/test-sp11-stick-monitor-gates.py      (the harness tests SKIP; the text tests run)

The code under test is the REAL wrapper, GIC arm and put-back, monitor stage and reporter in the harness of tools/test-sp11-stick-monitor.c under AddressSanitizer and
UndefinedBehaviorSanitizer, over the model of the GIC of tools/test-sp11-stick-gic.c: what it proves is the stage's order, bounds, put-back and report against the
model, and nothing about the unit's GIC (the rule has never run on the unit). The expected values are written from docs/SP11-STICK-GIC.md section 4 and from the pass 6
commit message, not from the C. No hardware, nothing under /Volumes, no network; everything is written below build/sp11-wrapper-test-scratch-monitor-gates/, deleted at
the end. A SKIP is never a PASS. EXPERIMENTAL; hardware NOT RUN."""
import concurrent.futures
import re
import shutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))
import sp11_stick_bundle_record_m1 as m1t  # noqa: E402
import sp11_stick_gic_testlib as G  # noqa: E402
import sp11_stick_monitor_testlib as M  # noqa: E402
import sp11_stick_wrapper_testlib as T  # noqa: E402

ARM = T.load('sp11_monitor_arm_tests', TOOLS / 'test-sp11-stick-monitor-arm.py')          # its readers of the text and its checks of a refused arm
MON = ARM.MON
FIX, RUNS, MUTANTS = MON.FIX, {}, {}
FOUND, Q13, PPI26 = 0x4E800001, ARM.Q13, 1 << 26          # ISENABLER0 as boot 12 and 13 read it, the bits the quarantine clears, PPI 26's own bit
GICD, IGRPEN1, ISEN = G.REG_BIT['GICD_CTLR'], G.REG_BIT['ICC_IGRPEN1'], G.REG_BIT['ISENABLER0']
BOTH_0 = ['NOHOLD GICD_CTLR 0', 'NOHOLD ICC_IGRPEN1_EL1 0']      # the two group 1 gates do not go back, from the start of the put-back: after a refusal and after the call
UNIT13, FOREIGN_SPI = ARM.UNIT13, ARM.FOREIGN_SPI
# scenario -> the GIC model's lines; `refuse-*` end in a refusal of the arm (INTID 40 enabled and pending: what the quarantine does not cover), `called-*` call the monitor
CASES = {
    'nominal': dict(gic=UNIT13),
    'refuse-foreign': dict(gic=[*UNIT13, *FOREIGN_SPI]),
    'refuse-gates-both': dict(gic=[*UNIT13, *FOREIGN_SPI, *BOTH_0]),
    'refuse-gate-igrpen1': dict(gic=[*UNIT13, *FOREIGN_SPI, 'NOHOLD ICC_IGRPEN1_EL1 0']),
    'refuse-gate-gicd': dict(gic=[*UNIT13, *FOREIGN_SPI, 'NOHOLD GICD_CTLR 0']),
    'refuse-gate-found-open': dict(gic=[*UNIT13, 'CTLR 0x12', *FOREIGN_SPI, 'NOHOLD ICC_IGRPEN1_EL1 0']),       # EnableGrp1A found set: class G is never begun
    'called-gates-both': dict(gic=[*UNIT13, *BOTH_0]),
    'called-gate-igrpen1': dict(gic=[*UNIT13, 'NOHOLD ICC_IGRPEN1_EL1 0']),
    'called-gate-gicd': dict(gic=[*UNIT13, 'NOHOLD GICD_CTLR 0']),
}
GIC_C = 'probes/sp11-stick-gic.c'
_RULE = 'return (r->restore_failed & gates) == gates;'
_FINAL_WRITE = 'if (r->begun & SG_CLASS_R) gic_put_back_bits(g, SG_R_ISENABLER0, gic_mask(g, SG_R_ISENABLER0), 1, !gic_both_gates_open(r));'
# mutant -> the patch of the real stage; each must be shown by the runs below and killed by the check that matches it
PATCHES = {
    'gates-rule-removed': (GIC_C, '!gic_both_gates_open(r)', '((void)gic_both_gates_open(r), 1)'),
    'gates-either': (GIC_C, _RULE, 'return (r->restore_failed & gates) != 0;'),
    'gates-not-reported': (GIC_C, _FINAL_WRITE, 'if ((r->begun & SG_CLASS_R) && !gic_both_gates_open(r)) sw_gic_put_back_reg(g, SG_R_ISENABLER0);'),
    'found-open-counted': (GIC_C, _RULE, 'return (r->restore_failed & gates) == gates || ((r->reg[SG_R_GICD_CTLR].old & SG_GICD_CTLR_G1A) && (r->restore_failed & (1u << SG_R_ICC_IGRPEN1)));'),
}


# ---- the harness ---------------------------------------------------------------------------------------------------------------------------
def exe_of(variant='base'):
    return M.harness(FIX['tree']) if variant == 'base' else M.harness(FIX['tree'], variant, patches=(PATCHES[variant],))


def batch(variant='base'):
    texts, worlds = [], {}
    for name, case in CASES.items():
        worlds[name] = M.world(FIX['bundle'], FIX['scratch'])
        texts.append((name, G.scenario(name, worlds[name], case['gic'], ARM.BOTH, ())))
    out = M.run(exe_of(variant), texts, {})
    runs = {name: MON.Run(name, worlds[name], out, ARM.BOTH) for name in CASES}
    for name, run in runs.items():
        run.reads, run.reads_at = out.reads[name], out.reads_at[name]
    return runs


def setUpModule():
    M.use_scratch('monitor-gates')
    shutil.rmtree(T.BUILD, ignore_errors=True)
    FIX['scratch'] = T.BUILD / 'host'
    FIX['scratch'].mkdir(parents=True)
    FIX['tree'] = T.q1n1_tree()
    FIX['fixtures'] = T.Fixtures(FIX['scratch'] / 'fixtures')
    FIX['bundle'] = FIX['fixtures'].bundle('gic', T.LOW, stores='ram,el2_sysreg,gic', extra=('--gic-stop',))
    if MON.runnable():
        with concurrent.futures.ThreadPoolExecutor(3) as pool:
            for future in [pool.submit(exe_of, v) for v in ('base', *PATCHES)]:
                future.result()
        RUNS.update(batch())
        for variant in PATCHES:
            MUTANTS[variant] = batch(variant)


def tearDownModule():
    shutil.rmtree(T.BUILD, ignore_errors=True)


# ---- what a run is held to; each returns its violations, so that a mutant can be shown to break the one that matches its fault ---------------------------------------
def writes_of(run, register):
    return [v for n, v in run.g.writes() if n == register]


def put_back_reads(run, register='GICR_ISENABLER0'):
    """How many times the put-back read `register`: the harness's count at the end of the run (the refusal's point, or the final one) less its count at the PUTBACK mark."""
    end = run.reads_at.get('REFUSED') or run.reads_at['FINAL']
    return end.get(register, 0) - run.reads_at['PUTBACK'].get(register, 0)


def withheld(run):
    """Both group 1 gates are left open by their put-backs: ISENABLER0 is NOT written (the put-back only reads it) and is named in FAILED with both gates, the
    quarantined interrupts stay disabled, both gates are still open, and the GIC record says NOT_RESTORED."""
    r, g, found = run.g.r(), run.g, []
    if writes_of(run, 'GICR_ISENABLER0'):
        found.append(f'ISENABLER0 was written {[hex(v) for v in writes_of(run, "GICR_ISENABLER0")]} behind two open gates')
    if put_back_reads(run) < 1:
        found.append('the put-back did not read ISENABLER0')
    if (r.outcome, r.restore_failed) != (G.OUT.NOT_RESTORED, GICD | IGRPEN1 | ISEN):
        found.append(f'{G.OUTCOMES[r.outcome]} with FAILED {r.restore_failed:#x}, expected NOT_RESTORED and {GICD | IGRPEN1 | ISEN:#x}')
    if g.state['isenabler0'] != (FOUND, PPI26):
        found.append(f'ISENABLER0 {[hex(v) for v in g.state["isenabler0"]]}, expected {FOUND:#x} then {PPI26:#x}: the quarantined bits stay clear')
    if g.state['igrpen1'][1] != 1 or not g.state['gicd_ctlr'][1] & 2:
        found.append(f'a gate was closed: ICC_IGRPEN1_EL1 {g.state["igrpen1"]}, GICD_CTLR {[hex(v) for v in g.state["gicd_ctlr"]]}')
    return found


def made(run, gate):
    """One gate left open and the other closed (or found open): the write is made, once, as the last write of the run, ISENABLER0 ends as found, and only `gate` is
    in FAILED."""
    r, g, found = run.g.r(), run.g, []
    if writes_of(run, 'GICR_ISENABLER0') != [Q13]:
        found.append(f'the writes of ISENABLER0 are {[hex(v) for v in writes_of(run, "GICR_ISENABLER0")]}, expected [{Q13:#x}]')
    if g.writes()[-1:] != [('GICR_ISENABLER0', Q13)]:
        found.append(f'the last write is {g.writes()[-1:]}, not the quarantine\'s enables')
    if (r.outcome, r.restore_failed) != (G.OUT.NOT_RESTORED, gate):
        found.append(f'{G.OUTCOMES[r.outcome]} with FAILED {r.restore_failed:#x}, expected NOT_RESTORED and only {gate:#x}')
    if g.state['isenabler0'] != (FOUND, FOUND):
        found.append(f'ISENABLER0 {[hex(v) for v in g.state["isenabler0"]]}, expected back as found')
    return found


def made_clean(run):
    """With no gate stuck the write is made as the last write and nothing is named in FAILED."""
    r, g = run.g.r(), run.g
    found = [] if g.writes()[-1:] == [('GICR_ISENABLER0', Q13)] and writes_of(run, 'GICR_ISENABLER0') == [Q13] else [f'the writes are {g.writes()[-2:]}']
    return found + ([] if (r.restore_failed, g.state['isenabler0']) == (0, (FOUND, FOUND)) else [f'FAILED {r.restore_failed:#x}, ISENABLER0 {g.state["isenabler0"]}'])


def found_open_made(run):
    """A gate found open is not a failed put-back: GICD_CTLR found with EnableGrp1A set (class G never begun, never written) and ICC_IGRPEN1_EL1 stuck open: the write
    is made behind two open gates, the limit that docs/SP11-STICK-GIC.md section 4 states."""
    r, g = run.g.r(), run.g
    found = [f'class G was begun ({G.classes(r.begun)}) or GICD_CTLR written'] if 'G' in G.classes(r.begun) or writes_of(run, 'GICD_CTLR') else []
    found += [] if g.state['igrpen1'][1] == 1 and g.state['gicd_ctlr'][1] & 2 else ['a gate is closed: the run does not show the limit']
    return found + made(run, IGRPEN1)


def refused(run):
    """The arm refused FOREIGN_PENDING and its put-back did not hold: before the call, through mon_arm_failed, DETAIL 0X30 (NOT_RESTORED), FIRST the refusal, the page
    at 29 of 30 lines in the order of the lines the text names."""
    r, texts = run.g.r(), run.mon.texts()
    found = ARM.refused_by_the_arm(run, G.OUT.NOT_RESTORED) + ARM.through_mon_arm_failed(run)
    found += [] if (r.first_outcome, r.outcome) == (G.OUT.FOREIGN_PENDING, G.OUT.NOT_RESTORED) else [f'FIRST {r.first_outcome}, outcome {r.outcome}']
    found += [] if len(run.mon.lines) == 29 else [f'the page has {len(run.mon.lines)} lines, not 29']
    at = [next((i for i, t in enumerate(texts) if t.startswith('SNAPMON ' + prefix)), -1) for prefix in ARM.PAGE_ORDER]
    return found + ([] if -1 not in at and sorted(at) == at else [f'the lines of the text are not on the page in order: {at}'])


def refused_ok(run):
    """A refusal whose put-back held: FOREIGN_PENDING as the detail, before the call, every register as found."""
    return ARM.refused_by_the_arm(run, G.OUT.FOREIGN_PENDING) + ARM.as_found(run) + ARM.through_mon_arm_failed(run)


def called(run):
    """The monitor was called and returned, the put-back failed after the call, which is no refusal: the stage ends DONE with GIC_NOT_RESTORED in the monitor's flags."""
    head, rec = MON.head_of(run), run.rec
    found = [] if (head['refusal'], ARM.calls(run)) == (0, 1) else [f'refusal {head["refusal"]}, {ARM.calls(run)} call(s)']
    found += [] if rec and rec.flags & M.FLAGS['GIC_NOT_RESTORED'] and rec.outcome == M.OUT.RETURNED else ['the monitor record lacks GIC_NOT_RESTORED or RETURNED']
    return found + MON.Held.common(run) + MON.GicRecord.agree(run)


# ---- the text, read as the comparison reads it ---------------------------------------------------------------------------------------------
def parts_of(lines):
    """The gic class as {register: the flat text of its W or R line and its continuation lines} and 'all': the whole class as one line of words."""
    parts, name = {'all': ARM.flat(lines)}, None
    for line in lines:
        match = ARM.ENTRY.match(line)
        if match:
            name = match.group(2).removesuffix('[n]')
            parts[name] = ''
        elif not line.startswith(' ' * 14):
            name = None
        if name:
            parts[name] += ' ' + line.strip()
    return {k: ' '.join(v.split()) for k, v in parts.items()}


def claims(parts):
    """What the text says about the last write of the put-back: {claim: bool}. The phrases are the ones of tools/sp11_stick_bundle_record_m1.py."""
    isen, pend, everything = parts['GICR_ISENABLER0'], parts['GICR_ICPENDR0'], parts['all']
    named = re.search(r'only read, and named in FAILED \(its bit (\d+), 0X([0-9A-F]+)\)', isen)
    return {
        'unconditional': 'the last register write of the whole run:' in isen,
        'points to an exception': 'the last register write of the whole run, but for the exception that follows:' in isen,
        'not made when both': bool(re.search(r'that write is NOT made when the put-backs of both ICC_IGRPEN1_EL1 and GICD_CTLR failed', isen)),
        'named in FAILED': bool(named) and (1 << int(named.group(1)), int(named.group(2), 16)) == (ISEN, ISEN),
        'ends not restored': bool(re.search(r'and the run ends NOT_RESTORED \(48, OUTCOME=0X30\)', isen)),
        'one gate: made': 'One gate stuck and the other closed is not that case: the write is made' in isen,
        'found open: made': bool(re.search(r'A gate found open is not counted as failed: .*the write is made although both gates are open', isen)),
        'after a refusal and after the call': 'The rule holds after a refusal and after the call alike' in isen,
        'icpendr0 excepts it': 'except in the both-gates case above, enabled again' in pend,
        'quarantine outlasts': 'In the both-gates case above there is no such write, and the quarantine is still on when the run ends' in everything,
    }


def facts(runs):
    """What the model shows, as the names of the runs of each kind: {kind: [name]}. A run counts only when the quarantine wrote ICENABLER0 (else there is nothing to
    enable again)."""
    kinds = {k: [] for k in ('both skipped', 'both written', 'both named', 'both not named', 'one written', 'one skipped', 'found open written', 'found open skipped',
                             'left on', 'refusal', 'called')}
    for name, run in runs.items():
        r, g = run.g.r(), run.g
        if Q13 not in writes_of(run, 'GICR_ICENABLER0'):
            continue
        gates, written = r.restore_failed & (GICD | IGRPEN1), bool(writes_of(run, 'GICR_ISENABLER0'))
        if gates == GICD | IGRPEN1:
            kinds['both written' if written else 'both skipped'].append(name)
            kinds['both named' if r.restore_failed & ISEN else 'both not named'].append(name)
            if g.state['isenabler0'][1] != FOUND:
                kinds['left on'].append(name)
            kinds['called' if ARM.calls(run) else 'refusal'].append(name)
        elif gates == IGRPEN1 and 'G' not in G.classes(r.begun) and g.state['gicd_ctlr'][0] & 2:
            kinds['found open written' if written else 'found open skipped'].append(name)
        elif gates:
            kinds['one written' if written else 'one skipped'].append(name)
    return kinds


def exception_problems(parts, runs):
    """The text's claims about the write that the put-back withholds, against what the runs of the model show: [str]. Empty for the real text and the real stage."""
    said, shown = claims(parts), facts(runs)
    found = []
    if said['unconditional']:
        found.append('the text says that the quarantine\'s enables are the last register write of the whole run, with no exception')
    if said['points to an exception'] and not said['not made when both']:
        found.append('the text points to an exception that follows, and none follows')
    if shown['both skipped'] and not said['not made when both']:
        found.append(f'runs {shown["both skipped"]} leave ISENABLER0 unwritten behind two open gates and the text does not say that the write is NOT made then')
    if said['not made when both'] and not shown['both skipped']:
        found.append('the text says that the write is NOT made when both gates failed, and no run shows it')
    if said['not made when both'] and shown['both written']:
        found.append(f'the text says that the write is NOT made when both gates failed, and runs {shown["both written"]} make it')
    if said['named in FAILED'] and shown['both not named']:
        found.append(f'the text says that ISENABLER0 is named in FAILED, and runs {shown["both not named"]} do not name it')
    if shown['both skipped'] and not said['named in FAILED']:
        found.append('the text does not say that ISENABLER0 is named in FAILED (with its bit) when the write is withheld')
    if said['ends not restored'] != bool(shown['both skipped']):
        found.append('the text and the model disagree on whether a withheld write ends the run NOT_RESTORED')
    if shown['one written'] and not said['one gate: made']:
        found.append(f'runs {shown["one written"]} make the write with one gate stuck and the text does not say so')
    if said['one gate: made'] and (shown['one skipped'] or not shown['one written']):
        found.append(f'the text says that the write is made with one gate stuck, and the runs show it skipped {shown["one skipped"]} or not at all')
    if shown['found open written'] and not said['found open: made']:
        found.append(f'runs {shown["found open written"]} make the write with a gate found open and the text does not say so')
    if said['found open: made'] and (shown['found open skipped'] or not shown['found open written']):
        found.append(f'the text says that a gate found open is not counted as failed, and the runs show the write skipped {shown["found open skipped"]} or not at all')
    if shown['both skipped'] and not said['icpendr0 excepts it']:
        found.append('the text says that a source that asserts through the call is enabled again when the run ends, and runs ' + f'{shown["left on"]} leave it disabled')
    if said['quarantine outlasts'] != bool(shown['left on']):
        found.append('the text and the model disagree on whether the quarantine is still on when the run ends')
    if said['after a refusal and after the call'] and not (shown['refusal'] and shown['called']):
        found.append('the text says that the rule holds after a refusal and after the call, and the runs do not show both')
    return found


def edited(parts, register, old, new):
    """`parts` with `old` replaced by `new` in the entry of `register` ('all' for the class as a whole): the phrase must be there, so that a text whose words moved fails loudly."""
    assert old in parts[register], f'{old!r} is not in the text of {register}'
    return {**parts, register: parts[register].replace(old, new)}


# ---- the text ------------------------------------------------------------------------------------------------------------------------------
class Text(unittest.TestCase):
    """The sentence of pass 6 and the words around it, in the record of the monitor entry run (no harness)."""

    @classmethod
    def setUpClass(cls):
        cls.text = ARM.render()
        cls.lines = ARM.block(cls.text)
        cls.parts = parts_of(cls.lines)

    def test_the_place_of_pass_6_holds_the_sentence_after_the_write_it_qualifies_and_before_class_c(self):
        self.assertTrue(m1t.PASS6_LINES, 'the place is no longer empty')
        self.assertEqual(m1t.GIC_LINES, m1t.gic_lines(pass6=m1t.PASS6_LINES))
        plain = m1t.gic_lines(pass6=())
        at = m1t.GIC_LINES.index(m1t.PASS6_LINES[0])
        self.assertEqual(m1t.GIC_LINES[at:at + len(m1t.PASS6_LINES)], m1t.PASS6_LINES)
        self.assertEqual((*m1t.GIC_LINES[:at], *m1t.GIC_LINES[at + len(m1t.PASS6_LINES):]), plain, 'nothing else moves when the sentence is taken out')
        self.assertTrue(re.search(r'NOT_RESTORED \(48, OUTCOME=0X30\)\.$', ' '.join(m1t.GIC_LINES[:at])), 'it follows the lines of the write')
        self.assertTrue(m1t.GIC_LINES[at + len(m1t.PASS6_LINES)].startswith('  W ICC_PMR_EL1'), 'and comes before the class C registers')
        self.assertTrue(all(line.startswith(' ' * 8) for line in m1t.PASS6_LINES), 'a continuation of the ISENABLER0 entry')
        self.assertLessEqual(max(map(len, self.lines)), 120)

    def test_the_sentence_says_what_the_stage_does_in_the_words_a_reader_looks_for(self):
        isen = self.parts['GICR_ISENABLER0']
        for needle in ('that write is NOT made when the put-backs of both ICC_IGRPEN1_EL1 and GICD_CTLR failed', 'which leaves both group 1 gates open', 'is then only read',
                       'named in FAILED (its bit 2, 0X4)', 'the bits Q cleared are still clear', 'they stay disabled when the run ends',
                       'and the run ends NOT_RESTORED (48, OUTCOME=0X30)', 'with FIRST naming the refusal when there was one',
                       'One gate stuck and the other closed is not that case: the write is made, inert behind the closed gate',
                       'A gate found open is not counted as failed', 'the write is made although both gates are open', 'The rule holds after a refusal and after the call alike',
                       'The reason (INFERRED)', 'EXPERIMENTAL: host model only, never run on the unit'):
            with self.subTest(needle):
                self.assertIn(needle, isen)

    def test_the_words_that_made_the_write_unconditional_are_gone_from_every_place_that_had_them(self):
        flat, whole = self.parts['all'], ' '.join(self.text.split())
        for gone in ('the last register write of the whole run:', 'and enabled again, when the run ends', 'always with a full put-back', 'after which everything written is put back and no monitor',
                     'as pass 5 left it', 'set again by the put-back.', 'that follows at once.'):
            with self.subTest(gone):
                self.assertNotIn(gone, whole)
        for needle in ('the last register write of the whole run, but for the exception that follows:', 'except in the both-gates case above, enabled again',
                       'after which what was written is put back, but for the one write that the exception under ISENABLER0 withholds',
                       'In the both-gates case above there is no such write, and the quarantine is still on when the run ends',
                       'put-back that follows at once, except in the both-gates case, in which that put-back does not lift it',
                       'the wrapper puts back what the arm wrote, but for the exception above', 'the both-gates case above is one, and GIC RESTORE then says CLEAN=NO',
                       'set again by the put-back (not in the both-gates case above)', 'as pass 6 left it'):
            with self.subTest(needle):
                self.assertIn(needle, flat)
        for needle in ('which is not made in the both-gates case above', 'the both-gates case, only in the host model', 'always followed by the put-back'):
            with self.subTest(needle):
                self.assertIn(needle, whole, 'the facts block, which follows the classes')

    def test_the_facts_block_still_says_that_neither_the_quarantine_nor_the_check_has_run_on_the_unit(self):
        self.assertIn('Neither the quarantine nor the check has run on the unit (hardware NOT RUN)', ' '.join(self.text.split()))
        self.assertNotIn('VERIFIED', self.text)

    def test_the_bit_the_sentence_names_for_isenabler0_is_the_models(self):
        self.assertEqual((ISEN, G.h(GICD | IGRPEN1 | ISEN)), (4, '0X205'))


# ---- the runs ------------------------------------------------------------------------------------------------------------------------------
class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not MON.runnable():
            raise unittest.SkipTest('no clang or no q1n1 checkout (Q1N1_SP11_TREE): the harness is not built')
        cls.parts = parts_of(ARM.block(ARM.render()))


class Withheld(Base):
    """(a) Both group 1 gates left open: the last write is not made, after a refusal and after the call."""

    def test_a_refused_arm_whose_put_back_leaves_both_gates_open_does_not_write_isenabler0_and_ends_not_restored_before_the_call(self):
        run = RUNS['refuse-gates-both']
        self.assertEqual(refused(run) + withheld(run), [])
        self.assertEqual(run.g.nviol, 0, run.g.viol)
        self.assertEqual(writes_of(run, 'GICR_ICENABLER0'), [Q13], 'the quarantine was written once, and is the only write of either enable register')
        self.assertEqual(run.g.writes()[-1], ('GICD_CTLR', 0x10), 'the last write of the run is the distributor\'s retry')
        self.assertEqual(ARM.calls(run), 0, 'the monitor is not called')
        self.assertEqual(MON.head_of(run)['refusal_detail'], G.OUT.NOT_RESTORED)
        self.assertEqual(ARM.page_has(run, [f'SNAPMON GIC QUAR MASK={G.h(Q13)} DONE={G.h(PPI26)} HPPIR=40', 'SNAPMON GIC RESTORE CLEAN=NO FAILED=0X205 RETRIED=0X201 BEGUN=GRC VBAR=NONE EL1=NONE',
                                            'SNAPMON GIC VERDICT=NOT_RESTORED FIRST=FOREIGN_PENDING OUTCOME=0X30 SUB=DONE', 'SNAPMON MON VERDICT=GIC_NOT_READY FLAGS=0X1 SUB=3',
                                            'SNAPMON REFUSED=GIC_NOT_READY STAGE=COPIED DETAIL=0X30']), [])
        left = [m.split()[0] for m in G.Checks.restored(run.g)]
        self.assertEqual(sorted(left), ['gicd_ctlr', 'igrpen1', 'isenabler0'], 'what is left changed: both gates and the quarantined enables')
        self.assertTrue(any(t.startswith('SNAPREP GIC RESTORE CLEAN=NO FAILED=0X205') for t in run.rep.texts()), 'the reporter reads the same record after a restart')

    def test_after_the_call_the_same_put_back_is_no_refusal_and_the_page_and_the_flags_say_so(self):
        run = RUNS['called-gates-both']
        self.assertEqual(called(run) + withheld(run), [])
        self.assertEqual(run.g.writes()[-1], ('GICD_CTLR', 0x10))
        self.assertEqual(run.mon.texts()[-3:], ['SNAPMON GIC RESTORE CLEAN=NO FAILED=0X205 RETRIED=0X201 BEGUN=GRC VBAR=NONE EL1=NONE', 'SNAPMON MON VERDICT=RETURNED FLAGS=0X8F SUB=7',
                                                  'SNAPMON STAGE=DONE RETURN=0'])
        self.assertEqual(run.rec.flags, 0x8F, 'IC_SYNC, CALLED, RETURNED, STACK_S2 and GIC_NOT_RESTORED')

    def test_with_no_gate_stuck_the_refused_arm_puts_everything_back_and_the_write_is_made(self):
        run = RUNS['refuse-foreign']
        self.assertEqual(made_clean(run), [], 'with no gate stuck the write is made')
        self.assertEqual(refused_ok(run), [])


class Made(Base):
    """(b) One gate stuck, either, and a gate found open: the write is made."""

    def test_with_one_gate_left_open_the_write_is_made_as_the_last_write_of_the_run_whichever_gate_it_is_and_whichever_way_the_run_ends(self):
        for name, gate in (('refuse-gate-igrpen1', IGRPEN1), ('refuse-gate-gicd', GICD), ('called-gate-igrpen1', IGRPEN1), ('called-gate-gicd', GICD)):
            with self.subTest(name):
                run = RUNS[name]
                self.assertEqual(made(run, gate), [])
                self.assertEqual(refused(run) if name.startswith('refuse') else called(run), [])
        self.assertEqual(made_clean(RUNS['nominal']) + made_clean(RUNS['refuse-foreign']), [], 'the controls: no gate stuck, the write is made')

    def test_a_gate_found_open_is_not_a_failed_put_back_so_the_write_is_made_behind_two_open_gates_the_limit_the_gic_document_states(self):
        run = RUNS['refuse-gate-found-open']
        self.assertEqual(found_open_made(run), [])
        self.assertEqual(refused(run), [])
        self.assertIn('SNAPMON NEXT GIC STORES AFTER 8 S G=0 R=1 C=1 T=0 V=0 E=0', run.mon.texts(), 'class G was not planned')

    def test_the_withheld_run_reads_the_register_one_time_less_than_the_run_that_writes_it(self):
        self.assertEqual((put_back_reads(RUNS['refuse-gates-both']), put_back_reads(RUNS['refuse-gate-igrpen1'])), (2, 3), 'no read-back when nothing was written')


# ---- the text against the model ------------------------------------------------------------------------------------------------------------
class Claims(Base):
    """(c) The record's claims about the withheld write against the runs, and the faults that the comparison must name."""

    def test_the_text_and_the_runs_of_the_real_stage_agree(self):
        self.assertEqual(exception_problems(self.parts, RUNS), [])
        shown = facts(RUNS)
        self.assertEqual((shown['both skipped'], shown['both written'], shown['one skipped'], shown['found open skipped']), (['refuse-gates-both', 'called-gates-both'], [], [], []))
        self.assertEqual(shown['one written'], ['refuse-gate-igrpen1', 'refuse-gate-gicd', 'called-gate-igrpen1', 'called-gate-gicd'])
        self.assertEqual(shown['found open written'], ['refuse-gate-found-open'])
        self.assertEqual((shown['refusal'], shown['called'], shown['left on']), (['refuse-gates-both'], ['called-gates-both'], ['refuse-gates-both', 'called-gates-both']))

    def test_the_text_without_its_sentence_is_refused_by_the_comparison(self):
        problems = exception_problems(parts_of([f'      {line}' if i else f'   gic: {line}' for i, line in enumerate(m1t.gic_lines(pass6=()))]), RUNS)
        self.assertTrue([p for p in problems if 'points to an exception that follows, and none follows' in p], problems)
        self.assertTrue([p for p in problems if 'does not say that the write is NOT made then' in p], problems)
        self.assertTrue([p for p in problems if 'does not say that ISENABLER0 is named in FAILED' in p], problems)

    def test_a_text_that_claims_the_write_unconditional_is_refused_by_the_comparison(self):
        unconditional = parts_of([f'      {line}' if i else f'   gic: {line}' for i, line in enumerate(m1t.gic_lines(pass6=()))])
        unconditional = edited(unconditional, 'GICR_ISENABLER0', 'the last register write of the whole run, but for the exception that follows:', 'the last register write of the whole run:')
        unconditional = edited(unconditional, 'GICR_ICPENDR0', 'except in the both-gates case above, enabled again', 'enabled again')
        problems = exception_problems(unconditional, RUNS)
        self.assertTrue([p for p in problems if 'with no exception' in p], problems)
        self.assertTrue([p for p in problems if 'does not say that the write is NOT made then' in p], problems)
        self.assertTrue([p for p in problems if 'is enabled again when the run ends' in p], problems)
        self.assertEqual(self.parts['GICR_ISENABLER0'].count('the last register write of the whole run'), 1)

    def test_each_edit_of_the_sentence_that_the_model_contradicts_is_named(self):
        for what, register, old, new, expected in (
                ('either gate', 'GICR_ISENABLER0', 'of both ICC_IGRPEN1_EL1 and GICD_CTLR failed', 'of either ICC_IGRPEN1_EL1 or GICD_CTLR failed', 'does not say that the write is NOT made then'),
                ('one gate also skips', 'GICR_ISENABLER0', 'is not that case: the write is made, inert behind the closed gate', 'is that case too: the write is not made', 'make the write with one gate stuck'),
                ('found open counted', 'GICR_ISENABLER0', 'A gate found open is not counted as failed', 'A gate found open is counted as failed', 'make the write with a gate found open'),
                ('another bit', 'GICR_ISENABLER0', '(its bit 2, 0X4)', '(its bit 3, 0X8)', 'does not say that ISENABLER0 is named in FAILED'),
                ('not restored dropped', 'GICR_ISENABLER0', 'and the run ends NOT_RESTORED (48, OUTCOME=0X30)', 'and the run ends clean', 'disagree on whether a withheld write ends the run NOT_RESTORED'),
                ('icpendr0 unconditional', 'GICR_ICPENDR0', 'except in the both-gates case above, enabled again', 'enabled again', 'leave it disabled'),
                ('quarantine lifted', 'all', 'the quarantine is still on when the run ends', 'the quarantine is lifted when the run ends', 'whether the quarantine is still on')):
            with self.subTest(what):
                problems = exception_problems(edited(self.parts, register, old, new), RUNS)
                self.assertTrue([p for p in problems if expected in p], problems)


class Mutants(Base):
    """Each mutant of the real stage fails the check that matches its fault on the run that shows it, and the real stage passes that check on the same run."""

    CHECKS = {
        'gates-rule-removed': (('refuse-gates-both', 'called-gates-both'), withheld),
        'gates-either': (('refuse-gate-igrpen1', 'refuse-gate-gicd', 'called-gate-igrpen1', 'called-gate-gicd'),
                         lambda run: made(run, IGRPEN1 if 'igrpen1' in run.name else GICD)),
        'gates-not-reported': (('refuse-gates-both', 'called-gates-both'), withheld),
        'found-open-counted': (('refuse-gate-found-open',), found_open_made),
    }
    # the problem the comparison of the text names on the mutant's runs
    NAMED = {'gates-rule-removed': 'and runs [', 'gates-either': 'the runs show it skipped', 'gates-not-reported': 'and runs [',
             'found-open-counted': 'the runs show the write skipped'}

    def test_there_is_a_mutant_for_every_fault_and_every_scenario_it_uses_exists(self):
        self.assertEqual(set(PATCHES), set(self.CHECKS))
        for variant, (scenarios, check) in self.CHECKS.items():
            self.assertTrue(set(scenarios) <= set(CASES), variant)

    def test_each_mutant_breaks_the_check_that_matches_it_and_the_real_stage_passes_it(self):
        for variant, (scenarios, check) in self.CHECKS.items():
            for name in scenarios:
                with self.subTest(mutant=variant, scenario=name):
                    self.assertEqual(check(RUNS[name]), [], 'the real stage passes')
                    self.assertNotEqual(check(MUTANTS[variant][name]), [], f'{variant} passed the check that is meant to catch it')

    def test_each_mutant_is_also_a_stage_that_the_text_disagrees_with(self):
        self.assertEqual(exception_problems(self.parts, RUNS), [])
        for variant in PATCHES:
            with self.subTest(variant):
                problems = exception_problems(self.parts, MUTANTS[variant])
                self.assertTrue([p for p in problems if self.NAMED[variant] in p], problems)

    def test_the_mutants_are_the_real_stage_but_for_their_fault(self):
        mutant = MUTANTS['gates-rule-removed']['refuse-gates-both']
        self.assertEqual(writes_of(mutant, 'GICR_ISENABLER0'), [Q13], 'the unconditional write re-arms the pending interrupt behind two open gates')
        self.assertEqual(mutant.g.state['isenabler0'], (FOUND, FOUND))
        self.assertEqual(writes_of(MUTANTS['gates-either']['refuse-gate-igrpen1'], 'GICR_ISENABLER0'), [], 'widened to either gate the rule suppresses a write that was inert')
        mutant = MUTANTS['gates-not-reported']['refuse-gates-both']
        self.assertEqual((writes_of(mutant, 'GICR_ISENABLER0'), mutant.g.r().restore_failed & ISEN, mutant.g.r().restore_failed), ([], 0, GICD | IGRPEN1),
                         'suppressed and not named: the page would not say it')
        self.assertEqual(writes_of(MUTANTS['found-open-counted']['refuse-gate-found-open'], 'GICR_ISENABLER0'), [], 'a gate found open counted: the write is suppressed')
        for variant in PATCHES:
            with self.subTest(variant):
                self.assertEqual(MUTANTS[variant]['nominal'].g.writes(), RUNS['nominal'].g.writes(), 'with no gate stuck a mutant is the real stage')


class Hygiene(unittest.TestCase):
    def test_the_files_of_this_phase_are_ascii_with_an_spdx_line_within_the_size_limits(self):
        for rel in ('tools/test-sp11-stick-monitor-gates.py', 'tools/sp11_stick_monitor_testlib.py', 'tools/test-sp11-stick-monitor.c', 'tools/sp11_stick_bundle_record_m1.py'):
            data = (ROOT / rel).read_bytes()
            with self.subTest(rel):
                self.assertFalse([b for b in data if b > 127], 'ASCII only')
                self.assertIn(b'SPDX-License-Identifier: MIT', data[:400])
                self.assertLess(len(data), 51200)
                self.assertLess(data.count(b'\n'), 1500)


if __name__ == '__main__':
    unittest.main()
