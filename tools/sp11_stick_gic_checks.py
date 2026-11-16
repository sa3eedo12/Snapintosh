# SPDX-License-Identifier: MIT
"""What the host-harness tests of the SP11 GIC and stop stage share (tools/test-sp11-stick-gic.py and tools/test-sp11-stick-gic-record.py;
docs/SP11-STICK-GIC.md section 9), on top of tools/sp11_stick_gic_testlib.py (the record, the model's output, the properties of a run) and
tools/sp11_stick_wrapper_testlib.py (the synthetic bundle and the base harness): the module fixtures of a test file (its own scratch
directory below build/, the two synthetic bundles, the harness variants), the scenario table of the main run with the model configuration of
each, the properties every run is held to, and the lines the page and the reporter must show, written from the document and not from the C.
Nothing here touches hardware. EXPERIMENTAL; hardware NOT RUN."""
import concurrent.futures
import dataclasses
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_gic_testlib as G  # noqa: E402
import sp11_stick_wrapper_testlib as T  # noqa: E402
from sp11_stick_wrapper_checks import Checks as WC, expected_reporter_lines, full_report, hexs  # noqa: E402
from sp11_stick_wrapper_testlib import E, sb  # noqa: E402

STAGE = sb.STAGE
OUT = G.OUT
FIX, RUNS = {}, {}
HZ = 19200000
FAKE_VECTORS, OLD_VBAR = 0xFEED0800, 0xCE879000      # the model's: the table the windows install, and the runner's that was there
OLD_HCR, OLD_SCTLR1, OLD_VBAR1 = 0x88000010, 0x30D00980, 0x7B000000      # the model's and the unit's (OBSERVED at the payload call) EL1 state
SEEDS = {'no-save': 'NO_SAVE', 'stray-store': 'STRAY_STORE', 'no-eoi': 'NO_EOI', 'timer-on': 'TIMER_ON', 'no-irq-mask': 'NO_IRQ_MASK',
         'store-first': 'STORE_FIRST', 'no-next': 'NO_NEXT', 'no-restore': 'NO_RESTORE', 'final-ack': 'FINAL_ACK', 'no-rearm': 'NO_REARM',
         'no-imo': 'NO_IMO', 'no-el1-restore': 'NO_EL1_RESTORE', 'no-next-el1': 'NO_NEXT_EL1', 'unbounded-el1': 'UNBOUNDED_EL1', 'no-settle': 'NO_SETTLE',
         'clear-others': 'CLEAR_OTHERS', 'keep-foreign': 'KEEP_FOREIGN_DEADLINE', 'restore-ctl-first': 'RESTORE_CTL_FIRST'}
PIN = {n: o for n, o, _, _ in sb.PIN_LAYOUT}
RECORD_AT = T.World.REPORT + G.REC_AT
WINDOW_AT = T.World.REPORT + sb.REPORT_OUT
EDGE26 = 2 << (2 * (26 - 16))                          # ICFGR1's field of INTID 26 reading edge-triggered


def runnable():
    return bool(FIX.get('tree') and T.find_tool('clang'))


def exe_of(variant='base'):
    if variant == 'base':
        return G.harness(FIX['tree'])
    return G.harness(FIX['tree'], variant, defines=[f'-DSP11_STICK_GIC_SEED_{SEEDS[variant]}'])


@dataclasses.dataclass
class Run:
    """One scenario: its world, the monitor's and the reporter's results, and what the model printed."""
    name: str
    w: object
    results: list
    g: object

    @property
    def mon(self):
        return self.results[0]

    @property
    def rep(self):
        return self.results[1] if len(self.results) > 1 else None

    @property
    def rec(self):
        return self.g.r()


def pin_patch(field, value, size=8):
    """A TAMPER line that changes a PIN.DAT field once the line of the last stage before the GIC stage is on the page."""
    return f'TAMPER STAGE=COPIED {T.World.PIN + PIN[field]:#x} {value.to_bytes(size, "little").hex()}'


