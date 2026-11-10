#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Derive the Surface Pro 11 USB host-controller table from a board descriptor.

q1n1's USB host link needs, per controller, the DWC3 register base and the Qualcomm glue block next to it. The A16
build hard-codes both (q1n1 platform/uefi/usb-ports.c lines 367 and 382). This tool derives them for the SP11 from the
board descriptor (config/boards/sp11.json, read with tools/board_descriptor.py) and refuses what the descriptor does
not support:

* a descriptor that does not list the controllers the Denali device tree enables: a descriptor whose `usb-host` device is
  `unknown` yields a null table and exit 1, naming the evidence needed. The shipped sp11.json lists both controllers,
  with evidence INFERRED from the pinned public device tree (nothing is measured on a unit yet);
* a base that differs from the pinned device tree, a window that overlaps another Hamoa controller, or a controller
  the board leaves disabled;
* an A16 hard-coded base (0x0a600000, 0x0a800000) whose provenance is not an independent source. An SP11 port gets
  such a value only because a public device tree or an SP11 measurement lists it, never because the A16 code has it.
  "Independent" means the source cites a device-tree line (hamoa.dtsi:NNNN) or an ACPI dump, or claims a measurement
  with VERIFIED or OBSERVED evidence; a negation ("unmeasured guess", "not measured on any unit") voids it.

Everything the tool knows beyond the descriptor comes from one pinned public Linux device tree and is labelled
INFERRED: that tree describes a Linux driver's view of the SoC, not this unit. Nothing here runs on hardware.

    python3 tools/sp11_usb_ports.py table [DESCRIPTOR] [--json]       derive and print; exit 0 ok, 1 refused
    python3 tools/sp11_usb_ports.py propose [DESCRIPTOR] [--out FILE] print the descriptor that would make it derivable
    python3 tools/sp11_usb_ports.py emit-c DESCRIPTOR OUT.c [--allow-unmeasured-id]
                                                                       write the C table q1n1 links under SNAP_PLATFORM_SP11;
                                                                       refused until the DWC3 core id is measured (X2), unless
                                                                       the flag accepts expect_gsnpsid 0 ("any DWC3 family")
    python3 tools/sp11_usb_ports.py inventory [--q1n1-dir DIR]       check the A16 inventory against a q1n1 checkout

