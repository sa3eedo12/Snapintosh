# SPDX-License-Identifier: MIT
"""Section 5d of the record of rung 5 (docs/SP11-STICK-MONITOR.md section 20, K13), in the operator's words: what is new, what to expect, what to film, what the page shows, what the film
decides, what a panic looks like and what counts as a result. The rest of the record's words for this rung (sections 0, 0a, 0b, 3c and the steps of 5c) are
tools/sp11_stick_bundle_panel_rung5.py's, which this module takes its constants from; its `section_5d` reaches this module.

The results are read in order and a film is in the first class that fits (PANIC TEXT, NOT A RESULT, NOT PASSED (a), WATCHDOG with no text, NEW STOP, NOT PASSED (b)); a run that uses the
whole box is expected, and a MON STOP WATCHDOG whose log kept growing is progress and not a failure. Every time-dependent sentence takes the box from the record (`box_s`, and `wait_s`,
the silence rule's wait for it).

Evidence: EXPERIMENTAL, hardware NOT RUN."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_bundle_record_m1 as m1t  # noqa: E402
import sp11_stick_bundle_panel_rung2 as r2  # noqa: E402
import sp11_stick_bundle_panel_rung3 as r3  # noqa: E402
import sp11_stick_bundle_panel_rung5 as r5  # noqa: E402

STOP24 = f'MON STOP S2_FAULT A={r3.page_hex(r5.BOOT24_STOP["a"])} B={r3.page_hex(r5.BOOT24_STOP["b"])}'
NOT_THE_FAILURE = ('the noise lines of section 0a (the SPMI and RTKit lines OBSERVED once in boots 21 and 23, the AFKFirmwareService line in boot 21; INFERRED to come from the monitor\'s '
                   'empty models), which the film shows before any panic text')
HEADING = '5d. Rung 5: what is new, what to film, and what counts as a result (the CPU-start acknowledge and let_panic)'


def results_paragraphs(run, wait_s):
    """The results of rung 5, one paragraph each, in the operator's words, read in this order from the film of either camera and the stills: the first class that fits is the film's."""
    box_s = run['box_s']
    return (
        'THE RESULTS. M1\'s own verdict is section 5a\'s and is reported beside rung 5\'s. The panel is read first and then the stop; a film is in the first class below that fits. Success of this '
        'rung is progress: the kernel gets past the PMGR driver and the CPU-start write and the film shows where it goes on. PANIC TEXT (other than the two falsifiers), WATCHDOG with a log that '
        'kept growing and NEW STOP at a later device are progress; NOT PASSED (a) and (b) are the failures. A WATCHDOG whose log stood still is a result of its own (below), neither.',
        'RUNG 5 PANIC TEXT: the panel shows the kernel\'s panic text, whatever the page says and whatever the stop is. THE TEXT IS THE RESULT: the coordinator transcribes its first lines verbatim '
        'from the film and the stills and hands them back. It is neither a pass nor a failure of the record; if it is the ApplePMGR assertion of boot 24 again, or "cpu 1 failed to boot for the '
        'first time, waited N seconds" with N shorter than the box, it is also the falsifier that the prediction above names. M1\'s verdict is separate.',
        'RUNG 5 NOT A RESULT: a film that does not let the coordinator read the panel for text or read MON STOP (too soft, too dark, the report unreadable under the kernel\'s text, the text too '
        'small to read): nothing is concluded from it, and the one-boot rule of section 5c says whether the approval is spent.',
        f'RUNG 5 NOT PASSED, (a): no panic text and MON STOP S2_FAULT with {r5.WINDOW} again: the acknowledge did not take. Either the access is one that the monitor does not take by design (a '
        'read, another width, a syndrome that does not describe it: section 3c) or it is a fault of the monitor: B= on the page is the syndrome, and the coordinator decodes it before saying '
        'which. It is the first thing to find; the page does not say why. A read there would be new and unexpected: nothing after the write reads (section 3c).',
        f'RUNG 5 WATCHDOG, NO TEXT: MON STOP WATCHDOG at the end of the {box_s} s box and no panic text: the kernel was still running, waiting for the CPU that never comes, working through its '
        'drivers or spinning (UNKNOWN which). A log that was still growing, or went beyond boot 21\'s last line, is PROGRESS and not a failure; a log that stood still for most of the box is the '
        'kernel\'s wait (INFERRED) and a result as well. Its last lines say how far it went.',
        'RUNG 5 NEW STOP: no panic text and a MON STOP of another kind (S2_FAULT, UNNAMED, PANIC_ENTRY, SERROR, DEVICE_RESET or another name) or an S2_FAULT with another A=: its kind, PC=, A= and '
        'the last line of the log are the result, and the coordinator reads them first. An S2_FAULT elsewhere in the CPU-start block, outside 0X300734004..0X30073400C, is the monitor failing '
        'closed on an access it does not take, a finding to read; inside the panel range (section 2) it is not M1\'s expected end (section 5a).',
        f'RUNG 5 NOT PASSED, (b): a restart of the Surface before the report, no report (a page that stops at MON CALL, a REFUSED line, MON PREPARE FAILED, a fault page of q1n1), the panel still '
        f'dark {wait_s} seconds after the MON CALL line, or any other end that none of the results above names, and no panic text: a restart is NOT A RESULT for M1 (section 5a) and NOT PASSED for '
        'rung 5, which has not reached its aim.')


def section_5d(panel, wait_s):
    """Section 5d of the record of rung 5, after 5c, in the operator's words. `wait_s` is the silence rule's wait for the box (the guide's silence_s)."""
    box_s = panel['run']['box_s']
    s = r3.BOOT21_STOP
    new = (f'WHAT IS NEW. The monitor ACKNOWLEDGES the guest\'s CPU-start stores (EXPERIMENTAL; not a model of the hardware: NO CPU IS STARTED and the three stores are dropped: section 3c). The guest\'s '
           f'command line is {r5.LINE}: six CPUs, no cpus=1 (with it boot 24 asserted in the PMGR driver), and XNU waits 120 s for the CPU that never announces itself. let_panic is set, as in boot 24, so '
           f'that the kernel\'s own panic text prints. The box is {box_s} s (D5\' and D8\': one CPU loaded for about {box_s} s, AC power REQUIRED, no temperature read). The panel, serial=2 and the three '
           'store classes are as before, and SNAPREP-M1-ACK.BIN is the reporter of the earlier runs.')
    expect = (f'WHAT TO EXPECT. As in boots 21 and 24: from the MON CALL line the panel is cleared and fills with the kernel\'s log, and the SNAPREP report page appears on its right when the guest has '
              f'stopped, about {box_s} s after the MON CALL line if the box is used, sooner on a stop. The panel may stand still for most of that time while the kernel waits for the CPU that never '
              f'comes (UNKNOWN): that is not a stop: only the report page (about {box_s} s) or, if it does not come, the {wait_s} s rule of section 5c ends the wait. A dark panel is expected for '
              f'part of the time (the kernel clears it); it is a restart only if the Shell\'s countdown or the runner\'s first lines come back. {r2.force_off(wait_s)}. {r2.COUNT.format(wait=wait_s)}')
    predicts = ('WHAT THE RECORD PREDICTS, AND WHAT FALSIFIES IT (INFERRED; no probabilities; none of it has been seen; read on the host in the kernelcache, a file and not a run, and in the public '
                'source). PREDICTION: the PMGR driver\'s start passes, as in boot 21 (two clusters); the kernel\'s CPU-boot thread starts all five secondary CPUs first (processor_boot does not wait: '
                '10 to 15 acknowledged stores in five start sequences), and after the write there is no read, poll, retry or wait in that path, so the acknowledge is enough; no CPU starts; the same '
                f'thread then waits for CPU 1 (120 s against the {box_s} s box: no "failed to boot" panic inside the box); IOKit matching and the root mount are not blocked by that wait in the '
                'source (INFERRED; whatever waits for all CPUs to be initialised waits, UNKNOWN which), so the kernel goes on on the boot CPU; and the run ends at the box (MON STOP WATCHDOG, the log '
                'showing how far it got), at the first device that the monitor does not model (MON STOP S2_FAULT with another A=: progress, and a new question; the host analysis names the video '
                f'decoder\'s window at IPA 0X380001000 as the likeliest first one, INFERRED, so the run may well end within seconds of the acknowledged stores and leave most of the {box_s} s box '
                'unused, which is no failure of this record; the others and the order are UNKNOWN) or in a new driver panic whose text now prints. FALSIFIERS: the '
                f'same ApplePMGR assertion (the topology reading is wrong); a MON STOP S2_FAULT with {r5.WINDOW} {r5.FALSIFIER_ACK}, a READ of that block being new and unexpected, since nothing after '
                'the write reads; a panic text "cpu 1 failed to boot for the first time, waited N seconds" (the timeout word was not honoured). Any panic may be followed by boot 24\'s end, an '
                'S2_FAULT at a read in the chip-reset path (INFERRED), which is a later stop and not a result of its own.')
    filming = (f'THE FILM is rung 4\'s, from the Shell\'s countdown until {r5.FILM_AFTER_S} seconds after the SNAPREP report page has appeared, or, if it never appears, until {r5.FILM_AFTER_S} s after the '
               f'last page change (about {r5.film_total_s(box_s)} s in all for this box): the whole panel with the main camera, 30 frames a second or more, steady, and if there is a second camera the '
               'TOP-LEFT quarter close up (step 2 of section 5c). It must show PAYLOAD CALL 0 (only the film shows it once the kernel has cleared the panel: if it does not, the approval is spent), the '
               'kernel\'s log, any panic text and the report with MON STOP and MON EXIT. THE PANIC TEXT, if there is one, is the result and may scroll off the part of the panel left of the report page '
               '(about its left 55 percent) (INFERRED): nobody touches the keyboard, the screen or the buttons while it runs (except FORCE OFF as step 5 of section 5c says). After those '
               f'{r5.FILM_AFTER_S} seconds, and BEFORE FORCE OFF (only when the SNAPREP page is on the screen; if it is not, FORCE OFF {wait_s} seconds after the MON CALL line as step 5 says, no stills '
               'first), take three sharp still photographs of that part of the panel, close and without glare, one of its top band, one of its middle and one of its bottom (asked, not required, and '
               'valuable). Lowercase letters on the panel are the kernel\'s (boots 21, 23 and 24: OBSERVED once each).')
    page = ('WHAT THE PAGE SHOWS. The first five events and the last stop, not the whole run (the monitor keeps at most the first 512 events: section 5b). The acknowledged stores are counted in a report '
            'window that this SNAPREP does not print: how many the monitor took is UNKNOWN to the page and the film, and no result rests on it. The MON CON lines hold the first lines of console that '
            'reach the monitor\'s ring: boot 24\'s carried the first lines of its panic text (OBSERVED once), boots 21 and 23 had none; the rest of a panic text is on the panel only (INFERRED).')
    decides = ('WHAT THE FILM DECIDES (what this record changes has not been seen; boots 21 and 24 are the comparison, OBSERVED once each). First the panel: the coordinator transcribes the first lines '
               'of any panic text from the film and the stills; the two falsifiers above read from it, and any other panic is the result. Then the kernel\'s log, replayed from its start '
               f'by the first frames after the MON CALL line: boot 21\'s had {r3.LOG_CPU0} early and a line {r3.LOG_CPUN} for each of the processors 1 to 5 ({r3.LOG_T}); with six CPUs they are expected '
               f'again and are not the failure. Then the report page: boot 21\'s stop was MON STOP S2_FAULT A={r3.page_hex(s["a"])} B={r3.page_hex(s["b"])} at {r3.BOOT21_S} s, the CPU-start stop is a '
               f'MON STOP S2_FAULT with {r5.WINDOW} (a read there, B= saying WnR 0, would be new and unexpected), and boot 24\'s was {STOP24} after its panic. An S2_FAULT at another address, or a run '
               f'longer than boot 21\'s, is progress. How far the log got, and its last line, is the result. NOT THE FAILURE: {NOT_THE_FAILURE}.')
    panic = ('WHAT A PANIC LOOKS LIKE (boot 24 showed one, OBSERVED once; the rest is INFERRED from XNU\'s panic format): a line that contains "panic(cpu" (the kernel may put its timestamp in front) '
             'and a caller address, the panic string (an assertion names its file and line), "Nested panic detected - entry count" lines when it panics again, the kexts of its backtrace and, at the '
             'end, "Nested panic count exceeds limit 5, machine will reset or spin" and "wdog: panic chipreset". The word "panic" alone is not one (the RTKit lines "General panic behavior" and the '
             'early line "debug_log_init: Error!! gPanicBase is still not initialized", OBSERVED twice in boot 23\'s replay, are not a panic). A call that the monitor refuses after the panic entry would '
             'stop the run before the text (MON STOP UNNAMED: section 3c). Either way, film it. The panic of the CPU that never comes reads "cpu 1 failed to boot for the first time, waited N '
             'seconds" (INFERRED); with the word at 120 it cannot come inside the box.')
    return (HEADING, *(line for text in (new, expect, predicts, filming, page, decides, panic, *results_paragraphs(panel['run'], wait_s)) for line in m1t.wrap(text, 3, 6)))
