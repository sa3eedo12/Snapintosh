#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The words of rung 8 of the monitor entry run (tools/sp11_stick_bundle_panel_rung8.py and ..._rung8d.py; docs/SP11-STICK-MONITOR.md section 20, K16; docs/SP11-M1-GUEST-PROFILES.md section 7):
a phrase pin and a deletion mutant for each sentence that rung 8 says again or changes, in sections 0, 0a, 0b, 3c, 4, 5c and 5d; the keyed block of boot 27 is held to the record's rules (the
film as the coordinator read it, OBSERVED once; the host reading of its clock lines, INFERRED); the classes of section 5d are in order, exclusive and leave no film without one, and the new
class separates a clock that took from one that did not; the window of the clock model is the one that the builder read from PARAMS; the stale sentences of rung 7's record (the quiet guest's
aim, its two changes, `UNTESTED` for a box of 120 s, `that anything runs with these nodes withdrawn`) are in no part of it and the older records say nothing of rung 8. The mechanism (the closed
list of four forms, the pair, the label, the comparison, the nine kinds against each other, the sizes and K16) is tools/test-sp11-stick-monitor-rung8.py, whose fixtures run here.

    python3 tools/test-sp11-stick-monitor-rung8-words.py

Boot 27's numbers are the coordinator's reading of the operator's film (OBSERVED, one unit, one boot); what the clock does to the kernel's boot is read on the host in the kernelcache, the
loader and the monitor's ELF (a file and not a run) and is INFERRED. Nothing here runs the unit: Hardware NOT RUN."""
import importlib.util
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
sys.path.insert(0, str(TOOLS))

spec = importlib.util.spec_from_file_location('sp11_monitor_rung8_mechanism', TOOLS / 'test-sp11-stick-monitor-rung8.py')
M8 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M8)
STATE, ch, m1, pn, M5, M7, flat = M8.STATE, M8.ch, M8.m1, M8.pn, M8.M5, M8.M7, M8.M6.flat
rp, r2, r3, r5, r6, r7, r8, r8b = M5.rp, M5.r2, M5.r3, M5.r5, M8.M6.r6, M7.r7, M8.r8, M8.r8b
import sp11_stick_bundle_panel_rung8d as r8d  # noqa: E402
import sp11_stick_bundle_panel_rung7d as r7d  # noqa: E402
import sp11_stick_bundle_record_m1_guide as m1g  # noqa: E402
import sp11_stick_bundle_panel_profile as pp  # noqa: E402

FIVE_D = '5d. Rung 8: the aim'
FIVE_B = '5b. The page, line by line'
HEAD3C = '3c. The panel as the kernel\'s console with the CPU-start stores acknowledged, let_panic, a wait of 120 s and a quiet guest with the PMU\'s clock bound'
CLOCK_WINDOW = '0X308700000..0X308718000'


def setUpModule():
    M8.setUpModule()


def tearDownModule():
    M8.tearDownModule()


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
                section3c=HEAD3C + f.split(HEAD3C)[1].split('4. Time box')[0], section4=f.split('4. Time box')[1].split('5. Refusal and stop rules')[0],
                section5a=f.split('5a. What M1 is, and when it has passed or failed')[1].split('5b. The page, line by line')[0],
                section5b=f.split('5b. The page, line by line')[1].split('5c. The operator\'s actions')[0],
                section5c=f.split('5c. The operator\'s actions')[1].split(FIVE_D)[0], section5d=f.split(FIVE_D)[1])


