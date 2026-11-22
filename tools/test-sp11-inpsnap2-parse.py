#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of tools/sp11_inpsnap2_parse.py, the parser of the film transcription of the page of the payload `inpsnap2` (stage I0 of the input plan).

    python3 tools/test-sp11-inpsnap2-parse.py

  Golden     seven pages of the compiled payload as the host model of the payload prints them (the fixture's words are made up, except the five boot 29 read: this is NOT a page of the unit) parse
             to the words they hold, and an independent renderer written here from the page's format (number form, verdicts, check, gate, end line) reproduces each of them byte for byte
  RoundTrip  a survey page rendered from a plain and from a DFS set of words parses back to exactly those words, and the object the parser makes gives the same plan through
             tools/geni_uart_plan.py as the words themselves; an abort in a level word stops the plan of a DFS survey
  Table      the parser's key map and addresses are the host plan's survey list, word for word (the 41 words and the three gate words)
  Refuse     every missing, extra, duplicated or reordered line, wrong key, duplicated key, bad check, number in another form, wrong or missing verdict, wrong end-line counter, inconsistent gate,
             unexplained SKIP, page without its end line and page printed twice is a PageError, never a partial answer; lines that are not the payload's are ignored
  Partial    `--partial` (parse_page(partial=True)): a page that ends at a line boundary without its end line, as after a hang or an SError, is read as far as it goes: every prefix of every golden page
             is read with the flag and refused without it, each line is checked as in a whole page (every missing, duplicated, swapped, bad-check, wrong-key, wrong-verdict and unexplained-SKIP
             case is refused for a prefix too), no counter is checked and none is claimed, a page with its end line is checked in full, and the plan stops on the first word the page lacks
  Cli        the command line prints the words, writes the object `geni_uart_plan.py plan` reads, runs the plan, refuses a bad page with a status and a message, refuses a partial page unless
             `--partial` is given, and with the flag prints the PARTIAL banner and lets the plan stop on the version word
  Model      (needs Q1N1_SP11_TREE, a q1n1 worktree with the host model of the payload, and clang; SKIPs without them) the pages the model prints for its scenarios are the golden pages byte
             for byte, they parse, the two early exits are refused by name, and the model's table is the parser's key map and addresses: the parser and the payload cannot drift apart unseen

Evidence: EXPERIMENTAL (host only); hardware NOT RUN."""
from pathlib import Path
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import geni_uart_plan as plan  # noqa: E402
import sp11_inpsnap2_parse as parse  # noqa: E402

BUILD = ROOT / 'build' / 'test-sp11-inpsnap2-parse'
PREFIX = 'PAYLOAD INPSNAP2 '

HEAD = ['READ ONLY EL=2 STAGE=I0 CAP=44/24', 'KEY=VALUE .ON .OFF .HW .ODD=CLOCK FAULT:6=ABORT SKIP=NOT READ K=LINE CRC16']
RCG_LINES = ['RCG M=0X10 N=0XFFC4 D=0XFFB4 K=FF54']
PLL_LINES = ['NEXT PLL', 'PLL MODE=0XC0000005 OP=1 ST=0 K=85B3', 'PLL L=0X1F AL=0X4000 USR=0X401 K=2415']
LVL_LINES = ['NEXT LVL', 'LVL LC0=0X2601 LC1=0X2602 LC2=0X2603 LC3=0X2604 K=FF76', 'LVL LC4=0X2605 LC5=0X2606 LC6=0X2607 LC7=0X2608 K=9911',
             'LVL LM0=0X10 LM1=0X11 LM2=0X12 LM3=0X13 K=30CA', 'LVL LM4=0X14 LM5=0X15 LM6=0X16 LM7=0X17 K=D36E', 'LVL LN0=0XFFC4 LN1=0XFFC3 LN2=0XFFC2 LN3=0XFFC1 K=F5F7',
             'LVL LN4=0XFFC0 LN5=0XFFBF LN6=0XFFBE LN7=0XFFBD K=C82D']
GATE_OK = ['NEXT GATE', 'GATE MA0=0.ON SA0=0.ON CR0=0X4000.ON K=3886', 'GATE SE0=0X80004000.OFF VT0=0X810223D0 GV=1 K=710E']
RCG_OK = ['NEXT RCG', 'RCG DFS=0 DIV=0 CMD=0 CFG=0X2601 K=EA31'] + RCG_LINES
QUP_OK = ['NEXT QUP', 'QUP VER=0X20050000 K=60CA']
PAGES = {
    'unit': HEAD + GATE_OK + RCG_OK + PLL_LINES + LVL_LINES + QUP_OK + ['END LOADS=44 FAULTS=0 SKIPS=0 BLK=0 CUTS=0 LINES=21 STOP=NONE'],
    'gateoff': HEAD + ['NEXT GATE', 'GATE MA0=0X80000000.OFF SA0=0.ON CR0=0X4000.ON K=B12C', GATE_OK[2]] + RCG_OK + PLL_LINES + LVL_LINES +
               ['NEXT QUP', 'QUP GATED OFF', 'END LOADS=43 FAULTS=0 SKIPS=0 BLK=0X10 CUTS=0 LINES=21 STOP=NONE'],
    'gateerr': HEAD + [GATE_OK[0], GATE_OK[1], 'GATE SE0=0X80004000.OFF VT0=0 GV=1 K=80E5'] + RCG_OK + PLL_LINES + LVL_LINES +
               ['NEXT QUP', 'QUP GATED ERR', 'END LOADS=43 FAULTS=0 SKIPS=0 BLK=0X10 CUTS=0 LINES=21 STOP=NONE'],
    'hwvote': HEAD + ['NEXT GATE', 'GATE MA0=0X80000002.HW SA0=0.ON CR0=0X4000.ON K=47EE', 'GATE SE0=0X80004000.OFF VT0=0X81022390 GV=1 K=2ED7'] + RCG_OK + PLL_LINES + LVL_LINES +
              ['NEXT QUP', 'QUP GATED OFF', 'END LOADS=43 FAULTS=0 SKIPS=0 BLK=0X10 CUTS=0 LINES=21 STOP=NONE'],
    'stop': HEAD + GATE_OK + ['NEXT RCG', 'RCG DFS=0 DIV=0XFFFFFFFF CMD=SKIP CFG=SKIP K=1F79', 'END LOADS=8 FAULTS=0 SKIPS=2 BLK=0X1E CUTS=0 LINES=8 STOP=DIV'],
    'fault': HEAD + GATE_OK + RCG_OK + PLL_LINES + ['NEXT LVL', 'LVL LC0=0X2601 LC1=0X2602 LC2=FAULT:6 LC3=SKIP K=78C2'] + ['LVL ' + ' '.join(f'{k}=SKIP' for k in keys) + ' K=1455'
             for keys in (('LC4', 'LC5', 'LC6', 'LC7'), ('LM0', 'LM1', 'LM2', 'LM3'), ('LM4', 'LM5', 'LM6', 'LM7'), ('LN0', 'LN1', 'LN2', 'LN3'), ('LN4', 'LN5', 'LN6', 'LN7'))] +
             QUP_OK + ['END LOADS=23 FAULTS=1 SKIPS=21 BLK=8 CUTS=0 LINES=21 STOP=NONE'],
    'dfs': HEAD + GATE_OK + ['NEXT RCG', 'RCG DFS=7 DIV=0 CMD=0 CFG=0X2601 K=D02E'] + RCG_LINES + PLL_LINES + LVL_LINES + QUP_OK +
           ['END LOADS=44 FAULTS=0 SKIPS=0 BLK=0 CUTS=0 LINES=21 STOP=NONE'],
}


def film(lines, noise=False):
    """A transcription: the page's lines with the prefix (and, if asked, the runner's lines around them, which the parser must ignore)."""
    out = [PREFIX + line for line in lines]
    if noise:
        out = ['PAYLOAD LOADED INDEX=0 BYTES=1234 SHA=0123456789ABCDEF', 'PAYLOAD VERIFY=OK', 'PAYLOAD CALL INDEX=0 '] + out[:5] + ['', '   some other film line'] + out[5:] + \
              ['PAYLOAD DONE INDEX=0 RETURN=0X0 STACK=OK LOADS=44 FAULTS=0 REFUSED=0 LINES=21 DROPPED=0']
    return '\n'.join(out) + '\n'


# ---- An independent renderer: the page from the words, written from the page's format and not from the parser ------------------------------------------------------------------------------
def crc16(data):
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def fmt(value):
    return str(value) if value < 10 else '0X%X' % value


def verdict(word, hwcg):
    if word == 0xFFFFFFFF:
        return 'ODD'
    if not word >> 31 or (word >> 28) & 7 == 2:
        return 'ON'
    return 'HW' if hwcg and word & 2 else 'OFF'


def gate_of(values):
    stopped = unknown = False
    vote = values['VT0']
    for key, hwcg, bit in (('MA0', True, 6), ('SA0', True, 7), ('CR0', False, 0)):
        kind = verdict(values[key], hwcg)
        if kind == 'HW':
            kind = 'ON' if vote >> bit & 1 else 'OFF'
        stopped |= kind == 'OFF'
        unknown |= kind == 'ODD'
    unknown |= vote in (0, 0xFFFFFFFF)
    return 'OFF' if stopped else 'ERR' if unknown else 'OPEN'


def data_line(block, items):
    """A data line from (key, state, value) items with its check: state 1 a word that read, 2 an abort (the value is its code), 3 not read."""
    tokens, data = [], b''
    for key, state, value in items:
        if state == 1:
            shown = fmt(value) + ('.' + verdict(value, key in ('MA0', 'SA0')) if key in ('MA0', 'SA0', 'CR0', 'SE0') else '')
        elif state == 2:
            shown = f'FAULT:{value}'
        else:
            shown, value = 'SKIP', 0
        tokens.append(f'{key}={shown}')
        data += bytes([state]) + value.to_bytes(4, 'little')
    return f'{block} ' + ' '.join(tokens) + f' K={crc16(data):04X}'


def render(values, fault=None):
    """The lines of the page for a complete set of words (44 values by key); `fault` is a key of the RCG, PLL or LVL block whose load aborts (its code is 6)."""
    lines, loads, faults, skips, flagged = list(HEAD), 0, 0, 0, 0
    gate = gate_of(values)
    for number, (block, layout) in enumerate(parse.LAYOUT):
        lines.append(f'NEXT {block}')
        if block == 'QUP' and gate != 'OPEN':
            lines.append(f'QUP GATED {gate}')
            flagged |= 1 << number
            break
        dead = False
        for keys in layout:
            tokens, data = [], b''
            for key in keys:
                if dead:
                    state, shown, word = 3, 'SKIP', 0
                    skips += 1
                    flagged |= 1 << number
                elif key == fault:
                    state, shown, word, dead = 2, 'FAULT:6', 6, True
                    loads, faults = loads + 1, faults + 1
                    flagged |= 1 << number
                else:
                    state, word = 1, values[key]
                    shown = fmt(word) + ('.' + verdict(word, key in ('MA0', 'SA0')) if key in ('MA0', 'SA0', 'CR0', 'SE0') else '')
                    loads += 1
                tokens.append(f'{key}={shown}')
                data += bytes([state]) + word.to_bytes(4, 'little')
            lines.append(f'{block} ' + ' '.join(tokens) + f' K={crc16(data):04X}')
    lines.append(f'END LOADS={loads} FAULTS={faults} SKIPS={skips} BLK={fmt(flagged)} CUTS=0 LINES={len(lines) + 1} STOP=NONE')
    return lines


def unit_values():
    """The words of the host model's unit-like fixture (made up, but for the five boot 29 read)."""
    values = {'MA0': 0, 'SA0': 0, 'CR0': 0x4000, 'SE0': 0x80004000, 'VT0': 0x810223D0, 'GV': 1, 'DFS': 0, 'DIV': 0, 'CMD': 0, 'CFG': 0x2601, 'M': 0x10, 'N': 0xFFC4, 'D': 0xFFB4,
              'MODE': 0xC0000005, 'OP': 1, 'ST': 0, 'L': 0x1F, 'AL': 0x4000, 'USR': 0x401, 'VER': 0x20050000}
    for i in range(8):
        values[f'LC{i}'], values[f'LM{i}'], values[f'LN{i}'] = 0x2601 + i, 0x10 + i, 0xFFC4 - i
    return values


