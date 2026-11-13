#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of the `autorun` subcommand of tools/stage-sp11-boot-media.py, on staged trees. No hardware, no emulator,
and nothing under /Volumes or /dev: every tree is a scratch directory below build/.

    python3 tools/test-sp11-autorun-tree.py

* the default path (`stage`, `verify`, README.TXT) against digests taken from the tool before autorun existed, so
  that a tree that never uses autorun is exactly what it was;
* what `autorun` writes, what `verify` accepts afterwards and what it still flags (a stray startup.nsh, a script that
  is not the one the plan generates, a changed plan or staged file, even with the manifest regenerated to match);
* the refusals (a missing source, a hash that differs, a banned name, an incomplete or tampered base tree, a
  destination that looks like a volume), `--clear`, `--remove`, and a failure part-way leaving the tree as it was.

The generator itself is tested by tools/test-sp11-autorun.py, which this file loads for the shared fixtures.
"""
from pathlib import Path
from unittest import mock
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import types
import unittest

TOOLS = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location('autorun_fixtures', TOOLS / 'test-sp11-autorun.py')
G = importlib.util.module_from_spec(spec)
spec.loader.exec_module(G)
autorun, stage, fixtures, make_pe, TWO, ONE = G.autorun, G.stage, G.fixtures, G.fixtures.make_pe, G.TWO, G.ONE

# Taken from the stager as it was before autorun existed, with the inputs of Tree.BLOBS (shell, slbounce, sltest, probe
# and tcblaunch) staged with --no-pins.
GOLDEN_STAGE_JSON_SHA256 = '14eb1af309abc380b1accc50d36b9e4843e50fdf1db33bb5c5affd00e5e555cf'
GOLDEN_VERIFY_JSON_SHA256 = 'f04b1d94d7108d4d4af258dc308db59fc99783b6a9f266fef183dbe504230ee5'
GOLDEN_README_SHA256 = '1c5401104dd6967031254cc7a8b459a3851c6545b13f0bff7d7a41e82431c3f0'
GOLDEN_TREE = {
    'EFI/BOOT/BOOTAA64.EFI': '7ea2caed317cdbd810c375262a89ebc39b4c913e216f788a1857de35cddd4a46',
    'SP11/MANIFEST.SHA256': '89fda992d5459a548e0dbcf35309b8adfd3a1b08986ce237018adc8fe1e7accc',
    'SP11/PROBE.EFI': '5821b96e9c1da321829fc63941ee01830513eb0453a408364428268ae8df01e1',
    'SP11/README.TXT': '1c5401104dd6967031254cc7a8b459a3851c6545b13f0bff7d7a41e82431c3f0',
    'SP11/SLBOUNCE.EFI': '1629db327565f3370a30d47ebc777bb92e0f9e1a2d6e1f44c29f9c94f0d64664',
    'SP11/SLTEST.EFI': 'c1c0f42ca7b10fd97586fa3986fcdb85ee4b52d512d9d58811d16744088b1f8d',
    'SP11/TCBLAUNCH.EXE': '7f8c88fc53aec343352e5a80865cccf51deeb7b24418516f3a499dcf6a627a98',
    'TCBLAUNCH.EXE': '7f8c88fc53aec343352e5a80865cccf51deeb7b24418516f3a499dcf6a627a98',
}
FILES = {'SLBOUNCE-DEVHE-MARK.EFI': 'mark.efi', 'SLBOUNCE-DEVHE.EFI': 'devhe.efi', 'Q1N1.EFI': 'q1n1.efi'}
SCRIPT, PLAN, README, MANIFEST = 'EFI/BOOT/startup.nsh', 'SP11/PLAN.TXT', 'SP11/README.TXT', 'SP11/MANIFEST.SHA256'


class Tree(fixtures.TempTreeTestCase):
    """Staged trees made from synthetic PE stubs, like those of tools/test-sp11-boot-media.py."""

    BLOBS = {
        'shell.efi': make_pe(subsystem=10, body=b'S' * 64), 'slbounce.efi': make_pe(subsystem=12, body=b'L' * 64),
        'sltest.efi': make_pe(subsystem=10, body=b'T' * 64), 'probe.efi': make_pe(subsystem=10, body=b'P' * 64),
        'tcblaunch.exe': make_pe(subsystem=16, body=b'C' * 64),
        'mark.efi': make_pe(subsystem=12, body=b'M' * 64), 'devhe.efi': make_pe(subsystem=12, body=b'D' * 64),
        'q1n1.efi': make_pe(subsystem=10, body=b'Q' * 64),
    }

    def setUp(self):
        super().setUp()
        for name, data in self.BLOBS.items():
            self.write(f'in/{name}', data)
        self.src, self.trees, self.dest = self.scratch / 'in', 0, None

    def run_main(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = stage.main([str(a) for a in argv])
        return code, json.loads(out.getvalue())

    def digest(self, source):
        return stage.sha256_file(self.src / source)

    def default_tree(self, tcblaunch=True):
        """A fresh staged tree (`self.dest`); each call makes another, so one test can stage several."""
        self.trees += 1
        self.dest = self.scratch / f'out{self.trees}'
        argv = ['stage', '--shell', self.src / 'shell.efi', '--slbounce', self.src / 'slbounce.efi',
                '--sltest', self.src / 'sltest.efi', '--probe', self.src / 'probe.efi', '--dest', self.dest, '--no-pins']
        code, result = self.run_main(argv + (['--tcblaunch', self.src / 'tcblaunch.exe'] if tcblaunch else []))
        self.assertEqual(code, 0, result)
        return self.dest

    def autorun_argv(self, steps=TWO, files=None, name='devhe-mark', dest=None):
        argv = ['autorun', '--dest', dest or self.dest, '--no-pins', '--name', name]
        for target, source in (FILES if files is None else files).items():
            argv += ['--file', target, self.src / source, self.digest(source)]
        for step in steps:
            argv += ['--step', step]
        return argv

    def staged(self, **kw):
        tree = self.default_tree()
        code, result = self.run_main(self.autorun_argv(**kw))
        self.assertEqual((code, result['status']), (0, 'AUTORUN_STAGED'), result)
        return tree, result

    def verify(self, tree):
        return self.run_main(['verify', '--dest', tree, '--no-pins'])

    def snapshot(self, tree):
        """Every path with its bytes' hash, mode and mtime: what a refused or undone autorun must not change."""
        out = {}
        for path in sorted(tree.rglob('*')):
            info = path.lstat()
            out[path.relative_to(tree).as_posix()] = (stage.sha256_file(path) if path.is_file() else None,
                                                      info.st_mode, info.st_mtime_ns)
        return out

    def regenerate_manifest(self, tree):
        """What a careful tamperer would do: make the manifest agree with whatever the tree now holds."""
        staged = {p.relative_to(tree).as_posix(): p.read_bytes() for p in tree.rglob('*') if p.is_file()}
        (tree / MANIFEST).write_text(stage.build_manifest(staged))
        stage.finalize_permissions(tree)

    def refused(self, argv, reason):
        before = self.snapshot(self.dest)
        code, result = self.run_main(argv)
        self.assertEqual((code, result.get('status'), result.get('reason')), (1, 'REFUSED', reason), result)
        self.assertEqual(self.snapshot(self.dest), before, 'a refused autorun left the tree changed')
        return result


