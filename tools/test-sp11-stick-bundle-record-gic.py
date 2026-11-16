#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of the record text for pass 5 of the GIC and stop stage and of its coupling to the wrapper it describes (tools/sp11_stick_bundle_record_gic.py;
the builder option --gic-quarantine; docs/SP11-STICK-GIC.md sections 4, 7, 8 and 12): the decision of the two named refusals, the signal in the REAL
binaries (the pass 5 pair built here, the dry-run pair built here, and the pairs of the first two attempts where their files exist), the builder with
those files in both directions, the text itself (what it says, its numbers against the stage's headers, its labels, its width), and the census of the pass 5
wrapper's ELF against the text in both directions, with a text that lacks a name, a text that has one too many, and ELF copies with one more instruction.

    python3 tools/test-sp11-stick-bundle-record-gic.py

The fixtures of tools/test-sp11-stick-bundle-build.py are loaded here with a scratch directory of their own (build/sp11-record-gic-test-scratch/, deleted at
the end). The decision, the text and the hygiene need no toolchain. The pass 5 and dry-run binaries are built here with tools/sp11_stick_wrapper_build.py
and need clang, ld.lld, the llvm tools and a q1n1 checkout ($Q1N1_SP11_TREE); the pairs of the first two attempts are local files (build/sp11-gic/bin and
$SP11_GICDRY2_DIR/binA, each held to the digests of the builds that ran). What cannot run is SKIPPED, which is not a pass. No hardware: NOT RUN."""
import collections
import hashlib
import importlib.util
import os
import re
import shutil
import struct
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOLS, THIS = ROOT / 'tools', Path(__file__).resolve()
sys.path.insert(0, str(TOOLS))
import sp11_monitor_census as C  # noqa: E402
import sp11_stick_bundle_chunks as ch  # noqa: E402
import sp11_stick_bundle_record as rec  # noqa: E402
import sp11_stick_bundle_record_gic as gq  # noqa: E402
import sp11_stick_wrapper_build as wb  # noqa: E402
import sp11_stick_wrapper_testlib as T  # noqa: E402

spec = importlib.util.spec_from_file_location('sp11_stick_bundle_build_tests', TOOLS / 'test-sp11-stick-bundle-build.py')
BT = importlib.util.module_from_spec(spec)
spec.loader.exec_module(BT)
sb = BT.sb
SCRATCH = ROOT / 'build' / 'sp11-record-gic-test-scratch'
STATE = {}
LLVM = ('clang', 'ld.lld', 'llvm-objcopy', 'llvm-readelf', 'llvm-nm', 'llvm-objdump')
sha = lambda data: hashlib.sha256(data).hexdigest()     # noqa: E731
OWN = (TOOLS / 'sp11_stick_bundle_record_gic.py', THIS)
# The builds of the first two attempts, which ran on the unit: local files, held to their digests (wrapper, reporter) so that the proof is about them.
EARLIER = {'first attempt': ('build/sp11-gic/bin', '444329eedee71e680dba2db53a6286e89c84d903ef92bc59994a12a07067c582',
                             'c05bc954c8e051d1dd3e7ca10c1f535a16cc0ad1789ee841697a6f45ec755d15'),
           'second attempt': ('$SP11_GICDRY2_DIR/binA', '784d5275398c8150db60e67f9fe34b50c9f6a749fb9a567cc2d3d280a0c1e33a',
                              'f8df06a6f389eae58a4bd86b5f55dcd6371025d9755c085c08ce360c95eba9be')}
# The census of the pass 5 wrapper: the 26 MSR instructions of 15 registers that docs/SP11-STICK-GIC.md section 8 lists, and what else it executes.
MSR_COUNTS = {'VBAR_EL2': 5, 'SPSR_EL2': 4, 'ELR_EL2': 4, 'DAIFSet': 2, 'DAIFClr': 1, 'ICC_PMR_EL1': 1, 'ICC_BPR1_EL1': 1, 'ICC_IGRPEN1_EL1': 1, 'ICC_EOIR1_EL1': 1,
              'ICC_DIR_EL1': 1, 'CNTHP_CVAL_EL2': 1, 'CNTHP_CTL_EL2': 1, 'HCR_EL2': 1, 'VBAR_EL1': 1, 'SCTLR_EL1': 1}
SYS_OPS, PLAIN = {'DC CVAC', 'DC CIVAC', 'IC IVAU', 'AT S1E2W', 'AT S1E2R'}, {'ERET', 'HVC'}
NEW_READS = ('ICC_HPPIR1_EL1', 'CNTV_CTL_EL0', 'CNTV_CVAL_EL0', 'CNTP_CTL_EL0', 'CNTP_CVAL_EL0', 'ICC_IAR1_EL1')
# The names the text says the binary does not hold: registers it does not write, and instructions it does not execute.
NOT_WRITTEN, NOT_EXECUTED = ('CNTHCTL_EL2', 'TPIDR_EL2'), ('SMC', 'SVC', 'TLBI', 'WFI', 'WFE')
# The census tool's name pattern has no DAIFClr (the wrapper of this stage is the first binary that has an MSR to it): the text must name it, so the pattern is widened.
WIDENED = re.compile(C.REGISTER.pattern.replace('|DAIFSet|', '|DAIFSet|DAIFClr|', 1))
ISB = struct.pack('<I', 0xD5033FDF)
# Instruction words for the ELF copies: msr tpidr_el2, x0; tlbi vmalle1; smc #0; wfi.
EXTRA = {'msr tpidr_el2': (0xD51CD040, 'TPIDR_EL2'), 'tlbi vmalle1': (0xD508871F, 'TLBI VMALLE1'), 'smc #0': (0xD4000003, 'SMC'), 'wfi': (0xD503207F, 'WFI')}
FIXED = dict(place=sb.placement(BT.BANK, 384 * BT.MIB, 48 * BT.MIB), stores=7, time_box_s=5, label='GICDRY-C', monitor=(98236, '1' * 64), reporter=(62000, '2' * 64),
             note=None, gic_stop=True, gic_quarantine=True, guest=dict(size=118870336, sha256='3' * 64, chunks=44, index='4' * 64))
# The SHA-256 of record_text for FIXED: the text is what the operator approves by its digest, so a changed byte is another record.
GOLDEN = '16bcbe619c72c1730f85dcf16537aeb983e0fee52cc1c7cef8cafdb9fe5e7165'
LOOKALIKES = ('ASSUMED CONFIRMED MEASURED PROVEN PROVED TESTED VALIDATED ESTABLISHED HYPOTHESIS GUESSED LIKELY SUSPECTED UNVERIFIED PRESUMED PROBABLE CERTAIN '
              'DEMONSTRATED QUALIFIED ASSERTED CLAIMED OBVIOUS TRIVIAL').split()


def tools_ready():
    return bool(STATE.get('tree') and all(T.find_tool(name) for name in LLVM))


def setUpModule():
    shutil.rmtree(SCRATCH, ignore_errors=True)
    BT.SCRATCH = SCRATCH / 'bt'
    BT.setUpModule()
    STATE['tree'] = T.q1n1_tree()
    if tools_ready():
        default = str(ROOT / 'config/monitor-profile-sp11-m1.json')
        STATE['gic'] = wb.build(str(SCRATCH / 'gic'), default, False, True)
        STATE['plain'] = wb.build(str(SCRATCH / 'plain'), default)


def tearDownModule():
    shutil.rmtree(SCRATCH, ignore_errors=True)
    BT.tearDownModule()


def render(**over):
    return rec.record_text({**FIXED, **over})


def template(monitor, reporter, *extra, stores='ram,el2_sysreg,gic'):
    """(code, stdout, stderr) of authorisation-template synthetic for these two files, with the builder's fixtures for everything else."""
    return BT.cli(['authorisation-template', 'synthetic', *BT.opts('synthetic', monitor=str(monitor), reporter=str(reporter), stores=stores), *extra])


def earlier_pairs():
    """{name: (wrapper, reporter, ELF)} of the earlier attempts whose local files are there and are the builds that ran."""
    found = {}
    for name, (where, wrapper_sha, reporter_sha) in EARLIER.items():
        directory = Path(where.replace('$SP11_GICDRY2_DIR', os.environ.get('SP11_GICDRY2_DIR') or '/nonexistent')) if where.startswith('$') else ROOT / where
        paths = [directory / n for n in ('SNAPMON-GIC.BIN', 'SNAPREP-GIC.BIN', 'snapmon-gic.elf')]
        if all(p.is_file() for p in paths) and (sha(paths[0].read_bytes()), sha(paths[1].read_bytes())) == (wrapper_sha, reporter_sha):
            found[name] = tuple(paths)
    return found


def problems_of(census, text, what='the GIC wrapper'):
    """C.compare of one binary's census with one text, with the name pattern that knows DAIFClr."""
    with mock.patch.object(C, 'REGISTER', WIDENED):
        return C.compare(census, [text], what)


def named_text(record):
    """Section 3 of a rendered record without the lines that say what the binary does not hold (those are checked against the census separately)."""
    body = record.split('3. Store classes', 1)[1].split('   Not authorised', 1)[0]
    for line in gq.EL2_NOT:
        assert f'      {line}\n' in body, line
        body = body.replace(f'      {line}\n', '')
    return body


def negation_problems(census):
    return [f'writes {n}' for n in NOT_WRITTEN if n in census['msr']] + [f'executes {n}' for n in NOT_EXECUTED if any(k.split()[0] == n for k in census['sys']) or n in census['plain']]


def omit(text, name):
    return re.sub(rf'(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])', '', text)


def msr_counts(elf):
    rows, _, _ = C.parse(C.disassemble(elf))
    return collections.Counter(C.register_name(operands.split(',')[0]) for _, _, mnemonic, operands in rows if mnemonic == 'msr')


def patched(elf, word, name):
    """A copy of `elf` in the scratch directory with its first aligned `isb` replaced by `word`."""
    data = bytearray(Path(elf).read_bytes())
    at = next(i for i in range(0, len(data) - 4, 4) if data[i:i + 4] == ISB)
    data[at:at + 4] = struct.pack('<I', word)
    return BT.write(SCRATCH / 'mutants' / name, bytes(data))


class Need(BT.Case):
    """Base of the tests that need the toolchain and a q1n1 checkout: the pass 5 and dry-run binaries are built in setUpModule."""

    @classmethod
    def setUpClass(cls):
        if not tools_ready() or 'gic' not in STATE:
            raise unittest.SkipTest('no clang, ld.lld, llvm tools or q1n1 checkout (Q1N1_SP11_TREE): the pass 5 wrapper is not built')
        cls.gic, cls.plain = SCRATCH / 'gic', SCRATCH / 'plain'
        cls.wrapper, cls.reporter, cls.elf = cls.gic / 'SNAPMON-GIC.BIN', cls.gic / 'SNAPREP-GIC.BIN', cls.gic / 'snapmon-gic.elf'
        cls.dry = (cls.plain / 'SNAPMON.BIN', cls.plain / 'SNAPREP.BIN')


# ---- the decision ------------------------------------------------------------------------------------------------------------------------
class Decision(BT.Case):
    def outcome(self, call, *args):
        try:
            call(*args)
        except ch.Refused as error:
            return error.name
        return None

    def test_the_two_refusals_and_the_cases_that_pass(self):
        both, one, none = b'..Q_NOT_HELD..FOREIGN_PENDING..', b'..FOREIGN_PENDING..', b'a wrapper with neither'
        table = [(both, False, True, 'GIC_QUARANTINE_REQUIRED'), (both, True, True, None), (both, False, False, None), (one, True, True, 'GIC_QUARANTINE_ABSENT'),
                 (none, True, True, 'GIC_QUARANTINE_ABSENT'), (one, False, True, None), (none, False, True, None), (b'', False, False, None)]
        for image, quarantine, authorised, expected in table:
            with self.subTest(image=image, quarantine=quarantine, authorised=authorised):
                self.assertEqual(self.outcome(gq.check_wrapper, image, quarantine, authorised), expected)

    def test_the_option_needs_gic_stop(self):
        self.assertEqual(self.outcome(gq.check_options, False, True), 'OPTIONS_BAD')
        for gic_stop, quarantine in ((True, True), (True, False), (False, False)):
            self.assertIsNone(self.outcome(gq.check_options, gic_stop, quarantine))

    def test_the_names_are_known_to_the_builder_and_the_option_is_a_builder_option(self):
        self.assertTrue(set(gq.REFUSALS) <= ch.KNOWN_REFUSALS)
        self.assertEqual(len(set(gq.REFUSALS)), 2)
        argv = ['authorisation-template', 'synthetic', '--facts', 'f', '--base', '0x1', '--guest-mib', '1', '--monitor', 'm', '--reporter', 'r', '--stores', 'ram',
                '--time-box-s', '1', '--gic-stop']
        parser = BT.bb.build_parser()
        self.assertEqual([parser.parse_args(argv + extra).gic_quarantine for extra in ([], [gq.OPTION])], [False, True])


# ---- the signal in the real binaries -----------------------------------------------------------------------------------------------------
class Signal(Need):
    def test_both_names_are_in_the_pass_5_pair_and_in_neither_of_the_dry_run_pair(self):
        for path in (self.wrapper, self.reporter):
            for name in gq.SIGNAL:
                self.assertIn(name, path.read_bytes(), (path.name, name))
        self.assertTrue(gq.is_pass5_wrapper(self.wrapper.read_bytes()))
        for path in self.dry:
            for name in gq.SIGNAL:
                self.assertNotIn(name, path.read_bytes(), (path.name, name))
            self.assertFalse(gq.is_pass5_wrapper(path.read_bytes()))

    def test_neither_name_is_in_the_wrapper_or_reporter_of_the_first_two_attempts(self):
        pairs = earlier_pairs()
        if not pairs:
            self.skipTest('the local files of the earlier attempts are absent or are not the builds that ran (build/sp11-gic/bin, $SP11_GICDRY2_DIR/binA)')
        for name, (wrapper, reporter, _) in pairs.items():
            for path in (wrapper, reporter):
                for signal in gq.SIGNAL:
                    self.assertNotIn(signal, path.read_bytes(), (name, path.name, signal))
            self.assertFalse(gq.is_pass5_wrapper(wrapper.read_bytes()), name)

    def test_the_signal_needs_both_names(self):
        data = self.wrapper.read_bytes()
        for name in gq.SIGNAL:
            self.assertGreaterEqual(data.count(name), 1)
            self.assertFalse(gq.is_pass5_wrapper(data.replace(name, b'X' + name[1:])), name)


# ---- the builder, both directions, with the real files -----------------------------------------------------------------------------------------
class Wiring(Need):
    def test_the_pass_5_wrapper_is_refused_when_the_gic_class_is_authorised_and_the_text_is_not_the_variant(self):
        self.refused('GIC_QUARANTINE_REQUIRED', template(self.wrapper, self.reporter, '--gic-stop'))
        self.refused('GIC_QUARANTINE_REQUIRED', template(self.wrapper, self.reporter))

    def test_the_option_is_refused_with_a_wrapper_that_is_not_pass_5_and_without_gic_stop(self):
        self.refused('GIC_QUARANTINE_ABSENT', template(self.dry[0], self.dry[1], '--gic-stop', gq.OPTION))
        self.refused('OPTIONS_BAD', template(self.wrapper, self.reporter, gq.OPTION))
        self.refused('OPTIONS_BAD', template(self.wrapper, self.reporter, gq.OPTION, '--m1-run', str(SCRATCH / 'absent.json')))

    def test_the_pass_5_pair_with_the_option_gets_the_variant_text_twice_the_same_and_the_other_kinds_keep_theirs(self):
        first = template(self.wrapper, self.reporter, '--gic-stop', gq.OPTION)
        self.assertEqual(first[0], 0, first[2])
        self.assertEqual(first, template(self.wrapper, self.reporter, '--gic-stop', gq.OPTION), 'two runs, the same bytes')
        text = ' '.join(first[1].split())
        for needle in (f'{len(self.wrapper.read_bytes())} bytes sha256 {sha(self.wrapper.read_bytes())}', '1456 bytes (1376 before pass 5', 'Q_NOT_HELD (22, OUTCOME=0X16)'):
            self.assertIn(needle, text)
        self.assertNotIn('its record, 1376 bytes', text)
        plain = template(self.dry[0], self.dry[1], stores='ram,el2_sysreg')
        self.assertEqual(plain[0], 0, plain[2])
        self.assertNotIn('Q_NOT_HELD', plain[1])
        self.assertIn('the dry-run wrapper', plain[1])

    def test_a_pass_5_wrapper_under_a_record_that_authorises_no_gic_class_is_not_refused(self):
        code, text, err = template(self.wrapper, self.reporter, stores='ram,el2_sysreg')
        self.assertEqual(code, 0, err)
        self.assertNotIn('Q_NOT_HELD', text)

    def test_the_reporter_slot_is_not_coupled_to_the_option(self):
        code, text, err = template(self.wrapper, self.dry[1], '--gic-stop', gq.OPTION)
        self.assertEqual(code, 0, err)
        self.assertIn(sha(self.dry[1].read_bytes()), text)


class Earlier(BT.Case):
    def test_the_option_is_refused_with_each_earlier_wrapper_and_without_it_the_text_is_the_old_one(self):
        pairs = earlier_pairs()
        if not pairs:
            self.skipTest('the local files of the earlier attempts are absent or are not the builds that ran (build/sp11-gic/bin, $SP11_GICDRY2_DIR/binA)')
        for name, (wrapper, reporter, _) in pairs.items():
            with self.subTest(name):
                self.refused('GIC_QUARANTINE_ABSENT', template(wrapper, reporter, '--gic-stop', gq.OPTION))
                code, text, err = template(wrapper, reporter, '--gic-stop')
                self.assertEqual(code, 0, err)
                self.assertIn('its record, 1376 bytes', ' '.join(text.split()))
                self.assertNotIn('Q_NOT_HELD', text)


# ---- the text ------------------------------------------------------------------------------------------------------------------------------
class Text(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.new, cls.old = render(), render(gic_quarantine=False)
        cls.flat, cls.old_flat = ' '.join(cls.new.split()), ' '.join(cls.old.split())

    def test_the_variant_says_what_pass_5_reads_writes_and_checks_and_the_earlier_text_does_not(self):
        for needle in ('1456 bytes (1376 before pass 5', 'version 3', 'GICD_ISENABLER[n] and GICD_ISPENDR[n] for n = 1 to the ITLinesNumber', 'ICENABLER0, once',
                       '0x4E800001', '0x4A800001', 'by ONE write of ISENABLER0', 'the last register write of the stage', 'must be exactly PPI 26\'s bit as found',
                       'Q_NOT_HELD (22, OUTCOME=0X16)', 'FOREIGN_PENDING (23, OUTCOME=0X17)', 'only INTID 1023 (none) or 26 may be next',
                       'CNTV_CTL_EL0, CNTV_CVAL_EL0, CNTP_CTL_EL0 and CNTP_CVAL_EL0', 'ICC_IAR1_EL1', 'DAIFClr', 'The pending state of those interrupts is never written'):
            with self.subTest(needle):
                self.assertIn(needle, self.flat)
                self.assertNotIn(needle, self.old_flat)
        self.assertNotIn('its record, 1376 bytes', self.flat)
        self.assertIn('its record, 1376 bytes', self.old_flat)

    def test_the_numbers_it_states_are_the_codes_of_the_stage(self):
        header = (ROOT / 'probes/sp11-stick-gic-record.h').read_text() + (ROOT / 'probes/sp11-stick-gic.h').read_text()
        number = lambda pattern: int(re.search(pattern, header).group(1), 0)       # noqa: E731
        q, f, ok, last = (number(rf'X\({n}, (\d+)\)') for n in ('Q_NOT_HELD', 'FOREIGN_PENDING', 'IRQ_OK', 'NOT_RESTORED'))
        self.assertEqual(number(r'#define SG_REC_BYTES (\d+)u'), 1456)
        self.assertEqual(number(r'#define SG_REC_VERSION (\d+)u'), 3)
        self.assertIn('SG_REC_AT == 0x11000u', header)
        self.assertEqual((number(r'SG_OUT_IS_REFUSAL\(o\) \(\(o\) >= (\d+)'), number(r'SG_OUT_IS_REFUSAL\(o\) \(\(o\) >= \d+ && \(o\) <= (\d+)'),
                          number(r'SG_OUT_IS_VERDICT\(o\) \(\(o\) >= (\d+)')), (1, 15, 32))
        self.assertEqual((q, f, ok, last), (22, 23, 32, 48))
        for needle in (f'Q_NOT_HELD ({q}, OUTCOME=0X{q:X})', f'FOREIGN_PENDING ({f}, OUTCOME=0X{f:X})', f'Q_NOT_HELD 0X{q:X}, FOREIGN_PENDING 0X{f:X}', f'IRQ_OK 0X{ok:X}',
                       f'NOT_RESTORED 0X{last:X}', '0X1 to 0XF a refusal', '0X10 to 0X1F', '0X20 to 0X2F', 'version 3', '1456 bytes', 'offset 0x11000', '2 s in the default build',
                       'and 8 s', 'ICC_PMR_EL1 0xF0, ICC_BPR1_EL1 0, ICC_IGRPEN1_EL1 1', 'IPRIORITYR byte (0x80 if it differs)', 'the IPRIORITYR byte of INTID 26',
                       'the IPRIORITYR bytes of INTIDs 23, 25, 27 and 30'):
            self.assertIn(needle, self.flat)
        self.assertEqual(0x4E800001 & ~(1 << 26), 0x4A800001)
        self.assertEqual([number(rf'SG_SURVEY_PPI{i} (\d+)u') for i in range(4)], [23, 25, 27, 30])
        self.assertEqual((number(r'SW_GIC_WAIT_S (\d+)u'), number(r'SP11_STICK_GIC_TIME_BOX_S (\d+)u'), number(r'SW_GIC_PRIORITY (0x[0-9A-Fa-f]+)u'),
                          number(r'SW_GIC_PMR (0x[0-9A-Fa-f]+)u')), (8, 2, 0x80, 0xF0))

    def test_the_facts_carry_their_labels_and_a_hardware_result_is_never_called_verified(self):
        for fact in ('ISENABLER0 0x4E800001 (boot 12)', 'ISPENDR0 0x08000000 (boot 13)', 'Virtual EL1 GSIV 27', 'GICD_CTLR written 0x13 read back 0x12 (boot 13)',
                     'an enabled, pending PPI is delivered to EL2'):
            self.assertIn(fact, self.flat)
        observed = self.flat.split('OBSERVED, one unit, one boot each', 1)[1].split('INFERRED:', 1)[0]
        self.assertRegex(observed, r'boot 12 from a photograph.*boot 13 from one video')
        self.assertRegex(self.flat, r'INFERRED: that INTID 27 is the EL1 virtual timer asserting \(CNTV_CTL_EL0\)')
        self.assertRegex(self.flat, r'UNKNOWN: whether PPI 26 itself reaches EL2')
        unlabelled = r'VERIFIED(?! on the host)'
        self.assertEqual(re.findall(unlabelled, self.new), [])
        self.assertTrue(re.findall(unlabelled, self.new.replace('VERIFIED on the host', 'VERIFIED on the unit')), 'the check can fail')
        self.assertFalse([w for w in LOOKALIKES if re.search(rf'\b{w}\b', self.new)])

    def test_the_lines_fit_the_text_is_ascii_deterministic_and_pinned(self):
        self.assertTrue(self.new.isascii())
        section = self.new.split('3. Store classes', 1)[1].split('   Not authorised', 1)[0]
        self.assertLessEqual(max(map(len, section.splitlines())), 120)       # wrapped by hand, so that no library version changes the digest
        self.assertEqual(self.new, render())
        self.assertEqual(sha(self.new.encode()), GOLDEN)

    def test_only_a_gic_stop_record_can_carry_the_variant(self):
        with self.assertRaises(AssertionError):
            render(gic_stop=False)
        self.assertNotIn('Q_NOT_HELD', render(gic_stop=False, gic_quarantine=False))


# ---- the census ------------------------------------------------------------------------------------------------------------------------
class Census(Need):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cen = C.census(cls.elf)
        cls.record = render()
        cls.named = named_text(cls.record)

    def test_the_text_names_what_the_binary_writes_and_executes_and_nothing_it_lacks(self):
        self.assertEqual(problems_of(self.cen, self.named), [])

    def test_the_census_has_the_numbers_the_text_states(self):
        self.assertEqual(dict(msr_counts(self.elf)), MSR_COUNTS)
        self.assertEqual((sum(MSR_COUNTS.values()), len(MSR_COUNTS)), (26, 15))
        self.assertEqual((set(self.cen['msr']), set(self.cen['sys']), set(self.cen['plain'])), (set(MSR_COUNTS), SYS_OPS, PLAIN))
        self.assertEqual(self.cen['undecoded'], 0)
        self.assertIn('26 MSR instructions of the same 15 registers', ' '.join(self.record.split()))

    def test_a_text_without_any_one_name_fails_naming_it(self):
        for name in (*sorted(MSR_COUNTS), *sorted(SYS_OPS), *sorted(PLAIN)):
            with self.subTest(name):
                problems = problems_of(self.cen, omit(self.named, name))
                self.assertTrue([p for p in problems if name in p and 'does not name it' in p], (name, problems[:3]))

    def test_a_read_that_acknowledges_an_interrupt_must_be_named(self):
        self.assertIn('ICC_IAR1_EL1', self.cen['mrs'])
        problems = problems_of(self.cen, omit(self.named, 'ICC_IAR1_EL1'))
        self.assertTrue([p for p in problems if 'ICC_IAR1_EL1' in p and 'acknowledges an interrupt' in p], problems)

    def test_a_name_the_binary_lacks_fails_naming_it(self):
        for added, kind in (('TTBR1_EL2', 'neither writes nor reads'), ('CNTV_TVAL_EL0', 'neither writes nor reads'), ('TLBI ALLE1', 'does not execute'),
                            ('DC ZVA', 'does not execute'), ('SMC', 'does not execute'), ('WFI', 'does not execute')):
            with self.subTest(added):
                problems = problems_of(self.cen, self.named + f'\n      and {added}\n')
                self.assertTrue([p for p in problems if added in p and kind in p], problems[:3])

    def test_the_new_reads_are_in_the_binary_and_named(self):
        for name in NEW_READS:
            self.assertIn(name, self.cen['mrs'], name)
            self.assertIn(name, self.named, name)

    def test_the_negations_hold_for_the_binary_and_name_what_the_test_checks(self):
        self.assertEqual(negation_problems(self.cen), [])
        said = ' '.join(gq.EL2_NOT)
        for name in (*NOT_WRITTEN, *NOT_EXECUTED):
            self.assertIn(name, said, name)
        self.assertEqual(set(WIDENED.findall(said)) | set(C.PLAIN_NAME.findall(said)) | ({'TLBI'} & set(said.replace(',', ' ').split())),
                         set(NOT_WRITTEN) | set(NOT_EXECUTED))

    def test_an_elf_with_one_more_instruction_of_a_kind_the_text_does_not_name_fails(self):
        for label, (word, name) in EXTRA.items():
            with self.subTest(label):
                cen = C.census(patched(self.elf, word, label.replace(' ', '-').replace('#', '') + '.elf'))
                self.assertTrue(name in cen['msr'] or name in cen['sys'] or name in cen['plain'], f'the copy has {label}: {sorted(cen["msr"])}')
                problems = problems_of(cen, self.named)
                self.assertTrue([p for p in problems if name in p and 'does not name it' in p], (name, problems[:3]))
                self.assertTrue(negation_problems(cen), 'the text says the binary has none of these')

    def test_every_write_is_as_in_the_builds_of_the_first_two_attempts(self):
        pairs = earlier_pairs()
        if not pairs:
            self.skipTest('the local files of the earlier attempts are absent or are not the builds that ran (build/sp11-gic/bin, $SP11_GICDRY2_DIR/binA)')
        now = (msr_counts(self.elf), set(self.cen['sys']), set(self.cen['plain']))
        for name, (_, _, elf) in pairs.items():
            with self.subTest(name):
                old = C.census(elf)
                self.assertEqual((msr_counts(elf), set(old['sys']), set(old['plain'])), now)
                self.assertTrue(set(NEW_READS) - {'ICC_IAR1_EL1', 'ICC_HPPIR1_EL1'} <= set(self.cen['mrs']) - set(old['mrs']), 'the survey reads are the new ones')
        extra = msr_counts(patched(self.elf, EXTRA['msr tpidr_el2'][0], 'compare.elf'))
        self.assertNotEqual(extra, now[0], 'the comparison can fail')


# ---- hygiene ---------------------------------------------------------------------------------------------------------------------------
class Hygiene(unittest.TestCase):
    def test_the_new_files_are_ascii_mit_small_and_hold_no_machine_text(self):
        for path in OWN:
            text = path.read_text()
            with self.subTest(path.name):
                self.assertTrue(text.isascii())
                self.assertIn('SPDX-License-Identifier: MIT', '\n'.join(text.splitlines()[:3]))
                self.assertLess(len(text.splitlines()), 1500)
                self.assertLess(len(text.encode()), 50 * 1024)
                for prefix in BT.MACHINE_PATHS:
                    self.assertNotIn(prefix, text)
                self.assertIsNone(re.search(r'(?i)\b([0-9a-f]{2}:){5}[0-9a-f]{2}\b', text))
                self.assertIsNone(re.search(r'\b(?:TO' r'DO|FIX' r'ME|X' r'XX|HA' r'CK|W' r'IP)\b', text))

    def test_the_two_shared_files_stay_below_their_caps(self):
        self.assertLess((TOOLS / 'sp11_stick_bundle_build.py').stat().st_size, 51200)
        self.assertLess((TOOLS / 'sp11_stick_bundle_record.py').stat().st_size, 50 * 1024)


if __name__ == '__main__':
    unittest.main()
