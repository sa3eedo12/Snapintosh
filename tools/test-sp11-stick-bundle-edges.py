#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Edge tests for the SP11 stick bundle checks: probes/sp11-stick-bundle.h, through the C harness of tools/test-sp11-stick-bundle.c
(AddressSanitizer and UndefinedBehaviorSanitizer), and tools/sp11_stick_bundle.py, on the same bytes.

    python3 tools/test-sp11-stick-bundle-edges.py
    Q1N1_SP11_TREE=<q1n1 checkout> python3 tools/test-sp11-stick-bundle-edges.py     (a path that is no checkout: the runner comparison SKIPs)

tools/test-sp11-stick-bundle.py owns the fixtures and the refusal families; this file imports them by path. Here every limit is pinned from
both sides (the largest value accepted, the smallest refused, the neighbours the grid allows), each case breaks or touches one rule only, so a
guard weakened by one comparison, one constant or one dropped term makes some case answer differently; and the order of the checks that
docs/SP11-STICK-BUNDLE.md section 8 states is held to what the code does. A case passes when the C header and the Python checks give the
same verdict and it is the one the case names. No hardware; the fixtures are synthetic. Evidence: EXPERIMENTAL."""
import importlib.util
import struct
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOC = HERE.parent / 'docs' / 'SP11-STICK-BUNDLE.md'


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


T = _load('sp11_bundle_fixtures', 'test-sp11-stick-bundle.py')
sb, E, MIB = T.sb, T.E, T.MIB
M64 = (1 << 64) - 1
LOW, GS, TOP = T.LOW_BASE, 256 * MIB, 1 << 36
FILE_MAX, REPORT = sb.FILE_MAX, sb.REPORT_BYTES
_FIXTURE = T.fixture()
PB, PS = (sb.get(_FIXTURE.pin, sb.PIN_LAYOUT, name) for name in ('place_base', 'place_size'))
PE, PAGES = PB + PS, PS >> 12


def case(name, expected, mode, *mutators, bank=False, policy=None):
    """(name, expected verdict name or (name, failing chunk), mode, scenario): the fixture, then each mutator in turn."""
    s = T.fixture(bank)
    for mutate in mutators:
        mutate(s)
    if policy is not None:
        s.policy = policy
    return name, expected, mode, s


# ---- Mutators ----------------------------------------------------------------------------------------------------------
def placed(base, guest=GS, reserve=48 * MIB, work=240 * MIB, **over):
    """The placement fields for these sizes, consistent by the Shape rule unless `over` says otherwise; sums wrap as the C unsigned arithmetic does."""
    fields = dict(guest_base=base, guest_size=guest, reserve_size=reserve, work_size=work, work_base=(base + guest + reserve) & M64,
                  place_base=base, place_size=(guest + reserve + work) & M64)
    fields.update(over)
    return T.P(**fields)


def gic(first=0x17080000, stride=0x40000, count=12, dist=0x17000000, its=0x17040000, limit=None):
    return T.P(gicd_base=dist, gicr_first=first, gicr_stride=stride, gicr_count=count, its_base=its,
               gicr_limit=first + count * stride if limit is None else limit)


def midr(*values):
    def mutate(s):
        s.pin[0xE0:0xF0] = struct.pack('<4I', *values, *[0] * (4 - len(values)))
        sb.put(s.pin, sb.PIN_LAYOUT, 'midr_count', len(values))
        sb.pin_digest(s.pin)
    return mutate


def avoid(*entries):
    def mutate(s):
        s.pin[0x180:0x1E0] = bytes(96)
        for i, (base, size, tag) in enumerate(entries):
            s.pin[0x180 + 24 * i:0x180 + 24 * i + 24] = struct.pack('<QQII', base, size, tag, 0)
        sb.put(s.pin, sb.PIN_LAYOUT, 'avoid_count', len(entries))
        sb.pin_digest(s.pin)
    return mutate


def acpi(*entries):
    """entries: (signature, length); the digest is the signature's SHA-256, never zero."""
    def mutate(s):
        s.pin[0x1E8:0x328] = bytes(320)
        for i, (sig, length) in enumerate(entries):
            s.pin[0x1E8 + 40 * i:0x1E8 + 40 * i + 40] = struct.pack('<4sI32s', sig, length, sb.sha256(sig))
        sb.put(s.pin, sb.PIN_LAYOUT, 'acpi_count', len(entries))
        sb.pin_digest(s.pin)
    return mutate


def tcr(t0sz=20, tg0=0):
    return T.O(tcr_el2=(0x80843514 & ~0xC03F) | (tg0 << 14) | t0sz)


def desc_map(descs, stride=48):
    """EFI descriptors (type, start, pages, attribute), each padded to `stride` with bytes the check must not read."""
    return b''.join(struct.pack('<IIQQQQ', t, 0, start, 0, pages, attr).ljust(stride, b'\xa5') for t, start, pages, attr in descs)


def with_map(descs, stride=48):
    def mutate(s):
        s.map, s.stride = desc_map(descs, stride), stride
    return mutate


def odd_stride_map(stride):
    """Two records `stride` bytes apart that are a valid map if that is the stride: the covering descriptor, whose attribute word is the first
    8 bytes of the second record. Only the check of the stride refuses it."""
    m = bytearray(2 * stride)
    struct.pack_into('<IIQQQ', m, stride, 15 if stride == 32 else 0, 0, 0x1000, 0, 1)
    struct.pack_into('<IIQQ', m, 0, 7, 0, PB, 0)
    struct.pack_into('<Q', m, 24, PAGES)
    if stride > 32:
        m[32] = 0x0F
    return bytes(m)


def with_odd_stride(stride):
    def mutate(s):
        s.map, s.stride = odd_stride_map(stride), stride
    return mutate


def declare(*ranges):
    return lambda s: setattr(s, 'declared', [list(r) for r in ranges])


def framebuffer(base, size):
    return T.O(fb_base=base, fb_bytes=size)


def raw_guest(entries, tail=b'', size=None, count=None, load=(LOW, GS)):
    """The head and table of a GUEST.DAT from raw entries (dest, size, file_offset, kind, flags, sha256), then `tail`; the head is right and
    carries the table's digest, so the checks reach the entries."""
    table = b''.join(struct.pack('<QQQHHI32s', d, z, o, k, f, 0, h) for d, z, o, k, f, h in entries)
    head = bytearray(sb.GUEST_HEAD_BYTES)
    for name, value in (('magic', b'SNAPBGST'), ('version', 1), ('head_size', 128), ('entry_size', 64), ('entry_count', len(entries) if count is None else count),
                        ('table_offset', 128), ('data_offset', 128 + len(table)), ('file_size', 128 + len(table) + len(tail) if size is None else size),
                        ('load_base', load[0]), ('load_size', load[1]), ('index_sha256', sb.sha256(table))):
        sb.put(head, sb.GUEST_LAYOUT, name, value)
    return bytes(head) + table + tail


def refit(s):
    """GUEST.DAT's file_size and PIN.DAT's guest_file_size follow the length of the file again."""
    sb.put(s.guest, sb.GUEST_LAYOUT, 'file_size', len(s.guest))
    T.P(guest_file_size=len(s.guest))(s)


def with_guest(guest):
    def mutate(s):
        s.guest = bytearray(guest)
        T.P(**sb.guest_pin_fields(guest))(s)
    return mutate


_MANY = {}


def many(n):
    """A valid GUEST.DAT of n chunks: the five required kinds, then OTHER; 16 bytes of data each, 64 KiB apart."""
    if n not in _MANY:
        kinds = (sb.KIND_HANDOFF, sb.KIND_PARAMS, sb.KIND_DEVICE_TREE, sb.KIND_KERNEL, sb.KIND_TABLES)
        _MANY[n] = sb.pack_guest(LOW, GS, [dict(kind=kinds[i] if i < 5 else sb.KIND_OTHER, dest=LOW + 0x10000 * i, data=T.noise(f'many{i}', 16))
                                           for i in range(n)])
    return _MANY[n]


