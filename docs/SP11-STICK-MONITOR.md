# SP11 stick wrapper: the monitor entry (work item 4b, Phases A to E)

Status: EXPERIMENTAL. Hardware NOT RUN. Nothing in this note authorises a run: the operator authorises by the SHA-256 of the record that
`--m1-run` makes (sections 10 and 12), and only then. Labels: VERIFIED (a host test of this tree shows it; never a hardware result), OBSERVED (a run on the unit,
one unit), INFERRED, EXPERIMENTAL, UNIMPLEMENTED, UNKNOWN. This work item has no hardware result, so nothing here is VERIFIED on the unit.

## 1. What it is

SNAPMON-M1.BIN is the stick wrapper (docs/SP11-STICK-WRAPPER.md) with the monitor of the M1 profile appended and CALLED. After COPIED the
wrapper synchronises the instruction caches, checks what the call relies on, arms the GIC with the arm half of the GIC stage (the quarantine of this
CPU's other enabled interrupts, classes G, R and C with their read-back, and the check of ICC_HPPIR1_EL1; docs/SP11-STICK-GIC.md section 4), calls the
monitor, and puts the GIC back, the quarantine's enables last (not written when the put-backs of both group 1 gates failed: the stage then ends NOT_RESTORED with that quarantine
still on; section 16). The monitor owns the CPU until it returns: nothing of the wrapper runs
meanwhile, so its stops are its own (the EL2 timer's interrupt) and the power switch (docs/SP11-MONITOR-PORT.md stop rules).

Opt-in at compile time: `SNAP_SP11_STICK_MONITOR=1` (needs `SNAP_SP11_STICK_GIC=1`, so `SNAP_SP11_STICK=1`; only 0 and 1; `#error` otherwise).
With it at 0 nothing here is compiled; the dry-run and GIC binaries rebuild byte for byte (section 8). The variant is a separate pair,
SNAPMON-M1.BIN and SNAPREP-M1.BIN: a different SHA-256 is a different authorisation.

## 2. Files

| File | Role |
| --- | --- |
| `probes/sp11-stick-monitor.c` (about 20 KB) | the stage; the call trampoline (assembly in a C file); seeded faults (host build only) |
| `probes/sp11-stick-monitor.h` | parameters, `#error` checks of the pins, the PARAMS offsets |
| `probes/sp11-stick-monitor-record.h` | the record at REPORT.DAT + 0x12000 and the page lines (one renderer for wrapper and reporter) |
| `probes/sp11-stick-reporter.c` | gated: the record lines, the window decode, the GIC subset |
| `probes/sp11-stick-wrapper.c`, `-hw.h` | gated hooks only: the stage call, the gate, thirteen EL2 and five ID reads, IC IALLUIS, the call's prototype |
| `tools/sp11_stick_wrapper_build.py` | `--monitor`, `--monitor-build`, `--monitor-offset` |
| `tools/test-sp11-stick-monitor{,-build,-record}.py`, `.c`, `sp11_stick_monitor_testlib.py` | sections 8 and 9 |
| `tools/test-sp11-stick-monitor-call.S` | Phase C: the boot stub that runs the trampoline's real bytes at EL2 under QEMU (section 13) |
| `tools/sp11_stick_bundle_m1.py`, `sp11_stick_bundle_build.py`, `sp11_stick_bundle_record.py` | Phase B (section 12): `--m1-run`, the build report read against the files, `PARAMS_LAUNCH`, `M1_TOUCHES` |
| `tools/sp11_monitor_census.py`, `tools/test-sp11-stick-monitor-auth.py`, `tools/test-sp11-stick-monitor-interlock.py` | Phase B: the record checked against the binaries; the suites (the second: the interlock, reach and the corrected statements) |
| `tools/sp11_stick_bundle_record_m1.py`, `tools/test-sp11-stick-monitor-{census,builder,devices}.py` | Phase D (section 14): the blocks of the M1 record that are checked against the binary or the sources (the guest SMC paragraph, the device section 3b, the placed entries); the three new suites |
| `tools/sp11_stick_bundle_record_m1.py` (`GIC_LINES`, `GIC_FACT_LINES`, `PASS6_LINES`), `tools/test-sp11-stick-monitor-arm.py`, `tools/test-sp11-stick-monitor.c` (`MONREADS`) | Phase E (section 15): the record's `gic` class after pass 5 and its test against the host model's logs; the arm refusals 22 and 23; the harness counts the MMIO reads that the model does not log |
| `tools/test-sp11-stick-monitor-gates.py`, `tools/sp11_stick_monitor_testlib.py` (`patches`, `reads_at`), `tools/test-sp11-stick-monitor.c` (`MONREADS POINT`) | Phase E part 2 (section 16): the exception to the put-back's last write (both group 1 gates left open) in the record text, the comments and the tests, with mutants of the stage |

## 3. Design

**Layout: append, do not link.** SNAPMON-M1.BIN = the wrapper image, zero padding to `OFFSET` (0x20000, a multiple of 4 KiB, so the monitor's
vectors, 2 KiB in, are 2 KiB aligned), then the profile build's `xnuboot.bin` byte for byte. The wrapper calls it with a direct `bl` to the symbol
`sp11_monitor`, which `--defsym` places at OFFSET (R_AARCH64_CALL26 against an absolute symbol: on q1n1's ELF check's allow-list; no BTI pad
needed for a direct call). One compilation of both would change the monitor's bytes and its profile digest; the monitor builds `-Os -mcmodel=tiny
-flto`, the payload recipe `-O2 -mcmodel=small`. Build order: monitor, its digest, wrapper (with `-DSP11_MONITOR_OFFSET/BYTES/SHA0..3`),
concatenation. The runner hashes the whole file before and after its bounce (q1n1's docs/SP11-PAYLOAD.md); the wrapper also hashes the monitor image in
place against the pinned digest before it calls (a wrapper paired with another monitor is refused, not called).

**The call.** `int64_t _start(u64 *out, u64 work, u64 work_size, const struct params *params, const struct secondary_config *host_cpus)`
(probes/xnuboot.S): x0 = REPORT.DAT + 0x1000; x1, x2 = PIN.DAT `work_base`, `work_size` (240 MiB, checked by the frozen geometry check); x3 = the
PARAMS chunk's destination (guest_base + 0xC000 in the real bundle); x4 = 0 (no physical secondary). The trampoline keeps x19, x29, x30, switches SP
to the stack of S2 below, `bl`s, restores. The monitor is an AAPCS64 function (it keeps x19 to x30 and SP); like the wrapper that ran on the unit it
uses x18 freely (INFERRED harmless: the dry run returned to the runner).

**Stages** (frozen 8 to 13; after MONITOR_ENTERED nothing may be refused: `sp11sb_report_check`):

| Step | What | Failure |
| --- | --- | --- |
| COPIED 9 | checks, reads only: the record slot and the stack area probed writable (AT), the monitor's 64 KiB window and the 184 KiB filler below the stack's area zero, the PIN.DAT's `build_label` starting with `M1RUN-` (D2, section 14: after the probe, so that its refusal can store the record), PARAMS (time box 1 to 5 s, `early_stop.flags` 0, `gpu.flags` 0), the five ID registers, the monitor image hashed in place | refuse GIC_NOT_READY, detail 64 to 73 and 75; the record is stored with it, except for 72 REPORT_UNWRITABLE: its slot is what was found unwritable, so the page says it and nothing is stored there |
| | `IC IALLUIS; DSB ISH; ISB` | none |
| | `sw_gic_arm`: the reads and the survey, the NEXT line, 8 s, then the stores in this order: the quarantine Q (a member of class R: one ICENABLER0 write, when this CPU has interrupts enabled besides PPI 26), G, R, C, each read back; then ICC_HPPIR1_EL1 read with IRQ masked (1023 or 26 passes); sets PREPARED | refuse GIC_NOT_READY, detail = the GIC outcome (this arm half returns 1 to 11, 13, 16 to 18, 20, 22, 23 and 48; 12, 14, 15, 19 and 21 are the dry run's own; Q_NOT_HELD 22 and FOREIGN_PENDING 23 are pass 5's, section 15); `mon_arm_failed` puts back first (the quarantine's enables last) and finishes the GIC record (sub-stage DONE) as the dry run's `gic_finish` does; the monitor is not called |
| PREPARED 10 | thirteen EL2 registers read ("pre"); the stack area filled with a pattern | none |
| MONITOR_ENTERED 11 | record stored (CALLING), head stored (`monitor_out`, `work_*`, `params_pa`, `ticks_launch`), the MON CALL line printed, the counter read once more, then the call | none |
| MONITOR_RETURNED 12 | extension slots compared with what was stored before the call; window cleaned by VA; x0 and window words 0 and 1 stored; registers read again and compared; stack and filler scanned, zeroed and cleaned | none: a difference is a finding |
| DONE 13 | GIC put back (C, R, G, and last the quarantine's enables, unless both group 1 gates failed to go back: section 16) and its record finished, this record stored, lines printed, `payload_entry` returns 0 | none |

No frozen refusal code fits the new checks: GIC_NOT_READY (82, side W) is reused with a detail from a new range (see section 11, change request 1).
A quarantine return (0x51554E01 or 0x51554E02, probes/xnuboot-terminal.h) is recorded and printed with "POWER CYCLE"; the wrapper still returns
0 and puts the GIC back (registers, not RAM); the reporter then decodes nothing of the window, which is partial.

**Stack: option S2.** The call runs on `[REPORT.DAT + 0x41000, + 0x81000)`, 256 KiB, the A16 host's size (q1n1 entry.S `q1n1_stack`), not on the
runner's 64 KiB (`Q1N1_PAYLOAD_STACK_BYTES`). H7 (tools/test-sp11-stick-monitor-build.py, the profile ELF with the test record, 542 functions):
non-recursive roots `xnuboot_prepare` 3,760 B, `xnuboot_report` 576, `xnuboot_async` 416, `xnuboot_pauth_resume` 400, `xnuboot_setup` 112; the
largest single frame is 7,584 B (`user_fused_apply`); `xnuboot_trap`, the path every guest exit takes, recurses (the analyser stops at
`pmgr_boot_access`), and there are 324 `blr` and 9 `br` sites. A handler also holds `_start`'s 96 B and the 272 B exception frame. So the numbers
are lower bounds and the bound the runner's stack would need (about 40 KiB after its 16 KiB fault reserve and the wrapper's own use) cannot be
shown. S2 costs one more RAM use to authorise (the area is inside REPORT.DAT, which the wrapper already writes) and gives the size of the stack q1n1
gives the A16 host's calls (entry.S); that the monitor needs no more is INFERRED, and the measurement below cannot replace the inference: it is a lower bound. The area is
filled with 0xC3A5C3A5C3A5C3A5 first and scanned after: `STACK=<used>/<bytes>` on the page and in the
record is the depth of the first run, to a word (the distance from the top to the lowest word the monitor changed). `STACK FULL` (flag 0x40) says that the stack
may have overrun: the monitor wrote the area's lowest word or anything below the area. Below the area lie the 184 KiB of REPORT.DAT between the records and the
area, the filler [0x13000, 0x41000), which a frame that skips the lowest word reaches without changing it. The filler must be zero before the call (else the check
refuses WINDOW_DIRTY, as for a stale window) and is scanned after it: a non-zero word sets STACK FULL, and the wrapper zeroes the filler and cleans it, as it does the
area. An overflow that goes on past the filler reaches the records and is seen by the extension compare (`WINDOW OVERRUN`); one that goes on into the monitor's window
is not seen by the wrapper, which cannot tell it from the monitor's data (a stack of 448 KiB or more is not expected, INFERRED from H7's lower bounds).

## 4. The page, and what it means

At most 30 lines (frozen). The stage prints, after `STAGE=COPIED`: `MON IDS` (two lines), `MON IMAGE` (offset, bytes, 8 digest bytes, the time box
and the two PARAMS flags), the GIC reads and the NEXT line (WI 8), `MON CALL` (x0 to x4 and SP), then after the call `MON RET`, `MON REGS`,
`MON QUARANTINE` (only then), `GIC RESTORE`, `MON VERDICT`, `STAGE=DONE RETURN=0`. A `MON QUARANTINE` page is 30 lines; the lines that can be dropped
(IDS, IMAGE, TICKS) are the non-essential ones. The page of an arm refusal, Q_NOT_HELD (22) or FOREIGN_PENDING (23), is 29 lines in the host model, one under the budget (Phase E, section 15).

| Page | Meaning | Next |
| --- | --- | --- |
| runner `PAYLOAD REFUSED=...` | the runner refused SNAPMON-M1.BIN (hash, size, ABI) | fix the file |
| `REFUSED=<NAME>` at stage <= GUEST_OK | bundle, pins or placement disagree; no RAM beyond REPORT.DAT written | frozen table |
| `MON VERDICT=<PARAMS_* or IMAGE_* or REPORT_UNWRITABLE or WINDOW_DIRTY or PIN_LABEL>`, `REFUSED=GIC_NOT_READY DETAIL=0X40..0X49 or 0X4B` | a check before the call; stage COPIED, placement written, no register written | the named field |
| `GIC VERDICT=<name>`, `REFUSED=GIC_NOT_READY DETAIL=0X1..0X30`; for Q_NOT_HELD (0X16) and FOREIGN_PENDING (0X17) also `GIC QUAR MASK= DONE= HPPIR=` before `GIC RESTORE` | the GIC arm refused or a store did not hold (U3), or a foreign interrupt was next; the GIC is put back (when both group 1 gates fail to go back the quarantine's enables are not written: DETAIL 0X30, section 16), no `MON CALL` line, nothing called | docs/SP11-STICK-GIC.md section 10 |
| last line `MON CALL ...`, no `MON RET` | the monitor never returned: unbounded guest (timer class or vector), a long prepare, or a fault in its first instructions. A hang or a reset then leaves in DRAM only the head and the CALLING record, both stored and cleaned before the call (its `ticks_call` is the counter read before the MON CALL line, section 5): the window is cleaned by the wrapper after the call returns, and the monitor's own clean at its exit covers the guest range, not REPORT.DAT (`publish(c->params->mem_low, c->params->mem_high)`, `probes/xnuboot/report.c:244`; `publish` is `probes/xnuboot/mmu_access.c:193`; INFERRED: the placement is above REPORT.DAT). All of it only if RAM survives the restart (U4) | power cycle |
| q1n1 fault page after `PAYLOAD CALL` | a fault before the monitor's `msr vbar_el2` (offset 0x8c in the existing ELF) or in `xnuboot_setup/save`; else a wrapper bug. `_start` does not look at `xnuboot_setup`'s return value (below) | `llvm-nm -n` on the ELF |
| `STACK=OVERFLOW` on `PAYLOAD DONE` | the runner's stack overran (not S2's) | report |
| `MON RET=0X0 STATUS=0X0` | the monitor returned; the reporter decodes the window | SNAPREP |
| `MON REGS RESTORED=NO CHANGED=0X.. <names>` | the monitor did not put those registers back | finding, not a refusal |
| `WINDOW OVERRUN` | the extension slots changed during the call | finding |
| `GIC RESTORE CLEAN=NO` | a GIC register did not go back | finding |
| `MON QUARANTINE` | partial window; RAM must not be reused (RET 0x51554E01 or 02) | power cycle |

Two lines say quarantine and are not the same thing on a photographed page. `MON QUARANTINE: THE REPORT IS PARTIAL AND RAM MUST NOT BE REUSED. POWER CYCLE` is the monitor's own
return code (0x51554E01 or 0x51554E02) after a call, and it follows a `MON CALL` line. `GIC QUAR MASK= DONE= HPPIR=` is the arm half's quarantine of the firmware's other enabled
interrupts: it is printed before any call, only when that half refused (Q_NOT_HELD or FOREIGN_PENDING), and no `MON CALL` line follows it.

**`_start` ignores a refused setup** (the reviewer's finding 8). `probes/xnuboot.S:97-100` is `bl xnuboot_setup`, `mov x1, sp`, `mrs x9, tpidr_el2`, `str x1, [x9]`: x0, the `int`
that `xnuboot_setup` returns (1 for a work area that is small or not 4 KiB aligned, `setup.c:15,22`, for a `txm_native` that is not zero, `:75`, and others; 0 at `:117`), is
never examined, and setup sets TPIDR_EL2 at `setup.c:40`, after three of its returns. After an early `return 1` the entry goes on with the TPIDR_EL2 the runner held and a half-built
context: INFERRED a wild store through that register or a fault in the monitor, shown at best as a q1n1 fault page. The wrapper's frozen geometry check and the builder's `PARAMS_LAUNCH`
keep the returns this wrapper could reach out of the way (INFERRED); nothing in the wrapper can see the value.

SNAPREP-M1.BIN (after a warm restart, if RAM survives: U4) prints the head's summary, then `MON VERDICT`, IMAGE, IDS, CALL, RET, REGS, QUARANTINE,
then, when RET is 0: `MON STATUS=OK` or `MON PREPARE FAILED CODE=-90 NV2_NO_RECORD` (the codes of probes/run.py PREPARE_FAILURES, named by a
switch; -90 to -93 the NV2 gate, -94 and -95 PAuth normalisation, -146 to -148 the SP11 guards) or `MON EL2 FAULT ESR= EC=` with `MON FAULT ELR= OFF=
FAR=` (OFF = ELR - the monitor's base, for `llvm-nm -n`), `MON COUNTS`, `MON STOP <kind> PC A B C` (the terminal word: S2_FAULT is the expected
first success), `MON EXIT` (the guest's PC, SPSR, ESR, FAR at an interrupt stop), five events (the SP11 kind-5 events 0x4E56, 0x4E57, 0x5041,
0x5443 carry the NV2 verdict, MMFR2/4, PAuth and timer-guard values) and up to six console lines folded to the panel font; then five GIC lines.
At most 35 of 40 lines (the worst case the tests build: a guest that ran, eight events, a long console), at most 95 of 100 characters. SNAPREP-M1.BIN is read only (section 6) and about 58 KB of the 64 KiB cap.

## 5. The record (REPORT.DAT + 0x12000, 512 bytes)

REPORT.DAT: head page (frozen), the monitor's window `[0x1000, 0x11000)` (the monitor stores nothing at or above out + 0x10000: a static
assertion in probes/xnuboot/report_layout.c), then one page per extension: the GIC record at +0x11000, this record at +0x12000; +0x41000 on is the
stack. Written whole before the call (sub-stage CALLING) and after each later step.

| Offset | Field |
| --- | --- |
| 0x00 | magic `SNAPMNT1`, u32 version 1, u32 size 512 |
| 0x10 | u32 outcome (below), u32 substage (NONE CHECKS SYNCED ARMING ARMED CALLING RETURNED DONE), u32 flags, u32 changed |
| 0x20 | counter_hz; ticks_call, ticks_return (CNTPCT_EL0; ticks_call is read before the MON CALL line for the CALLING record and again immediately before the call for the final one, so TICKS leaves out the line and the head's store); monitor_pa, monitor_offset, monitor_bytes |
| 0x50 | monitor_sha[4]: the digest as four little-endian words |
| 0x70 | stack_top, stack_bytes, stack_used |
| 0x88 | call_x[5] (x0 to x4 as passed) |
| 0xB0 | ret (x0 on return), status, entered (window words 0, 1) |
| 0xC8 | box_s, early_stop_flags, gpu_flags (the PARAMS words read) |
| 0xE0 | id[5]: ID_AA64MMFR2_EL1, ID_AA64MMFR4_EL1, REVIDR_EL1, ID_AA64ISAR1_EL1, ID_AA64ISAR2_EL1 |
| 0x108 | 13 rows of (pre, post): DAIF, SPSEL, VBAR_EL2, HCR_EL2, TPIDR_EL2, SCTLR_EL2, TCR_EL2, CNTHCTL_EL2, CNTHP_CTL_EL2, MDCR_EL2, VTTBR_EL2, VTCR_EL2, VMPIDR_EL2 |
| 0x1D8 | 40 zero bytes |

Flags: 1 IC_SYNC, 2 CALLED, 4 RETURNED, 8 STACK_S2, 0x10 WINDOW_OVERRUN, 0x20 REGS_CHANGED, 0x40 STACK_FULL (the monitor wrote the area's lowest word or anything below the area), 0x80 GIC_NOT_RESTORED. `changed` bit i is
row i; compared under masks (DAIF 0x3C0, SPSEL 1, CNTHP_CTL_EL2 bits 1:0: ISTATUS is read only; the rest whole). The monitor puts them back at its
exit (probes/xnuboot/report.c:6-47) from values it saved: HCR_EL2, VBAR_EL2, TPIDR_EL2 (last) and DAIF at entry (setup.c:126-131); VTCR, VTTBR, VMPIDR,
CNTHCTL and MDCR in prepare (prepare.c:398-399, 440, 446-447, 498-499); CNTHP by stopping the timer. A prepare refusal before those saves (-24, -15, -14,
-22, -17 and the other early checks; -22 and -26 also come after them, so the rows tell) leaves them zero, and the exit then writes zeros, not what they
held, into MDCR_EL2, VTTBR_EL2, VTCR_EL2, VMPIDR_EL2, TTBR0_EL1, TTBR1_EL1, MAIR_EL1 and MDSCR_EL1 (report.c:10,23-27,41; the saves are prepare.c:367-372,
398-399, 440, 498-515): expect CHANGED rows for the first four on such a status and none for the other four; that this is harmless to the non-VHE EL2
runner is INFERRED. SCTLR_EL2 and TCR_EL2 are read because the native PAC helper writes them with the MMU on (compat/pauth-native.S:77-85, with TLBI ALLE2
and the key registers at 93-172), called ungated from probes/xnuboot/pauth.c:142-159 by the SPTM sign and authenticate endpoints: reached only if the guest
calls one (INFERRED; whether it does inside the time box is UNKNOWN), and the record lists those writes as CONDITIONAL. VNCR_EL2 is NOT read (UNDEFINED
without NV2) and is not put back.
Outcomes: 0 NONE; refusals 64 PARAMS_PA, 65 PARAMS_SIZE, 66 PARAMS_EARLY_STOP, 67 PARAMS_GPU, 68 PARAMS_BOX, 69 IMAGE_SIZE, 70 IMAGE_UNREADABLE, 71
IMAGE_DIGEST, 72 REPORT_UNWRITABLE, 73 WINDOW_DIRTY, 74 GIC_NOT_READY, 75 PIN_LABEL (Phase D); results 96 RETURNED, 97 QUARANTINE, 98 RET_OTHER. The reporter's check names the
first failing class: 1 magic, 2 version, 3 size, 4 reserved, 5 values.

The PARAMS words (VERIFIED against clang's record layout of the monitor translation unit, M1 profile, no SNAP_I2C_INPUT): `sizeof(struct params)`
0x2410, `watchdog_seconds` 192, `gpu.flags` 0x2340, `txm_native` 0x23C0 (32 bytes, not read: Phase B's builder checks it), `early_stop.flags` 0x23E0
(bit 1 lets the monitor call a function pointer that PARAMS holds, and bit 3 makes its setup allocate `c->thermal`, which opens the loads of `compat/a16-thermal.c` from the A16's sensor addresses: section 14). The wrapper's check of these is a snapshot, not a
guard: it reads the two flag words once, before the call, and `txm_native` not at all (the builder checks all three); stage 2 gives the guest RW over DRAM and
the reserve (`probes/xnuboot.py:243`, INFERRED) and the monitor re-reads `gpu.flags` and `early_stop.flags` live (`async.c:79,111`, `trap_dispatch.c:461-705`,
`early_stop_thermal.c:66`: `xnuboot_async`, `xnuboot_trap`, `early_stop_host_tick`), so a guest that sets one opens that path.

## 6. Census (the wrapper binary)

VERIFIED by tools/test-sp11-stick-monitor-build.py from the disassembly and, for MSR, from the code words by their bits.
- Writes: ICC_PMR_EL1, ICC_BPR1_EL1, ICC_IGRPEN1_EL1, one each, in three out-of-line functions (the GIC arm's class C and its put-back). NO write of
  CNTHP, VBAR_EL2, HCR_EL2, DAIF, an EL1 register or ICC_EOIR1/DIR; no ERET, SVC, HVC, SMC, TLBI, WFE, WFI, `br`.
- Reads new in this variant (13): VBAR_EL2, TPIDR_EL2, CNTHCTL_EL2, CNTHP_CTL_EL2, MDCR_EL2, VTTBR_EL2, VTCR_EL2, VMPIDR_EL2, ID_AA64MMFR2_EL1,
  ID_AA64MMFR4_EL1, REVIDR_EL1, ID_AA64ISAR1_EL1, ID_AA64ISAR2_EL1. The rest are the dry run's and the GIC arm's (CurrentEL, MIDR, MPIDR, CNTFRQ,
  CNTPCT, CTR, HCR, SCTLR, TCR, PAR, SPSel, DAIF, ICC_SRE_EL2, ICC_CTLR_EL1, ICC_PMR/BPR1/IGRPEN1/RPR).
- System instructions: DC CVAC, DC CIVAC, AT S1E2R/W, DSB SY, ISB (as before) and one NEW class: `IC IALLUIS; DSB ISH; ISB`, one instruction in one
  function (`sp11hw_ic_ialluis`). The unit's CTR_EL0 0xB444C004 (OBSERVED, docs/SP11-MONITOR-PORT.md) has IDC and DIC set (INFERRED from the bits), which
  makes this insurance; it is the cheap way to be sure no stale line of what the firmware left at the guest's addresses is executed.
- GIC memory stores: only the arm half's functions call the two MMIO writers; the stage never does (a test lists the callers by name).
- Stores otherwise: the stack, arguments that point into the caller's stack, and `sp11hw_store64` (REPORT.DAT, the placement during the copy).
- One direct `bl` to the absolute address OFFSET, in `sw_monitor_call`.

## 7. Position independence, and the build tool's refusals

The monitor is "flat PIC" only by a comment in tree (`compat/nv2-gate.c`). The tool relinks the profile build's own objects with `--emit-relocs`
under probes/probe.ld, runs q1n1's `sp11_payload_elfcheck.py` on the result and requires the flat bytes to equal the monitor's: VERIFIED for the
test-record build (1,560 CALL26, 356 ADR_PREL_LO21, 50 JUMP26, 1 CONDBR19, nothing absolute). A seeded object with an absolute pointer fails it
(`MONITOR_NOT_PIC`, ABS64). A binary built with a different NV2 record is a different binary: this proof is repeated by the tool for each one.

Tool: `python3 tools/sp11_stick_wrapper_build.py --out build/<dir> --monitor <profile>/xnuboot.bin --monitor-build <profile>/profile-build.json
[--monitor-offset 0x20000]`. REFUSED= names: MONITOR_BUILD_BAD (record absent, not format 1, not profile sp11-m1, HARDWARE not NOT RUN, no ELF or
object beside it), MONITOR_MISMATCH (bytes, digest or ELF differ from the record; or the objects do not link to the monitor), MONITOR_LAYOUT
(`_start` not at 0, vectors not at 0x800, offset not a page), MONITOR_NOT_PIC, GIC_MACROS_DIFFER (the profile's five macros against the monitor
build's), WRAPPER_TOO_BIG, IMAGE_TOO_BIG (these seven are `MONITOR_REFUSALS` in the tool, apart from `REFUSALS`, which SP11-STICK-WRAPPER.md lists
name for name and has no room for them), and the older names. It prints the digests of the wrapper, the monitor and the whole file, and writes
`build-report.json` (including the NV2 record digest the monitor was built with). The record's flags are held to the rules of `sp11_m1_profile.load_profile` (required defines,
forbidden names and parts, the profile's required base defines, one NV2 header define), not only the five GIC macros: flags it would refuse make the record `MONITOR_BUILD_BAD`.
`_start` and the vectors are checked in the relinked ELF (`monitor-pic/xnuboot-relocs.elf`), the one shown to flatten to the monitor, as well as in the record's. What the position
proof cannot show: an address dependence that is not a relocation, an address built from integers, `.init_array` and TLS are not examined; and `profile-build.json` is
self-attesting, a file the profile tool wrote: its digests tie the files together, nothing authenticates it, and nothing proves that the objects were compiled with its flags.

## 8. Host-first evidence

Run from the repository root with `source <env>` first (clang, lld, llvm, QEMU on PATH; Q1N1_SP11_TREE set). Counts are tests, all PASS here.

| H | Suite | What | Tests |
| --- | --- | --- | --- |
| H1, H2, H6 | test-sp11-stick-monitor.py | H1: a fake monitor returns ok, a prepare failure, an ESR, both quarantine codes, another value; leaves HCR, VBAR, TPIDR, DAIF, CNTHCTL, MDCR, VTTBR, CNTHP changed; stores beyond its window (three offsets); uses 1 KiB, 128 KiB, all of the stack. H2: order (copy, IC sync with no GIC access yet, arm, PREPARED, call); exact x0 to x4 and SP; no store to the placement after MONITOR_ENTERED; GIC writes only classes G, R (the quarantine is a member) and C to this CPU's frame; every check before the call (eight refusals, four GIC arm refusals). H6: the stop timer's interrupts as `async.c` takes them (transcribed): HPPIR1 26, IAR1 26, re-arm, EOIR1 (+ DIR with EOImode), post 1023, three times, then the deadline without an acknowledge leaves nothing pending; also from the OBSERVED firmware state of section 9. Phase C: the GIC record the reporter reads after a failed put-back, the filler below the stack (three overflows), the unwritable record slot, the tick of the call, and the model's dirty lines (section 13). Phase D: the PIN.DAT's label (section 14, D2): the reviewer's mix of a `--gic-stop` PIN.DAT with the M1 image, a marker that is late or one byte short, and a one-character facts label that passes | 33 |
| H3, H4, H7 | test-sp11-stick-monitor-build.py | section 7; the gate off (the dry-run pair is the unit's, byte for byte; the GIC pair is not changed by this item: built twice from snapshots, with the earlier files as committed and as edited); the census; the stack census; hygiene | 33 |
| H5 | test-sp11-stick-monitor-record.py | the record decoded independently; the page lines of wrapper and reporter against an independent renderer; the reporter on REPORT.DAT files built in Python; the window offsets and PARAMS words compiled against the monitor's own translation unit; the event and status names against the host's tables | 19 |
| H8 | test-sp11-stick-monitor-build.py, class Trampoline | Phase C (section 13): the real bytes of `sw_monitor_call` run at EL2 under QEMU's `-cpu max`; the monitor and the wrapper as a whole are NOT RUN under QEMU (EDK2 sets T0SZ 16 under `-cpu max`; FEAT_NV2 there is UNKNOWN) | 4 (the suite: 37) |
| H9 | test-sp11-stick-monitor-census.py | Phase D (section 14, D3): the second review's mutants (an MSR, an SMC, a TLBI, a DC and an IC written over the first instruction of `xnuboot_report`; an MRS turned into an MSR in the wrapper; a data word that is an MSR), each failing the placed check that the name check passes; the ties of both ELFs to section 1b; the parser and the decoder | 20 |
| H10 | test-sp11-stick-monitor-builder.py | Phase D (D2, D4): the label marker in PIN.DAT, refused in the facts of every kind and when it does not fit; the two EFI digests; `real --m1-run` refused unless the panel is withheld and the HVC patch is 0; `real --m1-run` run end to end on the host (SKIP without the J700 boot set) | 14 |
| H11 | test-sp11-stick-monitor-devices.py | Phase D (D1, D5, D6, D7): the families of device code against the symbol table, the families the build leaves out, every SMC owner, the sources the gates are read from, the interrupts the monitor acknowledges, the thermal text, the kernelcache scan (SKIP without the boot set) and the hygiene of the new files | 22 |
| H12 | test-sp11-stick-monitor-arm.py | Phase E (section 15, E1 and E2): the record's `gic` class against the registers the host model of the arm half logs in 17 runs, its offsets and outcome numbers, the place for pass 6; the arm outcomes 22, 23 and 48 refused before the monitor is called (the call counter, the put-back, the page at 29 of 30 lines, the reporter without `GIC QUAR`); five seeded-bad stages | 27 |
| H13 | test-sp11-stick-monitor-gates.py | Phase E part 2 (section 16): the put-back's last write is not made with both group 1 gates left open (after a refusal and after the call) and is made with one gate stuck or a gate found open; the record's claims about it against the model; four mutants of the stage and nine faulty texts | 20 |

Seeded-bad stages (a test define each; the real stage passes the check, the seeded one fails it; all as required): `no-params-check`, `no-digest`,
`no-reg-check`, `no-ic-sync`, `stray-gic` (a write to another CPU's redistributor frame), `wrong-args` (x1 and x2 swapped), `no-s2`,
`no-overrun-check`, `store-after` (a byte to the placement after MONITOR_ENTERED), `refuse-after` (a refusal after the call: the frozen head check
rejects it); from Phase C `no-gic-finish`, `no-filler-scan`, `no-filler-precheck`, `no-filler-clean`, `flush-unwritable`, `ticks-early`, `no-window-clean`, `no-record-clean`; from Phase D `no-label-check`.
Build-side: an absolute-pointer object, a wrapper over OFFSET, a monitor that is not the named build's, a record of another profile,
different GIC macros, a misplaced entry or vectors, an offset that is not a page, a monitor that is empty or over the 8 MiB bound: each REFUSED by name. The identity check can fail (an un-gated
letter in a panel line changes the digest), the drift check can fail, and the replay ends in EV_ASYNC for a PPI whose group changed, a foreign
HPPIR value and a timer that never fires. What no host test shows: the monitor's code, the trampoline's execution on the unit's CPU with the MMU and caches on (QEMU runs it with both off), the unit's GIC, caches, timing.

## 9. The GIC as the unit showed it (OBSERVED once), and H6

The dry run of docs/SP11-STICK-GIC.md refused in its read phase (boot 12, one unit, from a photograph): ISENABLER0 0x4E800001 (SGI 0 and PPIs 23, 25,
26, 27, 30 enabled), IPRIORITYR[26] 0xA0, all PPIs level, PPI 26 neither pending nor active, GICR awake, GICD_CTLR 0x10 (ARE_NS 1, DS 0), GICD_TYPER
0xF87F051E, IIDR 0x401043B (the boot 12 note read 0x4010438 from the photograph: one digit misread, corrected from the boot 13 video, OBSERVED). These are the initial state of the `happy-observed` and `replay-observed*` cases.
WI 8's first relaxation of the precondition (pass 4, superseded: accept firmware-enabled PPIs, refuse only PPI 26 pending or active, write ISENABLER0 only when bit 26 is clear and leave the
other enabled PPIs as found) let the arm store GICD_CTLR (G1A), IPRIORITYR[26] = 0x80 and the class C registers and put them back (VERIFIED in the model). Its RISK (INFERRED then) was that PPIs 23, 25, 27 and 30
stay enabled while the guest runs and IRQ is routed to EL2: any of them arriving ends the run with EV_ASYNC (`async.c`: only HPPIR1 == 26 is the tick). Boot 13 showed it in the dry run (INTID 27, the EL1
virtual timer by the unit's GTDT, pending and enabled from firmware; OBSERVED from video), and pass 5 (e9bc7c1, docs/SP11-STICK-GIC.md) replaced the rule. Since pass 5 the arm half (1) takes every enabled
bit of this CPU's ISENABLER0 but PPI 26's away first, in one ICENABLER0 write (0x4A800001 where firmware left 0x4E800001); (2) writes ISENABLER0 for two purposes only: PPI 26's own bit, when it was found clear
(not on the unit's state), and, as the last write of the run, the bits it cleared (not written, since pass 6, when the put-backs of both group 1 gates failed: section 16); (3) refuses FOREIGN_PENDING (detail 23) when ICC_HPPIR1_EL1, read with IRQ masked after the stores, shows anything
but 1023 or 26, and Q_NOT_HELD (detail 22) when ISENABLER0 does not read back as PPI 26's bit alone after the quarantine. The cases above expect the first and the last of those writes. Whether PPI 26 arrives at
EL2 at all (U3), and from the guest at EL1, is UNKNOWN until the dry run's second half runs. What the monitor does with an interrupt that is not PPI 26 is unchanged (section 14, D7); the record's `gic` class
says what the arm half does since pass 5, per register, duration and outcome (section 15), and since pass 6 the one exception to its last write (section 16).

## 10. For Phase B: the record text, the builder and the authorisation

Phase B is implemented (section 12); this is the design it started from, kept as it was. The binary cross-check of section 12 found its text
incomplete (it left out the native PAC helper's writes and every secondary-CPU write, and said that the PAC keys are not put back, which the key bank switch does), so the text the record carries is `M1_TOUCHES` in `tools/sp11_stick_bundle_record.py`, not the list below.

Phase B (not part of this item) adds to `tools/sp11_stick_bundle_build.py` and its record module: (a) `--time-box-s 5` (D5: `TIME_BOX_MISMATCH` against
PARAMS word 24); (b) a builder-side `PARAMS_LAUNCH` refusal: `early_stop.flags` (0x23E0), `gpu.flags` (0x2340) and the 32 bytes of `txm_native` (0x23C0)
zero; (c) SNAPMON-M1.BIN's digest, offset and the NV2 record digest in the record; (d) the monitor files given to the existing `--monitor`/`--reporter`.
Proposed text, replacing the touches the record lists today (`el2_sysreg` omits VMPIDR_EL2, VNCR_EL2, SPSR/ELR_EL2, CNTHP_*, every EL1 register, the debug
registers, the keys and the maintenance, and names OSLAR, which is conditional and not taken on this unit):
- `el2_sysreg` (wrapper): IC IALLUIS; ICC_PMR_EL1, ICC_BPR1_EL1, ICC_IGRPEN1_EL1 (class C, with `gic`). (monitor): DAIF, TPIDR_EL2, VBAR_EL2, HCR_EL2
  (three sites), VTCR_EL2, VTTBR_EL2 (with TLBI VMALLS12E1IS), VMPIDR_EL2, CNTHCTL_EL2, VNCR_EL2, MDCR_EL2, SPSR_EL2, ELR_EL2, CNTHP_CVAL/CTL_EL2; the
  guest's EL1 regime: MAIR, TCR, TTBR0/1, VBAR, ESR, FAR, CNTKCTL, SCTLR, MDSCR, DBGBVR0-5 and DBGBCR0-5_EL1; the ten EL1 PAC key registers; AT, PAR_EL1,
  TLBI and DC maintenance. Restored at return except VNCR_EL2, VBAR_EL1, ESR/FAR_EL1, SPSR/ELR, SP_EL0/1, PAR_EL1 and the keys (probes/xnuboot/report.c).
- `gic` (pass 4's list; since pass 5 the record's text is section 15's, and since pass 6 it carries the exception of section 16): the wrapper's classes G, R, C and their put-back (GICD_CTLR, IGROUPR0, IPRIORITYR[26], ICFGR1, ISENABLER0 when bit 26 was clear and, since pass 5, ICENABLER0 for the quarantine and last ISENABLER0 for the bits it cleared (not when both group 1 gates failed to go back), ICPENDR0, ICC_PMR,
  BPR1, IGRPEN1); the monitor's ICC_IAR1, EOIR1 and DIR. No GIC frame of another CPU, no ITS.
- `ram`: the monitor's 64 KiB window, its 240 MiB work arena, the guest range and the cache maintenance that publishes it, REPORT.DAT's two records, and
  the 256 KiB stack in REPORT.DAT's tail (filled with a pattern, zeroed afterwards).
D8 (thermal): the monitor has no thermal guard on this machine (its own thermal code only loads from the A16's sensor addresses and is closed by `c->thermal`, which its
setup makes only if `early_stop.flags` has the thermal bit: section 14, D6); its CPU time inside the time box is the exposure. Time: the wrapper's 2.92 s (measured, the dry run), the GIC arm's 8 s wait
(`SW_GIC_WAIT_S`, an unconditional define in WI 8's header), the monitor's prepare (UNKNOWN), the 5 s box and the stop (up to 432 MiB of DC CIVAC,
INFERRED about 0.6 s): about 17 s plus prepare against the 20 s UPTIME precaution (docs/SP11-MONITOR-PORT.md; untested beyond the wrapper's own run).

## 11. What the real run needs, change requests, UNKNOWNs

From the operator: **D6**, the NV2 record (`tools/nv2_record.py ingest`, then `sp11_m1_profile.py build --nv2-record-header`, then this tool): until then
the gate refuses with -90..-93 (certain). The monitor digests in this work's evidence are built with a LOCAL TEST RECORD (tool `wi4b-test-only`,
synthetic, never offered as the unit's) and are not the unit's. **D8** as above. **D4**: the three store classes and the sub-lists of section 10.
N1 (the boot seed) is a default label, entropy UNIMPLEMENTED. D9: the panel withheld and the stock kernel (`--sptm-hvc-calls 0`); from Phase D `real --m1-run` refuses any other choice (section 14, D4).
Change requests (frozen interfaces untouched): (1) a refusal code for "a check before the monitor call failed": none fits, GIC_NOT_READY is reused
and a new code needs an `SP11SB_VERSION` bump; (2) WI 8: `#ifndef SW_GIC_WAIT_S` so that the M1 build can shorten the 8 s wait if the time budget needs it.
UNKNOWN: U3 (PPI 26), the monitor's behaviour on Oryon (RNDR U5, PAC U8, the debug-register writes), prepare duration, whether RAM survives a warm
restart (U4), the real stack depth, the NV2 verdict.

## 12. Phase B: the M1 record, the builder and the cross-check (host only)

Implemented; hardware NOT RUN. `--m1-run BUILD_REPORT` (on `authorisation-template`, `synthetic` and `real` of `tools/sp11_stick_bundle_build.py`) makes the
record of this run. BUILD_REPORT is the `build-report.json` of `tools/sp11_stick_wrapper_build.py --monitor` (it now also names the `profile`); the files come
through the existing `--monitor` (SNAPMON-M1.BIN) and `--reporter` (SNAPREP-M1.BIN). The kind needs `--stores ram,el2_sysreg,gic` and a time box of 1 to 5 s (the
wrapper refuses a longer one before the call; D5 is 5), else `OPTIONS_BAD`, as with `--gic-stop`. From Phase D it also needs `--m1-efi` for the two EFI files that run before the wrapper, and with `real` the panel
withheld and the HVC patch 0 (section 14). Without the flag nothing changes: the records of the dry run, of the
GIC and stop dry run and of its second attempt regenerate byte for byte (tested).

**Inputs, checked and not trusted** (`tools/sp11_stick_bundle_m1.py`). Every size and digest in the record is recomputed from the files. SNAPMON-M1.BIN must be the
wrapper, zero padding and the monitor byte for byte as the report says, built to refuse this monitor (its pinned offset, length and digest). The NV2 record the report
names must be in the monitor's own bytes once and parse (`tools/nv2_record.py`): the record prints its tool, evidence, build id and the four ID values the gate
compares, and says LOCAL TEST RECORD for tool `wi4b-test-only`. Refusals, each with a faulty case: `M1_REPORT_BAD` (not the report of this kind; no text of the file is
quoted), `M1_REPORT_MISMATCH` (the report does not describe the files: a digest, the layout, one changed byte of wrapper, padding or monitor, the pins, the NV2 record
absent, twice or damaged), `PARAMS_LAUNCH` (PARAMS not zero in gpu.flags 0x2340, txm_native 0x23C0, 32 bytes, or early_stop.flags 0x23E0; this kind only: the others never
call the monitor; a PARAMS chunk too short is `BUILT_SHAPE`), and the existing `TIME_BOX_MISMATCH` (PARAMS word 24) and `OPTIONS_BAD`. These are `m1.REFUSALS` with the two of the
interlock below, not `BUILDER_REFUSALS`: `docs/SP11-STICK-BUILD.md` lists those name for name and has no room (its cap went from 33,000 to 33,200 for one pointer line; nothing in it was cut).

**The interlock, both ways.** The records of the dry run and of the GIC and stop dry run say that no monitor is called, so a wrapper that calls one must not be authorised under
them. Every kind but `--m1-run` refuses a `--monitor` that calls one (`WRAPPER_CALLS_MONITOR`), found in the file by either of two signals: a direct `bl` in its first MiB into a 4 KiB
page that 16 zero bytes precede and non-zero bytes follow (the layout the build tool writes), or the text of the monitor stage (`MON REGS RESTORED=`, `MON QUARANTINE: THE REPORT IS
PARTIAL`). It cannot show a wrapper that enters a monitor by an indirect branch, by a page no zeros precede, or from a build whose text differs, a call after the first MiB, or what
runs; it has no override, so a false positive is a refusal until the signals change (the wrappers staged so far call page 0x11000 or none, and are tested not to match; the words
are `STAGE_TEXT` in `tools/sp11_stick_bundle_m1.py` and change together with the stage's, a test). The
`--m1-run` kind refuses a wrapper with no `bl` to the report's monitor offset, or one that says `MONITOR NOT CALLED` (`M1_NO_MONITOR_CALL`), and a monitor that its build report does
not name (`M1_REPORT_MISMATCH`: its digest, the layout, the offset, length and digest the wrapper was built to pin, the NV2 record). Each has a faulty case. The reporter slot is executed in
the same boot, so `--reporter` is refused for the same name in every kind, `--m1-run` included, when it holds that `bl` (`refuse_monitor_reporter`; Phase C): the `bl` alone, because
SNAPREP-M1.BIN has the stage's text by design and is the one reporter this kind authorises. Faulty case: the M1 wrapper given as `--reporter` under the default, `--gic-stop` and
`--m1-run` kinds (the third is refused for the reporter, not as `M1_REPORT_MISMATCH`: the reporter is looked at before the report is read); control: SNAPREP-M1.BIN under `--m1-run`.

**The text** (`tools/sp11_stick_bundle_record.py`; a test pins its digest): section 1b (the files taken apart, the NV2 record), the store classes as `M1_TOUCHES` instead
of `TOUCHES`, section 4 with the time budget and D8, two more stop rules and, from Phase C, the silence rule of section 5 in its own words (section 13); the text of every other kind is what it was. Budget: wrapper 2.9 s (OBSERVED once), GIC wait
8 s (`SW_GIC_WAIT_S`), prepare UNKNOWN, the box 5 s, the stop's clean of 432 MiB about 0.6 s (the plan's estimate, INFERRED) and at most 2.9 s (INFERRED bound: the dry run's 0.96 s
for 141.4 MiB included the re-hash): 2.9 + 8 + prepare + 5 + 0.6 is at least 16.5 s, at most 18.8 s, plus the prepare. The earliest restart datum is about UPTIME 17 s (INFERRED: one
boot, in a run that started secondary CPUs; plan section 10), so the run is as long as that by construction; it does not meet the plan's "well under 20 s", a precaution of low weight
(the PSCI-only control was alive at 268 s, OBSERVED). The 8 s is the operator's to change; change request (2) above is parked. D8: no thermal guard exists on this path; the monitor's own thermal code only loads
from the A16's sensor addresses and is closed by `c->thermal`, a private gate that its setup makes only if `early_stop.flags` has the thermal bit (required zero); approving the record accepts the exception.

**The record against the binary** (`tools/sp11_monitor_census.py`; the suite runs it on the monitor built with the local test record, 542 functions, and on the wrapper).
llvm-objdump finds in the monitor 66 registers written by MSR, 121 read, 13 TLBI, DC, IC and AT operations, SMC, ERET and WFE; in the wrapper 3 registers and 5
operations. The groups `wrapper` and `monitor A` to `G` of the record's el2_sysreg class are read back out of the rendered text and compared both ways: an instruction
no group names, a name the binary lacks and the wrong ELF (1b names the monitor's `elf sha256`) are failures; each of the 82 names omitted in turn fails by name. It
showed section 10's list incomplete: 17 registers (DAIFSet, CPACR_EL1, CPTR_EL2, ELR_EL1, ICC_PMR, BPR1, IGRPEN1 and SRE, MAIR_EL2, OSLAR_EL1, SCTLR_EL2, SPSR_EL1,
SPSel, SP_EL0, SP_EL1, TCR_EL2, TTBR0_EL2), TLBI ALLE1, ALLE1IS, ALLE2 and VMALLS12E1, IC IALLU, DC CVAC, CVAU and IVAC, SMC and WFE. By function (a test): SCTLR_EL2, TCR_EL2
and TLBI ALLE2 are written only in the two native PAC helpers (`pauth_native_user`, `pauth_native_sign_user`) and in `secondary_entry`; CPTR_EL2, CPACR_EL1, ICC_SRE_EL2 and the monitor's
ICC_PMR, BPR1 and IGRPEN1 only in `secondary_run`; MAIR_EL2, TTBR0_EL2 and SPSel only in `secondary_entry`; DC CVAC only in `secondary_power_off`; CNTVOFF_EL2 is read
(`vcpu_arch_prepare`) and never written. With host_cpus = 0 (x4 = 0) the secondary functions are unreached (INFERRED from the callers: the guest's CPU-release write is taken
only with host_cpus, `trap_dispatch.c:433`, a launch refuses without it, `cpu_boot.c:392`, and `secondary_entry` is entered only by PSCI CPU_ON), so CPTR_EL2, CPACR_EL1, ICC_SRE_EL2,
MAIR_EL2, TTBR0_EL2, SPSel and the monitor's three ICC writes are; SCTLR_EL2, TCR_EL2 and TLBI ALLE2 are NOT: the PAC helpers write them (group F, CONDITIONAL: the SPTM sign and
authenticate endpoints call `pauth_pointer` and `pauth_batch_sign`; the key bank switch is on the EL0 return and the guest's exit paths). SMC is the secondary code's PSCI calls (the
AFFINITY_INFO query is also inlined in `xnuboot_report`, asked only while a released secondary is unaccounted for) and the guest's two GPU calls: Phase A's text put an SMC and WFE
under the A16 early stop, which was wrong (`early_off` is the secondary entry's CPU_OFF). The distributor's address is built in `secondary_run` alone (a test). It cannot show which group is
reached when (333 indirect branches, 324 BLR and 9 BR, are not followed), MMIO by a
computed address, or the binary of the real record (the census is of the test-record build; LTO folding a record is INFERRED not to add code): run the tool on the real ELF. Phase D
adds the placement of every register, operation and data word, the ties of both ELFs to section 1b and the refusal of an unplaced data word (section 14, D3).

**Evidence.** `test-sp11-stick-monitor-auth.py` (53 tests in Phase B, 54 now): the options, each refusal, the text, the budget and D8, the census, the pinned records (`$SP11_GICDRY2_DIR`
for the third), hygiene. `test-sp11-stick-monitor-interlock.py` (37 in Phase B, 46 now): the interlock both ways on fabricated images and on the binaries, the reach claims against the census, the call
graph and the sources they cite, the corrected statements; `test-sp11-stick-monitor-build.py` (33 in Phase B, 37 now) gained the profile's flag rules, the layout check of the relinked ELF and the stated
limits of the position proof, each with a faulty case. Phase D ran `real --m1-run` once on the host (section 14, D4); the stage tree, the unit and hardware are NOT RUN. Still the operator's: D4 (the classes as the record lists them),
D6, D8, D10 (`SNAP_FAIR_TOKEN=1` is in the profile), D11 proper, N1.

## 13. Phase C: the code findings of the independent review (host only)

Implemented; hardware NOT RUN. Every change under `probes/` is inside `SNAP_SP11_STICK_MONITOR`: the four monitor-off binaries and the three regression records are unchanged
byte for byte (section 8), and the M1 pair has new digests. Each item has a test that the real stage passes and a seeded-bad stage that fails it.

| Item (finding) | Change | Test, and the seeded-bad stage that fails it |
| --- | --- | --- |
| C1 (2, HIGH) | `sw_gic_put_back` stores the GIC record when it begins (sub-stage RESTORING), so SNAPREP-M1 could print `CLEAN=YES` over a register that did not go back. After both put-backs, the refusal path and the success path, the record is finished as the dry run's `gic_finish` does: sub-stage DONE, the outcome (`NOT_RESTORED` for a failed put-back), `sw_gic_flush` | the real reporter on the REPORT.DAT of a failed put-back prints the wrapper's `GIC RESTORE` line (`CLEAN=NO`, the same FAILED mask) on both paths; a clean one reads `CLEAN=YES` on both; the stored record is DONE; `no-gic-finish` |
| C2 (5) | the filler is zero before the call (else `WINDOW_DIRTY`), scanned after it, a word there sets `STACK FULL`, then zeroed and cleaned (section 3). The flag's text is "the area's lowest word or anything below the area"; no record format change | a fake monitor that stores 4 words and 20,000 words below the area, and one that skips the area's lowest word: STACK FULL on the page and the filler zero afterwards (for the third the area scan alone saw one free word, so the flag came from the filler); a stale byte in the filler is refused; `no-filler-scan`, `no-filler-clean`, `no-filler-precheck` |
| C3 (10) | (a) `REPORT_UNWRITABLE` prints and stores nothing in the slot it found unwritable; (b) `ticks_call` is read again immediately before the call, after the MON CALL line and the head's store, and goes into the record after the extension compare (which needs the record as it was stored); the CALLING record keeps the earlier reading | no store into the record slot for an unwritable record slot or stack area, and every other refusal still stores it; the final `ticks_call` is the first counter read after the MON CALL line and later than the CALLING record's; `flush-unwritable`, `ticks-early` |
| C4 (6a) | the harness's dirty-line counts are asserted: no dirty line of the head, the GIC record or this record at the CALL mark, none of this record at a refusal, none anywhere in REPORT.DAT at the FINAL mark | `no-window-clean`, `no-record-clean`, `no-filler-clean`. The model is a dirty flag per 64-byte line: it shows that the stage cleans what it stores, nothing about when a real line is evicted or how Oryon's caches behave. A store beyond the window that is not in a record slot (`overrun-gap`) stays dirty: it is the monitor's word, not the stage's to clean |
| C5 (6b) | `sw_monitor_call` is executed: the eleven instructions of the M1 build's wrapper, at their own offset in a flat image with a fake monitor, run at EL2 under QEMU (`-M virt,virtualization=on -cpu max`, no firmware, the PL011) from a boot stub of this tree (`tools/test-sp11-stick-monitor-call.S`) | with a stack in x5 and with 0: the fake monitor sees x0 to x4 as passed and SP equal to x5 (16-byte aligned), or the caller's SP less the 32-byte frame; x0 comes back; SP, x19, x29 and x30 are as before. Six changed words each fail exactly the check that matches (SP from the wrong register, a zero stack not skipped, x19 restored from the wrong slot or never saved, the frame popped by less, an argument register clobbered); one that loses x30 never returns |
| C6 | the stop rule of this record kind: no page change for 60 seconds after the MON CALL line, note the last line and power cycle. The rule of the other kinds (twice the time box after ENTER) could end a legitimate run: the monitor prints nothing while it runs and its prepare is UNKNOWN. The 60 s is an operator-safe wait, not a measurement | the pinned digests of the M1 text; the other kinds' records regenerate byte for byte; a record that kept the old rule is another record |
| C7 | `--reporter` is refused for `WRAPPER_CALLS_MONITOR` when it holds the `bl` to an appended monitor, under every kind (section 12) | the M1 wrapper as `--reporter` under the three kinds; control: SNAPREP-M1.BIN under `--m1-run`; no reporter that ran or is staged is flagged |
| C8 | the failure table says what a hang or a reset leaves in DRAM, and section 4 what `_start` does with a refused setup (INFERRED) | documentation; the tests pin the sentences |

What C5 shows and does not: it shows that the instructions of the real function do what their comment says on QEMU's CPU model, and that each check can fail. It does not show the unit
(the MMU and the caches are off here; the runner's regime and the monitor's are not), the monitor's code, or any build but this one: the test expects the encoding of each word it
changes and says so if the compiler has made another. The verdicts are the stub's own comparisons on the CPU model, not the host's. EXPERIMENTAL.

## 14. Phase D: the second review's findings (host only)

Implemented; hardware NOT RUN. Every change under `probes/` is inside `SNAP_SP11_STICK_MONITOR`, so the monitor-off binaries are unchanged byte for byte (section 8); the M1 pair, the
M1 record text (its digest is re-pinned with it) and the test record's template have new digests. The findings are those of the second independent review (read only, on ee9814a), in its
order. Each new check has a faulty case that fails it. What the code does is VERIFIED only for what a host test shows, whether the guest reaches it is INFERRED, and nothing about the
unit is VERIFIED. The record's new blocks (the guest SMC paragraph, section 3b, the placed entries) are in `tools/sp11_stick_bundle_record_m1.py`, whose prose is filled by one function, so
that the bytes the operator approves depend on nothing else; the sentences that were wrong are reworded in place in `tools/sp11_stick_bundle_record.py`, none cut.

**D1, device code and its gates (finding 1).** The monitor binary holds code that stores to devices and calls firmware: the GPU's registers at PA 0x03d00000 to 0x03df0000 (`compat/adreno.h:42-70`;
the same base on the X1E80100 per `docs/SP11-GPU-A7XX-STUDY.md`, INFERRED for this unit), the GIC distributor at the address that PARAMS `gpu.gicd` holds (`gpu_glue.c:47-59`), a real `smc #0`
for an HVC #0 (`trap_dispatch.c:701`; `adreno_smc` has no ADOPTED test; `gpu_glue.c:10-16`) and a call through the PARAMS pointer `early_stop.host_call` (`early_stop_thermal.c:45-50`), so the record's
"no device" and "closed by gpu.flags" were wrong. Section 3b of the record names five families (adreno, early_stop, thermal, nvmeh, secondary) with their gates. PRIVATE: state in the work
area, which stage 2 makes read-only to the guest (`ADOPTED`, `c->thermal`, `host_cpus`). LIVE: a PARAMS word read again while the guest runs (`trap_dispatch.c:570,701`, `async.c:79,111,115`): with
`gpu.flags` set the guest reaches `adreno_smc` and `gpu_irq`, with `early_stop.flags` HOST and a `host_call` the pointer call. A LIVE gate is a bar a guest can lift; that it can write PARAMS is
INFERRED (stage 2 gives it DRAM and the reserve, `probes/xnuboot.py:243`) and whether the SPTM stops it is UNKNOWN (the page's typing was not traced). Not confirmed from the review:
`stage2.c:202` reads `gpu.flags` once, at prepare, to build the guest's GPU windows: it is no live gate (with the flag 0 there is no GPU window). The test
(`test-sp11-stick-monitor-devices.py`) reads the symbol table (llvm-nm): every family in the binary is named in 3b with its gate words, a family named there is in the binary, none of the
families the build leaves out (hamoa_, edp_, qcc, i2c_input, guest_net, dvfs, boot_reloc, boot_relay, display_timing, adreno_recover, adreno_coherent) is, every SMC is in a family or the report
path, and the source lines that the gates are read from are pinned. Faulty cases: a record without a family or a gate word, a `hamoa_` symbol added to a binary, an SMC in `vcpu_arch_prepare`.
Limits: the guest-device models (`sptm_*`, `sep_*`, `txm_*`, `device_*` and the like) are INFERRED to work on RAM and were not all read; the census sees system registers and not MMIO; an exploratory
scan for straight-line constant-address stores found `adreno_ring_kick`, `adreno_ring_reserve`, `tlbi` and `xnuboot_report` (the inlined `gpu_quiesce`) only and cannot see a runtime base such as
`gpu.gicd` (it is not kept as a test). The review's offer stands: compiling the families out of the M1 monitor (a profile change and new default-off gates in compat/) is a separate hardening item,
to be offered to the operator at the authorisation.

**D2, PIN.DAT bound to this record (finding 2).** For `--m1-run` only, the builder writes `M1RUN-` in front of the facts label in `build_label` (the facts label then holds 17 characters, else
`PIN_STRING`) and every kind refuses a facts label that carries it. The stage refuses a PIN.DAT whose label lacks it before the call: PIN_LABEL, SM_OUT 75, GIC_NOT_READY with that detail, after the
probe of the record slot so that the refusal stores its record. The reviewer's mix, the PIN.DAT of a `--gic-stop` bundle with the M1 image, runs through the real C stage in the harness: refused,
nothing stored but REPORT.DAT, the monitor not called; so is a marker that is late or one byte short; `no-label-check` is the seeded stage that lets it through. What it shows: which builder path made
the PIN.DAT, not what its record says; a label is no digest, and an edited PIN.DAT carries any label (the interface's checks and the operator's stager verify stand against that). The reverse, this
PIN.DAT with the dry-run or the GIC wrapper, is NOT refused: the first is frozen and the second does not know the marker. Section 1b now names SLBOUNCE-DEVHE-MARK.EFI and Q1N1-PAYLOAD.EFI by
SHA-256: `--m1-efi NAME=SHA256`, once each, required with `--m1-run` (the template, synthetic and real commands give the same text for the same inputs), refused without it, and compared with the
stage tree's `--file NAME PATH SHA256`. Nothing at boot compares them with the record (INFERRED from the review of the boot path: the runner hashes the payloads, the wrapper checks PIN.DAT and the
data): the operator's stager verify and STICK-FILES.TXT are the control. The autorun plan's name is the pinned label unless `--plan-name` is given, and it still fits the stager's rule
(`tools/sp11_autorun.py`: at most 24 characters of letters, digits, `.`, `_` and `-`); the stage tree itself is NOT RUN here.

**D3, the census places writes (finding 3).** The el2_sysreg class of the record ends with `placed` entries: for the wrapper and for the monitor every register written, every SYS operation, SMC,
ERET and WFE, and every data word that decodes as a system instruction, with the functions that hold it and how many each holds (`sp11_monitor_census.py placed --binary wrapper|monitor ELF`
prints them). The check is complete both ways, so a write that moves, doubles or appears anywhere fails. The review's ten monitor mutants (an MSR of TTBR0_EL2, SCTLR_EL2, TCR_EL2, MAIR_EL2 or CPTR_EL2, an SMC, a TLBI ALLE1IS, a DC CIVAC, an IC IALLUIS or a TLBI VMALLS12E1IS written over the first
instruction of `xnuboot_report`, the record's elf line re-synced) and the wrapper's MRS turned into an MSR pass the name check and fail the placed check. Both ELFs are tied to section 1b: their flat
bytes (llvm-objcopy -O binary) must have the size and SHA-256 that it names for the wrapper and the monitor. A data word that decodes as MSR, SYS, SYSL, WFE, WFI, SEV, SEVL, SVC, HVC, SMC, BRK, HLT, DCPS or
ERET fails unless the record places that word. One deviation from the brief: the monitor's own table of guest instruction words (`compat/native-writer.h:20-25`, six MSR words for ELR_EL1 and SPSR_EL1,
compared with the guest's code and never executed: INFERRED) would fail every check on the real binary, so the record names those six and any other, or a changed one, fails. Limits: data is decoded only in the
executable sections that llvm-objdump lists; indirect branches are not followed; the tool says which instructions exist and where, not which run; a rebuilt monitor or wrapper needs its `placed` entries
regenerated, and a stale one fails.

**D4, `real --m1-run` and D9 (finding 4).** `real --m1-run` is `OPTIONS_BAD`, the message naming D9, unless `--panel withheld` and `--sptm-hvc-calls 0`: the record says the guest gets no framebuffer and a
stock kernel, and stage 2 maps the framebuffer range from PARAMS when a panel is handed. Run once on the host: the J700 26A428 kernelcache, the M1 pair of the LOCAL TEST RECORD, the template's text approved,
17.6 s in all and GUEST.DAT 118,870,336 bytes; PIN.DAT and GUEST.DAT pass the interface's checks, `authorised_stores` is 7, the label is `M1RUN-example-synthetic`, and the census check passes on that record and both
ELFs. The test SKIPs (never passes) without the boot set. The stage tree, the unit and hardware are NOT RUN; the `SNAP_*` environment must be unset (`ENV_SNAP_SET`).

**D5, a guest SMC is not trapped (finding 5).** HCR_EL2 starts as `probes/xnuboot.S:128-142` sets it, 0xC4144001 (RW, TRVM, TVM, TIDCP, TID3, TWE, VM; no TSC, bit 19), every later write in the monitor changes other
bits (IMO and FMO `xnuboot.S:174-177`, NV and NV2 `prepare.c:466-473`, TWE `:481`, VI and VF `stage2.c:402-416`, API `pauth.c:65`, TVM and TRVM `pauth.c:116`) and `trap_dispatch.c` has no handler for EC 0x17:
an SMC that the guest executes at EL1 is not mediated (INFERRED from reading every write; a test pins it). Basis for not expecting one, reproduced: every 4-byte word of the two segments with execute permission
of the local J700 26A428 kernelcache (the only two: `__TEXT_EXEC`, 14,774,272 words, and `__TEXT_BOOT_EXEC`, 8,192; data words included) masked for SMC (0xD4000003) and HVC (0xD4000002): 0 and 0 (OBSERVED, a host
file, one kernelcache); the same scan finds 1 SVC, 1 ERET and 146 WFE, so it does find instructions. Whether the guest runs SMC code from anywhere else (a kext loaded later, an AuxKC) is UNKNOWN. The record says this
after its "Not authorised" line, with those labels, and does not claim "no firmware write"; setting TSC would change the monitor and is not done.

**D6, the thermal text (finding 6).** `compat/a16-thermal.c` only loads: SROT at 0x0c222000 + n * 0x1000, which `compat/hamoa-thermal.c` gives as this machine's TSENS SROT (INFERRED there), and TM at
0x0c22c000 + n * 0x1000, where this machine's is UNKNOWN. The gate is `c->thermal`, which `xnuboot_setup` makes only if `early_stop.flags` has the thermal bit then (`setup.c:102`): the builder requires it zero, the
wrapper checks it again and nothing makes it later (PRIVATE). `compat/hamoa-thermal.c` is not in the monitor (no `hamoa_` symbol, tested); the old text had it linked.

**D7, which interrupts are acknowledged (finding 7).** The boot CPU reads ICC_IAR1_EL1 in two places, both in `xnuboot_async` (the census: that function twice, `secondary_gic_stop`, not reached, once): INTID 26, when
`ICC_HPPIR1_EL1` names it, the timer's ISTATUS is set and the run's deadline is ahead (`async.c:87-94`), and, behind the LIVE gate `gpu.flags`, INTID 0x14C (`gpu_irq`, `async.c:79-83`). Any other IRQ or FIQ, and the timer at the
deadline, ends the run with the interrupt not acknowledged (`async.c:167`): the monitor records it, disables its timer and returns, so a foreign interrupt stays pending (INFERRED). The arm half's quarantine of the
firmware's enabled interrupts is pass 5's (section 9); Phase D left the record's `gic` class silent on it, and Phase E's text says it (section 15).

Counts: monitor 33, record 19, build 37, auth 54, interlock 46, census 20, builder 14, devices 22. The size caps of this note and of the auth suite's check of it were raised for this section; nothing was cut.

## 15. Phase E: the third review (host only): the M1 record's `gic` class after pass 5, the arm refusals 22 and 23, the stale statements

A third independent review, of the GIC stage's pass 5 (e9bc7c1), whose arm half the monitor entry calls (`probes/sp11-stick-gic.c`, shared), found what follows on the files of this item. Hardware NOT RUN;
nothing here authorises a run.

**E1, the `gic` class of the M1 record (HIGH).** The class said that "the other PPIs that firmware enabled stay as found" and nothing of the quarantine, the check of ICC_HPPIR1_EL1 or the outcomes 22 and
23: false for this build since pass 5. It is now `GIC_LINES` of `tools/sp11_stick_bundle_record_m1.py` (the literal in `M1_TOUCHES` is gone; the texts of the dry run and of `--gic-stop` are untouched, and
their digests regenerate) with the facts block `GIC_FACT_LINES` after the classes. Per register it gives, on one line (two spaces, `W` or `R`, the name): the frame and offset (D = gicd_base; RD = gicr_first +
index * gicr_stride, the frame whose GICR_TYPER matches this CPU; SGI = RD + 0x10000), when the stage touches it, what it is read back against, the outcome that names a failure and its put-back, in the order the
stage makes them: Q, the quarantine (ICENABLER0), first; G; R; C; the quarantine's enables (ISENABLER0) last, but for the exception of pass 6 (section 16). The arm half is the dry run's source built with SW_GIC_DRY_REGS=0: no CNTHP, VBAR_EL2, EL1 register or
EL1 timer; its survey is stored in the GIC record (version 3, 1456 bytes at REPORT.DAT + 0x11000) and printed by no M1 line.
- Duration. The quarantine is on from its ICENABLER0 write to the put-back's last write, which is after the monitor returns: the whole call (with the exception of pass 6, section 16, there is no such write and it is still on when the run ends). What bounds it is the monitor's own stop (the CNTHP interrupt, PPI 26, at
  the time box, 5 s at most; whether PPI 26 arrives is U3, UNKNOWN), after the monitor's prepare (duration UNKNOWN) and before its stop's clean (about 0.6 s, INFERRED) and the wrapper's scans and polls. Nothing in the
  wrapper bounds it: a call that never returns ends by the power cycle, and the put-back does not run (that the firmware sets the GIC up again at the restart is INFERRED). While it is on SGI 0 and PPIs 23, 25, 27
  and 30 cannot be signalled, the guest's EL1 timers (INTID 27 and 30 in the unit's GTDT, OBSERVED) among them: what the guest does without them is UNKNOWN.
