# SPDX-License-Identifier: MIT
"""Section 5d of the record of rung 8 (docs/SP11-STICK-MONITOR.md section 20, K16; docs/SP11-M1-GUEST-PROFILES.md section 7), in the operator's words: the aim, what is new, what to expect, what the
record predicts and what falsifies it, what to film, what the page shows, what the film and the page decide, what a panic looks like and what counts as a result; the legend of the reporter's lines is
rung 7's (tools/sp11_stick_bundle_panel_rung7d.py page_legend). The rest of the record's words for this rung are tools/sp11_stick_bundle_panel_rung8.py's, which this module takes its constants from;
its `section_5d` reaches this module.

Rung 7's section 5d asked where the quiet guest ends; boot 27 answered it (a wait for a root device, OBSERVED once). The aim of this rung is ONE question: does binding the PMU's real-time clock let the
clock driver register the IORTC resource, so that bsd_init does not wait 60 s for it (INFERRED)? The results are read in order and a film is in the first class that fits (PANIC TEXT, NOT A RESULT,
NOT PASSED (a), NEW STOP, THE CLOCK DID NOT TAKE, KERNEL WAITING, NOT PASSED (b)): the classes are exclusive and leave no film without one. Every time-dependent sentence takes the box from the record
(`box_s`, and `wait_s`, the silence rule's wait for it).

Evidence: EXPERIMENTAL, hardware NOT RUN."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_bundle_record_m1 as m1t  # noqa: E402
import sp11_stick_bundle_panel_rung2 as r2  # noqa: E402
import sp11_stick_bundle_panel_rung3 as r3  # noqa: E402
import sp11_stick_bundle_panel_rung5 as r5  # noqa: E402
import sp11_stick_bundle_panel_rung7d as d7  # noqa: E402
import sp11_stick_bundle_panel_rung8 as r8  # noqa: E402
import sp11_stick_bundle_rung8 as r8b  # noqa: E402
import sp11_stick_bundle_windows as win  # noqa: E402

page_legend = d7.page_legend
STOP27 = f'MON STOP WATCHDOG PC={r2.pc_text(r8.BOOT27_STOP["pc"])} A={r3.page_hex(r8.BOOT27_STOP["a"])} B={r3.page_hex(r8.BOOT27_STOP["b"])}'
CLOCK_ADDRESSES = 'f704 (count 4, parity_exp=0x000F), f715 (count 2) or f802 (count 6)'
START_LINES = '`AppleDialogSPMIPMU::start: Primary PMU detected`, `AppleARMRTC started!`, `AppleDialogSPMIPMURTC started!` and `Read RTC offset from leg_scrpad`'
BURST = 'the bsd_init block (mbinit, dlil_init, the ifnet attaches, memorystatus, apfs, NFS, ASP, L2TP) and its last lines, "Load request for com.apple.nke.pptp failed", `IOMFB: service matched: AppleDCPExpert`, `Couldn\'t alloc class "AFKResource"` and "Waiting on <dict>" for the root device'
NOT_THE_FAILURE = ('the parity-error lines of the registers that the clock model does not serve (the PMU\'s SOCD header at 8800, count 5, parity_exp=0x001F; the scratch pad at f700; the 56-byte read) and '
                   'the PMU and RTKit lines, "Couldn\'t alloc class AFKResource" and the AFKFirmwareService line, the USB "failed to find" lines (boots 21 to 27: OBSERVED once each), '
                   '"Load request for com.apple.nke.pptp failed: <err>" (INFERRED: the kernelcache has no pptp; harmless), and "memorystatus: System is unhealthy" with wired_mem_exhaustion and '
                   '"no victim found (action: 10)" (9,094 free pages of 16 KiB in boot 27: a warning for a kernel-only run, no panic path read on the host; a blocker later, when launchd needs memory: '
                   'the minimum for launchd is UNKNOWN); INFERRED to come from the monitor\'s empty models and the small guest, which the film shows before any panic text')


def heading(box_s):
    """The heading of section 5d: it names the aim, the wait and the box of this record."""
    return f'5d. Rung 8: the aim, what is new, what to film, what counts as a result (quiet guest with the PMU\'s clock, wait of {r8.CPU_WAIT_S} s, box of {box_s} s)'


def clock_window(panel):
    """The window of the clock model, as the builder read it from the PARAMS chunk: first byte and one past the last (0X308700000..0X308718000)."""
    d = panel['rung8']['diff']
    return f'0X{r8b.CLOCK_BASE:X}..0X{r8b.CLOCK_BASE + d["model_span"]:X}'


def withdrawn(panel):
    """The nodes that the profile withdraws, as the builder counted them: (base board's count, this guest's count, the difference)."""
    base, now = panel['rung8']['diff']['nodes']
    return base, now, base - now


def new_text(panel):
    """WHAT IS NEW: rung 7's form, with the one change that is in GUEST.DAT and nothing else."""
    base, now, _ = withdrawn(panel)
    return (f'WHAT IS NEW beyond rung 7 (record {r8.BOOT27_RECORD}, boot 27: section 0a). The monitor, its pair (SNAPMON-M1-ACK.BIN and SNAPREP-M1-ACK.BIN: the reporter v3), the box '
            f'({panel["run"]["box_s"]} s: D5\'\', which the operator chose in the app on 2026-11-19 for rung 6 and approving this text confirms for this record; one CPU loaded, AC power REQUIRED, no '
            'temperature read), let_panic, the panel, serial=2, the three store classes, the command line (cpu_boot_timeout_secs=120) and the device tree (the profile quiet\'s: '
            f'{now} of {base} nodes) are rung 7\'s: the acknowledge is EXPERIMENTAL and not a model of the hardware, NO CPU IS STARTED and the three stores are dropped (section 3c). ONE THING '
            'CHANGES, IN GUEST.DAT ONLY: THE PMU\'S REAL-TIME CLOCK IS BOUND at nub-spmi0 (the guest profile quiet-rtc): three words of one of the 120 device models of PARAMS (the kind, 9 to 48, the seed '
            'and the contract word), which make the controller of the PMU answer the clock driver\'s reads of its counter and its two offset fields as a clock and no longer as an empty bus '
            '(EXPERIMENTAL: section 3c).')


