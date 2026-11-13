#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Pre-allocate and verify the SP11 stick log region and its LBA map, on image files (host only).

The design is docs/SP11-STICK-LOG.md. In one paragraph: q1n1 at EL2 has no file system and no USB stack of the firmware, so
a log must reach the boot stick as raw sector writes into a region whose place is known beforehand. This tool makes that
region on the Mac, as a contiguous 1 MiB file (SP11/SNAPLOG.BIN) inside a FAT image, plus a 128-byte map file
(SP11/SNAPLOG.MAP) that names the region's absolute LBAs, the device capacity and the volume geometry. Every sector of the
region carries a tag (magic, its own index, a random 64-bit nonce), so a write can be refused unless the target still holds
our tag: a wrong or stale map then cannot damage the stick's file system. The Mac later reads the log by copying the file.

    python3 tools/sp11_stick_logmap.py build   --tree DIR --out IMG [--size-mib N] [--device-sectors N] [--log-mib N]
    python3 tools/sp11_stick_logmap.py prepare --image IN --out OUT [--partition N] [--device-sectors N] [--log-mib N]
    python3 tools/sp11_stick_logmap.py verify  --image IMG [--partition N] [--device-sectors N] [--allow-written]
    python3 tools/sp11_stick_logmap.py read-log --image IMG [--partition N]
    python3 tools/sp11_stick_logmap.py decode  --region SNAPLOG.BIN      (a copy the operator made of the file on the stick)

Exit status 0: done / verified. 1: refused or not verified (the reasons are printed). 2: usage.

Safety: images only. Every input and output path is refused when it is, or lies below, /Volumes or /dev (by spelling in any
letter case and by identity), has a `..` component or a symbolic-link component, or sits on a mount point: the same rules as
tools/stage-sp11-boot-media.py (that script has a hyphenated name and is not a library, so the rules are repeated here and
tools/test-sp11-stick-log.py compares the two). An output file is never overwritten, an input image is never modified, and
`prepare` and `build` delete an output they cannot verify. Nothing here opens a device or a mounted stick: the operator
writes a verified image to a stick with the operating system's own tools, outside this repository.

