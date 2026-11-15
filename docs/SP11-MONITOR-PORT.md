<!-- SPDX-License-Identifier: MIT -->
# SP11 monitor port: readiness audit and ordered plan to milestone M1

**Hardware status for the port itself: NOT RUN.** This document is a plan and an audit, not a result: no monitor code has run
on a Surface Pro 11. Build results are compile results (PASS or FAIL), never hardware results. Nothing here is a decision of
the project owner; section 15 lists what I need decided.

## 1. Reading this document

Labels (AGENTS.md): **VERIFIED** read in this repository or a reproducible host fact (a build, a test), never "ran on the
Surface"; **OBSERVED** measured on the unit (boots 3 to 7 and the probes) or reported by the operator, one unit, one boot each;
**INFERRED** reasoned, not checked; **EXPERIMENTAL** code exists, unqualified; **UNIMPLEMENTED** no code; **UNKNOWN** not
established, a named experiment would establish it.

Citations: `path:line` is this repository, `q1n1:path:line` the sibling q1n1 checkout (commit d48b0af). A quoted
source fragment is written `fragment`[path:line] and the test `tools/test-sp11-monitor-port.py` finds it on that line.
Boots: 3 = X2 image v2; 4 = payload chain psci, usbsmmu, timer; 5 = psci, usbsmmu; 6 = X2 image v3; 7 = payload cpuon; restart
probe steps 1 to 3 = plain q1n1, cpuon, cpuon; restart control steps 1 and 2 = psci only (2026-11-16); X1 probe page = the
first passive probe run (2026-11-11), a photograph.
Facts about the unit come from the coordinator's fact files (photographs; for boot 6, files read back from the stick), not from
the repository.

## 2. Milestone M1 and the verdict

**M1.** On the Surface boot CPU, one vCPU, started from the USB stick with no host link, the monitor runs at EL2, loads
the stock J700 kernel, the kernel's first instructions execute at EL1 under it, the panel is the only console, the run
ends in a controlled stop, and nothing is written to any disk. Later gates: X4 enumeration, X5 panel console, X6 storage, X7
launchd (`docs/SURFACE-PRO-11.md`, section 6.4).

M1 passes only if every row holds (panel and stick read afterwards).

| # | Acceptance (falsifiable) | Observable |
| --- | --- | --- |
| a | The runner loads monitor and bundle, both hashes agree before and after the bounce | `PAYLOAD READY` and `PAYLOAD VERIFY=OK` (boots 4, 5, 7 show the form) |
| b | The wrapper refuses on any precondition mismatch (MIDR, HCR_EL2 regime, CNTFRQ, GOP geometry, placement inside one conventional descriptor of the final map) | one `REFUSED` line, no guest entry |
| c | The guest ran: the monitor entered it and the stop reports a guest ELR inside the kernel image | the report's `entered` word is 1 (`status, entered = struct.unpack('<2Q'`[probes/run.py:1899]) and ELR is in the image; a first monitor call is stronger |
| d | The run ended by the monitor's own stop (watchdog, stage-2 fault with an IPA, guest exception), never a hang or a reset | the reporter's lines, then the q1n1 heartbeat |
| e | The stop came inside the time box (default 5 s of wall time, counted on `CNTPCT_EL0`) | the report's elapsed counter |
| f | No disk was written: no storage code path in the build, stick read-only, stick files byte-identical after the run | build audit, stick re-read |
| g | The A16 monitor is byte-identical with the new gate off | `tools/monitor_baseline.py` IDENTICAL |

**Verdict.** M1 is further away than the plan's wording suggests in seven respects and nearer in two.

| # | Finding | Label | Where |
| --- | --- | --- | --- |
| F1 | The monitor is a function, not a boot stage (section 3): wrapper, loader, bundle builder and report renderer are all new. | VERIFIED | 3, 4 |
| F2 | The runner never starts q1n1's GIC or tick (`int tick = q1n1_gic_init`[q1n1:platform/uefi/main.c:1336] is the proxy flow; the payload flow calls `sp11_payload_el2_run`[q1n1:platform/uefi/main.c:1641]), yet the monitor's stop is a GIC interrupt (PPI 26) and the unit reads `GICD_CTLR` 0x10 (boot 3): new EL2 GIC stores are needed. | VERIFIED / OBSERVED | 3, 7 |
| F3 | The A16 recipe needs 672 MiB contiguous (384 guest + 48 reserve + 240 work); the largest low extent is 590 MiB (576 MiB aligned; boot 6 map), so the loader falls to the 30 GiB bank, where no monitor has run and EL2's map is untested. | VERIFIED / OBSERVED | 5 |
| F4 | "ECV unusable" concerns `CNTPOFF_EL2`, which the monitor never touches; its real need, `CNTHCTL_EL2` bits 13 and 14, was never tested, and its ECV gate (passes at ECV = 2) cannot tell. | VERIFIED / UNKNOWN | 6 |
| F5 | The C2 vCPU count builds only at 6 (1 and 4 fail six static assertions); 6 vCPUs on the SP11 need `-DSNAP_FAIR_TOKEN=1` or the launch refuses at -146. | VERIFIED | 7, 9 |
| F6 | The NV2 gate refuses every launch until a record is embedded; the local one is from the first X2 image (E2H = 1), the monitor runs E2H = 0. | VERIFIED | 9 |
| F7 | No SP11 thermal guard is linked: "thermal guard on" cannot be met at M1. | VERIFIED | 11 |
| F8 | Nearer, in two respects: NV2 GO at E2H 0, the de-VHE regime, GICD, GICR, PPI 26, PSCI and CNTFRQ match the A16 recipe (boots 3, 6, 7) and `-145` is off the M1 path; the monitor already is a hash-pinned flat PIC binary, so it can be a payload. | OBSERVED / VERIFIED | 3, 4, 7, 9 |

## 3. Entry: the q1n1-to-monitor ABI and the SP11 boot CPU (Q1)

The A16 contract is a C call, not the board-info blob of plan item A4 (no caller, section 9):
`x0 = out buffer, x1 = work area, x2 = work size, x3 = params (physical), x4 = optional host secondary configuration
(zero disables physical launch)`[probes/xnuboot.S:5]. `_start`[probes/xnuboot.S:67] pushes callee-saved registers, saves
VBAR_EL2, HCR_EL2, TPIDR_EL2 and DAIF, masks interrupts, installs its own vectors and, after the guest stops, restores them
and returns (`bl xnuboot_report`[probes/xnuboot.S:226]; restores at `probes/xnuboot/report.c:21-48`).

