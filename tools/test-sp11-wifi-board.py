#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests for tools/sp11_wifi_board.py, the ath12k board-container reader.

The encoder below is written here from the format description in the module's docstring and shares no code with the
parser, so a parser bug cannot hide in a shared helper. Every failure mode F1 to F6 of the module has a case that must
be refused or held, and the property test cuts a valid container at every byte.

    python3 tools/test-sp11-wifi-board.py
"""
import contextlib
import importlib.util
import io
import struct
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('sp11_wifi_board', ROOT / 'tools' / 'sp11_wifi_board.py')
wb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wb)

MAGIC = b'QCA-ATH12K-BOARD\0'
WCN = 'bus=pci,vendor=17cb,device=1107,subsystem-vendor=17cb,subsystem-device=1107,qmi-chip-id=2,qmi-board-id=255'
OTHER = 'bus=pci,vendor=17cb,device=1107,subsystem-vendor=17aa,subsystem-device=e0e6,qmi-chip-id=2,qmi-board-id=255'
VARIANT = WCN + ',variant=LE_Altai'


def pad(n):
    return (-n) % 4


def elem(ident, body):
    return struct.pack('<II', ident, len(body)) + body + bytes(pad(len(body)))


def entry(top, names, data, order=None):
    subs = [elem(0, n.encode()) for n in names] + [elem(1, data)]
    return elem(top, b''.join(order(subs) if order else subs))


def container(*entries, magic=MAGIC):
    return magic + bytes(pad(len(magic))) + b''.join(entries)


GOOD = container(entry(0, [WCN, OTHER], b'\x01' * 7), entry(0, [VARIANT], b'\x02' * 12), entry(1, ['bus=pci'], b'\x03' * 5))


class ParsedContainer(unittest.TestCase):
    def test_valid_container_round_trips(self):
        got = wb.parse(GOOD)
        self.assertEqual([(e.kind, e.names, e.data) for e in got],
                         [('board', (WCN, OTHER), b'\x01' * 7), ('board', (VARIANT,), b'\x02' * 12),
                          ('regdb', ('bus=pci',), b'\x03' * 5)])

    def test_an_empty_container_is_valid_and_empty(self):
        self.assertEqual(wb.parse(container()), [])

    def test_bytearray_is_accepted_and_str_is_not(self):
        self.assertEqual(len(wb.parse(bytearray(GOOD))), 3)
        for bad in ('text', None, 5):
            with self.assertRaises(wb.BoardContainerError):
                wb.parse(bad)

    def test_data_digest_is_of_the_data_only(self):
        import hashlib
        self.assertEqual(wb.parse(GOOD)[0].sha256, hashlib.sha256(b'\x01' * 7).hexdigest())


class RefusedContainers(unittest.TestCase):
    def refused(self, blob, why):
        with self.assertRaises(wb.BoardContainerError, msg=why):
            wb.parse(blob)

    def test_not_a_container(self):
        self.refused(b'', 'empty')
        self.refused(b'QCA-ATH12K-BOARD', 'magic without its NUL')
        self.refused(b'QCA-ATH12K-BOARD\0\0\0', 'padding after the magic cut short')
        self.refused(b'QCA-ATH12K-B0ARD\0\0\0\0', 'one magic character differs')
        self.refused(b'qca-ath12k-board\0\0\0\0', 'magic in lower case')
        self.refused(MAGIC[:-1] + b'x' + GOOD[len(MAGIC):], 'magic whose NUL is replaced')

    def test_lengths_that_do_not_fit(self):
        self.refused(container() + b'\x00\x00\x00', 'three trailing bytes')
        self.refused(container() + struct.pack('<II', 0, 100) + b'abcd', 'length overruns the file')
        self.refused(container() + struct.pack('<II', 0, 0xffffffff), 'length 0xffffffff')
        self.refused(container() + struct.pack('<II', 0, 5) + b'12345', 'padding does not fit')
        self.refused(container() + elem(0, b'') + b'\xff' * 9, 'garbage that decodes as an element')
        sub_overrun = struct.pack('<II', 0, 8) + struct.pack('<II', 0, 99) + b'abcd'
        self.refused(container(sub_overrun), 'sub-element overruns its entry')
        unpadded = elem(0, b'n') + struct.pack('<II', 1, 5) + b'12345'
        self.refused(container() + struct.pack('<II', 0, len(unpadded)) + unpadded, 'last block missing its padding')

    def test_elements_the_driver_would_skip(self):
        self.refused(container(elem(2, bytes(8))), 'unknown top-level id')
        self.refused(container(entry(0, ['n'], b'd', order=lambda s: s + [elem(7, b'x')])), 'unknown sub id')
        self.refused(container(elem(0, b'')), 'entry with no sub-elements')
        self.refused(container(elem(0, elem(0, b'n'))), 'name without data')
        self.refused(container(elem(0, elem(1, b'd'))), 'data without a name')
        self.refused(container(entry(0, ['n'], b'd', order=lambda s: s[::-1])), 'data before the name')
        self.refused(container(entry(0, ['n'], b'd', order=lambda s: s + [elem(1, b'e')])), 'two data blocks')
        self.refused(container(entry(0, ['n'], b'd', order=lambda s: s + [elem(0, b'm')])), 'name after the data')
        self.refused(container(entry(0, [''], b'd')), 'empty name')
        self.refused(container(entry(0, ['a\0b'], b'd')), 'name with a NUL')
        self.refused(container(entry(0, ['caf\xe9'], b'd')), 'non-ASCII name')
        self.refused(container(entry(0, ['tab\there'], b'd')), 'control character in a name')

    def test_duplicate_names(self):
        self.refused(container(entry(0, [WCN], b'a'), entry(0, [WCN], b'b')), 'one name in two entries')
        self.refused(container(entry(0, [WCN, WCN], b'a')), 'one name twice in an entry')
        wb.parse(container(entry(0, ['bus=pci'], b'a'), entry(1, ['bus=pci'], b'b')))   # kinds are separate

    def test_every_prefix_is_an_error_or_a_prefix_of_the_entries(self):
        full = wb.parse(GOOD)
        boundaries, pos = {20}, 20
        while pos < len(GOOD):
            pos += 8 + ((struct.unpack_from('<I', GOOD, pos + 4)[0] + 3) & ~3)
            boundaries.add(pos)
        accepted = set()
        for n in range(len(GOOD) + 1):
            try:
                got = wb.parse(GOOD[:n])
            except wb.BoardContainerError:
                continue
            accepted.add(n)
            self.assertEqual(got, full[:len(got)], f'prefix of {n} bytes')
        self.assertEqual(accepted, boundaries)


class Lookups(unittest.TestCase):
    ids = (0x17cb, 0x1107, 0x17cb, 0x1107, 2, 255)

    def test_the_name_matches_the_drivers_format(self):
        self.assertEqual(wb.board_name(*self.ids), WCN)
        self.assertEqual(wb.board_name(*self.ids, variant='LE_Altai'), VARIANT)
        self.assertEqual(wb.board_name(0x17cb, 0x1112, 0x105b, 0x0108, 33, 24),
                         'bus=pci,vendor=17cb,device=1112,subsystem-vendor=105b,subsystem-device=0108,'
                         'qmi-chip-id=33,qmi-board-id=24')

    def test_arguments_are_range_checked(self):
        for bad in ((0x10000, 1, 1, 1, 1, 1), (1, -1, 1, 1, 1, 1), (1, 1, 1, 1, 2 ** 31, 1), (1, 1, 1, 1, 1, True),
                    (1, 1, 1, 1, 1.0, 1), ('17cb', 1, 1, 1, 1, 1)):
            with self.assertRaises(wb.BoardContainerError, msg=str(bad)):
                wb.board_name(*bad)
        for variant in ('', 'a,b', 'a b', 'x' * 32, 'caf\xe9'):
            with self.assertRaises(wb.BoardContainerError, msg=repr(variant)):
                wb.board_name(*self.ids, variant=variant)
        self.assertTrue(wb.board_name(*self.ids, variant='x' * 31).endswith('x' * 31))

    def test_the_match_is_exact(self):
        entries = wb.parse(GOOD)
        self.assertIsNotNone(wb.find(entries, 'board', OTHER))
        for near in (WCN[:-1], WCN + 'x', WCN.upper(), ' ' + WCN, WCN.replace('1107', '1108')):
            self.assertIsNone(wb.find(entries, 'board', near), near)
        self.assertIsNone(wb.find(entries, 'regdb', WCN), 'kinds are separate')

    def test_the_first_match_wins_across_aliases(self):
        entries = wb.parse(GOOD)
        self.assertEqual(wb.find(entries, 'board', WCN).data, b'\x01' * 7)
        self.assertEqual(wb.find(entries, 'board', OTHER).data, b'\x01' * 7)

    def test_board_lookup_order(self):
        entries = wb.parse(GOOD)
        got, name = wb.lookup_board(entries, *self.ids, variant='LE_Altai')
        self.assertEqual((got.data, name), (b'\x02' * 12, VARIANT))
        got, name = wb.lookup_board(entries, *self.ids, variant='NOT_THERE')
        self.assertEqual((got.data, name), (b'\x01' * 7, WCN), 'falls back to the name without the variant')
        got, name = wb.lookup_board(entries, *self.ids)
        self.assertEqual(name, WCN)
        got, name = wb.lookup_board(entries, 0x17cb, 0x1107, 0x17cb, 0x1107, 2, 254)
        self.assertEqual((got, name.endswith('qmi-board-id=254')), (None, True))

    def test_regdb_lookup_order(self):
        both = wb.parse(container(entry(1, [WCN], b'full'), entry(1, ['bus=pci'], b'bus')))
        self.assertEqual(wb.lookup_regdb(both, *self.ids)[0].data, b'full')
        self.assertEqual(wb.lookup_regdb(both, 0x17cb, 0x1107, 1, 1, 2, 255)[0].data, b'bus')
        only_bus = wb.parse(container(entry(1, ['bus=pci'], b'bus')))
        got, name = wb.lookup_regdb(only_bus, *self.ids)
        self.assertEqual((got.data, name), (b'bus', 'bus=pci'))
        none = wb.parse(container(entry(0, [WCN], b'board')))
        self.assertIsNone(wb.lookup_regdb(none, *self.ids)[0], 'a board entry is not a regdb entry')


class CommandLine(unittest.TestCase):
    work = ROOT / 'build' / 'test-sp11-wifi-board'

    @classmethod
    def setUpClass(cls):
        cls.work.mkdir(parents=True, exist_ok=True)
        (cls.work / 'good.bin').write_bytes(GOOD)
        (cls.work / 'bad.bin').write_bytes(GOOD[:-3])

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = wb.main([str(a) for a in argv])
        return code, out.getvalue(), err.getvalue()

    ids = ('--vendor', '17cb', '--device', '1107', '--subsystem-vendor', '17cb', '--subsystem-device', '1107',
           '--chip-id', '2', '--board-id', '255')

    def test_list(self):
        code, out, _ = self.run_cli('list', self.work / 'good.bin')
        self.assertEqual(code, 0)
        self.assertIn(WCN, out)
        self.assertEqual(out.count('sha256='), 3)

    def test_find_and_regdb(self):
        code, out, _ = self.run_cli('find', self.work / 'good.bin', *self.ids)
        self.assertEqual((code, 'FOUND: board' in out), (0, True))
        code, out, _ = self.run_cli('find', self.work / 'good.bin', *self.ids[:-1], '254')
        self.assertEqual((code, 'NO ENTRY' in out), (1, True))
        code, out, _ = self.run_cli('regdb', self.work / 'good.bin', *self.ids)
        self.assertEqual((code, 'FOUND: regdb bus=pci' in out), (0, True))

    def test_unusable_inputs_exit_two(self):
        for target in (self.work / 'bad.bin', self.work / 'missing.bin'):
            code, _, err = self.run_cli('list', target)
            self.assertEqual((code, err.startswith('UNUSABLE')), (2, True), str(target))
        code, _, err = self.run_cli('find', self.work / 'good.bin', *self.ids[:-1], '4294967295')
        self.assertEqual(code, 2)
        with self.assertRaises(SystemExit) as stop, contextlib.redirect_stderr(io.StringIO()):
            wb.main(['find', str(self.work / 'good.bin'), '--vendor', 'zz'])
        self.assertEqual(stop.exception.code, 2)


class Hygiene(unittest.TestCase):
    def test_spdx_and_no_machine_strings(self):
        for path in (Path(__file__), ROOT / 'tools' / 'sp11_wifi_board.py'):
            text = path.read_text()
            self.assertIn('SPDX-License-Identifier: MIT', '\n'.join(text.splitlines()[:3]))
            for prefix in ('/' + 'Users' + '/', '/' + 'home' + '/'):
                self.assertNotIn(prefix, text)
            self.assertTrue(text.isascii(), str(path))


if __name__ == '__main__':
    unittest.main(verbosity=1)
