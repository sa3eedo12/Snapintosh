# SPDX-License-Identifier: MIT
"""Section 5d of the record of rung 7 (docs/SP11-STICK-MONITOR.md section 20, K15; docs/SP11-M1-GUEST-PROFILES.md section 5), in the operator's words: the aim, what is new, what to expect,
what the record predicts and what falsifies it, what to film, what the page shows, what the film and the page decide, what a panic looks like and what counts as a result; and the legend of the
lines that the reporter of this record prints (the guide's section 5b gets it by the hook `page`). The rest of the record's words for this rung (sections 0, 0a, 0b, 3c and the steps of 5c) are
tools/sp11_stick_bundle_panel_rung7.py's, which this module takes its constants from; its `section_5d` reaches this module.

Rung 6's section 5d asked whether a guest timer interrupt reaches the kernel; boot 26 answered it (OBSERVED once), so the timer question is not asked again here: a MON STOP WATCHDOG page with
VECTORS above 0 is the normal silent end of a waiting kernel. The aim of this rung is where the quiet guest ends. The results are read in order and a film is in the first class that fits (PANIC
TEXT, NOT A RESULT, NOT PASSED (a), NEW STOP, KERNEL WAITING, NOT PASSED (b)): the classes are exclusive and leave no film without one. Every time-dependent sentence takes the box from the
record (`box_s`, and `wait_s`, the silence rule's wait for it).

Evidence: EXPERIMENTAL, hardware NOT RUN."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_bundle_record_m1 as m1t  # noqa: E402
import sp11_stick_bundle_panel_rung2 as r2  # noqa: E402
import sp11_stick_bundle_panel_rung3 as r3  # noqa: E402
import sp11_stick_bundle_panel_rung5 as r5  # noqa: E402
import sp11_stick_bundle_panel_rung6 as r6  # noqa: E402
import sp11_stick_bundle_panel_rung6d as d6  # noqa: E402
import sp11_stick_bundle_panel_rung7 as r7  # noqa: E402
import sp11_stick_bundle_windows as win  # noqa: E402

STOP24 = d6.STOP24
STOP25 = d6.STOP25
STOP26 = f'MON STOP S2_FAULT PC={r2.pc_text(r7.BOOT26_STOP["pc"])} A={r3.page_hex(r7.BOOT26_STOP["a"])} B={r3.page_hex(r7.BOOT26_STOP["b"])}'
VISIBLE_AFTER_BURST = ('the lines of the kext starter\'s load requests ("Load request for com.apple.nke.pptp failed", the line that boot 26 showed cut off) and, about 1 to 2 s after the bsd_init burst, '
                       '"Waiting on <dict>" (IOLog, so visible: INFERRED from IOFindBSDRoot, public IOKitBSDInit-12377.cpp:896, read on the host), and then a SILENT loop of 60 s waits for the root device '
                       '(no "Still waiting" line: it is compiled out; "!BSD" appears only on a 30 s timeout of the IOBSD wait, which is not expected)')
NOT_THE_FAILURE = ('the noise lines of section 0a (the SPMI parity-error lines and the PMU and RTKit lines OBSERVED once in boots 21, 23, 25 and 26, the AFKFirmwareService line in boot 21, and the USB "failed to '
                   'find" lines of boots 25 and 26), the silence of about 29 s between the USB lines and the bsd_init block (boot 26: OBSERVED once), the cut-off last line of boot 26, "Load request for '
                   'com.apple.nke.pptp failed: <err>" (INFERRED: the kernelcache has no pptp; harmless), "Couldn\'t alloc class AFKResource", and "memorystatus: System is unhealthy" with wired_mem_exhaustion '
                   'and "no victim found (action: 10)" (the 384 MiB guest, 8,838 free pages of 16 KiB: a warning for a kernel-only run, no panic path read on the host; a blocker later, when launchd needs '
                   'memory: the A16 chain ran 8192 MiB, the minimum for launchd is UNKNOWN, 2 GiB is a guess); INFERRED to come from the monitor\'s empty models and the small guest, which the film shows '
                   'before any panic text')


def heading(box_s):
    """The heading of section 5d: it names the aim, the wait and the box of this record."""
    return f'5d. Rung 7: the aim, what is new, what to film, what counts as a result (quiet guest, wait of {r7.CPU_WAIT_S} s, box of {box_s} s)'


def withdrawn(panel):
    """The nodes that the profile withdraws, as the builder counted them: (base board's count, this guest's count, the difference)."""
    base, now = panel['rung7']['diff']['nodes']
    return base, now, base - now


def new_text(panel):
    """WHAT IS NEW: rung 6's form, with the two changes that are in GUEST.DAT and nothing else."""
    base, now, gone = withdrawn(panel)
    return (f'WHAT IS NEW beyond rung 6 (record {r7.BOOT26_RECORD}, boot 26: section 0a). The monitor, its pair (SNAPMON-M1-ACK.BIN and SNAPREP-M1-ACK.BIN: the reporter v3 of rung 6), the box '
            f'({panel["run"]["box_s"]} s: D5\'\', which the operator chose in the app on 2026-11-19 for rung 6 and approving this text confirms for this record; one CPU loaded, AC power REQUIRED, no temperature '
            'read), let_panic, the panel, serial=2 and the three store classes are rung 6\'s: the acknowledge is EXPERIMENTAL and not a model of the hardware, NO CPU IS STARTED and the three stores '
            'are dropped (section 3c). Two things change, both in GUEST.DAT only. THE GUEST\'S DEVICE TREE IS THE PROFILE quiet: '
            f'{base} nodes become {now}, so that the video decoder, the scaler, the ProRes engine and nine audio roots are withdrawn with the nodes under them ({gone} in all), never faked (EXPERIMENTAL: section 3c). '
            f'THE COMMAND LINE ENDS WITH {r7.WORD} (rung 6\'s had 90): XNU\'s own wait for the CPU that never comes would end about {r7.CPU_PANIC_AT_S} s after the MON CALL line, outside the box (INFERRED).')


