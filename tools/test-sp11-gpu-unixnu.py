#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Checks that docs/SP11-GPU-UNIXNU.md and its evidence file still say what their pinned sources say.

Every fact in the study is a row of its evidence register (appendix C): a label, a basis, a source range and a token that
must occur in that range. The register is kept in docs/SP11-GPU-UNIXNU-EVIDENCE.md so that neither file grows without
bound; the checks read the two files as one study, the register standing where appendix C stands. The body cites rows as
[LABEL Uxx]. The study fails here when it loses a label, a pin, a section, a headline number or its stance, when a tag
cites a row that does not exist or carries another row's label, and when a row is cited by nothing, so an edit cannot
drift away from the register unnoticed.

The register is audited against the pinned sources when they are checked out. The repository carries none of them, so a
missing checkout is skipped and reported, never trusted:

    python3 tools/test-sp11-gpu-unixnu.py
    SP11_UNIXNU_REFS=<dir> SP11_LINUX_TREE=<dir> SP11_MESA_TREE=<dir> SP11_MESA_UNIXNU_TREE=<dir> \
        SP11_FIRMWARE_TREE=<dir> python3 tools/test-sp11-gpu-unixnu.py

SP11_UNIXNU_REFS holds one directory per UniXNU repository, named as in appendix B (default build/ref/unixnu). The four
tree variables name a Linux, a Mesa (our pin), a Mesa (UniXNU's pin) and a linux-firmware checkout (default
build/ref/<name>-<commit>). SP11_UNIXNU_DOC and SP11_UNIXNU_EVIDENCE_DOC replace the two files, which lets a seeded bug
be tried on copies.
"""
import collections
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = Path(os.environ.get('SP11_UNIXNU_DOC') or ROOT / 'docs' / 'SP11-GPU-UNIXNU.md')
EVIDENCE = Path(os.environ.get('SP11_UNIXNU_EVIDENCE_DOC') or ROOT / 'docs' / 'SP11-GPU-UNIXNU-EVIDENCE.md')
A7XX = ROOT / 'docs' / 'SP11-GPU-A7XX-STUDY.md'
PLAN = ROOT / 'docs' / 'SURFACE-PRO-11.md'
REF = ROOT / 'build' / 'ref'


def read_study():
    """The study as one text: the study file, then the evidence file whose rows belong under appendix C."""
    text = DOC.read_text()
    return text + '\n' + EVIDENCE.read_text() if EVIDENCE.is_file() else text

LABELS = ('VERIFIED', 'OBSERVED', 'EXPERIMENTAL', 'INFERRED', 'UNIMPLEMENTED', 'UNKNOWN')
REGISTER_LABELS = ('VERIFIED', 'OBSERVED', 'EXPERIMENTAL')
BASES = {'VERIFIED': ('design',), 'OBSERVED': ('device', 'host'), 'EXPERIMENTAL': ('design',)}
NEEDS_PREMISES = ('VERIFIED', 'OBSERVED', 'EXPERIMENTAL', 'INFERRED')
STANCE = 'This study does not recommend replacing any component of ours; the operator decides.'
SECTIONS = (
    'Key findings', '0. How to read this document', '1. Answers to the seven questions', '2. What the stack is',
    '3. Layer comparison with Snapintosh', '4. Generation delta: A740 to X1-85', '5. Environment delta',
    '6. Reuse paths under AGENTS.md', '7. Licensing and provenance', '8. Mapping onto the plan',
    '9. Open questions and falsifiers', 'Appendix A. Method notes', 'Appendix B. Pinned inputs',
    'Appendix C. Evidence register',
)
SUBSECTIONS = ('0.1', '0.2', '0.3') + tuple('2.%d' % n for n in range(1, 12))
# Numbers and names the study is built around; losing one means a claim was edited away or changed.
HEADLINES = (
    '12 + 5 + 2 = 19', 'Hardware status: NOT RUN', '0x43050a01', '0x43050c01', '0xffff43050a01', '0xffff43050c01',
    'gen70500_gmu.bin', 'gen70500_sqe.fw', 'gen70500_zap.mbn', '4.03.11', '1.62', '0.15', '4192 KiB', '128 buffers',
    '280 of 280', '6712 of 6911', '46 entries', '54 entries', 'BSD-2-Clause-Patent', 'GPL-2.0-or-later',
    'LGPL-3.0-or-later', 'int-070', 'CP_PROTECT',
)

# Key -> (checkout, prefix the register's paths are relative to). A file is looked up under the prefix, then at the root.
PIN_NAMES = {
    'UNX': 'UniXNU', 'A740': 'Adreno740', 'AMET': 'AdrenoMetal', 'AIRNIR': 'AirNir', 'PM4': 'AdrenoPM4',
    'AFW': 'AdrenoFirmware', 'SMMU4K': 'ArmSmmu4K', 'S8550': 'UniXNU-sm8550-common', 'SHENG': 'UniXNU-sheng',
}
UNIXNU = Path(os.environ.get('SP11_UNIXNU_REFS') or REF / 'unixnu')


def tree(variable, name):
    return Path(os.environ.get(variable) or REF / name)


KEYS = {key: (UNIXNU / name, '') for key, name in PIN_NAMES.items()}
KEYS['S8550'] = (UNIXNU / PIN_NAMES['S8550'], 'kits/Sm8550Platform/')
KEYS.update({
    'LNX': (tree('SP11_LINUX_TREE', 'linux-7475ea8b58616b33c04afb976cf0fbaad9dd63fb'), 'drivers/gpu/drm/msm/adreno/'),
    'MESA': (tree('SP11_MESA_TREE', 'mesa-bbeb2dca9feed6ac88d71234a9dc5ae8aeed4275'), 'src/freedreno/'),
    'MESAU': (tree('SP11_MESA_UNIXNU_TREE', 'mesa-85fda3b36405bcaea8bffc64aa4f2651dc90770d'), 'src/freedreno/'),
    'FW': (tree('SP11_FIRMWARE_TREE', 'linux-firmware-69e51bb26f3fa3e118dd770f1962a93bed690dc1'), ''),
    'REPO': (ROOT, ''),
})
NO_CHECKOUT = ('M2V', 'GHAPI')  # a raw-file read of a repository that is not cloned, and a dated API query

TAG = re.compile(r'\[(' + '|'.join(LABELS) + r')((?: U\d{2,3})*)\]')
ANY_TAG = re.compile(r'\[([A-Za-z]+)((?: U\d{2,3})+)\]')
ROW = re.compile(r'^\| (U\d{2,3}) \| (\w+) \| (\w+) \| `([^`]+)` \| `([^`]+)` \| ([^|]+) \|$')
SOURCE = re.compile(r'(?P<key>[A-Z0-9]+) (?P<path>[^\s:]+)(?::(?P<first>\d+)(?:-(?P<last>\d+))?)?')
SEPARATOR = re.compile(r'^\|(?:\s*:?-+:?\s*\|)+$')
Row = collections.namedtuple('Row', 'id label basis key path first last token what')


def section(text, prefix):
    for part in re.split(r'^## ', text, flags=re.M)[1:]:
        if part.startswith(prefix):
            return part
    return ''


def cells(row):
    return [cell.strip() for cell in row.strip().strip('|').split('|')]


def table_rows(block):
    """The body rows of every pipe table in `block`; a header row is the one a separator row follows."""
    lines = block.splitlines()
    for i, line in enumerate(lines):
        if line.startswith('|') and not SEPARATOR.match(line) and not (i + 1 < len(lines) and SEPARATOR.match(lines[i + 1])):
            yield line


def slug(title):
    return re.sub(r'\s', '-', re.sub(r'[^\w\s-]', '', title.replace('`', '').lower()))


def register_rows(text):
    """(rows of appendix C, problems met while parsing them)."""
    rows, found = [], []
    for line in text.split('## Appendix C.', 1)[-1].splitlines():
        if not re.match(r'^\| U\d', line):
            continue
        m = ROW.match(line)
        s = m and SOURCE.fullmatch(m[4])
        if not s:
            found.append('malformed register row: ' + line[:80])
            continue
        first = int(s['first']) if s['first'] else None
        last = int(s['last'] or s['first']) if first else None
        if first and last < first:
            found.append('register row %s has a reversed range' % m[1])
        rows.append(Row(m[1], m[2], m[3], s['key'], s['path'], first, last, m[5], m[6].strip()))
    return rows, found


def problems(text):
    """Everything wrong with the study's own structure, labels, pins and register; [] when it holds together."""
    found = []
    lines = text.splitlines()
    if 'SPDX-License-Identifier: MIT' not in '\n'.join(lines[:2]):
        found.append('SPDX line missing from the first two lines')
    if len(lines) < 2 or not lines[1].startswith('STUDY ONLY'):
        found.append('study-only banner missing on line 2')
    if sorted({c for c in text if not c.isascii()}) not in ([], ['\u00a7']):
        found.append('non-ASCII text other than the section sign')
    for prefix in ('/' + 'Users' + '/', '/' + 'home' + '/'):
        if prefix in text:
            found.append('machine path ' + prefix)
    for word in ('coordinator', 'task brief'):
        if word in text.lower():
            found.append('working-note word in the study: ' + word)
    if STANCE not in text:
        found.append('the stance sentence is missing')
    if tuple(re.findall(r'^## (.+)$', text, re.M)) != SECTIONS:
        found.append('section headings are not the expected ones in order')
    if tuple(re.findall(r'^### (\d+\.\d+) ', text, re.M)) != SUBSECTIONS:
        found.append('subsection numbers are not the expected ones in order')
    for literal in HEADLINES:
        if literal not in text:
            found.append('headline missing: ' + literal)

    rows, bad = register_rows(text)
    found += bad
    labels = {row.id: row.label for row in rows}
    if len(labels) != len(rows):
        found.append('duplicate register ids')
    for row in rows:
        if row.label not in REGISTER_LABELS or row.basis not in BASES.get(row.label, ()):
            found.append('register row %s: label %s with basis %s is not allowed' % (row.id, row.label, row.basis))
        if row.key not in KEYS and row.key not in NO_CHECKOUT:
            found.append('register row %s: unknown source key %s' % (row.id, row.key))

    body = text.split('## Appendix C.', 1)[0]
    cited = set()
    for m in ANY_TAG.finditer(body):
        if m[1] not in LABELS:
            found.append('unknown evidence label [%s]' % m[1])
    for m in TAG.finditer(body):
        ids = m[2].split()
        if m[1] in NEEDS_PREMISES and not ids:
            found.append('[%s] names no register row' % m[1])
        for rid in ids:
            cited.add(rid)
            if rid not in labels:
                found.append('[%s %s] cites a row that is not in the register' % (m[1], rid))
            elif m[1] in REGISTER_LABELS and labels[rid] != m[1]:
                found.append('[%s %s] but the register labels %s %s' % (m[1], rid, rid, labels[rid]))
    if set(labels) - cited:
        found.append('register rows cited by nothing (unused): ' + ', '.join(sorted(set(labels) - cited)))

    for number in range(1, 9):
        for row in table_rows(section(text, '%d. ' % number)):
            if not TAG.search(row):
                found.append('section %d: table row without a label tag: %s' % (number, row[:60]))
    questions = list(table_rows(section(text, '9. ')))
    if len(questions) < 10:
        found.append('section 9 lists fewer than ten open questions')
    for row in questions:
        c = cells(row)
        if len(c) != 5 or not re.fullmatch(r'Q\d\d', c[0]) or c[2] not in ('UNKNOWN', 'INFERRED'):
            found.append('section 9: malformed question row: ' + row[:60])
        elif not re.search(r'\b(X8[a-e]|host|operator)\b', c[4]):
            found.append('section 9: question %s names no gate' % c[0])

    pins = dict(re.findall(r'^\| `([\w./@-]+)` \| `([0-9a-f]{40})` \|$', text, re.M))
    if len(pins) != 20 or not set(PIN_NAMES.values()) <= set(pins):
        found.append('appendix B does not list the 20 pinned inputs')
    keys = {}
    for row in table_rows(text.split('### 0.2', 1)[-1].split('### 0.3', 1)[0]):
        c = cells(row)
        keys[c[0]] = c[1]
        m = re.search(r'`([\w./@-]+)` ([0-9a-f]{8})\b', c[1])
        if m and not pins.get(m[1], '').startswith(m[2]):
            found.append('pin pin mismatch for key %s: section 0.2 says %s, appendix B says %s' % (c[0], m[2], pins.get(m[1])))
        if not m and c[1] != '-':
            found.append('section 0.2: key %s names no pinned input' % c[0])
    if set(keys) != {row.key for row in rows}:
        found.append('citation keys of section 0.2 and of the register differ')

    slugs = {slug(title) for title in re.findall(r'^#{1,6} (.+)$', text, re.M)}
    for target in re.findall(r'\]\(#([^)]+)\)', text):
        if target not in slugs:
            found.append('anchor does not resolve: #' + target)
    for target in re.findall(r'\]\(([^)#:]+\.md)(?:#[^)]*)?\)', text):
        if not (ROOT / 'docs' / target).is_file():
            found.append('link does not resolve: ' + target)
    return found


