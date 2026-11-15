#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Keeps docs/SP11-MONITOR-PORT.md honest: its citations, quotations, labels, size and structure.

Run from the repository root:  python3 -B tools/test-sp11-monitor-port.py

Conventions the document follows and this test enforces (stated here because the document itself only describes them):

  citation   `path:line` or `path:first-last` in backticks. The path is relative to the repository root, or starts with
             `q1n1:` and is then relative to the sibling q1n1 checkout. The file must exist and the lines must lie
             inside it.
  quotation  `fragment`[path:line] or `fragment`[path:first-last]: a backticked fragment of source text directly followed
             by a bracketed citation. The fragment (a table-cell `\\|` read as `|`) must occur on the cited lines. A
             quotation is how the document says "this exact code is there"; a moved line fails here.
  bare path  a backticked path with a directory part and a file extension and no line: the file must exist.
  labels     VERIFIED, OBSERVED, INFERRED, EXPERIMENTAL, UNIMPLEMENTED, UNKNOWN. A table with a `Label` column holds only
             these (parenthesised notes aside), and an OBSERVED row names its source (a boot number, the restart probe, an
             operator report, a test id or a dump). A list of look-alike labels (ASSUMED, CONFIRMED, ...) must not occur
             anywhere.
  tables     every block of `|` lines has a header, an exact `| --- |` separator row and rows of the header's width: a
             table that loses a newline renders as prose and slips past every label check.
  claims     statements that were wrong once and must not come back (the restart section): a restart time or UPTIME
             budget read off the page's `UPTIME 16 S`, which was only the first heartbeat after the cpuon chain; an
             UPTIME window derived from the DONE marker gap, which holds the operator's power cycle; a plan that "runs
             unattended" across a self-restart, after which the firmware does not return to the stick. The restart
             numbers, the order of the restart survey, the EL2 entry state of the secondaries, and the unit's DFR0 and
             ISAR0 values (X1 probe page) with everything the document decodes from them are pinned.

What always runs: size, ASCII and hygiene, the section list, "NOT RUN" for the port, the label rules, the table and claim
checks, the citations into this repository, the work-plan graph, and arithmetic the document quotes (recomputed from the
sources). What is skipped,
visibly (a skip is never a pass): citations into the q1n1 checkout when none is found (set Q1N1_SP11_TREE to the checkout the
document names, commit d48b0af, to force it), and facts read from the local, git-ignored unit dumps under downloads/ when
they are absent.

The last classes feed deliberately damaged copies of the document to the same checkers; each must be refused.
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
SOURCE_OF_OBSERVED = re.compile(r'\bboots? \d|restart probe|X1 probe|\bsteps? [123]\b|operator report|\bT0\d|\bX2\b|\bdump\b|\bboot-\d',
                                re.I)
SEPARATOR = re.compile(r'^\|(\s*:?-{3,}:?\s*\|)+\s*$')
# Statements that were wrong once. UPTIME is q1n1's page counter, started before the payload runner, so the cpuon chain
# (two 8 s waits) is inside it: `UPTIME 16 S` on boot 7's last photograph was the first heartbeat after the chain, not
# the time of the restart. The 90 s between the DONE2 and DONE3 markers holds the operator's power cycle, and a
# self-restart ends the stick's plan: the firmware boots its own order, so no plan runs unattended.
REFUTED = (
    (r'(?:under|below|within|inside|at most|less than)\s+(?:that\s+|the\s+|about\s+)?(?:16|30)\s*s\b',
     'a 16 s or 30 s UPTIME budget has no basis'),
    (r'\b(?:16|30) s (?:bound|budget|limit)\b', 'a 16 s or 30 s UPTIME budget has no basis'),
    (r'least uptime at which a restart', 'the UPTIME 16 S photograph is not a restart time'),
    (r'seconds after (?:its|the) chain', 'the restart came after boot 7\'s last photograph at some unrecorded time'),
    (r'\b40 to 60 ?s\b', 'the DONE2 to DONE3 gap holds the operator\'s power cycle: no UPTIME window follows from it'),
    (r'unattended plan|one unattended|writes? the next marker|restarts? produces? the next marker',
     'after a self-restart the firmware does not return to the stick'),
    (r'U5 DFR0|values not in my sources|(?:DFR0|ISAR0)[^.|]{0,40}T01 page only|ISAR0 \(RNDR\), FGT and AMU values',
     'DFR0, ISAR0, FGT and AMU are known since the X1 probe page; only the OS lock, MDCR_EL2, ICC_SRE_EL2, DFR1 and ISAR2 are not'),
)
# ID registers of the unit, from the X1 probe page (2026-11-11, a photograph; one unit, one boot; passive reads at EL2).
# They are facts of the coordinator's fact file, not of this repository, so they are pinned here: the document's hex
# values and every field it decodes must agree with them, and the monitor's gates are held against them below.
DFR0, ISAR0, ISAR1 = 0x000F01F010305719, 0x1221111110212120, 0x0110111101211052
FIELDS = {'DebugVer': (DFR0, 0), 'BRPs': (DFR0, 12), 'WRPs': (DFR0, 20), 'RNDR': (ISAR0, 60), 'APA': (ISAR1, 4),
          'GPA': (ISAR1, 24)}
