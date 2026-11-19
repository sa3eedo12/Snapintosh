# SPDX-License-Identifier: MIT
"""Rung 7 of the monitor entry run (docs/SP11-STICK-MONITOR.md section 20, K15; docs/SP11-M1-GUEST-PROFILES.md section 5): the record for the boot after rung 6. Boot 26 (rung 6: the CPU-start
stores acknowledged, six CPUs, cpu_boot_timeout_secs=90, let_panic, a 120 s box, reporter v3) answered the question that it asked, that guest timer interrupts are delivered, took the kernel
through its long silent wait into bsd_init and stopped it at 61.60 s as MON STOP S2_FAULT at A=0X291000004, a 32-bit write at the scaler's window, a device that no model of the monitor
covers. This record keeps the monitor, the acknowledge, let_panic, the panel, the six CPUs, the box and the pair of rung 6 and changes two things in GUEST.DAT only: the command line has
cpu_boot_timeout_secs=120 in place of 90, so that XNU's own wait for the CPU that never comes ends outside the box and cannot cut the run, and the guest's device tree is the profile `quiet`
(43 nodes withdrawn by data, among them the scaler, the video decoder and the ProRes engine; never faked).

`--cpu-start ack --let-panic --boot-arg cpu_boot_timeout_secs=120 --panel console --time-box-s 120 --guest-profile j700ap-26A428-m1-quiet --sptm-hvc-calls 0` is the one combination that builds it
(tools/sp11_stick_bundle_m1.py and ..._chunks.py: ACK_FORMS, FORM_PROFILE), with the pair of ACK_PAIRS (rung 6's); the guide's table PANEL takes its words by the key `console+cpu_boot_timeout_secs=120+
let_panic+j700ap-26A428-m1-quiet` (tools/sp11_stick_bundle_panel_rungs.py). The words that it shares with rung 6 are tools/sp11_stick_bundle_panel_rung6.py's, by attribute: a hook that this module
does not define is rung 6's, and where a sentence of rung 6's is kept with a change, `patched` asserts that it is in rung 6's words exactly once, so that a change of those words cannot go unnoticed
here. Section 5d and the legend of the page are tools/sp11_stick_bundle_panel_rung7d.py's; the profile's paragraphs are tools/sp11_stick_bundle_panel_profile.py's. Every sentence that says what this
guest is against rung 6's is made from the builder's own chunk comparison (tools/sp11_stick_bundle_rung7.py, `compare`). Boot 26's result is a keyed block, RUNG6 below: the operator's film as the
coordinator read it (OBSERVED, one unit, one boot) and the host analysis of its stop (read on the host, a file and not a run; INFERRED where it says so).

Evidence: EXPERIMENTAL, hardware NOT RUN."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_bundle_chunks as ch  # noqa: E402
import sp11_stick_bundle_m1 as m1  # noqa: E402
import sp11_stick_bundle_record_m1 as m1t  # noqa: E402
import sp11_stick_bundle_panel_rung2 as r2  # noqa: E402
import sp11_stick_bundle_panel_rung3 as r3  # noqa: E402
import sp11_stick_bundle_panel_panic as rp  # noqa: E402
import sp11_stick_bundle_panel_rung5 as r5  # noqa: E402
import sp11_stick_bundle_panel_rung6 as r6  # noqa: E402
import sp11_stick_bundle_panel_profile as pp  # noqa: E402
import sp11_stick_bundle_windows as win  # noqa: E402

WORD = ch.RUNG7_FORM[1]                     # cpu_boot_timeout_secs=120: rung 5's word (m1.BOOT_ARGS); it goes with the acknowledging monitor
NUMBER = 7                                  # the rung's number, for the pointer to section 5d in the reading lines (tools/sp11_stick_bundle_panel.py)
PROFILE = ch.RUNG7_PROFILE                  # j700ap-26A428-m1-quiet
LINE = r5.LINE                              # the command line of rung 5 and of this record: the loader's with serial=2 and the word
CPU_WAIT_S = int(WORD.rpartition('=')[2])   # XNU's wait for the CPU that never comes, in s
BOX_S = m1.RUNG6_BOX_S                      # D5'''s 120 s, which rung 6 ran inside and this record asks again
CPU_PANIC_AT_S = 122                        # about 2.3 s (the start of the wait: boot 24's PMGR panic came at 2.25 s) plus CPU_WAIT_S (INFERRED): outside the box
BOOT26_RECORD = 'd9d79c87...5cda'           # the approved record of rung 6: SHA-256 d9d79c87cccc8c39f415fcee8a9cc900b8bb1bfe668fef9101c9fec9555a5cda
BOOT26_TICKS = 1_182_753_011                # MON RET TICKS= of boot 26, in ticks of 19.2 MHz (OBSERVED once)
BOOT26_S = f'{BOOT26_TICKS / 19.2e6:.2f}'
BOOT26_COUNTS = 'MON COUNTS EVENTS=512 TRAPS=177001 ABORTS=0 STEPS=1485 CALLS=66809 DROPPED=118428'
BOOT26_STOP = dict(pc=0xFFFFFE00095FF9AC, a=0x291000004, b=0x93880045, c=0xFFFFFE63AA8A0004)      # MON STOP S2_FAULT PC= A= B= C= of boot 26 (OBSERVED once)
BOOT26_SPSR = '0X80400204'                  # MON EXIT SPSR= of boot 26
BOOT26_ESR = 'MON ESR EC=0X24 ISV=1 SIZE=4 SRT=8 WNR=1 S1PTW=0 DFSC=0X5'
BOOT26_WVAL = 'MON WVAL X8=0X1 DATA=0X1'
BOOT26_TIM = 'MON TIM IRQS=1010 ASSERTS=2036 CLEARS=2036 VECTORS=969 CTL=0X1 CVAL=0X99C3A80F'
BOOT26_PAGE = ('PAYLOAD LOADED INDEX=0 BYTES=448463 SHA=BB34B9701D89302F, INDEX=1 BYTES=58634 SHA=533D004C27A11DA9, INDEX=2 BYTES=118908480 SHA=1DD2E9AC94FDB741, INDEX=3 BYTES=1024 '
               'SHA=9804989A2A727740, INDEX=4 BYTES=528384 SHA=2AF25E8AC647A885; SNAPMON STAGE=PIN_OK LABEL=M1RUN-ACKPANIC-RUNG6 CHUNKS=46')
BOOT26_MONITOR, BOOT26_REPORTER, BOOT26_GUEST, BOOT26_PIN = 'BB34B9701D89302F', '533D004C27A11DA9', '1DD2E9AC94FDB741', '9804989A2A727740'
BOOT26_LABEL = 'M1RUN-ACKPANIC-RUNG6'
BOOT25_REPORTER = r6.BOOT25_REPORTER        # rung 5's reporter, which boot 25 ran: not this record's
SCALER = f'IPA 0X291000004 lies in /device-tree/arm-io/scaler0 (compatible scaler,t8101, reg 0X{win.SCALER[0][0]:X} size 0X{win.SCALER[0][1] - win.SCALER[0][0]:X}; {win.for_boot26()})'
STAND_HEAD = '0a. Where things stand (boots 20, 21, 23 to 26; 2026-11-18/19; one unit, one boot each; OBSERVED unless INFERRED)'
HEADER = ('3c. The panel as the kernel\'s console with the CPU-start stores acknowledged, let_panic, a wait of 120 s and a quiet guest (rung 7: D9 panel handed, serial=2, cpu_boot_timeout_secs=120, '
          'let_panic set, guest profile quiet, stock kernel; docs/SP11-STICK-MONITOR.md section 20)')
PARAMS_ROW = r6.PARAMS_ROW
CITES = tuple(cite for cite in r6.CITES if cite[0] == r5.REPORTER)     # what this record cites of rung 6's table: the reporter's lines (rung 6 also cited the monitor's guest-timer path, in a paragraph that this record does not have)
VERDICTS = ('PANIC TEXT', 'WATCHDOG, NO TEXT', 'NEW STOP')      # the verdicts that this record's keyed blocks may carry: boots 24, 25 and 26


def patched(text, *pairs):
    """`text` (a string, or the lines of m1t.wrap) as one string with each (old, new) put in place once: AssertionError if `old` is not in it exactly once, so that a change of rung 6's
    words cannot go unnoticed by a sentence of rung 7 that was written against them."""
    flat = ' '.join(text.split()) if isinstance(text, str) else ' '.join(' '.join(text).split())
    for old, new in pairs:
        assert flat.count(old) == 1, f'rung 6\'s words have changed under rung 7: {old[:60]!r} is not in them exactly once'
        flat = flat.replace(old, new)
    return flat


# KEYED BLOCKS: the result of a run as the operator's film showed it and the coordinator read it (files/result-boot24.md, result-boot25.md and result-boot26.md of the session) and the host
# analysis of its stop (files/stopsite-r4.md, stopsite-r7.md and the decode of boot 26: read on the host from the kernelcache file, the guest's device tree and the public XNU source, a file and
# not a run). `record` is the run's approved record, `boot` its boot as the operator's log names it, `verdict` one of VERDICTS and `words` the paragraphs, each with its label. keyed_lines()
# holds them to the record's rules. Boot 24's block is rung 6's; boot 25's keeps rung 6's first three paragraphs and says in its last what boot 26 answered.
RUNG4 = r6.RUNG4
RUNG5 = dict(r6.RUNG5, words=(*r6.RUNG5['words'][:3], (
    'What boot 26 answered of that stop (OBSERVED once, one unit, one boot; section 0a): the guest\'s timer interrupts ARE delivered, so the silence of boot 25 was a kernel waiting and not a dead '
    'timer (INFERRED). Boot 26 ran the same acknowledge and went through a silence of about 28.5 s that ended at about 60.4 s of its own run, when bsd_init ran; boot 25\'s box of 60 s (TICKS give '
    '60.18 s) ended inside that wait, the second of two 30 s waits for an RTC resource that is never published (INFERRED from code read on the host: section 0a). The question that boot 25 left open, '
    'whether any guest timer interrupt was delivered, is closed.')))
RUNG6 = dict(record=BOOT26_RECORD, boot='boot 26', verdict='NEW STOP', words=(
    f'The call returned after TICKS={BOOT26_TICKS} at 19.2 MHz ({BOOT26_S} s; the box was {120 * 19_200_000:,} ticks, 120 s: the stop was the monitor\'s own, well inside it), {BOOT26_COUNTS}, then '
    f'MON STOP S2_FAULT PC={r2.pc_text(BOOT26_STOP["pc"])} A={r3.page_hex(BOOT26_STOP["a"])} B={r3.page_hex(BOOT26_STOP["b"])} C={r2.pc_text(BOOT26_STOP["c"])}, MON EXIT SPSR={BOOT26_SPSR}, '
    f'{BOOT26_ESR}, {BOOT26_WVAL}, with REGS RESTORED=YES and GIC RESTORE CLEAN=YES, on a clean report (OBSERVED once; the operator\'s film, read by the coordinator). M1 PASSED by section 5a (rows '
    f'a to g). The runner\'s page before the call showed every digit of the approved staging (OBSERVED once): {BOOT26_PAGE}.',
    'The log of the kernel filled the panel from the MON CALL line, as in boots 21, 24 and 25, and went further than in any earlier run (OBSERVED once): the SPMI and PMU lines with their failed '
    'register reads, then the USB lines "failed to find phyService" and "failed to find AppleUSBHostPort" about 31 s after the MON CALL line, then NO new line for about 28.5 s, and at about 60.4 s '
    'a block of about 40 lines at once, which is bsd_init running: ktriage_register_subsystem_strings, "mbinit: done", the new aio workqueue, dlil_init and the ifnet attaches of lo0, gif0 and stf0, '
    'memorystatus, the apfs, NFS, ASP and L2TP lines, and a last line cut off after "request for", which is INFERRED (high) to be "Load request for com.apple.nke.pptp failed: <err>" of the kext '
    'starter of the VPN protocols (it loads ppp, l2tp and pptp; the kernelcache has no pptp; harmless; the console had stopped mid-line at the stop). The report page followed at about 61.6 s. No '
    'panic text was anywhere in the film (OBSERVED once). The noise lines of boots 21, 23 and 25 were in it as before, and "Couldn\'t alloc class AFKResource", and "memorystatus: System is unhealthy" '
    'with wired_mem_exhaustion 1 (8,838 free pages of 16 KiB, about 138 MiB free: the kernel noticing the 384 MiB guest; "no victim found (action: 10)" and no panic path read on the host: a warning '
    'for a kernel-only run and a blocker later, when launchd and its daemons have nothing to evict; the A16 chain ran 8192 MiB, the minimum for launchd is UNKNOWN and 2 GiB is a guess, INFERRED): '
    'none of them is the failure.',
    'The decode of the stop, read on the host in the kernelcache and the guest\'s device tree (a file and not a run; the names are INFERRED from literals, there is no KDK): PC= is in '
    'com.apple.driver.AppleM2ScalerCSCDriver (the fileset entry whose segment holds it; HAL file AppleM2ScalerCSCHalMSR.cpp), in the SoC-version step of the scaler\'s HAL init, which ends with '
    '"Scaler HAL[n] Initialized": the instruction `str w8,[x0,#4]` after `mov w8,#1`, which follows a virtual call that returned x0 = 0XFFFFFE63AA8A0000 (a mapping of reg[0]), is a 32-bit write '
    'of 1 at offset 4 (x8=1, SRT=8, SIZE=4, WNR=1, FAR ...0004), and the same store of zero follows: ONE pulse, 1 and then 0 written to word 1 of reg[0], when the hardware version that the device '
    f'tree gives (22) is below 0X1A. It is not a loop (the function has no branch back; the first reading of this analysis as a loop is withdrawn). {SCALER} (found by walking the device tree '
    'through the arm-io ranges). No model of the monitor covers it, so the monitor fails closed: S2_FAULT, DFSC 5 = a translation fault at level 1. What the pulse does to the device is UNKNOWN. This '
    'is the stop that the host analysis ranked second (files/stopsite-r6.md of the session, Q9, rows 2 (scaler0) and 3 (apr), "STOP or WAIT", INFERRED low to medium); AVD, ranked first, was not touched before it.',
    'Why the scaler, and why at 61.6 s (read on the host; INFERRED where it says so): nine kexts (the scaler, AVD, AVE2, JPEG, ProRes, IOMFB, IOMFB-DCP, IOGPUFamily and the camera driver) wait for '
    'IOSurfaceRoot with no timeout, and IOSurfaceRoot\'s personality matches IOResourceMatch=IOBSD, which bsd_init publishes at about 61 s (INFERRED timing from code read on the host). The scaler, '
    'AVD, AVE2, JPEG, ProRes, IOMFB and IOMFB-DCP are released together (the order inside the release is UNKNOWN; the scaler first is OBSERVED). So what the film showed is where the other 51 or so '
    'nodes with unmodelled windows did NOT stop in 61.6 s (OBSERVED), and the waiters of IOSurfaceRoot start only now.',
    f'What the numbers say (INFERRED unless marked): {BOOT26_TIM} (OBSERVED once): the guest\'s timers work (the virtual FIQ line was raised and dropped 2,036 times and the guest entered its FIQ vector '
    '969 times in 61.6 s, and CVAL lies about 9.6 ms ahead of the clock: an armed deadline and not a stalled one), so the silence before bsd_init was a kernel waiting: the second of two 30 s waits in '
    'bsd_init for the RTC resource (IOKitInitializeTime, then the calendar\'s getUTCTimeOfDay: 0.4 + 30 + 30 = 60.4 s; INFERRED timing from code read on the host). The RTC resource is never '
    'published (AppleARMRTC\'s start stops before its "registering service" line (absent on the three frames read: OBSERVED once, and a film is not proof of absence), and the guest\'s device tree has no rtc node), so both waits end by timeout and no third RTC wait '
    'follows. The messages of those waits ("RTC did not show up") are kprintf lines, which go to the serial path, the monitor\'s ring, and not to the panel: the film cannot show them (INFERRED from '
    'the public source; this corrects the text of rung 6\'s record, which expected them on the film). MON ACK COUNT=15 (OBSERVED once): '
    'three stores for each of CPUs 1 to 5, which the acknowledge dropped, and nothing waited on those CPUs for 61 s; the wait of 90 s of that record was never reached.'))
BLOCKS = {'Rung 4': RUNG4, 'Rung 5': RUNG5, 'Rung 6': RUNG6}


def keyed_lines(block, what):
    """The paragraphs of section 0a that are one run's result: a keyed block. AssertionError for a verdict that this record is not for, and for a paragraph that breaks the record's rules (ASCII, no
    claim without a label, never the word that the project keeps for what an independent run showed)."""
    assert block['verdict'] in VERDICTS, f'the verdict of {what} is not one that rung 7 is the record for'
    for paragraph in block['words']:
        assert paragraph.isascii() and pp.NEVER not in paragraph, 'a paragraph of a keyed block is not ASCII or uses the word that M1 records never use'
        assert any(label in paragraph for label in ('OBSERVED', 'INFERRED', 'UNKNOWN')), 'a paragraph of a keyed block has no label'
    return (*m1t.wrap(f'{what} (record {block["record"]}, {block["boot"]}; one unit, one boot; the operator\'s film, read by the coordinator): {what.upper()} {block["verdict"]}.'),
            *(line for paragraph in block['words'] for line in m1t.wrap(paragraph)))


# ---- section 0 -------------------------------------------------------------------------------------------------------------------------------------------------------------------------
DECIDES = ('APPROVING THIS TEXT ALSO DECIDES THREE THINGS, THE FIRST OF WHICH THE OPERATOR HAS ALREADY CHOSEN IN THE APP FOR RUNG 6. (1) D5\'\': A TIME BOX OF 120 s. D5 and D8 decided 5 s and D5\' '
           'decided 60 s; for rung 6 the operator chose 120 s in the app on 2026-11-19 ("let\'s go with 120 seconds", the answer to the question whether its box should be 120 s or 60 s), rung 6 ran '
           'inside it (61.6 s of the 120: section 0a), and this record asks the same box, so that approving this text confirms it for this record: AC power REQUIRED, one CPU loaded for up to 120 s, no '
           'temperature readout, and the exposure sums of section 0b and section 4. Boot 25 left its CPU idle for the last 25 s of 60 and boot 26\'s log was silent for about 28.5 s of its 61.6 s '
           '(INFERRED: section 0a), so the extra time may be spent mostly idle. (2) A guest command line with cpu_boot_timeout_secs=120 where rung 6\'s had 90, so that XNU\'s own wait for the CPU that never comes, which would end about 122 s after the MON '
           'CALL line (INFERRED; the margin to the end of the box is about 2 s), ends outside the box and cannot cut the run; the guest is otherwise rung 6\'s, six CPUs included. (3) A GUEST DEVICE TREE WITH NODES WITHDRAWN: the profile quiet (the '
           'video decoder, the scaler, the ProRes engine and nine audio roots with the nodes under them; the counts are in section 3c), a virtual board description that is EXPERIMENTAL: the nodes are '
           'withdrawn from the tree, never faked, and nothing has run on the unit with them withdrawn. The monitor, its pair (SNAPMON-M1-ACK.BIN and the reporter v3, SNAPREP-M1-ACK.BIN, which are '
           'rung 6\'s byte for byte), the 120 device models and the PARAMS chunk are rung 6\'s: section 3c has the comparison that the builder makes. What the guest can reach is what rung 6\'s could, '
           'the existing stage-2 map and the monitor\'s models and report window, with no new register, no new system-register class and no new store class (read on the host in '
           'compat/cpu-start-ack.h, a file and not a run); a guest with nodes withdrawn reaches fewer devices and never more (INFERRED), and this approval is for that reach for up to the box. The '
           'acknowledging monitor, the panel as the console, the monitor\'s let_panic word and the timer reporter ran in boot 26 for 61.6 s; this tree, this word and a box that may be used up have not.')
RUNG = ('each of rungs 1 to 6, the first run with the panel withheld, the panel as the console (rung 2, boot 21), the same with cpus=1 (rung 3, boot 23), with cpus=1 and let_panic (rung 4, boot 24), '
        'with the CPU-start stores acknowledged, six CPUs and a 60 s box (rung 5, boot 25) and with a wait of 90 s, a box of 120 s and another reporter (rung 6, boot 26), whose records have their own '
        'approvals, spent: this record is rung 7, rung 6 with a wait of 120 s and the guest profile quiet, and no approval covers another\'s boot')
CONSOLE = ('The guest is given the panel (section 3c) and its command line is serial=2 with cpu_boot_timeout_secs=120, its device tree is the profile quiet\'s, its let_panic word is set and the '
           'monitor acknowledges its CPU-start stores: its console is the panel (boots 21, 24, 25 and 26: the kernel\'s log and panic text were on it, OBSERVED once each) and the MON CON lines are '
           'expected to carry at most the first lines of a panic text (boot 24: OBSERVED once; boots 21 and 23 had none); what the acknowledge, let_panic and the withdrawn nodes do beyond boot '
           '26\'s 61.6 s is INFERRED and has not been seen.')
RAN = 'ran in its acknowledging build twice, for 60 s (boot 25) and for 61.6 s (boot 26: section 0a), and in its plain build in boots 20, 21, 23 and 24.'
STOP_SEEN = {**r6.STOP_SEEN, 'S2_FAULT': 'OBSERVED three times (boots 21, 24 and 26, one unit, one boot each).'}
FAULT_SEEN = patched(r5.FAULT_SEEN, ('OBSERVED twice:', 'OBSERVED three times:'),
                     (f'and boot 24 at A={r3.page_hex(r5.BOOT24_STOP["a"])}, after its panic text', f'boot 24 at A={r3.page_hex(r5.BOOT24_STOP["a"])}, after its panic text, and boot 26 at '
                                                                                                     f'A={r3.page_hex(BOOT26_STOP["a"])}, a write at the scaler\'s window (section 0a)'))


def d5_second(box_s):
    return (f'D5\'\' (the operator chose {r6.BOX_S} s in the app on 2026-11-19, "let\'s go with 120 seconds", for rung 6\'s record, which ran inside it; this record asks the same box and approving this text '
            f'confirms it for this record; D5\', "Yes, up to 60 s" of 2026-11-18, covered rung 5 and is spent with it): the time box is {box_s} s, with AC power, one CPU running (the guest\'s tree lists six, '
            'and the acknowledge starts none) and no temperature readout (D8\')')


def why_lines(real):
    """Section 0's paragraph for this record: why rung 7, the two changes, the prediction in a line and where the rest is. Boot 26's facts are section 0a's (stand_extra)."""
    box_s = real['panel_facts']['run']['box_s'] if real.get('panel_facts', {}).get('run') else BOX_S
    diff = real['panel_facts']['rung7']['diff']
    return m1t.wrap(f'WHY RUNG 7. Rung 6 (boot 26, record {BOOT26_RECORD}) ran {BOOT26_S} s of its box of 120 s, answered the question that it asked (guest timer interrupts ARE delivered: INFERRED from VECTORS=969, OBSERVED once), took '
                    'the kernel through its long silent wait into bsd_init and ended on a NEW STOP: MON STOP S2_FAULT at '
                    f'A={r3.page_hex(BOOT26_STOP["a"])}, a 32-bit write in the window of the scaler, a device that no model of the monitor covers, so that the monitor failed closed (section 0a). '
                    'The host analysis ranked that device second behind the video decoder (files/stopsite-r6.md of the session, Q9; INFERRED). The guest profile quiet withdraws the scaler, the video '
                    'decoder, the ProRes engine and nine audio roots from the guest\'s device tree BY DATA, so that the kernel never attaches to them and never touches their windows (INFERRED: it is '
                    'the A16 route\'s own deferral, and it is EXPERIMENTAL for this unit). THIS RECORD CHANGES TWO THINGS FROM RUNG 6, IN GUEST.DAT ONLY. (1) THE GUEST\'S DEVICE TREE IS THE PROFILE quiet: '
                    f'THE BASE BOARD WITH {diff["nodes"][0] - diff["nodes"][1]} OF ITS {diff["nodes"][0]} NODES WITHDRAWN (the video decoder, the scaler, the ProRes engine and nine audio roots with the nodes '
                    f'under them), withdrawn and never faked (EXPERIMENTAL: section 3c). (2) THE COMMAND LINE HAS cpu_boot_timeout_secs={CPU_WAIT_S} IN PLACE OF 90: rung 6\'s 90 was chosen to ask the timer '
                    'question, which boot 26 has answered, and with it XNU\'s own panic for the CPU that never comes would fall at about 92 s and cut the run; with 120 it falls at about '
                    f'{CPU_PANIC_AT_S} s (INFERRED), outside the box by a margin of about 2 s. THE MONITOR, ITS PAIR, THE REPORTER, THE let_panic WORD, THE 120 MODELS AND THE PARAMS CHUNK ARE RUNG 6\'S, AND SO IS THE BOX: '
                    + d5_second(box_s) + '. THE AIM, THE PREDICTION, ITS FALSIFIERS AND WHAT THE FILM DECIDES are in section 5d, in the operator\'s words (INFERRED).')


