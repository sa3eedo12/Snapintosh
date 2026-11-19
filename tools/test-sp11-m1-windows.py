# SPDX-License-Identifier: MIT
"""tools/sp11_stick_bundle_windows.py (K15, R11-v2): the windows that the record of rung 7 names for the nodes that the quiet profile withdraws.

The independent review of the first text found two retained windows (the DARTs of the video decoder and of the scaler) listed as withdrawn and one withdrawn window (the scaler's
0X290000000) missing, in four places of a hand list. Now the four places render one table, and these tests hold the table to the device trees: `derive` reads the reg of the withdrawn
nodes through the ranges of arm-io from two parsed trees (probes/appledt.py), and `Trees` (the J700 26A428 kernelcache, git-ignored Apple material, SKIP without it: a skip is never a
pass) builds the base board and the quiet guest as the M1 builder does, parses both serialised trees and requires the table, and the sentences made of it, to be exactly what the trees
say, and the check to fail for a seeded wrong window (the old start of the video decoder, a retained DART listed as withdrawn, a missing window, a wrong audio window, a shared window
taken for a withdrawn one). `Shapes` needs no Apple input: small trees written by this file. Nothing is written to a stick; hardware NOT RUN."""
import contextlib
import io
import struct
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'probes'))
sys.path.insert(0, str(ROOT / 'tools'))
import xnuboot as xb  # noqa: E402
from appledt import DeviceTree, NAME_LENGTH  # noqa: E402
import sp11_stick_bundle_build as bb  # noqa: E402
import sp11_stick_bundle_chunks as ch  # noqa: E402
import sp11_stick_bundle_profile as gpb  # noqa: E402
import sp11_stick_bundle_windows as win  # noqa: E402

BASE = 'j700ap-26A428'
QUIET = gpb.QUIET
AIO = win.ARM_IO


def boot_set_present():
    try:
        return not bb.xnuboot().check_profile()
    except Exception:       # noqa: BLE001  a checkout without the Apple material must skip these tests, not break the file
        return False


needs_boot_set = unittest.skipUnless(boot_set_present(), 'no J700 26A428 boot set under downloads/extracted/j700ap (git-ignored Apple material)')


def node_blob(name, props, kids=()):
    out = struct.pack('<II', len(props) + 1, len(kids))
    for key, value in (('name', name.encode() + b'\0'), *props.items()):
        out += key.encode().ljust(NAME_LENGTH, b'\0') + struct.pack('<I', len(value)) + value + b'\0' * ((-len(value)) % 4)
    return out + b''.join(kids)


def reg(*pairs):
    return b''.join(struct.pack('<QQ', a, s) for a, s in pairs)


def tiny(children):
    """A tree whose arm-io has the one range 0 -> 0x210000000 (0x2f0000000 long) and `children` ({name: reg bytes})."""
    ranges = struct.pack('<QQQ', 0, 0x210000000, 0x2f0000000)
    return DeviceTree(node_blob('device-tree', {}, [node_blob('arm-io', {'ranges': ranges}, [node_blob(n, {'reg': r}) for n, r in children.items()])]))


def tree(name, include_deferred=False):
    """The guest tree as the M1 builder's real_built makes it, up to the loader's own build_devicetree; module state is restored afterwards."""
    with mock.patch.dict(xb.__dict__, xb.__dict__.copy()):
        xb.configure_guest(name)
        xb.configure_txm_arena()
        xb.configure_adreno_va_wide()
        ch.place_layout(xb, bb.placement_of(types.SimpleNamespace(base=hex(0x880000000), guest_mib=384, reserve_mib=None)))
        with contextlib.redirect_stdout(io.StringIO()):
            return xb.build_devicetree(include_deferred=include_deferred)


