# SPDX-License-Identifier: MIT
"""Rung 6 of the monitor entry run (docs/SP11-STICK-MONITOR.md section 20, K14): the record for the boot after rung 5. Boot 25 (rung 5: the CPU-start stores acknowledged, six CPUs,
cpu_boot_timeout_secs=120, let_panic, a 60 s box) ran its whole box and ended as MON STOP WATCHDOG with no panic text, the guest's CPU 0 idle in its WFI with every interrupt masked and the log
silent for the last 25 s. Whether any guest timer interrupt was delivered in those 60 s is UNKNOWN: that run's reporter printed no timer word. This record keeps the monitor, the acknowledge,
let_panic, the panel and the six CPUs, and changes three things: the command line has cpu_boot_timeout_secs=90 in place of 120, so that XNU's own wait for the CPU that never comes ends inside the
box if guest timers are delivered; the box is 120 s (D5''); and the reporter prints the guest timer's words (section 5b).

`--cpu-start ack --let-panic --boot-arg cpu_boot_timeout_secs=90 --panel console --time-box-s 120 --sptm-hvc-calls 0` is the one combination that builds it (tools/sp11_stick_bundle_m1.py, ACK_FORMS), with
the pair of ACK_PAIRS; the guide's table PANEL takes its words by the key `console+cpu_boot_timeout_secs=90+let_panic` (tools/sp11_stick_bundle_panel.py). The words that it shares with rung 5 are
tools/sp11_stick_bundle_panel_rung5.py's, by import; section 5d and the legend of the page are tools/sp11_stick_bundle_panel_rung6d.py's. Everything that depends on the box takes it from the record
(`box_s`, and `wait_s`, the silence rule's wait for it). Boot 25's result is a keyed block, RUNG5 below: the operator's film as the coordinator read it (OBSERVED, one unit, one boot) and the host
analysis of its stop (read on the host, a file and not a run; INFERRED where it says so).

Evidence: EXPERIMENTAL, hardware NOT RUN."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_bundle_m1 as m1  # noqa: E402
import sp11_stick_bundle_record_m1 as m1t  # noqa: E402
import sp11_stick_bundle_panel_rung2 as r2  # noqa: E402
import sp11_stick_bundle_panel_rung3 as r3  # noqa: E402
import sp11_stick_bundle_panel_panic as rp  # noqa: E402
import sp11_stick_bundle_panel_rung5 as r5  # noqa: E402

WORD = 'cpu_boot_timeout_secs=90'           # the one word of this record's command line (m1.BOOT_ARGS): it goes with the acknowledging monitor
NUMBER = 6                                  # the rung's number, for the pointer to section 5d in the reading lines (tools/sp11_stick_bundle_panel.py)
LINE = f'{r5.LOADER_LINE} {WORD}'
CPU_WAIT_S = int(WORD.rpartition('=')[2])   # XNU's wait for the CPU that never comes, in s
BOX_S = m1.RUNG6_BOX_S                      # D5'''s 120 s
PANIC_AT_S = 92                             # about 2.3 s (the start of the wait: boot 24's PMGR panic came at 2.25 s) plus CPU_WAIT_S (INFERRED)
BOOT25_RECORD = '64d87443...97f6'           # the approved record of rung 5: SHA-256 64d874432edbc46dd2ed30fd70e6f28bae3f063c34fb568ff68862ef538997f6
BOOT25_TICKS = 1_155_520_917                # MON RET TICKS= of boot 25, in ticks of 19.2 MHz (OBSERVED once)
BOOT25_S = f'{BOOT25_TICKS / 19.2e6:.2f}'
BOOT25_COUNTS = 'MON COUNTS EVENTS=512 TRAPS=168214 ABORTS=0 STEPS=1441 CALLS=66179 DROPPED=54212'
BOOT25_STOP = dict(pc=0xFFFFFE000B8716A8, a=0x9600004F, b=0xFFFFFECFFBFDC000)      # MON STOP WATCHDOG PC= A= B= of boot 25 (OBSERVED once); C= lost its last two digits to the runner's clip
BOOT25_SPSR = '0X604003C4'                  # MON EXIT SPSR= of boot 25
BOOT25_MONITOR = 'BB34B9701D89302F'         # SNAPMON-M1-ACK.BIN as the runner prints it (PAYLOAD LOADED INDEX=0): the same in this record
BOOT25_REPORTER = 'EE86676001D89CF6'        # SNAPREP-M1-ACK.BIN of boot 25 as the runner prints it (INDEX=1): not this record's
BOOT25_GUEST = '896F56E29DFC5E49'           # rung 5's GUEST.DAT as the runner prints its first 16 digits (INDEX=2)
BOOT25_PIN = 'BCE154588BB1E833'             # rung 5's PIN.DAT as the runner prints its first 16 digits (INDEX=3)
BOOT25_LABEL = 'M1RUN-ACKPANIC-RUNG5'
STAND_HEAD = '0a. Where things stand (boots 20, 21, 23, 24 and 25; 2026-11-18/19; one unit, one boot each; OBSERVED unless INFERRED)'
HEADER = ('3c. The panel as the kernel\'s console with the CPU-start stores acknowledged, let_panic and a wait of 90 s (rung 6: D9 panel handed, serial=2, cpu_boot_timeout_secs=90, '
          'let_panic set, stock kernel; docs/SP11-STICK-MONITOR.md section 20)')
PARAMS_ROW = r5.PARAMS_ROW

# KEYED BLOCKS: the result of a run as the operator's film showed it and the coordinator read it (files/result-boot24.md and files/result-boot25.md of the session) and the host analysis of its
# stop (files/stopsite-r4.md and files/stopsite-r7.md: read on the host from the kernelcache file, the guest's device tree and the public XNU source, a file and not a run). `record` is the run's
# approved record, `boot` its boot as the operator's log names it, `verdict` one of VERDICTS as the record's section 5d names it, and `words` the paragraphs, each with its label.
# keyed_lines() holds them to the record's rules.
RUNG4 = dict(record=r5.BOOT24_RECORD, boot='boot 24', verdict='PANIC TEXT', words=(
    'The panel showed the kernel\'s panic text (OBSERVED once; the coordinator\'s transcription of the film): panic(cpu 0 caller 0xfffffe00097ddef0): "ApplePMGR: virtual void '
    'ApplePMGR::enableCPUComplex(UInt32, bool):4861 REQUIRE failed: complex < _cpuComplexCount" @ApplePMGR.cpp:4861, then "Nested panic detected - entry count: 4" with an empty nested panic '
    'string, then "Nested panic count exceeds limit 5, machine will reset or spin" and "wdog: panic chipreset". The monitor\'s console ring carried the first lines of the same text onto the page '
    f'as MON CON lines (OBSERVED once). The call returned {r5.BOOT24_S} s in and the page said MON STOP S2_FAULT PC={r2.pc_text(r5.BOOT24_STOP["pc"])} A={r3.page_hex(r5.BOOT24_STOP["a"])} '
    f'B={r3.page_hex(r5.BOOT24_STOP["b"])} C={r2.pc_text(r5.BOOT24_STOP["c"])}, on a clean report (OBSERVED once): a 32-bit READ at a translation fault at level 1 at stage 2, INFERRED to be '
    'the kernel\'s read in its panic chip-reset path, after its text, at a register of the watchdog node (wdt) of the guest\'s device tree (read on the host, a file and not a run). M1 PASSED by '
    'section 5a.',
    'The cause, read on the host (a file and not a run; INFERRED where it says so): the assertion follows XNU\'s topology and not a hardware read. With cpus=1 XNU has one cluster and the PMGR '
    'driver\'s start enables two complexes, so the second asserts; boot 21 and boot 25, with six CPUs and two clusters, passed this driver, and no guest-data change keeps a one-CPU topology past '
    'it (INFERRED). Each nested panic repeats because the kernel\'s panic-record routine stores through a pointer that stays NULL when the guest\'s device tree has no panic-log region '
    '(INFERRED), and the monitor lets the kernel\'s own page faults through (probes/xnuboot/trap_dispatch.c:370-387): the repetition is the kernel\'s and not an artefact of the monitor.'))
RUNG5 = dict(record=BOOT25_RECORD, boot='boot 25', verdict='WATCHDOG, NO TEXT', words=(
    f'The call ran its whole box: TICKS={BOOT25_TICKS} at 19.2 MHz ({BOOT25_S} s; above the {60 * 19_200_000:,} of a 60 s box, and the stop was the monitor\'s own MON STOP WATCHDOG, so row e '
    f'held by its own rule), {BOOT25_COUNTS}, then MON STOP WATCHDOG PC={r2.pc_text(BOOT25_STOP["pc"])} A={r3.page_hex(BOOT25_STOP["a"])} B={r3.page_hex(BOOT25_STOP["b"])} (C= lost its last two '
    f'digits to the runner\'s clip at 100 columns) and MON EXIT SPSR={BOOT25_SPSR}, with GIC RESTORE CLEAN=YES, on a clean report (OBSERVED once; the operator\'s film, read by the coordinator). '
    'M1 PASSED by section 5a. No panic text was on the panel (OBSERVED once: no frame of the film shows it). The log of the kernel filled the panel from the MON CALL line, as in boots 21 and 24, '
    'and went much further than in any earlier run: about 35 s of drivers (the SPMI and PMU lines with their failed register reads, "AppleARMRTC started!", "AppleDialogSPMIPMURTC started!", then the USB '
    'lines "failed to find phyService" and "failed to find AppleUSBHostPort", about 28 to 35 s after the MON CALL line), and then the log was silent for the last 25 s of the box: the bottom lines and '
    'the cursor did not change (OBSERVED once). The noise lines of boots 21 and 23 were in it as before and are not the failure.',
    'Rung 5\'s falsifiers did not occur (OBSERVED once): no ApplePMGR assertion, no stop at 0X300734004..0X30073400C, no "cpu 1 failed to boot" text. Its prediction that the PMGR driver and the first '
    'CPU-start write pass held, and the stop at the video decoder\'s window (IPA 0X380001000) that the host analysis named as the likeliest early one did not come. Rung 5 was WATCHDOG, NO TEXT, '
    'with progress: the class of its section 5d that fits.',
    'The decode of the stop, read on the host in the kernelcache and the monitor\'s sources (a file and not a run; INFERRED where it says so): PC= is the instruction after a WFI in XNU\'s idle path, '
    'reached only through the scheduler\'s idle loop, and SPSR 0X604003C4 is EL1 with D, A, I and F masked, so CPU 0 was idle with every interrupt masked at the end of the box and every other '
    'thread was blocked. A=, B= and C= are the guest\'s last EL1 exception and not necessarily current: ESR 0X9600004F is a data abort at the same EL, a write, a permission fault at level 3, '
    'INFERRED to have been handled, since no panic followed. The kernel\'s next timed waits after its last line are IOKit\'s 30 s wait for the RTC resource, the calendar\'s 30 s wait for the same '
    'resource, the root device\'s 30 s wait for the BSD resource and then a silent wait for the root device, and the CPU-boot thread\'s own wait of cpu_boot_timeout_secs, whose panic would come at '
    'about 2.3 s plus the word, 122 s with 120 (INFERRED; read on the host).',
    'What the stop says, INFERRED: the kernel went idle and nothing woke it in the last 25 s. A timer interrupt could have; whether any guest timer interrupt was delivered in the 60 s is UNKNOWN, '
    'because that run\'s reporter printed no timer word and nothing else in boot 25 shows it either way (the one hint that leans to delivery is the PMU retry sleeps of the first 35 s, which block on '
    'a timer unless the kernel\'s idle latency exceeds 1 ms: weak). The host analysis of the monitor\'s code finds no reason for a timer interrupt not to arrive: the guest\'s timer is the monitor\'s '
    'emulation (it keeps the deadline in software, wakes itself with its own EL2 timer, PPI 26, and injects the expiry as a virtual FIQ), which the quarantine of the physical timer interrupts does not '
    'touch (probes/xnuboot/clock_timer.c:195-236, probes/xnuboot/async.c:95-105, probes/xnuboot/stage2.c:401-408), and an idle CPU 0 wakes only at its guest deadline or at the end of the '
    'box, because this monitor has no 1 ms physical poll (INFERRED). A timer that works and a kernel that waits for something that needs more than 60 s, and a timer that does not arrive, both fit boot '
    '25: this record is the cheapest run that tells them apart.'))
VERDICTS = ('PANIC TEXT', 'WATCHDOG, NO TEXT')      # the verdicts that this record's blocks may carry: the stop of rung 4 and of rung 5


def keyed_lines(block, what):
    """The paragraphs of section 0a that are one run's result: a keyed block. AssertionError for a verdict that this record is not for, and for a paragraph that breaks the record's rules (ASCII, no
    claim without a label, never the word that the project keeps for what an independent run showed)."""
    assert block['verdict'] in VERDICTS, f'the verdict of {what} is not one that rung 6 is the record for'
    for paragraph in block['words']:
        assert paragraph.isascii() and 'VERIFIED' not in paragraph, 'a paragraph of a keyed block is not ASCII or uses the word that M1 records never use'
        assert any(label in paragraph for label in ('OBSERVED', 'INFERRED', 'UNKNOWN')), 'a paragraph of a keyed block has no label'
    return (*m1t.wrap(f'{what} (record {block["record"]}, {block["boot"]}; one unit, one boot; the operator\'s film, read by the coordinator): {what.upper()} {block["verdict"]}.'),
            *(line for paragraph in block['words'] for line in m1t.wrap(paragraph)))


# ---- section 0 -------------------------------------------------------------------------------------------------------------------------------------------------------------------------
DECIDES = ('APPROVING THIS TEXT ALSO DECIDES THREE THINGS, THE FIRST OF WHICH THE OPERATOR HAS ALREADY CHOSEN IN THE APP. (1) D5\'\': A TIME BOX OF 120 s. D5 and D8 decided 5 s and D5\' decided 60 s; for '
           'this record the operator chose 120 s in the app on 2026-11-19 ("let\'s go with 120 seconds", the answer to the question whether its box should be 120 s or 60 s), and approving this text '
           'confirms it: AC power REQUIRED, one CPU loaded for up to 120 s, no temperature readout, and the exposure sums of section 0b and section 4. Boot 25 left its CPU idle for the last 25 '
           's of 60 (INFERRED: section 0a), so the extra time may be spent mostly idle. (2) A guest command line with cpu_boot_timeout_secs=90 where '
           'rung 5\'s had 120, so that XNU\'s own wait for the CPU that never comes ends inside the box if the guest\'s timers are delivered; the guest is otherwise rung 5\'s, six CPUs included. (3) A '
           'reporter that prints the guest timer\'s words and the monitor\'s other counters (section 5b): it reads the report window that the monitor already writes, and no byte of the monitor changes. '
           'What the guest can reach is what rung 5\'s could, the existing stage-2 map and the monitor\'s models and report window, with no new register, no new system-register class and no new store '
           'class (read on the host in compat/cpu-start-ack.h, a file and not a run; the census of section 3 is the plain monitor\'s but for the addresses of six data words), and this approval is for '
           'that reach for up to the box. The acknowledging monitor, the panel as the console and the monitor\'s let_panic word ran in boot 25 for 60 s; this word and this box have not.')
RUNG = ('each of rungs 1 to 5, the first run with the panel withheld, the panel as the console (rung 2, boot 21), the same with cpus=1 (rung 3, boot 23), with cpus=1 and let_panic (rung 4, boot 24) and '
        'with the CPU-start stores acknowledged, six CPUs and a 60 s box (rung 5, boot 25), whose records have their own approvals, spent: this record is rung 6, the same as rung 5 but for a wait of 90 s '
        'in the command line, a box of 120 s and another reporter, and no approval covers another\'s boot')
CONSOLE = ('The guest is given the panel (section 3c) and its command line is serial=2 with cpu_boot_timeout_secs=90, its let_panic word is set and the monitor acknowledges its CPU-start stores: '
           'its console is the panel (boots 21, 24 and 25: the kernel\'s log and panic text were on it, OBSERVED once each) and the MON CON lines are expected to carry at most the first lines of a '
           'panic text (boot 24: OBSERVED once; boots 21 and 23 had none); what the acknowledge and let_panic do to this run beyond boot 25\'s 60 s is INFERRED and has not been seen.')
RAN = ('ran once on this machine in its acknowledging build, for 60 s (boot 25: section 0a), and in its plain build in boots 20, 21, 23 and 24.')
STOP_SEEN = {**r5.STOP_SEEN, 'WATCHDOG': 'OBSERVED twice (boots 20 and 25, one unit, one boot each).'}


def d5_second(box_s):
    return (f'D5\'\' (the operator chose {BOX_S} s in the app on 2026-11-19, "let\'s go with 120 seconds", and approving this text confirms it for this record; D5\', "Yes, up to 60 s" of 2026-11-18, '
            f'covered rung 5 and is spent with it): the time box is {box_s} s, with AC power, one CPU running (the guest\'s tree lists six, and the acknowledge starts none) and no temperature readout (D8\')')


def why_lines(real):
    """Section 0's paragraph for this record: why rung 6, the three changes, the prediction and what falsifies it. Boot 25's facts are section 0a's (stand_extra)."""
    box_s = real['panel_facts']['run']['box_s'] if real.get('panel_facts', {}).get('run') else BOX_S
    return m1t.wrap(f'WHY RUNG 6. Rung 5 (boot 25, record {BOOT25_RECORD}) ran its whole box of 60 s and ended as MON STOP WATCHDOG with no panic text, and its log went further than in any earlier run '
                    f'(OBSERVED once: section 0a). At the stop CPU 0 was idle in its WFI with every interrupt masked and the log had been silent for the last 25 s (INFERRED from the PC; the WFI is read on the '
                    f'host in the kernelcache, a file and not a run). Whether any guest timer interrupt was delivered in those 60 s is UNKNOWN: that run\'s reporter printed no timer word. The host analysis '
                    f'of the monitor\'s code says that the guest\'s timer is the monitor\'s emulation, which the quarantine does not touch, and finds no reason for it not to work (INFERRED; section 0a); '
                    f'boot 25 neither shows nor refutes it. The kernel\'s own timeouts can decide it with no new instrument: XNU\'s CPU-boot thread waits cpu_boot_timeout_secs for the CPU that never comes '
                    f'and then panics, and with let_panic set the panic text prints. THIS RECORD CHANGES THREE THINGS FROM RUNG 5, AND NOTHING ELSE. (1) THE COMMAND LINE HAS cpu_boot_timeout_secs={CPU_WAIT_S} '
                    f'IN PLACE OF 120: if the guest\'s timers are delivered, that panic, "cpu 1 failed to boot for the first time, waited {CPU_WAIT_S} seconds", comes at about {PANIC_AT_S} s after the MON CALL '
                    f'line, inside the box (INFERRED: the wait starts about 2.3 s in, as boot 24\'s PMGR panic at 2.25 s suggests), and the film then holds an OBSERVED panic text, from which (XNU times a wait '
                    f'out by a timer interrupt: INFERRED) a guest timer interrupt reaching the kernel follows; silence to the end of the box does not support that reading. (2) THE TIME BOX IS {box_s} s, so that the panic, if it comes, has room: ' + d5_second(box_s) + '. (3) THE REPORTER '
                    'IS ANOTHER ONE: its page prints the guest timer\'s words (MON TIM, MON CLK) and the monitor\'s other counters (section 5b), so that a silent run still says whether the timer was armed, '
                    'delivered or lost. The monitor, the acknowledge, let_panic, the panel as the console, the six CPUs and the rest of the guest are rung 5\'s. THE PREDICTION, ITS FALSIFIERS AND WHAT THE FILM '
                    'DECIDES are in section 5d, in the operator\'s words (INFERRED).')