def cross_problems(text, a7xx, plan):
    """Cross-references into the a7xx study and the plan, which other work edits, so only their existence is checked."""
    found = []
    headings = set(re.findall(r'^#{2,3} (\d+(?:\.\d+)?)\b', a7xx, re.M))
    for number in sorted(set(re.findall(r'a7xx study \u00a7(\d+(?:\.\d+)?)', text))):
        if number not in headings:
            found.append('the a7xx study has no section ' + number)
    for ident in sorted(set(re.findall(r'\b(F[1-8]|X8[a-e]|R1[23]|N2|E\d\d)\b', section(text, '8. ')))):
        pattern = r'\b%s\b' % ident if ident.startswith('X') else r'^\| %s \|' % ident
        if not re.search(pattern, plan, re.M):
            found.append('the plan has no ' + ident)
    return found


def locate(key, rel):
    base, prefix = KEYS[key]
    return next((c for c in (base / prefix / rel, base / rel) if c.is_file()), None)


def audit(rows):
    """(failures, rows checked, rows skipped) of the register against the checkouts that are present."""
    bad, checked, skipped = [], 0, 0
    for row in rows:
        if row.key in NO_CHECKOUT or row.first is None or (row.key != 'REPO' and not KEYS[row.key][0].is_dir()):
            skipped += 1
            continue
        path = locate(row.key, row.path)
        if path is None:
            bad.append('%s: %s %s is not in the checkout' % (row.id, row.key, row.path))
            continue
        checked += 1
        content = path.read_text(errors='replace')
        if row.key == 'REPO' and row.path.endswith('.md'):  # shared documents move; their text is what is cited
            wanted = content
        else:
            wanted = '\n'.join(content.splitlines()[row.first - 1:row.last])
        if row.token not in wanted:
            seen = ' / '.join(line.strip()[:70] for line in wanted.splitlines()[:3])
            bad.append('%s: %r is not in %s %s:%d-%d; those lines read: %s' % (
                row.id, row.token, row.key, row.path, row.first, row.last, seen))
    return bad, checked, skipped


