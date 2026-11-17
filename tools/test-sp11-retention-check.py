#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of the host verdict tool of the retention probe (tools/sp11_retention_check.py): the Shell's `dmem` text of a result file, the
verdict per page, the memory map of the extraction boot, the series rule of the study's M10 and the command line. The writer of the text is
here and shares nothing with the tool. No hardware, no emulator; scratch files are below build/ and the tool is only ever pointed at them.

    python3 tools/test-sp11-retention-check.py

  * DumpParsing: a good file; every named refusal, each with a file that has only that defect (a truncated file, a UCS-2 file, a wrong block
    size, a swapped line, an altered text column, a stray line, a memory map whose page counts disagree ...); the verdicts of damaged pages and
    what the memory map says about them;
  * Series: the study's pass rule, ten consecutive trials with a whole page, a break resetting the run, firmware-owned pages not counting;
  * Cli: check, series, stick and --json, the exit codes, and that nothing it is pointed at is changed.

SP11_RETENTION_DIR names a directory with modified copies of the modules, for tools/test-sp11-retention-mutants.py."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import unittest
from datetime import datetime, timedelta

ROOT = Path(__file__).resolve().parents[1]
TOOLS = Path(os.environ.get('SP11_RETENTION_DIR') or Path(__file__).resolve().parent)
BUILD = ROOT / 'build' / 'test-sp11-retention-check' / (Path(os.environ['SP11_RETENTION_DIR']).name if os.environ.get('SP11_RETENTION_DIR') else 'run')
sys.dont_write_bytecode = True
sys.path.insert(0, str(TOOLS))
import sp11_autorun as autorun  # noqa: E402
import sp11_retention as R  # noqa: E402
import sp11_retention_check as C  # noqa: E402

PAGES = (0x9C000000, 0xF8400000, 0xFFFF00000)
MAP_ROWS = ['Available  000000009C000000-000000009C0FFFFF 0000000000000100 0000000000000008',
            'Available  00000000F8400000-00000000F84FFFFF 0000000000000100 0000000000000008',
            'Available  0000000FFFF00000-0000000FFFFFFFFF 0000000000000100 0000000000000008']
MAP_TAIL = [' ', '  Reserved  :            768 Pages (3,145,728 Bytes)', '              -------------- ', 'Total Memory:          1,021 MB (1,070,596,096 Bytes)']
CLOCK = ('11/17/2026', '01:20:58 (LOCAL)')       # what the pinned Shell's `date` and `time` print (OBSERVED under QEMU); the unit's clock reads local time


def block(address, page, full=False):
    """The Shell's `dmem <address> 0x1000` as text: a heading and 256 lines of 16 bytes with the text column. The pinned Shell prints the low 32
    bits of a line's address (OBSERVED under QEMU, tools/test-sp11-retention-qemu.py); `full` writes all of it, which another Shell might."""
    lines = [f'Memory Address {address:016X} 1000 Bytes']
    for k in range(256):
        chunk = page[16 * k:16 * k + 16]
        shown = address + 16 * k if full else (address + 16 * k) & 0xFFFFFFFF
        lines.append(f'  {shown:X}: ' + ' '.join(f'{b:02X}' for b in chunk[:8]) + '-' + ' '.join(f'{b:02X}' for b in chunk[8:])
                     + '  *' + ''.join(chr(b) if 0x20 <= b <= 0x7E else '.' for b in chunk) + '*')
    return lines


def dump_lines(pages, addresses=PAGES, rows=MAP_ROWS, plan='retention-m10', step=2, end=True, full=False, clock=CLOCK):
    lines = [line for address, page in zip(addresses, pages) for line in block(address, page, full)]
    if rows is not None:
        lines += ['Type       Start            End              # Pages          Attributes', *rows, *MAP_TAIL]
    return lines + (list(clock) if clock else []) + ([f'RETENTION-EXTRACT-END plan={plan} step={step} pages=3'] if end else [])


def text(lines, eol='\r\n'):
    return (eol.join(lines) + eol).encode('ascii')


def whole(counter=4, previous=3, path=1, stamp=1000):
    return [R.build_page(k, PAGES[k], counter, previous, stamp, 19200000, 0, 0xFFFFFFFF, path, path, (2, 2, 2), (7, 0x9C000000, 0x4000000)) for k in range(3)]


def flip(page, offset, mask=1):
    data = bytearray(page)
    data[offset] ^= mask
    return bytes(data)


