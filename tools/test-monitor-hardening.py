#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Host tests for the monitor-hardening fixes that followed the independent review of the SP11 monitor groundwork.

  NvmeWindowWiring   the SP11 monitor must not adopt the SSD on the A16 controller re-proof alone
                     (probes/xnuboot/ans_glue.c ans_window_prepare)
  GuestCpuCount      GUEST_CPU_COUNT: the A16 count cannot be overridden, the SP11 fair-lock rule is the topology
                     model's rule (compat/cpu.h, probes/xnuboot/prepare.c), and every site that still hard-codes six
                     follows the count (compat/sptm-root.h, compat/panic-state.h, probes/secondary.h)
  Mutants            the structural checks above fail on each weakened source

The status bands the two refusals use are pinned in tools/test-sp11-integration.py (StatusBands).

    python3 tools/test-monitor-hardening.py [-v] [-k NAME]

Host only: parses sources, preprocesses and compiles small translation units and the monitor object; nothing touches
hardware. Without clang, or without the shared q1n1 checkout the monitor build includes, the classes that need them
report SKIP, never PASS. Evidence: VERIFIED on the host only; the SP11 values behind the units under test are INFERRED
or unknown, and the refusals are never run on a Surface Pro.
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / 'build' / 'monitor-hardening'
UEFI = ROOT.parent / 'q1n1' / 'platform' / 'uefi'
CLANG_DIRS = ('/opt/homebrew/opt/llvm/bin', '/opt/homebrew/bin')
os.environ['PATH'] = os.pathsep.join([*CLANG_DIRS, os.environ.get('PATH', '')])   # test-guest-topology.py runs a bare `clang`
FREESTANDING = ['--target=aarch64-unknown-none-elf', '-ffreestanding']
ANS_GLUE, PREPARE = 'probes/xnuboot/ans_glue.c', 'probes/xnuboot/prepare.c'
PREPARE_HEADER = 'int64_t xnuboot_prepare(void)\n{'      # the declaration above it ends in a semicolon
CLUSTER_CORES = 4                      # TOPOLOGY_SP11_CLUSTER_CORES, compat/topology.h (INFERRED; X2 measures it)
PHYSICAL_CPUS = 12                     # TOPOLOGY_SP11_PHYSICAL_CPUS
SP11_LAUNCHABLE = ('-DSNAP_PLATFORM_SP11=1', '-DSNAP_FAIR_TOKEN=1')    # six vCPUs on SP11 need the queued lock


def find_tool(name):
    return shutil.which(name) or next((str(Path(d) / name) for d in CLANG_DIRS if (Path(d) / name).exists()), None)


def need_clang(test):
    if not find_tool('clang'):
        test.skipTest('clang not found')


def read(relpath, root=ROOT):
    return (root / relpath).read_text(encoding='utf-8')


def need(condition, message):
    if not condition:
        raise AssertionError(message)


def monitor_flags():
    return json.loads(read('config/monitor-flags.json'))['flags']


# --------------------------------------------------------------------------------------------------------------
# Source helpers: a C function and the two sides of its `#if SNAP_PLATFORM_SP11`

def function(text, header):
    """Text of the function whose first line is `header`, up to the closing brace in column 0."""
    need(text.count(header) == 1, f'{header!r} is not defined exactly once')
    start = text.index(header)
    return text[start:text.index('\n}\n', start) + 3]


def branches(text):
    """(sp11, a16) text of the first top-level `#if SNAP_PLATFORM_SP11 ... [#else ...] #endif` in `text`.

    Conditionals nested inside a side stay in it. The a16 side is empty when there is no #else."""
    depth, side, parts = 0, None, {'on': [], 'off': []}
    for line in text.splitlines(keepends=True):
        word = re.match(r'\s*#\s*(if|ifdef|ifndef|else|elif|endif)\b', line)
        if word:
            kind = word.group(1)
            if kind in ('if', 'ifdef', 'ifndef'):
                depth += 1
                if depth == 1 and side is None and re.match(r'\s*#\s*if\s+SNAP_PLATFORM_SP11\s*$', line):
                    side = 'on'
                    continue
            elif kind == 'else' and depth == 1 and side == 'on':
                side = 'off'
                continue
            elif kind == 'endif':
                if depth == 1 and side in ('on', 'off'):
                    break
                depth -= 1
        if side:
            parts[side].append(line)
    need(side is not None, 'no top-level #if SNAP_PLATFORM_SP11 in the text')
    return ''.join(parts['on']), ''.join(parts['off'])