def aim_text(panel):
    """THE AIM: the one question of this rung, with the three facts of the film that answer it."""
    return ('THE AIM. One question, and one film answers it: does binding the PMU\'s clock let AppleARMRTC register the IORTC resource (INFERRED)? Boot 27\'s film shows the clock driver\'s first '
            'read of the PMU failing its parity check four times in a row (address f704, 4 bytes, parity_rcv=0x0000, parity_exp=0x000F; about a thousand lines in all, with the other failing reads), and the bsd_init burst at about 60 s, after the two 30 s '
            'waits for the resource that the driver never publishes (section 0a). With the clock bound the host reading predicts three things, which the coordinator reads from the film: (1) the '
            'line `AppleARMRTC registering service!` about 7 s after the MON CALL line; (2) no parity error at f704, f715 or f802 (the noise at 8800, the scratch pad and the 56-byte read stays); (3) the '
            'bsd_init burst and "Waiting on <dict>" within about 10 s of the MON CALL line, not at about 60 s. A second question comes with it: where the kernel ends when bsd_init no longer waits '
            '(at the same wait for a root device, which is the expected end, at a device that has no model, or at a panic text). The guest\'s timers are known to work (boots 26 and 27: VECTORS above 0), '
            'so a MON STOP WATCHDOG page with VECTORS above 0 is the normal silent end of a waiting kernel and the timer question is not asked again.')