def cited_lines(key, rel, first=None, last=None):
    """The cited lines of a checkout file; the calling test is skipped when that checkout is absent."""
    path = locate(key, rel) if KEYS[key][0].is_dir() else None
    if path is None:
        raise unittest.SkipTest('%s checkout is not present' % key)
    content = path.read_text(errors='replace')
    return content if first is None else '\n'.join(content.splitlines()[first - 1:last])


def mutated(text, pattern, replacement, flags=0):
    changed = re.sub(pattern, replacement, text, count=1, flags=flags)
    assert changed != text, 'seeded bug %r changed nothing' % pattern
    return changed


def first_row_without_tags(text, number):
    block = section(text, '%d. ' % number)
    row = next(table_rows(block))
    return text.replace(row, TAG.sub('', row), 1)


MUTANTS = (
    ('a label tag dropped from a table row', 'label tag', lambda t: first_row_without_tags(t, 3)),
    ('a register label flipped', 'register', lambda t: mutated(t, r'(\| U25 \| )VERIFIED( \| )design', r'\1OBSERVED\2design')),
    ('a pin in appendix B altered', 'pin', lambda t: mutated(t, r'(\| `UniXNU` \| `)d830d23b', r'\1deadbeef')),
    ('a short pin in section 0.2 altered', 'pin', lambda t: mutated(t, r'(`Adreno740` )6fa7bae2', r'\g<1>6fa7bae3')),
    ('a section heading removed', 'section headings', lambda t: mutated(t, r'^## 5\. Environment delta\n', '', re.M)),
    ('a headline number changed', 'headline', lambda t: t.replace('12 + 5 + 2 = 19', '12 + 5 + 2 = 20')),
    ('the stance sentence removed', 'stance', lambda t: t.replace(STANCE, '')),
    ('a machine path added', 'machine path', lambda t: t + '\n' + '/' + 'Users' + '/someone/x\n'),
    ('the SPDX line dropped', 'SPDX', lambda t: t.split('\n', 1)[1]),
    ('non-ASCII text added', 'non-ASCII', lambda t: t.replace('STUDY ONLY -', 'STUDY ONLY \u2014', 1)),
    ('a cited register row deleted', 'not in the register', lambda t: mutated(t, r'^\| U25 \|.*\n', '', re.M)),
    ('an unused register row added', 'unused', lambda t: t + '| U999 | VERIFIED | design | `REPO compat/adreno-ib.c:64` | `x` | spare |\n'),
    ('a dangling anchor added', 'anchor', lambda t: t.replace('## Key findings', '## Key findings\n\n[x](#no-such-section)', 1)),
    ('an unknown evidence label used', 'unknown evidence label', lambda t: mutated(t, r'\[VERIFIED U01', '[PROVEN U01')),
    ('an open question without a gate', 'names no gate', lambda t: mutated(t, r'(\| Q01 \|.*\| )(?:X8[a-e]|host|operator)[^|]*\|', r'\1later |')),
    ('an open question labelled as verified', 'malformed question', lambda t: mutated(t, r'(\| Q02 \| [^|]*\| )(?:UNKNOWN|INFERRED)', r'\1VERIFIED')),
)


