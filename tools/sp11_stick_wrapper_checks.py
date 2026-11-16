# SPDX-License-Identifier: MIT
"""What the host-harness tests of the SP11 stick wrapper share (tools/test-sp11-stick-wrapper.py and
tools/test-sp11-stick-wrapper-refusals.py; docs/SP11-STICK-WRAPPER.md section 9), on top of tools/sp11_stick_wrapper_testlib.py: the module
fixtures of a test file (its own scratch directory below build/, the synthetic bundles for both placements and the harness variants it
runs), the runs, and the properties a run is held to. Each property returns its violations, so that a seeded-bad wrapper can be shown to
break the check that matches its fault while the real one does not. Nothing here touches hardware. EXPERIMENTAL; hardware NOT RUN."""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_wrapper_testlib as T  # noqa: E402
from sp11_stick_wrapper_testlib import E, sb  # noqa: E402

STAGE = sb.STAGE
FONT = set('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 :-./=?()_')
FIX = {}
VARIANTS = {'policy0': dict(defines=['-DSP11_STICK_HASH_POLICY=0']), 'policy1': dict(defines=['-DSP11_STICK_HASH_POLICY=1']),
            'policy2': dict(defines=['-DSP11_STICK_HASH_POLICY=2']), 'gic': dict(gic={'SNAP_TIMER_INTID': '27'}),
            'seed-early-store': dict(defines=['-DSP11_STICK_SEED_EARLY_STORE']), 'seed-no-chunk-check': dict(defines=['-DSP11_STICK_SEED_NO_CHUNK_CHECK']),
            'seed-no-prepass': dict(defines=['-DSP11_STICK_SEED_NO_PREPASS']), 'seed-no-clean': dict(defines=['-DSP11_STICK_SEED_NO_CLEAN']),
            'seed-verify-source': dict(defines=['-DSP11_STICK_SEED_VERIFY_SOURCE']), 'seed-no-at': dict(defines=['-DSP11_STICK_SEED_NO_AT'])}


def set_up(name, variants=(), bundles=('low', 'bank')):
    """The module fixtures of one test file (call from its setUpModule): a scratch directory of its own, so that two test files can run
    side by side, the synthetic bundles for the placements it uses and, when clang and the q1n1 checkout are there, the harness variants
    it runs (the base one always), compiled together."""
    T.use_scratch(name)
    shutil.rmtree(T.BUILD, ignore_errors=True)
    FIX['scratch'] = T.BUILD / 'host'
    FIX['scratch'].mkdir(parents=True)
    FIX['tree'] = T.q1n1_tree()
    FIX['fixtures'] = T.Fixtures(FIX['scratch'] / 'fixtures')
    for bundle in bundles:
        FIX[bundle] = FIX['fixtures'].bundle(bundle, {'low': T.LOW, 'bank': T.BANK}[bundle])
    if FIX['tree'] and T.find_tool('clang'):
        T.prebuild(FIX['tree'], {'base': {}, **{v: VARIANTS[v] for v in variants}})


def tear_down():
    shutil.rmtree(T.BUILD, ignore_errors=True)


def runnable():
    """Whether the harness can be built here: clang and the q1n1 checkout whose headers and sha256.c the wrapper is built with."""
    return bool(FIX.get('tree') and T.find_tool('clang'))


def exe_of(variant):
    return T.harness(FIX['tree'], variant, **({'base': {}, **VARIANTS}[variant]))


def run(scenarios, variant='base'):
    """{name: [Result]} for [(name, World, runs)]: one harness process, the scenarios one after the other."""
    return T.run_scenarios(exe_of(variant), ''.join(w.text(name, runs) for name, w, runs in scenarios))


def one(name, w, runs=('snapmon',), variant='base'):
    return run([(name, w, runs)], variant)[name]


def fresh(bundle='low', **over):
    return T.World(FIX[bundle], FIX['scratch'], **over)


def hexs(value):
    return '0X%X' % value


def full_report(head_bytes):
    return head_bytes + bytes(sb.REPORT_BYTES - len(head_bytes))


def lines_of_range(start, size):
    return set(range(start & ~(T.LINE - 1), start + size, T.LINE))


def line_set(events, kind):
    found = set()
    for e in events:
        if e.kind == kind:
            found.update(range(e.a, e.b + 1, T.LINE))
    return found


