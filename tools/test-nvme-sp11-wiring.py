#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The SP11 NVMe window, wired: the descriptor's storage facts, the target block the host builds from them, the reservation
layout the monitor reads, and the contract's refusals on the unit's own geometry.

  python3 tools/test-nvme-sp11-wiring.py [-v]

What it holds (each class names it):

  Layout       probes/nvmesp11.py, the C structs, compat/ans.h and the glue agree: field order, sizes, constants, offsets;
  Producer     the target is built only from the descriptor and the run's pins; a group the descriptor leaves out stays
               unknown; the placement helper refuses a short reservation and a block left from another run;
  Contract     on the unit's geometry (segment 6, ECAM 0x70000000, buses 0 and 1, 01:00.0, the SMMUv3 at 0x15400000, the
               StreamID window from the IORT) the Python model and compat/nvme-host.c (ASan and UBSan) accept the matching
               record and refuse each deliberately faulty one with its own reason: a wrong segment, another ECAM, a missing
               BDF, a class that is not NVM Express, a window past the namespace, a window into a GPT zone, an ECAM window
               across the 36-bit limit, ...;
  Adopt        nvmeh_sp11_adopt end to end on a fake controller: placed blocks adopt, an unplaced pair is refused by name,
               a half-placed pair and a wrong endpoint are refused before one doorbell;
  Reservation  the bytes the host places at the glue's offsets are the bytes the C code checks.

Synthetic geometry on the unit's documented constants (docs/SP11-NVME-WINDOW.md section 7.1): no GPT of the unit, no EUI64
and no BAR is read from anywhere; the namespace identity and the generation are made up. Evidence: VERIFIED on the host only.
Hardware: NOT RUN. The monitor's call site is held by tools/test-monitor-hardening.py (NvmeWindowWiring and its mutants).
"""
import copy
import dataclasses
import json
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for _dir in (ROOT / 'tools', ROOT / 'probes', ROOT / 'tools' / 'fixtures' / 'sp11'):
    sys.path.insert(0, str(_dir))
import answindow  # noqa: E402
import board_descriptor as bd  # noqa: E402
import nvmesp11 as ns  # noqa: E402
import nvmewindow as nw  # noqa: E402

BOARDS = ROOT / 'config' / 'boards'
BUILD = ROOT / 'build' / 'test-nvme-sp11-wiring'
FIXTURE = {'source': 'synthetic test fixture', 'evidence': 'EXPERIMENTAL'}
EUI, GENERATION = 0x1122334455667788, 7           # per-run pins, made up
SP11_CFG = [0xa80b144d, 0x00100006, 0x01080202, 0, nw.BAR | 4, 0, 0, 0] + [0] * 8    # the endpoint's first 16 dwords
UNIT = dict(segment=6, ecam=0x70000000, bdf=0x0100, smmu=0x15400000, stream_base=0x60000)


def descriptor(*edits, present=True):
    """The shipped descriptor as a dict, with the NVMe device present (a board that withholds it gets no target)."""
    d = json.loads((BOARDS / 'sp11.json').read_text(encoding='utf-8'))
    if present:
        d['devices']['nvme'] = {'state': 'present', 'mmio': False, 'ranges': []}
        d['provenance']['devices.nvme'] = dict(FIXTURE)
    for edit in edits:
        edit(d)
    return d


def board(*edits, present=True):
    return bd.validate(descriptor(*edits, present=present))


def unknown_storage(*keys):
    def edit(d):
        for key in keys:
            d['storage']['nvme'][key] = None
            d['provenance'][f'storage.nvme.{key}'] = {'source': 'UNKNOWN', 'evidence': 'UNIMPLEMENTED'}
    return edit


def sp11_case(b=None, writable=False, eui=EUI, generation=GENERATION):
    """A case whose target comes from the descriptor and whose record holds what the unit's takeover would observe."""
    t = ns.target_fields(b or board(), eui, generation)
    case = nw.base_case(writable=writable)
    case['t'] = {k: t[k] for k in nw.TARGET_FIELDS}
    # The record holds what the takeover observed; a pin the run lacks is not a reason for the record to lack its own value.
    observed_eui, observed_generation = eui or EUI, generation or GENERATION
    case['r'].update(generation=observed_generation, segment=UNIT['segment'], bdf=UNIT['bdf'], ecam_base=UNIT['ecam'],
                     cfg_pa=UNIT['ecam'] + nw.cfg_offset(UNIT['bdf']), class_code=0x010802, smmu_base=UNIT['smmu'],
                     smmu_mode=2, stream_id=UNIT['stream_base'] + UNIT['bdf'], eui64=observed_eui)
    return case


def edited(*edits, base=None):
    return nw.edited(*edits, base=base or sp11_case())


def verdict(case):
    model, c = nw.verdicts([case])
    assert model == c, (model, c)
    return c[0]


def run_adopt(case, ops, cfg=SP11_CFG, stale=None, driver=None):
    """The native adoption on a fake controller. `stale` is a state word an earlier launch left in the reservation; the answer then
    also holds `stop_reads`, the controller registers nvmeh_stop read afterwards. `driver` is another build of the driver."""
    line = nw.hexw(nw.words_of(case) + list(cfg)) + ' ' + ' '.join(ops)
    if stale is not None:
        line = f'S{stale:x} {line}'
    if driver is None:
        out = nw.native('adopt', [line])[0].split()
    else:
        proc = subprocess.run([str(driver), 'adopt'], input=line + '\n', capture_output=True, text=True, timeout=120, check=True)
        out = proc.stdout.split()
    stop_reads = int(out.pop()[1:]) if stale is not None else None
    return dict(rc=int(out[0]), prepare=int(out[1]), doorbells=int(out[2]), violations=int(out[3]), badreads=int(out[4]),
                ops=[tuple(int(x) for x in r.split(':')) for r in out[5:-1]], final_violations=int(out[-1][1:]), stop_reads=stop_reads)


