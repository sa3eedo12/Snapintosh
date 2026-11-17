#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of the authorisation record of point A1 for the xHCI S1 test (tools/sp11_xhci_s1_record.py, docs/SP11-XHCI-S1.md). Host only: nothing here boots,
stages, writes outside build/, or authorises a run.

    python3 tools/test-sp11-xhci-s1-record.py                        # the text, its digest, its rules
    Q1N1_SP11_TREE=<q1n1 checkout> python3 tools/test-sp11-xhci-s1-record.py   # also: every number of the text against the payload's own header

* the text is deterministic, ASCII and within 120 columns, and its SHA-256 for fixed example files is pinned (a change of a word changes the pin on purpose);
* the registers of the text are the thirteen of the payload's header, with the same offsets and groups, which the q1n1 host test holds to its own
  independent audit; the numbers of the text (waits, caps, arenas, timeouts) are the header's, and the numbers of its line budget (how many SMMU
  reports fit, when a success says LINES_CUT) are those of the host model's `edges` suite (tools/sp11_xhci_s1_budget_check.py);
* the text says what A1 covers and what it does not, the hazards, the stop rules and the budget; each rule of that check has a faulty text it must reject;
* the inputs are refused when wrong (one or two payloads, XHCI1.BIN first, a SHA-256 of 64 lower-case hex digits, a size; the runbook with its size and
  SHA-256, which the text names in its section 1);
* the runbook is pinned: the generator reads the file and hashes its bytes, `--runbook` names another file and defaults to the repository's own, a changed
  byte of the runbook changes the text, and a generator that ignores the runbook, hard-codes its digest or ignores `--runbook` is caught (mutated copies
  of the generator under build/ must fail the pin check; an unmutated copy must pass it);
* the command line (`tools/sp11_xhci_s1_record.py --xhci1 <file> [--xhci0 <file>] [--runbook <file>] [--digest]`) prints the text and the digest of the files
  given, and refuses a missing, empty or oversized file.
