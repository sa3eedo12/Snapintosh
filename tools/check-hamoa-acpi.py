#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Hold a Surface Pro 11 ACPI dump to a board descriptor, offline and read-only.

    python3 tools/check-hamoa-acpi.py DUMP_DIR [--descriptor config/boards/sp11.json] [--run RUNnn]

DUMP_DIR is what the UEFI probe wrote to its USB stick: a run directory holding RSDP.bin, XSDT.bin, APIC.bin (the
MADT) and MCFG.bin, or a directory holding exactly one RUNnn child (several need --run). memmap.txt and summary.txt
beside the tables are context for people and are not read.

probes/bootcontract.py does the checking: every table is structure-checked, then each fact the descriptor also
states (GIC distributor, redistributors, ITS, CPU MPIDRs, ECAM windows) must match exactly. The shipped sp11.json is a
template with unknown fields, so it is refused (descriptor-unimplemented) until a measured descriptor exists; what
the dump states is still printed first, so a first real dump can be read before that descriptor is written.

ACCEPTED means the tables agree with the descriptor. It does not mean the board can boot: the descriptor's own
readiness gate is reported on its own line.

This tool never writes. It opens regular files for reading and nothing else: a directory, FIFO, device or link
where a table should be is refused before it is opened.

Exit status: 0 accepted, 1 refused, 2 usage or unreadable input. Evidence: EXPERIMENTAL (synthetic tables only).
"""
import argparse
import os
import re
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _dir in (ROOT / 'tools', ROOT / 'probes'):
    if str(_dir) not in sys.path:
        sys.path.insert(0, str(_dir))
import board_descriptor as bd  # noqa: E402
import bootcontract as bc  # noqa: E402

# The UEFI probe names each table by its signature; the RSDP has none of four characters, so it is RSDP.bin.
TABLE_FILES = (('RSDP', 'RSDP.bin'), ('XSDT', 'XSDT.bin'), ('APIC', 'APIC.bin'), ('MCFG', 'MCFG.bin'))
RUN_DIR = re.compile(r'RUN[0-9]+\Z')
DEFAULT_DESCRIPTOR = ROOT / 'config' / 'boards' / 'sp11.json'


class UsageError(Exception):
    """The inputs could not be read at all (not the same as a dump that was read and refused)."""


def run_name(text):
    if not RUN_DIR.match(text):
        raise argparse.ArgumentTypeError(f'{text!r} is not a RUNnn directory name')
    return text


def resolve_run_dir(dump_dir, run):
    if not dump_dir.is_dir():
        raise UsageError('DUMP_DIR is not a directory')
    if run is not None:
        chosen = dump_dir / run
        if not chosen.is_dir():
            raise UsageError(f'{run} is not a directory inside DUMP_DIR')
        return chosen
    if any(os.path.lexists(dump_dir / name) for _, name in TABLE_FILES):
        return dump_dir
    try:
        children = sorted(entry for entry in dump_dir.iterdir() if RUN_DIR.match(entry.name) and entry.is_dir())
    except OSError as exc:
        raise UsageError(f'cannot list DUMP_DIR: {exc.strerror}') from exc
    if not children:
        raise UsageError('DUMP_DIR holds no ACPI table files and no RUNnn directory')
    if len(children) > 1:
        raise UsageError('DUMP_DIR holds several run directories (' + ', '.join(c.name for c in children)
                         + '); choose one with --run')
    return children[0]


def read_table(path):
    """The bytes of one table file, never more than the table cap.

    The path is inspected before it is opened: opening a device node or a terminal can act on the device, so only a
    regular file that is not itself a link is ever opened. The descriptor is inspected again after the open and must
    be the same file, because the name can be replaced in between (a FIFO would otherwise hang the read).
    """
    try:
        listed = os.lstat(path)
    except OSError as exc:
        raise UsageError(f'cannot open {path.name}: {exc.strerror}') from exc
    if not stat.S_ISREG(listed.st_mode):
        raise UsageError(f'{path.name} is not a regular file')
    if listed.st_size > bc.ACPI_MAX_TABLE_BYTES:
        raise bc.BootContractError('table-length', f'{path.name} is larger than the {bc.ACPI_MAX_TABLE_BYTES}-byte '
                                   'table cap')
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | os.O_NONBLOCK)
    except OSError as exc:
        raise UsageError(f'cannot open {path.name}: {exc.strerror}') from exc
    try:
        # Wrapping the descriptor in a file object first would fail on a directory with an unrelated error.
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (listed.st_dev, listed.st_ino):
            raise UsageError(f'{path.name} is not a regular file')
        with os.fdopen(fd, 'rb', closefd=False) as handle:
            data = handle.read(bc.ACPI_MAX_TABLE_BYTES + 1)
    finally:
        os.close(fd)
    if len(data) > bc.ACPI_MAX_TABLE_BYTES:
        raise bc.BootContractError('table-length', f'{path.name} is larger than the {bc.ACPI_MAX_TABLE_BYTES}-byte '
                                   'table cap')
    return data


def load_tables(run_dir):
    tables = {}
    for key, filename in TABLE_FILES:
        path = run_dir / filename
        if os.path.lexists(path):
            tables[key] = read_table(path)
    return tables


def load_board(path):
    path = Path(path)
    if not path.is_file():
        raise UsageError('the descriptor is not a readable file')
    try:
        return bd.load(path)
    except bd.BoardDescriptorError as exc:
        raise bc.BootContractError('descriptor-inconsistent', f'the descriptor does not validate: {exc}') from exc


def boot_gate_line(board):
    try:
        bd.require_boot_ready(board)
    except bd.BoardNotBootReady as exc:
        return f'descriptor boot gate : NOT READY ({exc})'
    return 'descriptor boot gate : ready'


def main(argv=None):
    parser = argparse.ArgumentParser(prog='check-hamoa-acpi.py', description='Check an ACPI dump against a board '
                                     'descriptor (read-only).')
    parser.add_argument('dump_dir', type=Path)
    parser.add_argument('--descriptor', default=str(DEFAULT_DESCRIPTOR),
                        help='board descriptor JSON (default: config/boards/sp11.json)')
    parser.add_argument('--run', type=run_name, help='run directory to read when DUMP_DIR holds several')
    args = parser.parse_args(argv)
    try:
        run_dir = resolve_run_dir(args.dump_dir, args.run)
        board = None
        refusal = None
        try:
            board = load_board(args.descriptor)
        except bc.BootContractError as exc:
            refusal = exc
        tables = load_tables(run_dir)
    except UsageError as exc:
        print(f'check-hamoa-acpi: {exc}', file=sys.stderr)
        return 2
    except bc.BootContractError as exc:
        print(f'REFUSED: {exc}')
        return 1

    print(f'ACPI tables read from {run_dir.name}: ' + ', '.join(key for key, _ in TABLE_FILES if key in tables))
    try:
        facts = bc.parse_hamoa_tables(tables)
    except bc.BootContractError as exc:
        print(f'REFUSED: {exc}')
        return 1
    print('observed in the dump (not yet confirmed against anything):')
    for line in bc.describe_acpi_facts(facts):
        print(f'  {line}')
    if refusal is not None:
        print(f'REFUSED: {refusal}')
        return 1
    print(f'descriptor          : {board.board_id} (status {board.status})')
    print(boot_gate_line(board))
    try:
        agreed = bc.check_hamoa_platform(board, tables)
    except bc.BootContractError as exc:
        print(f'REFUSED: {exc}')
        return 1
    print(f'ACCEPTED: the tables agree with descriptor {board.board_id}: GICv{agreed.gic_version}, '
          f'{len(agreed.mpidrs)} CPUs, {len(agreed.ecam)} ECAM window(s)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