class Checks:
    """The properties a run is held to. Each returns its violations, so that a seeded-bad wrapper can be shown to break the check that
    matches its fault while the real one does not."""

    @staticmethod
    def in_report(w, a, n):
        return w.REPORT <= a and a + n <= w.REPORT + len(w.report)

    @classmethod
    def fault(cls, r, w=None):
        return [f'bus fault: {r.fault}'] if r.faulted else []

    @classmethod
    def early_stores(cls, r, w):
        """Before the stage COPYING is in REPORT.DAT nothing but REPORT.DAT may be stored to."""
        return [f'store {e.a:#x}+{e.b:#x} while the report said stage {e.stage}' for e in r.of('ST')
                if e.stage < STAGE.COPYING and not cls.in_report(w, e.a, e.b)]

    @classmethod
    def confined(cls, r, w):
        """Every store is REPORT.DAT or inside a chunk destination range of the bundle's table."""
        ranges = [(a, a + n) for a, n in w.chunk_ranges()]
        return [f'store {e.a:#x}+{e.b:#x} is outside REPORT.DAT and every chunk' for e in r.of('ST')
                if not cls.in_report(w, e.a, e.b) and not any(s <= e.a and e.a + e.b <= t for s, t in ranges)]

    @classmethod
    def caches(cls, r, w):
        """Every written line was cleaned, every chunk line was cleaned and invalidated, no cache operation touched anything else, and
        nothing is dirty at the end: the clean finish."""
        found = []
        chunk_lines = set().union(*[lines_of_range(a, n) for a, n in w.chunk_ranges()])
        report_lines = lines_of_range(w.REPORT, sb.REPORT_HEAD_PAGE)
        written = set().union(*[lines_of_range(e.a, e.b) for e in r.of('ST')]) if r.of('ST') else set()
        cvac, civac = line_set(r.events, 'CVAC'), line_set(r.events, 'CIVAC')
        if written - cvac:
            found.append(f'{len(written - cvac)} written lines were never cleaned')
        if chunk_lines - civac:
            found.append(f'{len(chunk_lines - civac)} chunk lines were never invalidated')
        if (cvac | civac) - chunk_lines - report_lines:
            found.append('a cache operation outside the chunks and the report head')
        found += [f'{name} holds {n} dirty lines at the end' for name, n in r.dirty.items() if n]
        return found

    @classmethod
    def barriers(cls, r, w):
        """A DSB and an ISB after the last copy store and before the first invalidate, and after the last invalidate before the first re-read
        of the placement."""
        ev, ranges = r.events, [(a, a + n) for a, n in w.chunk_ranges()]
        inside = lambda a: any(s <= a < t for s, t in ranges)  # noqa: E731
        stores = [e.index for e in ev if e.kind == 'ST' and inside(e.a)]
        views = [e.index for e in ev if e.kind == 'VIEW' and inside(e.a)]
        civacs = [e.index for e in ev if e.kind == 'CIVAC']
        if not (stores and views and civacs):
            return ['no copy, no invalidate or no re-read in the log']
        found = []
        if not any(e.kind == 'DSB' and max(stores) < e.index < min(civacs) for e in ev):
            found.append('no DSB between the last copy store and the first invalidate')
        before = max(i for i in civacs if i < min(views))
        dsb = [e.index for e in ev if e.kind == 'DSB' and before < e.index < min(views)]
        if not dsb or not any(e.kind == 'ISB' and dsb[0] < e.index < min(views) for e in ev):
            found.append('no DSB and ISB between the last invalidate and the first re-read')
        return found

    @classmethod
    def lines(cls, r, limit):
        found = []
        if len(r.lines) > limit:
            found.append(f'{len(r.lines)} lines, the budget is {limit}')
        for line in r.lines:
            if line.length > sb.LINE_MAX or not line.clean or not set(line.text.upper()) <= FONT:
                found.append(f'line {line.index} is not panel text: {line.text!r}')
        return found

    @classmethod
    def refusal(cls, r, w, code, stage, detail=None):
        """The run ended in this refusal at this stage: the return value, the printed line, the head. Stage NONE: nothing could be recorded,
        so nothing may have been stored, cleaned or written to REPORT.DAT."""
        found = cls.fault(r)
        if r.ret != sb.RET_REFUSED + getattr(E, code):
            found.append(f'returned {r.ret:#x}, expected {sb.RET_REFUSED + getattr(E, code):#x}')
        shown = [t for t in r.texts() if t.startswith(f'SNAPMON REFUSED={code} ')]
        if len(shown) != 1:
            found.append(f'no single REFUSED={code} line in {r.texts()}')
        else:
            if f' STAGE={stage} ' not in shown[0] + ' ':
                found.append(f'stage is not {stage}: {shown[0]}')
            if detail is not None and not shown[0].endswith(f'DETAIL={hexs(detail)}'):
                found.append(f'detail is not {hexs(detail)}: {shown[0]}')
        if stage == 'NONE':
            found += [f'a store at {e.a:#x}' for e in r.of('ST')] + [f'a cache operation at {e.a:#x}' for e in r.of('CVAC') + r.of('CIVAC')]
            if r.report is not None and any(r.report):
                found.append('REPORT.DAT was written')
        else:
            head = r.head()
            if (head['stage'], head['refusal']) != (getattr(STAGE, stage), getattr(E, code)):
                found.append(f'head stage {head["stage"]} refusal {head["refusal"]}')
            if detail is not None and head['refusal_detail'] != detail:
                found.append(f'head detail {head["refusal_detail"]:#x}, expected {detail:#x}')
            if r.report_tail:
                found.append(f'{r.report_tail} nonzero bytes beyond the head')
            if sb.check_report(full_report(r.report)):
                found.append(f'the head fails its own check: {sb.error_name(sb.check_report(full_report(r.report)))}')
            if getattr(STAGE, stage) < STAGE.COPYING:
                found += cls.early_stores(r, w) + [f'store {e.a:#x} outside REPORT.DAT' for e in r.of('ST') if not cls.in_report(w, e.a, e.b)]
        return found + cls.lines(r, sb.WRAPPER_LINES)