Evidence: EXPERIMENTAL (host tool, synthetic and public-source tests only). Hardware: NOT RUN.
"""
import argparse
import copy
import dataclasses
import json
import os
import re
import sys
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))
import board_descriptor as bd  # noqa: E402

ROOT = _TOOLS.parent
DEFAULT_DESCRIPTOR = ROOT / 'config' / 'boards' / 'sp11.json'

LINUX_COMMIT = '7475ea8b58616b33c04afb976cf0fbaad9dd63fb'
Q1N1_COMMIT = '7872398694ca8322e9cdebcc4ca2380491295f24'
DT = 'arch/arm64/boot/dts/qcom/'
HAMOA = f'Linux {LINUX_COMMIT} {DT}hamoa.dtsi'
DENALI = f'Linux {LINUX_COMMIT} {DT}x1-microsoft-denali.dtsi'
PMICS = f'Linux {LINUX_COMMIT} {DT}hamoa-pmics.dtsi'
DWC3_QCOM = f'Linux {LINUX_COMMIT} drivers/usb/dwc3/dwc3-qcom.c'
DWC3_CORE = f'Linux {LINUX_COMMIT} drivers/usb/dwc3/core.h'

SOC_NAME, SOC_FAMILY = 'X1E80100', 'hamoa'
PAGE = bd.PAGE
IPA_LIMIT = 1 << bd.MONITOR_IPA_BITS

# Glue block ("qscratch") that sits next to the DWC3 core inside each device-tree window: offset and size. Linux
# derives the offset from the single reg entry (dwc3-qcom.c:662-664) and the window 0xfc100 contains it.
QSCRATCH_OFFSET = 0xf8800
QSCRATCH_SPAN = 0x400
# The glue block must not start inside the DWC3 core registers (dwc3-qcom.c:43, SDM845_DWC3_CORE_SIZE).
CORE_SPAN = 0xcd00
WINDOW_MAX = 0x200000            # controllers are 2 MiB apart in the device tree; a window never exceeds that
A16_BASES = {0x0a600000: 'USB0', 0x0a800000: 'USB1'}    # q1n1 platform/uefi/usb-ports.c:367
# Core ids by IP family: GSNPSID[31:16] (Linux core.h:81, 1293-1295). The exact release is UNKNOWN on this unit.
DWC3_FAMILIES = (0x5533, 0x3331, 0x3332)

# Table ABI shared with q1n1 platform/uefi/sp11-usb-ports.h. Change both together.
TABLE_MAGIC = int.from_bytes(b'SP11USB1', 'little')
TABLE_VERSION = 1
MAX_PORTS = 2
FLAG_HOST_ONLY = 1 << 0
FLAG_A16_BASE_LISTED = 1 << 1
KNOWN_FLAGS = FLAG_HOST_ONLY | FLAG_A16_BASE_LISTED
ERRORS = ('OK', 'NULL', 'MAGIC', 'VERSION', 'COUNT', 'RESERVED', 'FLAGS', 'BASE', 'WINDOW', 'LIMIT', 'QSCRATCH',
          'IDENTITY', 'A16_BASE', 'OVERLAP')

# Provenance that makes an A16-constant base independent, and the ones that void it. The descriptor loader keeps only
# `source` and `evidence`, so notes are not inspected. A word is not a citation: "unmeasured guess" and "not measured
# on any unit" both contain "measured". A source counts when it cites a public device-tree line or names an ACPI dump,
# or claims a measurement AND carries measured-grade evidence, and a negation anywhere in it voids all three.
PUBLIC_CITATIONS = (r'\bhamoa\.dtsi:\d+', r'\bacpi\b')
MEASUREMENT_CLAIM = r'\bmeasured\b'
MEASURED_EVIDENCE = ('VERIFIED', 'OBSERVED')
NEGATIONS = (r"\b(?:no|not|never|none|without|cannot|can't|isn't|wasn't|didn't|n/a|unknown|guess(?:ed)?|"
             r"assum(?:ed|ption))\b|\bun(?:measured|verified|confirmed)\b")
# Regexes, not substrings: a commit hash can contain "a16" by chance, and a hash must not void a good source.
A16_MARKERS = (r'\bq1n1\b', r'\ba16\b', r'usb-ports\.c', r'usb-ebs', r'usb1-device', r'bios312', r'usbconfigdxe',
               r'zenbook', r'ux3607')


class UsbPortError(ValueError):
    """The table cannot be produced; the message lists every refusal."""


@dataclasses.dataclass(frozen=True)
class Controller:
    """One DWC3 node of hamoa.dtsi: where Linux says it is, and whether the Denali board enables it."""
    range_name: str        # descriptor range name (at most 15 characters, board_descriptor.NAME_RE)
    dt_label: str
    dt_base: int
    dt_window: int         # the `reg` size, 0xfc100: DWC3 core and glue, not a multiple of a page
    node_lines: str        # hamoa.dtsi lines of the node header through its reg property
    iommu_line: int        # hamoa.dtsi line of its apps_smmu stream
    iommu_stream: int
    denali_lines: str      # x1-microsoft-denali.dtsi lines that enable it; empty when the board leaves it disabled

    @property
    def denali_enabled(self):
        return bool(self.denali_lines)


HAMOA_CONTROLLERS = (
    Controller('usb-1-ss2', 'usb_1_ss2', 0x0a000000, 0xfc100, '5016-5018', 5069, 0x14a0, ''),
    Controller('usb-2', 'usb_2', 0x0a200000, 0xfc100, '5107-5109', 5158, 0x14e0, ''),
    Controller('usb-mp', 'usb_mp', 0x0a400000, 0xfc100, '5175-5177', 5235, 0x1400, ''),
    Controller('usb-1-ss0', 'usb_1_ss0', 0x0a600000, 0xfc100, '5254-5256', 5307, 0x1420, '1364-1367'),
    Controller('usb-1-ss1', 'usb_1_ss1', 0x0a800000, 0xfc100, '5345-5347', 5398, 0x1460, '1394-1397'),
)
BY_RANGE = {c.range_name: c for c in HAMOA_CONTROLLERS}

# What the Denali board adds to each enabled controller, all read from the pinned trees. `value` is a short
# description for people; every one is INFERRED for the unit. Sources are file:lines within the pinned commit.
BOARD_FACTS = {
    'usb-1-ss0': {
        'connector': ('0, Left-side bottom port, power-role dual, data-role dual', 'x1-microsoft-denali.dtsi:62-67'),
        'dr_mode': ('host', 'x1-microsoft-denali.dtsi:1364-1365'),
        'hs_phy': ('eUSB2 PHY phy@fd3000 behind SMB2360 repeater 0',
                   'hamoa.dtsi:2819-2822; x1-microsoft-denali.dtsi:1348-1352; hamoa-pmics.dtsi:669-678'),
        'ss_phy': ('QMP USB3/DP combo phy@fd5000', 'hamoa.dtsi:2833-2835; x1-microsoft-denali.dtsi:1357-1375'),
        'retimer': ('Parade ps8830 on i2c3 address 0x8, reset pm8550 gpio 10', 'x1-microsoft-denali.dtsi:785-797'),
    },
    'usb-1-ss1': {
        'connector': ('1, Left-side top port, power-role dual, data-role dual', 'x1-microsoft-denali.dtsi:99-104'),
        'dr_mode': ('host', 'x1-microsoft-denali.dtsi:1394-1395'),
        'hs_phy': ('eUSB2 PHY phy@fd9000 behind SMB2360 repeater 1',
                   'hamoa.dtsi:2892-2895; x1-microsoft-denali.dtsi:1378-1382; hamoa-pmics.dtsi:684-693'),
        'ss_phy': ('QMP USB3/DP combo phy@fda000', 'hamoa.dtsi:2906-2908; x1-microsoft-denali.dtsi:1387-1405'),
        'retimer': ('Parade ps8830 on i2c7 address 0x8, reset tlmm gpio 176', 'x1-microsoft-denali.dtsi:851-863'),
    },
}

# Facts no public source settles; each names the measurement that would (docs/SP11-HOST-LINK-STUDY.md, section 6).
UNKNOWN_FACTS = (
    ('gsnpsid', 'the DWC3 core id at base+0xc120 on each controller: the first read-only X2 probe records it'),
    ('xhci_caplength_hciversion_maxports', 'xHCI CAPLENGTH, HCIVERSION and HCSPARAMS1 at base+0, +2 and +4: X2 probe'),
    ('uefi_leftover_state', 'which controller, PHY, clock, GDSC, repeater and retimer state firmware leaves at the '
                            'q1n1 entry point after a USB-stick boot and after slbounce: X2/X3 register captures'),
    ('adsp_pmic_glink_alive', 'whether the ADSP and PMIC-GLINK/UCSI answer at handoff (R19): X2 step 7'),
    ('data_role_outcome', 'which end of a bare cable is host after power delivery settles: X3, from the connector '
                          'status read over PMIC-GLINK and from the Mac side'),
)


@dataclasses.dataclass(frozen=True)
class Refusal:
    code: str
    detail: str


@dataclasses.dataclass(frozen=True)
class Fact:
    value: object
    evidence: str
    source: str


@dataclasses.dataclass(frozen=True)
class UsbPort:
    name: str                 # device-tree label, e.g. usb_1_ss0
    range_name: str
    dwc3_base: object         # int, or None when the descriptor does not establish it
    window_size: object
    qscratch_offset: int
    flags: int
    facts: dict

    @property
    def qscratch_base(self):
        return None if self.dwc3_base is None else self.dwc3_base + self.qscratch_offset


@dataclasses.dataclass(frozen=True)
class Result:
    board_id: str
    board_status: str
    ports: tuple
    refusals: tuple
    unknown: tuple
    boot_ready: str           # '' when the descriptor is boot-ready, else the reason it is not

    @property
    def ok(self):
        return not self.refusals

    @property
    def inferred(self):
        return tuple(sorted(f'{p.name}.{k}' for p in self.ports for k, f in p.facts.items() if f.evidence == 'INFERRED'))

    def to_json(self):
        return {
            'board': self.board_id, 'board_status': self.board_status, 'ok': self.ok,
            'boot_ready': self.boot_ready or 'yes',
            'ports': [{'name': p.name, 'range': p.range_name,
                       'dwc3_base': None if p.dwc3_base is None else f'{p.dwc3_base:#010x}',
                       'window_size': None if p.window_size is None else hex(p.window_size),
                       'qscratch_offset': hex(p.qscratch_offset),
                       'qscratch_base': None if p.qscratch_base is None else f'{p.qscratch_base:#010x}',
                       'flags': p.flags,
                       'facts': {k: {'value': f.value, 'evidence': f.evidence, 'source': f.source}
                                 for k, f in sorted(p.facts.items())}} for p in self.ports],
            'refusals': [{'code': r.code, 'detail': r.detail} for r in self.refusals],
            'inferred': list(self.inferred),
            'unknown': [{'fact': name, 'measurement': how} for name, how in self.unknown],
        }


def page_up(value):
    return (value + PAGE - 1) & ~(PAGE - 1)


def independent_source(board, range_name):
    """True when the descriptor's own provenance for this range names a source other than the A16 code or docs."""
    source, evidence = board.provenance.get(f'ranges.{range_name}', ('UNKNOWN', 'UNIMPLEMENTED'))
    text = source.lower()
    if evidence == 'UNIMPLEMENTED' or any(re.search(marker, text) for marker in A16_MARKERS):
        return False
    if re.search(NEGATIONS, text):
        return False
    if any(re.search(citation, text) for citation in PUBLIC_CITATIONS):
        return True
    return bool(re.search(MEASUREMENT_CLAIM, text)) and evidence in MEASURED_EVIDENCE


