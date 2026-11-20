#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The SP11 M1 guest menu (probes/guestprofile_m1.py, tools/sp11_stick_bundle_profile.py, docs/SP11-M1-GUEST-PROFILES.md): EXPERIMENTAL virtual board descriptions.

Ten names are admitted for --guest-profile: the base board j700ap-26A428 (the default, whose builds do not change), eight unions of whole root sets and quiet-rtc (the quiet board with the
PMU's real-time clock bound: tools/test-sp11-m1-rtc-profile.py has its guest). Checked here: each root
set as one tuple of the A16 chain's own pins; the profiles as the base board and whole sets; the closed list and every refusal by name (GUEST_PROFILE_REFUSED,
GUEST_PROFILE_SPLIT, GUEST_PROFILE_READBACK); the marks of the pinned label and the 23 characters they share; the options through the builder's stand-in loader (a profile is refused by
name in every form but rung 7's, whose own tests are tools/test-sp11-stick-monitor-rung7.py's); and, with the kernelcache, the generated trees (exactly the pinned roots vanish, nothing retained
changes or dangles, every hash binds, each dependency between sets is a refusal of the loader's own guard) and the guest of each profile against the default (the device tree and the one
boot_args word that holds its length differ; PARAMS and the 120 models do not).

    python3 tools/test-sp11-m1-guest-profile.py

