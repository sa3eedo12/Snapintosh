#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Give newly formatted, empty scratch APFS images the documented ISC roles.

This creates guest test media, not SEP-backed trusted storage. Apple's host
formatter restricts creation of the xART role to entitled callers. We instead
encode the public APFS field in a separate, unmounted image file and retain
the original. Never pass a physical device or an existing user filesystem.

  SOURCE OUTPUT --source-sha256 H
      Stamp xART (0x100) on a fresh one-volume container.
  --three-role SOURCE OUTPUT --source-sha256 H
      Stamp xART (0x100), Preboot (0x10) and Hardware (0x140), the roles
      tools/cycle_lib/speed_test.py demands of the ISC image, on a fresh
      container of exactly the three volumes Snapintosh-xART,
      Snapintosh-Preboot and Snapintosh-Hardware. Needs no macOS tool.
  --format-scratch SCRATCH [--size-mib N]
      macOS only: format that fresh three-volume container into a new sparse
      file under build/ with hdiutil and newfs_apfs, on the node of that one
      attached scratch image only, and detach it again.
  --build-three-role OUTPUT [--size-mib N]
      --format-scratch into OUTPUT.scratch, then --three-role into OUTPUT.

The stamped roles are a host-readable fact (speed_test.isc_roles). Whether the
guest's APFS accepts empty volumes with these roles is UNKNOWN until a boot.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import struct
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
MAX_SCRATCH_BYTES = 2 << 30  # permits native xART, Preboot and Hardware volumes
# newfs_apfs sizes nx_max_file_systems as ceil(size / 512 MiB) (OBSERVED with the
# macOS 27.3 formatter: 512 MiB 1, 768 MiB 2, 1 GiB 2, 1.5 GiB 3, 2 GiB 4), so a
# container that can hold three volumes needs more than 1 GiB.
MIN_THREE_ROLE_BYTES = (1 << 30) + 0x4000
DEFAULT_SIZE_MIB = 1536  # INFERRED order of the A16 chain's image (the oracle's medium minus the root), not read from it
APFS_CONTAINER_GUID = 'EF57347C-0000-11AA-AA11-00306543ECAC'
WINDOW = 1 << 20
# Names are this tool's labels; roles are apfs_role at volume superblock +964.
# The letter is newfs_apfs -R: the formatter may refuse it, then the role is stamped.
THREE_ROLE_VOLUMES = (
    ('Snapintosh-xART', 0x100, 'x'),
    ('Snapintosh-Preboot', 0x10, 'b'),
    ('Snapintosh-Hardware', 0x140, 'h'),
)


def checksum(block):
    if len(block) != 4096:
        raise ValueError('expected a 4 KiB APFS metadata block')
    a = b = 0
    mod = 0xffffffff
    for word, in struct.iter_unpack('<I', block[8:]):
        a = (a + word) % mod
        b = (b + a) % mod
    low = mod - (a + b) % mod
    high = mod - (a + low) % mod
    return struct.pack('<II', low, high)