def _null_port(c):
    return UsbPort(c.dt_label, c.range_name, None, None, QSCRATCH_OFFSET, 0, {})


def _port_facts(board, c, base, size):
    descriptor = board.provenance.get(f'ranges.{c.range_name}', ('UNKNOWN', 'UNIMPLEMENTED'))
    facts = {
        'dwc3_base': Fact(f'{base:#010x}', descriptor[1], f'descriptor ranges.{c.range_name}: {descriptor[0]}'),
        'window_size': Fact(hex(size), descriptor[1], f'descriptor ranges.{c.range_name} (a page multiple covering '
                                                       f'the {c.dt_window:#x} device-tree window)'),
        'qscratch_offset': Fact(hex(QSCRATCH_OFFSET), 'INFERRED',
                                f'dwc3-qcom.c:41-43,662-664 inside hamoa.dtsi:{c.node_lines}'),
        'iommu_stream': Fact(hex(c.iommu_stream), 'INFERRED', f'apps_smmu stream, hamoa.dtsi:{c.iommu_line}'),
        'dma_coherent': Fact(True, 'INFERRED', 'dma-coherent in the controller node'),
    }
    for key, (value, source) in BOARD_FACTS[c.range_name].items():
        facts[key] = Fact(value, 'INFERRED', source)
    return facts


