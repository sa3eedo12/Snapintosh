#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Acceptance and refusal tests for tools/sp11_usb_ports.py and the SP11 host-link study.

    python3 tools/test-sp11-usb-ports.py

Hardware is never touched. Every descriptor is derived from config/boards/sp11.json in memory (a proposal that lists the
two Denali USB controllers) and then made faulty one way at a time, so each refusal has a test that provokes it and a
test that the clean descriptor does not trigger it. Checks that need an input that may be absent report SKIP, never PASS:

* the A16 inventory against a q1n1 checkout (Q1N1_DIR, or ../q1n1);
* the device-tree and driver citations against the pinned Linux files under build/refs/ (fetch
  https://raw.githubusercontent.com/torvalds/linux/7475ea8b58616b33c04afb976cf0fbaad9dd63fb/<path>, or `gh api` with
  the Accept header application/vnd.github.raw, for the files named in DT_LINES below);
* the C validator in q1n1 platform/uefi/sp11-usb-ports.c against the Python reference over generated tables (needs
  clang and a q1n1 checkout that carries the SP11 patch: Q1N1_SP11_DIR, else Q1N1_DIR or ../q1n1 if they carry it).

Evidence: EXPERIMENTAL (host tests over a public-source descriptor). Hardware: NOT RUN.
"""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import board_descriptor as bd
import sp11_usb_ports as up

BUILD = ROOT / 'build' / 'sp11-usb-ports-test'
REFS = ROOT / 'build' / 'refs'
DOC = ROOT / 'docs' / 'SP11-HOST-LINK-STUDY.md'
SHIPPED = ROOT / 'config' / 'boards' / 'sp11.json'
A16 = ROOT / 'config' / 'boards' / 'a16.json'
LLVM = ('/opt/homebrew/opt/llvm/bin', '/opt/homebrew/opt/lld/bin', '/opt/homebrew/bin')
ENV = dict(os.environ, PATH=os.pathsep.join(LLVM + (os.environ.get('PATH', ''),)), PYTHONDONTWRITEBYTECODE='1')
HOME_PREFIXES = ('/' + 'Users' + '/', '/' + 'home' + '/')   # built at run time: no file carries a literal home path

INDEPENDENT = 'Linux 7475ea8b58616b33c04afb976cf0fbaad9dd63fb arch/arm64/boot/dts/qcom/hamoa.dtsi:5256 (test source)'


def shipped():
    return json.loads(SHIPPED.read_text(encoding='utf-8'))


def bare():
    """The shipped descriptor without USB controllers, whatever state the shipped file is in."""
    return up.strip_usb(shipped())


def proposal():
    return up.proposed_descriptor(shipped())


def derive(d):
    return up.derive(bd.validate(d))


def codes(d):
    return sorted(r.code for r in derive(d).refusals)


def usb(d, name):
    return next(r for r in d['ranges'] if r['name'] == name)


def drop(d, name):
    d['ranges'] = [r for r in d['ranges'] if r['name'] != name]
    d['devices']['usb-host']['ranges'].remove(name)
    d['provenance'].pop(f'ranges.{name}')
    return d


def add(d, name, base, size, source=INDEPENDENT):
    d['ranges'].append({'name': name, 'kind': 'mmio', 'base': base, 'size': size, 'device': 'usb-host'})
    d['devices']['usb-host']['ranges'].append(name)
    d['provenance'][f'ranges.{name}'] = {'source': source, 'evidence': 'INFERRED'}
    return d


def source(d, name, text):
    d['provenance'][f'ranges.{name}']['source'] = text
    return d


def load_text(text, name='descriptor.json'):
    BUILD.mkdir(parents=True, exist_ok=True)
    path = BUILD / name
    path.write_text(text, encoding='utf-8')
    return up.load_and_derive(path)


def tool(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        status = up.main(list(argv))
    return status, out.getvalue(), err.getvalue()


class ShippedDescriptor(unittest.TestCase):
    """A descriptor that lists no USB controller gives a null table and every consumer refuses.

    config/boards/sp11.json lists none today; if the proposal is applied to it these tests keep running on the bare
    copy, and test_the_shipped_file_is_in_one_of_its_two_valid_states covers the file itself.
    """

    def bare_result(self):
        BUILD.mkdir(parents=True, exist_ok=True)
        path = BUILD / 'bare.json'
        path.write_text(json.dumps(bare(), indent=2), encoding='utf-8')
        return path, up.load_and_derive(path)

    def test_the_shipped_file_is_in_one_of_its_two_valid_states(self):
        result = up.load_and_derive(SHIPPED)
        if shipped()['devices']['usb-host']['state'] == 'unknown':
            self.assertFalse(result.ok)
            self.assertIn('usb-host-not-present', [r.code for r in result.refusals])
        else:
            self.assertTrue(result.ok, result.refusals)

    def test_a_descriptor_without_usb_is_refused_with_a_null_table(self):
        path, result = self.bare_result()
        self.assertFalse(result.ok)
        self.assertEqual(sorted(r.code for r in result.refusals),
                         ['missing-controller', 'missing-controller', 'usb-host-not-present'])
        self.assertEqual([p.name for p in result.ports], ['usb_1_ss0', 'usb_1_ss1'])
        for port in result.ports:
            self.assertIsNone(port.dwc3_base)
            self.assertIsNone(port.window_size)
            self.assertIsNone(port.qscratch_base)
        document = result.to_json()
        self.assertTrue(all(p['dwc3_base'] is None for p in document['ports']))
        self.assertFalse(document['ok'])

    def test_a_refused_table_is_never_emitted(self):
        path, result = self.bare_result()
        with self.assertRaises(up.UsbPortError):
            up.table_dict(result)
        with self.assertRaises(up.UsbPortError):
            up.emit_c(result)

    def test_the_command_line_exits_1_and_names_the_evidence_needed(self):
        path, _ = self.bare_result()
        status, out, err = tool('table', str(path))
        self.assertEqual(status, 1)
        self.assertIn('REFUSED usb-host-not-present', out)
        self.assertIn('evidence needed', out)
        self.assertIn('base null', out)
        target = BUILD / 'never.c'
        target.unlink(missing_ok=True)
        status, out, err = tool('emit-c', str(path), str(target))
        self.assertEqual(status, 1)
        self.assertFalse(target.exists())

    def test_the_a16_descriptor_is_refused_as_not_sp11(self):
        result = up.load_and_derive(A16)
        self.assertEqual([r.code for r in result.refusals], ['not-sp11'])
        self.assertEqual(result.ports, ())

    def test_the_unknowns_are_listed_with_the_measurement_that_settles_them(self):
        names = {name for name, how in derive(bare()).unknown}
        self.assertEqual(names, {'gsnpsid', 'xhci_caplength_hciversion_maxports', 'uefi_leftover_state',
                                 'adsp_pmic_glink_alive', 'data_role_outcome'})
        for name, how in up.UNKNOWN_FACTS:
            self.assertRegex(how, r'X[23]|probe|measure|read')


class ProposedDescriptor(unittest.TestCase):
    """The change that makes the table derivable, and what comes out of it."""

    def test_the_proposal_passes_the_loader_and_derives_two_controllers(self):
        result = derive(proposal())
        self.assertTrue(result.ok, result.refusals)
        by = {p.name: p for p in result.ports}
        self.assertEqual(by['usb_1_ss0'].dwc3_base, 0x0a600000)
        self.assertEqual(by['usb_1_ss1'].dwc3_base, 0x0a800000)
        for p in result.ports:
            self.assertEqual(p.window_size, 0xfd000)            # 0xfc100 rounded up to a 4 KiB multiple
            self.assertEqual(p.qscratch_offset, 0xf8800)
            self.assertEqual(p.qscratch_base, p.dwc3_base + 0xf8800)
            self.assertEqual(p.flags, up.FLAG_HOST_ONLY | up.FLAG_A16_BASE_LISTED)

    def test_the_proposal_changes_only_the_usb_entries(self):
        before, after = bare(), proposal()
        self.assertEqual({k for k in before if before[k] != after[k]}, {'ranges', 'devices', 'provenance'})
        self.assertEqual(after['ranges'][:len(before['ranges'])], before['ranges'])
        self.assertEqual([r['name'] for r in after['ranges'][len(before['ranges']):]], ['usb-1-ss0', 'usb-1-ss1'])
        self.assertEqual({k for k in before['devices'] if before['devices'][k] != after['devices'][k]}, {'usb-host'})
        changed = {k for k in set(before['provenance']) | set(after['provenance'])
                   if before['provenance'].get(k) != after['provenance'].get(k)}
        self.assertEqual(changed, {'devices.usb-host', 'ranges.usb-1-ss0', 'ranges.usb-1-ss1'})
        self.assertEqual(after['board']['status'], 'UNIMPLEMENTED')      # a template stays a template

    def test_the_proposal_is_idempotent(self):
        once = proposal()
        self.assertEqual(up.proposed_descriptor(once), once)

    def test_only_a_page_rounded_window_is_representable_in_schema_v1(self):
        d = proposal()
        usb(d, 'usb-1-ss0')['size'] = '0xfc100'                      # the device-tree size, as written upstream
        with self.assertRaisesRegex(bd.BoardDescriptorError, 'multiples of'):
            bd.validate(d)

    def test_everything_beyond_the_descriptor_is_inferred_and_nothing_is_claimed_verified(self):
        result = derive(proposal())
        self.assertTrue(result.inferred)
        for p in result.ports:
            self.assertEqual({f.evidence for f in p.facts.values()}, {'INFERRED'})
        self.assertIn('usb_1_ss0.qscratch_offset', result.inferred)
        self.assertIn('usb_1_ss1.retimer', result.inferred)
        self.assertIn('usb_1_ss1.hs_phy', result.inferred)

    def test_the_proposal_is_not_boot_ready_and_says_so(self):
        result = derive(proposal())
        self.assertIn('UNIMPLEMENTED', result.boot_ready)
        self.assertNotIn('usb-host', result.boot_ready)

    def test_the_c_table_and_json_are_deterministic(self):
        result = derive(proposal())
        self.assertEqual(up.emit_c(result, 'x.json', allow_unmeasured_id=True),
                         up.emit_c(derive(proposal()), 'x.json', allow_unmeasured_id=True))
        self.assertEqual(json.dumps(result.to_json(), sort_keys=True), json.dumps(derive(proposal()).to_json(), sort_keys=True))
        table = up.table_dict(result)
        self.assertEqual(up.check_table(table), 'OK')
        self.assertEqual(table['count'], 2)
        self.assertEqual([p['expect_gsnpsid'] for p in table['ports']], [0, 0])    # unknown stays unknown

    def test_the_emitted_c_carries_no_machine_data_and_pins_the_gate(self):
        text = up.emit_c(derive(proposal()), 'sp11.json', allow_unmeasured_id=True)
        for prefix in HOME_PREFIXES:
            self.assertNotIn(prefix, text)
        self.assertTrue(text.startswith('/* SPDX-License-Identifier: MIT */'))
        self.assertIn('INFERRED', text)
        self.assertIn('Descriptor boot-ready: no', text)
        self.assertIn('q1n1_sp11_usb_table_get', text)
        self.assertNotIn('static const struct q1n1_sp11_usb_table q1n1_sp11_usb_table', text)

    def test_propose_writes_a_descriptor_the_table_command_accepts(self):
        BUILD.mkdir(parents=True, exist_ok=True)
        out = BUILD / 'proposed.json'
        self.assertEqual(tool('propose', str(SHIPPED), '--out', str(out))[0], 0)
        self.assertEqual(json.loads(out.read_text()), proposal())
        status, text, _ = tool('table', str(out))
        self.assertEqual(status, 0)
        self.assertIn('table: OK', text)
        status, text, _ = tool('table', str(out), '--json')
        self.assertEqual(json.loads(text)['ports'][0]['dwc3_base'], '0x0a600000')
        c_file = BUILD / 'proposed.c'
        self.assertEqual(tool('emit-c', str(out), str(c_file), '--allow-unmeasured-id')[0], 0)
        self.assertEqual(c_file.read_text(), up.emit_c(derive(proposal()), 'proposed.json', allow_unmeasured_id=True))

    def test_propose_refuses_a_descriptor_it_cannot_extend(self):
        BUILD.mkdir(parents=True, exist_ok=True)
        broken = BUILD / 'broken.json'
        broken.write_text('{"schema_version": 1}', encoding='utf-8')
        self.assertEqual(tool('propose', str(broken))[0], 2)
        broken.write_text('not json', encoding='utf-8')
        self.assertEqual(tool('propose', str(broken))[0], 2)


class FaultyDescriptors(unittest.TestCase):
    """One fault at a time. Each refusal code has a descriptor that provokes it."""

    def test_the_clean_proposal_has_no_refusal(self):
        self.assertEqual(codes(proposal()), [])

    def test_usb_host_unknown_with_ranges_listed_is_refused(self):
        d = proposal()
        d['devices']['usb-host'] = {'state': 'unknown', 'mmio': None, 'ranges': ['usb-1-ss0', 'usb-1-ss1']}
        d['provenance']['devices.usb-host'] = {'source': 'UNKNOWN', 'evidence': 'UNIMPLEMENTED'}
        self.assertEqual(codes(d), ['usb-host-not-present'])

    def test_usb_host_absent_without_ranges_is_refused_and_both_controllers_are_missing(self):
        d = drop(drop(proposal(), 'usb-1-ss0'), 'usb-1-ss1')
        d['devices']['usb-host'] = {'state': 'absent', 'mmio': False, 'ranges': []}
        self.assertEqual(codes(d), ['missing-controller', 'missing-controller', 'usb-host-not-present'])

    def test_usb_host_present_without_mmio_is_refused(self):
        d = drop(drop(proposal(), 'usb-1-ss0'), 'usb-1-ss1')
        d['devices']['usb-host'] = {'state': 'present', 'mmio': False, 'ranges': []}
        self.assertEqual(codes(d), ['missing-controller', 'missing-controller', 'usb-host-no-mmio'])

    def test_an_absent_device_that_still_owns_ranges_is_a_loader_error(self):
        d = proposal()
        d['devices']['usb-host'] = {'state': 'absent', 'mmio': False, 'ranges': []}
        result = load_text(json.dumps(d))
        self.assertEqual([r.code for r in result.refusals], ['descriptor-invalid'])

    def test_a_missing_controller_is_refused_and_leaves_a_null_row(self):
        for name, label in (('usb-1-ss0', 'usb_1_ss0'), ('usb-1-ss1', 'usb_1_ss1')):
            result = derive(drop(proposal(), name))
            self.assertEqual([r.code for r in result.refusals], ['missing-controller'])
            self.assertIn(name, result.refusals[0].detail)
            row = next(p for p in result.ports if p.name == label)
            self.assertIsNone(row.dwc3_base)
            self.assertFalse(result.ok)

    def test_an_unknown_usb_range_is_refused(self):
        self.assertEqual(codes(add(proposal(), 'usb-9', '0x0a900000', '0x1000')), ['unknown-range'])

    def test_a_controller_the_board_leaves_disabled_is_refused(self):
        self.assertEqual(codes(add(proposal(), 'usb-mp', '0x0a400000', '0xfd000')), ['controller-not-enabled'])
        self.assertEqual(codes(add(proposal(), 'usb-2', '0x0a200000', '0xfd000')), ['controller-not-enabled'])

    def test_a_base_that_differs_from_the_pinned_device_tree_is_refused(self):
        d = proposal()
        usb(d, 'usb-1-ss0')['base'] = '0x0a700000'
        self.assertEqual(codes(d), ['base-mismatch'])

    def test_swapped_bases_are_refused_even_with_independent_provenance(self):
        d = proposal()
        usb(d, 'usb-1-ss0')['base'], usb(d, 'usb-1-ss1')['base'] = '0x0a800000', '0x0a600000'
        self.assertEqual(codes(d), ['base-mismatch', 'base-mismatch'])

    def test_an_a16_base_sourced_from_the_a16_code_is_refused(self):
        for text in ('q1n1 platform/uefi/usb-ports.c:367 (USB0)',
                     'ASUS Zenbook A16 notes: USB0 is at this address',
                     'A16 hamoa.dtsi:5256',                       # a device-tree name does not launder an A16 source
                     'the A16 value, hamoa.dtsi:5256'):
            d = source(proposal(), 'usb-1-ss0', text)
            self.assertEqual(codes(d), ['a16-base-not-independent'], text)

    def test_an_a16_base_with_an_unrecognised_source_is_refused(self):
        d = source(proposal(), 'usb-1-ss1', 'a vendor datasheet table')
        self.assertEqual(codes(d), ['a16-base-not-independent'])

    def test_an_unimplemented_provenance_cannot_make_a_base_independent(self):
        d = proposal()
        d['provenance']['ranges.usb-1-ss0']['evidence'] = 'UNIMPLEMENTED'
        with self.assertRaises(bd.BoardDescriptorError):          # the loader already refuses a populated field like this
            bd.validate(d)

    def test_a_hash_that_happens_to_contain_a16_does_not_void_a_good_source(self):
        d = source(proposal(), 'usb-1-ss0', 'Linux 3a16b7c4e9d2f8a16b0c5d1e arch/arm64/boot/dts/qcom/hamoa.dtsi:5256')
        self.assertEqual(codes(d), [])

    def test_acpi_and_measurement_sources_are_independent(self):
        self.assertEqual(codes(source(proposal(), 'usb-1-ss0', 'ACPI DSDT of the unit, resource of the USB0 device')), [])
        for evidence in ('VERIFIED', 'OBSERVED'):         # a measurement claim needs measured-grade evidence
            d = source(proposal(), 'usb-1-ss0', 'measured at X2 from the unit')
            d['provenance']['ranges.usb-1-ss0']['evidence'] = evidence
            self.assertEqual(codes(d), [], evidence)

    def test_a_window_that_does_not_cover_the_glue_block_is_refused(self):
        for size in ('0x1000', '0xf8000'):
            d = proposal()
            usb(d, 'usb-1-ss0')['size'] = size
            self.assertEqual(codes(d), ['window-too-small'], size)
        d = proposal()
        usb(d, 'usb-1-ss0')['size'] = '0xf9000'                    # glue block ends at 0xf8c00: covered
        self.assertEqual(codes(d), [])

    def test_a_window_beyond_the_device_tree_window_is_refused(self):
        for size in ('0xfe000', '0x100000', '0x200000'):
            d = proposal()
            usb(d, 'usb-1-ss1')['size'] = size
            self.assertEqual(codes(d), ['window-exceeds-dt'], size)

    def test_windows_that_overlap_only_in_the_device_tree_are_refused(self):
        d = proposal()
        usb(d, 'usb-1-ss0')['size'] = '0xf9000'                    # descriptor windows stay disjoint ...
        usb(d, 'usb-1-ss1')['base'], usb(d, 'usb-1-ss1')['size'] = '0x0a6fa000', '0x2000'
        self.assertIn('controller-overlap', codes(d))              # ... but 0xfc100 reaches the second base

    def test_a_window_inside_an_unlisted_neighbour_is_refused(self):
        d = proposal()
        usb(d, 'usb-1-ss1')['base'] = '0x0a400000'                 # usb_mp's address, which the descriptor never lists
        self.assertIn('controller-overlap', codes(d))
        self.assertIn('base-mismatch', codes(d))

    def test_overlapping_descriptor_windows_are_a_loader_error(self):
        d = proposal()
        usb(d, 'usb-1-ss1')['base'] = '0x0a680000'
        result = load_text(json.dumps(d))
        self.assertEqual([r.code for r in result.refusals], ['descriptor-invalid'])
        self.assertIn('overlaps', result.refusals[0].detail)

    def test_a_usb_range_owned_by_another_device_is_refused(self):
        d = proposal()
        usb(d, 'usb-1-ss0')['device'] = 'gpu'
        d['devices']['gpu']['ranges'].append('usb-1-ss0')
        d['devices']['usb-host']['ranges'] = ['usb-1-ss1']
        self.assertEqual(codes(d), ['foreign-owner', 'missing-controller'])

    def test_malformed_input_is_reported_by_the_loader_not_repaired(self):
        for text in ('{"schema_version": 1', '[]', '{"schema_version": 1, "extra": 1}', ''):
            result = load_text(text)
            self.assertEqual([r.code for r in result.refusals], ['descriptor-invalid'], text)
            self.assertFalse(result.ok)
        result = up.load_and_derive(BUILD / 'does-not-exist.json')
        self.assertEqual([r.code for r in result.refusals], ['descriptor-invalid'])


class TableRules(unittest.TestCase):
    """The Python reference of the C validator: one faulty table per rule, in the C order."""

    def good(self):
        return up.table_dict(derive(proposal()))

    def test_the_derived_table_is_valid_and_each_rule_has_a_faulty_table(self):
        def broken(edit):
            t = copy.deepcopy(self.good())
            edit(t)
            return up.check_table(t)
        cases = {
            'MAGIC': lambda t: t.update(magic=t['magic'] ^ 1),
            'VERSION': lambda t: t.update(version=2),
            'COUNT': lambda t: t.update(count=0),
            'RESERVED': lambda t: t.update(count=1),
            'FLAGS': lambda t: t['ports'][0].update(flags=4),
            'BASE': lambda t: t['ports'][1].update(dwc3_base=0xa800010),
            'WINDOW': lambda t: t['ports'][0].update(window_size=0x201000),
            'LIMIT': lambda t: t['ports'][0].update(dwc3_base=1 << 36),
            'QSCRATCH': lambda t: t['ports'][0].update(qscratch_offset=0x1000),
            'IDENTITY': lambda t: t['ports'][0].update(expect_gsnpsid=0x12340000),
            'A16_BASE': lambda t: t['ports'][0].update(flags=up.FLAG_HOST_ONLY),
            'OVERLAP': lambda t: t['ports'][1].update(dwc3_base=0xa680000),
        }
        self.assertEqual(up.check_table(self.good()), 'OK')
        self.assertEqual(set(cases) | {'OK', 'NULL'}, set(up.ERRORS))
        for name, edit in cases.items():
            self.assertEqual(broken(edit), name)

    def test_boundaries(self):
        t = self.good()
        t['ports'][0].update(window_size=0x200000)
        t['ports'][1].update(dwc3_base=0xac00000)
        self.assertEqual(up.check_table(t), 'OK')                  # adjacent windows do not overlap
        t['ports'][0].update(qscratch_offset=0x200000 - 0x400)
        self.assertEqual(up.check_table(t), 'OK')                  # the glue block ends exactly at the window end
        t['ports'][0].update(qscratch_offset=0x200000 - 0x3fc)
        self.assertEqual(up.check_table(t), 'QSCRATCH')            # four bytes further does not fit
        t = self.good()
        t['ports'][0].update(window_size=0x1000)
        self.assertEqual(up.check_table(t), 'QSCRATCH')            # a 4 KiB window cannot hold the glue block
        t = self.good()
        t['ports'][0].update(dwc3_base=(1 << 36) - 0x200000, window_size=0x100000, flags=up.FLAG_HOST_ONLY)
        self.assertEqual(up.check_table(t), 'OK')                  # ends 1 MiB below the monitor's limit
        t['ports'][0].update(window_size=0x200000)
        self.assertEqual(up.check_table(t), 'LIMIT')               # ends exactly at it: refused, as in the descriptor

    def test_the_a16_bases_pass_only_with_the_listing_flag(self):
        for base in up.A16_BASES:
            t = self.good()
            t['ports'][0].update(dwc3_base=base, flags=up.FLAG_HOST_ONLY)
            t['ports'][1].update(dwc3_base=0xc200000)
            self.assertEqual(up.check_table(t), 'A16_BASE')
            t['ports'][0]['flags'] |= up.FLAG_A16_BASE_LISTED
            self.assertEqual(up.check_table(t), 'OK')

    def test_controller_judgement(self):
        self.assertEqual(up.check_controller(0, 0x5533330a, 0x01100020, 0x02000040), 'OK')
        self.assertEqual(up.check_controller(0, 0x33313130, 0x01200030, 0x02000040), 'OK')
        self.assertEqual(up.check_controller(0, 0x33323000, 0x01100020, 0x02000040), 'OK')
        self.assertEqual(up.check_controller(0, 0x12345678, 0x01100020, 0x02000040), 'FAMILY')
        self.assertEqual(up.check_controller(0x5533330a, 0x5533330b, 0x01100020, 0x02000040), 'FAMILY')
        self.assertEqual(up.check_controller(0, 0x5533330a, 0x0110001c, 0x02000040), 'CAPLENGTH')
        self.assertEqual(up.check_controller(0, 0x5533330a, 0x01100022, 0x02000040), 'CAPLENGTH')
        self.assertEqual(up.check_controller(0, 0x5533330a, 0x00ff0020, 0x02000040), 'HCIVERSION')
        self.assertEqual(up.check_controller(0, 0x5533330a, 0x02000020, 0x02000040), 'HCIVERSION')
        self.assertEqual(up.check_controller(0, 0x5533330a, 0x01100020, 0x00ffffff), 'MAXPORTS')
        self.assertEqual(up.check_controller(0, 0x5533330a, 0x01100020, 0x11000000), 'MAXPORTS')


class GitSource(up.TreeSource):
    """The pinned q1n1 commit read through git, so a tree carrying the SP11 patch (shifted lines) still verifies."""

    def __init__(self, root):
        super().__init__(root)
        self.git = shutil.which('git', path=ENV['PATH'])

    def usable(self):
        if not (self.root / '.git').exists() or not self.git:
            return False
        done = subprocess.run([self.git, '-C', str(self.root), 'cat-file', '-e', f'{up.Q1N1_COMMIT}^{{commit}}'],
                              capture_output=True)
        return done.returncode == 0

    def lines(self, rel):
        done = subprocess.run([self.git, '-C', str(self.root), 'show', f'{up.Q1N1_COMMIT}:{rel}'], capture_output=True)
        if done.returncode:
            raise OSError(rel)
        return done.stdout.decode('utf-8', errors='replace').splitlines()

    def names(self, rel_dir):
        done = subprocess.run([self.git, '-C', str(self.root), 'ls-tree', '--name-only', up.Q1N1_COMMIT, rel_dir + '/'],
                              capture_output=True, text=True)
        return sorted(Path(n).name for n in done.stdout.split())


def pinned_source():
    """(source, how). A git checkout is read at the pinned commit; a copy without .git (an archive, say) must be that commit."""
    root = up.find_q1n1()
    if root is None:
        return None, 'no q1n1 checkout: set Q1N1_DIR or place it next to this tree'
    git = GitSource(root)
    return (git if git.usable() else up.TreeSource(root)), str(root)


class Citations(unittest.TestCase):
    """The study and the tool cite lines. These tests read the cited lines."""

    @classmethod
    def setUpClass(cls):
        cls.source, cls.how = pinned_source()
        cls.doc = DOC.read_text(encoding='utf-8') if DOC.is_file() else ''

    def test_the_study_exists(self):
        self.assertTrue(DOC.is_file(), 'docs/SP11-HOST-LINK-STUDY.md is a deliverable')

    def test_every_a16_inventory_line_still_says_what_the_study_claims(self):
        if self.source is None:
            self.skipTest(self.how)
        bad = [(i.key, why) for i, ok, why in up.verify_inventory(self.source) if not ok]
        self.assertEqual(bad, [])

    def test_q1n1_programs_no_phy_clock_gdsc_regulator_retimer_gpio_or_i2c(self):
        if self.source is None:
            self.skipTest(self.how)
        self.assertEqual(up.negative_hits(self.source), [])

    def test_the_uefi_makefile_names_no_apple_usb_stack_file(self):
        if self.source is None:
            self.skipTest(self.how)
        self.assertNotIn('src/', '\n'.join(self.source.lines('platform/uefi/Makefile')))

    def test_the_inventory_check_can_fail(self):
        if self.source is None:
            self.skipTest(self.how)

        class Shifted(up.TreeSource):
            def __init__(self, inner):
                self.inner = inner

            def lines(self, rel):
                return ['// one line added at the top'] + self.inner.lines(rel)

            def names(self, rel_dir):
                return self.inner.names(rel_dir)

        wrong = [i.key for i, ok, why in up.verify_inventory(Shifted(self.source)) if not ok]
        self.assertGreaterEqual(len(wrong), 20)                  # a one-line drift is caught almost everywhere

        class Polluted(Shifted):
            def lines(self, rel):
                return self.inner.lines(rel) + ['static void gpio_setup(void) {}'] if rel.endswith('main.c') else self.inner.lines(rel)

        self.assertEqual([h[:2] for h in up.negative_hits(Polluted(self.source))][0][0], 'platform/uefi/main.c')

    def test_the_study_cites_every_inventory_line_and_names_every_refusal(self):
        for item in up.INVENTORY:
            self.assertIn(f'{Path(item.path).name}:{item.line}', self.doc, item.key)
            self.assertIn(item.key, self.doc)
        for code in ('usb-host-not-present', 'usb-host-no-mmio', 'no-usb-ranges', 'unknown-range', 'bad-range',
                     'foreign-owner', 'missing-controller', 'controller-not-enabled', 'base-mismatch',
                     'a16-base-not-independent', 'window-too-small', 'window-exceeds-dt', 'controller-overlap',
                     'not-sp11', 'descriptor-invalid'):
            self.assertIn(f'`{code}`', self.doc)
        for name, _ in up.UNKNOWN_FACTS:
            self.assertIn(f'`{name}`', self.doc)
        for pin in (up.LINUX_COMMIT, up.Q1N1_COMMIT):
            self.assertIn(pin, self.doc)

    DT_LINES = (
        ('hamoa.dtsi', 5016, r'usb_1_ss2: usb@a000000'), ('hamoa.dtsi', 5018, r'0x0a000000 0 0xfc100'),
        ('hamoa.dtsi', 5069, r'apps_smmu 0x14a0'), ('hamoa.dtsi', 5107, r'usb_2: usb@a200000'),
        ('hamoa.dtsi', 5109, r'0x0a200000 0 0xfc100'), ('hamoa.dtsi', 5158, r'apps_smmu 0x14e0'),
        ('hamoa.dtsi', 5161, r'maximum-speed = "high-speed"'),
        ('hamoa.dtsi', 5175, r'usb_mp: usb@a400000'), ('hamoa.dtsi', 5177, r'0x0a400000 0 0xfc100'),
        ('hamoa.dtsi', 5235, r'apps_smmu 0x1400'), ('hamoa.dtsi', 5241, r'dr_mode = "host"'),
        ('hamoa.dtsi', 5254, r'usb_1_ss0: usb@a600000'), ('hamoa.dtsi', 5256, r'0x0a600000 0 0xfc100'),
        ('hamoa.dtsi', 5258, r'GCC_CFG_NOC_USB3_PRIM_AXI_CLK'), ('hamoa.dtsi', 5282, r'GIC_SPI 355'),
        ('hamoa.dtsi', 5293, r'GCC_USB30_PRIM_GDSC'), ('hamoa.dtsi', 5294, r'rpmhpd_opp_nom'),
        ('hamoa.dtsi', 5296, r'GCC_USB30_PRIM_BCR'), ('hamoa.dtsi', 5307, r'apps_smmu 0x1420'),
        ('hamoa.dtsi', 5309, r'usb_1_ss0_hsphy'), ('hamoa.dtsi', 5320, r'dma-coherent'),
        ('hamoa.dtsi', 5322, r'status = "disabled"'),
        ('hamoa.dtsi', 5345, r'usb_1_ss1: usb@a800000'), ('hamoa.dtsi', 5347, r'0x0a800000 0 0xfc100'),
        ('hamoa.dtsi', 5384, r'GCC_USB30_SEC_GDSC'), ('hamoa.dtsi', 5398, r'apps_smmu 0x1460'),
        ('hamoa.dtsi', 5411, r'dma-coherent'), ('hamoa.dtsi', 5413, r'status = "disabled"'),
        ('hamoa.dtsi', 2819, r'usb_1_ss0_hsphy: phy@fd3000'), ('hamoa.dtsi', 2822, r'0x00fd3000'),
        ('hamoa.dtsi', 2833, r'usb_1_ss0_qmpphy: phy@fd5000'), ('hamoa.dtsi', 2835, r'0x00fd5000'),
        ('hamoa.dtsi', 2892, r'usb_1_ss1_hsphy: phy@fd9000'), ('hamoa.dtsi', 2895, r'0x00fd9000'),
        ('hamoa.dtsi', 2906, r'usb_1_ss1_qmpphy: phy@fda000'), ('hamoa.dtsi', 2908, r'0x00fda000'),
        ('hamoa.dtsi', 662, r'smem@ffe00000'), ('hamoa.dtsi', 4298, r'remoteproc_adsp: remoteproc@6800000'),
        ('hamoa.dtsi', 4334, r'glink-edge'), ('hamoa.dtsi', 4341, r'label = "lpass"'),
        ('hamoa.dtsi', 4342, r'qcom,remote-pid = <2>'),
        ('x1-microsoft-denali.dtsi', 53, r'pmic-glink \{'), ('x1-microsoft-denali.dtsi', 59, r'orientation-gpios'),
        ('x1-microsoft-denali.dtsi', 62, r'Left-side bottom port'), ('x1-microsoft-denali.dtsi', 63, r'connector@0'),
        ('x1-microsoft-denali.dtsi', 66, r'power-role = "dual"'), ('x1-microsoft-denali.dtsi', 67, r'data-role = "dual"'),
        ('x1-microsoft-denali.dtsi', 99, r'Left-side top port'), ('x1-microsoft-denali.dtsi', 100, r'connector@1'),
        ('x1-microsoft-denali.dtsi', 104, r'data-role = "dual"'),
        ('x1-microsoft-denali.dtsi', 785, r'&i2c3'), ('x1-microsoft-denali.dtsi', 792, r'parade,ps8830'),
        ('x1-microsoft-denali.dtsi', 795, r'pm8550_gpios 10'), ('x1-microsoft-denali.dtsi', 797, r'RPMH_RF_CLK3'),
        ('x1-microsoft-denali.dtsi', 851, r'&i2c7'), ('x1-microsoft-denali.dtsi', 858, r'parade,ps8830'),
        ('x1-microsoft-denali.dtsi', 861, r'tlmm 176'), ('x1-microsoft-denali.dtsi', 863, r'RPMH_RF_CLK4'),
        ('x1-microsoft-denali.dtsi', 1104, r'&remoteproc_adsp'), ('x1-microsoft-denali.dtsi', 1348, r'&usb_1_ss0_hsphy'),
        ('x1-microsoft-denali.dtsi', 1352, r'smb2360_0_eusb2_repeater'), ('x1-microsoft-denali.dtsi', 1357, r'&usb_1_ss0_qmpphy'),
        ('x1-microsoft-denali.dtsi', 1364, r'&usb_1_ss0 \{'), ('x1-microsoft-denali.dtsi', 1365, r'dr_mode = "host"'),
        ('x1-microsoft-denali.dtsi', 1378, r'&usb_1_ss1_hsphy'), ('x1-microsoft-denali.dtsi', 1382, r'smb2360_1_eusb2_repeater'),
        ('x1-microsoft-denali.dtsi', 1387, r'&usb_1_ss1_qmpphy'),
        ('x1-microsoft-denali.dtsi', 1394, r'&usb_1_ss1 \{'), ('x1-microsoft-denali.dtsi', 1395, r'dr_mode = "host"'),
        ('hamoa-pmics.dtsi', 669, r'smb2360_0: pmic@7'), ('hamoa-pmics.dtsi', 677, r'smb2360_0_eusb2_repeater: phy@fd00'),
        ('hamoa-pmics.dtsi', 678, r'qcom,smb2360-eusb2-repeater'), ('hamoa-pmics.dtsi', 684, r'smb2360_1: pmic@a'),
        ('hamoa-pmics.dtsi', 692, r'smb2360_1_eusb2_repeater: phy@fd00'), ('hamoa-pmics.dtsi', 693, r'qcom,smb2360-eusb2-repeater'),
        ('x1-microsoft-denali.dtsi', 1370, r'&usb_1_ss0_dwc3_hs'), ('x1-microsoft-denali.dtsi', 1371, r'pmic_glink_ss0_hs_in'),
        ('x1-microsoft-denali.dtsi', 1374, r'&usb_1_ss0_qmpphy_out'), ('x1-microsoft-denali.dtsi', 1375, r'retimer_ss0_ss_in'),
        ('x1-microsoft-denali.dtsi', 1400, r'&usb_1_ss1_dwc3_hs'), ('x1-microsoft-denali.dtsi', 1401, r'pmic_glink_ss1_hs_in'),
        ('x1-microsoft-denali.dtsi', 1404, r'&usb_1_ss1_qmpphy_out'), ('x1-microsoft-denali.dtsi', 1405, r'retimer_ss1_ss_in'),
        ('x1-microsoft-denali.dtsi', 1367, r'status = "okay"'), ('x1-microsoft-denali.dtsi', 1397, r'status = "okay"'),
        ('x1-microsoft-denali.dtsi', 54, r'qcom,x1e80100-pmic-glink'),
        ('ps883x.c', 26, r'REG_USB_PORT_CONN_STATUS_0\s+0x00'), ('ps883x.c', 98, r'ps883x_enable_vregs'),
        ('ucsi_glink.c', 25, r'UC_UCSI_READ_BUF_REQ\s+0x11'), ('ucsi_glink.c', 26, r'UC_UCSI_WRITE_BUF_REQ\s+0x12'),
        ('ucsi_glink.c', 27, r'UC_UCSI_USBC_NOTIFY_IND\s+0x13'),
        ('x1-el2.dtso', 55, r'&remoteproc_adsp'), ('x1-el2.dtso', 56, r'apps_smmu 0x1000 0x80'),
        ('dwc3-qcom.c', 41, r'SDM845_QSCRATCH_BASE_OFFSET\s+0xf8800'), ('dwc3-qcom.c', 42, r'SDM845_QSCRATCH_SIZE\s+0x400'),
        ('dwc3-qcom.c', 43, r'SDM845_DWC3_CORE_SIZE\s+0xcd00'), ('dwc3-qcom.c', 26, r'QSCRATCH_HS_PHY_CTRL\s+0x10'),
        ('dwc3-qcom.c', 30, r'QSCRATCH_SS_PHY_CTRL\s+0x30'), ('dwc3-qcom.c', 119, r'QSCRATCH_SS_PHY_CTRL'),
        ('dwc3-qcom.c', 121, r'QSCRATCH_HS_PHY_CTRL'), ('dwc3-qcom.c', 662, r'res.end = res.start \+ SDM845_QSCRATCH_BASE_OFFSET'),
        ('dwc3-qcom.c', 664, r'devm_ioremap\(dev, res.end, SDM845_QSCRATCH_SIZE\)'),
        ('core.h', 81, r'DWC3_GSNPSID_MASK'), ('core.h', 102, r'DWC3_GCTL\s+0xc110'), ('core.h', 106, r'DWC3_GSNPSID\s+0xc120'),
        ('core.h', 1293, r'DWC3_IP\s+0x5533'), ('core.h', 1294, r'DWC31_IP\s+0x3331'), ('core.h', 1295, r'DWC32_IP\s+0x3332'),
        ('pmic_glink.c', 285, r'PMIC_RTR_ADSP_APPS'), ('pmic_glink.c', 286, r'PMIC_RTR_SOCCP_APPS'),
        ('pmic_glink.c', 397, r'tms/servreg'), ('pmic_glink.c', 398, r'msm/adsp/charger_pd'),
        ('pmic_glink.c', 408, r'qcom,glymur-pmic-glink'), ('pmic_glink.c', 410, r'"qcom,pmic-glink".*pmic_glink_adsp_data'),
        ('ucsi_glink.c', 17, r'PMIC_GLINK_MAX_PORTS\s+3'), ('ucsi_glink.c', 221, r'gpiod_get_value'),
        ('phy-qcom-eusb2-repeater.c', 156, r'EUSB2_EN_CTL1, EUSB2_RPTR_EN'),
    )

    def test_device_tree_and_driver_citations_read_back(self):
        if not (REFS / 'hamoa.dtsi').is_file():
            self.skipTest('pinned Linux files not fetched under build/refs/')
        problems = []
        for name, line, pattern in self.DT_LINES:
            path = REFS / name
            if not path.is_file():
                problems.append((name, line, 'file not fetched'))
                continue
            lines = path.read_text(encoding='utf-8').splitlines()
            if not 0 < line <= len(lines) or not re.search(pattern, lines[line - 1]):
                problems.append((name, line, lines[line - 1].strip()[:60] if 0 < line <= len(lines) else 'beyond end'))
        self.assertEqual(problems, [])

    def test_the_tool_reference_table_matches_the_device_tree(self):
        if not (REFS / 'hamoa.dtsi').is_file():
            self.skipTest('pinned Linux files not fetched under build/refs/')
        lines = (REFS / 'hamoa.dtsi').read_text(encoding='utf-8').splitlines()
        denali = (REFS / 'x1-microsoft-denali.dtsi').read_text(encoding='utf-8').splitlines()
        for c in up.HAMOA_CONTROLLERS:
            first, last = (int(n) for n in c.node_lines.split('-'))
            self.assertRegex(lines[first - 1], rf'{c.dt_label}: usb@{c.dt_base:x}')
            self.assertRegex(lines[last - 1], rf'0x{c.dt_base:08x} 0 0x{c.dt_window:x}>')
            self.assertRegex(lines[c.iommu_line - 1], rf'apps_smmu 0x{c.iommu_stream:x} 0x0>')
            enabled = c.denali_lines
            if enabled:
                a, b = (int(n) for n in enabled.split('-'))
                self.assertRegex(denali[a - 1], rf'&{c.dt_label} \{{')
                self.assertRegex(' '.join(denali[a - 1:b]), r'dr_mode = "host".*status = "okay"')
            else:
                self.assertNotRegex('\n'.join(denali), rf'&{c.dt_label}\b')

    def test_every_line_the_study_cites_in_a_pinned_file_has_been_read_back(self):
        verified = {}
        for name, line, _ in self.DT_LINES:
            verified.setdefault(name, set()).add(line)
        pinned = {'hamoa-pmics.dtsi': 'hamoa-pmics.dtsi', 'hamoa.dtsi': 'hamoa.dtsi', 'x1-microsoft-denali.dtsi':
                  'x1-microsoft-denali.dtsi', 'denali': 'x1-microsoft-denali.dtsi', 'x1-el2.dtso': 'x1-el2.dtso',
                  'dwc3-qcom.c': 'dwc3-qcom.c', 'core.h': 'core.h', 'pmic_glink.c': 'pmic_glink.c',
                  'ucsi_glink.c': 'ucsi_glink.c', 'phy-qcom-eusb2-repeater.c': 'phy-qcom-eusb2-repeater.c',
                  'ps883x.c': 'ps883x.c'}
        token = re.compile(r'(?P<file>[A-Za-z0-9_.-]+)?:(?P<spans>\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*)')
        missing, unresolved = [], []
        for block in re.split(r'\n\s*\n', self.doc):
            last = None
            for line in block.splitlines():
                if re.match(r'\s*(\||[-*] |\d+\. )', line):
                    last = None                                       # a table row or list item starts afresh
                for m in token.finditer(line):
                    if m.group('file'):
                        last = pinned.get(m.group('file'))          # a q1n1 or unrelated name resets it
                        name = last
                    else:
                        name = last
                        if name is None and re.search(r'[`(]:\d', line[max(0, m.start() - 1):m.end()]):
                            unresolved.append(line.strip()[:60])
                    if name is None:
                        continue
                    for span in m.group('spans').split(','):
                        for n in (int(x) for x in span.split('-')):
                            if n not in verified.get(name, ()):
                                missing.append(f'{name}:{n}')
        self.assertEqual(sorted(set(missing)), [], 'cited in the study but not read back by DT_LINES')
        self.assertEqual(unresolved, [], 'a bare :line citation with no file in its paragraph')


class CrossLanguage(unittest.TestCase):
    """The C validator in q1n1 and the Python reference must agree, on the derived table and on generated faulty ones."""

    @classmethod
    def setUpClass(cls):
        # The C validator lives in the SP11 patch of q1n1: Q1N1_SP11_DIR names a tree that carries it, else Q1N1_DIR or
        # the sibling q1n1 is used if it does.
        cls.q1n1 = None
        for c in (os.environ.get('Q1N1_SP11_DIR'), os.environ.get('Q1N1_DIR'), ROOT.parent / 'q1n1'):
            if c and (Path(c) / 'platform/uefi/sp11-usb-ports.c').is_file() and (Path(c) / 'tools/test-q1n1-sp11-table.c').is_file():
                cls.q1n1 = Path(c)
                break
        cls.have_c = cls.q1n1 is not None and shutil.which('clang', path=ENV['PATH'])
        BUILD.mkdir(parents=True, exist_ok=True)

    def harness(self, extra_sources, name):
        if not self.have_c:
            self.skipTest('needs clang and a q1n1 checkout carrying the SP11 patch (Q1N1_SP11_DIR)')
        binary = BUILD / name
        done = subprocess.run(['clang', '-std=gnu11', '-O1', '-g', '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                               '-Wall', '-Wextra', '-Werror', f'-I{self.q1n1 / "platform/uefi"}', '-DSNAP_PLATFORM_SP11=1',
                               str(self.q1n1 / 'tools/test-q1n1-sp11-table.c'), str(self.q1n1 / 'platform/uefi/sp11-usb-ports.c')]
                              + [str(s) for s in extra_sources] + ['-o', str(binary)], env=ENV, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        return binary

    def test_the_emitted_table_passes_the_c_validator_and_carries_the_same_values(self):
        c_file = BUILD / 'emitted-table.c'
        c_file.write_text(up.emit_c(derive(proposal()), 'sp11.json', allow_unmeasured_id=True), encoding='utf-8')
        binary = self.harness([c_file], 'harness-emitted')
        done = subprocess.run([str(binary), 'emitted'], env=ENV, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        lines = done.stdout.splitlines()
        self.assertEqual(lines[:2], ['OK', 'count 2'])
        table = up.table_dict(derive(proposal()))
        for i, p in enumerate(table['ports']):
            self.assertEqual(lines[2 + i], f'port {i} base {p["dwc3_base"]:#x} window {p["window_size"]:#x} qscratch '
                                           f'{p["qscratch_offset"]:#x} gsnpsid {p["expect_gsnpsid"]:#x} flags {p["flags"]:#x}')

    def test_c_and_python_agree_on_generated_tables(self):
        c_file = BUILD / 'emitted-table.c'
        c_file.write_text(up.emit_c(derive(proposal()), 'sp11.json', allow_unmeasured_id=True), encoding='utf-8')
        binary = self.harness([c_file], 'harness-batch')
        base = up.table_dict(derive(proposal()))
        rng = random.Random(0x5011)
        interesting = [0, 1, 2, 3, 4, 0x400, 0xfff, 0x1000, 0x1001, 0xcd00, 0xf8800, 0xfd000, 0x1fffff, 0x200000, 0x201000,
                       0x0a600000, 0x0a800000, 0x0c000000, (1 << 36) - 0x1000, 1 << 36, (1 << 36) + 0x1000, (1 << 64) - 1,
                       0x5533330a, 0x33313130, 0x33323000, 0x12340000, 0xffffffff]
        tables = [copy.deepcopy(base)]
        for field in ('magic', 'version', 'count'):
            for v in interesting + [up.TABLE_MAGIC, up.TABLE_VERSION]:
                t = copy.deepcopy(base)
                t[field] = v if field == 'magic' else v & 0xffffffff
                tables.append(t)
        for index in (0, 1):
            for field in ('dwc3_base', 'window_size', 'qscratch_offset', 'expect_gsnpsid', 'flags'):
                for v in interesting + [1 << 5, 3]:
                    t = copy.deepcopy(base)
                    t['ports'][index][field] = v if field == 'dwc3_base' else v & 0xffffffff
                    tables.append(t)
        for _ in range(300):
            t = copy.deepcopy(base)
            for index in (0, 1):
                for field in ('dwc3_base', 'window_size', 'qscratch_offset', 'expect_gsnpsid', 'flags'):
                    if rng.random() < 0.35:
                        v = rng.choice(interesting)
                        t['ports'][index][field] = v if field == 'dwc3_base' else v & 0xffffffff
            if rng.random() < 0.2:
                t['count'] = rng.choice([0, 1, 2, 3])
            tables.append(t)
        text = ''
        for t in tables:
            numbers = [t['magic'], t['version'], t['count']]
            for p in t['ports']:
                numbers += [p['dwc3_base'], p['window_size'], p['qscratch_offset'], p['expect_gsnpsid'], p['flags']]
            text += ' '.join(f'{n:x}' for n in numbers) + '\n'
        done = subprocess.run([str(binary), 'batch'], input=text, env=ENV, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        verdicts = done.stdout.split()
        self.assertEqual(len(verdicts), len(tables))
        expected = [up.check_table(t) for t in tables]
        self.assertEqual([(i, a, b) for i, (a, b) in enumerate(zip(verdicts, expected)) if a != b], [])
        self.assertGreaterEqual(len(set(expected)), 12, 'the generated tables should reach every verdict')

    def test_c_and_python_agree_on_controller_judgement(self):
        c_file = BUILD / 'emitted-table.c'
        c_file.write_text(up.emit_c(derive(proposal()), 'sp11.json', allow_unmeasured_id=True), encoding='utf-8')
        binary = self.harness([c_file], 'harness-controller')
        ids = [0x5533330a, 0x33313130, 0x33323000, 0x12345678, 0, 0xffffffff, 0x5534330a]
        caps = [0x01100020, 0x01200030, 0x0110001c, 0x01100022, 0x00ff0020, 0x02000020, 0x01000040, 0xffffffff, 0]
        ports = [0x02000040, 0x00ffffff, 0x10000000, 0x11000000, 0x01000000, 0xffffffff]
        cases = [(e, i, c, p) for e in (0, 0x5533330a, 0x33313130) for i in ids for c in caps for p in ports]
        text = ''.join(' '.join(f'{n:x}' for n in case) + '\n' for case in cases)
        done = subprocess.run([str(binary), 'controller'], input=text, env=ENV, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        got = done.stdout.split()
        want = [up.check_controller(*case) for case in cases]
        self.assertEqual([(cases[i], a, b) for i, (a, b) in enumerate(zip(got, want)) if a != b], [])
        self.assertEqual(set(want), {'OK', 'FAMILY', 'CAPLENGTH', 'HCIVERSION', 'MAXPORTS'})


class Hygiene(unittest.TestCase):
    FILES = (Path(up.__file__), Path(__file__), DOC)

    def test_new_files_are_small_licensed_and_free_of_machine_paths(self):
        for path in self.FILES:
            self.assertTrue(path.is_file(), path)
            text = path.read_text(encoding='utf-8')
            self.assertLess(len(text.encode()), 50 * 1024, f'{path.name} is over 50 KiB')
            self.assertLess(len(text.splitlines()), 1500, f'{path.name} is over 1,500 lines')
            for prefix in HOME_PREFIXES:
                self.assertNotIn(prefix, text, path.name)
            if path.suffix == '.py':
                self.assertIn('SPDX-License-Identifier: MIT', '\n'.join(text.splitlines()[:3]), path.name)
            self.assertNotRegex(text, r'\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b', 'MAC-like string')

    def test_the_tool_never_writes_outside_the_paths_it_is_given(self):
        text = Path(up.__file__).read_text(encoding='utf-8')
        self.assertEqual(len(re.findall(r'\.write_text\(', text)), 2)       # propose --out and emit-c OUT only
        self.assertNotIn('tempfile', text)
        self.assertNotIn('subprocess', text)


if __name__ == '__main__':
    unittest.main(verbosity=1)
