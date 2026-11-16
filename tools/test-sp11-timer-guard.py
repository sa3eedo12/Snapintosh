#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""SP11 timer guards: the CNTHCTL_EL2 read-back and the CNTVOFF_EL2 refusal (compat/sp11-timer-guard.h).

    python3 tools/test-sp11-timer-guard.py [-v] [-k NAME]

Host only: nothing here touches hardware, and every register value is made up. Whether CNTHCTL_EL2 bits 13 and 14
can be set on a Surface Pro 11 and whether they trap EL1 reads is UNKNOWN until the hardware tests T-HW1 and T-HW2
(docs/SP11-MONITOR-PORT.md section 6.4) have run; a read-back guard can only ever catch "does not stick", and this
suite includes the one world it cannot catch (bits that read set and do nothing) so that nobody mistakes it for proof.

  GuardModel   tools/test-sp11-timer-guard.c (address and undefined-behaviour sanitizers) runs the header's own code
               against modelled worlds: the traps stick or one or both do not, CNTVOFF_EL2 is zero or not, both at
               once. Each world states its verdict as a literal; an independent oracle covers the rest. Every
               weakened copy of the header (MUTANTS) must make that program fail, for the rule named beside it.
  Wiring       probes/xnuboot/prepare.c calls the guard once, inside #if SNAP_PLATFORM_SP11, after the last other
               refusal and before the CPU is marked prepared, restores q1n1's saved CNTHCTL_EL2 and returns the
               status; every weakened copy of that hook (HOOK_MUTANTS) must fail the source check. The header
               compiles for aarch64 to exactly two system register reads, one conditional write and an ISB. With a q1n1
               checkout, the A16 monitor object has no CNTVOFF_EL2 access and nothing of the guard in its preprocessed
               text, and the SP11 object has both (the byte-level proof is tools/monitor_baseline.py).