PINS_0 = {
    '0 d5': 'rungs 6 and 7 ran inside it (61.6 s and 120.185 s of the 120, the whole box: section 0a)',
    '0 confirms': 'and this record asks the same box, so that approving this text confirms it for this record',
    '0 two': '(2) A GUEST THAT IS RUNG 7\'S, BYTE FOR BYTE, BUT FOR THREE WORDS IN ONE DEVICE MODEL OF ITS PARAMS CHUNK',
    '0 clock': 'The three words bind the PMU\'s real-time clock at nub-spmi0, the SPMI controller of the PMU, in place of the empty bus that rungs 6 and 7 gave it',
    '0 placeholder': 'a placeholder instant, 2026-11-20T00:00:00Z, plus the run\'s own time: not the unit\'s time, and what the kernel does with a wrong date is UNKNOWN',
    '0 reach': '(3) WHAT THE GUEST CAN REACH is rung 7\'s',
    '0 earlier': 'everything that bsd_init releases starts about 50 s earlier than in boot 27, in which it ran for the last 60 s of the box without a stop (OBSERVED once)',
    '0 why': 'WHY RUNG 8. Rung 7 (boot 27, record e42f451d...3b01) ran its whole box of 120 s (120.185 s)',
    '0 thousand': 'fails its parity check four times in a row (about a thousand lines in all, with the other failing reads)',
    '0 one thing': 'THIS RECORD CHANGES ONE THING FROM RUNG 7, IN GUEST.DAT ONLY: THE PMU\'S REAL-TIME CLOCK IS BOUND',
    '0 seed': '(the kind, 9 to 48, the seed and the contract word of the clock: 1,795,132,800,000,000 microseconds, 2026-11-20T00:00:00Z)',
    '0 unchanged': 'THE MONITOR, ITS PAIR, THE REPORTER, THE let_panic WORD, THE COMMAND LINE, THE DEVICE TREE AND THE BOX ARE RUNG 7\'S',
    '0 aim': 'THE AIM, THE PREDICTION, ITS FALSIFIERS AND WHAT THE FILM DECIDES are in section 5d',
    '0 idle': 'the extra seconds are there for what follows bsd_init, which the clock is expected to bring to within about 10 s of the MON CALL line (INFERRED)',
    '0 decided': 'Decided by the operator on 2026-11-19 (the answer "let\'s go with 120 seconds", quoted from the app, to the question whether the box of rung 6\'s record should be 120 s or 60 s):',
}
PINS_0B = {
    '0b ran': 'under a monitor that ran in its acknowledging build three times, for 60 s (boot 25), for 61.6 s (boot 26) and for 120.2 s (boot 27: section 0a)',
    '0b silence': 'in boot 27 the kernel went on past them for the whole box, through a silence of about 29 s (INFERRED: the second of two 30 s waits for an RTC resource that is never published) into bsd_init '
                  'and to a wait for a root device (OBSERVED once)',
    '0b clock': 'what it does with the clock bound, which should publish that resource, is UNKNOWN (it may wait, stop at a device that has no model, panic or spin)',
    '0b longest': 'the longest run so far was boot 27\'s 120.185 s, whose film showed no restart of the Surface to its end, 152 s (OBSERVED once): a run of 120 s has been seen once, and not with the clock bound',
    '0b restart': 'whether the Surface restarts was seen once, in boot 27: none, to the end of its film, 152 s (OBSERVED once); with the clock bound the kernel\'s course differs from that run\'s (UNKNOWN)',
    '0b consoles': '(OBSERVED once: boots 21, 24, 25, 26 and 27)',
}
PINS_4 = {
    '4 control': 'That control ran no guest; the stock kernel ran inside a box of 120 s once, in boot 27, without a restart to the end of its film (OBSERVED once). The clock changes what the kernel does in '
                 'that time (UNKNOWN), and the argument above does not reach it.',
}
PINS_0A = {
    'block 7': 'Rung 7 (record e42f451d...3b01, boot 27; one unit, one boot; the operator\'s film, read by the coordinator): RUNG 7 KERNEL WAITING.',
    'ticks': 'The call ran its whole box: TICKS=2307553890 at 19.2 MHz (120.185 s; the box was 2,304,000,000 ticks), MON COUNTS EVENTS=512 TRAPS=195787 ABORTS=0 STEPS=2007 CALLS=65009 DROPPED=129122',
    'stop': 'MON STOP WATCHDOG PC=0XFFFFFE000B8716A8 A=0X96000047 B=0XFFFFFE110E00C040 and MON STOP C=0XFFFFFE000BA87EA0, MON EXIT SPSR=0X604003C4, with REGS RESTORED=YES and GIC RESTORE CLEAN=YES',
    'no panic': 'No panic text was anywhere in the film (the word is only in the label and in PANIC=0), and no "RTC did not show up", "getGMTTimeOfDay" or "!BSD" line.',
    'profile took': 'no S2_FAULT of any address came: the quiet profile took (OBSERVED once)',
    'last line': 'ending in the last line of the log, `Waiting on <dict ID="0"><key>IOProviderClass</key><string ID="1">IOMedia</string><key>Content</key><string ID="2">Apple_HFS</string></dict>`',
    'no scroll': 'The panel did not scroll for the next 59 s (the first and last lines of the screen were the same in every frame)',
    'root device': 'The kernel was waiting for a root device, an IOMedia of content Apple_HFS (OBSERVED once; that nothing can answer it in this guest, which has no storage, is INFERRED).',
    'clock lines': 'The clock driver\'s own start lines ARE on the film (OBSERVED once): about 7 s in, `AppleDialogSPMIPMU::start: Primary PMU detected`, `AppleARMRTC started!`',
    'parity': 'failed four times (retry 3 to 0) with `parity error: parity_rcv=0x0000 parity_exp=0x000F` and `[0..3] = 0x00 parity=BAD`',
    'families': 'a second family (address 8800, count 5, parity_exp 0x001F) and a read of 56 bytes failed in the same way, about 1,000 lines in all.',
    'registering': '`AppleARMRTC registering service!` is on no frame that the OCR read (a film is not proof of absence).',
    'kprintf': 'The messages of the two 30 s waits that rung 7\'s record expected only on the monitor\'s ring were not on the film (OBSERVED once).',
    'waits': 'the two 30 s waits of bsd_init for that resource, 0.4 + 30 + 30 s, end at the burst at about 60 s (INFERRED timing, from code read on the host)',
    'wfi': 'the PC is the instruction after a WFI (SPSR 0X604003C4: EL1 with D, A, I and F masked, as in boot 25), so the kernel was idle at the box\'s end',
    'tim': 'MON TIM IRQS=1560 ASSERTS=3134 CLEARS=3134 VECTORS=1519 CTL=0X1 CVAL=0XEDFD7657, so the guest\'s timers are delivered, 12.6 FIQ vectors a second',
    'timeout': 'The root device\'s silent loop of 60 s waits would have its first timeout at about 120 s, at the box\'s end, and the CPU-boot panic at about 122 s did not come inside the box (INFERRED, as designed).',
    'block 6': 'Rung 6 (record d9d79c87...5cda, boot 26; one unit, one boot; the operator\'s film, read by the coordinator): RUNG 6 NEW STOP.',
    'ticks 6': 'The call returned after TICKS=1182753011 at 19.2 MHz (61.60 s; the box was 2,304,000,000 ticks)',
    'scaler': 'a 32-bit write of 1 at offset 4 of the first window of /device-tree/arm-io/scaler0 (reg 0X291000000, size 0X38000)',
    'rtc waits': 'the second of two 30 s waits in bsd_init for the RTC resource (IOKitInitializeTime, then the calendar\'s getUTCTimeOfDay: 0.4 + 30 + 30 = 60.4 s; INFERRED timing from code read on the host)',
    'block 5': 'Rung 5 (record 64d87443...97f6, boot 25; one unit, one boot; the operator\'s film, read by the coordinator): RUNG 5 WATCHDOG, NO TEXT.',
    'wfi 5': 'PC= is the instruction after a WFI in XNU\'s idle path and SPSR 0X604003C4 is EL1 with D, A, I and F masked, so CPU 0 was idle with every interrupt masked at the end of the box',
    'block 4': 'Rung 4 (record 67d814f6...42fa, boot 24; one unit, one boot; the operator\'s film, read by the coordinator): RUNG 4 PANIC TEXT.',
    'cause 4': 'with cpus=1 XNU has one cluster and the PMGR driver\'s start enables two complexes, so the second asserts; boots 21 and 25 to 27, with six CPUs and two clusters, passed this driver.',
    'stick': 'The stick after boots 21, 23, 24, 25, 26 and 27 was unchanged but for the Shell\'s marker',
    'stick 27': 'boots 26\'s and 27\'s by a read on the host of the stick as returned: their 18 staged files byte-identical and the Shell\'s marker the only new file)',
}
PINS_3C = {
    'header': HEAD3C + ' (rung 8: D9 panel handed, serial=2, cpu_boot_timeout_secs=120, let_panic set, guest profile quiet-rtc, stock kernel',
    'short': 'In short. This record is rung 8, after boot 27 (rung 7), which ran the whole box of 120 s through bsd_init to a wait for a root device, the first run to get that far (section 0a)',
    'intro': 'This record is rung 8: its guest is rung 7\'s with the PMU\'s real-time clock bound (the guest profile quiet-rtc: three words of one device model of PARAMS)',
    'programs': 'rung 8 is built with rung 6\'s pair, as rung 7 was, and with no other',
    'label': 'Label: PIN.DAT\'s build_label is M1RUN-ACKPANIC-R-r8-t.',
    'label mark': 'the mark R- of the guest profile quiet-rtc (rung 7\'s label, M1RUN-ACKPANIC-Q-RUNG7, had the mark Q-)',
    'other': 'this one differs in 1 chunk, which the builder compares by SHA-256 with the table of rung 7\'s own bundle that it pins, and it refuses any other difference (PANEL_MISMATCH): chunk 2, PARAMS '
             '(9,232 bytes), in three words of one of its 120 device models, the one at index 18 (offset 0X758 of the chunk, a window of 0X18000 bytes at 0X308700000), the SPMI controller of nub-spmi0',
    'words': 'the kind, 9 (the empty bus) in rung 7\'s and 48 (the PMU\'s clock) here; config[3], 256 (the queue depth) in rung 7\'s and 1,795,132,800,000,000 here, the seed of the clock, the instant '
             '2026-11-20T00:00:00Z; and config[4], 256 in rung 7\'s and 0XF715F704F802000E here, the clock model\'s contract word (with those three put back to rung 7\'s values the chunk\'s SHA-256 is rung 7\'s)',
    'same': 'The other 45 chunks are rung 7\'s byte for byte, HANDOFF (the boot_args page, with the command line and the length of the device tree) and DEVICE_TREE (327,664 bytes, 352 nodes) among them',
    'size': 'GUEST.DAT is as long as rung 7\'s (',
    'word text': 'cut a run that had not shown where the guest ends (boot 27 did: a wait for a root device);',
    'cpu boot': 'boots 25, 26 and 27 went on on the boot CPU (OBSERVED once each)',
    'reach': 'The controller of the PMU, whose model is the one thing that changes, was already in the guest\'s reach as an empty bus (0X308700000 and the next 0X18000 bytes of IPA)',
    'ack reach': 'boot 25 ran 60 s past it, boot 26 61.6 s and boot 27 120.2 s (OBSERVED once each)',
    'params': 'for the records of rungs 4 to 8 only',
    'params used': 'used on the Surface Pro 11 in boots 24 to 27 (OBSERVED once each),',
    'stops watchdog': 'MON STOP WATCHDOG (OBSERVED: boots 20, 25 and 27)',
    'lead': 'THE DEVICE TREE OF THIS GUEST IS A PROFILE\'S, NOT THE BASE BOARD\'S: j700ap-26A428-m1-quiet-rtc, mark R-, composed with rung 7\'s form (rung 8)',
    'withdrawn': 'WITHDRAWN: 43 nodes (395 -> 352, counted on the host).',
    'the clock': 'THE CLOCK (INFERRED; read on the host in the kernelcache, the loader and the monitor\'s ELF, a file and not a run',
    'parity rule': 'a read of n bytes passes when bits 31:16 of the first word of the controller\'s response, under a mask, equal (1 << n) - 1, and the data words are not looked at, so zero data passes',
    'refused': 'the alarm registers, the PMU\'s SOCD header at 8800, the scratch pad and every other slave are refused, with the empty bus\'s reply and a failure bit in the controller\'s flags',
    'seed': 'The seed is a placeholder, 2026-11-20T00:00:00Z, not the unit\'s time: a stick has no host clock, and a build that read one would not regenerate.',
    'tell': 'TELLS IT APART (INFERRED; no profile bundle but rung 7\'s has been booted): PIN label M1RUN-ACKPANIC-R-r8-t',
    'not claimed': 'that the withdrawn nodes are enough for a run longer than boot 27\'s 120.2 s (EXPERIMENTAL; the withdrawal ran once); that the clock changes what the kernel waits for or stops at '
                   '(INFERRED: section 5d); that the placeholder calendar is harmless (UNKNOWN)',
    'expect': 'from the host analysis of boots 24 to 27 and of the PMU\'s clock: none of it has been seen with the clock bound',
}
PINS_5A = {
    '5a console': 'its device tree is the profile quiet\'s, its PARAMS bind the PMU\'s clock, its let_panic word is set and the monitor acknowledges its CPU-start stores',
    '5a console 27': 'its console is the panel (boots 21, 24, 25, 26 and 27: the kernel\'s log and panic text were on it, OBSERVED once each)',
    '5a clock': 'what the acknowledge, let_panic and the withdrawn nodes do with the clock bound is INFERRED and has not been seen (boot 27 saw them for 120.2 s without it)',
    '5a exit': 'MON EXIT PC= beside it (OBSERVED: boots 20, 25 and 27, one unit, one boot each)',
    '5a all of it': 'used all of it (OBSERVED: boots 20, 25 and 27)',
}
PINS_5B = {
    '5b watchdog': 'OBSERVED three times (boots 20, 25 and 27, one unit, one boot each).',
}
PINS_5C = {
    'step4 monitor': 'The monitor\'s and the reporter\'s digits are rung 6\'s and rung 7\'s (boot 27 had INDEX=0 ... SHA=BB34B9701D89302F and INDEX=1 ... SHA=533D004C27A11DA9: the same',
    'step4 label': 'boot 27\'s page had LABEL=M1RUN-ACKPANIC-Q-RUNG7 CHUNKS=46: rung 7\'s bundle has the same programs, the same chunk count and the same GUEST.DAT size and differs in the GUEST.DAT digits, the '
                   'label and the PIN.DAT digits',
    'step4 guest': '(GUEST.DAT; rung 7\'s, which boot 27 ran, is the same size with SHA=25F74CB8CEDB5E20, read on the host from its bundle)',
    'rung': 'with a wait of 120 s and the guest profile quiet (rung 7, boot 27), whose records have their own approvals, spent: this record is rung 8, rung 7 with the guest profile quiet-rtc, and no approval '
            'covers another\'s boot',
}
PINS_5D = {
    'new': 'ONE THING CHANGES, IN GUEST.DAT ONLY: THE PMU\'S REAL-TIME CLOCK IS BOUND at nub-spmi0 (the guest profile quiet-rtc)',
    'aim': 'THE AIM. One question, and one film answers it: does binding the PMU\'s clock let AppleARMRTC register the IORTC resource (INFERRED)?',
    'aim three': '(1) the line `AppleARMRTC registering service!` about 7 s after the MON CALL line; (2) no parity error at f704, f715 or f802 (the noise at 8800, the scratch pad and the 56-byte read stays); '
                 '(3) the bsd_init burst and "Waiting on <dict>" within about 10 s of the MON CALL line, not at about 60 s',
    'timers': 'so a MON STOP WATCHDOG page with VECTORS above 0 is the normal silent end of a waiting kernel and the timer question is not asked again',
    'expect': 'WHAT TO EXPECT. As in boots 21 and 24 to 27',
    'courses': 'Then the two courses part: with the clock bound the log is expected to go on with the clock driver\'s `tick read!` line and `registering service!`',
    'loop': 'the root device\'s silent loop of 60 s waits having its first timeout at about 70 s (no "Still waiting" line: it is compiled out, INFERRED from the public source read on the host)',
    'kprintf': 'are kprintf lines that go to the monitor\'s ring and not to the panel: the film cannot show them (INFERRED; boot 27 showed none)',
    'predict': 'PREDICTION: the kernel goes as in boot 27 through its first 7 s',
    'clean end': 'THE EXPECTED CLEAN END: MON STOP WATCHDOG at the end of the box (120 s after the call), with "Waiting on <dict>" as the last text, which now appears within about 10 s of the call',
    'falsifiers': 'FALSIFIERS: no `registering service!` line on any frame from the MON CALL line to 20 s after it',
    'falsifier parity': 'a parity error at f704 (count 4, parity_exp=0x000F), f715 (count 2) or f802 (count 6) after the clock\'s start lines; a MON STOP UNNAMED with B= in ' + CLOCK_WINDOW +
                        ', the clock model\'s window (the model refused an access: A= is 0X30 and eight more hex digits);',
    'falsifier later': 'ANY NEW STOP LATER, that is a MON STOP of any kind but WATCHDOG or a panic text: it falsifies the clean end and is read by its class below, as progress and a new question',
    'contradict': 'A `registering service!` line with the burst still at about 60 s would contradict the reading that IORTC was the only thing that the two waits were for: INFERRED',
    'film': 'the kernel\'s log with its time, frame by frame from the MON CALL line to 20 s after it (the clock driver\'s lines about 7 s in and the time of the bsd_init burst are results in every '
            'class that has no panic text',
    'decides': 'WHAT THE FILM AND THE PAGE DECIDE (what this record changes has not been seen; boots 21 and 24 to 27 are the comparison, OBSERVED once each)',
    'questions': 'with THE CLOCK QUESTIONS answered from it, each with its time',
    'not failure': 'NOT THE FAILURE: the parity-error lines of the registers that the clock model does not serve',
    'panic looks': 'with the word at 120 it is expected about 122 s in, outside this record\'s box by a margin of about 2 s; a text of that kind inside the box means that the margin was wrong and is the class '
                   'RUNG 8 PANIC TEXT below',
    'class panic': 'RUNG 8 PANIC TEXT: the panel shows a panic text, whatever the page says and whatever the stop is',
    'class not a result': 'RUNG 8 NOT A RESULT: a film that does not let the coordinator read the panel for text or read MON STOP',
    'clock not a result': 'or read the clock driver\'s lines and the time of the bsd_init burst (the log must be readable frame by frame from the MON CALL line to 20 s after it)',
    'class a': 'RUNG 8 NOT PASSED, (a): no panic text and MON STOP S2_FAULT with A= in 0X300734004..0X30073400C again: the acknowledge did not take.',
    'class new': 'RUNG 8 NEW STOP: no panic text and a MON STOP of any kind but WATCHDOG',
    'clock refused': 'Two readings of an S2_FAULT and one of an UNNAMED stop are named. THE CLOCK MODEL REFUSED AN ACCESS: MON STOP UNNAMED with B= in ' + CLOCK_WINDOW + ', the window of the clock '
                     'model, and A= 0X30 and eight more hex digits, the last of which is the device result (INFERRED, read on the host in probes/xnuboot/mmio_dispatch.c: the reporter does not name a '
                     'device model\'s refusal): the guest touched a register or a width that the model does not take, or the model was not started (UNKNOWN which)',
    'other ends': 'THE OTHER ENDS: a MON STOP UNNAMED with B= in the clock\'s window (the model refused an access), a MON STOP S2_FAULT in the window of a device that the earlier release or the '
                  'calendar now reaches',
    'clock finding': 'An S2_FAULT at another address is progress, and a MON STOP UNNAMED with B= in the clock\'s window is a finding about the clock model.',
    'aim thousand': 'failing its parity check four times in a row (address f704, 4 bytes, parity_rcv=0x0000, parity_exp=0x000F; about a thousand lines in all, with the other failing reads), '
                    'and the bsd_init burst at about 60 s',
    'usb': 'The USB "failed to find" lines are expected at about 31 s in either way (INFERRED).',
    'did not take': 'THE PROFILE DID NOT TAKE: A= lies in a window of a node that the profile withdrew (the video decoder\'s 0X380000000..0X381404000 but not its DART\'s 0X381020000..0X381040000, which '
                    'stays;',
    'took in 27': 'boot 27 ran this tree for 120 s without such a stop (OBSERVED once), so it would be new, a finding about the profile and not progress.',
    'moved': 'THE STOP MOVED: an S2_FAULT with A= anywhere else, in the window of a device that was not withdrawn',
    'class clock': 'RUNG 8 THE CLOCK DID NOT TAKE: MON STOP WATCHDOG at the end of the 120 s box, no panic text, and the film shows what boot 27\'s showed of the clock',
    'clock took': 'A film in which the line cannot be found but the burst is early and no parity error at those addresses is on it is not this class: the line may have scrolled past (INFERRED), and the '
                  'clock took.',
    'class waiting': 'RUNG 8 KERNEL WAITING: MON STOP WATCHDOG at the end of the 120 s box, no panic text, and the clock took (the class above does not fit).',
    'waiting results': '(1) THE CLOCK\'S ANSWERS, whether `AppleARMRTC registering service!` is on the film and when (predicted about 7 s in)',
    'burst 60': 'A burst that came at about 60 s with the line present would contradict the reading that IORTC alone ended the two 30 s waits (INFERRED): a finding about bsd_init.',
    'vectors 0': 'A page with VECTORS 0 would contradict boots 26 and 27 and is itself a finding.',
    'class b': 'RUNG 8 NOT PASSED, (b): a restart of the Surface before the report, no report',
}
ALL = {'section0': PINS_0, 'section0b': PINS_0B, 'section4': PINS_4, 'section0a': PINS_0A, 'section3c': PINS_3C, 'section5a': PINS_5A, 'section5b': PINS_5B, 'section5c': PINS_5C, 'section5d': PINS_5D}