def expect_text(panel, wait_s):
    """WHAT TO EXPECT: boot 27's course for the first seconds, and the two courses after them."""
    box_s = panel['run']['box_s']
    return ('WHAT TO EXPECT. As in boots 21 and 24 to 27: from the MON CALL line the panel is cleared and fills with the kernel\'s log, and the SNAPREP report page appears on its right when the guest '
            f'has stopped: at the end of the box ({box_s} s) if the kernel waits, sooner on another stop. The first seconds are boot 27\'s: the SPMI parity-error lines of the registers that the clock '
            f'does not serve, the PMU lines and, about 7 s in, the clock driver\'s start lines ({START_LINES}). Then the two courses part: with the clock bound the log is expected to go on with the '
            f'clock driver\'s `tick read!` line and `registering service!` and, within about 10 s of the MON CALL line, {BURST}, and then to stand still (INFERRED), the root device\'s silent loop of 60 s waits '
            'having its first timeout at about 70 s (no "Still waiting" line: it is compiled out, INFERRED from the public source read on the host); without it, as in boot 27, the same lines come at '
            'about 60 s after a silence of about 29 s. The USB "failed to find" lines are expected at about 31 s in either way (INFERRED). All of it is NOT A FAILURE and not a result of its own. The messages of the two 30 s '
            'waits for the RTC resource ("RTC did not show up" and the line with "getGMTTimeOfDay") are kprintf lines that go to the monitor\'s ring and not to the panel: the film cannot show them '
            '(INFERRED; boot 27 showed none). A dark panel is expected for part of the time (the kernel clears it); '
            f'it is a restart only if the Shell\'s countdown or the runner\'s first lines come back. {r2.force_off(wait_s)}. {r2.COUNT.format(wait=wait_s)}')


def predicts_text(panel):
    """WHAT THE RECORD PREDICTS, AND WHAT FALSIFIES IT, for the quiet guest with the clock after boot 27."""
    box_s = panel['run']['box_s']
    return ('WHAT THE RECORD PREDICTS, AND WHAT FALSIFIES IT (INFERRED; no probabilities; none of it has been seen with this guest; read on the host in the kernelcache, the loader and the monitor\'s ELF, '
            'a file and not a run: files/spmi-rtc-note.md of the session, and boots 24 to 27). PREDICTION: the kernel goes as in boot 27 through its first 7 s; the clock driver then reads its '
            'two offset fields (4 bytes at f704, 2 at f715), which the clock model answers with the parity flag and zero data, logs `tick read!` and `registering service!` and publishes IORTC '
            '(the counter at f802 is read with the first read of the time); bsd_init\'s two waits for IORTC end at once and its burst comes within about 10 s of the MON CALL line and not at about 60 s; '
            'the rest of the course is boot 27\'s, earlier: the waiters of IOSurfaceRoot are released with the burst, and in boot 27 none of them (the JPEG, AVE and display drivers) stopped the run in '
            f'the 60 s after it (OBSERVED once). THE EXPECTED CLEAN END: MON STOP WATCHDOG at the end of the box ({box_s} s after the call), with "Waiting on <dict>" as the last text, which now appears '
            'within about 10 s of the call and stands for the rest of the box, the root device\'s loop of 60 s waits having its first timeout at about 70 s, silent (INFERRED), and wdt=-1 keeping its '
            '"Still waiting" recovery off. THE OTHER ENDS: a MON STOP UNNAMED with B= in the clock\'s window (the model refused an access), a MON STOP S2_FAULT in the window of a device that the earlier release or the calendar now reaches, or a PANIC TEXT '
            '(for example of a driver that meets the placeholder date: UNKNOWN). XNU\'s own panic for the CPU that never comes, "cpu 1 failed to boot for the first time", would come about 2.3 s '
            f'after the call plus the word, about {r8.CPU_PANIC_AT_S} s, after the box by a margin of about 2 s (INFERRED; it blocks only the CPU-boot thread and three driver starts): it is not expected '
            'inside the box, and one there would mean that the margin was wrong (class RUNG 8 PANIC TEXT). FALSIFIERS: no `registering service!` line on any frame from the MON CALL line to 20 s after it '
            '(a film is not proof of absence: the second witness is the burst, which comes at about 60 s if the clock did not take); a parity error at ' + CLOCK_ADDRESSES + ' after the clock\'s start '
            f'lines; a MON STOP UNNAMED with B= in {clock_window(panel)}, the clock model\'s window (the model refused an access: A= is 0X30 and eight more hex digits); ANY NEW STOP LATER, that is '
            'a MON STOP of any kind but WATCHDOG or a panic text: it falsifies the clean end and is read by its class below, as progress and a new question; A=0X291000004 again or a window of a '
            'withdrawn node (the profile did not take: it did in boot 27 and the tree is the same); a MON STOP S2_FAULT with '
            f'{r5.WINDOW} {r5.FALSIFIER_ACK}; the ApplePMGR assertion again. A `registering service!` line with the burst still at about 60 s would contradict the reading that IORTC was the only '
            'thing that the two waits were for: INFERRED, and named here as what it would be, a finding about bsd_init.')