def check_container(source):
    if not 0x4000 <= len(source) <= MAX_SCRATCH_BYTES or len(source) % 0x4000:
        raise ValueError('scratch image size is outside the permitted bound')
    if source[32:36] != b'NXSB' or struct.unpack('<IQ', source[36:48]) != (4096, len(source) // 4096):
        raise ValueError('expected an unwrapped APFS scratch container')
    if source[:8] != checksum(source[:4096]):
        raise ValueError('invalid container superblock checksum')


def volume_defect(block, name, roles):
    """Why `block` is not a fresh, empty, unencrypted volume `name` whose role is in `roles`; None if it is."""
    if block[:8] != checksum(block):
        return 'volume superblock checksum is invalid'
    if struct.unpack_from('<I', block, 24)[0] != 13:
        return 'block is not a filesystem object'
    role = struct.unpack_from('<H', block, 964)[0]
    if role not in roles:
        return f'role {role:#x} is not one of {", ".join(hex(r) for r in sorted(roles))}'
    if block[704:960].split(b'\0', 1)[0] != name.encode():
        return 'volume name differs'
    if any(struct.unpack_from('<5Q', block, 184)):
        return 'volume is not empty (used object counters)'
    if struct.unpack_from('<Q', block, 264)[0] != 1:
        return 'volume is encrypted or carries unexpected flags'
    return None


def set_role(block, role):
    block = bytearray(block)
    struct.pack_into('<H', block, 964, role)
    block[:8] = checksum(block)
    return bytes(block)


def make_xart(source):
    check_container(source)
    volumes = []
    for offset in range(0, len(source), 4096):
        if source[offset + 32:offset + 36] != b'APSB':
            continue
        block = source[offset:offset + 4096]
        if volume_defect(block, 'Snapintosh-xART', (0,)):
            raise ValueError('input is not the expected empty, unencrypted scratch volume')
        volumes.append(offset)
    if len(volumes) != 1:
        raise ValueError('expected exactly one freshly formatted volume superblock')
    result = bytearray(source)
    offset = volumes[0]
    struct.pack_into('<H', result, offset + 964, 0x100)
    result[offset:offset + 8] = checksum(result[offset:offset + 4096])
    return result, offset


def volume_superblock_offsets(source):
    found, at = [], source.find(b'APSB')
    while at != -1:
        if at % 4096 == 32:
            found.append(at - 32)
        at = source.find(b'APSB', at + 1)
    return found


def make_three_role(source):
    """Return ({offset: 4 KiB block to write}, [volume facts]) for a fresh three-volume container.

    `source` is bytes-like with slicing, len and find (bytes or FileImage); it
    is never modified. A volume the formatter already stamped with its own
    role is accepted and left byte-identical, any other nonzero role is refused.
    """
    check_container(source)
    if struct.unpack('<I', source[180:184])[0] < len(THREE_ROLE_VOLUMES):
        raise ValueError('container allows fewer than three volumes (nx_max_file_systems)')
    offsets = volume_superblock_offsets(source)
    if len(offsets) != len(THREE_ROLE_VOLUMES):
        raise ValueError(f'expected exactly three freshly formatted volume superblocks, found {len(offsets)}')
    wanted = {name: role for name, role, _ in THREE_ROLE_VOLUMES}
    patches, volumes, seen = {}, [], set()
    for offset in offsets:
        block = bytes(source[offset:offset + 4096])
        name = block[704:960].split(b'\0', 1)[0].decode('latin-1')
        if name not in wanted:
            raise ValueError(f'unexpected volume name {name!r}')
        if name in seen:
            raise ValueError(f'duplicate volume {name!r}')
        seen.add(name)
        defect = volume_defect(block, name, (0, wanted[name]))
        if defect:
            raise ValueError(f'{name}: {defect}')
        stamped = struct.unpack_from('<H', block, 964)[0] == 0
        if stamped:
            patches[offset] = set_role(block, wanted[name])
        volumes.append(dict(name=name, role=wanted[name], superblock_offset=offset,
                            by='stamp' if stamped else 'formatter'))
    return patches, volumes


class FileImage:
    """Read-only bytes-like view of an open file; only one window is ever in memory."""

    def __init__(self, stream):
        self.fd = stream.fileno()
        self.size = os.fstat(self.fd).st_size

    def __len__(self):
        return self.size

    def __getitem__(self, key):
        start, stop, step = key.indices(self.size)
        if step != 1:
            raise ValueError('only contiguous slices')
        data = bytearray()
        while len(data) < stop - start:
            piece = os.pread(self.fd, min(WINDOW, stop - start - len(data)), start + len(data))
            if not piece:
                raise ValueError('image shrank while it was read')
            data += piece
        return bytes(data)

    def find(self, needle, start=0):
        at = start
        while at < self.size:
            window = self[at:at + WINDOW + len(needle) - 1]
            found = window.find(needle)
            if found >= 0:
                return at + found
            at += WINDOW
        return -1


def sha256_of(image):
    digest = hashlib.sha256()
    for at in range(0, len(image), WINDOW):
        digest.update(image[at:at + WINDOW])
    return digest.hexdigest()


def write_patched(image, output, patches):
    """Write `image` to the new file `output` with 4 KiB block `patches` applied; return its SHA-256.

    All-zero windows stay holes in the output, so a mostly empty image of up
    to 2 GiB costs little disk.
    """
    digest = hashlib.sha256()
    stream = output.open('xb')  # an existing file raises here and is never touched
    try:
        with stream:
            for at in range(0, len(image), WINDOW):
                window = image[at:at + WINDOW]
                hits = [offset for offset in patches if at <= offset < at + len(window)]
                if hits:
                    window = bytearray(window)
                    for offset in hits:
                        window[offset - at:offset - at + 4096] = patches[offset]
                digest.update(window)
                if window.count(0) == len(window):
                    stream.seek(len(window), 1)
                else:
                    stream.write(window)
            stream.truncate(len(image))
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    return digest.hexdigest()


def readback_roles(path):
    """{role: name} as the ISC checker of the speed test reads it."""
    sys.path.insert(0, str(ROOT / 'tools'))
    try:
        from cycle_lib import speed_test
    finally:
        sys.path.pop(0)
    try:
        return speed_test.isc_roles(path)
    except Exception as error:  # the checker's own refusal type is cycle_lib.common.Refused
        raise ValueError(f'speed_test.isc_roles refused the output: {error}') from error


def stamp_three_role(source, output, source_sha256):
    source, output = Path(source), Path(output)
    if not source.is_file() or source.is_symlink():
        raise ValueError('source must be a regular scratch image file')
    if not 0x4000 <= source.stat().st_size <= MAX_SCRATCH_BYTES:
        raise ValueError('scratch image size is outside the permitted bound')
    with source.open('rb') as stream:
        image = FileImage(stream)
        digest = sha256_of(image)
        if digest != source_sha256:
            raise ValueError('fresh source image does not match the explicitly supplied SHA256')
        patches, volumes = make_three_role(image)
        written = write_patched(image, output, patches)
        if sha256_of(image) != digest:
            output.unlink(missing_ok=True)
            raise ValueError('source image changed while it was stamped')
        size = len(image)
    try:
        roles = readback_roles(output)
        want = {role: name for name, role, _ in THREE_ROLE_VOLUMES}
        if roles != want:
            raise ValueError(f'read-back roles {sorted(roles)} are not {sorted(want)}')
        with output.open('rb') as stream:
            if sha256_of(FileImage(stream)) != written:
                raise ValueError('the written output does not read back as written')
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    return dict(source=str(source), source_sha256=digest, output=str(output), bytes=size,
                sha256=written, volumes=volumes,
                roles_read_back={str(role): name for role, name in sorted(roles.items())},
                sep_trusted_storage=False)


class ScratchError(RuntimeError):
    pass


def _tool(*command):
    try:
        return subprocess.run(command, capture_output=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ScratchError(f'{command[0]}: {error}') from error


def _said(result):
    return (result.stderr or result.stdout).decode(errors='replace').strip()


def scratch_attachment(image):
    """(raw node, APFS container node or None, bytes) of `image` as hdiutil lists it; None if detached."""
    result = _tool('hdiutil', 'info', '-plist')
    if result.returncode:
        raise ScratchError('hdiutil info failed: ' + _said(result))
    for entry in plistlib.loads(result.stdout).get('images', []):
        if os.path.realpath(entry.get('image-path', '')) != os.path.realpath(image):
            continue
        nodes = entry.get('system-entities', [])
        # The whole-disk node has no content hint; the volumes of an attached
        # container are listed too, and are not ours to hand to a formatter.
        raw = [n['dev-entry'] for n in nodes if not n.get('content-hint')]
        container = [n['dev-entry'] for n in nodes if n.get('content-hint', '') == APFS_CONTAINER_GUID]
        if len(raw) != 1 or len(container) > 1:
            raise ScratchError(f'unexpected device nodes for the scratch image: {nodes}')
        return raw[0], (container[0] if container else None), entry['blockcount'] * entry['blocksize']
    return None


def attach_scratch(image, size):
    # -nomount: nothing may appear under /Volumes; the raw class stops hdiutil
    # looking for a partition map in a file that is one bare container.
    result = _tool('hdiutil', 'attach', '-nomount', '-noverify', '-noautofsck',
                   '-imagekey', 'diskimage-class=CRawDiskImage', str(image))
    if result.returncode:
        raise ScratchError('hdiutil attach failed: ' + _said(result))
    attached = scratch_attachment(image)
    if attached is None or attached[2] != size:
        raise ScratchError('the attached image is not the scratch image just created')
    return attached[0]


def detach_scratch(image):
    """Detach `image` by the node hdiutil lists for that image alone, and prove it is gone."""
    attached = scratch_attachment(image)
    if attached is None:
        return
    for flags in ((), ('-force',)):
        _tool('hdiutil', 'detach', *flags, attached[0])
        if scratch_attachment(image) is None:
            return
    raise ScratchError(f'scratch image is still attached as {attached[0]}')


def run_newfs(image, size, node, *args):
    attached = scratch_attachment(image)
    if attached is None or attached[2] != size or node not in attached[:2]:
        raise ScratchError(f'refusing newfs_apfs on {node}: not a node of the attached scratch image')
    return _tool('newfs_apfs', *args, node)


def add_volumes(image, size, container):
    made = []
    for name, role, letter in THREE_ROLE_VOLUMES:
        result = run_newfs(image, size, container, '-A', '-v', name, '-R', letter)
        refused = None
        if result.returncode:
            refused = f'newfs_apfs -R {letter} exit {result.returncode}: {_said(result)}'
            result = run_newfs(image, size, container, '-A', '-v', name)
            if result.returncode:
                raise ScratchError(f'newfs_apfs could not add {name}: {_said(result)}')
        made.append(dict(name=name, role=role, formatter_refused=refused))
    return made


def in_build(path):
    parent = Path(path).parent.resolve()
    return (ROOT / 'build').resolve() in (parent, *parent.parents) and not Path(path).is_symlink()


def check_three_role_size(size):
    if not MIN_THREE_ROLE_BYTES <= size <= MAX_SCRATCH_BYTES or size % 0x4000:
        raise ValueError(f'container size must be a 16 KiB multiple from {MIN_THREE_ROLE_BYTES} to '
                         f'{MAX_SCRATCH_BYTES} bytes (more than 1 GiB for three volumes)')


def format_scratch(path, size):
    """Create `path`, a new sparse file under build/, as a fresh container of the three volumes.

    macOS tools run only on the nodes of this one attached image, which is
    detached again and proven gone before this returns or raises.
    """
    path = Path(path)
    if not in_build(path):
        raise ValueError('scratch image must be a new file under build/')
    check_three_role_size(size)
    if sys.platform != 'darwin':
        raise ScratchError('formatting needs the macOS hdiutil and newfs_apfs')
    with path.open('xb') as stream:  # an existing file raises here and is never touched
        stream.truncate(size)
    failure, made = None, None
    try:
        node = attach_scratch(path, size)
        result = run_newfs(path, size, node, '-C')
        if result.returncode:
            raise ScratchError('newfs_apfs -C failed: ' + _said(result))
        container = None
        for _ in range(40):
            attached = scratch_attachment(path)
            container = attached and attached[1]
            if container:
                break
            time.sleep(0.5)
        if not container:
            raise ScratchError('the APFS container node did not appear')
        made = add_volumes(path, size, container)
    except BaseException as error:
        failure = error
    try:
        detach_scratch(path)
    except ScratchError as error:
        if failure is None:
            failure = error
        else:
            failure = ScratchError(f'{failure}; and: {error}')
    if failure is not None:
        if scratch_attachment(path) is None:
            path.unlink(missing_ok=True)
        raise failure
    with path.open('rb') as stream:
        image = FileImage(stream)
        make_three_role(image)  # fail closed unless it is the fresh three-volume container
        digest = sha256_of(image)
    return dict(scratch=str(path), bytes=size, sha256=digest, formatter=made)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('source', type=Path, nargs='?')
    ap.add_argument('output', type=Path, nargs='?')
    ap.add_argument('--source-sha256')
    ap.add_argument('--three-role', action='store_true')
    ap.add_argument('--format-scratch', type=Path, metavar='SCRATCH')
    ap.add_argument('--build-three-role', type=Path, metavar='OUTPUT')
    ap.add_argument('--size-mib', type=int, default=DEFAULT_SIZE_MIB)
    args = ap.parse_args(argv)
    modes = [args.three_role, args.format_scratch is not None, args.build_three_role is not None]
    if sum(modes) > 1:
        ap.error('--three-role, --format-scratch and --build-three-role are exclusive')
    if modes[1] or modes[2]:
        if args.source or args.output or args.source_sha256:
            ap.error('this mode takes no source, output or --source-sha256')
        try:
            if modes[1]:
                print(json.dumps(format_scratch(args.format_scratch, args.size_mib << 20), indent=2))
                return
            output = args.build_three_role
            if not in_build(output):
                raise ValueError('output must be a new file under build/')
            if output.exists():
                raise ValueError('output exists')
            scratch = output.with_name(output.name + '.scratch')
            fresh = format_scratch(scratch, args.size_mib << 20)
            result = stamp_three_role(scratch, output, fresh['sha256'])
        except (ValueError, ScratchError, FileExistsError) as error:
            ap.error(str(error))
        print(json.dumps(dict(result, formatter=fresh['formatter']), indent=2))
        return
    missing = [name for name, value in (('source', args.source), ('output', args.output),
                                        ('--source-sha256', args.source_sha256)) if value is None]
    if missing:
        ap.error('the following arguments are required: ' + ', '.join(missing))
    if args.three_role:
        try:
            result = stamp_three_role(args.source, args.output, args.source_sha256)
        except (ValueError, FileExistsError) as error:
            ap.error(str(error))
        print(json.dumps(result, indent=2))
        return
    if not args.source.is_file() or args.source.is_symlink():
        ap.error('source must be a regular scratch image file')
    if args.source.stat().st_size > MAX_SCRATCH_BYTES:
        ap.error('source exceeds scratch image size limit')
    source = args.source.read_bytes()
    digest = hashlib.sha256(source).hexdigest()
    if digest != args.source_sha256:
        ap.error('fresh source image does not match the explicitly supplied SHA256')
    result, offset = make_xart(source)
    with args.output.open('xb') as stream:
        stream.write(result)
    print(json.dumps(dict(source=str(args.source), source_sha256=digest,
                          output=str(args.output), bytes=len(result),
                          sha256=hashlib.sha256(result).hexdigest(),
                          superblock_offset=offset, role=256,
                          sep_trusted_storage=False), indent=2))


if __name__ == '__main__':
    main()
