#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The SP11 stick-log groundwork: the Mac-side region tool, the BOT/SCSI model and the guarded writer (docs/SP11-STICK-LOG.md).

    python3 tools/test-sp11-stick-log.py

Offline: no hardware, no network, no stick. Nothing here opens a device or touches /Volumes: every image is a file under
build/sp11-stick-log/. What it runs:

* tools/sp11_fatvol.py and tools/sp11_stick_logmap.py on a FAT32 image with an MBR (made by this tool's own builder), a bare
  FAT32 volume and a FAT16 volume built with probes/uefi/host/fatimg.py: prepare, verify, read-log, against an independent
  oracle written here from the BPB layout, against fatimg's own reader, and against macOS's fsck_msdos -n where it exists
  (SKIP otherwise, never a pass);
* deliberately faulty images that must be rejected: a fragmented file, a wrong cluster size, a map off by one sector in
  either direction, a wrong signature (map, header, tag, nonce), a capacity that does not match, a file of the wrong size,
  torn and unwritten sectors, a destination under /Volumes or /dev, a `..` component, a symlink, an existing output;
* the C model tools/sp11-stick-bot.c and tools/sp11-stick-log.c under ASan and UBSan with the fake device (the two
  tools/test-sp11-stick-*.c drivers), a differential run of the C and Python encoders and parsers on thousands of random
  inputs, and the whole flow: the C writer writing into an image the Python tool made, the Python tool reading the log back
  out of the file and proving that nothing outside the region changed;
* seeded bugs, one at a time, in the C model and in the Python tools: every one must be caught;
* the design document's constants against the code, its citations against the pinned sources (SKIP when the pinned files
  were not fetched into build/sp11-stick-log/refs/), and hygiene (licence, size, purity, the gate, a freestanding AArch64
  object that needs no libc, no machine strings).

What passing proves: the host logic. It does not prove that any stick behaves as the fake does, that q1n1's xHCI driver can
carry the bytes, or anything about the Surface Pro 11. Hardware: NOT RUN.
"""
import concurrent.futures
import json
import os
import random
import re
import shutil
import struct
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
BUILD = ROOT / 'build' / 'sp11-stick-log'
WORK = Path(os.environ.get('SP11_STICK_WORK') or BUILD / 'work')     # a mutant run gets a work tree of its own
DOC = ROOT / 'docs' / 'SP11-STICK-LOG.md'

# A mutant copy of the Python tools is imported from SP11_STICK_MODDIR (see run_python_mutant); the real ones otherwise.
MODDIR = os.environ.get('SP11_STICK_MODDIR')
sys.path.insert(0, MODDIR or str(TOOLS))
sys.path.insert(1, str(ROOT / 'probes' / 'uefi' / 'host'))
import fatimg  # noqa: E402
import sp11_fatvol as fv  # noqa: E402
import sp11_stick_logmap as lm  # noqa: E402

NONCE = 0x0123456789ABCDEF
SECTOR = 512
BOT_C, BOT_H = TOOLS / 'sp11-stick-bot.c', TOOLS / 'sp11-stick-bot.h'
LOG_C, LOG_H = TOOLS / 'sp11-stick-log.c', TOOLS / 'sp11-stick-log.h'
FAKE_H = TOOLS / 'sp11-stick-fakedev.h'
BOT_TEST, LOG_TEST = TOOLS / 'test-sp11-stick-bot.c', TOOLS / 'test-sp11-stick-log.c'
C_FILES = (BOT_C, BOT_H, LOG_C, LOG_H, FAKE_H, BOT_TEST, LOG_TEST)
PY_FILES = (TOOLS / 'sp11_fatvol.py', TOOLS / 'sp11_stick_logmap.py', Path(__file__).resolve())
CFLAGS = ['-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Wpedantic', '-Wshadow', '-DSNAP_PLATFORM_SP11=1']
LLVM_NM = shutil.which('llvm-nm') or '/opt/homebrew/opt/llvm/bin/llvm-nm'
CLANG = shutil.which('clang') or '/opt/homebrew/opt/llvm/bin/clang'
FSCK = '/sbin/fsck_msdos'


# ---------------------------------------------------------------------------------------------------------------------
# Building and running the C tests
# ---------------------------------------------------------------------------------------------------------------------
def compile_c(out, sources, include_dirs, sanitize=True, werror=True, extra=()):
    cmd = ['cc', *CFLAGS, *extra]
    for d in include_dirs:
        cmd += ['-I', str(d)]
    if werror:
        cmd.append('-Werror')
    if sanitize:
        cmd += ['-fsanitize=address,undefined', '-fno-sanitize-recover=all']
    cmd += [*map(str, sources), '-o', str(out)]
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=300)
    if result.returncode:
        raise RuntimeError(result.stderr[-3000:])
    return out


_binaries = {}


def binaries():
    """The two native test drivers, built once with sanitizers."""
    if not _binaries:
        (BUILD / 'bin').mkdir(parents=True, exist_ok=True)
        _binaries['bot'] = compile_c(BUILD / 'bin' / 'test-sp11-stick-bot', [BOT_TEST, BOT_C], [TOOLS])
        _binaries['log'] = compile_c(BUILD / 'bin' / 'test-sp11-stick-log', [LOG_TEST, LOG_C, BOT_C], [TOOLS])
    return _binaries


def oracle_run(exe, lines):
    """One process, one command per line, one answer per line."""
    result = subprocess.run([str(exe), '--stdin'], input='\n'.join(lines) + '\n', capture_output=True, text=True,
                            timeout=600)
    if result.returncode:
        raise RuntimeError(result.stderr[-2000:])
    return result.stdout.splitlines()


# ---------------------------------------------------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------------------------------------------------
def fresh(name):
    path = WORK / name
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def make_tree(directory):
    files = {'EFI/BOOT/BOOTAA64.EFI': b'MZ' + bytes(range(256)) * 12, 'SP11/Q1N1.EFI': b'MZ' + bytes(5000),
             'SP11/SLBOUNCE.EFI': b'MZ' + bytes(range(255, 0, -1)) * 9, 'SP11/README.TXT': b'plain text\n' * 40}
    for relative, data in files.items():
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return files


def build_img(name, mbr=True, spc=8, size_mib=300, device_sectors=None, nonce=NONCE, log_mib=1, mod=None):
    directory = fresh(name)
    tree = directory / 'tree'
    files = make_tree(tree)
    out = directory / 'stick.img'
    (mod or lm).build_image(tree, out, size_mib=size_mib, cluster_sectors=spc, device_sectors=device_sectors, nonce=nonce,
                            mbr=mbr, log_mib=log_mib)
    return out, files


def fat16_base(name):
    """A FAT16 superfloppy with an SP11 directory, built by the repository's own helper (probes/uefi/host/fatimg.py)."""
    directory = fresh(name)
    image = fatimg.Fat16Image(16 << 20)
    image.add_file('SP11/Q1N1.EFI', b'MZ' + bytes(5000))
    image.add_file('SP11/README.TXT', b'hello\n' * 100)
    image.add_file('EFI/BOOT/BOOTAA64.EFI', b'MZ' + bytes(range(256)))
    base = directory / 'base.img'
    base.write_bytes(image.to_bytes())
    return directory, base


def patch(path, offset, data):
    fd = os.open(path, os.O_RDWR)
    try:
        os.pwrite(fd, data, offset)
    finally:
        os.close(fd)


def add_long_name_file(vol, directory, long_name, alias11, data):
    """Place a file the way an operating system does for a name that is not 8.3: the LFN entries, then the short entry."""
    ref = vol.resolve_dir(directory)
    units = [ord(c) for c in long_name]
    count = -(-len(units) // 13)
    pad = 13 * count - len(units)
    units += ([0] if pad else []) + [0xFFFF] * max(pad - 1, 0)
    check = 0
    for c in alias11:
        check = (((check & 1) << 7) + (check >> 1) + c) & 0xFF
    entries = []
    for i in range(count, 0, -1):
        chunk = units[(i - 1) * 13:i * 13]
        e = bytearray(32)
        e[0] = i | (0x40 if i == count else 0)
        struct.pack_into('<5H', e, 1, *chunk[0:5])
        e[11], e[13] = 0x0F, check
        struct.pack_into('<6H', e, 14, *chunk[5:11])
        struct.pack_into('<2H', e, 28, *chunk[11:13])
        entries.append(bytes(e))
    clusters = -(-len(data) // vol.cluster_bytes)
    first = vol.allocate(clusters)
    vol.img.write(vol.cluster_offset(first), data + bytes(clusters * vol.cluster_bytes - len(data)))
    short = bytearray(32)
    short[0:11], short[11] = alias11, 0x20
    struct.pack_into('<H', short, 20, first >> 16)
    struct.pack_into('<HI', short, 26, first & 0xFFFF, len(data))
    entries.append(bytes(short))
    slots = [offset for offset, raw in vol._slots(ref) if raw[0] == 0x00]
    for k, entry in enumerate(entries):
        vol.img.write(slots[k], entry)


def pread(path, offset, length):
    fd = os.open(path, os.O_RDONLY)
    try:
        return os.pread(fd, length, offset)
    finally:
        os.close(fd)


class Oracle:
    """An independent reader of the facts the tool must get right, written from the BPB layout (struct offsets, no
    sp11_fatvol). It finds a file by its 11-byte name and says where its clusters are on the disk."""

    def __init__(self, path, part_start=0):
        self.path, self.part = Path(path), part_start
        b = pread(path, part_start * 512, 512)
        self.spc = b[13]
        self.rsvd = struct.unpack_from('<H', b, 14)[0]
        self.nfats = b[16]
        self.root_entries = struct.unpack_from('<H', b, 17)[0]
        self.fatsz = struct.unpack_from('<H', b, 22)[0] or struct.unpack_from('<I', b, 36)[0]
        self.total = struct.unpack_from('<H', b, 19)[0] or struct.unpack_from('<I', b, 32)[0]
        self.root_sectors = (self.root_entries * 32 + 511) // 512
        self.is32 = self.root_entries == 0
        self.root_cluster = struct.unpack_from('<I', b, 44)[0] if self.is32 else 0
        self.data_start = self.rsvd + self.nfats * self.fatsz + self.root_sectors
        self.clusters = (self.total - self.data_start) // self.spc

    @property
    def data_lba(self):
        return self.part + self.data_start

    def lba(self, cluster):
        return self.part + self.data_start + (cluster - 2) * self.spc

    def fat(self, cluster):
        width = 4 if self.is32 else 2
        raw = pread(self.path, (self.part + self.rsvd) * 512 + cluster * width, width)
        return int.from_bytes(raw, 'little') & (0x0FFFFFFF if self.is32 else 0xFFFF)

    def fat_copies_agree(self):
        size = self.fatsz * 512
        first = pread(self.path, (self.part + self.rsvd) * 512, size)
        return all(pread(self.path, (self.part + self.rsvd + k * self.fatsz) * 512, size) == first
                   for k in range(1, self.nfats))

    def chain(self, first):
        out, c = [], first
        while True:
            out.append(c)
            nxt = self.fat(c)
            if nxt >= (0x0FFFFFF8 if self.is32 else 0xFFF8):
                return out
            c = nxt

    def dir_bytes(self, cluster):
        if not self.is32 and cluster == 0:
            return pread(self.path, (self.part + self.rsvd + self.nfats * self.fatsz) * 512, self.root_sectors * 512)
        return b''.join(pread(self.path, self.lba(c) * 512, self.spc * 512) for c in self.chain(cluster))

    def find(self, path):
        """(first cluster, size, offset of the directory entry) of a file or directory, or None."""
        cluster = self.root_cluster
        parts = path.split('/')
        for depth, part in enumerate(parts):
            name = part.split('.')[0].ljust(8).encode() + (part.split('.')[1] if '.' in part else '').ljust(3).encode()
            data = self.dir_bytes(cluster)
            hit = None
            for at in range(0, len(data), 32):
                e = data[at:at + 32]
                if e[0] == 0:
                    break
                if e[0] != 0xE5 and e[11] & 0x3F != 0x0F and e[:11] == name:
                    hit = ((struct.unpack_from('<H', e, 20)[0] << 16) | struct.unpack_from('<H', e, 26)[0],
                           struct.unpack_from('<I', e, 28)[0])
                    break
            if hit is None:
                return None
            if depth == len(parts) - 1:
                return hit
            cluster = hit[0]
        return None


def decode_map_by_hand(raw):
    """The map layout of the design document, with struct and nothing else."""
    assert len(raw) == 128
    magic, ver, nbytes, nonce = struct.unpack_from('<8sIIQ', raw, 0)
    sector, cap, part, cs, data, region, runs, flags = struct.unpack_from('<8I', raw, 24)
    start, count = struct.unpack_from('<II', raw, 0x38)
    crc = struct.unpack_from('<I', raw, 124)[0]
    return dict(magic=magic, version=ver, nbytes=nbytes, nonce=nonce, sector=sector, capacity=cap, partition=part,
                cluster=cs, data=data, region=region, runs=runs, flags=flags, start=start, count=count, crc=crc)


def has_fsck():
    return os.path.exists(FSCK)


def fsck_clean(path):
    """macOS's own checker, read-only (-n), on an absolute path so it can never be read as a device name."""
    result = subprocess.run([FSCK, '-n', str(Path(path).resolve())], capture_output=True, text=True, timeout=300)
    return result.returncode == 0, result.stdout + result.stderr


# ---------------------------------------------------------------------------------------------------------------------
# The battery: every check the Python tools must pass. Run in-process by the tests and, against a mutated copy of the
# tools, in a subprocess by the mutant test (a mutant may hang; the harness then times it out and counts it as caught).
# ---------------------------------------------------------------------------------------------------------------------
class Battery:
    def __init__(self):
        self.failures = []

    def check(self, name, fn):
        try:
            if fn() is False:
                self.failures.append(name)
        except BaseException as exc:                      # a mutant may raise anything; that is a failure of the check
            self.failures.append(f'{name}: {type(exc).__name__} {exc}')

    def refuses(self, name, fn, reason=None):
        try:
            fn()
        except (lm.Refusal, fv.FatError) as exc:
            if reason is not None and exc.reason != reason:
                self.failures.append(f'{name}: refused as {exc.reason}, wanted {reason}')
        except BaseException as exc:
            self.failures.append(f'{name}: {type(exc).__name__} {exc}')
        else:
            self.failures.append(f'{name}: not refused')

    def finds(self, name, path, codes, **kw):
        try:
            report = lm.verify_image(path, **kw)
        except BaseException as exc:
            self.failures.append(f'{name}: {type(exc).__name__} {exc}')
            return
        got = {c for c, _ in report['findings']}
        if report['ok'] or not set(codes) <= got:
            self.failures.append(f'{name}: findings {sorted(got)}, wanted {sorted(codes)}')


def battery():
    b = Battery()
    img, files = build_img('bat-mbr')
    part = 2048
    ora = Oracle(img, part)

    # The good image verifies and agrees with the independent oracle.
    b.check('good image verifies', lambda: lm.verify_image(img)['ok'])
    log = ora.find('SP11/SNAPLOG.BIN')
    mapf = ora.find('SP11/SNAPLOG.MAP')
    chain = ora.chain(log[0])

    def agrees():
        raw = b''.join(pread(img, ora.lba(c) * 512, ora.spc * 512) for c in ora.chain(mapf[0]))[:mapf[1]]
        m = decode_map_by_hand(raw)
        first = ora.lba(chain[0])
        return (m['magic'] == b'SNAPLMAP' and m['start'] == first and m['count'] == 2048 and m['region'] == 2048 and
                m['cluster'] == ora.spc and m['data'] == ora.data_lba and m['partition'] == part and
                m['capacity'] == 614400 and m['runs'] == 1 and m['nonce'] == NONCE and
                all(b2 - a == 1 for a, b2 in zip(chain, chain[1:])) and len(chain) * ora.spc == 2048 and log[1] == 1 << 20)
    b.check('map and file agree with the oracle', agrees)
    b.check('the log is where the oracle says', lambda: pread(img, ora.lba(chain[0]) * 512, 8) == b'SNAPLOG1')
    b.check('the other files are intact', lambda: all(
        fv.FatVolume(fv.ImageFile(img), part).read_file(fv.FatVolume(fv.ImageFile(img), part).lookup(p)) == d
        for p, d in files.items()))

    # Rejected: a fragmented file (the same clusters, in an order that is not contiguous).
    frag = fresh('bat-frag') / 'x.img'
    fv.copy_sparse(img, frag)
    with fv.ImageFile(frag, writable=True) as im:
        vol = fv.open_volume(im)
        c = vol.chain(vol.lookup('SP11/SNAPLOG.BIN').first_cluster)
        vol.fat_set(c[10], c[12])
        vol.fat_set(c[12], c[11])
        vol.fat_set(c[11], c[13])
    b.finds('fragmented file is rejected', frag, ['fragmented', 'map_run_mismatch'])

    # Rejected: a hostile chain (a loop, a stray cluster, a free cluster) is reported and never followed.
    hostile = fresh('bat-chain') / 'x.img'
    fv.copy_sparse(img, hostile)
    with fv.ImageFile(hostile, writable=True) as im:
        vol = fv.open_volume(im)
        first = vol.lookup('SP11/SNAPLOG.BIN').first_cluster
        vol.fat_set(first + 5, first + 1)
        b.refuses('a looping chain', lambda: vol.chain(first), 'chain_loops')
        vol.fat_set(first + 5, vol.clusters + 7)
        b.refuses('a chain that leaves the volume', lambda: vol.chain(first), 'chain_leaves_volume')
        vol.fat_set(first + 5, 0)
        b.refuses('a chain that ends in a free cluster', lambda: vol.chain(first), 'chain_ends_in_free_or_bad')
    b.finds('verify reports a damaged chain', hostile, ['log_chain_invalid'])
    b.check('the FAT32 image has two FAT copies and they agree', lambda: ora.nfats == 2 and ora.fat_copies_agree())

    # Rejected: a wrong cluster size, in the volume or in the map.
    wrong = fresh('bat-cluster') / 'x.img'
    fv.copy_sparse(img, wrong)
    patch(wrong, part * 512 + 13, bytes([4]))
    b.finds('BPB cluster size changed (the volume no longer reads)', wrong, [])
    map_off = ora.lba(ora.chain(mapf[0])[0]) * 512
    for name, change, codes in (
            ('map cluster size', {'cluster_sectors': 4}, ['map_geometry_vs_volume']),
            ('map data lba', {'data_lba': ora.data_lba + 8}, ['map_geometry_vs_volume']),
            ('map partition lba', {'partition_lba': 0}, ['map_geometry_vs_volume']),
            ('map capacity', {'capacity_sectors': 614399}, ['capacity_mismatch']),
            ('map sector size', {'sector_size': 4096}, ['map_sector_size']),
            ('map nonce', {'nonce': NONCE ^ 4}, ['header_nonce', 'tag_invalid'])):
        t = fresh('bat-map-' + name.replace(' ', '-')) / 'x.img'
        fv.copy_sparse(img, t)
        m = lm.decode_map(pread(img, map_off, 128))
        fields = dict(nonce=m['nonce'], capacity_sectors=m['capacity_sectors'], partition_lba=m['partition_lba'],
                      cluster_sectors=m['cluster_sectors'], data_lba=m['data_lba'], region_sectors=m['region_sectors'],
                      runs=m['runs'])
        fields.update(change)
        sector_size = fields.pop('sector_size', 512)
        raw = lm.encode_map(fields['nonce'], fields['capacity_sectors'], fields['partition_lba'], fields['cluster_sectors'],
                            fields['data_lba'], fields['region_sectors'], fields['runs'])
        if sector_size != 512:
            raw = bytearray(raw)
            struct.pack_into('<I', raw, 0x18, sector_size)
            struct.pack_into('<I', raw, 124, lm.crc32(bytes(raw[:124])))
            raw = bytes(raw)
        patch(t, map_off, raw)
        b.finds('tampered ' + name, t, codes)

    # Rejected: a map off by one sector, either way, with its CRC kept valid.
    for delta in (1, -1):
        t = fresh(f'bat-off{delta}') / 'x.img'
        fv.copy_sparse(img, t)
        m = lm.decode_map(pread(img, map_off, 128))
        start, count = m['runs'][0]
        patch(t, map_off, lm.encode_map(m['nonce'], m['capacity_sectors'], m['partition_lba'], m['cluster_sectors'],
                                        m['data_lba'], m['region_sectors'], [(start + delta, count)]))
        b.finds(f'map off by {delta:+d} sector', t, ['map_run_mismatch'])

    # Rejected: a wrong signature, in the map, the header, a tag and the nonce.
    first_lba = ora.lba(chain[0])
    for name, offset, data, codes in (
            ('map magic', map_off, b'X', ['map_magic']),
            ('map crc', map_off + 125, b'\xff', ['map_crc']),
            ('header magic', first_lba * 512, b'X', ['header_magic']),
            ('header nonce', first_lba * 512 + 16, b'\x01', ['header_crc']),
            ('tag magic', (first_lba + 5) * 512, b'X', ['tag_invalid']),
            ('tag index', (first_lba + 5) * 512 + 4, b'\x09', ['tag_invalid']),
            ('tag nonce', (first_lba + 9) * 512 + 8, b'\x00', ['tag_invalid']),
            ('sector crc', (first_lba + 9) * 512 + 28, b'\xff\xff', ['sector_crc'])):
        t = fresh('bat-sig-' + name.replace(' ', '-')) / 'x.img'
        fv.copy_sparse(img, t)
        patch(t, offset, data)
        b.finds('wrong ' + name, t, codes)

    # Rejected: the capacity the caller says is not the map's; a file of the wrong size.
    b.finds('device capacity differs from the map', img, ['capacity_mismatch'], device_sectors=614401)
    t = fresh('bat-size') / 'x.img'
    fv.copy_sparse(img, t)
    with fv.ImageFile(t, writable=True) as im:
        entry = fv.open_volume(im).lookup('SP11/SNAPLOG.BIN')
        im.write(entry.offset + 28, struct.pack('<I', (1 << 20) - 512))
    b.finds('log file size differs from the region', t, ['log_file_size'])

    # Written and torn sectors.
    t = fresh('bat-written') / 'x.img'
    fv.copy_sparse(img, t)
    patch(t, (first_lba + 3) * 512, lm.encode_log_sector(NONCE, 3, 2, 0, b'hello'))
    b.finds('a written sector is not pristine', t, ['region_not_pristine'])
    b.check('allow_written accepts it', lambda: lm.verify_image(t, allow_written=True)['ok'])
    b.check('read-log returns it', lambda: lm.read_log(t)['text'] == b'hello')
    t2 = fresh('bat-torn') / 'x.img'
    fv.copy_sparse(t, t2)
    patch(t2, (first_lba + 3) * 512 + 33, b'\xff')
    b.finds('a torn sector is flagged', t2, ['sector_crc'], allow_written=True)

    # The Mac reads the log from a copy of the region file; these tools never open the stick's own file system.
    copy = fresh('bat-decode')
    with fv.ImageFile(t) as im:
        vol = fv.open_volume(im)
        region_bytes = vol.read_file(vol.lookup('SP11/SNAPLOG.BIN'))
    (copy / 'SNAPLOG.BIN').write_bytes(region_bytes)
    b.check('decode reads a copy of the region file', lambda: lm.decode_region_file(copy / 'SNAPLOG.BIN')['text'] == b'hello')
    (copy / 'short.bin').write_bytes(region_bytes[:-100])
    b.refuses('a region copy that is not whole sectors', lambda: lm.decode_region_file(copy / 'short.bin'),
              'region_file_size')
    (copy / 'half.bin').write_bytes(region_bytes[:1024 * 512])
    b.refuses('a region copy shorter than its header says', lambda: lm.decode_region_file(copy / 'half.bin'),
              'region_file_size')
    (copy / 'bad.bin').write_bytes(b'X' + region_bytes[1:])
    b.refuses('a region copy with a wrong signature', lambda: lm.decode_region_file(copy / 'bad.bin'), 'header_magic')
    b.refuses('a region copy under /Volumes', lambda: lm.decode_region_file('/Volumes/nothing-here/SNAPLOG.BIN'),
              'input_under_volumes')

    # prepare: a separate image, the FAT16 case from the repository's own helper, and the refusals.
    directory, base = fat16_base('bat-fat16')
    base_before = base.read_bytes()
    out = directory / 'prepared.img'
    facts = lm.prepare_image(base, out, nonce=NONCE)
    b.check('FAT16 prepare verifies', lambda: lm.verify_image(out)['ok'])
    o16 = Oracle(out, 0)
    l16, m16 = o16.find('SP11/SNAPLOG.BIN'), o16.find('SP11/SNAPLOG.MAP')
    c16 = o16.chain(l16[0])
    b.check('FAT16 geometry by the oracle', lambda: (
        not o16.is32 and o16.spc == 4 and facts['start_lba'] == o16.lba(c16[0]) and len(c16) * o16.spc == 2048 and
        all(y - x == 1 for x, y in zip(c16, c16[1:])) and facts['data_lba'] == o16.data_lba and
        pread(out, o16.lba(c16[0]) * 512, 8) == b'SNAPLOG1'))
    b.check('FAT16 copies agree', o16.fat_copies_agree)
    tree16 = fatimg.read_fat16(out.read_bytes())
    b.check('fatimg reads the log file', lambda: tree16.files['SP11/SNAPLOG.BIN'][:8] == b'SNAPLOG1' and
            len(tree16.files['SP11/SNAPLOG.BIN']) == 1 << 20 and len(tree16.files['SP11/SNAPLOG.MAP']) == 128 and
            tree16.files['SP11/Q1N1.EFI'] == b'MZ' + bytes(5000))
    b.check('the base image is untouched', lambda: base.read_bytes() == base_before)

    # Refusals of prepare and build.
    d = fresh('bat-refuse')
    b.refuses('output exists', lambda: lm.prepare_image(base, out), 'dest_exists')
    b.refuses('log of zero MiB', lambda: lm.prepare_image(base, d / 'a.img', log_mib=0), 'log_size')
    b.refuses('log of 33 MiB', lambda: lm.prepare_image(base, d / 'b.img', log_mib=33), 'log_size')
    b.refuses('nonce of zero', lambda: lm.prepare_image(base, d / 'c.img', nonce=0), 'nonce_range')
    b.refuses('nonce of 2^64', lambda: lm.prepare_image(base, d / 'd.img', nonce=1 << 64), 'nonce_range')
    b.refuses('device smaller than the image', lambda: lm.prepare_image(base, d / 'e.img', device_sectors=100),
              'device_smaller_than_image')
    b.refuses('device of 2^32 sectors', lambda: lm.prepare_image(base, d / 'f.img', device_sectors=1 << 32),
              'device_too_large')
    b.check('a refused prepare leaves no output', lambda: not any((d / n).exists() for n in
                                                                  ('a.img', 'b.img', 'c.img', 'd.img', 'e.img', 'f.img')))
    nodir = fresh('bat-nodir')
    bare = fatimg.Fat16Image(16 << 20)
    bare.add_file('OTHER/X.TXT', b'x')
    (nodir / 'base.img').write_bytes(bare.to_bytes())
    b.refuses('missing SP11 directory without --mkdir', lambda: lm.prepare_image(nodir / 'base.img', nodir / 'o.img'),
              'directory_missing')
    b.check('--mkdir creates it', lambda: lm.verify_image(lm.prepare_image(nodir / 'base.img', nodir / 'p.img',
                                                                              make_directory=True)['image'])['ok'])
    # No contiguous run: a volume whose free space is cut into pieces shorter than the log.
    chop = fresh('bat-chop')
    fv.format_fat32(chop / 'base.img', 614400, 8, mbr=True)
    with fv.ImageFile(chop / 'base.img', writable=True) as im:
        vol = fv.open_volume(im)
        vol.mkdir('SP11')
        for cluster in range(40, vol.clusters + 2, 64):
            vol.fat_set(cluster, vol.eoc)
    b.refuses('no contiguous free run', lambda: lm.prepare_image(chop / 'base.img', chop / 'o.img'), 'no_contiguous_free_run')
    b.check('and leaves no output', lambda: not (chop / 'o.img').exists())
    # Volumes the tool does not handle.
    odd = fresh('bat-odd')
    fv.format_fat32(odd / 'g.img', 614400, 8, mbr=True)
    patch(odd / 'g.img', 446 + 4, b'\xee')
    b.refuses('GPT protective MBR', lambda: lm.prepare_image(odd / 'g.img', odd / 'o1.img'), 'gpt_unsupported')
    wide = bytearray(fatimg.Fat16Image(16 << 20).to_bytes())
    struct.pack_into('<H', wide, 11, 4096)
    (odd / 't.img').write_bytes(bytes(wide))
    b.refuses('4096-byte sectors', lambda: lm.prepare_image(odd / 't.img', odd / 'o2.img'), 'unsupported_sector_size')

    # Destinations: the refusals of tools/stage-sp11-boot-media.py, for files.
    r = fresh('bat-dest')
    (r / 'real.img').write_bytes(b'')
    os.symlink(r / 'real.img', r / 'link.img')
    os.symlink(r, r / 'linkdir')
    for name, path, reason in (
            ('under /Volumes', '/Volumes/nothing-here/x.img', 'dest_under_volumes'),
            ('under /volumes (case)', '/volumes/nothing-here/x.img', 'dest_under_volumes'),
            ('under /dev', '/dev/null-x/x.img', 'dest_under_dev'),
            ('a device node', '/dev/null', 'dest_under_dev'),
            ('a parent reference', str(r / 'sub' / '..' / 'x.img'), 'dest_has_parent_reference'),
            ('a symbolic link', str(r / 'link.img'), 'dest_is_symlink'),
            ('a symbolic-link directory', str(r / 'linkdir' / 'x.img'), 'dest_path_has_symlink_component'),
            ('an existing file', str(r / 'real.img'), 'dest_exists'),
            ('a missing parent', str(r / 'nope' / 'x.img'), 'dest_parent_missing')):
        b.refuses('output ' + name, lambda p=path: lm.check_output_path(p), reason)
    for name, path, reason in (('under /Volumes', '/Volumes/nothing-here/x.img', 'input_under_volumes'),
                               ('under /dev', '/dev/disk0', 'input_under_dev'),
                               ('missing', str(r / 'missing.img'), 'input_does_not_exist'),
                               ('a symbolic link', str(r / 'link.img'), 'input_is_symlink'),
                               ('a directory', str(r), 'input_is_not_regular_file')):
        b.refuses('input ' + name, lambda p=path: lm.check_input_path(p), reason)
    b.refuses('prepare from /Volumes', lambda: lm.prepare_image('/Volumes/nothing-here/x.img', r / 'o.img'),
              'input_under_volumes')
    b.refuses('prepare to /Volumes', lambda: lm.prepare_image(base, '/Volumes/nothing-here/o.img'), 'dest_under_volumes')
    b.refuses('verify on /dev', lambda: lm.read_log('/dev/null'), 'input_under_dev')
    b.check('verify reports instead of raising', lambda: lm.verify_image('/Volumes/nothing-here/x.img')['findings'][0][0] ==
            'input_under_volumes')
    b.check('an ordinary output path is accepted', lambda: lm.check_output_path(r / 'fresh.img').name == 'fresh.img')
    # build refuses what it cannot create.
    bt = fresh('bat-build')
    tree = bt / 'tree'
    make_tree(tree)
    (tree / 'SP11' / 'LONGERNAME.EFI').write_bytes(b'x')
    b.refuses('long names in a tree', lambda: lm.build_image(tree, bt / 'o.img'), 'tree_name_not_83')
    b.check('and leaves no output', lambda: not (bt / 'o.img').exists())

    # Chronological decoding.
    def decode():
        region = bytearray(lm.build_region(NONCE, 64, 1000, 0, 5000))
        order = [(2, 1, b'beta '), (1, 0, b'alpha '), (2, 0, b'be'), (3, 0, b'gamma')]
        for index, (boot, seq, text) in zip((40, 7, 20, 3), order):
            region[index * 512:(index + 1) * 512] = lm.encode_log_sector(NONCE, index, boot, seq, text)
        region[9 * 512:10 * 512] = lm.encode_log_sector(NONCE, 9, 4, 0, b'torn')
        region[9 * 512 + 33] ^= 1
        region[11 * 512 + 8] ^= 1
        out = lm.decode_region(bytes(region), NONCE)
        return (out['text'] == b'alpha bebeta gamma' and out['torn'] == [9] and out['foreign'] == [11] and
                out['boots'] == [1, 2, 3])
    b.check('decode_region orders by boot and sequence', decode)
    return b.failures


# ---------------------------------------------------------------------------------------------------------------------
class FatVolumeTests(unittest.TestCase):
    def test_battery_passes_on_the_real_tools(self):
        failures = battery()
        self.assertEqual(failures, [])

    def test_fat32_mbr_image_is_consistent_with_the_os_checker(self):
        if not has_fsck():
            self.skipTest('SKIP: fsck_msdos is not available')
        # fsck_msdos cannot read through an MBR, so the same tree is built as a bare volume.
        img, _ = build_img('fsck-bare', mbr=False)
        ok, text = fsck_clean(img)
        self.assertTrue(ok, text)
        self.assertIn('9 files', text)           # fsck counts the 6 files and the 3 directories (EFI, EFI/BOOT, SP11)

    def test_fat16_prepared_image_is_consistent_with_the_os_checker(self):
        if not has_fsck():
            self.skipTest('SKIP: fsck_msdos is not available')
        directory, base = fat16_base('fsck-16')
        out = directory / 'p.img'
        lm.prepare_image(base, out, nonce=NONCE)
        ok, text = fsck_clean(out)
        self.assertTrue(ok, text)

    def test_other_cluster_sizes(self):
        for spc, size in ((1, 40), (2, 140), (16, 520), (128, 4100)):
            img, _ = build_img(f'spc{spc}', mbr=True, spc=spc, size_mib=size)
            self.assertTrue(lm.verify_image(img)['ok'], (spc, lm.verify_image(img)))
            ora = Oracle(img, 2048)
            chain = ora.chain(ora.find('SP11/SNAPLOG.BIN')[0])
            self.assertEqual(len(chain) * spc, 2048)
            self.assertEqual(pread(img, ora.lba(chain[0]) * 512, 8), b'SNAPLOG1')

    def test_head_of_a_larger_device(self):
        img, _ = build_img('head', device_sectors=30_273_437)
        self.assertEqual(lm.verify_image(img)['findings'][0][0], 'capacity_mismatch')
        self.assertTrue(lm.verify_image(img, device_sectors=30_273_437)['ok'])

    def test_prepare_leaves_long_name_entries_alone(self):
        """A real stick has long names (for example MANIFEST.SHA256): the entries must survive prepare byte for byte."""
        directory, base = fat16_base('lfn')
        with fv.ImageFile(base, writable=True) as im:
            vol = fv.open_volume(im)
            add_long_name_file(vol, 'SP11', 'MANIFEST.SHA256', b'MANIFE~1SHA', b'sum  file\n' * 20)
            add_long_name_file(vol, 'SP11', 'a long name with spaces.txt', b'ALONGN~1TXT', b'text\n')
        if has_fsck():
            ok, text = fsck_clean(base)
            self.assertTrue(ok, 'the fixture must be a valid long-name layout: ' + text)
        out = directory / 'p.img'
        lm.prepare_image(base, out, nonce=NONCE)
        self.assertTrue(lm.verify_image(out)['ok'])
        if has_fsck():
            ok, text = fsck_clean(out)
            self.assertTrue(ok, text)

        def slots(path):
            with fv.ImageFile(path) as im:
                vol = fv.open_volume(im)
                return [raw for _, raw in vol._slots(vol.resolve_dir('SP11'))]
        before, after = slots(base), slots(out)
        used = next(i for i, raw in enumerate(before) if raw[0] == 0)
        self.assertEqual(after[:used], before[:used])                      # every existing entry, long names included
        self.assertEqual([raw[:11] for raw in after[used:used + 2]], [b'SNAPLOG BIN', b'SNAPLOG MAP'])
        with fv.ImageFile(out) as im:
            vol = fv.open_volume(im)
            entry = vol.lookup_in(vol.resolve_dir('SP11'), b'MANIFE~1SHA')           # by its alias: the tool makes no long names
            self.assertEqual(vol.read_file(entry), b'sum  file\n' * 20)

    def test_prepare_never_modifies_its_input_and_is_repeatable(self):
        directory, base = fat16_base('repeat')
        before = base.read_bytes()
        a, b = directory / 'a.img', directory / 'b.img'
        lm.prepare_image(base, a, nonce=NONCE)
        lm.prepare_image(base, b, nonce=NONCE)
        self.assertEqual(base.read_bytes(), before)
        self.assertEqual(a.read_bytes(), b.read_bytes())            # fixed nonce: byte-identical images
        c = directory / 'c.img'
        lm.prepare_image(base, c)
        self.assertNotEqual(a.read_bytes(), c.read_bytes())          # a random nonce differs
        self.assertTrue(lm.verify_image(c)['ok'])

    def test_cli(self):
        img, _ = build_img('cli')
        run = lambda *a: subprocess.run([sys.executable, '-B', str(TOOLS / 'sp11_stick_logmap.py'), *a], cwd=ROOT,
                                        capture_output=True, text=True, timeout=300)
        ok = run('verify', '--image', str(img))
        self.assertEqual(ok.returncode, 0, ok.stderr)
        bad = run('verify', '--image', str(img), '--device-sectors', '614401')
        self.assertEqual(bad.returncode, 1)
        self.assertIn('NOT VERIFIED capacity_mismatch', bad.stderr)
        refused = run('verify', '--image', '/Volumes/nothing-here/x.img')
        self.assertEqual(refused.returncode, 1)
        self.assertIn('input_under_volumes', refused.stderr)
        self.assertEqual(run('verify').returncode, 2)
        out = fresh('cli2') / 'o.img'
        made = run('prepare', '--image', str(img), '--out', str(out))
        self.assertEqual(made.returncode, 1)                                   # the log files already exist in it
        self.assertFalse(out.exists())
        js = run('verify', '--image', str(img), '--json')
        self.assertTrue(json.loads(js.stdout)['ok'])
        text = run('read-log', '--image', str(img))
        self.assertEqual((text.returncode, text.stdout), (0, ''))
        region = fresh('cli3') / 'SNAPLOG.BIN'
        with fv.ImageFile(img) as im:
            vol = fv.open_volume(im)
            region.write_bytes(vol.read_file(vol.lookup('SP11/SNAPLOG.BIN')))
        decoded = run('decode', '--region', str(region))
        self.assertEqual((decoded.returncode, decoded.stdout), (0, ''))
        self.assertEqual(run('decode', '--region', '/Volumes/nothing-here/SNAPLOG.BIN').returncode, 1)


class DestinationParity(unittest.TestCase):
    """The refusals must be the ones tools/stage-sp11-boot-media.py applies to its destination."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location('stage_sp11_boot_media_for_parity', TOOLS / 'stage-sp11-boot-media.py')
        cls.stage = None
        cls.why = ''
        try:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            cls.stage = module
        except BaseException as exc:                       # the script is edited elsewhere; a load failure is a SKIP
            cls.why = f'{type(exc).__name__}: {exc}'

    def test_same_verdict_on_the_same_paths(self):
        if self.stage is None:
            self.skipTest('SKIP: tools/stage-sp11-boot-media.py could not be loaded: ' + self.why)
        r = fresh('parity')
        os.symlink(r, r / 'linkdir')
        paths = ['/Volumes', '/Volumes/x', '/volumes/x', '/VOLUMES/x/y', '/dev', '/dev/null', '/DEV/x',
                 '/System/Volumes/Data/Volumes/x', '/System/Volumes/Data/Volumes', str(r / 'plain'), str(r), '/', '/tmp-not-used']
        for p in paths:
            theirs = self._theirs(Path(p))
            ours = self._ours(Path(p))
            self.assertEqual(ours, theirs, p)

    def _theirs(self, path):
        try:
            self.stage.refuse_protected_location(path)
        except self.stage.Refusal as exc:
            return exc.reason
        return None

    def _ours(self, path):
        try:
            lm.refuse_protected_location(path, 'dest')
        except lm.Refusal as exc:
            return exc.reason
        return None


# ---------------------------------------------------------------------------------------------------------------------
class NativeBot(unittest.TestCase):
    def test_bot_driver_passes_under_sanitizers(self):
        result = subprocess.run([str(binaries()['bot'])], capture_output=True, text=True, timeout=600)
        self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-2000:])
        self.assertTrue(result.stdout.rstrip().endswith('PASS'), result.stdout)
        print('bot driver |', result.stdout.strip().splitlines()[0], file=sys.stderr)

    def test_wrappers_and_cdbs_agree_with_an_independent_oracle(self):
        rng = random.Random(0x5701)
        lines, expect = [], []
        for _ in range(1500):
            tag, length = rng.getrandbits(32), rng.choice([0, 1, 13, 36, 512, 4096, rng.getrandbits(32)])
            direction, lun = (rng.choice([1, 2]) if length else 0), rng.choice([0, 1, 3, 15, 16, 31, 255])
            cdb = bytes(rng.getrandbits(8) for _ in range(rng.choice([1, 6, 10, 12, 16])))
            lines.append(f'B {tag:x} {length} {direction} {lun} {cdb.hex()}')
            expect.append(struct.pack('<IIIBBB', 0x43425355, tag, length, 0x80 if direction == 1 else 0, lun & 15,
                                      len(cdb) & 31) + cdb.ljust(16, b'\0')[:16])
        for _ in range(1500):
            tag, requested = rng.getrandbits(32), rng.choice([0, 8, 36, 512, 4096])
            sig = rng.choice([0x53425355, 0x53425355, 0x53425355, 0x43425355, rng.getrandbits(32)])
            sent_tag = tag if rng.random() < 0.8 else rng.getrandbits(32)
            status = rng.choice([0, 1, 2, 3, 255])
            residue = rng.choice([0, 1, requested, requested + 1, rng.getrandbits(32)])
            raw = struct.pack('<IIIB', sig, sent_tag, residue, status)
            if rng.random() < 0.1:
                raw = raw[:rng.choice([0, 5, 12])] if rng.random() < 0.5 else raw + b'\0'
            lines.append(f'C {raw.hex() or "-"} {tag:x} {requested}')
            if len(raw) != 13 or sig != 0x53425355 or sent_tag != tag or status > 2:
                expect.append('INVALID')
            elif status == 2:
                expect.append('PHASE')
            elif residue > requested:
                expect.append('INVALID')
            else:
                expect.append(f'OK {status} {residue}')
        for _ in range(1500):
            op = rng.choice('RW')
            lba = rng.choice([0, 1, 100, 0x01020304, 0xFFFFFFF0, 0xFFFFFFFE, 0xFFFFFFFF, rng.getrandbits(32)])
            blocks, fua = rng.choice([0, 1, 2, 7, 8, 9, 255]), rng.choice([0, 1])
            lines.append(f'Q {op} {lba} {blocks} {fua}')
            if not 1 <= blocks <= 8:
                expect.append('BAD ARGUMENT')
            elif lba + blocks > 0xFFFFFFFE + 1:
                expect.append('OUT OF RANGE')
            else:
                expect.append(struct.pack('>BBIBHB', 0x28 if op == 'R' else 0x2A, 0x08 if op == 'W' and fua else 0, lba, 0,
                                          blocks, 0).hex())
        for kind, arg in ((0, 0), (0, 1), (0, 0xFFFF), (1, 0), (1, 5), (2, 0x81), (2, 0x02), (2, 0)):
            lines.append(f'U {kind} {arg:x}')
            if kind == 0:
                expect.append(struct.pack('<BBHHH', 0xA1, 0xFE, 0, arg, 1).hex())
            elif kind == 1:
                expect.append(struct.pack('<BBHHH', 0x21, 0xFF, 0, arg, 0).hex())
            else:
                expect.append(struct.pack('<BBHHH', 0x02, 0x01, 0, arg, 0).hex())
        got = oracle_run(binaries()['bot'], lines)
        self.assertEqual(len(got), len(lines))
        for line, want, have in zip(lines, expect, got):
            want = want.hex() if isinstance(want, bytes) else want
            self.assertEqual(have, want, line)


class NativeLog(unittest.TestCase):
    def test_log_driver_passes_under_sanitizers(self):
        result = subprocess.run([str(binaries()['log'])], capture_output=True, text=True, timeout=600)
        self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-2000:])
        self.assertTrue(result.stdout.rstrip().endswith('PASS'), result.stdout)
        print('log driver |', result.stdout.strip().splitlines()[0], file=sys.stderr)

    def test_crc_sector_and_header_codecs_agree_with_python(self):
        rng = random.Random(0xC0DE)
        lines, expect = [], []
        for _ in range(800):
            data = bytes(rng.getrandbits(8) for _ in range(rng.choice([0, 1, 9, 60, 124, 480, 512])))
            lines.append('K ' + (data.hex() or '-'))
            expect.append(f'{lm.crc32(data):08x}')
        for _ in range(800):
            index, boot, seq = rng.choice([1, 2, 7, 2047]), rng.choice([0, 1, 9, 0xFFFFFFFF]), rng.choice([0, 3, 0xFFFFFFFF])
            payload = bytes(rng.getrandbits(8) for _ in range(rng.choice([0, 1, 100, 479, 480])))
            lines.append(f'E {index} {NONCE:x} {boot} {seq} ' + (payload.hex() or '-'))
            expect.append(lm.encode_log_sector(NONCE, index, boot, seq, payload).hex())
        for _ in range(1200):
            index = rng.choice([1, 5, 2047])
            sector = bytearray(lm.encode_log_sector(NONCE, index, rng.getrandbits(8), rng.getrandbits(8),
                                                    bytes(rng.getrandbits(8) for _ in range(rng.choice([0, 7, 480])))))
            for _ in range(rng.choice([0, 0, 1, 2])):
                sector[rng.choice([rng.randrange(512), rng.randrange(32)])] ^= 1 << rng.randrange(8)
            if rng.random() < 0.15:
                struct.pack_into('<H', sector, 0x18, rng.choice([480, 481, 0xFFFF]))
            nonce = NONCE if rng.random() < 0.85 else NONCE ^ 1
            lines.append(f'S {index} {nonce:x} {bytes(sector).hex()}')
            info = lm.decode_log_sector(bytes(sector), index, nonce)
            expect.append(f'{int(info["owned"])} {int(info["valid"])} {info["boot_id"]} {info["seq"]} {info["used"]}')
        got = oracle_run(binaries()['log'], lines)
        self.assertEqual(len(got), len(lines))
        bad = [(l[:60], w, h) for l, w, h in zip(lines, expect, got) if w != h]
        self.assertEqual(bad[:3], [])

    def test_map_parsers_agree_on_random_and_mutated_maps(self):
        rng = random.Random(0x4D41)
        base = lm.encode_map(NONCE, 16384, 2048, 8, 2304, 2048, [(8192, 2048)])
        specials = [0, 1, 2, 3, 7, 8, 127, 128, 129, 255, 256, 2047, 2048, 2049, 8191, 8192, 8193, 0x7FFFFFFF, 0x80000000,
                    0xFFFFFFFE, 0xFFFFFFFF, 16383, 16384, 16385, 10239, 10240, 10241, 2303, 2304, 2305, 4096, 0x10000, 0x10001]
        maps = [base]
        for _ in range(4000):
            raw = bytearray(base)
            kind = rng.random()
            if kind < 0.25:
                for _ in range(rng.choice([1, 2, 3])):
                    raw[rng.randrange(128)] ^= 1 << rng.randrange(8)
            else:
                for _ in range(rng.choice([1, 1, 2, 3])):
                    field = rng.choice([0x08, 0x0C, 0x18, 0x1C, 0x20, 0x24, 0x28, 0x2C, 0x30, 0x34, 0x38, 0x3C, 0x40, 0x44,
                                        0x48, 0x4C, 0x50, 0x54, 0x58])
                    struct.pack_into('<I', raw, field, rng.choice(specials))
                if rng.random() < 0.1:
                    struct.pack_into('<Q', raw, 0x10, rng.choice([0, 1, NONCE, 1 << 63]))
            if rng.random() < 0.8:
                struct.pack_into('<I', raw, 124, lm.crc32(bytes(raw[:124])))
            maps.append(bytes(raw))
        maps += [base[:n] for n in (0, 1, 127)] + [base + b'\0']

        def python_verdict(raw):
            try:
                return not lm.map_policy_findings(lm.decode_map(raw))
            except lm.FormatError:
                return False

        got = oracle_run(binaries()['log'], ['M ' + (m.hex() or '00') if m else 'M 00' for m in maps])
        self.assertEqual(len(got), len(maps))
        accepted = {i for i, g in enumerate(got) if g == 'OK'}
        for i, (m, g) in enumerate(zip(maps, got)):
            if not m:
                continue
            if i >= len(maps) - 4:
                continue
            self.assertEqual(python_verdict(m), g == 'OK', (i, g, m.hex()))
        self.assertGreater(len(accepted), 5)
        self.assertGreaterEqual(len({g for g in got if g != 'OK'}), 7, 'the generator must reach many different refusals')

    def test_header_check_agrees_with_python(self):
        rng = random.Random(0x4844)
        map_raw = lm.encode_map(NONCE, 16384, 2048, 8, 2304, 2048, [(8192, 2048)])
        m = lm.decode_map(map_raw)
        header = lm.encode_header(NONCE, 2048, 8192, 2048, 16384)
        lines, expect = [], []
        for _ in range(1500):
            h = bytearray(header)
            for _ in range(rng.choice([0, 1, 1, 2])):
                h[rng.choice([rng.randrange(64), rng.randrange(512)])] ^= 1 << rng.randrange(8)
            if rng.random() < 0.6:
                struct.pack_into('<I', h, 60, lm.crc32(bytes(h[:60])))
            lines.append(f'H {map_raw.hex()} {bytes(h).hex()}')
            try:
                parsed = lm.decode_header(bytes(h))
                ok = (parsed['nonce'] == m['nonce'] and parsed['region_sectors'] == m['region_sectors'] and
                      parsed['start_lba'] == m['runs'][0][0] and parsed['partition_lba'] == m['partition_lba'] and
                      parsed['capacity_sectors'] == m['capacity_sectors'] and parsed['sector_size'] == 512)
            except lm.FormatError:
                ok = False
            expect.append('OK' if ok else 'HEADER MISMATCH')
        got = oracle_run(binaries()['log'], lines)
        self.assertEqual([(w, h) for w, h in zip(expect, got) if w != h][:3], [])
        self.assertIn('OK', got)
        self.assertIn('HEADER MISMATCH', got)


class EndToEnd(unittest.TestCase):
    """The C writer writes into a disk image the Python tool made; the Python tool reads the result back."""

    def write_log(self, img, text, boot_id=1, first_index=1, chunk=0):
        directory = img.parent
        with fv.ImageFile(img) as im:
            vol = fv.open_volume(im)
            (directory / 'map.bin').write_bytes(vol.read_file(vol.lookup('SP11/SNAPLOG.MAP')))
        (directory / 'log.txt').write_bytes(text)
        args = [str(binaries()['log']), '--image', str(img), '--map', str(directory / 'map.bin'), '--boot-id', str(boot_id),
                '--first-index', str(first_index), '--text', str(directory / 'log.txt')]
        if chunk:
            args += ['--chunk', str(chunk)]
        return subprocess.run(args, capture_output=True, text=True, timeout=300)

    def region_of(self, img):
        with fv.ImageFile(img) as im:
            vol = fv.open_volume(im)
            return vol.read_file(vol.lookup('SP11/SNAPLOG.BIN'))

    def next_slot(self, img):
        region = self.region_of(img)
        top, newest = 0, (0, 0, 0)
        nonce = lm.decode_header(region[:SECTOR])['nonce']
        for index in range(1, len(region) // SECTOR):
            sec = lm.decode_log_sector(region[index * SECTOR:(index + 1) * SECTOR], index, nonce)
            if sec['valid'] and sec['boot_id']:
                top = max(top, sec['boot_id'])
                newest = max(newest, (sec['boot_id'], sec['seq'], index))
        return (newest[2] + 1 if newest[2] and newest[2] + 1 < len(region) // SECTOR else 1), top + 1

    def test_the_whole_flow_on_a_bare_volume_and_on_an_mbr_disk(self):
        for name, mbr in (('e2e-bare', False), ('e2e-mbr', True)):
            img, files = build_img(name, mbr=mbr)
            before = fresh(name + '-copy') / 'before.img'
            fv.copy_sparse(img, before)
            text = b''.join(b'EL2 line %05d: UPTIME %d S HCR 0X0000000088000010\n' % (i, i * 3) for i in range(400))
            result = self.write_log(img, text, boot_id=1, first_index=1, chunk=777)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('status=OK', result.stdout)
            self.assertIn('violation=0', result.stdout)
            report = lm.verify_image(img, allow_written=True)
            self.assertTrue(report['ok'], report)
            self.assertGreater(report['facts']['written_sectors'], 30)
            self.assertEqual(lm.verify_image(img)['findings'][0][0], 'region_not_pristine')   # no longer pristine
            self.assertEqual(lm.read_log(img)['text'], text)                                   # read back through the FAT
            # Nothing outside the region changed: every byte of the two images agrees except the written sectors.
            ora = Oracle(img, 2048 if mbr else 0)
            chain = ora.chain(ora.find('SP11/SNAPLOG.BIN')[0])
            lo, hi = ora.lba(chain[0]), ora.lba(chain[0]) + 2048
            a, b = before.stat().st_size, img.stat().st_size
            self.assertEqual(a, b)
            fd_a, fd_b = os.open(before, os.O_RDONLY), os.open(img, os.O_RDONLY)
            try:
                chunk = 1 << 20
                for offset in range(0, a, chunk):
                    x, y = os.pread(fd_a, chunk, offset), os.pread(fd_b, chunk, offset)
                    if x != y:
                        first = offset // SECTOR
                        for k in range(0, len(x), SECTOR):
                            if x[k:k + SECTOR] != y[k:k + SECTOR]:
                                self.assertTrue(lo < first + k // SECTOR < hi, f'sector {first + k // SECTOR} outside the region')
            finally:
                os.close(fd_a)
                os.close(fd_b)
            with fv.ImageFile(img) as im:
                vol = fv.open_volume(im)
                for p, d in files.items():
                    self.assertEqual(vol.read_file(vol.lookup(p)), d)
            if not mbr and has_fsck():
                ok, out = fsck_clean(img)
                self.assertTrue(ok, out)

    def test_three_boots_in_order_and_a_wrap(self):
        img, _ = build_img('e2e-boots', log_mib=1)
        texts = []
        for boot in range(1, 4):
            index, boot_id = self.next_slot(img)
            self.assertEqual(boot_id, boot)
            text = (f'BOOT {boot}\n'.encode() + bytes(range(65, 91)) * (40 * boot) + b'\n')
            texts.append(text)
            result = self.write_log(img, text, boot_id=boot_id, first_index=index)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        got = lm.read_log(img)
        self.assertEqual(got['text'], b''.join(texts))
        self.assertEqual(got['boots'], [1, 2, 3])
        self.assertEqual((got['torn'], got['foreign']), ([], []))
        # A boot that writes more than the region holds wraps and overwrites the oldest sectors, never the header.
        big = bytes((i * 7) % 251 + 1 for i in range(480 * 2100))
        index, boot_id = self.next_slot(img)
        result = self.write_log(img, big, boot_id=boot_id, first_index=index)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(lm.verify_image(img, allow_written=True)['ok'])
        tail = lm.read_log(img)['text']
        self.assertTrue(big.endswith(tail[-480 * 2000:]) and len(tail) >= 480 * 2000)

    def test_a_stale_map_cannot_hurt_the_file_system(self):
        """The log file is deleted and another file takes its clusters; the old map still points at them."""
        img, files = build_img('e2e-stale')
        with fv.ImageFile(img, writable=True) as im:
            vol = fv.open_volume(im)
            entry = vol.lookup('SP11/SNAPLOG.BIN')
            old = vol.chain(entry.first_cluster)
            for cluster in old:
                vol.fat_set(cluster, 0)
            vol.img.write(entry.offset, b'\xe5')
            victim = vol.create_file('SP11/OTHER.BIN', b'precious' * 60000)
        taken = Oracle(img, 2048).find('SP11/OTHER.BIN')
        self.assertIsNotNone(taken)
        self.assertIn(taken[0], old, 'the test needs the other file to sit where the log was')
        with fv.ImageFile(img) as im:
            region_start = fv.open_volume(im).cluster_lba(old[0])
        before = pread(img, region_start * 512, 1 << 20)
        result = self.write_log(img, b'must not be written\n' * 100)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertRegex(result.stdout, r'status=(HEADER MISMATCH|SECTOR NOT OURS)')
        self.assertEqual(pread(img, region_start * 512, 1 << 20), before)
        self.assertIn('violation=0', result.stdout)
        with fv.ImageFile(img) as im:
            vol = fv.open_volume(im)
            self.assertEqual(vol.read_file(vol.lookup('SP11/OTHER.BIN')), b'precious' * 60000)
            self.assertEqual(victim.size, 480000)


# ---------------------------------------------------------------------------------------------------------------------
# Seeded bugs
# ---------------------------------------------------------------------------------------------------------------------
BOT_MUTANTS = {
    'a device that needs recovery is sent another command':
        [('    if (b->needs_reset)\n        return BOT_NEEDS_RESET;\n', '')],
    'the status tag is not checked':
        [('if (get_le32(csw) != BOT_CSW_SIGNATURE || get_le32(csw + 4) != expected_tag)', 'if (get_le32(csw) != BOT_CSW_SIGNATURE)')],
    'the status signature is not checked':
        [('if (get_le32(csw) != BOT_CSW_SIGNATURE || get_le32(csw + 4) != expected_tag)', 'if (get_le32(csw + 4) != expected_tag)')],
    'a status wrapper of the wrong length is accepted':
        [('if (!csw || length != BOT_CSW_BYTES)', 'if (!csw)')],
    'a reserved status value is accepted':
        [('if (csw[12] > BOT_CSW_PHASE_ERROR)', 'if (csw[12] > 3)')],
    'a residue above the request is accepted':
        [('if (left > requested)', 'if (0)')],
    'a bad status does not demand a reset':
        [('    if (verdict != BOT_OK) {\n        b->needs_reset = 1;\n        return verdict;\n    }',
          '    if (verdict != BOT_OK)\n        return verdict;')],
    'residue is ignored':
        [('if (residue != 0 || got != length)', 'if (got != length)')],
    'a short data stage is ignored':
        [('if (residue != 0 || got != length)', 'if (residue != 0)')],
    'a data-stage stall clears the wrong endpoint':
        [('b->ops->clear_halt(b->ctx, dir == BOT_DIR_IN) != 0', 'b->ops->clear_halt(b->ctx, dir != BOT_DIR_IN) != 0')],
    'a data-stage stall is not cleared':
        [('            b->stalls_cleared++;\n        } else if (io != BOT_IO_OK || got > length) {',
          '        } else if (io != BOT_IO_OK || got > length) {')],
    'the tag 0 is used after a wrap':
        [('    if (!b->tag)\n        b->tag++;\n', '')],
    'the direction flag is inverted':
        [('out[12] = dir == BOT_DIR_IN ? BOT_FLAG_IN : 0;', 'out[12] = dir == BOT_DIR_OUT ? BOT_FLAG_IN : 0;')],
    'the LUN field is not masked':
        [('out[13] = (uint8_t)(lun & 0x0Fu);', 'out[13] = lun;')],
    'the CDB length field is not masked':
        [('out[14] = (uint8_t)(cdb_length & 0x1Fu);', 'out[14] = (uint8_t)cdb_length;')],
    'a range one block past the capacity is allowed':
        [('(uint64_t)lba + blocks > (uint64_t)d->last_lba + 1u', '(uint64_t)lba + blocks > (uint64_t)d->last_lba + 2u')],
    'the range arithmetic wraps at 32 bits':
        [('(uint64_t)lba + blocks > (uint64_t)d->last_lba + 1u', '(uint32_t)(lba + blocks) > d->last_lba + 1u')],
    'writes do not ask for force unit access':
        [('fua ? SCSI_FLAG_FUA : 0', '0')],
    'a 4096-byte block size is accepted':
        [('return d->block_len == SCSI_BLOCK_BYTES ? SCSI_OK : SCSI_BLOCK_SIZE;', 'return SCSI_OK;')],
    'a capacity over 32 bits is accepted':
        [('    if (last == 0xFFFFFFFFu)\n        return SCSI_CAPACITY;\n', '')],
    'an unconnected peripheral qualifier is accepted':
        [('if ((buf[0] >> 5) != 0 || d->device_type != 0)', 'if (d->device_type != 0)')],
    'a device type other than direct access is accepted':
        [('if ((buf[0] >> 5) != 0 || d->device_type != 0)', 'if ((buf[0] >> 5) != 0)')],
    'write protection is reported as a plain check condition':
        [('    if (st == SCSI_CHECK && d->sense.valid && d->sense.key == SCSI_KEY_DATA_PROTECT)\n        return SCSI_PROTECTED;\n', '')],
    'recovery does not clear the bulk-out halt':
        [('if (b->ops->clear_halt(b->ctx, 1) != 0 || b->ops->clear_halt(b->ctx, 0) != 0)', 'if (b->ops->clear_halt(b->ctx, 1) != 0)')],
    'recovery ignores a failed reset request':
        [('    if (b->ops->reset(b->ctx) != 0)\n        return BOT_RESET_FAILED;\n', '    b->ops->reset(b->ctx);\n')],
    'the sense key is not masked':
        [('d->sense.key = buf[2] & 0x0Fu;', 'd->sense.key = buf[2];')],
    'unit attention is not retried':
        [('(d->sense.key != SCSI_KEY_UNIT_ATTENTION && d->sense.key != SCSI_KEY_NOT_READY)', '(1)')],
    'a failed status stage is retried more than once':
        [('if (io != BOT_IO_STALL || attempt == 1)', 'if (io != BOT_IO_STALL)')],
}

LOG_MUTANTS = {
    'a sector is overwritten without checking its tag':
        [('        if (!info.owned)\n            return fail(l, SLOG_E_NOT_OWNED, SCSI_OK);\n', '')],
    'ownership ignores the nonce':
        [('info->owned = bytes_equal(s, sector_magic, 4) && get_le32(s + 4) == index && get_le64(s + 8) == nonce;',
          'info->owned = bytes_equal(s, sector_magic, 4) && get_le32(s + 4) == index;')],
    'ownership ignores the index':
        [('info->owned = bytes_equal(s, sector_magic, 4) && get_le32(s + 4) == index && get_le64(s + 8) == nonce;',
          'info->owned = bytes_equal(s, sector_magic, 4) && get_le64(s + 8) == nonce;')],
    'the read-back is not compared':
        [('    if (!bytes_equal(l->scratch, sector, SLOG_SECTOR_BYTES))\n        return fail(l, SLOG_E_READBACK, SCSI_OK);\n', '')],
    'the ring wraps one sector too late':
        [('l->index = l->index + 1 >= l->map.region_sectors ? 1 : l->index + 1;', 'l->index = l->index + 1 > l->map.region_sectors ? 1 : l->index + 1;')],
    'the ring wraps onto the header sector':
        [('l->index = l->index + 1 >= l->map.region_sectors ? 1 : l->index + 1;', 'l->index = l->index + 1 >= l->map.region_sectors ? 0 : l->index + 1;')],
    'the ring wraps onto the header sector and the bounds check is gone':
        [('l->index = l->index + 1 >= l->map.region_sectors ? 1 : l->index + 1;', 'l->index = l->index + 1 >= l->map.region_sectors ? 0 : l->index + 1;'),
         ('    if (index < 1 || index >= l->map.region_sectors)\n        return fail(l, SLOG_E_BOUNDS, SCSI_OK);\n', '')],
    'the capacity is not compared with the stick':
        [('if (dev->block_len != SLOG_SECTOR_BYTES || (uint64_t)dev->last_lba + 1u != m.capacity_sectors)', 'if (dev->block_len != SLOG_SECTOR_BYTES)')],
    'the header sector is not checked':
        [('if (slog_header_check(l->scratch, &m) != SLOG_OK) {', 'if (0) {')],
    'the header nonce is not compared':
        [('if (get_le64(s + 0x10) != m->nonce || get_le32(s + 0x18) != m->sector_size ||', 'if (get_le32(s + 0x18) != m->sector_size ||')],
    'the header start LBA is not compared':
        [('get_le32(s + 0x1C) != m->region_sectors || get_le32(s + 0x20) != m->run_start[0] ||', 'get_le32(s + 0x1C) != m->region_sectors ||')],
    'a failure does not disarm the writer':
        [('    l->armed = 0;\n    return l->error;', '    return l->error;')],
    'the write budget is not enforced':
        [('    if (l->stats.writes >= SLOG_WRITE_BUDGET)\n        return fail(l, SLOG_E_BUDGET, SCSI_OK);\n', '')],
    'flush never synchronizes the cache':
        [('    if (l->dirty) {\n        sst = scsi_sync10(l->dev);', '    if (0) {\n        sst = scsi_sync10(l->dev);')],
    'a fragmented map (two runs) is accepted':
        [('    if (m->run_count != 1)\n        return SLOG_E_MAP_RUNS;\n', '')],
    'the region end is computed in 32 bits':
        [('(uint64_t)m->run_start[0] + m->run_sectors[0] > m->capacity_sectors', '(uint32_t)(m->run_start[0] + m->run_sectors[0]) > m->capacity_sectors')],
    'the CRC polynomial is wrong':
        [('0xEDB88320u', '0xEDB88321u')],
    'the sector CRC does not cover the payload':
        [('put_le32(out + 0x1C, slog_crc32(slog_crc32(0, out, 0x1C), out + SLOG_TAG_BYTES, used));', 'put_le32(out + 0x1C, slog_crc32(0, out, 0x1C));')],
    'a new boot starts on the newest sector':
        [('*next_index = !newest || newest + 1 >= sectors ? 1 : newest + 1;', '*next_index = !newest ? 1 : newest;')],
    'a foreign sector does not stop the scan':
        [('        if (!info.owned)\n            return SLOG_E_NOT_OWNED;\n        if (!info.valid || !info.boot_id)', '        if (!info.valid || !info.boot_id)')],
    'the next boot id is not advanced':
        [('*next_boot_id = top_boot + 1;', '*next_boot_id = top_boot;')],
    'an unchanged sector is rewritten on every flush':
        [('if (l->used && l->used != l->flushed_used) {', 'if (l->used) {')],
    'the sequence number does not advance':
        [('        l->seq++;\n', '')],
    'a sector is not zero-padded':
        [('    for (i = 0; i < SLOG_SECTOR_BYTES; i++)\n        out[i] = 0;\n    copy_bytes(out, sector_magic, 4);',
          '    copy_bytes(out, sector_magic, 4);')],
    'a map with a nonzero reserved area is accepted':
        [('if (get_le32(raw + 0x34) != 0 || !all_zero(raw + 0x58, 36))', 'if (get_le32(raw + 0x34) != 0)')],
    'a map with a bad geometry alignment is accepted':
        [('m->run_start[0] < m->data_lba ||\n        (m->run_start[0] - m->data_lba) % cs)', 'm->run_start[0] < m->data_lba)')],
}


def mutate_tree(directory, filename, edits):
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True)
    for p in C_FILES:
        shutil.copy(p, directory / p.name)
    target = directory / filename
    text = target.read_text()
    for old, new in edits:
        if text.count(old) != 1:
            raise AssertionError(f'{filename}: pattern occurs {text.count(old)} times: {old[:70]!r}')
        text = text.replace(old, new)
    target.write_text(text)


def run_c_mutant(args):
    name, filename, edits, index = args
    directory = BUILD / 'mutants' / f'c{index}'
    mutate_tree(directory, filename, edits)
    caught = []
    for label, sources, driver in (('bot', ('test-sp11-stick-bot.c', 'sp11-stick-bot.c'), 'test-sp11-stick-bot'),
                                   ('log', ('test-sp11-stick-log.c', 'sp11-stick-log.c', 'sp11-stick-bot.c'), 'test-sp11-stick-log')):
        try:
            exe = compile_c(directory / driver, [directory / s for s in sources], [directory], sanitize=False, werror=False)
        except RuntimeError as error:
            return name, 'DOES NOT COMPILE: ' + str(error)[-300:]
        try:
            run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=120)
            if run.returncode != 0:
                caught.append(label)
        except subprocess.TimeoutExpired:
            caught.append(label + ' (hang)')
    return name, ','.join(caught) or None


class CMutants(unittest.TestCase):
    def test_a_copy_without_a_bug_passes_the_same_harness(self):
        """The control: an unchanged copy, built and run exactly like a mutant, must pass both drivers."""
        name, verdict = run_c_mutant(('control', 'sp11-stick-bot.c', [], 900))
        self.assertIsNone(verdict, verdict)

    def test_seeded_bugs_are_caught(self):
        work = [(n, 'sp11-stick-bot.c', e, i) for i, (n, e) in enumerate(BOT_MUTANTS.items())]
        work += [(n, 'sp11-stick-log.c', e, 100 + i) for i, (n, e) in enumerate(LOG_MUTANTS.items())]
        survivors, bad = [], []
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 2)) as pool:
            for name, verdict in pool.map(run_c_mutant, work):
                print(f'C mutant | {verdict or "SURVIVED":24} | {name}', file=sys.stderr)
                if verdict is None:
                    survivors.append(name)
                elif verdict.startswith('DOES NOT COMPILE'):
                    bad.append((name, verdict))
        self.assertEqual(bad, [])
        self.assertEqual(survivors, [])
        self.assertGreaterEqual(len(work), 40)


PY_MUTANTS = {
    'a fragmented file is not detected':
        ('sp11_stick_logmap.py', [("        if len(runs) != 1:\n            fail('fragmented', f'{len(runs)} runs')\n", '')]),
    'a map run that is not the file is not detected':
        ('sp11_stick_logmap.py', [("        if runs != m['runs']:", '        if False:')]),
    'the capacity is not compared':
        ('sp11_stick_logmap.py', [("        if m['capacity_sectors'] != device:", '        if False:')]),
    'the header signature is not checked':
        ('sp11_stick_logmap.py', [('    if magic != HEADER_MAGIC:', '    if False:')]),
    'a tag with another nonce is accepted':
        ('sp11_stick_logmap.py', [('owned = magic == SECTOR_MAGIC and got_index == index and got_nonce == nonce',
                                   'owned = magic == SECTOR_MAGIC and got_index == index')]),
    'the volume geometry is not compared with the map':
        ('sp11_stick_logmap.py', [("        if (m['cluster_sectors'], m['data_lba'], m['partition_lba']) != (vol.sectors_per_cluster, vol.data_lba, vol.part_start):",
                                   '        if False:')]),
    'the map CRC is not checked':
        ('sp11_stick_logmap.py', [("    if crc != crc32(raw[:MAP_BYTES - 4]):\n        raise FormatError('map_crc')\n", '')]),
    'the map magic is not checked':
        ('sp11_stick_logmap.py', [('    if magic != MAP_MAGIC:', '    if False:')]),
    'the file size is not compared with the region':
        ('sp11_stick_logmap.py', [("        if log_entry.size != m['region_sectors'] * SECTOR or log_entry.size != len(chain) * vol.cluster_bytes:",
                                   '        if False:')]),
    'a refused output is left behind':
        ('sp11_stick_logmap.py', [('def _discard(path):\n    try:\n        os.unlink(path)\n    except OSError:\n        pass\n',
                                   'def _discard(path):\n    pass\n')]),
    'an existing output is not refused':
        ('sp11_stick_logmap.py', [("    if absolute.exists():\n        raise Refusal('dest_exists', str(absolute))\n", '')]),
    'a parent reference is accepted':
        ('sp11_stick_logmap.py', [("    if '..' in path.parts:\n        raise Refusal('dest_has_parent_reference', str(path))\n", '')]),
    'a symbolic link is accepted':
        ('sp11_stick_logmap.py', [("    if symlinked == path:\n        raise Refusal('dest_is_symlink', str(path))\n", '')]),
    '/Volumes and /dev are not refused':
        ('sp11_stick_logmap.py', [('    lowered = path.as_posix().lower()\n    for root, reason in PROTECTED_ROOTS:\n',
                                   '    lowered = path.as_posix().lower()\n    for root, reason in ():\n')]),
    'the cluster-to-LBA conversion is off by one sector':
        ('sp11_fatvol.py', [('return self.data_lba + (cluster - 2) * self.sectors_per_cluster',
                             'return self.data_lba + (cluster - 2) * self.sectors_per_cluster + 1')]),
    'the root directory is left out of the data start (FAT16)':
        ('sp11_fatvol.py', [('self.data_start = rsvd + nfats * self.fat_sectors + self.root_sectors',
                             'self.data_start = rsvd + nfats * self.fat_sectors')]),
    # Without its loop check the walk would never end, so the mutant also gets a hop limit that raises something else.
    'a chain loop is not detected':
        ('sp11_fatvol.py', [("            if cluster in seen:\n                raise FatError('chain_loops', str(cluster))\n", ''),
                            ("            seen.add(cluster)\n            out.append(cluster)\n",
                             "            out.append(cluster)\n            if len(out) > 100000:\n                raise RuntimeError('runaway chain')\n")]),
    'a chain that leaves the volume is followed':
        ('sp11_fatvol.py', [("            if cluster < 2 or cluster > self.clusters + 1:\n                raise FatError('chain_leaves_volume', str(cluster))\n", '')]),
    'only the first FAT copy is updated':
        ('sp11_fatvol.py', [('copies = range(self.nfats) if self.mirrored else (self.active_fat,)', 'copies = (self.active_fat,)')]),
    'the free-run search finds one cluster too few':
        ('sp11_fatvol.py', [('            if run == count:', '            if run == count - 1:')]),
    'the GPT protective MBR is not refused':
        ('sp11_fatvol.py', [("    if any(p['type'] == 0xEE for p in parts):\n        raise FatError('gpt_unsupported', 'a protective MBR: this tool reads MBR disks and bare volumes only')\n", '')]),
    'the sector size is not checked':
        ('sp11_fatvol.py', [("        if bps != SECTOR:\n            raise FatError('unsupported_sector_size', str(bps))\n", '')]),
}


def run_python_mutant(args):
    name, filename, edits, index = args
    directory = BUILD / 'mutants' / f'py{index}'
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True)
    for module in ('sp11_fatvol.py', 'sp11_stick_logmap.py'):
        shutil.copy(TOOLS / module, directory / module)
    text = (directory / filename).read_text()
    for old, new in edits:
        if text.count(old) != 1:
            return name, f'PATTERN OCCURS {text.count(old)} TIMES: {old[:70]!r}'
        text = text.replace(old, new)
    (directory / filename).write_text(text)
    env = dict(os.environ, SP11_STICK_MODDIR=str(directory), SP11_STICK_WORK=str(directory / 'work'),
               PYTHONDONTWRITEBYTECODE='1')
    try:
        run = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()), '--battery'], cwd=ROOT, env=env,
                             capture_output=True, text=True, timeout=90)       # the real battery takes about a second
    except subprocess.TimeoutExpired:
        return name, 'caught (hang)'
    finally:
        shutil.rmtree(directory / 'work', ignore_errors=True)               # the battery's images are large; the sources stay
    if run.returncode == 0:
        return name, None
    if run.stdout.strip():
        return name, 'caught: ' + run.stdout.strip().splitlines()[0][:60]          # a check of the battery failed
    last = run.stderr.strip().splitlines()[-1:] or ['no output']
    return name, 'caught by a crash: ' + last[0][:60]


class PythonMutants(unittest.TestCase):
    def test_a_copy_without_a_bug_passes_the_same_harness(self):
        """The control: an unchanged copy, run exactly like a mutant, must pass, or every 'caught' above means nothing."""
        name, verdict = run_python_mutant(('control', 'sp11_fatvol.py', [], 900))
        self.assertIsNone(verdict, verdict)

    def test_seeded_bugs_are_caught(self):
        work = [(n, f, e, i) for i, (n, (f, e)) in enumerate(PY_MUTANTS.items())]
        survivors, unplaced = [], []
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, os.cpu_count() or 2)) as pool:
            for name, verdict in pool.map(run_python_mutant, work):
                print(f'py mutant | {verdict or "SURVIVED":40} | {name}', file=sys.stderr)
                if verdict is None:
                    survivors.append(name)
                elif verdict.startswith('PATTERN'):
                    unplaced.append((name, verdict))
        self.assertEqual(unplaced, [])
        self.assertEqual(survivors, [])
        self.assertGreaterEqual(len(work), 20)


# ---------------------------------------------------------------------------------------------------------------------
class Hygiene(unittest.TestCase):
    def test_licence_ascii_and_size(self):
        for path in (*C_FILES, *PY_FILES):
            text = path.read_text()
            self.assertIn('SPDX-License-Identifier: MIT', '\n'.join(text.splitlines()[:3]), path.name)
            self.assertTrue(text.isascii(), path.name)
        # AGENTS.md bounds new C/C++ implementation files (1,500 lines, 50 KiB); the two Python tools are held to the same.
        for path in (*C_FILES, *PY_FILES[:2]):
            text = path.read_text()
            self.assertLess(len(text.splitlines()), 1500, path.name)
            self.assertLess(len(text.encode()), 50 * 1024, path.name)

    def test_no_machine_identifying_strings(self):
        for path in (*C_FILES, *PY_FILES, DOC):
            if not path.exists():
                continue
            text = path.read_text()
            for prefix in ('/' + 'Users' + '/', '/' + 'home' + '/'):
                self.assertNotIn(prefix, text, path.name)
            self.assertIsNone(re.search(r'\b[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}\b', text), f'{path.name}: a MAC address shape')
            self.assertIsNone(re.search(r'\b\d{1,3}(\.\d{1,3}){3}\b', text), f'{path.name}: an IPv4 address shape')

    def test_model_is_pure(self):
        forbidden = (r'\bprintf\b', r'\bfopen\b', r'\bmalloc\b', r'\bcalloc\b', r'\brealloc\b', r'\bfree\s*\(', r'__asm__',
                     r'\basm\s*\(', r'\bsystem\s*\(', r'\bgetenv\b', r'\btime\s*\(', r'\brand\s*\(', r'\bsocket\b',
                     r'\bopen\s*\(', r'\bmemset\b', r'\bmemcpy\b', r'\bmemcmp\b', r'\bstrlen\b', r'static\s+uint8_t\s+buf')
        for path in (BOT_C, BOT_H, LOG_C, LOG_H):
            text = re.sub(r'/\*.*?\*/', '', path.read_text(), flags=re.S)
            for pattern in forbidden:
                self.assertIsNone(re.search(pattern, text), f'{path.name}: {pattern}')
            for include in re.findall(r'#include\s*[<"]([^>"]+)[>"]', text):
                self.assertIn(include, ('stddef.h', 'stdint.h', 'sp11-stick-bot.h', 'sp11-stick-log.h'), include)

    def test_gate_off_preprocesses_to_nothing_and_builds_to_nothing(self):
        for path in (BOT_H, BOT_C, LOG_H, LOG_C):
            out = subprocess.run(['cc', '-E', '-I', str(TOOLS), str(path)], capture_output=True, text=True, check=True).stdout
            own, current = [], ''
            for line in out.splitlines():
                marker = re.match(r'# \d+ "([^"]*)"', line)
                if marker:
                    current = marker.group(1)
                elif line.strip() and current.startswith(str(TOOLS)):
                    own.append(line)
            self.assertEqual(own, [], f'{path.name}: text of our own files survives with the gate off')
        BUILD.mkdir(parents=True, exist_ok=True)
        for path in (BOT_C, LOG_C):
            obj = BUILD / f'gate-off-{path.stem}.o'
            subprocess.run(['cc', '-std=gnu11', '-c', '-Wall', '-Wextra', '-Werror', '-Wpedantic', '-I', str(TOOLS), str(path),
                            '-o', str(obj)], check=True)
            self.assertEqual(subprocess.run(['nm', '-g', str(obj)], capture_output=True, text=True).stdout.strip(), '',
                             'the gate-off object must define nothing')

    def test_default_flags_do_not_define_the_gate(self):
        defined = subprocess.run(['cc', '-E', '-dM', '-I', str(TOOLS), str(LOG_H)], capture_output=True, text=True,
                                 check=True).stdout
        self.assertIn('SNAP_PLATFORM_SP11 0', defined)

    def test_the_models_compile_cleanly_with_the_gate_on(self):
        BUILD.mkdir(parents=True, exist_ok=True)
        for path in (BOT_C, LOG_C):
            subprocess.run(['cc', *CFLAGS, '-Werror', '-I', str(TOOLS), '-c', str(path), '-o', str(BUILD / (path.stem + '.o'))],
                           check=True)

    def test_freestanding_aarch64_objects_need_no_libc(self):
        if not Path(LLVM_NM).exists() or not Path(CLANG).exists():
            self.skipTest('SKIP: LLVM (clang, llvm-nm) is not installed')
        BUILD.mkdir(parents=True, exist_ok=True)
        defined, undefined = set(), set()
        for path in (BOT_C, LOG_C):
            obj = BUILD / f'freestanding-{path.stem}.o'
            subprocess.run([CLANG, '--target=aarch64-none-elf', '-ffreestanding', '-O2', '-std=gnu11', '-Wall', '-Wextra',
                            '-Werror', '-DSNAP_PLATFORM_SP11=1', '-I', str(TOOLS), '-c', str(path), '-o', str(obj)], check=True)
            for line in subprocess.run([LLVM_NM, str(obj)], capture_output=True, text=True, check=True).stdout.splitlines():
                fields = line.split()
                (undefined if fields[-2:-1] == ['U'] else defined).add(fields[-1])
        # The writer calls the SCSI layer, so the two objects are judged together: what is left must be nothing.
        self.assertEqual(sorted(undefined - defined), [], 'the pair must link into a freestanding EL2 payload without libc')
        self.assertIn('slog_append', defined)
        self.assertIn('bot_reset_recovery', defined)

    def test_not_linked_into_any_build(self):
        for path in (ROOT / 'tools' / 'cycle.py', ROOT / 'probes' / 'run.py', ROOT / 'tools' / 'build-uefi-probe.py',
                     ROOT / 'tools' / 'build-uefi-tests.py'):
            if path.exists():
                text = path.read_text()
                self.assertNotIn('sp11-stick', text, path.name)
                self.assertNotIn('sp11_stick', text, path.name)

    def test_the_tool_never_opens_a_device_or_a_volume(self):
        for path in (TOOLS / 'sp11_fatvol.py', TOOLS / 'sp11_stick_logmap.py'):
            text = path.read_text()
            self.assertNotIn("open('/dev", text)
            self.assertNotIn('rdisk', text)
            self.assertNotIn('diskutil', text)
            self.assertNotIn('hdiutil', text)
            self.assertNotIn('tempfile', text)
            self.assertNotIn('subprocess', text)


# ---------------------------------------------------------------------------------------------------------------------
# The design document
# ---------------------------------------------------------------------------------------------------------------------
LABELS = ('VERIFIED', 'OBSERVED', 'INFERRED', 'EXPERIMENTAL', 'UNIMPLEMENTED', 'UNKNOWN')
REFS = BUILD / 'refs'
LINUX_REF_LINES = (
    # (file in build/sp11-stick-log/refs, line, regex): every Linux and EDK2 fact the study cites by line.
    ('hamoa.dtsi', 5254, r'usb_1_ss0: usb@a600000'), ('hamoa.dtsi', 5255, r'qcom,x1e80100-dwc3'),
    ('hamoa.dtsi', 5256, r'0x0a600000 0 0xfc100'), ('hamoa.dtsi', 5307, r'apps_smmu 0x1420 0x0'),
    ('hamoa.dtsi', 5320, r'dma-coherent'), ('hamoa.dtsi', 5345, r'usb_1_ss1: usb@a800000'),
    ('hamoa.dtsi', 5398, r'apps_smmu 0x1460 0x0'), ('hamoa.dtsi', 5411, r'dma-coherent'),
    ('hamoa.dtsi', 758, r'gcc: clock-controller@100000'), ('hamoa.dtsi', 760, r'0x00100000 0 0x200000'),
    ('hamoa.dtsi', 8436, r'apps_smmu: iommu@15000000'), ('hamoa.dtsi', 8437, r'qcom,x1e80100-smmu-500'),
    ('hamoa.dtsi', 8438, r'0x15000000 0 0x100000'), ('hamoa.dtsi', 2819, r'usb_1_ss0_hsphy: phy@fd3000'),
    ('hamoa.dtsi', 2833, r'usb_1_ss0_qmpphy: phy@fd5000'), ('hamoa.dtsi', 2892, r'usb_1_ss1_hsphy: phy@fd9000'),
    ('hamoa.dtsi', 2906, r'usb_1_ss1_qmpphy: phy@fda000'),
    ('x1-el2.dtso', 10, r'&apss_watchdog'), ('x1-el2.dtso', 25, r'this IOMMU is controlled by the firmware'),
    ('x1-el2.dtso', 51, r'&pcie_smmu'), ('x1-el2.dtso', 56, r'iommus = <&apps_smmu 0x1000 0x80>'),
    ('x1-el2.dtso', 67, r'&sbsa_watchdog'),
    ('hamoa.dtsi', 5258, r'clocks = <&gcc GCC_CFG_NOC_USB3_PRIM_AXI_CLK>'), ('hamoa.dtsi', 5279, r'assigned-clock-rates = <19200000>'),
    ('hamoa.dtsi', 5293, r'power-domains = <&gcc GCC_USB30_PRIM_GDSC>'), ('hamoa.dtsi', 5294, r'required-opps'),
    ('hamoa.dtsi', 5296, r'resets = <&gcc GCC_USB30_PRIM_BCR>'), ('hamoa.dtsi', 5298, r'interconnects = <&usb_south_anoc MASTER_USB3_0'),
    ('dwc3-qcom.c', 26, r'QSCRATCH_HS_PHY_CTRL\s+0x10'), ('dwc3-qcom.c', 30, r'QSCRATCH_SS_PHY_CTRL\s+0x30'),
    ('dwc3-qcom.c', 41, r'SDM845_QSCRATCH_BASE_OFFSET\s+0xf8800'),
    ('gcc-x1e80100.c', 6739, r'gcc_usb30_prim_gdsc'), ('gcc-x1e80100.c', 6740, r'gdscr = 0x39004'),
    ('gcc-x1e80100.c', 6751, r'gcc_usb30_sec_gdsc'), ('gcc-x1e80100.c', 6752, r'gdscr = 0xa1004'),
    ('gcc-x1e80100.c', 5368, r'gcc_usb30_prim_master_clk'), ('gcc-x1e80100.c', 5369, r'halt_reg = 0x39018'),
    ('gcc-x1e80100.c', 5386, r'gcc_usb30_prim_mock_utmi_clk'), ('gcc-x1e80100.c', 5387, r'halt_reg = 0x39028'),
    ('gcc-x1e80100.c', 5404, r'gcc_usb30_prim_sleep_clk'), ('gcc-x1e80100.c', 5405, r'halt_reg = 0x39024'),
    ('gcc-x1e80100.c', 2800, r'gcc_cfg_noc_usb3_prim_axi_clk'), ('gcc-x1e80100.c', 2801, r'halt_reg = 0x3908c'),
    ('gcc-x1e80100.c', 2492, r'gcc_aggre_usb3_prim_axi_clk'), ('gcc-x1e80100.c', 2493, r'halt_reg = 0x39090'),
    ('gcc-x1e80100.c', 5417, r'gcc_usb30_sec_master_clk'), ('gcc-x1e80100.c', 5418, r'halt_reg = 0xa1018'),
    ('gcc-x1e80100.c', 5435, r'gcc_usb30_sec_mock_utmi_clk'), ('gcc-x1e80100.c', 5436, r'halt_reg = 0xa1028'),
    ('gcc-x1e80100.c', 5453, r'gcc_usb30_sec_sleep_clk'), ('gcc-x1e80100.c', 5454, r'halt_reg = 0xa1024'),
    ('gcc-x1e80100.c', 2820, r'gcc_cfg_noc_usb3_sec_axi_clk'), ('gcc-x1e80100.c', 2821, r'halt_reg = 0xa108c'),
    ('gcc-x1e80100.c', 2512, r'gcc_aggre_usb3_sec_axi_clk'), ('gcc-x1e80100.c', 2513, r'halt_reg = 0xa1090'),
    ('gcc-x1e80100.c', 2612, r'gcc_aggre_usb_noc_axi_clk'), ('gcc-x1e80100.c', 2613, r'halt_reg = 0x2d034'),
    ('gcc-x1e80100.c', 5106, r'gcc_sys_noc_usb_axi_clk'), ('gcc-x1e80100.c', 5107, r'halt_reg = 0x2d014'),
    ('gcc-x1e80100.c', 2875, r'gcc_cfg_noc_usb_anoc_north_ahb_clk'), ('gcc-x1e80100.c', 2876, r'halt_reg = 0x2d028'),
    ('gcc-x1e80100.c', 2890, r'gcc_cfg_noc_usb_anoc_south_ahb_clk'), ('gcc-x1e80100.c', 2891, r'halt_reg = 0x2d02c'),
    ('gdsc.c', 21, r'PWR_ON_MASK\s+BIT\(31\)'), ('gdsc.c', 27, r'SW_COLLAPSE_MASK\s+BIT\(0\)'),
    ('clk-branch.h', 63, r'CBCR_CLK_OFF\s+BIT\(31\)'),
    ('arm-smmu.h', 36, r'sCR0_EXIDENABLE'), ('arm-smmu.h', 55, r'ID0_NUMSIDB'), ('arm-smmu.h', 56, r'ID0_EXIDS'),
    ('arm-smmu.h', 101, r'SMR_VALID\s+BIT\(31\)'), ('arm-smmu.h', 102, r'SMR_MASK\s+GENMASK\(31, 16\)'),
    ('arm-smmu.h', 103, r'SMR_ID\s+GENMASK\(15, 0\)'), ('arm-smmu.h', 119, r'S2CR_EXIDVALID'),
    ('arm-smmu.h', 120, r'S2CR_CBNDX\s+GENMASK\(7, 0\)'),
    ('arm-smmu.h', 27, r'ARM_SMMU_GR0_sCR0\s+0x0'), ('arm-smmu.h', 33, r'USFCFG'), ('arm-smmu.h', 39, r'CLIENTPD'),
    ('arm-smmu.h', 45, r'ARM_SMMU_GR0_ID0\s+0x20'), ('arm-smmu.h', 57, r'NUMSMRG'), ('arm-smmu.h', 59, r'ARM_SMMU_GR0_ID1\s+0x24'),
    ('arm-smmu.h', 79, r'ARM_SMMU_GR0_ID7\s+0x3c'), ('arm-smmu.h', 83, r'ARM_SMMU_GR0_sGFSR\s+0x48'),
    ('arm-smmu.h', 100, r'ARM_SMMU_GR0_SMR\(n\)\s+\(0x800'), ('arm-smmu.h', 105, r'ARM_SMMU_GR0_S2CR\(n\)\s+\(0xc00'),
    ('arm-smmu.h', 113, r'S2CR_TYPE\s+GENMASK\(17, 16\)'), ('arm-smmu.h', 115, r'S2CR_TYPE_TRANS'),
    ('arm-smmu.h', 116, r'S2CR_TYPE_BYPASS'), ('arm-smmu.h', 117, r'S2CR_TYPE_FAULT'),
    ('arm-smmu.h', 203, r'ARM_SMMU_CB_FSR_FORMAT\s+GENMASK\(10, 9\)'), ('arm-smmu.h', 218, r'ARM_SMMU_CB_FSR_FAULT\s+\(ARM_SMMU_CB_FSR_MULTI'),
    ('arm-smmu.h', 219, r'ARM_SMMU_CB_FSR_SS'), ('arm-smmu.h', 220, r'ARM_SMMU_CB_FSR_UUT'), ('arm-smmu.h', 221, r'ARM_SMMU_CB_FSR_EF'),
    ('arm-smmu.h', 222, r'ARM_SMMU_CB_FSR_PF'), ('arm-smmu.h', 223, r'ARM_SMMU_CB_FSR_TF'), ('arm-smmu.h', 224, r'ARM_SMMU_CB_FSR_IGN\)'),
    ('arm-smmu.h', 147, r'ARM_SMMU_SCTLR_CFCFG\s+BIT\(7\)'), ('arm-smmu.h', 149, r'ARM_SMMU_SCTLR_CFIE\s+BIT\(6\)'),
    ('arm-smmu.h', 150, r'ARM_SMMU_SCTLR_CFRE\s+BIT\(5\)'), ('arm-smmu.h', 154, r'ARM_SMMU_SCTLR_M\s+BIT\(0\)'),
    ('core.h', 102, r'DWC3_GCTL\s+0xc110'), ('core.h', 104, r'DWC3_GSTS\s+0xc118'), ('core.h', 106, r'DWC3_GSNPSID\s+0xc120'),
    ('core.h', 110, r'DWC3_GBUSERRADDR0\s+0xc130'), ('core.h', 138, r'DWC3_GUSB2PHYCFG'), ('core.h', 143, r'DWC3_GUSB3PIPECTL'),
    ('core.h', 264, r'DWC3_GCTL_PRTCAPDIR'), ('core.h', 265, r'DWC3_GCTL_PRTCAP_HOST\s+1'),
    ('core.h', 293, r'DWC3_GSTS_CSR_TIMEOUT'), ('core.h', 294, r'DWC3_GSTS_BUS_ERR_ADDR_VLD'),
    ('core.h', 295, r'DWC3_GSTS_CURMOD\(n\)'), ('core.h', 297, r'DWC3_GSTS_CURMOD_HOST\s+1'),
    ('storage.h', 63, r'US_BULK_CB_SIGN\s+0x43425355'), ('storage.h', 62, r'US_BULK_CB_WRAP_LEN\s+31'),
    ('storage.h', 75, r'US_BULK_CS_WRAP_LEN\s+13'), ('storage.h', 76, r'US_BULK_CS_SIGN\s+0x53425355'),
    ('storage.h', 77, r'US_BULK_STAT_OK\s+0'), ('storage.h', 78, r'US_BULK_STAT_FAIL\s+1'), ('storage.h', 79, r'US_BULK_STAT_PHASE\s+2'),
    ('storage.h', 82, r'US_BULK_RESET_REQUEST\s+0xff'), ('storage.h', 83, r'US_BULK_GET_MAX_LUN\s+0xfe'),
    ('storage.h', 32, r'USB_PR_BULK\s+0x50'), ('storage.h', 33, r'USB_PR_UAS\s+0x62'),
    ('scsi_proto.h', 21, r'TEST_UNIT_READY\s+0x00'), ('scsi_proto.h', 23, r'REQUEST_SENSE\s+0x03'), ('scsi_proto.h', 34, r'INQUIRY\s+0x12'),
    ('scsi_proto.h', 49, r'READ_CAPACITY\s+0x25'), ('scsi_proto.h', 50, r'READ_10\s+0x28'), ('scsi_proto.h', 51, r'WRITE_10\s+0x2a'),
    ('scsi_proto.h', 62, r'SYNCHRONIZE_CACHE\s+0x35'),
    ('UsbMassBot.h', 19, r'USB_BOT_RESET_REQUEST\s+0xFF'), ('UsbMassBot.h', 20, r'USB_BOT_GETLUN_REQUEST\s+0xFE'),
    ('UsbMassBot.h', 21, r'USB_BOT_CBW_SIGNATURE\s+0x43425355'), ('UsbMassBot.h', 22, r'USB_BOT_CSW_SIGNATURE\s+0x53425355'),
    ('UsbMassImpl.c', 16, r'mUsbCbi0Transport'), ('UsbMassImpl.c', 17, r'mUsbCbi1Transport'), ('UsbMassImpl.c', 18, r'mUsbBotTransport'),
    ('UsbEnumer.c', 124, r'UsbNode.Header.Type'), ('UsbEnumer.c', 126, r'UsbNode.ParentPortNumber\s+=\s+Device->ParentPort'),
    ('UsbEnumer.c', 127, r'UsbNode.InterfaceNumber'), ('UsbEnumer.c', 134, r'AppendDevicePathNode\s+\(HubIf->DevicePath'),
    ('UsbEnumer.c', 1027, r'for \(Index = 0; Index < HubIf->NumOfPort; Index\+\+\)'),
    ('UsbEnumer.c', 1080, r'for \(Index = 0; Index < RootHub->NumOfPort; Index\+\+\)'),
    ('Xhci.c', 1901, r'One notified function to stop the Host Controller when gBS->ExitBootServices'),
    ('Xhci.c', 1926, r'XhcHaltHC \(Xhc, XHC_GENERIC_TIMEOUT\)'), ('Xhci.c', 1932, r'XhcClearBiosOwnership'),
    ('Xhci.c', 2112, r'XhcExitBootService'),
)


def doc_text():
    return DOC.read_text() if DOC.exists() else ''


@unittest.skipUnless(DOC.exists(), 'SKIP: docs/SP11-STICK-LOG.md is not present')
class Document(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = doc_text()

    def test_plain_ascii_and_no_machine_data(self):
        self.assertTrue(self.text.isascii())
        self.assertIsNone(re.search(r'[^\x00-\x7f]', self.text))

    def test_relative_links_and_anchors_resolve(self):
        def slug(head):
            head = re.sub(r'[`*]', '', head.strip().lower())
            return re.sub(r'\s', '-', re.sub(r'[^a-z0-9 _-]', '', head))
        for target, anchor in re.findall(r'\]\(([^)#:]+\.md)(?:#([^)]*))?\)', self.text):
            path = DOC.parent / target
            self.assertTrue(path.is_file(), target)
            if anchor:
                heads = re.findall(r'^#{1,6} (.*)$', path.read_text(), re.M)
                self.assertIn(anchor, {slug(h) for h in heads}, f'{target}#{anchor}')
        heads = {slug(h) for h in re.findall(r'^#{1,6} (.*)$', self.text, re.M)}
        for anchor in re.findall(r'\]\(#([^)]+)\)', self.text):
            self.assertIn(anchor, heads, f'#{anchor}')

    def test_it_is_a_study_not_a_result(self):
        for phrase in ('Hardware status: NOT RUN', 'UNIMPLEMENTED', 'EXPERIMENTAL', 'UNKNOWN', 'INFERRED', 'VERIFIED'):
            self.assertIn(phrase, self.text)

    def test_every_measurement_and_stage_is_present(self):
        for n in range(1, 11):
            self.assertRegex(self.text, rf'\bM{n}\b')
        for s in ('S0', 'S1', 'S2', 'S3', 'S4', 'S5', 'S6'):
            self.assertRegex(self.text, rf'\*\*{s}\b')
        for word in ('go/no-go', 'Stop criterion', 'bulk-only', 'UAS', 'DRAM retention', 'SetVariable', 'camera',
                     'fail closed', 'power-cycle', 'READ CAPACITY', 'INQUIRY', 'ExitBootServices'):
            self.assertIn(word.lower(), self.text.lower(), word)

    def test_every_alternative_is_judged(self):
        for marker in ('(i)', '(ii)', '(iii)', '(iv)', '(v)'):
            self.assertIn(marker, self.text)

    def test_format_constants_match_the_code(self):
        block = re.search(r'```text format-constants\n(.*?)```', self.text, re.S)
        self.assertIsNotNone(block, 'the document needs its machine-checked constants block')
        consts = dict(line.split(' = ', 1) for line in block.group(1).strip().splitlines())
        header_h, bot_h = LOG_H.read_text(), BOT_H.read_text()

        def macro(text, name):
            return re.search(rf'#define {name}\s+(?:UINT32_C\()?([0-9A-Fa-fxu]+)', text).group(1).rstrip('u')

        pairs = {'MAP_BYTES': (lm.MAP_BYTES, 'SLOG_MAP_BYTES'), 'HEADER_BYTES': (lm.HEADER_BYTES, 'SLOG_HEADER_BYTES'),
                 'SECTOR_BYTES': (lm.SECTOR, 'SLOG_SECTOR_BYTES'), 'PAYLOAD_BYTES': (lm.PAYLOAD_BYTES, 'SLOG_PAYLOAD_BYTES'),
                 'TAG_BYTES': (32, 'SLOG_TAG_BYTES'), 'VERSION': (lm.VERSION, 'SLOG_VERSION'),
                 'MAX_RUNS': (lm.MAX_RUNS, 'SLOG_MAX_RUNS'), 'WRITE_BUDGET': (4096, 'SLOG_WRITE_BUDGET')}
        for key, (py, cname) in pairs.items():
            self.assertEqual(int(consts[key], 0), py, key)
            self.assertEqual(int(macro(header_h, cname), 0), py, cname)
        self.assertEqual(consts['MAP_MAGIC'], lm.MAP_MAGIC.decode())
        self.assertEqual(consts['HEADER_MAGIC'], lm.HEADER_MAGIC.decode())
        self.assertEqual(consts['SECTOR_MAGIC'], lm.SECTOR_MAGIC.decode())
        self.assertEqual(consts['LOG_FILE'], f'{lm.LOG_DIR}/{lm.LOG_NAME}')
        self.assertEqual(consts['MAP_FILE'], f'{lm.LOG_DIR}/{lm.MAP_NAME}')
        self.assertEqual(int(consts['CBW_BYTES']), int(macro(bot_h, 'BOT_CBW_BYTES')))
        self.assertEqual(int(consts['CSW_BYTES']), int(macro(bot_h, 'BOT_CSW_BYTES')))
        self.assertEqual(int(consts['CBW_SIGNATURE'], 16), int(macro(bot_h, 'BOT_CBW_SIGNATURE'), 16))
        self.assertEqual(int(consts['CSW_SIGNATURE'], 16), int(macro(bot_h, 'BOT_CSW_SIGNATURE'), 16))
        for name, value in (('SCSI_OP_INQUIRY', 0x12), ('SCSI_OP_READ_10', 0x28), ('SCSI_OP_WRITE_10', 0x2A),
                            ('SCSI_OP_READ_CAPACITY_10', 0x25), ('SCSI_OP_SYNCHRONIZE_CACHE_10', 0x35),
                            ('SCSI_OP_TEST_UNIT_READY', 0), ('SCSI_OP_REQUEST_SENSE', 3)):
            self.assertEqual(int(macro(bot_h, name), 0), value, name)
            self.assertEqual(int(consts[name.replace('SCSI_OP_', 'OP_')], 0), value, name)

    def test_pinned_facts_read_back(self):
        if not (REFS / 'hamoa.dtsi').is_file():
            self.skipTest('SKIP: the pinned Linux and EDK2 files are not under build/sp11-stick-log/refs/')
        problems = []
        for name, line, pattern in LINUX_REF_LINES:
            path = REFS / name
            if not path.is_file():
                problems.append((name, line, 'not fetched'))
                continue
            lines = path.read_text(encoding='utf-8').splitlines()
            if not 0 < line <= len(lines) or not re.search(pattern, lines[line - 1]):
                problems.append((name, line, lines[line - 1].strip()[:70] if 0 < line <= len(lines) else 'beyond the end'))
        self.assertEqual(problems, [])

    def test_every_cited_line_is_in_this_test(self):
        """A citation the study makes as file:line must be one the test reads back, whatever the file."""
        known = {(n, l) for n, l, _ in LINUX_REF_LINES}
        cited = {(m.group(1), int(m.group(2)))
                 for m in re.finditer(r'`([A-Za-z0-9_.-]+\.(?:c|h|S|dtsi|dtso|dts|py|md)):(\d+)`', self.text)}
        self.assertEqual(sorted(cited - known), [], 'cited, but not read back by this test')
        self.assertIsNone(re.search(r'`:\d+`', self.text), 'an abbreviated citation cannot be read back')
        self.assertGreater(len(cited), 60)

    def test_section_17_keeps_the_facts_of_boots_17_18a_and_19(self):
        """Section 17 transcribes three pages read from videos and counts on them in sections 1 and 9: a drifted value or count fails here."""
        body = self.text.split('## 17. Result of boots 17, 18A and 19', 1)[1].split('## 18. Result of boot 20', 1)[0]
        flat = ' '.join(body.split())
        pages = re.findall(r'```text\n(.*?)```', body, re.S)
        self.assertEqual([len(page.splitlines()) for page in pages], [21, 33, 19], 'the pages of boots 17, 18A and 19')
        snapshot = pages[1]
        payload_lines = [line for line in snapshot.splitlines() if line.startswith('PAYLOAD USBSNAP ')]
        self.assertEqual(len(payload_lines), 28)
        self.assertEqual(int(re.search(r' END .*LINES=(\d+)', snapshot).group(1)), len(payload_lines), 'END counts the lines of the page')
        for phrase in (
                # boot 19: the pre-flight read of usb_1_ss1, the base reading, the ignored cue and arena 2
                'PAYLOAD XHCI U1 CMD=0X0 STS=0X11:HCH,PCD CRCR=0X0 DCBAAP=0X0 CFG=0X0',
                'PAYLOAD XHCI BASE U0P1=0X20002A0 U0P2=0X20002A0 U1P1=0X20002A0 U1P2=0X24002C0',
                'PAYLOAD XHCI WATCH DONE CHANGED=NONE N=0', 'PAYLOAD XHCI NEXT ARENA 2 STACK 0XCE5D9000 PAGES 3 AFTER 8 S',
                # boot 17: the cable's port and arena 1
                'PAYLOAD XHCI WATCH DONE CHANGED=U0P2 N=2', 'PAYLOAD XHCI NEXT ARENA 1 FW 0XCF412000 PAGES 3 AFTER 8 S',
                # the stick's firmware path: boot 16, then boots 17, 18A and 19
                'Pci(0x80,0x0)/USB(0x1,0x0)', 'Pci(0x81,0x0)/USB(0x1,0x0)',
                # boot 18A: the NoC clock words, the drift check and the end of the page
                'PAYLOAD USBSNAP NOC AHB=0.ON NAXI=1.ON SAXI=1.ON', 'PAYLOAD USBSNAP DRIFT NONE CMP=84',
                'PAYLOAD USBSNAP END LOADS=334 FAULTS=0 SKIPS=0 BLK=0 CUTS=0 GAP_MS=8004 LINES=28',
                # SC3 after boots 15, 16, 17 and 19
                'the count is 4 of 5 (3 if boot 15 does not count'):
            self.assertIn(phrase, flat, phrase)
        self.assertIsNone(re.search(r'\b[0-9a-fA-F]{32,}\b', body), 'section 17 gives no record digest')
        self.assertEqual([pages[i].rstrip().splitlines()[-1] for i in (0, 2)], ['PAYLOAD XHCI NEXT RUN AFTER 8 S'] * 2, 'boots 17 and 19 end at NEXT RUN')

    ROW_G = ('held: a host build comparison on 2026-11-18 showed the default A16 monitor build (four flag sets) byte-identical between revisions 87da59673c9a and ea593683db78 '
             '(VERIFIED, host; says nothing about hardware); the monitor sources probes/ compat/ shadow/ config/ gpu/ drivers/ are also unchanged by git diff between 1e938a7 and ea59368')

    @classmethod
    def section_18_problems(cls, text):
        """What is wrong with section 18, boot 20 (the first run of the SP11 monitor entry), or with the places that count on it; an empty list is a pass."""
        body = text.split('## 18. Result of boot 20', 1)[1].split('## 19. Result of boot 21', 1)[0]
        flat = ' '.join(body.split())
        problems = []
        pages = re.findall(r'```text\n(.*?)```', body, re.S)
        if [len(page.splitlines()) for page in pages] != [76]:
            return ['the page of boot 20 is not one block of 76 lines']
        page = pages[0]
        lines = page.splitlines()
        for prefix, index in (('SNAPMON ', 0), ('SNAPREP ', 1)):
            counted = re.search(rf'PAYLOAD DONE INDEX={index} .* LINES=(\d+)', page)
            if not counted or int(counted.group(1)) != len([line for line in lines if line.startswith(prefix)]):
                problems.append(f'{prefix.strip()} lines are not the LINES= count of PAYLOAD DONE INDEX={index}')
        ticks = [int(n) for n in re.findall(r'MON RET=0X0 STATUS=0X0 ENTERED=1 TICKS=(\d+) STACK=8736/262144', page)]
        if ticks != [96720173, 96720173]:
            problems.append(f'the call is not 96,720,173 ticks on both lines: {ticks}')
        elif not (ticks[0] - 96_000_000 == 720173 and abs(ticks[0] / 19_200_000 - 5.0375) < 5e-5):
            problems.append('the arithmetic of the call')
        if 0x20000 + 316783 != 447855 or 'BYTES=447855' not in page or 'OFF=0X20000 BYTES=316783' not in page:
            problems.append('SNAPMON.BIN is not the offset of the monitor image plus its size')
        for needle in ('MON STATUS=OK ENTERED=0X1', 'MON COUNTS EVENTS=512 TRAPS=3127 ABORTS=0 STEPS=0 CALLS=1443 DROPPED=834', 'MON STOP WATCHDOG PC=0XFFFFFE000C1EEF00 A=0X0 B=0X0 C=0XD3AA0A80',
                       'MON EXIT PC=0XFFFFFE000C1EEF00 SPSR=0X804003C4 ESR=0X0 FAR=0X0', 'MON REGS RESTORED=YES', 'GIC RESTORE CLEAN=YES FAILED=0X0 RETRIED=0X0 BEGUN=GRC VBAR=NONE EL1=NONE',
                       'MON VERDICT=RETURNED FLAGS=0XF SUB=7', 'PAYLOAD CALL 0 1D32F5071506A00C', 'PAYLOAD CALL 1 EE86676001D89CF6', 'PAYLOAD VERIFY=OK', 'PAYLOAD READY FILES=5 RANGES=8 CODE=2',
                       'SNAPMON NEXT GIC STORES AFTER 8 S G=1 R=1 C=1 T=0 V=0 E=0', 'SNAPMON STAGE=PIN_OK LABEL=M1RUN-RUNG1 CHUNKS=44', 'SNAPREP GIC NEW GICD=0X13 RB=0X12 ISEN=0X4000000 PRIO=0X80 PMR=0XF8 IGRPEN1=1',
                       'SNAPREP MON CON DARWIN KERNEL VERSION 27.0.0: TUE AUG 11 21:05:57 PDT 2026? ROOT', 'SNAPREP MON CON :XNU-13432.1.9?1/RELEASE_ARM64_T8140?',
                       'SNAPREP MON CON VM BOOTSTRAP: 73 MAPS? 256 ENTRIES AND 64 NODES AVAILABLE?', 'PAYLOAD DONE INDEX=1 RETURN=0X0 STACK=OK LOADS=1 FAULTS=0 REFUSED=0 LINES=35 DROPPED=0'):
            if needle not in page:
                problems.append(f'the page lost {needle!r}')
        if lines[-1] != 'PAYLOAD DONE INDEX=1 RETURN=0X0 STACK=OK LOADS=1 FAULTS=0 REFUSED=0 LINES=35 DROPPED=0':
            problems.append('the page does not end at PAYLOAD DONE INDEX=1')
        if [re.search(r'INDEX=(\d)', line).group(1) for line in lines if line.startswith('PAYLOAD LOADED ')] != list('01234'):
            problems.append('the five LOADED lines are not indexes 0 to 4')
        for phrase in (
                # the verdict: rows a to g, with row g worded as the host comparison and labelled VERIFIED, host
                cls.ROW_G, 'not exercised: nothing mismatched, so the page has no `REFUSED` line', 'held: `MON STOP WATCHDOG`; no hang and no reset',
                'held by that rule: `TICKS=96720173` is 720,173 ticks (0.0375 s) above the box and the stop is `WATCHDOG`',
                'The commands of row g: `python3 tools/monitor_baseline.py capture NAME`', '`python3 tools/monitor_baseline.py compare EARLIER LATER`',
                'IDENTICAL for `bare` (368,439 bytes, ELF digest prefix `419fbd516138b03a`)', '`fast` (333,279 bytes, `4d1fbac34769b5c2`)',
                'It is a pass of that record\'s rows and of nothing else; gate X4\'s "kernel executes" stands at PARTIAL (one vCPU, 5 s, early boot only',
                # what it settles, and what it does not
                'U3 of the plan, that the monitor\'s own stop path works, is answered once: it did',
                'U10 was not seen: no stage-2 abort was counted (`ABORTS=0`)', 'so what a passing gate says about the regime is UNKNOWN',
                'This boot does not show that macOS boots', 'The unit stayed up to `UPTIME 28 S`, with no restart and no fault page',
                'A1 and A2 were neither run nor exercised', 'nothing is authorised by this section',
                # what the sources say about the lines (VERIFIED, host or read in the sources) and what stays INFERRED
                'the monitor records `WATCHDOG` when an interrupt reaches EL2 with the EL2 timer\'s `ISTATUS` set after the deadline (VERIFIED, read in `probes/xnuboot/async.c`)',
                'the `ESR` and `FAR` of that line are zero by construction at an interrupt stop (VERIFIED, read in the same file) and carry no information',
                'The console lines against the kernelcache (VERIFIED, host: read in the local copy of the J700 26A428 kernelcache, which is not committed)',
                'the `?PTR?` after the first two `0X` is INFERRED to be the placeholder `<ptr>`', 'the monitor keeps its first 512 events and counts the later ones as dropped (VERIFIED, read in `probes/xnuboot/telemetry.c`)',
                'One vCPU, as planned', 'INFERRED: the 720,173 ticks above the box are the prepare, the stop and the clean',
                # the stop-site analysis (18.5): where the guest was, why, and what that settles and does not
                'Result (INFERRED, high): the guest was not slow and not waiting, it was stuck.', 'source of the 26A428 kernel (xnu-13432, macOS 27) is not public, so every match with the public source is structural and INFERRED for this kernel',
                '`tools/kc-symbolicate.py` needs the KDK, which is absent on the machine used: NOT RUN', 'the stop PC `0XFFFFFE000C1EEF00` is a link-time address in `__TEXT_EXEC`',
                'it is `PE_init_iokit`, the kernel\'s I/O Kit start-up, called from the kernel\'s second thread after the banner and the VM bootstrap printfs',
                'The PC is the head of a five-instruction loop that touches only registers', 'It matches structurally the progress-bar loop of the public `pexpert/arm/pe_init.c`',
                '`while (((unsigned)(*delta + image_size)) >= (display_size / 2))`, which with `display_size` 0 compares an unsigned value with 0 and never ends',
                'carries the command line `-v debug=0x8 serial=3 cluster_power=0 wdt=-1` and an all-zero `Boot_Video` block, because the panel is withheld (D9;',
                'the register that holds it (`x10`) is 0 at the stop; no register was printed, so that is INFERRED', 'a withheld panel, meets a stock kernel that draws a progress bar',
                'the quarantined guest timers (INTID 27 and 30) are not the cause', '`C=0XD3AA0A80` is a stale value, not a guest PC',
                'The stop is past all of `kernel_bootstrap`, in the second thread', 'so the six lines are exactly the cap and hold about 330 of the 1,024 bytes the window keeps',
                '`TRAPS=3127` and `CALLS=1443` are cumulative, not a rate', 'More time would not show more text or traps, because the loop ends only if the video block changes',
                'That the kernel boots. It spun in an early loop (INFERRED, high)', 'a longer box on the withheld image only repeats the loop (INFERRED)',
                'The monitor entry works on Oryon v1, once', 'early SPTM, TXM and GIC paths ran with `ABORTS=0`',
                'The same stop PC again once the panel is handed, or once `-noprogress` is added', '`x10` not 0 or `x21` not 1 at the stop',
                'The next run hands the panel to the guest (rung 2: a non-zero display size) or adds `-noprogress` to the guest\'s command line',
                'The first stop is most likely `MON STOP S2_FAULT` with an IPA', 'CPU-start write to the power-management (PMGR) block, which the plan counts as success for M1',
                'a `cpu N failed to boot` message (it says `waited 1 seconds`)', 'Any other device-tree node that I/O Kit probes could stop the run first (UNKNOWN which)',
                'the guest ran 5 s, then spun in an early console loop, INFERRED, 18.5'):
            if phrase not in flat:
                problems.append(f'section 18 lost {phrase[:70]!r}')
        if flat.count('macOS boots') != 1:
            problems.append('"macOS boots" is not said exactly once, in the sentence that denies it')
        if re.search(r'\b[0-9a-fA-F]{32,}\b', body) or '31e5d4c2' in text.lower():
            problems.append('section 18 or the log gives a record digest')
        if re.search(r'file offset|fileoff|51EAF00|88526AF00', body, re.I) or re.search(r'\bx(?:10|21)=0[xX]', body):
            problems.append('section 18 gives a kernelcache file offset or a register value that was never read')
        for place, needle in (('the top paragraph', 'Boot 20 (2026-11-18, section 18) ran the SP11 monitor entry for the first time'), ('item 14 of section 1', '14. **Boot 20 (2026-11-18; section 18; on video; OBSERVED, one unit, one boot)'),
                              ('item 14 of section 1', '**The guest was stuck, not slow (INFERRED, high; a read-only analysis after the boot, 18.5):**'),
                              ('the top paragraph', 'spinning in an early loop of the kernel\'s I/O Kit start-up that cannot end on an image with the panel withheld'),
                              ('the evidence summary', 'The SP11 monitor entry, run once (boot 20, M1 rung 1, plan `M1RUN-RUNG1`)'),
                              ('the evidence summary', 'the guest was, INFERRED (high), stuck in an early loop of the kernel\'s I/O Kit start-up (18.5)'),
                              ('section 17.6', 'Boot 20 (section 18) was the first run of that monitor entry.')):
            if needle not in ' '.join(text.split()):
                problems.append(f'{place} does not count on boot 20')
        return problems

    def test_section_18_keeps_the_facts_of_boot_20(self):
        """Section 18 transcribes one page read from one video (the first run of the monitor entry), records the stop-site analysis that followed it, and sections 1, 9 and 17.6 count on
        both: a drifted value, count or label fails here, and each seeded fault below must be found."""
        self.assertEqual(self.section_18_problems(self.text), [])
        seeds = (('MON STOP WATCHDOG PC=0XFFFFFE000C1EEF00 A=0X0', 'MON STOP S2_FAULT PC=0XFFFFFE000C1EEF00 A=0X0'), ('TICKS=96720173', 'TICKS=96000000'), ('ABORTS=0 STEPS=0', 'ABORTS=3 STEPS=0'),
                 ('byte-identical between revisions 87da59673c9a and ea593683db78 (VERIFIED, host;', 'byte-identical between revisions 87da59673c9a and ea593683db78 (OBSERVED, host;'),
                 ('boot does not show that macOS boots:', 'boot shows that macOS boots:'), ('REFUSED=0 LINES=35 DROPPED=0', 'REFUSED=0 LINES=34 DROPPED=0'), ('U10 was not seen', 'U10 was seen'),
                 ('so what a passing gate says about the regime is UNKNOWN', 'so what a passing gate says about the regime is VERIFIED'), ('stands at PARTIAL (one vCPU, 5 s, early boot only', 'stands at PASS (one vCPU, 5 s, early boot only'),
                 ('not exercised: nothing mismatched', 'held: nothing mismatched'), ('SNAPREP MON CON :XNU-13432.1.9?1/', 'SNAPREP MON CON :XNU-13432.1.97/'),
                 ('(VERIFIED, read in the same file) and carry no information', '(OBSERVED, read in the same file) and carry no information'), ('PAYLOAD LOADED INDEX=3 ', 'PAYLOAD LOADED INDEX=9 '),
                 ('One vCPU, as planned', 'Two vCPUs'), ('## 19. Result of boot 21', 'Digest 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\n\n## 19. Result of boot 21'),
                 # the stop-site analysis: each seeded fault upgrades a label, flips a result or drops a caveat
                 ('Result (INFERRED, high): the guest was not slow and', 'Result (VERIFIED): the guest was not slow and'), ('The PC is the head of a five-instruction loop that touches only registers', 'The PC is the head of a five-instruction loop that reads memory'),
                 ('which with `display_size` 0 compares an unsigned value with 0 and never ends', 'which with `display_size` 0 compares an unsigned value with 0 and ends at once'),
                 ('is 0 at the stop; no register was printed, so that is INFERRED', 'is 0 at the stop (VERIFIED)'), ('are cumulative, not a rate', 'are a rate'),
                 ('`x10` not 0 or `x21` not 1 at the stop', '`x10` not 0 at the stop'), ('That the kernel boots. It spun in an early loop (INFERRED, high)', 'That the kernel boots. It booted (VERIFIED)'),
                 ('The next run hands the panel to the guest (rung 2: a non-zero display size) or adds `-noprogress`', 'The next run only repeats the box with a longer time'),
                 ('The first stop is most likely `MON STOP S2_FAULT` with an IPA', 'The first stop is most likely a hang'), ('which is absent on the machine used: NOT RUN', 'which is absent on the machine used: PASS'),
                 ('so every match with the public source is structural and INFERRED for this kernel', 'so every match with the public source is exact for this kernel'),
                 ('the guest ran 5 s, then spun in an early console loop, INFERRED, 18.5', 'the guest ran 5 s and booted, 18.5'), ('## 19. Result of boot 21', 'The stop PC is at file offset 0x51EAF00.\n\n## 19. Result of boot 21'))
        for old, new in seeds:
            self.assertIn(old, self.text, f'the seed {old!r} no longer matches the text: fix the seed, not the check')
            self.assertTrue(self.section_18_problems(self.text.replace(old, new, 1)), f'a seeded fault went unseen: {old!r} -> {new!r}')

    @classmethod
    def section_19_problems(cls, text):
        """What is wrong with section 19, boot 21 (the second run of the SP11 monitor entry, M1 rung 2, the panel as the kernel's console), or with the places that count on it; an empty list is a pass."""
        body = text.split('## 19. Result of boot 21', 1)[1].split('## 20. Result of boot 22', 1)[0]
        flat = ' '.join(body.split())
        problems = []
        pages = re.findall(r'```text\n(.*?)```', body, re.S)
        if [len(page.splitlines()) for page in pages] != [38]:
            return ['the page of boot 21 is not one block of 38 lines']
        page = pages[0]
        lines = page.splitlines()
        counted = re.search(r'PAYLOAD DONE INDEX=1 .* LINES=(\d+)', page)
        if not counted or int(counted.group(1)) != len([line for line in lines if line.startswith('SNAPREP ')]):
            problems.append('SNAPREP lines are not the LINES= count of PAYLOAD DONE INDEX=1')
        if len([line for line in lines if line.startswith('SNAPMON ')]) != 5 or 'PAYLOAD DONE INDEX=0 RETURN=0X0 STACK=OK LOADS=2 FAULTS=0 REFUSED=0 LINES=29 DROPPED=0' not in page:
            problems.append('the five SNAPMON lines left on the page, or the wrapper\'s LINES=29, changed')
        ticks = [int(n) for n in re.findall(r'MON RET=0X0 STATUS=0X0 ENTERED=1 TICKS=(\d+) STACK=10888/262144', page)]
        if ticks != [39245286, 39245286]:
            problems.append(f'the call is not 39,245,286 ticks on both lines: {ticks}')
        elif not (0 < 96_000_000 - ticks[0] and abs(ticks[0] / 19_200_000 - 2.044) < 5e-4):
            problems.append('the arithmetic of the call: 2.044 s inside the box of 5 s (96,000,000 ticks)')
        if 0x20000 + 316783 != 447855 or 'OFF=0X20000 BYTES=316783' not in page:
            problems.append('the monitor image of boot 21 is not boot 20\'s')
        for needle in ('MON STATUS=OK ENTERED=0X1', 'MON COUNTS EVENTS=512 TRAPS=136424 ABORTS=0 STEPS=925 CALLS=65955 DROPPED=96475',
                       'MON STOP S2_FAULT PC=0XFFFFFE00097A352C A=0X300734004 B=0X93970045 C=0XFFFFFE60A0178004',
                       'MON EXIT PC=0XFFFFFE00097A352C SPSR=0X604003C4 ESR=0X93970045 FAR=0XFFFFFE60A0178004', 'MON REGS RESTORED=YES',
                       'GIC RESTORE CLEAN=YES FAILED=0X0 RETRIED=0X0 BEGUN=GRC VBAR=NONE EL1=NONE', 'MON VERDICT=RETURNED FLAGS=0XF SUB=7', 'PAYLOAD CALL 1 EE86676001D89CF6', 'PAYLOAD VERIFY=OK',
                       'SNAPMON STAGE=DONE RETURN=0', 'SNAPREP GIC NEW GICD=0X13 RB=0X12 ISEN=0X4000000 PRIO=0X80 PMR=0XF8 IGRPEN1=1', 'SNAPREP CHUNKS TOTAL=46 COPIED=46 HASHED=27 BYTES_COPIED=161041856 BYTES_HASHED=118904848',
                       'SNAPREP GUEST.DAT BASE=0XC5CFE000 BYTES=118908480', 'DESC=221', 'SNAPREP MON IMAGE OFF=0X20000 BYTES=316783 SHA=0XAD4EBAA27314296C BOX_S=5'):
            if needle not in page:
                problems.append(f'the page lost {needle!r}')
        if lines[-1] != 'PAYLOAD DONE INDEX=1 RETURN=0X0 STACK=OK LOADS=1 FAULTS=0 REFUSED=0 LINES=29 DROPPED=0':
            problems.append('the page does not end at PAYLOAD DONE INDEX=1')
        if 'MON CON' in page:
            problems.append('the page of a serial=2 boot has no MON CON line')
        # The stop, decoded from the words on the page: the document's decode must be the arithmetic of ESR 0x93970045.
        esr, ipa, far, pc = 0x93970045, 0x300734004, 0xFFFFFE60A0178004, 0xFFFFFE00097A352C
        decoded = ((esr >> 26) & 0x3F, (esr >> 25) & 1, (esr >> 24) & 1, (esr >> 22) & 3, (esr >> 16) & 31, (esr >> 6) & 1, esr & 0x3F)
        if decoded != (0x24, 1, 1, 2, 23, 1, 5):
            problems.append(f'the decode of ESR 0x93970045 is not EC 0x24, IL 1, ISV 1, SAS 2, SRT 23, WnR 1, DFSC 5: {decoded}')
        if not (ipa & 0xFFF == far & 0xFFF == 4 and ipa & 0xFFFF == 0x4004 and ipa == 0x300734004 and ipa == 0x300730000 + 0x4004):
            problems.append('the IPA and the FAR share the page offset 4, and the IPA is the first register of the CPU-start window, window 24 (0x300730000) plus 0x4004')
        if 2880 * 1920 * 4 != 0x1518000 or 0xE4800000 <= ipa < 0xE4800000 + 0x1518000:
            problems.append('A= is inside the panel range, which is the case that RUNG 2 NOT PASSED names')
        if 0xFFFFFE000C1EEF00 <= pc < 0xFFFFFE000C1EEF10 or not 0xFFFFFE0008A08000 <= pc < 0xFFFFFE000C264000:
            problems.append('the stop PC is in boot 20\'s loop, or not in __TEXT_EXEC')
        for phrase in (
                # what ran, and what the record changed
                'Boot 21 (2026-11-18) is the second run of the SP11 monitor entry and the first with the panel handed to the guest.',
                'it also decides the store classes `ram`, `el2_sysreg` and `gic` (D4) for it and the panel choice of D9 (the panel handed, `serial=2`, the kernel stock)',
                'The record changes two things in the guest and nothing in the programs', '`GUEST.DAT` is 118,908,480 bytes in 46 chunks (boot 20\'s was 118,870,336 in 44)',
                'The record did not add `-noprogress`, so that a result has a cause.', 'this log does not repeat the digest',
                'The film is one video of 58.8 s (29.99 frames a second, not CRC-verified)',
                # the film in time
                'the screen goes to the kernel\'s text (the runner\'s page is cleared) and fills over about 1.2 s',
                'the SNAPREP report page appears on the right of the kernel\'s text, about 1.9 s after the first kernel text (the call took 2.044 s)',
                '(17 S at 24 s, 22 S at 29 s, 36 S in the closer view at the end); no black-out, no restart',
                # the page and its arithmetic
                'as the record predicted for `serial=2` (INFERRED then, OBSERVED now',
                'The whole call took 39,245,286 ticks of the 19.2 MHz counter, 2.044 s (arithmetic), against the box of 5 s (96,000,000 ticks): the box was not used up.',
                'so the fault that ended the run is not in `ABORTS`; what armed 925 single-steps, where boot 20 had none, is UNKNOWN',
                # the verdict
                '| RUNG 2 PASSED |', 'which is not in the loop; and the kernel\'s text on the panel in many frames from the first one after the clear | OBSERVED |',
                '`A=0X300734004` is far from the panel range (`0xE4800000`, 0x1518000 bytes)', 'not exercised: nothing mismatched, so no `REFUSED` line, which is no pass',
                'held, and proved: `TICKS=39245286` is 2.044 s', 'every file of the staged list passes `shasum -a 256 -c` (18 of 18)',
                'the only changes since staging are `SP11\\DONE1.TXT` (44 bytes, the Shell\'s marker) and macOS\'s own `.fseventsd` entry',
                'are unchanged by git diff between ea59368 (the tree of boot 20) and eb6ba3b (the source of the record) (VERIFIED, host; says nothing about hardware)',
                'the byte comparison of boot 20\'s row g was not repeated for this tree (NOT RUN)',
                'Rung 2 passed by the record\'s own rule, and the pass is of that rule and of nothing else',
                'stays at PARTIAL (one vCPU, 2.04 s, through IOKit platform matching to the first CPU-start;',
                # what the panel showed: short quotes and roles
                '`initialize_screen: b=E4800000, w=00000B40, h=00000780, r=00002D00, d=00000000`', '`pmap_startup()` 626 microsec', 'the six CPUs of the guest\'s device tree registered (CPUs 0 to 3 of type 0, 4 and 5 of type 1), only CPU 0 running',
                'so these lines alone do not show that it was passed; rows 7 and 8 do', 'the MAC policy initialisation (`AppleImage4`, `AppleMobileFileIntegrity`, `Sandbox`, `Quarantine`',
                'that they are harmless in general is not shown (UNKNOWN)', 'the stop was the monitor\'s own and no kernel panic message was read in the frames examined',
                # the stop
                'is the guest\'s own fault, not the watchdog', 'Decode of `ESR` `0x93970045` (INFERRED, from the layout as remembered): exception class `0x24`',
                '`ISV` 1, access size word (32 bits), register `SRT` 23, `WnR` 1 (a write), `DFSC` `0x05` (a translation fault at level 1: no stage-2 mapping)',
                'whose low 16 bits are `0x4004`', 'its CPU-start window is `0x300734004` to `0x30073400c`',
                '(`probes/xnuboot/trap_dispatch.c`, lines 430 to 444, and `probes/devicemodel.py`, lines 752 to 757; VERIFIED, read in the sources; the address itself is the page\'s: OBSERVED)',
                'That the guest\'s write is that write is INFERRED, and not more: the value written (`x23`) and the writing function were not printed',
                # what it settles, what it does not, the options
                'The stock kernel runs through `kernel_bootstrap`, `PE_init_iokit`, the I/O Kit start-up, the MAC policy initialisation and the platform-driver matching on the SP11 under the monitor',
                'The loop of boot 20 is gone. The report\'s PC is another kernel address (OBSERVED), and the panel shows phases that follow `PE_init_iokit`',
                'is consistent with this run. It is not separated from the other change of the record, `serial=2`',
                'whether CPU bring-up works (it was not reached), SMP, a root device, storage, userspace, a longer run',
                'This boot does not show that macOS boots, nor stability or performance.', 'That the guest\'s write is the CPU-start write (an address match only), and its value. Why 925 single-steps were counted (UNKNOWN).',
                'A guest-input change only: `cpus=1`', 'which assertion, and whether this guest repeats it, are UNKNOWN', 'A monitor change: accept the CPU-start write and start nothing',
                'Real secondaries (monitor and wrapper)', '(UNIMPLEMENTED)', 'none is a decision, none is authorised, none is run', 'The approval was for one boot and is spent.'):
            if phrase not in flat:
                problems.append(f'section 19 lost {phrase[:70]!r}')
        if flat.count('macOS boots') != 1:
            problems.append('"macOS boots" is not said exactly once, in the sentence that denies it')
        if re.search(r'\b[0-9a-fA-F]{32,}\b', body) or '1ad122b3' in text.lower():
            problems.append('section 19 or the log gives a record digest')
        if re.search(r'file offset|fileoff|51EAF00|88526AF00', body, re.I) or re.search(r'\bx(?:10|21|23)=0[xX]', body):
            problems.append('section 19 gives a kernelcache file offset or a register value that was never read')
        for place, needle in (('the top paragraph', 'Boot 21 (2026-11-18, section 19) ran rung 2 with the panel handed to the guest as the kernel\'s console (plan `M1RUN-CONSOLE-RUNG2`)'),
                              ('item 15 of section 1', '15. **Boot 21 (2026-11-18; section 19; on video; OBSERVED, one unit, one boot): M1 rung 2, the panel handed to the guest as the kernel\'s console (plan `M1RUN-CONSOLE-RUNG2`), PASSED by the record\'s own rule'),
                              ('item 15 of section 1', 'The run shows nothing past the first CPU-start write: not CPU bring-up, not SMP, not a root device, not stability or performance, and not that macOS boots.'),
                              ('the evidence summary', 'The SP11 monitor entry, its first two runs: boot 20 (rung 1, panel withheld) and boot 21 (rung 2, the panel handed as the kernel\'s console, plan `M1RUN-CONSOLE-RUNG2`)'),
                              ('section 18.6', 'Boot 21 (section 19) was the next run, rung 2, with the panel handed to the guest.')):
            if needle not in ' '.join(text.split()):
                problems.append(f'{place} does not count on boot 21')
        return problems

    def test_section_19_keeps_the_facts_of_boot_21(self):
        """Section 19 transcribes one page read from one video (the second run of the monitor entry, rung 2, the panel as the console), decodes its stop and says what it does not settle;
        sections 1, 9 and 18.6 count on it: a drifted value, count or label fails here, and each seeded fault below must be found."""
        self.assertEqual(self.section_19_problems(self.text), [])
        sources = ((ROOT / 'probes' / 'xnuboot' / 'trap_dispatch.c', 430, 444, ('0x300734004', '0x30073400c', 'c->host_cpus', 'global core mask at +4')),
                   (ROOT / 'probes' / 'devicemodel.py', 752, 757, ('0x300730000', '0x14000', '0x10000')))
        for path, first, last, needles in sources:
            window = '\n'.join(path.read_text().splitlines()[first - 1:last])
            for needle in needles:
                self.assertIn(needle, window, f'{path.name} lines {first} to {last}: the text of section 19 cites {needle!r} there')
        seeds = (('MON STOP S2_FAULT PC=0XFFFFFE00097A352C A=0X300734004', 'MON STOP WATCHDOG PC=0XFFFFFE00097A352C A=0X300734004'), ('TICKS=39245286', 'TICKS=96000000'), ('ABORTS=0 STEPS=925', 'ABORTS=3 STEPS=925'),
                 ('ESR=0X93970045 FAR', 'ESR=0X93970046 FAR'), ('A=0X300734004 B=', 'A=0X300734000 B='), ('SPSR=0X604003C4', 'SPSR=0X804003C4'), ('LINES=29 DROPPED=0\n```\n\nThe 29 `SNAPREP` lines', 'LINES=28 DROPPED=0\n```\n\nThe 29 `SNAPREP` lines'),
                 ('SNAPREP GIC NEW GICD=0X13', 'SNAPREP GIC NEW GICD=0X10'), ('SNAPREP MONITOR RETURNED STAGE=DONE', 'SNAPREP MONITOR RETURNED STAGE=DONE\nSNAPREP MON CON DARWIN KERNEL'),
                 ('PAYLOAD CALL 1 EE86676001D89CF6', 'PAYLOAD CALL 1 EE86676001D89CF7'),
                 ('(a write), `DFSC` `0x05` (a translation fault at level 1: no stage-2 mapping)', '(a read), `DFSC` `0x05` (a translation fault at level 1: no stage-2 mapping)'),
                 ('`SRT` 23', '`SRT` 24'), ('whose low 16 bits are `0x4004`', 'whose low 16 bits are `0x5004`'), ('its CPU-start window is `0x300734004` to `0x30073400c`', 'its CPU-start window is `0x300734008` to `0x30073400c`'),
                 ('That the guest\'s write is that write is INFERRED, and not more', 'That the guest\'s write is that write is VERIFIED, and not more'),
                 ('the value written (`x23`) and the writing function were not printed', 'the value written (`x23`) and the writing function were 0x2 and ApplePMGR'), ('96,000,000 ticks): the box was not used up.', '96,000,000 ticks): the box was used up.'),
                 ('the byte comparison of boot 20\'s row g was not repeated for this tree (NOT RUN)', 'the byte comparison of boot 20\'s row g was repeated for this tree (PASS)'),
                 ('(VERIFIED, host; says nothing about hardware)', '(OBSERVED, host; says nothing about hardware)'), ('(18 of 18)', '(17 of 18)'),
                 ('stays at PARTIAL (one vCPU, 2.04 s, through IOKit platform matching to the first CPU-start;', 'stays at PASS (one vCPU, 2.04 s, through IOKit platform matching to the first CPU-start;'),
                 ('This boot does not show that macOS boots, nor stability or performance.', 'This boot shows that macOS boots, and stability.'),
                 ('that they are harmless in general is not shown (UNKNOWN)', 'that they are harmless in general is shown (VERIFIED)'),
                 ('The record did not add `-noprogress`, so that a result has a cause.', 'The record added `-noprogress`.'),
                 ('so these lines alone do not show that it was passed; rows 7 and 8 do', 'so these lines show that it was passed'),
                 ('A guest-input change only: `cpus=1`', 'A guest-input change only: `cpus=2`'), ('which assertion, and whether this guest repeats it, are UNKNOWN', 'which assertion, and whether this guest repeats it, are VERIFIED'),
                 ('none is a decision, none is authorised, none is run', 'each is decided and authorised'), ('The approval was for one boot and is spent.', 'The approval stands for further boots.'),
                 ('Why 925 single-steps were counted (UNKNOWN).', 'Why 925 single-steps were counted (VERIFIED).'), ('(INFERRED then, OBSERVED now', '(OBSERVED then, VERIFIED now'),
                 ('the panel shows phases that follow `PE_init_iokit`', 'the panel shows the first statement of `PE_init_iokit`'), ('(17 S at 24 s, 22 S at 29 s, 36 S in the closer view at the end); no black-out, no restart', '(17 S at 24 s); a black-out and a restart'),
                 ('## 20. Result of boot 22', 'Digest 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\n\n## 20. Result of boot 22'), ('## 20. Result of boot 22', 'The record is 1AD122B3 and the stop PC is at file offset 0x51EAF00.\n\n## 20. Result of boot 22'),
                 ('Boot 21 (section 19) was the next run, rung 2, with the panel handed to the guest.', 'Boot 21 was a run.'))
        marker = '## 19. Result of boot 21'
        head, rest = self.text.split(marker, 1)
        for old, new in seeds:
            if old in rest:
                mutated = head + marker + rest.replace(old, new, 1)         # section 19 first: boot 20's page has some of the same lines
            else:
                self.assertIn(old, head, f'the seed {old!r} no longer matches the text: fix the seed, not the check')
                mutated = head.replace(old, new, 1) + marker + rest
            self.assertTrue(self.section_19_problems(mutated), f'a seeded fault went unseen: {old!r} -> {new!r}')

    @classmethod
    def section_20_problems(cls, text, window):
        """What is wrong with section 20, boot 22 (the S1 survey: the read-only payloads `nvmesnap`, then `smmuprobe`), with the places that count on it, or with the register values that it
        shares with section 7.1 of docs/SP11-NVME-WINDOW.md (window: that document's text); an empty list is a pass."""
        body = text.split('## 20. Result of boot 22', 1)[1].split('## 21. Result of boot 23', 1)[0]
        flat = ' '.join(body.split())
        problems = []
        pages = re.findall(r'```text\n(.*?)```', body, re.S)
        if [len(page.splitlines()) for page in pages] != [52]:
            return ['the page of boot 22 is not one block of 52 lines']
        page = pages[0]
        lines = page.splitlines()
        for prefix, expected in (('PAYLOAD NVMESNAP ', 27), ('PAYLOAD SMMUPROBE ', 16)):
            if len([line for line in lines if line.startswith(prefix)]) != expected:
                problems.append(f'the page does not hold {expected} lines of {prefix.split()[1]}')
        if 'PAYLOAD DONE INDEX=0 RETURN=0X0 STACK=OK LOADS=53 FAULTS=0 REFUSED=0 LINES=27 DROPPED=0' not in page or \
                lines[-1] != 'PAYLOAD DONE INDEX=1 RETURN=0X0 STACK=OK LOADS=39 FAULTS=0 REFUSED=0 LINES=16 DROPPED=0':
            problems.append('the LINES= counts of the two PAYLOAD DONE lines are not the 27 and the 16 lines of the block')
        for needle in ('PAYLOAD LOADED INDEX=0 BASE=0XCECB2000 BYTES=19857 SHA=A3DF0F84DDF56B74 TYPE=1', 'PAYLOAD LOADED INDEX=1 BASE=0XCECAC000 BYTES=14994 SHA=71F6FF4A583B0E78 TYPE=1',
                       'PAYLOAD READY FILES=2 RANGES=5 CODE=2', 'PAYLOAD CALL 0 A3DF0F84DDF56B74', 'PAYLOAD CALL 1 71F6FF4A583B0E78', 'PAYLOAD VERIFY=OK',
                       'PAYLOAD NVMESNAP START CURRENTEL=0X8 FREQ=19200000 ECAM=0X70000000 WINDOW=0X70300000',
                       'PAYLOAD NVMESNAP GATE LINK=UP RPMEM=1 MEM=1 BME=1 D=0 BAR0=0X70300000 MAP=DEVICE.OS.NS1 WHY=NONE',
                       'PAYLOAD NVMESNAP NVME CAP=0X303C0301FF CSTS=0X1 CC=0X460001 VS=0X10400', 'PAYLOAD NVMESNAP NVME.AQ CSTS2=0X1 AQA=0X10001 ASQ=0XCF3E7000 ACQ=0XCF3E8000',
                       'PAYLOAD NVMESNAP AQ ASQE=2 ACQE=2', 'PAYLOAD NVMESNAP ASQ AT=0XCF3E7000 MEMMAP_TYPE=4 MAP=WB.OS.NS1', 'PAYLOAD NVMESNAP ACQ AT=0XCF3E8000 MEMMAP_TYPE=4 MAP=WB.OS.NS1',
                       'PAYLOAD NVMESNAP VERDICT CTRL=RUNNING QUEUES=READ LINK=UP WHY=NONE', 'PAYLOAD NVMESNAP DONE NVME_GROUP_RAN LOADS=53 FAULTS=0 REFUSED=0 NVME_TRIED=8',
                       'PAYLOAD SMMUPROBE START CURRENTEL=0X8 FREQ=19200000 ECAM=0X70000000 SMMU=0X15400000', 'PAYLOAD SMMUPROBE MAP RP=DEVICE.OS.NS1 EP=DEVICE.OS.NS1 SMMU=FAULT=0X7:TRANS_L3',
                       'PAYLOAD SMMUPROBE RP.AER AT=0X100 UESTA=0X0 UEMASK=0X4400000 CESTA=0X0 CEMASK=0XE000 ROOTERR=0X0', 'PAYLOAD SMMUPROBE EP.AER AT=0X100 UESTA=0X0 UEMASK=0X400000 CESTA=0X0 CEMASK=0XE000',
                       'PAYLOAD SMMUPROBE VERDICT STREAM=0X60100 MODE=UNKNOWN WHY=SMMU_PAGE_UNMAPPED', 'PAYLOAD SMMUPROBE DONE SMMU_GROUP_SKIPPED LOADS=39 FAULTS=0 REFUSED=0 SMMU_TRIED=0'):
            if needle not in page:
                problems.append(f'the page lost {needle!r}')
        if 'NEXT SMMU_REGISTERS' in page or re.search(r'MODE=(?!UNKNOWN)', body):
            problems.append('the page of a skipped SMMU group has a NEXT SMMU_REGISTERS line, or the text gives the SMMU a mode')

        def words(prefix):
            line = next((line for line in lines if line.startswith(prefix)), '')
            return {name: int(value, 16) for name, value in re.findall(r'\b([A-Z][A-Z0-9_]*)=0X([0-9A-F]+)', line)}
        # The decodes of section 20.4, redone from the words on the page.
        regs, queue = words('PAYLOAD NVMESNAP NVME CAP='), words('PAYLOAD NVMESNAP NVME.AQ ')
        cap, cc, vs, aqa = regs.get('CAP', 0), regs.get('CC', 0), regs.get('VS', 0), queue.get('AQA', 0)
        if (cap & 0xFFFF, (cap >> 16) & 1, (cap >> 24) & 0xFF, (cap >> 32) & 0xF, (cap >> 36) & 1, (cap >> 37) & 0xFF, (cap >> 48) & 0xF, (cap >> 52) & 0xF, (cap >> 56) & 3) != (511, 1, 60, 0, 1, 1, 0, 0, 0):
            problems.append('CAP 0x303C0301FF is not MQES 511, CQR 1, TO 60, DSTRD 0, NSSRS 1, CSS 1, MPSMIN 0, MPSMAX 0 and no PMR or CMB')
        if (cc & 1, (cc >> 16) & 0xF, (cc >> 20) & 0xF, cc & 0xFFFE) != (1, 6, 4, 0) or (vs >> 16, (vs >> 8) & 0xFF, vs & 0xFF) != (1, 4, 0) or ((aqa & 0xFFF) + 1, ((aqa >> 16) & 0xFFF) + 1) != (2, 2):
            problems.append('CC 0x460001, VS 0x10400 or AQA 0x10001 is not EN 1 with entry sizes 2^6 and 2^4, NVMe 1.4.0 and two entries in each admin queue')
        rp, ep = words('PAYLOAD NVMESNAP RP ID='), words('PAYLOAD NVMESNAP EP ID=')
        identity = lambda w: (w.get('ID', 0) & 0xFFFF, w.get('ID', 0) >> 16, w.get('CLASSREV', 0) >> 8, w.get('CLASSREV', 0) & 0xFF, (w.get('HDR', 0) >> 16) & 0x7F)      # noqa: E731
        if identity(rp) != (0x17CB, 0x0111, 0x060400, 0, 1) or identity(ep) != (0x144D, 0xA80B, 0x010802, 2, 0):
            problems.append('the ids, classes, revisions and header types are not 0x17CB:0x0111 (a bridge, revision 0, type 1) and 0x144D:0xA80B (NVMe, revision 2, type 0)')
        for name in ('RP ID=', 'EP ID='):
            if words('PAYLOAD NVMESNAP ' + name) != words('PAYLOAD SMMUPROBE ' + name):
                problems.append(f'the two payloads read different words for {name[:-4]}')
        buses, memwin, bar0 = rp.get('BUSES', 0), words('PAYLOAD NVMESNAP RP MEMWIN=').get('MEMWIN', 0), ep.get('BAR0', 0)
        if (buses & 0xFF, (buses >> 8) & 0xFF, (buses >> 16) & 0xFF) != (0, 1, 1) or ((memwin & 0xFFF0) << 16, (((memwin >> 16) & 0xFFF0) << 16) | 0xFFFFF) != (0x70300000, 0x703FFFFF) or \
                (bar0 & 0xF, bar0 & ~0xF) != (4, 0x70300000):
            problems.append('BUSES, MEMWIN and BAR0 are not buses 0 to 1, the window 0x70300000 to 0x703FFFFF and a 64-bit memory BAR at 0x70300000')
        if (rp.get('CMDSTS', 0) & 0xFFFF, ep.get('CMDSTS', 0) & 0xFFFF, rp.get('CMDSTS', 0) >> 16, ep.get('CMDSTS', 0) >> 16) != (0x0003, 0x0006, 0x0010, 0x0010):
            problems.append('the command words are not 0x0003 (bus master off) at the root port and 0x0006 (memory space and bus master on) at the endpoint, with status 0x0010 at both')
        links = (words('PAYLOAD NVMESNAP RP.LINK ').get('LNKCTLSTA', 0) >> 16, words('PAYLOAD NVMESNAP EP.LINK ').get('LNKCTLSTA', 0) >> 16)
        if [(status & 0xF, (status >> 4) & 0x3F, (status >> 13) & 1) for status in links] != [(4, 4, 1), (4, 4, 0)]:
            problems.append('the link status words 0xF044 and 0x1044 are not speed 4, width 4, with the data link layer link active at the root port')
        sq0, sq1, cq0, cq1 = (words(f'PAYLOAD NVMESNAP {name} ') for name in ('SQ0', 'SQ1', 'CQ0', 'CQ1'))
        if (sq0.get('DW0', 0) & 0xFF, sq0.get('DW0', 0) >> 16, sq1.get('DW0', 0) & 0xFF, sq1.get('DW0', 0) >> 16, sq0.get('CDW10', 1), sq1.get('CDW10', 1)) != (6, 0x12, 6, 0x11, 0, 0) or \
                (cq0.get('DW3', 0) & 0xFFFF, (cq0.get('DW3', 0) >> 16) & 1, cq1.get('DW3', 0) & 0xFFFF, (cq1.get('DW3', 0) >> 16) & 1) != (0x12, 0, 0x11, 1):
            problems.append('the queue slots are not two Identify commands (opcode 6, CIDs 0x12 and 0x11, CDW10 0) with completions of phase 0 and phase 1, the reading of a queue that has wrapped once')
        if not 29 <= 45 - 15.25 <= 31:
            problems.append('the film starts at about UPTIME 30 S: 45 S at the last frame less 15.25 s')
        # The registers that boot 22 read again are those that the X2 image read on 2026-11-15, in section 7.1 of the NVME-WINDOW document.
        section_71 = window.split('### 7.1 ', 1)[1].split('### 7.2 ', 1)[0]
        known = {int(value, 16) for value in re.findall(r'0x([0-9A-Fa-f]+)', section_71)}
        for name, value in (('CAP', cap), ('VS', vs), ('CC', cc), ('AQA', aqa), ('ASQ', queue.get('ASQ', 0)), ('ACQ', queue.get('ACQ', 0)), ('BAR0', bar0 & ~0xF)):
            if value not in known:
                problems.append(f'{name} of boot 22 is not among the values of section 7.1 of docs/SP11-NVME-WINDOW.md')
        for phrase in (
                # what ran, how it was read, what is not on the film
                'Boot 22 (2026-11-18, about 15:05 by the Shell\'s marker) is the first run of session 1 of the storage sessions',
                'the coordinator\'s notes call it the S1 survey, which is not the partition plan\'s item S1',
                'The operator approved the two payload files and their manifest by SHA-256 for one boot, at about 14:58; this log does not repeat the digests of the approval',
                'both are read only: no store but to their own stack, no doorbell, no `CC` write and no admin command, and `smmuprobe` reads no SMMU register when EL2\'s stage 1 does not map the SMMU\'s page',
                'The film is one video of 15.25 s (59.94 frames a second, not CRC-verified) that starts late: its last frame reads `UPTIME 45 S`, so it starts at about `UPTIME 30 S`',
                'and the first 30 s of the boot (the Shell\'s countdown, the loading, the runner\'s start) are not on it',
                'what was read in a document or a source on the host is VERIFIED only as read',
                '| before the film (the first 30 s of `UPTIME`) | not filmed:', '(`UPTIME 44 S` at 14 s, `UPTIME 45 S` in the last frame); no hang, no fault page, no restart',
                # the page
                'The two `LINES=` counts are the payloads\' own and equal the 27 `NVMESNAP` and 16 `SMMUPROBE` lines of the block (arithmetic).',
                'the counter is the payload document\'s and is not decoded here',
                # the verdict
                'the time from `CALL 0` to the last `DONE` is not on the film (it started late)', 'OBSERVED (the absence of a fault); UNKNOWN (the rest)',
                'every line of `shasum -a 256 -c` over the staged list (15 files) is OK',
                'the only changes since the staging at 15:01 are `SP11\\DONE1.TXT` (36 bytes, the Shell\'s marker `AUTORUN nvmesnap-s1 STEP 1 STARTED`) and macOS\'s own `.fseventsd` entry',
                # the decodes (INFERRED)
                '`ID=0X11117CB` (vendor `0x17CB`, device `0x0111`)', '`CLASSREV=0X6040000` (class `0x060400`, a PCI bridge, revision 0)', '`BUSES=0X10100` (primary 0, secondary 1, subordinate 1)',
                '`MEMWIN=0X70307030` (a memory window of `0x70300000` to `0x703FFFFF`)', 'the command word `0x0003` is I/O and memory space on and bus master off', 'listed `0x0007`',
                '`ID=0XA80B144D` (vendor `0x144D`, device `0xA80B`)', '`CLASSREV=0X1080202` (class `0x010802`, NVMe, revision 2)',
                '`CMDSTS=0X100006` (command `0x0006`: memory space and bus master on; status `0x0010`: capability list)', 'one 64-bit memory BAR at `0x70300000`, inside the root port\'s window',
                '(status `0xF044`: current speed 4, width 4, data link layer link active)', '(status `0x1044`: speed 4, width 4)',
                'an NVMe 1.4.0 controller that is enabled and ready, not fatal, no shutdown, with 512 entries possible per queue, 4 KiB pages, a ready timeout of 30 s (60 units of 500 ms)',
                'two entries in each queue, in memory that the map calls boot-services data and EL2 maps write-back',
                'two Identify commands (opcode 6) for namespace 1 with `CDW10` 0 (the namespace form), whose completions have status success: the firmware\'s own DMA worked when it ran',
                'which reads as a queue that has wrapped once, so at least three commands were issued and the first is no longer in the ring',
                'no uncorrectable or correctable error status and no root error at either end',
                'a translation fault at level 3 (fault status `0x7`, which the payload prints as `TRANS_L3`): the page has no stage-1 mapping at EL2; no access was made; no SMMU register was read',
                # what it settles
                'The NVMe controller runs after the bounce: `CC.EN` 1, `CSTS.RDY` 1, `CFS` 0, version 1.4.0', 'The firmware-left state repeats across two boots:', 'Two boots agree; that does not make them constants',
                'so DMA reached the firmware\'s queue memory when the firmware ran (INFERRED, indirect: it says nothing about EL2\'s own state or a buffer of ours)',
                'It is UNMAPPED, not aborting, and `smmuprobe` skipped the SMMU\'s registers by its own rule.',
                'This fits the X2 image\'s two reads of that page that faulted (2026-11-14 and 2026-11-15), without proving the same cause (INFERRED).',
                # what it does not settle
                'so `MODE=UNKNOWN` is the whole answer (D7). Nor is it shown that the SMMU is protected: the page is only unmapped at EL2\'s stage 1, and why is UNKNOWN (a firmware-owned page, or simply a page that is not in EL2\'s map)',
                'Whether the SSD\'s DMA reaches a monitor-owned extent after ExitBootServices and the hand-over of the SMMU (D14): UNKNOWN.',
                'Anything about writes: the boot wrote nothing to the SSD, the SMMU or any device register',
                'Whether Windows boots normally after a controller reset (D11) is UNKNOWN and was not exercised.',
                'The meaning of `NVME_TRIED`, the Identify data, the SSD\'s namespace and LBA format, its serial number and its write cache: not read.',
                'Whether the root port\'s bus-master bit (clear in this reading, set in the firmware\'s own view of 2026-11-15) matters for the endpoint\'s DMA: UNKNOWN.',
                'Anything about the Wi-Fi module beyond this: it shares this SMMU node', 'which counts against D11.', 'That BAR0 repeated lets the descriptor pin it, still re-proved each run.',
                # the options
                'none is a decision, none is authorised, none is run',
                'a q1n1 or monitor change that gives EL2\'s stage 1 a Device mapping of the SMMU\'s page (not a payload: a payload cannot add a mapping)',
                'it writes the controller, D11 gates it and it is UNIMPLEMENTED (the takeover core exists on the host only)',
                'The approval of S1 was for one boot and is spent. Nothing is authorised by this section.'):
            if phrase not in flat:
                problems.append(f'section 20 lost {phrase[:70]!r}')
        if flat.count('OBSERVED (the page); VERIFIED (the row, as read)') != 2:
            problems.append('the two verdict rows of 20.3 do not both say OBSERVED (the page); VERIFIED (the row, as read)')
        for row in body.split('### 20.3', 1)[1].split('### 20.4', 1)[0].splitlines():
            if row.startswith('| ') and not row.startswith(('| ---', '| What ')) and not re.search(r'OBSERVED|VERIFIED|INFERRED|UNKNOWN|NOT RUN', row.rsplit('|', 2)[-2]):
                problems.append(f'a row of 20.3 has no label: {row[:50]!r}')
        if re.search(r'\b[0-9a-fA-F]{32,}\b', body) or re.search(r'file offset|fileoff', body, re.I):
            problems.append('section 20 gives a record digest or a kernelcache file offset')
        whole = ' '.join(text.split())
        for place, needle in (('the top paragraph', 'Boot 22 (2026-11-18, section 20) ran the first storage session, a read-only survey of the SSD\'s link, controller and admin queues and of the PCIe SMMU\'s mapping '
                                                    '(the payloads `nvmesnap` and `smmuprobe`): the controller was enabled and ready with the firmware\'s two-entry admin queues, as on 2026-11-15, and the SMMU\'s page '
                                                    'had no stage-1 mapping at EL2, so its state is still UNKNOWN (OBSERVED, one unit, one boot, from a film that started late; nothing was written).'),
                              ('item 16 of section 1', '16. **Boot 22 (2026-11-18; section 20; on video, which started at about `UPTIME 30 S`; OBSERVED, one unit, one boot): the first storage session, the S1 survey '
                                                       '(plan `nvmesnap-s1`), read only, ran to its end:'),
                              ('item 16 of section 1', 'The survey shows nothing about whether DMA reaches a monitor-owned extent after the takeover, nothing about writes and nothing about D11.'),
                              ('the evidence summary', 'The first storage session, the S1 survey (boot 22, plan `nvmesnap-s1`: the read-only payloads `nvmesnap`, then `smmuprobe`)'),
                              ('the evidence summary', 'the SMMU\'s mode, why its page is unmapped, the DMA reach after a takeover, writes and D11 are UNKNOWN | 20 |'),
                              ('section 19.6', 'Boot 22 (section 20) was not a run of the monitor: it was the read-only storage survey, S1.')):
            if needle not in whole:
                problems.append(f'{place} does not count on boot 22')
        return problems

    def test_section_20_keeps_the_facts_of_boot_22(self):
        """Section 20 transcribes one page read from one film that started late (the read-only S1 survey: the payloads `nvmesnap`, then `smmuprobe`), decodes its fields (INFERRED) and says what it
        does not settle; sections 1, 9 and 19.6 count on it and its registers are those of section 7.1 of docs/SP11-NVME-WINDOW.md: a drifted value, count or label fails here, and each seeded fault
        below must be found."""
        window = (ROOT / 'docs' / 'SP11-NVME-WINDOW.md').read_text()
        self.assertEqual(self.section_20_problems(self.text, window), [])
        body_seeds = (
            ('PAYLOAD NVMESNAP VERDICT CTRL=RUNNING QUEUES=READ LINK=UP WHY=NONE', 'PAYLOAD NVMESNAP VERDICT CTRL=RUNNING QUEUES=READ LINK=DOWN WHY=NONE'),
            ('CAP=0X303C0301FF CSTS=0X1 CC=0X460001', 'CAP=0X303C0301FE CSTS=0X1 CC=0X460001'), ('ASQ=0XCF3E7000 ACQ=0XCF3E8000', 'ASQ=0XCF3E7000 ACQ=0XCF3E8008'),
            ('PAYLOAD NVMESNAP AQ ASQE=2 ACQE=2', 'PAYLOAD NVMESNAP AQ ASQE=32 ACQE=32'), ('SMMU=FAULT=0X7:TRANS_L3', 'SMMU=OK'),
            ('MODE=UNKNOWN WHY=SMMU_PAGE_UNMAPPED', 'MODE=BYPASS WHY=NONE'), ('SMMU_TRIED=0', 'SMMU_TRIED=3'),
            ('LOADS=39 FAULTS=0 REFUSED=0 LINES=16 DROPPED=0', 'LOADS=39 FAULTS=0 REFUSED=0 LINES=15 DROPPED=0'), ('LOADS=53 FAULTS=0 REFUSED=0 LINES=27 DROPPED=0', 'LOADS=53 FAULTS=0 REFUSED=0 LINES=26 DROPPED=0'),
            ('PAYLOAD SMMUPROBE EP BAR1=0X0 CAPPTR=0X40\n', ''), ('PAYLOAD SMMUPROBE NEXT PCIE_CAPS AFTER 8 S\nPAYLOAD SMMUPROBE RP.PCIE', 'PAYLOAD SMMUPROBE NEXT SMMU_REGISTERS AFTER 8 S\nPAYLOAD SMMUPROBE RP.PCIE'),
            ('DW3=0X10011 CID=0X11 P=1', 'DW3=0X00011 CID=0X11 P=0'), ('SQ0 DW0=0X120006 OPC=0X6', 'SQ0 DW0=0X120002 OPC=0X2'),
            ('PAYLOAD NVMESNAP RP ID=0X11117CB CMDSTS=0X100003', 'PAYLOAD NVMESNAP RP ID=0X11117CB CMDSTS=0X100007'), ('LNKCTLSTA=0XF0440048', 'LNKCTLSTA=0XF0430048'),
            ('PAYLOAD NVMESNAP RP MEMWIN=0X70307030', 'PAYLOAD NVMESNAP RP MEMWIN=0X70307020'), ('BYTES=14994 SHA=71F6FF4A583B0E78', 'BYTES=14995 SHA=71F6FF4A583B0E78'),
            ('PAYLOAD CALL 0 A3DF0F84DDF56B74', 'PAYLOAD CALL 0 A3DF0F84DDF56B75'),
            ('one video of 15.25 s (59.94 frames a second', 'one video of 45 s (59.94 frames a second'), ('so it starts at about `UPTIME 30 S`', 'so it starts at `UPTIME 0 S`'),
            ('at about 14:58; this log does not repeat the digests of the approval', 'at about 14:58; the digest of the approval is below'),
            ('over the staged list (15 files) is OK', 'over the staged list (14 files) is OK'), ('`SP11\\DONE1.TXT` (36 bytes,', '`SP11\\DONE1.TXT` (44 bytes,'),
            ('is not on the film (it started late)', 'was 4 s'), ('OBSERVED (the absence of a fault); UNKNOWN (the rest)', 'the absence of a fault'),
            ('| OBSERVED (the page); VERIFIED (the row, as read) |', '| the page |'), ('a ready timeout of 30 s (60 units of 500 ms)', 'a ready timeout of 60 s (60 units of 500 ms)'),
            ('the command word `0x0003` is I/O and memory space on and bus master off', 'the command word `0x0003` is I/O and memory space on and bus master on'),
            ('(status `0xF044`: current speed 4, width 4,', '(status `0xF044`: current speed 3, width 4,'),
            ('which reads as a queue that has wrapped once, so at least three commands were issued', 'which reads as a queue that has not wrapped, so two commands were issued'),
            ('two Identify commands (opcode 6) for namespace 1', 'two Read commands (opcode 2) for namespace 1'), ('without proving the same cause (INFERRED).', 'proving the same cause (VERIFIED).'),
            ('Nor is it shown that the SMMU is protected', 'The SMMU is protected'), ('(D14): UNKNOWN.', '(D14): it does.'),
            ('Anything about writes: the boot wrote nothing', 'Anything about writes: the boot wrote a quiesce'), ('(D11) is UNKNOWN and was not exercised.', '(D11) is shown.'),
            ('The firmware-left state repeats across two boots:', 'The firmware-left state is constant:'), ('(INFERRED, indirect: it says nothing', '(VERIFIED, direct: it says nothing'),
            ('matters for the endpoint\'s DMA: UNKNOWN.', 'matters for the endpoint\'s DMA: it does not.'), ('Anything about the Wi-Fi module beyond this', 'The Wi-Fi module works'),
            ('a translation fault at level 3 (fault status `0x7`', 'a permission fault at level 3 (fault status `0x7`'),
            ('is the first run of session 1 of the storage sessions', 'is the first run of session 2 of the storage sessions'),
            ('none is a decision, none is authorised, none is run', 'each is decided and authorised'),
            ('The approval of S1 was for one boot and is spent.', 'The approval of S1 stands for further boots.'),
            ('## 21. Result of boot 23', 'Digest 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\n\n## 21. Result of boot 23'),
            ('## 21. Result of boot 23', 'The stop is at file offset 0x51EAF00.\n\n## 21. Result of boot 23'))
        place_seeds = (
            ('so its state is still UNKNOWN (OBSERVED, one unit, one boot, from a film that started late; nothing was written).', 'so its state is bypass (VERIFIED).'),
            ('The survey shows nothing about whether DMA reaches a monitor-owned extent after the takeover, nothing about writes and nothing about D11.', 'The survey shows that DMA reaches a monitor-owned extent after the takeover.'),
            ('the SMMU\'s mode, why its page is unmapped, the DMA reach after a takeover, writes and D11 are UNKNOWN | 20 |', 'the SMMU is in bypass | 20 |'),
            ('Boot 22 (section 20) was not a run of the monitor: it was the read-only storage survey, S1.', 'Boot 22 was a run.'))
        marker = '## 20. Result of boot 22'
        head, rest = self.text.split(marker, 1)
        limit = rest.index('## 21. Result of boot 23') + len('## 21. Result of boot 23')
        for old, new in body_seeds:
            self.assertIn(old, rest[:limit], f'the seed {old!r} no longer matches the text: fix the seed, not the check')
            self.assertTrue(self.section_20_problems(head + marker + rest.replace(old, new, 1), window), f'a seeded fault went unseen: {old!r} -> {new!r}')
        for old, new in place_seeds:
            self.assertIn(old, head, f'the seed {old!r} no longer matches the text: fix the seed, not the check')
            self.assertTrue(self.section_20_problems(head.replace(old, new, 1) + marker + rest, window), f'a seeded fault went unseen: {old!r} -> {new!r}')
        for old, new in (('0xCF3E8000', '0xCF3E8008'), ('0x303C0301FF', '0x303C0301FE'), ('0x00010400', '0x00010300')):
            section_71 = window.split('### 7.1 ', 1)[1].split('### 7.2 ', 1)[0]
            self.assertIn(old, section_71, f'the seed {old!r} no longer matches section 7.1: fix the seed, not the check')
            self.assertTrue(self.section_20_problems(self.text, window.replace(old, new)), f'a seeded fault in section 7.1 went unseen: {old!r} -> {new!r}')

    @classmethod
    def section_21_problems(cls, text):
        """What is wrong with section 21, boot 23 (the third run of the SP11 monitor entry, M1 rung 3, `cpus=1`), or with the places that count on it; an empty list is a pass."""
        body = text.split('## 21. Result of boot 23', 1)[1].split('## 22. Result of boot 24', 1)[0]
        flat = ' '.join(body.split())
        problems = []
        pages = re.findall(r'```text\n(.*?)```', body, re.S)
        if [len(page.splitlines()) for page in pages] != [38]:
            return ['the page of boot 23 is not one block of 38 lines']
        page = pages[0]
        lines = page.splitlines()
        counted = re.search(r'PAYLOAD DONE INDEX=1 .* LINES=(\d+)', page)
        if not counted or int(counted.group(1)) != len([line for line in lines if line.startswith('SNAPREP ')]):
            problems.append('SNAPREP lines are not the LINES= count of PAYLOAD DONE INDEX=1')
        if len([line for line in lines if line.startswith('SNAPMON ')]) != 5 or 'PAYLOAD DONE INDEX=0 RETURN=0X0 STACK=OK LOADS=2 FAULTS=0 REFUSED=0 LINES=29 DROPPED=0' not in page:
            problems.append('the five SNAPMON lines left on the page, or the wrapper\'s LINES=29, changed')
        ticks = [int(n) for n in re.findall(r'MON RET=0X0 STATUS=0X0 ENTERED=1 TICKS=(\d+) STACK=10888/262144', page)]
        if ticks != [41593665, 41593665]:
            problems.append(f'the call is not 41,593,665 ticks on both lines: {ticks}')
        elif not (0 < 96_000_000 - ticks[0] and abs(ticks[0] / 19_200_000 - 2.166) < 5e-4 and ticks[0] - 39245286 == 2348379 and abs(2348379 / 19_200_000 - 0.122) < 5e-4):
            problems.append('the arithmetic of the call: 2.166 s inside the box of 5 s (96,000,000 ticks), and 2,348,379 ticks (0.122 s) more than boot 21\'s 39,245,286')
        for needle in ('MON STATUS=OK ENTERED=0X1', 'MON COUNTS EVENTS=512 TRAPS=135562 ABORTS=0 STEPS=989 CALLS=65209 DROPPED=96264',
                       'SNAPREP MON STOP GUEST_EXC PC=0XFFFFFE000B8C2288 A=0X2000000 B=0XFFFFFEA937FDC000 C=0XFFFFFE000B8C22\n',
                       'SNAPREP MON EXIT PC=0XFFFFFE000B86C000 SPSR=0X604003C5 ESR=0X8200000F FAR=0X0', 'MON REGS RESTORED=YES',
                       'GIC RESTORE CLEAN=YES FAILED=0X0 RETRIED=0X0 BEGUN=GRC VBAR=NONE EL1=NONE', 'MON VERDICT=RETURNED FLAGS=0XF SUB=7', 'PAYLOAD CALL 1 EE86676001D89CF6', 'PAYLOAD VERIFY=OK',
                       'SNAPMON STAGE=DONE RETURN=0', 'SNAPREP GIC NEW GICD=0X13 RB=0X12 ISEN=0X4000000 PRIO=0X80 PMR=0XF8 IGRPEN1=1', 'SNAPREP CHUNKS TOTAL=46 COPIED=46 HASHED=27 BYTES_COPIED=161041856 BYTES_HASHED=118904848',
                       'SNAPREP GUEST.DAT BASE=0XC5CFE000 BYTES=118908480', 'SNAPREP TICKS HZ=19200000 PLACED=', 'DESC=221', 'SNAPREP MON IMAGE OFF=0X20000 BYTES=316783 SHA=0XAD4EBAA27314296C BOX_S=5',
                       'SNAPREP MON CALL X0=0XCE7D6000 X1=0X89B000000 X2=0XF000000 X3=0X88000C000 X4=0X0 SP=0XCE856000',
                       'SNAPREP MON EV 3 MONITOR PC=0XFFFFFE000C22B9E0 A=0X12 B=0XFFFFFE000B86FBB0 C=0XFFFFFE0006F84000'):
            if needle not in page:
                problems.append(f'the page lost {needle!r}')
        if lines[-1] != 'PAYLOAD DONE INDEX=1 RETURN=0X0 STACK=OK LOADS=1 FAULTS=0 REFUSED=0 LINES=29 DROPPED=0' or 'MON CON' in page:
            problems.append('the page does not end at PAYLOAD DONE INDEX=1, or has a MON CON line (a serial=2 boot has none)')
        # The stop, decoded from the words on the page: the document's decode must be the arithmetic of ESR_EL1 0x2000000, ESR_EL2 0x8200000F and SPSR 0x604003C5.
        esr_el1, esr_el2, spsr, x30 = 0x2000000, 0x8200000F, 0x604003C5, 0xFFFFFE000B8C2288
        if (esr_el1 >> 26, (esr_el1 >> 25) & 1, esr_el1 & 0x1FFFFFF) != (0, 1, 0):
            problems.append('the decode of A=0X2000000 is not EC 0, IL 1, ISS 0')
        if ((esr_el2 >> 26) & 0x3F, (esr_el2 >> 25) & 1, esr_el2 & 0x3F) != (0x20, 1, 0x0F):
            problems.append('the decode of ESR 0x8200000F is not EC 0x20, IL 1, IFSC 0x0F')
        if (spsr & 0xF, (spsr >> 6) & 0xF, (spsr >> 22) & 1) != (5, 0xF, 1):
            problems.append('the decode of SPSR 0x604003C5 is not EL1h, DAIF masked, PAN set')
        elr_low, elr_high, elr = 0xFFFFFE000B8C2200, 0xFFFFFE000B8C22FF, 0xFFFFFE000B8C22A0
        if not (elr_low <= x30 <= elr_high and elr_low <= elr <= elr_high and elr - x30 == 0x18 and 'C=0XFFFFFE000B8C22\n' in page and int('FFFFFE000B8C22', 16) << 8 == elr_low):
            problems.append('x30, the 14 digits of C= and the inferred ELR 0xFFFFFE000B8C22A0 do not lie in the 256 bytes 0xFFFFFE000B8C2200 to 0xFFFFFE000B8C22FF, 0x18 after x30')
        if not 0xFFFFFE0008A08000 <= x30 < 0xFFFFFE000C264000 or not 0xFFFFFE0008A08000 <= elr_high < 0xFFFFFE000C264000:
            problems.append('the stop is not in the kernelcache\'s __TEXT_EXEC range')
        stop_text = 'MON STOP GUEST_EXC PC=0XFFFFFE000B8C2288 A=0X2000000 B=0XFFFFFEA937FDC000 C=0XFFFFFE000B8C22A0'
        if len(stop_text) != 94 or len(stop_text) + len('SNAPREP ') != 102 or not 100 < len(stop_text) + len('SNAPREP ') <= 102:
            problems.append('the stop line is 94 characters, 102 with the SNAPREP prefix: two digits fall off a page of 100 columns')
        if not (54 * 32768 == 1769472 and 9 * 32768 == 294912 and 54 // 9 == 6 and 1769472 // 294912 == 6 and 54 % 9 == 0):
            problems.append('the log queue arithmetic of 21.6: 54 slots and 1,769,472 bytes against 9 slots and 294,912 bytes, a ratio of 6')
        if not (abs(16.0 + 2.166 - 18.2) < 0.1 and 18.3 < 25.19 and abs(25.19 - 18.3 - 6.9) < 0.1):
            problems.append('the film in time: the call of 2.166 s from 16.0 s, the page static from 18.3 s, the film of 25.19 s')
        # No kernel address beyond the page's own values, and no instruction word, disassembly or function offset of the analysis note (21.5).
        allowed = {token.upper() for token in re.findall(r'0[xX]FFFFFE[0-9A-Fa-f]*', page)} | {'0XFFFFFE000B8C22A0', '0XFFFFFE000B8C2200', '0XFFFFFE000B8C22FF', '0XFFFFFE0008A08000', '0XFFFFFE000C264000'}
        stray = sorted({token.upper() for token in re.findall(r'0[xX]FFFFFE[0-9A-Fa-f]*', body)} - allowed)
        if stray:
            problems.append(f'section 21 quotes kernel addresses that are not the page\'s: {stray}')
        if re.search(r'E7FFDEFF|DEADDEFF|B8C106C|B8C25D8|48BE288|B8C221C|B8C2310|pacibsp|retab|TPIDR|GENTER|AppleCotaBTM|configSampling', body, re.I):
            problems.append('section 21 gives an instruction word, a function offset, a per-instruction detail or a claim about the cause of the panic')
        for phrase in (
                # what ran, how it was read
                'Boot 23 (2026-11-18, about 15:59 by the unit\'s clock) is the third run of the SP11 monitor entry and the first with a CPU-count word in the guest\'s command line',
                'the operator approved a record of its own for rung 3 by its SHA-256 at about 15:05 (this log does not repeat the digest)',
                '`cpus=1` after rung 2\'s command line (`-v debug=0x8 serial=2 cluster_power=0 wdt=-1 cpus=1`), which asks XNU to run with one CPU',
                '`GUEST.DAT` is 118,908,480 bytes in 46 chunks, the size of boot 21\'s', 'the time box is 5 s and the label is `M1RUN-CPUS1-RUNG3`',
                'The film is one video of 25.19 s (59.94 frames a second, 3840 x 2160, not CRC-verified) that the operator stopped about 7 s after the report page appeared; the record asked for 60 s',
                'what the unit did after the film ended is not on it', '`SHA=A7E159AD6C18832C`', '`STAGE=PIN_OK LABEL=M1RUN-CPUS1-RUNG3 CHUNKS=46`',
                'q1n1\'s heartbeat reads `UPTIME 18 S` at the end of the film; no black-out and no restart in the 7 s after the page that the film holds',
                # the page and its limits
                'its full text is 94 characters (arithmetic, with a 16-digit `C=`), the runner adds the 8-character prefix `SNAPREP `, and 102 columns do not fit the page\'s 100, so the last two hex digits of `C=` are not on the page; '
                '`REPORT.DAT` is a RAM area that the run does not write back to the stick (row f of 21.3: the staged file on the stick is unchanged), so they cannot be read from the stick either',
                'The `TICKS HZ` line is partly under a reflection of the panel',
                'The whole call took 41,593,665 ticks of the 19.2 MHz counter, 2.166 s (arithmetic), against the box of 5 s (96,000,000 ticks): the box was not used up.',
                '41,593,665 (2.166 s), 2,348,379 ticks (0.122 s) more', '`TRAPS`, `CALLS`, `STEPS` and `DROPPED` are cumulative counts, not rates.', 'why `STEPS` is 989 where boot 21\'s was 925 is UNKNOWN',
                # the verdict
                'this is the result, by the letter of the record\'s 5d: `MON STOP GUEST_EXC` is not among the ends that PASSED allows and no other result names it',
                'but the stop is `GUEST_EXC`, which PASSED does not allow',
                'not the case: no `panic(cpu` text was read in the frames examined; the host analysis (21.5) INFERS that the kernel had entered its debugger funnel and that the monitor stopped it at the trap, before any text was printed '
                '| OBSERVED (the absence in the frames examined); INFERRED (why)',
                'not exercised: nothing mismatched, so no `REFUSED` line, which is no pass', 'held, and proved: `TICKS=41593665` is 2.166 s',
                'the five 16-digit prefixes on the film are those of the staged files (the coordinator read them against the stage tree)',
                'held: every file of the staged list passes `shasum -a 256 -c` from the stick\'s root (18 of 18, every line OK, exit 0)',
                '`REPORT.DAT` still has the digest of the all-zero staged file (the prefix `2AF25E8AC647A885` of the film\'s `PAYLOAD LOADED` line: a run does not write the report back, because `REPORT.DAT` is loaded into RAM, '
                'so the page is the only record of the run, as after boots 20 and 21)',
                'and the only changes since the staging at 15:12 are `SP11\\DONE1.TXT` (42 bytes, the Shell\'s marker `AUTORUN M1RUN-CPUS1-RUNG3 STEP 1 STARTED`) and macOS\'s own `.fseventsd` entry; '
                'the unit\'s own storage was not read back (NOT RUN) | OBSERVED (host check of the stick, read only, about 16:32, one boot) |',
                'unchanged by git diff between ea59368 (the tree of boot 20) and 3f48b0a (the source of the record) (VERIFIED, host; says nothing about hardware); the byte comparison of the builds was not repeated for this tree (NOT RUN)',
                '(`0xFFFFFE0008A08000` to `0xFFFFFE000C264000`; VERIFIED, host arithmetic on the local copy, as in 19.3)',
                'M1\'s own verdict (record, 5a) is the same page\'s: M1 PASSED by the rows a, c, d and e of the page, with row f held by the host check of the stick and row g held on the host.',
                'Rung 3\'s own verdict is **NOT PASSED, (c), by the letter of 5d**', 'It is not (a): `cpus=1` was honoured. It is not (b): no panic text was seen on the panel. It is not PARTIAL: the CPU-start stop is gone.',
                'it is neither softened nor upgraded here. The approval is spent (`PAYLOAD CALL 0` is on the film), and gate X4\'s "kernel executes" stays at PARTIAL (one vCPU, 2.17 s, further than boot 21;',
                # the decode
                '`MON STOP GUEST_EXC` is the event \'XNU took its own exception\': the monitor records the guest\'s `x30`, `ESR_EL1`, `FAR_EL1` and `ELR_EL1` as the stop line\'s `PC=`, `A=`, `B=` and `C=` '
                '(`probes/xnuboot/report_layout.c`, line 139, and `probes/xnuboot/trap_dispatch.c`, lines 262 to 340; VERIFIED, read in the sources, not run on the unit)',
                '`ESR_EL1`: exception class 0 (`unknown reason`), `IL` 1 (a 32-bit instruction), `ISS` 0: an unallocated or undefined instruction taken at EL1', '`FAR_EL1`, stale and not meaningful for class 0',
                '`ELR_EL1`, the address of the instruction that raised the exception, in `0xFFFFFE000B8C2200` to `0xFFFFFE000B8C22FF`: the same 256 bytes as `x30`',
                '`ESR` class `0x20` (an instruction abort from a lower exception level), `IFSC` `0x0F` (a permission fault, level 3 by the encoding); `SPSR` is EL1h with D, A, I and F masked and PAN set (the layout from memory)',
                'An exception of class 0 reaches the `GUEST_EXC` record only when the monitor\'s optional `skip_undef` (step over such an exception and count it) is off (VERIFIED, read in `probes/xnuboot/trap_dispatch.c`, lines 292 to 317), '
                'so it was off in this run (INFERRED).',
                'The monitor stops at the vector entry, before XNU\'s own handler runs, so what XNU would have done with the exception, and whether it would have printed anything, is not on the page.',
                # the host analysis
                'A read-only analysis on the host after the boot (a file, not a run: the local kernelcache and the public XNU source, tag `xnu-12377.121.6`; the KDK is absent, so no symbolication: NOT RUN)',
                'hold 64 words and the guest\'s `x30`; a host decode rejects exactly one of them as unallocated (VERIFIED, host; says nothing about hardware): XNU\'s own debugger-trap word, a public constant '
                '(`TRAP_DEBUGGER` in `osfmk/kern/debug.c` and `GDB_TRAP_INSTR2` in `osfmk/arm/trap_internal.h`)',
                'The exact `ELR` is therefore INFERRED to be `0xFFFFFE000B8C22A0` (high confidence; not read, because two digits of the page are clipped), 0x18 bytes after the page\'s `PC=`.',
                'So the stop is the guest kernel\'s own debugger trap and not a CPU feature that the unit lacks (INFERRED, high confidence)',
                'so hiding a feature in the ID registers does not apply, and emulating an instruction in the monitor is the wrong tool (INFERRED:',
                'The kernel therefore entered its panic or debugger funnel (INFERRED; a panic is far likelier than a stackshot or a profile-guided-optimisation path), and the panic text, the backtrace and any halt or reboot '
                'are printed only after that trap, which the monitor stopped on, so the panel shows none.',
                'Which call entered the funnel, why, and so the cause are UNKNOWN.',
                'The function that holds the word is INFERRED, by structure against the public source, to be the debugger-trap function of `osfmk/kern/debug.c` (`DebuggerTrapWithState` there); no kernel name was read.',
                # the panel
                '54 is 6 x 9 and 1,769,472 is 6 x 294,912 (arithmetic): the same ratio as six CPUs to one.',
                'So `cpus=1` was honoured by this guest\'s kernel (OBSERVED: the processor lines; INFERRED: that the log queue is sized by the CPU count).',
                'that is not proof of absence, because the replay changes screens faster than the film samples, so the side-by-side frames are the evidence',
                'the driver classes matched an `(ANS2)` provider and probed it',
                'The provider of the probe lines is the `(ANS2)` node of the guest\'s device tree (INFERRED: the M1 bundle carries no ANS model, section 18 of [SP11-STICK-MONITOR.md](SP11-STICK-MONITOR.md), so no model stands behind that node); '
                'the lines say that the classes matched, and nothing on the panel says that any of them started or finished (UNKNOWN).',
                'No `panic(cpu` text was read in the frames examined (OBSERVED once; a film is not proof of absence, and 21.5 says why none is expected).', 'a firmware banner and not a panic',
                # what it settles, what it does not, the options
                'The kernel ran on one vCPU for 2.17 s under the monitor, further than boot 21\'s 2.04 s',
                'M1 passed by its own rows (record, 5a; row f held by the host check of the stick, row g on the host); rung 3\'s own verdict is NOT PASSED (c) by the letter of its record (21.3).',
                'Why the kernel entered its debugger funnel, which call entered it, and so the cause of the panic (UNKNOWN): no register, slot or console tail of the run was kept, and the text is printed only after the trap. '
                'Whether `cpus=1` has anything to do with it cannot be told from boots 21 and 23 (UNKNOWN).',
                'The exact `ELR` (INFERRED `0xFFFFFE000B8C22A0`, not read) and the function name (INFERRED, by structure, no KDK).',
                'That any driver started or finished, that ANS2 works, that the storage path is reachable, or anything about timing beyond `TICKS`.',
                'Whether the unit stayed up after the film ended (7 s after the page) (UNKNOWN). The stick cannot say: it holds no record of the run beyond the Shell\'s marker, because `REPORT.DAT` is loaded into RAM and not written back '
                '(row f, 21.3; OBSERVED once, host read), so the clipped digits of `C=` and anything else that the page does not show are not on it.',
                'This boot does not show that macOS boots, nor stability or performance.',
                'Let the guest\'s own panic handler run: the monitor already has a `let_panic` diagnostic, a data word of its PARAMS that hands such an exception to the guest\'s own handler (off in this run, INFERRED, since the stop was recorded)',
                'It is data only: the monitor\'s two programs would stay byte-identical (INFERRED); it needs a record of its own and the operator\'s decision.',
                'Whether the panic path survives on this stack, and what follows the text (a halt, a reboot, a device reset, a stage-2 fault at a reset register or the watchdog at the box end), are UNKNOWN.',
                'Stepping over the trap with `skip_undef` is not an option: the kernel would fall silently into its post-trap path (INFERRED).',
                'The approval was for one boot and is spent. Nothing is authorised by this section.'):
            if phrase not in flat:
                problems.append(f'section 21 lost {phrase[:70]!r}')
        if flat.count('macOS boots') != 1:
            problems.append('"macOS boots" is not said exactly once, in the sentence that denies it')
        if re.search(r'\b[0-9a-fA-F]{32,}\b', body) or '749361cf' in text.lower():
            problems.append('section 21 or the log gives a record digest')
        for row in body.split('### 21.3', 1)[1].split('### 21.4', 1)[0].splitlines():
            if row.startswith('| ') and not row.startswith(('| ---', '| Result or row')) and not re.search(r'OBSERVED|VERIFIED|INFERRED|UNKNOWN|NOT RUN', row.rsplit('|', 2)[-2]):
                problems.append(f'a row of 21.3 has no label: {row[:50]!r}')
        whole = ' '.join(text.split())
        for place, needle in (('the top paragraph', 'Boot 23 (2026-11-18, section 21) ran rung 3 of the monitor entry with one CPU asked for (`cpus=1`, plan `M1RUN-CPUS1-RUNG3`): the guest honoured it, the CPU-start stop of boot 21 is gone, '
                                                    'the kernel ran on one vCPU for 2.17 s, further than boot 21, and the monitor stopped it at an exception that it took at EL1 (`MON STOP GUEST_EXC`), INFERRED to be the kernel\'s own debugger trap '
                                                    '(OBSERVED, one unit, one boot, on video; the identity of the stop is INFERRED and the cause UNKNOWN); the rung\'s own verdict is NOT PASSED (c) by the letter of its record, and M1\'s is PASSED.'),
                              ('item 17 of section 1', '17. **Boot 23 (2026-11-18; section 21; on video; OBSERVED, one unit, one boot): M1 rung 3, the console panel with `cpus=1` (plan `M1RUN-CPUS1-RUNG3`): the guest honoured `cpus=1`, '
                                                       'the CPU-start stop is gone, and the monitor stopped the guest at an exception it took at EL1 (`MON STOP GUEST_EXC`) 2.17 s into the call; the record\'s rung 3 verdict is NOT PASSED (c) by its letter, M1\'s own (5a) PASSED.**'),
                              ('item 17 of section 1', 'A host analysis after the boot INFERS (high confidence) that the exception is the guest kernel\'s own debugger trap, so that the kernel had entered its panic or debugger funnel; its text is printed only '
                                                       'after the trap, which the monitor stopped on, so the panel shows none, and the cause is UNKNOWN.'),
                              ('item 17 of section 1', 'and not that macOS boots. No next step is authorised. 18. **Boot 24'),
                              ('the evidence summary', 'The third run of the SP11 monitor entry (boot 23, M1 rung 3, plan `M1RUN-CPUS1-RUNG3`, `cpus=1` in the guest\'s command line)'),
                              ('the evidence summary', 'the cause and anything past the stop are UNKNOWN; rung 3 is NOT PASSED (c) by the letter of its record, M1 PASSED by its own rows | 21 |'),
                              ('section 20', 'Boot 23 (section 21) was the next run of the monitor entry, rung 3.')):
            if needle not in whole:
                problems.append(f'{place} does not count on boot 23')
        return problems

    def test_section_21_keeps_the_facts_of_boot_23(self):
        """Section 21 transcribes one page read from one film (the third run of the monitor entry, rung 3, `cpus=1`), decodes its stop, gives the classification of the record by its letter and the host analysis of the stop with
        the labels of its note, and says what it does not settle; sections 1, 9 and 20 count on it: a drifted value, count or label fails here, and each seeded fault below must be found."""
        self.assertEqual(self.section_21_problems(self.text), [])
        body_seeds = (
            ('SNAPREP MON STOP GUEST_EXC PC=0XFFFFFE000B8C2288', 'SNAPREP MON STOP S2_FAULT PC=0XFFFFFE000B8C2288'), ('ENTERED=1 TICKS=41593665 STACK=10888/262144\nSNAPMON MON REGS', 'ENTERED=1 TICKS=39245286 STACK=10888/262144\nSNAPMON MON REGS'),
            ('ABORTS=0 STEPS=989', 'ABORTS=3 STEPS=989'), ('TRAPS=135562', 'TRAPS=136424'), ('ESR=0X8200000F FAR=0X0', 'ESR=0X8200000E FAR=0X0'), ('A=0X2000000 B=0XFFFFFEA937FDC000 C=0XFFFFFE000B8C22\n', 'A=0X2000100 B=0XFFFFFEA937FDC000 C=0XFFFFFE000B8C22\n'),
            ('C=0XFFFFFE000B8C22\nSNAPREP MON EXIT', 'C=0XFFFFFE000B8C22A0\nSNAPREP MON EXIT'), ('SPSR=0X604003C5', 'SPSR=0X604003C4'), ('LINES=29 DROPPED=0\n```\n\nThe 29 `SNAPREP` lines', 'LINES=28 DROPPED=0\n```\n\nThe 29 `SNAPREP` lines'),
            ('SNAPREP GIC NEW GICD=0X13', 'SNAPREP GIC NEW GICD=0X10'), ('SNAPREP MONITOR RETURNED STAGE=DONE', 'SNAPREP MONITOR RETURNED STAGE=DONE\nSNAPREP MON CON DARWIN KERNEL'),
            ('PAYLOAD CALL 1 EE86676001D89CF6', 'PAYLOAD CALL 1 EE86676001D89CF7'), ('X0=0XCE7D6000 X1=', 'X0=0XCE7F0000 X1='),
            ('its full text is 94 characters', 'its full text is 90 characters'), ('so they cannot be read from the stick either', 'so they can be read from the stick'),
            ('2.166 s (arithmetic), against the box of 5 s', '2.166 s (arithmetic), against the box of 2 s'), ('2,348,379 ticks (0.122 s) more', '2,348,379 ticks (1.22 s) more'),
            ('this is the result, by the letter of the record\'s 5d', 'this is not the result, by the letter of the record\'s 5d'), ('Rung 3\'s own verdict is **NOT PASSED, (c), by the letter of 5d**', 'Rung 3\'s own verdict is **PASSED, by the letter of 5d**'),
            ('It is not (a): `cpus=1` was honoured. It is not (b)', 'It is (a): `cpus=1` was not honoured. It is not (b)'), ('M1 PASSED by the rows a, c, d and e of the page', 'M1 FAILED by the rows a, c, d and e of the page'),
            ('with row f held by the host check of the stick and row g held on the host.', 'with row g held on the host and row f not yet read back.'),
            ('passes `shasum -a 256 -c` from the stick\'s root (18 of 18, every line OK, exit 0)', 'passes `shasum -a 256 -c` from the stick\'s root (17 of 18, one line FAILED)'),
            ('a run does not write the report back, because `REPORT.DAT` is loaded into RAM, so the page is the only record of the run', 'a run writes the report back, so the stick holds the record of the run'),
            ('`REPORT.DAT` still has the digest of the all-zero staged file', '`REPORT.DAT` has a new digest'), ('(42 bytes, the Shell\'s marker `AUTORUN M1RUN-CPUS1-RUNG3 STEP 1 STARTED`)', '(44 bytes, the Shell\'s marker `AUTORUN M1RUN-CPUS1-RUNG3 STEP 1 STARTED`)'),
            ('the unit\'s own storage was not read back (NOT RUN) | OBSERVED (host check of the stick, read only, about 16:32, one boot) |', 'the unit\'s own storage was read back (PASS) | VERIFIED (the stick) |'),
            ('(row f, 21.3; OBSERVED once, host read), so the clipped digits', '(row f, 21.3; VERIFIED), so the clipped digits'), ('so the clipped digits of `C=` and anything else that the page does not show are not on it.', 'so the clipped digits of `C=` can be read from it.'),
            ('(row f of 21.3: the staged file on the stick is unchanged), so they cannot', '(row f of 21.3: the staged file on the stick is unchanged), so they can'),
            ('(record, 5a; row f held by the host check of the stick, row g on the host)', '(record, 5a; row f not yet read back, row g on the host)'),
            ('(VERIFIED, host; says nothing about hardware); the byte comparison of the builds was not repeated for this tree (NOT RUN)', '(VERIFIED, host); the byte comparison of the builds was repeated (PASS)'),
            ('`ISS` 0: an unallocated or undefined instruction taken at EL1', '`ISS` 0: a data abort taken at EL1'), ('`IFSC` `0x0F` (a permission fault, level 3 by the encoding)', '`IFSC` `0x05` (a translation fault, level 1 by the encoding)'),
            ('so it was off in this run (INFERRED)', 'so it was on in this run (INFERRED)'), ('as unallocated (VERIFIED, host; says nothing about hardware)', 'as unallocated (OBSERVED, on the unit)'),
            ('The exact `ELR` is therefore INFERRED to be', 'The exact `ELR` is therefore VERIFIED to be'), ('lacks (INFERRED, high confidence)', 'lacks (VERIFIED, high confidence)'),
            ('and emulating an instruction in the monitor is the wrong tool (INFERRED:', 'and emulating an instruction in the monitor is the right tool (INFERRED:'),
            ('Which call entered the funnel, why, and so the cause are UNKNOWN.', 'Which call entered the funnel, why, and so the cause are known.'), ('so the panel shows none. Which call', 'so the panel shows the text. Which call'),
            ('a panic is far likelier than a stackshot', 'a stackshot is far likelier than a panic'), ('no kernel name was read.', 'the name was read from the KDK.'),
            ('0x18 bytes after the page\'s `PC=`.', '0x18 bytes after the page\'s `PC=`, in the function at 0xFFFFFE000B8C221C.'), ('XNU\'s own debugger-trap word, a public constant', 'XNU\'s own debugger-trap word 0xE7FFDEFF, a public constant'),
            ('and so the cause are UNKNOWN.', 'and so the cause is an assertion in AppleCotaBTM.'),
            ('54 is 6 x 9 and 1,769,472 is 6 x 294,912 (arithmetic)', '54 is 6 x 9 and 1,769,472 is 5 x 294,912 (arithmetic)'), ('the driver classes matched an `(ANS2)` provider and probed it', 'the driver classes started and ran the storage path'),
            ('nothing on the panel says that any of them started or finished (UNKNOWN)', 'every one of them started and finished (OBSERVED)'),
            ('cannot be told from boots 21 and 23 (UNKNOWN).', 'is the cause (VERIFIED).'), ('This boot does not show that macOS boots, nor stability or performance.', 'This boot shows that macOS boots, and stability.'),
            ('Stepping over the trap with `skip_undef` is not an option', 'Stepping over the trap with `skip_undef` is the option'), ('(off in this run, INFERRED, since the stop was recorded)', '(on in this run, VERIFIED)'),
            ('The approval was for one boot and is spent. Nothing is authorised by this section.', 'The approval stands for further boots.'), ('five 16-digit prefixes on the film are those of the staged files', 'five 16-digit prefixes on the film are not those of the staged files'),
            ('at about 15:05 (this log does not repeat the digest)', 'at about 15:05 (the digest is 749361CF)'), ('| OBSERVED (the absence in the frames examined); INFERRED (why) |', '| OBSERVED (the absence) |'),
            ('## 22. Result of boot 24', 'Digest 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\n\n## 22. Result of boot 24'))
        place_seeds = (
            ('the rung\'s own verdict is NOT PASSED (c) by the letter of its record, and M1\'s is PASSED.', 'the rung PASSED and so did M1.'),
            ('the record\'s rung 3 verdict is NOT PASSED (c) by its letter, M1\'s own (5a) PASSED.**', 'the record\'s rung 3 verdict is PASSED.**'),
            ('and not that macOS boots. No next step is authorised.\n\n18. **Boot 24', 'and macOS boots. A next step is authorised.\n\n18. **Boot 24'), ('so the panel shows none, and the cause is UNKNOWN.', 'so the panel shows the text, and the cause is known.'),
            ('the cause and anything past the stop are UNKNOWN; rung 3 is NOT PASSED (c) by the letter of its record, M1 PASSED by its own rows | 21 |', 'the cause is known; rung 3 PASSED | 21 |'),
            ('Boot 23 (section 21) was the next run of the monitor entry, rung 3.', 'Boot 23 was a run.'))
        marker = '## 21. Result of boot 23'
        head, rest = self.text.split(marker, 1)
        limit = rest.index('## 22. Result of boot 24') + len('## 22. Result of boot 24')
        for old, new in body_seeds:
            self.assertIn(old, rest[:limit], f'the seed {old!r} no longer matches the text: fix the seed, not the check')
            self.assertTrue(self.section_21_problems(head + marker + rest.replace(old, new, 1)), f'a seeded fault went unseen: {old!r} -> {new!r}')
        for old, new in place_seeds:
            self.assertIn(old, head, f'the seed {old!r} no longer matches the text: fix the seed, not the check')
            self.assertTrue(self.section_21_problems(head.replace(old, new, 1) + marker + rest), f'a seeded fault went unseen: {old!r} -> {new!r}')

    @classmethod
    def section_22_problems(cls, text):
        """What is wrong with section 22, boot 24 (the fourth run of the SP11 monitor entry, M1 rung 4, the monitor's `let_panic` word set), or with the places that count on it; an empty list is a pass."""
        body = text.split('## 22. Result of boot 24', 1)[1].split('## 23. Result of boot 25', 1)[0]
        flat = ' '.join(body.split())
        problems = []
        pages = re.findall(r'```text\n(.*?)```', body, re.S)
        if [len(page.splitlines()) for page in pages] != [11, 31, 9]:
            return ['the blocks of boot 24 are not the runner page (11 lines), the report page (31 lines) and the panic text (9 lines)']
        runner, report, panic = pages
        report_lines = report.splitlines()
        counted = re.search(r'PAYLOAD DONE INDEX=1 .* LINES=(\d+)', report)
        if not counted or int(counted.group(1)) != 35 or len([line for line in report_lines if line.startswith('SNAPREP ')]) + 13 != int(counted.group(1)):
            problems.append('the report page counts 35 lines: its 22 SNAPREP lines and the 13 that 22.2 leaves out')
        if 29 + 6 != 35 or len([line for line in report_lines if line.startswith('SNAPREP MON CON ')]) != 6:
            problems.append('the 35 lines of boot 24 are the 29 of boots 21 and 23 and six MON CON lines')
        for needle in ('SHA=1D32F5071506A00C', 'SHA=EE86676001D89CF6', 'SHA=5EFFB5C128395793', 'SHA=CE9B363C8794D8ED', 'SHA=2AF25E8AC647A885', 'PAYLOAD VERIFY=OK', 'PAYLOAD CALL 0 1D32F5071506A00C',
                       'PAYLOAD READY FILES=5 RANGES=8 CODE=2', 'STAGE=PIN_OK LABEL=M1RUN-LETPANIC-RUNG4 CHUNKS=46', 'BOX_S=5', 'BYTES=118908480'):
            if needle not in runner:
                problems.append(f'the runner page lost {needle!r}')
        for needle in ('MON RET=0X0 STATUS=0X0 ENTERED=1 TICKS=43133334 STACK=10888/262144', 'MON REGS RESTORED=YES', 'GIC RESTORE CLEAN=YES FAILED=0X0 RETRIED=0X0 BEGUN=GRC VBAR=NONE EL1=NONE', 'MON STATUS=OK ENTERED=0X1',
                       'MON COUNTS EVENTS=512 TRAPS=143889 ABORTS=0 STEPS=953 CALLS=64986 DROPPED=94674',
                       'SNAPREP MON STOP S2_FAULT PC=0XFFFFFE0008BA7358 A=0X3082BC224 B=0X938A0005 C=0XFFFFFE612A564224',
                       'SNAPREP MON EXIT PC=0XFFFFFE0008BA7358 SPSR=0X604003C5 ESR=0X938A0005 FAR=0XFFFFFE612A564224',
                       'SNAPREP MON EV 0 OTHER PC=0X0 A=0X1201201023111111 B=0X21 C=0X512F0011', 'SNAPREP MON EV 4 SYSREG PC=0XFFFFFE000C2448CC A=0X3A7FE2 B=0X0 C=0X0',
                       'SNAPREP MON CON PANIC(CPU 0 CALLER 0XFFFFFE00097DDEF0): ?APPLEPMGR: VIRTUAL VOID', 'SNAPREP MON CON COMPLEX ? _CPUCOMPLEXCOUNT? ?APPLEPMGR.CPP:4861?',
                       'SNAPREP MON CON NESTED PANIC DETECTED - ENTRY COUNT: 2 PANIC_CALLER: 0XFFFFFE000\nSNAPREP MON CON C245398?', 'SNAPREP MON CON KERNEL TEXT EXEC SLIDE: 0X0000000004868000?'):
            if needle not in report:
                problems.append(f'the report page lost {needle[:70]!r}')
        ticks = int(re.search(r'TICKS=(\d+)', report).group(1))
        if not (96_000_000 > ticks == 43_133_334 and abs(ticks / 19_200_000 - 2.2465) < 5e-5 and ticks - 41_593_665 == 1_539_669 and abs(1_539_669 / 19_200_000 - 0.080) < 5e-4 and ticks - 39_245_286 == 3_888_048):
            problems.append('the arithmetic of the call: 43,133,334 ticks are 2.2465 s inside the box of 5 s, 1,539,669 ticks (0.080 s) more than boot 23 and 3,888,048 more than boot 21')
        esr, ipa, far, spsr = 0x938A0005, 0x3082BC224, 0xFFFFFE612A564224, 0x604003C5
        if ((esr >> 26) & 0x3F, (esr >> 25) & 1, (esr >> 24) & 1, (esr >> 22) & 3, (esr >> 16) & 0x1F, (esr >> 6) & 1, esr & 0x3F) != (0x24, 1, 1, 2, 10, 0, 5):
            problems.append('the decode of ESR 0x938A0005 is not EC 0x24, IL 1, ISV 1, word, SRT 10, a read, DFSC 5')
        if ipa & 0xFFF != far & 0xFFF or ipa & 0xFFF != 0x224 or (spsr & 0xF, (spsr >> 6) & 0xF, (spsr >> 22) & 1) != (5, 0xF, 1):
            problems.append('the IPA and the FAR share the offset 0x224, and SPSR 0x604003C5 is EL1h, DAIF masked, PAN set')
        if not (0xFFFFFE0008A08000 <= 0xFFFFFE0008BA7358 < 0xFFFFFE000C264000) or 0xE4800000 <= ipa < 0xE4800000 + 0x1518000:
            problems.append('the stop PC is in the kernelcache\'s __TEXT_EXEC range and the IPA is outside the panel range')
        pmgr = (0xFFFFFE000977EB30, 0xFFFFFE00097E507B)
        if not (pmgr[0] <= 0xFFFFFE00097DDEF0 <= pmgr[1] and pmgr[0] <= 0xFFFFFE00097A352C <= pmgr[1] and not pmgr[0] <= 0xFFFFFE0008BA7358 <= pmgr[1]):
            problems.append('the panic caller and boot 21\'s stop PC lie in ApplePMGR\'s range on the panel and boot 24\'s stop PC does not')
        if not (len('AUTORUN M1RUN-LETPANIC-RUNG4 STEP 1 STARTED') + 2 == 45 and len('M1RUN-LETPANIC-RUNG4') == 20):
            problems.append('the marker of row f is 45 bytes and the label 20 characters')
        for needle in ('panic(cpu 0 caller 0xfffffe00097ddef0): "ApplePMGR: virtual void ApplePMGR::enableCPUComplex(UInt32, bool):4861 REQUIRE failed: complex < _cpuComplexCount" @ApplePMGR.cpp:4861',
                       'Nested panic detected - entry count: 4 panic_caller: 0xfffffe000c245398', 'Nested panic count exceeds limit 5, machine will reset or spin', 'wdog: panic chipreset', 'Kernel text exec slide: 0x0000000004868000'):
            if needle not in panic:
                problems.append(f'the panic text lost {needle[:60]!r}')
        for phrase in (
                # what ran, how it was read
                'Boot 24 (2026-11-18, about 21:35 +04, the film\'s creation time) is the fourth run of the SP11 monitor entry and the first with the monitor\'s `let_panic` word set.',
                'the operator approved a record of its own for rung 4 by its SHA-256 at about 21:31 (this log does not repeat the digest)',
                'the monitor\'s PARAMS word `let_panic` (word 23, offset 0xB8) is 1 where rung 3\'s was 0, so that a guest exception that reaches that branch of the monitor runs XNU\'s own handler instead of ending the run',
                'The command line is rung 3\'s (`-v debug=0x8 serial=2 cluster_power=0 wdt=-1 cpus=1`)', '`SNAPMON.BIN` and `SNAPREP.BIN` are the frozen pair of boots 20, 21 and 23 (the same 16-digit prefixes on the page)',
                '`GUEST.DAT` is 118,908,480 bytes in 46 chunks', 'the time box is 5 s and the label is `M1RUN-LETPANIC-RUNG4`',
                'PANIC TEXT, NOT A RESULT, NOT PASSED (a), WATCHDOG with no text, NEW STOP and NOT PASSED (b)',
                'one 56.2 s film at 3840 x 2160 and 30 frames a second, not CRC-verified',
                'the three still photographs of the panel that the record asks for were not taken (they are optional)',
                'q1n1\'s first-line heartbeat counts on, read from `UPTIME 12 S` to `UPTIME 33 S`: no black-out and no restart in the film',
                # the pages and their limits
                'its 29 lines of boots 21 and 23 are now 35', 'boots 21 and 23 had no `MON CON` line at all, and boot 20\'s six were the banner and four early lines',
                'That the ring holds nothing before the panic, and that the panic path writes its text through the serial-putc call that the monitor logs where the ordinary log goes to the panel (`serial=2`), are INFERRED.',
                '43,133,334 (2.2465 s), 1,539,669 ticks (0.080 s) more than boot 23', 'The five events `EV 0` to `EV 4` are boots 21\'s and 23\'s, word for word.',
                'The whole call took 2.2465 s (arithmetic), against the box of 5 s (96,000,000 ticks): the box was not used up.',
                # the panic text
                'the panic\'s caller, `0xfffffe00097ddef0`, lies inside the range that the text itself lists for `com.apple.driver.ApplePMGR` (`0xfffffe000977eb30` to `0xfffffe00097e507b`: VERIFIED, host arithmetic on the panel\'s values)',
                'the kexts in the backtrace are `ApplePMGR` and `AppleT8140PMGR`, with the dependencies `AppleARMPlatform`, `IOReportFamily` and `Libm` (their UUIDs, and the kernel\'s, are on the panel and are not copied here)',
                'a backtrace whose last frames repeat a cycle of twelve frames for each level; the last block read is level 4 to 5',
                'the address `0xfffffe000b8c22a0`, which 21.5 INFERRED to be the debugger-trap site of boot 23, is one of the frames of every level\'s backtrace | OBSERVED (the address on the panel); INFERRED (that it is the same site)',
                'The text is printed through the kernel\'s own panic path, which the monitor lets run because `let_panic` is set (INFERRED, K12); the nested panics are the same path entered again (22.6).',
                # the stop
                'is the monitor\'s own stage-2 fault stop, as in boot 21 (19.5)', 'Decode of `ESR` `0x938A0005` (INFERRED, from the layout as remembered): exception class `0x24` (a data abort from a lower exception level), `ISV` 1, access size word (32 bits), '
                'register `SRT` 10 (`w10`), `WnR` 0 (a read), `DFSC` `0x05` (a translation fault at level 1: no stage-2 mapping)',
                'So the guest read a 32-bit value from the unmapped intermediate physical address `0x3082BC224` (`FAR` `0xFFFFFE612A564224`, the same page offset `0x224`).',
                'so the read is INFERRED to be the kernel\'s reset request to the SoC watchdog or the power manager; which device holds `0x3082BC000`, and what the code at the PC is, are UNKNOWN until the guest\'s device tree and the kernelcache are read (22.6)',
                'The unit did not reset: the page and the heartbeat stayed up in the film.',
                # the verdict
                'met: the panel shows `panic(cpu 0 caller 0xfffffe00097ddef0): "ApplePMGR: ..."` and the nested panics (22.3); this is the first result that fits, so the film is in it',
                'not the case: there is text, and the stop is a stage-2 fault at another PC', 'not the case: the stop is `S2_FAULT` 2.2465 s into the call, and there is text',
                'held: `PAYLOAD READY FILES=5 RANGES=8 CODE=2` and `PAYLOAD VERIFY=OK` twice, and the five 16-digit prefixes on the film are those that the record names for its files (the coordinator\'s reading)',
                'not exercised: nothing mismatched, so no `REFUSED` line, which is no pass', 'held, and proved: `TICKS=43133334` is 2.2465 s',
                'held: every file of the staged list passes `shasum -a 256 -c` from the stick\'s root (18 of 18, exit 0), `REPORT.DAT` still has the digest of the all-zero staged file '
                '(the prefix `2AF25E8AC647A885` of the film\'s `PAYLOAD LOADED` line: a run does not write the report back, so the page is the only record of the run), '
                'and the only changes since the staging are `SP11\\DONE1.TXT` (45 bytes, the Shell\'s marker `AUTORUN M1RUN-LETPANIC-RUNG4 STEP 1 STARTED`, written at about 21:35) and macOS\'s own `.fseventsd` entry; '
                'the unit\'s own storage was not read back (NOT RUN) | OBSERVED (host check of the stick, read only, one boot) |',
                'lists two added files, `compat/nvme-takeover.c` and `compat/nvme-takeover.h` (the host-tested NVMe takeover core), and no changed file (VERIFIED, host; says nothing about hardware); a fresh baseline comparison of the builds was not re-run (NOT RUN)',
                'M1 PASSED by the rows a, c, d and e of the page, with row f held by the host check of the stick and row g held as above. Rung 4\'s class is **PANIC TEXT**, the first result of the record\'s order that fits; '
                'it is a result and not a pass or a failure, and it is separate from M1\'s.',
                'The frozen monitor did not stop with `MON STOP UNNAMED` (the record\'s concern about a refused call after the panic entry): INFERRED, since either no SPTM call that the monitor refuses was made after the panic entry or none at all.',
                'The approval is spent (`PAYLOAD CALL 0` is on the film), and gate X4\'s "kernel executes" stays at PARTIAL',
                # the reading and the open questions
                'about 2.2 s into the guest\'s run, with `cpus=1`. Boot 21 (six CPUs, no `cpus=1`) passed this driver and stopped later, at the first CPU-start write (`PC=0xFFFFFE00097A352C`, which lies inside the range that this panic\'s text lists for ApplePMGR: '
                'VERIFIED, host arithmetic on the page\'s and the panel\'s values); so the assertion is new with `cpus=1`.',
                'A hypothesis, not verified: `_cpuComplexCount` counts the complexes of the CPUs that stay enabled, while the boot CPU\'s complex index is not below that count.',
                'That boot 23\'s exception was the same panic is INFERRED and not shown: its text was never printed, and its guest differs from this one\'s in the one data word.',
                'Whether the cycle is XNU\'s normal handling of that trap on this platform or an artefact of the monitor (a vector page, a missing state, a refused call) is UNKNOWN.',
                '`let_panic` did what the record hoped (OBSERVED: the kernel\'s own handler ran and printed; the mechanism is INFERRED).',
                '(UNKNOWN until a read-only host analysis of the local J700 26A428 kernelcache and of the guest\'s device tree answers them; this section records the questions and no answer)',
                '- Q1: what the symbolised return addresses', '- Q2: what `enableCPUComplex` checks', '- Q3: what in the guest\'s device tree and in `cpus=1` decides it', '- Q4: the ranked fixes', '- Q5: why each nesting level panics again with an empty string',
                '- Q6: which device holds the intermediate physical address `0x3082BC000`',
                # what it settles and does not
                'With the monitor\'s `let_panic` word set, the kernel\'s own exception handler ran where boot 23\'s run had been stopped, and the kernel printed a panic text on the panel',
                'The monitor\'s console ring carried the first six lines of the panic text onto the report page, which boots 21 and 23 did not have (the route is INFERRED).',
                'M1 passed again by its own rows (record, 5a; row f held by the host check of the stick, row g held as in 22.5); rung 4\'s class is PANIC TEXT (22.5).',
                'Why the assertion failed, whether `cpus=1` is what trips it, and whether boot 23\'s exception was the same panic: readings only (22.6), with the questions open.',
                'That the kernel gets further with any change, or boots: there is no second boot, and each statement of this section is one run of one unit.',
                'Nothing about SMP, a root device, storage, userspace, stability or performance, that any driver started or finished, or that macOS boots.',
                'The approval was for one boot and is spent. Nothing is authorised by this section, and no next step is chosen here: the open questions of 22.6 come first.',
                'Hardware: boot 24 only; every other statement of this section is a reading of its film or of the sources on the host.'):
            if phrase not in flat:
                problems.append(f'section 22 lost {phrase[:70]!r}')
        if flat.count('macOS boots') != 1:
            problems.append('"macOS boots" is not said exactly once, in the sentence that denies it')
        if re.search(r'\b[0-9a-fA-F]{32,}\b', body) or '67d814f6' in text.lower() or '749361cf' in text.lower() or re.search(r'[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}', body):
            problems.append('section 22 or the log gives a record digest or a UUID')
        for context in re.findall(r'VERIFIED.{0,45}', flat):
            if 'host' not in context:
                problems.append(f'a VERIFIED in section 22 that is not a host fact: {context!r}')
        allowed = {token.upper() for token in re.findall(r'0[xX]FFFFFE[0-9A-Fa-f]*', ' '.join(pages), re.I)} | {'0XFFFFFE000B8C22A0', '0XFFFFFE000B8C2288', '0XFFFFFE00097A352C', '0XFFFFFE0008A08000', '0XFFFFFE000C264000',
                                                                                                                  '0XFFFFFE000977EB30', '0XFFFFFE00097E507B'}
        stray = sorted({token.upper() for token in re.findall(r'0[xX]FFFFFE[0-9A-Fa-f]*', body, re.I)} - allowed)
        if stray:
            problems.append(f'section 22 quotes kernel addresses that are not the page\'s or the panel\'s: {stray}')
        if re.search(r'E7FFDEFF|DEADDEFF|pacibsp|retab|TPIDR|GENTER|AppleCotaBTM|configSampling|kc-disas|file offset', body, re.I):
            problems.append('section 22 gives an instruction word, a per-instruction detail, a file offset or a claim about the cause of the panic')
        if re.search(r'desktop|userspace boots|is stable|stability is', flat, re.I):
            problems.append('section 22 claims more than a panic text')
        for row in body.split('### 22.5', 1)[1].split('### 22.6', 1)[0].splitlines():
            if row.startswith('| ') and not row.startswith(('| ---', '| Result or row')) and not re.search(r'OBSERVED|VERIFIED|INFERRED|UNKNOWN|NOT RUN', row.rsplit('|', 2)[-2]):
                problems.append(f'a row of 22.5 has no label: {row[:50]!r}')
        whole = ' '.join(text.split())
        for place, needle in (('the top paragraph', 'Boot 24 (2026-11-18, section 22) ran rung 4, the same guest with the monitor\'s `let_panic` word set: the kernel\'s own handler ran and printed a panic text, '
                                                    'an ApplePMGR assertion (`enableCPUComplex`, `complex < _cpuComplexCount`), then nested panics, and the monitor stopped the run at a stage-2 fault 2.25 s into the call '
                                                    '(OBSERVED, one unit, one boot, on video; why the assertion failed is UNKNOWN); the record\'s class for rung 4 is PANIC TEXT, and M1 PASSED. Boot 25 (2026-11-19, section 23) ran rung 5,'),
                              ('item 18 of section 1', '18. **Boot 24 (2026-11-18; section 22; on video; OBSERVED, one unit, one boot): M1 rung 4, the console panel with `cpus=1` and the monitor\'s `let_panic` word set (plan `M1RUN-LETPANIC-RUNG4`): '
                                                       'the kernel\'s own exception handler ran where boot 23\'s run had been stopped and printed a panic text on the panel, and the monitor stopped the run at a stage-2 fault 2.25 s into the call '
                                                       '(`MON STOP S2_FAULT`, `A=0X3082BC224`); the record\'s class for rung 4 is PANIC TEXT, M1\'s own verdict (5a) PASSED.**'),
                              ('item 18 of section 1', 'No next step is authorised. Hardware: boot 24 only.'),
                              ('the evidence summary', 'The fourth run of the SP11 monitor entry (boot 24, M1 rung 4, plan `M1RUN-LETPANIC-RUNG4`, `cpus=1` and the monitor\'s `let_panic` word set)'),
                              ('the evidence summary', 'rung 4\'s class is PANIC TEXT, M1 PASSED by its own rows; a later host analysis reads those causes in the kernelcache (23.6) | 22 |'),
                              ('section 21.5', 'Boot 24 (section 22) printed the kernel\'s panic text for a guest that differs from this one\'s only in the `let_panic` word, so that the funnel is a panic there (OBSERVED); that boot 23\'s was the same panic is INFERRED.'),
                              ('section 21.7', 'Boot 24 (section 22) let the kernel\'s handler run, and its panic text named an ApplePMGR assertion; that boot 23\'s exception was the same panic, and that `cpus=1` is what trips it, are INFERRED and not shown.'),
                              ('section 21.7', 'This option was taken in boot 24 (section 22): the handler ran and printed a panic text.'),
                              ('section 21.7', 'Rung 4 (section 22) took the `let_panic` option.'),
                              ('section 19.6', 'Boot 24 (section 22) printed such a text: an ApplePMGR CPU-complex assertion with `cpus=1`; that it is the A16\'s is UNKNOWN.'),
                              ('section 19.2', 'boot 24\'s page had six `MON CON` lines again, the first lines of a panic text: section 22')):
            if needle not in whole:
                problems.append(f'{place} does not count on boot 24')
        return problems

    def test_section_22_keeps_the_facts_of_boot_24(self):
        """Section 22 transcribes the pages and the panic text of one film (the fourth run of the monitor entry, rung 4, `let_panic` set), decodes the stop, gives the classification by the record's letter and the open
        questions with their labels, and says what it does not settle; sections 1, 9, 19, 21 count on it: a drifted value, count or label fails here, and each seeded fault below must be found."""
        self.assertEqual(self.section_22_problems(self.text), [])
        body_seeds = (
            ('SNAPREP MON STOP S2_FAULT PC=0XFFFFFE0008BA7358', 'SNAPREP MON STOP GUEST_EXC PC=0XFFFFFE0008BA7358'), ('ENTERED=1 TICKS=43133334 STACK=10888/262144', 'ENTERED=1 TICKS=41593665 STACK=10888/262144'),
            ('ABORTS=0 STEPS=953', 'ABORTS=3 STEPS=953'), ('TRAPS=143889', 'TRAPS=135562'), ('A=0X3082BC224 B=0X938A0005', 'A=0X300734004 B=0X938A0005'), ('B=0X938A0005 C=0XFFFFFE612A564224', 'B=0X93970045 C=0XFFFFFE612A564224'),
            ('ESR=0X938A0005 FAR=0XFFFFFE612A564224', 'ESR=0X938A0004 FAR=0XFFFFFE612A564224'), ('SPSR=0X604003C5', 'SPSR=0X604003C4'), ('LINES=35 DROPPED=0\n```', 'LINES=29 DROPPED=0\n```'),
            ('SHA=5EFFB5C128395793', 'SHA=A7E159AD6C18832C'), ('SNAPMON STAGE=PIN_OK LABEL=M1RUN-LETPANIC-RUNG4 CHUNKS=46', 'SNAPMON STAGE=PIN_OK LABEL=M1RUN-CPUS1-RUNG3 CHUNKS=46'),
            ('SNAPREP MON CON PANIC(CPU 0 CALLER 0XFFFFFE00097DDEF0)', 'SNAPREP MON CON PANIC(CPU 0 CALLER 0XFFFFFE00097DDEF8)'), ('SNAPREP MON CON C245398?', 'SNAPREP MON CON C245399?'),
            ('Nested panic detected - entry count: 4 panic_caller: 0xfffffe000c245398', 'Nested panic detected - entry count: 6 panic_caller: 0xfffffe000c245398'),
            ('Nested panic count exceeds limit 5, machine will reset or spin\nwdog: panic chipreset\n```', 'Nested panic count exceeds limit 9, machine will reset or spin\nwdog: panic chipreset\n```'),
            ('1,539,669 ticks (0.080 s) more than boot 23', '1,539,669 ticks (0.80 s) more than boot 23'), ('`WnR` 0 (a read)', '`WnR` 1 (a write)'), ('register `SRT` 10 (`w10`)', 'register `SRT` 23 (`w23`)'),
            ('`DFSC` `0x05` (a translation fault at level 1: no stage-2 mapping)', '`DFSC` `0x0F` (a permission fault at level 3)'), ('(a data abort from a lower exception level)', '(an instruction abort from a lower exception level)'),
            ('so the read is INFERRED to be the kernel\'s reset request', 'so the read is VERIFIED to be the kernel\'s reset request'), ('are UNKNOWN until the guest\'s device tree and the kernelcache are read (22.6)', 'are known from the guest\'s device tree (22.6)'),
            ('The unit did not reset: the page and the heartbeat stayed up in the film.', 'The unit reset after the page.'), ('read from `UPTIME 12 S` to `UPTIME 33 S`', 'read from `UPTIME 12 S` to `UPTIME 3 S`'),
            ('a film being in the first that fits: PANIC TEXT,', 'a film being in the first that fits: NEW STOP,'), ('this is the first result that fits, so the film is in it', 'this is the last result that fits, so the film is in it'),
            ('not the case: there is text, and the stop is a stage-2 fault at another PC', 'the case: there is no text, and the stop is boot 23\'s'), ('Rung 4\'s class is **PANIC TEXT**', 'Rung 4\'s class is **NOT PASSED (a)**'),
            ('it is a result and not a pass or a failure, and it is separate from M1\'s.', 'it is a pass, and it is M1\'s.'), ('INFERRED, since either no SPTM call that the monitor refuses was made after the panic entry or none at all.', 'OBSERVED, since a refused call was made after the panic entry.'),
            ('M1 PASSED by the rows a, c, d and e of the page, with row f held', 'M1 FAILED by the rows a, c, d and e of the page, with row f held'),
            ('(18 of 18, exit 0), `REPORT.DAT` still has the digest of the all-zero staged file', '(17 of 18, exit 1), `REPORT.DAT` has a new digest'),
            ('a run does not write the report back, so the page is the only record of the run', 'a run writes the report back, so the stick holds the record of the run'),
            ('`SP11\\DONE1.TXT` (45 bytes,', '`SP11\\DONE1.TXT` (42 bytes,'), ('| OBSERVED (host check of the stick, read only, one boot) |', '| VERIFIED (the stick) |'),
            ('the unit\'s own storage was not read back (NOT RUN)', 'the unit\'s own storage was read back (PASS)'),
            ('lists two added files, `compat/nvme-takeover.c` and `compat/nvme-takeover.h` (the host-tested NVMe takeover core), and no changed file (VERIFIED, host; says nothing about hardware)', 'lists no file (VERIFIED, host)'),
            ('a fresh baseline comparison of the builds was not re-run (NOT RUN)', 'a fresh baseline comparison of the builds was re-run (PASS)'),
            ('(`0xfffffe000977eb30` to `0xfffffe00097e507b`: VERIFIED, host arithmetic on the panel\'s values)', '(`0xfffffe000977eb30` to `0xfffffe00097e507b`: VERIFIED)'),
            ('(their UUIDs, and the kernel\'s, are on the panel and are not copied here)', '(their UUIDs, and the kernel\'s, are AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE)'),
            ('| OBSERVED (the address on the panel); INFERRED (that it is the same site)', '| OBSERVED (the address on the panel and the same site)'),
            ('so the assertion is new with `cpus=1`.', 'so the assertion is old without `cpus=1`.'), ('A hypothesis, not verified:', 'A verified fact:'),
            ('That boot 23\'s exception was the same panic is INFERRED and not shown', 'That boot 23\'s exception was the same panic is VERIFIED'),
            ('(UNKNOWN until a read-only host analysis of the local J700 26A428 kernelcache and of the guest\'s device tree answers them;', '(known from a read-only host analysis of the local J700 26A428 kernelcache and of the guest\'s device tree, which answers them;'),
            ('(the chip-reset path, INFERRED)', '(the chip-reset path, VERIFIED)'),
            ('`let_panic` did what the record hoped (OBSERVED: the kernel\'s own handler ran and printed; the mechanism is INFERRED).', '`let_panic` did what the record hoped (VERIFIED).'),
            ('the route is INFERRED', 'the route is VERIFIED'),
            ('there is no second boot, and each statement of this section is one run of one unit.', 'there is a second boot, and every statement of this section is a rule.'),
            ('Nothing is authorised by this section, and no next step is chosen here', 'The next step is authorised by this section and chosen here'),
            ('Hardware: boot 24 only; every other statement', 'Hardware: boots 24 and 25; every other statement'),
            ('or that macOS boots.', 'or that macOS boots; the desktop appears.'),
            ('offset 0xB8) is 1 where rung 3\'s was 0', 'offset 0xB8) is 0 where rung 3\'s was 1'), ('the same 16-digit prefixes on the page), `GUEST.DAT`', 'other 16-digit prefixes on the page), `GUEST.DAT`'),
            ('## 23. Result of boot 25', 'Digest 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\n\n## 23. Result of boot 25'))
        place_seeds = (
            ('the record\'s class for rung 4 is PANIC TEXT, and M1 PASSED. Boot 25', 'the record\'s class for rung 4 is NOT PASSED (a), and M1 PASSED. Boot 25'),
            ('the record\'s class for rung 4 is PANIC TEXT, M1\'s own verdict (5a) PASSED.**', 'the record\'s class for rung 4 is PASSED.**'), ('No next step is authorised. Hardware: boot 24 only.', 'A next step is authorised.'),
            ('rung 4\'s class is PANIC TEXT, M1 PASSED by its own rows; a later host analysis reads those causes in the kernelcache (23.6) | 22 |', 'rung 4 PASSED | 22 |'),
            ('so that the funnel is a panic there (OBSERVED); that boot 23\'s was the same panic is INFERRED.', 'so that the funnel is a panic there (VERIFIED); that boot 23\'s was the same panic is VERIFIED.'),
            ('and that `cpus=1` is what trips it, are INFERRED and not shown.', 'and that `cpus=1` is what trips it, are shown.'), ('This option was taken in boot 24 (section 22): the handler ran and printed a panic text.', 'This option was not taken.'),
            ('Rung 4 (section 22) took the `let_panic` option.', 'Rung 4 (section 22) did not run.'), ('that it is the A16\'s is UNKNOWN.', 'that it is the A16\'s is VERIFIED.'),
            ('boot 24\'s page had six `MON CON` lines again', 'boot 24\'s page had no `MON CON` line either'))
        marker = '## 22. Result of boot 24'
        head, rest = self.text.split(marker, 1)
        limit = rest.index('## 23. Result of boot 25') + len('## 23. Result of boot 25')
        for old, new in body_seeds:
            self.assertIn(old, rest[:limit], f'the seed {old!r} no longer matches the text: fix the seed, not the check')
            self.assertTrue(self.section_22_problems(head + marker + rest.replace(old, new, 1)), f'a seeded fault went unseen: {old!r} -> {new!r}')
        for old, new in place_seeds:
            self.assertIn(old, head, f'the seed {old!r} no longer matches the text: fix the seed, not the check')
            self.assertTrue(self.section_22_problems(head.replace(old, new, 1) + marker + rest), f'a seeded fault went unseen: {old!r} -> {new!r}')

    @classmethod
    def section_23_problems(cls, text):
        """What is wrong with section 23, boot 25 (the fifth run of the SP11 monitor entry, M1 rung 5, the CPU-start stores acknowledged and dropped), or with the places that count on it; an empty list is a pass."""
        body = text.split('## 23. Result of boot 25', 1)[1].split('## 24. Result of boot 26', 1)[0].split('## Appendix A.', 1)[0]
        flat = ' '.join(body.split())
        problems = []
        pages = re.findall(r'```text\n(.*?)```', body, re.S)
        if [len(page.splitlines()) for page in pages] != [9, 20]:
            return ['the blocks of boot 25 are not the runner page (9 lines) and the report page (20 lines)']
        runner, report = pages
        for line in ('PAYLOAD LOADED INDEX=0 BASE=0XCEA1D000 BYTES=448463 SHA=BB34B9701D89302F ...', 'PAYLOAD LOADED INDEX=2 BASE=0XC5CFE000 BYTES=118908480 SHA=896F56E29DFC5E49 ...',
                     'SNAPMON STAGE=PIN_OK LABEL=M1RUN-ACKPANIC-RUNG5 CHUNKS=46'):
            if line not in runner:
                problems.append(f'the runner page lost {line!r}')
        for line in ('SNAPMON MON RET=0X0 STATUS=0X0 ENTERED=1 TICKS=1155520917 STACK=10888/262144', 'SNAPREP MON IMAGE OFF=0X20000 BYTES=317391 SHA=0X53A239F963E99DF BOX_S=60 ESF=0X0 GPUF=0X0',
                     'SNAPREP MON STATUS=OK ENTERED=0X1', 'SNAPREP MON COUNTS EVENTS=512 TRAPS=168214 ABORTS=0 STEPS=1441 CALLS=66179 DROPPED=54212',
                     'SNAPREP MON STOP WATCHDOG PC=0XFFFFFE000B8716A8 A=0X9600004F B=0XFFFFFECFFBFDC000 C=0XFFFFFE000BA87E', 'SNAPREP MON EXIT PC=0XFFFFFE000B8716A8 SPSR=0X604003C4 ESR=0X0 FAR=0X0',
                     'PAYLOAD DONE INDEX=1 RETURN=0X0 STACK=OK LOADS=1 FAULTS=0 REFUSED=0 LINES=29 DROPPED=0'):
            if line not in report:
                problems.append(f'the report page lost {line!r}')
        for phrase in ('is the fifth run of the SP11 monitor entry. It is the first with the monitor\'s CPU-start acknowledge',
                       'the operator approved a record of its own for rung 5 by its SHA-256 at about 05:50 (this log does not repeat the digest)',
                       'the command line drops `cpus=1` and adds `cpu_boot_timeout_secs=120`', 'the box in PARAMS word 24 is 60 s',
                       'The film ends 3.5 s after the report page appeared, so the record\'s 120 s after the page and its three still photographs were not taken',
                       '`TICKS=1155520917` is 60.1834 s (arithmetic), 3,520,917 ticks (0.1834 s) above the box of 60 s (1,152,000,000 ticks)',
                       'the last two hexadecimal digits of the `C=` of the `MON STOP` line are lost', '`REPORT.DAT` is a RAM area that a run does not write back to the stick',
                       'The page has 29 `SNAPREP` lines (`LINES=29`, as boots 21 and 23) and no `MON CON` line',
                       'No `panic(cpu` text and no `Nested panic` line is on any of the 90 frames at 1 frame a second',
                       'is the instruction after a wait-for-interrupt (WFI) in the kernel\'s retention-idle routine (VERIFIED, read on the host in the local kernelcache: a file, not a run)',
                       'So CPU 0 was in the kernel\'s idle thread when the box ended, with every interrupt masked, and nothing had woken it (INFERRED, high)',
                       '`A=` is `ESR_EL1` `0x9600004F`: exception class `0x25` (a data abort at the same exception level)',
                       'this is the first result that fits, so the film is in it: **WATCHDOG, NO TEXT, with PROGRESS**',
                       'held by the record\'s own rule: `TICKS=1155520917` is 3,520,917 ticks above the box',
                       'every file of the staged list passes `shasum -a 256 -c` from the stick\'s root (18 of 18, exit 0, read only, at about 06:25 on 2026-11-19)',
                       'and the only new file is `SP11\\DONE1.TXT` (45 bytes, the Shell\'s marker `AUTORUN M1RUN-ACKPANIC-RUNG5 STEP 1 STARTED`)',
                       'reports IDENTICAL for its four sets (bare, base, control and fast)',
                       'The falsifiers of the record did not occur: no ApplePMGR assertion, no stop in `0X300734004` to `0X30073400C`, no read of that block and no `cpu 1 failed to boot` panic',
                       'did not occur either (OBSERVED: the run lasted the whole box); whether that driver was started at all is UNKNOWN',
                       'is not supported by the host reading of the monitor\'s code',
                       'Whether any guest interrupt was delivered in boot 25 is UNKNOWN: the monitor\'s report window holds the guest-timer words and the stick reporter prints none',
                       'This is the second run ended by the stop timer (boot 20\'s was at 5 s)', 'That CPU bring-up works: no CPU was started',
                       'Why the kernel was idle and what it waits for: the timer reading is not supported by the code but not refuted by a run (23.6)',
                       'M1 passed again by its own rows (record, 5a); rung 5\'s class is WATCHDOG, NO TEXT, with PROGRESS (23.5).',
                       'Hardware: boot 25 only; every other statement of this section is a reading of its film or of the sources on the host.'):
            if phrase not in flat:
                problems.append(f'section 23 lost {phrase[:70]!r}')
        if re.search(r'[0-9A-Fa-f]{32,}|[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}|64d87443|67d814f6|749361cf', body):
            problems.append('a digest, an identifier or an approval prefix in section 23')
        for context in re.findall(r'VERIFIED.{0,70}', ' '.join(body.split('### 23.6', 1)[0].split())):
            if not re.search(r'host|read|source|arithmetic|chain|code|identity|range|coordinator|check', context):
                problems.append(f'a VERIFIED in section 23 that is not a host fact: {context!r}')
        if re.search(r'desktop|userspace boots|is stable|stability is', flat, re.I) or len(re.findall(r'macOS boots', flat)) != 1 or 'and not that macOS boots' not in flat:
            problems.append('section 23 claims more than a quiet kernel')
        for row in body.split('### 23.5', 1)[1].split('### 23.6', 1)[0].splitlines():
            if row.startswith('| ') and not row.startswith(('| ---', '| Result or row')) and not re.search(r'OBSERVED|VERIFIED|INFERRED|UNKNOWN|NOT RUN', row.rsplit('|', 2)[-2]):
                problems.append(f'a row of 23.5 has no label: {row[:50]!r}')
        whole = ' '.join(text.split())
        for place, needle in (('the top paragraph', 'Boot 25 (2026-11-19, section 23) ran rung 5, the guest\'s six CPUs with the CPU-start stores acknowledged and dropped by the monitor, a panic-text word and a box of 60 s: '
                                                    'no ApplePMGR assertion, no panic text, the kernel ran the whole box on one vCPU, went far beyond boot 21\'s last line and was idle in a wait-for-interrupt when the monitor\'s own watchdog stopped it '
                                                    '(`MON STOP WATCHDOG`, 60.18 s; OBSERVED, one unit, one boot, on video; why it was idle is UNKNOWN); the record\'s class for rung 5 is WATCHDOG, NO TEXT, with PROGRESS, and M1 PASSED.'),
                              ('item 19 of section 1', '19. **Boot 25 (2026-11-19; section 23; on video; OBSERVED, one unit, one boot): M1 rung 5,'),
                              ('item 19 of section 1', 'No next step is authorised. Hardware: boot 25 only.'),
                              ('the evidence summary', 'rung 5\'s class is WATCHDOG, NO TEXT, with PROGRESS, M1 PASSED by its own rows | 23 |'),
                              ('section 22.6', '**Later (the analyses of 2026-11-19: a file, not a run).** Q1 to Q6 were read on the host after the boot;'),
                              ('section 22.7', 'Boot 25 (section 23) took the experiment that these readings named: it dropped `cpus=1` and acknowledged the CPU-start stores.')):
            if needle not in whole:
                problems.append(f'{place} does not count on boot 25')
        return problems

    def test_section_23_keeps_the_facts_of_boot_25(self):
        """Section 23 records boot 25 (M1 rung 5): its page values keep their labels, the class is WATCHDOG, NO TEXT, with PROGRESS, M1 passed, the idle is a reading and its cause UNKNOWN; each seeded fault must be found."""
        self.assertEqual(self.section_23_problems(self.text), [])
        body_seeds = (('is the fifth run of the SP11 monitor entry.', 'is the fourth run of the SP11 monitor entry.'),
                      ('(this log does not repeat the digest)', '(the digest is 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef)'),
                      ('the command line drops `cpus=1` and adds `cpu_boot_timeout_secs=120`', 'the command line keeps `cpus=1` and adds `cpu_boot_timeout_secs=120`'), ('the box in PARAMS word 24 is 60 s', 'the box in PARAMS word 24 is 5 s'),
                      ('SNAPMON STAGE=PIN_OK LABEL=M1RUN-ACKPANIC-RUNG5 CHUNKS=46', 'SNAPMON STAGE=PIN_OK LABEL=M1RUN-LETPANIC-RUNG4 CHUNKS=46'),
                      ('SNAPREP MON STOP WATCHDOG PC=0XFFFFFE000B8716A8', 'SNAPREP MON STOP S2_FAULT PC=0XFFFFFE000B8716A8'),
                      ('SNAPREP MON COUNTS EVENTS=512 TRAPS=168214', 'SNAPREP MON COUNTS EVENTS=512 TRAPS=143889'),
                      ('3,520,917 ticks (0.1834 s) above the box of 60 s', '3,520,917 ticks (0.0834 s) above the box of 60 s'),
                      ('the last two hexadecimal digits of the `C=` of the `MON STOP` line are lost', 'the last two hexadecimal digits of the `C=` of the `MON STOP` line were read from the stick'),
                      ('`REPORT.DAT` is a RAM area that a run does not write back to the stick', '`REPORT.DAT` is a file that a run writes back to the stick'),
                      ('and no `MON CON` line (an empty console ring', 'and six `MON CON` lines (an empty console ring'),
                      ('is on any of the 90 frames at 1 frame a second', 'is on the first frame at 1 frame a second'),
                      ('(VERIFIED, read on the host in the local kernelcache: a file, not a run)', '(VERIFIED, as the unit ran it)'),
                      ('nothing had woken it (INFERRED, high)', 'nothing had woken it (VERIFIED, high)'),
                      ('exception class `0x25` (a data abort at the same exception level)', 'exception class `0x24` (a data abort from a lower exception level)'),
                      ('**WATCHDOG, NO TEXT, with PROGRESS**', '**PANIC TEXT**'),
                      ('(18 of 18, exit 0, read only, at about 06:25 on 2026-11-19)', '(17 of 18, exit 0, read only, at about 06:25 on 2026-11-19)'),
                      ('the only new file is `SP11\\DONE1.TXT` (45 bytes,', 'the only new file is `SP11\\DONE1.TXT` (42 bytes,'),
                      ('reports IDENTICAL for its four sets (bare, base, control and fast)', 'reports IDENTICAL for its one set (bare)'),
                      ('no read of that block and no `cpu 1 failed to boot` panic', 'a read of that block and a `cpu 1 failed to boot` panic'),
                      ('whether that driver was started at all is UNKNOWN', 'whether that driver was started at all is OBSERVED'),
                      ('is not supported by the host reading of the monitor\'s code', 'is supported by the host reading of the monitor\'s code'),
                      ('is UNKNOWN: the monitor\'s report window holds the guest-timer words and the stick reporter prints none', 'is OBSERVED: the monitor\'s report window holds the guest-timer words and the stick reporter prints them'),
                      ('This is the second run ended by the stop timer (boot 20\'s was at 5 s)', 'This is the first run ended by the stop timer'),
                      ('the timer reading is not supported by the code but not refuted by a run (23.6)', 'the timer reading is refuted by a run (23.6)'),
                      ('That CPU bring-up works: no CPU was started', 'That CPU bring-up works: six CPUs were started'),
                      ('Hardware: boot 25 only; every other statement of this section', 'Hardware: boots 24 and 25; every other statement of this section'),
                      ('and not that macOS boots', 'and macOS boots'),
                      ('## 23.6 What', 'Digest 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\n\n## 23.6 What'))
        place_seeds = (('why it was idle is UNKNOWN); the record\'s class for rung 5 is WATCHDOG', 'why it was idle is known); the record\'s class for rung 5 is WATCHDOG'),
                       ('rung 5\'s class is WATCHDOG, NO TEXT, with PROGRESS, M1 PASSED by its own rows | 23 |', 'rung 5\'s class is PANIC TEXT, M1 PASSED by its own rows | 23 |'),
                       ('No next step is authorised. Hardware: boot 25 only.', 'A next step is authorised.'),
                       ('Boot 25 (section 23) took the experiment that these readings named:', 'Boot 25 (section 23) did not run:'),
                       ('the record\'s class for rung 5 is WATCHDOG, NO TEXT, with PROGRESS, and M1 PASSED.', 'the record\'s class for rung 5 is NOT PASSED (a), and M1 PASSED.'))
        marker = '## 23. Result of boot 25'
        head, rest = self.text.split(marker, 1)
        limit = rest.index('## Appendix A.') + len('## Appendix A.')
        for old, new in body_seeds:
            self.assertIn(old, rest[:limit], f'the seed {old!r} no longer matches the text: fix the seed, not the check')
            self.assertTrue(self.section_23_problems(head + marker + rest.replace(old, new, 1)), f'a seeded fault went unseen: {old!r} -> {new!r}')
        for old, new in place_seeds:
            self.assertIn(old, head, f'the seed {old!r} no longer matches the text: fix the seed, not the check')
            self.assertTrue(self.section_23_problems(head.replace(old, new, 1) + marker + rest), f'a seeded fault went unseen: {old!r} -> {new!r}')

    @classmethod
    def section_24_problems(cls, text):
        """What is wrong with section 24, boot 26 (the sixth run of the SP11 monitor entry, M1 rung 6: guest timer interrupts delivered, the stop at the scaler's registers), or with the places that count on it; an empty list is a pass."""
        body = text.split('## 24. Result of boot 26', 1)[1].split('## 25. Result of boot 27', 1)[0].split('## Appendix A.', 1)[0]
        flat = ' '.join(body.split())
        problems = []
        pages = re.findall(r'```text\n(.*?)```', body, re.S)
        if [len(page.splitlines()) for page in pages] != [12, 22]:
            return ['the blocks of boot 26 are not the runner page (12 lines) and the report page (22 lines)']
        runner, report = pages
        for line in ('PAYLOAD LOADED INDEX=1 ... BYTES=58634 SHA=533D004C27A11DA9 ...', 'PAYLOAD LOADED INDEX=2 ... BYTES=118908480 SHA=1DD2E9AC94FDB741 ...',
                     'SNAPMON STAGE=PIN_OK LABEL=M1RUN-ACKPANIC-RUNG6 CHUNKS=46', 'SNAPMON MON IMAGE ... SHA=0X53A239F963E99DF BOX_S=120'):
            if line not in runner:
                problems.append(f'the runner page lost {line!r}')
        for line in ('SNAPMON MON RET=0X0 STATUS=0X0 ENTERED=1 TICKS=1182753011 STACK=10888/262144', 'SNAPREP MON IMAGE OFF=0X20000 BYTES=317391 SHA=0X53A239F963E99DF BOX_S=120',
                     'SNAPREP MON STATUS=OK ENTERED=0X1', 'SNAPREP MON COUNTS EVENTS=512 TRAPS=177001 ABORTS=0 STEPS=1485 CALLS=66809 DROPPED=118428',
                     'SNAPREP MON STOP S2_FAULT PC=0XFFFFFE00095FF9AC A=0X291000004 B=0X93880045 C=0XFFFFFE63AA8A0004', 'SNAPREP MON EXIT PC=0XFFFFFE00095FF9AC SPSR=0X80400204 ESR=0X93880045 FAR=0XFFFFFE63AA8A0004',
                     'SNAPREP MON ESR EC=0X24 ISV=1 SIZE=4 SRT=8 WNR=1 S1PTW=0 DFSC=0X5', 'SNAPREP MON WVAL X8=0X1 DATA=0X1',
                     'SNAPREP MON CLK READS=54084 AFTER=1180850601 FIRST=0X349DE7A8 LAST=0X7B001E29',
                     'SNAPREP MON TIM IRQS=1010 ASSERTS=2036 CLEARS=2036 VECTORS=969 CTL=0X1 CVAL=0X99C3A80F MORE=3',
                     'SNAPREP MON CNT MAPS=62592 MAPFAIL=0 DISP=0 PANIC=0 UNDEF=0 MAPOUT=58299 LOGDROP=118428',
                     'SNAPREP MON ACK COUNT=15 4=0X2 8=0X2 12=0X0 4=0X4 8=0X4 12=0X0 4=0X8 8=0X8 MORE=7',
                     'PAYLOAD DONE INDEX=1 RETURN=0X0 STACK=OK LOADS=1 FAULTS=0 REFUSED=0 LINES=40 DROPPED=0'):
            if line not in report:
                problems.append(f'the report page lost {line!r}')
        for phrase in ('is the sixth run of the SP11 monitor entry and the second with the monitor\'s CPU-start acknowledge',
                       'this log repeats neither the digest nor the wording',
                       'the guest differs from boot 25\'s in four bytes of data, the command line\'s `cpu_boot_timeout_secs=90` (boot 25\'s was 120) and PARAMS word 24, the box, which is 120 s (boot 25\'s was 60 s)',
                       'predicts a TIMER PANIC, `cpu 1 failed to boot for the first time, waited 90 seconds`, about 92 s into the call if one does',
                       'every digit of the page was read by eye from frames and checked in at least two of them (the coordinator\'s reading)',
                       'The film is shorter than the record\'s 120 s after the page; nothing was lost by that',
                       '`TICKS=1182753011` is 61.6017 s (arithmetic), well inside the box of 120 s',
                       'boot 26 made 8,787 more traps and 630 more calls than boot 25 in a call of 1.42 s more',
                       '`CVAL=0X99C3A80F` lies 229,980 guest ticks (about 9.6 ms at 24 MHz) after 1.25 times `LAST`',
                       '`MON ACK COUNT=15` is 15 of the guest\'s 32-bit stores to the power manager\'s CPU-start block that the monitor took and dropped (OBSERVED)',
                       'No panic text is on any of the 92 frames at 1 frame a second',
                       'is the monitor failing closed on a guest access to a device it does not model',
                       '`B=` is `ESR_EL2` `0x93880045`', '`SRT` 8 (the register `w8`), `WnR` 1 (a write)', '`DFSC` `0x05` (a translation fault at level 1: no stage-2 mapping)',
                       '`MON WVAL X8=0X1 DATA=0X1` says that the register written held 1: a 32-bit write of the value 1',
                       'The address `0x291000004` lies in the first window of the local device tree\'s `scaler0` node, which has four windows: `0x291000000` (size `0x38000`), `0x291210000` (size `0x4000`), `0x291204000` (size `0x4000`) and `0x290000000` (size `0x8000`)',
                       'Its DART is a separate node, `dart-scaler`, with one window, `0x291260000` (size `0x20000`), and is not part of `scaler0` (it stays in the quiet profile\'s tree)',
                       '(VERIFIED: read on the host in the local DeviceTree.j700ap, a file, not a run)',
                       'this is the first class that fits, so the film is in it: **NEW STOP**',
                       'held: `TICKS=1182753011` is 61.60 s, inside the box of 120 s',
                       'every file of the staged list passes `shasum -a 256 -c` from the stick\'s root (18 of 18, read only, at about 14:10 on 2026-11-19)',
                       'the only new file is `SP11\\DONE1.TXT` (45 bytes, the Shell\'s marker `AUTORUN M1RUN-ACKPANIC-RUNG6 STEP 1 STARTED`)',
                       'reports IDENTICAL for its four sets',
                       'Rung 6\'s class is **NEW STOP**',
                       'The record\'s prediction, a TIMER PANIC about 92 s into the call, did not occur, and could not: the run ended at 61.60 s on the new stop',
                       'one pulse, not a loop (the earlier reading as a loop is withdrawn: the function has no branch back)',
                       'the second of two 30 s waits of `bsd_init` for the real-time clock\'s resource (`IOKitInitializeTime`, then the calendar\'s first read of the platform clock): 0.4 + 30 + 30 = 60.4 s',
                       'are `kprintf` lines, which go to the serial output and not to the video console (VERIFIED, the public `pe_kprintf` source), so the film cannot show them',
                       'is `Load request for com.apple.nke.pptp failed: <err>` (INFERRED, high;',
                       'in hindsight its page would have fallen in the record\'s class NOT DUE',
                       'The monitor\'s timer counters show the guest\'s timer path working: `MON TIM` shows `VECTORS=969` guest FIQ vector entries',
                       'That guest timer interrupts were delivered to the kernel is INFERRED from them (no other CPU runs)',
                       'the counts are OBSERVED; that guest timer interrupts were delivered to the kernel is INFERRED from them',
                       'The acknowledge took 15 CPU-start stores (`MON ACK COUNT=15`), and nothing waited on the CPUs for 61 s.',
                       'the run ended at 61.60 s, so the prediction was not tested',
                       'That CPU bring-up works (no CPU was started)',
                       'M1 passed again by its own rows (record, 5a); rung 6\'s class is NEW STOP (24.5).',
                       'Nothing is authorised by this section, and no next step is chosen here',
                       'Which of the two waits the film\'s quiet stretch was: the second is INFERRED by the arithmetic of 60.4 s, and the film cannot name it',
                       'Hardware: boot 26 only; every other statement of this section is a reading of its film or of the sources on the host.'):
            if phrase not in flat:
                problems.append(f'section 24 lost {phrase[:70]!r}')
        if re.search(r'[0-9A-Fa-f]{32,}|[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}|d9d79c87|64d87443|67d814f6|749361cf', body):
            problems.append('a digest, an identifier or an approval prefix in section 24')
        for context in re.findall(r'VERIFIED.{0,70}', ' '.join(body.split('### 24.6', 1)[0].split())):
            if not re.search(r'host|read|source|arithmetic|chain|code|identity|range|coordinator|check', context):
                problems.append(f'a VERIFIED in section 24 that is not a host fact: {context!r}')
        if re.search(r'desktop|userspace boots|is stable|stability is', flat, re.I) or len(re.findall(r'macOS boots', flat)) != 1 or 'and not that macOS boots' not in flat:
            problems.append('section 24 claims more than a stopped kernel')
        for row in body.split('### 24.5', 1)[1].split('### 24.6', 1)[0].splitlines():
            if row.startswith('| ') and not row.startswith(('| ---', '| Result or row')) and not re.search(r'OBSERVED|VERIFIED|INFERRED|UNKNOWN|NOT RUN', row.rsplit('|', 2)[-2]):
                problems.append(f'a row of 24.5 has no label: {row[:50]!r}')
        whole = ' '.join(text.split())
        for place, needle in (('the top paragraph', 'Boot 26 (2026-11-19, section 24) ran rung 6, the same acknowledging monitor and guest with `cpu_boot_timeout_secs=90`, a box of 120 s and a reporter that prints the guest timer\'s words: '
                                                    'the timer counters show guest timer interrupts delivered (`MON TIM`, `VECTORS=969`: the counter is OBSERVED, that an interrupt reached the kernel is INFERRED from it), the kernel ran 61.60 s and reached the start of its BSD layer, and the monitor stopped it at a stage-2 fault in the scaler\'s registers '
                                                    '(`MON STOP S2_FAULT`, `A=0X291000004`; OBSERVED, one unit, one boot, on video; why the kernel waited where it did is read from the kernelcache on the host); the record\'s class for rung 6 is NEW STOP, and M1 PASSED.'),
                              ('item 20 of section 1', '20. **Boot 26 (2026-11-19; section 24; on video; OBSERVED, one unit, one boot): M1 rung 6,'),
                              ('item 20 of section 1', 'No next step is authorised. Hardware: boot 26 only.'),
                              ('the evidence summary', 'rung 6\'s class is NEW STOP, M1 PASSED by its own rows | 24 |'),
                              ('section 23.6', 'Boot 26 (section 24) answered the timer question: its timer counters show guest timer interrupts delivered (the counters are OBSERVED; that they reached the kernel is INFERRED), and the 25 s of silence were a wait of `bsd_init` that ended (INFERRED).'),
                              ('section 23.7', 'Boot 26 (section 24) ran after this section was written: it answered the timer question and ended at a new stop.')):
            if needle not in whole:
                problems.append(f'{place} does not count on boot 26')
        return problems

    def test_section_24_keeps_the_facts_of_boot_26(self):
        """Section 24 records boot 26 (M1 rung 6): its page values keep their labels, the class is NEW STOP, M1 passed, the guest's timer path works (OBSERVED counters), the stop and the silence are readings from code, the
        prediction of the timer panic was not tested; each seeded fault must be found."""
        self.assertEqual(self.section_24_problems(self.text), [])
        body_seeds = (('is the sixth run of the SP11 monitor entry', 'is the fifth run of the SP11 monitor entry'),
                      ('this log repeats neither the digest nor the wording', 'the digest is 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef'),
                      ('(boot 25\'s was 120) and PARAMS word 24', '(boot 25\'s was 90) and PARAMS word 24'),
                      ('waited 90 seconds`, about 92 s into the call', 'waited 120 seconds`, about 92 s into the call'),
                      ('SNAPMON STAGE=PIN_OK LABEL=M1RUN-ACKPANIC-RUNG6 CHUNKS=46', 'SNAPMON STAGE=PIN_OK LABEL=M1RUN-ACKPANIC-RUNG5 CHUNKS=46'),
                      ('TICKS=1182753011 STACK=10888/262144', 'TICKS=1155520917 STACK=10888/262144'),
                      ('SNAPREP MON STOP S2_FAULT PC=0XFFFFFE00095FF9AC A=0X291000004', 'SNAPREP MON STOP WATCHDOG PC=0XFFFFFE00095FF9AC A=0X291000004'),
                      ('A=0X291000004 B=0X93880045', 'A=0X300734004 B=0X93880045'),
                      ('SNAPREP MON ESR EC=0X24 ISV=1 SIZE=4 SRT=8 WNR=1', 'SNAPREP MON ESR EC=0X24 ISV=1 SIZE=4 SRT=8 WNR=0'),
                      ('IRQS=1010 ASSERTS=2036 CLEARS=2036 VECTORS=969 CTL=0X1 CVAL=0X99C3A80F MORE=3', 'IRQS=1010 ASSERTS=2036 CLEARS=2036 VECTORS=0 CTL=0X1 CVAL=0X99C3A80F MORE=3'),
                      ('SNAPREP MON ACK COUNT=15', 'SNAPREP MON ACK COUNT=0'),
                      ('COUNTS EVENTS=512 TRAPS=177001', 'COUNTS EVENTS=512 TRAPS=168214'),
                      ('`TICKS=1182753011` is 61.6017 s (arithmetic), well inside the box of 120 s', '`TICKS=1182753011` is 60.1834 s (arithmetic), well inside the box of 120 s'),
                      ('boot 26 made 8,787 more traps', 'boot 26 made 87,870 more traps'),
                      ('lies 229,980 guest ticks (about 9.6 ms at 24 MHz)', 'lies 2,299,800 guest ticks (about 96 ms at 24 MHz)'),
                      ('That guest timer interrupts were delivered to the kernel is INFERRED from them (no other CPU runs)', 'That guest timer interrupts were delivered to the kernel is OBSERVED (no other CPU runs)'),
                      ('the counts are OBSERVED; that guest timer interrupts were delivered to the kernel is INFERRED from them', 'the counts are OBSERVED; that guest timer interrupts were delivered to the kernel is VERIFIED'),
                      ('that the monitor took and dropped (OBSERVED)', 'that the monitor took and dropped (VERIFIED)'),
                      ('No panic text is on any of the 92 frames', 'A panic text is on the last of the 92 frames'),
                      ('`SRT` 8 (the register `w8`), `WnR` 1 (a write)', '`SRT` 8 (the register `w8`), `WnR` 0 (a read)'),
                      ('`DFSC` `0x05` (a translation fault at level 1: no stage-2 mapping)', '`DFSC` `0x0F` (a permission fault at level 3)'),
                      ('says that the register written held 1: a 32-bit write of the value 1', 'says that the register written held 0: a 32-bit write of the value 0'),
                      ('which has four windows: `0x291000000` (size `0x38000`)', 'which has three windows: `0x291000000` (size `0x38000`)'),
                      ('`0x290000000` (size `0x8000`). Its DART is a separate node', '`0x290000000` (size `0x8000`). Its DART is a window of the same node'),
                      ('is not part of `scaler0` (it stays in the quiet profile\'s tree)', 'is part of `scaler0` (it leaves the quiet profile\'s tree)'),
                      ('this is the first class that fits, so the film is in it: **NEW STOP**', 'this is the last class that fits, so the film is in it: **NEW STOP**'),
                      ('Rung 6\'s class is **NEW STOP**', 'Rung 6\'s class is **TIMER PANIC**'),
                      ('(18 of 18, read only, at about 14:10 on 2026-11-19)', '(17 of 18, read only, at about 14:10 on 2026-11-19)'),
                      ('`SP11\\DONE1.TXT` (45 bytes, the Shell\'s marker `AUTORUN M1RUN-ACKPANIC-RUNG6 STEP 1 STARTED`)', '`SP11\\DONE1.TXT` (42 bytes, the Shell\'s marker `AUTORUN M1RUN-ACKPANIC-RUNG6 STEP 1 STARTED`)'),
                      ('reports IDENTICAL for its four sets', 'reports IDENTICAL for its one set'),
                      ('did not occur, and could not: the run ended at 61.60 s on the new stop', 'occurred: the run ended at 92 s on the panic'),
                      ('one pulse, not a loop (the earlier reading as a loop is withdrawn: the function has no branch back)', 'a loop (the first reading is kept: the function has a branch back)'),
                      ('the second of two 30 s waits of `bsd_init` for the real-time clock\'s resource', 'the only wait of `bsd_init` for the real-time clock\'s resource'),
                      ('which go to the serial output and not to the video console (VERIFIED, the public `pe_kprintf` source), so the film cannot show them', 'which go to the video console (VERIFIED, the public `pe_kprintf` source), so the film shows them'),
                      ('is `Load request for com.apple.nke.pptp failed: <err>` (INFERRED, high;', 'is `Load request for com.apple.nke.pptp failed: <err>` (VERIFIED, high;'),
                      ('in hindsight its page would have fallen in the record\'s class NOT DUE', 'in hindsight its page would have fallen in the record\'s class LOST TICK'),
                      ('The acknowledge took 15 CPU-start stores', 'The acknowledge took 5 CPU-start stores'),
                      ('the run ended at 61.60 s, so the prediction was not tested', 'the run ended at 61.60 s, so the prediction was refuted'),
                      ('That CPU bring-up works (no CPU was started)', 'That CPU bring-up works (five CPUs were started)'),
                      ('M1 passed again by its own rows (record, 5a); rung 6\'s class is NEW STOP (24.5).', 'M1 failed by its own rows (record, 5a); rung 6\'s class is NEW STOP (24.5).'),
                      ('the second is INFERRED by the arithmetic of 60.4 s, and the film cannot name it', 'the second is VERIFIED by the film'),
                      ('Nothing is authorised by this section, and no next step is chosen here', 'The next step is authorised by this section and chosen here'),
                      ('Hardware: boot 26 only; every other statement', 'Hardware: boots 25 and 26; every other statement'),
                      ('and not that macOS boots', 'and macOS boots'),
                      ('### 24.6 What', 'Digest 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\n\n### 24.6 What'))
        place_seeds = (('(`MON STOP S2_FAULT`, `A=0X291000004`; OBSERVED, one unit, one boot, on video; why the kernel waited where it did is read from the kernelcache on the host); the record\'s class for rung 6 is NEW STOP, and M1 PASSED.',
                        '(`MON STOP S2_FAULT`, `A=0X291000004`; OBSERVED, one unit, one boot, on video; why the kernel waited where it did is known); the record\'s class for rung 6 is NEW STOP, and M1 PASSED.'),
                       ('rung 6\'s class is NEW STOP, M1 PASSED by its own rows | 24 |', 'rung 6\'s class is TIMER PANIC, M1 PASSED by its own rows | 24 |'),
                       ('No next step is authorised. Hardware: boot 26 only.', 'A next step is authorised.'),
                       ('Boot 26 (section 24) answered the timer question: its timer counters show guest timer interrupts delivered (the counters are OBSERVED; that they reached the kernel is INFERRED),', 'Boot 26 (section 24) did not answer the timer question: no guest timer interrupt is delivered,'),
                       ('Boot 26 (section 24) ran after this section was written: it answered the timer question and ended at a new stop.', 'Boot 26 (section 24) did not run.'))
        marker = '## 24. Result of boot 26'
        head, rest = self.text.split(marker, 1)
        limit = rest.index('## Appendix A.') + len('## Appendix A.')
        for old, new in body_seeds:
            self.assertIn(old, rest[:limit], f'the seed {old!r} no longer matches the text: fix the seed, not the check')
            self.assertTrue(self.section_24_problems(head + marker + rest.replace(old, new, 1)), f'a seeded fault went unseen: {old!r} -> {new!r}')
        for old, new in place_seeds:
            self.assertIn(old, head, f'the seed {old!r} no longer matches the text: fix the seed, not the check')
            self.assertTrue(self.section_24_problems(head.replace(old, new, 1) + marker + rest), f'a seeded fault went unseen: {old!r} -> {new!r}')

    @classmethod
    def section_25_problems(cls, text):
        """What is wrong with section 25, boot 27 (the seventh run of the SP11 monitor entry, M1 rung 7: the quiet profile, the kernel waiting for a root device at the box's end), or with the places that count on it; an empty list is a pass."""
        body = text.split('## 25. Result of boot 27', 1)[1].split('## 26. Result of boot 28', 1)[0]
        flat = ' '.join(body.split())
        problems = []
        pages = re.findall(r'```text\n(.*?)```', body, re.S)
        if [len(page.splitlines()) for page in pages] != [7, 11]:
            return ['the blocks of boot 27 are not the identity lines (7 lines) and the monitor lines (11 lines)']
        identity, monitor = pages
        for line in ('MON IMAGE ... BOX_S=120', 'GUEST.DAT BASE=0XC5D04000 BYTES=118885568', 'CHUNKS TOTAL=46 COPIED=46 HASHED=27', 'PLACE BASE=0X880000000 SIZE=0X2A000000',
                     'PAYLOAD DONE INDEX=0 ... LOADS=2 FAULTS=0 REFUSED=0 LINES=29 ...', 'PAYLOAD DONE INDEX=1 ... LOADS=1 ... LINES=40 DROPPED=0', 'VERDICTS PIN=OK GUEST=OK'):
            if line not in identity:
                problems.append(f'the identity lines lost {line!r}')
        for line in ('MON RET=0X0 STATUS=0X0 ENTERED=1 TICKS=2307553890 STACK=10888/262144', 'MON COUNTS EVENTS=512 TRAPS=195787 ABORTS=0 STEPS=2007 CALLS=65009 DROPPED=129122',
                     'MON STOP WATCHDOG PC=0XFFFFFE000B8716A8 A=0X96000047 B=0XFFFFFE110E00C040', 'MON STOP C=0XFFFFFE000BA87EA0',
                     'MON EXIT PC=0XFFFFFE000B8716A8 SPSR=0X604003C4 ESR=0X0 FAR=0X0', 'MON CLK READS=62887 AFTER=2305631249 FIRST=0X34C3B41C LAST=0XBE30B526',
                     'MON TIM IRQS=1560 ASSERTS=3134 CLEARS=3134 VECTORS=1519 CTL=0X1 CVAL=0XEDFD7657', 'MON TIM WRITES=5679 IRQPC=0XFFFFFE000BA7D070 READS=2014',
                     'MON CNT MAPS=61035 MAPFAIL=0 DISP=0 PANIC=0 UNDEF=0 MAPOUT=56922 LOGDROP=129122', 'MON CNT CONDROP=0 RNGDROP=816 MAPDEV=995 MMIORD=10 MMIOWR=10 UARTTX=0',
                     'MON ACK COUNT=15 ... MORE=7'):
            if line not in monitor:
                problems.append(f'the monitor lines lost {line!r}')
        for phrase in ('Boot 27 is the seventh run of the SP11 monitor entry and the third with the monitor\'s CPU-start acknowledge',
                       'this log repeats neither the digest nor the wording',
                       'its device tree is the profile `quiet`', 'which withdraws 43 nodes (the video decoder, the scaler, the ProRes engine and nine audio roots, with the nodes under them; 395 nodes become 352)',
                       'and the command line\'s `cpu_boot_timeout_secs` is 120 (boot 26\'s was 90)', 'GUEST.DAT is 118,885,568 bytes',
                       'It names six results for rung 7 (its section 5d)',
                       'a machine OCR of 150 frames (9,258 lines) that cannot read the skewed or dim frames',
                       'so t0 is 26 s of the film, give or take 1 s',
                       'The record expected `Waiting on <dict>` about 62 s in; the film has it at t0 + 60 s, 2 s earlier (OBSERVED).',
                       '`TICKS=2307553890` is 120.1851 s, 3,553,890 ticks (0.1851 s) above the box of 120 s (arithmetic)',
                       'boot 27 made 18,786 more traps and 522 more steps than boot 26 and 1,800 fewer calls, in a call of 58.58 s more',
                       '`ABORTS=0`, `MAPFAIL=0`, `DISP=0` and `UNDEF=0` say that no monitor-side fault happened',
                       '`VECTORS=1519` guest FIQ vector entries and `IRQS=1560` monitor timer interrupts in 120.185 s, 12.6 entries a second',
                       '`CVAL=0XEDFD7657` lies 4,232,167 guest ticks (about 176 ms at 24 MHz) after 1.25 times `LAST`',
                       'No panic text is on any frame',
                       'No `RTC did not show up`, no `getGMTTimeOfDay` and no `!BSD` line is on the film',
                       'and it is not true of the clock driver\'s own start lines: three of them are on the film (OBSERVED, items 2 and 3)',
                       'is on no frame: the OCR of every frame finds `regist` only in `apfs_sysctl_register`, `ktriage_register` and `done registering sysctls`',
                       'That the driver\'s start does not reach its publish, because its offset read fails, is INFERRED; whether the failed reads are the cause is UNKNOWN until the host analysis.',
                       'is the monitor\'s own stop timer ending the run at the box\'s end',
                       'The PC is boot 25\'s, `0XFFFFFE000B8716A8`: the same address in the same kernelcache',
                       '`A=` is `ESR_EL1` `0x96000047`: exception class `0x25` (a data abort at the same exception level), a 32-bit instruction, `DFSC` `0x07` (a translation fault at level 3) and `WnR` 1 (a write)',
                       'A fault of the kernel at that address that the kernel handled is INFERRED (no panic text, `PANIC=0`, `UNDEF=0`); which routine is at `0XFFFFFE000BA87EA0`, and why a kernel write faults there, is UNKNOWN',
                       'this is the first class that fits, so the film is in it: **RUNG 7 KERNEL WAITING**',
                       'held by the record\'s own rule: `TICKS=2307553890` is 3,553,890 ticks above the box',
                       'the only new file is `SP11\\DONE1.TXT` (47 bytes, the Shell\'s marker `AUTORUN M1RUN-ACKPANIC-Q-RUNG7 STEP 1 STARTED`)',
                       'every file of the staged list is byte-identical to the stage tree on the stick (18 of 18, the host verification exit 0',
                       'held on the host (the coordinator\'s check; says nothing about hardware): `git diff --stat` of rung 6\'s tree (the commit `0f6174d`) against the committed tree of this record (the commit `5f2f8dd`) over `probes/`, `compat/`, `shadow/`, `config/`, `gpu/` and `drivers/` lists one file, the new builder-side data module `probes/guestprofile_m1.py`',
                       'so the monitor sources of rungs 6 and 7 are identical and the pair on the stick is rung 6\'s',
                       'and was not run again on the committed tree, since no monitor source changed between the two (NOT RUN again) | VERIFIED (host, git diff) |',
                       'M1 PASSED again, by the rows a to e of the page, row f of the stick and row g of the host (a `git diff`, above)',
                       '`MON REGS RESTORED=YES`, printed twice, as a `SNAPMON` line and as a `SNAPREP` line, and its `GIC RESTORE CLEAN=YES` are OBSERVED',
                       '`MON REGS RESTORED=YES` and `GIC RESTORE CLEAN=YES`, `STACK=10888/262144`, `ABORTS=0`; f and g below',
                       'The memory warning of boot 26 is on this film too (a 4K frame; OBSERVED): `memorystatus: System is unhealthy. memorystatus_available_pages: 9094 compressor_size:0`',
                       '9,094 pages of 16 KiB are about 142 MiB (arithmetic), the kernel noticing a guest of 384 MiB (INFERRED that the size is the cause)',
                       'The memory warning of boot 26 appears again in boot 27 (OBSERVED, 25.3: `memorystatus_available_pages: 9094`, 9,094 free pages of 16 KiB, about 142 MiB, `wired_mem_exhaustion: 1` and `no victim found (action: 10)`, no panic): a guest of 384 MiB is a warning so far; whether it is enough for anything later is UNKNOWN.',
                       'Rung 7\'s class is **RUNG 7 KERNEL WAITING**',
                       'None of the record\'s falsifiers occurred',
                       'The quiet profile removed the stop at `A=0X291000004`, and no other unmodelled device stopped the run in 120 s',
                       'Whether those drivers started at all, and whether any of the other 42 withdrawn nodes mattered, is UNKNOWN.',
                       'is the text that `IOFindBSDRoot` prints before its wait for a root device',
                       'so the monitor\'s SPMI model\'s parity bits are the suspect (INFERRED)',
                       'whether the M1 monitor binary carries that model is UNKNOWN',
                       'It is the first run whose kernel passes `bsd_init` and whose last line names what it waits for',
                       'The CPU-boot panic at about 122 s: the box ended before it, as designed',
                       'A root device: the wait is the next stop, and the guest has no storage.',
                       'Nothing is authorised by this section, and no next step is chosen here',
                       'Hardware: boot 27 only; every other statement of this section is a reading of its film or of the sources on the host.'):
            if phrase not in flat:
                problems.append(f'section 25 lost {phrase[:70]!r}')
        if re.search(r'[0-9A-Fa-f]{32,}|[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}|e42f451d|d9d79c87|64d87443|67d814f6|749361cf', body):
            problems.append('a digest, an identifier or an approval prefix in section 25')
        for context in re.findall(r'VERIFIED.{0,70}', flat):
            if not re.search(r'host|read|source|arithmetic|chain|code|identity|range|coordinator|check', context):
                problems.append(f'a VERIFIED in section 25 that is not a host fact: {context!r}')
        if re.search(r'desktop|userspace boots|is stable|stability is', flat, re.I) or len(re.findall(r'macOS boots', flat)) != 1 or 'and not that macOS boots' not in flat:
            problems.append('section 25 claims more than a kernel waiting for a root device')
        for row in body.split('### 25.5', 1)[1].split('### 25.6', 1)[0].splitlines():
            if row.startswith('| ') and not row.startswith(('| ---', '| Result or row')) and not re.search(r'OBSERVED|VERIFIED|INFERRED|UNKNOWN|NOT RUN', row.rsplit('|', 2)[-2]):
                problems.append(f'a row of 25.5 has no label: {row[:50]!r}')
        whole = ' '.join(text.split())
        for place, needle in (('the top paragraph', 'Boot 27 (section 25) ran rung 7, the same acknowledging monitor and reporter with the `quiet` device-tree profile (43 nodes withdrawn by data) and `cpu_boot_timeout_secs=120`: '
                                                    'the scaler stop did not return, the kernel ran the whole box of 120 s through `bsd_init` and was waiting for a root device when the monitor\'s own watchdog stopped it '
                                                    '(`MON STOP WATCHDOG`, 120.185 s;'),
                              ('the top paragraph', 'the record\'s class for rung 7 is RUNG 7 KERNEL WAITING, and M1 PASSED.'),
                              ('item 21 of section 1', '21. **Boot 27 (2026-11-20; section 25; on video; OBSERVED, one unit, one boot): M1 rung 7,'),
                              ('item 21 of section 1', 'No next step is authorised. Hardware: boot 27 only.'),
                              ('item 20 of section 1', 'the two timeout messages of those waits are serial-only (`kprintf`) and cannot be on the film, while the clock driver\'s own start lines can be (three are on boot 27\'s film, section 25).'),
                              ('the evidence summary', 'rung 7\'s class is RUNG 7 KERNEL WAITING, M1 PASSED (the coordinator\'s reading) | 25 |'),
                              ('section 24.6', 'That statement is about those two messages only: the clock driver\'s own start lines are on the film'),
                              ('section 24.7', 'Boot 27 (section 25) ran after this section was written: the quiet profile (the scaler withdrawn by data) removed this stop, and the kernel went through `bsd_init` to a wait for a root device.')):
            if needle not in whole:
                problems.append(f'{place} does not count on boot 27')
        return problems

    def test_section_25_keeps_the_facts_of_boot_27(self):
        """Section 25 records boot 27 (M1 rung 7): its page values keep their labels, the class is RUNG 7 KERNEL WAITING, M1 passed, the wait for a root device is a reading of the last line, the clock driver's start lines are on the film and
        the two timeout messages are not, the root device, the CPU-boot panic and the cause of the failed PMU reads are UNKNOWN; each seeded fault must be found."""
        self.assertEqual(self.section_25_problems(self.text), [])
        body_seeds = (('Boot 27 is the seventh run of the SP11 monitor entry and the third', 'Boot 27 is the sixth run of the SP11 monitor entry and the third'),
                      ('this log repeats neither the digest nor the wording', 'the digest is 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef'),
                      ('and the command line\'s `cpu_boot_timeout_secs` is 120 (boot 26\'s was 90)', 'and the command line\'s `cpu_boot_timeout_secs` is 90 (boot 26\'s was 120)'),
                      ('GUEST.DAT is 118,885,568 bytes', 'GUEST.DAT is 118,908,480 bytes'),
                      ('GUEST.DAT BASE=0XC5D04000 BYTES=118885568', 'GUEST.DAT BASE=0XC5D04000 BYTES=118908480'),
                      ('MON RET=0X0 STATUS=0X0 ENTERED=1 TICKS=2307553890', 'MON RET=0X0 STATUS=0X0 ENTERED=1 TICKS=1182753011'),
                      ('MON STOP WATCHDOG PC=0XFFFFFE000B8716A8 A=0X96000047', 'MON STOP S2_FAULT PC=0XFFFFFE000B8716A8 A=0X96000047'),
                      ('MON COUNTS EVENTS=512 TRAPS=195787', 'MON COUNTS EVENTS=512 TRAPS=177001'),
                      ('CLEARS=3134 VECTORS=1519 CTL=0X1', 'CLEARS=3134 VECTORS=0 CTL=0X1'),
                      ('MON ACK COUNT=15 ... MORE=7', 'MON ACK COUNT=0 ... MORE=7'),
                      ('so t0 is 26 s of the film, give or take 1 s', 'so t0 is 18.8 s of the film, give or take 1 s'),
                      ('`TICKS=2307553890` is 120.1851 s, 3,553,890 ticks (0.1851 s)', '`TICKS=2307553890` is 60.1851 s, 3,553,890 ticks (0.1851 s)'),
                      ('boot 27 made 18,786 more traps', 'boot 27 made 187,860 more traps'),
                      ('lies 4,232,167 guest ticks (about 176 ms at 24 MHz)', 'lies 42,321 guest ticks (about 1.7 ms at 24 MHz)'),
                      ('The record expected `Waiting on <dict>` about 62 s in; the film has it at t0 + 60 s, 2 s earlier (OBSERVED).', 'The record expected `Waiting on <dict>` about 62 s in; the film has it at t0 + 62 s, as expected (OBSERVED).'),
                      ('No panic text is on any frame', 'A panic text is on the last frame'),
                      ('No `RTC did not show up`, no `getGMTTimeOfDay` and no `!BSD` line is on the film', 'A `RTC did not show up` line is on the film'),
                      ('and it is not true of the clock driver\'s own start lines: three of them are on the film', 'and it is true of the clock driver\'s own start lines: none of them is on the film'),
                      ('is on no frame: the OCR of every frame finds `regist` only in', 'is on frame 40: the OCR of every frame finds `regist` also in'),
                      ('because its offset read fails, is INFERRED;', 'because its offset read fails, is VERIFIED;'),
                      ('is the monitor\'s own stop timer ending the run at the box\'s end', 'is the guest\'s own panic ending the run before the box\'s end'),
                      ('The PC is boot 25\'s, `0XFFFFFE000B8716A8`: the same address', 'The PC is boot 26\'s, `0XFFFFFE00095FF9AC`: the same address'),
                      ('`DFSC` `0x07` (a translation fault at level 3) and `WnR` 1 (a write)', '`DFSC` `0x0F` (a permission fault at level 3) and `WnR` 0 (a read)'),
                      ('A fault of the kernel at that address that the kernel handled is INFERRED', 'A fault of the kernel at that address that the kernel handled is VERIFIED'),
                      ('this is the first class that fits, so the film is in it: **RUNG 7 KERNEL WAITING**', 'this is the last class that fits, so the film is in it: **RUNG 7 KERNEL WAITING**'),
                      ('Rung 7\'s class is **RUNG 7 KERNEL WAITING**', 'Rung 7\'s class is **RUNG 7 NEW STOP**'),
                      ('(47 bytes, the Shell\'s marker', '(45 bytes, the Shell\'s marker'),
                      ('(18 of 18, the host verification exit 0', '(17 of 18, the host verification exit 0'),
                      ('so the monitor sources of rungs 6 and 7 are identical', 'so the monitor sources of rungs 6 and 7 differ'),
                      ('and was not run again on the committed tree, since no monitor source changed between the two (NOT RUN again) | VERIFIED (host, git diff) |', 'and was run again on the committed tree, since a monitor source changed between the two (IDENTICAL) | VERIFIED (hardware) |'),
                      ('`MON REGS RESTORED=YES` and `GIC RESTORE CLEAN=YES`, `STACK=10888/262144`', '`MON REGS RESTORED=NO` and `GIC RESTORE CLEAN=YES`, `STACK=10888/262144`'),
                      ('(INFERRED that the size is the cause); this reading does not place', '(VERIFIED that the size is the cause); this reading does not place'),
                      ('9,094 free pages of 16 KiB, about 142 MiB,', '9,094 free pages of 16 KiB, about 14 MiB,'),
                      ('one file, the new builder-side data module `probes/guestprofile_m1.py` (156 lines;', 'three files, the new builder-side data module `probes/guestprofile_m1.py` (156 lines;'),
                      ('and row g of the host (a `git diff`, above)', 'with row g outside this section\'s sources'),
                      ('None of the record\'s falsifiers occurred', 'A falsifier of the record occurred'),
                      ('The quiet profile removed the stop at `A=0X291000004`', 'The quiet profile moved the stop to `A=0X291000004`'),
                      ('and whether any of the other 42 withdrawn nodes mattered, is UNKNOWN.', 'and whether any of the other 42 withdrawn nodes mattered, is VERIFIED.'),
                      ('the monitor\'s SPMI model\'s parity bits are the suspect (INFERRED)', 'the monitor\'s SPMI model\'s parity bits are the cause (VERIFIED)'),
                      ('whether the M1 monitor binary carries that model is UNKNOWN', 'whether the M1 monitor binary carries that model is VERIFIED'),
                      ('It is the first run whose kernel passes `bsd_init`', 'It is the second run whose kernel passes `bsd_init`'),
                      ('the box ended before it, as designed', 'the box ended after it, as designed'),
                      ('A root device: the wait is the next stop, and the guest has no storage.', 'A root device: the guest has storage.'),
                      ('Nothing is authorised by this section, and no next step is chosen here', 'The next step is authorised by this section and chosen here'),
                      ('Hardware: boot 27 only; every other statement', 'Hardware: boots 26 and 27; every other statement'),
                      ('and not that macOS boots', 'and macOS boots'),
                      ('### 25.6 What', 'Digest 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\n\n### 25.6 What'))
        place_seeds = (('Boot 27 (section 25) ran rung 7, the same acknowledging monitor and reporter', 'Boot 27 (section 25) ran rung 6, the same acknowledging monitor and reporter'),
                       ('the record\'s class for rung 7 is RUNG 7 KERNEL WAITING, and M1 PASSED.', 'the record\'s class for rung 7 is NEW STOP, and M1 PASSED.'),
                       ('21. **Boot 27 (2026-11-20; section 25; on video; OBSERVED, one unit, one boot): M1 rung 7,', '21. **Boot 27 (2026-11-20; section 25; on video; VERIFIED, one unit, one boot): M1 rung 7,'),
                       ('No next step is authorised. Hardware: boot 27 only.', 'A next step is authorised.'),
                       ('the two timeout messages of those waits are serial-only (`kprintf`) and cannot be on the film, while the clock driver\'s own start lines can be (three are on boot 27\'s film, section 25).', 'the RTC messages of those waits are serial-only and cannot be on the film.'),
                       ('rung 7\'s class is RUNG 7 KERNEL WAITING, M1 PASSED (the coordinator\'s reading) | 25 |', 'rung 7\'s class is NEW STOP, M1 PASSED (the coordinator\'s reading) | 25 |'),
                       ('That statement is about those two messages only:', 'That statement is about every RTC message:'),
                       ('Boot 27 (section 25) ran after this section was written: the quiet profile (the scaler withdrawn by data) removed this stop,', 'Boot 27 (section 25) did not run: the quiet profile (the scaler withdrawn by data) removed this stop,'))
        marker = '## 25. Result of boot 27'
        head, rest = self.text.split(marker, 1)
        limit = rest.index('## 26. Result of boot 28') + len('## 26. Result of boot 28')
        for old, new in body_seeds:
            self.assertIn(old, rest[:limit], f'the seed {old!r} no longer matches the text: fix the seed, not the check')
            self.assertTrue(self.section_25_problems(head + marker + rest.replace(old, new, 1)), f'a seeded fault went unseen: {old!r} -> {new!r}')
        for old, new in place_seeds:
            self.assertIn(old, head, f'the seed {old!r} no longer matches the text: fix the seed, not the check')
            self.assertTrue(self.section_25_problems(head.replace(old, new, 1) + marker + rest), f'a seeded fault went unseen: {old!r} -> {new!r}')

    @classmethod
    def section_26_problems(cls, text):
        """What is wrong with section 26, boot 28 (the eighth run of the SP11 monitor entry, M1 rung 9, the first root-mount form: the carry worked, the stock kernel ran `launchd` from the RAM-disk root, its first boot task failed, the guest
        panicked in userspace and the monitor stopped the run at the chip-reset read), or with the places that count on it; an empty list is a pass."""
        body = text.split('## 26. Result of boot 28', 1)[1].split('## Appendix A.', 1)[0]
        flat = ' '.join(body.split())
        problems = []
        pages = re.findall(r'```text\n(.*?)```', body, re.S)
        if [len(page.splitlines()) for page in pages] != [10, 12]:
            return ['the blocks of boot 28 are not the identity lines (10 lines) and the monitor lines (12 lines)']
        identity, monitor = pages
        for line in ('CHUNKS TOTAL=339 COPIED=339 HASHED=176', 'TICKS HZ=19200000 PLACED=40419 COPIED=501263277', 'PLACE BASE=0X880000000 SIZE=0X1A8000000 DESC=219', 'GUEST.DAT BASE=0XF8D2A1000 BYTES=1926621440',
                     'PAYLOAD CALL 1 562B9B6EEAF97B87', 'PAYLOAD DONE INDEX=0 ... LOADS=2', 'PAYLOAD DONE INDEX=1 ... LOADS=1', 'STACK=OK', 'STAGE=DONE', 'VERDICTS PIN=OK GUEST=OK POLICY=0X3 LINES=29'):
            if line not in identity:
                problems.append(f'the identity lines lost {line!r}')
        for line in ('MON RET=0X0 STATUS=0X0 ENTERED=1 TICKS=769573015', 'MON REGS RESTORED=YES', 'GIC RESTORE CLEAN=YES FAILED=0X0 RETRIED=0X0', 'MON COUNTS EVENTS=512 TRAPS=648755 ABORTS=0',
                     'MON STOP S2_FAULT PC=0XFFFFFE0008BA7358 A=0X3082BC...', 'MON ESR EC=0X24 ISV=1 SIZE=4 SRT=10 WNR=0', 'MON CLK READS=269808', 'MON TIM IRQS=6525 ASSERTS=12376 CLEARS=12375 ...',
                     'MON CNT MAPS=79342 MAPFAIL=0 DISP=0 PANIC=13 ...', 'MON CNT CONDROP=12455 RNGDROP=3065 MAPDEV=995 ...', 'MON ACK COUNT=15', 'MON CON NESTED PANIC DETECTED - ENTRY COUNT: 2 PANIC...'):
            if line not in monitor:
                problems.append(f'the monitor lines lost {line!r}')
        for phrase in ('Boot 28 is the eighth run of the SP11 monitor entry and the first with a root',
                       'RUNG 8 WAS SKIPPED by the operator and never run: its record, for the clock alone (the quiet profile with the PMU\'s real-time clock bound), stays committed at `4895ea2`',
                       'this log repeats neither the digest nor the wording: it is the rung 9 record of the commit `b455ce2`',
                       'GUEST.DAT is 1,926,621,440 bytes in 339 chunks (291 of them the root, 143 of those ZERO), digest prefix `b7aeddff...`',
                       'it names ten results for rung 9 (its section 5d)',
                       'THE MIDDLE IS ON NO FILM: the end of the wrapper\'s silent phase, the guest\'s first seconds and the kernel\'s whole log up to `launchd`',
                       'the record\'s INFERRED reading of them is now OBSERVED',
                       'the read took about 46 s, about 40 MiB/s',
                       'the hash took about 8 s, a rate of the order of 200 MiB/s',
                       '`GUEST.DAT BASE=0XF8D2A1000`, 62.2 GiB',
                       'base plus size is `0XFFFFFF900`, under 2 KiB below the bound `0XFFFFFFFFF`), and 21.6 GiB above the guest\'s placement end `0XA28000000` (arithmetic)',
                       'one: no `NOT_IN_ONE_DESCRIPTOR` and no `PAYLOAD REFUSED` line, and the guest ran',
                       'it does: the file was read into a buffer at `0XF8D2A1000` and both digests agree',
                       'no restart and no reset in the carry of about two minutes: the flush operations, the firmware\'s watchdog and the Shell\'s state caused none',
                       'the copy was 26.1 s at 19.2 MHz and the placement 2.1 ms; the file\'s 1,926,621,440 bytes over 26.1 s are 73.8 MB/s, 70.4 MiB/s',
                       'NOT TESTED: the run ended at 40.08 s, before the counter\'s wrap at 178.96 s (arithmetic on 24 MHz), and no `MON STOP DEVICE_RESET` came',
                       'The bring-up page appeared at about 57 s of film 1 and stayed unchanged to its end at 95 s, 38 s or more',
                       '`mount_by_role:451: failed to find volume with role = 10 in APFS container with volcnt = 0 for root device /dev/disk1s1 with status 49245`',
                       '`mount_by_role_if_present:556: No volume with role 16 present on /dev/disk1s1.`',
                       '`mount_phase_one:1914: Update volume mount failed - error c05d (errno 93). Ignoring...`',
                       'then ten rounds of `wait_for_node:266: APFS IOKit node lookup timed out...` and `wait_for_node:272: Retrying (9 retries left)...` down to 1 retry, about 3.2 s a round and about 30 s in all',
                       'The roles are the APFS role words: 0x10 is Preboot and 0xC0 is Update (INFERRED from the numbers and the A16 notes).',
                       '`launchd`\'s first boot task asks for the Preboot volume and waits for an APFS IOKit node that does not exist (INFERRED, strongly:',
                       '`panic(cpu 0 caller 0xfffffe000c246d2c): userspace panic: boot task failure: mount-phase-1 - exited due to SIGABRT`',
                       '`Nested panic count exceeds limit 5, machine will reset or spin` and `wdog: panic chipreset`',
                       'The date in `launchd`\'s log, 2026-11-20 00:00:04, is the clock model\'s seed',
                       'plus 4.3 s (arithmetic): the kernel read the PMU clock that the model serves',
                       'how that fits boot 27\'s clock driver start at t0 + 7 s is UNKNOWN',
                       '`TICKS=769573015` is 40.082 s at 19.2 MHz (arithmetic), 13.4 % of the box of 300 s (5,760,000,000 ticks)',
                       '`MON CLK READS` per second of the call is 6,732 against boot 27\'s 523 (arithmetic)',
                       'THE PC IS BOOT 24\'S, the same address in the same kernelcache (OBSERVED)',
                       '`MON ESR EC=0X24 ISV=1 SIZE=4 SRT=10 WNR=0` is boot 24\'s syndrome again',
                       'The `A=` digits after `3082BC` are cut off; boot 24\'s was `0X3082BC224`, and the same address is INFERRED',
                       '`SPSR=0X604003C5` is EL1h with D, A, I and F masked and PAN set, boot 23\'s and boot 24\'s value (arithmetic on the bits)',
                       'So the end of the run is the monitor\'s stop at the kernel\'s chip-reset request after the guest\'s own panic, as boot 24\'s (INFERRED;',
                       'the guest\'s CPU-boot panic that XNU raises about 362 s after the call (INFERRED; the word is 360) was far outside it',
                       'This is the first class of the record\'s order that fits, so the film is in it: **RUNG 9 PANIC TEXT**',
                       'neither is on film 2; the first panic read is the userspace panic; the CPU-boot panic would fall at about 362 s, outside the box; the kernel\'s log from the call to `launchd` is on no film, so an earlier text there is UNKNOWN',
                       'did NOT hold: the run ended by the guest\'s own panic at 40.08 s, a PANIC TEXT end the record also names',
                       'held (the coordinator\'s reading): `STATUS=0X0 ENTERED=1`, `MON STOP S2_FAULT` with `A=` outside the panel\'s range',
                       'held: `TICKS=769573015` is 13.4 % of the box (arithmetic)',
                       'and the only new file is `SP11\\DONE1.TXT` (47 bytes, the Shell\'s marker `AUTORUN M1RUN-ACKPANIC-M-RUNG9 STEP 1 STARTED`)',
                       'not re-checked in this section: it is a host check of the record\'s tree, made when the record was written (NOT RUN here) | NOT RUN |',
                       'M1\'s own verdict (5a) is the coordinator\'s: M1 PASSED again, by the rows a, c, d and e of the page and row f of the stick',
                       'Rung 9\'s class is **RUNG 9 PANIC TEXT**',
                       'The approval is spent: the guest ran, so the wrapper\'s call, `PAYLOAD CALL 0`, was served (INFERRED: it is on no film)',
                       'The whole carry chain (the runner with the big-file gate on, and the root-carry pair of the wrapper and the monitor) therefore ran on the unit in this boot (OBSERVED once;',
                       'THE FIRST USER-MODE (EL0) CODE EVER RUN ON THE SURFACE PRO 11 UNDER THE MONITOR',
                       'The six `control` defines of the A16 monitor, which the record named as serving that path, were not needed to get here (the coordinator\'s reading; INFERRED, not re-read here).',
                       'so the question of rung 8, whether the bound PMU clock makes the kernel publish its clock, is answered by this boot (the film does not show the driver\'s lines)',
                       'that the Preboot and Update volumes come from the ISC container is INFERRED (strongly); whether an ISC with the roles and no content satisfies it, and what the next boot tasks of `launchd` need, are UNKNOWN',
                       'Whether `wdt=-1` keeps the guest\'s WD1 counter unarmed: the run ended before the counter\'s wrap at 178.96 s.',
                       'The 300 s box was not needed and the 360 s CPU-boot wait never fired',
                       'Nothing is authorised by this section, and no next step is chosen here',
                       'Hardware: boot 28 only; every other statement of this section is a reading of its films or of the sources on the host.'):
            if phrase not in flat:
                problems.append(f'section 26 lost {phrase[:70]!r}')
        if re.search(r'[0-9A-Fa-f]{32,}|[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}|e42f451d|d9d79c87|64d87443|67d814f6|749361cf', body):
            problems.append('a digest, an identifier or an approval prefix in section 26')
        for context in re.findall(r'VERIFIED.{0,70}', flat):
            if not re.search(r'host|read|source|arithmetic|chain|code|identity|range|coordinator|check', context):
                problems.append(f'a VERIFIED in section 26 that is not a host fact: {context!r}')
        if re.search(r'desktop|userspace boots|is stable|stability is', flat, re.I) or len(re.findall(r'macOS boots', flat)) != 1 or 'and not that macOS boots' not in flat:
            problems.append('section 26 claims more than a failed first boot task of `launchd`')
        for row in body.split('### 26.6', 1)[1].split('### 26.7', 1)[0].splitlines():
            if row.startswith('| ') and not row.startswith(('| ---', '| Result, stage or row')) and not re.search(r'OBSERVED|VERIFIED|INFERRED|UNKNOWN|NOT RUN', row.rsplit('|', 2)[-2]):
                problems.append(f'a row of 26.6 has no label: {row[:50]!r}')
        for row in body.split('### 26.2', 1)[1].split('### 26.3', 1)[0].splitlines():
            if row.startswith('| ') and not row.startswith(('| ---', '| What the record listed')) and not re.search(r'OBSERVED|INFERRED|UNKNOWN', row.rsplit('|', 2)[-2]):
                problems.append(f'a row of 26.2 has no label: {row[:50]!r}')
        whole = ' '.join(text.split())
        for place, needle in (('the top paragraph', 'Boot 28 (2026-11-20, section 26) ran rung 9, the first root-mount form (rung 8, the record for the clock alone, was skipped by the operator and never run): the root-carry pair carried a 1.93 GB file'),
                              ('the top paragraph', 'the record\'s class for rung 9 is RUNG 9 PANIC TEXT, and M1 PASSED.'),
                              ('the top paragraph', 'no host analysis of boot 27\'s new lines or of boot 28 has been done'),
                              ('item 22 of section 1', '22. **Boot 28 (2026-11-20; section 26; on two films that miss the middle of the run; OBSERVED, one unit, one boot): M1 rung 9, the first root-mount form'),
                              ('item 22 of section 1', 'No next step is authorised. Hardware: boot 28 only.'),
                              ('item 22 of section 1', 'whether `wdt=-1` keeps the guest\'s WD1 unarmed was not tested, the run ending at 40.08 s'),
                              ('the evidence summary', 'rung 9\'s class is RUNG 9 PANIC TEXT, M1 PASSED (the coordinator\'s reading) | 26 |'),
                              ('section 25.7', 'Boot 28 (section 26) ran after this section was written: with rung 8 skipped, the guest carried a root of 1.86 GB, the kernel mounted it and ran `launchd`, and `launchd`\'s first boot task failed.')):
            if needle not in whole:
                problems.append(f'{place} does not count on boot 28')
        return problems

    def test_section_26_keeps_the_facts_of_boot_28(self):
        """Section 26 records boot 28 (M1 rung 9): the carry's numbers and the page's values keep their labels, the class is RUNG 9 PANIC TEXT, rung 8 was skipped, `launchd` is the first user-mode code and its first boot task failed,
        the ISC is INFERRED and the middle of the run is on no film, WD1 and the flush time stay UNKNOWN, no record digest is printed; each seeded fault must be found."""
        self.assertEqual(self.section_26_problems(self.text), [])
        body_seeds = (('Boot 28 is the eighth run of the SP11 monitor entry and the first with a root', 'Boot 28 is the seventh run of the SP11 monitor entry and the first with a root'),
                      ('RUNG 8 WAS SKIPPED by the operator and never run', 'RUNG 8 RAN before it, as boot 27 and a half'),
                      ('stays committed at `4895ea2`', 'was approved at `4895ea2`'),
                      ('this log repeats neither the digest nor the wording', 'the digest is 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef'),
                      ('GUEST.DAT is 1,926,621,440 bytes in 339 chunks (291 of them the root, 143 of those ZERO)', 'GUEST.DAT is 118,885,568 bytes in 46 chunks (291 of them the root, 143 of those ZERO)'),
                      ('it names ten results for rung 9', 'it names six results for rung 9'),
                      ('THE MIDDLE IS ON NO FILM', 'THE MIDDLE IS ON FILM 1'),
                      ('the record\'s INFERRED reading of them is now OBSERVED', 'the record\'s INFERRED reading of them is now VERIFIED'),
                      ('the read took about 46 s, about 40 MiB/s', 'the read took about 75 s, about 24 MiB/s'),
                      ('the hash took about 8 s, a rate of the order of 200 MiB/s', 'the hash took about 80 s, a rate of the order of 20 MiB/s'),
                      ('`GUEST.DAT BASE=0XF8D2A1000`, 62.2 GiB', '`GUEST.DAT BASE=0XC5D04000`, 3.1 GiB'),
                      ('and 21.6 GiB above the guest\'s placement end `0XA28000000` (arithmetic)', 'and 19.5 GiB above the guest\'s placement end `0XA28000000` (arithmetic)'),
                      ('one: no `NOT_IN_ONE_DESCRIPTOR` and no `PAYLOAD REFUSED` line, and the guest ran', 'several: `NOT_IN_ONE_DESCRIPTOR` came, and the guest ran'),
                      ('it does: the file was read into a buffer at `0XF8D2A1000` and both digests agree', 'it does not: the file was read below 4 GiB and the digests differ'),
                      ('no restart and no reset in the carry of about two minutes', 'a restart in the carry of about two minutes'),
                      ('the copy was 26.1 s at 19.2 MHz and the placement 2.1 ms', 'the copy was 2.6 s at 19.2 MHz and the placement 2.1 ms'),
                      ('73.8 MB/s, 70.4 MiB/s', '73.8 MiB/s, 70.4 MB/s'),
                      ('NOT TESTED: the run ended at 40.08 s, before the counter\'s wrap at 178.96 s (arithmetic on 24 MHz), and no `MON STOP DEVICE_RESET` came', 'held: the run ended at 400 s, after the counter\'s wrap at 178.96 s, and no `MON STOP DEVICE_RESET` came'),
                      ('stayed unchanged to its end at 95 s, 38 s or more', 'went dark at 95 s, 38 s or more'),
                      ('`mount_by_role:451: failed to find volume with role = 10 in APFS container', '`mount_by_role:451: found the volume with role = 10 in APFS container'),
                      ('then ten rounds of `wait_for_node:266: APFS IOKit node lookup timed out...`', 'then two rounds of `wait_for_node:266: APFS IOKit node lookup timed out...`'),
                      ('0x10 is Preboot and 0xC0 is Update (INFERRED from the numbers and the A16 notes)', '0x10 is Preboot and 0xC0 is Update (VERIFIED from the numbers)'),
                      ('waits for an APFS IOKit node that does not exist (INFERRED, strongly:', 'waits for an APFS IOKit node that does not exist (VERIFIED, strongly:'),
                      ('userspace panic: boot task failure: mount-phase-1 - exited due to SIGABRT`', 'userspace panic: boot task failure: mount-phase-2 - exited due to SIGABRT`'),
                      ('`Nested panic count exceeds limit 5, machine will reset or spin` and `wdog: panic chipreset`', '`Nested panic count exceeds limit 5, machine will reset or spin`'),
                      ('plus 4.3 s (arithmetic): the kernel read the PMU clock that the model serves', 'plus 43 s (arithmetic): the kernel read the PMU clock that the model serves'),
                      ('how that fits boot 27\'s clock driver start at t0 + 7 s is UNKNOWN', 'how that fits boot 27\'s clock driver start at t0 + 7 s is VERIFIED'),
                      ('13.4 % of the box of 300 s (5,760,000,000 ticks)', '13.4 % of the box of 120 s (5,760,000,000 ticks)'),
                      ('THE PC IS BOOT 24\'S, the same address in the same kernelcache (OBSERVED)', 'THE PC IS BOOT 27\'S, the same address in the same kernelcache (OBSERVED)'),
                      ('is boot 24\'s syndrome again', 'is boot 26\'s syndrome again'),
                      ('boot 24\'s was `0X3082BC224`, and the same address is INFERRED', 'boot 24\'s was `0X3082BC224`, and the same address is VERIFIED'),
                      ('boot 23\'s and boot 24\'s value (arithmetic on the bits)', 'boot 26\'s value (arithmetic on the bits)'),
                      ('as boot 24\'s (INFERRED;', 'as boot 24\'s (VERIFIED;'),
                      ('about 362 s after the call (INFERRED; the word is 360)', 'about 62 s after the call (INFERRED; the word is 360)'),
                      ('This is the first class of the record\'s order that fits', 'This is the last class of the record\'s order that fits'),
                      ('**RUNG 9 PANIC TEXT**', '**RUNG 9 MOUNTED**'),
                      ('neither is on film 2; the first panic read is the userspace panic;', 'both are on film 2; the first panic read is the ApplePMGR assertion;'),
                      ('did NOT hold: the run ended by the guest\'s own panic at 40.08 s', 'held: the run ended by the monitor\'s watchdog at 300 s'),
                      ('held: `TICKS=769573015` is 13.4 % of the box (arithmetic)', 'did NOT hold: `TICKS=769573015` is 113.4 % of the box (arithmetic)'),
                      ('(47 bytes, the Shell\'s marker `AUTORUN M1RUN-ACKPANIC-M-RUNG9 STEP 1 STARTED`)', '(45 bytes, the Shell\'s marker `AUTORUN M1RUN-ACKPANIC-Q-RUNG7 STEP 1 STARTED`)'),
                      ('(NOT RUN here) | NOT RUN |', '(IDENTICAL) | VERIFIED (host, git diff) |'),
                      ('Rung 9\'s class is **RUNG 9 PANIC TEXT**', 'Rung 9\'s class is **RUNG 9 NEW STOP**'),
                      ('so the wrapper\'s call, `PAYLOAD CALL 0`, was served (INFERRED: it is on no film)', 'so the wrapper\'s call, `PAYLOAD CALL 0`, was served (VERIFIED: it is on film 1)'),
                      ('therefore ran on the unit in this boot (OBSERVED once;', 'therefore ran on the unit in this boot (VERIFIED on the unit;'),
                      ('THE FIRST USER-MODE (EL0) CODE EVER RUN ON THE SURFACE PRO 11 UNDER THE MONITOR', 'THE SECOND USER-MODE (EL0) CODE EVER RUN ON THE SURFACE PRO 11 UNDER THE MONITOR'),
                      ('(the coordinator\'s reading; INFERRED, not re-read here)', '(the coordinator\'s reading; VERIFIED, re-read here)'),
                      ('is answered by this boot (the film does not show the driver\'s lines)', 'is answered by this boot (the film shows the driver\'s lines)'),
                      ('that the Preboot and Update volumes come from the ISC container is INFERRED (strongly)', 'that the Preboot and Update volumes come from the ISC container is OBSERVED'),
                      ('Whether `wdt=-1` keeps the guest\'s WD1 counter unarmed: the run ended before the counter\'s wrap', 'Whether `wdt=-1` keeps the guest\'s WD1 counter unarmed: it does, the run ended after the counter\'s wrap'),
                      ('and not that macOS boots', 'and macOS boots'),
                      ('The 300 s box was not needed and the 360 s CPU-boot wait never fired', 'The 300 s box was needed and the 360 s CPU-boot wait fired'),
                      ('Nothing is authorised by this section, and no next step is chosen here', 'The next step is authorised by this section and chosen here'),
                      ('Hardware: boot 28 only; every other statement', 'Hardware: boots 27 and 28; every other statement'),
                      ('### 26.7 What', 'Digest 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\n\n### 26.7 What'),
                      ('MON STOP S2_FAULT PC=0XFFFFFE0008BA7358 A=0X3082BC...', 'MON STOP WATCHDOG PC=0XFFFFFE0008BA7358 A=0X3082BC...'),
                      ('MON COUNTS EVENTS=512 TRAPS=648755 ABORTS=0', 'MON COUNTS EVENTS=512 TRAPS=195787 ABORTS=0'),
                      ('GUEST.DAT BASE=0XF8D2A1000 BYTES=1926621440\n', 'GUEST.DAT BASE=0XF8D2A1000 BYTES=118885568\n'),
                      ('MON TIM IRQS=6525 ASSERTS=12376 CLEARS=12375 ...', 'MON TIM IRQS=1560 ASSERTS=3134 CLEARS=3134 ...'))
        place_seeds = (('Boot 28 (2026-11-20, section 26) ran rung 9, the first root-mount form (rung 8, the record for the clock alone, was skipped by the operator and never run)', 'Boot 28 (2026-11-20, section 26) ran rung 8, the clock alone (rung 9, the first root-mount form, was skipped by the operator and never run)'),
                       ('the record\'s class for rung 9 is RUNG 9 PANIC TEXT, and M1 PASSED.', 'the record\'s class for rung 9 is RUNG 9 MOUNTED, and M1 PASSED.'),
                       ('22. **Boot 28 (2026-11-20; section 26; on two films that miss the middle of the run; OBSERVED, one unit, one boot)', '22. **Boot 28 (2026-11-20; section 26; on two films that miss the middle of the run; VERIFIED, one unit, one boot)'),
                       ('No next step is authorised. Hardware: boot 28 only.', 'A next step is authorised.'),
                       ('whether `wdt=-1` keeps the guest\'s WD1 unarmed was not tested, the run ending at 40.08 s', 'whether `wdt=-1` keeps the guest\'s WD1 unarmed was tested, the run ending at 400 s'),
                       ('rung 9\'s class is RUNG 9 PANIC TEXT, M1 PASSED (the coordinator\'s reading) | 26 |', 'rung 9\'s class is RUNG 9 NEW STOP, M1 PASSED (the coordinator\'s reading) | 26 |'),
                       ('Boot 28 (section 26) ran after this section was written: with rung 8 skipped,', 'Boot 28 (section 26) did not run: with rung 8 skipped,'),
                       ('no host analysis of boot 27\'s new lines or of boot 28 has been done', 'a host analysis of boot 28 has been done'))
        marker = '## 26. Result of boot 28'
        head, rest = self.text.split(marker, 1)
        limit = rest.index('## Appendix A.')
        for old, new in body_seeds:
            self.assertIn(old, rest[:limit], f'the seed {old!r} no longer matches the text: fix the seed, not the check')
            self.assertTrue(self.section_26_problems(head + marker + rest.replace(old, new, 1)), f'a seeded fault went unseen: {old!r} -> {new!r}')
        for old, new in place_seeds:
            self.assertIn(old, head, f'the seed {old!r} no longer matches the text: fix the seed, not the check')
            self.assertTrue(self.section_26_problems(head.replace(old, new, 1) + marker + rest), f'a seeded fault went unseen: {old!r} -> {new!r}')

    @staticmethod
    def kernel_stops_problems(note):
        """What is wrong with docs/SP11-KERNEL-STOPS.md, the reference for the host analyses of the stops of boots 20, 21, 23, 24, 25 and 26 and the reading of boot 27's wait (a note that is not a hardware result); an empty list is a pass."""
        problems = []
        if not note.startswith('<!-- SPDX-License-Identifier: MIT -->\n'):
            problems.append('the first line is not the MIT SPDX line')
        if len(note.encode()) >= 60 * 1024 or not note.isascii():      # 60 KiB: raised on purpose from 50 KiB for section 7C and rows 24 to 30 of section 8 (the note is about 58 KiB); nothing was cut
            problems.append('the note is 60 KiB or more, or not ASCII')
        if re.search(r'[0-9A-Fa-f]{32,}|[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}', note) or any(prefix in note for prefix in ('/' + 'Users' + '/', '/' + 'Volumes' + '/', '/' + 'home' + '/')):
            problems.append('a digest, an identifier or a machine path')
        headings = re.findall(r'^## (\d+[A-Z]?)\. ', note, re.M)
        if headings != [str(n) for n in range(1, 8)] + ['7A', '7B', '7C'] + [str(n) for n in range(8, 12)]:
            problems.append(f'section numbers are {headings}')
        flat = ' '.join(note.split())
        for phrase in ('the monitor entry ran eight times on the unit, in boots 20, 21, 23, 24, 25, 26, 27 and 28',
                       '| 27 | rung 7: as rung 6 with the `quiet` device tree (43 nodes withdrawn by data) and `cpu_boot_timeout_secs=120`, 120 s box | `MON STOP WATCHDOG` at 120.185 s, `PC=0XFFFFFE000B8716A8`, `A=0X96000047`;',
                       'Boot 27, the second to last row, is not a seventh stop at a device',
                       'Boot 28, the last row, is no stop at a device either: the guest panicked in userspace after `launchd`\'s first boot task failed, and the monitor\'s own end is the chip-reset stop of boot 24 (section 7C)',
                       '| 28 | rung 9: the root-carry pair, a 6,144 MiB guest with the 1.86 GB RAM-disk root, the `quiet-rtc-acm` tree (clock bound, `acm_fastsim=1`), `cpu_boot_timeout_secs=360`, 300 s box | `MON STOP S2_FAULT` at 40.08 s, `PC=0XFFFFFE0008BA7358` (boot 24\'s), `A=0X3082BC...`, a 32-bit read,',
                       '## 7C. Boot 28: the first userspace boot, a failed boot task of `launchd`',
                       '**A userspace panic, then boot 24\'s stop (OBSERVED text; the chain INFERRED).**',
                       'this note\'s author did not re-read the kernelcache, and no host analysis of boot 28 was done',
                       'that `launchd` wants the ISC\'s Preboot volume is INFERRED, strongly',
                       'That the clock binding is what removed them is INFERRED, not separated: rung 8, which would have tested the binding alone, was skipped',
                       'Rows 18 to 23 were tested by boot 27 and rows 24 to 30 by boot 28',
                       'held in part: `launchd`\'s calendar date is the seed plus 4.3 s, so the clock took and the run reached `launchd` within 40.08 s',
                       'did NOT hold: the guest panicked in userspace and the run ended at 40.08 s with the monitor\'s chip-reset stop',
                       'NOT TESTED: the run ended at 40.08 s, before the wrap; no `MON STOP DEVICE_RESET`',
                       'NOT TESTED: the run ended at 40.08 s; no panic text of that kind',
                       'the monitor\'s own watchdog ended the run at the box\'s end (section 7B)',
                       '## 7B. Boot 27: the kernel\'s first wait, for a root device',
                       '**Not a stop at a device (OBSERVED).** No `S2_FAULT` of any address came in 120.185 s',
                       'The monitor ended the run because its box ended, as in boot 25.',
                       'The earlier sentence that the RTC messages cannot be on the film holds for those two messages only.',
                       'no host analysis of boot 27 was done',
                       'held, 2 s early: the burst ends in `Waiting on <dict ...>`',
                       'NOT TESTED: the box ended first, as designed; no panic text inside it',
                       'held: none is on the film (the clock driver\'s start lines are: section 7B)',
                       'The correction is about those two messages only: the clock driver\'s own start lines are on the film (OBSERVED in boots 26 and 27)',
                       'This note records them with their labels and does not re-derive them: its author did not re-read the kernelcache, so each VERIFIED is the analysis\'s own.',
                       '| 26 | rung 6: as rung 5 with `cpu_boot_timeout_secs=90`, 120 s box, timer words printed | `MON STOP S2_FAULT` at 61.60 s, `PC=0XFFFFFE00095FF9AC`, `A=0X291000004`, a 32-bit write of 1 |',
                       '| 25 | rung 5: six CPUs, CPU-start stores acknowledged, `let_panic`, 60 s box | `MON STOP WATCHDOG` at 60.18 s, `PC=0XFFFFFE000B8716A8` |',
                       '| 24 | rung 4: `cpus=1` and `let_panic` | `MON STOP S2_FAULT` at 2.2465 s, `A=0X3082BC224`, a 32-bit read, after a panic text |',
                       'The assertion is driven by XNU\'s topology, not by a hardware read.',
                       'The nested panics are XNU\'s own behaviour for this device tree, not a monitor artefact (VERIFIED chain; the message INFERRED).',
                       'The first reading of the idle did not survive (INFERRED).',
                       'Whether any guest FIQ was delivered in boot 25 is UNKNOWN',
                       'did NOT occur: the run lasted the whole box with no stage-2 fault',
                       'not supported by the code (section 7); not refuted by a run either',
                       'With 120 it fires at about 122 s, which is outside any box the wrapper takes (its cap is 120 s)',
                       '`Still waiting for root device` is not in this kernelcache (VERIFIED absence;',
                       '**one pulse, not a loop**',
                       'The analysis after boot 25, the first version of section 9 below and a later design note expected them on the film; that was wrong and is fixed here.',
                       'did NOT occur, and could not: they are `kprintf` lines that go to the serial output and not to the video console',
                       'held: `VECTORS=969`, `IRQS=1010`, `ASSERTS=CLEARS=2036` and `CTL=0X1`',
                       'NOT TESTED: the run ended at 61.60 s on the scaler stop, 30 s before that time',
                       'Section numbers that other documents cite stay fixed, so a later stop takes the next letter after section 7',
                       'A label is never raised: a host read is VERIFIED only as "read on the host", never as "ran on the unit".'):
            if phrase not in flat:
                problems.append(f'the note lost {phrase[:70]!r}')
        if 'VERIFIED here means read on the host' not in flat or flat.count('ran on the unit') != 2:
            problems.append('VERIFIED is not defined as a host read, or "ran on the unit" is used outside the definition and the closing rule')
        if re.search(r'desktop|macOS boots|is stable|stability is', flat, re.I):
            problems.append('the note claims more than a host analysis')
        rows = [line for line in note.split('## 8.', 1)[1].split('## 9.', 1)[0].splitlines() if line.startswith('| ') and not line.startswith(('| ---', '| # |'))]
        if len(rows) != 30:
            problems.append(f'section 8 has {len(rows)} rows, not 30')
        for target in re.findall(r'\]\(([^)#:]+\.md)\)', note):
            if not (ROOT / 'docs' / target).is_file():
                problems.append(f'the link {target} does not resolve')
        return problems

    def test_the_kernel_stops_note_keeps_its_labels_and_its_six_stops(self):
        """docs/SP11-KERNEL-STOPS.md is a reference for the host analyses of the stops of boots 20, 21, 23, 24, 25 and 26, the reading of boot 27's wait and the reading of boot 28's userspace failure: SPDX line, under 60 KiB, ASCII, its links resolve, the stops and the tested predictions keep their labels,
        the idle of boot 25 stays a reading, boot 26's silence and stop, boot 27's wait and boot 28's end stay readings, the two timeout messages of the clock waits stay off the film; each seeded fault below must be found."""
        note = (ROOT / 'docs' / 'SP11-KERNEL-STOPS.md').read_text(encoding='utf-8')
        self.assertEqual(self.kernel_stops_problems(note), [])
        seeds = (('the monitor entry ran eight times on the unit, in boots 20, 21, 23, 24, 25, 26, 27 and 28', 'the monitor entry ran four times on the unit, in boots 20, 21, 23 and 24'),
                 ('Boot 27, the second to last row, is not a seventh stop at a device', 'Boot 27, the second to last row, is a seventh stop at a device'),
                 ('Boot 28, the last row, is no stop at a device either: the guest panicked in userspace after `launchd`\'s first boot task failed', 'Boot 28, the last row, is a stop at a device: the guest panicked in userspace after `launchd`\'s first boot task failed'),
                 ('`MON STOP S2_FAULT` at 40.08 s, `PC=0XFFFFFE0008BA7358` (boot 24\'s), `A=0X3082BC...`, a 32-bit read,', '`MON STOP WATCHDOG` at 40.08 s, `PC=0XFFFFFE0008BA7358` (boot 24\'s), `A=0X3082BC...`, a 32-bit read,'),
                 ('**A userspace panic, then boot 24\'s stop (OBSERVED text; the chain INFERRED).**', '**A userspace panic, then boot 24\'s stop (VERIFIED text; the chain VERIFIED).**'),
                 ('and no host analysis of boot 28 was done', 'and a host analysis of boot 28 was done'),
                 ('that `launchd` wants the ISC\'s Preboot volume is INFERRED, strongly', 'that `launchd` wants the ISC\'s Preboot volume is VERIFIED'),
                 ('That the clock binding is what removed them is INFERRED, not separated:', 'That the clock binding is what removed them is VERIFIED, separated:'),
                 ('held in part: `launchd`\'s calendar date is the seed plus 4.3 s, so the clock took', 'held in part: `launchd`\'s calendar date is the seed plus 43 s, so the clock took'),
                 ('did NOT hold: the guest panicked in userspace and the run ended at 40.08 s', 'held: the guest ran to the end of the box and the run ended at 300 s'),
                 ('NOT TESTED: the run ended at 40.08 s, before the wrap; no `MON STOP DEVICE_RESET`', 'held: the run ended at 400 s, after the wrap; a `MON STOP DEVICE_RESET`'),
                 ('NOT TESTED: the run ended at 40.08 s; no panic text of that kind', 'held: the panic text of that kind came at 362 s'),
                 ('the monitor\'s own watchdog ended the run at the box\'s end (section 7B)', 'the monitor\'s own fault ended the run before the box (section 7B)'),
                 ('`MON STOP WATCHDOG` at 120.185 s, `PC=0XFFFFFE000B8716A8`, `A=0X96000047`;', '`MON STOP S2_FAULT` at 120.185 s, `PC=0XFFFFFE000B8716A8`, `A=0X96000047`;'),
                 ('**Not a stop at a device (OBSERVED).** No `S2_FAULT` of any address came in 120.185 s', '**Not a stop at a device (OBSERVED).** An `S2_FAULT` came in 120.185 s'),
                 ('The monitor ended the run because its box ended, as in boot 25.', 'The monitor ended the run because the kernel panicked, as in boot 24.'),
                 ('The earlier sentence that the RTC messages cannot be on the film holds for those two messages only.', 'The earlier sentence that the RTC messages cannot be on the film holds for every RTC message.'),
                 ('and no host analysis of boot 27 was done', 'and a host analysis of boot 27 was done'),
                 ('held, 2 s early: the burst ends in `Waiting on <dict ...>`', 'held, 20 s early: the burst ends in `Waiting on <dict ...>`'),
                 ('NOT TESTED: the box ended first, as designed; no panic text inside it', 'held: the panic fell at about 122 s; no panic text inside it'),
                 ('held: none is on the film (the clock driver\'s start lines are: section 7B)', 'did NOT hold: all are on the film (the clock driver\'s start lines are: section 7B)'),
                 ('The correction is about those two messages only: the clock driver\'s own start lines are on the film (OBSERVED in boots 26 and 27)', 'The correction is about every RTC message: the clock driver\'s own start lines are not on the film (OBSERVED in boots 26 and 27)'),
                 ('its author did not re-read the kernelcache, so each VERIFIED is the analysis\'s own.', 'its author re-read the kernelcache, so each VERIFIED is a run.'),
                 ('`MON STOP S2_FAULT` at 61.60 s, `PC=0XFFFFFE00095FF9AC`, `A=0X291000004`, a 32-bit write of 1 |', '`MON STOP WATCHDOG` at 61.60 s, `PC=0XFFFFFE00095FF9AC`, `A=0X291000004`, a 32-bit write of 1 |'),
                 ('`MON STOP WATCHDOG` at 60.18 s, `PC=0XFFFFFE000B8716A8` |', '`MON STOP WATCHDOG` at 5.04 s, `PC=0XFFFFFE000B8716A8` |'),
                 ('a 32-bit read, after a panic text |', 'a 32-bit write, after a panic text |'),
                 ('The assertion is driven by XNU\'s topology, not by a hardware read.', 'The assertion is driven by a hardware read, not by XNU\'s topology.'),
                 ('not a monitor artefact (VERIFIED chain; the message INFERRED).', 'a monitor artefact (VERIFIED chain; the message INFERRED).'),
                 ('The first reading of the idle did not survive (INFERRED).', 'The first reading of the idle is confirmed (VERIFIED).'),
                 ('Whether any guest FIQ was delivered in boot 25 is UNKNOWN', 'Whether any guest FIQ was delivered in boot 25 is OBSERVED'),
                 ('did NOT occur: the run lasted the whole box with no stage-2 fault', 'occurred: the run ended within seconds at a stage-2 fault'),
                 ('not supported by the code (section 7); not refuted by a run either', 'supported by the code (section 7); refuted by a run'),
                 ('With 120 it fires at about 122 s, which is outside any box', 'With 120 it fires at about 62 s, which is inside any box'),
                 ('**one pulse, not a loop**', '**a loop**'),
                 ('that was wrong and is fixed here.', 'that was right and is kept here.'),
                 ('did NOT occur, and could not: they are `kprintf` lines that go to the serial output and not to the video console', 'occurred: they are `kprintf` lines that go to the video console'),
                 ('held: `VECTORS=969`, `IRQS=1010`, `ASSERTS=CLEARS=2036` and `CTL=0X1`', 'held: `VECTORS=0`, `IRQS=1010`, `ASSERTS=CLEARS=2036` and `CTL=0X1`'),
                 ('NOT TESTED: the run ended at 61.60 s on the scaler stop, 30 s before that time', 'held: the run ended at 61.60 s on the scaler stop, 30 s before that time'),
                 ('Section numbers that other documents cite stay fixed, so a later stop takes the next letter after section 7', 'Section numbers that other documents cite move, so a later stop renumbers the sections after section 7'),
                 ('A label is never raised: a host read is VERIFIED only as "read on the host", never as "ran on the unit".', 'A label is raised when a host read is VERIFIED as "ran on the unit".'),
                 ('<!-- SPDX-License-Identifier: MIT -->\n', ''),
                 ('## 7A. Boot 26:', '## 7B. Boot 26:'),
                 ('## 11. Adding a stop to this note', '## 12. Adding a stop to this note'),
                 ('(INFERRED; nothing built, nothing approved)', '(INFERRED; the desktop is built and approved)'))
        for old, new in seeds:
            pattern = r'\s+'.join(re.escape(word) for word in old.split())
            self.assertEqual(len(re.findall(pattern, note)), 1, f'the seed {old!r} must hit exactly one place: fix the seed, not the check')
            self.assertTrue(self.kernel_stops_problems(re.sub(pattern, lambda _: new, note, count=1)), f'a seeded fault went unseen: {old!r} -> {new!r}')

    @staticmethod
    def status_problems(status, surface):
        """What is wrong with the status text of boots 20 and 21 in STATUS.md and SURFACE-PRO-11.md (whitespace-collapsed texts); an empty list is a pass."""
        problems = []
        for where, text, needles in (
                ('STATUS.md', status, (
                    'SP11 monitor entry: ran eight times. Boot 20: the stock kernel executed on one vCPU for 5 s and printed its banner, then spun in an early console loop (INFERRED). '
                    'Boot 21, with the panel as its console: it ran on one vCPU for 2.04 s through the I/O Kit start-up and the platform-driver matching and stopped at a stage-2 fault at what is INFERRED, by address, to be its first CPU-start write. '
                    'Boot 23, with `cpus=1` added to the guest\'s command line: the kernel honoured it (one processor line) and ran on one vCPU for 2.17 s, further than boot 21, without the CPU-start stop, and the monitor stopped it at an exception it took at EL1 '
                    '(`MON STOP GUEST_EXC`), INFERRED to be the kernel\'s own debugger trap (the cause is UNKNOWN); the rung 3 record\'s own verdict is NOT PASSED (c) by its letter, M1\'s PASSED. '
                    'Boot 24, with the monitor\'s `let_panic` word set: the kernel\'s own handler ran and printed a panic text, an ApplePMGR assertion (`enableCPUComplex`), then nested panics, and the monitor stopped the run at a stage-2 fault (`MON STOP S2_FAULT`) 2.25 s into the call; '
                    'the rung 4 record\'s class is PANIC TEXT, M1\'s PASSED (why the assertion failed is UNKNOWN). '
                    'Boot 25, with the CPU-start stores acknowledged and dropped, six CPUs and a box of 60 s: no assertion and no panic text; the kernel ran the whole box and was idle in a wait-for-interrupt when the monitor\'s own watchdog stopped it '
                    '(`MON STOP WATCHDOG`, 60.18 s); the rung 5 record\'s class is WATCHDOG, NO TEXT, with PROGRESS, M1\'s PASSED (why the kernel was idle is UNKNOWN; the host analyses of the stops: [SP11-KERNEL-STOPS.md](SP11-KERNEL-STOPS.md)). '
                    'Boot 26, with the CPU-start wait of the guest set to 90 s and a box of 120 s: the timer counters showed guest timer interrupts delivered (`MON TIM` `VECTORS=969`: the counter is OBSERVED, the delivery INFERRED from it), the kernel ran 61.60 s into the start of its BSD layer '
                    'and the monitor stopped it at a stage-2 fault in the scaler\'s registers (`MON STOP S2_FAULT`, `A=0X291000004`); the rung 6 record\'s class is NEW STOP, M1\'s PASSED '
                    '(why the real-time clock driver\'s start stops and what follows the scaler are UNKNOWN; the host analyses of the stops: [SP11-KERNEL-STOPS.md](SP11-KERNEL-STOPS.md)). '
                    'Boot 27, with the `quiet` device tree (43 nodes withdrawn by data) and a 120 s CPU-start wait: the scaler\'s stop did not return, the kernel ran the whole box of 120 s through `bsd_init` to a wait for a root device '
                    '(INFERRED from its last line, `Waiting on <dict ...>`) and the monitor\'s own watchdog stopped it (`MON STOP WATCHDOG`, 120.185 s); no panic text; the rung 7 record\'s class is RUNG 7 KERNEL WAITING, M1\'s PASSED '
                    '(a root device, the CPU-boot panic at about 122 s and why the PMU\'s reads fail the parity check are UNKNOWN). '
                    'Boot 28, with the root-carry pair, a guest of 6,144 MiB that carries a 1.86 GB RAM-disk root, the `quiet-rtc-acm` device tree and a box of 300 s: the carry of the 1.93 GB file worked (the stick read at about 40 MiB/s, about 46 s; '
                    'the firmware placed the file at `0XF8D2A1000` in one descriptor; a USB read above 4 GiB works; the copy took 26.1 s; no restart, refusal or firmware-watchdog reset), and the stock kernel mounted the root and ran `launchd`, `dyld` '
                    'and the ignition library at EL0 for about 36 s, the first user-mode code under the monitor; the calendar date in `launchd`\'s log is the clock model\'s seed plus 4 s, so the clock took; `launchd`\'s first boot task, `mount-phase-1`, '
                    'found no volume of role 0x10 or 0xC0, waited ten times for an APFS node and failed (INFERRED: the ISC\'s Preboot and Update volumes are not there), the guest panicked in userspace '
                    '(`userspace panic: boot task failure: mount-phase-1 - exited due to SIGABRT`) and the monitor stopped the run at boot 24\'s stop, INFERRED to be the kernel\'s chip-reset read (`MON STOP S2_FAULT`, 40.08 s); '
                    'the rung 9 record\'s class is RUNG 9 PANIC TEXT, M1\'s PASSED (what `launchd`\'s next boot tasks need, whether an ISC with no content satisfies `mount-phase-1`, the kernel\'s log from the call to `launchd` '
                    'and whether `wdt=-1` keeps the guest\'s WD1 unarmed are UNKNOWN). '
                    'Hardware status per gate X4: kernel executes: PARTIAL (one vCPU, 40.08 s, to launchd\'s failed first boot task).',
                    'the gate passed with it on the unit in boot 20 (OBSERVED, one boot; the SP11 monitor entry row below) and again in boot 21 (OBSERVED, one boot) and in boots 23, 24, 25, 26, 27 and 28 (OBSERVED, one boot each)',
                    'the monitor\'s own guest regime ran eight times, in boots 20, 21, 23, 24, 25, 26, 27 and 28, with the guards passed (`STATUS=0X0`; the SP11 monitor entry row below)',
                    'the GUEST.DAT files it built ran on the unit, in boots 20, 21, 23, 24, 25, 26, 27 and 28 (the SP11 monitor entry row below; boot 28\'s is the 1.93 GB root-carry file), hardware NOT RUN beyond the dry run and those runs.',
                    'The guest of boot 20 was, INFERRED (high), stuck in a loop of the kernel\'s I/O Kit start-up that a withheld panel makes endless', 'hardware NOT RUN beyond those eight runs',
                    'the monitor entry\'s eight runs in the rows below', '| SP11 monitor entry (work item 4b; M1 rungs 1 to 7 and 9) |', '(the opt-in monitor entry ran eight times: the row below)',
                    'Host tests, and eight runs on the unit on 2026-11-18 to 20 (boot 20, plan `M1RUN-RUNG1`, rung 1; boot 21, plan `M1RUN-CONSOLE-RUNG2`, rung 2; boot 23, plan `M1RUN-CPUS1-RUNG3`, rung 3; boot 24, plan `M1RUN-LETPANIC-RUNG4`, rung 4; boot 25, plan `M1RUN-ACKPANIC-RUNG5`, rung 5; boot 26, plan `M1RUN-ACKPANIC-RUNG6`, rung 6; boot 27, plan `M1RUN-ACKPANIC-Q-RUNG7`, rung 7; and boot 28, plan `M1RUN-ACKPANIC-M-RUNG9`, rung 9, the first root-mount form; rung 8, the clock alone, was skipped and never run; OBSERVED, one unit, one boot each',
                    '`MON STOP S2_FAULT`, 43,133,334 ticks, in boot 24; `MON STOP WATCHDOG`, 1,155,520,917 ticks, in boot 25; `MON STOP S2_FAULT`, 1,182,753,011 ticks, in boot 26; `MON STOP WATCHDOG`, 2,307,553,890 ticks, in boot 27; `MON STOP S2_FAULT`, 769,573,015 ticks, in boot 28), returned', 'in all eight boots (its regime is not recorded); `ABORTS=0` in all eight.',
                    '(in boot 24 the start-up ended in a kernel panic, in boot 25 in an idle wait-for-interrupt, in boot 26 at the scaler\'s registers, in boot 27 in the wait for a root device after bsd_init; boot 28 went on to userspace and ended in `launchd`\'s failed first boot task)',
                    'and the eighth run (2026-11-20, boot 28, M1 rung 9, the first root-mount form, rung 8 having been skipped: a 1.93 GB file was carried and the stock kernel mounted a 1.86 GB RAM-disk root and ran `launchd`, whose first boot task failed, '
                    'and the monitor stopped the run, INFERRED at the kernel\'s chip-reset read, after the guest\'s userspace panic, 40.08 s into the call; the same row)',
                    '| SP11 root carry (RAM-disk root as the guest\'s root, M1 rung 9; [SP11-STICK-ROOT.md](SP11-STICK-ROOT.md)) | EXPERIMENTAL; opt-in gates, off by default',
                    'Hardware NOT RUN beyond that one run |',
                    'and the seventh run (2026-11-20, boot 27, M1 rung 7, the `quiet` device tree with 43 nodes withdrawn by data and a 120 s CPU-start wait: the scaler\'s stop did not return and the kernel ran the whole box of 120 s through `bsd_init` to a wait for a root device, '
                    'INFERRED from its last line, when the monitor\'s own watchdog stopped it; the same row)',
                    'and the sixth run (2026-11-19, boot 26, M1 rung 6, the same monitor and guest with a 90 s CPU-start wait and a box of 120 s: the timer counters showed guest timer interrupts delivered (INFERRED), the kernel ran 61.60 s to the start of its BSD layer, '
                    'and the monitor stopped it at a stage-2 fault in the scaler\'s registers; the same row)',
                    'and the fifth run (2026-11-19, boot 25, M1 rung 5, the CPU-start stores acknowledged and dropped: the kernel ran the whole box of 60 s and was idle in a wait-for-interrupt when the monitor\'s own watchdog stopped it, 60.18 s into the call; the same row)',
                    'and the fourth run (2026-11-18, boot 24, M1 rung 4, the monitor\'s `let_panic` word set: the kernel\'s own handler ran and printed a panic text, an ApplePMGR assertion, then nested panics, '
                    'and the monitor stopped the run at a stage-2 fault 2.25 s into the call; the same row)',
                    'and the first run of the SP11 monitor entry (2026-11-18, boot 20, M1 rung 1:', 'and its second run (2026-11-18, boot 21, M1 rung 2:',
                    'and the third run of the SP11 monitor entry (2026-11-18, boot 23, M1 rung 3, `cpus=1`: the kernel honoured it and ran on one vCPU for 2.17 s, further than boot 21, '
                    'and the monitor stopped it at an exception it took at EL1, INFERRED to be the kernel\'s own debugger trap; the monitor entry row below)')),
                ('SURFACE-PRO-11.md', surface, (
                    'the first run of the SP11 monitor entry (2026-11-18, boot 20, M1 rung 1; E71,', '(gate X4: kernel executes: PARTIAL).', 'The second run (2026-11-18, boot 21, M1 rung 2; E72,',
                    'gate X4, kernel executes: PARTIAL (one vCPU, 5 s, early boot only)', 'it ran 5 s, then spun in an early console loop (INFERRED, high',
                    'gate X4, kernel executes: PARTIAL (one vCPU, 2.04 s, through IOKit platform matching to the first CPU-start)',
                    '| E71 | The first run of the SP11 monitor entry (work item 11, M1 rung 1, plan `M1RUN-RUNG1`, 2026-11-18, boot 20;',
                    '| E72 | The second run of the SP11 monitor entry (work item 11, M1 rung 2, plan `M1RUN-CONSOLE-RUNG2`, 2026-11-18, boot 21;',
                    'the stop decodes as a 32-bit write to an address with no stage-2 mapping, which matches the A16\'s first CPU-start write by address only',
                    '**Boot 20 (2026-11-18; OBSERVED on video, one unit, one boot; E71;', 'that the guest was stuck, not slow: the stop PC is the head of a five-instruction register-only loop in the kernel\'s I/O Kit start-up',
                    'So the next run hands the panel (rung 2) or adds `-noprogress`, and a longer box on the same image only repeats the loop.',
                    'Gate X4\'s "kernel executes" stands at PARTIAL (one vCPU, 5 s, early boot only; ran 5 s, then spun in an early console loop, INFERRED).',
                    '**Boot 21 (2026-11-18; OBSERVED on video, one unit, one boot; E72;', 'at what is INFERRED, by address, to be its first CPU-start write (gate X4',
                    'Gate X4\'s "kernel executes" stands at PARTIAL (one vCPU, 2.04 s, through IOKit platform matching to the first CPU-start).',
                    'The X5 exit names guest kernel text on the OLED: this boot is evidence for it (OBSERVED, one boot), and this record changes no gate row.',
                    'Nothing is shown about CPU bring-up, SMP, a root device, storage, userspace, stability or performance, nor that the SPMI and PMU errors are harmless beyond this run (UNKNOWN)',
                    'the monitor\'s own guest regime ran eight times, in boots 20, 21, 23, 24, 25, 26, 27 and 28 (E71, E72, E74, E75, E76, E77, E78, E79), and the conversion to 24 MHz is NOT RUN',
                    'The third run (2026-11-18, boot 23, M1 rung 3, `cpus=1`; E74,', 'The fourth run (2026-11-18, boot 24, M1 rung 4, the monitor\'s `let_panic` word set; E75,',
                    'The fifth run (2026-11-19, boot 25, M1 rung 5, the CPU-start stores acknowledged and dropped, six CPUs, a box of 60 s; E76,',
                    'The sixth run (2026-11-19, boot 26, M1 rung 6, the same monitor and guest with a 90 s CPU-start wait and a box of 120 s, and a reporter that prints the guest timer\'s words; E77,',
                    'The seventh run (2026-11-20, boot 27, M1 rung 7, the same monitor and reporter with the `quiet` device tree, 43 nodes withdrawn by data, and a 120 s CPU-start wait; E78,',
                    'the monitor entry ran eight times, in boots 20, 21, 23, 24, 25, 26, 27 and 28 (2026-11-18 to 20, M1 rungs 1 to 7 and 9; rung 8 was skipped and never run; OBSERVED, one boot each, E71, E72, E74, E75, E76, E77, E78 and E79): '
                    'gate X4, kernel executes: PARTIAL (one vCPU, 40.08 s, to launchd\'s failed first boot task).',
                    'In boot 28, with the root-carry pair, a guest of 6,144 MiB that carries a 1.86 GB RAM-disk root, the `quiet-rtc-acm` device tree and a box of 300 s, the carry worked and the stock kernel mounted the root and ran `launchd`, '
                    'the first user-mode code under the monitor;',
                    '| E79 | The eighth run of the SP11 monitor entry (work item 11, M1 rung 9, the first root-mount form, plan `M1RUN-ACKPANIC-M-RUNG9`, 2026-11-20, boot 28; rung 8, the record for the clock alone, was skipped by the operator and never run, '
                    'and stays committed at `4895ea2`;',
                    'the firmware placed it at `0XF8D2A1000` (62.2 GiB) in one descriptor, the wrapper copied it in 26.1 s (`TICKS HZ=19200000 PLACED=40419 COPIED=501263277`)',
                    'The monitor ended the run at boot 24\'s stop, INFERRED to be the kernel\'s chip-reset read, 40.08 s into the call (`MON STOP S2_FAULT`, `PC=0XFFFFFE0008BA7358`, boot 24\'s, `A=0X3082BC...`; 769,573,015 ticks for the whole call)',
                    'rung 9\'s class is RUNG 9 PANIC TEXT, M1 PASSED (the coordinator\'s reading); gate X4, kernel executes: PARTIAL (one vCPU, 40.08 s, to launchd\'s failed first boot task)',
                    '**Boot 28 (2026-11-20; OBSERVED on two videos that miss the middle of the run, one unit, one boot; E79;',
                    'Gate X4\'s "kernel executes" stays at PARTIAL (one vCPU, 40.08 s, to launchd\'s failed first boot task)',
                    'the PMU clock model works.',
                    'In boot 24, with the monitor\'s `let_panic` word set, the kernel\'s own handler ran and printed a panic text, an ApplePMGR assertion, then nested panics, and the monitor stopped the run at a stage-2 fault (`MON STOP S2_FAULT`, 2.25 s into the call), '
                    'with the registers and the GIC restored; the cause of the assertion is UNKNOWN.', '(WS-B, E70 to E72 and E74 to E79)',
                    'In boot 26, with a 90 s CPU-start wait and a box of 120 s, the timer counters showed guest timer interrupts delivered (`MON TIM` `VECTORS=969`: the counter is OBSERVED, one boot; the delivery INFERRED)',
                    'In boot 27, with the `quiet` device tree (43 nodes withdrawn by data) and a 120 s CPU-start wait, the scaler\'s stop did not return, the kernel ran the whole box of 120 s through `bsd_init` to a wait for a root device (INFERRED from its last line)',
                    '| E78 | The seventh run of the SP11 monitor entry (work item 11, M1 rung 7, plan `M1RUN-ACKPANIC-Q-RUNG7`, 2026-11-20, boot 27; the record was named by the operator by its SHA-256 for that boot, and its text is from the commit `5f2f8dd`):',
                    'ended the run itself at the box\'s end, 120.185 s into the call (`MON STOP WATCHDOG`, `PC=0XFFFFFE000B8716A8`, `A=0X96000047`; 2,307,553,890 ticks for the whole call)',
                    'the timer counters were `MON TIM IRQS=1560 ASSERTS=3134 CLEARS=3134 VECTORS=1519 CTL=0X1` and the acknowledge took 15 CPU-start stores (`MON ACK COUNT=15`); no panic text;',
                    'NOT RUN (symbolication, which needs the KDK; the A16 baseline comparison of row g on the committed tree): rung 7\'s class is RUNG 7 KERNEL WAITING, M1 PASSED (the coordinator\'s reading); gate X4, kernel executes: PARTIAL (one vCPU, 120.185 s, through bsd_init to a root-device wait)',
                    'row g: `git diff` shows the monitor sources of rungs 6 and 7 identical but for the builder-side data module `probes/guestprofile_m1.py`',
                    'with the registers and the GIC restored; there was no panic text.',
                    '**Boot 27 (2026-11-20; OBSERVED on video, one unit, one boot; E78;',
                    'What is now shown for gate X4 (one boot): the stock kernel runs through `bsd_init` under the monitor to its wait for a root device (INFERRED from its last line), with the guest\'s own timers (`MON TIM` `VECTORS=1519`: the counters are OBSERVED).',
                    'the two timeout messages of the clock waits cannot be on it (VERIFIED: they are `kprintf` lines)',
                    'Gate X4\'s "kernel executes" stays at PARTIAL (one vCPU, 120.185 s, through bsd_init to a root-device wait)',
                    '| E77 | The sixth run of the SP11 monitor entry (work item 11, M1 rung 6, plan `M1RUN-ACKPANIC-RUNG6`, 2026-11-19, boot 26; the record was named by the operator by its SHA-256 for that boot, and its text is from the commit `0f6174d`):',
                    'ended the run itself 61.60 s into the call (`MON STOP S2_FAULT`, `A=0X291000004`, a 32-bit write of 1 in the first register window of the device tree\'s `scaler0` node; 1,182,753,011 ticks for the whole call, the box being 120 s)',
                    'the timer counters were `MON TIM IRQS=1010 ASSERTS=2036 CLEARS=2036 VECTORS=969 CTL=0X1` (guest timer interrupts delivered: INFERRED from them) and the acknowledge took 15 CPU-start stores (`MON ACK COUNT=15`); no panic text;',
                    'NOT RUN (symbolication, which needs the KDK): rung 6\'s class is NEW STOP, M1 PASSED by its own rows; gate X4, kernel executes: PARTIAL (one vCPU, 61.60 s, into bsd_init)',
                    '**Boot 26 (2026-11-19; OBSERVED on video, one unit, one boot; E77;',
                    'What is now shown for gate X4 (one boot): the timer counters show the guest timer path working under the monitor (the counters are OBSERVED, the delivery INFERRED), and the stock kernel runs on into `bsd_init` (INFERRED from its log) under it.',
                    'The record\'s prediction of a timer panic at about 92 s was not tested: the run ended at 61.60 s.',
                    'Gate X4\'s "kernel executes" stays at PARTIAL (one vCPU, 61.60 s, into bsd_init)',
                    '| E76 | The fifth run of the SP11 monitor entry (work item 11, M1 rung 5, plan `M1RUN-ACKPANIC-RUNG5`, 2026-11-19, boot 25; the record was named by the operator by its SHA-256 for that boot, and its text is from the commit `ac9f634`):',
                    'the monitor ran the stock J700 26A428 kernel at EL1 on one vCPU (the guest\'s other five CPUs never started) and ended the run itself 60.18 s into the call (`MON STOP WATCHDOG`; 1,155,520,917 ticks for the whole call, the box being 60 s',
                    'no stop in the CPU-start block, no ApplePMGR assertion and no panic text; the kernel\'s log went far beyond boot 21\'s last line',
                    'the PC is the instruction after a wait-for-interrupt in the kernel\'s idle path, with every interrupt masked.',
                    'UNKNOWN (why the kernel was idle, whether any guest timer interrupt was delivered, how many CPU-start stores the acknowledge took, the NV2 record\'s regime; CPU bring-up, SMP, a root device, storage, userspace); '
                    'NOT RUN (symbolication, which needs the KDK): rung 5\'s class is WATCHDOG, NO TEXT, with PROGRESS, M1 PASSED by its own rows; gate X4, kernel executes: PARTIAL (one vCPU, 60.18 s, idle at the box\'s end)',
                    '**Boot 25 (2026-11-19; OBSERVED on video, one unit, one boot; E76;',
                    'No stop occurred in the CPU-start block, the ApplePMGR driver did not assert with six CPUs and no panic text was printed;',
                    'Why the kernel was idle is UNKNOWN: the reading of the film that the quarantined guest timers can never fire is not supported by the host reading of the monitor\'s code, and whether any guest timer interrupt was delivered is not on the page',
                    '| E75 | The fourth run of the SP11 monitor entry (work item 11, M1 rung 4, plan `M1RUN-LETPANIC-RUNG4`, 2026-11-18, boot 24; the record was named by the operator by its SHA-256 for that boot, and its text is from the commit `9485ab8`):',
                    'the kernel\'s own exception handler ran where boot 23\'s run had been stopped and printed a panic text on the panel, `panic(cpu 0 caller 0xfffffe00097ddef0)` with an ApplePMGR assertion, '
                    '`ApplePMGR::enableCPUComplex(UInt32, bool):4861 REQUIRE failed: complex < _cpuComplexCount`, then nested panics, `Nested panic count exceeds limit 5, machine will reset or spin` and `wdog: panic chipreset`',
                    'the monitor\'s console ring carried the first lines of the text onto the report page (six `MON CON` lines, which boots 21 and 23 did not have)',
                    'the stop (`A=0X3082BC224`, `ESR` `0x938A0005`) decodes as a 32-bit read of an address with no stage-2 mapping, INFERRED to be the kernel\'s chip-reset request',
                    'and its `REPORT.DAT` was not written back, so the page is the only record (host check, read only). | OBSERVED (one unit, one boot): the page, the panel text and the host check of the stick; '
                    'INFERRED (the decode of the stop; the readings of the assertion and of the nested panics; that the console ring is fed by the panic path); '
                    'UNKNOWN (why the assertion failed, whether `cpus=1` trips it and whether boot 23\'s exception was the same panic, whether the nested-panic cycle is XNU\'s or an artefact of the monitor, which device holds the address, the NV2 record\'s regime; SMP; userspace); '
                    'NOT RUN (symbolication, which needs the KDK; a baseline comparison of the builds); VERIFIED (host: the monitor source directories differ from boot 23\'s tree by two added files of the host-tested NVMe takeover core and no changed file): '
                    'rung 4\'s class is PANIC TEXT, M1 PASSED by its own rows; gate X4, kernel executes: PARTIAL (one vCPU, 2.25 s, to the kernel\'s own panic text)',
                    '**Boot 24 (2026-11-18; OBSERVED on video, one unit, one boot; E75;',
                    'It changed one data word in the guest and nothing in the programs: the monitor\'s `let_panic` word, which lets a guest exception that reaches its branch run XNU\'s own handler instead of ending the run.',
                    'The record\'s class is PANIC TEXT, M1\'s own verdict PASSED. Why the assertion failed, whether `cpus=1` trips it, whether the nested-panic cycle is XNU\'s or an artefact of the monitor, and which device holds the address are UNKNOWN. '
                    'Gate X4\'s "kernel executes" stands at PARTIAL (one vCPU, 2.25 s, to the kernel\'s own panic text). '
                    'Nothing is shown about CPU bring-up, SMP, a root device, userspace, stability or performance, nor that any driver started; there is no second boot, and nothing further is authorised.',
                    'the kernel honoured it and ran on one vCPU for 2.17 s, further than boot 21 and without the CPU-start stop, and the monitor stopped it itself at an exception it took at EL1 (`MON STOP GUEST_EXC`), '
                    'INFERRED to be the kernel\'s own debugger trap, the cause UNKNOWN',
                    '| E74 | The third run of the SP11 monitor entry (work item 11, M1 rung 3, plan `M1RUN-CPUS1-RUNG3`, 2026-11-18, boot 23;',
                    'the stick was byte-identical afterwards but for the Shell\'s marker and macOS\'s own `.fseventsd` entry, and its `REPORT.DAT` was not written back, so the page is the only record (host check, read only). '
                    '| OBSERVED (one unit, one boot): the page and the panel above, and the host check of the stick; INFERRED (the decode of the stop;',
                    'NOT RUN (symbolication, which needs the KDK); VERIFIED (host: the monitor sources unchanged by git diff between `ea59368` and `3f48b0a`;',
                    'which a host analysis after the boot INFERS (high confidence) to be the guest kernel\'s own debugger trap, so that the kernel had entered its panic or debugger funnel.',
                    'INFERRED (the decode of the stop; that the exception is the kernel\'s own debugger trap, high confidence; that `cpus=1` sized the log queue); UNKNOWN (why the kernel entered its debugger funnel: the cause of the panic;',
                    'rung 3 NOT PASSED (c) by the letter of its record, M1 PASSED by its own rows; gate X4, kernel executes: PARTIAL (one vCPU, 2.17 s, further than boot 21)',
                    '**Boot 23 (2026-11-18; OBSERVED on video, one unit, one boot; E74;',
                    'its text is printed only after the trap, which the monitor stopped on, so the panel shows none, and the cause is UNKNOWN. The record\'s rung 3 verdict is NOT PASSED (c) by its letter '
                    '(a stop that none of its results names), M1\'s own verdict PASSED. Gate X4\'s "kernel executes" stands at PARTIAL (one vCPU, 2.17 s, further than boot 21). '
                    'Nothing is shown about CPU bring-up, SMP, a root device, userspace, stability or performance, nor that any driver started; nothing further is authorised.'))):
            problems += [f'{where} lost {needle[:60]!r}' for needle in needles if needle not in text]
        row = status.split('| SP11 monitor entry (work item 4b; M1 rungs 1 to 7 and 9) |', 1)[-1].split(' | SP11 ', 1)[0]
        paragraph = surface.split('**Boot 20 (2026-11-18; OBSERVED on video', 1)[-1].split('**Stick payload runner', 1)[0]
        for where, text in (('the STATUS.md row of the monitor entry', row), ('the SURFACE-PRO-11.md paragraphs of boots 20, 21, 23, 24, 25, 26, 27 and 28', paragraph)):
            if re.search(r'desktop|macOS boots|kernel executes: PASS|kernel executes: DONE', text, re.I):
                problems.append(f'{where} claims more than a partial boot')
        for where, text in (('STATUS.md', status), ('SURFACE-PRO-11.md', surface)):
            if re.search(r'kernel executes: (?!PARTIAL)\w+', text):
                problems.append(f'{where} gives the kernel executes of gate X4 a status other than PARTIAL')
        if surface.count('gate X4, kernel executes: PARTIAL (one vCPU, 5 s, early boot only)') != 1 or \
                surface.count('gate X4, kernel executes: PARTIAL (one vCPU, 2.04 s, through IOKit platform matching to the first CPU-start)') != 1 or \
                surface.count('gate X4, kernel executes: PARTIAL (one vCPU, 2.17 s, further than boot 21)') != 1 or \
                surface.count('gate X4, kernel executes: PARTIAL (one vCPU, 2.25 s, to the kernel\'s own panic text)') != 1 or \
                surface.count('gate X4, kernel executes: PARTIAL (one vCPU, 60.18 s, idle at the box\'s end)') != 1 or \
                surface.count('gate X4, kernel executes: PARTIAL (one vCPU, 61.60 s, into bsd_init)') != 1 or \
                surface.count('gate X4, kernel executes: PARTIAL (one vCPU, 120.185 s, through bsd_init to a root-device wait)') != 1 or \
                surface.count('gate X4, kernel executes: PARTIAL (one vCPU, 40.08 s, to launchd\'s failed first boot task)') != 2:
            problems.append('SURFACE-PRO-11.md: E71 must say kernel executes: PARTIAL (one vCPU, 5 s, early boot only), E72 kernel executes: PARTIAL (one vCPU, 2.04 s, through IOKit platform matching to the first CPU-start), '
                            'E74 kernel executes: PARTIAL (one vCPU, 2.17 s, further than boot 21), E75 kernel executes: PARTIAL (one vCPU, 2.25 s, to the kernel\'s own panic text), '
                            'E76 kernel executes: PARTIAL (one vCPU, 60.18 s, idle at the box\'s end), E77 kernel executes: PARTIAL (one vCPU, 61.60 s, into bsd_init), E78 kernel executes: PARTIAL (one vCPU, 120.185 s, through bsd_init to a root-device wait), '
                            'and the X4 row and E79 kernel executes: PARTIAL (one vCPU, 40.08 s, to launchd\'s failed first boot task)')
        return problems

    def test_status_and_the_surface_pro_document_carry_boots_20_and_21_with_the_exact_status_words(self):
        """STATUS.md and SURFACE-PRO-11.md count boots 20 and 21 in their status text: gate X4's kernel executes stays PARTIAL, the early console loop of boot 20 and the identity of boot 21's stop
        stay INFERRED, nothing is said about a desktop or that macOS boots, and each seeded fault below must be found."""
        status, surface = (' '.join((ROOT / 'docs' / name).read_text().split()) for name in ('STATUS.md', 'SURFACE-PRO-11.md'))
        self.assertEqual(self.status_problems(status, surface), [])
        seeds = (('kernel executes: PARTIAL (one vCPU, 40.08 s, to launchd\'s failed first boot task). The monitor ended each run',
                  'kernel executes: PASS (one vCPU, 40.08 s, to launchd\'s failed first boot task). The monitor ended each run', 0),
                 ('then spun in an early console loop (INFERRED). Boot 21', 'then spun in an early console loop (VERIFIED). Boot 21', 0), ('hardware NOT RUN beyond those eight runs', 'hardware PASS beyond those eight runs', 0),
                 ('SP11 monitor entry: ran eight times.', 'SP11 monitor entry: ran once.', 0), ('the gate passed with it on the unit in boot 20 (OBSERVED, one boot;', 'the gate passed with it on the unit in boot 20 (VERIFIED, one boot;', 0),
                 ('and again in boot 21 (OBSERVED, one boot)', 'and not in boot 21 (OBSERVED, one boot)', 0),
                 ('stopped at a stage-2 fault at what is INFERRED, by address, to be its first CPU-start write.', 'stopped at a stage-2 fault at what is VERIFIED, by address, to be its first CPU-start write.', 0),
                 ('and until boot 28 the kernel was still in its start-up at the stop', 'and the kernel booted macOS: macOS boots', 0),
                 ('the monitor\'s own guest regime ran eight times, in boots 20, 21, 23, 24, 25, 26, 27 and 28, with the guards passed', 'the monitor\'s own guest regime ran once, in boot 20, with the guards passed', 0),
                 ('(gate X4: kernel executes: PARTIAL).', '(gate X4: kernel executes: PASS).', 1),
                 ('kernel executes: PARTIAL (one vCPU, 40.08 s, to launchd\'s failed first boot task). In boot 20,', 'kernel executes: DONE (one vCPU, 40.08 s, to launchd\'s failed first boot task). In boot 20,', 1),
                 ('it ran 5 s, then spun in an early console loop (INFERRED, high', 'it ran 5 s, then spun in an early console loop (VERIFIED, high', 1),
                 ('a longer box on the same image only repeats the loop.', 'a longer box on the same image reaches the desktop.', 1),
                 ('stands at PARTIAL (one vCPU, 5 s, early boot only; ran 5 s', 'stands at PASS (one vCPU, 5 s, early boot only; ran 5 s', 1),
                 ('stands at PARTIAL (one vCPU, 2.04 s, through IOKit platform matching to the first CPU-start).', 'stands at PASS (one vCPU, 2.04 s, through IOKit platform matching to the first CPU-start).', 1),
                 ('at what is INFERRED, by address, to be its first CPU-start write (gate X4', 'at what is VERIFIED, by address, to be its first CPU-start write (gate X4', 1),
                 ('which matches the A16\'s first CPU-start write by address only', 'which is the A16\'s first CPU-start write by value', 1),
                 ('this boot is evidence for it (OBSERVED, one boot), and this record changes no gate row.', 'this boot closes it (VERIFIED), and the gate is PASS.', 1),
                 ('Nothing is shown about CPU bring-up, SMP, a root device, storage, userspace, stability or performance, nor that the SPMI', 'Everything is shown about the desktop, nor that the SPMI', 1),
                 ('the monitor\'s own guest regime ran eight times, in boots 20, 21, 23, 24, 25, 26, 27 and 28 (E71, E72, E74, E75, E76, E77, E78, E79), and', 'the monitor\'s own guest regime is NOT RUN (E71, E72, E74, E75, E76, E77, E78, E79), and', 1),
                 ('the kernel honoured it (one processor line) and ran on one vCPU for 2.17 s, further than boot 21, without the CPU-start stop', 'the kernel ignored it and ran on six vCPUs for 2.17 s, further than boot 21, without the CPU-start stop', 0),
                 ('INFERRED to be the kernel\'s own debugger trap (the cause is UNKNOWN); the rung 3 record\'s own verdict is NOT PASSED (c) by its letter, M1\'s PASSED.',
                  'VERIFIED to be the kernel\'s own debugger trap (the cause is UNKNOWN); the rung 3 record\'s own verdict is PASSED by its letter, M1\'s PASSED.', 0),
                 ('INFERRED (the decode of the stop; that the exception is the kernel\'s own debugger trap, high confidence;', 'VERIFIED (the decode of the stop; that the exception is the kernel\'s own debugger trap, high confidence;', 1),
                 ('UNKNOWN (why the kernel entered its debugger funnel: the cause of the panic;', 'OBSERVED (why the kernel entered its debugger funnel: the cause of the panic;', 1),
                 ('rung 3 NOT PASSED (c) by the letter of its record, M1 PASSED by its own rows; gate X4', 'rung 3 PASSED by the letter of its record, M1 PASSED by its own rows; gate X4', 1),
                 ('and the cause is UNKNOWN. The record\'s rung 3 verdict is NOT PASSED (c) by its letter', 'and the cause is known. The record\'s rung 3 verdict is PASSED by its letter', 1),
                 ('nor that any driver started; nothing further is authorised.', 'and every driver started; a next run is authorised.', 1),
                 ('so that the kernel had entered its panic or debugger funnel.', 'so that the kernel had hung.', 1),
                 ('and its `REPORT.DAT` was not written back, so the page is the only record (host check, read only).', 'and its `REPORT.DAT` was written back, so the stick holds the record (host check, read only).', 1),
                 ('the page and the panel above, and the host check of the stick; INFERRED', 'the page and the panel above; INFERRED', 1),
                 ('NOT RUN (symbolication, which needs the KDK); VERIFIED (host:', 'NOT RUN (symbolication, which needs the KDK; the stick\'s read-back); VERIFIED (host:', 1),
                 # boot 24 (E75, the Boot 24 paragraph, the STATUS row)
                 ('and its `REPORT.DAT` was not written back, so the page is the only record (host check, read only). | OBSERVED (one unit, one boot): the page, the panel text and the host check of the stick;',
                  'and its `REPORT.DAT` was written back, so the stick holds the record (host check, read only). | OBSERVED (one unit, one boot): the page, the panel text and the host check of the stick;', 1),
                 ('| E75 | The fourth run of the SP11 monitor entry (work item 11, M1 rung 4, plan `M1RUN-LETPANIC-RUNG4`, 2026-11-18, boot 24;', '| E75 | The fourth run of the SP11 monitor entry (work item 11, M1 rung 4, plan `M1RUN-CPUS1-RUNG3`, 2026-11-18, boot 24;', 1),
                 ('`ApplePMGR::enableCPUComplex(UInt32, bool):4861 REQUIRE failed: complex < _cpuComplexCount`, then nested panics', '`ApplePMGR::enableCPUComplex(UInt32, bool):4861 REQUIRE failed: complex < _cpuComplexCount`, then a clean boot', 1),
                 ('the monitor\'s console ring carried the first lines of the text onto the report page (six `MON CON` lines, which boots 21 and 23 did not have)', 'the monitor\'s console ring carried no line of the text onto the report page (no `MON CON` line, as in boots 21 and 23)', 1),
                 ('decodes as a 32-bit read of an address with no stage-2 mapping, INFERRED to be the kernel\'s chip-reset request', 'decodes as a 32-bit write to an address with a stage-2 mapping, VERIFIED to be the kernel\'s chip-reset request', 1),
                 ('UNKNOWN (why the assertion failed, whether `cpus=1` trips it and whether boot 23\'s exception was the same panic,', 'OBSERVED (why the assertion failed, whether `cpus=1` trips it and whether boot 23\'s exception was the same panic,', 1),
                 ('rung 4\'s class is PANIC TEXT, M1 PASSED by its own rows; gate X4', 'rung 4\'s class is NOT PASSED (a), M1 PASSED by its own rows; gate X4', 1),
                 ('The record\'s class is PANIC TEXT, M1\'s own verdict PASSED.', 'The record\'s class is NOT PASSED (a), M1\'s own verdict PASSED.', 1),
                 ('there is no second boot, and nothing further is authorised.', 'there is a second boot, and a next run is authorised.', 1),
                 ('Gate X4\'s "kernel executes" stands at PARTIAL (one vCPU, 2.25 s, to the kernel\'s own panic text).', 'Gate X4\'s "kernel executes" stands at PASS (one vCPU, 2.25 s, to the kernel\'s own panic text).', 1),
                 ('In boot 24, with the monitor\'s `let_panic` word set, the kernel\'s own handler ran and printed a panic text', 'In boot 24, with the monitor\'s `let_panic` word set, the kernel\'s own handler ran and booted macOS', 1),
                 ('(WS-B, E70 to E72 and E74 to E79)', '(WS-B, E70 to E72 and E74 to E75)', 1),
                 ('Boot 24, with the monitor\'s `let_panic` word set: the kernel\'s own handler ran and printed a panic text, an ApplePMGR assertion (`enableCPUComplex`)', 'Boot 24, with the monitor\'s `let_panic` word set: the kernel\'s own handler ran and booted macOS (`enableCPUComplex`)', 0),
                 ('(why the kernel was idle is UNKNOWN; the host analyses', '(why the kernel was idle is known; the host analyses', 0),
                 ('`MON STOP WATCHDOG`, 1,155,520,917 ticks, in boot 25; `MON STOP S2_FAULT`, 1,182,753,011 ticks, in boot 26; `MON STOP WATCHDOG`, 2,307,553,890 ticks, in boot 27; `MON STOP S2_FAULT`, 769,573,015 ticks, in boot 28), returned', '`MON STOP WATCHDOG`, 96,720,173 ticks, in boot 25; `MON STOP S2_FAULT`, 1,182,753,011 ticks, in boot 26; `MON STOP WATCHDOG`, 2,307,553,890 ticks, in boot 27; `MON STOP S2_FAULT`, 769,573,015 ticks, in boot 28), returned', 0),
                 ('`MON STOP S2_FAULT`, 1,182,753,011 ticks, in boot 26; `MON STOP WATCHDOG`, 2,307,553,890 ticks, in boot 27; `MON STOP S2_FAULT`, 769,573,015 ticks, in boot 28), returned', '`MON STOP S2_FAULT`, 96,720,173 ticks, in boot 26; `MON STOP WATCHDOG`, 2,307,553,890 ticks, in boot 27; `MON STOP S2_FAULT`, 769,573,015 ticks, in boot 28), returned', 0),
                 ('in all eight boots (its regime is not recorded); `ABORTS=0` in all eight.', 'in all eight boots (its regime is recorded); `ABORTS=0` in all eight.', 0),
                 ('(in boot 24 the start-up ended in a kernel panic, in boot 25 in an idle wait-for-interrupt, in boot 26 at the scaler\'s registers, in boot 27 in the wait for a root device after bsd_init; boot 28 went on to userspace and ended in `launchd`\'s failed first boot task)', '(in boot 24 the start-up ended in a kernel panic, in boot 25 in an idle wait-for-interrupt, in boot 26 at the scaler\'s registers, in boot 27 in a desktop; boot 28 went on to userspace and ended in `launchd`\'s failed first boot task)', 0),
                 ('(the opt-in monitor entry ran eight times: the row below)', '(the opt-in monitor entry ran twice: the row below)', 0),
                 ('and the fourth run (2026-11-18, boot 24, M1 rung 4, the monitor\'s `let_panic` word set: the kernel\'s own handler ran and printed a panic text', 'and the fourth run (2026-11-18, boot 24, M1 rung 4, the monitor\'s `let_panic` word set: the kernel\'s own handler ran and printed no text', 0),
                 ('| SP11 monitor entry (work item 4b; M1 rungs 1 to 7 and 9) |', '| SP11 monitor entry (work item 4b; M1 rungs 1 to 3) |', 0),
                 # boot 25 (E76, the Boot 25 paragraph, the STATUS row)
                 ('and the fifth run (2026-11-19, boot 25, M1 rung 5,', 'and the fifth run (2026-11-19, boot 25, M1 rung 3,', 0),
                 ('the rung 5 record\'s class is WATCHDOG, NO TEXT, with PROGRESS, M1\'s PASSED', 'the rung 5 record\'s class is PANIC TEXT, M1\'s PASSED', 0),
                 ('| E76 | The fifth run of the SP11 monitor entry (work item 11, M1 rung 5, plan `M1RUN-ACKPANIC-RUNG5`, 2026-11-19, boot 25;', '| E76 | The fifth run of the SP11 monitor entry (work item 11, M1 rung 5, plan `M1RUN-LETPANIC-RUNG4`, 2026-11-19, boot 25;', 1),
                 ('(the guest\'s other five CPUs never started) and ended the run itself 60.18 s into the call', '(the guest\'s other five CPUs started) and ended the run itself 60.18 s into the call', 1),
                 ('no stop in the CPU-start block, no ApplePMGR assertion and no panic text;', 'a stop in the CPU-start block, an ApplePMGR assertion and a panic text;', 1),
                 ('the PC is the instruction after a wait-for-interrupt in the kernel\'s idle path, with every interrupt masked.', 'the PC is the instruction after a store in the kernel\'s idle path, with every interrupt enabled.', 1),
                 ('UNKNOWN (why the kernel was idle, whether any guest timer interrupt was delivered,', 'VERIFIED (why the kernel was idle, whether any guest timer interrupt was delivered,', 1),
                 ('rung 5\'s class is WATCHDOG, NO TEXT, with PROGRESS, M1 PASSED by its own rows; gate X4', 'rung 5\'s class is PANIC TEXT, M1 PASSED by its own rows; gate X4', 1),
                 ('Why the kernel was idle is UNKNOWN: the reading of the film that the quarantined guest timers can never fire is not supported by', 'Why the kernel was idle is known: the reading of the film that the quarantined guest timers can never fire is supported by', 1),
                 ('No stop occurred in the CPU-start block, the ApplePMGR driver did not assert with six CPUs and no panic text was printed;', 'A stop occurred in the CPU-start block, the ApplePMGR driver asserted with six CPUs and a panic text was printed;', 1),
                 # boot 26 (E77, the Boot 26 paragraph, the STATUS row)
                 ('and the sixth run (2026-11-19, boot 26, M1 rung 6,', 'and the sixth run (2026-11-19, boot 26, M1 rung 5,', 0),
                 ('the rung 6 record\'s class is NEW STOP, M1\'s PASSED', 'the rung 6 record\'s class is WATCHDOG, NO TEXT, M1\'s PASSED', 0),
                 ('the timer counters showed guest timer interrupts delivered (`MON TIM` `VECTORS=969`: the counter is OBSERVED, the delivery INFERRED from it)', 'the timer counters showed guest timer interrupts delivered (`MON TIM` `VECTORS=969`: VERIFIED)', 0),
                 ('| E77 | The sixth run of the SP11 monitor entry (work item 11, M1 rung 6, plan `M1RUN-ACKPANIC-RUNG6`, 2026-11-19, boot 26;', '| E77 | The sixth run of the SP11 monitor entry (work item 11, M1 rung 6, plan `M1RUN-ACKPANIC-RUNG5`, 2026-11-19, boot 26;', 1),
                 ('ended the run itself 61.60 s into the call (`MON STOP S2_FAULT`, `A=0X291000004`', 'ended the run itself 61.60 s into the call (`MON STOP S2_FAULT`, `A=0X3082BC224`', 1),
                 ('the timer counters were `MON TIM IRQS=1010 ASSERTS=2036 CLEARS=2036 VECTORS=969 CTL=0X1` (guest timer interrupts delivered: INFERRED from them)', 'the timer counters were `MON TIM IRQS=1010 ASSERTS=2036 CLEARS=2036 VECTORS=0 CTL=0X1` (guest timer interrupts delivered: INFERRED from them)', 1),
                 ('rung 6\'s class is NEW STOP, M1 PASSED by its own rows; gate X4', 'rung 6\'s class is PANIC TEXT, M1 PASSED by its own rows; gate X4', 1),
                 ('What is now shown for gate X4 (one boot): the timer counters show the guest timer path working', 'What is now VERIFIED for gate X4 (one boot): the timer counters show the guest timer path working', 1),
                 ('The record\'s prediction of a timer panic at about 92 s was not tested: the run ended at 61.60 s.', 'The record\'s prediction of a timer panic at about 92 s was refuted: the run ended at 61.60 s.', 1),
                 ('Gate X4\'s "kernel executes" stays at PARTIAL (one vCPU, 61.60 s, into bsd_init)', 'Gate X4\'s "kernel executes" stays at PASS (one vCPU, 61.60 s, into bsd_init)', 1),
                 ('the timer counters showed guest timer interrupts delivered (`MON TIM` `VECTORS=969`: the counter is OBSERVED, one boot; the delivery INFERRED)', 'the timer counters showed guest timer interrupts delivered (`MON TIM` `VECTORS=969`: VERIFIED, one boot)', 1))
        for old, new, which in seeds:
            texts = [status, surface]
            self.assertIn(old, texts[which], f'the seed {old!r} no longer matches the text: fix the seed, not the check')
            texts[which] = texts[which].replace(old, new, 1)
            self.assertTrue(self.status_problems(*texts), f'a seeded fault went unseen: {old!r} -> {new!r}')

    @staticmethod
    def storage_survey_status_problems(status, surface):
        """What is wrong with the status text of boot 22 (the S1 storage survey) in STATUS.md and SURFACE-PRO-11.md (whitespace-collapsed texts); an empty list is a pass."""
        problems = []
        for where, text, needles in (
                ('STATUS.md', status, (
                    'and a read-only survey of the SSD\'s link, controller and admin queues and of the PCIe SMMU\'s page (2026-11-18, boot 22: the SMMU\'s page was unmapped at EL2 and nothing of the SMMU was read; the storage row below)',
                    'the xHCI No-Op test, the storage survey and the monitor entry\'s eight runs in the rows below.',
                    '| SP11 storage preparation (adoption wiring, descriptor `storage`, offline provisioning, `nvmesnap`, `smmuprobe`) | EXPERIMENTAL; host tests, and one hardware survey: the read-only payloads '
                    '`nvmesnap` and `smmuprobe` (session 1) ran once, on 2026-11-18 (boot 22; OBSERVED, one unit, one boot, from a film that started late;',
                    'and found the PCIe SMMU\'s page unmapped at EL2\'s stage 1, so no SMMU register was read. Hardware NOT RUN for everything else: every write and every takeover.',
                    'The descriptor carries `storage.nvme` (OBSERVED in two boots with its provenance;',
                    'The SMMU state (D7), the SSD\'s DMA reach after a takeover (D14) and the controller takeover stay UNKNOWN and are not authorised')),
                ('SURFACE-PRO-11.md', surface, (
                    '| E73 | The storage survey (session 1 of the storage sessions, plan `nvmesnap-s1`, 2026-11-18, boot 22;', '`VERDICT STREAM=0X60100 MODE=UNKNOWN WHY=SMMU_PAGE_UNMAPPED`',
                    'the page has no stage-1 mapping at EL2, so it read no SMMU register',
                    '| OBSERVED (one unit, one boot, from a film that started late): the page above; INFERRED (the decodes of the fields; that the firmware\'s queue has wrapped once; '
                    'that the SSD\'s DMA reached the firmware\'s queue memory when the firmware ran); UNKNOWN (the SMMU\'s mode, D7;',
                    'NOT RUN (any write to the controller, the SSD or the SMMU; any read of an SMMU register): no gate changes',
                    '**Boot 22 (2026-11-18; OBSERVED on video, one unit, one boot, from a film that started late; E73;',
                    'The SMMU\'s mode (D7), why its page is unmapped, the SSD\'s DMA reach after a takeover (D14) and D11 are UNKNOWN, every write is NOT RUN, and nothing is shown about the Wi-Fi beyond the shared SMMU node; '
                    'this record changes no gate row and nothing further is authorised.',
                    'the q1n1 `nvmesnap` and `smmuprobe` payloads (read-only probes of the SSD\'s controller and of the PCIe SMMU; they ran once, in boot 22: E73)',
                    'and in boot 22 `smmuprobe` found the SMMU\'s page unmapped at EL2 and read nothing: E73)',
                    'D3, D4, D8 and D15 again on 2026-11-18 (OBSERVED in a second boot, section 7.2 there: E73);',
                    'D7 stays UNKNOWN: the read-only q1n1 `smmuprobe` payload (session 1) ran in boot 22 and found the SMMU\'s page unmapped at EL2, so nothing was read, and D14 stays UNKNOWN',
                    '(session 1 ran once, in boot 22; sessions 2 and 3 are not authorised)',
                    'ran once, in boot 22 (E73): the SMMU\'s page has no stage-1 mapping at EL2, so no SMMU register was read.',
                    'ran on 2026-11-18 (boot 22, E73) and found that page unmapped at EL2\'s stage 1, so nothing was read: OBSERVED once',
                    '`M1RUN-CONSOLE-RUNG2` (boot 21), `nvmesnap-s1` (boot 22), `M1RUN-CPUS1-RUNG3` (boot 23) and `M1RUN-LETPANIC-RUNG4` (boot 24), and on 2026-11-19 in the one step each of `M1RUN-ACKPANIC-RUNG5` (boot 25) and `M1RUN-ACKPANIC-RUNG6` (boot 26), and on 2026-11-20 in the one step each of `M1RUN-ACKPANIC-Q-RUNG7` (boot 27) and `M1RUN-ACKPANIC-M-RUNG9` (boot 28, with the big-file runner), OBSERVED, one unit, one boot each',
                    '(EXPERIMENTAL; hardware NOT RUN, except session 1, a read-only survey, which ran once in boot 22: E73).'))):
            problems += [f'{where} lost {needle[:60]!r}' for needle in needles if needle not in text]
        for where, text in (('STATUS.md', status), ('SURFACE-PRO-11.md', surface)):
            if re.search(r'smmuprobe[^|]{0,120}(?:never run|NOT RUN)|session 1, NOT RUN|smmuprobe` is the planned', text):
                problems.append(f'{where} still says that the smmuprobe payload has not run')
        paragraph = surface.split('**Boot 22 (2026-11-18; OBSERVED on video', 1)[-1].split('**Boot 23 (2026-11-18; OBSERVED on video', 1)[0].split('**Stick payload runner', 1)[0]
        if re.search(r'desktop|macOS boots|bypass|protected|kernel executes', paragraph, re.I):
            problems.append('the SURFACE-PRO-11.md paragraph of boot 22 claims more than a survey')
        return problems

    def test_status_and_the_surface_pro_document_carry_boot_22_with_the_exact_labels(self):
        """STATUS.md and SURFACE-PRO-11.md count the S1 storage survey of boot 22: it ran once, read only, found the SMMU's page unmapped at EL2 and changed no gate; the labels are exact, the old
        sentences that said the `smmuprobe` payload had not run are gone, and each seeded fault below must be found."""
        status, surface = (' '.join((ROOT / 'docs' / name).read_text().split()) for name in ('STATUS.md', 'SURFACE-PRO-11.md'))
        self.assertEqual(self.storage_survey_status_problems(status, surface), [])
        seeds = (('(boot 22; OBSERVED, one unit, one boot, from a film that started late;', '(boot 22; VERIFIED, one unit, one boot, from a film that started late;', 0),
                 ('and found the PCIe SMMU\'s page unmapped at EL2\'s stage 1, so no SMMU register was read.', 'and found the PCIe SMMU in bypass.', 0),
                 ('Hardware NOT RUN for everything else: every write and every takeover.', 'Hardware PASS for everything else.', 0),
                 ('stay UNKNOWN and are not authorised', 'stay UNKNOWN and are authorised', 0), ('(OBSERVED in two boots with its provenance;', '(VERIFIED with its provenance;', 0),
                 ('the xHCI No-Op test, the storage survey and', 'the xHCI No-Op test and', 0), ('the storage row below)', 'the storage row below; session 1, NOT RUN)', 0),
                 ('UNKNOWN (the SMMU\'s mode, D7;', 'VERIFIED (the SMMU\'s mode, D7;', 1),
                 ('NOT RUN (any write to the controller, the SSD or the SMMU; any read of an SMMU register): no gate changes', 'PASS (a write to the controller): a gate closes', 1),
                 ('they ran once, in boot 22: E73)', 'they have never run)', 1), ('(session 1 ran once, in boot 22; sessions 2 and 3 are not authorised)', '(none authorised, NOT RUN)', 1),
                 ('ran once, in boot 22 (E73): the SMMU\'s page has no stage-1 mapping at EL2, so no SMMU register was read.', 'exists and has never run.', 1),
                 ('found that page unmapped at EL2\'s stage 1, so nothing was read: OBSERVED once', 'is the planned way to read it, NOT RUN', 1),
                 ('and nothing is shown about the Wi-Fi beyond the shared SMMU node;', 'and the Wi-Fi works through the protected SMMU;', 1),
                 ('E73; [SP11-STICK-LOG.md](SP11-STICK-LOG.md) section 20).** The first run of the storage sessions', 'E73; [SP11-STICK-LOG.md](SP11-STICK-LOG.md) section 20).** The desktop of the storage sessions', 1),
                 ('`M1RUN-CONSOLE-RUNG2` (boot 21), `nvmesnap-s1` (boot 22), `M1RUN-CPUS1-RUNG3` (boot 23) and `M1RUN-LETPANIC-RUNG4` (boot 24), and on 2026-11-19 in the one step each of `M1RUN-ACKPANIC-RUNG5` (boot 25) and `M1RUN-ACKPANIC-RUNG6` (boot 26), and on 2026-11-20 in the one step each of `M1RUN-ACKPANIC-Q-RUNG7` (boot 27) and `M1RUN-ACKPANIC-M-RUNG9` (boot 28, with the big-file runner), OBSERVED', '`M1RUN-CONSOLE-RUNG2` (boot 21), `nvmesnap-s1` (boot 22), OBSERVED', 1),
                 ('(EXPERIMENTAL; hardware NOT RUN, except session 1, a read-only survey, which ran once in boot 22: E73).', '(EXPERIMENTAL; hardware NOT RUN).', 1))
        for old, new, which in seeds:
            texts = [status, surface]
            self.assertIn(old, texts[which], f'the seed {old!r} no longer matches the text: fix the seed, not the check')
            texts[which] = texts[which].replace(old, new, 1)
            self.assertTrue(self.storage_survey_status_problems(*texts), f'a seeded fault went unseen: {old!r} -> {new!r}')

    @staticmethod
    def nvme_window_survey_problems(doc):
        """What is wrong with section 7.2 of docs/SP11-NVME-WINDOW.md (the S1 survey of boot 22) or with the sentences of that document that count on it: the registers it read again must be those of
        section 7.1, the SMMU and the DMA reach stay UNKNOWN, and nothing is claimed past the page; an empty list is a pass."""
        problems = []
        s71 = doc.split('### 7.1 ', 1)[1].split('### 7.2 ', 1)[0]
        s72 = doc.split('### 7.2 ', 1)[1].split('\n## ', 1)[0]
        s7 = doc.split('\n## 7. ', 1)[1].split('\n## 8. ', 1)[0]
        hexes = lambda text: {int(v, 16) for v in re.findall(r'0x([0-9A-Fa-f]+)', text)}        # noqa: E731
        row = next((line for line in s72.splitlines() if line.startswith('| D8 again |')), '')
        if hexes(row) != {0x303C0301FF, 0x10400, 0x460001, 0x1, 0x10001, 0xCF3E7000, 0xCF3E8000} or not hexes(row) <= hexes(s71):
            problems.append('the D8 row of 7.2 does not read CAP, VS, CC, CSTS, AQA, ASQ and ACQ with the values of 7.1')
        flat = ' '.join(s72.split())
        for phrase in ('OBSERVED, one unit, one boot; the decodes are INFERRED; what the payload documents say is VERIFIED only as read in them. Nothing was written.',
                       'each equal to section 7.1\'s | OBSERVED (a second boot: the state repeats) |',
                       'the root port\'s command word reads `0x0003` (bus master off), where the firmware\'s own view of 2026-11-15 listed `0x0007` (its bearing on the SSD\'s DMA: UNKNOWN)',
                       'two Identify commands (opcode 6, namespace 1, `CDW10` 0, CIDs `0x12` and `0x11`) with success completions, in boot-services memory | OBSERVED; INFERRED (that the firmware\'s own DMA worked when it ran)',
                       'a translation fault at level 3, so no stage-1 mapping at EL2 (UNMAPPED, not aborting; why: UNKNOWN) and no SMMU register was read: `VERDICT STREAM=0X60100 MODE=UNKNOWN WHY=SMMU_PAGE_UNMAPPED` '
                       '| OBSERVED; the SMMU\'s mode and the DMA reach after a takeover UNKNOWN |',
                       'D7 and D14 stay UNKNOWN: reading the SMMU needs an EL2 stage-1 mapping of its page, which a payload cannot add (a q1n1 or monitor change).',
                       'The firmware\'s own DMA does not show that the SSD reaches a monitor-owned extent after a takeover (D14), and D11 was not exercised.'):
            if phrase not in flat:
                problems.append(f'7.2 lost {phrase[:60]!r}')
        if re.search(r'VERIFIED(?! only as read)|MODE=(?!UNKNOWN)|bypass|protected', s72):
            problems.append('7.2 upgrades a label, or gives the SMMU a mode')
        for question in ('D3', 'D4', 'D8', 'D15'):
            line = next((line for line in s7.splitlines() if line.startswith(f'| {question} |')), '')
            if 'OBSERVED in two boots (7.1, 7.2)' not in line.rsplit('|', 2)[-2]:
                problems.append(f'{question} of section 7 does not say OBSERVED in two boots (7.1, 7.2)')
        d7 = next((line for line in s7.splitlines() if line.startswith('| D7 |')), '')
        if not d7.rsplit('|', 2)[-2].strip().startswith('UNKNOWN') or 'S1\'s `smmuprobe` found that page unmapped in EL2\'s stage 1 and read nothing (7.2)' not in d7:
            problems.append('D7 of section 7 is not UNKNOWN after S1')
        whole = ' '.join(doc.split())
        for phrase in ('EXPERIMENTAL; ran once, read only (boot 22, [section 7.2](#72-what-the-s1-survey-read-on-2026-11-18-boot-22))', 'EXPERIMENTAL; image files only; NOT RUN, except session 1 (the row above)',
                       'D8 and D15 were read on 2026-11-15, section 7.1; D5 to D7 were not, the SMMU read faulted; session 1 (the q1n1 payloads `nvmesnap` and `smmuprobe`, read only) ran on 2026-11-18 '
                       'and found the SMMU\'s page unmapped at EL2, so D5 to D7 are still unread (section 7.2)'):
            if phrase not in whole:
                problems.append(f'the document lost {phrase[:60]!r}')
        return problems

    def test_section_7_2_of_the_nvme_window_document_repeats_section_7_1_and_leaves_the_smmu_unknown(self):
        """docs/SP11-NVME-WINDOW.md section 7.2 holds what boot 22 read (section 20 of this log): the same controller registers as section 7.1, the SMMU's page unmapped and its mode UNKNOWN;
        its test file is at its size limit, so this pin lives with the log's, and each seeded fault below must be found."""
        doc = (ROOT / 'docs' / 'SP11-NVME-WINDOW.md').read_text(encoding='utf-8')
        self.assertEqual(self.nvme_window_survey_problems(doc), [])
        seeds = (('ASQ `0xCF3E7000`, ACQ `0xCF3E8000`; each the same', 'ASQ `0xCF3E7000`, ACQ `0xCF3E8008`; each the same'),            # the 7.1 side
                 ('CAP `0x303C0301FF` (MQES', 'CAP `0x303C0301FE` (MQES'),
                 ('`CAP` `0x303C0301FF`, `VS` `0x00010400`', '`CAP` `0x303C0301FE`, `VS` `0x00010400`'), ('ACQ `0xCF3E8000`: each equal', 'ACQ `0xCF3E8008`: each equal'),     # the 7.2 side
                 ('(a second boot: the state repeats)', '(VERIFIED: the state is constant)'), ('(UNMAPPED, not aborting; why: UNKNOWN)', '(bypass)'),
                 ('MODE=UNKNOWN WHY=SMMU_PAGE_UNMAPPED', 'MODE=BYPASS WHY=NONE'), ('D7 and D14 stay UNKNOWN:', 'D7 and D14 are settled:'),
                 ('and D11 was not exercised.', 'and D11 passed.'), ('OBSERVED in two boots (7.1, 7.2): endpoint 01:00.0', 'OBSERVED once (7.1): endpoint 01:00.0'),
                 ('OBSERVED in two boots (7.1, 7.2): every expected field matched', 'OBSERVED once (7.1): every expected field matched'),
                 ('S1\'s `smmuprobe` found that page unmapped in EL2\'s stage 1 and read nothing (7.2)', 'S1\'s `smmuprobe` found that page in bypass and read nothing (7.2)'),
                 ('(its bearing on the SSD\'s DMA: UNKNOWN)', '(it stops the SSD\'s DMA)'), ('| OBSERVED; INFERRED (that the firmware\'s own DMA worked when it ran) |', '| VERIFIED |'),
                 ('EXPERIMENTAL; ran once, read only (boot 22,', 'EXPERIMENTAL; NOT RUN (boot 22,'), ('NOT RUN, except session 1 (the row above)', 'NOT RUN)'), ('so D5 to D7 are still unread (section 7.2)', 'so D5 to D7 are read (section 7.2)'))
        for old, new in seeds:
            self.assertIn(old, doc, f'the seed {old!r} no longer matches the text: fix the seed, not the check')
            self.assertTrue(self.nvme_window_survey_problems(doc.replace(old, new, 1)), f'a seeded fault went unseen: {old!r} -> {new!r}')

    @staticmethod
    def monitor_notes_problems(monitor, real):
        """What is wrong with the notes that count on boot 23 in docs/SP11-STICK-MONITOR.md (the header of K10 and the paragraph K11) and in docs/SP11-STICK-REAL.md (its status line and the three rows that name
        the boots of the guests): the page's values keep their labels, rung 3's verdict is the record's letter and M1's own is PASSED, and nothing is claimed past the stop; an empty list is a pass."""
        problems = []
        whole = ' '.join(monitor.split())
        for phrase in ('the record it makes ran once, in boot 23: K11).**',
                       '**K11, boot 23: rung 3 with `--boot-arg cpus=1` ran once (2026-11-18, plan `M1RUN-CPUS1-RUNG3`; OBSERVED, one unit, one boot; `docs/SP11-STICK-LOG.md` section 21).**',
                       'The operator approved K10\'s record by its SHA-256 for that one boot.',
                       'were those of boots 20 and 21 (the same 16-digit prefixes on the page) and `GUEST.DAT` was 118,908,480 bytes in 46 chunks with the one more word.',
                       'The kernel honoured `cpus=1` (one `processor_init` line where boot 21 had six, and a log queue sized for one CPU: INFERRED), the CPU-start stop of boot 21 did not recur,',
                       'the run ended 2.17 s into the call as `MON STOP GUEST_EXC` (`A=0X2000000`, `ESR_EL1` class 0; the last two hex digits of `C=` were cut by the page\'s width) on a clean report:',
                       'M1 PASSED by the rows of its record (5a), and rung 3\'s own verdict is NOT PASSED (c) by the letter of its 5d, because a guest exception is not among the ends that PASSED allows.',
                       'A host analysis after the boot INFERS (high confidence) that the exception is the guest kernel\'s own debugger trap, so that the kernel had entered its panic or debugger funnel; '
                       'its text is printed only after the trap, which the monitor stopped on, so the panel shows none, and the cause is UNKNOWN.',
                       'Still UNKNOWN: why the kernel entered the funnel, whether any driver started, and everything past the stop. The stick was read back afterwards (host check, read only): the 18 staged files unchanged, '
                       '`REPORT.DAT` not written back (the page is the only record of the run) and only the Shell\'s marker and macOS\'s own `.fseventsd` entry new (row f: held; OBSERVED, host read). '
                       'One unit, one boot; no claim about stability or performance.',
                       'Hardware: the monitor entry ran five times on the unit, in boots 20, 21, 23, 24 and 25 (M1 rungs 1 to 5, 2026-11-18 and 19; OBSERVED, one unit, one boot each; docs/SP11-STICK-LOG.md sections 18, 19, 21, 22 and 23; K9, K11, K12 and K13 below)',
                       'and rungs 3 to 5 once each, in boots 23 to 25 (K10 to K13), and the handed choice is NOT RUN; nothing here authorises a run, and',
                       'H18; host only, Hardware NOT RUN for the tooling; the record it makes ran once, in boot 24).**',
                       'H19; host only, Hardware NOT RUN for the tooling; the record it makes ran once, in boot 25).**',
                       'whether the panic path survives on this stack, and what follows its text, were UNKNOWN (section 21.7; boot 24 answers them in part, below).',
                       'and a panic text\'s first six lines did in boot 24: K12; an empty buffer prints no line)',
                       '**Boot 24 result** (2026-11-18, plan `M1RUN-LETPANIC-RUNG4`; OBSERVED, one unit, one boot; `docs/SP11-STICK-LOG.md` section 22): the kernel\'s own handler ran and printed a panic text, '
                       'an ApplePMGR assertion (`enableCPUComplex`, `complex < _cpuComplexCount`), then nested panics; its first lines reached the page as six `MON CON` lines; '
                       'the run ended 2.25 s in as `MON STOP S2_FAULT` on a clean report; rung 4\'s class is PANIC TEXT, M1 PASSED, row f held; why the assertion failed is UNKNOWN.',
                       '**Boot 25 result** (2026-11-19, plan `M1RUN-ACKPANIC-RUNG5`; OBSERVED, one unit, one boot; `docs/SP11-STICK-LOG.md` section 23; host analyses: `docs/SP11-KERNEL-STOPS.md`): '
                       'no stop in the CPU-start block, no ApplePMGR assertion and no panic text; the kernel ran the whole box and was idle in a WFI when the monitor\'s watchdog stopped it (`MON STOP WATCHDOG`, 60.18 s); '
                       'rung 5\'s class is WATCHDOG, NO TEXT, with PROGRESS, M1 PASSED, row f held; why it was idle is UNKNOWN.'):
            if phrase not in whole:
                problems.append(f'STICK-MONITOR lost {phrase[:60]!r}')
        k11 = monitor.split('**K11, boot 23:', 1)[-1].split('\n\n', 1)[0]
        if re.search(r'VERIFIED|desktop|macOS boots', k11):
            problems.append('K11 upgrades a label or claims more than the stop')
        flat = ' '.join(real.split())
        for phrase in ('its guests ran in boots 20, 21, 23, 24 and 25 (docs/SP11-STICK-LOG.md sections 18, 19, 21, 22 and 23).',
                       '| Anything the guest does under the monitor beyond the stops of boots 21 and 24 and the idle of boot 25 | UNKNOWN | the monitor entry ran with a guest of this tool, each time OBSERVED, one boot (`docs/SP11-STICK-LOG.md`): '
                       'boot 20, rung 1, panel withheld, the 5 s box, INFERRED stuck in an early loop (section 18); boot 21, rung 2, panel as the console, one vCPU, 2.04 s, a stage-2 fault at what is INFERRED, by address, to be the first CPU-start write (section 19); '
                       'boot 23, rung 3, `cpus=1`, 2.17 s, an exception at EL1 that the monitor stopped on, INFERRED to be the kernel\'s own debugger trap (section 21); '
                       'boot 24, rung 4, `let_panic`, 2.25 s, the kernel\'s own panic text, an ApplePMGR assertion, then a stage-2 fault (section 22); '
                       'boot 25, rung 5, six CPUs with the CPU-start stores dropped, 60.18 s, idle in a WFI at the box\'s end (section 23; the host analyses of the stops: `docs/SP11-KERNEL-STOPS.md`) |',
                       '(the console one ran in boot 21: D9, 2026-11-18, and with `cpus=1` in boot 23).',
                       'and with `cpus=1` to an exception at EL1 that the monitor stopped on, one vCPU, 2.17 s (boot 23: OBSERVED, one boot; INFERRED to be the kernel\'s own debugger trap); withheld, it was, INFERRED, '
                       'stuck in a loop (boot 20) | OBSERVED / INFERRED |'):
            if phrase not in flat:
                problems.append(f'STICK-REAL lost {phrase[:60]!r}')
        row = next((line for line in real.splitlines() if line.startswith('| Anything the guest does under the monitor beyond the stops of boots 21 and 24 and the idle of boot 25 |')), '')
        cells = row.split('|')
        if len(cells) < 4 or cells[2].strip() != 'UNKNOWN':
            problems.append('the STICK-REAL row about what the guest does under the monitor is missing or is not UNKNOWN')
        return problems

    def test_the_stick_monitor_and_stick_real_notes_carry_boot_23_with_the_exact_labels(self):
        """K10 and K11 of docs/SP11-STICK-MONITOR.md and three places of docs/SP11-STICK-REAL.md say what boot 23 showed (section 21 of this log): the values keep their labels, rung 3's verdict stays the record's
        letter, the stop's identity stays INFERRED and its cause UNKNOWN; each seeded fault below must be found, and each seed must hit exactly one place."""
        monitor, real = ((ROOT / 'docs' / name).read_text(encoding='utf-8') for name in ('SP11-STICK-MONITOR.md', 'SP11-STICK-REAL.md'))
        self.assertEqual(self.monitor_notes_problems(monitor, real), [])
        monitor_seeds = (('the record it makes ran once, in boot 23: K11).**', 'the record it makes has not run: K11).**'),
                         ('ran once (2026-11-18, plan `M1RUN-CPUS1-RUNG3`;', 'ran once (2026-11-18, plan `M1RUN-CONSOLE-RUNG2`;'),
                         ('were those of boots 20 and 21 (the same 16-digit prefixes on the page)', 'were new files (other 16-digit prefixes on the page)'),
                         ('118,908,480 bytes in 46 chunks with the one more word', '118,908,480 bytes in 47 chunks with the one more word'),
                         ('a log queue sized for one CPU: INFERRED)', 'a log queue sized for one CPU: OBSERVED)'), ('the CPU-start stop of boot 21 did not recur', 'the CPU-start stop of boot 21 recurred'),
                         ('the run ended 2.17 s into the call as `MON STOP GUEST_EXC`', 'the run ended 2.04 s into the call as `MON STOP S2_FAULT`'),
                         ('the last two hex digits of `C=` were cut by the page\'s width', 'the last two hex digits of `C=` were read from the stick'),
                         ('M1 PASSED by the rows of its record (5a)', 'M1 NOT PASSED by the rows of its record (5a)'),
                         ('is NOT PASSED (c) by the letter of its 5d', 'is PASSED by the letter of its 5d'),
                         ('INFERS (high confidence) that the exception is the guest kernel\'s own debugger trap', 'shows that the exception is the guest kernel\'s own debugger trap'),
                         ('so the panel shows none, and the cause is UNKNOWN.', 'so the panel shows the text, and the cause is known.'),
                         ('the 18 staged files unchanged, `REPORT.DAT` not written back (the page is the only record of the run)', 'the 18 staged files unchanged, `REPORT.DAT` written back (the stick holds the record of the run)'),
                         ('and only the Shell\'s marker and macOS\'s own `.fseventsd` entry new (row f: held; OBSERVED, host read)', 'and only the Shell\'s marker and macOS\'s own `.fseventsd` entry new (row f: NOT RUN)'),
                         ('the 18 staged files unchanged,', 'the 17 staged files unchanged,'),
                         ('(row f: held; OBSERVED, host read). One unit, one boot; no claim about stability or performance.', '(row f: held; OBSERVED, host read). The kernel boots and is stable; macOS boots.'),
                         ('Hardware: the monitor entry ran five times on the unit, in boots 20, 21, 23, 24 and 25 (M1 rungs 1 to 5,', 'Hardware: the monitor entry ran once on the unit, in boot 20 (M1 rung 1,'),
                         ('H18; host only, Hardware NOT RUN for the tooling; the record it makes ran once, in boot 24).**', 'H18; host only, Hardware NOT RUN).**'),
                         ('H19; host only, Hardware NOT RUN for the tooling; the record it makes ran once, in boot 25).**', 'H19; host only, Hardware NOT RUN).**'),
                         ('and rungs 3 to 5 once each, in boots 23 to 25 (K10 to K13), and the handed choice is NOT RUN', 'and rungs 3 to 5 are NOT RUN, and the handed choice is NOT RUN'),
                         ('**Boot 25 result** (2026-11-19, plan `M1RUN-ACKPANIC-RUNG5`;', '**Boot 25 result** (2026-11-19, plan `M1RUN-LETPANIC-RUNG4`;'),
                         ('no stop in the CPU-start block, no ApplePMGR assertion and no panic text; the kernel ran the whole box and was idle in a WFI',
                          'a stop in the CPU-start block, an ApplePMGR assertion and a panic text; the kernel ran the whole box and was idle in a WFI'),
                         ('rung 5\'s class is WATCHDOG, NO TEXT, with PROGRESS, M1 PASSED, row f held; why it was idle is UNKNOWN.', 'rung 5\'s class is PANIC TEXT, M1 PASSED, row f held; why it was idle is known.'),
                         ('were UNKNOWN (section 21.7; boot 24 answers them in part, below).', 'stay UNKNOWN (section 21.7).'),
                         ('and a panic text\'s first six lines did in boot 24: K12; an empty buffer prints no line)', 'an empty buffer prints no line)'),
                         ('**Boot 24 result** (2026-11-18, plan `M1RUN-LETPANIC-RUNG4`;', '**Boot 24 result** (2026-11-18, plan `M1RUN-CPUS1-RUNG3`;'),
                         ('an ApplePMGR assertion (`enableCPUComplex`, `complex < _cpuComplexCount`), then nested panics; its first lines reached the page as six `MON CON` lines;',
                          'an ApplePMGR assertion (`enableCPUComplex`, `complex < _cpuComplexCount`), then nested panics; none of it reached the page;'),
                         ('the run ended 2.25 s in as `MON STOP S2_FAULT` on a clean report; rung 4\'s class is PANIC TEXT, M1 PASSED, row f held; why the assertion failed is UNKNOWN.',
                          'the run ended 2.25 s in as `MON STOP S2_FAULT` on a clean report; rung 4\'s class is NOT PASSED (a), M1 PASSED, row f held; why the assertion failed is known.'))
        real_seeds = (('its guests ran in boots 20, 21, 23, 24 and 25 (docs/SP11-STICK-LOG.md sections 18, 19, 21, 22 and 23)', 'its guests ran in boots 20 and 21 (docs/SP11-STICK-LOG.md sections 18 and 19)'),
                      ('and the idle of boot 25 | UNKNOWN |', 'and the idle of boot 25 | OBSERVED |'),
                      ('INFERRED to be the kernel\'s own debugger trap (section 21); boot 24', 'VERIFIED to be the kernel\'s own debugger trap (section 21); boot 24'),
                      ('boot 24, rung 4, `let_panic`, 2.25 s, the kernel\'s own panic text, an ApplePMGR assertion, then a stage-2 fault (section 22);',
                       'boot 24, rung 4, `let_panic`, 2.25 s, the kernel\'s own panic text, a CLPC assertion, then a stage-2 fault (section 22);'),
                      ('boot 24, rung 4, `let_panic`, 2.25 s,', 'boot 24, rung 4, `let_panic`, 5.04 s,'),
                      ('boot 25, rung 5, six CPUs with the CPU-start stores dropped, 60.18 s, idle in a WFI', 'boot 25, rung 5, six CPUs with the CPU-start stores dropped, 5.04 s, idle in a WFI'),
                      ('and with `cpus=1` in boot 23).', 'and with `cpus=2` in boot 23).'),
                      ('(boot 23: OBSERVED, one boot; INFERRED to be the kernel\'s own debugger trap)', '(boot 23: OBSERVED, one boot; the kernel\'s own debugger trap)'))
        for old, new in monitor_seeds:
            self.assertEqual(monitor.count(old), 1, f'the seed {old!r} must hit exactly one place: fix the seed, not the check')
            self.assertTrue(self.monitor_notes_problems(monitor.replace(old, new, 1), real), f'a seeded fault went unseen: {old!r} -> {new!r}')
        for old, new in real_seeds:
            self.assertEqual(real.count(old), 1, f'the seed {old!r} must hit exactly one place: fix the seed, not the check')
            self.assertTrue(self.monitor_notes_problems(monitor, real.replace(old, new, 1)), f'a seeded fault went unseen: {old!r} -> {new!r}')

    def test_the_sentences_about_usb_1_ss1_not_being_started_carry_the_section_17_caveat(self):
        """Sections 1, 12, 13 and 14.2 say that usb_1_ss1 was never started by firmware, or untouched by it; they were read with the stick in the second
        port, and each keeps saying so where it stands (its words are unchanged)."""
        earlier = ' '.join(self.text.split('## 17. Result of boots 17, 18A and 19', 1)[0].split())
        for sentence in ('firmware never started it', 'was never started by firmware', 'untouched by firmware', 'is not among them'):
            places = [m.start() for m in re.finditer(re.escape(sentence), earlier, re.I)]
            self.assertTrue(places, sentence)
            for start in places:
                self.assertIn('section 17', earlier[start:start + 450], f'{sentence!r} lost its caveat')

    def test_q1n1_facts_exist(self):
        q1n1 = os.environ.get('Q1N1_DIR') or str(ROOT.parent / 'q1n1')
        base = Path(q1n1) / 'platform' / 'uefi'
        if not base.is_dir():
            self.skipTest('SKIP: no q1n1 checkout (set Q1N1_DIR)')
        facts = (('xhci.c', 'int xhci_init(struct xhci *x, uintptr_t base'), ('xhci.c', 'static void ring_init'),
                 ('xhci.c', 'uint32_t xhci_wait_port'), ('xhci.c', 'int xhci_address_device'),
                 ('xhci.c', 'int xhci_configure_endpoints'), ('xhci.c', 'int xhci_control'),
                 ('xhci.c', 'int xhci_send(struct xhci *x'), ('xhci.c', 'uint8_t *xhci_send_start'),
                 ('xhci.c', '(length % 512) == 0'), ('xhci.c', 'slot[1] = (port & 0xff) << 16'),
                 ('xhci.c', 'void xhci_release'), ('xhci.h', 'DMA is identity-mapped and cache-coherent'),
                 ('ncm.c', 'static int find_function'), ('ncm.c', 'static int get_descriptor'),
                 ('main.c', 'static int live_guarded'), ('main.c', 'static void sp11_report_el2_state'),
                 ('main.c', 'Q1N1_GUARD_RETURN'), ('main.c', 'q1n1_mmu_attr'), ('usb-ports.c', 'static int halt_host'),
                 ('usb-ports.c', 'xhci_init(&p->host, p->base, p->host_dma, Q1N1_USB_HOST_ARENA)'),
                 ('usb-ebs-prep.c', 'Snapshot while firmware USB0 runs'), ('usb-probe.c', 'USB2 HOST'))
        for name, token in facts:
            self.assertIn(token, (base / name).read_text(), f'{name}: {token}')
        self.assertFalse(any(w in (base / 'xhci.c').read_text().lower() for w in ('reset endpoint', 'tr_dequeue', 'hub_')),
                         'the study says xhci.c has no Reset Endpoint, Set TR Dequeue or hub code')


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--battery':
        failed = battery()
        for item in failed:
            print(item)
        sys.exit(1 if failed else 0)
    program = unittest.main(verbosity=1, exit=False)
    if program.result.wasSuccessful():
        shutil.rmtree(WORK, ignore_errors=True)        # the images are large; a failing run keeps them for inspection
    sys.exit(0 if program.result.wasSuccessful() else 1)