| Need of `_start` | Evidence | SP11 at a payload call | Label |
| --- | --- | --- | --- |
| EL2, non-VHE host: writes a whole HCR_EL2 (RW, TVM, TRVM, TIDCP, TID3, VM, TWE) and the non-VHE layouts of TCR, CNTHCTL, CPTR | `msr hcr_el2, x8`[probes/xnuboot.S:142]; `compat/el2-entry-model.h:188-196` | EL2; HCR_EL2 0x88000010 (E2H 0, TGE 1), CPTR_EL2 0x22FF (nothing trapped), CNTHCTL_EL2 0x3, DAIF 0x3C0, VBAR_EL2 in q1n1's image (boots 4, 5, 7) | OBSERVED |
| EL2 stage 1 is someone else's: no write of TTBR0, TCR or MAIR_EL2 on the boot CPU; secondaries copy four host-supplied values | `msr ttbr0_el2, x3`[probes/secondary.S:29]; `uint64_t regime[4]`[probes/secondary.h:8] | UEFI's map: SCTLR_EL2 0x100030C5183D (MMU, caches on), TCR 0x80843514, TTBR0 0xA7F2E000, MAIR 0xFFBB4400 (boots 4, 5, 7) | OBSERVED |
| The native PAC helper edits TCR and SCTLR_EL2 and demands E2H 0, MMU on, 4 KiB, T0SZ 17 to 24 | `4K EL2 translation only`[compat/pauth-native.S:30] | TCR 0x80843514 decodes to T0SZ 20, TG0 4 KiB, PS 44 bits: inside the rule (boots 4, 5, 7) | VERIFIED / OBSERVED |
| The map must cover monitor code, out buffer, work arena, guest RAM, VNCR pages (VNCR_EL2 is walked by the EL2 regime, `docs/BOOT-CHAIN.md` section 3) and the panel | quoted rule above | payload RAM and the panel: yes (boot 5); GIC, ECAM, xHC windows read without fault (boots 3, 5, 6); the 30 GiB bank: never touched | OBSERVED / UNKNOWN |
| GIC ready for PPI 26: ICC_SRE_EL2, PMR, IGRPEN1, group 1 + priority + enable of PPI 26, GICD ARE + G1 | `q1n1:platform/uefi/gicv3.c:98-129`; secondary path `probes/secondary.c:75-110` | GICD_CTLR 0x10 (ARE only), 12 of 12 redistributors awake, nothing enabled (T05, boot 3); ICC_SRE_EL2 not read | OBSERVED / UNKNOWN |
| Debug: MDCR.TDE, OS lock cleared, six breakpoints for the native writer (DebugVer 6 to 11) | `((dfr0>>12)&15)<5`[compat/native-writer-debug.h:63] | ID_AA64DFR0_EL1 0x000F01F010305719: DebugVer 9, BRPs 5 (six breakpoints), so that gate should pass (X1 probe page); OSLSR_EL1 and MDCR_EL2 at the call not read | OBSERVED / INFERRED / UNKNOWN |

Does the monitor expect to own the EL2 page tables? **No**: it runs on whatever map q1n1 or UEFI left, a dependency, not a
feature. On the unit that map is UEFI's reinterpretation (slbounce), and nothing yet shows it maps the high bank (U1); if it
does not, the monitor needs its own EL2 map. A return to the runner (`exit:`[probes/xnuboot.S:190]) prints
`PAYLOAD DONE` (boot 5).

**What the SP11 entry stub must do** (new, `SNAP_PLATFORM_SP11` plus a new opt-in gate):
1. Verify and refuse: CurrentEL 2, E2H 0, MIDR in the recorded set, CNTFRQ_EL0 19.2 MHz, MPIDR_EL1 0x80000000, GOP geometry
   and placement against the bundle's pinned record and the final memory map (`a->map_base`).
2. Carve the out buffer, the secondary configuration (zero) and the work arena; copy the bundle's chunks into place; clean
   to the point of coherency and invalidate the instruction cache (the A16 host does
   `proxy.flush_code(xb.IMAGE_PA`[probes/run.py:1812]).
3. Bring the boot CPU's GIC interface and PPI 26 up (F2), or stop by polling (D2).
4. Call `_start`; on return render the report (section 10); never return a half state.

## 4. Delivery without a host link (Q2)

**What `--boot` does on the A16.** `cycle.py launch --boot` runs the pinned launcher as a child
(`tools/cycle_lib/launch.py:528-560`): a live read of the idle q1n1 over the NCM link, then `run_xnuboot` (`probes/run.py:1274`):
ask q1n1 for its memory map, `place()` a region, build and upload about 115 MiB, publish a work locator,
`proxy.call(code, out, work`[probes/run.py:1869], poll for a host stop, read the report back, capture, quiesce and chainload.
None of these host-link steps exists on the SP11, and `cycle.py --target sp11` implements no active flow: a stick boot is
outside its attestation, authorisation and prepared-equals-booted layers (section 12).

**Runner limits** (`q1n1:docs/SP11-PAYLOAD.md`, "Limits" and "Payload ABI v1"; `q1n1:platform/uefi/sp11-payload.h:64-69`):

| Limit | Value | Consequence for the monitor | Label |
| --- | --- | --- | --- |
| Files | at most 5 (8 declared ranges minus image, map, preload), 256 MiB each | monitor plus bundle in 1 to 4 data files; the kernel image (slot of 115.5 MiB: `IMAGE_OFFSET`..`MONITOR_OFFSET`[probes/xnuboot.py:314-315]) fits in one | VERIFIED |
| Payload | `<stem>.BIN`, flat PIC at offset 0, called once in manifest order with `x0` = args on a 64 KiB stack, D/A/I/F masked, EL2 only, boot CPU only; hashed before and after the bounce; data files are not re-hashed at EL2 | the wrapper hashes the bundle itself | VERIFIED / OBSERVED (call: boots 4, 5, 7) |
| Output | `ops.puts`: 40 lines of 100 characters per payload (about 89 rows on this panel); no store op; `load32` is a guarded read | a reporter payload gets its own 40 lines | OBSERVED (boot 5 page) |

**Can the monitor be a payload?** Yes: it is a freestanding blob without absolute pointers
(`'-fno-pic'`[probes/run.py:145]; `absolute pointers must not appear`[compat/nv2-gate.c:6]) linked by `probes/probe.ld`.
My build of the SP11-gated recipe (311,359 bytes, BUILD PASS, section 7) fits the 464 KiB code window (`OUT_AT`[probes/run.py:232]).
It needs a `BTI c; B payload_entry` head, a wrapper as `payload_entry`, the runner's relocation check and a new allow-list
policy for a payload that writes EL2 system registers and RAM; it runs for seconds and replaces VBAR_EL2 and TPIDR_EL2
(restored on return).

