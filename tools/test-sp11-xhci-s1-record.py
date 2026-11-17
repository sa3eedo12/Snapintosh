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
  given, and refuses a missing, empty or oversized file;
* the supplement for boot 17 (`--supplement --xhci0 <file>`): XHCI0.BIN unchanged, one boot, rule 9 of the A1 record waived for it alone. Its text is pinned
  and says what it approves, amends and keeps, the facts of boots 15 and 16 with their labels, the hazards that are new, the outcome tree, the stop rules
  and the operator's extra actions (E0, short enough for one screen), each rule of that check with a faulty text it must reject; it refuses another
  XHCI0.BIN, XHCI1.BIN as the payload, another runbook, and an A1 text that no longer regenerates byte for byte from its pinned inputs, each for its own
  reason (a mutated copy of the generator without one of those checks must fail it, and so must a copy without one load-bearing sentence); the A1 record is
  the same with the supplement mode present; the places it cites exist in the runbook and in the stick log, also as of the commit the record names.
The q1n1 checks SKIP, and say so, without Q1N1_SP11_TREE.
"""
from pathlib import Path
import contextlib
import dataclasses
import hashlib
import importlib.util
import io
import os
import re
import shutil
import subprocess
import sys
import unittest

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(TOOLS))
import sp11_xhci_s1_record as rec  # noqa: E402
from sp11_xhci_s1_budget_check import budget_problems  # noqa: E402

TREE = os.environ.get('Q1N1_SP11_TREE')
_payloads = os.environ.get('Q1N1_SP11_PAYLOADS') or (str(Path(TREE) / 'build' / 'xhci-out' / 'payloads') if TREE else '')
PAYLOADS_DIR = Path(_payloads) if _payloads else None      # the built xhci0.bin and xhci1.bin, when there are any
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


# ---- The supplement for boot 17: XHCI0.BIN, unchanged, after the screen went off in boots 15 and 16 -----------------------------------------------------

# The A1 record as the operator approved it, copied here independently of rec.A1 so that a slip in the generator's pin is caught: (size, SHA-256).
APPROVED_XHCI1 = (19798, '989324b52e66c2128c13dcb84703c29b4b718b3326075935542b9ea541a16777')
APPROVED_XHCI0 = (19870, '63b11f901acbb4300dcd49a809841ccb5c1f626c777166547dc9967693f81e7f')
APPROVED_RUNBOOK = (51154, '88d96c587f493b3a83683cffa2c4e24eb8bb0468a649a6a890feb5a373ab319d')
APPROVED_RECORD = (21751, '6e21c40a0a0938860cf48d30be4a1a3e5ac398c94b511d322fe8791538e8f1ce')
# Pinned on purpose, like PIN_BYTES: the operator approves the digest of the supplement's text, so an edit of the text must change this line in the same change.
SUPPLEMENT_PIN_BYTES, SUPPLEMENT_PIN_SHA256 = 26162, 'ee0537639cbfe22ab30ca6c8b70cabfe072f940589b0d2f07ebf0253336910ff'

SUPPLEMENT_PHRASES = (
    # what it is, what it approves, and what it does not grant; how the operator approves it (the SHA-256 of the text and the 16 digits) and what A1 ran
    'RECORD OF AUTHORISATION POINT A1, SUPPLEMENT FOR BOOT 17: XHCI0.BIN ON usb_1_ss0 (EXPERIMENTAL; hardware NOT RUN)',
    'It approves ONE more boot, boot 17, with XHCI0.BIN only, and it amends one rule of the A1 record, for that boot only',
    'The operator approved point A1 in principle on 2026-11-17 02:10 and approved the A1 record by name; boots 15 and 16 ran under it with XHCI1.BIN '
    '(boot 16 repeated boot 15, which was not filmed)',
    'is what the operator approves, before boot 17, by quoting in writing the SHA-256 of this text (python3 tools/sp11_xhci_s1_record.py --supplement '
    '--xhci0 XHCI0.BIN --digest prints it) and the 16 digits 63B11F901ACBB430',
    'A2 (the first sector write to the stick) is NOT granted', 'the payload never touches a disk',
    # the files, and the stick log cited as of its commit (not by digest)
    'plan a1-xhci-s1-ss0: one autorun step, `load` SLBOUNCE-DEVHE-MARK.EFI, then Q1N1-PAYLOAD.EFI --el2; one boot, one controller',
    'XHCI0.BIN is the file the A1 record names, byte for byte: no byte of the payload is new for boot 17', 'not staged, does not run',
    'the runbook of sections 2, 6 and 8, UNCHANGED since the A1 record; read, not staged', 'A runbook of other bytes is another document',
    'the text that boots 15 and 16 ran under, which this record amends and keeps (section 2)',
    'docs/SP11-STICK-LOG.md is cited as of commit 42017e2 of the Snapintosh fork (its sections 10, 12, 13, 14.2 and 16), not by digest',
    # approves, amends, keeps
    'It approves ONE boot, boot 17: the plan a1-xhci-s1-ss0 with XHCI0.BIN (usb_1_ss0, SMMU stream 0x1420), started once',
    'with the Mac\'s USB-C cable connected to the free port at the on-screen cue, as in the A1 record, on AC power or at least 50% battery, with one hardware '
    'client at a time',
    'Boot 17 is the boot in which the runner prints PAYLOAD CALL 0 63B11F901ACBB430 (section 8)',
    'It approves nothing else: not a second boot, not a retry, not XHCI1.BIN again',
    'It amends rule 9 of the A1 record (its section 9, the stop rules), for boot 17 only',
    'The page of boot 16 ended at a NEXT line (section 3), so under that rule the sequence stopped there',
    'This record waives that stop, for boot 17 only',
    'the page of XHCI1.BIN was read (section 16 of docs/SP11-STICK-LOG.md transcribes it from the video; boot 15\'s page was not read, and this record takes '
    'boot 16\'s page as the page of XHCI1.BIN), and by approving this record the operator names XHCI0.BIN as the second file',
    'The waiver covers that page and nothing else: every stop rule applies to the page of boot 17 (section 7)',
    'says in O10 "No further xHC write until it is understood": it stays as it is, and for boot 17 only this record is the later word on those clauses',
    'Why this waiver, and why XHCI0.BIN (INFERRED): the firmware initialised usb_1_ss0 and used it for the stick',
    '0x0A800000, the window of usb_1_ss1, is not among them (OBSERVED: section 14.2 of docs/SP11-STICK-LOG.md',
    'with the same payload bytes as the A1 record and so no new code',
    # boot 17 changes two things, so a stop separates and a success does not by itself
    'Boot 17 changes TWO things from boot 16: the controller, and the first arena (the firmware\'s own memory, not the runner\'s stack)',
    'A stop separates a cause that belongs to usb_1_ss1, which UEFI never brought up (PHY, glue, a vote or a power state; the clocks and GDSC of both '
    'controllers read ON in S0), from one that does not',
    'a success does not by itself separate them (section 6)',
    'Everything else of the A1 record stands as written, for XHCI0.BIN: its sections 2 to 8, 10 and 11',
    'the runbook docs/SP11-XHCI-S1.md by the size and SHA-256 of section 1, UNCHANGED',
    'whose outcome tree O1 to O15 applies with U0 and U1 swapped for XHCI0.BIN, except that the Next column of its rows (O1, O9, O13)',
    'is advice for a later request and authorises nothing: for boot 17 section 7 is the only word on what follows',
    'and every ban: no write to a DWC3 core or glue register, an SMMU register, a clock, GDSC or GCC register, a PHY, repeater, retimer, I2C, SPMI, GPIO or '
    'UCSI register, any SMC or HVC, a UEFI variable, the stick, a disk or the NVMe. A2 is not granted',
    # the facts of boots 15 and 16, with their labels
    'OBSERVED, boot 16 (filmed in two videos; the page is cumulative and every line was read on several frames)',
    'The pre-flight read the controller (hazards (j) and (n)) and found it halted (STS 0x1) with CMD, CRCR, DCBAAP and CONFIG all 0',
    'so is that the free port is wired to usb_1_ss1, and a mode that uses another port is not excluded: section 16',
    'The screen went off 8.15 s (7.95 to 8.35 s) after that line was drawn and stayed off to the end of the video (2.6 s)',
    'No R1 line, VERDICT, END or fault page followed',
    'INFERRED: that the screen followed the RS store, the first store of the RUN group (the twelfth register store of the run)',
    'OBSERVED, boot 15 (by the operator, from memory only, not filmed; his first answer was that it ran to END, a later one that both boots ended with the '
    'screen off and that he is unsure of the details): the screen went off too',
    'Its page, its VERDICT, its arena and the moment the screen went off are UNKNOWN',
    'the unit stayed dark and he never turned it off',
    'when and how it was powered off are not recorded (docs/SP11-STICK-LOG.md section 16)',
    'No restart was seen', 'The Mac-side logger (989 ticks) saw no USB device or network interface added or removed',
    'UNKNOWN: the cause; whether the SoC stayed alive (nothing probes it: the panel was the only channel); whether a hardware watchdog was armed',
    'A boot 17 in which no No-Op completes (any verdict but NOOP_OK, or none) makes it 3 of 5 (2 if boot 15 does not count); an HSE in boot 17 would also be '
    'the first of SC3\'s two unexplained HSEs',
    # what is specific to XHCI0.BIN
    'The payload is the A1 record\'s, started on usb_1_ss0: window 0x0a600000, SMMU stream 0x1420',
    'The arenas, in this order: 1, the firmware\'s own xHCI memory, 0xCF412000, four pages',
    '(the S0 addresses: hub topology, OBSERVED; the direct topology\'s addresses were never read)',
    'when the ARENAS line says 1=OK, the first attempt of boot 17 uses RAM that the firmware used in S0, not the runner\'s stack as in boot 16',
    'VERDICT NOOP_OK ARENA=1 with END WRITES=17 is the shortest run', 'the page folds case: expect 63B11F901ACBB430',
    '30 s + 20 x 8 s = 190 s plus at most 10.2 s, about 200 s', 'reads up to about 210 S',
    'An END line with LINES_CUT after NOOP_OK ARENA=1 is therefore not accounted for, and stops (section 7 below)',
    # the hazards that are new or changed
    'The screen may go off again and the unit stay dark (a black-out, below), as in boot 16 and, by the operator\'s memory, boot 15',
    'Its bit 3, PPC, is clear (INFERRED: bit 3 is HCC_PPC in Linux drivers/usb/host/xhci-caps.h, read on master, not pinned; not run), so HCRST is not '
    'expected to remove port power there',
    'this replaces the UNKNOWN about PPC in hazard (f) of the A1 record for usb_1_ss1 only', 'The HCCPARAMS1 of usb_1_ss0 is not recorded (UNKNOWN)',
    'bits 10:9 of a context bank\'s FSR are the FORMAT field',
    '(VERIFIED by reading arm-smmu.h at the Linux revision pinned in section 10 of docs/SP11-STICK-LOG.md; not run)',
    'So no fault bit is set and the bank records no fault (INFERRED)',
    'are stale or undefined while no fault bit is set (INFERRED; they may hold an earlier fault whose FSR bits were cleared',
    'This replaces "its meaning is UNKNOWN" in section 4 of the A1 record; for this boot it also replaces the runbook\'s sentence that their meaning is '
    'UNKNOWN (docs/SP11-XHCI-S1.md section 5)',
    'a fault bit beyond FORMAT in FSR, a nonzero GFSR or a BERR= is a fault',
    'a change that nothing explains: SC2 is not excluded (as for SAME); report it, do not call it a fault',
    'The HCRST of XHCI0.BIN resets the controller that the stick is probably on', 'harmless after the runner loaded everything (hazard (d) of the A1 record)',
    'Arena 1 is RAM that the memory map calls free, not RAM the runner owns and declared (hazard (c) of the A1 record)',
    'Connecting the Mac to a download-mode device is read-only on our side: we add nothing to what macOS does by itself, install no driver and run no tool '
    'against it',
    'so removing it changes nothing of the verdict (INFERRED: the payload skips port events), but a running xHC may write event TRBs, which is why (c) waits',
    'DBOFF 0x2000, RTSOFF 0x1000 and two scratchpad buffers (HCS2 0x140000F1) were read on usb_1_ss1 only (OBSERVED, boot 16)',
    'REFUSED=OFFSET, SCRATCHPADS or HCE is possible, and a refusal writes nothing',
    # the outcome tree
    'Every reading below is INFERRED until the unit has run',
    'Section 16 of docs/SP11-STICK-LOG.md is the later word on O10\'s reading of the cause: no restart was seen, so of O10\'s two readings, hang or reset, '
    'the hang fits, and a dead display with a live SoC is a third that the runbook does not list',
    'NOOP_OK ARENA=1 (END HALTED=YES WRITES=17): the xHC of usb_1_ss0 read a TRB from the firmware\'s own memory and wrote the event back: DMA to that '
    'memory works through stream 0x1420 (O1)',
    'It does not say why boot 16 stopped: a cause specific to usb_1_ss1 and a cause tied to the runner-stack memory class (an SMMU stage 2 or an XPU range) '
    'both give this page, and a success ends the run before arena 2 is tried (INFERRED: one boot of each file, one unit). A stop on arena 1 is the '
    'informative branch',
    'the runner\'s own stack as the first arena on usb_1_ss0 (a changed payload, a new record) would separate the two',
    'NOOP_OK on another arena: if the ARENAS line did not offer arena 1, arena 2 is the first attempt (WRITES=17), the memory class of boot 16',
    'the cause is not specific to usb_1_ss1 alone. A systemic cause (the SMMU\'s context bank 3, an XPU, the EL2 environment), the firmware\'s memory as the '
    'arena, or any start of an xHC at EL2 fits; which of them is UNKNOWN',
    'A board restart is recorded with its time and the last line of the page, and a restart alone is not an xHC result (docs/SP11-XHCI-S1.md, 4.3)',
    'the read-only snapshot chain, a payload that reads the clock, GDSC, DWC3, glue and SMMU state and prints it before the RS store (a new record)',
    'NOOP_TIMEOUT or NOOP_HSE with SMMU=CHANGED: SC2 (O2, O7): context bank 3 (streams 0x1420 and 0x1460) faulted or redirected the xHC\'s DMA',
    'At the RUN or NOOP stage the payload goes on to the next arena, which is the experiment; at RESET or PROGRAM the run ends (O4, O5, O7)',
    'SMMU programming needs its own named authorisation: neither the A1 record nor this one covers it',
    'NOOP_TIMEOUT or NOOP_HSE with SMMU=SAME or UNREAD: SC2 is not excluded',
    'REFUSED=<name> (O9): nothing was written. REFUSED=OFFSET, SCRATCHPADS or HCE is possible on usb_1_ss0 (hazard (n); S0 saw HCE there behind a hub: '
    'docs/SP11-XHCI-S1.md, 6.2)',
    'that is O13, the other controller, and this boot\'s verdict is about usb_1_ss0\'s DMA only',
    'CHANGED=U0P1 or U0P2 is O12: the free port is on the controller under test, the Mac\'s cable sat on the controller being reset (hazard (f) of the A1 '
    'record), the stick is not on usb_1_ss0, and the premise of section 2 fails. CHANGED=NONE is O14; FAULT= is O15: stop',
    # the stop rules after this boot: STORE is added to the WHY values for boot 17 only, and the approval is spent once the payload has been called
    'The stop rules of section 9 of the A1 record apply to the page of boot 17 as written, with STORE added for boot 17 to its WHY= values (the runbook\'s '
    'O11 lists it)',
    'a WHY= of READ, CLOCK, STORE, BUDGET or LINES', 'The waiver of section 2 covers the page of boot 16 only',
    'Whatever the boot that printed PAYLOAD CALL 0 63B11F901ACBB430 shows, it is the last boot of A1 and of this record: there is no retry of a payload in the '
    'same boot, no second boot of this record, no XHCI1.BIN again and no other file, controller or arena order',
    'Any further xHC write, in any boot, needs a new record that the operator approves by name', 'SC3 counts as section 3 says',
    # the operator's actions and what is not authorised
    'read its plan a1-xhci-s1 as a1-xhci-s1-ss0, its XHCI1.BIN as XHCI0.BIN, and its second boot as boot 17',
    'The Shell\'s line reads AUTORUN a1-xhci-s1-ss0 STEP 1 OF 1', 'compare the digits with 63B11F901ACBB430, the first 16 of the digest of XHCI0.BIN in section 1',
    'a boot that did not print PAYLOAD CALL 0 63B11F901ACBB430 did not run the payload and is not boot 17: delete the marker and start again',
    'once that line has printed, the approval is spent',
    'If you did not plug the Mac\'s cable by WATCH DONE, power off in the 8 s after NEXT ARENA: a run without the cable is not approved, and that counts as '
    'the page being wrong',
    'A2; any write beyond sections 2 and 3 of the A1 record; a second boot of this record, a retry, XHCI1.BIN again',
    'every register class docs/SP11-XHCI-S1.md section 7 lists: each needs its own named authorisation', 'a run without the Mac\'s cable',
    'sending anything to a device that appears on the Mac after a black-out (E0(c) only looks)')

E0_PHRASES = (
    'E0, the extra actions of boot 17 (the operator\'s own; the payload writes nothing more for them)',
    'Before the boot: (e) Put the stick straight into the unit (no hub) and see which physical port holds it',
    '(a) After the stick has left the Mac (otherwise its removal shows as USB_REMOVED), start the Mac-side logger: python3 tools/sp11_mac_usb_watch.py '
    '(add --allow-empty-usb if the Mac has no other USB device), as for A1',
    'Keep the Mac awake and unlocked, and the logger running, through (c)',
    '(b) Film as before, at 60 frames per second if the phone allows', 'Say on the video which physical port holds the stick',
    'After a black-out: (c) applies only to a black panel before any VERDICT or END line',
    'A logo or boot screen means that the unit restarted: skip (c), say the time, the last line of the page and the last UPTIME shown',
    'A lit page that does not change for 30 s after a NEXT line: skip (c), say the last line',
    'say the Mac\'s clock on the video and touch no cable for 4 minutes (a payload that is alive in the dark runs on for up to about 200 s, and a running xHC '
    'writes event TRBs that a moved cable or stick would change)',
    'force the unit off at once, as below, only if it feels hot, and then skip the rest of (c)',
    'After the 4 minutes, unplug the stick (hazard (m) says why that is safe) and plug the Mac\'s cable into THAT port (move it from the free port)',
    'Wait 60 s and read the logger and System Information for any NEW USB device: vendor 05C6 (Qualcomm), for example product 9008 (its download mode; a Mac '
    'may list it as QHSUSB__BULK or QUSB__BULK), or any device you do not know',
    'macOS enumerates any device by itself; add nothing to that: no driver, no flashing or Terminal tool, no Disk Utility; do not open, mount, initialise, '
    'erase or copy from any disk that appears (eject it unopened) and accept no app, driver, profile or Photos prompt',
    'If macOS asks "Allow accessory to connect", choose Allow once (INFERRED: it lets macOS read the descriptors only, while Don\'t Allow would blind the '
    'logger) and say it on the video',
    'Report what the logger and System Information show (sanitise both: they list the Mac\'s own accessories)',
    'Then force the unit off: hold Volume-Up and Power together for about 15 s until the screen stays off (the Surface logo may flash: keep holding), then '
    'release both',
    'Holding Power alone for about 20 s restarts the Surface to its logo instead (Microsoft\'s "Force a shutdown and restart your Surface"); that is also '
    'acceptable, Windows may then start: shut it down from Windows',
    'Optional: (d) Only if the charger is not on a USB-C port: an inline power meter on the charger; note the draw while the screen is dark, and after the '
    'power-off if you can')
# Wording that must not come back: the event is "the screen went off", never a reset or a restart (boot 15's "ran to END" is quoted in the text as the
# operator's first answer, so only the claim that it did is withdrawn), and the old power-off, download-mode and "send nothing" sentences are replaced.
SUPPLEMENT_WITHDRAWN = ('the board reset', 'the unit reset', 'the SoC reset', 'reset itself', 'rebooted', 'shut itself down', 'boot 15 ran to END', 'may be on neither',
                        'crashed', 'bricked', 'REPORTED', 'holding Power for about 20 s.', 'we send nothing', 'QDLoader', 'powered it off by hand', 'Note the time',
                        'applies as written', 'the rings that the firmware\'s driver built')
# The numbers the supplement repeats from the A1 record: each must be in both texts.
SHARED_WITH_A1 = ('0xCF412000', 'four pages', '30 s + 20 x 8 s = 190 s plus at most 10.2 s', 'about 200 s', 'window 0x0a600000', 'SMMU stream 0x1420',
                  'after a success at arena 4 from one CHG line on, at arena 3 from two, at arena 2 from three, never at arena 1',
                  'SLBOUNCE-DEVHE-MARK.EFI', 'Q1N1-PAYLOAD.EFI', 'PAYLOAD.MAN lists exactly one payload, the one of the boot')


def supplement_text_problems(text):
    """What the supplement text lacks (a required phrase, E0 included) or has that was withdrawn."""
    flat = ' '.join(text.split())
    return ([f'missing: {phrase}' for phrase in SUPPLEMENT_PHRASES + E0_PHRASES if phrase not in flat] +
            [f'withdrawn wording is back: {phrase}' for phrase in SUPPLEMENT_WITHDRAWN if phrase in flat])


def citation_problems(runbook_text, stick_log_text):
    """The places the supplement cites in the runbook and in the stick log, which must be there under those names."""
    problems = [f'the runbook has no {heading!r}' for heading in ('## 2. What A1 covers and what it does not', '### 4.3 After the run', '### 4.4 Stop rules',
                                                                   '## 5. The expected page', '### 6.2 The refusals (nothing written)',
                                                                   '## 7. What the live link needs beyond A1', '## 10. What stays UNKNOWN') if heading not in runbook_text]
    problems += [f'the runbook has no row O{k}' for k in range(1, 16) if not re.search(rf'(?m)^\| O{k} \|', runbook_text)]
    if not re.search(r'(?m)^\| O10 \|.*No further xHC write until it is understood\.', runbook_text):
        problems.append('O10 of the runbook no longer says "No further xHC write until it is understood."')
    if not re.search(r'(?m)^\| O11 \|.*a `WHY=` of `READ`, `CLOCK`, `STORE`, `BUDGET` or `LINES`', runbook_text):
        problems.append('O11 of the runbook no longer lists STORE among the WHY= values')
    if 'S0 saw HCE on `usb_1_ss0` behind a hub' not in runbook_text:
        problems.append('the runbook (6.2) no longer says that S0 saw HCE on usb_1_ss0 behind a hub')
    if 'XHCI0.BIN` (the plan `a1-xhci-s1-ss0`) is run only after the page of `XHCI1.BIN` was read and the operator named the second file' not in runbook_text:
        problems.append('4.3 of the runbook no longer says when XHCI0.BIN is run')
    flat_runbook = ' '.join(runbook_text.split())
    problems += [f'the runbook no longer says {sentence!r}, which hazards (k) and (n) and the outcome tree of the supplement replace or extend'
                 for sentence in ('the unit\'s are not recorded (`0x2000` and `0x1000`', 'because the unit\'s are not recorded', 'their meaning is UNKNOWN',
                                  'the unit\'s DBOFF, RTSOFF and HCCPARAMS1.PPC (not recorded)',
                                  'A board restart is recorded with the `UPTIME` it happened at, and a restart alone is not an xHC result')
                 if sentence not in flat_runbook]
    problems += [f'the stick log has no {heading!r}' for heading in ('## 10. Pinned sources', '## 12. Result of the S0 survey', '## 13. Result of S0b',
                                                                      '### 14.2 Logging, the stick', '## 16. Result of boots 15 and 16') if heading not in stick_log_text]
    flat_log = ' '.join(stick_log_text.split())
    problems += [f'the stick log no longer says {sentence!r}, which section 3 of the supplement cites'
                 for sentence in ('his first answer was that it ran to END', 'When and how it was powered off afterwards are not recorded', 'no restart was seen')
                 if sentence not in flat_log]
    if 'drivers/iommu/arm/arm-smmu/arm-smmu.h' not in stick_log_text:
        problems.append('the stick log no longer pins arm-smmu.h, which hazard (k) of the supplement cites as read at the pinned Linux revision')
    return problems


def stick_log_at(commit):
    """docs/SP11-STICK-LOG.md as it is at `commit` of this repository (git show: read only), or None when git or the commit is not there."""
    try:
        done = subprocess.run(['git', '-C', str(ROOT), 'show', f'{commit}:docs/SP11-STICK-LOG.md'], capture_output=True, text=True, encoding='utf-8', check=False)
    except OSError:
        return None
    return done.stdout if done.returncode == 0 else None


def supplement_problems(module):
    """What a generator `module` (the real one or a mutated copy) gets wrong about the supplement, through its public functions: the pinned facts of the A1
    record, the text for the A1 record's own files, the refusals (each for its own reason, below) and the byte-for-byte regeneration of the A1 text.
    Builds its inputs from `module`'s own classes, and compares with the approved values copied above."""
    x1, x0 = module.PayloadFile(1, *APPROVED_XHCI1), module.PayloadFile(0, *APPROVED_XHCI0)
    runbook = module.RunbookFile(*APPROVED_RUNBOOK)
    approved = module.A1Facts(x1, x0, runbook, *APPROVED_RECORD)
    problems = []
    if module.A1 != approved:
        problems.append('the pinned facts of the A1 record are not the approved ones')
    if module.digest([x1, x0], runbook)[1:] != APPROVED_RECORD:
        problems.append('the A1 record does not regenerate byte for byte')
    try:
        text = module.render_supplement(x0, runbook)
    except module.RecordError as error:
        return problems + [f'the supplement was refused for the A1 record\'s own files: {error}']
    problems += supplement_text_problems(text)
    for label, digest_, line in (('XHCI0.BIN', APPROVED_XHCI0[1], rf'(?m)^   XHCI0\.BIN +sha256 {APPROVED_XHCI0[1]}$'),
                                 ('XHCI1.BIN', APPROVED_XHCI1[1], rf'(?m)^   XHCI1\.BIN +sha256 {APPROVED_XHCI1[1]}$'),
                                 ('the runbook', APPROVED_RUNBOOK[1], rf'(?m)^   docs/SP11-XHCI-S1\.md +sha256 {APPROVED_RUNBOOK[1]}$'),
                                 ('the A1 record', APPROVED_RECORD[1], rf'(?m)^   the A1 record +sha256 {APPROVED_RECORD[1]}$')):
        if text.count(digest_) != 1 or not re.search(line, text):
            problems.append(f'the digest line of {label} is not there exactly once')
    refused_as_xhci1 = 'XHCI1.BIN is the file of boots 15 and 16'
    refused_as_not_the_a1_xhci0 = 'XHCI0.BIN must be the file of the A1 record'
    refused_as_not_a_payload_file = 'XHCI0.BIN: a size of 1 to 65536 bytes'
    refused_as_not_a_runbook_file = 'the runbook: a size of 1 to 1048576 bytes'
    refused_as_not_the_a1_runbook = 'the runbook must be the one the A1 record pins'
    refused_as_drift = 'no longer regenerates byte for byte'
    # Each refusal is held to its own message: a check that is missing leaves the case to the next check, which refuses it for another reason.
    for label, call, reason in (
            ('XHCI1.BIN as the payload', lambda: module.render_supplement(x1, runbook), refused_as_xhci1),
            ('the digest of XHCI1.BIN under controller 0', lambda: module.render_supplement(module.PayloadFile(0, *APPROVED_XHCI1), runbook), refused_as_xhci1),
            ('the digest of XHCI0.BIN under controller 1', lambda: module.render_supplement(module.PayloadFile(1, *APPROVED_XHCI0), runbook), refused_as_xhci1),
            ('an empty XHCI0.BIN', lambda: module.render_supplement(module.PayloadFile(0, 0, sha(b'')), runbook), refused_as_not_a_payload_file),
            ('an XHCI0.BIN with an upper-case digest', lambda: module.render_supplement(module.PayloadFile(0, APPROVED_XHCI0[0], APPROVED_XHCI0[1].upper()), runbook),
             refused_as_not_a_payload_file),
            ('another XHCI0.BIN', lambda: module.render_supplement(module.PayloadFile(0, APPROVED_XHCI0[0], sha(b'another')), runbook), refused_as_not_the_a1_xhci0),
            ('a XHCI0.BIN of another size', lambda: module.render_supplement(module.PayloadFile(0, APPROVED_XHCI0[0] + 1, APPROVED_XHCI0[1]), runbook),
             refused_as_not_the_a1_xhci0),
            ('no payload', lambda: module.render_supplement(None, runbook), 'boot 17: the payload is XHCI0.BIN'),
            ('an empty runbook', lambda: module.render_supplement(x0, module.RunbookFile(0, sha(b''))), refused_as_not_a_runbook_file),
            ('no runbook', lambda: module.render_supplement(x0, None), refused_as_not_a_runbook_file),
            ('another runbook', lambda: module.render_supplement(x0, module.RunbookFile(APPROVED_RUNBOOK[0], sha(b'another'))), refused_as_not_the_a1_runbook),
            ('a runbook of another size', lambda: module.render_supplement(x0, module.RunbookFile(APPROVED_RUNBOOK[0] + 1, APPROVED_RUNBOOK[1])),
             refused_as_not_the_a1_runbook),
            ('an A1 record that does not regenerate to its pinned digest',
             lambda: module.render_supplement(x0, runbook, dataclasses.replace(approved, record_sha256=sha(b'drift'))), refused_as_drift),
            ('an A1 record that does not regenerate to its pinned size',
             lambda: module.render_supplement(x0, runbook, dataclasses.replace(approved, record_bytes=APPROVED_RECORD[0] + 1)), refused_as_drift)):
        try:
            call()
        except module.RecordError as error:
            if reason not in str(error):
                problems.append(f'{label} was refused for another reason than {reason!r}: {error}')
            continue
        problems.append(f'{label} was accepted')
    return problems