class DefaultPathIsUnchanged(Tree):
    """With autorun unused the tool's output is what it was before autorun existed (digests from that tool)."""

    def stage_output(self):
        """The text `stage` prints for the golden inputs, with the destination replaced."""
        self.trees += 1
        self.dest = self.scratch / f'out{self.trees}'
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = stage.main(['stage', '--shell', str(self.src / 'shell.efi'), '--slbounce', str(self.src / 'slbounce.efi'),
                               '--sltest', str(self.src / 'sltest.efi'), '--probe', str(self.src / 'probe.efi'),
                               '--tcblaunch', str(self.src / 'tcblaunch.exe'), '--dest', str(self.dest), '--no-pins'])
        self.assertEqual(code, 0)
        return out.getvalue().replace(str(self.dest), '<DEST>')

    def test_stage_and_verify_output_and_the_staged_files(self):
        self.assertEqual(stage.sha256_bytes(self.stage_output().encode()), GOLDEN_STAGE_JSON_SHA256)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(stage.main(['verify', '--dest', str(self.dest), '--no-pins']), 0)
        shown = out.getvalue().replace(str(self.dest), '<DEST>')
        self.assertEqual(stage.sha256_bytes(shown.encode()), GOLDEN_VERIFY_JSON_SHA256)
        self.assertNotIn('autorun', shown)
        files = {p.relative_to(self.dest).as_posix(): stage.sha256_file(p) for p in sorted(self.dest.rglob('*')) if p.is_file()}
        self.assertEqual(files, GOLDEN_TREE)
        self.assertEqual(stage.sha256_bytes(stage.build_readme_text().encode('ascii')), GOLDEN_README_SHA256)

    def test_a_default_tree_gets_no_startup_script_and_verify_no_new_key(self):
        tree = self.default_tree()
        self.assertEqual([p for p in tree.rglob('*') if p.name.lower() == 'startup.nsh'], [])
        self.assertEqual(set(self.verify(tree)[1]), {'status', 'dest', 'missing', 'present', 'manifest_ok', 'manifest_mismatches',
                                                      'startup_nsh_found', 'shell_pin', 'pin_mismatches'})

    def test_the_stage_and_verify_help_name_no_autorun_option(self):
        with mock.patch.object(sys, 'argv', ['stage-sp11-boot-media.py']):
            parser = stage.build_argparser()
        for command in ('stage', 'verify'):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
                parser.parse_args([command, '--help'])
            self.assertNotIn('autorun', out.getvalue().lower())

    def test_a_stray_startup_nsh_in_a_default_tree_is_still_a_finding(self):
        tree = self.default_tree()
        (tree / SCRIPT).write_bytes(autorun.render_script(G.plan_of()))
        self.regenerate_manifest(tree)
        code, result = self.verify(tree)
        self.assertEqual((code, result['status'], result['startup_nsh_found']), (1, 'FAIL', [SCRIPT]))
        self.assertEqual(result['manifest_mismatches'], [])
        self.assertTrue(any('autorun_plan_missing' in p for p in result['autorun']['problems']))


