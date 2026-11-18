#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of rung 2's own words (tools/sp11_stick_bundle_panel_rung2.py; docs/SP11-STICK-MONITOR.md section 20, K7): why the panel is handed after boot 20, the prediction and
what falsifies it, what the run may end as, section 5d (what to film, what to expect, what is new, what counts as a result), the rule that ends the run of a guest that keeps
painting (the SNAPREP page not on the screen when the wait is up) in every place that the operator reads it, what the record shares with the first run (derived from the box
and the digests of the programs, so that a record with another box or another wrapper says so), and the silence wait that the panel's rule names. The words are held to the
text of the records that the builder makes (the console suite's fixtures, with a scratch directory of their own, deleted at the end); each sentence has a phrase pin and a
deletion mutant that its pin alone rejects, and a generator without a block makes a record that the pins reject.

    python3 tools/test-sp11-stick-monitor-rung2.py

Everything here is host only: the numbers of boot 20 are the coordinator's stop-site analysis (INFERRED; the PC is OBSERVED), nothing is run. Hardware: NOT RUN."""
import importlib.util
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))

spec = importlib.util.spec_from_file_location('sp11_monitor_console_tests', TOOLS / 'test-sp11-stick-monitor-console.py')
CT = importlib.util.module_from_spec(spec)
spec.loader.exec_module(CT)
SCRATCH = ROOT / 'build' / 'sp11-monitor-rung2-test-scratch'
CT.SCRATCH = CT.PT.SCRATCH = CT.PT.BLD.SCRATCH = CT.PT.AUTH.SCRATCH = SCRATCH
pn, r2, BT, STATE = CT.pn, CT.pn.r2, CT.BT, CT.STATE
flat = lambda text: ' '.join(text.split())    # noqa: E731
WAIT = 60       # the silence rule's wait for the fixture's box of 5 s (the guide's silence_s)
FORCE = r2.force_off(WAIT)
COUNT = f'Count those {WAIT} seconds from the moment the MON CALL line is on the screen, on a watch or the camera\'s timer.'    # step 5 and 5d: a dark panel shows no clock


def setUpModule():
    CT.setUpModule()


def tearDownModule():
    CT.tearDownModule()


def noticed(test, text, phrases, where):
    """Every phrase of the statement is in `text` (each its own case, so that the one that went is named)."""
    for phrase in phrases:
        with test.subTest(where=where, phrase=phrase[:60]):
            test.assertIn(phrase, text)


# What the paragraphs of this rung's own words must carry: a phrase pin each (in the words of the text, line breaks gone) and a deletion mutant of each that its pin, and no
# other, must reject, as the text suite does for the review of the first run's record. The phrases of one table do not overlap.
WHY_PINS = {
    'why': 'WHY THE PANEL IS HANDED NOW. Boot 20\'s guest was stuck, not slow: it stopped in a loop that cannot end while its video block is all zero, which D9\'s first choice '
           'leaves it (INFERRED; section 0a has the analysis)',
    'two changes': 'THIS RECORD CHANGES TWO THINGS IN THE GUEST, WHICH BELONG TOGETHER: it hands the guest the panel, so that its video block is not zero, and it sends the '
                   'kernel\'s console to the panel (serial=2)',
    'programs': 'The two programs and the time box are boot 20\'s (section 3c)',
    'no -noprogress': 'It does NOT add -noprogress, a third change that could end the spin by itself and so leave the result without a cause',
    'prediction': 'PREDICTION (INFERRED): the PC= of the report is not in that loop, and the kernel prints more than the banner it printed in boot 20',
    'falsifier': 'FALSIFIER: a PC= in that loop again (0XFFFFFE000C1EEF00, or one of the next four instructions, up to 0XFFFFFE000C1EEF10): the panel did not remove the spin, '
                 'rung 2 is NOT PASSED and the next try is -noprogress, in a record of its own',
    'pointer': 'Section 3c has the other ends and section 5d what passes it'}
STAND_PINS = {
    'analysis': 'The stop-site analysis of that address, the coordinator\'s (INFERRED, not run again): 0XFFFFFE000C1EEF00 is the first of five instructions that use registers only '
                'and loop, read on the host (a file, not a run) in the kernelcache',
    'public match': 'That this is the progress-bar loop of PE_init_iokit is INFERRED, from a match of structure with the public XNU source (read from pe_init.c, tag xnu-12377.121.6; '
                    'this kernel\'s own source is not public)',
    'no exit': 'and that loop has no exit while the display size is 0',
    'zero video': 'The guest of boot 20 had an all-zero boot_args.Video, because D9 withheld the panel, and its command line has no -noprogress',
    'no register': 'no register was printed, so a display size of 0 is INFERRED too. So that guest was stuck and not slow (INFERRED)',
    'stuck': 'and a longer time box on that image would only repeat the spin (INFERRED)'}
PUBLIC_PINS = {
    'source': 'What the public source says of the console (read from the public source: video_console.c, boot.h and pe_init.c of tag xnu-12377.121.6, not this kernel\'s own, '
              'whose source is not public; so INFERRED for this kernel)',
    'cell': 'The text is drawn in cells of ISO_CHAR_WIDTH by ISO_CHAR_HEIGHT pixels, 8 x 16 in the kernelcache (above), and the rows and columns are the height and the width of the '
            'panel divided by them (vc_initialize, video_console.c:2785-2786)',
    'depth word': 'its bits 16 to 23 plus 1 (depth is bits 0 to 7, rotation 8 to 15, boot rotation 24 to 31: boot.h:33-37, read in pe_init.c:449-460)',
    'progress only': 'reaches only the progress-bar images (vc_uiscale, video_console.c:2524, 2664-2666 and 2696-2697): the text is 8 x 16 pixels at any scale and cannot be '
                     'enlarged from this side',
    'loader': 'The loader\'s depth word is 32: depth 32, no rotation, scale 1',
    'clear': 'For Video.v_display 0 the boot picks the text mode (pe_init.c:555-558); the screen is then taken (kPEAcquireScreen) and the console enabled (gc_enable), which resets '
             'the cursor to column 0, row 0 and clears the WHOLE screen to the background colour (video_console.c:436-440, 484 and 958), and vcattach then replays the kernel\'s '
             'message buffer on it (video_console.c:3082-3098)',
    'consequence': 'So the text is expected from the top-left cell, a dark panel from the MON CALL line is EXPECTED, and the runner\'s page and the wrapper\'s lines of before the '
                   'call are wiped from the panel: only the film holds them (all INFERRED for this kernel; when this happens, inside the time box, is UNKNOWN)',
    'screen clears': 'and the kernel\'s console is expected to CLEAR the whole panel when it takes it (read in the public source, below; INFERRED for this kernel), which wipes the '
                     'runner\'s page and the wrapper\'s lines drawn before the call: only the film holds them',
    'pointer': 'What to film, the new hazards of this choice and the results are in section 5d, in the operator\'s words; this section is the evidence for them'}