def dfs_values():
    """A DFS survey: the flag set at level 3, and eight levels from the pinned frequency table (the first eight rows: 64 MHz is the seventh)."""
    values = unit_values()
    values['DFS'] = 0x7
    for level, row in enumerate(plan.S2_TABLE[:8]):
        word = plan.rcg2_encode(row)
        values[f'LC{level}'], values[f'LM{level}'], values[f'LN{level}'] = word['cfg'], word['m'], word['n']
    return values


def words_of(values):
    """The object of survey words of the plan from a set of page values."""
    return {parse.KEY_WORD[key]: value for key, value in values.items() if key in parse.KEY_WORD}


class Golden(unittest.TestCase):
    def test_every_golden_page_parses_and_has_the_counts_it_states(self):
        for name, lines in PAGES.items():
            with self.subTest(page=name):
                page = parse.parse_page(film(lines))
                self.assertEqual(page.lines, len(lines))
                self.assertEqual(page.end['LINES'], len(lines))
                self.assertEqual(sum(1 for w in page.words.values() if w.state in ('OK', 'FAULT')), page.end['LOADS'])

    def test_the_unit_page_gives_the_41_words_of_the_plan(self):
        page = parse.parse_page(film(PAGES['unit']))
        self.assertEqual((page.stop, page.gated), (None, None))
        words = parse.plan_words(page)
        self.assertEqual(len(words), 41)
        self.assertEqual(words, words_of(unit_values()))
        self.assertEqual(words['cbcr'], 0x80004000)
        self.assertEqual(words['qup_ver'], 0x20050000)
        self.assertEqual(page.words['MA0'].clock, 'ON')
        self.assertEqual(page.words['SE0'].clock, 'OFF')

    def test_the_gated_pages_have_no_wrapper_word_and_the_stop_page_has_no_later_word(self):
        for name, gate in (('gateoff', 'OFF'), ('gateerr', 'ERR'), ('hwvote', 'OFF')):
            page = parse.parse_page(film(PAGES[name]))
            self.assertEqual(page.gated, gate, name)
            self.assertNotIn('qup_ver', parse.plan_words(page))
            self.assertNotIn('VER', page.words)
        page = parse.parse_page(film(PAGES['stop']))
        self.assertEqual(page.stop, 'DIV')
        self.assertEqual({key for key in page.words}, {'MA0', 'SA0', 'CR0', 'SE0', 'VT0', 'GV', 'DFS', 'DIV', 'CMD', 'CFG'})
        self.assertEqual(parse.plan_words(page)['div'], 0xFFFFFFFF)
        self.assertNotIn('cmd', parse.plan_words(page), 'a word that was not read is no word')

    def test_the_fault_page_keeps_the_abort_and_the_words_after_it_out_of_the_plan(self):
        page = parse.parse_page(film(PAGES['fault']))
        self.assertEqual(page.words['LC2'], parse.Word('FAULT', 6))
        self.assertEqual(page.words['LN7'], parse.Word('SKIP', 0))
        words = parse.plan_words(page)
        self.assertIn('lv_cfg1', words)
        self.assertNotIn('lv_cfg2', words)
        self.assertNotIn('lv_cfg3', words)
        self.assertEqual(words['qup_ver'], 0x20050000, 'the wrapper block is read after an abort in another block')

    def test_the_independent_renderer_reproduces_the_pages_of_the_payload_byte_for_byte(self):
        self.assertEqual(render(unit_values()), PAGES['unit'])
        values = unit_values()
        values['MA0'] = 0x80000000
        self.assertEqual(render(values), PAGES['gateoff'])
        values = unit_values()
        values['VT0'] = 0
        self.assertEqual(render(values), PAGES['gateerr'])
        values = unit_values()
        values['MA0'], values['VT0'] = 0x80000002, 0x810223D0 & ~0x40
        self.assertEqual(render(values), PAGES['hwvote'])
        values = unit_values()
        values['DFS'], values['CFG'] = 7, 0x2601
        self.assertEqual(render(values), PAGES['dfs'])
        self.assertEqual(render(unit_values(), fault='LC2'), PAGES['fault'])

    def test_noise_around_the_page_is_ignored(self):
        self.assertEqual(parse.parse_page(film(PAGES['unit'], noise=True)), parse.parse_page(film(PAGES['unit'])))