Evidence: EXPERIMENTAL (host tests only). Hardware: NOT RUN.
"""
import argparse
import json
import os
import secrets
import struct
import sys
import zlib
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))
import sp11_fatvol as fv  # noqa: E402

SECTOR = 512
LOG_DIR, LOG_NAME, MAP_NAME = 'SP11', 'SNAPLOG.BIN', 'SNAPLOG.MAP'
HEADER_MAGIC, MAP_MAGIC, SECTOR_MAGIC = b'SNAPLOG1', b'SNAPLMAP', b'SLG1'
VERSION = 1
HEADER_BYTES, MAP_BYTES = 64, 128
PAYLOAD_BYTES = 480
MAX_RUNS = 4
DEFAULT_LOG_MIB = 1
MAX_LOG_MIB = 32
LBA_LIMIT = 0xFFFFFFFF          # READ(10) and WRITE(10) carry a 32-bit LBA; the last usable one is 0xFFFFFFFE

HEADER = struct.Struct('<8sIIQIIIIII12sI')           # 64 bytes: the region's sector 0
MAP = struct.Struct('<8sIIQ8I8I36sI')                # 128 bytes: the map file
SECTOR_TAG = struct.Struct('<4sIQIIHHI')             # 32 bytes, then 480 bytes of payload
assert (HEADER.size, MAP.size, SECTOR_TAG.size) == (HEADER_BYTES, MAP_BYTES, 32)


class Refusal(Exception):
    """A refusal with a stable `reason` and a `detail` for the human."""

    def __init__(self, reason, detail=''):
        super().__init__(f'{reason}: {detail}' if detail else reason)
        self.reason = reason
        self.detail = detail


class FormatError(Refusal):
    """A byte layout that does not decode."""


# ---------------------------------------------------------------------------------------------------------------------
# Path safety: the rules of tools/stage-sp11-boot-media.py, applied to files.
# ---------------------------------------------------------------------------------------------------------------------
PROTECTED_ROOTS = (('/Volumes', 'under_volumes'), ('/dev', 'under_dev'))


def find_symlink_component(path):
    for candidate in [path] + list(path.parents):
        try:
            if candidate.is_symlink():
                return candidate
        except OSError:
            continue
    return None


def refuse_protected_location(path, prefix):
    """Refuse a path that is, or lies below, /Volumes or /dev by any spelling the file system resolves there."""
    lowered = path.as_posix().lower()
    for root, reason in PROTECTED_ROOTS:
        if lowered == root.lower() or lowered.startswith(root.lower() + '/'):
            raise Refusal(f'{prefix}_{reason}', str(path))
        for ancestor in (path, *path.parents):
            try:
                same = os.path.samefile(ancestor, root)
            except OSError:
                continue
            if same:
                raise Refusal(f'{prefix}_{reason}', str(path))


def find_mount_point_ancestor(path):
    for candidate in (path, *path.parents):
        if candidate != candidate.parent and os.path.ismount(candidate):
            return candidate
    return None


def check_output_path(path):
    """A file to be created: a new name in an ordinary directory, never below /Volumes or /dev, never on a mount point."""
    path = Path(path)
    if '..' in path.parts:
        raise Refusal('dest_has_parent_reference', str(path))
    symlinked = find_symlink_component(path)
    if symlinked == path:
        raise Refusal('dest_is_symlink', str(path))
    if symlinked is not None:
        raise Refusal('dest_path_has_symlink_component', str(symlinked))
    absolute = path.absolute()
    refuse_protected_location(absolute, 'dest')
    if absolute.exists():
        raise Refusal('dest_exists', str(absolute))
    parent = absolute.parent
    if not parent.is_dir():
        raise Refusal('dest_parent_missing', str(parent))
    refuse_protected_location(parent, 'dest')
    if os.path.ismount(parent):
        raise Refusal('dest_is_mount_point', str(parent))
    if parent.parent.exists() and parent.stat().st_dev != parent.parent.stat().st_dev:
        raise Refusal('dest_different_filesystem_from_parent', str(parent))
    mounted = find_mount_point_ancestor(parent)
    if mounted is not None:
        raise Refusal('dest_ancestor_is_mount_point', str(mounted))
    return absolute


def check_input_path(path, directory=False):
    """An image (or, with `directory`, a tree) to be read: same location rules, and it must already exist."""
    path = Path(path)
    if '..' in path.parts:
        raise Refusal('input_has_parent_reference', str(path))
    symlinked = find_symlink_component(path)
    if symlinked == path:
        raise Refusal('input_is_symlink', str(path))
    if symlinked is not None:
        raise Refusal('input_path_has_symlink_component', str(symlinked))
    absolute = path.absolute()
    refuse_protected_location(absolute, 'input')
    if not absolute.exists():
        raise Refusal('input_does_not_exist', str(absolute))
    if directory and not absolute.is_dir():
        raise Refusal('input_is_not_directory', str(absolute))
    if not directory and not absolute.is_file():
        raise Refusal('input_is_not_regular_file', str(absolute))
    mounted = find_mount_point_ancestor(absolute if directory else absolute.parent)
    if mounted is not None:
        raise Refusal('input_ancestor_is_mount_point', str(mounted))
    return absolute


# ---------------------------------------------------------------------------------------------------------------------
# Byte layouts (the C side is tools/sp11-stick-log.c; the tables are in docs/SP11-STICK-LOG.md).
# ---------------------------------------------------------------------------------------------------------------------
def crc32(data, seed=0):
    return zlib.crc32(data, seed) & 0xFFFFFFFF


def encode_header(nonce, region_sectors, start_lba, partition_lba, capacity_sectors, version=VERSION):
    body = HEADER.pack(HEADER_MAGIC, version, HEADER_BYTES, nonce, SECTOR, region_sectors, start_lba, partition_lba,
                       capacity_sectors, 0, bytes(12), 0)[:HEADER_BYTES - 4]
    return (body + struct.pack('<I', crc32(body))).ljust(SECTOR, b'\0')


def decode_header(sector):
    """The header of region sector 0 as a dict, or a FormatError naming the first thing wrong."""
    if len(sector) != SECTOR:
        raise FormatError('header_size', str(len(sector)))
    (magic, version, header_bytes, nonce, sector_size, region_sectors, start_lba, partition_lba, capacity, flags,
     reserved, crc) = HEADER.unpack_from(sector, 0)
    if magic != HEADER_MAGIC:
        raise FormatError('header_magic', repr(magic))
    if version != VERSION or header_bytes != HEADER_BYTES:
        raise FormatError('header_version', f'{version}/{header_bytes}')
    if crc != crc32(sector[:HEADER_BYTES - 4]):
        raise FormatError('header_crc')
    if flags or any(reserved) or any(sector[HEADER_BYTES:]):
        raise FormatError('header_reserved')
    return {'nonce': nonce, 'sector_size': sector_size, 'region_sectors': region_sectors, 'start_lba': start_lba,
            'partition_lba': partition_lba, 'capacity_sectors': capacity}


def encode_log_sector(nonce, index, boot_id=0, seq=0, payload=b'', flags=0):
    if len(payload) > PAYLOAD_BYTES:
        raise Refusal('payload_too_long', str(len(payload)))
    head = SECTOR_TAG.pack(SECTOR_MAGIC, index, nonce, boot_id, seq, len(payload), flags, 0)[:28]
    crc = crc32(payload, crc32(head))
    return (head + struct.pack('<I', crc) + payload).ljust(SECTOR, b'\0')


def decode_log_sector(raw, index, nonce):
    """Fields of one log sector. `owned` is the writer's rule (magic, index and nonce); `valid` adds the CRC."""
    magic, got_index, got_nonce, boot_id, seq, used, flags, crc = SECTOR_TAG.unpack_from(raw, 0)
    owned = magic == SECTOR_MAGIC and got_index == index and got_nonce == nonce
    valid = owned and used <= PAYLOAD_BYTES and crc == crc32(raw[32:32 + used], crc32(raw[:28]))
    return {'owned': owned, 'valid': valid, 'boot_id': boot_id, 'seq': seq, 'used': used, 'flags': flags,
            'payload': raw[32:32 + used] if valid else b''}


