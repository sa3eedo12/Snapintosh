# SP11 stick wrapper: the monitor entry (work item 4b, Phase A)

Status: EXPERIMENTAL. Hardware NOT RUN. Nothing in this note authorises a run: the operator authorises by the SHA-256 of the record that Phase B
adds (section 10), and only then. Labels: VERIFIED (a host test of this tree shows it; never a hardware result), OBSERVED (a run on the unit,
one unit), INFERRED, EXPERIMENTAL, UNIMPLEMENTED, UNKNOWN. This work item has no hardware result, so nothing here is VERIFIED on the unit.

## 1. What it is

SNAPMON-M1.BIN is the stick wrapper (docs/SP11-STICK-WRAPPER.md) with the monitor of the M1 profile appended and CALLED. After COPIED the
wrapper synchronises the instruction caches, checks what the call relies on, arms the GIC with the arm half of the GIC stage (classes G, R, C
only: docs/SP11-STICK-GIC.md), calls the monitor, and puts the GIC back. The monitor owns the CPU until it returns: nothing of the wrapper runs
meanwhile, so its stops are its own (the EL2 timer's interrupt) and the power switch (docs/SP11-MONITOR-PORT.md stop rules).

Opt-in at compile time: `SNAP_SP11_STICK_MONITOR=1` (needs `SNAP_SP11_STICK_GIC=1`, so `SNAP_SP11_STICK=1`; only 0 and 1; `#error` otherwise).
With it at 0 nothing here is compiled; the dry-run and GIC binaries rebuild byte for byte (section 8). The variant is a separate pair,
SNAPMON-M1.BIN and SNAPREP-M1.BIN: a different SHA-256 is a different authorisation.

## 2. Files

| File | Role |
| --- | --- |
| `probes/sp11-stick-monitor.c` (about 16 KB) | the stage; the call trampoline (assembly in a C file); seeded faults (host build only) |
| `probes/sp11-stick-monitor.h` | parameters, `#error` checks of the pins, the PARAMS offsets |
| `probes/sp11-stick-monitor-record.h` | the record at REPORT.DAT + 0x12000 and the page lines (one renderer for wrapper and reporter) |
| `probes/sp11-stick-reporter.c` | gated: the record lines, the window decode, the GIC subset |
| `probes/sp11-stick-wrapper.c`, `-hw.h` | gated hooks only: the stage call, the gate, thirteen EL2 and five ID reads, IC IALLUIS, the call's prototype |
| `tools/sp11_stick_wrapper_build.py` | `--monitor`, `--monitor-build`, `--monitor-offset` |
| `tools/test-sp11-stick-monitor{,-build,-record}.py`, `.c`, `sp11_stick_monitor_testlib.py` | sections 8 and 9 |

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
| COPIED 9 | checks, reads only: the record slot and the stack area probed writable (AT), the monitor's 64 KiB window zero, PARAMS (time box 1 to 5 s, `early_stop.flags` 0, `gpu.flags` 0), the five ID registers, the monitor image hashed in place | refuse GIC_NOT_READY, detail 64 to 73 |
| | `IC IALLUIS; DSB ISH; ISB` | none |
| | `sw_gic_arm`: reads, the NEXT line, 8 s, stores G, R, C, read back; sets PREPARED | refuse GIC_NOT_READY, detail = the GIC outcome (0 to 48); put back first |
| PREPARED 10 | thirteen EL2 registers read ("pre"); the stack area filled with a pattern | none |
| MONITOR_ENTERED 11 | record stored (CALLING), head stored (`monitor_out`, `work_*`, `params_pa`, `ticks_launch`), then the call | none |
| MONITOR_RETURNED 12 | extension slots compared with what was stored before the call; window cleaned by VA; x0 and window words 0 and 1 stored; registers read again and compared; stack scanned and zeroed | none: a difference is a finding |
| DONE 13 | GIC put back (C, R, G), record stored, lines printed, `payload_entry` returns 0 | none |

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
gives the A16 host's calls (entry.S); that the monitor needs no more is INFERRED, and the measurement below replaces the inference. The area is
filled with 0xC3A5C3A5C3A5C3A5 first and scanned after: `STACK=<used>/<bytes>` on the page and in the
record is the first run's measurement of the depth (exact to a word; `STACK FULL` if the lowest word changed). It is then zeroed and cleaned, so
REPORT.DAT beyond the records is what the runner loaded.

## 4. The page, and what it means

At most 30 lines (frozen). The stage prints, after `STAGE=COPIED`: `MON IDS` (two lines), `MON IMAGE` (offset, bytes, 8 digest bytes, the time box
and the two PARAMS flags), the GIC reads and the NEXT line (WI 8), `MON CALL` (x0 to x4 and SP), then after the call `MON RET`, `MON REGS`,
`MON QUARANTINE` (only then), `GIC RESTORE`, `MON VERDICT`, `STAGE=DONE RETURN=0`. A quarantine page is 30 lines; the lines that can be dropped
(IDS, IMAGE, TICKS) are the non-essential ones.

| Page | Meaning | Next |
| --- | --- | --- |
| runner `PAYLOAD REFUSED=...` | the runner refused SNAPMON-M1.BIN (hash, size, ABI) | fix the file |
| `REFUSED=<NAME>` at stage <= GUEST_OK | bundle, pins or placement disagree; no RAM beyond REPORT.DAT written | frozen table |
| `MON VERDICT=<PARAMS_* or IMAGE_* or REPORT_UNWRITABLE or WINDOW_DIRTY>`, `REFUSED=GIC_NOT_READY DETAIL=0X40..0X49` | a check before the call; stage COPIED, placement written, no register written | the named field |
| `GIC VERDICT=<name>`, `REFUSED=GIC_NOT_READY DETAIL=0X0..0X30` | the GIC arm refused or a store did not hold (U3); nothing called | docs/SP11-STICK-GIC.md |
| last line `MON CALL ...`, no `MON RET` | the monitor never returned: unbounded guest (timer class or vector), a long prepare, or a fault in its first instructions | power cycle |
| q1n1 fault page after `PAYLOAD CALL` | a fault before the monitor's `msr vbar_el2` (offset 0x8c in the existing ELF) or in `xnuboot_setup/save`; else a wrapper bug | `llvm-nm -n` on the ELF |
| `STACK=OVERFLOW` on `PAYLOAD DONE` | the runner's stack overran (not S2's) | report |
| `MON RET=0X0 STATUS=0X0` | the monitor returned; the reporter decodes the window | SNAPREP |
| `MON REGS RESTORED=NO CHANGED=0X.. <names>` | the monitor did not put those registers back | finding, not a refusal |
| `WINDOW OVERRUN` | the extension slots changed during the call | finding |
| `GIC RESTORE CLEAN=NO` | a GIC register did not go back | finding |
| `MON QUARANTINE` | partial window; RAM must not be reused (RET 0x51554E01 or 02) | power cycle |

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
| 0x20 | counter_hz; ticks_call, ticks_return (CNTPCT_EL0); monitor_pa, monitor_offset, monitor_bytes |
| 0x50 | monitor_sha[4]: the digest as four little-endian words |
| 0x70 | stack_top, stack_bytes, stack_used |
| 0x88 | call_x[5] (x0 to x4 as passed) |
| 0xB0 | ret (x0 on return), status, entered (window words 0, 1) |
| 0xC8 | box_s, early_stop_flags, gpu_flags (the PARAMS words read) |
| 0xE0 | id[5]: ID_AA64MMFR2_EL1, ID_AA64MMFR4_EL1, REVIDR_EL1, ID_AA64ISAR1_EL1, ID_AA64ISAR2_EL1 |
| 0x108 | 13 rows of (pre, post): DAIF, SPSEL, VBAR_EL2, HCR_EL2, TPIDR_EL2, SCTLR_EL2, TCR_EL2, CNTHCTL_EL2, CNTHP_CTL_EL2, MDCR_EL2, VTTBR_EL2, VTCR_EL2, VMPIDR_EL2 |
| 0x1D8 | 40 zero bytes |