class Pins(unittest.TestCase):
    def test_each_sentence_that_rung_8_says_again_is_pinned_and_a_record_without_it_is_rejected(self):
        parts = sections(STATE['r8'])
        for name, pins in ALL.items():
            with self.subTest(name):
                mutants_rejected(self, parts[name], pins)

    def test_a_generator_without_a_hook_makes_a_record_that_the_pins_reject(self):
        key = pn.kind_key({'panel': 'console', 'boot_arg': M8.F, 'let_panic': True, 'panel_facts': STATE['r8-panel']})
        for hook in ('stand', 'why', 'decides', 'hazard', 'step4', 'restart', 'restart_c', 'idle', 'ran'):
            with self.subTest(hook):
                words = dict(m1g.PANEL[key])
                words.pop(hook)
                with mock.patch.dict(m1g.PANEL, {key: words}):
                    code, text, err = M8.record8('mutant-hook')
                self.assertEqual(code, 0, err)
                self.assertNotEqual(text, STATE['r8'])
                parts = sections(text)
                self.assertTrue([n for name, pins in ALL.items() for n in pin_problems(parts[name], pins)], f'a record without the hook {hook} is rejected by a pin')

    def test_the_older_hooks_of_the_guide_are_what_they_were_for_every_other_record(self):
        self.assertIn('is UNTESTED: UNKNOWN (section 4)', m1g.restart_risk_words(120))
        self.assertIn('is UNTESTED: whether the Surface restarts there is UNKNOWN', m1g.restart_control_words(120))
        self.assertEqual((m1g.restart_risk_words(5), m1g.restart_control_words(5)), ('', ''))
        for name in ('r5-60', 'r6-120', 'r7'):
            f = flat(STATE[name])
            self.assertIn('is UNTESTED', f, name)
            self.assertNotIn('whether the Surface restarts was seen once', f, name)
        self.assertNotIn('UNTESTED', flat(STATE['r8']))