def mutant_driver(old, new):
    """The native driver built from compat/nvme-host.c with `old` replaced by `new` (once): the control of an experiment, which must
    behave differently from the real one. Built beside the real driver with its flags."""
    clang = nw.find_tool('clang')
    if not clang:
        raise unittest.SkipTest('clang not found')
    text = (ROOT / 'compat' / 'nvme-host.c').read_text(encoding='utf-8')
    assert text.count(old) == 1, f'the needle {old!r} must occur once'
    BUILD.mkdir(parents=True, exist_ok=True)
    source, exe = BUILD / 'nvme-host-mutant.c', BUILD / 'driver-mutant'
    source.write_text(text.replace(old, new, 1), encoding='utf-8')
    built = subprocess.run([clang, '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                            '-fno-sanitize-recover=undefined', '-DSNAP_PLATFORM_SP11=1', f'-I{ROOT / "compat"}',
                            str(ROOT / 'tools' / 'test-nvme-window-hamoa.c'), str(source), str(ROOT / 'compat' / 'board-info.c'),
                            '-o', str(exe)], capture_output=True, text=True, timeout=300)
    if built.returncode:
        raise AssertionError(f'host build of the mutant driver failed:\n{built.stderr[-3000:]}')
    return exe


def zero_blocks(case):
    case = copy.deepcopy(case)
    case['t'] = dict.fromkeys(nw.TARGET_FIELDS, 0)
    case['r'] = dict.fromkeys(nw.RECORD_FIELDS, 0) | {'ext': [(0, 0)] * nw.MAX_PROT}
    return case


def macro_values():
    """compat/ans.h's reservation offsets, evaluated by the compiler (the header is the monitor's own text)."""
    clang = nw.find_tool('clang')
    if not clang:
        raise unittest.SkipTest('clang not found')
    BUILD.mkdir(parents=True, exist_ok=True)
    exe = BUILD / 'offsets'
    source = ('#include <stdio.h>\n#include "ans.h"\nint main(void) {\n'
              '    printf("%llu %llu %llu %llu\\n", (unsigned long long)ANS_WINDOW_NVMEH,\n'
              '           (unsigned long long)ANS_WINDOW_NVMEH_TARGET, (unsigned long long)ANS_WINDOW_NVMEH_RECORD,\n'
              '           (unsigned long long)ANS_WINDOW_DMA);\n    return 0;\n}\n')
    with tempfile.NamedTemporaryFile('w', suffix='.c', dir=BUILD, delete=False) as handle:
        handle.write(source)
    try:
        built = subprocess.run([clang, '-std=gnu11', '-Wall', '-Werror', f'-I{ROOT / "compat"}', handle.name, '-o', str(exe)],
                               capture_output=True, text=True, timeout=120)
        if built.returncode:
            raise AssertionError(built.stderr[-2000:])
        out = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30, check=True).stdout.split()
    finally:
        Path(handle.name).unlink(missing_ok=True)
    return dict(zip(('nvmeh', 'target', 'record', 'dma'), (int(x) for x in out)))


class Layout(unittest.TestCase):
    def test_the_field_tables_are_the_models_and_the_driver_s(self):
        self.assertEqual(ns.TARGET_FIELDS, nw.TARGET_FIELDS)
        self.assertEqual(ns.RECORD_FIELDS, nw.RECORD_FIELDS)
        self.assertEqual((ns.K_ECAM, ns.K_BDF, ns.K_SMMU, ns.K_STREAM, ns.K_EUI64, ns.K_GENERATION, ns.K_ALL),
                         (nw.K_ECAM, nw.K_BDF, nw.K_SMMU, nw.K_STREAM, nw.K_EUI64, nw.K_GEN, nw.K_ALL))
        self.assertEqual((ns.R_CLEAN, ns.R_DMA_PROVEN, ns.R_WRITE_AUTH), (nw.R_CLEAN, nw.R_DMA, nw.R_WRITE))
        self.assertEqual((ns.ABI, ns.MAX_PROTECTED, ns.NVME_CLASS), (1, nw.MAX_PROT, 0x010802))

    def test_the_block_sizes_are_the_c_structs(self):
        sizes = {}
        proc = subprocess.run([str(nw.driver()), 'codes'], capture_output=True, text=True, timeout=60, check=True)
        for line in proc.stdout.splitlines():
            if line.startswith('size '):
                _, name, value = line.split()
                sizes[name] = int(value)
            if line.startswith('const '):
                _, name, value = line.split()
                sizes[name] = int(value)
        self.assertEqual((ns.TARGET_BYTES, ns.RECORD_BYTES), (sizes['target'], sizes['record']))
        self.assertEqual((ns.TARGET_BYTES, ns.RECORD_BYTES), (128, 656))
        for name, value in (('NVMEH_SP11_K_ALL', ns.K_ALL), ('NVMEH_SP11_R_ALL', 7), ('NVMEH_SP11_SMMU_BYPASS', ns.SMMU_BYPASS),
                            ('NVMEH_SP11_SMMU_S2_IDENTITY', ns.SMMU_S2_IDENTITY), ('NVMEH_SP11_MAX_PROTECTED', ns.MAX_PROTECTED),
                            ('NVMEH_SP11_CLASS_NVME', ns.NVME_CLASS)):
            with self.subTest(name):
                self.assertEqual(sizes[name], value)

    def test_the_offsets_are_the_monitor_s_own(self):
        offsets = macro_values()
        self.assertEqual(offsets['nvmeh'], answindow.WINDOW_NVMEH)
        self.assertEqual(offsets['target'], ns.TARGET_OFFSET)
        self.assertEqual(offsets['record'], ns.RECORD_OFFSET)
        self.assertEqual(offsets['dma'], answindow.WINDOW_DMA)
        # After the params (0x80) and the state (0xc0), in order, below the DMA window: ans_glue.c holds this with a
        # _Static_assert, and a host that placed a block elsewhere would be refused for an ABI mismatch, not noticed.
        self.assertLessEqual(offsets['nvmeh'] + 0x80 + 0xc0, offsets['target'])
        self.assertLessEqual(offsets['target'] + ns.TARGET_BYTES, offsets['record'])
        self.assertLessEqual(offsets['record'] + ns.RECORD_BYTES, offsets['dma'])

    def test_the_glue_reads_the_blocks_at_those_offsets_and_only_on_an_sp11_build(self):
        glue = (ROOT / 'probes' / 'xnuboot' / 'ans_glue.c').read_text(encoding='utf-8')
        start = glue.index('#if SNAP_PLATFORM_SP11\n#define ANS_NVMEH_TARGET')
        block = glue[start:glue.index('#endif', glue.index('_Static_assert', start))]
        self.assertIn('((const struct nvmeh_sp11_target *)((res)+ANS_WINDOW_NVMEH_TARGET))', block)
        self.assertIn('((const struct nvmeh_sp11_record *)((res)+ANS_WINDOW_NVMEH_RECORD))', block)
        self.assertEqual(glue.count('ANS_WINDOW_NVMEH_TARGET'), 3)      # the macro and the two uses in the static assertion
        self.assertEqual(len(re.findall(r'ANS_NVMEH_TARGET\(res\)', glue)), 2)       # the macro and its one use: the SP11 call

    def test_the_a16_text_of_the_two_headers_does_not_mention_the_sp11_blocks_outside_their_gate(self):
        header = (ROOT / 'compat' / 'ans.h').read_text(encoding='utf-8')
        self.assertEqual(header.count('ANS_WINDOW_NVMEH_TARGET'), 1)
        self.assertEqual(header.count('ANS_WINDOW_NVMEH_RECORD'), 1)
        glue = (ROOT / 'probes' / 'xnuboot' / 'ans_glue.c').read_text(encoding='utf-8')
        sp11_only = glue[glue.index('#if SNAP_PLATFORM_SP11\n#define ANS_NVMEH_TARGET'):]
        self.assertNotIn('nvmeh_sp11_target', glue[:glue.index('#if SNAP_PLATFORM_SP11\n#define ANS_NVMEH_TARGET')])
        self.assertIn('nvmeh_sp11_target', sp11_only)