def sparse_guest(total):
    """The head and table of a GUEST.DAT of exactly `total` bytes whose one data chunk (KERNEL) is zeros up to the end of the file, and the
    PIN.DAT fields that bind it: the file itself is these bytes and then zeros."""
    data_at, z = 128 + 64 * 5, sb.CHUNK_ZERO
    size = total - data_at
    entries = [(LOW, size, data_at, sb.KIND_KERNEL, 0, sb.sha256(b'kernel'))]
    entries += [(LOW + size + 0x1000 * i, 0x1000, 0, kind, z, bytes(32)) for i, kind in
                enumerate((sb.KIND_TABLES, sb.KIND_HANDOFF, sb.KIND_DEVICE_TREE, sb.KIND_PARAMS))]
    prefix = raw_guest(entries, size=total, load=(LOW, 512 * MIB))
    fields = dict(sb.placement(LOW, 512 * MIB), guest_file_size=total, guest_chunk_count=5, guest_file_sha256=sb.sha256(b'sparse'),
                  guest_index_sha256=prefix[0x48:0x68])
    return prefix, fields


class Padded:
    """A scenario whose GUEST.DAT is `prefix` and then zeros up to `total` bytes: Python gets the whole file, the harness `hex+count` (no 512 MiB of text)."""

    def __init__(self, s, prefix, total):
        self._s, self._prefix, self._total = s, bytes(prefix), total
        s.guest = self._prefix + bytes(total - len(self._prefix))

    def __getattr__(self, name):
        return getattr(self._s, name)

    def text(self, name, mode):
        whole, self._s.guest = self._s.guest, bytearray(self._prefix)
        try:
            text = self._s.text(name, mode)
        finally:
            self._s.guest = whole
        return text.replace(f'GUEST {self._prefix.hex()}\n', f'GUEST {self._prefix.hex()}+{self._total - len(self._prefix):x}\n')


TAIL = bytes(range(48))


def entry(dest=LOW, size=16, foff=0, kind=sb.KIND_KERNEL, flags=0, sha=None):
    return dest, size, foff, kind, flags, sb.sha256(b'entry') if sha is None else sha


def digest_case(name, expected, entries, policy=1, tail=TAIL, *after):
    """A hand-built GUEST.DAT, right head and table digest, entries given as a function of the file's length, run through the digest step."""
    count = len(entries(0))
    guest = raw_guest(entries(128 + 64 * count + len(tail)), tail)
    return case(name, expected, 'digests', lambda s: setattr(s, 'guest', bytearray(guest)), *after, policy=policy)


class Edges(T.Native):
    def run_cases(self, cases):
        """One harness run for the cases; each verdict is held to the Python checks and to the case."""
        out = T.run_harness(['batch'], ''.join(s.text(name, mode) for name, _, mode, s in cases)).splitlines()
        self.assertEqual(len(out), len(cases))
        for line, (name, expected, mode, s) in zip(out, cases):
            with self.subTest(case=name):
                want, bad = expected if isinstance(expected, tuple) else (expected, None)
                got_name, got_mode, code, code_name, got_bad = line.split()
                self.assertEqual((got_name, got_mode), (name, mode))
                py_code, py_bad = T.py_run(s, mode)
                self.assertEqual((int(code), code_name), (py_code, sb.error_name(py_code)), 'C and Python disagree')
                self.assertEqual(int(got_bad) if int(got_bad) >= 0 else None, py_bad)
                self.assertEqual(code_name, want, f'{name}: both say {code_name}, the case expects {want}')
                if bad is not None:
                    self.assertEqual(py_bad, bad)


class Files(Edges):
    def test_sizes_bases_and_the_end_of_the_address_space(self):
        c = []
        for slot in range(4):
            for size, want in ((0, 'FILE_SIZE'), (1, 'OK'), (FILE_MAX - 1, 'OK'), (FILE_MAX, 'OK'), (FILE_MAX + 1, 'FILE_SIZE'), (FILE_MAX + 0x1000, 'FILE_SIZE')):
                c.append(case(f'slot{slot}_size_{size:#x}', want, 'files', T.set_file(slot, size=size)))
        for size, want in ((REPORT, 'OK'), (REPORT - 1, 'FILE_SIZE'), (REPORT + 1, 'FILE_SIZE'), (REPORT + 0x1000, 'FILE_SIZE'), (FILE_MAX, 'FILE_SIZE')):
            c.append(case(f'report_size_{size:#x}', want, 'files', T.set_file(4, size=size)))
        for offset, want in ((0, 'OK'), (1, 'FILE_BASE'), (0x10, 'FILE_BASE'), (0x100, 'FILE_BASE'), (0x800, 'FILE_BASE'), (0xFFF, 'FILE_BASE')):
            c.append(case(f'base_plus_{offset:#x}', want, 'files', T.set_file(0, base=0xB4001000 + offset)))
        c += [case('base_zero', 'FILE_BASE', 'files', T.set_file(0, base=0)), case('lowest_page', 'OK', 'files', T.set_file(0, base=0x1000))]
        top = (1 << 64) - 0x1000
        for base, size, want in ((top, 0xFFF, 'OK'), (top, 0x1000, 'FILE_BASE'), (top - 0x1000, 0x1000, 'OK'), (top - 0x1000, 0x2000, 'FILE_BASE'), (top, FILE_MAX, 'FILE_BASE')):
            c.append(case(f'ends_at_{base + size:#x}', want, 'files', T.set_file(1, base=base, size=size)))
        self.run_cases(c)

    def test_the_slots_are_checked_one_after_the_other(self):
        wrong = b'\\SP11\\WRONG.BIN'
        self.run_cases([
            case('count_before_the_slots', 'FILE_COUNT', 'files', T.set_file(0, size=0), lambda s: s.files.append(list(s.files[0]))),
            case('slot0_size_before_slot1_role', 'FILE_SIZE', 'files', T.set_file(0, size=0), T.set_path(1, wrong)),
            case('slot0_role_before_slot4_size', 'FILE_ROLE', 'files', T.set_path(0, wrong), T.set_file(4, size=1)),
            case('slot0_base_before_slot1_role', 'FILE_BASE', 'files', T.set_file(0, base=0), T.set_path(1, wrong)),
            case('role_before_base_and_size_of_one_slot', 'FILE_ROLE', 'files', T.set_path(2, wrong), T.set_file(2, base=0, size=0)),
            case('base_before_size_of_one_slot', 'FILE_BASE', 'files', T.set_file(2, base=0, size=0)),
            case('slot4_alone_is_checked_like_the_others', 'FILE_SIZE', 'files', T.set_file(4, size=REPORT - 1))])