class Study(unittest.TestCase):
    text = read_study()

    def test_the_study_holds_together(self):
        self.assertEqual(problems(self.text), [])

    def test_references_into_the_a7xx_study_and_the_plan_resolve(self):
        self.assertEqual(cross_problems(self.text, A7XX.read_text(), PLAN.read_text()), [])

    def test_the_references_into_the_a7xx_study_are_checked(self):
        self.assertTrue(cross_problems('a7xx study \u00a799.9', A7XX.read_text(), PLAN.read_text()))
        self.assertTrue(cross_problems('## 8. x\nE99', A7XX.read_text(), PLAN.read_text()))

    def test_no_build_file_references_the_study_or_its_test(self):
        patterns = ('Makefile', '*/Makefile', '*/*.mk', '*/build*.sh', '*/build*.py', 'tools/build-*')
        for path in (p for pattern in patterns for p in ROOT.glob(pattern) if p.is_file()):
            content = path.read_text(errors='replace')
            for name in ('SP11-GPU-UNIXNU', 'test-sp11-gpu-unixnu'):
                self.assertNotIn(name, content, str(path))


class EvidenceFile(unittest.TestCase):
    """The register lives in its own file: the two files must stay linked, and no row may be left behind in the study file."""
    main = DOC.read_text()
    evidence = EVIDENCE.read_text() if EVIDENCE.is_file() else ''

    def test_the_two_files_link_each_other(self):
        self.assertTrue(EVIDENCE.is_file(), str(EVIDENCE))
        self.assertIn('](SP11-GPU-UNIXNU-EVIDENCE.md)', self.main)
        self.assertIn('](SP11-GPU-UNIXNU.md', self.evidence)

    def test_the_evidence_file_has_its_own_license_and_banner_lines(self):
        head = self.evidence.splitlines()[:2]
        self.assertEqual(len(head), 2)
        self.assertIn('SPDX-License-Identifier: MIT', head[0])
        self.assertTrue(head[1].startswith('STUDY ONLY'), head)

    def test_the_evidence_file_is_plain_text_with_no_machine_strings(self):
        self.assertIn(sorted({c for c in self.evidence if not c.isascii()}), ([], ['\u00a7']))
        for prefix in ('/' + 'Users' + '/', '/' + 'home' + '/'):
            self.assertNotIn(prefix, self.evidence)

    def test_the_evidence_file_is_one_register_table_and_has_no_section_headings(self):
        self.assertEqual(re.findall(r'^#{2,6} .*$', self.evidence, re.M), [])
        table = [line for line in self.evidence.splitlines() if line.startswith('|')]
        self.assertGreater(len(table), 2)
        self.assertEqual(cells(table[0]), ['ID', 'Label', 'Basis', 'Source', 'Token', 'What it shows'])
        self.assertRegex(table[1], SEPARATOR)
        self.assertEqual([row for row in table[2:] if not ROW.match(row)], [])

    def test_the_study_file_holds_no_register_rows(self):
        self.assertEqual([line for line in self.main.splitlines() if ROW.match(line)], [])