class Supplement(unittest.TestCase):
    """The supplement for boot 17: its text, its pins, its refusals, and the guarantee that the A1 record is the same with it as without it."""

    def render(self):
        return rec.render_supplement(rec.A1.xhci0, rec.A1.runbook)

    def test_the_pinned_facts_are_the_approved_a1_record(self):
        self.assertEqual(rec.A1, rec.A1Facts(rec.PayloadFile(1, *APPROVED_XHCI1), rec.PayloadFile(0, *APPROVED_XHCI0), rec.RunbookFile(*APPROVED_RUNBOOK),
                                             *APPROVED_RECORD))

    def test_the_a1_record_regenerates_byte_for_byte_from_its_pinned_inputs(self):
        text, size, digest = rec.digest([rec.A1.xhci1, rec.A1.xhci0], rec.A1.runbook)
        self.assertEqual((size, digest), APPROVED_RECORD, 'the A1 text changed: the operator approved the text whose digest this is')
        self.assertEqual(len(text.splitlines()), 228)

    def test_the_repositorys_runbook_is_the_one_the_a1_record_pins(self):
        self.assertEqual(rec.runbook_file(rec.DEFAULT_RUNBOOK), rec.A1.runbook, 'the runbook changed: the A1 record and the supplement both pin its bytes')

    def test_the_text_is_deterministic_ascii_and_within_the_width(self):
        first, second = self.render(), self.render()
        self.assertEqual(first, second)
        self.assertTrue(first.isascii())
        self.assertLessEqual(max(len(line) for line in first.splitlines()), rec.WIDTH)
        text, size, digest = rec.supplement_digest(rec.A1.xhci0, rec.A1.runbook)
        self.assertEqual((text, size, digest), (first, len(first.encode('ascii')), rec.sha256_text(first)))

    def test_the_digest_of_the_supplement_is_pinned(self):
        _, size, digest = rec.supplement_digest(rec.A1.xhci0, rec.A1.runbook)
        self.assertEqual((size, digest), (SUPPLEMENT_PIN_BYTES, SUPPLEMENT_PIN_SHA256),
                         'the text changed: the digest the operator approves changed with it; update the pin and say why')

    def test_the_a1_text_is_the_same_with_the_supplement_mode_present(self):
        text = rec.render(EXAMPLE, EXAMPLE_RUNBOOK)
        self.assertEqual((len(text.encode()), rec.sha256_text(text)), (PIN_BYTES, PIN_SHA256))
        self.assertNotIn('SUPPLEMENT', text)
        self.assertNotIn('boot 17', text)

    def test_the_text_says_everything_the_supplement_approves_amends_and_keeps(self):
        self.assertEqual(supplement_text_problems(self.render()), [])

    def test_each_phrase_has_a_faulty_text_it_rejects(self):
        flat = ' '.join(self.render().split())
        for phrase in SUPPLEMENT_PHRASES + E0_PHRASES:
            with self.subTest(phrase=phrase[:60]):
                self.assertEqual(supplement_text_problems(flat.replace(phrase, 'X' * len(phrase))), [f'missing: {phrase}'])

    def test_no_phrase_holds_another_so_that_each_faulty_text_breaks_one_phrase(self):
        phrases = SUPPLEMENT_PHRASES + E0_PHRASES
        self.assertEqual(len(set(phrases)), len(phrases))
        for first in phrases:
            for second in phrases:
                if first is not second:
                    self.assertNotIn(first, second, f'{first[:40]!r} is inside {second[:40]!r}')

    def test_the_withdrawn_wording_is_rejected_when_it_comes_back(self):
        flat = ' '.join(self.render().split())
        for phrase in SUPPLEMENT_WITHDRAWN:
            with self.subTest(back=phrase):
                self.assertEqual(supplement_text_problems(flat + ' ' + phrase), [f'withdrawn wording is back: {phrase}'])

    def test_the_waiver_is_for_boot_17_only_and_names_the_rule_and_the_second_file(self):
        flat = ' '.join(self.render().split())
        waiver = 'This record waives that stop, for boot 17 only'
        self.assertEqual(flat.count(waiver), 1)
        for label, faulty in (('no waiver', flat.replace(waiver, 'This record keeps that stop')), ('no rule named', flat.replace('amends rule 9 of the A1 record', 'amends a rule')),
                              ('a waiver without the second file named', flat.replace('the operator names XHCI0.BIN as the second file', 'the operator names a file')),
                              ('a waiver for more than one boot', flat.replace(waiver, 'This record waives that stop, for the boots that follow')),
                              ('a waiver that is not limited to the page of boot 16', flat.replace('The waiver covers that page and nothing else',
                                                                                                    'The waiver covers the sequence')),
                              ('a waiver that hides that the page of boot 15 was not read',
                               flat.replace('boot 15\'s page was not read, and this record takes boot 16\'s page as the page of XHCI1.BIN', 'both pages were read'))):
            with self.subTest(case=label):
                self.assertTrue(supplement_text_problems(faulty), label)

    def test_the_extra_actions_fit_one_screen_and_are_in_the_operators_section_in_the_order_of_the_work(self):
        text = self.render()
        self.assertLessEqual(len(rec.E0), 36)
        block = '\n'.join(rec.E0)
        self.assertEqual(text.count(block), 1)
        section_8 = text.split("\n8. THE OPERATOR'S ACTIONS")[1].split('\n9. NOT AUTHORISED')[0]
        self.assertIn(block, section_8)
        marks = [line[3:] for line in rec.E0 if re.match(r'   (\([a-e]\) |Before the boot:|After a black-out:|Optional:)', line)]
        self.assertEqual([mark.split(' ')[0] for mark in marks], ['Before', '(e)', '(a)', '(b)', 'After', '(c)', 'Optional:', '(d)'],
                         'before the boot (e), then (a) after the stick has left the Mac, then (b); after a black-out (c); optional (d)')
        for line in rec.E0:
            self.assertLessEqual(len(line), 118)

    def test_the_record_stays_compact(self):
        self.assertLessEqual(len(self.render().splitlines()), 270)

    def test_the_numbers_the_supplement_repeats_are_those_of_the_a1_record(self):
        a1_text = ' '.join(rec.digest([rec.A1.xhci1, rec.A1.xhci0], rec.A1.runbook)[0].split())
        flat = ' '.join(self.render().split())
        for snippet in SHARED_WITH_A1:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, a1_text)
                self.assertIn(snippet, flat)

    def test_the_page_digits_are_the_first_sixteen_of_the_payloads_digest(self):
        self.assertEqual(APPROVED_XHCI0[1][:16].upper(), '63B11F901ACBB430')
        flat = ' '.join(self.render().split())
        self.assertEqual(flat.count('63B11F901ACBB430'), 6, 'the approval sentence, section 2, section 4, section 7 and twice in section 8')
        self.assertEqual(flat.count('PAYLOAD CALL 0 63B11F901ACBB430'), 3, 'sections 2, 7 and 8: the line that says which boot is boot 17')
        self.assertEqual(flat.count(APPROVED_XHCI0[1][:16]), 1, 'the lower-case digits are only in the digest line')

    def test_the_stick_log_is_cited_as_of_the_commit_that_has_the_places_it_cites(self):
        self.assertEqual(rec.STICK_LOG_COMMIT, '42017e2')
        self.assertEqual(rec.DIGEST_COMMAND, 'python3 tools/sp11_xhci_s1_record.py --supplement --xhci0 XHCI0.BIN --digest')
        log = stick_log_at(rec.STICK_LOG_COMMIT)
        if log is None:
            self.skipTest(f'git or commit {rec.STICK_LOG_COMMIT} is not available here: the stick log at that commit is not checked')
        runbook = (ROOT / 'docs' / 'SP11-XHCI-S1.md').read_text(encoding='utf-8')
        self.assertEqual(citation_problems(runbook, log), [])
        self.assertIn('FORMAT', log, 'the commit the record cites is the one that holds the correction of the reading of FSR 0x400')

    def test_the_places_it_cites_are_in_the_runbook_and_the_stick_log(self):
        runbook, log = (ROOT / 'docs' / 'SP11-XHCI-S1.md').read_text(encoding='utf-8'), (ROOT / 'docs' / 'SP11-STICK-LOG.md').read_text(encoding='utf-8')
        self.assertEqual(citation_problems(runbook, log), [])
        for label, faulty_runbook, faulty_log in (('no 4.3', runbook.replace('### 4.3 After the run', '### 4.3 Afterwards'), log),
                                                   ('no O10 rule', runbook.replace('No further xHC write until it is understood.', 'Nothing.'), log),
                                                   ('no O7 row', runbook.replace('| O7 |', '| X7 |'), log),
                                                   ('no STORE in O11', runbook.replace('`CLOCK`, `STORE`, `BUDGET`', '`CLOCK`, `BUDGET`'), log),
                                                   ('no "unit\'s are not recorded" in section 2', runbook.replace('the unit\'s are not recorded (`0x2000`', 'the unit\'s are known (`0x2000`'), log),
                                                   ('no "meaning is UNKNOWN" in section 5', runbook.replace('their meaning is UNKNOWN', 'their meaning is known'), log),
                                                   ('no section 10', runbook.replace('## 10. What stays UNKNOWN', '## 10. What stays'), log),
                                                   ('no restart sentence in 4.3', runbook.replace('and a restart alone is not an xHC result', 'and a restart is a result'), log),
                                                   ('no section 16', runbook, log.replace('## 16. Result of boots 15 and 16', '## 16. Something else')),
                                                   ('no first answer of boot 15', runbook, log.replace('his first answer was that it ran to END', 'he said nothing')),
                                                   ('no power-off sentence', runbook, log.replace('When and how it was powered off afterwards are not recorded',
                                                                                                    'It was powered off at noon')),
                                                   ('no pin of arm-smmu.h', runbook, log.replace('drivers/iommu/arm/arm-smmu/arm-smmu.h', 'drivers/x.h'))):
            with self.subTest(case=label):
                self.assertTrue(citation_problems(faulty_runbook, faulty_log), label)

    def test_the_supplement_is_refused_for_any_other_file_runbook_or_a1_text(self):
        self.assertEqual(supplement_problems(rec), [])

    def test_a_line_over_the_width_or_not_ascii_is_refused(self):
        original = rec.WIDTH
        try:
            rec.WIDTH = 100
            with self.assertRaises(rec.RecordError):
                self.render()
        finally:
            rec.WIDTH = original
        original_note = rec.OPERATOR_APPROVAL
        try:
            rec.OPERATOR_APPROVAL = '2026-11-17 02:10 \u00e9'
            with self.assertRaises(rec.RecordError):
                self.render()
        finally:
            rec.OPERATOR_APPROVAL = original_note

    def test_the_text_names_no_machine_and_no_path(self):
        text = self.render()
        pattern = re.compile('|'.join(['/Us' + 'ers/', '/ho' + 'me/', '/Vol' + 'umes/']) + r'|\bsae' + r'ed\b|host' + r'name|\b[0-9a-f]{2}(?::[0-9a-f]{2}){5}\b', re.I)
        self.assertIsNone(pattern.search(text))
        self.assertLess(len(text.encode()), 50 * 1024)

    def test_the_plan_of_boot_17_is_the_second_plan_of_the_plan_module(self):
        import sp11_xhci_s1_plan as plan_mod
        self.assertEqual(rec.PLAN_NAME_SS0, 'a1-xhci-s1-ss0')
        self.assertEqual(plan_mod.PLAN_NAMES, {1: rec.PLAN_NAME, 0: rec.PLAN_NAME_SS0})
        self.assertEqual(rec.staging_command({rec.SLBOUNCE_NAME: ('a', 'b'), rec.RUNNER_NAME: ('c', 'd')}, rec.PLAN_NAME_SS0)[3:5], ['--name', rec.PLAN_NAME_SS0])
        self.assertEqual(rec.staging_command({rec.SLBOUNCE_NAME: ('a', 'b'), rec.RUNNER_NAME: ('c', 'd')})[3:5], ['--name', rec.PLAN_NAME], 'the A1 default is unchanged')


