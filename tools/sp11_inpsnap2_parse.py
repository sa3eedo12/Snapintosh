#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Parser of the page of the payload `inpsnap2` (stage I0 of the input plan: the read-only survey of the clock, mode-flag and engine words that decide how the Type Cover link's UART engine can
later be clocked). It turns the film transcription of the page into the object of survey words that `tools/geni_uart_plan.py plan` consumes, and refuses a page that does not add up.

    python3 tools/sp11_inpsnap2_parse.py film.txt                       # the transcription (lines that are not the payload's are ignored); prints the words
    python3 tools/sp11_inpsnap2_parse.py film.txt --json words.json     # and writes the object `geni_uart_plan.py plan words.json` reads
    python3 tools/sp11_inpsnap2_parse.py film.txt --plan                # and runs the plan on it: the plan or the STOP
    python3 tools/sp11_inpsnap2_parse.py film.txt --partial [--plan]    # a page that stops without its end line after a hang or an SError: the lines it has, and the plan's STOP

The page is strict. Every line of it is expected in its place with its keys in their order (the page has the same 21 lines whatever the unit answers, but for an abort, an all-ones word and a gated
wrapper), every number is in the payload's own form (a digit below 10, `0X` and the fewest upper-case hex digits above), a branch clock word carries the verdict its value gives, every data line
carries the check `K=` of its words (CRC-16/CCITT-FALSE of five bytes a word: the state, then the value least significant byte first), a word is `SKIP` only after an abort in its block or an all-ones word,
the wrapper's line is the one its gate gives, and the end line's counters are the page's own. A missing, extra, duplicated or reordered line, a wrong key, a bad check, a page without its end line (but with
`--partial`) and a page printed twice are errors (PageError), never a partial answer: a transcription that does not add up is read again from the film.

`--partial` (parse_page(..., partial=True)) is for the one page that cannot have an end line: the unit hung, took an SError or reset behind the last line on the panel. The page then ends at a line
boundary after its header and legend; every line it has is checked exactly as in a whole page, the end line's counters are not (there is none), the Page is marked incomplete and the summary says so, and
the words it shows are the words that read, so that the plan stops by itself on the first word it needs that the page lacks (a hang at the version word: `qup_ver`). A page that ends there because the
transcription was cut short looks the same: the film must show the panel ending there. A page that has its end line is checked in full whatever the flag says. The flag is never the default.
The words of the page are not the unit's until the film says so (evidence: EXPERIMENTAL; hardware NOT RUN)."""
import argparse
import binascii
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys

PREFIX = 'PAYLOAD INPSNAP2 '
HEADER = 'READ ONLY EL=2 STAGE=I0 CAP=44/24'
LEGEND = 'KEY=VALUE .ON .OFF .HW .ODD=CLOCK FAULT:6=ABORT SKIP=NOT READ K=LINE CRC16'
FULL = 0xFFFFFFFF
LAYOUT = (
    ('GATE', (('MA0', 'SA0', 'CR0'), ('SE0', 'VT0', 'GV'))),
    ('RCG', (('DFS', 'DIV', 'CMD', 'CFG'), ('M', 'N', 'D'))),
    ('PLL', (('MODE', 'OP', 'ST'), ('L', 'AL', 'USR'))),
    ('LVL', (('LC0', 'LC1', 'LC2', 'LC3'), ('LC4', 'LC5', 'LC6', 'LC7'), ('LM0', 'LM1', 'LM2', 'LM3'), ('LM4', 'LM5', 'LM6', 'LM7'), ('LN0', 'LN1', 'LN2', 'LN3'),
             ('LN4', 'LN5', 'LN6', 'LN7'))),
    ('QUP', (('VER',),)),
)
BLOCKS = tuple(block for block, _ in LAYOUT)
KEYS = tuple(key for _, lines in LAYOUT for line in lines for key in line)
CLOCK_KEYS = ('MA0', 'SA0', 'CR0', 'SE0')           # the branch clock words: the page gives each a verdict
HWCG_KEYS = {'MA0': 6, 'SA0': 7}                    # the two AHB clocks with a hardware gating bit, and their vote bit in VT0
WRAPPER_GATE_KEYS = ('MA0', 'SA0', 'CR0')           # the clock words the wrapper's read depends on: no word of the host plan
STATE_CODE = {'OK': 1, 'FAULT': 2, 'SKIP': 3}
# The page's key and the word of the host plan's survey (tools/geni_uart_plan.py SURVEY_READS) it is; the three gate words are the payload's own.
KEY_WORD = {'SE0': 'cbcr', 'VT0': 'vote', 'GV': 'gpll0_vote', 'DIV': 'div', 'DFS': 'dfs', 'CMD': 'cmd', 'CFG': 'cfg', 'M': 'm', 'N': 'n', 'D': 'd', 'MODE': 'gpll0_mode',
            'OP': 'gpll0_opmode', 'ST': 'gpll0_status', 'L': 'gpll0_l', 'AL': 'gpll0_alpha', 'USR': 'gpll0_user', 'VER': 'qup_ver',
            **{f'LC{i}': f'lv_cfg{i}' for i in range(8)}, **{f'LM{i}': f'lv_m{i}' for i in range(8)}, **{f'LN{i}': f'lv_n{i}' for i in range(8)}}
