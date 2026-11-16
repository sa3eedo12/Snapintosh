#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of the authorisation record of the monitor entry run (monitor port work item 4b, Phase B; docs/SP11-STICK-MONITOR.md section 10): the
builder's --m1-run kind and its refusals (the options, the build report read against the files given as --monitor and --reporter, the PARAMS
words that must be zero), the text of the record (its touches, the time budget and D8, the local test record said to be one), the record checked
against the binaries (tools/sp11_monitor_census.py: every register the monitor and the wrapper can write is named, every name is in the binary, a
record that omits one fails), and the records that must not change (the dry run, the GIC and stop dry run and its second attempt).

    python3 tools/test-sp11-stick-monitor-auth.py

The fixtures of tools/test-sp11-stick-bundle-build.py are loaded here with a scratch directory of their own (build/sp11-monitor-auth-test-scratch/,
deleted at the end). The refusals and the text are tested on a FABRICATED monitor image and build report (no toolchain needed); the census and the
end-to-end build use a monitor built from a LOCAL TEST RECORD of the NV2 gate (tool `wi4b-test-only`: synthetic, never the unit's), and SKIP without
clang, ld.lld, the llvm tools and a q1n1 checkout ($Q1N1_SP11_TREE). The regression records need local inputs under build/ and, for the third,
$SP11_GICDRY2_DIR (the directory of its facts, binaries and note); without them they SKIP, which is not a pass. No hardware: NOT RUN."""
import contextlib
import hashlib
import importlib.util
import io
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

ROOT = Path(__file__).resolve().parents[1]
TOOLS, THIS = ROOT / 'tools', Path(__file__).resolve()
sys.path.insert(0, str(TOOLS))
import nv2_record as NV2  # noqa: E402
import sp11_monitor_census as C  # noqa: E402
import sp11_stick_bundle as sb  # noqa: E402
import sp11_stick_bundle_build as bb  # noqa: E402
import sp11_stick_bundle_chunks as ch  # noqa: E402
import sp11_stick_bundle_m1 as m1  # noqa: E402
import sp11_stick_bundle_record as rec  # noqa: E402
import sp11_stick_wrapper_build as wb  # noqa: E402
import sp11_stick_wrapper_testlib as T  # noqa: E402

spec = importlib.util.spec_from_file_location('sp11_stick_bundle_build_tests', TOOLS / 'test-sp11-stick-bundle-build.py')
BT = importlib.util.module_from_spec(spec)
spec.loader.exec_module(BT)
SCRATCH = ROOT / 'build' / 'sp11-monitor-auth-test-scratch'
STATE, MIB = {}, sb.MIB
sha = lambda data: hashlib.sha256(data).hexdigest()     # noqa: E731
LLVM = ('clang', 'ld.lld', 'llvm-objcopy', 'llvm-readelf', 'llvm-nm', 'llvm-objdump')
OWN = ('tools/sp11_stick_bundle_m1.py', 'tools/sp11_monitor_census.py', 'tools/test-sp11-stick-monitor-auth.py', 'tools/test-sp11-stick-monitor-interlock.py',
       'docs/SP11-STICK-MONITOR.md')
DOC_CAP = 60000         # raised from 46,000 on purpose (Phase D: section 14 and the corrections; 36,000 before Phase C); nothing was cut to make room
# The records the operator was shown or approved, regenerated here from the files they were made from (docs/SP11-MONITOR-PORT.md section 15 D4/D7).
RECORDS = {'dry run': '45c36225d9f23cc683aadce324ab2b56a34822b3358d0043a08e035977bf700e',
           'gic and stop dry run': 'c74f7af0217c507e7738673b9d437d6c76589ef3c243c23eef4837b0ca435b3b',
           'gic and stop dry run, second attempt': 'cf021df5cdff174c684dc9208e49d71089b349648db8fcedd00c2e4bd0d4374e'}
FIXED = dict(place=sb.placement(BT.BANK, 384 * BT.MIB, 48 * BT.MIB), stores=7, time_box_s=5, label='M1-FIXED', monitor=(445935, '1' * 64), reporter=(58463, '2' * 64),
             note=None, gic_stop=False, guest=dict(size=118870336, sha256='3' * 64, chunks=44, index='4' * 64))
# The SHA-256 of record_text for FIXED and FABRICATED[...]: the text of an M1 record is what the operator approves, so a changed byte is another record.
GOLDEN = {'a test record': '7db08474819ac1e73a77059c9bc568912d8bb68967376bd08ec29eaf35377566', 'another record': '1f8cb965877cbef212bd3f8a3cbe8d595d318a525f969b9443e3ebe038307f7f'}


def quietly(function, *args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = function(*args)
        except SystemExit as stop:
            code = stop.code
    return code, out.getvalue(), err.getvalue()


def tools_ready():
    return bool(STATE.get('tree') and all(T.find_tool(t) for t in LLVM))


# ---- A fabricated monitor image and build report: what tools/sp11_stick_wrapper_build.py --monitor writes, made from known bytes -------------
def nv2_bytes(tool='nv2-ingest', build_id=0x1234):
    return NV2.pack(NV2.Record(soc=NV2.SOC['x1e80100'], midr_el1=0x12345678, revidr_el1=0, id_aa64mmfr2_el1=0x02001234, id_aa64mmfr4_el1=0,
                               vncr_redirect=NV2.RESULT['pass'], negative_control_trapped=NV2.RESULT['pass'], evidence=NV2.EVIDENCE['OBSERVED'], tool=tool,
                               build_id=build_id))


def entry(data):
    return {'bytes': len(data), 'sha256': sha(data)}


def fake_wrapper(offset=0x20000):
    """5,000 bytes that stand for the wrapper: one BL, at 8, to the monitor's offset, as the build tool's wrapper has one, and no text of its own."""
    return b'WRAP' * 2 + struct.pack('<I', 0x94000000 | (((offset - 8) >> 2) & 0x3FFFFFF)) + b'WRAP' * 1247


def fabricate(tool='nv2-ingest', offset=0x20000, wrapper=None, monitor=None, record=None):
    """The parts of a build of SNAPMON-M1.BIN from known bytes, and the report that describes them."""
    record = nv2_bytes(tool) if record is None else record
    wrapper = fake_wrapper(offset) if wrapper is None else wrapper
    monitor = b'MON0' * 300 + record + b'MON1' * 300 if monitor is None else monitor
    image, reporter = wrapper + bytes(offset - len(wrapper)) + monitor, b'REPO' * 200
    report = {'files': {m1.IMAGE: entry(image), m1.REPORTER: entry(reporter)},
              'wrapper': {**entry(wrapper), 'padding': offset - len(wrapper)},
              'monitor': {'offset': offset, 'bytes': len(monitor), 'sha256': sha(monitor), 'elf_sha256': sha(b'an ELF'), 'build_record_sha256': sha(b'a record'),
                          'nv2_record': sha(record), 'relocations': {'R_AARCH64_CALL26': 7, 'R_AARCH64_ADR_PREL_LO21': 3}, 'call': 'bl sp11_monitor'},
              'hardware': 'NOT RUN', 'profile': 'sp11-m1', 'variant': 'm1: the wrapper arms the GIC, calls the appended monitor and puts the GIC back',
              'added': ['-DSNAP_SP11_STICK_MONITOR=1', *m1.pinned_defines(monitor, offset)]}
    return dict(image=image, reporter=reporter, report=report, monitor=monitor, wrapper=wrapper, record=record, offset=offset)


EFI = {'SLBOUNCE-DEVHE-MARK.EFI': '7' * 64, 'Q1N1-PAYLOAD.EFI': '8' * 64}        # what --m1-efi names in every record these tests build: nothing here stages them
EFI_ARGS = [f'{n}={d}' for n, d in EFI.items()]


def stage(name, fx=None, report=None, image=None, reporter=None, raw=None):
    """Write a fabricated build below the scratch directory, with any part replaced; returns the paths, and --m1-efi that goes with --m1-run."""
    fx = fx or STATE['fake']
    d = SCRATCH / 'fake' / name
    paths = dict(monitor=BT.write(d / 'SNAPMON-M1.BIN', fx['image'] if image is None else image),
                 reporter=BT.write(d / 'SNAPREP-M1.BIN', fx['reporter'] if reporter is None else reporter),
                 m1_run=BT.write(d / 'build-report.json', raw if raw is not None else json.dumps(fx['report'] if report is None else report).encode()), m1_efi=EFI_ARGS)
    return paths


def template(paths, **over):
    return BT.template(**{'stores': 'ram,el2_sysreg,gic', **paths, **over})


def mutated(report, fn):
    """A deep copy of `report` with `fn` applied."""
    data = json.loads(json.dumps(report))
    fn(data)
    return data


def setUpModule():
    shutil.rmtree(SCRATCH, ignore_errors=True)
    BT.SCRATCH = SCRATCH / 'bt'
    BT.setUpModule()
    STATE['tree'] = T.q1n1_tree()
    STATE['fake'] = fabricate()
    STATE['text'] = template(stage('good'))[1]
    if not tools_ready():
        return
    record = NV2.Record(soc=NV2.SOC['x1e80100'], midr_el1=0xFEED4B01, revidr_el1=0, id_aa64mmfr2_el1=0x02001234, id_aa64mmfr4_el1=0,
                        vncr_redirect=NV2.RESULT['pass'], negative_control_trapped=NV2.RESULT['pass'], evidence=NV2.EVIDENCE['OBSERVED'], tool='wi4b-test-only',
                        build_id=0x4B01)
    header = SCRATCH / 'record' / 'test-record.h'
    T.write(header, NV2.emit_header(NV2.pack(record)).encode())
    profile = T.load('sp11_m1_profile', TOOLS / 'sp11_m1_profile.py')
    code, out, err = quietly(profile.main, ['build', '--nv2-record-header', str(header), str(SCRATCH / 'profile')])
    assert code == 0, (out, err)
    STATE['profile'] = SCRATCH / 'profile'
    STATE['record'] = json.loads((STATE['profile'] / 'profile-build.json').read_text())
    default = str(ROOT / 'config/monitor-profile-sp11-m1.json')
    STATE['m1'] = wb.build_monitor(str(SCRATCH / 'm1'), default, str(STATE['profile'] / 'xnuboot.bin'), str(STATE['profile'] / 'profile-build.json'))
    STATE['real'] = dict(monitor=str(SCRATCH / 'm1' / 'SNAPMON-M1.BIN'), reporter=str(SCRATCH / 'm1' / 'SNAPREP-M1.BIN'), m1_run=str(SCRATCH / 'm1' / 'build-report.json'),
                         m1_efi=EFI_ARGS)
    STATE['real_text'] = template(STATE['real'])[1]


def tearDownModule():
    shutil.rmtree(SCRATCH, ignore_errors=True)
    BT.tearDownModule()


class Need(BT.Case):
    """Base of the tests that need the toolchain and a q1n1 checkout."""

    @classmethod
    def setUpClass(cls):
        if not tools_ready() or 'm1' not in STATE:
            raise unittest.SkipTest('no clang, ld.lld, llvm tools or q1n1 checkout (Q1N1_SP11_TREE): the test monitor is not built')


# ---- (a) the options ------------------------------------------------------------------------------------------------------------------
class Options(BT.Case):
    def test_the_kind_stores_in_all_three_classes(self):
        paths = stage('options')
        for stores in ('ram,el2_sysreg', 'ram,gic', 'el2_sysreg,gic', 'ram', 'gic'):
            with self.subTest(stores):
                self.refused('OPTIONS_BAD', BT.template(**{**paths, 'stores': stores}))
        self.assertEqual(template(paths)[0], 0)

    def test_the_kind_is_not_the_gic_and_stop_dry_run(self):
        code, text, _ = BT.cli(['authorisation-template', 'synthetic', *BT.opts(stores='ram,el2_sysreg,gic', **stage('options')), '--gic-stop'])
        self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=OPTIONS_BAD'))

    def test_the_time_box_is_the_one_the_wrapper_accepts(self):
        paths = stage('options')
        for box in (0, 6, 7, 255):
            with self.subTest(box):
                self.refused('OPTIONS_BAD', template(paths, time_box_s=box))
        for box in (1, 5):
            with self.subTest(box):
                code, text, err = template(paths, time_box_s=box)
                self.assertEqual(code, 0, err)
                self.assertIn(f'4. Time box: {box} s of wall time', text)
        header = (ROOT / 'probes/sp11-stick-monitor.h').read_text()
        self.assertEqual(int(re.search(r'#define SP11_STICK_MONITOR_BOX_MAX_S (\d+)u', header).group(1)), m1.BOX_MAX_S, 'the wrapper refuses a longer box before the call')

    def test_params_that_carry_another_time_box_than_the_record_are_refused(self):
        real = ch.synthetic_built
        with mock.patch.object(ch, 'synthetic_built', lambda xb, a, b, c, seed, box: real(xb, a, b, c, seed, box + 1)):
            self.refused('TIME_BOX_MISMATCH', template(stage('options')))
        self.assertEqual(BT.template(stores='ram,el2_sysreg')[0], 0, 'the other kinds are as they were')

    def test_the_other_kinds_have_none_of_it(self):
        plain = BT.template()[1]
        self.assertNotIn('1b.', plain)
        self.assertIn('1b. The monitor entry run', template(stage('options'))[1])
        self.assertEqual(BT.template(stores='ram,el2_sysreg,gic')[0], 0, 'a record of the other kinds is made without the flag')


# ---- (c, d) the build report and the files given as --monitor and --reporter ------------------------------------------------------------
class Report(BT.Case):
    def test_the_record_names_the_files_and_the_build_it_was_read_from(self):
        fx = STATE['fake']
        text, m = STATE['text'], fx['report']['monitor']
        for needle in (f'payload 0  SNAPMON.BIN  {len(fx["image"])} bytes  sha256 {sha(fx["image"])}', f'payload 1  SNAPREP.BIN  {len(fx["reporter"])} bytes  sha256 {sha(fx["reporter"])}',
                       f'file         SNAPMON-M1.BIN  {len(fx["image"])} bytes  sha256 {sha(fx["image"])}', f'wrapper      {len(fx["wrapper"])} bytes  sha256 {sha(fx["wrapper"])}',
                       f'then {fx["offset"] - len(fx["wrapper"])} zero bytes up to the monitor', f'xnuboot.bin at offset {fx["offset"]:#x}, {len(fx["monitor"])} bytes, profile sp11-m1',
                       f'sha256 {sha(fx["monitor"])}', f'elf sha256   {m["elf_sha256"]}', f'build record {m["build_record_sha256"]}', f'NV2 record   sha256 {sha(fx["record"])}',
                       f'reporter     SNAPREP-M1.BIN  {len(fx["reporter"])} bytes  sha256 {sha(fx["reporter"])}', '7 CALL26', '3 ADR_PREL_LO21',
                       'soc x1e80100  tool nv2-ingest  build_id 0x00001234  evidence OBSERVED', 'vncr_redirect pass  negative_control_trapped pass',
                       'MIDR_EL1 0x0000000012345678', 'ID_AA64MMFR2_EL1 0x0000000002001234'):
            self.assertIn(needle, text)

    def test_a_record_of_another_nv2_record_is_another_record(self):
        other = fabricate(record=nv2_bytes('nv2-ingest', 0x1235))
        self.assertNotEqual(template(stage('other', other))[1], STATE['text'])

    def test_the_local_test_record_is_said_to_be_one_and_another_is_not(self):
        test = template(stage('test-record', fabricate(tool=m1.TEST_TOOL)))[1]
        self.assertIn('LOCAL TEST RECORD: made by the host tests (tool wi4b-test-only), synthetic, never measured on a unit.', test)
        self.assertIn('this record must not be approved', ' '.join(test.split()))
        self.assertNotIn('LOCAL TEST RECORD', STATE['text'])
        self.assertIn('approving this record approves this NV2 record and no other', ' '.join(STATE['text'].split()))
        self.assertIn('D6 (which NV2 record', STATE['text'])

    def test_the_files_come_through_monitor_and_reporter_and_no_other_way(self):
        paths = stage('swapped')
        # The monitor image in the reporter's slot is refused first, for the reporter slot's own refusal (the call in it), not for the report.
        self.refused('WRAPPER_CALLS_MONITOR', BT.template(stores='ram,el2_sysreg,gic', **{**paths, 'monitor': paths['reporter'], 'reporter': paths['monitor']}))
        self.refused('M1_REPORT_MISMATCH', template(stage('wrong-reporter', reporter=b'another reporter')))
        self.refused('M1_REPORT_MISMATCH', template(stage('wrong-image', image=STATE['fake']['image'] + b'\0')))
        self.refused('M1_REPORT_MISMATCH', BT.template(stores='ram,el2_sysreg,gic', **{**paths, 'monitor': paths['reporter']}))

    def test_a_file_that_is_not_the_report_of_this_kind_is_refused(self):
        good = STATE['fake']['report']
        cases = {'hardware': lambda r: r.update(hardware='RUN'), 'profile': lambda r: r.update(profile='a16'), 'variant': lambda r: r.update(variant='plain'),
                 'no gate': lambda r: r['added'].remove('-DSNAP_SP11_STICK_MONITOR=1'), 'added not a list': lambda r: r.update(added='-DSNAP_SP11_STICK_MONITOR=1'),
                 'bool for an int': lambda r: r['monitor'].update(offset=True), 'float': lambda r: r['wrapper'].update(padding=1.5),
                 'zero bytes': lambda r: r['files'][m1.IMAGE].update({'bytes': 0}), 'bad digest': lambda r: r['monitor'].update(sha256='x' * 64),
                 'upper-case digest': lambda r: r['monitor'].update(sha256=r['monitor']['sha256'].upper()),                  'no nv2': lambda r: r['monitor'].pop('nv2_record'), 'no profile': lambda r: r.pop('profile'),
                 'relocations a list': lambda r: r['monitor'].update(relocations=[1]), 'relocation count 0': lambda r: r['monitor'].update(relocations={'R_AARCH64_CALL26': 0}),
                 'relocation name': lambda r: r['monitor'].update(relocations={'x': 1}), 'no reporter entry': lambda r: r['files'].pop(m1.REPORTER),
                 'files a list': lambda r: r.update(files=[]), 'no wrapper': lambda r: r.pop('wrapper'), 'huge number': lambda r: r['monitor'].update(offset=1 << 40)}
        for name, fn in cases.items():
            with self.subTest(name):
                self.refused('M1_REPORT_BAD', template(stage('bad-' + name.replace(' ', '-'), report=mutated(good, fn))))
        raws = {'empty': b'', 'not JSON': b'not json', 'a list': b'[]', 'a number': b'1', 'not UTF-8': b'\xff\xfe', 'duplicate keys': b'{"files": 1, "files": 2}',
                'deep': b'[' * 5000 + b']' * 5000}
        for name, raw in raws.items():
            with self.subTest(name):
                self.refused('M1_REPORT_BAD', template(stage('raw-' + name.replace(' ', '-'), raw=raw)))

    def test_the_file_itself_must_be_a_plain_regular_file_of_a_sane_size(self):
        paths = stage('files')
        d = SCRATCH / 'fake' / 'files'
        os.symlink(paths['m1_run'], d / 'link.json')
        (d / 'dir.json').mkdir()
        big = BT.write(d / 'big.json', b' ' * (m1.REPORT_CAP + 1))
        for name in (str(d / 'link.json'), str(d / 'dir.json'), str(d / 'absent.json'), big, ''):
            with self.subTest(name[-9:]):
                self.refused('M1_REPORT_BAD', template({**paths, 'm1_run': name}))

    def test_a_report_that_does_not_describe_the_files_is_refused(self):
        fx, good = STATE['fake'], STATE['fake']['report']
        report_cases = {'image digest': lambda r: r['files'][m1.IMAGE].update(sha256=sha(b'other')), 'image size': lambda r: r['files'][m1.IMAGE].update({'bytes': 1}),
                        'reporter digest': lambda r: r['files'][m1.REPORTER].update(sha256=sha(b'other')), 'wrapper digest': lambda r: r['wrapper'].update(sha256=sha(b'other')),
                        'padding': lambda r: r['wrapper'].update(padding=r['wrapper']['padding'] - 1), 'monitor digest': lambda r: r['monitor'].update(sha256=sha(b'other')),
                        'monitor size': lambda r: r['monitor'].update({'bytes': r['monitor']['bytes'] - 1}), 'offset not a page': lambda r: r['monitor'].update(offset=0x20800),
                        'offset moved a page': lambda r: r['monitor'].update(offset=0x21000), 'nv2 digest': lambda r: r['monitor'].update(nv2_record=sha(b'another record')),
                        'pinned digest': lambda r: r.update(added=['-DSNAP_SP11_STICK_MONITOR=1', *m1.pinned_defines(b'another monitor', 0x20000)]),
                        'pins missing': lambda r: r.update(added=['-DSNAP_SP11_STICK_MONITOR=1'])}
        for name, fn in report_cases.items():
            with self.subTest(name):
                self.refused('M1_REPORT_MISMATCH', template(stage('lie-' + name.replace(' ', '-'), report=mutated(good, fn))))
        image = bytearray(fx['image'])
        for name, at in (('a wrapper byte', 2), ('a padding byte', len(fx['wrapper']) + 5), ('a monitor byte', fx['offset'] + 10)):
            with self.subTest(name):
                altered = bytearray(image)
                altered[at] ^= 1
                altered = bytes(altered)
                report = mutated(good, lambda r: r['files'][m1.IMAGE].update(entry(altered)))
                self.refused('M1_REPORT_MISMATCH', template(stage('byte-' + name.replace(' ', '-'), image=altered, report=report)))

    def test_the_nv2_record_must_be_in_the_monitor_once_and_parse(self):
        record = nv2_bytes()
        for name, fx in (('absent', fabricate(monitor=b'MON0' * 600, record=record)), ('twice', fabricate(monitor=b'A' * 40 + record + b'B' * 40 + record + b'C' * 40, record=record)),
                         ('damaged', fabricate(monitor=b'A' * 40 + record[:-1] + b'\x55' + b'B' * 40, record=record[:-1] + b'\x55'))):
            with self.subTest(name):
                code, text, err = template(stage('nv2-' + name, fx))
                self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=M1_REPORT_MISMATCH'), err)
        self.assertEqual(len(m1.find_nv2_record(STATE['fake']['monitor'], sha(record))), 1)

    def test_a_detail_quotes_no_text_of_the_file(self):
        raws = [b'{"\\u001b[2J": 1, "\\u001b[2J": 2}', b'{"hardware": "\\u001b[31mNOT RUN"}', b'\xff\x1b[2J', json.dumps({**STATE['fake']['report'], 'profile': '\x1b[2J'}).encode()]
        for i, raw in enumerate(raws):
            code, text, err = template(stage(f'quote-{i}', raw=raw))
            self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=M1_REPORT_BAD'), err)
            self.assertTrue(all(c == '\n' or ' ' <= c <= '~' for c in err), 'the detail is printed on the terminal: printable ASCII only')
            self.assertNotIn('[2J', err)
            self.assertNotIn('[31m', err)

    def test_the_pinned_defines_are_the_wrapper_tools(self):
        monitor = STATE['fake']['monitor']
        self.assertEqual(m1.pinned_defines(monitor, 0x20000), wb.monitor_defines(monitor, 0x20000))
        self.assertEqual((m1.IMAGE, m1.REPORTER), wb.MONITOR_NAMES)

    def test_an_approval_covers_the_report_so_it_cannot_be_carried_to_another_build(self):
        record = BT.write(SCRATCH / 'records' / 'm1.txt', STATE['text'].encode())
        paths = stage('good')
        code, text, err, out = BT.build_cli('m1-build', extra=['--no-manifest', '--authorisation', record], approve=False, stores='ram,el2_sysreg,gic', **paths)
        self.assertEqual(code, 0, (text, err))
        pin = (out / 'bundle/SP11/PIN.DAT').read_bytes()
        self.assertEqual((pin[0x60:0x80].hex(), sb.get(pin, sb.PIN_LAYOUT, 'authorised_stores')), (sha(STATE['text'].encode()), 7))
        built = json.loads((out / 'build-report.json').read_text())
        self.assertEqual((built['m1']['image'], built['m1']['profile'], built['m1']['test_record']), (entry(STATE['fake']['image']), 'sp11-m1', False))
        other = stage('other-build', fabricate(record=nv2_bytes('nv2-ingest', 0x7777)))
        self.refused('AUTH_RECORD_MISMATCH', BT.build_cli('m1-other', extra=['--no-manifest', '--authorisation', record], approve=False, stores='ram,el2_sysreg,gic', **other))
        self.assertFalse((SCRATCH / 'bt' / 'out' / 'm1-other').exists())

    def test_a_note_is_section_6_of_this_kind_too(self):
        note = BT.write(SCRATCH / 'notes' / 'm1-note.txt', b'M1 NOTE: part of the digest.\n')
        code, text, err = template(stage('noted'), note=note)
        self.assertEqual(code, 0, err)
        self.assertEqual(text, STATE['text'] + '\n6. Notes of this build (part of the digest)\nM1 NOTE: part of the digest.\n')


# ---- (b) the PARAMS words that must be zero -------------------------------------------------------------------------------------------
LAUNCH = (('gpu.flags', 0x2340, 8), ('txm_native', 0x23C0, 32), ('early_stop.flags', 0x23E0, 8))       # the words of the brief of 4b, written out here again


class Launch(BT.Case):
    @staticmethod
    def params(**set_at):
        data = bytearray(m1.PARAMS_BYTES)
        for at, value in set_at.items():
            data[int(at[1:], 16)] = value
        return [dict(kind=sb.KIND_PARAMS, dest=0x88000C000, data=bytes(data))]

    def test_every_word_is_refused_at_its_first_and_last_byte_and_the_bytes_around_them_are_not(self):
        m1.check_launch(self.params())
        for name, at, size in LAUNCH:
            for byte in (at, at + size - 1):
                with self.subTest(f'{name} {byte:#x}'):
                    with self.assertRaises(ch.Refused) as caught:
                        m1.check_launch(self.params(**{f'x{byte:x}': 1}))
                    self.assertEqual(caught.exception.name, 'PARAMS_LAUNCH')
                    self.assertIn(name, caught.exception.detail)
        owned = {b for _, at, size in LAUNCH for b in range(at, at + size)}
        for byte in (0x233F, 0x2348, 0x23BF, 0x23E8, m1.PARAMS_BYTES - 1, 0, 192):
            self.assertNotIn(byte, owned)
            m1.check_launch(self.params(**{f'x{byte:x}': 1}))        # a word the check does not own is not its business (the time box is check_params')

    def test_a_chunk_that_cannot_hold_them_is_refused_as_a_shape(self):
        full = self.params()[0]
        for chunks in ([], [dict(kind=sb.KIND_PARAMS, dest=0, size=m1.PARAMS_BYTES)], [dict(full, data=full['data'][:m1.PARAMS_BYTES - 1])], [full, dict(full)]):
            with self.assertRaises(ch.Refused) as caught:
                m1.check_launch(chunks)
            self.assertEqual(caught.exception.name, 'BUILT_SHAPE')

    def test_the_pipeline_refuses_each_word_for_this_kind_only(self):
        real = ch.synthetic_built
        for name, at, size in LAUNCH:
            for byte in (at, at + size - 1):
                def built(xb, a, b, c, seed, box, byte=byte):
                    got = real(xb, a, b, c, seed, box)
                    lay = ch.layout_of(xb)
                    words = bytearray(got['chunks'][lay.params_pa])
                    words[byte] = 0x08
                    got['chunks'][lay.params_pa] = bytes(words)
                    return got
                with self.subTest(f'{name} {byte:#x}'), mock.patch.object(ch, 'synthetic_built', built):
                    self.refused('PARAMS_LAUNCH', template(stage('launch')))
                    self.assertEqual(BT.template(stores='ram,el2_sysreg')[0], 0, 'a record of another kind never calls the monitor: its PARAMS is not launched')

    def test_the_words_are_the_monitors_and_the_wrappers(self):
        header = (ROOT / 'probes/sp11-stick-monitor.h').read_text()
        c = {n: int(v, 0) for n, v in re.findall(r'#define (SM_PARAMS_\w+) (0x[0-9A-Fa-f]+|\d+)u', header)}
        self.assertEqual((c['SM_PARAMS_BYTES'], c['SM_PARAMS_GPU_FLAGS'], c['SM_PARAMS_EARLY_STOP_FLAGS']), (m1.PARAMS_BYTES, 0x2340, 0x23E0))
        self.assertEqual(m1.LAUNCH_WORDS, LAUNCH)
        self.assertEqual(ch.PARAMS_WORDS * 8, m1.PARAMS_BYTES)


class Layout(Need):
    """The offsets of struct params against the monitor's own translation unit, as probes/xnuboot.c compiles it."""

    def compile(self, asserts, name):
        sys.path.insert(0, str(TOOLS))
        import monitor_build_offline as offline
        uefi, why = offline.locate_q1n1(ROOT)
        self.assertIsNotNone(uefi, why)
        source = SCRATCH / 'drift' / f'{name}.c'
        T.write(source, f'#include "{ROOT / "probes/xnuboot.c"}"\n#include <stddef.h>\n{asserts}\n'.encode())
        done = subprocess.run([str(offline.LLVM / 'clang'), *STATE['record']['flags'], f'-I{ROOT / "shadow"}', f'-I{uefi}', '-fsyntax-only', str(source)], capture_output=True, text=True)
        return done

    def test_the_launch_words_are_where_and_as_wide_as_the_checks_say(self):
        want = {n: (at, size) for n, at, size in LAUNCH}
        asserts = '\n'.join([f'_Static_assert(sizeof(struct params) == {m1.PARAMS_BYTES:#x}, "size");',
                             f'_Static_assert(offsetof(struct params, gpu) + offsetof(struct adreno_params, flags) == {want["gpu.flags"][0]:#x}, "gpu");',
                             f'_Static_assert(sizeof(((struct params *)0)->gpu.flags) == {want["gpu.flags"][1]}, "gpu width");',
                             f'_Static_assert(offsetof(struct params, early_stop) + offsetof(struct early_stop_params, flags) == {want["early_stop.flags"][0]:#x}, "early");',
                             f'_Static_assert(sizeof(((struct params *)0)->early_stop.flags) == {want["early_stop.flags"][1]}, "early width");',
                             f'_Static_assert(offsetof(struct params, txm_native) == {want["txm_native"][0]:#x}, "txm");',
                             f'_Static_assert(sizeof(struct txm_native_params) == {want["txm_native"][1]}, "txm width");'])
        done = self.compile(asserts, 'launch')
        self.assertEqual(done.returncode, 0, done.stderr[-2500:])

    def test_the_drift_check_can_fail(self):
        self.assertNotEqual(self.compile('_Static_assert(offsetof(struct params, txm_native) == 0x23C8, "wrong");', 'wrong').returncode, 0)


# ---- (e, g) the text ----------------------------------------------------------------------------------------------------------------
class Text(BT.Case):
    def test_the_touches_of_this_kind_replace_the_other_kinds(self):
        text, flat = STATE['text'], ' '.join(STATE['text'].split())
        for name in rec.STORES:
            self.assertIn(f'   {name}: {rec.M1_TOUCHES[name][0]}\n', text)
            for other in (rec.TOUCHES, rec.GIC_STOP_TOUCHES):
                self.assertNotIn(' '.join(other[name][1].split()), flat)      # the first lines share words (the GIC frames); the next ones are the other kinds'
        self.assertIn('authorised_stores = 7', text)
        self.assertNotIn('docs/SP11-STICK-WRAPPER.md section 4', text)
        self.assertNotIn('GIC registers for the timer stop (PPI 26)', text)
        self.assertIn('Record of the monitor entry run (docs/SP11-STICK-MONITOR.md section 10): payload 0 is SNAPMON-M1.BIN.', text)

    def test_the_class_blocks_are_wrapped_by_hand_within_120_columns(self):
        text = STATE['text']
        self.assertTrue(text.isascii())
        block = text.split('3. Store classes')[1].split('   Not authorised')[0]
        self.assertLessEqual(max(map(len, block.splitlines())), 120)
        for part in (text.split('1b. ')[1].split('2. Placement')[0], text.split('4. Time box')[1].split('5. Refusal')[0]):
            self.assertLessEqual(max(map(len, part.splitlines())), 120)

    def test_the_time_budget_has_its_numbers_and_they_add_up(self):
        text = STATE['text']
        parts = {k: float(v) for k, v in re.findall(r'^ {6}(wrapper checks, copy and verify|GIC arm: the announcement and the wait|guest)\s+(\d+(?:\.\d+)?) s', text, re.M)}
        self.assertEqual(len(parts), 3, parts)
        stop = re.search(r'stop: clean of (\d+) MiB\s+about (\d+\.\d) s \(the plan\'s estimate, INFERRED, not measured\),\s+at most (\d+\.\d) s', text)
        low, high = (float(x) for x in re.search(r'the known parts\s+at least (\d+\.\d) s \(2\.9 \+ 8 \+ 5 \+ 0\.6\), at most (\d+\.\d) s, plus the prepare', text).groups())
        self.assertEqual(round(sum(parts.values()) + float(stop.group(2)), 1), low, 'the sum of the figures as printed: the plan\'s 0.6 s for the stop')
        self.assertEqual(round(sum(parts.values()) + float(stop.group(3)), 1), high, 'and with the bound of 2.9 s')
        place = re.search(r'guest_size (\d+) MiB  reserve (\d+) MiB', text)
        self.assertEqual(int(stop.group(1)), int(place.group(1)) + int(place.group(2)), 'the stop cleans the guest range and the reserve')
        self.assertEqual(float(stop.group(3)), round(0.96 * int(stop.group(1)) / 141.4, 1))
        self.assertRegex(text, r'monitor prepare\s+UNKNOWN')
        flat = ' '.join(text.split())
        self.assertIn(f'{low} to {high} s plus the prepare in all', flat)
        self.assertIn(f'the known parts alone are at least {low} s', flat)
        self.assertIn('224 s and 268 s', flat)
        self.assertIn('The earliest restart datum is about UPTIME 17 s (INFERRED: one boot, in a run that started secondary CPUs; the plan, section 10)', flat)
        self.assertIn('keeping or relaxing it, with the 8 s wait, is the operator\'s', flat)

    def test_the_time_budget_follows_the_time_box_and_the_placement(self):
        three = template(stage('box3'), time_box_s=3)[1]
        self.assertIn('4. Time box: 3 s', three)
        self.assertRegex(three, r'guest\s+3 s\s+the time box')
        low = {box: float(re.search(r'the known parts\s+at least (\d+\.\d) s', t).group(1)) for box, t in ((3, three), (5, STATE['text']))}
        self.assertEqual(round(low[5] - low[3], 1), 2.0)
        text = rec.record_text({**FIXED, 'm1': m1_facts()})
        self.assertRegex(text, r'stop: clean of 432 MiB\s+about 0\.6 s \(the plan\'s estimate, INFERRED, not measured\),\s+at most 2\.9 s')
        self.assertRegex(text, r'the known parts\s+at least 16\.5 s \(2\.9 \+ 8 \+ 5 \+ 0\.6\), at most 18\.8 s')

    def test_the_thermal_exception_is_asked_for_and_no_guard_is_claimed(self):
        flat = ' '.join(STATE['text'].split())
        for needle in ('Thermal (D8): no thermal guard exists on this path', 'only loads, from the A16\'s sensor addresses', 'where this machine\'s is UNKNOWN',
                       'It is closed by c->thermal', 'the builder required it zero (PARAMS_LAUNCH)', 'a PRIVATE gate, section 3b',
                       'compat/hamoa-thermal.c is not in the monitor', 'Nothing reads a temperature during the run',
                       'Approving this record accepts the exception D8 describes', 'the census sees system registers only, not MMIO'):
            self.assertIn(needle, flat)
        for claim in ('thermal guard is on', 'thermal guard on.', 'guarded by', 'TSENS is read', 'temperature is read'):
            self.assertNotIn(claim, flat)

    def test_what_the_text_cannot_show_is_labelled(self):
        flat = ' '.join(STATE['text'].split())
        for needle in ('Which group is reached when is INFERRED from the sources', 'no write below was run by the monitor on the unit', 'Hardware: NOT RUN', 'operator decision NOT RECORDED',
                       'The builder does not run it', 'UNKNOWN: never run on this unit', 'U3: whether PPI 26 reaches EL2 at all is UNKNOWN', 'OBSERVED once',
                       'whether the guest reaches any of it inside the time box is UNKNOWN (untried on Oryon)'):
            self.assertIn(needle, flat)
        self.assertNotIn('VERIFIED', STATE['text'], 'no label of a hardware result, and no VERIFIED of anything the host proves only for a binary')

    def test_the_stop_rules_of_the_other_kinds_are_kept_but_for_the_silence_rule_and_this_kind_adds_two(self):
        after = STATE['text'].split('5. Refusal and stop rules')[1]
        plain = BT.template()[1].split('5. Refusal and stop rules')[1]
        replaced = plain.replace(rec.SILENCE_RULE, '\n'.join(rec.M1_SILENCE_RULE))
        self.assertNotEqual(replaced, plain, 'the plain text holds the rule that this kind replaces')
        self.assertTrue(after.startswith(replaced.rstrip('\n')), 'the rules of every record come first, unchanged but for the silence rule')
        for line in rec.M1_STOP_RULES:
            self.assertIn(line, after)
        flat = ' '.join(after.split())
        self.assertIn('No page change for 60 seconds after the MON CALL line: note the last line and power cycle', flat)
        self.assertIn('an operator-safe wait and not a measurement', flat)
        self.assertNotIn('twice the time box after ENTER: note the last line', flat, 'the rule of the other kinds would end a legitimate run here')

    def test_an_m1_record_that_kept_the_rule_of_the_other_kinds_is_another_record(self):
        """The faulty case: with the old rule in place the text is not the pinned one, and it says what the monitor's silence makes wrong."""
        with mock.patch.object(rec, 'M1_SILENCE_RULE', (rec.SILENCE_RULE,)):
            old = rec.record_text({**FIXED, 'm1': m1_facts(test=True)})
        self.assertNotEqual(sha(old.encode()), GOLDEN['a test record'])
        self.assertIn('twice the time box after ENTER: note the last line', old)

    def test_the_text_is_pinned(self):
        fx = {'a test record': m1_facts(test=True), 'another record': m1_facts()}
        for name, facts in fx.items():
            with self.subTest(name):
                self.assertEqual(sha(rec.record_text({**FIXED, 'm1': facts}).encode()), GOLDEN[name])

    def test_one_record_is_of_one_kind(self):
        with self.assertRaises(AssertionError):
            rec.record_text({**FIXED, 'm1': m1_facts(), 'gic_stop': True})

    def test_the_sections_of_a_real_build_come_before_this_kinds(self):
        real = dict(kernelcache_sha256='5' * 64, guest_profile='j700ap-26A428', panel='withheld', sptm_hvc_calls=0, seed='sp11-stick-bundle', descriptor_sha256='6' * 64,
                    board_id='sp11', board_status='EXPERIMENTAL', ecam_windows=0)
        text = rec.record_text({**FIXED, 'm1': m1_facts(), 'real': real})
        self.assertLess(text.index('1a. What this build is'), text.index('1b. The monitor entry run'))
        self.assertLess(text.index('1b. The monitor entry run'), text.index('2. Placement'))


def m1_facts(test=False):
    """The facts read_report returns for a fixed fabricated build."""
    fx = fabricate(tool=m1.TEST_TOOL if test else 'nv2-ingest')
    return m1.read_report(json.dumps(fx['report']).encode(), fx['image'], fx['reporter'], dict(EFI))


# ---- (f) the record against the binaries --------------------------------------------------------------------------------------------
class Census(Need):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.monitor = C.census(STATE['profile'] / 'xnuboot.elf')
        cls.wrapper = C.census(SCRATCH / 'm1' / 'snapmon-m1.elf')
        cls.elf_sha = sha((STATE['profile'] / 'xnuboot.elf').read_bytes())
        cls.text = STATE['real_text']

    def check(self, text=None, **kw):
        return C.check(self.text if text is None else text, self.monitor, self.wrapper, kw.get('elf', self.elf_sha))

    def test_the_record_of_the_build_with_the_test_record_names_what_both_binaries_hold_and_nothing_else(self):
        self.assertEqual(self.check(), [])

    def test_the_census_is_of_the_binary_and_has_the_size_the_record_must_cover(self):
        self.assertGreater(len(self.monitor['msr']), 60, 'the monitor writes far more than the thirteen registers of the study')
        self.assertEqual(set(self.wrapper['msr']), {'ICC_PMR_EL1', 'ICC_BPR1_EL1', 'ICC_IGRPEN1_EL1'})
        self.assertEqual({k for k in self.wrapper['sys']}, {'DC CVAC', 'DC CIVAC', 'AT S1E2R', 'AT S1E2W', 'IC IALLUIS'})
        self.assertEqual(set(self.monitor['plain']), {'SMC', 'ERET', 'WFE'})
        self.assertEqual((self.monitor['undecoded'], self.wrapper['undecoded']), (0, 0))

    def test_the_groups_of_the_record_are_the_tools_grammar(self):
        self.assertEqual(sorted(C.groups_of(self.text)), ['monitor A', 'monitor B', 'monitor C', 'monitor D', 'monitor E', 'monitor F', 'monitor G', 'wrapper'])

    def test_a_record_that_omits_any_one_name_fails_naming_it(self):
        regs, ops, plain = (sorted(self.monitor['msr']), sorted(self.monitor['sys']), sorted(self.monitor['plain']))
        omitted = [name for name in (*regs, *ops, *plain)]
        self.assertGreater(len(omitted), 80)
        for name in omitted:
            with self.subTest(name):
                problems = self.check(omit(self.text, name))
                self.assertTrue([p for p in problems if name in p and 'does not name it' in p], (name, problems[:3]))

    def test_a_name_the_binary_lacks_fails_naming_it(self):
        group = '      monitor G, in the binary and not reached by this run'
        self.assertIn(group, self.text)
        for added, kind in (('TTBR1_EL2', 'neither writes nor reads'), ('HFGRTR_EL2', 'neither writes nor reads'), ('TLBI ALLE3', 'does not execute'), ('DC ZVA', 'does not execute'),
                            ('HVC', 'does not execute'), ('AT S1E3R', 'does not execute')):
            with self.subTest(added):
                problems = self.check(self.text.replace(group, group + f' ({added})', 1))
                self.assertTrue([p for p in problems if added in p and kind in p], problems[:3])

    def test_a_name_that_only_the_wrapper_has_is_checked_against_the_wrapper(self):
        problems = self.check(omit_in(self.text, 'wrapper', 'IC IALLUIS'))
        self.assertTrue([p for p in problems if 'the wrapper' in p and 'IC IALLUIS' in p], problems)
        self.assertFalse([p for p in problems if 'the monitor' in p], 'the monitor still names it in group D')
        problems = self.check(omit_in(self.text, 'monitor D', 'IC IALLUIS'))
        self.assertTrue([p for p in problems if 'the monitor' in p and 'IC IALLUIS' in p], problems)
        self.assertFalse([p for p in problems if 'the wrapper' in p])

    def test_the_record_must_name_the_elf_that_was_checked(self):
        problems = self.check(elf='0' * 64)
        self.assertTrue([p for p in problems if 'not the one section 1b of the record names' in p], problems)
        self.assertTrue([p for p in self.check(self.text.replace('elf sha256', 'elf sha25x')) if 'not the one section 1b' in p])

    def test_a_record_of_another_kind_is_not_one_of_these(self):
        problems = C.check(BT.template(stores='ram,el2_sysreg,gic')[1], self.monitor, self.wrapper)
        self.assertTrue([p for p in problems if 'no `monitor X:` group' in p], problems[:2])

    def test_an_interrupt_acknowledge_is_a_read_that_must_be_named(self):
        self.assertIn('ICC_IAR1_EL1', self.monitor['mrs'])
        problems = self.check(omit(self.text, 'ICC_IAR1_EL1'))
        self.assertTrue([p for p in problems if 'ICC_IAR1_EL1' in p and 'acknowledges an interrupt' in p], problems)

    def test_what_a_static_census_cannot_show_is_in_the_binary_and_the_text_says_so(self):
        self.assertGreater(self.monitor['indirect'], 100, 'BLR and BR sites that no pass follows: which group is reached when cannot be shown')
        self.assertIn('INFERRED from the sources', ' '.join(self.text.split()))

    def test_the_distributor_address_is_built_in_one_function_which_the_run_does_not_reach(self):
        flags = {m.group(1): int(m.group(2), 0) for m in map(lambda f: re.fullmatch(r'-D(SNAP_GICD_BASE)=(0x[0-9A-Fa-f]+)', f), STATE['record']['flags']) if m}
        base = flags['SNAP_GICD_BASE']
        funcs = T.parse_functions(T.disassemble(STATE['profile'] / 'xnuboot.elf'))
        holders = sorted({n.split('#')[0] for n, ins in funcs.items() for _, m, o in ins if m in ('mov', 'movk', 'movz', 'orr') and f'#{base:#x}' in o})
        self.assertEqual(holders, ['secondary_run'])
        self.assertIn('secondary_run', self.text, 'the record says which function it is')

    def test_the_tool_runs_from_the_command_line_and_exits_by_its_verdict(self):
        record = BT.write(SCRATCH / 'records' / 'real.txt', self.text.encode())
        args = ['check', '--monitor-elf', str(STATE['profile'] / 'xnuboot.elf'), '--wrapper-elf', str(SCRATCH / 'm1' / 'snapmon-m1.elf'), record]
        code, out, err = quietly(C.main, args)
        self.assertEqual((code, err), (0, ''))
        self.assertIn('CHECK PASS', out)
        self.assertIn('INFERRED, not shown', out)
        bad = BT.write(SCRATCH / 'records' / 'bad.txt', omit(self.text, 'VNCR_EL2').encode())
        code, out, err = quietly(C.main, [*args[:-1], bad])
        self.assertEqual(code, 1)
        self.assertIn('PROBLEM the monitor: writes VNCR_EL2', out)
        code, out, err = quietly(C.main, ['check', '--monitor-elf', str(SCRATCH / 'absent.elf'), record])
        self.assertEqual(code, 2)
        self.assertIn('CANNOT RUN', err)

    def test_the_end_to_end_template_is_built_from_the_test_record_and_says_so(self):
        text = STATE['real_text']
        self.assertIn('LOCAL TEST RECORD', text)
        report = STATE['m1']
        for needle in (f'file         SNAPMON-M1.BIN  {report["files"]["SNAPMON-M1.BIN"]["bytes"]} bytes  sha256 {report["files"]["SNAPMON-M1.BIN"]["sha256"]}',
                       f'sha256 {report["monitor"]["sha256"]}', f'elf sha256   {report["monitor"]["elf_sha256"]}', f'NV2 record   sha256 {report["monitor"]["nv2_record"]}',
                       'soc x1e80100  tool wi4b-test-only  build_id 0x00004b01', 'profile sp11-m1'):
            self.assertIn(needle, text)
        self.assertEqual(elf_digest_in(text), self.elf_sha)


def omit(text, name):
    """The record with every whole-word occurrence of `name` (a register, or an operation such as `TLBI ALLE1`) taken out of its section 3."""
    head, rest = text.split('3. Store classes', 1)
    body, tail = rest.split('   Not authorised', 1)
    return head + '3. Store classes' + re.sub(rf'(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])', '', body) + '   Not authorised' + tail


def omit_in(text, group, name):
    """The record with `name` taken out of one group of the el2_sysreg class."""
    out, current = [], None
    for line in text.splitlines():
        head = C.GROUP_HEAD.match(line)
        if head:
            current = head.group(1)
        elif line and not line.startswith('         '):
            current = None
        out.append(re.sub(rf'(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])', '', line) if current == group else line)
    return '\n'.join(out) + '\n'


def elf_digest_in(text):
    return re.search(r'^ {3}elf sha256\s+([0-9a-f]{64})', text, re.M).group(1)


# ---- the records that must not change ------------------------------------------------------------------------------------------------
class Regression(BT.Case):
    """The dry run, the GIC and stop dry run and its second attempt regenerate byte for byte from the files they were made from."""

    def regenerate(self, facts, base, monitor, reporter, stores, note, gic_stop):
        paths = [Path(p) for p in (facts, monitor, reporter, note)]
        missing = [p.name for p in paths if not p.is_file()]
        if missing or not BT.REAL_DUMPS:
            self.skipTest(f'no local input: {", ".join(missing) or "the firmware dumps under downloads/sp11"}')
        argv = ['authorisation-template', 'synthetic', '--facts', facts, '--base', base, '--guest-mib', '384', '--monitor', monitor, '--reporter', reporter, '--stores', stores,
                '--time-box-s', '5', '--note', note, *(['--gic-stop'] if gic_stop else [])]
        code, text, err = BT.cli(argv)
        self.assertEqual(code, 0, err)
        return sha(text.encode())

    def test_the_dry_run_record(self):
        d = ROOT / 'build/sp11-dryrun'
        got = self.regenerate(str(ROOT / 'build/sp11-facts.local.json'), '0x880000000', str(d / 'SNAPMON.BIN'), str(d / 'SNAPREP.BIN'), 'ram,el2_sysreg', str(d / 'note-dryrun.txt'), False)
        self.assertEqual(got, RECORDS['dry run'])

    def test_the_gic_and_stop_dry_run_record(self):
        d = ROOT / 'build/sp11-gic'
        got = self.regenerate(str(d / 'facts-gicdry.local.json'), '0x880000000', str(d / 'bin/SNAPMON-GIC.BIN'), str(d / 'bin/SNAPREP-GIC.BIN'), 'ram,el2_sysreg,gic', str(d / 'note-gicdry.txt'), True)
        self.assertEqual(got, RECORDS['gic and stop dry run'])

    def test_the_second_attempt_of_the_gic_and_stop_dry_run(self):
        named = os.environ.get('SP11_GICDRY2_DIR')
        if not named:
            self.skipTest('SP11_GICDRY2_DIR is not set (the directory with facts-gicdry2.local.json, binA/ and note-gicdry2.txt)')
        d = Path(named)
        got = self.regenerate(str(d / 'facts-gicdry2.local.json'), '0x880000000', str(d / 'binA/SNAPMON-GIC.BIN'), str(d / 'binA/SNAPREP-GIC.BIN'), 'ram,el2_sysreg,gic',
                              str(d / 'note-gicdry2.txt'), True)
        self.assertEqual(got, RECORDS['gic and stop dry run, second attempt'])


class Hygiene(unittest.TestCase):
    def test_the_new_files_are_ascii_with_an_spdx_line_within_the_size_limits_and_hold_no_machine_data(self):
        for rel in (*OWN, 'tools/sp11_stick_bundle_record.py', 'tools/sp11_stick_bundle_build.py'):
            data = (ROOT / rel).read_bytes()
            with self.subTest(rel):
                self.assertFalse([b for b in data if b > 127], 'ASCII only')
                if rel.endswith('.md'):
                    self.assertLessEqual(len(data), DOC_CAP)
                else:
                    self.assertIn(b'SPDX-License-Identifier: MIT', data[:400])
                    self.assertLess(len(data), 51200)
                    self.assertLess(data.count(b'\n'), 1500)
                for needle in BT.MACHINE_PATHS:
                    self.assertNotIn(needle.encode(), data)

    def test_the_refusal_names_are_the_new_modules_and_the_note_lists_them(self):
        note = (ROOT / 'docs/SP11-STICK-MONITOR.md').read_text()
        self.assertEqual(m1.REFUSALS, ('M1_REPORT_BAD', 'M1_REPORT_MISMATCH', 'PARAMS_LAUNCH', 'WRAPPER_CALLS_MONITOR', 'M1_NO_MONITOR_CALL'))
        self.assertFalse(set(m1.REFUSALS) & set(bb.BUILDER_REFUSALS), 'docs/SP11-STICK-BUILD.md lists BUILDER_REFUSALS name for name and has no room for these')
        self.assertTrue(set(m1.REFUSALS) <= ch.KNOWN_REFUSALS)
        self.assertTrue(all(re.fullmatch(r'[A-Z0-9_]{1,24}', n) for n in m1.REFUSALS))
        self.assertFalse(set(m1.REFUSALS) & {n for _, n, _ in sb.ERRORS})
        for name in m1.REFUSALS:
            self.assertIn(name, note)
        for name in ('--m1-run', 'tools/sp11_monitor_census.py', 'tools/sp11_stick_bundle_m1.py', 'M1_TOUCHES'):
            self.assertIn(name, note)

    def test_the_build_note_points_at_the_kind_and_its_cap_was_raised_deliberately(self):
        build = (ROOT / 'docs/SP11-STICK-BUILD.md').read_text()
        self.assertIn('`--m1-run BUILD_REPORT`', build)
        self.assertIn('docs/SP11-STICK-MONITOR.md', build)
        self.assertLessEqual(len(build.encode()), 33200)
        self.assertIn('33200', (TOOLS / 'test-sp11-stick-bundle-build.py').read_text(), 'the cap of docs/SP11-STICK-BUILD.md in its own test')

    def test_the_flag_is_documented_where_the_options_are(self):
        done = quietly(bb.main, ['authorisation-template', 'synthetic', '--help'])
        self.assertIn('--m1-run BUILD_REPORT', ' '.join(done[1].split()))


if __name__ == '__main__':
    unittest.main()
