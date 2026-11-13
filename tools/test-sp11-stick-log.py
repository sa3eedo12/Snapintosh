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