# The window and offset of every key, as the payload's header has them (payloads/inpsnap2-regs.h of the fork): the test compares them with the host plan's survey list.
_GCC = {'MA0': 0x23000, 'SA0': 0x23004, 'CR0': 0x23008, 'SE0': 0x42274, 'VT0': 0x52020, 'GV': 0x52030, 'DFS': 0x4229C, 'DIV': 0x42284, 'CMD': 0x42288, 'CFG': 0x4228C,
        'M': 0x42290, 'N': 0x42294, 'D': 0x42298, 'MODE': 0x0, 'OP': 0x4, 'ST': 0xC, 'L': 0x10, 'AL': 0x14, 'USR': 0x18,
        **{f'LC{i}': 0x422A4 + 4 * i for i in range(8)}, **{f'LM{i}': 0x422E4 + 4 * i for i in range(8)}, **{f'LN{i}': 0x42324 + 4 * i for i in range(8)}}
ADDRESS = {**{key: ('gcc', offset) for key, offset in _GCC.items()}, 'VER': ('wrap0', 0x4)}
TOKEN = re.compile(r'([A-Z][A-Z0-9]*)=(FAULT:(?:0|[1-9][0-9]*)|SKIP|BUDGET|NA|0X[0-9A-F]+|[0-9]+)(?:\.(ON|OFF|HW|ODD))?')


class PageError(ValueError):
    """The transcription is not a page of the payload."""


class _PageEnds(Exception):
    """parse_page(partial=True) only: the transcription ends at a line boundary, before the end line."""


@dataclass(frozen=True)
class Word:
    state: str                  # 'OK' (a word that read), 'FAULT' (an abort; `value` is its code) or 'SKIP' (not read; `value` is 0)
    value: int
    clock: str = None           # the verdict of a branch clock word as the page printed it


@dataclass
class Page:
    words: dict                 # key -> Word, for every word the page shows
    stop: str                   # the key of the first all-ones word, or None
    gated: str                  # 'OFF' or 'ERR' when the wrapper's word was not read, else None
    end: dict                   # the end line's fields, or None for a partial page
    lines: int                  # the lines of the page, the end line included
    complete: bool = True       # False for a partial page: it ends at a line boundary without its end line, and no counter was checked


def number(text):
    """The payload's number form: one digit below 10, `0X` and the fewest upper-case hex digits from 10 up."""
    if re.fullmatch(r'[0-9]', text):
        return int(text)
    found = re.fullmatch(r'0X([1-9A-F][0-9A-F]{0,7})', text)
    if not found or int(found.group(1), 16) < 10:
        raise PageError(f'{text!r} is not a number in the form the page prints (a digit below 10, else 0X and the fewest upper-case hex digits of at most 32 bits)')
    return int(found.group(1), 16)


