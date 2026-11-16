#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Surface Pro 11 stick bundle: pack, unpack and validate PIN.DAT, the GUEST.DAT head and chunk table and REPORT.DAT's head.

    python3 tools/sp11_stick_bundle.py layout                  the layout, one "name value" per line (what the C harness prints)
    python3 tools/sp11_stick_bundle.py errors                  the refusal table
    python3 tools/sp11_stick_bundle.py check-pin PIN.DAT
    python3 tools/sp11_stick_bundle.py check-guest GUEST.DAT PIN.DAT [--hash chunks,file]

The host half of probes/sp11-stick-bundle.h; docs/SP11-STICK-BUNDLE.md is the note. The check_*() functions are a second,
independent implementation of the C checks, in the same order, so a record that is wrong twice gets the same refusal code
from both. tools/test-sp11-stick-bundle.py runs both on the same bytes and compares layouts, constants, names and codes;
nothing here includes or parses the header, so a constant defined here and not there fails that test. Chunk payloads are
opaque bytes. Output is deterministic: no timestamps, no randomness. A packer refuses to emit a record its own checker would
refuse. Evidence: EXPERIMENTAL (host tests only; hardware NOT RUN)."""
import hashlib
import struct
import sys

MIB = 1 << 20
VERSION = 1
RUNNER_MAGIC = 0x31594150314E3151
RUNNER_VERSION = 1
RUNNER_ARGS_BYTES = 192
FILES = 5
FILE_MAX = 256 * MIB
PATH_MAX = 256
LINE_MAX = 100
REPORTER_LINES = 40
WRAPPER_LINES = 30
IPA_LIMIT = 1 << 36
GUEST_ALIGN = 32 * MIB
GUEST_MIN = 192 * MIB
RESERVE_ALIGN = 16 * MIB
RESERVE_MIN = 48 * MIB
WORK_ALIGN = 0x10000
WORK_BYTES = 240 * MIB
EFI_CONVENTIONAL = 7
EFI_WB = 8
MAP_MIN_STRIDE = 40
MAP_MAX = 1024
DECLARED_MAX = 24
PIN_BYTES = 1024
MIDR_MAX = 4
AVOID_MAX = 4
ACPI_MAX = 8
GICR_MAX = 32
GUEST_HEAD_BYTES = 128
CHUNK_BYTES = 64
CHUNKS_MAX = 1024
DEST_ALIGN = 16
DATA_ALIGN = 64
REPORT_HEAD_PAGE = 4096
MONITOR_OUT_BYTES = 0x80000
REPORT_BYTES = REPORT_HEAD_PAGE + MONITOR_OUT_BYTES
REPORT_HEAD_BYTES = 296
REPORT_OUT = REPORT_HEAD_PAGE
NAME_MAX = 24
RET_REFUSED = 0x1000
ROLE_MONITOR, ROLE_REPORTER, ROLE_GUEST, ROLE_PIN, ROLE_REPORT = range(5)
KIND_KERNEL, KIND_TABLES, KIND_HANDOFF, KIND_DEVICE_TREE, KIND_PARAMS, KIND_MONITOR_IMAGE, KIND_MONITOR_DATA, KIND_OTHER = range(1, 9)
KIND_MAX = 8
CHUNK_ZERO = 1
STORE_RAM, STORE_EL2_SYSREG, STORE_GIC = 1, 2, 4
STORE_REQUIRED = STORE_RAM | STORE_EL2_SYSREG
STORE_ALL = STORE_REQUIRED | STORE_GIC
HASH_CHUNKS, HASH_FILE = 1, 2
AVOID_EL2_UNREACHABLE, AVOID_DEVICE_OWNED, AVOID_OPERATOR = 1, 2, 3
SIDE_B, SIDE_W, SIDE_R = 1, 2, 4
MAGICS = {'SP11SB_PIN_MAGIC': b'SNAPBPIN', 'SP11SB_GUEST_MAGIC': b'SNAPBGST', 'SP11SB_REPORT_MAGIC': b'SNAPBRPT'}
_CONSTANT_NAMES = (
    'VERSION', 'RUNNER_MAGIC', 'RUNNER_VERSION', 'RUNNER_ARGS_BYTES', 'FILES', 'FILE_MAX', 'PATH_MAX', 'LINE_MAX', 'REPORTER_LINES',
    'WRAPPER_LINES', 'MIB', 'IPA_LIMIT', 'GUEST_ALIGN', 'GUEST_MIN', 'RESERVE_ALIGN', 'RESERVE_MIN', 'WORK_ALIGN', 'WORK_BYTES',
    'EFI_CONVENTIONAL', 'EFI_WB', 'MAP_MIN_STRIDE', 'MAP_MAX', 'DECLARED_MAX', 'PIN_BYTES', 'MIDR_MAX', 'AVOID_MAX', 'ACPI_MAX',
    'GICR_MAX', 'GUEST_HEAD_BYTES', 'CHUNK_BYTES', 'CHUNKS_MAX', 'DEST_ALIGN', 'DATA_ALIGN', 'REPORT_HEAD_PAGE', 'MONITOR_OUT_BYTES',
    'REPORT_BYTES', 'REPORT_HEAD_BYTES', 'REPORT_OUT', 'NAME_MAX', 'RET_REFUSED', 'ROLE_MONITOR', 'ROLE_REPORTER', 'ROLE_GUEST',
    'ROLE_PIN', 'ROLE_REPORT', 'KIND_KERNEL', 'KIND_TABLES', 'KIND_HANDOFF', 'KIND_DEVICE_TREE', 'KIND_PARAMS', 'KIND_MONITOR_IMAGE',
    'KIND_MONITOR_DATA', 'KIND_OTHER', 'KIND_MAX', 'CHUNK_ZERO', 'STORE_RAM', 'STORE_EL2_SYSREG', 'STORE_GIC', 'STORE_REQUIRED',
    'STORE_ALL', 'HASH_CHUNKS', 'HASH_FILE', 'AVOID_EL2_UNREACHABLE', 'AVOID_DEVICE_OWNED', 'AVOID_OPERATOR', 'SIDE_B', 'SIDE_W', 'SIDE_R')
CONSTANTS = {'SP11SB_' + n: globals()[n] for n in _CONSTANT_NAMES}

# (code, name, sides): the refusal table. The order is the header's.
ERRORS = (
    (1, 'ARGS_BAD', 6), (2, 'FILE_COUNT', 7), (3, 'FILE_ROLE', 7), (4, 'FILE_BASE', 6), (5, 'FILE_SIZE', 7),
    (10, 'PIN_TRUNCATED', 7), (11, 'PIN_MAGIC', 7), (12, 'PIN_VERSION', 7), (13, 'PIN_SIZE', 7), (14, 'PIN_DIGEST', 7),
    (15, 'PIN_FLAGS', 7), (16, 'PIN_STRING', 7), (17, 'PIN_COUNT', 7), (18, 'PIN_RESERVED', 7), (19, 'PIN_ORDER', 7),
    (20, 'PIN_VALUE', 7), (21, 'NO_AUTHORISATION', 7), (22, 'STORE_NOT_AUTH', 7),
    (24, 'PLACE_ALIGN', 7), (25, 'PLACE_LAYOUT', 7), (26, 'PLACE_LIMIT', 7),
    (30, 'NOT_EL2', 2), (31, 'E2H_SET', 2), (32, 'EL2_REGIME', 2), (33, 'MIDR_MISMATCH', 2), (34, 'MPIDR_MISMATCH', 2),
    (35, 'CNTFRQ_MISMATCH', 2), (36, 'GIC_MISMATCH', 2), (37, 'GOP_MISMATCH', 2),
    (40, 'MAP_BAD', 3), (41, 'PLACE_NOT_CONV', 3), (42, 'PLACE_ATTR', 3), (43, 'PLACE_DECLARED', 3), (44, 'PLACE_FB', 3),
    (45, 'PLACE_AVOID', 3),
    (50, 'GUEST_TRUNCATED', 3), (51, 'GUEST_MAGIC', 3), (52, 'GUEST_VERSION', 3), (53, 'GUEST_FLAGS', 3), (54, 'GUEST_SIZE', 3),
    (55, 'GUEST_RESERVED', 3), (56, 'GUEST_COUNT', 3), (57, 'GUEST_LOAD', 3), (58, 'GUEST_PIN', 3), (59, 'CHUNK_KIND', 3),
    (60, 'CHUNK_FLAGS', 3), (61, 'CHUNK_RESERVED', 3), (62, 'CHUNK_ZERO', 3), (63, 'CHUNK_GEOMETRY', 3), (64, 'CHUNK_RANGE', 3),
    (65, 'CHUNK_ORDER', 3), (66, 'CHUNK_OVERLAP', 3), (67, 'CHUNK_EOF', 3), (68, 'CHUNK_OFFSET', 3), (69, 'PAD_NONZERO', 3),
    (70, 'CHUNK_DUP', 3), (71, 'CHUNK_MISSING', 3),
    (72, 'HASH_POLICY', 3), (73, 'INDEX_DIGEST', 3), (74, 'CHUNK_DIGEST', 3), (75, 'FILE_DIGEST', 3),
    (80, 'PLACE_UNREADABLE', 2), (81, 'COPY_VERIFY', 2), (82, 'GIC_NOT_READY', 2),
    (90, 'REPORT_SIZE', 4), (91, 'REPORT_MAGIC', 4), (92, 'REPORT_VERSION', 4), (93, 'REPORT_RESERVED', 4), (94, 'REPORT_VALUE', 4),
)
E = type('E', (), {name: code for code, name, _ in ERRORS})
STAGES = ('NONE', 'ENTERED', 'ARGS_OK', 'FILES_OK', 'PIN_OK', 'OBSERVED_OK', 'PLACED_OK', 'GUEST_OK', 'COPYING', 'COPIED', 'PREPARED',
          'MONITOR_ENTERED', 'MONITOR_RETURNED', 'DONE')
STAGE = type('STAGE', (), {name: n for n, name in enumerate(STAGES)})
_NAME = {0: 'OK', **{code: name for code, name, _ in ERRORS}}
CONSTANTS['SP11SB_ERROR_ROWS'] = 1 + len(ERRORS)
CONSTANTS['SP11SB_STAGE_COUNT'] = len(STAGES)


def error_name(code):
    return _NAME.get(code, 'UNKNOWN')


class Refusal(ValueError):
    """A record that is refused before a parser can read it safely; `code` is the refusal code the C checks give for the same bytes."""

    def __init__(self, code):
        super().__init__(f'{error_name(code)} ({code})')
        self.code = code


# name, offset, width, kind: s bytes, I u32, Q u64, H u16. Arrays of structures are one 's' member; their elements are below.
PIN_LAYOUT = (
    ('magic', 0x000, 8, 's'), ('version', 0x008, 4, 'I'), ('size', 0x00C, 4, 'I'), ('flags', 0x010, 4, 'I'),
    ('authorised_stores', 0x014, 4, 'I'), ('board_id', 0x018, 16, 's'), ('build_label', 0x028, 24, 's'),
    ('descriptor_sha256', 0x040, 32, 's'), ('authorisation_sha256', 0x060, 32, 's'), ('guest_file_size', 0x080, 8, 'Q'),
    ('guest_chunk_count', 0x088, 4, 'I'), ('reserved0', 0x08C, 4, 'I'), ('guest_file_sha256', 0x090, 32, 's'),
    ('guest_index_sha256', 0x0B0, 32, 's'), ('boot_mpidr', 0x0D0, 8, 'Q'), ('cntfrq_hz', 0x0D8, 4, 'I'), ('midr_count', 0x0DC, 4, 'I'),
    ('midr', 0x0E0, 16, 's'), ('gicd_base', 0x0F0, 8, 'Q'), ('gicr_first', 0x0F8, 8, 'Q'), ('gicr_limit', 0x100, 8, 'Q'),
    ('gicr_stride', 0x108, 8, 'Q'), ('its_base', 0x110, 8, 'Q'), ('gicr_count', 0x118, 4, 'I'), ('timer_intid', 0x11C, 4, 'I'),
    ('fb_base', 0x120, 8, 'Q'), ('fb_bytes', 0x128, 8, 'Q'), ('fb_width', 0x130, 4, 'I'), ('fb_height', 0x134, 4, 'I'),
    ('fb_stride', 0x138, 4, 'I'), ('fb_format', 0x13C, 4, 'I'), ('place_base', 0x140, 8, 'Q'), ('place_size', 0x148, 8, 'Q'),
    ('guest_base', 0x150, 8, 'Q'), ('guest_size', 0x158, 8, 'Q'), ('reserve_size', 0x160, 8, 'Q'), ('work_base', 0x168, 8, 'Q'),
    ('work_size', 0x170, 8, 'Q'), ('avoid_count', 0x178, 4, 'I'), ('reserved1', 0x17C, 4, 'I'), ('avoid', 0x180, 96, 's'),
    ('acpi_count', 0x1E0, 4, 'I'), ('reserved2', 0x1E4, 4, 'I'), ('acpi', 0x1E8, 320, 's'), ('reserved3', 0x328, 184, 's'),
    ('record_sha256', 0x3E0, 32, 's'))
AVOID_LAYOUT = (('base', 0, 8, 'Q'), ('size', 8, 8, 'Q'), ('tag', 16, 4, 'I'), ('reserved', 20, 4, 'I'))
ACPI_LAYOUT = (('sig', 0, 4, 's'), ('length', 4, 4, 'I'), ('sha256', 8, 32, 's'))
GUEST_LAYOUT = (
    ('magic', 0x00, 8, 's'), ('version', 0x08, 4, 'I'), ('head_size', 0x0C, 4, 'I'), ('flags', 0x10, 4, 'I'),
    ('entry_size', 0x14, 4, 'I'), ('entry_count', 0x18, 4, 'I'), ('reserved0', 0x1C, 4, 'I'), ('table_offset', 0x20, 8, 'Q'),
    ('data_offset', 0x28, 8, 'Q'), ('file_size', 0x30, 8, 'Q'), ('load_base', 0x38, 8, 'Q'), ('load_size', 0x40, 8, 'Q'),
    ('index_sha256', 0x48, 32, 's'), ('reserved1', 0x68, 24, 's'))
CHUNK_LAYOUT = (
    ('dest', 0x00, 8, 'Q'), ('size', 0x08, 8, 'Q'), ('file_offset', 0x10, 8, 'Q'), ('kind', 0x18, 2, 'H'), ('flags', 0x1A, 2, 'H'),
    ('reserved', 0x1C, 4, 'I'), ('sha256', 0x20, 32, 's'))
REPORT_LAYOUT = (
    ('magic', 0x000, 8, 's'), ('version', 0x008, 4, 'I'), ('head_size', 0x00C, 4, 'I'), ('stage', 0x010, 4, 'I'),
    ('refusal', 0x014, 4, 'I'), ('refusal_detail', 0x018, 8, 'Q'), ('hash_policy', 0x020, 4, 'I'), ('lines_printed', 0x024, 4, 'I'),
    ('current_el', 0x028, 8, 'Q'), ('midr_el1', 0x030, 8, 'Q'), ('mpidr_el1', 0x038, 8, 'Q'), ('cntfrq_el0', 0x040, 8, 'Q'),
    ('hcr_el2', 0x048, 8, 'Q'), ('sctlr_el2', 0x050, 8, 'Q'), ('tcr_el2', 0x058, 8, 'Q'), ('pin_code', 0x060, 4, 'I'),
    ('guest_code', 0x064, 4, 'I'), ('guest_file_base', 0x068, 8, 'Q'), ('guest_file_size', 0x070, 8, 'Q'), ('place_base', 0x078, 8, 'Q'),
    ('place_size', 0x080, 8, 'Q'), ('desc_base', 0x088, 8, 'Q'), ('desc_pages', 0x090, 8, 'Q'), ('desc_index', 0x098, 4, 'I'),
    ('chunks_total', 0x09C, 4, 'I'), ('chunks_copied', 0x0A0, 4, 'I'), ('chunks_hashed', 0x0A4, 4, 'I'), ('bytes_copied', 0x0A8, 8, 'Q'),
    ('bytes_hashed', 0x0B0, 8, 'Q'), ('counter_hz', 0x0B8, 8, 'Q'), ('ticks_enter', 0x0C0, 8, 'Q'), ('ticks_placed', 0x0C8, 8, 'Q'),
    ('ticks_copied', 0x0D0, 8, 'Q'), ('ticks_hashed', 0x0D8, 8, 'Q'), ('ticks_launch', 0x0E0, 8, 'Q'), ('ticks_return', 0x0E8, 8, 'Q'),
    ('monitor_out', 0x0F0, 8, 'Q'), ('work_base', 0x0F8, 8, 'Q'), ('work_size', 0x100, 8, 'Q'), ('params_pa', 0x108, 8, 'Q'),
    ('monitor_ret', 0x110, 8, 'Q'), ('monitor_status', 0x118, 8, 'Q'), ('monitor_entered', 0x120, 8, 'Q'))
OBSERVED_KEYS = ('current_el', 'hcr_el2', 'sctlr_el2', 'tcr_el2', 'midr_el1', 'mpidr_el1', 'cntfrq_el0', 'build_gicd_base',
                 'build_gicr_first', 'build_gicr_limit', 'build_gicr_stride', 'build_timer_intid', 'build_stores', 'fb_base', 'fb_bytes',
                 'fb_width', 'fb_height', 'fb_stride', 'fb_format')
RECORDS = (('sp11sb_pin', PIN_LAYOUT, PIN_BYTES), ('sp11sb_avoid', AVOID_LAYOUT, 24), ('sp11sb_acpi', ACPI_LAYOUT, 40),
           ('sp11sb_guest_head', GUEST_LAYOUT, GUEST_HEAD_BYTES), ('sp11sb_chunk', CHUNK_LAYOUT, CHUNK_BYTES),
           ('sp11sb_report', REPORT_LAYOUT, REPORT_HEAD_BYTES))
_MAP = {id(lay): {n: (o, w, k) for n, o, w, k in lay} for _, lay, _ in RECORDS}
M64 = (1 << 64) - 1


def layout_lines():
    """What `test-sp11-stick-bundle layout` prints, from the tables above."""
    out = []
    for record, layout, size in RECORDS:
        out.append(f'sizeof.{record} {size}')
        for name, off, width, _ in layout:
            out.append(f'offsetof.{record}.{name} {off}')
            out.append(f'sizeof.{record}.{name} {width}')
    out += [f'{name} {value}' for name, value in CONSTANTS.items()]
    out += [f'{name} {value.decode()}' for name, value in MAGICS.items()]
    out += [f'error {code} {sides} {name}' for code, name, sides in ERRORS]
    out += [f'stage {n} {name}' for n, name in enumerate(STAGES)]
    return out


def get(buf, layout, name, base=0):
    off, width, kind = _MAP[id(layout)][name]
    raw = bytes(buf[base + off:base + off + width])
    return raw if kind == 's' else int.from_bytes(raw, 'little')


def put(buf, layout, name, value, base=0):
    off, width, kind = _MAP[id(layout)][name]
    if kind == 's':
        value = value.encode() if isinstance(value, str) else bytes(value)
        if len(value) > width:
            raise ValueError(f'{name} is {len(value)} bytes, the field holds {width}')
        value = value + bytes(width - len(value))
    else:
        value = value.to_bytes(width, 'little')
    buf[base + off:base + off + width] = value


def sha256(data):
    return hashlib.sha256(data).digest()


# ---- Builders ---------------------------------------------------------------------------------------------------------
def placement(guest_base, guest_size, reserve_size=RESERVE_MIN):
    """The placement rule as fields: guest, reserve directly above it, then the work area, one contiguous range."""
    return dict(place_base=guest_base, place_size=guest_size + reserve_size + WORK_BYTES, guest_base=guest_base, guest_size=guest_size,
                reserve_size=reserve_size, work_base=guest_base + guest_size + reserve_size, work_size=WORK_BYTES)


def pack_pin(fields, check=True):
    """PIN.DAT from a dict of the fields in PIN_LAYOUT (strings as str or bytes; midr, avoid and acpi as lists; counts follow
    the lists unless given). The record digest is computed last. With check, a record the C checker would refuse raises."""
    f = dict(fields)
    buf = bytearray(PIN_BYTES)
    f.setdefault('magic', b'SNAPBPIN')
    f.setdefault('version', VERSION)
    f.setdefault('size', PIN_BYTES)
    midr, avoid, acpi = list(f.pop('midr', ())), list(f.pop('avoid', ())), list(f.pop('acpi', ()))
    f.setdefault('midr_count', len(midr))
    f.setdefault('avoid_count', len(avoid))
    f.setdefault('acpi_count', len(acpi))
    for name, value in f.items():
        put(buf, PIN_LAYOUT, name, value)
    for i, value in enumerate(midr):
        buf[0xE0 + 4 * i:0xE4 + 4 * i] = value.to_bytes(4, 'little')
    for i, entry in enumerate(avoid):
        for name, value in entry.items():
            put(buf, AVOID_LAYOUT, name, value, 0x180 + 24 * i)
    for i, entry in enumerate(acpi):
        for name, value in entry.items():
            put(buf, ACPI_LAYOUT, name, value, 0x1E8 + 40 * i)
    buf[PIN_BYTES - 32:] = sha256(bytes(buf[:PIN_BYTES - 32]))
    if check and (code := check_pin(bytes(buf))):
        raise ValueError(f'PIN.DAT refused by its own checker: {error_name(code)}')
    return bytes(buf)


def pin_digest(buf):
    """Recompute the record digest in place (a test that edits a field and wants the other checks to run)."""
    buf[PIN_BYTES - 32:] = sha256(bytes(buf[:PIN_BYTES - 32]))
    return buf


def pack_guest(load_base, load_size, chunks):
    """GUEST.DAT: head, table, data, in the one canonical layout. A chunk is dict(kind, dest, data=bytes) or, for a ZERO chunk,
    dict(kind, dest, size). The caller passes them in ascending destination order; sizes are the caller's to make multiples of 16."""
    count = len(chunks)
    table_end = GUEST_HEAD_BYTES + CHUNK_BYTES * count
    entries, data, expect = [], bytearray(), table_end
    for c in chunks:
        if c.get('data') is None:
            size, offset, digest, flags = c['size'], 0, bytes(32), CHUNK_ZERO
        else:
            size, flags = len(c['data']), 0
            offset = (expect + DATA_ALIGN - 1) & ~(DATA_ALIGN - 1)
            data += bytes(offset - expect) + c['data']
            expect, digest = offset + size, sha256(c['data'])
        entries.append(struct.pack('<QQQHHI32s', c['dest'], size, offset, c['kind'], flags, 0, digest))
    table = b''.join(entries)
    head = bytearray(GUEST_HEAD_BYTES)
    for name, value in (('magic', b'SNAPBGST'), ('version', VERSION), ('head_size', GUEST_HEAD_BYTES), ('entry_size', CHUNK_BYTES),
                        ('entry_count', count), ('table_offset', GUEST_HEAD_BYTES), ('data_offset', table_end),
                        ('file_size', expect), ('load_base', load_base), ('load_size', load_size), ('index_sha256', sha256(table))):
        put(head, GUEST_LAYOUT, name, value)
    out = bytes(head) + table + bytes(data)
    code = _guest_check(out, len(out), (load_base, load_size, len(out), count, sha256(table)))
    if code:
        raise ValueError(f'GUEST.DAT refused by its own checker: {error_name(code)}')
    return out


