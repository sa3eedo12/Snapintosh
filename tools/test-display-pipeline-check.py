#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests for compat/display-pipeline-check.{h,c}: the pure "is the firmware's display pipeline active?" decision.

Nothing here touches hardware or reads any private input. The C logic is built for the host with AddressSanitizer and
UndefinedBehaviorSanitizer (tools/test-display-pipeline-check.c) and driven with synthetic register snapshots: good
ones, every faulty class the brief names (all-ones from an unclocked block, enable bit with no timing programmed,
mismatched interface selection, truncation) and many more. The Hamoa parameters are the INFERRED ones from the pinned
public Linux sources (docs/SP11-DISPLAY-STUDY.md); the register VALUES are invented here and say nothing about any
unit. A second, deliberately different parameter set proves that no base or offset is hard-coded.

The mutation check is in tools/test-display-pipeline-check-mutants.py (a separate file only because this one is near the
50 KiB limit): it seeds bugs into copies of the C source under build/, rebuilds the harness and requires the scenario
table of this file to catch every one. Without clang the native classes report SKIP.

    python3 tools/test-display-pipeline-check.py
"""
import random
import re
import shutil
import struct
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / 'build' / 'test-display-pipeline-check'
HEADER = ROOT / 'compat' / 'display-pipeline-check.h'
SOURCE = ROOT / 'compat' / 'display-pipeline-check.c'
HARNESS = ROOT / 'tools' / 'test-display-pipeline-check.c'
TOOLS = ('/opt/homebrew/opt/llvm/bin',)
HOME_PREFIXES = ('/' + 'Users' + '/', '/' + 'home' + '/')
SANITIZE = ['-fsanitize=address,undefined', '-fno-sanitize-recover=undefined']
NULL_PARAMS, NULL_SNAP, NULL_OUT = 0xffffffff, 0xfffffffe, 0xfffffffd
EV = dict(GATES=1, VERSION=2, TIMING=4, GEOMETRY=8, ROUTING=16, COUNTERS=32)
G, GV = EV['GATES'], EV['GATES'] | EV['VERSION']
GVT, GVTG = GV | EV['TIMING'], GV | EV['TIMING'] | EV['GEOMETRY']
GVTGR = GVTG | EV['ROUTING']
ALL = GVTGR | EV['COUNTERS']


def find_tool(name):
    return shutil.which(name) or next((str(Path(d) / name) for d in TOOLS if (Path(d) / name).exists()), None)


def build(tag, source_text=None, header_text=None, werror=True):
    """Build the harness from the real sources, or from mutated copies, under build/. SkipTest without clang.
    A mutant is built without -Werror: it must be caught by behaviour, not by a new compiler warning."""
    clang = find_tool('clang')
    if not clang:
        raise unittest.SkipTest('clang not found')
    work = BUILD / tag
    (work / 'compat').mkdir(parents=True, exist_ok=True)
    (work / 'tools').mkdir(parents=True, exist_ok=True)
    (work / 'compat' / 'display-pipeline-check.c').write_text(SOURCE.read_text() if source_text is None else source_text)
    (work / 'compat' / 'display-pipeline-check.h').write_text(HEADER.read_text() if header_text is None else header_text)
    (work / 'tools' / 'test-display-pipeline-check.c').write_text(HARNESS.read_text())
    exe = work / 'harness'
    built = subprocess.run([clang, '-std=c11', '-O1', '-g', '-Wall', '-Wextra', *(['-Werror'] if werror else []), *SANITIZE,
                            str(work / 'tools' / 'test-display-pipeline-check.c'),
                            str(work / 'compat' / 'display-pipeline-check.c'), '-o', str(exe)],
                           capture_output=True, text=True, timeout=300)
    if built.returncode:
        raise AssertionError(f'host build of the display-pipeline-check harness failed:\n{built.stderr[-3000:]}')
    return exe


_MAIN = {}


def native():
    """(exe, layout) of the real build, built once per run."""
    if 'exe' not in _MAIN:
        exe = build('main')
        out = subprocess.run([str(exe), 'layout'], capture_output=True, text=True, timeout=60, check=True).stdout
        _MAIN.update(exe=exe, layout={k: int(v) for k, v in (line.split() for line in out.splitlines())})
    return _MAIN['exe'], _MAIN['layout']


def header_enum(name):
    """Enumerator names of `enum <name> {...}` in the header, in order."""
    body = re.search(r'enum %s\s*\{(.*?)\};' % name, HEADER.read_text(), re.S).group(1)
    return re.findall(r'\b(DPC_[A-Z0-9_]+)\b', re.sub(r'/\*.*?\*/', '', body, flags=re.S))


def reg_names():
    return [n[len('DPC_REG_'):] for n in header_enum('dpc_reg') if n != 'DPC_REG_COUNT']


def block_of(name):
    if name == 'GCC_DISP_AHB':
        return 'gcc'
    return 'dispcc' if name in ('GDSC', 'AHB_CBCR', 'MDP_CBCR') else 'mdss' if name == 'HW_VERSION' else 'intf'


# ---- Parameters -------------------------------------------------------------------------------------------------------
# Hamoa: INFERRED from the pinned public Linux sources (docs/SP11-DISPLAY-STUDY.md, "Pinned evidence").
HAMOA_REG_OFF = dict(GCC_DISP_AHB=0x27004, GDSC=0x9000, AHB_CBCR=0x80a8, MDP_CBCR=0x800c, HW_VERSION=0x0, INTF_EN=0x000,
                     INTF_STATUS=0x26c, INTF_CONFIG=0x004, INTF_HSYNC_CTL=0x008, INTF_VSYNC_PERIOD=0x00c,
                     INTF_DISP_V_START=0x01c, INTF_DISP_V_END=0x024, INTF_ACT_V_START=0x02c, INTF_ACT_V_END=0x034,
                     INTF_DISP_HCTL=0x03c, INTF_ACT_HCTL=0x040, INTF_CONFIG2=0x060, INTF_FLC_EN=0x0a8, INTF_FRAME_A=0x0ac,
                     INTF_FRAME_B=0x0ac)


def hamoa_params():
    mdp = 0x0ae01000
    return dict(abi=1, required=GVTGR, intf_index=5, dpu_major=9, dpu_minor=2, gop_width=2880, gop_height=1920,
                ctl_count=6, ctl_length=0x290, ctl_intf_active_off=0xf4, refresh_hz_min=24, refresh_hz_max=240,
                counter_gap_min_us=100000, reserved=0, gcc=(0x00100000, 0x200000), dispcc=(0x0af00000, 0x20000),
                mdss=(0x0ae00000, 0x1000), mdp=(mdp, 0x8f000), intf=(mdp + 0x39000, 0x280),
                ctl_base=[mdp + 0x15000 + 0x1000 * i for i in range(6)], reg_off=dict(HAMOA_REG_OFF))


def alt_params():
    """A made-up platform sharing no base, offset, INTF index, CTL page or DPU version with Hamoa."""
    p, mdp = hamoa_params(), 0x40200000
    p.update(intf_index=2, dpu_major=12, dpu_minor=4, ctl_count=3, ctl_length=0x400, ctl_intf_active_off=0x2f8,
             gcc=(0x3f000000, 0x300000), dispcc=(0x40000000, 0x40000), mdss=(0x40100000, 0x2000), mdp=(mdp, 0x100000),
             intf=(mdp + 0x7000, 0x1000), ctl_base=[mdp + 0x20000 + 0x1000 * i for i in range(3)])
    p['reg_off'] = {n: o + 0x40 + 4 * i for i, (n, o) in enumerate(HAMOA_REG_OFF.items())}
    p['reg_off'].update(INTF_FRAME_B=p['reg_off']['INTF_FRAME_A'], HW_VERSION=0x1c)
    return p


def pack_params(p, layout):
    buf = bytearray(layout['sizeof.dpc_params'])
    at = lambda name: layout['offsetof.dpc_params.' + name]
    for name in ('abi', 'required', 'intf_index', 'dpu_major', 'dpu_minor', 'gop_width', 'gop_height', 'ctl_count',
                 'ctl_length', 'ctl_intf_active_off', 'refresh_hz_min', 'refresh_hz_max', 'counter_gap_min_us',
                 'reserved'):
        struct.pack_into('<I', buf, at(name), p[name])
    for blk in ('gcc', 'dispcc', 'mdss', 'mdp', 'intf'):
        struct.pack_into('<QQ', buf, at(blk), *p[blk])
    for i, base in enumerate(p['ctl_base']):
        struct.pack_into('<Q', buf, at('ctl_base') + 8 * i, base)
    for i, name in enumerate(reg_names()):
        struct.pack_into('<I', buf, at('reg_off') + 4 * i, p['reg_off'][name])
    return bytes(buf)


# ---- Snapshots --------------------------------------------------------------------------------------------------------
def intf_values(width, height, widebus, hs=32, hbp=48, hfp=80, vs=5, vbp=30, vfp=35):
    """INTF register values as Linux programs a DP interface (dpu_hw_intf.c:128-257 with the porch shift and the
    halving of dpu_encoder_phys_vid.c:106-123), written out again here so the C decoder is checked against an
    independent computation and not against itself. Returns the values and (htotal, vtotal)."""
    if widebus:
        hbp, hfp, vbp, vfp = hbp + hfp, 0, vbp + vfp, 0
        hs, hbp, hfp, width = hs >> 1, hbp >> 1, hfp >> 1, width >> 1
    ht, vt = hs + hbp + width + hfp, vs + vbp + height + vfp
    v0 = (vs + vbp) * ht + hs + hbp
    hctl = (hs + hbp + width - 1) << 16 | (hs + hbp)
    return dict(INTF_HSYNC_CTL=ht << 16 | hs, INTF_VSYNC_PERIOD=ht * vt, INTF_DISP_V_START=v0,
                INTF_DISP_V_END=vt * ht - 1 - hfp - vfp * ht, INTF_ACT_V_START=v0, INTF_ACT_V_END=v0 + height * ht - 1,
                INTF_DISP_HCTL=hctl, INTF_ACT_HCTL=hctl, INTF_CONFIG=0x60000000,
                INTF_CONFIG2=0x11 if widebus else 0x10), ht, vt


class Snap:
    def __init__(self, p, layout):
        self.p, self.layout, self.names = p, layout, reg_names()
        self.regs = {n: [self.pa(n), 0, 0] for n in self.names}
        self.ctls = [[p['ctl_base'][i] + p['ctl_intf_active_off'], 0, layout['DPC_ST_OK']] for i in range(p['ctl_count'])]
        self.header = dict(magic=layout['DPC_SNAP_MAGIC'], major=layout['DPC_SNAP_ABI_MAJOR'], minor=0, total=None,
                           intf=p['intf_index'], regs=None, ctls=None, gap=150000, reserved=0)

    def pa(self, name):
        return self.p[block_of(name)][0] + self.p['reg_off'][name]

    def set(self, name, value, status=None, pa=None):
        self.regs[name] = [self.pa(name) if pa is None else pa, value,
                           self.layout['DPC_ST_OK'] if status is None else status]

    def clear(self, name):
        self.regs[name] = [0, 0, self.layout['DPC_ST_NOT_TRIED']]

    def clear_ctl(self, i):
        self.ctls[i] = [0, 0, self.layout['DPC_ST_NOT_TRIED']]

    def pack(self):
        h, L = self.header, self.layout
        regs = [self.regs[n] for n in self.names][:h['regs']]
        ctls = self.ctls[:h['ctls']]
        total = L['DPC_SNAP_HEADER'] + L['DPC_SNAP_ENTRY'] * (len(regs) + len(ctls)) if h['total'] is None else h['total']
        out = bytearray(struct.pack('<IHHIIIIII', h['magic'], h['major'], h['minor'], total, h['intf'],
                                    len(regs) if h['regs'] is None else h['regs'],
                                    len(ctls) if h['ctls'] is None else h['ctls'], h['gap'], h['reserved']))
        for pa, value, status in regs + ctls:
            out += struct.pack('<QII', pa, value, status)
        return bytes(out)


def good_snapshot(p, layout, widebus=False):
    s = Snap(p, layout)
    s.set('GCC_DISP_AHB', 0x1)
    s.set('GDSC', 0x80000000)
    s.set('AHB_CBCR', 0x1)
    s.set('MDP_CBCR', 0x1)
    s.set('HW_VERSION', p['dpu_major'] << 28 | p['dpu_minor'] << 16)
    s.set('INTF_EN', 1)
    s.set('INTF_STATUS', 1)
    for name, value in intf_values(p['gop_width'], p['gop_height'], widebus)[0].items():
        s.set(name, value)
    s.set('INTF_FLC_EN', 3)
    s.set('INTF_FRAME_A', 1000)
    s.set('INTF_FRAME_B', 1018)
    s.ctls[0][1] = 1 << p['intf_index']
    return s


# ---- Scenarios (symbolic expectations: verdict, why, detail, evidence; None = not asserted) -----------------------------
def case_builder(layout):
    """[(name, thunk -> (params dict, snapshot bytes, mode), expectation)]."""
    NT, OK, FAULT = (layout['DPC_ST_' + k] for k in ('NOT_TRIED', 'OK', 'FAULT'))
    cases = []

    def case(name, expect, params=None, snap=None, post=None, mode=0, alt=False, widebus=False):
        def thunk():
            base = alt_params() if alt else hamoa_params()
            s = good_snapshot(base, layout, widebus)     # the snapshot is of the unmutated platform
            if snap:
                snap(s)
            p = alt_params() if alt else hamoa_params()
            if params:
                params(p)
            data = s.pack()
            return p, post(data) if post else data, mode
        cases.append((name, thunk, expect))

    def regs(**kw):
        return lambda s: [s.set(n, v) for n, v in kw.items()]

    def ctl(i, value, status=OK):
        return lambda s: s.ctls[i].__setitem__(slice(1, 3), [value, status])

    def seq(*fns):
        return lambda s: [f(s) for f in fns]

    def stop_after(*keep):
        return lambda s: ([s.clear(n) for n in s.names if n not in keep], [s.clear_ctl(i) for i in range(len(s.ctls))])

    def no_ctl_reads(s):
        for i in range(len(s.ctls)):
            s.clear_ctl(i)

    def intf_regs(fn):
        return lambda s: [fn(s, n) for n in s.names if n.startswith('INTF_')]

    # -- good snapshots
    case('good', ('ACTIVE', 'OK', 0, ALL))
    case('good_widebus', ('ACTIVE', 'OK', 0, ALL), widebus=True)
    case('good_other_platform_shares_nothing_with_hamoa', ('ACTIVE', 'OK', 0, ALL), alt=True)
    case('good_counters_not_enabled', ('ACTIVE', 'OK', 0, GVTGR), snap=regs(INTF_FLC_EN=0))
    case('good_counters_not_read', ('ACTIVE', 'OK', 0, GVTGR),
         snap=lambda s: [s.clear(n) for n in ('INTF_FLC_EN', 'INTF_FRAME_A', 'INTF_FRAME_B')])
    case('good_counter_gap_too_short_proves_nothing', ('ACTIVE', 'OK', 0, GVTGR),
         snap=lambda s: (s.header.update(gap=5000), s.set('INTF_FRAME_B', 1000)))
    case('good_counter_wraps', ('ACTIVE', 'OK', 0, ALL), snap=regs(INTF_FRAME_A=0xfffffffe, INTF_FRAME_B=3))
    case('good_trailing_bytes_are_not_part_of_the_snapshot', ('ACTIVE', 'OK', 0, ALL), post=lambda d: d + b'\xff' * 7)
    case('good_misaligned_snapshot_pointer', ('ACTIVE', 'OK', 0, ALL), mode=3)
    case('good_without_version_when_not_required', ('ACTIVE', 'OK', 0, ALL & ~EV['VERSION']),
         params=lambda p: p.update(required=p['required'] & ~EV['VERSION']), snap=lambda s: s.clear('HW_VERSION'))
    case('good_version_step_is_not_compared', ('ACTIVE', 'OK', 0, ALL), snap=regs(HW_VERSION=0x9002ffff))
    case('good_other_bits_of_enable_and_status_are_ignored', ('ACTIVE', 'OK', 0, ALL),
         snap=regs(INTF_EN=0xaa01, INTF_STATUS=0x5551))
    case('good_routing_found_in_the_last_ctl_only', ('ACTIVE', 'OK', 0, ALL), snap=seq(ctl(0, 0), ctl(5, 1 << 5)))
    case('good_routing_found_despite_another_ctl_faulting', ('ACTIVE', 'OK', 0, ALL), snap=ctl(3, 0, FAULT))
    case('good_without_routing_when_not_required', ('ACTIVE', 'OK', 0, ALL & ~EV['ROUTING']),
         params=lambda p: p.update(required=p['required'] & ~EV['ROUTING']), snap=no_ctl_reads)
    case('good_active_h_disabled_uses_display_hctl', ('ACTIVE', 'OK', 0, ALL),
         snap=regs(INTF_CONFIG=0x40000000, INTF_ACT_HCTL=0x12340000))
    case('good_active_v_disabled_uses_display_window', ('ACTIVE', 'OK', 0, ALL),
         snap=regs(INTF_CONFIG=0x20000000, INTF_ACT_V_START=7, INTF_ACT_V_END=9))
    case('good_counters_required_and_present', ('ACTIVE', 'OK', 0, ALL),
         params=lambda p: p.update(required=p['required'] | EV['COUNTERS']))

    # -- the brief's faulty snapshots
    case('all_ones_in_intf_status_is_not_active', ('UNKNOWN', 'ALL_ONES', 'INTF_STATUS', GV), snap=regs(INTF_STATUS=0xffffffff))
    case('all_ones_in_intf_enable_is_not_active', ('UNKNOWN', 'ALL_ONES', 'INTF_EN', GV), snap=regs(INTF_EN=0xffffffff))
    case('all_ones_from_an_unclocked_intf_block', ('UNKNOWN', 'ALL_ONES', 'INTF_EN', GV),
         snap=intf_regs(lambda s, n: s.set(n, 0xffffffff)))
    case('all_ones_everywhere_is_unknown_not_active', ('UNKNOWN', 'ALL_ONES', 'GCC_DISP_AHB', 0),
         snap=lambda s: [s.set(n, 0xffffffff) for n in s.names])
    case('enable_set_but_no_timing_programmed', ('INCONSISTENT', 'NO_TIMING', 0, GV),
         snap=regs(INTF_HSYNC_CTL=0, INTF_VSYNC_PERIOD=0))
    case('enable_set_and_every_window_register_zero', ('INCONSISTENT', 'NO_TIMING', 0, GV),
         snap=regs(INTF_DISP_V_START=0, INTF_DISP_V_END=0, INTF_ACT_V_START=0, INTF_ACT_V_END=0, INTF_DISP_HCTL=0,
                   INTF_ACT_HCTL=0))
    case('enable_set_with_a_line_length_but_no_frame_period', ('INCONSISTENT', 'NO_TIMING', 0, GV), snap=regs(INTF_VSYNC_PERIOD=0))
    case('interface_selection_in_the_header_differs', ('INCONSISTENT', 'INTF_MISMATCH', 4, 0), snap=lambda s: s.header.update(intf=4))
    case('registers_read_from_another_interface_page', ('INCONSISTENT', 'ADDRESS_MISMATCH', 'INTF_EN', 0),
         snap=intf_regs(lambda s, n: s.set(n, s.regs[n][1], pa=s.regs[n][0] - 0x1000)))
    case('no_ctl_path_lists_the_interface', ('INCONSISTENT', 'ROUTING_MISMATCH', 5, GVTG), snap=ctl(0, 0))
    case('only_another_interface_is_listed', ('INCONSISTENT', 'ROUTING_MISMATCH', 5, GVTG), snap=ctl(0, 1 << 4))
    case('a_snapshot_cut_off_in_the_ctl_entries', ('UNKNOWN', 'TRUNCATED', 0, 0), post=lambda d: d[:-16])

    # -- gates
    case('power_domain_off', ('INACTIVE', 'POWER_OFF', 'GDSC', 0), snap=seq(regs(GDSC=0), stop_after('GCC_DISP_AHB', 'GDSC')))
    case('power_domain_off_even_if_later_registers_were_read', ('INACTIVE', 'POWER_OFF', 'GDSC', 0), snap=regs(GDSC=0))
    case('ahb_clock_off', ('INACTIVE', 'AHB_CLOCK_OFF', 'AHB_CBCR', 0),
         snap=seq(regs(AHB_CBCR=0x80000001), stop_after('GCC_DISP_AHB', 'GDSC', 'AHB_CBCR')))
    case('mdp_clock_off', ('INACTIVE', 'MDP_CLOCK_OFF', 'MDP_CBCR', 0),
         snap=seq(regs(MDP_CBCR=0x80000000), stop_after('GCC_DISP_AHB', 'GDSC', 'AHB_CBCR', 'MDP_CBCR')))
    case('dispcc_register_clock_off_means_the_gates_are_unreachable', ('UNKNOWN', 'DISPCC_NO_ACCESS', 'GCC_DISP_AHB', 0),
         snap=seq(regs(GCC_DISP_AHB=0x80000000), stop_after('GCC_DISP_AHB')))
    case('dispcc_register_clock_off_even_if_the_gates_were_read_anyway', ('UNKNOWN', 'DISPCC_NO_ACCESS', 'GCC_DISP_AHB', 0),
         snap=regs(GCC_DISP_AHB=0x80000000, GDSC=0))
    case('dispcc_register_clock_unreadable', ('UNKNOWN', 'READ_FAULT', 'GCC_DISP_AHB', 0),
         snap=lambda s: s.set('GCC_DISP_AHB', 1, FAULT))
    case('dispcc_register_clock_not_read', ('UNKNOWN', 'NOT_READ', 'GCC_DISP_AHB', 0), snap=lambda s: s.clear('GCC_DISP_AHB'))
    case('dispcc_register_clock_all_ones', ('UNKNOWN', 'ALL_ONES', 'GCC_DISP_AHB', 0), snap=regs(GCC_DISP_AHB=0xffffffff))
    case('first_gate_that_is_off_decides', ('INACTIVE', 'POWER_OFF', 'GDSC', 0),
         snap=seq(regs(GDSC=0), lambda s: s.set('AHB_CBCR', 0, FAULT)))
    case('power_domain_unreadable', ('UNKNOWN', 'READ_FAULT', 'GDSC', 0), snap=lambda s: s.set('GDSC', 0x80000000, FAULT))
    case('power_domain_not_read', ('UNKNOWN', 'NOT_READ', 'GDSC', 0), snap=lambda s: s.clear('GDSC'))
    case('power_domain_all_ones', ('UNKNOWN', 'ALL_ONES', 'GDSC', 0), snap=regs(GDSC=0xffffffff))
    case('ahb_clock_all_ones', ('UNKNOWN', 'ALL_ONES', 'AHB_CBCR', 0), snap=regs(AHB_CBCR=0xffffffff))
    case('mdp_clock_not_read', ('UNKNOWN', 'NOT_READ', 'MDP_CBCR', 0), snap=lambda s: s.clear('MDP_CBCR'))
    case('a16_clock_offset_used_on_hamoa', ('INCONSISTENT', 'ADDRESS_MISMATCH', 'AHB_CBCR', 0),
         snap=lambda s: s.set('AHB_CBCR', 1, pa=0x0af00000 + 0x80b0))

    # -- version
    case('version_major_differs', ('INCONSISTENT', 'VERSION_MISMATCH', 0x8002, G), snap=regs(HW_VERSION=0x80020000))
    case('version_minor_differs', ('INCONSISTENT', 'VERSION_MISMATCH', 0x9003, G), snap=regs(HW_VERSION=0x90030001))
    case('version_zero_is_a_mismatch', ('INCONSISTENT', 'VERSION_MISMATCH', 0, G), snap=regs(HW_VERSION=0))
    case('version_required_but_not_read', ('UNKNOWN', 'NOT_READ', 'HW_VERSION', G), snap=lambda s: s.clear('HW_VERSION'))
    case('version_all_ones', ('UNKNOWN', 'ALL_ONES', 'HW_VERSION', G), snap=regs(HW_VERSION=0xffffffff))
    case('version_faulted_even_when_not_required', ('UNKNOWN', 'READ_FAULT', 'HW_VERSION', G),
         params=lambda p: p.update(required=p['required'] & ~EV['VERSION']),
         snap=lambda s: s.set('HW_VERSION', 0x90020000, FAULT))

    # -- timing engine
    case('timing_engine_stopped', ('INACTIVE', 'TIMING_OFF', 0, GV), snap=regs(INTF_EN=0, INTF_STATUS=0))
    case('enable_set_status_clear', ('INCONSISTENT', 'ENABLE_STATUS', 2, GV), snap=regs(INTF_STATUS=0))
    case('enable_clear_status_set', ('INCONSISTENT', 'ENABLE_STATUS', 1, GV), snap=regs(INTF_EN=0))
    case('intf_registers_not_read_after_the_gates', ('UNKNOWN', 'NOT_READ', 'INTF_EN', GV),
         snap=intf_regs(lambda s, n: s.clear(n)))
    case('intf_register_faulted', ('UNKNOWN', 'READ_FAULT', 'INTF_HSYNC_CTL', GV),
         snap=lambda s: s.set('INTF_HSYNC_CTL', 0, FAULT))
    case('vsync_period_is_not_a_whole_number_of_lines', ('INCONSISTENT', 'TIMING_INCOHERENT', 'PERIOD', GV),
         snap=lambda s: s.set('INTF_VSYNC_PERIOD', s.regs['INTF_VSYNC_PERIOD'][1] + 1))
    case('vertical_window_ends_before_it_starts', ('INCONSISTENT', 'TIMING_INCOHERENT', 'V_WINDOW', GV),
         snap=regs(INTF_CONFIG=0x20000000, INTF_DISP_V_END=5))
    case('vertical_window_of_a_single_pixel', ('INCONSISTENT', 'TIMING_INCOHERENT', 'V_WINDOW', GV),
         snap=regs(INTF_CONFIG=0x20000000, INTF_DISP_V_END=106480))
    case('active_v_enabled_with_a_reversed_window', ('INCONSISTENT', 'TIMING_INCOHERENT', 'V_WINDOW', GV),
         snap=regs(INTF_ACT_V_START=8495088))
    case('active_h_enabled_with_a_zero_window', ('INCONSISTENT', 'GEOMETRY_MISMATCH', 1, GVT), snap=regs(INTF_ACT_HCTL=0))
    case('window_fills_the_frame_with_no_blanking', ('INCONSISTENT', 'TIMING_INCOHERENT', 'OUTSIDE_FRAME', GV),
         snap=regs(INTF_ACT_V_START=0, INTF_ACT_V_END=1920 * 3040 - 1, INTF_VSYNC_PERIOD=1920 * 3040))
    case('active_window_runs_past_the_frame', ('INCONSISTENT', 'TIMING_INCOHERENT', 'OUTSIDE_FRAME', GV),
         snap=regs(INTF_ACT_V_START=100 * 3040 + 80, INTF_ACT_V_END=100 * 3040 + 80 + 1920 * 3040 - 1))
    case('horizontal_window_ends_before_it_starts', ('INCONSISTENT', 'TIMING_INCOHERENT', 'H_WINDOW', GV),
         snap=regs(INTF_ACT_HCTL=0x00500060))
    case('horizontal_window_past_the_line', ('INCONSISTENT', 'TIMING_INCOHERENT', 'OUTSIDE_FRAME', GV),
         snap=regs(INTF_ACT_HCTL=3040 << 16 | 80))

    # -- geometry
    case('programmed_height_differs_from_the_mode', ('INCONSISTENT', 'GEOMETRY_MISMATCH', 2, GVT),
         params=lambda p: p.update(gop_height=1800))
    case('programmed_width_differs_from_the_mode', ('INCONSISTENT', 'GEOMETRY_MISMATCH', 1, GVT),
         params=lambda p: p.update(gop_width=2560))
    case('both_dimensions_differ', ('INCONSISTENT', 'GEOMETRY_MISMATCH', 3, GVT),
         params=lambda p: p.update(gop_width=1920, gop_height=1280))
    case('widebus_flag_set_with_the_full_width', ('INCONSISTENT', 'GEOMETRY_MISMATCH', 1, GVT), snap=regs(INTF_CONFIG2=0x11))
    case('halved_values_without_the_widebus_flag', ('INCONSISTENT', 'GEOMETRY_MISMATCH', 1, GVT), widebus=True,
         snap=regs(INTF_CONFIG2=0x10))
    case('widebus_with_an_odd_mode_width', ('INCONSISTENT', 'GEOMETRY_MISMATCH', 1, GVT), widebus=True,
         params=lambda p: p.update(gop_width=2881))

    # -- routing
    case('routing_required_but_no_ctl_read', ('UNKNOWN', 'MISSING_EVIDENCE', EV['ROUTING'], GVTG | EV['COUNTERS']),
         snap=no_ctl_reads)
    case('no_ctl_hit_while_one_ctl_was_not_read', ('UNKNOWN', 'MISSING_EVIDENCE', EV['ROUTING'], GVTG | EV['COUNTERS']),
         snap=seq(ctl(0, 0), lambda s: s.clear_ctl(5)))
    case('ctl_faulted_and_routing_required', ('UNKNOWN', 'READ_FAULT', 'CTL0', GVTG), snap=ctl(0, 0, FAULT))
    case('ctl_all_ones_and_routing_required', ('UNKNOWN', 'ALL_ONES', 'CTL1', GVTG), snap=seq(ctl(0, 0), ctl(1, 0xffffffff)))
    case('ctl_count_in_snapshot_differs_from_parameters', ('INCONSISTENT', 'ADDRESS_MISMATCH', 'CTL0', 0),
         snap=lambda s: s.header.update(ctls=5))
    case('ctl_read_at_the_wrong_address', ('INCONSISTENT', 'ADDRESS_MISMATCH', 'CTL2', 0),
         snap=lambda s: s.ctls[2].__setitem__(0, s.ctls[2][0] + 4))

    # -- counters
    case('counters_claimed_but_stalled', ('INCONSISTENT', 'COUNTER_STALLED', 0, GVTGR),
         snap=lambda s: (s.header.update(gap=120000), s.set('INTF_FRAME_B', 1000)))
    case('counters_move_faster_than_any_refresh', ('INCONSISTENT', 'COUNTER_FAST', 10000, GVTGR), snap=regs(INTF_FRAME_B=11000))
    case('counters_unread_after_enable_claimed', ('UNKNOWN', 'NOT_READ', 'INTF_FRAME_A', GVTGR),
         snap=lambda s: s.clear('INTF_FRAME_A'))
    case('counter_enable_faulted', ('UNKNOWN', 'READ_FAULT', 'INTF_FLC_EN', GVTGR), snap=lambda s: s.set('INTF_FLC_EN', 3, FAULT))
    case('counter_sample_all_ones', ('UNKNOWN', 'ALL_ONES', 'INTF_FRAME_B', GVTGR), snap=regs(INTF_FRAME_B=0xffffffff))
    case('counters_required_but_not_enabled', ('UNKNOWN', 'MISSING_EVIDENCE', EV['COUNTERS'], GVTGR),
         params=lambda p: p.update(required=p['required'] | EV['COUNTERS']), snap=regs(INTF_FLC_EN=0))

    # -- snapshot structure
    case('bad_magic', ('UNKNOWN', 'MALFORMED', 0, 0), snap=lambda s: s.header.update(magic=0x12345678))
    case('unknown_abi_major', ('UNKNOWN', 'MALFORMED', 0, 0), snap=lambda s: s.header.update(major=2))
    case('unknown_abi_minor', ('UNKNOWN', 'MALFORMED', 0, 0), snap=lambda s: s.header.update(minor=1))
    case('reserved_header_word_set', ('UNKNOWN', 'MALFORMED', 0, 0), snap=lambda s: s.header.update(reserved=1))
    case('too_many_ctl_entries_claimed', ('UNKNOWN', 'MALFORMED', 0, 0), snap=lambda s: s.header.update(ctls=9))
    case('total_length_disagrees_with_the_counts', ('UNKNOWN', 'MALFORMED', 0, 0), snap=lambda s: s.header.update(total=999))
    case('interface_index_out_of_range_in_the_header', ('UNKNOWN', 'MALFORMED', 0, 0), snap=lambda s: s.header.update(intf=32))
    case('fewer_register_entries_than_the_list', ('UNKNOWN', 'TRUNCATED', 0, 0), snap=lambda s: s.header.update(regs=18))
    case('status_value_out_of_range', ('UNKNOWN', 'MALFORMED', 0, 0), snap=lambda s: s.set('INTF_EN', 1, 3))
    case('snapshot_shorter_than_its_header', ('UNKNOWN', 'TRUNCATED', 0, 0), post=lambda d: d[:31])
    case('empty_snapshot', ('UNKNOWN', 'TRUNCATED', 0, 0), post=lambda d: b'')
    case('null_snapshot_pointer', ('UNKNOWN', 'TRUNCATED', 0, 0), mode=NULL_SNAP)
    case('null_parameter_pointer', ('UNKNOWN', 'BAD_PARAMS', 'NULL', 0), mode=NULL_PARAMS)

    # -- parameters
    def pf(name, fault, fn):
        case('params_' + name, ('UNKNOWN', 'BAD_PARAMS', fault, 0), params=fn)

    pf('unknown_abi', 'ABI', lambda p: p.update(abi=2))
    pf('reserved_set', 'RESERVED', lambda p: p.update(reserved=1))
    pf('gcc_overlaps_dispcc', 'OVERLAP', lambda p: p.update(gcc=(0x0af00000 - 0x100, 0x200000)))
    pf('empty_gcc_block', 'BLOCK', lambda p: p.update(gcc=(p['gcc'][0], 0)))
    pf('gcc_offset_past_the_block', 'OFFSET', lambda p: p['reg_off'].update(GCC_DISP_AHB=0x200000))
    pf('nothing_required', 'REQUIRED', lambda p: p.update(required=0))
    pf('gates_waived', 'REQUIRED', lambda p: p.update(required=p['required'] & ~EV['GATES']))
    pf('timing_waived', 'REQUIRED', lambda p: p.update(required=p['required'] & ~EV['TIMING']))
    pf('geometry_waived', 'REQUIRED', lambda p: p.update(required=p['required'] & ~EV['GEOMETRY']))
    pf('unknown_evidence_class', 'REQUIRED', lambda p: p.update(required=p['required'] | 0x40))
    pf('interface_index_beyond_the_register', 'INTF_INDEX', lambda p: p.update(intf_index=32))
    pf('version_required_but_unknown', 'VERSION', lambda p: p.update(dpu_major=0))
    pf('version_major_too_wide', 'VERSION', lambda p: p.update(dpu_major=16))
    pf('version_minor_too_wide', 'VERSION', lambda p: p.update(dpu_minor=0x1000))
    pf('zero_width', 'MODE', lambda p: p.update(gop_width=0))
    pf('zero_height', 'MODE', lambda p: p.update(gop_height=0))
    pf('absurd_width', 'MODE', lambda p: p.update(gop_width=16385))
    pf('empty_block', 'BLOCK', lambda p: p.update(mdss=(p['mdss'][0], 0)))
    pf('wrapping_block', 'BLOCK', lambda p: p.update(dispcc=(0xffffffffffff0000, 0x20000)))
    pf('intf_outside_mdp', 'BLOCK', lambda p: p.update(intf=(0x0b000000, 0x280)))
    pf('intf_straddles_the_end_of_mdp', 'BLOCK', lambda p: p.update(intf=(0x0ae01000 + 0x8f000 - 0x100, 0x280)))
    pf('dispcc_overlaps_mdss', 'OVERLAP', lambda p: p.update(dispcc=(0x0ae00800, 0x20000)))
    pf('mdss_overlaps_mdp', 'OVERLAP', lambda p: p.update(mdss=(0x0ae00000, 0x2000)))
    pf('unaligned_offset', 'OFFSET', lambda p: p['reg_off'].update(INTF_STATUS=0x26e))
    pf('offset_past_the_block', 'OFFSET', lambda p: p['reg_off'].update(INTF_STATUS=0x280))
    pf('hw_version_offset_past_its_block', 'OFFSET', lambda p: p['reg_off'].update(HW_VERSION=0x1000))
    pf('too_many_ctl_pages', 'CTL', lambda p: p.update(ctl_count=9))
    pf('routing_required_without_ctl_pages', 'CTL', lambda p: p.update(ctl_count=0))
    pf('ctl_register_past_its_page', 'CTL', lambda p: p.update(ctl_intf_active_off=0x290))
    pf('ctl_page_outside_mdp', 'CTL', lambda p: p['ctl_base'].__setitem__(2, 0x0b100000))
    pf('ctl_pages_overlap', 'CTL', lambda p: p['ctl_base'].__setitem__(1, p['ctl_base'][0] + 0x100))
    pf('ctl_page_overlaps_the_intf_page', 'CTL', lambda p: p['ctl_base'].__setitem__(1, p['intf'][0] - 0x100))
    pf('counters_required_without_a_refresh_range', 'COUNTER',
       lambda p: p.update(required=p['required'] | EV['COUNTERS'], refresh_hz_min=0, refresh_hz_max=0))
    pf('refresh_range_reversed', 'COUNTER', lambda p: p.update(refresh_hz_min=100, refresh_hz_max=50))
    pf('refresh_range_absurd', 'COUNTER', lambda p: p.update(refresh_hz_max=1001))
    pf('counters_required_without_a_minimum_gap', 'COUNTER',
       lambda p: p.update(required=p['required'] | EV['COUNTERS'], counter_gap_min_us=0))
    return cases


def resolve(expect, layout):
    """Symbolic (verdict, why, detail, evidence) -> numbers from the compiled enumerations."""
    verdict, why, detail, evidence = expect
    if isinstance(detail, str):
        if re.fullmatch(r'CTL\d+', detail):
            detail = layout['DPC_REG_COUNT'] + int(detail[3:])
        elif detail in reg_names():
            detail = layout['DPC_REG_' + detail]
        else:
            detail = layout['DPC_' + ('TF_' if detail in ('PERIOD', 'V_WINDOW', 'H_WINDOW', 'OUTSIDE_FRAME') else 'PF_') + detail]
    return layout['DPC_' + verdict], layout['DPC_WHY_' + why], detail, evidence


class HarnessFailure(AssertionError):
    pass


def batch(exe, records, layout):
    """[(params dict, snapshot bytes, mode)] -> one tuple (verdict, why, detail, evidence) or '-1' per record."""
    payload = bytearray()
    for p, data, mode in records:
        pbytes = pack_params(p, layout)
        payload += struct.pack('<III', len(pbytes), len(data), mode) + pbytes + data
    run = subprocess.run([str(exe), 'batch'], input=bytes(payload), capture_output=True, timeout=300)
    if run.returncode:
        raise HarnessFailure(f'harness exited {run.returncode}: {run.stderr.decode(errors="replace")[:1500]}')
    rows = [line if line == '-1' or line.startswith('bad-return') else tuple(int(x) for x in line.split())
            for line in run.stdout.decode().splitlines()]
    if len(rows) != len(records):
        raise HarnessFailure(f'harness answered {len(rows)} of {len(records)} records')
    return rows


def evaluate(exe, layout):
    """Run the scenario table against `exe`: ([(name, wanted, got)] that differ, all rows)."""
    cases = case_builder(layout)
    rows = batch(exe, [thunk() for _, thunk, _ in cases], layout)
    wrong = []
    for (name, _, expect), row in zip(cases, rows):
        want = resolve(expect, layout)
        if not (isinstance(row, tuple) and all(w is None or w == g for w, g in zip(want, row))):
            wrong.append((name, want, row))
    return wrong, rows


# ---- Tests ------------------------------------------------------------------------------------------------------------
class Layout(unittest.TestCase):
    def test_header_enumerators_match_the_compiled_values(self):
        _, L = native()
        for enum in ('dpc_reg', 'dpc_why', 'dpc_param_fault', 'dpc_timing_fault', 'dpc_verdict', 'dpc_blk'):
            for i, name in enumerate(header_enum(enum)):
                self.assertEqual(L[name], i + 1 if enum == 'dpc_verdict' else i, name)

    def test_register_list_is_the_one_the_study_names(self):
        self.assertEqual(len(reg_names()), 20)
        self.assertEqual(reg_names()[:5], ['GCC_DISP_AHB', 'GDSC', 'AHB_CBCR', 'MDP_CBCR', 'HW_VERSION'])

    def test_params_struct_has_no_padding_and_the_documented_size(self):
        _, L = native()
        self.assertEqual((L['sizeof.dpc_params'], L['sizeof.dpc_result']), (280, 16))
        self.assertEqual(L['offsetof.dpc_params.reg_off'] + 4 * len(reg_names()), L['sizeof.dpc_params'])

    def test_wire_format_constants(self):
        _, L = native()
        self.assertEqual((L['DPC_SNAP_HEADER'], L['DPC_SNAP_ENTRY'], L['DPC_SNAP_MAGIC']), (32, 16, 0x53435044))
        self.assertEqual(struct.pack('<I', L['DPC_SNAP_MAGIC']), b'DPCS')
        self.assertEqual([L['DPC_SNAP_OFF_' + n] for n in ('TOTAL', 'INTF', 'REGS', 'CTLS', 'GAP')], [8, 12, 16, 20, 24])

    def test_bit_meanings_match_the_cited_linux_values(self):
        _, L = native()
        self.assertEqual((L['DPC_CBCR_CLK_OFF'], L['DPC_GDSC_PWR_ON']), (1 << 31, 1 << 31))
        self.assertEqual((L['DPC_INTF_CFG_ACTIVE_H_EN'], L['DPC_INTF_CFG_ACTIVE_V_EN']), (1 << 29, 1 << 30))

    def test_names(self):
        exe, L = native()
        out = subprocess.run([str(exe), 'names'], capture_output=True, text=True, check=True).stdout.splitlines()
        why = [l.split(' ', 2)[2] for l in out if l.startswith('why ')]
        self.assertEqual(len(why), L['DPC_WHY_COUNT'] + 2)
        self.assertEqual(len(set(why[:L['DPC_WHY_COUNT']])), L['DPC_WHY_COUNT'])
        self.assertEqual(why[-1], 'unknown')
        verdicts = [l.split(' ', 2)[2] for l in out if l.startswith('verdict ')]
        self.assertEqual(verdicts, ['unknown', 'active', 'inactive', 'unknown', 'inconsistent', 'unknown'])


class Scenarios(unittest.TestCase):
    def test_every_scenario(self):
        exe, L = native()
        wrong, _ = evaluate(exe, L)
        self.assertEqual(wrong, [], '\n'.join(f'{n}: want {w}, got {g}' for n, w, g in wrong))

    def test_scenario_names_are_unique(self):
        names = [n for n, _, _ in case_builder(native()[1])]
        self.assertEqual(len(names), len(set(names)))
        self.assertGreaterEqual(len(names), 100)

    def test_every_reason_verdict_and_parameter_fault_is_exercised(self):
        exe, L = native()
        wrong, rows = evaluate(exe, L)
        self.assertEqual(wrong, [])
        rows = [r for r in rows if isinstance(r, tuple)]
        self.assertEqual({r[1] for r in rows}, set(range(L['DPC_WHY_COUNT'])), 'a reason no scenario reaches is untested')
        self.assertEqual({r[0] for r in rows}, {L['DPC_ACTIVE'], L['DPC_INACTIVE'], L['DPC_UNKNOWN'], L['DPC_INCONSISTENT']})
        self.assertEqual({r[2] for r in rows if r[1] == L['DPC_WHY_BAD_PARAMS']}, set(range(1, L['DPC_PF_COUNTER'] + 1)))
        self.assertEqual({r[2] for r in rows if r[1] == L['DPC_WHY_TIMING_INCOHERENT']},
                         {L['DPC_TF_PERIOD'], L['DPC_TF_V_WINDOW'], L['DPC_TF_H_WINDOW'], L['DPC_TF_OUTSIDE_FRAME']})

    def test_only_the_good_snapshots_are_active(self):
        exe, L = native()
        _, rows = evaluate(exe, L)
        for (name, _, _), row in zip(case_builder(L), rows):
            if isinstance(row, tuple) and row[0] == L['DPC_ACTIVE']:
                self.assertTrue(name.startswith('good'), name)

    def test_null_output_pointer_is_the_only_call_failure(self):
        exe, L = native()
        p = hamoa_params()
        self.assertEqual(batch(exe, [(p, good_snapshot(p, L).pack(), NULL_OUT)], L), ['-1'])

    def test_a_different_platform_needs_no_code_change(self):
        """The same logic accepts a platform sharing no base, offset, INTF, CTL page or version with Hamoa, and refuses
        its snapshot when read with Hamoa's parameters: nothing is hard-coded."""
        exe, L = native()
        alt = alt_params()
        data = good_snapshot(alt, L).pack()
        rows = batch(exe, [(alt, data, 0), (hamoa_params(), data, 0)], L)
        self.assertEqual(rows[0][0], L['DPC_ACTIVE'])
        self.assertEqual(rows[1][:2], (L['DPC_INCONSISTENT'], L['DPC_WHY_INTF_MISMATCH']))

    def test_no_literal_hardware_address_in_the_logic(self):
        text = re.sub(r'/\*.*?\*/', '', SOURCE.read_text(), flags=re.S)
        for literal in re.findall(r'\b0[xX][0-9a-fA-F]{5,}\b', text):
            self.assertIn(literal.lower(), ('0xffffffff', '0xfffffu'), literal)


