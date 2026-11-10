#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Edge tests for the Hamoa boot-contract adapter: probes/bootcontract.py and tools/check-hamoa-acpi.py.

tools/test-hamoa-bootcontract.py covers the refusal families. This file pins the exact edge of each numeric limit
(the largest value accepted and the smallest refused), the choice of run directory, and the adapter's lazy import of
the descriptor module. It builds its tables with that file's synthetic fixtures; nothing touches hardware.

    python3 tools/test-hamoa-bootcontract-bounds.py

Evidence: EXPERIMENTAL. Agreement with synthetic tables says nothing about what any firmware really emits.
"""
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'probes'))
sys.path.insert(0, str(ROOT / 'tools'))
import bootcontract as bc

BUILD = ROOT / 'build' / 'test-hamoa-bootcontract-bounds'
HOME_PREFIXES = ('/' + 'Users' + '/', '/' + 'home' + '/')
CAP = bc.ACPI_MAX_TABLE_BYTES


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fx = _load('hamoa_fixtures', 'test-hamoa-bootcontract.py')


def windows(count):
    """`count` ECAM allocations, one segment each, that touch in memory without overlapping."""
    return [(i, 0x40000000 + i * 0x10000000, 0, 255) for i in range(count)]


class TableLimits(fx.Base):
    def test_the_documented_limits(self):
        self.assertEqual((CAP, bc.MADT_MAX_ENTRIES, bc.MCFG_MAX_ALLOCATIONS), (1 << 20, 4096, 16))

    def test_a_table_may_be_exactly_the_cap_and_no_longer(self):
        body = CAP - bc.ACPI_HEADER_BYTES
        self.refused('table-structure', fx.good(XSDT=fx.sdt(b'XSDT', bytes(body))), reason='multiple of 8')
        self.refused('table-length', fx.good(XSDT=fx.sdt(b'XSDT', bytes(body + 1))), reason='outside')

    def test_a_table_may_be_just_its_header(self):
        self.refused('table-structure', fx.good(XSDT=fx.sdt(b'XSDT', b'')), reason='lists 0 tables')

    def test_an_rsdp_may_be_exactly_the_cap_and_no_longer(self):
        def padded(size):
            return fx.fix_rsdp(fx.rsdp(length=size) + bytes(size - bc.RSDP_V2_BYTES))
        self.accepted(fx.good(RSDP=padded(CAP)))
        self.refused('table-length', fx.good(RSDP=padded(CAP + 1)))


class XsdtEntries(fx.Base):
    def test_the_entry_area_must_hold_whole_entries(self):
        raw = fx.sdt(b'XSDT', struct.pack('<4Q', 0x2000, 0x3000, 0x4000, 0x5000) + bytes(4))
        self.refused('table-structure', fx.good(XSDT=raw), reason='multiple of 8')

    def test_two_entries_are_the_fewest_and_one_is_refused(self):
        self.accepted(fx.good(XSDT=fx.xsdt((0x2000, 0x3000))))
        self.refused('table-structure', fx.good(XSDT=fx.xsdt((0x2000,))), reason='at least')


class MadtEdges(fx.Base):
    def test_a_madt_needs_its_fixed_part_and_nothing_more(self):
        self.refused('table-length', fx.good(APIC=fx.sdt(b'APIC', bytes(4))), reason='fixed part')
        facts = bc.parse_hamoa_tables(fx.good(APIC=fx.madt([])))
        self.assertEqual([len(group) for group in (facts.gicc, facts.gicd, facts.gicr, facts.its)], [0, 0, 0, 0])

    def test_a_redistributor_range_may_end_at_the_top_of_the_address_space_but_not_wrap(self):
        top = (1 << 64) - fx.STRIDE
        facts = bc.parse_hamoa_tables(fx.good(fx.baseline_entries(gicr_ranges=((top, fx.STRIDE),))))
        self.assertEqual(facts.gicr, (bc.Gicr(top, fx.STRIDE),))
        with self.assertRaisesRegex(bc.BootContractError, 'wraps'):
            bc.parse_hamoa_tables(fx.good(fx.baseline_entries(gicr_ranges=((top, fx.STRIDE + 1),))))

    def test_an_empty_redistributor_range_is_refused_even_beside_a_correct_one(self):
        # Placed after the real range it changes neither the first base nor the total, so only its own length gives it away.
        empty = fx.baseline_entries(gicr_ranges=((fx.GICR_BASE, fx.GICR_SIZE), (fx.GICR_BASE + fx.GICR_SIZE, 0)))
        self.refused('gicr-mismatch', fx.good(empty), reason='nonzero multiple')


class McfgEdges(fx.Base):
    def test_sixteen_allocations_are_the_most(self):
        self.assertEqual(len(bc.parse_hamoa_tables(fx.good(MCFG=fx.mcfg(windows(16)))).ecam), 16)
        self.refused('table-structure', fx.good(MCFG=fx.mcfg(windows(17))), reason='more than 16')

    def test_a_window_may_cover_a_single_bus(self):
        facts = bc.parse_hamoa_tables(fx.good(MCFG=fx.mcfg([(0, 0x40000000, 5, 5)])))
        self.assertEqual(facts.ecam, (bc.EcamAllocation(0, 0x40000000, 5, 5),))
        self.refused('table-structure', fx.good(MCFG=fx.mcfg([(0, 0x40000000, 6, 5)])), reason='after its end bus')

    def test_bus_ranges_may_touch_but_not_share_a_bus(self):
        far = 0x80000000   # far enough that the memory windows cannot be what is refused
        touching = fx.good(MCFG=fx.mcfg([(0, 0x40000000, 0, 5), (0, far, 6, 9)]))
        self.assertEqual(len(bc.parse_hamoa_tables(touching).ecam), 2)
        sharing = fx.good(MCFG=fx.mcfg([(0, 0x40000000, 0, 5), (0, far, 5, 9)]))
        self.refused('table-structure', sharing, reason='overlapping bus ranges')


class Isolation(unittest.TestCase):
    def test_the_boot_gate_helper_makes_the_descriptor_module_importable_by_itself(self):
        # Isolated mode keeps tools/ off sys.path, as it is for a caller that imports only probes/bootcontract.
        code = ('import sys\nsys.path.insert(0, sys.argv[1])\nimport bootcontract\n'
                'assert "board_descriptor" not in sys.modules\n'
                'try:\n    bootcontract._require_boot_ready(object())\nexcept Exception:\n    pass\n'
                'print("board_descriptor" in sys.modules)\n')
        result = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(ROOT / 'probes')], capture_output=True,
                                text=True, timeout=120)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, 'True'), result.stderr)


class CliEdges(unittest.TestCase):
    DESCRIPTOR = BUILD / 'synthetic.json'

    def test_a_dump_dir_that_is_not_a_directory_is_reported_as_that(self):
        for target in (BUILD / 'absent', self.DESCRIPTOR):
            for extra in ((), ('--run', 'RUN01')):
                with self.subTest(target=target.name, extra=extra):
                    code, out, err = fx.run_cli(target, *extra, '--descriptor', self.DESCRIPTOR)
                    self.assertEqual((code, out), (2, ''))
                    self.assertIn('DUMP_DIR is not a directory', err)

    def test_only_directories_named_like_a_run_count_as_runs(self):
        parent = BUILD / 'mixed'
        fx.write_dump(parent / 'RUN01')
        (parent / 'RUN03').write_text('a file named like a run\n', encoding='utf-8')
        (parent / 'notes').mkdir()
        code, out, err = fx.run_cli(parent, '--descriptor', self.DESCRIPTOR)
        self.assertEqual((code, err), (0, ''), out)
        self.assertIn('ACPI tables read from RUN01:', out)

    def test_a_table_file_of_exactly_the_cap_is_read_and_judged_by_its_contents(self):
        dump = fx.write_dump(BUILD / 'at-cap')
        (dump / 'APIC.bin').write_bytes(bytes(CAP))
        code, out, _ = fx.run_cli(dump, '--descriptor', self.DESCRIPTOR)
        self.assertEqual(code, 1)
        self.assertIn('REFUSED: table-signature', out)
        self.assertNotIn('table cap', out)

    def test_the_tool_lists_the_tables_it_actually_read(self):
        full = fx.write_dump(BUILD / 'full' / 'RUN01')
        self.assertIn('ACPI tables read from RUN01: RSDP, XSDT, APIC, MCFG\n',
                      fx.run_cli(full, '--descriptor', self.DESCRIPTOR)[1])
        partial = fx.write_dump(BUILD / 'partial' / 'RUN01', {k: v for k, v in fx.good().items() if k != 'MCFG'})
        self.assertIn('ACPI tables read from RUN01: RSDP, XSDT, APIC\n',
                      fx.run_cli(partial, '--descriptor', self.DESCRIPTOR)[1])


class Hygiene(unittest.TestCase):
    def test_this_file_follows_the_repository_rules(self):
        text = Path(__file__).read_text(encoding='utf-8')
        self.assertIn('SPDX-License-Identifier: MIT', '\n'.join(text.splitlines()[:3]))
        self.assertLess(len(text.splitlines()), 1500)
        self.assertLess(len(text.encode()), 50 * 1024)
        for prefix in HOME_PREFIXES:
            self.assertNotIn(prefix, text)


def setUpModule():
    shutil.rmtree(BUILD, ignore_errors=True)
    BUILD.mkdir(parents=True)
    (BUILD / 'synthetic.json').write_text(json.dumps(fx.tbd.synthetic()), encoding='utf-8')


def tearDownModule():
    shutil.rmtree(BUILD, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