class Keyed(unittest.TestCase):
    def test_the_blocks_of_boots_24_to_27_are_held_to_the_records_rules(self):
        self.assertEqual(r8.VERDICTS, ('PANIC TEXT', 'WATCHDOG, NO TEXT', 'NEW STOP', 'KERNEL WAITING'))
        self.assertEqual([b['verdict'] for b in r8.BLOCKS.values()], list(r8.VERDICTS))
        self.assertEqual(list(r8.BLOCKS), ['Rung 4', 'Rung 5', 'Rung 6', 'Rung 7'])
        for what, block in r8.BLOCKS.items():
            lines = r8.keyed_lines(block, what)
            self.assertTrue(all(len(line) <= 120 for line in lines))
        bad = {'a verdict of another record': dict(r8.RUNG7, verdict='TIMER PANIC'), 'a paragraph without a label': dict(r8.RUNG7, words=('The kernel did what it did.',)),
               'a paragraph that is not ASCII': dict(r8.RUNG7, words=('OBSERVED \u00e9',)), 'the word that M1 records never use': dict(r8.RUNG7, words=('OBSERVED and ' + 'VERI' + 'FIED',))}
        for what, block in bad.items():
            with self.subTest(what), self.assertRaises(AssertionError):
                r8.keyed_lines(block, 'Rung 7')

    def test_boot_27s_numbers_are_the_operators_film_and_the_digits_are_rung_7s_bundle(self):
        self.assertEqual((r8.BOOT27_TICKS, r8.BOOT27_S), (2_307_553_890, '120.185'))
        self.assertEqual({k: hex(v) for k, v in r8.BOOT27_STOP.items()}, {'pc': '0xfffffe000b8716a8', 'a': '0x96000047', 'b': '0xfffffe110e00c040', 'c': '0xfffffe000ba87ea0'})
        self.assertEqual(r8.BOOT27_GUEST, r8b.RUNG7_GUEST[:16].upper())
        self.assertEqual((r8.BOOT27_RECORD[:8], r8.BOOT27_RECORD[-4:]), (r8b.RUNG7_RECORD[:8], r8b.RUNG7_RECORD[-4:]))
        self.assertEqual((r8.BOOT27_LABEL, r8.BOOT27_SPSR), ('M1RUN-ACKPANIC-Q-RUNG7', r6.BOOT25_SPSR), 'boot 27 stopped as boot 25 did: the instruction after a WFI')
        self.assertEqual((r8.RUNG5['record'], r8.RUNG6['record']), (r6.BOOT25_RECORD, r7.BOOT26_RECORD))
        self.assertEqual(r8.RUNG4['record'], r5.BOOT24_RECORD)

    def test_the_stale_sentences_of_rung_7s_record_are_in_no_part_of_rung_8s(self):
        f = flat(STATE['r8'])
        stale = ('WHY RUNG 7', 'THIS RECORD CHANGES TWO THINGS', 'RUNG 7 PANIC TEXT', 'RUNG 7 NEW STOP', 'RUNG 7 NOT PASSED', 'rung 7 is built with rung 6\'s pair and with no other',
                 'that anything runs with these nodes withdrawn', 'UNTESTED', 'is the profile quiet\'s (352 of 395 nodes) and the command line has', 'the longest run so far was boot 26\'s',
                 '(boots 21, 23, 24, 25 and 26: OBSERVED once each)', 'a quiet guest (rung 7:', 'THE AIM. Two questions', 'does withdrawing the', 'which appears about 62 s in',
                 'the first run that neither the monitor nor a panic', 'A=0X291000004 lies', 'the next stop of the run is a root device', 'rung 7 (K15)',
                 'THE CLOCK MODEL FAULTED', 'the model faulted', 'Three readings of an S2_FAULT', 'about a thousand times', 'OBSERVED once: boot 20, one unit',
                 'OBSERVED twice (boots 20 and 25', 'used here for the first time on the Surface Pro 11', 'lines come at about 31 s in either way')
        self.assertEqual([s for s in stale if s in f], [])
        for name in ('r5-60', 'r6-120', 'r7'):
            f = flat(STATE[name])
            for needle in ('rung 8', 'RUNG 8', 'quiet-rtc', 'R-RUNG', 'THE CLOCK DID NOT TAKE', 'registering service!'):
                with self.subTest(name=name, needle=needle):
                    if name == 'r7' and needle in ('registering service!',):
                        continue        # rung 7's record says what boot 26's film did not show of the clock driver
                    self.assertNotIn(needle, f)


