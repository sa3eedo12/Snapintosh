#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of the monitor entry's record and of SNAPREP-M1.BIN's decode (docs/SP11-STICK-MONITOR.md sections 5 and 6), monitor port work item 4b, Phase A,
H5: the record the wrapper stores (decoded here independently of the C, from the document's table), the page lines the wrapper and the reporter print
from it (rendered here independently), a reporter fed REPORT.DAT files built in Python (a guest that ran, a prepare failure, an EL2 fault, a
quarantine, a damaged record, no record) against the lines the document says it prints, the width and the font of every line, the 40-line budget and
the read-only property; and the drift tests that tie the reporter's offsets and names to the monitor's own sources (the offsets of its report window and
of PARAMS compiled against its translation unit, its prepare-failure names against probes/run.py, and the 64-bit width of every word the time box passes through, compiled and read as
source forms: Drift and BoxSources, for the cap of 120 s).

    python3 tools/test-sp11-stick-monitor-record.py

The harness tests need clang and a q1n1 checkout (Q1N1_SP11_TREE); without them they SKIP, which is not a pass. No hardware, nothing under /Volumes, no
network; everything is written below build/sp11-wrapper-test-scratch-monitor-record/ and deleted at the end. EXPERIMENTAL; hardware NOT RUN."""
import ast
import contextlib
import hashlib
import io
import json
import re
import shutil
import struct
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_gic_testlib as G  # noqa: E402
import sp11_stick_monitor_pagelib as P  # noqa: E402
import sp11_stick_monitor_testlib as M  # noqa: E402
import sp11_stick_wrapper_testlib as T  # noqa: E402
from sp11_stick_wrapper_checks import Checks as WC, expected_reporter_lines, full_report  # noqa: E402
from sp11_stick_wrapper_testlib import ROOT, sb  # noqa: E402

FIX, RUNS, REPORTS = {}, {}, {}
MASK = (1 << 64) - 1
FAKE_PA = 0xCE010000 + M.OFFSET                    # where the harness's file puts the fake image
STATUS_NAMES = {-1: ('SHADOW_ROOT_ROOM', 'shadow root'), -2: ('WALK_16K', '16K walk'), -3: ('S2_ROOT_ROOM', 'stage-2 root'),
                -4: ('PROTECT_PAGE', 'protect a monitor page'), -5: ('PROTECT_VECTORS', 'protect the vectors'), -6: ('ENTRY_SHADOW', 'entry point'),
                -7: ('WATCH_PAGE', 'watched handoff'), -8: ('TTBR0_ROOT_ROOM', 'TTBR0 shadow root'), -9: ('TTBR0_WALK', 'TTBR0 walk'),
                -10: ('S2_RANGES', 'stage-2 range'), -11: ('S2_MAP', 'stage-2 map'), -12: ('WRITE_WATCH', 'write-watch'),
                -13: ('SLIDE_REGION', 'SLIDE_REGION'), -14: ('DEVICE_REGISTRY', 'virtual device registry'), -15: ('CLOCK_ADAPTER', 'clock adapter'),
                -16: ('CPU_CONTRACT', 'CPU affinity'), -19: ('SECONDARY_CONFIG', 'host secondary'), -21: ('TRUST_CACHE', 'trust-cache'),
                -27: ('S2_PRESPLIT', 'pre-split'), -28: ('THERMAL', 'thermal'), -90: ('NV2_NO_RECORD', 'no valid functional record'),
                -91: ('NV2_OTHER_CORE', 'another SoC'), -92: ('NV2_FEAT', 'FEAT_NV2'), -93: ('NV2_NOT_PASSING', 'passing VNCR'),
                -94: ('PAUTH_MIDR', 'MIDR_EL1 is not'), -95: ('PAUTH_IDS', 'PAuth ID fields'), -96: ('NVME_WINDOW', 'NVMe window'),
                -120: ('NVME_WINDOW', 'NVMe window'), -144: ('NVME_WINDOW', 'NVMe window'), -145: ('NVME_WINDOW', 'NVMe window'),
                -146: ('VCPU_CLUSTERS', 'vCPU count'), -147: ('TIMER_GUARD_CNTHCTL', 'CNTHCTL_EL2'), -148: ('TIMER_GUARD_CNTVOFF', 'CNTVOFF_EL2'),
                -258: ('S2_GEOMETRY', 'stage-2 geometry'), -272: ('S2_GEOMETRY', 'stage-2 geometry'), -50: ('UNNAMED', None)}
EVENT_NAMES = {1: 'MONITOR', 2: 'S2_FAULT', 3: 'GUEST_EXC', 4: 'SYSREG', 5: 'OTHER', 6: 'WATCHDOG', 7: 'HANDOFF', 8: 'DISPATCH_CMD', 9: 'PANIC_ENTRY',
               10: 'MAP_FAILED', 11: 'MAP_OUTSIDE', 12: 'SERROR', 13: 'ASYNC', 15: 'MAP_DEVICE', 16: 'MMIO_STOP', 24: 'EXCEPTION_RETURN', 33: 'TIMER_FIQ',
               36: 'DEVICE_RESET', 52: 'EARLY_STOP'}


def h(v):
    return '0X%X' % v


def runnable():
    return bool(FIX.get('tree') and T.find_tool('clang'))


def setUpModule():
    M.use_scratch('monitor-record')
    shutil.rmtree(T.BUILD, ignore_errors=True)
    FIX['scratch'] = T.BUILD / 'host'
    FIX['scratch'].mkdir(parents=True)
    FIX['tree'] = T.q1n1_tree()
    FIX['fixtures'] = T.Fixtures(FIX['scratch'] / 'fixtures')
    FIX['bundle'] = FIX['fixtures'].bundle('gic', T.LOW, stores='ram,el2_sysreg,gic', extra=('--gic-stop',))
    if runnable():
        FIX['exe'] = M.harness(FIX['tree'])
        cases = {'happy': 'ret=0', 'leave-multi': 'leave=hcr:0x1 leave=daif:0x0 leave=mdcr:0x100', 'quarantine': 'ret=0x51554E02', 'overrun': 'overrun=0x10F00',
                 'stack-full': 'stackfull=1 stack=0x30000', 'ret-other': 'ret=0x1234'}
        texts, worlds = [], {}
        for name in cases:
            worlds[name] = M.world(FIX['bundle'], FIX['scratch'])
            texts.append((name, G.scenario(name, worlds[name], (), ('snapmon', 'snaprep'))))
        out = M.run(FIX['exe'], texts, cases)
        RUNS.update({name: (worlds[name], out.results[name], out.gic[name], out.recs[name]) for name in cases})


def tearDownModule():
    shutil.rmtree(T.BUILD, ignore_errors=True)


def window(status=0, entered=1, words=(0,) * 8, events=(), counters=(0,) * 13, bad=(0, 0), terminal=None, capture=None, console=b''):
    """A monitor window as the monitor lays it out (the offsets of probes/xnuboot.S and probes/xnuboot/report_layout.c)."""
    w = bytearray(M.WINDOW)
    struct.pack_into('<2Q', w, 0, status & MASK, entered)
    struct.pack_into('<8Q', w, 0x10, *words)
    for i, event in enumerate(events):
        struct.pack_into('<6Q', w, 0x50 + 48 * i, *event)
    w[0x6050:0x6050 + len(console)] = console
    struct.pack_into('<13Q', w, 0x67C0, *counters)
    struct.pack_into('<2Q', w, 0x6830, *bad)
    if terminal:
        struct.pack_into('<6Q', w, 0x6840, *terminal)
    if capture is not None:
        struct.pack_into('<37Q', w, 0x7AC8, capture, *[0x100 + i for i in range(31)], 0x7000, 0x3C5, 0xFFFF000008001234, 0x96000045, 0xFFFF000008004567)
    return bytes(w)


def report_with(run, win=None, rec=None, gic=None):
    """REPORT.DAT of the wrapper's run `run`, with the monitor's window and the two records replaced when given."""
    w, results, g, recs = RUNS[run]
    out = bytearray(full_report(results[0].report))
    out[0x1000:0x1000 + M.WINDOW] = win if win is not None else bytes(M.WINDOW)
    out[0x11000:0x11000 + G.REC_BYTES] = gic if gic is not None else g.rec
    out[M.REC_AT:M.REC_AT + M.REC_BYTES] = rec if rec is not None else recs['FINAL']
    return bytes(out)


def reporter(name, report):
    """The reporter's result on a REPORT.DAT built here: only SNAPREP runs, in a world whose REPORT.DAT is `report`."""
    w = M.world(FIX['bundle'], FIX['scratch'], report=report)
    return M.run(FIX['exe'], [(name, G.scenario(name, w, (), ('snaprep',)))]).results[name][0], w


def expected_window(rec, win_args, before=15):
    """The lines the document's section 6 and docs/SP11-STICK-REPORT-LINES.md say the reporter prints for a window of a guest that ran (or failed to),
    without the prefix: the independent renderer of tools/sp11_stick_monitor_pagelib.py. `before` is the 8 head lines and the 7 monitor lines."""
    spec = P.Spec(**win_args)
    return P.window_lines(spec, rec.ticks_call, rec.ticks_return, rec.monitor_pa, before, STATUS_NAMES)


def gic_lines(g):
    r = G.decode(g.rec)
    return [line for line in (G.render(r, w) for w in ('GICD', 'GICR', 'CPU', 'NEW', 'RESTORE')) if line]


def window_lines(rep):
    """The reporter's lines about the monitor's window: from the status line to the first GIC line."""
    texts = rep.texts()
    start = next(i for i, t in enumerate(texts) if t.startswith(('SNAPREP MON STATUS', 'SNAPREP MON PREPARE', 'SNAPREP MON EL2')))
    end = next((i for i, t in enumerate(texts) if i > start and t.startswith('SNAPREP GIC ')), len(texts))
    return texts[start:end]


def held(r, lines=sb.REPORTER_LINES):
    """What every SNAPREP run must be: at most 40 lines of panel text, no store, no cache operation, no translation, no register read, and no read of memory
    but the preload table and REPORT.DAT."""
    found = WC.fault(r) + WC.lines(r, lines)
    for kind in ('ST', 'CVAC', 'CIVAC', 'DSB', 'ISB', 'AT', 'MRS'):
        found += [f'{kind} at {e.a:#x}' for e in r.of(kind)]
    return found


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not runnable():
            raise unittest.SkipTest('no clang or no q1n1 checkout (Q1N1_SP11_TREE): the harness is not built')


class Record(Base):
    def test_the_stored_record_passes_its_check_and_says_what_the_run_did(self):
        w, results, g, recs = RUNS['happy']
        raw = recs['FINAL']
        self.assertEqual(M.check(raw), 0)
        r = M.decode(raw)
        self.assertEqual((r.version, r.size, r.outcome, r.substage, r.flags, r.changed), (1, 512, M.OUT.RETURNED, 7, 0xF, 0))
        self.assertEqual((r.monitor_offset, r.monitor_bytes, r.monitor_pa), (M.OFFSET, M.IMAGE_BYTES, FAKE_PA))
        self.assertEqual(r.sha, hashlib.sha256(M.FAKE_IMAGE).digest())
        self.assertEqual(r.ids, (0xA2A2, 0xA4A4, 0xAE, 0xA1A1, 0xA2A22))
        self.assertEqual((r.box_s, r.early_stop_flags, r.gpu_flags, r.entered, r.ret), (5, 0, 0, 1, 0))
        self.assertEqual((r.stack_top, r.stack_bytes, r.stack_used), (w.REPORT + sb.REPORT_BYTES, M.STACK_BYTES, 0x400))
        self.assertEqual(r.call_x[0], w.REPORT + sb.REPORT_OUT)
        self.assertEqual(r.counter_hz, results[0].head()['counter_hz'])
        self.assertTrue(all(pre == post for pre, post in r.rows), 'nothing changed in the model')
        self.assertEqual(r.rows[3][0], 0x88000010, 'HCR_EL2 as the unit showed it at the payload call (OBSERVED), here the model')

    def test_the_changes_after_the_call_are_in_the_rows_and_the_mask(self):
        r = M.decode(RUNS['leave-multi'][3]['FINAL'])
        self.assertEqual(M.changed_rows(r), ['DAIF', 'HCR_EL2', 'MDCR_EL2'])
        self.assertEqual(r.changed, (1 << 0) | (1 << 3) | (1 << 9))
        self.assertTrue(r.flags & M.FLAGS['REGS_CHANGED'])
        self.assertEqual(r.rows[3], (0x88000010, 0x1))

    def test_flags_and_outcomes(self):
        self.assertEqual(M.decode(RUNS['quarantine'][3]['FINAL']).outcome, M.OUT.QUARANTINE)
        self.assertEqual(M.decode(RUNS['ret-other'][3]['FINAL']).outcome, M.OUT.RET_OTHER)
        self.assertTrue(M.decode(RUNS['overrun'][3]['FINAL']).flags & M.FLAGS['WINDOW_OVERRUN'])
        r = M.decode(RUNS['stack-full'][3]['FINAL'])
        self.assertTrue(r.flags & M.FLAGS['STACK_FULL'])
        self.assertEqual(r.stack_used, M.STACK_BYTES)

    def test_the_check_names_each_failure_class(self):
        raw = bytearray(RUNS['happy'][3]['FINAL'])
        for offset, value, code in ((0, 0x58, 1), (8, 2, 2), (12, 0x64, 3), (0x1F0, 1, 4), (0x10, 0x55, 5), (0x14, 99, 5), (0x19, 1, 5), (0x1D, 0x20, 5)):
            bad = bytearray(raw)
            bad[offset] = value
            self.assertEqual(M.check(bytes(bad)), code, f'offset {offset:#x}')

    def test_the_page_lines_are_the_documents(self):
        for name in ('happy', 'leave-multi', 'quarantine', 'overrun', 'stack-full', 'ret-other'):
            with self.subTest(name):
                w, results, g, recs = RUNS[name]
                r, texts = M.decode(recs['FINAL']), results[0].texts()
                want = [f'SNAPMON {line}' for line in (M.render(r, which) for which in (*M.PRE_CALL, 'CALL', *M.POST_CALL, 'VERDICT')) if line]
                self.assertEqual([t for t in texts if t.startswith('SNAPMON MON ')], want)
                self.assertEqual(WC.lines(results[0], sb.WRAPPER_LINES), [])

    def test_the_page_is_thirty_lines_at_most_and_ends_with_the_stage(self):
        for name, (w, results, g, recs) in RUNS.items():
            self.assertLessEqual(len(results[0].lines), 30, name)
            self.assertEqual(results[0].texts()[-1], 'SNAPMON STAGE=DONE RETURN=0', name)


class Reporter(Base):
    def test_a_real_run_is_decoded_line_for_line(self):
        for name in ('happy', 'leave-multi', 'quarantine', 'overrun', 'stack-full', 'ret-other'):
            with self.subTest(name):
                w, results, g, recs = RUNS[name]
                rep = results[1]
                self.assertEqual(held(rep), [], name)
                r = M.decode(recs['FINAL'])
                head = expected_reporter_lines(results[0].head())
                head[0] = 'SNAPREP MONITOR RETURNED STAGE=DONE'
                mon = [f'SNAPREP {line}' for line in (M.render(r, which) for which in ('VERDICT', 'IMAGE', 'IDS0', 'IDS1', 'CALL', 'RET', 'REGS', 'QUARANTINE')) if line]
                window = [f'SNAPREP {x}' for x in expected_window(r, dict(entered=1))] if r.ret == 0 else []
                self.assertEqual(rep.texts(), head + mon + window + [f'SNAPREP {x}' for x in gic_lines(g)], name)

    def test_a_guest_that_ran(self):
        events = [(2, 0xFFFF000008001000 + 4 * i, 0x300000000 + i, 0x93000046, 0xFFFF000008002000, 0) for i in range(8)]
        args = dict(status=0, entered=1, words=(0, 0, 11, 8, 3 | (2 << 32), 0, 0x93000046, 7 | (42 << 32)), events=events,
                    counters=(0, 0, 0, 0, 0, 0, 5, 0, 0, 0, 0, 0, 0), terminal=(6, 0xFFFF000008001000, 0x96000045, 0xFFFF000008004567, 0x1, 0), capture=1,
                    console=b'Hello, world [boot]\nsecond line\x00third\n' + bytes(20))
        rep, w = reporter('ran', report_with('happy', window(**args)))
        self.assertEqual(held(rep), [])
        r = M.decode(RUNS['happy'][3]['FINAL'])
        self.assertEqual(window_lines(rep), [f'SNAPREP {x}' for x in expected_window(r, args)])

    def test_a_prepare_failure(self):
        for status, (name, keyword) in STATUS_NAMES.items():
            with self.subTest(status):
                args = dict(status=status, entered=0, events=[(5, 0x4E56, 0x100000001, 0xFEED4B01, 0, 0)], words=(0, 0, 0, 1, 0, 0, 0, 0))
                rep, w = reporter('prep', report_with('happy', window(**args)))
                self.assertEqual(held(rep), [])
                self.assertIn(f'SNAPREP MON PREPARE FAILED CODE={status} {name}', rep.texts())
                self.assertTrue(any(t.startswith('SNAPREP MON EV 0 OTHER PC=0X4E56') for t in rep.texts()))

    def test_an_el2_fault(self):
        args = dict(status=0x96000045, entered=0, bad=(FAKE_PA + 0x1234, 0xFFFF000011112222), words=(0, 0, 0, 0, 0, 0, 0, 0))
        rep, w = reporter('fault', report_with('happy', window(**args)))
        self.assertEqual(held(rep), [])
        self.assertIn(f'SNAPREP MON EL2 FAULT ESR=0X96000045 EC=0X25', rep.texts())
        self.assertIn('SNAPREP MON FAULT ELR=0X%X OFF=0X1234 FAR=0XFFFF000011112222' % (FAKE_PA + 0x1234), rep.texts())

    def test_a_quarantine_decodes_nothing_of_the_window(self):
        rep, w = reporter('quarantine', report_with('quarantine', window(status=0x1234, console=b'junk\n')))
        self.assertEqual(held(rep), [])
        self.assertFalse([t for t in rep.texts() if t.startswith(('SNAPREP MON STATUS', 'SNAPREP MON CON', 'SNAPREP MON COUNTS'))])
        self.assertIn('SNAPREP MON QUARANTINE: THE REPORT IS PARTIAL AND RAM MUST NOT BE REUSED. POWER CYCLE', rep.texts())

    def test_a_damaged_or_missing_record_is_named(self):
        raw = bytearray(RUNS['happy'][3]['FINAL'])
        raw[0] ^= 1
        rep, _ = reporter('damaged', report_with('happy', rec=bytes(raw)))
        self.assertIn('SNAPREP MON RECORD DAMAGED CODE=1', rep.texts())
        rep, _ = reporter('absent', report_with('happy', rec=bytes(M.REC_BYTES)))
        self.assertIn('SNAPREP MON NO RECORD: THE STAGE DID NOT RUN', rep.texts())
        self.assertEqual(held(rep), [])

    def test_a_console_is_folded_to_the_font_and_bounded(self):
        text = b'abc [x] {y}, z;\n' * 20 + b'a' * 200
        args = dict(console=text, words=(0, 0, 0, 0, 0, 0, 0, len(text) << 32))
        rep, _ = reporter('console', report_with('happy', window(**args)))
        self.assertEqual(held(rep), [])
        con = [t for t in rep.texts() if t.startswith('SNAPREP MON CON ')]
        self.assertEqual(len(con), 6)
        self.assertEqual(con[0], 'SNAPREP MON CON ABC ?X? ?Y?? Z?')

    def test_the_gic_subset_without_the_dry_runs_windows(self):
        rep = RUNS['happy'][1][1]
        texts = rep.texts()
        self.assertEqual([t for t in texts if t.startswith('SNAPREP GIC ')], [f'SNAPREP {x}' for x in gic_lines(RUNS['happy'][2])])
        self.assertFalse([t for t in texts if re.match(r'SNAPREP GIC (IRQ|CYC|EL1|DIAG|HVC|EXC|VERDICT)', t)])


class Drift(unittest.TestCase):
    """The reporter's constants against the monitor's own sources."""

    @classmethod
    def setUpClass(cls):
        cls.tree = T.q1n1_tree()
        if not (cls.tree and T.find_tool('clang')):
            raise unittest.SkipTest('no clang or q1n1 checkout')
        nv2 = T.load('nv2_record', T.TOOLS / 'nv2_record.py')
        profile = T.load('sp11_m1_profile', T.TOOLS / 'sp11_m1_profile.py')
        cls.scratch = ROOT / 'build' / 'sp11-wrapper-test-scratch-monitor-drift'
        shutil.rmtree(cls.scratch, ignore_errors=True)
        (cls.scratch / 'record').mkdir(parents=True)
        record = nv2.Record(soc=nv2.SOC['x1e80100'], midr_el1=0xFEED4B01, revidr_el1=0, id_aa64mmfr2_el1=0x02001234, id_aa64mmfr4_el1=0,
                            vncr_redirect=nv2.RESULT['pass'], negative_control_trapped=nv2.RESULT['pass'], evidence=nv2.EVIDENCE['OBSERVED'],
                            tool='wi4b-test-only', build_id=0x4B01)
        text = nv2.emit_header(nv2.pack(record))
        T.write(cls.scratch / 'record' / 'test-record.h', text.encode() if isinstance(text, str) else text)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            flags = profile.main(['flags', '--nv2-record-header', str(cls.scratch / 'record' / 'test-record.h')])
        cls.flags = json.loads(out.getvalue())['flags'] if flags == 0 else None
        assert cls.flags, err.getvalue()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            flags = profile.main(['flags', '--nv2-record-header', str(cls.scratch / 'record' / 'test-record.h'), '--cpu-start', 'ack'])
        cls.flags_ack = json.loads(out.getvalue())['flags'] if flags == 0 else None
        assert cls.flags_ack and '-DSNAP_SP11_CPU_START_ACK=1' in cls.flags_ack, err.getvalue()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.scratch, ignore_errors=True)

    def compile(self, asserts, name, ack=False):
        sys.path.insert(0, str(T.TOOLS))
        import monitor_build_offline as offline
        uefi, why = offline.locate_q1n1(ROOT)
        self.assertIsNotNone(uefi, why)
        source = self.scratch / f'{name}.c'
        T.write(source, f'#include "{ROOT / "probes/xnuboot.c"}"\n#include <stddef.h>\n{asserts}\n'.encode())
        done = subprocess.run([str(offline.LLVM / 'clang'), *(self.flags_ack if ack else self.flags), f'-I{ROOT / "shadow"}', f'-I{uefi}', '-fsyntax-only', str(source)],
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr[-2500:])

    def reporter_constants(self):
        text = (ROOT / 'probes/sp11-stick-reporter.c').read_text()
        return {name: int(value, 16) for name, value in re.findall(r'#define (RP_W_\w+) (0x[0-9A-Fa-f]+)u', text)}

    def test_the_window_offsets_are_the_monitors(self):
        c = self.reporter_constants()
        self.assertEqual(sorted(c), sorted(['RP_W_STATUS', 'RP_W_ENTERED', 'RP_W_WORDS', 'RP_W_LOG', 'RP_W_CONSOLE', 'RP_W_COUNTERS', 'RP_W_BAD', 'RP_W_TERMINAL',
                                            'RP_W_ENDPOINTS', 'RP_W_ASYNC', 'RP_W_CLOCK_READS', 'RP_W_TIMER', 'RP_W_CLOCK_RAW', 'RP_W_ACK']))
        self.assertEqual(c, {'RP_W_STATUS': P.STATUS, 'RP_W_ENTERED': P.ENTERED, 'RP_W_WORDS': P.WORDS, 'RP_W_LOG': P.LOG, 'RP_W_CONSOLE': P.CONSOLE,
                             'RP_W_COUNTERS': P.COUNTERS, 'RP_W_BAD': P.BAD, 'RP_W_TERMINAL': P.TERMINAL, 'RP_W_ENDPOINTS': P.ENDPOINTS, 'RP_W_ASYNC': P.ASYNC,
                             'RP_W_CLOCK_READS': P.CLOCK_READS, 'RP_W_TIMER': P.TIMER, 'RP_W_CLOCK_RAW': P.CLOCK_RAW, 'RP_W_ACK': P.ACK}, 'the independent renderer holds the same layout')
        self.compile(f'_Static_assert(16 + 8 * OUT_CPU_START_ACK == {c["RP_W_ACK"]:#x} && CPU_START_ACK_REPORT_WORDS == {P.ACK_WORDS} && '
                     f'sizeof(struct cpu_start_ack) == {P.ACK_LAYOUT >> 32} && CPU_START_ACK_VERSION == {P.ACK_LAYOUT & 0xFFFFFFFF} && CPU_START_ACK_FIRST == {P.ACK_FIRST} && '
                     f'CPU_START_ACK_MAGIC == {P.ACK_MAGIC:#x}ull && offsetof(struct cpu_start_ack, total) == 0 && offsetof(struct cpu_start_ack, last_pc) == 8 * 4 && '
                     f'offsetof(struct cpu_start_ack, first) == 8 * 6 && sizeof(((struct cpu_start_ack *)0)->first[0]) == 16, "the CPU-start acknowledge block");', 'ack', ack=True)
        asserts = '\n'.join([f'_Static_assert(16 + 8 * 8 == {c["RP_W_LOG"]:#x}, "log");',
                             f'_Static_assert(16 + 8 * (8 + 6 * EVENT_MAX) == {c["RP_W_CONSOLE"]:#x}, "console");',
                             f'_Static_assert(16 + 8 * OUT_COUNTERS == {c["RP_W_COUNTERS"]:#x} && OUT_COUNTERS_N == 13, "counters");',
                             f'_Static_assert(16 + 8 * OUT_BAD_REGS == {c["RP_W_BAD"]:#x}, "bad");',
                             f'_Static_assert(16 + 8 * OUT_LAST_EVENT == {c["RP_W_TERMINAL"]:#x}, "terminal");',
                             f'_Static_assert(16 + 8 * OUT_ENDPOINTS == {c["RP_W_ENDPOINTS"]:#x} && ENDPOINT_BUCKETS == 64 && '
                             f'16 + 8 * (OUT_ENDPOINTS + ENDPOINT_BUCKETS) == {c["RP_W_ENDPOINTS"] + 8 * 64:#x}, "endpoints and the bucket of the other calls");',
                             f'_Static_assert(16 + 8 * OUT_ASYNC_REGS == {c["RP_W_ASYNC"]:#x}, "async");',
                             f'_Static_assert(16 + 8 * OUT_CLOCK_FLOW == {c["RP_W_CLOCK_READS"]:#x} && offsetof(struct guest_clock, reads) == 0, "clock reads");',
                             f'_Static_assert(16 + 8 * OUT_TIMER_FLOW == {c["RP_W_TIMER"]:#x} && OUT_DEVICE_FLOW == OUT_TIMER_FLOW + 11, "the guest timer\'s words");',
                             f'_Static_assert(16 + 8 * (OUT_CLOCK_ADAPTER + 8) == {c["RP_W_CLOCK_RAW"]:#x} && '
                             f'16 + 8 * (OUT_CLOCK_ADAPTER + 9) == {c["RP_W_CLOCK_RAW"] + 8:#x}, "clock raw samples");',
                             '_Static_assert(EVENT_MAX == 512 && CONSOLE_MAX == 1024 && sizeof(struct event) == 48, "log geometry");',
                             '_Static_assert(offsetof(struct guest_regs, x) == 0 && sizeof(((struct guest_regs *)0)->x) == 8 * 31 && '
                             'offsetof(struct guest_regs, sp_el1) == 8 * 31 && offsetof(struct guest_regs, spsr) == 8 * 32 && '
                             'offsetof(struct guest_regs, pc) == 8 * 33 && offsetof(struct guest_regs, esr) == 8 * 34 && '
                             'offsetof(struct guest_regs, far) == 8 * 35 && GUEST_REGS_WORDS == 36, "snapshot: x0 to x30, sp, spsr, pc, esr, far");',
                             f'_Static_assert({c["RP_W_WORDS"]:#x} == 16 && {c["RP_W_STATUS"]:#x} == 0 && {c["RP_W_ENTERED"]:#x} == 8, "head words");'])
        self.compile(asserts, 'window')

    def test_the_drift_check_can_fail(self):
        with self.assertRaises(AssertionError):
            self.compile('_Static_assert(16 + 8 * OUT_LAST_EVENT == 0x6841, "a wrong offset");', 'wrong')

    def test_the_params_words_are_the_monitors(self):
        text = (ROOT / 'probes/sp11-stick-monitor.h').read_text()
        c = {n: int(v, 0) for n, v in re.findall(r'#define (SM_PARAMS_\w+) (0x[0-9A-Fa-f]+|\d+)u', text)}
        self.assertEqual(sorted(c), ['SM_PARAMS_BYTES', 'SM_PARAMS_EARLY_STOP_FLAGS', 'SM_PARAMS_GPU_FLAGS', 'SM_PARAMS_WATCHDOG'])
        self.compile('\n'.join([f'_Static_assert(sizeof(struct params) == {c["SM_PARAMS_BYTES"]:#x}, "size");',
                                f'_Static_assert(offsetof(struct params, watchdog_seconds) == {c["SM_PARAMS_WATCHDOG"]}, "watchdog");',
                                f'_Static_assert(offsetof(struct params, gpu) + offsetof(struct adreno_params, flags) == {c["SM_PARAMS_GPU_FLAGS"]:#x}, "gpu");',
                                f'_Static_assert(offsetof(struct params, early_stop) + offsetof(struct early_stop_params, flags) == '
                                f'{c["SM_PARAMS_EARLY_STOP_FLAGS"]:#x}, "early stop");']), 'params')

    def test_the_watchdog_is_64_bit_from_params_to_the_deadline(self):
        """The time box's cap is 120 s: 2.3e9 ticks at the unit's 19.2 MHz, above INT32_MAX from 112 s on. The words the box passes through in the monitor's translation unit are
        unsigned 64-bit ones: the PARAMS word, the arming function's argument and the vCPU's deadline and live flag."""
        def u64(expr):
            return f'__builtin_types_compatible_p(__typeof__({expr}), uint64_t)'
        self.compile('\n'.join([f'_Static_assert({u64("((struct params *)0)->watchdog_seconds")}, "PARAMS word");',
                                f'_Static_assert({u64("((struct vcpu *)0)->watchdog_deadline")} && {u64("((struct vcpu *)0)->watchdog_live")}, "deadline");',
                                '_Static_assert(__builtin_types_compatible_p(__typeof__(xnuboot_watchdog), void (uint64_t)), "arming function");']), 'watchdog-width')

    def test_the_width_check_can_fail(self):
        for expr in ('((struct params *)0)->watchdog_seconds', '((struct vcpu *)0)->watchdog_deadline'):
            with self.subTest(expr), self.assertRaises(AssertionError):
                self.compile(f'_Static_assert(__builtin_types_compatible_p(__typeof__({expr}), uint32_t), "narrow");', 'watchdog-narrow')

    def test_the_event_names_are_the_monitors(self):
        text = (ROOT / 'probes/sp11-stick-reporter.c').read_text()
        names = {int(n): name for n, name in re.findall(r'case (\d+): return "([A-Z0-9_]+)";', text.split('rp_event_name')[1].split('rp_status_name')[0])}
        names.pop(0, None)
        self.assertEqual(names, {k: v for k, v in EVENT_NAMES.items()})
        self.compile('\n'.join(f'_Static_assert(EV_{name} == {kind}, "EV_{name}");' for kind, name in names.items()), 'events')

    def test_the_status_names_are_tied_to_the_hosts_table(self):
        mod = ast.parse((ROOT / 'probes/run.py').read_text())
        table = next(ast.literal_eval(n.value) for n in mod.body if isinstance(n, ast.Assign) and any(getattr(t, 'id', None) == 'PREPARE_FAILURES' for t in n.targets))
        text = (ROOT / 'probes/sp11-stick-reporter.c').read_text().split('rp_status_name')[1].split('rp_font')[0]
        named = {int(n): name for n, name in re.findall(r'case (-\d+): return "([A-Z0-9_]+)";', text)}
        for code, (name, keyword) in STATUS_NAMES.items():
            if keyword is None:
                continue
            self.assertIn(code, table, f'probes/run.py has no status {code}')
            self.assertIn(keyword.lower(), table[code].lower(), f'status {code} is {name}: probes/run.py says {table[code][:80]!r}')
            if code not in (-96, -120, -144, -258, -272):          # the ends and the middle of a family are named by its range
                self.assertEqual(named.get(code), name, f'status {code}')
        for code in named:
            self.assertIn(code, table, f'the reporter names status {code} and probes/run.py does not know it')


