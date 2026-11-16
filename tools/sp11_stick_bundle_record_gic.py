# SPDX-License-Identifier: MIT
"""The text of the authorisation record of the GIC and stop dry run for pass 5 of the stage (the quarantine), and what ties that text to the
wrapper it describes (docs/SP11-STICK-GIC.md sections 4, 7, 8 and 12; the option --gic-quarantine of tools/sp11_stick_bundle_build.py).

Why a variant. Pass 5 of the stage (probes/sp11-stick-gic.c) reads more and writes two more registers than the stage that GIC_STOP_TOUCHES of
tools/sp11_stick_bundle_record.py describes, and its record in REPORT.DAT is 1456 bytes, not 1376. The operator approves the SHA-256 of the text,
so the text of the earlier attempts stays byte for byte (tools/test-sp11-stick-monitor-auth.py pins their digests) and this one is a variant that is
chosen by name: --gic-quarantine, valid only with --gic-stop. The record module renders it; the builder only chooses and checks.

The coupling. A text says nothing about a binary, so the builder refuses both ways of pairing them wrongly: GIC_QUARANTINE_REQUIRED, payload 0 carries the
pass 5 outcome names and the record authorises the gic class without this text; GIC_QUARANTINE_ABSENT, --gic-quarantine was given and payload 0 does not
carry them. The signal is the pair of outcome names Q_NOT_HELD and FOREIGN_PENDING, both in the file: the source that makes them first appears in
pass 5, and neither is in the first two GIC wrappers or in the dry-run wrapper (tools/test-sp11-stick-bundle-record-gic.py shows that with the files).
What the signal cannot show: it is a string check, not a census (the host test compares the text with the disassembly); a later build that keeps the
two names is recognised as pass 5 as well, so a change of the stage needs this text read again; and --reporter is not coupled (the record names its
digest, and a reporter of another pass only costs the survey lines on the page). A pass 5 wrapper under a record that authorises no gic class is not
refused: it can only refuse STORE_NOT_AUTH at PIN_OK, and tools/test-sp11-stick-gic-qemu.py builds exactly that bundle to show it.

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
    '   Text of pass 5 of the stage, the quarantine (selected by --gic-quarantine; docs/SP11-STICK-GIC.md sections 4, 7,',
    '   8 and 12). The builder found the outcome names Q_NOT_HELD and FOREIGN_PENDING in payload 0, which no earlier',
    '   build holds. That is a string check of the file, not a census: tools/test-sp11-stick-bundle-record-gic.py',
    '   compares the text below with the disassembly of the wrapper on the host.')
EL2_NAMED = (
    'required in every PIN.DAT by the interface (STORE_REQUIRED). Every system register write is as in the',
    'earlier attempts: pass 5 adds no MSR and no kind of SYS operation (its two new writes are memory-mapped: see',
    'gic below), and the census of this binary lists the same 26 MSR instructions of the same 15 registers as the',
    'builds of the first two attempts (VERIFIED on the host by tools/test-sp11-stick-bundle-record-gic.py; the',
    'builder does not disassemble). The wrapper writes these registers of the boot CPU, each read first, put back',
    'and verified by read-back: class C ICC_PMR_EL1, ICC_BPR1_EL1, ICC_IGRPEN1_EL1; class T CNTHP_CVAL_EL2 and',
    'CNTHP_CTL_EL2 (the EL2 physical timer, armed for a time box of 2 s in the default build, in each of two',
    'windows, then disabled); class V VBAR_EL2 (the wrapper\'s own vector table), and IRQ unmasked by DAIFClr (#2),',
    'only inside a window of at most twice the time box, and masked again by DAIFSet (#2), which the put-back uses',
    'too; in the handlers ICC_EOIR1_EL1 (ICC_DIR_EL1 when EOImode is set), SPSR_EL2 and ELR_EL2. Class E, only',
    'after a first window passed and a second announcement and 8 s: HCR_EL2 (RW and IMO, TGE clear), VBAR_EL1 and',
    'SCTLR_EL1 (MMU and caches off) for one ERET to a stub at EL1 that spins on CNTPCT_EL0 for at most twice the',
    'time box and ends by HVC; its lines are made visible with DC CIVAC and IC IVAU. The other system instructions',
    'are the dry run\'s: DC CVAC, AT S1E2W and AT S1E2R (they set PAR_EL1), DSB SY and ISB.',
    'Reads (MRS, no write): the dry run\'s (docs/SP11-STICK-WRAPPER.md section 4) and, of this stage, ICC_SRE_EL2',
    'and, if SRE is set, ICC_CTLR_EL1, ICC_PMR_EL1, ICC_BPR1_EL1, ICC_IGRPEN1_EL1 and ICC_RPR_EL1; CNTHCTL_EL2,',
    'CNTHP_CTL_EL2, CNTHP_CVAL_EL2, HCR_EL2, VBAR_EL2, VBAR_EL1, SCTLR_EL1, ESR_EL1, ELR_EL1 and FAR_EL1;',
    'ICC_IAR1_EL1 in the handlers, a read that acknowledges the interrupt. New in pass 5: ICC_HPPIR1_EL1, which the',
    'windows already read, is read once more after the last store with IRQ masked (it acknowledges nothing); and,',
    'in this build only (the dry run\'s: the arm half that a monitor entry would call is built without them), the',
    'EL1 timers CNTV_CTL_EL0, CNTV_CVAL_EL0, CNTP_CTL_EL0 and CNTP_CVAL_EL0, once each, in the survey.')
# What the binary does not hold: tools/test-sp11-stick-bundle-record-gic.py reads these lines out of the record and checks each name against the census.
EL2_NOT = (
    'It writes no CNTHCTL_EL2, TPIDR_EL2 or other register, executes no SMC, SVC, TLBI, WFI or WFE, and calls no',
    'monitor.')
EL2_TIME = ('Total time of the two windows and the two 8 s waits: about 20 s.',)
TOUCHES = {
    'ram': ('RAM inside the placement only, as the dry-run wrapper: the copy of the chunks, zero fills and the report in',
            'REPORT.DAT. One more RAM store of the GIC and stop stage: its record, version 3, 1456 bytes (1376 before pass',
            '5, which added the survey), at REPORT.DAT offset 0x11000 (after the monitor\'s 64 KiB window), which is inside',
            'the report. No other RAM.'),
    'el2_sysreg': (*EL2_NAMED, *EL2_NOT, *EL2_TIME),
    'gic': ('memory-mapped GIC registers in the pinned frames only (PIN.DAT gicd_base, gicr_first, gicr_stride, gicr_count).',
            'Reads before any store: GICD_CTLR, GICD_TYPER and GICD_IIDR; GICR_TYPER of each pinned frame until one matches',
            'this CPU\'s affinity; then, in this CPU\'s own redistributor, GICR_WAKER, IGROUPR0, ISENABLER0, ISPENDR0,',
            'ISACTIVER0, ICFGR1 and the IPRIORITYR byte of INTID 26. The survey, new in pass 5, is reads only, decides',
            'nothing and is printed by the reporter alone: GICD_ISENABLER[n] and GICD_ISPENDR[n] for n = 1 to the',
            'ITLinesNumber of GICD_TYPER (at most 31 of each; register 0 is banked in each redistributor and is not read)',
            'and the IPRIORITYR bytes of INTIDs 23, 25, 27 and 30. GICR_CTLR is read to wait for RWP after a write (at most',
            '100000 reads and 50 ms).',
            'Writes, in this order; each register is read before it is changed and read back after it, and a store that',
            'does not hold is a named failure:',
            '  Q, the quarantine, first, a member of class R: ICENABLER0, once, with every bit that ISENABLER0 was found to',
            '    have set except bit 26 (PPI 26, the EL2 physical timer): SGI 0 and PPIs 23, 25, 27 and 30 where the unit\'s',
            '    ISENABLER0 read 0x4E800001 (OBSERVED, boot 12), so the value is 0x4A800001 there; no write when the value',
            '    is 0. Then RWP, then ISENABLER0 is read back and must be exactly PPI 26\'s bit as found, else Q_NOT_HELD',
            '    (22, OUTCOME=0X16). Put back last, after the CPU interface and the distributor are, by ONE write of',
            '    ISENABLER0 with exactly the bits Q cleared, verified against the whole register as found: the last',
            '    register write of the stage. The pending state of those interrupts is never written: a source that',
            '    asserts through the run is pending, and enabled again, when the run ends.',
            '  G, class G: GICD_CTLR, only when EnableGrp1A reads clear: the value read with 0x2 and 0x1 set when DS is',
            '    clear (a read of 0x10 is written 0x13; boot 13 read back 0x12, OBSERVED), with 0x2 set when DS is set; the',
            '    old value is put back.',
            '  R, class R, in this CPU\'s own redistributor, INTID 26 only: IPRIORITYR byte (0x80 if it differs), ISENABLER0',
            '    bit 26 only when it reads clear (put back first, by ICENABLER0, and only if the stage set it), ICFGR1',
            '    field if it reads edge, IGROUPR0 bit if the distributor shows one security state; the put-backs are',
            '    ICPENDR0 (only if the stage made INTID 26 pending), IPRIORITYR, ICFGR1 and IGROUPR0.',
            '  C, class C, the CPU interface by system registers: ICC_PMR_EL1 0xF0, ICC_BPR1_EL1 0, ICC_IGRPEN1_EL1 1.',
            'Then, with IRQ still masked, ICC_HPPIR1_EL1 is read: only INTID 1023 (none) or 26 may be next, else',
            'FOREIGN_PENDING (23, OUTCOME=0X17): no window opens, everything is put back and the INTID is on the page.',
            'Never the ITS, another interrupt\'s configuration, an SPI (read only), another redistributor (GICR_TYPER read',
            'only), SMMU, PCIe, USB or the framebuffer.')}
FACT_LINES = (
    '   Pass 5 and what it rests on (docs/SP11-STICK-GIC.md sections 4 and 12; the labels are that document\'s):',
    '      Changed from the earlier attempts: the survey (reads), the quarantine (one ICENABLER0 write and the one',
    '      ISENABLER0 write that undoes it), the check of ICC_HPPIR1_EL1 before any window, record version 3 and two',
    '      outcomes. The windows, the timer, the vector table and the EL1 excursion are as before.',
    '      Outcomes (OUTCOME= on the page, in hex): 0X1 to 0XF a refusal before any store; 0X10 to 0X1F a store that did',
    '      not hold or a foreign interrupt next, always after a store and so always with a full put-back (new: Q_NOT_HELD',
    '      0X16, FOREIGN_PENDING 0X17); 0X20 to 0X2F what the windows showed (IRQ_OK 0X20); NOT_RESTORED 0X30.',
    '      OBSERVED, one unit, one boot each (boot 12 from a photograph of the page, boot 13 from one video read frame by',
    '      frame):',
    '         the firmware leaves SGI 0 and PPIs 23, 25, 26, 27 and 30 enabled: ISENABLER0 0x4E800001 (boot 12);',
    '         INTID 27 was pending before any store: ISPENDR0 0x08000000 (boot 13);',
    '         the unit\'s GTDT, from its ACPI dump, gives Virtual EL1 GSIV 27 (Secure EL1 29, Non-secure EL1 30,',
    '           Non-secure EL2 26);',
    '         GICD_CTLR written 0x13 read back 0x12 (boot 13);',
    '         an enabled, pending PPI is delivered to EL2 as an IRQ once group 1 and the CPU interface are on: INTID 27,',
    '           not 26, was the first interrupt of window 1 in boot 13, and everything was put back clean.',
    '      INFERRED: that INTID 27 is the EL1 virtual timer asserting (CNTV_CTL_EL0), until the survey of this build',
    '      reads it; that the monitor, which stops on any interrupt that is not PPI 26 (probes/xnuboot/async.c), would',
    '      end a monitor entry run at its first IRQ unmask on the same interrupt; that nothing else runs on this CPU',
    '      while the quarantine is on (the wrapper runs after the runner has left the firmware).',
    '      UNKNOWN: whether PPI 26 itself reaches EL2 (U3); whether INTID 27 is the only foreign source (the SPIs were',
    '      never read); whether the quarantine holds on this unit: its non-secure ICENABLER0 write is the first of its',
    '      kind there, and this run is that experiment.')