def batch(cases, variant='base', runs=('snapmon', 'snaprep')):
    """{name: Run} for {name: dict(gic=[...], extra=[...], between=[...], bundle=..., runs=...)}: one harness process, the scenarios in turn."""
    texts, worlds = [], {}
    for name, c in cases.items():
        worlds[name] = T.World(FIX[c.get('bundle', 'gic')], FIX['scratch'])
        texts.append((name, G.scenario(name, worlds[name], c.get('gic', ()), c.get('runs', runs), c.get('extra', ()), c.get('between', ()))))
    out = G.run(exe_of(variant), texts)
    return {name: Run(name, worlds[name], out.results[name], out.gic[name]) for name in cases}


# The scenarios of the main run: each is a line or two of the model's configuration (tools/test-sp11-stick-gic.c). The default is the unit as read
# by the first boots (GICD_CTLR 0x10, two security states, nothing enabled); UNIT is the state boot 12 found (OBSERVED, one unit, from a photo of the
# page): the firmware had enabled SGI 0 and PPIs 23, 25, 26, 27 and 30, PPI 26 at priority 0xA0, all PPIs level, PPI 26 neither pending nor active.
# The EL2 timer (CNTHP) has never been read on the unit: the model's CTL is bits 1:0 and ISTATUS is derived from the deadline, so a CTL of 1 with the
# default deadline 0 reads 0x5 (enabled, unmasked, asserting), a CTL of 3 reads 0x7 and a CTL of 1 with a far deadline reads 0x1. An asserting timer
# also shows as PPI 26 pending, and the PPI check comes first; HIDE_PEND is the non-secure view that cannot see that bit (an interrupt that is Secure).
FUTURE_CVAL = 0xFFFFFFFFFFFF
UNIT = ['TYPER 0xF87F051E', 'IIDR 0x4010438', 'RTYPER 0xD0000E7', 'ISENABLER0 0x4E800001', 'PRIO 0xA0']
UNIT_LINES = ('SNAPMON GIC GICD CTLR=0X10 ARE=1 DS=0 G1A=0 NEED_G=1 TYPER=0XF87F051E IIDR=0X4010438',
              'SNAPMON GIC GICR IDX=0 TYPER=0XD0000E7 WAKER=0X0 ISEN=0X4E800001 PEND=0 ACT=0')
