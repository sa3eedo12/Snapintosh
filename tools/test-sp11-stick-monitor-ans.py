#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The builder of the monitor entry run and the ANS window (monitor port work item 4b, storage integration part 2, R3; docs/SP11-STICK-MONITOR.md section 18): the
numbers that check_launch of tools/sp11_stick_bundle_m1.py reads PARAMS with are the monitor's (the ANS device kinds, the window word and the device model slots, from the
headers and, with a toolchain, from the monitor's own translation unit), and the builder refuses, end to end, a guest whose PARAMS lists an ANS model for the kind that calls
the monitor, and for that kind only. The pin that the call is reached only for an ANS window and that the builder refuses every ANS model is in
tools/test-sp11-stick-monitor-devices.py, with the faulty cases of both halves.

    python3 tools/test-sp11-stick-monitor-ans.py

The fixtures of tools/test-sp11-stick-monitor-devices.py (which loads those of tools/test-sp11-stick-monitor-auth.py) are loaded here with a scratch directory of their own
(build/sp11-monitor-ans-test-scratch/, deleted at the end). The compile needs clang, ld.lld, the llvm tools and a q1n1 checkout ($Q1N1_SP11_TREE) and SKIPs without them: a skip
is never a pass. No hardware: NOT RUN."""
import importlib.util
import re
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location('sp11_monitor_devices_tests', TOOLS / 'test-sp11-stick-monitor-devices.py')
DEV = importlib.util.module_from_spec(spec)
spec.loader.exec_module(DEV)
SCRATCH = ROOT / 'build' / 'sp11-monitor-ans-test-scratch'
DEV.SCRATCH = DEV.AUTH.SCRATCH = SCRATCH
AUTH, BT, STATE, m1, ch = DEV.AUTH, DEV.BT, DEV.STATE, DEV.m1, DEV.ch


def setUpModule():
    shutil.rmtree(SCRATCH, ignore_errors=True)
    DEV.setUpModule()


def tearDownModule():
    DEV.tearDownModule()
    shutil.rmtree(SCRATCH, ignore_errors=True)


class Headers(unittest.TestCase):
    def test_the_builder_and_the_monitor_mean_the_same_model_kinds_slots_and_window_word(self):
        device, ans = DEV.source('compat/device.h'), DEV.source('compat/ans.h')
        self.assertEqual({int(value): name for name, value in re.findall(r'#define (DEVICE_ANS_[A-Z0-9_]+) (\d+)\b', device)}, m1.ANS_KINDS)
        self.assertEqual(int(re.search(r'#define DEVICE_MODEL_MAX (\d+)', device)[1]), m1.MODEL_MAX)
        self.assertIn('struct device_model { uint64_t kind, base, span, config[5]; };', device)
        self.assertEqual((m1.MODEL_BYTES, m1.ANS_WINDOW_AT, m1.ANS_NVME_KIND), (8 * 8, 3 * 8 + 3 * 8, 50))
        self.assertIn(f'#define ANS_WINDOW_MODE {m1.ANS_WINDOW_MODE}u', ans)
        self.assertEqual(m1.ANS_KINDS[m1.ANS_NVME_KIND], 'DEVICE_ANS_NVME_V1')


class Builder(BT.Case):
    """The builder of the --m1-run kind refuses a guest whose PARAMS lists an ANS model, end to end (tools/sp11_stick_bundle_build.py calls check_launch for this kind)."""

    def test_a_synthetic_guest_with_an_ans_model_in_params_is_refused_for_the_m1_kind_and_a_record_of_another_kind_is_not(self):
        real = ch.synthetic_built

        def with_models(*models):
            def built(xb, a, b, c, seed, box):
                got = real(xb, a, b, c, seed, box)
                at = ch.layout_of(xb).params_pa
                got['chunks'][at] = DEV.params_chunks(len(models), models, got['chunks'][at])[0]['data']
                return got
            return mock.patch.object(ch, 'synthetic_built', built)
        with with_models((0, 1, 0), (1, 3, 0)):
            self.assertEqual(AUTH.template(AUTH.stage('models-other'))[0], 0, 'models of other kinds are no business of this refusal')
        for what, models in {'the ASC model': [(0, 49, 0)], 'the NVMe model as a window': [(0, 50, 1)], 'the pair a real profile has': DEV.ANS_CASES['both models of the pair'][1]}.items():
            with self.subTest(what), with_models(*models):
                self.refused('PARAMS_ANS', AUTH.template(AUTH.stage('models-ans')))
                self.assertEqual(BT.template(stores='ram,el2_sysreg')[0], 0, 'a record of another kind never calls the monitor: its PARAMS is not launched')


class Models(unittest.TestCase):
    """The numbers of check_launch's model check, against the monitor's own translation unit (the compile of tools/test-sp11-stick-monitor-auth.py, Layout)."""
    compile = AUTH.Layout.compile

    @classmethod
    def setUpClass(cls):
        if not AUTH.tools_ready() or 'record' not in STATE:
            raise unittest.SkipTest('no clang, ld.lld, llvm tools or q1n1 checkout (Q1N1_SP11_TREE): the monitor\'s translation unit is not compiled')

    def test_the_registry_is_where_and_as_wide_as_the_check_says_and_the_ans_kinds_and_the_window_word_are_the_headers(self):
        asserts = '\n'.join([f'_Static_assert(offsetof(struct params, model_count) == {m1.MODEL_COUNT_AT:#x}, "count");',
                             '_Static_assert(sizeof(((struct params *)0)->model_count) == 8, "count width");',
                             f'_Static_assert(offsetof(struct params, models) == {m1.MODELS_AT:#x}, "models");',
                             f'_Static_assert(sizeof(struct device_model) == {m1.MODEL_BYTES} && DEVICE_MODEL_MAX == {m1.MODEL_MAX}, "slots");',
                             f'_Static_assert(offsetof(struct device_model, kind) == 0 && offsetof(struct device_model, config[3]) == {m1.ANS_WINDOW_AT}, "words");',
                             f'_Static_assert(ANS_WINDOW_MODE == {m1.ANS_WINDOW_MODE}, "window mode");',
                             *[f'_Static_assert({name} == {kind}, "{name}");' for kind, name in m1.ANS_KINDS.items()]])
        done = self.compile(asserts, 'models')
        self.assertEqual(done.returncode, 0, done.stderr[-2500:])

    def test_the_drift_check_can_fail(self):
        wrong = ('offsetof(struct params, models) == 0x2E0', 'offsetof(struct device_model, config[3]) == 0x28', 'DEVICE_ANS_NVME_V1 == 51')
        done = self.compile('\n'.join(f'_Static_assert({text}, "wrong{n}");' for n, text in enumerate(wrong)), 'wrong')
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual([f'wrong{n}' in done.stderr for n in range(3)], [True] * 3, 'each of the three asserts fails by itself')


class Hygiene(unittest.TestCase):
    def test_this_file_is_ascii_with_an_spdx_line_within_the_size_limits_and_holds_no_machine_data(self):
        data = Path(__file__).read_bytes()
        self.assertFalse([b for b in data if b > 127], 'ASCII only')
        self.assertIn(b'SPDX-License-Identifier: MIT', data[:400])
        self.assertLess(len(data), 51200)
        self.assertLess(data.count(b'\n'), 1500)
        for needle in BT.MACHINE_PATHS:
            self.assertNotIn(needle.encode(), data)


if __name__ == '__main__':
    unittest.main()