class Producer(unittest.TestCase):
    def test_the_target_is_the_descriptors_and_the_pins(self):
        t = ns.target_fields(board(), EUI, GENERATION)
        self.assertEqual(t, dict(abi=1, known=63, segment=6, ecam_base=0x70000000, start_bus=0, end_bus=1, bdf=0x0100,
                                 smmu_base=0x15400000, stream_base=0x60000, rid_base=0, rid_count=0x10000, eui64=EUI,
                                 generation=GENERATION, reserved0=0, reserved1=0, reserved2=0))

    def test_a_group_the_descriptor_or_the_run_leaves_out_stays_unknown(self):
        cases = (('BDF absent', board(unknown_storage('endpoint')), EUI, GENERATION, ns.K_ALL & ~ns.K_BDF),
                 ('no stream window', board(unknown_storage('stream')), EUI, GENERATION, ns.K_ALL & ~ns.K_STREAM),
                 ('no SMMU', board(unknown_storage('stream', 'smmu')), EUI, GENERATION,
                  ns.K_ALL & ~ns.K_SMMU & ~ns.K_STREAM),
                 ('no ECAM and so no BDF', board(unknown_storage('stream', 'smmu', 'endpoint', 'root_port', 'ecam')), EUI,
                  GENERATION, ns.K_ALL & ~ns.K_ECAM & ~ns.K_BDF & ~ns.K_SMMU & ~ns.K_STREAM),
                 ('no segment', board(unknown_storage('stream', 'smmu', 'endpoint', 'root_port', 'ecam', 'segment')), EUI,
                  GENERATION, ns.K_EUI64 | ns.K_GENERATION),
                 ('no namespace pin', board(), None, GENERATION, ns.K_ALL & ~ns.K_EUI64),
                 ('no run pin', board(), EUI, None, ns.K_ALL & ~ns.K_GENERATION))
        for name, b, eui, generation, known in cases:
            with self.subTest(name):
                t = ns.target_fields(b, eui, generation)
                self.assertEqual(t['known'], known)
                for field, group in (('bdf', ns.K_BDF), ('smmu_base', ns.K_SMMU), ('rid_count', ns.K_STREAM),
                                     ('eui64', ns.K_EUI64), ('generation', ns.K_GENERATION), ('ecam_base', ns.K_ECAM)):
                    if not known & group:
                        self.assertEqual(t[field], 0, f'{field} carries a value for a group marked unknown')

    def test_the_c_contract_refuses_each_unknown_group_by_name(self):
        for name, b, reason in (('BDF absent', board(unknown_storage('endpoint')), 'UNKNOWN_BDF'),
                                ('no stream window', board(unknown_storage('stream')), 'UNKNOWN_STREAM'),
                                ('no SMMU', board(unknown_storage('stream', 'smmu')), 'UNKNOWN_SMMU'),
                                ('no ECAM', board(unknown_storage('stream', 'smmu', 'endpoint', 'root_port', 'ecam')),
                                 'UNKNOWN_ECAM')):
            with self.subTest(name):
                self.assertEqual(verdict(sp11_case(b)), reason)
        for name, kw, reason in (('no namespace pin', dict(eui=None), 'UNKNOWN_EUI64'),
                                 ('no run pin', dict(generation=None), 'UNKNOWN_GENERATION')):
            with self.subTest(name):
                self.assertEqual(verdict(sp11_case(**kw)), reason)

    def test_no_target_without_the_device_or_the_section(self):
        def nvme_is(state):
            def edit(d):
                d['devices']['nvme'] = {'state': state, 'mmio': None, 'ranges': []}
                d['provenance']['devices.nvme'] = dict(FIXTURE)
            return edit
        for name, b, reason in (('the shipped template (nvme unknown)', board(present=False), 'DEVICE_NOT_PRESENT'),
                                ('the guest view that withholds the SSD', board(nvme_is('absent'), present=False),
                                 'DEVICE_NOT_PRESENT')):
            with self.subTest(name), self.assertRaises(ns.Refused) as caught:
                ns.target_fields(b, EUI, GENERATION)
            self.assertEqual(caught.exception.reason, reason)
        a16 = bd.load(BOARDS / 'a16.json')
        with self.assertRaises(ns.Refused) as caught:
            ns.target_fields(a16, EUI, GENERATION)
        self.assertIn(caught.exception.reason, ('DEVICE_NOT_PRESENT', 'NO_STORAGE_SECTION'))
        with self.assertRaises(ns.Refused) as caught:
            ns.target_fields({'not': 'a board'}, EUI, GENERATION)
        self.assertEqual(caught.exception.reason, 'NOT_A_BOARD')

    def test_the_pins_are_nonzero_unsigned_64_bit_integers(self):
        for value in (0, -1, 1 << 64, True, 1.0, '7', [7]):
            for key in ('eui64', 'generation'):
                with self.subTest(key=key, value=value), self.assertRaises(ns.Refused) as caught:
                    ns.target_fields(board(), **{key: value})
                self.assertEqual(caught.exception.reason, 'PIN_INVALID')

    def test_pack_round_trips_and_refuses_what_the_monitor_would_refuse(self):
        t = ns.target_fields(board(), EUI, GENERATION)
        self.assertEqual(ns.unpack_target(ns.pack_target(t)), t)
        fields = {k: 0 for k in ns.RECORD_FIELDS} | dict(abi=1, protected_count=2)
        raw = ns.pack_record(fields, [(2048, 4095), (8192, 9000)])
        self.assertEqual(len(raw), ns.RECORD_BYTES)
        got, extents = ns.unpack_record(raw)
        self.assertEqual((got, extents), (fields, [(2048, 4095), (8192, 9000)]))
        full = ns.pack_record(fields | {'protected_count': 32}, [(1, 2)] * 32)             # the monitor's limit is exactly 32 extents
        self.assertEqual(ns.unpack_record(full)[1], [(1, 2)] * 32)
        over = bytearray(full)
        struct.pack_into('<Q', over, 8 * ns.RECORD_FIELDS.index('protected_count'), 33)
        self.assertEqual(ns.unpack_record(bytes(over))[1], [(1, 2)] * 32)               # the decoder shows what is there; the monitor refuses a count of 33
        struct.pack_into('<Q', over, 8 * ns.RECORD_FIELDS.index('protected_count'), 31)
        self.assertEqual(ns.unpack_record(bytes(over))[1], [(1, 2)] * 31)
        for name, call in (
                ('target with a missing field', lambda: ns.pack_target({k: 0 for k in ns.TARGET_FIELDS[:-1]})),
                ('target with an extra field', lambda: ns.pack_target({k: 0 for k in ns.TARGET_FIELDS} | {'serial': 1})),
                ('target word beyond 64 bits', lambda: ns.pack_target(t | {'ecam_base': 1 << 64})),
                ('target word negative', lambda: ns.pack_target(t | {'bdf': -1})),
                ('target word as a bool', lambda: ns.pack_target(t | {'abi': True})),
                ('record count that is not the extents given', lambda: ns.pack_record(fields | {'protected_count': 1},
                                                                                       [(1, 2), (3, 4)])),
                ('record with 33 extents', lambda: ns.pack_record(fields | {'protected_count': 33}, [(1, 2)] * 33)),
                ('record extent beyond 64 bits', lambda: ns.pack_record(fields | {'protected_count': 1}, [(1, 1 << 64)])),
                ('record missing a field', lambda: ns.pack_record({k: 0 for k in ns.RECORD_FIELDS[:-1]}, [])),
                ('short target', lambda: ns.unpack_target(bytes(127))),
                ('long record', lambda: ns.unpack_record(bytes(657)))):
            with self.subTest(name), self.assertRaises(ns.Refused):
                call()

    def test_placing_blocks_refuses_a_short_reservation_a_wrong_size_and_a_block_from_another_run(self):
        target = ns.pack_target(ns.target_fields(board(), EUI, GENERATION))
        record = ns.pack_record({k: 0 for k in ns.RECORD_FIELDS} | dict(abi=1, protected_count=1), [(2048, 4095)])
        res = bytearray(answindow.WINDOW_BYTES)
        ns.place(res, target, record)
        self.assertEqual(bytes(res[ns.TARGET_OFFSET:ns.TARGET_OFFSET + ns.TARGET_BYTES]), target)
        self.assertEqual(bytes(res[ns.RECORD_OFFSET:ns.RECORD_OFFSET + ns.RECORD_BYTES]), record)
        self.assertEqual(sum(1 for b in res if b), sum(1 for b in target + record if b))     # nothing else was touched
        for name, reason, call in (
                ('a second placement over the first', 'BLOCK_NOT_EMPTY', lambda: ns.place(res, target, record)),
                ('a reservation that ends before the record', 'RESERVATION',
                 lambda: ns.place(bytearray(ns.RECORD_OFFSET + ns.RECORD_BYTES - 1), target, record)),
                ('a reservation that is immutable bytes', 'RESERVATION',
                 lambda: ns.place(bytes(answindow.WINDOW_BYTES), target, record)),
                ('a short target', 'SIZE', lambda: ns.place(bytearray(answindow.WINDOW_BYTES), target[:-8], record)),
                ('a long record', 'SIZE', lambda: ns.place(bytearray(answindow.WINDOW_BYTES), target, record + bytes(8)))):
            with self.subTest(name), self.assertRaises(ns.Refused) as caught:
                call()
            self.assertEqual(caught.exception.reason, reason)
        stale = bytearray(answindow.WINDOW_BYTES)
        stale[ns.RECORD_OFFSET + 640] = 1                                 # one stray byte of an earlier run's record
        with self.assertRaises(ns.Refused) as caught:
            ns.place(stale, target, record)
        self.assertEqual(caught.exception.reason, 'BLOCK_NOT_EMPTY')
        self.assertFalse(any(stale[ns.TARGET_OFFSET:ns.TARGET_OFFSET + ns.TARGET_BYTES]), 'a refused placement wrote nothing')

    def test_placing_blocks_checks_the_layout_and_accepts_its_exact_limits(self):
        target = ns.pack_target(ns.target_fields(board(), EUI, GENERATION))
        record = ns.pack_record({k: 0 for k in ns.RECORD_FIELDS} | dict(abi=1, protected_count=1), [(2048, 4095)])
        ns.place(bytearray(ns.RECORD_OFFSET + ns.RECORD_BYTES), target, record)           # a reservation that ends exactly at the record
        adjacent, flush = ns.TARGET_OFFSET + ns.TARGET_BYTES, answindow.WINDOW_DMA - ns.RECORD_BYTES
        for name, offset in (('the record right after the target', adjacent), ('the record ending exactly at the DMA window', flush)):
            res = bytearray(answindow.WINDOW_BYTES)
            with self.subTest(name), mock.patch.object(ns, 'RECORD_OFFSET', offset):
                ns.place(res, target, record)
                self.assertEqual(bytes(res[offset:offset + ns.RECORD_BYTES]), record)
        for name, offset in (('the record over the target', adjacent - 8), ('the record running into the DMA window', flush + 8)):
            with self.subTest(name), mock.patch.object(ns, 'RECORD_OFFSET', offset), self.assertRaises(ns.Refused) as caught:
                ns.place(bytearray(answindow.WINDOW_BYTES), target, record)
            self.assertEqual(caught.exception.reason, 'LAYOUT')

    def test_the_smmu_is_the_named_smmuv3_and_a_board_without_a_storage_section_gets_no_target(self):
        plain = board()
        with self.assertRaises(ns.Refused) as caught:
            ns.target_fields(dataclasses.replace(plain, storage=None), EUI, GENERATION)
        self.assertEqual(caught.exception.reason, 'NO_STORAGE_SECTION')
        two = (('pcie-smmu', 'smmu-v3', 0x15400000, 0x80000), ('other-smmu', 'smmu-v3', 0x15500000, 0x80000))
        self.assertEqual(ns.target_fields(dataclasses.replace(plain, smmus=two), EUI, GENERATION)['smmu_base'], 0x15400000)
        for name, smmus in (('the named entry is not an SMMUv3', (('pcie-smmu', 'mmu-500', 0x15400000, 0x80000),)),
                            ('the named entry is not in the list', (('other-smmu', 'smmu-v3', 0x15500000, 0x80000),)),
                            ('the name is there twice', two + (('pcie-smmu', 'smmu-v3', 0x15600000, 0x80000),))):
            with self.subTest(name), self.assertRaises(ns.Refused) as caught:
                ns.target_fields(dataclasses.replace(plain, smmus=smmus), EUI, GENERATION)
            self.assertEqual(caught.exception.reason, 'SMMU_NOT_FOUND')

    def test_the_observed_values_are_cross_checked_against_the_descriptor(self):
        b = board()
        good = dict(segment=6, ecam_base=0x70000000, start_bus=0, end_bus=1, smmu_base=0x15400000, stream_id=0x60100,
                    root_port=dict(bus=0, device=0, function=0, vendor_id=0x17cb, device_id=0x0111, class_code=0x060400),
                    endpoint=dict(bus=1, device=0, function=0, vendor_id=0x144d, device_id=0xa80b, class_code=0x010802))
        self.assertEqual(ns.check_observed(b, good), [])
        self.assertEqual(ns.check_observed(b, {}), [])        # nothing observed, nothing contradicted
        for name, edit, expect in (
                ('another segment', lambda o: o.update(segment=5), 'SEGMENT'),
                ('another ECAM base', lambda o: o.update(ecam_base=0x7e000000), 'ECAM_BASE'),
                ('another bus range', lambda o: o.update(end_bus=255), 'END_BUS'),
                ('another SMMU', lambda o: o.update(smmu_base=0x15000000), 'SMMU_BASE'),
                ('another StreamID (segment 5\'s)', lambda o: o.update(stream_id=0x50100), 'STREAM_ID'),
                ('the endpoint on bus 2', lambda o: o['endpoint'].update(bus=2), 'ENDPOINT_BUS'),
                ('the endpoint at function 1', lambda o: o['endpoint'].update(function=1), 'ENDPOINT_FUNCTION'),
                ('a class that is not NVM Express', lambda o: o['endpoint'].update(class_code=0x010601), 'ENDPOINT_CLASS_CODE'),
                ('another vendor', lambda o: o['endpoint'].update(vendor_id=0x1b4b), 'ENDPOINT_VENDOR_ID'),
                ('another drive (device id)', lambda o: o['endpoint'].update(device_id=0xa80a), 'ENDPOINT_DEVICE_ID'),
                ('another root port', lambda o: o['root_port'].update(device_id=0x0120), 'ROOT_PORT_DEVICE_ID'),
                ('a root port that is not a bridge', lambda o: o['root_port'].update(class_code=0x010802),
                 'ROOT_PORT_CLASS_CODE')):
            o = copy.deepcopy(good)
            edit(o)
            with self.subTest(name):
                found = ns.check_observed(b, o)
                self.assertEqual([f[0] for f in found], [expect])
                self.assertEqual(found[0][1:] != (None, None), True)
        unstated = ns.check_observed(board(unknown_storage('endpoint')), good)
        self.assertEqual(sorted(f[0] for f in unstated),
                         sorted(['UNSTATED_ENDPOINT_' + k for k in ('BUS', 'DEVICE', 'FUNCTION', 'VENDOR_ID', 'DEVICE_ID',
                                                                    'CLASS_CODE')] + ['UNSTATED_STREAM_ID']))
        with self.assertRaises(ns.Refused):
            ns.check_observed(bd.load(BOARDS / 'a16.json'), good)