Without clang, llvm-objdump or q1n1 the classes that need them report SKIP, never PASS. Evidence: VERIFIED on the host
only. Scratch files live under build/sp11-timer-guard-test/ and are removed."""
from pathlib import Path
import json
import re
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import monitor_build_offline  # noqa: E402
from source_text import read_source  # noqa: E402

BUILD = ROOT / 'build' / 'sp11-timer-guard-test'
HEADER = ROOT / 'compat/sp11-timer-guard.h'
HARNESS = ROOT / 'tools/test-sp11-timer-guard.c'
PREPARE_C = ROOT / 'probes/xnuboot/prepare.c'
LLVM = Path('/opt/homebrew/opt/llvm/bin')
CLANG = str(LLVM / 'clang') if (LLVM / 'clang').exists() else 'clang'
OBJDUMP = str(LLVM / 'llvm-objdump') if (LLVM / 'llvm-objdump').exists() else 'llvm-objdump'
AARCH64 = ['--target=aarch64-unknown-none-elf', '-ffreestanding']
TOKENS = re.compile(r'snap_sp11_timer|SNAP_SP11_|sp11-timer-guard')


def scratch(prefix):
    BUILD.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(prefix=prefix, dir=BUILD)


def run(command, **kwargs):
    return subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=kwargs.pop('timeout', 600), **kwargs)


def need(condition, message):
    if not condition:
        raise AssertionError(message)


def drop_text(text, old, new=''):
    need(text.count(old) == 1, f'mutation anchor not unique: {old!r}')
    return text.replace(old, new)


# --------------------------------------------------------------------------------------------------------------
# 1. The header's own code against modelled worlds

def build_harness(exe, include_dir, sanitize):
    flags = ['-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror']
    flags += ['-fsanitize=address,undefined', '-fno-sanitize-recover=all'] if sanitize else ['-w']
    done = run(['cc', *flags, '-DSNAP_PLATFORM_SP11=1', f'-I{include_dir}', f'-I{ROOT / "compat"}', str(HARNESS),
                '-o', str(exe)])
    if done.returncode:
        raise AssertionError('compile failed:\n' + done.stderr[-3000:])


# name: (text of the header, its weakened form, a fragment of the first failure it must cause). The fragment names
# WHICH rule catches the weakening, so no mutant is killed by an unrelated one.
MUTANTS = {
    'only EL1TVCT is required': (
        '#define SNAP_SP11_CNTHCTL_TRAPS    (SNAP_SP11_CNTHCTL_EL1TVT | SNAP_SP11_CNTHCTL_EL1TVCT)',
        '#define SNAP_SP11_CNTHCTL_TRAPS    (SNAP_SP11_CNTHCTL_EL1TVCT)', 'SNAP_SP11_CNTHCTL_TRAPS == 8192 + 16384'),
    'EL1TVT at the wrong bit': (
        '(UINT64_C(1) << 13)', '(UINT64_C(1) << 12)', 'SNAP_SP11_CNTHCTL_EL1TVT == 8192'),
    'either trap bit is enough': (
        'if ((cnthctl & SNAP_SP11_CNTHCTL_TRAPS) != SNAP_SP11_CNTHCTL_TRAPS)',
        'if (!(cnthctl & SNAP_SP11_CNTHCTL_TRAPS))', 'CHECK(got == s->want)'),
    'CNTVOFF_EL2 is not checked': (
        '    if (cntvoff != 0)\n', '    if (0)\n', 'CHECK(got == s->want)'),
    'a small CNTVOFF_EL2 is tolerated': (
        '    if (cntvoff != 0)\n', '    if (cntvoff > 0xffff)\n', 'CHECK(got == s->want)'),
    'the two statuses are swapped': (
        '#define SNAP_SP11_TIMER_STATUS_CNTHCTL (-147)', '#define SNAP_SP11_TIMER_STATUS_CNTHCTL (-148)',
        'SNAP_SP11_TIMER_STATUS_CNTHCTL == -147'),
    'a refusal does not restore q1n1\'s CNTHCTL_EL2': (
        '        SNAP_SP11_SYSREG_WRITE_ISB(cnthctl_el2, host_cnthctl);', '        (void)host_cnthctl;',
        'W.cnthctl_writes == 1'),
    'a refusal restores the guest value, not q1n1\'s': (
        'SNAP_SP11_SYSREG_WRITE_ISB(cnthctl_el2, host_cnthctl);', 'SNAP_SP11_SYSREG_WRITE_ISB(cnthctl_el2, cnthctl);',
        'W.written[0] == s->saved'),
    'a launch that goes on still writes CNTHCTL_EL2': (
        '    if (refused)\n        SNAP_SP11_SYSREG_WRITE_ISB', '    if (1)\n        SNAP_SP11_SYSREG_WRITE_ISB',
        'W.cnthctl_writes == 0'),
    'the decision ignores what was read': (
        'refused = snap_sp11_timer_decide(cnthctl, cntvoff);',
        'refused = snap_sp11_timer_decide(SNAP_SP11_CNTHCTL_TRAPS, 0);', 'CHECK(got == s->want)'),
    'the decision uses q1n1\'s value, not the read-back': (
        'refused = snap_sp11_timer_decide(cnthctl, cntvoff);',
        'refused = snap_sp11_timer_decide(host_cnthctl, cntvoff);', 'CHECK(got == s->want)'),
    'what the guard saw is not reported': (
        '    seen->cnthctl = cnthctl;\n', '', 'CHECK(seen.cnthctl == before)'),
    'a refusal reports success': (
        '    return refused;\n}', '    return 0;\n}', 'CHECK(got == s->want)'),
    'CNTVOFF_EL2 is read from CNTHCTL_EL2': (
        'SNAP_SP11_SYSREG_READ(cntvoff_el2, cntvoff);', 'SNAP_SP11_SYSREG_READ(cnthctl_el2, cntvoff);',
        'CHECK(got == s->want)'),
    'the guard programs CNTVOFF_EL2': (
        '    refused = snap_sp11_timer_decide(cnthctl, cntvoff);\n',
        '    refused = snap_sp11_timer_decide(cnthctl, cntvoff);\n    SNAP_SP11_SYSREG_WRITE_ISB(cntvoff_el2, 0);\n',
        'W.cntvoff_writes == 0'),
    'the event number moved': (
        '#define SNAP_SP11_EVENT_TIMER 0x5443', '#define SNAP_SP11_EVENT_TIMER 0x5444', 'SNAP_SP11_EVENT_TIMER == 0x5443'),
}


class GuardModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not subprocess.run(['cc', '--version'], capture_output=True).returncode == 0:
            raise unittest.SkipTest('no C compiler')

    def test_the_headers_own_code_refuses_every_faulty_world(self):
        with scratch('model-') as tmp:
            exe = Path(tmp) / 'guard'
            build_harness(exe, ROOT / 'compat', sanitize=True)
            done = run([str(exe)])
            self.assertEqual(done.returncode, 0, done.stderr[-2000:])
            self.assertTrue(done.stdout.rstrip().endswith('PASS'), done.stdout)
            self.assertIn('18 scenarios', done.stdout)

    def test_every_weakened_header_fails_the_model_for_the_rule_it_breaks(self):
        text = HEADER.read_text()
        for name, (old, new, fragment) in MUTANTS.items():
            with self.subTest(mutant=name), scratch('mutant-') as tmp:
                mutant = drop_text(text, old, new)
                self.assertNotEqual(mutant, text)
                (Path(tmp) / HEADER.name).write_text(mutant)
                exe = Path(tmp) / 'guard'
                build_harness(exe, tmp, sanitize=False)
                done = run([str(exe)])
                self.assertNotEqual(done.returncode, 0, f'{name}: the model still passes')
                self.assertFalse(done.stdout.rstrip().endswith('PASS'), name)
                self.assertIn(fragment, done.stderr, name)
                print(f'mutant {name!r}: killed by {done.stderr.strip().splitlines()[-1].split("/tools/")[-1][:100]}',
                      file=sys.stderr)

    def test_the_documented_limit_is_a_scenario_that_is_not_refused(self):
        """The model includes the one world the guard cannot see; if it were refused the header's comment would be wrong."""
        self.assertRegex(HARNESS.read_text(),
                         r'\{"LIMIT: bits read set, writes ignored \(stuck but inert\)", 0x6003, 0, 0, 0\}')
        header = HEADER.read_text()
        self.assertIn('stuck but inert', header)
        self.assertIn('T-HW2', header)
        self.assertIn('never evidence that the traps work', header)