**The bundle** (offline, on the Mac, written only under the ignored `build/` and onto the stick; it holds Apple material and is
never committed or quoted; one pinned placement, section 5): `SNAPMON.BIN` (payload 0: head, wrapper, loader, monitor; about
0.4 MiB; VERIFIED by build); `SNAPREP.BIN` (payload 1: read-only report renderer, under 64 KiB; UNIMPLEMENTED); `GUEST.DAT` (a
chunk table and the chunks `xnuboot.build()` makes: rebased kernel, 16 KiB tables, handoff, device tree, SPTM and TXM data,
monitor images; image slot 115.5 MiB + 16 MiB tables + 5 MiB SPTM data; the kernelcache size is UNKNOWN, no copy under `downloads/`);
`PIN.DAT` (pinned facts: descriptor, placement, GOP, GIC, CNTFRQ, MIDRs, ACPI table digests; under 4 KiB; UNIMPLEMENTED).

**What the runner must grow:** nothing for ABI v1 to call the monitor. Optional, each a decision: an RSDP pointer in
`reserved[5]`, a data-file `verify` op, a fixed-address allocation for the big file. The last matters: q1n1 asks for
`AllocateAnyPages` (`Q1N1_PAYLOAD_ALLOCATE_ANY_PAGES`[q1n1:platform/uefi/sp11-payload-load.c:250]), small files landed near
0xCEA89000 (boots 5, 7), and where a 115 MiB block lands (the 590 MiB low extent or the bank) is UNKNOWN (U6): the wrapper
refuses an overlap with its pinned placement.
**What the host tool must do** (a new tool, `sp11_stick_bundle.py` in `tools/`): reuse `probes/xnuboot.py` `build()` and `place()`,
the `probes/monitor_work.py` validators, `def from_hamoa`[probes/bootcontract.py:105] fed a synthetic `info` from the pinned facts,
`tools/check-hamoa-acpi.py`, `board_descriptor.require_boot_ready` (it refuses `config/boards/sp11.json`, a template),
`tools/stage-sp11-boot-media.py` and the autorun plan (`MAX_PAYLOAD_FILES = 5`[tools/sp11_autorun.py:71]); and refuse a bundle whose placement leaves one conventional descriptor,
whose files exceed the caps or whose facts disagree with the dump. Read-and-hash throughput of 115 MiB through the unit's UEFI
USB stack is UNKNOWN (U6).

## 5. Memory (Q3)

Firmware map of the unit (boot 6, 231 descriptors; sums from the local `memmap.txt`):

| Region | Extent | Survives ExitBootServices | Rule for the monitor |
| --- | --- | --- | --- |
| Conventional (7) | 32105 MiB: bank 0x880000000 to 0x1000000000 (30720 MiB), low extents 590, 339, 283, 98, 21, 19, 11 MiB | free | guest and work here only |
| Runtime code and data; ACPI reclaim | 0xD4FBA000 to 0xD5406000; 0xD5466000 to 0xD57BF000 | yes | never touch (the ACPI tables are read later) |
| Reserved | 245 MiB, includes the panel 0xE4800000 to 0xE69C0000 and 0xFF800000 to 4 GiB | yes | never touch; panel mapped RW by design |
| Boot-services data | 153 MiB; one extent 0xCF2D3000 to 0xCF57E000 holds the NVMe's admin queues ASQ 0xCF3E7000, ACQ 0xCF3E8000 (boot 6) | free by UEFI rule, but the controller is enabled and owns them | do not reuse (A16 rule: only type 7 is free: `if kind == 7 and pages`[probes/monitor_work.py:32]) |
| Loader | runner image, map buffer, preload table, payload files, stack | yes | never overwrite |

RAM ends exactly at 2^36 (T09, boot 3; the map above). The monitor's stage 2 is 36-bit (`#define S2_TXSZ`[probes/xnuboot/stage2.c:20],
`STAGE2_IPA_BITS = 36`[probes/xnuboot.py:230], `IPA_LIMIT = 1 << 36`[probes/monitor_work.py:12]); the SP11-only span check
(`ipag_check_spans`[probes/xnuboot/stage2.c:253], -257 to -274) lets a range end exactly there and none start at or beyond it,
so a placement in the bank passes.

Placement A, low: 672 MiB free needed, the largest low extent is 590 MiB (576 aligned), so it fails (a
256 MiB guest needs 544 MiB and fits); the loader prefers it (`start + need <= FOUR_GIB`[probes/run.py:1219]) but cannot, and a
guest smaller than the 384 MiB A16 smoke is untested. Placement B, the bank (0x880000000 +): the loader's `large` branch
(`BOOTSTRAP_SPAN > 1 << 32`[probes/xnuboot.py:481]), host-modelled only (`xb.place(0x8c0000000`[tools/audit-device-exposure.py:55]);
no hardware run of any monitor above 4 GiB, and the EL2 map's coverage is UNKNOWN (U1).

The A16 layout assumes a free span under 4 GiB; a q1n1 heap for code, out buffer, SMP page and work locator
(`CODE_AT`..`WORK_AT`[probes/run.py:231-236]); `reserved(info)` naming q1n1's image, heap, DMA, stage and arena
(`probes/monitor_work.py:15-17`), none of which exist in the runner flow; panel geometry baked into `boot_args.Video` instead of
`a->fb_*`; `monitor_work.WORK_SIZE` fixed at 240 MiB. The SP11 needs one placement pinned at build time and verified at EL2:
inside one conventional descriptor of the final map, outside every declared range and the NVMe's boot-services extent, else
refuse.

## 6. Timer (Q4)

**6.1 What the A16 does.** Host counter 19.2 MHz; the guest is told 24 MHz (`virtual_counter_hz=24000000`[probes/guestprofile.py:323],
and in the watchdog and AOP timebase specs, `aop_timebase_boot=AOPTimebaseBootSpec(`[probes/guestprofile.py:223]).

