# SPDX-License-Identifier: MIT
"""Board-info blob, ABI 1.x: pack, decode and validate (host half of compat/board-info.h and board-info.c).

    python3 probes/boardinfo.py pack DESCRIPTOR.json -o BLOB [--force]
    python3 probes/boardinfo.py unpack BLOB          (inspection view as JSON; exit 1 if the blob is refused)
    python3 probes/boardinfo.py check BLOB [--boot-ready]

The blob is the compact form of a board descriptor (tools/board_descriptor.py). The monitor is freestanding C and
reads this, not JSON; the layout, version policy and unknown-value rules are documented in compat/board-info.h and
are repeated here only as tables that tools/test-board-info.py compares with the C unit byte for byte.

check() is a second, independent implementation of board_info_validate(): the same checks in the same order, so a
blob that is wrong in several ways reports the same error from both. It is not a wrapper around the C code and
does not execute it. A packer that emits a blob its own validator refuses raises, so a descriptor that validates
can never silently produce a blob the monitor will reject.

Packing is lossy only toward "unknown": a group is emitted as known only when every field it contains is known
and its prerequisites are (a MIDR set needs the SoC; MPIDR values need the affinity layout). A group that cannot
be emitted as known fails the boot-readiness gate instead of carrying a half-recorded value.

Provenance and evidence labels per claim stay in the JSON descriptor; only the board-level status label travels.
Evidence: EXPERIMENTAL (host tests only, never run on hardware).
"""
import argparse
import json
import re
import struct
import sys
import zlib
from pathlib import Path

_TOOLS = str(Path(__file__).resolve().parents[1] / 'tools')
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)
import board_descriptor as bd  # noqa: E402

MAGIC = b'SNAPBRDI'
ABI_MAJOR = 1
ABI_MINOR = 0
V1_SIZE = 2168
MAX_LENGTH = 4096
HEADER_SIZE = 32
FLAGS_REQUIRED_KNOWN = 0
FLAGS_OPTIONAL_KNOWN = 0
NAME_LEN = 16
DEVICE_NONE = 0xff
IPA_BITS = bd.MONITOR_IPA_BITS
IPA_LIMIT = 1 << IPA_BITS
PAGE = bd.PAGE
MIDR_PINNED_MASK = 0xff00fff0
GIC_FRAME = 0x10000
GICR_STRIDE_V3 = 0x20000
GICR_STRIDE_V4 = 0x40000
ECAM_BUS_SHIFT = 20
SOC_COUNT = max(bd.SOC_IDS.values()) + 1
DEV_COUNT = len(bd.DEVICE_VOCABULARY)
WINDOW_SLOTS = 3 + bd.MAX_SMMU + bd.MAX_ECAM + bd.MAX_RANGES

OK = 0
ERR_NULL, ERR_ALIGN, ERR_TRUNCATED, ERR_MAGIC, ERR_MAJOR, ERR_LENGTH, ERR_CRC = 1, 2, 3, 4, 5, 6, 7
ERR_REQUIRED_FLAGS, ERR_OPTIONAL_FLAGS, ERR_COUNT, ERR_RESERVED, ERR_VALUE = 8, 9, 10, 11, 12
ERR_CONSISTENCY, ERR_WINDOW, ERR_OVERLAP = 13, 14, 15

# Short fixed descriptions, identical to board_info_strerror() in compat/board-info.c.
STRERROR = {
    OK: 'ok',
    ERR_NULL: 'null pointer',
    ERR_ALIGN: 'blob not 8-byte aligned',
    ERR_TRUNCATED: 'blob shorter than its header or total_length',
    ERR_MAGIC: 'bad magic',
    ERR_MAJOR: 'unknown ABI major',
    ERR_LENGTH: 'total_length out of bounds',
    ERR_CRC: 'CRC mismatch',
    ERR_REQUIRED_FLAGS: 'unknown required flag',
    ERR_OPTIONAL_FLAGS: 'unknown optional flag at this minor',
    ERR_COUNT: 'array count above its bound',
    ERR_RESERVED: 'nonzero reserved or unknown-group data',
    ERR_VALUE: 'field outside its legal set',
    ERR_CONSISTENCY: 'contradicting fields',
    ERR_WINDOW: 'window empty, unaligned or beyond 1<<36',
    ERR_OVERLAP: 'overlapping windows',
}
UNKNOWN_ERROR = 'unknown error code'
ERROR_NAMES = {code: ('OK' if code == OK else f'ERR_{name}') for code, name in (
    (OK, ''), (ERR_NULL, 'NULL'), (ERR_ALIGN, 'ALIGN'), (ERR_TRUNCATED, 'TRUNCATED'), (ERR_MAGIC, 'MAGIC'),
    (ERR_MAJOR, 'MAJOR'), (ERR_LENGTH, 'LENGTH'), (ERR_CRC, 'CRC'), (ERR_REQUIRED_FLAGS, 'REQUIRED_FLAGS'),
    (ERR_OPTIONAL_FLAGS, 'OPTIONAL_FLAGS'), (ERR_COUNT, 'COUNT'), (ERR_RESERVED, 'RESERVED'),
    (ERR_VALUE, 'VALUE'), (ERR_CONSISTENCY, 'CONSISTENCY'), (ERR_WINDOW, 'WINDOW'), (ERR_OVERLAP, 'OVERLAP'))}