def derive(board):
    """Build the table from a validated Board. Always returns a Result; `ok` is False when anything is refused."""
    refusals = []

    def refuse(code, detail):
        refusals.append(Refusal(code, detail))

    try:
        bd.require_boot_ready(board)
        boot_ready = ''
    except bd.BoardNotBootReady as exc:
        boot_ready = str(exc).replace('descriptor is not boot-ready: ', '')

    def done(ports):
        return Result(board.board_id, board.status, tuple(ports), tuple(refusals), UNKNOWN_FACTS, boot_ready)

    if board.soc_name != SOC_NAME or board.soc_family != SOC_FAMILY:
        refuse('not-sp11', f'soc is {board.soc_name!r} / {board.soc_family!r}; this tool derives the Hamoa ({SOC_NAME}) '
                           'table only and never reuses the A16 constants for another SoC')
        return done(())

    state, mmio, owned = board.devices['usb-host']
    if state != 'present':
        refuse('usb-host-not-present', f'devices.usb-host is {state!r}: the descriptor does not say the controllers exist, '
                                       'so no base can be derived (the Denali device tree enables usb_1_ss0 and usb_1_ss1 '
                                       'at hamoa.dtsi:5256 and :5347; add them as ranges, section 5 of the study)')
    elif mmio is not True:
        refuse('usb-host-no-mmio', 'devices.usb-host is present but mmio is not true: it lists no controller windows')

    ranges = {r[0]: r for r in board.ranges or ()}
    listed = {}
    for name, (_, kind, base, size, device) in ranges.items():
        if name in BY_RANGE and device != 'usb-host':
            refuse('foreign-owner', f'range {name!r} names a Hamoa USB controller but belongs to device {device!r}')
        elif device == 'usb-host':
            if name not in BY_RANGE:
                refuse('unknown-range', f'range {name!r} belongs to usb-host but is not a Hamoa DWC3 controller; known: '
                                        f'{sorted(BY_RANGE)}')
            elif kind != 'mmio' or base is None:
                refuse('bad-range', f'range {name!r} must be an mmio range with a known base')
            else:
                listed[name] = (base, size)
    if state == 'present' and mmio is True and not any(n in listed for n in owned):
        refuse('no-usb-ranges', 'devices.usb-host owns no controller window')

    ports = []
    for c in HAMOA_CONTROLLERS:
        if c.range_name in listed and not c.denali_enabled:
            refuse('controller-not-enabled', f'{c.range_name} ({c.dt_label}) is listed but the Denali device tree leaves '
                                             'it disabled (x1-microsoft-denali.dtsi enables only usb_1_ss0 and usb_1_ss1)')
        if not c.denali_enabled:
            continue
        if c.range_name not in listed:
            refuse('missing-controller', f'{c.range_name} ({c.dt_label}) is enabled on the Denali board '
                                         f'(x1-microsoft-denali.dtsi:{c.denali_lines}) but the descriptor lists no window for '
                                         'it; both connectors must be known before X3 can say which one carries the Mac')
            ports.append(_null_port(c))
            continue
        base, size = listed[c.range_name]
        if base != c.dt_base:
            refuse('base-mismatch', f'{c.range_name}: descriptor base {base:#x} differs from the pinned device tree '
                                    f'{c.dt_base:#x} (hamoa.dtsi:{c.node_lines}); a measured value needs a reviewed '
                                    'reference update, not a silent override')
        if base in A16_BASES and not independent_source(board, c.range_name):
            refuse('a16-base-not-independent', f'{c.range_name}: base {base:#x} is an A16 hard-coded base '
                                               f'({A16_BASES[base]}, q1n1 usb-ports.c:367) and its provenance does not cite an '
                                               'independent source (the device tree, an ACPI dump or an SP11 measurement)')
        if size < QSCRATCH_OFFSET + QSCRATCH_SPAN:
            refuse('window-too-small', f'{c.range_name}: window {size:#x} does not cover the glue block ending at '
                                       f'{QSCRATCH_OFFSET + QSCRATCH_SPAN:#x}')
        if size > page_up(c.dt_window):
            refuse('window-exceeds-dt', f'{c.range_name}: window {size:#x} claims more than the device-tree window '
                                        f'{c.dt_window:#x} rounded up to a page ({page_up(c.dt_window):#x})')
        flags = FLAG_HOST_ONLY
        if base in A16_BASES and independent_source(board, c.range_name):
            flags |= FLAG_A16_BASE_LISTED
        ports.append(UsbPort(c.dt_label, c.range_name, base, size, QSCRATCH_OFFSET, flags, _port_facts(board, c, base, size)))

    # Exact device-tree windows (not the page-rounded descriptor sizes) must not overlap each other or any other
    # Hamoa controller's window, listed or not.
    spans = []
    for c in HAMOA_CONTROLLERS:
        base = listed[c.range_name][0] if c.range_name in listed else c.dt_base
        spans.append((c.range_name, base, base + c.dt_window, c.range_name in listed))
    for i, (a, a0, a1, a_listed) in enumerate(spans):
        for b, b0, b1, b_listed in spans[i + 1:]:
            if (a_listed or b_listed) and a0 < b1 and b0 < a1:
                refuse('controller-overlap', f'{a} [{a0:#x}, {a1:#x}) overlaps {b} [{b0:#x}, {b1:#x})')
    return done(ports)


def load_and_derive(path):
    """Read a descriptor with its loader. A descriptor the loader refuses is reported, not repaired."""
    try:
        return derive(bd.load(path))
    except bd.BoardDescriptorError as exc:
        return Result('?', '?', (), (Refusal('descriptor-invalid', str(exc)),), UNKNOWN_FACTS, 'descriptor did not load')


# ---- the table ABI and its reference validator (mirrors q1n1 platform/uefi/sp11-usb-ports.c, same order) ----

def table_dict(result):
    """The table as the C struct holds it. Refuses unless the derivation is clean.

    expect_gsnpsid is 0 for every port, meaning UNMEASURED: no source gives this unit's DWC3 core id, and q1n1's
    validator accepts any of DWC3_FAMILIES for 0. emit_c refuses to write that wildcard unless told to.
    """
    if not result.ok:
        raise UsbPortError('refusing to emit a table: ' + '; '.join(f'{r.code}: {r.detail}' for r in result.refusals))
    entries = [{'dwc3_base': p.dwc3_base, 'window_size': p.window_size, 'qscratch_offset': p.qscratch_offset,
                'expect_gsnpsid': 0, 'flags': p.flags} for p in result.ports]
    blank = {'dwc3_base': 0, 'window_size': 0, 'qscratch_offset': 0, 'expect_gsnpsid': 0, 'flags': 0}
    table = {'magic': TABLE_MAGIC, 'version': TABLE_VERSION, 'count': len(entries),
             'ports': entries + [dict(blank) for _ in range(MAX_PORTS - len(entries))]}
    verdict = check_table(table)
    if verdict != 'OK':
        raise UsbPortError(f'internal error: the derived table fails its own validator ({verdict})')
    return table