def guest_pin_fields(guest):
    """The three PIN.DAT fields that bind it to this GUEST.DAT (the whole-file digest, the size and the table digest)."""
    head = get(guest, GUEST_LAYOUT, 'index_sha256'), get(guest, GUEST_LAYOUT, 'entry_count')
    return dict(guest_file_size=len(guest), guest_chunk_count=head[1], guest_file_sha256=sha256(guest), guest_index_sha256=head[0])


def unpack(buf, layout):
    return {name: get(buf, layout, name) for name, _, _, _ in layout}


def unpack_guest(buf):
    """(head, [chunk, ...]) as dicts. Only the table is bounded, before an entry is read and as sp11sb_guest_head_check bounds it: a buffer
    under the 128-byte head raises Refusal(GUEST_TRUNCATED); an entry_count of 0, over CHUNKS_MAX or whose table does not fit the buffer
    raises Refusal(GUEST_COUNT), so a hostile head costs no loop. Nothing else is validated."""
    if len(buf) < GUEST_HEAD_BYTES:
        raise Refusal(E.GUEST_TRUNCATED)
    head = unpack(buf, GUEST_LAYOUT)
    count = head['entry_count']
    if not 1 <= count <= CHUNKS_MAX or GUEST_HEAD_BYTES + CHUNK_BYTES * count > len(buf):
        raise Refusal(E.GUEST_COUNT)
    return head, [unpack(buf[GUEST_HEAD_BYTES + CHUNK_BYTES * i:GUEST_HEAD_BYTES + CHUNK_BYTES * (i + 1)], CHUNK_LAYOUT) for i in range(count)]