`Trees` and `Guest` need downloads/extracted/j700ap/ (git-ignored Apple material, read and never copied) and SKIP without it: a skip is never a pass. The rest needs no Apple input; its
fixtures are those of tools/test-sp11-stick-monitor-panel.py with a scratch directory of their own (build/sp11-m1-guest-profile-test-scratch/, deleted at the end). Nothing is
written to a stick, no hardware: NOT RUN."""
import contextlib
import copy
import dataclasses
import hashlib
import importlib.util
import io
import runpy
import struct
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(ROOT / 'probes'))
sys.path.insert(0, str(TOOLS))
import xnuboot as xb  # noqa: E402
from appledt import DeviceTree  # noqa: E402
from deferred import defer_subtrees  # noqa: E402
import guestprofile as gp  # noqa: E402
import guestprofile_m1 as menu  # noqa: E402
import sp11_stick_bundle as sb  # noqa: E402
import sp11_stick_bundle_build as bb  # noqa: E402
import sp11_stick_bundle_chunks as ch  # noqa: E402
import sp11_stick_bundle_m1 as m1  # noqa: E402
import sp11_stick_bundle_profile as gpb  # noqa: E402
import sp11_stick_bundle_windows as win  # noqa: E402

spec = importlib.util.spec_from_file_location('sp11_monitor_panel_tests', TOOLS / 'test-sp11-stick-monitor-panel.py')
PT = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PT)
SCRATCH = ROOT / 'build' / 'sp11-m1-guest-profile-test-scratch'
PT.SCRATCH = PT.BLD.SCRATCH = PT.AUTH.SCRATCH = SCRATCH
BT, BLD = PT.BT, PT.BLD

BASE = 'j700ap-26A428'
P = BASE + '-m1-'
A = '/device-tree/arm-io/'
MIB, BANK = 1 << 20, 0x880000000
EXAMPLE_FACTS = ROOT / 'tools/testdata/sp11-facts.example.json'      # the public, synthetic example: nothing of the unit
PROFILE_SETS = {P + 'quiet': ('accel', 'audio'), P + 'quiet-aop': ('accel', 'audio', 'aop'), P + 'quiet-dcp': ('accel', 'audio', 'dcpext', 'dcp'),
                P + 'quiet-ave': ('accel', 'audio', 'ave'), P + 'quiet-clpc': ('accel', 'audio', 'clpc'), P + 'quiet-usb': ('accel', 'audio', 'usb'),
                P + 'quiet-smc': ('accel', 'audio', 'aop', 'dcpext', 'dcp', 'smcbtm'), P + 'all-quiet': ('accel', 'audio', 'aop', 'dcpext', 'dcp', 'ave', 'smcbtm')}
MARKS = {BASE: '', P + 'quiet': 'Q-', P + 'quiet-aop': 'A-', P + 'quiet-dcp': 'D-', P + 'quiet-ave': 'V-', P + 'quiet-clpc': 'C-', P + 'quiet-usb': 'U-', P + 'quiet-smc': 'S-',
         P + 'all-quiet': 'X-', P + 'quiet-rtc': 'R-', P + 'quiet-rtc-acm': 'M-'}
# The profiles that bind the PMU's real-time clock and the sets they withdraw (tools/test-sp11-m1-rtc-profile.py has their guests).
RTC_SETS = {P + 'quiet-rtc': ('accel', 'audio')}
# The profile of the root-mount form (K17): the clock profile's sets and the credential manager's software mode (tools/test-sp11-stick-monitor-rung9.py has its form).
ROOT_SETS = {P + 'quiet-rtc-acm': ('accel', 'audio')}
# The nodes of the published tree: the base board's 395 less the nodes under the roots that a profile adds (each count is a host run of the loader on the J700 26A428 kernelcache).
AFTER = {BASE: 395, P + 'quiet': 352, P + 'quiet-aop': 337, P + 'quiet-dcp': 341, P + 'quiet-ave': 350, P + 'quiet-clpc': 351, P + 'quiet-usb': 345, P + 'quiet-smc': 313,
         P + 'all-quiet': 311}
PPM_BTM = tuple((A + 'pmgr/ppm', n) for n in ('function-btm-config', 'function-btm-start', 'function-btm-stop', 'function-btm-vthr'))
MTP = A + 'dockchannel-mtp/mtp-transport/multi-touch'
SMC_FUNCTIONS = ((A + 'nub-spmi0/pmu-main', 'function-external_standby'), (MTP, 'function-afe-reset'), (MTP, 'function-ipd-pwr-seq-control')) + PPM_BTM
# set: (roots, functions withdrawn with it, base declarations it drops, sets it needs, nodes under its roots)
EXPECTED = {
    'accel': ((A + 'avd', A + 'scaler0', A + 'apr'), (), (), (), 3),
    'audio': (tuple(A + n for n in ('iop-audio-controller', 'iop-voicetrigger-controller', 'admac-base-ns', 'admac-leap-ns', 'admac-leap-s', 'admac-leap-s-rx-0-proxy',
                                    'audio-shared-dart-proxy', 'audio-hp-mic-proxy', 'i2c0/audio-codec-output')), (), (), (), 40),
    'aop': ((A + 'aop', A + 'aop2', A + 'aop-spmi0'), (), (), (), 15),
    'dcpext': ((A + 'dispext0', A + 'dcpext', A + 'dcpext-expert'), (), (), (), 4),
    'dcp': ((A + 'dcp', A + 'dcp0-expert', A + 'dp-audio0', A + 'dp-audio1', A + 'disp0', '/device-tree/backlight'), (), (), ('dcpext',), 7),
    'ave': ((A + 'ave', A + 'ave1'), (), (), (), 2),
    'smcbtm': ((A + 'smc', A + 'smctempsensor0', '/device-tree/buttons', A + ':j700_has_mesa_0', A + 'nub-spmi0/btm'), SMC_FUNCTIONS, (), ('aop', 'dcp', 'audio'), 13),
    'clpc': ((A + 'pmgr/clpc',), (), ((A + 'pmgr/clpc', 'function-ane_perf_ctr'),), (), 1),
    'usb': ((A + 'usb-drd', A + 'atc0-dpphy', A + 'i2c2/usb-hub-i2c'), (), (), (), 7),
}
# The profiles of the A16 chain in which one profile or another pins these roots: every root of the menu is one of their tuples, with their digest.
CHAIN = (gp.J700_26A428_NO_CLPC, gp.J700_26A428_BOOTFB_NO_USB, gp.J700_26A428_BOOTFB_XARS_UNIFORM_NO_AUDIO, gp.J700_26A428_XARS_UNIFORM_AUXKC_INSTALLED_ADRENO_METAL_DISPLAY,
         gp.J700_26A428_XARS_UNIFORM_AUXKC_INSTALLED_ADRENO_METAL_DISPLAY_SKS_QUIET)
UNIVERSE = tuple(r for _, (roots, *_) in EXPECTED.items() for r in roots)
RETAINED_ALWAYS = (A + 'smc-gpio', A + 'aop-gpio', A + 'sep', A + 'nub-spmi0', A + 'nub-spmi0/pmu-main', A + 'pmgr', A + 'pmgr/ppm', A + 'dart-disp0', A + 'dart-dcp', A + 'dart-ave', A + 'dart-aop')
OPTION_MARKERS = {('withheld', None): '', ('handed', None): 'PANEL-', ('console', None): 'CONSOLE-', ('console', 'cpus=1'): 'CPUS1-'}
EARLIER_NAMES = (
    'j700ap-26A428', 'j700ap-26A428-no-clpc', 'j700ap-26A428-sep-preload', 'j700ap-26A428-bootfb', 'j700ap-26A428-bootfb-nub', 'j700ap-26A428-bootfb-no-usb',
    'j700ap-26A428-bootfb-rtc', 'j700ap-26A428-bootfb-xars', 'j700ap-26A428-bootfb-xars-uniform', 'j700ap-26A428-bootfb-xars-uniform-no-audio',
    'j700ap-26A428-bootfb-auxkc', 'j700ap-26A428-bootfb-auxkc-gpu', 'j700ap-26A428-xars-uniform-auxkc', 'j700ap-26A428-xars-uniform-auxkc-adreno',
    'j700ap-26A428-xars-uniform-auxkc-adreno-nodart', 'j700ap-26A428-xars-uniform-auxkc-ans', 'j700ap-26A428-xars-uniform-auxkc-installed',
    'j700ap-26A428-xars-uniform-auxkc-installed-adreno', 'j700ap-26A428-xars-uniform-auxkc-installed-adreno-metal',
    'j700ap-26A428-xars-uniform-auxkc-installed-adreno-metal-display', 'j700ap-26A428-xars-uniform-auxkc-installed-adreno-metal-display-sks',
    'j700ap-26A428-xars-uniform-auxkc-installed-adreno-metal-display-sks-quiet', 'j700ap-26A428-xars-uniform-auxkc-installed-adreno-metal-display-sks-quiet-input',
    'j700ap-26A428-xars-uniform-auxkc-installed-adreno-metal-display-sks-quiet-input-touchpad')


def boot_set_present():
    try:
        return not bb.xnuboot().check_profile()
    except Exception:       # noqa: BLE001  a checkout without the Apple material must skip these tests, not break the file
        return False


needs_boot_set = unittest.skipUnless(boot_set_present(), 'no J700 26A428 boot set under downloads/extracted/j700ap (git-ignored Apple material)')


def setUpModule():
    PT.setUpModule()


def tearDownModule():
    PT.tearDownModule()


def ns(**kw):
    return types.SimpleNamespace(**kw)


def placement():
    return bb.placement_of(ns(base=hex(BANK), guest_mib=384, reserve_mib=None))


def tree(name, include_deferred=False):
    """The guest tree as the M1 builder's real_built makes it, up to the loader's own build_devicetree; module state is restored afterwards."""
    with mock.patch.dict(xb.__dict__, xb.__dict__.copy()):
        xb.configure_guest(name)
        xb.configure_txm_arena()
        xb.configure_adreno_va_wide()
        ch.place_layout(xb, placement())
        with contextlib.redirect_stdout(io.StringIO()):
            return xb.build_devicetree(include_deferred=include_deferred)


def props(dt, node):
    return {k: bytes(v.value) for k, v in dt.nodes[node].items()}


def below(dt, roots):
    return {n for n in dt.nodes for root in roots if n == root or n.startswith(root + '/')}


def provider(name):
    return (name.startswith('function-') or name.endswith('-parent') or name.endswith('-phandle') or name in ('msi-parent-controller', 'amfm-managed-port-control', 'usb-port'))


def extra_roots(name):
    """The roots that `name` adds to the base board's, from the data of the menu."""
    return tuple(r for n in menu.ORDER if n in PROFILE_SETS.get(name, ()) for r in EXPECTED[n][0])


def raw(names):
    """(roots, functions) of the base board and the whole sets `names`, without the composition's check of the needs."""
    sets = [menu.ROOT_SETS[n] for n in menu.ORDER if n in names]
    dropped = {d for s in sets for d in s.drops}
    return (menu.BASE.deferred_subtrees + tuple(r for s in sets for r in s.roots),
            tuple(f for f in menu.BASE.deferred_functions if (f.node, f.name) not in dropped) + tuple(f for s in sets for f in s.functions))


def closure(name):
    """`name` and every set it needs, by the data of EXPECTED."""
    out, todo = set(), [name]
    while todo:
        n = todo.pop()
        if n not in out:
            out.add(n)
            todo.extend(EXPECTED[n][3])
    return out


def refused(test, name, fn, *args, **kw):
    with test.assertRaises(ch.Refused) as caught:
        fn(*args, **kw)
    test.assertEqual(caught.exception.name, name, caught.exception.detail)
    return caught.exception