# --------------------------------------------------------------------------------------------------------------
# 2. Where the monitor calls it

def function(name, text):
    match = re.search(r'^[^\n;]*\b' + re.escape(name) + r'\([^;{]*\)\n\{\n', text, re.M)
    need(match is not None, f'{name} definition missing')
    return text[match.start():text.index('\n}\n', match.end()) + 3]


def guarded_lines(text, macro='SNAP_PLATFORM_SP11'):
    """Per line: is it in the true branch of an #if that names `macro`?"""
    stack, inside = [], []
    for line in text.split('\n'):
        stripped = line.strip()
        if stripped.startswith('#if'):
            stack.append([macro in stripped and not stripped.startswith('#ifndef'), True])
        elif stripped.startswith('#else') and stack:
            stack[-1][1] = False
        elif stripped.startswith('#endif') and stack:
            stack.pop()
        inside.append(any(on and branch for on, branch in stack))
    return inside


def check_hook(text):
    """Everything the guard call in probes/xnuboot/prepare.c must satisfy; raises AssertionError."""
    inside = guarded_lines(text)
    for number, line in enumerate(text.split('\n')):
        if TOKENS.search(line):
            need(inside[number], f'line {number + 1} is outside #if SNAP_PLATFORM_SP11: {line.strip()}')
    prepare = function('vcpu_arch_prepare', text)
    need(text.count('snap_sp11_timer_guard(') == 1 and prepare.count('snap_sp11_timer_guard(') == 1,
         'vcpu_arch_prepare must call the timer guard exactly once')
    call = prepare.index('snap_sp11_timer_guard(')
    need(call > prepare.rindex('return -37;'), 'the guard must come after the last other refusal')
    need(call < prepare.index('vcpu_of(c)->prepared = 1;'), 'the guard must come before the CPU is marked prepared')
    need(re.search(r'int64_t refused = snap_sp11_timer_guard\(vcpu_of\(c\)->save_cnthctl, &timer\);\s*'
                   r'if \(refused\) \{.*?return refused;\s*\}', prepare, re.S),
         'the guard must restore q1n1\'s saved CNTHCTL_EL2 and its status must be returned when non-zero')
    need(re.search(r'record\(c, EV_OTHER, SNAP_SP11_EVENT_TIMER, timer\.cnthctl, timer\.cntvoff,\s*'
                   r'\(uint64_t\)refused \| \(uint64_t\)v->slot << 32\);', prepare),
         'a refusal must leave its EV_OTHER record with both register values')
    need(prepare.count('msr cnthctl_el2') == 1,
         'vcpu_arch_prepare writes CNTHCTL_EL2 once, as before: the guard\'s restore lives in the header')
    need('guest_cnthctl = (vcpu_of(c)->save_cnthctl & ~3UL) | (1UL<<14) | (1UL<<13);' in prepare,
         'the CNTHCTL_EL2 value the model programs is no longer the monitor\'s')


