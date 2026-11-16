# SP11 stick wrapper: the monitor entry (work item 4b, Phases A to C)

Status: EXPERIMENTAL. Hardware NOT RUN. Nothing in this note authorises a run: the operator authorises by the SHA-256 of the record that
`--m1-run` makes (sections 10 and 12), and only then. Labels: VERIFIED (a host test of this tree shows it; never a hardware result), OBSERVED (a run on the unit,
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
| COPIED 9 | checks, reads only: the record slot and the stack area probed writable (AT), the monitor's 64 KiB window and the 184 KiB filler below the stack's area zero, PARAMS (time box 1 to 5 s, `early_stop.flags` 0, `gpu.flags` 0), the five ID registers, the monitor image hashed in place | refuse GIC_NOT_READY, detail 64 to 73; the record is stored with it, except for 72 REPORT_UNWRITABLE: its slot is what was found unwritable, so the page says it and nothing is stored there |
| | `IC IALLUIS; DSB ISH; ISB` | none |
| | `sw_gic_arm`: reads, the NEXT line, 8 s, stores G, R, C, read back; sets PREPARED | refuse GIC_NOT_READY, detail = the GIC outcome (0 to 48); put back first, and the GIC record finished (sub-stage DONE) as the dry run's `gic_finish` does |
| PREPARED 10 | thirteen EL2 registers read ("pre"); the stack area filled with a pattern | none |
| MONITOR_ENTERED 11 | record stored (CALLING), head stored (`monitor_out`, `work_*`, `params_pa`, `ticks_launch`), the MON CALL line printed, the counter read once more, then the call | none |
| MONITOR_RETURNED 12 | extension slots compared with what was stored before the call; window cleaned by VA; x0 and window words 0 and 1 stored; registers read again and compared; stack and filler scanned, zeroed and cleaned | none: a difference is a finding |
| DONE 13 | GIC put back (C, R, G) and its record finished, this record stored, lines printed, `payload_entry` returns 0 | none |

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
`MON QUARANTINE` (only then), `GIC RESTORE`, `MON VERDICT`, `STAGE=DONE RETURN=0`. A quarantine page is 30 lines; the lines that can be dropped
(IDS, IMAGE, TICKS) are the non-essential ones.

| Page | Meaning | Next |
| --- | --- | --- |
| runner `PAYLOAD REFUSED=...` | the runner refused SNAPMON-M1.BIN (hash, size, ABI) | fix the file |
| `REFUSED=<NAME>` at stage <= GUEST_OK | bundle, pins or placement disagree; no RAM beyond REPORT.DAT written | frozen table |
| `MON VERDICT=<PARAMS_* or IMAGE_* or REPORT_UNWRITABLE or WINDOW_DIRTY>`, `REFUSED=GIC_NOT_READY DETAIL=0X40..0X49` | a check before the call; stage COPIED, placement written, no register written | the named field |
| `GIC VERDICT=<name>`, `REFUSED=GIC_NOT_READY DETAIL=0X0..0X30` | the GIC arm refused or a store did not hold (U3); nothing called | docs/SP11-STICK-GIC.md |
| last line `MON CALL ...`, no `MON RET` | the monitor never returned: unbounded guest (timer class or vector), a long prepare, or a fault in its first instructions. A hang or a reset then leaves in DRAM only the head and the CALLING record, both stored and cleaned before the call (its `ticks_call` is the counter read before the MON CALL line, section 5): the window is cleaned by the wrapper after the call returns, and the monitor's own clean at its exit covers the guest range, not REPORT.DAT (`publish(c->params->mem_low, c->params->mem_high)`, `probes/xnuboot/report.c:244`; `publish` is `probes/xnuboot/mmu_access.c:193`; INFERRED: the placement is above REPORT.DAT). All of it only if RAM survives the restart (U4) | power cycle |
| q1n1 fault page after `PAYLOAD CALL` | a fault before the monitor's `msr vbar_el2` (offset 0x8c in the existing ELF) or in `xnuboot_setup/save`; else a wrapper bug. `_start` does not look at `xnuboot_setup`'s return value (below) | `llvm-nm -n` on the ELF |
| `STACK=OVERFLOW` on `PAYLOAD DONE` | the runner's stack overran (not S2's) | report |
| `MON RET=0X0 STATUS=0X0` | the monitor returned; the reporter decodes the window | SNAPREP |
| `MON REGS RESTORED=NO CHANGED=0X.. <names>` | the monitor did not put those registers back | finding, not a refusal |
| `WINDOW OVERRUN` | the extension slots changed during the call | finding |
| `GIC RESTORE CLEAN=NO` | a GIC register did not go back | finding |
| `MON QUARANTINE` | partial window; RAM must not be reused (RET 0x51554E01 or 02) | power cycle |

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
IMAGE_DIGEST, 72 REPORT_UNWRITABLE, 73 WINDOW_DIRTY, 74 GIC_NOT_READY; results 96 RETURNED, 97 QUARANTINE, 98 RET_OTHER. The reporter's check names the
first failing class: 1 magic, 2 version, 3 size, 4 reserved, 5 values.

