#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of the binaries and the gate of the SP11 GIC and stop variant (docs/SP11-STICK-GIC.md section 8), monitor port work item 8: that the
default (gate off) dry-run wrapper is still the binary that ran on the unit, that the variant builds twice to the same bytes, that its
instruction census lists exactly the system registers it writes and nothing else (no SMC, HVC, SVC, TLBI, IC, WFI or BR; one ERET), what each
register's code is and who may call it, how the vector table lies in the image, the opt-in gate, and the hygiene of the files this work item adds.

    python3 tools/test-sp11-stick-gic-build.py

These tests need clang, ld.lld, the llvm tools and a q1n1 checkout (Q1N1_SP11_TREE); without them the tests that build report SKIP, which is
not a pass. The census is read twice, from the disassembler's listing and from the code words by their bits, and the two must agree. What the
host tests do (tools/test-sp11-stick-gic.py) and what QEMU shows (tools/test-sp11-stick-gic-qemu.py) are not repeated here. No hardware,
nothing under /Volumes; everything is written below build/sp11-gic-build-test-scratch/ and deleted at the end. EXPERIMENTAL; hardware NOT RUN."""
import ast
import collections
import concurrent.futures
import getpass
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_wrapper_build as wb  # noqa: E402
import sp11_stick_gic_testlib as G  # noqa: E402
import sp11_stick_wrapper_testlib as T  # noqa: E402

ROOT = T.ROOT
SCRATCH = ROOT / 'build' / 'sp11-gic-build-test-scratch'
LLVM = ('clang', 'ld.lld', 'llvm-objcopy', 'llvm-readelf', 'llvm-nm', 'llvm-objdump', 'llvm-size')
STATE = {}
# The dry-run wrapper and reporter that ran on the unit (docs/SP11-MONITOR-PORT.md D7: 35,164 B 6137984f and 21,076 B 940c774f); the gate off must
# rebuild them byte for byte. A different digest here is a different binary and so a different authorisation, whoever changed it and why.
UNIT_RUN = {'SNAPMON.BIN': (35164, '6137984fbc61a48845e1ca7283911c6a87a4955802eebdb58ef941660bed2d15'),
            'SNAPREP.BIN': (21076, '940c774f1377980f378929400571ed223f00c5fb87a054fbcd2a092f3c0b9aab')}
GIC_SOURCES = ('sp11-stick-gic.c', 'sp11-stick-gic-dry.c', 'sp11-stick-gic-vec.c')
DRY_RUN_SOURCES = ('sp11-stick-wrapper.c', 'sp11-stick-load.c', 'sp11-stick-report.c', 'sp11-stick-reporter.c')
# The census of the variant: the system registers it writes, where each write is, and the ones it reads.
WRITES = {'VBAR_EL2', 'DAIFSet', 'DAIFClr', 'ICC_PMR_EL1', 'ICC_BPR1_EL1', 'ICC_IGRPEN1_EL1', 'ICC_EOIR1_EL1', 'ICC_DIR_EL1', 'CNTHP_CVAL_EL2',
          'CNTHP_CTL_EL2', 'SPSR_EL2', 'ELR_EL2', 'HCR_EL2', 'VBAR_EL1', 'SCTLR_EL1'}
EXCURSION = {'sw_gic_excursion', 'sw_gic_excursion_exit'}
WHERE = {'ICC_PMR_EL1': {'sp11hw_write_icc_pmr_el1'}, 'ICC_BPR1_EL1': {'sp11hw_write_icc_bpr1_el1'}, 'ICC_IGRPEN1_EL1': {'sp11hw_write_icc_igrpen1_el1'},
         'ICC_EOIR1_EL1': {'sp11hw_write_icc_eoir1_el1'}, 'ICC_DIR_EL1': {'sp11hw_write_icc_dir_el1'}, 'CNTHP_CVAL_EL2': {'sp11hw_write_cnthp_cval_el2'},
         'CNTHP_CTL_EL2': {'sp11hw_write_cnthp_ctl_el2'}, 'VBAR_EL2': {'sp11hw_write_vbar_el2', 'sw_gic_window', 'sw_gic_window_exit', *EXCURSION},
         'DAIFSet': {'sp11hw_irq_mask', 'sw_gic_window'}, 'DAIFClr': {'sw_gic_window'}, 'SPSR_EL2': {'sw_gic_vec_common', *EXCURSION},
         'ELR_EL2': {'sw_gic_vec_common', *EXCURSION}, 'HCR_EL2': {'sp11hw_write_hcr_el2'}, 'VBAR_EL1': {'sp11hw_write_vbar_el1'},
         'SCTLR_EL1': {'sp11hw_write_sctlr_el1'}}
# How many of each write there are: 26 MSR instructions of 15 registers (the dry run's pass 1 had 15 of 12).
COUNTS = {'VBAR_EL2': 5, 'DAIFSet': 2, 'DAIFClr': 1, 'ICC_PMR_EL1': 1, 'ICC_BPR1_EL1': 1, 'ICC_IGRPEN1_EL1': 1, 'ICC_EOIR1_EL1': 1, 'ICC_DIR_EL1': 1,
          'CNTHP_CVAL_EL2': 1, 'CNTHP_CTL_EL2': 1, 'SPSR_EL2': 4, 'ELR_EL2': 4, 'HCR_EL2': 1, 'VBAR_EL1': 1, 'SCTLR_EL1': 1}
# The survey's four reads of the EL1 timers at EL2 (pass 5): MRS only, in the dry run's build only, one function each (defined in sp11-stick-gic-dry.c).
SURVEY_TIMERS = {'CNTV_CTL_EL0', 'CNTV_CVAL_EL0', 'CNTP_CTL_EL0', 'CNTP_CVAL_EL0'}
READS = {'CurrentEL', 'MIDR_EL1', 'MPIDR_EL1', 'CNTFRQ_EL0', 'CNTPCT_EL0', 'CTR_EL0', 'HCR_EL2', 'SCTLR_EL2', 'TCR_EL2', 'PAR_EL1', 'SPSel', 'DAIF',
         'VBAR_EL2', 'ICC_SRE_EL2', 'ICC_CTLR_EL1', 'ICC_PMR_EL1', 'ICC_BPR1_EL1', 'ICC_IGRPEN1_EL1', 'ICC_HPPIR1_EL1', 'ICC_RPR_EL1', 'ICC_IAR1_EL1',
         'CNTHP_CTL_EL2', 'CNTHP_CVAL_EL2', 'ESR_EL2', 'FAR_EL2', 'ELR_EL2', 'SPSR_EL2', 'CNTHCTL_EL2', 'VBAR_EL1', 'SCTLR_EL1', 'ESR_EL1', 'ELR_EL1',
         'FAR_EL1', *SURVEY_TIMERS}
# The system register encodings (op0, op1, CRn, CRm, op2), from the Arm ARM as remembered: the check below compares them with the disassembler's names.
ENCODINGS = {(3, 4, 12, 0, 0): 'VBAR_EL2', (3, 0, 4, 6, 0): 'ICC_PMR_EL1', (3, 0, 12, 12, 3): 'ICC_BPR1_EL1', (3, 0, 12, 12, 7): 'ICC_IGRPEN1_EL1',
             (3, 0, 12, 12, 1): 'ICC_EOIR1_EL1', (3, 0, 12, 11, 1): 'ICC_DIR_EL1', (3, 4, 14, 2, 1): 'CNTHP_CTL_EL2', (3, 4, 14, 2, 2): 'CNTHP_CVAL_EL2',
             (3, 3, 14, 3, 1): 'CNTV_CTL_EL0', (3, 3, 14, 3, 2): 'CNTV_CVAL_EL0', (3, 3, 14, 2, 1): 'CNTP_CTL_EL0', (3, 3, 14, 2, 2): 'CNTP_CVAL_EL0',
             (3, 4, 4, 0, 0): 'SPSR_EL2', (3, 4, 4, 0, 1): 'ELR_EL2', (3, 0, 12, 12, 0): 'ICC_IAR1_EL1', (3, 0, 12, 12, 2): 'ICC_HPPIR1_EL1',
             (3, 0, 12, 11, 3): 'ICC_RPR_EL1', (3, 0, 12, 12, 4): 'ICC_CTLR_EL1', (3, 4, 12, 9, 5): 'ICC_SRE_EL2', (3, 4, 5, 2, 0): 'ESR_EL2',
             (3, 4, 6, 0, 0): 'FAR_EL2', (3, 0, 4, 2, 0): 'SPSel', (3, 3, 4, 2, 1): 'DAIF', (3, 0, 4, 2, 2): 'CurrentEL', (3, 0, 0, 0, 0): 'MIDR_EL1',
             (3, 0, 0, 0, 5): 'MPIDR_EL1', (3, 3, 14, 0, 0): 'CNTFRQ_EL0', (3, 3, 14, 0, 1): 'CNTPCT_EL0', (3, 3, 0, 0, 1): 'CTR_EL0',
             (3, 4, 1, 1, 0): 'HCR_EL2', (3, 4, 1, 0, 0): 'SCTLR_EL2', (3, 4, 2, 0, 2): 'TCR_EL2', (3, 0, 7, 4, 0): 'PAR_EL1',
             (3, 4, 14, 1, 0): 'CNTHCTL_EL2', (3, 0, 12, 0, 0): 'VBAR_EL1', (3, 0, 1, 0, 0): 'SCTLR_EL1', (3, 0, 5, 2, 0): 'ESR_EL1',
             (3, 0, 4, 0, 1): 'ELR_EL1', (3, 0, 6, 0, 0): 'FAR_EL1'}
# What the variant may not run at all. HVC is not here: the EL1 stub and its table are the only code that has one (a test below holds it to them), and
# IC is not either: only IC IVAU, in the one function that makes the stub's lines visible.
FORBIDDEN = {'tlbi', 'smc', 'svc', 'br', 'braa', 'brab', 'braaz', 'brabz', 'brk', 'sys', 'sysl', 'wfi', 'wfe', 'sev', 'sevl', 'hlt', 'dcps1',
             'dcps2', 'dcps3', 'drps', 'eretaa', 'eretab'}
SYSTEM_OPERANDS = {'dc': {'cvac', 'civac'}, 'at': {'s1e2w', 's1e2r'}, 'ic': {'ivau'}, 'dsb': {'sy'}, 'isb': {''}}
LIBC = {'memcpy', 'memset', 'memmove', 'memcmp', 'strlen', 'strcmp', 'strcpy', 'strncpy', 'bcopy', '__memcpy_chk'}


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


def using(tree):
    return mock.patch.dict(os.environ, {'Q1N1_SP11_TREE': str(tree)})


def census(funcs):
    """{mnemonic: Counter of (first operand for dc/at/dsb/isb, the register for msr/mrs)} for the instructions that matter, and where each is."""
    found, where = collections.defaultdict(collections.Counter), collections.defaultdict(set)
    for name, instructions in funcs.items():
        for _, mnemonic, operands in instructions:
            if mnemonic in ('msr', 'mrs', 'eret', 'dc', 'at', 'ic', 'tlbi', 'sys', 'sysl', 'hvc', 'smc', 'svc', 'wfi', 'wfe', 'sev', 'dsb', 'isb', 'dmb', 'br'):
                key = operands.split(',')[0].strip() if mnemonic in ('msr', 'dc', 'at', 'ic', 'dsb', 'isb') else operands.split(',')[-1].strip() if mnemonic == 'mrs' else ''
                found[mnemonic][key] += 1
                where[(mnemonic, key)].add(name.split('#')[0])
    return found, where


def classify(words):
    """The system instructions of a block of code by their bits alone (not by the disassembler's say-so)."""
    found = collections.Counter()
    for w in words:
        if (w & 0xFFF00000) == 0xD5100000:
            found['msr'] += 1
        elif (w & 0xFFF8F01F) == 0xD500401F:
            found['msr'] += 1
            found['msr_imm'] += 1
        elif (w & 0xFFF00000) == 0xD5300000:
            found['mrs'] += 1
        elif (w & 0xFFF80000) in (0xD5080000, 0xD5280000):
            found['sys'] += 1
        elif w in (0xD69F03E0, 0xD69F0BFF, 0xD69F0FFF):
            found['eret'] += 1
        elif (w & 0xFFE0001F) in (0xD4000001, 0xD4000002, 0xD4000003, 0xD4200000):
            found['exception'] += 1
        elif (w & 0xFFFFFC1F) == 0xD61F0000 or (w & 0xFEFFF800) == 0xD61F0800:
            found['br'] += 1
        elif w in (0xD503207F, 0xD503205F, 0xD503209F, 0xD50320BF):
            found['wfi_wfe_sev'] += 1
        elif (w & 0xFFFFF01F) == 0xD503301F:
            found['barrier'] += 1
    return found


def daif_violations(funcs):
    """Writes of PSTATE.DAIF by immediate that touch more than the I bit: FIQ, SError and debug exceptions stay masked."""
    return [f'{name}+{address:#x}: {mnemonic} {operands}' for name, body in funcs.items() for address, mnemonic, operands in body
            if mnemonic == 'msr' and operands.startswith(('DAIFSet', 'DAIFClr')) and not operands.endswith(', #0x2')]


def register_of(word):
    return ENCODINGS.get(((2 + (word >> 19 & 1)), word >> 16 & 7, word >> 12 & 15, word >> 8 & 15, word >> 5 & 7))


def funcs_of(**bodies):
    """A listing in llvm-objdump's format for functions given as name=[instruction text], 4 bytes apart from address 0."""
    text = ''
    for name, body in bodies.items():
        text += f'0000000000000000 <{name}>:\n'
        text += ''.join(f'{i * 4:8x}: \t{line.replace(" ", chr(9), 1)}\n' for i, line in enumerate(body))
    return T.parse_functions(text)


class Analysis(unittest.TestCase):
    """The census can fail: each scan is shown to flag the code it is for."""

    def test_the_bit_scan_flags_each_class_of_instruction_the_variant_may_not_run_and_counts_the_ones_it_may(self):
        # hvc #0, smc #0, svc #0, brk #0, wfi, wfe, tlbi vmalle1is, br x0, eret, msr TTBR0_EL2, x0, msr DAIFClr, #2, mrs x0, SCTLR_EL2, dsb sy
        words = [0xD4000002, 0xD4000003, 0xD4000001, 0xD4200000, 0xD503207F, 0xD503205F, 0xD508831F, 0xD61F0000, 0xD69F03E0, 0xD51C2000, 0xD50342FF,
                 0xD53C1000, 0xD5033F9F]
        found = classify(words)
        self.assertEqual((found['exception'], found['wfi_wfe_sev'], found['sys'], found['br'], found['eret'], found['msr'], found['msr_imm'], found['mrs'],
                          found['barrier']), (4, 2, 1, 1, 1, 2, 1, 1, 1))

    def test_the_name_table_knows_the_registers_the_stage_uses_and_not_one_it_does_not(self):
        self.assertEqual(register_of(0xD51CC000), 'VBAR_EL2')
        self.assertEqual(register_of(0xD51C2000 | 5), None, 'TTBR0_EL2')
        self.assertEqual(register_of(0xD53C1000), 'SCTLR_EL2')

    def test_the_census_lists_a_register_that_is_not_in_the_set_of_those_the_variant_may_write_and_a_daif_write_beyond_the_i_bit(self):
        funcs = funcs_of(stage=['msr VBAR_EL2, x0', 'msr TTBR0_EL2, x1', 'msr DAIFClr, #0x7', 'msr DAIFSet, #0x2', 'wfi', 'ret'])
        found, _ = census(funcs)
        self.assertEqual(set(found['msr']) - WRITES, {'TTBR0_EL2'})
        self.assertEqual(dict(found['wfi']), {'': 1})
        self.assertEqual([v.split(': ')[1] for v in daif_violations(funcs)], ['msr DAIFClr, #0x7'])


class Binary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_build()
        profile = T.write(SCRATCH / 'profile.json', json.dumps({'defines': [f'-D{k}={v}' for k, v in T.GIC.items()]}).encode())
        cls.dirs = {name: SCRATCH / name for name in ('default', 'gic-a', 'gic-b')}
        with using(STATE['tree']):
            cls.reports = {name: wb.build(str(d), profile, gic=name != 'default') for name, d in cls.dirs.items()}
        cls.funcs = {stem: T.parse_functions(T.disassemble(cls.dirs[d] / f'{stem}.elf')) for stem, d in
                     (('snapmon', 'default'), ('snaprep', 'default'), ('snapmon-gic', 'gic-a'), ('snaprep-gic', 'gic-a'))}
        cls.flat = {stem: (cls.dirs[d] / f'{stem.upper()}.BIN').read_bytes() for stem, d in
                    (('snapmon', 'default'), ('snaprep', 'default'), ('snapmon-gic', 'gic-a'), ('snaprep-gic', 'gic-a'))}
        cls.sections = {stem: T.sections(cls.dirs[d] / f'{stem}.elf') for stem, d in
                        (('snapmon', 'default'), ('snaprep', 'default'), ('snapmon-gic', 'gic-a'), ('snaprep-gic', 'gic-a'))}
        cls.words = {stem: T.code_words(cls.flat[stem], cls.sections[stem]['.text']) for stem in cls.flat}

    # -- the default build ------------------------------------------------------------------------------------------------------
    def test_with_the_gate_off_the_dry_run_wrapper_and_reporter_are_the_binaries_that_ran_on_the_unit(self):
        for name, (size, digest) in UNIT_RUN.items():
            data = (self.dirs['default'] / name).read_bytes()
            self.assertEqual((len(data), T.sha(data)), (size, digest), name)
        self.assertEqual(self.reports['default']['added'], ['-DSNAP_PLATFORM_SP11=1', '-DSNAP_SP11_STICK=1'])
        self.assertNotIn('variant', self.reports['default'])

    def test_the_default_wrapper_has_no_msr_no_exception_instruction_no_eret_and_no_tlbi_or_ic(self):
        found, _ = census(self.funcs['snapmon'])
        for mnemonic in ('msr', 'eret', 'tlbi', 'ic', 'smc', 'hvc', 'svc', 'wfi', 'br'):
            self.assertEqual(dict(found[mnemonic]), {}, mnemonic)
        self.assertEqual(classify(self.words['snapmon'])['msr'] + classify(self.words['snapmon'])['eret'], 0)

    # -- the variant's files ------------------------------------------------------------------------------------------------------
    def test_two_builds_of_the_variant_are_byte_identical_and_the_report_names_the_digests_the_gate_and_no_hardware_run(self):
        for name in wb.GIC_NAMES:
            a, b = [(self.dirs[d] / name).read_bytes() for d in ('gic-a', 'gic-b')]
            self.assertEqual(a, b, name)
            self.assertEqual(self.reports['gic-a']['files'][name], {'bytes': len(a), 'sha256': T.sha(a)})
        self.assertEqual(self.reports['gic-a'], self.reports['gic-b'])
        r = self.reports['gic-a']
        self.assertEqual((r['added'], r['hardware'], sorted(r['files'])), (['-DSNAP_PLATFORM_SP11=1', '-DSNAP_SP11_STICK=1', '-DSNAP_SP11_STICK_GIC=1'],
                                                                          'NOT RUN', sorted(wb.GIC_NAMES)))
        self.assertTrue(r['variant'].startswith('gic:') and r['monitor'].startswith('none'))
        self.assertNotEqual(self.flat['snapmon-gic'], self.flat['snapmon'])

    def test_each_image_is_its_text_and_rodata_and_nothing_else_and_the_entry_is_bti_then_a_branch(self):
        self.assertLess(len(self.flat['snaprep-gic']), wb.REPORTER_MAX)
        for stem in ('snapmon-gic', 'snaprep-gic'):
            sec = self.sections[stem]
            self.assertEqual(len(self.flat[stem]), sec['.rodata'][0] + sec['.rodata'][1], stem)
            self.assertEqual(sec['.text'][0], 0)
            self.assertEqual(struct.unpack('<2I', self.flat[stem][:8]), (0xD503245F, 0x14000001), stem)
            self.assertLessEqual(set(sec), {'NULL', '.text', '.rodata', '.rela.text', '.symtab', '.shstrtab', '.strtab'}, 'no data, no bss, no got')
            self.assertEqual(subprocess.run([T.find_tool('llvm-nm'), '-u', str(self.dirs['gic-a'] / f'{stem}.elf')], capture_output=True, text=True).stdout.strip(), '')

    # -- the census ----------------------------------------------------------------------------------------------------------------
    def test_the_variant_writes_exactly_the_system_registers_it_names_each_in_its_own_function(self):
        found, where = census(self.funcs['snapmon-gic'])
        self.assertEqual(set(found['msr']), WRITES)
        for name in WRITES:
            self.assertEqual(where[('msr', name)], WHERE[name], name)
        self.assertEqual(dict(found['msr']), COUNTS)
        self.assertEqual(sum(found['msr'].values()), 26, '26 MSR instructions of 15 registers; the dry run of pass 1 had 15 of 12')
        self.assertEqual(daif_violations(self.funcs['snapmon-gic']), [], 'only the I bit is ever unmasked or masked: FIQ and SError stay masked')

    def test_the_variant_reads_only_the_registers_it_names_and_acknowledges_in_two_places(self):
        found, where = census(self.funcs['snapmon-gic'])
        self.assertLessEqual(set(found['mrs']), READS)
        self.assertEqual(where[('mrs', 'ICC_IAR1_EL1')], {'sp11hw_read_icc_iar1_el1'})
        for name in SURVEY_TIMERS:
            self.assertEqual((found['mrs'][name], where[('mrs', name)]), (1, {f'sp11hw_read_{name.lower()}'}), 'the survey reads each EL1 timer register once, in a function of its own')
        self.assertEqual(T.callers_of(self.funcs['snapmon-gic'], 'sp11hw_read_icc_iar1_el1'), {'sw_gic_vec_irq': 1, 'sw_gic_vec_irq_el1': 1},
                         'window 1 acknowledges three interrupts in one place and never at the deadline; window 2 its one')

    def test_the_variant_has_two_erets_one_hvc_in_the_stub_sixteen_in_the_el1_table_one_ic_and_no_smc_svc_tlbi_wfi_wfe_or_br(self):
        found, where = census(self.funcs['snapmon-gic'])
        self.assertEqual((dict(found['eret']), where[('eret', '')]), ({'': 2}, {'sw_gic_vec_common', 'sw_gic_excursion'}))
        self.assertEqual((dict(found['hvc']), where[('hvc', '')]), ({'': 17}, {'sw_gic_el1_stub', 'sw_gic_el1_vectors'}))
        hvc = collections.Counter((name, operands) for name, body in self.funcs['snapmon-gic'].items() for _, m, operands in body if m == 'hvc')
        self.assertEqual(dict(hvc), {('sw_gic_el1_stub', '#0'): 1, ('sw_gic_el1_vectors', '#0x1'): 16}, 'HVC #0 ends the stub; HVC #1 is an EL1 vector reporting')
        self.assertEqual((dict(found['ic']), where[('ic', 'ivau')]), ({'ivau': 1}, {'sp11hw_make_visible'}))
        for mnemonic in FORBIDDEN & {'tlbi', 'smc', 'svc', 'wfi', 'wfe', 'br', 'sys'}:
            self.assertEqual(dict(found[mnemonic]), {}, mnemonic)
        for mnemonic, allowed in SYSTEM_OPERANDS.items():
            self.assertLessEqual(set(found[mnemonic]), allowed, mnemonic)
        self.assertTrue({'dc', 'at', 'ic', 'dsb', 'isb'} <= set(found))
        self.assertEqual(where[('dc', 'civac')], {'sp11hw_make_visible', 'sw_verify_all'}, 'the one new DC CIVAC is the stub\'s visibility')

    def test_every_indirect_call_is_through_a_register_loaded_from_the_runner_table_and_the_reporter_variant_has_no_system_instruction(self):
        self.assertEqual(T.indirect_branch_violations(self.funcs['snapmon-gic']), [])
        found, _ = census(self.funcs['snaprep-gic'])
        self.assertEqual({k: dict(v) for k, v in found.items()}, {})
        self.assertEqual(T.encoding_violations(self.words['snaprep-gic'], {0xD503245F: 'bti c'}, {}), [])
        self.assertEqual(T.store_violations(self.funcs['snaprep-gic']), [])

    def test_the_two_views_of_the_code_agree_the_listing_and_the_bits(self):
        by_bits, found = classify(self.words['snapmon-gic']), census(self.funcs['snapmon-gic'])[0]
        count = lambda *names: sum(sum(found[n].values()) for n in names)         # noqa: E731
        self.assertEqual((by_bits['msr'], by_bits['mrs'], by_bits['sys'], by_bits['eret'], by_bits['barrier']),
                         (count('msr'), count('mrs'), count('dc', 'at', 'ic'), count('eret'), count('dsb', 'isb', 'dmb')))
        self.assertEqual((by_bits['exception'], by_bits['br'], by_bits['wfi_wfe_sev']), (count('hvc'), 0, 0), 'the only exception instructions are the 17 HVCs')
        words = {w & T.SYS_MASK for w in self.words['snapmon-gic'] if (w & 0xFFF80000) == 0xD5080000}
        self.assertLessEqual(words, {0xD50B7A20, 0xD50B7E20, 0xD50C7820, 0xD50C7800, 0xD50B7520}, 'dc cvac, dc civac, at s1e2w, at s1e2r and ic ivau only')
        self.assertLessEqual({w for w in self.words['snapmon-gic'] if (w & 0xFFFFF01F) == 0xD503301F}, {0xD5033F9F, 0xD5033FDF}, 'dsb sy and isb only')

    def test_each_register_the_listing_names_is_the_one_the_encoding_says(self):
        """The disassembler's name for every MSR and MRS is checked against the register fields of the word, with a table of encodings written
        here: a register that the listing names differently from its encoding, or one that is not in the table, fails."""
        flat = self.flat['snapmon-gic']
        for name, instructions in self.funcs['snapmon-gic'].items():
            for address, mnemonic, operands in instructions:
                if mnemonic not in ('msr', 'mrs') or operands.startswith(('DAIFSet', 'DAIFClr')):
                    continue
                word = struct.unpack_from('<I', flat, address)[0]
                listed = operands.split(',')[0].strip() if mnemonic == 'msr' else operands.split(',')[-1].strip()
                self.assertEqual(register_of(word), listed, f'{name}+{address:#x}: {mnemonic} {operands}')
        for word in self.words['snapmon-gic']:
            if (word & 0xFFF8F01F) == 0xD500401F:
                self.assertEqual((word >> 16 & 7, word >> 8 & 15), (3, 2), 'the immediate forms are DAIFSet and DAIFClr with the I bit only')
                self.assertIn(word >> 5 & 7, (6, 7))

    def test_stores_are_to_the_stack_or_through_arguments_and_the_mmio_stores_are_two_one_instruction_functions_called_only_by_the_stage(self):
        funcs = self.funcs['snapmon-gic']
        callees = {name.split('#')[0] for name in funcs} - {'payload_entry', '_start'}
        self.assertEqual(T.store_violations(funcs, through_arguments=callees), [])
        for name, store in (('sp11hw_mmio_write8', 'strb'), ('sp11hw_mmio_write32', 'str')):
            self.assertEqual([(m, o) for _, m, o in funcs[name]], [(store, 'w1, [x0]'), ('ret', '')], name)
        calls = {name: T.callers_of(funcs, name) for name in ('sp11hw_mmio_write8', 'sp11hw_mmio_write32')}
        self.assertEqual({n: sum(c.values()) for n, c in calls.items()}, {'sp11hw_mmio_write8': 2, 'sp11hw_mmio_write32': 11},
                         'the GICD_CTLR store and put-back, the redistributor stores, and (pass 5) the quarantine\'s ICENABLER0 and the put-back\'s ISENABLER0')
        for name, callers in calls.items():
            self.assertTrue(all(c.startswith(('gic_', 'sw_gic_')) for c in callers), f'{name} is called from {sorted(callers)}: only the stage may')
        report_and_copy = {'sw_copy_all', 'sw_head_flush', 'sw_head_init', 'sw_refuse', 'sw_set_stage'}
        self.assertEqual({c for c in T.callers_of(funcs, 'sp11hw_store64') if not c.startswith(('gic_', 'sw_gic_'))}, report_and_copy)

    def test_the_system_register_writes_are_called_only_from_the_stage_and_the_vector(self):
        funcs = self.funcs['snapmon-gic']
        for name in (n for n in funcs if n.split('#')[0].startswith('sp11hw_write_') or n.split('#')[0] in ('sp11hw_irq_mask', 'sp11hw_make_visible')):
            for caller in T.callers_of(funcs, name.split('#')[0]):
                self.assertTrue(caller.startswith(('gic_', 'sw_gic_')), f'{name} is called from {caller}')

    def test_the_vector_table_is_2_kib_aligned_in_the_image_sixteen_entries_128_bytes_apart_and_the_window_has_no_wfi(self):
        funcs = self.funcs['snapmon-gic']
        done = subprocess.run([T.find_tool('llvm-nm'), str(self.dirs['gic-a'] / 'snapmon-gic.elf')], capture_output=True, text=True)
        symbols = {line.split()[2]: int(line.split()[0], 16) for line in done.stdout.splitlines() if len(line.split()) == 3}
        table = symbols['sw_gic_vectors']
        self.assertEqual(table % 0x800, 0, 'VBAR_ELx keeps bits 10:0 reserved: the table is 2 KiB aligned within a page-aligned image')
        entries = [a for a, _, _ in funcs['sw_gic_vectors'] if (a - table) % 0x80 == 0 and a - table < 0x800]
        self.assertEqual(len(entries), 16)
        for slot, address in enumerate(entries):
            body = [(m, o) for a, m, o in funcs['sw_gic_vectors'] if address <= a < address + 16]
            self.assertEqual(body, [('sub', 'sp, sp, #0xa0'), ('stp', 'x0, x1, [sp]'), ('mov', f'x0, #{slot:#x}'), ('b', f'{symbols["sw_gic_vec_common"]:#x} <sw_gic_vec_common>')])
        window = [m for _, m, _ in funcs['sw_gic_window'] + funcs['sw_gic_window_exit']]
        self.assertFalse({'wfi', 'wfe'} & set(window))
        self.assertEqual(window.count('msr'), 4, 'VBAR_EL2 in, DAIFClr, DAIFSet and VBAR_EL2 back')

    def test_the_excursion_routine_and_the_el1_table_and_stub_are_what_the_document_says(self):
        funcs = self.funcs['snapmon-gic']
        done = subprocess.run([T.find_tool('llvm-nm'), str(self.dirs['gic-a'] / 'snapmon-gic.elf')], capture_output=True, text=True)
        symbols = {line.split()[2]: int(line.split()[0], 16) for line in done.stdout.splitlines() if len(line.split()) == 3}
        table = symbols['sw_gic_el1_vectors']
        self.assertEqual(table % 0x800, 0, 'the EL1 table is 2 KiB aligned too: VBAR_EL1 keeps bits 10:0 reserved')
        entries = [a for a, _, _ in funcs['sw_gic_el1_vectors'] if (a - table) % 0x80 == 0 and a - table < 0x800]
        self.assertEqual(len(entries), 16)
        for slot, address in enumerate(entries):
            self.assertEqual([(m, o) for a, m, o in funcs['sw_gic_el1_vectors'] if address <= a < address + 8], [('mov', f'x16, #{slot:#x}'), ('hvc', '#0x1')])
        self.assertEqual((symbols['sw_gic_el1_stub'] - table, symbols['sw_gic_el1_end'] > symbols['sw_gic_el1_stub']), (15 * 0x80 + 0x10, True),
                         'the stub follows the sixteenth entry\'s two instructions inside its 128 bytes, so that one range of the image is made visible')
        stub = funcs['sw_gic_el1_stub']
        self.assertEqual([m for _, m, _ in stub], ['mrs', 'add', 'mrs', 'sub', 'tbnz', 'hvc', 'udf'], 'no branch but the loop, no memory access, no stack')
        self.assertEqual([o for _, m, o in stub if m == 'mrs'], ['x2, CNTPCT_EL0', 'x4, CNTPCT_EL0'], 'the only registers it reads are the counter')
        loop = next(o for _, m, o in stub if m == 'tbnz')
        self.assertTrue(loop.endswith(f'{stub[2][0]:#x} <sw_gic_el1_stub+{stub[2][0] - symbols["sw_gic_el1_stub"]:#x}>'), 'the loop is back to its own counter read')
        routine = [m for _, m, _ in funcs['sw_gic_excursion'] + funcs['sw_gic_excursion_exit']]
        self.assertEqual((routine.count('msr'), routine.count('eret'), {'wfi', 'wfe'} & set(routine)), (6, 1, set()))
        self.assertGreater(symbols['sw_gic_excursion_exit'], symbols['sw_gic_excursion'])

    def test_the_stack_the_stage_and_two_nested_exceptions_use_is_far_below_what_the_runner_leaves_a_payload(self):
        funcs = self.funcs['snapmon-gic']
        deepest, vector = T.stack_depth(funcs), 0xA0 + T.stack_depth(funcs, root='sw_gic_vec_common')
        self.assertLess(deepest + 2 * vector, 16 * 1024)

    def test_no_libc_function_is_in_or_called_from_the_variant(self):
        for stem in ('snapmon-gic', 'snaprep-gic'):
            self.assertFalse(LIBC & {name.split('#')[0] for name in self.funcs[stem]})
            called = {re.search(r'<([^>+]+)', text).group(1) for body in self.funcs[stem].values() for _, m, text in body if m == 'bl' and re.search(r'<([^>+]+)', text)}
            self.assertFalse(LIBC & called, stem)

    def test_the_authorisation_record_names_every_register_the_variant_writes_and_every_register_of_the_stage(self):
        import sp11_stick_bundle_build as bb
        text = '\n'.join(line for lines in bb.GIC_STOP_TOUCHES.values() for line in lines)
        for name in sorted(WRITES - {'DAIFSet', 'DAIFClr'}) + ['DAIF.I', 'GICD_CTLR', 'IPRIORITYR', 'ISENABLER0', 'ICFGR1', 'IGROUPR0', 'ICENABLER0', 'ICPENDR0',
                                                               'IC IVAU', 'DC CIVAC', 'HVC', 'ERET']:
            self.assertIn(name, text, name)


# ---- The gate ---------------------------------------------------------------------------------------------------------------------
class Gate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_build()
        cflags, _ = wb.recipe_flags(STATE['tree'])
        cls.base = [T.find_tool('clang'), *cflags, f'-I{STATE["tree"] / "payloads"}']
        cls.macros = [f'-D{k}={v}' for k, v in T.GIC.items()]
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
            return dict(zip(jobs, pool.map(lambda job: self.cc(job[0], list(job[1])), jobs)))

    def test_with_the_gic_gate_off_or_unset_the_gic_sources_compile_to_nothing_and_the_dry_run_sources_still_compile(self):
        off = {'unset': (*self.on, *self.macros), 'zero': (*self.on, *self.macros, '-DSNAP_SP11_STICK_GIC=0'), 'no stick': ('-DSNAP_SP11_STICK_GIC=0',),
               'nothing': ()}
        jobs = [(source, flags) for source in GIC_SOURCES for flags in off.values()]
        for (source, flags), (code, err, symbols) in self.many(jobs).items():
            with self.subTest(source=source, flags=flags):
                self.assertEqual((code, symbols), (0, ''), err)
        for (source, flags), (code, err, symbols) in self.many([(s, (*self.on, *self.macros)) for s in DRY_RUN_SOURCES]).items():
            self.assertEqual(code, 0, err)
            self.assertTrue(symbols.strip())

    def test_a_gic_gate_value_other_than_0_or_1_and_a_gate_without_the_stick_gate_do_not_compile_and_say_why(self):
        bad = {'two': ((*self.on, *self.macros, '-DSNAP_SP11_STICK_GIC=2'), 'accepts only 0 and 1'),
               'empty': ((*self.on, *self.macros, '-DSNAP_SP11_STICK_GIC='), ''),
               'no stick': (('-DSNAP_PLATFORM_SP11=1', '-DSNAP_SP11_STICK_GIC=1', *self.macros), 'needs SNAP_SP11_STICK=1'),
               'stick 0': (('-DSNAP_PLATFORM_SP11=1', '-DSNAP_SP11_STICK=0', '-DSNAP_SP11_STICK_GIC=1', *self.macros), 'needs SNAP_SP11_STICK=1')}
        fragments = {flags: fragment for flags, fragment in bad.values()}
        for (source, flags), (code, err, _) in self.many([(s, f) for s in (*GIC_SOURCES, *DRY_RUN_SOURCES) for f in fragments]).items():
            with self.subTest(source=source, flags=flags):
                self.assertEqual(code, 1)
                self.assertIn(fragments[flags], err)

    def test_with_the_gate_on_every_source_compiles_and_the_stage_is_in_the_wrapper_only(self):
        for (source, flags), (code, err, symbols) in self.many([(s, (*self.on, *self.macros, '-DSNAP_SP11_STICK_GIC=1')) for s in (*GIC_SOURCES, *DRY_RUN_SOURCES)]).items():
            with self.subTest(source=source):
                self.assertEqual(code, 0, err)
                self.assertTrue(symbols.strip(), 'every source has something in it with the gate on')

    def test_a_seeded_fault_is_a_host_test_option_and_does_not_compile_into_a_unit_build(self):
        for seed in ('NO_SAVE', 'STRAY_STORE', 'NO_EOI', 'TIMER_ON', 'NO_IRQ_MASK', 'STORE_FIRST', 'NO_NEXT', 'NO_RESTORE', 'FINAL_ACK', 'NO_REARM', 'NO_IMO',
                     'NO_EL1_RESTORE', 'NO_NEXT_EL1', 'UNBOUNDED_EL1', 'NO_SETTLE', 'KEEP_FOREIGN_DEADLINE', 'RESTORE_CTL_FIRST', 'NO_QUARANTINE',
                     'QUARANTINE_NO_PUTBACK', 'QUARANTINE_BIT26', 'QUARANTINE_STRAY_WRITE', 'NO_HPPIR_CHECK', 'QUARANTINE_AFTER_G'):
            code, err, _ = self.cc('sp11-stick-gic.c', [*self.on, *self.macros, '-DSNAP_SP11_STICK_GIC=1', f'-DSP11_STICK_GIC_SEED_{seed}'])
            with self.subTest(seed=seed):
                self.assertEqual(code, 1)
                self.assertIn('part of the host test build only', err)

    def census_of_the_core(self, extra):
        """The census and the hw-layer functions left undefined of sp11-stick-gic.c alone, the half the monitor entry will call, compiled with the
        payload recipe and `extra` flags."""
        obj = SCRATCH / 'gate' / f'core-{T.sha(repr(extra).encode())[:12]}.o'
        obj.parent.mkdir(exist_ok=True)
        done = subprocess.run([*self.base, *self.on, *self.macros, '-DSNAP_SP11_STICK_GIC=1', *extra, '-c', str(ROOT / 'probes/sp11-stick-gic.c'), '-o', str(obj)],
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        undefined = subprocess.run([T.find_tool('llvm-nm'), '-u', str(obj)], capture_output=True, text=True).stdout.split()
        return census(T.parse_functions(T.disassemble(obj)))[0], {s for s in undefined if s.startswith('sp11hw_')}

    def test_the_arm_and_put_back_half_built_for_the_monitor_entry_writes_no_register_of_the_dry_run_and_the_dry_run_build_of_it_does(self):
        dry_writers = {'sp11hw_write_cnthp_ctl_el2', 'sp11hw_write_cnthp_cval_el2', 'sp11hw_write_hcr_el2', 'sp11hw_write_vbar_el1', 'sp11hw_write_sctlr_el1'}
        found, undefined = self.census_of_the_core(['-DSW_GIC_DRY_REGS=0'])
        self.assertEqual(set(found['msr']), {'ICC_PMR_EL1', 'ICC_BPR1_EL1', 'ICC_IGRPEN1_EL1'}, 'the monitor sets CNTHP, VBAR_EL2 and HCR_EL2 itself: 4b must not')
        for mnemonic in ('eret', 'hvc', 'ic', 'smc', 'svc', 'tlbi', 'wfi', 'wfe', 'br', 'sys'):
            self.assertEqual(dict(found[mnemonic]), {}, mnemonic)
        self.assertFalse({'CNTHP_CTL_EL2', 'CNTHP_CVAL_EL2', 'VBAR_EL2', 'HCR_EL2', 'VBAR_EL1', 'SCTLR_EL1', 'CNTHCTL_EL2'} & set(found['mrs']), 'nor read')
        self.assertEqual((found['mrs']['ICC_HPPIR1_EL1'], SURVEY_TIMERS & set(found['mrs'])), (1, set()),
                         'it reads ICC_HPPIR1_EL1 once, the check before the window, and none of the survey\'s EL1 timers')
        self.assertEqual((set(found['dc']), undefined & dry_writers), ({'cvac'}, set()), 'it does not even call a writer of the dry run')
        self.assertFalse(undefined & {f'sp11hw_read_{n.lower()}' for n in SURVEY_TIMERS}, 'nor a reader of the survey\'s timers')
        found, undefined = self.census_of_the_core([])
        self.assertEqual({f'sp11hw_read_{n.lower()}' for n in SURVEY_TIMERS} & undefined, {f'sp11hw_read_{n.lower()}' for n in SURVEY_TIMERS},
                         'built for the dry run it calls the four readers, each defined once in sp11-stick-gic-dry.c, with no MRS of them of its own')
        self.assertFalse(SURVEY_TIMERS & set(found['mrs']))
        self.assertLessEqual({'VBAR_EL2', 'DAIFSet'}, set(found['msr']), 'built for the dry run its table also puts back VBAR_EL2 and DAIF')
        self.assertEqual((undefined & dry_writers, set(found['msr']) & {'HCR_EL2', 'VBAR_EL1', 'SCTLR_EL1', 'CNTHP_CTL_EL2', 'CNTHP_CVAL_EL2'}), (dry_writers, set()),
                         'and calls the dry run\'s five writers, each defined once in sp11-stick-gic-dry.c, with no MSR of them of its own')


# ---- Hygiene ------------------------------------------------------------------------------------------------------------------
def own_files():
    found = [*(ROOT / 'probes').glob('sp11-stick-gic*'), *(ROOT / 'tools').glob('sp11_stick_gic_*.py'), *(ROOT / 'tools').glob('test-sp11-stick-gic*'),
             ROOT / 'docs/SP11-STICK-GIC.md']
    return sorted(p for p in found if p.is_file())


class Hygiene(unittest.TestCase):
    def test_the_files_are_the_ones_this_work_item_adds(self):
        names = {p.name for p in own_files()}
        for wanted in ('sp11-stick-gic.c', 'sp11-stick-gic-dry.c', 'sp11-stick-gic.h', 'sp11-stick-gic-record.h', 'sp11-stick-gic-vec.c', 'sp11_stick_gic_testlib.py',
                       'sp11_stick_gic_checks.py', 'test-sp11-stick-gic.c', 'test-sp11-stick-gic.py', 'test-sp11-stick-gic-record.py', 'test-sp11-stick-gic-build.py',
                       'test-sp11-stick-gic-qemu.py', 'test-sp11-stick-gic-quarantine.py', 'test-sp11-stick-gic-putback.py', 'test-sp11-stick-gic-hppir.py',
                       'test-sp11-stick-gic-doc.py', 'SP11-STICK-GIC.md'):
            self.assertIn(wanted, names)

    def test_every_source_file_starts_with_an_spdx_mit_line(self):
        for path in own_files():
            if path.suffix != '.md':
                with self.subTest(path.name):
                    self.assertIn('SPDX-License-Identifier: MIT', '\n'.join(path.read_text().splitlines()[:3]))

    def test_every_c_and_python_file_stays_under_1500_lines_and_50_kib_and_the_note_under_58600_bytes(self):
        for path in own_files():
            with self.subTest(path.name):
                text = path.read_text()
                if path.suffix in ('.c', '.h', '.py'):
                    self.assertLess(len(text.splitlines()), 1500)
                    self.assertLess(len(text.encode()), 50 * 1024)
                else:
                    self.assertLessEqual(len(path.read_bytes()), 58600)     # raised from 58,400 for the durations with late passes and the boot 13 reads of the review of part 4 (58,530 B); from 56,000 for the record text of passes 5 and 6 (58,203 B: --gic-quarantine, the 12 s of the quarantine, the two new tests); from 48,000 in pass 6 (33,000 in pass 5, 30,000 before pass 3): the committed text was at 46,966 B and pass 6 adds statements
                    self.assertTrue(text.isascii())

    def test_no_file_names_the_build_host_a_person_a_network_a_key_or_an_unfinished_marker(self):
        user = getpass.getuser()
        markers = r'\b(?:TO' r'DO|FIX' r'ME|X' r'XX|HA' r'CK|W' r'IP)\b'
        patterns = {'a home directory': r'/(?:Users|home)/[A-Za-z0-9._-]+', 'a MAC address': r'\b[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}\b',
                    'a private address': r'\b(?:192\.168|10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b', 'a key': r'BEGIN (?:OPENSSH|RSA|PRIVATE)|ssh-(?:rsa|ed25519)',
                    'a credential': r'(?i)\b(?:password|passwd|secret|token)\s*[=:]\s*\S', 'an unfinished marker': markers, 'a session tag': r'/\*\s*[A-Z]\d+:'}
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

    def test_no_existing_monitor_source_names_the_gic_gate_or_includes_a_file_of_this_work_item(self):
        needles = ('SNAP_SP11_STICK_GIC', 'sp11-stick-gic', 'SP11_STICK_GIC_SEED')
        mine = {p.resolve() for p in own_files()} | {ROOT / 'probes' / n for n in ('sp11-stick-hw.h', 'sp11-stick-wrapper.c', 'sp11-stick-wrapper.h', 'sp11-stick-reporter.c')}
        mine |= {p.resolve() for p in (ROOT / 'probes').glob('sp11-stick-monitor*')}      # the monitor entry of the stick (work item 4b) is the client of sw_gic_arm and sw_gic_put_back
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
                self.assertNotIn(needle, text, f'{path.relative_to(ROOT)} mentions {needle}: the monitor build must not know the GIC stage')


DOC = ROOT / 'docs/SP11-STICK-GIC.md'


class Document(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = DOC.read_bytes()
        cls.text = cls.data.decode('ascii')
        cls.flat = cls.text.replace('\n', ' ')

    def test_the_note_is_ascii_and_within_its_size_budget(self):
        self.assertLessEqual(len(self.data), 58600)         # raised from 58,400 for the durations with late passes and the boot 13 reads of the review of part 4 (58,530 B), from 56,000 for the record text of passes 5 and 6 (58,203 B), from 48,000 in pass 6 (33,000 in pass 5; 30,000 before pass 3)

    def test_it_says_hardware_not_run_uses_the_labels_and_claims_no_hardware_result(self):
        for needle in ('Hardware NOT RUN', 'EXPERIMENTAL', 'VERIFIED', 'OBSERVED', 'INFERRED', 'UNIMPLEMENTED', 'UNKNOWN'):
            self.assertIn(needle, self.text)
        self.assertNotRegex(self.text, r'(?i)hardware (?:pass|qualified|validated|ok)\b')
        self.assertNotIn('ECVEn', self.text, 'whether CNTHCTL_EL2 bits 13 and 14 depend on SCR_EL3.ECVEn is UNKNOWN: nothing here says either way')
        self.assertNotIn('first code below EL2', self.text, 'el1trap is the first runner payload below EL2; the X2 image had run an EL1 stub earlier (T02)')
        self.assertIn('T02', self.text)

    def test_every_outcome_the_page_can_name_is_in_the_failure_table(self):
        table = self.text.split('## 10.')[1].split('## 11.')[0]
        for name in (n for code, n in G.OUTCOMES.items() if code):
            self.assertRegex(table, rf'\b{name}\b')

    def test_every_source_test_and_tool_file_of_the_work_item_is_named(self):
        for path in own_files():
            if path.suffix in ('.c', '.h', '.py'):
                self.assertIn(f'{path.parent.name}/{path.name}', self.text, f'{path.name} is not named in the note')

    def test_the_files_and_commands_it_names_exist(self):
        for rel in set(re.findall(r'`((?:probes|tools|docs|config)/[A-Za-z0-9_.-]+)`', self.text)):
            self.assertTrue((ROOT / rel).exists(), rel)
        for rel in set(re.findall(r'\b((?:probes|tools|docs)/[A-Za-z0-9_.-]+\.(?:c|h|py|md))\b', self.text)):
            self.assertTrue((ROOT / rel).exists(), rel)

    def test_the_numbers_it_states_are_the_codes(self):
        header = (ROOT / 'probes/sp11-stick-gic.h').read_text() + (ROOT / 'probes/sp11-stick-gic-record.h').read_text()
        self.assertEqual((G.REC_BYTES, int(re.search(r'SP11_STICK_GIC_TIME_BOX_S (\d+)u', header).group(1)), int(re.search(r'SW_GIC_WAIT_S (\d+)u', header).group(1))), (1456, 2, 8))
        for fragment in ('1456 bytes', 'magic `SNAPGIC1`', 'REPORT.DAT offset 0x11000', '`SP11_STICK_GIC_TIME_BOX_S` 2 (1 to 5)', 'the wait 8 s', 'priority 0x80, PMR 0xF0, BPR1 0',
                         '`0x1000 + 82` (GIC_NOT_READY)', '29 of the 30 allowed', 'at most 28 lines of its 40', '13 call sites', 'seventeen seeded-bad stages', 'twenty-three in all',
                         '182 words', 'version 3', 'the survey from 0x560', 'Q_NOT_HELD 22 and FOREIGN_PENDING 23'):
            self.assertIn(fragment, self.flat)
        self.assertEqual((G.sb.WRAPPER_LINES, G.sb.REPORTER_LINES, G.sb.RET_REFUSED, G.sb.REPORT_OUT), (30, 40, 0x1000, 0x1000))
        for name in ('NEXT GIC STORES AFTER 8 S', 'NEXT GIC WINDOW 2 S', 'NEXT GIC EL1 EXCURSION AFTER 8 S', 'GIC NO RECORD: THE STAGE DID NOT RUN', 'GIC RECORD DAMAGED CODE='):
            self.assertIn(name, self.text)

    def test_the_extension_record_rule_and_the_study_findings_are_written_down(self):
        self.assertEqual((G.REC_AT, G.REC_BYTES), (0x11000, 1456))
        self.assertLessEqual(G.REC_AT + G.REC_BYTES, 528384, 'inside REPORT.DAT')
        for needle in ('extension record', '8-byte ASCII tag', 'only the wrapper', 'the reporter', 'the monitor never does', 'zero tag', 'out + 0x10000',
                       'C4', 'C6', 'sw_gic_arm', 'sw_gic_put_back', '-DSW_GIC_DRY_REGS=0', 'must not arm CNTHP', 'PSTATE.I', 'xnuboot.S:158', 'Total window time'):
            self.assertIn(needle, self.flat.replace('Only the wrapper', 'only the wrapper'))

    def test_the_rule_for_the_enable_register_and_boot_12_are_written_down_and_no_text_says_the_whole_register_must_be_clear(self):
        for needle in ('PPI 26 itself pending or active', 'ISENABLER0 to enable PPI 26 only when bit 26 is clear', 'Boot 12 (OBSERVED', 'ISENABLER0 0x4E800001', '`unit-boot12`',
                       'GIC OLD PRIO= ICFGR1= IGROUPR0= ISPEND= ISACT='):
            self.assertIn(needle, self.flat)
        for stale in ('something is enabled in ISENABLER0', 'the whole of ISENABLER0 must be clear', 'and no interrupt has been enabled',
                      'ISENABLER0 only when bit 26 is clear and clears only that bit', 'are accepted and left as found', 'clearing the other enabled bits', 'IIDR 0x4010438'):
            self.assertNotIn(stale, self.flat)
        for path in ('probes/sp11-stick-gic.c', 'probes/sp11-stick-gic.h', 'probes/sp11-stick-gic-dry.c'):
            self.assertNotIn('must be clear, not only', (ROOT / path).read_text(), path)

    def test_boot_13_and_the_quarantine_are_written_down_with_their_labels_and_the_unit_is_not_said_to_have_answered_u3(self):
        for needle in ('Boot 13 (OBSERVED, one unit, one boot', '`unit-boot13`', 'IIDR 0x401043B', 'INTID 27', 'ISPENDR0 0x08000000 before any store',
                       'INFERRED, until the survey reads CNTV_CTL_EL0', 'P3 an enabled, pending PPI is delivered to EL2', 'Still UNKNOWN: U3 proper',
                       'one write of ICENABLER0 with every bit of the found ISENABLER0 but bit 26', '0x4A800001', 'as the last write of the run, one ISENABLER0 write with exactly the bits the quarantine cleared',
                       'the pending state of those interrupts is never written', 'FOREIGN_PENDING carrying the INTID', 'the seeded stage `no-quarantine`',
                       'the quarantine\'s own writes (ICENABLER0 with the mask, the last ISENABLER0) are NOT exercised under QEMU', 'EXPERIMENTAL (the quarantine)'):
            self.assertIn(needle, self.flat)
        table = self.text.split('## 10.')[1].split('## 11.')[0]
        for row in ('| Q_NOT_HELD (22) |', '| FOREIGN_PENDING (23) |'):
            self.assertIn(row, table)
        self.assertEqual((G.OUTCOMES[22], G.OUTCOMES[23]), ('Q_NOT_HELD', 'FOREIGN_PENDING'))
        self.assertIn('still UNKNOWN after boot 13', self.flat, 'U3 is not answered by a run that ended on INTID 27')

    def test_timer_owned_is_only_a_timer_asserting_now_and_the_put_back_order_is_written_down(self):
        for needle in ('IMASK clear (0X5): the timer is asserting right now', 'Other states are accepted, overwritten at the arm (CVAL, then CTL) and put back',
                       'T (CVAL, then CTL)', 'only for a timer asserting now'):
            self.assertIn(needle, self.flat)
        for stale in ('CNTHP_CTL.ENABLE is set', 'someone else uses the EL2 timer'):
            self.assertNotIn(stale, self.flat)
        for path in ('probes/sp11-stick-gic.c', 'probes/sp11-stick-gic.h', 'probes/sp11-stick-gic-dry.c'):
            self.assertNotIn('the monitor sets its own', (ROOT / path).read_text(), path)

    def test_the_tests_it_names_are_the_ones_that_exist(self):
        for name in ('test-sp11-stick-gic.py', 'test-sp11-stick-gic-quarantine.py', 'test-sp11-stick-gic-putback.py', 'test-sp11-stick-gic-hppir.py', 'test-sp11-stick-gic-doc.py',
                     'test-sp11-stick-gic-record.py', 'test-sp11-stick-gic-build.py', 'test-sp11-stick-gic-qemu.py'):
            self.assertIn(f'`tools/{name}`', self.text)
            self.assertTrue((ROOT / 'tools' / name).is_file())


if __name__ == '__main__':
    unittest.main()
