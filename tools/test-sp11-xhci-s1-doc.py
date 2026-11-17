#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of docs/SP11-XHCI-S1.md, the runbook and outcome tree of the xHCI S1 No-Op DMA test (authorisation point A1). Host only; reads files, writes nothing.

    python3 tools/test-sp11-xhci-s1-doc.py
    Q1N1_SP11_TREE=<q1n1 checkout> python3 tools/test-sp11-xhci-s1-doc.py    # also: the names and numbers against the payload's sources

* the sections the record cites (4, the operator's actions; 7, what the live link needs) exist under those numbers, and the links and anchors resolve;
* the thirteen registers of the document are the record's; the write counts it gives per stage follow from the register groups;
* every verdict, reason, stage, arena reason and refusal the payload can print is in the outcome tree, every outcome has a meaning and a next step;
* section 7 names each register class the live link touches (DWC3 core and glue, PHY, clocks, port reset, repeater, retimer, I2C, SPMI, GPIO, UCSI, SMMU);
* no machine-identifying data, ASCII, under the size and line limits;
* the record names the SHA-256 of this document, so the document (and the q1n1 payload document) names no digest of the record, no abbreviation of one and no
  size or line count of it: they would change the digest they name, and an equality test could never hold, so the check is by pattern, with faulty texts.
Each check is a function of the text with a faulty text it must reject; the checks against the payload SKIP, and say so, without Q1N1_SP11_TREE.
"""
from pathlib import Path
import hashlib
import os
import re
import sys
import unittest

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
DOC = ROOT / 'docs' / 'SP11-XHCI-S1.md'
sys.dont_write_bytecode = True
sys.path.insert(0, str(TOOLS))
import sp11_xhci_s1_record as rec  # noqa: E402
from sp11_xhci_s1_budget_check import budget_problems  # noqa: E402

TREE = os.environ.get('Q1N1_SP11_TREE')
# Pinned here and compared with the payload's sources when a q1n1 tree is given (payloads/xhci-regs.h and payloads/xhci.c).
VERDICTS = ('NOOP_OK', 'NOOP_TIMEOUT', 'NOOP_HSE', 'NOOP_BAD_COMPLETION', 'REFUSED')
REASONS = ('HALT', 'HCRST', 'CNR', 'HCH', 'NOT_HELD', 'EVENT', 'READ', 'CLOCK', 'STORE', 'HSE', 'HCE', 'CODE', 'POINTER', 'CYCLE', 'BUDGET', 'LINES', 'REDIRECT')
STAGES = ('ARENA', 'RESET', 'PROGRAM', 'RUN', 'NOOP')
ARENA_REASONS = ('NO_MAP', 'NOT_FREE', 'NO_AC64', 'TOO_BIG', 'SMALL_STACK')
REFUSALS = ('NO_COUNTER', 'NO_LOAD32', 'COUNTER_STUCK', 'READ_FAULT', 'NOT_XHCI', 'CAPLENGTH', 'HCIVERSION', 'DWC3_ID', 'OFFSET', 'HCE', 'HSE', 'CNR', 'RUNNING',
            'PAGESIZE', 'SMMU_UNREADABLE', 'SCRATCHPADS', 'NO_ARENA', 'SKIPPED_NOT_AT_EL2')
END_FLAGS = ('HALTED=NO', 'HALTED=UNKNOWN', 'STORE_REFUSED', 'LINES_CUT')
SECTIONS = ('1. Result in brief', '2. What A1 covers and what it does not', '3. The files, the record and the plan', '4. The runbook', '5. The expected page',
            '6. The outcome tree', '7. What the live link needs beyond A1', '8. The Mac-side logger', '9. Tests and evidence', '10. What stays UNKNOWN', '11. Sources')
SUBSECTIONS = ('4.1 Before the boot', '4.2 The run', '4.3 After the run', '4.4 Stop rules', '6.1 What the VERDICT means', '6.2 The refusals (nothing written)',
               '6.3 The plug watch, and the two answers together', '7.1 What q1n1\'s SP11-gated link writes today, against A1',
               '7.2 What the live link may need that q1n1 does not write at all', '7.3 The next requests, proposed')
LIVE_LINK_CLASSES = ('DWC3 core', 'GCTL', 'glue', 'PHY', 'Clocks', 'GDSC', 'PORTSC', 'port reset', 'repeater', 'Retimer', 'I2C', 'SPMI', 'GPIO', 'UCSI', 'SMMU',
                     'IMOD', 'doorbell', 'Type-C')
FORBIDDEN = re.compile('|'.join(['/Us' + 'ers/', '/ho' + 'me/', '/Vol' + 'umes/']) + r'|\bsae' + r'ed\b|host' + r'name|\b[0-9a-f]{2}(?::[0-9a-f]{2}){5}\b'
                       r'|\b(?:10|127|169\.254|172\.(?:1[6-9]|2\d|3[01])|192\.168)(?:\.\d{1,3}){2,3}\b', re.I)  # private and loopback addresses; xHCI section numbers are not


def slugify(heading):
    heading = heading.strip().lower().replace('`', '')
    return re.sub(r'\s', '-', re.sub(r'[^\w\- ]', '', heading))


def headings(text):
    return re.findall(r'^(#{1,6}) (.*)$', text, re.M)


def section(text, title):
    """The text of the heading whose title starts with `title`, up to the next heading of the same or a higher level."""
    lines, start, level = text.splitlines(), None, 0
    for i, line in enumerate(lines):
        match = re.match(r'^(#{1,6}) (.*)$', line)
        if not match:
            continue
        if start is None and match[2].startswith(title):
            start, level = i, len(match[1])
        elif start is not None and len(match[1]) <= level:
            return '\n'.join(lines[start:i])
    return '\n'.join(lines[start:]) if start is not None else ''


def tables(text):
    """[(header cells, [row cells])] of every pipe table."""
    found, block = [], []
    for line in text.splitlines() + ['']:
        if line.startswith('|'):
            block.append(line)
            continue
        if len(block) >= 3 and set(block[1].replace('|', '').replace(' ', '')) <= {'-', ':'}:
            cells = lambda s: [c.strip() for c in s.strip().strip('|').split('|')]  # noqa: E731
            found.append((cells(block[0]), [cells(row) for row in block[2:]]))
        block = []
    return found


def flat(text):
    return ' '.join(text.split())


def structure_problems(text):
    heads = [title for _, title in headings(text)]
    problems = [f'missing heading {title!r}' for title in SECTIONS + SUBSECTIONS if title not in heads]
    order = [heads.index(title) for title in SECTIONS if title in heads]
    if order != sorted(order):
        problems.append('sections out of order')
    return problems


def link_problems(text, base=DOC.parent):
    problems = []
    for target, anchor in re.findall(r'\]\(([^)#:]+\.md)(?:#([^)]*))?\)', text):
        path = base / target
        if not path.is_file():
            problems.append(f'{target} does not exist')
        elif anchor and anchor not in {slugify(h) for _, h in headings(path.read_text())}:
            problems.append(f'{target}#{anchor} has no such heading')
    for anchor in re.findall(r'\]\(#([^)]+)\)', text):
        if anchor not in {slugify(h) for _, h in headings(text)}:
            problems.append(f'#{anchor} has no such heading')
    return problems


def register_problems(text):
    """The doc's register table (name cells, block, offset) against the record's REGISTERS."""
    for header, rows in tables(text):
        if header[:3] == ['Register', 'Block', 'Offset']:
            break
    else:
        return ['no register table']
    doc = []
    for cells in rows:
        names, offsets = [n.strip() for n in cells[0].split(',')], [o.strip() for o in cells[2].split(',')]
        if len(names) != len(offsets):
            return [f'{cells[0]}: names and offsets differ in number']
        doc += [(name, cells[1], int(offset, 16)) for name, offset in zip(names, offsets)]
    want = sorted((name, where, offset) for name, where, offset, _ in rec.REGISTERS)
    return [] if sorted(doc) == want else [f'the table differs from the record: {sorted(set(doc) ^ set(want))}']


def count_problems(text):
    """The write counts of the page reading (1, 11, 13, 14, 17) from the register groups of the record: HCRST, the PROGRAM group, RS, the doorbell, the NOOP
    tail (ERDP twice and IMAN) and the halt."""
    program = sum('PROGRAM' in groups for *_, groups in rec.REGISTERS)
    tail = sum('NOOP' in groups for name, _, _, groups in rec.REGISTERS if name != 'DB0')
    doorbell = sum(name == 'DB0' for name, *_ in rec.REGISTERS)
    reset, run, halt = 1, 1, 1
    counts = {'1': reset, '11': reset + program, '13': reset + program + run + halt, '14': reset + program + run + doorbell + halt,
              '17': reset + program + run + doorbell + tail + halt}
    problems = [] if counts['17'] == rec.NOMINAL_STORES else [f'the record says {rec.NOMINAL_STORES} nominal stores, the groups give {counts["17"]}']
    page = flat(section(text, '5. The expected page'))
    wanted = {'1 is HCRST alone': counts['1'], '11 is through PROGRAM': counts['11'], '13 is RS written and the halt made': counts['13'],
              '14 is the doorbell rung and the halt made': counts['14'], '17 is the whole sequence': counts['17']}
    for phrase, value in wanted.items():
        if phrase not in page or int(phrase.split()[0]) != value:
            problems.append(f'{phrase!r} is not what the groups give ({value})')
    outcomes = flat(section(text, '6.1 What the VERDICT means'))
    for phrase in ('`WRITES=1`', '`WRITES=11`', '`WRITES=13`', '`WRITES=14`', '`WRITES=17`'):
        if phrase not in outcomes:
            problems.append(f'the outcome table never gives {phrase}')
    return problems


def outcome_problems(text):
    """Every outcome row has a page, a meaning and a next step (two lines each: what it means, what comes next); the numbers are O1 to O15 without a gap."""
    rows = []
    for header, body in tables(text):
        if header[:1] == ['#'] and 'Next' in header:
            rows += body
    numbers = [row[0] for row in rows]
    problems = [] if numbers == [f'O{n}' for n in range(1, 16)] else [f'the outcomes are {numbers}']
    for row in rows:
        if len(row) != 4 or not all(cell for cell in row):
            problems.append(f'{row[0]}: a cell is empty or missing')
    return problems


def coverage_problems(text, verdicts=VERDICTS, reasons=REASONS, stages=STAGES, arena=ARENA_REASONS, refusals=REFUSALS, flags=END_FLAGS):
    tree = flat(section(text, '6. The outcome tree')) + ' ' + flat(section(text, '5. The expected page'))
    problems = []
    for kind, names in (('verdict', verdicts), ('reason', reasons), ('stage', stages), ('arena reason', arena), ('refusal', refusals), ('END flag', flags)):
        problems += [f'{kind} {name} is not in the outcome tree' for name in names if not re.search(rf'(?<![A-Z_]){re.escape(name)}(?![A-Z_])', tree)]
    return problems


def live_link_problems(text):
    body = flat(section(text, '7. What the live link needs beyond A1'))
    return [f'section 7 never names {name!r}' for name in LIVE_LINK_CLASSES if name not in body]


def number_problems(text):
    body = flat(text)
    problems = []
    for phrase in ('thirteen xHCI registers', 'A nominal run makes 17 stores', 'about 71 s', 'five waits of 8 s', '30 s plug watch', 'NOT granted', 'Hardware status: NOT RUN',
                   'approved A1 in principle on 2026-11-17 at 02:10',
                   '30 s + 15 x 8 s = 150 s plus at most 7.9 s of bounded waits (3 x 2.3 s, and 1 s for a CNR wait in the pre-flight), about 158 s',
                   '30 s + 20 x 8 s = 190 s plus at most 10.2 s, about 200 s', 'a wait polled every 10 ms may end up to 10 ms late (about 0.1 s over a run)',
                   'the time the runner takes to draw the lines is UNKNOWN', 'HCRST or CNR did not clear in 1 s', 'a restart alone is not an xHC result',
                   'at or above `0xc100`'):
        if phrase not in body:
            problems.append(f'the text lacks {phrase!r}')
    if len(rec.REGISTERS) != 13 or rec.NOMINAL_STORES != 17 or 30 + 5 * 8 + 1 != 71:
        problems.append('the record or the timing arithmetic changed')
    if round(30 + 15 * 8 + 3 * 2.3 + 1) != 158 or round(30 + 20 * 8 + 4 * 2.3 + 1) != 200:
        problems.append('the worst-case durations of the text are not what the waits add up to (157.9 s and 200.2 s)')
    return problems


# What the delta review of 2026-11-17 (N1 to N7) had corrected, said in the words of the runbook: each phrase has a faulty text without it.
ROUND3_PHRASES = (
    # N1: LINES_CUT after a NOOP_OK is dropped SMMU1 reports, and how the stop rule reads it
    '`LINES_CUT` on the END line after any verdict except `NOOP_OK`', '**`LINES_CUT` after `VERDICT NOOP_OK`** does not stop the sequence',
    'reading it that way is the operator\'s decision, taken by approving the record', 'which is the optional second boot',
    '**`LINES_CUT` after `NOOP_OK`.**', 'prints 3, 2, 1 or 0 of the four `SMMU1` reports after 0, 1, 2 or 3 or more `CHG` lines',
    'after a success at arena 4 from one `CHG` line on, at arena 3 from two, at arena 2 from three, never at arena 1',
    'if an `R<k>` line that says `SMMU=CHANGED` has no `SMMU1 R<k>` line after it', 'nothing accounts for the cut: stop',
    'whatever the number of port changes in the watch, and never says `LINES_CUT`',
    # N2: a refusal in the first reads is the VERDICT line alone; what VALUE is
    'which appear only when the reads and checks of the controller\'s own words pass', 'The page is this one line, with no `U<c>` line',
    'an RTSOFF of `0xc0e0` puts ERSTSZ at `0xc108`: `VALUE=0XC108`', 'the page is that `VERDICT REFUSED=...` line alone',
    # N5: the stages that end the run, the event wait, the stick's controller
    'At the RUN or NOOP stage, a silence, a Host System Error or Host Controller Error (HSE or HCE), or a controller that does not start (O2, O3, O6, O7)',
    'A failure at the RESET or PROGRAM stage (O4, O5, and an HSE or HCE that the reset itself ends with, O7) ends the run',
    'at the RESET stage (`WRITES=1`: the reset itself ended with the error) it ended the run',
    'S0 observed the stick\'s controller as `usb_1_ss0`, with the stick in the port it was in that boot',
    'which connector it is on in this boot is not recorded', 'depends on where it is',
    # N7: the logger
    'The warning after three ticks with an empty `ioreg` names `--allow-empty-usb`', 'A baseline that is not the first tick\'s is printed as `BASELINE ... LATE after <n> pending ticks`',
    # round 4 (the final delta review, R1 to R11): the runbook is pinned by the record, the first-fault rule of the SMMU, the Host System and Host Controller
    # Errors, the doorbell count, the notes of 4.4 and 6.1, the topology of S0b
    'the record names the SHA-256 of this file\'s bytes in its section 1 (`--runbook` names the file; it defaults to `docs/SP11-XHCI-S1.md`)',
    'and so does a runbook whose bytes are not the ones the record names', 'this file cannot name the record\'s digest, its size or its line count',
    'this runbook is the file whose SHA-256 its section 1 names',
    '`SAME` does not prove that an attempt did not fault: the SMMU records only the first fault of a context bank until its FSR is cleared, which A1 never does (INFERRED, ARM IHI 0062)',
    'a `SAME` attempt may have faulted too, and the FAR and FSYNR0 of an `SMMU1 R<k>` line may belong to an earlier fault',
    'the DMA faulted and the SMMU, which records only the first fault of a context bank until its FSR is cleared (INFERRED), recorded nothing new',
    '`HCE` is an error inside the xHC, not a failed access',
    'does not stop the sequence when the note after the table of section 6.1 accounts for it (O11 says it stops otherwise)',
    'In the host model it can follow a success on `XHCI0.BIN` only',
    'A result of 100 characters or more takes a second line (it starts with `R<k>`), which no budget reserves',
    'in an attempt that starts with 32 lines on the page, the most with which one starts, it is dropped', 'the `R<k>` line ends before its `STS=` field',
    'That is a visibly cut line: stop', 'every result is one line, and `GFSR` is 0 before and after every attempt, so that a report is one line',
    'With a non-zero `GFSR` already on the `SMMU0` line (not observed)',
    'S0b, the direct topology that A1 uses, read both controllers idle with nothing connected while the stick was in')


# Round 5 (the last delta review, N-A to N-F): where the stick was, what SMMU=CHANGED compares, FSR=0X400, the diagram, the marker, the stops and the doorbell count.
ROUND5_PHRASES = (
    # N-A: the idle reading does not show where the stick was; the path of a direct boot makes usb_1_ss0 likely (STICK-LOG 13, last paragraph)
    'That reading does not show where the stick was (the link was down at EL2, cause UNKNOWN)',
    'the device path of boot 3, a direct boot, carried S0\'s PCI node `Pci(0x80,0x0)`, which makes `usb_1_ss0` likely (INFERRED: section 13, last paragraph)',
    'so, if the stick is in the same port as in boot 3, `XHCI0.BIN` is the file that resets its controller',
    # N-B: what SMMU=CHANGED and SMMU=SAME compare
    'with or without a change of the SMMU\'s fault registers and the DWC3\'s bus-error address and valid bit, goes on to the next arena',
    '`SMMU` compares the SMMU\'s fault registers and the DWC3\'s bus-error address and valid bit with those at the end of the attempt before',
    'the SMMU\'s fault registers and the DWC3\'s bus-error address and valid bit changed since the attempt before',
    'No event arrived and the SMMU\'s fault registers and the DWC3\'s bus-error address and valid bit did not move since the attempt before',
    # N-C: the values of context bank 3 are observed, their meaning is not
    'are `FSR=0X400` and the address and syndrome above; their meaning is UNKNOWN',
    # N-D: SAME or UNREAD does not exclude SC2
    'SC2 not excluded: the stage and WHY decide (O3 to O8)',
    # N-E: the marker is written before the step
    'A mismatch or no `VERIFY=OK`: power off; nothing of A1 has been written (the marker has; delete it to run again).',
    # N-F: the stop on a WHY=, the doorbell count of a failure at RUN, a restart that was reported
    '`STORE_REFUSED` on the END line, a `WHY=` of `READ`, `CLOCK`, `BUDGET` or `LINES`, `LINES_CUT` on the END line after any verdict except `NOOP_OK`',
    '14 more for each attempt that rang the doorbell, 13 for a failure at RUN before the doorbell, whatever its kind',
    'a restart was reported after two of three `cpuon` runs, with no xHC in the story, and after none of the two read-only controls',
    'a restart was reported after two of three `cpuon` runs and after none of the read-only controls')
# Wording that the last review withdrew or corrected: it must not come back.
WITHDRAWN_PHRASES = (
    'may be on neither', 'left by firmware', 'power off; nothing has been written', '13 for one that did not start',
    'and the two read-only controls did not', 'and the read-only controls did not', 'a change of the SMMU\'s registers')


def round5_problems(text):
    body = flat(text)
    return ([f'the text lacks {phrase!r}' for phrase in ROUND5_PHRASES if phrase not in body] +
            [f'withdrawn wording is back: {phrase!r}' for phrase in WITHDRAWN_PHRASES if phrase in body])


def round3_problems(text):
    body = flat(text)
    return [f'the text lacks {phrase!r}' for phrase in ROUND3_PHRASES if phrase not in body]


def label_problems(text):
    """The label table before section 1 names the six labels the brief allows (the first row of EXPERIMENTAL / UNIMPLEMENTED holds two)."""
    head = text.split('## 1.')[0]
    rows = [line for line in head.splitlines() if line.startswith('| ') and not line.startswith('| Label') and not line.startswith('| ---')]
    names = {cell for row in rows for cell in re.split(r' / ', row.split('|')[1].strip())}
    return [f'the label table lacks {label}' for label in ('VERIFIED', 'OBSERVED', 'INFERRED', 'EXPERIMENTAL', 'UNIMPLEMENTED', 'UNKNOWN') if label not in names]


def table_shape_problems(text):
    """Every row of every pipe table has as many cells as its header (a stray pipe in a cell splits it)."""
    problems, block = [], []
    for number, line in enumerate(text.splitlines() + [''], 1):
        if line.startswith('|'):
            block.append((number, line))
            continue
        problems += [f'line {n}: {row.count("|") - 1} cells against {block[0][1].count("|") - 1}' for n, row in block if row.count('|') != block[0][1].count('|')]
        block = []
    return problems


def hygiene_problems(text):
    problems = []
    if not text.isascii():
        problems.append('not ASCII')
    if len(text.encode()) >= 50 * 1024 or len(text.splitlines()) >= 1500:
        problems.append('over the size limits')
    found = FORBIDDEN.search(text)
    if found:
        problems.append(f'machine-identifying or path-like text: {found.group(0)!r}')
    return problems


HEX_RUN = re.compile(r'(?<![0-9A-Za-z_])[0-9a-f]{8,64}(?![0-9A-Za-z_])')
COUNT = re.compile(r'\b\d[\d,.]*\s*(?:lines?|bytes?|KiB|KB)\b', re.I)


def circularity_problems(text):
    """The record names the SHA-256 of this document, so a document the record pins cannot name the record's digest, its size or its line count: naming
    them would change the digest they name, and an equality test of the two could never hold. Pattern checks instead: a run of 8 to 64 lower-case hex digits
    with a digit and a letter in it (a digest or its abbreviation; an address after `0x` is not one), and a number of lines or bytes in a sentence that
    speaks of the record (code spans and file names are not counted)."""
    problems = [f'a digest or an abbreviation of one: {run.group(0)!r}' for run in HEX_RUN.finditer(text)
                if any(c.isdigit() for c in run.group(0)) and any(c in 'abcdef' for c in run.group(0))]
    chunks = []
    for block in re.split(r'\n\s*\n', re.sub(r'`[^`]*`', ' ', text)):
        for cell in block.split('|'):
            chunks += re.split(r'(?<=[.;])\s+', flat(cell))
    for sentence in chunks:
        if re.search(r'\brecord\b', sentence, re.I) and COUNT.search(sentence):
            problems.append(f'a size or a line count in a sentence about the record: {sentence[:80]!r}')
    return problems


def record_reference_problems(text, record_text):
    """The record cites section 7 of this document for what is not authorised and section 4 for the operator's actions: those headings must be those."""
    problems = []
    for phrase, title in (('docs/SP11-XHCI-S1.md section 7', '7. What the live link needs beyond A1'), ('docs/SP11-XHCI-S1.md has them in order', '4. The runbook')):
        if phrase not in flat(record_text):
            problems.append(f'the record no longer says {phrase!r}')
        if title not in [h for _, h in headings(text)]:
            problems.append(f'this document has no heading {title!r}')
    return problems


class Document(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DOC.read_text(encoding='utf-8')

    def test_the_sections_the_record_cites_exist_in_order(self):
        self.assertEqual(structure_problems(self.text), [])
        self.assertTrue(structure_problems(self.text.replace('## 7. What the live link needs beyond A1', '## 7. Something else')))
        self.assertTrue(structure_problems(self.text.replace('### 4.4 Stop rules', '')))
        swapped = self.text.replace('## 8. The Mac-side logger', '## 6x').replace('## 6. The outcome tree', '## 8. The Mac-side logger').replace('## 6x', '## 6. The outcome tree')
        self.assertTrue(structure_problems(swapped))

    def test_the_record_cites_what_this_document_has(self):
        record = rec.render([rec.PayloadFile(1, 100, 'a' * 64)], rec.RunbookFile(50000, 'b' * 64))
        self.assertEqual(record_reference_problems(self.text, record), [])
        self.assertTrue(record_reference_problems(self.text.replace('## 4. The runbook', '## 4. Procedure'), record))
        self.assertTrue(record_reference_problems(self.text, record.replace('section 7', 'section 9')))

    def test_links_and_anchors_resolve(self):
        self.assertEqual(link_problems(self.text), [])
        self.assertTrue(link_problems(self.text + '\n[x](SP11-NO-SUCH-FILE.md)\n'))
        self.assertTrue(link_problems(self.text + '\n[x](SP11-STICK-LOG.md#no-such-heading)\n'))
        self.assertTrue(link_problems(self.text + '\n[x](#no-such-heading)\n'))

    def test_the_register_table_is_the_records(self):
        self.assertEqual(register_problems(self.text), [])
        self.assertTrue(register_problems(self.text.replace('| CONFIG | op | 0x38 |', '| CONFIG | op | 0x3c |')))
        self.assertTrue(register_problems(self.text.replace('| DB0 | db | 0x00 |', '| DB1 | db | 0x00 |')))
        self.assertTrue(register_problems(self.text.replace('| ERSTSZ | ir0 | 0x08 |', '')))
        self.assertTrue(register_problems(self.text.replace('| USBCMD | op | 0x00 |', '| USBCMD | op | 0x00 |\n| USBSTS | op | 0x04 |')))

    def test_the_write_counts_follow_from_the_register_groups(self):
        self.assertEqual(count_problems(self.text), [])
        self.assertTrue(count_problems(self.text.replace('11 is through PROGRAM', '12 is through PROGRAM')))
        self.assertTrue(count_problems(self.text.replace('14 is the doorbell rung', '15 is the doorbell rung')))
        self.assertTrue(count_problems(self.text.replace('`WRITES=13`', '`WRITES=12`')))

    def test_every_outcome_has_a_meaning_and_a_next_step(self):
        self.assertEqual(outcome_problems(self.text), [])
        self.assertTrue(outcome_problems(self.text.replace('| O7 |', '| O8 |')))
        self.assertTrue(outcome_problems(re.sub(r'^\| O9 \|.*$', '| O9 | a page | | |', self.text, flags=re.M)))
        self.assertTrue(outcome_problems(re.sub(r'^\| O15 \|.*$', '', self.text, flags=re.M)))

    def test_every_name_the_payload_can_print_is_in_the_tree(self):
        self.assertEqual(coverage_problems(self.text), [])
        for kind, name in (('verdict', 'NOOP_BAD_COMPLETION'), ('reason', 'NOT_HELD'), ('reason', 'LINES'), ('stage', 'PROGRAM'), ('arena', 'SMALL_STACK'),
                           ('refusal', 'SMMU_UNREADABLE'), ('refusal', 'DWC3_ID'), ('flag', 'STORE_REFUSED'), ('flag', 'LINES_CUT')):
            with self.subTest(removed=name):
                mutated = re.sub(rf'(?<![A-Z_]){name}(?![A-Z_])', 'REMOVED', self.text)
                self.assertTrue(coverage_problems(mutated), name)
        self.assertTrue(coverage_problems(self.text, verdicts=VERDICTS + ('NOOP_NEW',)), 'a name the payload gained and the tree lacks')

    def test_the_live_link_section_names_every_register_class(self):
        self.assertEqual(live_link_problems(self.text), [])
        for name in LIVE_LINK_CLASSES:
            with self.subTest(removed=name):
                body = section(self.text, '7. What the live link needs beyond A1')
                mutated = self.text.replace(body, body.replace(name, 'X' * len(name)))
                self.assertTrue(live_link_problems(mutated), name)

    def test_the_numbers_and_the_two_open_questions_are_stated(self):
        self.assertEqual(number_problems(self.text), [])
        for phrase in ('A nominal run makes 17 stores', 'NOT granted', 'five waits of 8 s'):
            self.assertTrue(number_problems(self.text.replace(phrase, 'XXXX')), phrase)

    def test_the_corrections_of_the_delta_review_are_in_the_text(self):
        self.assertEqual(round3_problems(self.text), [])
        for phrase in ROUND3_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertTrue(round3_problems(flat(self.text).replace(phrase, 'X' * len(phrase))), phrase)

    def test_the_rows_of_the_last_review_are_in_the_text_and_the_withdrawn_wording_is_not(self):
        self.assertEqual(round5_problems(self.text), [])
        body = flat(self.text)
        for phrase in ROUND5_PHRASES:
            with self.subTest(removed=phrase):
                self.assertTrue(round5_problems(body.replace(phrase, 'X' * len(phrase))), phrase)
        for phrase in WITHDRAWN_PHRASES:
            with self.subTest(back=phrase):
                self.assertTrue(round5_problems(body + ' ' + phrase), phrase)

    def test_reverting_the_sentence_about_the_sticks_controller_or_the_marker_clause_is_caught(self):
        body = flat(self.text)
        start = body.index('That reading does not show where the stick was')
        end = body.index('is the file that resets its controller') + len('is the file that resets its controller')
        old = 'and does not print which controller the stick was on, so it may be on neither (INFERRED)'
        self.assertTrue(round5_problems(body.replace(body[start:end], old)), 'the withdrawn claim and the missing sentence')
        self.assertTrue(round5_problems(body.replace('`XHCI0.BIN` is the file that resets its controller', '`XHCI1.BIN` is the file that resets its controller')))
        self.assertTrue(round5_problems(body.replace('which makes `usb_1_ss0` likely', 'which makes `usb_1_ss1` likely')))
        clause = ' (the marker has; delete it to run again)'
        self.assertIn('nothing of A1 has been written' + clause, body)
        self.assertTrue(round5_problems(body.replace(clause, '')), 'the clause removed')
        self.assertTrue(round5_problems(body.replace('nothing of A1 has been written' + clause, 'nothing has been written')), 'the old wording back')
        self.assertTrue(round5_problems(body.replace('SC2 not excluded: the stage and WHY decide', 'The stage and WHY decide')), 'the diagram sends SAME away from SC2 again')

    def test_the_runbook_names_no_digest_size_or_line_count_of_the_record(self):
        self.assertEqual(circularity_problems(self.text), [])
        digest = hashlib.sha256(b'a record text').hexdigest()
        for label, addition in (('a full digest', f'The record names the digest {digest}.'), ('an abbreviated digest', f'The record digest starts with {digest[:8]}.'),
                                ('a digest after a label', f'sha256: {digest}'), ('a line count', 'The record has 198 lines.'),
                                ('a line count after a colon', 'The record: 198 lines.'), ('a size in bytes', 'The record text is 18,367 bytes long.'),
                                ('a size in KiB', 'The size of the record is about 18 KiB.')):
            with self.subTest(case=label):
                self.assertTrue(circularity_problems(self.text + '\n\n' + addition), label)
        for label, addition in (('lines of the page', 'The page has 40 lines.'), ('the record named', 'The record names the SHA-256 of this file.'),
                                ('an address', 'The window is at 0x0a800000.'), ('the runner digits', 'Compare the 16 digits with the first 16 of the record digest.')):
            with self.subTest(case=label):
                self.assertEqual(circularity_problems(self.text + '\n\n' + addition), [], label)

    def test_the_text_is_ascii_small_and_names_no_machine(self):
        self.assertEqual(hygiene_problems(self.text), [])
        for label, bad in (('non-ASCII', '\u00e9'), ('a home path', '/Us' + 'ers/x'), ('an IP address', '192.168.1.20'), ('a MAC address', 'aa:bb:cc:dd:ee:ff'),
                           ('a volume', '/Vol' + 'umes/X'), ('too long', '\n' * 1500)):
            with self.subTest(case=label):
                self.assertTrue(hygiene_problems(self.text + bad))

    def test_every_table_row_has_the_cells_of_its_header(self):
        self.assertEqual(table_shape_problems(self.text), [])
        self.assertTrue(table_shape_problems(self.text.replace('| ERSTSZ | ir0 | 0x08 |', '| ERSTSZ | ir0 | 0x08 | extra |')))
        self.assertTrue(table_shape_problems(self.text.replace('PROGRAM (1) |', 'PROGRAM | (1) |')))

    def test_the_labels_are_the_documents(self):
        self.assertEqual(label_problems(self.text), [])
        for label in ('VERIFIED', 'OBSERVED', 'INFERRED', 'UNIMPLEMENTED', 'UNKNOWN'):
            with self.subTest(removed=label):
                self.assertTrue(label_problems(self.text.replace(f'| {label} |', '| X |').replace(f'/ {label} |', '/ X |')))


@unittest.skipUnless(TREE and (Path(TREE) / 'payloads' / 'xhci-regs.h').is_file() and (Path(TREE) / 'payloads' / 'xhci.c').is_file(),
                     'Q1N1_SP11_TREE does not name a q1n1 tree with payloads/xhci-regs.h and xhci.c: the names are not compared with the payload')
class AgainstThePayload(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.header = (Path(TREE) / 'payloads' / 'xhci-regs.h').read_text(encoding='utf-8')
        cls.source = (Path(TREE) / 'payloads' / 'xhci.c').read_text(encoding='utf-8')

    def table(self, name):
        body = re.search(rf'{name}\[[^\]]*\](?:\[[^\]]*\])? = \{{(.*?)\}};', self.header, re.S).group(1)
        return tuple(re.findall(r'"([A-Z0-9_]*)"', body))

    def test_the_pinned_names_are_the_payloads(self):
        self.assertEqual(tuple(n for n in self.table('verdict_name') if n not in ('NONE',)), VERDICTS)
        self.assertEqual(tuple(n for n in self.table('why_name') if n), REASONS)
        self.assertEqual(tuple(n for n in self.table('stage_name') if n != 'HALT'), STAGES, 'STG_HALT is defined and never set, so the page never prints it')
        self.assertEqual(tuple(n for n in self.table('arena_why') if n not in ('OK', 'N_A')), ARENA_REASONS)

    def test_every_pinned_refusal_is_still_a_literal_of_the_payload(self):
        for name in REFUSALS:
            self.assertIn(f'"{name}"', self.source + (Path(TREE) / 'payloads' / 'xhci-ctl.c').read_text(), name)
        for flag in END_FLAGS:
            self.assertTrue(flag.split('=')[0] in self.source or flag in self.source, flag)

    def test_the_timing_of_the_page_reading_is_the_payloads(self):
        watch = int(re.search(r'#define WATCH_S (\d+)u', self.header).group(1))
        wait = int(re.search(r'#define WAIT_BEFORE_WRITE_S (\d+)u', self.header).group(1))
        self.assertEqual((watch, wait, watch + 5 * wait + 1), (30, 8, 71))

    def test_the_payload_documentation_the_record_cites_exists(self):
        self.assertTrue((Path(TREE) / 'docs' / 'SP11-PAYLOAD-XHCI.md').is_file())

    def test_the_q1n1_document_names_no_digest_size_or_line_count_of_the_record_either(self):
        self.assertEqual(circularity_problems((Path(TREE) / 'docs' / 'SP11-PAYLOAD-XHCI.md').read_text(encoding='utf-8')), [])

    def test_the_numbers_of_the_line_budget_are_the_host_models(self):
        cases = (Path(TREE) / 'tools' / 'test-q1n1-sp11-payload-xhci-cases.c').read_text(encoding='utf-8')
        text = DOC.read_text(encoding='utf-8')
        self.assertEqual(budget_problems(text, cases), [])
        self.assertTrue(budget_problems(text, cases.replace('{3, 2, 1, 0, 0, 0}', '{3, 2, 0, 0, 0, 0}')))
        self.assertTrue(budget_problems(text, cases.replace('>= 5)', '>= 6)')))
        self.assertTrue(budget_problems(text.replace('3, 2, 1 or 0 of the four', '3, 2 or 1 of the four'), cases))


if __name__ == '__main__':
    sys.exit(0 if unittest.main(verbosity=1, exit=False).result.wasSuccessful() else 1)
