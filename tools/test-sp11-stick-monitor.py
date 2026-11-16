#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests for the monitor entry of SNAPMON-M1.BIN (docs/SP11-STICK-MONITOR.md section 9), monitor port work item 4b, Phases A and C: what the wrapper does
around the call, host only. H1: a fake monitor (tools/test-sp11-stick-monitor.c) that returns ok, a prepare failure, an EL2 fault status, both
quarantine codes, another value, leaves HCR_EL2, VBAR_EL2 or TPIDR_EL2 changed or stores beyond its window. H2: the order of events (the copy, the
instruction-cache sync, the GIC arm, PREPARED, the call), the exact arguments x0 to x4, the stack, no store to the placement after MONITOR_ENTERED,
GIC writes only to this CPU's frame and only of classes G, R and C. Every check before the call, each refusing before anything but REPORT.DAT is
stored to. H6: the stop timer's interrupts as the monitor takes them (probes/xnuboot/async.c, transcribed in the harness) against the GIC state the
arm half leaves, with the firmware's own state as the unit showed it once (OBSERVED, one unit). Phase C (section 13): the GIC record that the reporter
reads after a failed put-back, the filler below the stack, an unwritable record slot, the tick of the call, and the dirty lines of the harness's model
of a cache. The seeded-bad stages: each test define breaks the check that matches its fault and the real stage passes that check.

    python3 tools/test-sp11-stick-monitor.py
    Q1N1_SP11_TREE=/nonexistent python3 tools/test-sp11-stick-monitor.py      (the harness tests SKIP)

