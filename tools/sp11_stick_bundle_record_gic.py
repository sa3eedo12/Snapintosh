# SPDX-License-Identifier: MIT
"""The text of the authorisation record of the GIC and stop dry run for the final stage (passes 5 and 6: the quarantine, and the rule for its last write),
and what ties that text to the wrapper it describes (docs/SP11-STICK-GIC.md sections 4, 7, 8 and 12; the option --gic-quarantine of
tools/sp11_stick_bundle_build.py).

Why a variant. Pass 5 of the stage (probes/sp11-stick-gic.c) reads more and writes two more registers than the stage that GIC_STOP_TOUCHES of
tools/sp11_stick_bundle_record.py describes, its record in REPORT.DAT is 1456 bytes, not 1376, and pass 6 makes the last of those writes conditional.
The operator approves the SHA-256 of the text, so the text of the earlier attempts stays byte for byte (tools/test-sp11-stick-monitor-auth.py pins their
digests) and this one is a variant that is chosen by name: --gic-quarantine, valid only with --gic-stop. The record module renders it; the builder only
chooses and checks. Section 4 has a variant too (time_lines): the generic line says that the time box is the PARAMS chunk's and that the stop is the
monitor's own, and the GIC wrapper reads no PARAMS word and calls no monitor. So has section 5 (silence_lines): the generic silence rule, twice the time
box after ENTER (10 s for a box of 5 s), would stop a healthy run, which is silent for about 10 s after its last NEXT line (8 s wait, then window 2:
docs/SP11-STICK-GIC.md section 4), and the document gives 15 s (section 11); the variant also says that NOT_RESTORED stops the series and that a window
verdict (OUTCOME 0X20 to 0X2F: no REFUSED line follows it, so the first bullet of the other records does not apply) does too. Two more shared lines have a
variant, because they say what is false of this stage: the heading of section 2 (PLACEMENT_HEADER: REPORT.DAT and the wrapper's stack are outside the
placement) and the last bullet of section 5 (HARDWARE_LINES: no guest runs here; a hung window or the EL1 stub is what the power cycle ends).

The text is kept in parts (EL2_WRITES, EL2_READS, GIC_READS, GIC_WRITES, ...), so that tools/test-sp11-stick-bundle-record-gic-model.py can hold what it
says is written and what it says is read to what the host model of the stage writes and reads, register by register.

The coupling. A text says nothing about a binary, so the builder refuses both ways of pairing them wrongly: GIC_QUARANTINE_REQUIRED, payload 0 carries the
pass 5 outcome names and the record authorises the gic class without this text; GIC_QUARANTINE_ABSENT, --gic-quarantine was given and payload 0 does not
carry them. The signal is the pair of outcome names Q_NOT_HELD and FOREIGN_PENDING, both in the file: the source that makes them first appears in
pass 5, and neither is in the first two GIC wrappers or in the dry-run wrapper (tools/test-sp11-stick-bundle-record-gic.py shows that with the files).
What the signal cannot show: it is a string check, not a census (the host test compares the text with the disassembly); a later build that keeps the
two names is recognised as pass 5 as well (pass 6 is one, and this text was read against it), so a change of the stage needs this text read again; and
--reporter is not coupled (the record names its digest, and a reporter of another pass only costs the survey lines on the page). A pass 5 wrapper under
a record that authorises no gic class is not refused: it can only refuse STORE_NOT_AUTH at PIN_OK, and tools/test-sp11-stick-gic-qemu.py builds exactly
that bundle to show it.

Evidence: EXPERIMENTAL, hardware NOT RUN. Nothing here approves anything."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_bundle_chunks as ch  # noqa: E402

OPTION = '--gic-quarantine'
REFUSALS = ('GIC_QUARANTINE_REQUIRED', 'GIC_QUARANTINE_ABSENT')     # beside BUILDER_REFUSALS and the names of tools/sp11_stick_bundle_m1.py
ch.KNOWN_REFUSALS.update(REFUSALS)
ch.KNOWN_REFUSALS.add('OPTIONS_BAD')
Refused = ch.Refused
SIGNAL = (b'Q_NOT_HELD', b'FOREIGN_PENDING')      # outcomes 22 and 23 of the stage, named in the wrapper's and the reporter's outcome tables


def is_pass5_wrapper(image):
    """True when every name of SIGNAL is in `image` (payload 0)."""
    return all(name in image for name in SIGNAL)


def check_options(gic_stop, gic_quarantine):
    """OPTIONS_BAD: --gic-quarantine chooses the text of the --gic-stop record and means nothing without it."""
    if gic_quarantine and not gic_stop:
        raise Refused('OPTIONS_BAD', f'{OPTION} is the text of the --gic-stop record for the pass 5 stage: give --gic-stop too')


def check_wrapper(image, gic_quarantine, gic_authorised):
    """The two named refusals. `image` is --monitor of a record that is not the monitor entry's (the builder has refused a wrapper that calls a monitor
    already); `gic_authorised` says whether the record authorises the gic class."""
    pass5, names = is_pass5_wrapper(image), ', '.join(name.decode() for name in SIGNAL)
    if pass5 and gic_authorised and not gic_quarantine:
        raise Refused('GIC_QUARANTINE_REQUIRED', f'--monitor carries the outcomes of pass 5 of the GIC stage ({names}) and the record authorises the gic class: '
                                                 f'the text of the other records does not describe it; give --gic-stop {OPTION}')
    if gic_quarantine and not pass5:
        raise Refused('GIC_QUARANTINE_ABSENT', f'{OPTION} is the text for pass 5 of the GIC stage and --monitor does not carry its outcomes ({names}): '
                                               f'give the pass 5 wrapper, or leave {OPTION} out')


# The lines under "Record of the GIC and stop dry run", and after the three classes of section 3. The classes below are printed by the record module as
# `   name: first` and `      rest`: a line is at most 120 characters there (tools/test-sp11-stick-bundle-record-gic.py holds them to it).
HEAD_LINES = (
    '   Text of passes 5 and 6 of the stage, the quarantine and the rule for its last write (selected by',
    '   --gic-quarantine; docs/SP11-STICK-GIC.md sections 4, 7, 8 and 12). The builder found the outcome names Q_NOT_HELD',
    '   and FOREIGN_PENDING in payload 0, which the builds before pass 5 do not hold (pass 6 has the same two). That is a',
    '   string check of the file, not a census: tools/test-sp11-stick-bundle-record-gic.py compares the text below with',
    '   the disassembly of the wrapper on the host, and tools/test-sp11-stick-bundle-record-gic-model.py compares what it',
    '   says is written and what is read, register by register, with the host model of the stage (the model is not the unit).')
# The el2_sysreg class in parts: what it says the wrapper writes, the system instructions that are not register writes, what it reads, what it does not
# hold (tools/test-sp11-stick-bundle-record-gic.py reads EL2_NOT out of the record and checks each name against the census), and the time.
EL2_WRITES = (
    'required in every PIN.DAT by the interface (STORE_REQUIRED). Every system register write is as in the',
    'earlier attempts: passes 5 and 6 add no MSR and no kind of SYS operation (their new writes are memory-mapped:',
    'see gic below), and the census of this binary lists the same 26 MSR instructions of the same 15 registers as',
    'the builds of the first two attempts (VERIFIED on the host by tools/test-sp11-stick-bundle-record-gic.py; the',
    'builder does not disassemble). The wrapper writes these registers of the boot CPU, each read first, put back',
    'and verified by read-back, except ICC_EOIR1_EL1, ICC_DIR_EL1 and the handlers\' SPSR_EL2 and ELR_EL2 (below):',
    'class C ICC_PMR_EL1, ICC_BPR1_EL1, ICC_IGRPEN1_EL1; class T CNTHP_CVAL_EL2 and CNTHP_CTL_EL2 (the EL2',
    'physical timer, armed for a time box of 2 s in the default build, in each of two windows, then disabled);',
    'class V VBAR_EL2 (the wrapper\'s own vector table), and IRQ unmasked by DAIFClr (#2), only inside window 1,',
    'which ends at 4.08 s at the latest, and masked again by DAIFSet (#2), which the put-back uses too. In the',
    'handlers: ICC_EOIR1_EL1 (ICC_DIR_EL1 when EOImode is set) is written to end the interrupt that was acknowledged,',
    'and is neither read nor put back; SPSR_EL2 and ELR_EL2 are read for the page (the entry\'s values) and set for the',
    'return, and are not put back. Class E, only after a first window passed and a second announcement and 8 s:',
    'HCR_EL2 (RW and IMO, TGE clear), VBAR_EL1 and SCTLR_EL1 (MMU and caches off), and SPSR_EL2 and ELR_EL2 for the',
    'one ERET to a stub at EL1 with IRQ unmasked in its SPSR (saved first, put back after the stub\'s HVC); the stub',
    'spins on the counter for at most 4 s (window 2) and ends by HVC; its lines are made visible with DC CIVAC and',
    'IC IVAU.')
EL2_OTHER = (
    'The other system instructions are the dry run\'s: DC CVAC, AT S1E2W and AT S1E2R (they set PAR_EL1), DSB SY and',
    'ISB.')
EL2_READS = (
    'Reads (MRS, no write): the dry run\'s (docs/SP11-STICK-WRAPPER.md section 4) and, of this stage, CurrentEL,',
    'SPSel and DAIF (the checks of the CPU\'s state), MPIDR_EL1 (the affinity to match), ICC_SRE_EL2 and, if SRE is',
    'set, ICC_CTLR_EL1, ICC_PMR_EL1, ICC_BPR1_EL1, ICC_IGRPEN1_EL1 and ICC_RPR_EL1; CNTHCTL_EL2, CNTHP_CTL_EL2,',
    'CNTHP_CVAL_EL2, HCR_EL2, VBAR_EL2, VBAR_EL1, SCTLR_EL1, ESR_EL1, ELR_EL1 and FAR_EL1; CNTPCT_EL0, the counter,',
    'which every wait and the EL1 stub read; in the handlers ESR_EL2, ELR_EL2, SPSR_EL2 and FAR_EL2 (the entry\'s',
    'syndrome, return address, saved state and fault address) and ICC_IAR1_EL1, a read that acknowledges the interrupt.',
    'New in pass 5: ICC_HPPIR1_EL1, which the windows already read, is read once more after the last store with IRQ',
    'masked (it acknowledges nothing); and, in this build only (the dry run\'s: the arm half that a monitor entry',
    'would call is built without them), the EL1 timers CNTV_CTL_EL0, CNTV_CVAL_EL0, CNTP_CTL_EL0 and CNTP_CVAL_EL0,',
    'once each, in the survey.')
EL2_NOT = (
    'It writes no CNTHCTL_EL2, TPIDR_EL2 or other register, executes no SMC, SVC, TLBI, WFI or WFE, and calls no',
    'monitor.')
EL2_TIME = ('Total time of the two windows and the two 8 s waits: about 20 s with the interrupts on time, at most about',
            '24 s (section 4).')
# The gic class in parts: the reads (the survey included), the writes in their order, the check, and what is never touched.
GIC_HEAD = ('memory-mapped GIC registers in the pinned frames only (PIN.DAT gicd_base, gicr_first, gicr_stride, gicr_count).',)
GIC_READS = (
    'Reads before any store: GICD_CTLR, GICD_TYPER and GICD_IIDR; GICR_TYPER of each pinned frame until one matches',
    'this CPU\'s affinity; then, in this CPU\'s own redistributor, GICR_WAKER, IGROUPR0, ISENABLER0, ISPENDR0,',
    'ISACTIVER0, ICFGR1 and the IPRIORITYR byte of INTID 26. The survey, new in pass 5, is reads only, decides',
    'nothing and is printed by the reporter alone: GICD_ISENABLER[n] and GICD_ISPENDR[n] for n = 1 to the',
    'ITLinesNumber of GICD_TYPER (at most 31 of each; register 0 is banked in each redistributor and is not read)',
    'and the IPRIORITYR bytes of INTIDs 23, 25, 27 and 30. After a write to GICD_CTLR, ICENABLER0 or ISENABLER0 the',
    'wait for RWP reads GICD_CTLR or GICR_CTLR (at most 100000 reads and 50 ms each).')
GIC_WRITES = (
    'Writes, in this order; each register is read before it is changed and read back after it, a put-back that does',
    'not read back as found is written once more, and a store that does not hold is a named failure:',
    '  Q, the quarantine, first, a member of class R: ICENABLER0, once, with every bit that ISENABLER0 was found to',
    '    have set except bit 26 (PPI 26, the EL2 physical timer): SGI 0 and PPIs 23, 25, 27 and 30 where the unit\'s',
    '    ISENABLER0 read 0x4E800001 (OBSERVED, boot 12), so the value is 0x4A800001 there; no write when the value',
    '    is 0. Then RWP, then ISENABLER0 is read back and must be exactly PPI 26\'s bit as found, else Q_NOT_HELD',
    '    (22, OUTCOME=0X16). Put back last, after the CPU interface and the distributor are, by ONE write of',
    '    ISENABLER0 with exactly the bits Q cleared, verified against the whole register as found: the last',
    '    register write of the stage, except that it is NOT made when the put-backs of both ICC_IGRPEN1_EL1 and',
    '    GICD_CTLR failed (both group 1 gates left open): ISENABLER0 is then only read and named in FAILED, the',
    '    foreign interrupts stay disabled and the run ends NOT_RESTORED (OUTCOME=0X30). The pending state of those',
    '    interrupts is never written: a source that asserts through the run is pending, and, except in that case,',
    '    enabled again, when the run ends. A gate found open is not counted: with EnableGrp1A found set (class G',
    '    never begun) and ICC_IGRPEN1_EL1 stuck open the write IS made; both gates were found closed on the unit',
    '    (GICD_CTLR 0x10, ICC_IGRPEN1_EL1 0: OBSERVED, boot 13).',
    '  G, class G: GICD_CTLR, only when EnableGrp1A reads clear: the value read with 0x2 and 0x1 set when DS is',
    '    clear (a read of 0x10 is written 0x13; boot 13 read back 0x12, OBSERVED), with 0x2 set when DS is set; the',
    '    old value is put back.',
    '  R, class R, in this CPU\'s own redistributor, INTID 26 only: IPRIORITYR byte (0x80 if it differs), ISENABLER0',
    '    bit 26 only when it reads clear (put back first, by ICENABLER0, and only if the stage set it), ICFGR1',
    '    field if it reads edge, IGROUPR0 bit if the distributor shows one security state; the put-backs are',
    '    ICPENDR0 (only if the stage made INTID 26 pending), IPRIORITYR, ICFGR1 and IGROUPR0.',
    '  C, class C, the CPU interface by system registers: ICC_PMR_EL1 0xF0, ICC_BPR1_EL1 0, ICC_IGRPEN1_EL1 1.')
GIC_CHECK = (
    'Then, with IRQ still masked, ICC_HPPIR1_EL1 is read once: only INTID 1023 (none) or 26 may be next, else',
    'FOREIGN_PENDING (23, OUTCOME=0X17): no window opens, everything is put back and the INTID is on the page.')
GIC_NEVER = (
    'Never the ITS, SMMU, PCIe, USB or the framebuffer. No SPI register is written (the SPIs are only read) and no',
    'other redistributor (only its GICR_TYPER is read). Of the interrupts but PPI 26 only the enable bits that Q',
    'cleared are written, by Q and its put-back (SGI 0 and PPIs 23, 25, 27 and 30 where ISENABLER0 read 0x4E800001):',
    'no priority or pending state of any of them is written, and IGROUPR0 and ICFGR1 are written whole, the other',
    'bits as read, and only in the cases of R above (neither on this unit\'s reads in boot 13: DS 0, ICFGR1 level).',
    'But a window takes whatever INTID arrives: ICC_IAR1_EL1 acknowledges it, ICC_EOIR1_EL1 ends it (ICC_DIR_EL1',
    'too when EOImode is set; it was in boot 13), a foreign SPI included (INFERRED from the architecture: a level',
    'source that still asserts is pending again, an edge or message interrupt is consumed). GICD_CTLR EnableGrp1A',
    'is distributor-wide: group 1 is enabled for every interrupt while the stage holds it set. PPI 26\'s own are the',
    'stage\'s: it sets them as the writes above say, and undoes them.')
TOUCHES = {
    'ram': ('the placement (the copy of the chunks, zero fills), REPORT.DAT (outside the placement) and the',
            'wrapper\'s stack, as the dry-run wrapper. One more RAM store of the GIC and stop stage: its record, version 3,',
            '1456 bytes (1376 before pass 5, which added the survey), at REPORT.DAT offset 0x11000 (after the monitor\'s',
            '64 KiB window), which is inside the report. No other RAM.'),
    'el2_sysreg': (*EL2_WRITES, *EL2_OTHER, *EL2_READS, *EL2_NOT, *EL2_TIME),
    'gic': (*GIC_HEAD, *GIC_READS, *GIC_WRITES, *GIC_CHECK, *GIC_NEVER)}
FACT_LINES = (
    '   Passes 5 and 6 and what they rest on (docs/SP11-STICK-GIC.md sections 4 and 12; the labels are that document\'s):',
    '      Changed from the earlier attempts: the survey (reads), the quarantine (one ICENABLER0 write and the one',
    '      ISENABLER0 write that undoes it, which pass 6 withholds when both group 1 gates stay open), the check of',
    '      ICC_HPPIR1_EL1 before any window, record version 3 and two outcomes. The windows, the timer, the vector table',
    '      and the EL1 excursion are as before.',
    '      Outcomes (OUTCOME= on the page, in hex): 0X1 to 0XF a refusal, before any store except COUNTER at the second',
    '      8 s wait (after the stores, with a put-back); 0X10 to 0X1F a store that did not hold or a foreign interrupt',
    '      next, always after a store and so always with a put-back (new: Q_NOT_HELD 0X16, FOREIGN_PENDING 0X17); 0X20 to',
    '      0X2F what the windows showed (IRQ_OK 0X20); NOT_RESTORED 0X30.',
    '      OBSERVED, one unit, one boot each (boot 12 from a photograph of the page, boot 13 from one video read frame by',
    '      frame):',
    '         the firmware leaves SGI 0 and PPIs 23, 25, 26, 27 and 30 enabled: ISENABLER0 0x4E800001 (boot 12);',
    '         INTID 27 was pending before any store: ISPENDR0 0x08000000 (boot 13);',
    '         the unit\'s GTDT gives Virtual EL1 GSIV 27 (Secure EL1 29, Non-secure EL1 30, Non-secure EL2 26) (from its',
    '           ACPI dump, not from boot 12 or 13);',
    '         GICD_CTLR written 0x13 read back 0x12 (boot 13);',
    '         an enabled, pending PPI is delivered to EL2 as an IRQ once group 1 and the CPU interface are on: INTID 27,',
    '           not 26, was the first interrupt of window 1 in boot 13, and everything was put back clean.',
    '      INFERRED: that INTID 27 is the EL1 virtual timer asserting (CNTV_CTL_EL0), until the survey of this build',
    '      reads it; that the monitor, which stops on any interrupt that is not PPI 26 (probes/xnuboot/async.c), would',
    '      end a monitor entry run at its first IRQ unmask on the same interrupt; that nothing else runs on this CPU',
    '      while the quarantine is on (the wrapper runs after the runner has left the firmware).',
    '      UNKNOWN: whether PPI 26 itself reaches EL2 (U3); whether INTID 27 is the only foreign source (the SPIs were',
    '      not read before this build, which reads INTIDs 32 to 991 on the unit); whether any extended SPI',
    '      (GICD_TYPER.ESPI is 1, range 31) or LPI (GICR_TYPER.PLPIS is 1) is enabled and pending: neither is surveyed or',
    '      quarantined; only the one ICC_HPPIR1_EL1 read before window 1 sees one, at one instant (the decode of the two',
    '      registers is INFERRED); whether the quarantine holds on this unit: its non-secure ICENABLER0 write is the',
    '      first of its kind there, and this run is that experiment.')
# Section 4 of this kind, in place of the generic line: the GIC wrapper reads no PARAMS word (tools/test-sp11-stick-bundle-record-gic.py scans its sources),
# calls no monitor, and times itself (docs/SP11-STICK-GIC.md section 4: 2 s first timers, bounds of 4.08 s and 4 s, two waits of 8 s).
TIME_LINES = (
    '4. Time box: watchdog_seconds, {box} s, is carried in the PARAMS chunk of GUEST.DAT because the builder requires a',
    '   time box for every record and checks the chunk against it (TIME_BOX_MISMATCH); the GIC wrapper does not read it',
    '   and calls no monitor, so nothing here is stopped by it. The wrapper times itself: each of its two windows arms',
    '   CNTHP 2 s ahead and is bounded at 4 s (window 1: 4.08 s, with 80 ms for its re-arms of 10 ms), and the two 8 s',
    '   waits, before the first store and before the EL1 excursion, come on top: about 20 s in all with the interrupts on',
    '   time (12 s if window 1 sees no interrupt, 22 s if window 2 then sees none: docs/SP11-STICK-GIC.md section 4); at',
    '   most about 24 s, the sum of the bounds (4.08 s + 8 s + 8 s + 4 s), because the verdict fails an interrupt that',
    '   comes early and not one that comes late.')


def time_lines(box_s):
    """Section 4 of the record of --gic-quarantine: the lines that replace the generic time box line, with the PARAMS watchdog `box_s` named."""
    return [TIME_LINES[0].format(box=box_s), *TIME_LINES[1:]]


# Section 5 of this kind, in place of the generic silence rule (twice the time box after ENTER: 10 s for a box of 5 s). The page of a healthy run is silent for
# 8 s after the first NEXT line (the wait), 2 s after the second (window 1) and about 10 s after the third (the wait, then window 2), at most 12 s when window 2
# sees no interrupt (the host model's page, tools/test-sp11-stick-bundle-record-gic-model.py; docs/SP11-STICK-GIC.md section 4), so 10 s would stop a healthy
# run; the document's rule is 15 s (section 11), and it stops the series on NOT_RESTORED and after any window verdict (OUTCOME 0X20 to 0X2F: the wrapper returns
# and prints no REFUSED line, which the first bullet of the other records needs). HARDWARE_LINES replaces the last bullet of the other records, which speaks of
# a guest that does not run here.
SILENCE_LINES = (
    '   - No page change for 15 s after a NEXT line or after ENTER (docs/SP11-STICK-GIC.md section 11): note the last line',
    '     and power cycle. A healthy run is silent for 8 s after NEXT GIC STORES AFTER 8 S and for about 10 s after NEXT GIC',
    '     EL1 EXCURSION AFTER 8 S (the 8 s wait, then window 2 of about 2 s; at most 12 s: section 4). This replaces the rule',
    '     of the other records, twice the time box.',
    '   - NOT_RESTORED stops the series (the quarantined interrupts may then stay disabled until the power cycle: INFERRED).',
    '   - After any window verdict (OUTCOME 0X20 to 0X2F, IRQ_OK 0X20 included; no REFUSED line follows it) stop: the next',
    '     work is the M1 stop design (D2), not a retry (docs/SP11-STICK-GIC.md section 11).')
HARDWARE_LINES = (
    '   - One hardware client; AC power or at least 50% battery; the operator present and filming. A hung window or stub is',
    '     ended by the operator\'s power cycle, which writes nothing.')
PLACEMENT_HEADER = '2. Placement (the range of the copy and the zero fills; REPORT.DAT and the wrapper\'s stack are outside it)'


def silence_lines():
    """The lines of section 5 of the record of --gic-quarantine that replace the generic silence rule: the 15 s of docs/SP11-STICK-GIC.md section 11 after a NEXT
    line or ENTER, the rule that NOT_RESTORED stops the series, and the rule that a window verdict does."""
    return SILENCE_LINES