def build_region(nonce, region_sectors, start_lba, partition_lba, capacity_sectors):
    """The pristine content of the log file: the header sector, then one empty tagged sector per remaining index."""
    out = [encode_header(nonce, region_sectors, start_lba, partition_lba, capacity_sectors)]
    out += [encode_log_sector(nonce, index) for index in range(1, region_sectors)]
    return b''.join(out)


def encode_map(nonce, capacity_sectors, partition_lba, cluster_sectors, data_lba, region_sectors, runs, version=VERSION):
    if not 1 <= len(runs) <= MAX_RUNS:
        raise Refusal('map_runs', str(len(runs)))
    flat = [v for run in runs for v in run] + [0] * (2 * (MAX_RUNS - len(runs)))
    body = MAP.pack(MAP_MAGIC, version, MAP_BYTES, nonce, SECTOR, capacity_sectors, partition_lba, cluster_sectors,
                    data_lba, region_sectors, len(runs), 0, *flat, bytes(36), 0)[:MAP_BYTES - 4]
    return body + struct.pack('<I', crc32(body))


def decode_map(raw):
    """The map file as a dict, or a FormatError naming the first thing wrong. Policy is checked by map_policy_findings."""
    if len(raw) != MAP_BYTES:
        raise FormatError('map_size', str(len(raw)))
    fields = MAP.unpack(raw)
    (magic, version, map_bytes, nonce, sector_size, capacity, partition_lba, cluster_sectors, data_lba, region_sectors,
     run_count, flags) = fields[:12]
    runs_flat, reserved, crc = fields[12:20], fields[20], fields[21]
    if magic != MAP_MAGIC:
        raise FormatError('map_magic', repr(magic))
    if version != VERSION or map_bytes != MAP_BYTES:
        raise FormatError('map_version', f'{version}/{map_bytes}')
    if crc != crc32(raw[:MAP_BYTES - 4]):
        raise FormatError('map_crc')
    if flags or any(reserved):
        raise FormatError('map_reserved')
    runs = [(runs_flat[2 * i], runs_flat[2 * i + 1]) for i in range(MAX_RUNS)]
    if not 1 <= run_count <= MAX_RUNS or any(runs[i] != (0, 0) for i in range(run_count, MAX_RUNS)):
        raise FormatError('map_runs', f'run_count {run_count}')
    return {'nonce': nonce, 'sector_size': sector_size, 'capacity_sectors': capacity, 'partition_lba': partition_lba,
            'cluster_sectors': cluster_sectors, 'data_lba': data_lba, 'region_sectors': region_sectors,
            'runs': runs[:run_count]}