class Contract(unittest.TestCase):
    """The model and the C code on the unit's geometry, against written-out verdicts."""

    CASES = (
        ('the matching record is accepted', 'OK', ()),
        ('a wrong segment (segment 5 is the A16\'s)', 'SEGMENT', (nw.setr(segment=5),)),
        ('another ECAM base (the unit\'s segment 5 allocation)', 'ECAM', (nw.setr(ecam_base=0x7e000000),)),
        ('the A16\'s ECAM base', 'ECAM', (nw.setr(ecam_base=0x7A0000000),)),
        ('the endpoint on bus 2, past the MCFG buses', 'BUS_RANGE', (nw.setr(bdf=0x0200, cfg_pa=0x70000000 + (2 << 20)),)),
        ('the endpoint at 01:01.0', 'BDF', (nw.setr(bdf=0x0108, cfg_pa=0x70100000 + (1 << 15)),)),
        ('the endpoint at 00:00.0 (the root port)', 'BDF', (nw.setr(bdf=0x0000, cfg_pa=0x70000000),)),
        ('a config address that is not the BDF\'s', 'CFG_ADDRESS', (nw.setr(cfg_pa=0x70000000),)),
        ('BDF absent from the descriptor', 'UNKNOWN_BDF', (nw.sett(known=ns.K_ALL & ~ns.K_BDF),)),
        ('a class that is not NVM Express (SATA)', 'CLASS', (nw.setr(class_code=0x010601),)),
        ('a class that is the root port\'s', 'CLASS', (nw.setr(class_code=0x060400),)),
        ('another namespace', 'EUI64', (nw.setr(eui64=EUI + 1),)),
        ('the apps SMMU instead of the PCIe one', 'SMMU_BASE', (nw.setr(smmu_base=0x15000000),)),
        ('an SMMU that aborts the stream', 'SMMU_MODE', (nw.setr(smmu_mode=0),)),
        ('an SMMU that translates in stage 1', 'SMMU_MODE', (nw.setr(smmu_mode=3),)),
        ('DMA never proven reachable', 'DMA_UNPROVEN', (nw.setr(flags=nw.R_CLEAN),)),
        ('segment 5\'s StreamID for this endpoint', 'STREAM', (nw.setr(stream_id=0x50100),)),
        ('the StreamID of the root port', 'STREAM', (nw.setr(stream_id=0x60000),)),
        ('a record from another run', 'STALE', (nw.setr(generation=GENERATION - 1),)),
        ('a hand-off that was not clean', 'RECORD_UNCLEAN', (nw.setr(flags=nw.R_DMA),)),
        ('a 4096-byte LBA format', 'LBA_FORMAT', (nw.setr(lba_bytes=4096),)),
        ('a format with metadata', 'METADATA', (nw.setr(metadata_bytes=8),)),
        ('a window ending at the namespace size', 'WINDOW_RANGE', (nw.window(nw.WIN_A[0], nw.LAYOUT_A.nsze),)),
        ('a window far beyond the namespace', 'WINDOW_RANGE', (nw.window(nw.WIN_A[0], nw.LAYOUT_A.nsze + (1 << 30)),)),
        ('a window into the backup GPT zone', 'WINDOW_GPT', (nw.window(nw.WIN_A[0], nw.LAYOUT_A.last_usable + 10),)),
        ('a window over the Windows volume', 'OVERLAP', (nw.window(nw.WIN_A[0] - 4096, nw.WIN_A[1]),)),
        ('a window one block over the recovery partition', 'OVERLAP',
         (nw.window(nw.WIN_A[0], nw.LAYOUT_A.partitions[-1].first),)),
        ('a window that is not a whole number of MiB', 'WINDOW_ALIGN', (nw.window(nw.WIN_A[0], nw.WIN_A[1] - 1),)),
        ('the ECAM window across the 36-bit limit', 'TARGET_INVALID', (nw.sett(ecam_base=(1 << 36) - (1 << 20)),)),
        ('the ECAM window at the 36-bit limit', 'TARGET_INVALID', (nw.sett(ecam_base=1 << 36),)),
        ('the SMMU pages across the 36-bit limit', 'TARGET_INVALID', (nw.sett(smmu_base=(1 << 36) - 0x10000),)),
        ('the ECAM window ending exactly at the limit', 'ECAM',
         (nw.sett(ecam_base=(1 << 36) - (2 << 20)),)),            # accepted as a pin; the record's base differs
        ('an ECAM base that is not 1 MiB aligned', 'TARGET_INVALID', (nw.sett(ecam_base=0x70080000),)),
        ('a writable window without the per-run authorisation', 'WRITE_UNAUTHORISED', (nw.writable,
                                                                                     nw.setr(flags=nw.R_CLEAN | nw.R_DMA))),
    )

    def test_every_case_has_its_written_out_verdict_in_both_implementations(self):
        cases = [edited(*edits) for _, _, edits in self.CASES]
        model, c = nw.verdicts(cases)
        for (name, expected, _), m, n in zip(self.CASES, model, c):
            with self.subTest(name):
                self.assertEqual((m, n), (expected, expected))

    def test_the_base_case_is_the_units_own_geometry(self):
        case = sp11_case()
        self.assertEqual((case['t']['segment'], case['t']['ecam_base'], case['t']['end_bus'], case['t']['bdf'],
                          case['t']['smmu_base'], case['t']['stream_base']),
                         (6, 0x70000000, 1, 0x100, 0x15400000, 0x60000))
        self.assertEqual(case['r']['cfg_pa'], 0x70100000)
        self.assertEqual(verdict(case), 'OK')
        self.assertEqual(verdict(sp11_case(writable=True)), 'OK')              # the writable base carries the authorisation

    def test_a_target_that_is_not_the_descriptors_is_refused_by_the_record_check(self):
        """The record is compared with the descriptor's target, field by field: changing the target and not the record
        (what a stale or wrong producer would do) is as visible as changing the record."""
        for name, edit, expected in (('another segment', nw.sett(segment=5), 'SEGMENT'),
                                     ('another ECAM base', nw.sett(ecam_base=0x7e000000), 'ECAM'),
                                     ('another BDF', nw.sett(bdf=0x0108), 'BDF'),
                                     ('another SMMU', nw.sett(smmu_base=0x15000000), 'SMMU_BASE'),
                                     ('another stream window', nw.sett(stream_base=0x50000), 'STREAM'),
                                     ('another namespace pin', nw.sett(eui64=EUI + 1), 'EUI64'),
                                     ('another run pin', nw.sett(generation=GENERATION + 1), 'STALE')):
            with self.subTest(name):
                self.assertEqual(verdict(edited(edit)), expected)


