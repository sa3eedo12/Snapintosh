#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of the record text for the final stage of the GIC and stop dry run (passes 5 and 6: the quarantine and the rule for its last write; the record module
tools/sp11_stick_bundle_record_gic.py; the builder option --gic-quarantine; docs/SP11-STICK-GIC.md sections 4, 7, 8 and 12) and of its coupling to the wrapper it
describes: the decision of the two named refusals, the signal in the REAL binaries (the final pair built here, the dry-run pair built here, and the pairs of the first
two attempts where their files exist), the builder with those files in both directions, the text itself (what it says, its numbers against the stage's headers,
its section 4 against the wrapper's sources, its section 5 silence rule against the document's 15 s and the silence of an orderly run, its labels, its width and a pin
of its digest), the census of the final wrapper's ELF against the text in both directions,
with a text that lacks a name, a text that has one too many, and ELF copies with one more instruction, and a pin of the template that the local facts and the
local run note give, with the line of the note that names the commit. The register-by-register account of the stores and reads against the host model of the stage, and
the sentences of the text that describe the stage's source (the registers read in the handlers, COUNTER, whole-register writes, the end of a foreign interrupt, the
verdict return) and the commit of the note against the sources of the pair, are in tools/test-sp11-stick-bundle-record-gic-model.py.

    python3 tools/test-sp11-stick-bundle-record-gic.py

The fixtures of tools/test-sp11-stick-bundle-build.py are loaded here with a scratch directory of their own (build/sp11-record-gic-test-scratch/, deleted at
the end). The decision, the text and the hygiene need no toolchain. The final and dry-run binaries are built here with tools/sp11_stick_wrapper_build.py
and need clang, ld.lld, the llvm tools and a q1n1 checkout ($Q1N1_SP11_TREE); the pairs of the first two attempts are local files (build/sp11-gic/bin and
$SP11_GICDRY2_DIR/binA, each held to the digests of the builds that ran); the template pin needs build/sp11-gic/facts-gicdry3.local.json and the run note
build/sp11-gic/note-gicdry3.txt, local files that are not committed. What cannot run is SKIPPED, which is not a pass. No hardware: NOT RUN."""
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
# The final pair of the stage (pass 6): what tools/sp11_stick_wrapper_build.py --gic builds, byte for byte. This text was read against it and the census below is of its
# ELF, so another pair is a changed stage: the text has to be read again against it, and this pin is the reminder.
FINAL = {'wrapper': (98236, 'ebac8675a246dfdbe1f0707e66b04b24e6d0b6fbdec383fdfebfee9eb6a960c2'),
         'reporter': (62000, '89b8fab1f6dadd72b39bdd07016b6436864af40551a2665074fbb2de2600b6d9')}
# The text offered to the operator for the third attempt: the template that the local facts and the local run note (version 6) give with the final pair. The note's
# line "Built from commit" ties the payload digests to their source, and Final checks that commit against the sources of the pair. A changed byte of the facts, the
# note or the text is another record: the pin applies only to the facts file and the note it names by digest and size; with any other it is skipped, which is not a pass.
LOCAL = dict(facts=('build/sp11-gic/facts-gicdry3.local.json', '6d278ac6837c96cc5125819e909a7fddb823ed6f83e5cd80577686218a8a5952'),
             note=('build/sp11-gic/note-gicdry3.txt', 'ff4a422fbbf4bed2c6e053e30f2983f8526238085e7af1dba1beb2a5d8cab3fc', 5047), size=20663,
             template='d80f817c50a349ff377e2c5993e8e440b9990791b290a95c00d40e2207bf7804')
COMMIT = '87da59673c9a59fc5709a57e2ef0581ff722c7c6'
COMMIT_LINE = re.compile(r'^Built from commit ([0-9a-f]{40}) \(payloads 0 and 1 built twice from that tree, identical\)\.$', re.M)
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
GOLDEN = 'ece5ebe68f7134d02c086a6399ebf19037261ad57a32ac33a4da9ca345117043'
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


def header_text():
    """The two headers of the stage that hold the numbers the text states."""
    return (ROOT / 'probes/sp11-stick-gic-record.h').read_text() + (ROOT / 'probes/sp11-stick-gic.h').read_text()


def pair_problems(wrapper, reporter):
    """What differs between the files `wrapper` and `reporter` and FINAL: [str]."""
    problems = []
    for label, path in (('wrapper', wrapper), ('reporter', reporter)):
        data = Path(path).read_bytes()
        if (len(data), sha(data)) != FINAL[label]:
            problems.append(f'the {label} is {len(data)} bytes sha256 {sha(data)}, not the final pair\'s {FINAL[label][0]} bytes sha256 {FINAL[label][1]}')
    return problems


# Pass 6's rule for the last ISENABLER0 write, as the text says it, and the sentence of pass 5 that it made false.
EXCEPTION_SAYS = ('the last register write of the stage, except that it is NOT made when the put-backs of both ICC_IGRPEN1_EL1 and GICD_CTLR failed (both group 1 gates left open): '
                  'ISENABLER0 is then only read and named in FAILED, the foreign interrupts stay disabled and the run ends NOT_RESTORED (OUTCOME=0X30). The pending state of those '
                  'interrupts is never written: a source that asserts through the run is pending, and, except in that case, enabled again, when the run ends.')
PASS_5_SAID = ('the last register write of the stage. The pending state of those interrupts is never written: a source that asserts through the run is pending, '
               'and enabled again, when the run ends.')


def exception_problems(flat):
    """What is wrong with `flat` (the text, white space collapsed) about pass 6's exception: [str]."""
    problems = [] if EXCEPTION_SAYS in flat else ['the pass 6 sentence is not there']
    return problems + [f'still says: {PASS_5_SAID}'] * (PASS_5_SAID in flat)