def filming_text(panel, wait_s):
    """THE FILM: rung 7's, with the clock's lines and the burst's time among what it must show."""
    return r8.patched(d7.filming_text(panel, wait_s),
                      ('the kernel\'s log with its time (THE LAST LINE OF THE LOG AND THE TIME OF ITS LAST CHANGE are a result in every class that has no panic text)',
                       'the kernel\'s log with its time, frame by frame from the MON CALL line to 20 s after it (the clock driver\'s lines about 7 s in and the time of the bsd_init burst are '
                       'results in every class that has no panic text, and so are THE LAST LINE OF THE LOG AND THE TIME OF ITS LAST CHANGE)'),
                      ('(boots 21, 23, 24, 25 and 26: OBSERVED once each)', '(boots 21, 23, 24, 25, 26 and 27: OBSERVED once each)'))


def decides_text():
    """WHAT THE FILM AND THE PAGE DECIDE: the panel, the log to its last line with the clock's questions, then the page, with boots 21 and 24 to 27 as the comparison."""
    return ('WHAT THE FILM AND THE PAGE DECIDE (what this record changes has not been seen; boots 21 and 24 to 27 are the comparison, OBSERVED once each). First the panel: the coordinator transcribes '
            'the first lines of any panic text from the film and the stills, and the classes below read from it. Then the kernel\'s log, replayed from its start by the first frames after the MON CALL '
            'line and read to its LAST LINE and the time of that line on the film\'s clock, with THE CLOCK QUESTIONS answered from it, each with its time: (1) are the clock driver\'s start lines '
            f'there about 7 s in ({START_LINES}); (2) is `AppleARMRTC registering service!` there, and when; (3) is there a parity error at {CLOCK_ADDRESSES} after those lines (the other families '
            'are expected: NOT THE FAILURE); (4) when does the bsd_init burst come (boot 27: at about 60 s; predicted within about 10 s) and what is the last line and when did it appear (boot 27: '
            f'"Waiting on <dict>" at about 60 s). With six CPUs the processor lines of boot 21\'s log are expected again and are not the failure. Then the report page: '
            f'boot 25\'s stop was {d7.STOP25} at the end of its box, boot 26\'s was {d7.STOP26} (a write at the scaler\'s window), boot 27\'s was {STOP27} '
            f'at the end of its box (SPSR {r8.BOOT27_SPSR}: EL1 with every interrupt masked, idle INFERRED), and the CPU-start stop is a MON STOP S2_FAULT with {r5.WINDOW} (a read there, MON ESR '
            'saying WNR 0, would be new and unexpected). An S2_FAULT at another address is progress, and a MON STOP UNNAMED with B= in the clock\'s window is a finding about the clock model. A line "RTC did not show up", one '
            'with "getGMTTimeOfDay" or "!BSD" on the film would contradict the host reading that the first two are kprintf lines of the monitor\'s ring and that IOBSD is published when bsd_init has run '
            '(boot 27 showed none): a finding to report (UNKNOWN which is wrong). NOT THE FAILURE: ' + NOT_THE_FAILURE + '.')


def panic_text():
    """WHAT A PANIC LOOKS LIKE: rung 7's words, with the class of this record."""
    return r8.patched(d7.panic_text(), ('is the class RUNG 7 PANIC TEXT below', 'is the class RUNG 8 PANIC TEXT below'))


