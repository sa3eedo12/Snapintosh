# SPDX-License-Identifier: MIT
"""Host side of the SP11 NVMe adoption: the target block from the board descriptor, the record block, their layout in the
window reservation, and the cross-check of what a takeover or a probe observed (compat/nvme-host.h, docs/SP11-NVME-WINDOW.md).

A library (the monitor-side C code is compat/nvme-host.c; the tests are tools/test-nvme-sp11-wiring.py).

What the monitor does. On an SP11 build ans_window_prepare (probes/xnuboot/ans_glue.c) calls nvmeh_sp11_adopt with three
blocks that live in the window reservation: the params (probes/answindow.py), the TARGET and the RECORD. The target is what
the board descriptor and the prepared run pin; the record is what the takeover tool recorded and is untrusted input. The
monitor compares them, re-reads the endpoint's configuration header, and only then runs the A16 controller re-proof. A block
that was never placed is all zeros and is refused (ABI), so a host that supplies nothing gets a named refusal and no adoption.

What this module does. It builds the target from `Board.storage` and never from a constant: a group the descriptor does not
state has its `known` bit clear, and the monitor refuses that group by name (UNKNOWN_BDF, UNKNOWN_STREAM, ...). The namespace
identity (EUI64) and the run generation are per-run pins and are never in the descriptor; the caller passes them or the group
stays unknown. Nothing here reads hardware, and nothing is invented: every number in a target block is a descriptor value or
a pin the caller named.

The RECORD producer (the takeover tool's output, the GPT survey of the SSD, the protected extents) is UNIMPLEMENTED on the SP11:
`pack_record` only lays out fields a producer will hand it. Evidence: EXPERIMENTAL (host only); the descriptor values behind it
are OBSERVED once on one unit (docs/SP11-NVME-WINDOW.md section 7.1). Hardware: NOT RUN.
"""
import struct
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _dir in (_ROOT / 'tools', _ROOT / 'probes'):
    if str(_dir) not in sys.path:
        sys.path.insert(0, str(_dir))
import answindow  # noqa: E402
import board_descriptor as bd  # noqa: E402

ABI = 1
MAX_PROTECTED = 32
U64 = (1 << 64) - 1

# struct nvmeh_sp11_target and struct nvmeh_sp11_record, word by word (compat/nvme-host.h); the test compares both with the C
# structs through the native driver, so an edit on one side only fails there.
TARGET_FIELDS = ('abi', 'known', 'segment', 'ecam_base', 'start_bus', 'end_bus', 'bdf', 'smmu_base', 'stream_base',
                 'rid_base', 'rid_count', 'eui64', 'generation', 'reserved0', 'reserved1', 'reserved2')
RECORD_FIELDS = ('abi', 'flags', 'generation', 'segment', 'bdf', 'ecam_base', 'cfg_pa', 'class_code', 'smmu_base',
                 'smmu_mode', 'stream_id', 'eui64', 'lba_bytes', 'metadata_bytes', 'nsze', 'first_usable',
                 'last_usable', 'protected_count')
TARGET_WORDS = len(TARGET_FIELDS)
RECORD_WORDS = len(RECORD_FIELDS) + 2 * MAX_PROTECTED
TARGET_BYTES = 8 * TARGET_WORDS
RECORD_BYTES = 8 * RECORD_WORDS

# NVMEH_SP11_K_* (which target groups the descriptor and the run measured), NVMEH_SP11_R_*, NVMEH_SP11_SMMU_*.
K_ECAM, K_BDF, K_SMMU, K_STREAM, K_EUI64, K_GENERATION = 1, 2, 4, 8, 16, 32
K_ALL = 63
R_CLEAN, R_DMA_PROVEN, R_WRITE_AUTH = 1, 2, 4
SMMU_BYPASS, SMMU_S2_IDENTITY = 1, 2
NVME_CLASS = bd.NVME_CLASS

# compat/ans.h: the two blocks sit after the params (+0) and the state (+0x80) of ANS_WINDOW_NVMEH, in the reservation's
# unused stretch below the DMA window. The offsets are from the reservation base, like answindow.WINDOW_*.
TARGET_OFFSET = answindow.WINDOW_NVMEH + 0x1000
RECORD_OFFSET = answindow.WINDOW_NVMEH + 0x2000


class Refused(ValueError):
    """The target cannot be built, or a block cannot be placed. `reason` is the name tests and operators match on."""

    def __init__(self, reason, message):
        super().__init__(f'{reason}: {message}')
        self.reason = reason