# ---- The sets and the profiles as data: no Apple input ----------------------------------------------------------------------------------------
class Sets(unittest.TestCase):
    def test_each_set_is_one_tuple_of_the_chains_own_pins_with_the_functions_drops_and_needs_that_the_menu_says(self):
        self.assertEqual(list(menu.ROOT_SETS), list(EXPECTED), 'the canonical order')
        pinned = {}
        for profile in CHAIN:
            for s in profile.deferred_subtrees:
                pinned.setdefault(s.node, set()).add(s)
        declared = {(f.node, f.name): f for profile in CHAIN for f in profile.deferred_functions}
        for name, (roots, functions, drops, needs, _) in EXPECTED.items():
            with self.subTest(name):
                s = menu.ROOT_SETS[name]
                self.assertEqual((s.name, [r.node for r in s.roots], [(f.node, f.name) for f in s.functions], s.drops, s.needs), (name, list(roots), list(functions), drops, needs))
                for root in s.roots:
                    self.assertEqual(pinned[root.node], {root}, f'{root.node}: the chain pins it once, with this digest')
                for f in s.functions:
                    self.assertEqual(declared[(f.node, f.name)], f)
        for roots in (menu.ROOT_SETS[n].roots for n in EXPECTED):
            self.assertEqual(len({r.node for r in roots}), len(roots))
        self.assertEqual(len(UNIVERSE), len(set(UNIVERSE)), 'no root is in two sets')
        self.assertEqual(len(UNIVERSE), 35)

    def test_the_profiles_are_the_base_board_and_whole_sets_in_the_canonical_order(self):
        self.assertIs(menu.BASE, gp.J700_26A428)
        self.assertEqual(menu.MENU, PROFILE_SETS)
        base_roots = [s.node for s in gp.J700_26A428.deferred_subtrees]
        self.assertEqual(len(base_roots), 12)
        for name, sets in PROFILE_SETS.items():
            with self.subTest(name):
                profile = gp.get_profile(name)
                differ = {f.name for f in dataclasses.fields(profile) if getattr(profile, f.name) != getattr(gp.J700_26A428, f.name)}
                self.assertEqual(differ, {'name', 'deferred_subtrees'} | ({'deferred_functions'} if name.endswith(('smc', 'all-quiet', 'clpc')) else set()))
                self.assertFalse(profile.acm_software_mode or profile.sep_asc.reply_transport, 'the SEP policy of -no-clpc is not part of any profile')
                self.assertEqual([s.node for s in profile.deferred_subtrees], base_roots + list(extra_roots(name)))
                self.assertEqual(menu.sets_of(profile), sets)
                self.assertEqual(menu.compose(name, reversed(sets)), profile, 'the order given does not matter')
        self.assertEqual(menu.sets_of(gp.J700_26A428), ())

    def test_the_a16_module_does_not_import_the_menu_and_a_fresh_import_of_it_registers_none_of_it(self):
        code = 'import sys; sys.path.insert(0, sys.argv[1]); import guestprofile as g; print(sorted(n for n in g.PROFILES if "-m1-" in n), "guestprofile_m1" in sys.modules)'
        out = subprocess.run([sys.executable, '-c', code, str(ROOT / 'probes')], capture_output=True, text=True, check=True).stdout
        self.assertEqual(out.strip(), '[] False', 'the A16 route copies and hashes probes/guestprofile.py alone: it must not depend on the menu')

    def test_the_registry_holds_the_menu_beside_every_earlier_name_and_the_base_entry_is_untouched(self):
        self.assertTrue(set(EARLIER_NAMES) <= set(gp.PROFILES), 'the registration of an earlier profile was lost or renamed')
        self.assertTrue(set(PROFILE_SETS) | set(RTC_SETS) | set(ROOT_SETS) <= set(gp.PROFILES))
        self.assertEqual(sorted(n for n in gp.PROFILES if n.startswith(P)), sorted({*PROFILE_SETS, *RTC_SETS, *ROOT_SETS}))
        self.assertTrue(all(gp.PROFILES[n].name == n for n in gp.PROFILES))
        self.assertIs(gp.get_profile(BASE), gp.J700_26A428)
        self.assertEqual(gp.J700_26A428.deferred_functions[0].node, A + 'pmgr/clpc', 'the base board declares the one function that the clpc set drops')

    def test_the_smc_and_btm_roots_and_the_four_btm_functions_are_one_unit_that_no_profile_can_split(self):
        unit = menu.ROOT_SETS['smcbtm']
        self.assertEqual([r.node for r in unit.roots][-1], A + 'nub-spmi0/btm')
        self.assertEqual({(f.node, f.name) for f in unit.functions} & set(PPM_BTM), set(PPM_BTM))
        for other in (s for n, s in menu.ROOT_SETS.items() if n != 'smcbtm'):
            self.assertFalse({r.node for r in other.roots} & {r.node for r in unit.roots})
            self.assertFalse({(f.node, f.name) for f in other.functions} & {(f.node, f.name) for f in unit.functions})
        refused_kinds = {}
        profile = gp.get_profile(P + 'quiet-smc')
        smc, btm = [r for r in unit.roots if r.node.endswith('/smc')], [r for r in unit.roots if r.node.endswith('/btm')]
        for case, drop_roots, drop_functions in (('the smc root alone', [r for r in unit.roots if r not in smc], ()), ('the btm root alone', [r for r in unit.roots if r not in btm], ()),
                                                 ('smc without btm', btm, ()), ('btm without the ppm functions', (), PPM_BTM),
                                                 ('without the smc-pmu functions', (), SMC_FUNCTIONS[:3]), ('without buttons', [r for r in unit.roots if r.node == '/device-tree/buttons'], ())):
            split = dataclasses.replace(profile, deferred_subtrees=tuple(s for s in profile.deferred_subtrees if s not in drop_roots),
                                        deferred_functions=tuple(f for f in profile.deferred_functions if (f.node, f.name) not in drop_functions))
            with self.subTest(case), self.assertRaises(menu.MenuError) as caught:
                menu.sets_of(split)
            refused_kinds[case] = caught.exception.kind
        self.assertEqual(set(refused_kinds.values()), {'split'})

    def test_a_set_without_the_sets_it_needs_cannot_be_composed_and_an_unknown_set_is_named(self):
        for name, (_, _, _, needs, _) in EXPECTED.items():
            if needs:
                with self.subTest(name), self.assertRaises(menu.MenuError) as caught:
                    menu.compose('x', (name,))
                self.assertEqual(caught.exception.kind, 'needs')
                self.assertTrue(all(n in str(caught.exception) for n in needs))
            else:
                self.assertEqual(menu.sets_of(menu.compose('x', (name,))), (name,))
        with self.assertRaises(menu.MenuError) as caught:
            menu.compose('x', ('smc',))
        self.assertEqual(caught.exception.kind, 'unknown')
        # a profile that is not the composition of its sets (an extra field, an extra root) is 'shape'
        with self.assertRaises(menu.MenuError) as caught:
            menu.sets_of(dataclasses.replace(gp.get_profile(P + 'quiet'), boot_mpidr=0x123))
        self.assertEqual(caught.exception.kind, 'shape')

    def test_the_credential_layer_is_one_data_flag_on_the_clock_profile_and_a_profile_that_is_more_than_the_layer_is_a_shape_error(self):
        rtc, acm = gp.get_profile(P + 'quiet-rtc'), gp.get_profile(P + 'quiet-rtc-acm')
        self.assertEqual({f.name for f in dataclasses.fields(acm) if getattr(acm, f.name) != getattr(rtc, f.name)}, {'name', 'acm_software_mode'}, 'the layer is that one field and nothing else')
        self.assertEqual((menu.sets_of(acm), menu.acm_of(acm), menu.acm_of(rtc), menu.rtc_of(acm), menu.binds_clock(acm.name), menu.binds_clock(rtc.name)),
                         (('accel', 'audio'), True, False, True, True, True))
        self.assertEqual(menu.compose(acm.name, ('audio', 'accel'), rtc=True, acm=True), acm)
        self.assertFalse(any(menu.acm_of(gp.get_profile(n)) for n in (*PROFILE_SETS, *RTC_SETS, BASE)), 'no other profile of the menu has the layer')
        for what, seeded in (('the SEP reply transport as well', dataclasses.replace(acm, sep_asc=dataclasses.replace(acm.sep_asc, reply_transport=True))),
                             ('another field', dataclasses.replace(acm, boot_mpidr=0x123)),
                             ('a root withdrawn that is no set', dataclasses.replace(acm, deferred_subtrees=acm.deferred_subtrees[:-1]))):
            with self.subTest(what), self.assertRaises(menu.MenuError) as caught:
                menu.sets_of(seeded)
            self.assertIn(caught.exception.kind, ('shape', 'split'), what)

    def test_the_clpc_set_drops_the_base_boards_declaration_that_sits_on_its_root(self):
        clpc = gp.get_profile(P + 'quiet-clpc')
        self.assertEqual(clpc.deferred_functions, ())
        self.assertEqual([(f.node, f.name) for f in gp.J700_26A428.deferred_functions], [(A + 'pmgr/clpc', 'function-ane_perf_ctr')])
        for name in PROFILE_SETS:
            if name != clpc.name:
                self.assertTrue(any(f.name == 'function-ane_perf_ctr' for f in gp.get_profile(name).deferred_functions), name)
        self.assertTrue(any(s.node == A + 'pmgr/clpc' for s in clpc.deferred_subtrees))

    def test_the_jpeg_roots_have_no_pin_and_no_profile_withdraws_them_nor_the_smc_gpio_provider(self):
        for profile in gp.PROFILES.values():
            self.assertFalse(any(s.node in menu.NOT_PINNED for s in profile.deferred_subtrees), profile.name)
        self.assertEqual(len(menu.NOT_PINNED), 4)
        self.assertTrue(all('jpeg' in n for n in menu.NOT_PINNED))
        for name in PROFILE_SETS:
            self.assertFalse({s.node for s in gp.get_profile(name).deferred_subtrees} & {A + 'smc-gpio', A + 'aop-gpio'}, name)


