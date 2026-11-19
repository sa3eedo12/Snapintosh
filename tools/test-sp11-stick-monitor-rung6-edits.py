#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The words of rung 6 that the operator's choice of D5'' (a box of 120 s, in the app on 2026-11-19) and the independent review of the first text of rung 6 changed (the words of
tools/sp11_stick_bundle_panel_rung6.py and ..._rung6d.py and the hooks of the guide; docs/SP11-STICK-MONITOR.md section 20, K14): a phrase pin and a deletion mutant for each such sentence, each of
them following the box, the decision said in every place that names the box, no place that leaves it open, and a generator without one of the new hooks rejected by the pins. The other
sentences of the record are tools/test-sp11-stick-monitor-rung6-words.py's, whose helpers and fixtures run here.

    python3 tools/test-sp11-stick-monitor-rung6-edits.py

What a sentence says about the unit is OBSERVED (boot 25, one unit, one boot: the coordinator's reading of the operator's film) or INFERRED (the host analysis of the monitor's sources and of the
kernelcache: a file and not a run). Nothing here runs the unit: Hardware NOT RUN."""
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))

spec = importlib.util.spec_from_file_location('sp11_monitor_rung6_words', TOOLS / 'test-sp11-stick-monitor-rung6-words.py')
W = importlib.util.module_from_spec(spec)
spec.loader.exec_module(W)
STATE, flat, m1g, r5 = W.STATE, W.flat, W.m1g, W.r5


def setUpModule():
    W.setUpModule()


def tearDownModule():
    W.tearDownModule()


PINS_0 = {
    'decides': "APPROVING THIS TEXT ALSO DECIDES THREE THINGS, THE FIRST OF WHICH THE OPERATOR HAS ALREADY CHOSEN IN THE APP. (1) D5'': A TIME BOX OF 120 s.",
    'decides chose': "for this record the operator chose 120 s in the app on 2026-11-19 (\"let's go with 120 seconds\", the answer to the question whether its box should be 120 s or 60 s), and approving "
                     "this text confirms it: AC power REQUIRED, one CPU loaded for up to 120 s, no temperature readout, and the exposure sums of section 0b and section 4. Boot 25 left its CPU idle "
                     "for the last 25 s of 60 (INFERRED: section 0a), so the extra time may be spent mostly idle.",
    'why word': "THE COMMAND LINE HAS cpu_boot_timeout_secs=90 IN PLACE OF 120: if the guest's timers are delivered, that panic, \"cpu 1 failed to boot for the first time, waited 90 seconds\", comes at "
                "about 92 s after the MON CALL line, inside the box (INFERRED: the wait starts about 2.3 s in, as boot 24's PMGR panic at 2.25 s suggests), and the film then holds an OBSERVED panic "
                "text, from which (XNU times a wait out by a timer interrupt: INFERRED) a guest timer interrupt reaching the kernel follows; silence to the end of the box does not support that reading.",
    'why box': "(2) THE TIME BOX IS {box} s, so that the panic, if it comes, has room: D5'' (the operator chose 120 s in the app on 2026-11-19, \"let's go with 120 seconds\", and approving this text "
               "confirms it for this record; D5', \"Yes, up to 60 s\" of 2026-11-18, covered rung 5 and is spent with it): the time box is {box} s, with AC power, one CPU running (the guest's tree "
               "lists six, and the acknowledge starts none) and no temperature readout (D8').",
    'longer box': "THIS RECORD'S TIME BOX IS {box} s, LONGER THAN THE 5 s THAT D5 AND D8 DECIDED: the operator chose this box in the app on 2026-11-19 (D5'', \"let's go with 120 seconds\"), and "
                  "approving this text confirms that choice and the exposure that section 4 gives, which D8 accepted for a box of 5 s only.",
    'decided': "Decided by the operator on 2026-11-19 (the answer \"let's go with 120 seconds\", quoted from the app, to the question whether the box of this record should be 120 s or 60 s): D5'' the "
               "time box of this record is {box} s, with AC power REQUIRED, one CPU loaded for up to {box} s and no temperature readout (D8'): approving this text confirms it. Sections 0b and 4 give "
               "the exposure."}
PINS_0A = {
    'stick': "The stick after boots 21, 23, 24 and 25 was unchanged but for the Shell's marker (and, after boots 21, 23 and 24, the Mac's own .fseventsd entry), and REPORT.DAT kept its all-zero staged "
             "digest: a run does not write the report back, so the page on the panel is its only record (OBSERVED once each; boot 25's by a read on the host of the stick as returned: its 18 staged "
             "files byte-identical and the Shell's marker the only new file)."}
PINS_3C = {
    'intro': "It is not decided: approving the SHA-256 of this record is that decision, and it confirms D5'' (the operator chose the box of 120 s on 2026-11-19: section 0), and it covers ONE boot of "
             "this stick (section 5c, THIS APPROVAL COVERS ONE BOOT); rung 5's approval covers none of it."}
PINS_5B = {
    'stop c': "- MON STOP <kind> PC= A= B= and, on a line of its own, MON STOP C=0X...: when C= would pass column 100 of the runner (boot 25: a kernel address of 16 digits after a long head lost its last "
              "two digits).",
    'capture': "- MON CAPTURE WORD=0X.. IS NO CLASS (status zero only) takes the place of MON EXIT and the registers when the capture word is not 0 and not 1 to 3: a window of another build or a damaged "
               "one, which the page says and does not read as a capture;",
    'tim': "- MON TIM IRQS= ASSERTS= CLEARS= VECTORS= CTL= CVAL= (only with a capture) and, when that line is full, a second line WRITES= IRQPC= READS= (and MORE=<n> for what does not fit the page: with "
           "text in the console ring and C= on a line of its own the page keeps one line for the timer, and CVAL, WRITES, IRQPC and READS can be counted in MORE, which leaves (b)'s time reading, the "
           "split of (c) from (d) and the WRITES of (e) UNKNOWN): the monitor's guest-timer counters at the stop.",
    'tim ctl': "CTL is the guest timer's control (1 enabled, 2 masked, 4 ISTATUS: 0X5 is a deadline that has passed, unmasked; this kernel writes the control only with 1 (four writes in the kernelcache, "
               "none of another value) and re-arms with the compare interval: read on the host, a file and not a run; so a page shows 0X1 or 0X5, and 0X0 only before its timer set-up); CVAL is the "
               "guest's compare value; WRITES and READS are the guest's accesses to the timer registers; IRQPC is ELR_EL2 at the last host tick.",
    'ack legend end': "COUNT=0 is a block with no store; no line at all is a monitor without the block, a window that is not status OK or a full page (a stop at a stage-2 store with text in the console "
                      "ring leaves no line for it); MON ACK LAYOUT=0X.. NOT READ names a block whose layout is not the one that the reporter reads. Section 3c has the prediction for COUNT (INFERRED); "
                      "no class of section 5d reads it."}
PINS_5D = {
    'new': "WHAT IS NEW. Rung 5's guest with three changes (section 0): XNU's wait for the CPU that never comes is 90 s (the command line is {line}: six CPUs, no cpus=1), the box is {box} s (D5'': one "
           "CPU loaded for up to {box} s, AC power REQUIRED, no temperature read; the operator chose it on 2026-11-19 and approving this text confirms it) and the SNAPREP page prints the guest "
           "timer's words (section 5b).",
    'film': "THE FILM is rung 5's, from the Shell's countdown until 120 seconds after the SNAPREP report page has appeared, or, if it never appears, until the FORCE OFF of step 5 of section 5c "
            "({wait} s after the MON CALL line) has been filmed (about {film} s in all for this box)",
    'spsr': "(SPSR 0X604003C4: EL1 with every interrupt masked, idle INFERRED)",
    'timer panic answer': "THE TEXT IS THE RESULT and the answer: a guest timer interrupt reached the kernel (the text is OBSERVED once, if it comes; that it took a timer interrupt is INFERRED).",
    'watchdog a vectors': "VECTORS 0 is the clean form: no timer interrupt reached the kernel; with VECTORS above 0 the kernel took a timer interrupt earlier, which answers the question of this rung "
                          "as (b) does, and only the last line is in doubt.",
    'watchdog b ctl': "CTL=0X1 is a deadline that the coordinator places by the distance of CVAL from LAST of the MON CLK line (CVAL less 1.25 times LAST, in ticks of 24 MHz: INFERRED) and says whether it "
                      "lies beyond the box, and CTL=0X5 is a deadline that had passed and was not yet served at the report.",
    'watchdog e': "(e) NOTHING ARMED, VECTORS 0 and CTL=0X0, 0X2, 0X3 or 0X7: the control says disabled or masked, which this kernel does not leave (it writes the control only with 1 and re-arms with "
                  "the compare interval: read on the host in the kernelcache, a file and not a run), so CTL=0X0 with WRITES 0 says that the guest had not reached its timer set-up, and any other page "
                  "is a finding to read, UNKNOWN whether it is the monitor's decode or accounting or the guest; no reading of what the kernel waits for is drawn from it.",
    'watchdog f': "(f) Any other MON TIM line (a CTL of 0X4, 0X6 or above 0X7, which the monitor cannot store: it keeps bits 0 and 1 of what the guest writes and computes bit 2 only for an enabled "
                  "timer) or none (the monitor left no capture word of class 1, 2 or 3, UNKNOWN why): a finding to read, UNKNOWN whether it is the monitor's decode or accounting or the guest, and "
                  "the film and the other lines are the result."}
PINS_HOOKS = {
    'hazard': "in boot 25 the kernel went on past them and the log was silent for the last 25 s of 60 (OBSERVED once; that its CPU 0 sat idle all that time, waiting, is INFERRED from the PC at the "
              "stop), and what it does in {box} s is UNKNOWN (it may wait, panic at about 92 s or spin).",
    'hazard decision': "is the operator's decision D5'', made in the app on 2026-11-19 and confirmed by approving this text (section 0)",
    'd8 decision': "D8 accepted the exposure of one CPU for a box of 5 s only: for this box of {box} s the operator chose it in the app on 2026-11-19 (D5''), and approving this text confirms it "
                   "(section 0).",
    'film until': "until 120 seconds after the SNAPREP report page has appeared, or, if it never appears, until the FORCE OFF of step 5 ({wait} s after the MON CALL line) has been filmed.",
    'step 6': "so that the text on it can be read, and a fourth of the whole report page, so that its lines can be read (asked, not required; section 5d)."}
SECTIONS = (('section0', PINS_0), ('section0a', PINS_0A), ('section3c', PINS_3C), ('section5b', PINS_5B), ('section5d', PINS_5D))
OPEN = ('HAS NOT ANSWERED YET', 'not yet decided', 'is the answer', "the operator's question", 'approving this text makes it', 'approving this text decides this box',
        "the operator's decision by approving", 'THE OPERATOR HAS NOT')


class Edits(unittest.TestCase):
    def test_each_edited_sentence_is_pinned_and_a_record_without_it_is_rejected(self):
        parts = W.sections(STATE['r6-120'])
        for name, pins in SECTIONS:
            W.mutants_rejected(self, parts[name], W.formatted(pins, 120))
        f = flat(STATE['r6-120'])
        for name, phrase in W.formatted(PINS_HOOKS, 120).items():
            with self.subTest(pin=name):
                self.assertGreaterEqual(f.count(flat(phrase)), 1, 'in the text')
                self.assertEqual(W.pin_problems(f.replace(flat(phrase), 'DAMAGED'), {name: phrase}), [name])

    def test_every_edited_sentence_follows_the_box(self):
        for box in (120, 30):
            parts = W.sections(STATE[f'r6-{box}'])
            for name, pins in SECTIONS:
                with self.subTest(box=box, section=name):
                    self.assertEqual(W.pin_problems(parts[name], W.formatted(pins, box)), [])
            self.assertEqual(W.pin_problems(STATE[f'r6-{box}'], W.formatted(PINS_HOOKS, box)), [])
        f30, f120 = flat(STATE['r6-30']), flat(STATE['r6-120'])
        self.assertIn('until the FORCE OFF of step 5 (90 s after the MON CALL line) has been filmed', f30)
        self.assertEqual(f120.count('until the FORCE OFF of step 5 (180 s after the MON CALL line) has been filmed'), 1)
        self.assertEqual(f120.count('until the FORCE OFF of step 5 of section 5c (180 s after the MON CALL line) has been filmed'), 1)

    def test_the_operators_choice_of_d5_is_said_where_the_box_is_named_and_no_place_leaves_it_open(self):
        f = flat(STATE['r6-120'])
        self.assertEqual(f.count("let's go with 120 seconds"), 4, 'the first decision of section 0, its second item, the longer box and the list of what the operator decided')
        for needle in OPEN:
            self.assertNotIn(needle, f)
        self.assertEqual(f.count('Decided by the operator on 2026-11-19'), 1)
        self.assertLess(f.index('Decided by the operator on 2026-11-17'), f.index('Decided by the operator on 2026-11-19'))
        self.assertLess(f.index('Decided by the operator on 2026-11-19'), f.index('Decided on 2026-11-16'))
        self.assertEqual(f.count("D5', \"Yes, up to 60 s\" of 2026-11-18, covered rung 5 and is spent with it"), 1, 'the answer for rung 5 is spent and says so')
        self.assertEqual(f.count('approving this text confirms'), 7, 'the first decision, its second item, the longer box, the list of the decided, the exposure of 0b and of section 4, and section 5d')
        self.assertTrue(f.isascii() and 'VERIFIED' not in f)

    def test_a_generator_without_one_of_the_new_hooks_makes_a_record_that_the_pins_reject(self):
        pins = {**{f'0/{k}': v for k, v in W.formatted(PINS_0, 120).items()}, **{f'hooks/{k}': v for k, v in W.formatted(PINS_HOOKS, 120).items()}}
        keys = m1g.PANEL['console+cpu_boot_timeout_secs=90+let_panic']
        dropped = lambda key, value: mock.patch.dict(keys, {key: value})      # noqa: E731
        for what, (patch, pin) in {'no list of what the operator decided': (dropped('decided', ''), '0/decided'), 'no decision on the longer box': (dropped('box_decision', ''), '0/longer box'),
                                   'no decision beside the exposure': (dropped('d8_decision', ''), 'hooks/d8 decision'), 'rung 5\'s span of the film': (dropped('film_until', r5.film_until), 'hooks/film until'),
                                   'rung 5\'s stills': (dropped('step6', r5.STEP6), 'hooks/step 6')}.items():
            with self.subTest(what), patch:
                code, text, err = W.record6('mutant-r6')
                self.assertEqual(code, 0, err)
                self.assertNotEqual(text, STATE['r6-120'])
                self.assertIn(pin, W.pin_problems(text, pins), what)

    def test_the_older_records_have_none_of_the_new_hooks_and_keep_rung_5s_film_and_stills(self):
        for key in ('console+cpu_boot_timeout_secs=120+let_panic', 'console+cpus=1+let_panic', 'console+cpus=1', 'console', 'handed'):
            for hook in ('decided', 'box_decision', 'd8_decision'):
                self.assertNotIn(hook, m1g.PANEL[key], f'{key}: the other records say nothing of D5\'\'')
        rung5 = m1g.PANEL['console+cpu_boot_timeout_secs=120+let_panic']
        self.assertIs(rung5['film_until'], r5.film_until)
        self.assertEqual(rung5['step6'], r5.STEP6)


if __name__ == '__main__':
    unittest.main()