def block(text):
    start = text.index('#if SNAP_PLATFORM_SP11\n    /* The two timer guards read the final state')
    return text[start:text.index('#endif\n', start) + len('#endif\n')]


# name: (how to weaken probes/xnuboot/prepare.c, a fragment of the failure it must cause)
HOOK_MUTANTS = {
    'guard call removed': (lambda t: t.replace(block(t), ''), 'exactly once'),
    'guard status ignored': (lambda t: drop_text(t, '            return refused;\n', '            (void)refused;\n'),
                             'status must be returned'),
    'guard outside the define': (
        lambda t: drop_text(t, '#if SNAP_PLATFORM_SP11\n    /* The two timer guards read the final state',
                            '#if 1\n    /* The two timer guards read the final state'), 'outside #if SNAP_PLATFORM_SP11'),
    'guard before the last refusal': (
        lambda t: drop_text(t.replace(block(t), ''), '    if(SNAP_NATIVE_WRITER_BREAKPOINTS) {\n        /* The instruction inventory',
                            block(t) + '    if(SNAP_NATIVE_WRITER_BREAKPOINTS) {\n        /* The instruction inventory'),
        'after the last other refusal'),
    'guard after the CPU is marked prepared': (
        lambda t: drop_text(t.replace(block(t), ''), '    vcpu_of(c)->prepared = 1;\n',
                            '    vcpu_of(c)->prepared = 1;\n' + block(t)), 'before the CPU is marked prepared'),
    'guard restores the guest value': (
        lambda t: drop_text(t, 'snap_sp11_timer_guard(vcpu_of(c)->save_cnthctl, &timer)',
                            'snap_sp11_timer_guard(vcpu_of(c)->guest_cnthctl, &timer)'), 'saved CNTHCTL_EL2'),
    'refusal leaves no record': (
        lambda t: drop_text(t, '            record(c, EV_OTHER, SNAP_SP11_EVENT_TIMER, timer.cnthctl, timer.cntvoff,\n'
                               '                   (uint64_t)refused | (uint64_t)v->slot << 32);\n'), 'EV_OTHER record'),
    'guard called twice': (
        lambda t: drop_text(t, '#endif\n    vcpu_of(c)->prepared = 1;\n',
                            '    {struct snap_sp11_timer_view again; (void)snap_sp11_timer_guard(0, &again);}\n'
                            '#endif\n    vcpu_of(c)->prepared = 1;\n'), 'exactly once'),
    'monitor writes CNTHCTL_EL2 a second time': (
        lambda t: drop_text(t, '    vcpu_of(c)->prepared = 1;\n',
                            '    __asm__ volatile("msr cnthctl_el2, xzr; isb");\n    vcpu_of(c)->prepared = 1;\n'),
        'writes CNTHCTL_EL2 once'),
    'programmed value changed': (
        lambda t: drop_text(t, '(vcpu_of(c)->save_cnthctl & ~3UL) | (1UL<<14) | (1UL<<13);',
                            '(vcpu_of(c)->save_cnthctl & ~1UL) | (1UL<<14) | (1UL<<13);'), 'no longer the monitor'),
}