# ---- Checks: the same order as the header --------------------------------------------------------------------------
def _u32(b, o):
    return int.from_bytes(b[o:o + 4], 'little')


def _u64(b, o):
    return int.from_bytes(b[o:o + 8], 'little')


def _overlap(a, an, b, bn):
    if an > M64 - a or bn > M64 - b:
        return True
    return bool(an and bn and a < b + bn and b < a + an)


def _zero(b):
    return not any(b)


def check_args(magic, version, size):
    return E.ARGS_BAD if magic != RUNNER_MAGIC or version != RUNNER_VERSION or size < RUNNER_ARGS_BYTES else 0


def path_is(path, role):
    """\\SP11\\ plus the role's name, any case, nothing after it. `path` is the zero-padded char[256]."""
    names = (b'SNAPMON.BIN', b'SNAPREP.BIN', b'GUEST.DAT', b'PIN.DAT', b'REPORT.DAT')
    if not 0 <= role < FILES:
        return False
    want = b'\\SP11\\' + names[role] + b'\0'
    return bytes(path[:len(want)]).ljust(len(want), b'\0').upper() == want


def check_file(path, base, size, role):
    if not path_is(path, role):
        return E.FILE_ROLE
    if not base or base & 0xFFF or size > M64 - base:
        return E.FILE_BASE
    if not size or size > FILE_MAX or (role == ROLE_REPORT and size != REPORT_BYTES):
        return E.FILE_SIZE
    return 0