class Apply(Tree):
    def test_it_writes_the_plan_files_the_script_and_a_manifest_that_lists_them(self):
        tree, result = self.staged()
        files = {name: self.BLOBS[source] for name, source in FILES.items()}
        for name, data in files.items():
            self.assertEqual((tree / 'SP11' / name).read_bytes(), data)
        plan = autorun.build_plan('devhe-mark', TWO, {name: stage.sha256_bytes(data) for name, data in files.items()})
        self.assertEqual((tree / SCRIPT).read_bytes(), autorun.render_script(plan))
        self.assertEqual((tree / PLAN).read_bytes(), autorun.render_plan_text(plan))
        self.assertEqual((tree / README).read_text(), autorun.readme_for_plan(stage.build_readme_text(), plan))
        self.assertEqual(result['files_written'], sorted([SCRIPT, MANIFEST, PLAN, README, 'SP11/Q1N1.EFI',
                                                          'SP11/SLBOUNCE-DEVHE-MARK.EFI', 'SP11/SLBOUNCE-DEVHE.EFI']))
        manifest = stage.parse_manifest((tree / MANIFEST).read_text())
        for rel in result['files_written']:
            if rel != MANIFEST:
                self.assertEqual(manifest[rel], stage.sha256_file(tree / rel), rel)
        self.assertEqual(result['plan'], 'devhe-mark')
        self.assertEqual([(s['load'], s['run']) for s in result['steps']],
                         [('SLBOUNCE-DEVHE-MARK.EFI', 'Q1N1.EFI --el2'), ('SLBOUNCE-DEVHE.EFI', 'Q1N1.EFI --el2')])

    def test_everything_the_default_tree_had_is_untouched_but_the_readme_and_manifest(self):
        before = self.snapshot(self.default_tree())
        self.run_main(self.autorun_argv())
        after = self.snapshot(self.dest)
        for rel, value in before.items():
            if rel not in (README, MANIFEST):
                self.assertEqual(after[rel], value, rel)
        self.assertNotEqual(after[README], before[README])

    def test_verify_accepts_the_tree_and_says_what_it_found(self):
        tree, _ = self.staged()
        code, result = self.verify(tree)
        self.assertEqual((code, result['status'], result['manifest_ok'], result['startup_nsh_found']), (0, 'COMPLETE', True, []))
        self.assertEqual(result['autorun'], {'plan': 'devhe-mark', 'steps': 2, 'script': 'present', 'problems': []})

    def test_the_manifest_lines_check_out_against_the_files(self):
        tree, _ = self.staged()
        for line in (tree / MANIFEST).read_text().splitlines():
            digest, _, rel = line.partition('  ')
            self.assertEqual(stage.sha256_file(tree / rel), digest, rel)

    def test_a_name_that_is_already_staged_with_the_same_bytes_is_reused_and_survives_remove(self):
        tree = self.default_tree()
        code, result = self.run_main(self.autorun_argv(steps=['SLBOUNCE-DEVHE.EFI PROBE.EFI'],
                                                       files={'SLBOUNCE-DEVHE.EFI': 'devhe.efi', 'PROBE.EFI': 'probe.efi'}))
        self.assertEqual((code, result['status'], result['files_already_staged']), (0, 'AUTORUN_STAGED', ['SP11/PROBE.EFI']))
        self.assertNotIn('SP11/PROBE.EFI', result['files_written'])
        _, removed = self.run_main(['autorun', '--dest', tree, '--remove'])
        self.assertIn('SP11/SLBOUNCE-DEVHE.EFI', removed['files_removed'])
        self.assertNotIn('SP11/PROBE.EFI', removed['files_removed'], 'a file of the base tree is never removed')
        self.assertEqual(self.verify(tree)[1]['status'], 'COMPLETE')

    def test_the_command_line_works_from_any_directory(self):
        tree = self.default_tree()
        environment = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}
        tool = [sys.executable, '-B', str(TOOLS / 'stage-sp11-boot-media.py')]
        done = subprocess.run(tool + [str(word) for word in self.autorun_argv()], cwd=self.scratch, env=environment,
                              capture_output=True, text=True)
        self.assertEqual((done.returncode, json.loads(done.stdout)['status']), (0, 'AUTORUN_STAGED'), done.stderr)
        done = subprocess.run(tool + ['verify', '--dest', str(tree), '--no-pins'], cwd=self.scratch, env=environment,
                              capture_output=True, text=True)
        self.assertEqual((done.returncode, json.loads(done.stdout)['autorun']['plan']), (0, 'devhe-mark'), done.stderr)
        done = subprocess.run(tool + ['autorun', '--dest', str(tree), '--remove'], cwd=self.scratch, env=environment,
                              capture_output=True, text=True)
        self.assertEqual((done.returncode, json.loads(done.stdout)['status']), (0, 'AUTORUN_REMOVED'), done.stderr)

    def test_the_hash_pin_of_the_shell_still_applies(self):
        self.default_tree()
        pins = self.write('pins.json', json.dumps({'shell': {'sha256': '0' * 64}}).encode())
        argv = self.autorun_argv()
        argv[argv.index('--no-pins'):argv.index('--no-pins') + 1] = ['--pins', pins]
        result = self.refused(argv, 'autorun_base_not_complete')
        self.assertIn('shell_pin_mismatch', result['detail'])

    def test_a_failure_part_way_leaves_the_tree_as_it_was(self):
        self.default_tree()
        before = self.snapshot(self.dest)
        real, calls = stage.write_file_deterministic, []

        def fail_on_the_fourth(path, data, mode):
            calls.append(path)
            if len(calls) == 4:
                raise OSError('disk full')
            return real(path, data, mode)

        args = types.SimpleNamespace(dest=self.dest, clear=False, remove=False, name='devhe-mark', pins=None, no_pins=True,
                                     step=TWO, file=[[n, str(self.src / s), self.digest(s)] for n, s in FILES.items()])
        with mock.patch.object(stage, 'write_file_deterministic', side_effect=fail_on_the_fourth), self.assertRaises(OSError):
            stage.cmd_autorun(args)
        self.assertGreaterEqual(len(calls), 4)
        self.assertEqual(self.snapshot(self.dest), before)

    def test_a_copy_that_does_not_read_back_is_refused_and_undone(self):
        self.default_tree()
        real = stage.sha256_file
        with mock.patch.object(stage, 'sha256_file', side_effect=lambda p: '0' * 64 if Path(p).name == 'PLAN.TXT' else real(p)):
            self.refused(self.autorun_argv(), 'copy_hash_mismatch')