The code under test is the REAL wrapper, GIC arm and put-back, monitor stage and reporter (probes/sp11-stick-*.c built with SNAP_SP11_STICK_MONITOR=1
and SP11_STICK_HOST_TEST=1), linked with the harness under AddressSanitizer and UndefinedBehaviorSanitizer. What the model proves is the stage's order,
bounds, checks and report against the model; it proves nothing about the monitor's code, the trampoline's assembly (tools/test-sp11-stick-monitor-build.py
reads its disassembly) or the unit. No hardware, nothing under /Volumes, no network; everything is written below build/. A SKIP is never a PASS.
EXPERIMENTAL; hardware NOT RUN."""
import concurrent.futures
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_gic_testlib as G  # noqa: E402
import sp11_stick_monitor_testlib as M  # noqa: E402
import sp11_stick_wrapper_testlib as T  # noqa: E402
from sp11_stick_wrapper_checks import Checks as WC, full_report  # noqa: E402
from sp11_stick_wrapper_testlib import E, sb  # noqa: E402

STAGE = sb.STAGE
FIX, RUNS = {}, {}
PIN = {n: o for n, o, _, _ in sb.PIN_LAYOUT}
# The GIC as the unit showed it once, in the read phase of the dry run (OBSERVED, one unit, from a photograph; docs/SP11-STICK-MONITOR.md section 8).
OBSERVED_GIC = ['CTLR 0x10', 'TYPER 0xF87F051E', 'IIDR 0x4010438', 'ISENABLER0 0x4E800001', 'PRIO 0xA0', 'CFG1 0', 'WAKER 0', 'IGROUPR0 0']
WRAPPER_WRITES = {'GICD_CTLR', 'GICR_IGROUPR0', 'GICR_IPRIORITYR', 'GICR_ICFGR1', 'GICR_ISENABLER0', 'GICR_ICENABLER0', 'GICR_ICPENDR0', 'ICC_PMR_EL1',
                  'ICC_BPR1_EL1', 'ICC_IGRPEN1_EL1'}             # classes G, R and C and their put-back: nothing else, whoever the register belongs to
BAD_STATUS = 0xFFFFFFFFFFFFFFA6                                   # a prepare failure: negative (the step is in the word; probes/xnuboot.S OUT_STATUS)
REGS = {'hcr': 0x88000010, 'vbar': 0xCE879000, 'tpidr': 0xCE8A0000}   # the model's values before the call (the harness's defaults)


def params_patch(world, offset, value):
    """TAMPER: a PARAMS word changed once the copy is on the page, so that the stage reads what the copy left in the placement."""
    dest = next(c['dest'] for c in world.bundle.chunks if c['kind'] == sb.KIND_PARAMS)
    return f'TAMPER STAGE=COPIED {dest + offset:#x} {value.to_bytes(8, "little").hex()}'


# scenario name -> (model lines, monitor configuration, extra scenario lines as a function of the world, world overrides as a function of the bundle)
CASES = {
    'happy': {},
    'happy-reported': dict(runs=('snapmon', 'snaprep')),
    'happy-eoimode': dict(gic=['ICCCTLR 2']),
    'happy-observed': dict(gic=OBSERVED_GIC),
    'prep-fail': dict(cfg=f'status={BAD_STATUS:#x}'),
    'bad-esr': dict(cfg='status=0x96000045'),
    'quarantine-own': dict(cfg='ret=0x51554E01'),
    'quarantine-gpu': dict(cfg='ret=0x51554E02'),
    'ret-other': dict(cfg='ret=0x1234'),
    'leave-hcr': dict(cfg='leave=hcr:0x80000000'),
    'leave-vbar': dict(cfg='leave=vbar:0xFEED0800'),
    'leave-tpidr': dict(cfg='leave=tpidr:0x1'),
    'leave-multi': dict(cfg='leave=daif:0x0 leave=cnthctl:0x6000 leave=mdcr:0x100 leave=vttbr:0x1000'),
    'leave-cnthp-enabled': dict(cfg='leave=cnthp:1'),
    'overrun': dict(cfg='overrun=0x10000'),
    'overrun-gap': dict(cfg='overrun=0x10F00'),
    'overrun-record': dict(cfg='overrun=0x11008'),
    'stack-deep': dict(cfg='stack=0x20000'),
    'stack-full': dict(cfg='stackfull=1'),
    'overflow-4': dict(cfg='below=4'),
    'overflow-20000': dict(cfg='below=20000'),
    'overflow-skip-lowest': dict(cfg='stack=0x3FFF8 below=4'),
    'not-restored': dict(cfg='bumpphase=1', gic=['NOHOLD ICC_PMR_EL1'], runs=('snapmon', 'snaprep')),
    'refuse-not-restored': dict(cfg='putphase=1', gic=['IGNORE ICC_IGRPEN1_EL1', 'NOHOLD ICC_PMR_EL1 1'], runs=('snapmon', 'snaprep')),
    'refuse-params-early': dict(extra=lambda w: [params_patch(w, M.PARAMS_EARLY, 8)]),
    'refuse-params-gpu': dict(extra=lambda w: [params_patch(w, M.PARAMS_GPU, 1)]),
    'refuse-params-box0': dict(extra=lambda w: [params_patch(w, M.PARAMS_WATCHDOG, 0)]),
    'refuse-params-box9': dict(extra=lambda w: [params_patch(w, M.PARAMS_WATCHDOG, 9)]),
    'refuse-image-digest': dict(world=lambda b: dict(images={0: M.FAKE_FILE[:-1] + b'\x5a'})),
    'refuse-image-size': dict(world=lambda b: dict(sizes={0: len(M.FAKE_FILE) + 4096, 1: 0x6000})),
    'refuse-window-dirty': dict(world=lambda b: dict(report=b.report[:0x3000] + b'\x01' + b.report[0x3001:])),
    'refuse-filler-dirty': dict(world=lambda b: dict(report=b.report[:M.FILLER_AT + 0x8000] + b'\x01' + b.report[M.FILLER_AT + 0x8001:])),
    'refuse-report-ro': dict(extra=lambda w: [f'AT {w.REPORT + M.STACK_AT:#x} 0x1000 ro']),
    'refuse-record-ro': dict(extra=lambda w: [f'AT {w.REPORT + M.REC_AT:#x} 0x1000 ro']),
    'refuse-gic-sre': dict(gic=['SRE 0']),
    'refuse-gic-active': dict(gic=['ISACTIVER0 0x4000000']),
    'refuse-gic-not-held': dict(gic=['IGNORE ICC_IGRPEN1_EL1']),
    'refuse-gic-group0': dict(gic=['NS_PPI 0']),
    'replay-ok': dict(cfg='replay=1'),
    'replay-eoimode': dict(cfg='replay=1', gic=['ICCCTLR 2']),
    'replay-observed': dict(cfg='replay=1', gic=OBSERVED_GIC),
    'replay-observed-eoimode': dict(cfg='replay=1', gic=[*OBSERVED_GIC, 'ICCCTLR 2']),
    'replay-group0': dict(cfg='replay=1 ns_ppi=0'),
    'replay-foreign-ppi': dict(cfg='replay=1', gic=['HPPIR 27']),
    'replay-no-fire': dict(cfg='replay=1', gic=['NO_FIRE']),
}
SEED_CASES = {'no-params-check': ('refuse-params-early',), 'no-digest': ('refuse-image-digest',), 'no-reg-check': ('leave-hcr',), 'no-ic-sync': ('happy',),
              'stray-gic': ('happy',), 'wrong-args': ('happy',), 'no-s2': ('happy',), 'no-overrun-check': ('overrun-gap',), 'store-after': ('happy',),
              'refuse-after': ('leave-hcr',), 'no-gic-finish': ('not-restored', 'refuse-not-restored'),
              'no-filler-scan': ('overflow-4', 'overflow-skip-lowest'), 'no-filler-precheck': ('refuse-filler-dirty',), 'no-filler-clean': ('overflow-4',),
              'flush-unwritable': ('refuse-record-ro',), 'ticks-early': ('happy',), 'no-window-clean': ('happy',), 'no-record-clean': ('happy',)}


class Run:
    """One scenario: its world, the monitor's and the reporter's results, what the model and the fake monitor printed."""

    def __init__(self, name, w, out, runs):
        self.name, self.w, self.results, self.g = name, w, out.results[name], out.gic[name]
        self.marks, self.replay, self.state, self.recs = out.marks[name], out.replay[name], out.state[name], out.recs[name]
        self.dirty = out.dirty[name]
        self.runs = runs

    @property
    def mon(self):
        return self.results[0]

    @property
    def rep(self):
        return self.results[1] if len(self.results) > 1 else None

    def mark(self, kind):
        return next((m for m in self.marks if m[0] == kind), None)

    @property
    def rec(self):
        """The monitor record as the stage last stored it, decoded independently of the C."""
        raw = self.recs.get('FINAL') or self.recs.get('REFUSED')
        return M.decode(raw) if raw else None


def runnable():
    return bool(FIX.get('tree') and T.find_tool('clang'))


def exe_of(variant='base'):
    if variant == 'base':
        return M.harness(FIX['tree'])
    return M.harness(FIX['tree'], variant, extra=[f'-DSP11_STICK_MONITOR_SEED_{M.SEEDS[variant]}'])


def batch(names, variant='base', runs=('snapmon',)):
    texts, worlds, cfg = [], {}, {}
    for name in names:
        c = CASES[name]
        over = c['world'](FIX['bundle']) if 'world' in c else {}
        w = worlds[name] = M.world(FIX['bundle'], FIX['scratch'], **over)
        texts.append((name, G.scenario(name, w, c.get('gic', ()), c.get('runs', runs), c['extra'](w) if 'extra' in c else ())))
        if 'cfg' in c:
            cfg[name] = c['cfg']
    out = M.run(exe_of(variant), texts, cfg)
    return {name: Run(name, worlds[name], out, runs) for name in names}


def setUpModule():
    M.use_scratch('monitor-host')
    shutil.rmtree(T.BUILD, ignore_errors=True)
    FIX['scratch'] = T.BUILD / 'host'
    FIX['scratch'].mkdir(parents=True)
    FIX['tree'] = T.q1n1_tree()
    FIX['fixtures'] = T.Fixtures(FIX['scratch'] / 'fixtures')
    FIX['bundle'] = FIX['fixtures'].bundle('gic', T.LOW, stores='ram,el2_sysreg,gic', extra=('--gic-stop',))
    if runnable():
        with concurrent.futures.ThreadPoolExecutor(3) as pool:
            for future in [pool.submit(exe_of, v) for v in ('base', *M.SEEDS)]:
                future.result()
        RUNS.update(batch(list(CASES)))


def tearDownModule():
    shutil.rmtree(T.BUILD, ignore_errors=True)


# ---- The properties a run is held to --------------------------------------------------------------------------------------------------
def head_of(run):
    return run.mon.head()


class Held:
    """Properties of a run; each returns its violations (so that a seeded-bad stage can be shown to break the one that matches its fault)."""

    @staticmethod
    def page(run):
        return WC.fault(run.mon) + WC.lines(run.mon, sb.WRAPPER_LINES)

    @staticmethod
    def head(run):
        """The head passes the interface's own check: no refusal after MONITOR_ENTERED (the frozen sp11sb_report_check)."""
        code = sb.check_report(full_report(run.mon.report))
        return [f'the head fails its own check: {sb.error_name(code)}'] if code else []

    @staticmethod
    def stores(run):
        """After MONITOR_ENTERED the wrapper stores only inside REPORT.DAT, never in the placement and never in the monitor's window; before the call
        its stores are REPORT.DAT's too (the copy aside)."""
        w, found = run.w, []
        place = (w.place[0], w.place[0] + w.place[1])
        for e in run.mon.of('ST'):
            inside = WC.in_report(w, e.a, e.b)
            if e.stage > STAGE.COPYING and not inside:
                found.append(f'store {e.a:#x}+{e.b:#x} outside REPORT.DAT at stage {e.stage}')
            if e.stage >= STAGE.MONITOR_ENTERED and place[0] <= e.a < place[1]:
                found.append(f'store {e.a:#x} to the placement after MONITOR_ENTERED')
            if inside and w.REPORT + sb.REPORT_OUT < e.a + e.b and e.a < w.REPORT + sb.REPORT_OUT + M.WINDOW:
                found.append(f'store {e.a:#x}+{e.b:#x} in the monitor window')
        return found

    @staticmethod
    def gic_writes(run):
        """Before and after the call the GIC writes are the wrapper's: classes G, R and C only, to this CPU's frame; none of CNTHP, VBAR_EL2, HCR_EL2
        or an EL1 register. The writes between the call and its return are the fake monitor's (the replay) and are not held to this."""
        call, ret = run.mark('CALL'), run.mark('RET')
        lo, hi = (call[1], ret[1]) if call and ret else (len(run.g.log), len(run.g.log))
        mine = run.g.log[:lo] + run.g.log[hi:]
        return ([f'the wrapper wrote {name}' for kind, name, *_ in mine if kind == 'W' and name not in WRAPPER_WRITES] +
                [f'the model refused an access: {v}' for v in run.g.viol])

    @staticmethod
    def order(run):
        """The copy, then IC IALLUIS (no GIC access yet), then the arm's writes, then the call, with the call's arguments exact and its stack in
        REPORT.DAT's tail."""
        ic, call, found, w = run.mark('IC'), run.mark('CALL'), [], run.w
        if not ic or not call:
            return ['no IC IALLUIS or no call']
        copies = [e.index for e in run.mon.of('ST') if e.stage == STAGE.COPYING and w.place[0] <= e.a < w.place[0] + w.place[1]]
        if copies and max(copies) >= ic[2] - 1:
            found.append('the instruction cache was dropped before the last copy store')
        if ic[1] != 0 or not ic[2] < call[2]:
            found.append(f'IC IALLUIS came after a GIC access or after the call: {ic[:3]} {call[:3]}')
        arm = [i for i, (kind, *_rest) in enumerate(run.g.log) if kind == 'W' and i < call[1]]
        if not arm or min(arm) < ic[1]:
            found.append('no GIC store between the sync and the call, or one before the sync')
        stages = [e.stage for e in run.mon.events[:call[2]]]
        if STAGE.PREPARED not in stages or STAGE.MONITOR_ENTERED not in stages or stages.index(STAGE.PREPARED) > stages.index(STAGE.MONITOR_ENTERED):
            found.append('PREPARED and MONITOR_ENTERED are not both in REPORT.DAT before the call, in that order')
        return found

    @staticmethod
    def args(run):
        """x0 REPORT.DAT + 0x1000, x1 and x2 the pinned work arena, x3 the PARAMS chunk's destination, x4 zero; SP the 16-byte aligned top of REPORT.DAT."""
        call, w = run.mark('CALL'), run.w
        params = next(c['dest'] for c in w.bundle.chunks if c['kind'] == sb.KIND_PARAMS)
        want = (w.REPORT + sb.REPORT_OUT, w.bundle.fields['work_base'], w.bundle.fields['work_size'], params, 0, w.REPORT + sb.REPORT_BYTES)
        return [] if call and tuple(call[4:10]) == want else [f'call arguments {None if not call else [hex(v) for v in call[4:10]]}, expected {[hex(v) for v in want]}']

    @staticmethod
    def common(run):
        return Held.page(run) + Held.head(run) + Held.stores(run) + Held.gic_writes(run)

    @staticmethod
    def refused_before_call(run, code):
        """A check before the call refused: GIC_NOT_READY with this detail, stage below MONITOR_ENTERED, no call, nothing stored but REPORT.DAT."""
        found = WC.fault(run.mon) + ([f'the monitor was called'] if run.mark('CALL') else [])
        h = head_of(run)
        if h['refusal'] != E.GIC_NOT_READY or h['refusal_detail'] != code or h['stage'] >= STAGE.MONITOR_ENTERED:
            found.append(f'head: stage {h["stage"]} refusal {h["refusal"]} detail {h["refusal_detail"]:#x}, expected {code:#x} before the call')
        found += [f'store {e.a:#x} outside REPORT.DAT' for e in run.mon.of('ST') if e.stage > STAGE.COPYING and not WC.in_report(run.w, e.a, e.b)]
        return found + Held.stores(run)

    @staticmethod
    def overflow_seen(run):
        """The monitor wrote below the stack's area: the page says STACK FULL and what it wrote is zero again (the dump counts every non-zero byte beyond
        the head, in the stage's view; the records and the monitor's entered word are all that may be left)."""
        regs = next((t for t in run.mon.texts() if t.startswith('SNAPMON MON REGS')), '')
        left = sum(1 for b in run.recs['FINAL'] if b) + sum(1 for b in run.g.rec if b) + 1
        return (([] if 'STACK FULL' in regs else ['the page does not say STACK FULL']) +
                ([] if run.mon.report_tail == left else [f'{run.mon.report_tail} non-zero bytes beyond the head, the records and the entered word are {left}']))

    @staticmethod
    def record_untouched(run):
        """A refusal for REPORT_UNWRITABLE stores nothing into the monitor's record slot, which is what was found unwritable."""
        lo = run.w.REPORT + M.REC_AT
        return [f'store {e.a:#x}+{e.b:#x} into the record slot' for e in run.mon.of('ST') if lo < e.a + e.b and e.a < lo + M.REC_BYTES]

    @staticmethod
    def ticks_at_call(run):
        """TICKS is the call: ticks_call of the final record is the first counter read after the MON CALL line (the head's store is before it too), and a
        later read than the CALLING record's, which is all a hang leaves."""
        at = next((i for i, t in enumerate(run.mon.texts()) if t.startswith('SNAPMON MON CALL')), None)
        final, calling = run.rec, M.decode(run.recs['CALL']) if run.recs.get('CALL') else None
        if at is None or final is None or calling is None or at not in run.g.line_ticks:
            return ['no MON CALL line, no final or CALLING record, or no counter read after the line']
        found = [] if final.ticks_call == run.g.line_ticks[at] else [f'ticks_call {final.ticks_call} is not the first counter read after the MON CALL line ({run.g.line_ticks[at]})']
        return found + ([] if calling.ticks_call < final.ticks_call else [f'the CALLING record holds {calling.ticks_call}, the final one {final.ticks_call}: not a later read'])

    @staticmethod
    def clean(run):
        """What a restart reads is what the stage stored, in the model's cache: at the CALL mark the head and both records are clean, at a refusal the
        record, and at the FINAL mark nothing of REPORT.DAT is dirty (the window after the call, the records after every store, the stack and any filler
        word after the scan). A model of a cache: it shows what the stage cleaned, not how Oryon's caches behave."""
        call, final, refused = (run.dirty.get(k) for k in ('CALL', 'FINAL', 'REFUSED'))
        found = [f'{call[p]} dirty lines of the {p} at the CALL mark' for p in ('head', 'gic', 'rec') if call and call[p]]
        found += [f'{refused["rec"]} dirty lines of the record at the refusal'] if refused and refused['rec'] else []
        found += [f'{final["total"]} dirty lines of REPORT.DAT at the FINAL mark: {final}'] if final and final['total'] else []
        return found + ([] if call or refused else ['no observation point'])


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not runnable():
            raise unittest.SkipTest('no clang or no q1n1 checkout (Q1N1_SP11_TREE): the harness is not built')

    def holds(self, found, what=''):
        self.assertEqual(found, [], what)