def clock_verdict(value, hwcg):
    """The verdict of a branch clock word: running when CLK_OFF (bit 31) is clear or the NoC FSM status (bits 30:28) is 2, `ODD` for all ones, `HW` for CLK_OFF set with HW_CTL (bit 1) set on a clock
    with a hardware gating bit, else `OFF` (VERIFIED in clk-branch.c/h at the pinned Linux: cbc:43-60, cbh:63-65, cbc:17-27)."""
    if value == FULL:
        return 'ODD'
    if not value >> 31 or (value >> 28) & 7 == 2:
        return 'ON'
    return 'HW' if hwcg and value >> 1 & 1 else 'OFF'


def line_check(words):
    """CRC-16/CCITT-FALSE of five bytes a word: its state (1, 2, 3), then its value least significant byte first."""
    data = b''.join(bytes([STATE_CODE[word.state]]) + word.value.to_bytes(4, 'little') for word in words)
    return binascii.crc_hqx(data, 0xFFFF)


def wrapper_gate(words):
    """'OPEN', 'OFF' (a wrapper clock word that read says stopped, or an AHB clock in hardware gating mode with no vote) or 'ERR' (what the gate needs did not read, read all ones, or the vote word
    VT0 is zero): the rule the page's `QUP` block follows, from the words of the page."""
    vote = words.get('VT0')
    off = unknown = False
    for key in WRAPPER_GATE_KEYS:
        word = words.get(key)
        if word is None or word.state != 'OK':
            unknown = True
            continue
        verdict = clock_verdict(word.value, key in HWCG_KEYS)
        if verdict == 'HW':
            verdict = 'ODD' if vote is None or vote.state != 'OK' else 'ON' if vote.value >> HWCG_KEYS[key] & 1 else 'OFF'
        off |= verdict == 'OFF'
        unknown |= verdict == 'ODD'
    if vote is None or vote.state != 'OK' or vote.value in (0, FULL):
        unknown = True
    return 'OFF' if off else 'ERR' if unknown else 'OPEN'


def parse_word(key, text):
    found = TOKEN.fullmatch(text)
    if not found or found.group(1) != key:
        raise PageError(f'{text!r} is not the word {key}')
    value, clock = found.group(2), found.group(3)
    if value in ('BUDGET', 'NA'):
        raise PageError(f'{key}={value} is not on a page of the shipped payload (44 loads, 24 lines, a table without a defect)')
    if value == 'SKIP':
        if clock:
            raise PageError(f'{key} is not read and carries a verdict')
        return Word('SKIP', 0)
    if value.startswith('FAULT:'):
        if clock:
            raise PageError(f'{key} aborted and carries a verdict')
        code = int(value[6:])
        if code > FULL:
            raise PageError(f'{key}: the abort code is out of range')
        return Word('FAULT', code)
    word = number(value)
    if key in CLOCK_KEYS:
        want = clock_verdict(word, key in HWCG_KEYS)
        if clock != want:
            raise PageError(f'{key}={value} must carry .{want}, the page says {clock and "." + clock}')
    elif clock:
        raise PageError(f'{key} is not a branch clock word and carries a verdict')
    return Word('OK', word, clock)


def parse_line(block, keys, text):
    found = re.fullmatch(rf'{block} (\S+(?: \S+)*) K=([0-9A-F]{{4}})', text)
    if not found:
        raise PageError(f'{text!r} is not a data line of the block {block} (the head, its words and K= with four upper-case hex digits)')
    tokens = found.group(1).split(' ')
    if len(tokens) != len(keys):
        raise PageError(f'{text!r} has {len(tokens)} words, the line has {len(keys)} ({", ".join(keys)})')
    words = [parse_word(key, token) for key, token in zip(keys, tokens)]
    if line_check(words) != int(found.group(2), 16):
        raise PageError(f'{text!r}: K={found.group(2)} is not the check of its words ({line_check(words):04X})')
    return words


