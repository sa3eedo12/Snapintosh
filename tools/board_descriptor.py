#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Board descriptor schema v1: load, validate, gate.

A descriptor is a JSON claim about one board's SoC, GIC, timer and physical
map. It is data for host tools and, packed by probes/boardinfo.py, for the
monitor (compat/board-info.h). Nothing here touches hardware.

Rules that exist for hardware safety rather than tidiness:

* Fail closed. Unknown keys, unknown versions, unknown SoCs and malformed
  numbers are refused, never ignored. An unmeasured value is an explicit null,
  and a null is never guessed or defaulted by any consumer.
* Every claim carries provenance (`source` plus an evidence label). A populated
  claim cannot be UNIMPLEMENTED or sourced "UNKNOWN"; a null claim must be
  exactly that, so the file states what is not known as plainly as what is.
* `validate()` accepts a structurally sound template (a descriptor whose
  `board.status` is UNIMPLEMENTED, or that still has nulls). Only
  `require_boot_ready()` decides whether it may describe a boot.
* The physical-address limit is `limits.ipa_bits` = 36, the monitor's stage-2
  geometry (probes/xnuboot/stage2.c, S2_TXSZ = 28). A window is the half-open
  range [base, base + size): it is refused when it starts at or above 1 << 36
  or ends beyond it, and accepted when its end is exactly 1 << 36 (its last
  byte is then (1 << 36) - 1). That is the rule the existing code applies:
  shadow/stage2.c refuses only hi > limit, probes/bootcontract.py only
  end > 1 << address_bits, probes/monitor_work.py only base + size > IPA_LIMIT.

