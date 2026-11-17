#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""SP11 offline storage provisioning, the image layer: path refusal, the bounded writer, the GPT reader, the window header, a source audit.

  python3 tools/test-snapmac-image-sp11.py

Everything runs on small image FILES made here under build/test-snapmac-image-sp11, with the planner test's GPT fixtures (decoded by q1n1's
tools/gpt_survey.py, the decoder the real survey uses). Nothing opens a disk or a device: the tests prove the tools refuse to. Every refusal has
a deliberately faulty case that must trip it, and every static check of the source audit has a seeded fault it must find. Evidence: EXPERIMENTAL
(synthetic images only); hardware NOT RUN.
"""
import ast
import contextlib
import datetime
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys
import tempfile
import types
import unittest
from unittest import mock
import warnings
import zlib

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import snapmac_image as si  # noqa: E402
import snapmac_window as sw  # noqa: E402


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pt = load('planner_sp11_test_for_image', HERE / 'test-snapmac-partition-plan-sp11.py')
SCRATCH = ROOT / 'build' / 'test-snapmac-image-sp11'
MIB = 1 << 20
DISK = 128 * MIB
GUID = 'f0f0f0f0-0000-4000-8000-000000000090'
MODULES = [HERE / 'snapmac_image.py', HERE / 'snapmac_window.py', HERE / 'snapmac_provision.py']
OWN = MODULES + [HERE / 'test-snapmac-image-sp11.py', HERE / 'test-snapmac-provision-sp11.py']


def pattern(seed, n):
    block = hashlib.shake_256(seed.encode()).digest(65536)
    return (block * (n // len(block) + 1))[:n]


def rows():
    return [pt.Row(0, pt.ESP, pt.T_ESP, 1 * MIB, 8 * MIB, 'EFI system partition'),
            pt.Row(1, pt.MSR, pt.T_MSR, 9 * MIB, 4 * MIB, 'Microsoft reserved partition'),
            pt.Row(2, pt.WINC, pt.T_BASIC, 13 * MIB, 47 * MIB, 'Basic data partition')]


def gpt_image(path, bs=512, entries=None, **kw):
    first, last = pt.windows(rows() if entries is None else entries, bs, DISK, pt.DISK_GUID, **kw)
    with open(path, 'wb') as f:
        f.truncate(DISK)
        f.write(first)
        f.seek(DISK - MIB)
        f.write(last)


HEADER_NAMES = ('sig', 'revision', 'size', 'crc', 'reserved', 'my', 'alt', 'first', 'last', 'guid', 'entries_lba', 'count', 'entry_size', 'array_crc')


def patch_header(path, lba, bs, **changes):
    """Rewrites the GPT header at `lba` with the named fields changed and its CRC right again, so that only those fields are wrong."""
    with open(path, 'r+b') as f:
        f.seek(lba * bs)
        fields = dict(zip(HEADER_NAMES, struct.unpack(si.HEADER, f.read(si.HEADER_BYTES))))
        fields.update(changes, crc=0)
        fields['crc'] = zlib.crc32(struct.pack(si.HEADER, *(fields[n] for n in HEADER_NAMES)))
        f.seek(lba * bs)
        f.write(struct.pack(si.HEADER, *(fields[n] for n in HEADER_NAMES)))


def patch_entry(path, bs, slot, edit):
    """Edits one entry in both copies of the array, and makes both array CRCs and both header CRCs right again."""
    for lba in (1, DISK // bs - 1):
        with open(path, 'r+b') as f:
            f.seek(lba * bs)
            fields = dict(zip(HEADER_NAMES, struct.unpack(si.HEADER, f.read(si.HEADER_BYTES))))
            f.seek(fields['entries_lba'] * bs)
            array = bytearray(f.read(fields['count'] * fields['entry_size']))
            entry = bytearray(array[slot * 128:(slot + 1) * 128])
            edit(entry)
            array[slot * 128:(slot + 1) * 128] = entry
            f.seek(fields['entries_lba'] * bs)
            f.write(array)
        patch_header(path, lba, bs, array_crc=zlib.crc32(bytes(array)))


class BytesPath(os.PathLike):
    def __fspath__(self):
        return b'/x'


class Scratch(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self._tmp = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def file(self, name, data=b''):
        path = self.root / name
        path.write_bytes(data)
        return path


class Paths(Scratch):
    def test_a_regular_file_is_accepted(self):
        path = self.file('a.img', b'x')
        self.assertEqual(si.refuse_path(path), os.path.realpath(path))

    def test_device_and_volume_paths_are_refused_however_they_are_spelled(self):
        for text in ('/dev/null', '/dev/zero', '/dev/disk0', '/dev/rdisk4', '/dev/../dev/null', '/DEV/null', '/dev/', '/dev',
                     '/Volumes/anything/x.img', '/volumes/anything/x.img', '/Volumes', '/VOLUMES/x', '/Volumes/../Volumes/x'):
            with self.subTest(path=text):
                with self.assertRaisesRegex(si.ImageError, 'device node or a volume'):
                    si.refuse_path(text)

    def test_a_relative_path_into_dev_and_a_symbolic_link_to_it_are_refused(self):
        with self.assertRaises(si.ImageError):
            si.refuse_path(os.path.relpath('/dev/null'))
        link = self.root / 'looks-like-an-image.img'
        link.symlink_to('/dev/null')
        with self.assertRaisesRegex(si.ImageError, 'device node or a volume'):
            si.refuse_path(link)
        with self.assertRaises(si.ImageError):
            si.Image(link)

    def test_a_symbolic_link_to_a_regular_file_is_followed_and_checked(self):
        target = self.file('real.img', b'x')
        link = self.root / 'link.img'
        link.symlink_to(target)
        self.assertEqual(si.refuse_path(link), os.path.realpath(target))

    def test_what_is_not_a_regular_file_is_refused(self):
        fifo = self.root / 'pipe'
        os.mkfifo(fifo)
        for path in (self.root, fifo, self.root / 'missing.img'):
            with self.subTest(path=path.name):
                with self.assertRaises(si.ImageError):
                    si.refuse_path(path)

    def test_odd_arguments_are_refused(self):
        for value in ('', 'a\x00b', None, 5, b'/tmp/x', BytesPath()):
            with self.subTest(value=value):
                with self.assertRaises(si.ImageError):
                    si.refuse_path(value)
                with self.assertRaises(si.ImageError):
                    si.check_output_path(value)

    def test_json_files_go_through_the_same_refusal_and_text_files_are_never_overwritten(self):
        for path in ('/dev/null', '/Volumes/x/plan.json'):
            with self.assertRaises(si.ImageError):
                si.read_json(path)
            with self.assertRaises(si.ImageError):
                si.write_text_file(path, 'x')
        with self.assertRaisesRegex(si.ImageError, 'not JSON'):
            si.read_json(self.file('bad.json', b'{'))
        with self.assertRaisesRegex(si.ImageError, 'larger than'):
            si.read_json(self.file('big.json', b'[]' + b' ' * 64), limit=8)
        out = self.root / 'out.json'
        si.write_text_file(out, 'one')
        with self.assertRaisesRegex(si.ImageError, 'never overwritten'):
            si.write_text_file(out, 'two')
        self.assertEqual(out.read_text(), 'one')

    def test_an_output_path_is_checked_before_any_work_and_for_every_way_it_can_fail(self):
        good = self.root / 'new.json'
        self.assertEqual(si.check_output_path(good), str(good))
        self.assertFalse(good.exists(), 'checking an output path creates nothing')
        (self.root / 'dev-link').symlink_to('/dev')
        (self.root / 'dangling').symlink_to(self.root / 'nowhere')
        for name, path in (('a device', '/dev/new.json'), ('a volume', '/Volumes/x/new.json'), ('a volume in lower case', '/volumes/x/new.json'),
                           ('a directory that is a symbolic link to /dev', self.root / 'dev-link' / 'new.json'),
                           ('a missing directory', self.root / 'missing' / 'new.json'), ('an existing file', self.file('exists.json', b'x')),
                           ('a directory', self.root), ('a dangling symbolic link', self.root / 'dangling'),
                           ('a file as the directory', self.root / 'exists.json' / 'new.json')):
            with self.subTest(case=name):
                with self.assertRaises(si.ImageError):
                    si.check_output_path(path)

    def test_a_read_is_bounded_and_a_read_only_image_cannot_be_written(self):
        path = self.file('a.img', b'abcdefgh')
        with si.Image(path) as image:
            self.assertEqual(image.read(2, 3), b'cde')
            self.assertEqual(image.read(3, 0), b'')
            for offset, length in ((-1, 1), (0, 9), (8, 1), (4, -1)):
                with self.assertRaises(si.ImageError):
                    image.read(offset, length)
            with self.assertRaisesRegex(si.WriteRefused, 'read-only'):
                image._write(0, b'x')
        self.assertEqual(path.read_bytes(), b'abcdefgh')

    def test_a_file_that_ends_inside_a_read_is_an_error_and_not_a_loop(self):
        with si.Image(self.file('a.img', b'abcdefgh')) as image, mock.patch.object(si.os, 'pread', side_effect=[b'', AssertionError('looped')]):
            with self.assertRaisesRegex(si.ImageError, 'ended inside a read'):
                image.read(0, 4)

    def test_close_releases_the_descriptor_and_a_second_close_is_harmless(self):
        image = si.Image(self.file('a.img', b'abcdefgh'))
        fd = image.fd
        image.close()
        self.assertIsNone(image.fd)
        with self.assertRaises(OSError):
            os.fstat(fd)
        image.close()

    def test_the_string_check_stands_without_the_identity_check_and_the_identity_check_without_the_string(self):
        with mock.patch.object(si.os.path, 'samefile', side_effect=OSError('no identity')):
            for text in ('/dev/null', '/dev/nothing/here', '/Volumes/x/y', '/volumes', '/dev'):
                with self.subTest(path=text):
                    self.assertIsNotNone(si._forbidden(text))
            self.assertIsNone(si._forbidden(str(self.root / 'a.img')))
        alias = str(self.root / 'alias')

        def same(a, b):
            if (a, b) == (alias, '/dev'):
                return True
            raise OSError('not the same')
        with mock.patch.object(si.os.path, 'samefile', side_effect=same):
            self.assertEqual(si._forbidden(alias + '/deeper/file'), '/dev')        # an ancestor two levels up that IS /dev by identity
            self.assertIsNone(si._forbidden(str(self.root / 'elsewhere' / 'file')))

    def test_a_json_file_of_exactly_the_limit_is_read_and_one_byte_more_is_not(self):
        data = b'[1, 2, 3]'
        self.assertEqual(si.read_json(self.file('x.json', data), limit=len(data)), [1, 2, 3])
        with self.assertRaisesRegex(si.ImageError, 'larger than'):
            si.read_json(self.file('y.json', data), limit=len(data) - 1)

    def test_a_file_that_is_not_the_one_that_was_checked_is_refused_at_the_open(self):
        path = self.file('a.img', b'abcdefgh')
        real = os.stat(path)
        for name, fake in (('another file', types.SimpleNamespace(st_mode=real.st_mode, st_dev=real.st_dev, st_ino=real.st_ino + 1, st_size=8)),
                           ('a character device', types.SimpleNamespace(st_mode=stat.S_IFCHR | 0o600, st_dev=real.st_dev, st_ino=real.st_ino, st_size=0))):
            with self.subTest(case=name), mock.patch.object(si.os, 'fstat', return_value=fake):
                with self.assertRaisesRegex(si.ImageError, 'changed between the check and the open'):
                    si.Image(path)

    def test_the_exclusive_create_refuses_an_output_that_appeared_after_the_check(self):
        """check_output_path is switched off, as if the file or the link had been made after it ran: only the open's own exclusive create is left."""
        other = self.file('other.json', b'keep')
        existing = self.file('existing.json', b'keep')
        (self.root / 'to-file').symlink_to(other)
        (self.root / 'to-nothing').symlink_to(self.root / 'nowhere.json')
        with mock.patch.object(si, 'check_output_path', side_effect=os.fspath):
            for name, target in (('an existing file', existing), ('a link to a file', self.root / 'to-file'), ('a dangling link', self.root / 'to-nothing')):
                with self.subTest(case=name):
                    with self.assertRaisesRegex(si.ImageError, 'never overwritten'):
                        si.write_text_file(target, 'replaced')
        self.assertEqual((existing.read_bytes(), other.read_bytes()), (b'keep', b'keep'))
        self.assertFalse((self.root / 'nowhere.json').exists(), 'a dangling link is not followed to create its target')

    def test_a_symbolic_link_put_in_place_of_the_checked_file_is_not_followed_at_the_open(self):
        real_refuse = si.refuse_path
        for writable in (False, True):
            checked, other = self.file(f'checked-{writable}.img', b'abcdefgh'), self.file(f'other-{writable}.img', b'ABCDEFGH')

            def swap(path, checked=checked, other=other):
                resolved = real_refuse(path)
                os.unlink(resolved)
                os.symlink(other, resolved)                           # after the check and before the open
                return resolved
            with self.subTest(writable=writable), mock.patch.object(si, 'refuse_path', side_effect=swap):
                with self.assertRaisesRegex(si.ImageError, 'cannot open'):
                    si.Image(checked, writable=writable)
            self.assertEqual(other.read_bytes(), b'ABCDEFGH')


