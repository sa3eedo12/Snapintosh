#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of the census as Phase D (D3) of monitor port work item 4b made it (docs/SP11-STICK-MONITOR.md section 14): it places every register, operation and
data word by function and count, ties both ELFs to the digests of section 1b, and refuses a data word that decodes as a system instruction unless the
record places it. The mutants are the second review's: ELF copies with one instruction word patched, the record's `elf sha256` line re-synced as the
reviewer did, each of which the name check alone accepts and the check now refuses.

    python3 tools/test-sp11-stick-monitor-census.py

The fixtures of tools/test-sp11-stick-monitor-auth.py are loaded with a scratch directory of their own (build/sp11-monitor-census-test-scratch/, deleted
at the end). The binaries tests need clang, ld.lld, the llvm tools and a q1n1 checkout ($Q1N1_SP11_TREE) and SKIP without them, which is not a pass; the
parser and decoder tests need no toolchain but clang for the assembled objects. No hardware: NOT RUN."""
import collections
import importlib.util
import re
import shutil
import struct
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))
import sp11_monitor_census as C  # noqa: E402
import sp11_stick_bundle_record_m1 as m1t  # noqa: E402

spec = importlib.util.spec_from_file_location('sp11_monitor_auth_tests', TOOLS / 'test-sp11-stick-monitor-auth.py')
AUTH = importlib.util.module_from_spec(spec)
spec.loader.exec_module(AUTH)
SCRATCH = ROOT / 'build' / 'sp11-monitor-census-test-scratch'
AUTH.SCRATCH = SCRATCH
BT, T, STATE, sha = AUTH.BT, AUTH.T, AUTH.STATE, AUTH.sha
# The reviewer's mutants: the word written over the first instruction of xnuboot_report, and what the census must then list there.
MUTANTS = {'msr TTBR0_EL2': (0xD51C2000, 'TTBR0_EL2'), 'msr SCTLR_EL2': (0xD51C1000, 'SCTLR_EL2'), 'msr TCR_EL2': (0xD51C2040, 'TCR_EL2'),
           'msr MAIR_EL2': (0xD51CA200, 'MAIR_EL2'), 'msr CPTR_EL2': (0xD51C1140, 'CPTR_EL2'), 'smc #0': (0xD4000003, 'SMC'),
           'tlbi alle1is': (0xD50C839F, 'TLBI ALLE1IS'), 'dc civac': (0xD50B7E20, 'DC CIVAC'), 'ic ialluis': (0xD508711F, 'IC IALLUIS'),
           'tlbi vmalls12e1is': (0xD50C83DF, 'TLBI VMALLS12E1IS')}
TABLE_WORD, NOT_A_SYSTEM_WORD, MSR_SCTLR_EL2_X0 = 0xD5184033, 0x12345678, 0xD51C1000


def setUpModule():
    shutil.rmtree(SCRATCH, ignore_errors=True)
    AUTH.setUpModule()


def tearDownModule():
    AUTH.tearDownModule()
    shutil.rmtree(SCRATCH, ignore_errors=True)


def sections(elf):
    """[(address, offset, size)] of the sections of `elf` that occupy file bytes."""
    out = subprocess.run([C.find_tool('llvm-readelf'), '-S', '--wide', str(elf)], capture_output=True, text=True, check=True).stdout
    return [(int(m.group(1), 16), int(m.group(2), 16), int(m.group(3), 16))
            for m in re.finditer(r'^\s*\[\s*\d+\]\s+\S+\s+PROGBITS\s+([0-9a-f]+)\s+([0-9a-f]+)\s+([0-9a-f]+)', out, re.M)]


def patch(elf, name, edits):
    """A copy of `elf` below the scratch directory with the 4-byte word at each address of `edits` replaced: its path."""
    data, secs = bytearray(Path(elf).read_bytes()), sections(elf)
    for address, word in edits.items():
        base, offset, _ = next(s for s in secs if s[0] <= address < s[0] + s[2])
        data[offset + address - base:offset + address - base + 4] = struct.pack('<I', word)
    return Path(BT.write(SCRATCH / 'mutants' / f'{name}.elf', bytes(data)))


def word_at(elf, address):
    base, offset, _ = next(s for s in sections(elf) if s[0] <= address < s[0] + s[2])
    return struct.unpack_from('<I', Path(elf).read_bytes(), offset + address - base)[0]


def table_of(cen, item):
    """The census table that holds `item`: a register that an MSR writes, a SYS operation (it has a space), or a plain instruction."""
    return cen['msr'] if item.endswith(('_EL1', '_EL2')) else cen['sys'] if ' ' in item else cen['plain']


def symbol(elf, name):
    out = subprocess.run([C.find_tool('llvm-nm'), '--defined-only', str(elf)], capture_output=True, text=True, check=True).stdout
    return next(int(line.split()[0], 16) for line in out.splitlines() if line.split()[-1] == name)


def resynced(text, elf):
    """The record text with the elf sha256 line naming `elf`, as the reviewer re-synced it."""
    return re.sub(r'(?m)^( {3}elf sha256\s+)[0-9a-f]{64}', lambda m: m.group(1) + sha(Path(elf).read_bytes()), text)


class Need(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not AUTH.tools_ready() or 'm1' not in STATE:
            raise unittest.SkipTest('no clang, ld.lld, llvm tools or q1n1 checkout (Q1N1_SP11_TREE): the test monitor is not built')
        cls.monitor_elf, cls.wrapper_elf = STATE['profile'] / 'xnuboot.elf', SCRATCH / 'm1' / 'snapmon-m1.elf'
        cls.monitor, cls.wrapper, cls.text = C.census(cls.monitor_elf), C.census(cls.wrapper_elf), STATE['real_text']
        cls.elf_sha = sha(cls.monitor_elf.read_bytes())

    def check(self, text=None, monitor=None, wrapper=None, **kw):
        return C.check(self.text if text is None else text, self.monitor if monitor is None else monitor, self.wrapper if wrapper is None else wrapper,
                       kw.get('elf', self.elf_sha), kw.get('monitor_flat'), kw.get('wrapper_flat'))


class Places(Need):
    """(a) The record places every register, operation and word by function and count; a write that moves or appears is a failure."""

    def test_the_record_places_everything_both_binaries_hold_and_the_ties_hold(self):
        self.assertEqual(self.check(monitor_flat=C.flat_bytes(self.monitor_elf), wrapper_flat=C.flat_bytes(self.wrapper_elf)), [])

    def test_the_placed_entries_are_what_the_tool_prints_for_the_binaries(self):
        """The literal lines of tools/sp11_stick_bundle_record_m1.py are the tool's output, so that a regenerated block is the one the record carries."""
        entries = [line for line in m1t.PLACED_LINES[next(i for i, line in enumerate(m1t.PLACED_LINES) if re.match(r'   (wrapper|monitor) ', line)):]]
        wanted = [line[6:] for line in C.placed_lines(self.wrapper, 'wrapper') + C.placed_lines(self.monitor, 'monitor')]
        self.assertEqual(entries, wanted)
        self.assertTrue(all(len(line) <= 112 for line in C.placed_lines(self.monitor, 'monitor')))

    def test_every_register_operation_and_word_is_placed_and_the_record_places_nothing_else(self):
        placed, problems = C.parse_placed(self.text)
        self.assertEqual(problems, [])
        for binary, cen in (('monitor', self.monitor), ('wrapper', self.wrapper)):
            held = {*cen['msr'], *cen['sys'], *cen['plain']} | ({'data words'} if cen['data_system'] else set())
            self.assertEqual(set(placed[binary]), held, binary)
        self.assertGreater(len(placed['monitor']), 80)
        self.assertEqual(placed['monitor']['data words'], collections.Counter({'0xd5184002': 3, '0xd518402c': 1, '0xd5184032': 1, '0xd5184033': 1}))

    def test_the_second_reviews_mutants_pass_the_name_check_and_fail_the_placed_check(self):
        report = symbol(self.monitor_elf, 'xnuboot_report')
        for name, (word, item) in MUTANTS.items():
            with self.subTest(name):
                elf = patch(self.monitor_elf, name.replace(' ', '_').replace('#', ''), {report: word})
                cen = C.census(elf)
                self.assertGreater(table_of(cen, item)[item]['xnuboot_report'], table_of(self.monitor, item)[item].get('xnuboot_report', 0),
                                   'the patched word is the instruction the mutant names')
                self.assertEqual(C.compare(cen, [t for g, t in C.groups_of(self.text).items() if g.startswith('monitor ')], 'the monitor'), [],
                                 'the old, name-level check accepts it: the record names the item for other functions')
                problems = self.check(resynced(self.text, elf), monitor=cen, elf=sha(elf.read_bytes()))
                self.assertTrue([p for p in problems if item in p and 'xnuboot_report' in p], (name, problems[:3]))

    def test_a_read_turned_into_a_write_in_the_wrapper_is_found_where_the_names_cannot_see_it(self):
        rows, _, _ = C.parse(C.disassemble(self.wrapper_elf))
        function, address, _, _ = next(r for r in rows if r[2] == 'mrs' and r[3].endswith('SCTLR_EL2'))
        elf = patch(self.wrapper_elf, 'wrapper_msr', {address: word_at(self.wrapper_elf, address) ^ (1 << 21)})
        cen = C.census(elf)
        self.assertIn(function, cen['msr']['SCTLR_EL2'], 'bit 21 turns MRS into MSR')
        self.assertEqual(C.compare(cen, [C.groups_of(self.text)['wrapper']], 'the wrapper'), [], 'the wrapper group names SCTLR_EL2 as a read: the names pass')
        problems = self.check(wrapper=cen)
        self.assertTrue([p for p in problems if 'the wrapper: holds SCTLR_EL2' in p and 'does not place it' in p], problems)

    def test_a_register_the_record_places_elsewhere_or_twice_or_not_at_all_fails(self):
        for edit, wanted in ((lambda t: t.replace('monitor TTBR0_EL1: user_root_install, vcpu_arch_prepare, xnuboot_report',
                                                  'monitor TTBR0_EL1: user_root_install, vcpu_arch_prepare, xnuboot_setup'), 'xnuboot_setup'),
                             (lambda t: t.replace('monitor DC CVAU: monitor_call_impl*4', 'monitor DC CVAU: monitor_call_impl*3'), 'held 4 times in monitor_call_impl'),
                             (lambda t: t.replace('wrapper IC IALLUIS: sp11hw_ic_ialluis', 'wrapper IC IALLUIS: sp11hw_ic_ialluis*2'), 'places it 2'),
                             (lambda t: re.sub(r'(?m)^ {9}monitor WFE: .*\n', '', t), 'holds WFE'),
                             (lambda t: t.replace('monitor WFE: early_off', 'monitor WFE, DC ZVA: early_off'), 'places DC ZVA')):
            with self.subTest(wanted):
                problems = self.check(edit(self.text))
                self.assertNotEqual(edit(self.text), self.text)
                self.assertTrue([p for p in problems if wanted in p], (wanted, problems[:3]))


