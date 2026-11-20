#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The stager's big data file (tools/sp11_stage_bigdata.py, tools/stage-sp11-boot-media.py read_validated_input, tools/sp11_autorun.py `bigfile`; docs/SP11-STICK-ROOT.md section 9; EXPERIMENTAL,
host only, hardware NOT RUN): the root-carry form's GUEST.DAT is 1.93 GB and the stager must never hold it. Checked here: with the gate off a data file over 256 MiB is refused as it always was
(input_too_large) and the limit of the tree's plan stays 256 MiB; with the gate on a sparse stand-in of 1.9 GB is validated by its identity and its SHA-256 in blocks, the resident set of this
process growing by less than 256 MiB (the whole test runs well inside 1 GiB), and a sparse stand-in over 2 GiB is refused by the gate's own limit; the copy is made in blocks, equals the file and
refuses (removing the partial copy) a file that was replaced, truncated or grown after the validation; a link is never followed.

    python3 tools/test-sp11-stage-bigdata.py

No Apple material, no stick, no /Volumes: the stand-ins are sparse files under build/. Hardware NOT RUN."""
import hashlib
import importlib.util
import os
import resource
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))
import sp11_autorun as autorun  # noqa: E402
import sp11_stage_bigdata as bigdata  # noqa: E402

spec = importlib.util.spec_from_file_location('stage_sp11_boot_media_big', TOOLS / 'stage-sp11-boot-media.py')
stager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stager)
SCRATCH = ROOT / 'build' / 'sp11-stage-bigdata-test-scratch'
MIB, GIB = 1 << 20, 1 << 30


def rss():
    """The peak resident set of this process in bytes (macOS reports bytes, Linux kibibytes)."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak if sys.platform == 'darwin' else peak * 1024


def sparse(name, size, tail=b'tail'):
    """A sparse file of `size` bytes: zeros but for `tail` at its end."""
    SCRATCH.mkdir(parents=True, exist_ok=True)
    path = SCRATCH / name
    with open(path, 'wb') as f:
        f.truncate(size)
        f.seek(size - len(tail))
        f.write(tail)
    return path


def setUpModule():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    for p in SCRATCH.iterdir():
        p.unlink()


def tearDownModule():
    for p in SCRATCH.glob('*'):
        p.unlink()
    SCRATCH.rmdir()


