#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Hostile chunk tables at the 2^31 and 2^32 edges through the C interface header (probes/sp11-stick-bundle.h, built with SNAP_SP11_STICK_ROOT_CARRY=1), complementing BigC of
tools/test-sp11-stick-bundle-big.py, which only reaches the header with valid files (that file is at its size limit, so these cases live here). The harness is the same
(tools/test-sp11-stick-bundle-big.c: AddressSanitizer, UndefinedBehaviorSanitizer, the implicit-conversion checks). Each case edits one entry of a table, recomputes the table digest and
the head's copy of it, and must be refused by name; each has a valid twin. Nine copies of the header, each with one 32-bit narrowing or one off-by-one put in by a text substitution,
must each change the verdict of the case that is aimed at it.

    python3 tools/test-sp11-stick-bundle-big-tables.py        (clang on macOS is needed: without it SKIP, which is not a pass)

The 2 GiB container is sparse (no disk, and the harness reads it through pread), so the resident set stays small. Everything is written below
build/sp11-bundle-big-tables-scratch/ and deleted. No hardware, no Apple material. Hardware NOT RUN."""
import contextlib
import importlib.util
import random
import resource
import shutil
import struct
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / 'probes'))
import sp11_stick_bundle as sb  # noqa: E402
import sp11_stick_bundle_root as root  # noqa: E402

spec = importlib.util.spec_from_file_location('sp11_bundle_big_fixtures', HERE / 'test-sp11-stick-bundle-big.py')
big = importlib.util.module_from_spec(spec)
spec.loader.exec_module(big)        # the container, the small chunks and the compiler of BigC: one definition of each

SCRATCH = ROOT / 'build' / 'sp11-bundle-big-tables-scratch'
HEADER, HARNESS = ROOT / 'probes/sp11-stick-bundle.h', HERE / 'test-sp11-stick-bundle-big.c'
GIB = 1 << 30
BASE, LOAD = 0x880000000, 0x180000000
ZERO_4G = 0x100010000           # a ZERO chunk of 4 GiB + 64 KiB: its end does not fit 32 bits
FLAGS = ['-DSNAP_SP11_STICK_MONITOR=1', '-DSNAP_SP11_STICK_ROOT_CARRY=1']
ENTRY, ZEROS = sb.CHUNK_BYTES, '00' * 32

# (what is put in, the text of the header, what it is replaced with, the case that must notice)
SEEDS = (
    ('a 32-bit file offset', 'uint64_t foff = SP11SB_CHUNK_U64(e, file_offset), aligned;', 'uint64_t foff = (uint32_t)SP11SB_CHUNK_U64(e, file_offset), aligned;', 'offset 2^32'),
    ('a file limit that is one too high', 'file_size > SP11SB_FILE_MAX) return SP11SB_E_GUEST_SIZE;', 'file_size >= SP11SB_FILE_MAX) return SP11SB_E_GUEST_SIZE;', 'a 2 GiB file'),
    ('a file check that is one too lax', 'f->size > SP11SB_FILE_MAX ||', 'f->size > SP11SB_FILE_MAX + 1 ||', 'a file of 2 GiB + 1'),
    ('a 32-bit end of the source range', 'size > file_size - foff) return SP11SB_E_CHUNK_EOF;', '(uint32_t)(foff + size) > file_size) return SP11SB_E_CHUNK_EOF;', 'a wrapping size'),
    ('bytes after the last chunk accepted', 'if (expect != file_size) return SP11SB_E_GUEST_SIZE;', 'if (expect > file_size) return SP11SB_E_GUEST_SIZE;', 'a short last chunk'),
    ('a 32-bit size for the end of the previous chunk', 'prev_end = dest + size;', 'prev_end = dest + (uint32_t)size;', 'a data chunk in a 4 GiB zero chunk'),
    ('a whole-file length through a signed int', 'sha256(f, len, d);', 'sha256(f, (uint64_t)(int)len, d);', 'the digest of a 2 GiB file'),
    ('no range check on the last chunk', 'if (dest < load_base || dest - load_base > load_size || size > load_size - (dest - load_base)) return SP11SB_E_CHUNK_RANGE;',
     'if (i + 1 < count && (dest < load_base || dest - load_base > load_size || size > load_size - (dest - load_base))) return SP11SB_E_CHUNK_RANGE;', 'a chunk at the window end'),
    ('a 32-bit end address of the previous chunk', 'prev_end = dest + size;', 'prev_end = (uint32_t)(dest + size);', 'overlap above 4 GiB'))


def setUpModule():
    shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True)


def tearDownModule():
    shutil.rmtree(SCRATCH, ignore_errors=True)


def compile_harness(name, header_text=None):
    """The harness against the real header, or against a copy of it with `header_text` (the harness includes ../probes/ relative to itself, so the copy gets its own two directories)."""
    tree = SCRATCH / name
    exe = tree / 'harness'
    if header_text is None:
        sources = [HARNESS]
        tree.mkdir()
    else:
        (tree / 'probes').mkdir(parents=True)
        (tree / 'tools').mkdir()
        (tree / 'probes' / HEADER.name).write_text(header_text)
        shutil.copy(HARNESS, tree / 'tools' / HARNESS.name)
        sources = [tree / 'tools' / HARNESS.name]
    done = subprocess.run([big.clang, '-std=c11', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined,implicit-conversion', '-fno-sanitize-recover=undefined',
                           *FLAGS, *map(str, sources), '-o', str(exe)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return exe


def verdict(exe, path, written, index=None, file_digest=ZEROS, policy=0):
    """The harness's answer for a GUEST.DAT: the name of the first failing check, or CRASH when the sanitizers ended the run."""
    done = subprocess.run([str(exe), 'guest', str(path), hex(BASE), hex(LOAD), hex(written['file_size']), str(written['count']), index or written['index_sha256'], file_digest,
                           str(policy)], capture_output=True, text=True)
    return done.stdout.split()[2] if done.returncode == 0 else 'CRASH'