# --------------------------------------------------------------------------------------------------------------
# 1. The SSD adoption entry

# The A16 side of ans_window_prepare after the window guard, as it was before the SP11 branch existed. The A16 monitor
# must keep exactly this text (the firewall proves the ELF; this proves the source did not drift).
A16_ADOPTION = '''#if SNAP_ANS_ASYNC
    /* The schedule's lock-free readers (timer_arm, the fast tick) find the state here; window mode only. */
    c->root_ext->ans=(struct ans_state *)res;
#endif
    return nvmeh_adopt(&nvmeh_direct,ANS_NVMEH_PARAMS(res),ANS_NVMEH_STATE(res),
                       c->params->mem_low,c->params->mem_high);
'''


def check_adoption_entry(text):
    """What probes/xnuboot/ans_glue.c ans_window_prepare must be until nvmeh_sp11_adopt is wired to real inputs."""
    body = function(text, 'static int64_t ans_window_prepare(struct context *c)')
    need('#if SNAP_PLATFORM_SP11' in body,
         'ans_window_prepare has no SP11 side: an SP11 build adopts the SSD on the A16 controller re-proof alone')
    need(body.index('if (!ans_window(c)) return 0;') < body.index('#if SNAP_PLATFORM_SP11'),
         'a run without the SSD window must be untouched: the window guard comes first')
    sp11, a16 = branches(body)
    code = re.sub(r'/\*.*?\*/', '', sp11, flags=re.S)          # a comment may name the function it forbids
    need('nvmeh_adopt' not in code, 'an SP11 build must not adopt the SSD with the A16 controller re-proof alone')
    need(re.search(r'\breturn\s+NVMEH_SP11_STATUS_UNWIRED\s*;', code), 'the SP11 side must refuse with the named status')
    need(re.search(r'->prepare\s*=\s*\(uint64_t\)\(int64_t\)\s*NVMEH_SP11_STATUS_UNWIRED\s*;', code),
         'the refusal must also be recorded in the physical NVMe state, where the host reads it')
    need(not re.search(r'\breturn\s+0\s*;', code), 'the SP11 side must not return success')
    need(a16 == A16_ADOPTION, 'the A16 side of ans_window_prepare changed')


def undefined_symbols(root, name, *defines):
    """Undefined symbols of probes/xnuboot.c compiled as the monitor is (without LTO, so the object stays ELF)."""
    obj = BUILD / f'{name}.o'
    obj.parent.mkdir(parents=True, exist_ok=True)
    obj.unlink(missing_ok=True)
    flags = [f for f in monitor_flags() if f != '-flto']
    built = subprocess.run([find_tool('clang'), *flags, *defines, f'-I{root / "shadow"}', f'-I{UEFI}', '-c',
                            str(root / 'probes' / 'xnuboot.c'), '-o', str(obj)], capture_output=True, text=True,
                           timeout=900)
    need(built.returncode == 0, built.stderr[-3000:])
    listed = subprocess.run([find_tool('llvm-nm'), '--undefined-only', '--format=just-symbols', str(obj)],
                            capture_output=True, text=True, check=True)
    return set(listed.stdout.split())


def copy_monitor(name):
    """A scratch copy of the monitor sources a build of probes/xnuboot.c reads, under build/."""
    dst = BUILD / 'mutants' / name
    shutil.rmtree(dst, ignore_errors=True)
    skip = shutil.ignore_patterns('uefi', '__pycache__', 'build')
    for directory in ('compat', 'shadow', 'config'):
        shutil.copytree(ROOT / directory, dst / directory, ignore=skip)
    shutil.copytree(ROOT / 'probes', dst / 'probes', ignore=skip)
    return dst