| Fact | Source | Label |
| --- | --- | --- |
| Boot refuses unless CNTFRQ_EL0 equals the loader's host rate and `ID_AA64MMFR0_EL1.ECV` is non-zero | `!(mmfr0 >> 60)`[probes/xnuboot/prepare.c:91] | VERIFIED |
| Guest counter and virtual timer reads are trapped and converted: Apple aliases (op1 4, CRn 15) by TIDCP, architectural CNTVCT/CNTVCTSS by `CNTHCTL_EL2` bits 14 and 13 (EL1TVCT, EL1TVT: FEAT_ECV level 1); EL1 physical access cleared | `(1UL<<14) \| (1UL<<13)`[probes/xnuboot/prepare.c:452] written at `:453`, no read-back | VERIFIED |
| Any other architectural timer register stops the run | `CLOCK_TIMER_UNSUPPORTED`[probes/xnuboot/trap_dispatch.c:849] | VERIFIED |
| The monitor's own clock is `CNTVCT_EL0` at EL2 (physical minus `CNTVOFF_EL2`, E2H 0; it never programs the offset); `CNTPOFF_EL2` is used nowhere (only the unlinked host model: grep of `compat probes shadow`) | `mrs %0, cntvct_el0`[probes/xnuboot/sysreg_clock.c:21]; `compat/timer-offset-model.h:9-14` | VERIFIED |
| Deadlines and the stop run on the EL2 physical timer `CNTHP_*`, PPI 26 as a literal (`compat/fast-timer-irq.h:56`); IRQs routed to EL2 by `HCR_EL2.IMO` | `pending == 26`[probes/xnuboot/async.c:91]; `orr x8, x8, #(1 << 4)`[probes/xnuboot.S:175] | VERIFIED |

**6.2 What the unit says.** CNTFRQ_EL0 0x124F800 (19.2 MHz, as on the A16), CNTHCTL_EL2 0x3 at handoff (boots 4, 5, 7), CNTVOFF_EL2 0 on
the boot CPU (boot 3, T03) and on all eleven secondaries (boot 7), ECV field 2, MIDR 0x512F0011: all OBSERVED. Linux's "Broken
CNTVOFF_EL2" erratum matches that MIDR, and its KVM workaround traps the guest's counter and virtual timer through ECV (plan
E17): an INFERRED prior for O1, nothing about this unit's EL3; how the register is broken is UNKNOWN. The `CNTPOFF_EL2` read
never returned (boot 4, line count; INFERRED), cause INFERRED `SCR_EL3.ECVEn` clear. **Not measured:** whether bits 13 and 14
can be set at EL2, whether they trap EL1 and EL0 reads, and whether the same EL3 gate controls them: UNKNOWN. The monitor mixes
`CNTVCT_EL0` samples with `CNTPCT_EL0` absolutes (`timer_arm`), sound only while `CNTVOFF_EL2` is small and equal on every core:
the unit shows 0 everywhere, but the monitor records only the boot CPU's value (`mrs %0, cntvoff_el2`[probes/xnuboot/prepare.c:71])
and never tests it.

**6.3 What the A16 gate cannot see.** With bits 13 and 14 inert, boot still passes (ECV = 2). The kernel's time path through
the Apple aliases (TIDCP) and EL0's alias encoding, emulated from the vector trap (`0xd53cfac0U`[compat/clock.c:7]), stays
scaled and needs no ECV; whatever reads the architectural `CNTVCT_EL0` would read an unscaled count (who does: UNKNOWN). The
report counts those reads (`arch_counter_reads`[probes/xnuboot/report.c:283]), so M1 shows a number but cannot show whether
the traps work.

**What the repo says about the guest** (the kernel was not inspected here): XNU reads `'timebase-frequency'`[probes/xnuboot.py:2054]
from the boot cpu node and a root `clock-frequency` (zero panics in `_enable_timebase_event_stream`, `docs/BOOT-CHAIN.md`
section 9). CNTFRQ_EL0 stays the host rate (EL2 cannot trap it), so a scaled guest rate needs a kernel whose audit finds no
CNTFRQ access (`probes/clockcontract.py:3-6`). SPTM and TXM never execute; no time dependence of theirs is documented (UNKNOWN).

| Option | Needs | Risk | Decided by |
| --- | --- | --- | --- |
| O1 A16 recipe: 24 MHz scaled by traps, plus a CNTHCTL read-back refusal and a CNTVOFF = 0 check | bits 13 and 14 effective at EL2 | silent mis-scale if the bits stick but do not trap | T-HW1, T-HW2 |
| O2 19.2 MHz everywhere (device tree, watchdog and AOP specs), no scaling; CNTFRQ_EL0 already reads 19.2 MHz | profile changes only | the repo blames the 24 MHz choice on the watchdog's checked arithmetic (`docs/BOOT-CHAIN.md` section 9, not re-derived; whose, and the effect: UNKNOWN) | an A16 run at 19.2 MHz (its host rate too), operator |
| O3 Patch the guest away from architectural counter reads | a Mac-side kernel scan, then a patch | breaks "stock kernel"; the image is digest pinned (`docs/KERNEL-PATCHES.md`) | only if O1 and O2 fail |
| O4 `CNTPOFF_EL2` / ECV level 2 | an EL3 gate that is open | stalls the board (boot 4); the A16 design does not use it | never |

**6.4 Experiments.** Host first: a counter-visibility matrix (guest access path x trap effective or inert -> what the guest
reads) extending `tools/test-timer-offset-model.py`, and a Mac-side kernelcache scan for architectural counter and timer
encodings beside `probes/clockcontract.py` (NOT RUN: no kernelcache here). Hardware: **T-HW1** a payload that writes
CNTHCTL_EL2 with exactly the monitor's value, reads back, restores, reads back (no CNTPOFF); **T-HW2** an EL1 stub round trip
reading CNTVCT_EL0 and the virtual timer under the traps and returning by HVC (plan M6). T-HW1 failing eliminates O1; T-HW2
passing makes O1 the decision.

## 7. Interrupts and CPUs (Q5)

| Item | Monitor today | Unit | Label |
| --- | --- | --- | --- |
| GICD, redistributors, EL2 timer | SP11 builds must define five macros or `secondary_gic_start` refuses; A16 defaults GICD 0x17000000, first 0x17080000, limit 0x17500000, stride 0x40000 (`probes/secondary.c:17-31`); the boot-CPU path has no GIC address; the EL2 timer INTID is the literal 26 | MADT (boot 6 dump): GICD 0x17000000 v4, GICR 0x17080000 + n x 0x40000 (12 frames, last ends 0x17380000), ITS 0x17040000; T05 read all 12 frames (boot 3); GTDT non-secure EL2 timer GSIV 26 (boot 6 dump) | VERIFIED / OBSERVED |
| PSCI conduit | `smc #0`[probes/secondary.c:52], secondary start only | FADT `arm_boot_arch` 0x1 (SMC); PSCI v1.1 (boots 4, 5); CPU_ON of all 11 cores succeeded from EL2 (boot 7) | OBSERVED |
| Guest interrupts | virtual AIC3 with timer FIQ and IPIs; physical forwarding only for the GPU (`docs/BOOT-CHAIN.md` section 8) | M1 has no physical device interrupt | VERIFIED |
| vCPU count | `#define GUEST_CPU_COUNT 6U`[compat/cpu.h:57] on the A16; SP11 default 6, overridable (`compat/cpu.h:44`) | the J700 tree has six cpu nodes; `cpus=1` trips a PMGR assertion (`docs/BOOT-CHAIN.md` section 7) | VERIFIED |