class SupplementCli(unittest.TestCase):
    """`python3 tools/sp11_xhci_s1_record.py --supplement --xhci0 <file> [--runbook <file>] [--digest]`: reads the files, writes nothing."""

    def setUp(self):
        self.dir = TOOLS.parent / 'build' / 'sp11-xhci-s1-supplement-cli-test'
        shutil.rmtree(self.dir, ignore_errors=True)
        self.dir.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.one, self.zero, self.runbook = self.dir / 'xhci1.bin', self.dir / 'xhci0.bin', self.dir / 'runbook.md'
        self.one.write_bytes(b'example XHCI1.BIN' * 100)
        self.zero.write_bytes(b'example XHCI0.BIN' * 100)
        self.runbook.write_bytes(b'# an example runbook\n' * 50)
        self.addCleanup(setattr, rec, 'A1', rec.A1)

    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = rec.main([str(a) for a in argv])
        return status, out.getvalue(), err.getvalue()

    def pretend_the_example_files_are_the_approved_ones(self):
        """A1 facts whose files are the example files, with the A1 text they give: the code path of the real facts, on files this test can write."""
        one, zero, runbook = rec.payload_file(1, self.one), rec.payload_file(0, self.zero), rec.runbook_file(self.runbook)
        _, size, digest = rec.digest([one, zero], runbook)
        rec.A1 = rec.A1Facts(one, zero, runbook, size, digest)
        return rec.A1

    def test_the_text_and_the_digest_are_those_of_the_files_given(self):
        facts = self.pretend_the_example_files_are_the_approved_ones()
        status, out, err = self.run_main('--supplement', '--xhci0', self.zero, '--runbook', self.runbook)
        self.assertEqual((status, err), (0, ''))
        self.assertEqual(out, rec.render_supplement(facts.xhci0, facts.runbook, facts))
        status, out, err = self.run_main('--supplement', '--xhci0', self.zero, '--runbook', self.runbook, '--digest')
        _, size, digest = rec.supplement_digest(facts.xhci0, facts.runbook, facts)
        self.assertEqual((status, out, err), (0, f'{size} {digest}\n', ''))

    def test_each_file_the_a1_record_did_not_name_is_refused_for_its_own_reason_with_nothing_on_standard_output(self):
        self.pretend_the_example_files_are_the_approved_ones()
        (self.dir / 'empty.bin').write_bytes(b'')
        (self.dir / 'big.bin').write_bytes(b'x' * 65537)
        (self.dir / 'another.bin').write_bytes(b'another XHCI0.BIN' * 100)
        (self.dir / 'another.md').write_bytes(b'# another runbook\n' * 50)
        (self.dir / 'empty.md').write_bytes(b'')
        for label, argv, reason in (
                ('another XHCI0.BIN', ('--xhci0', self.dir / 'another.bin', '--runbook', self.runbook), 'XHCI0.BIN must be the file of the A1 record'),
                ('XHCI1.BIN as the payload', ('--xhci0', self.one, '--runbook', self.runbook), 'XHCI1.BIN is the file of boots 15 and 16'),
                ('an empty XHCI0.BIN', ('--xhci0', self.dir / 'empty.bin', '--runbook', self.runbook), 'XHCI0.BIN: a size of 1 to 65536 bytes'),
                ('an XHCI0.BIN over 64 KiB', ('--xhci0', self.dir / 'big.bin', '--runbook', self.runbook), 'XHCI0.BIN: a size of 1 to 65536 bytes'),
                ('another runbook', ('--xhci0', self.zero, '--runbook', self.dir / 'another.md'), 'the runbook must be the one the A1 record pins'),
                ('an empty runbook', ('--xhci0', self.zero, '--runbook', self.dir / 'empty.md'), 'the runbook: a size of 1 to 1048576 bytes'),
                ('a missing XHCI0.BIN', ('--xhci0', self.dir / 'absent.bin', '--runbook', self.runbook), 'absent.bin'),
                ('a missing runbook', ('--xhci0', self.zero, '--runbook', self.dir / 'absent.md'), 'absent.md')):
            with self.subTest(case=label):
                status, out, err = self.run_main('--supplement', *argv)
                self.assertEqual((status, out), (1, ''))
                self.assertTrue(err.startswith('refused: '), err)
                self.assertIn(reason, err)

    def test_the_example_files_are_not_the_a1_records_when_the_real_facts_are_pinned(self):
        status, out, err = self.run_main('--supplement', '--xhci0', self.zero, '--runbook', self.runbook)
        self.assertEqual((status, out), (1, ''))
        self.assertIn('XHCI0.BIN must be the file of the A1 record', err)

    def test_the_options_are_checked(self):
        for label, argv in (('--supplement without --xhci0', ('--supplement',)), ('--supplement with --xhci1', ('--supplement', '--xhci0', self.zero, '--xhci1', self.one)),
                            ('no --xhci1 without --supplement', ('--xhci0', self.zero))):
            with self.subTest(case=label), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    rec.main([str(a) for a in argv])
                self.assertEqual(caught.exception.code, 2)

    def test_the_default_runbook_is_the_one_the_supplement_pins(self):
        self.pretend_the_example_files_are_the_approved_ones()
        status, _, err = self.run_main('--supplement', '--xhci0', self.zero)
        self.assertEqual(status, 1, 'the example facts pin the example runbook, not the repository\'s own')
        self.assertIn('the runbook must be the one the A1 record pins', err)
        rec.A1 = rec.A1Facts(rec.A1.xhci1, rec.A1.xhci0, rec.runbook_file(rec.DEFAULT_RUNBOOK), 0, 'x')
        status, _, err = self.run_main('--supplement', '--xhci0', self.zero)
        self.assertEqual(status, 1)
        self.assertIn('regenerate', err, 'the default runbook is the repository\'s: the files pass, and the A1 pin is what refuses')


