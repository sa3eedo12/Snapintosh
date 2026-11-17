#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""docs/SP11-RETENTION-PROBE.md against the tools it describes, and the same agreement tests against seeded mistakes in a copy of the document.

    python3 tools/test-sp11-retention-doc.py

What it holds the document to: the house rules (the licence line, the size limit, ASCII, no machine identifier, the evidence labels, hardware NOT
RUN, no run authorised); the links and anchors; the Shell script block of the extract step, the operator's actions and the verdict tool's report
against what the generator, the plan builder and the tool print; the list of named refusals against the tool's source; the unit's pages and the
conventional extents the document quotes against the six local memory maps (SKIP when they are absent); the reset ids, the plan names, the
unattended line and the stager's option; the numbers the document gives (the series rule, the threshold of a lost header, the count of seeded bugs,
the count of outcomes) and the test files it names. Writes only under build/. Hardware: NOT RUN (this checks a document)."""
import importlib.util
import os
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = Path(os.environ.get('SP11_RETENTION_DIR') or Path(__file__).resolve().parent)
DOC = ROOT / 'docs' / 'SP11-RETENTION-PROBE.md'
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(TOOLS))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


autorun = load('sp11_autorun', TOOLS / 'sp11_autorun.py')
import sp11_retention as R  # noqa: E402
import sp11_retention_check as C  # noqa: E402
import sp11_retention_plan as P  # noqa: E402
TC = load('retention_check_tests_for_doc', Path(__file__).resolve().parent / 'test-sp11-retention-check.py')
MUTANT_LIST = load('retention_mutants_for_doc', Path(__file__).resolve().parent / 'test-sp11-retention-mutants.py').MUTANTS
LABELS = ('VERIFIED', 'OBSERVED', 'INFERRED', 'EXPERIMENTAL', 'UNIMPLEMENTED', 'UNKNOWN')
MAPS = sorted((ROOT / 'downloads' / 'sp11').glob('x*-run*/memmap.txt'))


def block(text, name):
    return text[text.index(f'<!-- {name} -->') + len(name) + 9:text.index(f'<!-- /{name} -->')]


def fenced(text):
    return [line for line in text.strip().splitlines() if line != '```']


def slug(heading):
    return re.sub(r'[^a-z0-9 _-]', '', heading.lower()).replace(' ', '-')


def rows(section):
    return [[cell.strip() for cell in line.strip().strip('|').split('|')] for line in section.splitlines()
            if line.startswith('|') and not re.match(r'^\|[ \-|]+\|$', line)][1:]


class RetentionDocAgreement(unittest.TestCase):
    TEXT = None     # a changed copy of the document, for RetentionDocMutants

    @classmethod
    def setUpClass(cls):
        if not DOC.exists():
            raise unittest.SkipTest('docs/SP11-RETENTION-PROBE.md does not exist')
        cls.text = DOC.read_text(encoding='utf-8') if cls.TEXT is None else cls.TEXT
        cls.flat = ' '.join(cls.text.split())

    def says(self, phrase):
        self.assertIn(' '.join(phrase.split()), self.flat, f'the document does not say {phrase!r}')

    def test_the_document_follows_the_house_rules(self):
        self.assertEqual(self.text.splitlines()[0], '<!-- SPDX-License-Identifier: MIT -->')
        self.assertLess(len(self.text.splitlines()), 1500)
        self.assertLess(len(self.text.encode('utf-8')), 50 * 1024)
        self.assertTrue(self.text.isascii())
        for label in LABELS:
            self.assertIn(label, self.text)
        self.says('Evidence labels as in the other SP11 documents: VERIFIED, OBSERVED, INFERRED, EXPERIMENTAL, UNIMPLEMENTED, UNKNOWN.')
        self.assertNotIn('TODO', self.text)
        self.says('**EXPERIMENTAL; hardware NOT RUN.** Nothing in this document, in the tools and tests it names, or in the fact that files are staged on a '
                  "stick authorises a run: each run needs the operator's written approval of a record (section 9).")
        paths = '|'.join(['/Us' + 'ers/', '/ho' + 'me/', '/Vol' + 'umes/'])
        pattern = re.compile(paths + r'|\.loc' + r'al\b|\bsae' + r'ed\b|host' + r'name|\b[0-9a-f]{2}(?::[0-9a-f]{2}){5}\b', re.I)
        for number, line in enumerate(self.text.splitlines(), 1):
            self.assertIsNone(pattern.search(line), f'{number}: {line}')

    def test_the_links_resolve_and_the_headings_the_text_cites_exist(self):
        for target in re.findall(r'\]\(([^)]+)\)', self.text):
            if re.match(r'[a-z][a-z0-9+.-]*:', target):
                continue
            path, _, fragment = target.partition('#')
            other = ROOT / 'docs' / path if path else DOC
            self.assertTrue(other.is_file(), f'{target}: no such file')
            if fragment:
                headings = [slug(h) for h in re.findall(r'^#+ (.+)$', self.text if not path else other.read_text(encoding='utf-8'), re.M)]
                self.assertIn(fragment, headings, target)
        for number in range(1, 14):
            self.assertRegex(self.text, rf'(?m)^## {number}\. ', f'section {number}')
        self.says('(section 9)')
        self.assertTrue((ROOT / 'docs' / 'SP11-HOST-LINK-STUDY.md').is_file())

    def test_the_extract_block_is_what_the_generator_writes_and_the_checker_accepts(self):
        built = autorun.build_plan('retention-m10', [f'SLBOUNCE-DEVHE-MARK.EFI {autorun.RUNNER} --el2', 'RETENTION-EXTRACT 0x9C000000 0xF8400000 0xFFFF00000'],
                                   {'SLBOUNCE-DEVHE-MARK.EFI': 'a' * 64, autorun.RUNNER: '8' * 64})
        lines = autorun.script_lines(built)
        shown = fenced(block(self.text, 'retention-extract'))
        self.assertEqual(shown, lines[lines.index(':step2'):lines.index(':done')])
        self.assertEqual(autorun.script_problems('\r\n'.join(lines) + '\r\n'), [])
        first = next(i for i, line in enumerate(shown) if line.startswith('dmem '))
        self.assertTrue(all(line.strip().startswith(('if ', 'goto ', 'endif', 'echo "AUTORUN REFUSED', ':step2')) for line in shown[:first]))
        self.says('Between the guards and the first `dmem` there is no stall, no `load`, no run, no marker and no console line')
        self.says('`extract  RETENTION-EXTRACT 0x9C000000 0xF8400000 0xFFFF00000`')
        self.assertIn('  extract  ' + built.steps[1].run_line, autorun.render_plan_text(built).decode('ascii').split('\r\n'), 'the plan text spells it so')

    def test_the_operators_actions_are_what_the_builder_prints_and_there_are_two(self):
        shown = '\n'.join(fenced(block(self.text, 'retention-actions'))) + '\n'
        self.assertEqual(shown, P.operator_actions('retention-m10', 'psci-reset', 8, False))
        self.assertEqual(len(re.findall(r'^ACTION [0-9]:', shown, re.M)), 2)
        self.says('the plan `retention-m10` has two operator actions and no photograph')
        self.says('Whether the Surface firmware honours a key held at a restart (as it does at power-on) is UNKNOWN')

    def test_the_report_is_what_the_tool_prints_for_the_result_it_describes(self):
        pages = TC.whole()
        pages[1] = TC.flip(pages[1], 300)
        pages[2] = bytes(4096)
        owned = [TC.MAP_ROWS[0], TC.MAP_ROWS[1], 'BS_Data    0000000FFFF00000-0000000FFFFFFFFF 0000000000000100 0000000000000008']
        report = C.judge(C.parse_dump(TC.text(TC.dump_lines(pages, rows=owned))), R.config_bytes(1, 8, TC.PAGES))
        self.assertEqual('\n'.join(fenced(block(self.text, 'retention-report'))), C.format_report(report, 'RET2.TXT'))

    def test_the_named_refusals_are_the_tools(self):
        listed = re.findall(r'`([A-Z_0-9]+)`', block(self.text, 'retention-reasons'))
        source = (TOOLS / 'sp11_retention_check.py').read_text(encoding='utf-8')
        raised = set(re.findall(r"DumpError\('([A-Z_0-9]+)'", source))
        self.assertEqual(sorted(listed), sorted(raised))
        self.assertEqual(len(listed), len(set(listed)))
        self.says('with a named reason and exit status 2')

    def test_the_series_rule_the_thresholds_and_the_counts_are_the_tools(self):
        self.assertEqual(C.SERIES_PASS_TRIALS, 10)
        self.says('a path passes when a page is whole in ten consecutive trials')
        self.assertEqual(R.MIN_BODY_MATCHES, 16)
        self.says('at least 16 words of one write survive')
        self.says(f'{len(MUTANT_LIST)} seeded bugs in the modules above')
        outcomes = rows(block(self.text, 'retention-outcomes'))
        self.assertEqual(len(outcomes), 11)
        self.assertTrue(all(len(row) == 3 and all(row) for row in outcomes))
        self.says('Path `NONE` (a power cycle) says WHOLE')
        self.assertEqual(R.COUNTDOWN_MAX, 60)
        self.assertEqual(P.HOLD_SECONDS, 60)

    def test_the_unit_pages_the_reset_ids_the_plans_and_the_flag_are_the_tools(self):
        table = rows(self.text[self.text.index('| Page | Address |'):self.text.index('Three pages in three places')])
        self.assertEqual([row[1] for row in table], [f'`0x{page:X}`' for page in R.UNIT_PAGES])
        self.assertEqual(P.RESET_IDS, {'psci-reset': ('SYSTEM_RESET', 0x84000009), 'psci-reset2': ('SYSTEM_RESET2', 0xC4000012)})
        self.says('`PSCI_RESET` (`SYSTEM_RESET`, `0x84000009`) and `PSCI_RESET2` (`SYSTEM_RESET2`, `0xC4000012`')
        self.assertEqual((autorun.RETENTION_PLAN, autorun.RETENTION_UNATTENDED_PLAN), ('retention-m10', 'retention-m10-auto'))
        self.says('`unattended: stick first in the boot order, authorised by the operator (this tool changes no firmware setting)`')
        self.assertEqual(autorun.UNATTENDED_LINE, 'unattended: stick first in the boot order, authorised by the operator (this tool changes no firmware setting)')
        self.assertIn("'--stick-first-authorised'", (TOOLS / 'stage-sp11-boot-media.py').read_text(encoding='utf-8'))
        self.says('`--stick-first-authorised`')
        self.says('0x1000')

    def test_the_conventional_extents_the_document_quotes_are_in_every_local_map(self):
        if len(MAPS) != 6:
            self.skipTest('the six local memory maps (downloads/sp11/x*-run*/memmap.txt) are not all here')
        self.says('the 339 MiB extent `0x91480000` to `0xA6800000`')
        self.says('(`0xE69C0000` to `0xF8500000`)')
        for path in MAPS:
            extents = R.parse_probe_memmap(path.read_text(encoding='ascii'))
            starts = {e.start: e for e in extents if e.conventional}
            self.assertEqual(starts[0x91480000].end, 0xA6800000, path.parent.name)
            self.assertEqual(starts[0xE69C0000].end, 0xF8500000, path.parent.name)
            self.assertEqual((starts[0x880000000].end), 0x1000000000, path.parent.name)
            self.assertEqual(R.page_problems(R.UNIT_PAGES, extents), [])

    def test_the_record_and_the_files_the_document_names_are_there(self):
        record = P.authorisation_record('retention-m10', 'ab' * 32, 12748, 'cd' * 32, 'ef' * 32, 624, 'psci-reset', 8, R.UNIT_PAGES)
        for phrase in ('The UEFI Shell only', 'No code at EL2 writes the stick', 'The internal disk and its partition table, NVRAM and UEFI variables',
                       'a second payload in the same boot', 'Approved by the operator for one run', 'PAYLOAD.MAN, 624 bytes, SHA-256 ' + 'ef' * 32):
            self.assertIn(phrase, record)
        self.says('the payload, the configuration and the payload manifest by size and SHA-256')
        self.says('the internal disk and its partition table, NVRAM and UEFI variables, any firmware setting, any device register, any clock or voltage')
        self.says('what the payload reads (the guarded loads, the plain ones that nothing guards, the registers, `PAR_EL1` as the one it writes)')
        self.says('the power button answers a surprise before the reset call and danger at any time, and not a dark screen after it')
        for command in re.findall(r'python3 (tools/[a-z0-9_-]+\.py)', block(self.text, 'retention-tests') if '<!-- retention-tests -->' in self.text
                                  else self.text[self.text.index('## 12. Tests'):self.text.index('## 13.')]):
            self.assertTrue((ROOT / command).is_file(), command)
        for name in ('sp11_retention.py', 'sp11_retention_check.py', 'sp11_retention_plan.py', 'sp11_autorun.py', 'stage-sp11-boot-media.py'):
            self.assertTrue((ROOT / 'tools' / name).is_file(), name)
            self.assertIn(f'`tools/{name}`', self.text)

    def test_the_unattended_line_is_a_record_and_the_checks_of_a_series_and_a_stick_are_the_tools(self):
        self.says("That line is a **record of the operator's decision, not an interlock**")
        for name in ('sp11_autorun.py', 'sp11_retention_plan.py'):
            self.assertIn('not an interlock', ' '.join((TOOLS / name).read_text(encoding='utf-8').split()), name)
        self.says('**A series counts trials, and only trials it can judge.**')
        self.says('`series` refuses (`SERIES_FILE_REPEATED`) a file that is byte for byte one it has already read')
        self.says('(`SERIES_TRIAL_REPEATED`) a file whose whole page carries the boot counter and the timestamp of one it has read')
        self.says('It refuses (`NO_MEMORY_MAP`) a file with no memory map')
        self.says('A file whose whole pages carry different boot counters (a stale page beside new ones) warns, and is not kept either.')
        self.says('`stick` prints both and the file times of `DONE1.TXT`, `RET2.TXT` and `DONE2.TXT`')
        self.says('a `DONE1.TXT` that is not later than the staging (`STALE_STEP_MARKER`')
        self.says('a result whose own clock is not later than the staging (`STALE_RESULT`')
        self.assertEqual(C.FILE_TIME_SLACK_SECONDS, 10)
        self.says('(`staged: 2026-11-17 05:20:33`, for the retention plans only; no other plan\'s `PLAN.TXT` changes)')
        self.assertEqual(autorun.STAGED_FORMAT, '%Y-%m-%d %H:%M:%S')
        self.assertEqual(autorun.RETENTION_DATA_NAMES, ('PAYLOAD.MAN', 'RETPROBE.BIN', 'RETPATH.DAT'))
        self.says('a retention plan stages those three data names and no others')
        self.says('`SYSTEM_RESET` is the architectural cold-reset call and the warm reset is `SYSTEM_RESET2`')
        self.says('`DC CVAC` and `DC CIVAC` reach the point of coherency and no further')
        self.says("It writes no system register but `PAR_EL1`")
        self.says("`AT S1E2R` and `AT S1E2W` show that EL2's stage 1 maps the page, not that TrustZone, an XPU or QHEE leave the physical page alone")


def _pattern(old):
    return re.compile(r'\s+'.join(re.escape(part) for part in old.split()))


def _replace(old, new, count=1):
    pattern = _pattern(old)

    def mutate(doc):
        assert pattern.search(doc), old
        return pattern.sub(lambda found: new, doc, count)
    return mutate


def _in(block_name, old, new):
    pattern = _pattern(old)

    def mutate(doc):
        start, end = doc.index(f'<!-- {block_name} -->'), doc.index(f'<!-- /{block_name} -->')
        assert pattern.search(doc[start:end]), old
        return doc[:start] + pattern.sub(lambda found: new, doc[start:end], 1) + doc[end:]
    return mutate


DOC_MUTANTS = [
    ('the-licence-line-goes', lambda doc: doc.split('\n', 1)[1]),
    ('hardware-is-called-run', _replace('**EXPERIMENTAL; hardware NOT RUN.**', '**EXPERIMENTAL.**')),
    ('a-run-is-authorised', _replace('authorises a run', 'authorises the first run')),
    ('a-label-goes', _replace('UNIMPLEMENTED, UNKNOWN. Nothing about', 'UNKNOWN. Nothing about')),
    ('the-extract-block-gets-a-stall', _in('retention-extract', 'dmem 0x9C000000 0x1000 >a SP11\\RET2.TXT', 'stall 5000000\ndmem 0x9C000000 0x1000 >a SP11\\RET2.TXT')),
    ('the-first-dump-overwrites-nothing', _in('retention-extract', 'dmem 0xF8400000 0x1000 >>a', 'dmem 0xF8400000 0x1000 >a')),
    ('the-marker-moves', _in('retention-extract', 'echo "AUTORUN retention-m10 STEP 2 EXTRACTED" >a SP11\\DONE2.TXT\n', '')),
    ('a-page-address-changes-in-the-block', _in('retention-extract', 'dmem 0xFFFF00000 0x1000', 'dmem 0xFFFF00000 0x2000')),
    ('the-action-says-to-power-cycle', _in('retention-actions', 'Do NOT\n  power-cycle', 'Do\n  power-cycle')),
    ('the-action-forgets-the-hold', _in('retention-actions', 'HOLD Volume-Down and keep holding it', 'press nothing')),
    ('the-countdown-changes', _in('retention-actions', 'NEXT RESET AFTER 8 S.', 'NEXT RESET AFTER 5 S.')),
    ('the-report-changes', _in('retention-report', 'counter 4, CRC ok', 'counter 5, CRC ok')),
    ('the-damaged-bytes-change', _in('retention-report', 'damaged bytes 0x12C-0x130', 'damaged bytes 0x12C-0x134')),
    ('a-refusal-is-forgotten', _in('retention-reasons', '`BLOCK_SHORT`, ', '')),
    ('a-refusal-is-invented', _in('retention-reasons', '`BLOCK_LONG`,', '`BLOCK_LONG`, `BLOCK_WIDE`,')),
    ('the-series-needs-nine', _replace('whole in ten consecutive trials', 'whole in nine consecutive trials')),
    ('the-lost-header-threshold-changes', _replace('at least 16 words of one write survive', 'at least 17 words of one write survive')),
    ('the-mutant-count-changes', _replace(f'{len(MUTANT_LIST)} seeded bugs in the modules above', f'{len(MUTANT_LIST) - 1} seeded bugs in the modules above')),
    ('an-outcome-row-goes', _in('retention-outcomes', '| Path `NONE` (a power cycle) says WHOLE | a surprise: DRAM survives even a power cycle, or the pipeline is wrong | keep the file; repeat before believing it |\n', '')),
    ('a-page-address-changes', _replace('| 0 | `0x9C000000` |', '| 0 | `0x9C000008` |')),
    ('the-reset-id-changes', _replace('`SYSTEM_RESET`, `0x84000009`', '`SYSTEM_RESET`, `0x84000008`')),
    ('the-unattended-line-changes', _replace('`unattended: stick first in the boot order, authorised by the operator (this tool changes no firmware setting)`',
                                             '`unattended: stick first in the boot order`')),
    ('the-flag-is-misnamed', _replace('`--stick-first-authorised`', '`--stick-first`', count=0)),
    ('the-extent-changes', _replace('the 339 MiB extent `0x91480000` to `0xA6800000`', 'the 339 MiB extent `0x91480000` to `0xA6900000`')),
    ('the-guard-sentence-goes', _replace('Between the guards and the first `dmem` there is no stall, no `load`, no run, no marker and no console line',
                                         'Between the guards and the first `dmem` there is a pause')),
    ('the-record-sentence-changes', _replace('the internal disk and its partition table, NVRAM and UEFI variables', 'the internal disk')),
    ('a-test-file-is-misnamed', _replace('python3 tools/test-sp11-retention-qemu.py', 'python3 tools/test-sp11-retention-qemu2.py')),
    ('a-section-goes', _replace('## 11. If retention fails: the fallbacks', '## 11b. If retention fails: the fallbacks')),
    ('a-link-is-broken', _replace('(SP11-HOST-LINK-STUDY.md)', '(SP11-HOST-LINK-STUDYX.md)')),
    ('an-anchor-is-broken', _replace('SP11-STICK-LOG.md)', 'SP11-STICK-LOG.md#no-such-heading)')),
    ('a-machine-identifier-appears', _replace('# SP11 DRAM retention probe M10', '# SP11 DRAM retention probe M10 at /Us' + 'ers/x')),
    ('the-unattended-line-is-called-an-interlock', _replace("a **record of the operator's decision, not an interlock**", 'an **interlock**')),
    ('a-series-is-said-to-count-files', _replace('**A series counts trials, and only trials it can judge.**', '**A series counts files.**')),
    ('a-repeated-file-is-said-to-be-a-trial', _replace('`series` refuses (`SERIES_FILE_REPEATED`) a file that is byte for byte one it has already read', '`series` counts a file that is byte for byte one it has already read')),
    ('a-file-with-no-map-is-said-to-count', _replace('It refuses (`NO_MEMORY_MAP`) a file with no memory map', 'It counts a file with no memory map')),
    ('a-stale-marker-is-said-to-be-accepted', _replace('a `DONE1.TXT` that is not later than the staging (`STALE_STEP_MARKER`', 'a `DONE1.TXT` of any time (`STALE_STEP_MARKER`')),
    ('the-staging-time-is-said-to-go-to-every-plan', _replace("for the retention plans only; no other plan's `PLAN.TXT` changes", 'for every plan')),
    ('another-data-name-is-allowed', _replace('a retention plan stages those three data names and no others', 'a retention plan stages any data names')),
    ('the-reset-is-called-the-warm-one', _replace('`SYSTEM_RESET` is the architectural cold-reset call and the warm reset is `SYSTEM_RESET2`',
                                                 '`SYSTEM_RESET` is the architectural warm-reset call and the cold reset is `SYSTEM_RESET2`', count=0)),
    ('the-clean-is-said-to-reach-dram', _replace('`DC CVAC` and `DC CIVAC` reach the point of coherency and no further', '`DC CVAC` and `DC CIVAC` reach DRAM')),
    ('the-register-it-writes-is-forgotten', _replace('It writes no system register but `PAR_EL1`', 'It writes no system register')),
    ('the-record-description-forgets-the-manifest', _replace('the payload, the configuration and the payload manifest by size and SHA-256', 'the payload and the configuration by size and SHA-256')),
    ('the-power-button-answers-a-dark-screen', _in('retention-actions', 'The power button is for the end of the run (step 7) and for danger', 'Hold the power button')),
    ('the-actions-forget-the-stale-marker', _in('retention-actions', 'A marker left by an earlier run does harm', 'A marker left by an earlier run is harmless')),
    ('a-stale-reason-is-forgotten', _in('retention-reasons', '`STALE_STEP_MARKER` and `STALE_RESULT`', '`STALE_RESULT`')),
    ('the-extract-block-forgets-the-clock', _in('retention-extract', 'date >>a SP11\\RET2.TXT\ntime >>a SP11\\RET2.TXT\n', '')),
    ('the-report-forgets-the-clock', _in('retention-report', "extracted: 11/17/2026 01:20:58 (LOCAL), the unit's clock as its Shell printed it\n", '')),
    ('the-document-is-too-big', lambda doc: doc + '\n' + 'x' * 52000),
]


class RetentionDocMutants(unittest.TestCase):
    """The agreement tests above must be able to fail: each seeded mistake in the document is refused by one."""

    @staticmethod
    def outcome(text):
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(type('Changed', (RetentionDocAgreement,), {'TEXT': text}))
        result = unittest.TestResult()
        suite.run(result)
        return result

    def test_the_unchanged_document_passes_through_the_same_mechanism(self):
        result = self.outcome(DOC.read_text(encoding='utf-8'))
        self.assertTrue(result.wasSuccessful(), [str(f[1])[-500:] for f in result.failures + result.errors])
        self.assertGreaterEqual(result.testsRun, 9)

    def test_every_seeded_mistake_in_the_document_is_refused(self):
        original = DOC.read_text(encoding='utf-8')
        self.assertGreaterEqual(len(DOC_MUTANTS), 30)
        self.assertEqual(len({name for name, _ in DOC_MUTANTS}), len(DOC_MUTANTS))
        for name, mutate in DOC_MUTANTS:
            with self.subTest(mutant=name):
                changed = mutate(original)
                self.assertNotEqual(changed, original)
                self.assertFalse(self.outcome(changed).wasSuccessful(), 'every agreement test still passed')


if __name__ == '__main__':
    unittest.main()
