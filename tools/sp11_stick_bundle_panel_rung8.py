# SPDX-License-Identifier: MIT
"""Rung 8 of the monitor entry run (docs/SP11-STICK-MONITOR.md section 20, K16; docs/SP11-M1-GUEST-PROFILES.md sections 5 and 7): the record for the boot after rung 7. Boot 27 (rung 7: the quiet
profile, cpu_boot_timeout_secs=120, a box of 120 s) ran the whole box through bsd_init to a wait for a root device and ended as MON STOP WATCHDOG, the first run to reach the end of bsd_init (OBSERVED
once). About 60 s of it were two 30 s waits of bsd_init for an RTC resource that the clock driver never publishes, because its first read of the PMU fails its parity check four times in a row
(about a thousand lines in all, with the other failing reads) (INFERRED; files/spmi-rtc-note.md of the session, read on the host). This record keeps the monitor, the pair, the reporter, the box, the command line and the device tree of rung 7 and changes
ONE thing in GUEST.DAT: three words of one device model of PARAMS, which bind the PMU's real-time clock at nub-spmi0 (the guest profile quiet-rtc, mark R-; probes/guestprofile_m1.py).

`--cpu-start ack --let-panic --boot-arg cpu_boot_timeout_secs=120 --panel console --time-box-s 120 --guest-profile j700ap-26A428-m1-quiet-rtc --sptm-hvc-calls 0` is the one combination that builds
it (tools/sp11_stick_bundle_m1.py and ..._chunks.py: ACK_FORMS, FORM_PROFILE, RUNG8_FORM), with the pair of ACK_PAIRS (rung 6's, and rung 7's); the guide's table PANEL takes its words by the key
`console+cpu_boot_timeout_secs=120+let_panic+j700ap-26A428-m1-quiet-rtc` (tools/sp11_stick_bundle_panel_rungs.py). The words that it shares with rung 7 are tools/sp11_stick_bundle_panel_rung7.py's, by
attribute (a hook that this module does not define is rung 7's), and where a sentence of rung 7's is kept with a change, `patched` asserts that it is in rung 7's words exactly once, so that a
change of those words cannot go unnoticed here. Section 5d is tools/sp11_stick_bundle_panel_rung8d.py's and the profile's paragraphs are made here (rung 7's, for the quiet board, and the clock's).
Every sentence that says what this guest is against rung 7's is made from the builder's own chunk comparison (tools/sp11_stick_bundle_rung8.py, `compare`). Boot 27's result is a keyed block, RUNG7
below: the operator's film as the coordinator read it (OBSERVED, one unit, one boot) and the host analysis of its clock lines (read on the host, a file and not a run; INFERRED where it says so).

Evidence: EXPERIMENTAL, hardware NOT RUN."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_bundle_chunks as ch  # noqa: E402
import sp11_stick_bundle_record_m1 as m1t  # noqa: E402
import sp11_stick_bundle_panel_rung2 as r2  # noqa: E402
import sp11_stick_bundle_panel_rung3 as r3  # noqa: E402
import sp11_stick_bundle_panel_rung5 as r5  # noqa: E402
import sp11_stick_bundle_panel_rung6 as r6  # noqa: E402
import sp11_stick_bundle_panel_rung7 as r7  # noqa: E402
import sp11_stick_bundle_panel_profile as pp  # noqa: E402
import sp11_stick_bundle_rung8 as r8b  # noqa: E402
import sp11_stick_bundle_windows as win  # noqa: E402

NUMBER = 8                                  # the rung's number, for the pointer to section 5d in the reading lines (tools/sp11_stick_bundle_panel.py)
PROFILE = ch.RUNG8_PROFILE                  # j700ap-26A428-m1-quiet-rtc
CPU_WAIT_S, CPU_PANIC_AT_S, BOX_S = r7.CPU_WAIT_S, r7.CPU_PANIC_AT_S, r7.BOX_S
BOOT27_RECORD = 'e42f451d...3b01'           # the approved record of rung 7: SHA-256 e42f451de33c2d44ec86f10036627aa129f00e6ae1c3edf526231fd42adb3b01 (r8b.RUNG7_RECORD)
BOOT27_TICKS = 2_307_553_890                # MON RET TICKS= of boot 27, in ticks of 19.2 MHz (OBSERVED once)
BOOT27_S = f'{BOOT27_TICKS / 19.2e6:.3f}'
BOOT27_COUNTS = 'MON COUNTS EVENTS=512 TRAPS=195787 ABORTS=0 STEPS=2007 CALLS=65009 DROPPED=129122'
BOOT27_STOP = dict(pc=0xFFFFFE000B8716A8, a=0x96000047, b=0xFFFFFE110E00C040, c=0xFFFFFE000BA87EA0)      # MON STOP WATCHDOG PC= A= B= and MON STOP C= of boot 27 (OBSERVED once)
BOOT27_SPSR = '0X604003C4'
BOOT27_TIM = 'MON TIM IRQS=1560 ASSERTS=3134 CLEARS=3134 VECTORS=1519 CTL=0X1 CVAL=0XEDFD7657'
BOOT27_LABEL = r8b.RUNG7_LABEL
BOOT27_GUEST = r8b.RUNG7_GUEST[:16].upper()
BOOT27_LAST = ('Waiting on <dict ID="0"><key>IOProviderClass</key><string ID="1">IOMedia</string><key>Content</key><string ID="2">Apple_HFS</string></dict>')
STAND_HEAD = '0a. Where things stand (boots 20, 21, 23 to 27; 2026-11-18/20; one unit, one boot each; OBSERVED unless INFERRED)'
HEADER = ('3c. The panel as the kernel\'s console with the CPU-start stores acknowledged, let_panic, a wait of 120 s and a quiet guest with the PMU\'s clock bound (rung 8: D9 panel handed, serial=2, '
          'cpu_boot_timeout_secs=120, let_panic set, guest profile quiet-rtc, stock kernel; docs/SP11-STICK-MONITOR.md section 20)')
VERDICTS = (*r7.VERDICTS, 'KERNEL WAITING')     # the verdicts that this record's keyed blocks may carry: boots 24 to 27
PARAMS_ROW, CITES = r7.PARAMS_ROW, r7.CITES
patched = r7.patched


def keyed_lines(block, what):
    """The paragraphs of section 0a that are one run's result: a keyed block (rung 7's rules: ASCII, no claim without a label, never the word that the project keeps for what an independent run
    showed), with the verdicts of boots 24 to 27. AssertionError for a verdict that this record is not for."""
    assert block['verdict'] in VERDICTS, f'the verdict of {what} is not one that rung 8 is the record for'
    for paragraph in block['words']:
        assert paragraph.isascii() and pp.NEVER not in paragraph, 'a paragraph of a keyed block is not ASCII or uses the word that M1 records never use'
        assert any(label in paragraph for label in ('OBSERVED', 'INFERRED', 'UNKNOWN')), 'a paragraph of a keyed block has no label'
    return (*m1t.wrap(f'{what} (record {block["record"]}, {block["boot"]}; one unit, one boot; the operator\'s film, read by the coordinator): {what.upper()} {block["verdict"]}.'),
            *(line for paragraph in block['words'] for line in m1t.wrap(paragraph)))


# KEYED BLOCK of boot 27: the film as the coordinator read it (files/result-boot27.md of the session: 152.4 s, 4K, 30 frames a second, read with ffmpeg at 1 frame a second and 4K crops of the
# report page, and a machine OCR of 150 frames that cannot read the skewed or dim ones, so the digits of the page were read by eye) and the host reading of the film's clock lines (the
# kernelcache file, the loader and the monitor's ELF: a file and not a run). `record` is the run's approved record and `verdict` its class of that record's section 5d.
RUNG7 = dict(record=BOOT27_RECORD, boot='boot 27', verdict='KERNEL WAITING', words=(
    f'The call ran its whole box: TICKS={BOOT27_TICKS} at 19.2 MHz ({BOOT27_S} s; the box was {120 * 19_200_000:,} ticks), {BOOT27_COUNTS}, then MON STOP WATCHDOG PC={r2.pc_text(BOOT27_STOP["pc"])} '
    f'A={r3.page_hex(BOOT27_STOP["a"])} B={r3.page_hex(BOOT27_STOP["b"])} and MON STOP C={r2.pc_text(BOOT27_STOP["c"])}, MON EXIT SPSR={BOOT27_SPSR}, with REGS RESTORED=YES and GIC RESTORE CLEAN=YES, '
    'on a clean report (OBSERVED once; the operator\'s film, read by the coordinator). M1 PASSED by section 5a. No panic text was anywhere in the film (the word is only in the label and in PANIC=0), '
    'and no "RTC did not show up", "getGMTTimeOfDay" or "!BSD" '
    'line. The stop at the scaler\'s window of boot 26 did not return and no S2_FAULT of any address came: the quiet profile took (OBSERVED once).',
    f'The log: the guest\'s text from the MON CALL line, the SPMI parity-error lines, the PMU lines, the USB lines of boots 25 and 26 about 31 s in, no new line for about 29 s and, at about 60 s, the whole '
    f'bsd_init block in one burst (the block that boot 26 never finished: mbinit, dlil_init, the ifnet attaches, apfs, NFS, ASP, L2TP, `Load request for com.apple.nke.pptp failed: -603946991`, '
    f'`IOMFB: service matched: AppleDCPExpert`, `Couldn\'t alloc class "AFKResource"`), ending in the last line of the log, `{BOOT27_LAST}`. The panel did not scroll for the next 59 s (the first and '
    'last lines of the screen were the same in every frame), and the report page came with the stop at 120.2 s. The kernel was waiting for a root device, an IOMedia of content Apple_HFS (OBSERVED once; '
    'that nothing can answer it in this guest, which has no storage, is INFERRED). "memorystatus: System is unhealthy" with 9,094 free pages of 16 KiB and "no victim found (action: 10)" were on the film '
    'again and no panic followed.',
    'The clock driver\'s own start lines ARE on the film (OBSERVED once): about 7 s in, `AppleDialogSPMIPMU::start: Primary PMU detected`, '
    '`AppleARMRTC started!`, `AppleDialogSPMIPMURTC started!` and `Read RTC offset from leg_scrpad`, and then the PMU read at address f704 (count 4) failed four times (retry 3 to 0) with `parity error: '
    'parity_rcv=0x0000 parity_exp=0x000F` and `[0..3] = 0x00 parity=BAD`; a second family (address 8800, count 5, parity_exp 0x001F) and a read of 56 bytes failed in the same way, about 1,000 lines in '
    'all. The messages of the two 30 s waits that rung 7\'s record expected only on the monitor\'s ring were not on the film (OBSERVED once). `AppleARMRTC registering service!` is on no frame that the '
    'OCR read (a film is not proof of absence). So the clock driver reaches its first read of the PMU and does not get past it (INFERRED: it would otherwise register the IORTC resource), and the '
    'two 30 s waits of bsd_init for that resource, 0.4 + 30 + 30 s, end at the burst at about 60 s (INFERRED timing, from code read on the host).',
    f'The page besides (OBSERVED once): the PC is the instruction after a WFI (SPSR {BOOT27_SPSR}: EL1 with D, A, I and F masked, as in boot 25), so the kernel was idle at the box\'s end; A= and B= are '
    f'its last EL1 syndrome, 0X96000047 (a translation fault at level 3 on a write) at a kernel address that the host analysis places beyond the 384 MiB linear map, INFERRED to be a handled first touch of '
    f'a pageable allocation (no panic text, MON CNT PANIC=0 UNDEF=0); {BOOT27_TIM}, so the guest\'s timers are delivered, 12.6 FIQ vectors a second; MON ACK COUNT=15, the CPU-start stores dropped. The '
    'root device\'s silent loop of 60 s waits would have its first timeout at about 120 s, at the box\'s end, and the CPU-boot panic at about 122 s did not come inside the box (INFERRED, as designed).'))


# Boots 24 to 26 in the length that this record has room for: the facts that its aim leans on (the stop at the instruction after a WFI, the timers, the two 30 s waits of bsd_init for the RTC
# resource, the release of the waiters of IOSurfaceRoot) and the numbers of the pages. Rung 7's record carries them at length (tools/sp11_stick_bundle_panel_rung7.py RUNG5 and RUNG6).
RUNG4 = dict(record=r5.BOOT24_RECORD, boot='boot 24', verdict='PANIC TEXT', words=(
    'The panel showed the kernel\'s panic text (OBSERVED once; the coordinator\'s transcription of the film): panic(cpu 0 caller 0xfffffe00097ddef0): "ApplePMGR: virtual void '
    'ApplePMGR::enableCPUComplex(UInt32, bool):4861 REQUIRE failed: complex < _cpuComplexCount" @ApplePMGR.cpp:4861, then "Nested panic detected - entry count: 4", "Nested panic count exceeds limit 5, '
    f'machine will reset or spin" and "wdog: panic chipreset"; its first lines were on the page as MON CON lines. The call returned {r5.BOOT24_S} s in with MON STOP S2_FAULT '
    f'PC={r2.pc_text(r5.BOOT24_STOP["pc"])} A={r3.page_hex(r5.BOOT24_STOP["a"])} B={r3.page_hex(r5.BOOT24_STOP["b"])} C={r2.pc_text(r5.BOOT24_STOP["c"])}, a 32-bit READ at a translation fault at level 1, '
    'INFERRED to be the kernel\'s read in its panic chip-reset path at a register of the watchdog node (read on the host, a file and not a run). M1 PASSED by section 5a.',
    'The cause, read on the host (INFERRED): with cpus=1 XNU has one cluster and the PMGR driver\'s start enables two complexes, so the second asserts; boots 21 and 25 to 27, with six CPUs and two '
    'clusters, passed this driver. Each nested panic repeats because the kernel\'s panic-record routine stores through a pointer that stays NULL when the guest\'s device tree has no panic-log region '
    '(INFERRED), and the monitor lets the kernel\'s own page faults through (probes/xnuboot/trap_dispatch.c:370-387): the repetition is the kernel\'s.'))
RUNG5 = dict(record=r6.BOOT25_RECORD, boot='boot 25', verdict='WATCHDOG, NO TEXT', words=(
    f'The call ran its whole box: TICKS={r6.BOOT25_TICKS} at 19.2 MHz ({r6.BOOT25_S} s), {r6.BOOT25_COUNTS}, then MON STOP WATCHDOG PC={r2.pc_text(r6.BOOT25_STOP["pc"])} A={r3.page_hex(r6.BOOT25_STOP["a"])} '
    f'B={r3.page_hex(r6.BOOT25_STOP["b"])} and MON EXIT SPSR={r6.BOOT25_SPSR}, with GIC RESTORE CLEAN=YES, on a clean report; no panic text; the log went about 35 s of drivers (the SPMI and PMU lines with '
    'their failed register reads, "AppleARMRTC started!", "AppleDialogSPMIPMURTC started!", the USB "failed to find" lines at 28 to 35 s) and was silent for the last 25 s (OBSERVED once; the operator\'s '
    'film, read by the coordinator). M1 PASSED by section 5a. Rung 5\'s falsifiers did not occur: no ApplePMGR assertion, no stop at 0X300734004..0X30073400C, no "cpu 1 failed to boot" text, and the '
    'stop at the video decoder\'s window that the host analysis expected early did not come (OBSERVED once).',
    'The decode, read on the host in the kernelcache and the monitor\'s sources (a file and not a run; INFERRED where it says so): PC= is the instruction after a WFI in XNU\'s idle path and SPSR 0X604003C4 '
    'is EL1 with D, A, I and F masked, so CPU 0 was idle with every interrupt masked at the end of the box; ESR 0X9600004F is a data abort, a write, a permission fault at level 3, INFERRED to have been '
    'handled since no panic followed. Boot 26 answered what the silence was: the guest\'s timer interrupts ARE delivered, so it was a kernel waiting, the second of two 30 s waits that bsd_init makes for '
    'the RTC resource (INFERRED from code read on the host).'))
RUNG6 = dict(record=r7.BOOT26_RECORD, boot='boot 26', verdict='NEW STOP', words=(
    f'The call returned after TICKS={r7.BOOT26_TICKS} at 19.2 MHz ({r7.BOOT26_S} s; the box was {120 * 19_200_000:,} ticks), {r7.BOOT26_COUNTS}, then MON STOP S2_FAULT PC={r2.pc_text(r7.BOOT26_STOP["pc"])} '
    f'A={r3.page_hex(r7.BOOT26_STOP["a"])} B={r3.page_hex(r7.BOOT26_STOP["b"])} C={r2.pc_text(r7.BOOT26_STOP["c"])}, MON EXIT SPSR={r7.BOOT26_SPSR}, {r7.BOOT26_ESR}, {r7.BOOT26_WVAL}, with REGS '
    'RESTORED=YES and GIC RESTORE CLEAN=YES, on a clean report (OBSERVED once; the operator\'s film, read by the coordinator). M1 PASSED by section 5a. The log went further than in any earlier run: the SPMI '
    'and PMU lines, the USB lines about 31 s in, no new line for about 28.5 s and, at about 60.4 s, the first block of bsd_init (mbinit, dlil_init, the ifnet attaches, apfs, NFS, ASP, L2TP), cut off at '
    'the stop; no panic text (OBSERVED once).',
    f'The decode, read on the host in the kernelcache and the guest\'s device tree (a file and not a run; the names INFERRED from literals): PC= is in the scaler driver\'s HAL init, a 32-bit write of 1 at '
    f'offset 4 of the first window of /device-tree/arm-io/scaler0 (reg 0X{win.SCALER[0][0]:X}, size 0X{win.SCALER[0][1] - win.SCALER[0][0]:X}), taken when the hardware version that the device tree gives '
    '(22) is below 0X1A. No model of the monitor covers it, so the monitor failed closed (DFSC 5, a translation fault at level 1). Nine kexts (the scaler, AVD, AVE2, JPEG, ProRes, IOMFB, IOMFB-DCP, '
    'IOGPUFamily and the camera driver) wait for IOSurfaceRoot, which bsd_init publishes (INFERRED), so they are released together when it has run.',
    f'{r7.BOOT26_TIM} (OBSERVED once): the guest\'s timers work, with an armed deadline about 9.6 ms ahead (INFERRED), so the silence before bsd_init was a kernel waiting: the second of two 30 s waits in '
    'bsd_init for the RTC resource (IOKitInitializeTime, then the calendar\'s getUTCTimeOfDay: 0.4 + 30 + 30 = 60.4 s; INFERRED timing from code read on the host). The resource is never published '
    '(INFERRED) and the guest\'s device tree has no rtc node. MON ACK COUNT=15 (OBSERVED once): three stores for each of CPUs 1 to 5, dropped. "memorystatus: System is unhealthy" with 8,838 free pages '
    'of 16 KiB was on the film (a warning for a kernel-only run, no panic path read on the host).'))
BLOCKS = {'Rung 4': RUNG4, 'Rung 5': RUNG5, 'Rung 6': RUNG6, 'Rung 7': RUNG7}


# ---- section 0 -------------------------------------------------------------------------------------------------------------------------------------------------------------------------
DECIDES = ('APPROVING THIS TEXT ALSO DECIDES THREE THINGS, THE FIRST OF WHICH THE OPERATOR HAS ALREADY CHOSEN IN THE APP FOR RUNG 6. (1) D5\'\': A TIME BOX OF 120 s, which the operator chose in the app on '
           '2026-11-19 for rung 6 ("let\'s go with 120 seconds", the answer to the question whether its box should be 120 s or 60 s); rungs 6 and 7 ran inside it '
           f'(61.6 s and {BOOT27_S} s of the 120, the whole box: section 0a), and this record asks the same box, so that approving this text confirms it for this record: AC power REQUIRED, one CPU '
           'loaded for up to 120 s, no temperature readout, and the exposure sums of section 0b and section 4. Boot 27\'s CPU was idle for about the last 60 s of its box (INFERRED: the kernel was waiting), '
           'so the box may again be spent mostly idle. (2) A GUEST THAT IS RUNG 7\'S, BYTE FOR BYTE, BUT FOR THREE WORDS IN ONE DEVICE MODEL OF ITS PARAMS CHUNK: the command line has cpu_boot_timeout_secs=120 '
           'and the device tree is the profile quiet\'s, 43 nodes withdrawn by data and never faked, both as in boot 27, which ran them for 120.2 s without a stop at a withdrawn device (OBSERVED once). '
           'The three words bind the PMU\'s real-time clock at nub-spmi0, the SPMI controller of the PMU, in place of the empty bus that rungs 6 and 7 gave it (the guest profile quiet-rtc: EXPERIMENTAL, a '
           'virtual board description). The clock model is the monitor\'s own, which is in the monitor binary that ran in boots 25 to 27 (read on the host in its ELF, a file and not a run), and the '
           'guest\'s calendar then starts at a placeholder instant, 2026-11-20T00:00:00Z, plus the run\'s own time: not the unit\'s time, and what the kernel does with a wrong date is UNKNOWN. (3) WHAT THE '
           'GUEST CAN REACH is rung 7\'s: the existing stage-2 map and the monitor\'s models and report window, with no new register, no new system-register class and no new store class (read on the host in '
           'compat/cpu-start-ack.h, a file and not a run); the controller\'s windows were already in its reach as an empty bus (read on the host in probes/guestprofile.py, a file and not a run) and now '
           'answer as a clock (section 3c), and with IORTC expected to be published within seconds (INFERRED) everything that bsd_init releases starts about 50 s earlier than in boot 27, in which it ran '
           'for the last 60 s of the box without a stop (OBSERVED once); this approval is for that reach for up to the box. The monitor, its pair, the panel as the console, the monitor\'s let_panic word, '
           'the timer reporter and this tree and word ran in boot 27; the clock model did not.')
RUNG = ('each of rungs 1 to 7, the first run with the panel withheld, the panel as the console (rung 2, boot 21), the same with cpus=1 (rung 3, boot 23), with cpus=1 and let_panic (rung 4, boot 24), '
        'with the CPU-start stores acknowledged, six CPUs and a 60 s box (rung 5, boot 25), with a wait of 90 s, a box of 120 s and another reporter (rung 6, boot 26) and with a wait of 120 s and the '
        'guest profile quiet (rung 7, boot 27), whose records have their own approvals, spent: this record is rung 8, rung 7 with the guest profile quiet-rtc, and no approval covers another\'s boot')
CONSOLE = patched(r7.CONSOLE, ('its device tree is the profile quiet\'s,', 'its device tree is the profile quiet\'s, its PARAMS bind the PMU\'s clock,'),
                  ('(boots 21, 24, 25 and 26:', '(boots 21, 24, 25, 26 and 27:'),
                  ('do beyond boot 26\'s 61.6 s is INFERRED and has not been seen', 'do with the clock bound is INFERRED and has not been seen (boot 27 saw them for 120.2 s without it)'))
RAN = 'ran in its acknowledging build three times, for 60 s (boot 25), for 61.6 s (boot 26) and for 120.2 s (boot 27: section 0a), and in its plain build in boots 20, 21, 23 and 24.'
IDLE = ('the extra seconds are there for what follows bsd_init, which the clock is expected to bring to within about 10 s of the MON CALL line (INFERRED): the waiters of IOSurfaceRoot that it releases '
        '(the jpeg, AVE and display drivers in this tree) and the root device\'s silent loop of 60 s waits, whose first timeout then falls at about 70 s, inside the box (section 5d)')


def why_lines(real):
    """Section 0's paragraph for this record: why rung 8, the one change, the prediction in a line and where the rest is. Boot 27's facts are section 0a's (stand_extra)."""
    d = real['panel_facts']['rung8']['diff']
    return m1t.wrap(f'WHY RUNG 8. Rung 7 (boot 27, record {BOOT27_RECORD}) ran its whole box of 120 s ({BOOT27_S} s), went through bsd_init to a wait for a root device and was ended by the monitor\'s watchdog: the '
                    f'quiet profile took (no stop at a withdrawn device), and a guest without storage has no root device to wait for (INFERRED). The first 60 s of its run held two 30 s '
                    'waits of bsd_init for the RTC resource, which is never published because the clock driver\'s first read of the PMU, at address f704, fails its parity check four times in a row (about a thousand lines in all, with the other failing reads) '
                    '(INFERRED: from the film and from the kernelcache read on the host, files/spmi-rtc-note.md of the session). The monitor holds a model of that clock (the A16 chain\'s own) and the M1 '
                    'base board binds the controller of the PMU as an empty bus, whose reply carries no parity flag (read on the host in the monitor\'s ELF and the loader: a file and not a run). THIS RECORD '
                    'CHANGES ONE THING FROM RUNG 7, IN GUEST.DAT ONLY: THE PMU\'S REAL-TIME CLOCK IS BOUND (the guest profile quiet-rtc), which is three words of one of the '
                    f'{pp.MODELS} device models of PARAMS (the kind, 9 to 48, the seed and the contract word of the clock: {d["words"][1][1]:,} microseconds, {d["seed"]}) and no byte of the device tree, '
                    'the command line or the monitor. THE MONITOR, ITS PAIR, THE REPORTER, THE let_panic WORD, THE COMMAND LINE, THE DEVICE TREE AND THE BOX ARE RUNG 7\'S (D5\'\': 120 s, AC power, one CPU '
                    'running, no temperature readout: (1) above). THE AIM, THE PREDICTION, ITS FALSIFIERS AND WHAT THE FILM DECIDES are in section 5d, in the operator\'s words (INFERRED).')


