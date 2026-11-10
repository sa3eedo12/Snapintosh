#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""snapmac-partition-plan --profile sp11: exact numbers, every refusal, the N-reader gate.

  python3 tools/test-snapmac-partition-plan-sp11.py

Every disk here is synthetic: a 512 GiB Windows-style layout built in code with obviously fake
identifiers, written as the first and last MiB of a GPT image and decoded by q1n1's
tools/gpt_survey.py, the decoder the real survey uses. The tests run in 512e and 4Kn, read and
write only files under build/test-snapmac-partition-plan-sp11, and never open a disk or a device.
The A16 outputs are compared with golden digests so that the default profile stays byte-identical.
"""
import ast
from collections import namedtuple
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import signal
import struct
import sys
import tempfile
import unittest
from unittest import mock
import uuid
import zlib

HERE = Path(__file__).resolve().parent
Q1N1_TOOLS = Path(os.environ.get('Q1N1_TOOLS', HERE.parents[1] / 'q1n1/tools'))
sys.path.insert(0, str(Q1N1_TOOLS))
import gpt_survey as gs  # noqa: E402

SCRATCH = HERE.parent / 'build' / 'test-snapmac-partition-plan-sp11'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PLANNER = HERE / 'snapmac-partition-plan.py'
pm = load('snapmac_partition_plan', PLANNER)
a16 = load('a16_partition_plan_test', HERE / 'test-snapmac-partition-plan.py')

MIB, GIB = 1 << 20, 1 << 30
DISK = 512 * GIB
EUI = '0123456789ABCDEF'
EUI_REVERSED = 'EFCDAB8967452301'
T_ESP = 'c12a7328-f81f-11d2-ba4b-00a0c93ec93b'
T_MSR = 'e3c9e316-0b5c-4db8-817d-f92df00215ae'
T_BASIC = 'ebd0a0a2-b9e5-4433-87c0-68b6b72699c7'
T_RECOVERY = 'de94bba4-06d1-4d40-a16a-bfd50179d6ac'
T_OTHER = '0fc63daf-8483-4772-8e79-3d69d8477de4'
NIL = '00000000-0000-0000-0000-000000000000'
RECOVERY_ATTRS = 0x8000000000000001
FORMAT = 'snapmac-reader/1'
NEW_NAME = 'Snapintosh macOS'
GDISK_CODE = {T_ESP: 'EF00', T_MSR: '0C01', T_BASIC: '0700', T_RECOVERY: '2700'}

Row = namedtuple('Row', 'slot guid type offset size name attrs', defaults=(None,))


def G(n):
    return f'f0f0f0f0-0000-4000-8000-{n:012d}'


DISK_GUID, ESP, MSR, WINC, REC, NEW_GUID = G(1), G(10), G(11), G(12), G(13), G(90)


def attrs_of(r):
    return r.attrs if r.attrs is not None else (RECOVERY_ATTRS if r.type == T_RECOVERY else 0)


def layout(c_end=200 * GIB, recovery=True, esp_start=MIB):
    """ESP, MSR, C: ending at c_end, and a recovery partition 1 MiB short of the disk end."""
    rows = [Row(0, ESP, T_ESP, esp_start, 260 * MIB, 'EFI system partition'),
            Row(1, MSR, T_MSR, esp_start + 260 * MIB, 16 * MIB, 'Microsoft reserved partition'),
            Row(2, WINC, T_BASIC, esp_start + 276 * MIB, c_end - esp_start - 276 * MIB, 'Basic data partition')]
    if recovery:
        rows.append(Row(3, REC, T_RECOVERY, DISK - GIB - MIB, GIB, 'Windows recovery'))
    return rows


def change(rows, guid, **fields):
    return [r._replace(**fields) if r.guid == guid else r for r in rows]


def usable(bs, disk=DISK):
    array = 16384 // bs
    return 2 + array, disk // bs - 2 - array


def entry_array(rows, bs):
    array = bytearray(128 * 128)
    for r in rows:
        array[r.slot * 128:(r.slot + 1) * 128] = (
            uuid.UUID(r.type).bytes_le + uuid.UUID(r.guid).bytes_le +
            struct.pack('<QQQ', r.offset // bs, (r.offset + r.size) // bs - 1, attrs_of(r)) +
            r.name.encode('utf-16le').ljust(72, b'\0'))
    return bytes(array)


def windows(rows, bs=512, disk=DISK, guid=DISK_GUID, backup_rows=None, mbr=True):
    """The first and last MiB of the disk: protective MBR, both GPT headers and both entry arrays."""
    array = 16384 // bs
    last_block = disk // bs - 1
    first_usable, last_usable = usable(bs, disk)
    first, last = bytearray(MIB), bytearray(MIB)
    if mbr:
        first[446:462] = bytes([0, 0, 2, 0, 0xEE, 0xFF, 0xFF, 0xFF, 1, 0, 0, 0, 0xFF, 0xFF, 0xFF, 0xFF])
        first[510:512] = b'\x55\xaa'
    base = disk - MIB
    primary, backup = entry_array(rows, bs), entry_array(rows if backup_rows is None else backup_rows, bs)
    for mine, other, entries, arr in ((1, last_block, 2, primary), (last_block, 1, last_block - array, backup)):
        h = bytearray(struct.pack('<8sIIIIQQQQ16sQIII', b'EFI PART', 0x10000, 92, 0, 0, mine, other, first_usable,
                                  last_usable, uuid.UUID(guid).bytes_le, entries, 128, 128, zlib.crc32(arr)))
        struct.pack_into('<I', h, 16, zlib.crc32(h))
        if mine == 1:
            first[bs:bs + 92] = h
            first[2 * bs:2 * bs + len(arr)] = arr
        else:
            last[entries * bs - base:entries * bs - base + len(arr)] = arr
            last[mine * bs - base:mine * bs - base + 92] = h
    return bytes(first), bytes(last)


def survey_of(rows, bs=512, eui=EUI, disk=DISK, guid=DISK_GUID, **kw):
    d = gs.raw_windows(*windows(rows, bs, disk, guid, **kw), disk, bs)
    if eui:
        d['path_info'] = {'nvme_eui_bytes': eui}
    return json.loads(json.dumps({'disks': [d]}))


def blank_survey(bs=512):
    return json.loads(json.dumps({'disks': [gs.raw_windows(bytes(MIB), bytes(MIB), DISK, bs)]}))


def ranges(rows, bs):
    return [(r, r.offset // bs, (r.offset + r.size) // bs - 1) for r in sorted(rows, key=lambda r: r.offset)]


def win_record(rows, bs=512, disk=DISK, guid=DISK_GUID, eui=EUI, reader=None, largest=None):
    free = gs.unallocated([dict(first_lba=a, last_lba=b) for _, a, b in ranges(rows, bs)], *usable(bs, disk), bs)
    record = dict(Size=disk, LogicalSectorSize=bs, Guid='{' + guid.upper() + '}', PartitionStyle='GPT',
                  LargestFreeExtent=max((u['aligned_bytes'] for u in free), default=0) if largest is None else largest)
    if eui:
        record['UniqueId'] = 'eui.' + eui
    doc = dict(Disk=record, Partitions=[dict(Guid='{' + r.guid.upper() + '}', Offset=r.offset, Size=r.size,
                                              GptType='{' + r.type.upper() + '}') for r in sorted(rows, key=lambda r: r.offset)])
    if reader:
        doc['reader'] = reader
    return doc


def norm_record(rows, label, bs=512, disk=DISK, guid=DISK_GUID, eui=None, detail=2):
    """detail 0: block size and LBAs only; 1: adds GUIDs and types; 2: adds attributes, names, slots and disk facts."""
    first_usable, last_usable = usable(bs, disk)
    d = dict(block_size=bs, partition_style='GPT')
    if detail:
        d.update(disk_bytes=disk, disk_guid=guid, first_usable=first_usable, last_usable=last_usable)
    if eui:
        d['nvme_eui'] = eui
    parts = []
    for r, a, b in ranges(rows, bs):
        q = dict(first_lba=a, last_lba=b)
        if detail >= 1:
            q.update(guid=r.guid, type=r.type)
        if detail >= 2:
            q.update(index=r.slot, attributes=attrs_of(r), name=r.name)
        parts.append(q)
    return dict(format=FORMAT, reader=label, disk=d, partitions=parts)


def readers_for(rows, bs=512, eui=EUI):
    """The four X6 readers of one layout: Windows' view, a raw Mac capture, the firmware survey, a later Windows boot."""
    return [('windows', win_record(rows, bs)), ('mac-raw', survey_of(rows, bs, eui=None)),
            ('q1n1', survey_of(rows, bs, eui=eui)), ('windows-boot', win_record(rows, bs, reader='windows-boot'))]


def applied(rows, p, guid=NEW_GUID, slot=None, first=None, last=None, type=None, name=NEW_NAME, attrs=0):
    n, bs = p['new'], p['disk']['block']
    a, b = n['first_lba'] if first is None else first, n['last_lba'] if last is None else last
    if slot is None:
        slot = min(i for i in range(128) if i not in {r.slot for r in rows})
    return rows + [Row(slot, guid, type or n['type'], a * bs, (b - a + 1) * bs, name, attrs)]


def run_main(argv, module=None):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = (module or pm).main([str(a) for a in argv])
        except SystemExit as e:
            rc = e.code
    return rc, out.getvalue(), err.getvalue()


