#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of the arm half of the GIC stage as the monitor entry of SNAPMON-M1.BIN calls it (monitor port work item 4b, Phase E; docs/SP11-STICK-MONITOR.md
section 15). E1: the gic class of the M1 record (tools/sp11_stick_bundle_record_m1.py, GIC_LINES) against what the host model of the stage logs in its
nominal and its failing runs under the monitor entry's harness: every register that a run writes is a W line, every register that a run only reads is an
R line, and every W line is written by some run; the offsets, the outcome numbers and the lines of the page that the text names are the architecture's, the
stage's and the model's. E2: the arm refusals Q_NOT_HELD (22) and FOREIGN_PENDING (23), and the put-back that fails after them (NOT_RESTORED, 48), before
the monitor is called: through mon_arm_failed, with every register as found, the monitor never called and the page at 29 of its 30 lines. Every check has a
deliberately faulty case: a text that omits, mislabels or invents a register, names a wrong offset or outcome, and five seeded-bad stages (no quarantine, no
check of ICC_HPPIR1_EL1, a quarantine that is not put back, a refused arm that still calls the monitor, a refused arm that is not put back).

    python3 tools/test-sp11-stick-monitor-arm.py
    Q1N1_SP11_TREE=/nonexistent python3 tools/test-sp11-stick-monitor-arm.py      (the harness tests SKIP; the text tests run)

