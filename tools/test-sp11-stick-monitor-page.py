#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of the page that SNAPREP-M1.BIN prints from the monitor's report window (docs/SP11-STICK-REPORT-LINES.md; docs/SP11-STICK-MONITOR.md section 4):
the decoded syndrome of a stage-2 fault and the value its store wrote, the guest's registers, the clock block, the guest timer's words, the counters, the CPU-start acknowledge and the calls by
endpoint, and the budget that the events and the console share with them.
The page is compared line by line with the independent renderer of tools/sp11_stick_monitor_pagelib.py, on windows that model what the monitor leaves
at a stop: the situation of boot 20 (a watchdog stop, the counts the page showed), a stage-2 fault with an IPA, a panic entry (with and without a capture of
the registers), a guest exception, a window with every field full and windows whose values are as wide as the lines can be made to carry. No value of a
line may be cut at the runner's 100 columns (a cut value is a wrong one: the line gives way to a MORE count instead), a capture word the monitor never writes is no
capture, and a clock sample outside the call is NA. Malformed REPORT.DAT files must give the refusals the reporter already has and none of the new lines. The mutants
of the reporter's source are in tools/test-sp11-stick-monitor-page-mutants.py and the ties to the monitor's sources in tools/test-sp11-stick-monitor-page-sources.py.

    python3 tools/test-sp11-stick-monitor-page.py

The tests need clang and a q1n1 checkout (Q1N1_SP11_TREE); without them they SKIP, which is not a pass. No hardware, nothing under /Volumes, no network;
everything is written below build/sp11-wrapper-test-scratch-monitor-page/ and deleted at the end. EXPERIMENTAL; hardware NOT RUN."""
import ast
import dataclasses
import re
import shutil
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_gic_testlib as G  # noqa: E402
import sp11_stick_monitor_pagefix as PF  # noqa: E402
import sp11_stick_monitor_pagelib as P  # noqa: E402
import sp11_stick_monitor_testlib as M  # noqa: E402
import sp11_stick_wrapper_testlib as T  # noqa: E402
from sp11_stick_wrapper_checks import Checks as WC, full_report  # noqa: E402
from sp11_stick_wrapper_testlib import ROOT, sb  # noqa: E402

FIX, RUN = {}, {}
REPORTER_C = ROOT / 'probes/sp11-stick-reporter.c'
CALL_TICKS = 96_768_000         # 5.04 s at CNTFRQ 19.2 MHz, what the call of boot 20 took (OBSERVED): the wrapper's ticks_return less its ticks_call in the fixtures' records
NEW_PREFIXES = ('SNAPREP MON GX', 'SNAPREP MON CLK', 'SNAPREP MON TIM', 'SNAPREP MON CNT', 'SNAPREP MON EP ', 'SNAPREP MON ESR', 'SNAPREP MON WVAL', 'SNAPREP MON ACK',
                'SNAPREP MON CAPTURE')
WINDOW_PREFIXES = ('SNAPREP MON STATUS', 'SNAPREP MON PREPARE', 'SNAPREP MON EL2', 'SNAPREP MON COUNTS', 'SNAPREP MON STOP', 'SNAPREP MON EXIT', 'SNAPREP MON EV ',
                   'SNAPREP MON CON ', *NEW_PREFIXES)
# The lines of the page that the record of the run names, as printed before this change: none may be left out of a full page, nor reworded.
CITED = ('SNAPREP MONITOR RETURNED STAGE=DONE', 'SNAPREP VERDICTS PIN=', 'SNAPREP REGS EL=', 'SNAPREP REGS HCR=', 'SNAPREP CHUNKS TOTAL=', 'SNAPREP TICKS HZ=',
         'SNAPREP PLACE BASE=', 'SNAPREP GUEST.DAT BASE=', 'SNAPREP MON VERDICT=', 'SNAPREP MON IMAGE OFF=', 'SNAPREP MON IDS MMFR2=', 'SNAPREP MON IDS ISAR1=',
         'SNAPREP MON CALL X0=', 'SNAPREP MON RET=', 'SNAPREP MON REGS RESTORED=', 'SNAPREP MON STATUS=OK ENTERED=', 'SNAPREP MON COUNTS EVENTS=',
         'SNAPREP MON STOP ', 'SNAPREP MON EXIT PC=', 'SNAPREP MON EV 0 ', 'SNAPREP MON CON ', 'SNAPREP GIC GICD', 'SNAPREP GIC GICR', 'SNAPREP GIC CPU',
         'SNAPREP GIC NEW', 'SNAPREP GIC RESTORE')


def runnable():
    return bool(FIX.get('tree') and T.find_tool('clang'))


def with_ticks(raw, ticks=CALL_TICKS, call=None):
    """The wrapper's record `raw` with `ticks` between its counter reads before the call and after it, and `call` as the first of them when given."""
    offsets = {name: offset for name, offset, width in M.HEADER}
    assert offsets['ticks_return'] == offsets['ticks_call'] + 8
    out = bytearray(raw)
    call = M.decode(raw).ticks_call if call is None else call
    struct.pack_into('<2Q', out, offsets['ticks_call'], call, call + ticks)
    return bytes(out)


def setUpModule():
    M.use_scratch('monitor-page')
    shutil.rmtree(T.BUILD, ignore_errors=True)
    FIX['scratch'] = T.BUILD / 'host'
    FIX['scratch'].mkdir(parents=True)
    FIX['tree'] = T.q1n1_tree()
    FIX['fixtures'] = T.Fixtures(FIX['scratch'] / 'fixtures')
    FIX['bundle'] = FIX['fixtures'].bundle('gic', T.LOW, stores='ram,el2_sysreg,gic', extra=('--gic-stop',))
    if runnable():
        FIX['exe'] = M.harness(FIX['tree'])
        out = M.run(FIX['exe'], [('happy', G.scenario('happy', M.world(FIX['bundle'], FIX['scratch']), (), ('snapmon', 'snaprep')))], {'happy': 'ret=0'})
        RUN.update(results=out.results['happy'], gic=out.gic['happy'], rec=with_ticks(out.recs['happy']['FINAL']))
        FIX['rec'] = M.decode(RUN['rec'])


def tearDownModule():
    shutil.rmtree(T.BUILD, ignore_errors=True)


def report_with(window, rec=None):
    """REPORT.DAT of the happy run with `window` as the monitor's window and `rec` as the wrapper's record."""
    out = bytearray(full_report(RUN['results'][0].report))
    out[0x1000:0x1000 + P.WINDOW] = window
    out[0x11000:0x11000 + G.REC_BYTES] = RUN['gic'].rec
    out[M.REC_AT:M.REC_AT + M.REC_BYTES] = rec if rec is not None else RUN['rec']
    return bytes(out)


def run_reporters(exe, reports):
    """{name: Result} of the reporter in `exe` on each REPORT.DAT, in one harness process."""
    texts = [(name, G.scenario(name, M.world(FIX['bundle'], FIX['scratch'], report=data), (), ('snaprep',))) for name, data in reports.items()]
    return {name: results[0] for name, results in M.run(exe, texts).results.items()}


def held(r):
    """What every SNAPREP run must be: at most 40 lines of panel text, no store, no cache operation, no translation, no register read."""
    found = WC.fault(r) + WC.lines(r, sb.REPORTER_LINES)
    for kind in ('ST', 'CVAC', 'CIVAC', 'DSB', 'ISB', 'AT', 'MRS'):
        found += [f'{kind} at {e.a:#x}' for e in r.of(kind)]
    return found