class Properties(unittest.TestCase):
    def test_no_strict_prefix_of_a_good_snapshot_is_active(self):
        exe, L = native()
        p = hamoa_params()
        data = good_snapshot(p, L).pack()
        for k, row in enumerate(batch(exe, [(p, data[:k], 0) for k in range(len(data))], L)):
            self.assertEqual(row[:2], (L['DPC_UNKNOWN'], L['DPC_WHY_TRUNCATED']), f'prefix of {k} bytes')

    def test_degrading_any_one_reading_is_never_active(self):
        """NOT_TRIED, FAULT and all-ones on every register and CTL page. Only readings that carry no weight may leave
        the verdict ACTIVE: an unread counter enable (counters are optional), and any CTL page other than the one that
        lists the interface (another page lists it)."""
        exe, L = native()
        p = hamoa_params()
        records, labels = [], []
        for n in reg_names():
            for how in ('not-tried', 'fault', 'all-ones'):
                s = good_snapshot(p, L)
                {'not-tried': lambda: s.clear(n), 'fault': lambda: s.set(n, s.regs[n][1], L['DPC_ST_FAULT']),
                 'all-ones': lambda: s.set(n, 0xffffffff)}[how]()
                records.append((p, s.pack(), 0))
                labels.append((n, how))
        for i in range(p['ctl_count']):
            for how, (value, status) in (('not-tried', (0, L['DPC_ST_NOT_TRIED'])), ('fault', (0, L['DPC_ST_FAULT'])),
                                         ('all-ones', (0xffffffff, L['DPC_ST_OK']))):
                s = good_snapshot(p, L)
                s.ctls[i][1:] = [value, status]
                records.append((p, s.pack(), 0))
                labels.append((f'CTL{i}', how))
        still_active = {l for l, r in zip(labels, batch(exe, records, L)) if r[0] == L['DPC_ACTIVE']}
        self.assertEqual(still_active, {('INTF_FLC_EN', 'not-tried')} |
                         {(f'CTL{i}', how) for i in range(1, 6) for how in ('not-tried', 'fault', 'all-ones')})

    def test_changing_one_affirmative_bit_is_never_active(self):
        """Every bit the decision relies on, flipped alone. Bits it does not read (the hsync pulse width, a one-line
        slack in the vertical window) are deliberately absent."""
        exe, L = native()
        p = hamoa_params()
        flips = [('GDSC', 31), ('AHB_CBCR', 31), ('MDP_CBCR', 31), ('INTF_EN', 0), ('INTF_STATUS', 0)]
        flips += [('HW_VERSION', b) for b in range(16, 32)] + [('INTF_HSYNC_CTL', b) for b in range(16, 32)]
        flips += [(n, b) for n in ('INTF_VSYNC_PERIOD', 'INTF_ACT_HCTL') for b in range(32)]
        flips += [(n, b) for n in ('INTF_ACT_V_START', 'INTF_ACT_V_END') for b in range(12, 32)]
        records = []
        for n, bit in flips:
            s = good_snapshot(p, L)
            s.regs[n][1] ^= 1 << bit
            records.append((p, s.pack(), 0))
        s = good_snapshot(p, L)
        s.ctls[0][1] ^= 1 << p['intf_index']
        records.append((p, s.pack(), 0))
        for label, row in zip(flips + [('CTL0', p['intf_index'])], batch(exe, records, L)):
            self.assertNotEqual(row[0], L['DPC_ACTIVE'], f'{label} flipped')

    def test_random_snapshots_never_crash_and_are_never_active(self):
        exe, L = native()
        rng, p, records = random.Random(0x5eed), hamoa_params(), []
        for i in range(400):
            n = rng.choice([0, 1, 31, 32, 33, 47, 48, 100, 431, 432, 433, 500])
            data = bytes(rng.randrange(256) for _ in range(n))
            if i % 3 == 0 and n >= 32:
                data = struct.pack('<IHHII', L['DPC_SNAP_MAGIC'], 1, 0, rng.choice([n, 432, 0xffffffff]), 5) + data[16:]
            records.append((p, data, rng.randrange(8)))
        self.assertTrue(all(r[0] != L['DPC_ACTIVE'] for r in batch(exe, records, L)))

    def test_random_parameters_never_crash(self):
        exe, L = native()
        rng, snap, records = random.Random(0xd15c), good_snapshot(hamoa_params(), L).pack(), []
        for _ in range(300):
            p = hamoa_params()
            for key in rng.sample(['required', 'intf_index', 'dpu_major', 'dpu_minor', 'gop_width', 'gop_height',
                                   'ctl_count', 'ctl_length', 'ctl_intf_active_off', 'refresh_hz_min', 'refresh_hz_max',
                                   'counter_gap_min_us'], 4):
                p[key] = rng.choice([0, 1, 5, 31, 32, 0x290, 0xfff, 0x1000, 0xffffffff, rng.randrange(1 << 32)])
            for blk in rng.sample(['dispcc', 'mdss', 'mdp', 'intf'], 2):
                p[blk] = (rng.choice([0, 0x0ae00000, 0xffffffffffffffff, rng.randrange(1 << 48)]),
                          rng.choice([0, 0x1000, 0xffffffffffffffff, rng.randrange(1 << 20)]))
            if rng.random() < 0.3:
                p['ctl_base'] = [rng.randrange(1 << 48) for _ in range(6)]
            records.append((p, snap, 0))
        batch(exe, records, L)