class NvmeWindowWiring(unittest.TestCase):
    """The SP11 contract (compat/nvme-host.c) is not enforced if the live adoption path never reaches it."""

    def test_the_sp11_adoption_entry_refuses_with_a_named_status_and_the_a16_side_is_unchanged(self):
        check_adoption_entry(read(ANS_GLUE))

    def test_the_refusal_status_is_a_named_prepare_status_of_the_nvme_window_band(self):
        header = read('compat/nvme-host.h')
        found = re.search(r'#define\s+NVMEH_SP11_STATUS_UNWIRED\s+\((-\d+)\)', header)
        self.assertIsNotNone(found, 'compat/nvme-host.h does not define NVMEH_SP11_STATUS_UNWIRED')
        sp11_block = header[header.index('#if SNAP_PLATFORM_SP11'):]
        self.assertIn('NVMEH_SP11_STATUS_UNWIRED', sp11_block, 'the A16 build must not see the SP11 status')
        self.assertLess(header.index('#if SNAP_PLATFORM_SP11'), header.index('NVMEH_SP11_STATUS_UNWIRED'))

    def test_the_sp11_monitor_object_never_calls_the_a16_adopt_and_the_a16_one_still_does(self):
        need_clang(self)
        if not find_tool('llvm-nm') or not UEFI.is_dir():
            self.skipTest('llvm-nm or the shared q1n1 checkout is absent')
        a16 = undefined_symbols(ROOT, 'xnuboot-a16')
        self.assertIn('nvmeh_adopt', a16, 'the control is meaningless: the A16 object no longer calls nvmeh_adopt')
        # With the fair lock the build is launchable, so the adoption path is live code and cannot be dropped as dead.
        sp11 = undefined_symbols(ROOT, 'xnuboot-sp11', *SP11_LAUNCHABLE)
        self.assertIn('ans_reset', sp11, 'the control is meaningless: prepare is not in the SP11 object')
        self.assertNotIn('nvmeh_adopt', sp11)


# --------------------------------------------------------------------------------------------------------------
# 2. GUEST_CPU_COUNT

def snippet(source, *flags, root=ROOT, werror=True):
    """Syntax-check `source` (a C translation unit read from stdin) the way the monitor is compiled."""
    cmd = [find_tool('clang'), *FREESTANDING, '-fsyntax-only', '-Wall', '-Wextra', *(['-Werror'] if werror else []),
           '-I', str(root), '-I', str(root / 'compat'), *flags, '-x', 'c', '-']
    return subprocess.run(cmd, input=source, capture_output=True, text=True, timeout=300, cwd=root)


def fair_lock_verdict(count, token, root=ROOT):
    """'NEEDS', 'FREE' or 'ABSENT': what compat/cpu.h says about a build of `count` SP11 vCPUs, with or without a token."""
    flags = ['-DSNAP_PLATFORM_SP11=1', f'-DGUEST_CPU_COUNT={count}', *(['-DSNAP_FAIR_TOKEN=1'] if token else [])]
    done = subprocess.run([find_tool('clang'), *FREESTANDING, '-E', '-P', '-I', str(root / 'compat'), *flags, '-x', 'c', '-'],
                          input='#include "cpu.h"\n#ifdef GUEST_CPU_NEEDS_FAIR_LOCK\n#if GUEST_CPU_NEEDS_FAIR_LOCK\n'
                                'NEEDS\n#else\nFREE\n#endif\n#else\nABSENT\n#endif\n',
                          capture_output=True, text=True, timeout=120)
    need(done.returncode == 0, done.stderr[-2000:])
    return next(word for word in done.stdout.split() if word in ('NEEDS', 'FREE', 'ABSENT'))


# The sites that declare six of something per CPU without being able to follow the count. Each assertion sits beside
# its site, so an includer of the header cannot get a mismatched pair; the message names the site.
SITES = (
    ('compat/panic-state.h', ('panic state keeps one entry per guest vCPU',)),
    ('compat/sptm-root.h', ('SPTM_ROOT_CPUS must equal GUEST_CPU_COUNT',)),
    ('probes/secondary.h', ('secondary_config.affinity has one entry per guest vCPU',
                            'secondary_config.redistributor has one entry per guest vCPU')),
)