class RoundTrip(unittest.TestCase):
    def test_a_plain_survey_gives_the_same_plan_through_the_page_as_directly(self):
        values = unit_values()
        direct = plan.build_clock_plan(words_of(values))
        through = plan.build_clock_plan(parse.plan_words(parse.parse_page(film(render(values)))))
        self.assertEqual(direct.mode, 'plain')
        self.assertEqual(through, direct)

    def test_a_dfs_survey_gives_the_same_plan_through_the_page_as_directly(self):
        values = dfs_values()
        direct = plan.build_clock_plan(words_of(values))
        through = plan.build_clock_plan(parse.plan_words(parse.parse_page(film(render(values)))))
        self.assertEqual(direct.mode, 'dfs')
        self.assertEqual(through, direct)
        self.assertEqual(plan.decode_dfs_cmd(parse.plan_words(parse.parse_page(film(render(values)))) ['dfs']), {'dfs': True, 'level': 3})

    def test_an_abort_in_a_level_word_of_a_dfs_survey_stops_the_plan_and_in_a_plain_one_does_not(self):
        page = parse.parse_page(film(render(dfs_values(), fault='LM2')))
        with self.assertRaises(plan.PlanError) as stopped:
            plan.build_clock_plan(parse.plan_words(page))
        self.assertIn("survey word 'lv_n0' missing", str(stopped.exception), 'the first level word the plan needs that the abort and its skips took away')
        plain = parse.parse_page(film(render(unit_values(), fault='LM2')))
        self.assertEqual(plan.build_clock_plan(parse.plan_words(plain)).mode, 'plain')

    def test_a_gated_wrapper_leaves_the_version_missing_and_the_plan_stops_on_it(self):
        values = unit_values()
        values['MA0'] = 0x80000000
        page = parse.parse_page(film(render(values)))
        with self.assertRaises(plan.PlanError) as stopped:
            plan.build_clock_plan(parse.plan_words(page))
        self.assertIn("'qup_ver' missing", str(stopped.exception))

    def test_an_all_ones_identity_word_stops_the_plan_for_its_own_reason(self):
        text = film(PAGES['stop'])
        page = parse.parse_page(text)
        with self.assertRaises(plan.PlanError) as stopped:
            plan.build_clock_plan(parse.plan_words(page))
        self.assertIn('div reads all ones', str(stopped.exception))