class Returns(Base):
    """H1: what the fake monitor returns, and what the wrapper makes of it."""

    def test_happy(self):
        r = RUNS['happy']
        self.holds(Held.common(r) + Held.order(r) + Held.args(r))
        h = head_of(r)
        self.assertEqual((r.mon.ret, h['stage'], h['refusal'], h['monitor_ret'], h['monitor_status'], h['monitor_entered']), (0, STAGE.DONE, 0, 0, 0, 1))
        self.assertEqual((h['monitor_out'], h['work_base'], h['work_size']), (r.w.REPORT + sb.REPORT_OUT, r.w.bundle.fields['work_base'], sb.WORK_BYTES))
        self.assertTrue(h['ticks_copied'] <= h['ticks_launch'] < h['ticks_return'])
        self.assertEqual(r.state['ic'], 1, 'IC IALLUIS exactly once')
        rec_nonzero = sum(1 for b in r.recs['FINAL'] if b) + sum(1 for b in r.g.rec if b) + 1       # the record, the GIC record and the window's entered word
        self.assertEqual(r.mon.report_tail, rec_nonzero, 'every non-zero byte beyond the head is the records or the monitor window')

    def test_every_run_holds_the_common_properties(self):
        for name, r in RUNS.items():
            if name.startswith('refuse'):
                continue
            with self.subTest(name):
                if head_of(r)['refusal']:
                    self.skipTest('the GIC arm refused this state: the relaxation of its precondition (WI 8) is not in this tree')
                self.holds(Held.common(r) + Held.order(r) + Held.args(r))

    def test_results_pass_through(self):
        for name, ret, status in (('prep-fail', 0, BAD_STATUS), ('bad-esr', 0, 0x96000045), ('quarantine-own', 0x51554E01, 0), ('quarantine-gpu', 0x51554E02, 0),
                                  ('ret-other', 0x1234, 0)):
            with self.subTest(name):
                h = head_of(RUNS[name])
                self.assertEqual((h['stage'], h['refusal'], h['monitor_ret'], h['monitor_status']), (STAGE.DONE, 0, ret, status))

    def test_no_refusal_after_the_call(self):
        for name in CASES:
            if name.startswith('refuse'):
                continue
            with self.subTest(name):
                self.assertEqual(head_of(RUNS[name])['refusal'], 0)
                self.assertEqual(RUNS[name].mon.ret, 0, 'payload_entry returns 0 whatever the monitor returned')


