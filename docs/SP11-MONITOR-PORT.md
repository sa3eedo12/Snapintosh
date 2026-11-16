<!-- SPDX-License-Identifier: MIT -->
# SP11 monitor port: readiness audit and ordered plan to milestone M1

**Hardware status for the port itself: NOT RUN.** This document is a plan and an audit, not a result: no monitor code has run
on a Surface Pro 11. Build results are compile results (PASS or FAIL), never hardware results. Nothing here is a decision of
the project owner; section 15 lists what I need decided.

## 1. Reading this document

Labels (AGENTS.md): **VERIFIED** read in this repository or a reproducible host fact (a build, a test), never "ran on the
Surface"; **OBSERVED** measured on the unit or reported by the operator, one unit, one boot each; **INFERRED** reasoned, not
checked; **EXPERIMENTAL** code exists, unqualified; **UNIMPLEMENTED** no code; **UNKNOWN** not established, a named experiment
would establish it.

Citations: `path:line` is this repository, `q1n1:path:line` the sibling q1n1 checkout (1e7318f; its platform/ equals d48b0af's);
`fragment`[path:line] is a quoted fragment that `tools/test-sp11-monitor-port.py` finds on that line. Boots: 3 = X2 image v2;
4 = payload chain psci, usbsmmu, timer; 5 = psci, usbsmmu; 6 = X2 image v3; 7 = payload cpuon; restart probe steps 1 to 3 =
plain q1n1, cpuon, cpuon; restart control steps 1 and 2 = psci only (2026-11-16); X1 probe page = the first passive probe run
(2026-11-11), a photograph. Unit facts come from the coordinator's fact files (photographs; for boot 6, files read
back from the stick), not from this repository.

## 2. Milestone M1 and the verdict

**M1.** On the Surface boot CPU, one vCPU, started from the USB stick with no host link, the monitor runs at EL2, loads
the stock J700 kernel, the kernel's first instructions execute at EL1 under it, the panel is the only console, the run
ends in a controlled stop, and nothing is written to any disk. Later gates: X4 enumeration, X5 panel console, X6 storage, X7
launchd (`docs/SURFACE-PRO-11.md`, section 6.4).

M1 passes only if every row holds.

| # | Acceptance (falsifiable) | Observable |
| --- | --- | --- |
| a | The runner loads monitor and bundle, both hashes agree before and after the bounce | `PAYLOAD READY` and `PAYLOAD VERIFY=OK` (boots 4, 5, 7 show the form) |
| b | The wrapper refuses on any precondition mismatch (MIDR, regime, CNTFRQ, GOP, placement in one conventional descriptor of the final map) | one `REFUSED` line, no guest entry |
| c | The guest ran: the monitor entered it and the stop reports a guest ELR inside the kernel image | the report's `entered` word is 1 (`status, entered = struct.unpack('<2Q'`[probes/run.py:1907]) and ELR is in the image; a first monitor call is stronger |
| d | The run ended by the monitor's own stop (watchdog, stage-2 fault with an IPA, guest exception), never a hang or a reset | SNAPREP's lines, then the heartbeat |
| e | The stop came inside the time box (default 5 s of wall time, counted on `CNTPCT_EL0`) | the report's elapsed counter |
| f | No disk was written: no storage code path, stick files byte-identical after the run | build audit, stick re-read |
| g | The A16 monitor is byte-identical with the new gate off | `tools/monitor_baseline.py` IDENTICAL |

**Verdict.** M1 is further away than the plan's wording suggests in five respects and nearer in two.

| # | Finding | Label |
| --- | --- | --- |
| F1 | The monitor is a function, not a boot stage (section 3): wrapper, loader and reporter are new; the bundle builder exists since (section 4). | VERIFIED |
| F2 | The runner never starts q1n1's GIC or tick (`q1n1_gic_init`[q1n1:platform/uefi/main.c:1336] is the proxy flow; the payload flow calls `sp11_payload_el2_run`[q1n1:platform/uefi/main.c:1641]), yet the monitor's stop is a GIC interrupt (PPI 26) and `GICD_CTLR` reads 0x10 (boot 3): new EL2 GIC stores are needed. | VERIFIED / OBSERVED |
| F3 | The A16 recipe needs 672 MiB contiguous (384 guest + 48 reserve + 240 work), the largest low extent is 590 MiB (576 MiB aligned; boot 6 map): the loader falls to the untested 30 GiB bank unless the guest is 288 MiB or less (section 5). | VERIFIED / OBSERVED |
| F4 | `CNTHCTL_EL2` bits 13 and 14, the monitor's real timer need ("ECV unusable" concerns `CNTPOFF_EL2`, which it never touches), were never tested; the SP11 read-back (-147) catches only "does not stick" (sections 6, 9). | VERIFIED / UNKNOWN |
| F5 | The launch is gated: the NV2 gate refuses until a record is embedded (D6), six vCPUs refuse at -146 without `-DSNAP_FAIR_TOKEN=1` (D10; the vCPU count builds only at 6) and no SP11 thermal guard is linked (D8); the M1 profile requires the record and sets the lock. | VERIFIED |
| F6 | Nearer, in two respects: NV2 GO at E2H 0, the de-VHE regime, GICD, GICR, PPI 26, PSCI and CNTFRQ match the A16 recipe (boots 3, 6, 7) and `-145` is off the M1 path; the monitor already is a hash-pinned flat PIC binary, so it can be a payload. | OBSERVED / VERIFIED |

## 3. Entry: the q1n1-to-monitor ABI and the SP11 boot CPU (Q1)

The A16 contract is a C call, not the board-info blob of plan item A4 (`compat/board-info.[ch]`, included at `probes/xnuboot.c:61`,
has no caller; `PIN.DAT` does its job): `x0 = out buffer, x1 = work area, x2 = work size,
x3 = params (physical), x4 = optional host secondary configuration (zero disables physical launch)`[probes/xnuboot.S:5].
`_start` (`probes/xnuboot.S:67`) pushes callee-saved registers, saves VBAR_EL2, HCR_EL2, TPIDR_EL2 and DAIF, masks interrupts,
installs its own vectors and, after the guest stops, restores them and returns (`probes/xnuboot.S:226`, `probes/xnuboot/report.c:21-48`).