def check_files(files):
    """files: [(path bytes, base, size)] in manifest order."""
    if len(files) != FILES:
        return E.FILE_COUNT
    for role, (path, base, size) in enumerate(files):
        if code := check_file(path, base, size, role):
            return code
    return 0


def _string_ok(b):
    n = 0
    while n < len(b) and b[n]:
        c = b[n]
        if not (0x41 <= c <= 0x5A or 0x61 <= c <= 0x7A or 0x30 <= c <= 0x39 or c in b'._-'):
            return False
        n += 1
    return 0 < n < len(b) and _zero(b[n:])


def _pin(b, name):
    return get(b, PIN_LAYOUT, name)


def _pin_counts_ok(b):
    m, a, t, r, c = (_pin(b, n) for n in ('midr_count', 'avoid_count', 'acpi_count', 'gicr_count', 'guest_chunk_count'))
    return 1 <= m <= MIDR_MAX and a <= AVOID_MAX and t <= ACPI_MAX and 1 <= r <= GICR_MAX and 1 <= c <= CHUNKS_MAX


def _pin_reserved_ok(b):
    m, a, t = _pin(b, 'midr_count'), _pin(b, 'avoid_count'), _pin(b, 'acpi_count')
    if _pin(b, 'reserved0') or _pin(b, 'reserved1') or _pin(b, 'reserved2') or not _zero(_pin(b, 'reserved3')):
        return False
    if any(_u32(b, 0xE0 + 4 * i) for i in range(m, MIDR_MAX)):
        return False
    for i in range(AVOID_MAX):
        e = b[0x180 + 24 * i:0x180 + 24 * (i + 1)]
        if (not _zero(e)) if i >= a else _u32(e, 20):
            return False
    return all(_zero(b[0x1E8 + 40 * i:0x1E8 + 40 * (i + 1)]) for i in range(t, ACPI_MAX))