class Table(unittest.TestCase):
    def test_the_key_map_and_addresses_are_the_plans_survey_list(self):
        survey = {name: (base, off) for name, base, off, _ in plan.SURVEY_READS}
        self.assertEqual(len(survey), 41)
        self.assertEqual(sorted(parse.KEY_WORD.values()), sorted(survey))
        for key, name in parse.KEY_WORD.items():
            self.assertEqual(parse.ADDRESS[key], survey[name], f'{key} is the plan word {name}')
        self.assertEqual(sorted(set(parse.ADDRESS) - set(parse.KEY_WORD)), sorted(parse.WRAPPER_GATE_KEYS))
        self.assertEqual(len(parse.KEYS), 44)
        self.assertEqual(sorted(parse.KEYS), sorted(parse.ADDRESS))

    def test_the_layout_is_the_page_the_payload_prints(self):
        page = [line for line in PAGES['unit'] if line.split(' ')[0] in ('GATE', 'RCG', 'PLL', 'LVL', 'QUP') and ' K=' in line]
        self.assertEqual(len(page), sum(len(lines) for _, lines in parse.LAYOUT))
        flat = [tuple(token.split('=')[0] for token in line.split(' ')[1:-1]) for line in page]
        self.assertEqual(flat, [keys for _, lines in parse.LAYOUT for keys in lines])


def refuse(case, lines, text=None, partial=False):
    """Asserts that the page `lines` (or the transcription `text`) is refused (read with `partial` when asked); returns the message."""
    try:
        parse.parse_page(text if text is not None else film(lines), partial=partial)
    except parse.PageError as error:
        return str(error)
    raise AssertionError(f'{case}: the parser accepted a page it must refuse')