def stand_extra(real):
    """The paragraphs that section 0a gets after boot 20's: rung 7's (boots 21 and 23 condensed), the keyed blocks of rungs 4 to 7 (rungs 5 and 6 in the length of RUNG5 and RUNG6 here) and the stick
    after the boots."""
    lines = r7.stand_extra(real)
    first = next(i for i, line in enumerate(lines) if line.startswith('   Rung 4 (record'))
    at = next(i for i, line in enumerate(lines) if line.startswith('   The stick after boots'))
    stick = patched(lines[at:], ('The stick after boots 21, 23, 24, 25 and 26 was', 'The stick after boots 21, 23, 24, 25, 26 and 27 was'),
                    ('boot 26\'s by a read on the host of the stick as returned: its 18 staged files byte-identical and the Shell\'s marker the only new file)',
                     'boots 26\'s and 27\'s by a read on the host of the stick as returned: their 18 staged files byte-identical and the Shell\'s marker the only new file)'))
    return (*lines[:first], *(line for what, block in BLOCKS.items() for line in keyed_lines(block, what)), *m1t.wrap(stick))


def hazard(real, box_s, wait_s):
    """The panel's bullet of 0b: the console on the panel is boots 21's, 24's, 25's, 26's and 27's; the path of the guest past the acknowledged stores is boot 27's for its 120.2 s."""
    return patched(r7.hazard(real, box_s, wait_s),
                   ('(OBSERVED once: boots 21, 24, 25 and 26)', '(OBSERVED once: boots 21, 24, 25, 26 and 27)'),
                   ('in boot 26 the kernel went on past them for 61.6 s, through a silence of about 28.5 s (INFERRED: the second of two 30 s waits for an RTC resource that is never published) into '
                    f'bsd_init, and stopped at a device that has no model (OBSERVED once); what it does in {box_s} s with the quiet tree is UNKNOWN (it may wait, stop at a later device, panic or spin).',
                    'in boot 27 the kernel went on past them for the whole box, through a silence of about 29 s (INFERRED: the second of two 30 s waits for an RTC resource that is never published) into bsd_init '
                    'and to a wait for a root device (OBSERVED once); what it does with the clock bound, which should publish that resource, is UNKNOWN (it may wait, stop at a device that has no model, '
                    'panic or spin).'),
                   (f'the longest run so far was boot 26\'s 61.6 s, whose film showed no restart of the Surface to its end, 92 s (OBSERVED once), and a run of {box_s} s is UNTESTED.',
                    f'the longest run so far was boot 27\'s {BOOT27_S} s, whose film showed no restart of the Surface to its end, 152 s (OBSERVED once): a run of {box_s} s has been seen once, and not with '
                    'the clock bound.'))