def check_table(table):
    """Return the name of the first rule the table breaks, or 'OK'. First failure wins; the order is the C order."""
    if table['magic'] != TABLE_MAGIC:
        return 'MAGIC'
    if table['version'] != TABLE_VERSION:
        return 'VERSION'
    count, ports = table['count'], table['ports']
    if not 1 <= count <= MAX_PORTS or len(ports) != MAX_PORTS:
        return 'COUNT'
    if any(any(v for v in p.values()) for p in ports[count:]):
        return 'RESERVED'
    for p in ports[:count]:
        if p['flags'] & ~KNOWN_FLAGS:
            return 'FLAGS'
        if p['dwc3_base'] == 0 or p['dwc3_base'] & (PAGE - 1):
            return 'BASE'
        if p['window_size'] == 0 or p['window_size'] & (PAGE - 1) or p['window_size'] > WINDOW_MAX:
            return 'WINDOW'
        if p['dwc3_base'] >= IPA_LIMIT or p['window_size'] >= IPA_LIMIT - p['dwc3_base']:
            return 'LIMIT'
        offset = p['qscratch_offset']
        if offset & 3 or offset < CORE_SPAN or p['window_size'] < QSCRATCH_SPAN or offset > p['window_size'] - QSCRATCH_SPAN:
            return 'QSCRATCH'
        if p['expect_gsnpsid'] and (p['expect_gsnpsid'] >> 16) not in DWC3_FAMILIES:
            return 'IDENTITY'
        if p['dwc3_base'] in A16_BASES and not p['flags'] & FLAG_A16_BASE_LISTED:
            return 'A16_BASE'
    for i in range(count):
        for j in range(i + 1, count):
            a, b = ports[i], ports[j]
            if a['dwc3_base'] < b['dwc3_base'] + b['window_size'] and b['dwc3_base'] < a['dwc3_base'] + a['window_size']:
                return 'OVERLAP'
    return 'OK'


def check_controller(expect_gsnpsid, gsnpsid, cap_dword, hcsparams1):
    """Mirror of q1n1_sp11_usb_controller_ok: 'OK', 'FAMILY', 'CAPLENGTH', 'HCIVERSION' or 'MAXPORTS'."""
    if expect_gsnpsid:
        if gsnpsid != expect_gsnpsid:
            return 'FAMILY'
    elif (gsnpsid >> 16) not in DWC3_FAMILIES:
        return 'FAMILY'
    caplength = cap_dword & 0xff
    if caplength < 0x20 or caplength & 3:
        return 'CAPLENGTH'
    if not 0x0100 <= cap_dword >> 16 < 0x0200:
        return 'HCIVERSION'
    if not 1 <= hcsparams1 >> 24 <= 16:
        return 'MAXPORTS'
    return 'OK'


def emit_c(result, descriptor_name='descriptor', allow_unmeasured_id=False):
    """C source for q1n1's `q1n1_sp11_usb_table`. Generated build input: never committed.

    Refuses unless `allow_unmeasured_id`. No source measures this unit's DWC3 core id (UNKNOWN_FACTS 'gsnpsid'), so
    the table carries expect_gsnpsid = 0, which q1n1's validator reads as "accept any of the three DWC3 core
    families": an unknown encoded as a permissive wildcard. It is written only when the caller says so explicitly
    (emit-c --allow-unmeasured-id) and the generated text says so too; X2's measurement of the id replaces it.
    """
    table = table_dict(result)
    unmeasured = [p['dwc3_base'] for p in table['ports'][:table['count']] if p['expect_gsnpsid'] == 0]
    if unmeasured and not allow_unmeasured_id:
        raise UsbPortError('refusing to emit a table whose core id is unmeasured: expect_gsnpsid 0 makes q1n1 accept any '
                           'DWC3 core family (0x5533, 0x3331 or 0x3332). Measure GSNPSID at X2 first, or pass '
                           '--allow-unmeasured-id to accept that knowingly')
    lines = [
        '/* SPDX-License-Identifier: MIT */',
        f'/* Generated by tools/sp11_usb_ports.py from {descriptor_name} (board {result.board_id}, status {result.board_status}).',
        ' * Build input for SNAP_PLATFORM_SP11 only; do not edit and do not commit. Every value is INFERRED from a public',
        f' * device tree through the descriptor (Linux {LINUX_COMMIT}); no hardware has confirmed it.',
        f' * Descriptor boot-ready: {result.boot_ready and "no" or "yes"}.',
        ' * expect_gsnpsid is 0 (UNMEASURED, emitted only because --allow-unmeasured-id was given): q1n1 then accepts any',
        ' * DWC3 core family until X2 measures the exact id. */',
        '#include "sp11-usb-ports.h"',
        '',
        'static const struct q1n1_sp11_usb_table table = {',
        '    .magic = Q1N1_SP11_USB_MAGIC,',
        '    .version = Q1N1_SP11_USB_VERSION,',
        f'    .count = {table["count"]},',
        '    .port = {',
    ]
    for p, port in zip(table['ports'], list(result.ports) + [None] * MAX_PORTS):
        names = [n for bit, n in ((FLAG_HOST_ONLY, 'Q1N1_SP11_USB_PORT_HOST_ONLY'),
                                  (FLAG_A16_BASE_LISTED, 'Q1N1_SP11_USB_PORT_A16_BASE_LISTED')) if p['flags'] & bit]
        label = f' /* {port.name} */' if port is not None and p['dwc3_base'] else ''
        lines.append(f'        {{ .dwc3_base = UINT64_C({p["dwc3_base"]:#x}), .window_size = {p["window_size"]:#x}u, '
                     f'.qscratch_offset = {p["qscratch_offset"]:#x}u, .expect_gsnpsid = {p["expect_gsnpsid"]:#x}u, '
                     f'.flags = {" | ".join(names) or "0u"} }},{label}')
    lines += ['    },', '};', '',
              'const struct q1n1_sp11_usb_table *q1n1_sp11_usb_table_get(void) { return &table; }', '']
    return '\n'.join(lines)


# ---- the descriptor that makes the table derivable ----