FIVE_D_PINS = {
    'film': 'THE FILM is the only record of the kernel\'s text: the console draws only while the guest runs, the MON CON lines are not expected to carry it, and nothing is written '
            'to the stick (row f of section 5a).',
    'main camera': 'The main camera is on the whole panel, sharp enough to read small text, for the span that step 2 of section 5c gives. It must show the AUTORUN lines (steps 3 and '
                   '4); PAYLOAD CALL 0, which only the film can show once the kernel has cleared the panel (if the film does not show it, the approval is spent); the panel while '
                   'the guest runs; and the SNAPREP report, with MON STOP and MON EXIT, whose PC= decides the result, and the first UPTIME heartbeat after it.',
    'second camera': 'A second camera, if there is one, is on the TOP-LEFT quarter of the panel, close up, for the same span, steady, with focus and exposure locked, in a dim room: '
                     'the kernel\'s glyphs are 8 x 16 pixels, about 0.8 x 1.5 mm, and cannot be enlarged from our side (INFERRED; section 3c); a 4K frame of the whole panel gives a glyph '
                     'of about 9 x 18 pixels, which is marginal, and of the quarter about 18 x 36.',
    'one camera': 'With one camera only, film the whole panel at the best resolution it gives; the report page stays the primary record.',
    'frame rate': 'Film 30 frames a second or more (29.97 is fine: boot 20\'s film had it; 60 if the camera can): lines that scroll between two frames are not in it.',
    'margin': 'Stay on the final screen for ten seconds before the FORCE OFF of step 6, and hand every video back as recorded, not trimmed or re-encoded.',
    'glyphs': 'Kernel glyphs are text that is not the runner\'s capitals and digits: once the runner\'s page is up, any lowercase letter on the panel is the kernel\'s (INFERRED from '
              'section 5b: the runner\'s font has capitals only).',
    'photograph': 'A photograph taken afterwards is not enough: the heartbeat clears part of what the kernel left.',
    'expect': 'WHAT TO EXPECT. From the MON CALL line the screen may go black and fill with the kernel\'s text: that is expected (the kernel\'s console clears the whole panel when it '
              'takes it: read in the public source, not this kernel\'s own; section 3c).',
    'paint over': 'The text can paint over the page, and the report can then be hard to read: the film holds it.',
    'force off': f'The operator does nothing but film. {FORCE}.',
    'count': COUNT,
    'new lead': 'WHAT IS NEW beyond section 0b. It adds no store class, no device and no MMIO (INFERRED from the sources; never run), and the panel itself changes neither the monitor, '
                'the wrapper, the reporter nor the time box.',
    'reads': 'The monitor reads the panel range twice and cleans it at the stop (2764800 loads of 8 bytes and 345600 lines of DC CIVAC, at EL2, outside the time box): the cost is '
             'UNKNOWN, so the report can come later than the same box without the panel would give, and TICKS= can exceed the box (row e of section 5a).',
    'first time': 'The kernel\'s video-console code runs on this unit for the first time: what it does here is UNKNOWN.',
    'finding': 'after the report is a finding: film it, say what you saw, and FORCE OFF as step 6 says.',
    'nowhere': 'The console text is on the panel or nowhere (INFERRED): a panel with none of it leaves the run without kernel text.',
    'results head': 'THE RESULTS. M1\'s own verdict is section 5a\'s and is reported beside rung 2\'s, with one more condition: the panel did not end the run.',
    'stop report': 'A STOP REPORT is the SNAPREP page with MON STATUS=OK ENTERED=0X1 and a MON STOP line of any kind; it is CLEAN when none of MON REGS RESTORED=NO, WINDOW OVERRUN, '
                   'STACK FULL or GIC RESTORE CLEAN=NO is on it.',
    'loop': 'The loop of boot 20 is the five instructions from 0XFFFFFE000C1EEF00 to 0XFFFFFE000C1EEF10 (section 0); on GUEST_EXC the PC= is the guest\'s x30, so read C= (section 5b).',
    'either camera': 'The film, where a result names it, is the film of either camera: the close-up of a second camera counts as much as the whole panel\'s (step 2 of section 5c).',
    'passed': 'RUNG 2 PASSED: a stop report with a PC= that is not in that loop, and the film of either camera shows kernel glyphs on the panel, in any frame and not only the last '
              '(scrolled text counts), beyond the lines that the runner drew before the call.',
    'passed past': 'The PC= says only that it is not in the loop: the coordinator says from the kernelcache whether it is past it.',
    'partial': 'RUNG 2 PARTIAL: a stop report, clean or not (say which), with a PC= that is not in that loop, and no kernel glyph that can be read in the film of either camera: the '
               'aim is not met and why is UNKNOWN (the console did not draw within the box, was cleared again, or is not there); the MON CON lines are not expected to say.',
    'not passed loop': 'RUNG 2 NOT PASSED: a PC= in that loop again (the falsifier of section 0: the panel did not remove the spin);',
    'not passed s2': 'or a MON STOP S2_FAULT with A= inside the panel range (section 2: a finding about the panel, not the success that section 5a gives for a fault elsewhere);',
    'not passed dark': f'or the panel still dark {WAIT} seconds after the MON CALL line, or the Surface restarts, before the report;',
    'not passed none': 'or no report (a page that stops at MON CALL, a REFUSED line, MON PREPARE FAILED, a fault page of q1n1). Glyphs seen before such an end are an observation to '
                       'hand back, not a pass.',
    'not a result': 'RUNG 2 NOT A RESULT: a film that does not let the coordinator read MON STOP (too soft, too dark, or the report unreadable under the kernel\'s text): nothing is '
                    'concluded from it, and the one-boot rule of section 5c says whether the approval is spent.',
    'separate': 'A run that spins in that loop again is still a MON STOP WATCHDOG with a kernel PC=, which section 5a calls M1 PASSED, and it is rung 2 not passed: the two '
                'verdicts are separate.'}