def step4(guest, label):
    """5c, step 4: the programs' digits that tell this bundle from rung 7's (GUEST.DAT's digits, PIN.DAT's and PAYLOAD.MAN's differ; SNAPMON.BIN and SNAPREP.BIN are the same), compared on the runner's lines."""
    return (f' The monitor\'s and the reporter\'s digits are rung 6\'s and rung 7\'s (boot 27 had INDEX=0 ... SHA={r7.BOOT26_MONITOR} and INDEX=1 ... SHA={r7.BOOT26_REPORTER}: the same; a page whose INDEX=1 '
            f'line says SHA={r7.BOOT25_REPORTER} has rung 5\'s reporter, which prints none of the lines of section 5b that this record is for). Then compare, on the same line, INDEX=2 ... '
            f'BYTES={guest["size"]} SHA={guest["sha256"][:16].upper()} (GUEST.DAT; rung 7\'s, which boot 27 ran, is the same size with SHA={BOOT27_GUEST}, read on the host from its bundle) and, on the '
            f'SNAPMON STAGE=PIN_OK line, LABEL={label} CHUNKS={guest["chunks"]} (boot 27\'s page had LABEL={BOOT27_LABEL} CHUNKS=46: rung 7\'s bundle has the same programs, the same chunk count and the '
            'same GUEST.DAT size and differs in the GUEST.DAT digits, the label and the PIN.DAT digits; this record cannot name its own PIN.DAT SHA, which depends on this record\'s SHA-256).')


