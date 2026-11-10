#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Host tests for the --target layer of tools/cycle.py (tools/cycle_lib/targets.py). No machine access.

Everything runs in process against stubs: a workspace (tool sources, process table), a clock, a hamoa_thermal module in
sys.modules (the real one is written elsewhere), a temporary pins directory and, for the dispatch cases, stand-ins for
the command modules. subprocess, sockets and os.system are blocked for the whole run, so no case can reach a serial
port, a USB device, a block device or a child process. Every fixture value is synthetic.

  python3 tools/test-cycle-targets.py [-v] [-k NAME]
"""
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
import contextlib
import copy
import hashlib
import json
import os
import shutil
import signal
import sys
import tempfile
import types
import unittest

sys.dont_write_bytecode = True
TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))

import cycle  # noqa: E402
from cycle_lib import common, targets  # noqa: E402

BUILD_DIR = ROOT / 'build'
EXAMPLE = TOOLS / 'cycle_lib' / 'pins.example.json'
SP11 = targets.TARGETS['sp11']
A16 = targets.TARGETS['a16']

NOW = datetime(2027, 3, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp()
PREFIX = 'p1'
BOOT_ARGS = ['--image-base', '1', '--image-size', '2', '--heap-base', '3', '--heap-size', '4']
GUARD = ['--thermal-guard']

# mode -> (argv after the global --target, the run prefix its power attestation must name)
CASES = {
    'next': (['next'], 'next'),
    'status:check': (['status', '--check'], 'status'),
    'status:run': (['status'], 'status'),
    'capture:check': (['capture', '--prefix', PREFIX, '--check'], PREFIX),
    'capture:run': (['capture', '--prefix', PREFIX], PREFIX),
    'preserve:check': (['preserve', '--prefix', PREFIX, '--check'], PREFIX),
    'preserve:run': (['preserve', '--prefix', PREFIX], PREFIX),
    'retire:check': (['retire', '--prefix', PREFIX, '--check'], PREFIX),
    'retire:run': (['retire', '--prefix', PREFIX], PREFIX),
    'storage:check': (['storage', '--prefix', PREFIX, 'persistence', '--check'], PREFIX),
    'storage:run': (['storage', '--prefix', PREFIX, 'persistence'], PREFIX),
    'launch:check-inputs': (['launch', '--candidate', 'dir/c1.json', '--check-inputs'], 'c1'),
    'launch:prepare': (['launch', '--candidate', 'dir/c1.json', '--prepare'], 'c1'),
    'launch:boot': (['launch', '--candidate', 'dir/c1.json', '--boot'], 'c1'),
    'launch:boot-child': (['launch', '--candidate', 'dir/c1.json', '--boot-child'], 'c1'),
    'bootstrap-smoke:check': (['bootstrap-smoke', '--prefix', PREFIX, '--check'] + BOOT_ARGS, PREFIX),
    'bootstrap-smoke:prepare': (['bootstrap-smoke', '--prefix', PREFIX, '--prepare'] + BOOT_ARGS, PREFIX),
    'bootstrap-smoke:boot': (['bootstrap-smoke', '--prefix', PREFIX, '--boot'] + BOOT_ARGS, PREFIX),
    'speed-test:check': (['speed-test', '--prefix', PREFIX, '--check'], PREFIX),
    'speed-test:prepare': (['speed-test', '--prefix', PREFIX, '--prepare'], PREFIX),
    'speed-test:boot': (['speed-test', '--prefix', PREFIX, '--boot'], PREFIX),
    'speed-test:decode': (['speed-test', '--prefix', PREFIX, '--decode'], PREFIX),
    'speed-test:compare': (['speed-test', '--compare', 'a1', 'b1'], 'speed-test'),
    'installed-fresh:check': (['installed-fresh', '--prefix', PREFIX, '--check'] + GUARD, PREFIX),
    'installed-fresh:prepare': (['installed-fresh', '--prefix', PREFIX, '--prepare'] + GUARD, PREFIX),
    'installed-fresh:boot': (['installed-fresh', '--prefix', PREFIX, '--boot'] + GUARD, PREFIX),
    'installed-fresh:quiesce': (['installed-fresh', '--prefix', PREFIX, '--quiesce'] + GUARD, PREFIX),
}
UNCLASSIFIED = {
    'observe': ['observe', 'native', '--launch', 'L', '--', 'x'],
    'substituted': ['substituted', '--launch', 'L', '--replace', 'R'],
}

# The classification, written out independently of targets.MODES so that a silent edit of the table is caught.
PASSIVE, HOST, IO, CODE, WRITE, CLOCK = (
    'passive', 'host-build', 'target-io', 'code-load', 'storage-write', 'clock-change')
EXPECTED_CLASSES = {
    'next': (PASSIVE,), 'status:check': (PASSIVE,), 'capture:check': (PASSIVE,), 'preserve:check': (PASSIVE,),
    'retire:check': (PASSIVE,), 'storage:check': (PASSIVE,), 'launch:check-inputs': (PASSIVE,),
    'bootstrap-smoke:check': (PASSIVE,), 'speed-test:check': (PASSIVE,), 'installed-fresh:check': (PASSIVE,),
    'preserve:run': (HOST,), 'bootstrap-smoke:prepare': (HOST,), 'speed-test:prepare': (HOST,),
    'speed-test:decode': (HOST,), 'speed-test:compare': (HOST,), 'installed-fresh:prepare': (HOST,),
    'status:run': (IO,), 'capture:run': (IO,), 'storage:run': (IO,),
    'retire:run': (IO, CODE), 'bootstrap-smoke:boot': (IO, CODE), 'speed-test:boot': (IO, CODE),
    'launch:prepare': (IO, CODE, CLOCK), 'launch:boot': (IO, CODE, WRITE), 'launch:boot-child': (IO, CODE, WRITE),
    'installed-fresh:boot': (IO, CODE, WRITE, CLOCK), 'installed-fresh:quiesce': (IO, CLOCK),
}
PASSIVE_MODES = sorted(m for m, c in EXPECTED_CLASSES.items() if c == (PASSIVE,))
NON_PASSIVE = sorted(m for m, c in EXPECTED_CLASSES.items() if c != (PASSIVE,))
TARGET_IO_MODES = [m for m in NON_PASSIVE if IO in EXPECTED_CLASSES[m]]
HOST_BUILD_MODES = [m for m in NON_PASSIVE if IO not in EXPECTED_CLASSES[m]]

LOADING = ('disable_secure_boot', 'suspend_bitlocker', 'use_tcblaunch')
STORAGE = ('create_snapintosh_gpt_entry', 'enable_nvme_window_writes', 'cycle_mode_may_write_nvme')
CLOCKS = ('raise_gpu_cpu_clock_voltage',)
AUTH_KEYS = LOADING + ('add_uefi_boot_entry_or_write_esp',) + STORAGE + CLOCKS
CLASS_AUTH = {PASSIVE: (), HOST: (), IO: LOADING, CODE: LOADING, WRITE: STORAGE, CLOCK: CLOCKS}
REQUIRED_PINS = (
    (('platform_facts', 'uefi_version'), 'text'),
    (('platform_facts', 'windows_build'), 'text'),
    (('platform_facts', 'secure_boot_state'), 'text'),
    (('tcblaunch_sha256',), 'sha256'),
    (('boot_media_manifest_sha256',), 'sha256'),
    (('bootinfo',), 'object'),
    (('host_link', 'selected'), 'text'),
)
BAD_PIN_VALUES = {
    'text': [None, '', '   ', '<placeholder>', '<x>', 7, ['x']],
    'sha256': [None, '<placeholder>', 'A' * 64, 'a' * 63, 'a' * 65, 'g' * 64, 7],
    'object': [None, {}, [], 'x'],
}
# Options that raise clocks, voltage or bandwidth votes, with an argument vector that sets each one.
FORBIDDEN = {
    'gpu_opp_mhz': ['--gpu-opp-mhz', '915'], 'gpu_dvfs': ['--gpu-dvfs'],
    'gpu_dvfs_ceiling': ['--gpu-dvfs-ceiling', '800'], 'gpu_dvfs_hold': ['--gpu-dvfs-hold', '100'],
    'gpu_dvfs_defer': ['--gpu-dvfs-defer'], 'cpu_dvfs': ['--cpu-dvfs'],
    'cpu_dvfs_ceiling': ['--cpu-dvfs-ceiling', '3'], 'ddr_vote': ['--ddr-vote'], 'llcc': ['--llcc'],
    'llcc_table': ['--llcc-table', 'linux'], 'bw_probe': ['--bw-probe'],
}
BOOT_WORD = {
    'launch:boot': 'boot', 'launch:boot-child': 'boot', 'bootstrap-smoke:boot': 'boot', 'speed-test:boot': 'boot',
    'installed-fresh:boot': 'boot', 'launch:prepare': 'prepare', 'installed-fresh:prepare': 'prepare',
    'bootstrap-smoke:prepare': 'prepare', 'speed-test:prepare': 'prepare', 'status:run': 'status',
    'capture:run': 'capture', 'preserve:run': 'preserve', 'retire:run': 'retire', 'storage:run': 'storage',
    'speed-test:decode': 'decode', 'speed-test:compare': 'compare', 'installed-fresh:quiesce': 'quiesce',
}

SOURCES = {
    str(ROOT / 'tools' / 'cycle.py'): '1' * 64,
    str(ROOT / 'tools' / 'cycle_lib' / 'common.py'): '2' * 64,
    str(ROOT / 'tools' / 'cycle_lib' / 'targets.py'): '3' * 64,
    str(ROOT / 'tools' / 'cycle_lib' / 'pins.json'): '4' * 64,
}


THERMAL_SOURCE = ROOT / 'tools' / 'hamoa_thermal.py'
THERMAL_SHA = hashlib.sha256(THERMAL_SOURCE.read_bytes()).hexdigest()


def digest_of(sources):
    """The source digest recomputed from first principles: relative path and hash of every source (pins files left
    out), plus the thermal module that the admission imports, hashed from the file itself."""
    lines = sorted([f'{Path(p).relative_to(ROOT).as_posix()} {h}' for p, h in sources.items()
                    if Path(p).name not in ('pins.json', 'pins.example.json')]
                   + [f'tools/hamoa_thermal.py {THERMAL_SHA}'])
    return hashlib.sha256(('\n'.join(lines) + '\n').encode()).hexdigest()


DIGEST = digest_of(SOURCES)
BLOCKS = []


def setUpModule():
    BUILD_DIR.mkdir(exist_ok=True)
    for name in ('subprocess.Popen', 'socket.socket', 'os.system'):
        patcher = mock.patch(name, side_effect=AssertionError(f'{name} is blocked: these tests never reach a machine'))
        patcher.start()
        BLOCKS.append(patcher)


def tearDownModule():
    while BLOCKS:
        BLOCKS.pop().stop()


def stamp(offset):
    """RFC 3339 UTC time `offset` seconds after NOW."""
    return datetime.fromtimestamp(NOW + offset, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def stub_ws(sources=None, clients=()):
    table = {os.getpid(): (0.0, 0, 'python3 tools/test-cycle-targets.py')}
    for number, command in enumerate(clients, 1):
        table[900000 + number] = (0.0, 1, command)
    ws = types.SimpleNamespace(root=ROOT, processes=lambda: dict(table))
    ws.tool_sources = lambda: dict(SOURCES if sources is None else sources)
    return ws


class Untouchable:
    """Fails the test on any use: the a16 path must not read the workspace or the clock."""

    def __getattr__(self, name):
        raise AssertionError(f'the a16 path touched {name}')

    def __call__(self, *args):
        raise AssertionError('the a16 path called the clock')


NO_CROSS_CHECK = object()


def thermal_module(admit=None, ok=True):
    """Stand-in for tools/hamoa_thermal.py.

    `ok` is the result of power_attestation_ok (or a callable computing it); NO_CROSS_CHECK leaves the function out.
    """
    module = types.ModuleType('hamoa_thermal')
    module.calls = []

    def admit_target(info):
        module.calls.append(('admit_target', copy.deepcopy(info)))
        if admit is not None:
            return admit(info)

    module.admit_target = admit_target
    if ok is not NO_CROSS_CHECK:
        def power_attestation_ok(record, now, run_prefix):
            module.calls.append(('power_attestation_ok', dict(record), now, run_prefix))
            return ok(record, now, run_prefix) if callable(ok) else ok

        module.power_attestation_ok = power_attestation_ok
    return module


def good_section(prefix, recorded_offset=-60):
    return {
        'schema': 'snapintosh-cycle-pins-sp11-v1',
        'platform_facts': {'uefi_version': 'TEST-UEFI', 'windows_build': 'TEST-BUILD',
                           'secure_boot_state': 'TEST-STATE'},
        'tcblaunch_sha256': 'a' * 64,
        'boot_media_manifest_sha256': 'b' * 64,
        'bootinfo': {'current_el': 2, 'test_fixture': True},
        'host_link': {'selected': 'test-link'},
        'power_attestation': {'ac_connected': True, 'battery_percent': None, 'recorded_utc': stamp(recorded_offset),
                              'run_prefix': prefix, 'source_sha256': DIGEST},
        'run_authorization': {'enable_nvme_window_writes': True, 'cycle_mode_may_write_nvme': True,
                              'recorded_utc': stamp(recorded_offset), 'run_prefix': prefix, 'source_sha256': DIGEST},
        'authorizations': dict.fromkeys(AUTH_KEYS, True),
    }


def attest(**changes):
    return lambda section: section['power_attestation'].update(changes)


def authorize(**changes):
    return lambda section: section['authorizations'].update(changes)


def put(path, value):
    def apply(section):
        node = section
        for part in path[:-1]:
            node = node[part]
        node[path[-1]] = value
    return apply


def drop(*path):
    def apply(section):
        node = section
        for part in path[:-1]:
            node = node[part]
        del node[path[-1]]
    return apply


def parse(argv):
    return cycle.parse(argv)


@contextlib.contextmanager
def alarm(seconds):
    def expired(signum, frame):
        raise AssertionError(f'blocked for {seconds} s')

    previous = signal.signal(signal.SIGALRM, expired)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


class TempDirCase(unittest.TestCase):
    """A temporary directory under build/ (a path with no symbolic link, which the pins loader insists on)."""

    def setUp(self):
        holder = tempfile.TemporaryDirectory(dir=BUILD_DIR, prefix='targets-')
        self.addCleanup(holder.cleanup)
        self.dir = Path(holder.name).resolve()

    def install_module(self, name, module):
        missing = object()
        saved = sys.modules.get(name, missing)
        sys.modules[name] = module
        self.addCleanup(lambda: sys.modules.pop(name, None) if saved is missing
                        else sys.modules.__setitem__(name, saved))
        return module


class TableTests(unittest.TestCase):
    def test_rows_have_the_same_keys(self):
        self.assertEqual(set(A16), set(SP11))

    def test_classification_is_the_independent_one(self):
        self.assertEqual(targets.MODES, EXPECTED_CLASSES)

    def test_every_mode_has_a_case_that_parses_to_it(self):
        self.assertEqual(set(CASES), set(targets.MODES))
        for mode, (argv, _) in CASES.items():
            with self.subTest(mode=mode):
                self.assertEqual(targets.mode_of(parse(['--target', 'sp11'] + argv)), mode)

    def test_passive_modes_are_exactly_the_passive_classification(self):
        self.assertEqual(sorted(SP11['passive_modes']), PASSIVE_MODES)
        self.assertEqual(len(PASSIVE_MODES), 10)

    def test_sp11_policy_data(self):
        self.assertTrue(SP11['enforced'])
        self.assertEqual(SP11['pins_section'], 'sp11')
        self.assertEqual(SP11['authorization_keys'], AUTH_KEYS)
        self.assertEqual(SP11['per_run_authorizations'], ('enable_nvme_window_writes', 'cycle_mode_may_write_nvme'))
        self.assertEqual(A16['per_run_authorizations'], ())
        self.assertEqual(SP11['class_authorizations'], CLASS_AUTH)
        self.assertEqual(SP11['required_pins'], REQUIRED_PINS)
        self.assertEqual(SP11['power']['floor_percent'], 50)
        self.assertEqual(SP11['power']['max_age_seconds'], 1800)
        self.assertEqual(SP11['power']['future_skew_seconds'], 60)
        self.assertEqual(SP11['power']['contract_keys'],
                         ('ac_connected', 'battery_percent', 'recorded_utc', 'run_prefix'))
        self.assertEqual(SP11['power']['attestation_keys'], SP11['power']['contract_keys'] + ('source_sha256',))
        self.assertEqual((SP11['thermal']['module'], SP11['thermal']['admit'], SP11['thermal']['mandatory']),
                         ('hamoa_thermal', 'admit_target', True))
        self.assertTrue(SP11['singleton'])

    def test_a16_policy_data(self):
        self.assertFalse(A16['enforced'])
        self.assertTrue(A16['singleton'])
        self.assertEqual(A16['thermal']['module'], 'a16_thermal')
        self.assertEqual(targets.DEFAULT_TARGET, 'a16')

    def test_forbidden_options_are_the_expected_set(self):
        self.assertEqual(set(SP11['forbidden_options']), set(FORBIDDEN))

    def test_forbidden_option_defaults_equal_the_parser_defaults(self):
        absent = vars(parse(['installed-fresh', '--prefix', PREFIX, '--check']))
        for dest, off in SP11['forbidden_options'].items():
            with self.subTest(option=dest):
                self.assertIn(dest, absent)
                self.assertEqual(absent[dest], off)

    def test_cp_memcpy_cap_is_one_mib_for_every_target(self):
        for name, row in targets.TARGETS.items():
            with self.subTest(target=name):
                self.assertEqual(row['max_cp_memcpy_bytes'], 1 << 20)


class CpMemcpyTests(unittest.TestCase):
    def test_up_to_the_cap_is_accepted(self):
        for name in targets.TARGETS:
            for size in (0, 1, 4096, 1 << 20):
                with self.subTest(target=name, size=size):
                    targets.check_cp_memcpy(name, size)

    def refused(self, name, size):
        with self.assertRaises(targets.TargetRefused) as raised:
            targets.check_cp_memcpy(name, size)
        return raised.exception.code

    def test_over_the_cap_is_refused(self):
        for name in targets.TARGETS:
            for size in ((1 << 20) + 1, 4 << 20, 2 ** 64):
                with self.subTest(target=name, size=size):
                    self.assertEqual(self.refused(name, size), 'cp-memcpy-too-large')

    def test_nonsense_is_refused(self):
        for name in targets.TARGETS:
            for size in (-1, True, False, 1.5, '1', None, b'1'):
                with self.subTest(target=name, size=size):
                    self.assertEqual(self.refused(name, size), 'cp-memcpy-invalid')

    def test_unknown_target_is_refused(self):
        self.assertEqual(self.refused('x1', 1), 'unknown-target')


class UnknownTargetTests(unittest.TestCase):
    def refused(self, args):
        with self.assertRaises(targets.TargetRefused) as raised:
            targets.enforce(args, Untouchable())
        return raised.exception

    def test_unknown_and_non_string_targets_are_refused(self):
        for value in ('x1', 'A16', 'sp11 ', '', None, 5, ['sp11']):
            with self.subTest(target=value):
                args = types.SimpleNamespace(command='next', target=value)
                self.assertEqual(self.refused(args).code, 'unknown-target')

    def test_a_namespace_without_a_target_is_a16(self):
        self.assertIsNone(targets.enforce(types.SimpleNamespace(command='launch'), Untouchable()))

    def test_refusals_are_the_tool_s_refusal_type(self):
        error = self.refused(types.SimpleNamespace(command='next', target='x1'))
        self.assertIsInstance(error, common.Refused)
        self.assertEqual(str(error), f'target x1 refused (unknown-target): {error.detail}')


class A16UnchangedTests(unittest.TestCase):
    def test_enforce_reads_nothing_and_never_refuses(self):
        argvs = [a for a, _ in CASES.values()] + list(UNCLASSIFIED.values())
        argvs.append(['installed-fresh', '--prefix', PREFIX, '--boot', '--thermal-guard', '--gpu-dvfs',
                      '--gpu-dvfs-ceiling', '800', '--ddr-vote'])
        argvs.append(['installed-fresh', '--prefix', PREFIX, '--boot'])
        for argv in argvs:
            for flags in ([], ['--target', 'a16']):
                with self.subTest(argv=flags + argv):
                    decision = targets.enforce(parse(flags + argv), Untouchable(), pins_dir=Path('/nonexistent/x'),
                                               clock=Untouchable())
                    self.assertIsNone(decision)


class SP11PassiveTests(TempDirCase):
    def test_passive_modes_are_allowed_without_pins_or_workspace(self):
        for mode in PASSIVE_MODES:
            with self.subTest(mode=mode):
                decision = targets.enforce(parse(['--target', 'sp11'] + CASES[mode][0]), Untouchable(),
                                           pins_dir=self.dir / 'absent', clock=Untouchable())
                got = (decision['target'], decision['mode'], decision['classes'])
                self.assertEqual(got, ('sp11', mode, (PASSIVE,)))
                self.assertIn('UNIMPLEMENTED', decision['notice'])
                self.assertIn(mode, decision['notice'])

    def test_installed_fresh_check_does_not_need_the_guard_flag(self):
        args = parse(['--target', 'sp11', 'installed-fresh', '--prefix', PREFIX, '--check'])
        self.assertEqual(targets.enforce(args, Untouchable(), pins_dir=self.dir)['mode'], 'installed-fresh:check')

    def test_forbidden_options_are_refused_even_in_passive_modes(self):
        for dest, extra in FORBIDDEN.items():
            for kind in ('--check', '--prepare', '--boot', '--quiesce'):
                with self.subTest(option=dest, mode=kind):
                    argv = ['--target', 'sp11', 'installed-fresh', '--prefix', PREFIX, kind, '--thermal-guard']
                    args = parse(argv + extra)
                    with self.assertRaises(targets.TargetRefused) as raised:
                        targets.enforce(args, Untouchable(), pins_dir=self.dir / 'absent')
                    self.assertEqual(raised.exception.code, 'option-forbidden')
                    self.assertIn('--' + dest.replace('_', '-'), str(raised.exception))

    def test_unclassified_commands_are_refused_before_any_pins_are_read(self):
        for command, argv in UNCLASSIFIED.items():
            with self.subTest(command=command):
                with self.assertRaises(targets.TargetRefused) as raised:
                    targets.enforce(parse(['--target', 'sp11'] + argv), Untouchable(), pins_dir=self.dir / 'absent')
                self.assertEqual(raised.exception.code, 'unclassified-mode')

    def test_a_namespace_that_names_no_single_mode_is_refused(self):
        for chosen in ({}, {'prepare': True, 'boot': True}):
            with self.subTest(chosen=chosen):
                flags = dict(check_inputs=False, prepare=False, boot=False, boot_child=False)
                flags.update(chosen)
                args = types.SimpleNamespace(command='launch', target='sp11', **flags)
                with self.assertRaises(targets.TargetRefused) as raised:
                    targets.enforce(args, Untouchable(), pins_dir=self.dir)
                self.assertEqual(raised.exception.code, 'unclassified-mode')


class SP11Gate(TempDirCase):
    """Non-passive sp11 modes against a complete fixture, with one gate broken at a time."""

    def setUp(self):
        super().setUp()
        shutil.copyfile(EXAMPLE, self.dir / 'pins.example.json')
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop('SNAP_BATTERY_MINIMUM', None)
        self.thermal = self.install_module('hamoa_thermal', thermal_module())
        self.sources = dict(SOURCES)
        self.clients = []

    def write(self, mode, change=None):
        section = good_section(CASES[mode][1])
        if change:
            change(section)
        (self.dir / 'pins.json').write_text(json.dumps({'sp11': section}))

    def args(self, mode, *extra):
        return parse(['--target', 'sp11'] + CASES[mode][0] + list(extra))

    def call(self, mode, *extra, pins_dir=None, args=None):
        return targets.enforce(args or self.args(mode, *extra), stub_ws(self.sources, self.clients),
                               pins_dir=pins_dir or self.dir, clock=lambda: NOW)

    def refusal(self, mode, change=None, *extra):
        self.write(mode, change)
        with self.assertRaises(targets.TargetRefused) as raised:
            self.call(mode, *extra)
        return raised.exception

    def assertRefused(self, mode, code, change=None, *extra):
        error = self.refusal(mode, change, *extra)
        self.assertEqual(error.code, code, f'{mode}: {error}')
        return error

    def assertPasses(self, mode, change=None, *extra):
        self.assertRefused(mode, 'unimplemented-flow', change, *extra)

    def code_in(self, directory, mode='status:run'):
        with self.assertRaises(targets.TargetRefused) as raised:
            self.call(mode, pins_dir=directory)
        return raised.exception.code

    # the end state

    def test_all_gates_satisfied_still_ends_at_unimplemented(self):
        for mode in NON_PASSIVE:
            with self.subTest(mode=mode):
                error = self.assertRefused(mode, 'unimplemented-flow')
                self.assertIn(f'no SP11 {BOOT_WORD[mode]} flow implemented', str(error))
                self.assertIn('UNIMPLEMENTED', str(error))

    def test_the_gates_write_nothing(self):
        self.write('launch:boot')
        before = {name: (self.dir / name).read_bytes() for name in os.listdir(self.dir)}
        with self.assertRaises(targets.TargetRefused):
            self.call('launch:boot')
        self.assertEqual({name: (self.dir / name).read_bytes() for name in os.listdir(self.dir)}, before)

    # pins

    def test_no_pins_file(self):
        for mode in NON_PASSIVE:
            with self.subTest(mode=mode):
                self.assertEqual(self.code_in(self.dir, mode), 'no-pins')

    def test_the_example_fallback_is_never_used(self):
        self.assertTrue((self.dir / 'pins.example.json').is_file())
        self.assertEqual(self.code_in(self.dir), 'no-pins')

    def test_pins_without_an_sp11_section(self):
        for document in ('{}', '{"device": "x"}', '{"sp11": null}'):
            with self.subTest(document=document):
                (self.dir / 'pins.json').write_text(document)
                self.assertEqual(self.code_in(self.dir), 'no-sp11-pins')

    def test_the_unedited_example_is_refused_for_every_mode(self):
        for mode in NON_PASSIVE:
            with self.subTest(mode=mode):
                shutil.copyfile(EXAMPLE, self.dir / 'pins.json')
                self.assertEqual(self.code_in(self.dir, mode), 'example-pins')

    def test_an_example_edited_only_in_prose_is_still_incomplete(self):
        document = json.loads(EXAMPLE.read_text())
        document['sp11']['about'] = 'edited'
        (self.dir / 'pins.json').write_text(json.dumps(document))
        self.assertEqual(self.code_in(self.dir), 'incomplete-pins')

    def test_bad_documents(self):
        good = json.dumps({'sp11': good_section('status')})
        documents = {
            'duplicate key': '{"sp11": {}, "sp11": {}}',
            'duplicate nested key': good.replace('"schema"', '"schema": "x", "schema"', 1),
            'NaN': '{"sp11": {"schema": NaN}}', 'Infinity': '{"sp11": {"schema": Infinity}}',
            'empty': '', 'truncated': good[:-3], 'not an object': '[]', 'a string': '"x"', 'null': 'null',
            'section not an object': '{"sp11": 5}', 'section is a list': '{"sp11": []}',
            'wrong schema': good.replace('snapintosh-cycle-pins-sp11-v1', 'snapintosh-cycle-pins-sp11-v0'),
            'no schema': json.dumps({'sp11': {k: v for k, v in good_section('status').items() if k != 'schema'}}),
            'oversized': '{"sp11": {"pad": "' + 'x' * (targets.PINS_MAX_BYTES + 1) + '"}}',
            'deeply nested': '[' * 100000,
        }
        for name, text in documents.items():
            with self.subTest(document=name):
                (self.dir / 'pins.json').write_text(text)
                self.assertEqual(self.code_in(self.dir), 'bad-pins')
        with self.subTest(document='invalid UTF-8'):
            (self.dir / 'pins.json').write_bytes(b'{"sp11": "\xff"}')
            self.assertEqual(self.code_in(self.dir), 'bad-pins')

    def test_a_symbolic_link_for_the_pins_file_is_refused(self):
        self.write('status:run')
        (self.dir / 'pins.json').rename(self.dir / 'real.json')
        os.symlink(self.dir / 'real.json', self.dir / 'pins.json')
        self.assertEqual(self.code_in(self.dir), 'unsafe-pins-path')

    def test_a_symbolic_link_in_the_directory_path_is_refused(self):
        self.write('status:run')
        link = self.dir.parent / (self.dir.name + '-link')
        os.symlink(self.dir, link)
        self.addCleanup(link.unlink)
        self.assertEqual(self.code_in(link), 'unsafe-pins-path')

    def test_dot_dot_in_the_path_is_refused(self):
        self.write('status:run')
        (self.dir / 'sub').mkdir()
        self.assertEqual(self.code_in(self.dir / 'sub' / '..'), 'unsafe-pins-path')

    def test_a_directory_is_not_a_pins_file(self):
        (self.dir / 'pins.json').mkdir()
        self.assertEqual(self.code_in(self.dir), 'unsafe-pins-path')

    def test_a_fifo_is_refused_without_blocking(self):
        os.mkfifo(self.dir / 'pins.json')
        with alarm(10):
            self.assertEqual(self.code_in(self.dir), 'unsafe-pins-path')

    def test_every_required_pin(self):
        modes = ('status:run', 'launch:boot', 'installed-fresh:boot', 'speed-test:compare')
        for path, kind in REQUIRED_PINS:
            for mode in modes:
                with self.subTest(pin='.'.join(path), mode=mode, change='removed'):
                    error = self.assertRefused(mode, 'incomplete-pins', drop(*path))
                    self.assertIn('.'.join(path), str(error))
                for value in BAD_PIN_VALUES[kind]:
                    with self.subTest(pin='.'.join(path), mode=mode, value=value):
                        self.assertRefused(mode, 'incomplete-pins', put(path, value))

    def test_pins_are_checked_before_the_thermal_module_is_asked(self):
        self.assertRefused('launch:boot', 'incomplete-pins', drop('tcblaunch_sha256'))
        self.assertEqual(self.thermal.calls, [])

    # authorizations

    def test_authorization_mapping_per_mode(self):
        for mode in NON_PASSIVE:
            needed = []
            for cls in EXPECTED_CLASSES[mode]:
                needed += [k for k in CLASS_AUTH[cls] if k not in needed]
            for key in AUTH_KEYS:
                for how in ('false', 'absent'):
                    change = authorize(**{key: False}) if how == 'false' else drop('authorizations', key)
                    with self.subTest(mode=mode, key=key, how=how):
                        if key in needed:
                            error = self.assertRefused(mode, 'missing-authorization', change)
                            self.assertIn(key, str(error))
                        else:
                            self.assertPasses(mode, change)

    def test_no_authorizations_at_all(self):
        for mode in NON_PASSIVE:
            with self.subTest(mode=mode):
                needed = EXPECTED_CLASSES[mode] != (HOST,)
                self.write(mode, drop('authorizations'))
                with self.assertRaises(targets.TargetRefused) as raised:
                    self.call(mode)
                self.assertEqual(raised.exception.code, 'missing-authorization' if needed else 'unimplemented-flow')

    def test_malformed_authorizations(self):
        for name, value in (('a string', 'yes'), ('null', None), ('a list', [])):
            with self.subTest(authorizations=name):
                self.assertRefused('launch:boot', 'bad-pins', put(('authorizations',), value))
        for name, value in (('1', 1), ('"true"', 'true'), ('null', None), ('0', 0)):
            with self.subTest(value=name):
                self.assertRefused('launch:boot', 'bad-pins', authorize(use_tcblaunch=value))
        self.assertRefused('launch:boot', 'bad-pins', authorize(skip_every_check=True))

    def test_a_non_boolean_is_refused_even_for_a_key_the_mode_does_not_need(self):
        self.assertRefused('status:run', 'bad-pins', authorize(raise_gpu_cpu_clock_voltage='true'))

    # thermal guard and options

    def test_the_thermal_guard_cannot_be_off(self):
        for kind in ('--prepare', '--boot', '--quiesce'):
            with self.subTest(mode=kind):
                args = parse(['--target', 'sp11', 'installed-fresh', '--prefix', PREFIX, kind])
                self.write('installed-fresh:boot')
                with self.assertRaises(targets.TargetRefused) as raised:
                    self.call(None, args=args)
                self.assertEqual(raised.exception.code, 'thermal-guard-required')
        self.assertPasses('installed-fresh:boot')

    def test_forbidden_options_are_refused_in_every_installed_fresh_mode(self):
        for dest, extra in FORBIDDEN.items():
            for mode in ('installed-fresh:prepare', 'installed-fresh:boot', 'installed-fresh:quiesce'):
                with self.subTest(option=dest, mode=mode):
                    self.assertRefused(mode, 'option-forbidden', None, *extra)

    # power attestation

    def test_missing_attestation(self):
        changes = {'absent': drop('power_attestation'), 'null': put(('power_attestation',), None),
                   'empty': put(('power_attestation',), {}),
                   'all null': put(('power_attestation',), dict.fromkeys(SP11['power']['attestation_keys']))}
        for mode in NON_PASSIVE:
            for name, change in changes.items():
                with self.subTest(mode=mode, attestation=name):
                    self.assertRefused(mode, 'no-attestation', change)

    def test_the_missing_attestation_message_tells_the_operator_what_to_record(self):
        error = self.assertRefused('launch:boot', 'no-attestation', drop('power_attestation'))
        self.assertIn('"c1"', str(error))
        self.assertIn(DIGEST, str(error))

    def test_expired_attestation(self):
        for mode in NON_PASSIVE:
            with self.subTest(mode=mode):
                self.assertRefused(mode, 'attestation-expired', attest(recorded_utc=stamp(-1801)))
                self.assertRefused(mode, 'attestation-expired', attest(recorded_utc=stamp(-86400 * 365)))

    def test_attestation_age_boundaries(self):
        self.assertPasses('launch:boot', attest(recorded_utc=stamp(-1800)))
        self.assertPasses('launch:boot', attest(recorded_utc=stamp(60)))
        self.assertRefused('launch:boot', 'attestation-invalid', attest(recorded_utc=stamp(61)))

    def test_fractional_seconds_and_explicit_utc_offset_are_accepted(self):
        for text in (stamp(-60)[:-1] + '.250Z', stamp(-60)[:-1] + '+00:00'):
            with self.subTest(recorded=text):
                self.assertPasses('launch:boot', attest(recorded_utc=text))

    def test_attestation_for_another_run_prefix(self):
        for mode in NON_PASSIVE:
            with self.subTest(mode=mode):
                self.assertRefused(mode, 'attestation-prefix', attest(run_prefix='another-run'))

    def test_the_prefix_comes_from_the_candidate_for_launch_and_the_command_otherwise(self):
        self.assertPasses('launch:boot', attest(run_prefix='c1'))
        self.assertRefused('launch:boot', 'attestation-prefix', attest(run_prefix='dir/c1.json'))
        self.assertRefused('launch:boot', 'attestation-prefix', attest(run_prefix='launch'))
        self.assertRefused('status:run', 'attestation-prefix', attest(run_prefix=PREFIX))
        for mode, (argv, prefix) in CASES.items():
            with self.subTest(mode=mode):
                self.assertEqual(targets.run_prefix_of(parse(['--target', 'sp11'] + argv)), prefix)
        self.assertEqual(targets.run_prefix_of(parse(['speed-test', '--check'])), 'speed-test')
        self.assertEqual(targets.run_prefix_of(parse(['launch', '--candidate', 'c9', '--prepare'])), 'c9')

    def test_attestation_for_other_tool_sources(self):
        for mode in NON_PASSIVE:
            with self.subTest(mode=mode):
                self.assertRefused(mode, 'attestation-source', attest(source_sha256='9' * 64))

    def test_a_changed_source_after_the_attestation_is_refused(self):
        self.write('launch:boot')
        self.assertRefused('launch:boot', 'unimplemented-flow')
        for name in list(SOURCES):
            if Path(name).name in ('pins.json', 'pins.example.json'):
                continue
            with self.subTest(source=Path(name).name):
                self.sources = dict(SOURCES)
                self.sources[name] = 'f' * 64
                self.assertRefused('launch:boot', 'attestation-source')
        self.sources = dict(SOURCES)
        self.sources[str(ROOT / 'tools' / 'cycle_lib' / 'new_module.py')] = 'f' * 64
        self.assertRefused('launch:boot', 'attestation-source')

    def test_editing_pins_json_does_not_invalidate_the_attestation(self):
        self.sources[str(ROOT / 'tools' / 'cycle_lib' / 'pins.json')] = 'e' * 64
        self.assertPasses('launch:boot')

    def test_malformed_attestations(self):
        cases = {
            'extra key': lambda s: s['power_attestation'].update(extra=1),
            'missing source': drop('power_attestation', 'source_sha256'),
            'missing prefix': drop('power_attestation', 'run_prefix'),
            'not an object': put(('power_attestation',), 'attested'),
            'a list': put(('power_attestation',), []),
            'ac as text': attest(ac_connected='yes'), 'ac as number': attest(ac_connected=1),
            'ac null': attest(ac_connected=None),
            'battery bool': attest(battery_percent=True), 'battery float': attest(battery_percent=50.0),
            'battery text': attest(battery_percent='50'), 'battery 101': attest(battery_percent=101),
            'battery -1': attest(battery_percent=-1),
            'empty prefix': attest(run_prefix=''), 'prefix not text': attest(run_prefix=7),
            'short digest': attest(source_sha256='a' * 63), 'upper-case digest': attest(source_sha256='A' * 64),
            'digest null': attest(source_sha256=None),
            'time as number': attest(recorded_utc=1800000000), 'time null': attest(recorded_utc=None),
            'time garbage': attest(recorded_utc='yesterday'),
            'time with offset': attest(recorded_utc='2027-03-01T12:00:00+01:00'),
            'time without zone': attest(recorded_utc='2027-03-01T11:59:00'),
            'time with a space': attest(recorded_utc='2027-03-01 11:59:00Z'),
            'time with a newline': attest(recorded_utc=stamp(-60) + '\n'),
            'time impossible': attest(recorded_utc='2027-13-45T11:59:00Z'),
            'time leap second': attest(recorded_utc='2027-03-01T11:59:60Z'),
            'time non-ASCII digits': attest(recorded_utc='\u0662\u0660\u0662\u0667-03-01T11:59:00Z'),
        }
        for name, change in cases.items():
            for mode in ('status:run', 'launch:boot', 'installed-fresh:boot'):
                with self.subTest(attestation=name, mode=mode):
                    self.assertRefused(mode, 'attestation-invalid', change)

    def test_power_policy(self):
        on_battery = lambda percent: attest(ac_connected=False, battery_percent=percent)  # noqa: E731
        self.assertRefused('status:run', 'power-insufficient', on_battery(None))
        self.assertRefused('status:run', 'power-insufficient', on_battery(49))
        self.assertRefused('status:run', 'power-insufficient', on_battery(0))
        self.assertPasses('status:run', on_battery(50))
        self.assertPasses('status:run', on_battery(100))
        self.assertPasses('status:run', attest(ac_connected=True, battery_percent=None))
        self.assertPasses('status:run', attest(ac_connected=True, battery_percent=3))

    def test_the_battery_minimum_option_and_environment_raise_the_floor(self):
        on_battery = lambda percent: attest(ac_connected=False, battery_percent=percent)  # noqa: E731
        self.assertRefused('status:run', 'power-insufficient', on_battery(79), '--battery-minimum', '80')
        self.assertPasses('status:run', on_battery(80), '--battery-minimum', '80')
        os.environ['SNAP_BATTERY_MINIMUM'] = '90'
        self.assertRefused('status:run', 'power-insufficient', on_battery(89))
        self.assertPasses('status:run', on_battery(90))

    def test_a_battery_minimum_below_the_floor_is_refused_even_on_ac(self):
        self.assertRefused('status:run', 'power-insufficient', attest(ac_connected=True), '--battery-minimum', '40')

    def test_the_attestation_cannot_come_from_the_command_line(self):
        self.write('launch:boot', drop('power_attestation'))
        args = self.args('launch:boot')
        args.ac_connected, args.battery_percent = True, 100
        args.power_attestation = good_section('c1')['power_attestation']
        with self.assertRaises(targets.TargetRefused) as raised:
            self.call('launch:boot', args=args)
        self.assertEqual(raised.exception.code, 'no-attestation')

    # thermal admission

    def test_the_thermal_module_is_missing(self):
        self.install_module('hamoa_thermal', None)
        for mode in NON_PASSIVE:
            with self.subTest(mode=mode):
                self.assertRefused(mode, 'thermal-missing')

    def test_the_thermal_module_has_no_admission(self):
        module = types.ModuleType('hamoa_thermal')
        module.power_attestation_ok = lambda record, now, run_prefix: True
        self.install_module('hamoa_thermal', module)
        self.assertRefused('launch:boot', 'thermal-missing')

    def test_the_thermal_module_fails_to_import(self):
        with mock.patch.object(targets.importlib, 'import_module', side_effect=RuntimeError('broken module')):
            error = self.assertRefused('launch:boot', 'thermal-error')
        self.assertIn('broken module', str(error))

    def test_the_thermal_admission_refuses(self):
        def refuse(info):
            raise ValueError('SP11 fingerprint not yet measured (X2)')

        self.install_module('hamoa_thermal', thermal_module(admit=refuse))
        for mode in NON_PASSIVE:
            with self.subTest(mode=mode):
                error = self.assertRefused(mode, 'thermal-refused')
                self.assertIn('SP11 fingerprint not yet measured (X2)', str(error))

    def test_the_thermal_admission_returning_false_refuses(self):
        self.install_module('hamoa_thermal', thermal_module(admit=lambda info: False))
        self.assertRefused('launch:boot', 'thermal-refused')

    def test_the_thermal_admission_failing_is_an_error_not_a_pass(self):
        for exception in (RuntimeError('broken'), KeyError('info'), TypeError('arity')):
            with self.subTest(exception=type(exception).__name__):
                def explode(info, exception=exception):
                    raise exception

                self.install_module('hamoa_thermal', thermal_module(admit=explode))
                self.assertRefused('launch:boot', 'thermal-error')

    def test_the_thermal_admission_succeeds_with_none_or_true(self):
        for result in (None, True):
            with self.subTest(result=result):
                self.install_module('hamoa_thermal', thermal_module(admit=lambda info, result=result: result))
                self.assertPasses('launch:boot')

    def test_the_thermal_module_gets_the_recorded_boot_info_and_the_attestation(self):
        self.assertPasses('launch:boot')
        calls = {call[0]: call for call in self.thermal.calls}
        self.assertEqual(list(calls), ['power_attestation_ok', 'admit_target'])
        self.assertEqual(calls['admit_target'][1], {'current_el': 2, 'test_fixture': True})
        _, record, now, prefix = calls['power_attestation_ok']
        self.assertEqual(record, {'ac_connected': True, 'battery_percent': None, 'recorded_utc': stamp(-60),
                                  'run_prefix': 'c1'})
        self.assertEqual((now, prefix), (NOW, 'c1'))
        self.assertIsInstance(now, float)

    def test_the_attestation_cross_check_must_return_true(self):
        for result in (False, None, 0, 1, 'yes', [], {}):
            with self.subTest(result=result):
                self.install_module('hamoa_thermal', thermal_module(ok=result))
                self.assertRefused('launch:boot', 'attestation-rejected')

    def test_the_attestation_cross_check_raising_refuses(self):
        def explode(record, now, run_prefix):
            raise ValueError('stale')

        self.install_module('hamoa_thermal', thermal_module(ok=explode))
        error = self.assertRefused('launch:boot', 'attestation-rejected')
        self.assertIn('stale', str(error))

    def test_a_thermal_module_without_the_cross_check_is_refused_not_skipped(self):
        # The cross-check ties the operator's power record to the thermal module's own staleness rules. A module
        # that lacks it used to be admitted with the check silently skipped (a fail-open); it now refuses, whatever
        # its admission says.
        for admit in (None, lambda info: True, lambda info: False):
            with self.subTest(admit=admit):
                self.install_module('hamoa_thermal', thermal_module(ok=NO_CROSS_CHECK, admit=admit))
                error = self.assertRefused('launch:boot', 'thermal-missing')
                self.assertIn('power_attestation_ok', str(error))

    def test_the_thermal_module_is_not_asked_before_the_attestation_is_valid(self):
        self.assertRefused('launch:boot', 'attestation-expired', attest(recorded_utc=stamp(-5000)))
        self.assertRefused('launch:boot', 'missing-authorization', authorize(use_tcblaunch=False))
        self.assertEqual(self.thermal.calls, [])

    # single hardware client

    def test_a_running_hardware_client_blocks_every_target_mode(self):
        self.clients = ['python3 tools/cycle.py launch --candidate c0 --boot']
        for mode in TARGET_IO_MODES:
            with self.subTest(mode=mode):
                self.assertRefused(mode, 'hardware-client-running')

    def test_host_only_modes_do_not_check_for_a_client(self):
        self.clients = ['python3 tools/cycle.py launch --candidate c0 --boot']
        for mode in HOST_BUILD_MODES:
            with self.subTest(mode=mode):
                self.assertPasses(mode)

    def test_a_client_started_with_the_global_target_option_is_seen(self):
        for command in ('python3 tools/cycle.py --target a16 launch --candidate c0 --boot',
                        'python3 tools/cycle.py --target=sp11 storage --prefix x persistence'):
            with self.subTest(command=command):
                self.clients = [command]
                self.assertRefused('status:run', 'hardware-client-running')

    def test_an_offline_client_does_not_count(self):
        self.clients = ['python3 tools/cycle.py --target sp11 status --check', 'python3 tools/cycle.py next']
        self.assertPasses('status:run')


if __name__ == '__main__':
    unittest.main()