def stand_extra(real):
    """The paragraphs that section 0a gets after boot 20's: boots 21 and 23 condensed to what this record still needs, and the keyed blocks of rung 4's, rung 5's and rung 6's results."""
    s = r3.BOOT21_STOP
    return (*m1t.wrap(f'Boot 21 (rung 2, record {r3.BOOT21_RECORD}; one unit, one boot; the operator\'s film) ran the panel as the kernel\'s console: the call returned after {r3.BOOT21_S} s as '
                      f'MON STOP S2_FAULT PC={r2.pc_text(s["pc"])} A={r3.page_hex(s["a"])} B={r3.page_hex(s["b"])}, INFERRED to be the kernel\'s first CPU-start write (compat/boot-relocate.h:147-153; '
                      f'section 3c). The panel showed the kernel\'s own boot log from its start, with a processor line for each of the six CPUs and no MON CON line (serial=2). The log is noisy, and the '
                      f'noise is not the failure: {r3.NOISE} were on the film (OBSERVED once: boot 21; INFERRED to come from the monitor\'s empty models). Rung 2 PASSED. Boot 23 (rung 3, record '
                      f'{rp.BOOT23_RECORD}) added cpus=1, which was honoured (OBSERVED once), and the call ended {rp.BOOT23_S} s in as MON STOP GUEST_EXC PC={r2.pc_text(rp.BOOT23_STOP["pc"])} '
                      f'A={r3.page_hex(rp.BOOT23_STOP["a"])}: XNU\'s own debugger-trap word, executed when the kernel panics (INFERRED, read on the host), so no text was printed. Boot 24 showed what '
                      'that panic was.'),
            *keyed_lines(RUNG4, 'Rung 4'),
            *keyed_lines(RUNG5, 'Rung 5'),
            *keyed_lines(RUNG6, 'Rung 6'),
            *m1t.wrap('The stick after boots 21, 23, 24, 25 and 26 was unchanged but for the Shell\'s marker (and, after boots 21, 23 and 24, the Mac\'s own .fseventsd entry), and REPORT.DAT kept its all-zero '
                      'staged digest: a run does not write the report back, so the page on the panel is its only record (OBSERVED once each; boot 26\'s by a read on the host of the stick as returned: its 18 '
                      'staged files byte-identical and the Shell\'s marker the only new file).'))


