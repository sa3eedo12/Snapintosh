#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Acceptance and refusal tests for board descriptors (tools/board_descriptor.py).

Hardware is never touched. Every fixture is synthetic: `synthetic()` starts from
the structure of config/boards/sp11.json and fills each unknown with an invented
value labelled EXPERIMENTAL, so a boot-ready descriptor can be exercised without
asserting anything about a real board. The invented MIDR below is not an SP11
fact.

Two citation tests guard the shipped descriptors against drift:

* A16 values are checked against the repository lines they cite.
* SP11 values are checked against the pinned public Linux device trees
  (commit 7475ea8b58616b33c04afb976cf0fbaad9dd63fb, docs/SURFACE-PRO-11.md
  section 12). Those files are not committed; fetch them under build/refs/
  to run the check, otherwise it reports SKIP:

    https://raw.githubusercontent.com/torvalds/linux/<commit>/arch/arm64/boot/dts/qcom/<file>
    for hamoa.dtsi, x1-microsoft-denali.dtsi, x1e80100-microsoft-denali-oled.dts
"""
import contextlib
import copy
import dataclasses
import io
import json
from pathlib import Path
import re
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import board_descriptor as bd

BOARDS = ROOT / 'config' / 'boards'
REFS = ROOT / 'build' / 'refs'
LINUX_PIN = '7475ea8b58616b33c04afb976cf0fbaad9dd63fb'
UNKNOWN = {'source': 'UNKNOWN', 'evidence': 'UNIMPLEMENTED'}
FIXTURE = {'source': 'synthetic test fixture', 'evidence': 'EXPERIMENTAL'}
# Built at run time so that no file carries a literal home-directory path.
HOME_PREFIXES = ('/' + 'Users' + '/', '/' + 'home' + '/')


def raw(name):
    return json.loads((BOARDS / name).read_text(encoding='utf-8'))


def synthetic():
    """A boot-ready descriptor: SP11's structure, every unknown filled in."""
    d = raw('sp11.json')
    d['board'] = {'id': 'synthetic', 'name': 'Synthetic boot-ready board', 'status': 'EXPERIMENTAL'}
    d['soc']['midr'] = [{'value': '0x510f1230', 'mask': '0xff0ffff0'}]
    d['gic']['version'] = 4
    d['timer']['frequency_hz'] = 19200000
    d['pcie']['ecam'] = [{'segment': 0, 'base': '0x40000000', 'start_bus': 0, 'end_bus': 255}]
    d['ranges'][0].update(base='0x80000000', size='0x100000000')
    claims = ['soc.midr', 'gic.version', 'timer.frequency_hz', 'pcie.ecam', 'ranges.dram0']
    for name, state in (('uart-debug', 'absent'), ('usb-host', 'absent'), ('keyboard', 'present'),
                        ('touchpad', 'present'), ('touchscreen', 'absent'), ('nvme', 'present')):
        d['devices'][name] = {'state': state, 'mmio': False if state == 'present' else None, 'ranges': []}
        claims.append(f'devices.{name}')
    for claim in claims:
        d['provenance'][claim] = dict(FIXTURE)
    d['ranges'] = [r for r in d['ranges'] if r.get('device') != 'usb-host']
    for key in [k for k in d['provenance'] if k.startswith('ranges.usb-')]:
        del d['provenance'][key]
    return d


def board():
    return bd.validate(synthetic())


def at_limits():
    """A boot-ready descriptor with every bounded list at its maximum length.

    The last ECAM window ends exactly where DRAM begins, so adjacency is exercised too.
    """
    d = synthetic()
    d['soc']['midr'] = [{'value': f'0x510f{i:x}230', 'mask': '0xff0ffff0'} for i in range(1, 5)]
    d['clusters'].update(count=8, cores_per_cluster=4,
                         mpidrs=[f'{(cluster << 16) | (core << 8):#x}' for cluster in range(8) for core in range(4)])
    d['gic']['gicr']['size'] = '0x800000'
    d['pcie']['ecam'] = [{'segment': i, 'base': f'{0x40000000 + i * 0x10000000:#x}', 'start_bus': 0, 'end_bus': 255}
                         for i in range(4)]
    for i in range(5):
        d['smmu'].append({'name': f'extra-smmu-{i}', 'kind': 'mmu-500', 'base': f'{0x18000000 + i * 0x10000:#x}',
                          'size': '0x10000'})
        d['provenance'][f'smmu.extra-smmu-{i}'] = dict(FIXTURE)
    for i in range(28):
        add_range(f'extra-{i}', 'mmio', f'{0x20000000 + i * 0x10000:#x}', '0x1000')(d)
    return d


def _walk(node, parts):
    for key in parts:
        node = node[int(key)] if isinstance(node, list) else node[key]
    return node


def setter(path, value):
    parts = path.split('.')

    def edit(d):
        node = _walk(d, parts[:-1])
        node[int(parts[-1]) if isinstance(node, list) else parts[-1]] = value
    return edit


def dropper(path):
    parts = path.split('.')

    def edit(d):
        del _walk(d, parts[:-1])[parts[-1]]
    return edit


def chain(*edits):
    def edit(d):
        for each in edits:
            each(d)
    return edit


def add_range(name, kind, base, size, device=None):
    def edit(d):
        d['ranges'].append({'name': name, 'kind': kind, 'base': base, 'size': size, 'device': device})
        d['provenance'][f'ranges.{name}'] = dict(FIXTURE)
    return edit


def setprov(claim, **fields):
    def edit(d):
        d['provenance'][claim].update(fields)
    return edit


def dropprov(claim):
    return lambda d: d['provenance'].pop(claim)


def unknowns(*claims, drop=()):
    def edit(d):
        for claim in claims:
            d['provenance'][claim] = dict(UNKNOWN)
        for claim in drop:
            del d['provenance'][claim]
    return edit


def no_smmus(d):
    """An explicit empty SMMU list, which is a claim ("there are none") of its own."""
    d['smmu'] = []
    for name in ('apps-smmu', 'pcie-smmu', 'adreno-smmu'):
        del d['provenance'][f'smmu.{name}']
    d['provenance']['smmu'] = dict(FIXTURE)


S = setter


