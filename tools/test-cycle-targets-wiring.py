#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Host tests for how the --target layer is wired into cycle.py, common.py and pins.example.json. No machine access.

Covers the parser, cycle.main dispatch, the hardware-client command-line patterns, the tool-source digest, the A16
prepare/boot source invariant and the SP11 placeholder section of the example pins. The refusal matrix and the shared
fixtures (stubs, synthetic sources, mode tables) are in tools/test-cycle-targets.py, which is loaded here for them; it
is split in two only to keep each file under the 50 KiB limit. subprocess, sockets and os.system are blocked for the
run.

  python3 tools/test-cycle-targets-wiring.py [-v] [-k NAME]
"""
from pathlib import Path
from unittest import mock
import contextlib
import hashlib
import importlib
import importlib.util
import io
import json
import re
import sys
import types
import unittest

sys.dont_write_bytecode = True
TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))

import cycle  # noqa: E402
from cycle_lib import common, targets  # noqa: E402

spec = importlib.util.spec_from_file_location('cycle_targets_matrix', TOOLS / 'test-cycle-targets.py')
F = importlib.util.module_from_spec(spec)
spec.loader.exec_module(F)
ROOT, EXAMPLE, SP11, PREFIX = F.ROOT, F.EXAMPLE, F.SP11, F.PREFIX
CASES, UNCLASSIFIED, PASSIVE_MODES, NON_PASSIVE = F.CASES, F.UNCLASSIFIED, F.PASSIVE_MODES, F.NON_PASSIVE
AUTH_KEYS, REQUIRED_PINS, SOURCES, DIGEST = F.AUTH_KEYS, F.REQUIRED_PINS, F.SOURCES, F.DIGEST
parse, stub_ws = F.parse, F.stub_ws
setUpModule, tearDownModule = F.setUpModule, F.tearDownModule


ABBREVIATED_TARGETS = tuple('--target'[:n] for n in range(len('--t'), len('--target')))
HARDWARE_TAILS = (['status'], ['capture', '--prefix', 'p1'], ['retire', '--prefix', 'p1'],
                  ['launch', '--candidate', 'c1', '--boot'], ['observe', 'native', '--launch', 'L1'],
                  ['storage', '--prefix', 'p1', 'persistence'])


class ParserTests(unittest.TestCase):
    def failure(self, argv):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as raised:
            parse(argv)
        return raised.exception.code, err.getvalue()

    def test_default_is_a16(self):
        self.assertEqual(parse(['status']).target, 'a16')
        self.assertEqual(parse(['next']).target, 'a16')

    def test_explicit_a16_equals_omitted(self):
        for argv in [a for a, _ in CASES.values()] + list(UNCLASSIFIED.values()):
            with self.subTest(argv=argv):
                self.assertEqual(vars(parse(['--target', 'a16'] + argv)), vars(parse(argv)))

    def test_sp11_changes_only_the_target(self):
        for argv in [a for a, _ in CASES.values()]:
            with self.subTest(argv=argv):
                left, right = vars(parse(['--target', 'sp11'] + argv)), vars(parse(argv))
                self.assertEqual((left.pop('target'), right.pop('target')), ('sp11', 'a16'))
                self.assertEqual(left, right)

    def test_equals_form(self):
        self.assertEqual(parse(['--target=sp11', 'next']).target, 'sp11')

    def test_unknown_target_is_an_argparse_error(self):
        code, text = self.failure(['--target', 'x1', 'next'])
        self.assertEqual(code, 2)
        self.assertIn('invalid choice', text)

    def test_missing_value_is_an_argparse_error(self):
        self.assertEqual(self.failure(['--target'])[0], 2)

    def test_target_after_the_subcommand_is_rejected(self):
        code, text = self.failure(['launch', '--candidate', 'c1', '--check-inputs', '--target', 'sp11'])
        self.assertEqual(code, 2)
        self.assertIn('--target', text)

    def test_an_abbreviated_target_is_an_argparse_error(self):
        # `cycle.py --tar a16 retire ...` once parsed as --target and hid the run from the client scan.
        for spelling in ABBREVIATED_TARGETS:
            for argv in ([spelling, 'a16', 'status'], [spelling + '=sp11', 'next']):
                with self.subTest(argv=argv):
                    self.assertEqual(self.failure(argv)[0], 2)

    def test_top_level_help_names_the_choices(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as raised:
            parse(['--help'])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn('--target {a16,sp11}', out.getvalue())


class ClientPatternTests(unittest.TestCase):
    @staticmethod
    def seen(command):
        return any(pattern.search(command) for pattern in common.CLIENT_PATTERNS)

    def test_the_existing_forms_still_match(self):
        for command in ('python3 tools/cycle.py launch --candidate c1 --boot', 'python3 tools/cycle.py status',
                        'python3 tools/cycle.py capture --prefix p1', 'python3 tools/cycle.py retire --prefix p1',
                        'python3 tools/cycle.py storage --prefix p1 snapshot', 'python3 tools/cycle.py observe native'):
            with self.subTest(command=command):
                self.assertTrue(self.seen(command))

    def test_the_global_target_form_matches(self):
        for command in ('python3 tools/cycle.py --target a16 launch --candidate c1 --boot',
                        'python3 tools/cycle.py --target sp11 status',
                        'python3 tools/cycle.py --target=sp11 capture --prefix p1',
                        'python3 tools/cycle.py --target a16 --target sp11 retire --prefix p1'):
            with self.subTest(command=command):
                self.assertTrue(self.seen(command))

    def test_offline_forms_do_not_match(self):
        for command in ('python3 tools/cycle.py launch --candidate c1 --check-inputs',
                        'python3 tools/cycle.py status --check',
                        'python3 tools/cycle.py --target a16 launch --candidate c1 --check-inputs',
                        'python3 tools/cycle.py --target sp11 storage --prefix p1 persistence --check',
                        'python3 tools/cycle.py --tar sp11 storage --prefix p1 persistence --check',
                        'python3 tools/cycle.py --target sp11 next', 'python3 tools/cycle.py next'):
            with self.subTest(command=command):
                self.assertFalse(self.seen(command))

    def test_abbreviated_target_spellings_are_detected_whether_or_not_argparse_accepts_them(self):
        # A copy of cycle.py without allow_abbrev=False that is still running must count as a hardware client.
        for spelling in ABBREVIATED_TARGETS + ('--target',):
            for separator in ('=', ' '):
                for tail in HARDWARE_TAILS:
                    command = f'python3 tools/cycle.py {spelling}{separator}a16 ' + ' '.join(tail)
                    with self.subTest(command=command):
                        self.assertTrue(self.seen(command))

    def test_every_spelling_the_parser_accepts_is_detected_or_rejected(self):
        for spelling in ABBREVIATED_TARGETS + ('--target',):
            for head in ([spelling + '=a16'], [spelling, 'a16']):
                for tail in HARDWARE_TAILS:
                    argv = head + tail
                    with self.subTest(argv=argv):
                        try:
                            with contextlib.redirect_stderr(io.StringIO()):
                                parse(argv)
                        except SystemExit:
                            continue
                        self.assertTrue(self.seen('python3 tools/cycle.py ' + ' '.join(argv)))


class SourceDigestTests(unittest.TestCase):
    def test_matches_the_independent_computation(self):
        self.assertEqual(targets.source_digest(stub_ws()), DIGEST)

    def test_the_pins_files_are_left_out(self):
        sources = dict(SOURCES)
        sources[str(ROOT / 'tools' / 'cycle_lib' / 'pins.json')] = '0' * 64
        sources[str(ROOT / 'tools' / 'cycle_lib' / 'pins.example.json')] = '0' * 64
        self.assertEqual(targets.source_digest(stub_ws(sources)), DIGEST)

    def test_any_other_source_changes_it(self):
        for name in SOURCES:
            if Path(name).name == 'pins.json':
                continue
            with self.subTest(source=Path(name).name):
                sources = dict(SOURCES)
                sources[name] = 'f' * 64
                self.assertNotEqual(targets.source_digest(stub_ws(sources)), DIGEST)
        extra = dict(SOURCES)
        extra[str(ROOT / 'tools' / 'cycle_lib' / 'extra.py')] = 'f' * 64
        self.assertNotEqual(targets.source_digest(stub_ws(extra)), DIGEST)
        removed = {k: v for k, v in SOURCES.items() if not k.endswith('targets.py')}
        self.assertNotEqual(targets.source_digest(stub_ws(removed)), DIGEST)

    def test_it_names_no_machine_path(self):
        outside = {'/elsewhere/x.py': '5' * 64}
        expected = hashlib.sha256((f'tools/hamoa_thermal.py {F.THERMAL_SHA}\n' + 'x.py ' + '5' * 64 + '\n').encode()).hexdigest()
        self.assertEqual(targets.source_digest(stub_ws(outside)), expected)

    def test_the_real_workspace_hashes_the_new_module(self):
        names = {Path(p).name for p in common.Workspace(ROOT).tool_sources()}
        self.assertTrue({'cycle.py', 'targets.py', 'common.py'} <= names)


class A16SourceInvariantTests(F.TempDirCase):
    """The installed-fresh prepare/boot invariant (a changed source refuses the boot), unchanged by the target layer."""

    SOURCES = {'tools/cycle.py': '1' * 64, 'tools/cycle_lib/common.py': '2' * 64}

    def check(self, current):
        fresh = importlib.import_module('cycle_lib.installed_fresh')
        record = {'schema': fresh.SCHEMA, 'prefix': PREFIX, 'device': 'dev0', 'environment': {}, 'profile': 'prof',
                  'boot_argument_addition': '', 'auxkc_pin': {'sha256': 'aux'}, 'variant': 'v', 'flags': [],
                  'gpu_steps': [], 'source_sha256': self.SOURCES, 'inputs_sha256': {}, 'monitor_sha256': 'blob',
                  'elf_sha256': 'blob', 'image': {'firmware_sha256': 'fw'}}
        (self.dir / 'qualification.json').write_text(json.dumps(record))
        guest = types.ModuleType('guestprofile')
        guest.get_profile = lambda name: types.SimpleNamespace(hashes=lambda path: 'fw')
        self.install_module('guestprofile', guest)
        stubs = dict(environment=lambda a: {}, selected=lambda a: ('prof', None, 'aux'), boot_addition=lambda a: '',
                     variant_flags=lambda *a: [], gpu_steps=lambda *a: [], ddr_plan=lambda a: None,
                     llcc_plan=lambda a: None, sha=lambda path: current.get(str(path), 'blob'))
        ws = types.SimpleNamespace(root=ROOT, device='dev0')
        args = types.SimpleNamespace(prefix=PREFIX, device=None)
        with mock.patch.multiple(fresh, **stubs), mock.patch.object(fresh.st, 'flag_sets', lambda root: ([],)):
            return fresh.check(ws, args, self.dir)

    def test_unchanged_sources_pass(self):
        self.assertEqual(self.check(dict(self.SOURCES))['prefix'], PREFIX)

    def test_a_changed_source_refuses_the_boot(self):
        for name in self.SOURCES:
            with self.subTest(source=name):
                with self.assertRaises(common.Refused) as raised:
                    self.check(dict(self.SOURCES, **{name: 'f' * 64}))
                self.assertEqual(str(raised.exception), f'source changed: {name}')


class DispatchTests(F.TempDirCase):
    """cycle.main with the command modules replaced: the policy runs first and nothing behind a refusal is called."""

    MODULES = (('preserve', 'preserve'), ('retire', 'retire'), ('capture', 'main'), ('launch', 'main'),
               ('bootstrap_smoke', 'main'), ('speed_test', 'main'), ('installed_fresh', 'main'), ('status', 'status'),
               ('status', 'next_step'), ('storage', 'main'), ('observers', 'observe'), ('substitute', 'run_step'))

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(common, 'LIB', self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.stubs = {}
        for module_name, function in self.MODULES:
            module = importlib.import_module('cycle_lib.' + module_name)
            returns = {'preserve': {'status': 'stub'}, 'retire': {'status': 'stub'}, 'status': {'status': 'stub'},
                       'next_step': 'stub-step'}.get(function if module_name == 'status' else module_name, 0)
            stub = mock.MagicMock(name=f'{module_name}.{function}', return_value=returns)
            patcher = mock.patch.object(module, function, stub)
            patcher.start()
            self.addCleanup(patcher.stop)
            self.stubs[(module_name, function)] = stub

    def calls(self):
        return sum(stub.call_count for stub in self.stubs.values())

    @staticmethod
    def run_main(*argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cycle.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_a16_dispatches_exactly_as_before(self):
        for argv in [a for a, _ in CASES.values()] + list(UNCLASSIFIED.values()):
            for flags in ([], ['--target', 'a16']):
                before = self.calls()
                with self.subTest(argv=flags + argv):
                    code, out, err = self.run_main(*(flags + argv))
                    self.assertEqual(self.calls(), before + 1)
                    self.assertNotIn('REFUSED', out)
                    self.assertNotIn('--target', err)
                    self.assertEqual(code, 0)

    def test_sp11_passive_modes_run_the_offline_checks_and_say_so(self):
        for mode in PASSIVE_MODES:
            before = self.calls()
            with self.subTest(mode=mode):
                code, out, err = self.run_main('--target', 'sp11', *CASES[mode][0])
                self.assertEqual((code, self.calls()), (0, before + 1))
                self.assertIn('--target sp11', err)
                self.assertIn('UNIMPLEMENTED', err)

    def test_sp11_refusals_stop_before_any_command_module(self):
        argvs = {mode: (CASES[mode][0], 'no-pins') for mode in NON_PASSIVE}
        argvs.update({command: (argv, 'unclassified-mode') for command, argv in UNCLASSIFIED.items()})
        for name, (argv, code_name) in argvs.items():
            with self.subTest(mode=name):
                code, out, err = self.run_main('--target', 'sp11', *argv)
                record = json.loads(out.strip().splitlines()[-1])
                self.assertEqual((code, record['status']), (1, 'REFUSED'))
                self.assertTrue(record['reason'].startswith(f'target sp11 refused ({code_name})'), record['reason'])
                self.assertEqual(self.calls(), 0)
                self.assertNotIn('Traceback', err)

    def test_sp11_forbidden_option_stops_even_a_check(self):
        code, out, _ = self.run_main('--target', 'sp11', 'installed-fresh', '--prefix', PREFIX, '--check', '--gpu-dvfs')
        self.assertEqual(code, 1)
        self.assertIn('option-forbidden', out)
        self.assertEqual(self.calls(), 0)

    def test_the_policy_runs_before_every_command(self):
        for argv in [a for a, _ in CASES.values()] + list(UNCLASSIFIED.values()):
            for failure in (targets.TargetRefused('sp11', 'test', 'stop'), RuntimeError('unexpected')):
                with self.subTest(argv=argv, failure=type(failure).__name__):
                    with mock.patch.object(targets, 'enforce', side_effect=failure):
                        code, out, _ = self.run_main(*argv)
                    self.assertEqual(code, 1)
                    self.assertIn('REFUSED', out)
                    self.assertEqual(self.calls(), 0)


class ExampleTests(unittest.TestCase):
    A16_KEYS = {'about', 'base_launcher', 'battery', 'default_capture_set', 'device', 'image_bases', 'observers',
                'panel_python', 'schema', 'stages', 'stages_source', 'stopped_exits', 'storage', 'txm_qualification'}
    IDENTIFYING = (r'(?i)\b[0-9a-f]{2}([:-][0-9a-f]{2}){5}\b', r'\b\d{1,3}(\.\d{1,3}){3}\b',
                   r'(?i)/(users|home)/|\\users\\', r'(?i)[\w.+-]+@[\w-]+\.[\w.]+',
                   r'(?i)\.local\b|\bssid\b|\bserial\b|\bhostname\b', r'[0-9a-f]{32,}')

    def setUp(self):
        self.text = EXAMPLE.read_text()
        self.document = json.loads(self.text, object_pairs_hook=targets._no_duplicate_keys)
        self.section = self.document['sp11']

    @staticmethod
    def leaves(node, path=()):
        if isinstance(node, dict):
            for key, value in node.items():
                yield from ExampleTests.leaves(value, path + (key,))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                yield from ExampleTests.leaves(value, path + (index,))
        else:
            yield path, node

    def test_the_a16_keys_are_unchanged(self):
        self.assertEqual(set(self.document) - {'sp11'}, self.A16_KEYS)

    def test_the_schema_matches_the_policy_row(self):
        self.assertEqual(self.section['schema'], SP11['pins_schema'])

    def test_the_section_holds_placeholders_only(self):
        for path, value in self.leaves(self.section):
            with self.subTest(path='.'.join(map(str, path))):
                if path in (('schema',), ('about',)) or path[:2] == ('host_link', 'candidates'):
                    self.assertIsInstance(value, str)
                else:
                    self.assertIn(value, (None, False, '<placeholder>'))

    def test_authorizations_are_all_present_and_false(self):
        self.assertEqual(self.section['authorizations'], dict.fromkeys(AUTH_KEYS, False))

    def test_every_required_pin_is_present_in_the_example(self):
        for path, _ in REQUIRED_PINS:
            node = self.section
            for part in path:
                self.assertIn(part, node)
                node = node[part]

    def test_the_attestation_template_has_the_policy_keys(self):
        self.assertEqual(tuple(self.section['power_attestation']), SP11['power']['attestation_keys'])

    def test_nothing_identifies_a_machine_or_a_person(self):
        for pattern in self.IDENTIFYING:
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, json.dumps(self.section)))
        for pattern in self.IDENTIFYING[2:4]:
            with self.subTest(whole_file=pattern):
                self.assertIsNone(re.search(pattern, self.text))


if __name__ == '__main__':
    unittest.main()