CASES = {
    'happy': {},
    'unit-boot12': dict(gic=UNIT),
    'happy-enabled': dict(gic=['ISENABLER0 0x4000000']),
    'happy-other-enabled': dict(gic=['ISENABLER0 0x40000000']),
    'happy-ds1': dict(gic=['CTLR 0x50']),
    'happy-g-on': dict(gic=['CTLR 0x13']),
    'happy-edge': dict(gic=[f'CFG1 {EDGE26:#x}']),
    'happy-eoimode': dict(gic=['ICCCTLR 2']),
    'happy-prio': dict(gic=['PRIO 0x80', 'PMR 0xF0', 'BPR1 2', 'IGRPEN1 1']),
    'happy-frame3': dict(gic=['AFF 0 3', 'AFF 3 0']),
    'happy-other-pend': dict(gic=['ISPENDR0 0x8000000']),
    'happy-delay': dict(gic=['DELAY 5000']),
    'happy-late': dict(gic=['DELAY 100000']),
    'early': dict(gic=['DELAY -100000']),
    'happy-kept': dict(gic=['SPSR_KEPT 0x1000']),
    'no-fire': dict(gic=['NO_FIRE']),
    'no-deliver': dict(gic=['NO_DELIVER']),
    'sync': dict(gic=['SYNC_AT 100000']),
    'storm': dict(gic=['STORM']),
    'storm-once': dict(gic=['STORM_ONCE']),
    'storm-after': dict(gic=['STORM_AFTER 100000']),
    'final-stuck': dict(gic=['FINAL_STUCK']),
    'cycle-lost': dict(gic=['DROP_AFTER 1']),
    'final-lost': dict(gic=['DROP_AFTER 3']),
    'hppir-not-26': dict(gic=['HPPIR 27']),
    'no-istatus': dict(gic=['NO_ISTATUS']),
    'intid27': dict(gic=['INTID 27']),
    'intid1023': dict(gic=['INTID 1023']),
    'el1-lost': dict(gic=['EL1_LOST']),
    'el1-fiq': dict(gic=['EL1_VECTOR 10']),
    'el1-serror': dict(gic=['EL1_VECTOR 11']),
    'el1-sync-class': dict(gic=['EL1_SYNC 0x90000004']),
    'el1-hvc2': dict(gic=['EL1_SYNC 0x58000002']),
    'el1-intid': dict(gic=['EL1_INTID 27']),
    'el1-no-imo': dict(gic=['HCR_IGNORE_IMO']),
    'pct-trapped': dict(gic=['CNTHCTL 2']),
    'el1-image': dict(extra=[f'AT {G.FAKE_EL1_VECTORS:#x} 0x850 unmapped']),
    'el4': dict(gic=['CURRENTEL_LATE 4']),
    'spsel0': dict(gic=['SPSEL 0']),
    'daif-i': dict(gic=['DAIF 0x340']),
    'daif-f': dict(gic=['DAIF 0x380']),
    'imo': dict(extra=['REG hcr 0x88000000']),
    'pin-gicd': dict(extra=[pin_patch('gicd_base', 0x17010000)]),
    'pin-first': dict(extra=[pin_patch('gicr_first', 0x17090000)]),
    'pin-limit': dict(extra=[pin_patch('gicr_limit', 0x17370000)]),
    'pin-stride': dict(extra=[pin_patch('gicr_stride', 0x20000)]),
    'pin-intid': dict(extra=[pin_patch('timer_intid', 27, 4)]),
    'pin-count0': dict(extra=[pin_patch('gicr_count', 0, 4)]),
    'ctlr-0': dict(gic=['CTLR 0']),
    'ctlr-bit8': dict(gic=['CTLR 0x110']),
    'ctlr-bit5': dict(gic=['CTLR 0x20']),
    'ctlr-rwp': dict(gic=['CTLR 0x80000010']),
    'no-frame': dict(gic=['AFF 0 99']),
    'asleep': dict(gic=['WAKER 2']),
    'children-asleep': dict(gic=['WAKER 4']),
    'owned-pending': dict(gic=['ISPENDR0 0x4000000']),
    'owned-active': dict(gic=['ISACTIVER0 0x4000000']),
    'unit-pending': dict(gic=[*UNIT, 'ISPENDR0 0x4000000']),
    'unit-active': dict(gic=[*UNIT, 'ISACTIVER0 0x4000000']),
    'sre-clear': dict(gic=['SRE 0xE']),
    'timer-asserted': dict(gic=['CTL 1', 'HIDE_PEND']),
    'timer-asserted-pending': dict(gic=['CTL 1']),
    'happy-timer-masked': dict(gic=['CTL 3']),
    'happy-timer-future': dict(gic=['CTL 1', f'CVAL {FUTURE_CVAL:#x}']),
    'happy-timer-off': dict(gic=['CTL 0', 'CVAL 0x123456789']),
    'timer-expires': dict(gic=['CTL 1', 'CVAL 19201000']),      # one second ahead of the model's first tick (1000): past long before the put-back
    'counter': dict(extra=['TICK 1000 0']),
    'window': dict(extra=[f'AT {RECORD_AT:#x} 0x300 ro']),
    'not-authorised': dict(bundle='plain'),
    'nohold-gicd': dict(gic=['NOHOLD GICD_CTLR']),
    'nohold-pmr': dict(gic=['NOHOLD ICC_PMR_EL1']),
    'nohold-igrpen1': dict(gic=['NOHOLD ICC_IGRPEN1_EL1']),
    'nohold-ctl': dict(gic=['NO_FIRE', 'NOHOLD CNTHP_CTL_EL2']),
    'nohold-vbar': dict(gic=['NOHOLD VBAR_EL2']),
    'nohold-enable': dict(gic=['NOHOLD GICR_ICENABLER0']),
    'nohold-prio': dict(gic=['NOHOLD GICR_IPRIORITYR']),
    'nohold-hcr': dict(gic=['NOHOLD HCR_EL2']),
    'nohold-sctlr': dict(gic=['NOHOLD SCTLR_EL1']),
    'nohold-vbar1': dict(gic=['NOHOLD VBAR_EL1']),
    'ignore-gicd': dict(gic=['IGNORE GICD_CTLR']),
    'ignore-enable': dict(gic=['IGNORE GICR_ISENABLER0']),
    'ns-ppi': dict(gic=['NS_PPI 0']),
    'rwp-gicd': dict(gic=['RWP gicd']),
    'rwp-gicr': dict(gic=['RWP gicr']),
    'ignore-igrpen1': dict(gic=['IGNORE ICC_IGRPEN1_EL1']),
    'ignore-pmr': dict(gic=['PMR 0x80', 'IGNORE ICC_PMR_EL1']),
    'ignore-ctl': dict(gic=['IGNORE CNTHP_CTL_EL2']),
    'ignore-vbar1': dict(gic=['IGNORE VBAR_EL1']),
    'ignore-sctlr': dict(gic=['SCTLR1 0x30D00985', 'IGNORE SCTLR_EL1']),
    'extreme': dict(gic=['TYPER 0xFFFFFFFF', 'IIDR 0xFFFFFFFF', 'SRE 0xFFFFFFFFFFFFFFFF', 'ICCCTLR 0xFFFFFFFFFFFFFFFF', 'BPR1 7',
                         'IGRPEN1 0xFFFFFFFFFFFFFFFF', 'PMR 0xFF', 'CTL 2', 'PRIO 0xFF', 'CFG1 0xFFFFFFFF', 'IGROUPR0 0xFFFFFFFF',
                         'VBAR 0xFFFFFFFFFFFFFFFF', 'CVAL 0xFFFFFFFFFFFFFFFF', 'ISPENDR0 0xFBFFFFFF', 'CTLR 0x50']),
}


