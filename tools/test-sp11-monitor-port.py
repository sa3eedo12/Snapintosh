#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Keeps docs/SP11-MONITOR-PORT.md honest. Run from the repository root:  python3 -B tools/test-sp11-monitor-port.py

  citation   `path:line[-last]` (`q1n1:` = the sibling q1n1 checkout): the file and the lines exist; `fragment`[path:line]: the
             fragment occurs on the cited lines; a bare `dir/file.ext` must exist.
  labels     the six labels only, an OBSERVED row names its source, no look-alikes.     tables  header, separator, row width.
  claims     statements that were wrong once stay refused (REFUTED); the restart numbers, the survey, the secondaries' entry
             state and the ID registers are pinned.
  state      statements about the tree held against their sources: guard status values, the five bundle files in order, the
             work items' statuses (built is not run) and plan graph, the dry run, the dead low placement, MADT and MCFG.

Skipped, visibly (a skip is never a pass): q1n1 citations without a checkout (Q1N1_SP11_TREE, commit b875620) and figures read
from the git-ignored dumps under downloads/. SeededBad feeds damaged copies of the document to the same checkers: each must fail.
"""
import os
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / 'docs' / 'SP11-MONITOR-PORT.md'
LABELS = ('VERIFIED', 'OBSERVED', 'INFERRED', 'EXPERIMENTAL', 'UNIMPLEMENTED', 'UNKNOWN')
LOOKALIKES = ('ASSUMED CONFIRMED MEASURED PROVEN PROVED TESTED VALIDATED ESTABLISHED HYPOTHESIS GUESSED LIKELY SUSPECTED '
              'UNVERIFIED PRESUMED PROBABLE CERTAIN DEMONSTRATED QUALIFIED ASSERTED CLAIMED OBVIOUS TRIVIAL').split()
MAX_BYTES = 56000
SECTIONS = tuple(enumerate(('reading|milestone m1|entry|delivery|memory|timer|interrupts|id registers|platform contract|console|'
                            'thermal|safety|ordered work plan|first hardware run|decisions i need from the operator|'
                            'what i could not establish').split('|'), 1))
LOCAL_ONLY = ('downloads/', 'build/')
PATH = r'((?:q1n1:)?[A-Za-z0-9_+-]+(?:/[A-Za-z0-9_.+-]+)+\.[A-Za-z0-9]+)'
CITE = re.compile(r'`' + PATH + r':(\d+)(?:-(\d+))?`')
QUOTE = re.compile(r'`([^`\n]+)`\[' + PATH + r':(\d+)(?:-(\d+))?\]')
BARE = re.compile(r'`' + PATH + r'`(?!\[)')
SOURCE_OF_OBSERVED = re.compile(r'\bboots? \d|restart (?:probe|control)|X1 probe|\bsteps? [123]\b|operator report|\bT0\d|\bX2\b'
                                r'|\bdump\b|\bboot-\d', re.I)
SEPARATOR = re.compile(r'^\|(\s*:?-{3,}:?\s*\|)+\s*$')
# Statements that were wrong once: the restart, the dry run, the timer probes, X2 T02, ECVEn, the 2026-11-16 decisions.
REFUTED = (
    (r'(?:under|below|within|inside|at most|less than)\s+(?:that\s+|the\s+|about\s+)?(?:16|30)\s*s\b',
     'a 16 s or 30 s UPTIME budget has no basis'),
    (r'\b(?:16|30) s (?:bound|budget|limit)\b', 'a 16 s or 30 s UPTIME budget has no basis'),
    (r'least uptime at which a restart', 'the UPTIME 16 S photograph is not a restart time'),
    (r'seconds after (?:its|the) chain', 'the restart came after boot 7\'s last photograph at an unrecorded time'),
    (r'\b40 to 60 ?s\b', 'the DONE2 to DONE3 gap holds the operator\'s power cycle'),
    (r'unattended plan|one unattended|writes? the next marker|restarts? produces? the next marker',
     'after a self-restart the firmware does not return to the stick'),
    (r'(?:unobserved|not observed|observed only|only observed|restarts?)\b[^.|]{0,60}\bUPTIME 10 S',
     'the restart control saw the read-only flow alive at UPTIME 224 S and 268 S'),
    (r'U5 DFR0|values not in my sources|(?:DFR0|ISAR0)[^.|]{0,40}T01 page only|ISAR0 \(RNDR\), FGT and AMU values',
     'the ID registers, OS lock, MDCR_EL2 and ICC_SRE_EL2 were read (X1 probe page, boots 8, 9)'),
    (r'writability UNKNOWN|UNKNOWN until the dry run|STAGED on the stick|boot is NOT RUN|hazard reads?[^.|]{0,40}(?:still|to be run)',
     'the dry run ran (boot 10): the bank is writable; the hazard reads returned at EL2'),
    (r'from_hamoa`? (?:accepts|passes|is no longer blocked|is unblocked)', 'the adapter still refuses the unit\'s tables (D11)'),
    (r'traps (?:are|were) inert', 'boot 11: the ECV traps work for an EL1 stub'),
    (r'(?-i:cnthctl|el1trap|T-HW[12])[^|]{0,80}NOT RUN', 'cnthctl and el1trap ran in boot 11'),
    (r'U2 [^;.|]{0,30}UNKNOWN|O1 (?:is|was) (?:decided|approved)', 'U2 is answered for an EL1 stub; O1 is only supported'),
    (r'first code (?:ever )?(?:run )?below EL2|no code (?:has |had |ever )*run below EL2|no code below EL2 (?:has|had) (?:ever )?run',
     'X2 T02 ran an EL1 stub first: el1trap is the first runner payload below EL2'),
    (r'ECVEn`?\s+(?:is|was)\s+set|confirm\w*[^.|;]{0,25}ECVEn', 'whether the traps need ECVEn is UNKNOWN'),
    (r'E2H (?:= )?1\b', 'D6: the record has no regime field; an E2H 1 origin is not a fact'),
    (r'stop by polling|or a polling stop|polling stop in the wrapper|polling fallback',
     'D2: a polling stop cannot exist once the monitor owns the CPU'),
    (r'`real`\s+(?:is\s+)?(?:written and\s+)?(?:NOT RUN|UNIMPLEMENTED)|`real`[^|]{0,200}is written and NOT RUN|U9 stands',
     '`real` ran on the kernelcache (host only): U9 is answered'),
    (r'\bD9\b[^|.;]{0,30}\b(?:undecided|open|not decided|still to decide)\b|D9 `SNAP_NO_FB=1`', 'D9 was decided 2026-11-16'),
    (r'4b -> 6 -> 8', 'WI 8 comes before 4b: its arm half is 4b\'s GIC step'),
    (r'(?:flat PIC|position independent)[^.|]{0,40}VERIFIED|VERIFIED[^.|]{0,40}(?:flat PIC|position independent)',
     'no relocation check exists: position independence is INFERRED'),
    (r'guest (?:takes|gets|owns) the panel|panel (?:is )?handed to the guest|HVC patch (?:is )?on\b|--sptm-hvc-calls 1\b',
     'D9: the guest is denied the panel, the kernel is stock'),
    (r'MMFR4[^.|]{0,20}unread', 'the record holds MMFR4 0 (X2 run 4 T01)'),
)
# The unit's ID registers (X1 probe page, passive reads at EL2): the document's hex values and decoded fields must agree.
DFR0, ISAR0, ISAR1 = 0x000F01F010305719, 0x1221111110212120, 0x0110111101211052
FIELDS = {'DebugVer': (DFR0, 0), 'BRPs': (DFR0, 12), 'WRPs': (DFR0, 20), 'RNDR': (ISAR0, 60), 'APA': (ISAR1, 4),
          'GPA': (ISAR1, 24)}
STATED = ('DebugVer', 'BRPs', 'RNDR', 'APA')      # fields the document must state; the others are checked when it does
COUNT_WORDS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8}
# Phrases the restart section and the survey must keep.
REQUIRED = ('first heartbeat after the chain', 'not at one fixed UPTIME', 'UPTIME 123 S', 'UPTIME 36 S',
            'UPTIME 224 S and 268 S', '2 of 3 cpuon boots', 'Volume-Down + Power', 'no plan runs unattended',
            'M1 starts no secondary (x4 = 0)', 'INFERRED, strongly', 'correlation over five boots', 'about 11 %',
            'WI 10 is off the path')
SECONDARY_STATE = ('0X200 0X300 0X10000 0X10100 0X10300 0X20000 0X20100 0x80000000 0X100 0X10200 0x1488000038'.split() +
                   ['survived its own CPU_OFF and CPU_ON', '0X20200 and 0X20300 UNKNOWN',
                    'must not assume VBAR_EL2 or HCR_EL2 are reset'])
GUARD_HEADER, BUNDLE_DOC = 'compat/sp11-timer-guard.h', 'docs/SP11-STICK-BUNDLE.md'
BUNDLE_FILES = ('SNAPMON.BIN', 'SNAPREP.BIN', 'GUEST.DAT', 'PIN.DAT', 'REPORT.DAT')
BUNDLE_SIZES = (('`PIN.DAT` (1024 bytes', 1024), ('`REPORT.DAT` (528,384 zero bytes', 528384))
# Built is not run: what each work item may claim (its row must keep these words). WI 7, 9 and 8 ran (boots 10, 11, 12 and 13); WI 8's pass 5 did not.
WORK_ITEM_STATUS = {1: ('DONE, EXPERIMENTAL (b8e8f81',),
                    2: ('DONE (q1n1 d8a5d2b; ran twice, boots 8 and 9, OBSERVED)', 'all returned at EL2'),
                    3: ('`real` RAN end to end on the real kernelcache, host only, not a hardware result', 'answered U9',
                        'Still blocking a bundle for hardware: the monitor payload (WI 4b)'),
                    4: ('4a BUILT for the dry run, EXPERIMENTAL, RAN once on the unit (WI 7; its refusal paths NOT RUN',
                        '4b, the monitor entry after WI 8', 'UNIMPLEMENTED, a plan', 'D2 (decided), D4'),
                    5: ('BUILT, EXPERIMENTAL, host side',),
                    7: ('DONE, boot 10 (2026-11-16 10:21, plan DRYRUN-A, OBSERVED)', 'calls no monitor', '`DONE RETURN=0`',
                        '`AT S1E2W` accepted 337 of 337 probes', 'matched 13 of 13 digests',
                        "the stick unchanged but for the Shell's marker (nothing written by the unit)",
                        'answered U1 (writability), U6 (base); U1 stays open for `DC CVAC` alone'),
                    8: ('BUILT host-side, EXPERIMENTAL', 'RAN twice on the unit (boots 12 and 13, 2026-11-16, OBSERVED', 'pass 2 done',
                        'pass 3 done', 'host-tested only, hardware NOT RUN, its run GICDRY-C prepared and NOT authorised', 'before 4b',
                        'resolves U3; D2 (decided), D4', 'U3 open'),
                    9: ('DONE (q1n1 b875620; boot 11, 2026-11-16 11:44, OBSERVED', 'first runner payload to run code below EL2',
                        '`BITS13_14_STUCK=YES`', '`VERDICT=TRAPS_EFFECTIVE`', 'answered U2 (EL1 stub only)',
                        'resolved D3 for these two payloads')}
# The tree as the document states it (whitespace-collapsed); the sources that decide it are read below.
STATE_REQUIRED = '''\
is five files, the runner's whole manifest
bits that stick and trap nothing pass
T-HW2 saw them trap for an EL1 stub only
neither holds the monitor
11 = plan timers
SUPPORTED by boot 11, not decided
U2 trap effect (answered for an EL1 stub, WI 9)
after X2 T02's EL1 stub
ECVEn` clear, is INFERRED (X2 T03) and neither confirmed nor refuted by boot 11
whether they depend on `SCR_EL3.ECVEn`
by SHA-256 for one boot
(not met: boot 11)
alive at UPTIME 49 S
Placement A, low, is dead: 446 MiB remain below the 144 MiB file
passes on 0 of 6 local maps (REFUSED=PLACE_DECLARED
384 MiB passes 6 of 6 and ran in the dry run
then showed the bank writable: `AT S1E2W` accepted all 337 probes
13 of 13 data-chunk digests matched on re-read from DRAM after `DC CIVAC`
UNKNOWN: whether `DC CVAC` alone reaches DRAM (CIVAC cleans first)
the 138 MiB `GUEST.DAT` at 0xC446E000 (boot 10)
The wrapper took 2.92 s (19.2 MHz ticks)
DminLine 4; IDC 1, DIC 1 INFERRED
only the happy path ran
the bank, writable at EL2 (boot 10), is what is left
U1 `DC CVAC` alone reaching DRAM (bank writability: answered, WI 7)
U6 GUEST.DAT's base (answered, WI 7)
showed no restart in its 67.5 s video
Time budget: the wrapper 2.92 s as measured
APPROVED, for the dry run only
the dry run ran under it (WI 7)
Not the M1 run's decision
12 GICC entries of 80 bytes
one GICD structure of 25 bytes (ACPI: 24; the extra byte is 0)
then the ITS structure, no GICR-range structure
MCFG: eight allocations, segments 0 to 7
segment 2 at 0x6000000000
RESOLVED in e3844fa
a1fb91c (redistributors from the per-CPU GICC bases
then `check_hamoa_platform` refuses at `ecam-mismatch`
a descriptor holds at most four ECAM entries
the adapter demands set equality
the MCFG lists eight allocations, a descriptor holds four
an eight-entry descriptor is refused
the monitor entry (WI 4b) is a plan; the bundle builder ran `real` on the kernelcache, host only
probably position independent (INFERRED: no relocation check exists, WI 4b adds one)
a polling stop cannot exist, as the wrapper does not run while the monitor owns the CPU
the boot CPU writes only CNTHCTL_EL2
`q1n1:platform/uefi/gicv3.c:95-165`
head is q1n1's payload stub
holds the FIRST 1,024 bytes of guest output
the monitor's window [+0x1000, +0x11000)
extension records from +0x11000
D9, decided 2026-11-16 ("Withheld and stock")
D2 Stop: DECIDED 2026-11-16 with D5
D5 The M1 time box: DECIDED 2026-11-16 (with D2): 5 s
N2 Descriptor policy (new): DECIDED 2026-11-16 ("Approve")
five devices (debug UART, keyboard, touchpad, touchscreen, NVMe) absent
is a DEFAULT, not an operator decision
D11 proper stays OPEN
U9 kernelcache (answered, WI 3)
WI 4b, the monitor entry: a plan, UNIMPLEMENTED, hardware NOT RUN.
`SNAP_SP11_STICK_MONITOR`, default 0
Gaps G1 to G9
8 -> 4b -> 6 -> 11
its MMFR4 is 0 as read in X2 run 4 T01, but the gate has not run with it: a pass is INFERRED
D6 NV2 record: which record, and whether its regime matters: UNKNOWN
APPROVED for the GIC dry run only, by SHA-256, twice
for the monitor entry OPEN, asked by SHA-256 with the M1 record
nor does the GIC dry run, WI 8, which ran twice, boots 12 and 13
12 and 13 = plans GICDRY-A and GICDRY-B
SGI 0 and PPIs 23, 25, 26, 27 and 30 enabled by the firmware
PPI 26's own delivery is UNKNOWN until a window reaches CNTHP's deadline
quarantines this CPU's other enabled interrupts first
GUEST.DAT 118,870,336 B (44 chunks)
'''.splitlines()


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
    refs = [(m[1], int(m[2]), int(m[3] or m[2]), None) for m in CITE.finditer(text)]
    refs += [(m[2], int(m[3]), int(m[4] or m[3]), m[1].replace('\\|', '|')) for m in QUOTE.finditer(text)]
    refs += [(m[1], None, None, None) for m in BARE.finditer(text)]
    for rel, first, last, needle in refs:
        path, skip = resolve(rel, tree)
        if skip:
            continue
        checked += 1
        if not path.is_file():
            problems.append(f'no such file: {rel}')
        elif first is not None:
            lines = lines_of(path)
            if not 1 <= first <= last <= len(lines):
                problems.append(f'line out of range: {rel}:{first}-{last} (file has {len(lines)} lines)')
            elif needle is not None and needle not in '\n'.join(lines[first - 1:last]):
                problems.append(f'needle not found: `{needle}` at {rel}:{first}-{last}')
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
    """Every block of `|` lines needs a header, an exact separator row and rows as wide as the header (a lost newline would
    turn a table into prose and escape the label checks)."""
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
        problems.append('work item 10 must say: before SMP work only, operator in the loop, psci-only control DONE (UPTIME 224 S '
                        'and 268 S), left (a) film cpuon, (b) one core, (c) cores left running; cpuon 2 of 3')
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
    graph = {4: '1, 8', 6: '4, 5', 7: '3, 4a, D1, D7', 8: '4a, 7', 11: '3, 4, 5, 6, 8, 9'}   # WI 8's arm half precedes 4b
    problems += [f'WI {n} must depend on {d}' for n, d in graph.items() if rows.get(n, {}).get('Depends') != d]
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
        self.assertLess(me.stat().st_size, 52 * 1024)
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
        self.assertGreater(len(re.findall(r'q1n1:[\w/.+-]+:\d+', TEXT)), 3)


class Plan(unittest.TestCase):
    def test_work_items_dependencies_decisions_and_unknowns(self):
        self.assertEqual(check_plan(TEXT), [])


class Claims(unittest.TestCase):
    def test_the_restart_numbers_the_survey_and_the_secondary_entry_state(self):
        self.assertEqual(round(100 * (1 - 2 / 3) ** 2), 11)        # both restart controls alive if 2 of 3 boots restarted
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
        for needle in ('672 MiB', '240 work', '48 reserve', '384 guest'):
            self.assertIn(needle, TEXT)

    def test_the_low_placement_is_dead_and_the_dry_run_figures_agree(self):
        """A 288 MiB guest (576 MiB with reserve and work area) cannot fit below the runner's files at the top of the 590 MiB
        low extent (boots 8 to 10); the dry run's figures (boot 10) and the real GUEST.DAT's PARAMS agree."""
        reserve = lambda guest: max(0x3000000, (((guest << 20) + 0xfffffff) // 0x10000000 + 1) * 0x1000000) >> 20
        low, end, file_at, guest_at, guest_size = 0xA8000000, 0xCCE65000, 0xC3E65000, 0xC446E000, 144665408
        self.assertEqual(file_at + (144 << 20), end)
        self.assertEqual(-(-(guest_at + guest_size) // 4096) * 4096, end)         # GUEST.DAT's pages end at the extent end
        self.assertEqual(((file_at - low) >> 20, (guest_at - low) >> 20, 288 + reserve(288) + 240), (446, 452, 576))
        hz, guest_ok, hashed = 19200000, 36519004, 56114326                       # the wrapper's TICKS lines
        self.assertEqual((round(hashed / hz, 2), round((144664080 + 144665408) / (guest_ok / hz) / 2**20)), (2.92, 145))
        self.assertEqual(((337 - 1) * 2, 0x880000000 + 0x2A000000, 0x880000000 + (0x780000 << 12)), (672, 0x8AA000000, 1 << 36))
        self.assertEqual(0x89b000000 - 0x880000000, (384 + reserve(384)) << 20)   # the real GUEST.DAT: guest and reserve
        ctr = 0xB444C004
        self.assertEqual(((1 << ((ctr >> 16) & 15)) * 4, (ctr >> 28) & 1, (ctr >> 29) & 1), (64, 1, 1))    # DminLine, IDC, DIC
        flat = squash(TEXT)
        for needle in ('(452 below `GUEST.DAT`)', 'accepted all 337 probes', 'about 145 MiB/s', 'The wrapper took 2.92 s',
                       '`mem_high` 0x89b000000'):
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
        self.assertEqual((largest, (largest[1] - largest[0]) >> 20), ((0xA8000000, 0xCCE65000), 590))
        self.assertEqual(aligned >> 20, 576)
        self.assertEqual((bank[0], bank[1], (bank[1] - bank[0]) >> 20), (0x880000000, 1 << 36, 30720))
        self.assertEqual(sum(b - a for a, b in conv) >> 20, 32105)
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
        """Every local dump: 12 GICC entries (GICR 0x17080000 + n x 0x40000), a 25-byte GICD at 0x17000000, the ITS, no GICR
        structure, GTDT GSIV 26, eight MCFG allocations."""
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
        """A boot-ready board in memory: the adapter passes the CPU, GICD, GICR and ITS rules on every local dump, then refuses
        the MCFG's eight allocations (raising the limit alone fails: segment 2 lies beyond 1<<36). If the rule changes, update D11."""
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
        # The gates the document says should pass: a changed expression must be re-examined.
        gate = 'if(version<6 || version>11 || ((dfr0>>12)&15)<5)return 0;'
        self.assertIn(gate, self.source('compat/native-writer-debug.h'))
        self.assertFalse((DFR0 & 15) < 6 or (DFR0 & 15) > 11 or ((DFR0 >> 12) & 15) < 5)
        pauth = 'if(!(isar0>>60) || !(((isar1>>4)&255)|((isar2>>12)&15)))return 0;'
        prepare = self.source('probes/xnuboot/prepare.c')
        self.assertIn(pauth, prepare)
        self.assertIn('retry<64', prepare)                                         # the RNDR reads are still untried
        mmfr1, isar2, oslsr, icc_sre = 0x111111312120, 0x10000012, 0x8, 0xF        # boots 8, 9 (el2mem)
        self.assertEqual((field(mmfr1, 20), field(mmfr1, 8), field(mmfr1, 0)), (3, 1, 0))      # PAN, VH, HAFDBS
        self.assertEqual((field(isar2, 12), field(isar2, 8)), (0, 0))              # APA3, GPA3: the gate rests on ISAR1 APA 5
        self.assertEqual(((oslsr >> 1) & 1, icc_sre & 1, (icc_sre >> 3) & 1), (0, 1, 1))       # OSLK clear; SRE, Enable set
        self.assertFalse((ISAR0 >> 60) == 0 or (((ISAR1 >> 4) & 255) | ((isar2 >> 12) & 15)) == 0)
        flat = squash(TEXT)
        for needle in ('MMFR1 0x111111312120: PAN 3, VH 1, HAFDBS 0', 'ISAR2 0x10000012', 'OSLSR_EL1 0x8 (OS lock not set)',
                       'MDCR_EL2 0x6', 'ICC_SRE_EL2 0xF at the call'):
            self.assertIn(needle, flat)


