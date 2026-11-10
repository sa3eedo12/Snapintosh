#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Hardening tests for the --target layer of tools/cycle.py (tools/cycle_lib/targets.py). No machine access.

Three latent fail-opens found by an independent review of the SP11 gate:

* The thermal gate skipped the power-attestation cross-check when the thermal module did not define it, admitted any
  falsy return of the admission function (only the literal False refused), and left tools/hamoa_thermal.py out of the
  source digest the operator's attestation is bound to. A module without the cross-check now refuses, only None and
  True admit, and the digest covers the thermal module.
* The NVMe-write authorizations were standing booleans in pins.json, although docs/SURFACE-PRO-11.md section 8 says
  "per run". The two per-run ones must now also be recorded in sp11.run_authorization with a run prefix, the source
  digest and a timestamp that expires, as the power attestation is.
* The 1 MiB CP_MEMCPY cap is not enforced by any cycle flow (none chooses a size); the claim now says so and a test
  ties the cap to the constant the monitor uses where the size is chosen.

The fixtures (stub workspace, thermal stand-in, fixture pins section) are those of tools/test-cycle-targets.py, which
is loaded for them; subprocess, sockets and os.system are blocked for the run.

  python3 tools/test-cycle-targets-hardening.py [-v] [-k NAME]
"""
from pathlib import Path
from unittest import mock
import hashlib
import importlib
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import types
import unittest

sys.dont_write_bytecode = True
TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))

from cycle_lib import common, targets  # noqa: E402

spec = importlib.util.spec_from_file_location('cycle_targets_matrix', TOOLS / 'test-cycle-targets.py')
F = importlib.util.module_from_spec(spec)
spec.loader.exec_module(F)
ROOT, SP11, A16, NOW, DIGEST, SOURCES = F.ROOT, F.SP11, F.A16, F.NOW, F.DIGEST, F.SOURCES
CASES, NON_PASSIVE, EXPECTED_CLASSES, WRITE = F.CASES, F.NON_PASSIVE, F.EXPECTED_CLASSES, F.WRITE
stamp, attest, authorize, drop, put = F.stamp, F.attest, F.authorize, F.drop, F.put
setUpModule, tearDownModule = F.setUpModule, F.tearDownModule

PER_RUN = ('enable_nvme_window_writes', 'cycle_mode_may_write_nvme')
WRITE_MODES = [m for m in NON_PASSIVE if WRITE in EXPECTED_CLASSES[m]]
OTHER_MODES = [m for m in NON_PASSIVE if WRITE not in EXPECTED_CLASSES[m]]
LIMIT = SP11['power']['max_age_seconds']


def run_auth(**changes):
    return lambda section: section['run_authorization'].update(changes)


# The SP11Gate fixtures without its test methods, so that they are not run a second time from here.
class Gate(F.TempDirCase):
    write, args, call, refusal, assertRefused, assertPasses = (
        F.SP11Gate.write, F.SP11Gate.args, F.SP11Gate.call, F.SP11Gate.refusal, F.SP11Gate.assertRefused,
        F.SP11Gate.assertPasses)

    def setUp(self):
        super().setUp()
        shutil.copyfile(F.EXAMPLE, self.dir / 'pins.example.json')
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop('SNAP_BATTERY_MINIMUM', None)
        self.thermal = self.install_module('hamoa_thermal', F.thermal_module())
        self.sources = dict(SOURCES)
        self.clients = []


class ThermalGate(Gate):
    def test_only_none_and_true_admit(self):
        for result in (0, 1, 1.0, '', 'ok', 'False', [], [0], {}, 2.5, object()):
            with self.subTest(result=result):
                self.install_module('hamoa_thermal', F.thermal_module(admit=lambda info, result=result: result))
                error = self.assertRefused('launch:boot', 'thermal-refused')
                self.assertIn('not None or True', str(error))

    def test_false_and_the_two_admitting_returns_keep_their_meaning(self):
        self.install_module('hamoa_thermal', F.thermal_module(admit=lambda info: False))
        self.assertRefused('launch:boot', 'thermal-refused')
        for result in (None, True):
            self.install_module('hamoa_thermal', F.thermal_module(admit=lambda info, result=result: result))
            self.assertPasses('launch:boot')

    def test_a_module_without_the_cross_check_refuses_in_every_non_passive_mode(self):
        self.install_module('hamoa_thermal', F.thermal_module(ok=F.NO_CROSS_CHECK))
        for mode in NON_PASSIVE:
            with self.subTest(mode=mode):
                error = self.assertRefused(mode, 'thermal-missing')
                self.assertIn(SP11['thermal']['attestation_check'], str(error))

    def test_a_cross_check_that_is_not_callable_is_an_attestation_refusal_not_a_pass(self):
        module = F.thermal_module(ok=F.NO_CROSS_CHECK)
        module.power_attestation_ok = True
        self.install_module('hamoa_thermal', module)
        self.assertRefused('launch:boot', 'attestation-rejected')

    def test_the_real_thermal_module_has_what_the_policy_row_names(self):
        spec = importlib.util.spec_from_file_location('hamoa_thermal_real', TOOLS / 'hamoa_thermal.py')
        real = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(real)
        self.assertTrue(callable(getattr(real, SP11['thermal']['attestation_check'], None)))
        self.assertTrue(callable(getattr(real, SP11['thermal']['admit'], None)))


class SourceDigest(unittest.TestCase):
    def workspace(self, root):
        return types.SimpleNamespace(root=root, tool_sources=lambda: {str(root / 'tools' / 'cycle.py'): '1' * 64})

    def setUp(self):
        holder = tempfile.TemporaryDirectory(dir=F.BUILD_DIR, prefix='digest-')
        self.addCleanup(holder.cleanup)
        self.root = Path(holder.name).resolve()
        (self.root / 'tools').mkdir()
        self.thermal = self.root / 'tools' / 'hamoa_thermal.py'
        self.thermal.write_text('ADMIT = 1\n')

    def test_the_digest_follows_the_thermal_module(self):
        ws = self.workspace(self.root)
        first = targets.source_digest(ws)
        self.thermal.write_text('ADMIT = 2\n')
        second = targets.source_digest(ws)
        self.thermal.unlink()
        missing = targets.source_digest(ws)
        self.assertEqual(len({first, second, missing}), 3)

    def test_it_matches_an_independent_computation_over_the_module_bytes(self):
        sha = hashlib.sha256(self.thermal.read_bytes()).hexdigest()
        lines = sorted([f'tools/cycle.py {"1" * 64}', f'tools/hamoa_thermal.py {sha}'])
        expected = hashlib.sha256(('\n'.join(lines) + '\n').encode()).hexdigest()
        self.assertEqual(targets.source_digest(self.workspace(self.root)), expected)

    def test_a_missing_thermal_module_digests_as_missing(self):
        self.thermal.unlink()
        lines = sorted([f'tools/cycle.py {"1" * 64}', 'tools/hamoa_thermal.py missing'])
        expected = hashlib.sha256(('\n'.join(lines) + '\n').encode()).hexdigest()
        self.assertEqual(targets.source_digest(self.workspace(self.root)), expected)

    def test_the_digest_names_no_machine_path(self):
        text = json.dumps(targets.source_digest(self.workspace(self.root)))
        self.assertNotIn(str(self.root), text)


class AttestationBoundToThermalModule(Gate):
    """An attestation recorded against one thermal module does not survive a change to that module."""

    def setUp(self):
        super().setUp()
        self.root = self.dir / 'tree'
        (self.root / 'tools').mkdir(parents=True)
        self.thermal_file = self.root / 'tools' / 'hamoa_thermal.py'
        self.thermal_file.write_text('ADMIT = 1\n')
        self.workspace = types.SimpleNamespace(root=self.root, processes=lambda: {},
                                               tool_sources=lambda: {str(self.root / 'tools' / 'cycle.py'): '1' * 64})

    def enforce(self, digest):
        section = F.good_section('c1')
        section['power_attestation']['source_sha256'] = digest
        section['run_authorization']['source_sha256'] = digest
        (self.dir / 'pins.json').write_text(json.dumps({'sp11': section}))
        args = self.args('launch:boot')
        with self.assertRaises(targets.TargetRefused) as raised:
            targets.enforce(args, self.workspace, pins_dir=self.dir, clock=lambda: NOW)
        return raised.exception.code

    def test_changing_the_thermal_module_voids_the_attestation_and_the_run_authorization(self):
        digest = targets.source_digest(self.workspace)
        self.assertEqual(self.enforce(digest), 'unimplemented-flow')
        self.thermal_file.write_text('ADMIT = 2\n')
        self.assertEqual(self.enforce(digest), 'attestation-source')
        self.assertEqual(self.enforce(targets.source_digest(self.workspace)), 'unimplemented-flow')


class RunAuthorization(Gate):
    def test_modes_that_can_write_nvme_need_it(self):
        self.assertEqual(sorted(WRITE_MODES), ['installed-fresh:boot', 'launch:boot', 'launch:boot-child'])
        for mode in WRITE_MODES:
            with self.subTest(mode=mode):
                self.assertRefused(mode, 'no-run-authorization', drop('run_authorization'))
                self.assertRefused(mode, 'no-run-authorization', put(('run_authorization',), dict.fromkeys(
                    PER_RUN + ('recorded_utc', 'run_prefix', 'source_sha256'))))

    def test_standing_booleans_alone_never_enable_a_write(self):
        self.write('launch:boot', drop('run_authorization'))
        section = json.loads((self.dir / 'pins.json').read_text())['sp11']
        self.assertTrue(all(section['authorizations'][key] for key in PER_RUN))
        with self.assertRaises(targets.TargetRefused) as raised:
            self.call('launch:boot')
        self.assertEqual(raised.exception.code, 'no-run-authorization')

    def test_the_refusal_says_what_to_record(self):
        error = self.assertRefused('launch:boot', 'no-run-authorization', drop('run_authorization'))
        for text in ('sp11.run_authorization', 'enable_nvme_window_writes', 'run_prefix "c1"',
                     f'source_sha256 "{DIGEST}"', '30 minutes', 'per run'):
            self.assertIn(text, str(error))

    def test_modes_that_cannot_write_nvme_do_not_need_it(self):
        for mode in OTHER_MODES:
            with self.subTest(mode=mode):
                self.assertPasses(mode, drop('run_authorization'))

    def test_a_fresh_matching_record_passes_with_the_standing_authorizations(self):
        for mode in WRITE_MODES:
            with self.subTest(mode=mode):
                self.assertPasses(mode)

    def test_the_standing_authorizations_are_still_required_as_well(self):
        for key in PER_RUN:
            with self.subTest(key=key):
                self.assertRefused('launch:boot', 'missing-authorization', authorize(**{key: False}))

    def test_a_per_run_key_that_is_not_true_refuses(self):
        for key in PER_RUN:
            with self.subTest(key=key):
                error = self.assertRefused('launch:boot', 'missing-authorization', run_auth(**{key: False}))
                self.assertIn(key, str(error))

    def test_the_record_is_bound_to_this_run_prefix(self):
        error = self.assertRefused('launch:boot', 'run-authorization-prefix', run_auth(run_prefix='c2'))
        self.assertIn('"c2"', str(error))
        self.assertIn('"c1"', str(error))

    def test_the_record_is_bound_to_the_source_digest(self):
        error = self.assertRefused('launch:boot', 'run-authorization-source', run_auth(source_sha256='0' * 64))
        self.assertIn(DIGEST, str(error))

    def test_the_record_expires_with_the_power_attestation_limit(self):
        self.assertPasses('launch:boot', run_auth(recorded_utc=stamp(-LIMIT)))
        self.assertRefused('launch:boot', 'run-authorization-expired', run_auth(recorded_utc=stamp(-LIMIT - 1)))
        self.assertRefused('launch:boot', 'run-authorization-expired', run_auth(recorded_utc=stamp(-86400)))

    def test_a_record_from_the_future_is_invalid(self):
        self.assertPasses('launch:boot', run_auth(recorded_utc=stamp(SP11['power']['future_skew_seconds'])))
        self.assertRefused('launch:boot', 'run-authorization-invalid',
                           run_auth(recorded_utc=stamp(SP11['power']['future_skew_seconds'] + 5)))

    def test_malformed_records_are_invalid(self):
        bad = {
            'not an object': put(('run_authorization',), ['x']),
            'an extra key': run_auth(extra=True),
            'a missing key': drop('run_authorization', 'recorded_utc'),
            'a per-run key that is not a boolean': run_auth(enable_nvme_window_writes='true'),
            'a per-run key that is a number': run_auth(cycle_mode_may_write_nvme=1),
            'a bad timestamp': run_auth(recorded_utc='yesterday'),
            'a timestamp that is not text': run_auth(recorded_utc=5),
            'an empty prefix': run_auth(run_prefix=''),
            'a prefix that is not text': run_auth(run_prefix=7),
            'a short digest': run_auth(source_sha256='a' * 63),
            'an upper-case digest': run_auth(source_sha256='A' * 64),
        }
        for name, change in bad.items():
            with self.subTest(name):
                self.assertRefused('launch:boot', 'run-authorization-invalid', change)

    def test_the_check_runs_after_the_attestation_and_before_the_thermal_module(self):
        self.assertRefused('launch:boot', 'attestation-expired',
                           lambda s: (attest(recorded_utc=stamp(-5000))(s), drop('run_authorization')(s)))
        self.assertRefused('launch:boot', 'run-authorization-expired', run_auth(recorded_utc=stamp(-5000)))
        self.assertEqual(self.thermal.calls, [])

    def test_the_command_line_cannot_supply_it(self):
        self.write('launch:boot', drop('run_authorization'))
        args = self.args('launch:boot')
        args.run_authorization = F.good_section('c1')['run_authorization']
        with self.assertRaises(targets.TargetRefused) as raised:
            self.call('launch:boot', args=args)
        self.assertEqual(raised.exception.code, 'no-run-authorization')

    def test_the_example_pins_carry_an_empty_template_with_the_policy_keys(self):
        example = json.loads(F.EXAMPLE.read_text())['sp11']
        self.assertEqual(tuple(example['run_authorization']), PER_RUN + ('recorded_utc', 'run_prefix', 'source_sha256'))
        self.assertEqual(set(example['run_authorization'].values()), {None})
        self.assertEqual({k: example['authorizations'][k] for k in PER_RUN}, dict.fromkeys(PER_RUN, False))


class CpMemcpyClaim(unittest.TestCase):
    COHERENT_H = ROOT / 'compat' / 'adreno-coherent.h'

    def test_the_cap_equals_the_constant_the_monitor_uses_where_the_size_is_chosen(self):
        text = self.COHERENT_H.read_text()
        match = re.search(r'#define\s+ADRENO_COHERENT_COPY_MAX\s+\(1UL\s*<<\s*(\d+)\)', text)
        self.assertIsNotNone(match, 'ADRENO_COHERENT_COPY_MAX not found in compat/adreno-coherent.h')
        self.assertEqual(1 << int(match.group(1)), 1 << 20)
        for name, row in targets.TARGETS.items():
            with self.subTest(target=name):
                self.assertEqual(row['max_cp_memcpy_bytes'], 1 << int(match.group(1)))

    def test_no_cycle_flow_calls_the_predicate_because_none_chooses_a_size(self):
        sources = [TOOLS / 'cycle.py', *sorted((TOOLS / 'cycle_lib').glob('*.py'))]
        callers = [p.name for p in sources if p.name != 'targets.py' and 'check_cp_memcpy' in p.read_text()]
        self.assertEqual(callers, [], 'a cycle flow now calls check_cp_memcpy: update the claim in targets.py')

    def test_the_documentation_does_not_say_the_cycle_flows_refuse_it(self):
        self.assertNotIn('is refused by `check_cp_memcpy`', targets.__doc__)
        self.assertIn('No cycle.py flow calls it', targets.__doc__)
        self.assertIn('ADRENO_COHERENT_COPY_MAX', targets.__doc__)
        self.assertIn('nothing calls this today', targets.check_cp_memcpy.__doc__)

    def test_the_predicate_itself_still_refuses_what_it_claims_to(self):
        for name in targets.TARGETS:
            targets.check_cp_memcpy(name, 1 << 20)
            for size in ((1 << 20) + 1, 4 << 20, -1, True, 1.5, '1'):
                with self.subTest(target=name, size=size):
                    with self.assertRaises(targets.TargetRefused):
                        targets.check_cp_memcpy(name, size)


class Hygiene(unittest.TestCase):
    def test_spdx_and_sizes(self):
        for name in ('cycle_lib/targets.py', 'test-cycle-targets-hardening.py'):
            path = TOOLS / name
            text = path.read_text()
            self.assertIn('SPDX-License-Identifier: MIT', '\n'.join(text.splitlines()[:3]), name)
            self.assertLess(len(text.splitlines()), 1500, name)
            self.assertLess(path.stat().st_size, 50 * 1024, name)

    def test_the_example_pins_are_valid_json_without_duplicate_keys(self):
        json.loads(F.EXAMPLE.read_text(), object_pairs_hook=targets._no_duplicate_keys)


if __name__ == '__main__':
    unittest.main()
