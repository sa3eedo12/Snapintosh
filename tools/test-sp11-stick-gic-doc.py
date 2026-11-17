#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of what docs/SP11-STICK-GIC.md and the comments of the GIC stage's sources say after the third independent review (docs/SP11-STICK-GIC.md sections
4, 9, 10 and 12), monitor port work item 8, pass 6: no model and no build, only text.

    python3 tools/test-sp11-stick-gic-doc.py

Each statement the review made necessary is a NEEDLE that must be in the text, and each statement it found false is a STALE phrase that must not be. A check is a
function of the texts that returns what it found; the tests below show it empty for the real texts and non-empty for a copy with one needle removed or one stale
phrase put back, so that every needle and every phrase is shown to fail. Texts are compared with their whitespace (and the leading star of a block comment)
collapsed, so that a re-wrapped paragraph still matches. The two numbers that the corrected comments state (the longest NEXT WINDOW line, the lines of the
reporter's page) are computed here from the sources and compared with the text. Nothing here says anything about the unit; it proves that the notes say what the
code does and what is not known. No hardware, nothing under /Volumes, no network, no scratch. EXPERIMENTAL; hardware NOT RUN."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = 'docs/SP11-STICK-GIC.md'
GIC_C, GIC_H, DRY_C, RECORD_H, REPORTER_C = ('probes/sp11-stick-gic.c', 'probes/sp11-stick-gic.h', 'probes/sp11-stick-gic-dry.c', 'probes/sp11-stick-gic-record.h',
                                              'probes/sp11-stick-reporter.c')

# name -> [(label, text that must be there)]
NEEDS = {
    DOC: [
        ('the put-back rule', 'that write is NOT made when the put-backs of BOTH ICC_IGRPEN1_EL1 and GICD_CTLR failed'),
        ('what happens instead', 'ISENABLER0 is then only read, its bit is set in FAILED, the foreign interrupts stay disabled and the run ends NOT_RESTORED'),
        ('one gate alone', 'One gate left open alone does not withhold it'),
        ('the limit of the rule', 'a gate found open is not counted'),
        ('the limit is pinned', '`gates-found-open`'),
        ('the rule in a refused run', 'The rule holds in a refused run too'),
        ('the put-back row', 'withheld when both group 1 gates are still open'),
        ('the NOT_RESTORED row', 'ISENABLER0 among them, bit 2'),
        ('the risk', 'not when both group 1 gates stay open'),
        ('the record text', 'The pass 6 reply has the replacement sentence'),
        ('one read at one instant', 'ONE read at ONE instant'),
        ('PMR is unknown', 'whether the register honours PMR is UNKNOWN'),
        ('over-acceptance', 'over-acceptance'),
        ('over-refusal', 'over-refusal'),
        ('specials and LPIs', 'The special INTIDs 1020 to 1022 and an LPI\'s (8192 and up) are refused as foreign'),
        ('the FOREIGN_PENDING row', 'One read at one instant, and whether it honours PMR is UNKNOWN'),
        ('not surveyed, not quarantined', 'neither surveyed nor quarantined'),
        ('unknown whether any is pending', 'whether any is enabled or pending is UNKNOWN'),
        ('the ESPI bits', 'GICD_TYPER 0xF87F051E: ESPI 1 with range 31'),
        ('the PLPIS bit', 'GICR_TYPER low word 0xD0000E7: PLPIS 1'),
        ('the decode is inferred', 'the bit positions are INFERRED, the specification was not re-read'),
        ('the survey row', 'INTIDs 32 to 991 on the unit, 1023 at most; the extended SPIs and the LPIs are not surveyed'),
        ('the NEXT WINDOW width', 'an eight-digit INTID would make it 103, which the line buffer would cut at the interface\'s 100'),
        ('the lines of the reporter', 'at most 28 lines of its 40'),
        ('what is left (the mask)', 'the record in RAM holds BEGUN=R and no quarantine mask'),
        ('what is left (SG_SV_QUAR)', 'the record rule ties the mask to SG_SV_QUAR'),
        ('what is left (RETRIED)', 'RETRIED is set for PPI 26\'s own-bit step'),
        ('a freeze at the first store', 'at that write it does not hold the quarantine\'s mask yet and the reporter prints no QUAR line'),
        ('the new tests (putback)', '`tools/test-sp11-stick-gic-putback.py`'),
        ('the new tests (hppir)', '`tools/test-sp11-stick-gic-hppir.py`'),
        ('the new tests (doc)', '`tools/test-sp11-stick-gic-doc.py`'),
        ('hardware status', 'Hardware status: the stage ran on the unit in boots 12, 13 and 14 (section 12, OBSERVED, one unit, one boot each); the monitor entry, the M1 stop and any guest are NOT RUN.'),
    ],
    GIC_H: [
        ('the contract of the put-back', 'That last write is NOT made when the put-backs of both group 1 gates, ICC_IGRPEN1_EL1 and GICD_CTLR, failed'),
        ('what the arm half holds', 'the stores of the quarantine (first, a member of class R) and of classes G, R and C with their read-back, the check of ICC_HPPIR1_EL1'),
    ],
    GIC_C: [
        ('the rule', 'Except when both group 1 gates failed to go back and are open'),
        ('the gate', 'A gate whose put-back is named in restore_failed was left as the stage set it, which is open.'),
        ('one read', 'it is ONE read at ONE instant (no settle, no second read)'),
        ('PMR is unknown', 'whether ICC_HPPIR1_EL1 honours PMR is UNKNOWN (the model has both)'),
        ('specials and LPIs', 'the special INTIDs 1020 to 1022 and an LPI\'s (8192 and up) are foreign too'),
        ('the survey limit', 'INTIDs 32 to 32 x (ITLinesNumber + 1) - 1, which is 991 on the unit (ITLinesNumber 30) and at most 1023, and no more: the extended SPIs (GICD_TYPER.ESPI, INTIDs 4096 and up) and the LPIs (8192 and up) are neither surveyed nor quarantined'),
        ('the evidence', 'GICD_TYPER 0xF87F051E: ESPI 1 with range 31; GICR_TYPER low word 0xD0000E7: PLPIS 1; the decode is INFERRED'),
        ('unknown whether any is pending', 'whether any of them is enabled and pending there is UNKNOWN'),
        ('the SGIs', 'the enable bits of the other SGIs and PPIs of this CPU\'s redistributor, SGI 0 and PPIs 23, 25, 27 and 30 on the unit'),
        ('the R store', 'this store sets no other bit of ISENABLER0'),
    ],
    DRY_C: [
        ('the width of the NEXT WINDOW line', 'line is at most 99 characters with its prefix for the values that can reach it'),
        ('the unreachable case', 'would make the line 103 characters'),
    ],
    RECORD_H: [('the lines of the reporter', 'At most 28 lines when every one applies')],
    REPORTER_C: [('the lines of the reporter (head)', 'at most 28 more lines'), ('the lines of the reporter (decode)', 'At most 28 lines.')],
}

# name -> [(label, text that the review found false and that must not come back)]
STALE = {
    DOC: [('every line is at most 100 characters', 'writes both pages; every line is at most 100 characters')],
    DRY_C: [('at most 100 characters for any value', 'for any values the keys can print')],
    RECORD_H: [('at most 27 lines', 'At most 27 lines')],
    REPORTER_C: [('at most 23 lines (head)', 'at most 23 more lines'), ('at most 23 lines (decode)', 'At most 23 lines')],
    GIC_H: [('classes G, R and C only', 'the stores of classes G, R and C with their read-back, PREPARED, and the exact put-back of the same classes')],
    GIC_C: [('no other bit is ever set', 'no other bit of ISENABLER0 is ever set by the stage'),
            ('the only bit it can have set', 'so that bit is the only one it can have set'),
            ('the other PPIs, SGI 0 omitted', 'the other PPIs of this CPU\'s redistributor and writes nothing else of theirs'),
            ('the other PPIs\' priority byte', 'The other PPIs: the priority byte of each INTID')],
}


def flat(text):
    """The text with every run of whitespace, and the lone star that starts a line of a block comment, as one space."""
    return ' '.join(token for token in text.split() if token != '*')


def problems(texts):
    """What is missing or stale in {name: flat text}, as 'name: missing label' and 'name: stale label'."""
    found = [f'{name}: missing {label}' for name, needles in NEEDS.items() for label, needle in needles if needle not in texts[name]]
    return found + [f'{name}: stale {label}' for name, phrases in STALE.items() for label, phrase in phrases if phrase in texts[name]]


class Statements(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.texts = {name: flat((ROOT / name).read_text()) for name in {*NEEDS, *STALE}}

    def test_the_real_texts_have_every_statement_the_review_needs_and_none_it_found_false(self):
        self.assertEqual(problems(self.texts), [])

    def test_a_copy_without_one_needed_statement_is_refused_for_each_of_them(self):
        for name, needles in NEEDS.items():
            for label, needle in needles:
                with self.subTest(name=name, label=label):
                    self.assertIn(needle, self.texts[name], 'the needle is in the real text')
                    copy = {**self.texts, name: self.texts[name].replace(needle, '')}
                    self.assertIn(f'{name}: missing {label}', problems(copy))

    def test_a_copy_with_a_stale_statement_put_back_is_refused_for_each_of_them(self):
        for name, phrases in STALE.items():
            for label, phrase in phrases:
                with self.subTest(name=name, label=label):
                    copy = {**self.texts, name: self.texts[name] + ' ' + phrase}
                    self.assertIn(f'{name}: stale {label}', problems(copy))

    def test_the_pass_6_paragraph_names_no_hardware_result_and_carries_its_labels(self):
        text = self.texts[DOC]
        pass6 = text.split('Pass 6 (a third independent review of pass 5;')[1].split('Change requests (the frozen files are untouched)')[0]
        for label in ('EXPERIMENTAL', 'UNKNOWN', 'INFERRED'):
            self.assertIn(label, pass6)
        self.assertNotRegex(pass6, r'(?i)hardware (?:pass|qualified|validated|ok)\b')
        self.assertNotIn('OBSERVED', pass6, 'pass 6 states no new observation of the unit')


class Numbers(unittest.TestCase):
    def test_the_longest_next_window_line_is_99_characters_for_the_values_that_reach_it_and_103_for_an_eight_digit_intid(self):
        body = (ROOT / DRY_C).read_text().split('static void gic_say_next_window')[1].split('\n}\n')[0]
        keys = re.findall(r'(sg_key_h32|sg_key_h8|sg_key_bit|sc_key_dec)\([^"\n]*"( [A-Z0-9]+=)"', body)
        self.assertEqual([key for _, key in keys], [' GICD=', ' ISEN=', ' PRIO=', ' PMR=', ' IGRPEN1=', ' HPPIR='], 'the keys of the line: a new key needs the numbers worked out again')
        self.assertIn('pl_text(&l, "NEXT GIC WINDOW ");', body)
        width = {'sg_key_h32': len('0XFFFFFFFF'), 'sg_key_h8': len('0XFF'), 'sg_key_bit': 1}

        def longest(intid_digits):
            return len('SNAPMON NEXT GIC WINDOW 2 S' + ''.join(key + '0' * (width.get(fn) or intid_digits) for fn, key in keys))

        self.assertEqual((longest(4), longest(8)), (99, 103))
        line_max = int(re.search(r'#define SP11SB_LINE_MAX (\d+)u', (ROOT / 'probes/sp11-stick-bundle.h').read_text()).group(1))
        self.assertEqual(line_max, 100)
        self.assertLessEqual(longest(4), line_max)
        self.assertGreater(longest(8), line_max)

    def test_the_record_has_29_line_ids_in_the_reporters_order_and_irq_and_diag_exclude_each_other_so_28_at_most(self):
        text = (ROOT / RECORD_H).read_text()
        ids = [i.strip() for i in re.sub(r'/\*.*?\*/', '', re.search(r'enum sg_line_id \{(.*?)\};', text, re.S).group(1), flags=re.S).split(',') if i.strip()]
        self.assertEqual(ids[-1], 'SG_L_COUNT')
        order = re.findall(r'SG_L_[A-Z0-9]+', re.search(r'#define SG_REPORTER_ORDER \{(.*?)\}', text, re.S).group(1))
        self.assertEqual((len(ids) - 1, sorted(order) == sorted(ids[:-1])), (29, True))
        irq, diag = (re.search(rf'case {name}:\n(.*?)\n    case ', text, re.S).group(1) for name in ('SG_L_IRQ', 'SG_L_DIAG'))
        self.assertIn('if (!SG_WIN_U32(b, SG_WIN_EL2, irq_entries)) return 0;', irq)
        self.assertIn('if (SG_WIN_U32(b, SG_WIN_EL2, irq_entries) ||', diag)
        self.assertEqual(len(ids) - 1 - 1, 28)


if __name__ == '__main__':
    unittest.main()