KNOWN = {name: 1 << bit for bit, name in enumerate((
    'SOC', 'MIDR', 'COUNTS', 'AFFINITY', 'MPIDRS', 'GIC_VERSION', 'GICD', 'GICR', 'ITS', 'SMMU', 'TIMER_HZ',
    'TIMER_PPI', 'ECAM', 'RANGES', 'IPA'))}
KNOWN_ALL = (1 << len(KNOWN)) - 1
UNREADY = {name: 1 << bit for bit, name in enumerate((
    'STATUS', 'SOC', 'MIDR', 'COUNTS', 'AFFINITY', 'MPIDRS', 'GIC_VERSION', 'GICD', 'GICR', 'ITS', 'SMMU',
    'TIMER_HZ', 'TIMER_PPI', 'ECAM', 'RANGES', 'DEVICES', 'IPA'))}

ECAM = struct.Struct('<QHBBI')
SMMU = struct.Struct('<16sQQB7s')
RANGE = struct.Struct('<16sQQBBB5s')
if (ECAM.size, SMMU.size, RANGE.size) != (16, 40, 40):
    raise ImportError('board-info sub-struct sizes drifted from compat/board-info.h')

# Offsets of struct board_info, asserted in compat/board-info.h and compared with the C unit by the tests.
OFFSETS = {
    'abi_major': 8, 'abi_minor': 12, 'total_length': 16, 'crc32': 20, 'flags_required': 24, 'flags_optional': 28,
    'status': 32, 'soc_id': 36, 'board_id': 40, 'known': 56, 'reserved0': 60,
    'midr_count': 64, 'reserved1': 68, 'midr_value': 72, 'midr_mask': 88,
    'cluster_count': 104, 'cores_per_cluster': 108, 'affinity_mask': 112, 'cluster_mask': 120,
    'mpidr_count': 128, 'reserved2': 132, 'mpidr': 136,
    'gic_version': 392, 'its_present': 396, 'gicd_base': 400, 'gicd_size': 408, 'gicr_base': 416,
    'gicr_stride': 424, 'gicr_size': 432, 'its_base': 440, 'its_size': 448,
    'timer_hz': 456, 'timer_ppi': 460, 'ecam_count': 464, 'reserved3': 468, 'ecam': 472,
    'smmu_count': 536, 'reserved4': 540, 'smmu': 544, 'range_count': 864, 'reserved5': 868, 'range': 872,
    'device_present': 2152, 'device_absent': 2156, 'device_mmio': 2160, 'ipa_bits': 2164,
}
_U32 = ('abi_major', 'abi_minor', 'total_length', 'crc32', 'flags_required', 'flags_optional', 'status', 'soc_id',
        'known', 'reserved0', 'midr_count', 'reserved1', 'cluster_count', 'cores_per_cluster', 'mpidr_count',
        'reserved2', 'gic_version', 'its_present', 'timer_hz', 'ecam_count', 'reserved3', 'smmu_count',
        'reserved4', 'range_count', 'reserved5', 'device_present', 'device_absent', 'device_mmio', 'ipa_bits')
_U64 = ('affinity_mask', 'cluster_mask', 'gicd_base', 'gicd_size', 'gicr_base', 'gicr_stride', 'gicr_size',
        'its_base', 'its_size')
_NAME_RE = re.compile(rb'[a-z0-9][a-z0-9-]{0,14}\x00+')


class BoardInfoError(ValueError):
    """A blob or a descriptor that cannot be turned into one; `code` is the shared error number."""

    def __init__(self, code, message=None):
        super().__init__(message or f'{ERROR_NAMES.get(code, code)}: {STRERROR.get(code, UNKNOWN_ERROR)}')
        self.code = code


def strerror(code):
    return STRERROR.get(code, UNKNOWN_ERROR)


def crc32_of(blob, total_length=None):
    """The ABI's CRC: zlib CRC-32 of the first total_length bytes with the crc32 field read as zero."""
    body = bytearray(bytes(blob)[:len(blob) if total_length is None else total_length])
    body[OFFSETS['crc32']:OFFSETS['crc32'] + 4] = bytes(4)
    return zlib.crc32(bytes(body)) & 0xffffffff