class Registers(Base):
    """H1: a monitor that leaves a register changed is a finding on the page, never a refusal."""

    def test_restored_is_said(self):
        self.assertIn('SNAPMON MON REGS RESTORED=YES', RUNS['happy'].mon.texts())

    def test_changes_are_named(self):
        for name, names in (('leave-hcr', ['HCR_EL2']), ('leave-vbar', ['VBAR_EL2']), ('leave-tpidr', ['TPIDR_EL2']),
                            ('leave-multi', ['DAIF', 'CNTHCTL_EL2', 'MDCR_EL2']), ('leave-cnthp-enabled', ['CNTHP_CTL_EL2'])):
            with self.subTest(name):
                line = next(t for t in RUNS[name].mon.texts() if t.startswith('SNAPMON MON REGS'))
                self.assertIn('RESTORED=NO', line)
                for n in names:
                    self.assertIn(n, line)
                self.holds(Held.common(RUNS[name]))

    def test_vttbr_is_the_fourth_changed_name_and_is_only_counted(self):
        line = next(t for t in RUNS['leave-multi'].mon.texts() if t.startswith('SNAPMON MON REGS'))
        self.assertIn('CHANGED=0X', line)
        self.assertNotIn('VTTBR_EL2', line, 'the line names three')


class Window(Base):
    def test_overrun_into_each_slot_is_seen(self):
        for name in ('overrun', 'overrun-gap', 'overrun-record'):
            with self.subTest(name):
                self.assertIn('WINDOW OVERRUN', next(t for t in RUNS[name].mon.texts() if t.startswith('SNAPMON MON REGS')))
                self.holds(Held.common(RUNS[name]))

    def test_no_overrun_is_not_reported(self):
        self.assertNotIn('WINDOW OVERRUN', ' '.join(RUNS['happy'].mon.texts()))

    def test_stack(self):
        self.assertIn('STACK=1024/262144', next(t for t in RUNS['happy'].mon.texts() if t.startswith('SNAPMON MON RET')))
        self.assertIn('STACK=131072/262144', next(t for t in RUNS['stack-deep'].mon.texts() if t.startswith('SNAPMON MON RET')))
        self.assertIn('STACK FULL', next(t for t in RUNS['stack-full'].mon.texts() if t.startswith('SNAPMON MON REGS')))
        for name in ('happy', 'stack-deep', 'stack-full'):
            r = RUNS[name]
            self.assertEqual(r.mon.report_tail, sum(1 for b in r.recs['FINAL'] if b) + sum(1 for b in r.g.rec if b) + 1,
                             f'{name}: the stack area is zeroed again: only the records and the entered word are left beyond the head')