The q1n1 checks SKIP, and say so, without Q1N1_SP11_TREE.
"""
from pathlib import Path
import contextlib
import hashlib
import importlib.util
import io
import os
import re
import shutil
import sys
import unittest

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(TOOLS))
import sp11_xhci_s1_record as rec  # noqa: E402
from sp11_xhci_s1_budget_check import budget_problems  # noqa: E402

TREE = os.environ.get('Q1N1_SP11_TREE')
sha = lambda data: hashlib.sha256(data).hexdigest()  # noqa: E731
EXAMPLE = [rec.PayloadFile(1, 18846, sha(b'example XHCI1.BIN')), rec.PayloadFile(0, 18918, sha(b'example XHCI0.BIN'))]
EXAMPLE_RUNBOOK = rec.RunbookFile(50000, sha(b'example runbook'))
# Pinned on purpose: the operator approves the digest of the text, so an edit of the text must change this line in the same change. The files are
# examples (digests of fixed strings), so the pin follows the text and not the payloads or the runbook.
PIN_BYTES, PIN_SHA256 = 21751, "326cdf4acf9ffcaaf5ea7f47a341fa772047a180d3539d3dd2853aba8410b0db"

REQUIRED_PHRASES = (
    'A2 (the first sector write to the stick) is NOT granted', 'Approved in principle by the operator on 2026-11-17 02:10', 'hardware NOT RUN',
    'Thirteen registers', 'A nominal run makes 17 stores', 'eight seconds', '30 s read-only plug watch', '40 lines and 4096 loads', '3800',
    'In its binary only two stores use a pointer that is not rooted in the payload\'s own stack, context or line buffers',
    'it proves the base of a store and not its offset', 'The offsets are checked on the paths the host tests run', 'AddressSanitizer and UBSan on every run',
    'Every cache instruction is behind an arena gate too',
    'Any DWC3 core or glue register', 'any SMMU register, not even a fault status clear', 'USBSTS, even its write-1-to-clear bits', 'PORTSC of any port',
    'any clock, GDSC or GCC register', 'PHY, repeater, retimer, I2C, SPMI, GPIO or UCSI', 'any SMC or HVC', 'the stick, any disk or NVMe',
    'An unclocked block hangs the bus and costs a power cycle', 'An XPU or SMMU violation may reset the SoC', 'UNKNOWN', 'ExitBootServices',
    'a page that ends at a NEXT line', 'HALTED=NO', 'STORE_REFUSED', 'There is no retry of a payload in the same boot', 'NOT AUTHORISED BY THIS RECORD',
    '0xCF412000', '0x880000000', 'XHCI1.BIN', 'XHCI0.BIN', 'SLBOUNCE-DEVHE-MARK.EFI', 'Q1N1-PAYLOAD.EFI', 'plan a1-xhci-s1',
    'one video, with the VERDICT and END lines in it', 'plug the Mac\'s cable only at the on-screen cue',
    # what the review of 2026-11-17 added: the rules of a bad completion, the waits, the durations, the offsets and the hazards
    'RESET is announced again in each', 'RTSOFF are read from the controller and printed', '0x2000 and 0x1000, which the simulator and QEMU use, are INFERRED',
    'at or above 0xc100', 'CRCR, the doorbell and IMAN are not', 'Only arena 2 is RAM that the runner owns and declared to the payload',
    'with every group announced again', 'A bad completion ends it too', 'or a REDIRECT',
    'left halted but still programmed', 'HCRST 1 s, CNR 1 s',
    'Every eligible attempt keeps six lines',
    'A cable between two USB-C machines negotiates power and data roles and VBUS', 'returns PORTSC.PP to 0',
    'a store is a plain str', 'A restart alone is not an xHC result', 'two of the three cpuon boots', 'The unit\'s PPC is not recorded',
    # round 3 (the delta review of 2026-11-17, N1 to N7)
    'At the RUN or NOOP stage a silence (no event), a Host System Error or Host Controller Error (HSE or HCE), or a controller that does not start (HCH still set after RS)',
    'So does any failure at the RESET or PROGRAM stage, an HSE or HCE that the reset itself ends with included',
    'a failure at the RESET or PROGRAM stage stops the run too',
    'in the pre-flight lines only when the reads and checks of the controller\'s own words pass',
    'prints the VERDICT line alone, with no DBOFF or RTSOFF line', 'or DBOFF, whichever of the two is at fault',
    'Event 200 ms, polled every 1 ms throughout',
    'plus at most 7.9 s of bounded waits (3 x 2.3 s, and 1 s for a CNR wait in the pre-flight), about 158 s', 'plus at most 10.2 s, about 200 s',
    'a wait polled every 10 ms may end up to 10 ms late (about 0.1 s over a run)', 'the time the runner takes to draw the lines',
    'the SMMU reports of earlier attempts (the SMMU1 R<k> lines)', 'Only those optional lines are lost this way, never the first line of a result, the VERDICT or the END line',
    'leaves 3, 2, 1 or 0 of the four SMMU1 reports on the page after 0, 1, 2 or 3 or more CHG lines', 'and END says LINES_CUT every time',
    'after a success at arena 4 from one CHG line on, at arena 3 from two, at arena 2 from three, never at arena 1',
    'a VERDICT NOOP_OK may be followed by an END line with LINES_CUT', 'and the result is whole', '(three arenas) prints every report',
    'LINES_CUT on the END line after any VERDICT except NOOP_OK', 'does not stop the sequence when it is accounted for',
    'it is read, recorded, and the page is kept', 'A LINES_CUT that nothing accounts for',
    'Reading it that way is the operator\'s decision, taken by approving this record', 'is the optional second boot',
    'S0 observed the stick\'s controller as usb_1_ss0', 'with the stick in the port it was in that boot',
    'Which connector it is on in this boot is not recorded', 'depends on where the stick is',
    'start the Mac-side logger (with --allow-empty-usb if the Mac has no other USB device)',
    # round 4 (the final delta review of 2026-11-17, R1 to R11)
    'This text, with the digests it names (the files of section 1), is what the operator approves',
    'A runbook of other bytes is another document', 'the runbook that sections 9 and 10 refer to (outcome tree, actions in order); read, not staged',
    'a WHY= of READ, CLOCK, BUDGET or LINES', 'any REFUSED that the runbook of section 1 does not list, any line that its outcome tree does not list',
    'PAYLOAD LOADED ... SHA=<16 hex digits>, PAYLOAD VERIFY=OK and PAYLOAD CALL 0 <the same 16 digits>',
    'compare the digits with the first 16 of the digest of that boot\'s payload in section 1',
    'a mismatch, or no VERIFY=OK, means power off, and nothing of A1 has been written (the marker SP11\\DONE1.TXT has; delete it to run again)',
    'What follows is INFERRED from the architecture (ARM IHI 0062), not re-read',
    'The SMMU records only the first fault of a context bank until its FSR is cleared, which A1 never does: SMMU=SAME does not prove that an attempt did not '
    'fault, and an SMMU1 FAR or FSYNR0 may belong to an earlier fault',
    'A result of 100 characters or more takes a second line (R<k> STS=..), which no budget reserves',
    'an attempt that starts with 32 lines on the page, the most with which one starts, loses it', 'the R<k> line ends before its STS= field',
    'That is a visibly cut line, and nothing accounts for it (section 9)', '(an R<k> line that ends before its STS= field, section 7)',
    'whose results are one line each and whose sGFSR is 0 before and after every attempt, so that a report is one line (S0b read the sGFSR 0 before any attempt: OBSERVED)',
    'whatever the number of port changes in the watch (it prints at most four CHG lines)',
    'The model overwrites FAR at every fault, so it assumes that every failed attempt changes the SMMU\'s fault registers and the DWC3\'s bus-error address and valid bit',
    'With an sGFSR that is not 0 already at the pre-flight (not observed)',
    'S0 was the hub topology', 'S0b, the direct topology that A1 uses, read both controllers idle with nothing connected while the stick was in',
    'after each NEXT line are the cancel window, and nothing of that group is written yet', 'apart from that, touch nothing until END')


# Round 5 (the last delta review of 2026-11-17, N-A to N-F). Apart from REQUIRED_PHRASES because several of these hold UNKNOWN or XHCI0.BIN, which that tuple
# keeps as phrases of their own (no phrase of it may contain another: its replace-all check of one would break both).
ROUND5_PHRASES = (
    # N-A: the idle reading does not show where the stick was; the path of a direct boot makes usb_1_ss0 likely (STICK-LOG 13, last paragraph)
    'That reading does not show where the stick was (the link was down at EL2, cause UNKNOWN)',
    'the device path of boot 3, a direct boot, carried S0\'s PCI node Pci(0x80,0x0), which makes usb_1_ss0 likely (INFERRED: docs/SP11-STICK-LOG.md section 13, last paragraph)',
    'so, if the stick is in the same port as in boot 3, XHCI0.BIN is the file that resets its controller',
    # N-B: what SMMU=CHANGED and SMMU=SAME compare
    'with or without a change of the SMMU\'s fault registers and the DWC3\'s bus-error address and valid bit, halts the controller',
    'the line of an attempt says whether the SMMU\'s fault registers and the DWC3\'s bus-error address and valid bit changed since the end of the one before',
    'when all four arenas fail with a change of the SMMU\'s fault registers and the DWC3\'s bus-error address and valid bit',
    'that failed with a change of the SMMU\'s fault registers and the DWC3\'s bus-error address and valid bit says LINES_CUT too',
    # N-C: the value is observed, its meaning is not
    'The baseline already reads FSR=0X400 (OBSERVED, S0b; its meaning is UNKNOWN)',
    # N-F1 and N-F4: the files of section 1 are all pinned; a restart was reported, and none after the read-only controls
    'with the digests it names (the files of section 1)',
    'a restart was reported after two of the three cpuon boots and after none of the read-only control boots')
# Wording that the last review withdrew or corrected: it must not come back.
WITHDRAWN_PHRASES = (
    'may be on neither', 'left by firmware', 'and nothing has been written', 'the board restarted after two of the three cpuon boots',
    'a change of the SMMU\'s registers', 'changes the SMMU\'s registers', 'fault registers, halts the controller', '(the payload files and the runbook)')


def round5_problems(text):
    flat = ' '.join(text.split())
    return ([f'missing: {phrase}' for phrase in ROUND5_PHRASES if phrase not in flat] +
            [f'withdrawn wording is back: {phrase}' for phrase in WITHDRAWN_PHRASES if phrase in flat])


def missing_phrases(text):
    flat = ' '.join(text.split())
    return [phrase for phrase in REQUIRED_PHRASES if phrase not in flat]


def table_problems(table, header_table):
    """What differs between the record's register table [(name, where, offset, groups)] and the header's [(name, window, offset, group mask)]."""
    windows = {'op': 0, 'ir0': 1, 'db': 2}
    bits = {'RESET': 2, 'PROGRAM': 4, 'RUN': 8, 'NOOP': 16}
    problems = []
    if [r[0] for r in table] != [h[0] for h in header_table]:
        problems.append('the names or their order differ')
        return problems
    for (name, where, offset, groups), (_, window, header_offset, mask) in zip(table, header_table):
        if (windows[where], offset) != (window, header_offset):
            problems.append(f'{name}: {where}+{offset:#x} against the header\'s window {window} offset {header_offset:#x}')
        stated = sum(bit for word, bit in bits.items() if word in groups)
        if name != 'USBCMD' and stated != mask:
            problems.append(f'{name}: groups {groups!r} against the header\'s mask {mask}')
    return problems


def number_problems(text, constants):
    """The numbers of the text against the header's: the waits, the caps, the arenas."""
    problems = []
    text = ' '.join(text.split())
    expected = {
        'eight seconds': constants['WAIT_BEFORE_WRITE_S'] == 8, '30 s read-only plug watch': constants['WATCH_S'] == 30,
        '3800': constants['XHCI_S1_LOAD_CAP'] == 3800, '40 lines': constants['XHCI_S1_LINE_CAP'] == 40,
        '0xCF412000': constants['ARENA1_BASE'] == 0xCF412000, '0x880000000': constants['ARENA4_BASE'] == 0x880000000,
        'four pages': constants['ARENA1_PAGES'] == 4, 'at most 9 pages': True, 'halt 50 ms': constants['T_HALT_US'] == 50000,
        'HCRST 1 s': constants['T_RESET_US'] == 1000000, 'CNR 1 s': constants['T_CNR_US'] == 1000000, 'run 50 ms': constants['T_RUN_US'] == 50000,
        'every 1 ms for the first 100 ms of a wait and every 10 ms after': (constants['POLL_US'], constants['POLL_FAST_WINDOW_US'], constants['POLL_SLOW_US']) ==
        (1000, 100000, 10000), 'at or above 0xc100': constants['STORE_LIMIT'] == 0xc100, 'keeps six lines': constants['ATTEMPT_LINES_MIN'] == 6,
        'Completion Event 200 ms': constants['T_EVENT_US'] == 200000, 'Event 200 ms, polled every 1 ms throughout': (constants['T_EVENT_US'], constants['POLL_US']) == (200000, 1000),
        'up to four attempts': constants['ARENAS'] == 4,
        '24 KiB': constants['STACK_KEPT'] == 24 * 1024, 'DC CVAC': True, 'DC CIVAC': True, '16 TRBs': constants['CMD_TRBS'] == 16, '64 TRBs': constants['EVENT_TRBS'] == 64,
    }
    for phrase, holds in expected.items():
        if phrase not in text:
            problems.append(f'{phrase!r} is not in the text')
        elif not holds:
            problems.append(f'{phrase!r} is not what the header says')
    return problems