class ApplyRefusals(Tree):
    def argv_with(self, source_of_q1n1=None, digest=None, **kw):
        argv = self.autorun_argv(**kw)
        index = [str(word) for word in argv].index(str(self.src / 'q1n1.efi'))
        if source_of_q1n1 is not None:
            argv[index] = str(source_of_q1n1)
        if digest is not None:
            argv[index + 1] = digest
        return argv

    def test_a_source_that_is_missing_differs_from_its_hash_or_is_not_a_plain_efi_file(self):
        self.default_tree()
        self.refused(self.argv_with(self.src / 'nothing.efi', '0' * 64), 'missing_input')
        self.assertIn('Q1N1.EFI', self.refused(self.argv_with(digest='0' * 64), 'file_hash_mismatch')['detail'])
        link = self.scratch / 'link.efi'
        link.symlink_to(self.src / 'q1n1.efi')
        self.refused(self.argv_with(link), 'input_is_symlink')
        self.write('in/x86.efi', make_pe(machine=0x8664, subsystem=10))
        self.refused(self.argv_with(self.src / 'x86.efi', self.digest('x86.efi')), 'input_wrong_machine')
        self.write('in/driver.efi', make_pe(subsystem=12))
        self.refused(self.argv_with(self.src / 'driver.efi', self.digest('driver.efi')), 'input_wrong_subsystem')

    def test_a_source_named_like_the_scratch_build_is_refused(self):
        self.default_tree()
        for name in ('SLBOUNCE-OLD-NOSWEEP.EFI', 'slbounce-old-nosweep.efi'):
            self.write(f'in/{name}', self.BLOBS['q1n1.efi'])
            self.refused(self.argv_with(self.src / name), 'source_banned')

    def test_the_plan_is_refused_before_any_file_is_read(self):
        self.default_tree()
        two = {'SLBOUNCE-DEVHE.EFI': 'devhe.efi', 'Q1N1.EFI': 'q1n1.efi'}
        for steps, reason in ((['SLBOUNCE-DEVHE.EFI Q1N1.EFI --boot'], 'step_argument_banned'),
                              (['SLBOUNCE-OLD-NOSWEEP.EFI Q1N1.EFI'], 'step_slbounce_banned'),
                              (['SLBOUNCE-DEVHE.EFI SLTEST.EFI'], 'step_app_not_allowed'),
                              (['SLBOUNCE-DEVHE.EFI Q1N1.EFI --el2 --uart-proxy'], 'step_argument_banned')):
            self.refused(self.autorun_argv(steps=steps, files=two), reason)
        self.refused(self.autorun_argv(name='bad name'), 'plan_name_invalid')
        self.refused(self.autorun_argv(files={n: s for n, s in FILES.items() if n != 'Q1N1.EFI'}), 'file_hash_missing')
        self.refused(self.autorun_argv(files={**FILES, 'PROBE.EFI': 'probe.efi'}), 'file_unused')
        twice = self.autorun_argv()
        twice[twice.index('--step'):twice.index('--step')] = ['--file', 'Q1N1.EFI', str(self.src / 'q1n1.efi'), self.digest('q1n1.efi')]
        self.refused(twice, 'file_given_twice')

    def test_the_base_tree_must_verify_and_be_complete(self):
        self.default_tree(tcblaunch=False)
        self.refused(self.autorun_argv(), 'autorun_base_not_complete')
        tree = self.default_tree()
        (tree / 'SP11' / 'SLTEST.EFI').write_bytes(make_pe(subsystem=10, body=b'tampered'))
        self.refused(self.autorun_argv(), 'autorun_base_not_complete')

    def test_a_stray_startup_nsh_is_neither_replaced_nor_accepted(self):
        tree = self.default_tree()
        (tree / SCRIPT).write_bytes(b'echo hi\r\n')
        self.refused(self.autorun_argv(), 'autorun_already_present')
        (tree / SCRIPT).unlink()
        (tree / 'startup.nsh').write_bytes(b'echo hi\r\n')
        self.refused(self.autorun_argv(), 'autorun_base_not_complete')

    def test_it_is_not_applied_twice(self):
        self.staged()
        self.refused(self.autorun_argv(), 'autorun_already_present')

    def test_a_staged_file_of_that_name_with_other_bytes_is_not_replaced(self):
        self.default_tree()
        self.write('in/other-probe.efi', make_pe(subsystem=10, body=b'another probe'))
        self.refused(self.autorun_argv(steps=['SLBOUNCE-DEVHE.EFI PROBE.EFI'],
                                       files={'SLBOUNCE-DEVHE.EFI': 'devhe.efi', 'PROBE.EFI': 'other-probe.efi'}),
                     'autorun_would_replace_staged_file')

    def test_the_readme_it_rewrites_must_be_the_one_the_tool_stages(self):
        tree = self.default_tree()
        (tree / README).write_text('something else\n')
        self.regenerate_manifest(tree)
        self.refused(self.autorun_argv(), 'autorun_readme_unexpected')

    def test_the_destination_guards_of_stage_and_verify_apply(self):
        self.default_tree()
        for dest, reason in ((Path('/Volumes'), 'dest_under_volumes'), (Path('/dev'), 'dest_under_dev'),
                             (Path('/Volumes/sp11-test-stand-in'), 'dest_does_not_exist'),
                             (self.scratch / 'a' / '..' / 'out', 'dest_has_parent_reference'),
                             (self.scratch / 'absent', 'dest_does_not_exist')):
            for flags in (['--clear'], ['--remove'], ['--name', 'x', '--step', 'SLBOUNCE.EFI PROBE.EFI']):
                with self.subTest(dest=str(dest), flags=flags), \
                        mock.patch.object(stage, 'autorun_stage', side_effect=AssertionError('reached the tree')), \
                        mock.patch.object(stage, 'autorun_clear', side_effect=AssertionError('reached the tree')), \
                        mock.patch.object(stage, 'autorun_remove', side_effect=AssertionError('reached the tree')):
                    code, result = self.run_main(['autorun', '--dest', dest, *flags])
                    self.assertEqual((code, result['reason']), (1, reason))

    def test_symlinked_destinations_are_refused(self):
        self.default_tree()
        link = self.scratch / 'link'
        link.symlink_to(self.dest)
        code, result = self.run_main(['autorun', '--dest', link, '--clear'])
        self.assertEqual((code, result['reason']), (1, 'dest_is_symlink'))

    def test_clear_and_remove_take_no_plan_options(self):
        self.default_tree()
        self.refused(['autorun', '--dest', self.dest, '--clear', '--name', 'x'], 'autorun_options_conflict')
        self.refused(['autorun', '--dest', self.dest, '--remove', '--file', 'Q1N1.EFI', self.src / 'q1n1.efi', self.digest('q1n1.efi')],
                     'autorun_options_conflict')

    def test_exactly_one_action_is_required(self):
        self.default_tree()
        for argv in (['autorun', '--dest', self.dest], ['autorun', '--dest', self.dest, '--clear', '--remove'],
                     ['autorun', '--dest', self.dest, '--clear', '--step', 'SLBOUNCE.EFI PROBE.EFI']):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                stage.main([str(a) for a in argv])
            self.assertEqual(caught.exception.code, 2)