**Build evidence** (BUILD PASS/FAIL; `config/monitor-flags.json` plus the defines named): `SNAP_PLATFORM_SP11=1` PASS
(311,359 bytes); `+ SNAP_FAIR_TOKEN=1` PASS; `GUEST_CPU_COUNT=1U` and `=4U` **FAIL**, six static assertions:
`probes/secondary.h:27`, `probes/secondary.h:29`, `compat/panic-state.h:18`, `compat/sptm-root.h:10`,
`probes/xnuboot/context.c:696`, `probes/xnuboot/user_views.c:124`. So C2's count is not generalised past the macro: "one vCPU"
means six records, one released. M1 runs vCPU 0 on q1n1's CPU (no `SNAP_BOOT_RELOCATE`) with x4 = 0, no physical secondaries;
XNU's first CPU-start write then falls to stage 2 and stops the run (A16 behaviour, `compat/boot-relocate.h:150-153`), a
controlled stop if the 5 s bound is not reached first; whether XNU gets there first is UNKNOWN.

**Later: secondaries.** `probes/secondary.S` assumes EL2 and a matching MPIDR, then overwrites VBAR_EL2 and HCR_EL2 (`mov x1, #0x80000000`[probes/secondary.S:23]),
installs the host-recorded regime with the MMU off and runs on its own stack. The unit's secondaries start at EL2 with MMU and
caches off (`SCTLR_EL2` 0x30000000) but a non-uniform EL2 state (OBSERVED: boot 7, blurred, and restart probe step 3, a crisp
video): seven cores (0X200, 0X300, 0X10000, 0X10100, 0X10300, 0X20000, 0X20100) HCR_EL2 0x80000000 and VBAR_EL2 0; 0X100 (first
start) and 0X10200 HCR_EL2 0x1488000038 and one high-half VBAR_EL2, equal on both within a boot and different between boots; 0X100's
HCR_EL2 and VBAR_EL2 survived its own CPU_OFF and CPU_ON (step 3); 0X20200 and 0X20300 UNKNOWN (dropped by the line budget).
The entry code fits because it overwrites the state, but the wrapper must write every EL2 register it relies on and must not
assume VBAR_EL2 or HCR_EL2 are reset on a restarted core. Two gaps: MIDR differs by cluster (section 8) and `sp11_launch_gate`
decides from each core's own MIDR against one embedded record; the NV2 functional test ran on the boot CPU only.

## 8. ID registers, PAuth and friends (Q6)

| Item | Monitor today | SP11 fact | Change | Label |
| --- | --- | --- | --- | --- |
| MIDR | guest sees the real value (read once by the kernel) | 0x512F0011 on cluster 0, 0x511F0011 on clusters 1 and 2 (boot 7) | none for M1; per-cluster NV2 record later | OBSERVED |
| PAuth | ID normalisation only: ISAR1 QARMA5 fields mapped to the J700 ISAR2 shape; hardware computes, no software cipher; the loader drops PAC diversity and re-signing is the monitor's job (`probes/idregs.h`, `tools/kc-load.py:126-128`) | QARMA5, APA 5, GPA 1 (X2 T01, boots 3, 6) | none; the algorithm is invisible to the monitor; first guest PAC use is the test (U8) | VERIFIED / OBSERVED |
| TGran16 | not forged (`probes/idregs.h` touches only SVE, SME, ZFR0, SMFR0, PAuth); the guest's 16 KiB granule is shadowed into 4 KiB tables by `shadow/mmu.c` and `shadow/shadow.c` | TGran16 absent, as on the A16 (X2 T01, boots 3, 6) | none: the mechanism is CPU independent | VERIFIED / OBSERVED |
| ECV | see section 6 | level 2; EL1TVT and EL1TVCT untested | O1 guard | UNKNOWN |
| FGT, AMU | FGT only under `SNAP_LEAN_TRAPS == 2`[probes/xnuboot/prepare.c:491] (default 0); AMU untouched | FGT 1 (MMFR0), AMU 2 (PFR0) (X1 probe page) | none for M1 | VERIFIED / OBSERVED |
| RNG | `pauth_domain_prepare` refuses (-24) without ISAR0.RNDR or PAuth, or when 64 reads in a row fail (`mrs %0,s3_3_c2_c4_0`[probes/xnuboot/prepare.c:33]) | ID_AA64ISAR0_EL1 0x1221111110212120: RNDR 1; ISAR1 APA 5; ISAR2 not read (X1 probe page) | none; the ID gates should pass, the RNDR reads are untried | OBSERVED / INFERRED / UNKNOWN |

## 9. Platform contract and the gates (Q7)

| Where it refuses | Status | On the M1 path? | Becomes, for M1 | Label |
| --- | --- | --- | --- | --- |
| NV2 launch gate: no embedded record, MIDR/REVIDR/MMFR2/MMFR4 mismatch, PAuth shape (-90 to -95) | `snap_nv2_gate_decide_embedded`[probes/xnuboot/prepare.c:356]; record header via `SNAP_NV2_RECORD_HEADER`[compat/nv2-gate.c:10] | **yes, refuses every launch today** | embed a record; the local one (`build/nv2-record`) is from the first X2 image (E2H = 1); the regime is a decision | VERIFIED |
| vCPU count needs the fair lock (-146), checked first | `return GUEST_CPU_STATUS_FAIR_LOCK;`[probes/xnuboot/prepare.c:101] | yes | add `-DSNAP_FAIR_TOKEN=1` (opt-in, `tools/cycle.py:404`) | VERIFIED |
| SSD adoption (-145) | `if (!ans_window(c)) return 0;`[probes/xnuboot/ans_glue.c:104] comes before the SP11 refusal at `probes/xnuboot/ans_glue.c:113` | **no**: the smoke profile has no ANS window | stays a refusal for X6 | VERIFIED |
| DCZID != 4 (-26), PAN absent (-23), native-writer debug (-35 to -38), RNDR (-24) | `if(dczid!=4)return -26;`[probes/xnuboot/prepare.c:550] on every CPU; `pauth_domain_prepare(c))return -24;`[probes/xnuboot/prepare.c:82]; sections 3 and 8 | yes | DCZID 4 and DC ZVA passed (boot 3, T04); the DFR0 and -24 ID gates should pass (INFERRED, sections 3 and 8); other cores' DCZID, PAN (MMFR1 row unverified), breakpoint and MDSCR readbacks, OS lock: UNKNOWN | OBSERVED / INFERRED / UNKNOWN |