class Refuse(unittest.TestCase):
    unit = PAGES['unit']

    def test_every_missing_line_is_refused(self):
        for n in range(len(self.unit)):
            with self.subTest(missing=self.unit[n]):
                refuse('missing', self.unit[:n] + self.unit[n + 1:])

    def test_every_duplicated_line_is_refused(self):
        for n in range(len(self.unit)):
            with self.subTest(duplicated=self.unit[n]):
                refuse('duplicated', self.unit[:n + 1] + self.unit[n:])

    def test_every_pair_of_swapped_neighbours_is_refused(self):
        for n in range(len(self.unit) - 1):
            lines = list(self.unit)
            lines[n], lines[n + 1] = lines[n + 1], lines[n]
            with self.subTest(swapped=self.unit[n]):
                refuse('swapped', lines)

    def test_every_truncation_is_refused_and_says_the_page_has_no_end_line(self):
        for n in range(1, len(self.unit)):
            with self.subTest(kept=n):
                self.assertIn('a page without its end line is not read', refuse('truncated', self.unit[:n]))

    def test_a_page_printed_twice_and_a_line_after_the_end_are_refused(self):
        self.assertIn('after the end line', refuse('twice', self.unit + self.unit))
        self.assertIn('after the end line', refuse('trailer', self.unit + ['NEXT GATE']))

    def test_a_bad_check_is_refused_for_every_data_line(self):
        for n, line in enumerate(self.unit):
            if not re.search(r'^(GATE|RCG|PLL|LVL|QUP) .* K=[0-9A-F]{4}$', line):
                continue
            head, _, check = line.rpartition(' K=')
            flipped = check[:-1] + ('0' if check[-1] != '0' else '1')
            lines = list(self.unit)
            lines[n] = f'{head} K={flipped}'
            with self.subTest(line=line):
                self.assertIn('is not the check of its words', refuse('check', lines))

    def test_a_lower_case_short_or_long_check_is_refused(self):
        for bad in ('388', '38860', '38a6', '388G', ''):
            lines = list(self.unit)
            lines[3] = self.unit[3].replace('K=3886', f'K={bad}')
            with self.subTest(check=bad):
                self.assertIn('is not a data line', refuse('check form', lines))

    def test_a_wrong_key_order_is_refused_for_every_data_line_with_two_words(self):
        for n, line in enumerate(self.unit):
            if not re.search(r'^(GATE|RCG|PLL|LVL|QUP) .* K=[0-9A-F]{4}$', line):
                continue
            tokens = line.split(' ')
            if len(tokens) < 4:
                continue
            swapped = [tokens[0], tokens[2], tokens[1]] + tokens[3:]
            lines = list(self.unit)
            lines[n] = ' '.join(swapped)
            with self.subTest(line=line):
                refuse('order', lines)

    def test_a_duplicated_key_is_refused(self):
        lines = list(self.unit)
        lines[3] = lines[3].replace('SA0=0.ON', 'MA0=0.ON')
        self.assertIn('is not the word SA0', refuse('duplicated key', lines))

    def test_an_extra_word_or_a_missing_word_is_refused(self):
        lines = list(self.unit)
        lines[3] = lines[3].replace(' K=', ' GV=1 K=')
        self.assertIn('has 4 words', refuse('extra word', lines))
        lines[3] = 'GATE MA0=0.ON SA0=0.ON K=3886'
        self.assertIn('has 2 words', refuse('missing word', lines))

    def test_a_number_in_another_form_is_refused(self):
        for old, new in (('CFG=0X2601', 'CFG=0X02601'), ('CFG=0X2601', 'CFG=0x2601'), ('CFG=0X2601', 'CFG=9729'), ('CFG=0X2601', 'CFG=2601'), ('DIV=0', 'DIV=00'), ('GV=1', 'GV=0X1'),
                         ('GV=1', 'GV=01'), ('N=0XFFC4', 'N=0XffC4'), ('N=0XFFC4', 'N=0X0FFC4'), ('VER=0X20050000', 'VER=0X020050000'), ('MODE=0XC0000005', 'MODE=0X1C0000005')):
            lines = [line.replace(old, new) for line in self.unit]
            self.assertNotEqual(lines, self.unit)
            with self.subTest(change=(old, new)):
                refuse('number', lines)

    def test_a_nine_is_one_digit_and_ten_is_hex(self):
        self.assertEqual(parse.number('9'), 9)
        self.assertEqual(parse.number('0XA'), 10)
        for bad in ('10', '0X9', '0X0', '0', '0X', '0XG', '0Xa', ''):
            if bad == '0':
                self.assertEqual(parse.number(bad), 0)
                continue
            with self.subTest(text=bad):
                with self.assertRaises(parse.PageError):
                    parse.number(bad)

    def test_a_wrong_or_missing_verdict_is_refused(self):
        for old, new in (('MA0=0.ON', 'MA0=0'), ('MA0=0.ON', 'MA0=0.OFF'), ('MA0=0.ON', 'MA0=0.HW'), ('SE0=0X80004000.OFF', 'SE0=0X80004000.ON'), ('SE0=0X80004000.OFF', 'SE0=0X80004000'),
                         ('GV=1', 'GV=1.ON'), ('VT0=0X810223D0', 'VT0=0X810223D0.OFF'), ('CR0=0X4000.ON', 'CR0=0X4000.ODD')):
            lines = [line.replace(old, new) for line in self.unit]
            self.assertNotEqual(lines, self.unit)
            with self.subTest(change=(old, new)):
                refuse('verdict', lines)

    def test_a_wrong_header_or_legend_is_refused(self):
        refuse('stage', [self.unit[0].replace('I0', 'I1')] + self.unit[1:])
        refuse('caps', [self.unit[0].replace('44/24', '44/25')] + self.unit[1:])
        refuse('legend', [self.unit[0], self.unit[1].replace('K=LINE', 'K=ROW')] + self.unit[2:])

    def test_every_counter_of_the_end_line_is_checked(self):
        end = self.unit[-1]
        for old, new, message in (('LOADS=44', 'LOADS=43', 'LOADS=43'), ('FAULTS=0', 'FAULTS=1', 'FAULTS=1'), ('SKIPS=0', 'SKIPS=1', 'SKIPS=1'), ('BLK=0', 'BLK=1', 'BLK=1'),
                                  ('CUTS=0', 'CUTS=1', 'CUTS=1'), ('LINES=21', 'LINES=20', 'LINES=20'), ('STOP=NONE', 'STOP=VER', 'STOP=VER'), ('LOADS=44', 'LOADS=044', 'is not the end line'),
                                  ('BLK=0', 'BLK=0X0', 'is not a number')):
            with self.subTest(change=(old, new)):
                self.assertIn(message, refuse('end', self.unit[:-1] + [end.replace(old, new)]))

    def test_a_wrapper_gate_that_disagrees_with_the_words_is_refused(self):
        self.assertIn('close its gate', refuse('read behind a closed gate', PAGES['gateoff'][:-2] + QUP_OK + ['END LOADS=44 FAULTS=0 SKIPS=0 BLK=0X10 CUTS=0 LINES=21 STOP=NONE']))
        self.assertIn('give the gate OPEN', refuse('gated behind an open gate', self.unit[:-3] + ['NEXT QUP', 'QUP GATED OFF', 'END LOADS=43 FAULTS=0 SKIPS=0 BLK=0X10 CUTS=0 LINES=21 STOP=NONE']))
        self.assertIn('give the gate OFF', refuse('the wrong gate text', PAGES['gateoff'][:-2] + ['QUP GATED ERR', PAGES['gateoff'][-1]]))

    def test_a_skip_nothing_explains_is_refused(self):
        lines = list(self.unit)
        lines[3] = 'GATE MA0=0.ON SA0=0.ON CR0=0X4000.ON K=0000'
        self.assertIn('is not the check of its words', refuse('skip with a bad check', lines))
        lines[3] = data_line('GATE', [('MA0', 1, 0), ('SA0', 3, 0), ('CR0', 1, 0x4000)])
        self.assertIn('nothing before it explains why', refuse('unexplained skip', lines))

    def test_a_word_read_after_an_abort_or_a_stop_is_refused(self):
        lines = list(PAGES['fault'])
        lines[lines.index('LVL LC4=SKIP LC5=SKIP LC6=SKIP LC7=SKIP K=1455')] = data_line('LVL', [('LC4', 1, 0x2605), ('LC5', 3, 0), ('LC6', 3, 0), ('LC7', 3, 0)])
        self.assertIn('LC4 was read after an abort in its block', refuse('after an abort', lines))
        lines = list(PAGES['stop'])
        lines[-2] = data_line('RCG', [('DFS', 1, 0), ('DIV', 1, 0xFFFFFFFF), ('CMD', 1, 0), ('CFG', 3, 0)])
        self.assertIn('CMD was read after the all-ones word of DIV', refuse('after a stop', lines))

    def test_lines_after_a_stop_are_refused(self):
        self.assertIn('is not the end line', refuse('after a stop', PAGES['stop'][:-1] + ['NEXT PLL', PAGES['stop'][-1]]))

    def test_a_budget_or_unnamed_word_is_refused(self):
        for token in ('BUDGET', 'NA'):
            lines = [line.replace('CMD=0', f'CMD={token}') for line in self.unit]
            self.assertIn('shipped payload', refuse(token, lines))

    def test_a_missing_page_is_refused(self):
        self.assertIn('no line starts with', refuse('nothing', [], text='PAYLOAD LOADED INDEX=0\nsomething else\n'))
        self.assertIn('no line starts with', refuse('lower case prefix', [], text=film(self.unit).replace('PAYLOAD INPSNAP2', 'payload inpsnap2')))

    def test_the_two_early_exits_of_the_payload_are_refused_for_what_they_are(self):
        self.assertIn('ran below EL2 and read nothing', refuse('below EL2', ['SKIPPED_NOT_AT_EL2']))
        self.assertIn('no guarded load', refuse('no load32', ['REFUSED_NO_LOAD32']))


def data_keys(lines):
    """The keys of the data lines in `lines`, in the order they are printed."""
    return [token.split('=')[0] for line in lines if line.split(' ')[0] in parse.BLOCKS and ' K=' in line for token in line.split(' ')[1:-1]]


