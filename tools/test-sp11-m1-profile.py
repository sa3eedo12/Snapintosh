#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The SP11 milestone M1 monitor profile: its pinned flag set, the NV2 record it requires, and the build.

    python3 tools/test-sp11-m1-profile.py [-v] [-k NAME]

Host only: nothing here touches hardware, and every record value is made up (the tool's own synthetic example, whose
MIDR matches no core this repository knows). Nothing here shows that an Oryon v1 implements NV2 or that the monitor
runs on a Surface Pro 11: hardware NOT RUN.

  ProfilePins     config/monitor-profile-sp11-m1.json laid over the A16 recipe gives exactly the verified flag set: SP11 on,
                  the queued service lock on, the five GIC macros at the MADT's values (GICD 0x17000000, redistributor
                  frames 0x17080000 + n * 0x40000 for 12 frames ending at 0x17380000, EL2 timer INTID 26), the native-writer
                  breakpoints on, no relocation, no thermal define, no NV2 record in the file itself. A damaged profile
                  (each GIC macro missing, SP11 off, relocation or a thermal define added, a launch contract that
                  changes x4 or the thermal bit, and more) is refused.
  RecordInputs    the NV2 record is required and has no default: a missing, unreadable, linked, oversized, truncated,
                  damaged, edited or foreign (SoC) header, a record the launch gate would deny whatever the live registers
                  read, and a path that cannot sit in a C string literal each stop the tool with exit status 2 before
                  anything is built. A valid one is printed: SoC, build id, MIDR, REVIDR and both ID registers as recorded.
  Build           with clang, lld and a q1n1 checkout (the sibling or $Q1N1_SP11_TREE): the profile builds, carries the
                  record's 96 bytes exactly once, reads CNTVOFF_EL2 (the guard), and OUT/profile-build.json rebuilds the
                  identical monitor. Without them the class reports SKIP, never PASS.

The local record under build/nv2-record (the first X2 image, HCR_EL2.E2H = 1) is used only as a fixture by one test, if it
is there: it is not a decision (operator decision D6) and no value of it is written into any tracked file.
Scratch files live under build/sp11-m1-profile-test/ and are removed."""
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock
import ast
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import monitor_build_offline  # noqa: E402
import nv2_record as nv2  # noqa: E402
import sp11_m1_profile as tool  # noqa: E402

BUILD = ROOT / 'build' / 'sp11-m1-profile-test'
LLVM = Path('/opt/homebrew/opt/llvm/bin')
CLANG = str(LLVM / 'clang') if (LLVM / 'clang').exists() else 'clang'
OBJDUMP = str(LLVM / 'llvm-objdump') if (LLVM / 'llvm-objdump').exists() else 'llvm-objdump'
LOCAL_RECORD = ROOT / 'build/nv2-record/sp11-nv2-record.h'

# The verified flag set, written out here so that a change of the profile file fails this test rather than a boot.
# GIC values: the MADT of the unit's firmware (boot 6 dump; docs/SP11-MONITOR-PORT.md section 7), VERIFIED against
# probes/secondary.c's static assertions; the redistributor window ends where the twelfth 0x40000 frame ends.
EXPECTED_DEFINES = ('-DSNAP_PLATFORM_SP11=1', '-DSNAP_FAIR_TOKEN=1', '-DSNAP_GICD_BASE=0x17000000',
                    '-DSNAP_GICR_FIRST=0x17080000', '-DSNAP_GICR_LIMIT=0x17380000', '-DSNAP_GICR_STRIDE=0x40000',
                    '-DSNAP_TIMER_INTID=26')
GICR_FRAMES = 12


def scratch(prefix):
    BUILD.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(prefix=prefix, dir=BUILD)


def cli(*argv):
    """(exit status, stdout, stderr) of the tool's main, in process."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = tool.main(list(argv))
        except SystemExit as stop:
            code = stop.code
    return code, out.getvalue(), err.getvalue()