def guide_words(base):
    """The guide's PANEL entry of this record: rung 7's (`base`), with the words that this module says again, and the legend of the reporter's page (rung 7's)."""
    return {**base, 'decides': DECIDES, 'hazard': hazard, 'rung': RUNG, 'why': why_lines, 'stand': stand_extra, 'stand_head': STAND_HEAD, 'console': CONSOLE, 'ran': RAN, 'step4': step4,
            'idle': IDLE, 'restart': RESTART, 'restart_c': RESTART_CONTROL, 'stop_seen': STOP_SEEN, 'wd_exit': WD_BOXES + ', one unit, one boot each', 'wd_box': WD_BOXES}


# The time box ending a run (MON STOP WATCHDOG): rung 7's record says it of boot 20 and of boots 20 and 25; boots 25 and 27 ended so as well.
WD_BOXES = 'OBSERVED: boots 20, 25 and 27'
STOP_SEEN = {**r7.STOP_SEEN, 'WATCHDOG': 'OBSERVED three times (boots 20, 25 and 27, one unit, one boot each).'}


# The two sentences that every record with a box longer than D5's says of the restart risk (tools/sp11_stick_bundle_record_m1_guide.py restart_risk_words and restart_control_words): boot 27 ran
# the stock kernel for a whole box of 120 s and its film showed no restart to its end.
RESTART = ('Inside a box of 120 s with the stock kernel running, whether the Surface restarts was seen once, in boot 27: none, to the end of its film, 152 s (OBSERVED once); with the clock bound the '
           'kernel\'s course differs from that run\'s (UNKNOWN).')