class Partial(unittest.TestCase):
    """`--partial`: the page of a unit that hung, took an SError or reset ends at a line boundary and has no end line."""
    unit = PAGES['unit']

    def test_every_prefix_of_every_golden_page_is_read_with_the_flag_and_refused_without_it(self):
        for name, lines in PAGES.items():
            for n in range(2, len(lines)):
                with self.subTest(page=name, lines=n):
                    page = parse.parse_page(film(lines[:n]), partial=True)
                    self.assertFalse(page.complete)
                    self.assertIsNone(page.end)
                    self.assertEqual(page.lines, n)
                    message = refuse('no flag', lines[:n])
                    self.assertIn('a page without its end line is not read', message)
                    self.assertIn('--partial', message)

    def test_a_partial_page_has_exactly_the_words_its_lines_show_with_their_values(self):
        values = unit_values()
        for n in range(2, len(self.unit)):
            with self.subTest(lines=n):
                page = parse.parse_page(film(self.unit[:n]), partial=True)
                keys = data_keys(self.unit[:n])
                self.assertEqual(list(page.words), keys)
                self.assertEqual({key: word.value for key, word in page.words.items()}, {key: values[key] for key in keys})
                self.assertEqual(parse.plan_words(page), {parse.KEY_WORD[key]: values[key] for key in keys if key in parse.KEY_WORD})
        self.assertEqual(len(parse.parse_page(film(self.unit[:-1]), partial=True).words), 44, 'every word of the unit page, and no end line')

    def test_a_page_with_its_end_line_is_the_same_page_with_or_without_the_flag(self):
        for name, lines in PAGES.items():
            with self.subTest(page=name):
                whole = parse.parse_page(film(lines))
                self.assertTrue(whole.complete)
                self.assertEqual(parse.parse_page(film(lines), partial=True), whole)

    def test_the_default_is_strict(self):
        self.assertIs(inspect.signature(parse.parse_page).parameters['partial'].default, False)
        for name, lines in PAGES.items():
            for n in range(1, len(lines)):
                with self.subTest(page=name, lines=n):
                    self.assertIn('a page without its end line is not read', refuse('default', lines[:n]))

    def test_less_than_a_header_and_a_legend_is_no_page_even_with_the_flag(self):
        self.assertIn('even a partial page has its header and its legend', refuse('header only', self.unit[:1], partial=True))
        self.assertIn('no line starts with', refuse('nothing', [], text='PAYLOAD LOADED INDEX=0\nsomething else\n', partial=True))
        self.assertIn('ran below EL2 and read nothing', refuse('below EL2', ['SKIPPED_NOT_AT_EL2'], partial=True))
        self.assertIn('no guarded load', refuse('no load32', ['REFUSED_NO_LOAD32'], partial=True))
        self.assertEqual(parse.parse_page(film(self.unit[:2]), partial=True).words, {}, 'a header and a legend: nothing read yet, and nothing claimed')

    def test_every_line_but_the_last_is_checked_as_in_a_whole_page(self):
        for n in range(2, len(self.unit)):
            head = self.unit[:n]
            with self.subTest(lines=n):
                for k in range(n - 1):
                    refuse('missing', head[:k] + head[k + 1:], partial=True)
                for k in range(n):
                    refuse('duplicated', head[:k + 1] + head[k:], partial=True)
                for k in range(n - 1):
                    swapped = list(head)
                    swapped[k], swapped[k + 1] = swapped[k + 1], swapped[k]
                    refuse('swapped', swapped, partial=True)
                for k, line in enumerate(head):
                    if line.split(' ')[0] in parse.BLOCKS and ' K=' in line:
                        text, _, check = line.rpartition(' K=')
                        lines = list(head)
                        lines[k] = f'{text} K={check[:-1]}{"0" if check[-1] != "0" else "1"}'
                        self.assertIn('is not the check of its words', refuse('check', lines, partial=True))

    def test_the_last_line_of_a_partial_page_is_checked_like_any_other(self):
        for n in range(2, len(self.unit)):
            line = self.unit[n - 1]
            if line.split(' ')[0] not in parse.BLOCKS or ' K=' not in line:
                continue
            text, _, check = line.rpartition(' K=')
            tokens = text.split(' ')
            wrong = {'a dropped word': ' '.join(tokens[:-1]) + f' K={check}', 'an extra word': f'{text} GV=1 K={check}', 'a lower-case check': f'{text} K={check.lower()}' if check != check.lower() else None,
                     'a lower-case number': text.replace('=0X', '=0x') + f' K={check}' if '=0X' in text else None,
                     'swapped words': ' '.join([tokens[0], tokens[2], tokens[1]] + tokens[3:]) + f' K={check}' if len(tokens) > 2 else None, 'no check': text}
            for what, bad in wrong.items():
                if bad is not None:
                    with self.subTest(lines=n, what=what):
                        refuse(what, self.unit[:n - 1] + [bad], partial=True)

    def test_a_wrong_verdict_an_unexplained_skip_and_a_duplicated_key_are_refused_in_a_partial_page(self):
        lines = [line.replace('MA0=0.ON', 'MA0=0.OFF') for line in self.unit[:5]]
        self.assertNotEqual(lines, self.unit[:5])
        refuse('verdict', lines, partial=True)
        lines = list(self.unit[:5])
        lines[3] = data_line('GATE', [('MA0', 1, 0), ('SA0', 3, 0), ('CR0', 1, 0x4000)])
        self.assertIn('nothing before it explains why', refuse('unexplained skip', lines, partial=True))
        lines = list(self.unit[:5])
        lines[3] = lines[3].replace('SA0=0.ON', 'MA0=0.ON')
        self.assertIn('is not the word SA0', refuse('duplicated key', lines, partial=True))

    def test_a_page_that_ends_after_the_wrapper_head_is_read_whatever_the_gate_says(self):
        for name in ('unit', 'gateoff', 'gateerr', 'hwvote'):
            lines = PAGES[name][:19]
            with self.subTest(page=name):
                self.assertEqual(lines[-1], 'NEXT QUP')
                page = parse.parse_page(film(lines), partial=True)
                self.assertEqual((page.complete, page.gated, 'VER' in page.words), (False, None, False))
                self.assertIn('a page without its end line is not read', refuse('no flag', lines))

    def test_a_gated_wrapper_line_without_an_end_line_is_checked_against_the_words(self):
        for name, gate in (('gateoff', 'OFF'), ('gateerr', 'ERR'), ('hwvote', 'OFF')):
            page = parse.parse_page(film(PAGES[name][:-1]), partial=True)
            self.assertEqual((page.complete, page.gated), (False, gate), name)
        self.assertIn('give the gate OFF', refuse('the wrong gate text', PAGES['gateoff'][:-2] + ['QUP GATED ERR'], partial=True))
        self.assertIn('give the gate OPEN', refuse('gated behind an open gate', self.unit[:19] + ['QUP GATED OFF'], partial=True))
        self.assertIn('close its gate', refuse('read behind a closed gate', PAGES['gateoff'][:-2] + QUP_OK[1:], partial=True))

    def test_a_stop_page_ends_at_the_stop_and_nothing_may_follow_it(self):
        lines = PAGES['stop'][:-1]
        page = parse.parse_page(film(lines), partial=True)
        self.assertEqual((page.complete, page.stop), (False, 'DIV'))
        self.assertNotIn('cmd', parse.plan_words(page))
        self.assertIn('is not the end line', refuse('after the stop', lines + ['NEXT PLL'], partial=True))
        lines[-1] = data_line('RCG', [('DFS', 1, 0), ('DIV', 1, 0xFFFFFFFF), ('CMD', 1, 0), ('CFG', 3, 0)])
        self.assertIn('CMD was read after the all-ones word of DIV', refuse('word after the stop', lines, partial=True))

    def test_an_abort_page_ends_inside_the_dead_block_and_nothing_may_be_read_behind_the_abort(self):
        lines = PAGES['fault'][:13]
        self.assertTrue(lines[-1].startswith('LVL LC0=0X2601 LC1=0X2602 LC2=FAULT:6 LC3=SKIP'))
        page = parse.parse_page(film(lines), partial=True)
        self.assertEqual((page.words['LC2'], page.words['LC3']), (parse.Word('FAULT', 6), parse.Word('SKIP', 0)))
        self.assertNotIn('lv_cfg2', parse.plan_words(page))
        lines.append(data_line('LVL', [('LC4', 1, 0x2605), ('LC5', 3, 0), ('LC6', 3, 0), ('LC7', 3, 0)]))
        self.assertIn('LC4 was read after an abort in its block', refuse('word after the abort', lines, partial=True))

    def test_the_end_line_of_a_page_that_has_one_is_checked_in_full_with_the_flag(self):
        end = self.unit[-1]
        for old, new, message in (('LOADS=44', 'LOADS=43', 'LOADS=43'), ('FAULTS=0', 'FAULTS=1', 'FAULTS=1'), ('SKIPS=0', 'SKIPS=1', 'SKIPS=1'), ('BLK=0', 'BLK=1', 'BLK=1'),
                                  ('CUTS=0', 'CUTS=1', 'CUTS=1'), ('LINES=21', 'LINES=20', 'LINES=20'), ('STOP=NONE', 'STOP=VER', 'STOP=VER'), ('LOADS=44', 'LOADS=044', 'is not the end line')):
            with self.subTest(change=(old, new)):
                self.assertIn(message, refuse('end', self.unit[:-1] + [end.replace(old, new)], partial=True))
        self.assertIn('after the end line', refuse('twice', self.unit + self.unit, partial=True))
        self.assertIn('after the end line', refuse('trailer', self.unit + ['NEXT GATE'], partial=True))
        self.assertIn('expected', refuse('end line too early', self.unit[:11] + [end], partial=True))
        self.assertIn('is not a data line', refuse('end line instead of a data line', self.unit[:12] + [end], partial=True))

    def test_the_plan_stops_on_the_first_word_a_partial_page_lacks(self):
        for label, values in (('plain', unit_values()), ('dfs', dfs_values())):
            lines = render(values)
            for n in range(2, len(lines)):
                words = parse.plan_words(parse.parse_page(film(lines[:n]), partial=True))
                lacking = [name for name in plan.IDENTITY_WORDS if name not in words]
                with self.subTest(survey=label, lines=n):
                    if lacking:
                        with self.assertRaises(plan.PlanError) as stopped:
                            plan.build_clock_plan(words)
                        self.assertIn(f"survey word '{lacking[0]}' missing", str(stopped.exception))
                    else:
                        self.assertEqual(n, len(lines) - 1, 'only the page that lost nothing but its end line has every word')
                        self.assertEqual(plan.build_clock_plan(words), plan.build_clock_plan(words_of(values)))

    def test_a_hang_at_the_version_word_is_a_page_that_ends_after_the_wrapper_head(self):
        lines = self.unit[:19]
        self.assertEqual(lines[-1], 'NEXT QUP')
        page = parse.parse_page(film(lines), partial=True)
        self.assertEqual((len(page.words), len(parse.plan_words(page))), (43, 40))
        with self.assertRaises(plan.PlanError) as stopped:
            plan.build_clock_plan(parse.plan_words(page))
        self.assertIn("survey word 'qup_ver' missing", str(stopped.exception))


