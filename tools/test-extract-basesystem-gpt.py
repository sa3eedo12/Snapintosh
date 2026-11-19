#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Host tests for the GUID-partition slicing of tools/extract-basesystem.py (apfs_partition, slice_file, convert_udif on a GPT disk).

    python3 tools/test-extract-basesystem-gpt.py [-v]

Apple's BaseSystem image is a GPT disk (MBR, GPT, one Apple_APFS partition, backup GPT), and the profile pin is the bare APFS
container inside it. Everything here is synthetic: a made-up container of 512 blocks inside a hand-built GPT. The pure rules (both
GPT CRC-32s, exactly one APFS partition, the partition inside the usable range and the file) run everywhere; the hdiutil round trip
says SKIP where hdiutil is absent. Scratch files live under build/test-extract-basesystem-gpt (git-ignored) and are removed.
"""
import hashlib
import importlib.util
import shutil
import struct
import subprocess
import unittest
import uuid
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('extract_basesystem_gpt', ROOT / 'tools' / 'extract-basesystem.py')
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

BUILD = ROOT / 'build' / 'test-extract-basesystem-gpt'
SECTOR, GIB = 512, 1 << 30
APFS, OTHER = tool.APFS_PARTITION_TYPE, uuid.UUID('C12A7328-F81F-11D2-BA4B-00A0C93EC93B').bytes_le       # the EFI system partition type
needs_hdiutil = unittest.skipUnless(shutil.which('hdiutil'), 'needs the macOS hdiutil')


def container(blocks=512):
    raw = bytearray(hashlib.shake_256(b'gpt-raw').digest(blocks * 4096))
    raw[32:36] = b'NXSB'
    struct.pack_into('<I', raw, 36, 4096)
    struct.pack_into('<Q', raw, 40, blocks)
    return bytes(raw)


def gpt_disk(parts, count=128):
    """MBR, GPT header and entries (CRC-32s right), the partitions' data, backup entries and header. parts = [(type, data)]."""
    first, entries = 34, bytearray(count * 128)
    body, cursor = b'', first
    for i, (kind, data) in enumerate(parts):
        sectors = len(data) // SECTOR
        struct.pack_into('<16s16sQQQ', entries, i * 128, kind, bytes([i + 1]) * 16, cursor, cursor + sectors - 1, 0)
        body, cursor = body + data, cursor + sectors
    total = cursor + 33
    header = bytearray(92)
    header[:8] = b'EFI PART'
    struct.pack_into('<IIIII', header, 8, 0x10000, 92, 0, 0, 0)
    struct.pack_into('<QQQQ', header, 24, 1, total - 1, first, cursor - 1)
    header[56:72] = bytes(range(16))
    struct.pack_into('<QIII', header, 72, 2, count, 128, zlib.crc32(bytes(entries)))
    struct.pack_into('<I', header, 16, zlib.crc32(bytes(header)))
    mbr = bytearray(512)                                    # a protective MBR: one 0xEE partition from LBA 1, as the real image has
    mbr[446:462] = struct.pack('<B3sB3sII', 0, bytes([0, 2, 0]), 0xEE, bytes([255, 255, 255]), 1, min(total - 1, 0xFFFFFFFF))
    mbr[510:512] = b'\x55\xaa'
    return bytes(mbr) + bytes(header).ljust(SECTOR, b'\0') + bytes(entries) + body + bytes(entries) + bytes(header).ljust(SECTOR, b'\0')


def refuses(testcase, disk, pattern):
    path = BUILD / 'disk.raw'
    path.write_bytes(disk)
    with testcase.assertRaisesRegex(tool.Refusal, pattern):
        tool.apfs_partition(path)