Models with no call site: `compat/topology.c` (only its fair-lock rule is enforced, as -146), `compat/board-info.[ch]`
(`#include "../compat/board-info.h"`[probes/xnuboot.c:61], no caller) and `compat/hamoa-thermal.c` (linked, never called). For M1
`PIN.DAT` does the board-info job, topology stays a model (one vCPU released), thermal is D8.
**No root device:** the A16 smoke recipe demands a profile with no Adreno (`xb.GUEST_PROFILE.adreno is None`[tools/cycle_lib/bootstrap_smoke.py:101])
and no ANS boot, and sets `SNAP_NO_FB=1`, `SNAP_WATCHDOG=5`, `SNAP_GUEST_MIB=384` and the `SNAP_SPTM_HVC_CALLS` kernel patch
(`ENV = {'SNAP_NO_FB': '1'`[tools/cycle_lib/bootstrap_smoke.py:24]). Its first milestone is the first monitor call
(`Reaching that is the milestone`[probes/xnuboot.c:22]); the first unmodelled device ends the run with a stage-2 fault carrying
its IPA (which one, and when: UNKNOWN until the run). **Console:** keep `SNAP_NO_FB=1` (no guest framebuffer to scribble on
q1n1's page); XNU's `serial=3` output reaches SERIAL_PUTC and the console ring (section 10); guest glyphs are X5.
**Native calls or the HVC patch:** the stock path takes the first call through a stage-2 execute-never fault
(`HPFAR_EL2` reads zero for it on the A16, `docs/BOOT-CHAIN.md` section 10; Oryon v1 UNKNOWN, U10); the HVC patch
(`docs/KERNEL-PATCHES.md`) avoids the fault but changes the kernel: D9.

## 10. Console, diagnostics and the restart (Q8)

A16: the proxy reads the report. SP11: the runner's `puts` (40 lines per payload), q1n1's page and photographs; film the page: the board may restart. A monitor fault after the vectors are installed lands in
`bad:`[probes/xnuboot.S:410], which stores ESR, ELR and FAR and takes `exit`, so the reporter can print it. A fault before that,
or in the wrapper, lands in q1n1's fault page; a hang prints nothing after its last line. A guest panic reaches the SPTM panic
page (`SPTM_PANIC_PAGE_SIZE`[probes/xnuboot/prepare.c:104]) and the monitor's 1 KiB console ring
(`CONSOLE_MAX = 1024`[probes/run.py:199]). Whether the RAM report survives a restart is UNKNOWN (M10 NOT RUN).

**The restart.** OBSERVED, without times (operator reports): the board restarted by itself after boot 7 and after restart probe
step 2, both cpuon runs, and not in step 3 (cpuon; video through UPTIME 36 S, later UNKNOWN): 2 of 3 cpuon boots. UPTIME starts
before the runner, so a chain is inside it: boot 7's UPTIME 16 S was the first heartbeat after the chain, and the restart came
"right after the last photo", INFERRED at UPTIME about 17 to 25 S. Step 2 has no photograph after the chain, and its 90 s DONE2
to DONE3 gap holds the operator's power cycle (45 s once) and the time to notice: INFERRED, weakly, UPTIME about 41 S or
earlier, possibly inside the chain. So, INFERRED, not at one fixed UPTIME. A plain q1n1 page (step 1) was alive at UPTIME 123 S.
**The control** (restart control, 2026-11-16): the same runner and slbounce, a manifest of the read-only `PSCI.BIN` alone (no
CPU_ON, no CPU_OFF), was photographed alive at UPTIME 224 S and 268 S in two boots, one crisp photograph each (OBSERVED; the state
afterwards is UNKNOWN; the videos cover the first seconds only). So the runner flow with a read-only payload is not what restarts
the board (INFERRED, strongly: had it restarted 2 of 3 boots, both controls surviving had a chance of about 11 %; evidence, not
proof); the restart goes with the cpuon payload (CPU_ON and CPU_OFF of the secondaries; INFERRED, correlation over five
boots). Cause UNKNOWN (candidates, untested: an APSS hardware watchdog, `docs/SURFACE-PRO-11.md` E24 and X2 item 9; a reaction to
the core power cycles); times UNKNOWN. After a self-restart the firmware follows its normal boot order, not the stick (operator
report after boot 4): every stick boot is started by the operator (power off, Volume-Down + Power), a restart ends the
experiment, and no plan runs unattended. M1 starts no secondary (x4 = 0) and the read-only runner flow is no longer a suspect, so
the restart is probably irrelevant to M1 (not excluded: the control ran none of the monitor's EL2 register or GIC stores, and no
115 MiB load). **Budget (D5), a precaution of low weight, kept as the default:** bounce to end of report well under 20 s of
UPTIME (the earliest plausible restart, in a cpuon run, is about UPTIME 17 S); the stop is the monitor's, never a wait.
**Timing without UPTIME on screen:** a video's `creationdate` and the stick's DONE marker times are both local time and agree
within the FAT's 2 s resolution, so a video kept running until the screen goes dark dates a restart.

## 11. Thermal and power (Q9)

`compat/a16-thermal.c` reads A16 physical addresses (`static uint64_t tm(unsigned b)`[compat/a16-thermal.c:7]); its only
platform test (preflight -28) is `physical_hz!=19200000`[probes/xnuboot/prepare.c:93], which the SP11 also satisfies. It runs
only when the host sets the opt-in `EARLY_STOP_F_THERMAL`[compat/early-stop.h:28]; `compat/hamoa-thermal.c` is a read-only
model, linked and never called, which refuses every admission until X2 measures the platform. At EL2 an unclocked read can
hang the board (boot 3: the segment 3 ECAM read never returned and the board reset itself, operator report). For M1: leave the
A16 flag unset; "thermal guard on" (`docs/SURFACE-PRO-11.md`, section 8, "Always") cannot be met; the first run needs the
operator's power attestation (`tools/cycle_lib/targets.py` rule: AC or at least 50% battery, at most 30 minutes old, bound to
the run) and a decision (D8). Never bypassed: AC or 50% battery; no CPU or GPU clock or voltage change; a time-boxed run; no
TSENS read at any unmeasured address.