class Writer(Scratch):
    """The bounded writer on a 4 MiB image whose partition is [1 MiB, 3 MiB) between two protected extents."""

    def setUp(self):
        super().setUp()
        self.path = self.file('w.img', pattern('image', 4 * MIB))
        self.before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.image = si.Image(self.path, writable=True)
        self.addCleanup(self.image.close)
        self.protected = [(0, MIB), (3 * MIB, MIB)]
        self.writer = si.Writer(self.image, MIB, 3 * MIB, self.protected)

    def unchanged(self):
        return hashlib.sha256(self.path.read_bytes()).hexdigest() == self.before

    def test_a_write_inside_the_partition_is_made_and_journaled(self):
        self.writer.write(MIB, bytes(range(256)) * 2)
        self.writer.write(3 * MIB - 512, b'z' * 512)                      # the last sector of the partition
        self.assertEqual(self.image.read(MIB, 512), bytes(range(256)) * 2)
        self.assertEqual([e['offset'] for e in self.writer.journal], [MIB, 3 * MIB - 512])
        self.assertEqual(self.writer.journal[1]['sha256'], hashlib.sha256(b'z' * 512).hexdigest())

    def test_a_write_one_sector_past_the_end_is_refused_and_nothing_changes(self):
        for offset, length in ((3 * MIB, 512), (3 * MIB - 512, 1024), (MIB - 512, 512), (MIB - 512, 1024), (0, 512), (4 * MIB - 512, 512)):
            with self.subTest(offset=offset, length=length):
                with self.assertRaisesRegex(si.WriteRefused, 'leaves the partition'):
                    self.writer.write(offset, b'x' * length)
        self.assertTrue(self.unchanged())
        self.assertEqual(self.writer.journal, [])

    def test_a_write_that_is_empty_unaligned_or_too_big_is_refused(self):
        for offset, data in ((MIB, b''), (MIB + 1, b'x' * 512), (MIB, b'x' * 511), (MIB, b'x' * 513), (MIB, bytes(si.CHUNK + 512))):
            with self.subTest(offset=offset, length=len(data)):
                with self.assertRaises(si.WriteRefused):
                    self.writer.write(offset, data)
        self.assertTrue(self.unchanged())

    def test_an_extent_that_overlaps_a_protected_extent_cannot_be_opened_for_writing(self):
        for start, end in ((MIB - 512, 3 * MIB), (MIB, 3 * MIB + 512), (0, 4 * MIB), (512, 2 * MIB)):
            with self.subTest(extent=(start, end)):
                with self.assertRaisesRegex(si.WriteRefused, 'protected extent'):
                    si.Writer(self.image, start, end, self.protected)

    def test_each_write_is_checked_against_the_protected_extents_too(self):
        self.writer.protected.append((2 * MIB, 512))                       # a bug that got past the constructor must still be stopped
        for offset in (2 * MIB - 512, 2 * MIB):
            with self.subTest(offset=offset):
                with self.assertRaisesRegex(si.WriteRefused, 'touches the protected extent'):
                    self.writer.write(offset, b'x' * 1024 if offset < 2 * MIB else b'x' * 512)
        self.writer.write(2 * MIB + 512, b'x' * 512)
        self.assertEqual(len(self.writer.journal), 1)

    def test_the_extent_must_be_whole_sectors_inside_the_image(self):
        for start, end in ((MIB + 1, 3 * MIB), (MIB, 3 * MIB + 1), (MIB, MIB), (3 * MIB, MIB), (MIB, 9 * MIB), (-512, MIB)):
            with self.subTest(extent=(start, end)):
                with self.assertRaises(si.WriteRefused):
                    si.Writer(self.image, start, end, [])

    def test_a_read_only_image_cannot_be_given_a_writer(self):
        with si.Image(self.path) as image:
            with self.assertRaisesRegex(si.WriteRefused, 'read-only'):
                si.Writer(image, MIB, 3 * MIB, [])

    def test_a_write_over_the_chunk_is_refused_even_when_it_would_fit_the_partition(self):
        path = self.file('big.img', b'')
        with open(path, 'r+b') as f:
            f.truncate(24 * MIB)                                              # sparse: only what is written costs space
        with si.Image(path, writable=True) as image:
            writer = si.Writer(image, MIB, 23 * MIB, [(0, MIB), (23 * MIB, MIB)])
            with self.assertRaisesRegex(si.WriteRefused, 'at most'):
                writer.write(MIB, bytes(si.CHUNK + 512))
            writer.write(MIB, bytes(si.CHUNK))                                # exactly one chunk is fine
            self.assertEqual(len(writer.journal), 1)

    def test_sync_flushes_a_writable_image_and_nothing_else(self):
        with mock.patch.object(si.os, 'fsync') as fsync:
            self.image.sync()
            self.assertEqual(fsync.call_count, 1)
            with si.Image(self.path) as read_only:
                read_only.sync()
            self.assertEqual(fsync.call_count, 1)

    def test_the_chunk_is_the_a16_packers(self):
        self.assertEqual(si.CHUNK, load('a16_window_payload', HERE / 'snapmac-window-payload.py').CHUNK)

    def test_digests_cover_what_they_say(self):
        data = self.path.read_bytes()
        self.assertEqual(self.image.digest(0, self.image.size), hashlib.sha256(data).hexdigest())
        self.assertEqual(self.image.digest(5 * 512, 0), hashlib.sha256(b'').hexdigest())
        outside = hashlib.sha256(data[:MIB] + data[3 * MIB:]).hexdigest()
        self.assertEqual(self.image.digest_outside(MIB, 3 * MIB), outside)
        self.writer.write(MIB, b'q' * 512)
        self.assertNotEqual(self.image.digest(0, self.image.size), hashlib.sha256(data).hexdigest())
        self.assertEqual(self.image.digest_outside(MIB, 3 * MIB), outside)