def hazard(real, box_s, wait_s):
    """The panel's bullet of 0b: the console on the panel is boots 21's, 24's, 25's and 26's; the path of the guest past the acknowledged stores is boot 26's for its first 61.6 s and unknown after."""
    return ('The guest can write the unit\'s framebuffer, which the display scans out, and the kernel draws its console there (OBSERVED once: boots 21, 24, 25 and 26): from the MON CALL line the panel '
            'is cleared and fills with the kernel\'s log (the kernel\'s console clears the whole panel when it takes it: public source, not this kernel\'s own), and its text can paint over the '
            'page, so that the report can be hard to read. The film is the only record of that text and of the lines printed before the guest ran (section 5d). The monitor acknowledges the '
            'guest\'s CPU-start stores (section 3c) and no CPU starts: in boot 26 the kernel went on past them for 61.6 s, through a silence of about 28.5 s (INFERRED: the second of two 30 s waits for an RTC '
            'resource that is never published) into bsd_init, '
            f'and stopped at a device that has no model (OBSERVED once); what it does in {box_s} s with the quiet tree is UNKNOWN (it may wait, stop at a later device, panic or spin). With let_panic set a '
            'panic runs the kernel\'s own panic path, which boot 24 showed printing its text (OBSERVED once) before the monitor stopped it at a device that has no model. The quiet tree lacks the '
            'withdrawn roots: a driver that waited for one of them may meet its missing node and panic instead of waiting (INFERRED), and whether a withdrawal turns a wait into a panic is UNKNOWN. '
            f'A box of {box_s} s with a CPU that is mostly idle after the first 35 s or so (INFERRED from boots 25 and 26) is the operator\'s decision D5\'\', made in the app on 2026-11-19 and '
            'confirmed by approving this text (section 0): the longest run so far was boot 26\'s 61.6 s, whose film showed no restart of the Surface to its end, 92 s (OBSERVED once), and a run of '
            f'{box_s} s is UNTESTED. AC power is REQUIRED, and the monitor ends the run at the box or at its own stop either way (section 5d). {r2.force_off(wait_s)}.')


