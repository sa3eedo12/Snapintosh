#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of the choice of D9 that makes the panel the kernel's console, for the monitor entry run (monitor port work item 4b, rung 2; docs/SP11-STICK-MONITOR.md section 20):
`real --m1-run --panel console --sptm-hvc-calls 0`, the panel handed with serial=2 in the command line, as probes/run.py makes it for the A16's native panel console. The
text of its record (what the operator will see, what the console route turns off, what the runner's page does to the same pixels, what success is), what that text states of
q1n1's runner (a model of the runner's placement and, with $Q1N1_SP11_TREE, its source), the sources every section 3c cites, and the limit of the approved record's file,
which this record is the first to need raised. The reader, the options, the labels and the record of the panel handed are tested in tools/test-sp11-stick-monitor-panel.py,
whose fixtures and stand-in loader are loaded here with a scratch directory of their own (build/sp11-monitor-console-test-scratch/, deleted at the end).

    python3 tools/test-sp11-stick-monitor-console.py

The runner's source is read only when the tree is there and its tests SKIP without it, which is never a pass. The real guest, the J700 26A428 kernelcache, is the end-to-end test
of tools/test-sp11-stick-monitor-builder.py. No hardware: NOT RUN."""
import importlib.util
import re
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))

spec = importlib.util.spec_from_file_location('sp11_monitor_panel_tests', TOOLS / 'test-sp11-stick-monitor-panel.py')
PT = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PT)
SCRATCH = ROOT / 'build' / 'sp11-monitor-console-test-scratch'
PT.SCRATCH = PT.BLD.SCRATCH = PT.AUTH.SCRATCH = SCRATCH
pn, rec, BT, bb, ch, sha = PT.pn, PT.rec, PT.BT, PT.bb, PT.ch, PT.sha
STATE, record, record_problems, sections = PT.STATE, PT.record, PT.record_problems, PT.sections
CHOICES, LABEL_CONSOLE, GOLDEN = PT.CHOICES, PT.LABEL_CONSOLE, PT.GOLDEN


def setUpModule():
    PT.setUpModule()


def tearDownModule():
    PT.tearDownModule()


class Console(BT.Case):
    """The record of the panel as the kernel's console: the panel handed with serial=2, so that the kernel's own text is what the operator can see."""

    def setUp(self):
        self.text, self.panel = STATE['console'], STATE['panel']['console'][0]
        self.flat = ' '.join(self.text.split())
        self.handed = ' '.join(STATE['handed'].split())

    def test_the_record_is_consistent_with_its_guest(self):
        self.assertEqual(record_problems(self.text, 'console', self.panel), [])

    def test_the_record_is_the_one_that_was_made(self):
        self.assertEqual(sha(self.text.encode()), GOLDEN['console'], 'a changed byte of this record is another record: re-pin it on purpose, with the doc')

    def test_the_first_line_and_section_1a_say_which_choice_it_is(self):
        self.assertIn(f'(format 1, label M1RUN-CONSOLE-{LABEL_CONSOLE})', self.text.splitlines()[0])
        self.assertIn('   D9 as built  panel console: the guest is given the unit\'s framebuffer, and its command line makes the panel the kernel\'s console (serial=2)\n'
                      '                SPTM HVC call patch 0: a stock kernel\n', self.text)
        self.assertIn('   panel        0xe0000000 + 0x1518000 bytes, 2880 x 1920, text mode, the kernel\'s console (serial=2): section 3c\n', self.text)
        self.assertIn('This record is of the choice that hands the guest the panel as the kernel\'s console (serial=2)', self.flat)

    def test_it_opens_with_what_the_operator_will_see_and_what_he_will_not(self):
        head = self.flat.split('3c. The panel as the kernel\'s console (D9: panel handed, serial=2, stock kernel; docs/SP11-STICK-MONITOR.md section 20) ')[1]
        first = head.split('Rung 1 is the first run')[0]
        for needle in (f'In short. This record is rung 2: the first run again with the unit\'s framebuffer, {self.panel["fb_span"]} bytes at {self.panel["fb_pa"]:#x}, handed to '
                       'the guest to read and write, and with serial=2 in the kernel\'s command line, so that its own console is the panel',
                       'Why: boot 20\'s guest spun in a loop of PE_init_iokit that cannot end while the panel is withheld (INFERRED; sections 0 and 0a)',
                       'Expected, INFERRED (the A16 did the console, this unit never): the PC= of the report is not in that loop, and XNU\'s own boot text is on the panel while the '
                       'guest runs, and not on the MON CON lines of the report; from the MON CALL line the screen may go black and fill with that text (below)',
                       'the film is the only record of the text. What to film, what is new and the results of rung 2 are in section 5d, in the operator\'s words; this section is the '
                       'evidence for them'):
            self.assertIn(needle, first)

    def test_it_says_what_rung_2_is_and_that_its_approval_covers_one_boot_and_rung_1s_none(self):
        for needle in ('Rung 1 is the first run: D9\'s first choice, the panel withheld and a stock kernel, decided on 2026-11-16, with its own record and its own approval; boot 20 '
                       'ran it. This record is rung 2, the first run with two inputs of the guest changed: the guest is given',
                       'approving the SHA-256 of this record is that decision, and it covers ONE boot of this stick (section 5c, THIS APPROVAL COVERS ONE BOOT); rung 1\'s approval '
                       'covers none of it',
                       'and so does rung 1, the first run with the panel withheld, whose record has its own approval: this record is rung 2, the panel handed to the guest as the '
                       'kernel\'s console (serial=2), and neither approval covers the other\'s boot.', 'This text also asks the operator to read section 5d: it says what to film, what is new '
                       'beyond section 0b and what counts as a result of rung 2. Section 3c is the evidence for it, which the coordinator reads.',
                       'The operator reads and acts on sections 0, 0a, 0b, 4, 5, 5a, 5c and 5d, and reads in section 1b the paragraph on the NV2 record (D6)',
                       'The rest of sections 1 to 3c is evidence that the coordinator',
                       'APPROVING THIS TEXT ALSO DECIDES A CHOICE OF D9 THAT IS NOT DECIDED YET: the guest is handed the unit\'s panel as the kernel\'s console (serial=2)',
                       'D9 for the first run only: the panel withheld and a stock kernel'):
            self.assertIn(needle, self.flat)
        self.assertNotIn('Rung 2, another file, kernel, patch value', self.flat, 'this record is rung 2: the first run\'s sentence about it does not apply')

    def test_it_says_what_the_guest_receives_and_that_the_command_line_is_the_one_difference_from_the_handed_guest(self):
        for needle in ('command line -v debug=0x8 serial=2 cluster_power=0 wdt=-1', 'PANEL_MISMATCH if it is anything else',
                       'fb_pa 0xe0000000, fb_span 0x1518000 (22118400 bytes, 21.1 MiB): that range on 16 KiB pages (words 32 and 33 of struct params)',
                       'From the guest of the panel handed it differs in the command line only, which is one page of boot_args'):
            self.assertIn(needle, self.flat)
        self.assertIn('the guest is given the unit\'s framebuffer, and its command line has serial=2 where rung 1\'s has serial=3', self.flat)
        self.assertIn('Label: PIN.DAT\'s build_label is M1RUN-CONSOLE-' + LABEL_CONSOLE + '. The builder puts CONSOLE- after M1RUN- for this choice only and refuses a facts label '
                      'that starts with it or with PANEL-, so that the labels of the other two choices cannot read as this one', self.flat)

    def test_it_says_plainly_what_the_console_will_do_and_not_do(self):
        for needle in ('What the operator will see, INFERRED and never run on this unit: XNU\'s own boot text, drawn by the kernel itself into the panel while the guest runs',
                       'The A16 did this: its gate 3, the kernel\'s console on the panel (OBSERVED on the A16, as a history note of this project says; not re-run here: '
                       'docs/GUI-BOOT-ROADMAP.md, the table of gates, and docs/GUI-BOOT-ROADMAP.md section 3)',
                       'docs/COMPATIBILITY.md, section "Running the bundle checks", names the three console modes of probes/run.py',
                       'serial output bit clear, so that printf draws on the panel', 'the command line has serial=2 where the loader\'s has serial=3, the replacement that '
                       'probes/run.py:1591-1594 makes for --console panel', 'reads the command line back from boot_args (PANEL_MISMATCH if it is anything else, serial=3 included)',
                       'Text mode stays, v_display 0, the one where the CPU paints the glyphs (probes/xnuboot.py:188-192)',
                       'the MON CON lines of the report are not expected to carry the kernel\'s console text, because the serial route is off',
                       'are expected to hold none of that text (at most some early lines, printed before the video console takes over: INFERRED from the stop-site analysis of boot 20, '
                       'section 0), and an empty buffer prints no MON CON line at all (probes/sp11-stick-reporter.c:338-360)',
                       'What the kernel\'s kprintf sends to that UART through its own gate is another matter: debug=0x8 stays in the command line, '
                       'and the loader\'s comment says that serial= does not override the kprintf gates (probes/xnuboot.py:1961-1969), so whether any such text reaches the MON CON '
                       'lines is UNKNOWN, and it is not the console and does not count as the aim',
                       'So the console text is on the panel or nowhere (INFERRED): the choice between rung 1 and this one would have given up to 1,024 bytes of its text as MON CON lines',
                       'UNKNOWN: whether the guest reaches its console within the time box at all, and what it prints, because this RELEASE kernel\'s separate kprintf gate still '
                       'prevents relying on its diagnostics', 'a kernel that never takes its console leaves the panel as it was'):
            self.assertIn(needle, self.flat)
        self.assertNotIn('a kernel that prints nothing leaves the panel as it was', self.flat, 'a kernel that takes its console clears the whole panel (the public source, section 3c)')
        self.assertNotIn('goes to the monitor\'s console window and not to the panel', self.flat, 'that is the expectation of the panel handed, not of this choice')
        self.assertNotIn('are expected to be empty', self.flat, 'a kprintf that passes its gate could still reach the MON CON lines: not excluded')

    def test_it_says_what_the_runners_page_does_to_the_same_pixels(self):
        for needle in ('What the runner\'s page does to the same pixels, read in q1n1\'s runner (platform/uefi/main.c: screen_char, payload_emit, sp11_draw_uptime and the clear at '
                       'the runner\'s start; platform/uefi/sp11-payload-text.c: q1n1_payload_region_place), INFERRED for this run because nothing of it was run and this record '
                       'does not check that source', 'the runner paints the whole panel dark once, when it starts, and never clears the panel again',
                       'from (20, 20), in glyphs of 5 x 7 pixels at scale 3 (cells of 18 x 27 pixels), and an emblem 256 pixels square at the top right',
                       'at x = 1664, y = 308, at scale 2 (cells of 12 x 18 pixels), 89 rows', 'A character is drawn as foreground pixels only (colour 0xe8edf5): no background, '
                       'nothing erased, and a line past the region\'s last row is mirrored to the UART only', 'only adds glyph pixels over whatever the kernel left in the region: '
                       'it clears nothing', 'the runner\'s heartbeat redraws, once a second, a blinking block at the bottom left and its UPTIME line at (20, 74), clearing that '
                       'line\'s 576 x 27 pixels first', 'The kernel\'s text starts at the top of the panel, where the runner\'s own page is, and is expected to draw over it: that is no fault, '
                       'and the kernel\'s console is expected to CLEAR the whole panel when it takes it (read in the public source, below; INFERRED for this kernel), which wipes the '
                       'runner\'s page and the wrapper\'s lines drawn before the call: only the film holds them',
                       'Whether the kernel\'s text reaches the runner\'s region is UNKNOWN',
                       'The kernel draws its text only while the guest runs, which is at most the time box and starts after the monitor\'s prepare; what it leaves stays in the '
                       'framebuffer until something paints over it (the report\'s glyphs and the rectangle of 576 x 27 pixels at (20, 74) that the heartbeat clears each second, '
                       'above), so that the screen shows it after the stop too (INFERRED). The console scrolls when its text passes the last row of the panel. Its cell is 8 x 16 pixels, '
                       'read on the host (a file, not a run) in the kernelcache: its initialize_screen, at 0xfffffe000ba55740, sets the console\'s rows to the height shifted right by 4 '
                       'and its columns to the width shifted right by 3, so 120 rows of 360 columns on this panel, whatever the scale (the public source, below), about 0.8 x 1.5 mm on '
                       'this 13-inch panel (INFERRED from its nominal size, docs/SURFACE-PRO-11.md section 4.1), where the runner\'s own lines are 12 x 18 pixels or more. After a long '
                       'text the panel holds only the last rows, and what scrolled off, the wrapper\'s lines of before the call included, is on the film only (INFERRED)'):
            self.assertIn(needle, self.flat)

    def test_section_5d_has_the_film_the_new_hazards_and_the_results_in_60_lines_after_5c_and_3c_points_to_it(self):
        lines = self.text.splitlines()
        start = lines.index(next(line for line in lines if line.startswith('5d. ')))
        block = lines[start:]
        self.assertLessEqual(len(block), 60, 'the operator-facing part of this choice is short')
        self.assertLess(self.flat.index('5c. The operator\'s actions, in order'), self.flat.index('5d. The panel as the kernel\'s console'))
        self.assertTrue(all(len(line) <= 120 for line in block))
        flat5d = ' '.join(' '.join(block).split())
        for head in ('THE FILM is the only record of the kernel\'s text', 'WHAT TO EXPECT.', 'WHAT IS NEW beyond section 0b.', 'THE RESULTS.', 'RUNG 2 PASSED:', 'RUNG 2 PARTIAL:',
                     'RUNG 2 NOT PASSED:', 'RUNG 2 NOT A RESULT:'):
            with self.subTest(head):
                self.assertEqual(flat5d.count(head), 1)
        section3c = self.flat.split('3c. The panel as the kernel\'s console')[1].split('4. Time box')[0]
        self.assertIn('What to film, the new hazards of this choice and the results are in section 5d, in the operator\'s words; this section is the evidence for them', section3c)
        for gone in ('The film. It is the only record', 'New hazards of this choice', 'Results of this record.', 'RUNG 2 PASSED', 'RUNG 2 NOT PASSED'):
            with self.subTest(gone):
                self.assertNotIn(gone, section3c, 'these moved to section 5d')
        self.assertNotIn('3c. The panel as the kernel\'s console', flat5d)

    def test_the_hazards_of_0b_and_5d_are_each_said_once_and_the_handed_record_has_no_5d(self):
        zero_b = self.flat.split('0b. The hazards, in plain words')[1].split('1. What may run')[0]
        flat5d = self.flat.split('5d. The panel as the kernel\'s console')[1]
        for fact in ('which the display scans out', 'The film is the only record of that text', 'a dark panel is EXPECTED'):
            with self.subTest(fact):
                self.assertEqual(zero_b.count(fact), 1)
                self.assertNotIn(fact, flat5d, 'a fact of 0b is not said again in 5d')
        for fact in ('2764800 loads of 8 bytes', 'The kernel\'s video-console code runs on this unit for the first time'):
            with self.subTest(fact):
                self.assertEqual(flat5d.count(fact), 1)
                self.assertNotIn(fact, zero_b)
        self.assertNotIn('5d. ', STATE['handed'])
        self.assertNotIn('5d. ', STATE['withheld'])

    def test_it_keeps_every_statement_of_the_panel_handed_that_does_not_depend_on_the_console_route(self):
        for start, stop in (('How stage 2 maps it', ' Console.'), ('What stays withheld.', ' What the monitor does with the range.'),
                            ('What the monitor does with the range.', ' The screen.'), ('Store class.', ' Success of this record.')):
            paragraph = start + self.handed.split(start)[1].split(stop)[0]
            with self.subTest(start):
                self.assertIn(paragraph, self.flat)

    def test_it_names_the_range_in_the_ram_class_the_placement_and_group_e_and_adds_no_class(self):
        blocks = sections(self.text)
        self.assertIn(f'   panel_base {self.panel["fb_pa"]:#x}  panel_size {self.panel["fb_span"]:#x}  panel_end {self.panel["fb_pa"] + self.panel["fb_span"]:#x}  '
                      '(outside the placement; the guest writes it: section 3c)\n', blocks['2'])
        self.assertIn('and the panel range of section 3c (outside the placement: the guest writes it', ' '.join(blocks['3'].split()))
        self.assertIn('   range, the reserve and the panel range of section 3c.\n', self.text)
        self.assertIn('(PIN.DAT authorised_stores = 7)', self.text)
        self.assertIn('PIN.DAT\'s authorised_stores stays 7', self.flat)

    def test_it_has_the_stop_rule_of_the_panel_as_the_console_and_the_time_rows_of_the_panel_handed(self):
        after = self.text.split('5. Refusal and stop rules')[1]
        wait = rec.m1g.silence_s(5)
        stop_rules = pn.stop_rules(pn.with_run(self.panel, 5, 'a' * 64, 'b' * 64), wait)
        for line in (*rec.M1_STOP_RULES, *stop_rules):
            self.assertIn(line, after)
        rule = ' '.join(' '.join(stop_rules).split())
        self.assertTrue(rule.startswith('- Panel as the console (sections 3c and 5d): Whatever the guest paints, if the SNAPREP page is not on the screen '
                                        f'{wait} seconds after the MON CALL line, FORCE OFF (boot 20, with a 5 s box: the call returned 5.04 s after the MON CALL line, OBSERVED once). '
                                        'What the guest paints, the kernel\'s text included, does not count as a page change for the silence rule above.'))
        self.assertIn(f'the panel still dark {wait} seconds after the MON CALL line, or a restart', rule)
        self.assertNotIn('counts as a page change', rule)
        self.assertIn('From the MON CALL line the screen may go black and fill with the kernel\'s text: that is expected (the kernel\'s console clears the whole panel when it '
                      'takes it: read in the public source, not this kernel\'s own; when, inside the 5 s, is UNKNOWN)', rule)
        self.assertNotIn('Panel handed', after)
        self.assertIn('the only record of the kernel\'s text (the MON CON lines are not expected to carry it)', rule)
        self.assertIn('The runner\'s glyphs and the kernel\'s in the same cells are expected and no finding; ' + pn.CONSOLE_FINDING + ': film it, say what you saw, and FORCE OFF as '
                      'step 6 says.', rule)
        for banned in ('power cycle', 'hold Power', 'series'):
            self.assertNotIn(banned, rule.lower(), 'the reviewer\'s word is FORCE OFF (section 5c defines it)')
        for needle in ('framebuffer: two reads of the panel', 'and the two reads of the framebuffer, UNKNOWN (section 3c)',
                       'The two reads of the framebuffer (section 3c) are CPU time at EL2 on top of the figures above; their length is UNKNOWN.'):
            self.assertIn(needle, self.flat)

    def test_it_is_ascii_within_120_columns_carries_no_verified_label_and_is_the_same_when_made_again(self):
        self.assertTrue(self.text.isascii())
        for name, block in (('3c', self.text.split('\n3c. ')[1].split('\n4. Time box')[0]), ('4', self.text.split('4. Time box')[1].split('5. Refusal')[0])):
            self.assertLessEqual(max(map(len, block.splitlines())), 120, name)
        self.assertNotIn('VERIFIED', self.text)
        self.assertLess(self.text.index('3b. Device code'), self.text.index('3c. The panel as the kernel\'s console'))
        self.assertLess(self.text.index('3c. The panel as the kernel\'s console'), self.text.index('4. Time box'))
        code, again, err = record('again-console', 'console')
        self.assertEqual((code, again), (0, self.text), err)

    def test_the_stand_in_loader_is_left_as_it_was_found_after_a_build_of_this_choice(self):
        seen = []
        code, text, err = record('restore', 'console', lambda built, xb, lay: seen.append((xb, xb.BOOT_CMDLINE)))
        self.assertEqual(code, 0, err)
        xb, during = seen[0]
        self.assertEqual((during, xb.BOOT_CMDLINE), (pn.CMDLINES['console'], pn.LOADER_CMDLINE), 'serial=2 for the build, the loader\'s own line again afterwards')


class Approval(BT.Case):
    """The approved record is read from a file by bb.approve, whose limit (bb.AUTHORISATION_CAP, raised to 128 KiB by the text patch: its own suite holds both sides of it) the
    records of the choices that hand the panel need as well; nothing else limits the record (no stage tree, runner or page reads or copies it: PIN.DAT carries its digest)."""

    def approve(self, text, name):
        path = BT.write(SCRATCH / 'records' / f'{name}.txt', text.encode())
        return bb.approve(types.SimpleNamespace(authorisation=str(path), authorisation_sha256=None), text)

    def test_a_record_of_96000_bytes_is_approved_by_its_digest(self):
        text = 'a record line of twelve\n' * 4000                 # 96,000 bytes: more than the 65,536 that were once allowed, less than the 131,072 that are
        self.assertTrue(65536 < len(text) < bb.AUTHORISATION_CAP)
        self.assertEqual(self.approve(text, 'large').hex(), sha(text.encode()))

    def test_a_large_record_is_still_refused_when_it_is_not_this_builds(self):
        text = 'a record line of twelve\n' * 4000
        with self.assertRaises(ch.Refused) as caught:
            bb.approve(types.SimpleNamespace(authorisation=str(BT.write(SCRATCH / 'records' / 'other.txt', (text + 'x').encode())), authorisation_sha256=None), text)
        self.assertEqual(caught.exception.name, 'AUTH_RECORD_MISMATCH')

    def test_every_record_of_the_choices_leaves_a_sixteenth_of_the_limit_free(self):
        for choice in CHOICES:
            with self.subTest(choice=choice):
                self.assertLess(len(STATE[choice].encode()), bb.AUTHORISATION_CAP * 15 // 16, 'a record at fifteen sixteenths of the limit leaves no room for the next change of the shared prose: raise the cap on purpose')

    def test_nothing_else_reads_the_record_file_with_a_limit_of_its_own(self):
        readers = sorted(p.name for p in TOOLS.glob('*.py') if not p.name.startswith('test-') and re.search(r'--authorisation[\'"]?,\s*cap=', p.read_text()))
        self.assertEqual(readers, ['sp11_stick_bundle_build.py'], 'one reader with a limit; sp11_monitor_census.py check reads the record whole, the stager and the runner not at all')
        census = (TOOLS / 'sp11_monitor_census.py').read_text()
        self.assertIn("Path(a.record).read_text(encoding='ascii')", census)
        self.assertNotIn('authorisation', (TOOLS / 'stage-sp11-boot-media.py').read_text())


class Sources(unittest.TestCase):
    def test_every_citation_of_section_3c_has_its_fragment_on_its_lines(self):
        for path, first, last, fragments in pn.CITES:
            first, last, fragments = pn.r5.REPORTER_DRIFT.get((path, first, last), (first, last, fragments))      # the reporter of rung 6 has these lines elsewhere (the text keeps its approved cites)
            lines = (ROOT / path).read_text().splitlines()
            held = '\n'.join(lines[first - 1:last])
            for fragment in fragments:
                with self.subTest(f'{path}:{first}-{last} {fragment}'):
                    self.assertIn(fragment, held)

    def test_the_text_cites_nothing_that_the_table_does_not_hold_and_the_table_holds_nothing_the_text_does_not_cite(self):
        """The citations of section 3c of both choices and of the ram class of the choices (the rest of the record cites its own sources, held by the suites that wrote them)."""
        pattern = r'((?:probes|shadow|docs|compat|tools)/[A-Za-z0-9_./+-]+\.(?:c|h|py|md)):(\d+)(?:-(\d+))?'
        blocks = {c: STATE[c].split('\n3c. ')[1].split('\n4. Time box')[0] for c in ('handed', 'console')}
        cited = {(path, int(first), int(last or first)) for path, first, last in re.findall(pattern, ' '.join(blocks.values()) + ' ' + ' '.join(pn.RAM_NEW))}
        held = {(path, first, last) for path, first, last, _ in pn.CITES}
        self.assertEqual(sorted(cited - held), [], 'cited, and the table that holds the fragments does not have it')
        self.assertEqual(sorted(held - cited), [], 'in the table, and the text does not cite it')
        for choice, block in blocks.items():
            self.assertLessEqual({(p, int(f), int(last or f)) for p, f, last in re.findall(pattern, block)}, held, choice)

    def test_every_document_the_text_cites_is_cited_by_section_and_held_by_the_table_and_no_line_of_a_document_is_cited(self):
        blocks = ' '.join(' '.join(STATE[c].split('\n3c. ')[1].split('\n4. Time box')[0].split()) for c in ('handed', 'console'))
        self.assertEqual(re.findall(r'docs/[A-Za-z0-9_-]+\.md:\d+', blocks), [], 'the documents move: a section is cited, not a line')
        for ref, path, heading, fragments in pn.DOC_CITES:
            lines, fenced, at, end, level = (ROOT / path).read_text().splitlines(), False, None, None, len(heading.split()[0])
            for number, line in enumerate(lines):
                if line.lstrip().startswith('```'):
                    fenced = not fenced
                elif fenced:
                    continue
                elif at is None and line.startswith(heading):
                    at = number
                elif at is not None and re.match(rf'#{{1,{level}}} ', line):
                    end = number
                    break
            with self.subTest(ref):
                self.assertIsNotNone(at, f'{path} has no section that opens with {heading!r}')
                body = '\n'.join(lines[at:end])
                for fragment in fragments:
                    self.assertIn(fragment, body)
                self.assertIn(ref, blocks, 'in the table, and the text does not cite it')

    def test_the_reporter_reads_no_framebuffer_word_so_the_page_cannot_show_the_hashes(self):
        source = (ROOT / 'probes/sp11-stick-reporter.c').read_text()
        for word in ('OUT_FRAMEBUFFER', 'fb_pa', 'fb_span', 'fb_observed', 'fb_before', 'framebuffer'):
            self.assertNotIn(word, source)

    def test_the_monitor_uses_fb_pa_and_fb_span_only_where_the_text_says(self):
        found = sorted(p.name for p in (ROOT / 'probes/xnuboot').glob('*.c') if re.search(r'\bfb_(?:pa|span)\b', p.read_text()))
        self.assertEqual(found, ['context.c', 'fine_locks_calls.c', 'kernel_views.c', 'prepare.c', 'report.c', 'report_layout.c', 'sptm_dispatch.c', 'stage2.c'])
        said = ' '.join(STATE['console'].split('\n3c. ')[1].split('\n4. Time box')[0].split())
        for name in found:
            if name != 'report_layout.c':       # the struct that holds the two words, not a use of them
                with self.subTest(name):
                    self.assertIn(name, said, 'every file that uses them is named: m7 of the review of rung 2')

    def test_the_sections_of_the_notes_that_the_text_names_exist(self):
        for doc, headings in {'docs/SP11-STICK-REAL.md': ('## 2. What ran', '### 3.1 What `--descriptor` does in `real`'), 'docs/SP11-MONITOR-PROFILE.md': ('## 3. The flag set',),
                              'docs/SP11-MONITOR-PORT.md': ('## 2. Milestone M1 and the verdict', '## 9. Platform contract and the gates (Q7)'),
                              'docs/SP11-STICK-MONITOR.md': ('## 14. Phase D', '## 20. Phase F'), 'docs/GUI-BOOT-ROADMAP.md': ('## 3. Kernel console on the panel (September 2026)',)}.items():
            text = (ROOT / doc).read_text()
            for heading in headings:
                self.assertIn(heading, text, f'{doc}: {heading}')

    def test_the_console_route_of_each_choice_is_the_one_probes_run_py_makes_of_it(self):
        run = (ROOT / 'probes/run.py').read_text()
        self.assertIn("xb.BOOT_CMDLINE = xb.BOOT_CMDLINE.replace('serial=3', 'serial=2')", run)
        self.assertIn('serial=3', pn.LOADER_CMDLINE)
        self.assertEqual(pn.CMDLINES['console'], pn.LOADER_CMDLINE.replace('serial=3', 'serial=2'), 'the replacement of probes/run.py:1594, on the loader\'s line')
        self.assertIn("xb.BOOT_CMDLINE = loader_cmdline.replace('serial=3', 'serial=2')", (TOOLS / 'sp11_stick_bundle_chunks.py').read_text(), 'the builder makes the same replacement')
        self.assertIn('serial=3', (ROOT / 'probes/xnuboot.py').read_text())
        plan = ' '.join((ROOT / 'docs/SP11-MONITOR-PORT.md').read_text().split())
        self.assertRegex(plan, r'D9, decided 2026-11-16 .*?\*\*Console:\*\* the guest\'s `serial=3` output reaches the first 1 KiB of the report\'s console window .*?glyphs are X5')


class Runner(unittest.TestCase):
    """What section 3c of the record of the panel as the console says of q1n1's runner: the numbers from a model of its placement of the page for the unit's panel, and, when
    $Q1N1_SP11_TREE names the tree, the constants and the behaviour from its source. The record does not check that source (it says so)."""
    PAD, GAP, EMBLEM_BOTTOM, PAGE_COLS, REGION_LINES, LINE_MAX = 20, 24, 296, 90, 60, 100

    def place(self, width, height):
        """The region of q1n1_payload_region_place (platform/uefi/sp11-payload-text.c) for a panel of `width` x `height`: (placement, scale, x, y, rows)."""
        page_scale = 3 if width >= 1600 else 2
        rows_fit = lambda scale: 0 if height <= self.PAD + 7 * scale else (height - self.PAD - 7 * scale - 1) // (9 * scale) + 1     # noqa: E731
        left = self.PAD + self.PAGE_COLS * 6 * page_scale + self.GAP
        for scale in range(min(page_scale, 2), 0, -1):
            col0 = (left - self.PAD + 6 * scale - 1) // (6 * scale)
            row0 = (self.EMBLEM_BOTTOM - self.PAD + 9 * scale - 1) // (9 * scale)
            last = self.PAD + (col0 + self.LINE_MAX + 1 - 1) * 6 * scale
            if last < width and rows_fit(scale) > row0 and rows_fit(scale) - row0 >= self.REGION_LINES + 2:
                return 'right', scale, self.PAD + col0 * 6 * scale, self.PAD + row0 * 9 * scale, rows_fit(scale) - row0
        return 'below', page_scale, self.PAD, None, None

    def test_the_model_puts_the_page_of_the_unit_s_panel_where_the_record_says(self):
        self.assertEqual(self.place(2880, 1920), ('right', 2, 1664, 308, 89))
        flat = ' '.join(STATE['console'].split())
        self.assertIn('at x = 1664, y = 308, at scale 2 (cells of 12 x 18 pixels), 89 rows', flat)
        self.assertIn('(cells of 18 x 27 pixels)', flat)
        self.assertEqual(self.place(1280, 800)[0], 'below', 'a small panel gets the region below the page: the model is not one number')

    def test_the_runner_source_has_the_constants_and_the_behaviour_the_text_states(self):
        tree = bb.q1n1_tree()
        if tree is None:
            self.skipTest('no q1n1 checkout (Q1N1_SP11_TREE): the runner source is not read, so the numbers of the record rest on the model alone')
        main = (tree / 'platform/uefi/main.c').read_text()
        text = (tree / 'platform/uefi/sp11-payload-text.c').read_text()
        abi = (tree / 'platform/uefi/sp11-payload-abi.h').read_text()
        header = (tree / 'platform/uefi/sp11-payload.h').read_text()
        for value, source in ((r'#define PAD 20u', text), (r'#define GAP 24u', text), (r'#define EMBLEM_BOTTOM 296u', text), (r'#define PAGE_COLS 90u', text),
                              (r'#define NEED_ROWS \(Q1N1_PAYLOAD_REGION_LINES \+ 2u\)', text), (r'#define NEED_COLS \(Q1N1_PAYLOAD_LINE_MAX \+ 1u\)', text),
                              (r'#define Q1N1_PAYLOAD_LINE_MAX 100u', abi), (r'#define Q1N1_PAYLOAD_REGION_LINES 60u', header)):
            self.assertRegex(source, value)
        self.assertIn('fb.scale = fb.width >= 1600 ? 3 : 2;', main)
        self.assertIn('for (uint32_t scale = page_scale < 2 ? page_scale : 2; scale >= 1; scale--)', text)
        for behaviour in ('rect(20 + fb.col * 6 * fb.scale + i * fb.scale, y + j * fb.scale,', 'fb.scale, fb.scale, 0xe8edf5);',        # glyph pixels only, in this colour
                          'rect(0,0,fb.width,fb.height,0x080b10);',                                                                      # the clear, at the runner's start
                          'rect(20, 20 + row * 9 * fb.scale, 32 * 6 * fb.scale, 9 * fb.scale, 0x080b10);',                               # the heartbeat's own cells
                          'Past the region\'s last row it is mirrored only.'):
            self.assertIn(behaviour, main)
        self.assertEqual(len(re.findall(r'rect\(0,0,fb\.width,fb\.height,0x080b10\)', main)), 3, 'the clears that the text knows of: none after the page is drawn (reviewed by hand)')
        draw = main.split('static void screen_char(char c)')[1].split('static void out(')[0]
        self.assertNotIn('0x080b10', draw, 'screen_char paints no background')
        self.assertIn('fb.col++', draw)


if __name__ == '__main__':
    unittest.main()