# OBSERVED is what the unit showed in this project (a run, or a document's own label of one); a reading of a file or of a source is not, so each use of the word in these words
# must be followed by one of these tails.
OBSERVED_OK = (' on the A16', ': docs/', ' once', ', one unit, one boot', ' (B1 attempts 1 to 5, operator report)')


def pin_problems(text, pins):
    """The names of the pins whose phrase is not in `text` (line breaks gone)."""
    f = flat(text)
    return [name for name, phrase in pins.items() if flat(phrase) not in f]


def observed_problems(text):
    """The places where `text` says OBSERVED and not of something that the unit, the A16 or a document's own label shows (OBSERVED_OK)."""
    return [text[m.start():m.start() + 50] for m in re.finditer('OBSERVED', text) if not any(text[m.end():].startswith(tail) for tail in OBSERVED_OK)]


def mutants_rejected(test, text, pins):
    """`text` holds each phrase of `pins` once, and the text without one phrase is rejected by the pin of that phrase and by no other."""
    f = flat(text)
    test.assertEqual(pin_problems(f, pins), [])
    for name, phrase in pins.items():
        with test.subTest(pin=name):
            test.assertEqual(f.count(flat(phrase)), 1, 'each sentence is in the text once')
            test.assertEqual(pin_problems(f.replace(flat(phrase), 'DAMAGED'), pins), [name])


def hook_pins(text):
    """The sentences that the hooks of the guide put into the record of a panel choice, for the guest and the label of `text`: where the SNAPREP page ends the run of a guest that
    paints, what the screen does from the MON CALL line, what step 4 compares and the shared facts that boot 20 corrected. Each phrase has its own context, so that it is in the
    text once."""
    f = flat(text)
    size, sha, chunks = re.search(r'data GUEST\.DAT (\d+) bytes sha256 ([0-9a-f]{64}) chunks (\d+)', f).groups()
    label = re.search(r'\(format 1, label (\S+?)\)', f).group(1)
    runaway = (f'one CPU stays loaded, at worst, until the operator acts: {WAIT} s after the MON CALL line, whatever the guest paints (section 5), plus about 20 s holding Power, '
               f'{WAIT + 20} s or more, with no temperature read; approving this text accepts that too.')
    return {
        'reading': 'The operator reads and acts on sections 0, 0a, 0b, 4, 5, 5a, 5c and 5d, and reads in section 1b the paragraph on the NV2 record (D6)',
        'evidence': 'The rest of sections 1 to 3c is evidence that the coordinator',
        'reading 5d': 'This text also asks the operator to read section 5d: it says what to film, what is new beyond section 0b and what counts as a result of rung 2. Section 3c is '
                      'the evidence for it, which the coordinator reads.',
        '0b hazard': 'from the MON CALL line a dark panel is EXPECTED, because the kernel\'s console clears the whole panel when it takes it (read in the public source, not this '
                     'kernel\'s own; when, inside the time box, is UNKNOWN), and its text can paint over the page, so that the report can be hard to read. The film is the only '
                     f'record of that text and of the lines printed before the guest ran (section 5d). {FORCE}.',
        '0b runaway': f'(the plan\'s second most likely failure) {runaway}',
        '4 runaway': f'(the plan\'s second most likely failure, section 5a) {runaway}',
        '5 bullet': f'- Panel as the console (sections 3c and 5d): {FORCE}. What the guest paints, the kernel\'s text included, does not count as a page change for the silence rule '
                    'above.',
        '5 bullet dark': 'A dark panel before the wait is up is not a restart and not the black panel of step 7 of section 5c or of the NOT A RESULT page of section 5a; the panel '
                         f'still dark {WAIT} seconds after the MON CALL line, or a restart (the Shell\'s countdown or the runner\'s first lines on the screen again), before the report, '
                         'is that case.',
        '5a hang': f'a lit screen with neither STAGE=DONE nor the SNAPREP page {WAIT} s after the MON CALL line, whatever the guest paints (no controlled stop: row d; a panel still '
                   'dark then is NOT A RESULT, below)',
        '5a no result': f'- NOT A RESULT: a black panel before the MON CALL line, a panel still dark {WAIT} seconds after it, or a restart of the Surface, before STAGE=DONE and the '
                        'SNAPREP page have been on the screen, or a film that does not show them.',
        '5a row e': 'and, for a guest that is handed the panel, the monitor\'s two reads of the panel range (EL2 loops of 2764800 loads of 8 bytes, in its prepare before the timer is '
                    'armed and at the stop after it is off, and at the stop, if SCTLR_EL1 is not 0, DC CIVAC over 345600 lines: probes/xnuboot/prepare.c:578-581, '
                    'probes/xnuboot/report.c:243-250, probes/xnuboot/context.c:799-806, probes/xnuboot/mmu_access.c:193-198; they are not in the time box but are in TICKS and in '
                    'the operator\'s silence rules, and their cost is UNKNOWN)',
        '5b still': 'the monitor is called now, and the runner\'s page does not change until it returns, but the guest may paint over it (the screen may go black and fill with text): '
                    'the monitor\'s prepare',
        'step 2 fps': 'Film 30 frames a second or more (29.97 is fine: boot 20\'s film had it; 60 if the camera can) from the Shell\'s countdown until the first UPTIME heartbeat AFTER the '
                      'line STAGE=DONE',
        'step 2 main camera': 'Keep the whole panel in frame. That is the main camera: the pages, PAYLOAD CALL 0 and the PC= line are on it.',
        'step 2 second camera': 'For this choice the kernel\'s text is small (section 3c), so if there is a second camera, film the TOP-LEFT quarter of the panel close up for the same '
                                'span, held steady, with focus and exposure locked, in a dim room.',
        'step 2 one camera': 'With one camera only, film the whole panel at the best resolution it gives. The report page stays the primary record; the kernel\'s text is expected '
                             'from the top-left cell (INFERRED).',
        'step 4': f'Then compare, on the PAYLOAD LOADED line, INDEX=2 ... BYTES={size} SHA={sha[:16].upper()} (GUEST.DAT) and, on the SNAPMON STAGE=PIN_OK line, LABEL={label} '
                  f'CHUNKS={chunks} (boot 20\'s page had LABEL=M1RUN-RUNG1 CHUNKS=44: the first run\'s bundle staged under this plan has the same programs and would pass the '
                  'comparison above).',
        'step 5 wait': 'MON CALL, then the prepare, the box and the stop). The stop rules of section 5 say when a silence ends the run.',
        'step 5': f'From the MON CALL line the screen may go black and fill with text: that is expected. {FORCE}. {COUNT} If at any time the Surface is too hot',
        '5a fault success': 'A STAGE-2 FAULT WITH AN IPA IS SUCCESS (not when A= lies inside the panel range: section 5d): MON STOP S2_FAULT, with the guest PC, the IPA in A= and ESR_EL2 in B=,',
        '5a fault passed': 'with PC= a kernel address (C= on GUEST_EXC) and A= not 0 on S2_FAULT (not when A= lies inside the panel range: section 5d); rows a, c, d and e hold,',
        'step 6': 'that writes nothing of ours. For this choice wait ten seconds on the final screen before that FORCE OFF (a margin, not a measurement). Do not run the bundle a second time.',
        'step 7': 'For this choice a dark panel from the MON CALL line is not this case, since the kernel\'s console clears it: it is this case when the panel is still dark '
                  f'{WAIT} seconds after the MON CALL line (section 5).'}


