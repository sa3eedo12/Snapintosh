<!-- SPDX-License-Identifier: MIT -->
# SP11 monitor profile `sp11-m1`: the M1 build recipe and the two timer guards

**Status: EXPERIMENTAL, hardware NOT RUN.** This note describes work item 5 of the monitor port plan
([SP11-MONITOR-PORT.md](SP11-MONITOR-PORT.md), section 13): a named build profile for milestone M1, a required NV2 record input, and two new
fail-closed refusals in the monitor. What exists is a profile file, a builder, a header, a call site and host tests. The monitor built from the
profile has never run on a Surface Pro 11. None of the operator decisions of the audit (its section 15) is taken here: D6 (which NV2 record),
D9 (the HVC kernel patch) and D10 (the queued lock as an SP11-only opt-in) are carried as the audit states them.

## 1. Reading this note

Labels (AGENTS.md): **VERIFIED** read in this repository or a reproducible host fact (a build, a test), never "ran on the Surface";
**OBSERVED** measured on the unit (boots 3 to 7, the timers boot of 2026-11-16 and the probes) or reported by the operator, one unit, one boot each; **INFERRED** reasoned, not
checked; **EXPERIMENTAL** code exists, unqualified; **UNIMPLEMENTED** no code; **UNKNOWN** not established, a named experiment would
establish it. Test outcomes are not recorded in this note: they belong to the run that made them. Facts about the unit are the audit's.

## 2. What exists

| Part | Where | Label |
| --- | --- | --- |
| The profile: flags on top of the A16 recipe, required input, launch contract | `config/monitor-profile-sp11-m1.json` | EXPERIMENTAL |
| The builder: `show`, `flags`, `build`; refuses without a valid NV2 record | `tools/sp11_m1_profile.py` | EXPERIMENTAL |
| The two guards: decision, register sequence, statuses -147 and -148 | `compat/sp11-timer-guard.h` | EXPERIMENTAL |
| Their call site, last in `vcpu_arch_prepare`, under `#if SNAP_PLATFORM_SP11` | `probes/xnuboot/prepare.c` | EXPERIMENTAL |
| The names of the statuses and the band table | `PREPARE_FAILURES` in `probes/run.py` | EXPERIMENTAL |
| Where q1n1's `sha256.c` and `sha256.h` come from, when `../q1n1` is absent | `$Q1N1_SP11_TREE`, `tools/monitor_build_offline.py` | EXPERIMENTAL |
| Host tests | `tools/test-sp11-m1-profile.py`, `tools/test-sp11-timer-guard.py` and `.c`, `StatusBands` in `tools/test-sp11-integration.py` | EXPERIMENTAL |
| The wrapper, the loader, the bundle builder and the reporter (WI 3 and 4) | [SP11-STICK-WRAPPER.md](SP11-STICK-WRAPPER.md), [SP11-STICK-BUILD.md](SP11-STICK-BUILD.md) | EXPERIMENTAL (the dry run ran once on the unit) |

## 3. The flag set

The profile is `config/monitor-flags.json` (the A16 recipe, unchanged and never edited for the SP11) plus these defines, in this order:

| Define | Why | Label |
| --- | --- | --- |
| `-DSNAP_PLATFORM_SP11=1` | the SP11 units: NV2 and PAuth launch gate, placement model, stage-2 geometry check, the timer guards | VERIFIED (build) |
| `-DSNAP_FAIR_TOKEN=1` | six vCPUs span two clusters; without the queued service lock the launch refuses at -146 (D10: opt-in, SP11 only) | VERIFIED (build and `compat/cpu.h`) |
| `-DSNAP_GICD_BASE=0x17000000` | GICD v4 of the MADT (boot 6 dump) | OBSERVED |
| `-DSNAP_GICR_FIRST=0x17080000`, `-DSNAP_GICR_STRIDE=0x40000` | redistributor frames 0x17080000 + n x 0x40000, 12 frames (boot 6 dump; all 12 read in boot 3) | OBSERVED |
| `-DSNAP_GICR_LIMIT=0x17380000` | where the twelfth frame ends; the A16 default is 0x17500000 and `probes/secondary.c` has no SP11 default, so a build without all five macros makes `secondary_gic_start` refuse | VERIFIED (arithmetic, `probes/secondary.c:17-31`) |
| `-DSNAP_TIMER_INTID=26` | the non-secure EL2 timer of the GTDT (boot 6 dump) | OBSERVED |