def new_fields():
    """Every field zero: a blob that says nothing is known."""
    f = {name: 0 for name in _U32 + _U64}
    f.update(board_id=bytes(NAME_LEN), midr_value=[0] * bd.MAX_MIDR, midr_mask=[0] * bd.MAX_MIDR,
             mpidr=[0] * bd.MAX_CPUS, timer_ppi=[0] * 4,
             ecam=[dict(base=0, segment=0, start_bus=0, end_bus=0, reserved=0) for _ in range(bd.MAX_ECAM)],
             smmu=[dict(name=bytes(NAME_LEN), base=0, size=0, kind=0, reserved=bytes(7))
                   for _ in range(bd.MAX_SMMU)],
             range=[dict(name=bytes(NAME_LEN), base=0, size=0, kind=0, geometry_known=0, device=0,
                         reserved=bytes(5)) for _ in range(bd.MAX_RANGES)])
    f.update(abi_major=ABI_MAJOR, abi_minor=ABI_MINOR, total_length=V1_SIZE)
    return f


def _name_bytes(name):
    raw = name.encode('ascii')
    if not _NAME_RE.fullmatch(raw.ljust(NAME_LEN, b'\0')):
        raise BoardInfoError(ERR_VALUE, f'name {name!r} does not fit the blob')
    return raw.ljust(NAME_LEN, b'\0')


def fields_from_board(board):
    """Map a validated descriptor Board to blob fields; unknown values become cleared `known` bits."""
    f = new_fields()
    known = 0
    f['status'] = bd.EVIDENCE_LABELS.index(board.status)
    f['board_id'] = _name_bytes(board.board_id)

    soc = board.soc_name is not None and board.soc_family is not None
    if soc:
        f['soc_id'] = bd.SOC_IDS[board.soc_name]
        known |= KNOWN['SOC']
    if board.midr is not None and soc:
        f['midr_count'] = len(board.midr)
        for i, (value, mask) in enumerate(board.midr):
            f['midr_value'][i], f['midr_mask'][i] = value, mask
        known |= KNOWN['MIDR']
    if board.cluster_count is not None and board.cores_per_cluster is not None:
        f['cluster_count'], f['cores_per_cluster'] = board.cluster_count, board.cores_per_cluster
        known |= KNOWN['COUNTS']
    affinity = board.affinity_mask is not None and board.cluster_mask is not None
    if affinity:
        f['affinity_mask'], f['cluster_mask'] = board.affinity_mask, board.cluster_mask
        known |= KNOWN['AFFINITY']
    if board.mpidrs is not None and affinity:
        f['mpidr_count'] = len(board.mpidrs)
        for i, value in enumerate(board.mpidrs):
            f['mpidr'][i] = value
        known |= KNOWN['MPIDRS']
    if board.gic_version is not None:
        f['gic_version'] = board.gic_version
        known |= KNOWN['GIC_VERSION']
    if board.gicd is not None:
        f['gicd_base'], f['gicd_size'] = board.gicd
        known |= KNOWN['GICD']
    if board.gicr is not None:
        f['gicr_base'], f['gicr_stride'], f['gicr_size'] = board.gicr
        known |= KNOWN['GICR']
    if board.its is not None:
        f['its_present'] = 1 if board.its[0] else 0
        if board.its[0]:
            f['its_base'], f['its_size'] = board.its[1], board.its[2]
        known |= KNOWN['ITS']
    if board.smmus is not None:
        f['smmu_count'] = len(board.smmus)
        for i, (name, kind, base, size) in enumerate(board.smmus):
            f['smmu'][i] = dict(name=_name_bytes(name), base=base, size=size, kind=bd.SMMU_KINDS.index(kind),
                                reserved=bytes(7))
        known |= KNOWN['SMMU']
    if board.timer_hz is not None:
        f['timer_hz'] = board.timer_hz
        known |= KNOWN['TIMER_HZ']
    if board.timer_ppi is not None:
        f['timer_ppi'] = [0 if p is None else p for p in board.timer_ppi]
        known |= KNOWN['TIMER_PPI']
    if board.ecam is not None:
        f['ecam_count'] = len(board.ecam)
        for i, (segment, base, start, end) in enumerate(board.ecam):
            f['ecam'][i] = dict(base=base, segment=segment, start_bus=start, end_bus=end, reserved=0)
        known |= KNOWN['ECAM']
    if board.ranges is not None:
        f['range_count'] = len(board.ranges)
        for i, (name, kind, base, size, device) in enumerate(board.ranges):
            f['range'][i] = dict(name=_name_bytes(name), base=base or 0, size=size or 0,
                                 kind=bd.RANGE_KINDS.index(kind), geometry_known=0 if base is None else 1,
                                 device=DEVICE_NONE if device is None else bd.DEVICE_VOCABULARY.index(device),
                                 reserved=bytes(5))
        known |= KNOWN['RANGES']
    for index, name in enumerate(bd.DEVICE_VOCABULARY):
        state, mmio, _ = board.devices[name]
        if state == 'present':
            f['device_present'] |= 1 << index
        elif state == 'absent':
            f['device_absent'] |= 1 << index
        if mmio is True:
            f['device_mmio'] |= 1 << index
    if board.ipa_bits is not None:
        f['ipa_bits'] = board.ipa_bits
        known |= KNOWN['IPA']
    f['known'] = known
    return f