class Mutants(unittest.TestCase):
    """Each seeded bug must be rejected by problems(); the unmutated study must pass, or the check proves nothing."""
    text = read_study()

    def test_the_unmutated_study_passes(self):
        self.assertEqual(problems(self.text), [])

    def test_every_seeded_bug_is_caught(self):
        for name, marker, mutate in MUTANTS:
            with self.subTest(name):
                found = problems(mutate(self.text))
                self.assertTrue(any(marker in message for message in found), (marker, found[:3]))


class RegisterAudit(unittest.TestCase):
    rows = register_rows(read_study())[0]

    def test_the_register_is_not_empty(self):
        self.assertGreater(len(self.rows), 100)

    def test_every_row_matches_its_pinned_source(self):
        bad, checked, skipped = audit(self.rows)
        print('register audit: %d rows checked, %d skipped (no checkout or no line range)' % (checked, skipped), file=sys.stderr)
        self.assertEqual(bad, [])

    def test_a_moved_range_is_caught(self):
        moved = [r._replace(first=r.first + 40, last=r.last + 40) for r in self.rows if r.key == 'REPO' and r.path.startswith('compat/')]
        self.assertTrue(moved)
        self.assertEqual(len(audit(moved)[0]), len(moved))

    def test_a_missing_token_is_caught(self):
        row = next(r for r in self.rows if r.key == 'REPO' and r.path.startswith('compat/'))
        self.assertEqual(len(audit([row._replace(token='NOT_A_REAL_TOKEN')])[0]), 1)

    def test_a_missing_file_is_caught(self):
        row = next(r for r in self.rows if r.key == 'REPO' and r.path.startswith('compat/'))
        self.assertEqual(len(audit([row._replace(path='compat/no-such-file.c')])[0]), 1)

    def test_the_checkouts_are_at_their_pins(self):
        pins = dict(re.findall(r'^\| `([\w./@-]+)` \| `([0-9a-f]{40})` \|$', read_study(), re.M))
        present = 0
        for name in PIN_NAMES.values():
            repo = UNIXNU / name
            if not (repo / '.git').exists():
                continue
            present += 1
            head = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
            self.assertEqual(head, pins[name], name)
        if not present:
            self.skipTest('no UniXNU checkout is present')