class PinLimits(Edges):
    def pin(self, cases):
        self.run_cases([case(name, want, 'pinbody', *mutators) for name, want, *mutators in cases])

    def test_counts_and_arrays_at_each_end(self):
        c = [(f'guest_file_size_{n:#x}', want, T.P(guest_file_size=n)) for n, want in
             ((0, 'PIN_VALUE'), (191, 'PIN_VALUE'), (192, 'OK'), (193, 'OK'), (FILE_MAX - 1, 'OK'), (FILE_MAX, 'OK'), (FILE_MAX + 1, 'PIN_VALUE'))]
        c += [(f'guest_chunk_count_{n}', want, T.P(guest_chunk_count=n)) for n, want in
              ((0, 'PIN_COUNT'), (1, 'OK'), (1023, 'OK'), (1024, 'OK'), (1025, 'PIN_COUNT'), (0xFFFFFFFF, 'PIN_COUNT'))]
        c += [('midr_1', 'OK', midr(0x511F0011)), ('midr_4', 'OK', midr(1, 2, 3, 4)), ('midr_5', 'PIN_COUNT', T.P(midr_count=5)),
              ('midr_equal', 'PIN_ORDER', midr(5, 5)), ('midr_ascending', 'OK', midr(5, 6)), ('midr_descending', 'PIN_ORDER', midr(6, 5)),
              ('midr_zero', 'PIN_VALUE', midr(0, 1)), ('midr_slot_after_the_count', 'PIN_RESERVED', midr(5), T.PIN_AT(0xE4, 7))]
        c += [('avoid_0', 'OK', avoid()), ('avoid_4', 'OK', avoid(*[(0x10000000 + 0x1000000 * i, 0x1000, 1 + i % 3) for i in range(4)])),
              ('avoid_5', 'PIN_COUNT', T.P(avoid_count=5))]
        c += [(f'avoid_tag_{tag}', want, avoid((0x10000000, 0x100000, tag))) for tag, want in
              ((0, 'PIN_VALUE'), (1, 'OK'), (2, 'OK'), (3, 'OK'), (4, 'PIN_VALUE'), (0xFFFFFFFF, 'PIN_VALUE'))]
        top = M64 + 1
        c += [('avoid_base_unaligned', 'PIN_VALUE', avoid((0x10000001, 0x1000, 1))), ('avoid_size_unaligned', 'PIN_VALUE', avoid((0x10000000, 0x1001, 1))),
              ('avoid_empty', 'PIN_VALUE', avoid((0x10000000, 0, 1))), ('avoid_wraps', 'PIN_VALUE', avoid((top - 0x1000, 0x2000, 1))),
              ('avoid_ends_at_2_64', 'PIN_VALUE', avoid((top - 0x1000, 0x1000, 1))), ('avoid_ends_below_2_64', 'OK', avoid((top - 0x2000, 0x1000, 1))),
              ('avoid_overlap', 'PIN_VALUE', avoid((0x10000000, 0x2000, 1), (0x10001000, 0x1000, 2))),
              ('avoid_touching', 'OK', avoid((0x10000000, 0x1000, 1), (0x10001000, 0x1000, 2))),
              ('avoid_equal_bases', 'PIN_ORDER', avoid((0x10000000, 0x1000, 1), (0x10000000, 0x1000, 2)))]
        c += [('acpi_0', 'OK', acpi()), ('acpi_8', 'OK', acpi(*[(b'AP0' + bytes([0x31 + i]), 36) for i in range(8)])), ('acpi_9', 'PIN_COUNT', T.P(acpi_count=9)),
              ('acpi_equal', 'PIN_ORDER', acpi((b'GTDT', 36), (b'GTDT', 36))), ('acpi_ascending', 'OK', acpi((b'APIC', 36), (b'GTDT', 36))),
              ('acpi_descending', 'PIN_ORDER', acpi((b'GTDT', 36), (b'APIC', 36))), ('acpi_length_36', 'OK', acpi((b'APIC', 36))),
              ('acpi_length_35', 'PIN_VALUE', acpi((b'APIC', 35))), ('acpi_length_max', 'OK', acpi((b'APIC', 0xFFFFFFFF))),
              ('acpi_zero_digest', 'PIN_VALUE', acpi((b'APIC', 36)), T.PIN_AT(0x1E8 + 8, 0, 8), T.PIN_AT(0x1E8 + 16, 0, 8), T.PIN_AT(0x1E8 + 24, 0, 8),
               T.PIN_AT(0x1E8 + 32, 0, 8))]
        for sig, want in ((b'A0Z9', 'OK'), (b'@000', 'PIN_VALUE'), (b'[000', 'PIN_VALUE'), (b'/000', 'PIN_VALUE'), (b':000', 'PIN_VALUE'), (b'a000', 'PIN_VALUE'),
                          (b'0@00', 'PIN_VALUE'), (b'00[0', 'PIN_VALUE'), (b'000:', 'PIN_VALUE'), (b'0Z90', 'OK'), (b'\x00' + b'0AB', 'PIN_VALUE')):
            c.append((f'acpi_signature_{sig.hex()}', want, acpi((sig, 36))))
        c += [('gicr_count_0', 'PIN_COUNT', T.P(gicr_count=0)), ('gicr_count_1', 'OK', gic(count=1)), ('gicr_count_32', 'OK', gic(count=32)),
              ('gicr_count_33', 'PIN_COUNT', T.P(gicr_count=33))]
        self.pin(c)

    def test_gic_framebuffer_and_strings(self):
        gf, gl = 0x17080000, 0x17380000
        c = [(f'gicr_stride_{n:#x}', want, gic(stride=n)) for n, want in
             ((0, 'PIN_VALUE'), (0x10000, 'PIN_VALUE'), (0x1FFFF, 'PIN_VALUE'), (0x20000, 'OK'), (0x20001, 'PIN_VALUE'), (0x30000, 'OK'), (0x40000, 'OK'),
              (0x40001, 'PIN_VALUE'))]
        c += [('gicr_limit_one_frame_less', 'PIN_VALUE', T.P(gicr_limit=gl - 0x10000)), ('gicr_limit_one_frame_more', 'PIN_VALUE', T.P(gicr_limit=gl + 0x10000)),
              ('gicd_at_the_window_start', 'PIN_VALUE', gic(dist=gf)), ('gicd_just_below_the_window', 'OK', gic(dist=gf - 0x10000)),
              ('gicd_in_the_last_frame', 'PIN_VALUE', gic(dist=gl - 0x10000)), ('gicd_at_the_window_end', 'OK', gic(dist=gl)),
              ('gicd_unaligned', 'PIN_VALUE', gic(dist=0x17000000 + 0x8000)), ('gicd_zero', 'PIN_VALUE', gic(dist=0)), ('gicr_first_zero', 'PIN_VALUE', gic(first=0)),
              ('gicr_first_unaligned', 'PIN_VALUE', gic(first=gf + 1)), ('its_unaligned', 'PIN_VALUE', gic(its=0x17040001)), ('its_absent', 'OK', gic(its=0))]
        c += [(f'timer_intid_{n}', want, T.P(timer_intid=n)) for n, want in
              ((0, 'PIN_VALUE'), (15, 'PIN_VALUE'), (16, 'OK'), (26, 'OK'), (31, 'OK'), (32, 'PIN_VALUE'), (0xFFFFFFFF, 'PIN_VALUE'))]
        fb = 2880 * 1920 * 4
        c += [('fb_bytes_exact', 'OK'), ('fb_bytes_one_pixel_short', 'PIN_VALUE', T.P(fb_bytes=fb - 4)), ('fb_bytes_one_byte_short', 'PIN_VALUE', T.P(fb_bytes=fb - 1)),
              ('fb_bytes_one_more', 'OK', T.P(fb_bytes=fb + 1)), ('fb_bytes_zero', 'PIN_VALUE', T.P(fb_bytes=0)),
              ('fb_stride_below_width', 'PIN_VALUE', T.P(fb_stride=2879)), ('fb_stride_above_width_needs_bytes', 'PIN_VALUE', T.P(fb_stride=2881)),
              ('fb_stride_above_width', 'OK', T.P(fb_stride=2881, fb_bytes=2881 * 1920 * 4)), ('fb_format_0', 'OK', T.P(fb_format=0)),
              ('fb_format_1', 'OK', T.P(fb_format=1)), ('fb_format_2', 'PIN_VALUE', T.P(fb_format=2)), ('fb_width_zero', 'PIN_VALUE', T.P(fb_width=0)),
              ('fb_height_zero', 'PIN_VALUE', T.P(fb_height=0)), ('fb_base_zero', 'PIN_VALUE', T.P(fb_base=0)),
              ('fb_base_unaligned', 'PIN_VALUE', T.P(fb_base=0xE4800800))]
        c += [('cntfrq_zero', 'PIN_VALUE', T.P(cntfrq_hz=0))]
        c += [(f'{field}_zero', 'PIN_VALUE', T.P(**{field: T.Z})) for field in ('descriptor_sha256', 'guest_file_sha256', 'guest_index_sha256')]
        c += [('board_id_15_characters', 'OK', T.P(board_id=b'A' * 15)), ('board_id_16_characters', 'PIN_STRING', T.P(board_id=b'A' * 16)),
              ('build_label_23_characters', 'OK', T.P(build_label=b'B' * 23)), ('build_label_24_characters', 'PIN_STRING', T.P(build_label=b'B' * 24)),
              ('build_label_empty', 'PIN_STRING', T.P(build_label=b''))]
        for ch, want in (('-', 'OK'), ('.', 'OK'), ('_', 'OK'), ('0', 'OK'), ('9', 'OK'), ('A', 'OK'), ('Z', 'OK'), ('a', 'OK'), ('z', 'OK'), ('/', 'PIN_STRING'),
                         (':', 'PIN_STRING'), ('@', 'PIN_STRING'), ('[', 'PIN_STRING'), ('`', 'PIN_STRING'), ('{', 'PIN_STRING'), (',', 'PIN_STRING'),
                         (' ', 'PIN_STRING'), ('\x7f', 'PIN_STRING'), ('\xff', 'PIN_STRING')):
            c.append((f'string_character_{ord(ch):#04x}', want, T.P(build_label=ch.encode('latin-1') + b'x')))
        self.pin([(name, *rest) if len(rest) == 2 else (name, rest[0]) for name, *rest in c])

    def test_the_placement_arithmetic(self):
        b0 = TOP - 672 * MIB
        c = [('nominal', 'OK', placed(LOW)),
             ('guest_zero', 'PLACE_LAYOUT', placed(LOW, 0)), ('guest_160_MiB', 'PLACE_LAYOUT', placed(LOW, 160 * MIB)), ('guest_192_MiB', 'OK', placed(LOW, 192 * MIB)),
             ('guest_224_MiB', 'OK', placed(LOW, 224 * MIB)), ('guest_base_zero', 'PLACE_LAYOUT', placed(0)),
             ('reserve_zero', 'PLACE_LAYOUT', placed(LOW, reserve=0)), ('reserve_32_MiB', 'PLACE_LAYOUT', placed(LOW, reserve=32 * MIB)),
             ('reserve_48_MiB', 'OK', placed(LOW, reserve=48 * MIB)), ('reserve_64_MiB', 'OK', placed(LOW, reserve=64 * MIB)),
             ('work_zero', 'PLACE_LAYOUT', placed(LOW, work=0)), ('work_16_MiB_less', 'PLACE_LAYOUT', placed(LOW, work=224 * MIB)),
             ('work_16_MiB_more', 'PLACE_LAYOUT', placed(LOW, work=256 * MIB)), ('work_double', 'PLACE_LAYOUT', placed(LOW, work=480 * MIB)),
             ('work_64K_less_place_size_kept', 'PLACE_LAYOUT', placed(LOW, work=240 * MIB - 0x10000, place_size=GS + 48 * MIB + 240 * MIB)),
             ('work_64K_more_place_size_kept', 'PLACE_LAYOUT', placed(LOW, work=240 * MIB + 0x10000, place_size=GS + 48 * MIB + 240 * MIB)),
             ('work_64K_less_misaligns_place_size', 'PLACE_ALIGN', placed(LOW, work=240 * MIB - 0x10000)),
             ('work_64K_more_misaligns_place_size', 'PLACE_ALIGN', placed(LOW, work=240 * MIB + 0x10000)),
             ('work_base_64K_less', 'PLACE_LAYOUT', placed(LOW, work_base=LOW + GS + 48 * MIB - 0x10000)),
             ('work_base_64K_more', 'PLACE_LAYOUT', placed(LOW, work_base=LOW + GS + 48 * MIB + 0x10000)),
             ('place_size_16_MiB_less', 'PLACE_LAYOUT', placed(LOW, place_size=GS + 48 * MIB + 240 * MIB - 16 * MIB)),
             ('place_size_16_MiB_more', 'PLACE_LAYOUT', placed(LOW, place_size=GS + 48 * MIB + 240 * MIB + 16 * MIB)),
             ('place_base_32_MiB_more', 'PLACE_LAYOUT', placed(LOW, place_base=LOW + 32 * MIB)),
             ('place_base_4K_more', 'PLACE_ALIGN', placed(LOW, place_base=LOW + 0x1000)),
             ('place_size_4K_more', 'PLACE_ALIGN', placed(LOW, place_size=GS + 48 * MIB + 240 * MIB + 0x1000)),
             ('guest_base_16_MiB_off_the_grid', 'PLACE_ALIGN', placed(LOW + 16 * MIB)), ('reserve_49_MiB', 'PLACE_ALIGN', placed(LOW, reserve=49 * MIB)),
             ('work_base_4K_more', 'PLACE_ALIGN', placed(LOW, work_base=LOW + GS + 48 * MIB + 0x1000)),
             ('ends_at_2_36', 'OK', placed(b0, 384 * MIB)), ('ends_32_MiB_below_2_36', 'OK', placed(b0 - 32 * MIB, 384 * MIB)),
             ('ends_16_MiB_above_2_36', 'PLACE_LIMIT', placed(b0, 384 * MIB, reserve=64 * MIB)), ('ends_32_MiB_above_2_36', 'PLACE_LIMIT', placed(b0 + 32 * MIB, 384 * MIB)),
             ('starts_at_2_36', 'PLACE_LIMIT', placed(TOP, 192 * MIB)), ('starts_below_2_36_ends_above', 'PLACE_LIMIT', placed(TOP - 32 * MIB, 192 * MIB)),
             ('guest_sum_wraps', 'PLACE_LIMIT', placed(M64 + 1 - 32 * MIB)),
             ('reserve_sum_wraps', 'PLACE_LIMIT', placed(M64 + 1 - 448 * MIB, reserve=256 * MIB)),
             ('reserve_sum_reaches_2_64', 'PLACE_LIMIT', placed(M64 + 1 - 448 * MIB, reserve=192 * MIB)),
             ('work_sum_wraps', 'PLACE_LIMIT', placed(M64 + 1 - 448 * MIB, reserve=64 * MIB)),
             ('alignment_before_wrap', 'PLACE_ALIGN', placed(M64 + 1 - 16 * MIB)),
             ('wrap_before_layout', 'PLACE_LIMIT', placed(M64 + 1 - 32 * MIB, reserve=32 * MIB)),
             ('layout_before_the_end_limit', 'PLACE_LAYOUT', placed(TOP, 192 * MIB, reserve=32 * MIB))]
        self.pin(c)

    def test_store_classes_and_authorisation(self):
        self.pin([('stores_ram_only', 'STORE_NOT_AUTH', T.P(authorised_stores=1)), ('stores_without_ram', 'STORE_NOT_AUTH', T.P(authorised_stores=2)),
                  ('stores_with_gic', 'OK', T.P(authorised_stores=7)), ('stores_bit_3', 'PIN_FLAGS', T.P(authorised_stores=11)),
                  ('stores_bit_31', 'PIN_FLAGS', T.P(authorised_stores=0x80000003)), ('authorisation_zero', 'NO_AUTHORISATION', T.P(authorisation_sha256=T.Z)),
                  ('stores_before_authorisation', 'STORE_NOT_AUTH', T.P(authorised_stores=1, authorisation_sha256=T.Z)),
                  ('placement_before_stores', 'PLACE_ALIGN', T.P(authorised_stores=1), placed(LOW + 16 * MIB)),
                  ('placement_before_authorisation', 'PLACE_ALIGN', T.P(authorisation_sha256=T.Z), placed(LOW + 16 * MIB)),
                  ('values_before_placement', 'PIN_VALUE', T.P(cntfrq_hz=0), placed(LOW + 16 * MIB))])

    def test_the_rules_of_a_pin_run_in_the_order_the_note_gives(self):
        rules = [('PIN_FLAGS', T.P(flags=1)), ('PIN_STRING', T.P(board_id=b'')), ('PIN_COUNT', T.P(avoid_count=5)), ('PIN_RESERVED', T.P(reserved1=1)),
                 ('PIN_ORDER', acpi((b'GTDT', 36), (b'GTDT', 36))), ('PIN_VALUE', T.P(cntfrq_hz=0)), ('PLACE_LAYOUT', T.P(work_size=224 * MIB)),
                 ('STORE_NOT_AUTH', T.P(authorised_stores=1)), ('NO_AUTHORISATION', T.P(authorisation_sha256=T.Z))]
        c = [(f'{a}_before_{b}', a, 'pinbody', ma, mb) for (a, ma), (b, mb) in zip(rules, rules[1:])]
        c += [('header_before_the_digest', 'PIN_VERSION', 'pin', lambda s: sb.put(s.pin, sb.PIN_LAYOUT, 'version', 2)),
              ('digest_before_every_rule', 'PIN_DIGEST', 'pin', *[m for _, m in rules], lambda s: s.pin.__setitem__(0xD8, s.pin[0xD8] ^ 1))]
        self.run_cases([(name, want, mode, case(name, want, mode, *ms)[3]) for name, want, mode, *ms in c])