def aim_text(panel):
    """THE AIM: the two questions of this rung, and the reading of a silent end that does not ask the timer question again."""
    _, _, gone = withdrawn(panel)
    return (f'THE AIM. Two questions, and one film answers both. (1) Does withdrawing the {gone} nodes move the stop beyond the scaler? Boot 26 ended at the scaler\'s window, A={r3.page_hex(r7.BOOT26_STOP["a"])} '
            '(section 0a); in this guest\'s tree the scaler, the video decoder and the ProRes engine are not there to be attached. (2) Where does the run end now: at which device, at a wait with a '
            'silent log to the end of the box, or at a panic text? The guest\'s timers are known to work (boot 26: VECTORS=969, OBSERVED once), so a MON STOP WATCHDOG page with VECTORS above 0 is the '
            'normal silent end of a waiting kernel and is read for the last line of its log; the timer question is not asked again.')


def expect_text(panel, wait_s):
    """WHAT TO EXPECT: boot 26's course, which this guest is expected to follow until the nodes that it lacks would have been probed."""
    box_s = panel['run']['box_s']
    return ('WHAT TO EXPECT. As in boots 21, 24, 25 and 26: from the MON CALL line the panel is cleared and fills with the kernel\'s log, and the SNAPREP report page appears on its right when the guest '
            f'has stopped: at the end of the box ({box_s} s) if the kernel waits, sooner on another stop. In boot 26 the log stopped growing about 31 s after the MON CALL line, stood still for about 28.5 s and '
            'filled with the lines of bsd_init at about 60 s (mbinit, dlil, the ifnet attaches, memorystatus, apfs, NFS, ASP and L2TP): that silence and those lines are expected again and are not a stop; '
            f'only the report page (at the box end, or sooner) or, if it does not come, the {wait_s} s rule of section 5c ends the wait. After the burst the lines to expect are {VISIBLE_AFTER_BURST}: '
            'they are NOT A FAILURE and not a result of their own. The messages of the two 30 s waits for the RTC resource ("RTC did not show up" and the line with "getGMTTimeOfDay") are kprintf lines '
            'that go to the monitor\'s ring and not to the panel: the film cannot show them (INFERRED; this corrects rung 6\'s text). A dark panel is expected for part of the time (the kernel clears it); '
            f'it is a restart only if the Shell\'s countdown or the runner\'s first lines come back. {r2.force_off(wait_s)}. {r2.COUNT.format(wait=wait_s)}')