The code under test is the REAL wrapper, GIC arm and put-back, monitor stage and reporter (probes/sp11-stick-*.c built with SNAP_SP11_STICK_MONITOR=1 and
SP11_STICK_HOST_TEST=1) in the harness of tools/test-sp11-stick-monitor.c under AddressSanitizer and UndefinedBehaviorSanitizer; the model proves the stage's
order, bounds, put-back and report against the model, and nothing about the unit's GIC. The offsets that this file holds are the GICv3 architecture's as
remembered (the specification was not re-read: INFERRED). The state boot 13 read is OBSERVED, one unit, one boot (docs/SP11-STICK-GIC.md section 12). The
seeded faults of the GIC stage are those of probes/sp11-stick-gic.h, named here by their defines. No hardware, nothing under /Volumes, no network; everything
is written below build/sp11-wrapper-test-scratch-monitor-arm/, deleted at the end. A SKIP is never a PASS. EXPERIMENTAL; hardware NOT RUN."""
import concurrent.futures
import re
import shutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))
import sp11_stick_bundle_record as rec  # noqa: E402
import sp11_stick_bundle_record_gic as gq  # noqa: E402
import sp11_stick_bundle_record_m1 as m1t  # noqa: E402
import sp11_stick_gic_testlib as G  # noqa: E402
import sp11_stick_monitor_testlib as M  # noqa: E402
import sp11_stick_wrapper_testlib as T  # noqa: E402
from sp11_stick_wrapper_testlib import E, sb  # noqa: E402

MON = T.load('sp11_monitor_tests', TOOLS / 'test-sp11-stick-monitor.py')          # Held, Run and the fixtures of the monitor entry's own suite
AUTH = T.load('sp11_monitor_auth_tests', TOOLS / 'test-sp11-stick-monitor-auth.py')   # FIXED and m1_facts: the rendered record, without a build
STAGE, FIX, RUNS, SEEDED = sb.STAGE, MON.FIX, {}, {}
BOTH = ('snapmon', 'snaprep')
EDGE26 = 2 << (2 * (26 - 16))             # ICFGR1's field of INTID 26 reading edge-triggered
# The GIC as boot 13 read it (OBSERVED, one unit, one boot): ISENABLER0 0x4E800001, INTID 27 pending, EOImode 1, PMR 0xF8, BPR1 3, IGRPEN1 0, CNTHP_CTL 2; the stage's
# write of PMR is ignored on the unit (INFERRED). GICD_CTLR 0x10 written 0x13 reads back 0x12.
UNIT13 = ['CTLR 0x10', 'TYPER 0xF87F051E', 'IIDR 0x401043B', 'RTYPER 0xD0000E7', 'ISENABLER0 0x4E800001', 'PRIO 0xA0', 'ISPENDR0 0x8000000', 'ICCCTLR 2', 'PMR 0xF8',
          'BPR1 3', 'IGRPEN1 0', 'CTL 2', 'GICD_WMASK 0x92', 'IGNORE ICC_PMR_EL1']
FOREIGN_SPI = ['SPI_EN 1 0x100', 'SPI_PEND 1 0x100']        # INTID 40 enabled and pending: what the quarantine of this CPU's redistributor does not cover
Q13 = 0x4A800001                                            # the bits the quarantine clears of boot 12's ISENABLER0: SGI 0 and PPIs 23, 25, 27 and 30
# scenario -> the GIC model's lines, the fake monitor's configuration, the GIC outcome that refuses it (None: the monitor is called and returns)
CASES = {
    'nominal-found-clear': dict(),
    'nominal-unit13': dict(gic=UNIT13),
    'nominal-ds1': dict(gic=['CTLR 0x50']),
    'nominal-edge': dict(gic=[f'CFG1 {EDGE26:#x}']),
    'nominal-g-on': dict(gic=['CTLR 0x13']),
    'nominal-pending-left': dict(cfg='leave=cnthp:1'),
    'put-back-fails': dict(cfg='bumpphase=1', gic=['NOHOLD ICC_PMR_EL1']),
    'refuse-q-not-held': dict(gic=[*UNIT13, 'IGNORE GICR_ICENABLER0'], out=G.OUT.Q_NOT_HELD),
    'refuse-foreign-pending': dict(gic=[*UNIT13, *FOREIGN_SPI], out=G.OUT.FOREIGN_PENDING),
    'refuse-foreign-not-restored': dict(gic=[*UNIT13, *FOREIGN_SPI, 'NOHOLD GICR_ISENABLER0 1'], cfg='putphase=1', out=G.OUT.NOT_RESTORED),
    'refuse-g-not-held': dict(gic=['IGNORE GICD_CTLR'], out=G.OUT.G_NOT_HELD),
    'refuse-r-not-held': dict(gic=['NS_PPI 0'], out=G.OUT.R_NOT_HELD),
    'refuse-c-not-held': dict(gic=['IGNORE ICC_IGRPEN1_EL1'], out=G.OUT.C_NOT_HELD),
    'refuse-prio': dict(gic=['PMR 0x70', 'IGNORE ICC_PMR_EL1'], out=G.OUT.PRIO_NOT_BELOW_PMR),
    'refuse-sre': dict(gic=['SRE 0'], out=G.OUT.SRE_CLEAR),
    'refuse-ppi-owned': dict(gic=['ISACTIVER0 0x4000000'], out=G.OUT.PPI_OWNED),
    'refuse-asleep': dict(gic=['WAKER 0x6'], out=G.OUT.GICR_ASLEEP),
}
# Seeded-bad stages (build name -> the defines), and for each the run that must reject it: (scenario, the property that run must hold)
VARIANTS = {'no-quarantine': ['-DSP11_STICK_GIC_SEED_NO_QUARANTINE'], 'no-hppir-check': ['-DSP11_STICK_GIC_SEED_NO_HPPIR_CHECK'],
            'quarantine-no-putback': ['-DSP11_STICK_GIC_SEED_QUARANTINE_NO_PUTBACK'], 'arm-refusal-calls': ['-DSP11_STICK_MONITOR_SEED_ARM_REFUSAL_CALLS'],
            'no-arm-putback': ['-DSP11_STICK_MONITOR_SEED_NO_ARM_PUTBACK']}
# The GICv3 registers as the architecture places them (INFERRED: from memory, the specification was not re-read), as the record's text spells them, and the
# constants of probes/sp11-stick-gic.h that the stage uses for them.
ARCH = {'GICD_CTLR': ('D+0x000', 'SG_GICD_CTLR'), 'GICD_TYPER': ('D+0x004', 'SG_GICD_TYPER'), 'GICD_IIDR': ('D+0x008', 'SG_GICD_IIDR'),
        'GICD_ISENABLER': ('D+0x100+4n', 'SG_GICD_ISENABLER'), 'GICD_ISPENDR': ('D+0x200+4n', 'SG_GICD_ISPENDR'),
        'GICR_CTLR': ('RD+0x000', 'SG_GICR_CTLR'), 'GICR_TYPER': ('RD+0x008', 'SG_GICR_TYPER'), 'GICR_WAKER': ('RD+0x014', 'SG_GICR_WAKER'),
        'GICR_IGROUPR0': ('SGI+0x080', 'SG_IGROUPR0'), 'GICR_ISENABLER0': ('SGI+0x100', 'SG_ISENABLER0'), 'GICR_ICENABLER0': ('SGI+0x180', 'SG_ICENABLER0'),
        'GICR_ISPENDR0': ('SGI+0x200', 'SG_ISPENDR0'), 'GICR_ICPENDR0': ('SGI+0x280', 'SG_ICPENDR0'), 'GICR_ISACTIVER0': ('SGI+0x300', 'SG_ISACTIVER0'),
        'GICR_IPRIORITYR': ('SGI+0x41A', 'SG_IPRIORITYR'), 'GICR_ICFGR1': ('SGI+0xC04', 'SG_ICFGR1'),
        'ICC_PMR_EL1': ('(MSR)', None), 'ICC_BPR1_EL1': ('(MSR)', None), 'ICC_IGRPEN1_EL1': ('(MSR)', None), 'ICC_SRE_EL2': ('(MRS)', None),
        'ICC_CTLR_EL1': ('(MRS)', None), 'ICC_RPR_EL1': ('(MRS)', None), 'ICC_HPPIR1_EL1': ('(MRS)', None)}
# What the text says the page of a refusal shows, in this order (the lines are those of docs/SP11-STICK-GIC.md section 6 and docs/SP11-STICK-MONITOR.md section 4).
PAGE_ORDER = ('GIC QUAR MASK=', 'GIC RESTORE CLEAN=', 'GIC VERDICT=', 'MON VERDICT=GIC_NOT_READY', 'REFUSED=GIC_NOT_READY STAGE=COPIED DETAIL=')
GIC_REG = re.compile(r'(GICD|GICR|ICC)_')
ENTRY = re.compile(r'^ {8}([WR]) (\S+) (\S+)')            # a register line of the class, as printed: six spaces of the class block and two of the entry


# ---- the harness ---------------------------------------------------------------------------------------------------------------------------
def exe_of(variant='base'):
    return M.harness(FIX['tree']) if variant == 'base' else M.harness(FIX['tree'], variant, extra=VARIANTS[variant])


def batch(names, variant='base'):
    texts, worlds, cfg = [], {}, {}
    for name in names:
        worlds[name] = M.world(FIX['bundle'], FIX['scratch'])
        texts.append((name, G.scenario(name, worlds[name], CASES[name].get('gic', ()), BOTH, ())))
        if 'cfg' in CASES[name]:
            cfg[name] = CASES[name]['cfg']
    out = M.run(exe_of(variant), texts, cfg)
    runs = {name: MON.Run(name, worlds[name], out, BOTH) for name in names}
    for name, run in runs.items():
        run.reads = out.reads[name]
    return runs


def setUpModule():
    M.use_scratch('monitor-arm')
    shutil.rmtree(T.BUILD, ignore_errors=True)
    FIX['scratch'] = T.BUILD / 'host'
    FIX['scratch'].mkdir(parents=True)
    FIX['tree'] = T.q1n1_tree()
    FIX['fixtures'] = T.Fixtures(FIX['scratch'] / 'fixtures')
    FIX['bundle'] = FIX['fixtures'].bundle('gic', T.LOW, stores='ram,el2_sysreg,gic', extra=('--gic-stop',))
    if MON.runnable():
        with concurrent.futures.ThreadPoolExecutor(3) as pool:
            for future in [pool.submit(exe_of, v) for v in ('base', *VARIANTS)]:
                future.result()
        RUNS.update(batch(list(CASES)))
        for variant, (scenarios, _) in DETECTORS.items():
            SEEDED[variant] = batch(scenarios, variant)


def tearDownModule():
    shutil.rmtree(T.BUILD, ignore_errors=True)


# ---- what a run is held to -----------------------------------------------------------------------------------------------------------------
def calls(run):
    """The call counter: how many times the fake monitor was entered."""
    return len([m for m in run.marks if m[0] == 'CALL'])


def logged(run):
    """(the GIC registers written, the GIC registers read) by the stage in this run: the model's log outside the fake monitor's window, and the MMIO reads that
    the harness counts (the model logs only some of them)."""
    call, ret = run.mark('CALL'), run.mark('RET')
    lo, hi = (call[1], ret[1]) if call and ret else (len(run.g.log), len(run.g.log))
    mine = run.g.log[:lo] + run.g.log[hi:]
    writes = {e[1] for e in mine if e[0] == 'W' and GIC_REG.match(e[1])}
    reads = {e[1] for e in mine if e[0] == 'R' and GIC_REG.match(e[1])} | {n for n in run.reads if GIC_REG.match(n)}
    return writes, reads


def not_called(run):
    """The monitor was not called: no call counted, no state line, no MON CALL line, and the head stops short of MONITOR_ENTERED."""
    found = [f'the monitor was called {calls(run)} time(s)'] if calls(run) else []
    found += ['the fake monitor printed its state'] if run.state else []
    found += ['a MON CALL line is on the page'] if any(t.startswith('SNAPMON MON CALL') for t in run.mon.texts()) else []
    return found + ([f'the head reached stage {MON.head_of(run)["stage"]}'] if MON.head_of(run)['stage'] >= STAGE.MONITOR_ENTERED else [])


def refused_by_the_arm(run, code):
    """The arm half refused with GIC outcome `code`: before the call, with the GIC outcome as the refusal's detail, the monitor never called, and the page and
    the head within their rules."""
    return MON.Held.refused_before_call(run, code) + not_called(run) + MON.Held.page(run) + MON.Held.head(run)


def as_found(run):
    """Every register of the model is as the stage read it, none of the classes it began is left changed, and the GIC record says it is clean."""
    record = run.g.r()
    found = G.Checks.restored(run.g)
    if record.restore_failed or record.restore_retried:
        found.append(f'the record names failed {record.restore_failed:#x} and retried {record.restore_retried:#x} registers')
    return found


def through_mon_arm_failed(run):
    """mon_arm_failed ran: the put-back point and then the refusal point (and never the call's or the final one), the monitor record said GIC_NOT_READY at the
    arming sub-stage, and the GIC record was finished (sub-stage DONE) after the put-back."""
    found = [] if set(run.recs) == {'PUTBACK', 'REFUSED'} else [f'the observation points are {sorted(run.recs)}']
    record = M.decode(run.recs['REFUSED']) if 'REFUSED' in run.recs else None
    if record and (record.outcome, M.SUBSTAGES[record.substage]) != (M.OUT.GIC_NOT_READY, 'ARMING'):
        found.append(f'the monitor record says {M.OUTCOMES.get(record.outcome)} at {M.SUBSTAGES[record.substage]}')
    if run.g.r().substage != 11:
        found.append(f'the GIC record is at sub-stage {run.g.r().substage}, not DONE')
    return found


def page_has(run, tail):
    """The last lines of the wrapper's page, but for the closing PLACEMENT line, are `tail`."""
    texts = run.mon.texts()
    return [] if texts[-1 - len(tail):-1] == tail else [f'the page ends {texts[-1 - len(tail):-1]}']


# ---- the text ------------------------------------------------------------------------------------------------------------------------------
def render():
    return rec.record_text({**AUTH.FIXED, 'm1': AUTH.m1_facts()})


def block(text):
    """The lines of the gic class of an M1 record as printed: the first with `   gic: ` in front, the others with six spaces."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith('   gic: '))
    return lines[start:next(i for i in range(start + 1, len(lines)) if re.match(r'^ {3}\S', lines[i]))]


def entries(lines):
    """({register: where} of the W lines, {register: where} of the R lines, the problems of the lines themselves); the [n] of an indexed register is taken off."""
    found, problems = {'W': {}, 'R': {}}, []
    for line in lines:
        if match := ENTRY.match(line):
            kind, name, where = match.groups()
            name = name.removesuffix('[n]')
            if name in found['W'] or name in found['R']:
                problems.append(f'{name} is named by two lines')
            found[kind][name] = where
    return found['W'], found['R'], problems


def register_problems(lines, runs):
    """E1: the text against the model's logs: [str]."""
    written, read_only, problems = entries(lines)
    writes, reads = set(), set()
    for run in runs.values():
        w, r = logged(run)
        writes |= w
        reads |= r
    problems += [f'{n} is written by a run of the model and no W line names it' for n in sorted(writes - set(written))]
    problems += [f'{n} is only read by the runs of the model and no R line names it' for n in sorted(reads - writes - set(read_only))]
    problems += [f'{n} has a W line and no run of the model writes it' for n in sorted(set(written) - writes)]
    problems += [f'{n} has an R line and no run of the model reads it' for n in sorted(set(read_only) - reads)]
    problems += [f'{n} has an R line, as only read, and a run of the model writes it' for n in sorted(set(read_only) & writes)]
    return problems


def offset_problems(lines, header):
    """The offsets the text gives are the architecture's (ARCH) and the stage's own constants (`header`, the text of probes/sp11-stick-gic.h)."""
    written, read_only, _ = entries(lines)
    problems = []
    for name, where in {**written, **read_only}.items():
        if name not in ARCH:
            problems.append(f'{name} is a register that this test does not know')
            continue
        want, constant = ARCH[name]
        if where != want:
            problems.append(f'{name} is given at {where}, the architecture has {want}')
        if constant:
            value = int(re.search(rf'#define {constant}\s+(0x[0-9A-Fa-f]+)', header).group(1), 16) + (26 if name == 'GICR_IPRIORITYR' else 0)
            parts = re.fullmatch(r'(?:D|RD|SGI)\+(0x[0-9A-F]+)(?:\+4n)?', where)
            if not parts or int(parts.group(1), 16) != value:
                problems.append(f'{name} is given at {where}, the stage uses {value:#x} ({constant})')
    return problems


def flat(lines):
    """`lines` as one line of words: what a reader of the record sees, whatever the line breaks."""
    return ' '.join(' '.join(lines).split())


def outcome_problems(lines):
    """Every `NAME (n` and `NAME (n, OUTCOME=0Xh)` of the text is an outcome of the stage with that number."""
    text, problems = flat(lines), []
    for name, number in re.findall(r'\b([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+) \((\d+)[,)]', text):
        if getattr(G.OUT, name, None) != int(number):
            problems.append(f'{name} is given as {number}, the stage has {getattr(G.OUT, name, None)}')
    for name, number, hexed in re.findall(r'\b([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+) \((\d+), OUTCOME=0X([0-9A-F]+)\)', text):
        if int(hexed, 16) != int(number):
            problems.append(f'{name} is given as {number} and OUTCOME=0X{hexed}')
    return problems


def without(lines, name):
    """`lines` without the register line of `name` and its continuation lines."""
    kept, skipping = [], False
    for line in lines:
        match = ENTRY.match(line)
        if match:
            skipping = match.group(2).removesuffix('[n]') == name
        elif not line.startswith(' ' * 14):
            skipping = False
        if not skipping:
            kept.append(line)
    return kept


class Text(unittest.TestCase):
    """E1, the text alone: its place in the record, its labels, the offsets and outcomes it gives, the place for pass 6."""

    @classmethod
    def setUpClass(cls):
        cls.text = render()
        cls.lines = block(cls.text)
        cls.header = (ROOT / 'probes/sp11-stick-gic.h').read_text()

    def test_the_class_is_the_one_the_record_module_prints_within_the_columns_of_the_rest(self):
        self.assertEqual(self.lines[0], f'   gic: {m1t.GIC_LINES[0]}')
        self.assertEqual(self.lines[1:], [f'      {line}' for line in m1t.GIC_LINES[1:]])
        self.assertLessEqual(max(map(len, self.lines)), 120)
        facts = self.text.split('   The arm half since pass 5')[1].split('   Not authorised')[0]
        self.assertLessEqual(max(map(len, facts.splitlines())), 120)
        self.assertTrue(self.text.isascii())
        self.assertNotIn('VERIFIED', self.text, 'no label of a hardware result, and no VERIFIED of anything the host proves only for a binary')
        for other in (rec.TOUCHES, rec.GIC_STOP_TOUCHES, gq.TOUCHES):
            self.assertNotIn(' '.join(other['gic'][1].split()), ' '.join(self.text.split()))

    def test_the_statements_that_pass_5_made_false_are_gone_and_the_monitors_own_are_kept(self):
        flat = ' '.join(self.text.split())
        for gone in ('stay as found', 'ISENABLER0 bit 26 only when it reads clear', 'the other PPIs that firmware enabled', 'written by the wrapper before the call and put back after it, as in',
                     'The census cannot see the monitor\'s stores to the GIC distributor: they are in the GPU code of section 3b, at the address that PARAMS gpu.gicd holds, and its one fixed '
                     'distributor address is in secondary_run (group G, not reached). The monitor uses'):
            self.assertNotIn(gone, flat)
        for kept in ('the census cannot see its stores to the GIC distributor: they are in the GPU code of section 3b', 'acknowledges an interrupt in two cases only: INTID 26 (the timer) when its ISTATUS is set',
                     'Any other IRQ or FIQ, and the timer at the deadline, ends the run with the interrupt not acknowledged', 'a foreign interrupt stays pending (INFERRED from probes/xnuboot/async.c)'):
            self.assertIn(kept, flat)

    def test_each_observation_carries_its_label_and_the_boot_it_comes_from(self):
        flat = ' '.join(self.text.split())
        for needle in ('ISENABLER0 read 0x4E800001 (OBSERVED, boot 12)', 'boot 13 read it back 0x12, OBSERVED', 'it read 0xA0 on the unit, OBSERVED, boot 12', 'INFERRED that the write is ignored',
                       'ISPENDR0 0x08000000 (boots 13 and 14)', 'OBSERVED, one unit, one boot each, in the dry run\'s own build, not in this one',
                       'This build is the same source built for the monitor entry: it ran once in boot 20 (OBSERVED, one unit, one boot: GIC NEW ISEN=0X4000000, GIC RESTORE CLEAN=YES)',
                       'INFERRED: that INTID 27 pending is that timer asserting', 'PPI 26 itself reached EL2 four times in window 1 of boot 14',
                       'U3 answered; the monitor\'s own stop path ran later, in boot 20 (OBSERVED once)',
                       'the quarantine held: ICENABLER0 written 0x4A800001 left ISENABLER0 0x04000000 (PPI 26 only)', 'the put-back was whole, the quarantined enables last: ISENABLER0 0x4E800001',
                       'CNTV_CTL_EL0 read 0x5 (ENABLE 1, IMASK 0, ISTATUS 1)', 'SPI LINES=30 EN=3 PEND=9 BOTH=0', 'three boots, 12 to 14, found the same enabled set',
                       'That PPI 26 reaches EL2 is OBSERVED in the dry run\'s stage (boot 14, one unit, one boot: four deliveries from EL2 and one from EL1, U3 answered',
                       'INFERRED from the GICv3 text as remembered (the specification was not re-read)', 'the prepare alone is UNKNOWN'):
            self.assertIn(needle, flat)
        for stale in ('Whether PPI 26 reaches EL2 at all is UNKNOWN (U3)', 'UNKNOWN: whether PPI 26 itself reaches EL2 (U3)', 'two boots found the same enabled set',
                      'Neither the quarantine nor the check has run on the unit', 'the first of its kind there, and has not been made'):
            self.assertNotIn(stale, flat, 'boot 14 answered it')

    def test_the_duration_the_bound_and_what_the_wrapper_cannot_bound_are_said(self):
        flat = ' '.join(self.text.split())
        for needle in ('It is on from Q\'s write until the put-back\'s last write, which comes after the monitor has returned: the whole call',
                       'What bounds it is the monitor\'s own stop, the interrupt of its timer (CNTHP, PPI 26) at the time box on CNTPCT_EL0, at most 5 s',
                       'Nothing in the wrapper bounds the call', 'ends only by the operator\'s FORCE OFF (section 5c), and this stage\'s put-back does not run then',
                       'On a refusal of the arm half the quarantine lasts only as long as the put-back that follows at once, except in the both-gates case, in which that put-back does not lift it'):
            self.assertIn(needle, flat)

    def test_every_offset_is_the_architectures_and_the_stages_and_a_wrong_one_is_refused(self):
        self.assertEqual(offset_problems(self.lines, self.header), [])
        self.assertIn('SGI+0x180', ' '.join(self.lines))
        edited = [line.replace('SGI+0x180', 'SGI+0x188') for line in self.lines]
        self.assertTrue([p for p in offset_problems(edited, self.header) if 'GICR_ICENABLER0' in p and '0x188' in p])
        edited = [line.replace('SGI+0x41A', 'SGI+0x41B') for line in self.lines]
        self.assertTrue([p for p in offset_problems(edited, self.header) if 'GICR_IPRIORITYR' in p])
        self.assertEqual(int(re.search(r'#define SG_SGI_FRAME\s+(0x[0-9A-Fa-f]+)', self.header).group(1), 16), 0x10000, 'the text says SGI is RD + 0x10000')
        self.assertIn('SGI is RD + 0x10000', flat(self.lines))

    def test_every_outcome_is_the_stages_and_a_wrong_number_is_refused(self):
        self.assertEqual(outcome_problems(self.lines), [])
        text = flat(self.lines)
        for pair in ('Q_NOT_HELD (22, OUTCOME=0X16)', 'FOREIGN_PENDING (23, OUTCOME=0X17)', 'NOT_RESTORED (48, OUTCOME=0X30)', 'G_NOT_HELD (16)', 'R_NOT_HELD (17)', 'C_NOT_HELD (18)',
                     'PRIO_NOT_BELOW_PMR (20)', 'GICR_NOT_FOUND (8)', 'GICR_ASLEEP (9)', 'PPI_OWNED (10)', 'SRE_CLEAR (11)'):
            self.assertIn(pair, text)
        self.assertTrue(outcome_problems([line.replace('Q_NOT_HELD (22', 'Q_NOT_HELD (21') for line in self.lines]))
        self.assertTrue(outcome_problems([line.replace('OUTCOME=0X17', 'OUTCOME=0X18') for line in self.lines]))
        self.assertTrue(outcome_problems([line.replace('NOT_RESTORED (48', 'NOT_RESTORED (47') for line in self.lines]))

    def test_the_page_lines_the_text_names_come_in_the_order_of_the_page(self):
        at = [flat(self.lines).find(prefix) for prefix in PAGE_ORDER]
        self.assertNotIn(-1, at)
        self.assertEqual(sorted(at), at)

    def test_the_place_for_pass_6_is_filled_and_is_the_place_a_stand_in_sentence_takes_after_the_last_write_of_the_put_back(self):
        sentence = m1t.wrap('A stand-in for the sentence of pass 6.', first=8, rest=8, width=m1t.GIC_WIDTH)
        plain, filled = m1t.gic_lines(pass6=()), m1t.gic_lines(pass6=sentence)
        self.assertEqual(len(filled), len(plain) + len(sentence))
        at = filled.index(sentence[0])
        self.assertEqual(filled[at:at + len(sentence)], sentence)
        self.assertEqual((*filled[:at], *filled[at + len(sentence):]), plain, 'nothing else moves')
        self.assertTrue(re.search(r'NOT_RESTORED \(48, OUTCOME=0X30\)\.$', ' '.join(filled[:at])), 'it follows the lines of the last write')
        self.assertTrue(filled[at + len(sentence)].startswith('  W ICC_PMR_EL1'), 'and comes before the class C registers')
        self.assertTrue(m1t.PASS6_LINES, 'the place is no longer empty: Phase E part 2 filled it')
        self.assertEqual(m1t.GIC_LINES.index(m1t.PASS6_LINES[0]), at, 'the real sentence is where the stand-in goes (tools/test-sp11-stick-monitor-gates.py tests what it says)')


def fake_runs(lines):
    """A set of runs that log exactly the registers `lines` name (written for the W lines, read for the R lines): the text compared with a model that agrees."""
    written, read_only, _ = entries(lines)
    run = type('Run', (), {})()
    run.g = type('Gic', (), {'log': [('W', n, 0, 0, 0) for n in written] + [('R', n, 0, 0, 0) for n in [*written, *read_only]], 'viol': []})()
    run.mark = lambda kind: None
    run.reads = {}
    return {'all': run}


class Seeds(unittest.TestCase):
    """The faulty cases of the text: a register left out, labelled wrongly or invented is a problem that the comparison names."""

    @classmethod
    def setUpClass(cls):
        cls.lines = block(render())
        cls.fake = fake_runs(cls.lines)

    def test_the_text_has_no_problem_with_a_set_of_runs_that_agrees_with_it(self):
        self.assertEqual(register_problems(self.lines, self.fake), [])

    def test_each_kind_of_fault_is_named(self):
        cut = register_problems(without(self.lines, 'GICR_ICENABLER0'), self.fake)
        self.assertTrue([p for p in cut if 'GICR_ICENABLER0 is written by a run' in p], cut)
        cut = register_problems(without(self.lines, 'GICR_WAKER'), self.fake)
        self.assertTrue([p for p in cut if 'GICR_WAKER is only read' in p], cut)
        flipped = [line.replace('  W GICR_ICPENDR0 ', '  R GICR_ICPENDR0 ') for line in self.lines]
        self.assertTrue([p for p in register_problems(flipped, self.fake) if 'GICR_ICPENDR0 has an R line' in p])
        flipped = [line.replace('  R GICR_TYPER ', '  W GICR_TYPER ') for line in self.lines]
        self.assertTrue([p for p in register_problems(flipped, self.fake) if 'GICR_TYPER has a W line and no run' in p])
        invented = [*self.lines, '        W GICR_ICACTIVER0 SGI+0x380 a register that the stage does not write.']
        self.assertTrue([p for p in register_problems(invented, self.fake) if 'GICR_ICACTIVER0 has a W line' in p])
        twice = [*self.lines, '        R GICR_WAKER RD+0x014 named twice.']
        self.assertTrue([p for p in register_problems(twice, self.fake) if 'GICR_WAKER is named by two lines' in p])


# ---- the runs ------------------------------------------------------------------------------------------------------------------------------
class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not MON.runnable():
            raise unittest.SkipTest('no clang or no q1n1 checkout (Q1N1_SP11_TREE): the harness is not built')
        cls.lines = block(render())


class Registers(Base):
    """E1: the text against the model's logs, in the nominal runs and in the runs that fail."""

    def test_the_scenarios_end_as_they_are_meant_to(self):
        for name, case in CASES.items():
            with self.subTest(name):
                run = RUNS[name]
                self.assertEqual(run.g.nviol, 0, run.g.viol)
                head = MON.head_of(run)
                if case.get('out') is None:
                    self.assertEqual((head['refusal'], calls(run)), (0, 1), 'the monitor is called and the stage ends DONE')
                else:
                    self.assertEqual((head['refusal'], head['refusal_detail'], calls(run)), (E.GIC_NOT_READY, case['out'], 0))

    def test_the_text_names_every_register_the_model_writes_and_reads_and_each_w_line_is_written(self):
        self.assertEqual(register_problems(self.lines, RUNS), [])

    def test_the_text_without_a_register_or_with_a_wrong_label_is_refused_against_these_runs(self):
        for name in ('GICR_ICENABLER0', 'GICR_ICPENDR0', 'GICR_IGROUPR0', 'GICR_ICFGR1', 'ICC_BPR1_EL1'):
            with self.subTest(drop=name):
                self.assertTrue([p for p in register_problems(without(self.lines, name), RUNS) if name in p])
        for name in ('GICR_WAKER', 'GICR_CTLR', 'GICD_IIDR', 'GICD_ISPENDR', 'ICC_RPR_EL1', 'ICC_HPPIR1_EL1'):
            with self.subTest(drop=name):
                self.assertTrue([p for p in register_problems(without(self.lines, name), RUNS) if name in p])
        flipped = [line.replace('  W GICR_ISENABLER0 ', '  R GICR_ISENABLER0 ') for line in self.lines]
        self.assertTrue([p for p in register_problems(flipped, RUNS) if 'GICR_ISENABLER0' in p])

    def test_the_registers_that_only_some_runs_touch_are_in_the_runs_that_the_text_says_they_are(self):
        """The conditions of the text, each shown by a run: ICPENDR0 only when INTID 26 is left pending; IGROUPR0 only with DS set; ICFGR1 only for an edge PPI; the
        quarantine only when this CPU has more enabled than PPI 26; GICD_CTLR not at all when group 1 is already on; no other ICC register read when SRE is clear; no
        redistributor wake."""
        written = {name: logged(run)[0] for name, run in RUNS.items()}
        for name, reg, yes in (('nominal-pending-left', 'GICR_ICPENDR0', True), ('nominal-unit13', 'GICR_ICPENDR0', False), ('nominal-ds1', 'GICR_IGROUPR0', True),
                               ('nominal-unit13', 'GICR_IGROUPR0', False), ('nominal-edge', 'GICR_ICFGR1', True), ('nominal-unit13', 'GICR_ICFGR1', False),
                               ('nominal-unit13', 'GICR_ICENABLER0', True), ('nominal-g-on', 'GICD_CTLR', False), ('nominal-found-clear', 'GICD_CTLR', True),
                               ('nominal-unit13', 'GICR_ISENABLER0', True), ('refuse-asleep', 'GICR_ISENABLER0', False)):
            with self.subTest(run=name, register=reg):
                self.assertEqual(reg in written[name], yes)
        isenabler0 = [v for k, name, v, *_ in RUNS['nominal-unit13'].g.log if k == 'W' and name == 'GICR_ISENABLER0']
        self.assertEqual(isenabler0, [Q13], 'one write of ISENABLER0 on boot 13\'s state: the quarantine\'s enables, last')
        sre = logged(RUNS['refuse-sre'])[1]
        self.assertTrue({'ICC_SRE_EL2', 'GICD_CTLR', 'GICR_TYPER'} <= sre)
        self.assertEqual(sre & {'ICC_CTLR_EL1', 'ICC_PMR_EL1', 'ICC_BPR1_EL1', 'ICC_IGRPEN1_EL1', 'ICC_RPR_EL1', 'ICC_HPPIR1_EL1'}, set(), 'none of the other ICC registers is read when SRE is clear')
        for name, run in RUNS.items():
            self.assertNotIn('GICR_WAKER', logged(run)[0], f'{name}: waking a redistributor is not authorised')

    def test_the_survey_reads_registers_one_to_itlines_once_each_and_the_distributor_is_read_nowhere_else(self):
        reads = RUNS['nominal-unit13'].reads
        self.assertEqual((reads['GICD_ISENABLER'], reads['GICD_ISPENDR']), (30, 30), 'ITLinesNumber 30 (GICD_TYPER 0xF87F051E): one read of each register 1 to 30')
        self.assertEqual(reads['GICD_TYPER'], 1)
        self.assertEqual(reads['GICD_IIDR'], 1)
        self.assertGreaterEqual(reads['GICR_IPRIORITYR'], 5, 'INTID 26 and the four of the survey')
        self.assertEqual(reads['GICR_TYPER'], 1, 'the first frame matched: no other frame is read')

    def test_the_refusals_before_any_store_leave_the_survey_unread_and_write_nothing(self):
        for name in ('refuse-sre', 'refuse-ppi-owned', 'refuse-asleep'):
            with self.subTest(name):
                self.assertEqual(RUNS[name].g.writes(), [])
                self.assertNotIn('GICD_ISENABLER', RUNS[name].reads)

    def test_the_record_the_text_names_is_version_3_of_1456_bytes_at_0x11000(self):
        record = RUNS['nominal-unit13'].g.rec
        self.assertEqual((G.REC_AT, len(record), int.from_bytes(record[8:12], 'little'), int.from_bytes(record[12:16], 'little')), (0x11000, 1456, 3, 1456))


class Refusals(Base):
    """E2: Q_NOT_HELD (22) and FOREIGN_PENDING (23), and a put-back that fails after one of them, end the run before the monitor is called."""

    def test_a_quarantine_that_does_not_hold_refuses_before_the_call_and_leaves_the_gic_as_found(self):
        run = RUNS['refuse-q-not-held']
        self.assertEqual(refused_by_the_arm(run, G.OUT.Q_NOT_HELD) + as_found(run) + through_mon_arm_failed(run), [])
        self.assertEqual(run.g.writes(), [('GICR_ICENABLER0', Q13)], 'one write, and nothing stored after it: nothing to put back')
        record = run.g.r()
        self.assertEqual((G.classes(record.begun), record.sv.quarantined, record.sv.quar_rb, record.outcome, record.first_outcome),
                         ('R', Q13, 0x4E800001, G.OUT.Q_NOT_HELD, G.OUT.Q_NOT_HELD))
        self.assertEqual(record.sv.flags & G.SVF['HPPIR'], 0, 'ICC_HPPIR1_EL1 was never read')
        self.assertEqual(page_has(run, ['SNAPMON GIC QUAR MASK=0X4A800001 DONE=0X4E800001 HPPIR=-', 'SNAPMON GIC RESTORE CLEAN=YES FAILED=0X0 RETRIED=0X0 BEGUN=R VBAR=NONE EL1=NONE',
                                        'SNAPMON GIC VERDICT=Q_NOT_HELD OUTCOME=0X16 SUB=DONE', 'SNAPMON MON VERDICT=GIC_NOT_READY FLAGS=0X1 SUB=3',
                                        'SNAPMON REFUSED=GIC_NOT_READY STAGE=COPIED DETAIL=0X16']), [])

    def test_a_foreign_interrupt_next_refuses_before_the_call_with_its_intid_and_puts_everything_back_with_the_quarantines_enables_last(self):
        run = RUNS['refuse-foreign-pending']
        self.assertEqual(refused_by_the_arm(run, G.OUT.FOREIGN_PENDING) + as_found(run) + through_mon_arm_failed(run), [])
        found = 0x4E800001
        self.assertEqual(run.g.writes(), [('GICR_ICENABLER0', Q13), ('GICD_CTLR', 0x13), ('GICR_IPRIORITYR', 0x80), ('ICC_PMR_EL1', 0xF0), ('ICC_BPR1_EL1', 0), ('ICC_IGRPEN1_EL1', 1),
                                          ('ICC_IGRPEN1_EL1', 0), ('ICC_BPR1_EL1', 3), ('GICR_IPRIORITYR', 0xA0), ('GICD_CTLR', 0x10), ('GICR_ISENABLER0', Q13)],
                         'the quarantine first, the put-back C, R, G, and the quarantine\'s enables last (PMR: the write is ignored on this state, so nothing to put back)')
        self.assertEqual(G.Checks.quarantine(run.g, found) + G.Checks.quarantine_first(run.g, found) + G.Checks.quarantine_last(run.g, found) + G.Checks.pending_untouched(run.g) +
                         G.Checks.no_window_with_foreign_pending(run.g), [])
        record = run.g.r()
        self.assertEqual((G.classes(record.begun), record.sv.quarantined, record.sv.quar_rb, record.sv.hppir_pre, record.sv.flags & G.SVF['HPPIR'], record.sv.spi_both, record.sv.spi_first),
                         ('GRC', Q13, 0x4000000, 40, G.SVF['HPPIR'], 1, 40))
        self.assertEqual((run.g.state['deliveries'], run.g.state['acks'], run.g.state['excursions']), (0, 0, 0), 'IRQ was never unmasked')
        self.assertEqual(page_has(run, ['SNAPMON GIC QUAR MASK=0X4A800001 DONE=0X4000000 HPPIR=40', 'SNAPMON GIC RESTORE CLEAN=YES FAILED=0X0 RETRIED=0X0 BEGUN=GRC VBAR=NONE EL1=NONE',
                                        'SNAPMON GIC VERDICT=FOREIGN_PENDING OUTCOME=0X17 SUB=DONE', 'SNAPMON MON VERDICT=GIC_NOT_READY FLAGS=0X1 SUB=3',
                                        'SNAPMON REFUSED=GIC_NOT_READY STAGE=COPIED DETAIL=0X17']), [])

    def test_a_put_back_that_fails_after_a_refusal_is_the_detail_of_the_refusal_and_is_named(self):
        run = RUNS['refuse-foreign-not-restored']
        self.assertEqual(MON.Held.refused_before_call(run, G.OUT.NOT_RESTORED) + not_called(run) + MON.Held.page(run) + through_mon_arm_failed(run), [])
        record = run.g.r()
        self.assertEqual((record.first_outcome, record.outcome, record.restore_failed, record.restore_retried), (G.OUT.FOREIGN_PENDING, G.OUT.NOT_RESTORED, G.REG_BIT['ISENABLER0'], G.REG_BIT['ISENABLER0']))
        self.assertEqual(run.g.state['isenabler0'], (0x4E800001, 0x4000000), 'the enable register is left as the quarantine left it: loud, not silent')
        texts = run.mon.texts()
        self.assertIn('SNAPMON GIC VERDICT=NOT_RESTORED FIRST=FOREIGN_PENDING OUTCOME=0X30 SUB=DONE', texts)
        self.assertTrue(any(t.startswith('SNAPMON GIC RESTORE CLEAN=NO FAILED=') for t in texts), texts)
        self.assertEqual(texts[-2], 'SNAPMON REFUSED=GIC_NOT_READY STAGE=COPIED DETAIL=0X30')

    def test_the_page_is_29_lines_of_the_30_and_the_two_refusals_print_the_lines_the_text_names_in_its_order(self):
        for name in ('refuse-q-not-held', 'refuse-foreign-pending', 'refuse-foreign-not-restored'):
            with self.subTest(name):
                run = RUNS[name]
                self.assertEqual(len(run.mon.lines), 29, 'the review measured 29 of 30; a page that changes length is a change of the page budget (docs/SP11-STICK-MONITOR.md section 4)')
                self.assertEqual(MON.head_of(run)['lines_printed'], 29)
                texts = run.mon.texts()
                at = [next(i for i, t in enumerate(texts) if t.startswith('SNAPMON ' + prefix)) for prefix in PAGE_ORDER]
                self.assertEqual(sorted(at), at, 'the lines of the text, in the order of the page')

    def test_the_reporter_prints_the_gic_lines_of_the_record_but_no_quarantine_line_so_the_intid_is_on_the_live_page_only(self):
        for name in ('refuse-q-not-held', 'refuse-foreign-pending'):
            with self.subTest(name):
                rep = RUNS[name].rep.texts()
                self.assertEqual([t.split()[2] for t in rep if t.startswith('SNAPREP GIC ')], ['GICD', 'GICR', 'CPU', 'NEW', 'RESTORE'])
                self.assertFalse([t for t in rep if t.startswith(('SNAPREP GIC QUAR', 'SNAPREP GIC VERDICT', 'SNAPREP GIC SPI', 'SNAPREP GIC PPIP'))])
                self.assertTrue(any(t.startswith('SNAPREP MON VERDICT=GIC_NOT_READY') for t in rep))
        self.assertIn('SNAPMON GIC QUAR MASK=0X4A800001 DONE=0X4000000 HPPIR=40', RUNS['refuse-foreign-pending'].mon.texts(), 'the INTID is on the live page')

    def test_every_other_refusal_of_the_arm_is_the_same_kind_of_refusal(self):
        for name, case in CASES.items():
            if case.get('out') in (None, G.OUT.Q_NOT_HELD, G.OUT.FOREIGN_PENDING, G.OUT.NOT_RESTORED):
                continue
            with self.subTest(name):
                run = RUNS[name]
                self.assertEqual(refused_by_the_arm(run, case['out']) + as_found(run) + through_mon_arm_failed(run), [])

    def test_a_put_back_that_fails_after_the_call_is_not_a_refusal(self):
        run = RUNS['put-back-fails']
        self.assertEqual((MON.head_of(run)['refusal'], calls(run)), (0, 1))
        self.assertTrue(any('GIC RESTORE CLEAN=NO' in t for t in run.mon.texts()))


DETECTORS = {
    'no-quarantine': (('refuse-q-not-held', 'refuse-foreign-pending'), lambda run, name: refused_by_the_arm(run, CASES[name]['out'])),
    'no-hppir-check': (('refuse-foreign-pending',), lambda run, name: refused_by_the_arm(run, G.OUT.FOREIGN_PENDING)),
    'quarantine-no-putback': (('refuse-foreign-pending',), lambda run, name: as_found(run) + G.Checks.quarantine_last(run.g, 0x4E800001)),
    'arm-refusal-calls': (('refuse-q-not-held', 'refuse-foreign-pending'), lambda run, name: not_called(run) + through_mon_arm_failed(run)),
    'no-arm-putback': (('refuse-foreign-pending',), lambda run, name: as_found(run)),
}


class Faulty(Base):
    """Each seeded-bad stage fails the check that matches its fault on the run that shows it, and the real stage passes that check on the same run."""

    def test_each_seeded_stage_is_rejected_and_the_real_stage_is_not(self):
        for variant, (scenarios, check) in DETECTORS.items():
            for name in scenarios:
                with self.subTest(variant=variant, scenario=name):
                    self.assertEqual(check(RUNS[name], name), [], 'the real stage passes')
                    self.assertNotEqual(check(SEEDED[variant][name], name), [], f'the {variant} stage must fail it')

    def test_the_seeds_break_what_they_say_they_break(self):
        """The shape of each failure, so that a seed that fails for another reason is noticed: the stage without the quarantine calls the monitor; without the check
        of ICC_HPPIR1_EL1 it calls the monitor with INTID 40 next; a refused arm that calls on is a call; one that does not put back leaves the registers changed."""
        self.assertEqual(calls(SEEDED['no-quarantine']['refuse-q-not-held']), 1)
        self.assertEqual(calls(SEEDED['no-hppir-check']['refuse-foreign-pending']), 1)
        self.assertEqual(calls(SEEDED['arm-refusal-calls']['refuse-q-not-held']), 1)
        self.assertEqual(MON.head_of(SEEDED['arm-refusal-calls']['refuse-q-not-held'])['refusal'], 0, 'the run then ends DONE as if the arm had held')
        left = G.Checks.restored(SEEDED['no-arm-putback']['refuse-foreign-pending'].g)
        self.assertTrue([m for m in left if m.startswith('gicd_ctlr') or m.startswith('isenabler0')], left)
        self.assertTrue([m for m in G.Checks.restored(SEEDED['quarantine-no-putback']['refuse-foreign-pending'].g) if m.startswith('isenabler0')])
        self.assertEqual(calls(RUNS['refuse-foreign-pending']), 0)


class Hygiene(unittest.TestCase):
    def test_the_files_of_this_phase_are_ascii_with_an_spdx_line_within_the_size_limits(self):
        for rel in ('tools/test-sp11-stick-monitor-arm.py', 'tools/sp11_stick_bundle_record_m1.py', 'tools/test-sp11-stick-monitor.c', 'probes/sp11-stick-monitor.c'):
            data = (ROOT / rel).read_bytes()
            with self.subTest(rel):
                self.assertFalse([b for b in data if b > 127], 'ASCII only')
                self.assertIn(b'SPDX-License-Identifier: MIT', data[:400])
                self.assertLess(len(data), 51200)
                self.assertLess(data.count(b'\n'), 1500)


if __name__ == '__main__':
    unittest.main()
