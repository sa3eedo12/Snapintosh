#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of the mechanism of rung 10, rung 9's root-mount form with the ISC (docs/SP11-STICK-ROOT.md section 11; EXPERIMENTAL, hardware NOT RUN): the same form (`--cpu-start ack --let-panic --boot-arg
cpu_boot_timeout_secs=360 --time-box-s 300 --guest-profile j700ap-26A428-m1-quiet-rtc-acm --bigfile --ramdisk ... --root-hash ... --trust-cache ...`) with ONE more option, `--isc-image`, so that the RAM disk
is a GPT medium of the root and the ISC (probes/bootmedia.py). Checked here: the closed list of forms is still the five of rung 9 (the ISC is an input of rung 9's form, with its own label M1RUN-ACKPANIC-M-RUNG10 and
its own guide key); `--isc-image` with another profile, without the root's inputs or without the gate is a named refusal that writes nothing, and an image that is not an APFS container with the three roles is
ROOT_INPUT_BAD; the comparison of the finished chunk table with the pinned table of rung 9's own bundle, on a small universe that has the same rules (every seeded fault is PANEL_MISMATCH: the table, the three
chunks put back, the displacement of the root, the GPT, the digests of the two partitions); the streaming GUEST.DAT against `pack_guest` on a small root and ISC; the builder end to end through the stand-in loader,
with the record of rung 10 and the records of rung 9 and of each older kind refused against each other (AUTH_RECORD_MISMATCH, nothing written); the sizes and the caps. The words of the record are
tools/test-sp11-stick-monitor-rung10-words.py's, which takes its fixtures from here; `Real` needs downloads/extracted/j700ap/, the local pair, the facts and the ISC image, and SKIPs without them (a skip is never a
pass).

    python3 tools/test-sp11-stick-monitor-rung10.py

The regeneration script of the session (MODE=rung10) is the end-to-end test with the real J700 26A428 kernelcache, the real root, the real ISC and the real pair. Nothing here runs the unit: Hardware NOT RUN."""
import contextlib
import hashlib
import importlib.util
import os
import struct
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))

spec = importlib.util.spec_from_file_location('sp11_monitor_rung9_mechanism', TOOLS / 'test-sp11-stick-monitor-rung9.py')
M9 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M9)
M8, M7, M6, M5 = M9.M8, M9.M7, M9.M6, M9.M5
SCRATCH = ROOT / 'build' / 'sp11-monitor-rung10-test-scratch'
M9.SCRATCH = M5.SCRATCH = M5.PS.SCRATCH = M5.PT.SCRATCH = M5.PT.BLD.SCRATCH = M5.PT.AUTH.SCRATCH = SCRATCH
PS, PT, pn, m1, BT, ch, sb, bb, BLD, AUTH = M5.PS, M5.PT, M5.pn, M5.m1, M5.BT, M5.ch, M5.sb, M5.bb, M5.BLD, M5.AUTH
import sp11_stick_bundle_profile as gpb  # noqa: E402
import sp11_stick_bundle_root as root  # noqa: E402
import sp11_stick_bundle_rootbundle as rb  # noqa: E402
import sp11_stick_bundle_rung9 as r9b  # noqa: E402
import sp11_stick_bundle_rung10 as r10b  # noqa: E402
import sp11_stick_bundle_panel_rung10 as r10  # noqa: E402
import sp11_stick_bundle_source as src  # noqa: E402
import bootmedia as bm  # noqa: E402

STATE, OLD, NAMES, W360 = M5.STATE, M5.OLD, M5.NAMES, M9.W360
ROOTP = gpb.ROOT
MIB = 1 << 20
LABEL10 = 'RUNG10'
ISC_ARGS = dict(isc_image='stand-in-isc.apfs')
ISC_INFO = dict(bytes=r10b.ISC_BYTES, sha256='ab' * 32, roles=[16, 256, 320])
# What `compare` returns for the real guest: the numbers of the session's host build (the regeneration script checks them against the real kernelcache, the real root and the real ISC image).
REAL_DIFF10 = dict(count=347, outside=48, put_back=3, dt_bytes=(328_080, 328_080), avail=(r10b.RUNG9_END, r10b.RAMDISK_AT + r10b.MEDIUM_BYTES),
                   medium=dict(bytes=r10b.MEDIUM_BYTES, dest=r10b.RAMDISK_AT, end=r10b.RAMDISK_AT + r10b.MEDIUM_BYTES, chunks=299, zero=147, data_bytes=1_798_655_472, zero_bytes=1_140_503_056),
                   prefix=dict(chunks=2, data=1_248, bytes=1 << 20), root=dict(at=1 << 20, bytes=r10b.ROOT_BYTES, chunks=291, zero=143, last_dest=38_618_988_544, last_size=208_896, last_extra=143_360),
                   isc=dict(at=1_863_319_552, bytes=r10b.ISC_BYTES, chunks=5, zero=3, data=200_880, sha256='ab' * 32), tail=dict(at=2_938_109_952, bytes=1 << 20, chunks=1, data=16_896), table_room=677)


def fake_compare10(chunks, table, guest_bytes, nodes, isc):
    """What `compare` returns for the stand-in loader's filler guest (which is not rung 9's: the real comparison has its own tests below): the real numbers, with this build's node count."""
    return dict(REAL_DIFF10, nodes=(395, nodes), guest_bytes=(r10b.RUNG9_GUEST_BYTES, 1_926_984_384))


def stand_in_guest10(a, xb, place, facts, profile):
    """Rung 9's stand-in loader, and the ISC's facts that real_guest reads from the image before the loader maps it."""
    built, nodes = M9.stand_in_guest(a, xb, place, facts, profile)
    a.isc_info = ISC_INFO
    return built, nodes


@contextlib.contextmanager
def patched10():
    with M9.patched(), mock.patch.object(rb, 'guest', stand_in_guest10), mock.patch.object(r10b, 'compare', fake_compare10):
        yield


def over10(over):
    """The arguments of a build of rung 10 over the stand-in loader: rung 9's with the ISC image and the facts label RUNG10."""
    return M9.over9({'facts': BLD.facts_with(LABEL10), **ISC_ARGS, **over})


def record10(name, let_panic=True, **over):
    """(code, text, err) of the record of rung 10 from the stand-in loader."""
    args = M5.args5(name, 300, **over10(over))
    with patched10(), PS.stand_in():
        return BT.cli(['authorisation-template', 'real', *M9.opts9(args, let_panic)])


def build10(name, approved=None, **over):
    """(code, text, err, out) of a build of rung 10 with the approved record `approved` (its own by default)."""
    out, args = SCRATCH / 'out' / name, M5.args5(name, 300, **over10(over))
    with patched10(), PS.stand_in():
        text = approved if approved is not None else BT.cli(['authorisation-template', 'real', *M9.opts9(dict(args))])[1]
        argv = ['real', *M9.opts9(dict(args)), '--out', out, '--no-manifest', '--authorisation', BT.write(SCRATCH / 'records' / f'{name}.txt', text.encode())]
        return (*BT.cli(argv), out)


def setUpModule():
    M9.setUpModule()
    code, text, err = record10('setup-r10')
    assert code == 0, err
    STATE['r10'] = text
    made, real_with = [], r10b.with_guest

    def spy(*args):
        made.append(real_with(*args))
        return made[-1]
    with mock.patch.object(r10b, 'with_guest', spy):
        code, text, err = record10('setup-r10-panel')
    assert code == 0, err
    STATE['r10-panel'] = made[0]


def tearDownModule():
    M9.tearDownModule()


def tiny_tree(size, extents):
    """A device tree of the one node that matters: /device-tree with a child /device-tree/chosen that holds boot-ramdmg-size and boot-ramdmg-extents, as probes/appledt.py reads it."""
    def prop(name, value):
        return name.encode().ljust(32, b'\0') + struct.pack('<I', len(value)) + value + bytes(-len(value) % 4)
    return (struct.pack('<II', 1, 1) + prop('name', b'device-tree' + bytes(5)) + struct.pack('<II', 3, 0) + prop('name', b'chosen\0') + prop('boot-ramdmg-size', struct.pack('<Q', size))
            + prop('boot-ramdmg-extents', struct.pack('<QQ', *extents)))


def universe(root_bytes=2 * MIB, isc_bytes=MIB, base=0x1000000):
    """A small universe with the rules of rung 10's comparison: a root of four stretches (two of them zeros of 128 KiB), an ISC image that begins with data and ends in a long zero stretch, rung 9's four
    chunks outside the RAM disk (HANDOFF chunk 0 and PARAMS and the device tree, each with the word that names the RAM disk's end, and one that stays) and the root's own chunks as rung 9 cuts them, and
    the same four plus the GPT medium's chunks as rung 10 does. Returns (the pins for r10b, the chunks, the table, the ISC's facts, the medium's bytes)."""
    piece = 0x80000
    rng = os.urandom
    root_blob = bytearray(rng(root_bytes))
    root_blob[0x40000:0x60000] = bytes(0x20000)
    root_blob[0x140000:0x160000] = bytes(0x20000)
    isc_blob = bytearray(rng(isc_bytes))
    isc_blob[0x10000:] = bytes(isc_bytes - 0x10000)
    medium_layout = bm.layout(root_bytes, isc_bytes)
    end9, end10 = base + root_bytes, base + medium_layout['bytes']
    sha = lambda data: hashlib.sha256(bytes(data)).hexdigest()    # noqa: E731

    def outside(end, size=None, extents=None):
        handoff = bytearray(rng(0x2000))
        struct.pack_into('<Q', handoff, 0x18, end)
        struct.pack_into('<Q', handoff, 0x1020, end)
        params = bytearray(rng(8 * 60))
        struct.pack_into('<Q', params, 8 * 46, end)
        return bytes(handoff), bytes(params), tiny_tree(size, extents)
    h9, p9, t9 = outside(end9, root_bytes, (base, root_bytes))
    h10 = bytearray(h9)
    p10 = bytearray(p9)
    for at in r10b.AVAIL_WORDS:
        struct.pack_into('<Q', h10, at, end10)
    struct.pack_into('<Q', p10, 8 * 46, end10)
    t10 = tiny_tree(medium_layout['bytes'], (base, medium_layout['bytes']))
    keep = b'the chunk that stays'.ljust(32, b'.')
    rows9 = [(3, 0x1000, len(h9), sha(h9)), (5, 0x4000, len(p9), sha(p9)), (4, 0x5000, len(t9), sha(t9)), (1, 0x9000, len(keep), sha(keep))]
    root_chunks9 = src.split_run(sb.KIND_OTHER, base, src.BufferSource(bytes(root_blob)), 0x10000, 100, piece)
    chunks_dict, desc = bm.assemble(bytes(root_blob), bytes(isc_blob), base, 1 << 36)
    ordered = [chunks_dict[pa] for pa in sorted(chunks_dict)]
    medium_blob = src.Pieces([bytes(part) for part in ordered])
    root_chunks10 = src.split_run(sb.KIND_OTHER, base, medium_blob, 0x10000, 100, piece)

    def table_of(chunk_list):
        out = []
        for c in chunk_list:
            if 'source' in c:
                digest = hashlib.sha256(c['source'].read_at(c['offset'], c['size'])).digest()
                out.append(dict(kind=c['kind'], dest=c['dest'], size=c['size'], sha256=digest, flags=0))
            elif c.get('data') is None:
                out.append(dict(kind=c['kind'], dest=c['dest'], size=c['size'], sha256=bytes(32), flags=sb.CHUNK_ZERO))
            else:
                out.append(dict(kind=c['kind'], dest=c['dest'], size=len(c['data']), sha256=hashlib.sha256(c['data']).digest(), flags=0))
        return out
    rows_root9 = table_of(root_chunks9)
    text = lambda rows, shift=0: ''.join(f'{r["kind"]} {r["dest"] - shift:#x} {r["size"]} {bytes(r["sha256"]).hex()}\n' for r in rows)    # noqa: E731
    first = rows_root9[:-1]
    pins = dict(CHUNKS9=tuple(rows9), PATCHED=(0, 1, 2), HANDOFF_AT=0, PARAMS_AT=1, DT_AT=2, RAMDISK_AT=base, ROOT_BYTES=root_bytes, RUNG9_END=end9, ROOT_SHA256=sha(root_blob),
                ISC_BYTES=isc_bytes, MEDIUM_BYTES=medium_layout['bytes'], RUNG9_ROOT_ROWS=len(rows_root9), RUNG9_ROOT_ZERO_ROWS=sum(1 for r in first if r['flags']),
                RUNG9_ROOT_ROWS_SHA256=sha(text(first).encode()),
                RUNG9_LAST_ROW=(sb.KIND_OTHER, rows_root9[-1]['dest'], rows_root9[-1]['size'], bytes(rows_root9[-1]['sha256']).hex()), RUNG9_GUEST_BYTES=5000, RUNG9_CHUNK_COUNT=len(rows9) + len(rows_root9))
    chunks = [dict(kind=3, dest=0x1000, data=bytes(h10)), dict(kind=5, dest=0x4000, data=bytes(p10)), dict(kind=4, dest=0x5000, data=t10), dict(kind=1, dest=0x9000, data=keep), *root_chunks10]
    out_rows = [(3, 0x1000, len(h10), sha(h10)), (5, 0x4000, len(p10), sha(p10)), (4, 0x5000, len(t10), sha(t10)), (1, 0x9000, len(keep), sha(keep))]
    table = [dict(kind=k, dest=d, size=s, sha256=bytes.fromhex(h), flags=0) for k, d, s, h in out_rows] + table_of(root_chunks10)
    return pins, chunks, table, dict(bytes=isc_bytes, sha256=sha(isc_blob)), medium_layout['bytes']


class Form(unittest.TestCase):
    def test_the_closed_list_is_rung_9s_five_forms_and_the_isc_is_an_input_of_the_last(self):
        self.assertEqual(ch.ACK_FORMS, (ch.RUNG5_FORM, ch.RUNG6_FORM, ch.RUNG7_FORM, ch.RUNG8_FORM, ch.RUNG9_FORM), 'rung 10 is rung 9\'s form with an image: there is no sixth form')
        self.assertEqual(r10.KEY10 if hasattr(r10, 'KEY10') else pn.rungs.KEY10, f'console+{W360}+let_panic+{ROOTP}+isc')
        self.assertEqual(pn.kind_key(dict(panel='console', boot_arg=W360, let_panic=True, panel_facts=dict(profile=ROOTP, isc=True))), pn.rungs.KEY10)
        self.assertEqual(pn.kind_key(dict(panel='console', boot_arg=W360, let_panic=True, panel_facts=dict(profile=ROOTP))), pn.rungs.KEY9, 'rung 9\'s key is what it was')
        self.assertIs(pn.rungs.rung_of(dict(let_panic=True, boot_arg=W360, profile=ROOTP, isc=True)), r10)
        self.assertIsNot(pn.rungs.rung_of(dict(let_panic=True, boot_arg=W360, profile=ROOTP)), r10)
        self.assertIn(r10, pn.rungs.ACK)
        self.assertEqual((r10b.RUNG10_LABEL, len(r10b.RUNG10_LABEL)), ('M1RUN-ACKPANIC-M-RUNG10', 23), 'the label fills the 23 characters of PIN.DAT\'s field')

    def test_the_isc_option_is_refused_by_name_for_every_other_profile_without_the_root_and_without_the_gate_and_nothing_is_written(self):
        for what, profile in (('rung 8\'s profile', gpb.RTC), ('rung 7\'s profile', gpb.QUIET), ('the base board', gpb.DEFAULT)):
            with self.subTest(what), self.assertRaises(ch.Refused) as caught:
                rb.check_args(types.SimpleNamespace(isc_image='x.apfs', ramdisk=None, root_hash=None, trust_cache=None, bigfile=True, guest_mib=6144), profile, 'real')
            self.assertEqual(caught.exception.name, 'GUEST_PROFILE_REFUSED')
        cases = {'the root\'s profile without the root': (dict(ramdisk=None, root_hash=None, trust_cache=None), 'GUEST_PROFILE_REFUSED'),
                 'a root without its hash': (dict(root_hash=None), 'ROOT_INPUT_BAD'), 'no --bigfile': (dict(bigfile=None), 'OPTIONS_BAD'), 'another RAM': (dict(guest_mib=4096), 'OPTIONS_BAD'),
                 'the ISC with rung 9\'s box refused as rung 9 refuses it': (dict(time_box_s=1201), 'OPTIONS_BAD'), 'a word of another rung': (dict(boot_arg=M9.F), 'LET_PANIC_REFUSED')}
        for what, (over, name) in cases.items():
            with self.subTest(what):
                code, text, err = record10('isc-bad', **over)
                self.assertEqual((code, BT.refusal(text)), (1, f'REFUSED={name}'), err)
                code, text, err, out = build10('isc-bad-built', approved='not a record', **over)
                self.assertEqual(code, 1)
                self.assertFalse(out.exists(), 'nothing is written')
        args = M5.args5('isc-no-gate', 300, **over10({}))
        with mock.patch.dict(os.environ, {root.GATE_ENV: ''}), mock.patch.object(rb, 'guest', stand_in_guest10), PS.stand_in():
            code, text, err = BT.cli(['authorisation-template', 'real', *M9.opts9(args)])
        self.assertEqual((code, BT.refusal(text)), (1, 'REFUSED=ROOT_GATE_OFF'), err)

    def test_the_label_of_rung_10_is_the_only_one_the_comparison_admits(self):
        for label in ('RUNG9', 'RUNG11', 'ISC'):
            with self.subTest(label), self.assertRaises(ch.Refused) as caught:
                r10b.with_guest({}, ROOTP, dict(build_label='M1RUN-ACKPANIC-M-' + label), [], [], b'', 352, ISC_INFO)
            self.assertEqual(caught.exception.name, 'PANEL_MISMATCH')

    def test_an_isc_image_is_read_before_the_loader_maps_it(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        for what, content, name in (('a missing file', None, 'ROOT_INPUT_BAD'), ('a file that is not APFS', b'not an apfs container' * 100, 'ROOT_INPUT_BAD')):
            path = SCRATCH / 'isc-not-valid.apfs'
            path.unlink(missing_ok=True)
            if content is not None:
                path.write_bytes(content)
            with self.subTest(what), self.assertRaises(ch.Refused) as caught:
                root.check_isc(path)
            self.assertEqual(caught.exception.name, name)
        fake = mock.Mock()
        fake.isc_roles.return_value = {256: 'xART', 16: 'Preboot'}
        fake.ISC_ROLES = {256: 'xART', 16: 'Preboot', 320: 'Hardware'}
        path = SCRATCH / 'isc-two-roles.apfs'
        path.write_bytes(bytes(0x4000))
        with mock.patch.dict(sys.modules, {'cycle_lib': mock.Mock(speed_test=fake), 'cycle_lib.speed_test': fake}), self.assertRaises(ch.Refused) as caught:
            root.check_isc(path)
        self.assertEqual(caught.exception.name, 'ROOT_INPUT_BAD')
        self.assertIn('[16, 256]', caught.exception.detail)
        fake.isc_roles.return_value = dict(fake.ISC_ROLES)
        with mock.patch.dict(sys.modules, {'cycle_lib': mock.Mock(speed_test=fake), 'cycle_lib.speed_test': fake}):
            info = root.check_isc(path)
        self.assertEqual((info['bytes'], info['sha256'], info['roles']), (0x4000, hashlib.sha256(bytes(0x4000)).hexdigest(), [16, 256, 320]))


class Compare(unittest.TestCase):
    def setUp(self):
        self.pins, self.chunks, self.table, self.isc, self.medium = universe()

    def run_compare(self, chunks=None, table=None, isc=None, pins=None):
        with mock.patch.multiple(r10b, **(pins or self.pins)), mock.patch.object(r10b, 'pt', mock.Mock(BASE_NODES=395)):
            return r10b.compare(chunks or self.chunks, table or self.table, 4900, 352, isc or self.isc)

    def refused(self, *args, **kw):
        with self.assertRaises(ch.Refused) as caught:
            self.run_compare(*args, **kw)
        self.assertEqual(caught.exception.name, 'PANEL_MISMATCH')
        return caught.exception.detail

    def test_the_comparison_reads_the_small_universe_and_says_what_differs(self):
        diff = self.run_compare()
        self.assertEqual((diff['outside'], diff['put_back'], diff['nodes'], diff['guest_bytes'], diff['dt_bytes'][0] > 0), (4, 3, (395, 352), (5000, 4900), True))
        self.assertEqual((diff['root']['chunks'], diff['root']['at'], diff['medium']['bytes'], diff['medium']['dest']), (self.pins['RUNG9_ROOT_ROWS'], 1 << 20, self.medium, 0x1000000))
        self.assertEqual(diff['avail'], (0x1000000 + 2 * MIB, 0x1000000 + self.medium))
        self.assertGreater(diff['root']['last_extra'], 0, 'the last root chunk is extended by the ISC\'s first data, as in the real guest')
        self.assertEqual(diff['medium']['chunks'], diff['prefix']['chunks'] + diff['root']['chunks'] + diff['isc']['chunks'] + diff['tail']['chunks'])
        self.assertEqual(diff['medium']['data_bytes'] + diff['medium']['zero_bytes'], self.medium)

    def test_each_seeded_fault_of_the_guest_is_panel_mismatch_by_name(self):
        sha = lambda data: hashlib.sha256(data).digest()    # noqa: E731
        base = self.pins['RAMDISK_AT']
        other = lambda i, **kw: [{**row, **kw} if n == i else row for n, row in enumerate(self.table)]    # noqa: E731
        with_data = lambda dest, data: [{**c, 'data': data} if c['dest'] == dest else c for c in self.chunks]    # noqa: E731
        root_rows = [n for n, row in enumerate(self.table) if row['dest'] >= base + (1 << 20) and row['dest'] < base + (1 << 20) + 2 * MIB]
        zero_row = next(n for n in root_rows if self.table[n]['flags'] & sb.CHUNK_ZERO)
        data_row = next(n for n in root_rows[1:-1] if not self.table[n]['flags'] & sb.CHUNK_ZERO)
        handoff, params, tree = self.chunks[0]['data'], self.chunks[1]['data'], self.chunks[2]['data']
        flip = lambda blob, at: blob[:at] + bytes([blob[at] ^ 1]) + blob[at + 1:]    # noqa: E731
        faults = {'a chunk outside differs': (self.chunks, other(3, sha256=sha(b'o'))), 'a chunk outside is shorter': (self.chunks, other(3, size=19)), 'a chunk outside is elsewhere': (self.chunks, other(3, dest=0x9001)),
                  'a chunk outside is missing': (self.chunks, self.table[:3] + self.table[4:]), 'a chunk is added outside': (self.chunks, [*self.table[:4], dict(kind=1, dest=0xD000, size=16, sha256=sha(b'x'), flags=0), *self.table[4:]]),
                  'HANDOFF holds another end (first copy)': (with_data(0x1000, handoff[:0x18] + struct.pack('<Q', 1) + handoff[0x20:]), self.table),
                  'HANDOFF holds another end (second copy)': (with_data(0x1000, handoff[:0x1020] + struct.pack('<Q', 1) + handoff[0x1028:]), self.table),
                  'HANDOFF differs in another byte': (with_data(0x1000, flip(handoff, 0x100)), self.table), 'HANDOFF is shorter': (with_data(0x1000, handoff[:-1]), self.table),
                  'PARAMS holds another end': (with_data(0x4000, params[:8 * 46] + struct.pack('<Q', 1) + params[8 * 47:]), self.table), 'PARAMS differs in another word': (with_data(0x4000, flip(params, 8)), self.table),
                  'no data for PARAMS': ([c for c in self.chunks if c['dest'] != 0x4000], self.table),
                  'the tree has another size': (with_data(0x5000, tiny_tree(1, (self.pins['RAMDISK_AT'], 1))), self.table),
                  'the tree differs in another property': (with_data(0x5000, tree.replace(b'chosen', b'chosex')), self.table),
                  'a root chunk is missing': (self.chunks, [row for n, row in enumerate(self.table) if n != root_rows[1]]),
                  'a root chunk is not displaced': (self.chunks, other(root_rows[1], dest=self.table[root_rows[1]]['dest'] - (1 << 20))),
                  'a root chunk differs': (self.chunks, other(data_row, sha256=sha(b'o'))), 'a root chunk is not ZERO': (self.chunks, other(zero_row, flags=0)),
                  'a root chunk is of another kind': (self.chunks, other(data_row, kind=1)), 'a gap in the medium': (self.chunks, other(root_rows[1], dest=self.table[root_rows[1]]['dest'] + 16))}
        for what, (chunk_data, rows) in faults.items():
            with self.subTest(what):
                self.refused(chunk_data, rows)

    def test_the_gpt_and_the_two_partitions_are_read_back_from_the_chunks(self):
        def with_bytes(dest, offset, data):
            """The chunks with `data` written at `offset` of the chunk that starts at `dest` (a data chunk, or a window of a source), and the table row's digest made again."""
            chunks, table = [], []
            for c in self.chunks:
                if c['dest'] == dest:
                    blob = bytearray(c['data'] if c.get('data') is not None else c['source'].read_at(c['offset'], c['size']))
                    blob[offset:offset + len(data)] = data
                    c = dict(kind=c['kind'], dest=c['dest'], data=bytes(blob))
                chunks.append(c)
            for row, c in zip(self.table, self.chunks):
                same = chunks[self.chunks.index(c)]
                table.append(dict(row, sha256=hashlib.sha256(same['data']).digest()) if c['dest'] == dest else row)
            return chunks, table
        base = self.pins['RAMDISK_AT']
        first = next(c for c in self.chunks if c['dest'] == base)
        self.assertEqual(first['size'] if 'size' in first else len(first['data']), 1248, 'the GPT\'s first chunk holds the MBR, the header and the two entries')
        chunks, table = with_bytes(base, 510, b'\x00\x00')
        self.assertIn('GPT', self.refused(chunks, table))
        chunks, table = with_bytes(base, 600, b'\xff')
        self.assertIn('GPT', self.refused(chunks, table))
        with self.assertRaises(ch.Refused) as caught:
            self.run_compare(isc=dict(self.isc, sha256='cd' * 32))
        self.assertIn('ISC partition', caught.exception.detail)
        with self.assertRaises(ch.Refused) as caught:
            self.run_compare(pins=dict(self.pins, ROOT_SHA256='cd' * 32))
        self.assertIn('root partition', caught.exception.detail)
        with self.assertRaises(ch.Refused) as caught:
            self.run_compare(isc=dict(self.isc, bytes=self.isc['bytes'] + 0x4000))
        self.assertIn('form\'s ISC', caught.exception.detail)

    def test_the_pins_are_rung_9s_own_bundle_48_chunks_outside_the_ram_disk_and_the_numbers_of_the_root(self):
        self.assertEqual(len(r10b.CHUNKS9), 48)
        self.assertEqual((r10b.CHUNKS9[0][0], r10b.CHUNKS9[2][0], r10b.CHUNKS9[3][0]), (sb.KIND_HANDOFF, sb.KIND_PARAMS, sb.KIND_DEVICE_TREE))
        self.assertTrue(all(len(h) == 64 and h.strip('0123456789abcdef') == '' for _, _, _, h in r10b.CHUNKS9))
        self.assertEqual((r10b.RUNG9_GUEST[:16], r10b.RUNG9_RECORD[:8], r10b.RUNG9_LABEL, r10b.RUNG9_GUEST_BYTES, r10b.RUNG9_CHUNK_COUNT), ('b7aeddff38fd6ad8', 'dd2f39f4', 'M1RUN-ACKPANIC-M-RUNG9', 1_926_621_440, 339))
        self.assertEqual((r10b.RUNG9_ROOT_ROWS, r10b.RUNG9_ROOT_ZERO_ROWS, r10b.RUNG9_ROOT_ROWS + len(r10b.CHUNKS9)), (291, 143, 339))
        self.assertEqual((r10b.RAMDISK_AT, r10b.ROOT_BYTES, r10b.RUNG9_END), (0x88ED00000, 1_862_270_976, 0x8FDD00000))
        self.assertEqual((r10b.ISC_BYTES, r10b.MEDIUM_BYTES, r10b.layout(r10b.ISC_BYTES)['bytes'], r10b.layout(r10b.ISC_BYTES)['isc']), (1_074_790_400, 2_939_158_528, 2_939_158_528, (1_863_319_552, 1_074_790_400)))
        self.assertEqual(r10b.RAMDISK_AT + r10b.MEDIUM_BYTES, 0x93E000000, 'the end of the medium, which the free-memory words name')
        self.assertEqual(r10b.GPT_PREFIX, bm.ALIGN, 'the root partition starts at the loader\'s alignment')
        self.assertEqual(r10b.AVAIL_WORDS, (0x18, 0x1020))


class Streaming(unittest.TestCase):
    """GUEST.DAT of a guest whose RAM disk is a GPT medium: the bytes of `pack_guest` at every window, and the write-and-read-back."""

    def test_the_virtual_guest_of_a_medium_is_the_bytes_of_pack_guest(self):
        pins, chunks, table, isc, medium = universe()
        base = pins['RAMDISK_AT']
        tables = dict(kind=sb.KIND_TABLES, dest=0x20000, size=0x4000)       # the interface wants the five kinds: the universe has the other four (the chunk that stays is the kernel's kind)
        chunks = sorted([*chunks, tables], key=lambda c: c['dest'])
        as_data = [dict(kind=c['kind'], dest=c['dest'], data=c['source'].read_at(c['offset'], c['size'])) if 'source' in c else c for c in chunks]
        virtual = root.VirtualGuest(0x1000, base + medium, chunks)
        want = sb.pack_guest(0x1000, base + medium, as_data)
        self.assertEqual((len(virtual), virtual.sha256()), (len(want), hashlib.sha256(want).hexdigest()))
        self.assertEqual(virtual[:], want)
        for lo, hi in ((0, 4096), (4095, 4100), (len(want) - 1, len(want)), (len(want) // 2, len(want) // 2 + 70_000)):
            with self.subTest(lo=lo, hi=hi):
                self.assertEqual(virtual[lo:hi], want[lo:hi])
        SCRATCH.mkdir(parents=True, exist_ok=True)
        path = SCRATCH / 'stream10' / 'GUEST.DAT'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        rb.write_checked(path, virtual)
        self.assertEqual(path.read_bytes(), want)


class Builder(unittest.TestCase):
    def test_the_record_of_rung_10_is_built_with_its_label_and_is_under_the_held_limit(self):
        text = STATE['r10']
        self.assertTrue(text.startswith('SNAPINTOSH SP11 STICK BUNDLE - AUTHORISATION RECORD (format 1, label M1RUN-ACKPANIC-M-RUNG10)'))
        self.assertLessEqual(len(text.encode()), M5.HELD_LIMIT)
        self.assertNotEqual(text, STATE['r9'])

    def test_the_older_records_are_what_they_were_with_rung_10_in_the_tree(self):
        code, text, err = M9.record9('again-9')
        self.assertEqual((code, text), (0, STATE['r9']), err)
        self.assertNotIn('rung 10', STATE['r9'].lower(), 'rung 9\'s record says nothing of rung 10')
        code, text, err = M8.record8('again-8-r10')
        self.assertEqual((code, text), (0, STATE['r8']), err)

    def test_a_build_with_the_approved_record_of_another_kind_is_refused_whichever_is_the_guest(self):
        approved = {'rung 9': STATE['r9'], 'rung 8': STATE['r8'], 'rung 7': STATE['r7']}
        for what, text in approved.items():
            with self.subTest(f'rung 10\'s guest under {what}\'s record'):
                code, out, err, written = build10('cross-10-' + what.replace(' ', ''), approved=text)
                self.assertEqual((code, BT.refusal(out)), (1, 'REFUSED=AUTH_RECORD_MISMATCH'), err)
                self.assertFalse(written.exists(), 'nothing is written')
        with self.subTest('rung 9\'s guest under rung 10\'s record'):
            code, out, err, written = M9.build9('cross-9-with-10', approved=STATE['r10'])
            self.assertEqual((code, BT.refusal(out)), (1, 'REFUSED=AUTH_RECORD_MISMATCH'), err)
            self.assertFalse(written.exists())

    def test_the_approved_record_of_rung_10_builds_its_own_bundle_through_the_stand_in_loader(self):
        code, out, err, written = build10('own-10', approved=STATE['r10'])
        self.assertEqual(code, 0, err)
        self.assertTrue((written / 'bundle' / 'SP11' / 'GUEST.DAT').is_file() or any(written.rglob('GUEST.DAT')))


PAIR = ROOT / 'build/r13/rootpair'
FACTS = ROOT / 'build/m1p/sp11-facts-rung10.local.json'
J700 = ROOT / 'downloads/extracted/j700ap'
ISC_IMAGE = ROOT / 'build/r14/isc-three-role.apfs'
INPUTS = (J700 / 'kernelcache.release.mac17p', J700 / 'basesystem-26A428.apfs', J700 / 'basesystem-26A428.root-hash', J700 / 'basesystem-26A428.trustcache', PAIR / 'SNAPMON-M1-ACK.BIN',
          PAIR / 'SNAPREP-M1-ACK.BIN', PAIR / 'build-report.json', FACTS, ISC_IMAGE)


@unittest.skipUnless(all(p.is_file() for p in INPUTS), 'the real inputs are local and git-ignored: the J700 kernelcache and root, the root-carry pair, the facts of rung 10 and the ISC image')
class Real(unittest.TestCase):
    def test_the_real_isc_image_has_the_three_roles_the_form_size_and_a_sparse_body(self):
        info = root.check_isc(ISC_IMAGE)
        self.assertEqual((info['bytes'], info['roles']), (r10b.ISC_BYTES, [16, 256, 320]))
        self.assertEqual(info['sha256'], hashlib.sha256(ISC_IMAGE.read_bytes()).hexdigest() if info['bytes'] < 2 << 30 else info['sha256'])


class Hygiene(unittest.TestCase):
    def test_the_files_are_under_the_size_of_a_file_of_this_tool_and_the_cap_is_unchanged(self):
        for rel in ('tools/sp11_stick_bundle_rung10.py', 'tools/sp11_stick_bundle_panel_rung10.py', 'tools/sp11_stick_bundle_panel_rung10d.py', 'tools/sp11_stick_bundle_root.py',
                    'tools/sp11_stick_bundle_rootbundle.py', 'tools/sp11_stick_bundle_build.py', 'tools/sp11_stick_bundle_panel.py', 'tools/sp11_stick_bundle_panel_rungs.py', 'tools/test-sp11-stick-monitor-rung10.py',
                    'tools/test-sp11-stick-monitor-rung10-words.py', 'tools/sp11_stick_bundle_record_m1_guide.py'):
            data = (ROOT / rel).read_bytes()
            with self.subTest(rel):
                self.assertLessEqual((len(data), data.count(b'\n')), (51_200, 1_500))
        self.assertEqual(bb.AUTHORISATION_CAP, 13 << 14, 'unchanged by rung 10: its record is built from rung 9\'s words and fits under the held limit')


if __name__ == '__main__':
    unittest.main()