Flags: 1 IC_SYNC, 2 CALLED, 4 RETURNED, 8 STACK_S2, 0x10 WINDOW_OVERRUN, 0x20 REGS_CHANGED, 0x40 STACK_FULL, 0x80 GIC_NOT_RESTORED. `changed` bit i is
row i; compared under masks (DAIF 0x3C0, SPSEL 1, CNTHP_CTL_EL2 bits 1:0: ISTATUS is read only; the rest whole). The monitor puts these back at its
exit (probes/xnuboot/report.c): HCR_EL2, VBAR_EL2, TPIDR_EL2 last, DAIF, VTCR/VTTBR/VMPIDR, CNTHCTL, MDCR, CNTHP. SCTLR_EL2 and TCR_EL2 are read
because the native PAC helper would write them (off in M1, INFERRED), VNCR_EL2 is NOT read (UNDEFINED without NV2) and is not put back.
Outcomes: 0 NONE; refusals 64 PARAMS_PA, 65 PARAMS_SIZE, 66 PARAMS_EARLY_STOP, 67 PARAMS_GPU, 68 PARAMS_BOX, 69 IMAGE_SIZE, 70 IMAGE_UNREADABLE, 71
IMAGE_DIGEST, 72 REPORT_UNWRITABLE, 73 WINDOW_DIRTY, 74 GIC_NOT_READY; results 96 RETURNED, 97 QUARANTINE, 98 RET_OTHER. The reporter's check names the
first failing class: 1 magic, 2 version, 3 size, 4 reserved, 5 values.