def duration_problems(text, constants):
    """The worst-case durations of the text against the header's waits: the watch, five announcements of eight seconds per attempt (XHCI1.BIN has three
    arenas, XHCI0.BIN four) and the bounded waits of an attempt (HCRST, CNR, run, the event, the halt) with the one CNR wait of the pre-flight. The text says
    "about" for the total (a wait polled every 10 ms may end late, and the runner's drawing of the lines is not known) and shows its own arithmetic."""
    flat = ' '.join(text.split())
    per_attempt = (constants['T_RESET_US'] + constants['T_CNR_US'] + constants['T_RUN_US'] + constants['T_EVENT_US'] + constants['T_HALT_US']) / 1e6
    problems = []
    for name, attempts in (('XHCI1.BIN', 3), ('XHCI0.BIN', 4)):
        fixed = constants['WATCH_S'] + attempts * 5 * constants['WAIT_BEFORE_WRITE_S']
        bounded = attempts * per_attempt + constants['T_CNR_US'] / 1e6
        claim = f'{constants["WATCH_S"]} s + {attempts * 5} x {constants["WAIT_BEFORE_WRITE_S"]} s = {fixed} s plus at most {bounded:.1f} s'
        if claim not in flat:
            problems.append(f'{name}: {claim!r} is not in the text')
            continue
        total = re.search(re.escape(claim) + r'[^;]*?, about (\d+) s', flat)
        if not total or int(total.group(1)) != round(fixed + bounded):
            problems.append(f'{name}: the total is not about {round(fixed + bounded)} s')
        if name == 'XHCI1.BIN' and f'({attempts} x {per_attempt:.1f} s, and {constants["T_CNR_US"] / 1e6:.0f} s for a CNR wait in the pre-flight)' not in flat:
            problems.append(f'{name}: the arithmetic of the bounded waits is not in the text')
    return problems