def file_verdict(exe, role, size):
    done = subprocess.run([str(exe), 'file', str(role), hex(0x1000), hex(size)], capture_output=True, text=True)
    return done.stdout.split()[2] if done.returncode == 0 else 'CRASH'


@contextlib.contextmanager
def patched(path, index, **fields):
    """Entry `index` of the table edited in place (dest, size, offset), the table digest and the head's copy of it recomputed; yields the new digest and undoes all of it."""
    with open(path, 'r+b') as f:
        head = bytearray(f.read(sb.GUEST_HEAD_BYTES))
        count = sb.get(head, sb.GUEST_LAYOUT, 'entry_count')
        old_table = f.read(ENTRY * count)
        table = bytearray(old_table)
        dest, size, offset = struct.unpack_from('<QQQ', table, ENTRY * index)
        struct.pack_into('<QQQ', table, ENTRY * index, fields.get('dest', dest), fields.get('size', size), fields.get('offset', offset))
        old_index = sb.get(head, sb.GUEST_LAYOUT, 'index_sha256')
        new_index = sb.sha256(bytes(table))
        f.seek(sb.GUEST_HEAD_BYTES)
        f.write(table)
        sb.put(head, sb.GUEST_LAYOUT, 'index_sha256', new_index)
        f.seek(0)
        f.write(head)
    try:
        yield new_index.hex()
    finally:
        with open(path, 'r+b') as f:
            sb.put(head, sb.GUEST_LAYOUT, 'index_sha256', old_index)
            f.write(head)
            f.write(old_table)