def step4(guest, label):
    """5c, step 4: the programs' digits that tell this bundle from rung 6's (boot 26: GUEST.DAT, PIN.DAT and the label differ; SNAPMON.BIN and SNAPREP.BIN are the same), compared on the runner's lines."""
    return (f' The monitor\'s and the reporter\'s digits are rung 6\'s (boot 26 had INDEX=0 ... SHA={BOOT26_MONITOR} and INDEX=1 ... SHA={BOOT26_REPORTER}: the same; a page whose INDEX=1 line says '
            f'SHA={BOOT25_REPORTER} has rung 5\'s reporter, which prints none of the lines of section 5b that this record is for). Then compare, on the same line, '
            f'INDEX=2 ... BYTES={guest["size"]} SHA={guest["sha256"][:16].upper()} (GUEST.DAT) and, on the SNAPMON STAGE=PIN_OK line, LABEL={label} CHUNKS={guest["chunks"]} (boot 26\'s page had '
            f'LABEL={BOOT26_LABEL} CHUNKS={guest["chunks"]}, INDEX=2 ... SHA={BOOT26_GUEST} and INDEX=3 ... SHA={BOOT26_PIN}, the SHA of its PIN.DAT: rung 6\'s bundle has the same programs and the same '
            'chunk count and differs in the GUEST.DAT size and digits, the label and the PIN.DAT digits; this record cannot name its own PIN.DAT SHA, which depends on this record\'s SHA-256).')


