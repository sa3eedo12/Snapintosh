# SP11 stick wrapper: the GIC and stop dry run (work item 8)

Status: EXPERIMENTAL. Hardware NOT RUN. Nothing in this note authorises a run: the operator authorises by the SHA-256 of the record of
section 7, and only then. Labels: VERIFIED (a host or QEMU test of this tree shows it), OBSERVED (a run on the unit, one unit), INFERRED
(from the GICv3 architecture as remembered or from q1n1's code: the specification was not re-read), EXPERIMENTAL, UNIMPLEMENTED, UNKNOWN.
The earlier passes of this work item ran on the unit twice (boots 12 and 13, OBSERVED, one unit each, section 12); this pass has not run there, so none of its
statements is VERIFIED on the unit.

## 1. What it is

The A16 monitor stops its guest with the EL2 physical timer, a level PPI (INTID 26) routed to EL2 by HCR_EL2.IMO and taken from the guest at
EL1. On the Surface Pro 11 that has never been tried for PPI 26: before boot 13 this work had written no register of the GIC there, GICD_CTLR reads 0x10, and the firmware
left SGI 0 and PPIs 23, 25, 26, 27 and 30 enabled in this CPU's redistributor (boot 12, OBSERVED, one unit; docs/SP11-MONITOR-PORT.md U3, D2).
Boot 13 (OBSERVED, one unit, one boot; section 12) found INTID 27 pending as well, and window 1 ended on it before PPI 26 could be tried: the monitor
stops on any interrupt that is not PPI 26 (probes/xnuboot/async.c:87-95, VERIFIED by reading), so since pass 5 the stage quarantines the other enabled
interrupts of this CPU's redistributor for the run and refuses if one is still the highest pending.
This variant of the stick wrapper (docs/SP11-STICK-WRAPPER.md) tests the mechanism and
nothing else. After the unchanged stages (bundle checks, placement, copy and verify) it reads the GIC and surveys what else is enabled and pending (reads
only), announces its first store and waits 8 s, quarantines the other enabled interrupts, stores in classes G, R, C and T (the quarantine is the first
store, a member of R), checks that no foreign interrupt is the highest pending, and runs window 1 at EL2 (C4). Only if that passed it announces again, waits 8 s, writes the EL1
registers (class E) and runs window 2, an excursion to EL1 (C6). It puts everything back, reports, and returns:
`MONITOR NOT CALLED: UNIMPLEMENTED IN THIS BUILD`.

It is opt-in at compile time: `SNAP_SP11_STICK_GIC=1` (needs `SNAP_SP11_STICK=1` and so `SNAP_PLATFORM_SP11=1`; only 0 and 1; default 0).
With it at 0 every file of the variant compiles to nothing and the dry-run wrapper rebuilds byte for byte as the one that ran on the unit
(VERIFIED by tools/test-sp11-stick-gic-build.py). The variant is a separate binary pair, SNAPMON-GIC.BIN and SNAPREP-GIC.BIN, and a
different SHA-256 means a different authorisation.

## 2. What it decides, and what it cannot

Decides (UNKNOWN until the unit runs): U3, whether PPI 26 arrives at EL2 as an IRQ after non-secure stores and how long after the programmed
deadline (still UNKNOWN after boot 13, which ended on INTID 27 before CNTHP's deadline); whether the non-secure view takes the group 1 enable and owns
PPI 26 (the enable in GICD_CTLR held in boot 13, OBSERVED; PPI 26's group is UNKNOWN); whether the CPU interface and CNTHP registers hold at EL2 (they did
in boot 13, OBSERVED, one unit); whether the quarantine of the other enabled interrupts holds (a non-secure write of ICENABLER0, and a later one of
ISENABLER0: UNKNOWN until the unit runs). It feeds D2 (an interrupt stop for the M1 monitor, or the polling fallback) and D4. Three contracts of the
monitor's use of the interrupt:

- C4, window 1 at EL2. Three cycles, each armed a short time ahead (10 ms): at the vector HPPIR1 is 26 and CNTHP_CTL.ISTATUS is 1; IAR1 is
  26; once the level is dropped and the interrupt ended (EOIR1, DIR when EOImode is set) HPPIR1 is 1023 and the PPI is neither pending nor
  active. Then a final deadline taken with no acknowledge, as the monitor's stop path takes it (probes/xnuboot/async.c:165-171 never reads
  IAR): after CNTHP_CTL = 0 nothing may remain pending. Every latency is reported in ticks and in us against the deadline programmed.
- C6, window 2 from EL1. The monitor takes the interrupt from its guest at the lower-EL vector (+0x480, probes/xnuboot.S:260) with
  HCR_EL2.IMO set. Window 2 does the same from a stub at EL1 under HCR_EL2 0x80000010 (RW and IMO, TGE clear) with IRQ unmasked at EL1.
- The quarantine (pass 5). The monitor stops on any interrupt that is not PPI 26 (probes/xnuboot/async.c:87-95: only HPPIR1 == 26 and then IAR1 == 26 is
  the tick), and boot 13 found a foreign interrupt enabled and pending. While the stop timer can fire, no interrupt but PPI 26 may be enabled in this
  CPU's redistributor: the stage disables every other enabled bit of ISENABLER0 first, enables them again as its last write (pass 6: not when both group 1
  gates failed to go back, section 4), and refuses
  `FOREIGN_PENDING` if ICC_HPPIR1_EL1, read with IRQ masked, still names another interrupt.

Cannot decide: anything under a running guest, the other CPUs, the SMMU, the ITS, the M1 time box (D5: 5 s; here 2 s per window), EL3's
behaviour at other moments, and an interrupt taken at EL1 with PSTATE.I set (the monitor enters its guest masked, probes/xnuboot.S:158;
section 12). A QEMU run proves the sequence and the assembly, never the unit's EL3 (section 9).

## 3. Build and test

    export Q1N1_SP11_TREE=<q1n1 checkout>      (the fork: runner headers, sha256.c, the payload recipe; absent: REFUSED)
    python3 tools/sp11_stick_wrapper_build.py --out build/sp11-gic --gic       SNAPMON-GIC.BIN, SNAPREP-GIC.BIN, build-report.json
    python3 tools/sp11_stick_bundle_build.py authorisation-template real ... --monitor SNAPMON-GIC.BIN --reporter SNAPREP-GIC.BIN \
        --stores ram,el2_sysreg,gic --gic-stop                                  the record the operator approves, on stdout
    python3 tools/test-sp11-stick-gic.py | -gic-quarantine.py | -gic-putback.py | -gic-hppir.py | -gic-doc.py | -gic-record.py | -gic-build.py | -gic-qemu.py     the tests of section 9

The bundle builder writes the two payloads into the bundle as SNAPMON.BIN and SNAPREP.BIN (the frozen file roles); the record names their
sizes and digests. `--gic-stop` needs `gic` in `--stores` and changes only the three class texts of section 3 of the record.

## 4. The stage, step by step

Reads first, then one decision, then stores. Code: `probes/sp11-stick-gic.c` (the arm and the put-back), `probes/sp11-stick-gic-dry.c` (the dry
run's own: classes T, V, E, the windows, the verdict), `probes/sp11-stick-gic-vec.c` (assembly: the EL2 vector table, the window and excursion
routines, the EL1 table and stub), `probes/sp11-stick-gic-record.h` (the record and the page lines), `probes/sp11-stick-gic.h` (parameters,
register offsets, `struct sw_gic`, the interface), the gated layer at the end of `probes/sp11-stick-hw.h` (one function per register).