def sha_of(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        while block := f.read(MIB):
            digest.update(block)
    return digest.hexdigest()


class Gate(unittest.TestCase):
    def test_the_limit_is_256_mib_without_the_gate_and_2_gib_with_it_and_the_context_restores_it(self):
        self.assertEqual((autorun.DATA_MAX_BYTES, autorun.DATA_MAX_BYTES_BIG, bigdata.BIG_MIN), (256 * MIB, 2 * GIB, 256 * MIB))
        with autorun.bigfile(True):
            self.assertEqual(autorun.DATA_MAX_BYTES, 2 * GIB)
            with autorun.bigfile(False):
                self.assertEqual(autorun.DATA_MAX_BYTES, 2 * GIB, 'a context that is off leaves what is held, and does not lower the limit of one that is on')
        self.assertEqual(autorun.DATA_MAX_BYTES, 256 * MIB)
        with self.assertRaises(RuntimeError), autorun.bigfile(True):
            raise RuntimeError('the limit is given back on an error')
        self.assertEqual(autorun.DATA_MAX_BYTES, 256 * MIB)


class Validate(unittest.TestCase):
    def test_without_the_gate_a_data_file_over_256_mib_is_refused_as_it_always_was(self):
        path = sparse('over.bin', 256 * MIB + 1)
        with self.assertRaises(stager.Refusal) as caught:
            stager.read_validated_input(path, 'data')
        self.assertEqual(caught.exception.reason, 'input_too_large')

    def test_with_the_gate_a_1_9_gb_stand_in_is_hashed_in_blocks_and_never_held(self):
        size = 1_926_621_440
        path = sparse('guest-sized.bin', size)
        before = rss()
        with autorun.bigfile(True):
            facts, data = stager.read_validated_input(path, 'data')
        grown = rss() - before
        self.assertIsInstance(data, bigdata.BigData)
        self.assertEqual((facts['size'], len(data), facts['sha256'], stager.sha256_bytes(data)), (size, size, data.sha256, data.sha256))
        self.assertEqual(data.sha256, sha_of(path), 'the digest is the whole file\'s')
        self.assertLess(grown, 256 * MIB, f'the resident set grew by {grown >> 20} MiB while a file of {size >> 20} MiB was hashed')
        self.assertLess(rss(), GIB, 'the whole test stays inside 1 GiB')

    def test_the_stagers_verify_hashes_a_file_over_2_gib_in_blocks_within_1_gib(self):
        """`verify` hashes every staged file through sha256_file in blocks of 1 MiB: the resident set does not follow the file, whatever its size."""
        path = sparse('over-2g-verify.bin', 2 * GIB + 4096)
        before = rss()
        digest = stager.sha256_file(path)
        self.assertEqual(digest, sha_of(path))
        self.assertLess(rss() - before, 256 * MIB)
        self.assertLess(rss(), GIB)

    def test_with_the_gate_a_file_over_2_gib_is_refused_by_the_limit_and_a_small_one_is_still_read_whole(self):
        over = sparse('over-2g.bin', 2 * GIB + 1)
        small = SCRATCH / 'small.bin'
        small.write_bytes(b'x' * 4096)
        with autorun.bigfile(True):
            with self.assertRaises(stager.Refusal) as caught:
                stager.read_validated_input(over, 'data')
            facts, data = stager.read_validated_input(small, 'data')
        self.assertEqual(caught.exception.reason, 'input_too_large')
        self.assertEqual((type(data), len(data), facts['sha256']), (bytes, 4096, hashlib.sha256(data).hexdigest()))

    def test_a_link_and_a_directory_are_never_followed_for_a_big_file_either(self):
        real = sparse('real.bin', 300 * MIB)
        link = SCRATCH / 'link.bin'
        link.symlink_to(real)
        with autorun.bigfile(True):
            for path, name in ((link, 'input_is_symlink'), (SCRATCH, 'input_not_regular_file')):
                with self.subTest(name), self.assertRaises(stager.Refusal) as caught:
                    stager.read_validated_input(path, 'data')
                self.assertEqual(caught.exception.reason, name)


class Copy(unittest.TestCase):
    """The copy, with the threshold lowered to 1 MiB so that a few MiB are a big file (the code is the same; a 1.9 GB copy is the stage dry run of the session)."""

    def setUp(self):
        patch = mock.patch.object(bigdata, 'BIG_MIN', MIB)
        patch.start()
        self.addCleanup(patch.stop)
        self.path = SCRATCH / 'src.bin'
        self.path.unlink(missing_ok=True)
        self.path.write_bytes(os.urandom(3 * MIB + 17))

    def big(self):
        with autorun.bigfile(True):
            return stager.read_validated_input(self.path, 'data')

    def test_the_copy_equals_the_file_in_blocks_and_the_stager_writes_it_through_copy_to(self):
        facts, data = self.big()
        self.assertIsInstance(data, bigdata.BigData)
        dest = SCRATCH / 'dest.bin'
        stager.write_file_deterministic(dest, data, 0o644)
        self.assertEqual((dest.stat().st_size, sha_of(dest)), (len(data), data.sha256))
        with mock.patch.object(bigdata, 'BLOCK', 4096):
            data.copy_to(SCRATCH / 'dest2.bin')
        self.assertEqual(sha_of(SCRATCH / 'dest2.bin'), data.sha256)

    def test_a_file_that_changed_after_the_validation_is_refused_and_no_partial_copy_is_left(self):
        for what, change in (('replaced, same size', lambda: self.path.write_bytes(os.urandom(self.path.stat().st_size))),
                             ('truncated', lambda: os.truncate(self.path, MIB)), ('grown', lambda: self.path.open('ab').write(b'more')),
                             ('swapped for another inode', lambda: (self.path.unlink(), self.path.write_bytes(os.urandom(3 * MIB + 17))))):
            with self.subTest(what):
                self.path.unlink(missing_ok=True)
                self.path.write_bytes(os.urandom(3 * MIB + 17))
                facts, data = self.big()
                change()
                dest = SCRATCH / 'never.bin'
                with self.assertRaises(stager.Refusal) as caught:
                    data.copy_to(dest)
                self.assertEqual(caught.exception.reason, 'input_changed_while_reading')
                self.assertFalse(dest.exists(), 'no partial copy is left')

    def test_a_file_replaced_by_a_link_between_the_check_and_the_copy_is_not_followed(self):
        facts, data = self.big()
        other = SCRATCH / 'other.bin'
        other.write_bytes(self.path.read_bytes())
        self.path.unlink()
        self.path.symlink_to(other)
        with self.assertRaises(stager.Refusal) as caught:
            data.copy_to(SCRATCH / 'never2.bin')
        self.assertEqual(caught.exception.reason, 'input_unreadable')
        self.assertFalse((SCRATCH / 'never2.bin').exists())


class Hygiene(unittest.TestCase):
    def test_the_files_are_under_the_size_of_a_file_of_this_tool(self):
        for rel in ('tools/sp11_stage_bigdata.py', 'tools/stage-sp11-boot-media.py', 'tools/sp11_autorun.py', 'tools/test-sp11-stage-bigdata.py'):
            text = (ROOT / rel).read_text()
            with self.subTest(rel):
                self.assertLess(len(text.encode()), 51_200)
                self.assertLess(len(text.splitlines()), 1500)
                self.assertEqual(text.splitlines()[1 if text.startswith('#!') else 0].count('SPDX-License-Identifier: MIT'), 1)


if __name__ == '__main__':
    unittest.main()