def _pin(value, what):
    if value is None:
        return None
    if type(value) is not int or not 0 < value <= U64:
        raise Refused('PIN_INVALID', f'{what} must be a nonzero unsigned 64-bit integer, got {value!r}')
    return value


def target_fields(board, eui64=None, generation=None):
    """The target block's fields as a dict, from a validated Board (tools/board_descriptor.py) and the per-run pins.

    A group is marked known only when the descriptor states every value of it; the monitor refuses an unknown group by name.
    Raises Refused when the board says the NVMe device is not there, or carries no storage section at all."""
    if not isinstance(board, bd.Board):
        raise Refused('NOT_A_BOARD', 'a validated descriptor Board is required')
    if board.devices['nvme'][0] != 'present':
        raise Refused('DEVICE_NOT_PRESENT', f"devices.nvme is {board.devices['nvme'][0]!r}: a board that does not give the "
                                            'guest the SSD gets no target block')
    storage = board.storage
    if storage is None:
        raise Refused('NO_STORAGE_SECTION', 'the descriptor has no storage section')
    t = dict.fromkeys(TARGET_FIELDS, 0)
    t['abi'] = ABI
    known = 0
    if storage.segment is not None and storage.ecam is not None:
        t['segment'], (t['ecam_base'], t['start_bus'], t['end_bus']) = storage.segment, storage.ecam
        known |= K_ECAM
    if storage.endpoint is not None and known & K_ECAM:
        t['bdf'] = storage.endpoint.rid
        known |= K_BDF
    if storage.smmu is not None:
        bases = [base for name, kind, base, _ in board.smmus or () if name == storage.smmu and kind == 'smmu-v3']
        if len(bases) != 1:
            raise Refused('SMMU_NOT_FOUND', f'storage.nvme.smmu {storage.smmu!r} is not one SMMUv3 entry of the descriptor')
        t['smmu_base'] = bases[0]
        known |= K_SMMU
    if storage.stream is not None:
        t['rid_base'], t['rid_count'], t['stream_base'] = storage.stream
        known |= K_STREAM
    if _pin(eui64, 'the namespace EUI64') is not None:
        t['eui64'] = eui64
        known |= K_EUI64
    if _pin(generation, 'the run generation') is not None:
        t['generation'] = generation
        known |= K_GENERATION
    t['known'] = known
    return t


def _words(fields, names, what):
    if set(fields) != set(names):
        raise Refused('FIELDS', f'{what} needs exactly {list(names)}; got {sorted(set(fields) ^ set(names))} wrong')
    out = []
    for name in names:
        value = fields[name]
        if type(value) is not int or not 0 <= value <= U64:
            raise Refused('WORD_RANGE', f'{what} field {name} = {value!r} is not an unsigned 64-bit integer')
        out.append(value)
    return out


def pack_target(fields):
    """struct nvmeh_sp11_target as 128 little-endian bytes."""
    return struct.pack(f'<{TARGET_WORDS}Q', *_words(fields, TARGET_FIELDS, 'the target'))


def unpack_target(raw):
    if len(raw) != TARGET_BYTES:
        raise Refused('SIZE', f'a target block is {TARGET_BYTES} bytes, got {len(raw)}')
    return dict(zip(TARGET_FIELDS, struct.unpack(f'<{TARGET_WORDS}Q', raw)))


def pack_record(fields, extents):
    """struct nvmeh_sp11_record as 656 bytes: the 18 fields, then 32 (first_lba, last_lba) pairs, unused ones zero.

    `fields['protected_count']` must equal len(extents): the monitor reads that many pairs and refuses an empty or longer list
    (PROTECTED), so a producer that miscounts is told here and not by a refusal after the hand-off."""
    extents = [tuple(e) for e in extents]
    if len(extents) > MAX_PROTECTED:
        raise Refused('EXTENTS', f'{len(extents)} protected extents; the record holds {MAX_PROTECTED}')
    if fields.get('protected_count') != len(extents):
        raise Refused('EXTENTS', f"protected_count {fields.get('protected_count')!r} is not the {len(extents)} extents given")
    words = _words(fields, RECORD_FIELDS, 'the record')
    for first, last in extents:
        words += _words(dict(first=first, last=last), ('first', 'last'), 'a protected extent')
    words += [0] * (RECORD_WORDS - len(words))
    return struct.pack(f'<{RECORD_WORDS}Q', *words)