def strip_usb(raw):
    """A copy of a raw descriptor with every Hamoa USB controller range removed and usb-host back to `unknown`."""
    d = copy.deepcopy(raw)
    names = {c.range_name for c in HAMOA_CONTROLLERS}
    d['ranges'] = [r for r in d['ranges'] if r['name'] not in names]
    for name in names:
        d['provenance'].pop(f'ranges.{name}', None)
    d['devices']['usb-host'] = {'state': 'unknown', 'mmio': None, 'ranges': []}
    d['provenance']['devices.usb-host'] = {'source': 'UNKNOWN', 'evidence': 'UNIMPLEMENTED'}
    return d


def proposed_descriptor(raw):
    """`raw` with the two Denali USB controllers listed. A proposal for review, not an applied change.

    Sizes are 0xfd000: the device-tree window is 0xfc100, schema v1 needs a page multiple, so the tightest page-aligned
    claim overshoots by 0xf00 bytes, and the next controller starts 0x200000 later, so it claims nothing a neighbour owns.
    Idempotent: entries already present are replaced, so applying it to an updated file changes nothing.
    """
    d = strip_usb(raw)
    names = []
    for c in HAMOA_CONTROLLERS:
        if not c.denali_enabled:
            continue
        names.append(c.range_name)
        d['ranges'].append({'name': c.range_name, 'kind': 'mmio', 'base': f'{c.dt_base:#010x}',
                            'size': hex(page_up(c.dt_window)), 'device': 'usb-host'})
        d['provenance'][f'ranges.{c.range_name}'] = {
            'source': f'{HAMOA}:{c.node_lines} ({c.dt_label} reg {c.dt_base:#010x} size {c.dt_window:#x}, '
                      f'rounded up to a page multiple for schema v1)',
            'evidence': 'INFERRED',
            'note': f'Enabled by {DENALI}:{c.denali_lines} (dr_mode host). The glue block (offset 0xf8800) is inside '
                    'the window. Not measured on a unit.'}
    d['devices']['usb-host'] = {'state': 'present', 'mmio': True, 'ranges': names}
    d['provenance']['devices.usb-host'] = {
        'source': f'{DENALI}:1364-1367,1394-1397 (usb_1_ss0 and usb_1_ss1 enabled, dr_mode host); {HAMOA}:5256,5347',
        'evidence': 'INFERRED',
        'note': 'Two host controllers reach the two Left-side USB-C connectors through PMIC-GLINK '
                '(x1-microsoft-denali.dtsi:53-135). '
                'Whether either enumerates a Mac gadget is an X3 measurement.'}
    return d


# ---- the A16 inventory, checked against the q1n1 tree it cites ----

@dataclasses.dataclass(frozen=True)
class Item:
    key: str
    klass: str       # A16-SPECIFIC, FIRMWARE-COUPLED or GENERIC
    path: str        # relative to the q1n1 root
    line: int
    pattern: str     # regex the cited line must match
    summary: str


