#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests for how tools/check-hamoa-acpi.py reads a table file: the path is inspected before it is opened.

The checker used to open the path (non-blocking) and look at the descriptor afterwards, so a device node or terminal
reached through a symbolic link was opened first and only then refused. Opening such a node can act on the device.
It now looks at the path first and only opens a regular file that is not itself a link; the opened descriptor must be
that same file. Synthetic tables only (the dump fixtures are those of tools/test-hamoa-bootcontract.py); nothing here
reads a device or touches hardware.

    python3 tools/test-check-hamoa-acpi-hardening.py
"""
from pathlib import Path
from unittest import mock
import importlib.util
import os
import shutil
import sys
import unittest

sys.dont_write_bytecode = True
TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent

spec = importlib.util.spec_from_file_location('bootcontract_fixtures', TOOLS / 'test-hamoa-bootcontract.py')
B = importlib.util.module_from_spec(spec)
spec.loader.exec_module(B)
cli, bc = B.cli, B.bc

SCRATCH = ROOT / 'build' / 'test-check-hamoa-acpi-hardening'


class ReadTable(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.plain, self.other = SCRATCH / 'APIC.plain', SCRATCH / 'APIC.other'
        self.link, self.link_to_fifo, self.fifo = SCRATCH / 'APIC.link', SCRATCH / 'FIFO.link', SCRATCH / 'APIC.fifo'
        for path in (self.link, self.link_to_fifo, self.fifo):
            path.unlink(missing_ok=True)
        self.plain.write_bytes(b'0123456789')
        self.other.write_bytes(b'abcdefghij')
        self.link.symlink_to(self.plain)
        os.mkfifo(self.fifo)
        self.link_to_fifo.symlink_to(self.fifo)

    def never_opened(self, path, error, pattern):
        with mock.patch.object(cli.os, 'open', side_effect=AssertionError('opened before its type was known')):
            with self.assertRaisesRegex(error, pattern):
                cli.read_table(path)

    def test_nothing_that_is_not_a_regular_file_is_opened(self):
        for path in (self.fifo, SCRATCH, self.link, self.link_to_fifo):
            with self.subTest(path=path.name):
                self.never_opened(path, cli.UsageError, 'not a regular file')

    def test_an_oversize_table_is_refused_before_it_is_opened(self):
        with mock.patch.object(bc, 'ACPI_MAX_TABLE_BYTES', self.plain.stat().st_size - 1):
            self.never_opened(self.plain, bc.BootContractError, 'table cap')

    def test_the_descriptor_must_be_the_file_that_was_inspected(self):
        with mock.patch.object(cli.os, 'lstat', return_value=os.lstat(self.other)):
            with self.assertRaisesRegex(cli.UsageError, 'not a regular file'):
                cli.read_table(self.plain)

    def test_a_link_swapped_in_after_the_inspection_is_not_followed(self):
        with mock.patch.object(cli.os, 'lstat', return_value=os.lstat(self.plain)):
            with self.assertRaisesRegex(cli.UsageError, 'cannot open'):
                cli.read_table(self.link)

    def test_a_missing_file_is_a_usage_error(self):
        with self.assertRaisesRegex(cli.UsageError, 'cannot open'):
            cli.read_table(SCRATCH / 'absent.bin')

    def test_a_regular_table_is_read_whole(self):
        self.assertEqual(cli.read_table(self.plain), b'0123456789')


class CommandLine(unittest.TestCase):
    def test_a_link_in_place_of_a_table_is_refused_with_exit_2(self):
        for name, target_kind in (('link-to-file', 'file'), ('link-to-fifo', 'fifo')):
            shutil.rmtree(SCRATCH / name, ignore_errors=True)       # a leftover link to a FIFO would block the dump's writer
            dump = B.write_dump(SCRATCH / name)
            (dump / 'APIC.bin').unlink()
            if target_kind == 'file':
                (SCRATCH / 'real-apic.bin').write_bytes(b'x')
                (dump / 'APIC.bin').symlink_to(SCRATCH / 'real-apic.bin')
            else:
                (SCRATCH / 'real-apic.fifo').unlink(missing_ok=True)
                os.mkfifo(SCRATCH / 'real-apic.fifo')
                (dump / 'APIC.bin').symlink_to(SCRATCH / 'real-apic.fifo')
            with self.subTest(name):
                code, _, err = B.run_cli(dump)
                self.assertEqual(code, 2, err)
                self.assertIn('APIC.bin is not a regular file', err)

    def test_a_dump_of_regular_files_is_still_read(self):
        shutil.rmtree(SCRATCH / 'plain-dump', ignore_errors=True)
        code, out, _ = B.run_cli(B.write_dump(SCRATCH / 'plain-dump'))
        self.assertIn('ACPI tables read from', out)
        self.assertIn(code, (0, 1))      # the shipped descriptor is a template: refused, but the tables were read


class Hygiene(unittest.TestCase):
    def test_spdx_and_sizes(self):
        for name in ('check-hamoa-acpi.py', 'test-check-hamoa-acpi-hardening.py'):
            path = TOOLS / name
            text = path.read_text()
            self.assertIn('SPDX-License-Identifier: MIT', '\n'.join(text.splitlines()[:3]), name)
            self.assertLess(len(text.splitlines()), 1500, name)
            self.assertLess(path.stat().st_size, 50 * 1024, name)


if __name__ == '__main__':
    unittest.main()
