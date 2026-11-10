#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Refusal tests for the destination guard, the pins file and the validated bytes of tools/stage-sp11-boot-media.py.

Each case provokes one fault that the stager used to let through:

* a `--dest` that reaches /Volumes or /dev through `..`, a different letter case, the Data-volume firmlink or a mount
  point above it (the lexical, case-sensitive prefix test only saw the exact spelling);
* a `--pins` file that is missing, a directory, a FIFO, a symbolic link or unusable (read as "no pins", which switched
  the only hard pin on the UEFI shell off while `stage` exited 0 and `verify` reported COMPLETE);
* an input replaced between its validation and its staging (the file was read twice).

Synthetic PE stubs only; nothing reaches a disk, the network or hardware. The fixtures are those of
tools/test-sp11-boot-media.py, which is loaded for them.

    python3 tools/test-sp11-boot-media-hardening.py
"""
from pathlib import Path
from unittest import mock
import contextlib
import importlib.util
import io
import json
import os
import sys
import unittest

TOOLS = Path(__file__).resolve().parent
sys.dont_write_bytecode = True

spec = importlib.util.spec_from_file_location('boot_media_fixtures', TOOLS / 'test-sp11-boot-media.py')
T = importlib.util.module_from_spec(spec)
spec.loader.exec_module(T)
stage, make_pe, _Args = T.stage, T.make_pe, T._Args

DATA_VOLUMES = Path('/System/Volumes/Data/Volumes')


class Case(T.TempTreeTestCase):
    def inputs(self):
        return {'shell': self.write('in/shell.efi', make_pe(subsystem=10)),
                'slbounce': self.write('in/slbounce.efi', make_pe(subsystem=12)),
                'sltest': self.write('in/sltest.efi', make_pe(subsystem=10))}

    def stage_args(self, dest, **extra):
        inputs = self.inputs()
        return _Args(shell=inputs['shell'], slbounce=inputs['slbounce'], sltest=inputs['sltest'], probe=None,
                     tcblaunch=None, dest=dest, **extra)

    def refusal(self, call, *args, **kwargs):
        with self.assertRaises(stage.Refusal) as caught:
            call(*args, **kwargs)
        return caught.exception.reason

    def pins_file(self, document, name='pins.json'):
        return self.write(name, json.dumps(document).encode())


class DestinationGuard(Case):
    def test_a_parent_reference_is_refused_whatever_it_resolves_to(self):
        (self.scratch / 'a').mkdir()
        spellings = (self.scratch / 'a' / '..' / 'dest', Path('/private/tmp/../../Volumes/sp11-test-stand-in'),
                     Path('/tmp/../Volumes/sp11-test-stand-in'), Path('../sp11-test-stand-in'),
                     Path('/dev/../dev/sp11-test-stand-in'))
        for must_exist in (False, True):
            for dest in spellings:
                with self.subTest(dest=str(dest), must_exist=must_exist):
                    self.assertEqual(self.refusal(stage.check_destination_safety, dest, must_exist),
                                     'dest_has_parent_reference')

    def test_volumes_and_dev_are_refused_in_any_letter_case(self):
        for text, reason in (('/volumes/sp11-test-stand-in', 'dest_under_volumes'),
                             ('/VOLUMES/sp11-test-stand-in', 'dest_under_volumes'),
                             ('/DEV/sp11-test-stand-in', 'dest_under_dev'),
                             ('/Dev/sp11-test-stand-in', 'dest_under_dev')):
            with self.subTest(dest=text):
                self.assertEqual(self.refusal(stage.check_destination_safety, Path(text), False), reason)

    def test_the_spelling_check_alone_ignores_letter_case(self):
        # When no identity comparison is possible the spelling must still catch every letter case.
        with mock.patch.object(stage.os.path, 'samefile', side_effect=OSError('no identity available')):
            for text, reason in (('/VOLUMES/sp11-test-stand-in', 'dest_under_volumes'),
                                 ('/vOlUmEs/sp11-test-stand-in', 'dest_under_volumes'),
                                 ('/DEV/sp11-test-stand-in', 'dest_under_dev'),
                                 ('/dEv/sp11-test-stand-in', 'dest_under_dev')):
                with self.subTest(dest=text):
                    self.assertEqual(self.refusal(stage.check_destination_safety, Path(text), False), reason)

    def test_the_data_volume_spelling_of_volumes_is_refused(self):
        if not DATA_VOLUMES.is_dir() or not os.path.samefile(DATA_VOLUMES, '/Volumes'):
            print('SKIP: /System/Volumes/Data/Volumes is not this host\'s firmlink to /Volumes')
            self.skipTest('no Data-volume firmlink for /Volumes')
        self.assertEqual(self.refusal(stage.check_destination_safety, DATA_VOLUMES / 'sp11-test-stand-in', False),
                         'dest_under_volumes')

    def test_identity_catches_a_directory_that_is_spelled_differently(self):
        protected = self.scratch / 'protected-root'
        protected.mkdir()
        (protected / 'child').mkdir()
        with mock.patch.object(stage, 'PROTECTED_ROOTS', (('/nonexistent-lexical-root', 'dest_under_volumes'),)), \
                mock.patch.object(stage.os.path, 'samefile',
                                  side_effect=lambda a, b: Path(a) == protected):
            self.assertEqual(self.refusal(stage.check_destination_safety, protected / 'child' / 'dest', False),
                             'dest_under_volumes')

    def test_a_symlink_to_volumes_is_refused(self):
        link = self.scratch / 'volumes-link'
        link.symlink_to('/Volumes')
        self.assertEqual(self.refusal(stage.check_destination_safety, link / 'sp11-test-stand-in', False),
                         'dest_path_has_symlink_component')

    def test_a_mount_point_above_the_destination_is_refused(self):
        mount = self.scratch / 'mnt'
        (mount / 'sub').mkdir(parents=True)
        real = os.path.ismount
        with mock.patch.object(stage.os.path, 'ismount',
                               side_effect=lambda p: Path(p) == mount or real(p)):
            for must_exist, dest in ((False, mount / 'sub' / 'dest'), (True, mount / 'sub')):
                with self.subTest(dest=str(dest)):
                    self.assertEqual(self.refusal(stage.check_destination_safety, dest, must_exist),
                                     'dest_ancestor_is_mount_point')

    def test_an_ordinary_scratch_destination_is_still_accepted(self):
        dest = self.scratch / 'out' / 'dest'
        (self.scratch / 'out').mkdir()
        self.assertEqual(stage.check_destination_safety(dest, False), dest.absolute())

    def test_a_refused_destination_is_refused_before_anything_is_created(self):
        (self.scratch / 'a').mkdir()
        dest = self.scratch / 'a' / '..' / 'out'
        args = self.stage_args(dest, no_pins=True)
        self.assertEqual(self.refusal(stage.cmd_stage, args), 'dest_has_parent_reference')
        self.assertFalse((self.scratch / 'out').exists())


class PinsFile(Case):
    def test_a_pins_file_that_cannot_be_used_is_refused_and_nothing_is_staged(self):
        directory = self.scratch / 'pins-dir'
        directory.mkdir()
        fifo = self.scratch / 'pins.fifo'
        os.mkfifo(fifo)
        good = self.pins_file({'shell': {'sha256': '0' * 64}}, 'good.json')
        link = self.scratch / 'pins-link.json'
        link.symlink_to(good)
        oversize = self.write('big.json', b'{"x": "' + b'a' * stage.PINS_MAX_BYTES + b'"}')
        cases = {
            'absent': (self.scratch / 'absent.json', 'pins_missing'),
            'directory': (directory, 'pins_not_regular_file'),
            'fifo': (fifo, 'pins_not_regular_file'),
            'symlink': (link, 'pins_not_regular_file'),
            'too large': (oversize, 'pins_too_large'),
            'empty': (self.write('empty.json', b''), 'pins_malformed'),
            'not json': (self.write('text.json', b'shell: yes'), 'pins_malformed'),
            'not UTF-8': (self.write('bytes.json', b'\xff\xfe{}'), 'pins_malformed'),
            'too deeply nested': (self.write('deep.json', b'[' * 100000), 'pins_malformed'),
            'a list': (self.pins_file([{'shell': {'sha256': '0' * 64}}], 'list.json'), 'pins_shell_pin_invalid'),
            'no shell entry': (self.pins_file({'slbounce_reference': {'sha256': '0' * 64}}, 'noshell.json'),
                               'pins_shell_pin_invalid'),
            'empty shell entry': (self.pins_file({'shell': {}}, 'emptyshell.json'), 'pins_shell_pin_invalid'),
            'null shell entry': (self.pins_file({'shell': None}, 'nullshell.json'), 'pins_shell_pin_invalid'),
            'shell hash not text': (self.pins_file({'shell': {'sha256': 7}}, 'num.json'), 'pins_shell_pin_invalid'),
            'shell hash upper case': (self.pins_file({'shell': {'sha256': 'A' * 64}}, 'upper.json'),
                                      'pins_shell_pin_invalid'),
            'shell hash short': (self.pins_file({'shell': {'sha256': 'a' * 63}}, 'short.json'),
                                 'pins_shell_pin_invalid'),
        }
        for name, (path, reason) in cases.items():
            dest = self.scratch / f'out-{name.replace(" ", "-")}'
            with self.subTest(name):
                self.assertEqual(self.refusal(stage.cmd_stage, self.stage_args(dest, pins=path, no_pins=False)), reason)
                self.assertFalse(dest.exists())

    def test_giving_no_pins_and_not_asking_for_none_is_refused(self):
        dest = self.scratch / 'out'
        reason = self.refusal(stage.cmd_stage, self.stage_args(dest, pins=None, no_pins=False))
        self.assertEqual(reason, 'pins_required')
        self.assertFalse(dest.exists())

    def test_the_explicit_opt_out_stages_and_says_the_shell_is_unchecked(self):
        dest = self.scratch / 'out'
        result = stage.cmd_stage(self.stage_args(dest, pins=None, no_pins=True))
        self.assertEqual(result['shell_pin'], 'unchecked')
        self.assertTrue(any('NOT checked' in note for note in result['advisories']), result['advisories'])

    def test_a_matching_pin_is_reported_as_a_match(self):
        args = self.stage_args(self.scratch / 'out', pins=None, no_pins=False)
        pins = self.pins_file({'shell': {'sha256': stage.sha256_file(args.shell)}})
        args.pins = pins
        result = stage.cmd_stage(args)
        self.assertEqual(result['shell_pin'], 'match')
        self.assertEqual(result['advisories'], [])

    def test_the_shipped_pins_file_is_accepted_by_the_loader(self):
        document = stage.load_pins(stage.DEFAULT_PINS)
        self.assertRegex(document['shell']['sha256'], r'^[0-9a-f]{64}$')


class VerifyChecksTheShell(Case):
    def staged(self, **extra):
        dest = self.scratch / 'out'
        stage.cmd_stage(self.stage_args(dest, pins=None, no_pins=True))
        return dest

    def test_verify_fails_when_the_staged_shell_is_not_the_pinned_one(self):
        dest = self.staged()
        pins = self.pins_file({'shell': {'sha256': '0' * 64}})
        result = stage.cmd_verify(_Args(dest=dest, allow_incomplete=True, pins=pins, no_pins=False))
        self.assertEqual((result['status'], result['shell_pin']), ('FAIL', 'mismatch'))
        self.assertTrue(result['pin_mismatches'][0].startswith('shell_pin_mismatch:'))

    def test_verify_reports_a_match_and_stays_incomplete_without_tcblaunch(self):
        dest = self.staged()
        digest = stage.sha256_file(dest / 'EFI' / 'BOOT' / 'BOOTAA64.EFI')
        pins = self.pins_file({'shell': {'sha256': digest}})
        result = stage.cmd_verify(_Args(dest=dest, allow_incomplete=True, pins=pins, no_pins=False))
        self.assertEqual((result['status'], result['shell_pin'], result['pin_mismatches']),
                         ('INCOMPLETE', 'match', []))

    def test_a_replaced_shell_with_a_regenerated_manifest_is_still_caught(self):
        dest = self.staged()
        digest = stage.sha256_file(dest / 'EFI' / 'BOOT' / 'BOOTAA64.EFI')
        pins = self.pins_file({'shell': {'sha256': digest}})
        other = make_pe(subsystem=10, body=b'\x07' * 64)
        (dest / 'EFI' / 'BOOT' / 'BOOTAA64.EFI').write_bytes(other)
        manifest = dest / 'SP11' / 'MANIFEST.SHA256'
        lines = [f'{stage.sha256_bytes(other)}  EFI/BOOT/BOOTAA64.EFI\n'
                 if line.rstrip('\n').endswith('  EFI/BOOT/BOOTAA64.EFI') else line
                 for line in manifest.read_text().splitlines(True)]
        manifest.write_text(''.join(lines))
        result = stage.cmd_verify(_Args(dest=dest, allow_incomplete=True, pins=pins, no_pins=False))
        self.assertEqual(result['manifest_mismatches'], [])        # the manifest alone is satisfied ...
        self.assertEqual((result['status'], result['shell_pin']), ('FAIL', 'mismatch'))   # ... the pin is not

    def test_verify_refuses_a_missing_pins_file_unless_told_otherwise(self):
        dest = self.staged()
        missing = self.scratch / 'absent.json'
        self.assertEqual(self.refusal(stage.cmd_verify, _Args(dest=dest, allow_incomplete=True, pins=missing,
                                                               no_pins=False)), 'pins_missing')
        result = stage.cmd_verify(_Args(dest=dest, allow_incomplete=True, pins=None, no_pins=True))
        self.assertEqual((result['status'], result['shell_pin']), ('INCOMPLETE', 'unchecked'))


class CommandLine(Case):
    def run_main(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = stage.main(argv)
        return code, json.loads(out.getvalue())

    def stage_argv(self, dest):
        inputs = self.inputs()
        return ['stage', '--shell', str(inputs['shell']), '--slbounce', str(inputs['slbounce']),
                '--sltest', str(inputs['sltest']), '--dest', str(dest)]

    def test_a_missing_pins_file_exits_1_and_stages_nothing(self):
        dest = self.scratch / 'out'
        code, result = self.run_main(self.stage_argv(dest) + ['--pins', str(self.scratch / 'no-such-pins.json')])
        self.assertEqual((code, result['status'], result['reason']), (1, 'REFUSED', 'pins_missing'))
        self.assertFalse(dest.exists())

    def test_the_default_pins_are_the_shipped_ones_and_refuse_a_foreign_shell(self):
        dest = self.scratch / 'out'
        code, result = self.run_main(self.stage_argv(dest))
        self.assertEqual((code, result['reason']), (1, 'input_hash_pin_mismatch'))
        self.assertFalse(dest.exists())

    def test_no_pins_stages_and_exits_0_with_the_shell_unchecked(self):
        dest = self.scratch / 'out'
        code, result = self.run_main(self.stage_argv(dest) + ['--no-pins'])
        self.assertEqual((code, result['shell_pin']), (0, 'unchecked'))

    def test_pins_and_no_pins_together_are_a_usage_error(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as raised:
            stage.main(self.stage_argv(self.scratch / 'out') + ['--pins', str(stage.DEFAULT_PINS), '--no-pins'])
        self.assertEqual(raised.exception.code, 2)
        self.assertFalse((self.scratch / 'out').exists())

    def test_verify_with_the_default_pins_rejects_an_unpinned_shell_with_exit_1(self):
        dest = self.scratch / 'out'
        self.assertEqual(self.run_main(self.stage_argv(dest) + ['--no-pins'])[0], 0)
        code, result = self.run_main(['verify', '--dest', str(dest), '--allow-incomplete'])
        self.assertEqual((code, result['status'], result['shell_pin']), (1, 'FAIL', 'mismatch'))
        code, result = self.run_main(['verify', '--dest', str(dest), '--allow-incomplete', '--no-pins'])
        self.assertEqual((code, result['shell_pin']), (0, 'unchecked'))


class StagedBytes(Case):
    def test_the_staged_bytes_and_the_manifest_are_the_validated_bytes(self):
        args = self.stage_args(self.scratch / 'out', pins=None, no_pins=True)
        validated = args.slbounce.read_bytes()
        replacement = make_pe(subsystem=12, body=b'\x01' * 64)
        self.assertNotEqual(replacement, validated)
        parse = stage.read_pe_header
        swaps = []

        def parse_then_replace(data):
            result = parse(data)
            if data == validated and not swaps:
                swaps.append(True)
                args.slbounce.write_bytes(replacement)    # a second read of the path would now see other bytes
            return result

        with mock.patch.object(stage, 'read_pe_header', side_effect=parse_then_replace):
            result = stage.cmd_stage(args)
        self.assertEqual(swaps, [True])
        dest = args.dest
        self.assertEqual((dest / 'SP11' / 'SLBOUNCE.EFI').read_bytes(), validated)
        digest = stage.sha256_bytes(validated)
        self.assertEqual(result['present']['slbounce']['sha256'], digest)
        manifest = (dest / 'SP11' / 'MANIFEST.SHA256').read_text()
        self.assertIn(f'{digest}  SP11/SLBOUNCE.EFI\n', manifest)
        self.assertEqual(stage.cmd_verify(_Args(dest=dest, allow_incomplete=True))['manifest_mismatches'], [])

    def test_a_pinned_shell_replaced_after_validation_cannot_be_staged(self):
        args = self.stage_args(self.scratch / 'out', pins=None, no_pins=False)
        validated = args.shell.read_bytes()
        args.pins = self.pins_file({'shell': {'sha256': stage.sha256_bytes(validated)}})
        parse = stage.read_pe_header

        def parse_then_replace(data):
            result = parse(data)
            if data == validated:
                args.shell.write_bytes(make_pe(subsystem=10, body=b'\x02' * 64))
            return result

        with mock.patch.object(stage, 'read_pe_header', side_effect=parse_then_replace):
            stage.cmd_stage(args)
        self.assertEqual((args.dest / 'EFI' / 'BOOT' / 'BOOTAA64.EFI').read_bytes(), validated)

    def test_the_reader_refuses_a_file_that_is_not_the_one_that_was_checked(self):
        first = self.write('first.bin', b'x' * 16)
        second = self.write('second.bin', b'x' * 16)
        checked = first.lstat()
        self.assertEqual(self.refusal(stage.read_regular_bytes, second, checked, 1024), 'input_changed_while_reading')
        first.write_bytes(b'y' * 17)
        self.assertEqual(self.refusal(stage.read_regular_bytes, first, checked, 1024), 'input_changed_while_reading')
        link = self.scratch / 'link.bin'
        link.symlink_to(second)
        self.assertEqual(self.refusal(stage.read_regular_bytes, link, second.lstat(), 1024), 'input_unreadable')

    def test_the_reader_returns_exactly_the_bytes_of_an_unchanged_file(self):
        path = self.write('same.bin', bytes(range(256)) * 5000)
        self.assertEqual(stage.read_regular_bytes(path, path.lstat(), 1 << 22), path.read_bytes())


class Hygiene(unittest.TestCase):
    def test_spdx_and_sizes(self):
        for name in ('stage-sp11-boot-media.py', 'test-sp11-boot-media-hardening.py'):
            path = TOOLS / name
            text = path.read_text()
            self.assertIn('SPDX-License-Identifier: MIT', '\n'.join(text.splitlines()[:3]), name)
            self.assertLess(len(text.splitlines()), 1500, name)
            self.assertLess(path.stat().st_size, 50 * 1024, name)


if __name__ == '__main__':
    unittest.main()