| Step | Function | Reads | Stores |
| --- | --- | --- | --- |
| CPU and pins | `gic_check_cpu` | CurrentEL 2, SPSel 1, DAIF masked, HCR_EL2.IMO, PIN.DAT's GIC fields, the counter, an AT write probe of the record | none |
| EL1 checks | `gic_dry_precheck` | CNTHCTL_EL2.EL1PCTEN, an AT read probe that the stub image is identity-mapped (EL1 with the MMU off fetches by physical address), old HCR_EL2, VBAR_EL1, SCTLR_EL1 | none |
| Reads | `gic_read_*` | GICD_CTLR, TYPER, IIDR; a bounded scan of the pinned frames for MPIDR's affinity, then this PPI's registers; ICC_SRE_EL2 and, only if SRE is set, the rest of the CPU interface, VBAR_EL2, CNTHP | none |
| Survey | `gic_read_survey` | after every read that a refusal rests on, so that a refusal leaves it unread: GICD_ISENABLER[n] and GICD_ISPENDR[n] for n = 1 to ITLinesNumber (at most 31: INTIDs 32 to 991 on the unit, 1023 at most; the extended SPIs and the LPIs are not surveyed, section 12), the priority bytes of INTIDs 23, 25, 27 and 30, and, in the dry run's build only, CNTV_CTL_EL0, CNTV_CVAL_EL0, CNTP_CTL_EL0, CNTP_CVAL_EL0 | none; it decides nothing |
| Announce | `sw_gic_arm_stores` | | `NEXT GIC STORES AFTER 8 S G= R= C= T= V= E=`, then 8 s on the counter, then the first store |
| Q, G, R, C | `gic_store_q`, `_g`, `_r`, `_c` | back, each | section 7; Q first, so that nothing foreign can be signalled once group 1 is enabled |
| Check | `gic_check_pending` | ICC_HPPIR1_EL1, with IRQ masked | none; FOREIGN_PENDING unless it reads 1023 or 26; then PREPARED, written after Q, G, R, C and this check held; one read at one instant (section 12) |
| T | `gic_arm_timer` | back | CNTHP_CVAL_EL2 now + time box, CNTHP_CTL_EL2 enable |
| V, window 1 | `gic_el2_window` | the done flag, CNTPCT | VBAR_EL2 (own table), DAIF.I clear; at the end masked and VBAR_EL2 back |
| Classify 1 | `gic_classify_el2` | the diagnosis registers | none; its IRQ or DIAG line is printed before E |
| E, window 2 | `gic_el1_window` | back | the second NEXT line and 8 s, then VBAR_EL1, SCTLR_EL1, HCR_EL2, T again, ERET to the stub (section 7) |
| Classify 2 | `gic_classify_el1` | as above, in the HVC path | none |
| Put back | `gic_dry_restore`, `sw_gic_put_back` | each register again | CNTHP_CTL 0 (always), E (HCR_EL2 first), V, T (CVAL, then CTL), then C, R, G, and last the quarantined enables (one ISENABLER0 write, withheld when both group 1 gates are still open) |
| Report | `gic_finish` | | the record to REPORT.DAT, the page lines |

Each class is marked `begun` in the record, and the record stored and cleaned to RAM, before the class's first store, so that a freeze at a
store is named by what the record holds. Every register is read before it is changed and read back after: one that did not hold is a named
failure (the stage stops storing, puts back, reports). The put-back is verified by read-back, one more write at most, then NOT_RESTORED; a
register never written is only read. The reads refuse PPI_OWNED only for PPI 26 itself pending or active: other enabled bits, and PPI 26
already enabled, are accepted (not refused), and PPI 26 already enabled is left as found. The stage writes ISENABLER0 to enable PPI 26 only when bit 26 is
clear (that write has bit 26 alone, and ICENABLER0 with bit 26 alone undoes it), and writes ISENABLER0 for no other purpose than the quarantine's put-back below.