def header_constants(text):
    """The #define numbers and enum values of payloads/xhci-regs.h that the record quotes."""
    found = {}
    for name, value in re.findall(r'#define (\w+) \(?(0x[0-9a-fA-F]+u?|\d+u?|UINT64_C\(0x[0-9A-Fa-f]+\))\)?', text):
        found[name] = int(re.sub(r'UINT64_C\((0x[0-9A-Fa-f]+)\)', r'\1', value.rstrip('u')), 0)
    found['STACK_KEPT'] = 24 * 1024 if '#define STACK_KEPT (24u * 1024u)' in text else 0
    for name, value in re.findall(r'\b(CMD_TRBS|EVENT_TRBS) = (\d+)', text):
        found[name] = int(value)
    return found


def q1n1_module():
    path = Path(TREE) / 'tools' / 'test-q1n1-sp11-payload-xhci.py'
    spec = importlib.util.spec_from_file_location('q1n1_xhci_test_for_record', path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(Path(TREE) / 'tools'))
    spec.loader.exec_module(module)
    return module


class Record(unittest.TestCase):
    def test_the_text_is_deterministic_ascii_and_within_the_width(self):
        first, second = rec.render(EXAMPLE, EXAMPLE_RUNBOOK), rec.render(EXAMPLE, EXAMPLE_RUNBOOK)
        self.assertEqual(first, second)
        self.assertTrue(first.isascii())
        self.assertLessEqual(max(len(line) for line in first.splitlines()), rec.WIDTH)
        self.assertEqual(rec.digest(EXAMPLE, EXAMPLE_RUNBOOK)[2], rec.sha256_text(first))
        self.assertEqual(rec.digest(EXAMPLE[:1], EXAMPLE_RUNBOOK)[0], rec.render(EXAMPLE[:1], EXAMPLE_RUNBOOK))
        self.assertNotEqual(rec.render(EXAMPLE[:1], EXAMPLE_RUNBOOK), first, 'the second file is in the text only when it is given')

    def test_the_digest_of_the_example_record_is_pinned(self):
        text, size, digest = rec.digest(EXAMPLE, EXAMPLE_RUNBOOK)
        self.assertEqual((size, digest), (PIN_BYTES, PIN_SHA256), 'the text changed: the digest the operator approves changed with it; update the pin and say why')

    def test_the_text_says_everything_a1_covers_and_does_not(self):
        self.assertEqual(missing_phrases(rec.render(EXAMPLE, EXAMPLE_RUNBOOK)), [])

    def test_each_rule_of_the_phrase_check_has_a_faulty_text_it_rejects(self):
        flat = ' '.join(rec.render(EXAMPLE, EXAMPLE_RUNBOOK).split())
        for phrase in REQUIRED_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertEqual(missing_phrases(flat.replace(phrase, 'X' * len(phrase))), [phrase])

    def test_the_rows_of_the_last_review_are_in_the_text_and_the_withdrawn_wording_is_not(self):
        flat = ' '.join(rec.render(EXAMPLE, EXAMPLE_RUNBOOK).split())
        self.assertEqual(round5_problems(flat), [])
        for phrase in ROUND5_PHRASES:
            with self.subTest(removed=phrase):
                self.assertTrue(round5_problems(flat.replace(phrase, 'X' * len(phrase))), phrase)
        for phrase in WITHDRAWN_PHRASES:
            with self.subTest(back=phrase):
                self.assertTrue(round5_problems(flat + ' ' + phrase), phrase)

    def test_reverting_the_sentence_about_the_sticks_controller_is_caught(self):
        flat = ' '.join(rec.render(EXAMPLE, EXAMPLE_RUNBOOK).split())
        start, end = flat.index('That reading does not show where the stick was'), flat.index('is the file that resets its controller.') + len('is the file that resets its controller.')
        sentence = flat[start:end]
        self.assertIn('which makes usb_1_ss0 likely (INFERRED: docs/SP11-STICK-LOG.md section 13, last paragraph)', sentence)
        old = 'the stick\'s controller is not printed there, so the stick may be on neither controller (INFERRED).'
        self.assertTrue(round5_problems(flat.replace(sentence, old)), 'the withdrawn claim and the missing sentence')
        self.assertTrue(round5_problems(flat.replace('XHCI0.BIN is the file that resets', 'XHCI1.BIN is the file that resets')), 'the other file named')
        self.assertTrue(round5_problems(flat.replace('which makes usb_1_ss0 likely', 'which makes usb_1_ss1 likely')), 'the other controller named')

    def test_removing_the_marker_clause_after_the_runners_lines_is_caught(self):
        flat = ' '.join(rec.render(EXAMPLE, EXAMPLE_RUNBOOK).split())
        clause = ' (the marker SP11\\DONE1.TXT has; delete it to run again)'
        self.assertIn('nothing of A1 has been written' + clause, flat)
        self.assertTrue(missing_phrases(flat.replace(clause, '')), 'the clause removed')
        self.assertTrue(missing_phrases(flat.replace('nothing of A1 has been written' + clause, 'nothing has been written')), 'the old wording back')
        self.assertTrue(round5_problems(flat.replace('nothing of A1 has been written' + clause, 'nothing has been written')), 'the old wording back')

    def test_the_registers_of_the_text_are_the_thirteen_and_none_other(self):
        text = rec.render(EXAMPLE, EXAMPLE_RUNBOOK)
        names = [name for name, *_ in rec.REGISTERS]
        self.assertEqual(len(names), 13)
        for name in names:
            self.assertEqual(len(re.findall(rf'^   {name}\s+(?:op|ir0|db)\s+\+ 0x', text, re.M)), 1, name)
        for forbidden in ('PORTSC ', 'GCTL', 'GUSB2PHYCFG', 'GFSR ', 'USBSTS  '):
            self.assertFalse(re.search(rf'^   {forbidden}', text, re.M), forbidden)

    def test_the_inputs_are_refused_when_wrong(self):
        good = rec.PayloadFile(1, 100, sha(b'x'))
        for label, payloads in (('none', []), ('three', [good, EXAMPLE[1], good]), ('ss0 first', [EXAMPLE[1]]), ('ss0 twice', [good, EXAMPLE[1], EXAMPLE[1]]),
                                ('upper-case digest', [rec.PayloadFile(1, 100, sha(b'x').upper())]), ('short digest', [rec.PayloadFile(1, 100, 'ab')]),
                                ('size zero', [rec.PayloadFile(1, 0, sha(b'x'))]), ('size huge', [rec.PayloadFile(1, 1 << 20, sha(b'x'))])):
            with self.subTest(case=label), self.assertRaises(rec.RecordError):
                rec.render(payloads, EXAMPLE_RUNBOOK)

    def test_the_runbook_is_refused_when_wrong(self):
        for label, runbook in (('size zero', rec.RunbookFile(0, sha(b'x'))), ('size huge', rec.RunbookFile(1 << 21, sha(b'x'))),
                               ('upper-case digest', rec.RunbookFile(100, sha(b'x').upper())), ('short digest', rec.RunbookFile(100, 'ab')),
                               ('none', None), ('a payload file', rec.PayloadFile(1, 100, sha(b'x')))):
            with self.subTest(case=label), self.assertRaises(rec.RecordError):
                rec.render(EXAMPLE, runbook)

    def test_the_runbook_is_named_by_size_and_digest_in_section_1_and_nowhere_else(self):
        text = rec.render(EXAMPLE, EXAMPLE_RUNBOOK)
        section_1 = text.split('\n2. WHAT THE PAYLOAD WRITES')[0]
        self.assertRegex(section_1, rf'(?m)^   {re.escape(rec.RUNBOOK_NAME)} +sha256 {EXAMPLE_RUNBOOK.sha256}$')
        self.assertRegex(section_1, rf'(?m)^      {EXAMPLE_RUNBOOK.size} bytes; the runbook that sections 9 and 10 refer to')
        self.assertEqual(text.count(EXAMPLE_RUNBOOK.sha256), 1)

    def test_one_changed_byte_of_the_runbook_changes_the_digest_line_and_nothing_else(self):
        first = rec.render(EXAMPLE, EXAMPLE_RUNBOOK)
        other = rec.RunbookFile(EXAMPLE_RUNBOOK.size, sha(b'example runbook, one byte changed'))
        second = rec.render(EXAMPLE, other)
        self.assertEqual(len(first.splitlines()), len(second.splitlines()))
        changed = [a for a, b in zip(first.splitlines(), second.splitlines()) if a != b]
        self.assertEqual(len(changed), 1)
        self.assertIn(rec.RUNBOOK_NAME, changed[0])
        self.assertNotEqual(rec.digest(EXAMPLE, EXAMPLE_RUNBOOK)[2], rec.digest(EXAMPLE, other)[2])
        self.assertNotEqual(rec.render(EXAMPLE, rec.RunbookFile(EXAMPLE_RUNBOOK.size + 1, EXAMPLE_RUNBOOK.sha256)), first, 'the size is named too')

    def test_a_line_over_the_width_or_not_ascii_is_refused(self):
        original = rec.WIDTH
        try:
            rec.WIDTH = 100
            with self.assertRaises(rec.RecordError):
                rec.render(EXAMPLE, EXAMPLE_RUNBOOK)
        finally:
            rec.WIDTH = original
        original_note = rec.OPERATOR_APPROVAL
        try:
            rec.OPERATOR_APPROVAL = '2026-11-17 02:10 \u00e9'
            with self.assertRaises(rec.RecordError):
                rec.render(EXAMPLE, EXAMPLE_RUNBOOK)
        finally:
            rec.OPERATOR_APPROVAL = original_note

    def test_the_text_names_no_machine_and_no_path(self):
        text = rec.render(EXAMPLE, EXAMPLE_RUNBOOK)
        pattern = re.compile('|'.join(['/Us' + 'ers/', '/ho' + 'me/', '/Vol' + 'umes/']) + r'|\bsae' + r'ed\b|host' + r'name|\b[0-9a-f]{2}(?::[0-9a-f]{2}){5}\b', re.I)
        self.assertIsNone(pattern.search(text))
        self.assertLess(len(text.encode()), 50 * 1024)

    def test_the_two_pinned_efi_files_are_those_of_the_timers_plan(self):
        self.assertEqual(rec.SLBOUNCE_SHA256[:8], '4107c28c')
        self.assertEqual(rec.RUNNER_SHA256[:8], 'c2057909')
        self.assertTrue(all(re.fullmatch(r'[0-9a-f]{64}', d) for d in (rec.SLBOUNCE_SHA256, rec.RUNNER_SHA256)))