def set_up(name, cases=None, variants=()):
    """The module fixtures of a test file (call from its setUpModule): a scratch directory of its own, so that two test files can run side by
    side, the synthetic bundles (`gic` authorises the gic class and carries the GIC and stop record, `plain` is the dry run's) and, when clang
    and the q1n1 checkout are there, the base harness and the seeded variants, compiled together, and the run of the scenarios `cases`."""
    G.use_scratch(name)
    shutil.rmtree(T.BUILD, ignore_errors=True)
    FIX['scratch'] = T.BUILD / 'host'
    FIX['scratch'].mkdir(parents=True)
    FIX['tree'] = T.q1n1_tree()
    FIX['fixtures'] = T.Fixtures(FIX['scratch'] / 'fixtures')
    FIX['gic'] = FIX['fixtures'].bundle('gic', T.LOW, stores='ram,el2_sysreg,gic', extra=('--gic-stop',))
    FIX['plain'] = FIX['fixtures'].bundle('plain', T.LOW, stores='ram,el2_sysreg')
    if runnable():
        with concurrent.futures.ThreadPoolExecutor(3) as pool:
            for future in [pool.submit(exe_of, v) for v in ('base', *variants)]:
                future.result()
        RUNS.update(batch(CASES if cases is None else {k: CASES[k] for k in cases}))


def tear_down():
    shutil.rmtree(T.BUILD, ignore_errors=True)