U = 'platform/uefi/'
INVENTORY = (
    Item('I01', 'A16-SPECIFIC', U + 'usb-ports.c', 367, r'0x0a800000u : 0x0a600000u',
         'the two DWC3 controller bases, one literal per port'),
    Item('I02', 'A16-SPECIFIC', U + 'usb-ports.c', 382, r'base \+ 0xf8800', 'the Qualcomm glue ("qscratch") offset'),
    Item('I03', 'A16-SPECIFIC', U + 'usb-ports.c', 108, r'0x33313130',
         'GSNPSID must equal the A16 core id (IP family 0x3331, release 0x3130)'),
    Item('I04', 'A16-SPECIFIC', U + 'usb-ports.c', 109, r'!= 0x30', 'xHCI CAPLENGTH must be 0x30'),
    Item('I05', 'A16-SPECIFIC', U + 'usb-ports.c', 110, r'>> 24\) != 2', 'xHCI MaxPorts must be 2'),
    Item('I06', 'A16-SPECIFIC', U + 'usb-ports.h', 6, r'Q1N1_USB_PORTS 2', 'two controllers, sized into the shared state'),
    Item('I07', 'A16-SPECIFIC', U + 'usb-ebs.h', 9, r'USB_EBS_DWC3_BASE.*0x0a600000', 'USB0 core base (boot window, takeover)'),
    Item('I08', 'A16-SPECIFIC', U + 'usb-ebs.h', 10, r'USB_EBS_QSCRATCH_BASE.*0x0a6f8800', 'USB0 glue base'),
    Item('I09', 'FIRMWARE-COUPLED', U + 'usb-ebs.h', 11, r'UsbConfigDxe BIOS312',
         'RVA into the ASUS BIOS312 UsbConfigDxe image'),
    Item('I10', 'A16-SPECIFIC', U + 'usb1-device.c', 4, r'0x0a800000u', 'USB1 core base for the CDC fallback'),
    Item('I11', 'A16-SPECIFIC', U + 'usb1-device.c', 5, r'0x0a8f8800u', 'USB1 glue base for the CDC fallback'),
    Item('I12', 'A16-SPECIFIC', U + 'qcom-dwc3.c', 244, r'0x0a800000', 'device-mode serial string keyed on the USB1 base'),
    Item('I13', 'A16-SPECIFIC', U + 'main.c', 1182, r'USB_EBS_DWC3_BASE', 'bootinfo carries the A16 USB0 bases'),
    Item('I14', 'FIRMWARE-COUPLED', U + 'usb-role-change.c', 13, r'CONFIG_CODE_FNV',
         'fingerprint of the BIOS312 UsbConfigDxe code before any role call'),
    Item('I15', 'FIRMWARE-COUPLED', U + 'usb-role-change.c', 21, r'EXPECTED_USB0_BASE.*0x0a600000',
         'the firmware role-change path refuses any other base'),
    Item('I16', 'FIRMWARE-COUPLED', U + 'boot-window.c', 17, r'A16-Q1N1-BOOT',
         'the boot window rides the firmware USB Function driver'),
    Item('I17', 'FIRMWARE-COUPLED', U + 'ucsi-console.c', 3, r'PmicGlinkDxe', 'UCSI through the A16 firmware PMIC-GLINK driver'),
    Item('I18', 'A16-SPECIFIC', U + 'live-battery.h', 50, r'PMIC_RTR_SOCCP_APPS',
         'the GLINK client talks to the SoCCP channel; Hamoa uses PMIC_RTR_ADSP_APPS'),
    Item('I19', 'A16-SPECIFIC', U + 'smem.h', 41, r'0xffe00000', 'SMEM base for the GLINK transport'),
    Item('I20', 'A16-SPECIFIC', U + 'main.c', 1205, r'0xb0000000, 0x4c000000, 0xa0000000',
         'chainload stage addresses chosen for the A16 memory map'),
    Item('I21', 'A16-SPECIFIC', U + 'qcom-dwc3.c', 541, r'firmware owns PHY power, clocks',
         'q1n1 never powers or trains a PHY: firmware must have'),
    Item('I22', 'A16-SPECIFIC', U + 'usb-ports.c', 3, r'No PD commands',
         'no Type-C, PD, retimer or GPIO programming in q1n1'),
    Item('G01', 'GENERIC', U + 'xhci.c', 262, r'max_ports = \(hcs1 >> 24\)', 'port count read from HCSPARAMS1'),
    Item('G02', 'GENERIC', U + 'xhci.c', 263, r'context_size = .*\? 64 : 32', 'context size read from HCCPARAMS1'),
    Item('G03', 'GENERIC', U + 'xhci.c', 409, r'root hub port number',
         'devices are addressed on a root port only: no hub, route string or transaction translator'),
    Item('G04', 'GENERIC', U + 'xhci.h', 13, r'cache-coherent on this SoC', 'documented DMA assumption (Hamoa DT: dma-coherent)'),
    Item('G05', 'GENERIC', U + 'ncm.c', 89, r'CLASS_CDC_CONTROL && subclass == SUBCLASS_NCM',
         'the NCM function is found by class, not by vendor or product id'),
    Item('G06', 'GENERIC', U + 'usb-ports.c', 11, r'#define GCTL 0xc110', 'DWC3 global register offsets (Linux core.h:102,106)'),
    Item('G07', 'GENERIC', U + 'qcom-dwc3.c', 537, r'QSCRATCH_SS_PHY_CTRL', 'glue VBUS override, as Linux dwc3-qcom.c:119-127'),
    Item('G08', 'GENERIC', U + 'q1n1-proxy.c', 8, r'REQ_NOP', 'proxy wire format, transport independent'),
    Item('G09', 'GENERIC', U + 'main.c', 983, r'usb_ports = q1n1_usb_ports_init\(', 'the only caller of the port manager'),
    Item('G10', 'GENERIC', U + 'usb-ports.c', 17, r'HOST_DWELL_US 1500000u',
         'host, device and disconnect dwell times, tuned on the A16 with one Mac (policy, not hardware)'),
    Item('G11', 'GENERIC', U + 'usb-ports.c', 70, r'Do not change direction while xHCI might still own DMA',
         'the host is halted before any role change, so firmware-owned rings cannot be written after reuse'),
    Item('G12', 'GENERIC', 'tools/a16xhci.py', 331, r'def no_op',
         'the host-side driver checks DMA with a No-Op command; the C driver has no such checkpoint'),
    Item('G13', 'GENERIC', U + 'usb-probe.c', 2, r'Passive boot-services protocol inventory',
         'a passive UEFI protocol inventory (USB FUNCTION, USB2 HOST, SERIAL IO): no controller method, no MMIO'),
    Item('G14', 'GENERIC', 'Makefile', 185, r'usb\.o usb_dwc3\.o',
         "m1n1's Apple-silicon USB stack (src/usb.c, src/usb_dwc3.c) is built for the Apple target, not by platform/uefi"),
    Item('I23', 'FIRMWARE-COUPLED', U + 'main.c', 1161, r'Q1N1_BOOT_PROXY_NCM',
         'the NCM-only boot choice comes out of the A16 firmware boot window'),
    Item('I24', 'FIRMWARE-COUPLED', U + 'usb-typec-probe.c', 147, r'Raw orientation byte',
         'orientation is read out of the firmware driver memory by a diagnostic, not from a pin'),
    Item('N02', 'GENERIC', U + 'qcom-dwc3.c', 417, r'carkit/I2C',
         'the one token hit of the negative search: a comment about a USB event type'),
)
# Searched with a regex over the whole platform/uefi tree: nothing there programs a PHY, clock, GDSC, regulator,
# retimer, repeater, GPIO or I2C controller. The one allowed hit is a comment about a USB event type.
NEGATIVE_PATTERN = r'gpio|tlmm|\bi2c\b|retimer|ps883|repeater|\bgdsc\b|gcc_|rpmh|spmi|regulator|eusb2|\bqmp\b'
NEGATIVE_ALLOWED = {(U + 'qcom-dwc3.c', 417)}


def find_q1n1(explicit=None):
    """A q1n1 checkout: explicit, Q1N1_DIR, or the sibling `q1n1` of this tree."""
    for c in (explicit, os.environ.get('Q1N1_DIR'), ROOT.parent / 'q1n1'):
        if c and (Path(c) / 'platform' / 'uefi' / 'usb-ports.c').is_file():
            return Path(c)
    return None


class TreeSource:
    """Reads q1n1 files from a working tree. The inventory cites commit Q1N1_COMMIT, so a tree that carries the SP11
    patch (whose gated edits move lines) needs a reader of that commit instead; tests supply one when a .git exists."""

    def __init__(self, root):
        self.root = Path(root)

    def lines(self, rel):
        return (self.root / rel).read_text(encoding='utf-8', errors='replace').splitlines()

    def names(self, rel_dir):
        return sorted(p.name for p in (self.root / rel_dir).glob('*') if p.is_file())


