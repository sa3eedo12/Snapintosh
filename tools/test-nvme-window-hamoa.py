#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Host tests for the SP11 NVMe window: GPT layout fixtures, window model, takeover contract, admin allow-list.

    python3 tools/test-nvme-window-hamoa.py

Nothing here touches hardware, a block device or the network. The C safety logic (compat/nvme-host.c under
-DSNAP_PLATFORM_SP11=1) is built for the host with ASan and UBSan (tools/test-nvme-window-hamoa.c) and driven with the
very inputs the Python host model (tools/fixtures/sp11/nvmewindow.py, gptlayout.py) sees; a verdict that differs fails
the test, and so does a sanitizer report. Expected verdicts are written out as tables in nvmewindow.py, so the two
implementations cannot agree on a wrong answer unnoticed. Without clang the native classes report SKIP, never PASS.
Every layout, GUID, address and identity is synthetic. The code and the model are EXPERIMENTAL (host-tested only).
"""
import ast
import copy
import importlib.util
import json
from pathlib import Path
import random
import re
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools' / 'fixtures' / 'sp11'))
from nvmewindow import *                    # noqa: E402,F401,F403  (the model, the tables and the native bridge)
import nvmewindow as nw                     # noqa: E402


def _descriptor_fixtures():
    spec = importlib.util.spec_from_file_location('board_descriptor_fixtures', ROOT / 'tools' / 'test-board-descriptor.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def window_only(edits):
    """True when the edits change nothing but the window's first and last LBA."""
    case, base = edited(*edits), base_case()
    return case['t'] == base['t'] and case['r'] == base['r'] and all(
        case['p'][k] == base['p'][k] for k in base['p'] if k not in ('first_lba', 'last_lba'))


def layout_doc(name='gpt-512-shrunk.json'):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


def refuse(edit):
    doc = layout_doc()
    edit(doc)
    return doc


def part(doc, i=0):
    return doc['partitions'][i]


MALFORMED = (
    ('unknown top-level key', lambda d: d.update(extra=1)), ('missing partitions', lambda d: d.pop('partitions')),
    ('missing block size', lambda d: d.pop('block_size')), ('another schema name', lambda d: d.update(schema='gpt')),
    ('unknown schema version', lambda d: d.update(schema_version=2)),
    ('boolean schema version', lambda d: d.update(schema_version=True)),
    ('block size 1024', lambda d: d.update(block_size=1024)), ('block size as text', lambda d: d.update(block_size='512')),
    ('float last block', lambda d: d.update(last_block=1953525167.0)), ('negative last block', lambda d: d.update(last_block=-1)),
    ('boolean last block', lambda d: d.update(last_block=True)),
    ('boolean header LBA that equals 1', lambda d: d['primary'].update(my_lba=True)),
    ('boolean alternate LBA that equals 1', lambda d: d['backup'].update(alternate_lba=True)),
    ('boolean partition index that equals 1', lambda d: part(d, 1).update(index=True)),
    ('boolean attributes that equal 0', lambda d: part(d, 2).update(attributes=False)),
    ('last block beyond 63 bits', lambda d: d.update(last_block=1 << 63)),
    ('usable range reversed', lambda d: d.update(first_usable=d['last_usable'] + 1)),
    ('last usable beyond the disk', lambda d: d.update(last_usable=d['last_block'] + 1)),
    ('first usable inside the primary array', lambda d: d.update(first_usable=10)),
    ('last usable inside the backup array', lambda d: d.update(last_usable=d['last_block'] - 10)),
    ('primary header points elsewhere', lambda d: d['primary'].update(alternate_lba=5)),
    ('primary header not at LBA 1', lambda d: d['primary'].update(my_lba=2)),
    ('backup header not at the last LBA', lambda d: d['backup'].update(my_lba=d['last_block'] - 1)),
    ('backup header points elsewhere', lambda d: d['backup'].update(alternate_lba=2)),
    ('entry size not 128 * 2**n', lambda d: [d[h].update(entry_size=192) for h in ('primary', 'backup')]),
    ('entry array under 16 KiB', lambda d: [d[h].update(count=64) for h in ('primary', 'backup')]),
    ('entry count zero', lambda d: [d[h].update(count=0) for h in ('primary', 'backup')]),
    ('headers disagree on the count', lambda d: d['backup'].update(count=256)),
    ('primary array below LBA 2', lambda d: d['primary'].update(entries_lba=1)),
    ('backup array reaches the header', lambda d: d['backup'].update(entries_lba=d['last_block'])),
    ('header with an extra key', lambda d: d['primary'].update(revision=1)),
    ('header not an object', lambda d: d.update(primary=[])),
    ('partitions not a list', lambda d: d.update(partitions={})),
    ('partition not an object', lambda d: d['partitions'].append(7)),
    ('partition with an unknown key', lambda d: part(d).update(flags=1)),
    ('partition missing its GUID', lambda d: part(d).pop('guid')),
    ('duplicate partition index', lambda d: part(d, 1).update(index=0)),
    ('index beyond the array', lambda d: part(d).update(index=128)), ('negative index', lambda d: part(d).update(index=-1)),
    ('duplicate partition GUID', lambda d: part(d, 1).update(guid=part(d)['guid'])),
    ('all-zero type GUID', lambda d: part(d).update(type='00000000-0000-0000-0000-000000000000')),
    ('upper-case GUID', lambda d: part(d).update(guid=part(d)['guid'].upper())),
    ('braced GUID', lambda d: part(d).update(guid='{' + part(d)['guid'] + '}')),
    ('GUID not text', lambda d: part(d).update(guid=5)),
    ('GUID of the wrong length', lambda d: part(d).update(guid=part(d)['guid'][:-1])),
    ('first after last', lambda d: part(d).update(first_lba=part(d)['last_lba'] + 1)),
    ('partition below the usable range', lambda d: part(d).update(first_lba=10)),
    ('partition beyond the usable range', lambda d: part(d, 3).update(last_lba=d['last_usable'] + 1)),
    ('overlapping partitions', lambda d: part(d, 1).update(first_lba=part(d)['last_lba'])),
    ('float LBA', lambda d: part(d).update(first_lba=2048.0)), ('LBA as text', lambda d: part(d).update(last_lba='534527')),
    ('negative attributes', lambda d: part(d).update(attributes=-1)),
    ('attributes beyond 64 bits', lambda d: part(d).update(attributes=1 << 64)),
    ('name of 37 characters', lambda d: part(d).update(name='x' * 37)),
    ('name with NUL', lambda d: part(d).update(name='a\0b')), ('name not text', lambda d: part(d).update(name=None)),
    ('type_name not text', lambda d: part(d).update(type_name=3)),
    ('bytes inconsistent', lambda d: part(d).update(bytes=1)), ('offset inconsistent', lambda d: part(d).update(offset=1)),
    ('blocks inconsistent', lambda d: d.update(blocks=7)), ('disk_bytes inconsistent', lambda d: d.update(disk_bytes=7)),
    ('note not text', lambda d: d.update(note=3)), ('note too long', lambda d: d.update(note='n' * 601)),
    ('disk GUID malformed', lambda d: d.update(disk_guid='not-a-guid')),
)