# What boot 20 corrected in the prose that every kind of the record shares (the withheld record too): the review of rung 2 (m3, m8 and the frame rate of m6) and its delta review
# (N1: TICKS HASHED is the whole step of the wrapper, counted from its entry; N6: a file read on the host is not OBSERVED; N7: row f; N8: the frame rate).
SHARED_PINS = {
    'hashing row': 'that same step in boot 20 (TICKS HASHED) 2.41 s OBSERVED once (46,208,412 ticks of 19.2 MHz from the wrapper\'s entry)',
    'hashing sentence': 'Boot 20 measured that whole step (checks, copy and verify) for its own 118.9 MB GUEST.DAT at 2.41 s (TICKS HASHED=46,208,412 at 19.2 MHz, counted from the '
                        'wrapper\'s entry; OBSERVED once), which agrees with that scaling.',
    'uptime': 'boot 20: UPTIME 28 S at its first heartbeat, and no restart to the end of its film at 45 s; OBSERVED once). The earliest restart datum is about UPTIME 17 s',
    '0b restart': 'the earliest datum is about UPTIME 17 s, and boot 20 showed none to the end of its film at 45 s: section 4)',
    '0a stick': 'The stick was unchanged but for the Shell\'s marker and the Mac\'s own .fseventsd entry.',
    'row f': 'and the stick is byte-identical after the run but for the Shell\'s marker and, once the Mac has mounted the stick, its .fseventsd entry, which the coordinator re-reads;',
    '0b smc': 'the kernelcache holds none: read on the host (a file, not a run)). The one certain write to the stick',
    '3b smc': 'hold 0 SMC and 0 HVC words (read on the host (a file, not a run), one kernelcache;',
    'font': 'in the panel\'s font (capitals and digits; ":" prints as "!" (OBSERVED once: boot 20) and anything else that is not in the font as "?")',
    'fps': 'Film 30 frames a second or more (29.97 is fine: boot 20\'s film had it; 60 if the camera can) from the Shell\'s countdown'}
STALE = ('Film at 60 fps', 'Film at least 30 frames a second', 'anything else is a question mark', 'unchanged but for the Shell\'s marker.', 'OBSERVED on a host file', 'its hashing in boot 20',
         'the wrapper\'s hashing of its own', 'but for the Shell\'s marker, which the coordinator re-reads')


class Constants(unittest.TestCase):
    def test_the_stop_pc_and_the_loop_are_boot_20s_and_print_as_the_page_prints_a_number(self):
        self.assertEqual(r2.pc_text(r2.STOP_PC), '0XFFFFFE000C1EEF00')
        self.assertEqual([r2.pc_text(pc) for pc in r2.LOOP_PCS], ['0XFFFFFE000C1EEF00', '0XFFFFFE000C1EEF04', '0XFFFFFE000C1EEF08', '0XFFFFFE000C1EEF0C', '0XFFFFFE000C1EEF10'])

    def test_the_first_runs_programs_and_box_are_the_ones_its_approved_record_named(self):
        monitor, reporter = STATE['first_run_pair']
        self.assertEqual((monitor[:16], reporter[:16]), ('1d32f5071506a00c', 'ee86676001d89cf6'))
        self.assertTrue(all(re.fullmatch(r'[0-9a-f]{64}', d) for d in (monitor, reporter)))
        self.assertEqual(r2.RUNG1_BOX_S, CT.rec.m1g.D5_BOX_S)

    def test_the_facts_hold_the_box_and_each_program_against_the_first_runs(self):
        same = r2.facts(5, r2.RUNG1_MONITOR, r2.RUNG1_REPORTER)
        self.assertEqual(r2.differs(same), [])
        for name, run in (('box', r2.facts(30, r2.RUNG1_MONITOR, r2.RUNG1_REPORTER)), ('monitor', r2.facts(5, 'a' * 64, r2.RUNG1_REPORTER)),
                          ('reporter', r2.facts(5, r2.RUNG1_MONITOR, 'b' * 64)), ('all', r2.facts(30, 'a' * 64, 'b' * 64))):
            with self.subTest(name):
                self.assertEqual(len(r2.differs(run)), 3 if name == 'all' else 1)
        self.assertIn('its time box is 30 s where the first run\'s was 5 s', r2.differs(r2.facts(30, r2.RUNG1_MONITOR, r2.RUNG1_REPORTER))[0])

    def test_the_call_that_boot_20_made_returned_5_04_s_after_the_mon_call_line(self):
        self.assertEqual(r2.CALL_S, '5.04')
        self.assertEqual(round(96_720_173 / 19.2e6, 2), 5.04, 'TICKS of boot 20 at 19.2 MHz')


