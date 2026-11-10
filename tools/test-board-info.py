#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests for the q1n1 -> monitor board-info ABI: compat/board-info.{h,c} and probes/boardinfo.py.

Nothing here touches hardware. The C validator is built for the host with AddressSanitizer and
UndefinedBehaviorSanitizer (tools/test-board-info.c) and the Python mirror is run on the very same bytes, so a
divergence between the two, or a sanitizer report, fails the test. Every blob is synthetic or comes from
config/boards; no vendor data is involved. Without clang the native classes report SKIP and the Python-only classes
still run.

    python3 tools/test-board-info.py
"""
import contextlib
import dataclasses
import importlib.util
import io
import json
from pathlib import Path
import random
import re
import shutil
import struct
import subprocess
import sys
import unittest
from unittest import mock
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'probes'))
sys.path.insert(0, str(ROOT / 'tools'))
import board_descriptor as bd
import boardinfo as bi

BUILD = ROOT / 'build' / 'test-board-info'
TOOLS = ('/opt/homebrew/opt/llvm/bin',)
HOME_PREFIXES = ('/' + 'Users' + '/', '/' + 'home' + '/')
BATCH_NULL_BLOB, BATCH_NULL_OUT = 0xffffffff, 0xfffffffe
LIMIT = 1 << 36
A4_FILES = ('compat/board-info.h', 'compat/board-info.c', 'probes/boardinfo.py', 'tools/test-board-info.c',
            'tools/test-board-info.py')


def _load_descriptor_fixtures():
    spec = importlib.util.spec_from_file_location('board_descriptor_fixtures', ROOT / 'tools' / 'test-board-descriptor.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tbd = _load_descriptor_fixtures()
S, chain, unknowns, dropprov = tbd.S, tbd.chain, tbd.unknowns, tbd.dropprov


def find_tool(name):
    return shutil.which(name) or next((str(Path(d) / name) for d in TOOLS if (Path(d) / name).exists()), None)


_NATIVE = {}


def native_harness():
    """Build tools/test-board-info.c once per run; SkipTest when there is no compiler."""
    if 'exe' not in _NATIVE:
        clang = find_tool('clang')
        if not clang:
            raise unittest.SkipTest('clang not found')
        BUILD.mkdir(parents=True, exist_ok=True)
        exe = BUILD / 'harness'
        cmd = [clang, '-std=c11', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
               '-fno-sanitize-recover=undefined', f'-I{ROOT / "compat"}', str(ROOT / 'tools' / 'test-board-info.c'),
               str(ROOT / 'compat' / 'board-info.c'), '-o', str(exe)]
        built = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if built.returncode:
            raise AssertionError(f'host build of the board-info harness failed:\n{built.stderr[-3000:]}')
        _NATIVE['exe'] = exe
    return _NATIVE['exe']


def _batch(records):
    payload = bytearray()
    for blob, mode in records:
        data = b'' if blob is None else bytes(blob)
        payload += struct.pack('<II', len(data), mode) + data
    return subprocess.run([str(native_harness()), 'batch'], input=bytes(payload), capture_output=True, timeout=300)


def native_run(records):
    """[(blob or None, mode)] -> [(error code, unready mask or None)] from the sanitizer-built C validator."""
    proc = _batch(records)
    if proc.returncode:
        lo, hi = 0, len(records)        # bisect to the record that made the harness fail
        while hi - lo > 1:
            mid = (lo + hi) // 2
            lo, hi = (lo, mid) if _batch(records[lo:mid]).returncode else (mid, hi)
        raise AssertionError(f'harness exited {proc.returncode} on record {lo}: {proc.stderr.decode(errors="replace")[:2500]}')
    verdicts = []
    for line in proc.stdout.decode().splitlines():
        code, mask = line.split()
        verdicts.append((int(code), None if mask == '-' else int(mask, 16)))
    if len(verdicts) != len(records):
        raise AssertionError(f'harness answered {len(verdicts)} of {len(records)} records')
    return verdicts


def refresh_crc(buf):
    total = struct.unpack_from('<I', buf, 16)[0]
    struct.pack_into('<I', buf, 20, zlib.crc32(bytes(buf[:20]) + bytes(4) + bytes(buf[24:total])) & 0xffffffff)


def patch(blob, offset, fmt, value, fix_crc=True):
    buf = bytearray(blob)
    struct.pack_into(fmt, buf, offset, value)
    if fix_crc:
        refresh_crc(buf)
    return bytes(buf)


def padded(blob, extra, minor=0):
    buf = bytearray(blob) + extra
    struct.pack_into('<I', buf, 16, len(buf))
    struct.pack_into('<I', buf, 12, minor)
    refresh_crc(buf)
    return bytes(buf)


BASE_BOARD = tbd.board()
BASE = bi.pack(BASE_BOARD)
A16 = bi.pack(bd.load(ROOT / 'config' / 'boards' / 'a16.json'))
SP11 = bi.pack(bd.load(ROOT / 'config' / 'boards' / 'sp11.json'))
F0 = bi.decode(BASE)
KNOWN_BASE = F0['known']


def forge(*edits, **encode_kwargs):
    f = bi.decode(BASE)
    for edit in edits:
        edit(f)
    return bi.encode(f, **encode_kwargs)


def top(**changes):
    return lambda f: f.update(changes)


def item(array, index, **changes):
    return lambda f: f[array][index].update(changes)


def slot(array, index, value):
    def edit(f):
        f[array][index] = value
    return edit


def clear(group):
    return top(known=KNOWN_BASE & ~bi.KNOWN[group])


def name16(text):
    return text.encode().ljust(16, b'\0')


def label(code):
    return f'{code} ({bi.ERROR_NAMES.get(code, "?")})'


def cpus(clusters, cores, first=0):
    """Edits for a consistent clusters x cores topology with a redistributor per cpu; cluster numbers start at `first`."""
    n = clusters * cores
    return (top(cluster_count=clusters, cores_per_cluster=cores, mpidr_count=n, gicr_size=0x40000 * n),
            *(slot('mpidr', i, ((first + i // cores) << 16) | ((i % cores) << 8) if i < n else 0) for i in range(32)))


class Parity(unittest.TestCase):
    """Base for tests that need the native validator."""

    @classmethod
    def setUpClass(cls):
        native_harness()

    def expect(self, cases):
        """cases: (label, blob, expected code). C and Python must both return exactly that code."""
        verdicts = native_run([(blob, 0) for _, blob, _ in cases])
        for (name, blob, expected), (c_code, c_mask) in zip(cases, verdicts):
            with self.subTest(name):
                self.assertEqual(label(c_code), label(expected), 'C validator')
                self.assertEqual(label(bi.check(blob)), label(expected), 'Python validator')
                if expected == bi.OK:
                    self.assertEqual(c_mask, bi.unready(bi.decode(blob)), 'unready mask')


HEADER_CASES = (
    ('magic first byte', patch(BASE, 0, '<B', ord('X')), bi.ERR_MAGIC),
    ('magic last byte', patch(BASE, 7, '<B', ord('J')), bi.ERR_MAGIC),
    ('major 0', patch(BASE, 8, '<I', 0), bi.ERR_MAJOR),
    ('major 2', patch(BASE, 8, '<I', 2), bi.ERR_MAJOR),
    ('major max', patch(BASE, 8, '<I', 0xffffffff), bi.ERR_MAJOR),
    ('total_length 0', patch(BASE, 16, '<I', 0), bi.ERR_LENGTH),
    ('total_length 7', patch(BASE, 16, '<I', 7), bi.ERR_LENGTH),
    ('total_length below the 1.0 size', patch(BASE, 16, '<I', 2160), bi.ERR_LENGTH),
    ('total_length unaligned', patch(BASE, 16, '<I', 2172), bi.ERR_LENGTH),
    ('total_length 2169', patch(BASE, 16, '<I', 2169), bi.ERR_LENGTH),
    ('total_length above the maximum', patch(BASE, 16, '<I', 4104), bi.ERR_LENGTH),
    ('total_length 0xfffffff8', patch(BASE, 16, '<I', 0xfffffff8), bi.ERR_LENGTH),
    ('total_length beyond the bytes given', patch(BASE, 16, '<I', 2176), bi.ERR_TRUNCATED),
    ('crc bit', patch(BASE, 20, '<I', F0['crc32'] ^ 1, fix_crc=False), bi.ERR_CRC),
    ('body bit with a stale crc', patch(BASE, 2000, '<B', BASE[2000] ^ 0x10, fix_crc=False), bi.ERR_CRC),
    ('crc over the bytes after the 1.0 layout', patch(padded(BASE, bytes(8)), 2170, '<B', 1, fix_crc=False), bi.ERR_CRC),
    ('required flag', patch(BASE, 24, '<I', 1), bi.ERR_REQUIRED_FLAGS),
    ('required flag, newer minor', patch(patch(BASE, 12, '<I', 1), 24, '<I', 0x80000000), bi.ERR_REQUIRED_FLAGS),
    ('optional flag at minor 0', patch(BASE, 28, '<I', 1), bi.ERR_OPTIONAL_FLAGS),
    ('exactly the 32-byte header, bad magic', b'X' + BASE[1:32], bi.ERR_MAGIC),
)

COUNT_CASES = tuple((text, forge(top(**{field: value})), bi.ERR_COUNT) for text, field, value in (
    ('midr_count 5', 'midr_count', 5), ('mpidr_count 33', 'mpidr_count', 33), ('ecam_count 5', 'ecam_count', 5),
    ('smmu_count 9', 'smmu_count', 9), ('range_count 33', 'range_count', 33),
    ('midr_count huge', 'midr_count', 0xffffffff), ('range_count huge', 'range_count', 0xffffffff),
    ('mpidr_count 2^31', 'mpidr_count', 1 << 31)))

_RES = bi.ERR_RESERVED
# Groups whose fields are checked as a set: each field must be refused on its own while the group is unknown.
ONE_SIDED = (('COUNTS', ('cluster_count', 'cores_per_cluster')), ('AFFINITY', ('affinity_mask', 'cluster_mask')),
             ('GICD', ('gicd_base', 'gicd_size')), ('GICR', ('gicr_base', 'gicr_stride', 'gicr_size')))
RESERVED_CASES = (
    *((f'reserved{i}', forge(top(**{f'reserved{i}': 1})), _RES) for i in range(6)),
    ('unknown known bit', forge(top(known=KNOWN_BASE | 1 << 15)), _RES),
    ('every known bit', forge(top(known=0xffffffff)), _RES),
    ('device_present beyond the vocabulary', forge(top(device_present=F0['device_present'] | 1 << 10)), _RES),
    ('device_absent bit 31', forge(top(device_absent=F0['device_absent'] | 1 << 31)), _RES),
    ('device_mmio bit 10', forge(top(device_mmio=F0['device_mmio'] | 1 << 10)), _RES),
    *((f'{group} data while unknown', forge(clear(group)), _RES) for group in bi.KNOWN),
    *((f'{group} unknown with only {keep}', forge(clear(group), top(**{n: 0 for n in fields if n != keep})), _RES)
      for group, fields in ONE_SIDED for keep in fields),
    *((f'timer ppi {k} alone while unknown',
       forge(clear('TIMER_PPI'), top(timer_ppi=[p * (i == k) for i, p in enumerate(F0['timer_ppi'])])), _RES)
      for k in range(4)),
    ('its window without an its', forge(top(its_present=0)), _RES),
    ('its base alone without an its', forge(top(its_present=0, its_size=0)), _RES),
    ('its size alone without an its', forge(top(its_present=0, its_base=0)), _RES),
    ('midr_value beyond the count', forge(slot('midr_value', 1, 1)), _RES),
    ('midr_mask beyond the count', forge(slot('midr_mask', 1, 1)), _RES),
    ('mpidr beyond the count', forge(slot('mpidr', 12, 1)), _RES),
    ('ecam entry beyond the count', forge(item('ecam', 1, base=0x1000)), _RES),
    ('ecam reserved', forge(item('ecam', 0, reserved=1)), _RES),
    ('smmu entry beyond the count', forge(item('smmu', 3, size=0x1000)), _RES),
    ('smmu reserved', forge(item('smmu', 0, reserved=b'\1' + bytes(6))), _RES),
    ('range entry beyond the count', forge(item('range', 4, size=0x1000)), _RES),
    ('range reserved', forge(item('range', 0, reserved=b'\1' + bytes(4))), _RES),
    *((f'range {field} beyond the count', forge(item('range', 4, **{field: value})), _RES)
      for field, value in (('name', name16('late')), ('kind', 1), ('geometry_known', 1), ('device', 1))),
    ('geometry-less range with a base', forge(item('range', 1, geometry_known=0)), _RES),
    ('geometry-less range with only a base', forge(item('range', 1, geometry_known=0, size=0)), _RES),
    ('geometry-less range with only a size', forge(item('range', 1, geometry_known=0, base=0)), _RES),
)

_VAL = bi.ERR_VALUE
VALUE_CASES = (
    ('board id uppercase', forge(top(board_id=name16('SP11'))), _VAL),
    ('board id empty', forge(top(board_id=bytes(16))), _VAL),
    ('board id without a NUL', forge(top(board_id=b'a' * 16)), _VAL),
    ('board id with data after a NUL', forge(top(board_id=b'ab\0cd'.ljust(16, b'\0'))), _VAL),
    ('board id leading hyphen', forge(top(board_id=name16('-ab'))), _VAL),
    ('board id with a space', forge(top(board_id=name16('a b'))), _VAL),
    ('status 5', forge(top(status=5)), _VAL),
    ('status huge', forge(top(status=0xffffffff)), _VAL),
    ('soc_id 0', forge(top(soc_id=0)), _VAL),
    ('soc_id 3', forge(top(soc_id=3)), _VAL),
    ('midr mask lacks pinned bits', forge(slot('midr_mask', 0, 0xffff0000)), _VAL),
    ('midr mask lacks the part number only', forge(slot('midr_value', 0, 0x510f0000), slot('midr_mask', 0, 0xff0f0000)), _VAL),
    ('midr value outside its mask', forge(slot('midr_value', 0, F0['midr_value'][0] | 1)), _VAL),
    ('midr architecture nibble not 0xf', forge(slot('midr_value', 0, 0x510e1230)), _VAL),
    ('midr duplicate', forge(top(midr_count=2), slot('midr_value', 1, F0['midr_value'][0]),
                             slot('midr_mask', 1, F0['midr_mask'][0])), _VAL),
    ('midr known with no entry', forge(top(midr_count=0), slot('midr_value', 0, 0), slot('midr_mask', 0, 0)), _VAL),
    ('cluster_count 0', forge(top(cluster_count=0)), _VAL),
    ('cluster_count 9', forge(top(cluster_count=9)), _VAL),
    ('cores_per_cluster 0', forge(top(cores_per_cluster=0)), _VAL),
    ('cores_per_cluster 17', forge(top(cores_per_cluster=17)), _VAL),
    ('more than 32 cpus', forge(top(cluster_count=8, cores_per_cluster=5)), _VAL),
    ('33 cpus as 3 clusters of 11', forge(top(cluster_count=3, cores_per_cluster=11)), _VAL),
    ('1 cluster of 17 cores', forge(*cpus(1, 17)), _VAL),
    ('9 clusters of 1 core', forge(*cpus(9, 1)), _VAL),
    ('affinity mask 0', forge(top(affinity_mask=0)), _VAL),
    ('affinity mask beyond the MPIDR bits', forge(top(affinity_mask=F0['affinity_mask'] | 1 << 63)), _VAL),
    ('cluster mask 0', forge(top(cluster_mask=0)), _VAL),
    ('cluster mask outside the affinity mask', forge(top(cluster_mask=F0['cluster_mask'] | 1 << 24)), _VAL),
    ('mpidrs known with none listed', forge(top(mpidr_count=0), *[slot('mpidr', i, 0) for i in range(12)]), _VAL),
    ('mpidr duplicate', forge(slot('mpidr', 1, F0['mpidr'][0])), _VAL),
    ('gic version 0', forge(top(gic_version=0)), _VAL),
    ('gic version 2', forge(top(gic_version=2)), _VAL),
    ('gic version 5', forge(top(gic_version=5)), _VAL),
    ('gicd smaller than a frame', forge(top(gicd_size=0x8000)), _VAL),
    ('gicd size 0', forge(top(gicd_size=0)), _VAL),
    ('gicd size unaligned', forge(top(gicd_size=0x18000)), _VAL),
    ('gicd base unaligned', forge(top(gicd_base=0x17001000)), _VAL),
    ('gicr stride below the v4 minimum', forge(top(gicr_stride=0x20000, gicr_size=0x20000 * 12)), _VAL),
    ('gicr stride unaligned', forge(top(gicr_stride=0x44000, gicr_size=0x44000 * 12)), _VAL),
    ('gicr size not a stride multiple', forge(top(gicr_size=0x310000)), _VAL),
    ('gicr size 0', forge(top(gicr_size=0)), _VAL),
    ('gicr base unaligned', forge(top(gicr_base=0x17080100)), _VAL),
    ('gicr for more than 32 cpus', forge(top(gicr_size=0x40000 * 33)), _VAL),
    ('its_present 2', forge(top(its_present=2)), _VAL),
    ('its size 0', forge(top(its_size=0)), _VAL),
    ('its base unaligned', forge(top(its_base=0x17040100)), _VAL),
    ('its size unaligned', forge(top(its_size=0x40100)), _VAL),
    ('smmu name uppercase', forge(item('smmu', 0, name=name16('APPS'))), _VAL),
    ('smmu duplicate name', forge(item('smmu', 1, name=F0['smmu'][0]['name'])), _VAL),
    # The byte after a name is the low byte of the entry's base: a reader that strays one byte past the name sees it.
    ('smmu duplicate name, bases differ in the low byte', forge(item('smmu', 1, name=F0['smmu'][0]['name'], base=0x15400001)), _VAL),
    ('smmu name of 16 characters', forge(item('smmu', 0, name=b'a' * 16, base=0x15000061)), _VAL),
    ('smmu kind 2', forge(item('smmu', 0, kind=2)), _VAL),
    ('timer below 1 MHz', forge(top(timer_hz=999999)), _VAL),
    ('timer above 1 GHz', forge(top(timer_hz=1_000_000_001)), _VAL),
    ('no timer ppi', forge(top(timer_ppi=[0, 0, 0, 0])), _VAL),
    ('timer ppi 15', forge(top(timer_ppi=[15, 30, 27, 26])), _VAL),
    ('timer ppi 32', forge(top(timer_ppi=[32, 30, 27, 26])), _VAL),
    ('timer ppi duplicated', forge(top(timer_ppi=[30, 30, 27, 26])), _VAL),
    ('ecam base unaligned to 1 MiB', forge(item('ecam', 0, base=0x40080000)), _VAL),
    ('ecam end before start', forge(item('ecam', 0, start_bus=5, end_bus=4)), _VAL),
    ('ecam duplicate segment', forge(top(ecam_count=2), item('ecam', 1, base=0x50000000)), _VAL),
    ('range duplicate name', forge(item('range', 1, name=F0['range'][0]['name'])), _VAL),
    ('range duplicate name, bases differ in the low byte', forge(item('range', 1, name=F0['range'][0]['name'], base=0x3d00001)), _VAL),
    ('range name of 16 characters', forge(item('range', 1, name=b'a' * 16, base=0x3d00061)), _VAL),
    ('range kind 2', forge(item('range', 1, kind=2)), _VAL),
    ('range geometry flag 2', forge(item('range', 1, geometry_known=2)), _VAL),
    ('mmio range without geometry', forge(item('range', 1, geometry_known=0, base=0, size=0)), _VAL),
    ('range device beyond the vocabulary', forge(item('range', 1, device=10)), _VAL),
    ('dram range with a device', forge(item('range', 0, device=0)), _VAL),
    ('ipa_bits 35', forge(top(ipa_bits=35)), _VAL),
    ('ipa_bits 37', forge(top(ipa_bits=37)), _VAL),
    ('ipa_bits 0', forge(top(ipa_bits=0)), _VAL),
)

_CON = bi.ERR_CONSISTENCY
CONSISTENCY_CASES = (
    ('midr implementer is not Qualcomm', forge(slot('midr_value', 0, 0x410f1230)), _CON),
    ('midr without a soc', forge(top(soc_id=0, known=KNOWN_BASE & ~bi.KNOWN['SOC'])), _CON),
    ('mpidrs without a layout', forge(top(affinity_mask=0, cluster_mask=0, known=KNOWN_BASE & ~bi.KNOWN['AFFINITY'])), _CON),
    ('mpidrs without a layout or counts', forge(top(affinity_mask=0, cluster_mask=0, cluster_count=0, cores_per_cluster=0,
                                                    mpidr_count=1, known=KNOWN_BASE & ~(bi.KNOWN['AFFINITY'] | bi.KNOWN['COUNTS'])),
                                                *(slot('mpidr', i, 0) for i in range(1, 12))), _CON),
    ('mpidr count differs from the topology', forge(top(mpidr_count=11), slot('mpidr', 11, 0)), _CON),
    ('mpidr outside the affinity mask', forge(slot('mpidr', 0, 1 << 24)), _CON),
    ('mpidr clusters unbalanced', forge(slot('mpidr', 3, 0x10400)), _CON),
    ('gicr smaller than the cpu count', forge(top(gicr_size=0x40000 * 4)), _CON),
    ('device present and absent', forge(top(device_absent=F0['device_absent'] | 1)), _CON),
    ('device present and absent without mmio', forge(top(device_absent=F0['device_absent'] | 1 << 8)), _CON),
    ('mmio device that is absent', forge(top(device_mmio=F0['device_mmio'] | 1 << 3)), _CON),
    ('range names an absent device', forge(item('range', 3, device=3)), _CON),
    ('extra range for an absent device', forge(top(range_count=5), item('range', 4, name=name16('uart0'), base=0x9a0000,
                                              size=0x1000, kind=1, geometry_known=1, device=3)), _CON),
    ('mmio claimed without a range', forge(top(device_mmio=F0['device_mmio'] | 1 << 5)), _CON),
    ('range without the mmio claim', forge(top(device_mmio=F0['device_mmio'] & ~1)), _CON),
)

WINDOW_CASES = (
    ('empty range', forge(item('range', 1, size=0)), bi.ERR_WINDOW),
    ('range base unaligned', forge(item('range', 1, base=0x3d00800)), bi.ERR_WINDOW),
    ('range size unaligned', forge(item('range', 1, size=0x40800)), bi.ERR_WINDOW),
    ('range at the 36-bit limit', forge(item('range', 0, base=LIMIT, size=0x1000)), bi.ERR_WINDOW),
    ('range ending one page beyond the limit', forge(item('range', 0, size=LIMIT - 0x80000000 + 0x1000)), bi.ERR_WINDOW),
    ('range whose end wraps', forge(item('range', 0, base=0xfffffffffffff000, size=0x2000)), bi.ERR_WINDOW),
    ('range of 2^64 - 4 KiB', forge(item('range', 0, base=0x1000, size=0xfffffffffffff000)), bi.ERR_WINDOW),
    ('gicd at the limit', forge(top(gicd_base=LIMIT)), bi.ERR_WINDOW),
    ('ecam ending one MiB beyond the limit', forge(item('ecam', 0, base=LIMIT - (1 << 20), start_bus=0, end_bus=1)), bi.ERR_WINDOW),
    ('two ranges overlap', forge(item('range', 2, base=0x3d10000)), bi.ERR_OVERLAP),
    ('gicd inside gicr', forge(top(gicd_base=F0['gicr_base'])), bi.ERR_OVERLAP),
    ('ecam over dram', forge(item('ecam', 0, base=0x80000000)), bi.ERR_OVERLAP),
    ('range on the its', forge(item('range', 3, base=F0['its_base'], size=0x1000)), bi.ERR_OVERLAP),
)

ACCEPT_CASES = (
    ('synthetic boot-ready board', BASE, bi.OK),
    ('a16 descriptor', A16, bi.OK),
    ('sp11 descriptor', SP11, bi.OK),
    ('board id of 15 characters', forge(top(board_id=name16('a' * 15))), bi.OK),
    ('timer at 1 MHz', forge(top(timer_hz=1_000_000)), bi.OK),
    ('timer at 1 GHz', forge(top(timer_hz=1_000_000_000)), bi.OK),
    ('timer ppi 16 and 31', forge(top(timer_ppi=[16, 31, 27, 26])), bi.OK),
    ('gic v3 with the smaller stride', forge(top(gic_version=3, gicr_stride=0x20000, gicr_size=0x20000 * 12)), bi.OK),
    ('dram ending one page below the limit', forge(item('range', 0, size=LIMIT - 0x1000 - 0x80000000)), bi.OK),
    # The end is exclusive: a window whose last byte is (1 << 36) - 1 is in range, as in shadow/stage2.c.
    ('dram ending exactly at the limit', forge(item('range', 0, size=LIMIT - 0x80000000)), bi.OK),
    ('ecam ending exactly at the limit', forge(item('ecam', 0, base=LIMIT - (1 << 20), start_bus=0, end_bus=0)), bi.OK),
    ('adjacent ranges', forge(item('range', 2, base=0x3d40000)), bi.OK),
    ('ecam ending one MiB below the limit', forge(item('ecam', 0, base=LIMIT - (2 << 20), start_bus=0, end_bus=0)), bi.OK),
    ('bytes after total_length', BASE + b'\xff' * 64, bi.OK),
    ('newer minor with an extra payload', padded(BASE, bytes(range(8)), minor=1), bi.OK),
    ('largest total_length', padded(BASE, bytes(4096 - 2168), minor=1), bi.OK),
    ('minor 1 with an optional flag', patch(patch(BASE, 12, '<I', 1), 28, '<I', 1), bi.OK),
    ('every list at its maximum', bi.pack(bd.validate(tbd.at_limits())), bi.OK),
    ('1 cpu in 1 cluster', forge(*cpus(1, 1)), bi.OK),
    ('32 cpus as 8 clusters of 4', forge(*cpus(8, 4)), bi.OK),
    ('32 cpus as 2 clusters of 16', forge(*cpus(2, 16)), bi.OK),
    ('clusters numbered from 1', forge(*cpus(3, 4, first=1)), bi.OK),
    ('its known and absent', forge(top(its_present=0, its_base=0, its_size=0)), bi.OK),
    ('absent gpu without a range', forge(top(device_present=F0['device_present'] & ~1, device_absent=F0['device_absent'] | 1,
                                             device_mmio=F0['device_mmio'] & ~1, range_count=3),
                                         *(slot('range', i, F0['range'][i + 1]) for i in range(1, 4))), bi.OK),
    ('midrs that differ only in the mask', forge(top(midr_count=2), slot('midr_value', 1, F0['midr_value'][0]),
                                                 slot('midr_mask', 1, 0xff0fffff)), bi.OK),
    ('midrs that differ only in the value', forge(top(midr_count=2), slot('midr_value', 1, 0x510f1240),
                                                  slot('midr_mask', 1, F0['midr_mask'][0])), bi.OK),
    # The unused ecam slot after the last entry is zero, which would read as a 1 MiB window at address 0.
    ('a window at address zero', forge(item('range', 1, base=0, size=0x1000)), bi.OK),
    ('a range for a device whose state is unknown',
     forge(top(device_present=F0['device_present'] & ~(1 << 5), device_mmio=F0['device_mmio'] & ~(1 << 2)),
           item('range', 3, device=5)), bi.OK),
)


class Layout(Parity):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        out = subprocess.run([str(native_harness()), 'layout'], capture_output=True, text=True, timeout=60, check=True)
        cls.c = {name: int(value) for name, value in (line.split() for line in out.stdout.splitlines())}

    def test_sizes_and_offsets_match(self):
        c = self.c
        self.assertEqual((c['sizeof.board_info'], bi.V1_SIZE), (2168, 2168))
        self.assertEqual((c['sizeof.board_info_header'], c['sizeof.board_info_ecam']), (32, bi.ECAM.size))
        self.assertEqual((c['sizeof.board_info_smmu'], c['sizeof.board_info_range']), (bi.SMMU.size, bi.RANGE.size))
        for field, offset in bi.OFFSETS.items():
            with self.subTest(field):
                struct_name = 'board_info_header' if offset < bi.HEADER_SIZE else 'board_info'
                self.assertEqual(c[f'offsetof.{struct_name}.{field}'], offset)
        for field, offset in (('base', 0), ('segment', 8), ('start_bus', 10), ('end_bus', 11), ('reserved', 12)):
            self.assertEqual(c[f'offsetof.board_info_ecam.{field}'], offset)
        for field, offset in (('base', 16), ('size', 24), ('kind', 32), ('reserved', 33)):
            self.assertEqual(c[f'offsetof.board_info_smmu.{field}'], offset)
        for field, offset in (('base', 16), ('size', 24), ('kind', 32), ('geometry_known', 33), ('device', 34),
                              ('reserved', 35)):
            self.assertEqual(c[f'offsetof.board_info_range.{field}'], offset)

    def test_constants_match(self):
        c = self.c
        pairs = {
            'BOARD_INFO_ABI_MAJOR': bi.ABI_MAJOR, 'BOARD_INFO_ABI_MINOR': bi.ABI_MINOR, 'BOARD_INFO_MAX_LENGTH': bi.MAX_LENGTH,
            'BOARD_INFO_NAME_LEN': bi.NAME_LEN, 'BOARD_INFO_MAX_MIDR': bd.MAX_MIDR, 'BOARD_INFO_MAX_CPUS': bd.MAX_CPUS,
            'BOARD_INFO_MAX_ECAM': bd.MAX_ECAM, 'BOARD_INFO_MAX_SMMU': bd.MAX_SMMU, 'BOARD_INFO_MAX_RANGES': bd.MAX_RANGES,
            'BOARD_INFO_IPA_BITS': bd.MONITOR_IPA_BITS, 'BOARD_INFO_PAGE': bd.PAGE, 'BOARD_INFO_DEVICE_NONE': bi.DEVICE_NONE,
            'BOARD_INFO_FLAGS_REQUIRED_KNOWN': bi.FLAGS_REQUIRED_KNOWN, 'BOARD_INFO_FLAGS_OPTIONAL_KNOWN': bi.FLAGS_OPTIONAL_KNOWN,
            'BOARD_INFO_STATUS_COUNT': len(bd.EVIDENCE_LABELS), 'BOARD_INFO_SOC_COUNT': bi.SOC_COUNT,
            'BOARD_INFO_DEV_COUNT': len(bd.DEVICE_VOCABULARY), 'BOARD_INFO_KNOWN_ALL': bi.KNOWN_ALL,
            'BOARD_INFO_RANGE_DRAM': bd.RANGE_KINDS.index('dram'), 'BOARD_INFO_RANGE_MMIO': bd.RANGE_KINDS.index('mmio'),
            'BOARD_INFO_SMMU_V3': bd.SMMU_KINDS.index('smmu-v3'), 'BOARD_INFO_SMMU_MMU500': bd.SMMU_KINDS.index('mmu-500'),
            'BOARD_INFO_SOC_NONE': 0, 'BOARD_INFO_SOC_X1E80100': bd.SOC_IDS['X1E80100'],
            'BOARD_INFO_SOC_X2E_96_100': bd.SOC_IDS['X2E-96-100'], 'BOARD_INFO_OK': bi.OK,
            'BOARD_INFO_ERR_COUNT_ALL': max(bi.STRERROR) + 1,
        }
        for index, status in enumerate(bd.EVIDENCE_LABELS):
            pairs[f'BOARD_INFO_STATUS_{status}'] = index
        for index, device in enumerate(bd.DEVICE_VOCABULARY):
            pairs['BOARD_INFO_DEV_' + device.upper().replace('-', '_')] = index
        for group, bit in bi.KNOWN.items():
            pairs[f'BOARD_INFO_KNOWN_{group}'] = bit
        for group, bit in bi.UNREADY.items():
            pairs[f'BOARD_INFO_UNREADY_{group}'] = bit
        for code, name in bi.ERROR_NAMES.items():
            if code:
                pairs[f'BOARD_INFO_{name}'] = code
        for key, expected in pairs.items():
            with self.subTest(key):
                self.assertEqual(c[key], expected)

    def test_error_strings_match(self):
        out = subprocess.run([str(native_harness()), 'strerror'], capture_output=True, text=True, timeout=60, check=True)
        seen = dict(line.split('\t') for line in out.stdout.splitlines())
        self.assertEqual(len(seen), max(bi.STRERROR) + 4)
        for code in range(-1, max(bi.STRERROR) + 3):
            self.assertEqual(seen[str(code)], bi.strerror(code))
        self.assertEqual(len(set(bi.STRERROR.values())), len(bi.STRERROR))
        self.assertEqual(bi.strerror(-1), 'unknown error code')


class Verdicts(Parity):
    def test_header_checks(self):
        self.expect(HEADER_CASES)

    def test_counts(self):
        self.expect(COUNT_CASES)

    def test_reserved_and_canonical_zero(self):
        self.expect(RESERVED_CASES)

    def test_values(self):
        self.expect(VALUE_CASES)

    def test_consistency(self):
        self.expect(CONSISTENCY_CASES)

    def test_windows_and_overlap(self):
        self.expect(WINDOW_CASES)

    def test_accepted_blobs(self):
        self.expect(ACCEPT_CASES)

    def test_every_bit_flip_after_the_header_breaks_the_crc(self):
        cases = [(f'byte {i}', patch(BASE, i, '<B', BASE[i] ^ (1 << (i % 8)), fix_crc=False), bi.ERR_CRC)
                 for i in range(20, len(BASE))]
        verdicts = native_run([(blob, 0) for _, blob, _ in cases])
        self.assertEqual({code for code, _ in verdicts}, {bi.ERR_CRC})
        self.assertEqual({bi.check(blob) for _, blob, _ in cases}, {bi.ERR_CRC})

    def test_every_truncation_is_refused(self):
        cuts = [BASE[:n] for n in range(len(BASE))]
        self.assertEqual({code for code, _ in native_run([(c, 0) for c in cuts])}, {bi.ERR_TRUNCATED})
        self.assertEqual({bi.check(c) for c in cuts}, {bi.ERR_TRUNCATED})

    def test_null_and_misaligned_pointers(self):
        records = [(None, BATCH_NULL_BLOB), (BASE, BATCH_NULL_OUT)] + [(BASE, shift) for shift in range(1, 8)]
        codes = [code for code, _ in native_run(records)]
        self.assertEqual(codes, [bi.ERR_NULL, bi.ERR_NULL] + [bi.ERR_ALIGN] * 7)
        self.assertEqual(bi.check(None), bi.ERR_NULL)
        self.assertEqual([bi.check(BASE, address=shift) for shift in range(1, 8)], [bi.ERR_ALIGN] * 7)
        self.assertEqual(bi.check(BASE, address=0x1000), bi.OK)

    def test_check_order_reports_the_first_failure(self):
        both_wrong = patch(patch(BASE, 8, '<I', 2), 0, '<B', 0, fix_crc=False)
        self.assertEqual(bi.check(both_wrong), bi.ERR_MAGIC)
        self.assertEqual(native_run([(both_wrong, 0)]), [(bi.ERR_MAGIC, None)])
        stale = patch(patch(BASE, 24, '<I', 1, fix_crc=False), 2000, '<B', 0xaa, fix_crc=False)
        self.assertEqual(bi.check(stale), bi.ERR_CRC)       # the CRC is checked before any flag or field
        self.assertEqual(native_run([(stale, 0)]), [(bi.ERR_CRC, None)])

    def test_validation_does_not_mutate_the_blob(self):
        data = bytearray(BASE)
        native_run([(bytes(data), 0)])
        bi.check(data)
        self.assertEqual(bytes(data), BASE)


class Fuzz(Parity):
    EDGE32 = (0, 1, 2, 3, 4, 8, 16, 31, 32, 33, 36, 0xffff, 0x10000, 0x7fffffff, 0x80000000, 0xffffffff)
    EDGE64 = (0, 1, 0xfff, 0x1000, 1 << 35, LIMIT - 0x1000, LIMIT, LIMIT + 0x1000, 1 << 63, 0xfffffffffffff000,
              0xffffffffffffffff)

    def mutate(self, rng, blob):
        buf = bytearray(blob)
        for _ in range(rng.randint(1, 4)):
            kind = rng.randrange(6)
            at = rng.randrange(len(buf))
            if kind == 0:
                buf[at] ^= 1 << rng.randrange(8)
            elif kind == 1:
                struct.pack_into('<I', buf, at & ~3, rng.choice(self.EDGE32))
            elif kind == 2:
                struct.pack_into('<Q', buf, at & ~7 if (at & ~7) + 8 <= len(buf) else 0, rng.choice(self.EDGE64))
            elif kind == 3:
                buf[at] = rng.choice((0, 1, 0x7f, 0x80, 0xff))
            elif kind == 4:
                a, b = (rng.randrange(len(buf) // 8) * 8 for _ in range(2))
                buf[a:a + 8], buf[b:b + 8] = buf[b:b + 8], buf[a:a + 8]
            else:
                a, b = rng.randrange(len(buf) - 40), rng.randrange(len(buf) - 40)
                buf[a:a + 40] = buf[b:b + 40]
        if rng.random() < 0.9 and len(buf) >= 24:
            refresh_crc(buf)
        if rng.random() < 0.04:
            del buf[rng.randrange(len(buf)):]
        return bytes(buf)

    def test_seeded_mutations_agree_and_stay_in_bounds(self):
        rng = random.Random(0x5a17)
        seeds = (BASE, A16, SP11, padded(BASE, bytes(64), minor=1))
        blobs = [self.mutate(rng, rng.choice(seeds)) for _ in range(4000)]
        verdicts = native_run([(blob, 0) for blob in blobs])
        seen = set()
        for index, (blob, (c_code, c_mask)) in enumerate(zip(blobs, verdicts)):
            py_code = bi.check(blob)
            self.assertEqual(label(c_code), label(py_code), f'mutation {index}')
            if py_code == bi.OK:
                self.assertEqual(c_mask, bi.unready(bi.decode(blob)), f'mutation {index}')
            seen.add(py_code)
        # A fuzz run that only ever fails the CRC would prove nothing about the later phases.
        self.assertGreaterEqual(len(seen), 9, sorted(seen))
        self.assertIn(bi.OK, seen)


class Readiness(Parity):
    def edits(self):
        def drop_ranges(d):
            d['ranges'] = None
            for device in ('gpu', 'gpu-gmu', 'display'):
                d['devices'][device].update(mmio=False, ranges=[])
        every_smmu = ['smmu.apps-smmu', 'smmu.pcie-smmu', 'smmu.adreno-smmu']
        return (
            ('soc.midr', chain(S('soc.midr', None), unknowns('soc.midr')), ('MIDR',)),
            ('soc.name', chain(S('soc.name', None), S('soc.family', None), S('soc.midr', None),
                               unknowns('soc.name', 'soc.family', 'soc.midr')), ('SOC', 'MIDR')),
            ('clusters.count', chain(S('clusters.count', None), unknowns('clusters.count')), ('COUNTS',)),
            ('clusters.cores_per_cluster', chain(S('clusters.cores_per_cluster', None),
                                                 unknowns('clusters.cores_per_cluster')), ('COUNTS',)),
            ('clusters.mpidr', chain(S('clusters.mpidr', None), S('clusters.mpidrs', None),
                                     unknowns('clusters.mpidr', 'clusters.mpidrs')), ('AFFINITY', 'MPIDRS')),
            ('clusters.mpidrs', chain(S('clusters.mpidrs', None), unknowns('clusters.mpidrs')), ('MPIDRS',)),
            ('gic.version', chain(S('gic.version', None), unknowns('gic.version')), ('GIC_VERSION',)),
            ('gic.gicd', chain(S('gic.gicd', None), unknowns('gic.gicd')), ('GICD',)),
            ('gic.gicr', chain(S('gic.gicr', None), unknowns('gic.gicr')), ('GICR',)),
            ('gic.its', chain(S('gic.its', None), unknowns('gic.its')), ('ITS',)),
            ('timer.frequency_hz', chain(S('timer.frequency_hz', None), unknowns('timer.frequency_hz')), ('TIMER_HZ',)),
            ('timer.ppi', chain(S('timer.ppi', None), unknowns('timer.ppi')), ('TIMER_PPI',)),
            ('timer.ppi.hyp', S('timer.ppi.hyp', None), ('TIMER_PPI',)),
            ('smmu', chain(S('smmu', None), unknowns('smmu', drop=every_smmu)), ('SMMU',)),
            ('pcie.ecam', chain(S('pcie.ecam', None), unknowns('pcie.ecam')), ('ECAM',)),
            ('limits.ipa_bits', chain(S('limits.ipa_bits', None), unknowns('limits.ipa_bits')), ('IPA',)),
            ('ranges', chain(drop_ranges, unknowns('ranges', drop=['ranges.dram0', 'ranges.gpu-regs', 'ranges.gpu-gmu',
                                                                    'ranges.mdss-top'])), ('RANGES',)),
            ('no dram window', chain(lambda d: d['ranges'].pop(0), dropprov('ranges.dram0')), ('RANGES',)),
            ('dram geometry unknown', chain(S('ranges.0.base', None), S('ranges.0.size', None),
                                            unknowns('ranges.dram0')), ('RANGES',)),
            ('second dram window of unknown geometry', chain(tbd.add_range('dram1', 'dram', None, None),
                                                             unknowns('ranges.dram1')), ('RANGES',)),
            ('device state unknown', chain(S('devices.nvme', {'state': 'unknown', 'mmio': None, 'ranges': []}),
                                           unknowns('devices.nvme')), ('DEVICES',)),
            ('board.status', S('board.status', 'UNIMPLEMENTED'), ('STATUS',)),
        )

    def test_unready_mask_equals_the_descriptor_gate(self):
        packed, expectations = [], []
        for name, edit, groups in self.edits():
            d = tbd.synthetic()
            edit(d)
            board = bd.validate(d)
            with self.assertRaises(bd.BoardNotBootReady, msg=name):
                bd.require_boot_ready(board)
            packed.append(bi.pack(board))
            expectations.append((name, sum(bi.UNREADY[g] for g in groups)))
        for (name, expected), blob, (c_code, c_mask) in zip(expectations, packed, native_run([(b, 0) for b in packed])):
            with self.subTest(name):
                self.assertEqual(c_code, bi.OK)
                self.assertEqual(c_mask, expected)
                self.assertEqual(bi.unready(bi.decode(blob)), expected)

    def test_boot_ready_descriptor_has_an_empty_mask(self):
        self.assertIs(bd.require_boot_ready(BASE_BOARD), BASE_BOARD)
        self.assertEqual(native_run([(BASE, 0)]), [(bi.OK, 0)])
        self.assertEqual(bi.unready(F0), 0)

    def test_shipped_descriptors_are_not_boot_ready(self):
        for name, blob, expected in (
                ('a16', A16, ('COUNTS', 'MPIDRS', 'ITS', 'SMMU', 'TIMER_PPI', 'ECAM', 'RANGES', 'DEVICES')),
                ('sp11', SP11, ('STATUS', 'MIDR', 'GIC_VERSION', 'TIMER_HZ', 'ECAM', 'RANGES', 'DEVICES'))):
            with self.subTest(name):
                mask = sum(bi.UNREADY[g] for g in expected)
                self.assertEqual(native_run([(blob, 0)]), [(bi.OK, mask)])
                self.assertEqual(bi.names_of(bi.unready(bi.decode(blob)), bi.UNREADY), list(expected))
                with self.assertRaises(bd.BoardNotBootReady):
                    bd.require_boot_ready(bd.load(ROOT / 'config' / 'boards' / f'{name}.json'))


class Python(unittest.TestCase):
    """Packer, codec and inspection tests that need no compiler."""

    def test_round_trip_and_deterministic_packing(self):
        for name, blob in (('synthetic', BASE), ('a16', A16), ('sp11', SP11)):
            with self.subTest(name):
                self.assertEqual(len(blob), bi.V1_SIZE)
                self.assertEqual(bi.encode(bi.decode(blob)), blob)
                board = tbd.board() if name == 'synthetic' else bd.load(ROOT / 'config' / 'boards' / f'{name}.json')
                self.assertEqual(bi.pack(board), blob)

    def test_header_is_independently_correct(self):
        magic, major, minor, total, crc, req, opt = struct.unpack_from('<8s6I', BASE)
        self.assertEqual((magic, major, minor, total, req, opt), (b'SNAPBRDI', 1, 0, 2168, 0, 0))
        zeroed = bytearray(BASE)
        zeroed[20:24] = bytes(4)
        self.assertEqual(crc, zlib.crc32(bytes(zeroed)) & 0xffffffff)

    def test_hand_built_minimal_blob(self):
        buf = bytearray(2168)
        buf[:8] = b'SNAPBRDI'
        struct.pack_into('<4I', buf, 8, 1, 0, 2168, 0)
        struct.pack_into('<I', buf, 32, 4)          # status UNIMPLEMENTED
        buf[40:44] = b'min\0'
        refresh_crc(buf)
        self.assertEqual(bi.check(bytes(buf)), bi.OK)
        self.assertEqual(bi.unready(bi.decode(bytes(buf))), 0x1ffff)   # nothing known: every group unready
        self.assertEqual(bi.describe(bi.decode(bytes(buf)))['known'], [])

    def test_window_set_matches_the_descriptor(self):
        for name, blob, path in (('synthetic', BASE, None), ('a16', A16, 'a16'), ('sp11', SP11, 'sp11')):
            with self.subTest(name):
                board = tbd.board() if path is None else bd.load(ROOT / 'config' / 'boards' / f'{path}.json')
                slots = bi._windows(bi.decode(blob))
                self.assertEqual(len(slots), bi.WINDOW_SLOTS)
                self.assertEqual({w for w in slots if w}, {(w.base, w.size) for w in bd.windows(board)})

    def test_packing_is_lossy_only_toward_unknown(self):
        board = BASE_BOARD

        def packed(**changes):
            return bi.decode(bi.pack(dataclasses.replace(board, **changes)))
        f = packed(soc_family=None)
        self.assertFalse(f['known'] & bi.KNOWN['SOC'])
        self.assertFalse(f['known'] & bi.KNOWN['MIDR'], 'a MIDR cannot be known without its SoC')
        self.assertEqual((f['soc_id'], f['midr_count']), (0, 0))
        f = packed(affinity_mask=None)
        self.assertFalse(f['known'] & (bi.KNOWN['AFFINITY'] | bi.KNOWN['MPIDRS']))
        f = packed(its=(False, 0, 0))
        self.assertTrue(f['known'] & bi.KNOWN['ITS'] and f['its_present'] == 0)
        f = packed(timer_ppi=(29, 30, None, 26))
        self.assertEqual(f['timer_ppi'], [29, 30, 0, 26])
        self.assertTrue(bi.unready(f) & bi.UNREADY['TIMER_PPI'])
        devices = dict(board.devices)
        devices['wifi'] = ('present', None, ())
        f = packed(devices=devices)
        self.assertFalse(f['device_mmio'] >> bd.DEVICE_VOCABULARY.index('wifi') & 1, 'only a proven mmio claim is sent')

    def test_pack_refuses_what_it_cannot_express(self):
        with self.assertRaises(bi.BoardInfoError) as caught:
            bi.pack(dataclasses.replace(BASE_BOARD, board_id='Not-Valid'))
        self.assertEqual(caught.exception.code, bi.ERR_VALUE)
        self.assertIn('does not fit', str(caught.exception))       # refused while packing, not by the final check
        with mock.patch.object(bi, 'check', return_value=bi.ERR_CRC):
            with self.assertRaisesRegex(bi.BoardInfoError, 'refuses'):
                bi.pack(BASE_BOARD)

    def test_decode_refuses_short_input(self):
        with self.assertRaises(bi.BoardInfoError) as caught:
            bi.decode(BASE[:bi.V1_SIZE - 1])
        self.assertEqual(caught.exception.code, bi.ERR_TRUNCATED)

    def test_validate_raises_with_the_shared_code(self):
        for _, blob, code in HEADER_CASES[:4] + COUNT_CASES[:1]:
            with self.assertRaises(bi.BoardInfoError) as caught:
                bi.validate(blob)
            self.assertEqual(caught.exception.code, code)
            self.assertIn(bi.strerror(code), str(caught.exception))
        self.assertEqual(bi.validate(BASE), F0)

    def test_blob_carries_no_provenance_or_free_text(self):
        for blob in (BASE, A16, SP11):
            for needle in (b'UNKNOWN', b'EXPERIMENTAL', b'source', HOME_PREFIXES[0].encode(), HOME_PREFIXES[1].encode()):
                self.assertNotIn(needle, blob)

    def test_describe_is_json_and_names_the_gaps(self):
        view = bi.describe(bi.decode(SP11))
        json.dumps(view)
        self.assertEqual(view['status'], 'UNIMPLEMENTED')
        self.assertEqual(view['unready'], ['STATUS', 'MIDR', 'GIC_VERSION', 'TIMER_HZ', 'ECAM', 'RANGES', 'DEVICES'])
        self.assertNotIn('midr', view)
        self.assertEqual(view['gic']['gicr']['stride'], '0x40000')
        self.assertEqual(bi.describe(bi.decode(BASE))['unready'], [])
        self.assertNotIn('version', view['gic'])
        self.assertEqual(view['timer'], {'ppi': {'secure_phys': 29, 'phys': 30, 'virt': 27, 'hyp': 26}})
        self.assertEqual(view['ranges'][0], {'name': 'dram0', 'kind': 'dram', 'base': None, 'size': None, 'device': None})
        self.assertEqual([view['devices'][d] for d in ('gpu', 'wifi', 'nvme')], ['present (own ranges)', 'present', 'unknown'])

    def test_describe_reports_every_known_group(self):
        mpidrs = [f'{(cluster << 16) | (core << 8):#x}' for cluster in range(3) for core in range(4)]
        self.assertEqual(bi.describe(F0), {
            'abi': '1.0', 'board_id': 'synthetic', 'status': 'EXPERIMENTAL', 'known': list(bi.KNOWN), 'unready': [],
            'soc': {'name': 'X1E80100', 'family': 'hamoa'}, 'midr': [{'value': '0x510f1230', 'mask': '0xff0ffff0'}],
            'clusters': {'count': 3, 'cores_per_cluster': 4},
            'mpidr_layout': {'affinity_mask': '0xff00ffffff', 'cluster_mask': '0xff0000'}, 'mpidrs': mpidrs,
            'gic': {'version': 4, 'gicd': {'base': '0x17000000', 'size': '0x10000'},
                    'gicr': {'base': '0x17080000', 'stride': '0x40000', 'size': '0x300000'},
                    'its': {'present': True, 'base': '0x17040000', 'size': '0x40000'}},
            'smmu': [{'name': 'apps-smmu', 'kind': 'mmu-500', 'base': '0x15000000', 'size': '0x100000'},
                     {'name': 'pcie-smmu', 'kind': 'smmu-v3', 'base': '0x15400000', 'size': '0x80000'},
                     {'name': 'adreno-smmu', 'kind': 'mmu-500', 'base': '0x3da0000', 'size': '0x40000'}],
            'timer': {'frequency_hz': 19200000, 'ppi': {'secure_phys': 29, 'phys': 30, 'virt': 27, 'hyp': 26}},
            'ecam': [{'segment': 0, 'base': '0x40000000', 'start_bus': 0, 'end_bus': 255}],
            'ranges': [{'name': 'dram0', 'kind': 'dram', 'base': '0x80000000', 'size': '0x100000000', 'device': None},
                       {'name': 'gpu-regs', 'kind': 'mmio', 'base': '0x3d00000', 'size': '0x40000', 'device': 'gpu'},
                       {'name': 'gpu-gmu', 'kind': 'mmio', 'base': '0x3d6a000', 'size': '0x35000', 'device': 'gpu-gmu'},
                       {'name': 'mdss-top', 'kind': 'mmio', 'base': '0xae00000', 'size': '0x1000', 'device': 'display'}],
            'devices': {'gpu': 'present (own ranges)', 'gpu-gmu': 'present (own ranges)', 'display': 'present (own ranges)',
                        'uart-debug': 'absent', 'usb-host': 'absent', 'keyboard': 'present', 'touchpad': 'present',
                        'touchscreen': 'absent', 'wifi': 'present', 'nvme': 'present'},
            'ipa_bits': 36})


class Cli(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = BUILD / 'cli'
        shutil.rmtree(cls.dir, ignore_errors=True)
        cls.dir.mkdir(parents=True)

    def run_cli(self, *args):
        proc = subprocess.run([sys.executable, str(ROOT / 'probes' / 'boardinfo.py'), *args], capture_output=True, text=True,
                              timeout=120)
        return proc.returncode, proc.stdout, proc.stderr

    def test_pack_unpack_check(self):
        out = self.dir / 'sp11.bin'
        rc, stdout, _ = self.run_cli('pack', str(ROOT / 'config' / 'boards' / 'sp11.json'), '-o', str(out))
        self.assertEqual(rc, 0)
        self.assertIn('2168 bytes', stdout)
        self.assertEqual(out.read_bytes(), SP11)
        rc, stdout, _ = self.run_cli('unpack', str(out))
        self.assertEqual((rc, json.loads(stdout)['board_id']), (0, 'sp11'))
        rc, stdout, _ = self.run_cli('check', str(out))
        self.assertEqual(rc, 0)
        self.assertIn('VALID', stdout)
        rc, _, stderr = self.run_cli('check', str(out), '--boot-ready')
        self.assertEqual(rc, 1)
        self.assertIn('not boot-ready', stderr)

    def test_boot_ready_blob_passes_the_gate(self):
        out = self.dir / 'synthetic.bin'
        out.write_bytes(BASE)
        rc, stdout, _ = self.run_cli('check', str(out), '--boot-ready')
        self.assertEqual(rc, 0)
        self.assertIn('unready: none', stdout)

    def test_existing_output_is_kept_unless_forced(self):
        out = self.dir / 'keep.bin'
        out.write_bytes(b'precious')
        args = ('pack', str(ROOT / 'config' / 'boards' / 'a16.json'), '-o', str(out))
        rc, _, stderr = self.run_cli(*args)
        self.assertEqual((rc, out.read_bytes()), (2, b'precious'))
        self.assertIn('--force', stderr)
        rc, _, _ = self.run_cli(*args, '--force')
        self.assertEqual((rc, out.read_bytes()), (0, A16))

    def test_refusals_exit_nonzero_and_write_nothing(self):
        bad = self.dir / 'bad.json'
        bad.write_text('{"schema_version": 99}', encoding='utf-8')
        out = self.dir / 'never.bin'
        rc, _, stderr = self.run_cli('pack', str(bad), '-o', str(out))
        self.assertEqual(rc, 1)
        self.assertIn('REFUSED', stderr)
        self.assertFalse(out.exists())
        corrupt = self.dir / 'corrupt.bin'
        corrupt.write_bytes(patch(BASE, 2000, '<B', BASE[2000] ^ 1, fix_crc=False))
        rc, _, stderr = self.run_cli('check', str(corrupt))
        self.assertEqual(rc, 1)
        self.assertIn('ERR_CRC', stderr)
        rc, _, stderr = self.run_cli('check', str(self.dir / 'missing.bin'))
        self.assertEqual(rc, 1)
        self.assertIn('REFUSED', stderr)
        self.assertEqual(self.run_cli()[0], 2)


class Freestanding(unittest.TestCase):
    def test_monitor_target_object_has_no_undefined_or_writable_symbols(self):
        clang, nm = find_tool('clang'), find_tool('llvm-nm')
        if not (clang and nm):
            self.skipTest('clang or llvm-nm not found')
        flags = [f for f in json.loads((ROOT / 'config' / 'monitor-flags.json').read_text())['flags'] if f != '-flto']
        BUILD.mkdir(parents=True, exist_ok=True)
        obj = BUILD / 'board-info-aarch64.o'
        built = subprocess.run([clang, *flags, f'-I{ROOT / "compat"}', '-c', str(ROOT / 'compat' / 'board-info.c'),
                                '-o', str(obj)], capture_output=True, text=True, timeout=300)
        if built.returncode and 'unable to find target' in built.stderr:
            self.skipTest('clang cannot target aarch64-unknown-none-elf')
        self.assertEqual(built.returncode, 0, built.stderr)
        symbols = subprocess.run([nm, '--format=posix', str(obj)], capture_output=True, text=True, check=True).stdout
        kinds = {line.split()[1] for line in symbols.splitlines() if line.strip()}
        self.assertNotIn('U', kinds, symbols)       # no libc: memcpy/memset must not be needed
        self.assertFalse(kinds & set('DdBbCc'), symbols)    # no mutable state shared between EL2 entries
        defined = {line.split()[0] for line in symbols.splitlines() if line.split()[1] in 'Tt'}
        self.assertTrue({'board_info_validate', 'board_info_unready', 'board_info_strerror'} <= defined, defined)


class Hygiene(unittest.TestCase):
    def test_new_files_follow_the_repository_rules(self):
        for relpath in A4_FILES:
            with self.subTest(relpath):
                path = ROOT / relpath
                text = path.read_text(encoding='utf-8')
                self.assertIn('SPDX-License-Identifier: MIT', '\n'.join(text.splitlines()[:3]))
                self.assertLess(len(text.splitlines()), 1500)
                self.assertLess(len(text.encode()), 50 * 1024)
                for prefix in HOME_PREFIXES:
                    self.assertNotIn(prefix, text)

    def test_compat_unit_is_not_wrapped_in_the_platform_gate(self):
        # The A16 build must not contain this code whatever the macros say; it is linked only through the OPTIONAL tuple.
        for relpath in ('compat/board-info.c', 'compat/board-info.h'):
            source = (ROOT / relpath).read_text(encoding='utf-8')
            self.assertIsNone(re.search(r'^\s*#\s*(if|ifdef|ifndef|elif)\b.*SNAP_PLATFORM', source, re.M), relpath)


if __name__ == '__main__':
    unittest.main()