def encode(fields, *, extra=b'', crc=None, total_length=None):
    """Serialize fields; total_length and crc32 are computed unless given (tests give them to forge blobs).

    `extra` is appended after the 1.0 layout and covered by the CRC, as a later minor would."""
    buf = bytearray(V1_SIZE)
    buf[:8] = MAGIC
    for name in _U32:
        struct.pack_into('<I', buf, OFFSETS[name], fields[name])
    for name in _U64:
        struct.pack_into('<Q', buf, OFFSETS[name], fields[name])
    buf[OFFSETS['board_id']:OFFSETS['board_id'] + NAME_LEN] = fields['board_id']
    struct.pack_into('<4I', buf, OFFSETS['midr_value'], *fields['midr_value'])
    struct.pack_into('<4I', buf, OFFSETS['midr_mask'], *fields['midr_mask'])
    struct.pack_into(f'<{bd.MAX_CPUS}Q', buf, OFFSETS['mpidr'], *fields['mpidr'])
    struct.pack_into('<4B', buf, OFFSETS['timer_ppi'], *fields['timer_ppi'])
    for i, e in enumerate(fields['ecam']):
        ECAM.pack_into(buf, OFFSETS['ecam'] + i * ECAM.size, e['base'], e['segment'], e['start_bus'], e['end_bus'],
                       e['reserved'])
    for i, e in enumerate(fields['smmu']):
        SMMU.pack_into(buf, OFFSETS['smmu'] + i * SMMU.size, e['name'], e['base'], e['size'], e['kind'],
                       e['reserved'])
    for i, e in enumerate(fields['range']):
        RANGE.pack_into(buf, OFFSETS['range'] + i * RANGE.size, e['name'], e['base'], e['size'], e['kind'],
                        e['geometry_known'], e['device'], e['reserved'])
    buf += extra
    struct.pack_into('<I', buf, OFFSETS['total_length'], len(buf) if total_length is None else total_length)
    struct.pack_into('<I', buf, OFFSETS['crc32'], crc32_of(buf) if crc is None else crc)
    return bytes(buf)


def decode(blob):
    """Read every 1.0 field from the first V1_SIZE bytes; performs no validation beyond having enough bytes."""
    data = bytes(blob)
    if len(data) < V1_SIZE:
        raise BoardInfoError(ERR_TRUNCATED)
    f = {name: struct.unpack_from('<I', data, OFFSETS[name])[0] for name in _U32}
    f.update({name: struct.unpack_from('<Q', data, OFFSETS[name])[0] for name in _U64})
    f['board_id'] = data[OFFSETS['board_id']:OFFSETS['board_id'] + NAME_LEN]
    f['midr_value'] = list(struct.unpack_from('<4I', data, OFFSETS['midr_value']))
    f['midr_mask'] = list(struct.unpack_from('<4I', data, OFFSETS['midr_mask']))
    f['mpidr'] = list(struct.unpack_from(f'<{bd.MAX_CPUS}Q', data, OFFSETS['mpidr']))
    f['timer_ppi'] = list(struct.unpack_from('<4B', data, OFFSETS['timer_ppi']))
    f['ecam'] = [dict(zip(('base', 'segment', 'start_bus', 'end_bus', 'reserved'),
                          ECAM.unpack_from(data, OFFSETS['ecam'] + i * ECAM.size))) for i in range(bd.MAX_ECAM)]
    f['smmu'] = [dict(zip(('name', 'base', 'size', 'kind', 'reserved'),
                          SMMU.unpack_from(data, OFFSETS['smmu'] + i * SMMU.size))) for i in range(bd.MAX_SMMU)]
    f['range'] = [dict(zip(('name', 'base', 'size', 'kind', 'geometry_known', 'device', 'reserved'),
                           RANGE.unpack_from(data, OFFSETS['range'] + i * RANGE.size)))
                  for i in range(bd.MAX_RANGES)]
    return f


def _name_ok(name):
    return len(name) == NAME_LEN and _NAME_RE.fullmatch(name) is not None


def _zero(entry):
    return not any(any(v) if isinstance(v, bytes) else v for v in entry.values())


def _has(f, group):
    return bool(f['known'] & KNOWN[group])