def unpack_record(raw):
    if len(raw) != RECORD_BYTES:
        raise Refused('SIZE', f'a record block is {RECORD_BYTES} bytes, got {len(raw)}')
    words = struct.unpack(f'<{RECORD_WORDS}Q', raw)
    fields = dict(zip(RECORD_FIELDS, words))
    count = fields['protected_count']
    pairs = [(words[len(RECORD_FIELDS) + 2 * i], words[len(RECORD_FIELDS) + 2 * i + 1]) for i in range(MAX_PROTECTED)]
    return fields, pairs[:count] if count <= MAX_PROTECTED else pairs


def place(reservation, target, record):
    """Write the two blocks into a window reservation image (a bytearray, offsets from its base).

    Refuses a reservation too short to hold them, a block of the wrong size, and a destination that is not all zero: a block
    from an earlier run must not be overwritten half-way or left in place by a caller that forgot to place its own. The
    monitor cannot tell a fresh pair from a stale one (docs/SP11-NVME-WINDOW.md, "The reservation across warm restarts"), so the
    producer is where that is held."""
    if not isinstance(reservation, bytearray):
        raise Refused('RESERVATION', 'the reservation image must be a bytearray')
    if len(target) != TARGET_BYTES or len(record) != RECORD_BYTES:
        raise Refused('SIZE', f'a target is {TARGET_BYTES} bytes and a record {RECORD_BYTES}')
    if len(reservation) < RECORD_OFFSET + RECORD_BYTES:
        raise Refused('RESERVATION', f'the reservation image is {len(reservation)} bytes; the record ends at '
                                     f'{RECORD_OFFSET + RECORD_BYTES:#x}')
    if TARGET_OFFSET + TARGET_BYTES > RECORD_OFFSET or RECORD_OFFSET + RECORD_BYTES > answindow.WINDOW_DMA:
        raise Refused('LAYOUT', 'the blocks overlap each other or reach the DMA window')
    for name, at, size in (('target', TARGET_OFFSET, TARGET_BYTES), ('record', RECORD_OFFSET, RECORD_BYTES)):
        if any(reservation[at:at + size]):
            raise Refused('BLOCK_NOT_EMPTY', f'the {name} block is not empty: refusing to overwrite another run\'s block')
    reservation[TARGET_OFFSET:TARGET_OFFSET + TARGET_BYTES] = target
    reservation[RECORD_OFFSET:RECORD_OFFSET + RECORD_BYTES] = record


def check_observed(board, observed):
    """The descriptor's expected values against what a takeover, a probe or a record observed.

    `observed` is a dict with any of: segment, ecam_base, start_bus, end_bus, smmu_base, stream_id, and root_port and
    endpoint as dicts (bus, device, function, vendor_id, device_id, class_code). Returns a list of (name, expected, observed)
    for every disagreement, and (UNSTATED_<field>, None, observed) for an observed value the descriptor does not state: a
    value that cannot be cross-checked is not a value that matched. An empty list means every observed value matched."""
    if not isinstance(board, bd.Board) or board.storage is None:
        raise Refused('NO_STORAGE_SECTION', 'a descriptor Board with a storage section is required')
    s, out = board.storage, []

    def compare(name, expected, got):
        if got is None:
            return
        if expected is None:
            out.append(('UNSTATED_' + name, None, got))
        elif expected != got:
            out.append((name, expected, got))

    compare('SEGMENT', s.segment, observed.get('segment'))
    ecam = s.ecam
    compare('ECAM_BASE', ecam and ecam[0], observed.get('ecam_base'))
    compare('START_BUS', ecam and ecam[1], observed.get('start_bus'))
    compare('END_BUS', ecam and ecam[2], observed.get('end_bus'))
    smmu = [base for name, kind, base, _ in board.smmus or () if name == s.smmu]
    compare('SMMU_BASE', smmu[0] if smmu else None, observed.get('smmu_base'))
    for key, function in (('root_port', s.root_port), ('endpoint', s.endpoint)):
        seen = observed.get(key)
        if seen is None:
            continue
        label = key.upper()
        for field in ('bus', 'device', 'function', 'vendor_id', 'device_id', 'class_code'):
            compare(f'{label}_{field.upper()}', function and getattr(function, field), seen.get(field))
    sid = observed.get('stream_id')
    if sid is not None:
        if s.stream is None or s.endpoint is None:
            out.append(('UNSTATED_STREAM_ID', None, sid))
        else:
            rid_base, rid_count, stream_base = s.stream
            compare('STREAM_ID', stream_base + s.endpoint.rid - rid_base, sid)
    return out