def window_part(texts):
    start = next(i for i, t in enumerate(texts) if t.startswith(('SNAPREP MON STATUS', 'SNAPREP MON PREPARE', 'SNAPREP MON EL2')))
    end = next((i for i, t in enumerate(texts) if i > start and t.startswith('SNAPREP GIC ')), len(texts))
    return start, texts[start:end]


def render(spec, start, rec=None, gic_lines=P.GIC_LINES):
    """The renderer's window part for `spec` as the runner prints it (cut at 100 characters), `start` lines of the page having come before it."""
    rec = rec or FIX['rec']
    return [('SNAPREP ' + x)[:100] for x in P.window_lines(spec, rec.ticks_call, rec.ticks_return, rec.monitor_pa, start, {-90: ('NV2_NO_RECORD',)}, gic_lines=gic_lines)]


def cut_values(texts, spec, rec=None):
    """The values of MON CLK, MON CNT and MON EP lines that are not what the window holds, checked without the renderer: a value cut at the runner's 100 columns is a
    different number, and so is any other wrong one. AFTER is the right number or NA, and MORE is a count."""
    rec, found = rec or FIX['rec'], []
    reads, first, last = spec.clock
    clock = {'READS': str(reads), 'FIRST': P.h(first), 'LAST': P.h(last)}
    counters = dict(zip(P.COUNTER_NAMES, map(str, spec.counters)))
    timer = {name: P.h(spec.timer[place]) if hexadecimal else str(spec.timer[place]) for name, place, hexadecimal in P.TIMER_FIELDS}
    endpoints = {str(n): str(calls) for n, calls in enumerate(spec.endpoints) if calls}
    stores = [f'{w >> 32}={P.h(w & 0xFFFFFFFF)}' for _, w in spec.ack.first[:P.ACK_FIRST]] if spec.ack is not None else []
    for text in texts:
        if text.startswith('SNAPREP MON ACK '):         # the count whole, and the stores listed are the first ones of the block in order, each whole
            tokens = text[len('SNAPREP MON ACK '):].split(' ')
            listed = [t for t in tokens[1:] if not t.startswith('MORE=')]
            if spec.ack is None or spec.ack.layout != P.ACK_LAYOUT:
                if not tokens[0].startswith('LAYOUT='):
                    found.append(f'{text!r}: a block that is not there or not read')
            elif tokens[0] != f'COUNT={spec.ack.total}' or listed != stores[:len(listed)]:
                found.append(f'{text!r}: not the count and the first stores of the block')
        for prefix, table in (('SNAPREP MON CLK ', clock), ('SNAPREP MON CNT ', counters), ('SNAPREP MON TIM ', timer),
                              ('SNAPREP MON EP ', {'TOTAL': str((sum(spec.endpoints) + spec.other) & P.MASK), 'OTHER': str(spec.other)})):
            if not text.startswith(prefix):
                continue
            for token in text[len(prefix):].split(' '):
                key, _, value = token.partition('=')
                if prefix.endswith('EP ') and ':' in token:
                    key, _, value = token.partition(':')
                    ok = endpoints.get(key) == value
                elif key == 'MORE':
                    ok = value.isdigit() and int(value) > 0
                elif key == 'AFTER':
                    ok = value == 'NA' or value == str(last - rec.ticks_call)
                else:
                    ok = table.get(key) == value
                if not ok:
                    found.append(f'{text!r}: {token!r} is not a value of the window')
    return found


def host_decoder():
    """probes/run.py's s2_fault_words and the two tables it reads, compiled from that file's source alone (the module is a tool that does much more when imported)."""
    wanted = ('FSC_KINDS', 'FSC_OTHER')
    body = [n for n in ast.parse((ROOT / 'probes/run.py').read_text()).body
            if (isinstance(n, ast.Assign) and any(getattr(t, 'id', None) in wanted for t in n.targets)) or (isinstance(n, ast.FunctionDef) and n.name == 's2_fault_words')]
    space = {}
    exec(compile(ast.Module(body, []), 'probes/run.py', 'exec'), space)
    return space['s2_fault_words']


def syndrome_problems(line, words, esr):
    """What differs between a MON ESR line and the host's words for the same syndrome (probes/run.py s2_fault_words): the fault status, S1PTW, the direction, the size
    and the register, and ISV; an instruction abort has no access fields and a syndrome that is no abort from EL1 has nothing but its EC."""
    fields = dict(token.split('=') for token in line.split(' ')[3:])
    ec, found = esr >> 26, []
    if ec not in (0x20, 0x24):
        return [] if 'not an abort from EL1' in words and set(fields.values()) <= {'NA', P.h(ec)} else [f'{line!r} for {words!r}']
    if fields['DFSC'] != P.h(esr & 0x3F):
        found.append('the fault status')
    if ('on the stage-1 table walk' in words) != (fields['S1PTW'] == '1'):
        found.append('S1PTW')
    if ec == 0x20:
        if 'instruction fetch' not in words or any(fields[key] != 'NA' for key in ('ISV', 'SIZE', 'SRT', 'WNR')):
            found.append('an instruction abort has no access fields')
        return found
    access = re.search(r'(write|read)(?: of (\d+) bytes?, ([xw])(\d+)|, ISV clear)', words)
    if not access:
        return [*found, 'no access in the words']
    if fields['WNR'] != ('1' if access.group(1) == 'write' else '0'):
        found.append('the direction')
    want = ('1', access.group(2), access.group(4)) if access.group(2) else ('0', 'NA', 'NA')
    if (fields['ISV'], fields['SIZE'], fields['SRT']) != want:
        found.append('ISV, the size or the register')
    return found


def violations(r, spec, rec=None):
    """What is wrong with the page of `r` for a window made from `spec`: the budget, the panel text, the width of the lines built from values, the five GIC lines and the
    window's lines against the renderer."""
    found = held(r)
    texts = r.texts()
    start, got = window_part(texts)
    want = render(spec, start, rec)
    if got != want:
        i = next((i for i, (g, w) in enumerate(zip(got, want)) if g != w), min(len(got), len(want)))
        found.append(f'the window part differs at line {i}: got {got[i] if i < len(got) else None!r}, want {want[i] if i < len(want) else None!r}')
    found += cut_values(texts, spec, rec)
    found += [f'{t!r} is wider than {P.WIDE}' for t in texts if t.startswith(NEW_PREFIXES) and len(t) > P.WIDE]
    if len([t for t in texts if t.startswith('SNAPREP GIC ')]) != 5:
        found.append('the five GIC lines are not all on the page')
    return found