BOX_DECISION = ('the operator chose this box in the app on 2026-11-19 for rung 6 (D5\'\', "let\'s go with 120 seconds"), this record asks the same, and approving this text confirms that choice and the '
                'exposure that section 4 gives, which D8 accepted for a box of 5 s only')


def d8_decision(real, box_s, wait_s):
    """0b and section 4, beside the exposure: D8 accepted it for 5 s, and the operator chose this record's box in the app for rung 6."""
    return (f'D8 accepted the exposure of one CPU for a box of 5 s only: for this box of {box_s} s the operator chose it in the app on 2026-11-19 (D5\'\', for rung 6, which this record asks again), and '
            'approving this text confirms it (section 0).')


def decided(real, box_s, wait_s):
    """Section 0, after the decisions of 2026-11-17: the answer of 2026-11-19, quoted from the app as it is, in the layout of the guide's `item`."""
    first, *rest = m1t.wrap(f'the time box of this record is {box_s} s, with AC power REQUIRED, one CPU loaded for up to {box_s} s and no temperature readout (D8\'): rung 6 ran inside it and approving '
                            'this text confirms it for this record. Sections 0b and 4 give the exposure.', first=8, rest=8)
    return (*m1t.wrap('Decided by the operator on 2026-11-19 (the answer "let\'s go with 120 seconds", quoted from the app, to the question whether the box of rung 6\'s record should be 120 s or 60 s):'),
            '   ' + 'D5\'\''.ljust(5) + first[8:], *rest)


