#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests for tools/sp11_m1_descriptor.py (docs/SP11-STICK-REAL.md section 3): the guest-view board descriptor of the first monitor run.

    python3 tools/test-sp11-m1-descriptor.py

No Apple input, no hardware, nothing under /Volumes. The template is the committed config/boards/sp11.json; the facts and the firmware dumps are the
synthetic ones of tools/test-sp11-stick-bundle-build.py (its helpers are loaded here with a scratch directory of its own,
build/sp11-m1-descriptor-scratch/, deleted at the end); the board and ACPI fixtures are those of tools/test-board-descriptor.py and
tools/test-hamoa-bootcontract.py. Every policy field the tool sets is altered in turn and the tool must refuse it, by the board's own rule where the
board has one and by its own comparison with the derivation where it has not. Evidence: EXPERIMENTAL. Hardware: NOT RUN."""
import contextlib
import copy
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOLS, THIS = ROOT / 'tools', Path(__file__).resolve()
TOOL, TEMPLATE = TOOLS / 'sp11_m1_descriptor.py', ROOT / 'config' / 'boards' / 'sp11.json'
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location('sp11_stick_bundle_build_tests', TOOLS / 'test-sp11-stick-bundle-build.py')
BT = importlib.util.module_from_spec(spec)
spec.loader.exec_module(BT)
import sp11_m1_descriptor as M  # noqa: E402

bb, bd, sb, TBD, HB = BT.bb, M.bd, BT.sb, BT.TBD, BT.HB
bc = HB.bc
MIB, BANK, ATTR = BT.MIB, BT.BANK, BT.ATTR
SCRATCH = ROOT / 'build' / 'sp11-m1-descriptor-scratch'
ST = types.SimpleNamespace()
DEVICES = ('uart-debug', 'keyboard', 'touchpad', 'touchscreen', 'nvme')
REFUSALS_OF_THE_TOOL = ('TEMPLATE_CHANGED', 'GIC_VERSION_UNKNOWN', 'DRAM_BANK_UNCLEAR', 'GUEST_VIEW_MISMATCH')


def setUpModule():
    shutil.rmtree(SCRATCH, ignore_errors=True)
    BT.SCRATCH = SCRATCH
    f = bb.load_facts(BT.EXAMPLE)
    BT.FIX['dumps'] = [str(BT.make_dump(n, f, m)[0]) for n, m in (('sd-run1', BT.memmap()), ('sd-run2', BT.memmap(extra=3)))]
    BT.FIX['acpi'] = BT.acpi_of(BT.tables_for(f))
    BT.FIX['facts'] = BT.facts_file('facts.json')
    ST.facts, ST.dumps, ST.version, ST.bank = M.gather(args())
    ST.derived = M.derive(template(), ST.facts, ST.version, ST.bank)
    ST.data = M.render(ST.derived)


def tearDownModule():
    shutil.rmtree(SCRATCH, ignore_errors=True)


def template():
    return json.loads(TEMPLATE.read_bytes())


def args(**over):
    return types.SimpleNamespace(**{**dict(facts=BT.FIX['facts'], dump=BT.FIX['dumps'], template=str(TEMPLATE)), **over})


def cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = M.main([str(a) for a in argv])
    return code, out.getvalue(), err.getvalue()


def inputs(facts=None, dumps=None, template_file=None):
    return ['--facts', facts or BT.FIX['facts'], *[x for d in (dumps or BT.FIX['dumps']) for x in ('--dump', d)], '--template', template_file or TEMPLATE]


def derive_to(out, **kw):
    return cli(['derive', *inputs(**kw), '--out', out])


def check_file(path, **kw):
    return cli(['check', '--descriptor', path, *inputs(**kw)])


def written(name, data):
    return BT.write(SCRATCH / 'in' / name, data if isinstance(data, bytes) else data.encode())


def altered(name, *edits):
    d = copy.deepcopy(ST.derived)
    for edit in edits:
        edit(d)
    return written(name, M.render(d))


@contextlib.contextmanager
def fake_root():
    """A directory of the scratch tree as the tool's repository root, so that "below build/" and "elsewhere" are both places a test may write, and a check
    that fails can only ever write there."""
    root = SCRATCH / 'root'
    (root / 'build').mkdir(parents=True, exist_ok=True)
    with mock.patch.object(M, 'ROOT', root):
        yield root


class Case(BT.Case):
    def refused_with(self, expected, result, *needles):
        self.refused(expected, result)
        for needle in needles:
            self.assertIn(needle, result[2])
        return result


# ---- What the tool derives --------------------------------------------------------------------------------------------------------
class Derive(Case):
    def test_the_derived_file_is_boot_ready_and_passes_the_builders_own_gate(self):
        code, text, err = derive_to(SCRATCH / 'out/derived.json')
        self.assertEqual((code, text.splitlines()[-1]), (0, 'HARDWARE=NOT RUN'), err)
        path = SCRATCH / 'out/derived.json'
        board = bd.load(path)
        self.assertIs(bd.require_boot_ready(board), board)
        digest, state, board = bb.descriptor_digest(types.SimpleNamespace(descriptor=path), ST.facts, 'real')
        self.assertEqual((board.board_id, board.status, board.ecam), ('sp11', 'EXPERIMENTAL', ()))
        self.assertEqual((digest.hex(), state), (BT.sha(path.read_bytes()), 'boot-ready'))
        self.assertIn(f'sha256 {digest.hex()}', text)
        self.assertEqual(path.read_bytes(), ST.data)

    def test_the_four_fields_of_the_unit_and_the_three_decisions_are_what_they_say(self):
        board = bd.loads(ST.data.decode())
        self.assertEqual((board.status, board.gic_version, board.timer_hz, board.ecam), ('EXPERIMENTAL', 4, 19200000, ()))
        self.assertEqual(board.midr, tuple((v, 0xffffffff) for v in ST.facts['midr']))
        self.assertEqual([r for r in board.ranges if r[1] == 'dram'], [('dram0', 'dram', BANK, 30720 * MIB, None)])
        self.assertEqual({n for n, (state, _, _) in board.devices.items() if state == 'absent'}, set(DEVICES))
        self.assertEqual({n: board.devices[n] for n in DEVICES}, {n: ('absent', None, ()) for n in DEVICES})
        self.assertEqual(bd.withdrawn_devices(board), tuple(sorted(DEVICES)))
        self.assertEqual(bd.unknown_devices(board), ())

    def test_only_the_named_claims_differ_from_the_template(self):
        t, d = template(), ST.derived
        neutral = lambda x: {**x, 'provenance': 0}      # noqa: E731
        self.assertEqual(set(M.differences(neutral(t), neutral(d))),
                         {'board.name', 'board.status', 'soc.midr', 'gic.version', 'timer.frequency_hz', 'ranges[0].base', 'ranges[0].size', 'pcie.ecam',
                          *[f'devices.{n}.state' for n in DEVICES]})
        self.assertEqual({k for k in set(t['provenance']) | set(d['provenance']) if t['provenance'].get(k) != d['provenance'].get(k)},
                         {'board', 'soc.midr', 'gic.version', 'timer.frequency_hz', 'ranges.dram0', 'pcie.ecam', *[f'devices.{n}' for n in DEVICES]})

    def test_every_decision_carries_the_operators_words_and_the_document_and_the_unit_fields_are_observed(self):
        provenance = ST.derived['provenance']
        for claim in ('board', 'pcie.ecam', *[f'devices.{n}' for n in DEVICES]):
            with self.subTest(claim):
                self.assertIn('operator decision 2026-11-16, guest view for the first monitor run', provenance[claim]['source'])
                self.assertIn('docs/SP11-STICK-REAL.md', provenance[claim]['source'])
                self.assertEqual(provenance[claim]['evidence'], 'EXPERIMENTAL')
        for claim in ('soc.midr', 'gic.version', 'timer.frequency_hz', 'ranges.dram0'):
            with self.subTest(claim):
                self.assertEqual(provenance[claim]['evidence'], 'OBSERVED')
                self.assertNotIn('operator decision', provenance[claim]['source'])
                self.assertIn('docs/', provenance[claim]['source'])
        self.assertEqual(sum('operator decision' in v['source'] for v in provenance.values()), 7)
        self.assertIn('GUEST VIEW', ST.derived['board']['name'])

    def test_two_derivations_are_byte_identical_and_the_format_is_fixed(self):
        derive_to(SCRATCH / 'out/one.json')
        derive_to(SCRATCH / 'out/two.json')
        self.assertEqual((SCRATCH / 'out/one.json').read_bytes(), (SCRATCH / 'out/two.json').read_bytes())
        for seed in ('1', '2'):         # a separate process each, with another hash seed: no ordering of a set or a dict may reach the file
            run = subprocess.run([sys.executable, str(TOOL), 'derive', *map(str, inputs()), '--out', str(SCRATCH / f'out/seed{seed}.json')],
                                 capture_output=True, text=True, env={**os.environ, 'PYTHONHASHSEED': seed}, timeout=120)
            self.assertEqual((run.returncode, run.stdout.splitlines()[-1]), (0, 'HARDWARE=NOT RUN'), run.stderr)
            self.assertEqual((SCRATCH / f'out/seed{seed}.json').read_bytes(), ST.data)
        data = ST.data
        self.assertTrue(data.isascii() and data.endswith(b'}\n') and not data.endswith(b'\n\n'))
        self.assertEqual(data, (json.dumps(json.loads(data), indent=2, sort_keys=True) + '\n').encode())
        self.assertEqual(M.render(copy.deepcopy(ST.derived)), data)

    def test_the_committed_template_is_not_touched_and_is_still_refused(self):
        before = TEMPLATE.read_bytes()
        derive_to(SCRATCH / 'out/untouched.json')
        self.assertEqual(TEMPLATE.read_bytes(), before)
        with self.assertRaises(bd.BoardNotBootReady):
            bd.require_boot_ready(bd.load(TEMPLATE))
        self.assertEqual(template()['board']['status'], 'UNIMPLEMENTED')

    def test_the_output_says_what_it_is_and_which_adapter_check_refuses_it(self):
        code, text, err = derive_to(SCRATCH / 'out/said.json')
        self.assertEqual(code, 0, err)
        for needle in ('GUEST VIEW', 'not the Surface\'s description', 'ecam-mismatch', 'D11 proper', 'from_hamoa and check_hamoa_platform', 'absent (withheld from the guest',
                       'nothing removed from the Surface', 'pcie.ecam []', 'what real pins as descriptor_sha256', 'boot-ready'):
            self.assertIn(needle, text)
        self.assertEqual(sum(line.startswith('EXPECTED ') for line in text.splitlines()), 1)


# ---- The policy fields are enforced: the board's own rule where it has one, the comparison with the derivation where it has not --------
class Policy(Case):
    def test_the_unaltered_file_passes_the_check(self):
        code, text, err = check_file(written('same.json', ST.data))
        self.assertEqual((code, text.splitlines()[0]), (0, f'CHECK identical to the derivation, sha256 {BT.sha(ST.data)}'), err)
        self.assertEqual(text.splitlines()[-1], 'HARDWARE=NOT RUN')

    def test_a_device_back_to_unknown_is_not_boot_ready_by_the_boards_rule(self):
        for name in DEVICES:
            def edit(d, name=name):
                d['devices'][name] = {'state': 'unknown', 'mmio': None, 'ranges': []}
                d['provenance'][f'devices.{name}'] = dict(TBD.UNKNOWN)
            with self.subTest(name):
                self.refused_with('DESCRIPTOR_NOT_READY', check_file(altered(f'unknown-{name}.json', edit)), 'not boot-ready', f"device state unknown for ['{name}']")

    def test_a_present_device_without_a_window_is_refused_by_the_boards_rule(self):
        for name in DEVICES:
            for mmio, needle in ((True, 'present mmio device has no range'), (None, 'a present device must state whether it needs its own range')):
                def edit(d, name=name, mmio=mmio):
                    d['devices'][name] = {'state': 'present', 'mmio': mmio, 'ranges': []}
                with self.subTest(name, mmio=mmio):
                    self.refused_with('DESCRIPTOR_NOT_READY', check_file(altered(f'present-{name}-{mmio}.json', edit)), needle)

    def test_status_unimplemented_and_an_unknown_ecam_are_refused_by_the_boards_rule(self):
        self.refused_with('DESCRIPTOR_NOT_READY', check_file(altered('status.json', TBD.S('board.status', 'UNIMPLEMENTED'))), 'board.status is UNIMPLEMENTED')

        def unknown_ecam(d):
            d['pcie']['ecam'] = None
            d['provenance']['pcie.ecam'] = dict(TBD.UNKNOWN)
        self.refused_with('DESCRIPTOR_NOT_READY', check_file(altered('ecam-null.json', unknown_ecam)), 'pcie.ecam is unknown')

    def test_a_duplicate_segment_is_refused_by_the_boards_rule(self):
        window = lambda segment, base: {'segment': segment, 'base': base, 'start_bus': 0, 'end_bus': 1}      # noqa: E731
        self.refused_with('DESCRIPTOR_NOT_READY', check_file(altered('dup.json', TBD.S('pcie.ecam', [window(0, '0x30000000'), window(0, '0x40000000')]))),
                          'duplicate PCI segment')

    def test_a_non_empty_ecam_is_boot_ready_and_refused_by_the_comparison_with_the_derivation(self):
        edit = TBD.S('pcie.ecam', [{'segment': 0, 'base': '0x40000000', 'start_bus': 0, 'end_bus': 255}])
        path = altered('ecam-one.json', edit)
        self.assertIsNotNone(bd.require_boot_ready(bd.load(path)))                         # the board accepts it: only this tool's policy says no
        self.refused_with('GUEST_VIEW_MISMATCH', check_file(path), 'pcie.ecam')

    def test_a_wrong_pin_is_refused_against_the_facts(self):
        wrong = {'a MIDR value': (TBD.S('soc.midr', [{'value': '0x511f0021', 'mask': '0xffffffff'}, {'value': '0x512f0011', 'mask': '0xffffffff'}]), 'midr'),
                 'the distributor': (TBD.S('gic.gicd.base', '0x17010000'), 'gicd_base'), 'the ITS': (TBD.S('gic.its', {'present': False}), 'its_base'),
                 'the counter frequency': (TBD.S('timer.frequency_hz', 24000000), 'cntfrq_hz'), 'the hypervisor timer': (TBD.S('timer.ppi.hyp', 25), 'timer_intid')}
        for name, (edit, needle) in wrong.items():
            with self.subTest(name):
                self.refused_with('FACTS_DISAGREE', check_file(altered(f'pin-{needle}.json', edit)), needle)

    def test_what_the_board_accepts_and_the_facts_do_not_state_is_still_compared_with_the_derivation(self):
        def present(d):         # a present device the board accepts: bus-attached, no window of its own
            d['devices']['nvme'] = {'state': 'present', 'mmio': False, 'ranges': []}
        cases = {'the GIC version': (TBD.S('gic.version', 3), 'gic.version'), 'the bank': (TBD.S('ranges.0.size', '0x700000000'), 'ranges[0].size'),
                 'a template claim': (TBD.S('smmu.0.size', '0x80000'), 'smmu[0].size'), 'a device given back': (present, 'devices.nvme')}
        for name, (edit, needle) in cases.items():
            with self.subTest(name):
                path = altered(f'diff-{needle}.json', edit)
                self.assertIsNotNone(bd.require_boot_ready(bd.load(path)))
                self.refused_with('GUEST_VIEW_MISMATCH', check_file(path), needle)

    def test_a_changed_provenance_and_a_change_of_format_only_are_refused_too(self):
        def other_words(d):
            claim = d['provenance']['devices.nvme']
            claim['source'] = claim['source'].replace('operator decision 2026-11-16', 'somebody decided')
        self.refused_with('GUEST_VIEW_MISMATCH', check_file(altered('words.json', other_words)), 'provenance.devices.nvme.source')
        self.refused_with('GUEST_VIEW_MISMATCH', check_file(written('compact.json', json.dumps(ST.derived))), 'formatting only')
        self.refused_with('GUEST_VIEW_MISMATCH', check_file(written('unsorted.json', json.dumps(ST.derived, indent=2) + '\n')), 'formatting only')


# ---- The inputs ---------------------------------------------------------------------------------------------------------------------
class Inputs(Case):
    @staticmethod
    def stated(field):
        """The committed template with exactly one of the fields the tool fills already stated, as a promoted template would state it."""
        t, d = template(), ST.derived
        if field == 'board.status':
            t['board']['status'] = 'EXPERIMENTAL'
        elif field in ('soc.midr', 'gic.version', 'timer.frequency_hz', 'pcie.ecam'):
            section, key = field.split('.')
            t[section][key] = d[section][key]
            t['provenance'][field] = d['provenance'][field]
        elif field == 'ranges.dram0':
            t['ranges'][0] = d['ranges'][0]
            t['provenance'][field] = d['provenance'][field]
        else:
            t['devices'][field.split('.')[1]] = d['devices'][field.split('.')[1]]
            t['provenance'][field] = d['provenance'][field]
        return written(f'template-{field}.json', json.dumps(t))

    def test_a_template_that_already_states_a_field_is_refused_not_overwritten(self):
        for field in ('board.status', 'soc.midr', 'gic.version', 'timer.frequency_hz', 'ranges.dram0', 'pcie.ecam', *[f'devices.{n}' for n in DEVICES]):
            with self.subTest(field):
                path = self.stated(field)
                bd.load(path)       # a valid template
                out = SCRATCH / f'out/promoted-{field}.json'
                self.refused_with('TEMPLATE_CHANGED', derive_to(out, template_file=path), field)
                self.assertFalse(out.exists())

    def test_a_missing_or_invalid_template_is_refused(self):
        self.refused('PAYLOAD_BAD', derive_to(SCRATCH / 'out/t1.json', template_file=SCRATCH / 'in/none.json'))
        self.refused('DESCRIPTOR_NOT_READY', derive_to(SCRATCH / 'out/t2.json', template_file=written('broken.json', '{"schema_version": 1}')))
        self.assertEqual(sorted((SCRATCH / 'out').glob('t[12].json')), [])

    def test_a_template_with_one_more_unresolved_device_is_not_resolved_by_the_tool(self):
        t = template()
        t['devices']['wifi'] = {'state': 'unknown', 'mmio': None, 'ranges': []}
        t['provenance']['devices.wifi'] = dict(TBD.UNKNOWN)
        out = SCRATCH / 'out/wifi.json'
        self.refused_with('DESCRIPTOR_NOT_READY', derive_to(out, template_file=written('template-wifi.json', json.dumps(t))), "device state unknown for ['wifi']")
        self.assertFalse(out.exists())

    def test_facts_that_disagree_with_a_dump_or_with_the_template_are_refused(self):
        self.refused_with('FACTS_DISAGREE', derive_to(SCRATCH / 'out/f1.json', facts=BT.facts_file('badgicd.json', gicd_base='0x17010000')), 'gicd_base')
        facts, dumps = BT.variant('dumpits', dict(its_base=0x17050000))         # the facts agree with the template, the dump does not agree with the facts
        self.refused_with('FACTS_DISAGREE', derive_to(SCRATCH / 'out/f0.json', facts=facts, dumps=dumps), 'its_base: facts')
        facts, dumps = BT.variant('othergicd', dict(gicd_base=0x17010000), gicd_base='0x17010000')       # facts and dump agree with each other, not with the template
        self.assertEqual(bb.facts_check(bb.load_facts(facts), bb.find_dumps(dumps))[0][-2:], 'OK')
        self.refused_with('FACTS_DISAGREE', derive_to(SCRATCH / 'out/f2.json', facts=facts, dumps=dumps), "['gicd_base']")
        self.refused('DUMP_INCOMPLETE', derive_to(SCRATCH / 'out/f3.json', dumps=[SCRATCH / 'nowhere']))
        self.assertEqual(sorted((SCRATCH / 'out').glob('f[0123].json')), [])

    @staticmethod
    def dump_with_version(name, version):
        f = bb.load_facts(BT.EXAMPLE)
        d, tables = BT.make_dump(name, f, BT.memmap())
        entries = [HB.gicd(f['gicd_base'], version), *[HB.gicc(i, m) for i, m in enumerate(BT.MPIDRS)], HB.gicr(f['gicr_first'], f['gicr_limit'] - f['gicr_first']),
                   HB.its(f['its_base'])]
        BT.write(d / 'APIC.bin', HB.madt(entries))
        return str(d), HB.madt(entries)

    def test_a_gic_version_the_madt_does_not_state_or_that_the_facts_contradict_is_refused(self):
        facts = lambda name, apic: BT.facts_file(name, acpi=[{**a, 'length': len(apic), 'sha256': BT.sha(apic)} if a['sig'] == 'APIC' else a for a in BT.FIX['acpi']])    # noqa: E731
        silent, apic0 = self.dump_with_version('sd-v0', 0)
        self.refused_with('GIC_VERSION_UNKNOWN', derive_to(SCRATCH / 'out/v0.json', facts=facts('v0.json', apic0), dumps=[silent]), 'one of [3, 4]')
        old, apic3 = self.dump_with_version('sd-v3', 3)
        self.refused_with('FACTS_DISAGREE', derive_to(SCRATCH / 'out/v3.json', facts=facts('v3.json', apic3), dumps=[old]), 'gicr_stride')
        self.assertEqual(sorted((SCRATCH / 'out').glob('v[03].json')), [])
        self.assertEqual(M.gic_version(ST.dumps, ST.facts), 4)
        self.raises('FACTS_DISAGREE', M.gic_version, ST.dumps, {**ST.facts, 'gicr_stride': 0x20000})       # the function's own check, whatever its caller did first

    def test_a_bank_that_is_not_one_conventional_write_back_descriptor_the_same_in_every_dump_is_refused(self):
        f = bb.load_facts(BT.EXAMPLE)
        dump = lambda name, descs: str(BT.make_dump(name, f, descs)[0])        # noqa: E731
        low_only = [d for d in BT.memmap() if d[1] < 1 << 32]
        cases = {'two banks': [dump('sd-two', [*BT.memmap(), (7, 0x2000000000, 256, ATTR)])], 'no bank': [dump('sd-none', low_only)],
                 'a bank that is not write-back': [dump('sd-nowb', [*low_only, (7, BANK, 30720 * 256, 0x1007)])],
                 'a bank that is not conventional': [dump('sd-notconv', [*low_only, (6, BANK, 30720 * 256, ATTR)])],
                 'dumps that disagree': [BT.FIX['dumps'][0], dump('sd-small', [*low_only, (7, BANK, 20480 * 256, ATTR)])]}
        for name, dumps in cases.items():
            with self.subTest(name):
                self.refused('DRAM_BANK_UNCLEAR', derive_to(SCRATCH / f'out/bank-{len(name)}.json', dumps=dumps))
        self.assertEqual(sorted((SCRATCH / 'out').glob('bank-*.json')), [])
        self.assertEqual(M.dram_bank(bb.find_dumps(BT.FIX['dumps'])), (BANK, 30720 * MIB))


# ---- Where it writes -----------------------------------------------------------------------------------------------------------------
class Output(Case):
    def test_it_never_overwrites(self):
        out = SCRATCH / 'out/once.json'
        self.assertEqual(derive_to(out)[0], 0)
        first = out.read_bytes()
        out.chmod(0o644)
        self.refused_with('OUT_NOT_EMPTY', derive_to(out), 'never overwrites')
        self.assertEqual(out.read_bytes(), first)
        out.write_bytes(b'something else')
        self.refused('OUT_NOT_EMPTY', derive_to(out))
        self.assertEqual(out.read_bytes(), b'something else')
        directory = SCRATCH / 'out/a-directory'
        directory.mkdir()
        self.refused('OUT_NOT_EMPTY', derive_to(directory))

    def test_it_writes_only_below_build_unless_told_otherwise_and_never_through_a_link(self):
        with fake_root() as root:
            for out in (root / 'tools/x.json', root / 'docs/x.json', root / 'config/boards/x.json', root.parent / 'x.json', root / 'build', root / 'build/../build/x.json'):
                with self.subTest(out.relative_to(root.parent).as_posix()):
                    self.refused('OUT_DIR_REFUSED', derive_to(out))
            self.assertEqual(sorted(p.relative_to(root).as_posix() for p in root.rglob('*')), ['build'])      # nothing written, no directory made
            (SCRATCH / 'real-dir').mkdir(parents=True)
            (root / 'build/link-dir').symlink_to(SCRATCH / 'real-dir')
            self.refused('OUT_DIR_REFUSED', derive_to(root / 'build/link-dir/through.json'))
            self.assertEqual(list((SCRATCH / 'real-dir').iterdir()), [])
            elsewhere = SCRATCH / 'elsewhere'
            self.assertEqual(cli(['derive', *inputs(), '--out', elsewhere / 'sub/ok.json', '--allow-under', elsewhere])[0], 0)
            self.assertEqual((elsewhere / 'sub/ok.json').read_bytes(), ST.data)
            self.refused('OUT_DIR_REFUSED', cli(['derive', *inputs(), '--out', SCRATCH / 'other/x.json', '--allow-under', elsewhere]))
            self.refused('OUT_DIR_REFUSED', cli(['derive', *inputs(), '--out', elsewhere, '--allow-under', elsewhere]))     # the directory itself is no file below it
            self.assertFalse((SCRATCH / 'other').exists())
            self.assertEqual(derive_to(root / 'build/ok.json')[0], 0)

    def test_a_path_under_volumes_or_dev_is_refused_by_its_spelling_even_when_allowed(self):
        with mock.patch.object(M, 'write_new') as write:        # a failing check must not be able to write there
            for spelling in ('/Volumes/SP11BOOT/descriptor.json', '/volumes/sp11boot/descriptor.json', '/dev/m1-descriptor.json'):
                for allow in ([], ['--allow-under', str(Path(spelling).parent)]):
                    with self.subTest(spelling, allowed=bool(allow)):
                        self.refused_with('OUT_DIR_REFUSED', cli(['derive', *inputs(), '--out', spelling, *allow]), 'dest_under_')
        write.assert_not_called()

    def test_it_makes_the_directories_below_build_and_a_plain_file(self):
        out = SCRATCH / 'out/deep/er/descriptor.json'
        self.assertEqual(derive_to(out)[0], 0)
        self.assertEqual((out.stat().st_mode & 0o777, out.read_bytes()), (0o644, ST.data))

    def test_a_refused_output_reads_no_input_and_a_refused_derivation_writes_nothing(self):
        with fake_root() as root:
            self.refused('OUT_DIR_REFUSED', derive_to(root / 'tools/never.json', facts=SCRATCH / 'in/missing-facts.json'))        # the path is judged first
            out = root / 'build/nothing.json'
            self.refused('PAYLOAD_BAD', derive_to(out, facts=SCRATCH / 'in/missing-facts.json'))
            self.assertFalse(out.exists())


# ---- The adapter refuses a guest view, as announced ------------------------------------------------------------------------------------
class Adapter(Case):
    def test_check_hamoa_platform_refuses_it_at_the_ecam_equality_rule_and_only_there(self):
        board = bd.loads(ST.data.decode())
        with self.assertRaises(bc.BootContractError) as caught:
            bc.check_hamoa_platform(board, HB.good())
        self.assertEqual(caught.exception.code, 'ecam-mismatch')
        self.assertIn('missing [], unexpected', caught.exception.reason)
        d = copy.deepcopy(ST.derived)
        d['pcie']['ecam'] = [dict(segment=HB.ECAM[0], base=f'{HB.ECAM[1]:#x}', start_bus=HB.ECAM[2], end_bus=HB.ECAM[3])]
        agreed = bc.check_hamoa_platform(bd.validate(d), HB.good())         # the same descriptor with the tables' one allocation listed: every other check passes
        self.assertEqual((agreed.gic_version, len(agreed.mpidrs)), (4, 12))

    def test_from_hamoa_refuses_it_for_the_same_reason_and_accepts_the_view_once_the_list_matches(self):
        with self.assertRaises(bc.BootContractError) as caught:
            bc.HostRuntime.from_hamoa(HB.host_info(), bd.loads(ST.data.decode()), HB.good())
        self.assertEqual(caught.exception.code, 'ecam-mismatch')
        d = copy.deepcopy(ST.derived)
        d['pcie']['ecam'] = [dict(segment=HB.ECAM[0], base=f'{HB.ECAM[1]:#x}', start_bus=HB.ECAM[2], end_bus=HB.ECAM[3])]
        self.assertEqual(bc.HostRuntime.from_hamoa(HB.host_info(), bd.validate(d), HB.good()).adapter, 'hamoa')

    def test_no_table_can_satisfy_an_empty_list_which_is_why_d11_proper_is_left_for_later(self):
        with self.assertRaises(bc.BootContractError) as caught:
            bc.check_hamoa_platform(bd.loads(ST.data.decode()), HB.good(ecam=()))
        self.assertEqual(caught.exception.code, 'table-structure')

    def test_check_hamoa_acpi_prints_a_ready_gate_and_then_the_same_refusal(self):
        dump = HB.write_dump(SCRATCH / 'acpi/guest-view')
        code, out, err = HB.run_cli(dump, '--descriptor', written('guest-view.json', ST.data))
        self.assertEqual(code, 1, err)
        self.assertIn('descriptor boot gate : ready', out)
        self.assertTrue(out.splitlines()[-1].startswith('REFUSED: ecam-mismatch'), out)
        d = copy.deepcopy(ST.derived)
        d['pcie']['ecam'] = [dict(segment=HB.ECAM[0], base=f'{HB.ECAM[1]:#x}', start_bus=HB.ECAM[2], end_bus=HB.ECAM[3])]
        code, out, err = HB.run_cli(dump, '--descriptor', written('with-ecam.json', M.render(d)))
        self.assertEqual((code, err), (0, ''), out)         # the same dump, the same descriptor but for the one list: accepted
        self.assertTrue(out.splitlines()[-1].startswith('ACCEPTED: '), out)


# ---- The document ----------------------------------------------------------------------------------------------------------------------
class Document(Case):
    def test_the_note_says_what_the_file_is_and_what_it_leaves_open(self):
        doc = ROOT / 'docs/SP11-STICK-REAL.md'
        text = doc.read_text()
        self.assertLess(len(text.encode()), 30000)
        self.assertIn('Hardware: NOT RUN', text[:600])
        self.assertRegex(text, r'(?m)^## 3\. ')         # the section every provenance string of the tool cites
        self.assertIn(M.DOC, 'docs/SP11-STICK-REAL.md section 3')
        for needle in ('tools/sp11_m1_descriptor.py', 'tools/test-sp11-m1-descriptor.py', 'GUEST VIEW', 'D11 proper', 'ecam-mismatch', 'from_hamoa', 'check_hamoa_platform',
                       'X6', 'N2', 'operator decision 2026-11-16, guest view for the first monitor run', "not the Surface's description", 'never overwrites', '`OUT_NOT_EMPTY`',
                       '`OUT_DIR_REFUSED`', *[f'`{name}`' for name in REFUSALS_OF_THE_TOOL], 'derive --facts', 'check --descriptor'):
            self.assertIn(needle, text)
        for device in DEVICES:
            self.assertIn(device if device != 'uart-debug' else '`devices.uart-debug`', text)
        mp = BT.load('sp11_monitor_port_tests', TOOLS / 'test-sp11-monitor-port.py')
        self.assertEqual((mp.check_tables(text), mp.check_labels(text)), ([], []))


# ---- Hygiene ---------------------------------------------------------------------------------------------------------------------------
class Hygiene(Case):
    def test_licence_size_and_no_machine_or_apple_text(self):
        for path in (TOOL, THIS):
            text = path.read_text()
            self.assertTrue(text.isascii(), path.name)
            self.assertIn('SPDX-License-Identifier: MIT', text[:200], path.name)
            self.assertLess(len(text.splitlines()), 500 if path == TOOL else 1500, path.name)
            self.assertLess(len(text.encode()), 50 * 1024, path.name)
            for prefix in BT.MACHINE_PATHS:
                self.assertNotIn(prefix, text, f'{path.name}: {prefix}')
            self.assertIsNone(re.search(r'0xfffffe[0-9a-f]{10}|\b[0-9a-f]{64}\b', text, re.I), path.name)
        self.assertIsNone(re.search(r'\b[0-9a-f]{64}\b', ST.data.decode()), 'the derived file names no digest')
        for prefix in BT.MACHINE_PATHS:
            self.assertNotIn(prefix, ST.data.decode(), f'the derived file: {prefix}')

    def test_the_tool_says_what_it_is_and_edits_no_rule_of_the_board_modules(self):
        text = TOOL.read_text()
        for needle in ('GUEST VIEW', 'ecam-mismatch', 'D11 proper', 'never overwrites', 'Hardware: NOT RUN', 'from_hamoa', 'check_hamoa_platform', 'require_boot_ready',
                       'docs/SP11-STICK-REAL.md'):
            self.assertIn(needle, text)
        self.assertIsNone(re.search(r'\b(bd|bc|bb|ch|sb)\.[\w.]+\s*(=[^=]|\+=)', text), 'the tool assigns to a module it only uses')
        self.assertIsNone(re.search(r'^\s*(import|from)\s+(bootcontract|check_hamoa|importlib)', text, re.M), 'it loads neither adapter module')

    def test_the_tools_refusal_names_are_registered_and_new(self):
        for name in REFUSALS_OF_THE_TOOL:
            self.assertIn(name, M.ch.KNOWN_REFUSALS)
            self.assertRegex(name, r'\A[A-Z0-9_]{1,24}\Z')
        self.assertEqual(set(REFUSALS_OF_THE_TOOL), set(M.OWN_REFUSALS))
        self.assertTrue(set(REFUSALS_OF_THE_TOOL).isdisjoint({n for _, n, _ in sb.ERRORS} | set(bb.BUILDER_REFUSALS)))


if __name__ == '__main__':
    unittest.main()