class Model(unittest.TestCase):
    """The pages the payload's host model prints are the golden pages above. SKIPs, never passes, without Q1N1_SP11_TREE (a q1n1 worktree that has payloads/inpsnap2.c) and clang."""

    @classmethod
    def setUpClass(cls):
        tree = os.environ.get('Q1N1_SP11_TREE')
        source = Path(tree) / 'tools' / 'test-q1n1-sp11-payload-inpsnap2.c' if tree else None
        if source is None or not source.is_file():
            raise unittest.SkipTest('Q1N1_SP11_TREE is not a q1n1 worktree with the host model of inpsnap2')
        if not shutil.which('clang'):
            raise unittest.SkipTest('clang not installed')
        BUILD.mkdir(parents=True, exist_ok=True)
        cls.tree, cls.binary = Path(tree), BUILD / 'model'
        done = subprocess.run(['clang', '-std=gnu11', '-O1', '-w', '-DQ1N1_PAYLOAD_HOST_TEST=1', f'-I{tree}/platform/uefi', f'-I{tree}/payloads', '-o', str(cls.binary), str(source)],
                              capture_output=True, text=True, cwd=tree)
        if done.returncode:
            raise RuntimeError('the host model did not build:\n' + done.stderr[-1500:])

    def printed(self, *options):
        done = subprocess.run([str(self.binary), *options], capture_output=True, text=True, cwd=self.tree, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr[-500:])
        return done.stdout

    def test_every_golden_page_is_a_page_the_model_prints_and_parses(self):
        for name, lines in PAGES.items():
            with self.subTest(page=name):
                shown = self.printed('--scenario', name)
                self.assertEqual(shown, film(lines))
                page = parse.parse_page(shown)
                self.assertEqual(page.end['LINES'], len(lines))

    def test_the_nominal_page_of_the_model_is_the_unit_page(self):
        self.assertEqual(self.printed('--page'), film(PAGES['unit']))

    def test_every_prefix_of_every_page_the_model_prints_is_read_with_the_flag_and_refused_without_it(self):
        for name in PAGES:
            lines = self.printed('--scenario', name).splitlines()
            for n in range(2, len(lines)):
                text = '\n'.join(lines[:n]) + '\n'
                with self.subTest(page=name, lines=n):
                    self.assertFalse(parse.parse_page(text, partial=True).complete)
                    self.assertIn('a page without its end line is not read', refuse('no flag', [], text=text))

    def test_the_two_early_exits_the_model_prints_are_refused_by_name(self):
        for scenario, line in (('el1', 'SKIPPED_NOT_AT_EL2'), ('noload32', 'REFUSED_NO_LOAD32')):
            with self.subTest(scenario=scenario):
                shown = self.printed('--scenario', scenario)
                self.assertEqual(shown, film([line]))
                with self.assertRaises(parse.PageError) as error:
                    parse.parse_page(shown)
                self.assertIn(line, str(error.exception))

    def test_the_models_table_is_the_parsers_key_map_and_addresses(self):
        rows = [line.split() for line in self.printed('--table').splitlines()]
        windows = {'GCC': 0x00100000, 'QUP': 0x00bc0000}
        for key, window, address, _ in rows:
            base, offset = parse.ADDRESS[key]
            self.assertEqual(int(address, 16), windows[window] + offset, key)
            self.assertEqual(window, 'GCC' if base == 'gcc' else 'QUP', key)
        self.assertEqual(sorted(row[0] for row in rows), sorted(parse.KEYS))