class Refusals(unittest.TestCase):
    def check(self, cases):
        for label, edit, pattern in cases:
            with self.subTest(label):
                d = synthetic()
                edit(d)
                with self.assertRaisesRegex(bd.BoardDescriptorError, pattern):
                    bd.validate(d)

    def test_the_control_descriptor_is_accepted(self):
        self.assertEqual(board().board_id, 'synthetic')

    def test_structure_and_version(self):
        self.check([
            ('schema version 2', S('schema_version', 2), 'schema_version'),
            ('schema version 0', S('schema_version', 0), 'schema_version'),
            ('schema version as a string', S('schema_version', '1'), 'schema_version'),
            ('schema version as a bool', S('schema_version', True), 'schema_version'),
            ('schema version as a float', S('schema_version', 1.0), 'schema_version'),
            ('unknown top-level key', S('extra', 1), 'unknown key'),
            ('missing gic section', dropper('gic'), 'missing key'),
            ('missing provenance section', dropper('provenance'), 'missing key'),
            ('unknown key in board', S('board.extra', 1), 'unknown key'),
            ('unknown key in soc', S('soc.extra', 1), 'unknown key'),
            ('unknown key in clusters', S('clusters.extra', 1), 'unknown key'),
            ('unknown key in gicd', S('gic.gicd.extra', 1), 'unknown key'),
            ('unknown key in timer', S('timer.extra', 1), 'unknown key'),
            ('unknown key in pcie', S('pcie.extra', 1), 'unknown key'),
            ('unknown key in limits', S('limits.extra', 1), 'unknown key'),
            ('unknown key in a range', S('ranges.1.extra', 1), 'unknown key'),
            ('unknown key in a device', S('devices.gpu.extra', 1), 'unknown key'),
            ('missing key in a range', dropper('ranges.1.device'), 'missing key'),
            ('board id with capitals', S('board.id', 'SP11'), 'name must match'),
            ('empty board name', S('board.name', ''), 'board.name'),
            ('board name that is not a string', S('board.name', 5), 'board.name'),
            ('board name over 100 characters', S('board.name', 'x' * 101), 'board.name'),
            ('board status outside the labels', S('board.status', 'READY'), 'board.status'),
            ('board status in lowercase', S('board.status', 'verified'), 'board.status'),
            ('limits.ipa_bits 35', S('limits.ipa_bits', 35), 'stage 2 is 36-bit'),
            ('limits.ipa_bits 48', S('limits.ipa_bits', 48), 'stage 2 is 36-bit'),
            ('limits.ipa_bits as a string', S('limits.ipa_bits', '36'), 'stage 2 is 36-bit'),
            ('limits.ipa_bits as a float', S('limits.ipa_bits', 36.0), 'stage 2 is 36-bit'),
            ('limits.ipa_bits as a bool', S('limits.ipa_bits', True), 'stage 2 is 36-bit'),
        ])

    def test_descriptor_must_be_an_object(self):
        for value in ([], None, 'sp11', 1, [synthetic()]):
            with self.subTest(value=type(value).__name__), self.assertRaisesRegex(bd.BoardDescriptorError, 'expected an object'):
                bd.validate(value)

    def test_soc_and_midr(self):
        def with_midr(value=None, mask=None):
            def edit(d):
                entry = d['soc']['midr'][0]
                entry['value'] = entry['value'] if value is None else value
                entry['mask'] = entry['mask'] if mask is None else mask
            return edit
        self.check([
            ('unknown SoC', S('soc.name', 'X1E00000'), 'unknown SoC'),
            ('SoC name as a list', S('soc.name', ['X1E80100']), 'unknown SoC'),
            ('family of another SoC', S('soc.family', 'glymur'), 'does not belong'),
            ('family without a SoC', chain(S('soc.name', None), S('soc.midr', None)), 'does not belong to SoC None'),
            ('MIDR without a SoC', chain(S('soc.name', None), S('soc.family', None)), 'meaningless without a known SoC'),
            ('value bits outside the mask', with_midr(value='0x510f1231'), 'outside mask'),
            ('mask ignores the implementer', with_midr('0x000f1230', '0x00fffff0'), 'must cover the implementer'),
            ('mask ignores the part number', with_midr('0x510f0000', '0xff0f0000'), 'must cover the implementer'),
            ('architecture nibble is not 0xf', with_midr(value='0x51051230'), 'architecture field'),
            ('implementer of another vendor', with_midr(value='0x410f1230'), 'is not the implementer known'),
            ('empty MIDR list', S('soc.midr', []), 'matches nothing'),
            ('MIDR list not a list', S('soc.midr', {}), 'expected a list'),
            ('MIDR entry not an object', S('soc.midr.0', '0x510f1230'), 'expected an object'),
            ('duplicate MIDR entries', lambda d: d['soc']['midr'].append(dict(d['soc']['midr'][0])), 'duplicate entries'),
            ('five MIDR entries', S('soc.midr', [{'value': f'0x510f{i:x}230', 'mask': '0xff0ffff0'} for i in range(1, 6)]),
             'exceed the limit of 4'),
            ('MIDR value not hex', with_midr(value='0xZZ'), 'not a 0x-prefixed hex'),
            ('MIDR value without a prefix', with_midr(value='510f1230'), 'not a 0x-prefixed hex'),
            ('MIDR value with an upper-case prefix', with_midr(value='0X510F1230'), 'not a 0x-prefixed hex'),
            ('MIDR value with no digits', with_midr(value='0x'), 'not a 0x-prefixed hex'),
            ('MIDR value negative', with_midr(value='-0x510f1230'), 'negative'),
            ('MIDR value of 33 bits', with_midr(value='0x1510f1230'), 'does not fit in 32 bits'),
            ('MIDR value as an integer', with_midr(value=0x510f1230), 'expected a 0x-prefixed hex string'),
            ('MIDR value as a float', with_midr(value=1.5), 'expected a 0x-prefixed hex string'),
            ('MIDR value as a bool', with_midr(value=True), 'expected a 0x-prefixed hex string'),
        ])

    def test_clusters(self):
        self.check([
            ('zero clusters', S('clusters.count', 0), 'outside 1..8'),
            ('nine clusters', S('clusters.count', 9), 'outside 1..8'),
            ('negative cluster count', S('clusters.count', -1), 'outside 1..8'),
            ('cluster count as a bool', S('clusters.count', True), 'expected an integer'),
            ('cluster count as a float', S('clusters.count', 3.0), 'expected an integer'),
            ('zero cores per cluster', S('clusters.cores_per_cluster', 0), 'outside 1..16'),
            ('seventeen cores per cluster', S('clusters.cores_per_cluster', 17), 'outside 1..16'),
            ('more than 32 cores in total', chain(S('clusters.count', 8), S('clusters.cores_per_cluster', 5)),
             'exceed the limit of 32'),
            ('mpidrs without a layout', S('clusters.mpidr', None), 'needs clusters.mpidr'),
            ('too few mpidrs', lambda d: d['clusters']['mpidrs'].pop(), 'expected 3x4'),
            ('too many mpidrs', lambda d: d['clusters']['mpidrs'].append('0x30000'), 'expected 3x4'),
            ('empty mpidrs', S('clusters.mpidrs', []), 'empty'),
            ('duplicate mpidr', S('clusters.mpidrs.1', '0x0'), 'duplicate MPIDR'),
            ('mpidr bits outside the affinity mask', S('clusters.mpidrs.1', '0x1000000'), 'outside the affinity mask'),
            ('mpidr value as an integer', S('clusters.mpidrs.1', 256), 'expected a 0x-prefixed hex string'),
            ('cluster mask splits the cores wrongly', S('clusters.mpidr.cluster_mask', '0xff00'), 'does not split'),
            ('cluster mask outside the affinity mask',
             chain(S('clusters.mpidr.affinity_mask', '0xffff'), S('clusters.mpidr.cluster_mask', '0xff0000')),
             'nonzero subset of the affinity mask'),
            ('zero cluster mask', S('clusters.mpidr.cluster_mask', '0x0'), 'nonzero subset'),
            ('zero affinity mask', S('clusters.mpidr.affinity_mask', '0x0'), 'affinity_mask: 0x0 must be a nonzero'),
            ('uneven clusters with the stated count', S('clusters.mpidrs.3', '0x10400'), 'does not split'),
            ('affinity mask beyond Aff3..Aff0', S('clusters.mpidr.affinity_mask', '0xffffffffff'), 'subset of 0xff00ffffff'),
            ('unknown key in the mpidr layout', S('clusters.mpidr.extra', 1), 'unknown key'),
        ])

    def test_gic(self):
        self.check([
            ('GIC version 2', S('gic.version', 2), 'outside 3..4'),
            ('GIC version 5', S('gic.version', 5), 'outside 3..4'),
            ('GIC version as a string', S('gic.version', '4'), 'expected an integer'),
            ('unaligned GICD base', S('gic.gicd.base', '0x17000100'), 'distributor frame is 64 KiB'),
            ('GICD smaller than a frame', S('gic.gicd.size', '0x1000'), 'distributor frame is 64 KiB'),
            ('zero-size GICD', S('gic.gicd.size', '0x0'), 'distributor frame is 64 KiB'),
            ('GICD size not a frame multiple', S('gic.gicd.size', '0x18000'), 'distributor frame is 64 KiB'),
            ('GICR stride of one frame', S('gic.gicr.stride', '0x10000'), 'at least 0x40000 for GICv4'),
            ('GICv4 with a GICv3 stride', S('gic.gicr.stride', '0x20000'), 'at least 0x40000 for GICv4'),
            ('GICv3 with a one-frame stride', chain(S('gic.version', 3), S('gic.gicr.stride', '0x10000')),
             'at least 0x20000'),
            ('GICR stride not a frame multiple', S('gic.gicr.stride', '0x48000'), 'multiple of 0x10000'),
            ('GICR size not a multiple of the stride', S('gic.gicr.size', '0x310000'), 'nonzero multiple of the stride'),
            ('GICR size zero', S('gic.gicr.size', '0x0'), 'nonzero multiple of the stride'),
            ('unaligned GICR base', S('gic.gicr.base', '0x17080100'), 'nonzero multiple of the stride'),
            ('fewer redistributors than cores', S('gic.gicr.size', '0x200000'), 'cannot serve 12 cores'),
            ('thirty-three redistributors', S('gic.gicr.size', '0x840000'), 'exceed the limit of 32'),
            ('ITS present as an integer', S('gic.its.present', 1), 'present must be true or false'),
            ('absent ITS carrying a base', S('gic.its', {'present': False, 'base': '0x0'}), 'unknown key'),
            ('unaligned ITS base', S('gic.its.base', '0x17040100'), 'ITS frames are 64 KiB'),
            ('zero-size ITS', S('gic.its.size', '0x0'), 'ITS frames are 64 KiB'),
            ('ITS size not a frame multiple', S('gic.its.size', '0x41000'), 'ITS frames are 64 KiB'),
            ('ITS without a size', dropper('gic.its.size'), 'missing key'),
            ('ITS as a bool', S('gic.its', True), 'expected an object'),
            ('ITS overlapping the redistributors', S('gic.its.size', '0x50000'), 'gic.its: overlaps gic.gicr'),
            ('GICD overlapping the ITS', S('gic.gicd.size', '0x50000'), 'overlaps gic.gicd'),
        ])

    def test_smmu_timer_and_pcie(self):
        def ecam(**fields):
            return lambda d: d['pcie']['ecam'][0].update(fields)
        self.check([
            ('SMMU of an unknown kind', S('smmu.0.kind', 'mmu-600'), 'unknown SMMU kind'),
            ('duplicate SMMU name', S('smmu.1.name', 'apps-smmu'), 'duplicate SMMU name'),
            ('SMMU name with capitals', S('smmu.0.name', 'AppsSMMU'), 'name must match'),
            ('nine SMMUs', lambda d: d.update(smmu=[dict(d['smmu'][0]) for _ in range(9)]), 'exceed the limit of 8'),
            ('unaligned SMMU base', S('smmu.0.base', '0x15000100'), 'multiples of'),
            ('SMMUs overlapping', S('smmu.1.base', '0x15000000'), 'smmu.pcie-smmu: overlaps smmu.apps-smmu'),
            ('timer PPI below 16', S('timer.ppi.hyp', 15), 'outside 16..31'),
            ('timer PPI above 31', S('timer.ppi.hyp', 32), 'outside 16..31'),
            ('duplicate timer INTIDs', S('timer.ppi.virt', 26), 'must be distinct'),
            ('every timer PPI null', S('timer.ppi', {k: None for k in bd.TIMER_PPI_KEYS}), 'all four entries are null'),
            ('missing timer PPI key', dropper('timer.ppi.virt'), 'missing key'),
            ('unknown timer PPI key', S('timer.ppi.extra', 1), 'unknown key'),
            ('timer frequency of 999 Hz', S('timer.frequency_hz', 999), 'outside'),
            ('timer frequency of 2 GHz', S('timer.frequency_hz', 2_000_000_000), 'outside'),
            ('timer frequency as a float', S('timer.frequency_hz', 19200000.0), 'expected an integer'),
            ('timer frequency as a string', S('timer.frequency_hz', '19200000'), 'expected an integer'),
            ('timer frequency as a bool', S('timer.frequency_hz', True), 'expected an integer'),
            ('ECAM end bus before start bus', ecam(start_bus=4, end_bus=3), 'before start_bus'),
            ('ECAM base not 1 MiB aligned', ecam(base='0x40080000'), '1 MiB aligned'),
            ('ECAM bus 256', ecam(end_bus=256), 'outside 0..255'),
            ('ECAM negative segment', ecam(segment=-1), 'outside 0..65535'),
            ('ECAM window inside DRAM', ecam(base='0x80000000'), 'DRAM against MMIO'),
            ('ECAM window ending beyond 1<<36', ecam(base='0xfff000000'), 'extends beyond'),
            ('duplicate PCI segment', lambda d: d['pcie']['ecam'].append(dict(d['pcie']['ecam'][0], base='0x50000000')),
             'duplicate PCI segment'),
            ('five ECAM windows', lambda d: d['pcie'].update(ecam=[dict(d['pcie']['ecam'][0], segment=i) for i in range(5)]),
             'exceed the limit of 4'),
        ])

    def test_ranges_and_windows(self):
        top = (1 << 36)
        self.check([
            ('MMIO overlapping MMIO', add_range('extra', 'mmio', '0x03d10000', '0x1000'), 'overlaps ranges.gpu-regs'),
            ('MMIO covering other windows', add_range('extra', 'mmio', '0x03c00000', '0x200000'), 'overlaps'),
            ('MMIO overlapping the GIC', add_range('extra', 'mmio', '0x17000000', '0x1000'), 'overlaps gic.gicd'),
            ('MMIO inside DRAM', add_range('extra', 'mmio', '0x90000000', '0x1000'), 'DRAM against MMIO'),
            ('DRAM spanning MMIO', S('ranges.0.base', '0x03000000'), 'DRAM against MMIO'),
            ('DRAM overlapping DRAM', add_range('dram1', 'dram', '0x100000000', '0x1000'), 'overlaps ranges.dram0'),
            ('window ending one page beyond 1<<36', add_range('top', 'mmio', f'{top - 0x1000:#x}', '0x2000'),
             'extends beyond'),
            ('DRAM ending one page beyond 1<<36',
             chain(S('ranges.0.base', '0xf00000000'), S('ranges.0.size', '0x100001000')), 'extends beyond'),
            ('window starting at 1<<36', add_range('top', 'mmio', f'{top:#x}', '0x1000'), 'extends beyond'),
            ('window far above 1<<36', add_range('top', 'mmio', '0xfffffffffffff000', '0x1000'), 'extends beyond'),
            ('size that would wrap a 64-bit sum', add_range('top', 'mmio', '0x1000', '0xfffffffffffff000'),
             'extends beyond'),
            ('zero-size window', add_range('zero', 'mmio', '0x20000000', '0x0'), 'zero-size'),
            ('unaligned base', add_range('odd', 'mmio', '0x20000100', '0x1000'), 'multiples of'),
            ('unaligned size', add_range('odd', 'mmio', '0x20000000', '0x1100'), 'multiples of'),
            ('negative size', add_range('neg', 'mmio', '0x20000000', '-0x1000'), 'negative'),
            ('size in decimal', add_range('dec', 'mmio', '0x20000000', '4096'), 'not a 0x-prefixed'),
            ('size with 17 digits', add_range('wide', 'mmio', '0x20000000', '0x10000000000000000'), 'at most 16 digits'),
            ('base as a float', add_range('flt', 'mmio', 536870912.0, '0x1000'), 'expected a 0x-prefixed hex string'),
            ('base as a bool', add_range('flag', 'mmio', True, '0x1000'), 'expected a 0x-prefixed hex string'),
            ('range of an unknown kind', add_range('rom', 'rom', '0x20000000', '0x1000'), 'unknown range kind'),
            ('MMIO with unknown geometry', add_range('hole', 'mmio', None, None), 'only a dram range'),
            ('DRAM with only a base', add_range('dram1', 'dram', '0x300000000', None), 'must both be a number or both null'),
            ('duplicate range name', add_range('gpu-regs', 'mmio', '0x20000000', '0x1000'), 'duplicate range name'),
            ('range name too long', add_range('a' * 16, 'mmio', '0x20000000', '0x1000'), 'name must match'),
            ('range of an unknown device', add_range('x', 'mmio', '0x20000000', '0x1000', 'modem'), 'unknown device'),
            ('DRAM owned by a device', add_range('dram1', 'dram', '0x300000000', '0x1000', 'gpu'), 'belongs to no device'),
            ('thirty-three ranges', lambda d: d.update(ranges=[dict(d['ranges'][1]) for _ in range(33)]),
             'exceed the limit of 32'),
        ])

    def test_boundary_windows_are_accepted(self):
        # Half-open windows: an end exactly at 1<<36 is valid, as in shadow/stage2.c (refused only beyond it).
        for label, base, size in (('last page, ending exactly at the limit', f'{(1 << 36) - 0x1000:#x}', '0x1000'),
                                  ('last page but one', f'{(1 << 36) - 0x2000:#x}', '0x1000'),
                                  ('first page', '0x1000', '0x1000'),
                                  ('large window under the limit', '0x400000000', '0xbffff000')):
            with self.subTest(label):
                d = synthetic()
                add_range('edge', 'mmio', base, size)(d)
                bd.validate(d)

    def test_a_dram_window_may_end_exactly_at_the_limit(self):
        # A 32 GiB part whose last bank ends on 1<<36 (docs/SURFACE-PRO-11.md E23) must be describable.
        d = synthetic()
        chain(S('ranges.0.base', '0xf00000000'), S('ranges.0.size', '0x100000000'))(d)
        self.assertEqual(max(r[2] + r[3] for r in bd.validate(d).ranges), 1 << 36)

    def test_every_limit_is_accepted_exactly(self):
        full = bd.require_boot_ready(bd.validate(at_limits()))
        self.assertEqual((len(full.midr), full.cpu_count, len(full.smmus), len(full.ecam), len(full.ranges),
                          full.gicr[2] // full.gicr[1]),
                         (bd.MAX_MIDR, bd.MAX_CPUS, bd.MAX_SMMU, bd.MAX_ECAM, bd.MAX_RANGES, bd.MAX_CPUS))
        for label, edit in (
                ('a 100-character board name', S('board.name', 'x' * 100)),
                ('a 600-character provenance source', setprov('gic.gicd', source='x' * 600)),
                ('a single-bus ECAM window', lambda d: d['pcie']['ecam'][0].update(start_bus=3, end_bus=3)),
                ('an ITS directly after the GICD', S('gic.its', {'present': True, 'base': '0x17010000', 'size': '0x10000'})),
                ('an ITS directly before the GICD', S('gic.its', {'present': True, 'base': '0x16ff0000', 'size': '0x10000'})),
                ('an empty SMMU list that claims "none"', no_smmus)):
            with self.subTest(label):
                d = synthetic()
                edit(d)
                bd.validate(d)

    def test_an_absent_its_owns_no_window(self):
        d = synthetic()
        d['gic']['its'] = {'present': False}
        absent = bd.validate(d)
        self.assertEqual(absent.its, (False, 0, 0))
        self.assertNotIn('gic.its', {w.name for w in bd.windows(absent)})

    def test_devices(self):
        def add_to_device(device, rng):
            return lambda d: d['devices'][device].update(ranges=rng)
        self.check([
            ('unknown device name', lambda d: d['devices'].update(modem={'state': 'absent', 'mmio': None, 'ranges': []}),
             'unknown device name'),
            ('missing device entry', dropper('devices.nvme'), 'no entry for'),
            ('present mmio device without a range', S('devices.gpu.ranges', []), 'has no range'),
            ('present device with mmio null', S('devices.keyboard.mmio', None), 'must state whether'),
            ('absent device keeping a range', S('devices.gpu.state', 'absent'), 'absent device must not own a range'),
            ('absent device flagged mmio', S('devices.touchscreen.mmio', True), 'absent device must not own a range'),
            ('absent device keeping a range, mmio unknown', chain(S('devices.gpu.state', 'absent'), S('devices.gpu.mmio', None)),
             'absent device must not own a range'),
            ('range naming an absent device', add_range('ts-regs', 'mmio', '0x20000000', '0x1000', 'touchscreen'),
             'which is absent'),
            ('range naming a device that does not list it', add_range('wifi-regs', 'mmio', '0x20000000', '0x1000', 'wifi'),
             'does not list this range'),
            ("device listing another device's range", add_to_device('display', ['mdss-top', 'gpu-regs']),
             'which belongs to'),
            ('mmio=false device listing a range',
             chain(add_range('wifi-regs', 'mmio', '0x20000000', '0x1000', 'wifi'), add_to_device('wifi', ['wifi-regs'])),
             'must not list ranges'),
            ('device state outside the vocabulary', S('devices.gpu.state', 'enabled'), 'state must be one of'),
            ('mmio as a string', S('devices.gpu.mmio', 'yes'), 'mmio must be'),
            ('mmio as an integer', S('devices.gpu.mmio', 1), 'mmio must be'),
            ('duplicate range reference', add_to_device('gpu', ['gpu-regs', 'gpu-regs']), 'duplicate range reference'),
            ('range reference that is not defined', add_to_device('gpu', ['gpu-regs', 'nothing']), 'that are not defined'),
            ('range reference that is not a name', add_to_device('gpu', [1]), 'name must match'),
            ('ranges as a string', add_to_device('gpu', 'gpu-regs'), 'expected a list'),
            ('devices as a list', S('devices', []), 'expected an object'),
            ('device entry as a string', S('devices.gpu', 'present'), 'expected an object'),
        ])

    def test_provenance(self):
        self.check([
            ('no entry for a populated field', dropprov('gic.gicd'), 'no entry for populated field'),
            ('empty SMMU list without its claim', chain(no_smmus, dropprov('smmu')), 'no entry for populated field'),
            ('no entry for an unknown field', chain(S('soc.midr', None), dropprov('soc.midr')), 'no entry for unknown field'),
            ('entry for a claim that does not exist', lambda d: d['provenance'].update({'soc.nonsense': dict(FIXTURE)}),
             'unknown claim'),
            ('evidence outside the five labels', setprov('gic.gicd', evidence='CERTAIN'), 'is not one of'),
            ('evidence label in lowercase', setprov('gic.gicd', evidence='verified'), 'is not one of'),
            ('evidence as an integer', setprov('gic.gicd', evidence=1), 'is not one of'),
            ('populated field sourced UNKNOWN', setprov('gic.gicd', source='UNKNOWN'), 'real source'),
            ('populated field sourced "unknown"', setprov('gic.gicd', source=' unknown '), 'real source'),
            ('populated field marked UNIMPLEMENTED', setprov('gic.gicd', evidence='UNIMPLEMENTED'), 'cannot be UNIMPLEMENTED'),
            ('unknown field with a real source',
             chain(S('soc.midr', None), setprov('soc.midr', source='somewhere', evidence='UNIMPLEMENTED')),
             'must be recorded as source "UNKNOWN"'),
            ('unknown field claiming evidence',
             chain(S('soc.midr', None), setprov('soc.midr', source='UNKNOWN', evidence='INFERRED')),
             'must be recorded as source "UNKNOWN"'),
            ('VERIFIED without a source', setprov('gic.gicd', source='', evidence='VERIFIED'), 'non-empty string'),
            ('blank source', setprov('gic.gicd', source='   '), 'non-empty string'),
            ('source that is not a string', setprov('gic.gicd', source=None), 'non-empty string'),
            ('source with a newline', setprov('gic.gicd', source='a\nb'), 'control characters'),
            ('source with a tab', setprov('gic.gicd', source='a\tb'), 'control characters'),
            ('source naming a macOS home directory', setprov('gic.gicd', source=HOME_PREFIXES[0] + 'someone/notes.txt'),
             'absolute or machine-specific path'),
            ('source naming a Linux home directory', setprov('gic.gicd', source='see ' + HOME_PREFIXES[1] + 'someone/notes'),
             'absolute or machine-specific path'),
            ('source starting with a tilde', setprov('gic.gicd', source='~/notes'), 'absolute or machine-specific path'),
            ('source naming a drive path', setprov('gic.gicd', source='C:\\Users\\someone\\notes'),
             'absolute or machine-specific path'),
            ('source over 600 characters', setprov('gic.gicd', source='x' * 601), 'at most 600'),
            ('note that is not a string', setprov('gic.gicd', note=5), 'non-empty string'),
            ('note with a control character', setprov('gic.gicd', note='a\x00b'), 'control characters'),
            ('unknown field in a provenance entry', setprov('gic.gicd', confidence='high'), 'unknown key'),
            ('provenance entry without evidence', lambda d: d['provenance']['gic.gicd'].pop('evidence'), 'missing key'),
            ('provenance entry that is not an object', lambda d: d['provenance'].update({'gic.gicd': 'INFERRED'}),
             'expected an object'),
            ('provenance as a list', S('provenance', []), 'expected an object'),
        ])


class JsonText(unittest.TestCase):
    def test_refused_text(self):
        text = json.dumps(synthetic())
        duplicate_device = text.replace('"keyboard": {', '"keyboard": {"state": "absent", "mmio": null, "ranges": []}, '
                                        '"keyboard": {', 1)
        self.assertNotEqual(duplicate_device, text)
        # Whether the interpreter reports absurd nesting as RecursionError or parses it
        # depends on the build, so the contract is only "a refusal, never a crash".
        deep_array = '[' * 100000 + ']' * 100000
        deep_object = '{"board":' * 100000 + '1' + '}' * 100000
        for label, body, pattern in (
                ('duplicate top-level key', '{"schema_version": 1, "schema_version": 1}', 'duplicate JSON key'),
                ('duplicate device name', duplicate_device, 'duplicate JSON key'),
                ('NaN', text.replace('19200000', 'NaN'), 'non-finite'),
                ('Infinity', text.replace('19200000', 'Infinity'), 'non-finite'),
                ('-Infinity', text.replace('19200000', '-Infinity'), 'non-finite'),
                ('truncated JSON', text[:200], 'invalid JSON'),
                ('empty text', '', 'invalid JSON'),
                ('top-level array', '[]', 'expected an object'),
                ('top-level string', '"x"', 'expected an object'),
                ('absurdly nested arrays', deep_array, 'invalid JSON|expected an object'),
                ('absurdly nested objects', deep_object,
                 'invalid JSON|duplicate JSON key|expected an object|unknown key|missing key'),
                # An interpreter without the integer digit limit parses this number and refuses the
                # incomplete object instead; either way it is a refusal.
                ('integer beyond the digit limit', '{"schema_version": ' + '9' * 5000 + '}',
                 'invalid JSON|missing key')):
            with self.subTest(label), self.assertRaisesRegex(bd.BoardDescriptorError, pattern):
                bd.loads(body)

    def test_recursion_failure_is_reported_as_a_refusal(self):
        with mock.patch.object(bd.json, 'loads', side_effect=RecursionError('simulated')):
            with self.assertRaisesRegex(bd.BoardDescriptorError, 'invalid JSON: RecursionError'):
                bd.loads('{}')

    def test_valid_text_round_trips(self):
        self.assertEqual(bd.loads(json.dumps(synthetic())), board())

    def test_unreadable_files_are_refused(self):
        with self.assertRaisesRegex(bd.BoardDescriptorError, 'cannot read'):
            bd.load(BOARDS / 'no-such-board.json')
        with self.assertRaisesRegex(bd.BoardDescriptorError, 'cannot read'):
            bd.load(BOARDS)


class BootReadiness(unittest.TestCase):
    def refused(self, edit, *needles):
        d = synthetic()
        edit(d)
        candidate = bd.validate(d)      # a template is structurally sound ...
        with self.assertRaises(bd.BoardNotBootReady) as caught:
            bd.require_boot_ready(candidate)    # ... and still cannot describe a boot
        for needle in needles:
            self.assertIn(needle, str(caught.exception))
        return str(caught.exception)

    def test_synthetic_descriptor_is_boot_ready(self):
        ready = board()
        self.assertIs(bd.require_boot_ready(ready), ready)

    def test_each_required_field_is_individually_required(self):
        every_range = ['ranges.dram0', 'ranges.gpu-regs', 'ranges.gpu-gmu', 'ranges.mdss-top']
        every_smmu = ['smmu.apps-smmu', 'smmu.pcie-smmu', 'smmu.adreno-smmu']

        def drop_ranges(d):
            d['ranges'] = None
            for name in ('gpu', 'gpu-gmu', 'display'):
                d['devices'][name].update(mmio=False, ranges=[])
        cases = (
            ('soc.midr', chain(S('soc.midr', None), unknowns('soc.midr'))),
            ('soc.name', chain(S('soc.name', None), S('soc.family', None), S('soc.midr', None),
                               unknowns('soc.name', 'soc.family', 'soc.midr'))),
            ('clusters.count', chain(S('clusters.count', None), unknowns('clusters.count'))),
            ('clusters.cores_per_cluster', chain(S('clusters.cores_per_cluster', None), unknowns('clusters.cores_per_cluster'))),
            ('clusters.mpidr', chain(S('clusters.mpidr', None), S('clusters.mpidrs', None),
                                     unknowns('clusters.mpidr', 'clusters.mpidrs'))),
            ('clusters.mpidrs', chain(S('clusters.mpidrs', None), unknowns('clusters.mpidrs'))),
            ('gic.version', chain(S('gic.version', None), unknowns('gic.version'))),
            ('gic.gicd', chain(S('gic.gicd', None), unknowns('gic.gicd'))),
            ('gic.gicr', chain(S('gic.gicr', None), unknowns('gic.gicr'))),
            ('gic.its', chain(S('gic.its', None), unknowns('gic.its'))),
            ('timer.frequency_hz', chain(S('timer.frequency_hz', None), unknowns('timer.frequency_hz'))),
            ('timer.ppi', chain(S('timer.ppi', None), unknowns('timer.ppi'))),
            ('smmu', chain(S('smmu', None), unknowns('smmu', drop=every_smmu))),
            ('pcie.ecam', chain(S('pcie.ecam', None), unknowns('pcie.ecam'))),
            ('limits.ipa_bits', chain(S('limits.ipa_bits', None), unknowns('limits.ipa_bits'))),
            ('ranges', chain(drop_ranges, unknowns('ranges', drop=every_range))),
        )
        for field, edit in cases:
            with self.subTest(field):
                message = self.refused(edit, f'{field} is unknown')
                if field == 'ranges':       # an unknown list is not also "a list without DRAM"
                    self.assertNotIn('DRAM', message)

    def test_other_ways_to_be_unready(self):
        self.refused(S('board.status', 'UNIMPLEMENTED'), 'board.status is UNIMPLEMENTED')
        self.refused(S('timer.ppi.hyp', None), 'timer.ppi has an unrecorded interrupt')
        self.refused(chain(lambda d: d['ranges'].pop(0), dropprov('ranges.dram0')), 'no DRAM window with known geometry')
        self.refused(chain(S('ranges.0.base', None), S('ranges.0.size', None), unknowns('ranges.dram0')),
                     'ranges.dram0 geometry is unknown')
        self.refused(chain(S('devices.nvme', {'state': 'unknown', 'mmio': None, 'ranges': []}), unknowns('devices.nvme')),
                     "device state unknown for ['nvme']")

    def test_unimplemented_provenance_is_a_last_line_of_defence(self):
        # validate() cannot produce this state, so build it directly: a Board whose
        # provenance admits something the field checks above did not notice.
        odd = dataclasses.replace(board(), provenance={'soc.midr': ('UNKNOWN', 'UNIMPLEMENTED')})
        with self.assertRaisesRegex(bd.BoardNotBootReady, 'provenance still UNIMPLEMENTED'):
            bd.require_boot_ready(odd)

    def test_the_provenance_backstop_stays_quiet_when_a_field_check_already_refused(self):
        odd = dataclasses.replace(board(), status='UNIMPLEMENTED', provenance={'soc.midr': ('UNKNOWN', 'UNIMPLEMENTED')})
        with self.assertRaises(bd.BoardNotBootReady) as caught:
            bd.require_boot_ready(odd)
        self.assertIn('board.status is UNIMPLEMENTED', str(caught.exception))
        self.assertNotIn('provenance still', str(caught.exception))

    def test_a_cpu_count_needs_both_factors(self):
        self.assertEqual(board().cpu_count, 12)
        for field in ('cluster_count', 'cores_per_cluster'):
            with self.subTest(field):
                self.assertIsNone(dataclasses.replace(board(), **{field: None}).cpu_count)

    def test_not_boot_ready_is_a_descriptor_error(self):
        self.assertTrue(issubclass(bd.BoardNotBootReady, bd.BoardDescriptorError))

    def test_withdrawn_and_unknown_devices(self):
        d = synthetic()
        expected = ('touchscreen', 'uart-debug', 'usb-host')
        self.assertEqual(bd.withdrawn_devices(board()), expected)
        self.assertEqual(bd.withdrawn_devices(d), expected)
        self.assertEqual(bd.unknown_devices(board()), ())
        d['schema_version'] = 2
        with self.assertRaises(bd.BoardDescriptorError):
            bd.withdrawn_devices(d)        # an unvalidated dict must not silently yield ()
        sp11 = bd.load(BOARDS / 'sp11.json')
        self.assertEqual(bd.withdrawn_devices(sp11), ())
        self.assertEqual(bd.unknown_devices(sp11),
                         ('keyboard', 'nvme', 'touchpad', 'touchscreen', 'uart-debug'))

    def test_windows_cover_every_claimed_region(self):
        names = {w.name for w in bd.windows(board())}
        self.assertEqual(names, {'gic.gicd', 'gic.gicr', 'gic.its', 'smmu.apps-smmu', 'smmu.pcie-smmu', 'smmu.adreno-smmu',
                                 'pcie.ecam0', 'ranges.dram0', 'ranges.gpu-regs', 'ranges.gpu-gmu', 'ranges.mdss-top'})
        ecam = next(w for w in bd.windows(board()) if w.name == 'pcie.ecam0')
        self.assertEqual((ecam.base, ecam.size), (0x40000000, 256 << 20))


class ShippedDescriptors(unittest.TestCase):
    def test_both_validate(self):
        for name in ('a16.json', 'sp11.json'):
            with self.subTest(name):
                bd.load(BOARDS / name)

    def test_vocabulary_is_pinned_for_the_blob(self):
        # The index in DEVICE_VOCABULARY is a bit position in compat/board-info.h.
        self.assertEqual(bd.DEVICE_VOCABULARY, ('gpu', 'gpu-gmu', 'display', 'uart-debug', 'usb-host',
                                                'keyboard', 'touchpad', 'touchscreen', 'wifi', 'nvme'))
        self.assertEqual(bd.SOC_IDS, {'X1E80100': 1, 'X2E-96-100': 2})
        self.assertEqual(set(bd.SOC_IDS), set(bd.KNOWN_SOCS))
        self.assertEqual((bd.MAX_MIDR, bd.MAX_CPUS, bd.MAX_SMMU, bd.MAX_ECAM, bd.MAX_RANGES), (4, 32, 8, 4, 32))

    def test_sp11_is_a_template_that_fails_closed(self):
        sp11 = bd.load(BOARDS / 'sp11.json')
        self.assertEqual(sp11.status, 'UNIMPLEMENTED')
        self.assertIsNone(sp11.midr)
        self.assertIsNone(sp11.gic_version)
        self.assertIsNone(sp11.timer_hz)
        self.assertIsNone(sp11.ecam)
        dram = [r for r in sp11.ranges if r[1] == 'dram']
        self.assertTrue(dram and all(r[2] is None and r[3] is None for r in dram))
        with self.assertRaises(bd.BoardNotBootReady) as caught:
            bd.require_boot_ready(sp11)
        for needle in ('UNIMPLEMENTED', 'soc.midr', 'gic.version', 'timer.frequency_hz', 'pcie.ecam', 'DRAM'):
            self.assertIn(needle, str(caught.exception))

    def test_a16_is_not_boot_ready_either(self):
        a16 = bd.load(BOARDS / 'a16.json')
        self.assertEqual(a16.status, 'INFERRED')
        self.assertEqual(a16.midr, ((0x512f0021, 0xffffffff),))
        with self.assertRaises(bd.BoardNotBootReady) as caught:
            bd.require_boot_ready(a16)
        for needle in ('clusters.count', 'gic.its', 'smmu', 'pcie.ecam', 'no DRAM window', 'device state unknown'):
            self.assertIn(needle, str(caught.exception))

    def test_verified_is_reserved_for_the_monitor_geometry(self):
        # VERIFIED is claimed only where a value is read from this repository's source;
        # every value taken from a public device tree or an older note stays INFERRED/OBSERVED.
        for name in ('a16.json', 'sp11.json'):
            with self.subTest(name):
                verified = sorted(k for k, v in raw(name)['provenance'].items() if v['evidence'] == 'VERIFIED')
                self.assertEqual(verified, ['limits.ipa_bits'])

    def test_sp11_values_cite_the_pinned_tree_or_the_monitor(self):
        sp11 = raw('sp11.json')['provenance']
        for claim, entry in sp11.items():
            if entry['evidence'] == 'UNIMPLEMENTED' or claim == 'limits.ipa_bits':
                continue
            with self.subTest(claim):
                self.assertIn(LINUX_PIN, entry['source'])
                self.assertEqual(entry['evidence'], 'INFERRED')

    def test_no_machine_identifying_paths(self):
        for path in (*BOARDS.glob('*.json'), ROOT / 'tools' / 'board_descriptor.py'):
            text = path.read_text(encoding='utf-8')
            for prefix in HOME_PREFIXES:
                with self.subTest(path=path.name, prefix=prefix[:3]):
                    self.assertNotIn(prefix, text)

    def test_cli(self):
        a16, sp11 = str(BOARDS / 'a16.json'), str(BOARDS / 'sp11.json')

        def run(*argv):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = bd.main(list(argv))
            return code, out.getvalue(), err.getvalue()
        code, out, _ = run('check', a16, sp11)
        self.assertEqual(code, 0)
        self.assertIn('a16, status INFERRED', out)
        self.assertIn('sp11, status UNIMPLEMENTED', out)
        code, out, _ = run('check', a16, sp11, '--boot-ready')
        self.assertEqual(code, 1)
        self.assertEqual(out.count('REFUSED'), 2)
        code, out, _ = run('check', str(BOARDS / 'missing.json'))
        self.assertEqual((code, 'REFUSED: cannot read' in out), (1, True))
        for argv in ((), ('check',), ('verify', a16), ('check', a16, '--bogus')):
            with self.subTest(argv=argv):
                self.assertEqual(run(*argv)[0], 2)


def _cited(relpath, first, last, base=ROOT):
    lines = (base / relpath).read_text(encoding='utf-8').splitlines()
    return '\n'.join(lines[first - 1:last]).lower()


def _hex(value, width=0):
    return f'0x{value:0{width}x}'


# (claim, repo file, first line, last line, needle derived from the descriptor). The
# needle is computed from the loaded descriptor, so editing either the descriptor or
# the cited source without the other fails here.
A16_CITATIONS = (
    ('board', 'AGENTS.md', 20, 21, lambda b: b.display_name),
    ('board', 'README.md', 5, 6, lambda b: 'ASUS Zenbook A16'),
    ('soc.name', 'AGENTS.md', 20, 21, lambda b: b.soc_name),
    ('soc.name', 'README.md', 5, 6, lambda b: b.soc_name),
    ('soc.family', 'tools/a16_thermal.py', 16, 16, lambda b: b.soc_family),
    ('soc.midr', 'docs/FEASIBILITY.md', 94, 94, lambda b: _hex(b.midr[0][0])),
    ('clusters.mpidr', 'compat/boot-relocate.h', 82, 82, lambda b: _hex(b.affinity_mask)),
    ('clusters.mpidr', 'compat/boot-relocate.h', 83, 83, lambda b: _hex(b.cluster_mask)),
    ('gic.version', 'tools/probe-a16-secondary.py', 52, 52, lambda b: f'version={b.gic_version}'),
    ('gic.version', 'tools/probe-a16-secondary.py', 53, 53, lambda b: f'GICv{b.gic_version}'),
    ('gic.version', 'tools/probe-a16-secondary.py', 136, 137, lambda b: f'GICv{b.gic_version}'),
    ('gic.gicd', 'probes/secondary.c', 26, 26, lambda b: _hex(b.gicd[0])),
    ('gic.gicd', 'tools/a16_thermal.py', 18, 19, lambda b: _hex(b.gicd[0])),
    ('gic.gicr', 'compat/boot-relocate.h', 84, 86, lambda b: _hex(b.gicr[0])),
    ('gic.gicr', 'compat/boot-relocate.h', 84, 86, lambda b: _hex(b.gicr[0] + b.gicr[2])),
    ('gic.gicr', 'compat/boot-relocate.h', 84, 86, lambda b: _hex(b.gicr[1])),
    ('gic.gicr', 'probes/secondary.c', 27, 29, lambda b: _hex(b.gicr[0])),
    ('gic.gicr', 'probes/secondary.c', 27, 29, lambda b: _hex(b.gicr[0] + b.gicr[2])),
    ('gic.gicr', 'probes/secondary.c', 27, 29, lambda b: _hex(b.gicr[1])),
    ('gic.gicr', 'probes/xnuboot/cpu_boot.c', 45, 45, lambda b: _hex(b.gicr[0] + b.gicr[2])),
    ('timer.frequency_hz', 'tools/a16_thermal.py', 18, 18, lambda b: str(b.timer_hz)),
    ('timer.frequency_hz', 'probes/xnuboot.py', 147, 147, lambda b: str(b.timer_hz)),
    ('timer.frequency_hz', 'probes/xnuboot/prepare.c', 93, 93, lambda b: str(b.timer_hz)),
    ('timer.ppi', 'probes/secondary.c', 30, 30, lambda b: f'SNAP_TIMER_INTID {b.timer_ppi[3]}'),
    ('timer.ppi', 'compat/fast-timer-irq.h', 2, 2, lambda b: f'PPI {b.timer_ppi[3]}'),
    ('limits.ipa_bits', 'probes/xnuboot/stage2.c', 20, 20, lambda b: f'S2_TXSZ    {64 - b.ipa_bits}u'),
    ('ranges.adreno', 'compat/adreno.h', 38, 38, lambda b: _hex(_range(b, 'adreno')[0], 8)),
    ('ranges.adreno', 'compat/adreno.h', 44, 44, lambda b: _hex(sum(_range(b, 'adreno')), 8)),
    ('ranges.i2c-keyboard', 'compat/i2c-input.h', 32, 34, lambda b: _hex(_range(b, 'i2c-keyboard')[0], 8)),
    ('ranges.i2c-keyboard', 'compat/i2c-input.h', 32, 34, lambda b: _hex(_range(b, 'i2c-keyboard')[1])),
    ('ranges.i2c-touchpad', 'compat/i2c-input.h', 33, 34, lambda b: _hex(_range(b, 'i2c-touchpad')[0], 8)),
    ('ranges.qcc2072-bar0', 'compat/qcc2072-wifi.h', 55, 56, lambda b: _hex(_range(b, 'qcc2072-bar0')[0])),
    ('ranges.qcc2072-bar0', 'compat/qcc2072-wifi.h', 55, 56, lambda b: _hex(_range(b, 'qcc2072-bar0')[1])),
    ('ranges.qcc2072-bar2', 'compat/qcc2072-wifi.h', 57, 58, lambda b: _hex(_range(b, 'qcc2072-bar2')[0])),
    ('ranges.qcc2072-bar2', 'compat/qcc2072-wifi.h', 57, 58, lambda b: _hex(_range(b, 'qcc2072-bar2')[1])),
)


def _range(b, name):
    entry = next(r for r in b.ranges if r[0] == name)
    return entry[2], entry[3]


class Citations(unittest.TestCase):
    def test_a16_values_match_the_repository_lines_they_cite(self):
        a16 = bd.load(BOARDS / 'a16.json')
        for claim, relpath, first, last, want in A16_CITATIONS:
            needle = want(a16)
            with self.subTest(claim=claim, cited=f'{relpath}:{first}-{last}', needle=needle):
                self.assertIn(needle.lower(), _cited(relpath, first, last))
                self.assertIn(relpath, a16.provenance[claim][0])

    @unittest.skipUnless((REFS / 'hamoa.dtsi').is_file(), 'pinned device trees not fetched under build/refs/')
    def test_sp11_values_match_the_pinned_device_trees(self):
        sp11 = bd.load(BOARDS / 'sp11.json')
        provenance = raw('sp11.json')['provenance']
        hamoa, denali, oled = 'hamoa.dtsi', 'x1-microsoft-denali.dtsi', 'x1e80100-microsoft-denali-oled.dts'

        def reg(base, size):
            # <hi lo hi lo> pairs appear as "0 0x15000000 0 0x100000" or "0x0 0x03da0000 0x0 0x40000".
            return rf'0x0*{base:x}\s+0(x0)?\s+0x0*{size:x}\b'

        def has(name, first, last, pattern, claim, field='source'):
            with self.subTest(claim=claim, cited=f'{name}:{first}-{last}', pattern=pattern):
                self.assertRegex(_cited(name, first, last, REFS), pattern.lower())
                # A claim that stays unknown keeps its explanation in the note; its source is UNKNOWN.
                self.assertIn(f'{name}:{first}' if field == 'note' else name, provenance[claim][field])

        has(oled, 12, 14, 'surface pro 11th edition', 'board')
        has(oled, 13, 14, re.escape('qcom,' + sp11.soc_name.lower()), 'soc.name')
        for line, mpidr in zip((74, 91, 102, 113, 124, 141, 152, 163, 174, 191, 202, 213), sp11.mpidrs):
            has(hamoa, line, line, re.escape(f'reg = <0x0 {mpidr:#x}>'), 'clusters.mpidrs')
        for i in range(sp11.cluster_count):
            has(hamoa, 221, 275, rf'cluster{i}\b', 'clusters.count')
        for i in range(sp11.cores_per_cluster):
            has(hamoa, 221, 275, rf'core{i}\b', 'clusters.cores_per_cluster')
        has(hamoa, 8559, 8559, 'arm,gic-v3"', 'gic.version', field='note')
        has(hamoa, 8558, 8561, reg(*sp11.gicd), 'gic.gicd')
        has(hamoa, 8558, 8561, reg(sp11.gicr[0], sp11.gicr[2]), 'gic.gicr')
        has(hamoa, 8561, 8561, rf'gicr \* {sp11.gicr[2] // sp11.gicr[1]}\b', 'gic.gicr')
        has(hamoa, 8568, 8569, re.escape(f'redistributor-stride = <0x0 {sp11.gicr[1]:#x}>'), 'gic.gicr')
        has(hamoa, 8575, 8581, reg(sp11.its[1], sp11.its[2]), 'gic.its')
        has(hamoa, 8575, 8581, 'arm,gic-v3-its', 'gic.its')
        for name, kind, base, size in sp11.smmus:
            first, last = {'apps-smmu': (8436, 8438), 'pcie-smmu': (8544, 8546), 'adreno-smmu': (4236, 4239)}[name]
            has(hamoa, first, last, reg(base, size), f'smmu.{name}')
            has(hamoa, first, last, re.escape('arm,mmu-500' if kind == 'mmu-500' else 'arm,smmu-v3'), f'smmu.{name}')
        for line, intid in zip((9117, 9118, 9119, 9120), sp11.timer_ppi):
            has(hamoa, line, line, rf'gic_ppi {intid - 16}\b', 'timer.ppi')
        for name, (first, last) in {'gpu-regs': (4014, 4016), 'gpu-gmu': (4173, 4175), 'mdss-top': (5553, 5555)}.items():
            has(hamoa, first, last, reg(*_range(sp11, name)), f'ranges.{name}')
        has(denali, 769, 771, r'&gpu \{\s+status = "okay"', 'devices.gpu')
        has(denali, 923, 925, r'&mdss \{\s+status = "okay"', 'devices.display')
        has(denali, 998, 1001, 'pci17cb,1107', 'devices.wifi')
        has(hamoa, 4173, 4175, 'gmu@3d6a000', 'devices.gpu-gmu')


if __name__ == '__main__':
    unittest.main()
