#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The words of rung 7 of the monitor entry run (tools/sp11_stick_bundle_panel_rung7.py and ..._rung7d.py; docs/SP11-STICK-MONITOR.md section 20, K15; docs/SP11-M1-GUEST-PROFILES.md section 5): a
phrase pin and a deletion mutant for each sentence of sections 0, 0a, 0b, 3c, 5b, 5c and 5d; the keyed blocks of boots 24, 25 and 26 are held to the record's rules; the corrections that the
host analysis of boot 26 made (the stop is one pulse in the scaler's HAL init and no loop, the silence is the second of two RTC waits, the RTC messages are not on the film, the cut-off line is a
load request for pptp, the margin of the CPU wait is about 2 s) are in the text and the stale sentences of rung 6's record are in no part of it; the classes of section 5d are exclusive, in order and
leave no film without one; no sentence says what the quiet guest is made of that the builder's comparison did not find. The mechanism (the closed list of three forms, the pairs, the label, the
comparison, the eight kinds against each other, the sizes and K15) is tools/test-sp11-stick-monitor-rung7.py, whose fixtures run here.

    python3 tools/test-sp11-stick-monitor-rung7-words.py

Boot 26's numbers are the coordinator's reading of the operator's film (OBSERVED, one unit, one boot); what the kernel does after them is read on the host in the kernelcache and the public source
and is INFERRED. Nothing here runs the unit: Hardware NOT RUN."""
import importlib.util
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))

spec = importlib.util.spec_from_file_location('sp11_monitor_rung7_mechanism', TOOLS / 'test-sp11-stick-monitor-rung7.py')
M7 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M7)
STATE, ch, m1, pn, M5, flat = M7.STATE, M7.ch, M7.m1, M7.pn, M7.M5, M7.M6.flat
rp, r2, r3, r5, r6, r7 = M5.rp, M5.r2, M5.r3, M5.r5, M7.M6.r6, M7.r7
import sp11_stick_bundle_panel_rung7d as r7d  # noqa: E402
import sp11_stick_bundle_record_m1_guide as m1g  # noqa: E402

FIVE_D = '5d. Rung 7: the aim'
FIVE_B = '5b. The page, line by line'
HEAD3C = '3c. The panel as the kernel\'s console with the CPU-start stores acknowledged, let_panic, a wait of 120 s and a quiet guest'


def setUpModule():
    M7.setUpModule()


def tearDownModule():
    M7.tearDownModule()


def pin_problems(text, pins):
    """The names of the pins whose phrase is not in `text` (line breaks gone)."""
    f = flat(text)
    return [name for name, phrase in pins.items() if flat(phrase) not in f]


def mutants_rejected(test, text, pins):
    """`text` holds each phrase of `pins` once, and without one phrase it is rejected by the pin of that phrase alone."""
    f = flat(text)
    test.assertEqual(pin_problems(f, pins), [])
    for name, phrase in pins.items():
        with test.subTest(pin=name):
            test.assertEqual(f.count(flat(phrase)), 1, 'each sentence is in the text once')
            test.assertEqual(pin_problems(f.replace(flat(phrase), 'DAMAGED'), pins), [name])


def sections(text):
    f = flat(text)
    return dict(section0=f.split('0. What approving this text decides (read this first)')[1].split('0a. Where things stand')[0],
                section0a=f.split('0a. Where things stand')[1].split('0b. The hazards, in plain words')[0],
                section0b=f.split('0b. The hazards, in plain words')[1].split('1. What may run')[0],
                section3c=HEAD3C + f.split(HEAD3C)[1].split('4. Time box')[0], section5b=f.split(FIVE_B)[1].split('5c. The operator\'s actions')[0],
                section5c=f.split('5c. The operator\'s actions')[1].split(FIVE_D)[0], section5d=f.split(FIVE_D)[1])


PINS = {
    '0 d5': 'for rung 6 the operator chose 120 s in the app on 2026-11-19 ("let\'s go with 120 seconds", the answer to the question whether its box should be 120 s or 60 s), rung 6 ran inside it',
    '0 confirms': 'and this record asks the same box, so that approving this text confirms it for this record',
    '0 word': '(2) A guest command line with cpu_boot_timeout_secs=120 where rung 6\'s had 90, so that XNU\'s own wait for the CPU that never comes, which would end about 122 s after the MON CALL line '
              '(INFERRED; the margin to the end of the box is about 2 s), ends outside the box and cannot cut the run',
    '0 profile': '(3) A GUEST DEVICE TREE WITH NODES WITHDRAWN: the profile quiet',
    '0 pair': 'the reporter v3, SNAPREP-M1-ACK.BIN, which are rung 6\'s byte for byte',
    '0 why': 'WHY RUNG 7. Rung 6 (boot 26, record d9d79c87...5cda) ran 61.60 s of its box of 120 s, answered the question that it asked (guest timer interrupts ARE delivered: INFERRED from VECTORS=969, OBSERVED once)',
    '0 two things': 'THIS RECORD CHANGES TWO THINGS FROM RUNG 6, IN GUEST.DAT ONLY.',
    '0 margin': 'outside the box by a margin of about 2 s.',
    '0 aim': 'THE AIM, THE PREDICTION, ITS FALSIFIERS AND WHAT THE FILM DECIDES are in section 5d',
    '0 decided': 'Decided by the operator on 2026-11-19 (the answer "let\'s go with 120 seconds", quoted from the app, to the question whether the box of rung 6\'s record should be 120 s or 60 s):',
    '0 box': 'the operator chose this box in the app on 2026-11-19 for rung 6 (D5\'\', "let\'s go with 120 seconds"), this record asks the same, and approving this text confirms that choice',
    '0 idle': 'the extra seconds are there for what follows the bsd_init burst of about 60 s',
    '0 longest': 'the longest run so far was boot 26\'s 61.6 s, whose film showed no restart of the Surface to its end, 92 s (OBSERVED once)',
    '0 silence': 'through a silence of about 28.5 s (INFERRED: the second of two 30 s waits for an RTC resource that is never published) into bsd_init',
}
PINS_0A = {
    'block 4': 'Rung 4 (record 67d814f6...42fa, boot 24; one unit, one boot; the operator\'s film, read by the coordinator): RUNG 4 PANIC TEXT.',
    'block 5': 'Rung 5 (record 64d87443...97f6, boot 25; one unit, one boot; the operator\'s film, read by the coordinator): RUNG 5 WATCHDOG, NO TEXT.',
    'block 5 answered': 'What boot 26 answered of that stop (OBSERVED once, one unit, one boot; section 0a): the guest\'s timer interrupts ARE delivered',
    'block 5 wait': 'boot 25\'s box of 60 s (TICKS give 60.18 s) ended inside that wait, the second of two 30 s waits for an RTC resource that is never published',
    'block 6': 'Rung 6 (record d9d79c87...5cda, boot 26; one unit, one boot; the operator\'s film, read by the coordinator): RUNG 6 NEW STOP.',
    'ticks': 'The call returned after TICKS=1182753011 at 19.2 MHz (61.60 s; the box was 2,304,000,000 ticks, 120 s',
    'stop': 'MON STOP S2_FAULT PC=0XFFFFFE00095FF9AC A=0X291000004 B=0X93880045 C=0XFFFFFE63AA8A0004, MON EXIT SPSR=0X80400204, MON ESR EC=0X24 ISV=1 SIZE=4 SRT=8 WNR=1 S1PTW=0 DFSC=0X5, '
            'MON WVAL X8=0X1 DATA=0X1',
    'page': 'INDEX=2 BYTES=118908480 SHA=1DD2E9AC94FDB741, INDEX=3 BYTES=1024 SHA=9804989A2A727740, INDEX=4 BYTES=528384 SHA=2AF25E8AC647A885; SNAPMON STAGE=PIN_OK LABEL=M1RUN-ACKPANIC-RUNG6 CHUNKS=46',
    'log': 'then NO new line for about 28.5 s, and at about 60.4 s a block of about 40 lines at once, which is bsd_init running',
    'pptp': 'a last line cut off after "request for", which is INFERRED (high) to be "Load request for com.apple.nke.pptp failed: <err>"',
    'memory': '(8,838 free pages of 16 KiB, about 138 MiB free: the kernel noticing the 384 MiB guest; "no victim found (action: 10)" and no panic path read on the host: a warning for a kernel-only run '
              'and a blocker later',
    'owner': 'PC= is in com.apple.driver.AppleM2ScalerCSCDriver (the fileset entry whose segment holds it; HAL file AppleM2ScalerCSCHalMSR.cpp), in the SoC-version step of the scaler\'s HAL init',
    'pulse': 'ONE pulse, 1 and then 0 written to word 1 of reg[0], when the hardware version that the device tree gives (22) is below 0X1A.',
    'no loop': 'It is not a loop (the function has no branch back; the first reading of this analysis as a loop is withdrawn).',
    'scaler0': 'IPA 0X291000004 lies in /device-tree/arm-io/scaler0 (compatible scaler,t8101, reg 0X291000000 size 0X38000',
    'iosurface': 'nine kexts (the scaler, AVD, AVE2, JPEG, ProRes, IOMFB, IOMFB-DCP, IOGPUFamily and the camera driver) wait for IOSurfaceRoot with no timeout, and IOSurfaceRoot\'s personality matches '
                 'IOResourceMatch=IOBSD, which bsd_init publishes at about 61 s',
    'released': 'The scaler, AVD, AVE2, JPEG, ProRes, IOMFB and IOMFB-DCP are released together (the order inside the release is UNKNOWN; the scaler first is OBSERVED).',
    'not stop': 'what the film showed is where the other 51 or so nodes with unmodelled windows did NOT stop in 61.6 s (OBSERVED), and the waiters of IOSurfaceRoot start only now.',
    'tim': 'MON TIM IRQS=1010 ASSERTS=2036 CLEARS=2036 VECTORS=969 CTL=0X1 CVAL=0X99C3A80F (OBSERVED once): the guest\'s timers work',
    'rtc': 'the silence before bsd_init was a kernel waiting: the second of two 30 s waits in bsd_init for the RTC resource (IOKitInitializeTime, then the calendar\'s getUTCTimeOfDay: 0.4 + 30 + 30 = 60.4 s',
    'no third': 'so both waits end by timeout and no third RTC wait follows.',
    'kprintf': 'The messages of those waits ("RTC did not show up") are kprintf lines, which go to the serial path, the monitor\'s ring, and not to the panel: the film cannot show them',
    'ack': 'MON ACK COUNT=15 (OBSERVED once): three stores for each of CPUs 1 to 5',
    'stick': 'The stick after boots 21, 23, 24, 25 and 26 was unchanged but for the Shell\'s marker',
    'noise 0a': 'The noise lines of boots 21, 23 and 25 were in it as before',
    'cval 0a': 'and CVAL lies about 9.6 ms ahead of the clock: an armed deadline and not a stalled one)',
    'scaler windows': 'other windows 0X291204000 and 0X291210000 (0X4000 each) and 0X290000000 (0X8000), and its DART, the node dart-scaler that stays in the quiet tree, at 0X291260000',
    'q9 rows': 'Q9, rows 2 (scaler0) and 3 (apr), "STOP or WAIT", INFERRED low to medium)',
    'registering': 'its "registering service" line (absent on the three frames read: OBSERVED once, and a film is not proof of absence), and the guest\'s device tree has no rtc node)',
}
PINS_3C = {
    'header': HEAD3C + ' (rung 7: D9 panel handed, serial=2, cpu_boot_timeout_secs=120, let_panic set, guest profile quiet, stock kernel',
    'label': 'Label: PIN.DAT\'s build_label is M1RUN-ACKPANIC-Q-r7-t.',
    'label mark': 'the mark Q- of the guest profile quiet (rung 6\'s label, M1RUN-ACKPANIC-RUNG6, had no mark)',
    'intro': 'This record is rung 7: its guest has the device tree of the profile quiet and its command line has cpu_boot_timeout_secs=120 where rung 6\'s has 90',
    'pair': 'rung 7 is built with rung 6\'s pair and with no other, and rung 5\'s record with rung 5\'s.',
    'differences': 'this one differs in 2 chunks, which the builder compares by SHA-256 with the table of rung 6\'s own bundle that it pins, and it refuses any other difference (PANEL_MISMATCH)',
    'handoff': 'with those two put back to rung 6\'s values its SHA-256 is rung 6\'s',
    'params': 'The other 44 chunks are rung 6\'s byte for byte, PARAMS among them: the box, the let_panic word, the 120 models and the programs\' places.',
    'word': 'The word is rung 5\'s 120 and not rung 6\'s 90: with 90 the panic would come about 92 s after the MON CALL line and cut a run',
    'margin': 'outside the box of 120 s by a margin of about 2 s, so that the box ends the run if nothing else does; the margin is thin and the record says so',
    'blocks': 'The wait blocks only the CPU-boot thread and the starts of AppleARMWatchdogTimer, AppleProcessorTrace and AppleT8140CLPC, and nothing on the path to the root device or to launchd',
    'reach': 'because the waiters of IOSurfaceRoot are released together when bsd_init publishes IOBSD at about 61 s (INFERRED); in this guest\'s tree neither node is present, the jpeg, AVE and display nodes are',
    'profile': 'THE DEVICE TREE OF THIS GUEST IS A PROFILE\'S, NOT THE BASE BOARD\'S: j700ap-26A428-m1-quiet, mark Q-',
    'withdrawn': 'WITHDRAWN: 43 nodes (395 -> 352, counted on the host).',
    'expect': 'What the run may end as. The prediction (INFERRED from the host analysis of boots 24 to 26: none of it has been seen with the quiet guest)',
    'restart': 'NOT PASSED for rung 7 (section 5d)',
    'result windows': 'AVD (MON STOP S2_FAULT with an IPA in 0x380000000..0x381404000 but for its DART\'s 0x381020000..0x381040000) is next',
}
PINS_5D = {
    'new': 'WHAT IS NEW beyond rung 6 (record d9d79c87...5cda, boot 26: section 0a).',
    'aim': 'THE AIM. Two questions, and one film answers both. (1) Does withdrawing the 43 nodes move the stop beyond the scaler?',
    'timer': 'the timer question is not asked again.',
    'expect': 'After the burst the lines to expect are the lines of the kext starter\'s load requests ("Load request for com.apple.nke.pptp failed", the line that boot 26 showed cut off) and, about 1 to 2 s '
              'after the bsd_init burst, "Waiting on <dict>" (IOLog, so visible: INFERRED from IOFindBSDRoot, public IOKitBSDInit-12377.cpp:896, read on the host), and then a SILENT loop of 60 s waits for '
              'the root device (no "Still waiting" line: it is compiled out; "!BSD" appears only on a 30 s timeout of the IOBSD wait, which is not expected)',
    'rtc film': 'The messages of the two 30 s waits for the RTC resource ("RTC did not show up" and the line with "getGMTTimeOfDay") are kprintf lines that go to the monitor\'s ring and not to the panel: '
                'the film cannot show them (INFERRED; this corrects rung 6\'s text).',
    'predicts': 'PREDICTION: the kernel goes as in boot 26 through its first 31 s of log',
    'jpeg': 'The first rows that remain are the four JPEG nodes (AppleJPEGHal::checkHardwareVersion: an S2_FAULT at their first window is possible, INFERRED low to medium; withdrawing them needs a new pin '
            'and is not in this record), then AVE and the display chain',
    'clean end': 'THE EXPECTED CLEAN END: MON STOP WATCHDOG at the end of the box (120 s after the call), with the last text on the panel "Waiting on <dict>", which appears about 62 s in (INFERRED); the first 60 s '
                 'timeout of the root device\'s loop is at about 122 s, just outside the box',
    'normal': 'A page with VECTORS above 0 and MON STOP WATCHDOG is the normal end.',
    'cpu margin': 'after the box by a margin of about 2 s (INFERRED; the wait blocks only the CPU-boot thread and three driver starts, nothing on the path to the root device): it is not expected inside the box, '
                  'and one there would mean that the margin was wrong (class RUNG 7 PANIC TEXT).',
    'falsifiers': 'FALSIFIERS: a MON STOP S2_FAULT with A= in the window of a node that the profile withdrew (THE PROFILE DID NOT TAKE: 0X380000000..0X381404000 for the video decoder but for its '
                  'DART\'s 0X381020000..0X381040000, 0X291000000..0X291038000, 0X291204000..0X291208000, 0X291210000..0X291214000 and 0X290000000..0X290008000 for the scaler, or another window',
    'film': 'THE FILM is rung 5\'s, from the Shell\'s countdown until 120 seconds after the SNAPREP report page has appeared, or, if it never appears, until the FORCE OFF of step 5 of section 5c (180 s after the '
            'MON CALL line) has been filmed',
    'stills': 'and a fourth of the whole report page',
    'last line': 'THE LAST LINE OF THE LOG AND THE TIME OF ITS LAST CHANGE are a result in every class that has no panic text',
    'decides': 'WHAT THE FILM AND THE PAGE DECIDE (what this record changes has not been seen; boots 21, 24, 25 and 26 are the comparison, OBSERVED once each).',
    'last 26': 'Boot 26\'s last line was cut off after "request for" (INFERRED: the kext starter\'s "Load request for com.apple.nke.pptp failed"), at the end of the bsd_init block, about 60.4 s after the MON CALL line',
    'contradict': 'A line "RTC did not show up", one with "getGMTTimeOfDay" or "!BSD" on the film would contradict the host reading that the first two are kprintf lines of the monitor\'s ring',
    'not failure': 'NOT THE FAILURE: the noise lines of section 0a',
    'pptp not failure': 'the cut-off last line of boot 26, "Load request for com.apple.nke.pptp failed: <err>" (INFERRED: the kernelcache has no pptp; harmless)',
    'memory not failure': '"memorystatus: System is unhealthy" with wired_mem_exhaustion and "no victim found (action: 10)" (the 384 MiB guest, 8,838 free pages of 16 KiB: a warning for a kernel-only run, no panic '
                          'path read on the host; a blocker later, when launchd needs memory: the A16 chain ran 8192 MiB, the minimum for launchd is UNKNOWN, 2 GiB is a guess)',
    'panic looks': 'with the word at 120 it is expected about 122 s in, outside this record\'s box by a margin of about 2 s; a text of that kind inside the box means that the margin was wrong',
    'class panic': 'RUNG 7 PANIC TEXT: the panel shows a panic text, whatever the page says and whatever the stop is',
    'class not a result': 'RUNG 7 NOT A RESULT: a film that does not let the coordinator read the panel for text or read MON STOP',
    'class a': 'RUNG 7 NOT PASSED, (a): no panic text and MON STOP S2_FAULT with A= in 0X300734004..0X30073400C again: the acknowledge did not take.',
    'class new': 'RUNG 7 NEW STOP: no panic text and a MON STOP of any kind but WATCHDOG',
    'did not take': 'THE PROFILE DID NOT TAKE: A= lies in a window of a node that the profile withdrew (the video decoder\'s 0X380000000..0X381404000 but not its DART\'s 0X381020000..0X381040000, which '
                    'stays; the scaler\'s 0X291000000..0X291038000, 0X291204000..0X291208000, 0X291210000..0X291214000 and 0X290000000..0X290008000, but not its DART\'s 0X291260000..0X291280000, which '
                    'stays; the ProRes engine\'s 0X294000000..0X294008000 and 0X295000000..0X295004000; the audio roots\' 0X313200000..0X313234000, 0X313300000..0X313334000 and '
                    '0X313800000..0X313834000; the coordinator reads these as the reg of the 43 nodes in the base board\'s DeviceTree.j700ap through the ranges of arm-io, and takes no other window for '
                    'this reading: the video decoder\'s 0X308078000 and the audio roots\' 0X3082A8000 are shared with nodes that stay)',
    'timers inferred': '(timers are delivered: INFERRED from boot 26\'s VECTORS=969, OBSERVED once)',
    'noise boots': 'the PMU and RTKit lines OBSERVED once in boots 21, 23, 25 and 26',
    'moved': 'THE STOP MOVED: an S2_FAULT with A= anywhere else, in the window of a device that was not withdrawn',
    'class waiting': 'RUNG 7 KERNEL WAITING: MON STOP WATCHDOG at the end of the 120 s box and no panic text. THE RESULT IS THE LAST LINE OF THE LOG AND ITS TIME ON THE FILM\'S CLOCK',
    'vectors 0': 'A page with VECTORS 0 would contradict boot 26 and is itself a finding to report',
    'class b': 'RUNG 7 NOT PASSED, (b): a restart of the Surface before the report, no report',
}
PINS_5B = {
    'tim': 'Boot 26 printed them: IRQS=1010 ASSERTS=2036 CLEARS=2036 VECTORS=969 CTL=0X1 CVAL=0X99C3A80F in 61.60 s (OBSERVED once), so the guest\'s timer interrupts are delivered and VECTORS above 0 is '
           'the normal page of a kernel that is waiting',
    'cval': 'places the armed deadline in time (INFERRED: boot 26\'s lay about 9.6 ms ahead)',
}
PINS_5C = {
    'step4 monitor': 'The monitor\'s and the reporter\'s digits are rung 6\'s (boot 26 had INDEX=0 ... SHA=BB34B9701D89302F and INDEX=1 ... SHA=533D004C27A11DA9: the same',
    'step4 label': 'boot 26\'s page had LABEL=M1RUN-ACKPANIC-RUNG6 CHUNKS=',
    'step4 digests': 'INDEX=2 ... SHA=1DD2E9AC94FDB741 and INDEX=3 ... SHA=9804989A2A727740, the SHA of its PIN.DAT',
    'step4 differs': 'rung 6\'s bundle has the same programs and the same chunk count and differs in the GUEST.DAT size and digits, the label and the PIN.DAT digits',
    'film until': 'or, if it never appears, until the FORCE OFF of step 5 (180 s after the MON CALL line) has been filmed',
}
ALL = {'section0': PINS, 'section0a': PINS_0A, 'section3c': PINS_3C, 'section5d': PINS_5D, 'section5b': PINS_5B, 'section5c': PINS_5C}


def pins_of(name):
    return {'section0': {k: v for k, v in PINS.items() if k.startswith('0 ')}}.get(name, ALL[name])


class Pins(unittest.TestCase):
    def test_each_sentence_of_sections_0_0a_3c_5b_5c_and_5d_is_pinned_and_a_record_without_it_is_rejected(self):
        parts = sections(STATE['r7'])
        for name, pins in ALL.items():
            with self.subTest(name):
                part = parts[name]
                if name == 'section0':
                    part = part + ' ' + parts['section0b']
                mutants_rejected(self, part, pins)

    def test_a_generator_without_a_hook_makes_a_record_that_the_pins_reject(self):
        key = pn.kind_key({'panel': 'console', 'boot_arg': M7.F, 'let_panic': True, 'panel_facts': STATE['r7-panel']})
        for hook in ('stand', 'why', 'decides', 'hazard', 'step4', 'page'):
            with self.subTest(hook):
                words = dict(m1g.PANEL[key])
                words.pop(hook)
                with mock.patch.dict(m1g.PANEL, {key: words}):
                    code, text, err = M7.record7('mutant-hook')
                self.assertEqual(code, 0, err)
                self.assertNotEqual(text, STATE['r7'])
                problems = [n for p in ALL.values() for n in pin_problems(text, p)]
                self.assertTrue(problems, f'a record without the hook {hook} is rejected by a pin')


F = M7.F


class Keyed(unittest.TestCase):
    def test_the_blocks_of_boots_24_25_and_26_are_held_to_the_records_rules(self):
        self.assertEqual(r7.VERDICTS, ('PANIC TEXT', 'WATCHDOG, NO TEXT', 'NEW STOP'))
        self.assertEqual([b['verdict'] for b in r7.BLOCKS.values()], ['PANIC TEXT', 'WATCHDOG, NO TEXT', 'NEW STOP'])
        for what, block in r7.BLOCKS.items():
            lines = r7.keyed_lines(block, what)
            self.assertTrue(all(len(line) <= 120 for line in lines))
        bad = {'a verdict of another record': dict(r7.RUNG6, verdict='TIMER PANIC'), 'a paragraph without a label': dict(r7.RUNG6, words=('The kernel did what it did.',)),
               'a paragraph that is not ASCII': dict(r7.RUNG6, words=('OBSERVED \u00e9',)), 'the word that M1 records never use': dict(r7.RUNG6, words=('OBSERVED and ' + 'VERI' + 'FIED',))}
        for what, block in bad.items():
            with self.subTest(what), self.assertRaises(AssertionError):
                r7.keyed_lines(block, 'Rung 6')

    def test_boot_26s_numbers_are_the_operators_film_and_the_page_digits_are_the_approved_staging(self):
        self.assertEqual((r7.BOOT26_TICKS, r7.BOOT26_S), (1_182_753_011, '61.60'))
        self.assertEqual({k: hex(v) for k, v in r7.BOOT26_STOP.items()}, {'pc': '0xfffffe00095ff9ac', 'a': '0x291000004', 'b': '0x93880045', 'c': '0xfffffe63aa8a0004'})
        self.assertEqual((r7.BOOT26_MONITOR, r7.BOOT26_REPORTER), (M7.m1.ACK_MONITOR_SHA256[:16].upper(), M7.m1.ACK_REPORTER6_SHA256[:16].upper()))
        self.assertEqual(r7.BOOT26_GUEST, M7.r7b.RUNG6_GUEST[:16].upper())
        self.assertEqual(r7.BOOT26_RECORD[:8], M7.r7b.RUNG6_RECORD[:8])
        self.assertEqual(r7.BOOT26_RECORD[-4:], M7.r7b.RUNG6_RECORD[-4:])

    def test_the_stale_sentences_of_rung_6s_record_and_the_withdrawn_readings_are_in_no_part_of_rung_7s(self):
        f = flat(STATE['r7'])
        stale = ('TIMER PANIC', 'the code then loops', 'writes WZR to the same offset', 'the shape of a 30 s IOKit wait that timed out', 'whether any guest timer interrupt was delivered in those 60 s is UNKNOWN',
                 '9.75 ms', 'boots 21 to 25', 'boots 21 to 26', '0X380001000..0X381404000', '0x380001000..0x381404000', 'DART at 0X291260000)', 'scaler0 and apr: stop or wait',
                 'the cut-off "Load request for com.apple.nke.pptp failed" of the kext starter', '"registering service" line, OBSERVED,', 'ARE delivered: OBSERVED once',
                 'this record is the cheapest run', 'nested_panic_max', 'cpu_boot_timeout_secs=90 and a box of 120 s, record d9d79c87...5cda, boot 26 (section 0a); its approval is spent; this record is rung 7: its '
                 'command line has cpu_boot_timeout_secs=90', 'about 92 s after the MON CALL line, inside the box', 'After about 60 s the log may show the lines of the kernel\'s other timed waits')
        self.assertEqual([s for s in stale if s in f], [])
        self.assertEqual(f.count('"RTC did not show up"'), 3 + f.count('"RTC did not show up" or'), 'the RTC messages are named where the text says that the film cannot show them')
        for needle in ('"RTC did not show up"',):
            for where in re.finditer(re.escape(needle), f):
                context = f[max(0, where.start() - 160):where.end() + 220]
                self.assertTrue(re.search(r'kprintf|film cannot show|would contradict', context), context)


class Classes(unittest.TestCase):
    def test_the_classes_of_section_5d_are_in_order_exclusive_and_leave_no_film_without_one(self):
        d = sections(STATE['r7'])['section5d']
        names = re.findall(r'(RUNG 7 (?:PANIC TEXT|NOT A RESULT|NOT PASSED, \((?:a|b)\)|NEW STOP|KERNEL WAITING))', d)
        first = [n for i, n in enumerate(names) if n not in names[:i]]
        self.assertEqual(first, ['RUNG 7 PANIC TEXT', 'RUNG 7 NOT A RESULT', 'RUNG 7 NOT PASSED, (a)', 'RUNG 7 NEW STOP', 'RUNG 7 KERNEL WAITING', 'RUNG 7 NOT PASSED, (b)'], 'first-fit order')
        for kind in ('S2_FAULT', 'UNNAMED', 'PANIC_ENTRY', 'SERROR', 'DEVICE_RESET', 'GUEST_EXC'):
            self.assertIn(kind, d.split('RUNG 7 NEW STOP')[1].split('RUNG 7 KERNEL WAITING')[0])
        self.assertIn('no panic text and a MON STOP of any kind but WATCHDOG', d)
        self.assertIn('MON STOP WATCHDOG at the end of the 120 s box and no panic text', d)
        self.assertIn('or any other end that none of the results above names', d)
        self.assertNotIn('RUNG 6', d.replace('RUNG 6\'S', ''), 'no class carries rung 6\'s name')

    def test_every_class_that_reads_a_stop_names_the_progress_and_the_falsifier_that_the_prediction_gives(self):
        d = flat(sections(STATE['r7'])['section5d'])
        for phrase in ('THE PROFILE DID NOT TAKE', 'THE STOP MOVED', 'progress', 'KERNEL WAITING', 'THE EXPECTED CLEAN END', 'FALSIFIERS', 'the four JPEG nodes'):
            self.assertIn(phrase, d)


class Anchors(unittest.TestCase):
    def test_a_change_of_rung_6s_words_is_an_assertion_error_not_silent_text(self):
        with self.assertRaises(AssertionError):
            r7.patched('rung 6 words that changed', ('not in the text', 'x'))
        with self.assertRaises(AssertionError):
            r7.patched('twice twice', ('twice', 'x'))
        self.assertEqual(r7.patched('one old text', ('old', 'new')), 'one new text')

    def test_the_guide_key_the_module_and_the_hooks_of_the_record(self):
        key = f'console+{F}+let_panic+{M7.Q}'
        panel = STATE['r7-panel']
        self.assertEqual(pn.kind_key({'panel': 'console', 'boot_arg': F, 'let_panic': True, 'panel_facts': panel}), key)
        self.assertEqual(pn.kind_key({'panel': 'console', 'boot_arg': F, 'let_panic': True}), f'console+{F}+let_panic', 'rung 5\'s key is what it was')
        self.assertIn(key, m1g.PANEL)
        self.assertEqual([pn._rung(panel), pn._rung({k: v for k, v in panel.items() if k != 'profile'}), pn._rung({**panel, 'boot_arg': M7.W90, 'profile': None})], [r7, r5, r6])
        self.assertEqual((r7.NUMBER, pn.ACK), (7, (r5, r6, r7)))
        self.assertIn('counts as a result of rung 7.', flat(STATE['r7']))
        new = {'stand', 'why', 'decides', 'hazard', 'rung', 'step4', 'page', 'idle', 'gic_idle', 'box_decision', 'd8_decision', 'decided', 'fault_seen', 'stop_seen', 'console', 'ran'}
        self.assertTrue(new <= set(m1g.PANEL[key]))
        self.assertEqual(sorted(k for k in m1g.PANEL if 'let_panic' in k), sorted([f'console+{F}+let_panic', f'console+{M7.W90}+let_panic', 'console+cpus=1+let_panic', key]))

    def test_the_older_records_say_nothing_of_rung_7_the_quiet_profile_or_the_scaler_decode(self):
        for name in ('r5-60', 'r6-120'):
            f = flat(STATE[name])
            for needle in ('rung 7', 'RUNG 7', 'quiet', 'Q-RUNG', 'AppleM2ScalerCSCDriver', 'IOSurfaceRoot'):
                with self.subTest(name=name, needle=needle):
                    self.assertNotIn(needle, f)


class Cites(unittest.TestCase):
    def test_every_source_line_it_cites_has_its_fragment_and_every_citation_is_in_a_table(self):
        pattern = r'((?:probes|shadow|compat|tools)/[A-Za-z0-9_./+-]+\.(?:c|h|py)):(\d+)(?:-(\d+))?'
        text = ' '.join(sections(STATE['r7']).values())
        cited = {(p, int(a), int(b or a)) for p, a, b in re.findall(pattern, text)}
        mine = {(p, a, b) for p, a, b, _ in r7.CITES}
        known = {(p, a, b) for p, a, b, _ in (*pn.CITES, *rp.CITES, *r5.CITES)}
        self.assertLessEqual(cited - mine, known, 'cited, and no table holds the fragments of it')
        self.assertEqual(mine - cited, set(), 'every entry of the table is cited')
        for path, first, last, fragments in r7.CITES:
            held = '\n'.join((ROOT / path).read_text().splitlines()[first - 1:last])
            for fragment in fragments:
                with self.subTest(f'{path}:{first}-{last} {fragment}'):
                    self.assertIn(fragment, held)
        for path, first, last in sorted(cited - mine):
            shift = r5.CITE_DRIFT.get((path, first), 0)
            with self.subTest(f'{path}:{first}-{last}'):
                tables = [t for t in (*pn.CITES, *rp.CITES, *r5.CITES) if t[:3] == (path, first, last)]
                self.assertTrue(tables, 'held by a table')
                held = '\n'.join((ROOT / path).read_text().splitlines()[first - 1 + shift:last + shift])
                self.assertTrue(all(fragment in held for fragment in tables[0][3]), f'{path}:{first}-{last}')
        self.assertFalse([old for old in ('244-245', '338-360', '247-271') if f'{r5.REPORTER}:{old}' in text], 'the lines of the reporter of the older records')


class Hygiene(unittest.TestCase):
    def test_nothing_is_called_verified_and_the_record_and_the_modules_are_ascii_and_within_the_line_width(self):
        f = STATE['r7']
        self.assertTrue(f.isascii() and M7.pp.NEVER not in f)
        self.assertIn('0a. Where things stand (boots 20, 21, 23 to 26; 2026-11-18/19; one unit, one boot each; OBSERVED unless INFERRED)', f)
        for rel in ('tools/sp11_stick_bundle_panel_rung7.py', 'tools/sp11_stick_bundle_panel_rung7d.py', 'tools/sp11_stick_bundle_rung7.py', 'tools/test-sp11-stick-monitor-rung7.py',
                    'tools/test-sp11-stick-monitor-rung7-words.py', 'tools/sp11_stick_bundle_panel_rungs.py'):
            text = (ROOT / rel).read_text()
            with self.subTest(rel):
                self.assertTrue(text.isascii())
                self.assertNotIn(M7.pp.NEVER, text)
        self.assertTrue(all(len(line) <= 120 for line in f.split('0. What approving')[1].split('1. What may run')[0].splitlines() + f.split(FIVE_D)[1].splitlines()))
        self.assertEqual(M7.pp.record_problems(f, M7.Q, r7.kind_facts(STATE['r7-panel'])), [], 'no sentence says that a profile guest differs in one word only')
        self.assertLessEqual(len(f.encode()), M7.HELD_LIMIT)

    def test_each_paragraph_of_the_words_says_what_it_is(self):
        panel = STATE['r7-panel']
        for paragraph in (*r7.extra_paragraphs(panel), r7.word_text(), r7.expect_text(panel['run']), ' '.join(r7.stand_extra({'panel_facts': panel})), ' '.join(r7.why_lines({'panel_facts': panel})),
                          *r7d.results_paragraphs(panel['run'], 180), r7d.new_text(panel), r7d.aim_text(panel), r7d.predicts_text(panel), r7d.decides_text()):
            with self.subTest(paragraph[:40]):
                self.assertTrue(re.search(r'OBSERVED|INFERRED|UNKNOWN|THE RESULTS|RUNG 7|WHAT IS NEW|THE AIM|A PROFILE\'S', paragraph), 'each paragraph is labelled or is a result')
        for paragraph in (r7.ACK_REACH, *r7d.LEGEND):
            self.assertTrue(re.search(r'OBSERVED|INFERRED|UNKNOWN|raw|legend', paragraph) or paragraph.startswith('- MON'), paragraph[:40])


if __name__ == '__main__':
    unittest.main()