class Cli(unittest.TestCase):
    """`python3 tools/sp11_xhci_s1_record.py --xhci1 <file> [--xhci0 <file>] [--digest]`: reads the files, writes nothing."""

    def setUp(self):
        self.dir = TOOLS.parent / 'build' / 'sp11-xhci-s1-record-test'
        shutil.rmtree(self.dir, ignore_errors=True)
        self.dir.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.one, self.zero = self.dir / 'xhci1.bin', self.dir / 'xhci0.bin'
        self.one.write_bytes(b'example XHCI1.BIN' * 100)
        self.zero.write_bytes(b'example XHCI0.BIN' * 100)
        self.runbook = self.dir / 'runbook.md'
        self.runbook.write_bytes(b'# an example runbook\n' * 50)

    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = rec.main([str(a) for a in argv])
        return status, out.getvalue(), err.getvalue()

    def payloads(self, *paths):
        return [rec.PayloadFile(c, len(p.read_bytes()), sha(p.read_bytes())) for c, p in zip((1, 0), paths)]

    def test_the_text_and_the_digest_are_those_of_the_files_given(self):
        status, out, err = self.run_main('--xhci1', self.one, '--xhci0', self.zero, '--runbook', self.runbook)
        self.assertEqual((status, err), (0, ''))
        runbook = rec.runbook_file(self.runbook)
        self.assertEqual(runbook, rec.RunbookFile(len(self.runbook.read_bytes()), sha(self.runbook.read_bytes())), 'the digest of the bytes on disk')
        self.assertEqual(out, rec.render(self.payloads(self.one, self.zero), runbook))
        status, out, err = self.run_main('--xhci1', self.one, '--runbook', self.runbook, '--digest')
        _, size, digest = rec.digest(self.payloads(self.one), runbook)
        self.assertEqual((status, out, err), (0, f'{size} {digest}\n', ''))

    def test_a_different_file_gives_a_different_digest(self):
        first = self.run_main('--xhci1', self.one, '--runbook', self.runbook, '--digest')[1]
        self.one.write_bytes(self.one.read_bytes()[:-1] + b'!')
        self.assertNotEqual(self.run_main('--xhci1', self.one, '--runbook', self.runbook, '--digest')[1], first)

    def test_a_different_runbook_gives_a_different_digest(self):
        first = self.run_main('--xhci1', self.one, '--runbook', self.runbook, '--digest')[1]
        self.runbook.write_bytes(self.runbook.read_bytes()[:-1] + b'!')
        self.assertNotEqual(self.run_main('--xhci1', self.one, '--runbook', self.runbook, '--digest')[1], first)

    def test_the_runbook_defaults_to_the_repositorys_own_file(self):
        self.assertEqual(rec.DEFAULT_RUNBOOK, ROOT / 'docs' / 'SP11-XHCI-S1.md')
        self.assertEqual(rec.RUNBOOK_NAME, 'docs/SP11-XHCI-S1.md')
        self.assertTrue(rec.DEFAULT_RUNBOOK.is_file())
        implicit = self.run_main('--xhci1', self.one, '--digest')
        self.assertEqual(implicit, self.run_main('--xhci1', self.one, '--runbook', rec.DEFAULT_RUNBOOK, '--digest'))
        self.assertEqual(implicit[0], 0)
        status, text, _ = self.run_main('--xhci1', self.one)
        self.assertEqual(status, 0)
        self.assertIn(f'{rec.RUNBOOK_NAME} ', text)
        self.assertIn(sha(rec.DEFAULT_RUNBOOK.read_bytes()), text, 'the text names the digest of the committed runbook, byte for byte')

    def test_wrong_inputs_are_refused_with_nothing_on_standard_output(self):
        (self.dir / 'empty.bin').write_bytes(b'')
        (self.dir / 'huge.bin').write_bytes(bytes(65537))
        (self.dir / 'huge.md').write_bytes(bytes((1 << 20) + 1))
        for label, argv in (('a missing file', ('--xhci1', self.dir / 'absent.bin')), ('an empty file', ('--xhci1', self.dir / 'empty.bin')),
                            ('a file over 64 KiB', ('--xhci1', self.dir / 'huge.bin')), ('a missing second file', ('--xhci1', self.one, '--xhci0', self.dir / 'absent.bin')),
                            ('a missing runbook', ('--xhci1', self.one, '--runbook', self.dir / 'absent.md')),
                            ('an empty runbook', ('--xhci1', self.one, '--runbook', self.dir / 'empty.bin')),
                            ('a runbook over 1 MiB', ('--xhci1', self.one, '--runbook', self.dir / 'huge.md'))):
            with self.subTest(case=label):
                status, out, err = self.run_main(*argv)
                self.assertEqual((status, out), (1, ''))
                self.assertTrue(err.startswith('refused: '), err)