def predicts_text(panel):
    """WHAT THE RECORD PREDICTS, AND WHAT FALSIFIES IT, for the quiet guest after boot 26."""
    box_s = panel['run']['box_s']
    return ('WHAT THE RECORD PREDICTS, AND WHAT FALSIFIES IT (INFERRED; no probabilities; none of it has been seen with this guest; read on the host in the kernelcache, the guest\'s device tree and '
            'the public source, a file and not a run: files/stopsite-r6.md Q9 and files/stopsite-r8.md of the session, and boot 26). PREDICTION: the kernel goes as in boot 26 through its first 31 s of '
            'log (the SPMI, PMU and USB lines; the first of the two 30 s RTC waits of bsd_init is hidden by that noise), is silent for about 29 s (the second RTC wait, which times out: the RTC resource is '
            'never published) and runs the bsd_init burst at about 60.4 s after the MON CALL line; the quiet tree changes nothing on that path. The waiters of IOSurfaceRoot are then released, as in boot 26, but '
            'the scaler, the video decoder and the ProRes engine are not in this guest\'s tree, so none of the three stops the run. The first rows that remain are the four JPEG nodes '
            '(AppleJPEGHal::checkHardwareVersion: an S2_FAULT at their first window is possible, INFERRED low to medium; withdrawing them needs a new pin and is not in this record), then AVE and the display '
            'chain (the display driver waits for the DCP expert, which never answers: a WAIT, INFERRED). THE EXPECTED CLEAN END: MON STOP WATCHDOG at the end of the box '
            f'({box_s} s after the call), with the last text on the panel "Waiting on <dict>", which appears about 62 s in (INFERRED); the first 60 s timeout of the root device\'s loop is at about 122 s, just '
            'outside the box, and wdt=-1 keeps its "Still waiting" recovery off (INFERRED). THE OTHER ENDS: a MON STOP S2_FAULT in the JPEG, AVE or display windows, which names the next row; or a PANIC '
            'TEXT, a driver that met a withdrawn node (whether a withdrawal turns a wait into a panic is UNKNOWN). A page with VECTORS above 0 and MON STOP WATCHDOG is the normal end. XNU\'s own panic for the '
            'CPU that never comes, "cpu 1 failed to boot for the first time", would come about 2.3 s after the call plus the word, about '
            f'{r7.CPU_PANIC_AT_S} s, after the box by a margin of about 2 s (INFERRED; the wait blocks only the CPU-boot thread and three driver starts, nothing on the path to the root device): it is not '
            'expected inside the box, and one there would mean that the margin was wrong (class RUNG 7 PANIC TEXT). FALSIFIERS: a MON STOP S2_FAULT with A= in the window of a node that the profile withdrew (THE '
            f'PROFILE DID NOT TAKE: {win.for_falsifiers()}, or another window that the coordinator names from the {withdrawn(panel)[2]} nodes of section 3c); '
            f'A={r3.page_hex(r7.BOOT26_STOP["a"])} again; a MON STOP S2_FAULT with {r5.WINDOW} {r5.FALSIFIER_ACK}, a READ of that block being new and unexpected; the ApplePMGR assertion again (the '
            'topology reading of rung 5 was wrong; it held in boots 25 and 26); a "cpu 1 failed to boot" panic text before the end of the box. A stop at a device that has no model, or a panic of another '
            'driver, is progress and a new question, not a falsifier.')


