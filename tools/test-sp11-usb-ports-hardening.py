#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests for the independent-source guard and the unmeasured core id of tools/sp11_usb_ports.py.

* An A16-hard-coded base (0x0a600000, 0x0a800000) is accepted on an SP11 port only when its provenance cites an
  independent source. The guard used to look for the words "hamoa.dtsi", "acpi" or "measured" anywhere in the text,
  so "unmeasured guess" and "not measured on any unit" passed. A source now has to cite a device-tree line
  (hamoa.dtsi:NNNN) or an ACPI dump, or claim a measurement together with VERIFIED or OBSERVED evidence, and any
  negation voids it.
* The table carries expect_gsnpsid = 0 because no source measures the unit's DWC3 core id, and q1n1's validator reads
  0 as "accept any of three DWC3 families": an unknown encoded as a wildcard. `emit_c` and `emit-c` now refuse it
  unless the caller passes allow_unmeasured_id / --allow-unmeasured-id, and say so in the generated text.

Synthetic descriptors only (the fixtures are those of tools/test-sp11-usb-ports.py, which is loaded for them);
nothing touches hardware.

    python3 tools/test-sp11-usb-ports-hardening.py
"""
from pathlib import Path
import importlib.util
import json
import sys
import unittest

sys.dont_write_bytecode = True
TOOLS = Path(__file__).resolve().parent

spec = importlib.util.spec_from_file_location('usb_ports_fixtures', TOOLS / 'test-sp11-usb-ports.py')
T = importlib.util.module_from_spec(spec)
spec.loader.exec_module(T)
up, bd = T.up, T.bd
proposal, derive, codes, source, tool, BUILD = T.proposal, T.derive, T.codes, T.source, T.tool, T.BUILD

A16_RANGES = ('usb-1-ss0', 'usb-1-ss1')       # the two Denali controllers sit at the two A16 bases
REFUSED = ['a16-base-not-independent']
CITATION = 'Linux 7475ea8b58616b33c04afb976cf0fbaad9dd63fb arch/arm64/boot/dts/qcom/hamoa.dtsi:5256'


def with_provenance(text, evidence='INFERRED', name='usb-1-ss0'):
    d = source(proposal(), name, text)
    d['provenance'][f'ranges.{name}']['evidence'] = evidence
    return d


class IndependentSource(unittest.TestCase):
    def test_a_negated_or_empty_claim_does_not_make_an_a16_base_independent(self):
        cases = (
            ('unmeasured guess', 'INFERRED'),
            ('not measured on any unit', 'INFERRED'),
            ('not measured on any unit', 'OBSERVED'),
            ('never measured', 'OBSERVED'),
            ('measured: no', 'OBSERVED'),
            ('unverified, measured by nobody', 'VERIFIED'),
            ('no ACPI table was read', 'INFERRED'),
            ('ACPI not read; a guess', 'INFERRED'),
            ('without hamoa.dtsi:5256', 'INFERRED'),
            (CITATION + ' is not what this unit has', 'INFERRED'),
            ('assumed from the ACPI of another board', 'INFERRED'),
            ('see hamoa.dtsi', 'INFERRED'),                                 # a file name is not a line citation
            ('hamoa.dtsi:', 'INFERRED'),
            ('acpidump, unnamed table', 'INFERRED'),                        # not the whole word
            ('measured', 'INFERRED'),                                       # a measurement claim needs measured evidence
            ('measured at X2 from the unit', 'INFERRED'),
            ('measured at X2 from the unit', 'EXPERIMENTAL'),
        )
        for name in A16_RANGES:
            for text, evidence in cases:
                with self.subTest(range=name, source=text, evidence=evidence):
                    self.assertEqual(codes(with_provenance(text, evidence, name)), REFUSED)

    def test_a_cited_public_source_or_a_measured_one_still_counts(self):
        cases = (
            (CITATION, 'INFERRED'),
            (CITATION + ' (test source)', 'INFERRED'),
            ('ACPI DSDT of the unit, resource of the USB0 device', 'INFERRED'),
            ('the unit\'s ACPI DSDT', 'OBSERVED'),
            ('measured at X2 from the unit', 'OBSERVED'),
            ('measured at X2 from the unit', 'VERIFIED'),
        )
        for name in A16_RANGES:
            for text, evidence in cases:
                with self.subTest(range=name, source=text, evidence=evidence):
                    self.assertEqual(codes(with_provenance(text, evidence, name)), [])

    def test_the_listing_flag_is_set_only_for_an_independent_source(self):
        good = derive(with_provenance(CITATION))
        self.assertEqual([p.flags for p in good.ports], [up.FLAG_HOST_ONLY | up.FLAG_A16_BASE_LISTED] * 2)
        bad = derive(with_provenance('unmeasured guess'))
        self.assertFalse(bad.ok)

    def test_the_a16_markers_still_void_a_good_looking_source(self):
        for text in (CITATION + ' q1n1 usb-ports.c:367', 'A16 ' + CITATION, 'the zenbook ' + CITATION):
            with self.subTest(source=text):
                self.assertEqual(codes(with_provenance(text)), REFUSED)

    def test_every_shipped_proposal_source_is_independent(self):
        board = bd.validate(proposal())
        for name in A16_RANGES:
            self.assertTrue(up.independent_source(board, name), name)


class UnmeasuredCoreId(unittest.TestCase):
    def setUp(self):
        BUILD.mkdir(parents=True, exist_ok=True)
        self.descriptor = BUILD / 'hardening-proposed.json'
        self.descriptor.write_text(json.dumps(proposal(), indent=2), encoding='utf-8')
        self.target = BUILD / 'hardening-table.c'
        self.target.unlink(missing_ok=True)

    def test_emit_c_refuses_an_unmeasured_id_by_default(self):
        with self.assertRaisesRegex(up.UsbPortError, 'core id is unmeasured') as raised:
            up.emit_c(derive(proposal()), 'sp11.json')
        self.assertIn('--allow-unmeasured-id', str(raised.exception))
        self.assertIn('0x5533', str(raised.exception))

    def test_the_explicit_opt_in_emits_and_the_text_says_it_was_asked_for(self):
        text = up.emit_c(derive(proposal()), 'sp11.json', allow_unmeasured_id=True)
        self.assertIn('UNMEASURED', text)
        self.assertIn('--allow-unmeasured-id', text)
        self.assertEqual(text.count('.expect_gsnpsid = 0x0u'), 2)

    def test_the_command_line_refuses_without_the_flag_and_writes_nothing(self):
        status, out, err = tool('emit-c', str(self.descriptor), str(self.target))
        self.assertEqual(status, 1)
        self.assertIn('REFUSED', err)
        self.assertIn('--allow-unmeasured-id', err)
        self.assertFalse(self.target.exists())

    def test_the_command_line_with_the_flag_writes_the_same_text_as_the_function(self):
        status, _, err = tool('emit-c', str(self.descriptor), str(self.target), '--allow-unmeasured-id')
        self.assertEqual((status, err), (0, ''))
        self.assertEqual(self.target.read_text(),
                         up.emit_c(derive(proposal()), self.descriptor.name, allow_unmeasured_id=True))

    def test_the_table_command_still_derives_and_reports_the_unmeasured_id(self):
        status, out, _ = tool('table', str(self.descriptor))
        self.assertEqual(status, 0)
        self.assertIn('table: OK', out)
        self.assertIn('core id: UNMEASURED', out)
        self.assertIn('--allow-unmeasured-id', out)

    def test_a_refused_derivation_is_reported_before_the_core_id(self):
        bare = BUILD / 'hardening-bare.json'
        bare.write_text(json.dumps(T.bare()), encoding='utf-8')
        status, _, err = tool('emit-c', str(bare), str(self.target), '--allow-unmeasured-id')
        self.assertEqual(status, 1)
        self.assertIn('usb-host-not-present', err)
        self.assertFalse(self.target.exists())

    def test_the_in_memory_table_still_marks_the_id_as_zero(self):
        table = up.table_dict(derive(proposal()))
        self.assertEqual([p['expect_gsnpsid'] for p in table['ports']], [0, 0])
        self.assertEqual(up.check_table(table), 'OK')

    def test_the_wildcard_is_what_the_refusal_is_about(self):
        for family in up.DWC3_FAMILIES:
            self.assertEqual(up.check_controller(0, family << 16 | 0x330a, 0x01100020, 0x02000040), 'OK', hex(family))
        self.assertEqual(up.check_controller(0, 0x12340000, 0x01100020, 0x02000040), 'FAMILY')


class Hygiene(unittest.TestCase):
    def test_spdx_and_sizes(self):
        for name in ('sp11_usb_ports.py', 'test-sp11-usb-ports-hardening.py'):
            path = TOOLS / name
            text = path.read_text()
            self.assertIn('SPDX-License-Identifier: MIT', '\n'.join(text.splitlines()[:3]), name)
            self.assertLess(len(text.splitlines()), 1500, name)
            self.assertLess(path.stat().st_size, 50 * 1024, name)


if __name__ == '__main__':
    unittest.main()
