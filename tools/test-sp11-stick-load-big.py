#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests for the loader of the SP11 stick wrapper (probes/sp11-stick-load.c) at the sizes and addresses of the root carry (docs/SP11-STICK-ROOT.md): the copy of a ZERO chunk of
4 GiB + 64 KiB above 4 GiB, a 2 GiB container whose data ends at file offset 2^31 and whose destinations cross a 4 GiB boundary, the verify of that copy, and the entries that
sw_chunks_check must refuse. The loader linked is the real file, in the C harness tools/test-sp11-stick-load-big.c (a model of the machine under it, AddressSanitizer,
UndefinedBehaviorSanitizer, the implicit-conversion checks); five copies of it, each with one 32-bit narrowing put in by a text substitution, must each fail a check.

    Q1N1_SP11_TREE=<q1n1 checkout> python3 tools/test-sp11-stick-load-big.py        (without a checkout, or clang on macOS: SKIP, which is not a pass)

Not run: the verify of a ZERO chunk longer than 4 GiB (it would read 4 GiB of memory; its loop is sp11sb_zero's, a 64-bit index over a 64-bit count, read in
probes/sp11-stick-bundle.h), the cache maintenance and the stores on the unit. Everything is written below build/sp11-load-big-scratch/ and deleted. Hardware NOT RUN."""
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / 'probes'))
import sp11_stick_wrapper_testlib as T  # noqa: E402

SCRATCH = ROOT / 'build' / 'sp11-load-big-scratch'
LOADER = ROOT / 'probes' / 'sp11-stick-load.c'
GIB = 1 << 30
TREE = T.q1n1_tree()
CLANG = T.find_tool('clang')
usable = unittest.skipUnless(TREE and CLANG and sys.platform == 'darwin', 'a q1n1 checkout (set Q1N1_SP11_TREE) and clang on macOS are needed')

# (what is narrowed, the text of the loader, what it is replaced with, the scenario that must notice)
SEEDS = (
    ('the copy loop variable', '    uint64_t o, word;\n', '    uint32_t o;\n    uint64_t word;\n', 'zero'),
    ('the length of the cache clean', 'sp11hw_clean_range(k.dest, k.size);', 'sp11hw_clean_range(k.dest, (uint32_t)k.size);', 'zero'),
    ('the bytes-copied counter', 'c->head.r.bytes_copied += k.size;', 'c->head.r.bytes_copied += (uint32_t)k.size;', 'zero'),
    ('the file offset of an entry', 'k->file_offset = SP11SB_CHUNK_U64(k->entry, file_offset);', 'k->file_offset = (uint32_t)SP11SB_CHUNK_U64(k->entry, file_offset);', 'refuse'),
    ('the end of the source range', 'k->size > c->guest_len - k->file_offset', '(uint32_t)(k->file_offset + k->size) > c->guest_len', 'refuse'))


def setUpModule():
    shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True)


def tearDownModule():
    shutil.rmtree(SCRATCH, ignore_errors=True)


class Loader(unittest.TestCase):
    @classmethod
    @usable
    def setUpClass(cls):
        cls.flags = T.common_flags(TREE)
        cls.sha = SCRATCH / 'sha256.o'
        T.clang([*cls.flags, '-c', str(TREE / 'platform/uefi/sha256.c'), '-o', str(cls.sha)], 'compiling sha256.c')
        cls.exe = cls.build('real', LOADER)

    @classmethod
    def build(cls, name, loader):
        out = SCRATCH / name
        out.mkdir()
        flags = [*cls.flags, '-fsanitize=implicit-conversion', f'-I{ROOT / "probes"}']
        T.clang([*flags, '-c', str(loader), '-o', str(out / 'load.o')], f'compiling the loader ({name})')
        T.clang([*flags, '-c', str(HERE / 'test-sp11-stick-load-big.c'), '-o', str(out / 'harness.o')], 'compiling the harness')
        T.clang([*T.SANITIZE, str(out / 'load.o'), str(out / 'harness.o'), str(cls.sha), '-o', str(out / 'harness')], f'linking the harness ({name})')
        return out / 'harness'

    def run_harness(self, exe, *args):
        done = subprocess.run([str(exe), *args], capture_output=True, text=True, timeout=300)
        lines = [line.split() for line in done.stdout.splitlines()]
        results = {w[1]: w[2] for w in lines if w[0] == 'RESULT'}
        rss = next((int(w[1]) for w in lines if w[0] == 'MAXRSS'), None)
        return done, results, rss

    def passes(self, count, *args):
        done, results, rss = self.run_harness(self.exe, *args)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr[-1500:])
        self.assertEqual((len(results), set(results.values())), (count, {'ok'}), done.stdout)
        self.assertLess(rss, GIB)

    @usable
    def test_a_zero_chunk_of_4_gib_and_64_kib_is_stored_in_order_cleaned_and_counted(self):
        self.passes(5, 'zero')

    @usable
    def test_a_2_gib_container_above_4_gib_is_copied_and_verified_and_damage_is_named(self):
        self.passes(8, 'sparse', str(SCRATCH / 'sparse.bin'))
        self.assertFalse((SCRATCH / 'sparse.bin').exists())

    @usable
    def test_entries_that_the_32_bit_edges_would_let_through_are_refused_by_name(self):
        self.passes(7, 'refuse')

    @usable
    def test_each_seeded_32_bit_narrowing_fails_a_check(self):
        text = LOADER.read_text()
        for n, (what, old, new, scenario) in enumerate(SEEDS):
            with self.subTest(what):
                self.assertEqual(text.count(old), 1, 'the loader changed shape: the seed no longer applies')
                copy = SCRATCH / f'seed{n}.c'
                copy.write_text(text.replace(old, new))
                exe = self.build(f'seed{n}', copy)
                done, results, rss = self.run_harness(exe, scenario)
                self.assertNotEqual(done.returncode, 0, f'{what} was not noticed:\n{done.stdout}')


if __name__ == '__main__':
    unittest.main()