class Writer(unittest.TestCase):
    def run_writer(self, intf, ctls, gap, puts):
        exe, _ = native()
        payload = struct.pack('<IIII', intf, ctls, gap, len(puts))
        for kind, index, pa, value, status in puts:
            payload += struct.pack('<IIQII', kind, index, pa, value, status)
        out = subprocess.run([str(exe), 'writer'], input=payload, capture_output=True, timeout=60, check=True).stdout.decode()
        return (dict(l.split(' ', 1) for l in out.splitlines() if not l.startswith('put')),
                [int(l.split()[2]) for l in out.splitlines() if l.startswith('put')])

    def test_the_c_writer_and_the_python_packer_build_the_same_bytes(self):
        _, L = native()
        p = hamoa_params()
        s = good_snapshot(p, L)
        puts = [(0, i, *s.regs[n]) for i, n in enumerate(reg_names())] + [(1, i, *c) for i, c in enumerate(s.ctls)]
        reply, rcs = self.run_writer(p['intf_index'], p['ctl_count'], 150000, puts)
        self.assertEqual(rcs, [0] * len(puts))
        self.assertEqual(bytes.fromhex(reply['hex']), s.pack())
        self.assertEqual(int(reply['len']), len(s.pack()))

    def test_unwritten_entries_are_not_tried(self):
        data = bytes.fromhex(self.run_writer(5, 2, 0, [])[0]['hex'])
        n = len(reg_names())
        self.assertEqual(len(data), 32 + 16 * (n + 2))
        self.assertTrue(all(data[32 + 16 * i + 12:32 + 16 * i + 16] == bytes(4) for i in range(n + 2)))

    def test_writer_refusals(self):
        self.assertEqual(self.run_writer(5, 9, 0, [])[0]['len'], '0')
        self.assertEqual(self.run_writer(32, 2, 0, [])[0]['len'], '0')
        n = len(reg_names())
        _, rcs = self.run_writer(5, 2, 0, [(0, n, 0, 0, 1), (0, 0, 0, 0, 3), (1, 2, 0, 0, 1), (1, 1, 0, 0, 1), (0, n - 1, 0, 0, 1)])
        self.assertEqual(rcs, [-1, -1, -1, 0, 0])