class Words(unittest.TestCase):
    def setUp(self):
        self.same = r2.facts(5, r2.RUNG1_MONITOR, r2.RUNG1_REPORTER)
        self.other = r2.facts(30, 'a' * 64, r2.RUNG1_REPORTER)

    def test_the_text_says_the_programs_and_the_box_are_the_first_runs_only_when_they_are(self):
        said = flat(r2.same_text(self.same))
        noticed(self, said, (f'SNAPMON-M1.BIN and SNAPREP-M1.BIN have the SHA-256 that boot 20 ran ({r2.RUNG1_MONITOR[:8]}... and {r2.RUNG1_REPORTER[:8]}..., in full in section 1)',
                             'the time box is 5 s and the store classes are the first run\'s', 'Only GUEST.DAT', 'and PIN.DAT', 'and PAYLOAD.MAN, which lists them'), 'same')
        said = flat(r2.same_text(self.other))
        self.assertIn('this record is NOT the first run with the guest changed alone: its time box is 30 s where the first run\'s was 5 s; SNAPMON-M1.BIN has the SHA-256 aaaaaaaa', said)
        self.assertNotIn('the time box is 5 s and the store classes are the first run\'s', said)
        self.assertNotIn('Only GUEST.DAT', said, 'with another wrapper or box, GUEST.DAT and PIN.DAT are not the only files that differ')
        self.assertIn('Section 0 and section 4 say what that adds', said)
        self.assertIn('the panel itself changes neither the monitor, the wrapper, the reporter nor the time box.', r2.hazard_lead(self.same))
        self.assertIn('(what else this record changes from the first run is said at the start of section 3c)', r2.hazard_lead(self.other))
        self.assertNotIn('what else this record changes', r2.hazard_lead(self.same))

    def test_why_the_panel_is_handed_says_the_one_change_the_prediction_and_the_falsifier_in_about_nine_lines(self):
        lines = r2.why_lines({'panel_facts': {'run': self.same}})
        said = flat(' '.join(lines))
        self.assertTrue(all(len(line) <= 118 and line.isascii() for line in lines))
        self.assertLessEqual(len(lines), 10, 'the analysis is section 0a\'s (stand_extra): section 0 keeps the decision')
        self.assertEqual(pin_problems(said, WHY_PINS), [])
        self.assertNotIn('VERIFIED', said)
        other = flat(' '.join(r2.why_lines({'panel_facts': {'run': self.other}})))
        self.assertIn('The two programs and the box are NOT all boot 20\'s: its time box is 30 s where the first run\'s was 5 s', other)
        self.assertNotIn('The two programs and the time box are boot 20\'s', other)
        self.assertNotIn('The two programs', flat(' '.join(r2.why_lines({}))), 'a hand-made record that carries no `run` entry says nothing of the programs')

    def test_the_stop_site_analysis_is_in_0a_for_this_rung_only_and_says_what_it_rests_on(self):
        lines = r2.stand_extra({})
        said = flat(' '.join(lines))
        self.assertTrue(all(len(line) <= 118 and line.isascii() for line in lines))
        self.assertEqual(pin_problems(said, STAND_PINS), [])
        self.assertNotIn('VERIFIED', said)

    def test_the_likely_stops_are_labelled_and_the_operator_only_films(self):
        said = flat(r2.expect_text(self.same))
        noticed(self, said, ('What the run may end as, if the loop is gone (INFERRED from the stop-site analysis of boot 20, section 0: none of it has been seen, and which comes first, '
                             'and when, is UNKNOWN)', 'MON STOP S2_FAULT with A= an IPA: the kernel\'s first access to a device that the monitor does not model, most likely its first '
                             'CPU-start write to the PMGR block (docs/SP11-MONITOR-PORT.md section 7; compat/boot-relocate.h:147-153)',
                             'section 5a counts that as a SUCCESS for M1, because it shows that the kernel got that far',
                             'MON STOP WATCHDOG at the end of the 5 s box with the kernel still booting: the film then shows how far it got', 'MON STOP PANIC_ENTRY: the kernel\'s own panic '
                             'text on the panel, which is to be filmed, and a finding of section 5a', 'with x4 = 0 it falls to stage 2 and stops the run as above',
                             'MON STOP GUEST_EXC: an exception that the kernel took', 'In every case the operator does nothing but film (section 5d)'), 'expect')
        self.assertIn('MON STOP WATCHDOG at the end of the 30 s box', r2.expect_text(self.other))

    def test_the_results_are_the_named_ones_of_the_review_and_name_the_wait_that_is_given(self):
        for wait in (60, 90, 180):
            paragraphs = r2.results_paragraphs(self.same, wait)
            said = flat(' '.join(paragraphs))
            with self.subTest(wait=wait):
                self.assertEqual([p.split(':')[0] for p in paragraphs[1:5]], ['RUNG 2 PASSED', 'RUNG 2 PARTIAL', 'RUNG 2 NOT PASSED', 'RUNG 2 NOT A RESULT'])
                self.assertIn(f'or the panel still dark {wait} seconds after the MON CALL line, or the Surface restarts, before the report;', said)
                self.assertNotIn('or the panel goes black', said, 'a dark panel from the MON CALL line is expected: it ends the run only when the wait is up')
        self.assertNotIn('the kernel got past the spin', ' '.join(r2.results_paragraphs(self.same, 60)), 'a PC= outside the loop does not say that the kernel is past it')

    def test_the_rule_that_ends_the_run_of_a_guest_that_paints_names_the_wait_and_the_call_of_boot_20(self):
        for wait in (60, 90, 180):
            said = flat(r2.force_off(wait))
            with self.subTest(wait=wait):
                self.assertEqual(said, f'Whatever the guest paints, if the SNAPREP page is not on the screen {wait} seconds after the MON CALL line, FORCE OFF (boot 20, with a 5 s box: '
                                       'the call returned 5.04 s after the MON CALL line, OBSERVED once)')
        for wait, box in ((60, 5), (90, 30), (180, 120)):
            said = flat(' '.join(r2.stop_rule(wait, box)))
            with self.subTest(box=box):
                self.assertIn(f'if the SNAPREP page is not on the screen {wait} seconds after the MON CALL line, FORCE OFF', said)
                self.assertIn(f'the panel still dark {wait} seconds after the MON CALL line, or a restart', said)
                self.assertIn(f'when, inside the {box} s, is UNKNOWN', said)
                self.assertIn('does not count as a page change for the silence rule above', said)
                self.assertNotIn('counts as a page change', said.replace('does not count as a page change', ''))
                self.assertNotIn('only a panel that is still dark', said)

    def test_the_words_of_the_hooks_name_the_wait_and_the_hold(self):
        guide = CT.rec.m1g
        self.assertIn('one CPU stays loaded, at worst, until the operator acts: 60 s after the MON CALL line, whatever the guest paints (section 5), plus about 20 s holding Power, 80 s '
                      'or more', r2.runaway(60, 20))
        self.assertEqual(guide.runaway_words(5, {'panel': 'console'}), r2.runaway(60, 20))
        self.assertIn('60 s of silence (section 5)', guide.runaway_words(5), 'the first run keeps its own words')
        self.assertEqual(r2.hang({}, 5, 60), 'a lit screen with neither STAGE=DONE nor the SNAPREP page 60 s after the MON CALL line, whatever the guest paints (no controlled stop: row d; a '
                                              'panel still dark then is NOT A RESULT, below)')
        self.assertEqual(r2.row_e({}, 5, 60), '', 'a hand-made record without panel facts has no row e words')
        self.assertIn('EL2 loops of 2764800 loads of 8 bytes', r2.row_e({'panel_facts': {'fb_span': 22118400}}, 5, 60))

    def test_the_wait_is_counted_from_the_mon_call_line_in_step_5_and_in_5d_for_any_wait(self):
        for wait in (60, 90, 180):
            count = f'Count those {wait} seconds from the moment the MON CALL line is on the screen, on a watch or the camera\'s timer.'
            with self.subTest(wait=wait):
                self.assertIn(count, r2.step5({}, 5, wait))
                self.assertEqual(flat(' '.join(r2.section_5d({'run': self.same, 'fb_span': 22118400}, wait))).count(count), 1)
        self.assertNotIn('Count those', flat(' '.join(r2.stop_rule(60, 5))), 'the stop rule of section 5 says the rule; step 5 and 5d say how to time it')


