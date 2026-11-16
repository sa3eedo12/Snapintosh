#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of the review addendum to monitor port work item 4b, Phase B (docs/SP11-STICK-MONITOR.md section 12): the interlock between the record of
the monitor entry run and every other record (a wrapper that calls a monitor is refused under the others, one that calls none under this one), what the
record and the note say about the native PAC helper, the early restores and the secondary-CPU code, each checked against the census of the binary and
against the sources it cites, and the corrected statements of the note.

    python3 tools/test-sp11-stick-monitor-interlock.py

The fixtures of tools/test-sp11-stick-monitor-auth.py are loaded here with a scratch directory of their own (build/sp11-monitor-interlock-test-scratch/,
deleted at the end). The interlock is tested on FABRICATED images and reports (no toolchain), and on binaries built from a LOCAL TEST RECORD of the NV2
gate; the census and the binaries need clang, ld.lld, the llvm tools and a q1n1 checkout ($Q1N1_SP11_TREE), and SKIP without them, which is not a pass.
The wrappers of the pinned regression records are used where their files exist (build/sp11-dryrun, build/sp11-gic, and $SP11_GICDRY2_DIR for the third).
No hardware: NOT RUN."""
import importlib.util
import os
import re
import shutil
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))
import sp11_monitor_census as C  # noqa: E402
import sp11_stick_bundle_m1 as m1  # noqa: E402
import sp11_stick_bundle_record as rec  # noqa: E402
import sp11_stick_wrapper_build as wb  # noqa: E402

spec = importlib.util.spec_from_file_location('sp11_monitor_auth_tests', TOOLS / 'test-sp11-stick-monitor-auth.py')
AUTH = importlib.util.module_from_spec(spec)
spec.loader.exec_module(AUTH)
BT = AUTH.BT
SCRATCH = ROOT / 'build' / 'sp11-monitor-interlock-test-scratch'
AUTH.SCRATCH = SCRATCH
STATE = {}
sha = AUTH.sha
NOTE = ROOT / 'docs/SP11-STICK-MONITOR.md'
NOT_CALLED = b'MONITOR NOT CALLED: UNIMPLEMENTED IN THIS BUILD'
# The contract of the detection, written out again here so that a changed constant fails a test instead of changing what the tests do.
PAD, PAGE, SCAN = 16, 4096, 1 << 20
STAGE = (b'MON REGS RESTORED=', b'MON QUARANTINE: THE REPORT IS PARTIAL')
SECONDARY = {'secondary_entry', 'secondary_run', 'secondary_power_off', 'secondary_gic_stop', 'secondary_release', 'secondary_guest_enter', 'xnuboot_secondary_stop', 'early_off'}
PAC_CODE = {'pauth_native_user', 'pauth_native_sign_user', 'pauth_pacga', 'pauth_switch_enter', 'pauth_switch_leave'}
KEYS = [f'AP{k}Key{half}_EL1' for k in ('IA', 'IB', 'DA', 'DB') for half in ('Lo', 'Hi')]


def setUpModule():
    shutil.rmtree(SCRATCH, ignore_errors=True)
    AUTH.setUpModule()
    if AUTH.tools_ready():
        default = str(ROOT / 'config/monitor-profile-sp11-m1.json')
        STATE['plain'] = wb.build(str(SCRATCH / 'plain'), default)
        STATE['gic'] = wb.build(str(SCRATCH / 'gic'), default, False, True)


def tearDownModule():
    AUTH.tearDownModule()
    shutil.rmtree(SCRATCH, ignore_errors=True)


def other_kind(paths, gic_stop):
    """(code, stdout, stderr) of the template of a kind that is not the M1 kind, given the files of `paths` as --monitor and --reporter."""
    over = dict(monitor=paths['monitor'], reporter=paths['reporter'], stores='ram,el2_sysreg,gic' if gic_stop else 'ram,el2_sysreg')
    return BT.cli(['authorisation-template', 'synthetic', *BT.opts('synthetic', **over), *(['--gic-stop'] if gic_stop else [])])


def call_image(target=0x2000, pad=PAD, after=b'\x01' * 16, pc=0x10, size=None):
    """A flat image of non-zero filler with one BL at `pc` to `target`, where `pad` zero bytes end and `after` begins."""
    size = size or target + 256
    data = bytearray(b'\xaa' * size)
    data[target - pad:target] = bytes(pad)
    data[target:target + len(after)] = after
    data[pc:pc + 4] = struct.pack('<I', 0x94000000 | (((target - pc) >> 2) & 0x3FFFFFF))
    return bytes(data)


class Interlock(BT.Case):
    """(i) Every kind of record but the M1 kind refuses a --monitor that calls a monitor."""

    def test_a_wrapper_that_calls_a_monitor_is_refused_under_the_dry_run_and_the_gic_stop_kinds(self):
        paths = AUTH.stage('calls')
        for gic_stop in (False, True):
            with self.subTest(gic_stop=gic_stop):
                code, text, err = other_kind(paths, gic_stop)
                self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=WRAPPER_CALLS_MONITOR'), err)
                self.assertIn('a BL at 0x8 calls the zero-padded page at 0x20000', err)
                self.assertIn('give --m1-run', err)

    def test_the_controls_the_other_kinds_accept_a_wrapper_that_calls_none_and_the_m1_kind_accepts_the_monitor(self):
        stand_in = dict(monitor=BT.FIX['monitor'], reporter=BT.FIX['reporter'])
        for gic_stop in (False, True):
            self.assertEqual(other_kind(stand_in, gic_stop)[0], 0, gic_stop)
        self.assertEqual(AUTH.template(AUTH.stage('calls'))[0], 0)

    def test_the_build_command_refuses_it_before_it_builds_anything(self):
        paths = AUTH.stage('calls-build')
        code, text, err, out = BT.build_cli('calls-build', extra=['--no-manifest'], approve=False, stores='ram,el2_sysreg', monitor=paths['monitor'], reporter=paths['reporter'])
        self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=WRAPPER_CALLS_MONITOR'), (text, err))
        self.assertFalse(out.exists())

    def test_the_two_refusals_are_one_each_way_and_are_the_builders_known_names(self):
        self.assertTrue({'WRAPPER_CALLS_MONITOR', 'M1_NO_MONITOR_CALL'} <= set(m1.REFUSALS))
        self.assertTrue(set(m1.REFUSALS) <= BT.ch.KNOWN_REFUSALS)


class Signals(BT.Case):
    """What the file is searched for, and where the search stops."""

    def test_the_call_is_a_bl_into_a_zero_padded_page_and_each_part_of_that_is_needed(self):
        self.assertEqual(len(m1.appended_image_calls(call_image())), 1)
        cases = {'15 zero bytes only': call_image(pad=PAD - 1), 'a target that is not a page': call_image(target=0x2004),
                 'nothing but zeros after it': call_image(after=bytes(16)), 'a target beyond the file': call_image()[:0x1000]}
        for name, image in cases.items():
            with self.subTest(name):
                self.assertEqual(m1.appended_image_calls(image), [], name)
        self.assertEqual(m1.appended_image_calls(call_image(target=0x1000, pc=0x2000, size=0x3000)), [(0x2000, 0x1000)], 'a call backwards is a call')

    def test_the_search_for_the_call_stops_after_the_first_mebibyte_and_the_limit_is_documented(self):
        edge = SCAN
        size = edge + 0x30000
        seen = call_image(target=edge + 0x1000, pc=edge - 4, size=size)
        missed = call_image(target=edge + 0x1000, pc=edge + 4, size=size)
        self.assertEqual(len(m1.monitor_call_evidence(seen)), 1)
        self.assertEqual(m1.monitor_call_evidence(missed), [], 'a call after the first MiB is not seen: what the detection cannot show')
        for needle in ('first SCAN_BYTES', 'indirect branch', 'false positive'):
            self.assertIn(needle, ' '.join(m1.monitor_call_evidence.__doc__.split()))

    def test_the_constants_are_the_contract_the_note_describes(self):
        self.assertEqual((m1.PAD_BYTES, m1.PAGE, m1.SCAN_BYTES, m1.STAGE_TEXT), (PAD, PAGE, SCAN, STAGE))
        self.assertEqual(m1.NOT_CALLED_TEXT, NOT_CALLED[:len(m1.NOT_CALLED_TEXT)])
        self.assertEqual(NOT_CALLED[:18], b'MONITOR NOT CALLED')

    def test_the_text_of_the_stage_is_enough_by_itself_wherever_it_is(self):
        for text in STAGE:
            self.assertEqual(len(m1.monitor_call_evidence(b'\xaa' * 5000 + text + b'\xaa' * (SCAN + 100))), 1, text)
        self.assertEqual(m1.monitor_call_evidence(b'\xaa' * 5000 + NOT_CALLED), [], 'the text of a wrapper that calls none is not the stage text')

    def test_a_refusal_detail_names_offsets_and_our_own_words_only(self):
        with self.assertRaises(m1.Refused) as caught:
            m1.refuse_monitor_wrapper(b'\x1b[2J' * 8 + STAGE[0])
        self.assertTrue(caught.exception.detail.isascii())
        self.assertNotIn('[2J', caught.exception.detail)


class Binaries(AUTH.Need):
    """The interlock on the binaries: the one that calls the test monitor, and the three that call none."""

    def test_the_m1_image_is_found_by_both_signals_and_the_wrapper_alone_by_the_text(self):
        image, wrapper = Path(AUTH.STATE['real']['monitor']).read_bytes(), (SCRATCH / 'm1' / 'wrapper-m1.bin').read_bytes()

        def scrub(data):
            for text in STAGE:
                data = data.replace(text, b'\x20' * len(text))
            return data
        found = m1.monitor_call_evidence(image)
        self.assertEqual(len(m1.appended_image_calls(image)), 1)
        self.assertEqual(len(found), 1 + len(STAGE), found)
        self.assertEqual(m1.appended_image_calls(wrapper), [], 'the wrapper alone has no page after it to call')
        self.assertEqual(len(m1.monitor_call_evidence(wrapper)), len(STAGE))
        self.assertEqual(len(m1.monitor_call_evidence(scrub(image))), 1, 'with the text taken out the call is still seen')
        self.assertEqual(m1.monitor_call_evidence(scrub(wrapper)), [], 'and with neither there is nothing')

    def test_the_real_m1_image_is_refused_under_the_other_kinds_and_accepted_under_its_own(self):
        paths = AUTH.STATE['real']
        for gic_stop in (False, True):
            code, text, err = other_kind(paths, gic_stop)
            self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=WRAPPER_CALLS_MONITOR'), (gic_stop, err))
        self.assertEqual(AUTH.template(paths)[0], 0)

    def test_the_reporter_of_this_kind_prints_the_same_words_and_is_not_a_wrapper(self):
        reporter = (SCRATCH / 'm1' / 'SNAPREP-M1.BIN').read_bytes()
        self.assertTrue(m1.monitor_call_evidence(reporter), 'it decodes the monitor record, so as payload 0 it would be taken for a monitor stage')

    def test_the_wrappers_built_here_without_a_monitor_are_not_flagged(self):
        for name, key, file in (('the dry run wrapper', 'plain', 'SNAPMON.BIN'), ('the GIC and stop wrapper', 'gic', 'SNAPMON-GIC.BIN')):
            with self.subTest(name):
                self.assertEqual(m1.monitor_call_evidence((SCRATCH / key / file).read_bytes()), [])
        self.assertEqual(m1.monitor_call_evidence(Path(BT.FIX['monitor']).read_bytes()), [])

    def pinned(self, path):
        path = Path(path)
        if not path.is_file():
            self.skipTest(f'{path.name}: no local copy of the wrapper that a pinned record was made from')
        return path.read_bytes()

    def test_the_pinned_dry_run_wrapper_is_not_flagged(self):
        self.assertEqual(m1.monitor_call_evidence(self.pinned(ROOT / 'build/sp11-dryrun/SNAPMON.BIN')), [])

    def test_the_pinned_gic_and_stop_wrapper_is_not_flagged(self):
        self.assertEqual(m1.monitor_call_evidence(self.pinned(ROOT / 'build/sp11-gic/bin/SNAPMON-GIC.BIN')), [])

    def test_the_second_attempts_wrapper_calls_a_page_and_only_the_zero_padding_keeps_it_from_being_flagged(self):
        named = os.environ.get('SP11_GICDRY2_DIR')
        if not named:
            self.skipTest('SP11_GICDRY2_DIR is not set')
        data = self.pinned(Path(named) / 'binA/SNAPMON-GIC.BIN')
        pages = [t for t in m1.bl_calls(data) if t % PAGE == 0 and 0 < t < len(data)]
        self.assertTrue(pages, 'this wrapper calls a page-aligned function: a detector without the zero-padding rule would refuse the record of its dry run')
        self.assertEqual(m1.monitor_call_evidence(data), [])


class M1Kind(BT.Case):
    """(ii) The M1 kind refuses a wrapper that calls no monitor, and a monitor that the build report does not name."""

    def staged(self, tag, **kw):
        return AUTH.template(AUTH.stage(tag, AUTH.fabricate(**kw)))

    def test_a_wrapper_that_calls_none_is_refused_though_the_report_describes_it(self):
        fake = bytearray(AUTH.fake_wrapper())
        fake[100:100 + len(NOT_CALLED)] = NOT_CALLED
        cases = {'no bl at all': b'WRAP' * 1250, 'a bl to the next page': AUTH.fake_wrapper(0x21000), 'a bl to the page before': AUTH.fake_wrapper(0x1F000),
                 'a bl and the text that says none is called': bytes(fake)}
        for name, wrapper in cases.items():
            with self.subTest(name):
                code, text, err = self.staged('nocall-' + name.replace(' ', '-'), wrapper=wrapper)
                self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=M1_NO_MONITOR_CALL'), err)
        self.assertEqual(self.staged('call-control')[0], 0)

    def test_the_call_of_a_wrapper_longer_than_the_scan_of_the_other_kinds_is_found(self):
        offset = 0x200000
        wrapper = bytearray(b'WRAP' * 300_000)
        pc = len(wrapper) - 0x100
        wrapper[pc:pc + 4] = struct.pack('<I', 0x94000000 | (((offset - pc) >> 2) & 0x3FFFFFF))
        self.assertEqual(self.staged('far-call', offset=offset, wrapper=bytes(wrapper))[0], 0)

    def test_a_monitor_that_the_build_report_does_not_name_is_refused(self):
        fx = AUTH.fabricate()
        other = AUTH.fabricate(monitor=b'MON9' * 400 + fx['record'] + b'MONA' * 100)
        image = fx['wrapper'] + bytes(fx['offset'] - len(fx['wrapper'])) + other['monitor']
        self.refused('M1_REPORT_MISMATCH', AUTH.template(AUTH.stage('other-monitor', fx, image=image)))

        def rewrite(report):
            report['files'][m1.IMAGE].update(AUTH.entry(image))
            report['monitor'].update(bytes=len(other['monitor']), sha256=sha(other['monitor']))
        self.refused('M1_REPORT_MISMATCH', AUTH.template(AUTH.stage('forged', fx, image=image, report=AUTH.mutated(fx['report'], rewrite))))

    def test_the_other_way_is_a_different_refusal_from_a_report_that_does_not_match(self):
        names = {m1.Refused('M1_NO_MONITOR_CALL', '').name, m1.Refused('M1_REPORT_MISMATCH', '').name}
        self.assertEqual(len(names), 2)


class ReporterSlot(BT.Case):
    """Hardening of the interlock: the reporter (payload 1) is executed in the same boot, so --reporter is refused for the same name when it holds the
    call to an appended monitor, under every kind. The call alone is looked for: SNAPREP-M1.BIN has the text of the monitor stage by design."""

    def calling(self):
        return AUTH.STATE['fake']['image']

    def test_a_reporter_that_calls_a_monitor_is_refused_under_the_default_the_gic_stop_and_the_m1_kinds(self):
        reporter = BT.write(SCRATCH / 'reporter-calls' / 'SNAPREP.BIN', self.calling())
        for gic_stop in (False, True):
            with self.subTest(gic_stop=gic_stop):
                code, text, err = other_kind(dict(monitor=BT.FIX['monitor'], reporter=reporter), gic_stop)
                self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=WRAPPER_CALLS_MONITOR'), err)
                self.assertIn('--reporter calls a monitor', err)
        code, text, err = AUTH.template(AUTH.stage('reporter-calls-m1', reporter=self.calling()))
        self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=WRAPPER_CALLS_MONITOR'), err)
        self.assertIn('--reporter calls a monitor', err, 'the reporter is looked at before the report is read: not M1_REPORT_MISMATCH')

    def test_the_controls_a_reporter_that_calls_none_is_accepted_under_every_kind(self):
        for gic_stop in (False, True):
            self.assertEqual(other_kind(dict(monitor=BT.FIX['monitor'], reporter=BT.FIX['reporter']), gic_stop)[0], 0, gic_stop)
        self.assertEqual(AUTH.template(AUTH.stage('reporter-control'))[0], 0)

    def test_the_detail_names_the_option_and_the_stage_text_alone_refuses_a_wrapper_but_not_a_reporter(self):
        with self.assertRaises(m1.Refused) as reporter:
            m1.refuse_monitor_reporter(self.calling())
        with self.assertRaises(m1.Refused) as wrapper:
            m1.refuse_monitor_wrapper(self.calling())
        self.assertEqual((reporter.exception.name, wrapper.exception.name), ('WRAPPER_CALLS_MONITOR', 'WRAPPER_CALLS_MONITOR'))
        self.assertIn('--reporter', reporter.exception.detail)
        self.assertNotIn('--monitor', reporter.exception.detail)
        self.assertIn('--monitor', wrapper.exception.detail)
        self.assertNotIn('--reporter', wrapper.exception.detail)
        text_only = b'\xaa' * 5000 + STAGE[0] + b'\xaa' * 5000
        m1.refuse_monitor_reporter(text_only)
        with self.assertRaises(m1.Refused):
            m1.refuse_monitor_wrapper(text_only)


class ReporterBinaries(AUTH.Need):
    """The reporter slot on the binaries: the real wrapper of the M1 build in it is refused, and no reporter that ran or is staged is flagged."""

    def test_the_real_m1_wrapper_as_the_reporter_is_refused_under_every_kind_and_the_real_reporter_is_accepted(self):
        real = AUTH.STATE['real']
        for gic_stop in (False, True):
            code, text, err = other_kind(dict(monitor=BT.FIX['monitor'], reporter=real['monitor']), gic_stop)
            self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=WRAPPER_CALLS_MONITOR'), (gic_stop, err))
            self.assertIn('--reporter calls a monitor', err)
        code, text, err = AUTH.template({**real, 'reporter': real['monitor']})
        self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=WRAPPER_CALLS_MONITOR'), err)
        self.assertEqual(AUTH.template(real)[0], 0, 'the control: SNAPREP-M1.BIN as --reporter under --m1-run')

    def test_the_reporter_of_this_kind_has_the_stage_text_and_is_not_refused(self):
        reporter = (SCRATCH / 'm1' / 'SNAPREP-M1.BIN').read_bytes()
        self.assertTrue(m1.monitor_call_evidence(reporter), 'the text of the stage is in it: the signal that --monitor uses would refuse it')
        m1.refuse_monitor_reporter(reporter)

    def pinned(self, path):
        path = Path(path)
        if not path.is_file():
            self.skipTest(f'{path.name}: no local copy of the reporter that a pinned record was made from')
        return path.read_bytes()

    def test_no_reporter_that_ran_or_is_staged_is_flagged(self):
        for name, key, file in (('the dry run reporter', 'plain', 'SNAPREP.BIN'), ('the GIC and stop reporter', 'gic', 'SNAPREP-GIC.BIN')):
            with self.subTest(name):
                m1.refuse_monitor_reporter((SCRATCH / key / file).read_bytes())
        m1.refuse_monitor_reporter(Path(BT.FIX['reporter']).read_bytes())

    def test_the_pinned_reporters_are_not_flagged(self):
        named = os.environ.get('SP11_GICDRY2_DIR')
        for path in (ROOT / 'build/sp11-dryrun/SNAPREP.BIN', ROOT / 'build/sp11-gic/bin/SNAPREP-GIC.BIN', Path(named or '/nonexistent') / 'binA/SNAPREP-GIC.BIN'):
            with self.subTest(path.name):
                m1.refuse_monitor_reporter(self.pinned(path))


class Reach(AUTH.Need):
    """The record's claims of reach, against the census and the call graph of the monitor built from the local test record."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        elf = AUTH.STATE['profile'] / 'xnuboot.elf'
        cls.cen = C.census(elf)
        cls.callers = {}
        current = None
        for line in C.disassemble(elf).splitlines():
            label = re.match(r'^[0-9a-f]+ <(.+)>:$', line)
            if label:
                current = label.group(1)
                continue
            call = re.match(r'^\s+[0-9a-f]+:\s+bl\s+0x[0-9a-f]+ <([^>+]+)>', line)
            if call and current:
                cls.callers.setdefault(call.group(1), set()).add(current)
        cls.text = AUTH.STATE['real_text']
        cls.groups = C.groups_of(cls.text)

    def writers(self, name):
        return set(self.cen['msr'].get(name) or self.cen['sys'].get(name) or self.cen['plain'].get(name) or ())

    def test_sctlr_el2_tcr_el2_and_tlbi_alle2_are_written_only_by_the_two_pac_helpers_and_the_secondary_entry(self):
        want = {'pauth_native_user', 'pauth_native_sign_user', 'secondary_entry'}
        for name in ('SCTLR_EL2', 'TCR_EL2', 'TLBI ALLE2'):
            self.assertEqual(self.writers(name), want, name)

    def test_the_registers_only_the_secondary_code_writes(self):
        only = {'secondary_run': ('CPTR_EL2', 'CPACR_EL1', 'ICC_SRE_EL2', 'ICC_PMR_EL1', 'ICC_BPR1_EL1', 'ICC_IGRPEN1_EL1'),
                'secondary_entry': ('MAIR_EL2', 'TTBR0_EL2', 'SPSel', 'IC IALLU'), 'secondary_power_off': ('DC CVAC',)}
        for function, names in only.items():
            for name in names:
                self.assertEqual(self.writers(name), {function}, name)
        self.assertEqual(self.writers('WFE'), {'early_off', 'secondary_power_off'})
        self.assertEqual(self.writers('SMC'), {'early_off', 'gpu_smc', 'secondary_power_off', 'secondary_release', 'xnuboot_report'})

    def test_every_name_the_record_calls_unreached_is_written_only_by_the_secondary_code(self):
        unreached = ' '.join(self.groups['monitor G'].split()).split('The secondary entry also writes')[0].split('The GPU calls')[0]
        names, ops, plain = C.named([unreached])
        self.assertTrue({'CPTR_EL2', 'CPACR_EL1', 'ICC_SRE_EL2', 'MAIR_EL2', 'TTBR0_EL2', 'SPSel', 'ICC_PMR_EL1'} <= names and {'IC IALLU', 'DC CVAC'} <= ops and {'SMC', 'WFE'} <= plain)
        allowed = {'SMC': SECONDARY | {'gpu_smc', 'xnuboot_report'}}
        for name in sorted(names | ops | plain):
            self.assertLessEqual(self.writers(name), allowed.get(name, SECONDARY), name)
        for name in ('SCTLR_EL2', 'TCR_EL2', 'TLBI ALLE2'):
            self.assertNotIn(name, unreached, 'these are not unreached: the PAC helpers write them')
        flat = ' '.join(self.groups['monitor G'].split())
        self.assertIn('The secondary entry also writes SCTLR_EL2, TCR_EL2 and TLBI ALLE2, which group F reaches too: they are not claimed unreached', flat)

    def test_the_conditional_group_names_what_the_pac_code_writes(self):
        group = self.groups['monitor F']
        self.assertTrue(re.search(r'CONDITIONAL', self.text))
        for name in (*KEYS, 'APGAKeyLo_EL1', 'APGAKeyHi_EL1', 'TCR_EL2', 'SCTLR_EL2', 'DAIFSet', 'DAIF', 'SCTLR_EL1', 'HCR_EL2'):
            self.assertIn(name, group)
        for name in (*KEYS, 'APGAKeyLo_EL1', 'APGAKeyHi_EL1', 'TCR_EL2', 'SCTLR_EL2', 'TLBI ALLE2'):
            self.assertTrue(self.writers(name) <= PAC_CODE | {'secondary_entry'}, (name, self.writers(name)))
        for name in KEYS:
            self.assertEqual(self.writers(name), {'pauth_native_user', 'pauth_native_sign_user', 'pauth_switch_enter', 'pauth_switch_leave'}, name)
        self.assertEqual(self.writers('APGAKeyLo_EL1'), {'pauth_pacga', 'pauth_switch_enter', 'pauth_switch_leave'})
        flat = ' '.join(group.split())
        for needle in ('INFERRED from the callers', 'UNKNOWN (untried on Oryon)', 'the callers pass the IA and DA keys',
                       'the host\'s are put back before any other monitor code runs: the ten key registers (the eight above and APGAKeyLo_EL1 APGAKeyHi_EL1) with SCTLR_EL1 and HCR_EL2'):
            self.assertIn(needle, flat)
        self.assertIn('The PAC keys are not the report\'s to put back: group F does, and no row shows them', ' '.join(self.groups['monitor E'].split()))
        self.assertNotIn('and the PAC keys of group F', self.text, 'the keys are put back by the bank switch and the helper')
        for name in ('SCTLR_EL1', 'HCR_EL2'):
            self.assertIn('pauth_switch_enter', self.writers(name), 'the key bank switch writes both')

    def test_the_callers_that_make_the_pac_code_reachable_are_the_sptm_endpoints_the_exit_paths_and_the_trap(self):
        c = self.callers
        self.assertEqual(c['pauth_native_user'] - {'pauth_native_user'}, {'pauth_batch_native_sign'})
        self.assertEqual(c['pauth_batch_native_sign'] - {'pauth_batch_native_sign'}, {'pauth_batch_sign'})
        self.assertIn('monitor_call_impl', c['pauth_batch_sign'])
        self.assertEqual(c['pauth_native_sign_user'] - {'pauth_native_sign_user'}, {'pauth_pointer'})
        self.assertIn('monitor_call_impl', c['pauth_pointer'])
        self.assertEqual(c['pauth_switch_enter'], {'xnuboot_pauth_resume'})
        self.assertEqual(c['pauth_switch_leave'], {'async_lower', 'exit', 'sync_lower'})
        self.assertEqual(c['pauth_pacga'] - {'pauth_pacga'}, {'xnuboot_trap'})

    def test_the_secondary_code_is_entered_only_with_host_cpus_and_has_no_direct_caller_outside_itself(self):
        c = self.callers
        self.assertNotIn('secondary_entry', c, 'it is entered by PSCI CPU_ON at an address, never called')
        self.assertEqual(c['secondary_release'] - {'secondary_release'}, {'guest_cpu_release_write'})
        self.assertEqual(c['guest_cpu_release_write'] - {'guest_cpu_release_write'}, {'xnuboot_trap'})
        self.assertEqual(c['xnuboot_secondary_stop'] - {'xnuboot_secondary_stop'}, {'secondary_run'})
        self.assertEqual(c['gpu_smc'], {'adreno_smc'})
        trap = (ROOT / 'probes/xnuboot/trap_dispatch.c').read_text()
        self.assertRegex(trap, r'c->host_cpus && ec==0x24 &&\s+!\(iss&\(1U<<7\)\)\) \{\s+struct dc_access a=dc_decode\(esr\);')
        self.assertIn('if (!c->host_cpus || !c->secondary_base || !slot || slot>=6 ||', (ROOT / 'probes/xnuboot/cpu_boot.c').read_text())

    def test_cntvoff_el2_is_read_and_never_written(self):
        self.assertNotIn('CNTVOFF_EL2', self.cen['msr'])
        self.assertIn('CNTVOFF_EL2', self.cen['mrs'])
        self.assertIn('reads CNTVOFF_EL2 and never writes it', ' '.join(self.text.split()))

    def test_the_early_restores_are_in_the_report_path_and_the_text_says_what_they_write_after_an_early_refusal(self):
        zeros = ('MDCR_EL2', 'VTCR_EL2', 'VTTBR_EL2', 'VMPIDR_EL2', 'MDSCR_EL1', 'MAIR_EL1', 'TTBR0_EL1', 'TTBR1_EL1')
        for name in (*zeros, 'VBAR_EL2', 'HCR_EL2', 'TPIDR_EL2', 'SCTLR_EL1', 'TCR_EL1', 'DAIF'):
            self.assertIn('xnuboot_report', self.writers(name), name)
        flat = ' '.join(self.groups['monitor E'].split())
        self.assertIn('IF PREPARE REFUSED BEFORE IT SAVED THEM', flat)
        self.assertIn('writes ZEROS, not what the registers held, into ' + ', '.join(zeros[:-1]) + ' and ' + zeros[-1], flat)
        self.assertIn('expect CHANGED rows for the first four on such a status (the other four have no row)', flat)
        self.assertIn('harmless to the runner, which is at EL2 with E2H = 0 and has no use for them, is INFERRED', flat)
        self.assertIn('The report also asks PSCI (SMC, AFFINITY_INFO)', flat)


