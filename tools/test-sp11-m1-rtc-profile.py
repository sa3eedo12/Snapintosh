#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The quiet-rtc profile of the SP11 M1 guest menu (probes/guestprofile_m1.py RTC_MENU, tools/sp11_stick_bundle_profile.py, docs/SP11-M1-GUEST-PROFILES.md section 7): EXPERIMENTAL,
host only, hardware NOT RUN.

The profile is the quiet board with the PMU's real-time clock bound, as the A16 chain binds it in -bootfb-rtc. Checked here: it is the quiet profile and the boot spec of one node
(the A16 chain's own SPMIBootSpec(rtc=True) of nub-spmi0) and nothing else; a clock bound anywhere else, at two nodes or beside a split set is refused by name before the loader is
called (GUEST_PROFILE_SPLIT); no form of any record admits it (no record text exists: GUEST_PROFILE_REFUSED); and, with the kernelcache, its guest is the quiet guest with the same
device tree and the same HANDOFF page and three words (kind, seed, contract) of one of the 120 device models in the PARAMS chunk, and the read-back refuses each seeded fault of the
clock (unseeded, another seed, another node, a clock where none is bound, no clock where one is, a PARAMS chunk that is not the models' serialisation).

    python3 tools/test-sp11-m1-rtc-profile.py

`Guest` needs downloads/extracted/j700ap/ (git-ignored Apple material, read and never copied) and SKIPs without it: a skip is never a pass. The monitor's own model is
tools/test-pmu-rtc.c's (a host C test). Nothing is written to a stick, no hardware: NOT RUN."""
import dataclasses
import datetime
import importlib.util
import struct
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(ROOT / 'probes'))
sys.path.insert(0, str(TOOLS))
import devicemodel as dm  # noqa: E402
import guestprofile as gp  # noqa: E402
import guestprofile_m1 as menu  # noqa: E402
import sp11_stick_bundle as sb  # noqa: E402
import sp11_stick_bundle_chunks as ch  # noqa: E402
import sp11_stick_bundle_m1 as m1  # noqa: E402
import sp11_stick_bundle_profile as gpb  # noqa: E402

spec = importlib.util.spec_from_file_location('sp11_m1_guest_profile_tests', TOOLS / 'test-sp11-m1-guest-profile.py')
GP = importlib.util.module_from_spec(spec)
spec.loader.exec_module(GP)

QUIET, RTC = GP.P + 'quiet', GP.P + 'quiet-rtc'
CLOCK_BASE = 0x308700000
COUNT_AT, MODELS_AT, MODEL_BYTES = 0x2D0, 0x2D8, 64


def setUpModule():
    GP.setUpModule()


def tearDownModule():
    GP.tearDownModule()


def models_of(params):
    """The device models of a PARAMS chunk as lists of eight words: kind, base, span and config[0..4]."""
    count, = struct.unpack_from('<Q', params, COUNT_AT)
    return [list(struct.unpack_from('<8Q', params, MODELS_AT + MODEL_BYTES * i)) for i in range(count)]


def with_clock_at(profile, node):
    return dataclasses.replace(profile, spmi_boot=tuple(dataclasses.replace(s, rtc=s.node == node) for s in profile.spmi_boot))


# ---- The profile as data and the refusals before any guest is built: no Apple input -------------------------------------------------------------
class Menu(unittest.TestCase):
    def test_the_clock_profile_is_the_quiet_board_and_the_a16_chains_own_boot_spec_of_one_node_and_nothing_else(self):
        profile, quiet = gp.get_profile(RTC), gp.get_profile(QUIET)
        self.assertEqual(menu.RTC_MENU, GP.RTC_SETS)
        self.assertFalse(set(menu.RTC_MENU) & set(menu.MENU), 'the eight profiles of the text module\'s menu are the same eight')
        self.assertEqual({f.name for f in dataclasses.fields(profile) if getattr(profile, f.name) != getattr(quiet, f.name)}, {'name', 'spmi_boot'})
        self.assertEqual([(s.node, s.rtc) for s in profile.spmi_boot],
                         [(menu.RTC_NODE, True)] + [(s.node, False) for s in quiet.spmi_boot if s.node != menu.RTC_NODE], 'nub-spmi0 is first in the base board\'s order')
        self.assertEqual(profile.spmi_boot, gp.J700_26A428_BOOTFB_RTC.spmi_boot, 'the boot specs of the A16 chain\'s -bootfb-rtc, which binds the same node')
        self.assertEqual(menu.RTC_PROFILES, {RTC: profile})
        self.assertEqual(menu.compose(RTC, GP.RTC_SETS[RTC], rtc=True), profile)
        self.assertEqual(dataclasses.replace(menu.bind_rtc(quiet), name=RTC), profile)
        self.assertEqual((menu.sets_of(profile), menu.rtc_of(profile), menu.rtc_of(quiet), menu.rtc_of(gp.J700_26A428)), (GP.RTC_SETS[RTC], True, False, False))
        self.assertEqual(menu.sets_of(quiet), GP.RTC_SETS[RTC], 'the quiet profile is the same two sets without the clock')
        self.assertIs(gp.get_profile(RTC), profile, 'registered beside the eight')

    def test_a_clock_bound_at_another_node_at_two_nodes_or_beside_a_split_set_is_refused_before_the_loader_is_called(self):
        quiet = gp.get_profile(QUIET)
        other = [s.node for s in quiet.spmi_boot if s.node != menu.RTC_NODE]
        wrong = {'at aop-spmi0 and not at nub-spmi0': with_clock_at(quiet, GP.A + 'aop-spmi0'),
                 'at nub-spmi1': with_clock_at(quiet, other[0]),
                 'at two nodes': dataclasses.replace(quiet, spmi_boot=tuple(dataclasses.replace(s, rtc=s.node in (menu.RTC_NODE, other[0])) for s in quiet.spmi_boot)),
                 'at every node': dataclasses.replace(quiet, spmi_boot=tuple(dataclasses.replace(s, rtc=True) for s in quiet.spmi_boot))}
        self.assertEqual(len(other), 3)
        for case, bad in wrong.items():
            with self.subTest(case), mock.patch.dict(gp.PROFILES, {RTC: dataclasses.replace(bad, name=RTC)}):
                with self.assertRaises(menu.MenuError) as caught:
                    menu.sets_of(gp.get_profile(RTC))
                self.assertEqual(caught.exception.kind, 'shape')
                GP.refused(self, 'GUEST_PROFILE_SPLIT', gpb.resolve, GP.ns(guest_profile=RTC))
        smc = gp.get_profile(GP.P + 'quiet-smc')
        split = dataclasses.replace(menu.bind_rtc(smc), name=RTC, deferred_functions=smc.deferred_functions[:-1])
        with mock.patch.dict(gp.PROFILES, {RTC: split}):
            GP.refused(self, 'GUEST_PROFILE_SPLIT', gpb.resolve, GP.ns(guest_profile=RTC))
        with mock.patch.dict(gp.PROFILES, {RTC: dataclasses.replace(quiet, name=RTC)}):
            self.assertEqual(menu.sets_of(gp.get_profile(RTC)), GP.RTC_SETS[RTC], 'the same name without the clock is the quiet board: the menu does not know which was meant')
            self.assertFalse(menu.rtc_of(gp.get_profile(RTC)))
        with self.assertRaisesRegex(RuntimeError, 'not exactly one SPMI boot spec'):
            menu.bind_rtc(dataclasses.replace(quiet, spmi_boot=quiet.spmi_boot[1:]))
        with self.assertRaisesRegex(RuntimeError, 'not exactly one SPMI boot spec'):
            menu.bind_rtc(dataclasses.replace(quiet, spmi_boot=quiet.spmi_boot + quiet.spmi_boot[:1]))

    def test_no_form_of_any_record_admits_the_clock_profile_because_no_record_text_exists_for_it(self):
        form = ('console', ch.RUNG7_FORM[1], True, 'ack')
        self.assertNotIn(RTC, gpb.RUNG7_PROFILES)
        self.assertEqual(gpb.mark_of(RTC), 'R-')
        GP.refused(self, 'GUEST_PROFILE_REFUSED', gpb.check_form, RTC, form[1], True, 'ack', 'console', True, 120)
        GP.refused(self, 'GUEST_PROFILE_REFUSED', m1.pinned_label, 'RUNG7', True, *form, profile=RTC)
        self.assertNotIn(RTC, ch.FORM_PROFILE.values())
        self.assertFalse(ch.let_panic_form_ok('console', ch.RUNG7_FORM[1], 120, 'ack', RTC), 'the loader\'s form check refuses the name, so rung 7\'s box and word without the quiet profile are refused too')
        self.assertEqual(gpb.QUIET, QUIET, 'rung 7\'s record and its profile are not changed by the new name')

    def test_the_seed_is_one_fixed_instant_that_the_monitor_accepts_and_a_bundle_is_not_made_from_the_hosts_clock(self):
        self.assertEqual(menu.RTC_EPOCH_US, int(datetime.datetime(2026, 11, 20, tzinfo=datetime.timezone.utc).timestamp()) * 1_000_000)
        self.assertTrue(0 < menu.RTC_EPOCH_US <= dm.PMU_RTC_LIMIT_US)
        source = (ROOT / 'tools/sp11_stick_bundle_chunks.py').read_text() + (ROOT / 'probes/guestprofile_m1.py').read_text()
        self.assertNotIn('time_ns', source)
        self.assertNotIn('time.time', source)
        models = (dm.DeviceModel('/n', dm.SPMI_PMU_RTC_V1, CLOCK_BASE, 0x18000, (0x14000, 0x4000, 487, 0, dm.PMU_RTC_CONTRACT)),)
        self.assertEqual(menu.seed(models)[0].config[3], menu.RTC_EPOCH_US)
        self.assertEqual(menu.seeded(models)[0].config[3], menu.RTC_EPOCH_US)
        self.assertEqual(models[0].config[3], 0, 'the unseeded descriptor is not rewritten')
        empty = (dataclasses.replace(models[0], kind=dm.SPMI_GEN3_EMPTY_V1, config=(0x14000, 0x4000, 487, 256, 256)),)
        for bad in ((), empty):
            with self.assertRaises(ValueError):
                menu.seed(bad)
        self.assertIs(menu.seeded(empty), empty, 'a guest without a clock model keeps its models, the same object')
        with self.assertRaises(ValueError):
            menu.seed(menu.seed(models))


# ---- The guest of the real loader: boot set -----------------------------------------------------------------------------------------------------
@GP.needs_boot_set
class Guest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.quiet, cls.clock = GP.Guest.built(QUIET), GP.Guest.built(RTC)

    def test_the_clock_guest_is_the_quiet_guest_with_three_words_of_one_device_model(self):
        quiet, clock = self.quiet, self.clock
        self.assertEqual(clock['profile'], RTC)
        self.assertEqual([(d, k) for d, _, k in quiet['table']], [(d, k) for d, _, k in clock['table']], 'the same chunks at the same addresses')
        differing = [i for i, (x, y) in enumerate(zip(quiet['digests'], clock['digests'])) if x != y]
        self.assertEqual([quiet['table'][i][2] for i in differing], [sb.KIND_PARAMS], 'one chunk: PARAMS')
        self.assertEqual((quiet['tree'], quiet['handoff']), (clock['tree'], clock['handoff']), 'the device tree and the HANDOFF page are the quiet profile\'s, byte for byte')
        self.assertEqual(len(quiet['params']), len(clock['params']))
        words = [i for i in range(0, len(quiet['params']), 8) if quiet['params'][i:i + 8] != clock['params'][i:i + 8]]
        before, after = models_of(quiet['params']), models_of(clock['params'])
        self.assertEqual((len(before), len(after)), (120, 120))
        slots = [i for i, (x, y) in enumerate(zip(before, after)) if x != y]
        self.assertEqual(len(slots), 1)
        slot = slots[0]
        self.assertEqual(words, [MODELS_AT + MODEL_BYTES * slot + 8 * w for w in (0, 6, 7)], 'kind, config[3] and config[4] of that model and no other word of PARAMS')
        old, new = before[slot], after[slot]
        self.assertEqual((old[0], old[3:6], old[6:8]), (dm.SPMI_GEN3_EMPTY_V1, old[3:6], [256, 256]), 'an empty gen-3 bus before')
        self.assertEqual((new[0], new[1:6], new[6], new[7]), (dm.SPMI_PMU_RTC_V1, old[1:6], menu.RTC_EPOCH_US, dm.PMU_RTC_CONTRACT), 'the clock after: the same window, the seed and the contract')
        self.assertEqual((old[1], old[2], old[3:6]), (CLOCK_BASE, 0x18000, [0x14000, 0x4000, 487]), 'nub-spmi0: the node that holds the PMU')
        self.assertEqual(sum(m[0] == dm.SPMI_PMU_RTC_V1 for m in after) + sum(m[0] == dm.SPMI_PMU_RTC_V1 for m in before), 1)
        self.assertEqual([m for i, m in enumerate(after) if i != slot], [m for i, m in enumerate(before) if i != slot], 'the other 119 models')

    def test_the_read_back_refuses_each_seeded_fault_of_the_clock_and_of_the_models_beside_it(self):
        clock, quiet = self.clock, self.quiet
        loader, built = clock['loader'], clock['built']
        self.assertEqual(gpb.read_back(loader, built, RTC), len(GP.DeviceTree(clock['tree']).nodes))
        models = tuple(built['device_models'])
        index, = [i for i, m in enumerate(models) if m.kind == dm.SPMI_PMU_RTC_V1]

        def seeded(detail, name=RTC, source=built, **changes):
            error = GP.refused(self, 'GUEST_PROFILE_READBACK', gpb.read_back, loader, {**source, **changes}, name)
            self.assertIn(detail, error.detail)

        def replaced(**fields):
            return tuple(dataclasses.replace(m, **fields) if i == index else m for i, m in enumerate(models))
        unseeded = replaced(config=models[index].config[:3] + (0, dm.PMU_RTC_CONTRACT))
        seeded('with the clock seeded at RTC_EPOCH_US', device_models=unseeded)
        seeded('with the clock seeded at RTC_EPOCH_US', device_models=replaced(config=models[index].config[:3] + (menu.RTC_EPOCH_US + 1, dm.PMU_RTC_CONTRACT)))
        seeded('the loader\'s own', device_models=replaced(base=models[index].base + 0x100000))
        seeded('not bound', device_models=models[:index] + models[index + 1:])
        seeded('the loader\'s own', device_models=models + (models[0],))
        seeded('not bound', name=RTC, source=quiet['built'])
        seeded('bound in the device models', name=QUIET, source=built)
        chunk = bytearray(built['chunks'][loader.PARAMS_PA])
        chunk[MODELS_AT + MODEL_BYTES * index + 8 * 6] ^= 1
        seeded('not the serialisation of the device models', chunks={**built['chunks'], loader.PARAMS_PA: bytes(chunk)})
        seeded('not the serialisation of the device models', chunks={**built['chunks'], loader.PARAMS_PA: bytes(quiet['built']['chunks'][loader.PARAMS_PA])})
        self.assertEqual(gpb.read_back(loader, quiet['built'], QUIET), len(GP.DeviceTree(quiet['tree']).nodes), 'the quiet profile reads back as before')

    def test_an_unseedable_clock_stops_the_build_before_a_params_chunk_is_made(self):
        with mock.patch.object(menu, 'RTC_EPOCH_US', 0):
            a = GP.ns(base=hex(GP.BANK), guest_mib=384, reserve_mib=None, kernelcache=self.clock['loader'].DEFAULT_KERNELCACHE, panel='console', sptm_hvc_calls=0, time_box_s=5,
                      seed='sp11-stick-bundle', boot_arg='cpus=1', guest_profile=RTC)
            error = GP.refused(self, 'BUILT_SHAPE', GP.bb.real_built, a, self.clock['loader'], GP.bb.placement_of(a), GP.bb.load_facts(GP.EXAMPLE_FACTS))
        self.assertIn('cannot be seeded', error.detail)


if __name__ == '__main__':
    unittest.main()