def map_policy_findings(m):
    """The rules both sides enforce on a decoded map, independent of any image (the C parser applies the same)."""
    found = []
    if m['nonce'] == 0:
        found.append(('map_nonce_zero', ''))
    if m['sector_size'] != SECTOR:
        found.append(('map_sector_size', str(m['sector_size'])))
    if not 2 <= m['region_sectors'] <= 0x10000:
        found.append(('map_region_size', str(m['region_sectors'])))
    if len(m['runs']) != 1:
        found.append(('map_runs', f'{len(m["runs"])} runs: v1 accepts exactly one'))
        return found
    start, count = m['runs'][0]
    if count != m['region_sectors']:
        found.append(('map_run_not_region', f'{count} != {m["region_sectors"]}'))
    if m['capacity_sectors'] < 1 or m['capacity_sectors'] >= LBA_LIMIT:
        found.append(('map_capacity_range', str(m['capacity_sectors'])))
    if start + count > m['capacity_sectors']:
        found.append(('region_outside_capacity', f'{start}+{count} > {m["capacity_sectors"]}'))
    cs = m['cluster_sectors']
    if cs not in fv.SPC_VALUES:
        found.append(('map_cluster_sectors', str(cs)))
    elif m['data_lba'] < m['partition_lba'] or start < m['data_lba'] or (start - m['data_lba']) % cs:
        found.append(('map_geometry', f'run {start}, data {m["data_lba"]}, cluster {cs}'))
    return found


# ---------------------------------------------------------------------------------------------------------------------
# prepare and build
# ---------------------------------------------------------------------------------------------------------------------
def _device_sectors(img, device_sectors):
    if device_sectors is None:
        device_sectors = img.sectors
    if device_sectors < img.sectors:
        raise Refusal('device_smaller_than_image', f'{device_sectors} < {img.sectors}')
    if device_sectors >= LBA_LIMIT:
        raise Refusal('device_too_large', f'{device_sectors} sectors need READ CAPACITY(16)')
    return device_sectors