class Overflow(Base):
    """Finding 5 of the review: a stack that outgrows its area writes below it, into the 184 KiB of REPORT.DAT between the records and the area, and a frame
    that skips the area's lowest word reaches it without changing that word. The stage must say STACK FULL for either and leave the filler zero."""

    def test_a_word_below_the_area_is_seen_and_zeroed(self):
        for name in ('overflow-4', 'overflow-20000', 'overflow-skip-lowest'):
            with self.subTest(name):
                self.holds(Held.overflow_seen(RUNS[name]) + Held.common(RUNS[name]))

    def test_the_skipping_frame_is_seen_by_the_filler_alone(self):
        ret = next(t for t in RUNS['overflow-skip-lowest'].mon.texts() if t.startswith('SNAPMON MON RET'))
        self.assertIn(f'STACK={M.STACK_BYTES - 8}/{M.STACK_BYTES}', ret, 'the area scan saw one free word: STACK FULL did not come from the area')

    def test_a_clean_filler_is_not_flagged(self):
        for name in ('happy', 'stack-deep'):
            self.assertNotIn('STACK FULL', next(t for t in RUNS[name].mon.texts() if t.startswith('SNAPMON MON REGS')))


class Timing(Base):
    """Finding 10 of the review: TICKS is the call, not also the MON CALL line and the head's store before it."""

    def test_ticks_call_is_the_read_after_the_call_line(self):
        self.holds(Held.ticks_at_call(RUNS['happy']))