class Registers(Edges):
    def test_el2_regime_and_store_classes(self):
        c = [(f't0sz_{n}', want, tcr(t0sz=n)) for n, want in ((0, 'EL2_REGIME'), (16, 'EL2_REGIME'), (17, 'OK'), (20, 'OK'), (24, 'OK'), (25, 'EL2_REGIME'), (63, 'EL2_REGIME'))]
        c += [(f'tg0_{n}', want, tcr(tg0=n)) for n, want in ((0, 'OK'), (1, 'EL2_REGIME'), (2, 'EL2_REGIME'), (3, 'EL2_REGIME'))]
        c += [('mmu_on_only', 'OK', T.O(sctlr_el2=1)), ('mmu_off', 'EL2_REGIME', T.O(sctlr_el2=0x100030C5183C)),
              ('e2h_set', 'E2H_SET', T.O(hcr_el2=0x88000010 | 1 << 34)), ('bit_33_is_not_e2h', 'OK', T.O(hcr_el2=0x88000010 | 1 << 33)),
              ('bit_35_is_not_e2h', 'OK', T.O(hcr_el2=0x88000010 | 1 << 35))]
        c += [(f'current_el_{n:#x}', want, T.O(current_el=n)) for n, want in ((8, 'OK'), (0, 'NOT_EL2'), (4, 'NOT_EL2'), (9, 'NOT_EL2'), (0xC, 'NOT_EL2'), (8 | 1 << 32, 'NOT_EL2'))]
        c += [(f'build_stores_{n:#x}', want, T.O(build_stores=n)) for n, want in
              ((0, 'OK'), (1, 'OK'), (2, 'OK'), (3, 'OK'), (4, 'STORE_NOT_AUTH'), (7, 'STORE_NOT_AUTH'), (1 << 32 | 3, 'STORE_NOT_AUTH'), (1 << 63, 'STORE_NOT_AUTH'),
               (M64, 'STORE_NOT_AUTH'))]
        c.append(('build_stores_4_with_gic_authorised', 'OK', T.P(authorised_stores=7), T.O(build_stores=4)))
        c += [('midr_first', 'OK', T.O(midr_el1=0x511F0011)), ('midr_second', 'OK', T.O(midr_el1=0x512F0011)), ('midr_next', 'MIDR_MISMATCH', T.O(midr_el1=0x512F0012)),
              ('midr_high_bits', 'MIDR_MISMATCH', T.O(midr_el1=0x512F0011 | 1 << 32)), ('mpidr_next', 'MPIDR_MISMATCH', T.O(mpidr_el1=0x80000001)),
              ('mpidr_bit_63', 'MPIDR_MISMATCH', T.O(mpidr_el1=0x80000000 | 1 << 63)), ('cntfrq_less', 'CNTFRQ_MISMATCH', T.O(cntfrq_el0=19199999)),
              ('cntfrq_more', 'CNTFRQ_MISMATCH', T.O(cntfrq_el0=19200001)), ('cntfrq_high_bits', 'CNTFRQ_MISMATCH', T.O(cntfrq_el0=19200000 | 1 << 32))]
        c += [(f'{key}_one_more', 'GIC_MISMATCH', T.O(**{key: 1 + _FIXTURE.obs[key]})) for key in
              ('build_gicd_base', 'build_gicr_first', 'build_gicr_limit', 'build_gicr_stride', 'build_timer_intid')]
        c.append(('timer_intid_high_bits', 'GIC_MISMATCH', T.O(build_timer_intid=26 | 1 << 32)))
        c += [(f'{key}_one_more', 'GOP_MISMATCH', T.O(**{key: 1 + _FIXTURE.obs[key]})) for key in ('fb_base', 'fb_bytes', 'fb_width', 'fb_height', 'fb_stride', 'fb_format')]
        self.run_cases([case(name, want, 'observed', *ms) for name, want, *ms in c])

    def test_the_registers_run_in_the_order_the_note_gives(self):
        faults = [('NOT_EL2', T.O(current_el=4)), ('E2H_SET', T.O(hcr_el2=0x88000010 | 1 << 34)), ('EL2_REGIME', tcr(t0sz=25)),
                  ('MIDR_MISMATCH', T.O(midr_el1=0x413FD0C1)), ('MPIDR_MISMATCH', T.O(mpidr_el1=0x80000100)), ('CNTFRQ_MISMATCH', T.O(cntfrq_el0=24000000)),
                  ('GIC_MISMATCH', T.O(build_gicd_base=0x17010000)), ('GOP_MISMATCH', T.O(fb_width=1800)), ('STORE_NOT_AUTH', T.O(build_stores=7))]
        self.run_cases([case(f'{a}_before_{b}', a, 'observed', ma, mb) for (a, ma), (b, mb) in zip(faults, faults[1:])])