| Need of `_start` | Evidence | SP11 at a payload call | Label |
| --- | --- | --- | --- |
| EL2, non-VHE host: writes a whole HCR_EL2 (RW, TVM, TRVM, TIDCP, TID3, VM, TWE) and the non-VHE layouts of TCR, CNTHCTL, CPTR | `probes/xnuboot.S:142`; `compat/el2-entry-model.h:188-196` | EL2; HCR_EL2 0x88000010 (E2H 0, TGE 1), CPTR_EL2 0x22FF (nothing trapped), CNTHCTL_EL2 0x3, DAIF 0x3C0, VBAR_EL2 in q1n1's image (boots 4, 5, 7) | OBSERVED |
| EL2 stage 1 is someone else's: no write of TTBR0, TCR or MAIR_EL2 on the boot CPU; secondaries copy four host-supplied values | `probes/secondary.S:29`; `probes/secondary.h:8` | UEFI's map: SCTLR_EL2 0x100030C5183D (MMU, caches on), TCR 0x80843514, TTBR0 0xA7F2E000, MAIR 0xFFBB4400 (boots 4, 5, 7) | OBSERVED |
| The native PAC helper edits TCR and SCTLR_EL2 and demands E2H 0, MMU on, 4 KiB, T0SZ 17 to 24 | `4K EL2 translation only`[compat/pauth-native.S:30] | TCR 0x80843514 decodes to T0SZ 20, TG0 4 KiB, PS 44 bits: inside the rule (boots 4, 5, 7) | VERIFIED / OBSERVED |
| GIC ready for PPI 26: ICC_SRE_EL2, PMR, IGRPEN1, group 1 + priority + enable of PPI 26, GICD ARE + G1 | `q1n1:platform/uefi/gicv3.c:98-129`; secondary path `probes/secondary.c:75-110` | GICD_CTLR 0x10 (ARE only), 12 of 12 redistributors awake, nothing enabled (T05, boot 3); ICC_SRE_EL2 not read | OBSERVED / UNKNOWN |
| Debug: MDCR.TDE, OS lock cleared, six breakpoints for the native writer (DebugVer 6 to 11) | `((dfr0>>12)&15)<5`[compat/native-writer-debug.h:63] | ID_AA64DFR0_EL1 0x000F01F010305719: DebugVer 9, BRPs 5 (six breakpoints), so that gate should pass (X1 probe page); OSLSR_EL1 and MDCR_EL2 at the call not read | OBSERVED / INFERRED / UNKNOWN |

Does the monitor expect to own the EL2 page tables? **No**: it runs on whatever map q1n1 or UEFI left (on the unit, UEFI's), a
dependency, not a feature. The map must cover monitor code, out buffer, work arena, guest RAM, VNCR pages
(`docs/BOOT-CHAIN.md` section 3) and the panel: payload RAM and the panel are mapped (boot 5), the GIC, ECAM and xHC windows read
without fault (boots 3, 5, 6), and nothing yet shows it maps the high bank (U1; `el2mem`, WI 2): if not, the monitor needs its own map.

**What the SP11 entry stub (the wrapper, WI 4) must do**, under `SNAP_PLATFORM_SP11` and the opt-in `SNAP_SP11_STICK` (refusal
table and check order: `docs/SP11-STICK-BUNDLE.md` section 8): (1) verify and refuse: CurrentEL 2, E2H 0, MIDR, CNTFRQ_EL0,
MPIDR_EL1, GOP and placement against `PIN.DAT` and the final memory map (`a->map_base`); (2) copy the chunks into the placement and
clean to the point of coherency (the A16 host does `proxy.flush_code(xb.IMAGE_PA`[probes/run.py:1820]); (3) bring the boot CPU's
GIC interface and PPI 26 up (F2), or stop by polling (D2); (4) call `_start` and on return write the report head into `REPORT.DAT`
(section 10), never returning a half state.

## 4. Delivery without a host link (Q2)

**What `--boot` does on the A16.** `cycle.py launch --boot` runs the pinned launcher as a child (`tools/cycle_lib/launch.py:528-560`):
a live read of the idle q1n1 over the NCM link, then `run_xnuboot` (`probes/run.py:1282`): ask q1n1 for its memory map, `place()` a
region, build and upload about 115 MiB, publish a work locator, `proxy.call(code, out, work`[probes/run.py:1877], poll for a host
stop, read the report back, capture, quiesce and chainload. The SP11 has none of these host-link steps (`cycle.py --target sp11`
implements no active flow): a stick boot is outside its attestation, authorisation and prepared-equals-booted layers (section 12).

**Runner limits** (`q1n1:docs/SP11-PAYLOAD.md`, "Limits" and "Payload ABI v1"; `q1n1:platform/uefi/sp11-payload.h:64-69`):

| Limit | Value | Consequence for the monitor | Label |
| --- | --- | --- | --- |
| Files | at most 5, 256 MiB each | the bundle takes all five, none spare; the kernel image (slot of 115.5 MiB: `IMAGE_OFFSET`..`MONITOR_OFFSET`[probes/xnuboot.py:314-315]) fits in one | VERIFIED |
| Payload | `<stem>.BIN`, flat PIC at offset 0, called once in manifest order with `x0` = args on a 64 KiB stack, D/A/I/F masked, EL2 only, boot CPU only; hashed before and after the bounce | the wrapper hashes the bundle itself | VERIFIED / OBSERVED (call: boots 4, 5, 7) |
| Output | `ops.puts`: 40 lines of 100 characters per payload; no store op; `load32` is a guarded read | a reporter payload gets its own 40 lines | OBSERVED (boot 5 page) |

**Can the monitor be a payload?** Yes: a freestanding blob without absolute pointers (`absolute pointers must not appear`[compat/nv2-gate.c:6]),
314,871 bytes as the M1 profile (BUILD PASS), inside the 464 KiB code
window (`OUT_AT`[probes/run.py:232]). It needs a `BTI c; B payload_entry` head, a wrapper, the runner's relocation check and an
allow-list policy for a payload that writes EL2 system registers and RAM; it replaces VBAR_EL2 and TPIDR_EL2 for seconds.

**The bundle** is five files, the runner's whole manifest, in this order (`docs/SP11-STICK-BUNDLE.md` section 2, the frozen
interface, EXPERIMENTAL; built offline on the Mac under the ignored `build/` and onto the stick; it holds Apple material and is never
committed or quoted): `SNAPMON.BIN` (payload 0: head, wrapper, loader, the 314,871-byte monitor; the wrapper is WI 4); `SNAPREP.BIN`
(payload 1: the reporter, WI 4); `GUEST.DAT` (a chunk container of what `xnuboot.build()` makes, 144,665,408 bytes at the builder's
synthetic sizes, at most 256 MiB; the kernelcache size is UNKNOWN, no copy under `downloads/`); `PIN.DAT` (1024 bytes of pinned
facts); `REPORT.DAT` (528,384 zero bytes: the runner's allocation, found by name in the preload table, becomes the report buffer).
The runner hashes data files only before the bounce, so the wrapper hashes what it uses.

**What the runner must grow:** nothing for ABI v1 to call the monitor; no sixth file fits. A fixed-address allocation for the big
file would matter: q1n1 asks for `AllocateAnyPages` (`Q1N1_PAYLOAD_ALLOCATE_ANY_PAGES`[q1n1:platform/uefi/sp11-payload-load.c:250]),
and where a 138 MiB block lands (the 590 MiB low extent or the bank) and how fast it hashes at EL2 are UNKNOWN (U6; `hashbig`,
WI 2); the wrapper refuses an overlap with its pinned placement.

**The host tool** (WI 3, EXPERIMENTAL, hardware NOT RUN): `tools/sp11_stick_bundle_build.py` (`docs/SP11-STICK-BUILD.md`).
`facts-check`, `place-check`, `synthetic` and `authorisation-template` work from a facts file and the local dumps (D1, D4, D5, D9:
required arguments); `real` (a kernelcache through `xnuboot.build()` and a boot-ready `--descriptor`; the committed
`config/boards/sp11.json` `is a template and is refused`[docs/SP11-STICK-BUILD.md:165]) is written and NOT RUN, so U9 stands. It reads
the MADT itself (section 7), not through `from_hamoa`.

## 5. Memory (Q3)

Firmware map of the unit (boot 6, 231 descriptors; sums from the local `memmap.txt`):

| Region | Extent | Survives ExitBootServices | Rule for the monitor |
| --- | --- | --- | --- |
| Conventional (7) | 32105 MiB: bank 0x880000000 to 0x1000000000 (30720 MiB), low extents 590, 339, 283, 98, 21, 19, 11 MiB | free | guest and work here only |
| Runtime code and data; ACPI reclaim | 0xD4FBA000 to 0xD5406000; 0xD5466000 to 0xD57BF000 | yes | never touch (the ACPI tables are read later) |
| Reserved | 245 MiB, includes the panel 0xE4800000 to 0xE69C0000 and 0xFF800000 to 4 GiB | yes | never touch; panel mapped RW by design |
| Boot-services data | 153 MiB; one extent 0xCF2D3000 to 0xCF57E000 holds the NVMe's admin queues ASQ 0xCF3E7000, ACQ 0xCF3E8000 (boot 6) | free by UEFI rule, but the controller is enabled and owns them | do not reuse (A16 rule: only type 7 is free: `if kind == 7 and pages`[probes/monitor_work.py:32]) |
| Loader | runner image, map buffer, preload table, payload files, stack | yes | never overwrite |

RAM ends exactly at 2^36 (T09, boot 3; the map above). The monitor's stage 2 is 36-bit (`STAGE2_IPA_BITS = 36`[probes/xnuboot.py:230],
`IPA_LIMIT = 1 << 36`[probes/monitor_work.py:12]); the SP11-only span check
(`probes/xnuboot/stage2.c:253`, -257 to -274) lets a range end exactly there and none start at or beyond it, so a bank placement
passes.

