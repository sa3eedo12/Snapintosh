#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The SP11 window: its layout and its 4 KiB header, for the offline writer (docs/SP11-STORAGE-PROVISIONING.md). EXPERIMENTAL.

The window is the one GPT partition the operator creates with Windows' own partition manager (decision D3, plan WS-D). Its layout is the
A16 flow's (docs/FULL-INSTALL.md, "The window: what the guest sees"; tools/snapmac-window-header.py), with the disk-specific numbers made
parameters:

    +0          the first MiB: the monitor synthesizes the GPT it shows macOS; nothing here is read from media
    +512 KiB    the 4 KiB header, written LAST (state 1 means every byte before it was written and read back)
    +1 MiB      the ISC region (512 MiB on the A16; a parameter here)
    +1 MiB + ISC region   the container region, up to the last MiB
    last MiB    reserved: the monitor synthesizes the backup GPT

tools/test-snapmac-image-sp11.py (class Header) builds the header here and with the A16 script on the A16's numbers and compares the bytes,
so the two cannot drift. Evidence: EXPERIMENTAL (host only); hardware NOT RUN."""
import struct
import uuid
import zlib

MIB = 1 << 20
HEADER_BYTES = 4096
HEADER_OFFSET = 512 << 10
ISC_OFFSET = MIB
DEFAULT_ISC_REGION = 512 * MIB
VIRTUAL_BLOCK = 4096
MAGIC = b'SNAPWIN1'
VERSION = 1
HEADER_FORMAT = '<8sII16sQQII6Q32s32s32s32s'
STATE_PROVISIONED = 1


class WindowError(ValueError):
    pass


def layout(partition_offset, partition_bytes, isc_region=DEFAULT_ISC_REGION):
    """The window's geometry for a partition at `partition_offset` of `partition_bytes` (both multiples of 16 KiB)."""
    for name, value in (('partition offset', partition_offset), ('partition size', partition_bytes), ('ISC region', isc_region)):
        if not isinstance(value, int) or value <= 0 or value % 16384:
            raise WindowError(f'the {name} must be a positive multiple of 16 KiB')
    container_offset = ISC_OFFSET + isc_region
    container_region = partition_bytes - MIB - container_offset
    if container_region <= 0:
        raise WindowError('the partition is too small for the first MiB, the ISC region, a container and the last MiB')
    return dict(partition_offset=partition_offset, partition_bytes=partition_bytes, header_offset=HEADER_OFFSET, isc_offset=ISC_OFFSET,
                isc_region=isc_region, container_offset=container_offset, container_region=container_region,
                reserved_tail_offset=partition_bytes - MIB)


def build_header(lay, partition_guid, isc_bytes, isc_sha256, container_bytes, container_sha256, manifest_sha256, stamp):
    """The 4096-byte header (little-endian, CRC-32 of bytes 0..4091 in the last four): the layout of tools/snapmac-window-header.py."""
    if not 0 < isc_bytes <= lay['isc_region'] or isc_bytes % 4096:
        raise WindowError('the ISC image does not fit its region, or is not whole 4 KiB blocks')
    if not 0 < container_bytes <= lay['container_region']:
        raise WindowError('the container does not fit its region')
    stamp = stamp.encode('ascii') if isinstance(stamp, str) else stamp
    if len(stamp) > 32:
        raise WindowError('the timestamp is at most 32 characters')
    first = lay['partition_offset'] // 512
    last = (lay['partition_offset'] + lay['partition_bytes']) // 512 - 1
    body = struct.pack(HEADER_FORMAT, MAGIC, VERSION, HEADER_BYTES, uuid.UUID(partition_guid).bytes_le, first, last, VIRTUAL_BLOCK,
                       STATE_PROVISIONED, lay['isc_offset'], lay['isc_region'], isc_bytes, lay['container_offset'], lay['container_region'],
                       container_bytes, bytes.fromhex(isc_sha256), bytes.fromhex(container_sha256), bytes.fromhex(manifest_sha256), stamp)
    header = body + bytes(HEADER_BYTES - 4 - len(body))
    header += struct.pack('<I', zlib.crc32(header))
    assert len(header) == HEADER_BYTES
    return header


def parse_header(data):
    """The fields of a header, or WindowError when it is not one (size, CRC, magic, version). The state is returned, not judged."""
    if len(data) != HEADER_BYTES:
        raise WindowError(f'a header is {HEADER_BYTES} bytes')
    if zlib.crc32(data[:-4]) != struct.unpack('<I', data[-4:])[0]:
        raise WindowError('the header fails its CRC')
    f = struct.unpack(HEADER_FORMAT, data[:struct.calcsize(HEADER_FORMAT)])
    if f[0] != MAGIC or f[1] != VERSION or f[2] != HEADER_BYTES:
        raise WindowError('not a window header')
    keys = ('magic', 'version', 'header_bytes', 'guid', 'first_lba', 'last_lba', 'virtual_block', 'state', 'isc_offset', 'isc_region',
            'isc_bytes', 'container_offset', 'container_region', 'container_bytes', 'isc_sha256', 'container_sha256', 'manifest_sha256',
            'stamp')
    out = dict(zip(keys, f))
    out['guid'] = str(uuid.UUID(bytes_le=out['guid']))
    for k in ('isc_sha256', 'container_sha256', 'manifest_sha256'):
        out[k] = out[k].hex()
    out['stamp'] = out['stamp'].rstrip(b'\x00').decode('ascii')
    return out