RESTART_CONTROL = ('That control ran no guest; the stock kernel ran inside a box of 120 s once, in boot 27, without a restart to the end of its film (OBSERVED once). The clock changes what the kernel '
                   'does in that time (UNKNOWN), and the argument above does not reach it.')


# ---- section 3 and 3c ------------------------------------------------------------------------------------------------------------------------------------------------------------------
def console_text(panel):
    """The Console paragraph of section 3c for this record: rung 7's, with boot 27 among the runs that showed the console on the panel."""
    return patched(r7.console_text(panel), ('What the operator saw in boots 21, 24, 25 and 26 (OBSERVED once each)', 'What the operator saw in boots 21, 24, 25, 26 and 27 (OBSERVED once each)'))


def label(panel, label_text):
    """The label paragraph of section 3c: rung 7's, with the mark of this record's profile."""
    return patched(r7.label(panel, label_text), (f'the mark {pp.PROFILES["quiet"][0]} of the guest profile quiet (rung 6\'s label, {r7.BOOT26_LABEL}, had no mark)',
                                                 f'the mark R- of the guest profile quiet-rtc (rung 7\'s label, {BOOT27_LABEL}, had the mark {pp.PROFILES["quiet"][0]})'))


def kind_lines(panel):
    """The lines that section 3 adds after the kind's own line: rung 7's, with the clock in the guest's description."""
    return m1t.wrap(patched(r7.kind_lines(panel), ('the device tree of the guest profile quiet,', 'the device tree of the guest profile quiet and the PMU\'s clock bound in PARAMS (quiet-rtc),')))