IDLE = ('the extra seconds are there for what follows the bsd_init burst of about 60 s: the waiters of IOSurfaceRoot that are released with it (the jpeg, AVE and display drivers in this tree) and the '
        'root device\'s silent loop of 60 s waits, whose first timeout, about 122 s, falls just outside the box (section 5d)')
GIC_IDLE = ('The quarantine switches off the physical timer interrupts and not the timer that XNU uses, which is the monitor\'s emulation of it and delivers its interrupts (OBSERVED once: boot 26, '
            'section 0a), so the seconds of a box beyond the first 60 may add the kernel\'s own timed waits (section 5d).')
WHOLE = r6.WHOLE


def guide_words(base):
    """The guide's PANEL entry of this record: rung 6's (`base`), with the words that this module says again, and the legend of the reporter's page (rung7d)."""
    import sp11_stick_bundle_panel_rung7d as d
    return {**base, 'decides': DECIDES, 'hazard': hazard, 'rung': RUNG, 'why': why_lines, 'stand': stand_extra, 'stand_head': STAND_HEAD, 'console': CONSOLE, 'ran': RAN, 'stop_seen': STOP_SEEN,
            'fault_seen': FAULT_SEEN, 'step4': step4, 'page': d.page_legend, 'idle': IDLE, 'gic_idle': GIC_IDLE, 'whole': WHOLE, 'box_decision': BOX_DECISION, 'd8_decision': d8_decision,
            'decided': decided, 'film_until': r6.film_until, 'step6': r6.STEP6}


# ---- section 3 and 3c ------------------------------------------------------------------------------------------------------------------------------------------------------------------
def console_text(panel):
    """The Console paragraph of section 3c for this record: rung 6's, with boot 26 among the runs that showed the console on the panel."""
    return patched(r6.console_text(panel), ('What the operator saw in boots 21, 24 and 25 (OBSERVED once each)', 'What the operator saw in boots 21, 24, 25 and 26 (OBSERVED once each)'))


def label(panel, label_text):
    """The label paragraph of section 3c: rung 5's, with the mark of the guest profile (the label of rung 6 had none)."""
    old = 'in place of CONSOLE-, and refuses a facts label that starts with it, with CONSOLE-, CPUS1-, LETPANIC- or PANEL-,'
    new = (f'in place of CONSOLE- and, after it, the mark {pp.PROFILES["quiet"][0]} of the guest profile quiet (rung 6\'s label, {BOOT26_LABEL}, had no mark), and refuses a facts label that starts '
           'with it, with CONSOLE-, CPUS1-, LETPANIC- or PANEL- or with the mark of a profile,')
    return patched(r5.label(panel, label_text), (old, new))


def screen_text(height, width):
    """The paragraph 'The screen' of section 3c: rung 5's (what the runner's page does to the same pixels, and what the kernel's console does to them)."""
    return r5.screen_text(height, width)


def kind_lines(panel):
    """The lines that section 3 adds after the kind's own line: rung 6's, with the word and the profile in the guest's description."""
    return m1t.wrap(patched(r6.kind_lines(panel), ('with cpu_boot_timeout_secs=90 in its command line,', f'with cpu_boot_timeout_secs={CPU_WAIT_S} in its command line and the device tree of the guest profile quiet,')))


