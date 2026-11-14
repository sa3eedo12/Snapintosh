#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of `autorun --data` on staged trees: the payload runner's manifest and the files it reads. No hardware, no
emulator, and nothing under /Volumes or /dev: every tree is a scratch directory below build/.

    python3 tools/test-sp11-autorun-payload-tree.py

* staging a payload plan with one payload file and with three, what is written, listed and verified, and `--clear`,
  `--remove` and a failure part-way leaving the tree as it was;
* every refusal at staging time (a missing manifest, a manifest and files that disagree, a bad manifest of every kind,
  bad names, bad sources), each with the tree left exactly as it was;
* `verify` of a tampered tree, even when the stick tree's own manifest is regenerated to match, and a plan text edited;
* interoperability with the manifest tool of the q1n1 fork: SKIP unless Q1N1_DIR names a q1n1 checkout (a value that
  names no such tool FAILS), and for the real runner image SKIP unless SP11_Q1N1_PAYLOAD_EFI and SP11_PAYLOAD_STATE_BIN
  name files inside this tree. These stage and verify only: nothing is booted, so hardware status stays NOT RUN.

The pure rules are tested by tools/test-sp11-autorun-payload.py, whose goldens and helpers this file shares.
"""
from dataclasses import replace
from pathlib import Path
from unittest import mock
import hashlib
import importlib.util
import os
import subprocess
import sys
import unittest

TOOLS = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(TOOLS))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


P = load('autorun_payload_unit_tests', TOOLS / 'test-sp11-autorun-payload.py')     # which loads the other two suites
T, fx, autorun, stage, make_pe = P.T, P.fx, P.autorun, P.stage, P.make_pe
RUNNER, MANIFEST, SLBOUNCE, STEP, ONE, THREE = P.RUNNER, P.MANIFEST, P.SLBOUNCE, P.STEP, P.ONE, P.THREE
SCRIPT, PLAN, README, MAN_REL = T.SCRIPT, T.PLAN, T.README, T.MANIFEST
sha = lambda blob: hashlib.sha256(blob).hexdigest()  # noqa: E731


class PayloadTree(P.Tree):
    """Staged trees made from synthetic images (like those of tools/test-sp11-autorun-tree.py) and a stand-in runner."""

    BLOBS = {**P.Tree.BLOBS, 'payload.efi': make_pe(subsystem=10, body=b'R' * 64)}

    def data_argv(self, files, blob=None, steps=(STEP,), name='payload-a', digests=None, listed=True):
        """`autorun` for the payload `files` and the manifest `blob` (default: the listing of `files`); `listed` False
        leaves PAYLOAD.MAN out. The sources are written under in/data/; `digests` {NAME: sha256} overrides what is passed."""
        argv = ['autorun', '--dest', self.dest, '--no-pins', '--name', name,
                '--file', SLBOUNCE, self.src / 'mark.efi', self.digest('mark.efi'),
                '--file', RUNNER, self.src / 'payload.efi', self.digest('payload.efi')]
        blobs = ({MANIFEST: fx.listing(files) if blob is None else blob} if listed else {}) | dict(files)
        for item, data in blobs.items():
            self.write(f'in/data/{item}', data)
            argv += ['--data', item, self.src / 'data' / item, (digests or {}).get(item, sha(data))]
        for step in steps:
            argv += ['--step', step]
        return argv

    def swap_source(self, argv, old, new):
        argv[[str(word) for word in argv].index(str(old))] = str(new)
        return argv

    def staged_payload(self, files=ONE):
        tree = self.default_tree()
        code, result = self.run_main(self.data_argv(files))
        self.assertEqual((code, result['status']), (0, 'AUTORUN_STAGED'), result)
        return tree, result


class StageData(PayloadTree):
    def test_a_payload_plan_is_staged_listed_and_verified(self):
        for files, section in ((ONE, P.DATA_ONE), (THREE, P.DATA_THREE)):
            with self.subTest(files=sorted(files)):
                tree, result = self.staged_payload(files)
                blobs = P.staged(files)
                for name, blob in blobs.items():
                    self.assertEqual((tree / 'SP11' / name).read_bytes(), blob, name)
                self.assertEqual((tree / 'SP11' / RUNNER).read_bytes(), self.BLOBS['payload.efi'])
                self.assertEqual((tree / SCRIPT).read_bytes(), P.GOLDEN_SCRIPT.encode('ascii'))
                plan = autorun.attach_data(
                    autorun.build_plan('payload-a', [STEP], {SLBOUNCE: self.digest('mark.efi'), RUNNER: self.digest('payload.efi')}),
                    P.data_files(blobs), blobs[MANIFEST])
                self.assertEqual((tree / PLAN).read_bytes(), autorun.render_plan_text(plan))
                self.assertIn(section.encode('ascii'), (tree / PLAN).read_bytes())
                self.assertEqual(result['files_written'], sorted(
                    [SCRIPT, PLAN, README, MAN_REL, f'SP11/{SLBOUNCE}', f'SP11/{RUNNER}', *(f'SP11/{n}' for n in blobs)]))
                self.assertEqual(result['data'], {n: {'size': len(b), 'sha256': sha(b)} for n, b in blobs.items()})
                listed = stage.parse_manifest((tree / MAN_REL).read_text())
                for name, blob in blobs.items():
                    self.assertEqual(listed[f'SP11/{name}'], sha(blob), f'{name} is in the stick tree manifest')
                code, found = self.verify(tree)
                self.assertEqual((code, found['status'], found['manifest_ok'], found['startup_nsh_found']), (0, 'COMPLETE', True, []))
                self.assertEqual(found['autorun'], {'plan': 'payload-a', 'steps': 1, 'script': 'present', 'problems': []})

    def test_the_readme_and_the_script_do_not_change_for_data_files(self):
        tree, _ = self.staged_payload(THREE)
        plan = autorun.parse_plan_text((tree / PLAN).read_bytes())
        self.assertEqual([(d.name, d.size) for d in plan.data], [(MANIFEST, 920), ('DATA1.BIN', 160), ('DATA2.BIN', 102), ('STATE.BIN', 1024)])
        self.assertEqual((tree / README).read_text(), autorun.readme_for_plan(stage.build_readme_text(), plan))
        self.assertEqual((tree / SCRIPT).read_bytes(), autorun.render_script(plan))

    def test_several_runner_steps_share_one_manifest_and_mix_with_other_applications(self):
        tree = self.default_tree()
        steps = [STEP, f'SLBOUNCE-DEVHE.EFI {RUNNER} --el2', 'SLBOUNCE-DEVHE.EFI Q1N1.EFI --el2']
        argv = self.data_argv(ONE, steps=steps)
        argv[argv.index('--data'):argv.index('--data')] = ['--file', 'SLBOUNCE-DEVHE.EFI', str(self.src / 'devhe.efi'), self.digest('devhe.efi'),
                                                            '--file', 'Q1N1.EFI', str(self.src / 'q1n1.efi'), self.digest('q1n1.efi')]
        code, result = self.run_main(argv)
        self.assertEqual((code, result['status']), (0, 'AUTORUN_STAGED'), result)
        self.assertEqual([step['run'] for step in result['steps']], [f'{RUNNER} --el2', f'{RUNNER} --el2', 'Q1N1.EFI --el2'])
        self.assertEqual(list(result['data']), [MANIFEST, 'STATE.BIN'], 'one set of data files, not one per step')
        self.assertEqual((tree / PLAN).read_bytes().count(b'data files ('), 1)
        self.assertEqual((tree / SCRIPT).read_bytes().count(b'\r\nload SP11'), 3)
        self.assertEqual(self.verify(tree)[1]['autorun'], {'plan': 'payload-a', 'steps': 3, 'script': 'present', 'problems': []})

    def test_a_plan_without_data_files_is_staged_as_before(self):
        tree = self.default_tree()
        code, result = self.run_main(self.autorun_argv())
        self.assertEqual((code, result['status']), (0, 'AUTORUN_STAGED'))
        self.assertNotIn('data', result)
        self.assertNotIn(b'data files', (tree / PLAN).read_bytes())

    def test_clear_deletes_the_markers_only_and_remove_restores_the_tree_byte_for_byte(self):
        tree = self.default_tree()
        before, (_, verified) = self.snapshot(tree), self.verify(tree)
        self.run_main(self.data_argv(THREE))
        (tree / 'SP11' / 'DONE1.TXT').write_bytes(b'x')
        code, result = self.run_main(['autorun', '--dest', tree, '--clear'])
        self.assertEqual((code, result['markers_removed']), (0, ['SP11/DONE1.TXT']))
        for name in P.staged(THREE):
            self.assertTrue((tree / 'SP11' / name).is_file(), f'--clear removed {name}')
        self.assertEqual(self.verify(tree)[1]['status'], 'COMPLETE')
        (tree / 'SP11' / 'DONE2.TXT').write_bytes(b'x')
        code, removed = self.run_main(['autorun', '--dest', tree, '--remove'])
        self.assertEqual((code, removed['status']), (0, 'AUTORUN_REMOVED'))
        self.assertEqual(removed['files_removed'], sorted(
            [SCRIPT, PLAN, 'SP11/DONE2.TXT', f'SP11/{SLBOUNCE}', f'SP11/{RUNNER}', *(f'SP11/{n}' for n in P.staged(THREE))]))
        self.assertEqual(self.snapshot(tree), before)
        self.assertEqual(self.verify(tree)[1], verified)

    def test_apply_remove_apply_again(self):
        tree, _ = self.staged_payload(ONE)
        self.run_main(['autorun', '--dest', tree, '--remove'])
        code, again = self.run_main(self.data_argv(THREE, name='again'))
        self.assertEqual((code, again['status']), (0, 'AUTORUN_STAGED'), again)
        self.assertEqual(self.verify(tree)[1]['autorun']['plan'], 'again')

    def test_a_failure_while_writing_a_data_file_leaves_the_tree_as_it_was(self):
        self.default_tree()
        before, real = self.snapshot(self.dest), stage.write_file_deterministic

        def fail_on_the_state_file(path, data, mode):
            if Path(path).name == 'STATE.BIN':
                raise OSError('disk full')
            return real(path, data, mode)

        argv = self.data_argv(ONE)
        with mock.patch.object(stage, 'write_file_deterministic', side_effect=fail_on_the_state_file), self.assertRaises(OSError):
            self.run_main(argv)
        self.assertEqual(self.snapshot(self.dest), before)

    def test_a_copy_that_does_not_read_back_is_refused_and_undone(self):
        self.default_tree()
        before, real = self.snapshot(self.dest), stage.write_file_deterministic

        def write_other_bytes(path, data, mode):
            return real(path, b'X' + data[1:] if Path(path).name == MANIFEST else data, mode)

        argv = self.data_argv(ONE)
        with mock.patch.object(stage, 'write_file_deterministic', side_effect=write_other_bytes):
            code, result = self.run_main(argv)
        self.assertEqual((code, result['reason']), (1, 'copy_hash_mismatch'))
        self.assertEqual(self.snapshot(self.dest), before)

    def test_the_options_of_a_plan_are_refused_with_clear_and_remove(self):
        tree, _ = self.staged_payload(ONE)
        before = self.snapshot(tree)
        for action in ('--clear', '--remove'):
            code, result = self.run_main(['autorun', '--dest', tree, action, '--data', 'X.BIN', self.src / 'data' / 'STATE.BIN', 'a' * 64])
            self.assertEqual((code, result['reason']), (1, 'autorun_options_conflict'), action)
        self.assertEqual(self.snapshot(tree), before)

    def test_the_help_names_the_option_and_stage_and_verify_do_not(self):
        script = str(TOOLS / 'stage-sp11-boot-media.py')

        def helptext(command):
            return subprocess.run([sys.executable, '-B', script, command, '--help'], capture_output=True, text=True).stdout
        self.assertIn('--data NAME PATH SHA256', ' '.join(helptext('autorun').split()))
        self.assertIn('Q1N1-PAYLOAD.EFI', helptext('autorun'))
        for command in ('stage', 'verify'):
            self.assertNotIn('--data', helptext(command))


class StageDataRefusals(PayloadTree):
    """Everything the tool refuses at staging time, each with the tree left exactly as it was."""

    def test_the_steps_are_checked_before_any_file_is_read(self):
        self.default_tree()
        for text, reason in ((f'{SLBOUNCE} {RUNNER}', 'step_arguments_not_allowed'), (f'{SLBOUNCE} {RUNNER} --boot', 'step_argument_banned'),
                             (f'{SLBOUNCE} {RUNNER} --uart', 'step_argument_banned'), (f'{SLBOUNCE} {RUNNER} --uart-proxy', 'step_argument_banned'),
                             (f'{SLBOUNCE} {RUNNER} --fault-test', 'step_argument_banned'), (f'{SLBOUNCE} {RUNNER} --el2 --boot', 'step_argument_banned'),
                             (f'{SLBOUNCE} {RUNNER} --el3', 'step_arguments_not_allowed'), (f'{SLBOUNCE} {RUNNER} el2', 'step_arguments_not_allowed')):
            with self.subTest(step=text):
                self.refused(self.data_argv(ONE, steps=[text]), reason)

    def test_data_without_a_runner_step_is_refused_with_its_own_reason(self):
        self.default_tree()
        argv = self.autorun_argv(steps=['SLBOUNCE-DEVHE.EFI Q1N1.EFI --el2'], files={'SLBOUNCE-DEVHE.EFI': 'devhe.efi', 'Q1N1.EFI': 'q1n1.efi'})
        self.write('in/data/STATE.BIN', fx.SAMPLE['STATE.BIN'])
        argv[argv.index('--step'):argv.index('--step')] = ['--data', 'STATE.BIN', str(self.src / 'data' / 'STATE.BIN'), sha(fx.SAMPLE['STATE.BIN'])]
        self.refused(argv, 'data_without_runner')

    def test_a_missing_manifest_is_refused(self):
        self.default_tree()
        self.refused(self.data_argv(ONE, listed=False), 'data_manifest_missing')
        argv = self.data_argv({})
        self.refused(argv[:argv.index('--data')] + argv[argv.index('--step'):], 'data_manifest_missing')

    def test_what_the_manifest_and_the_files_must_agree_on(self):
        self.default_tree()
        good = P.rec()
        cases = {
            'a manifest entry with no staged file': (ONE, fx.listing(THREE), 'manifest_entry_not_staged'),
            'a staged file the manifest does not list': (THREE, fx.listing(ONE), 'data_file_not_in_manifest'),
            'a size that differs': (ONE, fx.manifest([P.rec(size=1025)]), 'manifest_entry_size_mismatch'),
            'a hash that differs': (ONE, fx.manifest([P.rec(digest='0' * 64)]), 'manifest_entry_hash_mismatch'),
            'a duplicate entry in another letter case': (ONE, fx.manifest([good, P.rec(directory='\\sp11\\', name='state.bin')]), 'manifest_duplicate_entry'),
            'a path outside \\SP11\\': (ONE, fx.manifest([P.rec(directory='\\OTHER\\')]), 'manifest_path_outside_sp11'),
            'a bad magic': (ONE, fx.manifest([good], magic=1), 'manifest_bad_magic'),
            'a bad version': (ONE, fx.manifest([good], version=2), 'manifest_bad_version'),
            'a bad count': (ONE, fx.manifest([], count=0), 'manifest_bad_count'),
            'a bad entry size': (ONE, fx.manifest([good], entry_size=300), 'manifest_bad_entry_size'),
            'a bad length': (ONE, fx.manifest([good], trailing=b'\0'), 'manifest_bad_length'),
            'six entries': (ONE, fx.listing(P.many(6)), 'manifest_bad_count'),
            'an oversized entry': (ONE, fx.manifest([P.rec(size=(256 << 20) + 1)]), 'manifest_size_out_of_range'),
            'an entry that lists the manifest': (ONE, fx.manifest([good, P.rec(MANIFEST, blob=b'x')]), 'manifest_lists_itself'),
            'a manifest that is not a manifest': (ONE, b'this is not a manifest', 'manifest_bad_length'),
        }
        for what, (files, blob, reason) in cases.items():
            with self.subTest(what):
                self.refused(self.data_argv(files, blob), reason)

    def test_names_and_counts(self):
        self.default_tree()
        for name, reason in (('state.bin', 'data_name_invalid'), ('STATE.bin', 'data_name_invalid'), ('LONGNAME9.BIN', 'data_name_invalid'),
                             ('STATE.LONG', 'data_name_invalid'), ('STATE', 'data_name_invalid'), ('PLAN.TXT', 'data_name_collides'),
                             ('README.TXT', 'data_name_collides'), ('PROBE.EFI', 'data_name_collides'), ('SLBOUNCE.EFI', 'data_name_collides'),
                             ('DONE1.TXT', 'data_name_collides'), ('STARTUP.NSH', 'data_name_collides'), ('SLTEST.EFI', 'data_name_collides')):
            with self.subTest(name=name):
                self.refused(self.data_argv({name: fx.SAMPLE['STATE.BIN']}), reason)
        self.refused(self.data_argv(P.many(6)), 'data_too_many_files')
        code, result = self.run_main(self.data_argv(P.many(5)))
        self.assertEqual((code, result['status']), (0, 'AUTORUN_STAGED'), 'five files besides the manifest are the limit')

    def test_the_sources_are_checked_like_the_other_files(self):
        self.default_tree()
        source = self.src / 'data' / 'STATE.BIN'
        self.assertIn('STATE.BIN', self.refused(self.data_argv(ONE, digests={'STATE.BIN': '0' * 64}), 'data_hash_mismatch')['detail'])
        self.refused(self.data_argv(ONE, digests={'STATE.BIN': 'A' * 64}), 'data_hash_malformed')
        self.refused(self.swap_source(self.data_argv(ONE), source, self.src / 'data' / 'absent.bin'), 'missing_input')
        link = self.scratch / 'link.bin'
        link.symlink_to(source)
        self.refused(self.swap_source(self.data_argv(ONE), source, link), 'input_is_symlink')
        self.write('in/data/EMPTY.BIN', b'')
        self.refused(self.swap_source(self.data_argv(ONE), source, self.src / 'data' / 'EMPTY.BIN'), 'input_empty_file')
        self.write('in/data/SLBOUNCE-OLD-NOSWEEP.EFI', fx.SAMPLE['STATE.BIN'])
        self.refused(self.swap_source(self.data_argv(ONE), source, self.src / 'data' / 'SLBOUNCE-OLD-NOSWEEP.EFI'), 'source_banned')

    def test_a_name_given_twice_is_refused(self):
        self.default_tree()
        argv = self.data_argv(ONE)
        argv[argv.index('--step'):argv.index('--step')] = ['--data', 'STATE.BIN', str(self.src / 'data' / 'STATE.BIN'), sha(fx.SAMPLE['STATE.BIN'])]
        self.refused(argv, 'data_given_twice')

    def test_a_tree_that_already_has_a_file_of_that_name_is_not_overwritten(self):
        tree = self.default_tree()
        (tree / 'SP11' / 'STATE.BIN').write_bytes(b'another file')
        stage.finalize_permissions(tree)
        self.regenerate_manifest(tree)          # a tree that verifies, with that file listed by hand
        self.assertEqual(self.verify(tree)[1]['status'], 'COMPLETE')
        self.refused(self.data_argv(ONE), 'data_would_replace_staged_file')


class VerifyData(PayloadTree):
    """A tampered tree is refused even when the stick tree's manifest is regenerated to match."""

    def change(self, edit, files=THREE):
        tree, _ = self.staged_payload(files)
        edit(tree)
        self.regenerate_manifest(tree)
        return self.verify(tree)

    def test_each_change_to_a_data_file_or_the_plan_is_a_failure_that_names_it(self):
        def plan_with(old, new):
            return lambda t: (t / PLAN).write_bytes((t / PLAN).read_bytes().replace(old, new, 1))

        def write(name, blob):
            return lambda t: (t / 'SP11' / name).write_bytes(blob)

        differs = 'size or sha256 differs from SP11/PLAN.TXT'
        cases = {
            'a data file with other bytes of the same size': (write('STATE.BIN', bytes(1024)), f'SP11/STATE.BIN: {differs}'),
            'a data file with another length': (write('DATA1.BIN', b'short'), f'SP11/DATA1.BIN: {differs}'),
            'a data file removed': (lambda t: (t / 'SP11' / 'DATA2.BIN').unlink(), 'SP11/DATA2.BIN: missing_input'),
            'a data file emptied': (write('DATA2.BIN', b''), 'SP11/DATA2.BIN: input_empty_file'),
            'the manifest replaced by another well formed one': (write(MANIFEST, fx.listing(ONE)), f'SP11/PAYLOAD.MAN: {differs}'),
            'a hash changed in PLAN.TXT': (plan_with(sha(fx.SAMPLE['STATE.BIN']).encode(), b'0' * 64), f'SP11/STATE.BIN: {differs}'),
            'a size changed in PLAN.TXT': (plan_with(b'size 1024', b'size 1025'), f'SP11/STATE.BIN: {differs}'),
        }
        for what, (edit, problem) in cases.items():
            with self.subTest(what):
                code, result = self.change(edit)
                self.assertEqual((code, result['status']), (1, 'FAIL'), what)
                self.assertIn(problem, result['autorun']['problems'], what)

    def test_a_plan_text_that_lost_or_changed_its_data_section_is_a_failure(self):
        def plan_text(edit):
            return lambda t: (t / PLAN).write_bytes(edit((t / PLAN).read_bytes()))

        block = b'  file DATA2.BIN\r\n        size 102\r\n        sha256 ' + sha(fx.SAMPLE['DATA2.BIN']).encode() + b'\r\n'
        extra = b'  file EXTRA.BIN\r\n        size 5\r\n        sha256 ' + b'1' * 64 + b'\r\n'
        cases = {
            'the whole data section removed': (plan_text(lambda b: b.replace(P.DATA_THREE.encode('ascii'), b'')), 'data_manifest_missing'),
            'the section heading edited': (plan_text(lambda b: b.replace(b'data files (', b'data files, (')), 'plan_text_not_canonical'),
            'one data block removed': (plan_text(lambda b: b.replace(block, b'')), 'manifest_entry_not_staged'),
            'a data block added': (plan_text(lambda b: b.replace(block, block + extra)), 'data_file_not_in_manifest'),
            'a data name in lower case': (plan_text(lambda b: b.replace(b'file STATE.BIN', b'file state.bin')), 'data_name_invalid'),
        }
        for what, (edit, reason) in cases.items():
            with self.subTest(what):
                code, result = self.change(edit)
                self.assertEqual((code, result['status']), (1, 'FAIL'), what)
                self.assertIn(reason, ' | '.join(result['autorun']['problems']), what)

    def test_verify_parses_the_manifest_again_instead_of_trusting_its_hash(self):
        # The tamperer rewrites PAYLOAD.MAN and its record in PLAN.TXT consistently and regenerates the stick tree's
        # manifest: every hash agrees, and only a parse can say that it is no longer a manifest of these files.
        wrong = {
            'manifest_bad_magic': b'\x01' + fx.listing(THREE)[1:],
            'manifest_entry_hash_mismatch': fx.manifest([P.rec('STATE.BIN'), P.rec('DATA1.BIN'), P.rec('DATA2.BIN', digest='f' * 64)]),
            'data_file_not_in_manifest': fx.manifest([P.rec('STATE.BIN'), P.rec('DATA1.BIN')]),
        }
        for reason, blob in wrong.items():
            with self.subTest(reason):
                def edit(tree, blob=blob):
                    plan = autorun.parse_plan_text((tree / PLAN).read_bytes())
                    (tree / 'SP11' / MANIFEST).write_bytes(blob)
                    record = autorun.DataFile(MANIFEST, len(blob), sha(blob))
                    (tree / PLAN).write_bytes(autorun.render_plan_text(replace(plan, data=(record, *plan.data[1:]))))
                code, result = self.change(edit)
                self.assertEqual((code, result['status']), (1, 'FAIL'))
                self.assertTrue(any(p.startswith(reason + ':') for p in result['autorun']['problems']), result['autorun'])

    def test_a_symlinked_data_file_is_a_failure(self):
        tree, _ = self.staged_payload(ONE)
        elsewhere = self.scratch / 'elsewhere.bin'
        elsewhere.write_bytes(fx.SAMPLE['STATE.BIN'])
        (tree / 'SP11' / 'STATE.BIN').unlink()
        (tree / 'SP11' / 'STATE.BIN').symlink_to(elsewhere)
        code, result = self.verify(tree)
        self.assertEqual((code, result['status']), (1, 'FAIL'))

    def test_a_data_file_that_the_stick_tree_manifest_does_not_list_is_a_failure(self):
        tree, _ = self.staged_payload(ONE)
        listing = tree / MAN_REL
        listing.write_text(''.join(line for line in listing.read_text().splitlines(True) if 'STATE.BIN' not in line))
        result = self.verify(tree)[1]
        self.assertEqual(result['status'], 'FAIL')
        self.assertIn('manifest_unlisted_extra_file:SP11/STATE.BIN', result['manifest_mismatches'])

    def test_a_removed_or_replaced_runner_image_is_still_a_failure(self):
        for edit in (lambda t: (t / 'SP11' / RUNNER).unlink(), lambda t: (t / 'SP11' / RUNNER).write_bytes(make_pe(subsystem=10, body=b'other'))):
            code, result = self.change(edit, ONE)
            self.assertEqual((code, result['status']), (1, 'FAIL'))
            self.assertTrue(any(p.startswith(f'SP11/{RUNNER}') for p in result['autorun']['problems']), result['autorun'])