The PARAMS words (VERIFIED against clang's record layout of the monitor translation unit, M1 profile, no SNAP_I2C_INPUT): `sizeof(struct params)`
0x2410, `watchdog_seconds` 192, `gpu.flags` 0x2340, `txm_native` 0x23C0 (32 bytes, not read: Phase B's builder checks it), `early_stop.flags` 0x23E0
(bit 3 would read the A16's thermal sensors at addresses that are not this machine's: `compat/a16-thermal.c`). The wrapper's check of these is a snapshot, not a
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
| H1, H2, H6 | test-sp11-stick-monitor.py | H1: a fake monitor returns ok, a prepare failure, an ESR, both quarantine codes, another value; leaves HCR, VBAR, TPIDR, DAIF, CNTHCTL, MDCR, VTTBR, CNTHP changed; stores beyond its window (three offsets); uses 1 KiB, 128 KiB, all of the stack. H2: order (copy, IC sync with no GIC access yet, arm, PREPARED, call); exact x0 to x4 and SP; no store to the placement after MONITOR_ENTERED; GIC writes only classes G, R, C to this CPU's frame; every check before the call (eight refusals, four GIC arm refusals). H6: the stop timer's interrupts as `async.c` takes them (transcribed): HPPIR1 26, IAR1 26, re-arm, EOIR1 (+ DIR with EOImode), post 1023, three times, then the deadline without an acknowledge leaves nothing pending; also from the OBSERVED firmware state of section 9. Phase C: the GIC record the reporter reads after a failed put-back, the filler below the stack (three overflows), the unwritable record slot, the tick of the call, and the model's dirty lines (section 13) | 31 |
| H3, H4, H7 | test-sp11-stick-monitor-build.py | section 7; the gate off (the dry-run pair is the unit's, byte for byte; the GIC pair is not changed by this item: built twice from snapshots, with the earlier files as committed and as edited); the census; the stack census; hygiene | 33 |
| H5 | test-sp11-stick-monitor-record.py | the record decoded independently; the page lines of wrapper and reporter against an independent renderer; the reporter on REPORT.DAT files built in Python; the window offsets and PARAMS words compiled against the monitor's own translation unit; the event and status names against the host's tables | 19 |
| H8 | test-sp11-stick-monitor-build.py, class Trampoline | Phase C (section 13): the real bytes of `sw_monitor_call` run at EL2 under QEMU's `-cpu max`; the monitor and the wrapper as a whole are NOT RUN under QEMU (EDK2 sets T0SZ 16 under `-cpu max`; FEAT_NV2 there is UNKNOWN) | 4 (the suite: 37) |

Seeded-bad stages (a test define each; the real stage passes the check, the seeded one fails it; all as required): `no-params-check`, `no-digest`,
`no-reg-check`, `no-ic-sync`, `stray-gic` (a write to another CPU's redistributor frame), `wrong-args` (x1 and x2 swapped), `no-s2`,
`no-overrun-check`, `store-after` (a byte to the placement after MONITOR_ENTERED), `refuse-after` (a refusal after the call: the frozen head check
rejects it); from Phase C `no-gic-finish`, `no-filler-scan`, `no-filler-precheck`, `no-filler-clean`, `flush-unwritable`, `ticks-early`, `no-window-clean`, `no-record-clean`.
Build-side: an absolute-pointer object, a wrapper over OFFSET, a monitor that is not the named build's, a record of another profile,
different GIC macros, a misplaced entry or vectors, an offset that is not a page, a monitor that is empty or over the 8 MiB bound: each REFUSED by name. The identity check can fail (an un-gated
letter in a panel line changes the digest), the drift check can fail, and the replay ends in EV_ASYNC for a PPI whose group changed, a foreign
HPPIR value and a timer that never fires. What no host test shows: the monitor's code, the trampoline's execution on the unit's CPU with the MMU and caches on (QEMU runs it with both off), the unit's GIC, caches, timing.

## 9. The GIC as the unit showed it (OBSERVED once), and H6

The dry run of docs/SP11-STICK-GIC.md refused in its read phase (boot 12, one unit, from a photograph): ISENABLER0 0x4E800001 (SGI 0 and PPIs 23, 25,
26, 27, 30 enabled), IPRIORITYR[26] 0xA0, all PPIs level, PPI 26 neither pending nor active, GICR awake, GICD_CTLR 0x10 (ARE_NS 1, DS 0), GICD_TYPER
0xF87F051E, IIDR 0x4010438. These are the initial state of the `happy-observed` and `replay-observed*` cases. WI 8 relaxed the precondition (accept
firmware-enabled PPIs, refuse only PPI 26 pending or active; ISENABLER0 written only when bit 26 is clear): with it the arm stores GICD_CTLR (G1A),
IPRIORITYR[26] = 0x80 and the class C registers, leaves ISENABLER0 alone, and puts everything back (VERIFIED in the model). RISK (INFERRED): PPIs 23,
25, 27 and 30 stay enabled while the guest runs and IRQ is routed to EL2: any of them arriving ends the run with EV_ASYNC (`async.c`: only
HPPIR1 == 26 is the tick). Whether PPI 26 arrives at EL2 at all (U3), and from the guest at EL1, is UNKNOWN until the dry run's second half runs.

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

## 12. Phase B: the M1 record, the builder and the cross-check (host only)

Implemented; hardware NOT RUN. `--m1-run BUILD_REPORT` (on `authorisation-template`, `synthetic` and `real` of `tools/sp11_stick_bundle_build.py`) makes the
record of this run. BUILD_REPORT is the `build-report.json` of `tools/sp11_stick_wrapper_build.py --monitor` (it now also names the `profile`); the files come
through the existing `--monitor` (SNAPMON-M1.BIN) and `--reporter` (SNAPREP-M1.BIN). The kind needs `--stores ram,el2_sysreg,gic` and a time box of 1 to 5 s (the
wrapper refuses a longer one before the call; D5 is 5), else `OPTIONS_BAD`, as with `--gic-stop`. Without the flag nothing changes: the records of the dry run, of the
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
(the PSCI-only control was alive at 268 s, OBSERVED). The 8 s is the operator's to change; change request (2) above is parked. D8: no thermal guard exists on this path; the A16's reads
sensors that are not this machine's and is reached only through early_stop.flags (required zero); approving the record accepts the exception.

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
computed address, or the binary of the real record (the census is of the test-record build; LTO folding a record is INFERRED not to add code): run the tool on the real ELF.

**Evidence.** `test-sp11-stick-monitor-auth.py` (53 tests in Phase B, 54 now): the options, each refusal, the text, the budget and D8, the census, the pinned records (`$SP11_GICDRY2_DIR`
for the third), hygiene. `test-sp11-stick-monitor-interlock.py` (37 in Phase B, 46 now): the interlock both ways on fabricated images and on the binaries, the reach claims against the census, the call
graph and the sources they cite, the corrected statements; `test-sp11-stick-monitor-build.py` (33 in Phase B, 37 now) gained the profile's flag rules, the layout check of the relinked ELF and the stated
limits of the position proof, each with a faulty case. NOT RUN: `real` with `--m1-run` (no kernelcache here), hardware. Still the operator's: D4 (the classes as the record lists them),
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