class Cache(Base):
    """Finding 6a of the review: the harness models a cache with a dirty flag per 64-byte line, and the stage cleans what it stores. What a restart reads
    is DRAM's. This is the model's cache, not the Oryon's: it shows that the stage cleans, and nothing about when a real line is evicted."""

    def test_clean_at_the_call_the_refusals_and_the_final_mark(self):
        for name, r in RUNS.items():
            if name in ('refuse-report-ro', 'refuse-record-ro', 'overrun-gap') or (not r.dirty.get('CALL') and not r.dirty.get('REFUSED')):
                continue
            with self.subTest(name):
                self.holds(Held.clean(r))

    def test_a_store_beyond_the_window_is_reported_and_not_cleaned_by_the_stage(self):
        """The one line the stage leaves dirty is the monitor's own stray word in the gap after the GIC record: it is not the stage's to clean, and
        the other two overruns land in the records, which the stage stores again."""
        self.assertEqual(RUNS['overrun-gap'].dirty['FINAL']['total'], 1)
        for name in ('overrun', 'overrun-record'):
            self.assertEqual(RUNS[name].dirty['FINAL']['total'], 0, name)

    def test_the_model_does_see_a_dirty_line(self):
        """The check is not vacuous: before the call the stack area is filled and not cleaned, so the model shows dirty lines there."""
        self.assertGreater(RUNS['happy'].dirty['CALL']['stack'], 0)