## 12. Safety and authorisation (Q10)

| M1 step | Hardware operation | AGENTS.md section 5 clause touched | Status |
| --- | --- | --- | --- |
| Stick boot, Shell, slbounce, EL2 takeover; the runner's read-only FAT access | firmware boot path as boots 4 to 7 | Secure Boot setting (owner's way A or B, `docs/SURFACE-PRO-11.md` section 8.1) | existing |
| Wrapper stores to RAM, system registers and the GIC | new EL2 code | the runner's rule: a payload storing to device registers needs a named, SHA-pinned authorisation (`q1n1:docs/SP11-PAYLOAD.md`, "What the next payloads need") | NEW: needs authorisation (D4, D7) |
| `_start` writes HCR, VBAR, TPIDR, VTCR, VTTBR, CNTHCTL, MDCR, OS lock | volatile CPU state, restored on return | recoverability: a documented stop and recovery path | NEW |
| Guest runs at EL1 | arbitrary Apple code; MMIO limited by stage 2 to RAM (and no panel with `SNAP_NO_FB=1`) | partition and firmware writes: none reachable | NEW |
| Disk, NVMe, USB, clock, voltage, DVFS | none: stage 2 omits them, no code path; the guest's `hvc` goes to firmware only for the GPU profile (`probes/xnuboot/trap_dispatch.c:702`) and `smc` is issued only by PSCI start (`probes/secondary.c:52`) and the GPU | A1 and A2 NOT granted, not touched; no unsafe clock or voltage operation | NOT RUN |
| Attestation, prepared-equals-booted, one client | `cycle.py` ends every SP11 mode but the passive checks in `'unimplemented-flow'`[tools/cycle_lib/targets.py:600], after its gates, so a stick boot is outside all of its layers | AGENTS.md section 5: AC or 50% battery, no change between prepare and boot | the bundle builder must carry them (manifest digests, an attestation file) |

## 13. Ordered work plan to M1 (Q11)

Unknowns referenced: U1 EL2 map coverage; U2 EL1TVT/EL1TVCT effect; U3 PPI 26 delivery; U4 restart; U5 OS lock, MDCR_EL2,
ICC_SRE_EL2, DFR1, ISAR2; U6 bundle throughput and allocation; U7 guest in the bank; U8 first PAC use under QARMA5; U9 kernelcache
size and counter scan (Mac); U10 stage-2 fault syndromes on Oryon v1.

