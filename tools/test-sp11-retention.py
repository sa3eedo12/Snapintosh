#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of the retention probe's host library and plan builder (tools/sp11_retention.py, tools/sp11_retention_plan.py): the page and
configuration layouts, the WHOLE, PARTIAL and NONE rule against an oracle written here in the payload's own style and against the payload's
reader itself, the candidate pages against memory maps, the operator actions and the authorisation record. No hardware, no emulator, nothing
under /Volumes or /dev; scratch files are below build/.

    python3 tools/test-sp11-retention.py

  * PageFormat: a built page is WHOLE; the layout of every header word; one deliberately damaged page per kind (a flipped body bit, a header
    word, the CRC word alone, an all-zero page, all ones, noise, another page's header, a lost header with too few or enough words of one epoch,
    two epochs, a torn page): the CRC check, the header check and the body check each have a page that only they reject;
  * Oracle: 600 random pages, the library against an independent loop-for-loop transcription of the payload's reader;
  * Config: the file round trip and every refusal reason, each with a file that breaks only that rule;
  * MemoryMap: the probe's map format, the page checks and, when downloads/sp11/x*-run*/memmap.txt exist, the unit pages against all six;
  * PlanBuilder: what the builder refuses (SYSTEM_RESET2 on the unit, an unattended plan without the operator's flag, a flag without that
    plan), the files it writes, the operator actions of both plans and the authorisation record, pinned by its SHA-256;
  * PayloadReader: the library against the payload's own reader, on a corpus of 3-page cases, through the q1n1 tree's host program
    (SKIP unless Q1N1_SP11_TREE names a q1n1 checkout and clang is installed).