class Checks(Base):
    """Every check before the call refuses with GIC_NOT_READY and an SM_OUT_* detail, before anything but REPORT.DAT is stored to."""

    def test_refusals(self):
        for name, code in (('refuse-params-early', M.OUT.PARAMS_EARLY_STOP), ('refuse-params-gpu', M.OUT.PARAMS_GPU), ('refuse-params-box0', M.OUT.PARAMS_BOX),
                           ('refuse-params-box9', M.OUT.PARAMS_BOX), ('refuse-image-digest', M.OUT.IMAGE_DIGEST), ('refuse-image-size', M.OUT.IMAGE_SIZE),
                           ('refuse-window-dirty', M.OUT.WINDOW_DIRTY), ('refuse-filler-dirty', M.OUT.WINDOW_DIRTY),
                           ('refuse-report-ro', M.OUT.REPORT_UNWRITABLE), ('refuse-record-ro', M.OUT.REPORT_UNWRITABLE)):
            with self.subTest(name):
                r = RUNS[name]
                self.holds(Held.refused_before_call(r, code))
                self.assertEqual(r.state, {}, 'the monitor was not called')
                self.assertEqual([e for e in r.g.log if e[0] == 'W'], [], 'no GIC register was written')

    def test_an_unwritable_report_is_printed_and_the_record_slot_is_not_stored_to(self):
        for name in ('refuse-report-ro', 'refuse-record-ro'):
            with self.subTest(name):
                self.assertTrue(any(t.startswith('SNAPMON MON VERDICT=REPORT_UNWRITABLE') for t in RUNS[name].mon.texts()), RUNS[name].mon.texts())
                self.holds(Held.record_untouched(RUNS[name]))

    def test_the_other_refusals_do_store_the_record(self):
        """The control of the one above: the record slot is stored for every other refusal, so that the reporter names it after a restart."""
        for name in ('refuse-params-gpu', 'refuse-window-dirty', 'refuse-filler-dirty', 'refuse-image-digest'):
            with self.subTest(name):
                self.assertTrue(M.decode(RUNS[name].recs['REFUSED']), name)

    def test_arm_refusals_carry_the_gic_outcome(self):
        for name, code in (('refuse-gic-sre', G.OUT.SRE_CLEAR), ('refuse-gic-active', G.OUT.PPI_OWNED), ('refuse-gic-not-held', G.OUT.C_NOT_HELD),
                           ('refuse-gic-group0', G.OUT.R_NOT_HELD)):
            with self.subTest(name):
                self.holds(Held.refused_before_call(RUNS[name], code))

    def test_a_refused_arm_puts_back_what_it_wrote(self):
        r = RUNS['refuse-gic-not-held']
        self.assertTrue([e for e in r.g.log if e[0] == 'W'], 'the C class had begun')
        for key in ('pmr', 'bpr1', 'gicd_ctlr', 'isenabler0', 'prio', 'igroupr0', 'icfgr1'):
            self.assertEqual(r.g.state[key][0], r.g.state[key][1], f'{key} is back')

    def test_put_back_failure_is_loud_and_not_a_refusal(self):
        r = RUNS['not-restored']
        self.assertEqual(head_of(r)['refusal'], 0)
        self.assertTrue(any('GIC RESTORE CLEAN=NO' in t for t in r.mon.texts()), r.mon.texts())


class Contract(Base):
    """H6: the stop timer's interrupts as the monitor takes them, against the state the arm half leaves."""

    def ticks(self, name):
        return [x for x in RUNS[name].replay if x['kind'] == 'TICK']

    def test_ticks_are_acknowledged_cleared_and_ended(self):
        for name in ('replay-ok', 'replay-eoimode', 'replay-observed', 'replay-observed-eoimode'):
            with self.subTest(name):
                r = RUNS[name]
                if head_of(r)['refusal']:
                    self.skipTest('the arm refused the firmware state: the relaxation of the precondition (WI 8) is not in this tree')
                t = self.ticks(name)
                self.assertEqual([(x['hppir'], x['iar'], x['post'], x['act']) for x in t], [(26, 26, 1023, 0)] * 3)
                final = [x for x in r.replay if x['kind'] == 'FINAL']
                self.assertEqual([(x['hppir'], x['pend'], x['act']) for x in final], [(1023, 0, 0)], 'the deadline taken without an acknowledge leaves nothing')
                self.assertFalse([x for x in r.replay if x['kind'] == 'ASYNC'])

    def test_the_replay_can_fail(self):
        for name in ('replay-group0', 'replay-foreign-ppi', 'replay-no-fire'):
            with self.subTest(name):
                self.assertTrue([x for x in RUNS[name].replay if x['kind'] == 'ASYNC'], f'{name} must end in EV_ASYNC, as the monitor stops on a tick that is not PPI 26')

    def test_the_observed_state_is_armed_or_refused_by_name(self):
        r = RUNS['happy-observed']
        h = head_of(r)
        if h['refusal']:
            self.assertEqual((h['refusal'], h['refusal_detail']), (E.GIC_NOT_READY, G.OUT.PPI_OWNED), 'the strict precondition names itself')
        else:
            self.assertFalse([e for e in r.g.log if e[0] == 'W' and e[1] == 'GICR_ISENABLER0'], 'PPI 26 was enabled already: ISENABLER0 is not written')