class Cli(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        BUILD.mkdir(parents=True, exist_ok=True)

    def run_cli(self, name, lines, *options, text=None):
        path = BUILD / f'{name}.txt'
        path.write_text(text if text is not None else film(lines), encoding='utf-8')
        return subprocess.run([sys.executable, '-B', str(ROOT / 'tools' / 'sp11_inpsnap2_parse.py'), str(path), *options], capture_output=True, text=True)

    def test_it_prints_the_words_and_writes_the_object_the_plan_reads(self):
        out = BUILD / 'words.json'
        done = self.run_cli('unit', PAGES['unit'], '--json', str(out))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('SE0  0x80004000 .OFF', done.stdout)
        self.assertEqual(json.loads(out.read_text(encoding='utf-8')), {name: f'0x{value:08X}' for name, value in words_of(unit_values()).items()})
        planned = subprocess.run([sys.executable, '-B', str(ROOT / 'tools' / 'geni_uart_plan.py'), 'plan', str(out)], capture_output=True, text=True)
        self.assertEqual(planned.returncode, 0, planned.stdout + planned.stderr)

    def test_it_runs_the_plan_on_a_dfs_page_and_stops_on_a_gated_one(self):
        done = self.run_cli('dfs', render(dfs_values()), '--plan')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('PLAN: dfs', done.stdout)
        done = self.run_cli('gated', PAGES['gateoff'], '--plan')
        self.assertEqual(done.returncode, 2)
        self.assertIn("STOP: survey word 'qup_ver' missing", done.stdout)

    def test_it_refuses_a_bad_page_with_a_status_and_a_message(self):
        done = self.run_cli('bad', self.unit_without(5))
        self.assertEqual(done.returncode, 1)
        self.assertIn('PAGE REFUSED', done.stderr)
        self.assertEqual(done.stdout, '')

    def test_a_partial_page_is_refused_unless_the_flag_is_given(self):
        lines = PAGES['unit'][:19]
        done = self.run_cli('partial-refused', lines)
        self.assertEqual(done.returncode, 1)
        self.assertIn('PAGE REFUSED', done.stderr)
        self.assertIn('a page without its end line is not read', done.stderr)
        self.assertIn('--partial', done.stderr)
        self.assertEqual(done.stdout, '')
        done = self.run_cli('partial-short', lines[:1], '--partial')
        self.assertEqual((done.returncode, done.stdout), (1, ''))
        self.assertIn('header', done.stderr)

    def test_with_the_flag_a_partial_page_prints_the_banner_and_the_plan_stops_on_the_version_word(self):
        out = BUILD / 'partial.json'
        done = self.run_cli('partial', PAGES['unit'][:19], '--partial', '--json', str(out), '--plan')
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertTrue(done.stdout.startswith('PARTIAL PAGE: 19 lines and no end line'), done.stdout[:200])
        self.assertIn('VER  (not on the page)', done.stdout)
        self.assertIn('NO END LINE', done.stdout)
        self.assertIn("STOP: survey word 'qup_ver' missing", done.stdout)
        written = json.loads(out.read_text(encoding='utf-8'))
        self.assertEqual(len(written), 40)
        self.assertNotIn('qup_ver', written)
        planned = subprocess.run([sys.executable, '-B', str(ROOT / 'tools' / 'geni_uart_plan.py'), 'plan', str(out)], capture_output=True, text=True)
        self.assertNotEqual(planned.returncode, 0)
        self.assertIn("'qup_ver' missing", planned.stdout + planned.stderr)
        done = self.run_cli('partial-mid', PAGES['unit'][:5], '--partial', '--plan')
        self.assertEqual(done.returncode, 2)
        self.assertIn("STOP: survey word 'dfs' missing", done.stdout)

    def test_the_flag_changes_nothing_for_a_whole_page(self):
        plain = self.run_cli('whole', PAGES['unit'])
        flagged = self.run_cli('whole', PAGES['unit'], '--partial')
        self.assertEqual((flagged.returncode, flagged.stdout, flagged.stderr), (plain.returncode, plain.stdout, plain.stderr))
        self.assertNotIn('PARTIAL', flagged.stdout)
        done = self.run_cli('whole-bad', self.unit_without(5), '--partial')
        self.assertEqual((done.returncode, done.stdout), (1, ''))

    @staticmethod
    def unit_without(n):
        return PAGES['unit'][:n] + PAGES['unit'][n + 1:]


if __name__ == '__main__':
    unittest.main()