Placement A, low: the A16 recipe needs 672 MiB, the largest low extent is 590 MiB (576 on the 32 MiB grid): it fails. With the
240 MiB work area and the loader's 48 MiB reserve, a 288 MiB guest passes `place-check` on all six local maps and 320 MiB is
refused in all six (`PLACE_NOT_CONV`), so the low limit lies between them (`docs/SP11-STICK-BUILD.md` section 7; VERIFIED host
arithmetic on OBSERVED maps; a guest smaller than the 384 MiB A16 smoke is untested). The loader prefers low (`probes/run.py:1227`).
Placement B, the bank (0x880000000 +): 384 MiB passes the same six maps; the loader's `large` branch (`probes/xnuboot.py:481`),
host-modelled only (`tools/audit-device-exposure.py:55`); no hardware run of any monitor above 4 GiB, and the EL2 map's coverage
is UNKNOWN (U1; `el2mem`, WI 2).

The A16 layout assumes a free span under 4 GiB, a q1n1 heap (`CODE_AT`..`WORK_AT`[probes/run.py:231-236]), `reserved(info)` naming
q1n1's own ranges (`probes/monitor_work.py:15-17`) and a fixed 240 MiB `monitor_work.WORK_SIZE`. The SP11 pins one placement at
build time (`docs/SP11-STICK-BUNDLE.md` section 5); the wrapper verifies it at EL2: inside one conventional descriptor of the final
map, outside every declared range, the framebuffer and the NVMe's boot-services extent.

## 6. Timer (Q4)

**6.1 What the A16 does** (VERIFIED, read in the code). Host counter 19.2 MHz; the guest is told 24 MHz
(`virtual_counter_hz=24000000`[probes/guestprofile.py:323], and in the watchdog and AOP timebase specs). Boot refuses unless
CNTFRQ_EL0 equals the loader's host rate and `ID_AA64MMFR0_EL1.ECV` is non-zero (`!(mmfr0 >> 60)`[probes/xnuboot/prepare.c:91]).
Guest counter and virtual timer reads are trapped and converted: Apple aliases (op1 4, CRn 15) by TIDCP, architectural
CNTVCT/CNTVCTSS by `CNTHCTL_EL2` bits 14 and 13 (EL1TVCT, EL1TVT: FEAT_ECV level 1; `(1UL<<14) | (1UL<<13)`[probes/xnuboot/prepare.c:452],
no read-back on the A16, SP11 builds read it back last: section 9); EL1 physical access is cleared and any other architectural timer
register stops the run (`CLOCK_TIMER_UNSUPPORTED`[probes/xnuboot/trap_dispatch.c:849]). The monitor's own clock is `CNTVCT_EL0` at EL2
(`mrs %0, cntvct_el0`[probes/xnuboot/sysreg_clock.c:21]; physical minus `CNTVOFF_EL2` at E2H 0, an offset it never programs);
`CNTPOFF_EL2` is used nowhere (grep of `compat probes shadow`; only the unlinked host model, `compat/timer-offset-model.h:9-14`).