def results_paragraphs(panel, run, wait_s):
    """The results of rung 8, one paragraph each, in the operator's words, read in this order from the film of either camera, the stills and the page: the first class that fits is the film's."""
    box_s = run['box_s']
    return (
        'THE RESULTS. M1\'s own verdict is section 5a\'s and is reported beside rung 8\'s. The panel is read first, then the stop, and for a stop at the end of the box the last line of the log and the '
        'time of its last change; a film is in the first class below that fits, in this order, and every film fits one. The aim of this rung (THE AIM above) is the answer to ONE question, whether '
        'binding the PMU\'s clock let the driver register IORTC: THE CLOCK DID NOT TAKE and KERNEL WAITING read it, and the two facts that the film shows of the clock (the `registering service!` line '
        'and the parity errors at f704, f715 and f802) are reported beside every class. A panic text or a stop of any kind is read for what it is. NOT A RESULT and the two NOT PASSED classes '
        'are the failures: nothing is learnt, or the record did not reach its aim.',
        'RUNG 8 PANIC TEXT: the panel shows a panic text, whatever the page says and whatever the stop is: nested panics and boot 24\'s end, MON STOP S2_FAULT at a read in the kernel\'s chip-reset path, '
        'follow within about a second and are a later stop and not a result of their own. THE TEXT IS THE RESULT: the coordinator transcribes its first lines verbatim from the film and the stills '
        f'and hands them back. It is also a falsifier if it is the ApplePMGR assertion of boot 24 again, or "cpu 1 failed to boot for the first time, waited N seconds" inside the box (it is expected '
        f'about {r8.CPU_PANIC_AT_S} s in, with N={r8.CPU_WAIT_S}). A panic of a driver at a later point is progress: it names where the guest goes with the clock, and one that names the RTC, the calendar '
        'or the time is a finding about the clock and its placeholder date.',
        'RUNG 8 NOT A RESULT: a film that does not let the coordinator read the panel for text or read MON STOP (too soft, too dark, the report unreadable under the kernel\'s text, the text too small '
        'to read), or read the clock driver\'s lines and the time of the bsd_init burst (the log must be readable frame by frame from the MON CALL line to 20 s after it): nothing is concluded from '
        'it, and the one-boot rule of section 5c says whether the approval is spent.',
        f'RUNG 8 NOT PASSED, (a): no panic text and MON STOP S2_FAULT with {r5.WINDOW} again: the acknowledge did not take. Either the access is one that the monitor does not take by design (a '
        'read, another width, a syndrome that does not describe it: section 3c) or it is a fault of the monitor: MON ESR and MON WVAL on the page and B= are the syndrome, and the coordinator '
        'decodes it before saying which. A read there would be new and unexpected: nothing after the write reads (section 3c).',
        'RUNG 8 NEW STOP: no panic text and a MON STOP of any kind but WATCHDOG (S2_FAULT, UNNAMED, PANIC_ENTRY, SERROR, DEVICE_RESET, GUEST_EXC or another name) that is not the S2_FAULT of the class '
        'above: its kind, PC=, A=, the page\'s MON ESR, MON WVAL and MON GX lines and the last line of the log with its time are the result, and the coordinator reads them first, with the MON TIM '
        'line and the clock\'s lines. Two readings of an S2_FAULT and one of an UNNAMED stop are named. THE CLOCK MODEL REFUSED AN ACCESS: MON STOP UNNAMED with B= in '
        f'{clock_window(panel)}, the window of the clock model, and A= 0X30 and eight more hex digits, the last of which is the device result (INFERRED, read on the host in probes/xnuboot/mmio_dispatch.c: '
        'the reporter does not name a device model\'s refusal): the guest touched a register or a width that the model does not take, or the model was not started (UNKNOWN which): a finding about the model and a '
        'falsifier of the prediction, read with the clock\'s lines on the film. THE PROFILE DID NOT TAKE: A= lies in '
        f'{win.for_new_stop()}: the kernel attached to a node that this tree no longer lists, or the withdrawal is not what the builder read back; boot 27 ran this tree for 120 s without such a stop '
        '(OBSERVED once), so it would be new, a finding about the profile and not progress. THE STOP MOVED: an S2_FAULT with A= anywhere else, in the window of a device that was not withdrawn (the '
        'coordinator names it from A= and PC= and the guest\'s tree; the likeliest are the four JPEG nodes, then AVE and the display chain, which bsd_init now releases about 50 s earlier: INFERRED): '
        'progress, and a new question, which model or withdrawal that device needs (UNKNOWN). An S2_FAULT elsewhere in the CPU-start block, outside 0X300734004..0X30073400C, is the monitor failing '
        'closed on an access it does not take, a finding to read; inside the panel range (section 2) it is not M1\'s expected end (section 5a). A stop of another kind is a finding to read, with the '
        'last line of the log.',
        f'RUNG 8 THE CLOCK DID NOT TAKE: MON STOP WATCHDOG at the end of the {box_s} s box, no panic text, and the film shows what boot 27\'s showed of the clock: a parity error at {CLOCK_ADDRESSES} after the line "Read RTC '
        'offset from leg_scrpad", or no `AppleARMRTC registering service!` on any frame from the MON CALL line to 20 s after it together with the bsd_init burst at about 60 s or later. THE RESULT IS '
        'THAT THE PREDICTION IS FALSIFIED: the coordinator reads which of the clock facts failed and transcribes the first parity lines verbatim, and says what it can of the cause from the page '
        '(the model may not be bound, may refuse the reads, or the parity rule may need more than the flag: UNKNOWN, and the host reading of the check is INFERRED). The page is read as KERNEL WAITING '
        'reads it. A film in which the line cannot be found but the burst is early and no parity error at those addresses is on it is not this class: the line may have scrolled past (INFERRED), and '
        'the clock took.',
        f'RUNG 8 KERNEL WAITING: MON STOP WATCHDOG at the end of the {box_s} s box, no panic text, and the clock took (the class above does not fit). THE RESULTS ARE, each with its time on the film\'s '
        'clock: (1) THE CLOCK\'S ANSWERS, whether `AppleARMRTC registering service!` is on the film and when (predicted about 7 s in) and that no parity error at f704, f715 or f802 is, with the other '
        'parity families still there (INFERRED); (2) WHEN THE BSD_INIT BURST CAME (predicted within about 10 s; boot 27: about 60 s); (3) THE LAST LINE OF THE LOG AND THE TIME OF ITS LAST CHANGE '
        '(predicted: "Waiting on <dict>" for an IOMedia of content Apple_HFS, within about 10 s, and then silence to the box\'s end, the root device\'s first 60 s timeout falling at about 70 s with '
        'no line); and (4) what the kernel is waiting for, said from that line and the host analysis (INFERRED). The page\'s MON TIM line is read beside it and does not ask the timer question again '
        '(VECTORS above 0 is the normal page of a waiting kernel, as in boots 26 and 27); CVAL less 1.25 times LAST of the MON CLK line, in ticks of 24 MHz, places the kernel\'s armed deadline '
        '(INFERRED). A page with VECTORS 0 would contradict boots 26 and 27 and is itself a finding. The MON ACK, MON CNT and MON CON lines are findings to read, not classes. A burst that came at '
        'about 60 s with the line present would contradict the reading that IORTC alone ended the two 30 s waits (INFERRED): a finding about bsd_init. A line after "Waiting on <dict>" that boot 27 '
        'did not show, for example at about 70 s, is progress.',
        f'RUNG 8 NOT PASSED, (b): a restart of the Surface before the report, no report (a page that stops at MON CALL, a REFUSED line, MON PREPARE FAILED, a fault page of q1n1), the panel still dark '
        f'{wait_s} seconds after the MON CALL line, or any other end that none of the results above names, and no panic text: a restart is NOT A RESULT for M1 (section 5a) and NOT PASSED for rung 8, '
        'which has not reached its aim.')


def section_5d(panel, wait_s):
    """Section 5d of the record of rung 8, after 5c, in the operator's words. `wait_s` is the silence rule's wait for the box (the guide's silence_s)."""
    box_s = panel['run']['box_s']
    texts = (new_text(panel), aim_text(panel), expect_text(panel, wait_s), predicts_text(panel), filming_text(panel, wait_s), d7.page_text(), decides_text(), panic_text(),
             *results_paragraphs(panel, panel['run'], wait_s))
    return (heading(box_s), *(line for text in texts for line in m1t.wrap(text, 3, 6)))
