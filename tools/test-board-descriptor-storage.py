#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The optional `storage` section of the board descriptor (tools/board_descriptor.py, docs/SP11-NVME-WINDOW.md section 4).

  python3 tools/test-board-descriptor-storage.py [-v]

What it holds: the shipped config/boards/sp11.json states the SSD's PCIe place as expected values (the values the unit's
firmware tables and the third X2 image gave, one unit); every value is OBSERVED and sourced; nothing boot-specific is in
the file; the A16 descriptor has no storage section; and each rule of the validator has a case that breaks it (a wrong
segment, an ECAM window past the monitor's 36-bit limit, a class that is not NVM Express, a BDF without the ECAM that
locates it, an SMMU that is not an SMMUv3, a RID that has no StreamID, ...).

Synthetic edits of the shipped file only; nothing is read from hardware or from any dump. Evidence: VERIFIED on the host.
The values themselves are OBSERVED once on one unit (docs/SP11-NVME-WINDOW.md section 7.1) and are never VERIFIED here.
"""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import board_descriptor as bd  # noqa: E402

BOARDS = ROOT / 'config' / 'boards'
KEYS = bd.STORAGE_KEYS
UNKNOWN = {'source': 'UNKNOWN', 'evidence': 'UNIMPLEMENTED'}
FIXTURE = {'source': 'synthetic test fixture', 'evidence': 'EXPERIMENTAL'}


def shipped(name='sp11.json'):
    return json.loads((BOARDS / name).read_text(encoding='utf-8'))


def nvme(d):
    return d['storage']['nvme']


def S(path, value):
    """Edit: d[a][b]...[z] = value (a list is indexed by an integer part)."""
    parts = path.split('.')

    def edit(d):
        node = d
        for part in parts[:-1]:
            node = node[int(part)] if isinstance(node, list) else node[part]
        node[int(parts[-1]) if isinstance(node, list) else parts[-1]] = value
    return edit


def drop(path):
    parts = path.split('.')

    def edit(d):
        node = d
        for part in parts[:-1]:
            node = node[part]
        del node[parts[-1]]
    return edit


def setprov(claim, entry):
    return lambda d: d['provenance'].__setitem__(claim, entry)


def updprov(claim, **fields):
    return lambda d: d['provenance'][claim].update(fields)


def dropprov(*claims):
    def edit(d):
        for claim in claims:
            del d['provenance'][claim]
    return edit


def unknown(*keys):
    """Set each storage claim to null with the provenance an unknown claim must carry."""
    def edit(d):
        for key in keys:
            nvme(d)[key] = None
            d['provenance'][f'storage.nvme.{key}'] = dict(UNKNOWN)
    return edit


def chain(*edits):
    def edit(d):
        for each in edits:
            each(d)
    return edit


def edited(*edits):
    d = shipped()
    chain(*edits)(d)
    return d


def board_ready():
    """The synthetic boot-ready descriptor of tools/test-board-descriptor.py, with the shipped storage section added."""
    spec = importlib.util.spec_from_file_location('tbd_for_storage', ROOT / 'tools' / 'test-board-descriptor.py')
    tbd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tbd)
    d = tbd.synthetic()
    ship = shipped()
    d['storage'] = ship['storage']
    d['provenance'].update({k: v for k, v in ship['provenance'].items() if k.startswith('storage.')})
    return d


class Shipped(unittest.TestCase):
    def test_the_sp11_template_states_the_observed_place_of_the_ssd(self):
        b = bd.load(BOARDS / 'sp11.json')
        self.assertEqual(b.storage, bd.NvmeStorage(
            segment=6, ecam=(0x70000000, 0, 1),
            root_port=bd.PciFunction(0, 0, 0, 0x17cb, 0x0111, 0x060400),
            endpoint=bd.PciFunction(1, 0, 0, 0x144d, 0xa80b, 0x010802),
            smmu='pcie-smmu', stream=(0, 0x10000, 0x60000)))
        self.assertEqual(b.storage.endpoint.rid, 0x0100)
        self.assertEqual(b.storage.root_port.rid, 0)

    def test_the_stream_window_gives_the_endpoint_the_streamid_the_firmware_tables_declare(self):
        b = bd.load(BOARDS / 'sp11.json')
        base, count, stream = b.storage.stream
        self.assertEqual(stream + (b.storage.endpoint.rid - base), 0x60100)
        # The A16's formula, segment << 16 | RID, gives the same value for this segment.
        self.assertEqual(b.storage.segment << 16 | b.storage.endpoint.rid, 0x60100)
        self.assertEqual(dict((n, base) for n, _, base, _ in b.smmus)[b.storage.smmu], 0x15400000)

    def test_every_claim_is_observed_and_says_where_it_was(self):
        provenance = shipped()['provenance']
        for key in KEYS:
            entry = provenance[f'storage.nvme.{key}']
            with self.subTest(key):
                self.assertEqual(entry['evidence'], 'OBSERVED')
                self.assertTrue(any(w in entry['source'] for w in ('docs/SP11-NVME-WINDOW.md', 'IORT')), entry['source'])
                self.assertIn('note', entry)
        self.assertEqual(sorted(k for k in provenance if k.startswith('storage.')), sorted(f'storage.nvme.{k}' for k in KEYS))

    def test_nothing_boot_specific_is_in_the_file(self):
        text = (BOARDS / 'sp11.json').read_text(encoding='utf-8').lower()
        # BAR0, the firmware's queue pages and the BootServices extent of the observed boot (docs/SP11-NVME-WINDOW.md 7.1).
        for value in ('70300000', 'cf3e7000', 'cf3e8000', 'cf2d3000', 'cf57e000', '303c0301ff'):
            with self.subTest(value):
                self.assertNotIn(value, text)

    def test_the_a16_descriptor_has_no_storage_section(self):
        self.assertNotIn('storage', shipped('a16.json'))
        self.assertIsNone(bd.load(BOARDS / 'a16.json').storage)

    def test_the_template_is_still_not_boot_ready_for_its_own_reasons_only(self):
        with self.assertRaises(bd.BoardNotBootReady) as caught:
            bd.require_boot_ready(bd.load(BOARDS / 'sp11.json'))
        self.assertNotIn('storage', str(caught.exception))

    def test_the_cli_accepts_the_template(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(bd.main(['check', str(BOARDS / 'sp11.json'), str(BOARDS / 'a16.json')]), 0)
        self.assertIn('sp11, status UNIMPLEMENTED', out.getvalue())


class Accepted(unittest.TestCase):
    def check(self, *edits):
        return bd.validate(edited(*edits))

    def test_each_group_may_be_unknown_on_its_own(self):
        for key in ('root_port', 'smmu'):
            with self.subTest(key):
                b = self.check(unknown(key) if key != 'smmu' else chain(unknown('stream'), unknown('smmu')))
                self.assertIsNone(getattr(b.storage, key))
        self.assertIsNone(self.check(unknown('endpoint')).storage.endpoint)       # BDF absent
        self.assertIsNone(self.check(unknown('stream')).storage.stream)

    def test_everything_unknown_is_a_valid_template(self):
        b = self.check(unknown('stream', 'smmu', 'endpoint', 'root_port', 'ecam', 'segment'))
        self.assertEqual(b.storage, bd.NvmeStorage(None, None, None, None, None, None))

    def test_the_same_segment_in_pcie_ecam_may_repeat_the_window_exactly(self):
        d = edited(S('pcie.ecam', [{'segment': 6, 'base': '0x70000000', 'start_bus': 0, 'end_bus': 1}]),
                   setprov('pcie.ecam', dict(FIXTURE)))
        self.assertEqual(bd.validate(d).storage.segment, 6)

    def test_another_segment_in_pcie_ecam_is_not_the_storage_windows_business(self):
        d = edited(S('pcie.ecam', [{'segment': 3, 'base': '0x40000000', 'start_bus': 0, 'end_bus': 1}]),
                   setprov('pcie.ecam', dict(FIXTURE)))
        self.assertEqual(bd.validate(d).storage.ecam[0], 0x70000000)

    def test_the_window_may_end_exactly_at_the_36_bit_limit(self):
        b = self.check(S('storage.nvme.ecam.base', hex((1 << 36) - (2 << 20))))
        self.assertEqual(b.storage.ecam[0] + (2 << 20), 1 << 36)

    def test_windows_that_only_touch_the_ecam_window_do_not_overlap_it(self):
        for label, base in (('starting where the apps SMMU ends', 0x15100000), ('ending where the PCIe SMMU starts', 0x15200000)):
            with self.subTest(label):
                self.assertEqual(self.check(S('storage.nvme.ecam.base', hex(base))).storage.ecam[0], base)

    def test_the_last_streamid_may_be_exactly_the_32_bit_limit(self):
        b = self.check(S('storage.nvme.stream.stream_base', '0xffff0000'))               # 0xffff0000 + 0x10000 - 1 is 0xffffffff
        self.assertEqual(b.storage.stream[2] + b.storage.stream[1] - 1, 0xffffffff)

    def test_a_boot_ready_board_stays_boot_ready_with_or_without_storage_facts(self):
        d = board_ready()
        ready = bd.require_boot_ready(bd.validate(d))
        self.assertEqual(ready.storage.segment, 6)
        chain(unknown('stream', 'smmu', 'endpoint', 'root_port', 'ecam', 'segment'))(d)
        self.assertEqual(bd.require_boot_ready(bd.validate(d)).storage.endpoint, None)

    def test_the_section_is_optional(self):
        d = board_ready()
        del d['storage']
        for key in [k for k in d['provenance'] if k.startswith('storage.')]:
            del d['provenance'][key]
        self.assertIsNone(bd.require_boot_ready(bd.validate(d)).storage)


class Refused(unittest.TestCase):
    def check(self, cases):
        for label, edit, pattern in cases:
            with self.subTest(label):
                with self.assertRaisesRegex(bd.BoardDescriptorError, pattern):
                    bd.validate(edited(edit))

    def test_the_shape_of_the_section(self):
        self.check([
            ('storage as null', S('storage', None), 'null is not a value'),
            ('storage as a list', S('storage', []), 'expected an object'),
            ('storage without nvme', S('storage', {}), 'missing key'),
            ('nvme as null', S('storage.nvme', None), 'expected an object'),
            ('unknown key in storage', S('storage.extra', 1), 'unknown key'),
            ('unknown key in nvme', S('storage.nvme.extra', 1), 'unknown key'),
            ('missing endpoint key', drop('storage.nvme.endpoint'), 'missing key'),
            ('missing stream key', drop('storage.nvme.stream'), 'missing key'),
            ('unknown key in ecam', S('storage.nvme.ecam.extra', 1), 'unknown key'),
            ('unknown key in endpoint', S('storage.nvme.endpoint.extra', 1), 'unknown key'),
            ('missing key in root port', drop('storage.nvme.root_port.class_code'), 'missing key'),
            ('unknown key in stream', S('storage.nvme.stream.extra', 1), 'unknown key'),
        ])

    def test_the_segment(self):
        self.check([(f'segment {v!r}', S('storage.nvme.segment', v), 'segment')
                    for v in (-1, 0x10000, '6', True, 6.0, [6])])

    def test_the_ecam_window(self):
        self.check([
            ('base not 1 MiB aligned', S('storage.nvme.ecam.base', '0x70080000'), 'must be nonzero and 1 MiB aligned'),
            ('base zero', S('storage.nvme.ecam.base', '0x0'), 'must be nonzero and 1 MiB aligned'),
            ('base as an integer', S('storage.nvme.ecam.base', 0x70000000), 'hex string'),
            ('base at the 36-bit limit', S('storage.nvme.ecam.base', '0x1000000000'), 'beyond 1<<36'),
            ('window crosses the 36-bit limit', S('storage.nvme.ecam.base', hex((1 << 36) - (1 << 20))), 'beyond 1<<36'),
            ('base beyond 64 bits', S('storage.nvme.ecam.base', '0x10000000000000000'), 'not a 0x-prefixed|does not fit'),
            ('buses reversed', chain(S('storage.nvme.ecam.start_bus', 1), S('storage.nvme.ecam.end_bus', 0)), 'before start_bus'),
            ('end bus 256', S('storage.nvme.ecam.end_bus', 256), 'end_bus'),
            ('start bus negative', S('storage.nvme.ecam.start_bus', -1), 'start_bus'),
            ('bus as a string', S('storage.nvme.ecam.end_bus', '1'), 'end_bus'),
            ('without a segment', chain(S('storage.nvme.segment', None), setprov('storage.nvme.segment', dict(UNKNOWN))),
             'needs storage.nvme.segment'),
            ('overlapping the GIC distributor', S('storage.nvme.ecam.base', '0x17000000'), 'overlaps gic.gicd'),
            ('overlapping an SMMU', S('storage.nvme.ecam.base', '0x15400000'), 'overlaps smmu.pcie-smmu'),
            ('contradicting pcie.ecam of its segment',
             chain(S('pcie.ecam', [{'segment': 6, 'base': '0x70100000', 'start_bus': 0, 'end_bus': 1}]),
                   setprov('pcie.ecam', dict(FIXTURE))), 'contradicts pcie.ecam'),
            ('another bus range than pcie.ecam of its segment',
             chain(S('pcie.ecam', [{'segment': 6, 'base': '0x70000000', 'start_bus': 0, 'end_bus': 255}]),
                   setprov('pcie.ecam', dict(FIXTURE))), 'contradicts pcie.ecam'),
            ('overlapping another segment window',
             chain(S('pcie.ecam', [{'segment': 3, 'base': '0x6ff00000', 'start_bus': 0, 'end_bus': 3}]),
                   setprov('pcie.ecam', dict(FIXTURE))), 'overlaps pcie.ecam3'),
        ])

    def test_the_functions(self):
        self.check([
            ('endpoint bus beyond the ECAM buses', S('storage.nvme.endpoint.bus', 2), 'outside the ECAM bus range'),
            ('root port bus below the ECAM buses', S('storage.nvme.ecam.start_bus', 1), 'outside the ECAM bus range'),
            ('endpoint bus below the ECAM buses', chain(unknown('root_port'), S('storage.nvme.ecam.start_bus', 2),
                                                        S('storage.nvme.ecam.end_bus', 3)),
             'outside the ECAM bus range'),
            ('endpoint on the root port\'s own bus', S('storage.nvme.endpoint.bus', 0), 'not below the root port'),
            ('endpoint above the root port\'s bus, root port after it', S('storage.nvme.root_port.bus', 1), 'not below the root port'),
            ('device 32', S('storage.nvme.endpoint.device', 32), 'device'),
            ('function 8', S('storage.nvme.endpoint.function', 8), 'function'),
            ('bus as a bool', S('storage.nvme.endpoint.bus', True), 'bus'),
            ('class code of a SATA controller', S('storage.nvme.endpoint.class_code', '0x010601'), 'not NVM Express'),
            ('class code of a bridge for the endpoint', S('storage.nvme.endpoint.class_code', '0x060400'), 'not NVM Express'),
            ('class code zero', S('storage.nvme.endpoint.class_code', '0x0'), 'not NVM Express'),
            ('class code beyond 24 bits', S('storage.nvme.endpoint.class_code', '0x1010802'), 'does not fit'),
            ('root port class of an NVMe', S('storage.nvme.root_port.class_code', '0x010802'), 'not a PCI-to-PCI bridge'),
            ('vendor id that reads as no device', S('storage.nvme.endpoint.vendor_id', '0xffff'), 'not a vendor id'),
            ('vendor id zero', S('storage.nvme.endpoint.vendor_id', '0x0'), 'not a vendor id'),
            ('vendor id beyond 16 bits', S('storage.nvme.endpoint.vendor_id', '0x1144d'), 'does not fit'),
            ('device id beyond 16 bits', S('storage.nvme.endpoint.device_id', '0x1a80b'), 'does not fit'),
            ('device id as a number', S('storage.nvme.endpoint.device_id', 0xa80b), 'hex string'),
            ('endpoint without the ECAM that locates it',
             chain(unknown('stream'), S('storage.nvme.ecam', None), setprov('storage.nvme.ecam', dict(UNKNOWN))),
             'needs storage.nvme.ecam'),
            ('root port without the ECAM', chain(S('storage.nvme.ecam', None), setprov('storage.nvme.ecam', dict(UNKNOWN)),
                                                 unknown('endpoint', 'stream')), 'needs storage.nvme.ecam'),
        ])

    def test_the_smmu_and_the_stream_window(self):
        self.check([
            ('an SMMU that is not in the list', S('storage.nvme.smmu', 'smmu-9'), 'is not an entry of smmu'),
            ('an SMMU that is an MMU-500', S('storage.nvme.smmu', 'apps-smmu'), 'needs an SMMUv3'),
            ('an SMMU name that is not a name', S('storage.nvme.smmu', 'Pcie SMMU'), 'name must match'),
            ('an SMMU list that is unknown',
             chain(S('smmu', None), setprov('smmu', dict(UNKNOWN)), dropprov('smmu.apps-smmu', 'smmu.pcie-smmu', 'smmu.adreno-smmu')), 'smmu list is unknown'),
            ('an SMMU list that is empty',
             chain(S('smmu', []), setprov('smmu', dict(FIXTURE)), dropprov('smmu.apps-smmu', 'smmu.pcie-smmu', 'smmu.adreno-smmu')), 'is not an entry of smmu'),
            ('a stream window without an SMMU', unknown('smmu'), 'needs storage.nvme.smmu'),
            ('a RID count of zero', S('storage.nvme.stream.rid_count', '0x0'), 'empty or runs past'),
            ('a RID count beyond the RID space', S('storage.nvme.stream.rid_count', '0x10001'), 'empty or runs past'),
            ('a RID window that runs past 16 bits', S('storage.nvme.stream.rid_base', '0x1'), 'empty or runs past'),
            ('a RID base beyond 16 bits', S('storage.nvme.stream.rid_base', '0x10000'), 'does not fit'),
            ('a StreamID beyond 32 bits', S('storage.nvme.stream.stream_base', '0x100000000'), 'does not fit'),
            ('StreamIDs that run past 32 bits', chain(S('storage.nvme.stream.stream_base', '0xffff0000'),
                                                      S('storage.nvme.stream.rid_count', '0x10001')), 'empty or runs past'),
            ('StreamIDs that run past 32 bits with a legal count',
             S('storage.nvme.stream.stream_base', '0xffff8000'), 'run past 32 bits'),
            ('a window that leaves the endpoint without a StreamID',
             chain(S('storage.nvme.stream.rid_base', '0x200'), S('storage.nvme.stream.rid_count', '0x100')),
             'no StreamID'),
            ('a window that ends just before the endpoint', chain(S('storage.nvme.stream.rid_base', '0x0'),
                                                                  S('storage.nvme.stream.rid_count', '0x100')), 'no StreamID'),
        ])

    def test_the_provenance_rules_cover_the_storage_claims(self):
        self.check([
            ('a populated claim that is UNIMPLEMENTED', updprov('storage.nvme.segment', evidence='UNIMPLEMENTED'),
             'cannot be UNIMPLEMENTED'),
            ('a populated claim sourced UNKNOWN', updprov('storage.nvme.ecam', source='UNKNOWN'), 'needs a real source'),
            ('a missing claim', dropprov('storage.nvme.endpoint'), 'no entry for populated field'),
            ('an unknown claim recorded as measured',
             chain(S('storage.nvme.stream', None)), 'unknown field must be recorded'),
            ('a claim for a field the section does not have', setprov('storage.nvme.serial', dict(FIXTURE)),
             'unknown claim'),
            ('a claim for a storage section that is absent', chain(drop('storage')), 'unknown claim'),
            ('a machine path in a source', updprov('storage.nvme.segment', source='/' + 'Users' + '/x/dump.txt'), 'absolute'),
            ('an evidence label outside the set', updprov('storage.nvme.segment', evidence='MEASURED'), 'evidence'),
        ])


class Contract(unittest.TestCase):
    def test_the_validator_and_the_json_agree_on_the_set_of_storage_keys(self):
        self.assertEqual(KEYS, ('segment', 'ecam', 'root_port', 'endpoint', 'smmu', 'stream'))
        self.assertEqual(tuple(nvme(shipped())), KEYS)

    def test_a_deep_copy_of_the_shipped_descriptor_is_valid_and_unchanged(self):
        d = shipped()
        before = copy.deepcopy(d)
        bd.validate(d)
        self.assertEqual(d, before)

    def test_the_class_codes_are_the_pci_ones(self):
        self.assertEqual((bd.NVME_CLASS, bd.BRIDGE_CLASS), (0x010802, 0x0604))


if __name__ == '__main__':
    unittest.main()