def filming_text(panel, wait_s):
    """THE FILM: rung 5's and rung 6's, from the Shell's countdown until the report page has been on the screen for r5.FILM_AFTER_S seconds, or the FORCE OFF of step 5 if it never appears."""
    box_s = panel['run']['box_s']
    return (f'THE FILM is rung 5\'s, from the Shell\'s countdown until {r5.FILM_AFTER_S} seconds after the SNAPREP report page has appeared, or, if it never appears, until the FORCE OFF of step 5 of '
            f'section 5c ({wait_s} s after the MON CALL line) has been filmed (about {r5.film_total_s(box_s)} s in all for this box): the whole panel with the main camera, 30 frames a second or more, steady, and if '
            'there is a second camera the TOP-LEFT quarter close up (step 2 of section 5c). It must show PAYLOAD CALL 0 (only the film shows it once the kernel has cleared the panel: if it does not, the '
            'approval is spent), the kernel\'s log with its time (THE LAST LINE OF THE LOG AND THE TIME OF ITS LAST CHANGE are a result in every class that has no panic text), any panic text and the report '
            'with MON STOP, MON EXIT and the MON lines. THE PANIC TEXT, if there is one, is the result and may scroll off the part of the panel left of the report page (about its left 55 percent) '
            '(INFERRED): nobody touches the keyboard, the screen or the buttons while it runs (except FORCE OFF as step 5 of section 5c says). After those '
            f'{r5.FILM_AFTER_S} seconds, and BEFORE FORCE OFF (only when the SNAPREP page is on the screen; if it is not, FORCE OFF {wait_s} seconds after the MON CALL line as step 5 says, no stills first), '
            'take three sharp still photographs of the part of the panel left of the report page, close and without glare, one of its top band, one of its middle and one of its bottom, and a fourth of the '
            'whole report page (it has up to 40 lines and its MON TIM, MON CNT and MON ACK lines are read with the film: asked, not required, and valuable). Lowercase letters on the panel are the kernel\'s '
            '(boots 21, 23, 24, 25 and 26: OBSERVED once each).')


def page_text():
    """WHAT THE PAGE SHOWS: rung 6's reporter, which boot 26 ran."""
    return ('WHAT THE PAGE SHOWS. The first five events and the last stop, as before (the monitor keeps at most the first 512 events: section 5b), and the guest timer\'s words and the monitor\'s counters '
            'from its report window, which rung 6\'s reporter (this record\'s too) prints and boot 26\'s page showed: MON TIM, MON CLK, MON CNT, MON ACK, MON EP and, at a stop, MON GX, MON ESR and MON WVAL '
            '(legend in section 5b). They are raw counts of the monitor: a value is not a finding by itself, and the classes below are the only readings that this record asks for. MON ACK is how many '
            'acknowledged stores the monitor took and the first eight (boot 26: COUNT=15, OBSERVED once), and no result rests on it. The MON CON lines hold the first lines of console that reach the '
            'monitor\'s ring: boot 24\'s carried the first lines of its panic text (OBSERVED once), boots 21 and 23 had none; the rest of a panic text is on the panel only (INFERRED).')