Usage: python3 tools/board_descriptor.py check FILE... [--boot-ready]
"""
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

SCHEMA_VERSION = 1
EVIDENCE_LABELS = ('VERIFIED', 'OBSERVED', 'INFERRED', 'EXPERIMENTAL', 'UNIMPLEMENTED')
UNKNOWN_SOURCE = 'UNKNOWN'
MONITOR_IPA_BITS = 36
PAGE = 0x1000

TOP_KEYS = ('schema_version', 'board', 'soc', 'clusters', 'gic', 'smmu', 'timer', 'pcie',
            'ranges', 'devices', 'limits', 'provenance')

# SoC name -> (family, MIDR implementer byte). 0x51 is Qualcomm in the Arm ARM
# MIDR_EL1 implementer list; docs/FEASIBILITY.md records it for the A16.
KNOWN_SOCS = {
    'X1E80100': ('hamoa', 0x51),
    'X2E-96-100': ('glymur', 0x51),
}
# Ids carried in the packed blob (compat/board-info.h). 0 means unknown; an id
# is never reused or renumbered once assigned.
SOC_IDS = {'X1E80100': 1, 'X2E-96-100': 2}

# Closed vocabulary: the guest-DT generator, the descriptor and the packed
# bitmap must agree on names, so a typo cannot silently become "absent". The
# list index is the bit position in the blob; append only (schema minor bump).
DEVICE_VOCABULARY = ('gpu', 'gpu-gmu', 'display', 'uart-debug', 'usb-host',
                     'keyboard', 'touchpad', 'touchscreen', 'wifi', 'nvme')
DEVICE_STATES = ('present', 'absent', 'unknown')
RANGE_KINDS = ('dram', 'mmio')
SMMU_KINDS = ('smmu-v3', 'mmu-500')

# Bounds shared with the blob so a descriptor that validates always packs.
MAX_MIDR = 4
MAX_CPUS = 32
MAX_SMMU = 8
MAX_ECAM = 4
MAX_RANGES = 32

NAME_RE = re.compile(r'[a-z0-9][a-z0-9-]{0,14}\Z')
HEX_RE = re.compile(r'0x[0-9a-fA-F]{1,16}\Z')
MPIDR_AFFINITY = 0xff00ffffff        # Aff3:Aff2:Aff1:Aff0 (Arm ARM, MPIDR_EL1)
TIMER_PPI_KEYS = ('secure_phys', 'phys', 'virt', 'hyp')


class BoardDescriptorError(ValueError):
    """The descriptor is malformed, inconsistent or unsafe to trust."""


class BoardNotBootReady(BoardDescriptorError):
    """The descriptor is valid but cannot describe a boot (unknowns remain)."""


def _err(what, why):
    return BoardDescriptorError(f'{what}: {why}')


def parse_hex(value, what, bits=64):
    """A 0x-prefixed hex string of at most `bits` bits.

    Addresses are strings because JSON has no hex literal and a JSON float or
    a 2**64 number must never be silently rounded or wrapped.
    """
    if not isinstance(value, str):
        raise _err(what, f'expected a 0x-prefixed hex string, got {type(value).__name__}')
    if value.startswith('-'):
        raise _err(what, f'negative number {value!r}')
    if not HEX_RE.match(value):
        raise _err(what, f'not a 0x-prefixed hex number of at most 16 digits: {value!r}')
    number = int(value, 16)
    if number >> bits:
        raise _err(what, f'{value} does not fit in {bits} bits')
    return number


def parse_int(value, what, low, high):
    if type(value) is not int:    # excludes bool and float
        raise _err(what, f'expected an integer, got {value!r}')
    if not low <= value <= high:
        raise _err(what, f'{value} outside {low}..{high}')
    return value


def parse_name(value, what):
    if not isinstance(value, str) or not NAME_RE.match(value):
        raise _err(what, f'name must match {NAME_RE.pattern}: {value!r}')
    return value


def _object(value, what, keys, optional=()):
    if not isinstance(value, dict):
        raise _err(what, 'expected an object')
    unknown = sorted(set(value) - set(keys) - set(optional))
    if unknown:
        raise _err(what, f'unknown key(s) {unknown}; refusing rather than ignoring')
    missing = [k for k in keys if k not in value]
    if missing:
        raise _err(what, f'missing key(s) {missing}; write an explicit null for unknown values')
    return value


def _list(value, what, limit):
    if not isinstance(value, list):
        raise _err(what, 'expected a list')
    if len(value) > limit:
        raise _err(what, f'{len(value)} entries exceed the limit of {limit}')
    return value


@dataclass(frozen=True)
class Window:
    """A physical-address window the descriptor claims (end is exclusive)."""
    name: str
    kind: str          # 'dram', 'mmio'
    base: int
    size: int

    @property
    def end(self):
        return self.base + self.size


@dataclass(frozen=True)
class Board:
    board_id: str
    display_name: str
    status: str
    soc_name: object            # str | None
    soc_family: object          # str | None
    midr: object                # tuple[(value, mask)] | None
    cluster_count: object       # int | None
    cores_per_cluster: object   # int | None
    affinity_mask: object       # int | None
    cluster_mask: object        # int | None
    mpidrs: object              # tuple[int] | None
    gic_version: object         # 3 | 4 | None
    gicd: object                # (base, size) | None
    gicr: object                # (base, stride, size) | None
    its: object                 # None unknown | (False, 0, 0) | (True, base, size)
    smmus: object               # tuple[(name, kind, base, size)] | None
    timer_hz: object            # int | None
    timer_ppi: object           # (secure_phys, phys, virt, hyp) INTIDs | None
    ecam: object                # tuple[(segment, base, start_bus, end_bus)] | None
    ranges: object              # tuple[(name, kind, base|None, size|None, device|None)] | None
    devices: dict               # name -> (state, mmio, tuple(range names))
    ipa_bits: object            # 36 | None
    provenance: dict            # claim -> (source, evidence)

    @property
    def cpu_count(self):
        if self.cluster_count is None or self.cores_per_cluster is None:
            return None
        return self.cluster_count * self.cores_per_cluster


def _check_window(name, base, size, ipa_limit):
    """Alignment and limit rules every window obeys, DRAM and MMIO alike."""
    if size == 0:
        raise _err(name, 'zero-size window')
    if base & (PAGE - 1) or size & (PAGE - 1):
        raise _err(name, f'base {base:#x} and size {size:#x} must be multiples of '
                         f'{PAGE:#x} (the monitor maps stage 2 in 4 KiB pages)')
    # base + size > limit, without forming a sum that could wrap in a 64-bit
    # consumer (the C validator in compat/board-info.c uses the same form). The
    # end is exclusive, so it may equal the limit; compare shadow/stage2.c.
    if base >= ipa_limit or size > ipa_limit - base:
        raise _err(name, f'[{base:#x}, {base:#x}+{size:#x}) extends beyond 1<<{MONITOR_IPA_BITS}')


def _parse_midr(value):
    if value is None:
        return None
    pairs = []
    for i, item in enumerate(_list(value, 'soc.midr', MAX_MIDR)):
        what = f'soc.midr[{i}]'
        item = _object(item, what, ('value', 'mask'))
        mid = parse_hex(item['value'], f'{what}.value', 32)
        mask = parse_hex(item['mask'], f'{what}.mask', 32)
        if mid & ~mask:
            raise _err(what, f'value {mid:#x} has bits outside mask {mask:#x}; it can never match')
        # A mask that ignores the implementer or part number would accept any
        # core: "unknown MIDR" must not be expressible as a pattern.
        if mask & 0xff00fff0 != 0xff00fff0:
            raise _err(what, f'mask {mask:#x} must cover the implementer (31:24) and part number (15:4)')
        if (mid >> 16) & 0xf != 0xf:
            raise _err(what, f'architecture field {(mid >> 16) & 0xf:#x}: only 0xf (Armv8+, ID-register defined) is accepted')
        pairs.append((mid, mask))
    if not pairs:
        raise _err('soc.midr', 'an empty set matches nothing; use null for unknown')
    if len(set(pairs)) != len(pairs):
        raise _err('soc.midr', 'duplicate entries')
    return tuple(pairs)


def _parse_clusters(section):
    section = _object(section, 'clusters', ('count', 'cores_per_cluster', 'mpidr', 'mpidrs'))
    count = None if section['count'] is None else parse_int(section['count'], 'clusters.count', 1, 8)
    cores = (None if section['cores_per_cluster'] is None
             else parse_int(section['cores_per_cluster'], 'clusters.cores_per_cluster', 1, 16))
    if count and cores and count * cores > MAX_CPUS:
        raise _err('clusters', f'{count * cores} cores exceed the limit of {MAX_CPUS}')
    affinity = cluster = None
    if section['mpidr'] is not None:
        layout = _object(section['mpidr'], 'clusters.mpidr', ('affinity_mask', 'cluster_mask'))
        affinity = parse_hex(layout['affinity_mask'], 'clusters.mpidr.affinity_mask', 40)
        cluster = parse_hex(layout['cluster_mask'], 'clusters.mpidr.cluster_mask', 40)
        if not affinity or affinity & ~MPIDR_AFFINITY:
            raise _err('clusters.mpidr.affinity_mask', f'{affinity:#x} must be a nonzero subset of {MPIDR_AFFINITY:#x}')
        if not cluster or cluster & ~affinity:
            raise _err('clusters.mpidr.cluster_mask', f'{cluster:#x} must be a nonzero subset of the affinity mask')
    mpidrs = None
    if section['mpidrs'] is not None:
        if affinity is None:
            raise _err('clusters.mpidrs', 'needs clusters.mpidr (the affinity mask that gives the values meaning)')
        mpidrs = tuple(parse_hex(v, f'clusters.mpidrs[{i}]', 40)
                       for i, v in enumerate(_list(section['mpidrs'], 'clusters.mpidrs', MAX_CPUS)))
        if not mpidrs:
            raise _err('clusters.mpidrs', 'empty; use null for unknown')
        if len(set(mpidrs)) != len(mpidrs):
            raise _err('clusters.mpidrs', 'duplicate MPIDR')
        if any(m & ~affinity for m in mpidrs):
            raise _err('clusters.mpidrs', 'a value has bits outside the affinity mask')
        if count and cores:
            if len(mpidrs) != count * cores:
                raise _err('clusters.mpidrs', f'{len(mpidrs)} entries, expected {count}x{cores}')
            groups = {}
            for m in mpidrs:
                groups[m & cluster] = groups.get(m & cluster, 0) + 1
            if len(groups) != count or set(groups.values()) != {cores}:
                raise _err('clusters.mpidrs', 'cluster mask does not split the cores into the stated clusters')
    return count, cores, affinity, cluster, mpidrs


def _parse_gic(section):
    section = _object(section, 'gic', ('version', 'gicd', 'gicr', 'its'))
    version = None if section['version'] is None else parse_int(section['version'], 'gic.version', 3, 4)
    gicd = gicr = its = None
    if section['gicd'] is not None:
        item = _object(section['gicd'], 'gic.gicd', ('base', 'size'))
        gicd = (parse_hex(item['base'], 'gic.gicd.base'), parse_hex(item['size'], 'gic.gicd.size'))
        if gicd[1] < 0x10000 or gicd[1] & 0xffff or gicd[0] & 0xffff:
            raise _err('gic.gicd', 'the distributor frame is 64 KiB: base and size must be multiples of 0x10000, size >= 0x10000')
    if section['gicr'] is not None:
        item = _object(section['gicr'], 'gic.gicr', ('base', 'stride', 'size'))
        gicr = (parse_hex(item['base'], 'gic.gicr.base'), parse_hex(item['stride'], 'gic.gicr.stride'),
                parse_hex(item['size'], 'gic.gicr.size'))
        base, stride, size = gicr
        # GICv3 redistributors are two 64 KiB frames, GICv4 adds two more. The
        # stride may be padded larger (redistributor-stride in the Linux DT)
        # but never smaller than the frames the version defines.
        minimum = 0x40000 if version == 4 else 0x20000
        if stride < minimum or stride & 0xffff:
            raise _err('gic.gicr.stride', f'{stride:#x} must be a multiple of 0x10000 and at least {minimum:#x}'
                       + (' for GICv4' if version == 4 else ' (GICv3 or version unknown)'))
        if not size or size % stride or base & 0xffff:
            raise _err('gic.gicr', f'size {size:#x} must be a nonzero multiple of the stride {stride:#x}; base 64 KiB aligned')
        if size // stride > MAX_CPUS:
            raise _err('gic.gicr', f'{size // stride} redistributors exceed the limit of {MAX_CPUS}')
    if section['its'] is not None:
        item = section['its']
        if isinstance(item, dict) and item.get('present') is False:
            its = (False, 0, 0)
            _object(item, 'gic.its', ('present',))
        else:
            item = _object(item, 'gic.its', ('present', 'base', 'size'))
            if item['present'] is not True:
                raise _err('gic.its', 'present must be true or false')
            its = (True, parse_hex(item['base'], 'gic.its.base'), parse_hex(item['size'], 'gic.its.size'))
            if its[1] & 0xffff or its[2] & 0xffff or not its[2]:
                raise _err('gic.its', 'ITS frames are 64 KiB multiples')
    return version, gicd, gicr, its


def _parse_smmu(value):
    if value is None:
        return None
    entries, names = [], set()
    for i, item in enumerate(_list(value, 'smmu', MAX_SMMU)):
        what = f'smmu[{i}]'
        item = _object(item, what, ('name', 'kind', 'base', 'size'))
        name = parse_name(item['name'], f'{what}.name')
        if name in names:
            raise _err(what, f'duplicate SMMU name {name!r}')
        names.add(name)
        if item['kind'] not in SMMU_KINDS:
            raise _err(what, f'unknown SMMU kind {item["kind"]!r}; known: {SMMU_KINDS}')
        entries.append((name, item['kind'], parse_hex(item['base'], f'{what}.base'),
                        parse_hex(item['size'], f'{what}.size')))
    return tuple(entries)


def _parse_timer(section):
    section = _object(section, 'timer', ('frequency_hz', 'ppi'))
    hz = (None if section['frequency_hz'] is None
          else parse_int(section['frequency_hz'], 'timer.frequency_hz', 1_000_000, 1_000_000_000))
    ppi = None
    if section['ppi'] is not None:
        item = _object(section['ppi'], 'timer.ppi', TIMER_PPI_KEYS)
        # PPIs are INTIDs 16..31 (a Linux DT GIC_PPI n is INTID 16 + n). One
        # entry may be null when only some interrupts are recorded (the A16
        # code knows the hypervisor timer's INTID 26 and nothing else).
        ppi = tuple(None if item[k] is None else parse_int(item[k], f'timer.ppi.{k}', 16, 31)
                    for k in TIMER_PPI_KEYS)
        known = [p for p in ppi if p is not None]
        if not known:
            raise _err('timer.ppi', 'all four entries are null; use null for the whole section')
        if len(set(known)) != len(known):
            raise _err('timer.ppi', 'the timer interrupts must be distinct INTIDs')
    return hz, ppi


def _parse_ecam(section):
    section = _object(section, 'pcie', ('ecam',))
    if section['ecam'] is None:
        return None
    entries, segments = [], set()
    for i, item in enumerate(_list(section['ecam'], 'pcie.ecam', MAX_ECAM)):
        what = f'pcie.ecam[{i}]'
        item = _object(item, what, ('segment', 'base', 'start_bus', 'end_bus'))
        segment = parse_int(item['segment'], f'{what}.segment', 0, 0xffff)
        base = parse_hex(item['base'], f'{what}.base')
        start = parse_int(item['start_bus'], f'{what}.start_bus', 0, 255)
        end = parse_int(item['end_bus'], f'{what}.end_bus', 0, 255)
        if end < start:
            raise _err(what, f'end_bus {end} before start_bus {start}')
        if base & 0xfffff:
            raise _err(what, f'ECAM base {base:#x} must be 1 MiB aligned (one bus is 1 MiB)')
        if segment in segments:
            raise _err(what, f'duplicate PCI segment {segment}')
        segments.add(segment)
        entries.append((segment, base, start, end))
    return tuple(entries)


def _parse_ranges(value):
    if value is None:
        return None
    entries, names = [], set()
    for i, item in enumerate(_list(value, 'ranges', MAX_RANGES)):
        what = f'ranges[{i}]'
        item = _object(item, what, ('name', 'kind', 'base', 'size', 'device'))
        name = parse_name(item['name'], f'{what}.name')
        if name in names:
            raise _err(what, f'duplicate range name {name!r}')
        names.add(name)
        if item['kind'] not in RANGE_KINDS:
            raise _err(what, f'unknown range kind {item["kind"]!r}; known: {RANGE_KINDS}')
        base, size = item['base'], item['size']
        if (base is None) != (size is None):
            raise _err(what, 'base and size must both be a number or both null')
        if base is None:
            if item['kind'] != 'dram':
                raise _err(what, 'only a dram range may have unknown geometry')
        else:
            base, size = parse_hex(base, f'{what}.base'), parse_hex(size, f'{what}.size')
        device = item['device']
        if device is not None and device not in DEVICE_VOCABULARY:
            raise _err(what, f'unknown device {device!r}; vocabulary: {DEVICE_VOCABULARY}')
        if device is not None and item['kind'] == 'dram':
            raise _err(what, 'a dram range belongs to no device')
        entries.append((name, item['kind'], base, size, device))
    return tuple(entries)


def _parse_devices(section, ranges):
    if not isinstance(section, dict):
        raise _err('devices', 'expected an object')
    range_names = {r[0] for r in ranges or ()}
    range_device = {r[0]: r[4] for r in ranges or ()}
    devices = {}
    for name, item in section.items():
        what = f'devices.{name}'
        if name not in DEVICE_VOCABULARY:
            raise _err(what, f'unknown device name; vocabulary: {DEVICE_VOCABULARY}')
        item = _object(item, what, ('state', 'mmio', 'ranges'))
        state, mmio = item['state'], item['mmio']
        if state not in DEVICE_STATES:
            raise _err(what, f'state must be one of {DEVICE_STATES}, got {state!r}')
        if mmio is not None and type(mmio) is not bool:
            raise _err(what, f'mmio must be true, false or null, got {mmio!r}')
        owned = tuple(parse_name(r, f'{what}.ranges') for r in _list(item['ranges'], f'{what}.ranges', MAX_RANGES))
        if len(set(owned)) != len(owned):
            raise _err(what, 'duplicate range reference')
        missing = [r for r in owned if r not in range_names]
        if missing:
            raise _err(what, f'refers to range(s) {missing} that are not defined')
        for r in owned:
            # Both ends of the link must agree, or a window could be mapped
            # for one device while another believes it owns it.
            if range_device[r] != name:
                raise _err(what, f'lists range {r!r}, which belongs to {range_device[r]!r}')
        # `mmio` says whether the guest reaches the device through windows of
        # its own. A bus-attached device (Wi-Fi behind PCIe config space, a
        # keyboard behind a controller another device owns) is mmio=false and
        # needs none; a device that needs one but has none is unmappable.
        if state == 'present':
            if mmio is None:
                raise _err(what, 'a present device must state whether it needs its own range (mmio true or false)')
            if mmio and not owned:
                raise _err(what, 'present mmio device has no range')
        if state == 'absent' and (owned or mmio):
            raise _err(what, 'absent device must not own a range')
        if mmio is False and owned:
            raise _err(what, 'mmio=false device must not list ranges')
        devices[name] = (state, mmio, owned)
    missing = [n for n in DEVICE_VOCABULARY if n not in devices]
    if missing:
        raise _err('devices', f'no entry for {missing}; list every device, writing state "unknown" where it is not known')
    for name, kind, base, size, device in ranges or ():
        if device is None:
            continue
        state, mmio, owned = devices[device]
        if state == 'absent':
            raise _err(f'ranges.{name}', f'refers to device {device!r}, which is absent; absent devices are withdrawn, never mapped')
        if name not in owned:
            raise _err(f'ranges.{name}', f'device {device!r} does not list this range')
    return devices


def windows(board):
    """Every physical window the descriptor claims, GIC/SMMU/ECAM included."""
    found = []
    if board.gicd:
        found.append(Window('gic.gicd', 'mmio', *board.gicd))
    if board.gicr:
        found.append(Window('gic.gicr', 'mmio', board.gicr[0], board.gicr[2]))
    if board.its and board.its[0]:
        found.append(Window('gic.its', 'mmio', board.its[1], board.its[2]))
    for name, kind, base, size in board.smmus or ():
        found.append(Window(f'smmu.{name}', 'mmio', base, size))
    for segment, base, start, end in board.ecam or ():
        found.append(Window(f'pcie.ecam{segment}', 'mmio', base, (end + 1) << 20))
    for name, kind, base, size, device in board.ranges or ():
        if base is not None:
            found.append(Window(f'ranges.{name}', kind, base, size))
    return found


def _claims(board):
    """claim name -> populated?  Every key needs a provenance entry."""
    claims = {'board': True,
              'soc.name': board.soc_name is not None, 'soc.family': board.soc_family is not None,
              'soc.midr': board.midr is not None,
              'clusters.count': board.cluster_count is not None,
              'clusters.cores_per_cluster': board.cores_per_cluster is not None,
              'clusters.mpidr': board.affinity_mask is not None, 'clusters.mpidrs': board.mpidrs is not None,
              'gic.version': board.gic_version is not None, 'gic.gicd': board.gicd is not None,
              'gic.gicr': board.gicr is not None, 'gic.its': board.its is not None,
              'timer.frequency_hz': board.timer_hz is not None, 'timer.ppi': board.timer_ppi is not None,
              'pcie.ecam': board.ecam is not None, 'limits.ipa_bits': board.ipa_bits is not None}
    for container, entries in (('smmu', board.smmus), ('ranges', board.ranges)):
        if entries is None:
            claims[container] = False        # the whole list is unknown
        elif not entries:
            claims[container] = True         # an explicit empty list claims "none"
        for entry in entries or ():
            claims[f'{container}.{entry[0]}'] = entry[2] is not None
    for name, (state, mmio, owned) in board.devices.items():
        claims[f'devices.{name}'] = state != 'unknown'
    return claims


def _check_text(what, text, limit=600):
    if not isinstance(text, str) or not text.strip() or len(text) > limit:
        raise _err(what, f'must be a non-empty string of at most {limit} characters')
    if re.search(r'[\x00-\x1f]', text):
        raise _err(what, 'contains control characters')
    # Provenance names a repo path or a public document, never a machine path.
    if text.startswith(('/', '~')) or re.search(r'(^|[\s(])(/(?:Users|home)/|[A-Za-z]:\\)', text):
        raise _err(what, 'absolute or machine-specific path; cite a repo-relative path or a public document')


def _parse_provenance(section, board_without_provenance):
    if not isinstance(section, dict):
        raise _err('provenance', 'expected an object')
    parsed = {}
    for key, item in section.items():
        item = _object(item, f'provenance.{key}', ('source', 'evidence'), optional=('note',))
        evidence = item['evidence']
        if evidence not in EVIDENCE_LABELS:
            raise _err(f'provenance.{key}.evidence', f'{evidence!r} is not one of {EVIDENCE_LABELS}')
        _check_text(f'provenance.{key}.source', item['source'])
        if 'note' in item:
            _check_text(f'provenance.{key}.note', item['note'])
        parsed[key] = (item['source'], evidence)
    claims = _claims(board_without_provenance)
    stale = sorted(set(parsed) - set(claims))
    if stale:
        raise _err('provenance', f'entries for unknown claim(s) {stale}')
    for key, populated in claims.items():
        if key not in parsed:
            kind = 'populated' if populated else 'unknown'
            raise _err('provenance', f'no entry for {kind} field {key!r}; every field states where it came from')
        source, evidence = parsed[key]
        if populated:
            if evidence == 'UNIMPLEMENTED':
                raise _err(f'provenance.{key}', 'a populated field cannot be UNIMPLEMENTED')
            if source.strip().upper() == UNKNOWN_SOURCE:
                raise _err(f'provenance.{key}', 'a populated field needs a real source, not UNKNOWN')
        else:
            if evidence != 'UNIMPLEMENTED' or source != UNKNOWN_SOURCE:
                raise _err(f'provenance.{key}', 'an unknown field must be recorded as source "UNKNOWN" with evidence UNIMPLEMENTED')
    return parsed


def validate(desc):
    """Validate a parsed descriptor; return a normalized Board or raise."""
    desc = _object(desc, 'descriptor', TOP_KEYS)
    if type(desc['schema_version']) is not int or desc['schema_version'] != SCHEMA_VERSION:
        raise _err('schema_version', f'{desc["schema_version"]!r} is not supported (this reader: {SCHEMA_VERSION})')

    ident = _object(desc['board'], 'board', ('id', 'name', 'status'))
    board_id = parse_name(ident['id'], 'board.id')
    display = ident['name']
    if not isinstance(display, str) or not display.strip() or len(display) > 100:
        raise _err('board.name', 'must be a non-empty string of at most 100 characters')
    if ident['status'] not in EVIDENCE_LABELS:
        raise _err('board.status', f'{ident["status"]!r} is not one of {EVIDENCE_LABELS}')

    soc = _object(desc['soc'], 'soc', ('name', 'family', 'midr'))
    name, family = soc['name'], soc['family']
    if name is not None and (not isinstance(name, str) or name not in KNOWN_SOCS):
        raise _err('soc.name', f'unknown SoC {name!r}; known: {sorted(KNOWN_SOCS)}')
    if family is not None:
        if name is None or family != KNOWN_SOCS[name][0]:
            raise _err('soc.family', f'{family!r} does not belong to SoC {name!r}')
    midr = _parse_midr(soc['midr'])
    if midr is not None:
        if name is None:
            raise _err('soc.midr', 'a MIDR set is meaningless without a known SoC')
        for value, mask in midr:
            if value >> 24 != KNOWN_SOCS[name][1]:
                raise _err('soc.midr', f'implementer {value >> 24:#x} is not the implementer known for {name}')

    count, cores, affinity, cluster, mpidrs = _parse_clusters(desc['clusters'])
    version, gicd, gicr, its = _parse_gic(desc['gic'])
    smmus = _parse_smmu(desc['smmu'])
    hz, ppi = _parse_timer(desc['timer'])
    ecam = _parse_ecam(desc['pcie'])
    ranges = _parse_ranges(desc['ranges'])
    devices = _parse_devices(desc['devices'], ranges)

    limits = _object(desc['limits'], 'limits', ('ipa_bits',))
    ipa = limits['ipa_bits']
    if ipa is not None and (type(ipa) is not int or ipa != MONITOR_IPA_BITS):
        raise _err('limits.ipa_bits', f'{ipa!r}: the monitor\'s stage 2 is {MONITOR_IPA_BITS}-bit (S2_TXSZ = 28)')
    limit = 1 << (ipa or MONITOR_IPA_BITS)

    if gicr and count and cores and gicr[2] // gicr[1] < count * cores:
        raise _err('gic.gicr', f'{gicr[2] // gicr[1]} redistributors cannot serve {count * cores} cores')

    board = Board(board_id, display, ident['status'], name, family, midr, count, cores, affinity, cluster,
                  mpidrs, version, gicd, gicr, its, smmus, hz, ppi, ecam, ranges, devices, ipa, {})

    seen = []
    for window in windows(board):
        _check_window(window.name, window.base, window.size, limit)
        for other in seen:
            if window.base < other.end and other.base < window.end:
                raise _err(window.name, f'overlaps {other.name}'
                           + (' (DRAM against MMIO)' if {window.kind, other.kind} == {'dram', 'mmio'} else ''))
        seen.append(window)

    provenance = _parse_provenance(desc['provenance'], board)
    return Board(**{**board.__dict__, 'provenance': provenance})


def withdrawn_devices(desc):
    """Names the guest device tree must omit entirely (state `absent`).

    Takes a Board or a raw descriptor dict (validated first, so an unchecked
    file cannot silently yield an empty list). A device that is merely
    `unknown` is not withdrawn: it is unresolved, and require_boot_ready()
    refuses to proceed until it is measured.
    """
    board = desc if isinstance(desc, Board) else validate(desc)
    return tuple(sorted(n for n, (state, _, _) in board.devices.items() if state == 'absent'))


def unknown_devices(board):
    return tuple(sorted(n for n, (state, _, _) in board.devices.items() if state == 'unknown'))


def require_boot_ready(board):
    """Raise BoardNotBootReady unless every field a boot depends on is known."""
    problems = []
    if board.status == 'UNIMPLEMENTED':
        problems.append('board.status is UNIMPLEMENTED: a template, not a boot profile')
    for field, value in (('soc.name', board.soc_name), ('soc.family', board.soc_family), ('soc.midr', board.midr),
                         ('clusters.count', board.cluster_count),
                         ('clusters.cores_per_cluster', board.cores_per_cluster),
                         ('clusters.mpidr', board.affinity_mask), ('clusters.mpidrs', board.mpidrs),
                         ('gic.version', board.gic_version), ('gic.gicd', board.gicd), ('gic.gicr', board.gicr),
                         ('gic.its', board.its), ('timer.frequency_hz', board.timer_hz),
                         ('timer.ppi', board.timer_ppi), ('smmu', board.smmus), ('pcie.ecam', board.ecam),
                         ('ranges', board.ranges), ('limits.ipa_bits', board.ipa_bits)):
        if value is None:
            problems.append(f'{field} is unknown (null)')
    if board.timer_ppi is not None and None in board.timer_ppi:
        problems.append('timer.ppi has an unrecorded interrupt')
    drams = [r for r in board.ranges or () if r[1] == 'dram']
    if board.ranges is not None and not any(r[2] is not None for r in drams):
        problems.append('no DRAM window with known geometry')
    for r in drams:
        if r[2] is None:
            problems.append(f'ranges.{r[0]} geometry is unknown (null)')
    unresolved = unknown_devices(board)
    if unresolved:
        problems.append(f'device state unknown for {list(unresolved)}')
    placeholders = sorted(k for k, (_, ev) in board.provenance.items() if ev == 'UNIMPLEMENTED')
    if placeholders and not problems:
        # Defence in depth: provenance says something is unmeasured that the
        # field checks above did not catch.
        problems.append(f'provenance still UNIMPLEMENTED for {placeholders}')
    if problems:
        raise BoardNotBootReady('descriptor is not boot-ready: ' + '; '.join(problems))
    return board


def _no_duplicate_keys(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise BoardDescriptorError(f'duplicate JSON key {key!r}')
        out[key] = value
    return out


def _reject_constant(token):
    raise BoardDescriptorError(f'non-finite number {token} in JSON')


def loads(text):
    try:
        raw = json.loads(text, object_pairs_hook=_no_duplicate_keys, parse_constant=_reject_constant)
    except BoardDescriptorError:
        raise
    except (ValueError, RecursionError) as exc:
        # ValueError covers malformed JSON and an integer literal beyond the
        # interpreter's digit limit; RecursionError covers absurd nesting.
        raise BoardDescriptorError(f'invalid JSON: {type(exc).__name__}: {str(exc)[:120]}') from exc
    return validate(raw)


def load(path):
    try:
        text = Path(path).read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError) as exc:
        raise BoardDescriptorError(f'cannot read {path}: {type(exc).__name__}') from exc
    return loads(text)


def main(argv):
    args = [a for a in argv if not a.startswith('--')]
    if not args or args[0] != 'check' or len(args) < 2 or set(argv) - set(args) - {'--boot-ready'}:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    failed = False
    for path in args[1:]:
        try:
            board = load(path)
            if '--boot-ready' in argv:
                require_boot_ready(board)
            print(f'{path}: OK ({board.board_id}, status {board.status})')
        except BoardDescriptorError as exc:
            failed = True
            print(f'{path}: REFUSED: {exc}')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