class Tables(unittest.TestCase):
    @classmethod
    @big.needs_clang
    def setUpClass(cls):
        cls.exe = compile_harness('real', None)
        cls.big_path = SCRATCH / 'big.dat'
        cls.big_written = big.container(cls.big_path, 2 * GIB, BASE, LOAD)
        with root.FileView(cls.big_path) as view:
            cls.big_digest = sb.sha256_range(view).hex()
        cls.last = cls.big_written['count'] - 1                 # the chunk that runs to the end of the file
        with open(cls.big_path, 'rb') as f:
            f.seek(sb.GUEST_HEAD_BYTES + ENTRY * cls.last)
            cls.last_dest, cls.last_size, cls.last_offset = struct.unpack('<QQQ', f.read(24))
        rng = random.Random(7)

        def small(name, *extra):
            path = SCRATCH / f'{name}.dat'
            return path, root.write_guest(path, BASE, LOAD, big.small_chunks(rng, BASE)[:5] + list(extra))
        data = lambda dest, size: dict(kind=sb.KIND_OTHER, dest=dest, data=rng.randbytes(size))      # noqa: E731
        zero = lambda dest, size: dict(kind=sb.KIND_OTHER, dest=dest, size=size)                    # noqa: E731
        low = BASE + 0x100000
        files = {
            'small': small('small', data(low, 4096)),
            'zero_over': small('zero_over', zero(low, ZERO_4G), data(BASE + 0x200000, 160)),
            'zero_then_data': small('zero_then_data', zero(low, ZERO_4G), data(low + ZERO_4G, 160)),
            'zero_alone': small('zero_alone', zero(low, ZERO_4G)),
            'at_window_end': small('at_window_end', data(BASE + LOAD, 4096)),
            'ends_at_window_end': small('ends_at_window_end', data(BASE + LOAD - 4096, 4096)),
            'data_overlap': small('data_overlap', data(low, 4096), data(low + 0x800, 160)),
            'data_adjacent': small('data_adjacent', data(low, 4096), data(low + 0x1000, 160))}
        cls.small_path, cls.small_written = files['small']
        cls.small_offset = struct.unpack_from('<Q', cls.small_path.read_bytes(), sb.GUEST_HEAD_BYTES + ENTRY * 5 + 16)[0]

        def ask(name, policy=0):
            path, written = files[name]
            return lambda exe: verdict(exe, path, written, policy=policy, file_digest=sb.sha256(path.read_bytes()).hex() if policy else ZEROS)

        def patch(path, written, index, **fields):
            def run(exe):
                with patched(path, index, **fields) as new_index:
                    return verdict(exe, path, written, new_index)
            return run
        # name: (what the header must answer, how to ask the harness); the edits of a case are undone after it
        cls.cases = {
            'offset 2^32': ('CHUNK_EOF', patch(cls.small_path, cls.small_written, 5, offset=(1 << 32) + cls.small_offset)),
            'a wrapping size': ('CHUNK_EOF', patch(cls.big_path, cls.big_written, cls.last, size=0x17FF00000)),
            'a short last chunk': ('GUEST_SIZE', patch(cls.big_path, cls.big_written, cls.last, size=cls.last_size - 64)),
            'a data chunk in a 4 GiB zero chunk': ('CHUNK_OVERLAP', ask('zero_over')),
            'a chunk at the window end': ('CHUNK_RANGE', ask('at_window_end')),
            'overlap above 4 GiB': ('CHUNK_OVERLAP', ask('data_overlap')),
            'a 2 GiB file': ('OK', lambda exe: verdict(exe, cls.big_path, cls.big_written)),
            'the digest of a 2 GiB file': ('OK', lambda exe: verdict(exe, cls.big_path, cls.big_written, file_digest=cls.big_digest, policy=2)),
            'a file of 2 GiB + 1': ('FILE_SIZE', lambda exe: file_verdict(exe, 2, 2 * GIB + 1))}
        cls.twins = {
            'the unedited small file': ('OK', ask('small', 3)),
            'a 4 GiB zero chunk with data after it': ('OK', ask('zero_then_data', 3)),
            'a 4 GiB zero chunk alone': ('OK', ask('zero_alone', 3)),
            'a chunk that ends at the window end': ('OK', ask('ends_at_window_end', 3)),
            'adjacent chunks above 4 GiB': ('OK', ask('data_adjacent', 3)),
            'a file of exactly 2 GiB': ('OK', lambda exe: file_verdict(exe, 2, 2 * GIB))}

    def test_a_table_edited_at_the_edges_is_refused_by_name_and_each_valid_twin_passes(self):
        for name, (want, run) in {**self.cases, **self.twins}.items():
            with self.subTest(name):
                self.assertEqual(run(self.exe), want)
        self.assertEqual(self.cases['a 2 GiB file'][1](self.exe), 'OK', 'the edits are undone')

    def test_each_seeded_32_bit_fault_of_the_header_changes_the_verdict_of_its_case(self):
        text = HEADER.read_text()
        for n, (what, old, new, case) in enumerate(SEEDS):
            with self.subTest(what):
                self.assertEqual(text.count(old), 1, 'the header changed shape: the seed no longer applies')
                want, run = self.cases[case]
                self.assertNotEqual(run(compile_harness(f'seed{n}', text.replace(old, new))), want, f'{what} was not noticed by the case {case!r}')

    def test_the_resident_set_of_this_run_is_bounded(self):
        self.assertLess(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, GIB)


if __name__ == '__main__':
    unittest.main()
