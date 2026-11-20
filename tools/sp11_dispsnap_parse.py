#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Reads the page of the q1n1 payload `dispsnap` and gives the display study's verdict on it: ACTIVE, INACTIVE, UNKNOWN or INCONSISTENT.

    python3 tools/sp11_dispsnap_parse.py PAGE.txt [--gop WxH] [--json]      exit 0 ACTIVE, 1 INACTIVE, 2 UNKNOWN, 3 INCONSISTENT, 4 the page is unusable

Status: EXPERIMENTAL, host only. No hardware has produced a page: every statement about what a register holds on a Surface Pro 11 is for the run to say (hardware NOT
RUN). The payload (the q1n1 fork, payloads/dispsnap.c, docs/SP11-PAYLOAD-DISPSNAP.md there) reads the registers of the study's X2 dump list (docs/SP11-DISPLAY-STUDY.md
section 8, the block between its BEGIN and END X2-DISPLAY-LIST markers) and prints them as lines; this tool turns the lines back into the snapshot of
compat/display-pipeline-check.h and runs that C decision function (built here with clang) on it. It reads a transcript of the panel or of the UART: any line without the
payload's prefix (`PAYLOAD DISPSNAP `) is ignored.

The page, as this tool reads it (every value is the raw 32-bit word: a number below 10 is one decimal digit, a larger one `0X` and upper-case hex digits; a clock or GDSC
word may carry `.ON`, `.OFF` or `.ODD` after it, which are decodes this tool ignores):
  `READ ONLY EL=2 HZ=<hex> GAP=8 S`       the counter frequency
  `GOP W=<n> H=<n> STRIDE=<n> FMT=<n>`    the mode the runner drew the page in: the width and height the interface's active window is compared with (an explicit --gop
                                          WxH takes its place; a page without a GOP line, or with a zero one, needs it)
  `MAP GCC=<hex> DISPCC=<hex> MDSS=<hex> DPU=<hex>` and `MAP INTF=<n> AT=<hex> CTL=<hex> STEP=<hex> REG=<hex>`
                                          the bases and the geometry the payload's loads used: the address of every register is computed from them and the study's
                                          offsets, so a payload that read somewhere else than the parameters say makes the check answer INCONSISTENT (address or interface)
  `GCC DAHB=..`, `DISPCC GDSC=.. AHBC=.. MDPC=..`, `MDSS VER=..`, `INTF ST= EN= CFG= CFG2= HS= VP= DVS= DVE= AVS= AVE= DHC= AHC= FLC= FRA=`, `CTL C0= .. C5=`
                                          the words of snapshot 1, under the study's registers GCC_DISP_AHB, GDSC, AHB_CBCR, MDP_CBCR, HW_VERSION, INTF_STATUS,
                                          INTF_EN, INTF_CONFIG, INTF_CONFIG2, INTF_HSYNC_CTL, INTF_VSYNC_PERIOD, INTF_DISP_V_START, INTF_DISP_V_END, INTF_ACT_V_START,
                                          INTF_ACT_V_END, INTF_DISP_HCTL, INTF_ACT_HCTL, INTF_FLC_EN, INTF_FRAME_A and the six CTL_INTF_ACTIVE words
  `S2 INTF ST=.. EN=.. FRB=.. DT=<hex>`   the second reading of the frame counter (INTF_FRAME_B) and the counter ticks between the two readings, from which the snapshot's
                                          gap in microseconds is DT * 10^6 / HZ
A value `FAULT:<n>` is a register whose guarded load aborted (status FAULT in the snapshot); `SKIP`, `NA`, `BUDGET`, `NOFLC` and every register of a block that is
`GATED BY` something, or absent from the page, are not attempted (NOT_TRIED). Nothing is guessed: a missing word is missing evidence, and the check never says ACTIVE
without all of it. The parameters (bases, offsets, the DPU version, the required evidence) are the study's tables, read from docs/SP11-DISPLAY-STUDY.md, never constants
of this file.