class Gpt(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        shutil.rmtree(BUILD, ignore_errors=True)
        BUILD.mkdir(parents=True)
        cls.raw = container()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(BUILD, ignore_errors=True)

    def test_the_one_apfs_partition_is_found_and_sliced_out_byte_for_byte(self):
        disk = gpt_disk([(APFS, self.raw)])
        path = BUILD / 'disk.raw'
        path.write_bytes(disk)
        first, last = tool.apfs_partition(path)
        self.assertEqual((first, last), (34, 34 + len(self.raw) // SECTOR - 1))
        out = BUILD / 'sliced.apfs'
        tool.slice_file(path, out, first * SECTOR, (last + 1 - first) * SECTOR, tool.Disk(25 * GIB, lambda p: 1 << 40))
        self.assertEqual(out.read_bytes(), self.raw)
        self.assertEqual(sorted(p.name for p in BUILD.iterdir() if p.name.endswith('.slice')), [])

    def test_other_partitions_are_ignored_but_two_apfs_ones_are_refused(self):
        other = bytes(SECTOR * 8)
        path = BUILD / 'disk.raw'
        path.write_bytes(gpt_disk([(OTHER, other), (APFS, self.raw)]))
        self.assertEqual(tool.apfs_partition(path), (34 + 8, 34 + 8 + len(self.raw) // SECTOR - 1))
        refuses(self, gpt_disk([(APFS, self.raw), (APFS, self.raw)]), 'holds 2 Apple APFS partitions')
        refuses(self, gpt_disk([(OTHER, other)]), 'holds 0 Apple APFS partitions')

    def test_seeded_wrong_disks_are_refused(self):
        good = bytearray(gpt_disk([(APFS, self.raw)]))
        cases = {}
        no_signature = bytearray(good)
        no_signature[SECTOR:SECTOR + 8] = b'EFI PARX'
        cases['no signature'] = (no_signature, 'no GPT header')
        header_crc = bytearray(good)
        header_crc[SECTOR + 56] ^= 1                        # the disk GUID: covered by the header CRC-32
        cases['header CRC'] = (header_crc, 'header fails its CRC-32')
        entries_crc = bytearray(good)
        entries_crc[2 * SECTOR + 40] ^= 1                   # an entry's last LBA: covered by the array CRC-32
        cases['entries CRC'] = (entries_crc, 'entry array fails its CRC-32')
        truncated = bytes(good)[:len(good) - 16 * SECTOR - 40000]
        cases['file too short for the partition'] = (truncated, 'outside the GPT usable range or the file')
        for label, (disk, pattern) in cases.items():
            with self.subTest(label):
                refuses(self, bytes(disk), pattern)
        with self.subTest('entry size'):
            odd = bytearray(good)
            struct.pack_into('<I', odd, SECTOR + 84, 64)
            struct.pack_into('<I', odd, SECTOR + 16, 0)
            struct.pack_into('<I', odd, SECTOR + 16, zlib.crc32(bytes(odd[SECTOR:SECTOR + 92])))
            refuses(self, bytes(odd), 'not 128-byte entries')

    def test_a_partition_outside_the_usable_range_is_refused_even_with_right_crcs(self):
        disk = bytearray(gpt_disk([(APFS, self.raw)]))
        struct.pack_into('<Q', disk, 2 * SECTOR + 32, 2)                    # first LBA 2: inside the GPT's own structures
        header = bytearray(disk[SECTOR:SECTOR + 92])
        entries = bytes(disk[2 * SECTOR:2 * SECTOR + 128 * 128])
        struct.pack_into('<I', header, 88, zlib.crc32(entries))
        struct.pack_into('<I', header, 16, 0)
        struct.pack_into('<I', header, 16, zlib.crc32(bytes(header)))
        disk[SECTOR:SECTOR + 92] = header
        refuses(self, bytes(disk), 'outside the GPT usable range')

    def test_a_short_source_never_leaves_a_half_written_file(self):
        path = BUILD / 'disk.raw'
        path.write_bytes(b'x' * 4096)
        out = BUILD / 'never.apfs'
        with self.assertRaisesRegex(tool.Refusal, 'ended inside the APFS partition'):
            tool.slice_file(path, out, 1024, 1 << 20, tool.Disk(25 * GIB, lambda p: 1 << 40))
        self.assertEqual([p.name for p in BUILD.iterdir() if p.name.startswith('never')], [])

    @needs_hdiutil
    def test_a_real_guid_partitioned_apfs_image_converts_to_the_bare_container(self):
        image = BUILD / 'real.dmg'
        subprocess.run(['hdiutil', 'create', '-size', '24m', '-layout', 'GPTSPUD', '-fs', 'APFS', '-volname', 'T', '-type', 'UDIF', '-ov', str(image)],
                       check=True, capture_output=True)
        out = BUILD / 'out'
        out.mkdir()
        fmt = tool.convert_udif(image, out / 'basesystem.apfs', tool.Disk(25 * GIB, lambda p: 1 << 40))
        self.assertIn('GUID map', fmt)
        sliced = (out / 'basesystem.apfs').read_bytes()
        block, count = struct.unpack_from('<IQ', sliced, 36)
        self.assertEqual((sliced[32:36], block, block * count, len(sliced) % 4096), (b'NXSB', 4096, len(sliced), 0),
                         'a bare APFS container: NXSB at byte 32 and its own block count equal the file size')
        self.assertLess(len(sliced), 24 << 20)
        self.assertEqual(sorted(p.name for p in out.iterdir()), ['basesystem.apfs'], 'the whole-disk scratch file is gone')


if __name__ == '__main__':
    unittest.main()