def _check_counts(f):
    if (f['midr_count'] > bd.MAX_MIDR or f['mpidr_count'] > bd.MAX_CPUS or f['ecam_count'] > bd.MAX_ECAM or
            f['smmu_count'] > bd.MAX_SMMU or f['range_count'] > bd.MAX_RANGES):
        return ERR_COUNT
    return OK


def _check_reserved(f):
    has = lambda group: _has(f, group)      # noqa: E731
    if (f['known'] & ~KNOWN_ALL or f['reserved0'] or f['reserved1'] or f['reserved2'] or f['reserved3'] or
            f['reserved4'] or f['reserved5'] or
            (f['device_present'] | f['device_absent'] | f['device_mmio']) >> DEV_COUNT):
        return ERR_RESERVED
    unknown_but_set = (
        (not has('SOC') and f['soc_id']),
        (not has('MIDR') and f['midr_count']),
        (not has('COUNTS') and (f['cluster_count'] or f['cores_per_cluster'])),
        (not has('AFFINITY') and (f['affinity_mask'] or f['cluster_mask'])),
        (not has('MPIDRS') and f['mpidr_count']),
        (not has('GIC_VERSION') and f['gic_version']),
        (not has('GICD') and (f['gicd_base'] or f['gicd_size'])),
        (not has('GICR') and (f['gicr_base'] or f['gicr_stride'] or f['gicr_size'])),
        (not has('ITS') and f['its_present']),
        (not f['its_present'] and (f['its_base'] or f['its_size'])),
        (not has('TIMER_HZ') and f['timer_hz']),
        (not has('TIMER_PPI') and any(f['timer_ppi'])),
        (not has('ECAM') and f['ecam_count']),
        (not has('SMMU') and f['smmu_count']),
        (not has('RANGES') and f['range_count']),
        (not has('IPA') and f['ipa_bits']))
    if any(unknown_but_set):
        return ERR_RESERVED
    if any(f['midr_value'][f['midr_count']:]) or any(f['midr_mask'][f['midr_count']:]):
        return ERR_RESERVED
    if any(f['mpidr'][f['mpidr_count']:]):
        return ERR_RESERVED
    for i, e in enumerate(f['ecam']):
        if not _zero(e) if i >= f['ecam_count'] else e['reserved']:
            return ERR_RESERVED
    for i, e in enumerate(f['smmu']):
        if not _zero(e) if i >= f['smmu_count'] else any(e['reserved']):
            return ERR_RESERVED
    for i, e in enumerate(f['range']):
        if i >= f['range_count']:
            if not _zero(e):
                return ERR_RESERVED
        elif any(e['reserved']) or (not e['geometry_known'] and (e['base'] or e['size'])):
            return ERR_RESERVED
    return OK


def _check_values(f):
    has = lambda group: _has(f, group)      # noqa: E731
    if f['status'] >= len(bd.EVIDENCE_LABELS) or not _name_ok(f['board_id']):
        return ERR_VALUE
    if has('SOC') and (f['soc_id'] == 0 or f['soc_id'] >= SOC_COUNT):
        return ERR_VALUE
    if has('MIDR'):
        if not f['midr_count']:
            return ERR_VALUE
        pairs = list(zip(f['midr_value'], f['midr_mask']))[:f['midr_count']]
        for i, (value, mask) in enumerate(pairs):
            if value & ~mask or mask & MIDR_PINNED_MASK != MIDR_PINNED_MASK or (value >> 16) & 0xf != 0xf:
                return ERR_VALUE
            if (value, mask) in pairs[:i]:
                return ERR_VALUE
    if has('COUNTS'):
        count, cores = f['cluster_count'], f['cores_per_cluster']
        if not 1 <= count <= 8 or not 1 <= cores <= 16 or count * cores > bd.MAX_CPUS:
            return ERR_VALUE
    if has('AFFINITY') and (not f['affinity_mask'] or f['affinity_mask'] & ~bd.MPIDR_AFFINITY or
                            not f['cluster_mask'] or f['cluster_mask'] & ~f['affinity_mask']):
        return ERR_VALUE
    if has('MPIDRS'):
        if not f['mpidr_count']:
            return ERR_VALUE
        listed = f['mpidr'][:f['mpidr_count']]
        if len(set(listed)) != len(listed):
            return ERR_VALUE
    if has('GIC_VERSION') and f['gic_version'] not in (3, 4):
        return ERR_VALUE
    if has('GICD') and (f['gicd_size'] < GIC_FRAME or f['gicd_size'] & (GIC_FRAME - 1) or
                        f['gicd_base'] & (GIC_FRAME - 1)):
        return ERR_VALUE
    if has('GICR'):
        stride, size = f['gicr_stride'], f['gicr_size']
        minimum = GICR_STRIDE_V4 if f['gic_version'] == 4 else GICR_STRIDE_V3
        if stride < minimum or stride & (GIC_FRAME - 1):
            return ERR_VALUE
        if not size or size % stride or f['gicr_base'] & (GIC_FRAME - 1) or size // stride > bd.MAX_CPUS:
            return ERR_VALUE
    if has('ITS'):
        if f['its_present'] > 1:
            return ERR_VALUE
        if f['its_present'] and (f['its_base'] & (GIC_FRAME - 1) or f['its_size'] & (GIC_FRAME - 1) or
                                 not f['its_size']):
            return ERR_VALUE
    seen = []
    for e in f['smmu'][:f['smmu_count']]:
        if not _name_ok(e['name']) or e['kind'] > 1 or e['name'] in seen:
            return ERR_VALUE
        seen.append(e['name'])
    if has('TIMER_HZ') and not 1_000_000 <= f['timer_hz'] <= 1_000_000_000:
        return ERR_VALUE
    if has('TIMER_PPI'):
        intids = [p for p in f['timer_ppi'] if p]
        if not intids or any(not 16 <= p <= 31 for p in intids) or len(set(intids)) != len(intids):
            return ERR_VALUE
    segments = []
    for e in f['ecam'][:f['ecam_count']]:
        if e['base'] & ((1 << ECAM_BUS_SHIFT) - 1) or e['end_bus'] < e['start_bus'] or e['segment'] in segments:
            return ERR_VALUE
        segments.append(e['segment'])
    seen = []
    for e in f['range'][:f['range_count']]:
        if not _name_ok(e['name']) or e['kind'] > 1 or e['geometry_known'] > 1 or e['name'] in seen:
            return ERR_VALUE
        if not e['geometry_known'] and e['kind'] != 0:
            return ERR_VALUE
        if e['device'] != DEVICE_NONE and (e['device'] >= DEV_COUNT or e['kind'] == 0):
            return ERR_VALUE
        seen.append(e['name'])
    if has('IPA') and f['ipa_bits'] != IPA_BITS:
        return ERR_VALUE
    return OK


