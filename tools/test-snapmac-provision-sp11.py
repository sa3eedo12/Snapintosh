#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""SP11 offline storage provisioning: the whole flow on synthetic image files, every refusal, the authorisation record.

  python3 tools/test-snapmac-provision-sp11.py            (the image layer: tools/test-snapmac-image-sp11.py; the forged plan and the write-time rules: tools/test-snapmac-provision-sp11-gate.py)

Every disk here is a synthetic 128 MiB image FILE built in code from the planner test's fixtures (obviously fake identifiers, decoded by q1n1's
tools/gpt_survey.py, the decoder the real survey uses). The tests read and write only files under build/test-snapmac-provision-sp11 and never
open a disk or a device: they prove the tools refuse to. Required refusals, each with a deliberately faulty case: a protected extent touched,
a write one sector past the partition end, a stale pre-image, a missing confirmation, a device or volume path in every position, and the
authorised device mode with no record, a wrong record and a valid one. Evidence: EXPERIMENTAL (synthetic images only); hardware NOT RUN.
"""
import contextlib
import datetime
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock
import zlib

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import snapmac_image as si  # noqa: E402
import snapmac_provision as sp  # noqa: E402
import snapmac_window as sw  # noqa: E402


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pt = load('planner_sp11_test_for_provision', HERE / 'test-snapmac-partition-plan-sp11.py')
SCRATCH = ROOT / 'build' / 'test-snapmac-provision-sp11'
MIB = 1 << 20
BS = 512
DISK = 128 * MIB
ISC_REGION = 4 * MIB
MIN_BYTES = 32 * MIB
STAMP = '2026-11-17T03:04:05Z'


def pattern(seed, n):
    block = hashlib.shake_256(seed.encode()).digest(65536)
    return (block * (n // len(block) + 1))[:n]


def rows_before():
    """ESP, MSR, a shrunk Windows volume ending at 60 MiB, 59 MiB of free space, a recovery partition 1 MiB short of the disk end."""
    return [pt.Row(0, pt.ESP, pt.T_ESP, 1 * MIB, 8 * MIB, 'EFI system partition'),
            pt.Row(1, pt.MSR, pt.T_MSR, 9 * MIB, 4 * MIB, 'Microsoft reserved partition'),
            pt.Row(2, pt.WINC, pt.T_BASIC, 13 * MIB, 47 * MIB, 'Basic data partition'),
            pt.Row(3, pt.REC, pt.T_RECOVERY, 119 * MIB, 8 * MIB, 'Windows recovery')]


def make_image(path, rows):
    """A sparse 128 MiB image: both GPT zones, and marked bytes at the start, the middle and the end of every partition, of the free space and
    of the window's future partition, so that two different extents never hash alike and a write of zeros is a change."""
    first, last = pt.windows(rows, BS, DISK, pt.DISK_GUID)
    mark = 64 * 1024
    with open(path, 'wb') as f:
        f.truncate(DISK)
        for offset, size, seed in [(r.offset, r.size, r.name) for r in rows] + [(60 * MIB, 59 * MIB, 'old free space'), (61 * MIB, 57 * MIB, 'new partition')]:
            for where in (offset, offset + size // 2, offset + size - mark):
                f.seek(where)
                f.write(pattern(f'{seed} {where}', mark))
        f.seek(61 * MIB + MIB + 2 * MIB)
        f.write(pattern('ISC region tail', 2 * MIB))                  # the writer zero-fills it: a pattern shows whether it did
        f.seek(61 * MIB + 512 * 1024)
        f.write(pattern('old header', 4096))
        f.seek(0)
        f.write(first)
        f.seek(DISK - MIB)
        f.write(last)


def copy_image(source, destination):
    """Copies only the data extents of a sparse image: holes stay holes, so an image costs what it holds, not its size."""
    with open(source, 'rb') as a, open(destination, 'wb') as b:
        size = os.fstat(a.fileno()).st_size
        b.truncate(size)
        position = 0
        while position < size:
            try:
                start = os.lseek(a.fileno(), position, os.SEEK_DATA)
            except OSError:                                          # no data after this point
                break
            position = os.lseek(a.fileno(), start, os.SEEK_HOLE)
            a.seek(start)
            b.seek(start)
            left = position - start
            while left:
                block = a.read(min(left, 8 * MIB))
                b.write(block)
                left -= len(block)


def rewrite_gpt(path, rows):
    first, last = pt.windows(rows, BS, DISK, pt.DISK_GUID)
    with open(path, 'r+b') as f:
        f.write(first)
        f.seek(DISK - MIB)
        f.write(last)


def readers_for(rows):
    return [('windows', pt.win_record(rows, BS, disk=DISK)), ('mac-raw', pt.survey_of(rows, BS, eui=None, disk=DISK)),
            ('q1n1', pt.survey_of(rows, BS, eui=pt.EUI, disk=DISK)), ('windows-boot', pt.win_record(rows, BS, disk=DISK, reader='windows-boot'))]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def flip(path, offset, mask=0xff):
    with open(path, 'r+b') as f:
        f.seek(offset)
        byte = f.read(1)
        f.seek(offset)
        f.write(bytes([byte[0] ^ mask]))


def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = sp.main([str(a) for a in argv])
        except SystemExit as e:
            rc = e.code
    return rc, out.getvalue(), err.getvalue()


class Fixture:
    """Built once: the disk before the operator's step, the plan, the disk after it, the four readers, the pre-image, a container and an ISC image."""

    def __init__(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self._tmp = tempfile.TemporaryDirectory(dir=SCRATCH)
        base = Path(self._tmp.name)
        self.before_rows = rows_before()
        self.survey_before = pt.survey_of(self.before_rows, BS, eui=pt.EUI, disk=DISK)
        self.w = sp.window_plan(self.survey_before, ISC_REGION, min_bytes=MIN_BYTES)
        self.after_rows = pt.applied(self.before_rows, self.w['plan'])
        self.readers = readers_for(self.after_rows)
        self.before_image = base / 'before.img'
        make_image(self.before_image, self.before_rows)
        self.golden = base / 'golden.img'
        copy_image(self.before_image, self.golden)
        rewrite_gpt(self.golden, self.after_rows)
        self.pre = sp.pre_image(self.golden, self.w, self.survey_before, self.readers)
        self.container = base / 'container.img'
        self.container.write_bytes(pattern('container', 20 * MIB))
        self.isc = base / 'isc.img'
        self.isc.write_bytes(pattern('isc', 2 * MIB))

    def close(self):
        self._tmp.cleanup()


FX = None


def setUpModule():
    global FX
    FX = Fixture()


def tearDownModule():
    FX.close()


class FlowBase(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self._tmp = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.before_rows, self.after_rows, self.survey_before = FX.before_rows, FX.after_rows, FX.survey_before
        self.w, self.pre, self.readers = FX.w, FX.pre, FX.readers
        self.golden, self.before_image, self.container, self.isc = FX.golden, FX.before_image, FX.container, FX.isc

    def file(self, name, data=b''):
        path = self.root / name
        path.write_bytes(data)
        return path

    def jsonfile(self, name, doc):
        return self.file(name, json.dumps(doc).encode())


class Flow(FlowBase):
    """The plan, the pre-image, the write, the checks, the undo and the command line, on one image copied afresh for each test."""

    def setUp(self):
        super().setUp()
        self.img = self.root / 'snapintosh-test.img'
        copy_image(self.golden, self.img)

    def write(self, **over):
        args = dict(image_path=self.img, w=self.w, pre=self.pre, container=self.container, isc=self.isc, confirm=self.img.name, stamp=STAMP,
                    before=self.survey_before, readers=self.readers)
        args.update(over)
        return sp.write_window(**args)

    # -- the plan
    def test_the_plan_is_the_planners_and_the_window_fits_it(self):
        n = self.w['plan']['new']
        self.assertEqual((n['offset'], n['bytes']), (61 * MIB, 57 * MIB))
        self.assertEqual(self.w['plan_sha256'], sp.digest_doc(self.w['plan']))
        lay = self.w['layout']
        self.assertEqual((lay['header_offset'], lay['isc_offset'], lay['container_offset'], lay['reserved_tail_offset']),
                         (512 << 10, MIB, MIB + ISC_REGION, 57 * MIB - MIB))
        self.assertEqual(lay['container_region'], 57 * MIB - 2 * MIB - ISC_REGION)

    def test_the_protected_extents_tile_everything_outside_the_new_partition(self):
        n, extents = self.w['plan']['new'], self.w['protected_extents']
        self.assertEqual(sum(e['bytes'] for e in extents), DISK - n['bytes'])
        cursor = 0
        for e in extents:
            if cursor == n['offset']:
                cursor = n['end']
            self.assertEqual(e['offset'], cursor, e['name'])
            cursor = e['offset'] + e['bytes']
        self.assertEqual(cursor, DISK)
        names = [e['name'] for e in extents]
        for wanted in ('gpt-primary', 'gpt-backup', 'guard-before', 'guard-after', 'partition-0', 'partition-3'):
            self.assertIn(wanted, names)
        by_name = {e['name']: e for e in extents}
        self.assertEqual((by_name['guard-before']['offset'], by_name['guard-before']['bytes']), (60 * MIB, MIB))
        self.assertEqual((by_name['guard-after']['offset'], by_name['guard-after']['bytes']), (118 * MIB, MIB))
        self.assertTrue(any(name.startswith('unallocated-') for name in names), 'the gaps are protected too')

    def test_a_plan_document_that_was_edited_is_refused(self):
        cases = {
            'a digest that does not match': lambda w: w.__setitem__('plan_sha256', '0' * 64),
            'the new offset moved': lambda w: w['plan']['new'].__setitem__('offset', w['plan']['new']['offset'] + 512),
            'a layout edited': lambda w: w['layout'].__setitem__('container_region', w['layout']['container_region'] + 512),
            'an extent dropped': lambda w: w['protected_extents'].pop(),
            'an extent shrunk': lambda w: w['protected_extents'][1].__setitem__('bytes', w['protected_extents'][1]['bytes'] - 512),
            'the format': lambda w: w.__setitem__('format', 'snapmac-window-plan/2'),
            'no plan': lambda w: w.__setitem__('plan', None),
        }
        for name, edit in cases.items():
            w = json.loads(json.dumps(self.w))
            edit(w)
            if name not in ('a digest that does not match', 'no plan'):
                w['plan_sha256'] = sp.digest_doc(w['plan'])             # re-sealed: only the recomputation can find the edit
            with self.subTest(edit=name):
                with self.assertRaises(sp.ProvisionError):
                    sp.check_plan_document(w)
        sp.check_plan_document(self.w)

    def test_a_plan_that_cannot_hold_the_window_is_refused(self):
        for name, args in (('an ISC region as big as the partition', dict(isc_region=57 * MIB)), ('an ISC region that is not whole 4 KiB blocks', dict(isc_region=4 * MIB + 512)),
                           ('no ISC region', dict(isc_region=0)), ('a free extent below the minimum', dict(isc_region=ISC_REGION, min_bytes=60 * MIB))):
            args.setdefault('min_bytes', MIN_BYTES)
            with self.subTest(case=name):
                with self.assertRaisesRegex(sp.ProvisionError, 'refused'):
                    sp.window_plan(self.survey_before, **args)

    # -- the pre-image
    def test_the_pre_image_digests_every_protected_extent_and_binds_the_plan(self):
        self.assertEqual(self.pre['plan_sha256'], self.w['plan_sha256'])
        self.assertEqual(len(self.pre['extents']), len(self.w['protected_extents']))
        self.assertTrue(self.pre['gate']['four_reader_gate_met'])
        self.assertEqual(self.pre['new']['guid'], pt.NEW_GUID)
        with si.Image(self.img) as image:
            for e in self.pre['extents']:
                self.assertEqual(image.digest(e['offset'], e['bytes']), e['sha256'], e['name'])

    def test_the_pre_image_refuses_an_image_of_another_disk_and_names_why_the_gate_did_not_open(self):
        first, last = pt.windows(self.after_rows, BS, DISK, pt.G(2))                   # another GPT disk GUID
        other = self.root / 'other-guid.img'
        copy_image(self.golden, other)
        with open(other, 'r+b') as f:
            f.write(first)
            f.seek(DISK - MIB)
            f.write(last)
        with self.assertRaisesRegex(sp.ProvisionError, 'not the disk the plan was made for'):
            sp.pre_image(other, self.w, self.survey_before, self.readers)
        first, last = pt.windows(self.after_rows, 4096, DISK, pt.DISK_GUID)           # 4096-byte blocks, the same disk size
        blocks = self.root / 'other-block.img'
        copy_image(self.golden, blocks)
        with open(blocks, 'r+b') as f:
            f.write(bytes(MIB))
            f.seek(DISK - MIB)
            f.write(bytes(MIB))
            f.seek(0)
            f.write(first)
            f.seek(DISK - MIB)
            f.write(last)
        with self.assertRaisesRegex(sp.ProvisionError, 'not the disk the plan was made for'):
            sp.pre_image(blocks, self.w, self.survey_before, self.readers)
        with self.assertRaisesRegex(sp.ProvisionError, 'gate refuses'):
            sp.pre_image(self.img, self.w, {'disks': []}, self.readers)
        with self.assertRaisesRegex(sp.ProvisionError, 'below the 4'):
            sp.pre_image(self.img, self.w, self.survey_before, self.readers, min_readers=2)

    def test_a_plan_whose_extents_do_not_tile_the_disk_is_refused(self):
        def refused(edit, fragment):
            plan = json.loads(json.dumps(self.w['plan']))
            edit(plan)
            with self.assertRaisesRegex(sp.ProvisionError, fragment):
                sp.protected_extents(plan)
        refused(lambda p: p['protected'][2].__setitem__('first_lba', p['protected'][1]['first_lba'] + 1), 'overlap')
        refused(lambda p: p['protected'][3].update(first_lba=DISK // BS + 100, last_lba=DISK // BS + 200), 'leaves the disk')
        refused(lambda p: p['new'].__setitem__('offset', p['new']['offset'] + 512), 'not the one gap')
        refused(lambda p: p['protected'].append(dict(p['protected'][0], index=9, first_lba=p['new']['first_lba'], last_lba=p['new']['last_lba'])), 'not the one gap')
        self.assertTrue(all(e['bytes'] > 0 for e in sp.protected_extents(self.w['plan'])), 'no extent is empty')

    def test_the_new_entry_must_be_the_one_the_pre_image_saw(self):
        rewrite_gpt(self.img, pt.applied(self.before_rows, self.w['plan'], guid=pt.G(92)))        # deleted and created again
        before = sha(self.img)
        with self.assertRaisesRegex(sp.StalePreimage, 'not the one the pre-image saw'):
            self.write()
        self.assertEqual(sha(self.img), before)

    def test_the_pre_image_refuses_what_is_not_the_plans_disk(self):
        wrong_rows = [r._replace(guid=pt.G(77)) if r.guid == pt.ESP else r for r in self.after_rows]
        cases = {
            'the gate has three readers': self.readers[:3],
            'a reader disagrees': self.readers[:3] + [('windows-boot', pt.win_record(self.before_rows, BS, disk=DISK, reader='windows-boot'))],
        }
        for name, readers in cases.items():
            with self.subTest(case=name):
                with self.assertRaisesRegex(sp.ProvisionError, 'gate'):
                    sp.pre_image(self.img, self.w, self.survey_before, readers)
        with self.assertRaisesRegex(sp.ProvisionError, 'gate'):
            sp.pre_image(self.img, self.w, self.survey_before, self.readers, min_readers=2)
        for name, path_rows in (('the operator has not created the entry', self.before_rows), ('a protected entry changed', wrong_rows),
                                ('two entries of the new type', pt.applied(self.after_rows, self.w['plan'], guid=pt.G(91)))):
            image = self.root / 'other.img'
            copy_image(self.golden, image)
            rewrite_gpt(image, path_rows)
            with self.subTest(case=name):
                with self.assertRaises(sp.ProvisionError):
                    sp.pre_image(image, self.w, self.survey_before, self.readers)
        with open(self.img, 'ab') as f:
            f.write(bytes(512))
        with self.assertRaisesRegex(sp.ProvisionError, 'bytes, the plan'):
            sp.pre_image(self.img, self.w, self.survey_before, self.readers)

    def test_the_new_entry_must_sit_at_the_planned_lbas_and_carry_allowed_attributes(self):
        n = self.w['plan']['new']
        for name, rows in (('moved', pt.applied(self.before_rows, self.w['plan'], first=n['first_lba'] + 32)),
                           ('shorter', pt.applied(self.before_rows, self.w['plan'], last=n['last_lba'] - 32)),
                           ('attributes', pt.applied(self.before_rows, self.w['plan'], attrs=1 << 2))):
            image = self.root / f'{name}.img'
            copy_image(self.before_image, image)
            rewrite_gpt(image, rows)
            with self.subTest(case=name):
                with self.assertRaises(sp.ProvisionError):
                    sp.pre_image(image, self.w, self.survey_before, readers_for(self.after_rows))

    # -- the write
    def test_the_write_puts_the_window_in_the_partition_and_changes_nothing_outside_it(self):
        n, lay = self.w['plan']['new'], self.w['layout']
        with si.Image(self.img) as image:
            before = image.digest_outside(n['offset'], n['end'])
        journal = self.write()
        self.assertEqual(sp.post_check(self.img, self.w, self.pre, journal), [])
        with si.Image(self.img) as image:
            self.assertEqual(image.digest_outside(n['offset'], n['end']), before)
            for e in self.pre['extents']:
                self.assertEqual(image.digest(e['offset'], e['bytes']), e['sha256'], e['name'])
            self.assertEqual(image.read(n['offset'] + lay['isc_offset'], 2 * MIB), self.isc.read_bytes())
            self.assertEqual(image.read(n['offset'] + lay['isc_offset'] + 2 * MIB, ISC_REGION - 2 * MIB), bytes(ISC_REGION - 2 * MIB))
            self.assertEqual(image.read(n['offset'] + lay['container_offset'], 20 * MIB), self.container.read_bytes())
            h = sw.parse_header(image.read(n['offset'] + lay['header_offset'], 4096))
        self.assertEqual((h['state'], h['guid'], h['container_bytes'], h['isc_bytes'], h['stamp']), (1, pt.NEW_GUID, 20 * MIB, 2 * MIB, STAMP))

    def test_without_a_stamp_the_header_gets_the_current_utc_time(self):
        self.write(stamp=None)
        n, lay = self.w['plan']['new'], self.w['layout']
        with si.Image(self.img) as image:
            h = sw.parse_header(image.read(n['offset'] + lay['header_offset'], 4096))
        made = datetime.datetime.strptime(h['stamp'], sp.STAMP).replace(tzinfo=datetime.timezone.utc)
        self.assertLess(abs((datetime.datetime.now(datetime.timezone.utc) - made).total_seconds()), 120)

    def test_every_journaled_write_is_inside_the_partition_and_the_header_is_last(self):
        journal = self.write()
        n, lay = self.w['plan']['new'], self.w['layout']
        for e in journal['writes']:
            self.assertGreaterEqual(e['offset'], n['offset'])
            self.assertLessEqual(e['offset'] + e['length'], n['end'])
            self.assertEqual((e['offset'] % 512, e['length'] % 512), (0, 0))
            self.assertLessEqual(e['length'], si.CHUNK)
        self.assertEqual(sum(e['length'] for e in journal['writes']), ISC_REGION + 20 * MIB + 4096)
        self.assertEqual(journal['writes'][-1]['offset'], n['offset'] + lay['header_offset'])
        self.assertEqual(sum(1 for e in journal['writes'] if e['offset'] == n['offset'] + lay['header_offset']), 1)
        self.assertEqual(journal['header_sha256'], journal['writes'][-1]['sha256'])
        self.assertEqual(journal['preimage_sha256'], sp.digest_doc(self.pre))

    def test_a_stale_pre_image_is_refused_before_the_first_write(self):
        stale, gpt = sp.StalePreimage, sp.ProvisionError
        for name, offset, expected in (('windows volume', 20 * MIB, stale), ('esp', 2 * MIB, stale), ('recovery', 120 * MIB, stale),
                                       ('guard before', 60 * MIB + 4096, stale), ('guard after', 118 * MIB + 4096, stale),
                                       ('the gap after the primary GPT', 512 * 1024, stale), ('the gap before the backup GPT', 127 * MIB + 4096, stale),
                                       ('the protective MBR', 450, stale), ('the primary entry array', 2 * 512 + 5, gpt), ('the backup entry array', DISK - 8192, gpt)):
            image = self.root / 'stale.img'
            copy_image(self.golden, image)
            flip(image, offset)
            changed = sha(image)
            with self.subTest(changed=name):
                with self.assertRaises(expected) as caught:
                    sp.write_window(image, self.w, self.pre, self.container, self.isc, image.name, STAMP, before=self.survey_before, readers=self.readers)
                if expected is gpt:
                    self.assertNotIsInstance(caught.exception, stale)
                    self.assertIn('GPT', str(caught.exception))
                self.assertEqual(sha(image), changed, 'a refused write must leave the image as it was')

    def test_a_missing_or_wrong_confirmation_is_refused_and_nothing_changes(self):
        before = sha(self.img)
        for confirm in ('', 'other.img', None, str(self.img)):
            with self.subTest(confirm=confirm):
                with self.assertRaisesRegex(sp.ProvisionError, 'missing confirmation'):
                    self.write(confirm=confirm)
                self.assertEqual(sha(self.img), before)

    def test_a_pre_image_of_another_plan_or_edited_is_refused(self):
        before = sha(self.img)
        for name, edit in (('plan', lambda p: p.__setitem__('plan_sha256', '1' * 64)), ('gate', lambda p: p['gate'].__setitem__('four_reader_gate_met', False)),
                           ('extent digest', lambda p: p['extents'][0].__setitem__('sha256', '0' * 64)), ('format', lambda p: p.__setitem__('format', 'x')),
                           ('an extent dropped', lambda p: p['extents'].pop()), ('the new entry', lambda p: p['new'].__setitem__('guid', pt.G(92)))):
            pre = json.loads(json.dumps(self.pre))
            edit(pre)
            with self.subTest(edit=name):
                with self.assertRaises(sp.ProvisionError):
                    self.write(pre=pre)
                self.assertEqual(sha(self.img), before)

    def test_a_pre_image_that_does_not_cover_the_plans_extents_is_refused_even_when_it_is_sealed_again(self):
        before = sha(self.img)
        for name, edit in (('an extent dropped', lambda e: e.pop()), ('an extent shifted', lambda e: e[1].__setitem__('offset', e[1]['offset'] + 512)),
                           ('the extents reversed', lambda e: e.reverse())):
            pre = json.loads(json.dumps(self.pre))
            edit(pre['extents'])
            pre['extents_sha256'] = sp.digest_doc(pre['extents'])
            with self.subTest(edit=name):
                with self.assertRaisesRegex(sp.ProvisionError, 'does not cover'):
                    self.write(pre=pre)
                self.assertEqual(sha(self.img), before)

    def test_a_window_that_does_not_fit_is_refused_before_any_write(self):
        before = sha(self.img)
        lay = self.w['layout']
        big = self.file('big.img', bytes(lay['container_region'] + 4096))
        odd = self.file('odd.img', bytes(8192 + 512))
        bigisc = self.file('bigisc.img', bytes(ISC_REGION + 4096))
        oddisc = self.file('oddisc.img', bytes(4096 + 512))
        for name, over in (('container larger than its region', dict(container=big)), ('container not whole 4 KiB blocks', dict(container=odd)),
                           ('ISC larger than its region', dict(isc=bigisc)), ('ISC not whole 4 KiB blocks', dict(isc=oddisc)),
                           ('empty container', dict(container=self.file('empty.img'))), ('empty ISC image', dict(isc=self.file('emptyisc.img')))):
            with self.subTest(case=name):
                with self.assertRaises((sp.ProvisionError, sw.WindowError)):
                    self.write(**over)
                self.assertEqual(sha(self.img), before)

    def test_a_container_that_fills_its_region_exactly_is_written_and_checks(self):
        lay, n = self.w['layout'], self.w['plan']['new']
        exact = self.file('exact.img', pattern('exact', lay['container_region']))
        journal = self.write(container=exact)
        self.assertEqual(sp.post_check(self.img, self.w, self.pre, journal), [])
        end = n['offset'] + lay['container_offset'] + lay['container_region']
        self.assertEqual(max(e['offset'] + e['length'] for e in journal['writes'] if e['length'] > 4096), end)
        self.assertLessEqual(end, n['end'] - MIB, 'the last MiB of the partition stays free for the backup GPT the monitor synthesizes')

    def test_the_write_refuses_an_image_that_is_not_the_one_the_plan_describes(self):
        rewrite_gpt(self.img, self.before_rows)                       # the operator never created the entry
        before = sha(self.img)
        with self.assertRaisesRegex(sp.ProvisionError, 'not exactly one entry'):
            self.write()
        self.assertEqual(sha(self.img), before)
        grown = self.root / 'grown.img'
        copy_image(self.golden, grown)
        with open(grown, 'ab') as f:
            f.write(bytes(512))
        with self.assertRaisesRegex(sp.ProvisionError, 'bytes, the plan'):
            sp.write_window(grown, self.w, self.pre, self.container, self.isc, grown.name, before=self.survey_before, readers=self.readers)

    def test_a_device_or_volume_path_is_never_an_image_a_container_or_an_isc_image(self):
        before = sha(self.img)
        for path in ('/dev/null', '/Volumes/x/snapintosh.img', '/volumes/x/snapintosh.img'):
            with self.subTest(path=path):
                with self.assertRaises(si.ImageError):
                    sp.write_window(path, self.w, self.pre, self.container, self.isc, os.path.basename(path), before=self.survey_before, readers=self.readers)
                with self.assertRaises(si.ImageError):
                    sp.pre_image(path, self.w, self.survey_before, self.readers)
                with self.assertRaises(si.ImageError):
                    self.write(container=path)
                with self.assertRaises(si.ImageError):
                    self.write(isc=path)
                self.assertEqual(sha(self.img), before)

    def test_no_header_is_written_when_the_container_does_not_read_back(self):
        n, lay = self.w['plan']['new'], self.w['layout']
        lo = n['offset'] + lay['container_offset']
        real = si.Image._write

        def lossy(image, offset, data):
            if lo <= offset < lo + lay['container_region']:
                return                                                  # a disk that acknowledges the write and loses the data
            real(image, offset, data)
        with mock.patch.object(si.Image, '_write', lossy):
            with self.assertRaisesRegex(sp.ProvisionError, 'does not read back'):
                self.write()
        with si.Image(self.img) as image:
            with self.assertRaises(sw.WindowError):
                sw.parse_header(image.read(n['offset'] + lay['header_offset'], sw.HEADER_BYTES))
            for e in self.pre['extents']:
                self.assertEqual(image.digest(e['offset'], e['bytes']), e['sha256'], e['name'])

    # -- after the write
    def test_post_check_finds_a_changed_protected_extent_a_bad_journal_and_a_damaged_window(self):
        journal = self.write()
        n, lay = self.w['plan']['new'], self.w['layout']
        for name, offset, fragment in (('protected byte', 14 * MIB, 'partition-2'), ('container byte', n['offset'] + lay['container_offset'] + 12345, 'container does not read back'),
                                       ('ISC byte', n['offset'] + lay['isc_offset'] + 7, 'ISC image does not read back'),
                                       ('header byte', n['offset'] + lay['header_offset'] + 20, 'the header')):
            flip(self.img, offset)
            with self.subTest(damage=name):
                self.assertTrue(any(fragment in p for p in sp.post_check(self.img, self.w, self.pre, journal)), sp.post_check(self.img, self.w, self.pre, journal))
            flip(self.img, offset)
        self.assertEqual(sp.post_check(self.img, self.w, self.pre, journal), [])
        for name, edit, fragment in (
                ('a write outside the partition', lambda j: j['writes'].append(dict(offset=14 * MIB, length=512, sha256='0' * 64)), 'outside the partition'),
                ('a write one sector past the end', lambda j: j['writes'].insert(0, dict(offset=n['end'], length=512, sha256='0' * 64)), 'outside the partition'),
                ('a write that starts before the partition', lambda j: j['writes'].insert(0, dict(offset=n['offset'] - 512, length=1024, sha256='0' * 64)), 'outside the partition'),
                ('the header not last', lambda j: j['writes'].reverse(), 'not the last write'),
                ('another pre-image', lambda j: j.__setitem__('preimage_sha256', '2' * 64), 'another plan or another pre-image'),
                ('another plan', lambda j: j.__setitem__('plan_sha256', '2' * 64), 'another plan or another pre-image'),
                ('no write', lambda j: j.__setitem__('writes', []), 'no write'),
                ('another manifest', lambda j: j['manifest']['container'].__setitem__('bytes', 1), 'manifest digest')):
            j = json.loads(json.dumps(journal))
            edit(j)
            with self.subTest(journal=name):
                self.assertTrue(any(fragment in p for p in sp.post_check(self.img, self.w, self.pre, j)), sp.post_check(self.img, self.w, self.pre, j))
        self.assertEqual(sp.post_check(self.img, self.w, self.pre, {'format': 'x'}), ['not a write journal'])

    def test_post_check_finds_an_unwritten_window_and_an_entry_that_is_gone(self):
        fake = dict(format=sp.JOURNAL_FORMAT, plan_sha256=self.w['plan_sha256'], preimage_sha256=sp.digest_doc(self.pre), manifest={},
                    writes=[dict(offset=61 * MIB, length=512, sha256='0' * 64)])
        self.assertTrue(sp.post_check(self.img, self.w, self.pre, fake))
        journal = self.write()
        rewrite_gpt(self.img, self.before_rows)
        self.assertTrue(any(p.startswith('the GPT') for p in sp.post_check(self.img, self.w, self.pre, journal)))

    def test_post_check_accepts_writes_that_touch_the_partitions_edges_and_nothing_else(self):
        journal = self.write()
        n = self.w['plan']['new']
        edges = json.loads(json.dumps(journal))
        edges['writes'][:0] = [dict(offset=n['offset'], length=512, sha256='0' * 64), dict(offset=n['end'] - 512, length=512, sha256='0' * 64)]
        self.assertEqual(sp.post_check(self.img, self.w, self.pre, edges), [])

    def test_post_check_finds_a_header_that_is_valid_and_is_not_the_plans(self):
        journal = self.write()
        n, lay = self.w['plan']['new'], self.w['layout']
        at = n['offset'] + lay['header_offset']
        with si.Image(self.img) as image:
            good = sw.parse_header(image.read(at, sw.HEADER_BYTES))
        other_layout = sw.layout(n['offset'] + 16384, n['bytes'] - 16384, ISC_REGION)
        wrong_state = bytearray(sw.build_header(lay, pt.NEW_GUID, 2 * MIB, good['isc_sha256'], 20 * MIB, good['container_sha256'], good['manifest_sha256'], STAMP))
        wrong_state[52:56] = struct.pack('<I', 2)
        wrong_state[-4:] = struct.pack('<I', zlib.crc32(bytes(wrong_state[:-4])))
        for name, header, field in (
                ('another partition GUID', sw.build_header(lay, pt.G(93), 2 * MIB, good['isc_sha256'], 20 * MIB, good['container_sha256'], good['manifest_sha256'], STAMP), 'guid'),
                ('another layout', sw.build_header(other_layout, pt.NEW_GUID, 2 * MIB, good['isc_sha256'], 20 * MIB, good['container_sha256'], good['manifest_sha256'], STAMP), 'first_lba'),
                ('a state that is not provisioned', bytes(wrong_state), 'state')):
            with open(self.img, 'r+b') as f:
                f.seek(at)
                f.write(header)
            with self.subTest(name):
                self.assertTrue(any(p.startswith(f'the header says {field} ') for p in sp.post_check(self.img, self.w, self.pre, journal)),
                                sp.post_check(self.img, self.w, self.pre, journal))

    def test_a_header_that_does_not_read_back_is_an_error(self):
        n, lay = self.w['plan']['new'], self.w['layout']
        at, real = n['offset'] + lay['header_offset'], si.Image._write

        def lossy(image, offset, data):
            if offset != at:                                                  # a disk that loses the header and nothing else
                real(image, offset, data)
        with mock.patch.object(si.Image, '_write', lossy):
            with self.assertRaisesRegex(sp.ProvisionError, 'header does not read back'):
                self.write()

    # -- the undo
    def test_verify_undo_accepts_the_entry_removed_and_the_writes_in_the_freed_extent(self):
        journal = self.write()
        undone = pt.survey_of(self.before_rows, BS, eui=pt.EUI, disk=DISK)
        self.assertEqual(sp.verify_undo(self.survey_before, readers_for(self.before_rows), self.w, journal, undone), [])
        edges = json.loads(json.dumps(journal))
        edges['writes'] += [dict(offset=60 * MIB, length=512, sha256='0' * 64), dict(offset=119 * MIB - 512, length=512, sha256='0' * 64)]   # the free extent's two edges
        self.assertEqual(sp.verify_undo(self.survey_before, readers_for(self.before_rows), self.w, edges, undone), [])

    def test_verify_undo_reports_what_the_planner_finds_and_refuses_what_it_cannot_read(self):
        after = pt.survey_of(self.after_rows, BS, eui=pt.EUI, disk=DISK)
        found = sp.verify_undo(self.survey_before, readers_for(self.after_rows), self.w, {'writes': []}, after)      # no write to blame: the planner's findings alone
        self.assertTrue(found and not any('free space' in p for p in found), found)
        with self.assertRaisesRegex(sp.ProvisionError, 'refused'):
            sp.verify_undo(self.survey_before, readers_for(self.before_rows), self.w, {'writes': []}, {'disks': []})

    def test_verify_undo_refuses_an_entry_still_there_and_a_write_outside_the_freed_extent(self):
        journal = self.write()
        undone = pt.survey_of(self.before_rows, BS, eui=pt.EUI, disk=DISK)
        self.assertTrue(sp.verify_undo(self.survey_before, readers_for(self.after_rows), self.w, journal, pt.survey_of(self.after_rows, BS, eui=pt.EUI, disk=DISK)))
        bad = json.loads(json.dumps(journal))
        for offset in (14 * MIB, 60 * MIB - 512, 119 * MIB):                     # in Windows, its last sector, in the recovery partition
            bad['writes'].append(dict(offset=offset, length=512, sha256='0' * 64))
            with self.subTest(offset=offset):
                self.assertTrue(any('not inside the free space' in p for p in sp.verify_undo(self.survey_before, readers_for(self.before_rows), self.w, bad, undone)))
            bad['writes'].pop()

    # -- the command line
    def cli_files(self):
        survey = self.jsonfile('before.json', self.survey_before)
        plan = self.root / 'plan.json'
        rc, out, err = run_cli(['plan', '--survey', survey, '--out', plan, '--min-bytes', MIN_BYTES, '--isc-region', ISC_REGION])
        self.assertEqual((rc, err), (0, ''), out)
        afters = []
        for i, (label, doc) in enumerate(self.readers):
            afters += ['--after', self.jsonfile(f'r{i}-{label}.json', doc)]
        return survey, plan, afters

    def test_the_command_line_runs_the_whole_flow(self):
        survey, plan, afters = self.cli_files()
        pre, journal = self.root / 'pre.json', self.root / 'journal.json'
        rc, out, err = run_cli(['pre-image', '--image', self.img, '--plan', plan, '--before', survey, '--out', pre] + afters)
        self.assertEqual((rc, err), (0, ''), out)
        rc, out, err = run_cli(['write', '--image', self.img, '--plan', plan, '--pre-image', pre, '--before', survey, '--container', self.container,
                                '--isc', self.isc, '--confirm-image', self.img.name, '--stamp', STAMP, '--out', journal] + afters)
        self.assertEqual((rc, err), (0, ''), out)
        rc, out, err = run_cli(['post-check', '--image', self.img, '--plan', plan, '--pre-image', pre, '--journal', journal])
        self.assertEqual((rc, out.strip().splitlines()[-1], err), (0, 'PASS', ''))
        undone = self.jsonfile('undone.json', pt.survey_of(self.before_rows, BS, eui=pt.EUI, disk=DISK))
        afters = []
        for i, (label, doc) in enumerate(readers_for(self.before_rows)):
            afters += ['--after', self.jsonfile(f'u{i}-{label}.json', doc)]
        rc, out, err = run_cli(['verify-undo', '--before', survey, '--plan', plan, '--journal', journal, '--undone-survey', undone] + afters)
        self.assertEqual((rc, out.strip().splitlines()[-1], err), (0, 'PASS', ''))
        flip(self.img, 14 * MIB)                                                      # a byte of Windows changes after the write
        rc, out, err = run_cli(['post-check', '--image', self.img, '--plan', plan, '--pre-image', pre, '--journal', journal])
        self.assertEqual(rc, 1)
        self.assertIn('FAIL:', out)

    def test_the_command_line_refuses_with_status_2_and_writes_nothing(self):
        survey, plan, afters = self.cli_files()
        pre = self.jsonfile('pre.json', self.pre)
        self.assertEqual(run_cli(['plan', '--survey', survey, '--out', plan, '--min-bytes', MIN_BYTES, '--isc-region', ISC_REGION])[0], 2, 'an output is never overwritten')
        before = sha(self.img)
        gate = ['--before', survey] + afters
        base = ['write', '--image', self.img, '--plan', plan, '--pre-image', pre, '--container', self.container, '--isc', self.isc,
                '--confirm-image', self.img.name] + gate
        existing = self.file('exists.json', b'x')
        for name, argv in (('device image', ['write', '--image', '/dev/null', '--plan', plan, '--pre-image', pre, '--container', self.container, '--isc', self.isc,
                                              '--confirm-image', 'null', '--out', self.root / 'j1.json'] + gate),
                           ('volume image', ['write', '--image', '/Volumes/x/a.img', '--plan', plan, '--pre-image', pre, '--container', self.container,
                                             '--isc', self.isc, '--confirm-image', 'a.img', '--out', self.root / 'j2.json'] + gate),
                           ('no confirmation', ['write', '--image', self.img, '--plan', plan, '--pre-image', pre, '--container', self.container, '--isc', self.isc,
                                                '--out', self.root / 'j3.json'] + gate),
                           ('output on a volume', base + ['--out', '/Volumes/x/j.json']), ('output on a device', base + ['--out', '/dev/j.json']),
                           ('output that exists', base + ['--out', existing]), ('output in a missing directory', base + ['--out', self.root / 'nowhere' / 'j.json'])):
            with self.subTest(case=name):
                rc, out, err = run_cli(argv)
                self.assertEqual(rc, 2, (out, err))
                self.assertIn('REFUSED', err)
                self.assertEqual(sha(self.img), before, 'a refused write leaves the image as it was, even when the refusal is about the output')
        self.assertEqual(existing.read_bytes(), b'x')
        self.assertEqual(sorted(p.name for p in self.root.glob('j*.json')), [])
        out = self.root / 'pre-device.json'
        rc, _, err = run_cli(['pre-image', '--image', self.img, '--plan', plan, '--before', survey, '--out', out] + afters[:6] + ['--after', '/dev/null'])
        self.assertEqual(rc, 2)
        self.assertFalse(out.exists())
        rc, _, err = run_cli(['pre-image', '--image', '/dev/null', '--plan', plan, '--before', survey, '--out', out] + afters)
        self.assertEqual(rc, 2)

    def test_a_journal_that_cannot_be_saved_is_printed_and_the_exit_is_1(self):
        survey, plan, afters = self.cli_files()
        pre = self.jsonfile('pre.json', self.pre)
        argv = ['write', '--image', self.img, '--plan', plan, '--pre-image', pre, '--before', survey, '--container', self.container, '--isc', self.isc,
                '--confirm-image', self.img.name, '--stamp', STAMP, '--out', self.root / 'journal.json'] + afters
        with mock.patch.object(si, 'write_text_file', side_effect=OSError('no space left')):
            rc, out, err = run_cli(argv)
        self.assertEqual(rc, 1)
        self.assertIn('could not be saved', out)
        journal = json.loads(out.split('it follows:\n', 1)[1])
        self.assertEqual(sp.post_check(self.img, self.w, self.pre, journal), [])

    def test_the_command_line_refuses_a_survey_the_planner_refuses(self):
        out = self.root / 'plan2.json'
        no_eui = self.jsonfile('noeui.json', pt.survey_of(self.before_rows, BS, eui=None, disk=DISK))
        survey = self.jsonfile('before.json', self.survey_before)
        for name, argv in (('no NVMe identity', ['--survey', no_eui, '--min-bytes', MIN_BYTES, '--isc-region', ISC_REGION]),
                           ('an extent below the minimum', ['--survey', survey, '--min-bytes', 60 * MIB, '--isc-region', ISC_REGION]),
                           ('an ISC region the partition cannot hold', ['--survey', survey, '--min-bytes', MIN_BYTES, '--isc-region', 57 * MIB]),
                           ('one extent bound only', ['--survey', survey, '--extent-start', 60 * MIB // 512])):
            with self.subTest(case=name):
                rc, _, err = run_cli(['plan', '--out', out] + argv)
                self.assertEqual(rc, 2, err)
                self.assertFalse(out.exists())
        rc, _, _ = run_cli(['plan', '--out', out, '--survey', no_eui, '--min-bytes', MIN_BYTES, '--isc-region', ISC_REGION, '--no-eui'])
        self.assertEqual(rc, 0)

    def test_reader_labels_are_made_distinct_and_an_unreadable_reader_stays_a_reader_that_fails(self):
        one = self.file('r.json', b'{}')
        labels = [label for label, _ in sp._readers([one, one, self.root / 'nowhere' / 'r.json'])]
        self.assertEqual(labels, ['r', 'r-2', 'r-3'])
        self.assertIsInstance(sp._readers(['/dev/null'])[0][1], Exception)


class Authorisation(FlowBase):
    """The per-run authorisation record of the device mode: designed, validated, and never able to open anything."""
    now = datetime.datetime(2026, 11, 17, 3, 0, 0, tzinfo=datetime.timezone.utc)

    def record(self):
        r = sp.auth_template(self.w, self.pre, 'run-2026-11-17-a', self.now)
        r['acknowledged'] = {k: True for k in sp.ACKNOWLEDGEMENTS}
        return r

    def test_a_template_authorises_nothing_and_a_filled_record_is_valid_for_one_plan(self):
        template = sp.auth_template(self.w, self.pre, 'run-2026-11-17-a', self.now)
        self.assertTrue(all(v is False for v in template['acknowledged'].values()))
        self.assertTrue(any('acknowledgement' in p for p in sp.check_auth(template, self.w, self.pre, self.now)))
        self.assertEqual(sp.check_auth(self.record(), self.w, self.pre, self.now), [])
        self.assertEqual(set(template), set(sp.AUTH_KEYS))
        self.assertEqual(len(sp.ACKNOWLEDGEMENTS), 6)

    def test_each_invalid_record_is_refused_for_its_reason(self):
        def edited(f):
            r = self.record()
            f(r)
            return r
        later = self.now + datetime.timedelta(minutes=31)
        cases = [
            ('expired', self.record(), later, 'not valid now'),
            ('expired at the second', self.record(), self.now + datetime.timedelta(minutes=30), 'not valid now'),
            ('not yet', self.record(), self.now - datetime.timedelta(seconds=1), 'not valid now'),
            ('valid too long', edited(lambda r: r.__setitem__('expires_utc', '2026-11-17T03:30:01Z')), self.now, 'more than 30 minutes'),
            ('another plan', edited(lambda r: r.__setitem__('plan_sha256', '0' * 64)), self.now, 'another plan'),
            ('another pre-image', edited(lambda r: r.__setitem__('preimage_sha256', '0' * 64)), self.now, 'another pre-image'),
            ('another disk', edited(lambda r: r['disk'].__setitem__('gpt_guid', pt.G(5))), self.now, 'another disk'),
            ('another disk size', edited(lambda r: r['disk'].__setitem__('bytes', 1)), self.now, 'another disk'),
            ('another partition', edited(lambda r: r['partition'].__setitem__('offset', 62 * MIB)), self.now, 'another disk or another partition'),
            ('an acknowledgement false', edited(lambda r: r['acknowledged'].__setitem__('backup_verified', False)), self.now, 'acknowledgement'),
            ('an acknowledgement a string', edited(lambda r: r['acknowledged'].__setitem__('backup_verified', 'yes')), self.now, 'acknowledgement'),
            ('an acknowledgement missing', edited(lambda r: r['acknowledged'].pop('recovery_media_ready')), self.now, 'acknowledgement'),
            ('an unknown acknowledgement', edited(lambda r: r['acknowledged'].__setitem__('anything_else', True)), self.now, 'acknowledgement'),
            ('another tool', edited(lambda r: r.__setitem__('tool_source_sha256', '3' * 64)), self.now, 'another version'),
            ('an unknown key', edited(lambda r: r.__setitem__('extra', 1)), self.now, 'unknown or missing'),
            ('a missing key', edited(lambda r: r.pop('run_id')), self.now, 'unknown or missing'),
            ('a short run id', edited(lambda r: r.__setitem__('run_id', 'x')), self.now, 'run id'),
            ('a run id with a space', edited(lambda r: r.__setitem__('run_id', 'run 2026 11 17')), self.now, 'run id'),
            ('another format', edited(lambda r: r.__setitem__('format', 'snapmac-write-auth/2')), self.now, 'format'),
            ('another mode', edited(lambda r: r.__setitem__('mode', 'image')), self.now, 'format'),
            ('malformed times', edited(lambda r: r.__setitem__('issued_utc', 'yesterday')), self.now, 'times are not'),
        ]
        for name, record, when, fragment in cases:
            with self.subTest(case=name):
                self.assertTrue(any(fragment in p for p in sp.check_auth(record, self.w, self.pre, when)), sp.check_auth(record, self.w, self.pre, when))
        self.assertEqual(sp.check_auth('not a record', self.w, self.pre, self.now), ['not a record'])

    def test_the_device_mode_refuses_with_no_record_a_wrong_record_and_a_valid_one(self):
        wrong = self.record()
        wrong['plan_sha256'] = '0' * 64
        for name, record, fragment in (('no record', {}, 'no valid authorisation'), ('a wrong record', wrong, 'no valid authorisation'),
                                       ('a valid record', self.record(), 'designed and not enabled')):
            with self.subTest(case=name):
                with self.assertRaisesRegex(sp.DeviceModeRefused, fragment):
                    sp.open_device_for_write('/dev/rdisk9', record, self.w, self.pre, self.now)
        self.assertFalse(sp.DEVICE_MODE_ENABLED)

    def test_even_with_the_flag_flipped_nothing_opens_a_device_node(self):
        def tripwire(*a, **k):
            raise AssertionError(f'a file access was attempted: {a[:1]}')
        with mock.patch.object(sp, 'DEVICE_MODE_ENABLED', True), mock.patch('os.open', tripwire), mock.patch('os.stat', tripwire), \
                mock.patch('builtins.open', tripwire), mock.patch.object(si, 'Image', tripwire):
            with self.assertRaisesRegex(sp.DeviceModeRefused, 'not implemented'):
                sp.open_device_for_write('/dev/rdisk9', self.record(), self.w, self.pre, self.now)

    def test_the_command_line_refuses_the_device_mode(self):
        plan, pre, record = self.jsonfile('plan.json', self.w), self.jsonfile('pre.json', self.pre), self.root / 'auth.json'
        now = datetime.datetime.now(datetime.timezone.utc)
        filled = sp.auth_template(self.w, self.pre, 'run-2026-11-17-a', now)
        filled['acknowledged'] = {k: True for k in sp.ACKNOWLEDGEMENTS}
        record.write_text(json.dumps(filled))
        rc, out, err = run_cli(['auth-check', '--record', record, '--plan', plan, '--pre-image', pre])
        self.assertEqual((rc, out.strip().splitlines()[-1]), (0, 'PASS'))
        rc, out, err = run_cli(['auth-check', '--record', record, '--plan', plan, '--pre-image', pre, '--device', '/dev/rdisk9'])
        self.assertEqual(rc, 2)
        self.assertIn('designed and not enabled', err)
        record.write_text(json.dumps(sp.auth_template(self.w, self.pre, 'run-2026-11-17-a', now)))
        rc, out, err = run_cli(['auth-check', '--record', record, '--plan', plan, '--pre-image', pre, '--device', '/dev/rdisk9'])
        self.assertEqual(rc, 2)
        self.assertIn('no valid authorisation', err)
        rc, out, err = run_cli(['auth-check', '--record', record, '--plan', plan, '--pre-image', pre])
        self.assertEqual(rc, 1)


if __name__ == '__main__':
    sys.exit(0 if unittest.main(verbosity=1, exit=False).result.wasSuccessful() else 1)