class Adopt(unittest.TestCase):
    """nvmeh_sp11_adopt end to end on a fake controller that treats every other partition and the GPT as untouchable."""

    def test_placed_blocks_adopt_and_the_window_is_served_inside_the_window_only(self):
        span = nw.WIN_A[1] - nw.WIN_A[0] + 1
        got = run_adopt(sp11_case(), ['r:0:8', f'r:{span - 8}:8', f'r:{span}:1', 'w:0:8', 'f'])
        self.assertEqual((got['rc'], got['prepare'], got['violations'], got['badreads'], got['final_violations']),
                         (0, 0, 0, 0, 0))
        self.assertEqual(got['ops'], [(0, 1), (0, 1), (-84, 0), (-88, 0), (0, 1)])

    def test_nothing_placed_is_refused_by_name_and_sends_nothing(self):
        got = run_adopt(zero_blocks(sp11_case()), ['r:0:8', 'w:0:8', 'f'])
        self.assertEqual((got['rc'], got['prepare']), (-145, -145))
        self.assertEqual((got['doorbells'], got['violations'], got['badreads'], got['final_violations']), (0, 0, 0, 0))
        self.assertEqual(got['ops'], [(-87, 0)] * 3)                                  # not adopted: nothing sent

    def test_one_block_placed_is_an_abi_refusal_not_a_silent_pass(self):
        only_target = copy.deepcopy(sp11_case())
        only_target['r'] = zero_blocks(only_target)['r']
        got = run_adopt(only_target, ['r:0:8'])
        self.assertEqual((got['rc'], got['prepare'], got['ops'], got['doorbells']), (nw.CODE['ABI'], nw.CODE['ABI'], [(-87, 0)], 0))
        only_record = copy.deepcopy(sp11_case())
        only_record['t'] = zero_blocks(only_record)['t']
        got = run_adopt(only_record, ['r:0:8'])
        self.assertEqual((got['rc'], got['prepare'], got['ops'], got['doorbells']), (nw.CODE['ABI'], nw.CODE['ABI'], [(-87, 0)], 0))
        one_word = zero_blocks(sp11_case())
        one_word['r']['nsze'] = 1                                                    # a single stray word is not "nothing"
        self.assertEqual(run_adopt(one_word, ['r:0:8'])['rc'], nw.CODE['ABI'])

    def test_every_contract_refusal_stores_its_code_and_sends_nothing(self):
        for name, expected, edits in Contract.CASES:
            if expected == 'OK':
                continue
            got = run_adopt(edited(*edits), ['r:0:8', 'f'])
            with self.subTest(name):
                self.assertEqual((got['rc'], got['prepare']), (nw.CODE[expected], nw.CODE[expected]))
                self.assertEqual((got['doorbells'], got['violations'], got['badreads'], got['final_violations']), (0, 0, 0, 0))
                self.assertEqual(got['ops'], [(-87, 0)] * 2)

    def stale_cases(self):
        return (('nothing placed', zero_blocks(sp11_case()), -145), ('a record of another run', edited(nw.sett(generation=GENERATION + 1)), nw.CODE['STALE']))

    def test_a_state_word_left_by_an_earlier_launch_does_not_outlive_a_refusal(self):
        """The state lives in the window reservation, which survives a warm restart and which the monitor does not clear: an ADOPTED word
        left there must neither let a refused adoption ring a doorbell nor let nvmeh_stop read a controller whose params nobody validated."""
        for name, case, code in self.stale_cases():
            for state in (0, 1, 3):                       # nothing, ADOPTED, ADOPTED and FAILED
                got = run_adopt(case, ['r:0:8', 'f', 'w:0:8'], stale=state)
                with self.subTest(name, state=state):
                    self.assertEqual((got['rc'], got['prepare']), (code, code))
                    self.assertEqual((got['doorbells'], got['violations'], got['badreads'], got['final_violations']), (0, 0, 0, 0))
                    self.assertEqual(got['ops'], [(-87, 0)] * 3)
                    self.assertEqual(got['stop_reads'], 0, 'nvmeh_stop must not read a controller that was refused')

    def test_a_stale_word_does_not_stop_a_valid_adoption_and_a_valid_adoption_is_stopped_by_reading_one_register(self):
        for state in (0, 1, 2, 3):
            got = run_adopt(sp11_case(), ['r:0:8', 'f'], stale=state)
            with self.subTest(state=state):
                self.assertEqual((got['rc'], got['prepare'], got['ops'], got['violations']), (0, 0, [(0, 1), (0, 1)], 0))
                self.assertEqual(got['stop_reads'], 1, 'an adopted controller is stopped by one read of CSTS')

    def test_the_stale_state_experiment_detects_the_missing_reset(self):
        """The control: the same driver built without the statement that clears the state word rings a doorbell and reads the controller
        after a refusal, so the experiment above is not vacuous."""
        exe = mutant_driver('    s->status = 0;\n', '')
        for name, case, code in self.stale_cases():
            got = run_adopt(case, ['r:0:8', 'f'], stale=1, driver=exe)
            with self.subTest(name):
                self.assertEqual((got['rc'], got['prepare']), (code, code))                    # the refusal itself is unchanged
                self.assertEqual(got['ops'], [(0, 1), (0, 1)])                                  # yet a read and a flush were served, each ringing a doorbell
                self.assertEqual(got['stop_reads'], 1)

    def test_the_endpoint_is_re_read_from_the_pinned_bdf_with_the_units_ids(self):
        cases = (('the endpoint as the unit read', {}, 'OK'),
                 ('a function that reads as absent', {0: 0xffffffff}, 'NO_DEVICE'),
                 ('a controller that is not NVM Express', {2: 0x01060102}, 'CLASS'),
                 ('memory space and bus master off', {1: 0x00100000}, 'DEVICE_DISABLED'),
                 ('a bridge header', {3: 0x00010000}, 'HEADER_TYPE'),
                 ('BAR0 that is not the params\'', {4: (nw.BAR + 0x100000) | 4}, 'BAR'),
                 ('an I/O-space BAR0', {4: 1}, 'BAR'))
        for name, edits, expected in cases:
            cfg = list(SP11_CFG)
            for index, value in edits.items():
                cfg[index] = value
            got = run_adopt(sp11_case(), ['r:0:8'], cfg)
            with self.subTest(name):
                if expected == 'OK':
                    self.assertEqual((got['rc'], got['ops'], got['badreads']), (0, [(0, 1)], 0))
                else:
                    self.assertEqual((got['rc'], got['prepare'], got['ops'], got['badreads']),
                                     (nw.CODE[expected], nw.CODE[expected], [(-87, 0)], 0))

    def test_the_a16_controller_re_proof_still_runs_after_the_sp11_checks(self):
        got = run_adopt(edited(nw.setp(io_phase=2)), ['r:0:8'])
        self.assertEqual((got['rc'], got['prepare'], got['ops']), (-80, -80, [(-87, 0)]))