**6.2 What the unit says** (OBSERVED unless marked): CNTFRQ_EL0 0x124F800 (19.2 MHz), CNTVOFF_EL2 0 on the boot CPU (boot 3, T03) and on
all eleven secondaries (boot 7), ECV field 2. Linux's "Broken CNTVOFF_EL2" erratum matches the unit's MIDR 0x512F0011 (an INFERRED
prior for O1, plan E17; how the register is broken is UNKNOWN). The `CNTPOFF_EL2` read never returned (boot 4, line count;
INFERRED), cause INFERRED `SCR_EL3.ECVEn` clear, so ECV level 2 is no option. **Not measured:** whether bits 13 and 14 can be set at
EL2 and trap EL1 and EL0 reads, and whether the same EL3 gate controls them: UNKNOWN. The monitor mixes `CNTVCT_EL0` samples with
`CNTPCT_EL0` absolutes (`timer_arm`), sound only at `CNTVOFF_EL2` 0: the A16 monitor records only the boot CPU's value
(`mrs %0, cntvoff_el2`[probes/xnuboot/prepare.c:71]) and never tests it, the SP11 build refuses a non-zero one (-148).

**6.3 What the gates cannot see.** With bits 13 and 14 inert the A16 gate (ECV = 2) and the SP11 read-back (section 9) both pass.
The kernel's time path through the Apple aliases (TIDCP) and EL0's alias encoding, emulated from the vector trap
(`0xd53cfac0U`[compat/clock.c:7]), stays scaled and needs no ECV; whatever reads the architectural `CNTVCT_EL0` would read an
unscaled count (who does: UNKNOWN). The report counts those reads (`arch_counter_reads`[probes/xnuboot/report.c:283]), so M1 shows a
number but cannot show whether the traps work.

**What the repo says about the guest** (the kernel was not inspected here): XNU reads `'timebase-frequency'`[probes/xnuboot.py:2054]
from the boot cpu node and a root `clock-frequency` (zero panics, `docs/BOOT-CHAIN.md` section 9). CNTFRQ_EL0 stays the host rate
(EL2 cannot trap it), so a scaled guest rate needs a kernel whose audit finds no CNTFRQ access (`probes/clockcontract.py:3-6`).

**Options.** **O1**, the A16 recipe (24 MHz scaled by traps, with the SP11 guards of section 9): needs bits 13 and 14 effective at
EL2; risk: silent mis-scale if the bits stick but do not trap; decided by T-HW1 and T-HW2. **O2**, 19.2 MHz everywhere (device tree,
watchdog and AOP specs; CNTFRQ_EL0 already reads 19.2 MHz): profile changes only; the repo blames the 24 MHz choice on the watchdog's
checked arithmetic (`docs/BOOT-CHAIN.md` section 9, not re-derived); decided by an A16 run at 19.2 MHz (its host rate too), the
operator. **O3**, patching the guest away from architectural counter reads: a Mac-side kernel scan, then a patch; it breaks "stock
kernel" and the image is digest pinned (`docs/KERNEL-PATCHES.md`); only if O1 and O2 fail.

**6.4 Experiments.** Host first: WI 9 (a counter-visibility matrix extending `tools/test-timer-offset-model.py`; a Mac-side
kernelcache scan beside `probes/clockcontract.py`, NOT RUN: no kernelcache here). Hardware: **T-HW1** writes CNTHCTL_EL2 with the
monitor's value, reads back, restores (no CNTPOFF); **T-HW2** is an EL1 stub reading CNTVCT_EL0 and the virtual timer under the traps
and returning by HVC; both are BUILT, NOT RUN (q1n1 1e7318f; D3). T-HW1 failing eliminates O1; T-HW2 passing makes O1 the decision.

## 7. Interrupts and CPUs (Q5)

| Item | Monitor today | Unit | Label |
| --- | --- | --- | --- |
| GICD, redistributors, EL2 timer | SP11 builds must define five macros or `secondary_gic_start` refuses (the M1 profile does); A16 defaults differ from the unit only in the limit, 0x17500000 (`probes/secondary.c:17-31`); the boot-CPU path has no GIC address; deadlines and the stop run on the EL2 physical timer `CNTHP_*`, INTID the literal 26 (`probes/xnuboot/async.c:91`, `compat/fast-timer-irq.h:56`), IRQs routed to EL2 by `HCR_EL2.IMO` (`probes/xnuboot.S:175`) | MADT (boot 6 dump): GICD 0x17000000 v4, GICR 0x17080000 + n x 0x40000 (12 frames, last ends 0x17380000), ITS 0x17040000; T05 read all 12 frames (boot 3); GTDT non-secure EL2 timer GSIV 26 (boot 6 dump) | VERIFIED / OBSERVED |
| PSCI conduit | `smc #0`[probes/secondary.c:52], secondary start only | FADT `arm_boot_arch` 0x1 (SMC); PSCI v1.1 (boots 4, 5); CPU_ON of all 11 cores succeeded from EL2 (boot 7) | OBSERVED |
| Guest interrupts | virtual AIC3 with timer FIQ and IPIs; physical forwarding only for the GPU (`docs/BOOT-CHAIN.md` section 8) | M1 has no physical device interrupt | VERIFIED |
| vCPU count | `#define GUEST_CPU_COUNT 6U`[compat/cpu.h:57] on the A16; SP11 default 6, overridable (`compat/cpu.h:44`) | the J700 tree has six cpu nodes; `cpus=1` trips a PMGR assertion (`docs/BOOT-CHAIN.md` section 7) | VERIFIED |

**The ACPI tables (OBSERVED, the same in all six local dumps, each parsed here).** MADT revision 5: 12 GICC entries of 80 bytes
with their own redistributor bases, one GICD structure of 25 bytes (ACPI: 24; the extra byte is 0), then the ITS structure, no
GICR-range structure. MCFG: eight allocations, segments 0 to 7 (buses 0..255 for 0 to 2, 0..1 for 3 to 7; segment 2 at
0x6000000000). The boot-contract adapter (`probes/bootcontract.py`) now reads the MADT: RESOLVED in e3844fa (the 25-byte GICD,
`GICD_PADDED_LENGTH = 25`[probes/bootcontract.py:155]) and a1fb91c (redistributors from the per-CPU GICC bases,
`def _gicr_ranges_from_gicc`[probes/bootcontract.py:435]). VERIFIED here (a boot-ready board built in memory, the six dumps): CPUs,
GICD, GICR and ITS pass, then `check_hamoa_platform` refuses at `ecam-mismatch`: a descriptor holds at most four ECAM entries
(`MAX_ECAM = 4`[tools/board_descriptor.py:68]) and the adapter demands set equality (`if got != want.ecam:`[probes/bootcontract.py:539]):
D11. Not M1's: `PIN.DAT` does the board-info job, and the WI 3 builder reads both tables itself
(`tools/sp11_stick_bundle_build.py:173-194`).