def in_short(panel):
    """The summary of section 3c."""
    d = panel['rung8']['diff']
    return ('In short. This record is rung 8, after boot 27 (rung 7), which ran the whole box of 120 s through bsd_init to a wait for a root device, the first run to get that far '
            f'(section 0a): the monitor, the acknowledge, let_panic, the panel, the six CPUs, the pair, the box of {panel["run"]["box_s"]} s (D5\'\'), the command line and the device tree '
            f'({d["nodes"][1]} of {d["nodes"][0]} nodes) are rung 7\'s; the one change is the PMU\'s real-time clock, bound in three words of one device model of PARAMS. What it expects, what '
            'falsifies that and what counts as a result are in section 5d, in the operator\'s words; this section is the evidence for them.')


def programs(panel):
    """The sentence of the introduction on the programs: the pair is rung 6's and rung 7's."""
    return patched(r7.programs(panel), ('rung 7 is built with rung 6\'s pair and with no other', 'rung 8 is built with rung 6\'s pair, as rung 7 was, and with no other'))


def intro(panel):
    """The introduction of section 3c: rung 7 is the run before; this record changes three words of the guest's PARAMS chunk and nothing else."""
    box_s = panel['run']['box_s']
    return (f'Rung 7 is the run before this one: the panel as the console with the CPU-start stores acknowledged, let_panic, cpu_boot_timeout_secs=120, the guest profile quiet and a box of 120 s, '
            f'record {BOOT27_RECORD}, boot 27 (section 0a); its approval is spent. This record is rung 8: its guest is rung 7\'s with the PMU\'s real-time clock bound (the guest profile quiet-rtc: three '
            f'words of one device model of PARAMS); its monitor, its reporter, its box ({box_s} s, D5\'\', which approving this text confirms: section 0), its let_panic word, its command line and its '
            'device tree are rung 7\'s. It is not decided: approving the SHA-256 of this record is that decision, and it covers ONE boot of this stick (section 5c, THIS APPROVAL COVERS ONE BOOT); '
            f'rung 7\'s approval covers none of it. {programs(panel)} SNAPMON.BIN and SNAPREP.BIN are rung 7\'s, byte for byte. GUEST.DAT (its PARAMS chunk), PIN.DAT (the label and the digests) and '
            'PAYLOAD.MAN, which lists them, differ from rung 7\'s. Section 1b has the programs\' digests and the NV2 record, which is the first run\'s. Section 5 has the rule that rung 2 had.')


