#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of the plan `a1-xhci-s1` (tools/sp11_xhci_s1_plan.py): the step, the pinned hashes, the manifest and the data files, the script the plan
generates, every refusal of the public builder functions it relies on, and the argument vector that stages it. Host only: nothing is staged below a
mounted volume, nothing boots, and a plan authorises nothing.

    python3 tools/test-sp11-xhci-s1-plan.py
    Q1N1_SP11_TREE=<q1n1 checkout> python3 tools/test-sp11-xhci-s1-plan.py     # also: the q1n1 manifest tool writes the same PAYLOAD.MAN

The comparison with the q1n1 manifest tool SKIPs, and says so, unless Q1N1_SP11_TREE names a q1n1 checkout whose build/xhci-out/payloads (or
Q1N1_SP11_PAYLOADS) holds xhci1.bin and xhci0.bin.
"""
from pathlib import Path
import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
import unittest

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(TOOLS))
import sp11_autorun as autorun  # noqa: E402
import sp11_autorun_fixtures as fx  # noqa: E402
import sp11_xhci_s1_plan as plan_mod  # noqa: E402
import sp11_xhci_s1_record as rec  # noqa: E402

TREE = os.environ.get('Q1N1_SP11_TREE')
PAYLOADS = Path(os.environ.get('Q1N1_SP11_PAYLOADS') or (Path(TREE) / 'build' / 'xhci-out' / 'payloads' if TREE else '.'))
BUILD = ROOT / 'build' / 'sp11-xhci-s1-plan-test'
sha = lambda data: hashlib.sha256(data).hexdigest()  # noqa: E731
PAYLOAD1, PAYLOAD0 = bytes(range(256)) * 70, bytes(reversed(range(256))) * 71


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class Plan(unittest.TestCase):
    def test_the_plan_of_the_first_boot_is_one_step_with_the_two_pinned_files_and_the_manifest_and_xhci1(self):
        plan = plan_mod.build(1, PAYLOAD1)
        self.assertEqual(plan.name, 'a1-xhci-s1')
        self.assertEqual(len(plan.steps), 1)
        step = plan.steps[0]
        self.assertEqual((step.slbounce, step.app, step.args), ('SLBOUNCE-DEVHE-MARK.EFI', 'Q1N1-PAYLOAD.EFI', ('--el2',)))
        self.assertEqual((step.slbounce_sha256, step.app_sha256), (rec.SLBOUNCE_SHA256, rec.RUNNER_SHA256))
        self.assertEqual([(d.name, d.size, d.sha256) for d in plan.data],
                         [('PAYLOAD.MAN', len(plan_mod.manifest_for(1, PAYLOAD1)), sha(plan_mod.manifest_for(1, PAYLOAD1))), ('XHCI1.BIN', len(PAYLOAD1), sha(PAYLOAD1))])
        self.assertEqual(plan_mod.STEP, 'SLBOUNCE-DEVHE-MARK.EFI Q1N1-PAYLOAD.EFI --el2')

    def test_the_optional_second_boot_is_a_plan_of_its_own_for_xhci0(self):
        first, second = plan_mod.build(1, PAYLOAD1), plan_mod.build(0, PAYLOAD0)
        self.assertEqual((second.name, [d.name for d in second.data]), ('a1-xhci-s1-ss0', ['PAYLOAD.MAN', 'XHCI0.BIN']))
        self.assertNotEqual(plan_mod.manifest_for(1, PAYLOAD1), plan_mod.manifest_for(0, PAYLOAD0))
        self.assertEqual(first.steps, second.steps)
        entries = autorun.parse_payload_manifest(plan_mod.manifest_for(0, PAYLOAD0))
        self.assertEqual([e.path for e in entries], ['\\SP11\\XHCI0.BIN'], 'a manifest lists the one payload of its boot')

    def test_the_renderings_round_trip_and_the_script_runs_one_step_with_the_two_commands_and_no_banned_argument(self):
        for controller, payload in ((1, PAYLOAD1), (0, PAYLOAD0)):
            with self.subTest(controller=controller):
                plan = plan_mod.build(controller, payload)
                plan_text, script = plan_mod.renderings(plan)
                self.assertEqual(autorun.parse_plan_text(plan_text), plan)
                text = script.decode('ascii')
                self.assertEqual(autorun.script_problems(text), [])
                lines = text.splitlines()
                self.assertEqual(lines.count('load SP11\\SLBOUNCE-DEVHE-MARK.EFI'), 1)
                self.assertEqual(lines.count('SP11\\Q1N1-PAYLOAD.EFI --el2'), 1)
                self.assertEqual(text.count(':step1'), 1)
                self.assertNotIn(':step2', text)
                for banned in autorun.BANNED_ARGUMENTS:
                    self.assertNotIn(banned, text)
                self.assertIn('DONE1.TXT', text)
                self.assertEqual(script, autorun.render_script(plan), 'the same bytes the staging tool compares')

    def test_every_refusal_the_plan_relies_on_has_a_faulty_case(self):
        good_manifest = plan_mod.manifest_for(1, PAYLOAD1)
        cases = (
            ('a manifest of the other controller\'s file', lambda: plan_mod.build(1, PAYLOAD1, plan_mod.manifest_for(0, PAYLOAD1)), 'manifest_entry_not_staged'),
            ('a manifest of other bytes', lambda: plan_mod.build(1, PAYLOAD1, plan_mod.manifest_for(1, PAYLOAD0)), 'manifest_entry_'),
            ('a manifest with two files', lambda: plan_mod.build(1, PAYLOAD1, fx.listing({'XHCI1.BIN': PAYLOAD1, 'XHCI0.BIN': PAYLOAD0})), 'manifest_entry_not_staged'),
            ('a manifest of another magic', lambda: plan_mod.build(1, PAYLOAD1, bytes([good_manifest[0] ^ 1]) + good_manifest[1:]), 'manifest_bad_magic'),
            ('a truncated manifest', lambda: plan_mod.build(1, PAYLOAD1, good_manifest[:-1]), 'manifest_bad_length'),
            ('an empty payload', lambda: plan_mod.build(1, b'', fx.listing({'XHCI1.BIN': b'x'})), 'data_size_out_of_range'),
            ('a malformed slbounce hash', lambda: plan_mod.build(1, PAYLOAD1, slbounce_sha256='4107C28C' + '0' * 56), 'file_hash_malformed'),
            ('a malformed runner hash', lambda: plan_mod.build(1, PAYLOAD1, runner_sha256='abc'), 'file_hash_malformed'),
            ('a step with a banned argument', lambda: autorun.build_plan('x1', ['SLBOUNCE-DEVHE-MARK.EFI Q1N1-PAYLOAD.EFI --boot'],
                                                                         {rec.SLBOUNCE_NAME: rec.SLBOUNCE_SHA256, rec.RUNNER_NAME: rec.RUNNER_SHA256}), 'step_argument_banned'),
            ('a step with an unlisted slbounce build', lambda: autorun.build_plan('x1', ['SLBOUNCE-OLD-NOSWEEP.EFI Q1N1-PAYLOAD.EFI --el2'],
                                                                                  {rec.SLBOUNCE_NAME: rec.SLBOUNCE_SHA256, rec.RUNNER_NAME: rec.RUNNER_SHA256}), 'step_slbounce_banned'),
            ('a controller that does not exist', lambda: plan_mod.build(2, PAYLOAD1), None),
        )
        for label, build, reason in cases:
            with self.subTest(case=label):
                with self.assertRaises((autorun.PlanError, KeyError)) as caught:
                    build()
                if reason:
                    self.assertTrue(getattr(caught.exception, 'reason', '').startswith(reason), getattr(caught.exception, 'reason', caught.exception))

    def test_a_script_that_fails_its_whitelist_is_refused_by_the_renderings(self):
        plan = plan_mod.build(1, PAYLOAD1)
        original = autorun.script_problems
        try:
            autorun.script_problems = lambda text: ['a line the whitelist does not know']
            with self.assertRaises(autorun.PlanError):
                plan_mod.renderings(plan)
        finally:
            autorun.script_problems = original

    def test_the_staging_command_is_one_the_stage_tool_parses_and_names_exactly_the_plan(self):
        stage = load('stage_sp11_for_xhci_s1', TOOLS / 'stage-sp11-boot-media.py')
        argv = rec.staging_command(plan_mod.staging_files(1, PAYLOAD1))
        args = stage.build_argparser().parse_args(['--dest', '<ignored>'][:0] + ['autorun'] + [a if a != '<scratch>' else str(BUILD / 'scratch') for a in argv[1:]])
        self.assertEqual((args.name, args.step), ('a1-xhci-s1', ['SLBOUNCE-DEVHE-MARK.EFI Q1N1-PAYLOAD.EFI --el2']))
        self.assertEqual([f[0] for f in args.file], ['SLBOUNCE-DEVHE-MARK.EFI', 'Q1N1-PAYLOAD.EFI'])
        data = {name: digest for name, _, digest in args.data}
        self.assertEqual(data, {'PAYLOAD.MAN': sha(plan_mod.manifest_for(1, PAYLOAD1)), 'XHCI1.BIN': sha(PAYLOAD1)})
        self.assertEqual(rec.staging_command(plan_mod.staging_files(0, PAYLOAD0))[3:5], ['--name', 'a1-xhci-s1'], 'the record names the plan of the first boot')


@unittest.skipUnless(TREE and (Path(TREE) / 'tools' / 'sp11_payload_manifest.py').is_file() and (PAYLOADS / 'xhci1.bin').is_file() and (PAYLOADS / 'xhci0.bin').is_file(),
                     'Q1N1_SP11_TREE does not name a q1n1 tree with built xhci0.bin and xhci1.bin: the q1n1 manifest tool is not compared')
class WithTheQ1n1Tool(unittest.TestCase):
    def setUp(self):
        self.addCleanup(shutil.rmtree, BUILD, True)

    def test_the_q1n1_manifest_tool_writes_the_manifest_this_plan_expects_and_the_plan_accepts_the_real_binaries(self):
        for controller in (1, 0):
            with self.subTest(controller=controller):
                payload = (PAYLOADS / f'xhci{controller}.bin').read_bytes()
                out = BUILD / f'q1n1-tool-{controller}'
                shutil.rmtree(out, ignore_errors=True)
                done = subprocess.run(['python3', '-B', str(Path(TREE) / 'tools' / 'sp11_payload_manifest.py'), '--out-dir', str(out), str(PAYLOADS / f'xhci{controller}.bin')],
                                      capture_output=True, text=True)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                manifest = (out / 'SP11' / 'PAYLOAD.MAN').read_bytes()
                self.assertEqual(manifest, plan_mod.manifest_for(controller, payload), 'the independent writer and the q1n1 tool disagree')
                plan = plan_mod.build(controller, payload, manifest)
                plan_mod.renderings(plan)
                self.assertEqual((out / 'SP11' / f'XHCI{controller}.BIN').read_bytes(), payload)


if __name__ == '__main__':
    sys.exit(0 if unittest.main(verbosity=1, exit=False).result.wasSuccessful() else 1)