The base already holds the native-writer breakpoints (`-DSNAP_NATIVE_WRITER_BREAKPOINTS=1` and `-DSNAP_NATIVE_WRITER_ADMISSION=1`; the unit's
`ID_AA64DFR0_EL1` 0x000F01F010305719 reads six breakpoints, OBSERVED, so the debug gate should pass: INFERRED) and `-DSNAP_NATIVE_CALLS=1`; the
builder refuses a base that loses one of them (`required_base_defines`).

**Deliberately absent.** `SNAP_BOOT_RELOCATE`: M1 runs vCPU 0 on q1n1's CPU and starts no physical secondary. No thermal define: the A16 thermal
guard has no compile-time switch, it runs only when the launch parameters set `EARLY_STOP_F_THERMAL`, and it reads A16 physical addresses, so
the profile forbids every define containing `THERMAL` and its launch contract keeps that bit clear. `SNAP_SPTM_HVC_CALLS`: D9 is open, so this
is the native-calls-only variant; the HVC kernel patch would be one more define here and a change of the guest image, which the bundle builder
owns. `SNAP_NO_FB`, `SNAP_WATCHDOG` and `SNAP_GUEST_MIB` are environment of the guest-image build (WI 3), not flags of the monitor.

**Launch contract** (`runtime_contract`; the profile file is validated when read, the wrapper of WI 4 must honour it): `x4 = 0`, so no host
secondary configuration exists and no physical secondary starts; the thermal bit of `early_stop.flags` is clear. The SP11 monitor does not
itself refuse a set thermal bit (its A16 check, `physical_hz != 19200000`, also passes on this unit), so the wrapper must keep it clear; a
refusal of its own, with a number from the same band, is a recommendation for WI 4 and is not done here.

## 4. The NV2 record: a required input, never chosen here

The monitor refuses every launch until a record is embedded (-90; audit sections 2 and 9). The profile takes it as
`--nv2-record-header FILE`, a header written by `tools/nv2_record.py ingest`, which becomes `-DSNAP_NV2_RECORD_HEADER="<absolute path>"`.
There is no default, no environment variable and no search: without it `show`, `flags` and `build` stop with exit status 2 and name decision D6.

The header is `#include`d into the monitor, so the builder accepts only the exact text the converter writes for the 96 bytes it carries; any
other content would be code in the monitor. It refuses: a missing file, a link, a file that is not regular, more than 8192 bytes, non-ASCII
content, a byte count other than 96, a record that does not parse (magic, version, CRC, reserved fields), a header that is not the converter's
own text, a path that cannot sit in a C string literal, a record for another SoC than `compat/nv2-gate.h` builds for (x1e80100), and a record
the launch gate would deny whatever the live registers read (a VNCR or control result that is not "pass", evidence other than VERIFIED or
OBSERVED, ID registers that do not advertise NV2). What it cannot check, it prints: SoC, build id, tool, MIDR, REVIDR, `ID_AA64MMFR2_EL1` and
`ID_AA64MMFR4_EL1` as recorded, the SHA-256 of the header and of the 96 bytes, and "regime NOT RECORDED": the record does not say whether
`HCR_EL2.E2H` was 0 or 1 when it was measured, and the monitor runs with E2H = 0. The launch gate also compares MIDR, REVIDR and both ID
registers with the CPU it runs on, at launch, which no host tool can know.