def synthetic(**changes):
    """The record of tools/nv2_record.py's documented SYNTHETIC EXAMPLE, which the gate would not deny on its own."""
    values = dict(soc=nv2.SOC['x1e80100'], midr_el1=0xFEED0001, revidr_el1=0, id_aa64mmfr2_el1=0x02001234,
                  id_aa64mmfr4_el1=0, vncr_redirect=nv2.RESULT['pass'], negative_control_trapped=nv2.RESULT['pass'],
                  evidence=nv2.EVIDENCE['OBSERVED'], tool='synthetic-doc', build_id=1)
    values.update(changes)
    return nv2.Record(**values)


def header_of(record):
    return nv2.emit_header(nv2.pack(record))


def top_level_literal(relpath, name):
    for node in ast.parse((ROOT / relpath).read_text()).body:
        if isinstance(node, ast.Assign) and any(getattr(t, 'id', None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f'{relpath} has no literal {name}')


# --------------------------------------------------------------------------------------------------------------
# 1. The profile

class ProfilePins(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = json.loads((ROOT / tool.PROFILE_PATH).read_text())
        cls.base = json.loads((ROOT / tool.BASE_PATH).read_text())['flags']

    def test_the_profile_is_the_verified_flag_set_on_top_of_the_a16_recipe(self):
        self.assertEqual(tuple(self.profile['defines']), EXPECTED_DEFINES)
        loaded, base = tool.load_profile(ROOT)
        self.assertEqual((loaded['profile'], base), ('sp11-m1', self.base))
        flags = tool.profile_flags(loaded, base, mock.Mock(path=Path('/x/record.h')))
        self.assertEqual(flags[:len(self.base)], self.base)
        self.assertEqual(tuple(flags[len(self.base):-1]), EXPECTED_DEFINES)
        self.assertEqual(flags[-1], '-DSNAP_NV2_RECORD_HEADER="/x/record.h"')
        self.assertEqual(len(flags), len(set(flags)))

    def test_the_a16_recipe_gains_nothing(self):
        self.assertFalse([flag for flag in self.base if re.search(r'SP11|FAIR_TOKEN|GIC|TIMER_INTID|NV2_RECORD', flag)])

    def test_the_gic_macros_are_the_madt_facts_and_satisfy_the_monitors_own_assertions(self):
        values = {name: int(value, 0) for name, value in (flag[2:].split('=') for flag in EXPECTED_DEFINES)}
        self.assertEqual(values['SNAP_GICR_FIRST'] + GICR_FRAMES * values['SNAP_GICR_STRIDE'], values['SNAP_GICR_LIMIT'])
        self.assertEqual((values['SNAP_GICD_BASE'], values['SNAP_GICR_STRIDE'], values['SNAP_TIMER_INTID']),
                         (0x17000000, 0x40000, 26))
        text = re.sub(r'\s+', ' ', (ROOT / 'docs/SP11-MONITOR-PORT.md').read_text())
        for needle in ('0x17000000', '0x17080000', '0x17380000', '0x40000', 'GSIV 26'):
            self.assertIn(needle, text)
        done = subprocess.run([CLANG, '--target=aarch64-unknown-none-elf', '-ffreestanding', '-dM', '-E', '-x', 'c',
                               *EXPECTED_DEFINES, str(ROOT / 'probes/secondary.c')], capture_output=True, text=True, cwd=ROOT)
        if done.returncode:
            self.skipTest('clang cannot preprocess probes/secondary.c here: ' + done.stderr[-300:])
        self.assertIn('#define SECONDARY_GIC_UNMEASURED 0', done.stdout)
        syntax = subprocess.run([CLANG, '--target=aarch64-unknown-none-elf', '-ffreestanding', '-fsyntax-only', '-Wall',
                                 '-Wextra', '-Werror', *EXPECTED_DEFINES, str(ROOT / 'probes/secondary.c')],
                                capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(syntax.returncode, 0, syntax.stderr)

    def test_fair_token_is_on_and_thermal_and_relocation_are_off(self):
        flags = tool.profile_flags(*tool.load_profile(ROOT), mock.Mock(path=Path('/x/record.h')))
        names = {match.group(1) for flag in flags if (match := re.match(r'-D([A-Z0-9_]+)', flag))}
        self.assertIn('SNAP_FAIR_TOKEN', names)
        self.assertFalse([name for name in names if 'THERMAL' in name or name == 'SNAP_BOOT_RELOCATE'])
        self.assertIn('-DSNAP_FAIR_TOKEN=1', flags)
        contract = self.profile['runtime_contract']
        self.assertEqual((contract['x4'], contract['early_stop_thermal_bit']), (0, 'clear'))
        self.assertEqual(sorted(self.profile['forbidden_define_names']), ['SNAP_BOOT_RELOCATE', 'SNAP_NV2_RECORD_HEADER'])
        self.assertEqual(self.profile['forbidden_define_substrings'], ['THERMAL'])

    def test_the_native_writer_breakpoints_are_on(self):
        for flag in ('-DSNAP_NATIVE_WRITER_BREAKPOINTS=1', '-DSNAP_NATIVE_WRITER_ADMISSION=1', '-DSNAP_NATIVE_CALLS=1'):
            self.assertIn(flag, self.base)
            self.assertIn(flag, self.profile['required_base_defines'])

    def test_the_other_places_that_state_the_flag_set_agree(self):
        building = (ROOT / 'docs/BUILDING.md').read_text()
        for flag in EXPECTED_DEFINES[1:]:
            self.assertIn(flag, building)
        self.assertEqual(tuple(top_level_literal('tools/test-sp11-integration.py', 'SP11_FLAGS')), EXPECTED_DEFINES)

    def test_a_damaged_profile_is_refused(self):
        profile = json.loads((ROOT / tool.PROFILE_PATH).read_text())

        def edit(change):
            copy = json.loads(json.dumps(profile))
            change(copy)
            return copy

        def without(prefix):
            return lambda p: p.update(defines=[d for d in p['defines'] if not d.startswith(prefix)])
        # name: (the damaged profile, a fragment of the refusal): the fragment pins WHICH rule refuses it.
        cases = {
            'an unknown key': (edit(lambda p: p.update(extra=1)), 'keys must be exactly'),
            'a missing key': (edit(lambda p: p.pop('runtime_contract')), 'keys must be exactly'),
            'another profile': (edit(lambda p: p.update(profile='sp11-m2')), 'not the sp11-m1 profile'),
            'another base recipe': (edit(lambda p: p.update(base_flags='config/other.json')), 'the A16 recipe'),
            'no fair token': (edit(without('-DSNAP_FAIR_TOKEN=')), '-DSNAP_FAIR_TOKEN=1 is required'),
            'SP11 off': (edit(lambda p: p.update(defines=[d.replace('PLATFORM_SP11=1', 'PLATFORM_SP11=0') for d in p['defines']])),
                         '-DSNAP_PLATFORM_SP11=1 is required'),
            'a define twice': (edit(lambda p: p['defines'].append('-DSNAP_GICD_BASE=0x17000000')), 'defined twice'),
            'a malformed define': (edit(lambda p: p['defines'].append('SNAP_X')), 'is not -DNAME=VALUE'),
            'relocation on': (edit(lambda p: p['defines'].append('-DSNAP_BOOT_RELOCATE=1')), 'forbidden'),
            'a thermal define': (edit(lambda p: p['defines'].append('-DSNAP_SP11_THERMAL_GUARD=1')), 'forbidden'),
            'the record in the file': (edit(lambda p: p['defines'].append('-DSNAP_NV2_RECORD_HEADER=1')), 'forbidden'),
            'x4 not zero': (edit(lambda p: p['runtime_contract'].update(x4=1)), 'launch contract'),
            'the thermal bit not clear': (edit(lambda p: p['runtime_contract'].update(early_stop_thermal_bit='set')),
                                          'launch contract'),
            'another record define': (edit(lambda p: p['required_input'].update(define='SNAP_OTHER_HEADER')), 'required_input'),
            'an unknown SoC': (edit(lambda p: p['required_input'].update(soc='unknown')), 'required_input'),
            'strings that are not strings': (edit(lambda p: p.update(defines=[1, 2])), 'list of strings'),
        }
        for macro in tool.GIC_MACROS:
            cases[f'no {macro}'] = (edit(without(f'-D{macro}=')), f'-D{macro} is required')
        with scratch('profile-') as tmp:
            (Path(tmp) / 'config').mkdir()
            shutil.copy(ROOT / tool.BASE_PATH, Path(tmp) / tool.BASE_PATH)
            for name, (damaged, fragment) in cases.items():
                with self.subTest(name):
                    (Path(tmp) / tool.PROFILE_PATH).write_text(json.dumps(damaged))
                    with self.assertRaises(tool.Refused) as refused:
                        tool.load_profile(tmp)
                    self.assertIn(fragment, str(refused.exception))
            (Path(tmp) / tool.PROFILE_PATH).write_text(json.dumps(profile))
            tool.load_profile(tmp)                                   # the undamaged copy is accepted
            (Path(tmp) / tool.PROFILE_PATH).write_text('{"profile": 1, "profile": 2}')
            with self.assertRaises(tool.Refused):
                tool.load_profile(tmp)                               # a key twice
            base = json.loads((ROOT / tool.BASE_PATH).read_text())
            for name, flags in {
                    'the breakpoints dropped from the base': [f for f in base['flags'] if f != '-DSNAP_NATIVE_WRITER_BREAKPOINTS=1'],
                    'relocation in the base': [*base['flags'], '-DSNAP_BOOT_RELOCATE=1'],
                    'a thermal define in the base': [*base['flags'], '-DSNAP_A16_THERMAL=1'],
                    'a flag the base and the profile both define': [*base['flags'], '-DSNAP_FAIR_TOKEN=1']}.items():
                with self.subTest(name):
                    (Path(tmp) / tool.BASE_PATH).write_text(json.dumps(dict(base, flags=flags)))
                    (Path(tmp) / tool.PROFILE_PATH).write_text(json.dumps(profile))
                    with self.assertRaises(tool.Refused):
                        tool.load_profile(tmp)


# --------------------------------------------------------------------------------------------------------------
# 2. The record

class RecordInputs(unittest.TestCase):
    def setUp(self):
        holder = scratch('record-')
        self.addCleanup(holder.cleanup)
        self.dir = Path(holder.name).resolve()
        build = mock.patch.object(monitor_build_offline, 'build', side_effect=AssertionError('a refused input was built'))
        build.start()
        self.addCleanup(build.stop)

    def write(self, name, text, binary=False):
        path = self.dir / name
        path.write_bytes(text) if binary else path.write_text(text, newline='')
        return str(path)

    def refused(self, header, fragment, command='show'):
        code, out, err = cli(command, '--nv2-record-header', header) if header is not None else cli(command)
        self.assertEqual(code, 2, (out, err))
        self.assertIn(fragment, err)
        self.assertTrue(err.startswith('REFUSED: '), err)
        self.assertEqual(out, '', 'a refused input must print no flags')

    def test_a_missing_record_stops_every_command_with_the_operator_decision_named(self):
        for command in ('show', 'flags'):
            self.refused(None, 'operator decision D6', command)
        code, _, err = cli('build', str(self.dir / 'out'))
        self.assertEqual((code, 'operator decision D6' in err), (2, True))
        self.assertFalse((self.dir / 'out').exists())

    def test_a_record_that_is_not_a_plain_readable_file_is_refused(self):
        good = self.write('good.h', header_of(synthetic()))
        self.refused(str(self.dir / 'nope.h'), 'No such file')
        self.refused(str(self.dir), 'not a regular file')
        os.symlink(good, self.dir / 'link.h')
        self.refused(str(self.dir / 'link.h'), 'a link is refused')
        self.refused(self.write('empty.h', ''), 'empty')
        self.refused(self.write('big.h', 'x' * (tool.MAX_HEADER_BYTES + 1)), 'larger than')
        self.refused(self.write('binary.h', header_of(synthetic()).encode() + b'\xff', binary=True), 'not ASCII')

    def test_a_truncated_extended_or_damaged_record_is_refused(self):
        text = header_of(synthetic())
        self.refused(self.write('cut.h', ''.join(text.splitlines(keepends=True)[:6])), 'record bytes, a record is 96')
        self.refused(self.write('long.h', nv2.emit_header(nv2.pack(synthetic()) + b'\x00')), 'carries 97 record bytes')
        flipped = bytearray(nv2.pack(synthetic()))
        flipped[0x10] ^= 1
        self.refused(self.write('flipped.h', nv2.emit_header(bytes(flipped))), 'damaged (DENY_CRC)')
        wrong_magic = bytearray(nv2.pack(synthetic()))
        wrong_magic[0] ^= 1
        self.refused(self.write('magic.h', nv2.emit_header(nv2.seal(wrong_magic))), 'damaged (DENY_MAGIC)')

    def test_a_header_that_is_not_exactly_what_the_converter_writes_is_refused(self):
        """The header is #included into the monitor: anything but the converter's own text could be code in it."""
        text = header_of(synthetic())
        for name, edited in {'code after the bytes': text + '#define SNAP_INJECTED 1\n',
                             'an edited comment': text.replace('do not commit it', 'commit it'),
                             'windows line ends': text.replace('\n', '\r\n'),
                             'no final newline': text.rstrip('\n'),
                             'another array name': text.replace('snap_nv2_record_bytes', 'snap_other_bytes'),
                             'an extra declaration': text.replace('static const', 'extern int x; static const')}.items():
            with self.subTest(name):
                self.refused(self.write('edited.h', edited), 'not the header tools/nv2_record.py writes')

    def test_a_record_for_another_soc_or_one_the_gate_would_deny_is_refused(self):
        self.refused(self.write('soc.h', header_of(synthetic(soc=nv2.SOC['x2e-96-100']))), 'for SoC x2e-96-100')
        self.refused(self.write('unknown.h', header_of(synthetic(soc=nv2.SOC['unknown']))), 'for SoC unknown')
        for name, change, verdict in (
                ('vncr fail', dict(vncr_redirect=nv2.RESULT['fail']), 'DENY_VNCR_FAIL'),
                ('vncr not run', dict(vncr_redirect=nv2.RESULT['not-run']), 'DENY_VNCR_NOT_RUN'),
                ('control fail', dict(negative_control_trapped=nv2.RESULT['fail']), 'DENY_NEGCTL_FAIL'),
                ('control not run', dict(negative_control_trapped=nv2.RESULT['not-run']), 'DENY_NEGCTL_NOT_RUN'),
                ('inferred evidence', dict(evidence=nv2.EVIDENCE['INFERRED']), 'DENY_EVIDENCE'),
                ('no evidence', dict(evidence=nv2.EVIDENCE['NONE']), 'DENY_EVIDENCE'),
                ('NV2 not advertised', dict(id_aa64mmfr2_el1=0x01001234), 'DENY_NOT_ADVERTISED')):
            with self.subTest(name):
                self.refused(self.write('deny.h', header_of(synthetic(**change))), f'{verdict} (prepare status -9')

    def test_a_path_that_cannot_sit_in_a_c_string_literal_is_refused(self):
        for directory in ('with space', 'with"quote', 'back\\slash', "it's", 'caf\u00e9'):
            with self.subTest(directory=directory):
                (self.dir / directory).mkdir()
                self.refused(self.write(f'{directory}/record.h', header_of(synthetic())), 'C string literal')

    def test_a_valid_record_is_accepted_and_its_identity_is_printed(self):
        record = synthetic()
        header = self.write('record.h', header_of(record))
        code, out, err = cli('show', '--nv2-record-header', header)
        self.assertEqual((code, err), (0, ''))
        data = nv2.pack(record)
        for needle in ('x1e80100', '0x00000001', 'synthetic-doc', '0x00000000feed0001', '0x0000000000000000',
                       '0x0000000002001234', hashlib.sha256(data).hexdigest(), hashlib.sha256(header_of(record).encode()).hexdigest(),
                       'NOT RECORDED', 'operator decision D6', 'nothing here chooses'):
            self.assertIn(needle, out)
        code, flags, err = cli('flags', '--nv2-record-header', header)
        self.assertEqual((code, err), (0, ''))
        listed = json.loads(flags)['flags']
        self.assertEqual(listed[-1], f'-DSNAP_NV2_RECORD_HEADER="{header}"')
        self.assertEqual(tuple(listed[-1 - len(EXPECTED_DEFINES):-1]), EXPECTED_DEFINES)

    @unittest.skipUnless(LOCAL_RECORD.is_file() and LOCAL_RECORD.with_suffix('.bin').is_file(),
                         'the local record fixture build/nv2-record is absent')
    def test_the_local_record_prints_its_own_identity_and_is_not_a_decision(self):
        """LOCAL FIXTURE: the first X2 image's record (HCR_EL2.E2H = 1). A test input only; operator decision D6 is open."""
        record, data = nv2.load(str(LOCAL_RECORD.with_suffix('.bin')), 'bin')
        code, out, err = cli('show', '--nv2-record-header', str(LOCAL_RECORD))
        self.assertEqual((code, err), (0, ''), err)
        fields = nv2.fields_of(record)
        for key in ('soc', 'build_id', 'midr_el1', 'revidr_el1', 'id_aa64mmfr2_el1', 'id_aa64mmfr4_el1'):
            self.assertIn(fields[key], out)
        self.assertIn(hashlib.sha256(data).hexdigest(), out)
        self.assertIn('operator decision D6', out)


# --------------------------------------------------------------------------------------------------------------
# 3. The build

class Build(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.uefi, why = monitor_build_offline.locate_q1n1(ROOT)
        if cls.uefi is None:
            raise unittest.SkipTest(f'no q1n1 to link: {why}')
        for tool_path in (CLANG, str(monitor_build_offline.LLD)):
            if not shutil.which(tool_path) and not Path(tool_path).exists():
                raise unittest.SkipTest(f'{tool_path} not found')
        cls.holder = scratch('build-')
        cls.dir = Path(cls.holder.name).resolve()
        cls.header = cls.dir / 'record.h'
        cls.header.write_text(header_of(synthetic()), newline='')
        cls.out = cls.dir / 'm1'
        cls.code, cls.stdout, cls.stderr = cli('build', '--nv2-record-header', str(cls.header), str(cls.out))

    @classmethod
    def tearDownClass(cls):
        cls.holder.cleanup()

    def test_the_profile_builds_and_names_what_it_embedded(self):
        self.assertEqual(self.code, 0, self.stderr[-2000:])
        result = json.loads(self.stdout.strip().splitlines()[-1])
        self.assertEqual(result['profile'], 'sp11-m1')
        self.assertGreater(result['bytes'], 100000)
        self.assertLessEqual(result['bytes'], tool.code_window(ROOT))
        self.assertEqual(result['record_sha256'], hashlib.sha256(nv2.pack(synthetic())).hexdigest())
        blob = (self.out / 'xnuboot.bin').read_bytes()
        self.assertEqual((len(blob), hashlib.sha256(blob).hexdigest()), (result['bytes'], result['sha256']))

    def test_the_monitor_carries_the_record_exactly_once(self):
        self.assertEqual((self.out / 'xnuboot.bin').read_bytes().count(nv2.pack(synthetic())), 1)

    def test_the_build_record_rebuilds_the_identical_monitor(self):
        record = json.loads((self.out / 'profile-build.json').read_text())
        self.assertEqual((record['hardware'], record['profile']), ('NOT RUN', 'sp11-m1'))
        self.assertEqual(record['flags'][-1], f'-DSNAP_NV2_RECORD_HEADER="{self.header}"')
        again = self.dir / 'again'
        done = subprocess.run([sys.executable, '-B', str(ROOT / 'tools/monitor_build_offline.py'), str(ROOT), str(again),
                               str(self.out / 'profile-build.json')], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(done.returncode, 0, done.stderr[-2000:])
        rebuilt = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertEqual((rebuilt['sha256'], rebuilt['elf_sha256']),
                         (record['monitor']['sha256'], record['monitor']['elf_sha256']))

    def test_the_monitor_reads_cntvoff_el2_and_never_writes_it(self):
        if not Path(OBJDUMP).exists():
            self.skipTest('llvm-objdump not found')
        listing = subprocess.run([OBJDUMP, '-d', '--no-show-raw-insn', str(self.out / 'xnuboot.elf')], capture_output=True,
                                 text=True, check=True).stdout
        self.assertGreaterEqual(len(re.findall(r'\tmrs\t[^\n]*\bCNTVOFF_EL2\b', listing)), 1)
        self.assertEqual(len(re.findall(r'\tmsr\tCNTVOFF_EL2\b', listing)), 0)

    def test_without_a_usable_q1n1_nothing_is_built(self):
        out = self.dir / 'no-q1n1'
        with mock.patch.dict(os.environ, {monitor_build_offline.Q1N1_ENV: str(self.dir / 'nowhere')}):
            code, stdout, stderr = cli('build', '--nv2-record-header', str(self.header), str(out))
        self.assertEqual(code, 2)
        self.assertIn(monitor_build_offline.Q1N1_ENV, stderr)
        self.assertFalse(out.exists())

    def test_an_existing_output_directory_is_refused(self):
        code, _, stderr = cli('build', '--nv2-record-header', str(self.header), str(self.out))
        self.assertEqual(code, 2)
        self.assertIn('exists', stderr)

    def test_a_monitor_that_lacks_the_record_is_not_left_behind(self):
        out = self.dir / 'lacks'
        real = monitor_build_offline.build

        def build_without_the_record(tree, destination, flags):
            return real(tree, destination, [flag for flag in flags if 'NV2_RECORD_HEADER' not in flag])
        with mock.patch.object(monitor_build_offline, 'build', build_without_the_record):
            code, _, stderr = cli('build', '--nv2-record-header', str(self.header), str(out))
        self.assertEqual(code, 1)
        self.assertIn('0 times, not once', stderr)
        self.assertFalse((out / 'xnuboot.bin').exists() or (out / 'xnuboot.elf').exists() or (out / 'profile-build.json').exists())

    def test_a_monitor_larger_than_the_code_window_is_not_left_behind(self):
        window = tool.code_window(ROOT)
        self.assertEqual(window, 464 * 1024)            # OUT_AT - CODE_AT: "464 KiB reserved for probe code"
        out = self.dir / 'big'

        def oversize(tree, destination, flags):
            Path(destination).mkdir()
            (Path(destination) / 'xnuboot.bin').write_bytes(nv2.pack(synthetic()) + bytes(window))
            (Path(destination) / 'xnuboot.elf').write_bytes(b'elf')
            return dict(bytes=window + 96, sha256='0' * 64, elf_sha256='0' * 64)
        with mock.patch.object(monitor_build_offline, 'build', oversize):
            code, _, stderr = cli('build', '--nv2-record-header', str(self.header), str(out))
        self.assertEqual(code, 1)
        self.assertIn(f'more than the {window} that probes/run.py reserves', stderr)
        self.assertFalse((out / 'xnuboot.bin').exists() or (out / 'xnuboot.elf').exists() or (out / 'profile-build.json').exists())


if __name__ == '__main__':
    unittest.main()
