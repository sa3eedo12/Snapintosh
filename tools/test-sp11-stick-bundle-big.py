#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests for the root carry's host side (docs/SP11-STICK-ROOT.md): files of up to 2 GiB, the streamed GUEST.DAT that holds a 1.8 GiB RAM root, the `root-guest` command, the
gate of the wrapper and the box cap of the pair built with --root-carry.

    python3 tools/test-sp11-stick-bundle-big.py
    Q1N1_SP11_TREE=<the q1n1 tree with Q1N1_SP11_BIGFILE> python3 tools/test-sp11-stick-bundle-big.py     (a path that is no checkout: the tests that need it SKIP)

The multi-GiB cases use sparse files (a hole costs no disk and, read through a window, no memory) and a C harness built with AddressSanitizer and UndefinedBehaviorSanitizer
(tools/test-sp11-stick-bundle-big.c); each asserts a resident-set bound. Everything is written below build/sp11-bundle-big-scratch/, deleted at the end. No hardware, nothing
under /Volumes, no Apple material (the roots are generated). A SKIP is not a pass. Hardware NOT RUN."""
import contextlib
import hashlib
import importlib.util
import io
import json
import mmap
import os
import random
import re
import resource
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / 'probes'))
import sp11_stick_bundle as sb  # noqa: E402
import sp11_stick_bundle_build as bb  # noqa: E402
import sp11_stick_bundle_chunks as ch  # noqa: E402
import sp11_stick_bundle_m1 as m1  # noqa: E402
import sp11_stick_bundle_root as root  # noqa: E402
import sp11_stick_bundle_source as src  # noqa: E402

SCRATCH = ROOT / 'build' / 'sp11-bundle-big-scratch'
DOC = ROOT / 'docs' / 'SP11-STICK-ROOT.md'
MIB, GIB = sb.MIB, 1 << 30
sha = lambda data: hashlib.sha256(data).hexdigest()     # noqa: E731


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


B = load('sp11_bundle_build_fixtures', HERE / 'test-sp11-stick-bundle-build.py')     # the synthetic dumps and facts, cli()
clang = shutil.which('clang') or ('/opt/homebrew/opt/llvm/bin/clang' if Path('/opt/homebrew/opt/llvm/bin/clang').is_file() else None)
needs_q1n1 = unittest.skipUnless(bb.q1n1_tree(), 'no q1n1 checkout (set Q1N1_SP11_TREE)')
needs_clang = unittest.skipUnless(clang and sys.platform == 'darwin', 'clang on macOS is needed for the sanitizer harness')


def setUpModule():
    shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True)
    B.setUpModule()


def tearDownModule():
    shutil.rmtree(SCRATCH, ignore_errors=True)
    B.tearDownModule()


def noisy(rng, size, stretches=()):
    """`size` bytes with no zero byte except in the given half-open stretches."""
    data = bytearray(rng.randbytes(size).replace(b'\0', b'\1'))
    for start, end in stretches:
        data[start:end] = bytes(end - start)
    return bytes(data)


def regex_stretches(data, minimum):
    return [(m.start(), m.end()) for m in re.finditer(rb'\x00{%d,}' % minimum, data)]


def materialise(chunk):
    if chunk.get('data') is not None:
        return (chunk['kind'], chunk['dest'], len(chunk['data']), bytes(chunk['data']))
    if 'source' in chunk:
        return (chunk['kind'], chunk['dest'], chunk['size'], chunk['source'].read_at(chunk['offset'], chunk['size']))
    return (chunk['kind'], chunk['dest'], chunk['size'], None)


def merged(chunks):
    """Adjacent data pieces of one kind joined, so that a cut into pieces and a cut into one compare equal."""
    out = []
    for kind, dest, size, data in map(materialise, chunks):
        if out and data is not None and out[-1][3] is not None and out[-1][0] == kind and out[-1][1] + out[-1][2] == dest:
            out[-1] = (kind, out[-1][1], out[-1][2] + size, out[-1][3] + data)
        else:
            out.append((kind, dest, size, data))
    return out


def small_chunks(rng, base=0x880000000):
    """The five kinds a GUEST.DAT needs, with sizes that leave pads (not multiples of 64), a ZERO chunk and one more data chunk."""
    return [dict(kind=sb.KIND_KERNEL, dest=base, data=rng.randbytes(4096 + 16)), dict(kind=sb.KIND_TABLES, dest=base + 0x10000, data=rng.randbytes(0x4000)),
            dict(kind=sb.KIND_HANDOFF, dest=base + 0x20000, data=rng.randbytes(48)), dict(kind=sb.KIND_DEVICE_TREE, dest=base + 0x30000, data=rng.randbytes(96)),
            dict(kind=sb.KIND_PARAMS, dest=base + 0x40000, data=rng.randbytes(32)), dict(kind=sb.KIND_OTHER, dest=base + 0x50000, size=0x20000),
            dict(kind=sb.KIND_OTHER, dest=base + 0x70000, data=rng.randbytes(160))]


class Sources(unittest.TestCase):
    def test_zero_stretches_are_the_regex_matches_at_every_boundary(self):
        rng = random.Random(7)
        for minimum, window in ((64, 128), (64, 4096), (100, 400), (4096, 8192), (4096, 1 << 22), (0x10000, 1 << 20)):
            for case in range(6):
                size = rng.randrange(minimum * 3, minimum * 40)
                cuts = sorted(rng.sample(range(0, size - 1), 6))
                stretches = [(a, min(b, a + rng.choice((minimum - 1, minimum, minimum + 1, minimum * 2, minimum + rng.randrange(0, 5000))))) for a, b in zip(cuts[::2], cuts[1::2])]
                stretches = [(a, b) for a, b in stretches if b > a]
                if case == 1:
                    stretches = [(0, minimum), (size - minimum, size)]        # at both ends
                if case == 2:
                    stretches = [(0, size)]                                   # all zero
                if case == 3:
                    stretches = [(10, 10 + minimum), (10 + minimum + 1, 10 + 2 * minimum + 1)]     # one nonzero byte between
                data = noisy(rng, size, stretches)
                with self.subTest(minimum=minimum, window=window, case=case):
                    self.assertEqual(src.zero_stretches(src.BufferSource(data), minimum, window), regex_stretches(data, minimum))

    def test_the_scan_refuses_a_minimum_it_cannot_scan_exactly(self):
        with self.assertRaises(ValueError):
            src.zero_stretches(src.BufferSource(bytes(100)), 16)

    def test_pieces_slices_and_generated_read_the_same_bytes_as_a_join(self):
        rng = random.Random(3)
        parts = [rng.randbytes(n) for n in (5, 1, 4096, 77, 3000)]
        joined = b''.join(parts)
        pieces = src.Pieces([memoryview(parts[0]), parts[1], src.BufferSource(parts[2]), src.BufferSource(parts[3])[10:60], parts[4]])
        want = parts[0] + parts[1] + parts[2] + parts[3][10:60] + parts[4]
        self.assertEqual(len(pieces), len(want))
        for _ in range(200):
            a = rng.randrange(0, len(want))
            n = rng.randrange(0, len(want) - a + 1)
            self.assertEqual(pieces.read_at(a, n), want[a:a + n])
            self.assertEqual(pieces[a:a + n].read_at(0, n), want[a:a + n])
        self.assertEqual(src.joined([memoryview(joined)]), joined)
        with mock.patch.object(src, 'STREAM_RUN_MIN', 8):
            self.assertIsInstance(src.joined([memoryview(joined)]), src.Pieces)
        gen = src.Generated('t', 3 * MIB + 5, [(MIB - 7, MIB + 9)])
        self.assertEqual(gen.read_at(0, 3 * MIB + 5), b''.join(d for _, d in gen.windows(MIB // 3)))
        self.assertEqual(gen.read_at(MIB - 7, 16), bytes(16))
        self.assertNotEqual(gen.read_at(MIB + 9, 16), bytes(16))
        with self.assertRaises(ValueError):
            gen.read_at(3 * MIB, 6)

    def test_split_run_is_split_zero_when_nothing_is_dropped(self):
        rng = random.Random(11)
        for minimum in (64, 256, 4096):
            for case in range(8):
                size = rng.randrange(minimum * 4, minimum * 60) // 16 * 16
                cuts = sorted(rng.sample(range(0, size - 1), 8))
                data = noisy(rng, size, [(a, b) for a, b in zip(cuts[::2], cuts[1::2]) if b - a > 3])
                start = 0x880000000 + 16 * rng.randrange(0, 100)
                with mock.patch.object(ch, 'ZERO_MIN', minimum):
                    want = ch.split_zero(sb.KIND_OTHER, start, data)
                    got = src.split_run(sb.KIND_OTHER, start, src.BufferSource(data), minimum, 1024, size)
                with self.subTest(minimum=minimum, case=case):
                    self.assertEqual(merged(got), merged(want))
                    self.assertEqual([c['size'] if 'size' in c else len(c['data']) for c in got if c.get('data') is None and 'source' not in c],
                                     [c['size'] for c in want if c.get('data') is None])
        zero = src.split_run(sb.KIND_OTHER, 0x1000, src.BufferSource(bytes(48)), 64, 5)
        self.assertEqual(zero, [dict(kind=sb.KIND_OTHER, dest=0x1000, size=48)])

    def test_the_budget_keeps_the_longest_stretches_and_refuses_what_cannot_fit(self):
        minimum = 64
        stretches = [(96, 96 + 64), (496, 496 + 640), (2000, 2000 + 128), (3008, 3008 + 320), (4000, 4000 + 64)]       # on 16-byte boundaries, so that narrowing keeps their lengths
        data = noisy(random.Random(5), 4096 + 64, stretches)
        src_ = src.BufferSource(data)
        for budget in (1, 3, 5, 7, 9, 11, 13):
            chunks = src.split_run(sb.KIND_OTHER, 0x10000, src_, minimum, budget, 1 << 20)
            with self.subTest(budget=budget):
                self.assertLessEqual(len(chunks), budget)
                zeros = [c for c in chunks if 'source' not in c]
                self.assertEqual(len(zeros), min((budget - 1) // 2, len(stretches)))
                kept = sorted((c['size'] for c in zeros), reverse=True)
                longest = sorted((b - a for a, b in stretches), reverse=True)[:len(zeros)]
                self.assertEqual(kept, longest)
                self.assertEqual(b''.join(materialise(c)[3] or bytes(c['size']) for c in chunks), data)
        twins = src.BufferSource(noisy(random.Random(6), 4096, [(512, 640), (2048, 2176)]))       # equal stretches, room for one: the lower address is kept (a pinned digest depends on it)
        kept = [(c['dest'] - 0x10000, c['size']) for c in src.split_run(sb.KIND_OTHER, 0x10000, twins, minimum, 4, 1 << 20) if 'source' not in c]
        self.assertEqual(kept, [(512, 128)])
        with self.assertRaises(src.TableFull):
            src.split_run(sb.KIND_OTHER, 0x10000, src_, minimum, 0)
        pieces = src.split_run(sb.KIND_OTHER, 0x10000, src.BufferSource(noisy(random.Random(1), 1000 * 16)), minimum, 1024, 16 * 100)
        self.assertEqual([c['size'] for c in pieces], [1600] * 10)
        self.assertEqual([c['offset'] for c in pieces], [1600 * i for i in range(10)])


class Writer(unittest.TestCase):
    def path(self, name):
        return SCRATCH / name

    def test_the_streamed_file_is_the_packed_file(self):
        rng = random.Random(21)
        base, size = 0x880000000, 0x400000
        chunks = small_chunks(rng)
        want = sb.pack_guest(base, size, chunks)
        written = root.write_guest(self.path('a.dat'), base, size, chunks)
        self.assertEqual(self.path('a.dat').read_bytes(), want)
        self.assertEqual((written['file_size'], written['count']), (len(want), len(chunks)))
        self.assertEqual(written['index_sha256'], sb.get(want, sb.GUEST_LAYOUT, 'index_sha256').hex())
        # the same guest with the last data chunk as a window of a Source
        blob = rng.randbytes(1 << 17)
        as_data = chunks[:-1] + [dict(kind=sb.KIND_OTHER, dest=base + 0x70000, data=blob[0x3000:0x13000])]
        as_source = chunks[:-1] + [dict(kind=sb.KIND_OTHER, dest=base + 0x70000, size=0x10000, source=src.BufferSource(blob), offset=0x3000)]
        root.write_guest(self.path('b.dat'), base, size, as_source)
        self.assertEqual(self.path('b.dat').read_bytes(), sb.pack_guest(base, size, as_data))
        # a 64 KiB block with one non-zero byte (first, last, and the last byte of a short last block) is data, not a hole
        lone = bytearray(3 * 65536 + 96)
        lone[0] = lone[2 * 65536 - 1] = lone[-1] = 1
        holes = chunks + [dict(kind=sb.KIND_OTHER, dest=base + 0x80000, data=bytes(lone))]
        root.write_guest(self.path('e.dat'), base, size, holes)
        self.assertEqual(self.path('e.dat').read_bytes(), sb.pack_guest(base, size, holes))

    def test_pack_guest_refuses_a_chunk_that_is_a_source(self):
        chunk = dict(kind=sb.KIND_OTHER, dest=0x1000, size=16, source=src.BufferSource(bytes(16)), offset=0)
        with self.assertRaises(ValueError):
            sb.pack_guest(0x1000, 0x1000, [chunk])

    def test_every_zero_window_is_a_hole_and_the_length_is_right(self):
        rng = random.Random(4)
        base = 0x880000000
        big = src.Generated('holes', 40 * MIB, [(0, 40 * MIB - 4096)])       # zero but for its last page
        chunks = small_chunks(rng)[:-1] + [dict(kind=sb.KIND_OTHER, dest=base + 0x100000, size=40 * MIB, source=big, offset=0)]
        written = root.write_guest(self.path('c.dat'), base, 64 * MIB, chunks)
        st = os.stat(self.path('c.dat'))
        self.assertEqual(st.st_size, written['file_size'])
        self.assertLess(st.st_blocks * 512, 4 * MIB, 'a window of zeros must not be written')
        checked = root.verify_guest(self.path('c.dat'), base, 64 * MIB, written)
        self.assertEqual(checked['sha256'], sha(self.path('c.dat').read_bytes()))

    def test_verify_names_what_a_damaged_file_breaks(self):
        rng = random.Random(8)
        base, size = 0x880000000, 0x400000
        chunks = small_chunks(rng)
        written = root.write_guest(self.path('d.dat'), base, size, chunks)
        good = self.path('d.dat').read_bytes()
        table_end = sb.GUEST_HEAD_BYTES + sb.CHUNK_BYTES * written['count']
        pad = next(i for i in range(table_end, len(good)) if good[i - 1:i] == b'\0' and (i % 64) and not good[i])           # a pad byte
        entry3_digest = sb.GUEST_HEAD_BYTES + sb.CHUNK_BYTES * 3 + 32
        tail = sb.unpack_guest(good)[1][-1]         # the last chunk: a loop that stops one entry short never reads it
        flips = [('CHUNK_DIGEST', table_end + 200), ('INDEX_DIGEST', entry3_digest), ('PAD_NONZERO', pad), ('GUEST_SIZE', None), ('CHUNK_DIGEST', tail['file_offset'] + tail['size'] - 1)]
        for n, (name, at) in enumerate(flips):
            damaged = bytearray(good)
            if at is None:
                damaged += b'\0'
            else:
                damaged[at] ^= 0x40
            path = self.path(f'd-{n}-{name}.dat')
            path.write_bytes(damaged)
            with self.subTest(n), self.assertRaises(ch.Refused) as caught:
                root.verify_guest(path, base, size, written)
            self.assertEqual(caught.exception.name, name)
        short = self.path('d-short.dat')
        short.write_bytes(good[:-1])
        with self.assertRaises(ch.Refused) as caught:
            root.verify_guest(short, base, size, written)
        self.assertEqual(caught.exception.name, 'GUEST_TRUNCATED')

    def test_fileview_and_the_windowed_digest_agree_with_hashlib(self):
        data = random.Random(2).randbytes(3 * MIB + 17)
        path = self.path('v.bin')
        path.write_bytes(data)
        with root.FileView(path) as view:
            self.assertEqual((len(view), view[5:9], view[len(data) - 3:], view[len(data):], view[-4:]), (len(data), data[5:9], data[-3:], b'', data[-4:]))
            self.assertEqual(sb.sha256_range(view), hashlib.sha256(data).digest())
            self.assertEqual(sb.sha256_range(view, 100, 2 * MIB), hashlib.sha256(data[100:100 + 2 * MIB]).digest())
            with self.assertRaises(TypeError):
                view[3]
        with mock.patch.object(sb, 'WINDOW', 1000):
            self.assertEqual(sb.sha256_range(data), hashlib.sha256(data).digest())
            self.assertEqual(sb.sha256_range(bytearray(data), 7, 0), hashlib.sha256(b'').digest())


class Limits(unittest.TestCase):
    def test_the_default_is_256_mib_and_the_context_is_2_gib(self):
        self.assertEqual((sb.file_max(), sb.FILE_MAX, sb.FILE_MAX_BIG, sb.CONSTANTS['SP11SB_FILE_MAX']), (256 * MIB, 256 * MIB, 2 * GIB, 256 * MIB))
        path = lambda role: b'\\SP11\\' + [b'SNAPMON.BIN', b'SNAPREP.BIN', b'GUEST.DAT'][role] + bytes(256)      # noqa: E731
        for big, size, want in ((False, 256 * MIB, 0), (False, 256 * MIB + 1, sb.E.FILE_SIZE), (True, 256 * MIB + 1, 0), (True, 2 * GIB, 0), (True, 2 * GIB + 1, sb.E.FILE_SIZE),
                                (True, 4 * GIB, sb.E.FILE_SIZE), (True, 0, sb.E.FILE_SIZE), (True, (1 << 64) - 1, sb.E.FILE_BASE)):
            with self.subTest(big=big, size=size), sb.bigfile(big):
                self.assertEqual(sb.check_file(path(2), 0x1000, size, sb.ROLE_GUEST), want)
        self.assertEqual(sb.file_max(), 256 * MIB, 'the context restores what it found')
        with sb.bigfile(True):
            with sb.bigfile(False):
                self.assertEqual(sb.file_max(), 256 * MIB)
            self.assertEqual(sb.file_max(), 2 * GIB)
        with self.assertRaises(RuntimeError), sb.bigfile(True):
            raise RuntimeError
        self.assertEqual(sb.file_max(), 256 * MIB)

    def test_a_guest_head_at_2_gib_passes_the_size_rule_only_in_the_context(self):
        head = bytearray(sb.GUEST_HEAD_BYTES)
        for name, value in (('magic', b'SNAPBGST'), ('version', 1), ('head_size', 128), ('entry_size', 64), ('entry_count', 1), ('table_offset', 128), ('data_offset', 192),
                            ('file_size', 2 * GIB), ('load_base', 0x880000000), ('load_size', 0x180000000), ('index_sha256', bytes(32))):
            sb.put(head, sb.GUEST_LAYOUT, name, value)
        bind = (0x880000000, 0x180000000, 2 * GIB, 1, bytes(32))
        data = bytes(head) + bytes(64)          # one table entry of zeros: it passes the size rules and meets CHUNK_KIND
        self.assertEqual(sb._guest_check(data, 2 * GIB, bind), sb.E.GUEST_SIZE)
        with sb.bigfile():
            self.assertEqual(sb._guest_check(data, 2 * GIB, bind), sb.E.CHUNK_KIND)
            sb.put(head, sb.GUEST_LAYOUT, 'file_size', 2 * GIB + 16)
            self.assertEqual(sb._guest_check(bytes(head) + bytes(64), 2 * GIB + 16, (0x880000000, 0x180000000, 2 * GIB + 16, 1, bytes(32))), sb.E.GUEST_SIZE)

    def test_read_regular_takes_the_limit_of_the_context(self):
        path = SCRATCH / 'sparse.bin'
        with open(path, 'wb') as f:
            f.truncate(256 * MIB + 1)
        self.assertRaises(ch.Refused, bb.read_regular, path, 'x')
        with self.assertRaises(ch.Refused) as caught:
            bb.read_regular(path, 'x')
        self.assertEqual(caught.exception.name, 'FILE_SIZE')
        path.unlink()


class Flatten(unittest.TestCase):
    def synthetic(self):
        xb = bb.xnuboot()
        place = sb.placement(B.BANK, 1024 * MIB, 192 * MIB)
        bb.place_layout(xb, place)
        return xb, ch.synthetic_built(xb, 24 * MIB, 2 * MIB, 4 * MIB, 'big-test', 60), ch.layout_of(xb)

    def test_a_lazy_run_is_cut_as_a_joined_run_is(self):
        xb, built, lay = self.synthetic()
        want = ch.chunks_from_built(built, lay)
        with mock.patch.object(src, 'STREAM_RUN_MIN', 1 * MIB):
            got = ch.chunks_from_built(built, lay)
        self.assertTrue(any('source' in c for c in got), 'the image is long enough to stay a Source')
        self.assertEqual(merged(got), merged(want))
        self.assertEqual([c['kind'] for c in got if c.get('data') is not None and 'source' not in c and c['kind'] in (sb.KIND_PARAMS, sb.KIND_DEVICE_TREE)],
                         [c['kind'] for c in want if c['kind'] in (sb.KIND_PARAMS, sb.KIND_DEVICE_TREE)])

    def test_a_mapped_root_is_never_copied_and_is_cut_under_the_table(self):
        xb, built, lay = self.synthetic()
        path = SCRATCH / 'root.img'
        rng = random.Random(9)
        stretches = [(m * 3 * MIB, m * 3 * MIB + 70000 + 100 * m) for m in range(1, 10)] + [(40 * MIB - 100000, 40 * MIB)]
        data = noisy(rng, 40 * MIB, stretches)
        path.write_bytes(data)
        disk_pa = (max(hi for _, hi, _ in lay.classes) + 0x3FFF) & ~0x3FFF
        with open(path, 'rb') as f:
            mapped = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        built['chunks'][disk_pa] = mapped
        with mock.patch.object(src, 'STREAM_RUN_MIN', 30 * MIB):         # the 24 MiB image stays joined: only the root is a Source
            chunks = ch.chunks_from_built(built, lay)
            carried = [c for c in chunks if c['dest'] >= disk_pa]
            others = len(chunks) - len(carried)
            self.assertEqual(sum(root.size_of(c) for c in carried), len(data))
            self.assertEqual(sum(1 for c in carried if 'source' not in c), 10)
            self.assertEqual(b''.join(materialise(c)[3] or bytes(c['size']) for c in carried), data)
            with mock.patch.object(sb, 'CHUNKS_MAX', others + 5):
                fewer = ch.chunks_from_built(built, lay)
            # two stretches fit in 5 entries (the longest, which ends the run, and the next: 2 ZERO chunks and 2 pieces); a third needs 6
            self.assertEqual(len([c for c in fewer if c['dest'] >= disk_pa]), 4)
            with mock.patch.object(sb, 'CHUNKS_MAX', others):
                with self.assertRaises(ch.Refused) as caught:
                    ch.chunks_from_built(built, lay)
            self.assertEqual(caught.exception.name, 'ROOT_TABLE_FULL')
        with self.assertRaises(ch.Refused) as caught:       # the packer of bytes must not read a window of a Source as a ZERO chunk
            ch.guest_from_chunks(sb.placement(B.BANK, 1024 * MIB, 192 * MIB), chunks)
        self.assertEqual(caught.exception.name, 'BUILT_SHAPE')

    def test_a_parameters_or_tree_chunk_cannot_be_lazy(self):
        xb, built, lay = self.synthetic()
        with mock.patch.object(src, 'STREAM_RUN_MIN', 1):
            with self.assertRaises(ch.Refused) as caught:
                ch.chunks_from_built(built, lay)
        self.assertEqual(caught.exception.name, 'BUILT_SHAPE')


class RootGuest(B.Case):
    def run_cli(self, kind='synthetic', env=None, **over):
        out = SCRATCH / 'rg' / over.pop('name', 'a')
        o = dict(facts=B.FIX['facts'], base=hex(B.BANK), guest_mib=6144, time_box_s=300, dump=B.FIX['dumps'], out=out)
        if kind == 'synthetic':
            o.update(root_mib=24, image_mib=8, tables_mib=1, sptm_data_mib=4)
        o.update(over)
        argv = ['root-guest', kind] + [x for k, v in o.items() for item in (v if isinstance(v, list) else [v]) if item is not None for x in ('--' + k.replace('_', '-'), str(item))]
        with mock.patch.dict(os.environ, {root.GATE_ENV: '1'} if env is None else env, clear=False):
            return (*B.cli(argv), Path(out))

    def test_the_gate_is_off_by_default_and_nothing_is_written(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(root.GATE_ENV, None)
            code, out, err, path = self.run_cli(env={})
        self.assertEqual((code, B.refusal(out)), (1, 'REFUSED=ROOT_GATE_OFF'))
        self.assertFalse(path.exists())
        code, out, err, path = self.run_cli(env={root.GATE_ENV: '2'})
        self.assertEqual(B.refusal(out), 'REFUSED=ROOT_GATE_OFF')

    def test_a_build_writes_a_guest_and_a_report_and_nothing_that_could_be_staged(self):
        code, out, err, path = self.run_cli(name='ok')
        self.assertEqual(code, 0, err)
        self.assertEqual(sorted(p.name for p in path.iterdir()), ['GUEST.DAT', root.REPORT])
        report = json.loads((path / root.REPORT).read_text())
        self.assertEqual((report['hardware'], report['evidence'], report['kind']), ('NOT RUN', 'EXPERIMENTAL', 'synthetic'))
        self.assertIn('no PIN.DAT', report['not_a_bundle'])
        self.assertEqual(report['guest']['sha256'], sha((path / 'GUEST.DAT').read_bytes()))
        self.assertEqual(report['guest']['file_size'], (path / 'GUEST.DAT').stat().st_size)
        self.assertEqual((report['boot_words'], report['runner']['chunk_cap_kib'], report['root']['root_bytes']), ('cpu_boot_timeout_secs=360 wdt=-1', 8192, 24 * MIB))
        self.assertEqual(report['runner']['operations'], -(-report['guest']['file_size'] // (8 * MIB)))
        self.assertEqual(int(report['placement']['place_base'], 16) + int(report['placement']['place_size'], 16), 0xA28000000)
        self.assertNotIn(str(ROOT), json.dumps(report))
        self.assertIn('HARDWARE=NOT RUN', out)
        self.assertIn('NOT A BUNDLE', out)
        with root.FileView(path / 'GUEST.DAT') as view, sb.bigfile():
            head, table = sb.unpack_guest(view)
            self.assertEqual(sb._guest_check(view, len(view), (int(report['placement']['guest_base'], 16), int(report['placement']['guest_size'], 16), len(view), len(table),
                                                                head['index_sha256'])), 0)
            carried = [c for c in table if c['kind'] == sb.KIND_OTHER]
            self.assertEqual(sum(c['size'] for c in carried), 24 * MIB)
        again = self.run_cli(name='again')
        self.assertEqual(again[0], 0)
        self.assertEqual(sha((again[3] / 'GUEST.DAT').read_bytes()), report['guest']['sha256'], 'deterministic')

    def test_the_refusals_come_before_anything_is_built(self):
        for name, over, expected in (('box_over', dict(time_box_s=root.BOX_MAX_S + 1), 'OPTIONS_BAD'), ('box_zero', dict(time_box_s=0), 'OPTIONS_BAD'),
                                     ('wait_equals_box', dict(cpu_boot_timeout_s=300), 'ROOT_BOOT_ARG'), ('wait_under', dict(cpu_boot_timeout_s=10), 'ROOT_BOOT_ARG'),
                                     ('top', dict(guest_mib=30720 - 1024), 'ROOT_PLACEMENT'), ('outside', dict(out='/nonexistent-root-guest-dir'), 'OUT_DIR_REFUSED')):
            with self.subTest(name):
                code, out, err, path = self.run_cli(name=name, **over)
                self.assertEqual(B.refusal(out), f'REFUSED={expected}', err)
                self.assertFalse(path.exists() and any(path.iterdir()))
        with mock.patch.object(root, 'FREE_AFTER_BYTES', 1 << 60):
            self.assertEqual(B.refusal(self.run_cli(name='room')[1]), 'REFUSED=ROOT_NO_ROOM')
        with mock.patch.object(sb, 'CHUNKS_MAX', 5), mock.patch.object(src, 'STREAM_RUN_MIN', 1 * MIB):
            self.assertEqual(B.refusal(self.run_cli(name='full')[1]), 'REFUSED=ROOT_TABLE_FULL')
        code, out, err, path = self.run_cli(name='real-no-root', kind='real', kernelcache='/nonexistent', ramdisk='/nonexistent', root_hash='/nonexistent', trust_cache='/nonexistent')
        self.assertEqual(B.refusal(out), 'REFUSED=ROOT_INPUT_BAD', err)

    def test_the_boot_words_are_over_the_box_and_turn_the_kernel_watchdog_off(self):
        self.assertEqual(root.boot_arg(300, 360), 'cpu_boot_timeout_secs=360 wdt=-1')
        for box, wait in ((300, 300), (300, 299), (300, 86401), (0, 60), (root.BOX_MAX_S + 1, root.BOX_MAX_S + 100)):
            with self.subTest(box=box, wait=wait), self.assertRaises(ch.Refused):
                root.boot_arg(box, wait)
        self.assertEqual(root.boot_arg(root.BOX_MAX_S, root.BOX_MAX_S + 1), f'cpu_boot_timeout_secs={root.BOX_MAX_S + 1} wdt=-1')

    def test_the_device_tree_additions_for_the_root_are_the_loaders(self):
        import sp11_stick_bundle_profile as gpb
        uuid = 'A9037730-2821-4F3A-90DC-91BA521BB080'
        added = gpb.root_additions(dict(extents=(0x88ED00000, 0x6F000000), boot_uuid=uuid, payload=bytes(range(208))))
        chosen = added['/device-tree/chosen']
        self.assertEqual(sorted(chosen), ['apfs-preboot-uuid', 'boot-ramdmg-extents', 'boot-ramdmg-size', 'boot-uuid', 'protected-data-access', 'sepfw-load-at-boot'])
        self.assertEqual((chosen['boot-ramdmg-size'], chosen['boot-ramdmg-extents']), ((0x6F000000).to_bytes(8, 'little'), (0x88ED00000).to_bytes(8, 'little') + (0x6F000000).to_bytes(8, 'little')))
        self.assertEqual((chosen['boot-uuid'], chosen['apfs-preboot-uuid'], chosen['protected-data-access'], chosen['sepfw-load-at-boot']), (uuid.encode() + b'\0', uuid.encode() + b'\0', bytes(4), bytes(4)))
        self.assertEqual(added['/device-tree/chosen/secure-boot-hashes'], {'system-volume-auth-blob': bytes(range(208))})
        loader, policy = (ROOT / 'probes/xnuboot.py').read_text(), (ROOT / 'probes/ramdisk.py').read_text()
        for name in ('boot-ramdmg-size', 'boot-ramdmg-extents', 'boot-uuid', 'apfs-preboot-uuid'):
            self.assertIn(f"'{name}'", loader)
        for name in ('protected-data-access', 'sepfw-load-at-boot'):
            self.assertIn(f"'{name}'", policy)

    def test_the_rtc_is_seeded_in_parameters_or_refused(self):
        fake = mock.Mock()
        fake.seed_rtc_models = lambda models, epoch: (*models, epoch)
        with mock.patch.dict(sys.modules, {'devicemodel': fake}):
            built = ch.seed_rtc({'device_models': ('m',), 'chunks': {}}, 1_700_000_000_000_000)
        self.assertEqual(built['device_models'], ('m', 1_700_000_000_000_000))
        import devicemodel
        for models, epoch in (((), 5), ((), 0)):
            with self.subTest(epoch=epoch), self.assertRaises(ch.Refused) as caught:
                ch.seed_rtc({'device_models': models}, epoch)
            self.assertEqual(caught.exception.name, 'ROOT_RTC')
        self.assertTrue(devicemodel.seed_rtc_models, 'the real function is the one that refuses')
        self.assertIn('ROOT_RTC', ch.KNOWN_REFUSALS)
        self.assertIsNone(root.seed_rtc_failure({'device_models': ()}))
        unseeded = devicemodel.DeviceModel('/device-tree/arm-io/nub-spmi0', devicemodel.SPMI_PMU_RTC_V1, 0x308700000, 0x18000, (0x14000, 0x4000, 487, 0, devicemodel.PMU_RTC_CONTRACT))
        other = devicemodel.DeviceModel('/device-tree/arm-io/nub-spmi1', devicemodel.SPMI_GEN3_EMPTY_V1, 0x308800000, 0x18000, (0x14000, 0x4000, 488, 256, 256))
        self.assertIn('unseeded RTC', root.seed_rtc_failure({'device_models': (other, unseeded)}))
        seeded = ch.seed_rtc({'device_models': (other, unseeded), 'chunks': {1: b'x'}}, 1_700_000_000_000_000)
        self.assertEqual((seeded['device_models'][0], seeded['device_models'][1].config), (other, (0x14000, 0x4000, 487, 1_700_000_000_000_000, devicemodel.PMU_RTC_CONTRACT)))
        self.assertEqual(seeded['chunks'], {1: b'x'})
        self.assertIsNone(root.seed_rtc_failure(seeded))
        for models, epoch in (((other, unseeded), devicemodel.PMU_RTC_LIMIT_US + 1), (seeded['device_models'], 5), ((other,), 5)):       # out of range, a clock that is seeded already, no clock
            with self.subTest(epoch=epoch), self.assertRaises(ch.Refused) as caught:
                ch.seed_rtc({'device_models': models}, epoch)
            self.assertEqual(caught.exception.name, 'ROOT_RTC')


class Builder(B.Case):
    @needs_q1n1
    def test_the_manifest_tool_takes_the_chunk_cap_or_the_tree_is_refused(self):
        tool = bb.q1n1_manifest_tool(root.CHUNK_CAP_KIB)
        self.assertEqual(root.CHUNK_CAP_KIB, 8192)
        files = []
        for name, size in (('SNAPMON.BIN', 4096), ('SNAPREP.BIN', 4096), ('GUEST.DAT', 300 * MIB), ('PIN.DAT', 1024), ('REPORT.DAT', 528384)):
            path = SCRATCH / 'man' / name
            path.parent.mkdir(exist_ok=True)
            with open(path, 'wb') as f:
                f.truncate(size)
            files.append(path)
        manifest, listed = tool.build(files, chunk_cap_kib=root.CHUNK_CAP_KIB)
        self.assertEqual([e[1] for e in listed], [4096, 4096, 300 * MIB, 1024, 528384])
        with self.assertRaises(ValueError):
            tool.build(files)                                   # the default cap of 1 MiB is the 256 MiB limit
        old = SCRATCH / 'old-tree'
        (old / 'tools').mkdir(parents=True, exist_ok=True)
        (old / 'tools/preload.py').write_text((bb.q1n1_tree() / 'tools/preload.py').read_text())
        (old / 'tools/sp11_payload_manifest.py').write_text('def build(paths):\n    return b"", []\n')
        with mock.patch.dict(os.environ, {'Q1N1_SP11_TREE': str(old)}):
            self.assertIsNone(bb.q1n1_manifest_tool().build([]) and None)
            with self.assertRaises(ch.Refused) as caught:
                bb.q1n1_manifest_tool(root.CHUNK_CAP_KIB)
        self.assertEqual(caught.exception.name, 'Q1N1_TREE_ABSENT')

    @needs_q1n1
    def test_bigfile_calls_the_tool_with_the_cap_and_the_default_does_not(self):
        calls = []
        real_tool = bb.q1n1_manifest_tool

        def spy(cap=None):
            tool = real_tool(cap)
            return mock.Mock(build=lambda paths, **kw: (calls.append(kw), tool.build(paths, **kw))[1])
        with mock.patch.object(bb, 'q1n1_manifest_tool', spy):
            plain = B.build_cli('plain')
            big = B.build_cli('big', extra=['--bigfile'])
        self.assertEqual((plain[0], big[0]), (0, 0), (plain[2], big[2]))
        self.assertEqual(calls, [{}, {'chunk_cap_kib': 8192}])
        self.assertEqual(B.tree_digests(plain[3] / 'bundle'), B.tree_digests(big[3] / 'bundle'), 'the manifest does not carry the cap: the same bundle')

    def test_a_monitor_or_reporter_over_8_mib_is_refused_also_with_bigfile(self):
        path = SCRATCH / 'monitor-over-8-mib.bin'
        with open(path, 'wb') as f:
            f.truncate(8 * MIB + 1)
        for key in ('monitor', 'reporter'):
            for flag in ((), ('--bigfile',)):
                with self.subTest(key=key, flag=flag):
                    self.refused('FILE_SIZE', B.cli(['authorisation-template', 'synthetic', *B.opts(**{key: path}), *flag]))

    def test_place_check_keeps_the_top_for_the_runners_files(self):
        base = ['place-check', '--base', hex(B.BANK), '--facts', B.FIX['facts'], *[x for d in B.FIX['dumps'] for x in ('--dump', d)]]
        code, out, err = B.cli([*base, '--guest-mib', '6144', '--root-carry'])
        self.assertEqual(code, 0, err)
        self.assertIn('RESULT maps=3/3', out)
        self.assertIn('guest_mib=6144', out)
        code, out, err = B.cli([*base, '--guest-mib', '28672', '--root-carry'])
        self.assertEqual(B.refusal(out), 'REFUSED=ROOT_PLACEMENT')
        code, out, err = B.cli([*base, '--guest-mib', '28672'])
        self.assertEqual(code, 0, err)
        code, out, err = B.cli([*base, '--guest-mib', '6144', '--root-carry'])
        top = [line for line in out.splitlines() if line.startswith('PROPOSAL bank')][0]
        most = int(re.search(r'guest_mib_max=(\d+)', top).group(1))
        self.assertLessEqual(0x880000000 + most * MIB, (1 << 36) - root.DATA_TOP_BYTES)
        self.assertGreater(most, 6144)


def container(path, total, base, load):
    """A sparse GUEST.DAT of exactly `total` bytes: five small chunks and one data chunk to the end of the file, zero but for three marked pages."""
    rng = random.Random(31)
    small = small_chunks(rng, base)[:5]
    expect = sb.GUEST_HEAD_BYTES + sb.CHUNK_BYTES * (len(small) + 1)
    for c in small:
        expect = ((expect + 63) & ~63) + len(c['data'])
    offset = (expect + 63) & ~63
    size = total - offset
    patches = {4096: rng.randbytes(4096), size // 2 // 4096 * 4096: rng.randbytes(4096), size - 4096: rng.randbytes(4096)}

    class Marked(src.Source):
        def __len__(self):
            return size

        def read_at(self, at_offset, count):
            data = bytearray(count)
            for at, blob in patches.items():
                lo, hi = max(at, at_offset), min(at + len(blob), at_offset + count)
                if lo < hi:
                    data[lo - at_offset:hi - at_offset] = blob[lo - at:hi - at]
            return bytes(data)
    written = root.write_guest(path, base, load, small + [dict(kind=sb.KIND_OTHER, dest=base + 0x100000, size=size, source=Marked(), offset=0)])
    assert written['file_size'] == total
    return written


class BigC(unittest.TestCase):
    """A sparse container of exactly 2 GiB through the C header, built with the gate on and off."""
    pin = None

    @classmethod
    @needs_clang
    def setUpClass(cls):
        cls.exe = {}
        for name, flags in (('on', ['-DSNAP_SP11_STICK_MONITOR=1', '-DSNAP_SP11_STICK_ROOT_CARRY=1']), ('off', ['-DSNAP_SP11_STICK_MONITOR=1']), ('plain', []),
                            ('len31', ['-DSNAP_SP11_STICK_MONITOR=1', '-DSNAP_SP11_STICK_ROOT_CARRY=1', '-DBIG_SEED_LEN31']),
                            ('touch', ['-DSNAP_SP11_STICK_MONITOR=1', '-DSNAP_SP11_STICK_ROOT_CARRY=1', '-DBIG_SEED_TOUCH'])):
            exe = SCRATCH / f'big-{name}'
            done = subprocess.run([clang, '-std=c11', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined,implicit-conversion', '-fno-sanitize-recover=undefined',
                                   *flags, str(HERE / 'test-sp11-stick-bundle-big.c'), '-o', str(exe)], capture_output=True, text=True)
            assert done.returncode == 0, done.stderr
            cls.exe[name] = exe
        cls.base, cls.load = 0x880000000, 0x180000000
        cls.path = SCRATCH / 'big.dat'
        cls.written = container(cls.path, 2 * GIB, cls.base, cls.load)
        cls.small_path = SCRATCH / 'small.dat'          # 600 MiB: what the seeded fault that maps the data in is measured on, so that no run of this file holds more than 1 GiB
        cls.small_written = container(cls.small_path, 600 * MIB, cls.base, cls.load)

    def run_c(self, name, path=None, written=None, policy=3):
        written = written or self.written
        path = path or self.path
        with root.FileView(path) as view:
            file_digest = sb.sha256_range(view).hex() if policy & 2 else '00' * 32
        done = subprocess.run([str(self.exe[name]), 'guest', str(path), hex(self.base), hex(self.load), hex(written['file_size']), str(written['count']), written['index_sha256'],
                               file_digest, str(policy)], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr[-2000:])
        words = done.stdout.split()
        return int(words[1]), words[2], int(words[3]), int(words[4])

    @needs_clang
    def test_the_file_is_2_gib_exactly_and_a_hole_for_the_most_of_it(self):
        self.assertEqual(self.written['file_size'], 2 * GIB)
        self.assertEqual(os.stat(self.path).st_size, 2 * GIB)
        self.assertLess(os.stat(self.path).st_blocks * 512, 64 * MIB)

    @needs_clang
    def test_the_header_with_the_gate_checks_digests_and_structure_in_a_window_at_a_time(self):
        code, name, bad, rss = self.run_c('on')
        self.assertEqual((code, name), (0, 'OK'))
        self.assertLess(rss, 256 * MIB, 'the harness reads the 2 GiB through pread and maps in only the head, the table and the pads')
        py_code = sb._guest_check
        with root.FileView(self.path) as view, sb.bigfile():
            head, table = sb.unpack_guest(view)
            bind = (self.base, self.load, self.written['file_size'], len(table), head['index_sha256'])
            self.assertEqual(py_code(view, len(view), bind), 0)
            for i, c in enumerate(table):
                if not c['flags'] & sb.CHUNK_ZERO:
                    self.assertEqual(sb.sha256_range(view, c['file_offset'], c['size']), c['sha256'], i)

    @needs_clang
    def test_the_header_without_the_gate_refuses_the_same_file(self):
        for name in ('off', 'plain'):
            with self.subTest(name):
                code, got, bad, rss = self.run_c(name, policy=0)
                self.assertEqual((code, got), (sb.E.GUEST_SIZE, 'GUEST_SIZE'))

    @needs_clang
    def test_one_damaged_byte_in_the_middle_of_the_file_is_found_by_the_chunk_digest(self):
        with open(self.path, 'r+b') as f:
            f.seek(GIB + 100)
            held = f.read(1)
            f.seek(GIB + 100)
            f.write(bytes([held[0] ^ 1]))
        try:
            code, name, bad, rss = self.run_c('on', policy=1)
            self.assertEqual((code, name, bad), (sb.E.CHUNK_DIGEST, 'CHUNK_DIGEST', 5))
            with root.FileView(self.path) as view, sb.bigfile():
                self.assertEqual(sb.check_digests(view, bytes(1024), sb.HASH_CHUNKS), (sb.E.CHUNK_DIGEST, 5))
        finally:
            with open(self.path, 'r+b') as f:
                f.seek(GIB + 100)
                f.write(held)
        self.assertEqual(self.run_c('on', policy=1)[:2], (0, 'OK'))

    @needs_clang
    def test_a_file_longer_or_shorter_than_it_says_is_refused_at_the_limit(self):
        size = self.written['file_size']
        with open(self.path, 'r+b') as f:
            f.seek(size - 1)
            last = f.read(1)
        try:
            for delta, expected in ((1, 'GUEST_SIZE'), (-1, 'GUEST_TRUNCATED')):
                with open(self.path, 'r+b') as f:
                    f.truncate(size + delta)
                with self.subTest(delta):
                    self.assertEqual(self.run_c('on', policy=0)[1], expected)
        finally:
            with open(self.path, 'r+b') as f:
                f.truncate(size)
                f.seek(size - 1)
                f.write(last)
        self.assertEqual(self.run_c('on', policy=1)[:2], (0, 'OK'))

    @needs_clang
    def test_files_check_at_the_limit_from_both_sides_and_with_the_gate_off(self):
        def file_code(name, role, base, size):
            done = subprocess.run([str(self.exe[name]), 'file', str(role), hex(base), hex(size)], capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
            return int(done.stdout.split()[1])
        for role in (0, 1, 2):
            for size, on, off in ((1, 0, 0), (256 * MIB, 0, 0), (256 * MIB + 1, 0, sb.E.FILE_SIZE), (GIB, 0, sb.E.FILE_SIZE), (2 * GIB - 1, 0, sb.E.FILE_SIZE), (2 * GIB, 0, sb.E.FILE_SIZE),
                              (2 * GIB + 1, sb.E.FILE_SIZE, sb.E.FILE_SIZE), (4 * GIB, sb.E.FILE_SIZE, sb.E.FILE_SIZE), ((1 << 64) - 1, sb.E.FILE_BASE, sb.E.FILE_BASE)):
                with self.subTest(role=role, size=size):
                    self.assertEqual(file_code('on', role, 0x1000, size), on)
                    self.assertEqual(file_code('off', role, 0x1000, size), off)
                    with sb.bigfile():
                        self.assertEqual(sb.check_file(b'\\SP11\\' + [b'SNAPMON.BIN', b'SNAPREP.BIN', b'GUEST.DAT'][role] + bytes(256), 0x1000, size, role), on)

    @needs_clang
    def test_the_seeded_faults_of_the_harness_are_caught(self):
        self.assertNotEqual(self.run_c('len31', policy=0)[:2], (0, 'OK'), 'a length through 31 bits is not the file')
        rss = self.run_c('touch', self.small_path, self.small_written, policy=3)[3]
        good = self.run_c('on', self.small_path, self.small_written, policy=3)
        self.assertEqual(good[:2], (0, 'OK'))
        self.assertGreater(rss, 400 * MIB, 'hashing through the mapping makes the resident set the size of the file')
        self.assertGreater(rss, 4 * good[3], 'so the bound of the other test is a measurement and not a promise')


class Pair(unittest.TestCase):
    @needs_q1n1
    @needs_clang
    def test_the_box_ceiling_and_the_file_limit_follow_the_gate_only(self):
        import sp11_stick_wrapper_build as wb
        tree = wb.q1n1_tree()
        cflags, _ = wb.recipe_flags(tree)
        base = [clang, *cflags, f'-I{tree / "payloads"}', '-DSNAP_PLATFORM_SP11=1', '-DSNAP_SP11_STICK=1', '-DSNAP_SP11_STICK_GIC=1', '-DSNAP_SP11_STICK_MONITOR=1',
                *wb.gic_defines(str(ROOT / 'config/monitor-profile-sp11-m1.json')), *wb.monitor_defines(bytes(0x2000), 0x20000), '-DSW_GIC_DRY_REGS=0', '-fsyntax-only']
        base = [f for f in base if not f.startswith('-O')]

        def compiles(*defines):
            return subprocess.run([*base, *defines, str(ROOT / 'probes/sp11-stick-monitor.c')], capture_output=True, text=True).returncode == 0
        carry = '-DSNAP_SP11_STICK_ROOT_CARRY=1'
        for defines, ok in (((), True), (('-DSP11_STICK_MONITOR_BOX_MAX_S=120u',), True), (('-DSP11_STICK_MONITOR_BOX_MAX_S=121u',), False),
                            (('-DSP11_STICK_MONITOR_BOX_MAX_S=1200u',), False), ((carry,), True), ((carry, '-DSP11_STICK_MONITOR_BOX_MAX_S=1200u'), True),
                            ((carry, '-DSP11_STICK_MONITOR_BOX_MAX_S=1201u'), False), ((carry, '-DSP11_STICK_MONITOR_BOX_MAX_S=0u'), False)):
            with self.subTest(defines=defines):
                self.assertEqual(compiles(*defines), ok)

    @needs_q1n1
    @needs_clang
    def test_the_runner_limit_the_header_repeats_is_the_runners_in_both_modes(self):
        tree = bb.q1n1_tree()
        if 'Q1N1_SP11_BIGFILE' not in (tree / 'platform/uefi/sp11-payload.h').read_text():
            self.skipTest('this q1n1 tree has no Q1N1_SP11_BIGFILE (the W4 runner)')
        base = [clang, '-std=gnu11', '-fsyntax-only', '-Wall', '-Wextra', '-Werror', '-DSP11SB_Q1N1_ABI_CHECK', '-DSNAP_PLATFORM_SP11=1', '-DQ1N1_SP11_PAYLOAD=1',
                f'-I{tree / "platform/uefi"}']

        def compiles(*defines):
            return subprocess.run([*base, *defines, str(HERE / 'test-sp11-stick-bundle.c')], capture_output=True, text=True).returncode == 0
        both = ('-DQ1N1_SP11_BIGFILE=1', '-DSNAP_SP11_STICK_MONITOR=1', '-DSNAP_SP11_STICK_ROOT_CARRY=1')
        for defines, ok in (((), True), (both, True), (both[:1], False), (both[1:], False), (('-DSNAP_SP11_STICK_MONITOR=1',), True),
                            (('-DSNAP_SP11_STICK_ROOT_CARRY=1',), True),         # the gate means nothing without the monitor entry build: tools/sp11_stick_wrapper_build.py refuses that
                            ((*both, '-DQ1N1_SP11_BIGFILE_CHUNK_KIB=4096u'), False)):
            with self.subTest(defines=defines):
                self.assertEqual(compiles(*defines), ok)

    def test_the_gate_is_a_property_of_the_monitor_entry_build_and_the_pair_that_ran_is_unchanged_without_it(self):
        header = (ROOT / 'probes/sp11-stick-bundle.h').read_text()
        gated = header[header.index('#if SNAP_SP11_STICK_MONITOR\n/* The root carry'):header.index('#define SP11SB_PATH_MAX')]
        for needle in ('#error "SNAP_SP11_STICK_ROOT_CARRY accepts only 0 and 1"', '#define SP11SB_FILE_MAX UINT64_C(0x80000000)    /* 2 GiB per file */'):
            self.assertIn(needle, gated)
        self.assertEqual(gated.count('#define SP11SB_FILE_MAX UINT64_C(0x10000000)    /* 256 MiB per file */'), 2, 'the default is in the gate and after it')
        self.assertEqual(sb.FILE_MAX_BIG, 0x80000000)
        import sp11_stick_gatescan as gatescan
        commit = re.search(r"^COMMIT = '([0-9a-f]{40})'", (HERE / 'test-sp11-stick-bundle-record-gic.py').read_text(), re.M).group(1)     # the commit of the GIC pair that ran on the unit
        try:
            self.assertEqual(gatescan.source_problems(commit, ['probes/sp11-stick-bundle.h', 'probes/sp11-stick-hw.h']), [], 'an edit outside the monitor gate changes that pair')
        except LookupError as why:
            self.skipTest(str(why))

    def test_the_root_carry_pair_is_not_the_pair_of_any_form_and_the_old_boxes_stay_refused(self):
        (monitor, reporter), = m1.ROOT_CARRY_PAIRS.items()
        self.assertEqual([key for key, pair in m1.ACK_PAIRS.items() if pair[0] == monitor], [ch.RUNG9_PROFILE], 'rung 9 (K17) is the one form that has the root-carry pair')
        self.assertEqual([key for key, pair in m1.ACK_PAIRS.items() if pair[1] == reporter], [ch.RUNG9_PROFILE])
        self.assertEqual((m1.box_cap(monitor), m1.box_cap(m1.ACK_MONITOR_SHA256), m1.BOX_MAX_S, root.BOX_MAX_S), (1200, 120, 120, 1200))
        self.assertIsNone(m1.check_pair_form('ack', ch.RUNG9_FORM[1], monitor, reporter, ch.RUNG9_PROFILE))
        for key in (k for k in m1.ACK_PAIRS if k != ch.RUNG9_PROFILE):
            with self.subTest(key), self.assertRaises(ch.Refused) as caught:
                m1.check_pair_form('ack', key if key.startswith('cpu_') else 'cpu_boot_timeout_secs=120', monitor, reporter, key if key.startswith('j700') else None)
            self.assertEqual(caught.exception.name, 'M1_REPORT_MISMATCH')
        for box in (121, 300, 1200):
            with self.subTest(box), self.assertRaises(ch.Refused) as caught:
                m1.check_options(sb.STORE_ALL, False, box, 'console', 0, 'cpu_boot_timeout_secs=120', True, 'ack', ch.RUNG7_PROFILE)
            self.assertEqual(caught.exception.name, 'OPTIONS_BAD')
        for form in ch.ACK_FORMS[:4]:
            self.assertFalse(ch.let_panic_form_ok(form[0], form[1], 300, 'ack', ch.FORM_PROFILE.get(form)))
        self.assertTrue(ch.let_panic_form_ok(*ch.RUNG9_FORM[:3], 'ack', ch.RUNG9_PROFILE), 'a box of 300 s is the form of rung 9 (K17) and of no other')
        self.assertEqual(sorted(ch.ACK_FORMS, key=lambda f: (f[2], len(f))), [ch.RUNG5_FORM, ch.RUNG6_FORM, ch.RUNG7_FORM, ch.RUNG8_FORM, ch.RUNG9_FORM])
        import sp11_stick_wrapper_build as wb
        self.assertEqual((wb.ROOT_CARRY_BOX_MAX_S, wb.ROOT_CARRY_FILE_MAX), (m1.ROOT_CARRY_BOX_MAX_S, sb.FILE_MAX_BIG))
        self.assertEqual(wb.ROOT_CARRY_DEFINES, ('-DSNAP_SP11_STICK_ROOT_CARRY=1', '-DSP11_STICK_MONITOR_BOX_MAX_S=1200u'))


class Hygiene(unittest.TestCase):
    def test_licence_sizes_and_no_machine_text(self):
        for path in (HERE / 'sp11_stick_bundle_root.py', HERE / 'sp11_stick_bundle_source.py', HERE / 'test-sp11-stick-bundle-big.c', HERE / 'test-sp11-stick-bundle-big-tables.py',
                     HERE / 'test-sp11-stick-load-big.py', HERE / 'test-sp11-stick-load-big.c', Path(__file__).resolve(), DOC):
            text = path.read_text()
            self.assertTrue(text.isascii(), path.name)
            self.assertIn('SPDX-License-Identifier: MIT', text[:200], path.name)
            self.assertLess(len(text.splitlines()), 1500, path.name)
            self.assertLess(len(text.encode()), 51200, path.name)
            for prefix in ('/' + 'Users' + '/', '/' + 'home' + '/', '/' + 'tmp' + '/', 'mk' + 'temp'):
                self.assertNotIn(prefix, text, f'{path.name}: {prefix}')

    def test_the_note_names_the_gates_the_refusals_and_what_is_unknown(self):
        text = DOC.read_text()
        for needle in ('SNAP_SP11_STICK_ROOT_CARRY', 'SP11_ROOT_BUILD', 'root-guest', '--root-carry', '--bigfile', 'Q1N1_SP11_BIGFILE', 'UNKNOWN', 'NOT RUN', 'wdt=-1', 'cpu_boot_timeout_secs',
                       '--rtc-epoch-us', 'SP11_ROOT_BUILD=1', 'place-check'):
            self.assertIn(needle, text)
        for name in (*root.REFUSALS, *src.REFUSALS, 'ROOT_RTC'):
            self.assertIn(f'`{name}`', text, name)
        self.assertEqual(len(set(root.REFUSALS)), len(root.REFUSALS))

    def test_the_resident_set_of_this_run_is_bounded(self):
        self.assertLess(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, 2 * GIB)


if __name__ == '__main__':
    unittest.main()
