#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests for the kernelcache path of tools/sp11_stick_bundle_build.py (`real`; docs/SP11-STICK-REAL.md).

    python3 tools/test-sp11-stick-bundle-real.py
    Q1N1_SP11_TREE=/nonexistent python3 tools/test-sp11-stick-bundle-real.py        (the stage-tree test SKIPs)

Two kinds of test. `Pipeline`, `Refusals`, `Document` and `Hygiene` need no Apple input: they run the real pipeline (descriptor gate, environment,
record, classification, PIN.DAT, files, report) around a stand-in for the loader's build(). `Kernelcache` needs the J700 26A428 boot set under
downloads/extracted/j700ap/ (git-ignored Apple material, read and never copied) and SKIPs without it: a skip is never a pass.

The fixtures are those of tools/test-sp11-stick-bundle-build.py, loaded with a scratch directory of its own (build/sp11-bundle-real-scratch/,
deleted at the end). Nothing derived from the kernelcache is written outside that directory. No hardware: NOT RUN."""
import argparse
import contextlib
import importlib
import importlib.util
import io
import json
import os
import re
import struct
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOLS, PROBES, THIS = ROOT / 'tools', ROOT / 'probes', Path(__file__).resolve()
DOC = ROOT / 'docs' / 'SP11-STICK-REAL.md'
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location('sp11_stick_bundle_build_tests', TOOLS / 'test-sp11-stick-bundle-build.py')
BT = importlib.util.module_from_spec(spec)
spec.loader.exec_module(BT)
bb, ch, sb, TBD = BT.bb, BT.ch, BT.sb, BT.TBD
MIB, BANK, sha = BT.MIB, BT.BANK, BT.sha
SCRATCH = ROOT / 'build' / 'sp11-bundle-real-scratch'
LOADER = bb.xnuboot                 # the loader module factory, kept before a test replaces it
SNAP_KEYS = ('SNAP_NATIVE_CALLS', 'SNAP_SPTM_HVC_CALLS', 'SNAP_SEED')
STAND_IN_IMAGE = b'a stand-in for the kernelcache image'


def setUpModule():
    BT.SCRATCH = SCRATCH
    BT.setUpModule()
    BT.write(SCRATCH / 'in/stand-in.kc', b'not a kernelcache: the tests that need no Apple input only name this file\n')
    BT.write(SCRATCH / 'in/garbage.kc', bytes(range(256)) * 64)


def tearDownModule():
    BT.tearDownModule()


# ---- Fixtures -------------------------------------------------------------------------------------------------------------------
def descriptor(name='board.json', edit=None):
    """A boot-ready board that agrees with the example facts (SYNTHETIC: tools/test-board-descriptor.py's), then `edit`."""
    board = TBD.synthetic()
    board['soc']['midr'] = [{'value': '0x510F0010', 'mask': '0xff0ffff0'}]      # the example's 0x511F0011 and 0x512F0011 under this mask
    board['timer']['ppi'] = {'secure_phys': 29, 'phys': 30, 'virt': 27, 'hyp': 26}
    if edit:
        edit(board)
    return BT.write(SCRATCH / 'in' / name, json.dumps(board).encode())


def real(**over):
    return {**dict(kernelcache=SCRATCH / 'in/stand-in.kc', panel='withheld', sptm_hvc_calls=0, descriptor=descriptor()), **over}


def template(**over):
    return BT.template('real', **real(**over))


def build(name, extra=('--no-manifest',), approve=True, **over):
    out, args = SCRATCH / 'out' / name, real(**over)
    argv = ['real', *BT.opts('real', **args), '--out', out, *extra]
    if approve:
        argv += ['--authorisation', BT.write(SCRATCH / f'records/{name}.txt', BT.template('real', **args)[1].encode())]
    return (*BT.cli(argv), out)


def stand_in_loader(calls, noise='LOADER NARRATION', error=None, slices=()):
    """Replaces bb.xnuboot by the real loader module (it loads without Apple input) whose build() and params_blob() are stand-ins. build() records what the
    builder handed it and returns a dict of build()'s shape from the synthetic filler, with a time box of 99 that only the builder's second serialisation
    of the parameters corrects."""
    def logged(name, fn):
        return lambda *args, **kw: (calls.append((name,)), fn(*args, **kw))[1]

    def factory():
        xb = LOADER()
        for name in ('configure_guest', 'configure_txm_arena', 'configure_adreno_va_wide', 'place'):
            setattr(xb, name, logged(name, getattr(xb, name)))

        def build_(path, **options):
            print(noise)
            calls.append(('build', {key: os.environ.get(key) for key in SNAP_KEYS}, xb.HOST_TIMEBASE_HZ, xb.FRAMEBUFFER, options))
            if error:
                raise error
            built = ch.synthetic_built(xb, 2 * MIB, MIB, 4 * MIB, os.environ['SNAP_SEED'], 99)
            return {**built, 'image': types.SimpleNamespace(data=STAND_IN_IMAGE), 'guest_profile': 'stand-in', 'dart_apf_slices': list(slices)}

        def params_blob(built):
            calls.append(('params_blob', built['watchdog']))
            words = list(struct.unpack(f'<{ch.PARAMS_WORDS}Q', built['chunks'][xb.PARAMS_PA]))
            words[ch.PARAMS_WATCHDOG] = built['watchdog']
            return struct.pack(f'<{ch.PARAMS_WORDS}Q', *words)
        xb.build, xb.params_blob = build_, params_blob
        return xb
    return mock.patch.object(bb, 'xnuboot', factory)


def snap_env():
    return [k for k in os.environ if k.startswith('SNAP_')]


def section_1a(text):
    """The lines of section 1a of a record, its heading first: what a real build says it is."""
    lines = text.split('\n')
    start = next(i for i, line in enumerate(lines) if line.startswith('1a. '))
    return lines[start:lines.index('', start)]


# ---- The real pipeline around a stand-in loader ----------------------------------------------------------------------------------
class Pipeline(BT.Case):
    def test_the_loader_is_driven_as_probes_run_py_drives_it_for_each_d9_choice(self):
        facts = bb.load_facts(BT.FIX['facts'])
        for panel in ('withheld', 'handed'):
            for hvc in (0, 1):
                calls = []
                with stand_in_loader(calls):
                    code, _, err = template(panel=panel, sptm_hvc_calls=hvc)
                self.assertEqual(code, 0, (panel, hvc, err))
                self.assertEqual([c[0] for c in calls], ['configure_guest', 'configure_txm_arena', 'configure_adreno_va_wide', 'place', 'build', 'params_blob'])
                _, env, hz, framebuffer, options = calls[4]
                self.assertEqual(env, {'SNAP_NATIVE_CALLS': '1', 'SNAP_SPTM_HVC_CALLS': str(hvc), 'SNAP_SEED': 'sp11-stick-bundle'})
                self.assertEqual((hz, options), (facts['cntfrq_hz'], {}))
                self.assertEqual(framebuffer, None if panel == 'withheld' else (facts['fb_base'], facts['fb_width'], facts['fb_height'], facts['fb_stride'] * 4, 32))
                self.assertEqual(calls[5], ('params_blob', 5))
        self.assertEqual(snap_env(), [])

    def test_the_record_is_the_record_alone_and_a_rebuild_is_byte_identical(self):
        calls, trees = [], []
        with stand_in_loader(calls):
            code, text, err = template(panel='handed', sptm_hvc_calls=1)
            self.assertEqual(code, 0, err)
            self.assertTrue(text.startswith('SNAPINTOSH SP11 STICK BUNDLE - AUTHORISATION RECORD') and text.endswith('\n'))
            self.assertNotIn('LOADER NARRATION', text + err)
            for i in (1, 2):
                code, out_text, err, out = build(f'twice{i}', panel='handed', sptm_hvc_calls=1)
                self.assertEqual(code, 0, (out_text, err))
                self.assertNotIn('LOADER NARRATION', out_text + err)
                self.assertEqual(out_text.splitlines()[0], f'BUILT kind=real out={out.relative_to(ROOT)}')
                self.assertEqual(out_text.splitlines()[-1], 'HARDWARE=NOT RUN')
                trees.append(BT.tree_digests(out))
        self.assertEqual(trees[0], trees[1])
        self.assertIn('bundle/SP11/GUEST.DAT', trees[0])
        report = json.loads((out / bb.REPORT_NAME).read_text())
        self.assertEqual(report['real'], dict(kernelcache_sha256=BT.sha(STAND_IN_IMAGE), guest_profile='stand-in', panel='handed', sptm_hvc_calls=1, seed='sp11-stick-bundle'))
        self.assertEqual((report['kind'], report['hardware'], report['descriptor'], report['time_box_s']), ('real', 'NOT RUN', 'boot-ready', 5))
        self.assertNotIn('the kernelcache path', report['not_run'])
        self.assertEqual(snap_env(), [])

    def test_a_synthetic_report_has_no_real_section(self):
        code, _, err, out = BT.build_cli('no-real-section', extra=['--no-manifest'])
        self.assertEqual(code, 0, err)
        self.assertNotIn('real', json.loads((out / bb.REPORT_NAME).read_text()))

    def test_a_synthetic_record_has_no_line_of_a_real_build(self):
        for stores in ('ram,el2_sysreg', 'ram,el2_sysreg,gic'):
            self.assertNotIn('1a. ', BT.template(stores=stores)[1])

    def test_a_real_record_names_the_kernelcache_and_the_d9_choices_the_build_used(self):
        words = {'withheld': 'the guest is given no framebuffer', 'handed': 'the guest is given the unit\'s framebuffer',
                 0: 'a stock kernel', 1: 'the loader replaces the kernel\'s SPTM call words'}
        records = []
        for panel in ('withheld', 'handed'):
            for hvc in (0, 1):
                with self.subTest(panel=panel, hvc=hvc), stand_in_loader([]):
                    code, text, err = template(panel=panel, sptm_hvc_calls=hvc)
                    self.assertEqual(code, 0, err)
                    records.append(text)
                    self.assertEqual(section_1a(text)[:4], [
                        '1a. What this build is (the host-side real path of docs/SP11-STICK-REAL.md: GUEST.DAT is made from a kernelcache)',
                        f'   kernelcache  guest profile stand-in  sha256 {sha(STAND_IN_IMAGE)}',
                        f'   D9 as built  panel {panel}: {words[panel]}', f'                SPTM HVC call patch {hvc}: {words[hvc]}'])
        self.assertEqual(len(set(records)), 4)

    def test_the_seed_line_calls_only_the_default_label_a_default(self):
        default = '   boot seed    label sp11-stick-bundle (a default, not an operator decision; boot entropy UNIMPLEMENTED)'
        with stand_in_loader([]):
            self.assertEqual(section_1a(template()[1])[4], default)
            self.assertEqual(section_1a(template(seed='sp11-stick-bundle')[1])[4], default)
            self.assertEqual(section_1a(template(seed='another')[1])[4], '   boot seed    label another (given with --seed)')

    def test_a_real_record_names_the_descriptor_and_calls_a_view_without_config_windows_a_guest_view(self):
        def no_windows(board):
            board['pcie']['ecam'] = []

        def observed(board):
            no_windows(board)
            board['board']['status'] = 'OBSERVED'
        view = ['                guest view: pcie.ecam empty; D11 proper open']
        for name, edit, status, tail in (('one window', None, 'EXPERIMENTAL', []), ('no window', no_windows, 'EXPERIMENTAL', view), ('no window, OBSERVED', observed, 'OBSERVED', [])):
            path = descriptor(re.sub(r'\W+', '-', name) + '.json', edit)
            with self.subTest(name), stand_in_loader([]):
                code, text, err = template(descriptor=path)
                self.assertEqual(code, 0, err)
                self.assertEqual(section_1a(text)[5:], [f'   descriptor   sha256 {sha(Path(path).read_bytes())} (PIN.DAT descriptor_sha256)',
                                                        f'                board synthetic  status {status}', *tail])

    def test_the_approved_record_covers_the_descriptor_so_another_descriptor_is_another_record(self):
        other = descriptor('another.json', lambda board: board['board'].update(name='The same board under another name'))
        self.assertNotEqual(Path(other).read_bytes(), Path(real()['descriptor']).read_bytes())
        with stand_in_loader([]):
            approved = BT.write(SCRATCH / 'records/approved.txt', template()[1].encode())
            argv = ['real', *BT.opts('real', **real(descriptor=other)), '--out', SCRATCH / 'out/another-descriptor', '--no-manifest', '--authorisation', approved]
            self.refused('AUTH_RECORD_MISMATCH', BT.cli(argv))
        self.assertFalse((SCRATCH / 'out/another-descriptor').exists())

    def test_the_report_names_the_descriptor_that_pin_dat_pins_whether_or_not_one_was_given(self):
        with stand_in_loader([]):
            code, text, err, out = build('names-descriptor')
        self.assertEqual(code, 0, (text, err))
        report, digest = json.loads((out / bb.REPORT_NAME).read_text()), sha(Path(real()['descriptor']).read_bytes())
        self.assertEqual((report['descriptor'], report['descriptor_sha256'], report['board_id'], report['board_status']), ('boot-ready', digest, 'synthetic', 'EXPERIMENTAL'))
        self.assertEqual((out / 'bundle/SP11/PIN.DAT').read_bytes()[0x40:0x60].hex(), digest)
        code, _, err, out = BT.build_cli('names-no-descriptor', extra=['--no-manifest'])
        self.assertEqual(code, 0, err)
        report = json.loads((out / bb.REPORT_NAME).read_text())
        self.assertEqual((report['descriptor_sha256'], report['board_id'], report['board_status']), (sha(b'synthetic bundle: no board descriptor given'), None, None))
        self.assertEqual((out / 'bundle/SP11/PIN.DAT').read_bytes()[0x40:0x60].hex(), report['descriptor_sha256'])

    def test_the_boot_seed_is_a_label_the_build_is_a_function_of(self):
        records, seen = {}, []
        for seed in (None, 'sp11-stick-bundle', 'another'):
            with stand_in_loader(seen):
                records[seed] = template(**({} if seed is None else dict(seed=seed)))[1]
        self.assertEqual(records[None], records['sp11-stick-bundle'])
        self.assertNotEqual(records[None], records['another'])
        self.assertEqual([c[1]['SNAP_SEED'] for c in seen if c[0] == 'build'], ['sp11-stick-bundle', 'sp11-stick-bundle', 'another'])
        actions = {a.dest: a for a in bb.build_parser()._subparsers._group_actions[0].choices['real']._actions}
        self.assertEqual((actions['seed'].default, actions['seed'].required), ('sp11-stick-bundle', False))

    def test_the_loader_draws_a_fresh_boot_seed_unless_told_a_label_which_is_why_the_builder_always_tells_it_one(self):
        xb = LOADER()
        self.assertNotEqual(xb.random_seed(), xb.random_seed())
        with mock.patch.dict(os.environ, {'SNAP_SEED': 'a label'}):
            self.assertEqual(xb.random_seed(), xb.random_seed())

    def test_a_refusal_of_the_loader_is_named_leaves_no_variable_and_prints_nothing_of_its_own(self):
        for error in (ValueError('firmware does not match: a stand-in message'), OSError('a stand-in OS error'), SystemExit('layout: a stand-in exit')):
            calls = []
            with stand_in_loader(calls, error=error):
                code, text, err = template()
            self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=BUILT_SHAPE'), err)
            self.assertIn('build() refused', err)
            self.assertNotIn('LOADER NARRATION', text + err)
            self.assertEqual((snap_env(), [c[0] for c in calls].count('params_blob')), ([], 0))

    def test_an_apf_slice_over_owned_memory_is_refused_by_the_loaders_own_rule_and_its_node_is_not_quoted(self):
        place = sb.placement(BANK, 256 * MIB, bb.xnuboot().bootstrap_reserve(256 * MIB))
        slice_at = lambda base: [types.SimpleNamespace(first=base, last=base + 0xFFF, path='/stand-in/node')]       # noqa: E731
        for name, base, panel, ok in (('the work area', place['work_base'] + 0x1000, 'withheld', False), ('the guest', BANK + 0x4000, 'withheld', False),
                                      ('the panel', 0xE0000000 + 0x4000, 'handed', False), ('the panel, withheld', 0xE0000000 + 0x4000, 'withheld', True),
                                      ('free memory', 0x2000000000, 'handed', True)):
            with self.subTest(name), stand_in_loader([], slices=slice_at(base)):
                code, text, err = template(panel=panel)
                self.assertEqual((code, BT.refusal(text)), (0, None) if ok else (1, 'REFUSED=BUILT_SHAPE'), err)
                self.assertNotIn('stand-in/node', err)
                self.assertTrue(ok or 'APF' in err)

    def test_every_cheap_refusal_comes_before_the_loader_is_asked_to_build(self):
        gone = SCRATCH / 'in/not-there.kc'
        cases = (('DESCRIPTOR_NOT_READY', dict(descriptor=None)), ('DESCRIPTOR_NOT_READY', dict(descriptor=ROOT / 'config/boards/sp11.json')),
                 ('FACTS_DISAGREE', dict(facts=BT.facts_file('wrong.json', gicd_base='0x17010000'))), ('OPTIONS_BAD', dict(time_box_s=0)),
                 ('PLACE_DECLARED', dict(kernelcache=gone, declared=f'{BANK + 0x100000:#x}:0x1000')), ('KERNELCACHE_MISSING', dict(kernelcache=gone)),
                 ('KERNELCACHE_MISSING', dict(kernelcache=SCRATCH / 'in')))
        for expected, over in cases:
            calls = []
            with self.subTest(expected, over=list(over)), stand_in_loader(calls):
                self.refused(expected, template(**over))
                self.assertNotIn('build', [c[0] for c in calls])
        calls = []
        for name in ('SNAP_SEED', 'SNAP_NO_FB', 'SNAP_NATIVE_CALLS', 'SNAP_SPTM_HVC_CALLS', 'SNAP_KERNEL_SYSREG_ZERO', 'SNAP_KERNEL_REFMOD_SKIP', 'SNAP_KERNEL_USER_4K',
                     'SNAP_HIDE_AMX', 'SNAP_GUEST_MIB', 'SNAP_WATCHDOG', 'SNAP_PHYS_BASE'):
            with self.subTest(name), stand_in_loader(calls), mock.patch.dict(os.environ, {name: '1'}):
                self.refused('ENV_SNAP_SET', template())
        self.assertNotIn('build', [c[0] for c in calls])

    @BT.needs_q1n1
    def test_a_real_build_through_the_stand_in_makes_the_stage_tree_that_the_stager_verifies(self):
        with stand_in_loader([]):
            code, text, err, out = build('staged', extra=BT.stage_args(), panel='withheld', sptm_hvc_calls=1)
        self.assertEqual(code, 0, (text, err))
        found = bb.stager().cmd_verify(argparse.Namespace(dest=out / 'stage', pins=None, no_pins=True))
        self.assertEqual((found['status'], found['autorun']['problems']), ('COMPLETE', []))
        self.assertEqual(json.loads((out / bb.REPORT_NAME).read_text())['stage']['status'], 'COMPLETE')


# ---- Refusals that need no loader ------------------------------------------------------------------------------------------------
class Refusals(BT.Case):
    FIELDS = {
        'board.status': TBD.S('board.status', 'UNIMPLEMENTED'),
        'soc.midr': TBD.chain(TBD.S('soc.midr', None), TBD.unknowns('soc.midr')),
        'clusters.count': TBD.chain(TBD.S('clusters.count', None), TBD.unknowns('clusters.count')),
        'clusters.cores_per_cluster': TBD.chain(TBD.S('clusters.cores_per_cluster', None), TBD.unknowns('clusters.cores_per_cluster')),
        'clusters.mpidr': TBD.chain(TBD.S('clusters.mpidr', None), TBD.S('clusters.mpidrs', None), TBD.unknowns('clusters.mpidr', 'clusters.mpidrs')),
        'clusters.mpidrs': TBD.chain(TBD.S('clusters.mpidrs', None), TBD.unknowns('clusters.mpidrs')),
        'gic.version': TBD.chain(TBD.S('gic.version', None), TBD.unknowns('gic.version')),
        'gic.gicd': TBD.chain(TBD.S('gic.gicd', None), TBD.unknowns('gic.gicd')),
        'gic.gicr': TBD.chain(TBD.S('gic.gicr', None), TBD.unknowns('gic.gicr')),
        'gic.its': TBD.chain(TBD.S('gic.its', None), TBD.unknowns('gic.its')),
        'timer.frequency_hz': TBD.chain(TBD.S('timer.frequency_hz', None), TBD.unknowns('timer.frequency_hz')),
        'timer.ppi': TBD.chain(TBD.S('timer.ppi', None), TBD.unknowns('timer.ppi')),
        'timer.ppi has an unrecorded': TBD.S('timer.ppi.hyp', None),
        'smmu': TBD.chain(TBD.S('smmu', None), TBD.unknowns('smmu', drop=['smmu.apps-smmu', 'smmu.pcie-smmu', 'smmu.adreno-smmu'])),
        'pcie.ecam': TBD.chain(TBD.S('pcie.ecam', None), TBD.unknowns('pcie.ecam')),
        'limits.ipa_bits': TBD.chain(TBD.S('limits.ipa_bits', None), TBD.unknowns('limits.ipa_bits')),
        'no DRAM window': TBD.chain(lambda d: d['ranges'].pop(0), TBD.dropprov('ranges.dram0')),
        'ranges.dram0 geometry': TBD.chain(TBD.S('ranges.0.base', None), TBD.S('ranges.0.size', None), TBD.unknowns('ranges.dram0')),
    }

    def not_ready(self, name, edit):
        with self.assertRaises(ch.Refused) as caught:
            bb.descriptor_digest(types.SimpleNamespace(descriptor=descriptor(re.sub(r'\W+', '-', name) + '.json', edit)), bb.load_facts(BT.FIX['facts']), 'real')
        self.assertEqual(caught.exception.name, 'DESCRIPTOR_NOT_READY')
        self.assertIn('not boot-ready', caught.exception.detail)
        self.assertIn(name, caught.exception.detail)

    def test_a_descriptor_is_refused_for_each_field_that_require_boot_ready_names(self):
        for name, edit in self.FIELDS.items():
            with self.subTest(name):
                self.not_ready(name, edit)

    def test_a_device_whose_state_is_unknown_is_refused_and_named(self):
        for device in ('uart-debug', 'keyboard', 'touchpad', 'touchscreen', 'nvme'):
            with self.subTest(device):
                self.not_ready(f"['{device}']", TBD.chain(TBD.S(f'devices.{device}', {'state': 'unknown', 'mmio': None, 'ranges': []}), TBD.unknowns(f'devices.{device}')))

    def test_what_is_not_a_descriptor_is_not_ready_either(self):
        ns = lambda path: types.SimpleNamespace(descriptor=path)       # noqa: E731
        facts = bb.load_facts(BT.FIX['facts'])
        good = json.loads(Path(descriptor('good.json')).read_text())
        bad = {'not json': b'{', 'not utf-8': b'\xff\xfe\x00', 'wrong schema version': json.dumps({**good, 'schema_version': 2}).encode(),
               'an unknown key': json.dumps({**good, 'extra': 1}).encode(), 'a duplicate key': Path(descriptor('good.json')).read_bytes().replace(b'{', b'{"schema_version": 1, ', 1),
               'a template': (ROOT / 'config/boards/sp11.json').read_bytes()}
        for name, data in bad.items():
            with self.subTest(name), self.assertRaises(ch.Refused) as caught:
                bb.descriptor_digest(ns(BT.write(SCRATCH / 'in/bad-descriptor.json', data)), facts, 'real')
            self.assertEqual(caught.exception.name, 'DESCRIPTOR_NOT_READY')

    def test_the_facts_must_agree_with_the_descriptor_on_every_value_it_pins(self):
        ns, facts = types.SimpleNamespace(descriptor=descriptor()), bb.load_facts(BT.FIX['facts'])
        self.assertEqual(bb.descriptor_digest(ns, facts, 'real')[1], 'boot-ready')
        for key, value in (('gicd_base', facts['gicd_base'] + 0x10000), ('gicr_first', facts['gicr_first'] + 0x40000), ('gicr_stride', 0x20000),
                           ('gicr_limit', facts['gicr_limit'] + 0x40000), ('its_base', 0), ('its_base', facts['its_base'] + 0x10000), ('cntfrq_hz', 24000000),
                           ('timer_intid', 27), ('midr', [0x511F0011, 0x511F0021]), ('boot_mpidr', 0x80000123)):
            with self.subTest(key, value=value), self.assertRaises(ch.Refused) as caught:
                bb.descriptor_digest(ns, {**facts, key: value}, 'real')
            self.assertEqual(caught.exception.name, 'FACTS_DISAGREE')
            self.assertIn(key, caught.exception.detail)

    def test_the_builder_never_applies_the_adapters_ecam_rule_so_d11_blocks_the_descriptor_and_not_the_builder(self):
        for path in BT.MODULES:
            for call in ('check_hamoa_platform', 'from_hamoa', 'parse_hamoa_tables'):
                self.assertNotIn(call, path.read_text(), path.name)

        def four_windows_no_mcfg_lists(d):      # a descriptor holds at most four ECAM windows; these four match nothing the dumps' MCFG says
            d['pcie']['ecam'] = [{'segment': s, 'base': f'{0x30000000 + s * 0x10000000:#x}', 'start_bus': 0, 'end_bus': 1} for s in range(4)]
        ns, facts = types.SimpleNamespace(descriptor=descriptor('ecam.json', four_windows_no_mcfg_lists)), bb.load_facts(BT.FIX['facts'])
        self.assertEqual(bb.descriptor_digest(ns, facts, 'real')[1], 'boot-ready')
        self.assertEqual(bb.load_facts(BT.FIX['facts'])['ecam'], None)      # nothing compares the descriptor's ECAM with an MCFG unless the facts state one

    def test_a_kernelcache_that_is_not_the_pinned_one_is_refused_by_the_loader_by_name(self):
        code, text, err = template(kernelcache=SCRATCH / 'in/garbage.kc')
        self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=BUILT_SHAPE'), err)
        self.assertIn('does not match the selected guest profile', err)
        self.assertIn('kernelcache', err)
        self.assertEqual(snap_env(), [])

    def test_the_d9_choices_have_no_default_and_each_value_parses(self):
        sub = bb.build_parser()._subparsers._group_actions[0].choices['real']
        actions = {a.dest: a for a in sub._actions}
        self.assertEqual([(actions[d].required, actions[d].default) for d in ('panel', 'sptm_hvc_calls')], [(True, None)] * 2)
        base = [x for x in BT.opts('real', **real()) if x not in ('--panel', 'withheld', '--sptm-hvc-calls', '0')]
        for panel in ('handed', 'withheld'):
            for hvc in ('0', '1'):
                parsed = sub.parse_args([*base, '--panel', panel, '--sptm-hvc-calls', hvc, '--out', 'x'])
                self.assertEqual((parsed.panel, parsed.sptm_hvc_calls), (panel, int(hvc)))
        for argv in (['--panel', 'both'], ['--sptm-hvc-calls', '2']):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                sub.parse_args([*base, *argv, '--out', 'x'])


# ---- The J700 kernelcache ---------------------------------------------------------------------------------------------------------
def boot_set_present():
    try:
        return not LOADER().check_profile()
    except Exception:       # noqa: BLE001  a broken checkout must skip these tests, not break the file
        return False


needs_boot_set = unittest.skipUnless(boot_set_present(), 'no J700 26A428 boot set under downloads/extracted/j700ap (git-ignored Apple material)')


@needs_boot_set
class Kernelcache(BT.Case):
    cache = {}

    @classmethod
    def setUpClass(cls):
        cls.facts, cls.kernelcache = bb.load_facts(BT.FIX['facts']), LOADER().DEFAULT_KERNELCACHE
        cls.pinned = LOADER().PROFILE_FIRMWARE[cls.kernelcache.name]

    def make(self, panel='withheld', hvc=0, seed='sp11-stick-bundle', keep=False):
        """What the CLI does up to GUEST.DAT, as a small record (the 119 MB guest, its layout and its calls); only withheld guests are kept."""
        key = (panel, hvc, seed)
        if key in self.cache:
            return self.cache[key]
        a = argparse.Namespace(base=hex(BANK), guest_mib=384, reserve_mib=None, kernelcache=self.kernelcache, panel=panel, sptm_hvc_calls=hvc, time_box_s=5, seed=seed)
        place, xb = bb.placement_of(a), LOADER()
        built = bb.real_built(a, xb, place, self.facts)
        lay = ch.layout_of(xb)
        chunks = ch.chunks_from_built(built, lay)
        ch.check_params(chunks, place, 5)
        record = types.SimpleNamespace(place=place, lay=lay, guest=ch.guest_from_chunks(place, chunks), contract=built['native_call_contract'], image=bytes(built['image'].data))
        if keep:
            self.cache[key] = record
        return record

    def test_every_d9_choice_builds_classifies_and_fits_the_cap(self):
        guests = set()
        for panel in ('withheld', 'handed'):
            for hvc in (0, 1):
                r = self.make(panel, hvc, keep=panel == 'withheld')
                head, table = sb.unpack_guest(r.guest)
                with self.subTest(panel=panel, hvc=hvc):
                    self.assertLess(len(r.guest), sb.FILE_MAX)
                    self.assertTrue(10 < len(table) <= sb.CHUNKS_MAX)
                    self.assertEqual({c['kind'] for c in table}, set(range(1, 8)))          # every kind but OTHER: no RAM disk, SEP preload or trust-cache fallback
                    self.assertTrue({True, False} <= {bool(c['flags'] & sb.CHUNK_ZERO) for c in table})
                    self.assertEqual((sb.params_dest(r.guest), head['load_base'], head['load_size']), (r.lay.params_pa, BANK, 384 * MIB))
                    params = next(c for c in table if c['kind'] == sb.KIND_PARAMS)
                    self.assertEqual(struct.unpack_from('<Q', r.guest, params['file_offset'] + 8 * ch.PARAMS_WATCHDOG)[0], 5)
                    self.assertEqual(struct.unpack_from('<2Q', r.guest, params['file_offset'] + 8 * ch.PARAMS_MEM_LOW), (BANK, r.place['work_base']))
                    self.assertEqual(r.place['work_base'], BANK + 384 * MIB + 48 * MIB)
                    guests.add(sha(r.guest))
        self.assertEqual(len(guests), 4, 'the D9 choices are four different guests')

    def test_the_kernelcache_is_unmodified_except_for_the_loaders_rebase_and_the_hvc_call_patch(self):
        sys.path.insert(0, str(PROBES))
        nativecalls, kc_load = importlib.import_module('nativecalls'), importlib.import_module('kc-load')
        image = kc_load.Image(self.kernelcache.read_bytes())
        fixed = bytearray(image.data)
        for offset, value in kc_load.chained_fixups(image, 0):       # the rebase: pointers resolved to their link-time values, no instruction touched
            struct.pack_into('<Q', fixed, offset, value)
        plain, patched = self.make('withheld', 0, keep=True), self.make('withheld', 1, keep=True)
        pa0 = plain.lay.classes[1][0]           # the image reservation: where the loader puts the first segment
        changed = []
        for r, patch in ((plain, False), (patched, True)):
            head, table = sb.unpack_guest(r.guest)
            for name, vmaddr, vmsize, fileoff, filesize in image.segments:
                if not filesize:
                    continue
                lo = pa0 + vmaddr - image.vmin
                got, want = bytearray(filesize), fixed[fileoff:fileoff + filesize]
                for c in table:
                    a, b = max(lo, c['dest']), min(lo + filesize, c['dest'] + c['size'])
                    if a < b and not c['flags'] & sb.CHUNK_ZERO:
                        got[a - lo:b - lo] = r.guest[c['file_offset'] + a - c['dest']:c['file_offset'] + b - c['dest']]
                for block in range(0, filesize, 0x4000):
                    if got[block:block + 0x4000] != want[block:block + 0x4000]:
                        changed += [(patch, name, block + k, bytes(got[block + k:block + k + 4]), bytes(want[block + k:block + k + 4]))
                                    for k in range(0, 0x4000, 4) if got[block + k:block + k + 4] != want[block + k:block + k + 4]]
        self.assertEqual([c for c in changed if not c[0]], [], 'with the call patch off, the image is the kernelcache with its pointers rebased')
        self.assertEqual(len(changed), len(patched.contract['call_sites']))
        for _, _, _, got, want in changed:      # the patch replaces exactly the words the loader's contract lists, by its one replacement word
            self.assertEqual((struct.unpack('<I', got)[0], struct.unpack('<I', want)[0] in nativecalls.CALL_WORDS), (nativecalls.HVC_WORD, True))
        self.assertEqual((plain.contract['mode'], patched.contract['mode']), ('private', 'hvc'))
        self.assertEqual(sha(plain.image), self.pinned)

    def test_the_same_inputs_make_the_same_guest_and_the_seed_is_the_only_nondeterminism(self):
        first, key = self.make('withheld', 0, keep=True), ('withheld', 0, 'sp11-stick-bundle')
        del self.cache[key]                                         # force a second, independent build
        second = self.make('withheld', 0)
        self.cache[key] = first
        self.assertEqual(first.guest, second.guest)
        other = self.make('withheld', 0, seed='another seed label')
        table, other_table = sb.unpack_guest(first.guest)[1], sb.unpack_guest(other.guest)[1]
        self.assertEqual([(c['dest'], c['size'], c['kind']) for c in table], [(c['dest'], c['size'], c['kind']) for c in other_table])
        body = lambda guest, c: b'' if c['flags'] & sb.CHUNK_ZERO else guest[c['file_offset']:c['file_offset'] + c['size']]        # noqa: E731
        differing = {c['kind'] for c in table if body(first.guest, c) != body(other.guest, c)}
        self.assertEqual(differing, {sb.KIND_HANDOFF}, 'only the handoff holds the boot seed')
    def test_the_a16_flows_own_offline_check_passes_at_the_bank_base(self):
        for hvc in (None, '0', '1'):
            xb = LOADER()
            xb.configure_guest()
            xb.configure_txm_arena()
            xb.configure_adreno_va_wide()
            xb.place(BANK, 384 * MIB + xb.bootstrap_reserve(384 * MIB), guest_span=384 * MIB)
            out = io.StringIO()
            with mock.patch.dict(os.environ, {} if hvc is None else {'SNAP_NATIVE_CALLS': '1', 'SNAP_SPTM_HVC_CALLS': hvc}), \
                    mock.patch.object(sys, 'argv', ['xnuboot.py', '--check']), contextlib.redirect_stdout(out):
                code = xb.main()
            lines = out.getvalue().splitlines()
            with self.subTest(hvc=hvc):
                self.assertEqual((code, sum(line.startswith('  ok ') for line in lines), any('FAIL' in line for line in lines)), (0, 12, False))

    def test_a_damaged_kernelcache_is_refused_by_the_loader_not_built(self):
        data = self.kernelcache.read_bytes()
        for name, damaged in (('truncated', data[:-MIB]), ('one byte changed', data[:4096] + bytes([data[4096] ^ 1]) + data[4097:]), ('empty', b'')):
            path = SCRATCH / 'in' / 'damaged.kc'
            path.write_bytes(damaged)
            try:
                with self.subTest(name):
                    a = argparse.Namespace(base=hex(BANK), guest_mib=384, reserve_mib=None, kernelcache=path, panel='withheld', sptm_hvc_calls=0, time_box_s=5, seed='x')
                    self.raises('BUILT_SHAPE', bb.real_built, a, LOADER(), bb.placement_of(a), self.facts)
            finally:
                path.unlink()
        self.assertEqual(snap_env(), [])

    bundles = {}

    def cli_bundle(self, panel, hvc):
        """One CLI build per D9 choice for the whole class (about 20 s: the record, then the files); with the stage tree when q1n1's manifest tool is there."""
        if (panel, hvc) not in self.bundles:
            extra = BT.stage_args() if bb.q1n1_tree() else ['--no-manifest']
            name = f'cli-{panel}{hvc}'
            code, text, err, out = build(name, extra=extra, panel=panel, sptm_hvc_calls=hvc, guest_mib=384, kernelcache=self.kernelcache)
            self.assertEqual(code, 0, (text, err))
            self.bundles[panel, hvc] = (out, text, (SCRATCH / f'records/{name}.txt').read_text())
        return self.bundles[panel, hvc]

    def test_the_cli_builds_the_real_bundle_for_both_d9_choices_and_every_check_accepts_it(self):
        for panel, hvc in (('withheld', 1), ('handed', 0)):
            out, text, record = self.cli_bundle(panel, hvc)
            lines = text.splitlines()
            self.assertEqual((lines[0][:11], lines[-1], sum(line.startswith('FILE ') for line in lines)), ('BUILT kind=', 'HARDWARE=NOT RUN', 6 if bb.q1n1_tree() else 5))
            files = BT.read_bundle(out)
            guest, pin = files['GUEST.DAT'], files['PIN.DAT']
            self.assertEqual((sb.check_pin(pin), sb.check_guest(guest, pin), sb.check_digests(guest, pin, sb.HASH_CHUNKS | sb.HASH_FILE)[0]), (0, 0, 0))
            self.assertLess(len(guest), sb.FILE_MAX)
            report = json.loads((out / bb.REPORT_NAME).read_text())
            self.assertEqual(report['real'], dict(kernelcache_sha256=self.pinned, guest_profile='j700ap-26A428', panel=panel, sptm_hvc_calls=hvc, seed='sp11-stick-bundle'))
            self.assertEqual([m['result'] for m in report['maps']], ['OK'] * 3)
            self.assertTrue(record.startswith('SNAPINTOSH SP11 STICK BUNDLE') and sha(guest) in record, 'the record is the record alone and names this GUEST.DAT')

    def test_the_c_twin_of_the_interface_accepts_the_real_bundle(self):
        files, place = BT.read_bundle(self.cli_bundle('withheld', 1)[0]), sb.placement(BANK, 384 * MIB, 48 * MIB)
        s = BT.scenario(files, self.facts, place)
        self.assertEqual(BT.H.py_run(s, 'preflight'), (0, None))
        self.assertEqual(BT.H.run_harness(['batch'], s.text('real', 'preflight')).split()[2:4], ['0', 'OK'])
        self.assertEqual(BT.H.run_harness(['batch'], s.text('real', 'params')).split()[-1], str(BANK + 0xC000))

    @BT.needs_q1n1
    def test_the_stage_tree_of_the_real_bundle_is_complete_under_the_stagers_verify(self):
        out = self.cli_bundle('withheld', 1)[0]
        found = bb.stager().cmd_verify(argparse.Namespace(dest=out / 'stage', pins=None, no_pins=True))
        self.assertEqual((found['status'], found['autorun']['problems']), ('COMPLETE', []))
        self.assertEqual(json.loads((out / bb.REPORT_NAME).read_text())['stage']['status'], 'COMPLETE')


# ---- Hygiene and the document -----------------------------------------------------------------------------------------------------
class Hygiene(BT.Case):
    def test_licence_size_and_no_machine_text_in_this_file_and_the_document(self):
        for path in (THIS, DOC):
            text = path.read_text()
            self.assertTrue(text.isascii(), path.name)
            self.assertIn('SPDX-License-Identifier: MIT', text[:200] if path.suffix != '.md' else text[:100], path.name)
            self.assertLess(len(text.splitlines()), 1500, path.name)
            self.assertLess(len(text.encode()), 30000 if path == DOC else 50 * 1024, path.name)
            for prefix in BT.MACHINE_PATHS:
                self.assertNotIn(prefix, text, f'{path.name}: {prefix}')

    def test_nothing_of_the_loader_or_the_kernelcache_is_written_in_this_file_or_the_document(self):
        for path in (THIS, DOC):
            text = path.read_text()
            self.assertIsNone(re.search(r'0xfffffe[0-9a-f]{10}', text, re.I), f'{path.name}: a kernel virtual address')
            self.assertIsNone(re.search(r'\b(?:0x)?[0-9a-f]{96,}\b', text, re.I), f'{path.name}: a long hexadecimal run (only SHA-256 digests belong in the document)')
        self.assertIsNone(re.search(r'\b[0-9a-f]{64}\b', THIS.read_text()), 'no digest is hard-coded in the test file')


class Document(BT.Case):
    NAMES = ('DESCRIPTOR_NOT_READY', 'FACTS_DISAGREE', 'KERNELCACHE_MISSING', 'ENV_SNAP_SET', 'BUILT_SHAPE', 'PLACE_DECLARED', 'AUTH_RECORD_MISMATCH')

    def test_the_note_is_labelled_and_says_what_ran_what_is_blocked_and_what_is_not_decided(self):
        text = DOC.read_text()
        self.assertTrue(text.startswith('<!-- SPDX-License-Identifier: MIT -->\n'))
        self.assertIn('Hardware: NOT RUN', text[:1200])
        for needle in ('--descriptor', 'require_boot_ready', 'D9', 'D11', 'SNAP_SEED', '--seed', 'probes/xnuboot.py --check', 'tools/monitor_baseline.py', 'WI 4b', 'WI 6', 'WI 11',
                       'UNKNOWN', 'UNIMPLEMENTED', 'place-check', 'test-sp11-stick-bundle-real.py', 'build/sp11-descriptor.local.json', 'tools/sp11_m1_descriptor.py', 'GUEST VIEW',
                       'D11 proper'):
            self.assertIn(needle, text)
        for name in self.NAMES:
            self.assertIn(f'`{name}`', text, name)
        mp = BT.load('sp11_monitor_port_tests', TOOLS / 'test-sp11-monitor-port.py')
        self.assertEqual((mp.check_tables(text), mp.check_labels(text)), ([], []))


if __name__ == '__main__':
    unittest.main()