class Freestanding(unittest.TestCase):
    def test_object_has_no_undefined_or_writable_symbols(self):
        clang, nm = find_tool('clang'), find_tool('llvm-nm')
        if not clang or not nm:
            self.skipTest('clang or llvm-nm not found')
        BUILD.mkdir(parents=True, exist_ok=True)
        obj = BUILD / 'aarch64.o'
        built = subprocess.run([clang, '--target=aarch64-unknown-none-elf', '-ffreestanding', '-fno-builtin',
                                '-fno-stack-protector', '-O2', '-std=c11', '-Wall', '-Wextra', '-Werror', '-c', str(SOURCE),
                                '-o', str(obj)], capture_output=True, text=True)
        if built.returncode and 'unknown target' in built.stderr.lower():
            self.skipTest('clang cannot target aarch64-unknown-none-elf')
        self.assertEqual(built.returncode, 0, built.stderr)
        symbols = [l.split() for l in subprocess.run([nm, str(obj)], capture_output=True, text=True, check=True).stdout.splitlines()]
        self.assertEqual([s for s in symbols if len(s) == 2 and s[0] == 'U'], [], 'undefined symbols')
        self.assertEqual([s for s in symbols if len(s) == 3 and s[1] in ('d', 'D', 'b', 'B')], [], 'writable data')


