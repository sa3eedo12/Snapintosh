#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests for tools/sp11_stick_bundle_build.py and tools/sp11_stick_bundle_chunks.py (docs/SP11-STICK-BUILD.md).

    python3 tools/test-sp11-stick-bundle-build.py
    Q1N1_SP11_TREE=/nonexistent python3 tools/test-sp11-stick-bundle-build.py     (the tests that need q1n1's manifest tool SKIP)
    SP11_BUILD_SKIP_SLOW=1 python3 tools/test-sp11-stick-bundle-build.py          (skips the build at the default, realistic sizes)

No hardware, nothing under /Volumes. Every file is written below build/sp11-bundle-build-scratch/, deleted at the end. Fixtures are synthetic.
The tests that read the real dumps under downloads/sp11/ (git-ignored local data) SKIP when they are absent: a skip is never a pass. The C
validators run under AddressSanitizer through the harness of tools/test-sp11-stick-bundle.py. Hardware NOT RUN."""
import argparse
import contextlib
import dataclasses
import hashlib
import importlib.util
import io
import json
import os
import random
import re
import shutil
import struct
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))
import sp11_stick_bundle as sb  # noqa: E402
import sp11_stick_bundle_build as bb  # noqa: E402
import sp11_stick_bundle_chunks as ch  # noqa: E402

SCRATCH = ROOT / 'build' / 'sp11-bundle-build-scratch'
EXAMPLE, DOC, THIS = TOOLS / 'testdata/sp11-facts.example.json', ROOT / 'docs/SP11-STICK-BUILD.md', Path(__file__).resolve()
MIB, MODULES = sb.MIB, (TOOLS / 'sp11_stick_bundle_build.py', TOOLS / 'sp11_stick_bundle_chunks.py')
MPIDRS = (0x0, 0x100, 0x200, 0x300, 0x10000, 0x10100, 0x10200, 0x10300, 0x20000, 0x20100, 0x20200, 0x20300)
ATTR, LOW, BANK, FIX = 0x100F, 0x98000000, 0x880000000, {}
MACHINE_PATHS = ('/' + 'Users' + '/', '/' + 'home' + '/', '/' + 'tmp' + '/', 'mk' + 'temp')     # written so that this file does not contain them
sha = lambda data: hashlib.sha256(data).hexdigest()     # noqa: E731
needs_q1n1 = unittest.skipUnless(bb.q1n1_tree(), 'no q1n1 checkout (set Q1N1_SP11_TREE): its manifest tool cannot be used')
REAL_DUMPS = list((ROOT / 'downloads/sp11').glob('x*-run*/summary.txt'))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


H = load('sp11_stick_bundle_tests', TOOLS / 'test-sp11-stick-bundle.py')       # the C harness, Scn, efi_map
BM = load('sp11_boot_media_tests', TOOLS / 'test-sp11-boot-media.py')          # make_pe
HB = load('hamoa_bootcontract_tests', TOOLS / 'test-hamoa-bootcontract.py')    # the synthetic ACPI table builders
TBD = load('board_descriptor_tests', TOOLS / 'test-board-descriptor.py')       # a boot-ready synthetic descriptor


# ---- Synthetic fixtures ----
def write(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(data)
    return str(path)


def tables_for(f, gicd_length=24, per_cpu=False):
    """The tables a dump of these facts holds. per_cpu is the unit's firmware form: a 25-byte GICD, each CPU's own redistributor base in its GICC and
    no redistributor range structure."""
    gtdt = struct.pack('<QIIIIIIIIIQII', (1 << 64) - 1, 0, 29, 4, 30, 4, 27, 4, f['timer_intid'], 4, (1 << 64) - 1, 0, 0)
    cpus = [HB.gicc(i, m, gicr=f['gicr_first'] + i * f['gicr_stride'] if per_cpu else 0) for i, m in enumerate(MPIDRS)]
    gicr = [] if per_cpu else [HB.gicr(f['gicr_first'], f['gicr_limit'] - f['gicr_first'])]
    entries = [HB.gicd(f['gicd_base'], 4), *cpus, *gicr, HB.its(f['its_base'])]
    if gicd_length != 24:
        entries[0] = HB.resized(entries[0], gicd_length)
    return {'APIC': HB.madt(entries), 'GTDT': HB.sdt(b'GTDT', gtdt), 'MCFG': HB.mcfg([(0, 0x40000000, 0, 255)]), 'FACP': HB.sdt(b'FACP', bytes(240))}


def memmap(split=False, extra=0):
    low = [(7, LOW, 256 * 256, ATTR), (7, LOW + 256 * MIB, 334 * 256, ATTR)] if split else [(7, LOW, 590 * 256, ATTR)]
    return [(0, 0x80000000, 0x100, ATTR), *low, (6, 0xCD000000, 0x40, ATTR), (0, 0xE0000000, 0x1518, ATTR), (7, BANK, 30720 * 256, ATTR),
            *[(1, 0xD0000000 + 0x2000 * i, 1, ATTR) for i in range(extra)]]


def make_dump(name, f, descs, summary=True, **table_kw):
    d, tables = SCRATCH / 'dumps' / name, tables_for(f, **table_kw)
    for sig, data in tables.items():
        write(d / f'{sig}.bin', data)
    write(d / 'memmap.raw', H.efi_map(descs))
    gop = [f'gop_width={f["fb_width"]}', f'gop_height={f["fb_height"]}', f'gop_stride_pixels={f["fb_stride"]}', f'gop_format={f["fb_format"]:#x}',
           f'gop_base={f["fb_base"]:#018x}', f'gop_size={f["fb_bytes"]:#018x}']
    if summary:
        write(d / 'summary.txt', ('# synthetic dump\n' + '\n'.join([f'memmap_count={len(descs)}', 'memmap_descriptor_size=48', *gop]) + '\n').encode())
    return d, tables


def acpi_of(tables):
    return [dict(sig=s, length=len(t), sha256=sha(t)) for s, t in sorted(tables.items())]


def facts_file(name, **changes):
    """The example's facts with the ACPI digests of the synthetic dumps (so they agree with those), then `changes`."""
    raw = {**json.loads(EXAMPLE.read_text()), 'acpi': FIX.get('acpi', []), **changes}
    return write(SCRATCH / 'facts' / name, json.dumps(raw).encode())


def variant(name, dump=None, **raw):
    """(facts file, [dump dir]): a dump built from the example's facts with `dump` changed, and facts whose ACPI list is that dump's."""
    d, tables = make_dump(f'sd-{name}', {**bb.load_facts(EXAMPLE), **(dump or {})}, memmap())
    return facts_file(f'{name}.json', **{'acpi': acpi_of(tables), **raw}), [str(d)]


def pe(subsystem, tag):
    return BM.make_pe(subsystem=subsystem, body=tag * 32)


def setUpModule():
    shutil.rmtree(SCRATCH, ignore_errors=True)
    f = bb.load_facts(EXAMPLE)
    FIX['dumps'] = [str(make_dump(n, f, m)[0]) for n, m in (('sd-run1', memmap()), ('sd-run2', memmap(extra=3)), ('sd-run3', memmap(split=True, extra=1)))]
    FIX['acpi'] = acpi_of(tables_for(f))
    FIX['facts'] = facts_file('facts.json')
    for key, name in (('monitor', 'SNAPMON.BIN'), ('reporter', 'SNAPREP.BIN')):
        FIX[key] = write(SCRATCH / f'in/{key}.bin', f'PLACEHOLDER {name}: the wrapper and the reporter do not exist yet (work item 4)\n'.encode() * 4)
    argv = ['stage', '--no-pins', '--dest', str(SCRATCH / 'base')]
    for flag, name, subsystem in (('--shell', 'BOOTAA64.EFI', 10), ('--slbounce', 'sl.efi', 12), ('--sltest', 'sltest.efi', 10), ('--tcblaunch', 'tcb.exe', 10)):
        argv += [flag, write(SCRATCH / 'in' / name, pe(subsystem, name.encode()))]
    with contextlib.redirect_stdout(io.StringIO()):
        assert bb.stager().main(argv) == 0
    FIX['efi'] = [[n, write(SCRATCH / 'in' / n, pe(s, n.encode()))] for n, s in ((bb.SLBOUNCE, 12), (bb.RUNNER, 10))]
    FIX['efi'] = [[n, p, sha(Path(p).read_bytes())] for n, p in FIX['efi']]


def tearDownModule():
    shutil.rmtree(SCRATCH, ignore_errors=True)


def opts(kind='synthetic', **over):
    o = dict(facts=FIX['facts'], base=hex(BANK), guest_mib=256, monitor=FIX['monitor'], reporter=FIX['reporter'], stores='ram,el2_sysreg', time_box_s=5,
             dump=FIX['dumps'], **(dict(image_mib=8, tables_mib=1, sptm_data_mib=4) if kind == 'synthetic' else {}))
    return [x for k, value in {**o, **over}.items() for v in (value if isinstance(value, list) else [value]) if v is not None for x in ('--' + k.replace('_', '-'), str(v))]


def cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = bb.main([str(a) for a in argv])
    return code, out.getvalue(), err.getvalue()


def template(kind='synthetic', **over):
    return cli(['authorisation-template', kind, *opts(kind, **over)])


def build_cli(name, extra=(), approve=True, **over):
    out, argv = SCRATCH / 'out' / name, ['synthetic', *opts(**over), '--out', SCRATCH / 'out' / name, *extra]
    if approve:
        argv += ['--authorisation', write(SCRATCH / f'records/{name}.txt', template(**over)[1].encode())]
    return (*cli(argv), out)


def refusal(text):
    last = text.splitlines()[-1] if text else ''
    return last if last.startswith('REFUSED=') else None


def tree_digests(root):
    return {p.relative_to(root).as_posix(): sha(p.read_bytes()) for p in sorted(Path(root).rglob('*')) if p.is_file()}


def stage_args(base=None):
    return ['--base-tree', base or SCRATCH / 'base', '--no-pins', *[x for item in FIX['efi'] for x in ('--file', *item)]]


class Case(unittest.TestCase):
    def refused(self, expected, result):
        self.assertEqual((result[0], refusal(result[1])), (1, f'REFUSED={expected}'), result[1:3])

    def raises(self, expected, fn, *args):
        with self.assertRaises(ch.Refused) as caught:
            fn(*args)
        self.assertEqual(caught.exception.name, expected, caught.exception.detail)


# ---- Facts and dumps ----
class Facts(Case):
    def test_the_example_is_valid_and_synthetic(self):
        f, text = bb.load_facts(EXAMPLE), EXAMPLE.read_text()
        self.assertIn('SYNTHETIC EXAMPLE', text)
        self.assertEqual((f['midr'], f['gicr_count'], f['timer_intid'], f['fb_base']), ([0x511F0011, 0x512F0011], 12, 26, 0xE0000000))
        self.assertNotIn('E4800000', text.upper())

    def test_facts_agree_with_the_synthetic_dumps_and_each_dump_is_named(self):
        code, text, _ = cli(['facts-check', '--facts', FIX['facts'], *[x for d in FIX['dumps'] for x in ('--dump', d)]])
        self.assertEqual(code, 0, text)
        self.assertEqual([line.split()[:3] for line in text.splitlines()[:3]], [['DUMP', f'sd-run{i}', 'OK'] for i in (1, 2, 3)])
        self.assertIn('NOT CHECKED FROM A DUMP: midr, cntfrq_hz', text)

    def test_every_seeded_disagreement_is_refused_with_its_name(self):
        f, dumps = bb.load_facts(FIX['facts']), bb.find_dumps(FIX['dumps'])
        acpi = lambda sig, n, d: [(s, n if s == sig else m, d if s == sig else x) for s, m, x in f['acpi']]      # noqa: E731
        cases = {'gicd_base': dict(gicd_base=f['gicd_base'] + 0x10000), 'gicr_first': dict(gicr_first=f['gicr_first'] + 0x10000),
                 'gicr_limit': dict(gicr_limit=f['gicr_limit'] + 0x40000), 'gicr_count': dict(gicr_count=11), 'gicr_stride': dict(gicr_stride=0x20000),
                 'its_base': dict(its_base=0), 'timer_intid': dict(timer_intid=27), 'boot_mpidr': dict(boot_mpidr=0x80000123),
                 'fb_base': dict(fb_base=f['fb_base'] + 0x1000), 'fb_width': dict(fb_width=1800), 'fb_format': dict(fb_format=0),
                 'acpi APIC sha256': dict(acpi=acpi('APIC', 1, bytes(32))), 'acpi GTDT length': dict(acpi=acpi('GTDT', 1, bytes(32))),
                 'ecam': dict(ecam=[(0, 0x40000000, 0, 254)]), 'fb_base ': dict(fb_base=BANK + 0x1000)}
        for name, change in cases.items():
            with self.subTest(fact=name):
                with self.assertRaises(ch.Refused) as caught:
                    bb.facts_check({**f, **change}, dumps)
                self.assertEqual(caught.exception.name, 'FACTS_DISAGREE')
                self.assertIn(name.strip(), caught.exception.detail)
        self.assertEqual(bb.facts_check({**f, 'ecam': [(0, 0x40000000, 0, 255)]}, dumps)[-1][:11], 'NOT CHECKED')

    def test_a_dump_that_disagrees_alone_is_named_and_the_units_firmware_form_is_read(self):
        f = bb.load_facts(FIX['facts'])
        d, tables = make_dump('sd-unitform', f, memmap(), gicd_length=25, per_cpu=True)
        unit = {**f, 'acpi': [(s, len(t), hashlib.sha256(t).digest()) for s, t in sorted(tables.items())]}
        self.assertEqual(bb.facts_check(unit, bb.find_dumps([d]))[0], 'DUMP sd-unitform OK')
        for name, change in (('stride', dict(gicr_stride=0x20000)), ('limit', dict(gicr_limit=f['gicr_limit'] + 0x40000)), ('count', dict(gicr_count=11))):
            self.raises('FACTS_DISAGREE', bb.facts_check, {**unit, **change}, bb.find_dumps([d]))
        gap, tables = make_dump('sd-gap', {**f, 'gicr_first': f['gicr_first'] + 0x10000}, memmap(), per_cpu=True)
        self.assertIn('gicr', self.disagreements({**unit, 'gicr_first': f['gicr_first'] + 0x10000}, [gap]))
        odd, _ = make_dump('sd-odd', {**f, 'gicd_base': f['gicd_base'] + 0x10000}, memmap())
        detail = self.disagreements(f, [FIX['dumps'][0], odd])
        self.assertIn('sd-odd', detail)
        self.assertNotIn('sd-run1 ', detail)

    def disagreements(self, facts, dirs):
        with self.assertRaises(ch.Refused) as caught:
            bb.facts_check(facts, bb.find_dumps(dirs))
        return caught.exception.detail

    def test_a_missing_or_damaged_table_and_a_missing_dump_are_refused(self):
        f = bb.load_facts(FIX['facts'])
        d, _ = make_dump('sd-nogtdt', f, memmap())
        (d / 'GTDT.bin').unlink()
        self.raises('DUMP_INCOMPLETE', bb.facts_check, f, bb.find_dumps([d]))
        d, _ = make_dump('sd-badsum', f, memmap())
        apic = (d / 'APIC.bin').read_bytes()
        (d / 'APIC.bin').write_bytes(bytes([apic[0] ^ 1]) + apic[1:])
        self.raises('DUMP_INCOMPLETE', bb.facts_check, f, bb.find_dumps([d]))
        self.raises('DUMP_INCOMPLETE', bb.find_dumps, [SCRATCH / 'nowhere'])
        with mock.patch.object(bb, 'ROOT', SCRATCH / 'empty'):
            self.raises('NO_DUMPS', bb.find_dumps, [])

    def test_the_loader_reads_strictly(self):
        raw = EXAMPLE.read_text()
        bad = {'duplicate key': raw.replace('"cntfrq_hz"', '"boot_mpidr": 1, "cntfrq_hz"', 1), 'unknown key': raw.replace('"board_id"', '"extra": 1, "board_id"'),
               'not json': '{', 'not a number': raw.replace('19200000', '"fast"'), 'format': raw.replace('bundle-facts-1', 'bundle-facts-2')}
        for name, text in bad.items():
            with self.subTest(case=name):
                self.raises('FACTS_BAD', bb.load_facts, write(SCRATCH / 'facts/bad.json', text.encode()))
        self.raises('PIN_STRING', bb.load_facts, write(SCRATCH / 'facts/label.json', raw.replace('example-synthetic', 'x' * 30).encode()))
        many = raw.replace('"avoid": [', '"avoid": [' + '{"base": 4096, "size": 4096, "tag": 1},' * 4)
        self.raises('PIN_COUNT', bb.load_facts, write(SCRATCH / 'facts/avoid.json', many.encode()))
        self.raises('PAYLOAD_BAD', bb.load_facts, SCRATCH / 'facts/none.json')

    def test_facts_the_interface_refuses_are_refused_by_its_name_before_any_filler_exists(self):
        for expected, dump, raw in (('PIN_ORDER', None, dict(midr=['0x511F0011', '0x511F0011'])), ('PIN_COUNT', None, dict(midr=[])),
                                    ('PIN_VALUE', dict(timer_intid=32), dict(timer_intid=32)), ('PIN_VALUE', dict(its_base=0x17001000), dict(its_base=0x17001000))):
            with self.subTest(expected, raw=list(raw)):
                facts, dumps = variant(expected.lower(), dump, **raw)
                self.refused(expected, template(facts=facts, dump=dumps))


@unittest.skipUnless(REAL_DUMPS, 'no firmware dumps under downloads/sp11 (git-ignored local data)')
class RealDumps(Case):
    def test_the_documented_constants_agree_with_every_real_dump_and_the_example_is_synthetic_where_it_differs(self):
        dumps, f = bb.find_dumps([]), bb.load_facts(EXAMPLE)
        self.assertGreaterEqual(len(dumps), 3)
        d = dumps[0]
        local = {**f, 'fb_base': bb.number(d.summary['gop_base'], 'x'), 'fb_bytes': bb.number(d.summary['gop_size'], 'x'),
                 'acpi': [(s, len(bb.sdt(d, s)), hashlib.sha256(bb.sdt(d, s)).digest()) for s, _, _ in f['acpi']]}
        self.assertEqual(bb.facts_check(local, dumps)[-1][:11], 'NOT CHECKED')
        with self.assertRaises(ch.Refused) as caught:
            bb.facts_check(f, dumps)
        names = {m.group(1) for m in re.finditer(r'(?:^|; )([a-z_0-9 ]+): facts', caught.exception.detail)}
        self.assertTrue(names and all(n == 'fb_base' or n.startswith('acpi') for n in names), names)

    def test_the_bank_and_a_low_guest_pass_every_map_and_a_larger_low_guest_does_not(self):
        found = re.search(r'PROPOSAL low base=(0x[0-9a-f]+) extent_mib=576 guest_mib_max=288 verified_guest_mib=288 maps=(\d+)/\2',
                          cli(['place-check', '--base', hex(BANK), '--guest-mib', 384])[1])
        self.assertTrue(found)
        for base, guest, ok in ((BANK, 384, True), (int(found.group(1), 16), 288, True), (int(found.group(1), 16), 320, False)):
            code, text, _ = cli(['place-check', '--base', hex(base), '--guest-mib', guest])
            self.assertEqual((code, refusal(text)), (0, None) if ok else (1, 'REFUSED=PLACE_NOT_CONV'), text)


# ---- Placement ----
class Place(Case):
    def place(self, base, guest, *extra, facts=None, dumps=None):
        return cli(['place-check', '--base', hex(base), '--guest-mib', guest, *[x for d in dumps or FIX['dumps'] for x in ('--dump', d)],
                    '--facts', facts or FIX['facts'], *extra])

    def test_the_bank_passes_every_map_and_what_fits_is_listed_without_choosing(self):
        code, text, _ = self.place(BANK, 384)
        self.assertEqual(code, 0, text)
        self.assertEqual([line.split()[-1] for line in text.splitlines() if line.startswith('MAP')], ['PASS'] * 3)
        self.assertIn(f'end={BANK + (384 + 48 + 240) * MIB:#x}', text)
        self.assertIn('RESULT maps=3/3', text)
        self.assertIn('PROPOSAL bank base=0x880000000 extent_mib=30720', text)
        self.assertIn('PROPOSAL low none', text)                # no extent is one descriptor in all three maps and holds 192 MiB + 48 + 240
        self.assertIn(f'PROPOSAL low base={LOW:#x} extent_mib=576', self.place(BANK, 384, dumps=FIX['dumps'][:2])[1])

    def test_a_placement_that_one_map_splits_is_refused_and_the_maps_that_passed_are_printed(self):
        code, text, _ = self.place(LOW, 256)
        self.assertEqual((code, refusal(text)), (1, 'REFUSED=PLACE_NOT_CONV'))
        self.assertEqual([line.split()[-1] for line in text.splitlines() if line.startswith('MAP')], ['PASS', 'PASS', 'REFUSED=PLACE_NOT_CONV'])
        self.assertIn('RESULT maps=2/3', text)

    def test_the_seeded_placement_mistakes_each_get_the_interface_name(self):
        gop = json.loads(EXAMPLE.read_text())['gop']
        for expected, base, guest, extra, facts in (
                ('PLACE_FB', BANK, 256, [], facts_file('fb.json', gop={**gop, 'base': hex(BANK + 0x1000000)})),
                ('PLACE_AVOID', BANK, 256, [], facts_file('avoid.json', avoid=[dict(base=BANK + 0x2000000, size=0x1000, tag='OPERATOR')])),
                ('PLACE_DECLARED', BANK, 256, ['--declared', f'{BANK + 0x3000000:#x}:0x10000'], None),
                ('PLACE_LIMIT', (1 << 36) - 512 * MIB, 256, [], None), ('PLACE_ALIGN', BANK + 0x1000000, 256, [], None), ('PLACE_LAYOUT', BANK, 160, [], None),
                ('PLACE_NOT_CONV', 0x80000000, 256, [], None)):
            with self.subTest(expected):
                self.refused(expected, self.place(base, guest, *extra, facts=facts))

    def test_a_framebuffer_must_be_known_and_the_end_may_touch_two_to_the_36(self):
        bare, _ = make_dump('sd-nosummary', bb.load_facts(EXAMPLE), memmap(), summary=False)
        self.refused('FB_UNKNOWN', cli(['place-check', '--base', hex(BANK), '--guest-mib', 256, '--dump', bare]))
        code, text, _ = self.place((1 << 36) - (256 + 48 + 240) * MIB, 256)
        self.assertEqual((code, text.count('PASS')), (0, 3), text)


# ---- The authorisation record ----
class Authorisation(Case):
    def test_the_record_is_deterministic_names_the_digests_and_states_what_it_is(self):
        text = template()[1]
        self.assertEqual(text, template()[1])
        self.assertTrue(text.isascii() and text.endswith('\n'))
        for needle in ('operator decision NOT RECORDED', 'Hardware: NOT RUN', 'names no person', 'SNAPMON.BIN', 'SNAPREP.BIN', 'GUEST.DAT', 'REPORT.DAT', 'PIN.DAT',
                       sha(Path(FIX['monitor']).read_bytes()), sha(Path(FIX['reporter']).read_bytes()), '   ram:', '   el2_sysreg:', 'Time box: 5 s', 'REFUSED line',
                       f'guest_base {BANK:#x}', f'work_size {sb.WORK_BYTES // MIB} MiB'):
            self.assertIn(needle, text)
        self.assertNotIn('   gic:', text)
        for prefix in MACHINE_PATHS[:3]:
            self.assertNotIn(prefix, text)

    def test_any_change_of_the_build_is_another_record(self):
        base = template()[1]
        for name, over in (('stores', dict(stores='ram,el2_sysreg,gic')), ('time box', dict(time_box_s=6)), ('placement', dict(base=hex(BANK + 0x2000000))),
                           ('image', dict(image_mib=9)), ('seed', dict(seed='other'))):
            self.assertNotEqual(template(**over)[1], base, name)
        self.assertIn('   gic:', template(stores='ram,el2_sysreg,gic')[1])

    def test_the_digest_is_required_nonzero_and_the_digest_of_this_builds_record(self):
        edited = write(SCRATCH / 'records/edited.txt', template()[1].encode() + b' ')
        for extra, expected in (([], 'NO_AUTHORISATION'), (['--authorisation-sha256', '0' * 64], 'NO_AUTHORISATION'), (['--authorisation-sha256', '1' * 64], 'AUTH_RECORD_MISMATCH'),
                                (['--authorisation-sha256', 'xyz'], 'OPTIONS_BAD'), (['--authorisation', edited], 'AUTH_RECORD_MISMATCH')):
            self.refused(expected, build_cli('auth', extra=['--no-manifest', *extra], approve=False))
        self.assertFalse((SCRATCH / 'out/auth').exists())
        digest = sha(template()[1].encode())
        code, text, err, out = build_cli('auth-hex', approve=False, extra=['--no-manifest', '--authorisation-sha256', digest])
        self.assertEqual(code, 0, (text, err))
        self.assertEqual((out / 'bundle/SP11/PIN.DAT').read_bytes()[0x60:0x80].hex(), digest)


# ---- A complete synthetic bundle ----
def scenario(files, facts, place):
    """What the wrapper would hold for these files: slots in LoaderData, the final map, registers equal to the pins."""
    s = H.Scn()
    s.args, s.stride, s.policy, s.report = (sb.RUNNER_MAGIC, 1, 192), 48, 3, H.valid_report()
    slots, pages = [0x84000000 + 0x2000000 * i for i in range(5)], [-(-len(files[n]) // 4096) for n in bb.NAMES]
    s.files = [[bytearray(b'\\SP11\\' + n.encode()).ljust(256, b'\0'), slots[i], len(files[n])] for i, n in enumerate(bb.NAMES)]
    s.declared = [[slots[i], pages[i] * 4096] for i in range(5)]
    descs = [(0, 0x80000000, 0x100, ATTR), *[(2, slots[i], pages[i], ATTR) for i in range(5)], (0, facts['fb_base'], 0x1518, ATTR),
             (7, place['place_base'] - 16 * MIB, (place['place_size'] + 32 * MIB) // 4096, ATTR)]
    s.map, s.pin, s.guest = bytearray(H.efi_map(sorted(descs, key=lambda d: d[1]))), bytearray(files['PIN.DAT']), bytearray(files['GUEST.DAT'])
    s.obs = dict(current_el=8, hcr_el2=0x88000010, sctlr_el2=0x100030C5183D, tcr_el2=0x80843514, midr_el1=facts['midr'][-1], mpidr_el1=facts['boot_mpidr'],
                 cntfrq_el0=facts['cntfrq_hz'], build_gicd_base=facts['gicd_base'], build_gicr_first=facts['gicr_first'], build_gicr_limit=facts['gicr_limit'],
                 build_gicr_stride=facts['gicr_stride'], build_timer_intid=facts['timer_intid'], build_stores=3,
                 **{k: facts[k] for k in ('fb_base', 'fb_bytes', 'fb_width', 'fb_height', 'fb_stride', 'fb_format')})
    return s


def read_bundle(out):
    return {n: (Path(out) / 'bundle/SP11' / n).read_bytes() for n in bb.NAMES}


class Build(Case):
    @needs_q1n1
    def test_a_complete_synthetic_bundle_with_a_stage_tree_that_the_stager_verifies(self):
        code, text, err, out = build_cli('small', extra=stage_args())
        self.assertEqual(code, 0, (text, err))
        files, tree = read_bundle(out), tree_digests(out / 'stage')
        self.assertEqual([len(files[n]) for n in ('PIN.DAT', 'REPORT.DAT')], [1024, sb.REPORT_BYTES])
        self.assertEqual(files['REPORT.DAT'], bytes(sb.REPORT_BYTES))
        self.assertEqual((files['SNAPMON.BIN'], files['SNAPREP.BIN']), (Path(FIX['monitor']).read_bytes(), Path(FIX['reporter']).read_bytes()))
        self.assertEqual({line.split()[1]: line.split()[-1] for line in text.splitlines() if line.startswith('FILE')},
                         {n: sha(files[n]) for n in bb.NAMES} | {'PAYLOAD.MAN': sha((out / 'bundle/SP11/PAYLOAD.MAN').read_bytes())})
        self.assertIn(f'STAGE {len(tree)} files, COMPLETE, shell_pin unchecked', text)
        found = bb.stager().cmd_verify(argparse.Namespace(dest=out / 'stage', pins=None, no_pins=True))
        self.assertEqual((found['status'], found['autorun']['steps'], found['autorun']['problems'], found['startup_nsh_found']), ('COMPLETE', 1, [], []))
        self.assertTrue(all(f'SP11/{n}' in tree for n in (*bb.NAMES, 'PAYLOAD.MAN', 'PLAN.TXT', bb.SLBOUNCE, bb.RUNNER)) and 'EFI/BOOT/startup.nsh' in tree)
        self.assertEqual({tree[f'SP11/{n}'] for n in bb.NAMES}, {sha(files[n]) for n in bb.NAMES})
        listed = dict(reversed(line.split('  ', 1)) for line in (out / 'STICK-FILES.TXT').read_text().splitlines())
        self.assertEqual(listed, tree)
        entries = load('sp11_autorun_unit', TOOLS / 'sp11_autorun.py').parse_payload_manifest((out / 'stage/SP11/PAYLOAD.MAN').read_bytes())
        self.assertEqual([(e.path, e.size, e.sha256) for e in entries], [(f'\\SP11\\{n}', len(files[n]), sha(files[n])) for n in bb.NAMES])
        report = json.loads((out / bb.REPORT_NAME).read_text())
        self.assertEqual((report['hardware'], report['evidence'], report['stage']['status'], [m['result'] for m in report['maps']]), ('NOT RUN', 'EXPERIMENTAL', 'COMPLETE', ['OK'] * 3))
        for path in (out / bb.REPORT_NAME, out / 'STICK-FILES.TXT', out / 'stage/SP11/PLAN.TXT'):
            self.assertNotIn(MACHINE_PATHS[0], path.read_text())

    def test_every_file_passes_the_interface_checks_in_python_and_in_c_for_the_bank_and_a_low_placement(self):
        f = bb.load_facts(FIX['facts'])
        for name, base in (('bank', BANK), ('low', LOW)):
            code, text, err, out = build_cli(f'c-{name}', extra=['--no-manifest'], base=hex(base), dump=FIX['dumps'][:2])      # the third map splits the low extent
            self.assertEqual(code, 0, (text, err))
            files, place = read_bundle(out), sb.placement(base, 256 * MIB, 48 * MIB)
            s = scenario(files, f, place)
            self.assertEqual(H.py_run(s, 'preflight'), (0, None), name)
            self.assertEqual(H.run_harness(['batch'], s.text(name, 'preflight')).split()[2:4], ['0', 'OK'], name)
            self.assertEqual(H.run_harness(['batch'], s.text(name, 'params')).split()[-1], str(base + 0xC000))
            head, table = sb.unpack_guest(files['GUEST.DAT'])
            self.assertEqual((sb.params_dest(files['GUEST.DAT']), head['load_base'], head['load_size']), (base + 0xC000, base, 256 * MIB))
            self.assertEqual({c['kind'] for c in table}, set(range(1, 8)))
            self.assertTrue({True, False} <= {bool(c['flags'] & sb.CHUNK_ZERO) for c in table})
            params = [c for c in table if c['kind'] == sb.KIND_PARAMS][0]
            self.assertEqual(struct.unpack_from('<2Q', files['GUEST.DAT'], params['file_offset'] + 8 * ch.PARAMS_MEM_LOW), (base, base + (256 + 48) * MIB))

    def test_the_same_inputs_make_the_same_bytes(self):
        record, trees = write(SCRATCH / 'records/det.txt', template()[1].encode()), []
        staged = stage_args() if bb.q1n1_tree() else ['--no-manifest']
        for i in (1, 2):
            self.assertEqual(cli(['synthetic', *opts(), '--out', SCRATCH / f'out/det{i}', *staged, '--authorisation', record])[0], 0)
            trees.append(tree_digests(SCRATCH / f'out/det{i}'))
        self.assertEqual(trees[0], trees[1])
        self.assertIn('bundle/SP11/GUEST.DAT', trees[0])
        self.assertEqual('stage/SP11/GUEST.DAT' in trees[0], bool(bb.q1n1_tree()))

    def test_the_output_directory_is_confined_and_nothing_is_replaced_by_a_refused_build(self):
        run = lambda out, *extra: cli(['synthetic', *opts(), '--out', out, '--no-manifest', '--authorisation', SCRATCH / 'records/c.txt', *extra])   # noqa: E731
        write(SCRATCH / 'records/c.txt', template()[1].encode())
        for out in (ROOT / 'tools/not-build', SCRATCH / '../escape', '/Volumes/SP11BOOT/x', ROOT.parent / 'elsewhere'):
            self.refused('OUT_DIR_REFUSED', run(out))
        self.assertFalse((ROOT / 'tools/not-build').exists() or (SCRATCH.parent / 'escape').exists() or (ROOT.parent / 'elsewhere').exists())
        self.assertEqual(run(SCRATCH / 'out/conf')[0], 0)
        self.refused('OUT_NOT_EMPTY', run(SCRATCH / 'out/conf'))
        kept = (SCRATCH / 'out/conf/bundle/SP11/GUEST.DAT').read_bytes()
        self.refused('FILE_SIZE', cli(['synthetic', *opts(monitor=self.sparse(sb.FILE_MAX + 1)), '--out', SCRATCH / 'out/conf', '--force', '--no-manifest', '--authorisation', 'x']))
        self.assertEqual((SCRATCH / 'out/conf/bundle/SP11/GUEST.DAT').read_bytes(), kept, 'a refused --force build left the earlier one alone')
        self.assertEqual(run(SCRATCH / 'out/conf', '--force')[0], 0)
        write(SCRATCH / 'out/foreign/file.txt', b'x')
        self.refused('OUT_NOT_EMPTY', run(SCRATCH / 'out/foreign', '--force'))
        self.assertTrue((SCRATCH / 'out/foreign/file.txt').exists())
        self.assertEqual(run(ROOT.parent / 'allowed', '--allow-under', ROOT.parent / 'allowed')[0], 0)
        shutil.rmtree(ROOT.parent / 'allowed')
        self.raises('OUT_DIR_REFUSED', bb.put, SCRATCH / 'out/conf', '../evil', b'x')

    def sparse(self, size):
        path = SCRATCH / f'in/sparse-{size}.bin'
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'wb') as handle:
            handle.truncate(size)
        return path

    def test_the_manifest_needs_the_q1n1_tree_and_a_bundle_without_it_does_not(self):
        with mock.patch.dict(os.environ, {'Q1N1_SP11_TREE': str(SCRATCH / 'no-such-checkout')}):
            self.refused('Q1N1_TREE_ABSENT', build_cli('absent'))
            self.assertFalse((SCRATCH / 'out/absent').exists())
            self.assertEqual(build_cli('nomanifest', extra=['--no-manifest'])[0], 0)
            self.refused('OPTIONS_BAD', build_cli('conflict', extra=['--no-manifest', *stage_args()]))
        with mock.patch.dict(os.environ, {'Q1N1_SP11_TREE': ''}):
            self.assertIsNone(bb.q1n1_tree())

    @needs_q1n1
    def test_a_stage_tree_that_fails_the_stagers_verify_is_refused_and_leaves_nothing(self):
        shutil.copytree(SCRATCH / 'base', SCRATCH / 'tampered')
        (SCRATCH / 'tampered/TCBLAUNCH.EXE').write_bytes(b'not what the manifest lists')
        self.refused('STAGE_VERIFY_FAILED', build_cli('tampered', extra=stage_args(SCRATCH / 'tampered')))
        real, calls = bb.stager(), []

        class Proxy:                    # the stager, but its second verify (after the autorun) reports FAIL
            def __getattr__(self, name):
                return getattr(real, name)

            def cmd_verify(self, args):
                calls.append(1)
                return real.cmd_verify(args) if len(calls) == 1 else {'status': 'FAIL', 'manifest_mismatches': ['seeded'], 'shell_pin': 'unchecked'}
        with mock.patch.object(bb, 'stager', Proxy):
            self.refused('STAGE_VERIFY_FAILED', build_cli('final-verify', extra=stage_args()))
        self.refused('OPTIONS_BAD', build_cli('onefile', extra=['--base-tree', SCRATCH / 'base', '--no-pins', '--file', *FIX['efi'][0]]))
        wrong = [[*FIX['efi'][0][:2], '0' * 64], FIX['efi'][1]]        # a SHA-256 that is not the file's: the stager refuses the plan
        self.refused('STAGE_REFUSED', build_cli('plan', extra=['--base-tree', SCRATCH / 'base', '--no-pins', *[x for f in wrong for x in ('--file', *f)]]))
        self.assertEqual([(SCRATCH / 'out' / n).exists() for n in ('tampered', 'final-verify', 'onefile', 'plan')], [False] * 4)

    def test_the_decisions_are_arguments_without_defaults(self):
        sub = bb.build_parser()._subparsers._group_actions[0].choices
        for kind in ('synthetic', 'real'):
            actions = {a.dest: a for a in sub[kind]._actions}
            for dest in ('base', 'guest_mib', 'stores', 'time_box_s', 'facts', 'monitor', 'reporter', 'out'):
                self.assertTrue(actions[dest].required, (kind, dest))
        self.assertTrue(all({a.dest: a for a in sub['real']._actions}[d].required for d in ('panel', 'sptm_hvc_calls', 'kernelcache')))
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            bb.build_parser().parse_args(['synthetic', *opts(base=None), '--out', 'x'])

    def test_the_seeded_mistakes_of_the_inputs_each_get_a_stable_name(self):
        facts = facts_file('wrong.json', gicd_base='0x17010000')
        for expected, over in (
                ('FACTS_DISAGREE', dict(facts=facts)), ('PLACE_NOT_CONV', dict(base=hex(LOW))), ('PLACE_LAYOUT', dict(guest_mib=192)),
                ('PLACE_ALIGN', dict(base=hex(BANK + 0x1000000))), ('PLACE_LIMIT', dict(base=hex((1 << 36) - 512 * MIB))), ('CHUNK_RANGE', dict(image_mib=116)),
                ('OPTIONS_BAD', dict(time_box_s=0)), ('OPTIONS_BAD', dict(stores='ram,disk')), ('STORE_NOT_AUTH', dict(stores='ram')),
                ('FILE_SIZE', dict(monitor=self.sparse(sb.FILE_MAX + 1))), ('PAYLOAD_BAD', dict(reporter=write(SCRATCH / 'in/empty.bin', b''))),
                ('PLACE_DECLARED', dict(declared=f'{BANK + 0x100000:#x}:0x1000')), ('PLACE_AVOID', dict(facts=facts_file('avoid2.json', avoid=[dict(base=BANK, size=0x1000, tag=3)]))),
                ('DUMP_INCOMPLETE', dict(dump=[str(SCRATCH / 'nowhere')]))):
            with self.subTest(expected, over=list(over)):
                self.refused(expected, template(**over))
        self.assertEqual(template(base=hex(LOW), guest_mib=256, dump=FIX['dumps'][:2])[0], 0)       # the other option of D1 builds too

    def test_the_real_path_refuses_what_it_cannot_do_here(self):
        board = TBD.synthetic()
        board['soc']['midr'] = [{'value': '0x510F0010', 'mask': '0xff0ffff0'}]
        board['timer']['ppi'] = {'secure_phys': 29, 'phys': 30, 'virt': 27, 'hyp': 26}
        write(SCRATCH / 'in/board.json', json.dumps(board).encode())
        write(SCRATCH / 'in/wrong.json', json.dumps({**board, 'timer': {'frequency_hz': 24000000, 'ppi': board['timer']['ppi']}}).encode())
        real = dict(kernelcache=SCRATCH / 'in/none.kc', panel='withheld', sptm_hvc_calls=0)
        ready = dict(real, descriptor=SCRATCH / 'in/board.json')
        self.refused('DESCRIPTOR_NOT_READY', template('real', **real))
        self.refused('DESCRIPTOR_NOT_READY', template('real', **dict(real, descriptor=ROOT / 'config/boards/sp11.json')))
        self.refused('FACTS_DISAGREE', template('real', **dict(real, descriptor=SCRATCH / 'in/wrong.json')))
        self.refused('KERNELCACHE_MISSING', template('real', **ready))
        with mock.patch.dict(os.environ, {'SNAP_NO_FB': '1'}):
            self.refused('ENV_SNAP_SET', template('real', **ready))
        code, text, err, out = build_cli('synth-desc', extra=['--no-manifest'], descriptor=SCRATCH / 'in/board.json')
        self.assertEqual(code, 0, (text, err))
        self.assertEqual(json.loads((out / bb.REPORT_NAME).read_text())['descriptor'], 'boot-ready')

    @unittest.skipIf(os.environ.get('SP11_BUILD_SKIP_SLOW'), 'SP11_BUILD_SKIP_SLOW is set')
    @needs_q1n1
    def test_the_default_realistic_sizes_build_verify_and_are_deleted(self):
        code, text, err, out = build_cli('default', extra=stage_args(), image_mib=115.5, tables_mib=16, sptm_data_mib=5)
        try:
            self.assertEqual(code, 0, (text, err))
            files = read_bundle(out)
            self.assertTrue(130 * MIB < len(files['GUEST.DAT']) < 150 * MIB, len(files['GUEST.DAT']))
            self.assertTrue(10 < len(sb.unpack_guest(files['GUEST.DAT'])[1]) < 200)
            self.assertEqual((sb.check_guest(files['GUEST.DAT'], files['PIN.DAT']), sb.check_digests(files['GUEST.DAT'], files['PIN.DAT'], 3)), (0, (0, None)))
            self.assertEqual(json.loads((out / bb.REPORT_NAME).read_text())['stage']['status'], 'COMPLETE')
        finally:
            shutil.rmtree(out, ignore_errors=True)
        self.assertFalse(out.exists())


# ---- build()'s chunks: classify, flatten, pad, split, pack ----
_LAID = {}


def laid_out(base=BANK, guest=256):
    if (base, guest) not in _LAID:
        xb = bb.xnuboot()
        xb.place(base, (guest + 48) * MIB, guest_span=guest * MIB)
        _LAID[base, guest] = (xb, ch.layout_of(xb), ch.synthetic_built(xb, 2 * MIB, MIB, 4 * MIB, 's', 5), sb.placement(base, guest * MIB, 48 * MIB))
    return _LAID[base, guest]


def memory(writes, lo, hi):
    """(bytes, written mask) of ascending-address writes [(pa, bytes)] onto a flat region: the model of the A16 loader's upload."""
    mem, done = bytearray(hi - lo), bytearray(hi - lo)
    for pa, blob in sorted(writes):
        mem[pa - lo:pa - lo + len(blob)] = blob
        done[pa - lo:pa - lo + len(blob)] = b'\1' * len(blob)
    return mem, done


class Chunks(Case):
    def test_every_chunk_of_a_build_shaped_dict_is_classified_by_its_reservation(self):
        for base, expected_end in ((BANK, 0xED00000), (LOW, 0xA700000)):
            xb, lay, built, place = laid_out(base, 256)
            kinds = {pa: ch.kind_of(lay, pa, len(b), False) for pa, b in built['chunks'].items()}
            in_class = lambda i: {k for pa, k in kinds.items() if lay.classes[i][0] <= pa < lay.classes[i][1]}     # noqa: E731
            self.assertEqual((kinds[lay.params_pa], kinds[lay.devicetree_pa], kinds[lay.handoff_pa]), (sb.KIND_PARAMS, sb.KIND_DEVICE_TREE, sb.KIND_HANDOFF))
            self.assertEqual((in_class(1), in_class(2)), ({sb.KIND_KERNEL}, {sb.KIND_MONITOR_IMAGE}))
            self.assertEqual(set(kinds.values()), {1, 3, 4, 5, 6, 7})
            self.assertTrue(all(ch.kind_of(lay, pa, 0x4000, True) == sb.KIND_TABLES for pa, _ in built['tables'].chunks()))
            self.assertEqual([c[2] for c in lay.classes], ['handoff', 'image', 'monitors', 'frame table', 'frame map', 'page tables', 'sptm data'])
            self.assertEqual((lay.params_pa, lay.classes[-1][1] - base), (base + 0xC000, expected_end))

    def test_flattening_leaves_exactly_the_memory_the_ascending_upload_leaves(self):
        for base in (BANK, LOW):
            xb, lay, built, place = laid_out(base, 256)
            chunks = ch.chunks_from_built(built, lay)
            lo, hi = lay.phys_base, lay.phys_base + 0x10000000
            want, wrote = memory([(pa, bytes(b)) for pa, b in [*built['chunks'].items(), *built['tables'].chunks()]], lo, hi)
            got, got_wrote = memory([(c['dest'], bytes(c['size']) if 'size' in c else c['data']) for c in chunks], lo, hi)
            self.assertTrue(all(g == w for g, w, d in zip(got, want, wrote) if d), 'a written byte differs')
            self.assertTrue(all(not g for g, d, e in zip(got, wrote, got_wrote) if e and not d), 'a byte nobody wrote was invented')
            self.assertLess(sum(got_wrote) - sum(wrote), 16 * len(chunks))              # only the pad to 16
            self.assertEqual([c['dest'] for c in chunks], sorted(c['dest'] for c in chunks))
            self.assertTrue(all(c['dest'] % 16 == 0 and len(c['data'] if 'data' in c else bytes(c['size'])) % 16 == 0 for c in chunks))
            self.assertLess(len(chunks), 60, 'adjacent zero pages and table pages are coalesced')
            self.assertEqual(sb.params_dest(ch.guest_from_chunks(place, chunks)), lay.params_pa)

    def test_random_overlaps_resolve_as_the_ascending_upload_resolves_them(self):
        rng, h = random.Random(7), laid_out()[1].handoff_pa
        for trial in range(40):
            writes = {h + 16 * rng.randrange(0, 0x400): bytes(rng.randrange(1, 256) if rng.random() < 0.7 else 0 for _ in range(16 * rng.randrange(1, 0x40)))
                      for _ in range(rng.randrange(2, 9))}
            runs = ch.flatten({pa: (1, memoryview(b)) for pa, b in writes.items()})
            self.assertEqual([bytes(x) for x in memory(list(writes.items()), h, h + 0x4800)], [bytes(x) for x in memory([(s, d) for s, _, d in runs], h, h + 0x4800)], trial)
        self.assertEqual(ch.flatten({0x1000: (1, memoryview(b'a' * 32)), 0x1008: (1, memoryview(b'b' * 8))}), [(0x1000, 1, b'a' * 8 + b'b' * 8 + b'a' * 16)])
        self.assertEqual(ch.flatten({0x1000: (1, memoryview(b'a' * 32)), 0x1010: (1, memoryview(b'b' * 32))}), [(0x1000, 1, b'a' * 16 + b'b' * 32)])

    def test_padding_and_zero_splitting(self):
        self.assertEqual(ch.pad_runs([(0x1000, 1, b'x' * 20)]), [(0x1000, 1, b'x' * 20 + bytes(12))])
        self.raises('BUILT_SHAPE', ch.pad_runs, [(0x1009, 1, b'x' * 16)])
        data = b'a' * 0x100 + bytes(ch.ZERO_MIN + 7) + b'b' * 0x101
        parts = ch.split_zero(sb.KIND_KERNEL, 0x4000, data + bytes(-len(data) % 16))
        self.assertEqual([('size' in c, c['dest'] - 0x4000) for c in parts], [(False, 0), (True, 0x100), (False, 0x100 + ch.ZERO_MIN + 0)])
        self.assertEqual(ch.split_zero(sb.KIND_HANDOFF, 0, bytes(0x4000)), [dict(kind=sb.KIND_HANDOFF, dest=0, size=0x4000)])
        self.assertEqual(ch.split_zero(sb.KIND_PARAMS, 0, bytes(0x40))[0].get('data'), bytes(0x40))
        self.assertEqual(len(ch.split_zero(sb.KIND_KERNEL, 0, bytes(ch.ZERO_MIN - 16) + b'x' * 16)), 1)

    def test_a_shape_that_does_not_look_like_build_is_refused_not_guessed(self):
        xb, lay, built, place = laid_out()
        tables, page = lay.classes[5][0], lambda n: ch.SyntheticTables({lay.classes[5][0]: bytearray(n)})        # noqa: E731
        with_chunk = lambda pa, blob: {**built, 'chunks': {**built['chunks'], pa: blob}}        # noqa: E731
        mutations = {
            'not a dict': ['chunks'], 'no tables': {k: v for k, v in built.items() if k != 'tables'}, 'other params_pa': {**built, 'params_pa': 0x1000},
            'a chunk below the region': with_chunk(lay.phys_base - 0x1000, b'x' * 16), 'a chunk in the bootstrap reserve': with_chunk(lay.bootstrap_pa, b'x' * 16),
            'a chunk across two reservations': with_chunk(lay.classes[1][1] - 0x10, b'x' * 0x40), 'a table page in chunks': with_chunk(tables + 0x100000, bytes(0x4000)),
            'an empty chunk': with_chunk(lay.handoff_pa + 0x20000, b''), 'a str chunk': with_chunk(lay.handoff_pa + 0x20000, 'x'),
            'an unaligned start': with_chunk(lay.handoff_pa + 0x20008, b'x' * 16), 'kinds overlap': with_chunk(lay.params_pa - 0x100, b'x' * 0x200),
            'a short table page': {**built, 'tables': page(0x2000)}, 'an unaligned table page': {**built, 'tables': ch.SyntheticTables({tables + 0x1000: bytearray(0x4000)})},
            'no chunks at all': {**built, 'chunks': {}, 'tables': ch.SyntheticTables({})},
            'a table page that is also a chunk': {**built, 'tables': page(0x4000), 'chunks': {**built['chunks'], tables: bytes(0x4000)}}}
        for name, mutated in mutations.items():
            with self.subTest(name):
                self.raises('BUILT_SHAPE', ch.chunks_from_built, mutated, lay)
        new = dataclasses.replace(lay, classes=(*lay.classes[:-1], (*lay.classes[-1][:2], 'something new')))
        self.raises('BUILT_SHAPE', ch.chunks_from_built, built, new)

    def test_the_seeded_chunk_set_mistakes_each_get_the_interface_name(self):
        xb, lay, built, place = laid_out()
        chunks = ch.chunks_from_built(built, lay)
        for kind in (sb.KIND_KERNEL, sb.KIND_TABLES, sb.KIND_HANDOFF, sb.KIND_DEVICE_TREE, sb.KIND_PARAMS):
            self.raises('CHUNK_MISSING', ch.guest_from_chunks, place, [c for c in chunks if c['kind'] != kind])
        first = lambda kind: next(c for c in chunks if c['kind'] == kind)       # noqa: E731
        for kind, other in ((sb.KIND_DEVICE_TREE, sb.KIND_PARAMS), (sb.KIND_PARAMS, sb.KIND_DEVICE_TREE)):
            self.raises('CHUNK_DUP', ch.guest_from_chunks, place, [dict(c, kind=other) if c is first(kind) else c for c in chunks])
        past = [*chunks[:-1], dict(chunks[-1], dest=BANK + 256 * MIB)]
        self.raises('CHUNK_RANGE', ch.guest_from_chunks, place, past)
        self.raises('CHUNK_RANGE', ch.guest_from_chunks, sb.placement(BANK, 64 * MIB), chunks)       # an image that does not fit the guest span
        self.raises('CHUNK_OVERLAP', ch.guest_from_chunks, place, [chunks[0], dict(chunks[1], dest=chunks[0]['dest'] + 16), *chunks[2:]])
        self.raises('GUEST_COUNT', ch.guest_from_chunks, place, [])
        self.raises('BUILT_SHAPE', ch.guest_from_chunks, place, [dict(chunks[0], dest=-1)])

    def test_params_must_say_the_placement_and_the_time_box(self):
        xb, lay, built, place = laid_out()
        chunks = ch.chunks_from_built(built, lay)
        ch.check_params(chunks, place, 5)
        self.raises('TIME_BOX_MISMATCH', ch.check_params, chunks, place, 6)
        for what, other in (('base', sb.placement(BANK + 0x2000000, 256 * MIB, 48 * MIB)), ('size', sb.placement(BANK, 224 * MIB, 48 * MIB)),
                            ('reserve', sb.placement(BANK, 256 * MIB, 64 * MIB))):
            with self.subTest(what):
                self.raises('PARAMS_MISMATCH', ch.check_params, chunks, other, 5)
        xb2, lay2, built2, _ = laid_out(LOW, 256)
        self.raises('PARAMS_MISMATCH', ch.check_params, ch.chunks_from_built(built2, lay2), place, 5)         # a build made for another base
        self.raises('BUILT_SHAPE', ch.check_params, [c for c in chunks if c['kind'] != sb.KIND_PARAMS], place, 5)
        self.raises('BUILT_SHAPE', ch.check_params, [dict(c, data=c['data'][:100]) if c['kind'] == sb.KIND_PARAMS else c for c in chunks], place, 5)

    def test_the_params_word_indexes_are_the_c_structs(self):
        text = re.sub(r'/\*.*?\*/', '', (ROOT / 'probes/xnuboot/report_layout.c').read_text(), flags=re.S)
        index, words = {}, 0
        for decl in re.finditer(r'uint64_t\s+([^;]*);', text[text.index('struct params {'):]):
            for name in decl.group(1).split(','):
                index[name.split('[')[0].strip()] = words
                words += int(re.search(r'\[(\d+)\]', name).group(1)) if '[' in name else 1
            if 'mem_high' in index:
                break
        self.assertEqual((index['watchdog_seconds'], index['mem_low'], index['mem_high']), (ch.PARAMS_WATCHDOG, ch.PARAMS_MEM_LOW, ch.PARAMS_MEM_HIGH))
        xb = bb.xnuboot()
        self.assertEqual(ch.PARAMS_WORDS, xb.PARAMS_WORDS)
        self.assertIn(f'#define PARAMS_WORDS {ch.PARAMS_WORDS}', (ROOT / 'probes/xnuboot/report_layout.c').read_text())

    def test_the_filler_is_a_reproducible_sha256_counter_stream(self):
        self.assertEqual((ch.stream('x', 70)[:32], ch.stream('x', 33) == ch.stream('y', 33)), (hashlib.sha256(b'x:' + bytes(8)).digest(), False))
        self.assertEqual(ch.stream('x', 70), ch.stream('x', 100)[:70])


# ---- Library-level refusals ----
class LibraryRefusals(Case):
    def files(self, **over):
        return {**dict(zip(bb.NAMES, (b'm' * 8, b'r' * 8, b'g' * 8, b'p' * 8, bytes(sb.REPORT_BYTES)))), **over}

    def test_more_than_five_files_a_file_over_the_cap_and_a_report_of_the_wrong_size(self):
        named = [(n, 100) for n in bb.NAMES[:4]] + [('REPORT.DAT', sb.REPORT_BYTES)]
        bb.check_bundle_files(named)
        bb.check_bundle_files([*named[:2], ('GUEST.DAT', sb.FILE_MAX), *named[3:]])
        for expected, files in (('FILE_COUNT', named + [('EXTRA.DAT', 1)]), ('FILE_COUNT', named[:4]), ('FILE_SIZE', [*named[:2], ('GUEST.DAT', sb.FILE_MAX + 1), *named[3:]]),
                                ('FILE_SIZE', [*named[:4], ('REPORT.DAT', 0x80000)]), ('FILE_SIZE', [*named[:2], ('GUEST.DAT', 0), *named[3:]]),
                                ('FILE_ROLE', [named[1], named[0], *named[2:]])):
            self.raises(expected, bb.check_bundle_files, files)
        self.raises('FILE_SIZE', bb.verify_files, self.files(**{'REPORT.DAT': bytes(0x80000)}), {})
        self.raises('FILE_COUNT', bb.verify_files, self.files(**{'EXTRA.DAT': b'x'}), {})
        self.raises('REPORT_NOT_ZERO', bb.verify_files, self.files(**{'REPORT.DAT': bytes(sb.REPORT_BYTES - 1) + b'\1'}), {})

    def test_the_refusal_names_are_panel_text_and_do_not_collide_with_the_interface(self):
        names = list(bb.BUILDER_REFUSALS)
        self.assertEqual(len(set(names)), len(names))
        self.assertTrue(all(re.fullmatch(r'[A-Z0-9_]{1,24}', n) for n in names))
        self.assertFalse(set(names) & {n for _, n, _ in sb.ERRORS})
        self.assertIs(bb.Refused, ch.Refused)
        with self.assertRaises(AssertionError):
            ch.Refused('NOT_A_NAME')

class Hygiene(Case):
    def test_licence_sizes_and_no_machine_text(self):
        for path in (*MODULES, THIS, EXAMPLE, DOC):
            text = path.read_text()
            self.assertTrue(text.isascii(), path.name)
            self.assertIn('SPDX-License-Identifier: MIT', text[:200] if path.suffix != '.md' else text[:100], path.name)
            self.assertLess(len(text.splitlines()), 1500, path.name)
            self.assertLess(len(text.encode()), 30000 if path == DOC else 50 * 1024, path.name)
            for prefix in MACHINE_PATHS:
                self.assertNotIn(prefix, text, f'{path.name}: {prefix}')
            self.assertIsNone(re.search(r'(?i)\b([0-9a-f]{2}:){5}[0-9a-f]{2}\b', text), path.name)

    def test_the_modules_call_the_interface_for_packing_and_checks(self):
        for path in MODULES:
            self.assertNotRegex(path.read_text().replace('def pack_pin(fields, check=True)', ''), r'def (check_pin|pack_guest|pack_pin|check_place)\b|struct\.pack_into')


class Document(Case):
    def test_the_note_is_labelled_and_names_every_subcommand_and_every_builder_refusal(self):
        text = DOC.read_text()
        self.assertTrue(text.startswith('<!-- SPDX-License-Identifier: MIT -->\n'))
        self.assertIn('hardware NOT RUN', text[:900])
        for needle in ('facts-check', 'place-check', 'synthetic', 'authorisation-template', 'STICK-FILES.TXT', 'Q1N1_SP11_TREE', 'diskutil', 'sp11-facts.example.json', 'D1', 'D9',
                       'NOT RUN', 'UNIMPLEMENTED', 'sp11_stick_bundle_chunks.py', 'bootcontract', 'SP11_BUILD_SKIP_SLOW'):
            self.assertIn(needle, text)
        for name in bb.BUILDER_REFUSALS:
            self.assertIn(f'`{name}`', text, name)
        mp = load('sp11_monitor_port_tests', TOOLS / 'test-sp11-monitor-port.py')
        self.assertEqual((mp.check_tables(text), mp.check_labels(text)), ([], []))


if __name__ == '__main__':
    unittest.main()