**Build evidence** (run here): the M1 profile (A16 flags, the profile's defines, the local NV2 record) BUILD PASS, 314,871 bytes;
`GUEST_CPU_COUNT=1U` and `=4U` **FAIL** at static assertions (`probes/secondary.h:27` and `probes/secondary.h:29`, re-run here; four
more sites on the first audit's tree). So "one vCPU" means six records, one released: M1 runs vCPU 0 on q1n1's CPU (no
`SNAP_BOOT_RELOCATE`) with x4 = 0; XNU's first CPU-start write falls to stage 2 and stops the run (A16 behaviour,
`compat/boot-relocate.h:150-153`), a controlled stop if the 5 s bound is not reached first; whether XNU gets there first is UNKNOWN.

**Later: secondaries.** `probes/secondary.S` assumes EL2 and a matching MPIDR, then overwrites VBAR_EL2 and HCR_EL2 (`mov x1, #0x80000000`[probes/secondary.S:23]),
installs the host-recorded regime with the MMU off and runs on its own stack. The unit's secondaries start at EL2 with MMU and
caches off (`SCTLR_EL2` 0x30000000) but a non-uniform EL2 state (OBSERVED: boot 7, restart probe step 3): seven cores (0X200,
0X300, 0X10000, 0X10100, 0X10300, 0X20000, 0X20100) HCR_EL2 0x80000000 and VBAR_EL2 0; 0X100 (first start) and 0X10200 HCR_EL2
0x1488000038 and one high-half VBAR_EL2, equal on both within a boot and different between boots; 0X100's HCR_EL2 and VBAR_EL2
survived its own CPU_OFF and CPU_ON (step 3); 0X20200 and 0X20300 UNKNOWN. The entry code fits because it overwrites the state,
but the wrapper must write every EL2 register it relies on and must not assume VBAR_EL2 or HCR_EL2 are reset on a restarted
core. MIDR differs by cluster (section 8) and `sp11_launch_gate` judges each core against one embedded record; the NV2
functional test ran on the boot CPU only.

## 8. ID registers, PAuth and friends (Q6)

| Item | Monitor today | SP11 fact | Change | Label |
| --- | --- | --- | --- | --- |
| MIDR | the guest sees the real value (read once by the kernel) | 0x512F0011 on cluster 0, 0x511F0011 on clusters 1 and 2 (boot 7) | none for M1; per-cluster NV2 record later | OBSERVED |
| PAuth | ID normalisation only: ISAR1 QARMA5 fields mapped to the J700 ISAR2 shape; hardware computes; the loader drops PAC diversity, re-signing is the monitor's job (`probes/idregs.h`, `tools/kc-load.py:126-128`) | QARMA5, APA 5, GPA 1 (X2 T01, boots 3, 6) | none; the algorithm is invisible to the monitor; first guest PAC use is the test (U8) | VERIFIED / OBSERVED |
| TGran16 | not forged; the 16 KiB guest granule is shadowed into 4 KiB tables (`shadow/mmu.c`, `shadow/shadow.c`) | TGran16 absent, as on the A16 (X2 T01, boots 3, 6) | none: CPU independent | VERIFIED / OBSERVED |
| FGT, AMU | FGT only under `SNAP_LEAN_TRAPS == 2`[probes/xnuboot/prepare.c:491] (default 0); AMU untouched | FGT 1 (MMFR0), AMU 2 (PFR0) (X1 probe page) | none for M1 | VERIFIED / OBSERVED |
| RNG | `pauth_domain_prepare` refuses (-24) without ISAR0.RNDR or PAuth, or when 64 reads in a row fail (`probes/xnuboot/prepare.c:33`) | ID_AA64ISAR0_EL1 0x1221111110212120: RNDR 1; ISAR1 APA 5; ISAR2 not read (X1 probe page) | none; the ID gates should pass, the RNDR reads are untried | OBSERVED / INFERRED / UNKNOWN |

## 9. Platform contract and the gates (Q7)

| Where it refuses | Status | On the M1 path? | Becomes, for M1 | Label |
| --- | --- | --- | --- | --- |
| NV2 launch gate: no embedded record, MIDR/REVIDR/MMFR2/MMFR4 mismatch, PAuth shape (-90 to -95) | `snap_nv2_gate_decide_embedded`[probes/xnuboot/prepare.c:356]; `SNAP_NV2_RECORD_HEADER`[compat/nv2-gate.c:10] | **yes, refuses every launch today** | a record, the profile's required input; the local one (`build/nv2-record`) is from the first X2 image (E2H = 1): D6 | VERIFIED |
| vCPU count needs the fair lock (-146), checked first | `GUEST_CPU_STATUS_FAIR_LOCK`[probes/xnuboot/prepare.c:101] | yes | `-DSNAP_FAIR_TOKEN=1`, in the M1 profile (opt-in, `tools/cycle.py:404`; D10) | VERIFIED |
| `CNTHCTL_EL2` read-back (-147: EL1TVT or EL1TVCT clear) and `CNTVOFF_EL2` not 0 on this CPU (-148), the last refusals of `vcpu_arch_prepare` | `compat/sp11-timer-guard.h:49-50`; `snap_sp11_timer_guard(`[probes/xnuboot/prepare.c:614] | yes, SP11 builds only | stay: `CNTVOFF_EL2` reads 0 on all twelve cores (boots 3, 7); the read-back is T-HW1's question asked by the launch | EXPERIMENTAL / OBSERVED |
| SSD adoption (-145) | `!ans_window(c)`[probes/xnuboot/ans_glue.c:104] comes before the SP11 refusal at `probes/xnuboot/ans_glue.c:113` | **no**: the smoke profile has no ANS window | stays a refusal for X6 | VERIFIED |
| DCZID != 4 (-26), PAN absent (-23), native-writer debug (-35 to -38), RNDR (-24) | `if(dczid!=4)return -26;`[probes/xnuboot/prepare.c:550] on every CPU; `probes/xnuboot/prepare.c:82` | yes | DCZID 4 and DC ZVA passed (boot 3, T04); the DFR0 and -24 ID gates should pass (INFERRED, sections 3 and 8); other cores' DCZID, PAN, breakpoint and MDSCR readbacks, OS lock: UNKNOWN | OBSERVED / INFERRED / UNKNOWN |

A guard refusal restores q1n1's `CNTHCTL_EL2` and leaves an `EV_OTHER` record (pc 0x5443); `PREPARE_FAILURES` names both
(`probes/run.py:689-692`). They show that bits 13 and 14 do not stick or that the offset is not zero, **not** that the traps work
(bits that stick and trap nothing pass): a pass is UNKNOWN until T-HW2 (`docs/SP11-MONITOR-PROFILE.md` section 6; host tests not
re-run). **The M1 profile** (WI 5): the A16 flags, SP11, `-DSNAP_FAIR_TOKEN=1`, the five GIC macros; no relocation, thermal define
or HVC patch (D9); its launch contract, x4 = 0 and the early-stop thermal bit clear, is the wrapper's to keep (the monitor does not
refuse it).