class Gpt(Scratch):
    """The GPT reader on 128 MiB images: both block sizes, and every kind of damage it must refuse."""

    def parsed(self, path):
        with si.Image(path) as image:
            return si.parse_gpt(image)

    def damage(self, bs, offset):
        path = self.root / 'g.img'
        gpt_image(path, bs)
        with open(path, 'r+b') as f:
            f.seek(offset)
            byte = f.read(1)
            f.seek(offset)
            f.write(bytes([byte[0] ^ 0x5a]))
        return path

    def test_both_block_sizes_are_read(self):
        for bs in (512, 4096):
            path = self.root / f'g{bs}.img'
            gpt_image(path, bs)
            with self.subTest(block=bs):
                g = self.parsed(path)
                self.assertEqual((g['block'], g['disk_guid']), (bs, pt.DISK_GUID))
                self.assertEqual((g['first_usable'], g['last_usable']), pt.usable(bs, DISK))
                self.assertEqual([(e['index'], e['type'], e['guid'], e['first_lba'], e['last_lba'], e['attributes'], e['name']) for e in g['entries']],
                                 [(r.slot, r.type, r.guid, r.offset // bs, (r.offset + r.size) // bs - 1, pt.attrs_of(r), r.name) for r in rows()])

    def test_each_kind_of_damage_is_refused_for_its_own_reason(self):
        array_lba = lambda bs: DISK // bs - 1 - 16384 // bs                  # noqa: E731  (where the fixture puts the backup array)
        for bs in (512, 4096):
            for name, offset in (('primary header', bs + 40), ('primary array', 2 * bs + 5), ('backup header', DISK - bs + 40),
                                 ('backup array', array_lba(bs) * bs + 5)):
                with self.subTest(block=bs, damage=name):
                    with self.assertRaisesRegex(si.ImageError, 'fails its CRC'):
                        self.parsed(self.damage(bs, offset))

    def test_a_header_with_a_right_crc_and_one_wrong_field_is_refused_for_that_field(self):
        for bs in (512, 4096):
            for name, changes, message in (
                    ('signature', dict(sig=b'EFI PARX'), 'no GPT header'), ('revision', dict(revision=0x20000), 'no GPT header'),
                    ('size', dict(size=96), 'no GPT header'), ('own LBA', dict(my=2), 'says it is at'),
                    ('entry size', dict(entry_size=64), 'not 128-byte entries'), ('no entries', dict(count=0), 'not 128-byte entries'),
                    ('too many entries', dict(count=129), 'not 128-byte entries'), ('array CRC', dict(array_crc=1), 'fails its CRC'),
                    ('pointer to the backup', dict(alt=DISK // bs - 2), 'does not point at the last block')):
                path = self.root / 'f.img'
                gpt_image(path, bs)
                patch_header(path, 1, bs, **changes)
                with self.subTest(block=bs, field=name):
                    with self.assertRaisesRegex(si.ImageError, message):
                        self.parsed(path)

    def test_a_backup_that_disagrees_with_the_primary_in_any_one_field_is_refused_though_both_are_valid(self):
        path = self.root / 'd.img'
        gpt_image(path, backup_rows=rows()[:2])
        with self.assertRaisesRegex(si.ImageError, 'disagree'):
            self.parsed(path)
        for bs in (512, 4096):
            for name, changes in (('disk GUID', dict(guid=bytes(16))), ('first usable', dict(first=100)), ('last usable', dict(last=DISK // bs - 100)),
                                  ('pointer to the primary', dict(alt=2))):
                gpt_image(other := self.root / f'd{bs}.img', bs)
                patch_header(other, DISK // bs - 1, bs, **changes)
                with self.subTest(block=bs, field=name):
                    with self.assertRaisesRegex(si.ImageError, 'disagree'):
                        self.parsed(other)

    def test_a_disk_that_is_not_the_size_its_header_says_and_a_disk_without_a_gpt_are_refused(self):
        path = self.root / 'short.img'
        gpt_image(path)
        with open(path, 'r+b') as f:
            f.truncate(DISK - 512)
        with self.assertRaisesRegex(si.ImageError, 'does not point at the last block'):
            self.parsed(path)
        for data in (bytes(DISK // 64), b'x' * 1024, b''):
            with self.assertRaises(si.ImageError):
                self.parsed(self.file('none.img', data))

    def test_entries_outside_the_usable_range_backwards_or_with_a_bad_name_are_refused(self):
        for bs in (512, 4096):
            first_usable, last_usable = pt.usable(bs, DISK)
            for name, edit, message in (
                    ('below the range', lambda e: e.__setitem__(slice(32, 40), struct.pack('<Q', first_usable - 1)), 'usable range'),
                    ('above the range', lambda e: e.__setitem__(slice(40, 48), struct.pack('<Q', last_usable + 1)), 'usable range'),
                    ('backwards', lambda e: e.__setitem__(slice(32, 48), struct.pack('<QQ', 100, 99)), 'usable range'),
                    ('a name that is not UTF-16', lambda e: e.__setitem__(slice(56, 58), b'\x00\xd8'), 'not UTF-16')):
                path = self.root / 'e.img'
                gpt_image(path, bs)
                patch_entry(path, bs, 0, edit)
                with self.subTest(block=bs, entry=name):
                    with self.assertRaisesRegex(si.ImageError, message):
                        self.parsed(path)

    def test_entries_at_the_edges_of_the_usable_range_and_of_one_block_are_accepted(self):
        for bs in (512, 4096):
            first_usable, last_usable = pt.usable(bs, DISK)
            for first, last in ((first_usable, last_usable), (first_usable, first_usable), (last_usable, last_usable)):
                path = self.root / 'e.img'
                gpt_image(path, bs)
                patch_entry(path, bs, 0, lambda e: e.__setitem__(slice(32, 48), struct.pack('<QQ', first, last)))
                with self.subTest(block=bs, entry=(first, last)):
                    got = self.parsed(path)['entries'][0]
                    self.assertEqual((got['first_lba'], got['last_lba']), (first, last))


class Header(Scratch):
    def test_the_header_is_the_a16_scripts_byte_for_byte_on_the_a16s_numbers(self):
        a16 = load('a16_window_header', HERE / 'snapmac-window-header.py')
        manifest = dict(container=dict(bytes=100 * MIB, sha256=hashlib.sha256(b'container').hexdigest()))
        payload = self.root / 'payload'
        payload.mkdir()
        (payload / 'manifest.json').write_text(json.dumps(manifest))
        isc = self.file('isc.img', pattern('isc', 8192))
        fixed = type('Fixed', (datetime.datetime,), {'now': classmethod(lambda cls, tz=None: datetime.datetime(2026, 11, 17, 3, 4, 5, tzinfo=tz))})
        with warnings.catch_warnings(), mock.patch.object(a16, 'datetime', types.SimpleNamespace(datetime=fixed, timezone=datetime.timezone)), \
                contextlib.redirect_stdout(io.StringIO()):
            warnings.simplefilter('ignore', ResourceWarning)                 # the A16 script leaves a file open
            a16.main(['--payload', str(payload), '--isc', str(isc), '--partition-guid', GUID])
        lay = sw.layout(a16.PART_OFFSET, a16.PART_SIZE)
        ours = sw.build_header(lay, GUID, 8192, hashlib.sha256(isc.read_bytes()).hexdigest(), 100 * MIB, manifest['container']['sha256'],
                               hashlib.sha256((payload / 'manifest.json').read_bytes()).hexdigest(), '2026-11-17T03:04:05Z')
        self.assertEqual(ours, (payload / 'header.bin').read_bytes())
        window = json.loads((payload / 'window.json').read_text())['layout']
        self.assertEqual((window['header_offset'], window['isc_offset'], window['isc_region'], window['container_offset'], window['container_region']),
                         (lay['header_offset'], lay['isc_offset'], lay['isc_region'], lay['container_offset'], lay['container_region']))

    def lay(self):
        return sw.layout(61 * MIB, 57 * MIB, 4 * MIB)

    def header(self, **over):
        args = dict(partition_guid=GUID, isc_bytes=2 * MIB, isc_sha256='11' * 32, container_bytes=20 * MIB, container_sha256='22' * 32,
                    manifest_sha256='33' * 32, stamp='2026-11-17T03:04:05Z')
        args.update(over)
        return sw.build_header(self.lay(), **args)

    def test_a_header_parses_back_and_its_geometry_is_the_plans(self):
        h = sw.parse_header(self.header())
        self.assertEqual((h['guid'], h['first_lba'], h['last_lba'], h['virtual_block'], h['state']), (GUID, 61 * MIB // 512, 118 * MIB // 512 - 1, 4096, 1))
        self.assertEqual((h['isc_offset'], h['isc_region'], h['container_offset'], h['container_region']), (MIB, 4 * MIB, 5 * MIB, 57 * MIB - 6 * MIB))
        self.assertEqual((h['isc_sha256'], h['container_sha256'], h['manifest_sha256'], h['stamp']), ('11' * 32, '22' * 32, '33' * 32, '2026-11-17T03:04:05Z'))

    def test_a_damaged_header_is_refused(self):
        good = bytearray(self.header())
        for name, mutate in (('crc', lambda b: b.__setitem__(100, b[100] ^ 1)), ('magic', lambda b: b.__setitem__(0, 0x58)), ('truncated', lambda b: b.pop())):
            bad = bytearray(good)
            mutate(bad)
            with self.subTest(damage=name):
                with self.assertRaises(sw.WindowError):
                    sw.parse_header(bytes(bad))

    def test_a_header_with_a_right_crc_and_one_wrong_field_is_not_a_header(self):
        good = bytearray(self.header())
        for name, offset, value in (('magic', 0, b'SNAPWIN2'), ('version', 8, struct.pack('<I', 2)), ('header size', 12, struct.pack('<I', 8192))):
            bad = bytearray(good)
            bad[offset:offset + len(value)] = value
            bad[-4:] = struct.pack('<I', zlib.crc32(bytes(bad[:-4])))
            with self.subTest(field=name):
                with self.assertRaisesRegex(sw.WindowError, 'not a window header'):
                    sw.parse_header(bytes(bad))
        for name, data in (('one byte short', good[:-1]), ('one byte long', good + b'\0'), ('empty', b'')):
            with self.subTest(length=name):
                with self.assertRaisesRegex(sw.WindowError, '4096 bytes'):
                    sw.parse_header(bytes(data))
        wrong = bytearray(good)
        wrong[-1] ^= 1
        with self.assertRaisesRegex(sw.WindowError, 'fails its CRC'):
            sw.parse_header(bytes(wrong))

    def test_the_limits_of_a_window_are_exact(self):
        self.header(stamp='x' * 32)                                            # 32 characters fit; 33 do not (below)
        with self.assertRaises(sw.WindowError):
            self.header(stamp='x' * 33)
        self.assertEqual(sw.layout(61 * MIB, 6 * MIB + 16384, 4 * MIB)['container_region'], 16384)
        with self.assertRaisesRegex(sw.WindowError, 'too small'):
            sw.layout(61 * MIB, 6 * MIB, 4 * MIB)                              # exactly no room for a container

    def test_a_window_that_does_not_fit_is_refused(self):
        for over in (dict(isc_bytes=4 * MIB + 4096), dict(isc_bytes=0), dict(isc_bytes=4097), dict(container_bytes=0),
                     dict(container_bytes=self.lay()['container_region'] + 1), dict(stamp='x' * 33)):
            with self.subTest(over=over):
                with self.assertRaises(sw.WindowError):
                    self.header(**over)
        self.header(container_bytes=self.lay()['container_region'])                      # exactly the region fits
        for args in ((61 * MIB, 3 * MIB, 4 * MIB), (0, 57 * MIB, 4 * MIB), (61 * MIB + 1, 57 * MIB, 4 * MIB), (61 * MIB, 57 * MIB, 0)):
            with self.subTest(layout=args):
                with self.assertRaises(sw.WindowError):
                    sw.layout(*args)


# ---- What no run shows: the tools open regular files and nothing else ------------------------------------------------------------------

BANNED_IMPORTS = frozenset('subprocess socket urllib http ctypes fcntl shutil pty termios ssl requests asyncio multiprocessing select mmap '
                           'tempfile glob'.split())
OS_ALLOWED = frozenset('os.fspath os.path.realpath os.path.abspath os.path.dirname os.path.basename os.path.isdir os.path.lexists '
                       'os.path.samefile os.path.getsize os.stat os.fstat os.open os.close os.pread os.pwrite os.fsync'.split())
PATH_WRITERS = frozenset('write_bytes write_text touch unlink mkdir rmdir rename symlink_to hardlink_to chmod truncate'.split())
WORDS = ('diskutil', 'hdiutil', 'dd if', '/dev/disk', '/dev/rdisk', 'rdisk', 'authopen', 'O_DIRECT', 'F_NOCACHE', 'IOKit', 'DADisk')
DEVICE_FUNCTION_CALLS = {'check_auth', 'DeviceModeRefused', "'; '.join"}


def without_docstrings(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body and isinstance(node.body[0], ast.Expr) \
                and isinstance(getattr(node.body[0], 'value', None), ast.Constant) and isinstance(node.body[0].value.value, str):
            node.body = node.body[1:] or [ast.Pass()]
    return tree


def audit(name, text):
    """Every way this source breaks the rules of the offline tools; [] is clean. Comments and docstrings are not code and are not read."""
    own = name == 'snapmac_image.py'
    tree = ast.parse(text)
    found, calls = [], []
    imported = {a.name.split('.')[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module.split('.')[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    if imported & BANNED_IMPORTS:
        found.append(f'imports {sorted(imported & BANNED_IMPORTS)}')
    calls = [ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)]
    for call in sorted(set(calls)):
        if call.startswith('os.') and call not in OS_ALLOWED:
            found.append(f'calls {call}, which is not on the list of os calls')
        if call in ('eval', 'exec', '__import__', 'compile'):
            found.append(f'calls {call}')
        if '.' in call and call.rsplit('.', 1)[-1] in PATH_WRITERS:
            found.append(f'calls {call}, a file change outside the image layer')
    for call, allowed in (('os.open', 1 if own else 0), ('open', 2 if own else 0), ('os.pwrite', 1 if own else 0)):
        if calls.count(call) != allowed:
            found.append(f'{calls.count(call)} calls of {call}, {allowed} allowed')
    code = without_docstrings(ast.parse(text))
    unparsed = ast.unparse(code)
    for word in WORDS:
        if word in unparsed:
            found.append(f'names {word}')
    paths = sorted(n.value for n in ast.walk(code) if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.startswith('/') and len(n.value) > 1)
    if paths != (['/Volumes', '/dev'] if own else []):
        found.append(f'path constants {paths}')
    functions = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    for fn in functions:
        # In evaluation order: an inner call ends before the call that takes its result.
        inner = [ast.unparse(n.func) for n in sorted((n for n in ast.walk(fn) if isinstance(n, ast.Call)), key=lambda n: (n.end_lineno, n.end_col_offset))]
        if 'os.open' in inner and (fn.name != '__init__' or 'refuse_path' not in inner or inner.index('refuse_path') > inner.index('os.open')):
            found.append(f'{fn.name} opens a file without refusing the path first')
        if fn.name == 'open_device_for_write' and set(inner) != DEVICE_FUNCTION_CALLS:
            found.append(f'open_device_for_write calls {sorted(set(inner) ^ DEVICE_FUNCTION_CALLS)}')
    callers = [fn.name for fn in functions if any(isinstance(n, ast.Call) and ast.unparse(n.func).endswith('._write') for n in ast.walk(fn))]
    if callers != (['write'] if own else []):
        found.append(f'_write is called by {callers}')
    if name == 'snapmac_provision.py' and not re.search(r'^DEVICE_MODE_ENABLED = False\b', text, re.M):
        found.append('DEVICE_MODE_ENABLED is not False')
    return found


MACHINE = re.compile('|'.join(['/Us' + 'ers/', '/ho' + 'me/']) + r'|\.loc' + r'al\b|\bsae' + r'ed\b|host' + r'name|\b[0-9a-f]{2}(?::[0-9a-f]{2}){5}\b', re.I)


def machine_lines(text):
    return [n for n, line in enumerate(text.splitlines(), 1) if MACHINE.search(line)]


def seeded(name, old, new):
    """The source of `name` with its text `old` replaced once by `new`; the needle must be there."""
    text = (HERE / name).read_text(encoding='utf-8')
    assert text.count(old) == 1, (name, old)
    return name, text.replace(old, new, 1)


def appended(name, code):
    return name, (HERE / name).read_text(encoding='utf-8') + '\n\n' + code


class Sources(unittest.TestCase):
    texts = {p.name: p.read_text(encoding='utf-8') for p in MODULES}

    def test_the_real_sources_pass_the_audit(self):
        for name, text in self.texts.items():
            self.assertEqual(audit(name, text), [], name)

    def test_each_seeded_fault_is_found(self):
        image, provision, window = 'snapmac_image.py', 'snapmac_provision.py', 'snapmac_window.py'
        faults = [
            ('a shell import', seeded(provision, 'import argparse\n', 'import argparse\nimport subprocess\n'), 'imports'),
            ('a network import', seeded(image, 'import hashlib\n', 'import hashlib\nfrom urllib import request\n'), 'imports'),
            ('a raw device import', seeded(window, 'import struct\n', 'import struct\nimport fcntl\n'), 'imports'),
            ('a second os.open', appended(image, 'def f(p):\n    return os.open(p, os.O_RDWR)\n'), 'calls of os.open'),
            ('an os.open in the provision tool', appended(provision, 'def f(p):\n    return os.open(p, os.O_RDWR)\n'), 'calls of os.open'),
            ('a builtin open in the provision tool', appended(provision, 'def f(p):\n    return open(p, "wb")\n'), 'calls of open'),
            ('a third builtin open in the image layer', appended(image, 'def f(p):\n    return open(p, "wb")\n'), 'calls of open'),
            ('a second pwrite', appended(image, 'def f(fd):\n    os.pwrite(fd, b"x", 0)\n'), 'calls of os.pwrite'),
            ('os.system', appended(provision, 'def f():\n    os.system("x")\n'), 'os.system'),
            ('an os call that is not on the list', appended(image, 'def f():\n    os.unlink("x")\n'), 'os.unlink'),
            ('a path method that writes', appended(provision, 'def f(p):\n    Path(p).write_bytes(b"")\n'), 'write_bytes'),
            ('eval', appended(provision, 'def f(s):\n    return eval(s)\n'), 'eval'),
            ('a device path constant', appended(provision, 'DEVICE = "/dev/rdisk2"\n'), 'path constants'),
            ('a third forbidden root', seeded(image, "FORBIDDEN_ROOTS = ('/dev', '/Volumes')", "FORBIDDEN_ROOTS = ('/dev', '/Volumes', '/private')"), 'path constants'),
            ('a disk tool named in code', appended(provision, 'TOOL = "diskutil"\n'), 'names diskutil'),
            ('a disk image tool named in code', appended(window, 'TOOL = "hdiutil"\n'), 'names hdiutil'),
            ('the device function opens a file', seeded(provision, 'problems = check_auth(record, w, pre, now)\n    if problems:',
                                                         'problems = check_auth(record, w, pre, now)\n    open(path)\n    if problems:'), 'open_device_for_write'),
            ('the device function calls the image layer', seeded(provision, "    if not DEVICE_MODE_ENABLED:\n        raise DeviceModeRefused('the authorised device mode is designed",
                                                                 "    si.Image(path, True)\n    if not DEVICE_MODE_ENABLED:\n        raise DeviceModeRefused('the authorised device mode is designed"), 'open_device_for_write'),
            ('the device mode switched on', seeded(provision, 'DEVICE_MODE_ENABLED = False', 'DEVICE_MODE_ENABLED = True'), 'DEVICE_MODE_ENABLED'),
            ('the raw write called outside the bounded writer', appended(provision, 'def f(image):\n    image._write(0, b"x")\n'), '_write is called by'),
            ('the raw write called from another method of the image layer', seeded(image, '    def sync(self):', '    def poke(self):\n        self._write(0, b"x")\n\n    def sync(self):'), '_write is called by'),
            ('the open before the refusal', seeded(image, 'self.path = refuse_path(path)\n        before = os.stat(self.path)',
                                                   'self.path = os.fspath(path)\n        before = os.stat(self.path)'), 'without refusing the path first'),
        ]
        for label, (name, text), fragment in faults:
            with self.subTest(fault=label):
                self.assertTrue(any(fragment in f for f in audit(name, text)), (audit(name, text), fragment))

    def test_comments_and_docstrings_are_not_code(self):
        for name, text in self.texts.items():
            quiet = text + '\n# diskutil, /dev/rdisk2, import subprocess, os.system("x")\n\ndef f():\n    """open(p); /dev/rdisk2; hdiutil"""\n'
            self.assertEqual(audit(name, quiet), [], name)

    def test_the_files_are_within_the_size_limits_carry_the_licence_and_name_no_machine(self):
        for path in OWN:
            with self.subTest(file=path.name):
                text = path.read_text(encoding='utf-8')
                self.assertLess(len(text.splitlines()), 1500)
                self.assertLess(len(text.encode('utf-8')), 50 * 1024)
                self.assertIn('SPDX-License-Identifier: MIT', text[:200])
                text.encode('ascii')
                self.assertEqual(machine_lines(text), [], path.name)

    def test_the_machine_scan_finds_what_it_is_for(self):
        for line in ('x = "/Us' + 'ers/someone/a"', 'x = "/ho' + 'me/someone"', 'host' + 'name = 1', 'a.loc' + 'al', ':'.join(['00', '11', '22', '33', '44', '55']),
                     'sae' + 'ed'):
            self.assertEqual(machine_lines(line), [1], line)
        self.assertEqual(machine_lines('x = "build/test"'), [])

    def test_the_planner_is_reused_and_not_grown(self):
        planner = (HERE / 'snapmac-partition-plan.py').read_text(encoding='utf-8')
        for name in ('pwrite', 'os.open', 'snapmac_image', 'write_window', 'preimage', 'journal', 'header.bin'):
            self.assertNotIn(name, planner, 'the planner must not grow a writer')
        self.assertIn('spec_from_file_location', self.texts['snapmac_provision.py'])


if __name__ == '__main__':
    sys.exit(0 if unittest.main(verbosity=1, exit=False).result.wasSuccessful() else 1)