def in_short(panel):
    """The summary of section 3c."""
    d = panel['rung7']['diff']
    return ('In short. This record is rung 7, after boot 26 (rung 6), which ran 61.6 s and stopped at the scaler\'s window, a device that has no model, after guest timer interrupts were shown to be '
            f'delivered (section 0a): the monitor, the acknowledge, let_panic, the panel, the six CPUs, the pair and the box of {panel["run"]["box_s"]} s (D5\'\') are rung 6\'s; the device tree is the '
            f'profile quiet\'s ({d["nodes"][1]} of {d["nodes"][0]} nodes) and the command line has cpu_boot_timeout_secs={CPU_WAIT_S}. What it expects, what falsifies that and what counts as a '
            'result are in section 5d, in the operator\'s words; this section is the evidence for them.')


def programs(panel):
    """The sentence of the introduction on the programs: the pair is rung 6's."""
    run = panel['run']
    return (f'The programs: SNAPMON-M1-ACK.BIN has the SHA-256 {run["monitor"][:16]}..., rung 5\'s and rung 6\'s: the wrapper with the acknowledging monitor ({r5.ACK_MONITOR}, 608 bytes longer '
            f'than the plain one, {r5.PLAIN_MONITOR}), built with the box cap at {m1.BOX_MAX_S} s in place of 5 s (SP11_STICK_MONITOR_BOX_MAX_S, section 4). SNAPREP-M1-ACK.BIN has the SHA-256 '
            f'{run["reporter"][:16]}..., rung 6\'s (reporter v3, which boot 26 ran): it prints the monitor\'s report window words that boot 25\'s reporter did not print (section 5b), reads REPORT.DAT and '
            'prints and stores to its stack only (read on the host: the audit of its code, a file and not a run). The builder holds each acknowledging record to its pair by digest (M1_REPORT_MISMATCH '
            'for another pair): rung 7 is built with rung 6\'s pair and with no other, and rung 5\'s record with rung 5\'s.')


def intro(panel):
    """The introduction of section 3c: rung 6 is the run before; this record changes the guest's device tree and command line and nothing else."""
    box_s = panel['run']['box_s']
    return (f'Rung 6 is the run before this one: the panel as the console with the CPU-start stores acknowledged, let_panic, cpu_boot_timeout_secs=90 and a box of 120 s, record {BOOT26_RECORD}, boot 26 '
            f'(section 0a); its approval is spent. This record is rung 7: its guest has the device tree of the profile quiet and its command line has cpu_boot_timeout_secs={CPU_WAIT_S} where '
            f'rung 6\'s has 90; its monitor, its reporter, its box ({box_s} s, D5\'\', which approving this text confirms: section 0), its let_panic word and its PARAMS chunk are rung 6\'s. It is not '
            'decided: approving the SHA-256 of this record is that decision, and it covers ONE boot of this stick (section 5c, THIS APPROVAL COVERS ONE BOOT); rung 6\'s approval covers none of it. '
            f'{programs(panel)} SNAPMON.BIN and SNAPREP.BIN are rung 6\'s, byte for byte. GUEST.DAT (the command line and the length of the device tree in boot_args, and the device tree), PIN.DAT (the '
            'label and the digests) and PAYLOAD.MAN, which lists them, differ from rung 6\'s. Section 1b has the programs\' digests and the NV2 record, which is the first run\'s. Section 5 has the rule '
            'that rung 2 had.')


def other_differences(panel):
    """The sentence of section 3c on what else differs: made from the builder's own chunk comparison with rung 6's guest (tools/sp11_stick_bundle_rung7.py compare)."""
    d = panel['rung7']['diff']
    nodes, dt, size, word, first, second = d['nodes'], d['dt_bytes'], d['guest_bytes'], d['dt_length'], *d['differing']
    return ('Other differences from the withheld guest follow from the panel\'s address, which lies below the bank: the guest\'s frame map and tables are wider (docs/SP11-STICK-REAL.md '
            f'section 2: 46 chunks against 44 in the host run that table records). From rung 6\'s guest (GUEST.DAT {BOOT26_GUEST}..., {size[0]:,} bytes, {d["count"]} chunks) this one differs in '
            f'{len(d["differing"])} chunks, which the builder compares by SHA-256 with the table of rung 6\'s own bundle that it pins, and it refuses any other difference (PANEL_MISMATCH): chunk '
            f'{first}, HANDOFF, whose boot_args page differs in two fields and no other byte (the length of the device tree, {word[0]:,} bytes in rung 6\'s and {word[1]:,} here, and the command '
            f'line, which ends with cpu_boot_timeout_secs={CPU_WAIT_S} where rung 6\'s ends with 90; with those two put back to rung 6\'s values its SHA-256 is rung 6\'s), and chunk {second}, '
            f'DEVICE_TREE ({dt[0]:,} bytes in rung 6\'s, {dt[1]:,} here; {nodes[0]} nodes in rung 6\'s, {nodes[1]} here, counted by the builder from the chunk it built). The other '
            f'{d["count"] - len(d["differing"])} chunks are rung 6\'s byte for byte, PARAMS among them{"" if d["params_same"] else " (NOT HERE)"}: the box, the let_panic word, the 120 models and the '
            f'programs\' places. GUEST.DAT is {size[0] - size[1]:,} bytes smaller than rung 6\'s ({size[0]:,} to {size[1]:,}) and has its own SHA-256 (docs/SP11-STICK-REAL.md section 2).')