def _install_log(img, vol, device_sectors, log_mib, nonce, directory, make_directory):
    if not 1 <= log_mib <= MAX_LOG_MIB:
        raise Refusal('log_size', f'{log_mib} MiB')
    if nonce is None:
        nonce = secrets.randbits(64) or 1
    if not 0 < nonce < 1 << 64:
        raise Refusal('nonce_range', hex(nonce))
    if make_directory:
        try:
            vol.resolve_dir(directory)
        except fv.FatError:
            vol.mkdir(directory)
    size = log_mib << 20
    entry, first = vol.reserve_file(f'{directory}/{LOG_NAME}', size)
    region_sectors = size // SECTOR
    start_lba = vol.cluster_lba(first)
    if start_lba + region_sectors > img.sectors:
        raise Refusal('region_outside_image', f'{start_lba}+{region_sectors} of {img.sectors}')
    img.write(start_lba * SECTOR, build_region(nonce, region_sectors, start_lba, vol.part_start, device_sectors))
    raw = encode_map(nonce, device_sectors, vol.part_start, vol.sectors_per_cluster, vol.data_lba, region_sectors,
                     [(start_lba, region_sectors)])
    vol.create_file(f'{directory}/{MAP_NAME}', raw)
    return {'nonce': nonce, 'start_lba': start_lba, 'region_sectors': region_sectors, 'capacity_sectors': device_sectors,
            'partition_lba': vol.part_start, 'cluster_sectors': vol.sectors_per_cluster, 'data_lba': vol.data_lba,
            'first_cluster': first}