The quarantine (pass 5, EXPERIMENTAL). Boot 13 (OBSERVED, section 12) found INTID 27 pending and enabled before any store of this work, and window 1 took it
first. So the first store, a member of class R, disables this CPU's other enabled interrupts before group 1 is enabled: one write of ICENABLER0 with every bit
of the found ISENABLER0 but bit 26 (SGI 0 and PPIs 23, 25, 27 and 30 on the unit: 0x4A800001), then the wait for GICR_CTLR.RWP and a read of ISENABLER0, which
must be exactly bit 26 as found (else Q_NOT_HELD; nothing is written when there is nothing to disable). The pending state of those interrupts is never
written: a source that stays asserting stays pending and is not forwarded. After Q, G, R and C, with DAIF.I still set, the arm reads ICC_HPPIR1_EL1 (no side
effect, unlike IAR1): 1023 or 26 passes, anything else, the special INTIDs 1020 to 1022 included, is FOREIGN_PENDING carrying the INTID, followed by the
whole put-back and no window (an SPI that is enabled and pending, which the quarantine of this CPU's redistributor does not cover, is the case). The check
is inside the arm, so that the monitor entry refuses too. The put-back is C, R (PPI 26's own enable first, ahead of the configuration registers), G, the
check that the running priority is idle, and then, as the last write of the run, one ISENABLER0 write with exactly the bits the quarantine cleared, so that
nothing foreign can be signalled while the CPU interface and the distributor are back; ISENABLER0 is then verified whole against the value found (else
NOT_RESTORED, after the one retry). Pass 6 (a reviewer's finding; EXPERIMENTAL, host model only): that write is NOT made when the put-backs of BOTH
ICC_IGRPEN1_EL1 and GICD_CTLR failed (both bits are in FAILED: group 1 is still enabled at the CPU interface and at the distributor), because enabling the
foreign interrupts again would re-arm a pending one (INTID 27 in boot 13) behind two open gates. ISENABLER0 is then only read, its bit is set in FAILED, the
foreign interrupts stay disabled and the run ends NOT_RESTORED (OUTCOME=0X30; the refusal or the window's outcome stays FIRST). One gate left open alone does
not withhold it: the other is closed and the write is inert. The rule counts put-backs that failed, so a gate found open is not counted: with EnableGrp1A found
set (class G never begun) and ICC_IGRPEN1_EL1 stuck open, the write IS made behind two open gates (a limit; the model pins it, `gates-found-open`). On this unit
EnableGrp1A was found clear and IGRPEN1 0 (boot 13, OBSERVED, one unit), so both gates are the stage's own. The rule holds in a refused run too.

The two halves, for the monitor entry (work item 4b). One source of truth. 4b calls `sw_gic_arm` (the reads and the survey, the NEXT line and wait, the
quarantine, G, R and C with read-back, the check of ICC_HPPIR1_EL1, PREPARED written after the arm succeeded), then the monitor, then `sw_gic_put_back` (C, R
and G exactly, the quarantined enables last unless both group 1 gates are still open, verified, one retry, NOT_RESTORED loud). It must not arm CNTHP or change VBAR_EL2 or HCR_EL2: the monitor does those
itself (probes/xnuboot.S:172-178). The dry run's own registers (T, V, E) are in `probes/sp11-stick-gic-dry.c` only, and `probes/sp11-stick-gic.c` built with
`-DSW_GIC_DRY_REGS=0` has no write or read of them (VERIFIED on its object: MSR to ICC_PMR_EL1, ICC_BPR1_EL1, ICC_IGRPEN1_EL1 only; MRS of ICC_HPPIR1_EL1 once,
the check, and of none of the survey's EL1 timers; no ERET, HVC, IC).

Window 1 (EL2). `sw_gic_window` is the only code that changes VBAR_EL2 on purpose and unmasks IRQ at EL2 (the I bit only). It polls a done flag
and CNTPCT against the deadline: no WFI, so a lost interrupt ends by the counter. The table has sixteen entries 128 bytes apart, 2 KiB aligned;
each saves x0 and x1, numbers itself and joins one body that saves the other caller-saved registers, calls the C half and ERETs. Slot 5
(current EL, SPx, IRQ, +0x280), cycles 0 to 2: record HPPIR1 and CNTHP_CTL, read IAR1, re-arm CVAL 10 ms ahead (the level is low before the end
of interrupt, compat/fast-timer-irq.h), EOIR1 (DIR when EOImode), record the state after a 10 us settle on the counter, return with IRQ unmasked. The fourth entry reads no
IAR: CNTHP_CTL = 0, settle, record, end masked. An IAR that is not 26, an entry before its deadline or more than six entries ends it by name; any
other slot is recorded and ends it: no retry.

Window 2 (EL1). Before any store: EL1PCTEN set and the image identity-mapped, else a refusal. Then `NEXT GIC EL1 EXCURSION AFTER 8 S`, 8 s,
VBAR_EL1 (the EL1 table), SCTLR_EL1 0x30500800 (MMU and caches off, as q1n1's el1trap), HCR_EL2 0x80000010, each read back (IMO must hold:
EL1_NOT_HELD), the stub made visible (DC CIVAC, IC IVAU), CNTHP armed, and an ERET to the stub with SPSR 0x345 (EL1h, D, A, F masked, I clear).
The stub spins on CNTPCT_EL0 up to twice the time box from its own first read (a CNTPOFF offset cannot matter), then `hvc #0`; a UDF follows.
The EL1 table: sixteen entries `mov x16, #n; hvc #1`. EL2's slot 9 (lower EL, IRQ, +0x480) records INTID, ELR, SPSR and latency, silences the
timer, ends the interrupt and the excursion at EL2h with the caller's PSTATE; slot 8 (lower EL, synchronous, +0x400): HVC #0 is
NO_IRQ_FROM_EL1 with the diagnosis registers, HVC #1 an exception of EL1 itself (0x100 + n; ESR_EL1, ELR_EL1, FAR_EL1), any other class
EL1_EXCEPTION; every other slot ends the window by its number. The stage is never left at EL1. q1n1's `payloads/el1trap.c` is the pattern
(OBSERVED, boot 11: the first q1n1 runner payload to run code below EL2, one unit; the X2 image's T02 had run an EL1 stub with vector tables
and an ERET path, OBSERVED): evidence for the pattern, none for U3.

Parameters (compile time; another value is another binary): `SP11_STICK_GIC_TIME_BOX_S` 2 (1 to 5), `SP11_STICK_GIC_CYCLE_US` 10000, `SP11_STICK_GIC_SLACK_US`
1000 (a label, not a limit), the wait 8 s, priority 0x80, PMR 0xF0, BPR1 0. Every wait is bounded by the counter (the 8 s waits by a read count
too). Stack: the state is under 2 KiB (static assert); the call graph and two exception frames stay far below the stack (tested).

Time. Each window's first timer is armed one time box (2 s) ahead, a later cycle 10 ms ahead. Window 1 takes about 2.03 s (hard bound 4.08 s),
window 2 about 2 s (the stub's bound 4 s). With the two 8 s waits: about 20 s on a passing run, 22 s when window 2 sees no interrupt, 12 s when
window 1 sees none.

## 5. The record in REPORT.DAT, and the extension-record convention

REPORT.DAT is 528,384 bytes (0x81000): the head page (the frozen interface), then the window the monitor writes through x0 = REPORT.DAT + 0x1000.
Every store of the monitor lies below out + 0x10000 (VERIFIED by reading: the static asserts of probes/xnuboot/report_layout.c, lines 524 to 586
and 604, and REPORT_RESERVE in probes/run.py:233), so the first free byte is REPORT.DAT offset 0x11000. Pass 1 put the record at 0x1000, the
first byte of that window: a collision once a monitor is called. The convention for what lives after the window, an extension record:

- It starts with an 8-byte ASCII tag, a u32 version and a u32 size, then 64-bit words, in a slot of 0x1000 bytes from 0x11000. This record is
  the first (magic `SNAPGIC1`, 1456 bytes: version 3 added the 80 bytes of the survey to the 1376 of version 2); the next slot would start at 0x12000.
- Only the wrapper (SNAPMON.BIN, which runs first) writes a slot; the reporter (SNAPREP.BIN) and the operator read it; the monitor never does.
  A zero tag means the stage did not run; a tag a reader does not know is skipped; a record that fails its own check is named damaged and not
  printed.
- The head page is not changed: its `monitor_*` fields stay zero when no monitor is called, so `sp11sb_report_check` passes (tested).

Record (little-endian, 182 words, no padding, reserved words zero; the field names are in `probes/sp11-stick-gic-record.h`): magic, version 3, size
at 0x00; outcome 0x10, substage 0x14, planned and begun class masks 0x18 and 0x1C (G 1, R 2, C 4, T 8, V 16, E 32), wrote, restore_failed and
restore_retried 0x20 to 0x28 (one bit per register of the table), first outcome 0x2C, done flag 0x30, next cycle 0x34, counter Hz 0x38, time box
0x40, slack 0x48, cycle 0x50 (ticks), GICD base 0x58, GICR base 0x60, then index, INTID, GICD TYPER and IIDR, GICR WAKER and TYPER, ICC_SRE_EL2,
ICC_CTLR_EL1, CNTHCTL_EL2 0x98, resume SPSR 0xA0. Five interrupts from 0xA8, 0x48 bytes each: deadline, tick at the vector, ELR, SPSR, IAR,
HPPIR1 at the vector and after the end, CNTHP_CTL at the entry, flags, latency in ticks and in us (no ESR: INFERRED that an IRQ entry leaves ESR_EL2 as it was);
0 to 2 the acknowledged cycles, 3 the final
one with no acknowledge, 4 the EL1 interrupt. Two windows from 0x210, 0x88 bytes each: arm tick, deadline, entry counts, the stub's HVC flag, the
first other entry (vector, ESR, ELR, FAR, SPSR), the diagnosis registers. Then 18 rows of `{old, new, readback, final}` from 0x320: GICD_CTLR,
IGROUPR0, ISENABLER0, ISPENDR0, ISACTIVER0, IPRIORITYR, ICFGR1, ICC_PMR, ICC_BPR1, ICC_IGRPEN1, CNTHP_CTL, CNTHP_CVAL, VBAR_EL2, DAIF, ICC_RPR,
HCR_EL2, VBAR_EL1, SCTLR_EL1. Then the survey from 0x560 (version 3, 80 bytes): CNTV_CTL, CNTV_CVAL, CNTP_CTL and CNTP_CVAL as read at EL2 (0x560 to 0x580); then
u32 words: ITLinesNumber as used 0x580, SPIs enabled 0x584, SPIs pending 0x588, SPIs both enabled and pending 0x58C, the first and the second of those 0x590
and 0x594, the priority bytes of INTIDs 23, 25, 27 and 30 0x598 (the first in the low byte), the quarantine mask 0x59C, ISENABLER0 read back right after it
0x5A0, ICC_HPPIR1_EL1 before the window 0x5A4, flags 0x5A8 (timers 1, SPIs 2, PPI priorities 4, quarantine 8, HPPIR 16) and a reserved zero word 0x5AC. A flag
says that its words are observations: a word whose flag is clear must be zero, and the reporter prints a line only for what its flags say was read, so that a
zero is never taken for an observation (the arm half built for the monitor entry never sets the timers' flag). `sg_record_check` (also in the reporter) accepts a record only with its magic, version, size, no reserved bits and every field in range (the survey's too:
counts in range, first and second consistent with both, PPI 26's bit never in the quarantine): a
record that fails is named `GIC RECORD DAMAGED CODE=n` (1 magic, 2 version, 3 size, 4 reserved or index, 5 value), one that is zero
`GIC NO RECORD: THE STAGE DID NOT RUN`; a record of version 2 is code 2.

Return value and head. The stage returns 0 whenever it ran to its end with a clean put-back, whatever the windows showed: the VERDICT line and
the record say. A refusal, a store or put-back that did not hold, and a NOT_RESTORED return `0x1000 + 82` (GIC_NOT_READY) with
`refusal_detail` = the outcome number, at the stage reached (COPIED, or PREPARED once Q, G, R, C and the check held). It is the closest existing code (section
12). The arm writes PREPARED after it succeeded.

## 6. The page lines

The wrapper adds ten lines at most (the dry run prints 19: 29 of the 30 allowed; the closing `MONITOR NOT CALLED` line is essential under the
gate): `GIC GICD`, `GIC GICR` (or `NOT_FOUND FRAMES= FIRST= TYPER0=`) and `GIC CPU` (the reads), `NEXT GIC STORES AFTER 8 S G= R= C= T= V= E=`,
`NEXT GIC WINDOW 2 S GICD= ISEN= PRIO= PMR= IGRPEN1= HPPIR=` (what the stores put in place, before window 1 opens: ISEN is what the quarantine left, HPPIR is
ICC_HPPIR1_EL1 as the check read it; pass 5 replaced `CTL=` by `HPPIR=` to stay within 100 characters, and the CNTHP control is on `GIC CPU`), `GIC IRQ INTID= LAT_US=a/b/c/d
OK=xxxx FINAL=CLEAR|PEND|NONE` (window 1's four interrupts; OK per interrupt 1, 0 or - not entered) or `GIC DIAG CTL= HPPIR= PEND= ISEN= GICD=
RPR= DAIF=` when it saw nothing; then, only if window 1 passed, `NEXT GIC EL1 EXCURSION AFTER 8 S HCR= SCTLR1= VBAR1= PCT=`, `GIC EL1 INTID=
LAT_US= EARLY|ON_TIME|LATE ELR= SPSR= OK=` or `GIC DIAG1` (the stub's bound ended it), `GIC RESTORE CLEAN= FAILED= RETRIED= BEGUN= VBAR=OK|BAD|NONE
EL1=OK|BAD|NONE`, `GIC VERDICT=<name> [FIRST=<name>] OUTCOME= SUB=`. A refusal by the quarantine or the check (Q_NOT_HELD, FOREIGN_PENDING) adds one more
line before the put-back's, `GIC QUAR MASK= DONE= HPPIR=` (below), on a page that is short then (25 lines in the model's scenarios). The reporter prints at most 28 lines of its 40 from the record, 37
with the nine of its head (VERDICT, FRAMES, GICD, GICR, CPU, OLD, SPI, PPIP, TIMERS, QUAR, NEW, TIMER, IRQ, one `GIC CYC<k> IAR= HPPIR= POST= F=<ISTATUS><PEND><ACT> TICKS=
US=` per interrupt, DIAG, EXC, EL1, DIAG1, HVC, EXC1, HCR, VBAR1, SCTLR1, FINAL, RESTORE). The four of pass 5: `GIC SPI LINES= EN= PEND= BOTH= FIRST= SECOND=` (the
distributor's INTIDs 32 and up: the ITLinesNumber used, the INTIDs enabled, pending and both, and the first two that are both, 0 for none), `GIC PPIP P23= P25=
P27= P30=` (their IPRIORITYR bytes), `GIC TIMERS VCTL= VCVAL= PCTL= PCVAL=` (the EL1 virtual and physical timers as read at EL2: CTL is ENABLE, IMASK and
ISTATUS; the dry run's build only) and `GIC QUAR MASK=<hex>|NONE DONE= HPPIR=<dec>|-` (the bits written to ICENABLER0, ISENABLER0 as read back right after it, and
ICC_HPPIR1_EL1 as read after the last store with IRQ masked); each prints only when its flags say that its registers were read. Its `GIC OLD PRIO= ICFGR1= IGROUPR0= ISPEND= ISACT=` shows ISPENDR0 and ISACTIVER0 whole (32-bit
masks), so that a foreign interrupt in flight is visible before the first store (PMR, BPR1 and IGRPEN1 are on `GIC CPU` only). A line of a phase
not reached (CPU, NEW, FINAL after an early refusal) is unfilled zeros, not an observation. One renderer (`sg_line`) writes the record's lines on both pages; each is at most 100 characters for any
record that passes the check (VERIFIED). The NEXT lines are the wrapper's own: `NEXT GIC WINDOW` is at most 99 characters for the values that can reach it (HPPIR is
1023 or 26 there, since anything else was refused FOREIGN_PENDING first); an eight-digit INTID would make it 103, which the line buffer would cut at the interface's 100,
and cannot occur (pass 6 corrected the source comment that said 100 for every value).

## 7. Store classes for the authorisation record (D4)

`tools/sp11_stick_bundle_build.py authorisation-template ... --stores ram,el2_sysreg,gic --gic-stop` names them; a class not listed in
`--stores` is withheld and the wrapper refuses `STORE_NOT_AUTH` at PIN_OK before it stores anything but REPORT.DAT page 0 (VERIFIED on the
host and under QEMU).

| Class | `--stores` | Registers (window) | Evidence |
| --- | --- | --- | --- |
| ram | `ram` | the copy and the head as the dry run; the record, 1456 bytes at REPORT.DAT + 0x11000 | INFERRED nothing else |
| G | `gic` | GICD_CTLR, only when EnableGrp1A reads clear: old value, `old \| 0x2 \| 0x1` with DS = 0 (q1n1's A16 recipe writes 0x13), `old \| 0x2` with DS = 1 (after the 8 s wait, put back after the windows) | INFERRED (section 12) |
| R | `gic` | this CPU's redistributor only (found by affinity; other frames: GICR_TYPER read), SGI frame. First the quarantine: ICENABLER0 with every bit of the found ISENABLER0 but bit 26, then GICR_CTLR.RWP. Then INTID 26: IPRIORITYR byte, ISENABLER0 bit 26 only when found clear, ICFGR1 if edge, IGROUPR0 if DS = 1. Put back: ICENABLER0 (bit 26 only, and only if the stage set it), ICPENDR0, and last ISENABLER0 with exactly the quarantined bits (not written when both group 1 gates are still open: NOT_RESTORED) | EXPERIMENTAL (the quarantine); INFERRED (the rest) |
| C | `el2_sysreg` | ICC_PMR_EL1, ICC_BPR1_EL1, ICC_IGRPEN1_EL1 | INFERRED |
| T | `el2_sysreg` | CNTHP_CVAL_EL2, CNTHP_CTL_EL2 (armed in each window for the time box, disabled again) | INFERRED |
| V | `el2_sysreg` | VBAR_EL2 and DAIF.I inside window 1 and the excursion; in the handlers ICC_EOIR1_EL1, ICC_DIR_EL1 (EOImode), SPSR_EL2, ELR_EL2 | EXPERIMENTAL; the pattern OBSERVED: el1trap, T02 |
| E | `el2_sysreg` | only after window 1 passed, a second NEXT line and 8 s: HCR_EL2 (0x80000010), VBAR_EL1, SCTLR_EL1 (0x30500800), put back HCR_EL2 first; SPSR_EL2 and ELR_EL2 for the ERET; the stub's HVC; DC CIVAC and IC IVAU over its 2 KiB table and code | EXPERIMENTAL; el1trap ran, boot 11 |

Nothing else: no CNTHCTL_EL2 write, TPIDR_EL2, ITS, write of another PPI's or SPI's registers but the enable bits that the quarantine clears and the put-back
sets again (the SPIs are only read), other redistributor beyond a read of its GICR_TYPER, SMMU, PCIe, USB,
framebuffer, secondary CPU, no SMC, SVC or TLBI. HVC is only the EL1 stub's and the EL1 table's. Beyond the brief's list: ICC_DIR_EL1, SPSR_EL2,
ELR_EL2 and the E class (the census says so, section 8). Pass 5 adds the quarantine's one ICENABLER0 write and the one ISENABLER0 write that undoes it, and
reads only: the survey (GICD_ISENABLER[n], GICD_ISPENDR[n], four IPRIORITYR bytes, and in the dry run's build the four EL1 timer registers) and ICC_HPPIR1_EL1.

## 8. Binary properties

SNAPMON-GIC.BIN is about 98 KB (the default 35,164 B), SNAPREP-GIC.BIN about 62 KB (under the reporter's 64 KiB); the build report names the
digests. Two builds are byte identical; no undefined symbol; the ELF check passes; text and rodata only, no data, no bss, no GOT, no libc.
Census (listing and bit patterns agree; each register's name is checked against its encoding): 26 MSR instructions of 15 registers:
VBAR_EL2 x5, SPSR_EL2 x4, ELR_EL2 x4, DAIFSet x2, DAIFClr x1 (immediate #2 only), ICC_PMR_EL1, ICC_BPR1_EL1, ICC_IGRPEN1_EL1, ICC_EOIR1_EL1,
ICC_DIR_EL1, CNTHP_CVAL_EL2, CNTHP_CTL_EL2 and, new in pass 2, HCR_EL2, VBAR_EL1, SCTLR_EL1, once each. New instruction classes: ERET x2 (the
vector body, the excursion), HVC x17 (one `hvc #0` in the stub, sixteen `hvc #1` in the EL1 table), IC IVAU x1 and one more DC CIVAC
(`sp11hw_make_visible`), a UDF after the stub's HVC, MRS of CNTHCTL_EL2, VBAR_EL1, SCTLR_EL1, ESR_EL1, ELR_EL1, FAR_EL1. Pass 5 adds no MSR and no new
instruction class: new are MRS of CNTV_CTL_EL0, CNTV_CVAL_EL0, CNTP_CTL_EL0 and CNTP_CVAL_EL0 (once each, in four functions of `probes/sp11-stick-gic-dry.c`:
the dry run's build only) and one MRS of ICC_HPPIR1_EL1 in the arm half (the check). As the dry run: DC CVAC,
AT S1E2W and S1E2R, DSB SY, ISB; no SMC, SVC, TLBI, WFI, WFE, BR; every BLR through the runner's ops table. MMIO stores: two one-instruction
functions (`strb`, `str`), 13 call sites (pass 5 added two: the quarantine's ICENABLER0 and the put-back's last ISENABLER0, which pass 6 made conditional: one test and a branch, no new call site, register or instruction class), all in the stage; every other
store is to the stack or through an argument. The default SNAPMON.BIN
census is unchanged (no MSR, ERET, TLBI, IC, SMC, HVC, SVC). The reporter variant has no system instruction and stores only to its stack.

## 9. Tests (host first; every one can fail)

| File | What it proves |
| --- | --- |
| `tools/test-sp11-stick-gic.py` + `tools/test-sp11-stick-gic.c` | the real wrapper, stage and reporter under ASan and UBSan against a model of the machine (`tools/sp11_stick_gic_testlib.py`, `tools/sp11_stick_gic_checks.py`): the happy path and its variants, C4 and C6, every refusal before a store (the write log proves none), windows that end badly (a lost interrupt in the EL1 window, a wrong vector class, IMO clear, a stuck line after the final deadline, a second storm, and more), registers that do not restore, stores that do not hold, a timer found enabled, and seventeen seeded-bad stages (one fault each in the order, the NEXT lines,
the end of interrupt, the re-arm, the final acknowledge, IMO, the EL1 restore, the stub's bound, the settle, keeping a foreign deadline or restoring the enable first; the six of pass 5 are in the next file, twenty-three in all), each failing its matching check while the real stage passes it |
| `tools/test-sp11-stick-gic-quarantine.py` | pass 5, over the same model: the page boot 13 printed, reproduced line for line by the stage as it ran (the seeded stage `no-quarantine`; only the digits of LAT_US, TICKS and US differ, because the model steps its counter 1000 ticks per read); the state boot 12 found (other PPIs and PPI 26 already enabled: IRQ_OK, PPI 26's own enable never written); the quarantine (one ICENABLER0 write first and one ISENABLER0 write last, ISENABLER0 whole at the end, no pending state written, nothing foreign enabled in any window, HPPIR1 1023 before the window); a foreign SPI (FOREIGN_PENDING with its INTID, clean put-back, no IRQ unmasked); a quarantine that does not hold (Q_NOT_HELD) and one whose put-back does not (NOT_RESTORED); the survey (registers read, counts, a hostile ITLines, PPI priorities, EL1 timers; a refusal leaves it unread); six seeded-bad stages (no quarantine, no put-back of it, PPI 26 quarantined too, a stray write, no check of HPPIR1, the quarantine after group 1) |
| `tools/test-sp11-stick-gic-putback.py` | pass 6, over the same model, each check shown to fail on a MUTANT of the real stage (a text patch made in memory in a scratch copy of one source; `SP11_GIC_MUTANT=<name>` runs every scenario on one mutant, and the tests that fail are the ones that kill it): the last write of the put-back is NOT made when both group 1 gates stay open (ISENABLER0 only read and named in FAILED, the run NOT_RESTORED) and is made when one gate stays open alone, when a gate was found open (the limit of section 4), and in a refused run; the RWP waits after the quarantine's write and after the last write (`RWP gicr` stuck, `RWP_LAG`); a quarantine that holds in part (`ICE_HOLD`); a put-back that fails after a refusal (`NOHOLD reg 0`: outcomes 22 and 23, and a store that did not hold). Eight mutants: no RWP condition at Q, no RWP wait at Q, a weak read-back of Q, no RWP wait after the last write, the gate rule removed, widened to either gate, not named in FAILED, and a refusal that hides a failed put-back |
| `tools/test-sp11-stick-gic-hppir.py` | pass 6, over the same model in BOTH variants of what ICC_HPPIR1_EL1 does with PMR (it honours it; or `HPPIR_IGNORES_PMR`): an enabled, pending SPI at, below and above PMR (the stage's decision in each, so that the over-acceptance and the over-refusal are on record; the rule is not changed), the special INTIDs 1020 to 1022 and the LPI range (8192, INTIDs whose low ten bits are 26 and 1023, the largest) refused FOREIGN_PENDING with their INTID and nothing unmasked, and an interrupt that asserts after the one read (`SPI_LATE`: window 1 takes it, so the check is one read at one instant). Three mutants: the specials accepted, the LPIs accepted, the INTID cut to ten bits |
| `tools/test-sp11-stick-gic-doc.py` | pass 6, text only (no model): what this note and the comments of the sources say about the put-back rule and its limit, the limits of the check of ICC_HPPIR1_EL1, the extended SPIs and the LPIs, and the numbers pass 6 corrected; each needed statement is shown to be needed by a copy without it, each corrected statement is refused when put back, and the longest NEXT WINDOW line (99 characters, 103 for an eight-digit INTID) and the 29 line ids of the reporter's page are computed from the sources |
| `tools/test-sp11-stick-gic-record.py` | the record round trip, the check (version 3: each rule of the survey isolated), damaged and extreme records through the reporter, line widths, the authorisation record (`--gic-stop`) |
| `tools/test-sp11-stick-gic-build.py` | section 8, the core object alone, the gate, hygiene, this note |
| `tools/test-sp11-stick-gic-qemu.py` | the real runner and the real binaries at EL2 under QEMU/EDK2, both windows, the survey lines and the check of ICC_HPPIR1_EL1 |

The host model proves order, bounds, put-back and report, not the assembly (the census and QEMU do) or the unit's GIC. QEMU (virt,
gic-version=3, cortex-a76): EDK2 leaves this CPU's redistributor asleep (WAKER 0x6), and the real binary refuses `GICR_ASLEEP` (VERIFIED under
QEMU). With the redistributor woken by the shell (`mm`), both windows run: four interrupts at EL2 (the
last unacknowledged, nothing pending after CNTHP_CTL = 0) and one from EL1 at +0x480 (SPSR 0x345), and every register is restored
(VERIFIED under QEMU only; QEMU's latencies say nothing about the unit's). ISENABLER0 reads clear at the call under QEMU (also when the shell was asked
to set bits before the runner started: INFERRED to be EDK2's ExitBootServices handler), and the bundle has no slot for a payload that would set it first
(SP11SB_FILES is 5), so the quarantine's own writes (ICENABLER0 with the mask, the last ISENABLER0) are NOT exercised under QEMU (`GIC QUAR MASK=NONE`): only the
host model and the census show them. QEMU does run the survey's reads and the check of ICC_HPPIR1_EL1 (1023), and the stage's own enable of PPI 26 and its put-back.

## 10. Failure table: what the page says, what it means, what next

A refusal before `NEXT` stored nothing but REPORT.DAT. Never retry the same bundle blindly. The outcome numbers (the head's `refusal_detail`): refusals before
any store 1 to 15, a store that did not hold or an arm that left a foreign interrupt pending 16 to 31 (always after a store, so always with a put-back;
Q_NOT_HELD 22 and FOREIGN_PENDING 23 are pass 5's), what the windows showed 32 to 47, NOT_RESTORED 48.

| VERDICT | Meaning | Next |
| --- | --- | --- |
| NOT_EL2, SPSEL_NOT_1, DAIF_NOT_MASKED, IMO_CLEAR | the runner's state at the call is not the one the stage needs | fix the runner state; nothing stored |
| PIN_GIC | PIN.DAT's GIC fields differ from the build's five macros, or the geometry is not walkable | rebuild bundle and wrapper from one profile |
| COUNTER | counter frequency out of range, or the counter did not run during an 8 s wait | investigate the counter |
| GICD_VIEW | GICD_CTLR has RWP, an unknown bit, or affinity routing off: not a view the stage decodes | the GICD line has the value; a new decode is needed |
| GICR_NOT_FOUND | no pinned frame's affinity matched MPIDR | check the pins and MPIDR (FRAMES, FIRST, TYPER0 on the page) |
| GICR_ASLEEP | WAKER shows sleep: waking it is not an authorised store | decide whether to authorise a WAKER write: a new class |
| PPI_OWNED | INTID 26 itself is pending or active: another owner's interrupt is in flight (other enabled PPIs, and PPI 26 enabled, are not refused) | find the owner; the GIC OLD line has ISPENDR0 and ISACTIVER0 whole |
| SRE_CLEAR | ICC_SRE_EL2.SRE is clear: the ICC registers would be UNDEFINED, so none was read | the interface is off: not this stage's to enable |
| TIMER_OWNED | CNTHP_CTL reads ENABLE and ISTATUS set, IMASK clear (0X5): the timer is asserting right now | an interrupt is firing: find it. Other states are accepted, overwritten at the arm (CVAL, then CTL) and put back |
| WINDOW_UNWRITABLE | the AT write probe of REPORT.DAT + 0x11000 failed | the record page is not writable: bundle or map |
| EL1_PCT_TRAPPED | CNTHCTL_EL2.EL1PCTEN is clear: the stub could not read the counter, its bound would never end | refused before any store |
| EL1_IMAGE | the stub or its table is not identity-mapped at EL2: with the MMU off EL1 would fetch elsewhere and abort in a loop no counter ends | refused before any store; fix the map |
| G_NOT_HELD, R_NOT_HELD, C_NOT_HELD, T_NOT_HELD | the store (or its RWP) did not read back: that class's registers are put back | the answer is the finding (non-secure write ignored, PPI not the OS's, CNTHP not writable); nothing armed |
| PRIO_NOT_BELOW_PMR | the priority reads back at or above PMR: the non-secure view differs | the OLD and NEW lines have both |
| EL1_NOT_HELD | HCR_EL2, VBAR_EL1 or SCTLR_EL1 did not read back as written, or IMO did not hold: EL1 was not entered, E is put back | the HCR, VBAR1 and SCTLR1 lines have OLD, RB, FIN: the finding |
| Q_NOT_HELD (22) | the quarantine's ICENABLER0 write (or its RWP) did not leave ISENABLER0 as bit 26 was found: the other enables are still on, or PPI 26's own went too; everything written is put back | the answer is the finding (a non-secure write of ICENABLER0 ignored, or those enables not this CPU's to clear); no group 1 enable, no window; the QUAR line has MASK= and DONE= (what was read back) |
| FOREIGN_PENDING (23) | after Q, G, R and C, with IRQ masked, ICC_HPPIR1_EL1 is neither 1023 nor 26: an enabled SPI, a PPI that did not disable, a special INTID (1020 to 1022) or an LPI's (8192 and up). One read at one instant, and whether it honours PMR is UNKNOWN (section 12) | the QUAR line has the INTID as HPPIR=, the SPI line the SPIs (FIRST=); no window, everything put back; the monitor entry, which calls the same arm, refuses the same way |
| IRQ_OK | both windows: four interrupts at EL2 each as C4 says, and the interrupt from EL1 at +0x480, ended, restored | read LAT_US: the interrupt stop is viable (U3 answered yes); the I-set entry is still UNKNOWN |
| NO_IRQ | window 1: nothing arrived by its deadline; DIAG: CTL bit 2 (ISTATUS) clear: the timer did not fire; ISTATUS set, PEND 0: the line did not reach the GIC; PEND 1 and HPPIR 0X3FF: not delivered (group, enable, priority); HPPIR 0X1A: signalled and not taken (masked, RPR) | the polling fallback (D2), with the DIAG line as the reason |
| WRONG_INTID | another INTID, or 1023 (spurious: no end of interrupt) | the IRQ line has it |
| EXCEPTION | another vector entry in window 1: the EXC line has vector, ESR, ELR | decode ESR; the table or a register access faulted |
| STORM | an interrupt before its programmed deadline, or more entries than the cycles allow, or still signalled after the end | the line is stuck or the timer did not stop; IRQ stays masked |
| CYCLE_LOST | one of the three re-armed deadlines produced no interrupt (the CYC lines show which) | the re-arm did not take, or the level did not drop: D2 |
| HPPIR_NOT_26 | HPPIR1 at a vector entry was not 26 although the interrupt came | the GIC signals another interrupt: group or priority view |
| NO_ISTATUS | CNTHP_CTL.ISTATUS was clear at a vector entry | the interrupt was not the timer's condition |
| NOT_CLEAN | after EOI (DIR) HPPIR1 is not 1023, or the PPI is pending or active | the level did not drop or the end did not take (CYC F=) |
| FINAL_PENDING | after the final deadline (no acknowledge) and CNTHP_CTL = 0 something stayed pending | the monitor's stop path would leave an interrupt: D2 |
| NO_IRQ_FROM_EL1 | nothing arrived at +0x480 by the stub's bound; HVC #0 ended it; DIAG1 and the HVC line have ISTATUS, HPPIR, PEND, ISEN, GICD, RPR, PMR, HCR, SPSR | as NO_IRQ, for the guest's case |
| EL1_WRONG_INTID | another INTID or 1023 from EL1 | the EL1 line has it |
| EL1_EXCEPTION | any other class or vector in window 2: EXC1 has vector (0x100 + n: an EL1 vector), ESR, ELR, SPSR | decode ESR; the stub or its table faulted |
| NOT_RESTORED | a put-back did not hold after one retry; FAILED names the registers (ISENABLER0 among them, bit 2, when the quarantine's last write was withheld because both group 1 gates, ICC_IGRPEN1_EL1 and GICD_CTLR, are still open), FIRST what the windows or a refusal had shown | stop the series: the machine is not as found; with both gates open the foreign interrupts are also still disabled |

Last line on a freeze or reset: `NEXT GIC STORES AFTER 8 S` (in the wait or at the first store, which is the quarantine's ICENABLER0 write when
something is enabled; the record in RAM names the class begun, if RAM
survived; at that write it does not hold the quarantine's mask yet and the reporter prints no QUAR line, section 12), `NEXT GIC WINDOW 2 S` (the timer is armed, IRQ unmasked at some point) or `NEXT GIC EL1 EXCURSION AFTER 8 S` (E writes, ERET, the stub;
window 1's page is already filmed).

## 11. Proposed supervised plan (for the coordinator; nothing here authorises it)

One boot, one autorun step, one hardware client; AC power or at least 50% battery; thermal guard as the other runs; the operator present
and filming. Manifest, exactly: `\SP11\SNAPMON.BIN` (from SNAPMON-GIC.BIN), `\SP11\SNAPREP.BIN` (from SNAPREP-GIC.BIN), `GUEST.DAT`,
`PIN.DAT`, `REPORT.DAT`, built with `--stores ram,el2_sysreg,gic --gic-stop`; the record's SHA-256 is the authorisation, named by the
operator. Read the page: up to `NEXT GIC STORES AFTER 8 S` it is read-only evidence; the stores follow 8 s later, the EL1 writes 8 s after
`NEXT GIC EL1 EXCURSION AFTER 8 S`. Total window time: about 20 s from the first NEXT line to the last (section 4). Stop rules: a refusal
ends the boot; freeze on a NEXT line or no page change for 15 s: note the
last line and power cycle (nothing writes to disk); two resets at the same last line stop the series; NOT_RESTORED stops the series; a fault
page is a finding. After IRQ_OK or a failed window with its DIAG, the next work is the M1 stop design (D2), not a retry.

## 12. Decisions, deviations, risks, change requests

GICD_CTLR reading (INFERRED). 0x10 read non-secure is ARE_NS (bit 4) with EnableGrp1A (bit 1) and EnableGrp1 (bit 0) clear and DS (bit 6)
clear: two security states, EL3 owns group 0 and secure group 1. So the non-secure group 1 enable IS needed (class G), and the A16's
precondition `(GICD_CTLR & 0x80000012) == 0x12` does not hold. The stage does not rely on this reading: it decodes the value it reads, plans G
only if EnableGrp1A is clear, and refuses otherwise. With DS = 0 IGROUPR0 is RAZ/WI to non-secure writes, so none is made; ISENABLER0 reading
back after the stage's write (made only when bit 26 was found clear) shows whether PPI 26 is ours (else R_NOT_HELD). The GTDT's non-secure EL2
timer (GSIV 26, level, active high) is OBSERVED.

Boot 12 (OBSERVED, one unit, from a photo of the page): the first stage refused PPI_OWNED in the reads and stored nothing
(`GIC VERDICT=PPI_OWNED OUTCOME=0XA SUB=READS`, `REFUSED=GIC_NOT_READY STAGE=COPIED DETAIL=0XA`, return 0x1052). What was read: GICD_CTLR
0x10, TYPER 0xF87F051E, IIDR 0x401043B (the boot 12 note read 0x4010438 from the photo: one digit misread, corrected by the boot 13 video); GICR TYPER 0xD0000E7, WAKER 0, ISENABLER0 0x4E800001, IPRIORITYR[26] 0xA0, ICFGR1 0 (every PPI level),
IGROUPR0 0, PPI 26 neither pending nor active. The CPU interface and the timer were never read in that boot: the zeros the reporter printed for
them are unfilled fields. The first rule (the whole of ISENABLER0 clear) was too strict for the firmware's SGI 0 and PPIs 23, 25, 26, 27 and 30;
section 4 has its replacement. IGROUPR0 = 0 says nothing about PPI 26's group (INFERRED: the group registers are Secure-only when DS = 0 and
GICD_TYPER bit 10, SecurityExtn, is set); a Non-secure read of its enable and priority suggests Non-secure group 1 (INFERRED, specification not
re-read) and proves no delivery. The model scenario `unit-boot12` reproduces the state and reaches IRQ_OK without setting bit 26 (since pass 5 it writes
ICENABLER0 once first and ISENABLER0 once last: the quarantine) (VERIFIED on the host only).

Boot 13 (OBSERVED, one unit, one boot, 2026-11-16, from one video read frame by frame): the second dry run ran its reads, its stores and window 1, took ONE
interrupt, INTID 27, not 26, about 2.0 s before the CNTHP deadline (`GIC IRQ INTID=27 LAT_US=-1999619/-/-/- OK=0--- FINAL=NONE`, `GIC VERDICT=WRONG_INTID
OUTCOME=0X22 SUB=DONE`), and put everything back (`GIC RESTORE CLEAN=YES FAILED=0X0 RETRIED=0X0 BEGUN=GRCTV VBAR=OK EL1=NONE`); return 0, no fault, no reset;
window 2 was not reached, as designed. Read for the first time on the unit: ICC_SRE_EL2 0xF, EOImode 1, PMR 0xF8, BPR1 3, IGRPEN1 0, CNTHP_CTL 0x2 (masked,
disabled), IPRIORITYR[26] 0xA0, and ISPENDR0 0x08000000 before any store: INTID 27 was already pending, and enabled (ISENABLER0 bit 27). The stores held:
GICD_CTLR written 0x13 read back 0x12 (EnableGrp1A held; bit 0 does not stick in the non-secure view with DS = 0), IPRIORITYR[26] 0xA0 to 0x80, IGRPEN1 1,
PMR 0xF8 before and after the stage's write of 0xF0 (INFERRED that the write was ignored: the page shows 0xF8 twice; the held check passes because the
priority is below the PMR read back); ISENABLER0 was not written (bit 26 was set). After EOIR1 and DIR HPPIR1 still read 27, a level source still asserting,
and PPI 26 itself showed F=000. OBSERVED: P1 the reads of the CPU interface and the EL2 timer work at EL2; P2 the stores of G, R (priority), C and T held; P3 an
enabled, pending PPI is delivered to EL2 as an IRQ with ICC_IGRPEN1_EL1 1, GICD EnableGrp1A 1, PMR 0xF8, PSTATE.I clear and HCR_EL2.IMO 1, the wrapper's own
vector table, IAR1, EOIR1, DIR and the put-back working; P4 the firmware left INTID 27 pending and enabled (the unit's GTDT gives Virtual EL1 GSIV 27, Secure
EL1 29, Non-secure EL1 30, Non-secure EL2 26, all level, active high). INFERRED, until the survey reads CNTV_CTL_EL0: that INTID 27 is the EL1 virtual timer
asserting. Still UNKNOWN: U3 proper (PPI 26 itself: window 1 ended at the first interrupt, before CNTHP's deadline), whether INTID 27 is the only foreign source
(SGI 0 and PPIs 23, 25 and 30 are enabled too, and the SPIs were not read), the I-set entry (window 2). That is pass 5: the survey reads what is enabled and
pending beyond PPI 26, the quarantine silences this CPU's other enabled interrupts for the run, and ICC_HPPIR1_EL1 is read before the window. The model scenario
`unit-boot13` reproduces the page line for line but the digits of LAT_US, TICKS and US (VERIFIED on the host only); the stage as it ran is the seeded stage
`no-quarantine`. The monitor stops on any interrupt that is not PPI 26, so the same INTID 27 would have ended the M1 run at its first IRQ unmask.

The study of the monitor entry (read-only), checked against the code: the monitor's report ends below out + 0x10000
(section 5); it arms CNTHP and sets IMO and FMO itself (probes/xnuboot.S:172-178; VBAR_EL2 at :116, DAIF masked at :82); its lower-EL IRQ vector
is +0x480 (:260); its tick path reads HPPIR1, IAR1, re-arms and ends (probes/xnuboot/async.c:90-106) and its stop path (:165-171) never reads
IAR. Two differences: the study cites :158-170 for the stop path (a few lines off), and says the guest runs with PSTATE.I set at EL1 where the
brief says clear. The code enters the guest masked (`mov x12, #0x3c5`, xnuboot.S:158; the comment at :175 says EL1 cannot mask). Window 2 runs
with I clear, as the brief says. That an IRQ routed to EL2 is not masked by PSTATE.I at EL1 is INFERRED, UNKNOWN on the unit: a third phase
with I set would answer it, at the price of one more outcome and one more authorisation.

Deviations: T before V, so that no exception can arise between installing the table and opening the window (the brief's order was G, R, C, V,
T); PREPARED written by the arm; the extra census entries of section 8; one code for every GIC refusal. Pass 5: the quarantine is a member of class R, not a
class of its own (so BEGUN= on the page is unchanged: GRCTV); its two outcomes are in the 16 to 31 range, not among the refusals 1 to 15, because they come
after a store; the check of ICC_HPPIR1_EL1 accepts 1023 and 26 and treats every other value, the special INTIDs included, as foreign.
UNIMPLEMENTED: the monitor entry, the real M1 stop, secondaries, waking a redistributor, the ITS, LPIs. UNKNOWN: U3, the latency, the group and security of
PPI 26, whether the quarantine holds, the foreign sources the survey will find, the I-set EL1 entry (ICC and CNTHP access at EL2 held in boot 13, OBSERVED,
one unit). Risks: the stub's fetch depends on the identity map and on
cache maintenance that QEMU cannot show; a stuck-high line, or a foreign timer whose deadline passed during the run and is put back, can never clear
its pending bit, so it ends NOT_RESTORED (loud; the series stops; model only); the quarantine switches SGI 0 and four PPIs of this CPU off for about 20 s
(nothing else runs on this CPU then: INFERRED, the wrapper runs after the runner has left the firmware; no other CPU's redistributor is written), and the
pending state of those interrupts is never written, so a source that asserts through the run is pending, and enabled again, when the run ends (not when both
group 1 gates stay open: the quarantined interrupts then stay disabled and the run is NOT_RESTORED, section 4).
Before window 1 can answer U3 only these can still stop a run (boot 13 passed every read and every store check up to window 1, and CNTHP_CTL read 0x2
there, so TIMER_OWNED, which is only for a timer asserting now, did not apply): Q_NOT_HELD (the quarantine's non-secure ICENABLER0 write is the first of its
kind: the experiment itself), FOREIGN_PENDING (an enabled SPI, or a source the quarantine did not silence: the OLD line shows ISPEND whole, and the SPI, PPIP
and TIMERS lines name the rest), PPI_OWNED if PPI 26 is then pending or active, and TIMER_OWNED if CNTHP is then asserting; G_NOT_HELD, R_NOT_HELD,
C_NOT_HELD, T_NOT_HELD and PRIO_NOT_BELOW_PMR held in boot 13 in the same state (OBSERVED, one unit, one boot).

Pass 6 (a third independent review of pass 5; EXPERIMENTAL, host only, nothing ran on the unit). The put-back's last write is withheld when both group 1 gates stay
open (section 4): `if (r->begun & SG_CLASS_R) gic_put_back_bits(g, SG_R_ISENABLER0, gic_mask(g, SG_R_ISENABLER0), 1, !gic_both_gates_open(r));`, and a gate found open is not
counted. The tests that did not bite now do (section 9: `RWP gicr`, `RWP_LAG`, `ICE_HOLD`, `NOHOLD reg 0`, each shown to fail on a mutant of the real stage). The check of
ICC_HPPIR1_EL1 has two limits, stated and not changed: it is ONE read at ONE instant (no settle, no second read: a source that asserts after it is for the window or the
monitor to meet, and the model shows window 1 taking one), and whether the register honours PMR is UNKNOWN (the tests run both variants): if it does, an enabled and pending
SPI at or below PMR passes (over-acceptance: it is not signalled unless something raises PMR), if it does not, such an SPI is refused (over-refusal). The special INTIDs 1020
to 1022 and an LPI's (8192 and up) are refused as foreign. The extended SPIs and the LPIs are neither surveyed nor quarantined, and whether any is enabled or pending is
UNKNOWN: the survey reads the SPIs the distributor implements, INTIDs 32 to 991 on the unit (ITLinesNumber 30), and no read was added. The register bits are the evidence that they may exist (GICD_TYPER 0xF87F051E: ESPI 1 with range 31,
1,024 extended SPIs from INTID 4096; GICR_TYPER low word 0xD0000E7: PLPIS 1; the bit positions are INFERRED, the specification was not re-read), and only the one
ICC_HPPIR1_EL1 read before the window, at one instant, can see one. Statements corrected, none changing a byte of a binary: the NEXT WINDOW line's "at most 100 characters for
any value" (103 for an eight-digit INTID, which cannot reach it), "at most 23 lines" of the reporter (twice) and "at most 27" of the record header (28, 37 with the head),
"classes G, R and C" in `probes/sp11-stick-gic.h` (the quarantine, the check and outcomes 22 and 23 were missing), "no other bit of ISENABLER0 is ever set by the stage"
(false since pass 5), "the other PPIs" (SGI 0 omitted), and this note's "every line is at most 100 characters" (section 6). Left, as the reviewer allowed (not a two-line
change): after a freeze at the ICENABLER0 write the record in RAM holds BEGUN=R and no quarantine mask, because the record rule ties the mask to SG_SV_QUAR, "the write was made"
(`sg_survey_check`), and moving it earlier needs a new flag, a new rule, the reporter and the pinned record text; the mask is derivable (the found ISENABLER0 of the GICR line less
bit 26: 0x4A800001 on the unit). And RETRIED is set for PPI 26's own-bit step in the rare case that its enable was found set and went clear with nothing to write: the verdict is
NOT_RESTORED either way.

Change requests (the frozen files are untouched): (1) GIC_NOT_READY (82) says "did not read back as written" in `probes/sp11-stick-bundle.h` and
`tools/sp11_stick_bundle.py`; docs/SP11-STICK-BUNDLE.md now widens it (documentation only) to every refusal and failure of this stage,
`refusal_detail` naming the outcome of section 10. (2) The interface should name the extension-record rule of
section 5. (3) The text of the authorisation record that `--gic-stop` prints (`tools/sp11_stick_bundle_record_gic.py`, another owner's) says that the ISENABLER0 write is the
last register write of the stage and that the quarantined interrupts are enabled again when the run ends: both are incomplete since pass 6, because the write is withheld
when the put-backs of both ICC_IGRPEN1_EL1 and GICD_CTLR failed (ISENABLER0 is then only read and named in FAILED, and the run ends NOT_RESTORED). The pass 6 reply has the
replacement sentence.

Hardware status: NOT RUN.