# ---- The closed list, the refusals and the labels: no Apple input ------------------------------------------------------------------------------
class Closed(unittest.TestCase):
    def test_the_list_is_the_base_board_the_eight_and_the_clock_profile_and_nothing_else_is_refused_by_name(self):
        self.assertEqual(gpb.NAMES, (BASE, *PROFILE_SETS, *RTC_SETS, *ROOT_SETS))
        self.assertEqual(gpb.DEFAULT, BASE)
        self.assertEqual(gpb.DEFAULT, ch.GUEST_PROFILE_DEFAULT)
        self.assertEqual(gpb.resolve(ns()), BASE, 'absent is the default')
        for name in gpb.NAMES:
            self.assertEqual(gpb.resolve(ns(guest_profile=name)), name)
        for name in [n for n in gp.PROFILES if n not in gpb.NAMES] + ['', 'quiet', 'all-quiet', P[:-1], (P + 'quiet').upper(), P + 'quiet ', None, 7]:
            with self.subTest(name):
                error = refused(self, 'GUEST_PROFILE_REFUSED', gpb.resolve, ns(guest_profile=name))
                self.assertIn('--guest-profile', error.detail)
                self.assertIn(P + 'all-quiet', error.detail, 'the message lists the menu')

    def test_a_registered_profile_that_is_not_whole_sets_is_refused_before_the_loader_is_called(self):
        good = gp.get_profile(P + 'quiet-smc')
        smc = [s for s in good.deferred_subtrees if s.node.endswith(('/smc', '/btm'))]
        for case, bad in (('smc without btm', dataclasses.replace(good, deferred_subtrees=tuple(s for s in good.deferred_subtrees if s != smc[1]))),
                          ('the unit without aop', dataclasses.replace(good, deferred_subtrees=tuple(s for s in good.deferred_subtrees if not s.node.endswith('/aop2')))),
                          ('a function lost', dataclasses.replace(good, deferred_functions=good.deferred_functions[:-1])),
                          ('another field', dataclasses.replace(good, boot_mpidr=0x123))):
            with self.subTest(case), mock.patch.dict(gp.PROFILES, {good.name: bad}):
                refused(self, 'GUEST_PROFILE_SPLIT', gpb.resolve, ns(guest_profile=good.name))
        with mock.patch.dict(gp.PROFILES):
            del gp.PROFILES[good.name]
            refused(self, 'GUEST_PROFILE_REFUSED', gpb.resolve, ns(guest_profile=good.name))

    def test_the_three_refusals_are_known_to_the_builder_and_are_nobody_elses(self):
        self.assertEqual(gpb.REFUSALS, ('GUEST_PROFILE_REFUSED', 'GUEST_PROFILE_SPLIT', 'GUEST_PROFILE_READBACK'))
        self.assertTrue(set(gpb.REFUSALS) <= ch.KNOWN_REFUSALS)
        self.assertFalse(set(gpb.REFUSALS) & (set(m1.REFUSALS) | set(bb.BUILDER_REFUSALS) | {n for _, n, _ in sb.ERRORS}))

    def test_the_option_is_on_real_and_its_template_with_no_choices_and_the_default_is_the_base_board(self):
        subs = bb.build_parser()._subparsers._group_actions[0].choices
        template_kinds = subs['authorisation-template']._subparsers._group_actions[0].choices
        for parser in (subs['real'], template_kinds['real']):
            action = {a.dest: a for a in parser._actions}['guest_profile']
            self.assertEqual((action.default, action.required, action.choices), (BASE, False, None), 'the refusal names GUEST_PROFILE_REFUSED: argparse does not')
            self.assertIn('EXPERIMENTAL', action.help)
            self.assertTrue(all(n in action.help for n in gpb.NAMES))
        for parser in (subs['synthetic'], template_kinds['synthetic']):
            self.assertNotIn('guest_profile', {a.dest for a in parser._actions})

    def test_every_profile_has_a_mark_of_two_characters_that_no_other_marker_starts_with(self):
        self.assertEqual(gpb.MARKS, MARKS)
        self.assertEqual(len({*MARKS.values()}), 11)
        markers = [*m1.PANEL_MARKS.values(), *m1.BOOT_ARGS.values(), m1.LET_PANIC_MARK]
        for name, mark in MARKS.items():
            self.assertEqual(len(mark), 0 if name == BASE else gpb.MARK_CHARS)
            self.assertTrue(mark == '' or (mark[1] == '-' and mark[0].isupper()))
            self.assertFalse(any(m.startswith(mark) for m in markers if mark), f'{mark} would read as the start of a marker')
        self.assertEqual(gpb.mark_of(None), '')
        refused(self, 'GUEST_PROFILE_REFUSED', gpb.mark_of, 'no-such-profile')

    def test_the_label_of_the_profile_record_is_its_own_and_every_other_form_with_a_profile_is_refused(self):
        form = ('console', ch.RUNG7_FORM[1], True, 'ack')
        label = m1.pinned_label('RUNG7', True, *form, profile=P + 'quiet')
        self.assertEqual(label, 'M1RUN-ACKPANIC-Q-RUNG7')
        self.assertLessEqual(len(label), m1.PIN_LABEL_MAX)
        for (choice, word), marker in OPTION_MARKERS.items():       # the four older forms keep their labels with the default board, named or not
            for profile in (None, BASE):
                self.assertEqual(m1.pinned_label('RUNG4', True, choice, word, profile=profile), 'M1RUN-' + marker + 'RUNG4')
            refused(self, 'GUEST_PROFILE_REFUSED', m1.pinned_label, 'RUNG4', True, choice, word, profile=P + 'quiet')
        for name in MARKS:
            if name not in (BASE, P + 'quiet', ch.RUNG8_PROFILE):
                refused(self, 'GUEST_PROFILE_REFUSED', m1.pinned_label, 'RUNG7', True, *form, profile=name)
        self.assertEqual(m1.pinned_label('RUNG8', True, *form, profile=ch.RUNG8_PROFILE), 'M1RUN-ACKPANIC-R-RUNG8', 'rung 8\'s form (the same four words as rung 7\'s) has its own profile and mark')
        self.assertEqual(m1.pinned_label('RUNG2', True, 'console'), 'M1RUN-CONSOLE-RUNG2', 'rung 2\'s label is what it was')
        self.assertEqual(m1.pinned_label('RUNG3', True, 'console', 'cpus=1', profile=BASE), 'M1RUN-CPUS1-RUNG3', 'rung 3\'s label is what it was')
        # the budget: M1RUN- and ACKPANIC- are 15, a mark 2 and a facts label of six (RUNG10) is the 23rd character
        self.assertEqual(len(m1.LABEL_PREFIX + 'ACKPANIC-' + max(MARKS.values(), key=len) + 'RUNG10'), m1.PIN_LABEL_MAX)

    def test_a_facts_label_that_carries_a_mark_or_does_not_fit_is_refused_and_the_other_kinds_keep_the_facts_label(self):
        form = ('console', ch.RUNG7_FORM[1], True, 'ack')
        for name, mark in MARKS.items():
            if mark:
                with self.subTest(mark):
                    error = refused(self, 'PIN_STRING', m1.pinned_label, mark + 'x', True, *form, profile=P + 'quiet')
                    self.assertIn(name, error.detail)
                    self.assertEqual(m1.pinned_label(mark + 'x', False), mark + 'x')
        self.assertEqual(m1.pinned_label('RUNG10', True, *form, profile=P + 'quiet'), 'M1RUN-ACKPANIC-Q-RUNG10')
        error = refused(self, 'PIN_STRING', m1.pinned_label, 'RUNG100', True, *form, profile=P + 'quiet')
        self.assertIn('holds 6', error.detail)
        refused(self, 'GUEST_PROFILE_REFUSED', m1.pinned_label, 'RUNG4', True, 'console', 'cpus=1', profile='no-such-profile')