def _pin_order_ok(b):
    m, a, t = _pin(b, 'midr_count'), _pin(b, 'avoid_count'), _pin(b, 'acpi_count')
    if any(_u32(b, 0xE0 + 4 * (i - 1)) >= _u32(b, 0xE0 + 4 * i) for i in range(1, min(m, MIDR_MAX))):
        return False
    for i in range(1, min(t, ACPI_MAX)):
        if not b[0x1E8 + 40 * (i - 1):0x1E8 + 40 * (i - 1) + 4] < b[0x1E8 + 40 * i:0x1E8 + 40 * i + 4]:
            return False
    return all(_u64(b, 0x180 + 24 * (i - 1)) < _u64(b, 0x180 + 24 * i) for i in range(1, min(a, AVOID_MAX)))


def _pin_values_ok(b):
    m, a, t = _pin(b, 'midr_count'), _pin(b, 'avoid_count'), _pin(b, 'acpi_count')
    gd, gf, gl, gs, n = (_pin(b, x) for x in ('gicd_base', 'gicr_first', 'gicr_limit', 'gicr_stride', 'gicr_count'))
    fbb, fbs, gsz = _pin(b, 'fb_bytes'), _pin(b, 'fb_base'), _pin(b, 'guest_file_size')
    w, h, st, intid = (_pin(b, x) for x in ('fb_width', 'fb_height', 'fb_stride', 'timer_intid'))
    if any(not _u32(b, 0xE0 + 4 * i) for i in range(min(m, MIDR_MAX))) or not _pin(b, 'cntfrq_hz'):
        return False
    if not gd or gd & 0xFFFF or not gf or gf & 0xFFFF or gs < 0x20000 or gs & 0xFFFF or _pin(b, 'its_base') & 0xFFFF:
        return False
    if not n or gs > (M64 - gf) // n or gl != gf + n * gs or gd > M64 - 0x10000 or (gd < gl and gf < gd + 0x10000) or not 16 <= intid <= 31:
        return False
    if not fbs or fbs & 0xFFF or not w or not h or st < w or _pin(b, 'fb_format') > 1:
        return False
    if st * h > M64 >> 2 or fbb < st * h * 4 or fbb > M64 - fbs:
        return False
    if any(_zero(_pin(b, x)) for x in ('descriptor_sha256', 'guest_file_sha256', 'guest_index_sha256')):
        return False
    if not GUEST_HEAD_BYTES + CHUNK_BYTES <= gsz <= FILE_MAX:
        return False
    prev_end = 0
    for i in range(min(a, AVOID_MAX)):
        base, size, tag = _u64(b, 0x180 + 24 * i), _u64(b, 0x188 + 24 * i), _u32(b, 0x190 + 24 * i)
        if (base | size) & 0xFFF or not size or size > M64 - base or (i and base < prev_end):
            return False
        if not AVOID_EL2_UNREACHABLE <= tag <= AVOID_OPERATOR:
            return False
        prev_end = base + size
    for i in range(min(t, ACPI_MAX)):
        e = b[0x1E8 + 40 * i:0x1E8 + 40 * (i + 1)]
        if not all(0x41 <= c <= 0x5A or 0x30 <= c <= 0x39 for c in e[:4]) or _u32(e, 4) < 36 or _zero(e[8:40]):
            return False
    return True