class Reservation(unittest.TestCase):
    """What the host places is what the C code reads: the three blocks through the offsets the glue uses."""

    @staticmethod
    def words(res, offset, count):
        return list(struct.unpack_from(f'<{count}Q', res, offset))

    def place(self, case):
        res = bytearray(answindow.WINDOW_BYTES)
        r = case['r']
        record = ns.pack_record({k: r[k] for k in ns.RECORD_FIELDS}, r['ext'][:r['protected_count']])
        ns.place(res, ns.pack_target(case['t']), record)
        p = struct.pack(f'<{len(nw.PARAM_FIELDS)}Q', *[case['p'][k] for k in nw.PARAM_FIELDS])
        res[answindow.WINDOW_NVMEH:answindow.WINDOW_NVMEH + len(p)] = p
        return res

    def read_case(self, res):
        offsets = macro_values()
        t = dict(zip(ns.TARGET_FIELDS, self.words(res, offsets['target'], len(ns.TARGET_FIELDS))))
        words = self.words(res, offsets['record'], ns.RECORD_WORDS)
        r = dict(zip(ns.RECORD_FIELDS, words[:len(ns.RECORD_FIELDS)]))
        pairs = words[len(ns.RECORD_FIELDS):]
        r['ext'] = [(pairs[2 * i], pairs[2 * i + 1]) for i in range(ns.MAX_PROTECTED)]
        p = dict(zip(nw.PARAM_FIELDS, self.words(res, offsets['nvmeh'], len(nw.PARAM_FIELDS))))
        return dict(t=t, r=r, p=p)

    def test_the_blocks_read_back_at_the_glue_s_offsets_are_the_case_the_driver_accepts(self):
        case = sp11_case()
        back = self.read_case(self.place(case))
        self.assertEqual(back['t'], case['t'])
        self.assertEqual(back['p'], case['p'])
        self.assertEqual({k: v for k, v in back['r'].items() if k != 'ext'}, {k: v for k, v in case['r'].items() if k != 'ext'})
        self.assertEqual(back['r']['ext'], [tuple(e) for e in case['r']['ext']])
        self.assertEqual(verdict(back), 'OK')
        self.assertEqual(run_adopt(back, ['r:0:8'])['ops'], [(0, 1)])

    def test_a_reservation_with_nothing_placed_reads_back_as_the_unwired_status(self):
        res = bytearray(answindow.WINDOW_BYTES)
        p = struct.pack(f'<{len(nw.PARAM_FIELDS)}Q', *[sp11_case()['p'][k] for k in nw.PARAM_FIELDS])
        res[answindow.WINDOW_NVMEH:answindow.WINDOW_NVMEH + len(p)] = p
        got = run_adopt(self.read_case(res), ['r:0:8'])
        self.assertEqual((got['rc'], got['prepare'], got['ops']), (-145, -145, [(-87, 0)]))

    def test_a_block_placed_one_word_off_is_not_accepted(self):
        case = sp11_case()
        res = self.place(case)
        shifted = bytearray(res)
        shifted[ns.TARGET_OFFSET:ns.TARGET_OFFSET + ns.TARGET_BYTES] = bytes(ns.TARGET_BYTES)
        shifted[ns.TARGET_OFFSET + 8:ns.TARGET_OFFSET + 8 + ns.TARGET_BYTES] = res[ns.TARGET_OFFSET:ns.TARGET_OFFSET + ns.TARGET_BYTES]
        self.assertEqual(run_adopt(self.read_case(shifted), ['r:0:8'])['rc'], nw.CODE['ABI'])


if __name__ == '__main__':
    unittest.main()