# ---- The properties every run is held to --------------------------------------------------------------------------------------------------
class Held:
    """Properties of a run; each returns its violations (so that a seeded-bad stage can be shown to break the one that matches its fault)."""

    @staticmethod
    def page_text(run):
        """At most 30 lines of panel text, and at most 10 of them new (the stage prints at most ten)."""
        new = sum(1 for t in run.mon.texts() if ' GIC ' in t or 'NEXT GIC' in t)
        return WC.lines(run.mon, sb.WRAPPER_LINES) + ([f'{new} lines are new, the limit is 10'] if new > 10 else [])

    @staticmethod
    def report(run):
        """The head passes the interface's check, and nothing is stored after the copy but REPORT.DAT: page 0 beyond the head stays zero, the
        monitor's window stays untouched and the record is the only other content, in its own slot after the window (REPORTTAIL counts the
        non-zero bytes beyond the head)."""
        mon, w, found = run.mon, run.w, []
        if sb.check_report(full_report(mon.report)):
            found.append(f'the head fails its own check: {sb.error_name(sb.check_report(full_report(mon.report)))}')
        if mon.report_tail != sum(1 for b in run.g.rec if b):
            found.append(f'{mon.report_tail} non-zero bytes beyond the head, the record has {sum(1 for b in run.g.rec if b)}')
        found += [f'store {e.a:#x}+{e.b:#x} outside REPORT.DAT after the copy' for e in mon.of('ST') if e.stage >= STAGE.COPIED and not WC.in_report(w, e.a, e.b)]
        found += [f'store {e.a:#x}+{e.b:#x} beyond the head and outside the record after the copy' for e in mon.of('ST')
                  if e.stage >= STAGE.COPIED and WC.in_report(w, e.a, e.b) and e.a >= w.REPORT + sb.REPORT_HEAD_BYTES
                  and not (w.REPORT + G.REC_AT <= e.a and e.a + e.b <= w.REPORT + G.REC_AT + G.REC_BYTES)]
        found += [f'store {e.a:#x}+{e.b:#x} in the monitor window' for e in mon.of('ST')
                  if w.REPORT + sb.REPORT_OUT < e.a + e.b and e.a < w.REPORT + sb.REPORT_OUT + G.MONITOR_WINDOW]
        return found

    @staticmethod
    def common(run):
        return (WC.fault(run.mon) + G.Checks.stray_writes(run.g) + Held.page_text(run) + Held.report(run) + G.Checks.one_interrupt(run.g) +
                G.Checks.timer_quiet(run.g))

    @staticmethod
    def bounded(run):
        """Window 1 ended on the counter: not before its deadline when nothing arrived, and not many steps after it."""
        r, close = run.rec, next(e for e in run.g.log if e[1] == 'WINDOW_CLOSE')
        return [] if 0 <= close[4] - r.win[0].deadline <= 5000 else [f'the window closed at {close[4]}, its deadline was {r.win[0].deadline}']

    @staticmethod
    def bounded_el1(run):
        """Window 2 ended on the stub's own counter bound (twice the time box from its start) when nothing arrived: not before it, and not many
        steps after it."""
        r, close = run.rec, next(e for e in run.g.log if e[1] == 'EXC_CLOSE')
        return [] if 0 <= close[4] - r.win[1].deadline <= 40000 else [f'the excursion closed at {close[4]}, its bound was {r.win[1].deadline}']


def el2_ok(r):
    """Window 1 passed every check: its four interrupts came and each is marked OK."""
    return all(G.entered(r.irq[k]) and r.irq[k].flags & G.IRQF['OK'] for k in range(4))