class VerifyIsStrict(Tree):
    """A tampered autorun tree is refused even when the tamperer regenerates the manifest to match."""

    def change(self, edit):
        tree, _ = self.staged()
        edit(tree)
        self.regenerate_manifest(tree)
        return self.verify(tree)

    def test_each_change_is_a_failure(self):
        def script_with(old, new):
            return lambda t: (t / SCRIPT).write_bytes((t / SCRIPT).read_bytes().replace(old, new, 1))

        def plan_with(old, new):
            return lambda t: (t / PLAN).write_bytes((t / PLAN).read_bytes().replace(old, new, 1))

        q1n1 = stage.sha256_bytes(self.BLOBS['q1n1.efi']).encode()
        cases = {
            'a command appended to the script': lambda t: (t / SCRIPT).write_bytes((t / SCRIPT).read_bytes() + b'del SP11\\DONE1.TXT\r\n'),
            'a banned argument': script_with(b'Q1N1.EFI --el2', b'Q1N1.EFI --boot'),
            'the script of another plan': lambda t: (t / SCRIPT).write_bytes(autorun.render_script(G.plan_of(ONE, 'other'))),
            'a script with LF endings': lambda t: (t / SCRIPT).write_bytes((t / SCRIPT).read_bytes().replace(b'\r\n', b'\n')),
            'an empty script': lambda t: (t / SCRIPT).write_bytes(b''),
            'a hash changed in PLAN.TXT': plan_with(q1n1, b'0' * 64),
            'prose changed in PLAN.TXT': plan_with(b'How it runs', b'How it goes'),
            'PLAN.TXT removed': lambda t: (t / PLAN).unlink(),
            'PLAN.TXT not text': lambda t: (t / PLAN).write_bytes(b'\xff\xfe'),
            'a step file replaced': lambda t: (t / 'SP11' / 'Q1N1.EFI').write_bytes(make_pe(subsystem=10, body=b'another q1n1')),
            'a step file removed': lambda t: (t / 'SP11' / 'SLBOUNCE-DEVHE.EFI').unlink(),
            'a step file of the wrong kind': lambda t: (t / 'SP11' / 'SLBOUNCE-DEVHE.EFI').write_bytes(make_pe(subsystem=10)),
            'the README reverted to the manual text': lambda t: (t / README).write_text(stage.build_readme_text()),
            'the README edited': lambda t: (t / README).write_text((t / README).read_text().replace('AUTORUN IS ENABLED', 'autorun is off')),
        }
        for what, edit in cases.items():
            with self.subTest(what):
                code, result = self.change(edit)
                self.assertEqual((code, result['status']), (1, 'FAIL'), what)
                self.assertTrue(result['autorun']['problems'], what)

    def test_a_tampered_script_stays_a_startup_nsh_finding(self):
        _, result = self.change(lambda t: (t / SCRIPT).write_bytes(b'echo hi\r\n'))
        self.assertEqual(result['startup_nsh_found'], [SCRIPT])
        self.assertTrue(any('differs' in p for p in result['autorun']['problems']))

    def test_other_startup_scripts_are_findings_even_beside_a_valid_one(self):
        for rel in ('startup.nsh', 'SP11/startup.nsh', 'EFI/startup.nsh'):
            with self.subTest(rel):
                code, result = self.change(lambda t: (t / rel).write_bytes(b'echo hi\r\n'))
                self.assertEqual((code, result['status']), (1, 'FAIL'))
                self.assertEqual(result['startup_nsh_found'], [rel])
                self.assertEqual(result['autorun']['problems'], [], 'the valid script itself is still accepted')

    def test_without_the_manifest_regenerated_the_change_is_caught_too(self):
        tree, _ = self.staged()
        (tree / SCRIPT).write_bytes(b'echo hi\r\n')
        result = self.verify(tree)[1]
        self.assertEqual(result['status'], 'FAIL')
        self.assertIn(f'manifest_hash_mismatch:{SCRIPT}', result['manifest_mismatches'])

    def test_a_script_that_is_not_listed_in_the_manifest_is_a_failure(self):
        tree, _ = self.staged()
        manifest = tree / MANIFEST
        manifest.write_text(''.join(line for line in manifest.read_text().splitlines(True) if 'startup.nsh' not in line))
        result = self.verify(tree)[1]
        self.assertEqual(result['status'], 'FAIL')
        self.assertIn(f'manifest_unlisted_extra_file:{SCRIPT}', result['manifest_mismatches'])

    def test_a_run_time_marker_is_a_finding_until_it_is_cleared(self):
        tree, _ = self.staged()
        (tree / 'SP11' / 'DONE1.TXT').write_bytes(b'AUTORUN devhe-mark STEP 1 STARTED\r\n')
        code, result = self.verify(tree)
        self.assertEqual((code, result['status']), (1, 'FAIL'))
        self.assertIn('manifest_unlisted_extra_file:SP11/DONE1.TXT', result['manifest_mismatches'])
        self.assertEqual(self.run_main(['autorun', '--dest', tree, '--clear'])[1]['markers_removed'], ['SP11/DONE1.TXT'])
        self.assertEqual(self.verify(tree)[1]['status'], 'COMPLETE')

    def test_a_plan_without_its_script_is_a_disabled_autorun_not_a_failure(self):
        tree, _ = self.staged()
        (tree / SCRIPT).unlink()
        self.regenerate_manifest(tree)
        code, result = self.verify(tree)
        self.assertEqual((code, result['status'], result['autorun']['script'], result['autorun']['problems']), (0, 'COMPLETE', 'absent', []))

    def test_symlinks_are_not_followed(self):
        tree, _ = self.staged()
        elsewhere = self.scratch / 'elsewhere.nsh'
        elsewhere.write_bytes((tree / SCRIPT).read_bytes())
        (tree / SCRIPT).unlink()
        (tree / SCRIPT).symlink_to(elsewhere)
        code, result = self.verify(tree)
        self.assertEqual((code, result['status']), (1, 'FAIL'))