class Sources(unittest.TestCase):
    """The facts of the sources that the note and the record cite, so that a change of the code is a failure here and not a stale sentence."""

    def source(self, rel):
        return (ROOT / rel).read_text()

    def test_the_exit_restores_the_eight_registers_from_saves_that_prepare_makes(self):
        report = self.source('probes/xnuboot/report.c').split('static void xnuboot_restore_host', 1)[1].split('uint64_t xnuboot_report', 1)[0]
        saves = {'mdcr_el2': 'save_mdcr', 'mdscr_el1': 'save_mdscr', 'ttbr0_el1': 'save_ttbr0', 'ttbr1_el1': 'save_ttbr1', 'mair_el1': 'save_mair', 'vttbr_el2': 'save_vttbr',
                 'vtcr_el2': 'save_vtcr', 'vmpidr_el2': 'save_vmpidr'}
        prepare, setup = self.source('probes/xnuboot/prepare.c'), self.source('probes/xnuboot/setup.c')
        for register, field in saves.items():
            self.assertRegex(report, rf'msr {register}, %0.*{field}')
            self.assertRegex(prepare, rf'mrs %0, {register}.*{field}')
            self.assertNotIn(field, setup, f'{field} is saved in prepare, not at entry: a refusal before prepare leaves it zero')
        for field in ('save_vbar', 'save_hcr', 'save_tpidr', 'save_sctlr', 'save_tcr', 'save_daif'):
            self.assertIn(f'vcpu_of(c)->{field} =', setup, 'saved at entry')
        self.assertIn('if (vcpu_of(c)->clock_saved) {', report)
        self.assertIn('static int64_t vcpu_arch_prepare(struct context *c);', prepare)
        self.assertRegex(prepare, r'return vcpu_arch_prepare\(c\);')

    def test_the_native_pac_helper_is_called_without_a_gate_from_the_sptm_endpoints(self):
        pauth = self.source('probes/xnuboot/pauth.c')
        self.assertRegex(pauth, r'pauth_native_user\(&request,&result\)')
        self.assertRegex(pauth, r'pauth_native_sign_user\(&request,&result\)')
        self.assertIn('leave restores before monitor C runs', self.source('compat/pauth-switch.h'), 'the host keys are put back by the bank switch, not by the report')
        native = self.source('compat/pauth-native.S')
        for line in ('msr tcr_el2,x9', 'tlbi alle2', 'msr sctlr_el2,x9', 'msr daifset,#15', 'tbz x22,#0,.Lreturn', 'tbnz x9,#34,.Lreturn'):
            self.assertIn(line, native)

    def test_the_monitor_reads_the_flags_again_while_the_guest_runs(self):
        self.assertIn('c->params->gpu.flags & ADRENO_F_ENABLE', self.source('probes/xnuboot/async.c'))
        self.assertIn('c->params->gpu.flags) gpu_tick(c, elr)', self.source('probes/xnuboot/async.c'))
        self.assertIn('const struct early_stop_params *p = &c->params->early_stop;', self.source('probes/xnuboot/early_stop_thermal.c'))
        self.assertIn("(PHYS_BASE, BOOTSTRAP_PA + BOOTSTRAP_SPAN, 'rw', 'DRAM + reserve')", self.source('probes/xnuboot.py'))

    def test_the_wrapper_reads_two_of_the_three_launch_words_and_not_txm_native(self):
        text = self.source('probes/sp11-stick-monitor.c')
        self.assertIn('SM_PARAMS_EARLY_STOP_FLAGS', text)
        self.assertIn('SM_PARAMS_GPU_FLAGS', text)
        self.assertNotIn('txm_native', text)
        self.assertNotIn('0x23C0', text + self.source('probes/sp11-stick-monitor.h'))