def _pin_geometry(b):
    pb, ps, gb, gs, rs, wb, ws = (_pin(b, x) for x in ('place_base', 'place_size', 'guest_base', 'guest_size', 'reserve_size',
                                                        'work_base', 'work_size'))
    if (gb | gs) & (GUEST_ALIGN - 1) or rs & (RESERVE_ALIGN - 1) or wb & (WORK_ALIGN - 1) or (pb | ps) & (RESERVE_ALIGN - 1):
        return E.PLACE_ALIGN
    e3 = gb + gs + rs + ws
    if e3 > M64 or gb + gs > M64 or gb + gs + rs > M64:
        return E.PLACE_LIMIT
    if not gb or gs < GUEST_MIN or rs < RESERVE_MIN or ws != WORK_BYTES or pb != gb or wb != gb + gs + rs or ps != gs + rs + ws:
        return E.PLACE_LAYOUT
    return E.PLACE_LIMIT if e3 > IPA_LIMIT else 0


def _pin_head(b):
    if len(b) < PIN_BYTES:
        return E.PIN_TRUNCATED
    if b[:8] != b'SNAPBPIN':
        return E.PIN_MAGIC
    if _pin(b, 'version') != VERSION:
        return E.PIN_VERSION
    return E.PIN_SIZE if len(b) != PIN_BYTES or _pin(b, 'size') != PIN_BYTES else 0


def _pin_body(b):
    if _pin(b, 'flags') or _pin(b, 'authorised_stores') & ~STORE_ALL:
        return E.PIN_FLAGS
    if not _string_ok(_pin(b, 'board_id')) or not _string_ok(_pin(b, 'build_label')):
        return E.PIN_STRING
    if not _pin_counts_ok(b):
        return E.PIN_COUNT
    if not _pin_reserved_ok(b):
        return E.PIN_RESERVED
    if not _pin_order_ok(b):
        return E.PIN_ORDER
    if not _pin_values_ok(b):
        return E.PIN_VALUE
    if code := _pin_geometry(b):
        return code
    if _pin(b, 'authorised_stores') & STORE_REQUIRED != STORE_REQUIRED:
        return E.STORE_NOT_AUTH
    return E.NO_AUTHORISATION if _zero(_pin(b, 'authorisation_sha256')) else 0


def check_pin(b, digest=True):
    """PIN.DAT: the header, then (with digest) the record digest, then every field rule."""
    if code := _pin_head(b):
        return code
    if digest and sha256(b[:PIN_BYTES - 32]) != _pin(b, 'record_sha256'):
        return E.PIN_DIGEST
    return _pin_body(b)


def check_observed(pin, o):
    """o: dict with OBSERVED_KEYS (registers, the monitor build's GIC macros and stores, the runner's framebuffer)."""
    midr = [_u32(pin, 0xE0 + 4 * i) for i in range(min(_pin(pin, 'midr_count'), MIDR_MAX))]
    if o['current_el'] != 8:
        return E.NOT_EL2
    if o['hcr_el2'] & (1 << 34):
        return E.E2H_SET
    if not o['sctlr_el2'] & 1 or (o['tcr_el2'] >> 14) & 3 or not 17 <= o['tcr_el2'] & 63 <= 24:
        return E.EL2_REGIME
    if o['midr_el1'] not in midr:
        return E.MIDR_MISMATCH
    if o['mpidr_el1'] != _pin(pin, 'boot_mpidr'):
        return E.MPIDR_MISMATCH
    if o['cntfrq_el0'] != _pin(pin, 'cntfrq_hz'):
        return E.CNTFRQ_MISMATCH
    if any(o[k] != _pin(pin, p) for k, p in (('build_gicd_base', 'gicd_base'), ('build_gicr_first', 'gicr_first'),
                                              ('build_gicr_limit', 'gicr_limit'), ('build_gicr_stride', 'gicr_stride'),
                                              ('build_timer_intid', 'timer_intid'))):
        return E.GIC_MISMATCH
    if any(o[k] != _pin(pin, k) for k in ('fb_base', 'fb_bytes', 'fb_width', 'fb_height', 'fb_stride', 'fb_format')):
        return E.GOP_MISMATCH
    return E.STORE_NOT_AUTH if o['build_stores'] & ~_pin(pin, 'authorised_stores') & M64 else 0