STATED = ('DebugVer', 'BRPs', 'RNDR', 'APA')      # fields the document must state; the others are checked when it does
COUNT_WORDS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8}
# Phrases the restart section and the survey must keep (matched on whitespace-collapsed text).
REQUIRED = ('first heartbeat after the chain', 'not at one fixed UPTIME', 'UPTIME 123 S', 'UPTIME 36 S', 'UPTIME 10 S',
            '2 of 3 cpuon boots', 'creationdate', 'Volume-Down + Power', 'no plan runs unattended',
            'M1 starts no secondary (x4 = 0)')
SECONDARY_STATE = ('0X200', '0X300', '0X10000', '0X10100', '0X10300', '0X20000', '0X20100', '0x80000000', '0X100',
                   '0X10200', '0x1488000038', 'survived its own CPU_OFF and CPU_ON', '0X20200 and 0X20300 UNKNOWN',
                   'must not assume VBAR_EL2 or HCR_EL2 are reset')


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
    if len(re.findall(r'UPTIME 17 S', flat)) < 2:
        problems.append('the earliest plausible restart, UPTIME about 17 S, must stand in sections 10 and 15')
    row = plan_rows(text).get(10, {})
    survey = (row.get('Work', '') + ' ' + row.get('Acceptance (can fail)', '')).strip()
    if not re.search(r'operator in the loop.*\(a\).*psci-only.*150 s.*twice.*\(b\).*cpuon.*video.*\(c\).*P1 only.*left running'
                     r'.*CPU_OFF omitted.*2 of 3', survey, re.S):
        problems.append('work item 10 must list, in order: (a) the psci-only read-only chain held 150 s twice, (b) cpuon '
                        'under continuous video, (c) variants; operator in the loop; cpuon at 2 of 3')
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


def check_all(text, tree):
    problems = (check_structure(text) + check_labels(text) + check_plan(text) + check_tables(text) + check_claims(text) +
                check_id_registers(text))
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

    def test_the_restart_probe_is_a_source_for_observed_rows(self):
        table = '| Item | Label |\n| --- | --- |\n| entry state | OBSERVED (restart probe, step 3) |\n'
        self.assertEqual(check_labels(table), [])


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
        small = 256 << 20
        small_reserve = max(0x3000000, ((small + 0xfffffff) // 0x10000000 + 1) * 0x1000000)
        self.assertEqual((small + small_reserve + work) >> 20, 544)
        for needle in ('672 MiB', '544 MiB', '240 MiB', '48 reserve', '384 guest'):
            self.assertIn(needle, TEXT)

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

    def test_the_gic_and_timer_facts_of_the_acpi_dump(self):
        import struct
        run = ROOT / 'downloads/sp11/x2-run12'
        if not (run / 'APIC.bin').is_file() or not (run / 'GTDT.bin').is_file():
            self.skipTest('the local boot-6 ACPI dump is absent: GIC and timer figures are not recomputed')
        madt = (run / 'APIC.bin').read_bytes()
        frames, gicd, its, offset = [], None, None, 44
        while offset < len(madt):
            kind, length = madt[offset], madt[offset + 1]
            if kind == 0xB:
                frames.append(struct.unpack_from('<Q', madt, offset + 60)[0])
            elif kind == 0xC:
                gicd = struct.unpack_from('<Q', madt, offset + 8)[0]
            elif kind == 0xF:
                its = struct.unpack_from('<Q', madt, offset + 8)[0]
            offset += length
        self.assertEqual((gicd, its, len(frames)), (0x17000000, 0x17040000, 12))
        self.assertEqual(frames, [0x17080000 + n * 0x40000 for n in range(12)])
        self.assertEqual(frames[-1] + 0x40000, 0x17380000)
        self.assertEqual(struct.unpack_from('<I', (run / 'GTDT.bin').read_bytes(), 72)[0], 26)
        for needle in ('0x17380000', 'GSIV 26'):
            self.assertIn(needle, TEXT)

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
        """The inputs are facts of the restart probe from the coordinator's fact file, not from this repository (marker
        times are local, the FAT has a 2 s resolution); they are pinned here so that section 10 cannot drift from them."""
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
        bad = self.damaged('operator in the loop, one boot per sample (a restart ends the run; every stick boot is started by hand):'
                           ' (a) the read-only runner chain (psci-only manifest) held 150 s, twice, as the control;',
                           'one unattended plan of four cpuon steps and one plain control step (each self-restart writes the next marker);')
        problems = check_claims(bad)
        self.assertTrue([p for p in problems if p.startswith('refuted')])
        self.assertTrue([p for p in problems if 'work item 10' in p])

    def test_a_reordered_survey_and_a_lost_entry_state_are_refused(self):
        reordered = self.damaged('(c) variants', '(a) variants')
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

    def test_the_unknowns_that_the_x1_probe_page_closed_stay_closed(self):
        reopened = self.damaged('U5 OS lock, MDCR_EL2, ICC_SRE_EL2, DFR1, ISAR2', 'U5 DFR0, OS lock')
        self.assertTrue([p for p in check_claims(reopened) if p.startswith('refuted')])


if __name__ == '__main__':
    unittest.main(verbosity=1)