# ---- The windows --------------------------------------------------------------------------------------------------------------------------------------
def console_text(total=None):
    """A synthetic kernel console: a banner of 99 characters that folds into two lines of the page, four short lines and a long rest."""
    lines = [b'Synthetic banner: a line of ninety-nine characters, so that the console folds it into two panel lines of 64 columns',
             b'second line of the console', b'third line: serial=3 wdt=-1', b'fourth line', b'fifth line (the sixth of the page)']
    text = b'\n'.join(lines) + b'\n'
    if total:
        text += b'rest ' * ((total - len(text)) // 5)
        text += b'x' * (total - len(text))
    return text


def spec_boot20():
    """The situation of boot 20, as far as the page of that boot said (OBSERVED: a watchdog stop at PC 0xFFFFFE000C1EEF00, EVENTS=512 TRAPS=3127 ABORTS=0
    STEPS=0 CALLS=1443 DROPPED=834, six console lines): the rest is a model. x10 = 0 and x21 = 1 are the prediction of the stop-site analysis (INFERRED,
    falsifiable by this page); the counters, the calls by endpoint, the clock and the guest timer's words (a guest that armed its timer 41 times and never took
    a deadline: no assert, no vector) are made up so that the arithmetic can be checked (the buckets and OTHER add up to CALLS) and are not what the unit holds."""
    call = FIX['rec'].ticks_call
    x = [0xFFFFFE0007004000 + 0x1111 * (i + 1) for i in range(31)]
    x[10], x[21] = 0, 1
    endpoints = [0] * 64
    for n, calls in {1: 150, 2: 560, 3: 40, 4: 2, 5: 311, 12: 3, 13: 1, 14: 5, 15: 1, 18: 1, 19: 5, 20: 1, 21: 30, 45: 327, 50: 5}.items():
        endpoints[n] = calls
    text = console_text(327)
    events = [(1, 0xFFFFFE000C22B000 + 0x40 * i, 0x20 + i, 0x300 + i, 0, 1) for i in range(6)]
    return P.Spec(words=(0, 0, 3127, 512, 0, 0, 0, 1443 | len(text) << 32), events=tuple(events), console=text, capture=1,
                  counters=(700, 0, 0, 0, 0, 0, 834, 0, 0, 0, 0, 0, 0), terminal=(6, 0xFFFFFE000C1EEF00, 0, 0, 0xD3AA0A80, 0),
                  regs=(*x, 0xFFFFFE0007FF4000, 0x804003C4, 0xFFFFFE000C1EEF00, 0, 0), endpoints=tuple(endpoints), other=1,
                  clock=(1250, call + 2_880_000, call + 9_600_000), timer=(310, 41, 5042, 0, 0, 0, 1, 0x1F3A5C0D0, 0xFFFFFE000C1EEF00))


def esr_word(ec=0x24, isv=1, sas=3, srt=8, wnr=1, s1ptw=0, dfsc=0x06, il=1):
    """An ESR_EL2 laid out as probes/run.py s2_fault_words reads it: EC 31:26, IL 25, ISV 24, SAS 23:22, SRT 20:16, S1PTW 7, WnR 6 and the fault status 5:0 (0x06 is a
    translation fault at level 2). Written from the field positions, not from the reporter."""
    return ec << 26 | il << 25 | isv << 24 | sas << 22 | srt << 16 | s1ptw << 7 | wnr << 6 | dfsc


def spec_s2(esr=0x93C08046, **over):
    """A stage-2 fault with an IPA: the expected end of M1 (a model: the IPA, the syndrome and the registers are made up). `esr` is the ESR_EL2 of the fault, in the
    stop's B and in the capture's ESR word, and the capture is of class 3, as the monitor leaves it for such a fault (probes/xnuboot/trap_dispatch.c). 0x93C08046 is a
    store of 8 bytes from x0 (EC 0x24, ISV, SAS 3, SRT 0, WnR) that missed at level 2. `over` replaces fields of the window."""
    x = [0xFFFFFE0008000000 + 0x2020 * i for i in range(31)]
    endpoints = [0] * 64
    endpoints[2], endpoints[45], endpoints[19] = 1200, 400, 6
    fields = dict(words=(0, 0, 9000, 512, 2, 0, 0, 2000 | 400 << 32), events=tuple((1, 0xFFFFFE000C230000 + 8 * i, 0x13, 0, 0, 1) for i in range(5)),
                  console=console_text(400), capture=3, counters=(1200, 0, 0, 0, 0, 0, 1500, 0, 0, 12, 400, 3500, 300),
                  terminal=(2, 0xFFFFFE000C1F4A10, 0x28E104004, esr, 0xFFFFFE0021EB4004, 0),
                  regs=(*x, 0xFFFFFE0007FF0000, 0x600003C5, 0xFFFFFE000C1F4A10, esr, 0xFFFFFE0021EB4004), endpoints=tuple(endpoints),
                  clock=(5, 10_000_000 + FIX['rec'].ticks_call, 70_000_000 + FIX['rec'].ticks_call),
                  timer=(900, 120, 3000, 40, 40, 40, 1, 0x4A3B2C1D0, 0xFFFFFE000C1F4A00))
    return P.Spec(**{**fields, **over})


def spec_s2_fault():
    return spec_s2()


ESR_CASES = {'store 8 bytes x8': esr_word(sas=3, srt=8), 'store 4 bytes x8': esr_word(sas=2, srt=8), 'store 2 bytes x30': esr_word(sas=1, srt=30),
             'store 1 byte x0': esr_word(sas=0, srt=0), 'store 4 bytes wzr': esr_word(sas=2, srt=31), 'load': esr_word(wnr=0, srt=9),
             'ISV clear': esr_word(isv=0, sas=0, srt=0), 'ISV clear with bits set': esr_word(isv=0, sas=3, srt=17), 'stage-1 table walk': esr_word(s1ptw=1),
             'permission fault level 3': esr_word(dfsc=0x0F), 'external abort': esr_word(dfsc=0x10), 'alignment': esr_word(dfsc=0x21),
             'instruction abort': esr_word(ec=0x20, isv=0, sas=0, srt=0, wnr=0, dfsc=0x05), 'instruction abort with data bits set': esr_word(ec=0x20),
             'data abort from EL2': esr_word(ec=0x25), 'a hypervisor call': esr_word(ec=0x16)}


def esr_specs():
    """{'esr0': (description, ESR, window), ...} for the syndromes of ESR_CASES: scenario names have no blanks."""
    return {f'esr{i}': (text, esr, spec_s2(esr)) for i, (text, esr) in enumerate(ESR_CASES.items())}


def spec_panic_entry():
    """A panic entry: the kernel announced a fatal exception, and the stop that ended the run left the capture of class 1 that the generic stop path of
    probes/xnuboot/trap_entry.c and the watchdog's of async.c leave (INFERRED from those sources, not observed: the page of a run that stopped here is the
    first to tell). The registers are made up; the PC of the capture is the instruction after the call, at which XNU's `wfe` waits."""
    endpoints = [0] * 64
    endpoints[2], endpoints[45] = 900, 1000
    x = [0xFFFFFE0009000000 + 0x3131 * i for i in range(31)]
    return P.Spec(words=(0, 0, 4000, 512, 0, 0, 0, 1900 | 1000 << 32), events=tuple((1, 0xFFFFFE000C230000 + 8 * i, 0x2D, 0, 0, 1) for i in range(5)),
                  console=console_text(1000), capture=1, counters=(900, 0, 0, 1, 0, 0, 100, 0, 0, 0, 0, 0, 0),
                  terminal=(9, 0xFFFFFE000B86EBBC, 0xFFFFFE000B86E000, 0xFFFFFE000C1F0000, 0x96000005, 0), endpoints=tuple(endpoints),
                  regs=(*x, 0xFFFFFE0007FE8000, 0x800003C5, 0xFFFFFE000B86EBC0, 0, 0), clock=(40, FIX['rec'].ticks_call + 1000, FIX['rec'].ticks_call + 5000),
                  timer=(40, 6, 900, 3, 3, 3, 5, 0x2A5B3C4D0, 0xFFFFFE000B86EBB8))


def spec_panic_entry_nocap():
    """The same window with the capture word zero (UNKNOWN whether every stop leaves a capture): no register lines and no MON EXIT, so the events and the
    console keep all their lines."""
    return dataclasses.replace(spec_panic_entry(), capture=0)


def spec_guest_exc():
    """A guest exception: PC= is the guest's x30 and A, B, C are its ESR, FAR and ELR; the capture is that of class 1 (a model: the registers are made up)."""
    endpoints = [0] * 64
    endpoints[2], endpoints[3], endpoints[45] = 100, 50, 200
    x = [0xFFFFFE0009100000 + 0x2525 * i for i in range(30)] + [0xFFFFFE000B86F000]
    return P.Spec(words=(0, 0, 500, 512, 0, 0, 0, 350 | 200 << 32), events=tuple((1, 0xFFFFFE000C230000 + 8 * i, 0x2D, 0, 0, 1) for i in range(5)),
                  console=console_text(200), capture=1, counters=(100, 3, 0, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0),
                  terminal=(3, 0xFFFFFE000B86F000, 0x96000045, 0x10, 0xFFFFFE000C1F0000, 0), endpoints=tuple(endpoints),
                  regs=(*x, 0xFFFFFE0007FD0000, 0x600003C5, 0xFFFFFE000C1F0000, 0x96000045, 0x10), clock=(0, 0, 0))


def spec_full():
    """Every field full: sixty-four buckets, 64-bit registers, a console of 1,024 bytes, the longest values the lines can carry. The page must be exactly the
    budget: 40 lines, with the first event and one console line left for the events and the console."""
    call = FIX['rec'].ticks_call
    x = [0xFFFFFE000C000000 + 0x1111 * (i + 1) for i in range(31)]
    text = console_text(1024)
    return P.Spec(words=(0, 0, 123456789, 512, 7 | 5 << 32, 0, 0, 98765 | len(text) << 32), events=tuple((1, 0xFFFFFE000C230000 + 8 * i, 0x2D, 1, 2, 3) for i in range(5)),
                  console=text, capture=2, counters=(1234, 2345, 3456, 4567, 5678, 6789, 7891, 8912, 9123, 1234, 2345, 3456, 4567),
                  terminal=(6, 0xFFFFFE000C1EEF00, 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFE, 0xFFFFFFFFFFFFFFFD, 0),
                  regs=(*x, 0xFFFFFFFFFFFFFFF0, 0xFFFFFFFFFFFFFFFF, 0xFFFFFE000C1EEF00, 0xFFFFFFFFFFFFFFFE, 0xFFFFFFFFFFFFFFFD),
                  endpoints=tuple(100000 + 7 * n for n in range(64)), other=12345, clock=(9_999_999, call + 1_000_000, call + 96_000_000),
                  timer=(123456, 7890, 5042000, 1234, 1233, 1234, 5, 0x1F3A5C0D0, 0xFFFFFE000C1EEF00))


def spec_clock_odd():
    """A clock whose last sample is below the call's start (counters that disagree: CNTVOFF_EL2 not zero would do it): AFTER is not computed."""
    call = FIX['rec'].ticks_call
    return P.Spec(words=(0, 0, 10, 512, 0, 0, 0, 10 | 9 << 32), events=((1, 0xFFFFFE000C230000, 0x13, 0, 0, 1),), console=b'one line\n', counters=(1,) * 13,
                  terminal=(6, 0xFFFFFE000C1EEF00, 0, 0, 0, 0), clock=(3, call - 1000, call - 10))


def spec_clock_late():
    """A clock whose last sample is above the call's end (the units disagree: a sample is taken inside the call, so AFTER could not be above TICKS): AFTER is NA."""
    call = FIX['rec'].ticks_call
    return dataclasses.replace(spec_clock_odd(), clock=(3, call + 1000, call + CALL_TICKS + 1))


def spec_clock_end():
    """The last sample on the call's last tick: AFTER is TICKS, the greatest value it can have."""
    call = FIX['rec'].ticks_call
    return dataclasses.replace(spec_clock_odd(), clock=(3, call + 1000, call + CALL_TICKS))


WIDE_CALL = 0x1000000000000000      # a wrapper whose counter read before the call was already this high: the raw samples then take sixteen hex digits
WIDE_COUNTERS = (12345, 23456, 34567, 45678, 56789, 67891, 78912, 89123, 91234, 12345, 23456, 3456, 45678)     # five digits but MMIOWR's: 88, 95 and 41 characters on three lines


def spec_clock_wide():
    """The widest clock line that a sane window can make: READS of twenty digits and raw samples of sixteen hex digits, with AFTER a number. The last field gives way to MORE."""
    return dataclasses.replace(spec_clock_odd(), clock=(2 ** 64 - 1, WIDE_CALL + 1000, WIDE_CALL + CALL_TICKS - 5))


def spec_clock_huge():
    """A call of 2 ** 62 ticks (not a real one: the units are wrong) with AFTER of nineteen digits: FIRST would end the line at 93 characters, which leaves no room for the MORE that
    LAST then needs, so FIRST gives way as well."""
    return dataclasses.replace(spec_clock_odd(), clock=(2 ** 64 - 1, WIDE_CALL + 5, WIDE_CALL + 10 ** 18))


def spec_wide_counters():
    """A watchdog stop whose thirteen counters have five digits (MMIOWR four) and whose console is empty: they need three lines, which the page has room for (the first
    event is the only line kept for what follows; with console text the page has the two lines of wide-counters-s2), and none is cut."""
    return dataclasses.replace(spec_panic_entry(), words=(0, 0, 4000, 512, 0, 0, 0, 1900), console=b'', counters=WIDE_COUNTERS, terminal=(6, 0xFFFFFE000C1EEF00, 0, 0, 0xD3AA0A80, 0))


def spec_wide_counters_s2():
    """A stage-2 fault whose counters are the same: the page has two lines for them, MMIOWR would end the second at 95 characters but leaves no room for the MORE that UARTTX
    then needs, so MMIOWR and UARTTX are counted in MORE and none is cut."""
    return spec_s2(counters=WIDE_COUNTERS)


def spec_all_ones():
    """A window of garbage in the words the new lines print (the capture word, the counters, the clock and the buckets all ones; the sums wrap): the lines give way to
    MORE counts, a counter or a sample is never cut at the runner's 100 columns, and the all-ones capture word is no capture."""
    ones = 2 ** 64 - 1
    return dataclasses.replace(spec_panic_entry(), capture=ones, counters=(ones,) * 13, clock=(ones, ones, ones), endpoints=(ones,) * 64, other=ones, timer=(ones,) * 9)


def spec_s2_full():
    """The worst page of a stage-2 fault: a store of x30 (64 bits) with every field wide, sixty-four buckets, a console of 1,024 bytes and five events. The page is
    exactly the budget, 40 lines: the syndrome, the value written, the registers, the clock, two counter lines and one histogram line leave one event and one console line."""
    call = FIX['rec'].ticks_call
    esr = esr_word(srt=30)
    x = [0xFFFFFE000C000000 + 0x1111 * (i + 1) for i in range(30)] + [0xFFFFFFFFFFFFFFFF]
    text = console_text(1024)
    return spec_s2(esr, words=(0, 0, 123456789, 512, 7 | 5 << 32, 0, 0, 98765 | len(text) << 32), console=text,
                   counters=(1234, 2345, 3456, 4567, 5678, 6789, 7891, 8912, 9123, 1234, 2345, 3456, 4567),
                   terminal=(2, 0xFFFFFE000C1F4A10, 0xFFFFFFFFFFFF, esr, 0xFFFFFFFFFFFFFFFE, 0),
                   regs=(*x, 0xFFFFFFFFFFFFFFF0, 0xFFFFFFFFFFFFFFFF, 0xFFFFFE000C1F4A10, esr, 0xFFFFFFFFFFFFFFFE), endpoints=tuple(100000 + 7 * n for n in range(64)),
                   other=12345, clock=(9_999_999, call + 1_000_000, call + 96_000_000))


def spec_s2_nocap():
    """A stage-2 fault whose window has no capture (not what the monitor leaves, which gives such a stop the class 3): the syndrome is the stop's and prints, and there
    are no registers to take the value from."""
    return spec_s2(capture=0)


def spec_s2_class_1():
    """A stage-2 fault with a capture of class 1 (not what the monitor leaves for it, which is class 3): the registers print, but the value written does not: only the
    capture of a stage-2 fault is the registers of the faulting instruction."""
    return spec_s2(capture=1)


def spec_s2_odd_capture():
    """A stage-2 fault with a capture word of 2 ** 32 + 3: its low half is the class of a stage-2 fault and the word is not a class the monitor stores (a word cut to 32
    bits would take it for one): no registers and no value."""
    return spec_s2(capture=2 ** 32 + 3)


def spec_mmio_stop():
    """An MMIO stop (EV_MMIO_STOP 16: b is an ESR_EL2 too, with a store's syndrome) with the capture of class 3: only the stop of a stage-2 fault has its syndrome and
    the value written decoded, so this page has neither line."""
    return spec_s2(esr_word(), terminal=(16, 0xFFFFFE000C1F4A10, 0x7000000040, esr_word(), 0x28E104004, 0))


def spec_capture_4():
    """The first word that is not a class of the capture: the page prints no MON EXIT and no registers."""
    return dataclasses.replace(spec_panic_entry(), capture=4)


def spec_capture_odd():
    return dataclasses.replace(spec_panic_entry(), capture=0x1234)


def spec_wide_timer():
    """A watchdog stop with a capture but no events and no console, whose nine timer words are 64-bit all ones: nothing is kept for the events and the console, so the page has
    room for two timer lines: IRQS and ASSERTS fill the first, CLEARS and VECTORS go on the second and the other five are counted in MORE (CTL does not fit with the room MORE
    needs), none cut."""
    return dataclasses.replace(spec_panic_entry(), words=(0, 0, 4000, 0, 0, 0, 0, 1900), events=(), console=b'', timer=(2 ** 64 - 1,) * 9,
                               terminal=(6, 0xFFFFFE000C1EEF00, 0, 0, 0xD3AA0A80, 0))


def spec_wide_timer_s2():
    """The same words on a stage-2 fault, whose page has one line for them: IRQS and ASSERTS, then MORE=7."""
    return spec_s2(timer=(2 ** 64 - 1,) * 9)


FIXTURES = {'boot20': spec_boot20, 's2-fault': spec_s2_fault, 'panic-entry': spec_panic_entry, 'panic-entry-nocap': spec_panic_entry_nocap, 'guest-exc': spec_guest_exc,
            'full': spec_full, 'clock-odd': spec_clock_odd, 'clock-late': spec_clock_late, 'clock-end': spec_clock_end, 'clock-wide': spec_clock_wide,
            'wide-counters': spec_wide_counters, 'wide-counters-s2': spec_wide_counters_s2, 'all-ones': spec_all_ones, 's2-full': spec_s2_full, 's2-nocap': spec_s2_nocap,
            's2-odd-capture': spec_s2_odd_capture, 'mmio-stop': spec_mmio_stop, 'capture-4': spec_capture_4, 'capture-odd': spec_capture_odd,
            'wide-timer': spec_wide_timer, 'wide-timer-s2': spec_wide_timer_s2, 'clock-huge': spec_clock_huge, 's2-class-1': spec_s2_class_1}
RECORDS = {'clock-wide': dict(call=WIDE_CALL), 'clock-huge': dict(call=WIDE_CALL, ticks=2 ** 62)}      # fixtures whose wrapper record has other ticks around the call
FIXTURES.update({name: (lambda build=build: build(FIX['rec'].ticks_call)) for name, build in PF.BUILDERS.items()})      # an acknowledging monitor's windows: tools/sp11_stick_monitor_pagefix.py


def fixture_reports():
    """(specs, REPORT.DAT files, decoded wrapper records of the fixtures that have their own) of every fixture."""
    specs = {name: make() for name, make in FIXTURES.items()}
    recs = {name: with_ticks(RUN['rec'], **how) for name, how in RECORDS.items()}
    return specs, {name: report_with(P.build(spec), recs.get(name)) for name, spec in specs.items()}, {name: M.decode(raw) for name, raw in recs.items()}


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not runnable():
            raise unittest.SkipTest('no clang or no q1n1 checkout (Q1N1_SP11_TREE): the harness is not built')
        cls.specs, reports, cls.recs = fixture_reports()
        cls.results = run_reporters(FIX['exe'], reports)


class Fixtures(Base):
    def test_every_fixture_is_the_renderers_page_and_nothing_else(self):
        for name, spec in self.specs.items():
            with self.subTest(name):
                self.assertEqual(violations(self.results[name], spec, self.recs.get(name)), [], name)

    def test_boot_20_prints_the_lines_the_note_asked_for(self):
        spec, texts = self.specs['boot20'], self.results['boot20'].texts()
        self.assertIn('SNAPREP MON COUNTS EVENTS=512 TRAPS=3127 ABORTS=0 STEPS=0 CALLS=1443 DROPPED=834', texts)
        self.assertIn('SNAPREP MON STOP WATCHDOG PC=0XFFFFFE000C1EEF00 A=0X0 B=0X0 C=0XD3AA0A80', texts)
        self.assertIn('SNAPREP MON EXIT PC=0XFFFFFE000C1EEF00 SPSR=0X804003C4 ESR=0X0 FAR=0X0', texts)
        gx = [t.split(' ') for t in texts if t.startswith('SNAPREP MON GX')]
        self.assertEqual([g[2] for g in gx], ['GX0-3', 'GX4-7', 'GX8-11', 'GX12-15', 'GX16-19', 'GX20-23', 'GX24-27', 'GX28-SP'])
        for i, g in enumerate(gx):
            self.assertEqual(g[3:], ['0X%X' % v for v in spec.regs[4 * i:4 * i + 4]], f'x{4 * i} onward: x0 to x30 and the SP, in order')
        self.assertEqual(gx[2][3:][2], '0X0', 'x10 is zero (the prediction)')
        self.assertEqual(gx[5][3:][1], '0X1', 'x21 is one (the prediction)')
        self.assertEqual(gx[7][3:][3], '0XFFFFFE0007FF4000', 'the last value of the last line is the SP, after x30')
        self.assertEqual(gx[7][3:][2], '0X%X' % spec.regs[30], 'and x30 is the one before it')
        eps = [t for t in texts if t.startswith('SNAPREP MON EP ')]
        self.assertEqual([t for t in texts if t.startswith('SNAPREP MON TIM')],
                         ['SNAPREP MON TIM IRQS=5042 ASSERTS=0 CLEARS=0 VECTORS=0 CTL=0X1 CVAL=0X1F3A5C0D0 WRITES=41', 'SNAPREP MON TIM IRQPC=0XFFFFFE000C1EEF00 READS=310'],
                         'the guest timer, all nine words: no virtual FIQ was ever set or taken (a model); two lines, the second takes what the histogram would have had')
        self.assertTrue(eps[0].startswith('SNAPREP MON EP TOTAL=1443 OTHER=1 1:150 2:560 3:40 4:2 5:311'), eps[0])
        self.assertEqual(len(eps), 1, 'the histogram has the one line that the guest timer and the counters leave')
        self.assertTrue(eps[0].endswith(' 18:1 MORE=5'), 'and what it cannot hold is counted: 5 buckets more, endpoint 45 among them')
        clk = [t for t in texts if t.startswith('SNAPREP MON CLK')]
        self.assertEqual(len(clk), 1)
        self.assertTrue(clk[0].startswith('SNAPREP MON CLK READS=1250 AFTER=9600000 FIRST=0X'), clk[0])

    def test_the_stop_kinds_keep_their_lines_and_a_window_without_a_capture_prints_no_registers_and_no_timer(self):
        for name, kind in (('s2-fault', 'S2_FAULT'), ('panic-entry', 'PANIC_ENTRY'), ('panic-entry-nocap', 'PANIC_ENTRY'), ('guest-exc', 'GUEST_EXC')):
            texts = self.results[name].texts()
            captured = not name.endswith('nocap')
            with self.subTest(name):
                self.assertTrue([t for t in texts if t.startswith(f'SNAPREP MON STOP {kind} ')])
                self.assertEqual(len([t for t in texts if t.startswith('SNAPREP MON GX')]), 8 if captured else 0)
                self.assertEqual(len([t for t in texts if t.startswith('SNAPREP MON EXIT')]), 1 if captured else 0)
                self.assertEqual(len([t for t in texts if t.startswith('SNAPREP MON CNT')]), 2)
                self.assertEqual(len([t for t in texts if t.startswith('SNAPREP MON CLK')]), 1)
                self.assertEqual(bool([t for t in texts if t.startswith('SNAPREP MON TIM')]), captured, 'the guest timer comes with the registers, a page without them has none')
                self.assertEqual(bool([t for t in texts if t.startswith('SNAPREP MON EP ')]), name in ('panic-entry-nocap', 'guest-exc'),
                                 'the histogram is the line that gives way: a stage-2 fault page has none, and neither has a stop whose C= took a line of its own')
        nocap, call = self.results['panic-entry-nocap'].texts(), FIX['rec'].ticks_call
        self.assertEqual([t for t in nocap if t.startswith(('SNAPREP MON CLK', 'SNAPREP MON CNT', 'SNAPREP MON EP '))],
                         ['SNAPREP MON CLK READS=40 AFTER=5000 FIRST=0X%X LAST=0X%X' % (call + 1000, call + 5000),
                          'SNAPREP MON CNT MAPS=900 MAPFAIL=0 DISP=0 PANIC=1 UNDEF=0 MAPOUT=0 LOGDROP=100', 'SNAPREP MON CNT CONDROP=0 RNGDROP=0 MAPDEV=0 MMIORD=0 MMIOWR=0 UARTTX=0',
                          'SNAPREP MON EP TOTAL=1900 OTHER=0 2:900 45:1000'], 'the page of a window without a capture, byte for byte the one the registers\' addition left it')
        self.assertEqual([len([t for t in nocap if t.startswith(p)]) for p in ('SNAPREP MON EV ', 'SNAPREP MON CON ')], [5, 6], 'and every event and console line it showed')
        s2 = self.results['s2-fault'].texts()
        self.assertIn('SNAPREP MON STOP S2_FAULT PC=0XFFFFFE000C1F4A10 A=0X28E104004 B=0X93C08046 C=0XFFFFFE0021EB4004', s2)
        self.assertIn('SNAPREP MON ESR EC=0X24 ISV=1 SIZE=8 SRT=0 WNR=1 S1PTW=0 DFSC=0X6', s2, 'a store of 8 bytes from x0 that missed at level 2')
        self.assertIn('SNAPREP MON WVAL X0=0XFFFFFE0008000000 DATA=0XFFFFFE0008000000', s2, 'what it wrote: x0, whole')
        self.assertIn('SNAPREP MON CLK READS=5 AFTER=70000000 FIRST=0X%X LAST=0X%X' % (10_000_000 + FIX['rec'].ticks_call, 70_000_000 + FIX['rec'].ticks_call), s2)
        self.assertEqual([t for t in s2 if t.startswith('SNAPREP MON TIM')], ['SNAPREP MON TIM IRQS=3000 ASSERTS=40 CLEARS=40 VECTORS=40 CTL=0X1 CVAL=0X4A3B2C1D0 MORE=3'],
                         'one line on this page: the chain that delivers a deadline and the guest timer\'s state first, three words counted in MORE')
        for name in ('boot20', 'panic-entry', 'panic-entry-nocap', 'guest-exc', 'full', 'clock-odd', 'mmio-stop'):
            self.assertFalse([t for t in self.results[name].texts() if t.startswith(('SNAPREP MON ESR', 'SNAPREP MON WVAL'))], f'{name} is no stage-2 fault')
        panic = self.results['panic-entry'].texts()
        self.assertIn('SNAPREP MON EXIT PC=0XFFFFFE000B86EBC0 SPSR=0X800003C5 ESR=0X0 FAR=0X0', panic)
        self.assertIn('SNAPREP MON CNT MAPS=900 MAPFAIL=0 DISP=0 PANIC=1 UNDEF=0 MAPOUT=0 LOGDROP=100', panic)
        exc = self.results['guest-exc'].texts()
        self.assertIn('SNAPREP MON CLK READS=0 AFTER=NA FIRST=0X0 LAST=0X0', exc)
        self.assertIn('SNAPREP MON CNT MAPS=100 MAPFAIL=3 DISP=0 PANIC=0 UNDEF=2 MAPOUT=0 LOGDROP=0', exc)
        bare = self.results['panic-entry-nocap'].texts()
        self.assertEqual(len([t for t in bare if t.startswith('SNAPREP MON EV ')]), 5, 'with no register lines the page has room for all five events')
        self.assertEqual(len([t for t in bare if t.startswith('SNAPREP MON CON ')]), 6, 'and for six console lines')

    def test_the_full_window_is_exactly_the_budget_and_keeps_every_line_the_record_names(self):
        r = self.results['full']
        texts = r.texts()
        self.assertEqual(len(texts), sb.REPORTER_LINES, 'the worst case fills the 40 lines of the runner and not one more')
        self.assertLessEqual(sb.WRAPPER_LINES + len(texts) + 2 * 3 + sb.FILES + 1, 89, 'the 89 rows of the unit\'s panel (docs/SP11-STICK-BUNDLE.md section 2)')
        self.assertEqual(held(r), [])
        for prefix in CITED:
            with self.subTest(prefix):
                self.assertTrue([t for t in texts if t.startswith(prefix)], f'{prefix} is on the page')
        self.assertEqual(len([t for t in texts if t.startswith('SNAPREP MON EV ')]), 1)
        self.assertEqual(len([t for t in texts if t.startswith('SNAPREP MON CON ')]), 1)
        self.assertEqual(len([t for t in texts if t.startswith('SNAPREP MON GX')]), 8)
        self.assertEqual([t for t in texts if t.startswith('SNAPREP MON TIM')],
                         ['SNAPREP MON TIM IRQS=5042000 ASSERTS=1234 CLEARS=1233 VECTORS=1234 CTL=0X5 CVAL=0X1F3A5C0D0', 'SNAPREP MON TIM WRITES=7890 IRQPC=0XFFFFFE000C1EEF00 READS=123456'],
                         'two lines: the nine words, none counted in MORE')
        self.assertEqual(len([t for t in texts if t.startswith('SNAPREP MON EP ')]), 0, 'the line that the stop\'s C= took was the histogram\'s')
        self.assertLessEqual(max(len(t) for t in texts), 100)
        regs = [t for t in texts if t.startswith('SNAPREP MON GX')]
        self.assertLessEqual(max(len(t) for t in regs), 95, 'four values of 18 characters and the longest name')

    def test_every_page_line_is_in_the_panels_font_within_the_runner_s_width(self):
        for name, r in self.results.items():
            with self.subTest(name):
                self.assertEqual([t for t in r.texts() if not set(t) <= P.FONT or len(t) > sb.LINE_MAX], [])

    def test_the_budget_never_takes_the_gic_lines_or_the_first_event_and_console_line(self):
        for name, r in self.results.items():
            with self.subTest(name):
                texts, words = r.texts(), self.specs[name].words
                self.assertGreaterEqual(len([t for t in texts if t.startswith('SNAPREP GIC ')]), 5)
                self.assertEqual(bool([t for t in texts if t.startswith('SNAPREP MON EV 0 ')]), bool(words[3]), 'the first event, where the window has one')
                self.assertEqual(bool([t for t in texts if t.startswith('SNAPREP MON CON ')]), bool(words[7] >> 32), 'the first console line, where it has console bytes')

    def test_a_prepare_failure_prints_none_of_the_new_lines(self):
        spec = P.Spec(status=-90, entered=0, words=(0, 0, 0, 1, 0, 0, 0, 0), events=((5, 0x4E56, 0x100000001, 0xFEED4B01, 0, 0),), capture=1, clock=(5, 9, 99),
                      counters=(1,) * 13, endpoints=(3,) * 64)
        r = run_reporters(FIX['exe'], {'prepare': report_with(P.build(spec))})['prepare']
        self.assertEqual(violations(r, spec), [])
        self.assertIn('SNAPREP MON PREPARE FAILED CODE=-90 NV2_NO_RECORD', r.texts())
        self.assertFalse([t for t in r.texts() if t.startswith(NEW_PREFIXES)])

    def test_a_clock_that_never_ran_or_ran_outside_the_call_says_na(self):
        call = FIX['rec'].ticks_call
        for clock, after in (((0, 0, 0), 'NA'), ((3, call - 1000, call - 10), 'NA'), ((3, call + 1, call), '0'), ((3, 5, call + 19_200_000), '19200000'),
                             ((3, 5, call + CALL_TICKS), str(CALL_TICKS)), ((3, 5, call + CALL_TICKS + 1), 'NA'), ((3, 5, 2 ** 64 - 1), 'NA')):
            spec = P.Spec(clock=clock, terminal=(6, 0xFFFFFE000C1EEF00, 0, 0, 0, 0))
            r = run_reporters(FIX['exe'], {'clock': report_with(P.build(spec))})['clock']
            with self.subTest(clock=clock):
                self.assertEqual(violations(r, spec), [])
                self.assertIn(f'SNAPREP MON CLK READS={clock[0]} AFTER={after} FIRST=0X%X LAST=0X%X' % (clock[1], clock[2]), r.texts())

    def test_the_worst_stage_2_fault_page_is_exactly_the_budget_with_the_syndrome_and_the_value_written(self):
        r = self.results['s2-full']
        texts = r.texts()
        self.assertEqual(len(texts), sb.REPORTER_LINES, 'the 40 lines of the runner and not one more')
        self.assertLessEqual(sb.WRAPPER_LINES + len(texts) + 2 * 3 + sb.FILES + 1, 89, 'the 89 rows of the unit\'s panel (docs/SP11-STICK-BUNDLE.md section 2)')
        self.assertEqual(held(r), [])
        for prefix in CITED:
            with self.subTest(prefix):
                self.assertTrue([t for t in texts if t.startswith(prefix)], f'{prefix} is on the page')
        for prefix, n in (('SNAPREP MON ESR', 1), ('SNAPREP MON WVAL', 1), ('SNAPREP MON GX', 8), ('SNAPREP MON CLK', 1), ('SNAPREP MON TIM', 1), ('SNAPREP MON CNT', 2), ('SNAPREP MON EP ', 0),
                          ('SNAPREP MON EV ', 1), ('SNAPREP MON CON ', 1), ('SNAPREP GIC ', 5)):
            self.assertEqual(len([t for t in texts if t.startswith(prefix)]), n, prefix)
        self.assertIn('SNAPREP MON ESR EC=0X24 ISV=1 SIZE=8 SRT=30 WNR=1 S1PTW=0 DFSC=0X6', texts)
        self.assertIn('SNAPREP MON WVAL X30=0XFFFFFFFFFFFFFFFF DATA=0XFFFFFFFFFFFFFFFF', texts, 'the longest of the two lines: 63 characters')
        self.assertLessEqual(max(len(t) for t in texts if t.startswith(NEW_PREFIXES)), P.WIDE)

    def test_no_value_of_a_new_line_is_cut_and_what_does_not_fit_is_counted_in_more(self):
        counters = ['MAPS=12345', 'MAPFAIL=23456', 'DISP=34567', 'PANIC=45678', 'UNDEF=56789', 'MAPOUT=67891', 'LOGDROP=78912', 'CONDROP=89123', 'RNGDROP=91234',
                    'MAPDEV=12345', 'MMIORD=23456', 'MMIOWR=3456', 'UARTTX=45678']
        cnt = lambda name: [t for t in self.results[name].texts() if t.startswith('SNAPREP MON CNT')]       # noqa: E731
        self.assertEqual(cnt('wide-counters'), ['SNAPREP MON CNT ' + ' '.join(counters[:6]), 'SNAPREP MON CNT ' + ' '.join(counters[6:12]),
                                                'SNAPREP MON CNT ' + ' '.join(counters[12:])], 'five digits (MMIOWR four): 88, 95 and 22 characters, three lines where there is room')
        self.assertEqual(cnt('wide-counters-s2'), ['SNAPREP MON CNT ' + ' '.join(counters[:6]), 'SNAPREP MON CNT ' + ' '.join(counters[6:11]) + ' MORE=2'],
                         'the stage-2 page has two lines: MMIOWR would fit the second at 95 but leaves no room for the MORE that UARTTX needs: both are counted, not cut')
        self.assertEqual([t for t in self.results['clock-wide'].texts() if t.startswith('SNAPREP MON CLK')],
                         ['SNAPREP MON CLK READS=18446744073709551615 AFTER=%d FIRST=0X%X MORE=1' % (CALL_TICKS - 5, WIDE_CALL + 1000)],
                         'twenty digits and two sixteen-digit samples do not fit 95 columns: the last gives way, whole or not at all')
        self.assertEqual([t for t in self.results['clock-huge'].texts() if t.startswith('SNAPREP MON CLK')],
                         ['SNAPREP MON CLK READS=18446744073709551615 AFTER=%d MORE=2' % 10 ** 18], 'FIRST would end the line at 93 and leave no room for the MORE that LAST needs')
        wide = '18446744073709551615'
        self.assertEqual([t for t in self.results['wide-timer'].texts() if t.startswith('SNAPREP MON TIM')],
                         [f'SNAPREP MON TIM IRQS={wide} ASSERTS={wide}', f'SNAPREP MON TIM CLEARS={wide} VECTORS={wide} MORE=5'], 'two lines of 70 and 79 characters')
        self.assertEqual([t for t in self.results['wide-timer-s2'].texts() if t.startswith('SNAPREP MON TIM')], [f'SNAPREP MON TIM IRQS={wide} ASSERTS={wide} MORE=7'],
                         'one line of 77 characters on the stage-2 page')
        ones = self.results['all-ones'].texts()
        self.assertIn('SNAPREP MON CLK READS=18446744073709551615 AFTER=NA FIRST=0XFFFFFFFFFFFFFFFF MORE=1', ones)
        lines = [t for t in ones if t.startswith('SNAPREP MON CNT')]
        shown = [token for t in lines for token in t.split(' ')[3:] if not token.startswith('MORE=')]
        self.assertEqual(shown, [f'{name}=18446744073709551615' for name in P.COUNTER_NAMES[:len(shown)]], 'the counters shown are a prefix of the thirteen, each whole')
        self.assertEqual(lines[-1].split(' ')[-1], f'MORE={13 - len(shown)}')
        self.assertGreater(13 - len(shown), 0)
        self.assertTrue([t for t in ones if t.startswith('SNAPREP MON EP TOTAL=')], 'the sum of the buckets wraps and the line is whole')
        for name, r in self.results.items():
            with self.subTest(name):
                self.assertEqual(cut_values(r.texts(), self.specs[name], self.recs.get(name)), [])

    def test_a_capture_word_the_monitor_never_stores_is_no_capture(self):
        for name in ('capture-4', 'capture-odd', 's2-odd-capture', 'all-ones'):
            texts = self.results[name].texts()
            with self.subTest(name):
                self.assertFalse([t for t in texts if t.startswith(('SNAPREP MON EXIT', 'SNAPREP MON GX', 'SNAPREP MON WVAL', 'SNAPREP MON TIM'))], 'nothing of a capture from a word that is no capture')
                self.assertTrue([t for t in texts if t.startswith('SNAPREP MON CLK')])
        self.assertTrue([t for t in self.results['s2-odd-capture'].texts() if t.startswith('SNAPREP MON ESR')], 'the syndrome is the stop\'s and needs no capture')
        self.assertFalse([t for t in self.results['s2-nocap'].texts() if t.startswith(('SNAPREP MON EXIT', 'SNAPREP MON GX', 'SNAPREP MON WVAL'))])
        for name in ('panic-entry', 'full', 's2-fault'):
            self.assertEqual(len([t for t in self.results[name].texts() if t.startswith('SNAPREP MON GX')]), 8, f'{name}: the classes 1, 2 and 3 are captures')
        class_1 = self.results['s2-class-1'].texts()
        self.assertEqual(len([t for t in class_1 if t.startswith('SNAPREP MON GX')]), 8)
        self.assertTrue([t for t in class_1 if t.startswith('SNAPREP MON ESR')])
        self.assertFalse([t for t in class_1 if t.startswith('SNAPREP MON WVAL')], 'the value written is the register of the faulting instruction only for the capture of a stage-2 fault')

    def test_the_syndrome_and_the_value_written_agree_with_the_hosts_decoder(self):
        decoder, table = host_decoder(), esr_specs()
        results = run_reporters(FIX['exe'], {key: report_with(P.build(spec)) for key, (text, esr, spec) in table.items()})
        for key, (text, esr, spec) in table.items():
            texts = results[key].texts()
            with self.subTest(text):
                self.assertEqual(violations(results[key], spec), [], text)
                line = [t for t in texts if t.startswith('SNAPREP MON ESR ')]
                self.assertEqual(len(line), 1)
                self.assertEqual(syndrome_problems(line[0], decoder(0x28E104004, esr), esr), [])
                stored = esr >> 26 == 0x24 and esr >> 24 & 1 and esr >> 6 & 1 and not esr >> 7 & 1
                self.assertEqual(len([t for t in texts if t.startswith('SNAPREP MON WVAL')]), 1 if stored else 0, 'the value of a register is shown for a store the instruction made')
                if stored:
                    srt, bits = esr >> 16 & 31, 8 << (esr >> 22 & 3)
                    whole = 0 if srt == 31 else spec.regs[srt]
                    self.assertIn('SNAPREP MON WVAL %s=0X%X DATA=0X%X' % ('XZR' if srt == 31 else f'X{srt}', whole, whole & ((1 << bits) - 1)), texts)


class Malformed(Base):
    """A REPORT.DAT the reporter cannot trust is refused by the checks it already has; none of the page's window lines is printed from it."""

    def refused(self, name, data, token):
        r = run_reporters(FIX['exe'], {name: data})[name]
        texts = r.texts()
        self.assertEqual(held(r), [], name)
        self.assertTrue(any(t.startswith(f'SNAPREP REFUSED={token} ') or t.startswith(f'SNAPREP {token}') for t in texts), f'{name}: {token} in {texts}')
        self.assertFalse([t for t in texts if t.startswith(WINDOW_PREFIXES)], f'{name}: no line of the window')

    def test_a_bad_magic_is_refused_with_the_head_words(self):
        data = bytearray(report_with(P.build(spec_boot20())))
        data[0] ^= 0x20
        self.refused('magic', bytes(data), 'REPORT_MAGIC')

    def test_a_short_file_is_refused_by_the_slot_check(self):
        good = report_with(P.build(spec_boot20()))
        self.refused('short', good[:0x9000], 'FILE_SIZE')

    def test_a_file_cut_in_the_register_block_is_refused_by_the_slot_check(self):
        good = report_with(P.build(spec_boot20()))
        self.refused('cut-in-the-registers', good[:0x1000 + P.ASYNC + 8 * 20], 'FILE_SIZE')

    def test_a_head_that_fails_its_value_checks_is_refused(self):
        data = bytearray(report_with(P.build(spec_boot20())))
        data[0x1F0] = 1                                 # a byte of the head page after the head: REPORT_RESERVED
        self.refused('reserved', bytes(data), 'REPORT_RESERVED')

    def test_a_damaged_or_missing_record_leaves_the_window_unread(self):
        raw = bytearray(RUN['rec'])
        raw[0] ^= 1
        self.refused('damaged', report_with(P.build(spec_boot20()), bytes(raw)), 'MON RECORD DAMAGED CODE=1')
        self.refused('absent', report_with(P.build(spec_boot20()), bytes(M.REC_BYTES)), 'MON NO RECORD')

    def test_a_monitor_that_returned_a_quarantine_code_leaves_the_window_unread(self):
        raw = bytearray(RUN['rec'])
        raw[0xB0:0xB8] = (0x51554E02).to_bytes(8, 'little')
        raw[0x10:0x14] = (M.OUT.QUARANTINE).to_bytes(4, 'little')
        self.refused('quarantine', report_with(P.build(spec_boot20()), bytes(raw)), 'MON QUARANTINE')


if __name__ == '__main__':
    unittest.main()