# ---- The options end to end through the builder's stand-in loader ---------------------------------------------------------------------------------
class Cli(unittest.TestCase):
    """The options through the builder's stand-in loader: the default board named or not is what it was, and a profile is refused by name in every form but rung 7's (the options of rung 7
    itself, the label and the comparison with rung 6's guest are tools/test-sp11-stick-monitor-rung7.py's; the guests of the real loader are the business of `Guest`)."""
    LABEL = 't-menu'

    def test_the_default_named_or_not_is_the_same_record_as_before_the_option(self):
        code, text, err = PT.record('menu-absent', 'console', facts=BLD.facts_with(self.LABEL), boot_arg='cpus=1')
        self.assertEqual(code, 0, err)
        code, named, err = PT.record('menu-named', 'console', facts=BLD.facts_with(self.LABEL), boot_arg='cpus=1', guest_profile=BASE)
        self.assertEqual((code, named), (0, text))
        self.assertIn(f'label M1RUN-CPUS1-{self.LABEL})', text.splitlines()[0])

    def test_a_profile_is_refused_by_name_in_every_form_but_rung_7_before_a_guest_is_built(self):
        for i, (choice, over) in enumerate((('withheld', {}), ('handed', {}), ('console', {}), ('console', dict(boot_arg='cpus=1')))):
            for name in (P + 'quiet', P + 'all-quiet'):
                with self.subTest(choice=choice, over=over, name=name):
                    code, text, err = PT.record(f'menu-form-{i}', choice, facts=BLD.facts_with(self.LABEL), guest_profile=name, **over)
                    self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=GUEST_PROFILE_REFUSED'), err)

    def test_the_closed_list_refuses_by_name_in_the_cli_before_any_guest_is_built(self):
        for i, name in enumerate(('j700ap-26A428-no-clpc', 'quiet', '', ' ' + BASE)):
            with self.subTest(name):
                code, text, err = PT.record(f'menu-refused-{i}', 'console', facts=BLD.facts_with(self.LABEL), boot_arg='cpus=1', guest_profile=name)
                self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=GUEST_PROFILE_REFUSED'), err)
        good = gp.get_profile(P + 'quiet-smc')
        with mock.patch.dict(gp.PROFILES, {good.name: dataclasses.replace(good, deferred_functions=good.deferred_functions[:-1])}):
            code, text, err = PT.record('menu-split', 'console', facts=BLD.facts_with(self.LABEL), boot_arg='cpus=1', guest_profile=good.name)
        self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=GUEST_PROFILE_SPLIT'), err)
        code, text, err = PT.record('menu-facts-mark', 'console', facts=BLD.facts_with('Q-x'), boot_arg='cpus=1')
        self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=PIN_STRING'), err)