def _discard(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def prepare_image(src, dst, partition=None, device_sectors=None, log_mib=DEFAULT_LOG_MIB, nonce=None,
                  directory=LOG_DIR, make_directory=False):
    """Copy `src` to a NEW file `dst`, add the log region and the map there, and verify the result."""
    src = check_input_path(src)
    dst = check_output_path(dst)
    created = False
    try:
        fv.copy_sparse(src, dst)
        created = True
        with fv.ImageFile(dst, writable=True) as img:
            vol = fv.open_volume(img, partition)
            if vol.sectors_per_cluster * SECTOR > 1 << 16:
                raise Refusal('cluster_too_large', str(vol.cluster_bytes))
            facts = _install_log(img, vol, _device_sectors(img, device_sectors), log_mib, nonce, directory,
                                 make_directory)
        report = verify_image(dst, partition=partition, device_sectors=device_sectors, directory=directory)
        if not report['ok']:
            raise Refusal('prepared_image_does_not_verify', '; '.join(f'{c} {d}'.strip() for c, d in report['findings']))
    except FileExistsError as exc:
        raise Refusal('dest_exists', str(dst)) from exc
    except BaseException:
        if created:
            _discard(dst)
        raise
    facts['image'] = str(dst)
    return facts


def _tree_files(tree):
    """The regular files of a staged tree as (relative path, bytes). Anything that cannot be a plain 8.3 file is refused."""
    files, root = [], Path(tree)
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        if path.is_symlink():
            raise Refusal('tree_has_symlink', str(relative))
        if path.is_dir():
            continue
        if not path.is_file():
            raise Refusal('tree_has_special_file', str(relative))
        if len(relative.parts) > 4:
            raise Refusal('tree_too_deep', str(relative))
        for part in relative.parts:
            try:
                fv.short_name(part)
            except fv.FatError as exc:
                raise Refusal('tree_name_not_83', f'{relative}: long names need long-name entries, which this tool '
                                                  f'does not create ({exc.reason})') from exc
        files.append((relative.as_posix(), path.read_bytes()))
    return files


def build_image(tree, dst, size_mib=300, cluster_sectors=8, device_sectors=None, log_mib=DEFAULT_LOG_MIB, nonce=None,
                mbr=True, directory=LOG_DIR):
    """Build a new FAT32 image from a staged directory tree, add the log region and the map, and verify the result."""
    tree = check_input_path(tree, directory=True)
    dst = check_output_path(dst)
    files = _tree_files(tree)
    created = False
    try:
        fv.format_fat32(dst, size_mib * 2048, cluster_sectors, mbr=mbr)
        created = True
        with fv.ImageFile(dst, writable=True) as img:
            vol = fv.open_volume(img)
            made = set()
            for relative, data in files:
                parts = relative.split('/')
                for depth in range(1, len(parts)):
                    folder = '/'.join(parts[:depth])
                    if folder not in made:
                        vol.mkdir(folder)
                        made.add(folder)
                vol.create_file(relative, data)
            facts = _install_log(img, vol, _device_sectors(img, device_sectors), log_mib, nonce, directory, True)
        report = verify_image(dst, device_sectors=device_sectors, directory=directory)
        if not report['ok']:
            raise Refusal('built_image_does_not_verify', '; '.join(f'{c} {d}'.strip() for c, d in report['findings']))
    except FileExistsError as exc:
        raise Refusal('dest_exists', str(dst)) from exc
    except BaseException:
        if created:
            _discard(dst)
        raise
    facts['image'] = str(dst)
    return facts


# ---------------------------------------------------------------------------------------------------------------------
# verify and read-log
# ---------------------------------------------------------------------------------------------------------------------
def _runs(vol, chain):
    """Consecutive clusters merged into (first absolute LBA, sectors) runs."""
    runs = []
    for cluster in chain:
        lba = vol.cluster_lba(cluster)
        if runs and runs[-1][0] + runs[-1][1] == lba:
            runs[-1] = (runs[-1][0], runs[-1][1] + vol.sectors_per_cluster)
        else:
            runs.append((lba, vol.sectors_per_cluster))
    return runs


def verify_image(path, partition=None, device_sectors=None, allow_written=False, directory=LOG_DIR):
    """Re-read an image and prove the log region: returns {'ok': bool, 'findings': [(code, detail)], 'facts': {...}}.

    Proved: the file is one contiguous run; the map's run is exactly the file's clusters converted to absolute LBAs; the
    map's cluster size, data start and partition start are the volume's; the map's capacity is the device's; the region
    lies inside the device; the file read through the FAT and the raw sectors at the map's LBAs are identical; the header
    carries the signature, the nonce and the same numbers as the map; every other sector carries its own tag and a valid
    CRC, and (unless `allow_written`) is still empty.
    """
    findings, facts = [], {}

    def fail(code, detail=''):
        findings.append((code, str(detail)))

    def done():
        return {'ok': not findings, 'findings': findings, 'facts': facts}

    try:
        path = check_input_path(path)
        img = fv.ImageFile(path)
    except (Refusal, fv.FatError) as exc:
        fail(exc.reason, exc.detail)
        return done()
    with img:
        try:
            vol = fv.open_volume(img, partition)
            log_entry = vol.lookup(f'{directory}/{LOG_NAME}')
            map_entry = vol.lookup(f'{directory}/{MAP_NAME}')
        except fv.FatError as exc:
            fail('volume_error', exc)
            return done()
        if log_entry is None or map_entry is None:
            fail('log_file_missing' if log_entry is None else 'map_file_missing', directory)
            return done()
        try:
            m = decode_map(vol.read_file(map_entry))
        except (FormatError, fv.FatError) as exc:
            fail(exc.reason, exc.detail)
            return done()
        facts.update({'nonce': m['nonce'], 'runs': m['runs'], 'region_sectors': m['region_sectors'],
                      'capacity_sectors': m['capacity_sectors']})
        findings.extend(map_policy_findings(m))
        device = img.sectors if device_sectors is None else device_sectors
        if device < img.sectors:
            fail('device_smaller_than_image', f'{device} < {img.sectors}')
        if m['capacity_sectors'] != device:
            fail('capacity_mismatch', f'map {m["capacity_sectors"]}, device {device}')
        if (m['cluster_sectors'], m['data_lba'], m['partition_lba']) != (vol.sectors_per_cluster, vol.data_lba, vol.part_start):
            fail('map_geometry_vs_volume', f'map cluster {m["cluster_sectors"]} data {m["data_lba"]} partition '
                                           f'{m["partition_lba"]}; volume cluster {vol.sectors_per_cluster} data '
                                           f'{vol.data_lba} partition {vol.part_start}')
        try:
            chain = vol.chain(log_entry.first_cluster) if log_entry.first_cluster else []
        except fv.FatError as exc:
            fail('log_chain_invalid', exc)
            return done()
        if not chain:
            fail('log_file_empty')
            return done()
        runs = _runs(vol, chain)
        facts['file_runs'] = runs
        if len(runs) != 1:
            fail('fragmented', f'{len(runs)} runs')
        if log_entry.size != m['region_sectors'] * SECTOR or log_entry.size != len(chain) * vol.cluster_bytes:
            fail('log_file_size', f'size {log_entry.size}, region {m["region_sectors"] * SECTOR}, '
                                  f'{len(chain)} clusters of {vol.cluster_bytes}')
        if runs != m['runs']:
            fail('map_run_mismatch', f'map {m["runs"]}, file {runs}')
        start, count = m['runs'][0]
        if start + count > min(img.sectors, device) or start < vol.data_lba:
            fail('region_outside_image', f'{start}+{count} of {img.sectors}/{device}, data starts {vol.data_lba}')
            return done()
        raw = img.read(start * SECTOR, count * SECTOR)
        try:
            if raw != vol.read_file(log_entry):
                fail('map_vs_file_content', 'the raw sectors at the map LBAs are not the file the FAT names')
        except fv.FatError as exc:
            fail('log_file_unreadable', exc)
        try:
            header = decode_header(raw[:SECTOR])
        except FormatError as exc:
            fail(exc.reason, exc.detail)
            return done()
        for key, want in (('nonce', m['nonce']), ('region_sectors', m['region_sectors']), ('start_lba', start),
                          ('partition_lba', m['partition_lba']), ('capacity_sectors', m['capacity_sectors']),
                          ('sector_size', SECTOR)):
            if header[key] != want:
                fail('header_' + key, f'header {header[key]}, map {want}')
        bad_tag, bad_crc, written = [], [], []
        for index in range(1, count):
            sec = decode_log_sector(raw[index * SECTOR:(index + 1) * SECTOR], index, m['nonce'])
            if not sec['owned']:
                bad_tag.append(index)
            elif not sec['valid']:
                bad_crc.append(index)
            elif sec['used'] or sec['boot_id'] or sec['seq']:
                written.append(index)
        if bad_tag:
            fail('tag_invalid', f'{len(bad_tag)} sectors, first {bad_tag[:4]}')
        if bad_crc:
            fail('sector_crc', f'{len(bad_crc)} sectors, first {bad_crc[:4]}')
        if written and not allow_written:
            fail('region_not_pristine', f'{len(written)} written sectors, first {written[:4]}')
        facts['written_sectors'] = len(written)
    return done()


def decode_region(region, nonce):
    """Chronological text of a log region: sectors ordered by (boot_id, seq); torn and foreign sectors are counted."""
    segments, torn, foreign = [], [], []
    for index in range(1, len(region) // SECTOR):
        sec = decode_log_sector(region[index * SECTOR:(index + 1) * SECTOR], index, nonce)
        if not sec['owned']:
            foreign.append(index)
        elif not sec['valid']:
            torn.append(index)
        elif sec['boot_id']:
            segments.append((sec['boot_id'], sec['seq'], index, sec['payload']))
    segments.sort()
    boots = sorted({s[0] for s in segments})
    return {'text': b''.join(s[3] for s in segments), 'sectors': len(segments), 'boots': boots, 'torn': torn,
            'foreign': foreign}


def read_log(path, partition=None, directory=LOG_DIR):
    """Read the log the way the Mac does after the stick is back: the file, by name, through the FAT."""
    with fv.ImageFile(check_input_path(path)) as img:
        vol = fv.open_volume(img, partition)
        log_entry, map_entry = vol.lookup(f'{directory}/{LOG_NAME}'), vol.lookup(f'{directory}/{MAP_NAME}')
        if log_entry is None or map_entry is None:
            raise Refusal('log_file_missing', directory)
        m = decode_map(vol.read_file(map_entry))
        return decode_region(vol.read_file(log_entry), m['nonce'])


def decode_region_file(path):
    """Decode a copy of SNAPLOG.BIN that the operator copied off the stick: the header sector carries the nonce.

    The copy lives in an ordinary directory: like every input here it may not be under /Volumes or /dev."""
    absolute = check_input_path(path)
    size = absolute.stat().st_size
    if size % SECTOR or not 2 * SECTOR <= size <= MAX_LOG_MIB << 20:
        raise Refusal('region_file_size', f'{size} bytes')
    data = absolute.read_bytes()
    header = decode_header(data[:SECTOR])
    if header['region_sectors'] * SECTOR != size:
        raise Refusal('region_file_size', f'{size} bytes, the header says {header["region_sectors"]} sectors')
    return decode_region(data, header['nonce'])


# ---------------------------------------------------------------------------------------------------------------------
def _hex(text):
    return int(text, 0)


def build_argparser():
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    sub = p.add_subparsers(dest='command', required=True)
    for name in ('build', 'prepare', 'verify', 'read-log'):
        s = sub.add_parser(name)
        s.add_argument('--json', action='store_true', help='print the result as JSON')
        if name != 'build':
            s.add_argument('--image', required=True, type=Path)
            s.add_argument('--partition', type=int, help='MBR partition number (default: the only FAT one)')
        if name in ('prepare', 'build'):
            s.add_argument('--out', required=True, type=Path)
            s.add_argument('--device-sectors', type=_hex, help='capacity of the stick in 512-byte sectors (default: the '
                           'image size)')
            s.add_argument('--log-mib', type=int, default=DEFAULT_LOG_MIB)
            s.add_argument('--nonce', type=_hex, help='64-bit nonce (default: random; fixed only for tests)')
        if name == 'prepare':
            s.add_argument('--mkdir', action='store_true', help=f'create {LOG_DIR}/ when it does not exist')
        if name == 'build':
            s.add_argument('--tree', required=True, type=Path)
            s.add_argument('--size-mib', type=int, default=300)
            s.add_argument('--cluster-sectors', type=int, default=8)
            s.add_argument('--no-mbr', action='store_true', help='a bare volume at sector 0 instead of an MBR disk')
        if name in ('verify',):
            s.add_argument('--device-sectors', type=_hex, help='capacity of the stick when the image is only its head')
            s.add_argument('--allow-written', action='store_true', help='accept sectors the writer has used')
    s = sub.add_parser('decode', help='print the text in a copy of SNAPLOG.BIN')
    s.add_argument('--json', action='store_true', help='print the result as JSON')
    s.add_argument('--region', required=True, type=Path)
    return p


def main(argv=None):
    args = build_argparser().parse_args(argv)
    try:
        if args.command == 'build':
            result = build_image(args.tree, args.out, args.size_mib, args.cluster_sectors, args.device_sectors,
                                 args.log_mib, args.nonce, mbr=not args.no_mbr)
        elif args.command == 'prepare':
            result = prepare_image(args.image, args.out, args.partition, args.device_sectors, args.log_mib, args.nonce,
                                   make_directory=args.mkdir)
        elif args.command == 'verify':
            result = verify_image(args.image, args.partition, args.device_sectors, args.allow_written)
        else:
            decoded = (decode_region_file(args.region) if args.command == 'decode'
                       else read_log(args.image, args.partition))
            result = {k: v for k, v in decoded.items() if k != 'text'}
            result['text'] = decoded['text'].decode('ascii', 'backslashreplace')
    except (Refusal, fv.FatError) as exc:
        print(f'REFUSED {exc.reason} {exc.detail}'.rstrip(), file=sys.stderr)
        return 1
    if args.command == 'verify' and not result['ok']:
        for code, detail in result['findings']:
            print(f'NOT VERIFIED {code} {detail}'.rstrip(), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
    elif args.command in ('read-log', 'decode'):
        sys.stdout.write(result['text'])
    else:
        print('verified' if args.command == 'verify' else 'done', file=sys.stderr)
        for key, value in sorted(result.items()):
            print(f'{key}: {value}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
