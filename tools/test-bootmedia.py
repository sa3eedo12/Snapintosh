#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""GPT boot medium, the xART fixture and the three-role ISC fixture.

    python3 tools/test-bootmedia.py
    ISC_FIXTURE_HOST_TOOLS=1 python3 tools/test-bootmedia.py   # adds MacHostToolTests

MacHostToolTests attach one scratch image under build/ with hdiutil and format
it with newfs_apfs; they are skipped unless that variable is set on macOS.
"""
import contextlib
import hashlib
import io
import os
from pathlib import Path
import runpy
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'probes'))
import bootmedia


class GPTTests(unittest.TestCase):
    def fixture(self):
        root, isc = b'R' * 0x14000, b'I' * 0x8000
        base = 0x100000000
        chunks, desc = bootmedia.assemble(root, isc, base, base + (256 << 20))
        image = bytearray(desc['bytes'])
        cursor = base
        for pa, blob in sorted(chunks.items()):
            self.assertEqual(pa, cursor, 'uninitialized or overlapping guest media bytes')
            image[pa - base:pa - base + len(blob)] = blob
            cursor += len(blob)
        self.assertEqual(cursor, base + len(image))
        self.assertIs(chunks[base + desc['root'][0]], root)
        self.assertIs(chunks[base + desc['isc'][0]], isc)
        return image, desc

    def test_native_disk_structure_and_unmodified_payloads(self):
        image, desc = self.fixture()
        self.assertEqual(bootmedia.inspect(lambda o, n: image[o:o+n], len(image)), desc)
        self.assertEqual(image[510:512], b'\x55\xaa')
        self.assertEqual(image[450], 0xee)
        for offset in (512, len(image) - 512):
            h = bytearray(image[offset:offset+92])
            expected = struct.unpack_from('<I', h, 16)[0]
            h[16:20] = bytes(4)
            self.assertEqual(zlib.crc32(h), expected)
        self.assertEqual(image[1024:1040], bootmedia.APFS_TYPE.bytes_le)
        self.assertEqual(image[1152:1168], bootmedia.ISC_TYPE.bytes_le)
        for name, fill in [('root', b'R'), ('isc', b'I')]:
            off, size = desc[name]
            self.assertEqual(image[off:off+size], fill * size)
        end = desc['root'][0] + desc['root'][1]
        self.assertFalse(any(image[end:desc['isc'][0]]))

    def test_corrupt_gpt_copies_fail_before_payload_access(self):
        image, _ = self.fixture()
        # Primary/backup headers, both arrays, protective MBR, extra entries.
        for off in [450, 510, 512, 528, 536, 552, 584, 600, 1024, 1152, 1280,
                    len(image)-512, len(image)-496, len(image)-16384]:
            bad = bytearray(image)
            bad[off] ^= 1
            with self.subTest(offset=off), self.assertRaises(ValueError):
                bootmedia.inspect(lambda o, n: bad[o:o+n], len(bad))

    def test_crc_valid_wrong_partitions_are_rejected(self):
        image, _ = self.fixture()
        for field, value in [(32, 1), (40, (len(image)//512)), (48, 1),
                             (128+32, 2048), (128+40, 2047)]:
            bad = bytearray(image)
            struct.pack_into('<Q', bad, 1024 + field, value)
            entries = bad[1024:1024+16384]
            bad[-512-16384:-512] = entries
            for off in [512, len(bad)-512]:
                struct.pack_into('<I', bad, off+88, zlib.crc32(entries))
                struct.pack_into('<I', bad, off+16, 0)
                struct.pack_into('<I', bad, off+16, zlib.crc32(bad[off:off+92]))
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                bootmedia.inspect(lambda o, n: bad[o:o+n], len(bad))

    def test_bounds_and_allocator_headroom(self):
        for sizes in [(0, 16384), (16383, 16384), (16384, 16385)]:
            with self.assertRaises(ValueError):
                bootmedia.layout(*sizes)
        for base, end in [(-16384, 1<<30), (1, 1<<30), (0, 128<<20), (0, (1<<36)+1)]:
            with self.assertRaises(ValueError):
                bootmedia.assemble(bytes(16384), bytes(16384), base, end)

    def test_sparse_upload_replaces_old_ram_exactly(self):
        base = 0x100000000
        source = bytearray((8 << 20) + 0x4000)
        source[3] = 17
        source[0x234567] = 42
        source[-1] = 99
        memory = bytearray(b'\xaa' * len(source))
        calls = []

        class Proxy:
            def memset8(self, address, value, size):
                calls.append(('zero', address, size))
                self_range(address, size)
                memory[address-base:address-base+size] = bytes([value]) * size

            def writemem(self, address, data):
                calls.append(('write', address, len(data)))
                self_range(address, len(data))
                memory[address-base:address-base+len(data)] = data

        def self_range(address, size):
            self.assertLessEqual(size, 8 << 20)
            self.assertGreaterEqual(address, base)
            self.assertLessEqual(address+size, base+len(source))

        sent = bootmedia.upload_auxiliary(Proxy(), base, source)
        self.assertEqual(memory, source)
        self.assertEqual(sent, 2 * (64 << 10) + 0x4000)
        self.assertEqual([c[0] for c in calls], ['zero', 'zero', 'write', 'write', 'write'])
        for address, data in [(base+1, source), (base, bytes(3)), (1<<36, source)]:
            before = list(calls)
            with self.assertRaises(ValueError):
                bootmedia.upload_auxiliary(Proxy(), address, data)
            self.assertEqual(calls, before)


NAMES = ('Snapintosh-xART', 'Snapintosh-Preboot', 'Snapintosh-Hardware')
ISC_ROLES = {256: NAMES[0], 16: NAMES[1], 320: NAMES[2]}
BUILD = ROOT / 'build'
HOST_TOOLS = (os.environ.get('ISC_FIXTURE_HOST_TOOLS') == '1' and sys.platform == 'darwin' and
              all(shutil.which(tool) for tool in ('hdiutil', 'newfs_apfs')))


def scratch_dir():
    BUILD.mkdir(exist_ok=True)
    return tempfile.TemporaryDirectory(dir=BUILD, prefix='test-isc-')


class Oversized:
    def __len__(self):
        return (2 << 30) + 0x4000

    def __getitem__(self, index):
        raise AssertionError('oversized input must not be read')


class Fixtures:
    api = runpy.run_path(str(ROOT / 'tools/make-isc-fixture.py'))

    def volume(self, name, role=0):
        block = bytearray(4096)
        struct.pack_into('<I', block, 24, 13)
        block[32:36] = b'APSB'
        struct.pack_into('<Q', block, 264, 1)
        struct.pack_into('<H', block, 964, role)
        block[704:704 + len(name)] = name.encode()
        block[:8] = self.api['checksum'](block)
        return bytes(block)

    def fixture(self, names=NAMES, roles=None, max_fs=3, blocks=8):
        roles = roles or (0,) * len(names)
        image = bytearray(blocks * 4096)
        image[32:36] = b'NXSB'
        struct.pack_into('<IQ', image, 36, 4096, blocks)
        struct.pack_into('<I', image, 180, max_fs)
        for index, (name, role) in enumerate(zip(names, roles)):
            image[(index + 1) * 4096:(index + 2) * 4096] = self.volume(name, role)
        image[:8] = self.api['checksum'](image[:4096])
        return image

    def apply(self, image, patches):
        result = bytearray(image)
        for offset, block in patches.items():
            result[offset:offset + 4096] = block
        return result


class ThreeRoleTests(Fixtures, unittest.TestCase):
    def refused(self, image, reason):
        with self.assertRaisesRegex(ValueError, reason):
            self.api['make_three_role'](image)

    def test_roles_stamped_and_only_role_and_checksum_change(self):
        source = self.fixture()
        before = bytes(source)
        patches, volumes = self.api['make_three_role'](source)
        self.assertEqual(source, before)
        self.assertEqual([v['by'] for v in volumes], ['stamp'] * 3)
        result = self.apply(source, patches)
        for index, role in enumerate((0x100, 0x10, 0x140)):
            offset = (index + 1) * 4096
            self.assertEqual(struct.unpack_from('<H', result, offset + 964)[0], role)
            self.assertEqual(result[offset:offset + 8], self.api['checksum'](result[offset:offset + 4096]))
        changed = {i for i, (a, b) in enumerate(zip(source, result)) if a != b}
        allowed = {o + k for o in (4096, 8192, 12288) for k in (*range(8), 964, 965)}
        self.assertLessEqual(changed, allowed)

    def test_roles_the_formatter_set_are_kept(self):
        source = self.fixture(roles=(0, 0x10, 0x140))
        patches, volumes = self.api['make_three_role'](source)
        self.assertEqual(sorted(patches), [4096])
        self.assertEqual([v['by'] for v in volumes], ['stamp', 'formatter', 'formatter'])

    def test_wrong_role_refused(self):
        self.refused(self.fixture(roles=(0x10, 0, 0)), 'Snapintosh-xART: role 0x10 is not one of 0x0, 0x100')
        self.refused(self.fixture(roles=(0, 0x140, 0)), 'Snapintosh-Preboot: role 0x140 is not one of')
        self.refused(self.fixture(roles=(0, 0, 0x100)), 'Snapintosh-Hardware: role 0x100 is not one of')
        self.refused(self.fixture(roles=(0, 0, 0x40)), 'Snapintosh-Hardware: role 0x40 is not one of')

    def test_missing_or_extra_volume_refused(self):
        self.refused(self.fixture(names=NAMES[:2]), 'found 2')
        self.refused(self.fixture(names=()), 'found 0')
        self.refused(self.fixture(names=NAMES + ('Snapintosh-extra',)), 'found 4')

    def test_wrong_or_repeated_volume_name_refused(self):
        self.refused(self.fixture(names=(NAMES[0], NAMES[1], 'Macintosh HD')), "unexpected volume name 'Macintosh HD'")
        self.refused(self.fixture(names=(NAMES[0], NAMES[0], NAMES[2])), 'duplicate volume')

    def test_oversize_and_undersize_containers_refused(self):
        self.refused(Oversized(), 'outside the permitted bound')
        self.refused(self.fixture(max_fs=2), 'fewer than three volumes')

    def test_non_fresh_container_refused(self):
        for offset, reason in [(184, 'not empty'), (192, 'not empty'), (200, 'not empty'), (208, 'not empty'),
                               (216, 'not empty'), (264, 'encrypted'), (24, 'not a filesystem object')]:
            bad = self.fixture()
            bad[12288 + offset] ^= 1
            bad[12288:12296] = self.api['checksum'](bad[12288:16384])
            with self.subTest(offset=offset):
                self.refused(bad, 'Snapintosh-Hardware: .*' + reason)
        bad = self.fixture()
        bad[12288 + 100] ^= 1
        self.refused(bad, 'Snapintosh-Hardware: volume superblock checksum is invalid')
        bad = self.fixture()
        bad[100] ^= 1
        self.refused(bad, 'invalid container superblock checksum')
        bad = self.fixture()
        bad[32] ^= 1
        self.refused(bad, 'unwrapped APFS')

    def test_patched_copy_is_byte_exact_and_never_overwrites(self):
        with scratch_dir() as work:
            source, output = Path(work) / 'in.img', Path(work) / 'out.img'
            image = self.fixture(blocks=768)
            source.write_bytes(image)
            with source.open('rb') as stream:
                view = self.api['FileImage'](stream)
                patches, _ = self.api['make_three_role'](view)
                digest = self.api['write_patched'](view, output, patches)
            expected = self.apply(image, patches)
            self.assertEqual(output.read_bytes(), expected)
            self.assertEqual(digest, hashlib.sha256(expected).hexdigest())
            with source.open('rb') as stream, self.assertRaises(FileExistsError):
                self.api['write_patched'](self.api['FileImage'](stream), output, patches)
            self.assertEqual(output.read_bytes(), expected)

    def test_file_view_finds_across_window_boundaries(self):
        with scratch_dir() as work:
            path = Path(work) / 'x.img'
            window = self.api['WINDOW']
            data = bytearray(3 * window)
            data[window - 2:window + 2] = b'APSB'
            path.write_bytes(data)
            with path.open('rb') as stream:
                view = self.api['FileImage'](stream)
                self.assertEqual(view.find(b'APSB'), window - 2)
                self.assertEqual(view.find(b'APSB', window - 1), -1)
                self.assertEqual(view[window - 4:window + 4], bytes(data[window - 4:window + 4]))


class ThreeRoleCommandTests(Fixtures, unittest.TestCase):
    def run_main(self, *argv):
        error = io.StringIO()
        with contextlib.redirect_stderr(error), contextlib.redirect_stdout(io.StringIO()), \
                self.assertRaises(SystemExit) as stop:
            self.api['main'](list(argv))
        self.assertEqual(stop.exception.code, 2)
        return error.getvalue()

    def test_stamp_refusals(self):
        with scratch_dir() as work:
            work = Path(work)
            source, output = work / 'in.img', work / 'out.img'
            source.write_bytes(self.fixture())
            link = work / 'link.img'
            link.symlink_to(source)
            good = hashlib.sha256(source.read_bytes()).hexdigest()
            for argv, reason in [
                    (['--three-role', source, output, '--source-sha256', '0' * 64], 'does not match the explicitly supplied SHA256'),
                    (['--three-role', link, output, '--source-sha256', good], 'regular scratch image file'),
                    (['--three-role', source, output], 'the following arguments are required: --source-sha256'),
                    (['--three-role', source, '--source-sha256', good], 'the following arguments are required: output'),
                    (['--three-role', '--format-scratch', work / 'a.img'], 'are exclusive'),
                    (['--format-scratch', work / 'a.img', source], 'takes no source'),
            ]:
                with self.subTest(reason=reason):
                    self.assertIn(reason, self.run_main(*map(str, argv)))
                    self.assertFalse(output.exists())
            output.write_bytes(b'keep')
            self.assertIn('File exists', self.run_main('--three-role', str(source), str(output), '--source-sha256', good))
            self.assertEqual(output.read_bytes(), b'keep')

    def test_output_the_speed_test_cannot_read_is_refused_and_removed(self):
        with scratch_dir() as work:
            source, output = Path(work) / 'in.img', Path(work) / 'out.img'
            image = self.fixture()
            source.write_bytes(image)
            with self.assertRaisesRegex(ValueError, 'speed_test.isc_roles refused the output'):
                self.api['stamp_three_role'](source, output, hashlib.sha256(image).hexdigest())
            self.assertFalse(output.exists())

    def test_source_changed_during_stamping_is_refused(self):
        with scratch_dir() as work:
            source, output = Path(work) / 'in.img', Path(work) / 'out.img'
            image = self.fixture()
            source.write_bytes(image)
            copy = self.api['write_patched']

            def write_then_touch(*args):
                digest = copy(*args)
                with source.open('r+b') as stream:
                    stream.seek(20000)
                    stream.write(b'x')
                return digest

            namespace = self.api['stamp_three_role'].__globals__  # run_path returns a copy of it
            with mock.patch.dict(namespace, {'write_patched': write_then_touch}), \
                    self.assertRaisesRegex(ValueError, 'changed while it was stamped'):
                self.api['stamp_three_role'](source, output, hashlib.sha256(image).hexdigest())
            self.assertFalse(output.exists())

    def test_oversize_source_refused_before_reading(self):
        with scratch_dir() as work:
            source = Path(work) / 'big.img'
            with source.open('xb') as stream:
                stream.truncate((2 << 30) + 0x4000)
            with mock.patch.object(self.api['FileImage'], '__getitem__', side_effect=AssertionError('read')):
                self.assertIn('outside the permitted bound', self.run_main(
                    '--three-role', str(source), str(Path(work) / 'out.img'), '--source-sha256', '0' * 64))

    def test_format_refusals_never_reach_a_macos_tool(self):
        with scratch_dir() as work, mock.patch.object(subprocess, 'run', side_effect=AssertionError('tool run')):
            work = Path(work)
            existing = work / 'old.img'
            existing.write_bytes(b'keep')
            for argv, reason in [
                    (['--format-scratch', ROOT / 'tools' / 'not-in-build.img'], 'new file under build/'),
                    (['--format-scratch', work / 'a.img', '--size-mib', '1024'], 'more than 1 GiB'),
                    (['--format-scratch', work / 'a.img', '--size-mib', '2049'], 'more than 1 GiB'),
                    (['--format-scratch', existing], 'File exists'),
                    (['--build-three-role', ROOT / 'tools' / 'not-in-build.apfs'], 'new file under build/'),
                    (['--build-three-role', existing], 'output exists'),
            ]:
                with self.subTest(reason=reason):
                    self.assertIn(reason, self.run_main(*map(str, argv)))
            self.assertEqual(existing.read_bytes(), b'keep')
            self.assertEqual(sorted(p.name for p in work.iterdir()), ['old.img'])

    def test_default_mode_is_unchanged(self):
        with scratch_dir() as work:
            source, output = Path(work) / 'in.img', Path(work) / 'out.img'
            image = ScratchTests.fixture(self)
            source.write_bytes(image)
            with contextlib.redirect_stdout(io.StringIO()) as report:
                self.api['main']([str(source), str(output), '--source-sha256',
                                  hashlib.sha256(image).hexdigest()])
            self.assertEqual(output.read_bytes(), bytes(self.api['make_xart'](image)[0]))
            self.assertIn('"role": 256', report.getvalue())
            self.assertIn('the following arguments are required: source, output, --source-sha256',
                          self.run_main())


class Overlay(runpy.run_path(str(ROOT / 'tools/make-isc-fixture.py'))['FileImage']):
    """A file view with whole 4 KiB blocks replaced, to mutate a real image without copying it."""

    def __init__(self, stream, patches):
        super().__init__(stream)
        self.patches = patches

    def __getitem__(self, key):
        start, stop, _ = key.indices(self.size)
        data = bytearray(super().__getitem__(key))
        for offset, block in self.patches.items():
            low, high = max(start, offset), min(stop, offset + len(block))
            if low < high:
                data[low - start:high - start] = block[low - offset:high - offset]
        return bytes(data)


@unittest.skipUnless(HOST_TOOLS, 'set ISC_FIXTURE_HOST_TOOLS=1 on macOS: attaches one scratch image under build/')
class MacHostToolTests(unittest.TestCase):
    api = runpy.run_path(str(ROOT / 'tools/make-isc-fixture.py'))

    @classmethod
    def setUpClass(cls):
        BUILD.mkdir(exist_ok=True)
        cls.work = Path(tempfile.mkdtemp(dir=BUILD, prefix='test-isc-host-'))
        cls.scratch = cls.work / 'fresh.img'
        try:
            cls.fresh = cls.api['format_scratch'](cls.scratch, cls.api['MIN_THREE_ROLE_BYTES'])
            sys.path.insert(0, str(ROOT / 'tools'))
            from cycle_lib import speed_test
            cls.speed_test = speed_test
        except BaseException:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            cls.api['detach_scratch'](cls.scratch)
        finally:
            shutil.rmtree(cls.work, ignore_errors=True)

    def test_formatted_scratch_is_detached_with_the_three_volumes(self):
        self.assertIsNone(self.api['scratch_attachment'](self.scratch))
        roles = self.speed_test.isc_roles(self.scratch)
        self.assertEqual(sorted(roles.values()), sorted(NAMES))
        self.assertEqual((roles[16], roles[320]), (NAMES[1], NAMES[2]))
        refused = self.fresh['formatter'][0]['formatter_refused']
        self.assertEqual(256 in roles, refused is None)

    def test_stamped_image_reads_back_as_the_three_roles(self):
        output = self.work / 'isc.apfs'
        result = self.api['stamp_three_role'](self.scratch, output, self.fresh['sha256'])
        self.assertEqual(self.speed_test.isc_roles(output), ISC_ROLES)
        self.assertEqual(result['roles_read_back'], {str(r): n for r, n in sorted(ISC_ROLES.items())})
        with output.open('rb') as stream:
            self.assertEqual(self.api['sha256_of'](self.api['FileImage'](stream)), result['sha256'])
        self.assertIsNone(self.api['scratch_attachment'](self.scratch))

    def test_mutants_of_the_real_container_are_refused_each_for_its_own_reason(self):
        with self.scratch.open('rb') as stream:
            volumes = {v['name']: v['superblock_offset']
                       for v in self.api['make_three_role'](self.api['FileImage'](stream))[1]}
            blocks = {n: self.api['FileImage'](stream)[o:o + 4096] for n, o in volumes.items()}
            used = bytearray(blocks[NAMES[1]])
            used[184] = 1
            used[:8] = self.api['checksum'](used)
            for patches, reason in [
                    ({volumes[NAMES[0]]: self.api['set_role'](blocks[NAMES[0]], 0x10)},
                     'Snapintosh-xART: role 0x10 is not one of'),
                    ({volumes[NAMES[2]]: bytes(4096)}, 'found 2'),
                    ({volumes[NAMES[1]]: bytes(used)}, 'Snapintosh-Preboot: volume is not empty'),
            ]:
                with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                    self.api['make_three_role'](Overlay(stream, patches))


class ScratchTests(unittest.TestCase):
    api = runpy.run_path(str(ROOT / 'tools/make-isc-fixture.py'))

    def test_scratch_size_refused_before_reading(self):
        class Oversized:
            def __len__(self):
                return (2 << 30) + 0x4000
            def __getitem__(self, index):
                raise AssertionError('oversized input must not be read')
        self.assertEqual(self.api['MAX_SCRATCH_BYTES'], 2 << 30)
        with self.assertRaises(ValueError):
            self.api['make_xart'](Oversized())

    def fixture(self):
        image = bytearray(16384)
        image[32:36] = b'NXSB'
        struct.pack_into('<IQ', image, 36, 4096, 4)
        block = bytearray(4096)
        struct.pack_into('<I', block, 24, 13)
        block[32:36] = b'APSB'
        struct.pack_into('<Q', block, 264, 1)
        block[704:719] = b'Snapintosh-xART'
        block[:8] = self.api['checksum'](block)
        image[4096:8192] = block
        image[:8] = self.api['checksum'](image[:4096])
        return image

    def test_only_role_and_checksum_change(self):
        source = self.fixture()
        before = bytes(source)
        result, off = self.api['make_xart'](source)
        self.assertEqual(source, before)
        self.assertEqual(off, 4096)
        changes = {i for i, (a, b) in enumerate(zip(source, result)) if a != b}
        self.assertLessEqual(changes, set(range(off, off+8)) | {off+964, off+965})
        self.assertEqual(struct.unpack_from('<H', result, off+964)[0], 256)
        self.assertEqual(result[off:off+8], self.api['checksum'](result[off:off+4096]))

    def test_refuse_used_or_unrecognized_filesystems(self):
        source = self.fixture()
        for off in [24, 32, 184, 192, 200, 208, 216, 264, 704, 964]:
            bad = bytearray(source)
            bad[4096+off] ^= 1
            bad[4096:4104] = self.api['checksum'](bad[4096:8192])
            with self.subTest(offset=off), self.assertRaises(ValueError):
                self.api['make_xart'](bad)
        for off in [0, 32, 36, 40, 4096]:
            bad = bytearray(source)
            bad[off] ^= 1
            with self.subTest(offset=off), self.assertRaises(ValueError):
                self.api['make_xart'](bad)
        duplicate = bytearray(source)
        duplicate[8192:12288] = duplicate[4096:8192]
        with self.assertRaises(ValueError):
            self.api['make_xart'](duplicate)


if __name__ == '__main__':
    unittest.main()