What the verdict does not say: it is about the registers the firmware left, read at one moment; ACTIVE means the study's evidence classes were established (gates,
version, timing, geometry, routing; the frame counters are corroboration: without an enable that says they count they add nothing, but an enable that does says the
counter runs, and then both readings (`FRA` and the `S2` line's `FRB`) are needed, so a page without snapshot 2 is UNKNOWN (not read) and not ACTIVE), not that the panel
is lit, not that the firmware's scanout is the GOP framebuffer, and nothing about
the backlight."""
import argparse
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / 'docs' / 'SP11-DISPLAY-STUDY.md'
CHECK_C = ROOT / 'compat' / 'display-pipeline-check.c'
CHECK_H = ROOT / 'compat' / 'display-pipeline-check.h'
HARNESS_C = ROOT / 'tools' / 'test-display-pipeline-check.c'
BUILD = ROOT / 'build' / 'sp11-dispsnap-parse'
PREFIX = 'PAYLOAD DISPSNAP '
TOOLS = ('/opt/homebrew/opt/llvm/bin',)

# page key -> the study's register id (enum dpc_reg without the prefix), in the reader's order
KEYS = {'DAHB': 'GCC_DISP_AHB', 'GDSC': 'GDSC', 'AHBC': 'AHB_CBCR', 'MDPC': 'MDP_CBCR', 'VER': 'HW_VERSION', 'EN': 'INTF_EN', 'ST': 'INTF_STATUS', 'CFG': 'INTF_CONFIG',
        'HS': 'INTF_HSYNC_CTL', 'VP': 'INTF_VSYNC_PERIOD', 'DVS': 'INTF_DISP_V_START', 'DVE': 'INTF_DISP_V_END', 'AVS': 'INTF_ACT_V_START', 'AVE': 'INTF_ACT_V_END',
        'DHC': 'INTF_DISP_HCTL', 'AHC': 'INTF_ACT_HCTL', 'CFG2': 'INTF_CONFIG2', 'FLC': 'INTF_FLC_EN', 'FRA': 'INTF_FRAME_A', 'FRB': 'INTF_FRAME_B'}
CTL_KEYS = tuple(f'C{i}' for i in range(6))
HEADS = {'GCC': {'DAHB'}, 'DISPCC': {'GDSC', 'AHBC', 'MDPC'}, 'MDSS': {'VER'},
         'INTF': {'ST', 'EN', 'CFG', 'CFG2', 'HS', 'VP', 'DVS', 'DVE', 'AVS', 'AVE', 'DHC', 'AHC', 'FLC', 'FRA'}, 'CTL': set(CTL_KEYS)}
DECODE_ONLY = {'SC', 'HW', 'MAJ', 'MIN', 'PWR', 'CLK', 'DISP'}
NUMBER = re.compile(r'^(0X[0-9A-F]+|\d+)(?:\.(?:ON|OFF|ODD))?$')
NOT_TRIED = ('SKIP', 'NA', 'BUDGET', 'NOFLC')
EXIT = {'ACTIVE': 0, 'INACTIVE': 1, 'UNKNOWN': 2, 'INCONSISTENT': 3}


class PageError(ValueError):
    """The page is not one this tool can read: a malformed or repeated field. It is never turned into a verdict."""


def number(text):
    found = NUMBER.match(text)
    if not found:
        raise PageError(f'{text!r} is not a value the payload prints')
    value = int(found.group(1), 16 if found.group(1).startswith('0X') else 10)
    if value > 0xffffffff:
        raise PageError(f'{text!r} does not fit 32 bits')
    return value


def parse_page(text):
    """The fields of a transcript of the page: {'hz', 'gop', 'map', 'rows': {page key: ('ok', value) | ('fault', code) | ('not_tried', why)}, 'frb', 'dt', 'gated', 'end', 'lines'}.
    Raises PageError on a field that is not well formed, a key twice in snapshot 1, or a key under another line's head."""
    page = {'hz': None, 'hz_default': False, 'gop': None, 'map': {}, 'rows': {}, 'frb': None, 'dt': None, 'gated': {}, 'end': None, 'lines': 0, 's2_skipped': None}
    for raw in text.splitlines():
        at = raw.find(PREFIX)
        if at < 0:
            continue
        line = raw[at + len(PREFIX):].strip()
        tokens = line.split()
        if not tokens:
            continue
        page['lines'] += 1
        head = tokens[0]
        fields = {}
        for token in tokens[1:]:
            if '=' in token:
                key, _, value = token.partition('=')
                fields.setdefault(key, []).append(value)
        if head == 'READ':
            found = re.search(r'\bHZ=(0X[0-9A-F]+|\d+)', line)
            if not found:
                raise PageError('the header line has no HZ')
            page['hz'] = number(found.group(1))
            page['hz_default'] = 'DEFAULT' in tokens
        elif head == 'GOP':
            page['gop'] = tuple(number(fields[k][0]) for k in ('W', 'H', 'STRIDE', 'FMT'))
        elif head == 'MAP':
            for key, values in fields.items():
                if key in page['map']:
                    raise PageError(f'MAP {key} twice')
                page['map'][key] = number(values[0])
        elif head == 'S2' and len(tokens) > 1 and tokens[1] == 'INTF':
            for key, values in fields.items():
                if key == 'FRB':
                    page['frb'] = _row(values[0])
                elif key == 'DT':
                    page['dt'] = number(values[0])
        elif head == 'S2' and len(tokens) > 1 and tokens[1] == 'SKIPPED':
            page['s2_skipped'] = ' '.join(tokens[2:])
        elif head in HEADS:
            if len(tokens) > 1 and tokens[1] == 'GATED':
                page['gated'][head] = ' '.join(tokens[3:]) if tokens[2:3] == ['BY'] else ''
                continue
            for key, values in fields.items():
                if key in DECODE_ONLY:
                    continue
                if key not in HEADS[head]:
                    raise PageError(f'{key} is not a word of the {head} lines')
                if key in page['rows']:
                    raise PageError(f'{key} twice in snapshot 1')
                page['rows'][key] = _row(values[0])
        elif head == 'END':
            page['end'] = {k: v[0] for k, v in fields.items()}
    return page


def _row(text):
    if text.startswith('FAULT:'):
        return ('fault', int(text[6:]))
    if text in NOT_TRIED:
        return ('not_tried', text)
    return ('ok', number(text))


# ---- The study's tables: the parameters of the check ------------------------------------------------------------------------------------------------
def study_tables(path=STUDY):
    """(registers {id: (block, base, offset)}, ctl [(base, offset)], parameters {name: int or str}) from the X2-DISPLAY-LIST block of the study."""
    text = Path(path).read_text(encoding='utf-8')
    block = re.search(r'<!-- BEGIN X2-DISPLAY-LIST.*?-->(.*?)<!-- END X2-DISPLAY-LIST', text, re.S).group(1)
    registers, ctl, params = {}, [], {}
    for line in block.splitlines():
        cells = [c.strip() for c in line.strip().strip('|').split('|')] if line.startswith('|') else []
        if len(cells) >= 5 and cells[0].isdigit() and cells[1].isupper() and cells[3].startswith('0x'):
            registers[cells[1]] = (cells[2], int(cells[3], 16), int(cells[4], 16))
        elif len(cells) == 3 and cells[0].isdigit() and cells[1].startswith('0x') and cells[2].startswith('0x'):
            ctl.append((int(cells[1], 16), int(cells[2], 16)))
        elif len(cells) >= 2 and re.fullmatch(r'[a-z_.]+', cells[0]) and re.fullmatch(r'0x[0-9a-fA-F]+|\d+', cells[1]):
            params[cells[0]] = int(cells[1], 0)
    return registers, ctl, params


def study_map(registers, ctl, params):
    """What the MAP lines of a payload that follows the study must print."""
    return {'GCC': params['gcc.base'], 'DISPCC': params['dispcc.base'], 'MDSS': params['mdss.base'], 'DPU': params['mdp.base'], 'INTF': params['intf_index'],
            'AT': params['intf.base'] - params['mdp.base'], 'CTL': ctl[0][0] - params['mdp.base'], 'STEP': ctl[1][0] - ctl[0][0], 'REG': params['ctl_intf_active_off']}


def build_inputs(page, registers, ctl, params, layout, gop=None):
    """(params bytes, snapshot bytes) for the C check from a parsed page. The address of a register is the page's own base (its MAP line) plus the study's offset; a register the
    page does not show is NOT_TRIED. `gop` is (w, h) or None for the page's own GOP line."""
    given = page['map']
    needed = ('GCC', 'DISPCC', 'MDSS', 'DPU', 'INTF', 'AT', 'CTL', 'STEP', 'REG')
    if any(k not in given for k in needed):
        raise PageError('the page has no complete MAP lines: it cannot say where it read')
    if gop:
        size = gop
    elif page['gop']:
        size = (page['gop'][0], page['gop'][1])
    else:
        size = None
    if not size or not size[0] or not size[1]:
        raise PageError('the page has no GOP mode (or a zero one): give --gop WxH')
    p = dict(params)
    p.update(gop_width=size[0], gop_height=size[1])
    pbytes = bytearray(layout['sizeof.dpc_params'])
    at = lambda name: layout['offsetof.dpc_params.' + name]
    for name, value in (('abi', 1), ('required', p['required']), ('intf_index', p['intf_index']), ('dpu_major', p['dpu_major']), ('dpu_minor', p['dpu_minor']),
                        ('gop_width', p['gop_width']), ('gop_height', p['gop_height']), ('ctl_count', p['ctl_count']), ('ctl_length', p['ctl_length']),
                        ('ctl_intf_active_off', p['ctl_intf_active_off']), ('refresh_hz_min', p['refresh_hz_min']), ('refresh_hz_max', p['refresh_hz_max']),
                        ('counter_gap_min_us', p['counter_gap_min_us']), ('reserved', 0)):
        struct.pack_into('<I', pbytes, at(name), value)
    for blk in ('gcc', 'dispcc', 'mdss', 'mdp', 'intf'):
        struct.pack_into('<QQ', pbytes, at(blk), p[f'{blk}.base'], p[f'{blk}.length'])
    for i, (base, _) in enumerate(ctl):
        struct.pack_into('<Q', pbytes, at('ctl_base') + 8 * i, base)
    order = reg_order()
    for i, name in enumerate(order):
        struct.pack_into('<I', pbytes, at('reg_off') + 4 * i, registers[name][2])
    # the snapshot, in the wire format of compat/display-pipeline-check.h
    entries = []
    mdp, intf_page = given['DPU'], given['DPU'] + given['AT']
    page_key = {v: k for k, v in KEYS.items()}
    gap_us = page['dt'] * 1000000 // page['hz'] if page['dt'] is not None and page['hz'] else 0
    for name in order:
        key = page_key[name]
        state = (page['frb'] or ('not_tried', 'absent')) if name == 'INTF_FRAME_B' else page['rows'].get(key, ('not_tried', 'absent'))
        base = {'gcc': given['GCC'], 'dispcc': given['DISPCC'], 'mdss': given['MDSS'], 'intf': intf_page}[registers[name][0]]
        entries.append(_entry(layout, base + registers[name][2], state))
    ctl_entries = []
    for i in range(params['ctl_count']):
        state = page['rows'].get(f'C{i}', ('not_tried', 'absent'))
        ctl_entries.append(_entry(layout, mdp + given['CTL'] + given['STEP'] * i + given['REG'], state))
    head = struct.pack('<IHHIIIIII', layout['DPC_SNAP_MAGIC'], layout['DPC_SNAP_ABI_MAJOR'], 0, layout['DPC_SNAP_HEADER'] + layout['DPC_SNAP_ENTRY'] * (len(entries) + len(ctl_entries)),
                       given['INTF'], len(entries), len(ctl_entries), gap_us, 0)
    return bytes(pbytes), head + b''.join(entries + ctl_entries)


def _entry(layout, pa, state):
    kind, value = state
    if kind == 'ok':
        return struct.pack('<QII', pa, value, layout['DPC_ST_OK'])
    if kind == 'fault':
        return struct.pack('<QII', pa, 0, layout['DPC_ST_FAULT'])
    return struct.pack('<QII', 0, 0, layout['DPC_ST_NOT_TRIED'])


def reg_order():
    """The registers of enum dpc_reg, in order, from the header."""
    body = re.search(r'enum dpc_reg\s*\{(.*?)\};', CHECK_H.read_text(), re.S).group(1)
    names = re.findall(r'\bDPC_REG_([A-Z0-9_]+)\b', re.sub(r'/\*.*?\*/', '', body, flags=re.S))
    return [n for n in names if n != 'COUNT']


# ---- The C check ----------------------------------------------------------------------------------------------------------------------------------
def find_tool(name):
    return shutil.which(name) or next((str(Path(d) / name) for d in TOOLS if (Path(d) / name).exists()), None)


def build_check(tag='main', extra=()):
    """(executable, layout dict, names) of the check's harness built with clang under build/; None when there is no clang."""
    clang = find_tool('clang')
    if not clang:
        return None
    work = BUILD / tag
    work.mkdir(parents=True, exist_ok=True)
    exe = work / 'harness'
    done = subprocess.run([clang, '-std=c11', '-O1', '-g', '-Wall', '-Wextra', '-Werror', *extra, str(HARNESS_C), str(CHECK_C), '-o', str(exe)], capture_output=True, text=True,
                          timeout=300, cwd=ROOT)
    if done.returncode:
        raise RuntimeError('the check did not build:\n' + done.stderr[-2000:])
    layout = {k: int(v) for k, v in (line.split() for line in subprocess.run([str(exe), 'layout'], capture_output=True, text=True, check=True).stdout.splitlines())}
    names = {'verdict': {}, 'why': {}}
    for line in subprocess.run([str(exe), 'names'], capture_output=True, text=True, check=True).stdout.splitlines():
        kind, number_, name = line.split()
        names[kind][int(number_)] = name
    return exe, layout, names


def run_check(exe, pbytes, snapshot):
    record = struct.pack('<III', len(pbytes), len(snapshot), 0) + pbytes + snapshot
    done = subprocess.run([str(exe), 'batch'], input=record, capture_output=True, timeout=120)
    if done.returncode or not done.stdout.strip():
        raise RuntimeError(f'the check harness failed: {done.stderr.decode(errors="replace")[:500]}')
    return tuple(int(x) for x in done.stdout.decode().split())


def judge(text, gop=None, tag='main'):
    """The verdict on a transcript: {'verdict', 'why', 'detail', 'evidence', 'map_differences', 'gap_us', 'complete'}. Raises PageError for an unusable page."""
    built = build_check(tag)
    if built is None:
        raise RuntimeError('clang not found')
    exe, layout, names = built
    page = parse_page(text)
    registers, ctl, params = study_tables()
    pbytes, snapshot = build_inputs(page, registers, ctl, params, layout, gop)
    verdict, why, detail, evidence = run_check(exe, pbytes, snapshot)
    wanted = study_map(registers, ctl, params)
    return {'verdict': names['verdict'][verdict].upper(), 'why': names['why'][why], 'detail': detail, 'evidence': evidence,
            'map_differences': {k: (page['map'].get(k), v) for k, v in wanted.items() if page['map'].get(k) != v},
            'gap_us': page['dt'] * 1000000 // page['hz'] if page['dt'] is not None and page['hz'] else 0, 'complete': page['end'] is not None,
            'gated': page['gated'], 's2_skipped': page['s2_skipped']}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('page', help='a transcript of the payload\'s page (lines with the prefix PAYLOAD DISPSNAP)')
    ap.add_argument('--gop', help='the GOP mode WxH, when the page has none or to override it')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args(argv)
    gop = None
    if args.gop:
        found = re.fullmatch(r'(\d+)x(\d+)', args.gop)
        if not found:
            print('--gop wants WxH', file=sys.stderr)
            return 4
        gop = (int(found.group(1)), int(found.group(2)))
    try:
        result = judge(Path(args.page).read_text(encoding='utf-8', errors='replace'), gop)
    except PageError as err:
        print(f'UNUSABLE PAGE: {err}', file=sys.stderr)
        return 4
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"{result['verdict']} {result['why']} detail={result['detail']} evidence={result['evidence']:#x} gap_us={result['gap_us']} complete={result['complete']}")
        for key, (got, want) in result['map_differences'].items():
            print(f'MAP {key}: the page says {got}, the study says {want}')
    return EXIT[result['verdict']]


if __name__ == '__main__':
    sys.exit(main())