class Classes(unittest.TestCase):
    def test_the_classes_of_section_5d_are_in_order_exclusive_and_leave_no_film_without_one(self):
        d = sections(STATE['r8'])['section5d']
        names = re.findall(r'(RUNG 8 (?:PANIC TEXT|NOT A RESULT|NOT PASSED, \((?:a|b)\)|NEW STOP|THE CLOCK DID NOT TAKE|KERNEL WAITING))', d)
        first = [n for i, n in enumerate(names) if n not in names[:i]]
        self.assertEqual(first, ['RUNG 8 PANIC TEXT', 'RUNG 8 NOT A RESULT', 'RUNG 8 NOT PASSED, (a)', 'RUNG 8 NEW STOP', 'RUNG 8 THE CLOCK DID NOT TAKE', 'RUNG 8 KERNEL WAITING', 'RUNG 8 NOT PASSED, (b)'],
                         'first-fit order: the clock class comes before the class that reads the clock as having taken')
        for kind in ('S2_FAULT', 'UNNAMED', 'PANIC_ENTRY', 'SERROR', 'DEVICE_RESET', 'GUEST_EXC'):
            self.assertIn(kind, d.split('RUNG 8 NEW STOP')[1].split('RUNG 8 THE CLOCK DID NOT TAKE')[0])
        self.assertIn('no panic text and a MON STOP of any kind but WATCHDOG', d)
        self.assertIn('or any other end that none of the results above names', d)
        self.assertNotIn('RUNG 7', d.replace('RUNG 7\'S', ''), 'no class carries rung 7\'s name')
        took = d.split('RUNG 8 THE CLOCK DID NOT TAKE')[1].split('RUNG 8 KERNEL WAITING')[0]
        for needle in ('f704 (count 4, parity_exp=0x000F)', 'f715 (count 2)', 'f802 (count 6)', 'registering service!', 'about 60 s or later', 'THE PREDICTION IS FALSIFIED'):
            self.assertIn(needle, took)

    def test_the_clock_window_is_the_one_that_the_builder_read_from_params_and_the_one_of_the_models_windows(self):
        diff = STATE['r8-panel']['rung8']['diff']
        self.assertEqual((r8b.CLOCK_BASE, diff['model_span']), (0x308700000, 0x18000))
        self.assertEqual(r8d.clock_window(STATE['r8-panel']), CLOCK_WINDOW)
        base, third = 0x308700000, 0x308714000        # the controller's three windows of 0x4000 bytes (0x308700000, 0x308704000, 0x308714000) and the model that spans them
        self.assertEqual(third + 0x4000 - base, 0x18000)
        d = sections(STATE['r8'])['section5d']
        self.assertEqual(d.count(CLOCK_WINDOW), 2, 'the falsifier and the class THE CLOCK MODEL REFUSED AN ACCESS name it, and nothing else does')

    def test_every_class_that_reads_a_stop_names_the_progress_and_the_falsifier_that_the_prediction_gives(self):
        d = flat(sections(STATE['r8'])['section5d'])
        for phrase in ('THE CLOCK MODEL REFUSED AN ACCESS', 'THE PROFILE DID NOT TAKE', 'THE STOP MOVED', 'progress', 'KERNEL WAITING', 'THE EXPECTED CLEAN END', 'FALSIFIERS', 'the four JPEG nodes', 'INFERRED'):
            self.assertIn(phrase, d)

    def test_the_prediction_names_what_is_inferred_and_nothing_of_it_is_called_verified_or_observed(self):
        d = flat(sections(STATE['r8'])['section5d'])
        predicts = d.split('WHAT THE RECORD PREDICTS, AND WHAT FALSIFIES IT')[1].split('THE FILM is rung 5')[0]
        self.assertTrue(predicts.startswith(' (INFERRED; no probabilities; none of it has been seen with this guest'))
        self.assertNotIn(pp.NEVER, predicts)
        for sentence in re.split(r'(?<=[.;]) ', predicts):
            if 'registering service' in sentence and 'about 7 s' in sentence:
                self.assertNotIn('OBSERVED', sentence, 'the clock\'s registration is a prediction and not an observation')


