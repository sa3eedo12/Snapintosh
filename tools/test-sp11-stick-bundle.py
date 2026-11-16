#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests for the SP11 stick bundle interface: probes/sp11-stick-bundle.h, tools/sp11_stick_bundle.py, the C harness
tools/test-sp11-stick-bundle.c and docs/SP11-STICK-BUNDLE.md.

    python3 tools/test-sp11-stick-bundle.py
    Q1N1_SP11_TREE=<q1n1 checkout> python3 tools/test-sp11-stick-bundle.py     (a path that is no checkout: those tests SKIP)

No hardware, and nothing written to the q1n1 tree. The C checks are built for the host with AddressSanitizer and
UndefinedBehaviorSanitizer and fed the same bytes as the independent Python checks; every seeded bad record must get the same
refusal code from both and from the table. Fixtures are synthetic: only the platform constants come from docs/SURFACE-PRO-11.md.
Without clang the native classes report SKIP (never PASS); without a q1n1 checkout the runner comparisons report SKIP.
"""
import copy
import hashlib
import importlib.util
import os
import re
import shutil
import struct
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import sp11_stick_bundle as sb  # noqa: E402
from sp11_stick_bundle import E  # noqa: E402

HEADER, HARNESS, DOC = ROOT / 'probes/sp11-stick-bundle.h', ROOT / 'tools/test-sp11-stick-bundle.c', ROOT / 'docs/SP11-STICK-BUNDLE.md'
MODULE, THIS = ROOT / 'tools/sp11_stick_bundle.py', Path(__file__).resolve()
BUILD = ROOT / 'build' / 'test-sp11-stick-bundle'
TOOLS = ('/opt/homebrew/opt/llvm/bin',)
MIB = 1 << 20
MONITOR_FLAGS = ['--target=aarch64-unknown-none-elf', '-ffreestanding', '-fno-pic', '-mgeneral-regs-only', '-Wall', '-Wextra', '-Werror',
                 '-fno-stack-protector', '-mcmodel=tiny']
DOC_MAX = 45000


def find_tool(name):
    return shutil.which(name) or next((str(Path(d) / name) for d in TOOLS if (Path(d) / name).exists()), None)


def q1n1_tree():
    """The q1n1 checkout for the runner comparisons. Q1N1_SP11_TREE set means that path and no other (a path that is no
    checkout skips the tests); unset, the places tools/test-sp11-monitor-port.py looks."""
    explicit = os.environ.get('Q1N1_SP11_TREE')
    if explicit is not None:
        candidates = [Path(explicit)] if explicit else []
    else:
        candidates = []
        for base in (ROOT.parent, ROOT.parent.parent, ROOT.parent.parent.parent):
            candidates.append(base / 'q1n1')
            candidates.extend(sorted(base.glob('q1n1-worktrees/*sp11*')))
    return next((t for t in candidates if (t / 'platform/uefi/sha256.c').is_file() and (t / 'platform/uefi/sp11-payload.h').is_file()), None)


_BUILT = {}


def compile_harness(kind):
    """kind: 'host' (the reference hash), 'q1n1' (the runner's sha256.c). SkipTest without a compiler or a checkout."""
    if kind not in _BUILT:
        clang = find_tool('clang')
        if not clang:
            raise unittest.SkipTest('clang not found')
        cmd = [clang, '-std=c11', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined', '-fno-sanitize-recover=undefined']
        sources = [str(HARNESS)]
        if kind == 'q1n1':
            tree = q1n1_tree()
            if not tree:
                raise unittest.SkipTest('no q1n1 checkout (set Q1N1_SP11_TREE): the runner sha256.c is not compared')
            cmd += ['-DSP11SB_HARNESS_Q1N1_SHA256', f'-I{tree / "platform/uefi"}']
            sources.append(str(tree / 'platform/uefi/sha256.c'))
        BUILD.mkdir(parents=True, exist_ok=True)
        exe = BUILD / f'harness-{kind}'
        built = subprocess.run(cmd + sources + ['-o', str(exe)], capture_output=True, text=True, timeout=300)
        if built.returncode:
            raise AssertionError(f'host build of the harness failed:\n{built.stderr[-3000:]}')
        _BUILT[kind] = exe
    return _BUILT[kind]


def run_harness(args, text='', kind='host'):
    done = subprocess.run([str(compile_harness(kind))] + args, input=text, capture_output=True, text=True, timeout=600)
    assert done.returncode == 0 and not done.stderr, f'harness {args} exited {done.returncode}: {done.stderr[-2000:]}'
    return done.stdout


# ---- Synthetic fixtures ------------------------------------------------------------------------------------------------
def noise(label, n):
    out, i = b'', 0
    while len(out) < n:
        out += hashlib.sha256(f'{label}:{i}'.encode()).digest()
        i += 1
    return out[:n]


UNIT = dict(boot_mpidr=0x80000000, cntfrq_hz=19200000, gicd_base=0x17000000, gicr_first=0x17080000, gicr_limit=0x17380000,
            gicr_stride=0x40000, its_base=0x17040000, gicr_count=12, timer_intid=26, fb_base=0xE4800000, fb_bytes=0x1518000,
            fb_width=2880, fb_height=1920, fb_stride=2880, fb_format=1)
CHUNKS = (   # kind, offset from the guest base, data size (or ('zero', size)); ascending, sizes a multiple of 16, some not of 64
    (sb.KIND_HANDOFF, 0x0, 0x2000), (sb.KIND_PARAMS, 0xC000, 0x1410), (sb.KIND_DEVICE_TREE, 0x10000, 0x1020),
    (sb.KIND_KERNEL, 0x80000, 0x2000), (sb.KIND_KERNEL, 0x84000, 0x830), (sb.KIND_MONITOR_IMAGE, 0x7400000, 0x400),
    (sb.KIND_TABLES, 0x9200000, 0x400), (sb.KIND_TABLES, 0x9204000, 0x400), (sb.KIND_MONITOR_DATA, 0xA200000, ('zero', 0x8000)),
    (sb.KIND_OTHER, 0xA210000, 0x440))
NAMES = ('SNAPMON.BIN', 'SNAPREP.BIN', 'GUEST.DAT', 'PIN.DAT', 'REPORT.DAT')


def efi_map(descs):
    return b''.join(struct.pack('<IIQQQQ', t, 0, start, 0, pages, attr) + bytes(8) for t, start, pages, attr in descs)


def up(n):
    return (n + 0xFFF) & ~0xFFF


class Scn:
    """Everything a check reads, as the wrapper would hold it: the five slots, PIN.DAT, GUEST.DAT, the registers, the final map."""

    def copy(self):
        return copy.deepcopy(self)

    def env(self):
        return dict(args=self.args, files=[(bytes(p), b, z) for p, b, z in self.files], pin=bytes(self.pin), guest=bytes(self.guest),
                    obs=self.obs, map=None if self.map is None else bytes(self.map), stride=self.stride,
                    declared=[tuple(d) for d in self.declared], policy=self.policy)

    def text(self, name, mode):
        hexs = lambda b: '-' if b is None else '=' if not len(b) else bytes(b).hex()
        lines = [f'BEGIN {name}', 'ARGS %x %d %d' % self.args]
        lines += [f'FILE {bytes(p).hex()} {b:x} {z:x}' for p, b, z in self.files]
        lines += ['OBS ' + ' '.join(f'{self.obs[k]:x}' for k in sb.OBSERVED_KEYS), 'PIN ' + hexs(self.pin), 'GUEST ' + hexs(self.guest),
                  f'MAP {self.stride:x} ' + hexs(self.map)]
        lines += [f'DECL {b:x} {z:x}' for b, z in self.declared]
        lines += [f'POLICY {self.policy:x}']
        if self.report is not None and mode == 'report':
            lines.append('REPORT ' + hexs(self.report))
        return '\n'.join(lines) + f'\nRUN {mode}\n'


def valid_report():
    b = bytearray(sb.REPORT_BYTES)
    for name, value in (('magic', b'SNAPBRPT'), ('version', 1), ('head_size', sb.REPORT_HEAD_BYTES), ('stage', sb.STAGE.DONE), ('hash_policy', 3),
                        ('lines_printed', 22), ('chunks_total', 10), ('chunks_copied', 10), ('chunks_hashed', 9), ('counter_hz', 19200000)):
        sb.put(b, sb.REPORT_LAYOUT, name, value)
    return b


_FIXTURES = {}


def fixture(bank=False, fresh=False):
    """A bundle that passes every check: low (a 256 MiB guest in a 576 MiB descriptor) or in the bank (384 MiB, 0x880000000)."""
    if fresh or bank not in _FIXTURES:
        base, gsize = (0x880000000, 384 * MIB) if bank else (0x90000000, 256 * MIB)
        chunks = [dict(kind=k, dest=base + o, **(dict(size=d[1]) if isinstance(d, tuple) else dict(data=noise(f'chunk{k}{o}', d))))
                  for k, o, d in CHUNKS]
        guest = sb.pack_guest(base, gsize, chunks)
        fields = dict(authorised_stores=3, board_id='sp11', build_label='synthetic-bank' if bank else 'synthetic-low',
                      descriptor_sha256=sb.sha256(b'synthetic board descriptor'), authorisation_sha256=sb.sha256(b'synthetic authorisation'),
                      midr=[0x511F0011, 0x512F0011], avoid=[dict(base=0x10000000, size=0x100000, tag=sb.AVOID_EL2_UNREACHABLE)],
                      acpi=[dict(sig=s, length=0x100 + n, sha256=sb.sha256(b'synthetic ' + s)) for n, s in enumerate((b'APIC', b'FACP', b'GTDT'))],
                      **UNIT, **sb.placement(base, gsize), **sb.guest_pin_fields(guest))
        sizes = [0x4C000, 0x8000, len(guest), sb.PIN_BYTES, sb.REPORT_BYTES]
        slots = [0xB4000000 + 0x100000 * i for i in range(5)]
        extra = [(0xB4500000, 0x40000), (0xB4600000, 0x1000), (0xB4700000, 0x10000)]
        descs = [(0, 0x80000000, 0x600, 0x100F), (7, 0x880000000 if bank else 0x90000000, (30720 if bank else 576) * MIB // 4096, 0x100F),
                 (5, 0xD4FBA000, 0x44C, 0x800000000000100F), (0, 0xE4800000, 0x21C0, 0x100F)]
        descs += [(1 if i < 2 else 2, slots[i], up(sizes[i]) // 4096, 0x100F) for i in range(5)] + [(2, b, z // 4096, 0x100F) for b, z in extra]
        s = Scn()
        s.args = (sb.RUNNER_MAGIC, 1, 192)
        s.files = [[bytearray(b'\\SP11\\' + NAMES[i].encode()).ljust(256, b'\0'), slots[i], sizes[i]] for i in range(5)]
        s.pin, s.guest, s.map, s.stride, s.policy = bytearray(sb.pack_pin(fields)), bytearray(guest), bytearray(efi_map(descs)), 48, 3
        s.declared = [[slots[i], up(sizes[i])] for i in range(5)] + [list(e) for e in extra]
        s.obs = dict(current_el=8, hcr_el2=0x88000010, sctlr_el2=0x100030C5183D, tcr_el2=0x80843514, midr_el1=0x512F0011, mpidr_el1=0x80000000,
                     cntfrq_el0=19200000, build_gicd_base=0x17000000, build_gicr_first=0x17080000, build_gicr_limit=0x17380000,
                     build_gicr_stride=0x40000, build_timer_intid=26, build_stores=3, fb_base=0xE4800000, fb_bytes=0x1518000, fb_width=2880,
                     fb_height=1920, fb_stride=2880, fb_format=1)
        s.report = valid_report()
        _FIXTURES[bank] = s
    return _FIXTURES[bank].copy()


def py_run(s, mode):
    """The Python verdict of one RUN, as (code, failing chunk or None)."""
    pin, o = bytes(s.pin), s.obs
    if mode == 'preflight':
        return sb.preflight(s.env())
    if mode == 'digests':
        return sb.check_digests(bytes(s.guest), pin, s.policy)
    one = {'pin': lambda: sb.check_pin(pin), 'pinbody': lambda: sb.check_pin(pin, digest=False),
           'files': lambda: sb.check_files([(bytes(p), b, z) for p, b, z in s.files]), 'observed': lambda: sb.check_observed(pin, o),
           'place': lambda: sb.check_place(pin, None if s.map is None else bytes(s.map), s.stride, o['fb_base'], o['fb_bytes'], s.declared),
           'guest': lambda: sb.check_guest(bytes(s.guest), pin), 'report': lambda: sb.check_report(bytes(s.report))}
    return one[mode](), None


# ---- Mutators: each edits a copy of the fixture; P re-digests PIN.DAT so the case reaches the check it is about ----------
def P(**kw):
    def mutate(s):
        for k, v in kw.items():
            sb.put(s.pin, sb.PIN_LAYOUT, k, v)
        sb.pin_digest(s.pin)
    return mutate


def PIN_AT(offset, value, width=4):
    def mutate(s):
        s.pin[offset:offset + width] = value.to_bytes(width, 'little')
        sb.pin_digest(s.pin)
    return mutate


def O(**kw):
    return lambda s: s.obs.update(kw)


def C(i, **kw):
    def mutate(s):
        for k, v in kw.items():
            sb.put(s.guest, sb.CHUNK_LAYOUT, k, v, 128 + 64 * i)
    return mutate


def H(**kw):
    def mutate(s):
        for k, v in kw.items():
            sb.put(s.guest, sb.GUEST_LAYOUT, k, v)
    return mutate


def R(**kw):
    def mutate(s):
        for k, v in kw.items():
            sb.put(s.report, sb.REPORT_LAYOUT, k, v)
    return mutate


def chain(*fns):
    return lambda s: [f(s) for f in fns] and None


def chunk(s, i):
    return sb.unpack_guest(s.guest)[1][i]


def flip_data(i):
    def mutate(s):
        s.guest[chunk(s, i)['file_offset'] + 3] ^= 0x10
    return mutate


def poke_pad(s):
    c = chunk(s, 1)
    s.guest[c['file_offset'] + c['size']] = 1       # the 48 pad bytes after PARAMS


def other(s):
    return s.guest.__setitem__(slice(None), s.guest[:-16] + bytes(16))


def map_desc(index, **kw):
    def mutate(s):
        d = bytearray(s.map[index * 48:index * 48 + 48])
        for name, (off, width) in dict(type=(0, 4), start=(8, 8), pages=(24, 8), attr=(32, 8)).items():
            if name in kw:
                d[off:off + width] = kw[name].to_bytes(width, 'little')
        s.map[index * 48:index * 48 + 48] = d
    return mutate


def set_path(i, path):
    def mutate(s):
        s.files[i][0] = bytearray(path).ljust(256, b'\0')
    return mutate


def set_file(i, base=None, size=None):
    def mutate(s):
        s.files[i][1] = s.files[i][1] if base is None else base
        s.files[i][2] = s.files[i][2] if size is None else size
    return mutate


def set_attr(name, fn):
    return lambda s: setattr(s, name, fn(getattr(s, name)))


def bank_placement(base):
    return P(**sb.placement(base, 384 * MIB))


LOW_BASE = 0x90000000
PLACED = sb.placement(LOW_BASE, 256 * MIB)
Z = bytes(32)
# (name, expected refusal, run mode, mutator). preflight reaches every check in the wrapper's order with policy 3 (chunks and file).
CASES = [
    ('args_magic', 'ARGS_BAD', 'preflight', lambda s: setattr(s, 'args', (sb.RUNNER_MAGIC ^ 1, 1, 192))),
    ('args_version', 'ARGS_BAD', 'preflight', lambda s: setattr(s, 'args', (sb.RUNNER_MAGIC, 2, 192))),
    ('args_short', 'ARGS_BAD', 'preflight', lambda s: setattr(s, 'args', (sb.RUNNER_MAGIC, 1, 191))),
    ('files_six', 'FILE_COUNT', 'preflight', lambda s: s.files.append(list(s.files[0]))),
    ('files_four', 'FILE_COUNT', 'preflight', lambda s: s.files.pop()),
    ('files_none', 'FILE_COUNT', 'files', lambda s: s.files.clear()),
    ('files_swapped', 'FILE_ROLE', 'preflight', lambda s: s.files.insert(2, s.files.pop(3))),
    ('files_other_dir', 'FILE_ROLE', 'preflight', set_path(0, b'\\OTHER\\SNAPMON.BIN')),
    ('files_trailing_bytes', 'FILE_ROLE', 'preflight', set_path(1, b'\\SP11\\SNAPREP.BIN' + b'x' * 200)),
    ('files_no_terminator', 'FILE_ROLE', 'preflight', lambda s: s.files[4].__setitem__(0, bytearray(b'\\SP11\\REPORT.DAT' + b'x' * 239))),
    ('files_lower_case_ok', 'OK', 'preflight', set_path(2, b'\\sp11\\guest.dat')),
    ('files_base_zero', 'FILE_BASE', 'preflight', set_file(3, base=0)),
    ('files_base_unaligned', 'FILE_BASE', 'preflight', set_file(0, base=0xB4000010)),
    ('files_base_wraps', 'FILE_BASE', 'preflight', set_file(1, base=(1 << 64) - 0x1000, size=0x2000)),
    ('files_size_zero', 'FILE_SIZE', 'preflight', set_file(1, size=0)),
    ('files_size_over_cap', 'FILE_SIZE', 'preflight', set_file(2, size=(256 << 20) + 1)),
    ('files_report_size', 'FILE_SIZE', 'preflight', set_file(4, size=0x80000)),
    ('pin_truncated', 'PIN_TRUNCATED', 'preflight', set_attr('pin', lambda b: b[:1023])),
    ('pin_longer', 'PIN_SIZE', 'preflight', set_attr('pin', lambda b: b + b'\0')),
    ('pin_magic', 'PIN_MAGIC', 'preflight', lambda s: s.pin.__setitem__(0, 0x58)),
    ('pin_version', 'PIN_VERSION', 'preflight', P(version=2)),
    ('pin_size_field', 'PIN_SIZE', 'preflight', P(size=1023)),
    ('pin_digest', 'PIN_DIGEST', 'preflight', lambda s: s.pin.__setitem__(0xD8, s.pin[0xD8] ^ 1)),
    ('pin_flags', 'PIN_FLAGS', 'preflight', P(flags=1)),
    ('pin_unknown_store_class', 'PIN_FLAGS', 'preflight', P(authorised_stores=11)),
    ('pin_string_unterminated', 'PIN_STRING', 'preflight', P(board_id=b'A' * 16)),
    ('pin_string_after_nul', 'PIN_STRING', 'preflight', PIN_AT(0x18 + 6, 0x78, 1)),
    ('pin_string_bad_character', 'PIN_STRING', 'preflight', P(build_label='bad label')),
    ('pin_string_empty', 'PIN_STRING', 'preflight', P(build_label=b'')),
    ('pin_midr_count_0', 'PIN_COUNT', 'preflight', P(midr_count=0)),
    ('pin_midr_count_5', 'PIN_COUNT', 'preflight', P(midr_count=5)),
    ('pin_avoid_count_5', 'PIN_COUNT', 'preflight', P(avoid_count=5)),
    ('pin_acpi_count_9', 'PIN_COUNT', 'preflight', P(acpi_count=9)),
    ('pin_gicr_count_0', 'PIN_COUNT', 'preflight', P(gicr_count=0)),
    ('pin_chunk_count_0', 'PIN_COUNT', 'preflight', P(guest_chunk_count=0)),
    ('pin_chunk_count_1025', 'PIN_COUNT', 'preflight', P(guest_chunk_count=1025)),
    ('pin_reserved0', 'PIN_RESERVED', 'preflight', P(reserved0=1)),
    ('pin_reserved_tail', 'PIN_RESERVED', 'preflight', PIN_AT(0x328 + 5, 1, 1)),
    ('pin_unused_midr_slot', 'PIN_RESERVED', 'preflight', PIN_AT(0xE0 + 12, 7)),
    ('pin_unused_avoid_slot', 'PIN_RESERVED', 'preflight', PIN_AT(0x180 + 24 * 3, 0x1000, 8)),
    ('pin_avoid_reserved', 'PIN_RESERVED', 'preflight', PIN_AT(0x180 + 20, 1)),
    ('pin_unused_acpi_slot', 'PIN_RESERVED', 'preflight', PIN_AT(0x1E8 + 40 * 7 + 8, 1, 1)),
    ('pin_midr_descending', 'PIN_ORDER', 'preflight', lambda s: (PIN_AT(0xE0, 0x512F0011)(s), PIN_AT(0xE4, 0x511F0011)(s))),
    ('pin_acpi_descending', 'PIN_ORDER', 'preflight', lambda s: (s.pin.__setitem__(slice(0x1E8, 0x1EC), b'ZZZZ'), sb.pin_digest(s.pin))),
    ('pin_avoid_equal_bases', 'PIN_ORDER', 'preflight', lambda s: (P(avoid_count=2)(s), s.pin.__setitem__(slice(0x198, 0x1B0), s.pin[0x180:0x198]),
                                                                    sb.pin_digest(s.pin))),
    ('pin_midr_zero', 'PIN_VALUE', 'preflight', PIN_AT(0xE0, 0)),
    ('pin_cntfrq_0', 'PIN_VALUE', 'preflight', P(cntfrq_hz=0)),
    ('pin_gicr_stride_small', 'PIN_VALUE', 'preflight', P(gicr_stride=0x10000, gicr_limit=0x17080000 + 12 * 0x10000)),
    ('pin_gicr_limit_wrong', 'PIN_VALUE', 'preflight', P(gicr_limit=0x17370000)),
    ('pin_gicd_inside_window', 'PIN_VALUE', 'preflight', P(gicd_base=0x17090000)),
    ('pin_timer_not_a_ppi', 'PIN_VALUE', 'preflight', P(timer_intid=32)),
    ('pin_fb_stride_below_width', 'PIN_VALUE', 'preflight', P(fb_stride=2879)),
    ('pin_fb_too_small', 'PIN_VALUE', 'preflight', P(fb_bytes=0x1517000)),
    ('pin_fb_format_2', 'PIN_VALUE', 'preflight', P(fb_format=2)),
    ('pin_descriptor_digest_zero', 'PIN_VALUE', 'preflight', P(descriptor_sha256=Z)),
    ('pin_guest_size_over_cap', 'PIN_VALUE', 'preflight', P(guest_file_size=(256 << 20) + 1)),
    ('pin_avoid_overlap', 'PIN_VALUE', 'preflight', lambda s: (P(avoid_count=2)(s), PIN_AT(0x180 + 24, 0x10000000 + 0x80000, 8)(s),
                                                               PIN_AT(0x180 + 24 + 8, 0x1000, 8)(s), PIN_AT(0x180 + 24 + 16, 2)(s))),
    ('pin_avoid_tag_0', 'PIN_VALUE', 'preflight', PIN_AT(0x180 + 16, 0)),
    ('pin_acpi_signature_lower', 'PIN_VALUE', 'preflight', lambda s: (s.pin.__setitem__(slice(0x1E8 + 80, 0x1EC + 80), b'gtdt'), sb.pin_digest(s.pin))),
    ('pin_acpi_short_table', 'PIN_VALUE', 'preflight', PIN_AT(0x1E8 + 4, 35)),
    ('pin_no_authorisation', 'NO_AUTHORISATION', 'preflight', P(authorisation_sha256=Z)),
    ('pin_store_class_missing', 'STORE_NOT_AUTH', 'preflight', P(authorised_stores=1)),
    ('place_guest_base_unaligned', 'PLACE_ALIGN', 'preflight', P(guest_base=LOW_BASE + 0x1000000)),
    ('place_guest_size_unaligned', 'PLACE_ALIGN', 'preflight', P(guest_size=272 * MIB)),
    ('place_reserve_unaligned', 'PLACE_ALIGN', 'preflight', P(reserve_size=49 * MIB)),
    ('place_work_base_unaligned', 'PLACE_ALIGN', 'preflight', P(work_base=PLACED['work_base'] + 0x1000)),
    ('place_work_size', 'PLACE_LAYOUT', 'preflight', P(work_size=224 * MIB)),
    ('place_work_base', 'PLACE_LAYOUT', 'preflight', P(work_base=PLACED['work_base'] + 0x10000)),
    ('place_size_sum', 'PLACE_LAYOUT', 'preflight', P(place_size=PLACED['place_size'] + 16 * MIB)),
    ('place_base_differs', 'PLACE_LAYOUT', 'preflight', P(place_base=LOW_BASE + 0x2000000)),
    ('place_guest_too_small', 'PLACE_LAYOUT', 'preflight', P(**sb.placement(LOW_BASE, 160 * MIB))),
    ('place_reserve_too_small', 'PLACE_LAYOUT', 'preflight', P(**sb.placement(LOW_BASE, 256 * MIB, 32 * MIB))),
    ('place_crosses_2_36', 'PLACE_LIMIT', 'preflight', bank_placement((1 << 36) - 672 * MIB + 0x2000000)),
    ('place_wraps_u64', 'PLACE_LIMIT', 'preflight', P(guest_base=(1 << 64) - 32 * MIB)),
    ('place_ends_exactly_at_2_36', 'OK', 'pinbody', bank_placement((1 << 36) - 672 * MIB)),
    ('obs_not_el2', 'NOT_EL2', 'preflight', O(current_el=4)),
    ('obs_e2h_set', 'E2H_SET', 'preflight', O(hcr_el2=0x88000010 | 1 << 34)),
    ('obs_mmu_off', 'EL2_REGIME', 'preflight', O(sctlr_el2=0x30000000)),
    ('obs_tg0_16k', 'EL2_REGIME', 'preflight', O(tcr_el2=0x80843514 | 2 << 14)),
    ('obs_t0sz_25', 'EL2_REGIME', 'preflight', O(tcr_el2=(0x80843514 & ~63) | 25)),
    ('obs_t0sz_16', 'EL2_REGIME', 'preflight', O(tcr_el2=(0x80843514 & ~63) | 16)),
    ('obs_midr_outside_set', 'MIDR_MISMATCH', 'preflight', O(midr_el1=0x413FD0C1)),
    ('obs_midr_high_bits', 'MIDR_MISMATCH', 'preflight', O(midr_el1=0x512F0011 | 1 << 32)),
    ('obs_mpidr', 'MPIDR_MISMATCH', 'preflight', O(mpidr_el1=0x80000100)),
    ('obs_cntfrq', 'CNTFRQ_MISMATCH', 'preflight', O(cntfrq_el0=24000000)),
    ('obs_gicd_base', 'GIC_MISMATCH', 'preflight', O(build_gicd_base=0x17010000)),
    ('obs_gicr_limit', 'GIC_MISMATCH', 'preflight', O(build_gicr_limit=0x17500000)),
    ('obs_timer_intid', 'GIC_MISMATCH', 'preflight', O(build_timer_intid=27)),
    ('obs_gop_width', 'GOP_MISMATCH', 'preflight', O(fb_width=1800)),
    ('obs_gop_base', 'GOP_MISMATCH', 'preflight', O(fb_base=0xE4800000 + 0x1000)),
    ('obs_store_class_not_authorised', 'STORE_NOT_AUTH', 'preflight', O(build_stores=7)),
    ('map_stride_below_descriptor', 'MAP_BAD', 'preflight', lambda s: (setattr(s, 'stride', 32), setattr(s, 'map', struct.pack('<IIQQQ', 7, 0, LOW_BASE, 0, PLACED['place_size'] >> 12) + struct.pack('<IIQQQ', 15, 0, 0x1000, 0, 1)))),
    ('map_size_not_multiple', 'MAP_BAD', 'preflight', set_attr('map', lambda b: b + b'\0')),
    ('map_empty', 'MAP_BAD', 'preflight', lambda s: setattr(s, 'map', b'')),
    ('map_null', 'MAP_BAD', 'preflight', lambda s: setattr(s, 'map', None)),
    ('map_descriptor_unaligned', 'MAP_BAD', 'preflight', map_desc(0, start=0x80000010)),
    ('map_descriptor_no_pages', 'MAP_BAD', 'preflight', map_desc(2, pages=0)),
    ('map_descriptor_wraps', 'MAP_BAD', 'preflight', map_desc(2, start=(1 << 64) - 0x1000, pages=2)),
    ('map_too_many_descriptors', 'MAP_BAD', 'preflight', set_attr('map', lambda b: b + efi_map([(0, 0x100000 + 0x1000 * i, 1, 0x100F) for i in range(1100)]))),
    ('place_conv_descriptor_ends_inside', 'PLACE_NOT_CONV', 'preflight', map_desc(1, pages=0x20000)),
    ('place_split_over_two_descriptors', 'PLACE_NOT_CONV', 'preflight', lambda s: (map_desc(1, pages=0x10000)(s),
                                                                                    setattr(s, 'map', s.map + efi_map([(7, 0xA0000000, 0x14000, 0x100F)])))),
    ('place_in_loader_data', 'PLACE_NOT_CONV', 'preflight', map_desc(1, type=2)),
    ('place_touched_by_reserved', 'PLACE_NOT_CONV', 'preflight', set_attr('map', lambda b: b + efi_map([(0, 0xB1000000, 0x100, 0x100F)]))),
    ('place_not_write_back', 'PLACE_ATTR', 'preflight', map_desc(1, attr=0x1007)),
    ('place_over_declared_file', 'PLACE_DECLARED', 'preflight', lambda s: s.declared.append([0x91000000, 0x10000])),
    ('place_over_wrapping_declared', 'PLACE_DECLARED', 'preflight', lambda s: s.declared.append([(1 << 64) - 0x1000, 0x2000])),
    ('place_too_many_declared', 'PLACE_DECLARED', 'preflight', lambda s: s.declared.extend([[0x200000 + 0x1000 * i, 0x1000] for i in range(17)])),
    ('place_over_framebuffer', 'PLACE_FB', 'preflight', lambda s: (P(fb_base=LOW_BASE + 0x1000000)(s), O(fb_base=LOW_BASE + 0x1000000)(s))),
    ('place_over_avoid_range', 'PLACE_AVOID', 'preflight', lambda s: PIN_AT(0x180, LOW_BASE + 0x2000000, 8)(s)),
    ('guest_shorter_than_head', 'GUEST_TRUNCATED', 'preflight', set_attr('guest', lambda b: b[:100])),
    ('guest_cut_short', 'GUEST_TRUNCATED', 'preflight', set_attr('guest', lambda b: b[:-16])),
    ('guest_trailing_bytes', 'GUEST_SIZE', 'preflight', set_attr('guest', lambda b: b + bytes(16))),
    ('guest_magic', 'GUEST_MAGIC', 'preflight', lambda s: s.guest.__setitem__(1, 0)),
    ('guest_version', 'GUEST_VERSION', 'preflight', H(version=2)),
    ('guest_flags', 'GUEST_FLAGS', 'preflight', H(flags=1)),
    ('guest_head_size', 'GUEST_SIZE', 'preflight', H(head_size=129)),
    ('guest_entry_size', 'GUEST_SIZE', 'preflight', H(entry_size=63)),
    ('guest_table_offset', 'GUEST_SIZE', 'preflight', H(table_offset=192)),
    ('guest_data_offset', 'GUEST_SIZE', 'preflight', H(data_offset=768 + 64)),
    ('guest_reserved0', 'GUEST_RESERVED', 'preflight', H(reserved0=1)),
    ('guest_reserved1', 'GUEST_RESERVED', 'preflight', lambda s: s.guest.__setitem__(0x70, 1)),
    ('guest_count_0', 'GUEST_COUNT', 'preflight', H(entry_count=0)),
    ('guest_count_1025', 'GUEST_COUNT', 'preflight', H(entry_count=1025)),
    ('guest_count_does_not_fit', 'GUEST_COUNT', 'preflight', H(entry_count=1024)),
    ('guest_count_disagrees_with_data_offset', 'GUEST_SIZE', 'preflight', H(entry_count=500)),
    ('guest_load_window', 'GUEST_LOAD', 'preflight', H(load_base=LOW_BASE + 0x2000000)),
    ('guest_pin_file_size', 'GUEST_PIN', 'preflight', lambda s: P(guest_file_size=len(s.guest) + 64)(s)),
    ('guest_pin_chunk_count', 'GUEST_PIN', 'preflight', P(guest_chunk_count=11)),
    ('guest_pin_index_digest', 'GUEST_PIN', 'preflight', P(guest_index_sha256=sb.sha256(b'another table'))),
    ('chunk_kind_0', 'CHUNK_KIND', 'preflight', C(3, kind=0)),
    ('chunk_kind_9', 'CHUNK_KIND', 'preflight', C(3, kind=9)),
    ('chunk_flags_2', 'CHUNK_FLAGS', 'preflight', C(3, flags=2)),
    ('chunk_reserved', 'CHUNK_RESERVED', 'preflight', C(3, reserved=1)),
    ('chunk_zero_flag_on_data', 'CHUNK_ZERO', 'preflight', C(3, flags=1)),
    ('chunk_zero_with_offset', 'CHUNK_ZERO', 'preflight', C(8, file_offset=64)),
    ('chunk_zero_with_digest', 'CHUNK_ZERO', 'preflight', C(8, sha256=sb.sha256(b'x'))),
    ('chunk_data_without_digest', 'CHUNK_ZERO', 'preflight', C(3, sha256=Z)),
    ('chunk_dest_unaligned', 'CHUNK_GEOMETRY', 'preflight', lambda s: C(3, dest=chunk(s, 3)['dest'] + 8)(s)),
    ('chunk_size_zero', 'CHUNK_GEOMETRY', 'preflight', C(3, size=0)),
    ('chunk_size_unaligned', 'CHUNK_GEOMETRY', 'preflight', C(3, size=0x2008)),
    ('chunk_below_window', 'CHUNK_RANGE', 'preflight', C(3, dest=LOW_BASE - 0x1000)),
    ('chunk_at_window_end', 'CHUNK_RANGE', 'preflight', C(3, dest=LOW_BASE + 256 * MIB)),
    ('chunk_straddles_window_end', 'CHUNK_RANGE', 'preflight', C(3, dest=LOW_BASE + 256 * MIB - 0x1000)),
    ('chunk_dest_wraps', 'CHUNK_RANGE', 'preflight', C(3, dest=(1 << 64) - 0x1000)),
    ('chunk_unsorted', 'CHUNK_ORDER', 'preflight', C(2, dest=LOW_BASE + 0xC000 - 0x10)),
    ('chunk_overlaps_previous', 'CHUNK_OVERLAP', 'preflight', C(2, dest=LOW_BASE + 0xC010)),
    ('chunk_same_destination', 'CHUNK_OVERLAP', 'preflight', C(2, dest=LOW_BASE + 0xC000)),
    ('chunk_data_past_end_of_file', 'CHUNK_EOF', 'preflight', lambda s: C(3, file_offset=len(s.guest) - 16)(s)),
    ('chunk_offset_past_end_of_file', 'CHUNK_EOF', 'preflight', lambda s: C(3, file_offset=len(s.guest) + 64)(s)),
    ('chunk_offset_gap', 'CHUNK_OFFSET', 'preflight', lambda s: C(3, file_offset=chunk(s, 3)['file_offset'] + 64)(s)),
    ('chunk_offset_overlaps_previous', 'CHUNK_OFFSET', 'preflight', lambda s: C(3, file_offset=chunk(s, 3)['file_offset'] - 64)(s)),
    ('chunk_pad_not_zero', 'PAD_NONZERO', 'preflight', poke_pad),
    ('chunk_second_params', 'CHUNK_DUP', 'preflight', C(6, kind=sb.KIND_PARAMS)),
    ('chunk_second_device_tree', 'CHUNK_DUP', 'preflight', C(6, kind=sb.KIND_DEVICE_TREE)),
    ('chunk_no_params', 'CHUNK_MISSING', 'preflight', C(1, kind=sb.KIND_OTHER)),
    ('chunk_no_kernel', 'CHUNK_MISSING', 'preflight', chain(C(3, kind=sb.KIND_OTHER), C(4, kind=sb.KIND_OTHER))),
    ('chunk_no_tables', 'CHUNK_MISSING', 'preflight', chain(C(6, kind=sb.KIND_OTHER), C(7, kind=sb.KIND_OTHER))),
    ('chunk_no_handoff', 'CHUNK_MISSING', 'preflight', C(0, kind=sb.KIND_OTHER)),
    ('chunk_last_shorter_than_file', 'GUEST_SIZE', 'preflight', C(9, size=0x430)),
    ('digest_table', 'INDEX_DIGEST', 'preflight', lambda s: s.guest.__setitem__(128 + 64 * 3 + 0x20, s.guest[128 + 64 * 3 + 0x20] ^ 1)),
    ('digest_chunk_data', 'CHUNK_DIGEST', 'preflight', flip_data(3)),
    ('digest_file', 'FILE_DIGEST', 'preflight', P(guest_file_sha256=sb.sha256(b'another file'))),
    ('digest_policy_unknown_bit', 'HASH_POLICY', 'preflight', lambda s: setattr(s, 'policy', 4)),
    ('digest_policy_table_only_misses_data', 'OK', 'preflight', lambda s: (flip_data(3)(s), setattr(s, 'policy', 0))),
    ('digest_policy_file_only_sees_data', 'FILE_DIGEST', 'preflight', lambda s: (flip_data(3)(s), setattr(s, 'policy', 2))),
    ('report_valid', 'OK', 'report', lambda s: None),
    ('report_short', 'REPORT_SIZE', 'report', set_attr('report', lambda b: b[:-1])),
    ('report_head_size', 'REPORT_SIZE', 'report', R(head_size=300)),
    ('report_magic', 'REPORT_MAGIC', 'report', lambda s: s.report.__setitem__(7, 0)),
    ('report_never_written', 'REPORT_MAGIC', 'report', lambda s: s.report.__setitem__(slice(0, 8), bytes(8))),
    ('report_version', 'REPORT_VERSION', 'report', R(version=2)),
    ('report_page_tail', 'REPORT_RESERVED', 'report', lambda s: s.report.__setitem__(0x200, 1)),
    ('report_stage_unknown', 'REPORT_VALUE', 'report', R(stage=14)),
    ('report_refusal_unknown', 'REPORT_VALUE', 'report', R(refusal=99)),
    ('report_copied_over_total', 'REPORT_VALUE', 'report', R(chunks_copied=11)),
    ('report_hashed_over_total', 'REPORT_VALUE', 'report', R(chunks_hashed=11)),
    ('report_too_many_lines', 'REPORT_VALUE', 'report', R(lines_printed=31)),
    ('report_hash_policy', 'REPORT_VALUE', 'report', R(hash_policy=4)),
    ('report_unknown_pin_code', 'REPORT_VALUE', 'report', R(pin_code=99)),
    ('report_refusal_after_the_monitor_ran', 'REPORT_VALUE', 'report', R(refusal=E.COPY_VERIFY, stage=sb.STAGE.MONITOR_RETURNED)),
    ('report_refusal_before_copying_ok', 'OK', 'report', R(refusal=E.PIN_DIGEST, stage=sb.STAGE.FILES_OK)),
]
# Runtime codes no pure check raises; every other refusal must have a seeded record.
RUNTIME_ONLY = {'PLACE_UNREADABLE', 'COPY_VERIFY', 'GIC_NOT_READY'}


def case_scenarios(variant_bank=False):
    out = []
    for name, expected, mode, mutate in CASES:
        s = fixture(variant_bank)
        mutate(s)
        out.append((name, expected, mode, s))
    return out


class Native(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compile_harness('host')


class Layout(Native):
    def test_layout_constants_and_tables_agree_with_the_compiler(self):
        c = sorted(run_harness(['layout']).splitlines())
        py = sorted(sb.layout_lines())
        self.assertEqual(len(c), len(set(c)), 'the harness printed a line twice')
        self.assertEqual(py, c)

    def test_every_constant_the_header_defines_is_compared(self):
        text = HEADER.read_text()
        defined = set(re.findall(r'^#define (SP11SB_[A-Z0-9_]+)\s', text, re.M)) | set(re.findall(r'\b(SP11SB_[A-Z0-9_]+) = ', text))
        printed = {line.split()[0] for line in run_harness(['layout']).splitlines()}
        self.assertEqual(sorted(defined - printed - {'SP11SB_ERRORS', 'SP11SB_STAGES', 'SP11SB_E_OK'}), [])

    def test_names_are_panel_text_numbers_are_unique_and_sides_are_known(self):
        names, codes = [n for _, n, _ in sb.ERRORS], [c for c, _, _ in sb.ERRORS]
        self.assertTrue(all(re.fullmatch(r'[A-Z0-9_]{1,24}', n) for n in names + list(sb.STAGES)))
        self.assertEqual((len(set(names)), len(set(codes))), (len(names), len(codes)))
        self.assertTrue(all(0 < c < 256 and 1 <= s <= 7 for c, _, s in sb.ERRORS))
        self.assertEqual(sorted(E.__dict__[n] for n in names), sorted(codes))

class RoundTrip(unittest.TestCase):
    def test_packing_is_deterministic_and_matches_the_golden_digests(self):
        a, b, again = fixture(False, True), fixture(True, True), fixture(False, True)
        self.assertEqual((bytes(a.pin), bytes(a.guest)), (bytes(again.pin), bytes(again.guest)))
        got = {'low pin': a.pin, 'low guest': a.guest, 'bank pin': b.pin, 'bank guest': b.guest, 'report': valid_report()}
        self.assertEqual({k: hashlib.sha256(bytes(v)).hexdigest() for k, v in got.items()}, GOLDEN)

    def test_every_field_round_trips_through_the_layout_tables(self):
        s = fixture()
        for record, layout, size, buf in (('pin', sb.PIN_LAYOUT, 1024, s.pin), ('guest head', sb.GUEST_LAYOUT, 128, s.guest),
                                          ('chunk', sb.CHUNK_LAYOUT, 64, s.guest[128:]), ('report', sb.REPORT_LAYOUT, 296, valid_report())):
            again = bytearray(size)
            for name, _, _, _ in layout:
                sb.put(again, layout, name, sb.get(buf, layout, name))
            self.assertEqual(bytes(again), bytes(buf[:size]), record)

    def test_a_packer_refuses_what_its_checker_refuses(self):
        s = fixture()
        fields = sb.unpack(s.pin, sb.PIN_LAYOUT)
        with self.assertRaises(ValueError):
            sb.pack_pin(dict(authorised_stores=3, board_id='sp11', build_label='x'))
        with self.assertRaises(ValueError):
            sb.pack_guest(LOW_BASE, 256 * MIB, [dict(kind=sb.KIND_KERNEL, dest=LOW_BASE, data=b'x' * 16)])
        self.assertEqual(fields['magic'], b'SNAPBPIN')

    def test_the_placement_helper_makes_the_rule_the_checker_accepts(self):
        for base in (0x90000000, 0x880000000, (1 << 36) - 672 * MIB):
            for guest in (192 * MIB, 256 * MIB, 384 * MIB):
                s = fixture()
                P(**sb.placement(base, guest))(s)
                want = E.PLACE_LIMIT if base + guest + 48 * MIB + 240 * MIB > 1 << 36 else 0
                self.assertEqual(sb.check_pin(bytes(s.pin), digest=False), want, (hex(base), guest >> 20))

    def test_unpack_guest_names_the_chunks_it_was_given(self):
        head, chunks = sb.unpack_guest(fixture().guest)
        self.assertEqual([(c['kind'], c['dest'] - LOW_BASE) for c in chunks], [(k, o) for k, o, _ in CHUNKS])
        self.assertEqual(head['entry_count'], len(CHUNKS))
        self.assertEqual([c['flags'] for c in chunks].count(sb.CHUNK_ZERO), 1)


class Controls(Native):
    def test_the_unmutated_bundles_pass_both_checkers_low_and_in_the_bank(self):
        scenarios = []
        for bank in (False, True):
            for policy in (0, 1, 2, 3):
                s = fixture(bank)
                s.policy = policy
                scenarios.append((f'valid_{int(bank)}_{policy}', s))
        out = run_harness(['batch'], ''.join(s.text(name, 'preflight') for name, s in scenarios)).splitlines()
        self.assertEqual([line.split()[2] for line in out], ['0'] * 8)
        for name, s in scenarios:
            self.assertEqual(py_run(s, 'preflight'), (0, None), name)

    def test_x3_is_the_destination_of_the_one_params_chunk(self):
        for bank, base in ((False, 0x90000000), (True, 0x880000000)):
            s = fixture(bank)
            self.assertEqual(sb.params_dest(bytes(s.guest)), base + 0xC000)
            self.assertEqual(run_harness(['batch'], s.text('x3', 'params')).split(), ['x3', 'params', str(base + 0xC000)])
        s = fixture()
        C(1, kind=sb.KIND_OTHER)(s)
        self.assertEqual((sb.params_dest(bytes(s.guest)), run_harness(['batch'], s.text('none', 'params')).split()[-1]), (0, '0'))

    def test_the_wrappers_runtime_codes_are_in_the_table_and_the_return_value_is_a_code(self):
        self.assertEqual({n for c, n, s in sb.ERRORS if c in (80, 81, 82)}, RUNTIME_ONLY)
        self.assertEqual(sb.RET_REFUSED + E.PIN_DIGEST, 0x100E)


class SeededBad(Native):
    def test_enough_cases_and_every_refusal_has_one(self):
        refused = {expected for _, expected, _, _ in CASES if expected != 'OK'}
        self.assertGreaterEqual(len(refused), 30)
        self.assertEqual(len({name for name, _, _, _ in CASES}), len(CASES))
        missing = {n for _, n, _ in sb.ERRORS} - refused - RUNTIME_ONLY
        self.assertEqual(sorted(missing), [])

    def test_both_validators_refuse_each_record_with_the_same_code(self):
        cases = case_scenarios()
        out = run_harness(['batch'], ''.join(s.text(name, mode) for name, _, mode, s in cases)).splitlines()
        self.assertEqual(len(out), len(cases))
        for line, (name, expected, mode, s) in zip(out, cases):
            with self.subTest(case=name):
                got_name, got_mode, code, code_name, bad = line.split()
                self.assertEqual((got_name, got_mode), (name, mode))
                py_code, py_bad = py_run(s, mode)
                self.assertEqual((int(code), code_name), (py_code, sb.error_name(py_code)), 'C and Python disagree')
                self.assertEqual(code_name, expected, f'{name}: both say {code_name}, the case expects {expected}')
                self.assertEqual(int(bad) if int(bad) >= 0 else None, py_bad)

    def test_a_record_that_is_wrong_twice_gets_the_first_refusal_in_the_wrappers_order(self):
        both = fixture()
        P(cntfrq_hz=0, guest_base=LOW_BASE + 0x1000000)(both)
        O(current_el=4)(both)
        self.assertEqual(py_run(both, 'preflight')[0], E.PIN_VALUE)
        self.assertEqual(run_harness(['batch'], both.text('twice', 'preflight')).split()[2], str(E.PIN_VALUE))

    def test_the_bad_chunk_is_named(self):
        s = fixture()
        flip_data(5)(s)
        self.assertEqual(py_run(s, 'preflight'), (E.CHUNK_DIGEST, 5))
        self.assertEqual(run_harness(['batch'], s.text('named', 'preflight')).split()[-1], '5')


class Fuzz(Native):
    RUNS = (('pin', 0x1234ABCD, 3000, 0), ('guest', 0xFEEDF00D, 3000, 800), ('guest', 0x0DDBA11, 400, 0), ('map', 0x5EED5EED, 1500, 0),
            ('report', 0xBADC0DE, 1500, 4200), ('path', 0x7A7A7A7A, 600, 24))

    def python_lines(self, s, which, seed, iterations, limit):
        state, out = seed, []
        target = {'pin': s.pin, 'guest': s.guest, 'map': s.map, 'report': s.report}.get(which)
        for i in range(iterations):
            r, state = sb.xorshift(state)
            bit = r & 7
            buf = s.files[i % len(s.files)][0] if which == 'path' else target
            pos = (r >> 8) % (limit if limit and limit < len(buf) else len(buf))
            buf[pos] ^= 1 << bit
            if which == 'pin':
                s.policy = 3
                codes = [py_run(s, m)[0] for m in ('pin', 'pinbody', 'observed', 'place', 'guest', 'digests')]
            elif which == 'guest':
                s.policy = 3
                codes = [py_run(s, m)[0] for m in ('guest', 'digests')]
            else:
                codes = [py_run(s, {'map': 'place', 'report': 'report', 'path': 'files'}[which])[0]]
            out.append(' '.join(['fuzz', which, str(i), str(pos), str(bit)] + [str(c) for c in codes]))
            buf[pos] ^= 1 << bit
        return out

    def test_single_bit_flips_never_crash_the_checks_and_both_implementations_agree(self):
        known = {0} | {c for c, _, _ in sb.ERRORS}
        for which, seed, iterations, limit in self.RUNS:
            with self.subTest(blob=which, limit=limit):
                s = fixture()
                text = s.text('fuzz', 'report') + f'FUZZ {which} {seed:x} {iterations}' + (f' {limit}' if limit else '') + '\n'
                lines = run_harness(['fuzz'], text.replace('RUN report\n', '')).splitlines()
                self.assertEqual(lines, self.python_lines(s, which, seed, iterations, limit))
                codes = {int(c) for line in lines for c in line.split()[5:]}
                self.assertLessEqual(codes, known)
                self.assertIn(0, codes)

    def test_the_mutations_reach_acceptance_and_many_different_refusals(self):
        column = lambda which, seed, n, limit, c: {int(line.split()[5 + c]) for line in self.python_lines(fixture(), which, seed, n, limit)}
        self.assertGreaterEqual(len(column('pin', 0x1234ABCD, 3000, 0, 1)), 10)
        self.assertGreaterEqual(len(column('guest', 0xFEEDF00D, 3000, 800, 0)), 15)
        self.assertGreaterEqual(column('report', 0xBADC0DE, 1500, 4200, 0), {0, E.REPORT_RESERVED, E.REPORT_VALUE})
        self.assertGreaterEqual(column('map', 0x5EED5EED, 1500, 0, 0), {0, E.MAP_BAD, E.PLACE_NOT_CONV})


class Sha256(unittest.TestCase):
    def expect(self):
        lengths = [0, 1, 2, 3, 55, 56, 57, 63, 64, 65, 119, 120, 127, 128, 129, 255, 256, 1000, 4096, 65537]
        return [f'{n} ' + hashlib.sha256(bytes((i * 131 + 7) & 255 for i in range(n))).hexdigest() for n in lengths]

    def test_the_harness_reference_hash_is_sha256(self):
        self.assertEqual(run_harness(['sha']).splitlines(), self.expect())

    def test_the_runners_own_sha256_agrees_and_drives_every_check(self):
        self.assertEqual(run_harness(['sha'], kind='q1n1').splitlines(), self.expect())
        cases = case_scenarios()[:60]
        text = ''.join(s.text(name, mode) for name, _, mode, s in cases)
        self.assertEqual(run_harness(['batch'], text, kind='q1n1'), run_harness(['batch'], text))


class RunnerConstants(unittest.TestCase):
    def test_the_runner_constants_the_header_repeats_are_the_runners(self):
        tree, clang = q1n1_tree(), find_tool('clang')
        if not clang:
            self.skipTest('clang not found')
        if not tree:
            self.skipTest('no q1n1 checkout (set Q1N1_SP11_TREE): the runner headers are not compared')
        cmd = [clang, '-std=gnu11', '-fsyntax-only', '-Wall', '-Wextra', '-Werror', '-DSP11SB_Q1N1_ABI_CHECK', '-DSNAP_PLATFORM_SP11=1',
               '-DQ1N1_SP11_PAYLOAD=1', f'-I{tree / "platform/uefi"}', str(HARNESS)]
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr[-2000:])

    def test_the_runner_leaves_the_payload_stack_alone_between_payloads(self):
        tree = q1n1_tree()
        if not tree:
            self.skipTest('no q1n1 checkout (set Q1N1_SP11_TREE)')
        text = (tree / 'platform/uefi/sp11-payload-run.c').read_text()
        self.assertEqual(len(re.findall(r'guard\[i\] = GUARD_PATTERN', text)), 1)
        self.assertNotIn('memset', text)


class Freestanding(unittest.TestCase):
    def build(self, opt, extra=()):
        clang = find_tool('clang')
        if not clang:
            self.skipTest('clang not found')
        BUILD.mkdir(parents=True, exist_ok=True)
        obj = BUILD / f'unit{opt}.o'
        done = subprocess.run([clang, *MONITOR_FLAGS, opt, '-DSP11SB_FREESTANDING_UNIT', *extra, '-c', str(HARNESS), '-o', str(obj)],
                              capture_output=True, text=True, timeout=300)
        self.assertEqual(done.returncode, 0, done.stderr[-3000:])
        return obj

    def test_compiles_with_the_monitor_flags_without_libc_or_load_address_dependence(self):
        for opt in ('-O1', '-Os', '-O2'):
            with self.subTest(opt=opt):
                obj = self.build(opt)
                nm, readelf, size = find_tool('llvm-nm'), find_tool('llvm-readelf'), find_tool('llvm-size')
                if not (nm and readelf and size):
                    self.skipTest('llvm binutils not found')
                self.assertEqual(subprocess.run([nm, '-u', str(obj)], capture_output=True, text=True).stdout.strip(), '', 'undefined symbols (libc?)')
                relocs = set(re.findall(r'R_AARCH64_\w+', subprocess.run([readelf, '-r', str(obj)], capture_output=True, text=True).stdout))
                self.assertFalse({r for r in relocs if re.search(r'ABS64|ABS32|ABS16|GOT|MOVW|TLS|RELATIVE', r)}, relocs)
                sizes = {m.group(1): int(m.group(2)) for m in re.finditer(r'^(\.\S+)\s+(\d+)', subprocess.run([size, '-A', str(obj)], capture_output=True, text=True).stdout, re.M)}
                self.assertEqual([s for s in sizes if s.startswith(('.data', '.bss', '.tdata', '.tbss', '.init_array')) and sizes[s]], [])

    def test_the_header_alone_compiles_as_c11_on_the_host_and_as_gnu17_freestanding(self):
        clang = find_tool('clang')
        if not clang:
            self.skipTest('clang not found')
        for flags in (['-std=c11'], ['-std=gnu17', *MONITOR_FLAGS, '-O1']):
            done = subprocess.run([clang, *flags, '-fsyntax-only', '-DSP11SB_FREESTANDING_UNIT', str(HARNESS)], capture_output=True, text=True, timeout=120)
            self.assertEqual(done.returncode, 0, done.stderr[-2000:])


def load_monitor_port_test():
    spec = importlib.util.spec_from_file_location('sp11_monitor_port_test', ROOT / 'tools/test-sp11-monitor-port.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def md_rows(text, first):
    """[(cells)] of every table whose header starts with `first`."""
    mp = load_monitor_port_test()
    return [row for header, row, _ in mp.table_rows(text) if header[:len(first)] == first]


def number(cell):
    return int(cell, 0)


class Document(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DOC.read_text()

    def test_size_header_and_what_the_document_says_first(self):
        self.assertLess(len(self.text.encode()), DOC_MAX)
        self.assertTrue(self.text.isascii())
        self.assertTrue(self.text.startswith('<!-- SPDX-License-Identifier: MIT -->\n'))
        self.assertIn('hardware NOT RUN', self.text[:700])
        for needle in ('SNAP_SP11_STICK', 'tools/monitor_baseline.py', 'REPORT.DAT', 'SNAPREP.BIN', 'SNAPMON.BIN', 'GUEST.DAT', 'PIN.DAT'):
            self.assertIn(needle, self.text)
        for prefix in ('/' + 'Users' + '/', '/' + 'home' + '/', '/' + 'tmp' + '/'):
            self.assertNotIn(prefix, self.text)

    def test_every_table_is_well_formed_and_every_label_is_one_of_the_six(self):
        mp = load_monitor_port_test()
        self.assertEqual(mp.check_tables(self.text), [])
        self.assertEqual(mp.check_labels(self.text), [])

    def test_the_repository_citations_hold(self):
        mp = load_monitor_port_test()
        problems, checked = mp.check_citations(self.text, q1n1_tree())
        self.assertEqual(problems, [])
        self.assertGreater(checked, 10)

    def test_the_q1n1_citations_were_checked(self):
        if not q1n1_tree():
            self.skipTest('no q1n1 checkout (set Q1N1_SP11_TREE): the q1n1 citations of the note are not checked')
        self.assertIn('q1n1:', self.text)

    def test_the_refusal_table_is_the_header_table(self):
        rows = md_rows(self.text, ['Code', 'Name', 'Side'])
        got = {number(r[0]): (r[1].strip('`'), r[2]) for r in rows}
        want = {code: (name, ('B' if s & 1 else '-') + ('W' if s & 2 else '-') + ('R' if s & 4 else '-')) for code, name, s in sb.ERRORS}
        self.assertEqual(got, want)

    def test_the_layout_tables_are_the_layouts(self):
        for header, layout in ((['Offset', 'Width', 'Field', 'Unit'], sb.PIN_LAYOUT), (['Offset', 'Width', 'Head field'], sb.GUEST_LAYOUT),
                               (['Offset', 'Width', 'Entry field'], sb.CHUNK_LAYOUT), (['Offset', 'Width', 'Report field'], sb.REPORT_LAYOUT)):
            got = sorted((number(r[0]), number(r[1]), r[2].strip('`')) for r in md_rows(self.text, header))
            self.assertEqual(got, sorted((o, w, n) for n, o, w, _ in layout), header[2])
        for entry in ('base (u64 at 0), size (u64 at 8), tag (u32 at 16), reserved (u32 at 20)',
                      'signature (4 bytes at 0), length (u32 at 4)\nand SHA-256 (32 bytes at 8)'):
            self.assertIn(entry, self.text)
        self.assertEqual([(n, o, w) for n, o, w, _ in sb.AVOID_LAYOUT], [('base', 0, 8), ('size', 8, 8), ('tag', 16, 4), ('reserved', 20, 4)])
        self.assertEqual([(n, o, w) for n, o, w, _ in sb.ACPI_LAYOUT], [('sig', 0, 4), ('length', 4, 4), ('sha256', 8, 32)])

    def test_kinds_stages_and_work_items_are_the_modules(self):
        kinds = {number(r[0]): r[1] for r in md_rows(self.text, ['Kind', 'Name'])}
        self.assertEqual(kinds, {v: k[len('SP11SB_KIND_'):] for k, v in sb.CONSTANTS.items() if k.startswith('SP11SB_KIND_') and k != 'SP11SB_KIND_MAX'})
        stages = {number(r[0]): r[1] for r in md_rows(self.text, ['Stage', 'Name'])}
        self.assertEqual(stages, dict(enumerate(sb.STAGES)))
        self.assertEqual({number(r[0].split()[0]) for r in md_rows(self.text, ['Work item'])}, {3, 4, 6, 7})

    def test_the_constants_the_note_quotes_are_the_headers(self):
        c, text = sb.CONSTANTS, ' '.join(self.text.replace(',', '').split())
        for needle in (f'{c["SP11SB_PIN_BYTES"]} bytes', f'{c["SP11SB_GUEST_HEAD_BYTES"]}-byte head', f'{c["SP11SB_CHUNKS_MAX"]} chunks',
                       f'{c["SP11SB_REPORT_BYTES"]} bytes (0x81000)', f'{c["SP11SB_REPORT_HEAD_BYTES"]} bytes', '2^36', '256 MiB', '240 MiB', '48 MiB', '192 MiB',
                       '30 lines', f'at most {c["SP11SB_REPORTER_LINES"]} lines', '6 + 6 + 30 + 40 = 82', 'one canonical layout'):
            self.assertIn(needle, text, needle)

    def test_the_decisions_are_listed_and_none_is_taken(self):
        for n in range(1, 11):
            self.assertRegex(self.text, rf'\bD{n}\b')
        self.assertNotRegex(self.text, r'(?i)\bI (decided|chose) D\d')


class Hygiene(unittest.TestCase):
    FILES = (HEADER, MODULE, HARNESS, THIS, DOC)

    def test_licence_lines_sizes_and_no_machine_text(self):
        for path in self.FILES:
            text = path.read_text()
            self.assertIn('SPDX-License-Identifier: MIT', text.splitlines()[0] if path.suffix != '.py' else text[:100], path.name)
            if path.suffix in ('.c', '.h', '.py'):
                self.assertLess(len(text.splitlines()), 1500, path.name)
                self.assertLess(len(text.encode()), 50 * 1024, path.name)
            for prefix in ('/' + 'Users' + '/', '/' + 'home' + '/', '/' + 'tmp' + '/', 'mk' + 'temp'):
                self.assertNotIn(prefix, text, f'{path.name}: {prefix}')
            self.assertIsNone(re.search(r'(?i)\b([0-9a-f]{2}:){5}[0-9a-f]{2}\b', text), f'{path.name}: a MAC-like string')

    def test_no_existing_build_includes_the_header(self):
        needle = 'sp11-stick-bundle'
        for rel in ('probes/run.py', 'probes/xnuboot.py', 'config/monitor-flags.json', 'tools/monitor_baseline.py', 'tools/cycle.py'):
            self.assertNotIn(needle, (ROOT / rel).read_text(), rel)


GOLDEN = {'low pin': 'ba1e56d9b2446f096214a3c3695b4a2b54d7df8f4e769cf71080bad8ae97d3aa',
          'low guest': '557b4647dbbad5b57c07baeaddff512ddb61210344fcbee83eb2b5858cea9ae8',
          'bank pin': '2be5f2f44b58dde9e46a2f4de9b6e898f6ecb11922e5ee61a0703a16bd64d909',
          'bank guest': '2f6905d8c3baafd27571250fa6734691562033625229030ad1807429094e20ef',
          'report': '180425a8e7670ecb4db093cefa007a01b11e94fe31c80d7126b0cb88305734f9'}


if __name__ == '__main__':
    unittest.main()