# Each mutation of the generator's source: a name, the text it replaces (once) and its replacement, and what the checks must say. Each is a way to render a
# supplement that looks right and is not, or to accept a file that is not the one approved.
SUPPLEMENT_MUTATIONS = (
    ('prints-the-digest-of-xhci1-for-xhci0', "f'   {name:<24} sha256 {payload.sha256}'", "f'   {name:<24} sha256 {a1.xhci1.sha256}'",
     'the digest line of XHCI0.BIN is not there exactly once'),
    ('accepts-any-xhci0-file', '    if payload != a1.xhci0:\n', '    if False:\n', 'another XHCI0.BIN was accepted'),
    ('accepts-any-runbook', '    if runbook != a1.runbook:\n', '    if False:\n', 'another runbook was accepted'),
    ('does-not-check-the-a1-text', '    if (size, sha) != (a1.record_bytes, a1.record_sha256):\n', '    if False:\n',
     'an A1 record that does not regenerate to its pinned digest was accepted'),
    ('changes-a-word-of-the-a1-text', 'at most 9 pages, the guard page', 'at most 8 pages, the guard page', 'the A1 record does not regenerate byte for byte'),
    # each refusal for its own reason: with one check gone the case is left to the next check, which refuses it with another message
    ('skips-the-payload-format-check', "    if not 1 <= payload.size <= 65536 or not re.fullmatch(r'[0-9a-f]{64}', payload.sha256):\n", '    if False:\n',
     'an empty XHCI0.BIN was refused for another reason than'),
    ('skips-the-runbook-format-check', '    check_runbook(runbook)\n    if runbook != a1.runbook:\n', '    if runbook != a1.runbook:\n',
     'an empty runbook was refused for another reason than'),
    ('accepts-xhci1-by-its-digest', ' or (payload.size, payload.sha256) == (a1.xhci1.size, a1.xhci1.sha256)', '',
     'the digest of XHCI1.BIN under controller 0 was refused for another reason than'),
    ('accepts-controller-1', 'payload.controller != 0 or ', '', 'the digest of XHCI0.BIN under controller 1 was refused for another reason than'),
    # the load-bearing sentences of the text
    ('drops-the-waiver', 'waives that stop, for boot 17 only', 'keeps that stop for boot 17', 'missing: This record waives that stop, for boot 17 only'),
    ('drops-the-two-variables-sentence', 'Boot 17 changes TWO things from boot 16', 'Boot 17 changes the controller',
     'missing: Boot 17 changes TWO things from boot 16'),
    ('drops-the-advice-only-sentence', 'is advice for a later request', 'is the plan for the next boot', 'missing: is advice for a later request and authorises nothing'),
    ('drops-the-not-boot-17-rule', 'did not run the payload and is not boot 17: delete the marker and start again', 'did not run the payload: start again',
     'missing: a boot that did not print PAYLOAD CALL 0 63B11F901ACBB430 did not run the payload and is not boot 17'),
    ('drops-the-spent-rule', 'once that line has printed, the approval is spent', 'once that line has printed, go on',
     'missing: once that line has printed, the approval is spent'),
    ('drops-the-missed-cue-rule', 'power off in the 8 s after NEXT ARENA', 'carry on after NEXT ARENA',
     "missing: If you did not plug the Mac's cable by WATCH DONE, power off in the 8 s after NEXT ARENA"),
    ('drops-store-from-the-why-list', 'a WHY= of READ, CLOCK, STORE, BUDGET or LINES', 'a WHY= of READ, CLOCK, BUDGET or LINES',
     'missing: a WHY= of READ, CLOCK, STORE, BUDGET or LINES'),
    ('drops-the-boot-15-contradiction', 'his first answer was that it ran to END, a later one that both boots ', 'he says that both boots ',
     'missing: OBSERVED, boot 15 (by the operator, from memory only, not filmed; his first answer'),
    ('quotes-the-reset-again', 'No restart was seen.', 'No restart was seen, though the unit reset itself.', 'withdrawn wording is back: reset itself'),
    ('cites-the-stick-log-without-a-commit', 'is cited as of commit {STICK_LOG_COMMIT} of the Snapintosh fork', 'is cited',
     'missing: docs/SP11-STICK-LOG.md is cited as of commit 42017e2'),
    ('drops-the-download-mode-sentence', 'Connecting the Mac to a download-mode device is read-only on our side', 'Connecting the Mac to a download-mode device is fine',
     'missing: Connecting the Mac to a download-mode device is read-only on our side'),
    # the extra actions of the operator
    ('drops-the-four-minute-wait', 'touch no cable for 4 minutes', 'touch no cable for 4 seconds',
     "missing: say the Mac's clock on the video and touch no cable for 4 minutes"),
    ('drops-the-force-off-wording', 'hold Volume-Up and Power together for about 15 s', 'hold Power for about 20 s',
     'missing: Then force the unit off: hold Volume-Up and Power together for about 15 s'),
    ('drops-the-allow-once-sentence', 'choose Allow once', 'choose Allow always', 'missing: If macOS asks "Allow accessory to connect", choose Allow once'),
    # the pins of the A1 record
    ('pins-another-xhci0', "PayloadFile(0, 19870, '63b11f901acbb4300dcd49a809841ccb5c1f626c777166547dc9967693f81e7f')",
     "PayloadFile(0, 19870, '63b11f901acbb4300dcd49a809841ccb5c1f626c777166547dc9967693f81e70')", 'the pinned facts of the A1 record are not the approved ones'),
    ('leaves-the-xhci0-digest-out', "f'   {name:<24} sha256 {payload.sha256}'", "f'   {name:<24}'", 'the digest line of XHCI0.BIN is not there exactly once'),
    ('pins-another-runbook', "RunbookFile(51154, '88d96c587f493b3a83683cffa2c4e24eb8bb0468a649a6a890feb5a373ab319d')",
     "RunbookFile(51154, '88d96c587f493b3a83683cffa2c4e24eb8bb0468a649a6a890feb5a373ab319e')", 'the pinned facts of the A1 record are not the approved ones'),
    ('pins-another-a1-record-digest', "21751, '6e21c40a0a0938860cf48d30be4a1a3e5ac398c94b511d322fe8791538e8f1ce')",
     "21751, '6e21c40a0a0938860cf48d30be4a1a3e5ac398c94b511d322fe8791538e8f1cf')", 'the pinned facts of the A1 record are not the approved ones'),
)