class Anchors(unittest.TestCase):
    def test_a_change_of_rung_7s_words_is_an_assertion_error_not_silent_text(self):
        self.assertIs(r8.patched, r7.patched)
        with self.assertRaises(AssertionError):
            r8.patched('rung 7 words that changed', ('not in the text', 'x'))
        with self.assertRaises(AssertionError):
            r8.patched('twice twice', ('twice', 'x'))
        self.assertEqual(r8.patched('one old text', ('old', 'new')), 'one new text')

    def test_the_guide_key_the_module_and_the_hooks_of_the_record(self):
        key = f'console+{M8.F}+let_panic+{M8.RTC}'
        panel = STATE['r8-panel']
        self.assertEqual(pn.kind_key({'panel': 'console', 'boot_arg': M8.F, 'let_panic': True, 'panel_facts': panel}), key)
        self.assertIn(key, m1g.PANEL)
        self.assertEqual([pn._rung(panel), pn._rung({k: v for k, v in panel.items() if k != 'profile'}), pn._rung({**panel, 'profile': M8.Q}), pn._rung({**panel, 'boot_arg': M7.W90, 'profile': None})],
                         [r8, r5, r7, r6])
        self.assertEqual((r8.NUMBER, pn.ACK), (8, (r5, r6, r7, r8, pn.ACK[4])))
        self.assertEqual(pn.ACK[4].NUMBER, 9, 'rung 9 (K17) is the fifth of the acknowledging monitor\'s records')
        self.assertIn('counts as a result of rung 8.', flat(STATE['r8']))
        new = {'stand', 'why', 'decides', 'hazard', 'rung', 'step4', 'idle', 'console', 'ran', 'restart', 'restart_c', 'stand_head', 'stop_seen', 'wd_exit', 'wd_box'}
        self.assertTrue(new <= set(m1g.PANEL[key]))
        self.assertTrue(all(m1g.PANEL[key][k] is m1g.PANEL[f'console+{M8.F}+let_panic+{M8.Q}'][k] for k in ('page', 'step6', 'film_until', 'whole', 'fault_seen')),
                        'the legend, the film steps and the fault words are rung 7\'s')
        seen, seen7 = m1g.PANEL[key]['stop_seen'], m1g.PANEL[f'console+{M8.F}+let_panic+{M8.Q}']['stop_seen']
        self.assertEqual({k: v for k, v in seen.items() if k != 'WATCHDOG'}, {k: v for k, v in seen7.items() if k != 'WATCHDOG'}, 'the stop words are rung 7\'s but for the count of the watchdog')
        self.assertNotEqual(seen['WATCHDOG'], seen7['WATCHDOG'])
        self.assertEqual(sorted(k for k in m1g.PANEL if 'let_panic' in k), sorted([f'console+{M8.F}+let_panic', f'console+{M7.W90}+let_panic', 'console+cpus=1+let_panic',
                                                                                      f'console+{M8.F}+let_panic+{M8.Q}', key, 'console+cpu_boot_timeout_secs=360+let_panic+j700ap-26A428-m1-quiet-rtc-acm']))

    def test_the_profile_paragraphs_are_checked_on_the_finished_record_and_a_seeded_false_sentence_is_found(self):
        text, panel = STATE['r8'], STATE['r8-panel']
        self.assertEqual(r8.record_problems(text, panel), [])
        flat_text = ' '.join(text.split())
        self.assertEqual(r8.record_problems(flat_text.replace('CHANGES ONE THING FROM RUNG 7', 'CHANGES ONE THING IN THE GUEST'), panel), ['FALSE: one thing in the guest'])
        clock = ' '.join(r8.CLOCK_TEXT.split()[:6])
        self.assertEqual(r8.record_problems(flat_text.replace(clock, 'DAMAGED'), panel), ['MISSING'])
        self.assertEqual(r8.record_problems(flat_text.replace('smc-gpio stays published', 'x'), panel), ['MISSING'])
        self.assertEqual(len(r8.profile_paragraphs(panel)), 7)