class SeededBad(unittest.TestCase):
    """Deliberately damaged copies: the checkers must say no."""

    def test_a_missing_file_a_line_beyond_the_end_and_a_missing_needle_are_refused(self):
        for tail, problem in (('`probes/does-not-exist.c:12`', 'no such file'), ('`compat/clock.c:99999`', 'out of range'),
                              ('`this text is not in that file`[compat/clock.c:5]', 'needle not found')):
            self.assertTrue([p for p in check_citations(TEXT + f'\nSee {tail}.\n', TREE)[0] if problem in p], tail)

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
        row = next(line for line in TEXT.splitlines() if line.startswith('| 1 |'))
        self.assertEqual(split_row(row)[2], 'none')
        for depends, what in (('11', 'cycle'), ('40', 'missing WI'), ('D99', 'D99')):
            bad = TEXT.replace(row, row.replace('| none |', f'| {depends} |', 1), 1)
            self.assertTrue([p for p in check_plan(bad) if what in p], depends)
        self.assertGreaterEqual(len(plan_rows(TEXT)), 8)

    @staticmethod
    def damaged(old, new):
        """TEXT with the first occurrence of `old` (words in any line wrapping) replaced; the seed must still apply."""
        pattern = r'\s+'.join(re.escape(word) for word in old.split())
        bad, count = re.subn(pattern, lambda _: new, TEXT, count=1)
        assert count == 1, f'the document no longer contains the text this seeded defect replaces: {old[:50]}'
        return bad

    @staticmethod
    def seeds(block):
        """`old => new [=> needle|needle]` lines: damaged text, and what every refusal must show (default: refuted)."""
        parts = [line.split(' => ') for line in block.splitlines()]
        return [(p[0], p[1], p[2].split('|') if len(p) > 2 else ['refuted']) for p in parts]

    # Refused by check_claims (the first occurrence of the document's text is replaced by the wrong text).
    CLAIM_SEEDS = '''\
Boot 7's UPTIME 16 S was the first heartbeat after the chain => Boot 7's UPTIME 16 S was when it restarted => a 16 s figure
well under 20 s of UPTIME as a precaution => at most 16 s => refuted|20 s UPTIME budget
well under 20 s of UPTIME as a precaution => under about 30 s of UPTIME as a precaution
so, INFERRED, not at one fixed UPTIME. => so, INFERRED, the restart is at about UPTIME 40 to 60 S. => refuted|not at one fixed UPTIME
operator in the loop, one boot per sample. => one unattended plan (each self-restart writes the next marker). => refuted|work item 10
(c) a variant that leaves cores running => (a) a variant that leaves cores running => work item 10
must not assume VBAR_EL2 or HCR_EL2 are reset on a restarted core => may assume the EL2 state is reset on a restarted core => section 7 lacks
0X20200 and 0X20300 UNKNOWN => 0X20200 and 0X20300 as the others => section 7 lacks
was photographed alive at UPTIME 224 S and 268 S => was observed only to UPTIME 10 S (boot 5) => refuted|section 10 must give the restart control
| 11 | **M1 run** | 3, 4, 5, 6, 8, 9 | => | 11 | **M1 run** | 3, 4, 5, 6, 8, 9, 10 | => the M1 run (WI 11)
as a precaution of low weight (earliest => as a bound (earliest => precaution of low weight
U5 debug and RNDR behaviour, other cores => U5 DFR0, OS lock
(bank writability: answered, WI 7) => (bank writability UNKNOWN until the dry run)
DONE, boot 10 (2026-11-16 10:21, => BUILT and STAGED on the stick, the boot is NOT RUN
all returned at EL2 => are the hazard reads, still to be run
not through `from_hamoa`. => `from_hamoa` is no longer blocked.
`VERDICT=TRAPS_EFFECTIVE` => the ECV traps are inert
DONE (q1n1 b875620; boot 11 => BUILT, NOT RUN (q1n1 b875620; boot 11
(answered for an EL1 stub, WI 9) => (UNKNOWN)
O1, the A16 recipe => O1 is decided, the A16 recipe
the first runner payload to run code => the first code
neither confirmed nor refuted by boot 11 => wrong: `SCR_EL3.ECVEn` is set
D6 NV2 record: which record, and whether its regime matters: UNKNOWN. => D6 NV2 record: the local one is from the first X2 image (E2H = 1).
the GIC timer interrupt; a polling stop cannot exist once the monitor owns the CPU. => the GIC timer interrupt, or stop by polling.
`real` RAN end to end on the real kernelcache, host only, not a hardware result => `real` NOT RUN
D9 Panel and kernel: DECIDED => D9 Panel and kernel: still undecided
8 -> 4b -> 6 -> 11 => 4b -> 6 -> 8 -> 11
probably position independent (INFERRED: no relocation check exists, WI 4b adds one) => a flat PIC binary (VERIFIED)
the guest is denied the panel (`SNAP_NO_FB`, `--panel withheld`) and the kernel stays unmodified (`--sptm-hvc-calls 0`) => the guest takes the panel and the HVC patch is on (`--sptm-hvc-calls 1`)
its MMFR4 is 0 as read in X2 run 4 T01 => MMFR4 is unread
'''
    ID_SEEDS = '''\
BRPs 5 (six breakpoints) => BRPs 4 (five breakpoints) => BRPs 4|five breakpoints
RNDR 1; => RNDR 0; => RNDR 0
0x000F01F010305719 => 0x000F01F010304719 => ID_AA64DFR0_EL1
'''

    def test_the_claims_that_were_wrong_once_stay_refused(self):
        for check, block in ((check_claims, self.CLAIM_SEEDS), (check_id_registers, self.ID_SEEDS)):
            for old, new, shown in self.seeds(block):
                problems = check(self.damaged(old, new))
                for what in shown:
                    self.assertTrue([p for p in problems if what in p], (old[:40], what))

    def test_a_table_that_lost_a_newline_or_a_cell_is_refused(self):
        glued = TEXT.replace('| --- | --- | --- |\n| a |', '| --- | --- | --- || a |', 1)
        self.assertNotEqual(glued, TEXT)
        self.assertTrue([p for p in check_tables(glued) if 'separator' in p])
        row = next(line for line in TEXT.splitlines() if line.startswith('| a |'))
        short = TEXT.replace(row, row.rsplit('|', 2)[0] + '|', 1)
        self.assertTrue([p for p in check_tables(short) if 'cells' in p])

    # Each wrong state contradicts a source or a measurement: check_port_state must refuse it.
    STATE_CASES = '''\
then `check_hamoa_platform` refuses at `ecam-mismatch` => then `check_hamoa_platform` accepts them
one GICD structure of 25 bytes (ACPI: 24; the extra byte is 0) => one GICD structure of 24 bytes
then the ITS structure, no GICR-range structure => then the ITS structure and a GICR-range structure
MCFG: eight allocations, segments 0 to 7 => MCFG: four allocations, segments 0 to 3
bits that stick and trap nothing pass => bits that stick prove the traps work
`real` RAN end to end on the real kernelcache => `real` PASS
4a BUILT for the dry run, EXPERIMENTAL, RAN once on the unit => 4a DONE
4b, the monitor entry after WI 8 => 4b, the monitor entry before WI 8
`el1trap` is the first runner payload to run code below EL2 => `el1trap` is the first code below EL2
SUPPORTED by boot 11, not decided => DECIDED by boot 11
after X2 T02's EL1 stub => on the unit
and neither confirmed nor refuted by boot 11 => and confirmed by boot 11
`BITS13_14_STUCK=YES`, `RESTORED=YES` => `BITS13_14_STUCK=NO`, `RESTORED=YES`
(not met: boot 11) => (met)
and calls no monitor => and calls the monitor
then showed the bank writable => left it open whether the bank is writable
Placement A, low, is dead => Placement A, low, works
UNKNOWN: whether `DC CVAC` alone reaches DRAM (CIVAC cleans first) => `DC CVAC` alone reaches DRAM
(nothing written by the unit) => (the unit wrote the report to it)
U6 GUEST.DAT's base (answered, WI 7) => U6 GUEST.DAT's base
Not the M1 run's decision => The M1 run's decision too
is five files, the runner's whole manifest => is four files, the runner's whole manifest
`REPORT.DAT` (528,384 zero bytes => `REPORT.DAT` (524,288 zero bytes
| 4a, 7 | => | 4, 6, 7 |
| 1, 8 | => | 1 |
| 3, 4, 5, 6, 8, 9 | => | 3, 5, 8, 9 |
host-tested only, hardware NOT RUN, its run GICDRY-C => host-tested only, hardware PASS, its run GICDRY-C
RAN twice on the unit (boots 12 and 13 => BUILT, NOT RUN (boots 12 and 13
APPROVED for the GIC dry run only, by SHA-256, twice => OPEN, asked by SHA-256 with the dry-run record
D2 Stop: DECIDED 2026-11-16 with D5 => D2 Stop: OPEN with D5
N2 Descriptor policy (new): DECIDED => N2 Descriptor policy (new): proposed
is a DEFAULT, not an operator decision => is the operator's decision
D11 proper stays OPEN => D11 proper is settled
U9 kernelcache (answered, WI 3) => U9 kernelcache
a plan, UNIMPLEMENTED, hardware NOT RUN. => built, hardware PASS.
holds the FIRST 1,024 bytes of guest output and counts the rest => is a ring of the LAST 1,024 bytes of guest output
extension records from +0x11000 => extension records from +0x10000
the boot CPU writes only CNTHCTL_EL2 => the boot CPU writes TCR, CNTHCTL and CPTR
head is q1n1's payload stub => head is the monitor's own
a pass is INFERRED => a pass is VERIFIED
`SNAP_SP11_STICK_MONITOR`, default 0 => `SNAP_SP11_STICK_MONITOR`, default 1
'''

    def test_a_wrong_state_of_the_tree_is_refused(self):
        for old, new, _ in self.seeds(self.STATE_CASES):
            self.assertTrue(check_port_state(self.damaged(old, new)), old)

    def test_the_bundle_in_the_wrong_order_is_refused(self):
        swapped = self.damaged('`SNAPMON.BIN` (payload 0', '`REPORT.DAT` (payload 0')
        self.assertTrue([p for p in check_port_state(swapped) if 'interface' in p])

    def test_a_dropped_ecam_decision_is_refused(self):
        dropped = self.damaged('- D11 The ECAM rule', '- D12 The ECAM rule')
        self.assertTrue([p for p in check_plan(dropped) if 'D11' in p])
        self.assertTrue([p for p in check_port_state(dropped) if 'D11' in p])


if __name__ == '__main__':
    unittest.main(verbosity=1)