def pin_problems(module, directory):
    """What a generator `module` (the real one or a mutated copy) gets wrong about pinning the runbook, through its command line: the text for each of two
    runbooks that differ in one byte must hold that file's own SHA-256, and the two texts must differ. Writes under `directory` only."""
    first, second, one = directory / 'runbook-a.md', directory / 'runbook-b.md', directory / 'xhci1.bin'
    first.write_bytes(b'runbook a\n' * 40)
    second.write_bytes(b'runbook a\n' * 39 + b'runbook b\n')
    one.write_bytes(b'example XHCI1.BIN' * 100)
    texts, problems = {}, []
    for label, path in (('a', first), ('b', second)):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = module.main(['--xhci1', str(one), '--runbook', str(path)])
        if status:
            return [f'--runbook {label} was refused: {err.getvalue().strip()}']
        texts[label] = out.getvalue()
        if sha(path.read_bytes()) not in texts[label]:
            problems.append(f'the text for runbook {label} does not hold the SHA-256 of that file')
    if texts['a'] == texts['b']:
        problems.append('a changed byte of the runbook does not change the text')
    return problems


# Each mutation of the generator's source: a name, the text it replaces (once) and its replacement. Each is a way to pin nothing while looking as if it pins.
GENERATOR_MUTATIONS = (
    ('hashes-a-constant-instead-of-the-runbook', "    data = Path(path).read_bytes()\n    return RunbookFile(", "    data = b'constant'\n    return RunbookFile("),
    ('prints-a-constant-digest', 'sha256 {runbook.sha256}', 'sha256 {SLBOUNCE_SHA256}'),
    ('prints-the-size-in-place-of-the-digest', 'sha256 {runbook.sha256}', 'sha256 {runbook.size}'),
    ('ignores-the-runbook-option', 'runbook_file(args.runbook)', 'runbook_file(DEFAULT_RUNBOOK)'),
)