The local record under `build/nv2-record` came from the first X2 image (INFERRED E2H = 1: run 4's page printed no `HCR_EL2`; see D6 in [SP11-MONITOR-PORT.md](SP11-MONITOR-PORT.md) section 15). The tests use it, if present, only to show that the builder
prints a record's own identity, and no value of it is written into a tracked file. Which record the M1 build embeds is decision D6, decided 2026-11-17: this one.

## 5. Building

```sh
python3 tools/nv2_record.py ingest <measurement.log> --out build/nv2-record/record.bin --header build/nv2-record/record.h
python3 tools/sp11_m1_profile.py show  --nv2-record-header build/nv2-record/record.h
python3 tools/sp11_m1_profile.py build --nv2-record-header build/nv2-record/record.h build/sp11-m1
```

`flags` prints the final flag list as JSON (a `flags` key, the form `tools/monitor_build_offline.py` takes). `build` needs Homebrew `llvm` and
`lld` and q1n1's `platform/uefi/sha256.c` and `sha256.h`: from the sibling `../q1n1`, or from the checkout named by
`Q1N1_SP11_TREE=<q1n1 checkout>` for a layout without that sibling (nothing is created outside the repository; a value that is set and wrong is
refused, never replaced by the sibling). The output directory must be new. It holds the offline build (`xnuboot.bin`, the flat monitor, and
`xnuboot.elf`) and `profile-build.json`: the flags (with the local path of the record header), the record's identity and the monitor's size and
SHA-256, whose `flags` key rebuilds the identical monitor with `tools/monitor_build_offline.py`. It is a local record under `build/`: never
commit it, or the header. The builder removes the binary and ELF and exits 1 when the monitor does not carry the
record's 96 bytes exactly once, or is larger than the 464 KiB code window of `probes/run.py` (`OUT_AT - CODE_AT`). The SHA-256 is what a named
authorisation of the monitor payload (audit section 12, WI 6) refers to.

## 6. The two timer guards

### 6.1 What they check, and what they cannot

| Guard | Refuses when | Why | Label |
| --- | --- | --- | --- |
| CNTHCTL read-back, status -147 | `EL1TVT` [13] or `EL1TVCT` [14] of `CNTHCTL_EL2` reads back clear, after the monitor wrote them and armed everything else | the A16 recipe scales the guest's counter by trapping its reads with those two bits and never read the register back; the ECV gate passes at ECV = 2 whether or not they work | VERIFIED (host model) |
| `CNTVOFF_EL2`, status -148 | `CNTVOFF_EL2` is not zero on this CPU | the monitor mixes `CNTVCT_EL0` samples (physical minus the offset at E2H = 0) with `CNTPCT_EL0` absolutes (`timer_arm`), sound only at zero; the unit shows 0 on the boot CPU (boot 3) and on all eleven secondaries (boot 7) | VERIFIED (host model) / OBSERVED (zero) |

**The read-back catches "does not stick" and nothing else.** Bits that read back set and trap nothing ("stuck but inert") pass it by
construction; only an EL1 round trip that reads `CNTVCT_EL0` and the virtual timer under the traps (T-HW2, audit section 6.4) shows that the
traps work. The host model includes that world as a scenario that is not refused, so the limit is a tested fact and not a hope. A refusal
at -147 is not "the traps work" turned around: a launch that passes does not show that the traps work in the guest's regime. T-HW1 and T-HW2
ran once on the unit (OBSERVED, one boot, 2026-11-16; [SURFACE-PRO-11.md](SURFACE-PRO-11.md) E67): bits 13 and 14 stuck (written `0x6000`, read
back `0x6000`) and an EL1 stub's reads of `CNTVCT_EL0` and `CNTV_CTL_EL0` trapped to EL2, under `HCR_EL2` `0x80000000` and not the monitor's guest
regime, so "stuck but inert" did not occur on the boot CPU. Whether bits 13 and 14 depend on `SCR_EL3.ECVEn` is UNKNOWN (Arm ARM not re-read):
if they do, the traps taking effect would not fit the INFERRED ECVEn-clear explanation of the `CNTPOFF_EL2` stall of boot 4, whose cause stays UNKNOWN.

### 6.2 Where they sit, and what a refusal leaves

Both run in `vcpu_arch_prepare`, on every CPU that prepares (the boot CPU; a secondary decides for itself), after the last other refusal and
just before the CPU is marked prepared. That is a choice, not the place the plan's first sketch gave (beside the `CNTHCTL_EL2` write): by
then the debug and EL1 translation registers the report restores (`MDCR_EL2`, `MDSCR_EL1`, the OS lock, `TTBR0_EL1`, `TTBR1_EL1`,
`MAIR_EL1`) have been captured, so a refusal needs no capture of its own and leaves like -23 and -26; the NV2 gate runs first and keeps its
richer diagnostics (the ID registers it used); the read sees the final state; and the lines of `probes/xnuboot/prepare.c` that the audit cites
stay where they are.

On a refusal the guard writes q1n1's saved `CNTHCTL_EL2` back (with an ISB), records one `EV_OTHER` event and returns the status. The
report restores the same value again, so the two are idempotent. With both guards failing, the CNTHCTL status is returned and the record
keeps both values. The event: `pc` 0x5443; `a` = `CNTHCTL_EL2` as read back, `b` = `CNTVOFF_EL2`, `c` = status | slot << 32.

### 6.3 The status values

| Status | Decoded by | Label |
| --- | --- | --- |
| -147 | `PREPARE_FAILURES` in `probes/run.py` ("SP11 timer guard: CNTHCTL_EL2 did not read back with EL1TVT and EL1TVCT both set ...") | VERIFIED |
| -148 | the same ("SP11 timer guard: CNTVOFF_EL2 is not zero on this CPU ...") | VERIFIED |

They are the first two numbers of the band that `probes/run.py` kept free for the next SP11 refusal (-147 .. -255), and the band table there
and `StatusBands` in `tools/test-sp11-integration.py` now name them; neither collides with `compat/nv2-gate.h` and `compat/oryon-idregs.h`
(-90 .. -95), the NVMe window (-96 .. -145), the vCPU count (-146) or the stage-2 geometry (-257 .. -274).

## 7. The A16 monitor stays byte-identical

The header preprocesses to nothing without `SNAP_PLATFORM_SP11`, its include and the call site sit inside `#if SNAP_PLATFORM_SP11`, and the
A16 object has no access to `CNTVOFF_EL2` at all. The byte-level proof is `tools/monitor_baseline.py` (`capture` before and after, `compare`;
[BUILDING.md](BUILDING.md#reproducing-the-a16-monitor-baseline)); it needs the q1n1 checkout, which `Q1N1_SP11_TREE` can name.

## 8. Tests

| Command | What it holds | Does not show |
| --- | --- | --- |
| `python3 tools/test-sp11-timer-guard.py` | the header's own code against modelled worlds (traps stick or not, each bit alone, offset zero or not, both), an independent oracle, sixteen weakened headers and ten weakened call sites that must fail, the aarch64 instructions (two reads, one conditional write, an ISB), and with q1n1 the A16 text and object | that the traps work, or what the unit's registers read |
| `python3 tools/test-sp11-m1-profile.py` | the pinned flag set, a damaged profile refused, every refusal of the record input, the build (record exactly once, code window, reproducible from `profile-build.json`) | that the monitor runs; which record to use |
| `python3 tools/test-sp11-integration.py` | the status bands including -147 and -148, the SP11 builds with `Q1N1_SP11_TREE` | anything about hardware |
| `python3 tools/test-monitor-baseline.py` | the baseline tool and the q1n1 override | the monitor itself |

A test that lacks clang, `llvm-objdump` or a q1n1 checkout prints SKIP with the reason, and a SKIP is not a pass.

## 9. What is not established

- `CNTHCTL_EL2` bits 13 and 14 on the unit: OBSERVED once (T-HW1, T-HW2, 2026-11-16, E67): they stick and an EL1 stub's counter and virtual-timer reads trap
  with them set. Still UNKNOWN: the traps in the monitor's own guest regime (`HCR_EL2` with `VM` and `TVM`), on a secondary CPU and with the conversion; `SCR_EL3.ECVEn`
  (never read; whether the two bits depend on it is UNKNOWN too).
- Which NV2 record to embed: DECIDED 2026-11-17 (D6: the first-image record, INFERRED E2H = 1). Whether a record measured at E2H = 1 describes the E2H = 0 regime the monitor runs in: UNKNOWN.
- Whether the NV2 gate passes on the unit's boot CPU, which needs a record that equals its live MIDR, REVIDR and ID registers: UNKNOWN.
- Whether M1 starts: the audit's expected first failures still stand. Hardware: NOT RUN.