def page_lines(text):
    """The lines of the page (without the prefix) in a transcription; the runner's own lines and anything else are not the page. Leading and trailing blanks are the film's."""
    return [line.strip()[len(PREFIX):] for line in text.splitlines() if line.strip().startswith(PREFIX)]


def parse_page(text, partial=False):
    """The Page of a transcription, or a PageError that names what does not add up. With `partial`, a page that ends at a line boundary after its header and legend without its end line (a hang, an
    SError or a reset behind the last line on the panel) is read as far as it goes: each line it has is checked as in a whole page, no counter can be, and the Page is marked incomplete."""
    body = page_lines(text)
    if not body:
        raise PageError(f'no line starts with {PREFIX!r}')
    for exit_line, meaning in (('SKIPPED_NOT_AT_EL2', 'the payload ran below EL2 and read nothing'), ('REFUSED_NO_LOAD32', 'the runner gave the payload no guarded load and it read nothing')):
        if body[0] == exit_line:
            raise PageError(f'{exit_line}: {meaning}; there is no page')
    at = 0

    def take(expected=None):
        nonlocal at
        if at >= len(body):
            if partial and at >= 2:
                raise _PageEnds
            hint = ('even a partial page has its header and its legend' if partial
                    else 'a page without its end line is not read (a page that stops after a hang or an SError is read only with --partial)')
            raise PageError(f'the page ends after {at} lines, before {expected or "its next line"}: {hint}')
        line = body[at]
        if expected is not None and line != expected:
            raise PageError(f'line {at + 1} is {line!r}, expected {expected!r}')
        at += 1
        return line

    take(HEADER)
    take(LEGEND)
    words, dead, stop, gated = {}, set(), None, None
    try:
        for block, lines in LAYOUT:
            if stop:
                break
            take(f'NEXT {block}')
            if block == 'QUP':
                if at >= len(body):
                    take()      # behind `NEXT QUP` the page cannot say whether the wrapper was gated or its read hung
                gate = wrapper_gate(words)
                if body[at].startswith('QUP GATED '):
                    shown = take()
                    if shown not in ('QUP GATED OFF', 'QUP GATED ERR'):
                        raise PageError(f'line {at} is {shown!r}, not a gated wrapper line')
                    gated = shown[-3:].strip()
                    if gate != gated:
                        raise PageError(f'the page says {shown} but the words of the page give the gate {gate}')
                    break
                if gate != 'OPEN':
                    raise PageError(f'the wrapper was read although the words of the page close its gate ({gate})')
            for keys in lines:
                line_words = parse_line(block, keys, take())
                for key, word in zip(keys, line_words):
                    if key in words:
                        raise PageError(f'{key} is on the page twice')
                    if stop or block in dead:
                        if word.state != 'SKIP':
                            raise PageError(f'{key} was read after {"the all-ones word of " + stop if stop else "an abort in its block"}')
                    elif word.state == 'SKIP':
                        raise PageError(f'{key} is not read and nothing before it explains why (no abort in its block, no all-ones word)')
                    words[key] = word
                    if word.state == 'FAULT':
                        dead.add(block)
                    if word.state == 'OK' and word.value == FULL and not stop:
                        stop = key
                if stop:
                    break
        end = take()
    except _PageEnds:
        return Page(words, stop, gated, None, len(body), complete=False)
    found = re.fullmatch(r'END LOADS=(0|[1-9][0-9]*) FAULTS=(0|[1-9][0-9]*) SKIPS=(0|[1-9][0-9]*) BLK=(\S+) CUTS=(0|[1-9][0-9]*) LINES=(0|[1-9][0-9]*) STOP=([A-Z][A-Z0-9]*)', end)
    if not found:
        raise PageError(f'{end!r} is not the end line')
    # The counters are decimal, the block mask is in the number form of the page.
    fields = {'LOADS': int(found.group(1)), 'FAULTS': int(found.group(2)), 'SKIPS': int(found.group(3)), 'BLK': number(found.group(4)), 'CUTS': int(found.group(5)),
              'LINES': int(found.group(6)), 'STOP': found.group(7) if found.group(7) != 'NONE' else None}
    if at != len(body):
        raise PageError(f'{len(body) - at} line(s) after the end line: a page printed twice, or a transcription of two pages')
    states = [word.state for word in words.values()]
    flagged = {block for block, lines in LAYOUT for line in lines for key in line if key in words and words[key].state != 'OK'}
    if gated:
        flagged.add('QUP')
    if stop:
        flagged |= set(BLOCKS[next(i for i, (block, lines) in enumerate(LAYOUT) if any(stop in line for line in lines)):])
    expected = {'LOADS': sum(1 for s in states if s in ('OK', 'FAULT')), 'FAULTS': states.count('FAULT'), 'SKIPS': states.count('SKIP'), 'CUTS': 0, 'LINES': len(body),
                'STOP': stop, 'BLK': sum(1 << BLOCKS.index(block) for block in flagged)}
    for name, value in expected.items():
        if fields[name] != value:
            raise PageError(f'the end line says {name}={fields[name]}, the page adds up to {value}')
    return Page(words, stop, gated, fields, len(body))