class Review(unittest.TestCase):
    """The reviewer's findings on the first text (files/review-m1-r12-1.md): each corrected sentence is in the record once, the wording that it replaced is in no part of it, a record with the old
    wording is rejected by the pins (a seeded-wrong record), and the sources that the new sentence reads say what it says."""
    OLD_NEW = (
        ("THE OTHER ENDS: a MON STOP S2_FAULT, in the clock's window or in the window of a device that",
         "THE OTHER ENDS: a MON STOP UNNAMED with B= in the clock's window (the model refused an access), a MON STOP S2_FAULT in the window of a device that"),
        ("a MON STOP S2_FAULT with A= in 0X308700000..0X308718000, the clock model's window (the model faulted, or the guest touched a register that it does not take);",
         "a MON STOP UNNAMED with B= in 0X308700000..0X308718000, the clock model's window (the model refused an access: A= is 0X30 and eight more hex digits);"),
        ("and one in the clock's window is a finding about the clock model.", "and a MON STOP UNNAMED with B= in the clock's window is a finding about the clock model."),
        ("Three readings of an S2_FAULT are named. THE CLOCK MODEL FAULTED: A= lies in 0X308700000..0X308718000, the window of the clock model: the guest touched a register that the model does "
         "not take, or the model faulted (UNKNOWN which)",
         "Two readings of an S2_FAULT and one of an UNNAMED stop are named. THE CLOCK MODEL REFUSED AN ACCESS: MON STOP UNNAMED with B= in 0X308700000..0X308718000, the window of the clock model, "
         "and A= 0X30 and eight more hex digits, the last of which is the device result (INFERRED, read on the host in probes/xnuboot/mmio_dispatch.c: the reporter does not name a device model's "
         "refusal): the guest touched a register or a width that the model does not take, or the model was not started (UNKNOWN which)"),
        ("fails its parity check about a thousand times",
         "fails its parity check four times in a row (about a thousand lines in all, with the other failing reads)"),
        ("failing its parity check (address f704, 4 bytes, parity_rcv=0x0000, parity_exp=0x000F) about a thousand times, and the bsd_init burst",
         "failing its parity check four times in a row (address f704, 4 bytes, parity_rcv=0x0000, parity_exp=0x000F; about a thousand lines in all, with the other failing reads), and the bsd_init burst"),
        ("MON STOP WATCHDOG (OBSERVED once: boot 20)", "MON STOP WATCHDOG (OBSERVED: boots 20, 25 and 27)"),
        ("MON EXIT PC= beside it (OBSERVED once: boot 20, one unit, one boot)", "MON EXIT PC= beside it (OBSERVED: boots 20, 25 and 27, one unit, one boot each)"),
        ("used all of it (OBSERVED once: boot 20)", "used all of it (OBSERVED: boots 20, 25 and 27)"),
        ("OBSERVED twice (boots 20 and 25, one unit, one boot each).", "OBSERVED three times (boots 20, 25 and 27, one unit, one boot each)."),
        ("used here for the first time on the Surface Pro 11,", "used on the Surface Pro 11 in boots 24 to 27 (OBSERVED once each),"),
        ('The USB "failed to find" lines come at about 31 s in either way.', 'The USB "failed to find" lines are expected at about 31 s in either way (INFERRED).'))

    def test_the_old_wording_is_in_no_part_of_the_record_the_new_is_in_it_once_and_a_record_with_the_old_is_rejected(self):
        f = flat(STATE['r8'])
        for index, (old, new) in enumerate(self.OLD_NEW):
            with self.subTest(index):
                self.assertEqual((f.count(new), f.count(old)), (1, 0))
                parts = sections(f.replace(new, old))
                self.assertTrue([n for name, pins in ALL.items() for n in pin_problems(parts[name], pins)], 'a record with the old wording is rejected by some pin')

    def test_a_refusal_by_a_device_model_is_a_stop_that_the_reporter_does_not_name_with_the_ipa_in_b(self):
        reporter = (ROOT / 'probes' / 'sp11-stick-reporter.c').read_text()
        names = reporter.split('static const char *rp_event_name(uint64_t kind)')[1].split('\n}\n')[0]
        self.assertNotIn('case 34:', names)
        self.assertIn('default: return "UNNAMED";', names)
        self.assertIn('rp_put_hex(a, &l, " A=", sp11sb_rd64(w + RP_W_TERMINAL + 16));', reporter)
        self.assertIn('rp_put_hex(a, &l, " B=", sp11sb_rd64(w + RP_W_TERMINAL + 24));', reporter)
        self.assertIn('#define EV_DEVICE_FAILED 34    /* a = kind << 32 | device_result, b = IPA, c = ESR */', (ROOT / 'probes' / 'xnuboot' / 'report_layout.c').read_text())
        self.assertIn('else record(c, EV_DEVICE_FAILED, *elr, (model->kind << 32) | result, ipa, esr);', (ROOT / 'probes' / 'xnuboot' / 'mmio_dispatch.c').read_text())
        d = flat(sections(STATE['r8'])['section5d'])
        self.assertEqual(d.count('MON STOP UNNAMED with B= in'), 4, 'the other ends, the falsifier, the finding and the class name it')
        self.assertNotIn('S2_FAULT with A= in ' + CLOCK_WINDOW, d)