class Note(unittest.TestCase):
    """The corrected statements of the note and the record's section 4 and 1b."""

    flat = ' '.join(NOTE.read_text().split())

    def test_the_false_statements_are_gone(self):
        for gone in ('(off in M1, INFERRED)', 'the measurement below replaces the inference', '(exact to a word; `STACK FULL` if the lowest word changed)',
                     'REPORT.DAT beyond the records is what the runner loaded', 'The monitor puts these back at its exit',
                     'is neither scanned nor zeroed', '(the fix is Phase C)', 'an overflow that skips that word (a large frame) is not seen',
                     'which nothing scans or zeroes'):
            self.assertNotIn(gone, self.flat)

    def test_the_corrections_are_made(self):
        for needle in ('the exit then writes zeros, not what they held, into MDCR_EL2, VTTBR_EL2, VTCR_EL2, VMPIDR_EL2, TTBR0_EL1, TTBR1_EL1, MAIR_EL1 and MDSCR_EL1',
                       'expect CHANGED rows for the first four on such a status and none for the other four',
                       'that this is harmless to the non-VHE EL2 runner is INFERRED',
                       'cannot replace the inference: it is a lower bound', 'a frame that skips the lowest word reaches without changing it',
                       'The filler must be zero before the call (else the check refuses WINDOW_DIRTY, as for a stale window) and is scanned after it: a non-zero word sets '
                       'STACK FULL, and the wrapper zeroes the filler and cleans it',
                       'the monitor wrote the area\'s lowest word or anything below the area',
                       'The wrapper\'s check of these is a snapshot, not a guard', '`txm_native` not at all (the builder checks all three)',
                       'the monitor re-reads `gpu.flags` and `early_stop.flags` live', 'at least 16.5 s, at most 18.8 s, plus the prepare',
                       'The earliest restart datum is about UPTIME 17 s', 'The 8 s is the operator\'s to change',
                       'with the two of the interlock below', '`WRAPPER_CALLS_MONITOR`', '`M1_NO_MONITOR_CALL`', 'it has no override', 'CNTVOFF_EL2 is read'):
            self.assertIn(needle, self.flat)

    def test_the_failure_table_says_what_a_hang_leaves_and_the_setup_return_is_quoted(self):
        for needle in ('A hang or a reset then leaves in DRAM only the head and the CALLING record, both stored and cleaned before the call',
                       '`publish(c->params->mem_low, c->params->mem_high)`, `probes/xnuboot/report.c:244`; `publish` is `probes/xnuboot/mmu_access.c:193`',
                       'All of it only if RAM survives the restart (U4)',
                       '`_start` ignores a refused setup', '`probes/xnuboot.S:97-100` is `bl xnuboot_setup`, `mov x1, sp`, `mrs x9, tpidr_el2`, `str x1, [x9]`',
                       'INFERRED a wild store through that register or a fault in the monitor'):
            self.assertIn(needle, self.flat)

    def test_the_phase_c_statements_are_made(self):
        for needle in ('except for 72 REPORT_UNWRITABLE', 'The 60 s is an operator-safe wait, not a measurement',
                       'refused for the same name in every kind, `--m1-run` included', '`refuse_monitor_reporter`'):
            self.assertIn(needle, self.flat)

    def test_the_stop_figure_of_the_record_says_both_the_plans_and_the_bound(self):
        text = AUTH.STATE['text']
        self.assertRegex(text, r'about 0\.6 s \(the plan\'s estimate, INFERRED, not measured\),\s+at most \d+\.\d s \(INFERRED bound')

    def test_the_record_states_the_limits_of_the_position_proof_and_the_self_attesting_record(self):
        flat = ' '.join(AUTH.STATE['text'].split())
        for needle in ('It cannot show an address dependence that is no relocation, an address built from integers, .init_array or TLS',
                       'the build record above was written by the build tool itself: its digests tie the files together, and nothing authenticates the record'):
            self.assertIn(needle, flat)

    def test_the_record_states_the_stack_filler_and_the_params_snapshot(self):
        flat = ' '.join(AUTH.STATE['text'].split())
        for needle in ('A stack that outgrows it writes below it, into REPORT.DAT 0x13000 to 0x41000: that range must be zero before the call '
                       '(else WINDOW_DIRTY, a refusal) and is scanned after it;',
                       'a word in it is STACK FULL on the page, and the wrapper then zeroes it and cleans it (a store to REPORT.DAT, made only then)',
                       'That the stack fits its area is INFERRED',
                       'That is a snapshot: the monitor reads gpu.flags and early_stop.flags again while the guest runs'):
            self.assertIn(needle, flat)
        self.assertNotIn('which nothing scans or zeroes', flat, 'the filler is scanned and zeroed now: the sentence of Phase B is gone')


if __name__ == '__main__':
    unittest.main()