class Interop(PayloadTree):
    """The manifest tool of the q1n1 fork writes what this tool stages. Its code runs only to write manifests into this
    test's scratch directory: nothing is booted, and nothing is written to that repository (no bytecode)."""

    def manifest_tool(self):
        directory = os.environ.get('Q1N1_DIR')
        if not directory:
            print('SKIP: Q1N1_DIR (a q1n1 checkout, for tools/sp11_payload_manifest.py) is not set')
            self.skipTest('Q1N1_DIR is not set')
        path = Path(directory) / 'tools' / 'sp11_payload_manifest.py'
        self.assertTrue(path.is_file(), 'Q1N1_DIR has no tools/sp11_payload_manifest.py')
        saved_path, saved_modules = list(sys.path), set(sys.modules)
        try:
            return load('q1n1_sp11_payload_manifest', path)
        finally:
            sys.path[:] = saved_path
            for name in set(sys.modules) - saved_modules - {'q1n1_sp11_payload_manifest'}:
                sys.modules.pop(name, None)

    def stage_from(self, out, names, runner=None, files_name='interop'):
        """`autorun` for the manifest and `names` written to out/SP11 by q1n1's tool; returns (code, result)."""
        runner = runner or self.src / 'payload.efi'
        argv = ['autorun', '--dest', self.dest, '--no-pins', '--name', files_name,
                '--file', SLBOUNCE, self.src / 'mark.efi', self.digest('mark.efi'), '--file', RUNNER, runner, stage.sha256_file(runner)]
        for name in [MANIFEST, *names]:
            argv += ['--data', name, out / 'SP11' / name, stage.sha256_file(out / 'SP11' / name)]
        return self.run_main(argv + ['--step', STEP])

    def test_a_manifest_of_the_q1n1_tool_is_accepted_and_verified(self):
        tool = self.manifest_tool()
        for count in (1, 3, 5):
            with self.subTest(files=count):
                tree = self.default_tree()
                out = self.scratch / f'q1n1-out-{count}'
                entries = tool.write_tree(out, [self.write(f'in/q1n1/file{k}.bin', bytes([k + 1]) * (200 + 17 * k)) for k in range(count)])
                code, result = self.stage_from(out, [path.rsplit('\\', 1)[1] for path, _, _, _ in entries])
                self.assertEqual((code, result['status']), (0, 'AUTORUN_STAGED'), result)
                parsed = autorun.parse_payload_manifest((tree / 'SP11' / MANIFEST).read_bytes())
                self.assertEqual([(e.size, e.sha256, e.path) for e in parsed], [(size, digest, path) for path, size, digest, _ in entries])
                self.assertEqual(self.verify(tree)[1]['autorun']['problems'], [])
                self.assertEqual(self.run_main(['autorun', '--dest', tree, '--remove'])[1]['status'], 'AUTORUN_REMOVED')

    def test_a_file_the_q1n1_manifest_does_not_list_is_refused(self):
        tool = self.manifest_tool()
        self.default_tree()
        out = self.scratch / 'q1n1-out-extra'
        tool.write_tree(out, [self.write('in/q1n1/state.bin', fx.SAMPLE['STATE.BIN'])])
        (out / 'SP11' / 'EXTRA.BIN').write_bytes(b'not in the manifest')
        before = self.snapshot(self.dest)
        code, result = self.stage_from(out, ['STATE.BIN', 'EXTRA.BIN'])
        self.assertEqual((code, result.get('reason')), (1, 'data_file_not_in_manifest'), result)
        self.assertEqual(self.snapshot(self.dest), before)

    def test_the_real_runner_image_and_state_payload_pass_the_role_checks_and_the_round_trip(self):
        tool = self.manifest_tool()
        real = {key: Path(os.environ[var]) for key, var in (('runner', 'SP11_Q1N1_PAYLOAD_EFI'), ('state', 'SP11_PAYLOAD_STATE_BIN'))
                if os.environ.get(var) and Path(os.environ[var]).is_file()}
        if len(real) != 2:
            print('SKIP: SP11_Q1N1_PAYLOAD_EFI and SP11_PAYLOAD_STATE_BIN (files inside this tree) are not both set')
            self.skipTest('real payload images not given')
        tree = self.default_tree()
        out = self.scratch / 'q1n1-out-real'
        tool.write_tree(out, [real['state']])
        code, result = self.stage_from(out, ['STATE.BIN'], runner=real['runner'], files_name='real-payload')
        self.assertEqual((code, result['status']), (0, 'AUTORUN_STAGED'), result)
        self.assertEqual(self.verify(tree)[1]['autorun']['problems'], [])
        self.assertEqual(self.run_main(['autorun', '--dest', tree, '--remove'])[1]['status'], 'AUTORUN_REMOVED')


class Hygiene(unittest.TestCase):
    def test_spdx_sizes_and_no_machine_paths(self):
        prefixes = ('/' + 'Users' + '/', '/' + 'home' + '/')
        for name in ('test-sp11-autorun-payload.py', 'test-sp11-autorun-payload-tree.py', 'sp11_autorun_fixtures.py'):
            path = TOOLS / name
            text = path.read_text()
            self.assertIn('SPDX-License-Identifier: MIT', '\n'.join(text.splitlines()[:3]), name)
            self.assertLess(len(text.splitlines()), 1500, name)
            self.assertLess(path.stat().st_size, 50 * 1024, name)
            for prefix in prefixes:
                self.assertNotIn(prefix, text, f'{name} names a home directory')


if __name__ == '__main__':
    unittest.main()