def expected_reporter_lines(h):
    """What SNAPREP prints for a head that passes its check, written from docs/SP11-STICK-WRAPPER.md section 6, not from the source."""
    stage, refusal = sb.STAGES[h['stage']], h['refusal']
    if refusal:
        out = [f'SNAPREP REFUSED={sb.error_name(refusal)} STAGE={stage} DETAIL={hexs(h["refusal_detail"])}']
    elif h['stage'] == STAGE.DONE:
        out = ['SNAPREP DRY RUN COMPLETE STAGE=DONE: THE MONITOR WAS NOT CALLED']
    else:
        out = [f'SNAPREP WRAPPER DID NOT FINISH STAGE={stage}']
    out.append(f'SNAPREP VERDICTS PIN={sb.error_name(h["pin_code"])} GUEST={sb.error_name(h["guest_code"])} POLICY={hexs(h["hash_policy"])} '
              f'LINES={h["lines_printed"]}')
    if h['midr_el1']:
        out.append(f'SNAPREP REGS EL={hexs(h["current_el"])} MIDR={hexs(h["midr_el1"])} MPIDR={hexs(h["mpidr_el1"])} CNTFRQ={h["cntfrq_el0"]}')
        out.append(f'SNAPREP REGS HCR={hexs(h["hcr_el2"])} SCTLR={hexs(h["sctlr_el2"])} TCR={hexs(h["tcr_el2"])}')
    if refusal and h['stage'] < STAGE.COPYING:
        return out + ['SNAPREP RAM UNTOUCHED BEYOND REPORT.DAT']
    if refusal:
        out.append('SNAPREP PLACEMENT WRITTEN')
    since = lambda t: t - h['ticks_enter'] if t else 0  # noqa: E731
    out.append(f'SNAPREP CHUNKS TOTAL={h["chunks_total"]} COPIED={h["chunks_copied"]} HASHED={h["chunks_hashed"]} BYTES_COPIED={h["bytes_copied"]} '
               f'BYTES_HASHED={h["bytes_hashed"]}')
    out.append(f'SNAPREP TICKS HZ={h["counter_hz"]} PLACED={since(h["ticks_placed"])} COPIED={since(h["ticks_copied"])} HASHED={since(h["ticks_hashed"])}')
    out.append(f'SNAPREP PLACE BASE={hexs(h["place_base"])} SIZE={hexs(h["place_size"])} DESC={h["desc_index"]} DBASE={hexs(h["desc_base"])} '
               f'DPAGES={hexs(h["desc_pages"])}')
    out.append(f'SNAPREP GUEST.DAT BASE={hexs(h["guest_file_base"])} BYTES={h["guest_file_size"]}')
    return out


def reporter_checks(r, w, runs_report=True):
    """What every SNAPREP run must be: at most 40 lines of panel text, no store, no cache operation, no translation, no register read, and
    no read of memory but the preload table and REPORT.DAT (the other files have no region in these worlds, so a read would be a bus fault)."""
    found = Checks.fault(r) + Checks.lines(r, sb.REPORTER_LINES)
    if len(r.lines) > 9:
        found.append(f'{len(r.lines)} lines: the reporter prints at most 9')
    for kind in ('ST', 'CVAC', 'CIVAC', 'DSB', 'ISB', 'AT', 'MRS'):
        found += [f'{kind} at {e.a:#x}' for e in r.of(kind)]
    for e in r.of('VIEW'):
        if not (w.table <= e.a < w.table + 0x2000 or w.REPORT <= e.a < w.REPORT + len(w.report)):
            found.append(f'a read of {e.a:#x}')
    return found