class SupplementMutants(unittest.TestCase):
    """The checks of the supplement are not vacuous: a faithful copy of the generator passes `supplement_problems`, and each mutated copy fails it."""

    def setUp(self):
        self.dir = TOOLS.parent / 'build' / 'sp11-xhci-s1-supplement-mutants-test'
        shutil.rmtree(self.dir, ignore_errors=True)
        (self.dir / 'tools').mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.source = Path(rec.__file__).read_text(encoding='utf-8')

    def copy_of_the_generator(self, name, source):
        path = self.dir / 'tools' / f'{name}.py'
        path.write_text(source, encoding='utf-8')
        spec = importlib.util.spec_from_file_location(f'supplement_copy_{name}', path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module      # a dataclass looks its module up by name
        self.addCleanup(sys.modules.pop, spec.name, None)
        spec.loader.exec_module(module)
        return module

    def test_a_faithful_copy_passes_so_that_the_mutants_fail_for_their_own_fault(self):
        self.assertEqual(supplement_problems(self.copy_of_the_generator('unmutated', self.source)), [])

    def test_each_way_of_rendering_a_wrong_supplement_is_caught_for_its_own_fault(self):
        for name, old, new, expected in SUPPLEMENT_MUTATIONS:
            with self.subTest(mutation=name):
                self.assertEqual(self.source.count(old), 1, 'the mutation applies exactly once')
                found = supplement_problems(self.copy_of_the_generator(name.replace('-', '_'), self.source.replace(old, new)))
                self.assertTrue(any(expected in problem for problem in found), f'{name}: {expected!r} not among {found}')


@unittest.skipUnless(PAYLOADS_DIR and (PAYLOADS_DIR / 'xhci0.bin').is_file() and (PAYLOADS_DIR / 'xhci1.bin').is_file(),
                     'Q1N1_SP11_TREE (or Q1N1_SP11_PAYLOADS) does not name built xhci0.bin and xhci1.bin: the files are not compared with the A1 record')
class AgainstTheBuiltPayloads(unittest.TestCase):
    def test_the_built_files_are_those_the_a1_record_names(self):
        self.assertEqual(rec.payload_file(1, PAYLOADS_DIR / 'xhci1.bin'), rec.A1.xhci1)
        self.assertEqual(rec.payload_file(0, PAYLOADS_DIR / 'xhci0.bin'), rec.A1.xhci0)

    def test_the_a1_record_and_the_supplement_regenerate_from_the_built_files_and_the_repositorys_runbook(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(rec.main(['--xhci1', str(PAYLOADS_DIR / 'xhci1.bin'), '--xhci0', str(PAYLOADS_DIR / 'xhci0.bin'), '--digest']), 0)
            self.assertEqual(rec.main(['--supplement', '--xhci0', str(PAYLOADS_DIR / 'xhci0.bin'), '--digest']), 0)
        first, second = out.getvalue().splitlines()
        self.assertEqual(first, '{} {}'.format(*APPROVED_RECORD))
        self.assertEqual(second, f'{SUPPLEMENT_PIN_BYTES} {SUPPLEMENT_PIN_SHA256}')
        self.assertEqual(err.getvalue(), '')


if __name__ == '__main__':
    sys.exit(0 if unittest.main(verbosity=1, exit=False).result.wasSuccessful() else 1)