def check_launch_refusal(text):
    """What probes/xnuboot/prepare.c xnuboot_prepare must do with a build the topology model cannot place."""
    body = function(text, PREPARE_HEADER)
    sp11, a16 = branches(body)
    need(re.search(r'=\s*GUEST_CPU_NEEDS_FAIR_LOCK\s*;', sp11) and
         re.search(r'if\s*\(\s*\w+\s*\)\s*return\s+GUEST_CPU_STATUS_FAIR_LOCK\s*;', sp11),
         'an SP11 build that needs a fair service lock and has none must refuse before preparing anything')
    need(not re.search(r'\breturn\s+0\s*;', sp11), 'the refusal must not return success')
    need(re.search(r'__asm__\s+volatile\s*\(\s*""\s*:\s*"\+r"\(\w+\)\s*\)\s*;', sp11),
         'the refusal must stay a run-time decision: folded to a constant it drops the rest of prepare from the build')
    need(a16 == '', 'the A16 side must be empty')
    need('FAIR_LOCK' not in text.replace(sp11, ''), 'the fair-lock rule leaked outside the SP11 gate')
    need(body.index('#if SNAP_PLATFORM_SP11') < body.index('if (p->device_count > 4'),
         'the refusal must come before the devices, the shadow and stage 2 are built')


