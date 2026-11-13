#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""FAT16 and FAT32 volume access on image files, for the SP11 stick-log tools (host only).

EXPERIMENTAL. Image files only: this module opens a regular file (never a device node, never through a symbolic link),
and it never loads a whole image. It reads and writes the sectors it needs with pread and pwrite, so a sparse 300 MiB
fixture costs almost nothing. The one thing it assumes about the media is a 512-byte sector.

What it does: find a FAT volume in an image (a bare volume, or one primary partition of an MBR disk), read the BPB, follow
cluster chains with loop and bounds checks, look files up by their 8.3 names, find the first run of N free clusters,
create a directory or a contiguous file, and keep every FAT copy and the FAT32 FSInfo hints consistent. `format_fat32`
builds an empty image for fixtures and for the `build` command of tools/sp11_stick_logmap.py.

What it refuses, by a stable reason name: FAT12, exFAT, GPT, sectors other than 512 bytes, a volume that extends past the
end of the image, a chain that loops or leaves the volume, long-name creation, deletion and directory growth. Reading a
directory skips long-name entries and a file is matched by its short name, which is the name the SP11 boot-media layout
uses for the files this project stages.

Evidence: EXPERIMENTAL (host tests only: tools/test-sp11-stick-log.py, which also checks images against macOS's own
fsck_msdos where it exists). Hardware: NOT RUN. Nothing here touches a stick.
"""
import dataclasses
import os
import stat
import struct

SECTOR = 512
# 2025-01-01: a fixed stamp keeps every image this module builds byte-identical from run to run.
FAT_DATE = ((2025 - 1980) << 9) | (1 << 5) | 1
ATTR_VOLUME, ATTR_DIRECTORY, ATTR_LFN = 0x08, 0x10, 0x0F
# MBR partition types that hold a FAT volume: FAT12/16 (0x01, 0x04, 0x06), FAT32 (0x0B, 0x0C), FAT16 LBA (0x0E).
FAT_PARTITION_TYPES = (0x01, 0x04, 0x06, 0x0B, 0x0C, 0x0E)
ENTRY = struct.Struct('<11sBBBHHHHHHHI')
FAT32_MIN_CLUSTERS = 65525
FAT16_MIN_CLUSTERS = 4085
SPC_VALUES = (1, 2, 4, 8, 16, 32, 64, 128)


class FatError(ValueError):
    """A refusal. `reason` is a stable machine name; `detail` says what was found."""

    def __init__(self, reason, detail=''):
        super().__init__(f'{reason}: {detail}' if detail else reason)
        self.reason = reason
        self.detail = detail


class ImageFile:
    """Random access to one regular file. Bounds-checked: a read or write past the end is a refusal, not a short result."""

    def __init__(self, path, writable=False):
        flags = (os.O_RDWR if writable else os.O_RDONLY) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0)
        try:
            self.fd = os.open(path, flags)
        except OSError as exc:
            raise FatError('image_unreadable', f'{path}: {exc.strerror}') from exc
        info = os.fstat(self.fd)
        if not stat.S_ISREG(info.st_mode):
            os.close(self.fd)
            raise FatError('image_not_regular_file', str(path))
        self.path = str(path)
        self.size = info.st_size
        self.writable = writable

    @property
    def sectors(self):
        return self.size // SECTOR

    def read(self, offset, length):
        if offset < 0 or length < 0 or offset + length > self.size:
            raise FatError('read_beyond_image', f'{offset}+{length} of {self.size}')
        out = bytearray()
        while len(out) < length:
            block = os.pread(self.fd, length - len(out), offset + len(out))
            if not block:
                raise FatError('short_read', f'{offset + len(out)}')
            out += block
        return bytes(out)

    def write(self, offset, data):
        if not self.writable:
            raise FatError('image_read_only', self.path)
        if offset < 0 or offset + len(data) > self.size:
            raise FatError('write_beyond_image', f'{offset}+{len(data)} of {self.size}')
        done = 0
        while done < len(data):
            done += os.pwrite(self.fd, data[done:], offset + done)

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def looks_like_boot_sector(sector0):
    """True when sector 0 carries a plausible FAT BPB (so the image is a bare volume, not an MBR disk)."""
    if len(sector0) < SECTOR or sector0[510:512] != b'\x55\xaa' or sector0[0] not in (0xEB, 0xE9):
        return False
    bps, rsvd = struct.unpack_from('<H', sector0, 11)[0], struct.unpack_from('<H', sector0, 14)[0]
    # Other sector sizes still look like a boot sector, so that the refusal names the sector size, not a missing partition.
    return bps in (512, 1024, 2048, 4096) and sector0[13] in SPC_VALUES and sector0[16] in (1, 2) and rsvd >= 1


def read_partitions(img):
    """The used primary entries of the MBR in sector 0 (each as a dict). Refuses a disk without the 0x55AA signature."""
    sector0 = img.read(0, SECTOR)
    if sector0[510:512] != b'\x55\xaa':
        raise FatError('mbr_signature_missing')
    parts = []
    for i in range(4):
        entry = sector0[446 + 16 * i:462 + 16 * i]
        start, count = struct.unpack_from('<II', entry, 8)
        if entry[4]:
            parts.append({'index': i + 1, 'type': entry[4], 'start': start, 'sectors': count})
    return parts


def open_volume(img, partition=None):
    """Find the FAT volume of an image: a bare volume at sector 0, or one MBR partition (the only FAT one, or `partition`)."""
    if partition is None and looks_like_boot_sector(img.read(0, SECTOR)):
        return FatVolume(img, 0)
    parts = read_partitions(img)
    if any(p['type'] == 0xEE for p in parts):
        raise FatError('gpt_unsupported', 'a protective MBR: this tool reads MBR disks and bare volumes only')
    if partition is not None:
        chosen = [p for p in parts if p['index'] == partition]
        if not chosen:
            raise FatError('partition_missing', str(partition))
    else:
        chosen = [p for p in parts if p['type'] in FAT_PARTITION_TYPES]
        if not chosen:
            raise FatError('no_fat_partition')
        if len(chosen) > 1:
            raise FatError('partition_ambiguous', 'name one with --partition')
    part = chosen[0]
    if not part['start'] or not part['sectors'] or part['start'] + part['sectors'] > img.sectors:
        raise FatError('partition_outside_image', f'{part["start"]}+{part["sectors"]} of {img.sectors}')
    vol = FatVolume(img, part['start'])
    if vol.total_sectors > part['sectors']:
        raise FatError('volume_exceeds_partition', f'{vol.total_sectors} > {part["sectors"]}')
    return vol


@dataclasses.dataclass(frozen=True)
class Entry:
    name: bytes          # the 11-byte short name
    attr: int
    first_cluster: int
    size: int
    offset: int          # byte offset of the 32-byte directory entry inside the image

    @property
    def is_dir(self):
        return bool(self.attr & ATTR_DIRECTORY)


def short_name(name):
    """The strict upper-case 8.3 form (11 bytes). Anything that would need a long-name entry is refused."""
    base, dot, ext = name.partition('.')
    allowed = set('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-')
    if (not base or len(base) > 8 or len(ext) > 3 or '.' in ext or (dot and not ext)
            or not set(base + ext) <= allowed):
        raise FatError('name_not_83', repr(name))
    return base.ljust(8).encode('ascii') + ext.ljust(3).encode('ascii')


class FatVolume:
    """One FAT16 or FAT32 volume inside an ImageFile. `part_start` is the absolute LBA of its boot sector."""

    def __init__(self, img, part_start=0):
        self.img = img
        self.part_start = part_start
        raw = img.read(part_start * SECTOR, SECTOR)
        if raw[510:512] != b'\x55\xaa':
            raise FatError('boot_signature_missing')
        if raw[3:11] == b'EXFAT   ':
            raise FatError('exfat_unsupported')
        bps, spc, rsvd, nfats, root_entries, tot16, _media, fatsz16 = struct.unpack_from('<HBHBHHBH', raw, 11)
        tot32 = struct.unpack_from('<I', raw, 32)[0]
        # The FAT32-only fields share their offsets with the FAT16 boot code and volume id: read them only when the BPB is
        # the FAT32 layout (no 16-bit FAT size).
        fat32_layout = fatsz16 == 0
        if fat32_layout:
            fatsz32, ext_flags, _fsver, root_cluster, fsinfo = struct.unpack_from('<IHHIH', raw, 36)
        else:
            fatsz32 = ext_flags = root_cluster = fsinfo = 0
        if bps != SECTOR:
            raise FatError('unsupported_sector_size', str(bps))
        if spc not in SPC_VALUES or nfats not in (1, 2) or rsvd < 1:
            raise FatError('bpb_invalid', f'spc={spc} nfats={nfats} reserved={rsvd}')
        self.sectors_per_cluster = spc
        self.cluster_bytes = spc * SECTOR
        self.reserved = rsvd
        self.nfats = nfats
        self.fat_sectors = fatsz16 or fatsz32
        self.total_sectors = tot16 or tot32
        self.root_entries = root_entries
        self.root_sectors = (root_entries * 32 + SECTOR - 1) // SECTOR
        if not self.fat_sectors or not self.total_sectors:
            raise FatError('bpb_invalid', 'zero FAT size or volume size')
        self.data_start = rsvd + nfats * self.fat_sectors + self.root_sectors     # volume-relative sector of cluster 2
        if self.total_sectors <= self.data_start:
            raise FatError('bpb_invalid', 'volume smaller than its metadata')
        self.clusters = (self.total_sectors - self.data_start) // spc
        if part_start + self.total_sectors > img.sectors:
            raise FatError('volume_exceeds_image', f'{part_start}+{self.total_sectors} of {img.sectors} sectors')
        if self.clusters < FAT16_MIN_CLUSTERS:
            raise FatError('fat12_unsupported', f'{self.clusters} clusters')
        self.kind = 'FAT16' if self.clusters < FAT32_MIN_CLUSTERS else 'FAT32'
        if self.kind == 'FAT32':
            if root_entries or fatsz16 or root_cluster < 2:
                raise FatError('bpb_invalid', 'FAT32 volume with a FAT16-style root')
            self.root_cluster = root_cluster
            self.fsinfo_sector = fsinfo if 0 < fsinfo < rsvd else 0
            self.entry_bytes, self.entry_mask, self.eoc, self.bad = 4, 0x0FFFFFFF, 0x0FFFFFFF, 0x0FFFFFF7
            self.mirrored = not ext_flags & 0x80
            self.active_fat = ext_flags & 0x0F
        else:
            if not root_entries or fatsz32:
                raise FatError('bpb_invalid', 'FAT16 volume with a FAT32-style root')
            self.root_cluster = 0
            self.fsinfo_sector = 0
            self.entry_bytes, self.entry_mask, self.eoc, self.bad = 2, 0xFFFF, 0xFFFF, 0xFFF7
            self.mirrored, self.active_fat = True, 0
        if self.active_fat >= nfats:
            raise FatError('bpb_invalid', f'active FAT {self.active_fat} of {nfats}')
        self.eoc_min = self.bad + 1
        self.root_dir_offset = (rsvd + nfats * self.fat_sectors) * SECTOR + part_start * SECTOR

    # -- geometry -------------------------------------------------------------------------------------------------
    @property
    def data_lba(self):
        """Absolute LBA of cluster 2, the first sector of the data area."""
        return self.part_start + self.data_start

    def cluster_lba(self, cluster):
        if cluster < 2 or cluster > self.clusters + 1:
            raise FatError('cluster_out_of_range', str(cluster))
        return self.data_lba + (cluster - 2) * self.sectors_per_cluster

    def cluster_offset(self, cluster):
        return self.cluster_lba(cluster) * SECTOR

    # -- the FAT --------------------------------------------------------------------------------------------------
    def _fat_offset(self, copy):
        return (self.part_start + self.reserved + copy * self.fat_sectors) * SECTOR

    def fat_get(self, cluster):
        if cluster < 0 or cluster > self.clusters + 1:
            raise FatError('cluster_out_of_range', str(cluster))
        raw = self.img.read(self._fat_offset(self.active_fat) + cluster * self.entry_bytes, self.entry_bytes)
        return int.from_bytes(raw, 'little') & self.entry_mask

    def fat_set(self, cluster, value):
        if cluster < 2 or cluster > self.clusters + 1:
            raise FatError('cluster_out_of_range', str(cluster))
        copies = range(self.nfats) if self.mirrored else (self.active_fat,)
        for copy in copies:
            offset = self._fat_offset(copy) + cluster * self.entry_bytes
            old = int.from_bytes(self.img.read(offset, self.entry_bytes), 'little')
            # FAT32 keeps four reserved bits above the 28-bit entry; they are not ours to change.
            merged = (old & ~self.entry_mask) | (value & self.entry_mask)
            self.img.write(offset, merged.to_bytes(self.entry_bytes, 'little'))

    def chain(self, first):
        """The clusters of the chain starting at `first`, in order. Loops, free or bad links and strays are refusals."""
        out, seen, cluster = [], set(), first
        while True:
            if cluster < 2 or cluster > self.clusters + 1:
                raise FatError('chain_leaves_volume', str(cluster))
            if cluster in seen:
                raise FatError('chain_loops', str(cluster))
            seen.add(cluster)
            out.append(cluster)
            link = self.fat_get(cluster)
            if link >= self.eoc_min:
                return out
            if link == 0 or link == self.bad:
                raise FatError('chain_ends_in_free_or_bad', f'cluster {cluster} -> {link:#x}')
            cluster = link

    def _fat_entries(self):
        count = self.clusters + 2
        raw = self.img.read(self._fat_offset(self.active_fat), count * self.entry_bytes)
        fmt = '<%d%s' % (count, 'H' if self.entry_bytes == 2 else 'I')
        return [v & self.entry_mask for v in struct.unpack(fmt, raw)]

    def find_free_run(self, count):
        """First cluster of the lowest run of `count` consecutive free clusters, or None."""
        entries, run = self._fat_entries(), 0
        for cluster in range(2, self.clusters + 2):
            run = run + 1 if entries[cluster] == 0 else 0
            if run == count:
                return cluster - count + 1
        return None

    def allocate(self, count):
        """Take `count` contiguous free clusters, link them into one chain and return the first. Refuses when none exists."""
        if count < 1:
            raise FatError('allocate_zero')
        first = self.find_free_run(count)
        if first is None:
            raise FatError('no_contiguous_free_run', f'{count} clusters')
        for i in range(count):
            self.fat_set(first + i, first + i + 1 if i + 1 < count else self.eoc)
        self._fsinfo_adjust(-count, first + count)
        return first

    def _fsinfo_adjust(self, free_delta, next_free):
        if not self.fsinfo_sector:
            return
        offset = (self.part_start + self.fsinfo_sector) * SECTOR
        raw = bytearray(self.img.read(offset, SECTOR))
        if struct.unpack_from('<III', raw, 0)[0] != 0x41615252 or struct.unpack_from('<I', raw, 484)[0] != 0x61417272:
            return
        free, _hint = struct.unpack_from('<II', raw, 488)
        if free != 0xFFFFFFFF:
            free = max(0, free + free_delta)
        hint = next_free if next_free <= self.clusters + 1 else 2
        struct.pack_into('<II', raw, 488, free, hint)
        self.img.write(offset, bytes(raw))

    # -- directories ----------------------------------------------------------------------------------------------
    def _dir_ranges(self, dir_cluster):
        if dir_cluster == 0:
            if self.kind != 'FAT16':
                raise FatError('directory_invalid', 'cluster 0 on FAT32')
            return [(self.root_dir_offset, self.root_sectors * SECTOR)]
        return [(self.cluster_offset(c), self.cluster_bytes) for c in self.chain(dir_cluster)]

    def _slots(self, dir_cluster):
        for start, length in self._dir_ranges(dir_cluster):
            raw = self.img.read(start, length)
            for at in range(0, length, 32):
                yield start + at, raw[at:at + 32]

    @staticmethod
    def _parse(offset, raw):
        _name, attr, _nt, _tenth, _ct, _cd, _ad, hi, _wt, _wd, lo, size = ENTRY.unpack(raw)
        return Entry(raw[0:11], attr, (hi << 16) | lo, size, offset)

    def root_ref(self):
        return self.root_cluster if self.kind == 'FAT32' else 0

    def lookup_in(self, dir_cluster, name11):
        for offset, raw in self._slots(dir_cluster):
            if raw[0] == 0x00:
                return None
            if raw[0] == 0xE5 or raw[11] & 0x3F == ATTR_LFN or raw[11] & ATTR_VOLUME:
                continue
            if raw[0:11] == name11:
                return self._parse(offset, raw)
        return None

    def resolve_dir(self, path):
        """The directory reference (first cluster; 0 for the FAT16 root) of `path` ('' or '/' is the root)."""
        ref = self.root_ref()
        for part in [p for p in path.split('/') if p]:
            entry = self.lookup_in(ref, short_name(part))
            if entry is None or not entry.is_dir:
                raise FatError('directory_missing', path)
            ref = entry.first_cluster
        return ref

    def lookup(self, path):
        parent, _, name = path.strip('/').rpartition('/')
        return self.lookup_in(self.resolve_dir(parent), short_name(name))

    def read_file(self, entry):
        if entry.is_dir:
            raise FatError('not_a_file', repr(entry.name))
        if not entry.size:
            return b''
        data = b''.join(self.img.read(self.cluster_offset(c), self.cluster_bytes) for c in self.chain(entry.first_cluster))
        if len(data) < entry.size:
            raise FatError('chain_shorter_than_size', f'{len(data)} < {entry.size}')
        return data[:entry.size]

    def _add_entry(self, dir_cluster, name11, attr, first_cluster, size):
        for offset, raw in self._slots(dir_cluster):
            if raw[0] in (0x00, 0xE5):
                packed = ENTRY.pack(name11, attr, 0, 0, 0, FAT_DATE, FAT_DATE, first_cluster >> 16, 0, FAT_DATE,
                                    first_cluster & 0xFFFF, size)
                self.img.write(offset, packed)
                return offset
        raise FatError('directory_full', 'this module does not grow directories')

    def mkdir(self, path):
        parent, _, name = path.strip('/').rpartition('/')
        parent_ref = self.resolve_dir(parent)
        name11 = short_name(name)
        if self.lookup_in(parent_ref, name11) is not None:
            raise FatError('exists', path)
        cluster = self.allocate(1)
        self.img.write(self.cluster_offset(cluster), bytes(self.cluster_bytes))
        dot = ENTRY.pack(b'.          ', ATTR_DIRECTORY, 0, 0, 0, FAT_DATE, FAT_DATE, cluster >> 16, 0, FAT_DATE,
                         cluster & 0xFFFF, 0)
        # A directory's `..` names its parent by first cluster, but the root is always 0, on FAT32 too (fsck_msdos checks it).
        parent_link = 0 if parent_ref == self.root_ref() else parent_ref
        dotdot = ENTRY.pack(b'..         ', ATTR_DIRECTORY, 0, 0, 0, FAT_DATE, FAT_DATE, parent_link >> 16, 0, FAT_DATE,
                            parent_link & 0xFFFF, 0)
        self.img.write(self.cluster_offset(cluster), dot + dotdot)
        self._add_entry(parent_ref, name11, ATTR_DIRECTORY, cluster, 0)
        return cluster

    def create_file(self, path, data):
        """Create a file whose clusters are one contiguous run. An empty file takes no cluster."""
        parent, _, name = path.strip('/').rpartition('/')
        parent_ref = self.resolve_dir(parent)
        name11 = short_name(name)
        if self.lookup_in(parent_ref, name11) is not None:
            raise FatError('exists', path)
        first = 0
        if data:
            clusters = -(-len(data) // self.cluster_bytes)
            first = self.allocate(clusters)
            self.img.write(self.cluster_offset(first), data + bytes(clusters * self.cluster_bytes - len(data)))
        self._add_entry(parent_ref, name11, 0x20, first, len(data))
        return self.lookup_in(parent_ref, name11)

    def reserve_file(self, path, size):
        """Create a file of `size` bytes on one contiguous run of clusters WITHOUT writing its data (the caller does).

        Returns (entry, first_cluster). The size must be a whole number of clusters: a file whose last cluster is only
        partly inside it would leave slack that raw writes must never touch.
        """
        if size < 1 or size % self.cluster_bytes:
            raise FatError('size_not_cluster_multiple', f'{size} bytes, cluster {self.cluster_bytes}')
        parent, _, name = path.strip('/').rpartition('/')
        parent_ref = self.resolve_dir(parent)
        name11 = short_name(name)
        if self.lookup_in(parent_ref, name11) is not None:
            raise FatError('exists', path)
        first = self.allocate(size // self.cluster_bytes)
        self._add_entry(parent_ref, name11, 0x20, first, size)
        return self.lookup_in(parent_ref, name11), first


def format_fat32(path, total_sectors, sectors_per_cluster=8, partition_start=2048, mbr=True, label='SP11STICK',
                 volume_id=0x534C4F47):
    """Create `path` (which must not exist) as a sparse image of `total_sectors` holding one empty FAT32 volume.

    With `mbr` the volume is the only primary partition (type 0x0C) starting at `partition_start`; without it the volume
    starts at sector 0. The layout follows Microsoft's published FAT32 sizing rule, so the cluster count decides the type
    the way every firmware and driver decides it.
    """
    spc = sectors_per_cluster
    if spc not in SPC_VALUES:
        raise FatError('bad_cluster_size', str(spc))
    start = partition_start if mbr else 0
    volume_sectors = total_sectors - start
    reserved, nfats = 32, 2
    tmp2 = (256 * spc + nfats) // 2
    fat_sectors = (volume_sectors - reserved + tmp2 - 1) // tmp2
    clusters = (volume_sectors - reserved - nfats * fat_sectors) // spc
    if clusters < FAT32_MIN_CLUSTERS:
        raise FatError('too_few_clusters_for_fat32', f'{clusters} clusters from {total_sectors} sectors at {spc} per cluster')
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0), 0o644)
    try:
        os.ftruncate(fd, total_sectors * SECTOR)

        def put(sector, data):
            os.pwrite(fd, data, sector * SECTOR)

        boot = bytearray(SECTOR)
        boot[0:3] = b'\xeb\x58\x90'
        boot[3:11] = b'SNAPFAT '
        struct.pack_into('<HBHBHHBH', boot, 11, SECTOR, spc, reserved, nfats, 0, 0, 0xF8, 0)
        struct.pack_into('<HHII', boot, 24, 63, 255, start, volume_sectors)
        struct.pack_into('<IHHIHH', boot, 36, fat_sectors, 0, 0, 2, 1, 6)
        boot[64], boot[66] = 0x80, 0x29
        struct.pack_into('<I', boot, 67, volume_id)
        boot[71:82] = label.upper().ljust(11)[:11].encode('ascii')
        boot[82:90] = b'FAT32   '
        boot[510:512] = b'\x55\xaa'
        info = bytearray(SECTOR)
        struct.pack_into('<I', info, 0, 0x41615252)
        struct.pack_into('<I', info, 484, 0x61417272)
        struct.pack_into('<II', info, 488, clusters - 1, 3)
        struct.pack_into('<I', info, 508, 0xAA550000)
        tail = bytearray(SECTOR)
        tail[510:512] = b'\x55\xaa'
        for base in (start, start + 6):
            put(base, bytes(boot))
            put(base + 1, bytes(info))
            put(base + 2, bytes(tail))
        fat = bytearray(SECTOR)
        struct.pack_into('<III', fat, 0, 0x0FFFFFF8, 0x0FFFFFFF, 0x0FFFFFFF)
        for copy in range(nfats):
            put(start + reserved + copy * fat_sectors, bytes(fat))
        root = start + reserved + nfats * fat_sectors
        put(root, ENTRY.pack(label.upper().ljust(11)[:11].encode('ascii'), ATTR_VOLUME, 0, 0, 0, FAT_DATE, FAT_DATE, 0, 0,
                             FAT_DATE, 0, 0))
        if mbr:
            sector0 = bytearray(SECTOR)
            struct.pack_into('<I', sector0, 440, volume_id)
            sector0[446:462] = struct.pack('<B3sB3sII', 0, b'\xfe\xff\xff', 0x0C, b'\xfe\xff\xff', start, volume_sectors)
            sector0[510:512] = b'\x55\xaa'
            put(0, bytes(sector0))
    except BaseException:
        os.close(fd)
        os.unlink(path)           # only reached after our own O_EXCL create: nothing else is ever removed
        raise
    os.close(fd)


def copy_sparse(src, dst):
    """Copy a regular file to a new path, leaving all-zero 1 MiB blocks as holes. Refuses an existing destination.

    A copy that fails part-way is removed again; only a file this call created is ever removed.
    """
    chunk = 1 << 20
    sfd = os.open(src, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0))
    try:
        info = os.fstat(sfd)
        if not stat.S_ISREG(info.st_mode):
            raise FatError('image_not_regular_file', str(src))
        dfd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0),
                      0o644)
        try:
            os.ftruncate(dfd, info.st_size)
            zero = bytes(chunk)
            for offset in range(0, info.st_size, chunk):
                block = os.pread(sfd, chunk, offset)
                if block != zero[:len(block)]:
                    os.pwrite(dfd, block, offset)
        except BaseException:
            os.close(dfd)
            os.unlink(dst)
            raise
        os.close(dfd)
    finally:
        os.close(sfd)