def decides_text():
    """WHAT THE FILM AND THE PAGE DECIDE: the panel, the log to its last line, then the page, with boots 21 to 26 as the comparison."""
    return ('WHAT THE FILM AND THE PAGE DECIDE (what this record changes has not been seen; boots 21, 24, 25 and 26 are the comparison, OBSERVED once each). First the panel: the coordinator transcribes '
            'the first lines of any panic text from the film and the stills, and the classes below read from it. Then the kernel\'s log, replayed from its start by the first frames after the MON CALL line '
            f'and read to its LAST LINE and the time of that line on the film\'s clock: boot 21\'s had {r3.LOG_CPU0} early and a line {r3.LOG_CPUN} for each of the processors 1 to 5 ({r3.LOG_T}); with six '
            'CPUs they are expected again and are not the failure. Boot 26\'s last line was cut off after "request for" (INFERRED: the kext starter\'s "Load request for com.apple.nke.pptp failed"), at the end of the bsd_init block, about '
            f'60.4 s after the MON CALL line; a log that goes beyond it (to "Waiting on <dict>", expected about 62 s in), or a run longer than boot 26\'s {r7.BOOT26_S} s, is progress. Then the report page: '
            f'boot 24\'s stop was {STOP24} after its panic, boot 25\'s was {STOP25} at the end of its box (SPSR '
            f'{r6.BOOT25_SPSR}: EL1 with every interrupt masked, idle INFERRED), boot 26\'s was {STOP26} (a write at the scaler\'s window), and the CPU-start stop is a MON STOP S2_FAULT with {r5.WINDOW} (a '
            'read there, MON ESR saying WNR 0, would be new and unexpected). An S2_FAULT at another address is progress. A line "RTC did not show up", one with "getGMTTimeOfDay" or "!BSD" on the film would '
            'contradict the host reading that the first two are kprintf lines of the monitor\'s ring and that IOBSD is published at about 61 s: a finding to report (UNKNOWN which of the two is wrong). '
            f'NOT THE FAILURE: {NOT_THE_FAILURE}.')


def panic_text():
    """WHAT A PANIC LOOKS LIKE: boot 24's panic and the one that this record's word puts outside the box."""
    return ('WHAT A PANIC LOOKS LIKE (boot 24 showed one, OBSERVED once; the rest is INFERRED from XNU\'s panic format): a line that contains "panic(cpu" (the kernel may put its timestamp in front) '
            'and a caller address, the panic string (an assertion names its file and line), "Nested panic detected - entry count" lines when it panics again, the kexts of its backtrace and, at the '
            'end, "Nested panic count exceeds limit 5, machine will reset or spin" and "wdog: panic chipreset". The word "panic" alone is not one (the RTKit lines "General panic behavior" and the '
            'early line "debug_log_init: Error!! gPanicBase is still not initialized", OBSERVED twice in boot 23\'s replay, are not a panic). A call that the monitor refuses after the panic entry '
            'would stop the run before the text (MON STOP UNNAMED: section 3c). Either way, film it. The panic of the CPU that never comes reads "cpu 1 failed to boot for the first time, waited N '
            f'seconds" (INFERRED): with the word at {r7.CPU_WAIT_S} it is expected about {r7.CPU_PANIC_AT_S} s in, outside this record\'s box by a margin of about 2 s; a text of that kind inside the box '
            'means that the margin was wrong and is the class RUNG 7 PANIC TEXT below.')