class DerivedFacts(unittest.TestCase):
    """Counts and absences the study states that no single register row can show; each needs the pinned checkouts."""
    text = read_study()

    def test_the_linux_rsc_and_pdc_writes_add_up_to_the_count_unixnu_checks(self):
        gmu = 'a6xx_gmu.c'
        counts = (cited_lines('LNX', gmu, 717, 732).count('gmu_write_rscc('),
                  cited_lines('LNX', gmu, 741, 745).count('gmu_write_rscc('),
                  cited_lines('LNX', gmu, 798, 800).count('pdc_write('))
        self.assertEqual(counts, (12, 5, 2))
        self.assertEqual(sum(counts), 19)
        self.assertIn('SHENG_GMU_RSC_SETUP_COUNT 19u', cited_lines('SHENG', 'scripts/prepare-sheng-gmu.py', 103, 103))

    def test_the_firmware_names_and_versions_are_the_ones_in_whence(self):
        block = cited_lines('FW', 'WHENCE', 7226, 7237)
        for token in ('qcom/a740_sqe.fw', 'qcom/gmu_gen70200.bin', 'qcom/sm8550/a740_zap.mbn', 'v1.81', 'v4.01.09', 'v0.11',
                      'qcom/gen70500_gmu.bin', 'qcom/gen70500_sqe.fw', 'qcom/x1e80100/gen70500_zap.mbn',
                      'v4.03.11', 'v1.62', 'v0.15'):
            self.assertIn(token, block)

    def test_the_x1_85_catalog_entry_names_no_zap_image_and_the_a740_entry_does(self):
        self.assertNotIn('zapfw', cited_lines('LNX', 'a7xx_catalog.c', 376, 410))
        self.assertIn('zapfw', cited_lines('LNX', 'a7xx_catalog.c', 353, 353))

    def test_the_protect_table_has_48_slots_and_unixnu_counts_46(self):
        self.assertIn('DECLARE_ADRENO_PROTECT(a730_protect, 48)', cited_lines('LNX', 'a7xx_catalog.c', 189, 189))
        self.assertIn('NATIVE_ADRENO_A740_PROTECT_COUNT 46', cited_lines('A740', 'NativeAdrenoA740HwInit.h', 168, 168))

    def test_the_power_up_lists_have_the_sizes_the_study_compares(self):
        catalog = cited_lines('LNX', 'a7xx_catalog.c')
        sizes = {}
        for name in ('a7xx_pwrup_reglist_regs', 'a750_ifpc_reglist_regs', 'a7xx_dyn_pwrup_reglist_regs'):
            body = re.search(r'\b%s\[\] = \{\n(.*?)\n\};' % name, catalog, re.S)[1]
            sizes[name] = sum(1 for line in body.splitlines() if line.lstrip().startswith(('REG_', '{')))
        self.assertEqual(sizes, {'a7xx_pwrup_reglist_regs': 16, 'a750_ifpc_reglist_regs': 58, 'a7xx_dyn_pwrup_reglist_regs': 1})
        unixnu = cited_lines('A740', 'NativeAdrenoPwrupRegs.h')
        entries = re.search(r'NativeAdrenoA740PwrupRegs\[\]=\{\n(.*?)\n\};', unixnu, re.S)[1]
        self.assertEqual(len(re.findall(r'^\s*0x[0-9a-f]+,$', entries, re.M)), 16)

    def test_only_the_x1_85_entry_sets_ifpc_and_both_share_the_clock_gating_and_protect_tables(self):
        a740 = cited_lines('LNX', 'a7xx_catalog.c', 341, 361)
        x185 = cited_lines('LNX', 'a7xx_catalog.c', 376, 397)
        self.assertNotIn('ADRENO_QUIRK_IFPC', a740)
        self.assertNotIn('ifpc_reglist', a740)
        self.assertIn('ADRENO_QUIRK_IFPC', x185)
        self.assertIn('.ifpc_reglist = &a750_ifpc_reglist', x185)
        for block in (a740, x185):
            self.assertIn('.family = ADRENO_7XX_GEN2', block)
            self.assertIn('.hwcg = a740_hwcg', block)
            self.assertIn('.protect = &a730_protect', block)

    def test_the_linux_clock_gating_table_has_the_54_entries_unixnu_counts(self):
        body = re.search(r'\ba740_hwcg\[\] = \{\n(.*?)\n\};', cited_lines('LNX', 'a7xx_catalog.c'), re.S)[1]
        self.assertEqual(sum(1 for line in body.splitlines() if line.lstrip().startswith('{ REG_')), 54)
        self.assertIn('NATIVE_ADRENO_A740_HWCG_COUNT 54', cited_lines('A740', 'NativeAdrenoA740HwInit.h', 118, 118))

    def test_only_the_donor_board_probe_reads_the_generated_protect_and_clock_gating_tables(self):
        # The header ships beside the kernel service, yet no kernel-side source includes it.
        expected = {'A740': 'NativeAdrenoA740HwInit.h', 'SHENG': 'GpuPowerProbe.cpp'}
        users, present = {}, []
        for key in ('A740', 'AMET', 'AIRNIR', 'PM4', 'AFW', 'SMMU4K', 'S8550', 'SHENG'):
            base, prefix = KEYS[key]
            if not base.is_dir():
                continue
            present.append(key)
            for path in sorted((base / prefix).rglob('*')):
                if path.is_file() and '.git' not in path.parts and path.suffix in ('.c', '.cc', '.cpp', '.h', '.hpp', '.m', '.mm'):
                    text = path.read_text(errors='replace')
                    if 'NativeAdrenoA740Protect' in text or 'NativeAdrenoA740Hwcg' in text:
                        users.setdefault(key, []).append(path.name)
        if not present:
            self.skipTest('no UniXNU checkout is present')
        self.assertEqual(users, {key: [name] for key, name in expected.items() if key in present})

    def test_the_kernel_side_kits_handle_no_zap_image(self):
        # The only mention is a comment saying that GMU start-up success does not cover the zap shader.
        expected = {'S8550': [('ShengGmu.h', 1)]}
        checked = 0
        for key in ('A740', 'PM4', 'AFW', 'SMMU4K', 'S8550'):
            base, prefix = KEYS[key]
            if not base.is_dir():
                continue
            checked += 1
            hits = []
            for path in sorted((base / prefix).rglob('*')):
                if path.is_file() and '.git' not in path.parts:
                    count = path.read_text(errors='replace').lower().count('zap')
                    if count:
                        hits.append((path.name, count))
            self.assertEqual(hits, expected.get(key, []), key)
        if not checked:
            self.skipTest('no UniXNU checkout is present')

    def test_nothing_in_the_pinned_tree_ever_sets_the_service_s_gpu_context_flag(self):
        # The identifier occurs once in its declaration (false) and once in the check that refuses the ring step, so the
        # service's own start path cannot reach the point where it publishes.
        hits, checked = [], 0
        for key in PIN_NAMES:
            base, prefix = KEYS[key]
            if not base.is_dir():
                continue
            checked += 1
            for path in sorted(base.rglob('*')):
                if path.is_file() and '.git' not in path.parts and path.suffix in ('.c', '.cc', '.cpp', '.h', '.hpp', '.m', '.mm'):
                    for number, line in enumerate(path.read_text(errors='replace').splitlines(), 1):
                        if 'gpu_context_ready_' in line:
                            hits.append((key, path.name, number))
        if not checked:
            self.skipTest('no UniXNU checkout is present')
        if 'A740' in {key for key in PIN_NAMES if KEYS[key][0].is_dir()}:
            self.assertEqual(hits, [('A740', 'NativeAdrenoService.cpp', 71), ('A740', 'NativeAdrenoService.cpp', 428)])
            service = (KEYS['A740'][0] / 'NativeAdrenoService.cpp').read_text(errors='replace')
            self.assertEqual(len(re.findall(r'(?<![A-Za-z_])ready_=true', service)), 1)

    def test_the_only_caller_of_the_publish_entry_point_is_the_donor_board_probe(self):
        callers, present = [], []
        for key in PIN_NAMES:
            base, prefix = KEYS[key]
            if not base.is_dir():
                continue
            present.append(key)
            for path in sorted(base.rglob('*')):
                if path.is_file() and '.git' not in path.parts and path.suffix in ('.c', '.cc', '.cpp', '.h', '.hpp', '.m', '.mm'):
                    for number, line in enumerate(path.read_text(errors='replace').splitlines(), 1):
                        if re.search(r'\bnative_adreno_publish\s*\(', line) and 'extern' not in line and 'IOReturn native_adreno_publish' not in line:
                            callers.append((key, path.name, number))
        if not present:
            self.skipTest('no UniXNU checkout is present')
        if {'A740', 'SHENG'} <= set(present):
            self.assertEqual(callers, [('SHENG', 'GpuPowerProbe.cpp', 1330)])

    def test_the_two_mesa_pins_agree_on_the_x1_85_rows(self):
        for key, first, last in (('MESAU', 1070, 1074), ('MESA', 1280, 1283)):
            block = cited_lines(key, 'common/freedreno_devices.py', first, last)
            for token in ('GPUId(740)', '0x43050a01', '0xffff43050a01', '0xffff43050c01'):
                self.assertIn(token, block, key)

    def test_the_bridge_builder_lists_exactly_the_translator_patches_the_repository_tracks(self):
        builder, patches = KEYS['UNX'][0] / 'scripts/build-adreno-metal-bridge.py', KEYS['AMET'][0] / 'bridge/patches'
        if not (builder.is_file() and patches.is_dir()):
            self.skipTest('the UniXNU main and AdrenoMetal checkouts are not both present')
        listed = re.findall(r"'(\d{4}-[^']*\.patch)'", builder.read_text())
        self.assertEqual(len(listed), 25)
        self.assertEqual(sorted(listed), sorted(path.name for path in patches.iterdir()))
        diffs = collections.Counter()
        for path in patches.iterdir():
            body = path.read_text(errors='replace')
            self.assertIsNone(re.search(r'licen[cs]e|copyright|spdx', body, re.I), path.name)
            for name in re.findall(r'^diff --git a/(\S+) ', body, re.M):
                diffs['/'.join(name.split('/')[:2])] += 1
        self.assertEqual(dict(diffs), {'crates/metal2vulkan-runtime': 49, 'crates/metal2vulkan': 7,
                                       'crates/metal2vulkan-air': 1, 'docs/FAST-PATHS.md': 2})

    def test_unixnu_tracks_three_accelerator_base_abi_data_files(self):
        data = KEYS['UNX'][0] / 'scripts/data'
        if not data.is_dir():
            self.skipTest('no UniXNU main checkout is present')
        self.assertEqual(sorted(path.name for path in data.glob('ioaccelerator-base-abi*.json')),
                         ['ioaccelerator-base-abi-24A446.json', 'ioaccelerator-base-abi-26C5035r.json',
                          'ioaccelerator-base-abi.json'])


class Hygiene(unittest.TestCase):
    def test_spdx_and_no_machine_strings(self):
        text = Path(__file__).read_text()
        self.assertIn('SPDX-License-Identifier: MIT', '\n'.join(text.splitlines()[:2]))
        for prefix in ('/' + 'Users' + '/', '/' + 'home' + '/'):
            self.assertNotIn(prefix, text)
        self.assertTrue(text.isascii())


if __name__ == '__main__':
    unittest.main(verbosity=1)