def other_differences(panel):
    """The sentence of section 3c on what else differs: made from the builder's own chunk comparison with rung 7's guest (tools/sp11_stick_bundle_rung8.py compare)."""
    d = panel['rung8']['diff']
    old, new = d['words']
    size, nodes, dt = d['guest_bytes'], d['nodes'], d['dt_bytes']
    return ('Other differences from the withheld guest follow from the panel\'s address, which lies below the bank: the guest\'s frame map and tables are wider (docs/SP11-STICK-REAL.md section 2: 46 chunks '
            f'against 44 in the host run that table records). From rung 7\'s guest (GUEST.DAT {BOOT27_GUEST}..., {size[0]:,} bytes, {d["count"]} chunks, the one that boot 27 ran) this one differs in '
            f'{len(d["differing"])} chunk, which the builder compares by SHA-256 with the table of rung 7\'s own bundle that it pins, and it refuses any other difference (PANEL_MISMATCH): chunk '
            f'{d["differing"][0]}, PARAMS ({d["params_bytes"]:,} bytes), in three words of one of its {pp.MODELS} device models, the one at index {d["model"]} (offset 0X{d["model_at"]:X} of the chunk, a '
            f'window of 0X{d["model_span"]:X} bytes at 0X{r8b.CLOCK_BASE:X}), the SPMI controller of nub-spmi0: the kind, {old[0]} (the empty bus) in rung 7\'s and {new[0]} (the PMU\'s clock) here; '
            f'config[3], {old[1]} (the queue depth) in rung 7\'s and {new[1]:,} here, the seed of the clock, the instant {d["seed"]}; and config[4], {old[2]} in rung 7\'s and 0X{new[2]:X} here, the '
            f'clock model\'s contract word (with those three put back to rung 7\'s values the chunk\'s SHA-256 is rung 7\'s). The other {d["count"] - len(d["differing"])} chunks are rung 7\'s byte for '
            f'byte, HANDOFF (the boot_args page, with the command line and the length of the device tree) and DEVICE_TREE ({dt[1]:,} bytes, {nodes[1]} nodes) among them, and so are the box, the '
            f'let_panic word, the other {pp.MODELS - 1} models and the programs\' places. GUEST.DAT is as long as rung 7\'s ({size[1]:,} bytes) and has its own SHA-256 (docs/SP11-STICK-REAL.md section 2).')


def word_text():
    """The paragraph of section 3c on the command line: rung 7's (the word is rung 7's), with boot 27 among the runs that went on on the boot CPU."""
    return patched(r7.word_text(), ('cut a run that has not yet shown where the quiet guest ends;', 'cut a run that had not shown where the guest ends (boot 27 did: a wait for a root device);'),
                   ('boots 25 and 26 went on on the boot CPU (OBSERVED once each)', 'boots 25, 26 and 27 went on on the boot CPU (OBSERVED once each)'))


ACK_REACH = patched(r7.ACK_REACH, ('boot 25 ran 60 s past it and boot 26 61.6 s (OBSERVED once each)', 'boot 25 ran 60 s past it, boot 26 61.6 s and boot 27 120.2 s (OBSERVED once each)')) + (
    f' The controller of the PMU, whose model is the one thing that changes, was already in the guest\'s reach as an empty bus (0X{r8b.CLOCK_BASE:X} and the next 0X18000 bytes of IPA): the clock '
    'model serves the clock\'s own registers and refuses every other transfer with the empty bus\'s reply (read on the host in compat/device/device_rtc.c and compat/pmu-rtc.h, a file and not a run).')
CLOCK_TEXT = ('THE CLOCK (INFERRED; read on the host in the kernelcache, the loader and the monitor\'s ELF, a file and not a run: files/spmi-rtc-note.md of the session). Boot 27\'s film shows the clock '
              'driver\'s first read of the PMU, 4 bytes at address f704, failing its parity check (parity_rcv=0x0000, parity_exp=0x000F). The check is the kernel\'s: a read of n bytes passes when '
              'bits 31:16 of the first word of the controller\'s response, under a mask, equal (1 << n) - 1, and the data words are not looked at, so zero data passes. The empty bus that rungs 6 and '
              '7 bind answers with the header and no flag. The clock model of the monitor answers the clock driver\'s reads with the flag: the 47-bit counter at f802 (32,768 Hz: the seed plus '
              'the guest\'s own time, read only) and the two offset fields at f704 and f715, which the driver takes with any value, zero included; the alarm registers, the PMU\'s SOCD header at '
              '8800, the scratch pad and every other slave are refused, with the empty bus\'s reply and a failure bit in the controller\'s flags, so part of the parity noise stays on the film. After '
              'both offset reads the driver logs `registering service!` and publishes IORTC, and bsd_init\'s waits for it end (INFERRED). The seed is a placeholder, 2026-11-20T00:00:00Z, not the '
              'unit\'s time: a stick has no host clock, and a build that read one would not regenerate. What later code does with that calendar is UNKNOWN.')