def stand_extra(real):
    """The paragraphs that section 0a gets after boot 20's: boots 21 and 23 condensed to what this record still needs, and the keyed blocks of rung 4's and rung 5's results."""
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
            *m1t.wrap('The stick after boots 21, 23, 24 and 25 was unchanged but for the Shell\'s marker (and, after boots 21, 23 and 24, the Mac\'s own .fseventsd entry), and REPORT.DAT kept its all-zero '
                      'staged digest: a run does not write the report back, so the page on the panel is its only record (OBSERVED once each; boot 25\'s by a read on the host of the stick as returned: its 18 '
                      'staged files byte-identical and the Shell\'s marker the only new file).'))


def hazard(real, box_s, wait_s):
    """The panel's bullet of 0b: the console on the panel is boots 21's, 24's and 25's; the path of the guest past the acknowledged stores is boot 25's for its first 60 s and unknown after."""
    return ('The guest can write the unit\'s framebuffer, which the display scans out, and the kernel draws its console there (OBSERVED once: boots 21, 24 and 25): from the MON CALL line the panel is '
            'cleared and fills with the kernel\'s log (the kernel\'s console clears the whole panel when it takes it: public source, not this kernel\'s own), and its text can paint over the '
            'page, so that the report can be hard to read. The film is the only record of that text and of the lines printed before the guest ran (section 5d). The monitor acknowledges the '
            f'guest\'s CPU-start stores (section 3c) and no CPU starts: in boot 25 the kernel went on past them and the log was silent for the last 25 s of 60 (OBSERVED once; that its CPU 0 sat '
            f'idle all that time, waiting, is INFERRED from the PC at the stop), and what it does in {box_s} s is UNKNOWN (it may wait, panic at about {PANIC_AT_S} s or spin). With let_panic set a panic runs the kernel\'s own panic path, which boot 24 '
            f'showed printing its text (OBSERVED once) before the monitor stopped it at a device that has no model. A box of {box_s} s with a CPU that is mostly idle after the first 35 s or so '
            f'(INFERRED from boot 25) is the operator\'s decision D5\'\', made in the app on 2026-11-19 and confirmed by approving this text (section 0): the longest run so far was boot 25\'s 60 s, which showed no restart of the Surface to the end of its film, 90 s '
            f'(OBSERVED once), and a run of {box_s} s is UNTESTED. AC power is REQUIRED, and the monitor ends the run at the box or at its own stop either way (section 5d). '
            f'{r2.force_off(wait_s)}.')