def results_paragraphs(run, wait_s):
    """The results of rung 7, one paragraph each, in the operator's words, read in this order from the film of either camera, the stills and the page: the first class that fits is the film's."""
    box_s = run['box_s']
    return (
        'THE RESULTS. M1\'s own verdict is section 5a\'s and is reported beside rung 7\'s. The panel is read first, then the stop, and for a stop at the end of the box the last line of the log and the '
        'time of its last change; a film is in the first class below that fits, in this order, and every film fits one. The aim of this rung (THE AIM above) is progress beyond boot 26\'s stop and the '
        'place where the quiet guest ends: an end that names a new device, a wait or a panic text is that answer and is read for what it is. NOT A RESULT and the two NOT PASSED classes are the '
        'failures: nothing is learnt, or the record did not reach its aim.',
        'RUNG 7 PANIC TEXT: the panel shows a panic text, whatever the page says and whatever the stop is: nested panics and boot 24\'s end, MON STOP S2_FAULT at a read in the kernel\'s chip-reset path, '
        'follow within about a second and are a later stop and not a result of their own. THE TEXT IS THE RESULT: the coordinator transcribes its first lines verbatim from the film and the stills '
        f'and hands them back. It is also a falsifier if it is the ApplePMGR assertion of boot 24 again, or "cpu 1 failed to boot for the first time, waited N seconds" inside the box (it is expected '
        f'about {r7.CPU_PANIC_AT_S} s in, with N={r7.CPU_WAIT_S}). A panic of a driver at a later point is progress: it names where the quiet guest goes.',
        'RUNG 7 NOT A RESULT: a film that does not let the coordinator read the panel for text or read MON STOP (too soft, too dark, the report unreadable under the kernel\'s text, the text too small '
        'to read): nothing is concluded from it, and the one-boot rule of section 5c says whether the approval is spent.',
        f'RUNG 7 NOT PASSED, (a): no panic text and MON STOP S2_FAULT with {r5.WINDOW} again: the acknowledge did not take. Either the access is one that the monitor does not take by design (a '
        'read, another width, a syndrome that does not describe it: section 3c) or it is a fault of the monitor: MON ESR and MON WVAL on the page and B= are the syndrome, and the coordinator '
        'decodes it before saying which. A read there would be new and unexpected: nothing after the write reads (section 3c).',
        'RUNG 7 NEW STOP: no panic text and a MON STOP of any kind but WATCHDOG (S2_FAULT, UNNAMED, PANIC_ENTRY, SERROR, DEVICE_RESET, GUEST_EXC or another name) that is not the S2_FAULT of the class '
        'above: its kind, PC=, A=, the page\'s MON ESR, MON WVAL and MON GX lines and the last line of the log with its time are the result, and the coordinator reads them first, with the MON TIM line. '
        'Two readings of an S2_FAULT are named. THE PROFILE DID NOT TAKE: A= lies in '
        f'{win.for_new_stop()}: the kernel attached to a node that this tree no longer lists, or the withdrawal is not what the '
        'builder read back, which is a finding about the profile and not progress. THE STOP MOVED: an S2_FAULT with A= anywhere else, in the window of a device that was not withdrawn (the coordinator '
        'names it from A= and PC= and the guest\'s tree; the likeliest are the four JPEG nodes, then AVE and the display chain: INFERRED): progress, beyond boot 26\'s stop, and a new question, which model or '
        'withdrawal that device needs (UNKNOWN). An S2_FAULT elsewhere in the '
        'CPU-start block, outside 0X300734004..0X30073400C, is the monitor failing closed on an access it does not take, a finding to read; inside the panel range (section 2) it is not M1\'s expected '
        'end (section 5a). A stop of another kind is a finding to read, with the last line of the log.',
        f'RUNG 7 KERNEL WAITING: MON STOP WATCHDOG at the end of the {box_s} s box and no panic text. THE RESULT IS THE LAST LINE OF THE LOG AND ITS TIME ON THE FILM\'S CLOCK, which the coordinator reads '
        'from the film and the stills: the last line that the kernel printed before its silence, and when (the log may be silent for the last tens of seconds, as in boots 25 and 26), and what the '
        'kernel is waiting for is said from that line and the host analysis (INFERRED: the expected last text is "Waiting on <dict>", which appears about 62 s in, and the root device\'s loop of 60 s '
        'waits that follows prints nothing, its first timeout being at about 122 s, just outside the box). The page\'s MON TIM line is read beside it and does not ask the timer question again (timers are delivered: INFERRED from boot 26\'s VECTORS=969, OBSERVED once): VECTORS above 0, as in boot 26, '
        'is the normal page of a waiting kernel; CTL=0X1 and the distance of CVAL from LAST of the MON CLK line (CVAL less 1.25 times LAST, in ticks of 24 MHz: INFERRED) say where the kernel\'s armed '
        'deadline lies, and whether it lies beyond the box. A page with VECTORS 0 would contradict boot 26 and is itself a finding to report (UNKNOWN whether the monitor\'s accounting or the guest). '
        'The MON ACK, MON CNT and MON CON lines are findings to read, not classes. A log that went beyond boot 26\'s last line (the cut-off load request at the end of the bsd_init block) is progress; a log '
        'that stands at the same place is not.',
        f'RUNG 7 NOT PASSED, (b): a restart of the Surface before the report, no report (a page that stops at MON CALL, a REFUSED line, MON PREPARE FAILED, a fault page of q1n1), the panel still dark '
        f'{wait_s} seconds after the MON CALL line, or any other end that none of the results above names, and no panic text: a restart is NOT A RESULT for M1 (section 5a) and NOT PASSED for rung 7, '
        'which has not reached its aim.')


