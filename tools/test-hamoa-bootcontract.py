#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests for the Hamoa (Snapdragon X Elite) boot-contract adapter: probes/bootcontract.py and tools/check-hamoa-acpi.py.

Every ACPI table here is synthetic and assembled by this file from the public ACPI and PCI Firmware layouts. No
firmware dump or vendor data is involved and nothing touches hardware. The positive cases use a boot-ready synthetic
board (tools/test-board-descriptor.py builds it from the shipped SP11 template with every unknown filled in); the
shipped config/boards/sp11.json is itself a template and must be refused.

    python3 tools/test-hamoa-bootcontract.py

Evidence: EXPERIMENTAL. Agreement with synthetic tables says nothing about what any firmware really emits.
"""
import contextlib
import dataclasses
import importlib.util
import io
import json
import os
from pathlib import Path
import random
import re
import shutil
import signal
import struct
import subprocess
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'probes'))
sys.path.insert(0, str(ROOT / 'tools'))
import board_descriptor as bd
import bootcontract as bc

BUILD = ROOT / 'build' / 'test-hamoa-bootcontract'
HOME_PREFIXES = ('/' + 'Users' + '/', '/' + 'home' + '/')
OWNED_FILES = ('probes/bootcontract.py', 'tools/check-hamoa-acpi.py', 'tools/test-hamoa-bootcontract.py')


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tbd = _load('board_descriptor_fixtures', 'test-board-descriptor.py')
cli = _load('check_hamoa_acpi', 'check-hamoa-acpi.py')
S, chain = tbd.S, tbd.chain


@contextlib.contextmanager
def deadline(seconds=20):
    """Turns an unbounded walk into a failure instead of a hung test run."""
    def expired(signum, frame):
        raise AssertionError(f'no result within {seconds} s: an unbounded walk?')
    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


# --------------------------------------------------------------------------------------------------------------
# Synthetic ACPI tables. Field order follows the ACPI 6.5 tables the code under test reads; OEM fields are fake.
# --------------------------------------------------------------------------------------------------------------
HEADER = struct.Struct('<4sIBB6s8sI4sI')
RSDP_LAYOUT = struct.Struct('<8sB6sBIIQB3s')
GICC_FORMATS = {76: '<BBHIIIIIQQQQIQQ', 80: '<BBHIIIIIQQQQIQQBBH', 82: '<BBHIIIIIQQQQIQQBBHH'}
assert HEADER.size == 36 and RSDP_LAYOUT.size == 36
assert all(struct.calcsize(fmt) == size for size, fmt in GICC_FORMATS.items())


def fix_sdt(raw):
    raw = bytearray(raw)
    if len(raw) > 9:
        raw[9] = 0
        raw[9] = -sum(raw) & 0xff
    return bytes(raw)


def fix_rsdp(raw):
    raw = bytearray(raw)
    if len(raw) > 8:
        raw[8] = 0
        raw[8] = -sum(raw[:20]) & 0xff
    if len(raw) >= 36:
        raw[32] = 0
        raw[32] = -sum(raw) & 0xff
    return bytes(raw)


def sdt(signature, body, *, length=None):
    total = HEADER.size + len(body) if length is None else length
    return fix_sdt(HEADER.pack(signature, total, 1, 0, b'SYNTH ', b'SYNTHTBL', 1, b'SYNT', 1) + body)


def rsdp(xsdt_address=0x1000, *, revision=2, signature=b'RSD PTR ', length=36):
    return fix_rsdp(RSDP_LAYOUT.pack(signature, 0, b'SYNTH ', revision, 0, length, xsdt_address, 0, b'\0\0\0'))


def xsdt(addresses=(0x2000, 0x3000, 0x4000, 0x5000)):
    return sdt(b'XSDT', struct.pack(f'<{len(addresses)}Q', *addresses))


def gicc(uid, mpidr, *, enabled=True, gicr=0, length=80):
    fields = [0xB, length, 0, uid, uid, int(enabled), 0, 0, 0, 0, 0, 0, 0, gicr, mpidr]
    return struct.pack(GICC_FORMATS[length], *fields, *[0] * {76: 0, 80: 3, 82: 4}[length])


def gicd(base, version=4):
    return struct.pack('<BBHIQIB3s', 0xC, 24, 0, 0, base, 0, version, b'\0\0\0')


def gicr(base, length):
    return struct.pack('<BBHQI', 0xE, 16, 0, base, length)


def its(base, its_id=0):
    return struct.pack('<BBHIQI', 0xF, 20, 0, its_id, base, 0)


def resized(entry, size):
    """The entry with its length byte (and body) changed to `size`: the same bytes, wrongly sized."""
    raw = bytearray(entry)
    raw[1] = size
    return bytes(raw[:size]).ljust(size, b'\0')


def madt(entries):
    return sdt(b'APIC', struct.pack('<II', 0, 0) + b''.join(entries))


def mcfg(allocations):
    body = bytes(8)
    for segment, base, start, end in allocations:
        body += struct.pack('<QHBBI', base, segment, start, end, 0)
    return sdt(b'MCFG', body)


BOARD = tbd.board()
MPIDRS = tuple(BOARD.mpidrs)
GICD_BASE = BOARD.gicd[0]
GICR_BASE, STRIDE, GICR_SIZE = BOARD.gicr
ITS_BASE = BOARD.its[1]
ECAM = tuple(BOARD.ecam[0])
I_GICD, I_CPU0, I_GICR, I_ITS = 0, 1, 1 + len(MPIDRS), 2 + len(MPIDRS)


def baseline_entries(*, mpidrs=MPIDRS, gicd_version=4, gicr_ranges=((GICR_BASE, GICR_SIZE),), its_bases=(ITS_BASE,),
                     cpu_length=80, per_cpu_gicr=False):
    entries = [gicd(GICD_BASE, gicd_version)]
    for uid, mpidr in enumerate(mpidrs):
        entries.append(gicc(uid, mpidr, length=cpu_length, gicr=GICR_BASE + uid * STRIDE if per_cpu_gicr else 0))
    entries += [gicr(base, length) for base, length in gicr_ranges]
    entries += [its(base) for base in its_bases]
    return entries


def swap(entries, index, entry):
    out = list(entries)
    out[index] = entry
    return out


def without(entries, *indexes):
    return [entry for i, entry in enumerate(entries) if i not in indexes]


def good(entries=None, ecam=(ECAM,), **tables):
    out = {'RSDP': rsdp(), 'XSDT': xsdt(), 'APIC': madt(baseline_entries() if entries is None else entries),
           'MCFG': mcfg(ecam)}
    out.update(tables)
    return out


def variant(*edits):
    description = tbd.synthetic()
    chain(*edits)(description)
    return bd.validate(description)


def host_info(**changes):
    info = dict(current_el=2, timer_hz=19200000, heap_base=0x80000000, heap_size=0x4000000, fb_base=0xf9a20000,
                fb_size=2880 * 1800 * 4, fb_width=2880, fb_height=1800, fb_stride=2880, fb_format=1)
    info.update(changes)
    return info


BASELINE = bc.check_hamoa_platform(BOARD, good())


class Base(unittest.TestCase):
    def refused(self, code, tables=None, board=BOARD, reason=None):
        with deadline():
            with self.assertRaises(bc.BootContractError) as caught:
                bc.check_hamoa_platform(board, good() if tables is None else tables)
        self.assertEqual(caught.exception.code, code, str(caught.exception))
        if reason is not None:
            self.assertIn(reason, caught.exception.reason)
        return caught.exception

    def accepted(self, tables=None, board=BOARD):
        with deadline():
            return bc.check_hamoa_platform(board, good() if tables is None else tables)


class Fixture(unittest.TestCase):
    def test_synthetic_board_has_the_shape_the_tables_assume(self):
        self.assertEqual((len(MPIDRS), STRIDE, GICR_SIZE, BOARD.gic_version), (12, 0x40000, 12 * 0x40000, 4))
        self.assertEqual((BOARD.cluster_count, BOARD.cores_per_cluster), (3, 4))
        self.assertEqual(len(ECAM), 4)
        self.assertEqual(len(good()['APIC']), 44 + 24 + 12 * 80 + 16 + 20)

    def test_the_shipped_templates_are_not_boot_ready(self):
        for name in ('sp11', 'a16'):
            with self.subTest(name), self.assertRaises(bd.BoardNotBootReady):
                bd.require_boot_ready(bd.load(ROOT / 'config' / 'boards' / f'{name}.json'))

    def test_error_codes_are_a_unique_registry(self):
        codes = bc.BOOT_CONTRACT_CODES
        self.assertEqual(len(set(codes)), len(codes))
        for code in codes:
            self.assertRegex(code, r'\A[a-z]+(-[a-z0-9]+)*\Z')
        with self.assertRaises(AssertionError):
            bc.BootContractError('not-a-registered-code', 'x')
        self.assertTrue(issubclass(bc.BootContractError, ValueError))
        self.assertEqual(str(bc.BootContractError('table-length', 'why')), 'table-length: why')


class Acceptance(Base):
    def test_baseline_is_accepted_with_the_descriptor_facts(self):
        platform = self.accepted()
        self.assertEqual((platform.gic_version, platform.gicd_base, platform.gicr_base, platform.gicr_stride,
                          platform.gicr_size, platform.its_base), (4, GICD_BASE, GICR_BASE, STRIDE, GICR_SIZE, ITS_BASE))
        self.assertEqual(platform.mpidrs, tuple(sorted(MPIDRS)))
        self.assertEqual(platform.ecam, (ECAM,))
        self.assertEqual(platform.notes, ())

    def test_parse_reports_what_the_tables_state_without_a_descriptor(self):
        facts = bc.parse_hamoa_tables(good())
        self.assertEqual(facts.xsdt_entries, 4)
        self.assertEqual(facts.gicd, (bc.Gicd(GICD_BASE, 4),))
        self.assertEqual([cpu.mpidr for cpu in facts.gicc], list(MPIDRS))
        self.assertTrue(all(cpu.enabled for cpu in facts.gicc))
        self.assertEqual(facts.gicr, (bc.Gicr(GICR_BASE, GICR_SIZE),))
        self.assertEqual(facts.its, (bc.Its(0, ITS_BASE),))
        self.assertEqual(facts.ecam, (bc.EcamAllocation(*ECAM),))
        lines = '\n'.join(bc.describe_acpi_facts(facts))
        for text in (f'{GICD_BASE:#x}', 'version 4', '12 enabled of 12', 'buses 0..255'):
            self.assertIn(text, lines)

    def test_gicd_version_zero_is_accepted_but_not_taken_as_confirmation(self):
        platform = self.accepted(good(baseline_entries(gicd_version=0)))
        self.assertEqual(platform.gic_version, 4)
        self.assertTrue(any('not confirmed' in note for note in platform.notes), platform.notes)

    def test_reserved_and_multithread_mpidr_bits_are_ignored(self):
        mpidrs = [m | 1 << 31 | 1 << 30 | 1 << 24 for m in MPIDRS]
        self.assertEqual(self.accepted(good(baseline_entries(mpidrs=mpidrs))).mpidrs, tuple(sorted(MPIDRS)))

    def test_disabled_entries_are_not_interpreted(self):
        # Firmware may leave a disabled entry zeroed: its UID and MPIDR then collide with CPU 0's.
        entries = baseline_entries() + [gicc(0, 0, enabled=False), gicc(99, 1 << 45, enabled=False)]
        self.assertEqual(self.accepted(good(entries)), BASELINE)

    def test_redistributors_may_be_several_contiguous_ranges_in_any_order(self):
        third = GICR_SIZE // 3
        ranges = ((GICR_BASE + 2 * third, third), (GICR_BASE, third), (GICR_BASE + third, third))
        self.assertEqual(self.accepted(good(baseline_entries(gicr_ranges=ranges))), BASELINE)

    def test_per_cpu_redistributor_bases_inside_the_range_are_accepted(self):
        self.assertEqual(self.accepted(good(baseline_entries(per_cpu_gicr=True))), BASELINE)

    def test_gicc_length_variants(self):
        for length in (76, 80, 82):
            with self.subTest(length):
                self.assertEqual(self.accepted(good(baseline_entries(cpu_length=length))), BASELINE)

    def test_board_without_an_its(self):
        board = variant(S('gic.its', {'present': False}))
        platform = self.accepted(good(baseline_entries(its_bases=())), board)
        self.assertIsNone(platform.its_base)

    def test_gicv3_board(self):
        board = variant(S('gic.version', 3), S('gic.gicr.stride', '0x20000'), S('gic.gicr.size', '0x180000'))
        for version in (0, 3):
            with self.subTest(version):
                entries = baseline_entries(gicd_version=version, gicr_ranges=((GICR_BASE, 12 * 0x20000),))
                platform = self.accepted(good(entries), board)
                self.assertEqual((platform.gic_version, platform.gicr_stride, platform.gicr_size),
                                 (3, 0x20000, 0x180000))

    def test_two_pci_segments_in_any_order(self):
        second = (1, 0x50000000, 0, 127)
        board = variant(S('pcie.ecam', [dict(segment=s, base=hex(b), start_bus=lo, end_bus=hi)
                                        for s, b, lo, hi in (ECAM, second)]))
        for allocations in ((ECAM, second), (second, ECAM)):
            with self.subTest(allocations[0][0]):
                self.assertEqual(self.accepted(good(ecam=allocations), board).ecam, tuple(sorted((ECAM, second))))

    def test_unknown_madt_types_with_a_sane_length_are_skipped_with_a_note(self):
        extra = [struct.pack('<BBHIQQ', 0xD, 24, 0, 0, 0x17090000, 0)[:24], bytes([0x80, 6, 1, 2, 3, 4])]
        platform = self.accepted(good(baseline_entries() + extra))
        self.assertEqual(platform.gicr_size, GICR_SIZE)
        self.assertTrue(any('0x0d x1' in note and '0x80 x1' in note for note in platform.notes), platform.notes)

    def test_other_keys_and_bytearrays_are_accepted(self):
        tables = good()
        tables['FACP'] = b'not parsed'
        tables['APIC'] = bytearray(tables['APIC'])
        self.assertEqual(self.accepted(tables), BASELINE)

    def test_the_check_does_not_modify_its_inputs(self):
        tables = good()
        tables['XSDT'] = bytearray(tables['XSDT'])
        before = {key: bytes(value) for key, value in tables.items()}
        first = bc.check_hamoa_platform(BOARD, tables)
        self.assertEqual({key: bytes(value) for key, value in tables.items()}, before)
        self.assertEqual(bc.check_hamoa_platform(BOARD, tables), first)


REQUIRED_FIELDS = (('clusters.count', 'cluster_count'), ('clusters.cores_per_cluster', 'cores_per_cluster'),
                   ('clusters.mpidr', 'affinity_mask'), ('clusters.mpidrs', 'mpidrs'), ('gic.version', 'gic_version'),
                   ('gic.gicd', 'gicd'), ('gic.gicr', 'gicr'), ('gic.its', 'its'), ('pcie.ecam', 'ecam'))


class DescriptorRefusals(Base):
    def test_a_template_is_refused_before_any_table_is_read(self):
        board = variant(S('board.status', 'UNIMPLEMENTED'))
        self.refused('descriptor-unimplemented', board=board)
        self.refused('descriptor-unimplemented', tables={}, board=board)

    def test_the_shipped_sp11_descriptor_is_a_template(self):
        shipped = bd.load(ROOT / 'config' / 'boards' / 'sp11.json')
        self.refused('descriptor-unimplemented', board=shipped)

    def test_the_shipped_a16_descriptor_is_not_a_hamoa_profile(self):
        exc = self.refused('descriptor-unknown', board=bd.load(ROOT / 'config' / 'boards' / 'a16.json'))
        self.assertIn('gic.its', exc.reason)

    def test_each_required_field_must_be_known(self):
        for path, attr in REQUIRED_FIELDS:
            with self.subTest(path):
                exc = self.refused('descriptor-unknown', board=dataclasses.replace(BOARD, **{attr: None}))
                self.assertIn(path, exc.reason)

    def test_every_unknown_field_is_named(self):
        exc = self.refused('descriptor-unknown', board=dataclasses.replace(BOARD, gic_version=None, ecam=None))
        self.assertIn('gic.version', exc.reason)
        self.assertIn('pcie.ecam', exc.reason)

    def test_gic_version_must_be_3_or_4(self):
        for version in (2, 5, 0, '4'):
            with self.subTest(version):
                self.refused('descriptor-gic-version', board=dataclasses.replace(BOARD, gic_version=version))

    def test_the_redistributor_stride_must_be_the_architectural_one(self):
        for label, version, stride in (('padded', 4, 0x60000), ('v4 with the v3 stride', 4, 0x20000),
                                       ('v3 with the v4 stride', 3, 0x40000)):
            with self.subTest(label):
                board = dataclasses.replace(BOARD, gic_version=version, gicr=(GICR_BASE, stride, GICR_SIZE))
                self.refused('descriptor-inconsistent', board=board)

    def test_the_redistributor_size_must_cover_whole_frames_for_every_cpu(self):
        # The two "not a multiple" sizes still hold at least one frame per CPU after rounding down, so only the
        # whole-frame check can refuse them; without it they would surface later as a mismatch with the MADT.
        for label, size, reason in (('not a multiple, too few frames', 0x2f0000, None),
                                    ('not a multiple, rounds down to enough frames', GICR_SIZE + STRIDE // 4,
                                     'nonzero multiple'),
                                    ('not a multiple, one byte over', GICR_SIZE + 1, 'nonzero multiple'),
                                    ('zero', 0, 'nonzero multiple'), ('fewer frames than CPUs', 8 * STRIDE, 'frames')):
            with self.subTest(label):
                self.refused('descriptor-inconsistent', board=dataclasses.replace(BOARD, gicr=(GICR_BASE, STRIDE, size)),
                             reason=reason)

    def test_the_mpidr_list_must_match_the_topology(self):
        self.refused('descriptor-inconsistent', board=dataclasses.replace(BOARD, mpidrs=MPIDRS[:-1]))
        self.refused('descriptor-inconsistent', board=dataclasses.replace(BOARD, mpidrs=MPIDRS[:-1] + MPIDRS[:1]))

    def test_things_that_are_not_boards_are_refused(self):
        for label, board in (('dict', bd.validate(tbd.synthetic()).__dict__ if hasattr(BOARD, '__dict__') else {}),
                             ('None', None), ('namespace', types.SimpleNamespace(status='EXPERIMENTAL'))):
            with self.subTest(label):
                self.refused('descriptor-unknown', board=board)

    def test_hand_built_boards_in_the_wrong_shape_are_refused_not_crashed_on(self):
        for label, changes in (('gicd', dict(gicd=5)), ('its', dict(its=(True, 1))), ('ecam', dict(ecam=(5,))),
                               ('mpidrs', dict(mpidrs=5)), ('gicr', dict(gicr=(1, 2)))):
            with self.subTest(label):
                self.refused('descriptor-inconsistent', board=dataclasses.replace(BOARD, **changes))


def stale(entries, **kw):
    return good(entries, **kw)


def mismatch_cases():
    entries = baseline_entries()
    third = GICR_SIZE // 3
    cases = []

    def add(code, label, tables, **extra):
        cases.append((code, label, tables, extra.get('board', BOARD)))

    add('gicd-mismatch', 'base moved', good(swap(entries, I_GICD, gicd(GICD_BASE + 0x10000))))
    add('gicd-mismatch', 'no distributor', good(without(entries, I_GICD)))
    add('gicd-mismatch', 'two distributors', good(entries + [gicd(GICD_BASE)]))
    add('version-mismatch', 'GICv3 stated', good(baseline_entries(gicd_version=3)))
    add('version-mismatch', 'GICv5 stated', good(baseline_entries(gicd_version=5)))

    add('cpu-mismatch', 'one CPU fewer', good(without(entries, I_CPU0 + 11)))
    add('cpu-mismatch', 'one CPU more', good(entries[:I_GICR] + [gicc(12, 0x30000)] + entries[I_GICR:]))
    add('cpu-mismatch', 'one MPIDR replaced', good(swap(entries, I_CPU0 + 11, gicc(11, 0x30000))))
    add('cpu-mismatch', 'duplicate MPIDR', good(swap(entries, I_CPU0 + 11, gicc(11, MPIDRS[10]))))
    add('cpu-mismatch', 'duplicate UID', good(swap(entries, I_CPU0 + 11, gicc(0, MPIDRS[11]))))
    add('cpu-mismatch', 'MPIDR bit 40 set', good(swap(entries, I_CPU0 + 3, gicc(3, MPIDRS[3] | 1 << 40))))
    add('cpu-mismatch', 'one CPU disabled', good(swap(entries, I_CPU0, gicc(0, MPIDRS[0], enabled=False))))
    add('cpu-mismatch', 'no CPU entries', good(without(entries, *range(I_CPU0, I_GICR))))
    narrow = dataclasses.replace(BOARD, affinity_mask=0xffff00)
    add('cpu-mismatch', 'affinity bit outside the descriptor mask', good(swap(entries, I_CPU0 + 1, gicc(1, 0x101))),
        board=narrow)

    add('gicr-mismatch', 'base moved', good(swap(entries, I_GICR, gicr(GICR_BASE + STRIDE, GICR_SIZE))))
    add('gicr-mismatch', 'one frame short', good(swap(entries, I_GICR, gicr(GICR_BASE, GICR_SIZE - STRIDE))))
    add('gicr-mismatch', 'one frame long', good(swap(entries, I_GICR, gicr(GICR_BASE, GICR_SIZE + STRIDE))))
    add('gicr-mismatch', 'not a whole number of frames', good(swap(entries, I_GICR, gicr(GICR_BASE, GICR_SIZE - STRIDE // 2))))
    add('gicr-mismatch', 'zero length', good(swap(entries, I_GICR, gicr(GICR_BASE, 0))))
    add('gicr-mismatch', 'no redistributor structure', good(without(entries, I_GICR)))
    add('gicr-mismatch', 'a gap between ranges',
        good(baseline_entries(gicr_ranges=((GICR_BASE, third), (GICR_BASE + third + STRIDE, third + third)))))
    add('gicr-mismatch', 'per-CPU base outside the range',
        good(swap(entries, I_CPU0 + 4, gicc(4, MPIDRS[4], gicr=GICR_BASE + GICR_SIZE + STRIDE))))
    add('gicr-mismatch', 'per-CPU base one past the end',
        good(swap(entries, I_CPU0 + 4, gicc(4, MPIDRS[4], gicr=GICR_BASE + GICR_SIZE))))
    add('gicr-mismatch', 'per-CPU base between frames',
        good(swap(entries, I_CPU0 + 4, gicc(4, MPIDRS[4], gicr=GICR_BASE + STRIDE // 2))))
    add('gic-overlap', 'the range listed twice', good(baseline_entries(gicr_ranges=((GICR_BASE, GICR_SIZE),) * 2)))
    add('gic-overlap', 'overlapping ranges',
        good(baseline_entries(gicr_ranges=((GICR_BASE, 2 * third), (GICR_BASE + third, 2 * third)))))
    add('gic-overlap', 'two CPUs share a redistributor',
        good(swap(swap(entries, I_CPU0 + 1, gicc(1, MPIDRS[1], gicr=GICR_BASE + STRIDE)), I_CPU0 + 2,
                  gicc(2, MPIDRS[2], gicr=GICR_BASE + STRIDE))))

    add('its-mismatch', 'base moved', good(baseline_entries(its_bases=(ITS_BASE + 0x40000,))))
    add('its-mismatch', 'missing', good(without(entries, I_ITS)))
    add('its-mismatch', 'two ITS blocks', good(baseline_entries(its_bases=(ITS_BASE, ITS_BASE + 0x40000))))
    add('its-mismatch', 'listed for a board that has none', good(), board=variant(S('gic.its', {'present': False})))

    second = (1, 0x50000000, 0, 127)
    add('ecam-mismatch', 'base moved', good(ecam=((0, ECAM[1] + 0x100000, 0, 255),)))
    add('ecam-mismatch', 'end bus', good(ecam=((0, ECAM[1], 0, 127),)))
    add('ecam-mismatch', 'start bus', good(ecam=((0, ECAM[1], 1, 255),)))
    add('ecam-mismatch', 'segment', good(ecam=((1, ECAM[1], 0, 255),)))
    add('ecam-mismatch', 'an extra allocation', good(ecam=(ECAM, second)))
    two = variant(S('pcie.ecam', [dict(segment=s, base=hex(b), start_bus=lo, end_bus=hi) for s, b, lo, hi in (ECAM, second)]))
    add('ecam-mismatch', 'a missing allocation', good(ecam=(ECAM,)), board=two)
    return cases


class Mismatches(Base):
    def test_each_stale_value_is_refused_with_its_own_code(self):
        cases = mismatch_cases()
        self.assertGreater(len(cases), 35)
        for code, label, tables, board in cases:
            with self.subTest(f'{code}: {label}'):
                self.refused(code, tables, board)

    def test_cpu_identity_faults_are_named_not_just_refused(self):
        # A duplicate or a wrong count would also fail the final set comparison, so only the reason shows
        # that each check itself is in place.
        entries = baseline_entries()
        for label, tables, reason, board in (
                ('same affinity twice', good(swap(entries, I_CPU0 + 11, gicc(11, MPIDRS[10]))), 'same MPIDR affinity', BOARD),
                ('same UID twice', good(swap(entries, I_CPU0 + 11, gicc(0, MPIDRS[11]))), 'same ACPI processor UID', BOARD),
                ('one CPU fewer', good(without(entries, I_CPU0 + 11)), 'enables 11 CPU interfaces', BOARD),
                ('one CPU more', good(entries[:I_GICR] + [gicc(12, 0x30000)] + entries[I_GICR:]),
                 'enables 13 CPU interfaces', BOARD),
                ('bit 40', good(swap(entries, I_CPU0 + 3, gicc(3, MPIDRS[3] | 1 << 40))), 'reserved bits above bit 39', BOARD),
                ('outside the mask', good(swap(entries, I_CPU0 + 1, gicc(1, 0x101))), 'outside the descriptor mask',
                 dataclasses.replace(BOARD, affinity_mask=0xffff00))):
            with self.subTest(label):
                self.refused('cpu-mismatch', tables, board, reason=reason)

    def test_split_redistributor_ranges_must_each_hold_whole_frames(self):
        # Touching ranges that sum to the descriptor's size would pass every other GICR check; a frame cannot
        # straddle two discovery ranges, so each range has to be a whole number of frames by itself.
        first = 6 * STRIDE + STRIDE // 2
        pieces = ((GICR_BASE, first), (GICR_BASE + first, GICR_SIZE - first))
        self.assertTrue(all(length % STRIDE for _, length in pieces))
        self.assertEqual(sum(length for _, length in pieces), GICR_SIZE)
        self.refused('gicr-mismatch', good(baseline_entries(gicr_ranges=pieces)), reason='nonzero multiple')

    def test_every_mismatch_family_is_covered(self):
        covered = {code for code, _, _, _ in mismatch_cases()}
        self.assertEqual(covered, {'gicd-mismatch', 'version-mismatch', 'cpu-mismatch', 'gicr-mismatch', 'gic-overlap',
                                   'its-mismatch', 'ecam-mismatch'})


class TableRefusals(Base):
    def test_each_table_is_required(self):
        for key in good():
            with self.subTest(key):
                tables = good()
                del tables[key]
                self.refused('table-missing', tables)
                tables[key] = None
                self.refused('table-missing', tables)

    def test_tables_must_be_a_mapping_of_bytes(self):
        for not_a_mapping in (None, [], 'RSDP'):
            with self.subTest(repr(not_a_mapping)), self.assertRaises(bc.BootContractError) as caught:
                bc.check_hamoa_platform(BOARD, not_a_mapping)
            self.assertEqual(caught.exception.code, 'table-missing')
        for value in ('text', 7, memoryview(good()['XSDT']), [1, 2]):
            with self.subTest(type(value).__name__):
                self.refused('table-structure', good(XSDT=value))

    def test_every_truncation_is_refused_without_crashing(self):
        for key, table in good().items():
            for length in range(len(table)):
                with self.subTest(key=key, length=length):
                    with deadline():
                        with self.assertRaises(bc.BootContractError):
                            bc.check_hamoa_platform(BOARD, good(**{key: table[:length]}))

    def test_trailing_bytes_are_refused_rather_than_ignored(self):
        for key, table in good().items():
            with self.subTest(key):
                self.refused('table-length', good(**{key: table + b'\0'}))

    def test_length_fields_must_equal_the_bytes_supplied(self):
        def with_length(key, table, value):
            offset = 20 if key == 'RSDP' else 4
            raw = bytearray(table)
            raw[offset:offset + 4] = struct.pack('<I', value)
            return fix_rsdp(raw) if key == 'RSDP' else fix_sdt(raw)
        for key, table in good().items():
            for label, value in (('one too many', len(table) + 1), ('one too few', len(table) - 1),
                                 ('below the header', 35), ('zero', 0), ('over the cap', (1 << 20) + 1),
                                 ('far over the cap', 0xffffffff)):
                with self.subTest(key=key, case=label):
                    self.refused('table-length', good(**{key: with_length(key, table, value)}))

    def test_checksums_are_enforced(self):
        def flipped(key, offset):
            raw = bytearray(good()[key])
            raw[offset] ^= 0x01
            return good(**{key: bytes(raw)})
        for key, offset in (('XSDT', 40), ('APIC', 40), ('MCFG', 40), ('RSDP', 12), ('RSDP', 34)):
            with self.subTest(key=key, offset=offset):
                self.refused('table-checksum', flipped(key, offset))

    def test_each_rsdp_checksum_is_checked_on_its_own(self):
        # Any single flipped byte breaks both sums, so it cannot show that the ACPI 1.0 check is in place. A fault in
        # the first 20 bytes that is cancelled in the reserved bytes leaves the whole-structure sum at zero, which
        # only the dedicated check can see.
        raw = bytearray(rsdp())
        raw[10] = (raw[10] + 1) & 0xff
        raw[33] = (raw[33] - 1) & 0xff
        self.assertEqual(sum(raw) & 0xff, 0)
        self.refused('table-checksum', good(RSDP=bytes(raw)), reason='first 20 bytes')
        raw = bytearray(rsdp())
        raw[34] ^= 0x01
        self.assertEqual(sum(raw[:20]) & 0xff, 0)
        self.refused('table-checksum', good(RSDP=bytes(raw)), reason='extended checksum')

    def test_signatures_are_enforced(self):
        body = good()['XSDT'][36:]
        for key, table in (('XSDT', sdt(b'RSDT', body)), ('APIC', sdt(b'MADT', good()['APIC'][36:])),
                           ('MCFG', sdt(b'MCFX', good()['MCFG'][36:])), ('RSDP', rsdp(signature=b'RSD PTX '))):
            with self.subTest(key):
                self.refused('table-signature', good(**{key: table}))

    def test_rsdp_must_be_acpi_2_with_an_xsdt(self):
        for revision in (0, 1):
            with self.subTest(revision):
                self.refused('table-structure', good(RSDP=rsdp(revision=revision)))
        self.refused('table-structure', good(RSDP=fix_rsdp(rsdp(revision=0)[:20])))
        self.refused('table-structure', good(RSDP=rsdp(xsdt_address=0)))
        self.refused('table-length', good(RSDP=fix_rsdp(rsdp()[:20])))

    def test_xsdt_shapes(self):
        for label, table in (('odd size', sdt(b'XSDT', bytes(20))), ('empty', sdt(b'XSDT', b'')),
                             ('one entry', xsdt((0x2000,))), ('null entry', xsdt((0x2000, 0))),
                             ('duplicate entry', xsdt((0x2000, 0x3000, 0x2000)))):
            with self.subTest(label):
                self.refused('table-structure', good(XSDT=table))

    def test_madt_entries_of_length_zero_or_one_are_refused_as_such(self):
        for kind in (0xB, 0xC, 0xE, 0xF, 0x70):
            for size in (0, 1):
                with self.subTest(kind=kind, size=size):
                    exc = self.refused('table-structure', good(baseline_entries() + [bytes([kind, size]) + bytes(10)]))
                    self.assertIn(f'has length {size}', exc.reason)

    def test_madt_entries_must_fit_in_the_table(self):
        self.refused('table-structure', good(baseline_entries() + [bytes([0xB, 200]) + bytes(10)]))
        self.refused('table-structure', good(baseline_entries() + [bytes([0x70, 4, 0])]))
        self.refused('table-structure', good(baseline_entries() + [b'\x0b']))

    def test_known_madt_structures_have_exact_lengths(self):
        cases = (('gicc', I_CPU0, gicc(0, MPIDRS[0]), (75, 77, 79, 81, 83, 4)),
                 ('gicd', I_GICD, gicd(GICD_BASE), (23, 25, 4)), ('gicr', I_GICR, gicr(GICR_BASE, GICR_SIZE), (15, 17, 4)),
                 ('its', I_ITS, its(ITS_BASE), (19, 21, 4)))
        for label, index, entry, sizes in cases:
            for size in sizes:
                with self.subTest(label=label, size=size):
                    self.refused('table-structure', good(swap(baseline_entries(), index, resized(entry, size))))

    def test_the_madt_walk_has_a_bound(self):
        spare = 4096 - len(baseline_entries())
        filler = [bytes([0x70, 2])]
        self.accepted(good(baseline_entries() + filler * spare))
        exc = self.refused('table-structure', good(baseline_entries() + filler * (spare + 1)))
        self.assertIn('more than 4096', exc.reason)

    def test_redistributor_ranges_cannot_wrap_the_address_space(self):
        self.refused('table-structure', good(swap(baseline_entries(), I_GICR, gicr((1 << 64) - 0x1000, 0x10000))))

    def test_mcfg_shapes(self):
        base = ECAM[1]
        entry = struct.pack('<QHBBI', base, 0, 0, 255, 0)
        self.refused('table-length', good(MCFG=sdt(b'MCFG', b'')))
        cases = (('15 bytes of allocation', sdt(b'MCFG', bytes(8) + entry[:15])), ('no allocation', sdt(b'MCFG', bytes(8))),
                 ('start after end', mcfg(((0, base, 5, 4),))),
                 ('base not 4 KiB aligned', mcfg(((0, base + 0x800, 0, 255),))),
                 ('seventeen allocations', mcfg(tuple((n, base + n * 0x10000000, 0, 255) for n in range(17)))),
                 ('the same allocation twice', mcfg((ECAM, ECAM))),
                 ('overlapping bus ranges', mcfg(((0, base, 0, 100), (0, 0x50000000, 50, 150)))),
                 ('overlapping memory', mcfg(((0, base, 0, 255), (1, base + 0x8000000, 0, 255)))))
        for label, table in cases:
            with self.subTest(label):
                self.refused('table-structure', good(MCFG=table))

    def test_mcfg_allocations_in_distinct_memory_and_buses_are_not_overlaps(self):
        facts = bc.parse_hamoa_tables(good(ecam=((0, 0x40000000, 0, 15), (0, 0x50000000, 16, 31), (1, 0x60000000, 0, 255))))
        self.assertEqual(len(facts.ecam), 3)


class Runtime(Base):
    def hamoa(self, info=None, board=BOARD, tables=None, **kw):
        return bc.HostRuntime.from_hamoa(host_info() if info is None else info, board,
                                         good() if tables is None else tables, **kw)

    def failure(self, code, **kw):
        with deadline():
            with self.assertRaises(bc.BootContractError) as caught:
                self.hamoa(**kw)
        self.assertEqual(caught.exception.code, code, str(caught.exception))
        return caught.exception

    def test_confirmed_board_yields_the_shared_host_contract(self):
        host = self.hamoa()
        self.assertEqual(host.adapter, 'hamoa')
        self.assertEqual(dataclasses.replace(host, adapter='q1n1'), bc.HostRuntime.from_q1n1(host_info()))
        with_fb = self.hamoa(with_framebuffer=True)
        self.assertEqual(with_fb.framebuffer, bc.HostRuntime.from_q1n1(host_info(), with_framebuffer=True).framebuffer)

    def test_the_q1n1_adapter_is_unchanged(self):
        host = bc.HostRuntime.from_q1n1(host_info())
        self.assertEqual((host.adapter, host.timer_hz, host.heap.base), ('q1n1', 19200000, 0x80000000))

    def test_acpi_disagreement_is_reported_before_host_problems(self):
        self.failure('gicd-mismatch', info=host_info(current_el=1), tables=good(swap(baseline_entries(), I_GICD, gicd(0))))

    def test_timer_rate_must_match_the_descriptor(self):
        exc = self.failure('timer-mismatch', info=host_info(timer_hz=24000000))
        self.assertIn('24000000', exc.reason)

    def test_host_problems_are_wrapped_but_stay_value_errors(self):
        for label, info in (('EL1', host_info(current_el=1)), ('heap too small', host_info(heap_size=0x1000)),
                            ('unaligned heap', host_info(heap_base=0x80000800)), ('no timer', host_info(timer_hz=0))):
            with self.subTest(label):
                exc = self.failure('host-info', info=info)
                self.assertIsInstance(exc, ValueError)

    def test_a_descriptor_that_acpi_confirms_but_that_is_not_boot_ready_is_refused(self):
        exc = self.failure('descriptor-not-boot-ready', board=dataclasses.replace(BOARD, midr=None))
        self.assertIn('soc.midr', exc.reason)

    def test_the_shipped_template_cannot_become_a_host_runtime(self):
        self.failure('descriptor-unimplemented', board=bd.load(ROOT / 'config' / 'boards' / 'sp11.json'))

    def test_every_registered_code_has_a_case_that_raises_it(self):
        def check(tables=None, board=BOARD):
            return lambda: bc.check_hamoa_platform(board, good() if tables is None else tables)

        def host(**kw):
            return lambda: self.hamoa(**kw)
        entries = baseline_entries()
        cases = {
            'descriptor-unimplemented': check(board=variant(S('board.status', 'UNIMPLEMENTED'))),
            'descriptor-unknown': check(board=dataclasses.replace(BOARD, gic_version=None)),
            'descriptor-gic-version': check(board=dataclasses.replace(BOARD, gic_version=2)),
            'descriptor-inconsistent': check(board=dataclasses.replace(BOARD, gicr=(GICR_BASE, 0x60000, GICR_SIZE))),
            'descriptor-not-boot-ready': host(board=dataclasses.replace(BOARD, midr=None)),
            'table-missing': check({'RSDP': rsdp()}),
            'table-signature': check(good(XSDT=sdt(b'RSDT', b'\0' * 16))),
            'table-length': check(good(XSDT=good()['XSDT'][:-1])),
            'table-checksum': check(good(XSDT=good()['XSDT'][:9] + b'\x55' + good()['XSDT'][10:])),
            'table-structure': check(good(XSDT=xsdt((0x2000,)))),
            'gicd-mismatch': check(good(without(entries, I_GICD))),
            'version-mismatch': check(good(baseline_entries(gicd_version=3))),
            'gicr-mismatch': check(good(without(entries, I_GICR))),
            'gic-overlap': check(good(baseline_entries(gicr_ranges=((GICR_BASE, GICR_SIZE),) * 2))),
            'its-mismatch': check(good(without(entries, I_ITS))),
            'cpu-mismatch': check(good(without(entries, I_CPU0))),
            'ecam-mismatch': check(good(ecam=((0, ECAM[1], 0, 1),))),
            'timer-mismatch': host(info=host_info(timer_hz=24000000)),
            'host-info': host(info=host_info(current_el=1)),
        }
        self.assertEqual(set(cases), set(bc.BOOT_CONTRACT_CODES))
        for code, trigger in cases.items():
            with self.subTest(code), self.assertRaises(bc.BootContractError) as caught:
                trigger()
            self.assertEqual(caught.exception.code, code, str(caught.exception))


def load_bearing_offsets():
    """Byte offsets of the baseline tables whose corruption must never go unnoticed, from the public layouts."""
    signature_and_length = set(range(8))
    offsets = {'RSDP': set(range(8)) | set(range(20, 24)), 'XSDT': set(signature_and_length),
               'APIC': set(signature_and_length), 'MCFG': signature_and_length | set(range(44, 56))}
    position = 44
    for entry in baseline_entries():
        kind, size = entry[0], entry[1]
        found = {position, position + 1}
        if kind == 0xC:
            found |= set(range(position + 8, position + 16)) | {position + 20}
        elif kind == 0xB:
            # Enabled flag, a per-CPU redistributor base (zero in the baseline), and MPIDR bytes 0-2 (Aff0-Aff2),
            # 4 (Aff3) and 5-7 (RES0). Byte 3 holds RES1/U/MT and the reserved bits, which are masked off.
            found |= {position + 12} | set(range(position + 60, position + 68)) | {position + 68, position + 69,
                                                                                     position + 70} | set(range(position + 72, position + 76))
        elif kind == 0xE:
            found |= set(range(position + 4, position + 16))
        elif kind == 0xF:
            found |= set(range(position + 8, position + 16))
        offsets['APIC'] |= found
        position += size
    return offsets


class Corruption(Base):
    def test_every_load_bearing_byte_is_checked_and_no_other_byte_matters(self):
        sensitive = load_bearing_offsets()
        problems = []
        for key, table in good().items():
            for offset in range(len(table)):
                raw = bytearray(table)
                raw[offset] ^= 0xff
                repaired = fix_rsdp(raw) if key == 'RSDP' else fix_sdt(raw)
                try:
                    with deadline():
                        bc.check_hamoa_platform(BOARD, good(**{key: repaired}))
                    refused = False
                except bc.BootContractError:
                    refused = True
                if refused != (offset in sensitive[key]):
                    problems.append((key, offset, 'refused' if refused else 'accepted'))
        self.assertEqual(problems, [])

    def test_random_corruption_is_refused_or_harmless_and_always_terminates(self):
        rng = random.Random(0x5eed)
        refused = accepted = 0
        with deadline(120):
            for _ in range(4000):
                tables = good()
                key = rng.choice(sorted(tables))
                raw = bytearray(tables[key])
                for _ in range(rng.randint(1, 4)):
                    raw[rng.randrange(len(raw))] = rng.randrange(256)
                roll = rng.random()
                if roll < 0.1:
                    raw = raw[:rng.randrange(len(raw))]
                elif roll < 0.2:
                    raw += bytes(rng.randrange(256) for _ in range(rng.randint(1, 8)))
                tables[key] = bytes(raw) if rng.random() < 0.25 else (fix_rsdp(raw) if key == 'RSDP' else fix_sdt(raw))
                try:
                    platform = bc.check_hamoa_platform(BOARD, tables)
                except bc.BootContractError:
                    refused += 1
                else:
                    accepted += 1
                    self.assertEqual(dataclasses.replace(platform, notes=()), dataclasses.replace(BASELINE, notes=()))
        self.assertGreater(refused, 1500)
        self.assertGreater(accepted, 50)


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), deadline(30):
        try:
            code = cli.main([str(arg) for arg in argv])
        except SystemExit as stop:
            code = stop.code
    return code, out.getvalue(), err.getvalue()


def write_dump(directory, tables=None):
    directory.mkdir(parents=True, exist_ok=True)
    tables = good() if tables is None else tables
    for key, filename in cli.TABLE_FILES:
        if key in tables:
            (directory / filename).write_bytes(tables[key])
    (directory / 'summary.txt').write_text('synthetic dump\n', encoding='utf-8')
    (directory / 'memmap.txt').write_text('synthetic memory map\n', encoding='utf-8')
    return directory


def snapshot(root):
    return sorted((str(path.relative_to(root)), path.stat().st_size, path.stat().st_mtime_ns)
                  for path in root.rglob('*'))


def setUpModule():
    shutil.rmtree(BUILD, ignore_errors=True)
    BUILD.mkdir(parents=True)
    (BUILD / 'synthetic.json').write_text(json.dumps(tbd.synthetic()), encoding='utf-8')


def tearDownModule():
    shutil.rmtree(BUILD, ignore_errors=True)


class Cli(unittest.TestCase):
    DESCRIPTOR = BUILD / 'synthetic.json'
    SHIPPED = ROOT / 'config' / 'boards' / 'sp11.json'

    def test_a_matching_dump_is_accepted(self):
        dump = write_dump(BUILD / 'ok')
        before = snapshot(BUILD / 'ok')
        code, out, err = run_cli(dump, '--descriptor', self.DESCRIPTOR)
        self.assertEqual((code, err), (0, ''), out)
        lines = out.splitlines()
        self.assertTrue(lines[-1].startswith('ACCEPTED: '), out)
        for text in ('observed in the dump', f'{GICD_BASE:#x}', '12 enabled of 12', 'descriptor boot gate : ready'):
            self.assertIn(text, out)
        self.assertNotIn('SYNTH', out)
        self.assertEqual(snapshot(BUILD / 'ok'), before)

    def test_the_shipped_template_is_refused_but_the_dump_is_still_shown(self):
        dump = write_dump(BUILD / 'template')
        code, out, _ = run_cli(dump, '--descriptor', self.SHIPPED)
        self.assertEqual(code, 1)
        self.assertLess(out.index('observed in the dump'), out.index('REFUSED: descriptor-unimplemented'))
        self.assertTrue(out.splitlines()[-1].startswith('REFUSED: descriptor-unimplemented'), out)

    def test_the_default_descriptor_is_the_shipped_sp11_template(self):
        code, out, _ = run_cli(write_dump(BUILD / 'default'))
        self.assertEqual(code, 1)
        self.assertIn('descriptor-unimplemented', out)

    def test_a_mismatching_dump_names_the_first_mismatch(self):
        dump = write_dump(BUILD / 'stale', good(baseline_entries(its_bases=(ITS_BASE + 0x40000,))))
        code, out, _ = run_cli(dump, '--descriptor', self.DESCRIPTOR)
        self.assertEqual(code, 1)
        self.assertTrue(out.splitlines()[-1].startswith('REFUSED: its-mismatch'), out)

    def test_a_corrupt_table_is_refused_before_anything_is_trusted(self):
        for label, tables, code in (('checksum', good(APIC=good()['APIC'][:40] + b'\x7f' + good()['APIC'][41:]),
                                     'table-checksum'),
                                    ('missing', {key: value for key, value in good().items() if key != 'MCFG'},
                                     'table-missing')):
            with self.subTest(label):
                result, out, _ = run_cli(write_dump(BUILD / f'bad-{label}', tables), '--descriptor', self.DESCRIPTOR)
                self.assertEqual(result, 1)
                self.assertTrue(out.splitlines()[-1].startswith(f'REFUSED: {code}'), out)
                self.assertNotIn('observed in the dump', out)

    def test_an_invalid_descriptor_is_a_refusal_not_a_crash(self):
        bad = BUILD / 'bad-descriptor.json'
        bad.write_text('{"schema_version": 1', encoding='utf-8')
        code, out, _ = run_cli(write_dump(BUILD / 'for-bad'), '--descriptor', bad)
        self.assertEqual(code, 1)
        self.assertIn('descriptor-inconsistent', out)

    def test_run_directories(self):
        parent = BUILD / 'runs'
        code, _, err = run_cli(parent)
        self.assertEqual(code, 2, err)
        write_dump(parent / 'RUN01')
        self.assertEqual(run_cli(parent, '--descriptor', self.DESCRIPTOR)[0], 0)
        write_dump(parent / 'RUN02', good(baseline_entries(its_bases=(ITS_BASE + 0x40000,))))
        self.assertEqual(run_cli(parent, '--descriptor', self.DESCRIPTOR)[0], 2)
        self.assertEqual(run_cli(parent, '--run', 'RUN01', '--descriptor', self.DESCRIPTOR)[0], 0)
        self.assertEqual(run_cli(parent, '--run', 'RUN02', '--descriptor', self.DESCRIPTOR)[0], 1)
        self.assertEqual(run_cli(parent, '--run', 'RUN09', '--descriptor', self.DESCRIPTOR)[0], 2)
        for bad in ('../RUN01', 'run01', 'RUN', 'RUN01/'):
            with self.subTest(bad):
                self.assertEqual(run_cli(parent, '--run', bad, '--descriptor', self.DESCRIPTOR)[0], 2)

    def test_unusable_inputs_are_usage_errors(self):
        self.assertEqual(run_cli(BUILD / 'does-not-exist')[0], 2)
        self.assertEqual(run_cli(self.DESCRIPTOR)[0], 2)
        empty = BUILD / 'empty'
        empty.mkdir()
        self.assertEqual(run_cli(empty)[0], 2)
        self.assertEqual(run_cli(write_dump(BUILD / 'no-descriptor'), '--descriptor', BUILD / 'missing.json')[0], 2)
        self.assertEqual(run_cli()[0], 2)

    def test_a_directory_or_fifo_in_place_of_a_table_is_refused_without_blocking(self):
        directory = write_dump(BUILD / 'dir-as-table')
        (directory / 'APIC.bin').unlink()
        (directory / 'APIC.bin').mkdir()
        code, _, err = run_cli(directory, '--descriptor', self.DESCRIPTOR)
        self.assertEqual(code, 2)
        self.assertIn('not a regular file', err)
        fifo = write_dump(BUILD / 'fifo-as-table')
        (fifo / 'APIC.bin').unlink()
        os.mkfifo(fifo / 'APIC.bin')
        code, _, err = run_cli(fifo, '--descriptor', self.DESCRIPTOR)
        self.assertEqual(code, 2)
        self.assertIn('not a regular file', err)

    def test_an_oversize_table_is_refused_by_size(self):
        dump = write_dump(BUILD / 'oversize')
        (dump / 'APIC.bin').write_bytes(bytes((1 << 20) + 1))
        code, out, _ = run_cli(dump, '--descriptor', self.DESCRIPTOR)
        self.assertEqual(code, 1)
        self.assertIn('REFUSED: table-length', out)
        self.assertIn('table cap', out)

    def test_it_runs_as_a_script_and_exits_with_the_documented_status(self):
        dump = write_dump(BUILD / 'script')
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        script = str(ROOT / 'tools' / 'check-hamoa-acpi.py')
        good_run = subprocess.run([sys.executable, script, str(dump), '--descriptor', str(self.DESCRIPTOR)],
                                  capture_output=True, text=True, timeout=120, env=env)
        self.assertEqual(good_run.returncode, 0, good_run.stdout + good_run.stderr)
        bad_run = subprocess.run([sys.executable, script, str(dump), '--descriptor', str(self.SHIPPED)],
                                 capture_output=True, text=True, timeout=120, env=env)
        self.assertEqual(bad_run.returncode, 1, bad_run.stdout + bad_run.stderr)
        usage = subprocess.run([sys.executable, script, str(BUILD / 'nowhere')], capture_output=True, text=True,
                               timeout=120, env=env)
        self.assertEqual(usage.returncode, 2, usage.stdout + usage.stderr)

    def test_the_tool_and_the_adapter_have_no_way_to_write(self):
        forbidden = re.compile(r'\.write\(|\.write_bytes|\.write_text|unlink|mkdir|rmdir|rename|replace\(|shutil|'
                               r'O_WRONLY|O_RDWR|O_CREAT|O_TRUNC|O_APPEND|chmod|symlink|truncate|subprocess|socket',
                               re.M)
        for relpath in ('tools/check-hamoa-acpi.py',):
            source = (ROOT / relpath).read_text(encoding='utf-8')
            code = '\n'.join(line for line in source.splitlines() if not line.lstrip().startswith(('#', '"""')))
            self.assertEqual(forbidden.findall(code.split('def main', 1)[0] + code.split('def main', 1)[1]), [], relpath)
        adapter = (ROOT / 'probes' / 'bootcontract.py').read_text(encoding='utf-8')
        section = adapter.split('# Hamoa ACPI cross-check', 1)[1]
        self.assertEqual([m for m in re.findall(r'\bopen\(|\.write|unlink|mkdir|os\.|subprocess', section)
                          if m != 'os.'], [])


class Hygiene(unittest.TestCase):
    def test_owned_files_follow_the_repository_rules(self):
        for relpath in OWNED_FILES:
            with self.subTest(relpath):
                text = (ROOT / relpath).read_text(encoding='utf-8')
                self.assertIn('SPDX-License-Identifier: MIT', '\n'.join(text.splitlines()[:3]))
                self.assertLess(len(text.splitlines()), 1500)
                self.assertLess(len(text.encode()), 50 * 1024)
                for prefix in HOME_PREFIXES:
                    self.assertNotIn(prefix, text)
                self.assertIsNone(re.search(r'(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b', text), 'MAC-like string')

    def test_a16_callers_of_bootcontract_do_not_import_the_descriptor_module(self):
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import bootcontract; "
                "sys.exit(1 if 'board_descriptor' in sys.modules else 0)")
        result = subprocess.run([sys.executable, '-c', code, str(ROOT / 'probes')], capture_output=True, text=True,
                                timeout=120, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_existing_contract_tests_are_not_edited_by_this_work(self):
        text = (ROOT / 'tools' / 'test-bootcontract.py').read_text(encoding='utf-8')
        self.assertNotIn('hamoa', text.lower())


if __name__ == '__main__':
    unittest.main()