class Record(CT.BT.Case):
    """The records of the three choices, as the builder makes them for the stand-in guest (the stand-in pair stands for the first run's: tools/test-sp11-stick-monitor-panel.py)."""

    def test_section_0_of_the_console_record_gives_the_reason_after_the_decision_it_explains(self):
        text = flat(STATE['console'])
        self.assertLess(text.index('APPROVING THIS TEXT ALSO DECIDES A CHOICE OF D9 THAT IS NOT DECIDED YET'), text.index('WHY THE PANEL IS HANDED NOW'))
        self.assertLess(text.index('WHY THE PANEL IS HANDED NOW'), text.index('Approving this text covers ONE boot of this stick.'))
        self.assertLess(text.index('FALSIFIER'), text.index('0a. Where things stand'))
        self.assertLess(text.index('0a. Where things stand'), text.index('0b. The hazards, in plain words'))
        self.assertEqual(text.count('ended as MON STOP WATCHDOG'), 1, 'boot 20\'s result is section 0a\'s: the console words point there and do not say it again')
        self.assertIn('section 0a has the analysis', text)
        self.assertLess(text.index('0a. Where things stand'), text.index('The stop-site analysis of that address'))
        self.assertLess(text.index('The stop-site analysis of that address'), text.index('The stick was unchanged but for the Shell\'s marker'))
        self.assertIn('The two programs and the time box are boot 20\'s (section 3c)', text, 'the stand-in pair stands for the first run\'s here')

    def test_each_sentence_of_section_0_0a_3c_and_5d_is_pinned_and_a_record_without_it_is_rejected(self):
        f = flat(STATE['console'])
        section0 = f.split('0. What approving this text decides (read this first)')[1].split('0a. Where things stand')[0]
        stand = f.split('0a. Where things stand')[1].split('0b. The hazards, in plain words')[0]
        block = f.split('3c. The panel as the kernel\'s console')[1].split('4. Time box')[0]
        five_d = f.split('5d. The panel as the kernel\'s console')[1]
        mutants_rejected(self, section0, WHY_PINS)
        mutants_rejected(self, stand, STAND_PINS)
        mutants_rejected(self, block, PUBLIC_PINS)
        mutants_rejected(self, five_d, FIVE_D_PINS)
        for word in ('five instructions that use registers only', 'PE_init_iokit', 'all-zero boot_args.Video', 'no register was printed'):
            with self.subTest(word):
                self.assertNotIn(word, section0, 'the analysis is in 0a and in no other place of section 0')
                self.assertIn(word, stand)

    def test_each_sentence_that_a_hook_puts_in_the_record_is_pinned_and_a_record_without_it_is_rejected(self):
        mutants_rejected(self, STATE['console'], hook_pins(STATE['console']))

    def test_a_fault_in_the_panel_range_is_not_the_success_of_5a_and_each_choice_says_where_it_is_taken_up(self):
        for choice, where in (('console', '5d'), ('handed', '3c')):
            with self.subTest(choice):
                self.assertEqual(flat(STATE[choice]).count(f'(not when A= lies inside the panel range: section {where})'), 2, 'the success paragraph and the M1 PASSED bullet')
        self.assertNotIn('not when A= lies inside the panel range', flat(STATE['withheld']), 'the first run maps no panel: its success is the plan\'s')
        self.assertIn('S2_FAULT with A= inside it is not the success that section 5a gives for a fault elsewhere', flat(STATE['handed']))

    def test_what_boot_20_corrected_is_in_every_kind_of_record_once(self):
        for choice in ('withheld', 'handed', 'console'):
            with self.subTest(choice):
                mutants_rejected(self, STATE[choice], SHARED_PINS)
                for stale in STALE:
                    self.assertNotIn(stale, flat(STATE[choice]))

    def test_the_wait_for_the_snaprep_page_is_the_same_in_every_place_the_operator_reads_it(self):
        for choice in ('handed', 'console'):
            f = flat(STATE[choice])
            with self.subTest(choice):
                self.assertEqual(f.count(f'if the SNAPREP page is not on the screen {WAIT} seconds after the MON CALL line, FORCE OFF'), 4 if choice == 'console' else 3)
                self.assertNotIn('counts as a page change for the silence rule', f)
                self.assertNotIn('a black panel, or a restart of the Surface, before STAGE=DONE', f)
                self.assertNotIn('a page that stops at MON CALL and does not change', f)
                self.assertNotIn('a page that stands still for the prepare, the box and the stop', f)
                self.assertNotIn('the page does not change until it returns:', f)
        for rule in ('60 s of silence (section 5)', 'a page that stands still for the prepare, the box and the stop'):
            self.assertIn(rule, flat(STATE['withheld']), 'the first run keeps its own words')

    def test_the_second_camera_the_analysis_and_the_public_source_are_the_console_records_only(self):
        for choice in ('withheld', 'handed'):
            f = flat(STATE[choice])
            for word in ('second camera', 'TOP-LEFT quarter', 'The stop-site analysis of that address', 'What the public source says', 'either camera', '5d. '):
                with self.subTest(choice, word=word):
                    self.assertNotIn(word, f)
            self.assertIn('Keep the whole panel in frame.', f)
        # the words that both panel choices share (step 4's GUEST.DAT line and the label, step 6's margin) are not the first run's
        self.assertNotIn('INDEX=2 ... BYTES=', flat(STATE['withheld']))
        self.assertNotIn('wait ten seconds on the final screen', flat(STATE['withheld']))
        self.assertEqual(flat(STATE['console']).count('Keep the whole panel in frame.'), 1, 'the first run\'s sentence of step 2 stays, and the second camera follows it')
        self.assertIn('Then compare, on the PAYLOAD LOADED line, INDEX=2 ... BYTES=', flat(STATE['handed']) + flat(STATE['console']))

    def test_a_reading_of_a_file_is_never_called_observed_in_the_words_of_this_choice(self):
        for rel in ('tools/sp11_stick_bundle_panel.py', 'tools/sp11_stick_bundle_panel_rung2.py'):
            with self.subTest(rel):
                self.assertEqual(observed_problems((ROOT / rel).read_text()), [])
        f = flat(STATE['console'])
        for name, part in (('0', f.split('WHY THE PANEL IS HANDED NOW')[1].split('0a. Where things stand')[0]), ('0a', ' '.join(r2.stand_extra({}))),
                           ('3c', f.split('3c. The panel as the kernel\'s console')[1].split('4. Time box')[0]), ('5d', f.split('5d. The panel as the kernel\'s console')[1])):
            with self.subTest(name):
                self.assertEqual(observed_problems(part), [])
        docs = (ROOT / 'docs/SP11-STICK-MONITOR.md').read_text()
        self.assertEqual(observed_problems(docs.split('**K7, rung 2 after boot 20')[1].split('\n\n')[0]), [])
        for said in ('Its cell is 8 x 16 pixels (OBSERVED on a host file)', 'the loop is (OBSERVED in the kernelcache)', 'OBSERVED'):
            self.assertTrue(observed_problems(said), said)

    def test_a_generator_without_one_of_the_new_blocks_makes_a_record_that_the_pins_reject(self):
        guide = CT.rec.m1g
        pins = {**WHY_PINS, **STAND_PINS, **PUBLIC_PINS, **FIVE_D_PINS, **hook_pins(STATE['console'])}
        for what, patch in {'no second camera in step 2': mock.patch.dict(guide.PANEL['console'], {'film': ''}),
                            'no stop-site analysis in 0a': mock.patch.dict(guide.PANEL['console'], {'stand': lambda real: ()}),
                            'no why paragraph in section 0': mock.patch.dict(guide.PANEL['console'], {'why': lambda real: ()}),
                            'no public-source paragraph in 3c': mock.patch.object(r2, 'public_text', lambda: 'x'),
                            'no section 5d': mock.patch.object(r2, 'section_5d', lambda panel, wait: ()),
                            'no step 4 comparison': mock.patch.dict(guide.PANEL['console'], {'step4': None}),
                            'no wait in step 5': mock.patch.dict(guide.PANEL['console'], {'step5': ''}),
                            'no margin in step 6': mock.patch.dict(guide.PANEL['console'], {'step6': ''}),
                            'no dark panel in step 7': mock.patch.dict(guide.PANEL['console'], {'step7': ''}),
                            'no row e words': mock.patch.dict(guide.PANEL['console'], {'row_e': ''}),
                            'no fault note in 5a': mock.patch.dict(guide.PANEL['console'], {'fault_note': ''}),
                            'no count of the wait': mock.patch.object(r2, 'COUNT', ''),
                            'the first run\'s no controlled stop': mock.patch.dict(guide.PANEL['console'], {'hang': ''}),
                            'the first run\'s not a result': mock.patch.dict(guide.PANEL['console'], {'no_result': ''}),
                            'the first run\'s runaway': mock.patch.dict(guide.PANEL['console'], {'runaway': None}),
                            'no hazard of the panel in 0b': mock.patch.dict(guide.PANEL['console'], {'hazard': ''}),
                            'the first run\'s reading pointer': mock.patch.dict(guide.PANEL['console'], {'also_reads': (), 'evidence_to': '3b'})}.items():
            with self.subTest(what), patch:
                code, text, err = CT.PT.record('mutant-a3', 'console')
                self.assertEqual(code, 0, err)
                self.assertNotEqual(text, STATE['console'])
                self.assertTrue(pin_problems(text, pins), what)

    def test_the_other_choices_have_none_of_it(self):
        for choice in ('withheld', 'handed'):
            text = flat(STATE[choice])
            for word in ('WHY THE PANEL IS HANDED NOW', '0XFFFFFE000C1EEF00', 'RUNG 2 PASSED', 'FALSIFIER', 'What the run may end as'):
                with self.subTest(choice, word=word):
                    self.assertNotIn(word, text)

    def test_the_handed_record_says_what_it_shares_with_the_first_run_in_the_same_derived_words(self):
        text = flat(STATE['handed'])
        self.assertIn('The panel needs no change of the monitor, the wrapper or the reporter', text)
        self.assertIn(f'have the SHA-256 that boot 20 ran ({r2.RUNG1_MONITOR[:8]}... and {r2.RUNG1_REPORTER[:8]}..., in full in section 1)', text)
        self.assertNotIn('Made with the same M1 pair as the first run\'s record', text, 'the first sentence of that kind was written for one pair and one box')

    def test_the_console_record_names_the_programs_and_the_box_it_was_made_with(self):
        text = STATE['console']
        monitor, reporter = (re.search(rf'payload {n}  SNAP{name}.BIN  \d+ bytes  sha256 ([0-9a-f]{{64}})', text).group(1) for n, name in ((0, 'MON'), (1, 'REP')))
        self.assertEqual((monitor, reporter), (r2.RUNG1_MONITOR, r2.RUNG1_REPORTER))
        self.assertIn('4. Time box: 5 s of wall time', text)

    def test_boot_20s_stage_2_observation_is_said_once_in_0b_and_as_one_of_the_map_of_ram_only(self):
        ram_bullet = ('the guest is given RAM and the panel range only (INFERRED; boot 20\'s 5 s, with the map of RAM only, showed no stage-2 abort, OBSERVED once; this map, with '
                      'the panel range, has not run)')
        for choice in ('handed', 'console'):
            text = flat(STATE[choice])
            zero_b = text.split('0b. The hazards, in plain words')[1].split('1. What may run')[0]
            with self.subTest(choice):
                self.assertIn(ram_bullet, zero_b)
                self.assertEqual(zero_b.count('no stage-2 abort'), 1, 'said once in 0b')
                self.assertEqual(zero_b.count('map of RAM only'), 1)
                self.assertNotIn('of section 0a and of the bullet below is of the map of RAM only', text, 'the sentence that said it a second time is gone')
                self.assertIn('boot 20\'s map of RAM only showed no stage-2 abort in 5 s, OBSERVED once, and this map with the panel range has not run', text)
        self.assertNotIn('map of RAM only', flat(STATE['withheld']), 'the first run\'s text says no such thing: its map is the one that boot 20 ran')

    def test_the_new_text_is_in_the_record_once_each_in_the_section_that_holds_it(self):
        text = flat(STATE['console'])
        block = text.split('3c. The panel as the kernel\'s console')[1].split('4. Time box')[0]
        five_d = text.split('5d. The panel as the kernel\'s console')[1]
        for phrase, where in (('What the run may end as, if the loop is gone', block), ('What the public source says of the console', block), ('RUNG 2 PASSED:', five_d),
                              ('RUNG 2 PARTIAL:', five_d), ('RUNG 2 NOT PASSED:', five_d), ('RUNG 2 NOT A RESULT:', five_d),
                              ('The loop of boot 20 is the five instructions from', five_d), ('FALSIFIER', text)):
            with self.subTest(phrase):
                self.assertEqual(text.count(phrase), 1)
                self.assertIn(phrase, where)


class Hygiene(unittest.TestCase):
    def test_the_new_files_are_ascii_with_an_spdx_line_within_the_size_limits_and_hold_no_machine_data(self):
        for rel in ('tools/sp11_stick_bundle_panel_rung2.py', 'tools/test-sp11-stick-monitor-rung2.py'):
            data = (ROOT / rel).read_bytes()
            with self.subTest(rel):
                self.assertFalse([b for b in data if b > 127], 'ASCII only')
                self.assertIn(b'SPDX-License-Identifier: MIT', data[:400])
                self.assertLess(len(data), 51200)
                self.assertLess(data.count(b'\n'), 1500)
                for needle in BT.MACHINE_PATHS:
                    self.assertNotIn(needle.encode(), data)

    def test_the_module_cites_no_disassembly_and_no_vendor_byte(self):
        source = (ROOT / 'tools/sp11_stick_bundle_panel_rung2.py').read_text()
        self.assertNotRegex(source, r'\b(?:ldr|str|cmp|b\.hi|csel|add|sub)\s+[wx]\d', 'a description of the loop, not its instructions')
        self.assertNotIn('VERIFIED', source, 'no record of this tool carries that label')


if __name__ == '__main__':
    unittest.main()