**No root device:** the A16 smoke recipe demands a profile with no Adreno (`tools/cycle_lib/bootstrap_smoke.py:101`) and no ANS boot,
and sets `SNAP_NO_FB=1` and the `SNAP_SPTM_HVC_CALLS` kernel patch (`tools/cycle_lib/bootstrap_smoke.py:24`). Its first milestone is
the first monitor call (`probes/xnuboot.c:22`); the first unmodelled device ends the run with a
stage-2 fault carrying its IPA (which one: UNKNOWN). **Console:** keep `SNAP_NO_FB=1`; `serial=3` output reaches the
console ring (section 10); glyphs are X5. **Native calls or the HVC patch:** the stock path takes the first call through a stage-2
execute-never fault (`HPFAR_EL2` reads zero for it on the A16, `docs/BOOT-CHAIN.md` section 10; Oryon v1 UNKNOWN, U10); the HVC
patch (`docs/KERNEL-PATCHES.md`) avoids the fault but changes the kernel: D9.

## 10. Console, diagnostics and the restart (Q8)

A16: the proxy reads the report. SP11: the wrapper (30 lines) and SNAPREP (40) print through the runner's `puts`, `REPORT.DAT` holds
the report head and the monitor's window (`docs/SP11-STICK-BUNDLE.md` section 6), and the panel is photographed: film it, the
board may restart. A monitor fault after the vectors are installed lands in `bad:`[probes/xnuboot.S:410], which stores ESR, ELR
and FAR and takes `exit`, so the reporter can print it; a fault before that, or in the wrapper, lands in q1n1's fault page, and a
hang prints nothing after its last line. A guest panic reaches the SPTM panic page (`probes/xnuboot/prepare.c:104`) and the
monitor's 1 KiB console ring (`probes/run.py:199`).

**The restart.** OBSERVED, times UNKNOWN (operator reports): the board restarted by itself after boot 7 and after restart probe
step 2, both cpuon runs, not in step 3 (cpuon; video through UPTIME 36 S, later UNKNOWN): 2 of 3 cpuon boots. UPTIME starts
before the runner, so a chain is inside it: boot 7's UPTIME 16 S was the first heartbeat after the chain and the restart came
"right after the last photo" (INFERRED, UPTIME about 17 to 25 S); step 2's 90 s DONE2 to DONE3 gap holds the operator's power
cycle (45 s once) and the time to notice (INFERRED, weakly, UPTIME about 41 S or earlier, possibly inside the chain). So,
INFERRED, not at one fixed UPTIME. A plain q1n1 page (step 1) was alive at UPTIME 123 S. **The control** (2026-11-16): the same
runner and slbounce with only the read-only `PSCI.BIN` (no CPU_ON, no CPU_OFF) was photographed alive at UPTIME 224 S and 268 S in
two boots, one crisp photograph each (OBSERVED; the state afterwards UNKNOWN). So the read-only runner flow is not what restarts the
board (INFERRED, strongly: had it restarted 2 of 3 boots, both controls surviving had a chance of about 11 %); the restart goes
with the cpuon payload (CPU_ON and CPU_OFF of the secondaries; INFERRED, correlation over five boots); cause UNKNOWN (untested: an
APSS hardware watchdog, X2 item 9 of `docs/SURFACE-PRO-11.md`; a reaction to the core power cycles). After a self-restart the
firmware follows its normal boot order, not the stick (operator report after boot 4): every stick boot is started by the operator
(power off, Volume-Down + Power) and no plan runs unattended. M1 starts no secondary (x4 = 0), so the restart is probably irrelevant
to it (not excluded: the control ran none of the monitor's stores and no 138 MiB load). **Budget (D5), a precaution of low weight,
kept as the default:** bounce to end of report well under 20 s of UPTIME (the earliest plausible restart, in a cpuon run, is about
UPTIME 17 S); the stop is the monitor's, never a wait.

## 11. Thermal and power (Q9)

`compat/a16-thermal.c` reads A16 physical addresses (`compat/a16-thermal.c:7`); its only platform test (preflight -28) is
`physical_hz!=19200000`[probes/xnuboot/prepare.c:93], which the SP11 also satisfies. It runs only on the host's opt-in
`EARLY_STOP_F_THERMAL`[compat/early-stop.h:28]; `compat/hamoa-thermal.c` is a read-only model, linked and never called, that refuses
every admission until X2 measures the platform. An unclocked read at EL2 can hang the board (boot 3: the segment 3 ECAM read never
returned and the board reset itself, operator report). For M1: leave the A16 flag unset; "thermal guard on"
(`docs/SURFACE-PRO-11.md`, section 8, "Always") cannot be met, so the first run needs the operator's power attestation
(`tools/cycle_lib/targets.py`: AC or at least 50% battery, at most 30 minutes old, bound to the run) and D8. Never bypassed: AC or
50% battery; no CPU or GPU clock or voltage change; a time-boxed run; no TSENS read at an unmeasured address.

## 12. Safety and authorisation (Q10)

| M1 step | Hardware operation | AGENTS.md section 5 clause touched | Status |
| --- | --- | --- | --- |
| Stick boot, Shell, slbounce, EL2 takeover; the runner's read-only FAT access | as boots 4 to 7 | Secure Boot setting (owner's way A or B, `docs/SURFACE-PRO-11.md` section 8.1) | existing |
| Wrapper stores to RAM, system registers and the GIC | new EL2 code | the runner's rule: a payload storing to device registers needs a named, SHA-pinned authorisation (`q1n1:docs/SP11-PAYLOAD.md`); `PIN.DAT` carries the store classes (D4) and the authorisation digest, zero refused (D7) | NEW |
| `_start` writes HCR, VBAR, TPIDR, VTCR, VTTBR, CNTHCTL, MDCR, OS lock; the guest then runs Apple code at EL1 | CPU state restored on return; guest MMIO limited by stage 2 to RAM (no panel with `SNAP_NO_FB=1`) | recoverability: a documented stop and recovery path; partition and firmware writes: none reachable | NEW |
| Disk, NVMe, USB, clock, voltage, DVFS | none: stage 2 omits them, no code path; the guest's `hvc` goes to firmware only for the GPU profile (`probes/xnuboot/trap_dispatch.c:702`), `smc` only for PSCI start (`probes/secondary.c:52`) and the GPU | A1 and A2 NOT granted, not touched; no unsafe clock or voltage operation | NOT RUN |
| Attestation, prepared-equals-booted, one client | `cycle.py` ends every SP11 mode but the passive checks in `'unimplemented-flow'`[tools/cycle_lib/targets.py:600], so a stick boot is outside all of its layers | AGENTS.md section 5: AC or 50% battery, no change between prepare and boot | the builder carries the digests; the power attestation stays the operator's |

