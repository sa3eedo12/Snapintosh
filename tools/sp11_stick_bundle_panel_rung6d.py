# SPDX-License-Identifier: MIT
"""Section 5d of the record of rung 6 (docs/SP11-STICK-MONITOR.md section 20, K14), in the operator's words: what is new, what to expect, what the record predicts and what falsifies it, what to
film, what the page shows, what the film and the page decide, what a panic looks like and what counts as a result; and the legend of the lines that the reporter of this record prints (the guide's
section 5b gets it by the hook `page`). The rest of the record's words for this rung (sections 0, 0a, 0b, 3c and the steps of 5c) are tools/sp11_stick_bundle_panel_rung6.py's, which this module takes
its constants from; its `section_5d` reaches this module.

The results are read in order and a film is in the first class that fits (TIMER PANIC, PANIC TEXT other than that, NOT A RESULT, NOT PASSED (a), WATCHDOG with no text, NEW STOP, NOT PASSED (b)); a run
that uses the whole box is a result, and a MON STOP WATCHDOG is read by the guest timer's words of its page in an order that leaves no page without a class. Every time-dependent sentence takes
the box from the record (`box_s`, and `wait_s`, the silence rule's wait for it).

Evidence: EXPERIMENTAL, hardware NOT RUN."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_bundle_record_m1 as m1t  # noqa: E402
import sp11_stick_bundle_panel_rung2 as r2  # noqa: E402
import sp11_stick_bundle_panel_rung3 as r3  # noqa: E402
import sp11_stick_bundle_panel_rung5 as r5  # noqa: E402
import sp11_stick_bundle_panel_rung6 as r6  # noqa: E402

def heading(box_s):
    """The heading of section 5d: it names the wait and the box of this record."""
    return f'5d. Rung 6: what is new, what to film, and what counts as a result (the wait of {r6.CPU_WAIT_S} s, a box of {box_s} s, the timer words)'
STOP24 = f'MON STOP S2_FAULT A={r3.page_hex(r5.BOOT24_STOP["a"])} B={r3.page_hex(r5.BOOT24_STOP["b"])}'
STOP25 = f'MON STOP WATCHDOG PC={r2.pc_text(r6.BOOT25_STOP["pc"])} A={r3.page_hex(r6.BOOT25_STOP["a"])} B={r3.page_hex(r6.BOOT25_STOP["b"])}'
TIMER_TEXT = f'cpu 1 failed to boot for the first time, waited {r6.CPU_WAIT_S} seconds'
OTHER_LINES = '"RTC did not show up", a line with "getGMTTimeOfDay" and "call failed with", "!BSD" and "Waiting on" and a matching dictionary'
OTHER_WAITS = (f'{OTHER_LINES}: INFERRED from the host analysis of the kernelcache (a file and not a run), the kernel\'s 30 s waits for the RTC resource and the BSD resource, which come after '
               'boot 25\'s last line')
NOT_THE_FAILURE = ('the noise lines of section 0a (the SPMI, PMU and RTKit lines OBSERVED once in boots 21, 23 and 25, the AFKFirmwareService line in boot 21 and the USB "failed to find" lines in boot '
                   '25; INFERRED to come from the monitor\'s empty models), which the film shows before any panic text')


def results_paragraphs(run, wait_s):
    """The results of rung 6, one paragraph each, in the operator's words, read in this order from the film of either camera, the stills and the page: the first class that fits is the film's."""
    box_s = run['box_s']
    return (
        'THE RESULTS. M1\'s own verdict is section 5a\'s and is reported beside rung 6\'s. The panel is read first, then the stop, and for a stop at the end of the box the page\'s MON TIM line; a '
        'film is in the first class below that fits, in this order. The aim of this rung is one answer: does a guest timer interrupt reach the kernel? The TIMER PANIC answers yes, and the '
        'classes of a silent run answer it from the monitor\'s counters (INFERRED); a panic or a stop of another kind is progress and is read for what it is. NOT A RESULT and the two NOT PASSED '
        'classes are the failures: nothing is learnt, or the record did not reach its aim.',
        f'RUNG 6 TIMER PANIC: the panel shows the panic text "{TIMER_TEXT}" (the number is the word\'s), about {r6.PANIC_AT_S} s after the MON CALL line by the film\'s clock (a few seconds either way), '
        'whatever the page says and whatever the stop is: nested panics and boot 24\'s end, MON STOP S2_FAULT at a read in the kernel\'s chip-reset path, follow within about a second and are a later '
        'stop and not a result of their own. THE TEXT IS THE RESULT and the answer: a guest timer interrupt reached the kernel (the text is OBSERVED once, if it comes; that it took a timer interrupt is '
        'INFERRED). The coordinator transcribes its first '
        'lines verbatim from the film and the stills and hands them back, and reads the page\'s MON TIM line beside it (IRQS and VECTORS above 0: INFERRED). M1\'s verdict is separate.',
        f'RUNG 6 PANIC TEXT, OTHER: the panel shows another panic text, whatever the page says and whatever the stop is. THE TEXT IS THE RESULT: the coordinator transcribes its first lines verbatim '
        'from the film and the stills. It is also a falsifier if it is the ApplePMGR assertion of boot 24 again, or "cpu 1 failed to boot for the first time, waited N seconds" with N other than '
        f'{r6.CPU_WAIT_S} (the word was not honoured). A panic of another driver at a later point is progress and says nothing about the timers by itself.',
        'RUNG 6 NOT A RESULT: a film that does not let the coordinator read the panel for text or read MON STOP (too soft, too dark, the report unreadable under the kernel\'s text, the text too small '
        'to read): nothing is concluded from it, and the one-boot rule of section 5c says whether the approval is spent.',
        f'RUNG 6 NOT PASSED, (a): no panic text and MON STOP S2_FAULT with {r5.WINDOW} again: the acknowledge did not take. Either the access is one that the monitor does not take by design (a '
        'read, another width, a syndrome that does not describe it: section 3c) or it is a fault of the monitor: MON ESR and MON WVAL on the page and B= are the syndrome, and the coordinator '
        'decodes it before saying which. A read there would be new and unexpected: nothing after the write reads (section 3c).',
        f'RUNG 6 WATCHDOG, NO TEXT: MON STOP WATCHDOG at the end of the {box_s} s box and no panic text. The timer question is read from the page\'s MON TIM line (section 5b), in this order, the first '
        'that fits: (a) LINE RAISED, NOT TAKEN, ASSERTS above CLEARS and VECTORS below ASSERTS: the monitor raised the guest\'s timer line more often than the guest entered its FIQ vector, and '
        'the line was still raised at the stop (INFERRED from the monitor\'s counters; the timer\'s own state is then CTL=0X5). VECTORS 0 is the clean form: no timer interrupt reached the kernel; '
        'with VECTORS above 0 the kernel took a timer interrupt earlier, which answers the question of this rung as (b) does, and only the last line is in doubt. '
        'Whether a WFI wakes on a pending interrupt that is masked, on this CPU, is UNKNOWN: this class decides the next change of the monitor. (b) TIMER DELIVERED, VECTORS above 0: the guest took '
        'at least one timer interrupt (INFERRED from the monitor\'s counters), so the timers work: the kernel waits for something '
        f'else, or its wait of {r6.CPU_WAIT_S} s began later than predicted. CTL and CVAL say what it waits for in time: CTL=0X1 is a deadline that the coordinator places by the distance of CVAL from '
        'LAST of the MON CLK line (CVAL less 1.25 times LAST, in ticks of 24 MHz: INFERRED) and says whether it lies beyond the box, and CTL=0X5 is a deadline that had passed and was not yet served at the '
        'report. (c) LOST TICK, VECTORS 0 '
        'and CTL=0X5 with CVAL more than a second (24 million ticks) behind 1.25 times LAST: a deadline was armed, passed long before the report and is not masked, and no tick raised the line for it '
        '(IRQS 0 is the clean form). (d) NOT DUE, VECTORS 0 and CTL=0X1, or CTL=0X5 with CVAL within a second of 1.25 times LAST: a deadline is armed and had not been served at the report; '
        'CVAL says where it lies. (e) NOTHING ARMED, VECTORS 0 and CTL=0X0, 0X2, 0X3 or 0X7: the control says disabled or masked, which this kernel does not leave (it writes the control only with 1 and '
        're-arms with the compare interval: read on the host in the kernelcache, a file and not a run), so CTL=0X0 with WRITES 0 says that the guest had not reached its timer set-up, and any other page '
        'is a finding to read, UNKNOWN whether it is the monitor\'s decode or accounting or the guest; no reading of what the kernel waits for is drawn from it. (f) Any other MON TIM line (a CTL of 0X4, '
        '0X6 or above 0X7, which the monitor cannot store: it keeps bits 0 and 1 of what the guest writes and computes bit 2 only for an enabled timer) or none (the monitor left no capture word of class '
        '1, 2 or 3, UNKNOWN why): a finding to read, UNKNOWN whether it is the monitor\'s decode or accounting or the guest, and the film and the other lines are the result. A log that kept growing to the end, '
        'or went beyond boot 25\'s last line, is progress in every case.',
        'RUNG 6 NEW STOP: no panic text and a MON STOP of another kind (S2_FAULT, UNNAMED, PANIC_ENTRY, SERROR, DEVICE_RESET, GUEST_EXC or another name) or an S2_FAULT with another A=: its kind, PC=, A=, '
        'the page\'s MON ESR, MON WVAL and MON GX lines and the last line of the log are the result, and the coordinator reads them first, with the MON TIM line (VECTORS above 0 answers the timer '
        'question as TIMER DELIVERED does). An S2_FAULT elsewhere in the CPU-start block, outside 0X300734004..0X30073400C, is the monitor failing closed on an access it does not take, a '
        'finding to read; inside the panel range (section 2) it is not M1\'s expected end (section 5a).',
        f'RUNG 6 NOT PASSED, (b): a restart of the Surface before the report, no report (a page that stops at MON CALL, a REFUSED line, MON PREPARE FAILED, a fault page of q1n1), the panel still dark '
        f'{wait_s} seconds after the MON CALL line, or any other end that none of the results above names, and no panic text: a restart is NOT A RESULT for M1 (section 5a) and NOT PASSED for rung 6, '
        'which has not reached its aim.')


def section_5d(panel, wait_s):
    """Section 5d of the record of rung 6, after 5c, in the operator's words. `wait_s` is the silence rule's wait for the box (the guide's silence_s)."""
    box_s = panel['run']['box_s']
    new = (f'WHAT IS NEW. Rung 5\'s guest with three changes (section 0): XNU\'s wait for the CPU that never comes is {r6.CPU_WAIT_S} s (the command line is {r6.LINE}: six CPUs, no cpus=1), the box '
           f'is {box_s} s (D5\'\': one CPU loaded for up to {box_s} s, AC power REQUIRED, no temperature read; the operator chose it on 2026-11-19 and approving this text confirms it) and the SNAPREP page prints the guest '
           'timer\'s words (section 5b). The monitor ACKNOWLEDGES the guest\'s CPU-start stores (EXPERIMENTAL; not a model of the hardware: NO CPU IS STARTED and the three stores are dropped: '
           'section 3c), let_panic is set so that the kernel\'s own panic text prints, and the panel, serial=2 and the three store classes are rung 5\'s.')
    expect = (f'WHAT TO EXPECT. As in boots 21, 24 and 25: from the MON CALL line the panel is cleared and fills with the kernel\'s log, and the SNAPREP report page appears on its right when the guest '
              f'has stopped: about {r6.PANIC_AT_S + 1} s after the MON CALL line if the kernel panics as predicted below, at the end of the box ({box_s} s) if it does not, sooner on another stop. In boot 25 the '
              'log stopped growing about 35 s after the MON CALL line and stood still for the rest of its box: that is expected again and is not a stop; only the report page (about '
              f'{r6.PANIC_AT_S + 1} to {box_s} s) or, if it does not come, the {wait_s} s rule of section 5c ends the wait. Between about 60 s and the panic the log may show lines of the kernel\'s other '
              f'timed waits ({OTHER_WAITS}): they are NOT A FAILURE and not a result of their own. A dark panel is expected for part of the time (the kernel clears it); it is a restart only if the '
              f'Shell\'s countdown or the runner\'s first lines come back. {r2.force_off(wait_s)}. {r2.COUNT.format(wait=wait_s)}')
    predicts = ('WHAT THE RECORD PREDICTS, AND WHAT FALSIFIES IT (INFERRED; no probabilities; none of it has been seen with this word; read on the host in the kernelcache and the monitor\'s sources, a '
                f'file and not a run, and in the public source). PREDICTION: the guest runs as in boot 25, the PMGR driver and the CPU-start stores pass and the log goes as far as it went, and, because '
                f'the guest\'s timers are delivered (the reading of section 0a), XNU\'s CPU-boot thread ends its wait for CPU 1 about {r6.PANIC_AT_S} s after the MON CALL line (the wait starts about 2.3 '
                f's in) and the kernel panics with "{TIMER_TEXT}"; nested panics and boot 24\'s end ({STOP24} at a read in the chip-reset path) follow within about a second, so the call returns about '
                f'{r6.PANIC_AT_S + 1} s in and the page\'s MON TIM line has IRQS and VECTORS above 0. That text is the proof that a guest timer interrupt reaches the kernel, and the only one that '
                'the film can give. FALSIFIERS: silence to the end of the box with no panic text (the reading that the guest\'s timers are delivered, or the start of the wait at about 2.3 s, is '
                'wrong: the classes of RUNG 6 WATCHDOG, NO TEXT say which, from the page); a panic text "cpu 1 failed to boot ... waited N seconds" with N other than '
                f'{r6.CPU_WAIT_S} (the word was not honoured); the ApplePMGR assertion again (the topology reading of rung 5 was wrong; it held in boot 25); a MON STOP S2_FAULT with {r5.WINDOW} '
                f'{r5.FALSIFIER_ACK}, a READ of that block being new and unexpected, since nothing after the write reads. A stop at a device that has no model, or a panic of another driver, before '
                f'the {r6.PANIC_AT_S} s mark ends the run before the question is asked: progress, and a new question.')
    filming = (f'THE FILM is rung 5\'s, from the Shell\'s countdown until {r5.FILM_AFTER_S} seconds after the SNAPREP report page has appeared, or, if it never appears, until the FORCE OFF of step 5 of '
               f'section 5c ({wait_s} s after the MON CALL line) has been filmed (about {r5.film_total_s(box_s)} s in all for this box): the whole panel with the main camera, 30 frames a second or more, steady, and if there is a second camera '
               'the TOP-LEFT quarter close up (step 2 of section 5c). It must show PAYLOAD CALL 0 (only the film shows it once the kernel has cleared the panel: if it does not, the approval is '
               'spent), the kernel\'s log with its time, any panic text and the report with MON STOP, MON EXIT and the new MON lines. THE PANIC TEXT, if there is one, is the result and may scroll off '
               'the part of the panel left of the report page (about its left 55 percent) (INFERRED): nobody touches the keyboard, the screen or the buttons while it runs (except FORCE OFF as step 5 of '
               f'section 5c says). After those {r5.FILM_AFTER_S} seconds, and BEFORE FORCE OFF (only when the SNAPREP page is on the screen; if it is not, FORCE OFF {wait_s} seconds after the MON '
               'CALL line as step 5 says, no stills first), take three sharp still photographs of the part of the panel left of the report page, close and without glare, one of its top band, one of '
               'its middle and one of its bottom, and a fourth of the whole report page (it has up to 40 lines now and the guest timer\'s words decide the result of a silent run: asked, not '
               'required, and valuable). Lowercase letters on the panel are the kernel\'s (boots 21, 23, 24 and 25: OBSERVED once each).')
    page = ('WHAT THE PAGE SHOWS. The first five events and the last stop, as before (the monitor keeps at most the first 512 events: section 5b), and, new in this record, the guest timer\'s words and '
            'the monitor\'s counters from its report window: MON TIM, MON CLK, MON CNT, MON ACK, MON EP and, at a stop, MON GX, MON ESR and MON WVAL (legend in section 5b). They are raw counts of the monitor: '
            'a value is not a finding by itself, and the classes below are the only readings that this record asks for. MON ACK is how many acknowledged stores the monitor took and the first '
            'eight (section 3c has the prediction, INFERRED), and no result rests on it. The MON CON lines hold the first lines of console that reach the monitor\'s ring: boot 24\'s carried '
            'the first lines of its panic text (OBSERVED once), boots 21 and 23 had none; the rest of a panic text is on the panel only (INFERRED).')
    decides = ('WHAT THE FILM AND THE PAGE DECIDE (what this record changes has not been seen; boots 21, 24 and 25 are the comparison, OBSERVED once each). First the panel: the coordinator transcribes '
               'the first lines of any panic text from the film and the stills, and the classes below read from it. Then the kernel\'s log, replayed from its start by the first frames after the MON '
               f'CALL line: boot 21\'s had {r3.LOG_CPU0} early and a line {r3.LOG_CPUN} for each of the processors 1 to 5 ({r3.LOG_T}); with six CPUs they are expected again and are not the failure. '
               f'Then the report page: boot 24\'s stop was {STOP24} after its panic, boot 25\'s was {STOP25} at the end of its box (SPSR {r6.BOOT25_SPSR}: EL1 with every interrupt masked, idle INFERRED), and the '
               f'CPU-start stop is a MON STOP S2_FAULT with {r5.WINDOW} (a read there, MON ESR saying WNR 0, would be new and unexpected). An S2_FAULT at another address, or a run longer than boot '
               f'25\'s log, is progress. How far the log got, its last line and the time of its last change are results. If the film shows any of the lines {OTHER_LINES} and the page says VECTORS 0, '
               f'that disagreement is itself a result to report (UNKNOWN which of the two is wrong). NOT THE FAILURE: {NOT_THE_FAILURE}.')
    panic = ('WHAT A PANIC LOOKS LIKE (boot 24 showed one, OBSERVED once; the rest is INFERRED from XNU\'s panic format): a line that contains "panic(cpu" (the kernel may put its timestamp in front) '
             'and a caller address, the panic string (an assertion names its file and line), "Nested panic detected - entry count" lines when it panics again, the kexts of its backtrace and, at the '
             'end, "Nested panic count exceeds limit 5, machine will reset or spin" and "wdog: panic chipreset". The word "panic" alone is not one (the RTKit lines "General panic behavior" and the '
             'early line "debug_log_init: Error!! gPanicBase is still not initialized", OBSERVED twice in boot 23\'s replay, are not a panic). A call that the monitor refuses after the panic entry '
             'would stop the run before the text (MON STOP UNNAMED: section 3c). Either way, film it. The panic of the CPU that never comes reads "cpu 1 failed to boot for the first time, waited N '
             f'seconds" (INFERRED): with the word at {r6.CPU_WAIT_S} it is the TIMER PANIC below and is expected about {r6.PANIC_AT_S} s in; with 120 it could not come inside rung 5\'s box.')
    return (heading(box_s), *(line for text in (new, expect, predicts, filming, page, decides, panic, *results_paragraphs(panel['run'], wait_s)) for line in m1t.wrap(text, 3, 6)))


# ---- the legend of the reporter's lines, for section 5b -------------------------------------------------------------------------------------------------------------------------------
LEGEND = (
    '- MON STOP <kind> PC= A= B= and, on a line of its own, MON STOP C=0X...: when C= would pass column 100 of the runner (boot 25: a kernel address of 16 digits after a long head lost its last two '
    'digits).',
    '- MON CAPTURE WORD=0X.. IS NO CLASS (status zero only) takes the place of MON EXIT and the registers when the capture word is not 0 and not 1 to 3: a window of another build or a damaged '
    'one, which the page says and does not read as a capture; a window that took none prints no MON EXIT, MON GX or MON TIM.',
    '- MON ESR EC= ISV= SIZE= SRT= WNR= S1PTW= DFSC= (only after MON STOP S2_FAULT): the fields of the stop\'s syndrome, the B= of MON STOP: EC and DFSC in hex, the others in decimal, NA where the '
    'bits mean something else. EC is ESR[31:26]: 0X24 a data abort and 0X20 an instruction abort from the guest\'s EL1 (any other EC is no guest stage-2 fault: every field but EC is NA); ISV 1 says '
    'that the syndrome describes the access; SIZE is the access size in bytes (1, 2, 4 or 8; NA when ISV is clear), so that a store of a W register reads SIZE=4; SRT is the guest register (31 the '
    'zero register); WNR 1 is a store and 0 a read; S1PTW 1 is a fault on the guest\'s own stage-1 table walk; DFSC is the fault status (0X4 to 0X7 a translation fault at level 0 to 3, 0XC to 0XF '
    'a permission fault).',
    '- MON WVAL X<n>= DATA= (only for a stage-2 fault that is a store the instruction made): the guest register that SRT names (XZR for 31, which is zero) whole, and DATA= its low SIZE bytes '
    '(INFERRED: the data the access carried).',
    '- MON GX0-3 to MON GX28-SP (eight lines, only with a capture): x0 to x30 and the selected SP, four to a line in 0X hex; the last line is x28, x29, x30, SP (INFERRED: the guest\'s at the '
    'instruction that MON EXIT PC= names).',
    '- MON CLK READS= AFTER= FIRST= LAST=: READS is the guest clock reads that the monitor answered; FIRST is the host counter at the first sample that the clock backend took for any caller; LAST is '
    'the host counter at the report writer\'s own last sample, taken when the report was written; AFTER is LAST minus the wrapper\'s counter before the call, the ticks (those of MON RET TICKS=) from '
    'the call\'s start to the report (NA when there was no sample or it lies outside the call). LAST and AFTER are the monitor\'s report-time sample: they are NOT the guest\'s last use of the clock, '
    'and a small AFTER does not say that the backend was idle.',
    '- MON TIM IRQS= ASSERTS= CLEARS= VECTORS= CTL= CVAL= (only with a capture) and, when that line is full, a second line WRITES= IRQPC= READS= (and MORE=<n> for what does not fit the page: with '
    'text in the console ring and C= on a line of its own the page keeps one line for the timer, and CVAL, WRITES, IRQPC and READS can be counted in MORE, which leaves (b)\'s time reading, the split '
    'of (c) from (d) and the WRITES of (e) UNKNOWN): the monitor\'s guest-timer counters at the stop. IRQS is the monitor\'s own EL2 timer interrupts (INTID 26) taken, whatever deadline they served '
    '(alone they say nothing about a guest deadline); '
    'ASSERTS and CLEARS are the times HCR_EL2.VF was set and cleared for the guest; VECTORS is the entries into the guest\'s FIQ vector with VF set; CTL is the guest timer\'s control (1 enabled, '
    '2 masked, 4 ISTATUS: 0X5 is a deadline that has passed, unmasked; this kernel writes the control only with 1 (four writes in the kernelcache, none of another value) and re-arms with the '
    'compare interval: read on the host, a file and not a run; so a page shows 0X1 or 0X5, and 0X0 only before its timer set-up); CVAL is the guest\'s compare value; WRITES and READS are the '
    'guest\'s accesses to the timer registers; IRQPC is ELR_EL2 at '
    'the last host tick. Every field is a counter or a register that the monitor kept, and no unit has printed one yet; what they mean is INFERRED from the monitor\'s sources (section 5d reads '
    'them), and the page cannot say how long the guest sat in WFI.',
    '- MON CNT MAPS= MAPFAIL= DISP= PANIC= UNDEF= MAPOUT= LOGDROP= and, on the next line, CONDROP= RNGDROP= MAPDEV= MMIORD= MMIOWR= UARTTX=: the monitor\'s counters (SPTM map calls and failures, calls '
    'refused as no endpoint call, panic-entry calls served, instructions stepped over, mappings outside memory, log events dropped; console bytes dropped, mapping spans dropped, device mappings, reads '
    'and writes of the UART window, bytes the guest wrote to it). A count says what the monitor did, and what it means is the coordinator\'s.',
    '- MON ACK COUNT=<n> <offset>=<0Xvalue> ... [MORE=<m>] (only from the acknowledging monitor): COUNT is every 32-bit store that the guest made to PMGR\'s CPU-start block that the monitor took '
    'and ignored (no CPU was started); then the first eight as OFFSET=VALUE (offsets 4, 8 and 12 are the global, the E-cluster and the P-cluster masks; VALUE is the 32 bits written); MORE is the '
    'stores not listed. COUNT=0 is a block with no store; no line at all is a monitor without the block, a window that is not status OK or a full page (a stop at a stage-2 store with text in the '
    'console ring leaves no line for it); MON ACK LAYOUT=0X.. NOT READ names a block whose '
    'layout is not the one that the reporter reads. Section 3c has the prediction for COUNT (INFERRED); no class of section 5d reads it.',
    '- MON EP TOTAL= OTHER= and <endpoint>:<calls> for every bucket that is not zero, ascending: the SPTM calls by endpoint number, which the coordinator holds the names of. A page of 40 lines leaves '
    'out first the lines that the record does not name (MON EV 1 to 4, MON CON after the first, this histogram): that is not a finding.',
)


def page_legend(m1, box_s, real=None):
    """The lines that the guide's section 5b gets after MON EXIT for this record: the legend of the reporter's window lines (LEGEND), in the order of the page, each a bullet."""
    return tuple(line for text in LEGEND for line in m1t.wrap(text, 3, 5))
