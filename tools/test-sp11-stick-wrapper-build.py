#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of what the build of the SP11 stick wrapper leaves behind (docs/SP11-STICK-WRAPPER.md section 9): the build recipe tool, the two
flat binaries and the instructions they contain, the default-off gate, the hygiene of the new files and the document.

    python3 tools/test-sp11-stick-wrapper-build.py

What the wrapper and the reporter DO on the host harness is tools/test-sp11-stick-wrapper.py and tools/test-sp11-stick-wrapper-refusals.py. These
tests need clang, ld.lld, the llvm
tools and a q1n1 checkout (Q1N1_SP11_TREE); without them the tests that build report SKIP, which is not a pass. No hardware, nothing
under /Volumes; everything is written below build/sp11-wrapper-build-test-scratch/ and deleted at the end. EXPERIMENTAL; hardware NOT RUN."""
import ast
import concurrent.futures
import contextlib
import getpass
import io
import json
import os
import re
import shlex
import shutil
import struct
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_wrapper_build as wb  # noqa: E402
import sp11_stick_wrapper_testlib as T  # noqa: E402

ROOT = T.ROOT
SCRATCH = ROOT / 'build' / 'sp11-wrapper-build-test-scratch'
LLVM = ('clang', 'ld.lld', 'llvm-objcopy', 'llvm-readelf', 'llvm-nm', 'llvm-objdump', 'llvm-size')
PROBES = ('sp11-stick-wrapper.c', 'sp11-stick-load.c', 'sp11-stick-report.c', 'sp11-stick-reporter.c')
STATE = {}


def setUpModule():
    shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True)
    STATE['tree'] = T.q1n1_tree()
    STATE['tools'] = all(T.find_tool(name) for name in LLVM)


def tearDownModule():
    shutil.rmtree(SCRATCH, ignore_errors=True)


def require_build():
    if not STATE['tools']:
        raise unittest.SkipTest('clang, ld.lld or an llvm tool was not found')
    if not STATE['tree']:
        raise unittest.SkipTest('no q1n1 checkout (set Q1N1_SP11_TREE): the payloads are built with its recipe')


def profile(name, defines=None, **macros):
    """A monitor profile below the scratch: the five GIC macros of the host tests (some replaced by `macros`) unless `defines` is the list."""
    items = defines if defines is not None else [f'-D{k}={v}' for k, v in {**T.GIC, **macros}.items()]
    return T.write(SCRATCH / 'profiles' / f'{name}.json', json.dumps({'defines': items}).encode())


def run_tool(argv, tree=None):
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.dict(os.environ, {'Q1N1_SP11_TREE': str(tree)} if tree is not None else {}), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = wb.main([str(a) for a in argv])
        except SystemExit as error:         # argparse refusing the arguments
            code = error.code
    return code, out.getvalue(), err.getvalue()


def using(tree):
    return mock.patch.dict(os.environ, {'Q1N1_SP11_TREE': str(tree)})


def refused(test, name, argv, tree=None):
    """The tool refused with this name as the last line of standard output and exit status 1; returns what it said on standard error."""
    code, out, err = run_tool(argv, tree)
    test.assertEqual((code, out.strip().splitlines()[-1:]), (1, [f'REFUSED={name}']), (out, err))
    return err


def fake_tree(name, replace=None):
    """The files of the q1n1 checkout that the build reads, copied below the scratch, some of them replaced by the given text."""
    root = SCRATCH / 'trees' / name
    for rel in wb.Q1N1_INPUTS:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(STATE['tree'] / rel, root / rel)
    for rel, text in (replace or {}).items():
        (root / rel).write_text(text)
    return root


# ---- Reading disassembly: the same helpers judge the real binaries and the seeded-bad ones ---------------------------------------
FORBIDDEN = {'msr', 'tlbi', 'ic', 'smc', 'hvc', 'svc', 'eret', 'eretaa', 'eretab', 'br', 'braa', 'brab', 'braaz', 'brabz', 'brk', 'sys', 'sysl', 'wfi',
             'wfe', 'sev', 'sevl', 'hlt', 'dcps1', 'dcps2', 'dcps3', 'drps'}
SYSTEM_OPERANDS = {'dc': {'cvac', 'civac'}, 'at': {'s1e2w', 's1e2r'}, 'dsb': {'sy'}, 'isb': {''}}
MRS_NAMES = {'CurrentEL', 'MIDR_EL1', 'MPIDR_EL1', 'CNTFRQ_EL0', 'CNTPCT_EL0', 'CTR_EL0', 'HCR_EL2', 'SCTLR_EL2', 'TCR_EL2', 'PAR_EL1'}
LIBC = {'memcpy', 'memset', 'memmove', 'memcmp', 'strlen', 'strcmp', 'strcpy', 'strncpy', 'bcopy', '__memcpy_chk'}


def mnemonic_violations(funcs, reads_registers):
    """Forbidden mnemonics anywhere, and for the instructions that are allowed in the wrapper only their named operands; a reporter
    (reads_registers False) may not even read a system register or do cache or barrier work."""
    found = []
    for name, instructions in funcs.items():
        for addr, mnemonic, operands in instructions:
            if mnemonic in FORBIDDEN:
                found.append(f'{name}+{addr:#x}: {mnemonic} {operands}')
            elif mnemonic in ('dc', 'at', 'dsb', 'isb', 'dmb', 'mrs'):
                first = operands.split(',')[0].strip()
                if not reads_registers:
                    found.append(f'{name}+{addr:#x}: {mnemonic} {operands} (the reporter has no such instruction)')
                elif mnemonic == 'mrs':
                    if operands.split(',')[-1].strip() not in MRS_NAMES:
                        found.append(f'{name}+{addr:#x}: {mnemonic} {operands}')
                elif first not in SYSTEM_OPERANDS.get(mnemonic, set()):
                    found.append(f'{name}+{addr:#x}: {mnemonic} {operands}')
    return found


def words_of(flat):
    return list(struct.unpack(f'<{len(flat) // 4}I', flat[:len(flat) // 4 * 4]))


def funcs_of(**bodies):
    """A listing in llvm-objdump's format for functions given as name=[instruction text], 4 bytes apart from address 0."""
    text = ''
    for name, body in bodies.items():
        text += f'0000000000000000 <{name}>:\n'
        text += ''.join(f'{i * 4:8x}: \t{line.replace(" ", chr(9), 1)}\n' for i, line in enumerate(body))
    return T.parse_functions(text)