def step4(guest, label):
    """5c, step 4: the programs' digits that tell this bundle from rung 5's (boot 25: SNAPREP, GUEST.DAT, PIN.DAT and the label differ; SNAPMON.BIN is the same), compared on the runner's lines."""
    return (f' The monitor\'s digits are rung 5\'s (boot 25 had SHA={BOOT25_MONITOR}: the same) and the reporter\'s are not: a page whose INDEX=1 line says SHA={BOOT25_REPORTER} has the old '
            'reporter, which prints none of the lines of section 5b that this record is for. Then compare, on the same line, '
            f'INDEX=2 ... BYTES={guest["size"]} SHA={guest["sha256"][:16].upper()} (GUEST.DAT) and, on the SNAPMON STAGE=PIN_OK line, LABEL={label} CHUNKS={guest["chunks"]} (boot 25\'s page had '
            f'LABEL={BOOT25_LABEL} CHUNKS={guest["chunks"]}, INDEX=2 ... SHA={BOOT25_GUEST} and INDEX=3 ... SHA={BOOT25_PIN}, the SHA of its PIN.DAT: rung 5\'s bundle has the same size and the same chunk '
            'count and differs in the SNAPREP digits, the GUEST.DAT digits, the label and the PIN.DAT digits; this record cannot name its own PIN.DAT SHA, which depends on this record\'s SHA-256).')