class Placement(Edges):
    def test_the_descriptor_that_holds_the_placement(self):
        cover = (7, PB, PAGES, 0x100F)
        pl = lambda name, want, *m: case(name, want, 'place', *m)
        self.run_cases([
            pl('exact_fit', 'OK', with_map([cover])), pl('one_page_more_below', 'OK', with_map([(7, PB - 0x1000, PAGES + 1, 0x100F)])),
            pl('one_page_more_above', 'OK', with_map([(7, PB, PAGES + 1, 0x100F)])),
            pl('one_page_short_above', 'PLACE_NOT_CONV', with_map([(7, PB, PAGES - 1, 0x100F)])),
            pl('starts_one_page_late', 'PLACE_NOT_CONV', with_map([(7, PB + 0x1000, PAGES - 1, 0x100F)])),
            pl('not_conventional', 'PLACE_NOT_CONV', with_map([(2, PB, PAGES, 0x100F)])), pl('no_descriptor_there', 'PLACE_NOT_CONV', with_map([(7, 0x100000, 16, 0x100F)])),
            pl('split_in_two_conventional', 'PLACE_NOT_CONV', with_map([(7, PB, PAGES // 2, 0x100F), (7, PB + PS // 2, PAGES // 2, 0x100F)])),
            pl('two_identical_conventional', 'PLACE_NOT_CONV', with_map([cover, cover])),
            pl('two_conventional_containing_it', 'PLACE_NOT_CONV', with_map([(7, PB - 0x1000, PAGES + 2, 0x100F), cover])),
            pl('another_descriptor_overlaps_a_page', 'PLACE_NOT_CONV', with_map([cover, (0, PE - 0x1000, 1, 0x100F)])),
            pl('neighbours_only_touch', 'OK', with_map([(0, PB - 0x1000, 1, 0x100F), cover, (0, PE, 1, 0x100F)])),
            pl('attribute_wb_only', 'OK', with_map([(7, PB, PAGES, 0x8)])), pl('attribute_wb_and_flags', 'OK', with_map([(7, PB, PAGES, 0x8000000000001008)])),
            pl('attribute_uc_wc_wt', 'PLACE_ATTR', with_map([(7, PB, PAGES, 0x7)])), pl('attribute_zero', 'PLACE_ATTR', with_map([(7, PB, PAGES, 0)])),
            pl('attribute_without_wb', 'PLACE_ATTR', with_map([(7, PB, PAGES, 0x1007)])),
            pl('placement_size_zero', 'PLACE_LAYOUT', with_map([cover]), T.P(place_size=0)),
            pl('layout_before_the_map', 'PLACE_LAYOUT', lambda s: setattr(s, 'map', b''), T.P(place_size=0)),
            pl('placement_wraps', 'PLACE_LAYOUT', with_map([cover]), T.P(place_base=M64 + 1 - 0x1000, place_size=0x2000))])

    def test_the_map_itself(self):
        cover, fill = (7, PB, PAGES, 0x100F), lambda n: [(0, 0x100000 + 0x1000 * i, 1, 0x100F) for i in range(n)]
        pl = lambda name, want, *m: case(name, want, 'place', *m)
        c = [pl('descriptor_unaligned', 'MAP_BAD', with_map([cover, (0, 0x100010, 1, 0x100F)])), pl('descriptor_empty', 'MAP_BAD', with_map([cover, (0, 0x100000, 0, 0x100F)])),
             pl('descriptor_ends_at_2_64', 'MAP_BAD', with_map([cover, (0, M64 + 1 - 0x1000, 1, 0x100F)])),
             pl('descriptor_ends_below_2_64', 'OK', with_map([cover, (0, M64 + 1 - 0x2000, 1, 0x100F)])),
             pl('descriptor_pages_over_2_52', 'MAP_BAD', with_map([cover, (0, 0x100000, 1 << 52, 0x100F)])),
             pl('bad_descriptor_after_the_holder', 'MAP_BAD', with_map([(2, PB, PAGES, 0), (0, 0x100010, 1, 0x100F)])),
             pl('map_empty', 'MAP_BAD', lambda s: setattr(s, 'map', b'')), pl('size_not_a_multiple', 'MAP_BAD', with_map([cover]), lambda s: setattr(s, 'map', s.map + b'\0')),
             pl('stride_zero', 'MAP_BAD', with_map([cover]), lambda s: setattr(s, 'stride', 0))]
        c += [pl(f'stride_{n}', 'OK', with_map([cover, (0, 0x100000, 1, 0x100F)], n)) for n in (40, 41, 48, 56, 64)]
        c += [pl(f'stride_{n}_would_parse_as_a_valid_map', 'MAP_BAD', with_odd_stride(n)) for n in (32, 36, 39)]
        for n, want in ((1, 'OK'), (2, 'OK'), (1023, 'OK'), (1024, 'OK'), (1025, 'MAP_BAD'), (1026, 'MAP_BAD')):
            c.append(pl(f'descriptors_{n}', want, with_map(fill(n - 1) + [cover])))
            c.append(pl(f'descriptors_{n}_holder_first', want, with_map([cover] + fill(n - 1))))
        self.run_cases(c)

    def test_declared_ranges_the_framebuffer_and_the_avoid_list(self):
        pl = lambda name, want, *m: case(name, want, 'place', *m)
        c = [pl('declared_none', 'OK', declare())]
        for name, ranges, want in (('touching_below', [(PB - 0x1000, 0x1000)], 'OK'), ('one_byte_into_the_start', [(PB - 0x1000, 0x1001)], 'PLACE_DECLARED'),
                                   ('touching_above', [(PE, 0x1000)], 'OK'), ('one_byte_into_the_end', [(PE - 1, 2)], 'PLACE_DECLARED'),
                                   ('inside', [(PB + 5, 1)], 'PLACE_DECLARED'), ('covering_it', [(PB - 0x1000, PS + 0x2000)], 'PLACE_DECLARED'),
                                   ('empty_inside', [(PB + 5, 0)], 'OK'), ('wraps', [(M64 + 1 - 0x1000, 0x2000)], 'PLACE_DECLARED'),
                                   ('ends_at_2_64', [(M64 + 1 - 0x1000, 0x1000)], 'PLACE_DECLARED'), ('ends_below_2_64', [(M64 + 1 - 0x2000, 0x1000)], 'OK'),
                                   ('24', [(0x1000 * (i + 1), 0x1000) for i in range(24)], 'OK'), ('25', [(0x1000 * (i + 1), 0x1000) for i in range(25)], 'PLACE_DECLARED'),
                                   ('26', [(0x1000 * (i + 1), 0x1000) for i in range(26)], 'PLACE_DECLARED')):
            c.append(pl(f'declared_{name}', want, declare(*ranges)))
        for name, base, size, want in (('touching_below', PB - 0x1000, 0x1000, 'OK'), ('one_byte_into_the_start', PB - 0x1000, 0x1001, 'PLACE_FB'),
                                       ('touching_above', PE, 0x1000, 'OK'), ('one_byte_into_the_end', PE - 1, 2, 'PLACE_FB'), ('empty_inside', PB + 5, 0, 'OK'),
                                       ('wraps', M64 + 1 - 0x1000, 0x2000, 'PLACE_FB')):
            c.append(pl(f'framebuffer_{name}', want, framebuffer(base, size)))
        for name, entries, want in (('touching_below', [(PB - 0x1000, 0x1000, 1)], 'OK'), ('one_byte_into_the_start', [(PB - 0x1000, 0x1001, 1)], 'PLACE_AVOID'),
                                    ('touching_above', [(PE, 0x1000, 1)], 'OK'), ('last_page_of_the_end', [(PE - 0x1000, 0x2000, 1)], 'PLACE_AVOID'),
                                    ('fourth_entry', [(0x10000000, 0x1000, 1), (0x10001000, 0x1000, 1), (0x10002000, 0x1000, 1), (PB, 0x1000, 3)], 'PLACE_AVOID')):
            c.append(pl(f'avoid_{name}', want, avoid(*entries)))
        c.append(pl('avoid_five', 'PLACE_AVOID', T.P(avoid_count=5)))
        c += [pl('declared_before_the_framebuffer', 'PLACE_DECLARED', declare((PB, 1)), framebuffer(PB, 1)),
              pl('framebuffer_before_the_avoid_list', 'PLACE_FB', framebuffer(PB, 1), avoid((PB, 0x1000, 1))),
              pl('attribute_before_declared', 'PLACE_ATTR', with_map([(7, PB, PAGES, 0)]), declare((PB, 1))),
              pl('holder_before_attribute', 'PLACE_NOT_CONV', with_map([(2, PB, PAGES, 0)])),
              pl('map_before_holder', 'MAP_BAD', with_map([(2, PB, PAGES, 0), (0, 0x100010, 1, 0)]))]
        self.run_cases(c)


class Guest(Edges):
    def test_chunks_at_the_ends_of_the_window(self):
        end = LOW + GS
        last = lambda dest: T.C(9, dest=dest)
        g = lambda name, want, *m: case(name, want, 'guest', *m)
        self.run_cases([
            g('last_chunk_ends_at_the_window_end', 'OK', last(end - 0x440)), g('last_chunk_16_bytes_past_the_end', 'CHUNK_RANGE', last(end - 0x430)),
            g('last_chunk_starts_at_the_end', 'CHUNK_RANGE', last(end)), g('last_chunk_16_bytes_earlier', 'OK', last(end - 0x450)),
            g('first_chunk_at_the_window_base', 'OK'), g('first_chunk_16_bytes_below', 'CHUNK_RANGE', T.C(0, dest=LOW - 16)),
            g('first_chunk_16_bytes_above', 'OK', T.C(0, dest=LOW + 16)), g('chunk_wraps', 'CHUNK_RANGE', T.C(9, dest=M64 + 1 - 0x440)),
            g('window_smaller_than_the_chunks', 'GUEST_LOAD', T.H(load_size=GS - 32 * MIB)), g('window_moved', 'GUEST_LOAD', T.H(load_base=LOW + 32 * MIB))])

    def test_chunk_counts_at_the_cap(self):
        self.run_cases([
            case('1023_chunks', 'OK', 'guest', with_guest(many(1023))), case('1024_chunks', 'OK', 'guest', with_guest(many(1024))),
            case('1025_chunks_in_the_head', 'GUEST_COUNT', 'guest', with_guest(many(1024)), T.H(entry_count=1025)),
            case('1024_chunks_end_to_end', 'OK', 'preflight', with_guest(many(1024)), policy=3),
            case('1023_chunks_end_to_end', 'OK', 'preflight', with_guest(many(1023)), policy=3),
            case('count_over_the_file', 'GUEST_COUNT', 'guest', T.H(entry_count=1024)), case('count_zero', 'GUEST_COUNT', 'guest', T.H(entry_count=0))])

    def test_a_file_of_exactly_256_MiB(self):
        c = []
        for total, want in ((FILE_MAX - 64, 'OK'), (FILE_MAX, 'OK'), (FILE_MAX + 64, 'GUEST_SIZE')):
            prefix, fields = sparse_guest(total)
            c.append(case(f'guest_file_{total:#x}', want, 'guest', T.P(**fields)))
            c[-1] = (c[-1][0], want, 'guest', Padded(c[-1][3], prefix, total))
        self.run_cases(c)

    def test_the_digest_step_stays_inside_the_file(self):
        right = sb.sha256(TAIL[-16:])
        d = digest_case
        self.run_cases([
            d('offset_past_the_end', 'CHUNK_EOF', lambda n: [entry(foff=n + 0x1000, size=0x1000)]),
            d('size_runs_past_the_end', 'CHUNK_EOF', lambda n: [entry(foff=n - 16, size=0x1000)]),
            d('size_runs_one_byte_past_the_end', 'CHUNK_EOF', lambda n: [entry(foff=n - 16, size=17)]),
            d('offset_at_the_end', 'CHUNK_EOF', lambda n: [entry(foff=n, size=16)]),
            d('last_16_bytes_wrong_digest', ('CHUNK_DIGEST', 0), lambda n: [entry(foff=n - 16, size=16)]),
            d('last_16_bytes_right_digest', 'OK', lambda n: [entry(foff=n - 16, size=16, sha=right)]),
            d('whole_file_as_one_chunk', ('CHUNK_DIGEST', 0), lambda n: [entry(foff=0, size=n)]),
            d('whole_file_and_one_byte', 'CHUNK_EOF', lambda n: [entry(foff=0, size=n + 1)]),
            d('offset_wraps', 'CHUNK_EOF', lambda n: [entry(foff=M64 + 1 - 16, size=16)]),
            d('size_wraps', 'CHUNK_EOF', lambda n: [entry(foff=8, size=M64 + 1 - 16)]),
            d('size_is_2_64_minus_1', 'CHUNK_EOF', lambda n: [entry(foff=1, size=M64)]),
            d('second_entry_past_the_end', 'CHUNK_EOF', lambda n: [entry(foff=n - 16, size=16, sha=right), entry(foff=n + 16, size=16)]),
            d('second_entry_wrong_digest', ('CHUNK_DIGEST', 1), lambda n: [entry(foff=n - 16, size=16, sha=right), entry(foff=n - 32, size=16)]),
            d('zero_entries_are_not_read', 'OK', lambda n: [entry(foff=M64, size=M64, flags=sb.CHUNK_ZERO, sha=bytes(32)), entry(foff=n - 16, size=16, sha=right)]),
            d('policy_3_refuses_the_chunk_first', 'CHUNK_EOF', lambda n: [entry(foff=n + 0x1000, size=0x1000)], 3),
            d('policy_0_reads_no_chunk', 'OK', lambda n: [entry(foff=n + 0x1000, size=0x1000)], 0),
            d('policy_2_hashes_the_file_only', 'FILE_DIGEST', lambda n: [entry(foff=n + 0x1000, size=0x1000)], 2),
            d('policy_4', 'HASH_POLICY', lambda n: [entry(foff=n + 0x1000, size=0x1000)], 4),
            d('table_digest_differs', 'INDEX_DIGEST', lambda n: [entry(foff=n - 16, size=16, sha=right)], 1, TAIL, lambda s: s.guest.__setitem__(130, s.guest[130] ^ 1)),
            d('count_beyond_the_file', 'GUEST_COUNT', lambda n: [entry(foff=n - 16, size=16, sha=right)], 1, TAIL, T.H(entry_count=1024))])

    def test_the_runners_sha256_gives_the_same_verdicts(self):
        cases = [case(f'{name}', want, 'digests', lambda s, g=g: setattr(s, 'guest', bytearray(g)), policy=3) for name, want, g in
                 (('hand_built', 'CHUNK_EOF', raw_guest([entry(foff=1 << 20, size=16)], TAIL)), ('many', 'OK', many(1024)))]
        cases += [case('fixture_with_the_whole_file_hashed', 'OK', 'digests', policy=3), case('fixture_one_data_byte_changed', ('CHUNK_DIGEST', 3), 'digests', T.flip_data(3), policy=3)]
        text = ''.join(s.text(name, mode) for name, _, mode, s in cases)
        self.assertEqual(T.run_harness(['batch'], text, kind='q1n1'), T.run_harness(['batch'], text))

    def test_the_order_of_the_guest_rules(self):
        trailing = lambda s: (s.guest.extend(bytes(16)), refit(s))
        g = lambda name, want, *m: case(name, want, 'guest', *m)
        self.run_cases([
            g('reserved_before_the_length_against_file_size', 'GUEST_RESERVED', T.H(reserved0=1), T.set_attr('guest', lambda b: b[:-16])),
            g('head_sizes_before_reserved', 'GUEST_SIZE', T.H(head_size=129, reserved0=1)),
            g('flags_before_head_sizes', 'GUEST_FLAGS', T.H(flags=1, head_size=129)),
            g('count_before_data_offset', 'GUEST_COUNT', T.H(entry_count=0, data_offset=1)),
            g('data_offset_before_load', 'GUEST_SIZE', T.H(data_offset=1, load_base=LOW + 32 * MIB)),
            g('load_before_the_pin', 'GUEST_LOAD', T.H(load_base=LOW + 32 * MIB), T.P(guest_chunk_count=11)),
            g('pin_before_the_chunks', 'GUEST_PIN', T.P(guest_chunk_count=11), T.C(3, kind=0)),
            g('first_bad_chunk_decides', 'CHUNK_OFFSET', lambda s: T.C(0, file_offset=T.chunk(s, 0)['file_offset'] + 64)(s), T.C(3, kind=0)),
            g('kind_before_flags_of_one_chunk', 'CHUNK_KIND', T.C(3, kind=0, flags=2)), g('range_before_order_of_one_chunk', 'CHUNK_RANGE', T.C(3, dest=LOW - 0x1000)),
            g('trailing_bytes_before_the_duplicate', 'GUEST_SIZE', trailing, T.C(6, kind=sb.KIND_PARAMS)),
            g('duplicate_before_the_missing_kind', 'CHUNK_DUP', T.C(6, kind=sb.KIND_PARAMS), T.C(3, kind=sb.KIND_OTHER), T.C(4, kind=sb.KIND_OTHER))])


class Report(Edges):
    def test_stages_counters_and_what_is_not_checked(self):
        r = lambda name, want, **kw: case(name, want, 'report', T.R(**kw))
        c = [r(f'refusal_at_stage_{n}', 'OK', refusal=E.COPY_VERIFY, stage=n) for n in range(0, 12)]
        c += [r(f'refusal_at_stage_{n}', 'REPORT_VALUE', refusal=E.COPY_VERIFY, stage=n) for n in (12, 13, 14)]
        c += [r(f'no_refusal_at_stage_{n}', want, stage=n) for n, want in ((12, 'OK'), (13, 'OK'), (14, 'REPORT_VALUE'))]
        c += [r('counters_equal', 'OK', chunks_total=10, chunks_copied=10, chunks_hashed=10), r('copied_over_total', 'REPORT_VALUE', chunks_copied=11),
              r('hashed_over_total', 'REPORT_VALUE', chunks_hashed=11), r('total_1024', 'OK', chunks_total=1024, chunks_copied=1024, chunks_hashed=1024),
              r('total_1025', 'REPORT_VALUE', chunks_total=1025, chunks_copied=0, chunks_hashed=0)]
        c += [r(f'lines_{n}', want, lines_printed=n) for n, want in ((0, 'OK'), (30, 'OK'), (31, 'REPORT_VALUE'))]
        c += [r(f'hash_policy_{n}', want, hash_policy=n) for n, want in ((0, 'OK'), (1, 'OK'), (2, 'OK'), (3, 'OK'), (4, 'REPORT_VALUE'), (0x80000000, 'REPORT_VALUE'))]
        c += [r('refusal_94_is_known', 'OK', refusal=94, stage=3), r('refusal_95', 'REPORT_VALUE', refusal=95, stage=3), r('refusal_257', 'REPORT_VALUE', refusal=257, stage=3),
              r('pin_code_82', 'OK', pin_code=82), r('pin_code_83', 'REPORT_VALUE', pin_code=83), r('guest_code_83', 'REPORT_VALUE', guest_code=83),
              r('ticks_not_in_order', 'OK', ticks_enter=1 << 60, ticks_return=5), r('bytes_not_checked', 'OK', bytes_copied=M64, bytes_hashed=M64)]
        self.run_cases(c)

    def test_the_head_is_read_in_the_order_the_note_gives(self):
        def tail(s):
            s.report[0x200] = 1

        self.run_cases([
            case('length_before_magic', 'REPORT_SIZE', 'report', lambda s: s.report.__setitem__(0, 0), T.set_attr('report', lambda b: b[:-1])),
            case('magic_before_version', 'REPORT_MAGIC', 'report', lambda s: s.report.__setitem__(0, 0), T.R(version=2)),
            case('version_before_head_size', 'REPORT_VERSION', 'report', T.R(version=2, head_size=300)),
            case('head_size_before_the_page', 'REPORT_SIZE', 'report', T.R(head_size=300), tail),
            case('the_page_before_the_values', 'REPORT_RESERVED', 'report', tail, T.R(stage=14))])


class Order(Edges):
    BAD = dict(args=lambda s: setattr(s, 'args', (sb.RUNNER_MAGIC, 2, 192)), files=T.set_file(0, size=0), pin=T.P(flags=1), registers=T.O(current_el=4),
               placement=T.map_desc(1, type=2), guest=T.H(version=2), digest=T.flip_data(3))
    CODE = dict(args='ARGS_BAD', files='FILE_SIZE', pin='PIN_FLAGS', registers='NOT_EL2', placement='PLACE_NOT_CONV', guest='GUEST_VERSION', digest='CHUNK_DIGEST')

    def test_every_pair_of_records_gives_the_earlier_record_s_refusal(self):
        names = list(self.BAD)
        self.run_cases([case(f'{a}_before_{b}', self.CODE[a], 'preflight', self.BAD[b], self.BAD[a], policy=3) for i, a in enumerate(names) for b in names[i + 1:]])

    def test_the_digests_run_in_the_order_the_note_gives(self):
        table_byte = lambda s: s.guest.__setitem__(128 + 64 * 3 + 32, s.guest[128 + 64 * 3 + 32] ^ 1)     # a digest field: no chunk rule reads it
        self.run_cases([
            case('policy_before_the_table_digest', 'HASH_POLICY', 'preflight', table_byte, policy=4),
            case('table_digest_before_the_chunks', 'INDEX_DIGEST', 'preflight', table_byte, T.flip_data(5), policy=3),
            case('chunks_before_the_file', 'CHUNK_DIGEST', 'preflight', T.flip_data(3), T.P(guest_file_sha256=sb.sha256(b'another file')), policy=3)])


class Unpack(Edges):
    def head(self, count, length=sb.GUEST_HEAD_BYTES):
        head = bytearray(T.fixture().guest[:sb.GUEST_HEAD_BYTES])
        sb.put(head, sb.GUEST_LAYOUT, 'entry_count', count)
        sb.put(head, sb.GUEST_LAYOUT, 'file_size', length)
        return bytes(head).ljust(length, b'\0')[:length]

    def test_a_hostile_head_is_refused_before_any_loop(self):
        for count, length in ((0xFFFFFFFF, 128), (0x10000000, 128), (1025, 128), (1025, 128 + 64 * 1025), (0, 128)):
            with self.subTest(count=count, length=length):
                start = time.perf_counter()
                with self.assertRaises(sb.Refusal) as caught:
                    sb.unpack_guest(self.head(count, length))
                self.assertEqual(caught.exception.code, E.GUEST_COUNT)
                self.assertLess(time.perf_counter() - start, 0.5)

    def test_unpack_refuses_what_the_c_head_check_refuses_and_with_the_same_code(self):
        buffers = {'count_max': self.head(0xFFFFFFFF), 'count_1025': self.head(1025), 'count_1025_with_room': self.head(1025, 128 + 64 * 1025),
                   'count_zero': self.head(0), 'count_over_the_buffer': self.head(1024, 128 + 64 * 10),
                   'count_1_but_no_table': self.head(1), 'table_one_byte_short': self.head(1, 128 + 64 - 1), 'shorter_than_the_head': self.head(1)[:127]}
        cases = []
        for name, buf in buffers.items():
            s = T.fixture()
            s.guest = bytearray(buf)
            cases.append((name, s))
        out = T.run_harness(['batch'], ''.join(s.text(name, 'guest') for name, s in cases)).splitlines()
        for line, (name, s) in zip(out, cases):
            with self.subTest(case=name):
                with self.assertRaises(sb.Refusal) as caught:
                    sb.unpack_guest(bytes(s.guest))
                self.assertEqual(line.split()[3], sb.error_name(caught.exception.code))

    def test_a_table_that_fits_is_unpacked_whole(self):
        for n in (1, 5, 1024):
            guest = many(n) if n >= 5 else raw_guest([entry()], TAIL)
            head, chunks = sb.unpack_guest(guest)
            self.assertEqual((head['entry_count'], len(chunks)), (n, n))
        for n in (1, 1024):
            self.assertEqual(len(sb.unpack_guest(self.head(n, 128 + 64 * n))[1]), n)      # a table that fills the buffer exactly


class Document(unittest.TestCase):
    def test_the_note_states_what_the_checks_do(self):
        text = ' '.join(DOC.read_text().split())
        for needle in ("then slot 0 to slot 4 and in each slot FILE_ROLE, FILE_BASE, FILE_SIZE", "checks REPORT.DAT's slot alone first",
                       "the first bad chunk decides, so chunk 0's offset is refused before chunk 3's kind", "`place_base` and `place_size` of 16 MiB",
                       "a misaligned one is PLACE_ALIGN, not PLACE_LAYOUT", "a refusal with stage MONITOR_RETURNED or DONE",
                       "PLACE_LIMIT when a sum wraps; PLACE_LAYOUT; PLACE_LIMIT when the end is over 2^36", "then STORE_NOT_AUTH, last NO_AUTHORISATION",
                       "PLACE_LAYOUT for a placement of zero bytes or one that wraps", "and, for a data chunk, CHUNK_EOF, CHUNK_OFFSET, PAD_NONZERO",
                       "GUEST_COUNT (the step reads the count again so that it is safe on its own)",
                       "initialises and stores such structures word by word, as the wrapper does", "tools/test-sp11-stick-bundle-edges.py"):
            self.assertIn(needle, text)


class Hygiene(unittest.TestCase):
    def test_licence_size_and_no_machine_text(self):
        path = Path(__file__).resolve()
        text = path.read_text()
        self.assertIn('SPDX-License-Identifier: MIT', text[:100])
        self.assertLess(len(text.splitlines()), 1500)
        self.assertLess(len(text.encode()), 50 * 1024)
        for prefix in ('/' + 'Users' + '/', '/' + 'home' + '/', '/' + 'tmp' + '/'):
            self.assertNotIn(prefix, text)
        self.assertLess(len((HERE / 'test-sp11-stick-bundle.c').read_bytes()), 50 * 1024)


if __name__ == '__main__':
    unittest.main()
