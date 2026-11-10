#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Reader and checker for the ath12k board container (`board-2.bin`) that SP11 Wi-Fi staging depends on.

It answers one question before any hardware is involved: given the identifiers a WCN7850 reports (PCI vendor, device,
subsystem vendor and device, and the QMI chip and board ids), does a staged `board-2.bin` hold a board-data entry for
that module, and which regulatory-database entry does it carry? Nothing is sent to a device and nothing is written.

Format facts (read from Linux ath12k at commit 7475ea8b58616b33c04afb976cf0fbaad9dd63fb, `core.c:432-541`,
`core.c:557-660` and `hw.h:93,304-319`; BSD-3-Clause-Clear; no code or text is copied):

    container = magic "QCA-ATH12K-BOARD" + NUL, padded with zeros to a multiple of 4, then top-level elements
    element   = u32 id, u32 length (both little endian), `length` bytes, padded to a multiple of 4
    top id 0  = a board entry, id 1 = a regulatory-database entry; each holds sub-elements of the same shape:
                id 0 = a name (ASCII, no NUL), id 1 = the data; one or more names, then the data

Failure modes, written before the code (the tests in tools/test-sp11-wifi-board.py hold every one):
  F1. A file that is not a container, or is cut short, is taken for an empty or partial one. Refused: the magic must be
      present with its NUL, every length must fit in what remains, and nothing may trail the last element.
  F2. A length that overruns its parent, including 0xffffffff and a padded length that no longer fits, reads beyond the
      element or wraps. Refused before any slice is taken.
  F3. An element the driver would skip (an unknown id, a name without data, two data blocks, data before a name, an
      empty or non-printable name) is accepted here and later mismatches the driver. Refused: the driver warns and
      skips these, so a file that has one is not what a reader of the driver's source expects.
  F4. Two entries with one name make the lookup depend on order. Refused; the driver takes the first match.
  F5. A name that differs from the driver's by one character matches. The match is exact on the whole string, as the
      driver compares length and then bytes.
  F6. A truncated prefix of a valid file parses as something other than a prefix of its entries. Tested as a property.

EXPERIMENTAL: the lookup order below mirrors the pinned driver and has never been compared with a running device.
"""
import argparse
import hashlib
import struct
import sys
from dataclasses import dataclass

MAGIC = b'QCA-ATH12K-BOARD\0'
HEADER = 8
BOARD, REGDB = 0, 1                       # top-level element ids (ath12k_bd_ie_type)
NAME, DATA = 0, 1                         # sub-element ids (ath12k_bd_ie_board_type, ath12k_bd_ie_regdb_type)
KINDS = {BOARD: 'board', REGDB: 'regdb'}
BUS_ONLY_NAME = 'bus=pci'                 # the regulatory entry's own name in the pinned linux-firmware containers
MAX_ID = 0x7fffffff                       # the driver prints the QMI chip and board ids with %d


class BoardContainerError(ValueError):
    """The container or an argument is unusable; the message names the first violated rule."""


@dataclass(frozen=True)
class Entry:
    kind: str                             # 'board' or 'regdb'
    names: tuple
    data: bytes

    @property
    def sha256(self):
        return hashlib.sha256(self.data).hexdigest()


def _align4(n):
    return (n + 3) & ~3


def _elements(buf, start, end, where):
    """Yield (id, body) for each element in buf[start:end]; the whole span must be consumed."""
    pos = start
    while pos < end:
        if end - pos < HEADER:
            raise BoardContainerError(f'{where}: {end - pos} trailing byte(s) cannot hold an element header')
        ident, length = struct.unpack_from('<II', buf, pos)
        pos += HEADER
        if end - pos < _align4(length):
            raise BoardContainerError(f'{where}: element {ident} claims {length} bytes, {end - pos} remain')
        yield ident, buf[pos:pos + length]
        pos += _align4(length)


def _entry(kind, body, index):
    names, data = [], None
    for ident, sub in _elements(body, 0, len(body), f'entry {index}'):
        if ident == NAME and data is None:
            if not sub or any(b < 0x20 or b > 0x7e for b in sub):
                raise BoardContainerError(f'entry {index}: a name is empty or not printable ASCII')
            names.append(sub.decode('ascii'))
        elif ident == DATA and data is None:
            data = bytes(sub)
        else:
            raise BoardContainerError(f'entry {index}: sub-element {ident} is unknown, repeated or out of order')
    if not names or data is None:
        raise BoardContainerError(f'entry {index}: needs at least one name followed by one data block')
    return Entry(kind, tuple(names), data)


def parse(blob):
    """All entries of a board container, in file order. Raises BoardContainerError on any violated rule."""
    if not isinstance(blob, (bytes, bytearray)):
        raise BoardContainerError('the container must be bytes')
    blob = bytes(blob)
    if not blob.startswith(MAGIC):
        raise BoardContainerError('missing the QCA-ATH12K-BOARD magic and its NUL')
    start = _align4(len(MAGIC))
    if len(blob) < start:
        raise BoardContainerError('the file ends inside the padding after the magic')
    entries, seen = [], set()
    for index, (ident, body) in enumerate(_elements(blob, start, len(blob), 'container')):
        if ident not in KINDS:
            raise BoardContainerError(f'top-level element {index} has unknown id {ident}')
        entry = _entry(KINDS[ident], body, index)
        for name in entry.names:
            if (entry.kind, name) in seen:
                raise BoardContainerError(f'{entry.kind} name {name!r} appears twice')
            seen.add((entry.kind, name))
        entries.append(entry)
    return entries


def find(entries, kind, name):
    """The first entry of `kind` that carries exactly `name`, or None."""
    for entry in entries:
        if entry.kind == kind and name in entry.names:
            return entry
    return None


def _field(value, limit, what):
    if type(value) is not int or not 0 <= value <= limit:
        raise BoardContainerError(f'{what} must be an integer in 0..{limit:#x}')
    return value


def board_name(vendor, device, subsystem_vendor, subsystem_device, chip_id, board_id, variant=None):
    """The name ath12k builds for a PCI Wi-Fi 7 device (the BUS_AND_BOARD search used by WCN7850 and QCC2072)."""
    parts = [f'bus=pci,vendor={_field(vendor, 0xffff, "vendor"):04x},device={_field(device, 0xffff, "device"):04x}',
             f'subsystem-vendor={_field(subsystem_vendor, 0xffff, "subsystem vendor"):04x}',
             f'subsystem-device={_field(subsystem_device, 0xffff, "subsystem device"):04x}',
             f'qmi-chip-id={_field(chip_id, MAX_ID, "chip id")}', f'qmi-board-id={_field(board_id, MAX_ID, "board id")}']
    name = ','.join(parts)
    if variant is not None:
        if not variant or len(variant) >= 0x20 or any(ord(c) < 0x21 or ord(c) > 0x7e or c == ',' for c in variant):
            raise BoardContainerError('a calibration variant is 1 to 31 printable characters without a comma')
        name += f',variant={variant}'
    return name


def lookup_board(entries, *ids, variant=None):
    """(entry, name used) in the driver's order: the full name with the variant, then the same name without it."""
    tried = [board_name(*ids, variant=variant)]
    if variant is not None:
        tried.append(board_name(*ids))
    for name in tried:
        entry = find(entries, 'board', name)
        if entry:
            return entry, name
    return None, tried[-1]