BOX_DECISION = ('the operator chose this box in the app on 2026-11-19 (D5\'\', "let\'s go with 120 seconds"), and approving this text confirms that choice and the exposure that section 4 gives, '
                'which D8 accepted for a box of 5 s only')


def d8_decision(real, box_s, wait_s):
    """0b and section 4, beside the exposure: D8 accepted it for 5 s, and the operator chose this record's box in the app after it was written."""
    return (f'D8 accepted the exposure of one CPU for a box of 5 s only: for this box of {box_s} s the operator chose it in the app on 2026-11-19 (D5\'\'), and approving this text confirms it '
            '(section 0).')


def decided(real, box_s, wait_s):
    """Section 0, after the decisions of 2026-11-17: the answer of 2026-11-19, quoted from the app as they are, in the layout of the guide's `item`."""
    first, *rest = m1t.wrap(f'the time box of this record is {box_s} s, with AC power REQUIRED, one CPU loaded for up to {box_s} s and no temperature readout (D8\'): approving this text confirms it. '
                            'Sections 0b and 4 give the exposure.', first=8, rest=8)
    return (*m1t.wrap('Decided by the operator on 2026-11-19 (the answer "let\'s go with 120 seconds", quoted from the app, to the question whether the box of this record should be 120 s or 60 s):'),
            '   ' + 'D5\'\''.ljust(5) + first[8:], *rest)