class Wiring(unittest.TestCase):
    def test_the_header_is_included_only_inside_the_sp11_gate(self):
        text = (ROOT / 'probes/xnuboot.c').read_text()
        include = '#include "../compat/sp11-timer-guard.h"\n'
        self.assertEqual(text.count(include), 1)
        self.assertTrue(guarded_lines(text[:text.index(include)] + include)[-2])
        unity = read_source(ROOT / 'probes/xnuboot.c')
        self.assertLess(unity.index(include), unity.index('static int64_t vcpu_arch_prepare(struct context *c)\n{'))

    def test_the_prepare_hook_is_where_it_must_be(self):
        check_hook(PREPARE_C.read_text())

    def test_weakened_hooks_fail_the_source_check(self):
        text = PREPARE_C.read_text()
        for name, (mutate, fragment) in HOOK_MUTANTS.items():
            with self.subTest(name):
                mutant = mutate(text)
                self.assertNotEqual(mutant, text)
                with self.assertRaises(AssertionError) as caught:
                    check_hook(mutant)
                self.assertIn(fragment, str(caught.exception))
                print(f'hook mutant {name!r}: {str(caught.exception)[:100]}', file=sys.stderr)

    def probe_object(self, tmp, *define):
        """The guard as the monitor compiles it, in a function of its own; (returncode, stderr, object path)."""
        source = '#include "sp11-timer-guard.h"\nint64_t probe(uint64_t host, struct snap_sp11_timer_view *seen)\n' \
                 '{ return snap_sp11_timer_guard(host, seen); }\n'
        obj = Path(tmp) / 'probe.o'
        done = subprocess.run([CLANG, *AARCH64, '-O1', '-Wall', '-Wextra', '-Werror', *define, '-I', str(ROOT / 'compat'),
                               '-c', '-x', 'c', '-', '-o', str(obj)], input=source, text=True, capture_output=True)
        return done, obj

    def test_the_header_is_empty_without_the_sp11_gate_and_compiles_for_aarch64_with_it(self):
        for define in ((), ('-DSNAP_PLATFORM_SP11=0',)):
            done = run([CLANG, *AARCH64, '-E', '-P', *define, '-I', str(ROOT / 'compat'), '-x', 'c', str(HEADER)])
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(done.stdout.strip(), '', define)
        with scratch('compile-') as tmp:
            done, obj = self.probe_object(tmp, '-DSNAP_PLATFORM_SP11=1')
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertTrue(obj.is_file())
            # Without the gate the types are absent, so a use of the guard is a compile error, not a silent no-op.
            self.assertNotEqual(self.probe_object(tmp)[0].returncode, 0)

    def test_the_guard_compiles_to_two_reads_one_conditional_write_and_an_isb(self):
        if not Path(OBJDUMP).exists():
            self.skipTest('llvm-objdump not found')
        with scratch('asm-') as tmp:
            done, obj = self.probe_object(tmp, '-DSNAP_PLATFORM_SP11=1')
            self.assertEqual(done.returncode, 0, done.stderr)
            listing = subprocess.run([OBJDUMP, '-d', '--no-show-raw-insn', str(obj)], capture_output=True, text=True,
                                     check=True).stdout
        sysregs = sorted(re.sub(r'\s+', ' ', line.split('\t', 1)[1]).strip()
                         for line in listing.splitlines() if re.search(r'\t(mrs|msr)\t', line))
        self.assertEqual(len(sysregs), 3, sysregs)
        self.assertEqual(sum(line.startswith('mrs') and line.endswith('CNTHCTL_EL2') for line in sysregs), 1, sysregs)
        self.assertEqual(sum(line.startswith('mrs') and line.endswith('CNTVOFF_EL2') for line in sysregs), 1, sysregs)
        self.assertEqual([line for line in sysregs if line.startswith('msr')], ['msr CNTHCTL_EL2, x0'], sysregs)
        self.assertEqual(len(re.findall(r'\tisb\n', listing)), 1)

    def test_the_model_programs_the_register_the_way_the_monitor_does(self):
        self.assertIn('(vcpu_of(c)->save_cnthctl & ~3UL) | (1UL<<14) | (1UL<<13)', PREPARE_C.read_text())
        self.assertIn('(saved & ~UINT64_C(3)) | (UINT64_C(1) << 14) | (UINT64_C(1) << 13)', HARNESS.read_text())