| WI | Work | Depends | Acceptance (can fail) | Host-first evidence | HW boot | Parallel | UNKNOWN / decision |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | **Freeze the interfaces**: placement rule, chunk container, `PIN.DAT` fields, report buffer, gate name (`SNAP_SP11_STICK`) | none | an interface note and a schema test, shared by builder and wrapper, that rejects a seeded bad record | schema test, seeded bad records | no | no | none |
| 2 | **EL2 probe**, a new read-only payload `el2mem`: guarded `load32` at the first and last word of each candidate region, the bank and a low control; passive reads of OSLSR_EL1, MDCR_EL2, ICC_SRE_EL2, ID_AA64DFR1_EL1, ID_AA64ISAR2_EL1 | none | every load answers, or the aborting address is printed (error 6); the five registers are printed | scripted-load tests like those of the `usbsmmu` payload; disassembly allow-list: loads and MRS only | 1 | yes | resolves U1, U5; feeds D1 |
| 3 | **Bundle builder**, a new host tool: pinned placement, chunk container, `PIN.DAT`, manifest, autorun plan | 1, 2, D1 | deterministic output; refuses facts that disagree with the dump, a placement outside one conventional descriptor, over-cap files and a descriptor not boot-ready; `python3 probes/xnuboot.py --check` passes offline at the pinned base | seeded-bad fixtures; `tools/test-board-descriptor.py`, `tools/test-hamoa-bootcontract.py` | no | yes | U9 (Mac's kernelcache) |
| 4 | **Wrapper, loader and reporter** under `SNAP_SP11_STICK`: precondition refusals, chunk copy with cache maintenance, GIC bring-up, the `_start` call, rendering | 1 | a host harness with a fake runner refuses every damaged bundle (outside the region, overlap, bad hash, truncated chunk, wrong MIDR); the A16 build stays IDENTICAL | native tests under ASan and UBSan, as for the runner | no | yes | D2, D4 |
| 5 | **M1 monitor profile**: SP11 with `-DSNAP_FAIR_TOKEN=1`, the NV2 record, the five GIC macros, CNTHCTL read-back and CNTVOFF checks, breakpoints on, no thermal flag | 1 | BUILD PASS; `tools/test-monitor-hardening.py`, `tools/test-sp11-integration.py`, `tools/test-nv2-gate.py` pass; `tools/monitor_baseline.py` IDENTICAL for the A16 | existing suites | no | yes | D6, D9, D10 |
| 6 | **Authorisation package**: every payload named by SHA-256, each store class listed with its register, window and evidence | 4, 5 | a recorded operator decision naming the digests | review against the runner's rule | no | no | D4, D7 |
| 7 | **Bundle dry run**: a real-size data file, the wrapper stops before `_start` | 3, 4, 6 | all hashes agree before and after the bounce; copy and clean finish; timings and the firmware's placement of the big file are printed | host dry run of the wrapper | 1 | no | resolves U6 |
| 8 | **GIC and stop dry run**: the wrapper arms the stop timer, `_start` replaced by an immediate return | 7 | PPI 26 arrives, the stop is on time, EL2 state restored, the page alive afterwards | GIC register-sequence model, as `compat/el2-entry-model.c` does for entry | 1 | no | resolves U3; D2 |
| 9 | **Timer probes** T-HW1, then T-HW2 (section 6.4) | none | T-HW1: the written bits read back and the old value is restored; T-HW2: EL1 reads arrive as traps with the scaled value | counter-visibility matrix; a model of the EL1 stub | 1 or 2 | yes | resolves U2; D3 |
| 10 | **Restart survey**, before SMP work only (M1 does not need it); operator in the loop, one boot per sample. DONE: the psci-only control, alive at UPTIME 224 S and 268 S (section 10). Left: (a) film a cpuon page until it restarts (last frame, video `creationdate` and DONE markers date it); (b) a cpuon variant that starts and stops ONE core only; (c) a variant that leaves cores running; APSS watchdog registers read-only (X2 item 9) separately | none | cpuon restarted in 2 of 3 boots, times unknown: (a) dates a restart or shows none up to a stated UPTIME; (b), (c) show whether one core, or cores left running, change it | payload-plan tests (`tools/test-sp11-autorun-payload.py`) | 1 or more each | yes | narrows U4 |
| 11 | **M1 run** | 5, 8, 9 | the seven rows of section 2 | all of the above | 1 to 3 | no | resolves U7, U8, U10; D5, D8 |

**Critical path:** 1 -> 4 -> 6 -> 7 -> 8 -> 11; WI 2 and 3 join at 7, WI 5 at 6, WI 9 at 11; WI 10 is off the path. WI 2, 9 and 10 are
separate payloads or plans, and WI 3, 4 and 5 touch disjoint files: independent agents can take them at once. Decisions gate
WI 3 (D1), 4 (D2, D4), 5 (D6, D9, D10), 6 (D7), 9 (D3) and 11 (D5, D8). A row that depends on an UNKNOWN does not close before
its experiment has run.

## 14. The first hardware run (Q12)

If it works the panel shows, in order: the runner's `LOADED` and `READY` lines; the wrapper's check lines (row b of section 2)
all OK; `COPIED`, `CLEAN`, `GIC READY`, `ENTER`; then the reporter: `ENTERED=1`, the terminal reason (watchdog,
stage-2 fault with IPA, or guest exception), ELR and ESR, the first monitor calls with their `x16` values, the guest console
tail, the count of architectural counter reads and the elapsed ticks; then the heartbeat. (A design, not an output.) The time
budget is a target, UNKNOWN until WI 7: bundle hash and copy at most 4 s, guest at most 5 s, report at most 2 s, all well under
the 20 s of UPTIME of section 10.

**Stop rules.** (1) A wrapper `REFUSED` line ends the boot; fix the cause, never retry the same bundle. (2) No page change for
twice the time box after `ENTER`: note the last line and power cycle; a repeat follows rule 1.
(3) Two resets at the same last line stop the series. (4) The time box and the 20 s UPTIME budget are defaults, the budget a precaution of low weight (section 10); only the operator relaxes them (D5).
(5) A touch outside RAM is a finding, not a retry. (6) One hardware client, AC power or at least 50% battery, the operator present
and filming; a spinning guest that no timer stops is ended by the operator's power cycle, which writes nothing.

**Expected first failures, most likely first.**
1. NV2 gate refusal (-90 to -95): certain until WI 5 embeds a record.
2. The stop timer never fires or the wrong interrupt arrives (GIC state, U3): the guest runs unbounded and the operator ends it.
3. A data abort at EL2 in the monitor on the bank (U1): the `bad:` report, FAR in the bank.
4. The first monitor call never arrives as expected on native calls (stage-2 execute-never fault, `HPFAR_EL2`, U10).
5. Debug (U5): the DFR0 gate should pass (INFERRED), but a breakpoint write or readback (-36), MDSCR (-37) or the OS lock can
   fail; -24 only if 64 RNDR reads fail; inert traps (U2: no refusal, only T-HW2 shows it); FPAC or PAC failure at the first
   signed return (U8).
6. XNU stops at its first unmodelled device with a stage-2 fault and an IPA: that is success for M1.
7. A restart with no report (U4; M1 starts no secondary, so unlikely): the page is gone; the stop rules apply.

**Kill criteria (plan section 3.3).** K1: the NV2 redirect or its negative control failing in the monitor's context stops the
port (the plan's D4). K2: unchanged (boots 4 to 7). K3: panel plus photographs is the log strategy; two
resets without a readable report strain it. K4 to K7: not exercised. Proposed: an EL2 map that cannot reach the
placement, and inert ECV traps with O2 and O3 both unacceptable; each needs a design study first.

## 15. Decisions I need from the operator

- D1 Placement: a low region with a smaller guest (256 MiB fits) or the bank at 0x880000000; after WI 2.
- D2 Stop mechanism: GIC-timer stop (needs GIC stores) or a polling stop in the wrapper (it cannot bound a spinning guest).
- D3 Timer: run T-HW1 and T-HW2 before M1, and whether to run the optional A16 experiment at 19.2 MHz (O2).
- D4 Whether the wrapper, a payload, may store to GIC registers, and which stores are authorised.
- D5 The M1 time box: 5 s proposed; bounce to end of report well under 20 s of UPTIME as a precaution of low weight (earliest plausible restart, in a cpuon run, about UPTIME 17 S): keep or relax.
- D6 NV2 record: accept the first-image record (E2H = 1) or require one from the E2H = 0 regime.
- D7 Authorisation of the SHA-named monitor payload (section 12).
- D8 Thermal: accept an attested, time-boxed, one-CPU run as an exception to "thermal guard on", or wire the Hamoa guard first.
- D9 `SNAP_NO_FB=1` with native calls only, or also the A16 smoke's `SNAP_SPTM_HVC_CALLS` kernel patch.
- D10 `-DSNAP_FAIR_TOKEN=1` as an SP11-only opt-in.

## 16. What I could not establish

- The J700 kernelcache size and anything about its code (no copy under `downloads/`; the scans are NOT RUN).
- Whether the EL2 map reaches the bank; whether CNTHCTL_EL2 bits 13 and 14 work; whether `SCR_EL3.ECVEn` gates them.
- The OS lock (OSLSR_EL1), MDCR_EL2 and ICC_SRE_EL2 at the payload call; ID_AA64DFR1 and ID_AA64ISAR2 (not read); whether the
  debug and RNDR instructions behave as the ID registers say; the EL2 entry state of cores 0X20200 and 0X20300.
- Whether XNU, SPTM or TXM care about the 19.2 versus 24 MHz choice beyond the repo's watchdog note.
- The restart: its cause and UPTIME (boot 7 INFERRED 17 to 25 S; step 2 INFERRED 41 S or earlier, weak; step 3 unobserved past
  36 S); whether one core, or cores left running, is enough (WI 10); whether the read-only runner flow restarts after
  UPTIME 268 S (state after the photographs unknown); the APSS watchdog state; whether the report buffer survives a reset.
- Throughput of a 115 MiB read, where `AllocateAnyPages` puts it, and the sweep's behaviour at that size.
- How the guest behaves above 4 GiB on hardware; first PAC use under QARMA5; stage-2 fault syndromes on this core.
- A16 hardware qualification of `SNAP_FAIR_TOKEN` in this tree (its documents are not here).