def check_place(pin, memory_map, stride, fb_base, fb_bytes, declared):
    """memory_map: the final EFI map as bytes; declared: [(base, size)]."""
    pb, ps, a = _pin(pin, 'place_base'), _pin(pin, 'place_size'), _pin(pin, 'avoid_count')
    if not ps or ps > M64 - pb:
        return E.PLACE_LAYOUT
    pe = pb + ps
    if memory_map is None or stride < MAP_MIN_STRIDE or not len(memory_map) or len(memory_map) % stride or len(memory_map) // stride > MAP_MAX:
        return E.MAP_BAD
    hit, other, attr = 0, False, 0
    for i in range(len(memory_map) // stride):
        d = memory_map[i * stride:i * stride + stride]
        start, pages = _u64(d, 8), _u64(d, 24)
        if start & 0xFFF or not pages or pages > M64 >> 12 or pages << 12 > M64 - start:
            return E.MAP_BAD
        end = start + (pages << 12)
        if start < pe and pb < end:
            if _u32(d, 0) == EFI_CONVENTIONAL and start <= pb and pe <= end:
                hit, attr = hit + 1, _u64(d, 32)
            else:
                other = True
    if hit != 1 or other:
        return E.PLACE_NOT_CONV
    if not attr & EFI_WB:
        return E.PLACE_ATTR
    if len(declared) > DECLARED_MAX or any(_overlap(pb, ps, base, size) for base, size in declared):
        return E.PLACE_DECLARED
    if _overlap(pb, ps, fb_base, fb_bytes):
        return E.PLACE_FB
    if a > AVOID_MAX or any(_overlap(pb, ps, _u64(pin, 0x180 + 24 * k), _u64(pin, 0x188 + 24 * k)) for k in range(a)):
        return E.PLACE_AVOID
    return 0


def _guest_check(f, length, bind):
    """bind = (load_base, load_size, file_size, chunk_count, index digest): what PIN.DAT pins (or what the packer just built)."""
    g = lambda name: get(f, GUEST_LAYOUT, name)
    if length < GUEST_HEAD_BYTES:
        return E.GUEST_TRUNCATED
    if f[:8] != b'SNAPBGST':
        return E.GUEST_MAGIC
    if g('version') != VERSION:
        return E.GUEST_VERSION
    if g('flags'):
        return E.GUEST_FLAGS
    if g('head_size') != GUEST_HEAD_BYTES or g('entry_size') != CHUNK_BYTES or g('table_offset') != GUEST_HEAD_BYTES:
        return E.GUEST_SIZE
    if g('reserved0') or not _zero(g('reserved1')):
        return E.GUEST_RESERVED
    file_size = g('file_size')
    if length < file_size:
        return E.GUEST_TRUNCATED
    if length > file_size or file_size > FILE_MAX:
        return E.GUEST_SIZE
    count = g('entry_count')
    if not 1 <= count <= CHUNKS_MAX:
        return E.GUEST_COUNT
    table_end = GUEST_HEAD_BYTES + CHUNK_BYTES * count
    if table_end > file_size:
        return E.GUEST_COUNT
    if g('data_offset') != table_end:
        return E.GUEST_SIZE
    load_base, load_size = g('load_base'), g('load_size')
    if (load_base, load_size) != bind[:2]:
        return E.GUEST_LOAD
    if (file_size, count, g('index_sha256')) != bind[2:]:
        return E.GUEST_PIN
    expect, prev_dest, prev_end, seen = table_end, 0, 0, [0] * (KIND_MAX + 1)
    for i in range(count):
        c = unpack(f[GUEST_HEAD_BYTES + CHUNK_BYTES * i:GUEST_HEAD_BYTES + CHUNK_BYTES * (i + 1)], CHUNK_LAYOUT)
        dest, size, foff, kind, flags = c['dest'], c['size'], c['file_offset'], c['kind'], c['flags']
        zero = bool(flags & CHUNK_ZERO)
        if not 1 <= kind <= KIND_MAX:
            return E.CHUNK_KIND
        if flags & ~CHUNK_ZERO:
            return E.CHUNK_FLAGS
        if c['reserved']:
            return E.CHUNK_RESERVED
        if (foff or not _zero(c['sha256'])) if zero else _zero(c['sha256']):
            return E.CHUNK_ZERO
        if (dest | size) & (DEST_ALIGN - 1) or not size:
            return E.CHUNK_GEOMETRY
        if dest < load_base or dest - load_base > load_size or size > load_size - (dest - load_base):
            return E.CHUNK_RANGE
        if i and dest < prev_dest:
            return E.CHUNK_ORDER
        if i and dest < prev_end:
            return E.CHUNK_OVERLAP
        if not zero:
            if foff > file_size or size > file_size - foff:
                return E.CHUNK_EOF
            aligned = (expect + DATA_ALIGN - 1) & ~(DATA_ALIGN - 1)
            if foff != aligned:
                return E.CHUNK_OFFSET
            if not _zero(f[expect:aligned]):
                return E.PAD_NONZERO
            expect = foff + size
        seen[kind] += 1
        prev_dest, prev_end = dest, dest + size
    if expect != file_size:
        return E.GUEST_SIZE
    if seen[KIND_PARAMS] > 1 or seen[KIND_DEVICE_TREE] > 1:
        return E.CHUNK_DUP
    if not (seen[KIND_KERNEL] and seen[KIND_TABLES] and seen[KIND_HANDOFF] and seen[KIND_DEVICE_TREE] and seen[KIND_PARAMS]):
        return E.CHUNK_MISSING
    return 0


def check_guest(f, pin):
    """GUEST.DAT structure and its binding to PIN.DAT; no hashing."""
    bind = (_pin(pin, 'guest_base'), _pin(pin, 'guest_size'), _pin(pin, 'guest_file_size'), _pin(pin, 'guest_chunk_count'),
            _pin(pin, 'guest_index_sha256'))
    return _guest_check(f, len(f), bind)


def params_dest(f):
    """x3 of the monitor call: the destination of the one PARAMS chunk, 0 when there is none."""
    if len(f) < GUEST_HEAD_BYTES:
        return 0
    count = get(f, GUEST_LAYOUT, 'entry_count')
    if count > CHUNKS_MAX or GUEST_HEAD_BYTES + CHUNK_BYTES * count > len(f):
        return 0
    for i in range(count):
        c = unpack(f[GUEST_HEAD_BYTES + CHUNK_BYTES * i:GUEST_HEAD_BYTES + CHUNK_BYTES * (i + 1)], CHUNK_LAYOUT)
        if c['kind'] == KIND_PARAMS:
            return c['dest']
    return 0


def check_digests(f, pin, policy):
    """The table digest always; per-chunk and whole-file by policy. Returns (code, failing chunk or None)."""
    if policy & ~(HASH_CHUNKS | HASH_FILE):
        return E.HASH_POLICY, None
    if len(f) < GUEST_HEAD_BYTES:
        return E.GUEST_TRUNCATED, None
    count = get(f, GUEST_LAYOUT, 'entry_count')
    if not 1 <= count <= CHUNKS_MAX or GUEST_HEAD_BYTES + CHUNK_BYTES * count > len(f):
        return E.GUEST_COUNT, None
    if sha256(f[GUEST_HEAD_BYTES:GUEST_HEAD_BYTES + CHUNK_BYTES * count]) != get(f, GUEST_LAYOUT, 'index_sha256'):
        return E.INDEX_DIGEST, None
    for i in range(count if policy & HASH_CHUNKS else 0):
        c = unpack(f[GUEST_HEAD_BYTES + CHUNK_BYTES * i:GUEST_HEAD_BYTES + CHUNK_BYTES * (i + 1)], CHUNK_LAYOUT)
        if c['flags'] & CHUNK_ZERO:
            continue
        if c['file_offset'] > len(f) or c['size'] > len(f) - c['file_offset']:
            return E.CHUNK_EOF, None
        if sha256(f[c['file_offset']:c['file_offset'] + c['size']]) != c['sha256']:
            return E.CHUNK_DIGEST, i
    if policy & HASH_FILE and sha256(f) != _pin(pin, 'guest_file_sha256'):
        return E.FILE_DIGEST, None
    return 0, None


def check_report(b):
    """REPORT.DAT as SNAPREP reads it; the monitor's window is not looked at."""
    if b is None or len(b) != REPORT_BYTES:
        return E.REPORT_SIZE
    if b[:8] != b'SNAPBRPT':
        return E.REPORT_MAGIC
    r = lambda name: get(b, REPORT_LAYOUT, name)
    if r('version') != VERSION:
        return E.REPORT_VERSION
    if r('head_size') != REPORT_HEAD_BYTES:
        return E.REPORT_SIZE
    if not _zero(b[REPORT_HEAD_BYTES:REPORT_HEAD_PAGE]):
        return E.REPORT_RESERVED
    stage, refusal, total = r('stage'), r('refusal'), r('chunks_total')
    known = lambda code: code in _NAME
    if (stage > len(STAGES) - 1 or (refusal and not known(refusal)) or r('hash_policy') & ~(HASH_CHUNKS | HASH_FILE) or
            r('lines_printed') > WRAPPER_LINES or total > CHUNKS_MAX or r('chunks_copied') > total or r('chunks_hashed') > total or
            (r('pin_code') and not known(r('pin_code'))) or (r('guest_code') and not known(r('guest_code'))) or
            (refusal and stage > STAGE.MONITOR_ENTERED)):
        return E.REPORT_VALUE
    return 0


def preflight(env):
    """The wrapper's order: args, files, PIN.DAT, observed, placement, GUEST.DAT structure, GUEST.DAT digests.
    env: dict(args=(magic, version, size), files, pin, guest, obs, map, stride, declared, policy). Returns (code, chunk)."""
    if code := check_args(*env['args']):
        return code, None
    if code := check_files(env['files']):
        return code, None
    if code := check_pin(env['pin']):
        return code, None
    if code := check_observed(env['pin'], env['obs']):
        return code, None
    obs = env['obs']
    if code := check_place(env['pin'], env['map'], env['stride'], obs['fb_base'], obs['fb_bytes'], env['declared']):
        return code, None
    if code := check_guest(env['guest'], env['pin']):
        return code, None
    return check_digests(env['guest'], env['pin'], env['policy'])


def xorshift(state):
    """The fuzz generator shared with the C harness: xorshift64*. Returns (output, next state)."""
    state ^= state >> 12
    state ^= (state << 25) & M64
    state ^= state >> 27
    return (state * 0x2545F4914F6CDD1D) & M64, state


def main(argv):
    if len(argv) == 2 and argv[1] == 'layout':
        print('\n'.join(layout_lines()))
    elif len(argv) == 2 and argv[1] == 'errors':
        print('\n'.join(f'{code:3d} {name:<{NAME_MAX}} {"B" if sides & 1 else "-"}{"W" if sides & 2 else "-"}{"R" if sides & 4 else "-"}'
                        for code, name, sides in ERRORS))
    elif len(argv) == 3 and argv[1] == 'check-pin':
        code = check_pin(open(argv[2], 'rb').read())
        print(f'{code} {error_name(code)}')
        return 1 if code else 0
    elif len(argv) in (4, 6) and argv[1] == 'check-guest':
        guest, pin = open(argv[2], 'rb').read(), open(argv[3], 'rb').read()
        if code := check_pin(pin):
            print(f'{code} {error_name(code)} (PIN.DAT)')
            return 1
        policy = 0
        if len(argv) == 6 and argv[4] == '--hash':
            policy = sum({'chunks': HASH_CHUNKS, 'file': HASH_FILE}[w] for w in argv[5].split(',') if w)
        code = check_guest(guest, pin) or check_digests(guest, pin, policy)[0]
        print(f'{code} {error_name(code)}')
        return 1 if code else 0
    else:
        print(__doc__.split('\n\n')[0], file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