class Analysis(unittest.TestCase):
    """The disassembly checks can fail: each is shown to flag the code it is for and to pass the code it is not for."""

    def stores(self, body, through=False):
        return T.store_violations(funcs_of(f=body), through_arguments={'f'} if through else ())

    def test_a_store_through_the_stack_pointer_or_an_address_derived_from_it_is_accepted(self):
        self.assertEqual(self.stores(['str x1, [sp, #8]', 'add x8, sp, #0x10', 'str x1, [x8, #8]', 'mov x9, x8', 'stp x1, x2, [x9]', 'ret']), [])

    def test_a_store_through_an_argument_is_accepted_only_where_arguments_are_allowed(self):
        body = ['mov x19, x0', 'bl 0x1000 <g>', 'str x1, [x19, #8]', 'ret']
        self.assertEqual((len(self.stores(body)), self.stores(body, through=True)), (1, []))

    def test_a_pointer_from_memory_the_runner_table_a_global_or_a_constant_is_never_accepted(self):
        for body in (['ldr x8, [x0, #0x78]', 'str x1, [x8]', 'ret'], ['ldr x8, [x0]', 'str x1, [x8]', 'ret'],
                     ['adrp x8, 0x1000 <g>', 'str x1, [x8]', 'ret'], ['mov x8, #0x1000', 'str x1, [x8]', 'ret']):
            with self.subTest(body=body):
                self.assertEqual(len(self.stores(body, through=True)), 1)

    def test_a_call_forgets_the_caller_saved_registers_and_nothing_else(self):
        self.assertEqual(len(self.stores(['mov x8, x0', 'bl 0x1000 <g>', 'str x1, [x8]', 'ret'], through=True)), 1)
        self.assertEqual(self.stores(['mov x19, x0', 'bl 0x1000 <g>', 'str x1, [x19]', 'ret'], through=True), [])

    def test_a_join_keeps_only_what_both_paths_agree_on(self):
        agree = ['cbz x1, 0x10', 'add x8, sp, #0x8', 'b 0x14', 'nop', 'add x8, sp, #0x20', 'str x1, [x8]', 'ret']
        differ = ['cbz x1, 0x10', 'add x8, sp, #0x8', 'b 0x14', 'nop', 'ldr x8, [x0]', 'str x1, [x8]', 'ret']
        self.assertEqual((self.stores(agree), len(self.stores(differ))), ([], 1))

    def test_a_spilled_argument_is_known_again_when_reloaded_and_another_spilled_pointer_is_not(self):
        spill = ['sub sp, sp, #0x20', 'str x0, [sp, #0x8]', 'bl 0x1000 <g>', 'ldr x8, [sp, #0x8]', 'str w1, [x8]', 'ret']
        other = ['sub sp, sp, #0x20', 'ldr x9, [x1]', 'str x9, [sp, #0x8]', 'ldr x8, [sp, #0x8]', 'str w1, [x8]', 'ret']
        self.assertEqual((self.stores(spill, through=True), len(self.stores(other, through=True))), ([], 1))

    def test_indirect_branches_are_found_unless_they_go_through_the_runner_table(self):
        through = (['ldr x8, [x0, #0x78]', 'blr x8', 'ret'], ['ldr x8, [x0, #0x88]', 'mov x9, x8', 'blr x9', 'ret'])
        for body in through:
            self.assertEqual(T.indirect_branch_violations(funcs_of(f=body)), [], body)
        for body in (['ldr x8, [x0, #0x20]', 'blr x8', 'ret'], ['br x8'], ['blr x8', 'ret'], ['ldr x8, [x0, #0x78]', 'add x8, x8, #8', 'blr x8', 'ret']):
            with self.subTest(body=body):
                self.assertEqual(len(T.indirect_branch_violations(funcs_of(f=body))), 1)

    def test_the_stack_depth_follows_the_call_graph_and_recursion_is_refused(self):
        funcs = funcs_of(payload_entry=['stp x29, x30, [sp, #-0x20]!', 'bl 0x1000 <leaf>', 'ret'], leaf=['sub sp, sp, #0x100', 'ret'])
        self.assertEqual(T.stack_depth(funcs), 0x20 + 0x100)
        with self.assertRaises(AssertionError):
            T.stack_depth(funcs_of(payload_entry=['bl 0x1000 <payload_entry>', 'ret']))

    def test_the_encoding_scan_flags_each_class_of_instruction_the_unit_may_not_run_and_passes_the_ones_it_may(self):
        bad = {'msr ttbr0_el1': 0xD5182000, 'msr daifset': 0xD50342DF, 'mrs sctlr_el1': 0xD5381000, 'tlbi vmalle1': 0xD508871F, 'ic iallu': 0xD508751F,
               'dc zva': 0xD50B7420, 'dc ivac': 0xD5087620, 'at s1e1r': 0xD5087800, 'svc': 0xD4000001, 'hvc': 0xD4000002, 'smc': 0xD4000003,
               'eret': 0xD69F03E0, 'br x8': 0xD61F0100, 'wfi': 0xD503207F, 'dmb ish': 0xD5033BBF}
        good = {'bti c': 0xD503245F, 'nop': 0xD503201F, 'dsb sy': 0xD5033F9F, 'isb': 0xD5033FDF, 'dc cvac, x9': 0xD50B7A29, 'dc civac': 0xD50B7E20,
                'at s1e2w': 0xD50C7820, 'at s1e2r': 0xD50C7800, 'mrs x8, midr_el1': 0xD5380008, 'mrs currentel': 0xD5384240, 'ret': 0xD65F03C0,
                'ldr x0, [x0]': 0xF9400000}
        for name, word in bad.items():
            with self.subTest(name):
                self.assertEqual(len(T.encoding_violations([word], T.ALLOWED_SYSTEM, T.MRS_ALLOWED)), 1)
        for name, word in good.items():
            with self.subTest(name):
                self.assertEqual(T.encoding_violations([word], T.ALLOWED_SYSTEM, T.MRS_ALLOWED), [])
        self.assertEqual(len(T.encoding_violations([0xD5380008], {0xD503245F: 'bti c'}, {})), 1, 'the reporter has no MRS at all')