def _check_consistency(f):
    has = lambda group: _has(f, group)      # noqa: E731
    if has('MIDR'):
        if not has('SOC'):
            return ERR_CONSISTENCY
        if any(value >> 24 != 0x51 for value in f['midr_value'][:f['midr_count']]):
            return ERR_CONSISTENCY
    if has('MPIDRS'):
        if not has('AFFINITY'):
            return ERR_CONSISTENCY
        listed = f['mpidr'][:f['mpidr_count']]
        if any(m & ~f['affinity_mask'] for m in listed):
            return ERR_CONSISTENCY
        if has('COUNTS'):
            if f['mpidr_count'] != f['cluster_count'] * f['cores_per_cluster']:
                return ERR_CONSISTENCY
            groups = {}
            for m in listed:
                groups[m & f['cluster_mask']] = groups.get(m & f['cluster_mask'], 0) + 1
            if any(n != f['cores_per_cluster'] for n in groups.values()) or len(groups) != f['cluster_count']:
                return ERR_CONSISTENCY
    if (has('GICR') and has('COUNTS') and
            f['gicr_size'] // f['gicr_stride'] < f['cluster_count'] * f['cores_per_cluster']):
        return ERR_CONSISTENCY
    if f['device_present'] & f['device_absent'] or f['device_mmio'] & f['device_absent']:
        return ERR_CONSISTENCY
    for e in f['range'][:f['range_count']]:
        if e['device'] != DEVICE_NONE and f['device_absent'] >> e['device'] & 1:
            return ERR_CONSISTENCY
    for i in range(DEV_COUNT):
        if not f['device_present'] >> i & 1:
            continue
        owns = any(e['device'] == i for e in f['range'][:f['range_count']])
        if owns != bool(f['device_mmio'] >> i & 1):
            return ERR_CONSISTENCY
    return OK


def _windows(f):
    """(base, size) of every window in the order the C unit visits them; None marks an empty slot."""
    slots = [(f['gicd_base'], f['gicd_size']) if _has(f, 'GICD') else None,
             (f['gicr_base'], f['gicr_size']) if _has(f, 'GICR') else None,
             (f['its_base'], f['its_size']) if _has(f, 'ITS') and f['its_present'] else None]
    for i, e in enumerate(f['smmu']):
        slots.append((e['base'], e['size']) if i < f['smmu_count'] else None)
    for i, e in enumerate(f['ecam']):
        slots.append((e['base'], (e['end_bus'] + 1) << ECAM_BUS_SHIFT) if i < f['ecam_count'] else None)
    for i, e in enumerate(f['range']):
        slots.append((e['base'], e['size']) if i < f['range_count'] and e['geometry_known'] else None)
    return slots


def _check_windows(f):
    slots = _windows(f)
    for window in slots:
        if window is None:
            continue
        base, size = window
        if not size or (base | size) & (PAGE - 1):
            return ERR_WINDOW
        if base >= IPA_LIMIT or size > IPA_LIMIT - base:
            return ERR_WINDOW
    live = [w for w in slots if w is not None]
    for i, (base, size) in enumerate(live):
        for other_base, other_size in live[:i]:
            if base < other_base + other_size and other_base < base + size:
                return ERR_OVERLAP
    return OK


def check(blob, address=0):
    """Mirror of board_info_validate(): the error code, OK (0) for a blob the monitor would accept.

    `address` is where the blob would sit in memory; only its low three bits matter (ALIGN)."""
    if blob is None:
        return ERR_NULL
    if address & 7:
        return ERR_ALIGN
    data = bytes(blob)
    if len(data) < HEADER_SIZE:
        return ERR_TRUNCATED
    if data[:8] != MAGIC:
        return ERR_MAGIC
    major, minor, total, crc, required, optional = struct.unpack_from('<6I', data, 8)
    if major != ABI_MAJOR:
        return ERR_MAJOR
    if total & 7 or total > MAX_LENGTH or total < V1_SIZE:
        return ERR_LENGTH
    if total > len(data):
        return ERR_TRUNCATED
    if crc32_of(data, total) != crc:
        return ERR_CRC
    if required & ~FLAGS_REQUIRED_KNOWN:
        return ERR_REQUIRED_FLAGS
    if minor <= ABI_MINOR and optional & ~FLAGS_OPTIONAL_KNOWN:
        return ERR_OPTIONAL_FLAGS
    f = decode(data)
    for phase in (_check_counts, _check_reserved, _check_values, _check_consistency, _check_windows):
        code = phase(f)
        if code:
            return code
    return OK


def validate(blob):
    """Decode a blob the monitor would accept, or raise BoardInfoError with the shared error code."""
    code = check(blob)
    if code:
        raise BoardInfoError(code)
    return decode(blob)


def unready(f):
    """Mirror of board_info_unready(): UNREADY bits for groups a boot depends on that the blob leaves unknown."""
    mask = 0
    has = lambda group: _has(f, group)      # noqa: E731
    if f['status'] == bd.EVIDENCE_LABELS.index('UNIMPLEMENTED'):
        mask |= UNREADY['STATUS']
    for group in ('SOC', 'MIDR', 'COUNTS', 'AFFINITY', 'MPIDRS', 'GIC_VERSION', 'GICD', 'GICR', 'ITS', 'SMMU',
                  'TIMER_HZ', 'TIMER_PPI', 'ECAM', 'RANGES'):
        if not has(group):
            mask |= UNREADY[group]
    if has('TIMER_PPI') and not all(f['timer_ppi']):
        mask |= UNREADY['TIMER_PPI']
    if has('RANGES'):
        drams = [e for e in f['range'][:f['range_count']] if e['kind'] == 0]
        if not any(e['geometry_known'] for e in drams) or any(not e['geometry_known'] for e in drams):
            mask |= UNREADY['RANGES']
    if (f['device_present'] | f['device_absent']) != (1 << DEV_COUNT) - 1:
        mask |= UNREADY['DEVICES']
    if not has('IPA'):
        mask |= UNREADY['IPA']
    return mask


def names_of(mask, table):
    return [name for name, bit in table.items() if mask & bit]


def pack(board):
    """Descriptor Board -> blob bytes. Refuses to emit a blob its own validator rejects."""
    blob = encode(fields_from_board(board))
    code = check(blob)
    if code:
        raise BoardInfoError(code, f'descriptor {board.board_id!r} packs to a blob the validator refuses: '
                                   f'{ERROR_NAMES[code]} ({strerror(code)})')
    return blob


def json_to_blob(text):
    return pack(bd.loads(text))


def describe(f):
    """Inspection view of a decoded, validated blob. Not a descriptor: it carries no provenance."""
    def hexes(values):
        return [f'{v:#x}' for v in values]
    view = {'abi': f"{f['abi_major']}.{f['abi_minor']}", 'board_id': f['board_id'].rstrip(b'\0').decode('ascii'),
            'status': bd.EVIDENCE_LABELS[f['status']],
            'known': names_of(f['known'], KNOWN), 'unready': names_of(unready(f), UNREADY)}
    if _has(f, 'SOC'):
        name = next(n for n, i in bd.SOC_IDS.items() if i == f['soc_id'])
        view['soc'] = {'name': name, 'family': bd.KNOWN_SOCS[name][0]}
    if _has(f, 'MIDR'):
        view['midr'] = [{'value': f'{v:#x}', 'mask': f'{m:#x}'}
                        for v, m in zip(f['midr_value'][:f['midr_count']], f['midr_mask'][:f['midr_count']])]
    if _has(f, 'COUNTS'):
        view['clusters'] = {'count': f['cluster_count'], 'cores_per_cluster': f['cores_per_cluster']}
    if _has(f, 'AFFINITY'):
        view['mpidr_layout'] = {'affinity_mask': f"{f['affinity_mask']:#x}", 'cluster_mask': f"{f['cluster_mask']:#x}"}
    if _has(f, 'MPIDRS'):
        view['mpidrs'] = hexes(f['mpidr'][:f['mpidr_count']])
    gic = {}
    if _has(f, 'GIC_VERSION'):
        gic['version'] = f['gic_version']
    if _has(f, 'GICD'):
        gic['gicd'] = {'base': f"{f['gicd_base']:#x}", 'size': f"{f['gicd_size']:#x}"}
    if _has(f, 'GICR'):
        gic['gicr'] = {'base': f"{f['gicr_base']:#x}", 'stride': f"{f['gicr_stride']:#x}",
                       'size': f"{f['gicr_size']:#x}"}
    if _has(f, 'ITS'):
        gic['its'] = ({'present': True, 'base': f"{f['its_base']:#x}", 'size': f"{f['its_size']:#x}"}
                      if f['its_present'] else {'present': False})
    if gic:
        view['gic'] = gic
    if _has(f, 'SMMU'):
        view['smmu'] = [{'name': e['name'].rstrip(b'\0').decode('ascii'), 'kind': bd.SMMU_KINDS[e['kind']],
                         'base': f"{e['base']:#x}", 'size': f"{e['size']:#x}"} for e in f['smmu'][:f['smmu_count']]]
    timer = {}
    if _has(f, 'TIMER_HZ'):
        timer['frequency_hz'] = f['timer_hz']
    if _has(f, 'TIMER_PPI'):
        timer['ppi'] = dict(zip(bd.TIMER_PPI_KEYS, [p or None for p in f['timer_ppi']]))
    if timer:
        view['timer'] = timer
    if _has(f, 'ECAM'):
        view['ecam'] = [{'segment': e['segment'], 'base': f"{e['base']:#x}", 'start_bus': e['start_bus'],
                         'end_bus': e['end_bus']} for e in f['ecam'][:f['ecam_count']]]
    if _has(f, 'RANGES'):
        view['ranges'] = [{'name': e['name'].rstrip(b'\0').decode('ascii'), 'kind': bd.RANGE_KINDS[e['kind']],
                           'base': f"{e['base']:#x}" if e['geometry_known'] else None,
                           'size': f"{e['size']:#x}" if e['geometry_known'] else None,
                           'device': None if e['device'] == DEVICE_NONE else bd.DEVICE_VOCABULARY[e['device']]}
                          for e in f['range'][:f['range_count']]]
    view['devices'] = {name: ('present' if f['device_present'] >> i & 1 else
                              'absent' if f['device_absent'] >> i & 1 else 'unknown') +
                             (' (own ranges)' if f['device_mmio'] >> i & 1 else '')
                       for i, name in enumerate(bd.DEVICE_VOCABULARY)}
    if _has(f, 'IPA'):
        view['ipa_bits'] = f['ipa_bits']
    return view


def _read(path):
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise BoardInfoError(ERR_NULL, f'cannot read {path}: {type(exc).__name__}') from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description='Pack, inspect and check board-info blobs.')
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('pack', help='descriptor JSON -> blob')
    p.add_argument('descriptor')
    p.add_argument('-o', '--output', required=True)
    p.add_argument('--force', action='store_true', help='replace an existing output file')
    p = sub.add_parser('unpack', help='blob -> inspection JSON')
    p.add_argument('blob')
    p = sub.add_parser('check', help='validate a blob; --boot-ready also requires every group to be known')
    p.add_argument('blob')
    p.add_argument('--boot-ready', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.command == 'pack':
            out = Path(args.output)
            if out.exists() and not args.force:
                print(f'{out}: exists; pass --force to replace it', file=sys.stderr)
                return 2
            blob = pack(bd.load(args.descriptor))
            out.write_bytes(blob)
            print(f'{out}: {len(blob)} bytes, ABI {ABI_MAJOR}.{ABI_MINOR}, crc32 {crc32_of(blob):#010x}')
            return 0
        blob = _read(args.blob)
        f = validate(blob)
        if args.command == 'unpack':
            print(json.dumps(describe(f), indent=2))
            return 0
        mask = unready(f)
        print(f'{args.blob}: VALID; unready: {names_of(mask, UNREADY) or "none"}')
        if args.boot_ready and mask:
            print(f'{args.blob}: REFUSED: not boot-ready', file=sys.stderr)
            return 1
        return 0
    except (BoardInfoError, bd.BoardDescriptorError) as exc:
        print(f'REFUSED: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