# The paragraph that says what the stage never touches, as it must say it (the quarantine writes the enable bits of the other interrupts; a window takes and ends whatever
# INTID arrives), and the wording of the two versions before it.
NEVER_SAYS = ('Never the ITS, SMMU, PCIe, USB or the framebuffer. No SPI register is written (the SPIs are only read) and no other redistributor (only its GICR_TYPER is read). '
              'Of the interrupts but PPI 26 only the enable bits that Q cleared are written, by Q and its put-back (SGI 0 and PPIs 23, 25, 27 and 30 where ISENABLER0 read '
              '0x4E800001): no priority or pending state of any of them is written, and IGROUPR0 and ICFGR1 are written whole, the other bits as read, and only in the cases of R '
              'above (neither on this unit\'s reads in boot 13: DS 0, ICFGR1 level). But a window takes whatever INTID arrives: ICC_IAR1_EL1 acknowledges it, ICC_EOIR1_EL1 ends it '
              '(ICC_DIR_EL1 too when EOImode is set; it was in boot 13), a foreign SPI included (INFERRED from the architecture: a level source that still asserts is pending '
              'again, an edge or message interrupt is consumed). GICD_CTLR EnableGrp1A is distributor-wide: group 1 is enabled for every interrupt while the stage holds it set.')
NEVER_SAID = ('Never the ITS, SMMU, PCIe, USB or the framebuffer. No SPI is written (the SPIs are only read) and no other redistributor (only its GICR_TYPER is read). '
              'Of the interrupts but PPI 26 only the enable bits that Q cleared are written, by Q and its put-back (SGI 0 and PPIs 23, 25, 27 and 30 where ISENABLER0 read '
              '0x4E800001): no priority, group, trigger or pending state of any of them is written.')
NEVER_WAS = ('another interrupt\'s configuration', 'No SPI is written', 'no priority, group, trigger or pending state')


def never_problems(flat):
    return ([] if NEVER_SAYS in flat else ['the paragraph is not there']) + [f'still says: {old}' for old in NEVER_WAS if old in flat]


