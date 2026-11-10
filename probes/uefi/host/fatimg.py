# SPDX-License-Identifier: MIT
"""Minimal FAT16 image writer and reader for the probe's emulator tests.

Only regular-file images are ever built or read. The writer produces a
partition-less ("superfloppy") volume holding upper-case 8.3 names, which is
all a boot stick for the emulator needs. The reader understands what the
firmware's FAT driver writes back: long file names, the NT lower-case flags,
deleted entries and cluster chains.

Layout is fixed (512-byte sectors, 4 sectors per cluster, two FATs, 512 root
entries) so that images are byte-identical from run to run.
"""
from __future__ import annotations

import struct

SECTOR = 512
ATTR_DIRECTORY = 0x10
ATTR_VOLUME_ID = 0x08
ATTR_LFN = 0x0F
# 2025-01-01 00:00:00; any fixed value keeps the image deterministic.
FAT_DATE = ((2025 - 1980) << 9) | (1 << 5) | 1


class FatError(Exception):
    pass


def short_name(name: str) -> bytes:
    base, _, ext = name.partition(".")
    if not base or len(base) > 8 or len(ext) > 3 or name != name.upper() or "." in ext:
        raise FatError(f"only upper-case 8.3 names can be written: {name!r}")
    return base.ljust(8).encode("ascii") + ext.ljust(3).encode("ascii")


class _Dir:
    def __init__(self):
        self.children: dict = {}