def film_until(real, box_s, wait_s):
    """The span of the film in step 2 of 5c: rung 5's, but a report page that never appears ends it at the FORCE OFF of step 5 (the last page change is the MON CALL line, which is the box
    before that wait)."""
    return (f'until {r5.FILM_AFTER_S} seconds after the SNAPREP report page has appeared, or, if it never appears, until the FORCE OFF of step 5 ({wait_s} s after the MON CALL line) has been filmed')


STILLS_END = 'so that the text on it can be read (asked, not required; section 5d).'
assert r5.STEP6.count(STILLS_END) == 1, 'step 6 of rung 5 has changed'
STEP6 = r5.STEP6.replace(STILLS_END, 'so that the text on it can be read, and a fourth of the whole report page, so that its lines can be read (asked, not required; section 5d).')


IDLE = ('the extra seconds are there for the kernel\'s own wait of 90 s, which ends inside the box only if the guest\'s timer interrupts are delivered (section 5d)')
GIC_IDLE = ('The quarantine switches off the physical timer interrupts and not the timer that XNU uses, which is the monitor\'s emulation of it (INFERRED: section 0a), so the seconds of a box '
            'beyond the first 60 may add the kernel\'s own timed waits (section 5d).')
WHOLE = ('Only the counts (MON COUNTS), the monitor\'s window words that the reporter of this record prints (MON TIM, MON CNT, MON EP) and the end of the run (MON STOP, MON EXIT) reflect '
         'the whole box.')