class GuestCpuCount(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not find_tool('clang'):
            raise unittest.SkipTest('clang not found')

    # ---- A16: the count is the literal six, and a command-line override is the redefinition it always was ----

    def test_an_a16_build_cannot_override_the_count_and_the_redefinition_is_an_error(self):
        source = '#include "cpu.h"\n_Static_assert(GUEST_CPU_COUNT == 6, "an A16 build has six vCPUs");\n'
        ok = snippet(source)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        same = snippet(source, '-DGUEST_CPU_COUNT=6U')            # an identical redefinition is silent, as it always was
        self.assertEqual(same.returncode, 0, same.stderr)
        for override in ('6', '4U', '7', '13'):
            with self.subTest(override=override):
                refused = snippet(source, f'-DGUEST_CPU_COUNT={override}')
                self.assertNotEqual(refused.returncode, 0, 'an A16 build accepted -DGUEST_CPU_COUNT=' + override)
                self.assertIn('macro redefined', refused.stderr)
                self.assertIn('GUEST_CPU_COUNT', refused.stderr)
                lenient = snippet(source, f'-DGUEST_CPU_COUNT={override}', werror=False)    # the header's six wins
                self.assertEqual(lenient.returncode, 0, lenient.stderr)

    def test_the_a16_text_of_the_edited_headers_does_not_mention_the_count_check(self):
        for relpath, _ in SITES:
            done = subprocess.run([find_tool('clang'), *FREESTANDING, '-E', '-P', '-I', str(ROOT), '-I', str(ROOT / 'compat'),
                                   '-x', 'c', '-'], input=f'#include "{relpath}"\n', capture_output=True, text=True, timeout=120)
            with self.subTest(header=relpath):
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertNotIn('GUEST_CPU_COUNT', done.stdout)

    # ---- SP11: the override is allowed and range-checked, and the fair-lock rule is the topology model's ----

    def test_an_sp11_build_may_override_the_count_inside_the_physical_cores(self):
        source = '#include "cpu.h"\nint probe[GUEST_CPU_COUNT];\n'
        for count in (1, 4, 6, 12):
            with self.subTest(count=count):
                done = snippet(source, '-DSNAP_PLATFORM_SP11=1', f'-DGUEST_CPU_COUNT={count}')
                self.assertEqual(done.returncode, 0, done.stderr)
        for count in (0, 13):
            with self.subTest(count=count):
                done = snippet(source, '-DSNAP_PLATFORM_SP11=1', f'-DGUEST_CPU_COUNT={count}')
                self.assertNotEqual(done.returncode, 0)
                self.assertIn('between 1 and the 12 physical cores', done.stderr)

    def test_the_fair_lock_rule_of_cpu_h_is_the_topology_models_for_every_count(self):
        BUILD.mkdir(parents=True, exist_ok=True)
        spec = importlib.util.spec_from_file_location('hardening_test_guest_topology', ROOT / 'tools' / 'test-guest-topology.py')
        topology = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(topology)
        topology.BUILD = BUILD / 'topology'            # not the path tools/test-guest-topology.py builds: the suite runs both
        status = topology.ST
        for count in range(1, PHYSICAL_CPUS + 1):
            for token in (False, True):
                with self.subTest(count=count, token=token):
                    model, _ = topology.place('sp11', 'pack', 0, count, token)
                    self.assertIn(model, (status['TOPOLOGY_OK'], status['TOPOLOGY_NEEDS_FAIR_LOCK']))
                    wanted = 'NEEDS' if model == status['TOPOLOGY_NEEDS_FAIR_LOCK'] else 'FREE'
                    self.assertEqual(fair_lock_verdict(count, token), wanted)
                    self.assertEqual(model == status['TOPOLOGY_NEEDS_FAIR_LOCK'], count > CLUSTER_CORES and not token)

    def test_the_a16_cpu_header_has_no_fair_lock_rule(self):
        done = subprocess.run([find_tool('clang'), *FREESTANDING, '-E', '-P', '-I', str(ROOT / 'compat'), '-x', 'c', '-'],
                              input='#include "cpu.h"\n', capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn('FAIR_LOCK', done.stdout)

    def test_the_sp11_monitor_refuses_the_launch_the_topology_model_would_refuse(self):
        check_launch_refusal(read(PREPARE))

    def test_the_bare_sp11_object_keeps_the_code_the_compile_check_covers(self):
        """The refusal above must not let the optimizer drop prepare from a build that has no fair lock."""
        if not find_tool('llvm-nm') or not UEFI.is_dir():
            self.skipTest('llvm-nm or the shared q1n1 checkout is absent')
        self.assertIn('ans_reset', undefined_symbols(ROOT, 'xnuboot-sp11-bare', '-DSNAP_PLATFORM_SP11=1'))

    # ---- every site that hard-codes six follows the count ----

    def test_every_hard_coded_six_follows_an_sp11_count(self):
        for relpath, messages in SITES:
            source = f'#include "{relpath}"\n'
            with self.subTest(header=relpath, count=6):
                done = snippet(source, '-DSNAP_PLATFORM_SP11=1', '-DGUEST_CPU_COUNT=6')
                self.assertEqual(done.returncode, 0, done.stderr)
            with self.subTest(header=relpath, count='default'):
                done = snippet(source, '-DSNAP_PLATFORM_SP11=1')
                self.assertEqual(done.returncode, 0, done.stderr)
            for count in (1, 4, 5, 7, 8, 12):
                done = snippet(source, '-DSNAP_PLATFORM_SP11=1', f'-DGUEST_CPU_COUNT={count}')
                for message in messages:
                    with self.subTest(header=relpath, count=count, message=message):
                        self.assertNotEqual(done.returncode, 0, 'a mismatched count was accepted')
                        self.assertIn(message, done.stderr)

    def test_the_a16_headers_still_compile_alone(self):
        for relpath, _ in SITES:
            with self.subTest(header=relpath):
                done = snippet(f'#include "{relpath}"\n')
                self.assertEqual(done.returncode, 0, done.stderr)


# --------------------------------------------------------------------------------------------------------------
# 3. The structural checks must fail on a weakened source

def swap(old, new):
    def mutate(text):
        need(text.count(old) == 1, f'mutation anchor not unique: {old!r}')
        return text.replace(old, new)
    return mutate


A16_CALL = ('return nvmeh_adopt(&nvmeh_direct,ANS_NVMEH_PARAMS(res),ANS_NVMEH_STATE(res),\n'
            '                       c->params->mem_low,c->params->mem_high);')
STATE_WRITE = re.compile(r'^\s*ANS_NVMEH_STATE\(res\)->prepare=\(uint64_t\)\(int64_t\)NVMEH_SP11_STATUS_UNWIRED;\n', re.M)

# name: (file, check, how to weaken it, a fragment of the failure it must cause). The fragment pins WHICH rule catches
# the mutant, so none is killed by an unrelated one.
MUTANTS = {
    'sp11 adoption goes back to the A16 re-proof': (
        ANS_GLUE, check_adoption_entry, swap('return NVMEH_SP11_STATUS_UNWIRED;', A16_CALL), 'A16 controller re-proof alone'),
    'sp11 adoption reports success': (
        ANS_GLUE, check_adoption_entry, swap('return NVMEH_SP11_STATUS_UNWIRED;', 'return 0;'), 'named status'),
    'sp11 refusal forgets the physical NVMe state': (
        ANS_GLUE, check_adoption_entry, lambda t: need(len(STATE_WRITE.findall(t)) == 1, 'anchor') or STATE_WRITE.sub('', t),
        'physical NVMe state'),
    'a16 adoption loses its asynchronous state hook': (
        ANS_GLUE, check_adoption_entry,
        swap('    c->root_ext->ans=(struct ans_state *)res;\n', ''), 'A16 side of ans_window_prepare changed'),
    'window guard moved behind the branch': (
        ANS_GLUE, check_adoption_entry,
        swap('    if (!ans_window(c)) return 0;\n    const struct device_model *m=ans_nvme_model(c);\n'
             '    uint64_t res=m->config[0];\n#if SNAP_PLATFORM_SP11\n',
             '    const struct device_model *m=ans_nvme_model(c);\n    uint64_t res=m->config[0];\n'
             '#if SNAP_PLATFORM_SP11\n    if (!ans_window(c)) return 0;\n'),
        'window guard comes first'),
    'fair-lock refusal removed': (
        PREPARE, check_launch_refusal,
        swap('    if (fair_lock_missing) return GUEST_CPU_STATUS_FAIR_LOCK;\n', ''),
        'must refuse before preparing anything'),
    'fair-lock refusal reports success': (
        PREPARE, check_launch_refusal, swap('return GUEST_CPU_STATUS_FAIR_LOCK;', 'return 0;'), 'must refuse before'),
    'fair-lock refusal folded to a constant': (
        PREPARE, check_launch_refusal, swap('    __asm__ volatile("" : "+r"(fair_lock_missing));\n', ''),
        'must stay a run-time decision'),
    'fair-lock refusal moved after the first preparation': (
        PREPARE, check_launch_refusal, lambda t: move_refusal_after_first_step(t),
        'before the devices, the shadow and stage 2 are built'),
}


def move_refusal_after_first_step(text):
    first = text.index('#if SNAP_PLATFORM_SP11\n', text.index(PREPARE_HEADER))
    last = text.index('#endif\n', first) + len('#endif\n')
    step = '    if (p->device_count > 4 || p->mem_high <= p->mem_low) return -14;\n'
    need(text.startswith(step, last), 'mutation anchor: the first preparation step does not follow the refusal block')
    return text[:first] + step + text[first:last] + text[last + len(step):]


class Mutants(unittest.TestCase):
    def test_each_structural_check_passes_on_the_tree_and_fails_on_its_mutants(self):
        for name, (relpath, check, mutate, fragment) in MUTANTS.items():
            text = read(relpath)
            with self.subTest(mutant=name):
                check(text)
                mutated = mutate(text)
                self.assertNotEqual(mutated, text)
                with self.assertRaises(AssertionError) as caught:
                    check(mutated)
                self.assertIn(fragment, str(caught.exception))
                print(f'mutant {name!r}: caught: {str(caught.exception)[:90]}', file=sys.stderr)

    @unittest.skipUnless(find_tool('clang') and find_tool('llvm-nm') and UEFI.is_dir(), 'clang, llvm-nm and q1n1 are needed')
    def test_the_monitor_object_checks_fail_on_the_mutants_only_a_build_can_show(self):
        root = copy_monitor('sp11-adopt-a16')
        target = root / ANS_GLUE
        target.write_text(swap('return NVMEH_SP11_STATUS_UNWIRED;', A16_CALL)(target.read_text(encoding='utf-8')), encoding='utf-8')
        self.assertIn('nvmeh_adopt', undefined_symbols(root, 'xnuboot-sp11-adopt-a16', *SP11_LAUNCHABLE))
        root = copy_monitor('sp11-folded')
        target = root / PREPARE
        target.write_text(swap('    __asm__ volatile("" : "+r"(fair_lock_missing));\n', '')(target.read_text(encoding='utf-8')),
                          encoding='utf-8')
        self.assertNotIn('ans_reset', undefined_symbols(root, 'xnuboot-sp11-folded', '-DSNAP_PLATFORM_SP11=1'),
                         'the folded refusal should have dropped prepare from the bare SP11 object')


if __name__ == '__main__':
    unittest.main()