class Fat16Image:
    def __init__(self, total_bytes: int = 16 << 20, label: str = "PROBESTICK"):
        if total_bytes % SECTOR:
            raise FatError("image size must be a multiple of 512")
        self.total_sectors = total_bytes // SECTOR
        self.spc = 4
        self.reserved = 4
        self.nfats = 2
        self.root_entries = 512
        self.root_sectors = self.root_entries * 32 // SECTOR
        fat_sectors = 1
        while True:
            clusters = (self.total_sectors - self.reserved - self.root_sectors
                        - self.nfats * fat_sectors) // self.spc
            need = ((clusters + 2) * 2 + SECTOR - 1) // SECTOR
            if need <= fat_sectors:
                break
            fat_sectors = need
        # Firmware decides FAT12/16/32 purely from the cluster count.
        if not 4085 <= clusters < 65525:
            raise FatError(f"{clusters} clusters is not a FAT16 volume")
        self.fat_sectors = fat_sectors
        self.clusters = clusters
        self.label = label
        self.root = _Dir()

    def mkdir(self, path: str) -> _Dir:
        node = self.root
        for part in filter(None, path.split("/")):
            short_name(part)
            node = node.children.setdefault(part, _Dir())
            if not isinstance(node, _Dir):
                raise FatError(f"{part!r} is a file")
        return node

    def add_file(self, path: str, data: bytes) -> None:
        parent, _, name = path.rpartition("/")
        directory = self.mkdir(parent)
        short_name(name)
        directory.children[name] = bytes(data)

    def to_bytes(self) -> bytes:
        cluster_bytes = self.spc * SECTOR
        data_start = (self.reserved + self.nfats * self.fat_sectors + self.root_sectors) * SECTOR
        img = bytearray(self.total_sectors * SECTOR)
        fat = [0] * (self.clusters + 2)
        fat[0], fat[1] = 0xFFF8, 0xFFFF
        state = {"next": 2}

        def alloc(nbytes: int) -> list:
            count = max(1, -(-nbytes // cluster_bytes))
            first = state["next"]
            if first + count > self.clusters + 2:
                raise FatError("image is full")
            state["next"] += count
            chain = list(range(first, first + count))
            for a, b in zip(chain, chain[1:]):
                fat[a] = b
            fat[chain[-1]] = 0xFFFF
            return chain

        def write_chain(chain, payload):
            for i, cluster in enumerate(chain):
                chunk = payload[i * cluster_bytes:(i + 1) * cluster_bytes]
                off = data_start + (cluster - 2) * cluster_bytes
                img[off:off + len(chunk)] = chunk

        def entry(name11, attr, first, size):
            return struct.pack("<11sBBBHHHHHHHI", name11, attr, 0, 0, 0, FAT_DATE, FAT_DATE, 0, 0,
                               FAT_DATE, first, size)

        def emit(node: _Dir, parent_first: int, is_root: bool) -> int:
            n_entries = len(node.children) + (0 if is_root else 2) + (1 if is_root else 0)
            chain = None if is_root else alloc(n_entries * 32)
            first = 0 if is_root else chain[0]
            entries = []
            if is_root:
                entries.append(entry(self.label.upper().ljust(11).encode("ascii")[:11],
                                     ATTR_VOLUME_ID, 0, 0))
            else:
                entries.append(entry(b".          ", ATTR_DIRECTORY, first, 0))
                entries.append(entry(b"..         ", ATTR_DIRECTORY, parent_first, 0))
            for name in sorted(node.children):
                child = node.children[name]
                if isinstance(child, _Dir):
                    entries.append(entry(short_name(name), ATTR_DIRECTORY,
                                         emit(child, first, False), 0))
                else:
                    first_cluster = 0
                    if child:
                        cchain = alloc(len(child))
                        write_chain(cchain, child)
                        first_cluster = cchain[0]
                    entries.append(entry(short_name(name), 0x20, first_cluster, len(child)))
            blob = b"".join(entries)
            if is_root:
                if len(blob) > self.root_entries * 32:
                    raise FatError("root directory is full")
                root_off = (self.reserved + self.nfats * self.fat_sectors) * SECTOR
                img[root_off:root_off + len(blob)] = blob
            else:
                write_chain(chain, blob)
            return first

        emit(self.root, 0, True)

        boot = bytearray(SECTOR)
        boot[0:3] = b"\xEB\x3C\x90"
        boot[3:11] = b"PROBEFAT"
        struct.pack_into("<HBHBHHBHHHII", boot, 11, SECTOR, self.spc, self.reserved, self.nfats,
                         self.root_entries,
                         self.total_sectors if self.total_sectors < 0x10000 else 0, 0xF8,
                         self.fat_sectors, 32, 2, 0,
                         0 if self.total_sectors < 0x10000 else self.total_sectors)
        struct.pack_into("<BBBI11s8s", boot, 36, 0x80, 0, 0x29, 0x50524F42,
                         self.label.upper().ljust(11).encode("ascii")[:11], b"FAT16   ")
        boot[510:512] = b"\x55\xAA"
        img[0:SECTOR] = boot
        fat_bytes = b"".join(struct.pack("<H", v) for v in fat)
        for i in range(self.nfats):
            off = (self.reserved + i * self.fat_sectors) * SECTOR
            img[off:off + len(fat_bytes)] = fat_bytes
        return bytes(img)


class FatTree:
    """What a FAT16 image contains: files by path (case preserved) and directories."""

    def __init__(self):
        self.files: dict = {}
        self.dirs: set = set()
        self.volume_label = ""


def _lfn_checksum(name11: bytes) -> int:
    total = 0
    for b in name11:
        total = (((total & 1) << 7) + (total >> 1) + b) & 0xFF
    return total


def read_fat16(image: bytes) -> FatTree:
    if len(image) < SECTOR or image[510:512] != b"\x55\xAA":
        raise FatError("no boot sector signature")
    (bps, spc, reserved, nfats, root_entries, total16, _media, fat_sectors) = struct.unpack_from(
        "<HBHBHHBH", image, 11)
    total32 = struct.unpack_from("<I", image, 32)[0]
    total = total16 or total32
    if bps != SECTOR or spc == 0 or fat_sectors == 0 or nfats == 0:
        raise FatError("unsupported BPB")
    root_sectors = (root_entries * 32 + bps - 1) // bps
    data_start = (reserved + nfats * fat_sectors + root_sectors) * bps
    clusters = (total - reserved - nfats * fat_sectors - root_sectors) // spc
    if not 4085 <= clusters < 65525:
        raise FatError("not a FAT16 volume")
    cluster_bytes = spc * bps
    fat_off = reserved * bps

    def fat_entry(cluster):
        return struct.unpack_from("<H", image, fat_off + cluster * 2)[0]

    def read_chain(first, limit=None):
        out, seen = bytearray(), set()
        cluster = first
        while 2 <= cluster < 0xFFF7:
            if cluster in seen or cluster > clusters + 1:
                raise FatError("cluster chain loops or leaves the volume")
            seen.add(cluster)
            off = data_start + (cluster - 2) * cluster_bytes
            out += image[off:off + cluster_bytes]
            cluster = fat_entry(cluster)
            if limit is not None and len(out) >= limit:
                break
        if cluster != 0xFFFF and not (0xFFF8 <= cluster) and limit is None:
            raise FatError(f"cluster chain ends with {cluster:#x}")
        return bytes(out)

    tree = FatTree()

    def walk(raw: bytes, prefix: str, is_root: bool):
        lfn_parts: dict = {}
        lfn_sum = None
        for off in range(0, len(raw), 32):
            e = raw[off:off + 32]
            if len(e) < 32 or e[0] == 0x00:
                break
            if e[0] == 0xE5:
                lfn_parts, lfn_sum = {}, None
                continue
            attr = e[11]
            if attr & 0x3F == ATTR_LFN:
                seq = e[0] & 0x1F
                units = e[1:11] + e[14:26] + e[28:32]
                lfn_parts[seq] = units
                lfn_sum = e[13]
                continue
            name11 = bytes(e[0:11])
            if attr & ATTR_VOLUME_ID:
                if is_root:
                    tree.volume_label = name11.decode("ascii", "replace").strip()
                lfn_parts, lfn_sum = {}, None
                continue
            long_name = None
            if lfn_parts and lfn_sum == _lfn_checksum(name11):
                joined = b"".join(lfn_parts[k] for k in sorted(lfn_parts))
                text = joined.decode("utf-16-le", "replace")
                long_name = text.split("\x00")[0].rstrip("\uffff")
            lfn_parts, lfn_sum = {}, None
            if long_name is None:
                base = name11[0:8].decode("ascii", "replace").rstrip()
                ext = name11[8:11].decode("ascii", "replace").rstrip()
                if e[12] & 0x08:
                    base = base.lower()
                if e[12] & 0x10:
                    ext = ext.lower()
                long_name = base + ("." + ext if ext else "")
            if long_name in (".", ".."):
                continue
            first = struct.unpack_from("<H", e, 26)[0]
            size = struct.unpack_from("<I", e, 28)[0]
            path = prefix + long_name
            if attr & ATTR_DIRECTORY:
                tree.dirs.add(path)
                walk(read_chain(first), path + "/", False)
            else:
                tree.files[path] = read_chain(first, size)[:size] if size else b""

    root_off = (reserved + nfats * fat_sectors) * bps
    walk(image[root_off:root_off + root_sectors * bps], "", True)
    return tree
