#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Unit and integration tests for tools/stage-sp11-boot-media.py.

Runs entirely on synthetic PE stubs built in-process; never touches a real
disk, network, or executes any staged input. A handful of tests additionally
exercise the real local artifacts this agent built (slbounce-always.efi,
sltest.efi, the pinned UEFI shell) when they are present under the paths
this project's docs/BUILDING.md and docs/SURFACE-PRO-11.md describe; those
are skipped (not failed) when the artifacts are absent, matching this tree's
tools/suite.py SKIP convention.
"""
from pathlib import Path
import contextlib
import importlib.util
import io
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / 'tools' / 'stage-sp11-boot-media.py'
SCRATCH_ROOT = ROOT / 'build' / 'test-sp11-boot-media'

spec = importlib.util.spec_from_file_location('stage_sp11_boot_media', MODULE_PATH)
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)


# ---------------------------------------------------------------------------
# Synthetic PE builder. Every field is written explicitly (not copied from a
# real binary) so tests can independently prove the parser reads the same
# offsets the PE/COFF format defines, rather than merely echoing whatever a
# real toolchain happened to produce.
# ---------------------------------------------------------------------------

def _optional_header(magic, subsystem):
    fields = [
        ('<H', magic), ('<B', 0), ('<B', 0), ('<I', 0), ('<I', 0), ('<I', 0),
        ('<I', 0), ('<I', 0), ('<Q', 0), ('<I', 0x1000), ('<I', 0x200),
        ('<H', 0), ('<H', 0), ('<H', 0), ('<H', 0), ('<H', 0), ('<H', 0),
        ('<I', 0), ('<I', 0x2000), ('<I', 0x200), ('<I', 0), ('<H', subsystem), ('<H', 0),
    ]
    data = b''.join(struct.pack(fmt, val) for fmt, val in fields)
    assert len(data) == 72, len(data)  # Subsystem must land at byte 68 of this block
    return data


def make_pe(machine=0xAA64, subsystem=10, magic=0x20B, body=b'\x00' * 64, e_lfanew=64):
    # The 4-byte e_lfanew field occupies [0x3C, 0x40), so a 64-byte DOS header
    # already has room for it; the PE signature then starts right at offset
    # e_lfanew with no gap, matching what the real parser expects.
    dos = bytearray(max(e_lfanew, 64))
    dos[0:2] = b'MZ'
    struct.pack_into('<I', dos, 0x3C, e_lfanew)
    opt = _optional_header(magic, subsystem)
    file_header = struct.pack('<HHIIIHH', machine, 0, 0, 0, 0, len(opt), 0x0002)
    return bytes(dos) + b'PE\x00\x00' + file_header + opt + body


class TempTreeTestCase(unittest.TestCase):
    def setUp(self):
        SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
        self.scratch = Path(tempfile.mkdtemp(dir=str(SCRATCH_ROOT)))
        self.addCleanup(shutil.rmtree, self.scratch, ignore_errors=True)

    def write(self, relative, data):
        path = self.scratch / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path


# ---------------------------------------------------------------------------
# read_pe_header
# ---------------------------------------------------------------------------

class PEHeaderTests(unittest.TestCase):
    def test_valid_pe32plus_arm64(self):
        info = stage.read_pe_header(make_pe(machine=0xAA64, subsystem=10))
        self.assertEqual(info, {'ok': True, 'machine': 0xAA64, 'subsystem': 10, 'magic': 0x20B})

    def test_truncated_dos_header(self):
        info = stage.read_pe_header(make_pe()[:0x20])
        self.assertEqual(info, {'ok': False, 'reason': 'truncated_dos_header'})

    def test_bad_mz_signature(self):
        data = bytearray(make_pe())
        data[0:2] = b'XX'
        self.assertEqual(stage.read_pe_header(bytes(data))['reason'], 'bad_mz_signature')

    def test_bad_pe_offset_points_past_eof(self):
        data = bytearray(make_pe())
        struct.pack_into('<I', data, 0x3C, 0xFFFFFF00)
        self.assertEqual(stage.read_pe_header(bytes(data))['reason'], 'bad_pe_offset')

    def test_bad_pe_signature(self):
        data = bytearray(make_pe())
        data[64:68] = b'XXXX'
        self.assertEqual(stage.read_pe_header(bytes(data))['reason'], 'bad_pe_signature')

    def test_truncated_file_header(self):
        full = make_pe()
        truncated = full[:64 + 4 + 10]  # 10 of the required 20 file-header bytes
        self.assertEqual(stage.read_pe_header(truncated)['reason'], 'truncated_file_header')

    def test_truncated_optional_header_before_subsystem(self):
        full = make_pe()
        opt_header_off = 64 + 4 + 20
        truncated = full[:opt_header_off + 40]  # short of offset 68 (Subsystem)
        self.assertEqual(stage.read_pe_header(truncated)['reason'], 'truncated_optional_header')

    def test_unsupported_pe32_magic(self):
        info = stage.read_pe_header(make_pe(magic=0x10B))
        self.assertEqual(info['reason'], 'unsupported_pe_magic')

    def test_wrong_machine_is_readable_but_reported(self):
        info = stage.read_pe_header(make_pe(machine=0x8664))  # x86-64, not ARM64
        self.assertEqual(info, {'ok': True, 'machine': 0x8664, 'subsystem': 10, 'magic': 0x20B})


# ---------------------------------------------------------------------------
# validate_input_file
# ---------------------------------------------------------------------------

class InputValidationTests(TempTreeTestCase):
    def test_valid_shell_input(self):
        p = self.write('shell.efi', make_pe(subsystem=10))
        facts = stage.validate_input_file(p, 'shell')
        self.assertEqual(facts['machine'], 0xAA64)
        self.assertEqual(facts['subsystem'], 10)
        self.assertEqual(facts['sha256'], stage.sha256_file(p))

    def test_missing_input(self):
        with self.assertRaises(stage.Refusal) as ctx:
            stage.validate_input_file(self.scratch / 'nope.efi', 'shell')
        self.assertEqual(ctx.exception.reason, 'missing_input')

    def test_symlink_input_refused(self):
        real = self.write('real.efi', make_pe())
        link = self.scratch / 'link.efi'
        link.symlink_to(real)
        with self.assertRaises(stage.Refusal) as ctx:
            stage.validate_input_file(link, 'shell')
        self.assertEqual(ctx.exception.reason, 'input_is_symlink')

    def test_input_under_symlinked_directory_refused(self):
        real_dir = self.scratch / 'realdir'
        real_dir.mkdir()
        (real_dir / 'shell.efi').write_bytes(make_pe())
        link_dir = self.scratch / 'linkdir'
        link_dir.symlink_to(real_dir)
        with self.assertRaises(stage.Refusal) as ctx:
            stage.validate_input_file(link_dir / 'shell.efi', 'shell')
        self.assertEqual(ctx.exception.reason, 'input_path_has_symlink_component')

    def test_directory_as_input_refused(self):
        d = self.scratch / 'adir'
        d.mkdir()
        with self.assertRaises(stage.Refusal) as ctx:
            stage.validate_input_file(d, 'shell')
        self.assertEqual(ctx.exception.reason, 'input_not_regular_file')

    def test_empty_file_refused(self):
        p = self.write('empty.efi', b'')
        with self.assertRaises(stage.Refusal) as ctx:
            stage.validate_input_file(p, 'shell')
        self.assertEqual(ctx.exception.reason, 'input_empty_file')

    def test_oversize_input_refused(self):
        p = self.write('big.efi', make_pe(body=b'\x00' * (stage.MAX_EFI_BYTES + 1)))
        with self.assertRaises(stage.Refusal) as ctx:
            stage.validate_input_file(p, 'shell')
        self.assertEqual(ctx.exception.reason, 'input_too_large')

    def test_wrong_machine_refused(self):
        p = self.write('x86.efi', make_pe(machine=0x8664, subsystem=10))
        with self.assertRaises(stage.Refusal) as ctx:
            stage.validate_input_file(p, 'shell')
        self.assertEqual(ctx.exception.reason, 'input_wrong_machine')

    def test_role_subsystem_table(self):
        cases = [
            ('shell', 10, True), ('shell', 12, False),
            ('sltest', 10, True), ('sltest', 12, False),
            ('slbounce', 12, True), ('slbounce', 10, False), ('slbounce', 11, False),
            ('probe', 10, True), ('probe', 11, True), ('probe', 12, True), ('probe', 9, False),
        ]
        for role, subsystem, should_pass in cases:
            with self.subTest(role=role, subsystem=subsystem):
                p = self.write(f'{role}-{subsystem}.efi', make_pe(subsystem=subsystem))
                if should_pass:
                    stage.validate_input_file(p, role)
                else:
                    with self.assertRaises(stage.Refusal) as ctx:
                        stage.validate_input_file(p, role)
                    self.assertEqual(ctx.exception.reason, 'input_wrong_subsystem')

    def test_swapped_slbounce_sltest_both_refused(self):
        """Per-role subsystem checks alone catch an accidental --slbounce/--sltest swap."""
        slbounce_like = self.write('slbounce.efi', make_pe(subsystem=12))
        sltest_like = self.write('sltest.efi', make_pe(subsystem=10))
        stage.validate_input_file(slbounce_like, 'slbounce')
        stage.validate_input_file(sltest_like, 'sltest')
        with self.assertRaises(stage.Refusal):
            stage.validate_input_file(slbounce_like, 'sltest')
        with self.assertRaises(stage.Refusal):
            stage.validate_input_file(sltest_like, 'slbounce')

    def test_tcblaunch_only_checks_machine_not_subsystem(self):
        # tcblaunch.exe is a Windows boot application (subsystem 16 in slbounce's
        # own sl.c check); this tool deliberately never enforces a subsystem or
        # hash expectation for it (see module docstring and REPORT.md).
        p = self.write('tcblaunch.exe', make_pe(machine=0xAA64, subsystem=16))
        facts = stage.validate_input_file(p, 'tcblaunch')
        self.assertEqual(facts['subsystem'], 16)
        bad = self.write('tcblaunch-bad.exe', make_pe(machine=0x8664, subsystem=16))
        with self.assertRaises(stage.Refusal) as ctx:
            stage.validate_input_file(bad, 'tcblaunch')
        self.assertEqual(ctx.exception.reason, 'input_wrong_machine')


# ---------------------------------------------------------------------------
# check_destination_safety
# ---------------------------------------------------------------------------

class DestinationSafetyTests(TempTreeTestCase):
    def test_new_empty_dest_ok_for_stage(self):
        dest = self.scratch / 'dest'
        result = stage.check_destination_safety(dest, must_exist=False)
        self.assertEqual(result, dest.absolute())

    def test_existing_empty_dir_ok_for_stage(self):
        dest = self.scratch / 'dest'
        dest.mkdir()
        stage.check_destination_safety(dest, must_exist=False)

    def test_non_empty_dest_refused_for_stage(self):
        dest = self.scratch / 'dest'
        dest.mkdir()
        (dest / 'leftover').write_bytes(b'x')
        with self.assertRaises(stage.Refusal) as ctx:
            stage.check_destination_safety(dest, must_exist=False)
        self.assertEqual(ctx.exception.reason, 'dest_not_empty')

    def test_dest_parent_missing_refused(self):
        dest = self.scratch / 'nope' / 'dest'
        with self.assertRaises(stage.Refusal) as ctx:
            stage.check_destination_safety(dest, must_exist=False)
        self.assertEqual(ctx.exception.reason, 'dest_parent_missing')

    def test_dest_is_symlink_refused(self):
        real = self.scratch / 'real'
        real.mkdir()
        link = self.scratch / 'link'
        link.symlink_to(real)
        with self.assertRaises(stage.Refusal) as ctx:
            stage.check_destination_safety(link, must_exist=False)
        self.assertEqual(ctx.exception.reason, 'dest_is_symlink')

    def test_dest_path_has_symlink_component_refused(self):
        real_dir = self.scratch / 'realdir'
        real_dir.mkdir()
        link_dir = self.scratch / 'linkdir'
        link_dir.symlink_to(real_dir)
        with self.assertRaises(stage.Refusal) as ctx:
            stage.check_destination_safety(link_dir / 'dest', must_exist=False)
        self.assertEqual(ctx.exception.reason, 'dest_path_has_symlink_component')

    def test_dest_does_not_exist_refused_for_verify(self):
        with self.assertRaises(stage.Refusal) as ctx:
            stage.check_destination_safety(self.scratch / 'nope', must_exist=True)
        self.assertEqual(ctx.exception.reason, 'dest_does_not_exist')

    def test_dest_is_file_not_directory_refused(self):
        f = self.scratch / 'afile'
        f.write_bytes(b'x')
        with self.assertRaises(stage.Refusal) as ctx:
            stage.check_destination_safety(f, must_exist=True)
        self.assertEqual(ctx.exception.reason, 'dest_is_not_directory')

    def test_dest_under_volumes_refused(self):
        with self.assertRaises(stage.Refusal) as ctx:
            stage.check_destination_safety(Path('/Volumes/sp11-boot-media-test-stand-in'),
                                            must_exist=False)
        self.assertEqual(ctx.exception.reason, 'dest_under_volumes')

    def test_dest_under_dev_refused(self):
        with self.assertRaises(stage.Refusal) as ctx:
            stage.check_destination_safety(Path('/dev/sp11-boot-media-test-stand-in'),
                                            must_exist=False)
        self.assertEqual(ctx.exception.reason, 'dest_under_dev')

    def test_dest_is_mount_point_refused(self):
        dest = self.scratch / 'dest'
        dest.mkdir()
        with mock.patch('os.path.ismount', return_value=True):
            with self.assertRaises(stage.Refusal) as ctx:
                stage.check_destination_safety(dest, must_exist=True)
        self.assertEqual(ctx.exception.reason, 'dest_is_mount_point')

    def test_dest_different_filesystem_from_parent_refused(self):
        dest = self.scratch / 'dest'
        dest.mkdir()
        real_stat = os.stat

        class _Fake:
            def __init__(self, inner, dev):
                self._inner = inner
                self.st_dev = dev

            def __getattr__(self, name):
                return getattr(self._inner, name)

        def fake_stat(path, *a, **kw):
            result = real_stat(path, *a, **kw)
            if Path(path) == dest:
                return _Fake(result, result.st_dev + 1)
            return result

        # os.path.ismount neutralized here so this isolates the explicit
        # st_dev comparison from the (usually overlapping) mount-point check.
        with mock.patch('os.path.ismount', return_value=False), \
             mock.patch('os.stat', side_effect=fake_stat):
            with self.assertRaises(stage.Refusal) as ctx:
                stage.check_destination_safety(dest, must_exist=True)
        self.assertEqual(ctx.exception.reason, 'dest_different_filesystem_from_parent')


# ---------------------------------------------------------------------------
# manifest path-traversal guard
# ---------------------------------------------------------------------------

class PathSafetyTests(unittest.TestCase):
    def test_safe_relative_paths_accepted(self):
        for name in ('SP11/SLBOUNCE.EFI', 'EFI/BOOT/BOOTAA64.EFI', 'a'):
            self.assertTrue(stage.is_safe_relative_path(name), name)

    def test_unsafe_relative_paths_rejected(self):
        for name in ('../../etc/passwd', '/etc/passwd', 'SP11/../../etc/passwd',
                     '~/.ssh/id_rsa', 'a\\b', '', 'a/./b', 'a//b'.replace('//', '/./'),
                     'a/..'):
            self.assertFalse(stage.is_safe_relative_path(name), name)


# ---------------------------------------------------------------------------
# End-to-end stage/verify round trips
# ---------------------------------------------------------------------------

class _Args:
    """Plain namespace standing in for argparse.Namespace in direct cmd_* calls.

    A call that gives no pins (pins=None or absent) stands in for the command line's --no-pins; one that gives a
    pins path stands in for --pins PATH. test-sp11-boot-media-hardening.py covers the refusals.
    """
    def __init__(self, **kw):
        kw.setdefault('pins', None)
        kw.setdefault('no_pins', kw['pins'] is None)
        self.__dict__.update(kw)


class StageVerifyEndToEndTests(TempTreeTestCase):
    def make_inputs(self):
        return {
            'shell': self.write('in/shell.efi', make_pe(subsystem=10)),
            'slbounce': self.write('in/slbounce.efi', make_pe(subsystem=12)),
            'sltest': self.write('in/sltest.efi', make_pe(subsystem=10)),
        }

    def test_stage_without_tcblaunch_is_incomplete_but_succeeds(self):
        inputs = self.make_inputs()
        dest = self.scratch / 'out'
        args = _Args(shell=inputs['shell'], slbounce=inputs['slbounce'], sltest=inputs['sltest'],
                      probe=None, tcblaunch=None, dest=dest, pins=None)
        result = stage.cmd_stage(args)
        self.assertEqual(result['status'], 'STAGED_INCOMPLETE')
        self.assertEqual(result['missing'], ['tcblaunch'])
        self.assertFalse((dest / 'SP11' / 'TCBLAUNCH.EXE').exists())
        # No tcblaunch input means no root-level duplicate either: nothing
        # should appear at the media root beyond EFI/ and SP11/.
        self.assertFalse((dest / 'TCBLAUNCH.EXE').exists())
        self.assertTrue((dest / 'EFI' / 'BOOT' / 'BOOTAA64.EFI').exists())
        self.assertTrue((dest / 'SP11' / 'README.TXT').exists())
        self.assertTrue((dest / 'SP11' / 'MANIFEST.SHA256').exists())
        # README.TXT must be plain ASCII (enforced again here at the file level,
        # independent of build_readme_text()'s own internal assertion).
        (dest / 'SP11' / 'README.TXT').read_text(encoding='ascii')

    def test_stage_with_tcblaunch_and_probe_is_complete(self):
        inputs = self.make_inputs()
        tcblaunch = self.write('in/tcblaunch.exe', make_pe(machine=0xAA64, subsystem=16))
        probe = self.write('in/probe.efi', make_pe(subsystem=10))
        dest = self.scratch / 'out'
        args = _Args(shell=inputs['shell'], slbounce=inputs['slbounce'], sltest=inputs['sltest'],
                      probe=probe, tcblaunch=tcblaunch, dest=dest, pins=None)
        result = stage.cmd_stage(args)
        self.assertEqual(result['status'], 'STAGED_COMPLETE')
        self.assertEqual(result['missing'], [])
        self.assertTrue((dest / 'SP11' / 'PROBE.EFI').exists())
        self.assertTrue((dest / 'SP11' / 'TCBLAUNCH.EXE').exists())
        # slbounce.efi's real driver (bounce_main.c) hard-codes looking up
        # tcblaunch.exe at the media root, not under SP11/: `stage` must
        # duplicate the same input bytes there too (see TCBLAUNCH_ROOT_REL).
        root_copy = dest / 'TCBLAUNCH.EXE'
        self.assertTrue(root_copy.exists())
        self.assertEqual(root_copy.read_bytes(), tcblaunch.read_bytes())
        self.assertIn('TCBLAUNCH.EXE', result['files_written'])

    def test_verify_matches_freshly_staged_tree(self):
        inputs = self.make_inputs()
        tcblaunch = self.write('in/tcblaunch.exe', make_pe(machine=0xAA64, subsystem=16))
        dest = self.scratch / 'out'
        stage.cmd_stage(_Args(shell=inputs['shell'], slbounce=inputs['slbounce'],
                               sltest=inputs['sltest'], probe=None, tcblaunch=tcblaunch,
                               dest=dest, pins=None))
        result = stage.cmd_verify(_Args(dest=dest, allow_incomplete=False))
        self.assertEqual(result['status'], 'COMPLETE')
        self.assertTrue(result['manifest_ok'])
        self.assertEqual(result['manifest_mismatches'], [])
        self.assertEqual(result['startup_nsh_found'], [])
        # The manifest must cover the root-level tcblaunch.exe duplicate by
        # name, proving it is a tracked, verified file and not a silent
        # by-product `verify` simply fails to notice either way.
        manifest_text = (dest / 'SP11' / 'MANIFEST.SHA256').read_text()
        self.assertIn('  TCBLAUNCH.EXE\n', manifest_text)
        self.assertIn('  SP11/TCBLAUNCH.EXE\n', manifest_text)

    def test_verify_detects_root_tcblaunch_removed(self):
        inputs = self.make_inputs()
        tcblaunch = self.write('in/tcblaunch.exe', make_pe(machine=0xAA64, subsystem=16))
        dest = self.scratch / 'out'
        stage.cmd_stage(_Args(shell=inputs['shell'], slbounce=inputs['slbounce'],
                               sltest=inputs['sltest'], probe=None, tcblaunch=tcblaunch,
                               dest=dest, pins=None))
        (dest / 'TCBLAUNCH.EXE').unlink()
        result = stage.cmd_verify(_Args(dest=dest, allow_incomplete=False))
        self.assertEqual(result['status'], 'FAIL')
        self.assertIn('manifest_listed_file_missing:TCBLAUNCH.EXE', result['manifest_mismatches'])

    def test_verify_detects_root_tcblaunch_tamper(self):
        inputs = self.make_inputs()
        tcblaunch = self.write('in/tcblaunch.exe', make_pe(machine=0xAA64, subsystem=16))
        dest = self.scratch / 'out'
        stage.cmd_stage(_Args(shell=inputs['shell'], slbounce=inputs['slbounce'],
                               sltest=inputs['sltest'], probe=None, tcblaunch=tcblaunch,
                               dest=dest, pins=None))
        target = dest / 'TCBLAUNCH.EXE'
        data = bytearray(target.read_bytes())
        data[-1] ^= 0xFF
        target.write_bytes(bytes(data))
        result = stage.cmd_verify(_Args(dest=dest, allow_incomplete=False))
        self.assertEqual(result['status'], 'FAIL')
        self.assertIn('manifest_hash_mismatch:TCBLAUNCH.EXE', result['manifest_mismatches'])
        # The SP11/ copy is untouched, so this must be the only mismatch:
        # proves the two copies are verified independently, not conflated.
        self.assertNotIn('manifest_hash_mismatch:SP11/TCBLAUNCH.EXE', result['manifest_mismatches'])

    def test_verify_incomplete_without_tcblaunch(self):
        inputs = self.make_inputs()
        dest = self.scratch / 'out'
        stage.cmd_stage(_Args(shell=inputs['shell'], slbounce=inputs['slbounce'],
                               sltest=inputs['sltest'], probe=None, tcblaunch=None,
                               dest=dest, pins=None))
        result = stage.cmd_verify(_Args(dest=dest, allow_incomplete=False))
        self.assertEqual(result['status'], 'INCOMPLETE')
        self.assertEqual(result['missing'], ['tcblaunch'])

    def test_verify_detects_one_byte_tamper(self):
        inputs = self.make_inputs()
        tcblaunch = self.write('in/tcblaunch.exe', make_pe(machine=0xAA64, subsystem=16))
        dest = self.scratch / 'out'
        stage.cmd_stage(_Args(shell=inputs['shell'], slbounce=inputs['slbounce'],
                               sltest=inputs['sltest'], probe=None, tcblaunch=tcblaunch,
                               dest=dest, pins=None))
        target = dest / 'SP11' / 'SLTEST.EFI'
        data = bytearray(target.read_bytes())
        data[-1] ^= 0xFF
        target.write_bytes(bytes(data))
        result = stage.cmd_verify(_Args(dest=dest, allow_incomplete=False))
        self.assertEqual(result['status'], 'FAIL')
        self.assertIn('manifest_hash_mismatch:SP11/SLTEST.EFI', result['manifest_mismatches'])

    def test_verify_detects_unlisted_extra_file(self):
        inputs = self.make_inputs()
        tcblaunch = self.write('in/tcblaunch.exe', make_pe(machine=0xAA64, subsystem=16))
        dest = self.scratch / 'out'
        stage.cmd_stage(_Args(shell=inputs['shell'], slbounce=inputs['slbounce'],
                               sltest=inputs['sltest'], probe=None, tcblaunch=tcblaunch,
                               dest=dest, pins=None))
        (dest / 'SP11' / 'EXTRA.BIN').write_bytes(b'surprise')
        result = stage.cmd_verify(_Args(dest=dest, allow_incomplete=False))
        self.assertEqual(result['status'], 'FAIL')
        self.assertIn('manifest_unlisted_extra_file:SP11/EXTRA.BIN', result['manifest_mismatches'])

    def test_verify_detects_manifest_entry_path_traversal(self):
        inputs = self.make_inputs()
        tcblaunch = self.write('in/tcblaunch.exe', make_pe(machine=0xAA64, subsystem=16))
        dest = self.scratch / 'out'
        stage.cmd_stage(_Args(shell=inputs['shell'], slbounce=inputs['slbounce'],
                               sltest=inputs['sltest'], probe=None, tcblaunch=tcblaunch,
                               dest=dest, pins=None))
        manifest = dest / 'SP11' / 'MANIFEST.SHA256'
        manifest.write_text(manifest.read_text() +
                             'a' * 64 + '  ../../../etc/passwd\n')
        result = stage.cmd_verify(_Args(dest=dest, allow_incomplete=False))
        self.assertEqual(result['status'], 'FAIL')
        self.assertTrue(any(m.startswith('manifest_entry_path_traversal:')
                             for m in result['manifest_mismatches']))

    def test_verify_flags_startup_nsh(self):
        inputs = self.make_inputs()
        tcblaunch = self.write('in/tcblaunch.exe', make_pe(machine=0xAA64, subsystem=16))
        dest = self.scratch / 'out'
        stage.cmd_stage(_Args(shell=inputs['shell'], slbounce=inputs['slbounce'],
                               sltest=inputs['sltest'], probe=None, tcblaunch=tcblaunch,
                               dest=dest, pins=None))
        nsh_bytes = b'echo hi\n'
        (dest / 'EFI' / 'BOOT' / 'startup.nsh').write_bytes(nsh_bytes)
        # Also list startup.nsh in the manifest with its correct hash, so the
        # unlisted-extra-file check cannot independently cause FAIL: this
        # isolates the startup.nsh scan itself as the thing under test (a
        # mutation that disabled only the startup_hits gate was otherwise
        # indistinguishable from a working one, since any stray new file is
        # *also* caught as an unlisted manifest entry).
        manifest = dest / 'SP11' / 'MANIFEST.SHA256'
        digest = stage.sha256_bytes(nsh_bytes)
        manifest.write_text(manifest.read_text() + f'{digest}  EFI/BOOT/startup.nsh\n')
        result = stage.cmd_verify(_Args(dest=dest, allow_incomplete=False))
        self.assertEqual(result['manifest_mismatches'], [])
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(result['startup_nsh_found'], ['EFI/BOOT/startup.nsh'])

    def test_stage_never_creates_startup_nsh(self):
        inputs = self.make_inputs()
        dest = self.scratch / 'out'
        stage.cmd_stage(_Args(shell=inputs['shell'], slbounce=inputs['slbounce'],
                               sltest=inputs['sltest'], probe=None, tcblaunch=None,
                               dest=dest, pins=None))
        hits = [p for p in dest.rglob('*') if p.is_file() and p.name.lower() == 'startup.nsh']
        self.assertEqual(hits, [])

    def test_determinism_across_two_independent_stagings(self):
        inputs = self.make_inputs()
        tcblaunch = self.write('in/tcblaunch.exe', make_pe(machine=0xAA64, subsystem=16))
        dest_a, dest_b = self.scratch / 'out-a', self.scratch / 'out-b'
        for dest in (dest_a, dest_b):
            stage.cmd_stage(_Args(shell=inputs['shell'], slbounce=inputs['slbounce'],
                                   sltest=inputs['sltest'], probe=None, tcblaunch=tcblaunch,
                                   dest=dest, pins=None))
        files_a = sorted(p.relative_to(dest_a).as_posix() for p in dest_a.rglob('*') if p.is_file())
        files_b = sorted(p.relative_to(dest_b).as_posix() for p in dest_b.rglob('*') if p.is_file())
        self.assertEqual(files_a, files_b)
        for rel in files_a:
            pa, pb = dest_a / rel, dest_b / rel
            self.assertEqual(pa.read_bytes(), pb.read_bytes(), rel)
            sa, sb = pa.stat(), pb.stat()
            self.assertEqual(stat_mode_bits(sa), stat_mode_bits(sb), rel)
            self.assertEqual(sa.st_mtime_ns, sb.st_mtime_ns, rel)
            self.assertEqual(sa.st_mtime_ns, 0, rel)  # fixed DETERMINISTIC_EPOCH

    def test_shell_hash_pin_mismatch_refused(self):
        inputs = self.make_inputs()
        pins_path = self.write('pins.json', json.dumps(
            {'shell': {'sha256': '0' * 64}}).encode())
        dest = self.scratch / 'out'
        args = _Args(shell=inputs['shell'], slbounce=inputs['slbounce'], sltest=inputs['sltest'],
                      probe=None, tcblaunch=None, dest=dest, pins=pins_path)
        with self.assertRaises(stage.Refusal) as ctx:
            stage.cmd_stage(args)
        self.assertEqual(ctx.exception.reason, 'input_hash_pin_mismatch')
        self.assertFalse(dest.exists())

    def test_shell_hash_pin_match_succeeds(self):
        inputs = self.make_inputs()
        real_sha = stage.sha256_file(inputs['shell'])
        pins_path = self.write('pins.json', json.dumps(
            {'shell': {'sha256': real_sha}}).encode())
        dest = self.scratch / 'out'
        result = stage.cmd_stage(_Args(shell=inputs['shell'], slbounce=inputs['slbounce'],
                                        sltest=inputs['sltest'], probe=None, tcblaunch=None,
                                        dest=dest, pins=pins_path))
        self.assertEqual(result['status'], 'STAGED_INCOMPLETE')  # still no tcblaunch

    def test_slbounce_reference_mismatch_is_advisory_not_refused(self):
        inputs = self.make_inputs()
        pins_path = self.write('pins.json', json.dumps(
            {'shell': {'sha256': stage.sha256_file(inputs['shell'])},
             'slbounce_reference': {'sha256': '0' * 64}}).encode())
        dest = self.scratch / 'out'
        result = stage.cmd_stage(_Args(shell=inputs['shell'], slbounce=inputs['slbounce'],
                                        sltest=inputs['sltest'], probe=None, tcblaunch=None,
                                        dest=dest, pins=pins_path))
        self.assertEqual(result['status'], 'STAGED_INCOMPLETE')
        self.assertTrue(any('slbounce' in a for a in result['advisories']))

    def test_real_shipped_pins_file_is_well_formed(self):
        pins_path = ROOT / 'tools' / 'sp11-boot-media-pins.json'
        pins = json.loads(pins_path.read_text())
        self.assertRegex(pins['shell']['sha256'], r'^[0-9a-f]{64}$')
        self.assertEqual(pins['shell']['size'], 1105920)
        for key in ('slbounce_reference', 'sltest_reference'):
            self.assertRegex(pins[key]['sha256'], r'^[0-9a-f]{64}$')


def stat_mode_bits(st):
    import stat as statmod
    return statmod.S_IMODE(st.st_mode)


# ---------------------------------------------------------------------------
# Generated operator README text
# ---------------------------------------------------------------------------

class ReadmeTextTests(unittest.TestCase):
    """slbounce's test_main.c calls GetVolume(ImageHandle) then
    FileOpen(volume, argv[1]) directly (src/util.c): argv[1] is opened
    against the same filesystem SLTEST.EFI itself was loaded from, with no
    Shell drive-letter resolution involved. A "fs0:\\..." argument is a
    plain string FAT/EFI_FILE_PROTOCOL.Open() cannot resolve to a real path,
    so it fails closed ("Opening file failed.") rather than running the
    self-test — not a hard hang, but a silently useless invocation. This is
    a regression guard for that exact wording.
    """

    def test_readme_is_ascii(self):
        stage.build_readme_text().encode('ascii')

    def test_readme_uses_volume_relative_sltest_invocation(self):
        text = stage.build_readme_text()
        self.assertIn('SP11\\SLTEST.EFI SP11\\TCBLAUNCH.EXE', text)
        self.assertNotIn('fs0:\\SP11\\TCBLAUNCH.EXE', text)

    def test_readme_never_mentions_creating_startup_nsh(self):
        text = stage.build_readme_text()
        self.assertIn('No startup.nsh file is present', text)
        self.assertNotIn('create startup.nsh', text.lower())

    def test_readme_documents_root_tcblaunch_duplicate(self):
        # Regression guard for the bounce_main.c:213,219 / util.c GetVolume()
        # fact: slbounce.efi's real driver only ever opens tcblaunch.exe at
        # the media root, never SP11\tcblaunch.exe, so the README must both
        # list the root copy and explain it is automatic (the operator must
        # not be told to create it by hand, nor be left unaware it exists).
        text = stage.build_readme_text()
        self.assertIn('  TCBLAUNCH.EXE            (media root)', text)
        self.assertIn('placed here by the staging tool, not by you', text)
        self.assertIn('load SP11\\SLBOUNCE.EFI', text)
        self.assertIn('always opens plain "tcblaunch.exe" at this media\'s\n'
                       '     root', text)


# ---------------------------------------------------------------------------
# main() / CLI-level tests
# ---------------------------------------------------------------------------

class MainCLITests(TempTreeTestCase):
    def run_main(self, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = stage.main(argv)
        return code, buf.getvalue()

    def test_cli_stage_then_verify_incomplete_then_allow_incomplete(self):
        shell = self.write('shell.efi', make_pe(subsystem=10))
        slbounce = self.write('slbounce.efi', make_pe(subsystem=12))
        sltest = self.write('sltest.efi', make_pe(subsystem=10))
        dest = self.scratch / 'out'
        code, out = self.run_main(['stage', '--shell', str(shell), '--slbounce', str(slbounce),
                                    '--sltest', str(sltest), '--dest', str(dest), '--no-pins'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['status'], 'STAGED_INCOMPLETE')

        code, out = self.run_main(['verify', '--dest', str(dest), '--no-pins'])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)['status'], 'INCOMPLETE')

        code, out = self.run_main(['verify', '--dest', str(dest), '--allow-incomplete', '--no-pins'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['status'], 'INCOMPLETE')

    def test_cli_refusal_exits_nonzero_with_json_reason(self):
        dest = self.scratch / 'out'
        code, out = self.run_main(['verify', '--dest', str(dest)])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)['status'], 'REFUSED')
        self.assertEqual(json.loads(out)['reason'], 'dest_does_not_exist')


# ---------------------------------------------------------------------------
# Optional integration tests against this agent's real local build artifacts.
# These SKIP (not FAIL) when the artifact is absent, per tools/suite.py's
# SKIP convention (a printed "SKIP: ..." line ahead of skipTest()).
# ---------------------------------------------------------------------------

def _first_existing(*candidates):
    for c in candidates:
        if c and Path(c).is_file():
            return Path(c)
    return None


class RealArtifactIntegrationTests(TempTreeTestCase):
    def test_stage_real_artifacts_if_present(self):
        shell = _first_existing(os.environ.get('SP11_SHELL_EFI'),
                                 ROOT / 'downloads' / 'sp11' / 'boot-media' / 'shellaa64.efi')
        slbounce = _first_existing(os.environ.get('SP11_SLBOUNCE_EFI'),
                                    ROOT / 'build' / 'uefi' / 'slbounce-always.efi')
        sltest = _first_existing(os.environ.get('SP11_SLTEST_EFI'),
                                  ROOT / 'build' / 'uefi' / 'sltest.efi')
        if not (shell and slbounce and sltest):
            print('SKIP: real shell/slbounce/sltest artifacts not found '
                  '(set SP11_SHELL_EFI/SP11_SLBOUNCE_EFI/SP11_SLTEST_EFI or build them '
                  'per docs/BUILDING.md)')
            self.skipTest('real artifacts not found')
        probe = _first_existing(os.environ.get('SP11_PROBE_EFI'),
                                 ROOT / 'build' / 'uefi' / 'sp11-el2-probe.efi')
        if probe is None:
            print('SKIP: optional sp11-el2-probe.efi not found (uefi-probe agent artifact; '
                  'staging without it is still exercised below)')
        dest = self.scratch / 'out'
        args = _Args(shell=shell, slbounce=slbounce, sltest=sltest, probe=probe,
                      tcblaunch=None, dest=dest, pins=ROOT / 'tools' / 'sp11-boot-media-pins.json')
        result = stage.cmd_stage(args)
        self.assertEqual(result['missing'], ['tcblaunch'])
        verify_result = stage.cmd_verify(_Args(dest=dest, allow_incomplete=True))
        self.assertEqual(verify_result['status'], 'INCOMPLETE')
        self.assertEqual(verify_result['manifest_mismatches'], [])


if __name__ == '__main__':
    unittest.main()