def verify_inventory(source):
    """(item, ok, why) for every cited line. Raises nothing; absence is a result."""
    out = []
    cache = {}
    for item in INVENTORY:
        try:
            lines = cache.setdefault(item.path, source.lines(item.path))
        except (OSError, ValueError):
            out.append((item, False, 'file missing'))
            continue
        text = lines[item.line - 1] if 0 < item.line <= len(lines) else ''
        ok = re.search(item.pattern, text) is not None
        out.append((item, ok, '' if ok else f'line {item.line} reads {text.strip()[:70]!r}'))
    return out


def negative_hits(source):
    """Lines of platform/uefi that mention a PHY, clock, GDSC, regulator, retimer, repeater, GPIO or I2C controller."""
    hits = []
    for name in source.names('platform/uefi'):
        if not name.endswith(('.c', '.h', '.S')):
            continue
        rel = f'platform/uefi/{name}'
        for number, text in enumerate(source.lines(rel), 1):
            if re.search(NEGATIVE_PATTERN, text, re.I) and (rel, number) not in NEGATIVE_ALLOWED:
                hits.append((rel, number, text.strip()[:80]))
    return hits


# ---- command line ----

def _print_result(result):
    print(f'board {result.board_id} (status {result.board_status}); descriptor boot-ready: {result.boot_ready or "yes"}')
    for p in result.ports:
        if p.dwc3_base is None:
            print(f'  {p.name:10} base null, window null  [not established by the descriptor]')
            continue
        print(f'  {p.name:10} base {p.dwc3_base:#010x} window {p.window_size:#x} glue {p.qscratch_base:#010x} flags {p.flags:#x}')
        for key, fact in sorted(p.facts.items()):
            print(f'      {key:16} {fact.evidence:11} {fact.value}')
    if result.ok:
        print('table: OK (every value INFERRED from the pinned device tree via the descriptor; hardware NOT RUN)')
        print('core id: UNMEASURED (expect_gsnpsid 0, which q1n1 reads as "any DWC3 family"); emit-c refuses to write '
              'it unless --allow-unmeasured-id is given')
    for r in result.refusals:
        print(f'REFUSED {r.code}: {r.detail}')
    print('unknown (measure; never guessed):')
    for name, how in result.unknown:
        print(f'  {name}: {how}')
    if not result.ok:
        print('evidence needed: a descriptor that lists usb-1-ss0 and usb-1-ss1 with independent provenance; '
              'run `propose` for the exact change')


def main(argv):
    parser = argparse.ArgumentParser(prog='sp11_usb_ports.py', description=__doc__.split('\n\n')[0])
    sub = parser.add_subparsers(dest='command', required=True)
    t = sub.add_parser('table')
    t.add_argument('descriptor', nargs='?', default=str(DEFAULT_DESCRIPTOR))
    t.add_argument('--json', action='store_true')
    p = sub.add_parser('propose')
    p.add_argument('descriptor', nargs='?', default=str(DEFAULT_DESCRIPTOR))
    p.add_argument('--out')
    e = sub.add_parser('emit-c')
    e.add_argument('descriptor')
    e.add_argument('out')
    e.add_argument('--allow-unmeasured-id', action='store_true',
                   help='write expect_gsnpsid 0 (q1n1 then accepts any DWC3 core family); without it emit-c refuses '
                        'until the id is measured at X2')
    i = sub.add_parser('inventory')
    i.add_argument('--q1n1-dir')
    args = parser.parse_args(argv)

    if args.command == 'table':
        result = load_and_derive(args.descriptor)
        if args.json:
            print(json.dumps(result.to_json(), indent=2, sort_keys=True))
        else:
            _print_result(result)
        return 0 if result.ok else 1
    if args.command == 'propose':
        try:
            raw = json.loads(Path(args.descriptor).read_text(encoding='utf-8'))
            proposal = proposed_descriptor(raw)
            result = derive(bd.validate(proposal))
        except (OSError, ValueError, KeyError, TypeError, bd.BoardDescriptorError) as exc:
            print(f'REFUSED: cannot build a proposal from {args.descriptor}: {exc}', file=sys.stderr)
            return 2
        if not result.ok:
            print('REFUSED: the proposal itself does not derive: ' + '; '.join(r.code for r in result.refusals), file=sys.stderr)
            return 1
        text = json.dumps(proposal, indent=2) + '\n'
        if args.out:
            Path(args.out).write_text(text, encoding='utf-8')
        else:
            sys.stdout.write(text)
        return 0
    if args.command == 'emit-c':
        result = load_and_derive(args.descriptor)
        try:
            source = emit_c(result, Path(args.descriptor).name, args.allow_unmeasured_id)
        except UsbPortError as exc:
            print(f'REFUSED: {exc}', file=sys.stderr)
            return 1
        Path(args.out).write_text(source, encoding='utf-8')
        return 0
    q1n1 = find_q1n1(args.q1n1_dir)
    if q1n1 is None:
        print('SKIP: no q1n1 checkout (pass --q1n1-dir or set Q1N1_DIR)', file=sys.stderr)
        return 2
    source = TreeSource(q1n1)
    bad = 0
    for item, ok, why in verify_inventory(source):
        bad += not ok
        print(f'{"ok  " if ok else "FAIL"} {item.key} {item.klass:16} {item.path}:{item.line} {item.summary}' + (f' ({why})' if why else ''))
    for rel, number, text in negative_hits(source):
        bad += 1
        print(f'FAIL N01 unexpected PHY/clock/GPIO token at {rel}:{number}: {text}')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