def guide_words(base):
    """The guide's PANEL entry of this record: rung 5's (`base`), with the words that this module says again, and the legend of the reporter's page (rung6d)."""
    import sp11_stick_bundle_panel_rung6d as d
    return {**base, 'decides': DECIDES, 'hazard': hazard, 'rung': RUNG, 'why': why_lines, 'stand': stand_extra, 'stand_head': STAND_HEAD, 'console': CONSOLE, 'ran': RAN, 'stop_seen': STOP_SEEN,
            'step4': step4, 'page': d.page_legend, 'idle': IDLE, 'gic_idle': GIC_IDLE, 'whole': WHOLE, 'box_decision': BOX_DECISION, 'd8_decision': d8_decision, 'decided': decided,
            'film_until': film_until, 'step6': STEP6}


# ---- section 3 and 3c ------------------------------------------------------------------------------------------------------------------------------------------------------------------
def moved(text):
    """The reporter of this record (the pair's SNAPREP-M1-ACK.BIN, docs/SP11-STICK-REPORT-LINES.md) has the lines that rung 5's words cite (probes/sp11-stick-reporter.c) at other lines: this
    record cites where they are now (r5.REPORTER_DRIFT), and the approved texts before it keep the lines of their own reporter."""
    for (path, first, last), (now_first, now_last, _) in r5.REPORTER_DRIFT.items():
        text = text.replace(f'{path}:{first}-{last}', f'{path}:{now_first}-{now_last}')
    return text


def console_text(panel):
    """The Console paragraph of section 3c for this record: rung 5's, with boot 25 among the runs that showed the console on the panel and the reporter's lines where this record's reporter has them."""
    text = r5.console_text(panel)
    assert text.count('What the operator saw in boots 21 and 24 (OBSERVED once each)') == 1, 'the Console paragraph of rung 5 has changed under rung 6'
    return moved(text.replace('What the operator saw in boots 21 and 24 (OBSERVED once each)', 'What the operator saw in boots 21, 24 and 25 (OBSERVED once each)'))


def label(panel, label_text):
    """The label paragraph of section 3c: rung 5's, which says that the builder puts ACKPANIC- after M1RUN- and refuses a facts label that starts with a marker (both records have that marker)."""
    return r5.label(panel, label_text)


def screen_text(height, width):
    """The paragraph 'The screen' of section 3c: rung 5's (what the runner's page does to the same pixels, and what the kernel's console does to them)."""
    return r5.screen_text(height, width)


def kind_lines(panel):
    """The lines that section 3 adds after the kind's own line."""
    return m1t.wrap('This record is of the choice that hands the guest the panel as the kernel\'s console (serial=2) with cpu_boot_timeout_secs=90 in its command line, with the acknowledging monitor '
                    'and the monitor\'s let_panic word set (section 3c): the ram class names its range and no class is added. The monitor takes the guest\'s 32-bit stores to offsets 4, 8 and 12 of the '
                    'PMGR CPU-start block (IPA 0X300734000) and ignores their value; it writes only its own RAM and its report window, so the store classes are the same three (D1b of '
                    'docs/SP11-STICK-MONITOR.md), and no CPU is started: "a secondary CPU" below stays not authorised. EXPERIMENTAL, not a model of the hardware.')


def in_short(panel):
    """The summary of section 3c."""
    box_s = panel['run']['box_s']
    return ('In short. This record is rung 6, after boot 25 (rung 5), whose 60 s box ended as MON STOP WATCHDOG with no panic text and the kernel idle (section 0a): the monitor, the acknowledge, '
            f'let_panic, the panel and the six CPUs are rung 5\'s; the command line has cpu_boot_timeout_secs={CPU_WAIT_S}, the box is {box_s} s (D5\'\') and the reporter prints the guest timer\'s '
            'words. What it expects, what falsifies that and what counts as a result are in section 5d, in the operator\'s words; this section is the evidence for them.')


def programs(panel):
    """The sentence of the introduction on the programs: the monitor is rung 5's and the reporter is not."""
    run = panel['run']
    return (f'The programs: SNAPMON-M1-ACK.BIN has the SHA-256 {run["monitor"][:16]}..., rung 5\'s: the wrapper with the acknowledging monitor ({r5.ACK_MONITOR}, 608 bytes longer than the plain one, '
            f'{r5.PLAIN_MONITOR}), built with the box cap at {m1.BOX_MAX_S} s in place of 5 s (SP11_STICK_MONITOR_BOX_MAX_S, section 4). SNAPREP-M1-ACK.BIN has the SHA-256 {run["reporter"][:16]}...: '
            'the reporter that prints the monitor\'s report window words that boot 25\'s did not print (section 5b); it reads REPORT.DAT and prints and stores to its stack only (read on the host: '
            'the audit of its code, a file and not a run). The builder holds each acknowledging record to its pair by digest (M1_REPORT_MISMATCH for another pair), so that this record cannot be '
            'built with rung 5\'s reporter, nor rung 5\'s with this one.')


