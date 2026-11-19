#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The words of rung 6 of the monitor entry run (tools/sp11_stick_bundle_panel_rung6.py and ..._rung6d.py; docs/SP11-STICK-MONITOR.md section 20, K14): a phrase pin and a deletion mutant for each
sentence of the record, every time-dependent sentence follows the box, no sentence says 60 s for the 120 s record, the keyed block of rung 5's result is held to the record's rules, the classes of
section 5d leave no page of the guest timer's words without a class and the legend of the reporter's lines is in section 5b. The mechanism (the closed list of two forms, the pair of each, the label,
the guest, the seven kinds against each other, the size of the files and K14) is tools/test-sp11-stick-monitor-rung6.py, whose fixtures run here. The sentences that the operator's choice of D5'' and the
review of the first text changed are tools/test-sp11-stick-monitor-rung6-edits.py's.

    python3 tools/test-sp11-stick-monitor-rung6-words.py

Boot 25's numbers are the coordinator's reading of the operator's film (OBSERVED, one unit, one boot); what the monitor does with the guest's timer is read in its source and is INFERRED. Nothing here
runs the unit: Hardware NOT RUN."""
import importlib.util
import itertools
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))

spec = importlib.util.spec_from_file_location('sp11_monitor_rung6_mechanism', TOOLS / 'test-sp11-stick-monitor-rung6.py')
M6 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M6)
STATE, flat, m1t, m1g, pn, rp, r2, r3, r5, r6, r6d, m1, ch, BLD = M6.STATE, M6.flat, M6.m1t, M6.m1g, M6.pn, M6.rp, M6.r2, M6.r3, M6.r5, M6.r6, M6.r6d, M6.m1, M6.ch, M6.BLD
W90, F, P, record6 = M6.W90, M6.F, M6.P, M6.record6
FIVE_D = '5d. Rung 6: what is new'
FIVE_B = '5b. The page, line by line'


def setUpModule():
    M6.setUpModule()


def tearDownModule():
    M6.tearDownModule()


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
    head3c = '3c. The panel as the kernel\'s console with the CPU-start stores acknowledged, let_panic and a wait of 90 s'
    return dict(section0=f.split('0. What approving this text decides (read this first)')[1].split('0a. Where things stand')[0],
                section0a=f.split('0a. Where things stand')[1].split('0b. The hazards, in plain words')[0],
                section0b=f.split('0b. The hazards, in plain words')[1].split('1. What may run')[0],
                section3c=head3c + f.split(head3c)[1].split('4. Time box')[0], section5b=f.split(FIVE_B)[1].split('5c. The operator\'s actions')[0], section5d=f.split(FIVE_D)[1])


PINS_0 = {
    'decides d5': 'D5 and D8 decided 5 s and D5\' decided 60 s;',
    'decides word': '(2) A guest command line with cpu_boot_timeout_secs=90 where rung 5\'s had 120, so that XNU\'s own wait for the CPU that never comes ends inside the box if the guest\'s timers '
                    'are delivered; the guest is otherwise rung 5\'s, six CPUs included.',
    'decides reporter': '(3) A reporter that prints the guest timer\'s words and the monitor\'s other counters (section 5b): it reads the report window that the monitor already writes, and no byte of '
                        'the monitor changes.',
    'decides reach': 'What the guest can reach is what rung 5\'s could, the existing stage-2 map and the monitor\'s models and report window, with no new register, no new system-register class and no '
                     'new store class (read on the host in compat/cpu-start-ack.h, a file and not a run; the census of section 3 is the plain monitor\'s but for the addresses of six data words), and this '
                     'approval is for that reach for up to the box.',
    'decides run': 'The acknowledging monitor, the panel as the console and the monitor\'s let_panic word ran in boot 25 for 60 s; this word and this box have not.',
    'why': 'WHY RUNG 6. Rung 5 (boot 25, record 64d87443...97f6) ran its whole box of 60 s and ended as MON STOP WATCHDOG with no panic text, and its log went further than in any earlier run',
    'why unknown': 'Whether any guest timer interrupt was delivered in those 60 s is UNKNOWN: that run\'s reporter printed no timer word.',
    'why analysis': 'The host analysis of the monitor\'s code says that the guest\'s timer is the monitor\'s emulation, which the quarantine does not touch, and finds no reason for it not to work (INFERRED; '
                    'section 0a); boot 25 neither shows nor refutes it.',
    'why reporter': '(3) THE REPORTER IS ANOTHER ONE: its page prints the guest timer\'s words (MON TIM, MON CLK) and the monitor\'s other counters (section 5b), so that a silent run still says '
                    'whether the timer was armed, delivered or lost.',
    'why rest': 'The monitor, the acknowledge, let_panic, the panel as the console, the six CPUs and the rest of the guest are rung 5\'s. THE PREDICTION, ITS FALSIFIERS AND WHAT THE FILM DECIDES are in '
                'section 5d, in the operator\'s words (INFERRED).',
    'idle hook': 'the log keeps the first 512 events whatever the box (section 5b), the extra seconds are there for the kernel\'s own wait of 90 s, which ends inside the box only if the guest\'s timer '
                 'interrupts are delivered (section 5d), and whether the Surface restarts inside a box of {box} s is UNKNOWN (section 4).'}
PINS_0A = {
    'boot 21': 'Boot 21 (rung 2, record 1ad122b3...fc3b; one unit, one boot; the operator\'s film) ran the panel as the kernel\'s console: the call returned after 2.044 s as MON STOP S2_FAULT '
               'PC=0XFFFFFE00097A352C A=0X300734004 B=0X93970045, INFERRED to be the kernel\'s first CPU-start write (compat/boot-relocate.h:147-153; section 3c).',
    'boot 23': 'Boot 23 (rung 3, record 749361cf...a1a7) added cpus=1, which was honoured (OBSERVED once), and the call ended 2.166 s in as MON STOP GUEST_EXC PC=0XFFFFFE000B8C2288 A=0X2000000',
    'rung 4': 'Rung 4 (record 67d814f6...42fa, boot 24; one unit, one boot; the operator\'s film, read by the coordinator): RUNG 4 PANIC TEXT.',
    'rung 4 text': 'panic(cpu 0 caller 0xfffffe00097ddef0): "ApplePMGR: virtual void ApplePMGR::enableCPUComplex(UInt32, bool):4861 REQUIRE failed: complex < _cpuComplexCount" @ApplePMGR.cpp:4861',
    'rung 4 stop': 'MON STOP S2_FAULT PC=0XFFFFFE0008BA7358 A=0X3082BC224 B=0X938A0005 C=0XFFFFFE612A564224, on a clean report (OBSERVED once): a 32-bit READ at a translation fault at level 1 at '
                   'stage 2',
    'rung 4 cause': 'With cpus=1 XNU has one cluster and the PMGR driver\'s start enables two complexes, so the second asserts; boot 21 and boot 25, with six CPUs and two clusters, passed this driver',
    'rung 5': 'Rung 5 (record 64d87443...97f6, boot 25; one unit, one boot; the operator\'s film, read by the coordinator): RUNG 5 WATCHDOG, NO TEXT.',
    'rung 5 call': 'TICKS=1155520917 at 19.2 MHz (60.18 s; above the 1,152,000,000 of a 60 s box, and the stop was the monitor\'s own MON STOP WATCHDOG, so row e held by its own rule)',
    'rung 5 counts': 'MON COUNTS EVENTS=512 TRAPS=168214 ABORTS=0 STEPS=1441 CALLS=66179 DROPPED=54212, then MON STOP WATCHDOG PC=0XFFFFFE000B8716A8 A=0X9600004F B=0XFFFFFECFFBFDC000 (C= lost its '
                     'last two digits to the runner\'s clip at 100 columns) and MON EXIT SPSR=0X604003C4, with GIC RESTORE CLEAN=YES, on a clean report (OBSERVED once;',
    'rung 5 panel': 'No panic text was on the panel (OBSERVED once: no frame of the film shows it).',
    'rung 5 log': 'about 35 s of drivers (the SPMI and PMU lines with their failed register reads, "AppleARMRTC started!", "AppleDialogSPMIPMURTC started!", then the USB lines "failed to find '
                  'phyService" and "failed to find AppleUSBHostPort", about 28 to 35 s after the MON CALL line), and then the log was silent for the last 25 s of the box',
    'rung 5 falsifiers': 'Rung 5\'s falsifiers did not occur (OBSERVED once): no ApplePMGR assertion, no stop at 0X300734004..0X30073400C, no "cpu 1 failed to boot" text.',
    'rung 5 avd': 'the stop at the video decoder\'s window (IPA 0X380001000) that the host analysis named as the likeliest early one did not come.',
    'rung 5 decode': 'PC= is the instruction after a WFI in XNU\'s idle path, reached only through the scheduler\'s idle loop, and SPSR 0X604003C4 is EL1 with D, A, I and F masked, so CPU 0 was idle '
                     'with every interrupt masked at the end of the box and every other thread was blocked.',
    'rung 5 waits': 'The kernel\'s next timed waits after its last line are IOKit\'s 30 s wait for the RTC resource, the calendar\'s 30 s wait for the same resource, the root device\'s 30 s wait for '
                    'the BSD resource and then a silent wait for the root device, and the CPU-boot thread\'s own wait of cpu_boot_timeout_secs, whose panic would come at about 2.3 s plus the word, '
                    '122 s with 120 (INFERRED; read on the host).',
    'rung 5 reading': 'What the stop says, INFERRED: the kernel went idle and nothing woke it in the last 25 s. A timer interrupt could have; whether any guest timer interrupt was delivered in the 60 s '
                      'is UNKNOWN, because that run\'s reporter printed no timer word',
    'rung 5 timer': '(probes/xnuboot/clock_timer.c:195-236, probes/xnuboot/async.c:95-105, probes/xnuboot/stage2.c:401-408), and an idle CPU 0 wakes only at its guest deadline or at the end'}
PINS_3C = {
    'header': '3c. The panel as the kernel\'s console with the CPU-start stores acknowledged, let_panic and a wait of 90 s (rung 6: D9 panel handed, serial=2, cpu_boot_timeout_secs=90, let_panic set, '
              'stock kernel; docs/SP11-STICK-MONITOR.md section 20)',
    'in short': 'In short. This record is rung 6, after boot 25 (rung 5), whose 60 s box ended as MON STOP WATCHDOG with no panic text and the kernel idle (section 0a): the monitor, the acknowledge, '
                'let_panic, the panel and the six CPUs are rung 5\'s; the command line has cpu_boot_timeout_secs=90, the box is {box} s (D5\'\') and the reporter prints the guest timer\'s words.',
    'intro': 'Rung 5 is the run before this one: the same panel as the console with the CPU-start stores acknowledged, record 64d87443...97f6, boot 25 (section 0a); its approval is spent. This record '
             'is rung 6: its command line has cpu_boot_timeout_secs=90 where rung 5\'s has 120, its box is {box} s where rung 5\'s was 60 s (D5\'\') and its reporter prints the guest timer\'s words.',
    'programs': 'The programs: SNAPMON-M1-ACK.BIN has the SHA-256 {monitor}..., rung 5\'s: the wrapper with the acknowledging monitor (053a239f..., 608 bytes longer than the plain one, ad4ebaa2...), '
                'built with the box cap at 120 s in place of 5 s (SP11_STICK_MONITOR_BOX_MAX_S, section 4).',
    'pair': 'The builder holds each acknowledging record to its pair by digest (M1_REPORT_MISMATCH for another pair), so that this record cannot be built with rung 5\'s reporter, nor rung 5\'s with '
            'this one. SNAPMON.BIN is rung 5\'s, byte for byte.',
    'other differences': 'From rung 5\'s guest this one differs in four bytes of data and in nothing else: three in the command line in boot_args (cpu_boot_timeout_secs=90 where rung 5\'s has 120:',
    'console': 'Console. What the operator saw in boots 21, 24 and 25 (OBSERVED once each) and expects to see again (INFERRED):',
    'word': 'The extra word. The command line is the loader\'s with serial=2 and, after it, cpu_boot_timeout_secs=90 ({line}): one word, from the builder\'s list (--boot-arg: cpus=1 with --cpu-start stop '
            'and cpu_boot_timeout_secs=120 or cpu_boot_timeout_secs=90 with --cpu-start ack, BOOT_ARG_REFUSED for any other word or pair)',
    'word wait': 'That wait ends only by a timer interrupt: with 120, as in rung 5, it could not end inside a box of 60 s whatever the timers did; with 90 it ends about 92 s in, inside the box of '
                 '120 s, if a guest timer interrupt is delivered (INFERRED), and that is the point of this record.',
    'params word': 'The builder sets the word for the records of rungs 4, 5 and 6 only (--let-panic: LET_PANIC_REFUSED for any other combination)',
    'ack match': 'The monitor takes such a store only when it is a valid-syndrome 32-bit store at exactly one of the three addresses, as a stage-2 translation fault, with no secondary CPU configured; '
                 'any other access keeps the stop',
    'ack reach': 'What the acknowledge lets the guest reach. A guest that is not stopped at the CPU-start write runs on past a point that no run had passed before boot 25, for up to the box: boot 25 '
                 'ran 60 s past it (OBSERVED once) and this record runs up to 120 s.',
    'ack device': 'Which device the kernel touches first after the acknowledged stores is no longer a question of the first seconds: the host analysis expected the video decoder\'s window at IPA '
                  '0X380001000 to stop the run early, and boot 25 did not stop there (OBSERVED once)',
    'page can': 'What the page can and cannot show. Besides the first five events of the monitor\'s log, the counts and the stop line (boot 25\'s lost two digits of C= to the runner\'s clip at 100 '
                'columns; this reporter prints C= on a line of its own when it would pass that column) and the first lines of the console ring as MON CON lines, the reporter of this record prints the',
    'ack count': 'a prediction that this record\'s page can confirm or refute: its MON ACK line prints the count and the first eight stores; 10 to 15 stores in all',
    'ack printed': 'this record\'s SNAPREP prints it (MON ACK, section 5b), and no result of this record rests on the count.',
    'ends': 'What the run may end as. The prediction (INFERRED from the host analysis of boots 24 and 25: none of it has been seen with this word), its falsifiers and the other ends are in section 5d',
    'label': 'Label: PIN.DAT\'s build_label is M1RUN-ACKPANIC-{label}. The builder puts ACKPANIC- after M1RUN- for this record, in place of CONSOLE-'}
PINS_5D = {
    'expect': 'In boot 25 the log stopped growing about 35 s after the MON CALL line and stood still for the rest of its box: that is expected again and is not a stop; only the report page (about 93 to '
              '{box} s) or, if it does not come, the {wait} s rule of section 5c ends the wait.',
    'expect waits': 'Between about 60 s and the panic the log may show lines of the kernel\'s other timed waits ("RTC did not show up", a line with "getGMTTimeOfDay" and "call failed with", "!BSD" and '
                    '"Waiting on" and a matching dictionary: INFERRED from the host analysis of the kernelcache (a file and not a run), the kernel\'s 30 s waits for the RTC resource and the BSD '
                    'resource, which come after boot 25\'s last line): they are NOT A FAILURE and not a result of their own.',
    'expect force off': 'Whatever the guest paints, if the SNAPREP page is not on the screen {wait} seconds after the MON CALL line, FORCE OFF',
    'predicts': 'WHAT THE RECORD PREDICTS, AND WHAT FALSIFIES IT (INFERRED; no probabilities; none of it has been seen with this word;',
    'predicts panic': 'XNU\'s CPU-boot thread ends its wait for CPU 1 about 92 s after the MON CALL line (the wait starts about 2.3 s in) and the kernel panics with "cpu 1 failed to boot for the '
                      'first time, waited 90 seconds"; nested panics and boot 24\'s end (MON STOP S2_FAULT A=0X3082BC224 B=0X938A0005 at a read in the chip-reset path) follow within about a second',
    'predicts proof': 'That text is the proof that a guest timer interrupt reaches the kernel, and the only one that the film can give.',
    'falsifiers': 'FALSIFIERS: silence to the end of the box with no panic text (the reading that the guest\'s timers are delivered, or the start of the wait at about 2.3 s, is wrong: the classes of '
                  'RUNG 6 WATCHDOG, NO TEXT say which, from the page); a panic text "cpu 1 failed to boot ... waited N seconds" with N other than 90 (the word was not honoured); the ApplePMGR '
                  'assertion again (the topology reading of rung 5 was wrong; it held in boot 25); a MON STOP S2_FAULT with A= in 0X300734004..0X30073400C',
    'falsifiers early': 'A stop at a device that has no model, or a panic of another driver, before the 92 s mark ends the run before the question is asked: progress, and a new question.',
    'film stills': 'and a fourth of the whole report page (it has up to 40 lines now and the guest timer\'s words decide the result of a silent run: asked, not required, and valuable)',
    'page': 'WHAT THE PAGE SHOWS. The first five events and the last stop, as before (the monitor keeps at most the first 512 events: section 5b), and, new in this record, the guest timer\'s words and',
    'page ack': 'MON ACK is how many acknowledged stores the monitor took and the first eight (section 3c has the prediction, INFERRED), and no result rests on it.',
    'decides': 'WHAT THE FILM AND THE PAGE DECIDE (what this record changes has not been seen; boots 21, 24 and 25 are the comparison, OBSERVED once each).',
    'decides disagree': 'and the page says VECTORS 0, that disagreement is itself a result to report (UNKNOWN which of the two is wrong).',
    'panic': 'The panic of the CPU that never comes reads "cpu 1 failed to boot for the first time, waited N seconds" (INFERRED): with the word at 90 it is the TIMER PANIC below and is expected about '
             '92 s in; with 120 it could not come inside rung 5\'s box.',
    'results': 'THE RESULTS. M1\'s own verdict is section 5a\'s and is reported beside rung 6\'s. The panel is read first, then the stop, and for a stop at the end of the box the page\'s MON TIM line;',
    'timer panic': 'RUNG 6 TIMER PANIC: the panel shows the panic text "cpu 1 failed to boot for the first time, waited 90 seconds" (the number is the word\'s), about 92 s after the MON CALL line by the '
                   'film\'s clock (a few seconds either way), whatever the page says and whatever the stop is',
    'panic other': 'RUNG 6 PANIC TEXT, OTHER: the panel shows another panic text, whatever the page says and whatever the stop is.',
    'panic other falsifier': 'or "cpu 1 failed to boot for the first time, waited N seconds" with N other than 90 (the word was not honoured).',
    'not a result': 'RUNG 6 NOT A RESULT: a film that does not let the coordinator read the panel for text or read MON STOP',
    'not passed a': 'RUNG 6 NOT PASSED, (a): no panic text and MON STOP S2_FAULT with A= in 0X300734004..0X30073400C again: the acknowledge did not take.',
    'watchdog': 'RUNG 6 WATCHDOG, NO TEXT: MON STOP WATCHDOG at the end of the {box} s box and no panic text. The timer question is read from the page\'s MON TIM line (section 5b), in this order, '
                'the first that fits:',
    'watchdog a': '(a) LINE RAISED, NOT TAKEN, ASSERTS above CLEARS and VECTORS below ASSERTS: the monitor raised the guest\'s timer line more often than the guest entered its FIQ vector, and the '
                  'line was still raised at the stop (INFERRED from the monitor\'s counters; the timer\'s own state is then CTL=0X5).',
    'watchdog a wfi': 'Whether a WFI wakes on a pending interrupt that is masked, on this CPU, is UNKNOWN: this class decides the next change of the monitor.',
    'watchdog b': '(b) TIMER DELIVERED, VECTORS above 0: the guest took at least one timer interrupt (INFERRED from the monitor\'s counters), so the timers work: the kernel waits for something '
                  'else, or its wait of 90 s began later than predicted.',
    'watchdog c': '(c) LOST TICK, VECTORS 0 and CTL=0X5 with CVAL more than a second (24 million ticks) behind 1.25 times LAST: a deadline was armed, passed long before the report and is '
                  'not masked, and no tick raised the line for it (IRQS 0 is the clean form).',
    'watchdog d': '(d) NOT DUE, VECTORS 0 and CTL=0X1, or CTL=0X5 with CVAL within a second of 1.25 times LAST: a deadline is armed and had not been served at the report; CVAL says where '
                  'it lies.',
    'new stop': 'RUNG 6 NEW STOP: no panic text and a MON STOP of another kind (S2_FAULT, UNNAMED, PANIC_ENTRY, SERROR, DEVICE_RESET, GUEST_EXC or another name) or an S2_FAULT with another A=:',
    'not passed b': 'RUNG 6 NOT PASSED, (b): a restart of the Surface before the report, no report'}
PINS_HOOKS = {
    'ran': 'under a monitor that ran once on this machine in its acknowledging build, for 60 s (boot 25: section 0a), and in its plain build in boots 20, 21, 23 and 24.',
    'stop seen': 'WATCHDOG: the time box ended the run: the stop timer fired at the deadline and the monitor took the guest back.',
    'stop seen words': 'OBSERVED twice (boots 20 and 25, one unit, one boot each).',
    'gic idle': 'The quarantine switches off the physical timer interrupts and not the timer that XNU uses, which is the monitor\'s emulation of it (INFERRED: section 0a), so the seconds of a box '
                'beyond the first 60 may add the kernel\'s own timed waits (section 5d).',
    'whole': 'Only the counts (MON COUNTS), the monitor\'s window words that the reporter of this record prints (MON TIM, MON CNT, MON EP) and the end of the run (MON STOP, MON EXIT) reflect the whole '
             'box.',
    'hazard restart': 'the longest run so far was boot 25\'s 60 s, which showed no restart of the Surface to the end of its film, 90 s (OBSERVED once), and a run of {box} s is UNTESTED.',
    'step 4': 'The monitor\'s digits are rung 5\'s (boot 25 had SHA=BB34B9701D89302F: the same) and the reporter\'s are not: a page whose INDEX=1 line says SHA=EE86676001D89CF6 has the old reporter, '
              'which prints none of the lines of section 5b that this record is for.',
    'step 4 boot 25': 'boot 25\'s page had LABEL=M1RUN-ACKPANIC-RUNG5 CHUNKS=',
    'step 4 digits': 'INDEX=2 ... SHA=896F56E29DFC5E49 and INDEX=3 ... SHA=BCE154588BB1E833, the SHA of its PIN.DAT:',
    'heading': '0a. Where things stand (boots 20, 21, 23, 24 and 25; 2026-11-18/19; one unit, one boot each; OBSERVED unless INFERRED)',
    'rung': 'store class needs its own record, and so does each of rungs 1 to 5, the first run with the panel withheld, the panel as the console (rung 2, boot 21), the same with cpus=1 (rung 3, boot 23), '
            'with cpus=1 and let_panic (rung 4, boot 24) and with the CPU-start stores acknowledged, six CPUs and a 60 s box (rung 5, boot 25), whose records have their own approvals, spent: this '
            'record is rung 6, the same as rung 5 but for a wait of 90 s in the command line, a box of 120 s and another reporter, and no approval covers another\'s boot.',
    'console 5a': 'its console is the panel (boots 21, 24 and 25: the kernel\'s log and panic text were on it, OBSERVED once each)',
    'silence tail': 'The wait is 120 s after the STAGE=DONE line for this record (the film of the kernel\'s text: step 6 of section 5c): a lit page that has stopped there, with or without the SNAPREP '
                    'page, is FORCE OFF then (section 5c); without the SNAPREP page it is no result (section 5a).',
    'reading pointer': 'what counts as a result of rung 6. Section 3c is the evidence for it, which the coordinator reads.',
    'film step': 'The film runs about {film} s for this box (the runner\'s loading, the call of {box} s, the stop and 120 s after the report page: an estimate, not a measurement)',
    'd8 sums': 'plus the monitor\'s prepare, {box} s of it by the guest (D8, section 4).',
    'force off': 'one CPU stays loaded, at worst, until the operator acts: {wait} s after the MON CALL line, whatever the guest paints (section 5), plus about 20 s holding Power, {hold} s or more',
    'cite 491': 'MON STOP S2_FAULT with the address in A= (probes/xnuboot/trap_dispatch.c:491)'}
PINS_5B = {
    'stop c end': 'a window that took none prints no MON EXIT, MON GX or MON TIM.',
    'esr': '- MON ESR EC= ISV= SIZE= SRT= WNR= S1PTW= DFSC= (only after MON STOP S2_FAULT): the fields of the stop\'s syndrome, the B= of MON STOP: EC and DFSC in hex, the others in decimal, NA where the '
           'bits mean something else.',
    'esr size': 'SIZE is the access size in bytes (1, 2, 4 or 8; NA when ISV is clear), so that a store of a W register reads SIZE=4;',
    'wval': '- MON WVAL X<n>= DATA= (only for a stage-2 fault that is a store the instruction made): the guest register that SRT names (XZR for 31, which is zero) whole, and DATA= its low SIZE bytes '
            '(INFERRED: the data the access carried).',
    'gx': '- MON GX0-3 to MON GX28-SP (eight lines, only with a capture): x0 to x30 and the selected SP, four to a line in 0X hex; the last line is x28, x29, x30, SP (INFERRED: the guest\'s at the '
          'instruction that MON EXIT PC= names).',
    'clk': '- MON CLK READS= AFTER= FIRST= LAST=: READS is the guest clock reads that the monitor answered; FIRST is the host counter at the first sample that the clock backend took for any caller; LAST is '
           'the host counter at the report writer\'s own last sample, taken when the report was written;',
    'clk after': 'AFTER is LAST minus the wrapper\'s counter before the call, the ticks (those of MON RET TICKS=) from the call\'s start to the report (NA when there was no sample or it lies outside the '
                 'call). LAST and AFTER are the monitor\'s report-time sample: they are NOT the guest\'s last use of the clock, and a small AFTER does not say that the backend was idle.',
    'tim irqs': 'IRQS is the monitor\'s own EL2 timer interrupts (INTID 26) taken, whatever deadline they served (alone they say nothing about a guest deadline); ASSERTS and CLEARS are the times '
                'HCR_EL2.VF was set and cleared for the guest; VECTORS is the entries into the guest\'s FIQ vector with VF set;',
    'tim reading': 'Every field is a counter or a register that the monitor kept, and no unit has printed one yet; what they mean is INFERRED from the monitor\'s sources (section 5d reads them), and the '
                   'page cannot say how long the guest sat in WFI.',
    'ack legend': '- MON ACK COUNT=<n> <offset>=<0Xvalue> ... [MORE=<m>] (only from the acknowledging monitor): COUNT is every 32-bit store that the guest made to PMGR\'s CPU-start block that the monitor took '
                  'and ignored (no CPU was started); then the first eight as OFFSET=VALUE (offsets 4, 8 and 12 are the global, the E-cluster and the P-cluster masks; VALUE is the 32 bits written); MORE is the '
                  'stores not listed.',
    'cnt': '- MON CNT MAPS= MAPFAIL= DISP= PANIC= UNDEF= MAPOUT= LOGDROP= and, on the next line, CONDROP= RNGDROP= MAPDEV= MMIORD= MMIOWR= UARTTX=: the monitor\'s counters',
    'ep': '- MON EP TOTAL= OTHER= and <endpoint>:<calls> for every bucket that is not zero, ascending: the SPTM calls by endpoint number',
    'order': '- MON EXIT PC= SPSR= ESR= FAR=: the guest\'s own state at an interrupt stop, where it was.'}


def values(box):
    low, high = (round(x, 1) for x in (box + 2.4 + 8 + 0.04, box + 2.9 + 8 + 3.1))
    return dict(box=box, wait=box + 60, film=r5.film_total_s(box), line=r6.LINE, monitor=M6.AUTH.sha(STATE['fake']['image'])[:16], label=M6.LABEL6, low=low, high=high, hold=box + 60 + 20)


def formatted(pins, box):
    return {name: phrase.format(**values(box)) for name, phrase in pins.items()}


ALLOWED_60 = ('D5\' decided ', 'ran in boot 25 for ', 'ran its whole box of ', 'delivered in those ', 'Yes, up to ', 'above the 1,152,000,000 of a ', 'delivered in the ', 'needs more than ',
              'acknowledging build, for ', 'the longest run so far was boot 25\'s ', 'boot 14 from one ', '(rung 5), whose ', 'where rung 5\'s was ', 'inside a box of ', 'boot 25 ran ',
              'time box of 120 s plus ', 'six CPUs and a ', 'Between about ', 'beyond boot 25\'s ', 'be 120 s or ')      # the left of each 60 s that this record has: all of them history (boot 25, rung 5, D5', the question that the operator answered), none the box


class Words(unittest.TestCase):
    def test_each_sentence_of_sections_0_0a_3c_5b_and_5d_is_pinned_and_a_record_without_it_is_rejected(self):
        parts = sections(STATE['r6-120'])
        for name, pins in (('section0', PINS_0), ('section0a', PINS_0A), ('section3c', PINS_3C), ('section5b', PINS_5B), ('section5d', PINS_5D)):
            mutants_rejected(self, parts[name], formatted(pins, 120))

    def test_each_hook_sentence_is_pinned_and_a_record_without_it_is_rejected(self):
        pins, f = formatted(PINS_HOOKS, 120), flat(STATE['r6-120'])
        for name, phrase in pins.items():
            with self.subTest(pin=name):
                self.assertGreaterEqual(f.count(flat(phrase)), 1, 'in the text')
                self.assertEqual(pin_problems(f.replace(flat(phrase), 'DAMAGED'), {name: phrase}), [name])

    def test_every_time_dependent_sentence_follows_the_box(self):
        for box in (120, 30):
            key = f'r6-{box}'
            parts = sections(STATE[key])
            for name, pins in (('section0', PINS_0), ('section0a', PINS_0A), ('section3c', PINS_3C), ('section5b', PINS_5B), ('section5d', PINS_5D)):
                with self.subTest(box=box, section=name):
                    self.assertEqual(pin_problems(parts[name], formatted(pins, box)), [])
            self.assertEqual(pin_problems(STATE[key], formatted(PINS_HOOKS, box)), [])
        f30, f120 = flat(STATE['r6-30']), flat(STATE['r6-120'])
        self.assertIn('Count those 90 seconds from the moment the MON CALL line is on the screen', f30)
        self.assertIn('Count those 180 seconds from the moment the MON CALL line is on the screen', f120)
        self.assertIn('about 210 s in all for this box', f30)
        self.assertIn('about 300 s in all for this box', f120)
        for text, box in ((f30, 30), (f120, 120)):
            self.assertRegex(text, rf"one CPU is loaded for about \d+\.\d to \d+\.\d s plus the monitor's prepare, {box} s of it by the guest")
            self.assertRegex(text, rf'\(\d+\.\d \+ 8 \+ {box} \+ 0\.04\)')       # the sums of D8: the wrapper's copy and clean follow the guest's size, the box is the record's
        self.assertNotIn('180 seconds after the MON CALL line', f30)
        self.assertNotIn('90 seconds after the MON CALL line', f120)
        for text in (f30, f120):
            self.assertNotIn('the 5 s box', text.replace('the 5 s box was not used', ''), 'no sentence of its own calls the box 5 s')

    def test_no_sentence_says_60_s_for_the_record_whose_box_is_120_s(self):
        f = flat(STATE['r6-120'])
        found = [f[:m.start()] for m in re.finditer(r'\b60 ?(?:s\b|seconds)', f)]
        self.assertEqual(len(found), 21, 'the 60 s of this record: history, each of them one of ALLOWED_60')
        for left in found:
            with self.subTest(left[-40:]):
                self.assertTrue(any(left.endswith(allowed) for allowed in ALLOWED_60), f'a 60 s that is no history: {left[-50:]!r}')
        self.assertEqual(sorted(allowed for allowed in ALLOWED_60 if not any(left.endswith(allowed) for left in found)), [], 'every allowed left context is used')
        for needle in ('FORCE OFF 60', 'after 60 s', 'the 60 s rule', 'box of 60 s of this', 'time box of 60 s'):
            self.assertNotIn(needle, f)
        self.assertGreaterEqual(f.count('180 seconds after the MON CALL line'), 3, 'the wait of this box is 180 s wherever it is named')
        self.assertNotIn('120 seconds after the MON CALL line', f)

    def test_the_findings_of_the_reviews_of_rung_5_are_in_the_words_of_rung_6(self):
        f = flat(STATE['r6-120'])
        for bad in ('That is a fault of the monitor and the first thing to find', '(the acknowledge did not take: a fault of the monitor)', '60 seconds after the SNAPREP report page',
                    'until 60 seconds after the SNAPREP page', 'a fault of the monitor (INFERRED reasons', 'any lowercase letter on the panel is the kernel\'s (INFERRED from section 5b',
                    'the kernel may only idle without its timer interrupts (section 0b)', 'A kernel without those timer interrupts may only idle',
                    'the page does not print the count; 10 to 15 stores in all, the order per CPU not established) and the early stop', 'the AVD'):
            self.assertNotIn(bad, f)
        self.assertEqual(f.count('the coordinator decodes it before saying which'), 2, 'the falsifier and class (a) of section 5d: the syndrome is decoded before the monitor is blamed')
        self.assertEqual(f.count('until 120 seconds after the SNAPREP report page has appeared'), 2, 'step 2 of section 5c and section 5d')
        self.assertEqual(f.count('a valid-syndrome 32-bit store at exactly one of the three addresses, as a stage-2 translation fault, with no secondary CPU configured; any other access keeps the stop'), 1)
        self.assertEqual(f.count('contains "panic(cpu"'), 1)
        self.assertEqual(f.count('LAST is the host counter at the report writer\'s own last sample'), 1, 'review of the reporter, finding 1: LAST is no guest event')
        self.assertEqual(f.count('the guest\'s last use of the clock'), f.count('are NOT the guest\'s last use of the clock'), 'only the denial: LAST is no guest event')
        self.assertEqual(f.count('are NOT the guest\'s last use of the clock'), 1)
        for bad in ('the page does not print the count', 'UNKNOWN to the page and the film, and no result of this record rests on it', 'or the number of acknowledged stores'):
            self.assertNotIn(bad, f)
        self.assertNotIn('VERIFIED', f)

    def test_rungs_4_and_5_results_are_keyed_blocks_that_a_wrong_verdict_or_a_bad_paragraph_cannot_fill(self):
        text = flat(' '.join(r6.keyed_lines(r6.RUNG5, 'Rung 5')))
        self.assertIn('RUNG 5 WATCHDOG, NO TEXT.', text)
        self.assertIn('RUNG 4 PANIC TEXT.', flat(' '.join(r6.keyed_lines(r6.RUNG4, 'Rung 4'))))
        self.assertEqual(r6.VERDICTS, ('PANIC TEXT', 'WATCHDOG, NO TEXT'))
        for block, name in ((r6.RUNG4, 'Rung 4'), (r6.RUNG5, 'Rung 5')):
            for verdict in ('PASSED', 'NOT PASSED (a)', 'WATCHDOG NO TEXT', 'TIMER PANIC', None):
                with mock.patch.dict(block, verdict=verdict), self.assertRaises(AssertionError):
                    r6.keyed_lines(block, name)
            for what, words in {'non-ASCII': ('A paragraph with a \u00e9 (OBSERVED once).',), 'the word that M1 records never use': ('Read in the film, VERIFIED once (OBSERVED).',),
                                'no label': ('A paragraph that says what happened and does not say how it is known.',)}.items():
                with self.subTest(f'{name} {what}'), mock.patch.dict(block, words=words), self.assertRaises(AssertionError):
                    r6.keyed_lines(block, name)
        self.assertEqual((r6.BOOT25_TICKS, r6.BOOT25_S), (1_155_520_917, '60.18'))
        self.assertEqual(r6.BOOT25_STOP, dict(pc=0xFFFFFE000B8716A8, a=0x9600004F, b=0xFFFFFECFFBFDC000))
        esr = r6.BOOT25_STOP['a']
        self.assertEqual((esr >> 26, (esr >> 6) & 1, esr & 0x3F), (0x25, 1, 0x0F), 'A= of boot 25: EC 0x25 (a data abort at the same EL), WnR 1 (a write), DFSC 0xF (a permission fault at level 3)')
        self.assertEqual(r6.BOOT25_TICKS / 19.2e6 > 60, True, 'above the 60 s box, and the stop was the monitor\'s own')
        self.assertEqual((r6.CPU_WAIT_S, r6.PANIC_AT_S, r6.BOX_S), (90, 92, 120))

    def test_section_5d_follows_the_box_and_every_page_of_the_timer_words_has_a_class(self):
        for box in (120, 30):
            lines = r6.section_5d(STATE[f'r6-{box}-panel'], box + 60)
            self.assertLessEqual(len(lines), 175)
            self.assertTrue(all(len(line) <= 118 and line.isascii() for line in lines))
            self.assertEqual(len(STATE[f'r6-{box}'].split('\n5d. ')[1].splitlines()), len(lines))
        text = flat(' '.join(r6d.results_paragraphs(STATE['r6-120-panel']['run'], 180)))
        # the classes (a) to (f) of a MON STOP WATCHDOG page, first fit: (vectors, asserts, clears, ctl, cval behind 1.25 x LAST by more than a second)
        def klass(vectors, asserts, clears, ctl, late=False):
            if asserts > clears and vectors < asserts:
                return 'a'
            if vectors > 0:
                return 'b'
            if ctl == 0x5:
                return 'c' if late else 'd'
            if ctl == 0x1:
                return 'd'
            return 'e' if ctl in (0x0, 0x2, 0x3, 0x7) else 'f'
        for vectors, asserts, clears, ctl, late in itertools.product((0, 1, 5), (0, 1, 5), (0, 1, 5), range(0, 9), (False, True)):
            got = klass(vectors, asserts, clears, ctl, late)
            self.assertIn(got, 'abcdef', 'every page has a class')
        for letter in 'abcdef':
            self.assertIn(f'({letter}) ', text)
        self.assertEqual([klass(0, 0, 0, ctl) for ctl in (0, 1, 2, 3, 5, 7, 4, 6)], ['e', 'd', 'e', 'e', 'd', 'e', 'f', 'f'], 'CTL is 0, 1, 2, 3, 5 or 7 on a page: the monitor keeps bits 0 and 1 and computes bit 2')
        self.assertEqual(klass(0, 0, 0, 5, True), 'c')
        self.assertEqual((klass(0, 1, 0, 5), klass(1, 2, 1, 5), klass(2, 2, 2, 1), klass(3, 1, 0, 5)), ('a', 'a', 'b', 'b'), 'a line raised and not taken is (a) whether or not an earlier one was taken')
        for verdict in ('TIMER PANIC', 'PANIC TEXT, OTHER', 'NOT A RESULT', 'NOT PASSED, (a)', 'WATCHDOG, NO TEXT', 'NEW STOP', 'NOT PASSED, (b)'):
            self.assertEqual(text.count(f'RUNG 6 {verdict}'), 1, verdict)
        order = [text.index(f'RUNG 6 {v}') for v in ('TIMER PANIC', 'PANIC TEXT, OTHER', 'NOT A RESULT', 'NOT PASSED, (a)', 'WATCHDOG, NO TEXT', 'NEW STOP', 'NOT PASSED, (b)')]
        self.assertEqual(order, sorted(order), 'first fit: the classes are in the order that they are read')
        self.assertIn('sections 0, 0a, 0b, 4, 5, 5a, 5c and 5d', flat(STATE['r6-120']))

    def test_the_timer_words_of_the_page_are_the_names_that_the_monitor_and_the_reporter_use(self):
        text = flat(' '.join(r6d.page_legend(None, 120)))
        for name in ('IRQS', 'ASSERTS', 'CLEARS', 'VECTORS', 'CTL', 'CVAL', 'WRITES', 'IRQPC', 'READS'):
            self.assertIn(f'{name}=', text, name)
        report = (ROOT / 'probes/xnuboot/report.c').read_text()
        for field in ('timer.reads', 'timer.writes', 'timer_irqs', 'timer_asserts', 'timer_clears', 'timer_vectors'):
            self.assertIn(field, report, field)
        guide = flat(' '.join(m1g.page_lines(m1_facts(), 120, dict(panel='console', boot_arg=W90, let_panic=True))))
        self.assertIn('- MON TIM IRQS= ASSERTS= CLEARS= VECTORS= CTL= CVAL=', guide)
        self.assertLess(guide.index('- MON EXIT PC= SPSR= ESR= FAR='), guide.index('- MON TIM IRQS='), 'the legend follows MON EXIT, as the reporter prints it')
        self.assertLess(guide.index('- MON EP TOTAL='), guide.index('- MON EV n <kind>'))
        for key in ('console+cpu_boot_timeout_secs=120+let_panic', 'console+cpus=1+let_panic', 'console+cpus=1', 'console', 'handed'):
            self.assertNotIn('page', m1g.PANEL[key], 'the older records have no legend of the new lines')

    def test_a_generator_without_one_of_the_blocks_makes_a_record_that_the_pins_reject(self):
        pins = {**{f'0/{k}': v for k, v in formatted(PINS_0, 120).items()}, **{f'0a/{k}': v for k, v in formatted(PINS_0A, 120).items()}, **{f'3c/{k}': v for k, v in formatted(PINS_3C, 120).items()},
                **{f'5d/{k}': v for k, v in formatted(PINS_5D, 120).items()}, **{f'5b/{k}': v for k, v in formatted(PINS_5B, 120).items()},
                **{f'hooks/{k}': v for k, v in formatted(PINS_HOOKS, 120).items()}}
        keys = m1g.PANEL['console+cpu_boot_timeout_secs=90+let_panic']
        dropped = lambda key, value: mock.patch.dict(keys, {key: value})      # noqa: E731
        for what, (patch, pin) in {'no why': (dropped('why', lambda real: ()), '0/why'), 'no boot facts in 0a': (dropped('stand', lambda real: ()), '0a/rung 5'),
                                   'no hazard in 0b': (dropped('hazard', ''), 'hooks/hazard restart'), 'no decision': (dropped('decides', ''), '0/decides word'),
                                   'rung 5\'s count of runs': (dropped('ran', r5.RAN), 'hooks/ran'), 'no stop seen': (dropped('stop_seen', {}), 'hooks/stop seen words'),
                                   'no 5d': (mock.patch.object(r6, 'section_5d', lambda panel, wait: ()), '5d/expect'), 'rung 5\'s word text': (mock.patch.object(r6, 'word_text', r5.word_text), '3c/word'),
                                   'rung 5\'s reach': (mock.patch.object(r6, 'ACK_REACH', 'x'), '3c/ack reach'), 'rung 5\'s ends': (mock.patch.object(r6, 'expect_text', r5.expect_text), '3c/ends'),
                                   'no legend': (dropped('page', None), '5b/tim irqs'), 'no idle sentence': (dropped('idle', ''), '0/idle hook'), 'no gic sentence': (dropped('gic_idle', ''), 'hooks/gic idle'),
                                   'no log sentence': (dropped('whole', ''), 'hooks/whole'), 'no console note': (dropped('console', ''), 'hooks/console 5a'),
                                   'rung 5\'s step 4': (dropped('step4', r5.step4), 'hooks/step 4'), 'rung 5\'s rung': (dropped('rung', r5.RUNG), 'hooks/rung'),
                                   'rung 5\'s heading': (dropped('stand_head', r5.STAND_HEAD), 'hooks/heading'), 'rung 5\'s intro': (mock.patch.object(r6, 'intro', r5.intro), '3c/intro')}.items():
            with self.subTest(what), patch:
                code, text, err = record6('mutant-r6')
                self.assertEqual(code, 0, err)
                self.assertNotEqual(text, STATE['r6-120'])
                self.assertIn(pin, pin_problems(text, pins), what)

    def test_every_source_line_it_cites_has_its_fragment_and_every_citation_is_in_a_table(self):
        pattern = r'((?:probes|shadow|compat|tools)/[A-Za-z0-9_./+-]+\.(?:c|h|py)):(\d+)(?:-(\d+))?'
        text = ' '.join(sections(STATE['r6-120']).values())
        cited = {(p, int(a), int(b or a)) for p, a, b in re.findall(pattern, text)}
        mine = {(p, a, b) for p, a, b, _ in r6.CITES}
        known = {(p, a, b) for p, a, b, _ in (*pn.CITES, *rp.CITES, *r5.CITES)}
        self.assertLessEqual(cited - mine, known, 'cited, and no table holds the fragments of it')
        self.assertEqual(mine - cited, set(), 'every entry of the table is cited')
        for path, first, last, fragments in r6.CITES:
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
        reporter = r5.REPORTER
        self.assertEqual({(a, b) for p, a, b in cited if p == reporter}, {(256, 257), (281, 305)}, 'this record cites where its reporter has the console size and the stop names')
        self.assertEqual({k[1:]: v[:2] for k, v in r5.REPORTER_DRIFT.items()}, {(244, 245): (256, 257), (338, 360): (390, 433), (247, 271): (281, 305)}, 'the approved texts keep their lines')
        self.assertFalse([old for old in ('244-245', '338-360', '247-271') if f'{reporter}:{old}' in text], 'the lines of the reporter of the older records')

    def test_nothing_is_called_verified_and_each_paragraph_says_what_it_is(self):
        f = STATE['r6-120']
        self.assertTrue(f.isascii() and 'VERIFIED' not in f)
        for rel in ('tools/sp11_stick_bundle_panel_rung6.py', 'tools/sp11_stick_bundle_panel_rung6d.py', 'tools/test-sp11-stick-monitor-rung6.py', 'tools/test-sp11-stick-monitor-rung6-words.py'):
            self.assertTrue((ROOT / rel).read_text().isascii(), rel)
        self.assertNotIn('VERIFIED', (ROOT / 'tools/sp11_stick_bundle_panel_rung6d.py').read_text())
        panel = STATE['r6-120-panel']
        for paragraph in (*r6.extra_paragraphs(panel), r6.word_text(), r6.expect_text(panel['run']), ' '.join(r6.stand_extra({})), ' '.join(r6.why_lines({})), *r6d.results_paragraphs(panel['run'], 180)):
            with self.subTest(paragraph[:40]):
                self.assertTrue(re.search(r'OBSERVED|INFERRED|UNKNOWN|THE RESULTS|RUNG 6', paragraph), 'each paragraph is labelled or is a result')
        for paragraph in (r6.ACK_REACH, r6.PAGE_CAN, *r6d.LEGEND):
            self.assertTrue(re.search(r'OBSERVED|INFERRED|UNKNOWN|raw|legend', paragraph) or paragraph.startswith('- MON'), paragraph[:40])
        self.assertTrue(all(len(line) <= 120 for line in f.split('0. What approving')[1].split('1. What may run')[0].splitlines() + f.split(FIVE_D)[1].splitlines()))


def m1_facts():
    """The M1 facts of the record of rung 6 as the guide reads them: the report of the stand-in pair, read by the builder's own reader."""
    fx = M6.AUTH.STATE['fake']
    return m1.read_report(M6.AUTH.json.dumps(M6.M5.ack_report()).encode(), fx['image'], fx['reporter'], dict(M6.AUTH.EFI), 'ack')


if __name__ == '__main__':
    unittest.main()