def section_5d(panel, wait_s):
    """Section 5d of the record of rung 7, after 5c, in the operator's words. `wait_s` is the silence rule's wait for the box (the guide's silence_s)."""
    box_s = panel['run']['box_s']
    texts = (new_text(panel), aim_text(panel), expect_text(panel, wait_s), predicts_text(panel), filming_text(panel, wait_s), page_text(), decides_text(), panic_text(),
             *results_paragraphs(panel['run'], wait_s))
    return (heading(box_s), *(line for text in texts for line in m1t.wrap(text, 3, 6)))


# ---- the legend of the reporter's lines, for section 5b ---------------------------------------------------------------------------------------------------------------------------
TIM_LEGEND = (
    '- MON TIM IRQS= ASSERTS= CLEARS= VECTORS= CTL= CVAL= (only with a capture) and, when that line is full, a second line WRITES= IRQPC= READS= (and MORE=<n> for what does not fit the page: with '
    'text in the console ring and C= on a line of its own the page keeps one line for the timer, and CVAL, WRITES, IRQPC and READS can be counted in MORE): the monitor\'s guest-timer counters at '
    'the stop. IRQS is the monitor\'s own EL2 timer interrupts (INTID 26) taken, whatever deadline they served (alone they say nothing about a guest deadline); ASSERTS and CLEARS are the times '
    'HCR_EL2.VF was set and cleared for the guest; VECTORS is the entries into the guest\'s FIQ vector with VF set; CTL is the guest timer\'s control (1 enabled, 2 masked, 4 ISTATUS: 0X5 is a '
    'deadline that has passed, unmasked; this kernel writes the control only with 1 and re-arms with the compare interval: read on the host, a file and not a run; so a page shows 0X1 or 0X5, and 0X0 '
    'only before its timer set-up); CVAL is the guest\'s compare value; WRITES and READS are the guest\'s accesses to the timer registers; IRQPC is ELR_EL2 at the last host tick. Boot 26 printed '
    f'them: {r7.BOOT26_TIM[8:]} in {r7.BOOT26_S} s (OBSERVED once), so the guest\'s timer interrupts are delivered and VECTORS above 0 is the normal page of a kernel that is waiting; CVAL less 1.25 '
    'times LAST of the MON CLK line, in ticks of 24 MHz, places the armed deadline in time (INFERRED: boot 26\'s lay about 9.6 ms ahead); the page cannot say how long the guest sat in WFI.',
)
LEGEND = tuple(TIM_LEGEND[0] if item.startswith('- MON TIM ') else item for item in d6.LEGEND)
assert sum(item.startswith('- MON TIM ') for item in d6.LEGEND) == 1 and len(LEGEND) == len(d6.LEGEND), 'the legend of rung 6 has changed under rung 7'


def page_legend(m1, box_s, real=None):
    """The lines that the guide's section 5b gets after MON EXIT for this record: the legend of the reporter's window lines (LEGEND), in the order of the page, each a bullet."""
    return tuple(line for text in LEGEND for line in m1t.wrap(text, 3, 5))