def intro(panel):
    """The introduction of section 3c: rung 5 is the run before; this record changes the guest's command line and box, and the reporter."""
    box_s = panel['run']['box_s']
    return (f'Rung 5 is the run before this one: the same panel as the console with the CPU-start stores acknowledged, record {BOOT25_RECORD}, boot 25 (section 0a); its approval is spent. This '
            f'record is rung 6: its command line has cpu_boot_timeout_secs={CPU_WAIT_S} where rung 5\'s has 120, its box is {box_s} s where rung 5\'s was 60 s (D5\'\') and its reporter prints '
            f'the guest timer\'s words. It is not decided: approving the SHA-256 of this record is that decision, and it confirms D5\'\' (the operator chose the box of 120 s on 2026-11-19: section 0), and it covers ONE boot of this stick (section 5c, THIS APPROVAL COVERS ONE '
            f'BOOT); rung 5\'s approval covers none of it. {programs(panel)} SNAPMON.BIN is rung 5\'s, byte for byte. SNAPREP.BIN (the reporter), GUEST.DAT (the command line in boot_args and the time '
            'box in PARAMS), PIN.DAT (the label and the digests) and PAYLOAD.MAN, which lists them, differ from rung 5\'s. Section 1b has the programs\' digests and the NV2 record, which is the first '
            'run\'s. Section 5 has the rule that rung 2 had.')


def other_differences(panel):
    """The sentence of section 3c on what else differs: the guest's pages. The count is the builder's own chunk comparison with rung 5's guest (tools/test-sp11-stick-monitor-rung6.py)."""
    return ('Other differences from the withheld guest follow from the panel\'s address, which lies below the bank: the guest\'s frame map and tables are wider (docs/SP11-STICK-REAL.md '
            'section 2: 46 chunks against 44 in the host run that table records). From rung 5\'s guest this one differs in four bytes of data and in nothing else: three in the command line in boot_args '
            '(cpu_boot_timeout_secs=90 where rung 5\'s has 120: the digits 9, 0 and the terminating zero in place of 1, 2, 0) and one in PARAMS (the box, word 24: 120 where rung 5\'s has 60); the '
            'same size, the same chunks, and its own SHA-256 (docs/SP11-STICK-REAL.md section 2).')


def word_text():
    """The paragraph of section 3c on the command line: the one word, what XNU does with it (INFERRED), why it is 90 and what it needs to end the wait."""
    return (f'The extra word. The command line is the loader\'s with serial=2 and, after it, cpu_boot_timeout_secs={CPU_WAIT_S} ({LINE}): one word, from the builder\'s list (--boot-arg: cpus=1 with '
            '--cpu-start stop and cpu_boot_timeout_secs=120 or cpu_boot_timeout_secs=90 with --cpu-start ack, BOOT_ARG_REFUSED for any other word or pair), read back from boot_args by the builder '
            '(PANEL_MISMATCH for any other line). The name is a string in the kernelcache and its variable is a 32-bit tunable with the default 1 and no clamp (read on the host, a file, not a run). '
            'What it does is INFERRED from the public source (tag xnu-12377.121.6, not this kernel\'s own): the kernel\'s CPU-boot thread calls processor_boot for all five secondary CPUs first, '
            'and processor_boot does not wait; only then, in that thread, sched_cpu_init_completed waits for each CPU in turn, CPU 1 first (processor_wait_for_start: a timed block of the word\'s '
            'seconds, the staged device tree having no target-is-fpga to multiply it) and panics "cpu 1 failed to boot for the first time, waited N seconds" when it times out. That wait ends only by '
            'a timer interrupt: with 120, as in rung 5, it could not end inside a box of 60 s whatever the timers did; with '
            f'{CPU_WAIT_S} it ends about {PANIC_AT_S} s in, inside the box of {BOX_S} s, if a guest timer interrupt is delivered (INFERRED), and that is the point of this record. IOKit matching, '
            'bsd_init and the root mount are not blocked by that wait in the source, and boot 25 went on on the boot CPU (OBSERVED once). cpus=1 is not in this line (boot 24 asserted in the PMGR '
            'driver: section 0a) and there is no cpumask= (XNU takes the two together as an incorrect configuration). EXPERIMENTAL, and a new operator decision.')


ACK_REACH = ('What the acknowledge lets the guest reach. A guest that is not stopped at the CPU-start write runs on past a point that no run had passed before boot 25, for up to the box: boot 25 ran '
             '60 s past it (OBSERVED once) and this record runs up to 120 s. What it can touch is what every run of this monitor can: the existing stage-2 map (its RAM and reserve, the panel range, the '
             'monitor\'s work area read-only) and the monitor\'s models and report window; the acknowledging build adds no register, no system-register class and no store class (read on the host: its '
             'census is the plain monitor\'s but for the addresses of six data words), and a guest that stores forever to the block costs one trap each, ended by the box. The approval of this record '
             'covers that reach for up to the box (section 0). Which device the kernel touches first after the acknowledged stores is no longer a question of the first seconds: the host analysis '
             'expected the video decoder\'s window at IPA 0X380001000 to stop the run early, and boot 25 did not stop there (OBSERVED once); a stop at a device that has no model later in the run is '
             'possible and ends it, as in every run. The six CoreSight lock models of the staged PARAMS cover the key write that cpu_start makes for CPUs 1 to 5, and the CPU nodes\' acc-impl-reg and '
             'cpu-uttdbg-reg windows have no model, but the start path does not touch them (read on the host).')