class ClearAndRemove(Tree):
    def test_clear_removes_only_the_markers(self):
        tree, _ = self.staged()
        before = self.snapshot(tree)
        for name in ('DONE1.TXT', 'DONE2.TXT', 'done3.txt', 'DONE.TXT', 'DONE123.TXT'):
            (tree / 'SP11' / name).write_bytes(b'x')
        code, result = self.run_main(['autorun', '--dest', tree, '--clear'])
        self.assertEqual((code, result['status']), (0, 'AUTORUN_CLEARED'))
        self.assertEqual(result['markers_removed'], ['SP11/DONE1.TXT', 'SP11/DONE2.TXT', 'SP11/done3.txt'])
        self.assertEqual(sorted(p.name for p in (tree / 'SP11').iterdir() if p.name.startswith('DONE')), ['DONE.TXT', 'DONE123.TXT'])
        for name in ('DONE.TXT', 'DONE123.TXT'):
            (tree / 'SP11' / name).unlink()
        stage.finalize_permissions(tree)
        self.assertEqual(self.snapshot(tree), before)

    def test_clear_with_nothing_to_clear_succeeds_and_says_so(self):
        code, result = self.run_main(['autorun', '--dest', self.default_tree(), '--clear'])
        self.assertEqual((code, result['markers_removed']), (0, []))

    def test_remove_restores_the_manual_state_byte_for_byte(self):
        tree = self.default_tree()
        before, (_, verified) = self.snapshot(tree), self.verify(tree)
        self.run_main(self.autorun_argv())
        (tree / 'SP11' / 'DONE1.TXT').write_bytes(b'x')
        code, result = self.run_main(['autorun', '--dest', tree, '--remove'])
        self.assertEqual((code, result['status']), (0, 'AUTORUN_REMOVED'))
        self.assertEqual(result['files_removed'], [SCRIPT, 'SP11/DONE1.TXT', PLAN, 'SP11/Q1N1.EFI',
                                                   'SP11/SLBOUNCE-DEVHE-MARK.EFI', 'SP11/SLBOUNCE-DEVHE.EFI'])
        self.assertEqual(self.snapshot(tree), before)
        self.assertEqual(self.verify(tree)[1], verified)

    def test_remove_on_a_tree_without_autorun_is_a_no_op(self):
        tree = self.default_tree()
        before = self.snapshot(tree)
        code, result = self.run_main(['autorun', '--dest', tree, '--remove'])
        self.assertEqual((code, result['status']), (0, 'AUTORUN_NOT_PRESENT'))
        self.assertEqual(self.snapshot(tree), before)

    def test_remove_refuses_a_tree_it_cannot_vouch_for_and_changes_nothing(self):
        tree, _ = self.staged()
        (tree / 'SP11' / 'Q1N1.EFI').write_bytes(make_pe(subsystem=10, body=b'another q1n1'))
        self.refused(['autorun', '--dest', tree, '--remove'], 'autorun_tree_not_valid')

    def test_apply_remove_apply_again(self):
        tree, _ = self.staged()
        self.run_main(['autorun', '--dest', tree, '--remove'])
        code, again = self.run_main(self.autorun_argv(steps=['SLBOUNCE-DEVHE.EFI Q1N1.EFI'], name='again',
                                                      files={'SLBOUNCE-DEVHE.EFI': 'devhe.efi', 'Q1N1.EFI': 'q1n1.efi'}))
        self.assertEqual((code, again['status']), (0, 'AUTORUN_STAGED'))
        self.assertEqual(self.verify(tree)[1]['autorun']['plan'], 'again')

    def test_a_failure_while_removing_leaves_the_tree_as_it_was(self):
        tree, _ = self.staged()
        before, real = self.snapshot(tree), stage.write_file_deterministic

        def fail_on_the_manifest(path, data, mode):
            if Path(path).name == 'MANIFEST.SHA256':
                raise OSError('disk full')
            return real(path, data, mode)

        args = types.SimpleNamespace(dest=tree, clear=False, remove=True, name=None, file=[], step=None)
        with mock.patch.object(stage, 'write_file_deterministic', side_effect=fail_on_the_manifest), self.assertRaises(OSError):
            stage.cmd_autorun(args)
        self.assertEqual(self.snapshot(tree), before)


