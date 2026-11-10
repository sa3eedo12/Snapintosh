#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Host tests for the guest CPU topology model (compat/topology.[ch]).

Host only: the model is built natively with sanitizers and compared with the
live host tools, the C preprocessor and the monitor sources. Nothing here talks
to hardware. The mutation checks edit a COPY of compat/topology.c under build/;
the real source never changes.

SP11 facts used by the model (3 clusters of 4 Oryon v1 cores, the redistributor
window, the hypervisor timer INTID) are INFERRED from public sources until the
EL2 probe (plan item X2) measures them; the synthetic redistributor images are
EXPERIMENTAL stand-ins.
"""
import ast
import copy
import os
import re
import runpy
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / 'build'
HARNESS = [ROOT / 'tools/test-guest-topology.c', ROOT / 'tools/test-guest-topology-gicr.c']
MODEL = ROOT / 'compat/topology.c'
HEADER = ROOT / 'compat/topology.h'
SANITIZED = ['clang', '-std=c11', '-O1', '-g', '-fsanitize=address,undefined',
             '-fno-sanitize-recover=all', '-Wall', '-Wextra', '-Werror']
FREESTANDING = ['--target=aarch64-unknown-none-elf', '-ffreestanding', '-fno-pic',
                '-mgeneral-regs-only', '-O1', '-Wall', '-Wextra', '-Werror', '-mcmodel=tiny']
A16_REDISTRIBUTOR_FIRST, A16_REDISTRIBUTOR_LIMIT, GICR_STRIDE = 0x17080000, 0x17500000, 0x40000


def run(cmd, **kwargs):
    return subprocess.run([str(c) for c in cmd], cwd=ROOT, capture_output=True, text=True, **kwargs)


def header_enum(name):
    """Enumerator values of `enum <name>` in compat/topology.h, comments removed."""
    text = re.sub(r'/\*.*?\*/', '', HEADER.read_text(), flags=re.S)
    body = re.search(r'enum\s+%s\s*\{([^}]*)\}' % name, text).group(1)
    values, current = {}, -1
    for item in filter(None, (i.strip() for i in body.split(','))):
        label, _, explicit = (p.strip() for p in item.partition('='))
        current = int(explicit, 0) if explicit else current + 1
        values[label] = current
    return values


ST = header_enum('topology_status')
GST = header_enum('topology_gicr_status')

_harness_binary = []


def harness():
    """The sanitized harness, built once per process."""
    if not _harness_binary:
        BUILD.mkdir(exist_ok=True)
        out = BUILD / 'test-guest-topology'
        result = run([*SANITIZED, *HARNESS, MODEL, '-I', ROOT / 'compat', '-o', out])
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        _harness_binary.append(out)
    return _harness_binary[0]


def place(profile, order, primary, count, fair):
    """Model placement through the harness command line: (status, [affinity...])."""
    result = run([harness(), '--place', profile, order, hex(primary), count, int(fair)])
    status = int(re.search(r'^place=(\d+) ', result.stdout, re.M).group(1))
    return status, [int(a, 0) for _, a, _ in re.findall(r'^slot (\d+) (\S+) (\d+)$',
                                                        result.stdout, re.M)]


def assign(profile, order, primary, count, fair, inventory, version=4,
           stride=GICR_STRIDE, base=A16_REDISTRIBUTOR_FIRST, limit=A16_REDISTRIBUTOR_LIMIT):
    """Placement then redistributor assignment: (place status, gicr status, assign status, [rd...])."""
    listing = ','.join('%s@%s' % (cpu['mpidr'], cpu['redistributor']) for cpu in inventory['cpus'])
    result = run([harness(), '--assign', profile, order, hex(primary), count, int(fair),
                  version, hex(stride), hex(base), hex(limit), listing])
    text = result.stdout
    grab = lambda key: int(m.group(1)) if (m := re.search(r'^%s=(\d+)' % key, text, re.M)) else None
    return (grab('place'), grab('gicr'), grab('assign'),
            [int(v, 0) for _, v in re.findall(r'^rd (\d+) (\S+)$', text, re.M)])


def a16_inventory():
    """The measured A16 inventory shape used by tools/test-secondary-runtime.py."""
    mpidrs = [c * 0x10000 + i * 0x100 for c in range(3) for i in range(6)]
    return dict(current_el=2, psci_compliant=True, psci_uses_hvc=False, boot_mpidr='0x0',
                features={f: 0 for f in ('0xc4000003', '0x84000002', '0xc4000004')},
                gic=dict(distributors=[dict(base='0x17000000', version=4)]),
                cpus=[dict(mpidr=hex(m), flags=1, affinity_state=0 if m == 0 else 1,
                           redistributor=hex(A16_REDISTRIBUTOR_FIRST + n * GICR_STRIDE),
                           efficiency_class=0 if m < 0x10000 else 1)
                      for n, m in enumerate(mpidrs)])


def sp11_inventory(count=12):
    """EXPERIMENTAL synthetic SP11 inventory: 3 x 4 cores, one frame each at stride 0x40000."""
    mpidrs = [c * 0x10000 + i * 0x100 for c in range(3) for i in range(4)][:count]
    return dict(cpus=[dict(mpidr=hex(m), redistributor=hex(A16_REDISTRIBUTOR_FIRST + n * GICR_STRIDE))
                      for n, m in enumerate(mpidrs)])


def preprocess(source, *flags):
    """Object-like macros left by `clang -E -dM`; None when the preprocessor fails."""
    result = run(['clang', '--target=aarch64-unknown-none-elf', '-ffreestanding', '-E', '-dM',
                  '-x', 'c', source, '-I', ROOT / 'compat', *flags])
    if result.returncode:
        return None, result.stderr
    return dict(re.findall(r'^#define (\w+) (.*)$', result.stdout, re.M)), result.stderr


class NativeModel(unittest.TestCase):
    def test_native_suite_passes_under_sanitizers(self):
        result = run([harness()])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        summary = re.search(r'test-guest-topology: (\d+) checks, (\d+) failures, (\d+) groups',
                            result.stdout)
        self.assertTrue(summary, result.stdout)
        checks, failures, groups = map(int, summary.groups())
        self.assertEqual(failures, 0)
        # Floors keep a harness that lost most of its tests from passing vacuously.
        self.assertGreaterEqual(checks, 250000)
        self.assertGreaterEqual(groups, 33)
        self.assertTrue(result.stdout.rstrip().endswith('PASS'))
        self.assertEqual(result.stderr, '')

    def test_command_line_refuses_malformed_requests(self):
        for argv in (['--place'], ['--place', 'a16', 'pack', '0', '6'],
                     ['--place', 'x11', 'pack', '0', '6', '0'],
                     ['--place', 'a16', 'middle', '0', '6', '0'],
                     ['--place', 'a16', 'pack', 'zero', '6', '0'],
                     ['--assign', 'a16', 'pack', '0', '6', '0'],
                     ['--vmpidr'], ['--vmpidr', 'x'], ['--bogus', 'a', 'b', 'c', 'd', 'e']):
            self.assertEqual(run([harness(), *argv]).returncode, 2, argv)

    def test_freestanding_object_has_no_hidden_state_or_runtime_calls(self):
        # The model is linked into SP11 monitor builds: no writable globals, no
        # runtime calls beyond what probes/memory.S already provides.
        nm = shutil.which('llvm-nm') or shutil.which('nm')
        if not nm:
            self.skipTest('llvm-nm not found')
        BUILD.mkdir(exist_ok=True)
        obj = BUILD / 'topology-freestanding.o'
        result = run(['clang', *FREESTANDING, '-c', MODEL, '-I', ROOT / 'compat', '-o', obj])
        self.assertEqual(result.returncode, 0, result.stderr)
        symbols = run([nm, obj]).stdout.splitlines()
        undefined = {line.split()[-1] for line in symbols if line.split()[0] == 'U'}
        self.assertLessEqual(undefined, {'memcpy', 'memset'}, undefined)
        writable = [line for line in symbols if len(line.split()) == 3 and line.split()[1] in 'DdBbCc']
        self.assertEqual(writable, [])
        defined = {line.split()[-1] for line in symbols if len(line.split()) == 3}
        for name in ('topology_place', 'topology_check_placement', 'topology_gicr_discover',
                     'topology_gicr_assign', 'topology_profile_sp11', 'topology_policy_pack'):
            self.assertIn(name, defined)


class LiveSources(unittest.TestCase):
    """The model against the A16 code paths it must reproduce bit for bit."""

    @classmethod
    def setUpClass(cls):
        cls.api = runpy.run_path(str(ROOT / 'tools/secondary-runtime.py'))
        cls.memory = dict(sctlr='0x10003000100d', tcr='0x80843514', ttbr0='0xa8f5a000',
                          mair='0xffbb4400', hcr='0x88000010')

    def words(self, prime, inventory=None, mask=0x3e):
        with patch.dict('os.environ', {'SNAP_SMP_PRIME': prime}):
            return self.api['config_words'](inventory or a16_inventory(), self.memory, mask)

    def test_default_layout_equals_pack_from_the_boot_cpu(self):
        words = self.words('0')
        status, affinity = place('a16', 'pack', 0x0, 6, False)
        self.assertEqual(status, ST['TOPOLOGY_OK'])
        # Slot 0 is the CPU q1n1 already runs on, so the tool leaves its target unset.
        self.assertEqual(words[8], 0)
        self.assertEqual(list(words[9:14]), affinity[1:])
        _, gicr, assigned, redistributors = assign('a16', 'pack', 0x0, 6, False, a16_inventory())
        self.assertEqual((gicr, assigned), (GST['TOPOLOGY_GICR_OK'],) * 2)
        self.assertEqual(list(words[15:20]), redistributors[1:])

    def test_prime_layout_equals_pack_from_the_prime_core(self):
        words = self.words('1')
        status, affinity = place('a16', 'pack', 0x10000, 6, False)
        self.assertEqual(status, ST['TOPOLOGY_OK'])
        self.assertEqual(list(words[8:14]), affinity)
        _, gicr, assigned, redistributors = assign('a16', 'pack', 0x10000, 6, False, a16_inventory())
        self.assertEqual((gicr, assigned), (GST['TOPOLOGY_GICR_OK'],) * 2)
        self.assertEqual(list(words[14:20]), redistributors)

    def test_model_never_spans_clusters_without_a_fair_lock(self):
        # tools/secondary-runtime.py refuses a layout spanning physical clusters
        # (the v26 starvation). The model agrees for every primary and count.
        with self.assertRaisesRegex(ValueError, 'span physical clusters'):
            moved = a16_inventory()
            moved['boot_mpidr'] = '0x10000'
            for cpu in moved['cpus']:
                cpu['affinity_state'] = 0 if cpu['mpidr'] == '0x10000' else 1
            self.words('0', moved)
        for primary in (0x0, 0x10000, 0x20000):
            for count in range(1, 7):
                self.assertEqual(place('a16', 'pack', primary, count, False)[0], ST['TOPOLOGY_OK'])
        for policy in ('pack', 'spread'):
            self.assertEqual(place('a16', policy, 0x0, 7, False)[0] == ST['TOPOLOGY_OK'],
                             False if policy == 'pack' else False)
        self.assertEqual(place('a16', 'pack', 0x0, 7, True)[0], ST['TOPOLOGY_OK'])

    def test_sp11_assignment_through_the_command_line(self):
        inventory = sp11_inventory()
        # Limit is 12 frames, not the A16 window: the A16 limit would admit MMIO that is not a GICR.
        args = ('sp11', 'pack', 0x10000, 4, False, inventory)
        place_status, gicr, assigned, redistributors = assign(
            *args, limit=A16_REDISTRIBUTOR_FIRST + 12 * GICR_STRIDE)
        self.assertEqual((place_status, gicr, assigned), (0, 0, 0))
        self.assertEqual(redistributors, [A16_REDISTRIBUTOR_FIRST + n * GICR_STRIDE for n in range(4, 8)])
        gappy = copy.deepcopy(inventory)
        del gappy['cpus'][2]
        _, gicr, _, redistributors = assign('sp11', 'pack', 0x0, 4, False, gappy,
                                            limit=A16_REDISTRIBUTOR_FIRST + 12 * GICR_STRIDE)
        self.assertEqual(gicr, GST['TOPOLOGY_GICR_DUPLICATE_AFFINITY'])
        self.assertEqual(redistributors, [])
        _, gicr, _, _ = assign('sp11', 'pack', 0x0, 4, False, inventory, version=5,
                               limit=A16_REDISTRIBUTOR_FIRST + 12 * GICR_STRIDE)
        self.assertEqual(gicr, GST['TOPOLOGY_GICR_BAD_VERSION'])

    def test_guest_vmpidr_matches_setup_c(self):
        source = (ROOT / 'probes/xnuboot/setup.c').read_text()
        self.assertEqual(len(re.findall(
            r'vmpidr\s*=\s*0x80000000UL\s*\|\s*\(n\s*<\s*4\s*\?\s*n\s*:\s*0x100\s*\+\s*n\s*-\s*4\)',
            source)), 1)
        for slot in range(6):
            live = 0x80000000 | (slot if slot < 4 else 0x100 + slot - 4)
            out = run([harness(), '--vmpidr', slot]).stdout
            self.assertEqual(int(out.split('=')[1], 0), live, slot)
        # setup.c extrapolates past the J700 six-core shape; the model refuses to.
        for slot in (6, 7, 11):
            self.assertEqual(int(run([harness(), '--vmpidr', slot]).stdout.split('=')[1], 0), 0)

    def test_gicr_affinity_conversion_matches_the_el2_code(self):
        source = (ROOT / 'probes/secondary.c').read_text()
        self.assertIn('((s->affinity&0xffffff)|((s->affinity>>32)<<24))', source)

    def test_run_py_links_the_model_only_for_sp11(self):
        tree = ast.parse((ROOT / 'probes/run.py').read_text())
        optional = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                        and any(getattr(t, 'id', '') == 'OPTIONAL' for t in n.targets))
        # Other platform work adds its own tuples; only this model's entry is pinned.
        gates = [define for define, source in optional['xnuboot'] if source == 'compat/topology.c']
        self.assertEqual(gates, ['-DSNAP_PLATFORM_SP11=1'])
        self.assertTrue((ROOT / 'compat/topology.c').is_file())


class ProfileMacros(unittest.TestCase):
    def cpu_count(self, *flags):
        macros, err = preprocess(ROOT / 'compat/cpu.h', *flags)
        return macros['GUEST_CPU_COUNT'] if macros else err

    def test_a16_count_is_the_literal_six_and_ignores_the_model(self):
        self.assertIn('#define GUEST_CPU_COUNT 6U\n#endif', (ROOT / 'compat/cpu.h').read_text())
        self.assertEqual(self.cpu_count(), '6U')
        result = run(['clang', '--target=aarch64-unknown-none-elf', '-ffreestanding', '-E',
                      '-x', 'c', ROOT / 'compat/cpu.h', '-I', ROOT / 'compat'])
        self.assertNotIn('topology.h', result.stdout)

    def test_count_override_takes_effect(self):
        # A16: the header's literal six is not overridable. A -D of another spelling is the macro redefinition it always
        # was (an error under the monitor's -Werror, tools/test-monitor-hardening.py); without -Werror the six wins.
        self.assertEqual(self.cpu_count('-DGUEST_CPU_COUNT=4U'), '6U')
        self.assertEqual(self.cpu_count('-DSNAP_PLATFORM_SP11=1'), '6U')
        for count in (1, 4, 6, 12):
            self.assertEqual(self.cpu_count('-DSNAP_PLATFORM_SP11=1', '-DGUEST_CPU_COUNT=%d' % count),
                             str(count))
        result = run(['clang', '--target=aarch64-unknown-none-elf', '-ffreestanding', '-E',
                      '-DSNAP_PLATFORM_SP11=1', '-x', 'c', ROOT / 'compat/cpu.h', '-I', ROOT / 'compat'])
        self.assertIn('topology.h', result.stdout)

    def test_sp11_refuses_counts_outside_the_physical_cores(self):
        for count in (0, 13, 16, 64, -1):
            out = self.cpu_count('-DSNAP_PLATFORM_SP11=1', '-DGUEST_CPU_COUNT=%d' % count)
            self.assertIn('GUEST_CPU_COUNT must be between 1 and the 12 physical cores', out, count)
        # The A16 build has no such range check and no override at all: its six is the header's, as it always was.
        self.assertEqual(self.cpu_count('-DGUEST_CPU_COUNT=13'), '6U')

    def gic_macros(self, *flags):
        macros, _ = preprocess(ROOT / 'probes/secondary.c', *flags)
        return macros

    def test_a16_gic_macros_are_the_original_literals(self):
        macros = self.gic_macros()
        self.assertEqual({k: macros[k] for k in ('SNAP_GICD_BASE', 'SNAP_GICR_FIRST', 'SNAP_GICR_LIMIT',
                                                  'SNAP_GICR_STRIDE', 'SNAP_TIMER_INTID',
                                                  'SECONDARY_GIC_UNMEASURED')},
                         {'SNAP_GICD_BASE': '0x17000000', 'SNAP_GICR_FIRST': '0x17080000',
                          'SNAP_GICR_LIMIT': '0x17500000', 'SNAP_GICR_STRIDE': '0x40000',
                          'SNAP_TIMER_INTID': '26', 'SECONDARY_GIC_UNMEASURED': '0'})

    def test_sp11_gic_has_no_defaults_and_fails_closed(self):
        five = ['-DSNAP_GICD_BASE=0x17000000', '-DSNAP_GICR_FIRST=0x17080000',
                '-DSNAP_GICR_LIMIT=0x17380000', '-DSNAP_GICR_STRIDE=0x40000', '-DSNAP_TIMER_INTID=26']
        self.assertEqual(self.gic_macros('-DSNAP_PLATFORM_SP11=1')['SECONDARY_GIC_UNMEASURED'], '1')
        self.assertEqual(self.gic_macros('-DSNAP_PLATFORM_SP11=1', *five)['SECONDARY_GIC_UNMEASURED'], '0')
        for omitted in range(5):
            self.assertEqual(self.gic_macros('-DSNAP_PLATFORM_SP11=1',
                                             *five[:omitted], *five[omitted + 1:])['SECONDARY_GIC_UNMEASURED'],
                             '1', five[omitted])
        BUILD.mkdir(exist_ok=True)

        def compile_variant(*flags):
            return run(['clang', *FREESTANDING, '-c', ROOT / 'probes/secondary.c', *flags,
                        '-o', BUILD / 'secondary-topology-check.o'])
        for flags in ((), ('-DSNAP_PLATFORM_SP11=1',), ('-DSNAP_PLATFORM_SP11=1', *five)):
            result = compile_variant(*flags)
            self.assertEqual(result.returncode, 0, result.stderr)
        # A timer that is not a PPI is a build error, not a silently wrong register write.
        result = compile_variant('-DSNAP_PLATFORM_SP11=1', *five[:4], '-DSNAP_TIMER_INTID=32')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('secondary timer must be a PPI', result.stderr)
        result = compile_variant('-DSNAP_PLATFORM_SP11=1', *five[:4], '-DSNAP_TIMER_INTID=15')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('secondary timer must be a PPI', result.stderr)
        # Strides that alias the next frame (one 64 KiB frame) or are not frame multiples are refused;
        # so is a window that ends at or before its first frame.
        for bad in ('-DSNAP_GICR_STRIDE=0x10000', '-DSNAP_GICR_STRIDE=0x28000', '-DSNAP_GICR_STRIDE=0'):
            result = compile_variant('-DSNAP_PLATFORM_SP11=1', *five[:3], bad, '-DSNAP_TIMER_INTID=26')
            self.assertNotEqual(result.returncode, 0, bad)
            self.assertIn('redistributor window', result.stderr, bad)
        result = compile_variant('-DSNAP_PLATFORM_SP11=1', five[0], five[1], '-DSNAP_GICR_LIMIT=0x17080000',
                                 *five[3:])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('redistributor window', result.stderr)
        # The two-frame GICv3 stride is a valid explicit choice.
        result = compile_variant('-DSNAP_PLATFORM_SP11=1', *five[:3], '-DSNAP_GICR_STRIDE=0x20000',
                                 '-DSNAP_TIMER_INTID=26')
        self.assertEqual(result.returncode, 0, result.stderr)


# name, text in compat/topology.c that must occur exactly once, replacement, and
# the test groups of tools/test-guest-topology.c and tools/test-guest-topology-gicr.c of
# which at least one must fail.
MUTATIONS = [
    ('pack-order-off-by-one', 'order[i] = (primary_cluster + shift + i) % n;',
     'order[i] = (primary_cluster + shift + i + 1u) % n;', ('sp11-vectors', 'placement-matrix')),
    ('spread-starts-on-primary-cluster', 'shift = policy->order == TOPOLOGY_SPREAD ? 1u : 0u;',
     'shift = 0u;', ('sp11-vectors',)),
    ('primary-core-reused', 'if (cluster == primary_cluster && k == primary_core)\n            continue;',
     'if (0)\n            continue;', ('placement-matrix', 'sp11-vectors')),
    ('fair-lock-ignored-by-place', 'if (out->cluster_span > policy->max_span_unfair && !fair_lock) {',
     'if (out->cluster_span > policy->max_span_unfair && 0) {', ('sp11-vectors', 'placement-matrix')),
    ('oversubscription-off-by-one', 'if (vcpus > topology_profile_cores(profile))\n        return TOPOLOGY_OVERSUBSCRIBED;',
     'if (vcpus > topology_profile_cores(profile) + 1u)\n        return TOPOLOGY_OVERSUBSCRIBED;',
     ('refusals',)),
    ('core-affinity-skewed', 'return cluster->first_affinity + (uint64_t)index * cluster->core_step;',
     'return cluster->first_affinity + (uint64_t)index * cluster->core_step + (index == 4u);',
     ('a16-bit-exact', 'placement-matrix')),
    ('vmpidr-split-moved', '(slot < 4u ? slot', '(slot < 3u ? slot', ('guest-vmpidr',)),
    ('ppi-edge-bit-dropped', 'UINT32_C(1) << ((intid - 16u) * 2u + 1u)', 'UINT32_C(1) << ((intid - 16u) * 2u)',
     ('ppi',)),
    ('check-skips-fair-lock-rule', 'if (span > policy->max_span_unfair && !fair_lock)\n        return TOPOLOGY_NEEDS_FAIR_LOCK;',
     'if (0)\n        return TOPOLOGY_NEEDS_FAIR_LOCK;', ('a16-bit-exact', 'check-placement')),
    ('check-ignores-cluster-index', '|| pl->cluster[i] != c || used[c][k])', '|| used[c][k])',
     ('check-placement',)),
    ('check-accepts-spread-imbalance', 'if (per[c] > per[k] + 1u && per[k] != profile->cluster[k].cores)',
     'if (0 && per[c] > per[k] + 1u && per[k] != profile->cluster[k].cores)', ('check-placement',)),
    ('check-accepts-unfilled-pack-cluster',
     'if (per[(pc + i) % n] != profile->cluster[(pc + i) % n].cores)\n                return TOPOLOGY_BAD_PLACEMENT;',
     'if (0)\n                return TOPOLOGY_BAD_PLACEMENT;', ('check-placement',)),
    ('gicr-last-bit-ignored', 'if (typer & TOPOLOGY_GICR_TYPER_LAST)', 'if (typer & 0u)',
     ('gicr-discovery', 'gicr-faulty/')),
    ('gicr-v4-stride-minimum-lost',
     'minimum = window->gic_version == 4 ? TOPOLOGY_GICR_STRIDE_V4 : TOPOLOGY_GICR_STRIDE_V3;',
     'minimum = TOPOLOGY_GICR_STRIDE_V3;', ('gicr-stride', 'gicr-faulty/explicit-v4-with-v3-stride')),
    ('gicr-duplicate-affinity-accepted',
     'for (uint32_t i = 0; i < map->count; i++)\n            if (map->frame[i].affinity == affinity) {\n                st = TOPOLOGY_GICR_DUPLICATE_AFFINITY;',
     'for (uint32_t i = 0; i < 0u; i++)\n            if (map->frame[i].affinity == affinity) {\n                st = TOPOLOGY_GICR_DUPLICATE_AFFINITY;',
     ('gicr-faulty/duplicate-affinity', 'gicr-faulty/v3-stride-on-v4-image')),
    ('gicr-partial-frame-read', 'if (window->limit - address < stride) {', 'if (0) {',
     ('gicr-faulty/partial-trailing-frame', 'gicr-faulty/address-overflow')),
    ('gicr-all-ones-typer-accepted', 'if (typer == ~UINT64_C(0)) {', 'if (0) {',
     ('gicr-faulty/all-ones-typer',)),
    ('gicr-window-frame-count-loosened', 'span / stride != cores', 'span / stride < cores', ('gicr-window',)),
    ('gicr-affinity-shift', '((affinity >> 32) << 24)', '((affinity >> 32) << 16)',
     ('gicr-conversion', 'gicr-discovery')),
    ('gicr-stale-map-kept', 'fail:\n    gicr_map_clear(map);\n    return st;', 'fail:\n    return st;',
     ('gicr-faulty/', 'gicr-discovery')),
]


class MutationChecks(unittest.TestCase):
    """Each mutant of a COPY of compat/topology.c must make the harness fail in a named group."""
    observed = []

    @classmethod
    def setUpClass(cls):
        cls.dir = BUILD / 'topology-mutation'
        shutil.rmtree(cls.dir, ignore_errors=True)
        cls.dir.mkdir(parents=True)
        cls.flags = ['clang', '-std=c11', '-O1', '-Wall', '-Wextra']
        cls.harness_objects = []
        for source in HARNESS:
            obj = cls.dir / (source.stem + '.o')
            result = run([*cls.flags, '-c', source, '-o', obj])
            if result.returncode:
                raise RuntimeError(result.stderr)
            cls.harness_objects.append(obj)
        cls.source = MODEL.read_text()

    @classmethod
    def tearDownClass(cls):
        for name, how, groups in cls.observed:
            print('mutation %-38s %-8s %s' % (name, how, ', '.join(groups)))

    def build(self, name, text):
        """Compiles the mutant against the shared harness objects; returns the test binary."""
        directory = self.dir / name
        directory.mkdir()
        (directory / 'topology.c').write_text(text)
        for cmd in ([*self.flags, '-c', directory / 'topology.c', '-I', ROOT / 'compat',
                     '-o', directory / 'topology.o'],
                    ['clang', *self.harness_objects, directory / 'topology.o', '-o', directory / 'test']):
            result = run(cmd)
            self.assertEqual(result.returncode, 0, result.stderr)
        return directory / 'test'

    def execute(self, binary):
        """(status, stdout, stderr, failed groups); a crash or hang counts for the running group."""
        env = dict(os.environ, TOPOLOGY_TEST_TRACE='1')
        try:
            result = subprocess.run([str(binary)], cwd=ROOT, capture_output=True, text=True,
                                    timeout=60, env=env)
            status, stdout, stderr = result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired as hung:
            status, stdout = 'timeout', ''
            stderr = hung.stderr.decode() if isinstance(hung.stderr, bytes) else (hung.stderr or '')
        failed = re.findall(r'^GROUP-FAIL (\S+) ', stderr, re.M)
        begun = re.findall(r'^GROUP-BEGIN (\S+)$', stderr, re.M)
        if (status == 'timeout' or status < 0) and begun:
            failed.append(begun[-1])
        return status, stdout, stderr, failed

    def test_unmutated_copy_passes(self):
        status, stdout, stderr, failed = self.execute(self.build('control', self.source))
        self.assertEqual((status, failed), (0, []), stdout + stderr)

    def test_every_mutation_is_caught(self):
        self.assertGreaterEqual(len(MUTATIONS), 5)
        self.assertEqual(len({name for name, *_ in MUTATIONS}), len(MUTATIONS))
        for name, old, new, groups in MUTATIONS:
            with self.subTest(mutation=name):
                self.assertEqual(self.source.count(old), 1, 'anchor for %s must occur once' % name)
                status, _, _, failed = self.execute(self.build(name, self.source.replace(old, new)))
                self.assertNotEqual(status, 0, 'mutation %s survived' % name)
                how = 'timeout' if status == 'timeout' else 'crash' if status < 0 else 'failed'
                self.observed.append((name, how, sorted(set(failed))))
                self.assertTrue(any(g.startswith(prefix) for g in failed for prefix in groups),
                                '%s failed %s, expected one of %s' % (name, failed, groups))


if __name__ == '__main__':
    unittest.main(verbosity=2)