PAGE_CAN = ('What the page can and cannot show. Besides the first five events of the monitor\'s log, the counts and the stop line (boot 25\'s lost two digits of C= to the runner\'s clip at 100 columns; '
            'this reporter prints C= on a line of its own when it would pass that column) and the first lines of the console ring as MON CON lines, the reporter of this record prints the monitor\'s report '
            'window words: the guest timer (MON TIM), the clock (MON CLK), the counters (MON CNT, MON EP), the acknowledged stores (MON ACK) and, at a stop, the guest\'s registers (MON GX), with the '
            'syndrome and the value of a store that a stage-2 fault stopped on (MON ESR, MON WVAL); section 5b is their legend. It prints them raw: a value is what the monitor stored, never a finding, '
            'and the readings that this record asks for are the classes of section 5d and no other. It does not print the rest of a panic text or the number of exceptions that the monitor let '
            'through (UNKNOWN to the page and the film). The page is at most 40 lines for payload 1, so a page with many registers leaves a line or two of the events and of the counters out '
            '(section 5b).')


ACK_COUNT = (('a prediction that nothing in this run can confirm or refute: the page does not print the count; 10 to 15 stores in all',
              'a prediction that this record\'s page can confirm or refute: its MON ACK line prints the count and the first eight stores; 10 to 15 stores in all'),
             ('this record\'s SNAPREP does not print the block, so how many stores it took is UNKNOWN to the page and the film, and no result of this record rests on it.',
              'this record\'s SNAPREP prints it (MON ACK, section 5b), and no result of this record rests on the count.'))


def extra_paragraphs(panel):
    """The paragraphs of section 3c that only this record has, after the command line's: rung 5's (the PARAMS word, the first acknowledge paragraph, what still stops the run, the panic path), with
    this record's reach of the acknowledge, its own account of the page, and the count that its reporter prints."""
    parts = r5.extra_paragraphs(panel)
    assert len(parts) == 6 and parts[2].startswith('What the acknowledge lets the guest reach.') and parts[5].startswith('What the page can and cannot show.'), 'the paragraphs of rung 5 have changed'
    assert parts[0].count('for rung 4\'s and this record only') == 1, 'the PARAMS paragraph of rung 5 has changed'
    ack = parts[1]
    for old, new in ACK_COUNT:
        assert ack.count(old) == 1, 'the acknowledge paragraph of rung 5 has changed'
        ack = ack.replace(old, new)
    return tuple(moved(part) for part in (parts[0].replace('for rung 4\'s and this record only', 'for the records of rungs 4, 5 and 6 only'), ack, ACK_REACH, parts[3], parts[4], PAGE_CAN))


def expect_text(run):
    """The paragraph of section 3c that stands where rung 3's and 4's what-the-run-may-end-as is: the prediction and its falsifiers are section 5d's, in the operator's words, and only there."""
    return ('What the run may end as. The prediction (INFERRED from the host analysis of boots 24 and 25: none of it has been seen with this word), its falsifiers and the other ends are in section '
            '5d, in the operator\'s words (WHAT THE RECORD PREDICTS, AND WHAT FALSIFIES IT), and what the film and the page decide is there as well; which end comes first, and when, is UNKNOWN. A restart '
            'of the Surface is NOT A RESULT for M1 (section 5a) and NOT PASSED for rung 6 (section 5d); whether a restart comes inside a box of this length is UNKNOWN (section 4). In every case the '
            'operator does nothing but film (section 5d).')


# ---- section 5d ------------------------------------------------------------------------------------------------------------------------------------------------------------------------
def section_5d(panel, wait_s):
    """Section 5d of the record, the operator's words: tools/sp11_stick_bundle_panel_rung6d.py, which takes its constants from this module (so it is imported here, when asked)."""
    import sp11_stick_bundle_panel_rung6d as d
    return d.section_5d(panel, wait_s)


# What section 3c and the sections before it cite that rung 5's table does not hold, by path:line (held to the sources, in both directions, by tools/test-sp11-stick-monitor-rung6-words.py): the
# guest timer's path in the monitor, which section 0a gives as the reading of boot 25's stop.
CITES = (
    ('probes/xnuboot/clock_timer.c', 195, 236, ('vcpu_of(c)->timer.ctl == 1 && !vcpu_of(c)->timer_pending', 'vcpu_of(c)->timer_pending = guest_timer_pending(', 'guest_interrupt_sync(c);')),
    ('probes/xnuboot/async.c', 95, 105, ('vcpu_of(c)->timer_irqs++;', 'timer_sync(c);')),
    ('probes/xnuboot/stage2.c', 401, 408, ('static void guest_interrupt_sync', 'timer_asserts++')),
    (r5.REPORTER, 256, 257, r5.REPORTER_DRIFT[(r5.REPORTER, 244, 245)][2]),       # the console ring's size on the page, where this record's reporter has it
    (r5.REPORTER, 281, 305, r5.REPORTER_DRIFT[(r5.REPORTER, 247, 271)][2]),       # the stop kinds' names
)