def preprocess_monitor(uefi, *extra):
    flags = [f for f in json.loads((ROOT / 'config/monitor-flags.json').read_text())['flags'] if f.startswith('-D')]
    done = run([CLANG, *AARCH64, *flags, *extra, f'-I{ROOT / "shadow"}', f'-I{uefi}', '-E', '-P', 'probes/xnuboot.c'])
    need(done.returncode == 0, done.stderr[-2000:])
    return done.stdout


def monitor_object(uefi, tmp, name, *extra):
    flags = [f for f in json.loads((ROOT / 'config/monitor-flags.json').read_text())['flags'] if f != '-flto']
    obj = Path(tmp) / f'{name}.o'
    done = run([CLANG, *flags, *extra, f'-I{ROOT / "shadow"}', f'-I{uefi}', '-c', 'probes/xnuboot.c', '-o', str(obj)])
    need(done.returncode == 0, done.stderr[-3000:])
    return subprocess.run([OBJDUMP, '-d', '--no-show-raw-insn', str(obj)], capture_output=True, text=True, check=True).stdout


SP11 = ('-DSNAP_PLATFORM_SP11=1', '-DSNAP_FAIR_TOKEN=1', '-DSNAP_GICD_BASE=0x17000000', '-DSNAP_GICR_FIRST=0x17080000',
        '-DSNAP_GICR_LIMIT=0x17380000', '-DSNAP_GICR_STRIDE=0x40000', '-DSNAP_TIMER_INTID=26')


class MonitorObject(unittest.TestCase):
    """The A16 monitor does not contain the guard; the SP11 one does (needs clang, llvm-objdump and a q1n1 checkout)."""

    @classmethod
    def setUpClass(cls):
        cls.uefi, why = monitor_build_offline.locate_q1n1(ROOT)
        if cls.uefi is None:
            raise unittest.SkipTest(f'no q1n1 to link: {why}')
        if not Path(CLANG).exists() or not Path(OBJDUMP).exists():
            raise unittest.SkipTest('clang and llvm-objdump are needed')

    def test_the_a16_text_has_none_of_the_guard_and_the_sp11_text_has_all_of_it(self):
        for extra in ((), ('-DSNAP_PLATFORM_SP11=0',)):
            found = TOKENS.search(preprocess_monitor(self.uefi, *extra))
            self.assertIsNone(found, f'the A16 monitor text {extra} mentions {found and found.group(0)}')
        sp11 = preprocess_monitor(self.uefi, *SP11)
        for token in ('snap_sp11_timer_guard', 'snap_sp11_timer_decide', '0x5443', '"cntvoff_el2"'):
            self.assertTrue(token in sp11, f'{token} is missing from the preprocessed SP11 monitor')

    def test_the_a16_object_never_reads_cntvoff_el2_and_the_sp11_object_does(self):
        with scratch('objects-') as tmp:
            a16 = monitor_object(self.uefi, tmp, 'a16')
            sp11 = monitor_object(self.uefi, tmp, 'sp11', *SP11)
        reads = lambda listing, reg: len(re.findall(r'\tmrs\t[^\n]*\b' + reg + r'\b', listing))
        writes = lambda listing, reg: len(re.findall(r'\tmsr\t' + reg + r'\b', listing))
        self.assertEqual(reads(a16, 'CNTVOFF_EL2'), 0)
        self.assertGreaterEqual(reads(sp11, 'CNTVOFF_EL2'), 1)
        # The guard is inlined into every copy of the function it ends, so "at least one more", not exactly one.
        self.assertGreaterEqual(reads(sp11, 'CNTHCTL_EL2'), reads(a16, 'CNTHCTL_EL2') + 1)
        self.assertGreaterEqual(writes(sp11, 'CNTHCTL_EL2'), writes(a16, 'CNTHCTL_EL2') + 1)
        self.assertEqual(writes(sp11, 'CNTVOFF_EL2'), 0)


if __name__ == '__main__':
    unittest.main()