The PARAMS words (VERIFIED against clang's record layout of the monitor translation unit, M1 profile, no SNAP_I2C_INPUT): `sizeof(struct params)`
0x2410, `watchdog_seconds` 192, `gpu.flags` 0x2340, `txm_native` 0x23C0 (32 bytes, not read: Phase B's builder checks it), `early_stop.flags` 0x23E0
(bit 3 would read the A16's thermal sensors at addresses that are not this machine's: `compat/a16-thermal.c`).

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
`build-report.json` (including the NV2 record digest the monitor was built with).

## 8. Host-first evidence

Run from the repository root with `source <env>` first (clang, lld, llvm, QEMU on PATH; Q1N1_SP11_TREE set). Counts are tests, all PASS here.

| H | Suite | What | Tests |
| --- | --- | --- | --- |
| H1, H2, H6 | test-sp11-stick-monitor.py | H1: a fake monitor returns ok, a prepare failure, an ESR, both quarantine codes, another value; leaves HCR, VBAR, TPIDR, DAIF, CNTHCTL, MDCR, VTTBR, CNTHP changed; stores beyond its window (three offsets); uses 1 KiB, 128 KiB, all of the stack. H2: order (copy, IC sync with no GIC access yet, arm, PREPARED, call); exact x0 to x4 and SP; no store to the placement after MONITOR_ENTERED; GIC writes only classes G, R, C to this CPU's frame; every check before the call (eight refusals, four GIC arm refusals). H6: the stop timer's interrupts as `async.c` takes them (transcribed): HPPIR1 26, IAR1 26, re-arm, EOIR1 (+ DIR with EOImode), post 1023, three times, then the deadline without an acknowledge leaves nothing pending; also from the OBSERVED firmware state of section 9 | 18 |
| H3, H4, H7 | test-sp11-stick-monitor-build.py | section 7; the gate off (the dry-run pair is the unit's, byte for byte; the GIC pair is not changed by this item: built twice from snapshots, with the earlier files as committed and as edited); the census; the stack census; hygiene | 30 |
| H5 | test-sp11-stick-monitor-record.py | the record decoded independently; the page lines of wrapper and reporter against an independent renderer; the reporter on REPORT.DAT files built in Python; the window offsets and PARAMS words compiled against the monitor's own translation unit; the event and status names against the host's tables | 19 |
| H8 | QEMU | NOT RUN: QEMU would show the call sequence only (EDK2 sets T0SZ 16 under `-cpu max`; FEAT_NV2 there is UNKNOWN) and nothing about the unit | |

Seeded-bad stages (a test define each; the real stage passes the check, the seeded one fails it; all as required): `no-params-check`, `no-digest`,
`no-reg-check`, `no-ic-sync`, `stray-gic` (a write to another CPU's redistributor frame), `wrong-args` (x1 and x2 swapped), `no-s2`,
`no-overrun-check`, `store-after` (a byte to the placement after MONITOR_ENTERED), `refuse-after` (a refusal after the call: the frozen head check
rejects it). Build-side: an absolute-pointer object, a wrapper over OFFSET, a monitor that is not the named build's, a record of another profile,
different GIC macros, a misplaced entry or vectors, an offset that is not a page, a monitor that is empty or over the 8 MiB bound: each REFUSED by name. The identity check can fail (an un-gated
letter in a panel line changes the digest), the drift check can fail, and the replay ends in EV_ASYNC for a PPI whose group changed, a foreign
HPPIR value and a timer that never fires. What no host test shows: the monitor's code, the trampoline's execution, the unit's GIC, caches, timing.

## 9. The GIC as the unit showed it (OBSERVED once), and H6

The dry run of docs/SP11-STICK-GIC.md refused in its read phase (boot 12, one unit, from a photograph): ISENABLER0 0x4E800001 (SGI 0 and PPIs 23, 25,
26, 27, 30 enabled), IPRIORITYR[26] 0xA0, all PPIs level, PPI 26 neither pending nor active, GICR awake, GICD_CTLR 0x10 (ARE_NS 1, DS 0), GICD_TYPER
0xF87F051E, IIDR 0x4010438. These are the initial state of the `happy-observed` and `replay-observed*` cases. WI 8 relaxed the precondition (accept
firmware-enabled PPIs, refuse only PPI 26 pending or active; ISENABLER0 written only when bit 26 is clear): with it the arm stores GICD_CTLR (G1A),
IPRIORITYR[26] = 0x80 and the class C registers, leaves ISENABLER0 alone, and puts everything back (VERIFIED in the model). RISK (INFERRED): PPIs 23,
25, 27 and 30 stay enabled while the guest runs and IRQ is routed to EL2: any of them arriving ends the run with EV_ASYNC (`async.c`: only
HPPIR1 == 26 is the tick). Whether PPI 26 arrives at EL2 at all (U3), and from the guest at EL1, is UNKNOWN until the dry run's second half runs.

## 10. For Phase B: the record text, the builder and the authorisation

Phase B (not part of this item) adds to `tools/sp11_stick_bundle_build.py` and its record module: (a) `--time-box-s 5` (D5: `TIME_BOX_MISMATCH` against
PARAMS word 24); (b) a builder-side `PARAMS_LAUNCH` refusal: `early_stop.flags` (0x23E0), `gpu.flags` (0x2340) and the 32 bytes of `txm_native` (0x23C0)
zero; (c) SNAPMON-M1.BIN's digest, offset and the NV2 record digest in the record; (d) the monitor files given to the existing `--monitor`/`--reporter`.
Proposed text, replacing the touches the record lists today (`el2_sysreg` omits VMPIDR_EL2, VNCR_EL2, SPSR/ELR_EL2, CNTHP_*, every EL1 register, the debug
registers, the keys and the maintenance, and names OSLAR, which is conditional and not taken on this unit):
- `el2_sysreg` (wrapper): IC IALLUIS; ICC_PMR_EL1, ICC_BPR1_EL1, ICC_IGRPEN1_EL1 (class C, with `gic`). (monitor): DAIF, TPIDR_EL2, VBAR_EL2, HCR_EL2
  (three sites), VTCR_EL2, VTTBR_EL2 (with TLBI VMALLS12E1IS), VMPIDR_EL2, CNTHCTL_EL2, VNCR_EL2, MDCR_EL2, SPSR_EL2, ELR_EL2, CNTHP_CVAL/CTL_EL2; the
  guest's EL1 regime: MAIR, TCR, TTBR0/1, VBAR, ESR, FAR, CNTKCTL, SCTLR, MDSCR, DBGBVR0-5 and DBGBCR0-5_EL1; the ten EL1 PAC key registers; AT, PAR_EL1,
  TLBI and DC maintenance. Restored at return except VNCR_EL2, VBAR_EL1, ESR/FAR_EL1, SPSR/ELR, SP_EL0/1, PAR_EL1 and the keys (probes/xnuboot/report.c).
- `gic`: the wrapper's classes G, R, C and their put-back (GICD_CTLR, IGROUPR0, IPRIORITYR[26], ICFGR1, ISENABLER0 only when bit 26 was clear, ICC_PMR,
  BPR1, IGRPEN1); the monitor's ICC_IAR1, EOIR1 and DIR. No GIC frame of another CPU, no ITS.
- `ram`: the monitor's 64 KiB window, its 240 MiB work arena, the guest range and the cache maintenance that publishes it, REPORT.DAT's two records, and
  the 256 KiB stack in REPORT.DAT's tail (filled with a pattern, zeroed afterwards).
D8 (thermal): the monitor has no thermal guard on this machine (the A16's reads TSENS at addresses that are not this one's, unreachable with
`early_stop.flags` 0); its CPU time inside the time box is the exposure. Time: the wrapper's 2.92 s (measured, the dry run), the GIC arm's 8 s wait
(`SW_GIC_WAIT_S`, an unconditional define in WI 8's header), the monitor's prepare (UNKNOWN), the 5 s box and the stop (up to 432 MiB of DC CIVAC,
INFERRED about 0.6 s): about 17 s plus prepare against the 20 s UPTIME precaution (docs/SP11-MONITOR-PORT.md; untested beyond the wrapper's own run).

## 11. What the real run needs, change requests, UNKNOWNs

From the operator: **D6**, the NV2 record (`tools/nv2_record.py ingest`, then `sp11_m1_profile.py build --nv2-record-header`, then this tool): until then
the gate refuses with -90..-93 (certain). The monitor digests in this work's evidence are built with a LOCAL TEST RECORD (tool `wi4b-test-only`,
synthetic, never offered as the unit's) and are not the unit's. **D8** as above. **D4**: the three store classes and the sub-lists of section 10.
N1 (the boot seed) is a default label, entropy UNIMPLEMENTED. D9: the panel withheld and the stock kernel (`--sptm-hvc-calls 0`).
Change requests (frozen interfaces untouched): (1) a refusal code for "a check before the monitor call failed": none fits, GIC_NOT_READY is reused
and a new code needs an `SP11SB_VERSION` bump; (2) WI 8: `#ifndef SW_GIC_WAIT_S` so that the M1 build can shorten the 8 s wait if the time budget needs it.
UNKNOWN: U3 (PPI 26), the monitor's behaviour on Oryon (RNDR U5, PAC U8, the debug-register writes), prepare duration, whether RAM survives a warm
restart (U4), the real stack depth, the NV2 verdict.