# ---- The generated trees: boot set --------------------------------------------------------------------------------------------------------------
@needs_boot_set
class Trees(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.full, cls.base = tree(BASE, include_deferred=True), tree(BASE)
        cls.trees = {name: tree(name) for name in PROFILE_SETS}

    def withdrawn(self, name):
        return below(self.full, extra_roots(name))

    def test_each_root_set_alone_withdraws_exactly_its_roots_and_the_counts_are_pinned(self):
        self.assertEqual((len(self.full.nodes), len(self.base.nodes), len(self.base.deferred_nodes)), (412, 395, 17))
        for name, (roots, _, _, needs, count) in EXPECTED.items():
            with self.subTest(name):
                names = closure(name)
                specs, functions = raw(names)
                dt = copy.deepcopy(self.full)
                removed = set(defer_subtrees(dt, specs, functions)) - set(self.base.deferred_nodes)
                self.assertEqual(removed, below(self.full, [r for n in menu.ORDER if n in names for r in EXPECTED[n][0]]) - set(self.base.deferred_nodes))
                self.assertEqual(len(below(self.full, roots)), count, f'{name}: {count} nodes under its roots')
                self.assertEqual(len(removed), sum(EXPECTED[n][4] for n in names))

    def test_exactly_the_pinned_roots_vanish_for_every_profile_and_the_counts_are_pinned(self):
        for name, sets in PROFILE_SETS.items():
            with self.subTest(name):
                dt, gone = self.trees[name], self.withdrawn(name)
                self.assertEqual(set(self.base.nodes) - set(dt.nodes), gone)
                self.assertEqual(set(dt.nodes) - set(self.base.nodes), set())
                self.assertEqual(set(dt.deferred_nodes), set(self.full.nodes) - set(dt.nodes))
                self.assertEqual((len(dt.nodes), len(gone)), (AFTER[name], 395 - AFTER[name]))
                self.assertEqual(len(gone), sum(EXPECTED[n][4] for n in sets))
                for root in extra_roots(name):
                    self.assertIn(root, self.base.nodes)
                    self.assertNotIn(root, dt.nodes)
        self.assertEqual({n: len(t.nodes) for n, t in self.trees.items()}, {n: v for n, v in AFTER.items() if n != BASE})

    def test_the_models_and_every_retained_node_are_the_base_boards(self):
        for name, dt in self.trees.items():
            with self.subTest(name):
                self.assertEqual(len(self.base.device_models), 120)
                self.assertEqual(dt.device_models, self.base.device_models)
                self.assertEqual(dt.monitor_uat_geometry, self.base.monitor_uat_geometry)
                gone, dropped = self.withdrawn(name), {(f.node, f.name) for f in gp.get_profile(name).deferred_functions} ^ {(f.node, f.name) for f in gp.J700_26A428.deferred_functions}
                for node in dt.nodes:
                    expected = {k: v for k, v in props(self.base, node).items() if (node, k) not in dropped}
                    self.assertEqual(props(dt, node), expected, node)
                    self.assertEqual(dt.children[node], [c for c in self.base.children[node] if c not in gone], node)
                self.assertEqual(DeviceTree(dt.serialize()).serialize(), dt.serialize(), 'the published tree parses back to itself')

    def test_the_six_cpu_nodes_and_the_pmgr_and_coresight_pins_are_untouched(self):
        base = gp.get_profile(BASE)
        for name, dt in self.trees.items():
            with self.subTest(name):
                for i in range(6):
                    cpu = f'/device-tree/cpus/cpu{i}'
                    self.assertEqual(props(dt, cpu), props(self.base, cpu), cpu)
                self.assertEqual(dt.children['/device-tree/cpus'], self.base.children['/device-tree/cpus'])
                profile = gp.get_profile(name)
                self.assertEqual((profile.pmgr_boot, profile.soctuner_boot, profile.coresight_boot, profile.uniform_cpu_clusters),
                                 (base.pmgr_boot, base.soctuner_boot, base.coresight_boot, base.uniform_cpu_clusters))
                self.assertEqual(len(profile.coresight_boot), 6)
                exempt = {(f.node, f.name) for f in profile.deferred_functions} ^ {(f.node, f.name) for f in base.deferred_functions}      # what the profile declares withdrawn, nothing else
                self.assertEqual(sorted(k for n, k in exempt if n == A + 'pmgr/ppm'), sorted(k for _, k in PPM_BTM) if name.endswith(('smc', 'all-quiet')) else [],
                                 'the four btm functions of ppm are the one change to a PMGR property, and only where btm goes')
                for node in below(self.base, (A + 'pmgr',)):
                    if node in dt.nodes:
                        self.assertEqual(props(dt, node), {k: v for k, v in props(self.base, node).items() if (node, k) not in exempt}, node)
                self.assertEqual(A + 'pmgr/clpc' in dt.nodes, not name.endswith('clpc'))

    def test_every_root_a_profile_does_not_withdraw_and_what_a_boot_needs_are_still_published(self):
        retained = runpy.run_path(str(ROOT / 'tools/test-quiet-profile.py'))['RETAINED']       # the A16 quiet profile's own list of roots a boot needs
        for name, dt in self.trees.items():
            with self.subTest(name):
                for root in UNIVERSE:
                    self.assertEqual(root in dt.nodes, root not in extra_roots(name), root)
                for root in (*RETAINED_ALWAYS, *retained):
                    self.assertEqual(root in dt.nodes, root in self.base.nodes, root)
                self.assertGreaterEqual(len([r for r in retained if r in dt.nodes]), 15, 'the check is not vacuous')

    def test_no_retained_provider_reference_dangles_in_any_profile(self):
        for name, dt in self.trees.items():
            with self.subTest(name):
                phandles = {bytes(self.full.get(n, 'AAPL,phandle')) for n in self.withdrawn(name) if self.full.get(n, 'AAPL,phandle') is not None}
                self.assertTrue(len(phandles) > 10)
                for node, node_props in dt.nodes.items():
                    for prop_name, prop in node_props.items():
                        if provider(prop_name):
                            self.assertNotIn(bytes(prop.value[:4]), phandles, (node, prop_name))

    def test_every_hash_binds_the_exact_subtree_of_every_set(self):
        for name, (roots, _, _, needs, _) in EXPECTED.items():
            specs, functions = raw(closure(name))
            index = [i for i, s in enumerate(specs) if s.node in roots]
            with self.subTest(name):
                for spec in specs[len(menu.BASE.deferred_subtrees):]:
                    self.assertEqual(hashlib.sha256(self.full.serialize(spec.node)).hexdigest(), spec.sha256, spec.node)
                for i in index:
                    wrong = list(specs)
                    wrong[i] = dataclasses.replace(specs[i], sha256=('0' if specs[i].sha256[0] != '0' else '1') + specs[i].sha256[1:])
                    with self.assertRaisesRegex(ValueError, 'deferred subtree changed'):
                        defer_subtrees(self.full, wrong, functions)
                root = roots[0]       # one property of the root changes: the pinned hash no longer matches, and nothing is withdrawn
                prop_name = next(n for n in self.full.nodes[root] if n not in ('name', 'AAPL,phandle'))
                original, before = self.full.nodes[root][prop_name].value, [props(self.full, n) for n in self.full.nodes]
                self.full.nodes[root][prop_name].value = bytes(original[:-1]) + bytes([original[-1] ^ 1]) if len(original) else b'\x01'
                try:
                    with self.assertRaisesRegex(ValueError, 'deferred subtree changed'):
                        defer_subtrees(self.full, specs, functions)
                finally:
                    self.full.nodes[root][prop_name].value = original
                self.assertEqual([props(self.full, n) for n in self.full.nodes], before)

    def test_an_added_dependency_on_a_withdrawn_provider_is_refused_for_every_set(self):
        for name, (roots, _, _, needs, _) in EXPECTED.items():
            specs, functions = raw(closure(name))
            dt = copy.deepcopy(self.full)
            root = next(r for r in roots if dt.get(r, 'AAPL,phandle') is not None)
            dt.put(A + 'wdt', 'function-test-m1-menu', bytes(dt.get(root, 'AAPL,phandle')) + struct.pack('<I', 1))
            with self.subTest(name), self.assertRaisesRegex(ValueError, 'still references a deferred provider'):
                defer_subtrees(dt, specs, functions)

    def test_each_need_between_sets_is_a_refusal_of_the_loaders_own_guard_and_each_union_with_its_needs_is_accepted(self):
        for name, (_, _, _, needs, _) in EXPECTED.items():
            names = closure(name)
            specs, functions = raw(names)
            self.assertEqual(len(defer_subtrees(copy.deepcopy(self.full), specs, functions)) - 17, sum(EXPECTED[n][4] for n in names), name)
            for need in needs:
                with self.subTest(set=name, without=need):
                    specs, functions = raw(names - {need})
                    with self.assertRaisesRegex(ValueError, 'still references a deferred provider'):
                        defer_subtrees(copy.deepcopy(self.full), specs, functions)

    def test_the_loader_cannot_see_the_btm_trap_so_the_menu_refuses_the_split_and_the_unit_is_what_the_loader_accepts(self):
        names = closure('smcbtm')
        specs, functions = raw(names)
        split_specs = tuple(s for s in specs if not s.node.endswith('/btm'))
        split_functions = tuple(f for f in functions if (f.node, f.name) not in PPM_BTM)
        # the guard accepts smc without btm: nothing dangles, and the trap (btm published once pmu-main returns, AppleCotaBTM "unable to config PMU") is a run-time fact
        self.assertTrue(defer_subtrees(copy.deepcopy(self.full), split_specs, split_functions))
        good = gp.get_profile(P + 'quiet-smc')
        split = dataclasses.replace(good, deferred_subtrees=tuple(s for s in good.deferred_subtrees if not s.node.endswith('/btm')),
                                    deferred_functions=tuple(f for f in good.deferred_functions if (f.node, f.name) not in PPM_BTM))
        with self.assertRaises(menu.MenuError) as caught:
            menu.sets_of(split)
        self.assertEqual(caught.exception.kind, 'split')


# ---- The guest of each profile: boot set and the kernelcache --------------------------------------------------------------------------------------
@needs_boot_set
class Guest(unittest.TestCase):
    """real_built for the console panel with cpus=1 (the guest of rung 3), once per profile, reduced to chunk digests and the chunks that matter."""
    summaries = {}

    @classmethod
    def built(cls, name):
        if name not in cls.summaries:
            loader = bb.xnuboot()
            a = ns(base=hex(BANK), guest_mib=384, reserve_mib=None, kernelcache=loader.DEFAULT_KERNELCACHE, panel='console', sptm_hvc_calls=0, time_box_s=5, seed='sp11-stick-bundle',
                   boot_arg='cpus=1', **({} if name is None else dict(guest_profile=name)))
            built = bb.real_built(a, loader, bb.placement_of(a), bb.load_facts(EXAMPLE_FACTS))
            gpb.read_back(loader, built, built['guest_profile'])       # the builder's own check, which `prepare` makes after real_built
            chunks = ch.chunks_from_built(built, ch.layout_of(loader))
            ch.check_params(chunks, bb.placement_of(a), 5)
            data = {c['kind']: c.get('data') for c in chunks if c['kind'] in (sb.KIND_PARAMS, sb.KIND_DEVICE_TREE)}
            cls.summaries[name] = dict(profile=built['guest_profile'], table=[(c['dest'], c.get('size', len(c.get('data', b''))), c['kind']) for c in chunks],
                                       digests=[hashlib.sha256(c.get('data', b'')).hexdigest() for c in chunks], params=data[sb.KIND_PARAMS], tree=data[sb.KIND_DEVICE_TREE],
                                       handoff_pa=loader.HANDOFF_PA, loader=loader, built=built,
                                       handoff={c['dest']: bytes(c['data']) for c in chunks if c['kind'] == sb.KIND_HANDOFF and 'data' in c})
        return cls.summaries[name]

    def test_each_guest_differs_from_the_default_in_the_device_tree_and_the_tree_length_word_only(self):
        base = self.built(BASE)
        self.assertEqual(base['profile'], BASE)
        self.assertEqual(self.built(None)['digests'], base['digests'], 'naming the default is no change: the same chunks, byte for byte')
        field = base['handoff_pa'] + 0x1000 + 0x68      # boot_args (the handoff's second page) deviceTreeLength, a u32: probes/xnuboot.py boot_args
        for name in PROFILE_SETS:
            with self.subTest(name):
                guest = self.built(name)
                self.assertEqual(guest['profile'], name, 'the loader built the profile that was asked for')
                self.assertEqual([(d, k) for d, _, k in base['table']], [(d, k) for d, _, k in guest['table']], 'the same chunks at the same addresses')
                differing = [i for i, (x, y) in enumerate(zip(base['digests'], guest['digests'])) if x != y]
                self.assertEqual(sorted({base['table'][i][2] for i in differing}), sorted((sb.KIND_DEVICE_TREE, sb.KIND_HANDOFF)))
                self.assertEqual(len(differing), 2, 'two chunks of the table: the HANDOFF page that holds the word, and the DEVICE_TREE')
                self.assertTrue(all(base['table'][i][1] == guest['table'][i][1] for i in differing if base['table'][i][2] != sb.KIND_DEVICE_TREE), 'only the device tree changes its size')
                self.assertLess(len(guest['tree']), len(base['tree']))
                moved = [dest + o for dest, blob in base['handoff'].items() for o in range(len(blob)) if blob[o] != guest['handoff'][dest][o]]
                self.assertTrue(moved and all(field <= pa < field + 4 for pa in moved), 'the only bytes that differ outside the tree are the length of the tree')
                dest = next(d for d, blob in guest['handoff'].items() if d <= field < d + len(blob))
                self.assertEqual(struct.unpack_from('<I', guest['handoff'][dest], field - dest)[0], len(DeviceTree(guest['tree']).serialize()))
                self.assertEqual(len(DeviceTree(guest['tree']).nodes), AFTER[name])

    def test_the_read_back_of_the_quiet_profile_refuses_windows_that_the_record_does_not_name(self):
        guest = self.built(P + 'quiet')
        gpb.read_back(guest['loader'], guest['built'], P + 'quiet')
        for node, windows in {'avd': ((0x380001000, 0x381404000),), 'scaler0': win.SCALER[:3]}.items():
            with self.subTest(node), mock.patch.dict(win.BY_NODE, {node: windows}):
                refused(self, 'GUEST_PROFILE_READBACK', gpb.read_back, guest['loader'], guest['built'], P + 'quiet')

    def test_the_parameters_the_monitor_reads_and_the_120_device_models_are_identical_in_every_guest(self):
        base = self.built(BASE)
        count = struct.unpack_from('<Q', base['params'], 0x2D0)[0]
        self.assertEqual(count, 120)
        for name in PROFILE_SETS:
            with self.subTest(name):
                guest = self.built(name)
                self.assertEqual(guest['params'], base['params'])
                self.assertEqual(guest['params'][0x2D8:0x2D8 + 64 * count], base['params'][0x2D8:0x2D8 + 64 * count])

    def test_the_read_back_refuses_each_seeded_fault_and_leaves_the_default_alone(self):
        guest = self.built(P + 'quiet-smc')
        loader, built, name = guest['loader'], guest['built'], P + 'quiet-smc'
        gpb.read_back(loader, built, name)
        gpb.read_back(loader, {}, BASE)             # the default is not read back: not even a built dict is needed
        good = DeviceTree(bytes(built['chunks'][loader.DEVICETREE_PA]))

        def seeded(blob, profile_name=name, patched=None):
            copy_built = {**built, 'chunks': {**built['chunks'], loader.DEVICETREE_PA: blob}}
            with mock.patch.dict(gp.PROFILES, patched or {}):
                return refused(self, 'GUEST_PROFILE_READBACK', gpb.read_back, loader, copy_built, profile_name)

        def mutated(change):
            dt = copy.deepcopy(good)
            change(dt)
            return dt.serialize()

        def drop(path):
            def change(dt):
                parent = path.rsplit('/', 1)[0]
                dt.children[parent].remove(path)
                for n in [n for n in dt.nodes if n == path or n.startswith(path + '/')]:
                    del dt.nodes[n], dt.children[n]
            return change
        quiet_tree = self.built(P + 'quiet')['tree']
        self.assertIn('withdrawn nodes are still published', seeded(quiet_tree).detail, 'the tree of a smaller profile than the one named')
        self.assertIn('nodes that are not withdrawn are not published', seeded(bytes(built['chunks'][loader.DEVICETREE_PA]), P + 'quiet').detail, 'the tree of a bigger one')
        self.assertIn('nodes that are not withdrawn are not published', seeded(mutated(drop(A + 'smc-gpio'))).detail)
        def flip(dt):
            prop = next(n for n in dt.nodes[A + 'wdt'] if n not in ('name', 'AAPL,phandle'))
            value = bytes(dt.nodes[A + 'wdt'][prop].value) or b'\0'
            dt.nodes[A + 'wdt'][prop].value = bytes([value[0] ^ 1]) + value[1:]
        self.assertIn('are not those of the generated tree', seeded(mutated(lambda dt: dt.put(A + 'wdt', 'function-seeded', b'\x01\x02\x03\x04'))).detail)
        self.assertIn('are not those of the generated tree', seeded(mutated(flip)).detail)
        self.assertIn('not the serialisation', seeded(good.serialize() + b'\0').detail)
        self.assertIn('cannot be read', seeded(b'not a tree').detail)
        profile = gp.get_profile(name)
        wrong = dataclasses.replace(profile.deferred_subtrees[-1], sha256='0' * 64)
        self.assertIn('is not its pin', seeded(good.serialize(), patched={name: dataclasses.replace(profile, deferred_subtrees=profile.deferred_subtrees[:-1] + (wrong,))}).detail)
        wrong_function = dataclasses.replace(profile.deferred_functions[-1], value=bytes(len(profile.deferred_functions[-1].value)))
        self.assertIn('is not withdrawn as the profile says', seeded(good.serialize(), patched={name: dataclasses.replace(profile, deferred_functions=profile.deferred_functions[:-1] + (wrong_function,))}).detail)


if __name__ == '__main__':
    unittest.main()