## 13. Ordered work plan to M1 (Q11)

Unknowns referenced: U1 EL2 map coverage; U2 trap effect; U3 PPI 26 delivery; U4 restart; U5 OS lock, MDCR_EL2, ICC_SRE_EL2, DFR1,
ISAR2; U6 bundle rate and placement; U7 guest in the bank; U8 PAC under QARMA5; U9 kernelcache (Mac); U10 stage-2 syndromes.

| WI | Work | Depends | Acceptance (can fail) | Host-first evidence | HW boot | UNKNOWN / decision |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | **Freeze the interfaces**: DONE, EXPERIMENTAL (b8e8f81; `docs/SP11-STICK-BUNDLE.md`) | none | a schema test shared by builder and wrapper rejects a seeded bad record | the interface's host tests (C and Python twins) | no | none |
| 2 | **EL2 probes**: BUILT, NOT RUN (q1n1 d8a5d2b): read-only `el2mem` (`AT S1E2R` scan of the RAM incl. the bank, ID registers; `MDSCR_EL1`, `OSLSR_EL1`, `ICC_SRE_EL2` last, after an 8 s warning: they may go to EL3, INFERRED) and `hashbig` (SHA-256 of the largest data file at EL2, timed, with its base) | none | the bank and the largest low extent answer or the fault is named; the registers print; `hashbig`: digest MATCH, rate, base | q1n1 host and QEMU tests with a modelled MMU (23 SP11 suites PASS on d8a5d2b: the coordinator's run) | 1 or 2; the first is staged, ready to run, not run (`memhash`: `hashbig`, `el2mem`, a 144 MiB file; read-only) | resolves U1, U5, part of U6; feeds D1 |
| 3 | **Bundle builder**: BUILT for the synthetic path, EXPERIMENTAL (8a0ac97; `docs/SP11-STICK-BUILD.md`); `real` NOT RUN (needs the kernelcache and a boot-ready descriptor) | 1, D1 | deterministic output; refuses facts that disagree with the dump, a placement outside one conventional descriptor, over-cap files and a descriptor not boot-ready; real: `python3 probes/xnuboot.py --check` passes offline at the pinned base | its seeded refusals; `tools/test-board-descriptor.py`, `tools/test-hamoa-bootcontract.py` | no | U9; D1, D11 |
| 4 | **Wrapper, loader and reporter** under `SNAP_SP11_STICK`: 4a BUILT for the dry run, EXPERIMENTAL, NOT RUN (6151945, `docs/SP11-STICK-WRAPPER.md`, not audited here): precondition refusals, chunk copy with cache maintenance, rendering; UNIMPLEMENTED (4b): the monitor entry (GIC bring-up, the `_start` call) | 1 | a host harness with a fake runner refuses every damaged bundle (outside the region, overlap, bad hash, truncated chunk, wrong MIDR); the A16 build stays IDENTICAL (the coordinator's run: IDENTICAL) | native tests under ASan and UBSan, as for the runner | no | D2, D4 |
| 5 | **M1 monitor profile**: BUILT, EXPERIMENTAL, host side (`docs/SP11-MONITOR-PROFILE.md`; section 9) | 1 | BUILD PASS (314,871 bytes here, local record); `tools/monitor_baseline.py` IDENTICAL for the A16 (the coordinator's run, not re-run here) | the profile's host tests (`tools/test-sp11-m1-profile.py`, `tools/test-sp11-timer-guard.py`), `tools/test-sp11-integration.py` | no | D6, D9, D10 |
| 6 | **Authorisation package**: every payload named by SHA-256, each store class listed with its register, window and evidence; the builder prints the record text (`authorisation-template`) | 4, 5 | a recorded operator decision naming the digests | review against the runner's rule | no | D4, D7 |
| 7 | **Bundle dry run**: a real-size data file, the wrapper stops before `_start` | 3, 4, 6 | all hashes agree before and after the bounce; copy and clean finish; timings and the firmware's placement of the big file are printed | host dry run of the wrapper; `hashbig` (WI 2) shows the rate and base earlier | 1 | resolves U6 |
| 8 | **GIC and stop dry run**: the wrapper arms the stop timer, `_start` replaced by an immediate return | 7 | PPI 26 arrives, the stop is on time, EL2 state restored, the page alive afterwards | GIC register-sequence model, as `compat/el2-entry-model.c` does for entry | 1 | resolves U3; D2 |
| 9 | **Timer probes** T-HW1 (`cnthctl`), then T-HW2 (`el1trap`; section 6.4): BUILT, NOT RUN (q1n1 1e7318f); `el1trap` is the first code below EL2 on the unit | none | T-HW1: `BITS13_14_STUCK=YES` and `RESTORED=YES`; T-HW2, only after it: EL1 reads arrive as traps with the scaled value | counter-visibility matrix; a model of the EL1 stub | 1 or 2 | resolves U2; D3 |
| 10 | **Restart survey**, before SMP work only (M1 does not need it); operator in the loop, one boot per sample. DONE: the psci-only control, alive at UPTIME 224 S and 268 S (section 10). Left: (a) film a cpuon page until it restarts (the last frame dates it: a video's `creationdate` and the stick's DONE marker times are both local time and agree within the FAT's 2 s); (b) a cpuon variant that starts and stops ONE core only; (c) a variant that leaves cores running | none | cpuon restarted in 2 of 3 boots, times unknown: (a) dates a restart or shows none up to a stated UPTIME; (b), (c) show whether one core, or cores left running, change it | payload-plan tests (`tools/test-sp11-autorun-payload.py`) | 1 or more each | narrows U4 |
| 11 | **M1 run** | 3, 5, 8, 9 | the seven rows of section 2 | all of the above | 1 to 3 | resolves U7, U8, U10; D5, D8 |

**Critical path:** 4 -> 6 -> 7 -> 8 -> 11 (WI 1 is done; WI 3's synthetic bundle, at real size, serves 7); WI 3's real path (D1, a
boot-ready descriptor, the kernelcache) and WI 9 join at 11, WI 5 at 6 (D6); WI 2, the first hardware boots, feeds D1 and U6; WI 9
waits for D3; WI 10 is off the path. Decisions gate WI 3 (D1, D11), 4 (D2, D4), 5 (D6, D9, D10), 6 (D7), 9 (D3) and 11 (D5, D8).
Built is not closed: a row that depends on an UNKNOWN stays open until its experiment has run.

## 14. The first hardware run (Q12)

If it works the panel shows, in order: the runner's `LOADED` and `READY` lines; the wrapper's check lines; `COPIED`, `CLEAN`,
`GIC READY`, `ENTER`; then the reporter: `ENTERED=1`, the terminal reason, ELR and ESR, the first monitor calls, the guest console
tail, the architectural counter reads and the elapsed ticks; then the heartbeat (a design, not an output). The time budget is a
target, UNKNOWN until WI 7: hash and copy at most 4 s, guest at most 5 s, report at most 2 s, all well under the 20 s of UPTIME of
section 10.

**Stop rules.** (1) A wrapper `REFUSED` line ends the boot; fix the cause, never retry the same bundle. (2) No page change for
twice the time box after `ENTER`: note the last line and power cycle; a repeat follows rule 1.
(3) Two resets at the same last line stop the series. (4) The time box and the 20 s UPTIME budget are defaults, the budget a precaution of low weight (section 10); only the operator relaxes them (D5).
(5) A touch outside RAM is a finding, not a retry. (6) One hardware client, AC power or at least 50% battery, the operator present
and filming; a spinning guest that no timer stops is ended by the operator's power cycle, which writes nothing.

**Expected first failures, most likely first.**
1. NV2 gate refusal (-90 to -95): certain until a record is embedded (D6); with the local one MIDR and MMFR2 equal the unit's (boot 7,
   X1 probe page), REVIDR and MMFR4 are unread (`el2mem` reads REVIDR): a pass is INFERRED.
2. The stop timer never fires or the wrong interrupt arrives (GIC state, U3): the guest runs unbounded and the operator ends it.
3. A data abort at EL2 in the monitor on the bank (U1): the `bad:` report, FAR in the bank.
4. The first monitor call never arrives as expected on native calls (stage-2 execute-never fault, `HPFAR_EL2`, U10).
5. Debug (U5): the DFR0 gate should pass (INFERRED); a breakpoint write or readback (-36), MDSCR (-37) or the OS lock can fail; -24
   only if 64 RNDR reads fail; the timer guards -147 and -148 (section 9; inert traps refuse nothing, only T-HW2 shows them); FPAC
   or PAC failure at the first signed return (U8).
6. XNU stops at its first unmodelled device with a stage-2 fault and an IPA: that is success for M1.
7. A restart with no report (U4; M1 starts no secondary, so unlikely): the page is gone; the stop rules apply.

**Kill criteria (plan section 3.3).** K1: the NV2 redirect or its negative control failing in the monitor's context stops the
port. K2 to K7: unchanged or not exercised. Proposed: an EL2 map that cannot reach the placement, and inert ECV traps with O2 and O3
both unacceptable.

## 15. Decisions I need from the operator

- D1 Placement: a low region with a smaller guest (288 MiB fits, 320 does not) or the bank at 0x880000000; after WI 2.
- D2 Stop mechanism: GIC-timer stop (needs GIC stores) or a polling stop in the wrapper (it cannot bound a spinning guest).
- D3 Timer, before M1: authorise `cnthctl`, then `el1trap` only after `BITS13_14_STUCK=YES` and `RESTORED=YES` (each by named SHA-256: both write EL2 state); and whether to run the optional A16 experiment at 19.2 MHz (O2).
- D4 Whether the wrapper, a payload, may store to GIC registers, and which stores are authorised.
- D5 The M1 time box: 5 s proposed; bounce to end of report well under 20 s of UPTIME as a precaution of low weight (earliest plausible restart, in a cpuon run, about UPTIME 17 S): keep or relax.
- D6 NV2 record: accept the first-image record (E2H = 1) or require one from the E2H = 0 regime.
- D7 Authorisation of the SHA-named monitor payload (section 12).
- D8 Thermal: accept an attested, time-boxed, one-CPU run as an exception to "thermal guard on", or wire the Hamoa guard first.
- D9 `SNAP_NO_FB=1` with native calls only, or also the A16 smoke's `SNAP_SPTM_HVC_CALLS` kernel patch.
- D10 `-DSNAP_FAIR_TOKEN=1` as an SP11-only opt-in.
- D11 The ECAM rule of the boot-contract adapter (the GICD, e3844fa, and the redistributors, a1fb91c, are resolved; the rule is
  needed before any `from_hamoa` use and a boot-ready SP11 descriptor, not for M1 or the bundle): the MCFG lists eight allocations,
  a descriptor holds four, the adapter demands equality. Raising the count alone fails (VERIFIED here, in memory: an eight-entry
  descriptor is refused, segment 2 `extends beyond 1<<`[tools/board_descriptor.py:198] the monitor's 36-bit limit). Relax that
  bound too and keep equality, or accept a descriptor that lists only the segments the port uses, as a subset of the MCFG.

## 16. What I could not establish

- The J700 kernelcache size and anything about its code (no copy under `downloads/`).
- Whether the EL2 map reaches the bank; whether CNTHCTL_EL2 bits 13 and 14 work; whether `SCR_EL3.ECVEn` gates them; whether XNU, SPTM
  or TXM care about 19.2 versus 24 MHz beyond the repo's watchdog note.
- The OS lock (OSLSR_EL1), MDCR_EL2 and ICC_SRE_EL2 at the payload call; ID_AA64DFR1 and ID_AA64ISAR2 (`el2mem` reads them: NOT
  RUN); whether the debug and RNDR instructions behave as the ID registers say; the EL2 entry state of cores 0X20200 and 0X20300.
- The restart: its cause and times (section 10, WI 10); whether the read-only runner flow restarts after UPTIME 268 S; the APSS
  watchdog state; whether the report buffer survives a reset.
- Throughput of a 138 MiB read (`hashbig`: NOT RUN), where `AllocateAnyPages` puts it, and the sweep's behaviour at that size.
- Whether the `from_hamoa` steps after the ECAM rule pass on a real boot-ready SP11 descriptor (none exists; the committed one is a template).
- How the guest behaves above 4 GiB on hardware; first PAC use under QARMA5; stage-2 fault syndromes on this core.
- A16 hardware qualification of `SNAP_FAIR_TOKEN` in this tree (its documents are not here).