def word_text():
    """The paragraph of section 3c on the command line: the one word, what XNU does with it (INFERRED), why it is 120 and why rung 6's was 90."""
    return (f'The extra word. The command line is the loader\'s with serial=2 and, after it, cpu_boot_timeout_secs={CPU_WAIT_S} ({LINE}): one word, from the builder\'s list (--boot-arg: cpus=1 with '
            '--cpu-start stop and cpu_boot_timeout_secs=120 or cpu_boot_timeout_secs=90 with --cpu-start ack, BOOT_ARG_REFUSED for any other word or pair), read back from boot_args by the builder '
            '(PANEL_MISMATCH for any other line). The name is a string in the kernelcache and its variable is a 32-bit tunable with the default 1 and no clamp (read on the host, a file, not a run). '
            'What it does is INFERRED from the public source (tag xnu-12377.121.6, not this kernel\'s own): the kernel\'s CPU-boot thread calls processor_boot for all five secondary CPUs first, '
            'and processor_boot does not wait; only then, in that thread, sched_cpu_init_completed waits for each CPU in turn, CPU 1 first (processor_wait_for_start: a timed block of the word\'s '
            'seconds, the staged device tree having no target-is-fpga to multiply it) and panics "cpu 1 failed to boot for the first time, waited N seconds" when it times out. The word is rung 5\'s '
            f'120 and not rung 6\'s 90: with 90 the panic would come about 92 s after the MON CALL line and cut a run that has not yet shown where the quiet guest ends; with {CPU_WAIT_S} it comes about '
            f'{CPU_PANIC_AT_S} s in (the wait starts about 2.3 s in, after the last CPU-start store, as boot 24\'s PMGR panic at 2.25 s suggests: INFERRED), outside the box of {BOX_S} s by a margin of about '
            '2 s, so that the box ends the run if nothing else does; the margin is thin and the record says so: a panic text "cpu 1 failed to boot ... waited 120 seconds" inside the box would mean that it '
            'was wrong (section 5d). The wait blocks only the CPU-boot thread and the starts of AppleARMWatchdogTimer, AppleProcessorTrace and AppleT8140CLPC, and nothing on the path to the root device or '
            'to launchd (read on the host in the kernelcache and the public source, a file and not a run); boots 25 and 26 went on on the boot CPU (OBSERVED once each). cpus=1 is not in this line (boot 24 '
            'asserted in the PMGR driver: section 0a) and there is no cpumask= (XNU takes the two together as an incorrect configuration). EXPERIMENTAL, and a new operator decision.')


ACK_REACH = ('What the acknowledge lets the guest reach. A guest that is not stopped at the CPU-start write runs on past a point that no run had passed before boot 25, for up to the box: boot 25 ran '
             '60 s past it and boot 26 61.6 s (OBSERVED once each), and this record runs up to 120 s. What it can touch is what every run of this monitor can: the existing stage-2 map (its RAM and '
             'reserve, the panel range, the monitor\'s work area read-only) and the monitor\'s models and report window; the acknowledging build adds no register, no system-register class and no '
             'store class (read on the host: its census is the plain monitor\'s but for the addresses of six data words), and a guest that stores forever to the block costs one trap each, ended by the '
             'box. The approval of this record covers that reach for up to the box (section 0). Which device the kernel touches after the acknowledged stores is no longer a question of the first seconds: '
             'boot 25 did not stop at the video decoder\'s window (IPA 0X380001000) that the host analysis expected first, and boot 26 stopped at the scaler\'s, a device that has no model '
             '(OBSERVED once), because the waiters of IOSurfaceRoot are released together when bsd_init publishes IOBSD at about 61 s (INFERRED); in this guest\'s tree neither node is present, the jpeg, AVE '
             'and display nodes are, and a stop at another device that has no model is possible and ends the run, as in every run. The six CoreSight lock '
             'models of the staged PARAMS cover the key write that cpu_start makes for CPUs 1 to 5, and the CPU nodes\' acc-impl-reg and cpu-uttdbg-reg windows have no model, but the start path does '
             'not touch them (read on the host).')


def kind_facts(panel):
    """The facts of this kind for pp.profile_text (and for pp.record_problems on the finished record): its label, its guest as the page prints it and, in `changes`, the sentence on what the guest
    differs in, made from the comparison that the builder made with rung 6's own bundle."""
    r7 = panel['rung7']
    d = r7['diff']
    changes = (f'Against rung 6\'s own guest it differs in {len(d["differing"])} of {d["count"]} chunks, which the builder compares by SHA-256 (Other differences, above): the DEVICE_TREE chunk '
               f'({d["dt_bytes"][1]:,} bytes; rung 6\'s {d["dt_bytes"][0]:,}) and the HANDOFF page, in the tree\'s length word and in the command line\'s word (120 where rung 6\'s has 90); PARAMS, '
               f'the {pp.MODELS} models and the programs are rung 6\'s.')
    return dict(kind='rung 6\'s form with the wait of 120 s (rung 7)', label=r7['label'], let_panic=True, models=pp.MODELS, guest_sha16=r7['guest_sha16'], guest_bytes=r7['guest_bytes'],
                changes=changes)


def extra_paragraphs(panel):
    """The paragraphs of section 3c that only these records have, after the command line's: rung 6's (the PARAMS word, the acknowledge, what still stops the run, the panic path and the page), with
    this record's reach of the acknowledge, and the profile's paragraphs (tools/sp11_stick_bundle_panel_profile.py, composed with this kind) at the end."""
    parts = r6.extra_paragraphs(panel)
    assert len(parts) == 6 and parts[2].startswith('What the acknowledge lets the guest reach.'), 'the paragraphs of rung 6 have changed under rung 7'
    params = patched(parts[0], ('for the records of rungs 4, 5 and 6 only', 'for the records of rungs 4 to 7 only'))
    return (params, *parts[1:2], ACK_REACH, *parts[3:], *pp.profile_text(panel['profile'], kind_facts(panel)))


def expect_text(run):
    """The paragraph of section 3c that stands where rung 3's and 4's what-the-run-may-end-as is: the prediction and its falsifiers are section 5d's, in the operator's words, and only there."""
    return patched(r6.expect_text(run), ('from the host analysis of boots 24 and 25: none of it has been seen with this word', 'from the host analysis of boots 24 to 26: none of it has been seen with the quiet guest'),
                   ('NOT PASSED for rung 6 (section 5d)', 'NOT PASSED for rung 7 (section 5d)'))


def section_5d(panel, wait_s):
    """Section 5d of the record, the operator's words: tools/sp11_stick_bundle_panel_rung7d.py, which takes its constants from this module (so it is imported here, when asked)."""
    import sp11_stick_bundle_panel_rung7d as d
    return d.section_5d(panel, wait_s)


def __getattr__(name):
    """A hook or a constant that this module does not say again is rung 6's (CITES, film_until, STEP6 and the rest)."""
    if name.startswith('__'):
        raise AttributeError(name)
    return getattr(r6, name)