class BoxSources(unittest.TestCase):
    """The arithmetic that the time box passes through, as read for the cap of 120 s (probes/sp11-stick-monitor.h; VERIFIED by reading, hardware NOT RUN): at the unit's 19.2 MHz the box
    is 2.3e9 ticks, above INT32_MAX from 112 s on, so every step is a 64-bit one and the EL2 timer is set through CNTHP_CVAL_EL2, never the signed 32-bit TVAL. These are the forms that
    reading found. A rewrite of one fails here and is read again, instead of overflowing in a run nobody repeats. No toolchain needed."""
    FORMS = {
        'probes/xnuboot.S': (r'ldr x0, \[x21, #192\]\s+/\* params\.watchdog_seconds \*/\n\s+bl xnuboot_watchdog',),
        'probes/xnuboot/clock_timer.c': (
            r'void xnuboot_watchdog\(uint64_t seconds\)\n\{.*?uint64_t freq, now;.*?watchdog_deadline = now \+ freq \* seconds;',
            r'uint64_t budget = \(int64_t\)\(end - physical\) > 0 \? end - physical : 0;\n\s+uint64_t delay = budget;',
            r'uint64_t next = physical \+ delay;\n\s+__asm__ volatile\("msr cnthp_cval_el2, %0" :: "r"\(next\)\);'),
        'probes/xnuboot/async.c': (r'\(int64_t\)\(vcpu_of\(c\)->watchdog_deadline - physical\) <= 0',),
        'compat/early-stop.c': (r'return \(int64_t\)\(deadline - budget\) < 0 \? deadline : budget;',
                                r'if \(\(int64_t\)\(now - deadline\) < 0 \|\| \(int64_t\)\(deadline - budget\) >= 0\) return 0;'),
        'compat/clock.c': (r'uint64_t clock_rate_wait\(const struct clock_rate \*rate, uint64_t host_now,\n\s+uint64_t guest_deadline, uint64_t host_budget\)\n\{\n'
                           r'\s+uint64_t delta = guest_deadline - clock_rate_sample\(rate, host_now\);\n\s+if \(\(int64_t\)delta <= 0\) return 0;',
                           r'if \(q > host_budget / rate->host_hz\) return host_budget;')}
    SOURCES = ('probes/xnuboot/*.c', 'probes/xnuboot.c', 'probes/xnuboot.S', 'compat/*.c', 'compat/*.h', 'compat/*.S', 'shadow/*.c', 'shadow/*.h', 'shadow/*.S')

    @classmethod
    def texts(cls):
        return {str(p.relative_to(ROOT)): p.read_text() for pattern in cls.SOURCES for p in sorted(ROOT.glob(pattern))}

    @classmethod
    def problems(cls, texts):
        """The forms that are not exactly once in their file, and the monitor sources that write the EL2 timer's 32-bit value."""
        found = [f'{path}: {pattern[:48]} is not there once' for path, patterns in cls.FORMS.items() for pattern in patterns
                 if len(re.findall(pattern, texts[path], re.S)) != 1]
        return found + [f'{path} names CNTHP_TVAL' for path, text in texts.items() if re.search(r'cnthp_tval', text, re.I)]

    def test_the_boxs_arithmetic_is_64_bit_and_the_timer_is_set_through_cval(self):
        self.assertEqual(self.problems(self.texts()), [])

    def test_each_form_can_fail(self):
        texts = self.texts()
        for path, old, new in (('probes/xnuboot.S', 'ldr x0, [x21, #192]', 'ldr w0, [x21, #192]'),
                               ('probes/xnuboot/clock_timer.c', 'void xnuboot_watchdog(uint64_t seconds)\n{', 'void xnuboot_watchdog(unsigned seconds)\n{'),
                               ('probes/xnuboot/clock_timer.c', 'uint64_t freq, now;', 'uint32_t freq, now;'),
                               ('probes/xnuboot/clock_timer.c', 'uint64_t budget = (int64_t)(end - physical)', 'uint64_t budget = (int32_t)(end - physical)'),
                               ('probes/xnuboot/clock_timer.c', 'msr cnthp_cval_el2, %0" :: "r"(next)', 'msr cnthp_tval_el2, %0" :: "r"(next)'),
                               ('probes/xnuboot/async.c', '(int64_t)(vcpu_of(c)->watchdog_deadline - physical) <= 0', '(int32_t)(vcpu_of(c)->watchdog_deadline - physical) <= 0'),
                               ('compat/early-stop.c', 'return (int64_t)(deadline - budget) < 0 ? deadline : budget;', 'return (int32_t)(deadline - budget) < 0 ? deadline : budget;'),
                               ('compat/clock.c', 'uint64_t delta = guest_deadline', 'uint32_t delta = guest_deadline')):
            with self.subTest(path=path, old=old):
                self.assertIn(old, texts[path])
                self.assertNotEqual(self.problems({**texts, path: texts[path].replace(old, new, 1)}), [])


if __name__ == '__main__':
    unittest.main()