def lookup_regdb(entries, *ids, variant=None):
    """(entry, name used) in the driver's order: the full board name, then the bus-only name."""
    for name in (board_name(*ids, variant=variant), BUS_ONLY_NAME):
        entry = find(entries, 'regdb', name)
        if entry:
            return entry, name
    return None, BUS_ONLY_NAME


def _hex(text):
    return int(text, 16)


def main(argv=None):
    ap = argparse.ArgumentParser(description='Inspect an ath12k board-2.bin container (read-only).')
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name in ('list', 'find', 'regdb'):
        p = sub.add_parser(name)
        p.add_argument('file')
        if name != 'list':
            p.add_argument('--vendor', type=_hex, required=True, help='PCI vendor id, hex')
            p.add_argument('--device', type=_hex, required=True, help='PCI device id, hex')
            p.add_argument('--subsystem-vendor', type=_hex, required=True, help='hex')
            p.add_argument('--subsystem-device', type=_hex, required=True, help='hex')
            p.add_argument('--chip-id', type=int, required=True, help='QMI chip id, decimal')
            p.add_argument('--board-id', type=int, required=True, help='QMI board id, decimal')
            p.add_argument('--variant', default=None, help='calibration variant from the device tree or SMBIOS')
    args = ap.parse_args(argv)
    try:
        with open(args.file, 'rb') as stream:
            entries = parse(stream.read())
    except (OSError, BoardContainerError) as error:
        print(f'UNUSABLE: {error}', file=sys.stderr)
        return 2
    if args.cmd == 'list':
        for entry in entries:
            print(f'{entry.kind} data={len(entry.data)} sha256={entry.sha256} names={len(entry.names)}')
            for name in entry.names:
                print(f'  {name}')
        return 0
    ids = (args.vendor, args.device, args.subsystem_vendor, args.subsystem_device, args.chip_id, args.board_id)
    try:
        entry, name = (lookup_board if args.cmd == 'find' else lookup_regdb)(entries, *ids, variant=args.variant)
    except BoardContainerError as error:
        print(f'UNUSABLE: {error}', file=sys.stderr)
        return 2
    if entry is None:
        print(f'NO ENTRY: {args.cmd} {name}')
        return 1
    print(f'FOUND: {entry.kind} {name} data={len(entry.data)} sha256={entry.sha256}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