class DumpParsing(unittest.TestCase):
    def test_a_good_file_is_parsed_and_judged(self):
        blob = text(dump_lines(whole()))
        dump = C.parse_dump(blob, expected_pages=PAGES, step=2)
        self.assertEqual((dump.plan, dump.step), ('retention-m10', 2))
        self.assertEqual([(a, p) for a, p in dump.pages], list(zip(PAGES, whole())))
        self.assertEqual(len(dump.memmap), 3)
        self.assertEqual((dump.clock, dump.clock_text), (datetime(2026, 11, 17, 1, 20, 58), '11/17/2026 01:20:58 (LOCAL)'))
        self.assertEqual(dump.digest, hashlib.sha256(blob).hexdigest())
        report = C.judge(dump, R.config_bytes(1, 8, PAGES))
        self.assertEqual((report.verdict, report.path, report.counter, report.previous_counter), ('WHOLE', 'PSCI_RESET', 4, 3))
        self.assertEqual([r.verdict.state for r in report.pages], ['WHOLE'] * 3)
        self.assertEqual([r.map_free for r in report.pages], [True] * 3)
        self.assertEqual((report.has_map, report.counter_conflict, report.stamps), (True, False, ((4, 1000),)))
        shown = C.format_report(report, 'RET2.TXT')
        for phrase in ('RET2.TXT plan=retention-m10 step=2', 'reset path: PSCI_RESET (requested by the configuration: PSCI_RESET; recorded by the pages: PSCI_RESET)',
                       "extracted: 11/17/2026 01:20:58 (LOCAL), the unit's clock as its Shell printed it",
                       'PSCI_FEATURES as the probe read them: SYSTEM_RESET=0x0 (implemented), SYSTEM_RESET2=0xFFFFFFFF (NOT_SUPPORTED(-1))',
                       'page 0  0x9C000000  WHOLE', 'page 2  0xFFFF00000  WHOLE', 'counter 4, CRC ok, 992 of 992 body words', 'free in the extraction boot (Available)',
                       'boot counter: this boot 4, previous 3, step +1 (the probe found whole pages of boot 3 when it started)',
                       'verdict for path PSCI_RESET: WHOLE (WHOLE x3, PARTIAL x0, NONE x0)'):
            self.assertIn(phrase, shown)
        self.assertNotIn('WARNING', shown)
        self.assertEqual(C.parse_dump(text(dump_lines(whole()), '\n')).pages, dump.pages, 'LF line ends are read as CRLF ones')

    def test_the_shells_32_bit_line_addresses_and_full_ones_are_both_read_and_a_wrong_one_is_not(self):
        pages = whole()
        truncated, full = dump_lines(pages), dump_lines(pages, full=True)
        self.assertIn('  FFF00000: 52 45 54 43', ' '.join(truncated), 'the third page is above 4 GiB and its lines show 32 bits')
        self.assertIn('  FFFF00000: 52 45 54 43', ' '.join(full))
        self.assertEqual(C.parse_dump(text(truncated)).pages, C.parse_dump(text(full)).pages)
        wrong = [line.replace('  FFF00030:', '  FFF00040:') for line in truncated]
        self.assertNotEqual(wrong, truncated)
        self.refused(text(wrong), 'LINE_ADDRESS')

    def test_a_file_without_a_memory_map_is_judged_alone_says_so_and_is_refused_in_a_series(self):
        """Judged alone it was already allowed and says so; what changed is that a series refuses it, because without the map nothing says whether
        firmware owned a page and the old rule counted such a file as a kept trial (a no-map dump counted ten times passed)."""
        dump = C.parse_dump(text(dump_lines(whole(), rows=None)))
        self.assertEqual(dump.memmap, ())
        report = C.judge(dump)
        self.assertEqual({r.map_note for r in report.pages}, {'no memory map in the file'})
        self.assertEqual((report.has_map, [r.map_free for r in report.pages]), (False, [None] * 3))
        self.assertIsNone(report.path_requested)
        self.assertEqual(report.path_used, 'PSCI_RESET')
        self.assertIn('WARNING: the file has no memory map: it is judged alone, and a series refuses it (NO_MEMORY_MAP)', C.format_report(report))
        with self.assertRaises(C.DumpError) as caught:
            C.series([report])
        self.assertEqual(caught.exception.reason, 'NO_MEMORY_MAP')

    def test_a_clock_that_is_not_the_shells_is_refused_and_it_is_one_date_and_one_time(self):
        for what, clock in (('a date that is no date', ('13/45/2026', '01:20:58 (LOCAL)')), ('a time that is no time', ('11/17/2026', '25:61:61 (LOCAL)'))):
            with self.subTest(what):
                self.refused(text(dump_lines(whole(), clock=clock)), 'NO_CLOCK')
        good = dump_lines(whole())
        last = len(good) - 1
        for what, blob in (('a time without its date', good[:last - 2] + good[last - 1:]), ('the date and time twice', good[:last] + list(CLOCK) + good[last:]),
                           ('a zone with no brackets', good[:last - 1] + ['01:20:58 LOCAL'] + good[last:])):
            with self.subTest(what):
                self.refused(text(blob), 'UNEXPECTED_LINE')
        self.assertEqual(C.parse_dump(text(dump_lines(whole(), clock=('02/03/2026', '23:59:59 (UTC+04:00)')))).clock, datetime(2026, 2, 3, 23, 59, 59))

    def test_a_file_with_no_date_and_time_is_still_judged_and_says_that_nothing_tells_when_it_was_extracted(self):
        """The clock is evidence of when, not of what: a unit whose firmware gave no time must not lose the verdict on its pages. A stick refuses it."""
        dump = C.parse_dump(text(dump_lines(whole(), clock=None)))
        self.assertEqual((dump.clock, dump.clock_text), (None, ''))
        report = C.judge(dump)
        self.assertEqual(report.verdict, 'WHOLE')
        shown = C.format_report(report, 'RET2.TXT')
        self.assertIn('WARNING: the file has no date and time, so nothing says when it was extracted (a stick refuses it: NO_CLOCK)', shown)
        self.assertNotIn('extracted:', shown)

    def refused(self, blob, reason, **kw):
        with self.assertRaises(C.DumpError) as caught:
            C.parse_dump(blob, **kw)
        self.assertEqual(caught.exception.reason, reason, caught.exception)
        return caught.exception

    def test_every_damaged_or_truncated_file_is_refused_with_its_own_name(self):
        good = dump_lines(whole())
        full = text(good)
        cut = len(good) - 1
        cases = {
            'EMPTY_FILE': b'',
            'UCS2_OUTPUT': b'\xff\xfe' + full.decode('ascii').encode('utf-16-le'),
            'NOT_ASCII': full.replace(b'RETCAN', b'RET\x80AN', 1),
            'NO_END_LINE': full[:-len(good[-1]) - 2],
            'END_LINE_MALFORMED': text(good[:-1] + ['RETENTION-EXTRACT-END plan=retention-m10 step=two pages=3']),
            'END_LINE_PAGE_COUNT': text(good[:-1] + ['RETENTION-EXTRACT-END plan=retention-m10 step=2 pages=4']),
            'BLOCK_SIZE': text([good[0].replace(' 1000 ', ' 0800 ')] + good[1:]),
            'BLOCK_ADDRESS_DUPLICATE': text(good[:257] + [good[257].replace('F8400000', '9C000000')] + good[258:]),
            'BLOCK_SHORT': text(good[:256] + good[257:]),
            'BLOCK_LONG': text(good[:257] + [good[256].replace('  9C000FF0:', '  9C001000:')] + good[257:]),
            'LINE_MALFORMED': text(good[:1] + [good[1].replace('52 45', '52 4')] + good[2:]),
            'LINE_ADDRESS': text(good[:3] + [good[4], good[3]] + good[5:]),
            'ASCII_COLUMN': text(good[:1] + [good[1].replace('*RETCAN01', '*RETCAN02')] + good[2:]),
            'BLOCK_COUNT': text(good[:257 * 2] + good[257 * 3:]),
            'UNEXPECTED_LINE': text(good[:257 * 3] + ['stray text'] + good[257 * 3:]),
            'MEMMAP_MALFORMED': text([line.replace('0000000000000100 0000000000000008', '0000000000000101 0000000000000008', 1) if line.startswith('Available  000000009C') else line
                                      for line in good]),
        }
        for reason, blob in cases.items():
            with self.subTest(reason):
                self.refused(blob, reason)
        self.assertEqual(self.refused(full, 'END_LINE_STEP_MISMATCH', step=3).detail, 'the file is RET3.TXT and its end line says step 2')
        self.refused(full, 'PAGES_DIFFER_FROM_CONFIG', expected_pages=(PAGES[0], PAGES[1], 0x1000))

    def test_a_truncated_file_is_never_judged_whatever_is_left_of_it(self):
        full = text(dump_lines(whole()))
        for keep in (100, 5000, len(full) // 2, len(full) - 200, len(full) - 3):
            with self.subTest(keep=keep), self.assertRaises(C.DumpError):
                C.parse_dump(full[:keep])

    def test_a_refused_configuration_or_other_pages_stop_the_judgement(self):
        dump = C.parse_dump(text(dump_lines(whole())))
        with self.assertRaises(C.DumpError) as caught:
            C.judge(dump, R.config_bytes(1, 8, PAGES)[:-1] + b'\0')
        self.assertEqual(caught.exception.reason, 'CONFIG_REFUSED')
        with self.assertRaises(C.DumpError) as caught:
            C.judge(dump, R.config_bytes(1, 8, (PAGES[0], PAGES[1], 0x10000000)))
        self.assertEqual(caught.exception.reason, 'PAGES_DIFFER_FROM_CONFIG')

    def judged(self, pages, rows=MAP_ROWS, config=None):
        return C.judge(C.parse_dump(text(dump_lines(pages, rows=rows))), config)

    def test_a_damaged_page_says_which_bytes_and_a_none_page_says_what_the_map_says(self):
        pages = whole()
        pages[1] = flip(pages[1], 300)                     # a body bit: PARTIAL with its bytes named
        pages[2] = bytes(4096)
        owned = [MAP_ROWS[0], MAP_ROWS[1], 'BS_Data    0000000FFFF00000-0000000FFFFFFFFF 0000000000000100 0000000000000008']
        report = self.judged(pages, owned)
        self.assertEqual((report.verdict, [r.verdict.state for r in report.pages]), ('WHOLE', ['WHOLE', 'PARTIAL', 'NONE']))
        shown = C.format_report(report)
        self.assertIn('page 1  0xF8400000  PARTIAL header names the page (counter 4), CRC BAD, 1 of 992 body words wrong, damaged bytes 0x12C-0x130', shown)
        self.assertIn('page 2  0xFFFF00000  NONE    no canary found; map: owned by firmware in the extraction boot (BS_Data): damage or loss here is no evidence',
                      shown)
        self.assertFalse(report.pages[2].map_free)
        self.assertEqual(self.judged([bytes(4096)] * 3).verdict, 'NONE')
        self.assertIn('NONE says the pages carry no canary. It does not say the probe wrote one', C.format_report(self.judged([bytes(4096)] * 3)))

    def test_the_verdict_is_the_best_state_and_a_page_with_a_lost_header_is_partial(self):
        lost = whole()
        lost[0] = bytes(128) + lost[0][128:]
        lost[1] = lost[1][:2048] + bytes(2048)
        lost[2] = b'\xff' * 4096
        report = self.judged(lost)
        self.assertEqual([r.verdict.state for r in report.pages], ['PARTIAL', 'PARTIAL', 'NONE'])
        self.assertEqual(report.verdict, 'PARTIAL')
        self.assertEqual(report.counter, 0)
        shown = C.format_report(report)
        self.assertIn('header lost; 992 body words of boot counter 4, page 0 survive: bytes 0x80-0x1000', shown)
        self.assertIn('boot counter: no whole page, so none to read', shown)

    def test_a_page_whose_crc_alone_fails_is_never_called_whole(self):
        pages = whole()
        pages[0] = flip(pages[0], 40)                      # w10, the frequency: not part of what names the page, only of the CRC
        report = self.judged(pages)
        self.assertEqual(report.pages[0].verdict.state, 'PARTIAL')
        self.assertIn('the body is intact and the header or the CRC word is not', C.format_report(report))

    def test_the_psci_answers_come_from_a_page_whose_crc_holds_or_say_they_are_unverified(self):
        def reset_answer(page, value):
            data = bytearray(page)
            data[44:48] = struct.pack('<I', value)                 # header word 11, the raw answer for SYSTEM_RESET, under the old CRC word
            return bytes(data)
        pages = whole()
        pages[0] = reset_answer(pages[0], 0xFFFFFFFE)
        shown = C.format_report(self.judged(pages))
        self.assertIn('SYSTEM_RESET=0x0 (implemented), SYSTEM_RESET2=0xFFFFFFFF (NOT_SUPPORTED(-1))', shown)
        self.assertNotIn('0xFFFFFFFE', shown)
        damaged = [reset_answer(page, 0xFFFFFFFE) for page in whole()]
        shown = C.format_report(self.judged(damaged))
        self.assertIn('SYSTEM_RESET=0xFFFFFFFE (INVALID_PARAMETERS(-2))', shown)
        self.assertIn('(the CRC of every page that names itself fails: unverified)', shown)
        self.assertEqual([C.psci_answer(raw) for raw in (0, 3, 0xFFFFFFFF, 0xFFFFFFF7, 0xFFFFFFF6, 0x80000000)],
                         ['implemented', 'implemented', 'NOT_SUPPORTED(-1)', 'INVALID_ADDRESS(-9)', 'negative', 'negative'])

    def test_the_reset_path_is_the_pages_and_a_disagreement_is_said(self):
        report = self.judged(whole(path=2), config=R.config_bytes(1, 8, PAGES))
        self.assertEqual((report.path_requested, report.path_used, report.path), ('PSCI_RESET', 'PSCI_RESET2', 'PSCI_RESET2'))
        self.assertIn('WARNING: the configuration and the pages disagree about the reset path', C.format_report(report))
        mixed = whole(path=1)
        mixed[1] = R.build_page(1, PAGES[1], 4, 3, path_requested=2, path_used=2)
        report = self.judged(mixed)
        self.assertIsNone(report.path_used)
        self.assertTrue(report.path_conflict)
        self.assertIn('WARNING: the pages that name themselves record different reset paths', C.format_report(report))
        self.assertEqual(self.judged([bytes(4096)] * 3).path, 'UNKNOWN')


class Series(unittest.TestCase):
    def report(self, verdict_pages, rows=MAP_ROWS, path=1, n=0, counters=(4, 4, 4), stamp=None, seconds=None):
        """The Report of one trial. `n` makes the trial its own, as two real runs are: the timestamp the probe wrote into its pages and the seconds on
        the Shell's clock differ. A trial of the same `n` is a byte for byte copy of the file; `stamp` and `seconds` set those two apart from `n`."""
        stamp, seconds = 1000 + n if stamp is None else stamp, n if seconds is None else seconds
        pages = [R.build_page(k, PAGES[k], counters[k], 3, stamp, 19200000, 0, 0xFFFFFFFF, path, path, (2, 2, 2), (7, 0x9C000000, 0x4000000))
                 if verdict_pages[k] else bytes(4096) for k in range(3)]
        clock = (CLOCK[0], f'01:{seconds // 60:02d}:{seconds % 60:02d} (LOCAL)')
        return C.judge(C.parse_dump(text(dump_lines(pages, rows=rows, clock=clock))), R.config_bytes(path, 8, PAGES))      # the config says the path of a NONE trial

    def trials(self, count, *args, first=0, **kw):
        return [self.report(*args, n=first + n, **kw) for n in range(count)]

    def test_ten_consecutive_trials_with_a_whole_page_pass_and_nine_do_not(self):
        trials = self.trials(10, (True, False, False))
        self.assertTrue(C.series(trials)['PSCI_RESET']['pass'])
        self.assertFalse(C.series(trials[:9])['PSCI_RESET']['pass'])
        self.assertEqual(C.series(trials)['PSCI_RESET']['trials'], 10)

    def test_a_trial_without_a_whole_page_resets_the_run_and_the_counts_are_kept(self):
        bad = self.report((False, False, False), n=6)
        result = C.series(self.trials(6, (True, True, True)) + [bad] + self.trials(6, (True, True, True), first=7))['PSCI_RESET']
        self.assertEqual((result['trials'], result['whole'], result['none'], result['best_streak'], result['pass']), (13, 12, 1, 6, False))

    def test_a_whole_page_the_extraction_boot_shows_owned_by_firmware_does_not_count(self):
        owned = [MAP_ROWS[0].replace('Available ', 'BS_Data   '), MAP_ROWS[1], MAP_ROWS[2]]
        trials = self.trials(12, (True, False, False), rows=owned)
        self.assertEqual({t.verdict for t in trials}, {'WHOLE'})
        self.assertEqual(C.series(trials)['PSCI_RESET']['best_streak'], 0)

    def test_a_whole_page_that_is_in_no_extent_of_the_map_does_not_count_either(self):
        elsewhere = [MAP_ROWS[1], MAP_ROWS[2]]              # the map has rows, but none that holds page 0
        trials = self.trials(12, (True, False, False), rows=elsewhere)
        self.assertEqual(([r.map_free for r in trials[0].pages], trials[0].has_map), ([None, True, True], True))
        self.assertEqual(C.series(trials)['PSCI_RESET']['best_streak'], 0, 'unknown is not free')
        self.assertEqual(C.series(self.trials(12, (True, False, False)))['PSCI_RESET']['best_streak'], 12)

    def test_the_paths_are_counted_apart(self):
        result = C.series(self.trials(10, (True, False, False), path=1) + self.trials(3, (True, False, False), path=2, first=10))
        self.assertEqual((result['PSCI_RESET']['pass'], result['PSCI_RESET2']['pass'], result['PSCI_RESET2']['trials']), (True, False, 3))

    def refused(self, trials, reason):
        with self.assertRaises(C.DumpError) as caught:
            C.series(trials)
        self.assertEqual(caught.exception.reason, reason, caught.exception)
        return caught.exception

    def test_one_dump_counted_ten_times_is_one_trial_and_is_refused(self):
        one = self.report((True, False, False))
        self.refused([one] * 10, 'SERIES_FILE_REPEATED')
        self.refused([self.report((True, False, False)) for _ in range(10)], 'SERIES_FILE_REPEATED')       # ten reads of byte for byte one file
        self.refused([self.report((False, False, False))] * 2, 'SERIES_FILE_REPEATED')                    # a NONE file too: it is the same file
        self.assertIn('byte for byte', self.refused([one, one], 'SERIES_FILE_REPEATED').detail)
        self.assertEqual(C.series(self.trials(10, (False, False, False)))['PSCI_RESET']['none'], 10, 'ten files that merely look alike are ten trials')

    def test_the_same_write_dumped_twice_is_refused_even_when_the_two_files_differ(self):
        one = self.report((True, False, False), n=3)
        again = self.report((True, False, False), n=3, seconds=59 * 60 + 59)
        self.assertEqual(one.stamps, ((4, 1003),))
        self.assertEqual(again.stamps, one.stamps)
        self.assertNotEqual(again.digest, one.digest, 'another clock: not byte for byte the same file')
        self.assertIn('boot counter 4 and timestamp 1003', self.refused([one, again], 'SERIES_TRIAL_REPEATED').detail)
        self.assertEqual(C.series([one, self.report((True, False, False), n=4)])['PSCI_RESET']['trials'], 2, 'the same counter with another timestamp is another write')
        self.assertEqual(C.series([one, self.report((True, False, False), n=4, counters=(7, 7, 7), stamp=1003)])['PSCI_RESET']['trials'], 2,
                         'the same timestamp with another counter is another write')

    def test_a_file_with_no_memory_map_is_refused_wherever_it_stands_in_the_series(self):
        good = self.trials(3, (True, False, False))
        bare = self.report((True, False, False), rows=None, n=9)
        for trials in ([bare] + good, good + [bare], good[:1] + [bare] + good[1:]):
            self.refused(trials, 'NO_MEMORY_MAP')

    def test_whole_pages_of_different_boots_in_one_file_warn_and_are_not_a_kept_trial(self):
        trials = self.trials(12, (True, True, True), counters=(4, 3, 5))
        self.assertTrue(all(t.counter_conflict and t.counter == 5 and t.verdict == 'WHOLE' for t in trials))
        shown = C.format_report(trials[0])
        self.assertIn('WARNING: the whole pages carry different boot counters (3, 4, 5): pages of different writes sit side by side, so this is not one trial '
                      'and a series does not keep it', shown)
        result = C.series(trials)['PSCI_RESET']
        self.assertEqual((result['whole'], result['best_streak'], result['pass']), (12, 0, False))
        self.assertFalse(self.report((True, True, True)).counter_conflict)
        self.assertFalse(self.report((True, False, False), counters=(4, 9, 9)).counter_conflict, 'a page that is not whole carries no counter of its own')


class Cli(unittest.TestCase):
    tool = TOOLS / 'sp11_retention_check.py'

    def setUp(self):
        self.root = BUILD / self._testMethodName
        shutil.rmtree(self.root, ignore_errors=True)
        (self.root / 'SP11').mkdir(parents=True)

    def run_tool(self, *args):
        return subprocess.run([sys.executable, '-B', str(self.tool), *map(str, args)], capture_output=True, text=True)

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in sorted(self.root.rglob('*')) if p.is_file()}

    def test_check_prints_the_verdict_and_a_json_form_exists(self):
        path = self.root / 'SP11' / 'RET2.TXT'
        path.write_bytes(text(dump_lines(whole())))
        (self.root / 'SP11' / 'RETPATH.DAT').write_bytes(R.config_bytes(1, 8, PAGES))
        before = self.snapshot()
        done = self.run_tool('check', path, '--config', self.root / 'SP11' / 'RETPATH.DAT')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('verdict for path PSCI_RESET: WHOLE (WHOLE x3, PARTIAL x0, NONE x0)', done.stdout)
        data = json.loads(self.run_tool('check', path, '--json').stdout)
        self.assertEqual((data[0]['verdict'], data[0]['path'], [p['state'] for p in data[0]['pages']]), ('WHOLE', 'PSCI_RESET', ['WHOLE'] * 3))
        self.assertEqual(self.snapshot(), before, 'the tool only reads')

    def test_a_damaged_file_is_refused_with_exit_status_2_and_its_name(self):
        path = self.root / 'SP11' / 'RET2.TXT'
        path.write_bytes(text(dump_lines(whole()))[:-60])
        done = self.run_tool('check', path)
        self.assertEqual((done.returncode, done.stdout.split(':')[0]), (2, 'REFUSED NO_END_LINE'), done.stdout)
        self.assertNotIn('WHOLE', done.stdout)
        path.write_bytes(text(dump_lines(whole(), step=3)))
        self.assertEqual(self.run_tool('check', path).returncode, 2, 'RET2.TXT whose end line says step 3')

    def test_stick_reads_a_tree_and_refuses_one_without_a_result(self):
        self.assertEqual(self.run_tool('stick', self.root).stdout.split(':')[0], 'REFUSED NO_RESULT_FILE')
        self.lay_out()
        before = self.snapshot()
        done = self.run_tool('stick', self.root)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('RET2.TXT plan=retention-m10 step=2', done.stdout)
        self.assertEqual(self.snapshot(), before)
        (self.root / 'SP11' / 'RETPATH.DAT').write_bytes(R.config_bytes(1, 8, PAGES)[:-1])
        self.assertIn('REFUSED', self.run_tool('stick', self.root).stdout)

    STAGED = datetime(2026, 11, 17, 1, 0, 0)

    def lay_out(self, staged=STAGED, started=datetime(2026, 11, 17, 1, 10, 0), clock=CLOCK, written=None, done1=True, done2=True, plan_text=None):
        """A stick as a run leaves it: PLAN.TXT as the generator writes it (with the staging time `staged`), RETPATH.DAT, DONE1.TXT stamped `started`,
        RET2.TXT with the Shell's `clock` in it and stamped `written` (by default that clock), and DONE2.TXT two seconds later."""
        sp11 = self.root / 'SP11'
        steps = [f'SLBOUNCE-DEVHE-MARK.EFI {autorun.RUNNER} --el2', 'RETENTION-EXTRACT ' + ' '.join(f'0x{page:X}' for page in PAGES)]
        data = [autorun.DataFile('PAYLOAD.MAN', 624, 'c' * 64), autorun.DataFile('RETPROBE.BIN', 100, 'd' * 64), autorun.DataFile('RETPATH.DAT', 64, 'e' * 64)]
        plan = autorun.attach_data(autorun.build_plan('retention-m10', steps, {'SLBOUNCE-DEVHE-MARK.EFI': 'a' * 64, autorun.RUNNER: 'b' * 64}), data)
        (sp11 / 'PLAN.TXT').write_bytes(autorun.render_plan_text(autorun.stamp_staging(plan, staged)) if plan_text is None else plan_text)
        (sp11 / 'RETPATH.DAT').write_bytes(R.config_bytes(1, 8, PAGES))
        (sp11 / 'RET2.TXT').write_bytes(text(dump_lines(whole(), clock=clock)))
        shown = datetime.strptime(f'{clock[0]} {clock[1][:8]}', '%m/%d/%Y %H:%M:%S')
        for name, stamp in (('DONE1.TXT', started if done1 else None), ('RET2.TXT', shown if written is None else written),
                            ('DONE2.TXT', shown + timedelta(seconds=2) if done2 else None)):
            if stamp is None:
                continue
            if name != 'RET2.TXT':
                (sp11 / name).write_bytes(b'AUTORUN retention-m10 marker\r\n')
            os.utime(sp11 / name, (stamp.timestamp(), stamp.timestamp()))

    def test_stick_prints_the_times_it_compared_and_accepts_a_result_later_than_the_staging(self):
        self.lay_out()
        done = self.run_tool('stick', self.root)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        for phrase in ('times: SP11/PLAN.TXT records the staging at 2026-11-17 01:00:00, by the clock of the computer that staged it',
                       'DONE1.TXT  written 2026-11-17 01:10:00 (step 1 started), 658 s before the extraction',
                       "RET2.TXT  extracted 2026-11-17 01:20:58 by the Shell's clock, file time 2026-11-17 01:20:58",
                       'DONE2.TXT  written 2026-11-17 01:21:00 (the extraction ended)', 'verdict for path PSCI_RESET: WHOLE'):
            self.assertIn(phrase, done.stdout)
        self.assertNotIn('WARNING', done.stdout)

    def test_stick_refuses_a_result_that_is_not_later_than_the_staging(self):
        for clock in (('11/17/2026', '01:00:00 (LOCAL)'), ('11/16/2026', '23:59:59 (LOCAL)'), ('01/01/2020', '00:00:00 (LOCAL)')):
            with self.subTest(clock=clock):
                self.lay_out(clock=clock, started=datetime(2026, 11, 17, 1, 10, 0))
                done = self.run_tool('stick', self.root)
                self.assertEqual((done.returncode, done.stdout.split(':')[0]), (2, 'REFUSED STALE_RESULT'), done.stdout)
                self.assertNotIn('WHOLE', done.stdout, 'a stale result is never judged')
                self.assertIn('it is a result left from an earlier run', done.stdout)

    def test_stick_refuses_a_step_1_marker_that_is_not_later_than_the_staging(self):
        for started in (datetime(2026, 11, 17, 1, 0, 0), datetime(2026, 11, 16, 23, 0, 0)):
            with self.subTest(started=started):
                self.lay_out(started=started)
                done = self.run_tool('stick', self.root)
                self.assertEqual((done.returncode, done.stdout.split(':')[0]), (2, 'REFUSED STALE_STEP_MARKER'), done.stdout)
                self.assertIn('step 1 was skipped in this one and RET2.TXT is a dump of RAM that nothing wrote', done.stdout)
                self.assertNotIn('NONE', done.stdout)

    def test_stick_refuses_a_result_when_step_1_never_started_and_a_plan_with_no_staging_time_and_a_result_with_no_clock(self):
        self.lay_out(done1=False)
        self.assertEqual(self.run_tool('stick', self.root).stdout.split(':')[0], 'REFUSED NO_STEP_MARKER')
        sp11 = self.root / 'SP11'
        for what, plan_text in (('an older PLAN.TXT', b'SP11 AUTORUN PLAN\r\nplan: retention-m10\r\nsteps: 2\r\n'), ('a staged line that is no time', b'staged: soon\r\n')):
            with self.subTest(what):
                self.lay_out(plan_text=plan_text)
                self.assertEqual(self.run_tool('stick', self.root).stdout.split(':')[0], 'REFUSED NO_STAGING_TIME')
        (sp11 / 'PLAN.TXT').unlink()
        done = self.run_tool('stick', self.root)
        self.assertEqual((done.returncode, done.stdout.split(':')[0]), (2, 'REFUSED NO_STAGING_TIME'), done.stdout)
        self.lay_out()
        (sp11 / 'RET2.TXT').write_bytes(text(dump_lines(whole(), clock=None)))
        done = self.run_tool('stick', self.root)
        self.assertEqual((done.returncode, done.stdout.split(':')[0]), (2, 'REFUSED NO_CLOCK'), done.stdout)
        self.assertNotIn('WHOLE', done.stdout, 'a result that cannot be compared with the staging is not judged by a stick')
        self.assertEqual(self.run_tool('check', sp11 / 'RET2.TXT').returncode, 0, 'check judges it alone')

    def test_stick_warns_when_the_file_time_is_not_the_time_written_in_the_file_and_names_a_missing_extraction_marker(self):
        self.lay_out(written=datetime(2026, 11, 17, 2, 0, 0), done2=False)
        done = self.run_tool('stick', self.root)
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertIn('WARNING: the file time of RET2.TXT is not the time written in it: it was copied or edited after the unit wrote it', done.stdout)
        self.assertIn('DONE2.TXT  is not on the stick: the extraction did not reach its marker', done.stdout)
        self.assertIn('verdict for path PSCI_RESET: WHOLE', done.stdout, 'a warning is not a refusal')

    def test_check_judges_a_file_alone_and_does_not_look_at_the_staging(self):
        self.lay_out(clock=('01/01/2020', '00:00:00 (LOCAL)'))
        done = self.run_tool('check', self.root / 'SP11' / 'RET2.TXT')
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertNotIn('staged', done.stdout)
        self.assertIn('extracted: 01/01/2020 00:00:00 (LOCAL)', done.stdout)

    def test_series_prints_each_file_and_the_rule(self):
        files = []
        for n in range(11):
            path = self.root / f'trial{n:02d}' / 'RET2.TXT'
            path.parent.mkdir()
            path.write_bytes(text(dump_lines(whole(counter=1 + n, previous=n, stamp=1000 + n), clock=(CLOCK[0], f'01:00:{n:02d} (LOCAL)'))))
            files.append(path)
        done = self.run_tool('series', *files)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('path PSCI_RESET: 11 trials, WHOLE 11, PARTIAL 0, NONE 0, best run of consecutive WHOLE trials 11 of 10 needed: PASS', done.stdout)
        short = self.run_tool('series', *files[:4])
        self.assertIn('best run of consecutive WHOLE trials 4 of 10 needed: NOT PASSED', short.stdout)

    def test_series_refuses_one_file_counted_ten_times_a_copy_of_it_and_a_file_with_no_map_with_exit_status_2(self):
        original = self.root / 'trial00' / 'RET2.TXT'
        original.parent.mkdir()
        original.write_bytes(text(dump_lines(whole())))
        copy = self.root / 'copy' / 'RET2.TXT'
        copy.parent.mkdir()
        copy.write_bytes(original.read_bytes())
        before = self.snapshot()
        for files in ([original] * 10, [original, copy]):
            done = self.run_tool('series', *files)
            self.assertEqual((done.returncode, done.stdout.split(':')[0]), (2, 'REFUSED SERIES_FILE_REPEATED'), done.stdout)
            self.assertNotIn('PASS', done.stdout, 'nothing is counted from a refused series')
        bare = self.root / 'bare' / 'RET2.TXT'
        bare.parent.mkdir()
        bare.write_bytes(text(dump_lines(whole(stamp=2000), rows=None, clock=(CLOCK[0], '02:00:00 (LOCAL)'))))
        done = self.run_tool('series', original, bare)
        self.assertEqual((done.returncode, done.stdout.split(':')[0]), (2, 'REFUSED NO_MEMORY_MAP'), done.stdout)
        self.assertIn('bare', done.stdout.split(':', 1)[1])
        self.assertEqual(self.run_tool('check', bare).returncode, 0, 'a file with no map is still judged alone')
        self.assertEqual(self.snapshot(), {**before, str(bare.relative_to(self.root)): bare.read_bytes()}, 'only reads')


if __name__ == '__main__':
    unittest.main()