class DataWords(Need):
    """(c) A word that llvm-objdump shows as data and that decodes as a system instruction fails unless the record places it; tables stay allowed."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cells = C.data_cells(C.disassemble(cls.monitor_elf))

    def test_the_monitors_own_table_is_six_msr_words_and_the_record_places_them(self):
        self.assertEqual(sorted(word for _, _, word, _ in self.monitor['data_system']),
                         sorted([0xD5184033, 0xD5184032, 0xD5184002, 0xD5184002, 0xD518402C, 0xD5184002]))
        self.assertEqual({name for *_, name in self.monitor['data_system']}, {'MSR (register)'})
        self.assertEqual(self.wrapper['data_system'], [])

    def test_a_new_msr_data_word_is_a_failure_and_the_old_check_exits_zero_on_it(self):
        _, address, _, _ = next(c for c in self.cells if c[2] == 4 and C.system_class(c[3]) is None)
        elf = patch(self.monitor_elf, 'data_msr', {address: MSR_SCTLR_EL2_X0})
        cen = C.census(elf)
        self.assertEqual([w for _, _, w, _ in cen['data_system']].count(MSR_SCTLR_EL2_X0), 1)
        self.assertEqual(cen['msr'].keys(), self.monitor['msr'].keys(), 'as an instruction it is not counted: it is data')
        problems = self.check(resynced(self.text, elf), monitor=cen, elf=sha(elf.read_bytes()))
        self.assertTrue([p for p in problems if 'the data words' in p and '0xd51c1000' in p], problems)

    def test_a_changed_or_missing_table_word_is_a_failure_too(self):
        by_value = {c[3]: c[1] for c in self.cells if c[2] == 4}
        for name, word in (('changed', 0xD5184034), ('gone', 0)):
            with self.subTest(name):
                elf = patch(self.monitor_elf, f'table_{name}', {by_value[TABLE_WORD]: word})
                problems = self.check(resynced(self.text, elf), monitor=C.census(elf), elf=sha(elf.read_bytes()))
                self.assertTrue([p for p in problems if 'the data words' in p], problems)

    def test_tables_stay_allowed(self):
        _, address, _, _ = next(c for c in self.cells if c[2] == 4 and C.system_class(c[3]) is None)
        elf = patch(self.monitor_elf, 'data_plain', {address: NOT_A_SYSTEM_WORD})
        self.assertEqual(self.check(resynced(self.text, elf), monitor=C.census(elf), elf=sha(elf.read_bytes())), [])


class Ties(Need):
    """(b) The ELFs checked are the binaries staged: their flat bytes are the wrapper and the monitor that section 1b names."""

    def test_a_wrapper_elf_that_is_not_the_staged_one_fails(self):
        flat_monitor = C.flat_bytes(self.monitor_elf)
        for name, elf in (('a patched copy', patch(self.wrapper_elf, 'wrapper_copy', {symbol(self.wrapper_elf, 'sw_probe_range'): 0xD503201F})),
                          ('another ELF', SCRATCH / 'm1' / 'snaprep-m1.elf')):
            with self.subTest(name):
                problems = C.tie_problems(self.text, flat_monitor, C.flat_bytes(elf))
                self.assertTrue([p for p in problems if 'the wrapper ELF are not the wrapper that section 1b' in p], problems)
        self.assertEqual(C.tie_problems(self.text, flat_monitor, C.flat_bytes(self.wrapper_elf)), [], 'the control')

    def test_a_monitor_elf_that_is_not_the_staged_one_fails_by_its_flat_bytes_even_with_its_elf_line_re_synced(self):
        elf = patch(self.monitor_elf, 'monitor_copy', {symbol(self.monitor_elf, 'xnuboot_report'): 0xD503201F})
        problems = self.check(resynced(self.text, elf), monitor=C.census(elf), elf=sha(elf.read_bytes()), monitor_flat=C.flat_bytes(elf))
        self.assertTrue([p for p in problems if 'flat bytes of the monitor ELF are not the monitor that section 1b' in p], problems)
        self.assertFalse([p for p in problems if 'not the one section 1b' in p], 'the elf line was re-synced: only the flat bytes say it')

    def test_the_command_line_needs_the_wrapper_elf_and_ties_it(self):
        record = BT.write(SCRATCH / 'records' / 'real.txt', self.text.encode())
        base = ['check', '--monitor-elf', str(self.monitor_elf)]
        code, out, err = AUTH.quietly(C.main, [*base, '--wrapper-elf', str(self.wrapper_elf), record])
        self.assertEqual((code, err), (0, ''), out)
        self.assertIn('CHECK PASS', out)
        code, out, err = AUTH.quietly(C.main, [*base, record])
        self.assertEqual(code, 1)
        self.assertIn('PROBLEM the wrapper was not checked: give --wrapper-elf', out)
        code, out, err = AUTH.quietly(C.main, [*base, '--wrapper-elf', str(SCRATCH / 'm1' / 'snaprep-m1.elf'), record])
        self.assertEqual(code, 1)
        self.assertIn('flat bytes of the wrapper ELF are not the wrapper that section 1b', out)
        code, out, err = AUTH.quietly(C.main, ['check', '--monitor-elf', str(SCRATCH / 'absent.elf'), '--wrapper-elf', str(self.wrapper_elf), record])
        self.assertEqual(code, 2)
        self.assertIn('CANNOT RUN', err)

    def test_the_tool_prints_the_entries_of_one_binary(self):
        code, out, err = AUTH.quietly(C.main, ['placed', '--binary', 'wrapper', str(self.wrapper_elf)])
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(out.splitlines(), C.placed_lines(self.wrapper, 'wrapper'))


class Parse(unittest.TestCase):
    """The placed entries of a record, on texts made here."""
    HEAD = '   el2_sysreg: x\n      wrapper: a\n      placed by function: the prose\n         is not an entry\n'
    TAIL = '      monitor A, y\n   gic: z\n'

    def parse(self, entries):
        return C.parse_placed(self.HEAD + entries + self.TAIL)

    def test_entries_wrap_on_twelve_spaces_and_share_their_items(self):
        placed, problems = self.parse('         wrapper A, B C: f, g*2,\n            h\n         monitor D: k\n')
        self.assertEqual((problems, placed['wrapper']['A'], placed['wrapper']['B C'], dict(placed['monitor']['D'])),
                         ([], collections.Counter({'f': 1, 'g': 2, 'h': 1}), collections.Counter({'f': 1, 'g': 2, 'h': 1}), {'k': 1}))

    def test_a_malformed_or_repeated_entry_is_reported(self):
        for entries, wanted in (('         wrapper A f, g\n', 'does not read'), ('         wrapper A: f*x\n', 'does not parse'), ('         wrapper A: f, , g\n', 'does not parse'),
                                ('         wrapper A: f\n         monitor A: g\n         wrapper A: h\n', 'places A twice under the wrapper')):
            with self.subTest(entries):
                self.assertTrue([p for p in self.parse(entries)[1] if wanted in p], self.parse(entries)[1])

    def test_entries_after_another_group_or_class_are_not_placed(self):
        placed, _ = C.parse_placed(self.HEAD + '         wrapper A: f\n' + self.TAIL + '         monitor B: g\n')
        self.assertEqual((set(placed['wrapper']), set(placed['monitor'])), ({'A'}, set()))


class Decode(unittest.TestCase):
    """The decoder, on objects assembled here: data words in a text section, as the monitor's table is, and real instructions."""
    SOURCE = ('.text\n.globl f\nf:\n nop\n msr sctlr_el2, x0\n mrs x1, sctlr_el2\n smc #0\n ret\n.word 0xd51c1000\n.word 0x12345678\n.word 0xd4000003\n'
              '.word 0xd69f03e0\n.word 0xd503205f\n.word 0xd5033fdf\n.byte 0x00, 0x10, 0x1c, 0xd5\n.word 0xd508871f\n.word 0xd4000002\n.word 0xd53c1000\n')

    @classmethod
    def setUpClass(cls):
        if not T.find_tool('clang') or not T.find_tool('llvm-objdump'):
            raise unittest.SkipTest('no clang or llvm-objdump')
        BT.write(SCRATCH / 'decode' / 'x.S', cls.SOURCE.encode())
        T.clang(['--target=aarch64-unknown-none-elf', '-c', str(SCRATCH / 'decode' / 'x.S'), '-o', str(SCRATCH / 'decode' / 'x.o')], 'assembling the decode object')
        cls.cen = C.census(SCRATCH / 'decode' / 'x.o')

    def test_instructions_are_counted_by_register_operation_and_function(self):
        self.assertEqual(dict(self.cen['msr']['SCTLR_EL2']), {'f': 1})
        self.assertEqual(dict(self.cen['mrs']['SCTLR_EL2']), {'f': 1})
        self.assertEqual(dict(self.cen['plain']['SMC']), {'f': 1})

    def test_data_words_are_decoded_by_class_and_a_plain_constant_is_not(self):
        names = {}
        for _, _, word, name in self.cen['data_system']:
            names.setdefault(f'{word:#010x}', name)
        for word, name in (('0xd51c1000', 'MSR (register)'), ('0xd4000003', 'SMC'), ('0xd69f03e0', 'ERET'), ('0xd503205f', 'WFE'), ('0xd508871f', 'SYS'),
                           ('0xd4000002', 'HVC')):
            self.assertEqual(names[word], name)
        for word, why in (('0x12345678', 'a constant that is no system instruction is a table entry, and tables stay allowed'),
                          ('0xd5033fdf', 'an ISB changes no register that the record must place'), ('0xd53c1000', 'an MRS reads')):
            self.assertNotIn(word, names, why)
        self.assertEqual([w for _, _, w, _ in self.cen['data_system']].count(0xD51C1000), 2, 'once as a word and once as four bytes')

    def test_the_class_table_decodes_the_words_the_brief_lists(self):
        for word, name in ((0xD51C1000, 'MSR (register)'), (0xD5033BBF, None), (0xD500401F, 'MSR (immediate)'), (0xD508711F, 'SYS'), (0xD52B0000, 'SYSL'),
                           (0xD503205F, 'WFE'), (0xD503207F, 'WFI'), (0xD503209F, 'SEV'), (0xD50320BF, 'SEVL'), (0xD4000001, 'SVC'), (0xD4000002, 'HVC'),
                           (0xD4000003, 'SMC'), (0xD69F03E0, 'ERET'), (0xD69F0BFF, 'ERETAA'), (0xD69F0FFF, 'ERETAB'), (0xD4200000, 'BRK'), (0xD4400000, 'HLT'),
                           (0xD4A00001, 'DCPS1'), (0xD503201F, None), (0x12345678, None), (0x00000000, None), (0xD65F03C0, None), (0xD5033FDF, None)):
            with self.subTest(f'{word:#x}'):
                self.assertEqual(C.system_class(word), name)


if __name__ == '__main__':
    unittest.main()