def silence_problems(section, doc, worst_s):
    """What is wrong with section 5 (`section`, the text from "5. " on) of a --gic-quarantine record against the document's stop rule and the longest silence that an
    orderly run of the stage has (`worst_s`): [str]. The rule must be the document's figure ("no page change for N s") for a NEXT line or ENTER, above that silence,
    must not be the rule of the other records, and NOT_RESTORED must stop the series."""
    flat, problems = ' '.join(section.split()), []
    rule = re.search(r'No page change for (\d+) s after a NEXT line or after ENTER', flat)
    doc_s = int(re.search(r'no page change for (\d+) s', ' '.join(doc.split())).group(1))
    if not rule:
        problems.append('there is no rule of seconds for a NEXT line or ENTER')
    else:
        if int(rule.group(1)) != doc_s:
            problems.append(f'the rule says {rule.group(1)} s and the document {doc_s} s')
        if int(rule.group(1)) <= worst_s:
            problems.append(f'{rule.group(1)} s is not above the {worst_s} s that an orderly run is silent for')
    if rec.SILENCE_RULE.strip() in flat:
        problems.append('the rule of the other records is still there')
    if 'NOT_RESTORED stops the series' not in flat:
        problems.append('NOT_RESTORED does not stop the series')
    return problems


def twenty_problems(text):
    """Every sentence of sections 3 to 5 of a record (`text`) that says "20 s" must be about the whole stage (two windows and two 8 s waits) and none about the quarantine,
    which lasts about 12 s on a passing run: [str]."""
    flat = ' '.join(text.split('3. Store classes', 1)[1].split('\n6. ', 1)[0].split())
    problems = []
    for sentence in re.split(r'(?<=\.)\s+', flat):
        if re.search(r'\b20 s\b', sentence):
            if re.search(r'quarantin|\bQ\b|\boff\b|disabled', sentence):
                problems.append(f'20 s for the quarantine: {sentence[:70]}')
            if not re.search(r'two windows and the two 8 s waits|in all with the interrupts on time', sentence):
                problems.append(f'20 s that is not the whole stage: {sentence[:70]}')
    return problems


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
                          number(r'SG_OUT_IS_VERDICT\(o\) \(\(o\) >= (\d+)'), number(r'SG_OUT_IS_VERDICT\(o\) \(\(o\) >= \d+ && \(o\) <= (\d+)')), (1, 15, 32, 47))
        self.assertEqual((q, f, ok, last), (22, 23, 32, 48))
        for needle in (f'Q_NOT_HELD ({q}, OUTCOME=0X{q:X})', f'FOREIGN_PENDING ({f}, OUTCOME=0X{f:X})', f'Q_NOT_HELD 0X{q:X}, FOREIGN_PENDING 0X{f:X}', f'IRQ_OK 0X{ok:X}',
                       f'NOT_RESTORED 0X{last:X}', f'the run ends NOT_RESTORED (OUTCOME=0X{last:X})', '0X1 to 0XF a refusal, before any store except COUNTER at the second 8 s wait '
                       '(after the stores, with a put-back)', '0X10 to 0X1F', '0X20 to 0X2F', f'After any window verdict (OUTCOME 0X{ok:X} to 0X{ok + 15:X}, IRQ_OK 0X{ok:X} included',
                       'version 3', '1456 bytes',
                       'offset 0x11000', '2 s in the default build',
                       'and 8 s', 'ICC_PMR_EL1 0xF0, ICC_BPR1_EL1 0, ICC_IGRPEN1_EL1 1', 'IPRIORITYR byte (0x80 if it differs)', 'the IPRIORITYR byte of INTID 26',
                       'the IPRIORITYR bytes of INTIDs 23, 25, 27 and 30'):
            self.assertIn(needle, self.flat)
        self.assertEqual(0x4E800001 & ~(1 << 26), 0x4A800001)
        self.assertEqual([number(rf'SG_SURVEY_PPI{i} (\d+)u') for i in range(4)], [23, 25, 27, 30])
        self.assertEqual((number(r'SW_GIC_WAIT_S (\d+)u'), number(r'SP11_STICK_GIC_TIME_BOX_S (\d+)u'), number(r'SW_GIC_PRIORITY (0x[0-9A-Fa-f]+)u'),
                          number(r'SW_GIC_PMR (0x[0-9A-Fa-f]+)u')), (8, 2, 0x80, 0xF0))

    def test_the_facts_carry_their_labels_and_a_hardware_result_is_never_called_verified(self):
        for fact in ('ISENABLER0 0x4E800001 (boot 12)', 'ISPENDR0 0x08000000 (boot 13)', 'Virtual EL1 GSIV 27', '(from its ACPI dump, not from boot 12 or 13)',
                     'GICD_CTLR written 0x13 read back 0x12 (boot 13)', 'an enabled, pending PPI is delivered to EL2'):
            self.assertIn(fact, self.flat)
        self.assertNotIn('not a boot)', self.flat)
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
        lines = self.new.splitlines()
        first = next(i for i, line in enumerate(lines) if line.startswith('4. Time box'))
        last = next((i for i, line in enumerate(lines) if line.startswith('6. ')), len(lines))
        three = self.new.split('3. Store classes', 1)[1].split('   Not authorised', 1)[0].splitlines()
        self.assertLessEqual(max(map(len, [*three, *lines[first:last], gq.PLACEMENT_HEADER])), 120)       # sections 3 to 5, wrapped by hand, so that no library version changes the digest
        self.assertEqual(self.new, render())
        self.assertEqual(sha(self.new.encode()), GOLDEN)

    def test_only_a_gic_stop_record_can_carry_the_variant(self):
        with self.assertRaises(AssertionError):
            render(gic_stop=False)
        self.assertNotIn('Q_NOT_HELD', render(gic_stop=False, gic_quarantine=False))

    def test_pass_6_s_exception_replaces_the_sentence_that_pass_6_made_false(self):
        self.assertEqual(exception_problems(self.flat), [])
        self.assertEqual(exception_problems(self.flat.replace(EXCEPTION_SAYS, PASS_5_SAID)), ['the pass 6 sentence is not there', f'still says: {PASS_5_SAID}'],
                         'the sentence of pass 5 fails the check')
        self.assertTrue(exception_problems(self.flat.replace('is NOT made', 'is made')), 'a text without the exception fails it')
        for needle in ('by ONE write of ISENABLER0', 'the last register write of the stage'):
            self.assertIn(needle, self.flat)

    def test_the_never_paragraph_says_what_the_quarantine_writes_and_nothing_wider(self):
        self.assertEqual(never_problems(self.flat), [])
        self.assertTrue(never_problems(self.flat.replace(NEVER_SAYS, NEVER_SAID)), 'the wording of the first version fails the check')
        self.assertEqual([i for i in range(32) if 0x4A800001 >> i & 1], [0, 23, 25, 27, 30], 'SGI 0 and PPIs 23, 25, 27 and 30 are the bits of the value Q writes')
        self.assertIn('PPI 26\'s own are the stage\'s: it sets them as the writes above say, and undoes them.', self.flat)

    def test_the_unknown_paragraph_names_the_extended_spis_and_the_lpis_in_the_documents_words(self):
        said = ('whether any extended SPI (GICD_TYPER.ESPI is 1, range 31) or LPI (GICR_TYPER.PLPIS is 1) is enabled and pending: neither is surveyed or quarantined; only the '
                'one ICC_HPPIR1_EL1 read before window 1 sees one, at one instant (the decode of the two registers is INFERRED)')
        unknown = self.flat.split('UNKNOWN: ', 1)[1].split(' Not authorised', 1)[0]
        self.assertIn(said, unknown)
        self.assertNotIn(said, ' '.join(self.old.split()))
        doc = (ROOT / 'docs/SP11-STICK-GIC.md').read_text()
        typer, rtyper = (int(re.search(rf'{name} (?:low word )?(0x[0-9A-F]+)', doc).group(1), 16) for name in ('GICD_TYPER', 'GICR_TYPER'))
        self.assertEqual((typer >> 8 & 1, typer >> 27 & 31, rtyper & 1), (1, 31, 1), 'ESPI 1, range 31 and PLPIS 1 in the values that the document records (INFERRED bit positions)')
        self.assertEqual(32 * ((typer & 31) + 1) - 1, 991)
        self.assertIn('which reads INTIDs 32 to 991 on the unit', self.flat)

    def test_section_4_of_the_variant_says_what_the_wrapper_does_with_the_time_box_and_the_other_records_keep_the_generic_line(self):
        generic = '4. Time box: 5 s of wall time on CNTPCT_EL0, carried in the PARAMS chunk (watchdog_seconds); the stop is the monitor\'s own.'
        for other in (render(gic_quarantine=False), render(gic_stop=False, gic_quarantine=False)):
            self.assertIn(generic, other)
        self.assertNotIn(generic, self.new)
        section = ' '.join(self.new.split('\n4. ', 1)[1].split('\n5. ', 1)[0].split())
        for needle in ('Time box: watchdog_seconds, 5 s, is carried in the PARAMS chunk of GUEST.DAT', '(TIME_BOX_MISMATCH)', 'the GIC wrapper does not read it and calls no monitor'):
            self.assertIn(needle, section)
        self.assertNotIn('the monitor\'s own', section)
        self.assertIn('TIME_BOX_MISMATCH', ch.KNOWN_REFUSALS)
        self.assertIn('watchdog_seconds, 9 s,', ' '.join(gq.time_lines(9)), 'the PARAMS word named is the time box of the record')

    def test_the_times_in_section_4_are_computed_from_the_stage_s_own_constants(self):
        number = lambda pattern: int(re.search(pattern, header_text()).group(1), 0)       # noqa: E731
        constants = tuple(number(pattern) for pattern in (r'SP11_STICK_GIC_TIME_BOX_S (\d+)u', r'SP11_STICK_GIC_CYCLE_US (\d+)u', r'#define SG_CYCLES (\d+)u', r'SW_GIC_WAIT_S (\d+)u'))
        self.assertEqual(constants, (2, 10000, 3, 8))
        self.assertIn('2 * r->timebox_ticks + (w == SG_WIN_EL2 ? 2 * (SG_CYCLES + 1) * r->cycle_ticks : 0)', (ROOT / 'probes/sp11-stick-gic-dry.c').read_text(),
                      'where the 4 s and the 4.08 s of window 1 come from')

        def says(box, cycle_us, cycles, wait):
            bound, extra_ms = 2 * box, 2 * (cycles + 1) * cycle_us // 1000
            first = bound + extra_ms / 1000
            return (f'arms CNTHP {box} s ahead and is bounded at {bound} s', f'(window 1: {first:.2f} s, with {extra_ms} ms for its re-arms of {cycle_us // 1000} ms)',
                    f'and the two {wait} s waits', f'about {2 * wait + 2 * box} s in all with the interrupts on time', f'{wait + 2 * box} s if window 1 sees no interrupt',
                    f'{2 * wait + 3 * box} s if window 2 then sees none', f'at most about {round(first + 2 * wait + bound)} s, the sum of the bounds ({first:.2f} s + {wait} s + {wait} s + {bound} s)')
        section = ' '.join(self.new.split('\n4. ', 1)[1].split('\n5. ', 1)[0].split())
        self.assertEqual([needle for needle in says(*constants) if needle not in section], [])
        self.assertEqual(len([needle for needle in says(3, *constants[1:3], 9) if needle not in section]), len(says(*constants)), 'other constants fail every needle')
        first = 2 * constants[0] + 2 * (constants[2] + 1) * constants[1] / 1e6
        for needle in (f'only inside window 1, which ends at {first:.2f} s at the latest', f'the stub spins on the counter for at most {2 * constants[0]} s (window 2)',
                       f'at most about {round(first + 2 * constants[3] + 2 * constants[0])} s (section 4)'):
            self.assertIn(needle, self.flat)
        self.assertNotIn('a window of at most twice the time box', self.flat)

    def test_the_gic_wrapper_has_no_watchdog_word_and_no_monitor_and_the_monitor_entry_build_has_both(self):
        gic, m1 = wb.SOURCES['SNAPMON-GIC.BIN'], wb.SOURCES['SNAPMON-M1.BIN']
        probes = lambda sources: [s for s in sources if s.startswith('probes/')]       # noqa: E731
        found = lambda sources: sorted((s, w) for s in probes(sources) for w in ('watchdog', 'SM_PARAMS') if w.lower() in (ROOT / s).read_text().lower())       # noqa: E731
        self.assertGreaterEqual(len(probes(gic)), 6)
        self.assertEqual(found(gic), [])
        self.assertNotIn('probes/sp11-stick-monitor.c', gic)
        self.assertIn(('probes/sp11-stick-monitor.c', 'watchdog'), found(m1))

    def test_section_5_of_the_variant_has_the_documents_15_s_above_the_silence_of_an_orderly_run_and_the_other_records_keep_the_generic_rule(self):
        number = lambda pattern: int(re.search(pattern, header_text()).group(1), 0)       # noqa: E731
        wait, box = number(r'SW_GIC_WAIT_S (\d+)u'), number(r'SP11_STICK_GIC_TIME_BOX_S (\d+)u')
        healthy, worst = wait + box, wait + 2 * box         # after NEXT GIC EL1 EXCURSION AFTER 8 S: the wait, then window 2 (about the box, at most twice it)
        doc = (ROOT / 'docs/SP11-STICK-GIC.md').read_text()
        section = self.new.split('\n5. ', 1)[1]
        self.assertEqual(silence_problems(section, doc, worst), [])
        for needle in (f'silent for {wait} s after NEXT GIC STORES AFTER {wait} S', f'about {healthy} s after NEXT GIC EL1 EXCURSION AFTER {wait} S', f'at most {worst} s: section 4'):
            self.assertIn(needle, ' '.join(section.split()))
        self.assertLessEqual(2 * 5, worst, 'twice the time box of 5 s, the rule of the other records, is not above what an orderly run is silent for')
        self.assertIn(rec.SILENCE_RULE, self.old)
        self.assertNotIn(rec.SILENCE_RULE, self.new)
        for bullet in ('A wrapper REFUSED line ends the boot', 'Two resets at the same last line stop the series', 'One hardware client'):
            self.assertIn(bullet, section)
            self.assertIn(bullet, self.old)
        self.assertIn('NOT_RESTORED stops the series', ' '.join(doc.split()))

    def test_a_record_with_10_s_or_12_s_without_the_not_restored_rule_or_with_the_generic_rule_or_against_another_figure_in_the_document_fails_the_silence_check(self):
        doc = (ROOT / 'docs/SP11-STICK-GIC.md').read_text()
        section, worst = self.new.split('\n5. ', 1)[1], 12
        for seconds in (10, 12):
            with self.subTest(seconds):
                problems = silence_problems(section.replace('No page change for 15 s after', f'No page change for {seconds} s after'), doc, worst)
                self.assertEqual(len(problems), 2, problems)
                self.assertIn(f'the rule says {seconds} s and the document 15 s', problems)
        self.assertEqual(silence_problems(section.replace(next(line for line in gq.SILENCE_LINES if 'NOT_RESTORED stops the series' in line) + '\n', ''), doc, worst),
                         ['NOT_RESTORED does not stop the series'])
        self.assertEqual(len(silence_problems(self.old.split('\n5. ', 1)[1], doc, worst)), 3, 'the generic rule: no rule of seconds, the old rule, no NOT_RESTORED')
        self.assertEqual(silence_problems(section, doc.replace('no page change for 15 s', 'no page change for 20 s'), worst), ['the rule says 15 s and the document 20 s'])

    def test_no_sentence_of_sections_3_to_5_gives_the_quarantine_20_s_and_the_20_s_that_it_has_are_the_whole_stage(self):
        self.assertEqual(twenty_problems(self.new), [])
        self.assertIn('about 20 s', self.flat)
        quarantine = self.new.replace('Total time of the two windows and the two 8 s waits: about 20 s with the interrupts on time', 'The quarantine is on for about 20 s')
        self.assertEqual(len(twenty_problems(quarantine)), 2, 'a quarantine of 20 s is neither the whole stage nor 12 s')
        self.assertEqual(len(twenty_problems(re.sub(r'about 20 s in all with the interrupts on\s+time', 'about 20 s', self.new))), 1)


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