class Cites(unittest.TestCase):
    def test_every_source_line_it_cites_is_in_a_table_that_holds_its_fragments(self):
        pattern = r'((?:probes|shadow|compat|tools)/[A-Za-z0-9_./+-]+\.(?:c|h|py)):(\d+)(?:-(\d+))?'
        text = ' '.join(sections(STATE['r8']).values())
        cited = {(p, int(a), int(b or a)) for p, a, b in re.findall(pattern, text)}
        known = {(p, a, b) for p, a, b, _ in (*pn.CITES, *rp.CITES, *r5.CITES, *r7.CITES)}
        self.assertLessEqual(cited, known, 'cited, and no table holds the fragments of it')
        self.assertIs(r8.CITES, r7.CITES)
        self.assertEqual({(p, a, b) for p, a, b, _ in r8.CITES} - cited, set(), 'every entry of the table is cited')


class Hygiene(unittest.TestCase):
    def test_nothing_is_called_verified_and_the_record_and_the_modules_are_ascii_and_within_the_line_width(self):
        f = STATE['r8']
        self.assertTrue(f.isascii() and pp.NEVER not in f)
        self.assertIn('0a. Where things stand (boots 20, 21, 23 to 27; 2026-11-18/20; one unit, one boot each; OBSERVED unless INFERRED)', f)
        self.assertEqual(f.count(FIVE_D), 1)
        self.assertIn(r8d.heading(120), f)
        for rel in ('tools/sp11_stick_bundle_panel_rung8.py', 'tools/sp11_stick_bundle_panel_rung8d.py', 'tools/sp11_stick_bundle_rung8.py', 'tools/test-sp11-stick-monitor-rung8.py',
                    'tools/test-sp11-stick-monitor-rung8-words.py'):
            text = (ROOT / rel).read_text()
            with self.subTest(rel):
                self.assertTrue(text.isascii())
                self.assertNotIn(pp.NEVER, text)
        self.assertTrue(all(len(line) <= 120 for line in f.split('0. What approving')[1].split('1. What may run')[0].splitlines() + f.split(FIVE_D)[1].splitlines()))
        self.assertLessEqual(len(f.encode()), M8.HELD_LIMIT)

    def test_each_paragraph_of_the_words_says_what_it_is(self):
        panel = STATE['r8-panel']
        for paragraph in (*r8.extra_paragraphs(panel), r8.word_text(), r8.expect_text(panel['run']), ' '.join(r8.stand_extra({'panel_facts': panel})), ' '.join(r8.why_lines({'panel_facts': panel})),
                          *r8d.results_paragraphs(panel, panel['run'], 180), r8d.new_text(panel), r8d.aim_text(panel), r8d.predicts_text(panel), r8d.decides_text(), r8.CLOCK_TEXT, r8.RESTART,
                          r8.RESTART_CONTROL):
            with self.subTest(paragraph[:40]):
                self.assertTrue(re.search(r'OBSERVED|INFERRED|UNKNOWN|THE RESULTS|RUNG 8|WHAT IS NEW|THE AIM|A PROFILE\'S', paragraph), 'each paragraph is labelled or is a result')
        self.assertTrue(re.search(r'OBSERVED|INFERRED|UNKNOWN', r8.ACK_REACH))
        self.assertIs(r8d.page_legend, r7d.page_legend)


if __name__ == '__main__':
    unittest.main()