class GicRecord(Base):
    """Finding 2 of the review: sw_gic_put_back stores the GIC record when it begins and the registers that did not go back are only in the stage's
    copy. SNAPREP-M1 prints the stored copy after a restart, so the stage must store it again once the put-back is done, on the success path and on
    the refusal path alike, and the reporter's GIC RESTORE line must be the wrapper's."""

    @staticmethod
    def line(texts, who):
        return next((t[len(who) + 1:] for t in texts if t.startswith(f'{who} GIC RESTORE')), None)

    @staticmethod
    def agree(run):
        """The violations: the reporter, reading REPORT.DAT as the wrapper left it, prints another GIC RESTORE line than the wrapper printed."""
        wrapper, reporter = GicRecord.line(run.mon.texts(), 'SNAPMON'), GicRecord.line(run.rep.texts(), 'SNAPREP') if run.rep else None
        if wrapper is None or reporter is None:
            return [f'a GIC RESTORE line is missing: the wrapper printed {wrapper!r}, the reporter {reporter!r}']
        return [] if wrapper == reporter else [f'the wrapper printed {wrapper!r} and the reporter {reporter!r}']

    def test_a_put_back_that_failed_is_said_by_the_reporter_as_the_wrapper_said_it(self):
        for name in ('not-restored', 'refuse-not-restored'):
            with self.subTest(name):
                run = RUNS[name]
                wrapper = self.line(run.mon.texts(), 'SNAPMON')
                self.assertIn('CLEAN=NO', wrapper, 'the scenario must be one where a register does not go back')
                self.assertRegex(wrapper, r'FAILED=0X[1-9A-F][0-9A-F]* ')
                self.holds(self.agree(run))
                self.assertIn('CLEAN=NO', self.line(run.rep.texts(), 'SNAPREP'))

    def test_the_refusal_path_carries_the_put_back_in_its_detail_and_stays_a_refusal(self):
        self.holds(Held.refused_before_call(RUNS['refuse-not-restored'], G.OUT.NOT_RESTORED))

    def test_a_clean_put_back_is_said_clean_by_both(self):
        self.assertIn('CLEAN=YES', self.line(RUNS['happy-reported'].mon.texts(), 'SNAPMON'))
        self.holds(self.agree(RUNS['happy-reported']))
        self.assertIn('CLEAN=YES', self.line(RUNS['refuse-gic-not-held'].mon.texts(), 'SNAPMON'))

    def test_the_stored_record_is_finished(self):
        """The GIC record in REPORT.DAT after the stage: sub-stage DONE (11), the outcome and the first outcome as the dry run's gic_finish sets them."""
        for name, outcome, first in (('happy', 0, 0), ('not-restored', G.OUT.NOT_RESTORED, 0), ('refuse-not-restored', G.OUT.NOT_RESTORED, G.OUT.C_NOT_HELD),
                                     ('refuse-gic-not-held', G.OUT.C_NOT_HELD, G.OUT.C_NOT_HELD)):
            with self.subTest(name):
                raw = RUNS[name].g.rec
                got = {k: int.from_bytes(raw[o:o + 4], 'little') for k, o in (('outcome', 0x10), ('substage', 0x14), ('first_outcome', 0x2C))}
                self.assertEqual(got, {'outcome': outcome, 'substage': 11, 'first_outcome': first}, name)


class Seeded(Base):
    """Each seeded-bad stage breaks the check that matches its fault; the real stage passes that check."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.seeded = {}
        for seed, scenarios in SEED_CASES.items():
            cls.seeded[seed] = batch(list(scenarios), seed)

    def test_each_seed_fails_its_check(self):
        checks = {'no-params-check': lambda r: Held.refused_before_call(r, M.OUT.PARAMS_EARLY_STOP),
                  'no-digest': lambda r: Held.refused_before_call(r, M.OUT.IMAGE_DIGEST),
                  'no-reg-check': lambda r: [] if 'HCR_EL2' in ' '.join(r.mon.texts()) else ['the changed HCR_EL2 was not reported'],
                  'no-ic-sync': Held.order, 'stray-gic': Held.gic_writes, 'wrong-args': Held.args, 'no-s2': Held.args,
                  'no-overrun-check': lambda r: [] if 'WINDOW OVERRUN' in ' '.join(r.mon.texts()) else ['the overrun was not reported'],
                  'store-after': Held.stores, 'refuse-after': Held.head, 'no-gic-finish': GicRecord.agree,
                  'no-filler-scan': Held.overflow_seen, 'no-filler-precheck': lambda r: Held.refused_before_call(r, M.OUT.WINDOW_DIRTY),
                  'no-filler-clean': Held.clean, 'flush-unwritable': Held.record_untouched, 'ticks-early': Held.ticks_at_call,
                  'no-window-clean': Held.clean, 'no-record-clean': Held.clean}
        for seed, check in checks.items():
            with self.subTest(seed):
                for name in SEED_CASES[seed]:
                    self.assertEqual(check(RUNS[name]), [], f'the real stage must pass the {seed} check on {name}')
                    self.assertNotEqual(check(self.seeded[seed][name]), [], f'the {seed} stage must fail it on {name}')


if __name__ == '__main__':
    unittest.main()