SP11_RETENTION_DIR names a directory with modified copies of the modules, for tools/test-sp11-retention-mutants.py."""
import contextlib
import hashlib
import io
import os
from pathlib import Path
import random
import re
import shutil
import struct
import subprocess
import sys
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
TOOLS = Path(os.environ.get('SP11_RETENTION_DIR') or Path(__file__).resolve().parent)
BUILD = ROOT / 'build' / 'test-sp11-retention' / (Path(os.environ['SP11_RETENTION_DIR']).name if os.environ.get('SP11_RETENTION_DIR') else 'run')
CORPUS_BINARY = ROOT / 'build' / 'test-sp11-retention' / 'retprobe-corpus'      # shared by the runs of the mutation runner: the C program is never mutated
sys.dont_write_bytecode = True
sys.path.insert(0, str(TOOLS))
sys.path.append(str(Path(__file__).resolve().parent))         # the fixtures are not among the modules a mutant copy replaces
import sp11_autorun as autorun  # noqa: E402
import sp11_autorun_fixtures as fx  # noqa: E402
import sp11_retention as R  # noqa: E402
import sp11_retention_plan as P  # noqa: E402

PAGES = (0x9C000000, 0xF8400000, 0xFFFF00000)
MAPS = sorted((ROOT / 'downloads' / 'sp11').glob('x*-run*/memmap.txt'))


def page(index=0, counter=5, **kw):
    return R.build_page(index, PAGES[index], counter, **kw)


def words(blob):
    return list(struct.unpack(f'<{len(blob) // 4}I', blob))


def pack(ws):
    return struct.pack(f'<{len(ws)}I', *ws)


def with_word(blob, n, value):
    ws = words(blob)
    ws[n] = value
    return pack(ws)


def oracle(blob, index, address):
    """The payload's reader (payloads/retprobe.c classify), transcribed loop for loop and sharing nothing with the library: (state, counter,
    wrong words, crc ok)."""
    w = words(blob)
    crc = ~0 & 0xFFFFFFFF
    for n, word in enumerate(w):
        word = 0 if n == 31 else word
        crc ^= word
        for _ in range(32):
            crc = (crc >> 1) ^ (0xEDB88320 & (0 - (crc & 1) & 0xFFFFFFFF))
    crc_ok = (~crc & 0xFFFFFFFF) == w[31]
    counter, bad = w[6], 0
    if w[0] == 0x43544552 and w[1] == 0x31304E41 and w[2] == 1 and w[3] == index and w[4] == address & 0xFFFFFFFF and w[5] == address >> 32 \
            and 1 <= counter <= 0xFFFFF:
        for i in range(992):
            bad += w[32 + i] != (counter << 12 | index << 10 | i)
        return ('WHOLE' if crc_ok and not bad else 'PARTIAL', counter, bad, crc_ok)
    epoch = agreed = 0
    for i in range(992):
        key = w[32 + i] >> 10
        if w[32 + i] & 0x3FF != i:
            continue
        same = sum(1 for j in range(992) if w[32 + j] & 0x3FF == j and w[32 + j] >> 10 == key)
        if same > agreed or (same == agreed and key < epoch):
            agreed, epoch = same, key
    matches = sum(1 for i in range(992) if w[32 + i] == (epoch << 10 | i))
    return ('PARTIAL' if matches >= 16 else 'NONE', 0, 992 - matches, crc_ok)


def damaged_pages(rng, addresses=PAGES, slot=None):
    """(name, bytes, index, address) of a page of every kind the corpus and the oracle test use, written for `addresses`."""
    k = rng.randrange(3) if slot is None else slot
    base = R.build_page(k, addresses[k], rng.randrange(1, 40), rng.randrange(0, 40), rng.getrandbits(48), 19200000, 0, 0xFFFFFFFF, rng.randrange(3),
                        rng.randrange(3), (rng.randrange(4), rng.randrange(4), rng.randrange(4)), (7, 0x9C000000, 0x4000000))
    kind = rng.randrange(11)
    ws = words(base)
    if kind == 1:
        for _ in range(rng.randrange(1, 9)):
            ws[rng.randrange(1024)] ^= 1 << rng.randrange(32)
    elif kind == 2:
        start = rng.randrange(1024)
        for n in range(start, min(1024, start + rng.randrange(1, 600))):
            ws[n] = 0
    elif kind == 3:
        ws = [rng.getrandbits(32) for _ in range(1024)]
    elif kind == 4:
        ws = [rng.choice((0, 0xFFFFFFFF))] * 1024
    elif kind == 5:
        for n in range(32, 1024):
            if rng.randrange(3):
                ws[n] = rng.getrandbits(32)
    elif kind == 6:
        ws = words(R.build_page((k + 1) % 3, addresses[(k + 1) % 3], ws[6]))
    elif kind == 7:
        for n in range(32):
            if rng.randrange(2):
                ws[n] = rng.getrandbits(32)
    elif kind == 8:
        other = words(R.build_page(k, addresses[k], ws[6] + rng.randrange(1, 4)))
        cut = rng.randrange(32, 1024)
        ws[cut:] = other[cut:]
    elif kind == 9:
        for n in range(rng.randrange(33, 1024), 1024):
            ws[n] = 0
    return (f'kind{kind}', pack(ws), k, addresses[k])


class PageFormat(unittest.TestCase):
    def test_a_built_page_is_whole_and_the_layout_is_the_documented_one(self):
        blob = R.build_page(1, PAGES[1], 7, 6, 0x123456789ABC, 19200000, 0, 0xFFFFFFFF, 1, 1, (2, 1, 0), (7, 0xE69C0000, 0x11B40000))
        self.assertEqual(len(blob), 4096)
        self.assertEqual(blob[:8], b'RETCAN01')
        w = words(blob)
        self.assertEqual(w[2:8], [1, 1, PAGES[1] & 0xFFFFFFFF, PAGES[1] >> 32, 7, 6])
        self.assertEqual((w[8], w[9], w[10], w[11], w[12]), (0x56789ABC, 0x1234, 19200000, 0, 0xFFFFFFFF))
        self.assertEqual(w[13], 1 | 1 << 8 | 2 << 16 | 1 << 24)
        self.assertEqual(w[14], 0 | 1 << 8)
        self.assertEqual(w[15:20], [7, 0xE69C0000, 0, 0x11B40000, 0])
        self.assertEqual(w[20:31], [0] * 11)
        self.assertEqual(w[31], zlib.crc32(blob[:124] + bytes(4) + blob[128:]))
        self.assertEqual(w[32:36], [7 << 12 | 1 << 10 | i for i in range(4)])
        self.assertEqual(w[1023], 7 << 12 | 1 << 10 | 991)
        verdict = R.classify_page(blob, 1, PAGES[1])
        self.assertEqual((verdict.state, verdict.named, verdict.counter, verdict.crc_ok, verdict.wrong_words), ('WHOLE', True, 7, True, 0))
        self.assertEqual(verdict.header['previous_states'], (2, 1, 0))
        self.assertEqual(verdict.header['timestamp'], 0x123456789ABC)
        self.assertEqual(verdict.intact, ((128, 4096),))

    def test_the_crc_alone_decides_a_page_whose_body_is_intact(self):
        for what, damaged in (('a header word that is not named by the rule', with_word(page(), 7, 99)),
                              ('a reserved header word', with_word(page(), 25, 1)),
                              ('the CRC word itself', with_word(page(), 31, words(page())[31] ^ 1))):
            with self.subTest(what):
                verdict = R.classify_page(damaged, 0, PAGES[0])
                self.assertEqual((verdict.state, verdict.named, verdict.crc_ok, verdict.wrong_words, verdict.damaged), ('PARTIAL', True, False, 0, ()))

    def test_the_body_alone_decides_a_page_whose_crc_was_forged(self):
        ws = words(page())
        ws[100] ^= 0x10                                        # a wrong body word...
        forged = bytearray(pack(ws))
        forged[124:128] = struct.pack('<I', R.page_crc(bytes(forged)))     # ...under a CRC recomputed to match
        verdict = R.classify_page(bytes(forged), 0, PAGES[0])
        self.assertEqual((verdict.state, verdict.crc_ok, verdict.wrong_words, verdict.damaged), ('PARTIAL', True, 1, ((400, 404),)))

    def test_each_part_of_the_header_that_names_a_page_is_checked(self):
        for what, damaged in (('the magic', with_word(page(), 0, 0)), ('the second half of the magic', with_word(page(), 1, 0)),
                              ('the version', with_word(page(), 2, 2)), ('the index', with_word(page(), 3, 1)),
                              ('the address, low word', with_word(page(), 4, PAGES[0] + 4096 & 0xFFFFFFFF)),
                              ('the address, high word', with_word(page(), 5, 1)), ('a counter of zero', with_word(page(), 6, 0)),
                              ('a counter above the limit', with_word(page(), 6, 0x100000))):
            with self.subTest(what):
                verdict = R.classify_page(damaged, 0, PAGES[0])
                self.assertFalse(verdict.named)
                self.assertEqual(verdict.state, oracle(damaged, 0, PAGES[0])[0])

    def test_pages_of_no_use_are_none_and_a_lost_header_leaves_the_body_to_speak(self):
        for what, blob in (('all zero', bytes(4096)), ('all ones', b'\xff' * 4096), ('noise', random.Random(5).randbytes(4096))):
            with self.subTest(what):
                verdict = R.classify_page(blob, 0, PAGES[0])
                self.assertEqual((verdict.state, verdict.named, verdict.counter, verdict.intact, verdict.damaged), ('NONE', False, 0, (), ()))
        ws = words(page(0, 9))
        ws[:32] = [0] * 32
        lost = R.classify_page(pack(ws), 0, PAGES[0])
        self.assertEqual((lost.state, lost.named, lost.epoch, lost.wrong_words, lost.intact), ('PARTIAL', False, (9, 0), 0, ((128, 4096),)))
        for matching, state in ((15, 'NONE'), (16, 'PARTIAL')):
            sparse = [0] * 32 + [0xFFFFFFFF] * 992
            for i in range(matching):
                sparse[32 + i * 7] = 9 << 12 | 0 << 10 | i * 7
            with self.subTest(matching=matching):
                self.assertEqual(R.classify_page(pack(sparse), 0, PAGES[0]).state, state)

    def test_two_epochs_the_plurality_wins_and_a_tie_goes_to_the_smaller(self):
        a, b = words(page(0, 5)), words(page(0, 6))
        for cut, wanted in ((400, (6, 0)), (700, (5, 0))):
            mixed = pack([0] * 32 + a[32:cut] + b[cut:])
            verdict = R.classify_page(mixed, 0, PAGES[0])
            self.assertEqual((verdict.state, verdict.epoch), ('PARTIAL', wanted), cut)
        tie = pack([0] * 32 + a[32:528] + b[528:])
        self.assertEqual(R.classify_page(tie, 0, PAGES[0]).epoch, (5, 0))
        self.assertEqual(R.classify_page(tie, 0, PAGES[0]).wrong_words, 496)

    def test_a_torn_page_says_which_bytes_survive(self):
        ws = words(page(2, 3))
        ws[600:700] = [0] * 100
        verdict = R.classify_page(pack(ws), 2, PAGES[2])
        self.assertEqual((verdict.state, verdict.wrong_words, verdict.damaged, verdict.intact), ('PARTIAL', 100, ((2400, 2800),), ((128, 2400), (2800, 4096))))

    def test_another_pages_content_is_not_this_page(self):
        verdict = R.classify_page(page(1, 4), 0, PAGES[0])        # page 1 read where page 0 was written
        self.assertEqual((verdict.state, verdict.named, verdict.epoch), ('PARTIAL', False, (4, 1)))

    def test_the_boot_counter_is_the_highest_whole_counter_plus_one(self):
        whole = lambda c: R.classify_page(page(0, c), 0, PAGES[0])  # noqa: E731
        none = R.classify_page(bytes(4096), 0, PAGES[0])
        partial = R.classify_page(with_word(page(0, 40), 7, 1), 0, PAGES[0])       # the header word 7 changed under an unchanged CRC word: PARTIAL
        self.assertEqual(R.next_boot_counter([none, none, none]), 1)
        self.assertEqual(R.next_boot_counter([whole(3), none, whole(7)]), 8)
        self.assertEqual(R.next_boot_counter([partial, whole(2), none]), 3, 'a PARTIAL page does not count')

    def test_bad_arguments_are_refused(self):
        for kw in ({'index': 3, 'counter': 1}, {'index': 0, 'counter': 0}, {'index': 0, 'counter': 0x100000}, {'index': -1, 'counter': 1}):
            with self.subTest(kw), self.assertRaises(ValueError):
                R.build_page(address=0x1000, **kw)
        with self.assertRaises(ValueError):
            R.classify_page(bytes(4095), 0, 0)


class Oracle(unittest.TestCase):
    def test_the_library_agrees_with_the_payloads_reader_on_random_pages(self):
        rng = random.Random(20261116)
        seen = {}
        for _ in range(600):
            kind, blob, index, address = damaged_pages(rng)
            got = R.classify_page(blob, index, address)
            self.assertEqual((got.state, got.counter if got.state == 'WHOLE' else 0, got.wrong_words, got.crc_ok),
                             (lambda s, c, b, ok: (s, c if s == 'WHOLE' else 0, b, ok))(*oracle(blob, index, address)), kind)
            seen[(kind, got.state)] = seen.get((kind, got.state), 0) + 1
        self.assertGreaterEqual({state for _, state in seen}, {'WHOLE', 'PARTIAL', 'NONE'}, 'the generator reaches every state')
        self.assertGreaterEqual(len({kind for kind, _ in seen}), 11)


class Config(unittest.TestCase):
    def test_the_file_round_trips_and_has_the_documented_layout(self):
        blob = R.config_bytes(1, 8, PAGES)
        self.assertEqual(len(blob), 64)
        self.assertEqual(blob[:8], b'RETCFG01')
        self.assertEqual(words(blob)[2:6], [1, 1, 8, 3])
        self.assertEqual(struct.unpack_from('<3Q', blob, 24), PAGES)
        self.assertEqual(words(blob)[15], zlib.crc32(blob[:60]))
        self.assertEqual(R.parse_config(blob), {'path': 1, 'countdown': 8, 'pages': PAGES})

    def broken(self, n, value, fix_crc=True):
        ws = words(R.config_bytes(1, 8, PAGES))
        ws[n] = value
        blob = bytearray(pack(ws))
        if fix_crc:
            blob[60:64] = struct.pack('<I', zlib.crc32(bytes(blob[:60])))
        return bytes(blob)

    def test_every_refusal_has_a_file_that_breaks_only_that_rule(self):
        good = R.config_bytes(1, 8, PAGES)
        cases = [('BAD_SIZE', good[:63]), ('BAD_SIZE', good + b'\0'), ('BAD_MAGIC', b'RETCFG02' + good[8:]),
                 ('BAD_CRC', self.broken(4, 9, fix_crc=False)), ('BAD_VERSION', self.broken(2, 2)), ('BAD_PATH', self.broken(3, 3)),
                 ('BAD_COUNTDOWN', self.broken(4, 0)), ('BAD_COUNTDOWN', self.broken(4, 61)), ('BAD_PAGE_COUNT', self.broken(5, 4)),
                 ('RESERVED_NOT_ZERO', self.broken(12, 1)), ('RESERVED_NOT_ZERO', self.broken(14, 1)),
                 ('PAGE_UNALIGNED', self.broken(6, 0x9C000800)), ('PAGE_RANGE', self.broken(8, 0)),
                 ('PAGE_RANGE', self.broken(11, 0x10000)), ('PAGES_NOT_DISTINCT', self.broken(8, PAGES[0]))]
        for reason, blob in cases:
            with self.subTest(reason=reason, size=len(blob)), self.assertRaises(R.ConfigError) as caught:
                R.parse_config(blob)
            self.assertEqual(caught.exception.reason, reason)

    def test_the_boundaries_that_are_allowed_are_accepted(self):
        for countdown in (1, 60):
            self.assertEqual(R.parse_config(R.config_bytes(0, countdown, PAGES))['countdown'], countdown)
        for path in (0, 1, 2):
            self.assertEqual(R.parse_config(R.config_bytes(path, 8, PAGES))['path'], path)
        top = (1 << 48) - 4096
        self.assertEqual(R.parse_config(R.config_bytes(0, 1, (4096, 8192, top)))['pages'][2], top)

    def test_config_bytes_refuses_what_it_would_not_write(self):
        for args in ((3, 8, PAGES), (1, 0, PAGES), (1, 61, PAGES), (1, 8, PAGES[:2]), (1, 8, (PAGES[0], PAGES[0], PAGES[1])), (1, 8, (0x1001, 0x2000, 0x3000))):
            with self.subTest(args), self.assertRaises(R.ConfigError):
                R.config_bytes(*args)


class MemoryMap(unittest.TestCase):
    TEXT = ('# SP11PROBE memmap v1\n# idx type phys virt pages attr name\n'
            '0001 07 0000000091480000 0000000000000000 0000000000015380 000000000000100F CONV\n'
            '0002 04 00000000A6800000 0000000000000000 0000000000000200 000000000000100F DATA\n'
            '0003 07 00000000A8000000 0000000000000000 0000000000024E65 000000000000100F CONV\n'
            '0004 07 0000000880000000 0000000000000000 0000000000780000 000000000000100F CONV\n')

    def test_the_probe_format_is_parsed(self):
        extents = R.parse_probe_memmap(self.TEXT)
        self.assertEqual([(e.kind, e.start, e.end, e.conventional) for e in extents][:2],
                         [('07', 0x91480000, 0x91480000 + 0x15380 * 4096, True), ('04', 0xA6800000, 0xA6800000 + 0x200 * 4096, False)])
        self.assertEqual(len(extents), 4)

    def test_a_page_must_be_inside_one_conventional_extent(self):
        extents = R.parse_probe_memmap(self.TEXT)
        self.assertEqual(R.page_problems((0x9C000000, 0x880000000, 0xA8000000, 0xA67FF000), extents), [])
        for page_, why in ((0x10000, 'is in no extent'), (0xA6800000, 'overlaps an extent of type 04'), (0xD0000000, 'is in no extent')):
            problems = R.page_problems((page_,), extents)
            self.assertEqual(len(problems), 1, page_)
            self.assertIn(why, problems[0])
        straddle = R.parse_probe_memmap('0001 07 0000000000000000 0000000000000000 0000000000000001 000000000000100F CONV\n'
                                        '0002 07 0000000000001000 0000000000000000 0000000000000001 000000000000100F CONV\n')
        self.assertEqual(R.page_problems((0x800,), straddle), ['0x800 straddles extents'])

    def test_the_unit_pages_are_conventional_in_every_local_map(self):
        if not MAPS:
            self.skipTest('no downloads/sp11/x*-run*/memmap.txt: nothing to check the unit pages against')
        self.assertEqual(len(MAPS), 6)
        for path in MAPS:
            with self.subTest(path.parent.name):
                text = path.read_text(encoding='ascii')
                rows = [line for line in text.splitlines() if line.strip() and not line.startswith('#')]
                declared = int(re.search(r'count=([0-9]+)', text).group(1))
                extents = R.parse_probe_memmap(text)
                self.assertEqual((len(extents), len(rows)), (declared, declared), 'every descriptor row is read, those named BS DATA and RT DATA too')
                self.assertGreater(sum(' ' in row.split(None, 6)[6] for row in rows), 100, 'the maps do have rows whose name has a space')
                for page_ in R.UNIT_PAGES:
                    holding = [e for e in extents if e.start < page_ + 4096 and page_ < e.end]
                    self.assertEqual([(e.kind, e.start <= page_ and page_ + 4096 <= e.end) for e in holding], [('07', True)], hex(page_))
                self.assertEqual(R.page_problems(R.UNIT_PAGES, extents), [])
        faulty = R.parse_probe_memmap(MAPS[0].read_text(encoding='ascii'))
        self.assertNotEqual(R.page_problems((0x9C000000, 0xF8400000, 0xE4800000), faulty), [], 'the panel region is reserved, not conventional')

    SPACED = ('# SP11PROBE memmap v1\n# descriptor_size=48 descriptor_version=1 count=5\n# idx type phys virt pages attr name\n'
              '0000 07 0000000091480000 0000000000000000 0000000000015380 000000000000100F CONV\n'
              '0001 04 00000000A6800000 0000000000000000 0000000000000200 000000000000100F BS DATA\n'
              '0002 06 00000000A6A00000 0000000000000000 0000000000000010 800000000000100F RT DATA\n'
              '0003 01 00000000A6A10000 0000000000000000 0000000000000010 000000000000100F LDR CODE\n'
              '0004 09 00000000A6A20000 0000000000000000 0000000000000010 000000000000100F ACPI RECLAIM\n')

    def test_rows_whose_names_have_spaces_are_read(self):
        extents = R.parse_probe_memmap(self.SPACED)
        self.assertEqual([(e.kind, e.start, e.conventional) for e in extents],
                         [('07', 0x91480000, True), ('04', 0xA6800000, False), ('06', 0xA6A00000, False), ('01', 0xA6A10000, False), ('09', 0xA6A20000, False)])
        self.assertEqual(extents[1].end, 0xA6800000 + 0x200 * 4096)

    def test_a_firmware_row_with_a_spaced_name_over_a_page_is_refused(self):
        faulty = ('# SP11PROBE memmap v1\n# descriptor_size=48 descriptor_version=1 count=2\n'
                  '0000 07 0000000091480000 0000000000000000 0000000000015380 000000000000100F CONV\n'
                  '0001 04 000000009C000000 0000000000000000 0000000000000010 000000000000100F BS DATA\n')
        problems = R.page_problems((0x9C000000, 0x9D000000), R.parse_probe_memmap(faulty))
        self.assertEqual(len(problems), 1, 'the page under the BS DATA row is refused and the page beside it is not')
        self.assertEqual(problems[0], '0x9C000000 overlaps an extent of type 04')
        for kind, name in (('06', 'RT DATA'), ('01', 'LDR CODE'), ('09', 'ACPI RECLAIM')):
            row = f'0001 {kind} 000000009C000000 0000000000000000 0000000000000010 000000000000100F {name}\n'
            self.assertEqual(R.page_problems((0x9C000000,), R.parse_probe_memmap(faulty.split('0001 04')[0] + row)), [f'0x9C000000 overlaps an extent of type {kind}'])

    def test_a_map_that_cannot_be_read_in_full_is_refused(self):
        row = '0001 07 0000000091480000 0000000000000000 0000000000015380 000000000000100F CONV\n'
        cases = {
            'a line that is not a row': self.SPACED + 'stray text\n',
            'a row with too few fields': row + '0002 04 00000000A6800000 0000000000000000 0000000000000200\n',
            'a field that is not hexadecimal': row + '0002 04 00000000A68000ZZ 0000000000000000 0000000000000200 000000000000100F BS DATA\n',
            'a row with no pages': row + '0002 04 00000000A6800000 0000000000000000 0000000000000000 000000000000100F BS DATA\n',
            'a header count above the rows': '# descriptor_size=48 descriptor_version=1 count=3 map_size=144\n' + row * 2,
            'a header count below the rows': '# descriptor_size=48 descriptor_version=1 count=1 map_size=48\n' + row * 2,
        }
        for what, text in cases.items():
            with self.subTest(what), self.assertRaises(R.MapError):
                R.parse_probe_memmap(text)
        self.assertEqual(len(R.parse_probe_memmap('# descriptor_size=48 descriptor_version=1 count=2 map_size=96\n' + row * 2)), 2)

    def test_the_unit_pages_are_distinct_aligned_and_above_nothing_the_wrapper_writes(self):
        self.assertEqual(len(set(R.UNIT_PAGES)), 3)
        self.assertTrue(all(p % 4096 == 0 for p in R.UNIT_PAGES))
        window = (0x880000000, 0x880000000 + 0x2A000000)           # the wrapper's placement window, docs/SP11-MONITOR-PORT.md section 5
        self.assertTrue(all(not window[0] <= p < window[1] for p in R.UNIT_PAGES))
        self.assertEqual(R.UNIT_PAGES[2] + 0x100000, 0x1000000000, 'the first page of the top MiB of the 30 GiB bank')


class PlanBuilder(unittest.TestCase):
    def setUp(self):
        self.out = BUILD / 'plan'
        shutil.rmtree(self.out, ignore_errors=True)
        self.payload = b'\x5f\x24\x03\xd5' + bytes(range(256)) * 3

    def refusal(self, **kw):
        args = {'payload': self.payload, 'path': 'psci-reset', 'countdown': 8, 'pages': PAGES, 'out_dir': self.out}
        args.update(kw)
        with self.assertRaises(P.PlanRefusal) as caught:
            P.build(**args)
        self.assertFalse(self.out.exists(), 'a refused request wrote something')
        return caught.exception.reason

    def test_what_the_builder_refuses_and_writes_nothing(self):
        self.assertEqual(self.refusal(path='psci-reset2'), 'path_not_implemented_on_the_unit')
        self.assertEqual(self.refusal(unattended=True), 'unattended_not_authorised')
        self.assertEqual(self.refusal(stick_first_authorised=True), 'stick_first_flag_without_unattended_plan')
        self.assertEqual(self.refusal(unattended=True, stick_first_authorised=True, path='none'), 'unattended_without_reset')
        self.assertEqual(self.refusal(path='warm'), 'path_unknown')
        self.assertEqual(self.refusal(countdown=0), 'config_refused')
        self.assertEqual(self.refusal(pages=(PAGES[0], PAGES[0], PAGES[1])), 'config_refused')
        self.assertEqual(self.refusal(slbounce='SLBOUNCE-OLD-NOSWEEP.EFI'), 'step_slbounce_banned')
        self.assertEqual(self.refusal(payload=b''), 'manifest_size_out_of_range', 'an empty payload cannot be listed in a manifest')

    def test_the_request_the_unit_can_honour_is_built_and_the_files_are_written(self):
        built = P.build(self.payload, 'psci-reset', 8, PAGES, self.out)
        self.assertEqual(built['plan'], 'retention-m10')
        self.assertEqual(built['steps'], ['SLBOUNCE-DEVHE-MARK.EFI Q1N1-PAYLOAD.EFI --el2', 'RETENTION-EXTRACT 0x9C000000 0xF8400000 0xFFFF00000'])
        self.assertEqual((self.out / 'RETPATH.DAT').read_bytes(), R.config_bytes(1, 8, PAGES))
        record = (self.out / 'RECORD.TXT').read_bytes()
        self.assertEqual(hashlib.sha256(record).hexdigest(), built['record_sha256'])
        self.assertEqual(built['payload_sha256'], hashlib.sha256(self.payload).hexdigest())
        autorun.build_plan(built['plan'], built['steps'], {'SLBOUNCE-DEVHE-MARK.EFI': 'a' * 64, 'Q1N1-PAYLOAD.EFI': 'b' * 64})
        unattended = P.build(self.payload, 'psci-reset', 8, PAGES, self.out, unattended=True, stick_first_authorised=True)
        self.assertEqual(unattended['plan'], 'retention-m10-auto')
        self.assertNotEqual(unattended['record_sha256'], built['record_sha256'])
        expected = P.build(self.payload, 'psci-reset2', 8, PAGES, self.out, expect_refusal=True)
        self.assertEqual(expected['plan'], 'retention-m10')

    def test_the_manifest_lists_the_payload_and_the_configuration_and_nothing_else_and_the_record_pins_it(self):
        built = P.build(self.payload, 'psci-reset', 8, PAGES, self.out)
        config = R.config_bytes(1, 8, PAGES)
        written = (self.out / 'PAYLOAD.MAN').read_bytes()
        self.assertEqual(written, fx.listing({'RETPROBE.BIN': self.payload, 'RETPATH.DAT': config}), 'the bytes an independent writer makes for the same two files')
        self.assertEqual(written, built['manifest'])
        self.assertEqual([(e.path, e.size, e.sha256) for e in autorun.parse_payload_manifest(written)],
                         [('\\SP11\\RETPROBE.BIN', len(self.payload), hashlib.sha256(self.payload).hexdigest()),
                          ('\\SP11\\RETPATH.DAT', 64, hashlib.sha256(config).hexdigest())])
        self.assertEqual(built['manifest_sha256'], hashlib.sha256(written).hexdigest())
        record = (self.out / 'RECORD.TXT').read_text(encoding='ascii')
        self.assertIn(f'PAYLOAD.MAN, {len(written)} bytes, SHA-256 {built["manifest_sha256"]}', record)
        self.assertIn('it lists RETPROBE.BIN and RETPATH.DAT and nothing else', record)
        other = P.build(self.payload + b'\0', 'psci-reset', 8, PAGES, self.out)
        self.assertNotEqual(other['manifest_sha256'], built['manifest_sha256'])
        self.assertNotEqual(other['record_sha256'], built['record_sha256'])

    def test_a_manifest_that_lists_one_more_file_is_the_one_the_stager_would_refuse(self):
        """The builder's own manifest passes the stager's rules for a retention plan; the same rules refuse an OTHER.BIN, which the runner would call."""
        files = {'RETPROBE.BIN': self.payload, 'RETPATH.DAT': R.config_bytes(1, 8, PAGES), 'OTHER.BIN': b'\x1f\x20\x03\xd5' * 8}
        plan = autorun.build_plan('retention-m10', P.step_texts(PAGES), {'SLBOUNCE-DEVHE-MARK.EFI': 'a' * 64, 'Q1N1-PAYLOAD.EFI': 'b' * 64})
        staged = [autorun.DataFile(name, len(blob), hashlib.sha256(blob).hexdigest()) for name, blob in {'PAYLOAD.MAN': fx.listing(files), **files}.items()]
        with self.assertRaises(autorun.PlanError) as caught:
            autorun.attach_data(plan, staged, fx.listing(files))
        self.assertEqual(caught.exception.reason, 'retention_data_names')

    def cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = P.main([str(a) for a in args])
        return code, out.getvalue()

    def test_the_command_line_writes_three_files_and_prints_the_stage_command_with_the_hashes_it_made(self):
        payload = BUILD / 'cli' / 'retprobe.bin'
        payload.parent.mkdir(parents=True, exist_ok=True)
        payload.write_bytes(self.payload)
        code, shown = self.cli('--payload', payload, '--path', 'psci-reset', '--out', self.out)
        self.assertEqual(code, 0, shown)
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ['PAYLOAD.MAN', 'RECORD.TXT', 'RETPATH.DAT'])
        for name in ('PAYLOAD.MAN', 'RETPATH.DAT'):
            digest = hashlib.sha256((self.out / name).read_bytes()).hexdigest()
            self.assertIn(f'--data {name} {self.out / name} {digest}', shown)
        self.assertIn(f'--data RETPROBE.BIN {payload} {hashlib.sha256(self.payload).hexdigest()}', shown)
        record = (self.out / 'RECORD.TXT').read_bytes()
        self.assertIn(f'record {len(record)} bytes SHA-256 {hashlib.sha256(record).hexdigest()}', shown)

    def test_the_command_line_reads_every_row_of_a_memory_map_and_refuses_one_it_cannot_read(self):
        payload = BUILD / 'cli' / 'retprobe.bin'
        payload.parent.mkdir(parents=True, exist_ok=True)
        payload.write_bytes(self.payload)
        rows = ('0000 07 000000009C000000 0000000000000000 0000000000000100 000000000000100F CONV\n'
                '0001 07 00000000F8400000 0000000000000000 0000000000000100 000000000000100F CONV\n'
                '0002 07 0000000FFFF00000 0000000000000000 0000000000000100 000000000000100F CONV\n')
        maps = BUILD / 'cli' / 'maps'
        maps.mkdir(exist_ok=True)
        for name, text in (('good.txt', rows), ('spaced.txt', rows + '0003 04 000000009C000000 0000000000000000 0000000000000010 000000000000100F BS DATA\n'),
                           ('stray.txt', rows + 'Type Start End\n')):
            (maps / name).write_text(text, encoding='ascii')
        code, shown = self.cli('--payload', payload, '--path', 'psci-reset', '--memmap', maps / 'good.txt', '--out', self.out)
        self.assertEqual(code, 0, shown)
        shutil.rmtree(self.out)
        shown_by_map = {}
        for name, reason in (('spaced.txt', 'pages_not_in_conventional_memory'), ('stray.txt', 'memory_map_unreadable')):
            with self.subTest(name):
                code, shown_by_map[name] = self.cli('--payload', payload, '--path', 'psci-reset', '--memmap', maps / name, '--out', self.out)
                self.assertEqual((code, shown_by_map[name].split(':')[0]), (2, f'REFUSED {reason}'), shown_by_map[name])
                self.assertFalse(self.out.exists(), 'a refused request wrote something')
        self.assertIn('0x9C000000 overlaps an extent of type 04', shown_by_map['spaced.txt'], 'the row named BS DATA is read and the page under it is refused')

    def test_the_builders_own_check_refuses_before_the_plan_rules_are_asked(self):
        reasons = [('psci-reset2', 8, PAGES, False, False, 'path_not_implemented_on_the_unit'), ('psci-reset', 8, PAGES, True, False, 'unattended_not_authorised'),
                   ('psci-reset', 8, PAGES, False, True, 'stick_first_flag_without_unattended_plan'), ('none', 8, PAGES, True, True, 'unattended_without_reset'),
                   ('warm', 8, PAGES, False, False, 'path_unknown'), ('psci-reset', 0, PAGES, False, False, 'config_refused'),
                   ('psci-reset', 8, (PAGES[0], PAGES[0], PAGES[2]), False, False, 'config_refused')]
        for path, countdown, pages, unattended, flag, reason in reasons:
            with self.subTest(reason=reason, path=path), self.assertRaises(P.PlanRefusal) as caught:
                P.check_request(path, countdown, pages, unattended, flag)
            self.assertEqual(caught.exception.reason, reason)
        P.check_request('psci-reset', 8, PAGES, False, False)
        P.check_request('psci-reset', 8, PAGES, True, True)
        P.check_request('psci-reset2', 8, PAGES, False, False, expect_refusal=True)

    def test_the_operator_actions_name_what_to_press_wait_for_and_when_to_take_the_stick(self):
        baseline = P.operator_actions('retention-m10', 'psci-reset', 8, False)
        for phrase in ('ACTION 1: start the stick and wait', 'ACTION 2: hold Volume-Down through the restart', 'the line NEXT RESET AFTER 8 S',
                       'HOLD Volume-Down and keep holding it', 'Do NOT\n  power-cycle', 'a power cycle loses what the probe wrote', 'AFTER: take the stick to the Mac',
                       'python3 tools/sp11_retention_check.py stick', 'UNKNOWN until a run: whether the firmware honours a held key',
                       'AUTORUN retention-m10 STEP 2 OF 2: SP11\\RET2.TXT is written'):
            self.assertIn(phrase, baseline)
        self.assertEqual(len(re.findall(r'^ACTION [0-9]:', baseline, re.M)), 2, 'two operator actions')
        flat = ' '.join(baseline.split())
        for phrase in ('A marker left by an earlier run does harm', 'a DONE1.TXT makes step 1 skip, so the probe does not run and step 2 dumps RAM that nothing wrote',
                       'a RET2.TXT makes step 2 refuse and leaves the old result to be read as this one', 'STALE_STEP_MARKER, STALE_RESULT',
                       'The power button is for the end of the run (step 7) and for danger'):
            self.assertIn(phrase, flat)
        unattended = P.operator_actions('retention-m10-auto', 'psci-reset', 8, True)
        self.assertNotIn('HOLD Volume-Down', unattended)
        self.assertNotIn('ACTION 2', unattended)
        self.assertIn('stick is first in the boot order', unattended)
        self.assertIn('put the boot order back', unattended)
        self.assertIn('A marker left by an earlier run does harm', unattended)
        self.assertIn('do NOT power-cycle it (that loses what the probe wrote)', unattended)
        control = P.operator_actions('retention-m10', 'none', 8, False)
        self.assertIn('the negative control', control)
        self.assertNotIn('HOLD Volume-Down', control)

    def record(self, **kw):
        args = dict(plan='retention-m10', payload_sha256='ab' * 32, payload_size=12748, config_sha256='cd' * 32, manifest_sha256='ef' * 32, manifest_size=624,
                    path='psci-reset', countdown=8, pages=PAGES)
        args.update(kw)
        return P.authorisation_record(**args)

    def test_the_authorisation_record_names_what_the_operator_approves(self):
        text = self.record()
        self.assertTrue(text.isascii())
        self.assertLessEqual(max(len(line) for line in text.splitlines()), 135)
        for phrase in ('SHA-256 ' + 'ab' * 32, 'SHA-256 ' + 'cd' * 32, 'PAYLOAD.MAN, 624 bytes, SHA-256 ' + 'ef' * 32, '12748 bytes',
                       'SYSTEM_RESET, the SMC call with function id 0x84000009',
                       'only when PSCI_FEATURES reported that function implemented', 'whether it restarts the unit, and whether DRAM keeps the pages, are UNKNOWN',
                       '0x9C000000, 0xF8400000, 0xFFFF00000', 'The UEFI Shell only', 'step 2 refuses if it exists',
                       'No code at EL2 writes the stick: no USB bring-up and no sector write', 'The internal disk and its partition table, NVRAM and UEFI variables',
                       'any firmware setting (the boot order, Secure Boot)', 'A line RESET RETURNED', 'Hardware NOT RUN', 'a second payload in the same boot'):
            self.assertIn(phrase, text)
        self.assertNotIn('boot order is a persistent firmware setting', text, 'the baseline plan has no such paragraph')
        reset2 = self.record(path='psci-reset2')
        self.assertIn('SYSTEM_RESET2, the SMC call with function id 0xC4000012, reset type 0', reset2)
        none = self.record(path='none')
        self.assertIn('no reset call is made', none)
        self.assertNotIn('It restarts the whole machine', none)
        auto = self.record(plan='retention-m10-auto', unattended=True)
        self.assertIn('The boot order is a persistent firmware setting. This tool does not change it.', auto)
        self.assertIn('no manual restart: the stick is first in the boot order', auto)
        self.assertIn('That line is a record of their decision and not an interlock', ' '.join(auto.split()))
        self.assertEqual(self.record(), text, 'deterministic')
        self.assertNotEqual(self.record(config_sha256='ce' * 32), text)
        self.assertNotEqual(self.record(manifest_sha256='f0' * 32), text, 'the manifest is part of what is approved')

    def test_the_record_says_what_the_review_found_missing(self):
        flat = ' '.join(self.record().split())
        for phrase in (
                "AT S1E2R and AT S1E2W ask EL2's stage 1 about each page and access nothing; they write PAR_EL1, the one system register the payload writes",
                'writes no other system register and no device register',
                "Plain loads that nothing guards: the runner's preload table, the 64-byte configuration file it names, the runner's memory map and, "
                'after the stores, the three pages it wrote',
                "The reset call quiesces no device, so for the stick's FAT and the internal NVMe it acts as a power cut would (INFERRED); the one write to the stick "
                'before it, SP11\\DONE1.TXT, was made at least 5 s earlier',
                'From the reset call until SP11\\RET2.TXT is written, a power cycle loses the pages, so the power button is not the answer to a dark or unexpected screen',
                'Any other surprise before the call (a hang with the page stuck): hold the power button',
                'Danger overrides all of the above',
                'SYSTEM_RESET is the architectural cold-reset call; the warm reset is SYSTEM_RESET2, which the unit answered NOT_SUPPORTED (OBSERVED once; '
                'the PSCI text was not re-read here: INFERRED). So NONE is a likely outcome of this run, and it says nothing is wrong with the probe or the unit',
                "DC CVAC and DC CIVAC reach the point of coherency and no further: whether a system cache (LLCC), a buffer of the memory controller, DRAM's "
                'self-refresh or a scrub of memory at boot keeps or loses a page is not named here',
                "They show that EL2 maps the page, not that TrustZone, an XPU or QHEE leave it alone outside UEFI's memory map: a store the hardware rejected "
                'would be an external abort, which only a power cycle ends'):
            self.assertIn(phrase, flat)
        self.assertNotIn('writes no system register', flat, 'AT writes PAR_EL1')
        none = ' '.join(self.record(path='none').split())
        for absent in ('The reset call quiesces', 'From the reset call until', 'cold-reset call'):
            self.assertNotIn(absent, none, 'a run with no reset call has no such sentence')
        self.assertIn('Danger overrides all of the above', none)
        reset2 = ' '.join(self.record(path='psci-reset2').split())
        self.assertIn('The reset call quiesces no device', reset2)
        self.assertNotIn('cold-reset call', reset2, 'the cold reset is SYSTEM_RESET, not the warm one')

    def test_the_record_text_is_pinned_so_that_a_changed_byte_is_noticed(self):
        text = self.record().encode('ascii')
        self.assertEqual((len(text), hashlib.sha256(text).hexdigest()), RECORD_PIN,
                         'the record text changed: this is another record; update the pin deliberately')


RECORD_PIN = (6667, '2e6845de6770f1d7ed52c1fc67f6b06c546045c8b286da63ea4f4518016298f6')    # (bytes, SHA-256) of record() of PlanBuilder


class PayloadReader(unittest.TestCase):
    """The library against the payload's own reader. The host program of the q1n1 fork reads three pages from a modelled machine and prints,
    per page, the state it decided, the counter of a WHOLE page, the wrong words and the CRC verdict of a PARTIAL one and the boot counter it takes;
    the same pages go through the library. The model's pages have addresses of its own, so the corpus names nominal ones that the program rewrites
    (corpus_fixup in tools/test-q1n1-sp11-payload-retprobe.c)."""
    NOMINAL = tuple(0xFEED00001000 + k * 0x1000 for k in range(3))

    @classmethod
    def setUpClass(cls):
        tree = os.environ.get('Q1N1_SP11_TREE')
        if not tree or not (Path(tree) / 'tools' / 'test-q1n1-sp11-payload-retprobe.c').is_file():
            raise unittest.SkipTest('Q1N1_SP11_TREE does not name a q1n1 checkout with the retprobe host program')
        if not shutil.which('clang'):
            raise unittest.SkipTest('clang is not installed')
        BUILD.mkdir(parents=True, exist_ok=True)
        cls.binary = CORPUS_BINARY
        sources = [Path(tree) / name for name in ('tools/test-q1n1-sp11-payload-retprobe.c', 'tools/test-q1n1-sp11-payload-retprobe-world.h', 'payloads/retprobe.c')]
        if cls.binary.is_file() and cls.binary.stat().st_mtime > max(s.stat().st_mtime for s in sources):
            return
        temporary = cls.binary.with_suffix(f'.{os.getpid()}')
        done = subprocess.run(['clang', '-std=gnu11', '-O1', '-g', '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-Wall', '-Wextra', '-Werror',
                               '-DSNAP_PLATFORM_SP11=1', '-DQ1N1_SP11_PAYLOAD=1', '-I', 'platform/uefi', '-I', 'payloads', '-DQ1N1_PAYLOAD_HOST_TEST=1',
                               'tools/test-q1n1-sp11-payload-retprobe.c', 'platform/uefi/sp11-payload-text.c', 'payloads/retprobe.c', '-o', str(temporary)],
                              cwd=tree, capture_output=True, text=True)
        if done.returncode:
            raise RuntimeError('the host program of the q1n1 fork does not build:\n' + done.stderr[-1500:])
        os.replace(temporary, cls.binary)

    def run_corpus(self, cases):
        path = BUILD / 'corpus.bin'
        path.write_bytes(b''.join(b''.join(case) for case in cases))
        done = subprocess.run([str(self.binary), 'corpus', str(path)], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.splitlines()

    def library(self, case):
        verdicts = [R.classify_page(blob, k, self.NOMINAL[k]) for k, blob in enumerate(case)]
        fields = [f'WHOLE:{v.counter}:0:-' if v.state == 'WHOLE' else 'NONE:0:0:-' if v.state == 'NONE'
                  else f'PARTIAL:0:{v.wrong_words}:{"O" if v.crc_ok else "B"}' for v in verdicts]
        return ' '.join(fields) + f' THIS={R.next_boot_counter(verdicts)}'

    def test_three_page_cases_get_the_same_states_counters_and_damage_counts(self):
        rng = random.Random(7)
        cases = [[damaged_pages(rng, self.NOMINAL, slot=k)[1] for k in range(3)] for _ in range(240)]
        cases += [[R.build_page(k, self.NOMINAL[k], 5 + 2 * k) for k in range(3)], [bytes(4096)] * 3, [b'\xff' * 4096] * 3]
        got = self.run_corpus(cases)
        self.assertEqual(len(got), len(cases))
        states = set()
        for n, (line, case) in enumerate(zip(got, cases)):
            self.assertEqual(line, f'CASE {n} ' + self.library(case), n)
            states |= {field.split(':')[0] for field in line.split()[2:5]}
        self.assertEqual(states, {'WHOLE', 'PARTIAL', 'NONE'}, 'the corpus reaches every state')

    def test_a_library_that_ignores_the_crc_would_disagree_with_the_payload(self):
        """The corpus has pages only the CRC rejects: counted by the payload as PARTIAL with the CRC verdict BAD and no wrong word."""
        damaged = with_word(R.build_page(0, self.NOMINAL[0], 5), 7, 99)
        got = self.run_corpus([[damaged, bytes(4096), bytes(4096)]])
        self.assertEqual(got[0], 'CASE 0 PARTIAL:0:0:B NONE:0:0:- NONE:0:0:- THIS=1')


class ManifestInterop(unittest.TestCase):
    """The manifest the builder writes against the q1n1 fork's own manifest tool for the same two files (SKIP without Q1N1_SP11_TREE)."""

    def test_the_builder_writes_the_bytes_the_q1n1_manifest_tool_writes(self):
        tree = os.environ.get('Q1N1_SP11_TREE')
        tool = Path(tree) / 'tools' / 'sp11_payload_manifest.py' if tree else None
        if tool is None or not tool.is_file():
            raise unittest.SkipTest('Q1N1_SP11_TREE does not name a q1n1 checkout with tools/sp11_payload_manifest.py')
        work = BUILD / 'manifest-interop'
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        payload = b'\x5f\x24\x03\xd5' + bytes(range(256)) * 3
        (work / 'retprobe.bin').write_bytes(payload)
        (work / 'retpath.dat').write_bytes(R.config_bytes(1, 8, PAGES))
        done = subprocess.run([sys.executable, '-B', str(tool), '--out-dir', str(work / 'stick'), str(work / 'retprobe.bin'), str(work / 'retpath.dat')],
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        built = P.build(payload, 'psci-reset', 8, PAGES, work / 'out')
        self.assertEqual((work / 'stick' / 'SP11' / 'PAYLOAD.MAN').read_bytes(), built['manifest'])


if __name__ == '__main__':
    unittest.main()