- The page. The arm refusals reach the operator through `mon_arm_failed`: `GIC QUAR`, `GIC RESTORE`, `GIC VERDICT`, `MON VERDICT=GIC_NOT_READY`, `REFUSED=GIC_NOT_READY STAGE=COPIED DETAIL=0X16 or 0X17` (0X30 when the
  put-back fails too). The foreign INTID of a FOREIGN_PENDING refusal is on that live page only: SNAPREP-M1.BIN prints `GIC GICD`, `GICR`, `CPU`, `NEW` and `RESTORE` and has no `GIC QUAR` line.
- Labels. The boot 12 and boot 13 facts are OBSERVED, one unit, one boot each, in the dry run's build (docs/SP11-STICK-GIC.md section 12), with its INFERRED and UNKNOWN items; the text says that neither the
  quarantine nor the check has run on the unit. The offsets are the stage's map, INFERRED from the GICv3 text as remembered. The text has no VERIFIED (a test).
- The place for pass 6 was `PASS6_LINES` in the same module, empty in Phase E. Phase E part 2 (section 16) filled it: its sentence is printed after the lines of the ISENABLER0 write, and the rendered text has no marker.
- The test. The census sees system registers and not MMIO, so `tools/test-sp11-stick-monitor-arm.py` compares the text with what the host model of the stage logs outside the fake monitor, in 17 runs (7 that complete,
  10 that refuse or fail): every register written in some run is a `W` line, every register only read an `R` line, every `W` line is written in some run; the offsets (the architecture's as remembered, and
  `probes/sp11-stick-gic.h`'s), the outcome numbers and the order of the page lines are checked, and so are the conditions the text states (ICPENDR0 only when INTID 26 is left pending, IGROUPR0 only with DS set, no
  other ICC register read when SRE is clear, no write of GICR_WAKER). The model logs only some MMIO reads (GICD_CTLR, GICD_ISENABLER[n], GICD_ISPENDR[n]), so the monitor entry's harness counts the others itself
  (`MONREADS`: the model's three read functions are renamed for the include, and no GIC test file is edited); that depends on the names `sp11hw_mmio_read8/32/64`, `locate`, `mine` and `in_window` of
  `tools/test-sp11-stick-gic.c`. Faulty cases: a text without a register, with one labelled the other way, with one no run writes, with one named twice, with a wrong offset and with a wrong outcome number.

**E2, the missing cases (MEDIUM).** The arm outcomes Q_NOT_HELD (22) and FOREIGN_PENDING (23) had no case at all. `refuse-q-not-held` (the model ignores the ICENABLER0 write) and `refuse-foreign-pending` (an SPI,
INTID 40, enabled and pending: what the quarantine of this CPU's redistributor does not cover) end before the monitor is called: GIC_NOT_READY with detail 22 or 23 at stage COPIED; through `mon_arm_failed` (the points
PUTBACK and REFUSED, the monitor record GIC_NOT_READY at ARMING, the GIC record DONE with the refusal as outcome and first outcome); the call counter 0 (no CALL mark, no state line, no `MON CALL` line); every
register as found (the model's state, the record's failed and retried masks 0) and, for 23, the writes in their exact order (the quarantine first, C, R and G put back, ISENABLER0 last); the page at 29 of the 30
lines (re-measured on the host model: 29 for both and for NOT_RESTORED, 26 to 28 for the refusals before a store); and the reporter without a `GIC QUAR` line. A third case, `refuse-foreign-not-restored`, makes the
put-back of ISENABLER0 fail: DETAIL 0X30, `GIC VERDICT=NOT_RESTORED FIRST=FOREIGN_PENDING`, the register left as the quarantine left it. Seeded-bad stages: the GIC stage's `no-quarantine`, `no-hppir-check` and
`quarantine-no-putback` (their defines are `probes/sp11-stick-gic.h`'s) and two of the monitor stage's own, `SP11_STICK_MONITOR_SEED_ARM_REFUSAL_CALLS` (a refused arm does not stop the call) and
`SP11_STICK_MONITOR_SEED_NO_ARM_PUTBACK` (`mon_arm_failed` does not put back), compiled only into the host harness. Each fails the check that matches its fault on the run that shows it, and the real stage passes that
check.

**E3, stale statements (LOW).** "Classes G, R and C" without the quarantine, the check and the outcomes 22 and 23, "ISENABLER0 only when bit 26 was clear" and "the other PPIs stay as found" are corrected: the header
comments of `probes/sp11-stick-monitor.c` and `-monitor.h`, the build report's `variant` string, and this note (sections 1, 3, 4, 9, 10 and 14). Nothing but comments changed there: the dry-run pair, the GIC pair and
the M1 pair, and their ELFs, rebuild byte for byte before and after. Section 4 also tells the two quarantines apart on a photographed page: `MON QUARANTINE ... POWER CYCLE` is the monitor's own return code and follows a
`MON CALL` line; `GIC QUAR MASK= DONE= HPPIR=` is the arm half's quarantine of foreign interrupts, comes before any call and only when that half refused.

Counts: monitor 33, record 19, build 37, auth 54, interlock 46, census 20, builder 14, devices 22, arm 27. The size cap of this note and of the auth suite's check of it was raised from 60,000 to 70,000 for this section;
nothing was cut. The M1 template (`build/sp11-m1-record-template.txt`, the test record) grew from 33,082 to 45,987 bytes and from 343 to 470 lines: only the `gic` class changed, and the facts block was added. The two
digests that the auth suite pins for the text were re-pinned.

## 16. Phase E, part 2: pass 6 of the GIC stage, the exception to the put-back's last write (host only)

Pass 6 (6eed864; `probes/sp11-stick-gic.c`, `gic_both_gates_open`) changed a rule that this note and the record text still stated the old way: the put-back's last write, which enables again the bits the
quarantine cleared, is NOT made when the put-backs of BOTH ICC_IGRPEN1_EL1 and GICD_CTLR failed (both group 1 gates left open). ISENABLER0 is then only read and named in FAILED (bit 2), the quarantined
interrupts stay disabled and the run ends NOT_RESTORED (48, OUTCOME=0X30; a refusal stays FIRST). One gate stuck and the other closed is not that case (the write is made, inert behind the closed gate); a
gate found open is not counted as failed, so with GICD_CTLR found with EnableGrp1A set and ICC_IGRPEN1_EL1 stuck the write IS made behind two open gates, a limit that docs/SP11-STICK-GIC.md section 4
states. On the unit's state both gates are the stage's own (OBSERVED, boots 12 and 13). The rule holds after a refusal (`mon_arm_failed`) and after the call alike: both call `sw_gic_put_back`. The reason
is the stage's, INFERRED. Hardware NOT RUN; the rule has never run on the unit.

**P1, the record's text.** `PASS6_LINES` of `tools/sp11_stick_bundle_record_m1.py`, empty in Phase E, holds the sentence, printed as a continuation of the ISENABLER0 entry, which now says "the last register
write of the whole run, but for the exception that follows". The ICPENDR0 entry, the how-long paragraph (no last write: the quarantine is still on when the run ends, and the put-back after a refusal does
not lift it), the refusal paragraph (DETAIL 0X30), the "Never" paragraph and the facts block ("always followed by the put-back", no longer "with a full put-back") carry the exception. Measured on the host
model, the case after a FOREIGN_PENDING refusal prints 29 lines with `GIC RESTORE CLEAN=NO FAILED=0X205 RETRIED=0X201 BEGUN=GRC` (a mask: GICD_CTLR 0X1, ISENABLER0 0X4, ICC_IGRPEN1_EL1 0X200), `GIC
VERDICT=NOT_RESTORED FIRST=FOREIGN_PENDING OUTCOME=0X30` and `DETAIL=0X30`; after the call it prints `GIC RESTORE CLEAN=NO FAILED=0X205 ...` and `MON VERDICT=RETURNED FLAGS=0X8F` (GIC_NOT_RESTORED, 0X80):
no refusal.

**P2, the other places.** Sections 1, 3, 4, 9, 10 and 15, and the comments of `probes/sp11-stick-monitor.c`, `.h` and `tools/test-sp11-stick-monitor.py`. Comments only: every binary and ELF of the
monitor-off, GIC and M1 builds is what it was before this part (SNAPMON-GIC.BIN and SNAPMON-M1.BIN moved with pass 6, whose arm half they share).

**P3 and P5, the tests.** `tools/test-sp11-stick-monitor-gates.py` (20 tests) runs nine scenarios in the harness: no gate stuck (control); a refused arm (FOREIGN_PENDING, INTID 40) with both gates stuck
(`NOHOLD GICD_CTLR 0` and `NOHOLD ICC_IGRPEN1_EL1 0`, from the start of the put-back), with either, and with a gate found open; and after the call with both and with either. It holds the stage to: no write of
ISENABLER0 and the put-back's reads of it (`MONREADS POINT`: 2, against 3 with a write read back), FAILED, NOT_RESTORED, the gates still open, the page and the path (`mon_arm_failed` or the call); with one
gate, the write as the last write. It compares the claims of the text with what the runs show. Four mutants (text patches of `probes/sp11-stick-gic.c` made in memory in a scratch copy: `patches` of `harness()`
in `tools/sp11_stick_monitor_testlib.py`; one that no longer matches exactly once fails loudly) each fail the check that matches and the comparison: `gates-rule-removed` (`withheld`), `gates-either` (`made`),
`gates-not-reported` (`withheld`) and `found-open-counted` (`found_open_made`). The comparison names nine faulty texts, among them one without the sentence, one that claims the write unconditional and one with
"either" for "both".

**P4, pins, limits.** The auth suite's two pinned texts were re-pinned; the template (`build/sp11-m1-record-template.txt`, the test record) is 47,878 bytes in 487 lines (45,987 and 470 before, on the pass 6
binaries). The size cap of this note and of the auth suite's check of it was raised from 70,000 to 75,500 for this section, which leaves a few hundred bytes; nothing was cut. Limits: `NOHOLD reg 0` reads the GIC record's sub-stage to know that
the put-back began, so these tests follow `tools/test-sp11-stick-gic.c`; the comparison reads phrases, so a re-worded sentence needs `claims()` changed with it.

Counts: monitor 33, record 19, build 37, auth 54, interlock 46, census 20, builder 14, devices 22, arm 27, gates 20.