class Shapes(unittest.TestCase):
    def test_a_window_is_translated_through_the_ranges_and_an_entry_outside_them_is_not_an_address(self):
        before = tiny({'avd': reg((0x170000000, 0x1404000), (0x0900000000, 0x10)), 'gone': reg()})
        after = tiny({'dart-avd': reg((0x171020000, 0x20000))})
        got = win.derive(before, after, [f'{AIO}/avd', f'{AIO}/gone'], nodes=2)
        self.assertEqual(got['by_node'], {'avd': ((0x380000000, 0x381404000),)})
        self.assertEqual((got['holes'], got['shared'], got['darts']), ({'avd': ((0x381020000, 0x381040000),)}, (), {'dart-avd': (0x381020000, 0x381040000)}))

    def test_a_window_that_a_retained_node_covers_is_shared_and_one_it_covers_in_part_is_refused(self):
        before = tiny({'x': reg((0x98078000, 0x4000))})
        self.assertEqual(win.derive(before, tiny({'aes': reg((0x98078000, 0x4000))}), [f'{AIO}/x'], nodes=1)['shared'], ((0x2A8078000, 0x2A807C000),))
        self.assertEqual(win.derive(before, tiny({'pmgr': reg((0x98070000, 0x10000))}), [f'{AIO}/x'], nodes=1)['by_node'], {})
        with self.assertRaises(ValueError):
            win.derive(before, tiny({'half': reg((0x98079000, 0x10000))}), [f'{AIO}/x'], nodes=1)

    def test_the_count_of_the_withdrawn_nodes_is_the_one_the_words_are_written_for(self):
        with self.assertRaises(ValueError):
            win.derive(tiny({'a': reg()}), tiny({}), [f'{AIO}/a'])

    def test_the_sentences_are_made_of_the_table_in_the_words_of_the_review(self):
        self.assertEqual(win.for_falsifiers(), '0X380000000..0X381404000 for the video decoder but for its DART\'s 0X381020000..0X381040000, 0X291000000..0X291038000, '
                         '0X291204000..0X291208000, 0X291210000..0X291214000 and 0X290000000..0X290008000 for the scaler')
        self.assertEqual(win.for_result(), '0x380000000..0x381404000 but for its DART\'s 0x381020000..0x381040000')
        self.assertEqual(win.for_boot26(), 'other windows 0X291204000 and 0X291210000 (0X4000 each) and 0X290000000 (0X8000), and its DART, the node dart-scaler that stays in the quiet tree, at 0X291260000')
        self.assertEqual(win.for_new_stop(), 'a window of a node that the profile withdrew (the video decoder\'s 0X380000000..0X381404000 but not its DART\'s 0X381020000..0X381040000, which stays; the '
                         'scaler\'s 0X291000000..0X291038000, 0X291204000..0X291208000, 0X291210000..0X291214000 and 0X290000000..0X290008000, but not its DART\'s 0X291260000..0X291280000, '
                         'which stays; the ProRes engine\'s 0X294000000..0X294008000 and 0X295000000..0X295004000; the audio roots\' 0X313200000..0X313234000, 0X313300000..0X313334000 and '
                         '0X313800000..0X313834000; the coordinator reads these as the reg of the 43 nodes in the base board\'s DeviceTree.j700ap through the ranges of arm-io, and takes no other '
                         'window for this reading: the video decoder\'s 0X308078000 and the audio roots\' 0X3082A8000 are shared with nodes that stay)')


@needs_boot_set
class Trees(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # both trees are serialised and parsed again, which is what the builder's read-back does with the published chunk
        cls.before = DeviceTree(tree(BASE, include_deferred=True).serialize())
        cls.base = DeviceTree(tree(BASE).serialize())
        cls.quiet = DeviceTree(tree(QUIET).serialize())
        cls.withdrawn = sorted(set(cls.base.nodes) - set(cls.quiet.nodes))

    def test_the_trees_say_the_table_and_the_table_is_what_the_words_render(self):
        self.assertEqual((len(self.base.nodes), len(self.quiet.nodes), len(self.withdrawn)), (395, 352, win.NODES))
        for before in (self.before, self.base):
            got = win.derive(before, self.quiet, self.withdrawn)
            self.assertEqual(got, win.expected())
        win.check(self.before, self.quiet, self.withdrawn)
        self.assertEqual(win.derive(self.base, self.quiet, self.withdrawn)['by_node']['scaler0'], tuple(sorted(win.SCALER)))

    def test_no_retained_window_is_listed_as_withdrawn_and_every_withdrawn_arm_io_window_is_listed_or_shared(self):
        got = win.derive(self.base, self.quiet, self.withdrawn)
        listed = {w for ws in got['by_node'].values() for w in ws} | set(got['shared'])
        for path in self.quiet.nodes:
            if path.rsplit('/', 1)[0] == AIO:
                for window in win._translate(self.quiet, path, [(0, 0x210000000, 0x2F0000000)]):
                    self.assertNotIn(window, {w for ws in got['by_node'].values() for w in ws}, path)
        for path in self.withdrawn:
            if path.rsplit('/', 1)[0] == AIO:
                for window in win._translate(self.base, path, [(0, 0x210000000, 0x2F0000000)]):
                    self.assertIn(window, listed, path)

    def test_the_check_fails_for_each_seeded_wrong_window(self):
        seeds = {'the old start of the video decoder': ('avd', ((0x380001000, 0x381404000),)),
                 'a retained DART listed as withdrawn': ('scaler0', (*win.SCALER, (0x291260000, 0x291280000))),
                 'the scaler without its 0X290000000': ('scaler0', win.SCALER[:3]),
                 'a wrong audio window': ('admac-base-ns', ((0x313800000, 0x313838000),)),
                 'a missing ProRes window': ('apr', win.PRORES[:1])}
        for what, (node, value) in seeds.items():
            with self.subTest(what), mock.patch.dict(win.BY_NODE, {node: value}):
                with self.assertRaises(ValueError):
                    win.check(self.before, self.quiet, self.withdrawn)
        for what, patch in {'the hole of the video decoder forgotten': mock.patch.dict(win.HOLES, {}, clear=True),
                            'a shared window taken for a withdrawn one': mock.patch.object(win, 'SHARED', win.SHARED[:1]),
                            'a retained DART that is not there': mock.patch.object(win, 'SCALER_DART', ('dart-scaler', 0x291264000, 0x291280000))}.items():
            with self.subTest(what), patch:
                with self.assertRaises(ValueError):
                    win.check(self.before, self.quiet, self.withdrawn)

    def test_a_tree_that_is_not_the_one_the_words_are_written_for_is_refused(self):
        lost = DeviceTree(self.quiet.serialize())
        lost.nodes.pop(f'{AIO}/dart-avd')
        lost.children[AIO].remove(f'{AIO}/dart-avd')
        with self.assertRaises(ValueError):
            win.check(self.before, lost, self.withdrawn)
        with self.assertRaises(ValueError):
            win.check(self.before, self.quiet, self.withdrawn[:-1])


if __name__ == '__main__':
    unittest.main()