def profile_paragraphs(panel):
    """The paragraphs of section 3c on the guest profile (the profile text of tools/sp11_stick_bundle_panel_profile.py has none for quiet-rtc: the quiet board's are rung 7's, taken from it for the
    profile quiet, and the clock's are here): what the guest is against rung 7's, the nodes withdrawn, the clock, how to tell it apart, what is not claimed, the hazard and the result."""
    d, r = panel['rung8']['diff'], panel['rung8']
    quiet = pp.profile_text(pp.PREFIX + 'quiet', dict(kind='rung 7\'s form', let_panic=True, models=pp.MODELS, changes='-'))
    assert len(quiet) == 6 and quiet[1].startswith('WITHDRAWN:') and quiet[3].startswith('NOT CLAIMED:') and quiet[4].startswith('HAZARD') and quiet[5].startswith('RESULT'), 'the profile text of quiet has changed'
    changes = (f'{pp.LEAD}: {pp.PREFIX}quiet-rtc, mark R-, composed with rung 7\'s form (rung 8): the quiet profile\'s device tree and boot_args byte for byte ({d["nodes"][1]} of {d["nodes"][0]} nodes) '
               f'and the PMU\'s clock bound at nub-spmi0. Against rung 7\'s own guest it differs in {len(d["differing"])} of {d["count"]} chunks, which the builder compares by SHA-256 (Other differences, above): the '
               f'PARAMS chunk, in three words of one device model; the DEVICE_TREE chunk ({d["dt_bytes"][1]:,} bytes), the HANDOFF page, the {pp.MODELS - 1} other models and the programs are rung 7\'s.')
    tell = (f'TELLS IT APART (INFERRED; no profile bundle but rung 7\'s has been booted): PIN label {r["label"]}; INDEX=2 on the PAYLOAD LOADED line shows BYTES={r["guest_bytes"]} SHA={r["guest_sha16"]} '
            f'(rung 7\'s: the same size, SHA={BOOT27_GUEST}); INDEX=3\'s SHA is PIN.DAT\'s, which holds this record\'s own SHA-256: no record names it.')
    claimed = patched(quiet[3], ('that anything runs with these nodes withdrawn (EXPERIMENTAL, hardware NOT RUN); that it changes what the kernel waits for or stops at (UNKNOWN)',
                                 'that the withdrawn nodes are enough for a run longer than boot 27\'s 120.2 s (EXPERIMENTAL; the withdrawal ran once); that the clock changes what the kernel waits for or '
                                 'stops at (INFERRED: section 5d); that the placeholder calendar is harmless (UNKNOWN)'))
    return (changes, quiet[1], CLOCK_TEXT, tell, claimed, quiet[4], quiet[5])


def record_problems(text, panel):
    """What is wrong with `text`, the finished record of rung 8: the names, in order, of each sentence of pp.FALSE_FOR_A_PROFILE that it carries ('FALSE: ...') and 'MISSING' if a paragraph of
    profile_paragraphs is not in it. An empty list is a record that does not say what a profile guest makes false and holds the words of its profile."""
    flat = ' '.join(text.split())
    problems = [f'FALSE: {name}' for name, pattern in pp.FALSE_FOR_A_PROFILE if re.search(pattern, flat)]
    return problems + ['MISSING' for paragraph in profile_paragraphs(panel) if ' '.join(paragraph.split()) not in flat][:1]


def extra_paragraphs(panel):
    """The paragraphs of section 3c that only these records have, after the command line's: rung 6's (the PARAMS word, the acknowledge, what still stops the run, the panic path and the page), with
    this record's reach of the acknowledge, and the profile's paragraphs at the end."""
    parts = r6.extra_paragraphs(panel)
    assert len(parts) == 6 and parts[2].startswith('What the acknowledge lets the guest reach.'), 'the paragraphs of rung 6 have changed under rung 8'
    params = patched(parts[0], ('for the records of rungs 4, 5 and 6 only', 'for the records of rungs 4 to 8 only'),
                     ('used here for the first time on the Surface Pro 11,', 'used on the Surface Pro 11 in boots 24 to 27 (OBSERVED once each),'))
    stops = patched(parts[3], ('MON STOP WATCHDOG (OBSERVED once: boot 20)', 'MON STOP WATCHDOG (OBSERVED: boots 20, 25 and 27)'))
    return (params, *parts[1:2], ACK_REACH, stops, *parts[4:], *profile_paragraphs(panel))


def expect_text(run):
    """The paragraph of section 3c that stands where rung 3's and 4's what-the-run-may-end-as is: the prediction and its falsifiers are section 5d's, in the operator's words, and only there."""
    return patched(r7.expect_text(run), ('from the host analysis of boots 24 to 26: none of it has been seen with the quiet guest', 'from the host analysis of boots 24 to 27 and of the PMU\'s clock: none of it has been seen with the clock bound'),
                   ('NOT PASSED for rung 7 (section 5d)', 'NOT PASSED for rung 8 (section 5d)'))


def section_5d(panel, wait_s):
    """Section 5d of the record, the operator's words: tools/sp11_stick_bundle_panel_rung8d.py, which takes its constants from this module (so it is imported here, when asked)."""
    import sp11_stick_bundle_panel_rung8d as d
    return d.section_5d(panel, wait_s)


def __getattr__(name):
    """A hook or a constant that this module does not say again is rung 7's (screen_text, stop_seen, film_until and the rest)."""
    if name.startswith('__'):
        raise AttributeError(name)
    return getattr(r7, name)