def first_existing(*candidates):
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


class RealArtifacts(Tree):
    """The reviewed images themselves, when this host has them: SKIPs (never passes) otherwise. Stages and verifies only;
    nothing is booted, and hardware status stays NOT RUN."""

    def test_real_images_pass_the_role_checks_and_the_round_trip(self):
        root = G.ROOT
        real = {
            'shell': first_existing(os.environ.get('SP11_SHELL_EFI'), root / 'build' / 'sp11-inputs' / 'shellaa64.efi',
                                    root / 'downloads' / 'sp11' / 'boot-media' / 'shellaa64.efi'),
            'slbounce': first_existing(os.environ.get('SP11_SLBOUNCE_EFI')),
            'q1n1': first_existing(os.environ.get('SP11_Q1N1_EFI')),
            'tests': first_existing(os.environ.get('SP11_TESTS_EFI'), root / 'build' / 'uefi' / 'sp11-el2-tests.efi'),
        }
        if not all(real.values()):
            print('SKIP: real images not found (set SP11_SHELL_EFI, SP11_SLBOUNCE_EFI, SP11_Q1N1_EFI and, if it is not '
                  'built under build/uefi/, SP11_TESTS_EFI)')
            self.skipTest('real images not found')
        self.trees += 1
        self.dest = self.scratch / 'real'
        code, result = self.run_main(['stage', '--shell', real['shell'], '--slbounce', real['slbounce'],
                                      '--sltest', self.src / 'sltest.efi', '--tcblaunch', self.src / 'tcblaunch.exe',
                                      '--dest', self.dest, '--pins', stage.DEFAULT_PINS])
        self.assertEqual((code, result['shell_pin']), (0, 'match'), result)
        files = {'SLBOUNCE-DRYRUN.EFI': real['slbounce'], 'Q1N1.EFI': real['q1n1'], 'TESTS.EFI': real['tests']}
        argv = ['autorun', '--dest', self.dest, '--pins', stage.DEFAULT_PINS, '--name', 'real-images']
        for name, path in files.items():
            argv += ['--file', name, path, stage.sha256_file(path)]
        code, result = self.run_main(argv + ['--step', 'SLBOUNCE-DRYRUN.EFI Q1N1.EFI --el2', '--step', 'SLBOUNCE-DRYRUN.EFI TESTS.EFI'])
        self.assertEqual((code, result['status']), (0, 'AUTORUN_STAGED'), result)
        code, result = self.run_main(['verify', '--dest', self.dest, '--pins', stage.DEFAULT_PINS])
        self.assertEqual((code, result['status'], result['shell_pin'], result['autorun']['problems']), (0, 'COMPLETE', 'match', []))
        self.assertEqual(self.run_main(['autorun', '--dest', self.dest, '--remove'])[1]['status'], 'AUTORUN_REMOVED')


class ManifestHelpers(Tree):
    def test_render_matches_build_manifest(self):
        text = (self.default_tree() / MANIFEST).read_text()
        self.assertEqual(stage.render_manifest(stage.parse_manifest(text)), text)

    def test_parse_refuses_what_verify_would_not_accept(self):
        good = 'a' * 64 + '  SP11/A.EFI\n'
        stage.parse_manifest(good)
        for text in ('garbage\n', 'A' * 64 + '  SP11/A.EFI\n', 'a' * 64 + '  ../etc/passwd\n', 'a' * 64 + '  /abs\n',
                     good + good, 'a' * 64 + ' SP11/A.EFI\n'):
            with self.assertRaises(stage.Refusal):
                stage.parse_manifest(text)


if __name__ == '__main__':
    unittest.main()