class RunbookPin(unittest.TestCase):
    """The record names the SHA-256 of the runbook it is read with: a generator that does not is caught, and the check is not vacuous."""

    def setUp(self):
        self.dir = TOOLS.parent / 'build' / 'sp11-xhci-s1-record-pin-test'
        shutil.rmtree(self.dir, ignore_errors=True)
        (self.dir / 'tools').mkdir(parents=True)
        (self.dir / 'docs').mkdir()
        (self.dir / 'docs' / rec.RUNBOOK_NAME.rsplit('/', 1)[1]).write_bytes(b'the default runbook of the copy\n')
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.source = Path(rec.__file__).read_text(encoding='utf-8')

    def copy_of_the_generator(self, name, source):
        path = self.dir / 'tools' / f'{name}.py'
        path.write_text(source, encoding='utf-8')
        spec = importlib.util.spec_from_file_location(f'record_copy_{name}', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_the_generator_passes_its_own_pin_check(self):
        self.assertEqual(pin_problems(rec, self.dir), [])
        self.assertEqual(pin_problems(self.copy_of_the_generator('unmutated', self.source), self.dir), [], 'a faithful copy passes: the mutants below fail for their own fault')

    def test_each_way_of_pinning_nothing_is_caught(self):
        for name, old, new in GENERATOR_MUTATIONS:
            with self.subTest(mutation=name):
                self.assertEqual(self.source.count(old), 1, 'the mutation applies exactly once')
                found = pin_problems(self.copy_of_the_generator(name.replace('-', '_'), self.source.replace(old, new)), self.dir)
                self.assertTrue(found, name)

    def test_a_generator_that_leaves_the_runbook_out_of_the_text_is_caught(self):
        source = re.sub(r"    lines \+= \[f'   \{RUNBOOK_NAME:<24\}.*?\n(?=    lines \+= para\('A runbook)", '', self.source, flags=re.S)
        self.assertNotEqual(source, self.source, 'the lines naming the runbook were found and removed')
        found = pin_problems(self.copy_of_the_generator('leaves_it_out', source), self.dir)
        self.assertEqual(len(found), 3, found)


@unittest.skipUnless(TREE and (Path(TREE) / 'payloads' / 'xhci-regs.h').is_file(), 'Q1N1_SP11_TREE does not name a q1n1 tree with payloads/xhci-regs.h: the header is not compared')
class AgainstThePayload(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.header = (Path(TREE) / 'payloads' / 'xhci-regs.h').read_text(encoding='utf-8')
        cls.module = q1n1_module()
        cls.table = cls.module.header_registers(cls.header)
        cls.text = rec.render(EXAMPLE, EXAMPLE_RUNBOOK)

    def test_the_registers_are_the_headers(self):
        self.assertEqual(table_problems(rec.REGISTERS, self.table), [])

    def test_a_faulty_table_is_rejected_by_that_comparison(self):
        wrong_offset = tuple((n, w, o + 4 if n == 'CONFIG' else o, g) for n, w, o, g in rec.REGISTERS)
        wrong_group = tuple((n, w, o, 'RUN' if n == 'IMAN' else g) for n, w, o, g in rec.REGISTERS)
        wrong_window = tuple((n, 'op' if n == 'ERSTSZ' else w, o, g) for n, w, o, g in rec.REGISTERS)
        for label, table in (('an offset', wrong_offset), ('a group', wrong_group), ('a window', wrong_window), ('an extra register', rec.REGISTERS + (('PORTSC1', 'op', 0x420, 'RUN'),)),
                             ('a missing register', rec.REGISTERS[:-1])):
            with self.subTest(case=label):
                self.assertTrue(table_problems(table, self.table), label)

    def test_the_numbers_of_the_text_are_the_headers(self):
        constants = header_constants(self.header)
        self.assertEqual(number_problems(self.text, constants), [])
        self.assertEqual(rec.NOMINAL_STORES, 17)

    def test_a_number_that_the_header_does_not_have_is_rejected(self):
        constants = header_constants(self.header)
        for name, value in (('WAIT_BEFORE_WRITE_S', 4), ('WATCH_S', 3), ('XHCI_S1_LOAD_CAP', 9000), ('ARENA1_BASE', 0xCF000000), ('T_EVENT_US', 1), ('ARENAS', 3),
                            ('T_RESET_US', 100000), ('T_CNR_US', 100000), ('STORE_LIMIT', 0xd000), ('ATTEMPT_LINES_MIN', 9), ('POLL_SLOW_US', 1000)):
            with self.subTest(constant=name):
                self.assertTrue(number_problems(self.text, dict(constants, **{name: value})))

    def test_the_worst_case_durations_follow_from_the_headers_waits(self):
        constants = header_constants(self.header)
        self.assertEqual(duration_problems(self.text, constants), [])
        for name, value in (('T_RESET_US', 100000), ('T_CNR_US', 100000), ('WAIT_BEFORE_WRITE_S', 4), ('WATCH_S', 20), ('T_EVENT_US', 1000000), ('T_HALT_US', 5000000)):
            with self.subTest(constant=name):
                self.assertTrue(duration_problems(self.text, dict(constants, **{name: value})), name)

    def test_the_numbers_of_the_line_budget_in_the_text_are_the_host_models(self):
        cases = (Path(TREE) / 'tools' / 'test-q1n1-sp11-payload-xhci-cases.c').read_text(encoding='utf-8')
        self.assertEqual(budget_problems(self.text, cases), [])
        for label, faulty in (('a report fewer', cases.replace('{3, 2, 1, 0, 0, 0}', '{3, 2, 0, 0, 0, 0}')), ('a later success rule', cases.replace('>= 5)', '>= 6)')),
                              ('no rule', cases.replace('changes + success >= 5', 'changes >= 5')), ('no table', cases.replace('printed_four_arenas', 'printed'))):
            with self.subTest(case=label):
                self.assertTrue(budget_problems(self.text, faulty))

    def test_the_controllers_of_the_text_are_the_headers(self):
        self.assertIn('#define XHCI_S1_U0_BASE UINT64_C(0x0a600000)', self.header)
        self.assertIn('#define XHCI_S1_U1_BASE UINT64_C(0x0a800000)', self.header)
        self.assertIn('(XHCI_S1_CONTROLLER ? 0x1460u : 0x1420u)', self.header)
        self.assertEqual({k: v[2] for k, v in rec.CONTROLLERS.items()}, {1: 0x0a800000, 0: 0x0a600000})
        self.assertEqual({k: v[3] for k, v in rec.CONTROLLERS.items()}, {1: 0x1460, 0: 0x1420})


if __name__ == '__main__':
    sys.exit(0 if unittest.main(verbosity=1, exit=False).result.wasSuccessful() else 1)