# ---- the final pair and the local template ----------------------------------------------------------------------------------------------------
class Final(Need):
    def generate(self, note):
        facts = ROOT / LOCAL['facts'][0]
        return BT.cli(['authorisation-template', 'synthetic', '--facts', str(facts), '--base', '0x880000000', '--guest-mib', '384', '--monitor', str(self.wrapper),
                       '--reporter', str(self.reporter), '--stores', 'ram,el2_sysreg,gic', '--gic-stop', gq.OPTION, '--time-box-s', '5', '--note', str(note)])

    def test_the_pair_built_here_is_the_final_pair_the_census_is_of_its_wrapper_and_a_changed_file_is_not_the_pair(self):
        self.assertEqual(pair_problems(self.wrapper, self.reporter), [])
        self.assertEqual(C.flat_bytes(self.elf), self.wrapper.read_bytes(), 'the ELF that the census reads is the flat wrapper of the pair')
        data = bytearray(self.wrapper.read_bytes())
        data[100] ^= 1
        changed = BT.write(SCRATCH / 'mutants' / 'wrapper-one-bit.bin', bytes(data))
        self.assertEqual(len(pair_problems(changed, self.reporter)), 1)
        self.assertEqual(len(pair_problems(self.reporter, self.wrapper)), 2)

    def test_the_template_of_the_local_facts_and_run_note_is_the_pinned_one_twice(self):
        (facts, facts_sha), (note, note_sha) = ((ROOT / LOCAL[key][0], LOCAL[key][1]) for key in ('facts', 'note'))
        if not (facts.is_file() and note.is_file()):
            self.skipTest('the local facts of the third attempt or its run note are absent (build/sp11-gic/facts-gicdry3.local.json, note-gicdry3.txt)')
        if (sha(facts.read_bytes()), sha(note.read_bytes()), len(note.read_bytes())) != (facts_sha, note_sha, LOCAL['note'][2]):
            self.skipTest('the local facts or the run note is not the file that the pin was made for')
        first, second = self.generate(note), self.generate(note)
        self.assertEqual(first[0], 0, first[2])
        self.assertEqual(first, second, 'two runs, the same bytes')
        self.assertEqual((len(first[1].encode()), sha(first[1].encode())), (LOCAL['size'], LOCAL['template']))
        self.assertIn(note.read_text().splitlines()[0], first[1], 'the note is part of the record')
        number = lambda pattern: int(re.search(pattern, header_text()).group(1), 0)       # noqa: E731
        wait, box = number(r'SW_GIC_WAIT_S (\d+)u'), number(r'SP11_STICK_GIC_TIME_BOX_S (\d+)u')
        flat_note = ' '.join(note.read_text().split())
        cycles, cycle_us = number(r'#define SG_CYCLES (\d+)u'), number(r'SP11_STICK_GIC_CYCLE_US (\d+)u')
        window_1 = 2 * box + 2 * (cycles + 1) * cycle_us / 1e6
        # window 1, the second wait, window 2: the first wait is before the first store
        self.assertIn(f'(about {wait + 2 * box} s on a passing run, at most about {round(window_1 + wait + 2 * box)} s: {window_1:.2f} s + {wait} s + {2 * box} s)', flat_note)
        self.assertIn(f'is bounded at about {2 * box} s', flat_note)
        self.assertNotIn('about 20 s', flat_note, 'the 20 s is the whole stage, and the note gives it to nothing')
        self.assertEqual(COMMIT_LINE.findall(note.read_text()), [COMMIT])
        self.assertEqual(COMMIT_LINE.findall(note.read_text().replace(COMMIT, COMMIT[:39])), [], 'a short hash is no commit line')
        codes = [number(rf'X\({name}, (\d+)\)') for name in ('EXCEPTION', 'EL1_EXCEPTION')]
        for says in (f'(EXCEPTION 0X{codes[0]:X} in window 1, EL1_EXCEPTION 0X{codes[1]:X} in window 2), ends the window and everything is put back',
                     'A data abort outside a window lands in q1n1\'s fault page', '(INFERRED: the write was ignored)', '; the decode is INFERRED)',
                     'The unit\'s GTDT (from its ACPI dump, not from boot 12 or 13)', '(but a window takes and ends whatever INTID arrives, an SPI included)',
                     'A gate found open is not counted (both were found closed on the unit: GICD_CTLR 0x10, IGRPEN1 0)',
                     'RAM (the placement, REPORT.DAT with a 1456 byte record at 0x11000, and the wrapper\'s stack)'):
            self.assertIn(says, flat_note)
        for old in ('ignored or scaled', 'up to 14 s', 'any SPI (read only)', 'A data abort lands in q1n1\'s fault page', 'not a boot)'):
            self.assertNotIn(old, flat_note)
        other = self.generate(BT.write(SCRATCH / 'mutants' / 'note-edited.txt', note.read_bytes().replace(b'THIRD ATTEMPT', b'3RD ATTEMPT', 1)))
        self.assertEqual(other[0], 0, other[2])
        self.assertNotEqual(sha(other[1].encode()), LOCAL['template'], 'another note is another record: the pin can fail')


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