def expected_writes(*, ctlr=0x10, prio=0, cfg1=0, igroupr0=0, isen=0, eoimode=0, pmr=0xFF, bpr1=0, igrpen1=0, cval0=0, ctl0=0, cvals, intid=26):
    """The stores of a clean run, in the document's order (docs/SP11-STICK-GIC.md section 4): G, R, C, T and window 1 (three acknowledged
    interrupts, each with the re-arm before its end, and the deadline taken without an acknowledge), the writes of class E, window 2, and the
    put-backs. `cvals` are the deadlines the record names: the arm, the three re-arms and window 2's arm. `isen` is ISENABLER0 as found: the
    PPI's enable is written, and cleared again, only when its bit was clear. `cval0` and `ctl0` are the EL2 timer as found: the arm overwrites
    the deadline, and the put-back writes the deadline, and then the enable bits when it had any, over the timer that is disabled."""
    bit, ds, edge, w = 1 << intid, ctlr >> 6 & 1, 2 << (2 * (intid - 16)), []
    end = [('ICC_EOIR1_EL1', intid)] + ([('ICC_DIR_EL1', intid)] if eoimode else [])
    if not ctlr & 2:
        w.append(('GICD_CTLR', ctlr | 2 | (0 if ds else 1)))
    if ds and not igroupr0 & bit:
        w.append(('GICR_IGROUPR0', igroupr0 | bit))
    if prio != 0x80:
        w.append(('GICR_IPRIORITYR', 0x80))
    if cfg1 & edge:
        w.append(('GICR_ICFGR1', cfg1 & ~edge))
    if not isen & bit:
        w.append(('GICR_ISENABLER0', bit))
    w += [('ICC_PMR_EL1', 0xF0), ('ICC_BPR1_EL1', 0), ('ICC_IGRPEN1_EL1', 1), ('CNTHP_CVAL_EL2', cvals[0]), ('CNTHP_CTL_EL2', 1),
          ('VBAR_EL2', FAKE_VECTORS), ('DAIF', 0)]
    for k in (1, 2, 3):
        w += [('CNTHP_CVAL_EL2', cvals[k]), *end]
    w += [('CNTHP_CTL_EL2', 0), ('DAIF', 1), ('VBAR_EL2', OLD_VBAR)]
    w += [('VBAR_EL1', G.FAKE_EL1_VECTORS), ('SCTLR_EL1', G.EL1_SCTLR), ('HCR_EL2', G.EL1_HCR), ('CNTHP_CVAL_EL2', cvals[4]), ('CNTHP_CTL_EL2', 1),
          ('VBAR_EL2', FAKE_VECTORS), ('CNTHP_CTL_EL2', 0), *end, ('VBAR_EL2', OLD_VBAR)]
    w += [('CNTHP_CTL_EL2', 0), ('HCR_EL2', OLD_HCR), ('SCTLR_EL1', OLD_SCTLR1), ('VBAR_EL1', OLD_VBAR1)]
    if cvals[4] != cval0:
        w.append(('CNTHP_CVAL_EL2', cval0))
    if ctl0 & 3:
        w.append(('CNTHP_CTL_EL2', ctl0 & 3))
    if igrpen1 != 1:
        w.append(('ICC_IGRPEN1_EL1', igrpen1))
    if bpr1 != 0:
        w.append(('ICC_BPR1_EL1', bpr1))
    if pmr != 0xF0:
        w.append(('ICC_PMR_EL1', pmr))
    if not isen & bit:
        w.append(('GICR_ICENABLER0', bit))
    if prio != 0x80:
        w.append(('GICR_IPRIORITYR', prio))
    if cfg1 & edge:
        w.append(('GICR_ICFGR1', cfg1))
    if ds and not igroupr0 & bit:
        w.append(('GICR_IGROUPR0', igroupr0))
    if not ctlr & 2:
        w.append(('GICD_CTLR', ctlr))
    return w


def expected_wrote(*, ctlr=0x10, prio=0, cfg1=0, igroupr0=0, isen=0, **_):
    """The registers a clean run marks as written in the record: the stores of expected_writes and the windows' VBAR_EL2 and DAIF."""
    names = ['ICC_PMR', 'ICC_BPR1', 'ICC_IGRPEN1', 'CNTHP_CVAL', 'CNTHP_CTL', 'VBAR_EL2', 'DAIF', 'VBAR_EL1', 'SCTLR_EL1', 'HCR_EL2']
    if not isen >> 26 & 1:
        names.append('ISENABLER0')
    if not ctlr & 2:
        names.append('GICD_CTLR')
    if ctlr >> 6 & 1 and not igroupr0 >> 26 & 1:
        names.append('IGROUPR0')
    if prio != 0x80:
        names.append('IPRIORITYR')
    if cfg1 & EDGE26:
        names.append('ICFGR1')
    return sum(G.REG_BIT[n] for n in names)


def prefixed(prefix, lines):
    return [f'{prefix} {line}' for line in lines]