class StudyDocument(unittest.TestCase):
    """The X2 dump list in docs/SP11-DISPLAY-STUDY.md is the list the code checks: the same registers in the same
    order, the same blocks, bases and offsets, and the parameter values the Hamoa fixture uses."""
    DOC = ROOT / 'docs' / 'SP11-DISPLAY-STUDY.md'

    def section(self):
        match = re.search(r'<!-- BEGIN X2-DISPLAY-LIST.*?-->(.*?)<!-- END X2-DISPLAY-LIST -->', self.DOC.read_text(), re.S)
        self.assertTrue(match, 'the X2 section markers are missing')
        return match.group(1)

    def test_register_table_is_the_register_list(self):
        rows = re.findall(r'^\| (\d+) \| ([A-Z0-9_]+) \| (\w+) \| (0x[0-9a-f]+) \| (0x[0-9a-f]+) \| ', self.section(), re.M)
        self.assertEqual([r[1] for r in rows], reg_names())
        self.assertEqual([int(r[0]) for r in rows], list(range(1, len(reg_names()) + 1)))
        p = hamoa_params()
        for _, name, blk, base, off in rows:
            self.assertEqual((blk, int(base, 16), int(off, 16)), (block_of(name), p[blk][0], HAMOA_REG_OFF[name]), name)

    def test_ctl_table(self):
        rows = re.findall(r'^\| (\d) \| (0x[0-9a-f]+) \| (0x[0-9a-f]+) \|$', self.section(), re.M)
        p = hamoa_params()
        self.assertEqual([(int(i), int(b, 16), int(o, 16)) for i, b, o in rows],
                         [(i, p['ctl_base'][i], p['ctl_intf_active_off']) for i in range(p['ctl_count'])])

    def test_parameter_table(self):
        found = {k: int(v, 0) for k, v in re.findall(r'^\| ([a-z_.]+) \| (0x[0-9a-f]+|\d+) \| ', self.section(), re.M)}
        p = hamoa_params()
        want = {k: p[k] for k in ('intf_index', 'dpu_major', 'dpu_minor', 'ctl_count', 'ctl_length', 'ctl_intf_active_off',
                                  'required', 'refresh_hz_min', 'refresh_hz_max', 'counter_gap_min_us')}
        for blk in ('gcc', 'dispcc', 'mdss', 'mdp', 'intf'):
            want[blk + '.base'], want[blk + '.length'] = p[blk]
        self.assertEqual({k: found.get(k) for k in want}, want)

    def test_no_machine_strings_and_size(self):
        text = self.DOC.read_text()
        for prefix in HOME_PREFIXES:
            self.assertNotIn(prefix, text)
        self.assertTrue(text.isascii())
        self.assertLess(len(text.splitlines()), 1500)
        self.assertLess(len(text.encode()), 50 * 1024)


class Hygiene(unittest.TestCase):
    FILES = ('compat/display-pipeline-check.h', 'compat/display-pipeline-check.c', 'tools/test-display-pipeline-check.c',
             'tools/test-display-pipeline-check.py')

    def test_spdx_and_no_machine_strings(self):
        for rel in self.FILES:
            text = (ROOT / rel).read_text()
            self.assertIn('SPDX-License-Identifier: MIT', '\n'.join(text.splitlines()[:2]), rel)
            for prefix in HOME_PREFIXES:
                self.assertNotIn(prefix, text, rel)

    def test_sizes(self):
        for rel in self.FILES:
            self.assertLess(len((ROOT / rel).read_text().splitlines()), 1500, rel)
            self.assertLess((ROOT / rel).stat().st_size, 50 * 1024, rel)


if __name__ == '__main__':
    unittest.main(verbosity=1)
