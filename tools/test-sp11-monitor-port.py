#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Keeps docs/SP11-MONITOR-PORT.md honest: its citations, quotations, labels, size, structure and reported state.

Run from the repository root:  python3 -B tools/test-sp11-monitor-port.py

Conventions the document follows and this test enforces:

  citation   `path:line` or `path:first-last` in backticks; the path is relative to the repository root, or starts with
             `q1n1:` (the sibling q1n1 checkout). The file must exist and the lines lie inside it.
  quotation  `fragment`[path:line]: a backticked fragment of source text directly followed by a bracketed citation; it
             must occur on the cited lines (a table-cell `\\|` reads as `|`), so a moved line fails here.
  bare path  a backticked path with a directory part and an extension, no line: the file must exist.
  labels     VERIFIED, OBSERVED, INFERRED, EXPERIMENTAL, UNIMPLEMENTED, UNKNOWN. A table with a `Label` column holds only
             these (parenthesised notes aside); an OBSERVED row names its source (a boot, the restart probe or control, an
             operator report, a test id, a dump). Look-alike labels (ASSUMED, CONFIRMED, ...) occur nowhere.
  tables     every block of `|` lines has a header, an exact `| --- |` separator and rows of the header's width.
  claims     statements that were wrong once and must not come back: a restart time or UPTIME budget read off boot 7's
             `UPTIME 16 S` (the first heartbeat after the cpuon chain), an UPTIME window derived from the DONE marker gap
             (it holds the operator's power cycle), a plan that "runs unattended" across a self-restart, the read-only
             runner flow "observed only to UPTIME 10 S" (the restart control: alive at 224 S and 268 S), an adapter that
             "accepts" the unit's tables. The restart numbers, the survey (WI 10, before SMP work only), the secondaries'
             EL2 entry state and the unit's DFR0 and ISAR0 values are pinned.
  state      what the document says about the tree as it is, held against its source: the timer guards' status values
             (compat/sp11-timer-guard.h), the five bundle files in the interface's order and sizes, the work items'
             statuses (built is not run), the low placement limit (288 MiB fits, 320 does not), the unit's MADT and MCFG
             and the adapter's behaviour on them (GICD and redistributors resolved; the ECAM rule, D11, refuses).

Always run: size, ASCII, hygiene, sections, "NOT RUN", labels, tables, claims, state, the citations into this repository, the
work-plan graph and the arithmetic the document quotes. Skipped, visibly (a skip is never a pass): the q1n1 citations when no
checkout is found (set Q1N1_SP11_TREE to the one the document names, commit 1e7318f) and the facts read from the local,
git-ignored unit dumps under downloads/ when they are absent (the ACPI figures and the adapter run on them).

The last class feeds deliberately damaged copies of the document to the same checkers; each must be refused.
"""
import os
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / 'docs' / 'SP11-MONITOR-PORT.md'
LABELS = ('VERIFIED', 'OBSERVED', 'INFERRED', 'EXPERIMENTAL', 'UNIMPLEMENTED', 'UNKNOWN')
LOOKALIKES = ('ASSUMED', 'CONFIRMED', 'MEASURED', 'PROVEN', 'PROVED', 'TESTED', 'VALIDATED', 'ESTABLISHED', 'HYPOTHESIS',
              'GUESSED', 'LIKELY', 'SUSPECTED', 'UNVERIFIED', 'PRESUMED', 'PROBABLE', 'CERTAIN', 'DEMONSTRATED',
              'QUALIFIED', 'ASSERTED', 'CLAIMED', 'OBVIOUS', 'TRIVIAL')
MAX_BYTES = 45000
SECTIONS = ((1, 'reading'), (2, 'milestone m1'), (3, 'entry'), (4, 'delivery'), (5, 'memory'), (6, 'timer'),
            (7, 'interrupts'), (8, 'id registers'), (9, 'platform contract'), (10, 'console'), (11, 'thermal'),
            (12, 'safety'), (13, 'ordered work plan'), (14, 'first hardware run'),
            (15, 'decisions i need from the operator'), (16, 'what i could not establish'))
LOCAL_ONLY = ('downloads/', 'build/')
PATH = r'((?:q1n1:)?[A-Za-z0-9_+-]+(?:/[A-Za-z0-9_.+-]+)+\.[A-Za-z0-9]+)'
CITE = re.compile(r'`' + PATH + r':(\d+)(?:-(\d+))?`')
QUOTE = re.compile(r'`([^`\n]+)`\[' + PATH + r':(\d+)(?:-(\d+))?\]')
BARE = re.compile(r'`' + PATH + r'`(?!\[)')
SOURCE_OF_OBSERVED = re.compile(r'\bboots? \d|restart (?:probe|control)|X1 probe|\bsteps? [123]\b|operator report|\bT0\d|\bX2\b'
                                r'|\bdump\b|\bboot-\d', re.I)
SEPARATOR = re.compile(r'^\|(\s*:?-{3,}:?\s*\|)+\s*$')
# Statements that were wrong once. UPTIME starts before the runner, so the cpuon chain is inside it: boot 7's `UPTIME 16 S`
# was the first heartbeat after the chain, not the restart time. The DONE2 to DONE3 gap holds the operator's power cycle,
# and after a self-restart the firmware boots its own order, so no plan runs unattended. The restart control (2026-11-16)
# kept the read-only runner flow alive to UPTIME 224 S and 268 S, not only to UPTIME 10 S.
REFUTED = (
    (r'(?:under|below|within|inside|at most|less than)\s+(?:that\s+|the\s+|about\s+)?(?:16|30)\s*s\b',
     'a 16 s or 30 s UPTIME budget has no basis'),
    (r'\b(?:16|30) s (?:bound|budget|limit)\b', 'a 16 s or 30 s UPTIME budget has no basis'),
    (r'least uptime at which a restart', 'the UPTIME 16 S photograph is not a restart time'),
    (r'seconds after (?:its|the) chain', 'the restart came after boot 7\'s last photograph at some unrecorded time'),
    (r'\b40 to 60 ?s\b', 'the DONE2 to DONE3 gap holds the operator\'s power cycle: no UPTIME window follows from it'),
    (r'unattended plan|one unattended|writes? the next marker|restarts? produces? the next marker',
     'after a self-restart the firmware does not return to the stick'),
    (r'(?:unobserved|not observed|observed only|only observed|restarts?)\b[^.|]{0,60}\bUPTIME 10 S',
     'the restart control observed the read-only runner flow alive at UPTIME 224 S and 268 S, not only to UPTIME 10 S'),
    (r'U5 DFR0|values not in my sources|(?:DFR0|ISAR0)[^.|]{0,40}T01 page only|ISAR0 \(RNDR\), FGT and AMU values',
     'DFR0, ISAR0, FGT and AMU are known since the X1 probe page; only the OS lock, MDCR_EL2, ICC_SRE_EL2, DFR1 and ISAR2 are not'),
    (r'from_hamoa`? (?:accepts|passes|is no longer blocked|is unblocked)',
     'the adapter still refuses the unit\'s tables at the ECAM rule (D11); only its GICD and redistributor rules are resolved'),
)
# The unit's ID registers (X1 probe page, 2026-11-11, one photograph, passive reads at EL2) are facts of the coordinator's
# fact file, pinned here: the document's hex values and decoded fields must agree, and the monitor's gates are held against them.
DFR0, ISAR0, ISAR1 = 0x000F01F010305719, 0x1221111110212120, 0x0110111101211052
FIELDS = {'DebugVer': (DFR0, 0), 'BRPs': (DFR0, 12), 'WRPs': (DFR0, 20), 'RNDR': (ISAR0, 60), 'APA': (ISAR1, 4),
          'GPA': (ISAR1, 24)}
STATED = ('DebugVer', 'BRPs', 'RNDR', 'APA')      # fields the document must state; the others are checked when it does
COUNT_WORDS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8}
# Phrases the restart section and the survey must keep (matched on whitespace-collapsed text).
REQUIRED = ('first heartbeat after the chain', 'not at one fixed UPTIME', 'UPTIME 123 S', 'UPTIME 36 S',
            'UPTIME 224 S and 268 S', '2 of 3 cpuon boots', 'creationdate', 'Volume-Down + Power', 'no plan runs unattended',
            'M1 starts no secondary (x4 = 0)', 'INFERRED, strongly', 'correlation over five boots', 'about 11 %',
            'WI 10 is off the path')
SECONDARY_STATE = ('0X200', '0X300', '0X10000', '0X10100', '0X10300', '0X20000', '0X20100', '0x80000000', '0X100',
                   '0X10200', '0x1488000038', 'survived its own CPU_OFF and CPU_ON', '0X20200 and 0X20300 UNKNOWN',
                   'must not assume VBAR_EL2 or HCR_EL2 are reset')
GUARD_HEADER, BUNDLE_DOC = 'compat/sp11-timer-guard.h', 'docs/SP11-STICK-BUNDLE.md'
BUNDLE_FILES = ('SNAPMON.BIN', 'SNAPREP.BIN', 'GUEST.DAT', 'PIN.DAT', 'REPORT.DAT')
BUNDLE_SIZES = (('`PIN.DAT` (1024 bytes', 1024), ('`REPORT.DAT` (528,384 zero bytes', 528384))
# Built is not run: what each work item may claim (its row must keep these words).
WORK_ITEM_STATUS = {1: ('DONE, EXPERIMENTAL (b8e8f81',),
                    2: ('BUILT, NOT RUN (q1n1 d8a5d2b)', 'the first is staged, ready to run, not run (`memhash`'),
                    3: ('BUILT for the synthetic path', '`real` NOT RUN'), 4: ('IN PROGRESS elsewhere, not audited here',),
                    5: ('BUILT, EXPERIMENTAL, host side',), 9: ('BUILT, NOT RUN (q1n1 1e7318f)', 'first code below EL2')}
# The tree as the document states it (matched on whitespace-collapsed text); the sources that decide them are read below.
STATE_REQUIRED = (
    'is five files, the runner\'s whole manifest', 'bits that stick and trap nothing pass', 'a pass is UNKNOWN until T-HW2',
    'a 288 MiB guest passes `place-check` on all six local maps and 320 MiB is refused in all six',
    '12 GICC entries of 80 bytes', 'one GICD structure of 25 bytes (ACPI: 24; the extra byte is 0)',
    'then the ITS structure, no GICR-range structure', 'MCFG: eight allocations, segments 0 to 7', 'segment 2 at 0x6000000000',
    'RESOLVED in e3844fa', 'a1fb91c (redistributors from the per-CPU GICC bases',
    'then `check_hamoa_platform` refuses at `ecam-mismatch`', 'a descriptor holds at most four ECAM entries',
    'the adapter demands set equality',
    'the MCFG lists eight allocations, a descriptor holds four, the adapter demands equality', 'an eight-entry descriptor is refused')


def q1n1_tree():
    candidates = [Path(os.environ['Q1N1_SP11_TREE'])] if os.environ.get('Q1N1_SP11_TREE') else []
    for base in (ROOT.parent, ROOT.parent.parent, ROOT.parent.parent.parent):
        candidates.append(base / 'q1n1')
        candidates.extend(sorted(base.glob('q1n1-worktrees/*sp11*')))
    for tree in candidates:
        if (tree / 'platform/uefi/main.c').is_file() and (tree / 'docs/SP11-PAYLOAD.md').is_file():
            return tree
    return None


def resolve(rel, tree):
    """(path, skip_reason): the file a citation names, or why it cannot be checked here."""
    if rel.startswith('q1n1:'):
        return (tree / rel[5:], None) if tree else (None, 'no q1n1 checkout')
    path = ROOT / rel
    if not path.exists() and rel.startswith(LOCAL_ONLY):
        return None, 'local-only file absent'
    return path, None


def lines_of(path):
    return path.read_text(errors='replace').splitlines()


def check_citations(text, tree):
    """(problems, checked): every citation, quotation and bare path in `text`, held against the files."""
    problems, checked = [], 0
    for match in CITE.finditer(text):
        rel, first, last = match.group(1), int(match.group(2)), int(match.group(3) or match.group(2))
        path, skip = resolve(rel, tree)
        if skip:
            continue
        checked += 1
        if not path.is_file():
            problems.append(f'no such file: {rel}')
        elif not 1 <= first <= last <= len(lines_of(path)):
            problems.append(f'line out of range: {rel}:{first}-{last} (file has {len(lines_of(path))} lines)')
    for match in QUOTE.finditer(text):
        needle, rel = match.group(1).replace('\\|', '|'), match.group(2)
        first, last = int(match.group(3)), int(match.group(4) or match.group(3))
        path, skip = resolve(rel, tree)
        if skip:
            continue
        checked += 1
        if not path.is_file():
            problems.append(f'no such file: {rel}')
        elif not 1 <= first <= last <= len(lines_of(path)):
            problems.append(f'line out of range: {rel}:{first}-{last}')
        elif needle not in '\n'.join(lines_of(path)[first - 1:last]):
            problems.append(f'needle not found: `{needle}` at {rel}:{first}-{last}')
    for match in BARE.finditer(text):
        rel = match.group(1)
        path, skip = resolve(rel, tree)
        if skip:
            continue
        checked += 1
        if not path.is_file():
            problems.append(f'no such file: {rel}')
    return problems, checked


def table_rows(text):
    """Yield (header cells, data row cells, raw row) for every table row, tables found by their `| --- |` rule."""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].startswith('|') and i + 1 < len(lines) and re.match(r'^\|(\s*:?-{3,}:?\s*\|)+\s*$', lines[i + 1]):
            header = split_row(lines[i])
            j = i + 2
            while j < len(lines) and lines[j].startswith('|'):
                yield header, split_row(lines[j]), lines[j]
                j += 1
            i = j
        else:
            i += 1


def split_row(row):
    return [c.strip() for c in re.split(r'(?<!\\)\|', row.strip())[1:-1]]


def check_labels(text):
    problems = []
    for word in LOOKALIKES:
        if re.search(r'\b' + word + r'\b', text):
            problems.append(f'look-alike label: {word}')
    for header, row, raw in table_rows(text):
        if 'Label' not in header:
            continue
        index = header.index('Label')
        if len(row) != len(header):
            problems.append(f'row has {len(row)} cells, header {len(header)}: {raw[:60]}')
            continue
        cell = re.sub(r'\([^)]*\)', '', row[index])
        tokens = [t.strip() for t in re.split(r'/|,|;|\band\b', cell) if t.strip()]
        if not tokens:
            problems.append(f'empty label: {raw[:60]}')
        for token in tokens:
            if token not in LABELS:
                problems.append(f'unknown label {token!r}: {raw[:60]}')
        if 'OBSERVED' in tokens and not SOURCE_OF_OBSERVED.search(raw):
            problems.append(f'OBSERVED row without a source: {raw[:60]}')
    return problems


def check_structure(text):
    problems = []
    data = text.encode()
    if len(data) >= MAX_BYTES:
        problems.append(f'{len(data)} bytes, limit {MAX_BYTES}')
    if not text.isascii():
        problems.append('not ASCII')
    if not text.startswith('<!-- SPDX-License-Identifier: MIT -->\n'):
        problems.append('first line is not the MIT SPDX line')
    for prefix in ('/' + 'Users' + '/', '/' + 'home' + '/'):
        if prefix in text:
            problems.append('machine path in the document')
    if 'Hardware status for the port itself: NOT RUN' not in text[:900]:
        problems.append('the port is not stated NOT RUN at the top')
    headings = re.findall(r'^## (\d+)\. (.+)$', text, re.M)
    numbers = [int(n) for n, _ in headings]
    if numbers != [n for n, _ in SECTIONS]:
        problems.append(f'section numbers are {numbers}')
    else:
        for (n, keyword), (_, title) in zip(SECTIONS, headings):
            if keyword not in title.lower():
                problems.append(f'section {n} is {title!r}, expected {keyword!r}')
    return problems


def plan_rows(text):
    rows = {}
    for header, row, raw in table_rows(text):
        if header[:2] == ['WI', 'Work'] and len(row) == len(header):
            rows[int(row[0])] = dict(zip(header, row))
    return rows


def check_plan(text):
    problems = []
    rows = plan_rows(text)
    if sorted(rows) != list(range(1, len(rows) + 1)) or len(rows) < 8:
        problems.append(f'work items are {sorted(rows)}')
    decisions = set(re.findall(r'^- (D\d+) ', text.split('## 15.')[-1].split('## 16.')[0], re.M))
    unknown_defs = set(re.findall(r'\b(U\d+) ', text.split('Unknowns referenced:')[-1].split('| WI |')[0]))
    resolved, edges = set(), {}
    for number, row in rows.items():
        edges[number] = [int(d) for d in re.findall(r'\b\d+\b', row['Depends'])]
        for dep in edges[number]:
            if dep not in rows:
                problems.append(f'WI {number} depends on missing WI {dep}')
        for decision in re.findall(r'\bD\d+\b', row['Depends'] + ' ' + row['UNKNOWN / decision']):
            if decision not in decisions:
                problems.append(f'WI {number} names {decision}, which section 15 does not define')
        for unknown in re.findall(r'\bU\d+\b', row['UNKNOWN / decision']):
            resolved.add(unknown)
        if not row['Acceptance (can fail)'] or not row['Host-first evidence']:
            problems.append(f'WI {number} has no acceptance or no host-first evidence')
    state = {}

    def visit(node, path):
        if state.get(node) == 'done' or node not in rows:
            return
        if state.get(node) == 'open':
            problems.append(f'dependency cycle through WI {node}: {path}')
            return
        state[node] = 'open'
        for dep in edges[node]:
            visit(dep, path + [dep])
        state[node] = 'done'

    for number in rows:
        visit(number, [number])
    if unknown_defs - resolved:
        problems.append(f'unknowns with no work item: {sorted(unknown_defs - resolved)}')
    if len(decisions) < 8:
        problems.append(f'only {len(decisions)} decisions listed')
    return problems


def check_tables(text):
    """A table that loses a newline becomes prose and escapes the label checks: every block of `|` lines needs a header,
    an exact separator row and rows as wide as the header."""
    problems, lines, i = [], text.splitlines(), 0
    while i < len(lines):
        if not lines[i].startswith('|'):
            i += 1
            continue
        j = i
        while j < len(lines) and lines[j].startswith('|'):
            j += 1
        block = lines[i:j]
        if len(block) < 3 or not SEPARATOR.match(block[1]):
            problems.append(f'table without a separator row at line {i + 1}: {block[0][:50]}')
        else:
            width = len(split_row(block[0]))
            problems += [f'line {i + 1 + k} has {len(split_row(row))} cells, the header {width}'
                         for k, row in enumerate(block) if len(split_row(row)) != width]
        i = j
    return problems


def squash(text):
    return ' '.join(text.split())


def section(text, number):
    found = re.search(rf'^## {number}\. .*?(?=^## {number + 1}\. |\Z)', text, re.M | re.S)
    return found.group(0) if found else ''


def check_claims(text):
    """The restart section and the survey: refuted statements stay out, the numbers and the order stay in."""
    flat, problems = squash(text), []
    for pattern, why in REFUTED:
        if re.search(pattern, flat, re.I):
            problems.append(f'refuted ({why}): /{pattern}/')
    for sentence in re.split(r'(?<=\.)\s+', flat):
        if re.search(r'\b16 ?s\b', sentence, re.I) and 'first heartbeat' not in sentence:
            problems.append(f'a 16 s figure that is not the first-heartbeat note: {sentence[:70]}')
    problems += [f'missing: {needle}' for needle in REQUIRED if needle not in flat]
    if len(re.findall(r'20 s of UPTIME', flat)) < 3:
        problems.append('the 20 s UPTIME budget must stand in sections 10, 14 and 15')
    if len(re.findall(r'precaution of low weight', flat)) < 3:
        problems.append('the 20 s UPTIME budget is a precaution of low weight in section 10, stop rule 4 and D5')
    if len(re.findall(r'UPTIME 17 S', flat)) < 2:
        problems.append('the earliest plausible restart, UPTIME about 17 S, must stand in sections 10 and 15')
    restart = squash(section(text, 10))
    if 'UPTIME 224 S and 268 S' not in restart:
        problems.append('section 10 must give the restart control: the read-only runner flow alive at UPTIME 224 S and 268 S')
    rows = plan_rows(text)
    row = rows.get(10, {})
    survey = (row.get('Work', '') + ' ' + row.get('Acceptance (can fail)', '')).strip()
    if not re.search(r'before SMP work.*M1 does not need.*operator in the loop.*DONE.*psci-only.*UPTIME 224 S and 268 S'
                     r'.*\(a\).*cpuon page until it restarts.*\(b\).*ONE core only.*\(c\).*cores running.*2 of 3',
                     survey, re.S):
        problems.append('work item 10 must say: before SMP work only (M1 does not need it), operator in the loop; the '
                        'psci-only control DONE at UPTIME 224 S and 268 S; left, in order: (a) film a cpuon page until '
                        'it restarts, (b) one core only, (c) cores left running; cpuon at 2 of 3')
    if re.search(r'\b10\b', rows.get(11, {}).get('Depends', '')):
        problems.append('the M1 run (WI 11) must not wait for the restart survey (WI 10): M1 starts no secondary')
    secondaries = squash(section(text, 7))
    problems += [f'section 7 lacks: {needle}' for needle in SECONDARY_STATE if needle not in secondaries]
    return problems


def check_id_registers(text):
    """The unit's ID registers as pinned above: the document's hex values and the fields it decodes must agree."""
    flat, problems = squash(text), []
    for name, value in (('ID_AA64DFR0_EL1', DFR0), ('ID_AA64ISAR0_EL1', ISAR0)):
        if f'{name} 0x{value:016X}' not in flat:
            problems.append(f'missing {name} 0x{value:016X}')
        problems += [f'{name} 0x{quoted} contradicts the pinned value' for quoted in
                     re.findall(name + r' 0x([0-9A-Fa-f]{16})', flat) if int(quoted, 16) != value]
    for field, (register, lsb) in FIELDS.items():
        claimed = re.findall(rf'\b{field} (\d+)(?!\s+to\s+\d)', flat)      # "DebugVer 6 to 11" is the monitor's range
        if not claimed and field in STATED:
            problems.append(f'the document states no {field}')
        problems += [f'{field} {n} contradicts the register ({(register >> lsb) & 15})'
                     for n in claimed if int(n) != (register >> lsb) & 15]
    breakpoints = ((DFR0 >> 12) & 15) + 1
    problems += [f'{word} breakpoints contradicts BRPs {breakpoints - 1} ({breakpoints} breakpoints)'
                 for word, number in COUNT_WORDS.items() if f'{word} breakpoints' in flat and number != breakpoints]
    return problems


def check_port_state(text):
    """The document's statements about the tree as it is, held against the sources that decide them."""
    flat = squash(text)
    problems = [f'state lacks: {needle}' for needle in STATE_REQUIRED if needle not in flat]
    header, interface = (ROOT / GUARD_HEADER).read_text(), (ROOT / BUNDLE_DOC).read_text()
    for define, shown in (('SNAP_SP11_TIMER_STATUS_CNTHCTL', '-147'), ('SNAP_SP11_TIMER_STATUS_CNTVOFF', '-148'),
                          ('SNAP_SP11_EVENT_TIMER', '0x5443')):
        if not re.search(rf'{define}\s+\(?{shown}\)?', header) or shown not in flat:
            problems.append(f'{GUARD_HEADER} and the document disagree on {define} = {shown}')
    names = tuple(re.findall(r'^\| \d \| `([A-Z0-9]+\.[A-Z]+)` \|', interface, re.M))
    if names != BUNDLE_FILES:
        problems.append(f'{BUNDLE_DOC} lists {names}, not the five files the document names')
    for needle, size in BUNDLE_SIZES:
        if f'exactly {size} bytes' not in interface or needle not in flat:
            problems.append(f'the interface and the document disagree on {needle}')
    start = flat.find('is five files, the runner\'s whole manifest')
    places = [flat.find(f'`{name}`', start) for name in BUNDLE_FILES]
    if start < 0 or -1 in places or places != sorted(places):
        problems.append('the bundle paragraph must name the five files in the interface\'s order')
    rows = plan_rows(text)
    for number, needles in WORK_ITEM_STATUS.items():
        row = squash(' '.join(rows.get(number, {}).values()))
        problems += [f'WI {number} lacks: {needle}' for needle in needles if needle not in row]
    if rows.get(11, {}).get('Depends') != '3, 5, 8, 9':
        problems.append('the M1 run (WI 11) depends on 3 (the real path), 5, 8 and 9')
    if 'D11' not in rows.get(3, {}).get('UNKNOWN / decision', '') or '- D11 The ECAM rule' not in text.split('## 15.')[-1]:
        problems.append('WI 3 is gated by D11 and section 15 keeps it: the ECAM rule')
    return problems


def load_adapter():
    """(board_descriptor, bootcontract, the descriptor tests' boot-ready fixture)."""
    import importlib.util
    import sys
    for sub in ('probes', 'tools'):
        if str(ROOT / sub) not in sys.path:
            sys.path.insert(0, str(ROOT / sub))
    import board_descriptor
    import bootcontract
    spec = importlib.util.spec_from_file_location('descriptor_fixture', ROOT / 'tools' / 'test-board-descriptor.py')
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    return board_descriptor, bootcontract, fixture


def check_all(text, tree):
    problems = (check_structure(text) + check_labels(text) + check_plan(text) + check_tables(text) + check_claims(text) +
                check_id_registers(text) + check_port_state(text))
    cite_problems, _ = check_citations(text, tree)
    return problems + cite_problems


TEXT = DOC.read_text()
TREE = q1n1_tree()


class Structure(unittest.TestCase):
    def test_size_hygiene_sections_and_not_run(self):
        self.assertEqual(check_structure(TEXT), [])

    def test_tables_are_well_formed(self):
        self.assertEqual(check_tables(TEXT), [])

    def test_the_test_file_follows_its_own_rules(self):
        me = Path(__file__)
        text = me.read_text()
        self.assertIn('SPDX-License-Identifier: MIT', '\n'.join(text.splitlines()[:2]))
        self.assertTrue(text.isascii())
        self.assertLess(me.stat().st_size, 50 * 1024)
        for prefix in ('/' + 'Users' + '/', '/' + 'home' + '/'):
            self.assertNotIn(prefix, text)

    def test_m1_has_its_seven_acceptance_rows_and_the_decisions_and_gaps_end_the_document(self):
        rows = [r for h, r, _ in table_rows(TEXT) if h[:2] == ['#', 'Acceptance (falsifiable)']]
        self.assertEqual([r[0] for r in rows], list('abcdefg'))
        tail = TEXT.split('## 15.')[-1]
        self.assertTrue(tail.startswith(' Decisions I need from the operator'))
        self.assertIn('## 16. What I could not establish', tail)


class Labels(unittest.TestCase):
    def test_label_tables_and_lookalikes(self):
        self.assertEqual(check_labels(TEXT), [])

    def test_every_label_is_used_somewhere(self):
        for label in ('VERIFIED', 'OBSERVED', 'INFERRED', 'UNKNOWN', 'UNIMPLEMENTED'):
            self.assertIn(label, TEXT)


class Citations(unittest.TestCase):
    def test_repository_citations_and_quotations(self):
        problems, checked = check_citations(TEXT, None)
        self.assertEqual(problems, [])
        self.assertGreater(checked, 80)
        self.assertGreater(len(QUOTE.findall(TEXT)), 40)

    def test_q1n1_citations(self):
        if TREE is None:
            self.skipTest('no q1n1 checkout found (set Q1N1_SP11_TREE): the q1n1 citations are not checked')
        problems, checked = check_citations(TEXT, TREE)
        self.assertEqual(problems, [])
        self.assertGreater(len([m for m in CITE.finditer(TEXT) if m.group(1).startswith('q1n1:')]) +
                           len([m for m in QUOTE.finditer(TEXT) if m.group(2).startswith('q1n1:')]), 3)


class Plan(unittest.TestCase):
    def test_work_items_dependencies_decisions_and_unknowns(self):
        self.assertEqual(check_plan(TEXT), [])


class Claims(unittest.TestCase):
    def test_the_restart_numbers_the_survey_and_the_secondary_entry_state(self):
        self.assertEqual(check_claims(TEXT), [])

    def test_the_id_registers_the_document_decodes(self):
        self.assertEqual(check_id_registers(TEXT), [])

    def test_the_state_of_the_tree_as_the_document_states_it(self):
        self.assertEqual(check_port_state(TEXT), [])

    def test_the_restart_probe_is_a_source_for_observed_rows(self):
        table = '| Item | Label |\n| --- | --- |\n| entry state | OBSERVED (restart probe, step 3) |\n'
        self.assertEqual(check_labels(table), [])
        self.assertEqual(check_labels(table.replace('restart probe, step 3', 'restart control')), [])
        self.assertTrue(check_labels(table.replace('restart probe, step 3', 'a restart')))


class Arithmetic(unittest.TestCase):
    """Numbers the document quotes, recomputed from the sources and the local dump."""

    def source(self, rel):
        return (ROOT / rel).read_text()

    def test_the_placement_recipe_is_672_mib(self):
        work = int(re.search(r'^WORK_PAGES = (\d+)', self.source('probes/monitor_work.py'), re.M).group(1)) << 12
        guest = int(re.search(r'^DEFAULT_GUEST_MIB = (\d+)', self.source('probes/xnuboot.py'), re.M).group(1))
        formula = 'return max(0x3000000, ((guest_span+0xfffffff)//0x10000000+1)*0x1000000)'
        self.assertIn(formula, self.source('probes/xnuboot.py'))
        span = guest << 20
        reserve = max(0x3000000, ((span + 0xfffffff) // 0x10000000 + 1) * 0x1000000)
        self.assertEqual((work >> 20, guest, reserve >> 20), (240, 384, 48))
        self.assertEqual((span + reserve + work) >> 20, 672)
        for needle in ('672 MiB', '240 MiB', '48 reserve', '384 guest'):
            self.assertIn(needle, TEXT)

    def test_the_low_placement_limit_lies_between_288_and_320_mib(self):
        """With the loader's 240 MiB work area and reserve, 288 MiB fits the largest low extent (590 MiB, 576 on the 32 MiB
        grid) and 320 MiB does not: the `place-check` results the document quotes."""
        reserve = lambda guest: max(0x3000000, (((guest << 20) + 0xfffffff) // 0x10000000 + 1) * 0x1000000) >> 20
        self.assertIn('return max(0x3000000, ((guest_span+0xfffffff)//0x10000000+1)*0x1000000)',
                      self.source('probes/xnuboot.py'))
        total = lambda guest: guest + reserve(guest) + 240
        self.assertEqual((reserve(288), total(288), total(320)), (48, 576, 608))
        self.assertEqual([g for g in range(32, 416, 32) if total(g) <= 576][-1], 288)
        self.assertTrue(288 <= 590 - 240 - 48 < 320)          # the unaligned bound, 302 MiB, lies between the two
        flat = squash(TEXT)
        for needle in ('unless the guest is 288 MiB or less', '288 MiB fits, 320 does not', 'so the low limit lies between them'):
            self.assertIn(needle, flat)

    def test_the_el2_regime_decodes_as_the_document_says(self):
        tcr = 0x80843514
        self.assertEqual(tcr & 0x3f, 20)           # T0SZ
        self.assertEqual((tcr >> 14) & 3, 0)       # TG0: 4 KiB
        self.assertTrue(17 <= tcr & 0x3f <= 24)    # compat/pauth-native.S accepts 17 to 24
        self.assertIn('T0SZ 20, TG0 4 KiB', TEXT)

    def test_the_firmware_map_figures(self):
        dump = ROOT / 'downloads/sp11/x2-run12/memmap.txt'
        if not dump.is_file():
            self.skipTest('the local boot-6 dump is absent: the memory-map figures are not recomputed')
        conv, end = [], 0
        for line in dump.read_text().splitlines():
            parts = line.split()
            if line.startswith('#') or len(parts) < 7:
                continue
            base, pages = int(parts[2], 16), int(parts[4], 16)
            end = max(end, base + pages * 4096)
            if parts[1] == '07':
                conv.append((base, base + pages * 4096))
        low = [(a, b) for a, b in conv if b <= 1 << 32]
        largest = max(low, key=lambda e: e[1] - e[0])
        aligned = (largest[1] // (32 << 20)) * (32 << 20) - ((largest[0] + (32 << 20) - 1) // (32 << 20)) * (32 << 20)
        bank = max(conv, key=lambda e: e[1] - e[0])
        self.assertEqual(end, 1 << 36)
        self.assertEqual((largest[1] - largest[0]) >> 20, 590)
        self.assertEqual(aligned >> 20, 576)
        self.assertEqual((bank[0], bank[1]), (0x880000000, 1 << 36))
        self.assertEqual((bank[1] - bank[0]) >> 20, 30720)
        self.assertLess(576, 672)
        self.assertIn(sum(b - a for a, b in conv) >> 20, (32105,))
        for needle in ('590 MiB', '576 MiB', '30720 MiB', '32105 MiB'):
            self.assertIn(needle, TEXT)

    @staticmethod
    def acpi_dumps():
        names = ('RSDP', 'XSDT', 'APIC', 'MCFG', 'GTDT')
        return [d for d in sorted((ROOT / 'downloads/sp11').glob('x*-run*')) if all((d / f'{n}.bin').is_file() for n in names)]

    @staticmethod
    def mcfg_rows(dump):
        """(base, segment, first bus, last bus) of every MCFG allocation, by segment."""
        import struct
        mcfg = (dump / 'MCFG.bin').read_bytes()
        rows = (struct.unpack_from('<QHBBI', mcfg, 44 + 16 * i)[:4] for i in range((len(mcfg) - 44) // 16))
        return sorted(rows, key=lambda row: row[1])

    def test_the_acpi_tables_of_the_local_dumps(self):
        """Sections 7 and 15, in every local dump: twelve 80-byte GICC entries (redistributors 0x17080000 + n x 0x40000), a
        GICD at 0x17000000 one byte over ACPI's 24 (extra byte zero), the ITS at 0x17040000 right after it, no GICR-range
        structure; the EL2 timer GSIV 26; eight MCFG allocations, segment 2 at 0x6000000000."""
        import struct
        dumps = self.acpi_dumps()
        if not dumps:
            self.skipTest('no local ACPI dump: the MADT, MCFG and GTDT figures are not recomputed')
        for dump in dumps:
            madt, offset, found = (dump / 'APIC.bin').read_bytes(), 44, []
            while offset < len(madt):
                found.append((madt[offset], madt[offset + 1], offset))
                offset += madt[offset + 1]
            base = lambda at: struct.unpack_from('<Q', madt, at)[0]
            gicc, (gicd,), (its,) = ([f for f in found if f[0] == kind] for kind in (0xB, 0xC, 0xF))
            self.assertEqual((madt[8], [kind for kind, _, _ in found]), (5, [0xB] * 12 + [0xC, 0xF]), dump.name)
            self.assertEqual({length for _, length, _ in gicc}, {80})
            self.assertEqual([base(at + 60) for _, _, at in gicc], [0x17080000 + n * 0x40000 for n in range(12)])
            self.assertEqual((gicd[1], madt[gicd[2] + 24], its[1]), (25, 0, 20))
            self.assertEqual((base(gicd[2] + 8), base(its[2] + 8)), (0x17000000, 0x17040000))
            self.assertEqual(struct.unpack_from('<I', (dump / 'GTDT.bin').read_bytes(), 72)[0], 26)
            rows = self.mcfg_rows(dump)
            self.assertEqual([(s, f, l) for _, s, f, l in rows], [(n, 0, 255 if n < 3 else 1) for n in range(8)])
            self.assertEqual(rows[2][0], 0x6000000000)
            self.assertGreaterEqual(rows[2][0], 1 << 36)
        for needle in ('0x17380000', 'GSIV 26'):
            self.assertIn(needle, TEXT)

    def test_the_adapter_reads_the_units_tables_up_to_the_ecam_rule(self):
        """Sections 7 and 15, run in memory: with a boot-ready board the adapter passes the CPU, GICD, redistributor and ITS
        rules on every local dump, then refuses the MCFG's eight allocations; raising the entry limit alone does not help
        (segment 2 lies beyond 1<<36). When the owner changes the rule this fails: update D11 and sections 7 and 13."""
        dumps = self.acpi_dumps()
        if not dumps:
            self.skipTest('no local ACPI dump: the adapter is not run on the unit tables')
        descriptor, adapter, fixture = load_adapter()
        board = fixture.board()
        for dump in dumps:
            tables = {n: (dump / f'{n}.bin').read_bytes() for n in ('RSDP', 'XSDT', 'APIC', 'MCFG')}
            with self.assertRaises(adapter.BootContractError) as caught:
                adapter.check_hamoa_platform(board, tables)
            self.assertEqual(caught.exception.code, 'ecam-mismatch', dump.name)
        saved, description = descriptor.MAX_ECAM, fixture.synthetic()
        description['pcie']['ecam'] = [{'segment': s, 'base': f'{b:#x}', 'start_bus': f, 'end_bus': l}
                                       for b, s, f, l in self.mcfg_rows(dumps[0])]
        descriptor.MAX_ECAM = 16
        try:
            with self.assertRaises(descriptor.BoardDescriptorError) as refused:
                descriptor.validate(description)
        finally:
            descriptor.MAX_ECAM = saved
        self.assertEqual(saved, 4)
        self.assertIn('pcie.ecam2: [0x6000000000', str(refused.exception))
        self.assertIn('extends beyond 1<<36', str(refused.exception))

    def test_the_id_registers_decode_and_the_monitors_gates_pass(self):
        field = lambda value, lsb: (value >> lsb) & 15
        self.assertEqual((field(DFR0, 0), field(DFR0, 12), field(DFR0, 20), field(DFR0, 36)), (9, 5, 3, 15))
        self.assertEqual(field(DFR0, 12) + 1, 6)                                   # BRPs 5: six breakpoints
        self.assertEqual((field(ISAR0, 60), field(ISAR1, 4), field(ISAR1, 24)), (1, 5, 1))   # RNDR, APA, GPA
        # The gates the document says should pass, read from the sources: a changed expression must be re-examined.
        gate = 'if(version<6 || version>11 || ((dfr0>>12)&15)<5)return 0;'
        self.assertIn(gate, self.source('compat/native-writer-debug.h'))
        self.assertFalse((DFR0 & 15) < 6 or (DFR0 & 15) > 11 or ((DFR0 >> 12) & 15) < 5)
        pauth = 'if(!(isar0>>60) || !(((isar1>>4)&255)|((isar2>>12)&15)))return 0;'
        prepare = self.source('probes/xnuboot/prepare.c')
        self.assertIn(pauth, prepare)
        self.assertIn('retry<64', prepare)                                         # the RNDR reads are still untried
        # ISAR2 was not read: APA 5 in ISAR1 satisfies the PAuth half of the gate whatever ISAR2 holds.
        self.assertFalse((ISAR0 >> 60) == 0 or ((ISAR1 >> 4) & 255) == 0)

    def test_the_restart_timing_arithmetic(self):
        """Facts of the restart probe (the coordinator's fact file; marker times are local, FAT resolution 2 s), pinned so
        that section 10 cannot drift from them."""
        from datetime import datetime
        clock = lambda text: datetime.strptime(text, '%H:%M:%S')
        gap = (clock('21:42:50') - clock('21:41:20')).seconds             # DONE3 minus DONE2
        power_cycle = (clock('21:41:20') - clock('21:40:35')).seconds     # step 1's UPTIME 123 S page to DONE2, about
        page_after_marker = 3.5                                           # step 3: DONE3, then the page at UPTIME 0 S
        self.assertEqual((gap, power_cycle), (90, 45))
        self.assertEqual(int(gap - power_cycle - page_after_marker), 41)  # step 2: UPTIME about 41 S or earlier
        self.assertEqual(42.5 - 6.5, 36.0)                                # step 3's video: page from 6.5 s of 42.5 s
        flat = squash(TEXT)
        for needle in ('90 s DONE2 to DONE3 gap', '(45 s once)', 'UPTIME about 41 S or earlier',
                       'video through UPTIME 36 S'):
            self.assertIn(needle, flat)

    def test_the_restart_control_numbers(self):
        """Facts of the restart control (2026-11-16, the coordinator's fact file): the read-only chain was alive at UPTIME
        224 S and 268 S in two boots, cpuon was followed by a restart in 2 of 3. Had the runner flow itself restarted 2 of 3
        boots, both control boots would have survived with probability (1/3)^2; each page starts about 4 s after its marker."""
        from datetime import datetime, timedelta
        clock = lambda text: datetime.strptime(text, '%H:%M:%S')
        self.assertEqual(round(100 * (1 - 2 / 3) ** 2), 11)
        self.assertEqual(clock('00:16:06') + timedelta(seconds=224), clock('00:19:50'))   # step 1's photograph
        self.assertLess(clock('00:19:50'), clock('00:20:42'))                           # before DONE2, the next stick boot
        self.assertEqual(clock('00:20:46') + timedelta(seconds=268), clock('00:25:14'))   # step 2's photograph
        flat = squash(TEXT)
        for needle in ('UPTIME 224 S and 268 S', 'about 11 %', 'correlation over five boots', 'INFERRED, strongly'):
            self.assertIn(needle, flat)


class SeededBad(unittest.TestCase):
    """Deliberately damaged copies: the checkers must say no."""

    def test_a_citation_to_a_missing_file(self):
        bad = TEXT + '\nSee `probes/does-not-exist.c:12`.\n'
        self.assertTrue([p for p in check_citations(bad, TREE)[0] if p.startswith('no such file')])

    def test_a_line_beyond_the_end_of_the_file(self):
        bad = TEXT + '\nSee `compat/clock.c:99999`.\n'
        self.assertTrue([p for p in check_citations(bad, TREE)[0] if 'out of range' in p])

    def test_a_quoted_needle_that_is_not_in_the_file(self):
        bad = TEXT + '\nIt says `this text is not in that file`[compat/clock.c:5].\n'
        self.assertTrue([p for p in check_citations(bad, TREE)[0] if p.startswith('needle not found')])

    def test_a_needle_moved_off_its_line(self):
        match = next(m for m in QUOTE.finditer(TEXT) if not m.group(2).startswith('q1n1:'))
        first = int(match.group(3))
        moved = TEXT.replace(match.group(0), match.group(0).replace(f':{first}]', f':{first + 400}]'), 1)
        self.assertTrue(check_citations(moved, TREE)[0])

    def test_an_unknown_label(self):
        bad = TEXT.replace('| VERIFIED |\n', '| ASSUMED |\n', 1)
        self.assertNotEqual(bad, TEXT)
        self.assertTrue([p for p in check_labels(bad) if 'ASSUMED' in p])
        table = '| Item | Label |\n| --- | --- |\n| x | VERIFIED, SORT OF |\n'
        self.assertTrue(check_labels(table))

    def test_an_observed_row_without_a_source(self):
        table = '| Item | Label |\n| --- | --- |\n| it was seen | OBSERVED |\n'
        self.assertTrue([p for p in check_labels(table) if 'without a source' in p])
        self.assertEqual(check_labels(table.replace('it was seen', 'it was seen (boot 5)')), [])

    def test_a_document_over_the_size_limit_or_without_not_run(self):
        self.assertTrue([p for p in check_structure(TEXT + 'x' * MAX_BYTES) if 'limit' in p])
        self.assertTrue([p for p in check_structure(TEXT.replace('Hardware status for the port itself: NOT RUN',
                                                                 'Hardware status for the port itself: PASS'))
                         if 'NOT RUN' in p])

    def test_a_missing_section(self):
        bad = TEXT.replace('## 6. Timer', '## 6x. Timer', 1)
        self.assertTrue([p for p in check_structure(bad) if 'section' in p])

    def test_a_work_item_that_closes_a_cycle_or_depends_on_a_missing_one(self):
        rows = plan_rows(TEXT)
        row = TEXT.splitlines()[next(i for i, l in enumerate(TEXT.splitlines()) if l.startswith('| 1 |'))]
        cells = split_row(row)
        self.assertEqual(cells[2], 'none')
        cycle = TEXT.replace(row, row.replace('| none |', '| 11 |', 1), 1)
        self.assertNotEqual(cycle, TEXT)
        self.assertTrue([p for p in check_plan(cycle) if 'cycle' in p])
        missing = TEXT.replace(row, row.replace('| none |', '| 40 |', 1), 1)
        self.assertTrue([p for p in check_plan(missing) if 'missing WI' in p])
        undefined = TEXT.replace(row, row.replace('| none |', '| D99 |', 1), 1)
        self.assertTrue([p for p in check_plan(undefined) if 'D99' in p])
        self.assertGreaterEqual(len(rows), 8)

    @staticmethod
    def damaged(old, new):
        """TEXT with the first occurrence of `old` (words in any line wrapping) replaced; the seed must still apply."""
        pattern = r'\s+'.join(re.escape(word) for word in old.split())
        bad, count = re.subn(pattern, lambda _: new, TEXT, count=1)
        assert count == 1, f'the document no longer contains the text this seeded defect replaces: {old[:50]}'
        return bad

    def test_the_uptime_16_mistake_is_refused(self):
        # boot 7's `UPTIME 16 S` was the first heartbeat after the chain: reading it as the restart time or a budget is wrong
        as_restart = self.damaged("boot 7's UPTIME 16 S was the first heartbeat after the chain",
                                  "boot 7's UPTIME 16 S was when it restarted")
        self.assertTrue([p for p in check_claims(as_restart) if 'a 16 s figure' in p])
        as_budget = self.damaged('bounce to end of report well under 20 s of UPTIME as a precaution',
                                 "q1n1's UPTIME at the report at most 16 s")
        self.assertTrue([p for p in check_claims(as_budget) if p.startswith('refuted')])
        self.assertTrue([p for p in check_claims(as_budget) if '20 s UPTIME budget' in p])

    def test_the_uptime_window_and_the_30_s_budget_are_refused(self):
        window = self.damaged('So, INFERRED, not at one fixed UPTIME.', 'So, INFERRED, the restart is at about UPTIME 40 to 60 S.')
        self.assertTrue([p for p in check_claims(window) if p.startswith('refuted')])
        self.assertTrue([p for p in check_claims(window) if 'not at one fixed UPTIME' in p])
        thirty = self.damaged('bounce to end of report well under 20 s of UPTIME as a precaution',
                              'bounce to end of report under about 30 s of UPTIME as a precaution')
        self.assertTrue([p for p in check_claims(thirty) if p.startswith('refuted')])

    def test_an_unattended_plan_is_refused(self):
        bad = self.damaged('operator in the loop, one boot per sample.',
                           'one unattended plan of four cpuon steps and one plain control step (each self-restart writes the next marker).')
        problems = check_claims(bad)
        self.assertTrue([p for p in problems if p.startswith('refuted')])
        self.assertTrue([p for p in problems if 'work item 10' in p])

    def test_a_reordered_survey_and_a_lost_entry_state_are_refused(self):
        reordered = self.damaged('(c) a variant that leaves cores running', '(a) a variant that leaves cores running')
        self.assertTrue([p for p in check_claims(reordered) if 'work item 10' in p])
        assumed = self.damaged('must not assume VBAR_EL2 or HCR_EL2 are reset on a restarted core',
                               'may assume the EL2 state is reset on a restarted core')
        self.assertTrue([p for p in check_claims(assumed) if 'section 7 lacks' in p])
        unknown = self.damaged('0X20200 and 0X20300 UNKNOWN', '0X20200 and 0X20300 as the others')
        self.assertTrue([p for p in check_claims(unknown) if 'section 7 lacks' in p])

    def test_a_table_that_lost_a_newline_or_a_cell_is_refused(self):
        glued = TEXT.replace('| --- | --- | --- |\n| a |', '| --- | --- | --- || a |', 1)
        self.assertNotEqual(glued, TEXT)
        self.assertTrue([p for p in check_tables(glued) if 'separator' in p])
        row = next(line for line in TEXT.splitlines() if line.startswith('| a |'))
        short = TEXT.replace(row, row.rsplit('|', 2)[0] + '|', 1)
        self.assertTrue([p for p in check_tables(short) if 'cells' in p])

    def test_a_wrong_debug_or_rng_claim_is_refused(self):
        # five breakpoints (BRPs 4) or RNDR absent would each contradict the unit's ID registers
        five = self.damaged('BRPs 5 (six breakpoints)', 'BRPs 4 (five breakpoints)')
        problems = check_id_registers(five)
        self.assertTrue([p for p in problems if p.startswith('BRPs 4')])
        self.assertTrue([p for p in problems if p.startswith('five breakpoints')])
        absent = self.damaged('RNDR 1;', 'RNDR 0;')
        self.assertTrue([p for p in check_id_registers(absent) if p.startswith('RNDR 0')])
        moved = self.damaged('0x000F01F010305719', '0x000F01F010304719')
        self.assertTrue([p for p in check_id_registers(moved) if 'ID_AA64DFR0_EL1' in p])

    def test_the_read_only_runner_flow_observed_only_to_uptime_10_is_refused(self):
        # the restart control photographed it alive at UPTIME 224 S and 268 S; boot 5's UPTIME 10 S is no longer its limit
        bad = self.damaged('was photographed alive at UPTIME 224 S and 268 S in two boots, one crisp photograph each',
                           'was observed only to UPTIME 10 S (boot 5)')
        problems = check_claims(bad)
        self.assertTrue([p for p in problems if p.startswith('refuted') and 'UPTIME 224 S and 268 S' in p])
        self.assertTrue([p for p in problems if 'section 10 must give the restart control' in p])

    def test_the_m1_run_waiting_for_the_restart_survey_or_a_budget_without_its_low_weight_is_refused(self):
        waiting = self.damaged('| 11 | **M1 run** | 3, 5, 8, 9 |', '| 11 | **M1 run** | 3, 5, 8, 9, 10 |')
        self.assertTrue([p for p in check_claims(waiting) if p.startswith('the M1 run (WI 11)')])
        bound = self.damaged('as a precaution of low weight (earliest plausible restart, in a cpuon run,',
                             'as a bound (earliest plausible restart, in a cpuon run,')
        self.assertTrue([p for p in check_claims(bound) if 'precaution of low weight' in p])

    def test_the_unknowns_that_the_x1_probe_page_closed_stay_closed(self):
        reopened = self.damaged('U5 OS lock, MDCR_EL2, ICC_SRE_EL2, DFR1, ISAR2', 'U5 DFR0, OS lock')
        self.assertTrue([p for p in check_claims(reopened) if p.startswith('refuted')])

    def test_a_wrong_state_of_the_tree_is_refused(self):
        # each seed contradicts a source or a measurement: the unit's tables, the adapter's refusal, the guards' limits,
        # the bundle, the work items' statuses, the low placement limit
        cases = (('then `check_hamoa_platform` refuses at `ecam-mismatch`', 'then `check_hamoa_platform` accepts them'),
                 ('one GICD structure of 25 bytes (ACPI: 24; the extra byte is 0)', 'one GICD structure of 24 bytes'),
                 ('then the ITS structure, no GICR-range structure', 'then the ITS structure and a GICR-range structure'),
                 ('MCFG: eight allocations, segments 0 to 7', 'MCFG: four allocations, segments 0 to 3'),
                 ('bits that stick and trap nothing pass', 'bits that stick prove the traps work'),
                 ('`real` NOT RUN', '`real` PASS'), ('IN PROGRESS elsewhere, not audited here', 'DONE'),
                 ('BUILT, NOT RUN (q1n1 d8a5d2b)', 'BUILT, PASS (q1n1 d8a5d2b)'),
                 ('the first is staged, ready to run, not run', 'the first has run'),
                 ('first code below EL2', 'first code at EL2'),
                 ('is five files, the runner\'s whole manifest', 'is four files, the runner\'s whole manifest'),
                 ('`REPORT.DAT` (528,384 zero bytes', '`REPORT.DAT` (524,288 zero bytes'),
                 ('a 288 MiB guest passes', 'a 320 MiB guest passes'))
        for old, new in cases:
            self.assertTrue(check_port_state(self.damaged(old, new)), old)

    def test_the_bundle_in_the_wrong_order_is_refused(self):
        swapped = self.damaged('`SNAPMON.BIN` (payload 0', '`REPORT.DAT` (payload 0')
        self.assertTrue([p for p in check_port_state(swapped) if 'interface' in p])

    def test_an_unblocked_adapter_and_a_dropped_ecam_decision_are_refused(self):
        unblocked = self.damaged('It reads the MADT itself (section 7), not through `from_hamoa`.',
                                 'It reads the MADT itself (section 7); `from_hamoa` is no longer blocked.')
        self.assertTrue([p for p in check_claims(unblocked) if p.startswith('refuted')])
        dropped = self.damaged('- D11 The ECAM rule', '- D12 The ECAM rule')
        self.assertTrue([p for p in check_plan(dropped) if 'D11' in p])
        self.assertTrue([p for p in check_port_state(dropped) if 'D11' in p])


if __name__ == '__main__':
    unittest.main(verbosity=1)