class Loader(unittest.TestCase):
    def test_fixtures_load_and_describe_a_typical_windows_disk(self):
        for name, block in (('gpt-512-shrunk.json', 512), ('gpt-512-free-at-end.json', 512), ('gpt-4096-shrunk.json', 4096)):
            layout = gl.load(FIXTURES / name)
            self.assertEqual(layout.block_size, block, name)
            self.assertEqual([p.type for p in layout.partitions], [
                'c12a7328-f81f-11d2-ba4b-00a0c93ec93b', 'e3c9e316-0b5c-4db8-817d-f92df00215ae',
                'ebd0a0a2-b9e5-4433-87c0-68b6b72699c7', 'de94bba4-06d1-4d40-a16a-bfd50179d6ac'], name)
            sizes = [(p.last - p.first + 1) * block // (1 << 20) for p in layout.partitions]
            self.assertEqual((sizes[0], sizes[1], sizes[3]), (260, 16, 1200), name)       # ESP, reserved, recovery
            self.assertGreater(sizes[2], 600 * 1024, name)                                # the Windows volume
            self.assertTrue(gl.free_extents(layout), name)
        self.assertEqual(gl.free_extents(LAYOUT_A), [(34, 2047), (1342744576, 1951066111), (1953523712, 1953525134)])
        self.assertEqual(gl.free_extents(LAYOUT_B), [(34, 2047), (1345202176, 1953525134)])
        self.assertEqual(LAYOUT_A.nsze * 512, LAYOUT_4K.nsze * 4096)          # the same disk, two LBA formats

    def test_malformed_layouts_are_refused(self):
        for name, edit in MALFORMED:
            with self.subTest(name), self.assertRaises(gl.LayoutError):
                gl.validate(refuse(edit))
        for bad in ([], 'text', None, 3):
            with self.subTest(repr(bad)), self.assertRaises(gl.LayoutError):
                gl.validate(bad)

    def test_informational_keys_are_accepted_only_when_consistent(self):
        doc = layout_doc()
        doc.update(blocks=doc['last_block'] + 1, disk_bytes=(doc['last_block'] + 1) * 512)
        for p in doc['partitions']:
            p.update(type_name='x', bytes=(p['last_lba'] - p['first_lba'] + 1) * 512, offset=p['first_lba'] * 512)
        self.assertEqual(gl.validate(doc), LAYOUT_A)

    def test_json_text_refusals(self):
        text = (FIXTURES / 'gpt-512-shrunk.json').read_text()
        self.assertEqual(gl.loads(text), LAYOUT_A)
        for name, bad in (
                ('duplicate key', text.replace('"block_size": 512,', '"block_size": 512, "block_size": 512,')),
                ('NaN', text.replace('"last_block": 1953525167', '"last_block": NaN')),
                ('Infinity', text.replace('"last_block": 1953525167', '"last_block": Infinity')),
                ('float', text.replace('"last_block": 1953525167', '"last_block": 1953525167.0')),
                ('exponent', text.replace('"last_block": 1953525167', '"last_block": 1.953525167e9')),
                ('trailing comma', text.replace('"block_size": 512,', '"block_size": 512,,')),
                ('empty', ''), ('truncated', text[:200]), ('BOM', '\ufeff' + text), ('deeply nested', '[' * 100000),
                ('not an object', '[1, 2]'), ('null', 'null')):
            with self.subTest(name), self.assertRaises(gl.LayoutError):
                gl.loads(bad)
        with self.assertRaises(gl.LayoutError):
            gl.load(FIXTURES / 'no-such-layout.json')

    def test_a_survey_disk_entry_converts_and_a_flagged_one_is_refused(self):
        disk = dict(layout_doc(), errors=[], warnings=['x'], pinned=True, device_path='Pci(0x0,0x0)')
        for key in ('schema', 'schema_version', 'note'):
            disk.pop(key)
        for p in disk['partitions']:
            p.update(type_name='t', bytes=(p['last_lba'] - p['first_lba'] + 1) * 512, offset=p['first_lba'] * 512)
        for header in ('primary', 'backup'):
            disk[header].update(revision=65536, header_crc=1, header_crc_ok=True)
        converted = gl.from_survey_disk(disk)
        self.assertEqual(converted.partitions, LAYOUT_A.partitions)
        self.assertEqual((converted.first_usable, converted.last_usable), (34, LAYOUT_A.last_usable))
        for name, edit in (('errors', lambda d: d.update(errors=['primary: CRC mismatch'])),
                           ('no primary', lambda d: d.pop('primary')), ('no size', lambda d: d.pop('block_size'))):
            broken = copy.deepcopy(disk)
            edit(broken)
            with self.subTest(name), self.assertRaises(gl.LayoutError):
                gl.from_survey_disk(broken)
        with self.assertRaises(gl.LayoutError):
            gl.from_survey_disk([])


class WindowModel(unittest.TestCase):
    """The generic model (any LBA size) against the written-out verdicts."""

    def test_the_tables_hold_for_the_512_layouts(self):
        window_reasons = ('OK',) + tuple(n for n in CODE_NAMES if n.startswith(('WINDOW_', 'OVERLAP')))
        count = 0
        for name, expected, edits in TARGET_CASES:
            if expected in window_reasons and window_only(edits):
                case = edited(*edits)
                with self.subTest(name):
                    self.assertEqual(gl.window_verdict(LAYOUT_A, case['p']['first_lba'], case['p']['last_lba']), expected)
                count += 1
        self.assertGreater(count, 30)
        for name, expected, (first, last) in LAYOUT_B_CASES:
            with self.subTest('B: ' + name):
                self.assertEqual(gl.window_verdict(LAYOUT_B, first, last), expected)

    def test_the_4096_layout_uses_bytes_for_alignment_and_guard(self):
        for name, expected, (first, last) in FOURK_CASES:
            with self.subTest(name):
                self.assertEqual(gl.window_verdict(LAYOUT_4K, first, last), expected)

    def test_a_window_must_equal_the_snapintosh_partition_when_one_is_named(self):
        guid = 'a1a1a1a1-0000-4000-8000-000000000005'
        doc = layout_doc()
        doc['partitions'].insert(3, {'index': 4, 'type': gl.SNAPINTOSH_TYPE, 'guid': guid, 'first_lba': GUARD_FIRST,
                                     'last_lba': WIN_A[1], 'attributes': 0, 'name': 'Snapintosh'})
        layout, extent_ = gl.validate(doc), (GUARD_FIRST, WIN_A[1])
        self.assertEqual(gl.window_verdict(layout, *extent_, partition_guid=guid), 'OK')
        self.assertEqual(gl.window_verdict(layout, *extent_), 'OVERLAP')               # as free space it is taken
        self.assertEqual(gl.window_verdict(layout, extent_[0], extent_[1] - 2048, partition_guid=guid), 'PARTITION_EXTENT')
        self.assertEqual(gl.window_verdict(layout, *extent_, partition_guid=guid[:-2] + 'ff'), 'PARTITION_MISSING')
        self.assertEqual(gl.window_verdict(layout, ESP.first, ESP.last, partition_guid=ESP.guid), 'PARTITION_TYPE')
        doc['partitions'][3]['last_lba'] = RECOVERY.first - 1                          # touches the recovery partition
        self.assertEqual(gl.window_verdict(gl.validate(doc), GUARD_FIRST, RECOVERY.first - 1, partition_guid=guid),
                         'WINDOW_GUARD')

    def test_window_arguments_must_be_u64(self):
        for bad in (-1, U64 + 1, 1.5, True, '1'):
            with self.subTest(repr(bad)), self.assertRaises(TypeError):
                gl.window_verdict(LAYOUT_A, bad, 5)

    def test_cli_verdicts_and_exit_codes(self):
        run = lambda *a: subprocess.run([sys.executable, '-B', str(FIXTURES / 'gptlayout.py'), *a],   # noqa: E731
                                        capture_output=True, text=True)
        good = run('check', str(FIXTURES / 'gpt-512-shrunk.json'), str(WIN_A[0]), str(WIN_A[1]))
        self.assertEqual((good.returncode, good.stdout.strip()), (0, 'OK'))
        bad = run('check', str(FIXTURES / 'gpt-512-shrunk.json'), str(WINDOWS.last), str(WIN_A[1]))
        self.assertEqual((bad.returncode, bad.stdout.strip()), (1, 'OVERLAP'))
        missing = run('check', str(FIXTURES / 'nope.json'), '1', '2')
        self.assertEqual(missing.returncode, 2)
        self.assertIn('refused', missing.stderr)


class Contract(unittest.TestCase):
    """compat/nvme-host.c's nvmeh_sp11_check against the model and the written-out verdicts."""

    def test_code_table_is_complete(self):
        out = subprocess.run([str(driver()), 'codes'], capture_output=True, text=True, timeout=60).stdout.splitlines()
        codes = {l.split()[1]: int(l.split()[2]) for l in out if l.startswith('code ')}
        self.assertEqual(codes, {n: CODE[n] for n in CODE_NAMES})
        header = (ROOT / 'compat' / 'nvme-host.h').read_text()
        in_header = {m.group(1): int(m.group(2)) for m in re.finditer(r'NVMEH_E_SP11_([A-Z0-9_]+)\s*=\s*(-\d+)', header)}
        self.assertEqual(in_header, codes)
        texts = [l.split(None, 3)[3] for l in out if l.startswith('code ')]
        self.assertEqual(len(set(texts)), len(texts))                 # every code has a text of its own
        self.assertFalse([t for t in texts if t.startswith('unknown')])
        consts = {l.split()[1]: int(l.split()[2]) for l in out if l.startswith('const ')}
        self.assertEqual((consts['NVMEH_SP11_ALIGN_LBAS'], consts['NVMEH_SP11_GUARD_LBAS'], consts['NVMEH_SP11_EDGE_LBAS'],
                          consts['NVMEH_SP11_MIN_WINDOW_LBAS'], consts['NVMEH_SP11_MAX_PROTECTED'], consts['NVMEH_SP11_ABI']),
                         (MIB_LBAS, MIB_LBAS, MIB_LBAS, 4096, MAX_PROT, 1))
        sizes = {l.split()[1]: int(l.split()[2]) for l in out if l.startswith('size ')}
        self.assertEqual((sizes['target'], sizes['record'], sizes['params'], sizes['params_words']),
                         (8 * len(TARGET_FIELDS), 8 * (len(RECORD_FIELDS) + 2 * MAX_PROT), 8 * len(PARAM_FIELDS),
                          len(PARAM_FIELDS)))

    def test_expected_verdicts_model_and_c(self):
        model, c = verdicts([edited(*edits) for _, _, edits in TARGET_CASES])
        for (name, expected, _), m, n in zip(TARGET_CASES, model, c):
            with self.subTest(name):
                self.assertEqual((m, n), (expected, expected))

    def test_every_refusal_reason_of_the_check_is_exercised(self):
        reached = {expected for _, expected, _ in TARGET_CASES} | {e for _, e, _ in LAYOUT_B_CASES}
        for name in CODE_NAMES:
            if not name.startswith(('ADMIN_', 'IO_', 'NO_DEVICE', 'DEVICE_', 'HEADER_', 'BAR', 'UNKNOWN_DEVICE')):
                self.assertIn(name, reached, name)

    def test_layout_b_windows_model_c_and_generic_agree(self):
        model, c = verdicts([base_case(LAYOUT_B, w) for _, _, w in LAYOUT_B_CASES])
        for (name, expected, w), m, n in zip(LAYOUT_B_CASES, model, c):
            with self.subTest(name):
                self.assertEqual((m, n, gl.window_verdict(LAYOUT_B, *w)), (expected,) * 3)

    def test_neighbours_off_the_mib_grid_still_get_a_full_guard(self):
        """The window is MiB aligned, a neighbour laid down by an older tool may not be: the gap is then 2047 or 2049
        blocks, and one block short is refused on either side while one block of slack is accepted."""
        cases = [(name, expected, offgrid_layout(*shift), window) for name, expected, shift, window in OFFGRID_CASES]
        model, c = verdicts([base_case(layout, window) for _, _, layout, window in cases])
        for (name, expected, layout, window), m, n in zip(cases, model, c):
            with self.subTest(name):
                self.assertEqual((m, n, gl.window_verdict(layout, *window)), (expected,) * 3)

    def test_the_monitor_refuses_a_4096_byte_disk_whatever_the_window(self):
        for name, expected, (first, last) in FOURK_CASES:
            with self.subTest(name):
                self.assertEqual(verdicts([base_case(LAYOUT_4K, (first, last))]), (['LBA_FORMAT'], ['LBA_FORMAT']))
        # The same record claiming 512 is a lie the geometry check catches: the 4096-byte GPT starts usable LBAs at 6.
        self.assertEqual(check_model(edited(setr(lba_bytes=512), setp(lba_bytes=512), base=base_case(LAYOUT_4K))), 'GEOMETRY')

    def test_layout_model_monitor_model_and_c_agree_on_every_boundary(self):
        """Three implementations, one answer: the layout-level model, the record-level model and the C code."""
        for layout in (LAYOUT_A, LAYOUT_B):
            marks = {0, 1, 33, 34, 35, layout.last_block, layout.last_block + 1, layout.nsze + 4096, layout.last_usable,
                     layout.last_usable + 1, layout.last_usable - 2048, layout.last_usable - 2049}
            for p in layout.partitions:
                for edge in (p.first, p.last + 1):
                    marks.update(edge + d for d in (-4097, -4096, -2049, -2048, -2047, -1, 0, 1, 2047, 2048, 2049, 4095, 4096))
            for gap_first, gap_last in gl.free_extents(layout):       # the largest and some small aligned windows per gap
                low, high = -(-gap_first // MIB_LBAS) * MIB_LBAS, (gap_last + 1) // MIB_LBAS * MIB_LBAS
                marks.update((low + MIB_LBAS, low + MIB_LBAS + 4095, low + MIB_LBAS + 8191, high - MIB_LBAS - 1, high - 1))
            marks = sorted(m for m in marks if 0 <= m <= U64)
            windows = [(a, b) for a in marks for b in marks]
            model, c = verdicts([base_case(layout, w) for w in windows])
            wrong = [(w, gl.window_verdict(layout, *w), m, n) for w, m, n in zip(windows, model, c)
                     if not gl.window_verdict(layout, *w) == m == n]
            self.assertEqual(wrong[:5], [])
            self.assertGreater(len(windows), 2000)
            self.assertIn('OK', c)

    def test_the_guard_to_the_usable_edges_is_exact(self):
        """An fdisk-style usable range (first usable LBA 2048, last usable end on a MiB): one guard in from each edge is
        accepted, one block less is not. With a 34-block GPT no aligned window can sit exactly on the guard. One
        partition in the middle keeps the protected list non-empty; every window stays a guard clear of it."""
        n, mid = 1000, 500
        doc = {'schema': gl.SCHEMA, 'schema_version': 1, 'block_size': 512, 'last_block': 2048 * n + 32,
               'first_usable': 2048, 'last_usable': 2048 * n - 1,
               'primary': {'my_lba': 1, 'alternate_lba': 2048 * n + 32, 'entries_lba': 2, 'count': 128, 'entry_size': 128},
               'backup': {'my_lba': 2048 * n + 32, 'alternate_lba': 1, 'entries_lba': 2048 * n, 'count': 128, 'entry_size': 128},
               'partitions': [{'index': 0, 'type': 'ebd0a0a2-b9e5-4433-87c0-68b6b72699c7',
                               'guid': 'a1a1a1a1-0000-4000-8000-000000000009', 'first_lba': 2048 * mid,
                               'last_lba': 2048 * (mid + 1) - 1, 'attributes': 0, 'name': 'middle'}]}
        layout = gl.validate(doc)
        cases = (('exactly one guard from the primary edge', 'OK', (4096, 2048 * (mid - 1) - 1)),
                 ('first MiB too close to the primary GPT', 'WINDOW_GUARD', (2048, 2048 * (mid - 1) - 1)),
                 ('inside the primary entry array', 'WINDOW_GPT', (1024, 2048 * (mid - 1) - 1)),
                 ('exactly one guard from the backup edge', 'OK', (2048 * (mid + 2), 2048 * (n - 1) - 1)),
                 ('last MiB too close to the backup GPT', 'WINDOW_GUARD', (2048 * (mid + 2), 2048 * n - 1)))
        model, c = verdicts([base_case(layout, w) for _, _, w in cases])
        for (name, expected, w), m, n_ in zip(cases, model, c):
            with self.subTest(name):
                self.assertEqual((m, n_, gl.window_verdict(layout, *w)), (expected,) * 3)

    def test_random_corruption_model_equals_c(self):
        rng = random.Random(20261110)
        pool = (0, 1, 2, 3, 4, 7, 0x7f, 0x80, 0xff, 0x100, 0xffff, 0x10000, 0xfffff, 0x100000, 0x7fffffff, 0x80000000,
                0xffffffff, 1 << 32, 1 << 35, IPA - 1, IPA, IPA + 1, 1 << 55, (1 << 55) - 1, 1 << 63, U64 - 1, U64)
        bases = [base_case(), base_case(writable=True), base_case(LAYOUT_B, (FREE_B, FREE_B + 134217727))]
        cases = []
        for _ in range(9000):
            case = copy.deepcopy(rng.choice(bases))
            for _ in range(rng.choice((1, 1, 2, 3))):
                which = rng.choice(('t', 'r', 'r', 'p', 'p', 'x'))
                if which == 'x':
                    i = rng.randrange(MAX_PROT)
                    ext = list(case['r']['ext'][i])
                    ext[rng.randrange(2)] = rng.choice(pool + (case['r']['ext'][i][0] + 1, case['r']['ext'][i][1] - 1))
                    case['r']['ext'][i] = tuple(ext)
                    continue
                key = rng.choice([k for k in sorted(case[which]) if k != 'ext'])
                old = case[which][key]
                case[which][key] = rng.choice(pool + ((old + 1) & U64, (old - 1) & U64, old ^ 1, rng.getrandbits(64)))
            cases.append(case)
        model, c = verdicts(cases)
        self.assertEqual([(i, m, n) for i, (m, n) in enumerate(zip(model, c)) if m != n][:5], [])
        self.assertGreater(len(set(model)), 20)                 # the corruption really reaches many reasons


class Endpoint(unittest.TestCase):
    @staticmethod
    def cfg(edits):
        cfg = list(GOOD_CFG)
        for i, v in edits.items():
            cfg[i] = v
        return cfg

    def test_config_space_proof_model_and_c(self):
        t = base_case()['t']
        out = native('endpoint', [hexw([t[k] for k in TARGET_FIELDS] + [bar] + self.cfg(edits))
                                  for _, _, edits, bar in ENDPOINT_CASES])
        for (name, expected, edits, bar), line in zip(ENDPOINT_CASES, out):
            code, badreads = (int(x) for x in line.split())
            with self.subTest(name):
                self.assertEqual((NAME[code], badreads), (expected, 0))
                self.assertEqual(prove_model(bar, self.cfg(edits)), expected)

    def test_reads_land_only_in_the_configuration_header_of_the_pinned_bdf(self):
        """A C bug in the ECAM arithmetic would read elsewhere; the fake counts every read outside the header."""
        for rid in (0x0000, 0x0100, 0x0108, 0x0101, 0x01f8, 0x7fff, 0xffff):
            case = edited(sett(bdf=rid, start_bus=0, end_bus=255))
            with self.subTest(hex(rid)):
                self.assertEqual(native('endpoint', [hexw([case['t'][k] for k in TARGET_FIELDS] + [BAR] + GOOD_CFG)]),
                                 ['0 0'])


class Admin(unittest.TestCase):
    def test_written_out_verdicts_model_and_c(self):
        out = native('admin', [f'{p:x} {o:x} {c:x}' for p, o, c, _ in ADMIN_CASES])
        for (phase, opcode, cdw10, expected), got in zip(ADMIN_CASES, out):
            with self.subTest(f'phase {phase} opcode {opcode:#x} cdw10 {cdw10:#x}'):
                self.assertEqual((NAME[int(got)], admin_model(phase, opcode, cdw10)), (expected, expected))

    def test_model_equals_c_on_every_opcode_phase_and_probe(self):
        keys = [(p, o, c) for p in (0, 1, 2) for o in range(257) for c in PROBE_CDW10]
        out = native('admin', [f'{p:x} {o:x} {c:x}' for p, o, c in keys])
        self.assertEqual([NAME[int(x)] for x in out], [admin_model(*k) for k in keys])
        allowed = lambda phase: {o for p, o, c in keys if p == phase and admin_model(p, o, c) == 'OK'}   # noqa: E731
        self.assertEqual(allowed(1), {0x02, 0x06, 0x0a})                    # Identify, Get Log Page, Get Features
        self.assertEqual(allowed(0), {0x00, 0x01, 0x02, 0x04, 0x05, 0x06, 0x09, 0x0a})
        for dangerous in (0x80, 0x84, 0x10, 0x11, 0x0d, 0x15, 0x81, 0x82):
            self.assertNotIn(dangerous, allowed(0) | allowed(1))

    def test_the_lists_equal_the_a16_takeover_tool_s(self):
        source = ROOT.parent / 'q1n1' / 'tools' / 'nvme_m3.py'
        if not source.exists():
            self.skipTest('q1n1 sibling checkout not present')
        found = {}
        for node in ast.parse(source.read_text()).body:
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id in (
                    'ADMIN_ALLOWED', 'SET_FEATURES_ALLOWED', 'IO_ALLOWED'):
                found[node.targets[0].id] = ast.literal_eval(node.value)
        self.assertEqual(set(found), {'ADMIN_ALLOWED', 'SET_FEATURES_ALLOWED', 'IO_ALLOWED'})
        self.assertEqual({o for o in range(256) for c in PROBE_CDW10 if admin_model(0, o, c) == 'OK'}, set(found['ADMIN_ALLOWED']))
        self.assertEqual({c for c in range(256) if admin_model(0, 0x09, c) == 'OK'}, set(found['SET_FEATURES_ALLOWED']))
        self.assertLessEqual(set(found['IO_ALLOWED']), {o for o in range(256) if io_model(o, 0) == 'OK'})

    def test_io_verdicts_model_and_c(self):
        keys = [(o, w) for o in range(257) for w in (0, 1)]
        out = native('io', [f'{o:x} {w:x}' for o, w in keys])
        self.assertEqual([NAME[int(x)] for x in out], [io_model(*k) for k in keys])
        self.assertEqual({o for o, w in keys if not w and io_model(o, w) == 'OK'}, {0x00, 0x02})
        self.assertEqual({o for o, w in keys if w and io_model(o, w) == 'OK'}, {0x00, 0x01, 0x02})
        self.assertEqual(io_model(1, 0), 'IO_READONLY')


class Identify(unittest.TestCase):
    A16_LIKE = ((0, 9), (0, 12))                     # a 512-byte format and a 4 KiB format the disk could be switched to

    def test_identify_supplies_the_current_format_and_never_a_better_one(self):
        facts = record_from_identify(identify_ns_page(1953525168, self.A16_LIKE, 0))
        self.assertEqual((facts['lba_bytes'], facts['metadata_bytes'], facts['nsze']), (512, 0, 1953525168))
        for flbas, expected in ((1, 4096), (0, 512)):
            self.assertEqual(record_from_identify(identify_ns_page(1953525168, self.A16_LIKE, flbas))['lba_bytes'], expected)
        for phase in (0, 1):                         # Format NVM, the only way to switch the format, is on no list
            self.assertEqual(admin_model(phase, 0x80, 0), 'ADMIN_OPCODE')

    def test_identify_is_read_only(self):
        self.assertEqual(admin_model(1, 0x06, 1), 'OK')
        self.assertEqual({o for o in range(256) for c in PROBE_CDW10 if admin_model(1, o, c) == 'OK'}, {0x02, 0x06, 0x0a})

    def test_format_facts_flow_into_the_verdict(self):
        for page, expected in ((identify_ns_page(LAYOUT_A.nsze, self.A16_LIKE, 0), 'OK'),
                               (identify_ns_page(LAYOUT_A.nsze, self.A16_LIKE, 1), 'LBA_FORMAT'),
                               (identify_ns_page(LAYOUT_A.nsze, ((8, 9), (0, 9)), 0), 'METADATA'),
                               (identify_ns_page(LAYOUT_A.nsze, ((0, 9),), 0, eui64=bytes(8)), 'EUI64')):
            facts = record_from_identify(page)
            case = edited(setr(lba_bytes=facts['lba_bytes'], metadata_bytes=facts['metadata_bytes'], nsze=facts['nsze'],
                               eui64=facts['eui64']))
            self.assertEqual(verdicts([case]), ([expected], [expected]))

    def test_the_format_index_uses_the_high_bits_when_there_are_more_than_16_formats(self):
        formats = [(0, 9)] * 40
        formats[35] = (0, 12)
        flbas = (35 & 0xf) | ((35 >> 4) << 5)        # NVMe 1.4: bits 3:0 and bits 6:5 of FLBAS
        self.assertEqual(record_from_identify(identify_ns_page(1000, formats, flbas))['lba_bytes'], 4096)
        self.assertEqual(record_from_identify(identify_ns_page(1000, formats, 3))['lba_bytes'], 512)

    def test_malformed_identify_pages_are_refused(self):
        good = identify_ns_page(1000, self.A16_LIKE, 0)
        for name, page in (('short', good[:4095]), ('long', good + b'\0'), ('empty', b''),
                           ('index beyond NLBAF', identify_ns_page(1000, ((0, 9),), 1)),
                           ('data size 2**8', identify_ns_page(1000, ((0, 8),), 0)),
                           ('data size 2**17', identify_ns_page(1000, ((0, 17),), 0)),
                           ('unused format', identify_ns_page(1000, ((0, 0),), 0))):
            with self.subTest(name), self.assertRaises(ValueError):
                record_from_identify(page)


class Board(unittest.TestCase):
    """nvmeh_sp11_target_from_board: ECAM and SMMU from a validated descriptor, everything else stays unknown."""

    @classmethod
    def setUpClass(cls):
        cls.tbd = _descriptor_fixtures()

    def board(self, *edits):
        d = self.tbd.synthetic()
        for edit in edits:
            edit(d)
        return bd.validate(d)

    def second_smmu(self, d):
        d['smmu'].append({'name': 'pcie-smmu-b', 'kind': 'smmu-v3', 'base': '0x15500000', 'size': '0x80000'})
        d['provenance']['smmu.pcie-smmu-b'] = dict(self.tbd.FIXTURE)

    def cases(self):
        S, tbd = self.tbd.S, self.tbd
        absent = lambda d: d['devices'].update(nvme={'state': 'absent', 'mmio': None, 'ranges': []})   # noqa: E731
        unknown_smmu = tbd.chain(S('smmu', None), tbd.unknowns('smmu', drop=('smmu.apps-smmu', 'smmu.pcie-smmu', 'smmu.adreno-smmu')))
        return (('the synthetic board, segment 0', 0, (), 'OK'), ('a segment the board does not describe', 1, (), 'UNKNOWN_ECAM'),
                ('NVMe absent', 0, (absent,), 'UNKNOWN_DEVICE'),
                ('ECAM unknown', 0, (tbd.chain(S('pcie.ecam', None), tbd.unknowns('pcie.ecam')),), 'UNKNOWN_ECAM'),
                ('two SMMUv3', 0, (self.second_smmu,), 'UNKNOWN_SMMU'), ('no SMMU', 0, (tbd.no_smmus,), 'UNKNOWN_SMMU'),
                ('SMMU list unknown', 0, (unknown_smmu,), 'UNKNOWN_SMMU'),
                ('no SMMUv3, only MMU-500', 0, (S('smmu.1.kind', 'mmu-500'),), 'UNKNOWN_SMMU'))

    def test_model_and_c_agree_and_leave_the_rest_unknown(self):
        cases = self.cases()
        boards = [self.board(*edits) for _, _, edits, _ in cases]
        out = native('board', [f'{seg:x} {bi.pack(b).hex()}' for (_, seg, _, _), b in zip(cases, boards)])
        for (name, seg, _, expected), board, line in zip(cases, boards, out):
            rc, known, segment, ecam, start, end, smmu = line.split()
            reason, t = target_from_board_model(board, seg)
            with self.subTest(name):
                self.assertEqual((NAME[int(rc)], reason), (expected, expected))
                self.assertEqual((int(known, 16), int(segment, 16), int(ecam, 16), int(start, 16), int(end, 16), int(smmu, 16)),
                                 tuple(t[k] for k in ('known', 'segment', 'ecam_base', 'start_bus', 'end_bus', 'smmu_base')))
        first = out[0].split()
        self.assertEqual((int(first[1], 16), int(first[3], 16), int(first[6], 16)), (K_ECAM | K_SMMU, ECAM, 0x15400000))
        self.assertEqual(base_case()['t']['ecam_base'], int(first[3], 16))   # the base case is the synthetic board's

    def test_the_shipped_sp11_template_cannot_become_a_target(self):
        board = bd.load(ROOT / 'config' / 'boards' / 'sp11.json')
        out = native('board', [f'0 {bi.pack(board).hex()}'])
        self.assertEqual(NAME[int(out[0].split()[0])], 'UNKNOWN_DEVICE')
        self.assertEqual(target_from_board_model(board, 0)[0], 'UNKNOWN_DEVICE')

    def test_pins_the_descriptor_cannot_carry_refuse_until_supplied(self):
        """The ECAM and SMMU groups alone are not enough: BDF, stream, EUI64 and generation must be pinned too."""
        case = edited(lambda c: c['t'].update(target_from_board_model(self.board(), 0)[1]))
        self.assertEqual(verdicts([case]), (['UNKNOWN_BDF'], ['UNKNOWN_BDF']))


class Adopt(unittest.TestCase):
    """nvmeh_sp11_adopt end to end on a fake controller that treats every other partition and the GPT as untouchable."""

    @staticmethod
    def run_adopt(case, ops, cfg=GOOD_CFG):
        out = native('adopt', [hexw(words_of(case) + list(cfg)) + ' ' + ' '.join(ops)])[0].split()
        return dict(rc=int(out[0]), prepare=int(out[1]), doorbells=int(out[2]), violations=int(out[3]), badreads=int(out[4]),
                    ops=[tuple(int(x) for x in r.split(':')) for r in out[5:-1]], final_violations=int(out[-1][1:]))

    def test_a_read_only_window_is_confined(self):
        span = WIN_A[1] - WIN_A[0] + 1
        ops = ['r:0:8', f'r:{span - 8}:8', f'r:{span}:1', f'r:{span - 4}:8', 'r:0:257', 'r:0:0', 'w:10:8', 'w:0:1', 'f',
               f'r:{U64}:8', f'r:{span - 1}:2', 'r:100:256', f'r:{span - 1}:1']
        got = self.run_adopt(base_case(), ops)
        self.assertEqual((got['rc'], got['prepare'], got['violations'], got['badreads'], got['final_violations']), (0, 0, 0, 0, 0))
        # (result, doorbells): reads in the window go out; everything else is refused before a doorbell.
        self.assertEqual(got['ops'], [(0, 1), (0, 1), (-84, 0), (-84, 0), (-84, 0), (-84, 0), (-88, 0), (-88, 0), (0, 1),
                                      (-84, 0), (-84, 0), (0, 1), (0, 1)])
        self.assertEqual(got['doorbells'], 0)

    def test_transfer_limit_and_data_buffer_bounds_hold(self):
        """The window is enforced through the command path the A16 uses: a transfer over the controller's limit, a data
        offset that is not page aligned and a buffer that would overrun the 128 KiB data area are refused before a
        doorbell, and the exact limits are accepted."""
        # 32 KiB stands for a controller whose MDTS is below the 128 KiB buffer; the host sets max_transfer from it.
        got = self.run_adopt(edited(setp(max_transfer=32768)), ['r:0:64', 'r:0:65', 'r:0:128', 'r:64:64'])
        self.assertEqual((got['rc'], got['violations'], got['badreads']), (0, 0, 0))
        self.assertEqual(got['ops'], [(0, 1), (-84, 0), (-84, 0), (0, 1)])
        ops = ['r:0:8:4096', 'r:0:8:512', 'r:0:8:4097', 'r:0:8:126976', 'r:0:16:126976', 'r:0:8:131072', 'r:0:8:135168',
               'r:0:256:0', 'r:0:257:0']
        got = self.run_adopt(base_case(), ops)
        self.assertEqual((got['rc'], got['violations'], got['badreads'], got['final_violations']), (0, 0, 0, 0))
        self.assertEqual(got['ops'], [(0, 1), (-84, 0), (-84, 0), (0, 1), (-84, 0), (-84, 0), (-84, 0), (0, 1), (-84, 0)])

    def test_a_writable_window_never_reaches_an_edge_megabyte(self):
        span = WIN_A[1] - WIN_A[0] + 1
        ops = ['w:2048:8', 'w:2047:8', f'w:{span - 2048 - 8}:8', f'w:{span - 2048 - 7}:8', f'w:{span - 2048}:8', 'w:0:8',
               f'w:{span - 8}:8', 'w:2048:0', 'w:2048:257', 'f', 'r:0:8', 'w:5000:256']
        got = self.run_adopt(base_case(writable=True), ops)
        self.assertEqual((got['rc'], got['violations'], got['badreads'], got['final_violations']), (0, 0, 0, 0))
        self.assertEqual(got['ops'], [(0, 1), (-84, 0), (0, 1), (-84, 0), (-84, 0), (-84, 0), (-84, 0), (-84, 0), (-84, 0),
                                      (0, 1), (0, 1), (0, 1)])

    def test_every_refusal_sends_nothing_and_leaves_the_controller_dead_to_the_monitor(self):
        for name, expected, edits in TARGET_CASES:
            if expected == 'OK':
                continue
            got = self.run_adopt(edited(*edits), ['r:0:8', 'w:0:8', 'f'])
            with self.subTest(name):
                self.assertEqual((got['rc'], got['prepare']), (CODE[expected], CODE[expected]))
                self.assertEqual((got['doorbells'], got['violations'], got['badreads'], got['final_violations']), (0, 0, 0, 0))
                self.assertEqual(got['ops'], [(-87, 0)] * 3)                  # NVMEH_E_DEAD: not adopted, nothing sent

    def test_endpoint_refusals_stop_the_adoption_before_the_controller(self):
        for name, expected, edits, bar in ENDPOINT_CASES:
            cfg = list(GOOD_CFG)
            for i, v in edits.items():
                cfg[i] = v
            got = self.run_adopt(edited(setp(bar=bar)), ['r:0:8'], cfg)
            with self.subTest(name):
                if expected == 'OK':
                    self.assertEqual((got['rc'], got['ops']), (0, [(0, 1)]))
                else:
                    self.assertEqual((got['rc'], got['prepare'], got['ops'], got['badreads']),
                                     (CODE[expected], CODE[expected], [(-87, 0)], 0))

    def test_the_a16_adoption_checks_still_run_after_the_sp11_ones(self):
        got = self.run_adopt(edited(setp(io_phase=2)), ['r:0:8'])
        self.assertEqual((got['rc'], got['prepare'], got['ops']), (-80, -80, [(-87, 0)]))        # NVMEH_E_PARAMS

    def test_all_accepted_windows_stay_inside_the_free_extent_on_the_fake_controller(self):
        for win in ((GUARD_FIRST, GUARD_LAST), WIN_A, (GUARD_FIRST, GUARD_FIRST + 4095)):
            span = win[1] - win[0] + 1
            got = self.run_adopt(base_case(window=win), ['r:0:8', f'r:{span - 8}:8', 'f'])
            with self.subTest(win):
                self.assertEqual((got['rc'], got['violations'], got['final_violations'], got['ops']),
                                 (0, 0, 0, [(0, 1), (0, 1), (0, 1)]))


class Gate(unittest.TestCase):
    """The SP11 code exists only under the gate, and what is built for the monitor needs no libc and holds no state."""

    def setUp(self):
        self.clang, self.nm = find_tool('clang'), find_tool('llvm-nm')
        if not (self.clang and self.nm):
            self.skipTest('clang or llvm-nm not found')

    def test_default_preprocessing_has_no_sp11_text(self):
        for define, present in (([], False), (['-DSNAP_PLATFORM_SP11=1'], True)):
            for unit in ('nvme-host.c', 'nvme-host.h'):
                text = subprocess.run([self.clang, '-E', '-P', '-xc', *define, f'-I{ROOT / "compat"}', str(ROOT / 'compat' / unit)],
                                      capture_output=True, text=True, check=True).stdout
                with self.subTest(unit=unit, define=define):
                    self.assertEqual('nvmeh_sp11_check' in text, present)

    def test_gate_conflicts_are_refused(self):
        built = subprocess.run([self.clang, '-fsyntax-only', '-DSNAP_PLATFORM_SP11=1', '-DSNAP_PLATFORM_A16=1',
                                f'-I{ROOT / "compat"}', str(ROOT / 'compat' / 'nvme-host.c')], capture_output=True, text=True)
        self.assertNotEqual(built.returncode, 0)
        self.assertIn('mutually exclusive', built.stderr)

    def test_the_monitor_object_has_no_libc_and_no_state(self):
        flags = [f for f in json.loads((ROOT / 'config' / 'monitor-flags.json').read_text())['flags'] if f != '-flto']
        BUILD.mkdir(parents=True, exist_ok=True)
        obj = BUILD / 'nvme-host-aarch64.o'
        built = subprocess.run([self.clang, *flags, '-DSNAP_PLATFORM_SP11=1', f'-I{ROOT / "compat"}', '-c',
                                str(ROOT / 'compat' / 'nvme-host.c'), '-o', str(obj)], capture_output=True, text=True, timeout=300)
        if built.returncode and 'unable to find target' in built.stderr:
            self.skipTest('clang cannot target aarch64-unknown-none-elf')
        self.assertEqual(built.returncode, 0, built.stderr)
        symbols = subprocess.run([self.nm, '--format=posix', str(obj)], capture_output=True, text=True, check=True).stdout
        kinds = {line.split()[1] for line in symbols.splitlines() if line.strip()}
        self.assertNotIn('U', kinds, symbols)                 # no memcpy/memset: the monitor links no libc
        self.assertFalse(kinds & set('DdBbCc'), symbols)      # nothing mutable shared between EL2 entries
        defined = {line.split()[0] for line in symbols.splitlines() if line.split()[1] in 'Tt'}
        self.assertTrue({'nvmeh_sp11_check', 'nvmeh_sp11_adopt', 'nvmeh_sp11_admin_verdict', 'nvmeh_adopt'} <= defined, defined)


REFS = ROOT / 'build' / 'refs'
CITED = (                       # (file under build/refs, line, text the line must hold): docs/SP11-NVME-WINDOW.md cites these
    ('hamoa.dtsi', 3434, 'pcie6a: pcie@1bf8000'), ('hamoa.dtsi', 3437, '0x01bf8000'), ('hamoa.dtsi', 3438, '0x70000000'),
    ('hamoa.dtsi', 3441, '0x70100000'), ('hamoa.dtsi', 3453, 'bus-range = <0x00 0xff>'),
    ('hamoa.dtsi', 3457, 'linux,pci-domain = <6>'), ('hamoa.dtsi', 3524, 'status = "disabled"'),
    ('hamoa.dtsi', 3526, 'pcie6a_port0: pcie@0'), ('hamoa.dtsi', 8544, 'pcie_smmu: iommu@15400000'),
    ('hamoa.dtsi', 8545, 'arm,smmu-v3'), ('hamoa.dtsi', 8546, '0x15400000'), ('hamoa.dtsi', 8555, 'Controlled by Gunyah'),
    ('x1-el2.dtso', 25, 'controlled by the firmware'), ('x1-el2.dtso', 48, 'iommu-map = <0 &pcie_smmu 0x60000 0x10000>'),
    ('x1-el2.dtso', 52, 'status = "okay"'), ('x1-microsoft-denali.dtsi', 267, 'VREG_NVME_3P3'),
    ('x1-microsoft-denali.dtsi', 1015, '&pcie6a'), ('x1-microsoft-denali.dtsi', 1016, 'vreg_nvme'),
    ('x1-microsoft-denali.dtsi', 1032, 'reset-gpios'), ('x1-microsoft-denali.dtsi', 277, 'regulator-boot-on'),
    ('nvme.h', 434, 'struct nvme_id_ns'),
    ('nvme.h', 561, 'NVME_ID_CNS_NS'), ('nvme.h', 562, 'NVME_ID_CNS_CTRL'), ('nvme.h', 606, 'NVME_NS_FLBAS_LBA_MASK'),
    ('nvme.h', 643, 'NVME_NS_FLBAS_LBA_UMASK'), ('nvme.h', 957, 'nvme_cmd_flush'), ('nvme.h', 958, 'nvme_cmd_write'),
    ('nvme.h', 959, 'nvme_cmd_read'), ('nvme.h', 1120, 'NVME_RW_FUA'), ('nvme.h', 1306, 'enum nvme_admin_opcode'),
    ('nvme.h', 1312, 'nvme_admin_identify'), ('nvme.h', 1329, 'nvme_admin_format_nvm'),
    ('nvme.h', 1332, 'nvme_admin_sanitize_nvm'), ('nvme.h', 1375, 'NVME_FEAT_ARBITRATION'),
    ('nvme.h', 1381, 'NVME_FEAT_NUM_QUEUES'), ('nvme.h', 1396, 'NVME_FEAT_SANITIZE'), ('nvme.h', 1406, 'NVME_LOG_ERROR'),
    ('nvme.h', 1407, 'NVME_LOG_SMART'), ('nvme.h', 1408, 'NVME_LOG_FW_SLOT'),
)


class Citations(unittest.TestCase):
    """The line citations in docs/SP11-NVME-WINDOW.md and compat/nvme-host.c, against the pinned public sources.

    Fetch the files from https://raw.githubusercontent.com/torvalds/linux/7475ea8b58616b33c04afb976cf0fbaad9dd63fb/ (the
    three device-tree files from arch/arm64/boot/dts/qcom/, nvme.h from include/linux/) into build/refs/. Without them
    this reports SKIP.
    """

    def test_cited_lines_say_what_the_documents_claim(self):
        missing = sorted({f for f, _, _ in CITED if not (REFS / f).exists()})
        if missing:
            self.skipTest(f'pinned sources not in build/refs: {missing}')
        for name, number, text in CITED:
            lines = (REFS / name).read_text(encoding='utf-8', errors='replace').splitlines()
            with self.subTest(f'{name}:{number}'):
                self.assertIn(text, lines[number - 1])


class Hygiene(unittest.TestCase):
    FILES = ('compat/nvme-host.c', 'compat/nvme-host.h', 'tools/test-nvme-window-hamoa.c', 'tools/test-nvme-window-hamoa.py',
             'tools/fixtures/sp11/gptlayout.py', 'tools/fixtures/sp11/nvmewindow.py', 'docs/SP11-NVME-WINDOW.md',
             'tools/fixtures/sp11/gpt-512-shrunk.json', 'tools/fixtures/sp11/gpt-512-free-at-end.json',
             'tools/fixtures/sp11/gpt-4096-shrunk.json')

    def test_new_and_edited_files_follow_the_repository_rules(self):
        for relpath in self.FILES:
            text = (ROOT / relpath).read_text(encoding='utf-8')
            with self.subTest(relpath):
                if relpath.endswith(('.c', '.h', '.py')):
                    self.assertIn('SPDX-License-Identifier: MIT', '\n'.join(text.splitlines()[:3]))
                self.assertLess(len(text.splitlines()), 1500)
                self.assertLess(len(text.encode()), 50 * 1024)
                for prefix in ('/' + 'Users' + '/', '/' + 'home' + '/'):
                    self.assertNotIn(prefix, text)

    def test_fixtures_say_they_are_synthetic(self):
        for path in sorted(FIXTURES.glob('gpt-*.json')):
            self.assertRegex(json.loads(path.read_text())['note'], r'(?i)synthetic', path.name)

    def test_the_documented_invariants_name_tests_that_exist(self):
        doc = (ROOT / 'docs' / 'SP11-NVME-WINDOW.md').read_text(encoding='utf-8')
        names = set(re.findall(r'`(test_[a-z0-9_]+)`', doc))
        self.assertGreater(len(names), 15)
        have = {n for cls in (Loader, WindowModel, Contract, Endpoint, Admin, Identify, Board, Adopt, Gate, Citations, Hygiene)
                for n in dir(cls) if n.startswith('test_')}
        self.assertEqual(sorted(names - have), [])


if __name__ == '__main__':
    unittest.main()