# What the wrapper's page showed of the stage, from the final record: the reads that were printed before a refusal, then NEXT, the windows and the end.
EARLY_READS = {OUT.NOT_EL2: (), OUT.SPSEL_NOT_1: (), OUT.DAIF_NOT_MASKED: (), OUT.IMO_CLEAR: (), OUT.PIN_GIC: (), OUT.WINDOW_UNWRITABLE: (),
               OUT.EL1_PCT_TRAPPED: (), OUT.EL1_IMAGE: (), OUT.GICD_VIEW: ('GICD',), OUT.GICR_NOT_FOUND: ('GICD', 'GICR'), OUT.GICR_ASLEEP: ('GICD', 'GICR'),
               OUT.PPI_OWNED: ('GICD', 'GICR'), OUT.SRE_CLEAR: ('GICD', 'GICR', 'CPU'), OUT.TIMER_OWNED: ('GICD', 'GICR', 'CPU'),
               OUT.COUNTER: ('GICD', 'GICR', 'CPU')}


def wrapper_gic_lines(r):
    """The page lines of the stage in order, written from the document's section 6, for the record `r` the run ended with."""
    out, lines = r.outcome, []
    lines += [G.render(r, item) for item in EARLY_READS.get(out, ('GICD', 'GICR', 'CPU'))]
    if out == OUT.COUNTER or out > OUT.EL1_IMAGE:
        lines.append(G.next_stores_line(r))
    if out > OUT.EL1_IMAGE:
        if r.begun & 16:
            lines.append(G.next_window_line(r))
        lines += [x for x in (G.render(r, 'IRQ'), G.render(r, 'DIAG')) if x]
        if el2_ok(r):
            lines.append(G.next_el1_line(r))
        lines += [x for x in (G.render(r, 'EL1'), G.render(r, 'DIAG1'), G.render(r, 'RESTORE')) if x]
    lines.append(G.render(r, 'VERDICT'))
    return lines


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not runnable():
            raise unittest.SkipTest('clang or the q1n1 checkout is missing')

    def holds(self, run, *extra):
        self.assertEqual(Held.common(run), [], run.mon.texts())
        for found in extra:
            self.assertEqual(found, [], run.mon.texts())

    def verdict(self, run, outcome, first=None):
        r = run.rec
        self.assertEqual((G.OUTCOMES[r.outcome], G.OUTCOMES[r.first_outcome]), (G.OUTCOMES[outcome], G.OUTCOMES[outcome if first is None else first]))

    def refused(self, run, outcome, stage, first=None):
        """The run ended in this GIC refusal: the return value, the head, the page's REFUSED line and the verdict (`first`: what the window had
        shown when a put-back that failed hides it)."""
        head = run.mon.head()
        self.assertEqual((run.mon.ret, run.mon.faulted), (sb.RET_REFUSED + E.GIC_NOT_READY, False))
        self.verdict(run, outcome, first)
        self.assertEqual((head['stage'], head['refusal'], head['refusal_detail']), (getattr(STAGE, stage), E.GIC_NOT_READY, outcome))
        self.assertEqual(run.mon.texts().count(f'SNAPMON REFUSED=GIC_NOT_READY STAGE={stage} DETAIL={hexs(outcome)}'), 1)

    def pages(self, run):
        """The wrapper's and the reporter's pages against the lines written independently from the record."""
        mon, rep, r = run.mon, run.rep, run.rec
        at = next(i for i, t in enumerate(mon.texts()) if t.startswith('SNAPMON STAGE=COPIED'))
        want = prefixed('SNAPMON', wrapper_gic_lines(r))
        self.assertEqual(mon.texts()[at + 1:at + 1 + len(want)], want)
        if rep:
            self.assertEqual(rep.texts(), expected_reporter_lines(mon.head()) + prefixed('SNAPREP', G.expected(r, G.REPORTER_ORDER)))
            self.assertEqual(WC.fault(rep) + WC.lines(rep, sb.REPORTER_LINES), [])
            self.assertEqual([f'{k} at {e.a:#x}' for k in ('ST', 'CVAC', 'CIVAC', 'DSB', 'ISB', 'AT', 'MRS') for e in rep.of(k)], [], 'the reporter stores nothing')
            self.assertEqual(rep.ret, 0)