class Work:
    """A scratch directory under build/ for the files a command-line run reads and writes."""

    def __init__(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self._tmp = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.root = Path(self._tmp.name)

    def write(self, name, doc, encoding='utf-8', prefix=b''):
        text = doc if isinstance(doc, str) else json.dumps(doc, indent=1) + '\n'
        path = self.root / name
        path.write_bytes(prefix + text.encode(encoding))
        return path

    def close(self):
        self._tmp.cleanup()


def afters(paths):
    return [x for p in paths for x in ('--after', str(p))]


def ieee(n):
    """A size the way gdisk prints it: one decimal place in the largest binary unit that fits."""
    for unit, size in (('PiB', 1 << 50), ('TiB', 1 << 40), ('GiB', 1 << 30), ('MiB', 1 << 20), ('KiB', 1 << 10)):
        if n >= size:
            return f'{n / size:.1f} {unit}'
    return f'{n} bytes'


def sgdisk_text(rows, bs, disk=DISK, guid=DISK_GUID, scan=False, free_delta=0, table_end_delta=0, entries=128,
                usable_last_delta=0):
    """Synthetic `sgdisk -p` (or, with scan, `gdisk -l`) text; the *_delta arguments make it wrong on purpose."""
    first_usable, last_usable = usable(bs, disk)
    array = 16384 // bs
    parts = sorted(rows, key=lambda r: r.offset)
    free = last_usable - first_usable + 1 - sum(r.size // bs for r in parts) + free_delta
    lines = []
    if scan:
        lines += ['GPT fdisk (gdisk) version 1.0.10', '', 'Partition table scan:', '  MBR: protective',
                  '  BSD: not present', '  APM: not present', '  GPT: present', '',
                  'Found valid GPT with protective MBR; using GPT.']
    lines += [f'Disk example.img: {disk // bs} sectors, {ieee(disk)}', 'Model: Example Disk',
              f'Sector size (logical/physical): {bs}/{bs} bytes', f'Disk identifier (GUID): {guid.upper()}',
              f'Partition table holds up to {entries} entries',
              f'Main partition table begins at sector 2 and ends at sector {1 + array + table_end_delta}',
              f'First usable sector is {first_usable}, last usable sector is {last_usable + usable_last_delta}',
              f'Partitions will be aligned on {MIB // bs}-sector boundaries',
              f'Total free space is {free} sectors ({ieee(free * bs)})', '',
              'Number  Start (sector)    End (sector)  Size       Code  Name']
    for r in parts:
        first = r.offset // bs
        lines.append(f'{r.slot + 1:>4} {first:>15} {first + r.size // bs - 1:>15}   {ieee(r.size):<10} '
                     f'{GDISK_CODE.get(r.type, "FFFF")}  {r.name}')
    return '\n'.join(lines) + '\n'


def gpt_show_table(rows, bs, disk=DISK):
    """The rows `gpt show` lists, as (start, size, index, contents), free space included."""
    total, array = disk // bs, 16384 // bs
    first_usable, last_usable = usable(bs, disk)
    table = [(0, 1, None, 'PMBR'), (1, 1, None, 'Pri GPT header'), (2, array, None, 'Pri GPT table')]
    cursor = first_usable
    for r in sorted(rows, key=lambda r: r.offset):
        first, size = r.offset // bs, r.size // bs
        if first > cursor:
            table.append((cursor, first - cursor, None, ''))
        table.append((first, size, r.slot + 1, f'GPT part - {r.type.upper()}'))
        cursor = first + size
    if cursor <= last_usable:
        table.append((cursor, last_usable + 1 - cursor, None, ''))
    return table + [(last_usable + 1, array, None, 'Sec GPT table'), (total - 1, 1, None, 'Sec GPT header')]


def gpt_show_text(table, bs, disk=DISK, media=True):
    lines = [f'gpt show: example: mediasize={disk}; sectorsize={bs}; blocks={disk // bs}'] if media else []
    lines.append('       start        size  index  contents')
    for start, size, index, contents in table:
        lines.append(f'{start:>12}{size:>12}{"" if index is None else index:>7}  {contents}'.rstrip())
    return '\n'.join(lines) + '\n'


def expected_sgdisk_doc(rows, label, bs):
    first_usable, last_usable = usable(bs)
    return dict(format=FORMAT, reader=label,
                disk=dict(block_size=bs, disk_bytes=DISK, disk_guid=DISK_GUID, first_usable=first_usable,
                          last_usable=last_usable, partition_style='GPT'),
                partitions=[dict(first_lba=a, last_lba=b, gdisk_type_code=GDISK_CODE.get(r.type, 'FFFF'))
                            for r, a, b in ranges(rows, bs)])


def expected_gpt_show_doc(rows, label, bs):
    return dict(format=FORMAT, reader=label, disk=dict(block_size=bs, disk_bytes=DISK, partition_style='GPT'),
                partitions=[dict(first_lba=a, last_lba=b, type=r.type) for r, a, b in ranges(rows, bs)])


def a16_outputs(module, extra, work):
    """Every step of the A16 command line on the existing A16 fixtures, as (exit code, sha256 of stdout, of stderr)."""
    digest = lambda text: hashlib.sha256(text.encode()).hexdigest()
    fixture = a16.LAYOUT
    swapped = [(3 if s == 16 else 16 if s == 3 else s,) + tuple(rest) for s, *rest in fixture]
    before = work.write('a16-before.json', a16.survey(fixture))
    plan_path = work.root / 'a16-plan.json'
    got = {}

    def step(name, argv):
        rc, out, err = run_main(argv + extra, module)
        got[name] = (rc, digest(out), digest(err))

    step('plan', ['plan', '--survey', before, '--out', plan_path])
    got['plan-file'] = digest(plan_path.read_text())
    plan = json.loads(plan_path.read_text())

    def after(rows):
        return work.write('a16-after.json', a16.survey(a16.applied(rows, plan)))

    step('verify', ['verify', '--before', before, '--after', after(fixture), '--plan', plan_path])
    step('verify-no-eui', ['verify', '--before', before, '--after', after(fixture), '--plan', plan_path, '--no-eui'])
    step('verify-reordered', ['verify', '--before', before, '--after', after(swapped), '--plan', plan_path])
    step('verify-nothing-applied', ['verify', '--before', before, '--after', before, '--plan', plan_path])
    step('undo', ['verify-undo', '--before', before, '--after', before])
    step('undo-applied', ['verify-undo', '--before', before, '--after', after(fixture)])
    return got


EMPTY = hashlib.sha256(b'').hexdigest()
# Recorded from the unmodified planner on the existing A16 fixtures: plan, verify and verify-undo of the A16 profile.
A16_GOLDEN = {
    'plan': (0, 'c9cc4b5102062783984e936882b8aad7064b1e2184a85932aa87a1124a04fe45', EMPTY),
    'plan-file': '18ea59cc9e37185c835163946ff538d6bb307f159539117ef72aebb262019fc5',
    'verify': (0, 'c26de83abdc9496cd1301470918ec39ecca1cf389ef0ae1c6504da1800d1c431', EMPTY),
    'verify-no-eui': (0, 'c26de83abdc9496cd1301470918ec39ecca1cf389ef0ae1c6504da1800d1c431', EMPTY),
    'verify-reordered': (0, 'a683d93446fc8ca7c731dc33351ff95bce949cfd24f7a3f429f7dca021a9effc', EMPTY),
    'verify-nothing-applied': (1, 'e19fd29b388066080d19b798cadaf5840de734d5ab280d4c75f90d752d306db1', EMPTY),
    'undo': (0, 'c26de83abdc9496cd1301470918ec39ecca1cf389ef0ae1c6504da1800d1c431', EMPTY),
    'undo-applied': (1, 'c173529447956f9bb67b695ad8c41de6a572e7894b6632b5b9d45e6c6728f50a', EMPTY),
}


class A16Unchanged(unittest.TestCase):
    """Without --profile, and with --profile a16, the planner prints and writes exactly what it always did."""

    def outputs(self, module, extra):
        work = Work()
        self.addCleanup(work.close)
        return a16_outputs(module, extra, work)

    def test_the_default_profile_reproduces_the_golden_outputs(self):
        self.assertEqual(self.outputs(pm, []), A16_GOLDEN)

    def test_naming_the_a16_profile_changes_nothing(self):
        self.assertEqual(self.outputs(pm, ['--profile', 'a16']), A16_GOLDEN)

    def test_the_short_goldens_are_the_words_the_operator_has_always_seen(self):
        digest = lambda text: hashlib.sha256(text.encode()).hexdigest()
        self.assertEqual(A16_GOLDEN['verify'][1], digest('PASS\n'))
        self.assertEqual(A16_GOLDEN['undo'][1], digest('PASS\n'))
        self.assertEqual(A16_GOLDEN['verify-nothing-applied'][1],
                         digest('FAIL: expected one new partition, found 0\n1 problem(s)\n'))
        self.assertEqual(A16_GOLDEN['undo-applied'][1],
                         digest('FAIL: partition set differs from BEFORE\nFAIL: free extents differ from BEFORE\n'
                                '2 problem(s)\n'))

    @unittest.skipUnless(os.environ.get('SNAPMAC_PLAN_BASELINE'),
                         'set SNAPMAC_PLAN_BASELINE to an unmodified tools/snapmac-partition-plan.py to compare directly')
    def test_the_unmodified_planner_gives_the_same_outputs(self):
        baseline = load('snapmac_partition_plan_baseline', os.environ['SNAPMAC_PLAN_BASELINE'])
        reference = self.outputs(baseline, [])
        self.assertEqual(reference, A16_GOLDEN)
        self.assertEqual(self.outputs(pm, []), reference)


class SectorCases:
    """Cases that must hold for 512e and 4Kn disks alike; the concrete classes set `bs`."""
    bs = 512

    def setUp(self):
        self.rows = layout()
        self.before = self.survey(self.rows)
        self.plan = pm.plan_sp11(self.before)

    def survey(self, rows, **kw):
        return survey_of(rows, self.bs, **kw)

    def readers(self, rows, **kw):
        return readers_for(rows, self.bs, **kw)

    def verify(self, rows=None, readers=None, plan=None, **kw):
        notes, verdict = [], {}
        problems = pm.verify_sp11(self.before, self.readers(rows) if readers is None else readers,
                                  self.plan if plan is None else plan, notes=notes, verdict=verdict, **kw)
        return problems, notes, verdict

    def has(self, problems, fragment):
        self.assertTrue(any(fragment in p for p in problems), f'{fragment!r} is not in {problems}')

    def numbers(self):
        lo, hi, guard = 200 * GIB // self.bs, (DISK - GIB - MIB) // self.bs - 1, MIB // self.bs
        return dict(lo=lo, hi=hi, first=lo + guard, last=hi - guard)

    def test_numbers(self):
        n, e, want = self.plan['new'], self.plan['extent'], self.numbers()
        self.assertEqual((e['first_lba'], e['last_lba'], e['offset'], e['bytes']),
                         (want['lo'], want['hi'], 200 * GIB, DISK - GIB - MIB - 200 * GIB))
        self.assertEqual((n['first_lba'], n['last_lba'], n['offset'], n['bytes'], n['end']),
                         (want['first'], want['last'], 200 * GIB + MIB, 311 * GIB - 3 * MIB, DISK - GIB - 2 * MIB))
        self.assertEqual((n['expected_index'], n['type'], n['name'], n['guard_bytes'], n['alignment_bytes']),
                         (4, pm.TYPE, NEW_NAME, MIB, 16384))
        self.assertEqual((e['before_neighbour'], e['after_neighbour']), (WINC, REC))
        self.assertEqual((self.plan['profile'], self.plan['format'], self.plan['layout']['notes']), ('sp11', 1, []))
        self.assertEqual(self.plan['disk'], dict(bytes=DISK, block=self.bs, gpt_guid=DISK_GUID, eui=EUI,
                                                  first_usable=usable(self.bs)[0], last_usable=usable(self.bs)[1]))
        self.assertEqual([q['guid'] for q in self.plan['protected']], [ESP, MSR, WINC, REC])
        self.assertEqual(self.plan['constraints'], dict(min_bytes=128 << 30, alignment_bytes=16384, guard_bytes=MIB))

    def test_every_unit_value_comes_from_before(self):
        disk, guid, eui, bs = 1024 * GIB, G(2), '1122334455667788', self.bs
        rows = [Row(0, G(20), T_ESP, 4 * MIB, 100 * MIB, 'ESP two'), Row(1, G(21), T_MSR, 104 * MIB, 16 * MIB, 'MSR two'),
                Row(2, G(22), T_BASIC, 120 * MIB, 300 * GIB - 120 * MIB, 'C two'),
                Row(5, G(23), T_RECOVERY, disk - 2 * GIB, GIB, 'Recovery two')]
        before = survey_of(rows, bs, eui=eui, disk=disk, guid=guid)
        p = pm.plan_sp11(before)
        lo, hi = 300 * GIB // bs, (disk - 2 * GIB) // bs - 1
        self.assertEqual(p['disk'], dict(bytes=disk, block=bs, gpt_guid=guid, eui=eui, first_usable=usable(bs, disk)[0],
                                         last_usable=usable(bs, disk)[1]))
        self.assertEqual((p['extent']['first_lba'], p['extent']['last_lba'], p['extent']['before_neighbour'],
                          p['extent']['after_neighbour'], p['new']['expected_index']), (lo, hi, G(22), G(23), 3))
        self.assertEqual((p['new']['first_lba'], p['new']['last_lba']), (lo + MIB // bs, hi - MIB // bs))
        self.assertEqual([q['guid'] for q in p['protected']], [G(20), G(21), G(22), G(23)])
        text = json.dumps(p) + '\n'.join(pm.plan_text(p))
        for fixture_value in (DISK_GUID, EUI, str(DISK), ESP, WINC, REC):
            self.assertNotIn(fixture_value, text)
        after = applied(rows, p)
        four = [('windows', win_record(after, bs, disk, guid, eui)),
                ('mac-raw', survey_of(after, bs, eui=None, disk=disk, guid=guid)),
                ('q1n1', survey_of(after, bs, eui=eui, disk=disk, guid=guid)),
                ('windows-boot', win_record(after, bs, disk, guid, eui, reader='windows-boot'))]
        self.assertEqual(pm.verify_sp11(before, four, p), [])
        self.assertEqual(pm.verify_undo_sp11(before, [('windows', win_record(rows, bs, disk, guid, eui)),
                                                      ('mac-raw', survey_of(rows, bs, eui=None, disk=disk, guid=guid)),
                                                      ('q1n1', survey_of(rows, bs, eui=eui, disk=disk, guid=guid)),
                                                      ('windows-boot', win_record(rows, bs, disk, guid, eui,
                                                                                  reader='windows-boot'))], p), [])

    def test_plan_is_numbers_not_commands(self):
        text = json.dumps(self.plan) + '\n'.join(pm.plan_text(self.plan))
        for word in ('New-Partition', 'Resize-Partition', 'diskpart', 'sgdisk', 'gpt add', 'diskutil', 'create partition'):
            self.assertNotIn(word, text)
        self.assertNotIn('windows', self.plan)
        s = self.plan['safety']
        self.assertEqual((s['requires_authorization'], s['requires_verified_backup'], s['executor'], s['tool_writes_disk'],
                          s['emits_commands'], s['bitlocker_recovery_key_off_device']), (True, True, 'operator', False, False, True))
        self.assertIn('REQUIRES AUTHORIZATION', text)
        self.assertIn('docs/SURFACE-PRO-11.md section 8', text)
        self.assertIn('REQUIRES A VERIFIED BACKUP', text)
        self.assertIn('docs/SURFACE-PRO-11.md sections 1.1 (N7) and 6.1 (X0)', text)
        self.assertIn('operator creates the entry with the Windows partition manager; this tool does not', text)
        self.assertIn('BitLocker: keep the BitLocker recovery key for C: off the device', text)
        self.assertIn('emits no command', text)

    def test_the_exact_change_verifies_with_four_readers(self):
        problems, notes, verdict = self.verify(applied(self.rows, self.plan))
        self.assertEqual((problems, notes), ([], []))
        r = verdict['readers']
        self.assertEqual((verdict['ok'], verdict['mode'], verdict['profile']), (True, 'verify', 'sp11'))
        self.assertEqual((r['supplied'], r['distinct'], r['agreeing'], r['required'], r['four_reader_gate_met']),
                         (4, 4, 4, 4, True))
        self.assertEqual(r['labels'], ['windows', 'mac-raw', 'q1n1', 'windows-boot'])
        self.assertEqual(r['full'], ['mac-raw', 'q1n1'])
        self.assertEqual(verdict['new_partition'], dict(guid=NEW_GUID, index=4, attributes=0, name=NEW_NAME,
                                                        first_lba=self.numbers()['first'], last_lba=self.numbers()['last']))

    def test_the_new_name_is_a_label_not_a_requirement(self):
        problems, _, verdict = self.verify(applied(self.rows, self.plan, name='Whatever Windows left'))
        self.assertEqual(problems, [])
        self.assertEqual(verdict['new_partition']['name'], 'Whatever Windows left')

    def test_a_moved_slot_is_a_note_not_silence(self):
        moved = change(self.rows, WINC, slot=7)
        problems, notes, _ = self.verify(applied(moved, self.plan))
        self.assertEqual(problems, [])
        self.assertEqual(notes, [f'reader {label}: partition {WINC} moved from GPT slot 2 to 7 (identity unchanged)'
                                 for label in ('mac-raw', 'q1n1')])

    def refuses(self, fragment, rows=None, survey=None, **kw):
        s = self.survey(rows) if survey is None else survey
        with self.assertRaisesRegex(pm.PlanError, re.escape(fragment)):
            pm.plan_sp11(s, **kw)

    def edited(self, edit, clear_errors=True):
        s = copy.deepcopy(self.before)
        d = s['disks'][0]
        if clear_errors:
            d['errors'] = []
        edit(d)
        return s

    def test_refuses_when_no_extent_is_big_enough(self):
        rows = layout(c_end=400 * GIB)
        self.refuses('no free extent holds', rows)
        self.refuses('no free extent holds', layout(c_end=DISK - GIB - MIB))
        self.refuses('free extents: LBA', rows)
        lo, hi = 400 * GIB // self.bs, (DISK - GIB - MIB) // self.bs - 1
        self.refuses('inside its guards', rows, extent=(lo, hi))
        self.assertEqual(pm.plan_sp11(self.survey(rows), (lo, hi), min_bytes=64 * GIB)['new']['bytes'], 111 * GIB - 3 * MIB)
        self.refuses('minimum size', rows, min_bytes=0)
        self.refuses('not an integer', rows, min_bytes='big')

    def test_two_qualifying_extents_need_a_choice(self):
        rows = self.rows + [Row(4, G(14), T_BASIC, 300 * GIB, 50 * GIB, 'D')]
        s, bs = self.survey(rows), self.bs
        first, second = (200 * GIB // bs, 300 * GIB // bs - 1), (350 * GIB // bs, (DISK - GIB - MIB) // bs - 1)
        with self.assertRaises(pm.AmbiguousExtent) as ctx:
            pm.plan_sp11(s, min_bytes=40 * GIB)
        self.assertEqual(ctx.exception.extents, [first, second])
        self.assertIn('--extent-start', str(ctx.exception))
        self.assertEqual(pm.plan_sp11(s)['extent']['before_neighbour'], G(14))
        p = pm.plan_sp11(s, first, min_bytes=40 * GIB)
        self.assertEqual((p['extent']['before_neighbour'], p['extent']['after_neighbour']), (WINC, G(14)))
        self.assertEqual(len(p['layout']['notes']), 2)
        self.refuses('is not a whole free extent', rows, extent=(first[0] + 1, first[1]))
        self.refuses('is not a whole free extent', rows, extent=(first[0], second[1]))

    def test_refuses_a_partition_that_is_not_16_kib_aligned(self):
        self.refuses('is not 16384-byte aligned', layout(c_end=200 * GIB + 4096))

    def test_the_start_and_the_size_are_each_checked_for_16_kib_alignment(self):
        shifted = DISK - GIB - MIB + 4096
        for name, rows in (('only the start', change(layout(c_end=200 * GIB + 4096), REC, offset=shifted)),
                           ('only the size', change(layout(), REC, offset=shifted)),
                           ('both', layout(c_end=200 * GIB + 4096))):
            with self.subTest(name):
                self.refuses('is not 16384-byte aligned', rows)

    def test_an_extent_too_small_for_its_guards_is_refused_for_the_right_reason(self):
        bs, lo = self.bs, 200 * GIB // self.bs

        def gap(extra):
            return change(layout(), REC, offset=200 * GIB + extra), (lo, lo + extra // bs - 1)

        rows, extent = gap(MIB)
        self.refuses('the extent gives 0 bytes inside its guards, 1 required', rows, extent=extent, min_bytes=1)
        rows, extent = gap(2 * MIB)
        self.refuses('the extent gives 0 bytes inside its guards, 1 required', rows, extent=extent, min_bytes=1)
        rows, extent = gap(2 * MIB + bs)
        self.refuses('is not 16384-byte aligned', rows, extent=extent, min_bytes=1)
        self.refuses(f'the extent gives {bs} bytes inside its guards, {bs + 1} required', rows, extent=extent, min_bytes=bs + 1)

    def test_the_minimum_size_may_be_met_exactly_and_must_be_positive(self):
        exact, e = self.plan['new']['bytes'], self.plan['extent']
        chosen = (e['first_lba'], e['last_lba'])
        self.assertEqual(pm.plan_sp11(self.before, min_bytes=exact)['new']['bytes'], exact)
        self.assertEqual(pm.plan_sp11(self.before, chosen, min_bytes=exact)['new']['bytes'], exact)
        self.refuses('no free extent holds', survey=self.before, min_bytes=exact + 1)
        self.refuses('inside its guards', survey=self.before, extent=chosen, min_bytes=exact + 1)
        self.assertEqual(pm.plan_sp11(self.before, min_bytes=1)['constraints']['min_bytes'], 1)

    def test_refuses_an_extent_beside_the_backup_gpt(self):
        self.refuses('beside the backup GPT', layout(recovery=False))

    def test_refuses_an_extent_at_the_first_usable_lba(self):
        rows = layout(c_end=400 * GIB, esp_start=140 * GIB)
        self.refuses('first usable LBA', rows)
        self.refuses('first usable LBA', rows, extent=(usable(self.bs)[0], 140 * GIB // self.bs - 1))

    def test_the_extent_must_be_bounded_by_one_partition_on_each_side(self):
        # The disk validator already refuses overlaps, so this guard is reachable only through facts that it passed
        # wrongly; it is exercised by tampering with them after validation.
        validated = pm.sp11_disk

        def tampered(edit):
            def check(disk):
                facts = validated(disk)
                edit(facts)
                return facts
            return check

        def second_neighbour_before(facts):
            before = next(q for q in facts['parts'] if q['guid'] == WINC)
            facts['parts'].append(dict(before, guid=G(15), index=15))

        def no_neighbour_after(facts):
            facts['parts'] = [q for q in facts['parts'] if q['guid'] != REC]

        for name, edit in (('two partitions end the extent', second_neighbour_before),
                           ('no partition after the extent', no_neighbour_after)):
            with self.subTest(name), mock.patch.object(pm, 'sp11_disk', side_effect=tampered(edit)):
                with self.assertRaises(pm.PlanError) as caught:
                    pm.plan_sp11(self.before)
                self.assertEqual(str(caught.exception), 'the extent is not bounded by one partition on each side')

    def test_refuses_a_disk_the_survey_cannot_vouch_for(self):
        bad_crc = self.survey(self.rows)
        bad_crc['disks'][0]['backup']['array_crc_ok'] = False
        other_backup = self.survey(self.rows, backup_rows=change(self.rows, WINC, name='changed'))
        cases = [('backup CRC', bad_crc, 'failed its CRC'),
                 ('backup differs', other_backup, 'primary/backup differ'),
                 ('no protective MBR', self.survey(self.rows, mbr=False), 'protective MBR'),
                 ('no GPT', blank_survey(self.bs), 'GPT header is missing'),
                 ('overlap', self.survey(self.rows + [Row(4, G(14), T_BASIC, 100 * GIB, GIB, 'x')]), 'overlap'),
                 ('beyond the usable range', self.survey(self.rows + [Row(4, G(14), T_BASIC, DISK - 8192, 4096, 'x')]),
                  'outside the usable LBA range'),
                 ('duplicate GUID', self.survey(self.rows + [Row(4, WINC, T_BASIC, 300 * GIB, GIB, 'x')]),
                  'duplicate partition GUID'),
                 ('no ESP', self.survey([r for r in self.rows if r.guid != ESP]), 'no EFI system partition'),
                 ('no basic data', self.survey([r for r in self.rows if r.guid != WINC]), 'no basic-data partition')]
        for name, s, fragment in cases:
            with self.subTest(name):
                self.refuses(fragment, survey=s)

    def test_free_space_of_one_sector_is_still_free_space(self):
        part = lambda a, b: dict(first_lba=a, last_lba=b)
        self.assertEqual(pm.free_extents([part(5, 9)], 4, 10), [(4, 4), (10, 10)])
        self.assertEqual(pm.free_extents([part(7, 9), part(4, 5)], 4, 10), [(6, 6), (10, 10)])
        self.assertEqual(pm.free_extents([part(4, 10)], 4, 10), [])
        self.assertEqual(pm.free_extents([], 4, 4), [(4, 4)])

    def test_partitions_may_touch_both_ends_of_the_usable_range_and_be_one_sector_long(self):
        bs, (first, last) = self.bs, usable(self.bs)
        facts = lambda rows: pm.sp11_disk(self.survey(rows, eui=None)['disks'][0])
        span = lambda f: [(q['first_lba'], q['last_lba']) for q in f['parts']]
        whole = facts([Row(0, G(40), T_BASIC, first * bs, (last - first + 1) * bs, 'whole usable range')])
        self.assertEqual((span(whole), whole['free']), ([(first, last)], []))
        one = facts([Row(0, G(41), T_BASIC, first * bs, bs, 'one sector')])
        self.assertEqual((span(one), one['free']), ([(first, first)], [(first + 1, last)]))
        gaps = facts([Row(0, G(42), T_BASIC, (first + 1) * bs, 3 * bs, 'a'),
                      Row(1, G(43), T_BASIC, (first + 5) * bs, (last - first - 5) * bs, 'b')])
        self.assertEqual(gaps['free'], [(first, first), (first + 4, first + 4), (last, last)])

    def test_independent_checks_catch_an_edited_survey(self):
        # The decoder's own `errors` list is cleared: the planner must find each problem again.
        cases = [
            ('backup disk GUID', lambda d: d['backup'].update(disk_guid=G(2)), 'disagree on disk_guid'),
            ('backup usable range', lambda d: d['backup'].update(last_usable=d['backup']['last_usable'] - 1), 'disagree on last_usable'),
            ('backup array CRC', lambda d: d['backup'].update(array_crc=1), 'disagree on array_crc'),
            ('header LBA', lambda d: d['primary'].update(my_lba=2), 'do not sit at LBA 1'),
            ('survey GUID', lambda d: d.update(disk_guid=G(2)), 'survey disk_guid differs'),
            ('unallocated list', lambda d: d['unallocated'][-1].update(last_lba=d['unallocated'][-1]['last_lba'] - 1),
             'unallocated list differs'),
            ('overlap', lambda d: d['partitions'][1].update(first_lba=d['partitions'][0]['last_lba']), 'overlap'),
            ('outside usable range', lambda d: d['partitions'][-1].update(last_lba=d['last_usable'] + 5),
             'outside the usable LBA range'),
            ('duplicate GUID', lambda d: d['partitions'][1].update(guid=d['partitions'][0]['guid']), 'share a GUID'),
            ('duplicate slot', lambda d: d['partitions'][1].update(index=d['partitions'][0]['index']), 'same GPT slot'),
            ('inverted range', lambda d: d['partitions'][1].update(last_lba=d['partitions'][1]['first_lba'] - 1),
             'first LBA after last LBA'),
            ('nil type', lambda d: d['partitions'][1].update(type=NIL), 'nil GUID or type'),
            ('slot out of range', lambda d: d['partitions'][1].update(index=128), 'slot or attributes out of range'),
            ('attributes out of range', lambda d: d['partitions'][1].update(attributes=1 << 64), 'slot or attributes out of range'),
            ('inconsistent size', lambda d: d.update(disk_bytes=d['disk_bytes'] + 512), 'inconsistent'),
            ('odd sector size', lambda d: d.update(block_size=1024), 'neither 512 nor 4096'),
            ('zero sector size', lambda d: d.update(block_size=0), 'neither 512 nor 4096'),
            ('one-block disk: consistent, but with no room for a GPT',
             lambda d: d.update(blocks=1, disk_bytes=d['block_size'], last_block=0), 'usable LBA range is empty or overlaps'),
            ('boolean as integer', lambda d: d.update(blocks=True), 'not an integer'),
            ('no partition list', lambda d: d.pop('partitions'), 'no partition list'),
            ('field missing', lambda d: d['partitions'][0].pop('name'), 'malformed survey'),
            ('name not text', lambda d: d['partitions'][0].update(name=5), 'name is not text'),
            ('no errors list', lambda d: d.pop('errors'), 'no errors list'),
            ('an error', lambda d: d.update(errors=['boom']), 'survey error: boom'),
            ('CRC never checked', lambda d: d['primary'].pop('array_crc_ok'), 'never checked'),
            ('entry count', lambda d: d['backup'].update(count=64), 'not 128 x 128-byte entries'),
            ('nil disk GUID', lambda d: [x.update(disk_guid=NIL) for x in (d, d['primary'], d['backup'])], 'GUID is nil'),
            ('usable range inside the primary array', lambda d: [x.update(first_usable=3) for x in (d, d['primary'], d['backup'])],
             'usable LBA range is empty or overlaps'),
            ('usable range inside the backup array',
             lambda d: [x.update(last_usable=d['last_block'] - 1) for x in (d, d['primary'], d['backup'])],
             'usable LBA range is empty or overlaps'),
        ]
        for name, edit, fragment in cases:
            with self.subTest(name):
                self.refuses(fragment, survey=self.edited(edit))
        for bad in ([], 5, {'disks': 5}, {'disks': [5]}):
            with self.subTest(repr(bad)):
                with self.assertRaises(pm.PlanError):
                    pm.plan_sp11(bad)

    def test_refuses_bad_new_types_and_names(self):
        for t in (NIL, T_ESP, T_MSR, T_BASIC, T_RECOVERY, 'not-a-guid', None):
            with self.subTest(type=t):
                self.refuses('new partition type', survey=self.before, new_type=t)
        self.refuses('already exists', survey=self.survey(applied(self.rows, self.plan)))
        for name in ('', 'x' * 37, 'a\x00b', 5, None):
            with self.subTest(name=name):
                self.refuses('name must be 1..36', survey=self.before, new_name=name)
        self.assertEqual(pm.plan_sp11(self.before, new_name='x' * 36)['new']['name'], 'x' * 36)
        other = pm.plan_sp11(self.before, new_type=T_OTHER.upper())
        self.assertEqual(other['new']['type'], T_OTHER)

    def test_the_nvme_eui_is_required_unless_waived(self):
        s = self.survey(self.rows, eui=None)
        self.refuses('carries no NVMe EUI64', survey=s)
        self.refuses('--no-eui', survey=s)
        waived = pm.plan_sp11(s, require_eui=False)
        self.assertIsNone(waived['disk']['eui'])

    def test_refuses_when_every_gpt_slot_is_used(self):
        tiny = [Row(4 + i, G(100 + i), T_BASIC, 200 * GIB + i * MIB, MIB, f'tiny {i}') for i in range(124)]
        self.refuses('all 128 GPT slots', self.rows + tiny)
        p = pm.plan_sp11(self.survey(self.rows + tiny[:-1]))
        self.assertEqual((p['new']['expected_index'], p['extent']['before_neighbour']), (127, G(100 + 122)))

    def test_the_partition_before_the_extent_must_be_basic_data(self):
        self.refuses('is not basic data', self.rows[:2] + [self.rows[2]._replace(size=100 * GIB - 277 * MIB)] +
                     [Row(4, G(14), T_OTHER, 100 * GIB, 100 * GIB, 'other')] + self.rows[3:])

    def test_selects_the_target_among_several_disks(self):
        second = survey_of(self.rows, self.bs, eui='1122334455667788', guid=G(2))['disks'][0]
        multi = {'disks': [self.before['disks'][0], second]}
        self.refuses('2 disks in the survey', survey=multi)
        for kw in (dict(disk_guid=DISK_GUID), dict(nvme_eui=EUI), dict(nvme_eui=EUI_REVERSED), dict(nvme_eui='eui.' + EUI.lower()),
                   dict(disk_guid=DISK_GUID.upper(), nvme_eui=EUI)):
            with self.subTest(**kw):
                self.assertEqual(pm.plan_sp11(multi, **kw)['disk']['gpt_guid'], DISK_GUID)
        self.assertEqual(pm.plan_sp11(multi, nvme_eui='1122334455667788')['disk']['gpt_guid'], G(2))
        for kw in (dict(nvme_eui='AAAAAAAAAAAAAAAA'), dict(disk_guid=G(3)), dict(disk_guid=G(2), nvme_eui=EUI)):
            with self.subTest(**kw):
                self.refuses('found 0 of 2', survey=multi, **kw)
        self.refuses('not an NVMe EUI64', survey=multi, nvme_eui='xyz')
        self.refuses('not a GUID', survey=multi, disk_guid='nope')
        self.assertEqual(pm.plan_sp11(self.before['disks'][0])['disk']['gpt_guid'], DISK_GUID)

    def test_the_refusal_to_choose_a_disk_lists_the_gpt_disks_or_says_there_are_none(self):
        other = survey_of(self.rows, self.bs, guid=G(2))['disks'][0]
        with self.assertRaises(pm.PlanError) as caught:
            pm.plan_sp11({'disks': [self.before['disks'][0], other]})
        self.assertTrue(str(caught.exception).endswith(
            f'(GPT disks: {DISK} bytes, GPT GUID {DISK_GUID}; {DISK} bytes, GPT GUID {G(2)})'), str(caught.exception))
        blank = blank_survey(self.bs)['disks'][0]
        with self.assertRaises(pm.PlanError) as caught:
            pm.plan_sp11({'disks': [blank, blank]})
        self.assertTrue(str(caught.exception).endswith('(GPT disks: none)'), str(caught.exception))

    def test_layout_oddities_are_notes_and_survey_warnings_pass_through(self):
        notes = lambda rows, **kw: pm.plan_sp11(self.survey(rows), **kw)['layout']['notes']
        self.assertEqual(notes([r for r in self.rows if r.guid != MSR]),
                         ['no Microsoft reserved partition (unusual for a Windows GPT disk)'])
        tail = Row(4, G(14), T_OTHER, DISK - GIB - MIB, GIB, 'tail')
        self.assertEqual(notes(change(self.rows, REC, offset=DISK - 2 * GIB - MIB) + [tail]),
                         ['the last partition is not a Windows recovery partition'])
        no_recovery = notes(layout(recovery=False) + [tail._replace(slot=3)])
        self.assertEqual(len(no_recovery), 2)
        self.assertIn('no Windows recovery partition', no_recovery)
        warned = self.edited(lambda d: d['warnings'].append('a' * 300), clear_errors=False)
        self.assertEqual(pm.plan_sp11(warned)['layout']['notes'], ['survey warning: ' + 'a' * 200])
        self.assertIn('note: survey warning:', '\n'.join(pm.plan_text(pm.plan_sp11(warned))))


    def ok_rows(self):
        return applied(self.rows, self.plan)

    def test_each_deviation_from_the_plan_is_caught(self):
        n, e = self.plan['new'], self.plan['extent']
        mid = (n['first_lba'] + n['last_lba']) // 2
        good = self.ok_rows()
        two = applied(applied(self.rows, self.plan, last=mid), self.plan, guid=G(91), first=mid + 1)
        cases = [
            ('nothing applied', self.rows, 'expected one new partition, found 0'),
            ('C: shrunk', change(good, WINC, size=self.rows[2].size - MIB), f'existing partition {WINC} changed: last_lba'),
            ('ESP renamed', change(good, ESP, name='renamed'), f'existing partition {ESP} changed: name'),
            ('MSR retyped', change(good, MSR, type=T_OTHER), f'existing partition {MSR} changed: type'),
            ('recovery attributes cleared', change(good, REC, attrs=0), f'existing partition {REC} changed: attributes'),
            ('no guard at the start', applied(self.rows, self.plan, first=e['first_lba']), 'the new partition is LBA'),
            ('no guard at the end', applied(self.rows, self.plan, last=e['last_lba']), 'the new partition is LBA'),
            ('the whole extent', applied(self.rows, self.plan, first=e['first_lba'], last=e['last_lba']),
             'free space inside the old extent is []'),
            ('one sector late', applied(self.rows, self.plan, first=n['first_lba'] + 1), 'the new partition is LBA'),
            ('one sector short', applied(self.rows, self.plan, last=n['last_lba'] - 1), 'the new partition is LBA'),
            ('wrong type', applied(self.rows, self.plan, type=T_OTHER), 'of type ' + T_OTHER),
            ('two new partitions', two, 'expected one new partition, found 2'),
            ('attributes 0x4', applied(self.rows, self.plan, attrs=4), 'the new partition has attributes 0x4'),
            ('recovery deleted', [r for r in good if r.guid != REC], f'existing partition {REC} is gone'),
            ('ESP deleted', [r for r in good if r.guid != ESP], f'existing partition {ESP} is gone'),
        ]
        for name, rows, fragment in cases:
            with self.subTest(name):
                problems, _, verdict = self.verify(rows)
                self.has(problems, fragment)
                self.assertFalse(verdict['ok'])
                self.assertFalse(verdict['readers']['four_reader_gate_met'])
                self.assertEqual(verdict['problems'], problems)

    def test_the_new_partition_may_keep_bit_63(self):
        problems, _, verdict = self.verify(applied(self.rows, self.plan, attrs=1 << 63))
        self.assertEqual(problems, [])
        self.assertEqual(verdict['new_partition']['attributes'], 1 << 63)

    def four(self, rows, **swap):
        """Four distinct readers of `rows`, one of each kind; `swap` replaces a reader by label."""
        bs = self.bs
        readers = [('windows', win_record(rows, bs)), ('mac-diskutil', norm_record(rows, 'mac-diskutil', bs)),
                   ('q1n1', survey_of(rows, bs)), ('windows-boot', win_record(rows, bs, reader='windows-boot'))]
        return [(label, swap.get(label, doc)) for label, doc in readers]

    def test_one_dissenting_reader_stops_the_gate(self):
        good, n, bs = self.ok_rows(), self.plan['new'], self.bs
        problems, _, verdict = self.verify(readers=self.four(good))
        self.assertEqual((problems, verdict['readers']['agreeing'], verdict['readers']['four_reader_gate_met']), ([], 4, True))

        windows = win_record(good, bs)
        next(q for q in windows['Partitions'] if q['Guid'] == '{' + NEW_GUID.upper() + '}')['Offset'] += bs
        normalized = norm_record(good, 'mac-diskutil', bs)
        normalized['partitions'][3]['last_lba'] += 1
        full = survey_of(applied(self.rows, self.plan, last=n['last_lba'] - 1), bs)
        for label, doc in (('windows', windows), ('mac-diskutil', normalized), ('q1n1', full)):
            with self.subTest(label):
                problems, _, verdict = self.verify(readers=self.four(good, **{label: doc}))
                r = verdict['readers']
                self.assertTrue(problems)
                self.assertTrue(all(p.startswith(f'reader {label}: ') for p in problems), problems)
                self.assertEqual((verdict['ok'], r['agreeing'], r['distinct'], r['four_reader_gate_met']), (False, 3, 4, False))

    def test_every_reader_is_projected_onto_the_expected_layout(self):
        good = self.ok_rows()
        windows = win_record(good, self.bs)
        windows['Partitions'].append(dict(windows['Partitions'][0]))
        problems, _, _ = self.verify(readers=self.four(good, windows=windows))
        self.has(problems, 'reader windows: two entries at LBA')
        windows = win_record(good, self.bs)
        del windows['Partitions'][1]
        problems, _, _ = self.verify(readers=self.four(good, windows=windows))
        self.has(problems, 'reader windows: no entry at LBA')
        normalized = norm_record(good, 'mac-diskutil', self.bs)
        normalized['partitions'][0]['name'] = 'renamed'
        normalized['partitions'][1]['attributes'] = 4
        normalized['partitions'][2]['guid'] = G(77)
        normalized['partitions'][4]['type'] = T_OTHER
        problems, _, _ = self.verify(readers=self.four(good, **{'mac-diskutil': normalized}))
        for field in ('name', 'attributes', 'guid', 'type'):
            self.has(problems, f'{field} differs')

    def test_a_document_counts_once(self):
        readers = self.four(self.ok_rows())
        again = readers[:3] + [('windows-again', copy.deepcopy(readers[0][1]))]
        problems, _, verdict = self.verify(readers=again)
        r = verdict['readers']
        self.has(problems, 'reader windows-again: the same document as reader windows; a document counts once')
        self.has(problems, '3 distinct reader document(s), 4 required')
        self.assertEqual((r['supplied'], r['distinct'], r['agreeing'], r['four_reader_gate_met']), (4, 3, 3, False))
        self.assertEqual(r['labels'], ['windows', 'mac-diskutil', 'q1n1'])

    def test_a_label_is_used_once(self):
        readers = self.four(self.ok_rows())
        clash = readers[:3] + [('windows', survey_of(self.ok_rows(), self.bs, eui=None))]
        problems, _, verdict = self.verify(readers=clash)
        self.has(problems, 'reader windows: its label is already used by another reader; give each reader its own')
        self.assertEqual(verdict['readers']['distinct'], 3)

    def test_the_reader_count_is_a_gate_unless_lowered_and_then_it_is_said(self):
        three = self.four(self.ok_rows())[:3]
        problems, notes, verdict = self.verify(readers=three)
        self.assertEqual(problems, ['3 distinct reader document(s), 4 required'])
        problems, notes, verdict = self.verify(readers=three, min_readers=3)
        self.assertEqual(problems, [])
        self.assertEqual(notes, ['partial: --min-readers 3 is below the 4 readers the X6 gate wants'])
        self.assertEqual((verdict['ok'], verdict['readers']['agreeing'], verdict['readers']['four_reader_gate_met']),
                         (True, 3, False))
        problems, _, verdict = self.verify(readers=self.four(self.ok_rows()) + [('extra', win_record(self.ok_rows(), self.bs, reader='extra'))])
        self.assertEqual((problems, verdict['readers']['agreeing'], verdict['readers']['four_reader_gate_met']), ([], 5, True))
        for bad in (0, -1):
            with self.assertRaisesRegex(pm.PlanError, 'at least 1'):
                self.verify(readers=three, min_readers=bad)
        with self.assertRaisesRegex(pm.PlanError, 'not an integer'):
            self.verify(readers=three, min_readers='3')

    def test_a_gate_without_a_full_reader_is_refused(self):
        good = self.ok_rows()
        readers = [('windows', win_record(good, self.bs)), ('mac-diskutil', norm_record(good, 'mac-diskutil', self.bs)),
                   ('windows-boot', win_record(good, self.bs, reader='windows-boot')),
                   ('other', norm_record(good, 'other', self.bs, detail=1))]
        problems, _, verdict = self.verify(readers=readers)
        self.assertEqual(problems, ['no full reader: at least one gpt_survey JSON is needed for the strict checks'])
        self.assertFalse(verdict['readers']['four_reader_gate_met'])
        self.assertIsNone(verdict['new_partition'])

    def test_the_nvme_eui_must_be_confirmed_by_a_reader_unless_waived_and_never_contradicted(self):
        good, bs = self.ok_rows(), self.bs
        quiet = [('windows', win_record(good, bs, eui=None)), ('mac-diskutil', norm_record(good, 'mac-diskutil', bs)),
                 ('mac-raw', survey_of(good, bs, eui=None)), ('windows-boot', win_record(good, bs, eui=None, reader='windows-boot'))]
        problems, _, _ = self.verify(readers=quiet)
        self.assertEqual(problems, ['no reader carries the NVMe EUI64, so the disk is pinned by its GPT GUID alone (--no-eui accepts that)'])
        problems, notes, verdict = self.verify(readers=quiet, require_eui=False)
        self.assertEqual((problems, verdict['ok']), ([], True))
        self.assertEqual(notes, ['no reader carries the NVMe EUI64, so the disk is pinned by its GPT GUID alone'])
        wrong = quiet[:2] + [('q1n1', survey_of(good, bs, eui='1122334455667788'))] + quiet[3:]
        for waive in (True, False):
            problems, _, _ = self.verify(readers=wrong, require_eui=not waive)
            self.has(problems, 'reader q1n1: NVMe EUI64 differs from the plan')
        reversed_order = quiet[:2] + [('q1n1', survey_of(good, bs, eui=EUI_REVERSED))] + quiet[3:]
        self.assertEqual(self.verify(readers=reversed_order)[0], [])
        for text in ('synthetic-unique-id', 'naa.' + EUI.lower(), 5):
            windows = win_record(good, bs, eui=None)
            windows['Disk']['UniqueId'] = text
            problems, _, _ = self.verify(readers=[('windows', windows)] + quiet[1:])
            self.has(problems, 'no reader carries the NVMe EUI64')
        windows = win_record(good, bs, eui=None)
        windows['Disk']['UniqueId'] = 'EUI.' + EUI.lower()
        self.assertEqual(self.verify(readers=[('windows', windows)] + quiet[1:])[0], [])

    def test_a_plan_made_without_an_nvme_eui_is_verified_against_readers_that_have_none(self):
        bs, before = self.bs, self.survey(self.rows, eui=None)
        plan = pm.plan_sp11(before, require_eui=False)
        self.assertIsNone(plan['disk']['eui'])
        readers = lambda rows: [('windows', win_record(rows, bs, eui=None)),
                                ('mac-diskutil', norm_record(rows, 'mac-diskutil', bs)),
                                ('mac-raw', survey_of(rows, bs, eui=None)),
                                ('windows-boot', win_record(rows, bs, eui=None, reader='windows-boot'))]
        notes, verdict = [], {}
        self.assertEqual(pm.verify_sp11(before, readers(applied(self.rows, plan)), plan, notes=notes, verdict=verdict), [])
        self.assertEqual((notes, verdict['ok'], verdict['readers']['four_reader_gate_met']), ([], True, True))
        self.assertEqual(pm.verify_undo_sp11(before, readers(self.rows), plan), [])

    def test_a_windows_record_is_read_for_what_it_states(self):
        good, bs = self.ok_rows(), self.bs
        other = 4096 if bs == 512 else 512

        def with_disk(**fields):
            doc = win_record(good, bs)
            doc['Disk'].update(fields)
            return doc

        cases = [('largest free extent', with_disk(LargestFreeExtent=GIB), 'largest free extent is'),
                 ('disk size', with_disk(Size=DISK + bs), f'disk_bytes is {DISK + bs}, expected {DISK}'),
                 ('disk GUID', with_disk(Guid='{' + G(2).upper() + '}'), f'disk_guid is {G(2)}, expected {DISK_GUID}'),
                 ('sector size', with_disk(LogicalSectorSize=other), f'block_size is {other}, expected {bs}'),
                 ('odd sector size', with_disk(LogicalSectorSize=1024), 'neither 512 nor 4096'),
                 ('MBR', with_disk(PartitionStyle='MBR'), 'is not GPT'),
                 ('RAW', with_disk(PartitionStyle='RAW'), 'is not GPT')]
        for name, doc, fragment in cases:
            with self.subTest(name):
                problems, _, verdict = self.verify(readers=self.four(good, windows=doc))
                self.has(problems, fragment)
                self.assertTrue(all(p.startswith('reader windows: ') or p == '3 distinct reader document(s), 4 required'
                                    for p in problems), problems)
                self.assertEqual((verdict['ok'], verdict['readers']['agreeing']), (False, 3))
        for style in (None, 'GPT', 2):
            doc = with_disk(PartitionStyle=style)
            if style is None:
                del doc['Disk']['PartitionStyle']
            self.assertEqual(self.verify(readers=self.four(good, windows=doc))[0], [])
        doc = win_record(good, bs)
        doc['Partitions'][0]['Size'] += 1
        self.has(self.verify(readers=self.four(good, windows=doc))[0], 'are not whole sectors')
        for name, offset, size in (('a negative offset', -bs, bs), ('an empty partition', bs, 0), ('a negative size', bs, -bs)):
            doc = win_record(good, bs)
            doc['Partitions'][0].update(Offset=offset, Size=size)
            with self.subTest(name):
                self.has(self.verify(readers=self.four(good, windows=doc))[0], 'are not whole sectors')
        doc = win_record(good, bs)
        doc['Partitions'].insert(0, dict(Guid='{' + G(60).upper() + '}', Offset=0, Size=bs, GptType='{' + T_BASIC.upper() + '}'))
        self.assertEqual(self.verify(readers=self.four(good, windows=doc))[0], ['reader windows: unexpected entry at LBA 0..0'])
        for edit, fragment in ((lambda d: d.pop('Disk'), 'needs "Disk" and "Partitions"'),
                               (lambda d: d.update(Partitions={}), 'needs "Disk" and "Partitions"'),
                               (lambda d: d['Partitions'].append(5), 'is not an object'),
                               (lambda d: d['Partitions'][0].update(Offset=True), 'not an integer'),
                               (lambda d: d['Partitions'][0].update(Guid='nope'), 'not a GUID'),
                               (lambda d: d['Partitions'][0].pop('GptType'), 'GptType')):
            doc = win_record(good, bs)
            edit(doc)
            with self.subTest(fragment):
                self.has(self.verify(readers=self.four(good, windows=doc))[0], fragment)

    def test_a_normalized_reader_states_only_what_it_knows(self):
        good, bs = self.ok_rows(), self.bs
        coded = norm_record(good, 'mac-diskutil', bs)
        for q, r in zip(coded['partitions'], sorted(good, key=lambda r: r.offset)):
            q['gdisk_type_code'] = GDISK_CODE.get(r.type, 'FFFF')
        self.assertEqual(self.verify(readers=self.four(good, **{'mac-diskutil': coded}))[0], [])
        for detail in (0, 1, 2):
            with self.subTest(detail=detail):
                doc = norm_record(good, 'mac-diskutil', bs, detail=detail)
                self.assertEqual(self.verify(readers=self.four(good, **{'mac-diskutil': doc}))[0], [])
        coded['partitions'][3]['gdisk_type_code'] = '0700'
        problems, _, _ = self.verify(readers=self.four(good, **{'mac-diskutil': coded}))
        self.has(problems, 'gdisk type code 0700 does not fit type ' + pm.TYPE)
        coded['partitions'][3]['gdisk_type_code'] = '8300'
        self.assertEqual(self.verify(readers=self.four(good, **{'mac-diskutil': coded}))[0], [])
        coded['partitions'][0]['gdisk_type_code'] = '8300'
        self.has(self.verify(readers=self.four(good, **{'mac-diskutil': coded}))[0], f'gdisk type code 8300 does not fit type {T_ESP}')

        def broken(edit):
            doc = norm_record(good, 'mac-diskutil', bs)
            edit(doc)
            return self.verify(readers=self.four(good, **{'mac-diskutil': doc}))[0]

        cases = [('unknown key', lambda d: d.update(extra=1), 'unknown'),
                 ('unknown disk key', lambda d: d['disk'].update(serial='x'), 'unknown'),
                 ('unknown partition key', lambda d: d['partitions'][0].update(device='x'), 'unknown'),
                 ('missing key', lambda d: d.pop('reader'), 'missing'),
                 ('lowercase code', lambda d: d['partitions'][0].update(gdisk_type_code='ef00'), 'four hex digits'),
                 ('bad label', lambda d: d.update(reader='bad label!'), 'reader label'),
                 ('label is null', lambda d: d.update(reader=None), 'reader label None'),
                 ('label is a number', lambda d: d.update(reader=7), 'reader label 7'),
                 ('label is a boolean', lambda d: d.update(reader=True), 'reader label True'),
                 ('label is a list', lambda d: d.update(reader=['x']), "reader label ['x']"),
                 ('not GPT', lambda d: d['disk'].update(partition_style='MBR'), 'is not GPT'),
                 ('odd sector size', lambda d: d['disk'].update(block_size=1024), 'neither 512 nor 4096'),
                 ('inverted LBAs', lambda d: d['partitions'][0].update(first_lba=9, last_lba=3), 'is not valid'),
                 ('negative LBA', lambda d: d['partitions'][0].update(first_lba=-1), 'is not valid'),
                 ('attributes too wide', lambda d: d['partitions'][0].update(attributes=1 << 64), 'out of range'),
                 ('name is a number', lambda d: d['partitions'][0].update(name=5), 'name is not text'),
                 ('partitions is not a list', lambda d: d.update(partitions={}), 'is not a list'),
                 ('a partition is a number', lambda d: d['partitions'].append(5), 'is not an object'),
                 ('boolean LBA', lambda d: d['partitions'][0].update(first_lba=True), 'not an integer')]
        for name, edit, fragment in cases:
            with self.subTest(name):
                problems = broken(edit)
                self.has(problems, fragment)

    def test_documents_that_are_not_readers_are_refused_by_name(self):
        good = self.ok_rows()
        cases = [('list', [], 'reader list: not a JSON object'),
                 ('number', 5, 'reader number: not a JSON object'),
                 ('empty', {}, 'reader empty: not a gpt_survey JSON, a Windows record or a snapmac-reader/1 document'),
                 ('bare', {'format': FORMAT}, 'reader bare: reader document: missing'),
                 ('other-format', {'format': 'snapmac-reader/2', 'disks': []}, 'reader other-format:'),
                 ('broken-file', pm.PlanError('broken.json: not JSON (Expecting value, line 1)'),
                  'reader broken-file: broken.json: not JSON (Expecting value, line 1)'),
                 ('no-disk', {'disks': []}, 'reader no-disk: expected exactly one disk matching the pins, found 0 of 0'),
                 ('weird-disk', {'disks': [5]}, 'reader weird-disk:')]
        for label, doc, fragment in cases:
            with self.subTest(label):
                problems, _, verdict = self.verify(readers=self.four(good)[:3] + [(label, doc)])
                self.has(problems, fragment)
                self.assertEqual((verdict['ok'], verdict['readers']['agreeing'], verdict['readers']['distinct']), (False, 3, 3))

    def test_an_unexpected_parser_exception_is_a_refusal_not_a_traceback(self):
        doc = self.four(self.ok_rows())[0][1]
        for exc in (AttributeError('no attribute'), KeyError('missing'), TypeError('bad type'), ValueError('bad value')):
            with self.subTest(type(exc).__name__), mock.patch.object(pm, '_record_windows', side_effect=exc):
                with self.assertRaises(pm.PlanError) as caught:
                    pm.load_reader(doc, 'windows', DISK_GUID)
                self.assertEqual(str(caught.exception), f'malformed reader document: {type(exc).__name__}: {exc}')
        with mock.patch.object(pm, '_record_windows', side_effect=pm.PlanError('a specific refusal')):
            with self.assertRaises(pm.PlanError) as caught:
                pm.load_reader(doc, 'windows', DISK_GUID)
            self.assertEqual(str(caught.exception), 'a specific refusal')

    def test_a_reader_field_that_is_not_text_is_refused_in_every_document_kind(self):
        good = self.ok_rows()
        for name in ('windows', 'q1n1'):
            doc = copy.deepcopy(dict(self.four(good))[name])
            doc['reader'] = 5
            with self.subTest(name):
                problems, _, verdict = self.verify(readers=self.four(good, **{name: doc}))
                self.has(problems, f'reader {name}: reader label 5 is not')
                self.assertEqual((verdict['ok'], verdict['readers']['agreeing'], verdict['readers']['distinct']), (False, 3, 3))

    def test_undo_passes_when_four_readers_see_the_before_disk(self):
        notes, verdict = [], {}
        problems = pm.verify_undo_sp11(self.before, self.readers(self.rows), notes=notes, verdict=verdict)
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual((verdict['mode'], verdict['ok'], verdict['new_partition'], verdict['readers']['four_reader_gate_met']),
                         ('verify-undo', True, None, True))

    def undo(self, rows, **kw):
        return pm.verify_undo_sp11(self.before, self.readers(rows), **kw)

    def test_undo_refuses_what_is_not_the_before_disk(self):
        good = self.ok_rows()
        self.has(self.undo(good), '1 partition(s) that BEFORE does not have remain')
        self.has(self.undo(good), 'free extents differ from BEFORE')
        self.has(self.undo(change(self.rows, WINC, name='renamed')), f'existing partition {WINC} changed: name')
        self.has(self.undo([r for r in self.rows if r.guid != MSR]), f'existing partition {MSR} is gone')
        self.has(self.undo(applied(self.rows, self.plan, first=self.plan['new']['first_lba'] + 8)),
                 'partition(s) that BEFORE does not have remain')
        problems = pm.verify_undo_sp11(self.before, self.readers(self.rows)[:3])
        self.assertEqual(problems, ['3 distinct reader document(s), 4 required'])

    def test_undo_pins_are_optional_for_one_disk_and_needed_for_several(self):
        second = survey_of(self.rows, self.bs, eui='1122334455667788', guid=G(2))['disks'][0]
        multi = {'disks': [self.before['disks'][0], second]}
        with self.assertRaisesRegex(pm.PlanError, '2 disks in the survey'):
            pm.verify_undo_sp11(multi, self.readers(self.rows))
        for pins in (dict(disk_guid=DISK_GUID), dict(nvme_eui=EUI), dict(disk_guid=DISK_GUID, nvme_eui=EUI_REVERSED)):
            with self.subTest(**pins):
                self.assertEqual(pm.verify_undo_sp11(multi, self.readers(self.rows), **pins), [])
        with self.assertRaisesRegex(pm.PlanError, 'found 0 of 2'):
            pm.verify_undo_sp11(multi, self.readers(self.rows), disk_guid=G(3))

    def tampered(self, edit):
        p = copy.deepcopy(self.plan)
        edit(p)
        return p

    def test_a_tampered_plan_is_not_trusted(self):
        good = self.ok_rows()
        cases = [('new offset', lambda p: p['new'].update(offset=p['new']['offset'] + 4096), 'sections: new'),
                 ('new first LBA', lambda p: p['new'].update(first_lba=p['new']['first_lba'] + 32), 'sections: new'),
                 ('new type', lambda p: p['new'].update(type=T_BASIC), 'the plan cannot be reproduced from BEFORE'),
                 ('protected row removed', lambda p: p['protected'].pop(), 'sections: protected'),
                 ('protected row edited', lambda p: p['protected'][0].update(name='renamed'), 'sections: protected'),
                 ('extent bytes', lambda p: p['extent'].update(bytes=p['extent']['bytes'] + 512), 'sections: extent'),
                 ('extent neighbour', lambda p: p['extent'].update(before_neighbour=G(5)), 'sections: extent'),
                 ('extent start', lambda p: p['extent'].update(first_lba=p['extent']['first_lba'] + 8),
                  'the plan cannot be reproduced from BEFORE'),
                 ('min bytes beyond the extent', lambda p: p['constraints'].update(min_bytes=1 << 62),
                  'the plan cannot be reproduced from BEFORE'),
                 ('alignment', lambda p: p['constraints'].update(alignment_bytes=512), 'sections: constraints'),
                 ('authorization waived', lambda p: p['safety'].update(requires_authorization=False), 'sections: safety'),
                 ('backup waived', lambda p: p['safety'].update(requires_verified_backup=False), 'sections: safety'),
                 ('commands allowed', lambda p: p['safety'].update(emits_commands=True), 'sections: safety'),
                 ('notes', lambda p: p['layout'].update(notes=['all is well']), 'sections: layout'),
                 ('extra section', lambda p: p.update(windows=['New-Partition']), 'sections: windows'),
                 ('missing section', lambda p: p.pop('layout'), 'sections: layout'),
                 ('disk facts', lambda p: p['disk'].update(first_usable=p['disk']['first_usable'] + 1), 'sections: disk'),
                 ('format', lambda p: p.update(format=2), 'sections: format'),
                 ('malformed', lambda p: p.pop('constraints'), 'the plan is malformed')]
        for name, edit, fragment in cases:
            with self.subTest(name):
                problems, _, verdict = self.verify(good, plan=self.tampered(edit))
                self.assertEqual(len(problems), 1, problems)
                self.assertIn(fragment, problems[0])
                self.assertEqual((verdict['ok'], verdict['readers']['agreeing'], verdict['new_partition']), (False, 0, None))
        problems, _, _ = self.verify(good, plan=json.loads(json.dumps(self.plan)))
        self.assertEqual(problems, [])

    def test_a_plan_for_another_disk_or_profile_is_refused(self):
        good = self.ok_rows()
        elsewhere = pm.plan_sp11(survey_of(self.rows, self.bs, eui='1122334455667788', guid=G(2)))
        with self.assertRaisesRegex(pm.PlanError, 'found 0 of 1'):
            self.verify(good, plan=elsewhere)
        a16_plan = dict(self.plan, profile='a16')
        for bad in (a16_plan, [], dict(self.plan, disk=5)):
            with self.subTest(repr(bad)[:30]):
                with self.assertRaisesRegex(pm.PlanError, 'is not an sp11 plan'):
                    self.verify(good, plan=bad)
        with self.assertRaisesRegex(pm.PlanError, 'verify needs the plan'):
            pm.verify_sp11(self.before, self.readers(good), None)
        with self.assertRaisesRegex(pm.PlanError, 'is not an sp11 plan'):
            self.undo(self.rows, p=a16_plan)

    def test_verify_refuses_a_before_that_already_holds_the_change(self):
        already = self.survey(applied(self.rows, self.plan))
        with self.assertRaisesRegex(pm.PlanError, 'already exists'):
            pm.plan_sp11(already)
        problems = pm.verify_sp11(already, self.readers(applied(self.rows, self.plan)), self.plan)
        self.assertTrue(problems)
        self.assertTrue(problems[0].startswith('the plan cannot be reproduced from BEFORE'))


class Sp11Plan512(SectorCases, unittest.TestCase):
    bs = 512

    def test_literal_numbers(self):
        n, e = self.plan['new'], self.plan['extent']
        self.assertEqual((e['first_lba'], e['last_lba'], e['offset'], e['bytes']),
                         (419430400, 1071642623, 214748364800, 333932658688))
        self.assertEqual((n['first_lba'], n['last_lba'], n['offset'], n['bytes'], n['end']),
                         (419432448, 1071640575, 214749413376, 333930561536, 548679974912))


class Sp11Plan4K(SectorCases, unittest.TestCase):
    bs = 4096

    def test_literal_numbers(self):
        n, e = self.plan['new'], self.plan['extent']
        self.assertEqual((e['first_lba'], e['last_lba'], e['offset'], e['bytes']),
                         (52428800, 133955327, 214748364800, 333932658688))
        self.assertEqual((n['first_lba'], n['last_lba'], n['offset'], n['bytes'], n['end']),
                         (52429056, 133955071, 214749413376, 333930561536, 548679974912))


class CommandLineCases:
    """The command lines for 512e and 4Kn disks: plan, verify and verify-undo, one file per reader."""
    bs = 512

    def setUp(self):
        self.rows = layout()
        self.work = Work()
        self.addCleanup(self.work.close)
        self.before_path = self.work.write('before.json', survey_of(self.rows, self.bs))
        self.plan_path = self.work.root / 'plan.json'
        self.verdict_path = self.work.root / 'verdict.json'

    def cli(self, mode, *argv):
        return run_main([mode, '--profile', 'sp11', *argv])

    def planned(self):
        rc, out, err = self.cli('plan', '--survey', self.before_path, '--out', self.plan_path)
        self.assertEqual((rc, err), (0, ''))
        return json.loads(self.plan_path.read_text()), out

    def after_files(self, rows):
        return afters([self.work.write(f'{label}.json', doc) for label, doc in readers_for(rows, self.bs)])

    def verify(self, rows, *extra):
        return self.cli('verify', '--before', self.before_path, *self.after_files(rows), '--plan', self.plan_path,
                        '--out', self.verdict_path, *extra)

    def refused(self, result, fragment):
        rc, out, err = result
        self.assertEqual((rc, out), (2, ''), err)
        self.assertTrue(err.startswith('REFUSED: '), err)
        self.assertIn(fragment, err)

    def test_plan_verify_and_undo_end_to_end(self):
        p, out = self.planned()
        self.assertEqual(p, pm.plan_sp11(survey_of(self.rows, self.bs)))
        self.assertEqual(self.plan_path.read_text(), json.dumps(p, indent=1) + '\n')
        self.assertEqual(out, '\n'.join(pm.plan_text(p)) + '\n')
        for words in ('REQUIRES AUTHORIZATION', 'REQUIRES A VERIFIED BACKUP', 'BitLocker',
                      'operator creates the entry with the Windows partition manager; this tool does not'):
            self.assertIn(words, out)
        self.assertEqual(self.verify(applied(self.rows, p)), (0, 'PASS\n', ''))
        verdict = json.loads(self.verdict_path.read_text())
        self.assertEqual((verdict['mode'], verdict['ok'], verdict['readers']['distinct'],
                          verdict['readers']['four_reader_gate_met']), ('verify', True, 4, True))
        undone = self.cli('verify-undo', '--before', self.before_path, *self.after_files(self.rows),
                          '--out', self.verdict_path)
        self.assertEqual(undone, (0, 'PASS\n', ''))
        self.assertEqual(json.loads(self.verdict_path.read_text())['mode'], 'verify-undo')

    def test_a_failed_verify_exits_1_and_the_verdict_says_why(self):
        self.planned()
        rc, out, err = self.verify(self.rows)
        lines = out.splitlines()
        self.assertEqual((rc, err), (1, ''))
        self.assertTrue(all(x.startswith('FAIL: ') for x in lines[:-1]), out)
        self.assertEqual(lines[-1], f'{len(lines) - 1} problem(s)')
        self.assertIn('expected one new partition, found 0', out)
        verdict = json.loads(self.verdict_path.read_text())
        self.assertEqual((verdict['ok'], len(verdict['problems'])), (False, len(lines) - 1))

    def test_files_with_the_same_name_in_different_folders_are_separate_readers(self):
        p, _ = self.planned()
        readers = readers_for(applied(self.rows, p), self.bs)
        files = []
        for n, (_, doc) in enumerate(readers[:3], 1):
            (self.work.root / f'folder{n}').mkdir()
            files.append(self.work.write(f'folder{n}/after.json', doc))
        files.append(self.work.write('windows-boot.json', readers[3][1]))
        result = self.cli('verify', '--before', self.before_path, *afters(files), '--plan', self.plan_path,
                          '--out', self.verdict_path)
        self.assertEqual(result, (0, 'PASS\n', ''))
        self.assertEqual(json.loads(self.verdict_path.read_text())['readers']['labels'],
                         ['after', 'after-2', 'after-3', 'windows-boot'])

    def test_a_file_name_that_is_not_a_valid_label_is_made_into_one(self):
        p, _ = self.planned()
        readers = readers_for(applied(self.rows, p), self.bs)
        names = ['my reader (1)', 'x' * 60, '\u00fc']
        files = [self.work.write(f'{name}.json', doc) for name, (_, doc) in zip(names, readers[:3])]
        files.append(self.work.write('windows-boot.json', readers[3][1]))
        result = self.cli('verify', '--before', self.before_path, *afters(files), '--plan', self.plan_path,
                          '--out', self.verdict_path)
        self.assertEqual(result, (0, 'PASS\n', ''))
        self.assertEqual(json.loads(self.verdict_path.read_text())['readers']['labels'],
                         ['my-reader--1-', 'x' * 40, '-', 'windows-boot'])

    def test_notes_print_before_the_verdict(self):
        p, _ = self.planned()
        moved = change(applied(self.rows, p), WINC, slot=7)
        rc, out, err = self.verify(moved)
        lines = out.splitlines()
        self.assertEqual((rc, err, lines[-1], len(lines)), (0, '', 'PASS', 3))
        self.assertTrue(all(x.startswith('note: reader ') and 'moved from GPT slot' in x for x in lines[:-1]), out)

    def test_a_refusal_exits_2_with_a_reason_and_writes_nothing(self):
        crowded = self.rows + [Row(4, G(14), T_BASIC, 300 * GIB, 50 * GIB, 'D')]
        two = self.work.write('two-extents.json', survey_of(crowded, self.bs))
        out_path = self.work.root / 'refused-plan.json'
        self.refused(self.cli('plan', '--survey', two, '--out', out_path, '--min-bytes', 40 * GIB), '--extent-start')
        self.assertFalse(out_path.exists())
        first = 200 * GIB // self.bs
        rc, _, err = self.cli('plan', '--survey', two, '--out', out_path, '--min-bytes', 40 * GIB,
                              '--extent-start', first, '--extent-end', 300 * GIB // self.bs - 1)
        self.assertEqual((rc, err), (0, ''))
        self.assertEqual(json.loads(out_path.read_text())['extent']['first_lba'], first)

        p, _ = self.planned()
        a16_plan = self.work.write('a16-plan.json', dict(p, profile='a16'))
        for name, mode, argv, fragment in [
                ('no survey', 'plan', ['--survey', self.work.root / 'missing.json'], 'cannot be read (FileNotFoundError)'),
                ('not JSON', 'plan', ['--survey', self.work.write('text.json', 'not json at all\n')], 'not JSON'),
                ('empty', 'plan', ['--survey', self.work.write('empty.json', '')], 'not JSON'),
                ('a directory', 'plan', ['--survey', self.work.root], 'not a regular file'),
                ('no GPT', 'plan', ['--survey', self.work.write('blank.json', blank_survey(self.bs))], ''),
                ('an a16 plan', 'verify', ['--before', self.before_path, *self.after_files(self.rows),
                                           '--plan', a16_plan], 'is not an sp11 plan'),
                ('--out is a directory', 'plan', ['--survey', self.before_path, '--out', self.work.root],
                 'refusing to write to a device or special file')]:
            with self.subTest(name):
                self.refused(self.cli(mode, *argv), fragment)
        self.verdict_path.unlink(missing_ok=True)
        self.refused(self.cli('verify', '--before', self.before_path, *self.after_files(self.rows),
                              '--plan', a16_plan, '--out', self.verdict_path), 'is not an sp11 plan')
        self.assertFalse(self.verdict_path.exists())

    def test_a_lone_extent_bound_is_refused_by_the_parser(self):
        for option in ('--extent-start', '--extent-end'):
            with self.subTest(option):
                rc, out, err = self.cli('plan', '--survey', self.before_path, option, 5)
                self.assertEqual((rc, out), (2, ''))
                self.assertIn('--extent-start and --extent-end go together', err)

    def test_a_bad_after_file_fails_the_gate_and_names_the_reader(self):
        p, _ = self.planned()
        files = self.after_files(applied(self.rows, p))
        self.work.write('windows.json', 'not json at all\n')
        rc, out, err = self.cli('verify', '--before', self.before_path, *files, '--plan', self.plan_path)
        self.assertEqual((rc, err), (1, ''))
        self.assertIn('FAIL: reader windows: windows.json: not JSON', out)
        self.assertIn('3 distinct reader document(s), 4 required', out)

    def test_windows_files_in_the_encodings_powershell_writes_verify(self):
        p, _ = self.planned()
        docs = dict(readers_for(applied(self.rows, p), self.bs))
        for name, kw in (('utf-16', dict(encoding='utf-16')), ('utf-8-sig', dict(encoding='utf-8-sig')),
                         ('utf-16-be', dict(encoding='utf-16-be', prefix=b'\xfe\xff'))):
            with self.subTest(name):
                files = [self.work.write('windows.json', docs['windows'], **kw),
                         self.work.write('mac-raw.json', docs['mac-raw']),
                         self.work.write('q1n1.json', docs['q1n1']),
                         self.work.write('windows-boot.json', docs['windows-boot'], **kw)]
                rc, out, err = self.cli('verify', '--before', self.before_path, *afters(files), '--plan', self.plan_path)
                self.assertEqual((rc, out, err), (0, 'PASS\n', ''))

    def test_the_reader_gate_can_be_lowered_only_in_the_open(self):
        p, _ = self.planned()
        files = self.after_files(applied(self.rows, p))[:6]
        rc, out, err = self.cli('verify', '--before', self.before_path, *files, '--plan', self.plan_path,
                                '--out', self.verdict_path)
        self.assertEqual((rc, err), (1, ''))
        self.assertIn('3 distinct reader document(s), 4 required', out)
        rc, out, err = self.cli('verify', '--before', self.before_path, *files, '--plan', self.plan_path,
                                '--out', self.verdict_path, '--min-readers', 3)
        self.assertEqual((rc, err), (0, ''))
        self.assertEqual(out.splitlines(), ['note: partial: --min-readers 3 is below the 4 readers the X6 gate wants', 'PASS'])
        self.assertFalse(json.loads(self.verdict_path.read_text())['readers']['four_reader_gate_met'])
        self.refused(self.cli('verify', '--before', self.before_path, *files, '--plan', self.plan_path,
                              '--min-readers', 0), 'at least 1')

    def test_undo_takes_its_pins_from_the_plan_or_the_command_line(self):
        p, _ = self.planned()
        files = self.after_files(self.rows)
        for pins in ([], ['--plan', self.plan_path], ['--disk-guid', DISK_GUID], ['--nvme-eui', EUI]):
            with self.subTest(pins=[str(x) for x in pins]):
                result = self.cli('verify-undo', '--before', self.before_path, *files, *pins)
                self.assertEqual(result, (0, 'PASS\n', ''))
        self.refused(self.cli('verify-undo', '--before', self.before_path, *files, '--disk-guid', G(3)), 'found 0 of 1')

    def test_an_unknown_profile_is_refused_by_the_parser(self):
        missing = self.work.root / 'missing.json'
        for mode, argv in (('plan', ['--survey', missing]),
                           ('verify', ['--before', missing, '--after', missing, '--plan', missing]),
                           ('verify-undo', ['--before', missing, '--after', missing])):
            with self.subTest(mode):
                rc, out, err = run_main([mode, '--profile', 'sp12', *argv])
                self.assertEqual((rc, out), (2, ''))
                self.assertIn("invalid choice: 'sp12'", err)

    def test_sp11_only_options_are_refused_under_a16_before_any_file_is_read(self):
        missing = self.work.root / 'missing.json'
        base = {'plan': ['--survey', missing], 'verify-undo': ['--before', missing, '--after', missing],
                'verify': ['--before', missing, '--after', missing, '--plan', missing]}
        for mode, extra in [('plan', ['--extent-start', 0]), ('plan', ['--extent-end', 5]), ('plan', ['--min-bytes', 0]),
                            ('plan', ['--new-type', T_BASIC]), ('plan', ['--new-name', 'x']),
                            ('plan', ['--disk-guid', DISK_GUID]), ('plan', ['--nvme-eui', EUI]),
                            ('verify', ['--min-readers', 0]), ('verify', ['--out', self.verdict_path]),
                            ('verify-undo', ['--min-readers', 1]), ('verify-undo', ['--out', self.verdict_path]),
                            ('verify-undo', ['--plan', missing]), ('verify-undo', ['--no-eui']),
                            ('verify-undo', ['--disk-guid', DISK_GUID]), ('verify-undo', ['--nvme-eui', EUI])]:
            with self.subTest(mode=mode, option=str(extra[0])):
                rc, out, err = run_main([mode, *base[mode], *extra])
                self.assertEqual((rc, out), (2, ''))
                self.assertIn(f'{extra[0]} needs --profile sp11', err)
        self.assertFalse(self.verdict_path.exists())

    def test_a_repeated_after_is_refused_under_a16(self):
        missing = self.work.root / 'missing.json'
        for mode, extra in (('verify', ['--plan', missing]), ('verify-undo', [])):
            with self.subTest(mode):
                rc, out, err = run_main([mode, '--before', missing, '--after', missing, '--after', missing, *extra])
                self.assertEqual((rc, out), (2, ''))
                self.assertIn('--after is given once with --profile a16', err)

    def test_a_crash_is_a_refusal_never_a_pass_or_a_failure(self):
        files = self.after_files(applied(self.rows, self.planned()[0]))
        for target, argv in (('plan_sp11', ['plan', '--survey', self.before_path]),
                             ('verify_sp11', ['verify', '--before', self.before_path, *files, '--plan', self.plan_path]),
                             ('verify_undo_sp11', ['verify-undo', '--before', self.before_path, *files])):
            with self.subTest(target), mock.patch.object(pm, target, side_effect=RuntimeError('boom')):
                self.assertEqual(self.cli(*argv), (2, '', 'REFUSED: unexpected RuntimeError: boom\n'))


class Sp11Cli512(CommandLineCases, unittest.TestCase):
    bs = 512


class Sp11Cli4K(CommandLineCases, unittest.TestCase):
    bs = 4096


def mutate(text, old, new=''):
    """`text` with the first `old` replaced; an edit that would change nothing fails the test instead of passing it."""
    assert old in text, f'{old!r} is not in the text, so the mutation would test nothing'
    return text.replace(old, new, 1)


def swap_line(text, prefix, new=None):
    """Replace, or with new=None drop, the one line that starts with `prefix`."""
    lines = text.split('\n')
    hits = [i for i, x in enumerate(lines) if x.lstrip().startswith(prefix)]
    assert len(hits) == 1, f'{prefix!r} starts {len(hits)} lines, so the mutation would test the wrong thing'
    lines[hits[0]:hits[0] + 1] = [] if new is None else [new]
    return '\n'.join(lines)


class ConverterCases:
    """`sgdisk -p` / `gdisk -l` and `gpt show` text through the converters, for 512e and 4Kn disks."""
    bs = 512

    def setUp(self):
        self.rows = layout()
        self.other_bs = 4096 if self.bs == 512 else 512

    def sgdisk(self, rows=None, **kw):
        return sgdisk_text(self.rows if rows is None else rows, self.bs, **kw)

    def show_table(self, rows=None):
        return gpt_show_table(self.rows if rows is None else rows, self.bs)

    def show(self, table=None, **kw):
        return gpt_show_text(self.show_table() if table is None else table, self.bs, **kw)

    def refuses(self, convert, text, fragment, **kw):
        with self.assertRaisesRegex(pm.PlanError, re.escape(fragment)):
            convert(text, 'lbl', **kw)

    def test_sgdisk_text_becomes_the_expected_reader(self):
        want = expected_sgdisk_doc(self.rows, 'mac-sgdisk', self.bs)
        text = self.sgdisk()
        gib_as_mib = re.sub(r'(?<![0-9.])1\.0 GiB', '1024.0 MiB', text)
        self.assertNotEqual(gib_as_mib, text)
        for name, variant in (('sgdisk -p', text), ('gdisk -l', self.sgdisk(scan=True)),
                              ('CRLF line ends', text.replace('\n', '\r\n')), ('1 GiB as 1024.0 MiB', gib_as_mib)):
            with self.subTest(name):
                doc = pm.convert_sgdisk(variant, 'mac-sgdisk')
                # Only gdisk's scan block states that the MBR is protective; sgdisk -p says nothing about it.
                self.assertEqual(doc, dict(want, mbr='protective') if name == 'gdisk -l' else want)
                for private in ('example.img', 'Example Disk', 'EFI system partition', 'Windows recovery'):
                    self.assertNotIn(private, json.dumps(doc))

    def test_gpt_show_text_becomes_the_expected_reader(self):
        want = expected_gpt_show_doc(self.rows, 'mac-gpt', self.bs)
        text = self.show()
        for name, variant, block_size in (('with the media line', text, None),
                                          ('without it, size given', self.show(media=False), self.bs),
                                          ('CRLF line ends', text.replace('\n', '\r\n'), None),
                                          ('"at sector" lines', text + 'Pri GPT at sector 1\n', None)):
            with self.subTest(name):
                self.assertEqual(pm.convert_gpt_show(variant, 'mac-gpt', block_size), want)

    def test_partitions_may_start_at_the_first_usable_sector_and_end_at_the_last(self):
        bs = self.bs
        first_usable, last_usable = usable(bs)
        edge = (last_usable + 1) * bs
        esp = lambda start: Row(0, ESP, T_ESP, start, 260 * MIB, 'first')
        rec = lambda end: Row(3, REC, T_RECOVERY, end - GIB, GIB, 'last')
        middle = Row(2, WINC, T_BASIC, 100 * GIB, 100 * GIB, 'middle')
        shapes = (('the first partition starts at the first usable sector', [esp(first_usable * bs), middle, rec(edge - MIB)]),
                  ('the last partition ends at the last usable sector', [esp(MIB), middle, rec(edge)]),
                  ('both', [esp(first_usable * bs), middle, rec(edge)]))
        for name, rows in shapes:
            with self.subTest(name):
                self.assertEqual(pm.convert_sgdisk(self.sgdisk(rows), 'edge'), expected_sgdisk_doc(rows, 'edge', bs))
                self.assertEqual(pm.convert_gpt_show(self.show(self.show_table(rows)), 'edge'),
                                 expected_gpt_show_doc(rows, 'edge', bs))

    def test_the_sector_size_comes_from_the_text_or_the_caller_and_never_disagrees(self):
        text = self.sgdisk()
        self.refuses(pm.convert_sgdisk, text, f'the text says {self.bs}-byte sectors but --block-size is {self.other_bs}',
                     block_size=self.other_bs)
        unstated = swap_line(text, 'Sector size')
        self.refuses(pm.convert_sgdisk, unstated, 'the text does not state the sector size')
        self.assertEqual(pm.convert_sgdisk(unstated, 'lbl', self.bs), expected_sgdisk_doc(self.rows, 'lbl', self.bs))
        self.refuses(pm.convert_sgdisk, unstated, 'the printed disk size does not fit', block_size=self.other_bs)
        self.refuses(pm.convert_sgdisk, unstated, 'is neither 512 nor 4096', block_size=1024)
        shown = self.show()
        self.refuses(pm.convert_gpt_show, shown, f'the text says {self.bs}-byte sectors but --block-size is {self.other_bs}',
                     block_size=self.other_bs)
        self.refuses(pm.convert_gpt_show, self.show(media=False), 'the text does not state the sector size')
        self.refuses(pm.convert_gpt_show, self.show(media=False), 'not where a 128-entry GPT puts them',
                     block_size=self.other_bs)

    def test_faulty_sgdisk_text_is_refused(self):
        text, scan, array = self.sgdisk(), self.sgdisk(scan=True), 16384 // self.bs
        free_off = re.sub(r'(Total free space is \d+ sectors \()[^)]*', r'\g<1>1.0 GiB', text)
        self.assertNotEqual(free_off, text)
        tail = Row(4, G(14), T_BASIC, DISK - MIB, MIB, 'tail')
        cases = [
            ('hybrid MBR', mutate(scan, 'MBR: protective', 'MBR: hybrid'), 'only a protective MBR'),
            ('BSD label', mutate(scan, 'BSD: not present', 'BSD: present'), 'only a protective MBR'),
            ('MBR line dropped from the scan', mutate(scan, '  MBR: protective\n'), 'block is incomplete or repeated'),
            ('GPT line dropped from the scan', mutate(scan, '  GPT: present\n'), 'block is incomplete or repeated'),
            ('scan heading dropped', mutate(scan, 'Partition table scan:\n'), 'block is incomplete or repeated'),
            ('MBR line twice', mutate(scan, '  MBR: protective\n', '  MBR: protective\n' * 2),
             'block is incomplete or repeated'),
            ('warning', 'Warning! Main and backup partition tables differ!\n' + text, 'resolve that first'),
            ('caution', 'Caution: invalid backup GPT header; regenerating it.\n' + text, 'resolve that first'),
            ('problem', 'Problem: the secondary header is not at the end of the disk.\n' + text, 'resolve that first'),
            ('invalid GPT', 'Found invalid GPT and valid MBR; converting MBR to GPT format in memory.\n' + text,
             'resolve that first'),
            ('unknown line', mutate(text, 'Model: Example Disk', 'Model: Example Disk\nDisk flags: none'), 'unrecognised line'),
            ('garbage row', text + 'garbage\n', 'unrecognised partition row'),
            ('bad type code', mutate(text, '0700', '07xx'), 'unrecognised partition row'),
            ('no disk line', swap_line(text, 'Disk example.img'), 'lacks'),
            ('no GUID line', swap_line(text, 'Disk identifier'), 'lacks'),
            ('no free line', swap_line(text, 'Total free space'), 'lacks'),
            ('empty', '', 'lacks'),
            ('junk', 'x' * 100 + '\n', 'unrecognised line'),
            ('truncated before the table', text.split('Number')[0], 'the partition table header'),
            ('last row dropped', text.rstrip('\n').rsplit('\n', 1)[0] + '\n', 'the stated free space does not match'),
            ('one free sector too many', self.sgdisk(free_delta=1), 'the stated free space does not match'),
            ('free size text off', free_off, 'the stated free space does not match'),
            ('64 entries', self.sgdisk(entries=64), 'the table is not 128 entries of 128 bytes'),
            ('table one sector long', self.sgdisk(table_end_delta=1), 'the table is not 128 entries of 128 bytes'),
            ('usable range past the disk', self.sgdisk(usable_last_delta=1), 'the table position or usable range'),
            ('table runs into the usable range',
             mutate(text, f'begins at sector 2 and ends at sector {1 + array}',
                    f'begins at sector 3 and ends at sector {2 + array}'), 'the table position or usable range'),
            ('row in the backup GPT', self.sgdisk(self.rows + [tail]), 'lies outside the usable range or overlaps'),
            ('overlapping rows', self.sgdisk(self.rows + [Row(4, G(14), T_BASIC, 100 * GIB, GIB, 'inside C:')]),
             'lies outside the usable range or overlaps'),
            ('one sector of overlap', self.sgdisk(self.rows + [Row(4, G(14), T_BASIC, 200 * GIB - self.bs, GIB, 'x')]),
             'lies outside the usable range or overlaps'),
            ('number repeats', self.sgdisk(self.rows + [Row(2, G(14), T_BASIC, 300 * GIB, GIB, 'D')]),
             'partition numbers repeat'),
            ('number past 128', self.sgdisk(self.rows + [Row(128, G(14), T_BASIC, 300 * GIB, GIB, 'D')]),
             'partition numbers repeat or fall outside 1..128'),
            ('size text off', mutate(text, '260.0 MiB', '261.0 MiB'), 'the printed size does not fit'),
            ('disk size text off', mutate(text, '512.0 GiB', '513.0 GiB'), 'the printed disk size does not fit'),
            ('key twice', mutate(text, 'Partition table holds up to 128 entries\n',
                                 'Partition table holds up to 128 entries\n' * 2), 'appears twice'),
        ]
        for name, variant, fragment in cases:
            with self.subTest(name):
                self.refuses(pm.convert_sgdisk, variant, fragment)

    def test_faulty_gpt_show_text_is_refused(self):
        table, array = self.show_table(), 16384 // self.bs
        text = self.show()
        free_at = next(i for i, x in enumerate(table) if x[3] == '' and x[2] is None)
        part_at = [i for i, x in enumerate(table) if x[2] is not None]
        header_line = '       start        size  index  contents\n'
        renumbered = lambda index: [(s, n, index if i == part_at[1] else k, c) for i, (s, n, k, c) in enumerate(table)]
        swapped = list(table)
        swapped[1], swapped[2] = (1, 1, None, 'Pri GPT table'), (2, array, None, 'Pri GPT header')
        shrunk = list(table)
        shrunk[2:3] = [(2, array - 2, None, 'Pri GPT table'), (array, 2, None, '')]
        cases = [
            ('a gap', self.show(table[:free_at] + table[free_at + 1:]), 'the rows are not contiguous from sector 0'),
            ('an overlap', self.show([(s + 1, n, k, c) if i == part_at[0] else (s, n, k, c)
                                      for i, (s, n, k, c) in enumerate(table)]), 'not contiguous'),
            ('an MBR row', mutate(text, 'GPT part - ' + T_BASIC.upper(), 'MBR part 7'), 'unrecognised line'),
            ('a bad UUID', mutate(text, T_BASIC.upper(), 'ZZ' + T_BASIC.upper()[2:]), 'unrecognised line'),
            ('an error message', 'gpt show: example: Permission denied\n' + text, 'unrecognised line'),
            ('the media line is off', mutate(text, f'blocks={DISK // self.bs}', f'blocks={DISK // self.bs + 1}'),
             'the media line does not match the rows'),
            ('no Sec header, media line kept', self.show(table[:-1]), 'the media line does not match the rows'),
            ('a repeated index', self.show(renumbered(1)), 'a partition index repeats'),
            ('index 0', self.show(renumbered(0)), 'has a bad index'),
            ('index 129', self.show(renumbered(129)), 'has a bad index'),
            ('anchors swapped', self.show(swapped), 'must each appear once, in order'),
            ('table shorter, free row beside it', self.show(shrunk), 'not where a 128-entry GPT puts them'),
            ('header repeated', mutate(text, header_line, header_line * 2), 'the column header is repeated or late'),
            ('header late', mutate(text, header_line) + header_line, 'the column header is repeated or late'),
            ('no header', mutate(text, header_line), 'no column header or no rows'),
            ('media line twice', text.split('\n')[0] + '\n' + text, 'the media line appears twice'),
            ('empty', '', 'no column header or no rows'),
        ]
        for name, variant, fragment in cases:
            with self.subTest(name):
                self.refuses(pm.convert_gpt_show, variant, fragment)
        with self.subTest('no Sec header, no media line'):
            self.refuses(pm.convert_gpt_show, self.show(table[:-1], media=False), 'must each appear once, in order',
                         block_size=self.bs)

    def converted_readers(self, rows, sgdisk=None):
        return [('windows', win_record(rows, self.bs)),
                ('mac-sgdisk', pm.convert_sgdisk(sgdisk_text(rows, self.bs) if sgdisk is None else sgdisk, 'mac-sgdisk')),
                ('q1n1', survey_of(rows, self.bs)),
                ('mac-gpt', pm.convert_gpt_show(gpt_show_text(gpt_show_table(rows, self.bs), self.bs), 'mac-gpt'))]

    def test_converted_text_is_a_reader_that_verifies_and_one_that_disagrees_stops_the_gate(self):
        before = survey_of(self.rows, self.bs)
        p = pm.plan_sp11(before)
        good, n = applied(self.rows, p), p['new']
        notes, verdict = [], {}
        self.assertEqual(pm.verify_sp11(before, self.converted_readers(good), p, notes=notes, verdict=verdict), [])
        self.assertEqual(notes, [])
        self.assertEqual((verdict['readers']['agreeing'], verdict['readers']['four_reader_gate_met']), (4, True))
        self.assertEqual(pm.verify_undo_sp11(before, self.converted_readers(self.rows), p), [])

        printed_as_basic_data = mutate(sgdisk_text(good, self.bs), 'FFFF', '0700')
        problems = pm.verify_sp11(before, self.converted_readers(good, printed_as_basic_data), p)
        self.assertEqual(problems, [f"reader mac-sgdisk: entry at LBA {n['first_lba']}..{n['last_lba']}: "
                                    f"gdisk type code 0700 does not fit type {n['type']}"])
        one_sector_late = sgdisk_text(applied(self.rows, p, first=n['first_lba'] + 1), self.bs)
        problems = pm.verify_sp11(before, self.converted_readers(good, one_sector_late), p)
        self.assertEqual(problems, [f"reader mac-sgdisk: no entry at LBA {n['first_lba']}..{n['last_lba']}",
                                    f"reader mac-sgdisk: unexpected entry at LBA {n['first_lba'] + 1}..{n['last_lba']}"])
        bigger_disk = sgdisk_text(good, self.bs, disk=DISK + self.bs)
        problems = pm.verify_sp11(before, self.converted_readers(good, bigger_disk), p)
        self.assertEqual(problems[0], f'reader mac-sgdisk: disk_bytes is {DISK + self.bs}, expected {DISK}')

    def test_convert_reader_writes_the_document_and_says_so(self):
        work = Work()
        self.addCleanup(work.close)
        for fmt, text, want in (('sgdisk', self.sgdisk(), expected_sgdisk_doc(self.rows, 'mac-x', self.bs)),
                                ('gpt-show', self.show(), expected_gpt_show_doc(self.rows, 'mac-x', self.bs))):
            for name, encoding in (('utf-8', 'utf-8'), ('utf-16', 'utf-16')):
                with self.subTest(fmt, encoding=name):
                    source = work.write(f'{fmt}-{name}.txt', text, encoding=encoding)
                    out = work.root / f'{fmt}-{name}.json'
                    rc, stdout, stderr = run_main(['convert-reader', '--from', fmt, '--in', source,
                                                   '--label', 'mac-x', '--out', out])
                    self.assertEqual((rc, stderr), (0, ''))
                    self.assertEqual(stdout, f'{out.name}: reader mac-x, {len(self.rows)} partitions, {self.bs}-byte sectors\n')
                    self.assertEqual(out.read_text(), json.dumps(want, indent=1) + '\n')
        unstated = work.write('unstated.txt', swap_line(self.sgdisk(), 'Sector size'))
        out = work.root / 'unstated.json'
        argv = ['convert-reader', '--from', 'sgdisk', '--in', unstated, '--label', 'mac-x', '--out', out]
        rc, stdout, stderr = run_main(argv)
        self.assertEqual((rc, stdout, out.exists()), (2, '', False))
        self.assertIn('the text does not state the sector size', stderr)
        self.assertEqual(run_main(argv + ['--block-size', self.bs])[0], 0)
        self.assertTrue(out.exists())

    def test_convert_reader_refuses_bad_input_and_writes_nothing(self):
        work = Work()
        self.addCleanup(work.close)
        out = work.root / 'reader.json'
        good, text = work.write('sgdisk.txt', self.sgdisk()), work.write('garbage.txt', 'hello\n')
        for name, argv, fragment in [
                ('garbage text', ['--from', 'sgdisk', '--in', text, '--label', 'lbl'], 'unrecognised line'),
                ('the wrong format', ['--from', 'gpt-show', '--in', good, '--label', 'lbl'], 'unrecognised line'),
                ('a bad label', ['--from', 'sgdisk', '--in', good, '--label', 'bad label!'], 'label'),
                ('a directory', ['--from', 'sgdisk', '--in', work.root, '--label', 'lbl'], 'not a regular file'),
                ('a missing file', ['--from', 'sgdisk', '--in', work.root / 'nope.txt', '--label', 'lbl'],
                 'cannot be read (FileNotFoundError)'),
                ('block size 1024', ['--from', 'sgdisk', '--in', good, '--label', 'lbl', '--block-size', 1024],
                 'the text says')]:
            with self.subTest(name):
                rc, stdout, stderr = run_main(['convert-reader', *argv, '--out', out])
                self.assertEqual((rc, stdout, out.exists()), (2, '', False), stderr)
                self.assertTrue(stderr.startswith('REFUSED: '), stderr)
                self.assertIn(fragment, stderr)
        rc, stdout, stderr = run_main(['convert-reader', '--from', 'sgdisk', '--in', good, '--label', 'lbl',
                                       '--out', work.root])
        self.assertEqual((rc, stdout), (2, ''))
        self.assertIn('refusing to write to a device or special file', stderr)
        boom = mock.Mock(side_effect=RuntimeError('boom'))
        with mock.patch.dict(pm.CONVERTERS, {'sgdisk': boom}):
            result = run_main(['convert-reader', '--from', 'sgdisk', '--in', good, '--label', 'lbl', '--out', out])
        self.assertEqual((result, out.exists()), ((2, '', 'REFUSED: unexpected RuntimeError: boom\n'), False))

    def test_converted_files_verify_from_the_command_line(self):
        work = Work()
        self.addCleanup(work.close)
        before = work.write('before.json', survey_of(self.rows, self.bs))
        plan_path = work.root / 'plan.json'
        self.assertEqual(run_main(['plan', '--profile', 'sp11', '--survey', before, '--out', plan_path])[0], 0)
        good = applied(self.rows, json.loads(plan_path.read_text()))
        files = [work.write('windows.json', win_record(good, self.bs)), work.write('q1n1.json', survey_of(good, self.bs))]
        for fmt, name, text in (('sgdisk', 'mac-sgdisk', sgdisk_text(good, self.bs)),
                                ('gpt-show', 'mac-gpt', gpt_show_text(gpt_show_table(good, self.bs), self.bs))):
            source = work.write(f'{name}.txt', text)
            out = work.root / f'{name}.json'
            self.assertEqual(run_main(['convert-reader', '--from', fmt, '--in', source, '--label', name, '--out', out])[0], 0)
            files.append(out)
        rc, stdout, stderr = run_main(['verify', '--profile', 'sp11', '--before', before, *afters(files),
                                       '--plan', plan_path])
        self.assertEqual((rc, stdout, stderr), (0, 'PASS\n', ''))


class Sp11Convert512(ConverterCases, unittest.TestCase):
    bs = 512


class Sp11Convert4K(ConverterCases, unittest.TestCase):
    bs = 4096


@contextlib.contextmanager
def no_hang(what, seconds=5):
    """Fail the test, rather than hang the run, if a read or write blocks on a pipe."""
    def expired(*_):
        raise AssertionError(f'{what} blocked: the planner opened a pipe')
    old = signal.signal(signal.SIGALRM, expired)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


class FileGuards(unittest.TestCase):
    """The planner reads regular files of sane size and writes regular files; it never opens a device or a pipe."""
    NO_SUCH_NODE = '/dev/snapmac-sp11-test-no-such-node'
    pipes = unittest.skipUnless(hasattr(os, 'mkfifo') and hasattr(signal, 'SIGALRM'), 'needs mkfifo and SIGALRM')

    def setUp(self):
        work = Work()
        self.addCleanup(work.close)
        self.root = work.root

    def refused(self, fragment, call, *args):
        with self.assertRaisesRegex(pm.PlanError, re.escape(fragment)):
            call(*args)

    def test_a_directory_a_missing_file_and_an_oversized_file_are_refused_unread(self):
        big = self.root / 'big.txt'
        big.write_bytes(b'x' * 11)
        with mock.patch.object(Path, 'read_bytes', side_effect=AssertionError('the file was read')):
            self.refused('not a regular file', pm._read_file, self.root, 10)
            self.refused('larger than 10 bytes', pm._read_file, big, 10)
        big.write_bytes(b'x' * 10)
        self.assertEqual(pm._read_file(big, 10), 'x' * 10)
        self.refused('cannot be read (FileNotFoundError)', pm._read_file, self.root / 'nope', 10)

    @pipes
    def test_a_pipe_is_refused_without_being_opened(self):
        fifo = self.root / 'pipe'
        os.mkfifo(fifo)
        with no_hang('reading'):
            self.refused('not a regular file', pm._read_file, fifo, 100)
            self.refused('not a regular file', pm._load_json, fifo)
            rc, out, err = run_main(['plan', '--profile', 'sp11', '--survey', fifo])
        self.assertEqual((rc, out), (2, ''))
        self.assertIn('pipe: not a regular file', err)

    def test_text_is_decoded_the_way_powershell_and_the_mac_write_it(self):
        text = 'Gr\u00f6\u00dfe: 42\n'
        for name, data in (('plain', text.encode()), ('UTF-8 BOM', b'\xef\xbb\xbf' + text.encode()),
                           ('UTF-16 LE BOM', b'\xff\xfe' + text.encode('utf-16-le')),
                           ('UTF-16 BE BOM', b'\xfe\xff' + text.encode('utf-16-be'))):
            with self.subTest(name):
                path = self.root / f'{name}.txt'
                path.write_bytes(data)
                self.assertEqual(pm._read_file(path, 1000), text)

    def test_broken_encodings_are_text_that_is_not_json_never_a_crash(self):
        for name, data in (('invalid UTF-8', b'ab\x80cd'), ('BOM only', b'\xff\xfe'), ('odd UTF-16', b'\xff\xfea\x00b'),
                           ('empty', b'')):
            with self.subTest(name):
                path = self.root / 'broken.json'
                path.write_bytes(data)
                self.assertIsInstance(pm._read_file(path, 100), str)
                self.refused('broken.json: not JSON', pm._load_json, path)

    def test_json_in_the_encodings_powershell_writes_loads(self):
        for name, data in (('UTF-8 BOM', b'\xef\xbb\xbf{"a": 1}'), ('UTF-16 LE BOM', b'\xff\xfe' + '{"a": 1}'.encode('utf-16-le'))):
            path = self.root / f'{name}.json'
            path.write_bytes(data)
            self.assertEqual(pm._load_json(path), {'a': 1}, name)

    def test_json_that_would_exhaust_the_parser_is_a_refusal(self):
        path = self.root / 'deep.json'
        path.write_text('[]')
        # Which depth raises RecursionError depends on the Python build, so the handler is driven directly.
        with mock.patch.object(json, 'loads', side_effect=RecursionError):
            self.refused('JSON nested too deeply or a number too long', pm._load_json, path)

    @unittest.skipIf(sys.get_int_max_str_digits() == 0, 'this Python has no limit on integer digits')
    def test_json_with_an_absurdly_long_number_is_a_refusal(self):
        path = self.root / 'long.json'
        path.write_text('1' * (sys.get_int_max_str_digits() + 1))
        self.refused('JSON nested too deeply or a number too long', pm._load_json, path)

    def test_a_read_error_keeps_its_own_message_and_is_not_reported_as_bad_json(self):
        for path, fragment in ((self.root / 'nope.json', 'cannot be read (FileNotFoundError)'),
                               (self.root, 'not a regular file')):
            with self.assertRaises(pm.PlanError) as caught:
                pm._load_json(path)
            self.assertIn(fragment, str(caught.exception))
            self.assertNotIn('nested too deeply', str(caught.exception))
            self.assertNotIn('not JSON', str(caught.exception))

    def test_the_size_limits_apply_on_the_command_line(self):
        big = self.root / 'big'
        big.write_bytes(bytes((1 << 20) + 1))
        rc, out, err = run_main(['convert-reader', '--from', 'sgdisk', '--in', big, '--label', 'x',
                                 '--out', self.root / 'o.json'])
        self.assertEqual((rc, out), (2, ''))
        self.assertIn(f'big: larger than {1 << 20} bytes', err)
        with big.open('wb') as f:
            f.truncate((32 << 20) + 1)
        rc, out, err = run_main(['plan', '--profile', 'sp11', '--survey', big])
        self.assertEqual((rc, out), (2, ''))
        self.assertIn(f'big: larger than {32 << 20} bytes', err)

    def test_a_device_or_a_special_file_is_never_a_write_target(self):
        link = self.root / 'link-to-a-device'
        os.symlink(self.NO_SUCH_NODE, link)
        targets = [('a device name', self.NO_SUCH_NODE), ('a symlink to one', link), ('a directory', self.root)]
        if hasattr(os, 'mkfifo'):
            os.mkfifo(self.root / 'pipe')
            targets.append(('a pipe', self.root / 'pipe'))
        # If a guard failed, the write itself must still never happen: the test is not allowed to touch /dev.
        with mock.patch.object(Path, 'write_text', side_effect=AssertionError('the guard let the write through')):
            for name, target in targets:
                with self.subTest(name):
                    self.refused('refusing to write to a device or special file', pm._write_file, target, 'x')

    def test_every_output_option_refuses_a_device(self):
        rows = layout()
        before = self.root / 'before.json'
        before.write_text(json.dumps(survey_of(rows)))
        plan_path = self.root / 'plan.json'
        self.assertEqual(run_main(['plan', '--profile', 'sp11', '--survey', before, '--out', plan_path])[0], 0)
        after = self.root / 'after.json'
        after.write_text(json.dumps(survey_of(applied(rows, json.loads(plan_path.read_text())), 512)))
        text = self.root / 'sgdisk.txt'
        text.write_text(sgdisk_text(rows, 512))
        out = self.NO_SUCH_NODE
        commands = [('plan', ['plan', '--profile', 'sp11', '--survey', before]),
                    ('verify', ['verify', '--profile', 'sp11', '--before', before, '--after', after, '--plan', plan_path,
                                '--min-readers', 1]),
                    ('verify-undo', ['verify-undo', '--profile', 'sp11', '--before', before, '--after', before,
                                     '--min-readers', 1]),
                    ('convert-reader', ['convert-reader', '--from', 'sgdisk', '--in', text, '--label', 'x'])]
        with mock.patch.object(Path, 'write_text', side_effect=AssertionError('the guard let the write through')):
            for name, argv in commands:
                with self.subTest(name):
                    rc, stdout, stderr = run_main(argv + ['--out', out])
                    self.assertEqual((rc, stdout), (2, ''))
                    self.assertIn('refusing to write to a device or special file', stderr)

    def test_a_regular_file_is_overwritten_and_a_missing_directory_is_a_refusal(self):
        path = self.root / 'out.json'
        pm._write_file(path, 'one\n')
        pm._write_file(path, 'two\n')
        self.assertEqual(path.read_text(), 'two\n')
        self.refused('cannot be written (FileNotFoundError)', pm._write_file, self.root / 'no' / 'dir' / 'x', 'x')


A16_PIN_NAMES = frozenset('DISK_BYTES BLOCK DISK_GUID EUI BEFORE_NEIGHBOUR AFTER_NEIGHBOUR ESP WINDOWS_C APPROVED_EXTENT '
                          'PINNED GUARD_LBAS MIN_BYTES the_disk check_disk plan verify verify_undo'.split())


def a16_pins():
    """The A16 profile's machine-specific identifiers and large numbers, read out of the planner's own constants.

    The public Windows partition type GUIDs, the Snapintosh type and the 1 MiB guard are shared by both profiles."""
    found = set()

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                walk(k)
                walk(v)
        elif isinstance(x, (tuple, list)):
            for v in x:
                walk(v)
        elif isinstance(x, str) and re.fullmatch(r'[0-9a-fA-F]{8}-[0-9a-fA-F-]{27}|[0-9A-F]{16}', x):
            found.add(x)
        elif isinstance(x, int) and not isinstance(x, bool) and x > 1 << 20:
            found.add(x)

    for name in ('DISK_BYTES', 'DISK_GUID', 'EUI', 'BEFORE_NEIGHBOUR', 'AFTER_NEIGHBOUR', 'ESP', 'WINDOWS_C',
                 'APPROVED_EXTENT', 'PINNED'):
        walk(getattr(pm, name))
    return found - set(pm.WINDOWS_TYPES) - {pm.TYPE, pm.NIL}


def a16_uses(nodes, pins):
    """Every A16 name or pinned value the given syntax nodes mention."""
    found = []
    for node in nodes:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and sub.id in A16_PIN_NAMES:
                found.append(f'name {sub.id} at line {sub.lineno}')
            elif (isinstance(sub, ast.Constant) and isinstance(sub.value, (str, int)) and not isinstance(sub.value, bool)
                  and sub.value in pins):
                found.append(f'a pinned value at line {sub.lineno}')
    return found


def a16_values_in(text, pins):
    return sorted((pin for pin in pins if (pin.lower() in text.lower() if isinstance(pin, str)
                                           else re.search(rf'(?<![0-9]){pin}(?![0-9])', text))), key=str)


class NoA16PinsInTheSp11Code(unittest.TestCase):
    """The sp11 profile must take every unit value from BEFORE, so it may not even mention an A16 pin."""

    def setUp(self):
        source = PLANNER.read_text()
        lines = source.splitlines()
        marker = next(i for i, line in enumerate(lines, 1) if line.startswith('# sp11 profile.'))
        # main() is the one place both profiles meet: it dispatches to the A16 functions by name.
        self.nodes = [n for n in ast.parse(source).body if n.lineno >= marker and getattr(n, 'name', '') != 'main']
        self.region = '\n'.join(lines[marker - 1:])
        self.pins = a16_pins()

    def test_the_scan_sees_the_pins_and_the_sp11_code(self):
        self.assertGreaterEqual(len(self.pins), 10)
        self.assertTrue({pm.DISK_GUID, pm.EUI, pm.DISK_BYTES, pm.ESP, pm.WINDOWS_C} <= self.pins)
        names = {n.name for n in self.nodes if hasattr(n, 'name')}
        self.assertTrue({'plan_sp11', 'verify_sp11', 'verify_undo_sp11', 'convert_sgdisk', 'convert_gpt_show',
                         'main_sp11', 'load_reader'} <= names, names)
        self.assertNotIn('main', names)
        self.assertEqual(a16_uses(ast.parse(f'x = DISK_GUID\ny = {pm.EUI!r}\nz = {pm.DISK_BYTES}').body, self.pins),
                         ['name DISK_GUID at line 1', 'a pinned value at line 2', 'a pinned value at line 3'])
        self.assertEqual(a16_values_in(f'a {pm.DISK_GUID.upper()} b {pm.DISK_BYTES} c', self.pins),
                         sorted({pm.DISK_GUID, pm.DISK_BYTES}, key=str))
        self.assertEqual(a16_values_in(f'{pm.DISK_BYTES}0 and 9{pm.DISK_BYTES}', self.pins), [])

    def test_the_sp11_code_names_and_contains_no_a16_pin(self):
        self.assertEqual(a16_uses(self.nodes, self.pins), [])
        self.assertEqual(a16_values_in(self.region, self.pins), [])

    def test_an_sp11_plan_and_this_test_carry_no_a16_pin(self):
        plan = pm.plan_sp11(survey_of(layout(), 512))
        self.assertEqual(a16_values_in(json.dumps(plan) + '\n'.join(pm.plan_text(plan)), self.pins), [])
        self.assertEqual(a16_values_in(Path(__file__).read_text(), self.pins), [])


if __name__ == '__main__':
    unittest.main()
