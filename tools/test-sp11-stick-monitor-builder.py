#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of what the builder does with --m1-run besides the record text (monitor port work item 4b, Phase D; docs/SP11-STICK-MONITOR.md section 14):
D2, the label marker that binds PIN.DAT to this kind of record (the builder adds it to --m1-run's build_label and to no other kind's, and refuses a facts
label that carries it or does not fit with it) and the two EFI files that section 1b names (--m1-efi); D4, `real --m1-run` refused unless the panel is
withheld and the SPTM HVC call patch is 0 (D9), and one run of `real --m1-run` end to end on the host with the J700 kernelcache and the census check on that
build. The C stage's side of D2 (the wrapper refuses a PIN.DAT without the marker) is tools/test-sp11-stick-monitor.py.

    python3 tools/test-sp11-stick-monitor-builder.py

The fixtures of tools/test-sp11-stick-monitor-auth.py are loaded with a scratch directory of their own (build/sp11-monitor-builder-test-scratch/, deleted at
the end). The refusals and the PIN.DAT label are tested on FABRICATED images and reports; the end to end test needs the J700 26A428 boot set under
downloads/extracted/j700ap/ (git-ignored Apple material, read and never copied; about 30 s) and the toolchain, and SKIPs without them: a skip is never a
pass. No hardware: NOT RUN."""
import importlib.util
import json
import re
import shutil
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))
import sp11_monitor_census as C  # noqa: E402
import sp11_stick_bundle_m1 as m1  # noqa: E402
import sp11_stick_monitor_testlib as M  # noqa: E402

spec = importlib.util.spec_from_file_location('sp11_monitor_auth_tests', TOOLS / 'test-sp11-stick-monitor-auth.py')
AUTH = importlib.util.module_from_spec(spec)
spec.loader.exec_module(AUTH)
SCRATCH = ROOT / 'build' / 'sp11-monitor-builder-test-scratch'
AUTH.SCRATCH = SCRATCH
BT, STATE, sb, sha = AUTH.BT, AUTH.STATE, AUTH.BT.sb, AUTH.sha
EFI = [f'{name}={digest}' for name, digest in AUTH.EFI.items()]
MARKER = b'M1RUN-'
LONG = 'abcdefghijklmnopqr'         # 18 characters: with the marker, 24 of the 23 that a pinned label may have


def setUpModule():
    shutil.rmtree(SCRATCH, ignore_errors=True)
    AUTH.setUpModule()


def tearDownModule():
    AUTH.tearDownModule()
    shutil.rmtree(SCRATCH, ignore_errors=True)


def facts_with(label):
    """The example facts with another build_label, as a file."""
    data = json.loads(Path(BT.FIX['facts']).read_text())
    data['build_label'] = label
    return BT.write(SCRATCH / 'facts' / f'{label}.json', json.dumps(data).encode())


def pin_of(out):
    return (Path(out) / 'bundle' / 'SP11' / 'PIN.DAT').read_bytes()


def label_of(pin):
    return sb.unpack(pin, sb.PIN_LAYOUT)['build_label'].rstrip(b'\0')


def descriptor(name):
    """A boot-ready board that agrees with the example facts (SYNTHETIC: tools/test-board-descriptor.py's), as a file."""
    board = BT.TBD.synthetic()
    board['soc']['midr'] = [{'value': '0x510F0010', 'mask': '0xff0ffff0'}]      # the example facts' MIDRs under this mask
    board['timer']['ppi'] = {'secure_phys': 29, 'phys': 30, 'virt': 27, 'hyp': 26}
    return BT.write(SCRATCH / 'in' / name, json.dumps(board).encode())


def m1_over(name, **over):
    return dict(AUTH.stage(name), stores='ram,el2_sysreg,gic', m1_efi=EFI, **over)


def dry_or_gic(name, gic_stop, facts=None):
    """(code, stdout, stderr, out) of a build of the dry run's kind or the GIC and stop dry run's, with the record built for the same inputs."""
    over = dict(stores='ram,el2_sysreg,gic' if gic_stop else 'ram,el2_sysreg', **({'facts': facts} if facts else {}))
    flag, out = ['--gic-stop'] if gic_stop else [], SCRATCH / 'out' / name
    code, record, err = BT.cli(['authorisation-template', 'synthetic', *BT.opts(**over), *flag])
    if code:
        return code, record, err, out
    argv = ['synthetic', *BT.opts(**over), '--out', out, '--no-manifest', '--authorisation', BT.write(SCRATCH / 'records' / f'{name}.txt', record.encode()), *flag]
    return (*BT.cli(argv), out)


class Label(BT.Case):
    """D2: the marker in PIN.DAT's build_label."""

    def test_the_marker_is_spelled_alike_in_the_header_the_builder_and_the_harness(self):
        header = (ROOT / 'probes/sp11-stick-monitor.h').read_text()
        self.assertEqual(re.search(r'#define SP11_STICK_MONITOR_LABEL_PREFIX "([^"]*)"', header).group(1), m1.LABEL_PREFIX)
        self.assertEqual((m1.LABEL_PREFIX, M.LABEL_PREFIX, MARKER.decode()), ('M1RUN-',) * 3)

    def test_an_m1_build_pins_the_marker_in_front_of_the_facts_label_and_the_record_shows_it(self):
        over = m1_over('label-build')
        code, text, err, out = BT.build_cli('label-build', extra=['--no-manifest'], **over)
        self.assertEqual(code, 0, (text, err))
        pin = pin_of(out)
        self.assertEqual(label_of(pin), MARKER + b'example-synthetic')
        self.assertEqual(sb.check_pin(pin), 0, 'the interface accepts the marked label: 23 bytes of letters, digits and a dash')
        self.assertIn('label M1RUN-example-synthetic)', BT.template(**over)[1].splitlines()[0])

    def test_every_other_kind_keeps_the_facts_label(self):
        for name, gic_stop in (('dry', False), ('gic', True)):
            with self.subTest(name):
                code, text, err, out = dry_or_gic(f'plain-{name}', gic_stop)
                self.assertEqual(code, 0, (text, err))
                self.assertEqual(label_of(pin_of(out)), b'example-synthetic')

    def test_a_label_that_does_not_fit_with_the_marker_is_refused_for_this_kind_only(self):
        for label, fits in (('abcdefghijklmnopq', True), (LONG, False)):
            with self.subTest(label=label):
                code, text, err = AUTH.template(AUTH.stage('long'), facts=facts_with(label))
                self.assertEqual((code, BT.refusal(text)), (0, None) if fits else (1, 'REFUSED=PIN_STRING'), err)
                self.assertEqual(BT.template(stores='ram,el2_sysreg', facts=facts_with(label))[0], 0, 'the dry run takes 18 characters')
        self.assertIn('the facts label holds 17', AUTH.template(AUTH.stage('long'), facts=facts_with(LONG))[2])

    def test_a_facts_label_that_carries_the_marker_is_refused_in_every_kind(self):
        facts = facts_with('M1RUN-x')
        for name, call in (('dry run', lambda: BT.cli(['authorisation-template', 'synthetic', *BT.opts(stores='ram,el2_sysreg', facts=facts)])),
                           ('gic and stop', lambda: BT.cli(['authorisation-template', 'synthetic', *BT.opts(stores='ram,el2_sysreg,gic', facts=facts), '--gic-stop'])),
                           ('m1 kind', lambda: AUTH.template(AUTH.stage('marked'), facts=facts))):
            with self.subTest(name):
                code, text, err = call()
                self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=PIN_STRING'), err)
                self.assertIn('only --m1-run adds', err)


class Efi(BT.Case):
    """D2: the two EFI files that run before the wrapper are named in section 1b by the SHA-256 the builder was given."""

    def refused(self, efi):
        code, text, err = AUTH.template(AUTH.stage('efi'), m1_efi=efi)
        self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=OPTIONS_BAD'), err)

    def test_both_files_are_named_with_their_digests_in_a_fixed_order_whatever_the_order_given(self):
        a = AUTH.template(AUTH.stage('efi-a'))[1]
        self.assertEqual(a, AUTH.template(AUTH.stage('efi-a'), m1_efi=EFI[::-1])[1])
        for name, digest in AUTH.EFI.items():
            self.assertRegex(a, rf'(?m)^ {{16}}{re.escape(name)}\s+sha256 {digest}$')
        self.assertLess(a.index('SLBOUNCE-DEVHE-MARK.EFI '), a.index('Q1N1-PAYLOAD.EFI '))
        self.assertIn('No check at boot compares them with this record', ' '.join(a.split()))

    def test_both_names_are_required_once_each_with_a_digest_in_lower_case_hex(self):
        for efi in ([], EFI[:1], EFI[1:], [EFI[0], EFI[0]], [*EFI, EFI[0]], [EFI[0], 'OTHER.EFI=' + '1' * 64], [EFI[0], 'Q1N1-PAYLOAD.EFI=' + '8' * 63],
                    [EFI[0], 'Q1N1-PAYLOAD.EFI=' + 'A' * 64], [EFI[0], 'Q1N1-PAYLOAD.EFI'], [EFI[0], 'Q1N1-PAYLOAD.EFI=' + 'g' * 64]):
            with self.subTest(efi=efi):
                self.refused(efi)

    def test_another_kind_refuses_the_option(self):
        for stores, flag in (('ram,el2_sysreg', []), ('ram,el2_sysreg,gic', ['--gic-stop'])):
            code, text, err = BT.cli(['authorisation-template', 'synthetic', *BT.opts('synthetic', stores=stores, m1_efi=EFI), *flag])
            self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=OPTIONS_BAD'), err)
            self.assertIn('--m1-efi belongs to the --m1-run record', err)

    def test_a_stage_tree_file_with_another_digest_than_the_record_names_is_refused(self):
        if not STATE.get('tree'):
            self.skipTest('no q1n1 checkout (Q1N1_SP11_TREE): the build asks for the manifest tool first')
        over = m1_over('efi-file')
        for name, digest in (('SLBOUNCE-DEVHE-MARK.EFI', 'f' * 64), ('Q1N1-PAYLOAD.EFI', '0' * 64)):
            with self.subTest(name):
                code, text, err = BT.cli(['synthetic', *BT.opts(**over), '--out', SCRATCH / 'out' / 'efi-file', '--file', name, str(SCRATCH / 'x.efi'), digest])
                self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=OPTIONS_BAD'), err)
                self.assertIn(f'--file {name} has another SHA-256 than --m1-efi names', err)
        self.assertFalse((SCRATCH / 'out' / 'efi-file').exists())


class D9(BT.Case):
    """D4: `real --m1-run` is refused unless the guest is given no panel and a stock kernel, which is what the record says."""

    def real(self, name='d9', m1_run=True, **over):
        base = dict(AUTH.stage(name), kernelcache=SCRATCH / 'in' / 'absent.kc', panel='withheld', sptm_hvc_calls=0, descriptor=descriptor('board.json'), guest_mib=384,
                    stores='ram,el2_sysreg,gic', m1_efi=EFI)
        if not m1_run:
            base = {**base, 'stores': 'ram,el2_sysreg', 'monitor': BT.FIX['monitor'], 'reporter': BT.FIX['reporter'], 'm1_run': None, 'm1_efi': None}
        return BT.template('real', **{**base, **over})

    def test_a_handed_panel_or_the_hvc_patch_is_refused_naming_d9(self):
        for over in (dict(panel='handed'), dict(sptm_hvc_calls=1), dict(panel='handed', sptm_hvc_calls=1)):
            with self.subTest(over):
                code, text, err = self.real(**over)
                self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=OPTIONS_BAD'), err)
                self.assertIn('D9 is open', err)
                self.assertIn('--panel withheld and --sptm-hvc-calls 0 only', err)

    def test_the_control_passes_the_options_and_stops_at_the_kernelcache(self):
        code, text, err = self.real()
        self.assertEqual(code, 1)
        self.assertIn(BT.refusal(text), ('REFUSED=KERNELCACHE_MISSING', 'REFUSED=ENV_SNAP_SET'), (text, err))

    def test_a_record_of_another_kind_takes_any_choice_of_d9(self):
        code, text, err = self.real(m1_run=False, panel='handed', sptm_hvc_calls=1)
        self.assertNotIn('D9 is open', err, 'the refusal belongs to --m1-run: the other kinds were never asked for it')
        self.assertIn(BT.refusal(text), ('REFUSED=KERNELCACHE_MISSING', 'REFUSED=ENV_SNAP_SET'), (text, err))

    def test_the_synthetic_kind_has_no_panel_to_refuse(self):
        self.assertEqual(AUTH.template(AUTH.stage('synthetic-d9'))[0], 0)


def boot_set_present():
    try:
        return not BT.bb.xnuboot().check_profile()
    except Exception:       # noqa: BLE001  a broken checkout must skip the test, not break the file
        return False


class EndToEnd(AUTH.Need):
    """D4: `real --m1-run` run once on the host, with the kernelcache, the M1 pair of the LOCAL TEST RECORD and the census check on that build."""

    @unittest.skipUnless(boot_set_present(), 'no J700 26A428 boot set under downloads/extracted/j700ap (git-ignored Apple material): `real --m1-run` NOT RUN')
    def test_real_m1_run_builds_and_the_census_checks_the_build(self):
        over = dict(STATE['real'], kernelcache=BT.bb.xnuboot().DEFAULT_KERNELCACHE, panel='withheld', sptm_hvc_calls=0, stores='ram,el2_sysreg,gic', m1_efi=EFI,
                    descriptor=descriptor('board-e2e.json'), guest_mib=384)
        started = time.monotonic()
        code, record, err = BT.template('real', **over)
        self.assertEqual(code, 0, err)
        out = SCRATCH / 'out' / 'real-m1'
        argv = ['real', *BT.opts('real', **over), '--out', out, '--authorisation', BT.write(SCRATCH / 'records' / 'real-m1.txt', record.encode()),
                *(() if STATE.get('tree') else ('--no-manifest',))]
        code, text, err = BT.cli(argv)
        self.assertEqual(code, 0, (text, err))
        seconds = time.monotonic() - started
        pin, guest = pin_of(out), (out / 'bundle' / 'SP11' / 'GUEST.DAT').read_bytes()
        fields = sb.unpack(pin, sb.PIN_LAYOUT)
        self.assertEqual((sb.check_pin(pin), sb.check_guest(guest, pin), fields['authorised_stores'], label_of(pin)), (0, 0, 7, MARKER + b'example-synthetic'))
        self.assertEqual(bytes(fields['authorisation_sha256']).hex(), sha(record.encode()))
        self.assertEqual((out / 'bundle' / 'SP11' / 'SNAPMON.BIN').read_bytes(), Path(STATE['real']['monitor']).read_bytes(), 'payload 0 is SNAPMON-M1.BIN')
        flat = ' '.join(record.split())
        for needle in ('D9 as built panel withheld: the guest is given no framebuffer', 'SPTM HVC call patch 0: a stock kernel', 'LOCAL TEST RECORD',
                       'SLBOUNCE-DEVHE-MARK.EFI sha256 ' + AUTH.EFI['SLBOUNCE-DEVHE-MARK.EFI']):
            self.assertIn(needle, flat)
        monitor_elf, wrapper_elf = STATE['profile'] / 'xnuboot.elf', SCRATCH / 'm1' / 'snapmon-m1.elf'
        problems = C.check(record, C.census(monitor_elf), C.census(wrapper_elf), sha(monitor_elf.read_bytes()), C.flat_bytes(monitor_elf), C.flat_bytes(wrapper_elf))
        self.assertEqual(problems, [], 'the census check on the record of this build')
        print(f'\nreal --m1-run end to end: {seconds:.1f} s, GUEST.DAT {len(guest)} bytes, record {len(record.encode())} bytes, PIN label {label_of(pin).decode()}')


if __name__ == '__main__':
    unittest.main()