SEEDED_UNIT = """typedef unsigned long u64;
u64 *global_pointer;
void seeded_store_global(u64 value) { *global_pointer = value; }
void seeded_msr(void) { __asm__ volatile("msr daifset, #2"); }
void seeded_tlbi(void) { __asm__ volatile("tlbi vmalle1"); }
void seeded_hvc(void) { __asm__ volatile("hvc #0"); }
void seeded_branch(void (*target)(void)) { __asm__ volatile("br %0" : : "r"(target)); }
"""


class SeededUnit(unittest.TestCase):
    """A unit compiled with the payload recipe that does what the real ones must not: every check of the Binary tests flags it."""

    @classmethod
    def setUpClass(cls):
        require_build()
        tree, out = STATE['tree'], SCRATCH / 'seeded'
        cflags, _ = wb.recipe_flags(tree)
        T.clang([*cflags, f'-I{tree / "payloads"}', '-c', T.write(out / 'seeded.c', SEEDED_UNIT.encode()), '-o', str(out / 'seeded.o')],
                'compiling the seeded unit')
        done = subprocess.run([T.find_tool('ld.lld'), '-static', '--entry=seeded_msr', '-o', str(out / 'seeded.elf'), str(out / 'seeded.o')],
                              capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
        subprocess.run([T.find_tool('llvm-objcopy'), '-O', 'binary', '--only-section=.text', str(out / 'seeded.elf'), str(out / 'seeded.text')], check=True)
        cls.funcs = T.parse_functions(T.disassemble(out / 'seeded.elf'))
        cls.words = words_of((out / 'seeded.text').read_bytes())

    def test_the_store_check_flags_the_store_through_a_global_pointer_and_only_that(self):
        found = T.store_violations(self.funcs)
        self.assertTrue(found and all(item.startswith('seeded_store_global+') for item in found), found)

    def test_the_encoding_scan_flags_msr_tlbi_hvc_and_br(self):
        found = T.encoding_violations(self.words, T.ALLOWED_SYSTEM, T.MRS_ALLOWED)
        self.assertEqual(sorted(re.sub(r'^word 0x[0-9a-f]+ 0x[0-9a-f]+ is ', '', item) for item in found),
                         ['ERET or an indirect branch BR', 'SVC, HVC, SMC or BRK', 'a SYS instruction (DC, IC, AT, TLBI) that is not allowed', 'an MSR'])

    def test_the_mnemonic_scan_and_the_indirect_branch_check_agree(self):
        found = mnemonic_violations(self.funcs, reads_registers=True)
        self.assertEqual(sorted(item.split(':')[0].split('+')[0] for item in found), ['seeded_branch', 'seeded_hvc', 'seeded_msr', 'seeded_tlbi'])
        self.assertEqual([item.split('+')[0] for item in T.indirect_branch_violations(self.funcs)], ['seeded_branch'])


# ---- The two binaries ---------------------------------------------------------------------------------------------------------
class Binary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_build()
        cls.profile = profile('host')
        cls.dirs = [SCRATCH / 'units' / name for name in ('a', 'b')]
        with using(STATE['tree']):
            cls.reports = [wb.build(str(d), cls.profile) for d in cls.dirs]
        cls.funcs = {stem: T.parse_functions(T.disassemble(cls.dirs[0] / f'{stem}.elf')) for stem in ('snapmon', 'snaprep')}
        cls.flat = {stem: (cls.dirs[0] / f'{stem.upper()}.BIN').read_bytes() for stem in ('snapmon', 'snaprep')}
        cls.sections = {stem: T.sections(cls.dirs[0] / f'{stem}.elf') for stem in ('snapmon', 'snaprep')}

    def each(self, check):
        for stem in ('snapmon', 'snaprep'):
            with self.subTest(binary=stem):
                check(stem)

    def test_two_builds_in_two_directories_are_byte_identical_and_the_report_names_the_digests(self):
        for name in wb.NAMES:
            a, b = [(d / name).read_bytes() for d in self.dirs]
            self.assertEqual(a, b, name)
            self.assertEqual(self.reports[0]['files'][name], {'bytes': len(a), 'sha256': T.sha(a)})
        self.assertEqual(self.reports[0], self.reports[1])
        self.assertEqual(json.loads((self.dirs[0] / wb.REPORT_NAME).read_text()), self.reports[0])

    def test_nothing_of_the_build_host_is_in_a_binary_or_the_report(self):
        host = [str(ROOT), str(STATE['tree']), str(Path.home()), getpass.getuser(), 'Users']
        blobs = {**{name: (self.dirs[0] / name).read_bytes() for name in wb.NAMES}, wb.REPORT_NAME: (self.dirs[0] / wb.REPORT_NAME).read_bytes()}
        for name, data in blobs.items():
            for needle in host:
                if len(needle) > 3:
                    self.assertNotIn(needle.encode(), data, f'{needle!r} in {name}')

    def test_the_reporter_is_under_64_kib_and_each_image_is_its_text_and_rodata_and_nothing_else(self):
        self.assertLess(len(self.flat['snaprep']), wb.REPORTER_MAX)

        def check(stem):
            sec = self.sections[stem]
            self.assertEqual(len(self.flat[stem]), sec['.rodata'][0] + sec['.rodata'][1])
            self.assertEqual(sec['.text'][0], 0)
        self.each(check)

    def test_the_entry_is_bti_then_a_branch_and_payload_entry_follows_it(self):
        def check(stem):
            self.assertEqual(struct.unpack('<2I', self.flat[stem][:8]), (0xD503245F, 0x14000001))
            done = subprocess.run([T.find_tool('llvm-nm'), str(self.dirs[0] / f'{stem}.elf')], capture_output=True, text=True)
            symbols = {line.split()[2]: int(line.split()[0], 16) for line in done.stdout.splitlines() if len(line.split()) == 3}
            self.assertEqual((symbols['_start'], symbols['payload_entry']), (0, 8))
        self.each(check)

    def test_the_code_has_only_the_system_instructions_the_unit_may_run(self):
        def check(stem):
            allowed, mrs = (T.ALLOWED_SYSTEM, T.MRS_ALLOWED) if stem == 'snapmon' else ({0xD503245F: 'bti c'}, {})
            self.assertEqual(T.encoding_violations(T.code_words(self.flat[stem], self.sections[stem]['.text']), allowed, mrs), [])
        self.each(check)

    def test_an_independent_scan_of_the_listing_finds_no_msr_tlbi_ic_exception_or_indirect_branch_and_only_the_named_operands(self):
        self.each(lambda stem: self.assertEqual(mnemonic_violations(self.funcs[stem], stem == 'snapmon'), []))

    def test_the_wrapper_uses_each_instruction_it_is_authorised_for_and_the_reporter_none_of_them(self):
        def used(stem):
            return {mnemonic for instructions in self.funcs[stem].values() for _, mnemonic, _ in instructions}
        self.assertTrue({'dc', 'at', 'dsb', 'isb', 'mrs'} <= used('snapmon'))
        self.assertFalse({'dc', 'at', 'dsb', 'isb', 'dmb', 'mrs'} & used('snaprep'))

    def test_every_indirect_call_goes_through_a_register_loaded_from_the_runner_table(self):
        self.each(lambda stem: self.assertEqual(T.indirect_branch_violations(self.funcs[stem]), []))

    def test_the_reporter_stores_only_to_the_stack(self):
        self.assertEqual(T.store_violations(self.funcs['snaprep']), [])

    def test_the_wrapper_stores_to_the_stack_or_through_a_pointer_argument_and_never_in_the_entry_function(self):
        callees = {name.split('#')[0] for name in self.funcs['snapmon']} - {'payload_entry', '_start'}
        self.assertEqual(T.store_violations(self.funcs['snapmon'], through_arguments=callees), [])

    def test_one_function_stores_to_a_computed_address_and_only_the_report_and_copy_code_calls_it(self):
        funcs = self.funcs['snapmon']
        copies = [name for name in funcs if name.split('#')[0] == 'sp11hw_store64']
        self.assertEqual(len(copies), 2, 'one copy in the report code, one in the loader: a static function of each translation unit')
        for name in copies:
            self.assertEqual([(m, o) for _, m, o in funcs[name]], [('str', 'x1, [x0]'), ('ret', '')], name)
        self.assertEqual(sorted(T.callers_of(funcs, 'sp11hw_store64')), ['sw_copy_all', 'sw_head_flush', 'sw_head_init', 'sw_refuse', 'sw_set_stage'])

    def test_no_data_no_bss_no_got_and_no_libc_function_is_in_or_called_from_an_image(self):
        def check(stem):
            self.assertLessEqual(set(self.sections[stem]), {'NULL', '.text', '.rodata', '.rela.text', '.symtab', '.shstrtab', '.strtab'})
            self.assertFalse(LIBC & {name.split('#')[0] for name in self.funcs[stem]})
            called = {re.search(r'<([^>+]+)', text).group(1) for instructions in self.funcs[stem].values() for _, m, text in instructions
                      if m == 'bl' and re.search(r'<([^>+]+)', text)}
            self.assertFalse(LIBC & called)
            done = subprocess.run([T.find_tool('llvm-nm'), '-u', str(self.dirs[0] / f'{stem}.elf')], capture_output=True, text=True)
            self.assertEqual(done.stdout.strip(), '')
        self.each(check)

    def test_the_stack_the_images_use_is_far_below_what_the_runner_leaves_a_payload(self):
        self.each(lambda stem: self.assertLess(T.stack_depth(self.funcs[stem]), 16 * 1024))

    def test_the_report_says_what_the_build_added_and_that_there_is_no_monitor_and_no_hardware_run(self):
        r = self.reports[0]
        self.assertEqual((r['added'], r['hardware']), (['-DSNAP_PLATFORM_SP11=1', '-DSNAP_SP11_STICK=1'], 'NOT RUN'))
        self.assertTrue(r['monitor'].startswith('none'))
        self.assertEqual(r['gic_macros'], [f'-D{k}={v}' for k, v in T.GIC.items()])
        for flag in ('-O2', '-ffreestanding', '-mgeneral-regs-only', '-fno-pic', '-mbranch-protection=none'):
            self.assertIn(flag, r['cflags'])
        self.assertIn('--emit-relocs', r['ldflags'])
        self.assertFalse([f for f in r['cflags'] if f in ('-MMD', '-MP')], 'the dependency-file flags are left out')

    def test_the_gic_macros_reach_the_wrapper_and_not_the_reporter(self):
        with using(STATE['tree']):
            other = wb.build(str(SCRATCH / 'units' / 'c'), profile('timer27', SNAP_TIMER_INTID='27'))
        self.assertNotEqual(other['files']['SNAPMON.BIN']['sha256'], self.reports[0]['files']['SNAPMON.BIN']['sha256'])
        self.assertEqual(other['files']['SNAPREP.BIN'], self.reports[0]['files']['SNAPREP.BIN'])
        self.assertIn('-DSNAP_TIMER_INTID=27', other['gic_macros'])


class Recipe(unittest.TestCase):
    def test_the_flags_the_tool_reads_are_the_ones_make_computes(self):
        if not shutil.which('make'):
            self.skipTest('make not found')
        if not STATE['tree']:
            self.skipTest('no q1n1 checkout')
        tree = STATE['tree']
        done = subprocess.run(['make', '-s', '-pn', '-f', 'platform/uefi/Makefile'], cwd=tree, capture_output=True, text=True)
        values = {m.group(1): shlex.split(m.group(2)) for m in re.finditer(r'^(PAYLOAD_(?:CFLAGS|LDFLAGS)) :?= (.*)$', done.stdout, re.M)}
        cflags, ldflags = wb.recipe_flags(tree)
        want = [f'-I{tree / f[2:]}' if f.startswith('-I') else f for f in values['PAYLOAD_CFLAGS'] if f not in ('-MMD', '-MP')]
        self.assertEqual((cflags, ldflags), (want, values['PAYLOAD_LDFLAGS']))


# ---- The build recipe tool ----------------------------------------------------------------------------------------------------
class BuildTool(unittest.TestCase):
    def test_the_gic_macros_come_from_a_profile_in_a_fixed_order_and_an_unrelated_define_is_ignored(self):
        path = profile('order', defines=['-DOTHER=1', 5, '-DSNAP_TIMER_INTID=0x1a', '-DSNAP_GICR_STRIDE=0x40000', '-DSNAP_GICR_LIMIT=7',
                                         '-DSNAP_GICR_FIRST=0x17080000', '-DSNAP_GICD_BASE=0X17000000', '-DSP11_X=2'])
        self.assertEqual(wb.gic_defines(path), ['-DSNAP_GICD_BASE=0X17000000', '-DSNAP_GICR_FIRST=0x17080000', '-DSNAP_GICR_LIMIT=7',
                                                '-DSNAP_GICR_STRIDE=0x40000', '-DSNAP_TIMER_INTID=0x1a'])

    def test_a_profile_that_is_not_what_the_build_can_trust_is_refused(self):
        full = [f'-D{k}={v}' for k, v in T.GIC.items()]
        cases = {'missing file': str(SCRATCH / 'profiles' / 'absent.json'), 'not json': T.write(SCRATCH / 'profiles' / 'text.json', b'nope'),
                 'no defines': T.write(SCRATCH / 'profiles' / 'empty.json', b'{}'),
                 'defines not a list': T.write(SCRATCH / 'profiles' / 'str.json', b'{"defines": "-DSNAP_GICD_BASE=1"}'),
                 'a macro twice': profile('twice', defines=[*full, '-DSNAP_GICD_BASE=1']), 'a macro missing': profile('missing', defines=full[1:]),
                 'not a number': profile('suffix', SNAP_GICD_BASE='0x17000000ULL'), 'an expression': profile('expression', SNAP_TIMER_INTID='26+1'),
                 'an injection': profile('injection', SNAP_TIMER_INTID='26 -DSP11_X=1'), 'empty value': profile('blank', SNAP_GICR_STRIDE='')}
        for label, path in cases.items():
            with self.subTest(label), self.assertRaises(wb.Refused) as context:
                wb.gic_defines(path)
            self.assertEqual(context.exception.name, 'PROFILE_BAD', label)

    def test_the_default_profile_is_the_m1_profile_when_it_is_there(self):
        default = ROOT / 'config/monitor-profile-sp11-m1.json'
        if not default.is_file():
            self.skipTest('config/monitor-profile-sp11-m1.json is not in this tree')
        values = {m.group(1): m.group(2) for item in json.loads(default.read_text())['defines'] for m in [re.fullmatch(r'-D(SNAP_\w+)=(.*)', item)] if m}
        self.assertEqual(wb.gic_defines(default), [f'-D{name}={values[name]}' for name in wb.GIC_MACROS])

    def test_an_output_directory_is_refused_unless_it_is_new_or_empty_below_build_and_not_a_link(self):
        (SCRATCH / 'out').mkdir(exist_ok=True)
        real, full, filled = SCRATCH / 'out' / 'real', SCRATCH / 'out' / 'full', SCRATCH / 'out' / 'filled'
        for directory in (real, full, filled):
            directory.mkdir(exist_ok=True)
        (full / 'x').write_bytes(b'x')
        (filled / wb.REPORT_NAME).write_bytes(b'{}')
        link = SCRATCH / 'out' / 'link'
        if not link.exists():
            link.symlink_to(real)
        (SCRATCH / 'out' / 'file').write_bytes(b'x')
        refusals = {'/tmp/sp11-wrapper': 'OUT_DIR_REFUSED', '/Volumes/x': 'OUT_DIR_REFUSED', '/dev/null': 'OUT_DIR_REFUSED',
                    str(ROOT / 'build' / '..' / 'x'): 'OUT_DIR_REFUSED', 'build/../x': 'OUT_DIR_REFUSED', str(ROOT): 'OUT_DIR_REFUSED',
                    str(link): 'OUT_DIR_REFUSED', str(link / 'inside'): 'OUT_DIR_REFUSED', str(full): 'OUT_NOT_EMPTY',
                    str(SCRATCH / 'out' / 'file'): 'OUT_NOT_EMPTY', str(filled): 'OUT_NOT_EMPTY'}
        for out, name in refusals.items():
            with self.subTest(out), self.assertRaises(wb.Refused) as context:
                wb.check_out(out, False)
            self.assertEqual(context.exception.name, name, out)
        with self.assertRaises(wb.Refused) as context:
            wb.check_out(str(full), True)
        self.assertEqual(context.exception.name, 'OUT_NOT_EMPTY', '--force replaces only the output of this tool')
        for ok, force in ((real, False), (SCRATCH / 'out' / 'new', False), (filled, True)):
            self.assertEqual(wb.check_out(str(ok), force), ok)

    def test_a_missing_tool_is_named(self):
        with self.assertRaises(wb.Refused) as context:
            wb.tool('sp11-wrapper-no-such-tool')
        self.assertEqual(context.exception.name, 'TOOL_MISSING')

    def test_a_q1n1_tree_that_is_not_there_or_not_whole_is_refused_and_no_other_tree_is_substituted(self):
        out = str(SCRATCH / 'never')
        for tree in (SCRATCH / 'no-such-tree', ''):
            refused(self, 'Q1N1_TREE_ABSENT', ['--out', out], tree=tree)
        self.assertFalse(Path(out).exists())
        if STATE['tree']:
            partial = fake_tree('partial')
            (partial / 'payloads/payload.ld').unlink()
            refused(self, 'Q1N1_TREE_ABSENT', ['--out', out], tree=partial)

    def test_the_cli_refuses_with_a_name_on_the_last_line_and_a_build_ends_with_hardware_not_run(self):
        require_build()
        refused(self, 'PROFILE_BAD', ['--out', SCRATCH / 'cli', '--profile', SCRATCH / 'profiles' / 'absent.json'], tree=STATE['tree'])
        self.assertFalse((SCRATCH / 'cli').exists(), 'a refused profile leaves no output directory')
        code, out, err = run_tool(['--out', SCRATCH / 'cli', '--profile', profile('cli')], tree=STATE['tree'])
        self.assertEqual(code, 0, err)
        lines = out.strip().splitlines()
        self.assertEqual(lines[-2:], ['MONITOR=NONE (the wrapper never enters one)', 'HARDWARE=NOT RUN'])
        self.assertRegex(lines[0], r'^SNAPMON\.BIN \d+ bytes sha256 [0-9a-f]{64}$')
        self.assertRegex(lines[1], r'^SNAPREP\.BIN \d+ bytes sha256 [0-9a-f]{64}$')
        refused(self, 'OUT_NOT_EMPTY', ['--out', SCRATCH / 'cli', '--profile', profile('cli')], tree=STATE['tree'])
        code, again, err = run_tool(['--out', SCRATCH / 'cli', '--profile', profile('cli'), '--force'], tree=STATE['tree'])
        self.assertEqual((code, again), (0, out), 'a forced rebuild of the same inputs gives the same bytes and the same report')

    def test_a_recipe_the_tool_cannot_read_is_refused_and_one_that_changed_changes_the_bytes_and_the_named_digest(self):
        require_build()
        text = (STATE['tree'] / 'platform/uefi/Makefile').read_text()
        out = SCRATCH / 'recipe'
        broken = {'a make variable': 'PAYLOAD_CFLAGS := $(OTHER) -O2\nPAYLOAD_LDFLAGS := --emit-relocs\n',
                  'no ldflags': 'PAYLOAD_CFLAGS := --target=aarch64-unknown-none-elf -ffreestanding -fno-pic -mgeneral-regs-only\n',
                  'no emit-relocs': 'PAYLOAD_CFLAGS := --target=aarch64-unknown-none-elf -ffreestanding -fno-pic -mgeneral-regs-only\n'
                                    'PAYLOAD_LDFLAGS := -static\n',
                  'no PIC flags': 'PAYLOAD_CFLAGS := --target=aarch64-unknown-none-elf -O2\nPAYLOAD_LDFLAGS := --emit-relocs\n'}
        for label, makefile in broken.items():
            with self.subTest(label):
                refused(self, 'RECIPE_CHANGED', ['--out', out / label.replace(' ', '-'), '--profile', profile('recipe')],
                        tree=fake_tree(label.replace(' ', '-'), {'platform/uefi/Makefile': makefile}))
        with using(STATE['tree']):
            base = wb.build(str(SCRATCH / 'recipe-base'), profile('recipe'))
        optimised_for_size = re.sub(r'(PAYLOAD_CFLAGS := [^\n]*?)-O2', r'\1-Os', text, count=1)
        self.assertNotEqual(optimised_for_size, text, 'the Makefile of this checkout has an -O2 in PAYLOAD_CFLAGS to change')
        with using(fake_tree('os', {'platform/uefi/Makefile': optimised_for_size})):
            changed = wb.build(str(SCRATCH / 'recipe-os'), profile('recipe'))
        self.assertNotEqual(changed['files']['SNAPMON.BIN']['sha256'], base['files']['SNAPMON.BIN']['sha256'])
        self.assertNotEqual(changed['q1n1_inputs']['platform/uefi/Makefile'], base['q1n1_inputs']['platform/uefi/Makefile'])
        self.assertIn('-Os', changed['cflags'])

    def test_each_way_a_build_can_go_wrong_is_refused_with_its_own_name(self):
        require_build()
        sha = (STATE['tree'] / 'platform/uefi/sha256.c').read_text()
        weak = '\n__attribute__((weak)) void *memcpy(void *, const void *, unsigned long);\n'
        called = sha + weak + 'void leak(void *d, const void *s, unsigned long n) { memcpy(d, s, n); }\n'
        addressed = sha + weak + 'void *leak(void) { return memcpy ? (void *)memcpy : 0; }\n'
        cases = {'BUILD_FAILED': {'payloads/start.S': 'this is not assembly\n'},
                 'ELF_REFUSED': {'tools/sp11_payload_elfcheck.py': 'import sys\nprint("seeded refusal", file=sys.stderr)\nsys.exit(1)\n'},
                 'UNDEFINED_SYMBOL': {'platform/uefi/sha256.c': called}}
        for name, replace in cases.items():
            with self.subTest(name):
                err = refused(self, name, ['--out', SCRATCH / 'wrong' / name.lower(), '--profile', profile('wrong')],
                              tree=fake_tree(name.lower(), replace))
                self.assertTrue(err.strip(), 'the reason is on standard error')
        with self.subTest('a weak reference taken through the GOT is refused by q1n1 own ELF check before the undefined-symbol check'):
            err = refused(self, 'ELF_REFUSED', ['--out', SCRATCH / 'wrong' / 'got', '--profile', profile('wrong')],
                          tree=fake_tree('got', {'platform/uefi/sha256.c': addressed}))
            self.assertIn('.got needs a loader', err)
        with mock.patch.object(wb, 'REPORTER_MAX', 1000):
            refused(self, 'REPORTER_TOO_BIG', ['--out', SCRATCH / 'wrong' / 'big', '--profile', profile('wrong')], tree=STATE['tree'])


# ---- The gate -----------------------------------------------------------------------------------------------------------------
class Gate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_build()
        tree = STATE['tree']
        cflags, _ = wb.recipe_flags(tree)
        cls.base = [T.find_tool('clang'), *cflags, f'-I{tree / "payloads"}']
        cls.gic = [f'-D{k}={v}' for k, v in T.GIC.items()]
        cls.on = ['-DSNAP_PLATFORM_SP11=1', '-DSNAP_SP11_STICK=1']

    def cc(self, source, flags):
        """(exit status, standard error, `llvm-nm` of the object) of compiling one source with the payload recipe and these flags."""
        out = SCRATCH / 'gate' / f'{T.sha(repr((source, tuple(flags))).encode())[:20]}.o'
        out.parent.mkdir(exist_ok=True)
        done = subprocess.run([*self.base, *flags, '-c', str(ROOT / 'probes' / source), '-o', str(out)], capture_output=True, text=True)
        symbols = subprocess.run([T.find_tool('llvm-nm'), str(out)], capture_output=True, text=True).stdout if done.returncode == 0 else None
        return done.returncode, done.stderr, symbols

    def many(self, jobs):
        with concurrent.futures.ThreadPoolExecutor(4) as pool:
            return dict(zip(jobs, pool.map(lambda job: self.cc(*job[:1], list(job[1])), jobs)))

    def test_with_the_gate_off_every_source_compiles_to_an_object_with_nothing_in_it(self):
        off = {'no definitions': (), 'platform only': ('-DSNAP_PLATFORM_SP11=1',), 'gate 0': ('-DSNAP_PLATFORM_SP11=1', '-DSNAP_SP11_STICK=0'),
               'gate 0 without the platform': ('-DSNAP_SP11_STICK=0',)}
        jobs = [(source, flags) for source in PROBES for flags in off.values()]
        for (source, flags), (code, err, symbols) in self.many(jobs).items():
            with self.subTest(source=source, flags=flags):
                self.assertEqual((code, symbols), (0, ''), err)

    def test_a_gate_value_other_than_0_or_1_and_a_gate_without_the_platform_do_not_compile_and_say_why(self):
        bad = {'two': (('-DSNAP_PLATFORM_SP11=1', '-DSNAP_SP11_STICK=2', *self.gic), 'accepts only 0 and 1'),
               'empty': (('-DSNAP_PLATFORM_SP11=1', '-DSNAP_SP11_STICK=', *self.gic), ''),
               'no platform': (('-DSNAP_SP11_STICK=1', *self.gic), 'needs SNAP_PLATFORM_SP11=1'),
               'platform 0': (('-DSNAP_PLATFORM_SP11=0', '-DSNAP_SP11_STICK=1', *self.gic), 'needs SNAP_PLATFORM_SP11=1')}
        fragments = {flags: fragment for flags, fragment in bad.values()}
        for (source, flags), (code, err, _) in self.many([(s, flags) for s in PROBES for flags in fragments]).items():
            with self.subTest(source=source, flags=flags):
                self.assertEqual(code, 1)
                self.assertIn(fragments[flags], err)

    def test_the_wrapper_needs_the_five_gic_macros_and_the_reporter_does_not(self):
        jobs = [(source, tuple([*self.on, *self.gic[:k], *self.gic[k + 1:]])) for source in PROBES for k in range(5)]
        jobs += [(source, tuple(self.on)) for source in PROBES]
        for (source, flags), (code, err, _) in self.many(jobs).items():
            with self.subTest(source=source, flags=flags):
                if source == 'sp11-stick-reporter.c':
                    self.assertEqual(code, 0, err)
                else:
                    self.assertEqual(code, 1)
                    self.assertIn('needs the five GIC macros', err)

    def test_the_gate_on_with_every_macro_compiles_and_the_reporter_ignores_the_wrapper_options(self):
        jobs = [(source, tuple([*self.on, *self.gic])) for source in PROBES]
        jobs += [('sp11-stick-reporter.c', tuple([*self.on, '-DSP11_STICK_HASH_POLICY=9', '-DSP11_STICK_SEED_NO_AT']))]
        for (source, flags), (code, err, symbols) in self.many(jobs).items():
            with self.subTest(source=source, flags=flags):
                self.assertEqual(code, 0, err)
                self.assertTrue(symbols.strip())

    def test_the_hash_policy_accepts_the_two_bits_and_nothing_else_and_a_seeded_fault_is_a_host_test_option(self):
        jobs = {(source, tuple([*self.on, *self.gic, f'-DSP11_STICK_HASH_POLICY={value}'])): value for source in PROBES[:3] for value in (0, 1, 2, 3, 4, 7)}
        for (source, flags), (code, err, _) in self.many(list(jobs)).items():
            with self.subTest(source=source, flags=flags):
                self.assertEqual(code == 0, jobs[(source, flags)] in (0, 1, 2, 3), err)
        for source in PROBES[:3]:
            code, err, _ = self.cc(source, [*self.on, *self.gic, '-DSP11_STICK_SEED_NO_AT'])
            self.assertEqual(code, 1)
            self.assertIn('host test build only', err)


# ---- Hygiene ------------------------------------------------------------------------------------------------------------------
def own_files():
    found = [*(ROOT / 'probes').glob('sp11-stick-*.[ch]'), *(ROOT / 'tools').glob('sp11_stick_wrapper*.py'),
             *(ROOT / 'tools').glob('test-sp11-stick-wrapper*'), ROOT / 'docs/SP11-STICK-WRAPPER.md']
    return sorted(p for p in found if p.name != 'sp11-stick-bundle.h' and p.is_file())


class Hygiene(unittest.TestCase):
    def test_the_files_are_the_ones_this_work_item_adds(self):
        names = {p.name for p in own_files()}
        for wanted in ('sp11-stick-hw.h', 'sp11-stick-common.h', 'sp11-stick-wrapper.h', 'sp11-stick-wrapper.c', 'sp11-stick-load.c',
                       'sp11-stick-report.c', 'sp11-stick-reporter.c', 'sp11_stick_wrapper_build.py', 'sp11_stick_wrapper_testlib.py',
                       'sp11_stick_wrapper_checks.py', 'test-sp11-stick-wrapper.c', 'test-sp11-stick-wrapper.py',
                       'test-sp11-stick-wrapper-refusals.py', 'test-sp11-stick-wrapper-build.py', 'test-sp11-stick-wrapper-qemu.py', 'SP11-STICK-WRAPPER.md'):
            self.assertIn(wanted, names)

    def test_every_source_file_starts_with_an_spdx_mit_line(self):
        for path in own_files():
            if path.suffix != '.md':
                with self.subTest(path.name):
                    self.assertIn('SPDX-License-Identifier: MIT', '\n'.join(path.read_text().splitlines()[:3]))

    def test_every_c_and_python_file_stays_under_1500_lines_and_50_kib(self):
        for path in own_files():
            if path.suffix in ('.c', '.h', '.py'):
                with self.subTest(path.name):
                    text = path.read_text()
                    self.assertLess(len(text.splitlines()), 1500)
                    self.assertLess(len(text.encode()), 50 * 1024)

    def test_no_file_names_the_build_host_a_person_a_network_a_key_or_an_unfinished_marker(self):
        user = getpass.getuser()
        # the marker words are written in pieces so that this file does not contain them
        markers = r'\b(?:TO' r'DO|FIX' r'ME|X' r'XX|HA' r'CK|W' r'IP)\b'
        patterns = {'a home directory': r'/(?:Users|home)/[A-Za-z0-9._-]+', 'a MAC address': r'\b[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}\b',
                    'a private address': r'\b(?:192\.168|10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b', 'a key': r'BEGIN (?:OPENSSH|RSA|PRIVATE)|ssh-(?:rsa|ed25519)',
                    'a credential': r'(?i)\b(?:password|passwd|secret|token)\s*[=:]\s*\S', 'an unfinished marker': markers,
                    'a session tag': r'/\*\s*[A-Z]\d+:'}
        if len(user) > 3:
            patterns['the user name'] = re.escape(user)
        for path in own_files():
            text = path.read_text()
            for label, pattern in patterns.items():
                found = re.search(pattern, text)
                self.assertIsNone(found, f'{path.name}:{text.count(chr(10), 0, found.start()) + 1 if found else 0} has {label}: {found.group(0) if found else ""!r}')

    def test_every_python_file_parses_and_starts_with_a_docstring(self):
        for path in own_files():
            if path.suffix == '.py':
                with self.subTest(path.name):
                    self.assertTrue(ast.get_docstring(ast.parse(path.read_text(), str(path))), 'a module docstring comes first')

    def test_no_existing_monitor_source_or_build_script_includes_a_new_header_or_the_gate(self):
        needles = ('sp11-stick-hw.h', 'sp11-stick-common.h', 'sp11-stick-wrapper.h', 'SNAP_SP11_STICK', 'SP11_STICK_HOST_TEST', 'sp11-stick-wrapper')
        mine = {p.resolve() for p in own_files()}
        scanned = []
        for sub in ('compat', 'shadow', 'probes'):
            scanned += [p for p in (ROOT / sub).rglob('*') if p.is_file() and p.suffix in ('.c', '.h', '.S', '.py', '.mk', '.json', '.ld', '') and p.resolve() not in mine]
        scanned += [p for p in [*(ROOT / 'tools').glob('cycle*.py'), *(ROOT / 'tools/cycle_lib').glob('*'), ROOT / 'Makefile'] if p.is_file()]
        self.assertTrue(scanned)
        for path in scanned:
            try:
                text = path.read_text()
            except UnicodeDecodeError:
                continue
            for needle in needles:
                self.assertNotIn(needle, text, f'{path.relative_to(ROOT)} mentions {needle}: the monitor build must not know the stick wrapper')


DOC = ROOT / 'docs/SP11-STICK-WRAPPER.md'


class Document(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = DOC.read_bytes()
        cls.text = cls.data.decode('ascii')

    def test_the_note_is_ascii_and_within_its_size_budget(self):
        self.assertLessEqual(len(self.data), 30000)

    def test_it_says_hardware_not_run_uses_the_labels_and_claims_no_hardware_result(self):
        for needle in ('hardware NOT RUN', 'EXPERIMENTAL', 'VERIFIED', 'OBSERVED', 'INFERRED', 'UNIMPLEMENTED', 'UNKNOWN'):
            self.assertIn(needle, self.text)
        self.assertNotRegex(self.text, r'(?i)hardware (?:pass|qualified|validated|ok)\b')

    def test_every_stage_the_wrapper_writes_is_named_and_the_three_it_never_writes_are_said_not_to_be_written(self):
        for name in T.sb.STAGES:
            self.assertRegex(self.text, rf'\b{name}\b')
        self.assertIn('PREPARED, MONITOR_ENTERED and MONITOR_RETURNED are never written', self.text.replace('\n', ' '))

    def test_every_refusal_the_wrapper_can_raise_is_named_or_covered_by_its_family(self):
        wrapper_side = [name for _, name, sides in T.sb.ERRORS if sides & 2]
        for name in wrapper_side:
            family = name.split('_')[0] + '_*'
            self.assertTrue(re.search(rf'\b{name}\b', self.text) or family in self.text, f'{name} is neither named nor covered by {family}')
        for name in ('HASH_POLICY', 'GIC_NOT_READY'):
            self.assertIn(name, self.text)

    def test_the_build_refusals_the_note_lists_are_the_tool_s(self):
        for name in wb.REFUSALS:
            self.assertIn(f'`{name}`', self.text)

    def test_the_numbers_it_states_are_the_interface_s(self):
        self.assertEqual((T.sb.WRAPPER_LINES, T.sb.REPORTER_LINES, T.sb.REPORT_HEAD_BYTES // 8, T.sb.RET_REFUSED), (30, 40, 37, 0x1000))
        for fragment in ('of the 30 allowed', '(the limit is 40)', '37 words', '`0x1000 + code`', 'at most 9 are printed'):
            self.assertIn(fragment, self.text)

    def test_the_files_and_commands_it_names_exist_and_its_links_resolve(self):
        for rel in set(re.findall(r'`((?:probes|tools|docs|config)/[A-Za-z0-9_.-]+)`', self.text)):
            self.assertTrue((ROOT / rel).exists(), rel)
        for target in re.findall(r'\]\(([^)#]+)(?:#[^)]*)?\)', self.text):
            self.assertTrue((DOC.parent / target).exists(), target)

    def test_the_note_names_every_source_and_test_file_of_the_work_item(self):
        for path in own_files():
            if path.suffix in ('.c', '.h', '.py') and not path.name.startswith('sp11-stick-gic'):      # the GIC variant's files are named in SP11-STICK-GIC.md
                self.assertIn(f'{path.parent.name}/{path.name}', self.text, f'{path.name} is not named in the note')

    def test_the_stack_depth_it_states_is_what_the_call_graph_gives(self):
        require_build()
        with using(STATE['tree']):
            wb.build(str(SCRATCH / 'units' / 'doc'), profile('doc'))
        funcs = T.parse_functions(T.disassemble(SCRATCH / 'units' / 'doc' / 'snapmon.elf'))
        stated = re.search(r'deepest stack use by the call graph is (\d+) bytes', self.text.replace('\n', ' '))
        self.assertTrue(stated)
        self.assertEqual(int(stated.group(1)), T.stack_depth(funcs))


if __name__ == '__main__':
    unittest.main()