def plan_words(page):
    """The object `geni_uart_plan.py plan` consumes: the plan's word names to the values of the words that read. A word that did not read is absent, so that the plan stops on it."""
    return {KEY_WORD[key]: word.value for key, word in page.words.items() if word.state == 'OK' and key in KEY_WORD}


def summary(page):
    shown = []
    for key in KEYS:
        word = page.words.get(key)
        if word is None:
            shown.append(f'{key:4s} (not on the page)')
        else:
            text = {'OK': f'0x{word.value:08X}' + (f' .{word.clock}' if word.clock else ''), 'FAULT': f'ABORT code {word.value}', 'SKIP': 'not read'}[word.state]
            shown.append(f'{key:4s} {text}')
    state = [f'stop at {page.stop}' if page.stop else 'no stop', f'wrapper gated {page.gated}' if page.gated else 'wrapper word read or not reached']
    if page.complete:
        return '\n'.join(shown + ['page: ' + '; '.join(state) + f'; end line {page.end}'])
    banner = (f'PARTIAL PAGE: {page.lines} lines and no end line (a hang, an SError or a reset behind the last line, or a transcription cut short): every line was checked, no counter could be; '
              'a key shown as not on the page was not read')
    return '\n'.join([banner] + shown + ['page: ' + '; '.join(state) + '; NO END LINE'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('film', help='the transcription of the page (a file, or - for the standard input)')
    parser.add_argument('--json', help='write the survey words object here')
    parser.add_argument('--plan', action='store_true', help='run tools/geni_uart_plan.py on the words')
    parser.add_argument('--partial', action='store_true', help='also read a page that ends without its end line after a hang or an SError: each line is checked as in a whole page, no counter can be, '
                        'and the plan stops on the first word the page lacks')
    args = parser.parse_args(argv)
    text = sys.stdin.read() if args.film == '-' else Path(args.film).read_text(encoding='utf-8')
    try:
        page = parse_page(text, partial=args.partial)
    except PageError as error:
        print(f'PAGE REFUSED: {error}', file=sys.stderr)
        return 1
    words = plan_words(page)
    print(summary(page))
    if args.json:
        Path(args.json).write_text(json.dumps({name: f'0x{value:08X}' for name, value in words.items()}, indent=1) + '\n', encoding='utf-8')
    if args.plan:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import geni_uart_plan as plan
        try:
            built = plan.build_clock_plan(words)
        except plan.PlanError as error:
            print(f'STOP: {error}')
            return 2
        print(f'PLAN: {built.mode}, {len(built.rows)} row(s), clk_sel {built.clk_sel}, divider {built.div}')
        for note in built.notes:
            print(f'  {note}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
