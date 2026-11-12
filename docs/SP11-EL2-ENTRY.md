# SP11 EL2 entry: VHE host or de-VHE trampoline, timer offsets, DC ZVA

**Status: UNIMPLEMENTED target, EXPERIMENTAL host models.** Nothing here has been run on a Surface Pro 11 and
nothing is linked into any build: the A16 monitor is unchanged. The three models are pure C, compiled only under
`SNAP_PLATFORM_SP11`, and exist so that the first measurements on the unit (gates X1 and X2 of
[SURFACE-PRO-11.md](SURFACE-PRO-11.md), section 6) select a branch instead of starting a design.

| Plan todo | Decision recorded here | Label |
| --- | --- | --- |
| C1, VHE host or de-VHE entry | Recommended default: a de-VHE trampoline on every CPU, run before anything else at EL2, so q1n1 and the monitor keep the non-VHE state they have on the A16. A VHE-native monitor only if X2 reads `ID_AA64MMFR4_EL1.E2H0` other than 0, or the functional de-VHE test fails. | INFERRED decision on VERIFIED architectural rules; X1 and X2 settle it |
| C3, timer offset and ECV | The monitor must not rely on `CNTVOFF_EL2`: write it, read it back, and fail closed on a difference. Take the guest time base from `CNTPCT_EL0` minus a monitor-owned epoch. | Policy proved on a model; whether the Oryon X1 erratum applies to this unit is UNKNOWN until `MIDR_EL1` is read at X2 |
| C6, DC ZVA | Not confirmed. Close as not applicable until X2 measures it. `DCZID_EL0` = 0x14 is DZP set with a 64-byte block: a configuration state, not evidence of broken silicon. The A16 admission test `dczid != 4` conflates the two. | VERIFIED decoding; OBSERVED (one third-party patch, no symptom stated); mechanism UNKNOWN |

Contents: [1](#1-labels-sources-and-how-to-run-the-checks) labels, sources, commands; [2](#2-c1-the-el2-entry-regime)
C1; [3](#3-c3-timer-offsets-and-ecv) C3; [4](#4-c6-dc-zva) C6; [5](#5-inferred-rules-and-open-items) the list of
rules no public sentence states; [6](#6-changes-the-integrator-may-want-not-made-here).

## 1. Labels, sources and how to run the checks

### 1.1 Evidence labels

| Label | Meaning here |
| --- | --- |
| VERIFIED | Read in public text and cited by document, section or rule identifier, or by file and lines at a pinned revision. |
| OBSERVED | A third party reports it about hardware. Not reproduced here. A report about one unit is not a property of this unit. |
| INFERRED | A conservative reading. No public sentence states it outright. The model takes the strict reading and section 5 lists each one. |
| EXPERIMENTAL | A host model. Passing proves the model against its oracle, not the hardware. |
| UNKNOWN | Not established. The text names the measurement that settles it. |

### 1.2 Public sources

Arm documents are cited by number, section and rule identifier only; no Arm text is reproduced. Linux and XNU were
read for facts (field positions, instruction sequences, constants) and nothing was copied; the models and this text
are written independently.

| Source | Pin | Licence | Used for |
| --- | --- | --- | --- |
| Arm Architecture Reference Manual for A-profile, DDI 0487 release M.d | sections D8.13.1 (rules CDMXB, QXCQP, STSYY, MNWSS), D8.13.2, D8.13.3, D8.1.4 (RGGJW), D8.16.1 (BQCDZ, HXRSY), D8.17.8 (BLDZX), D8.2.12.1 (WFZPW), C5.5.4, K1.2.4, D12.1.2, D12.2.1, D7.5.10, C5.3.38 | Arm copyright; cited, not copied | E2H, TLB and synchronization rules, register redirection, counters, DC ZVA |
| Arm system register and instruction descriptions, DDI 0601 and DDI 0602 (2026-09 releases) | HCR_EL2, TCR_EL2, TTBR0_EL2, CNTHCTL_EL2, CPTR_EL2, CNTVOFF_EL2, CNTPOFF_EL2, CNTVCT_EL0, CNTPCT_EL0, CNTFRQ_EL0, ID_AA64MMFR0, 1 and 4, DCZID_EL0, ESR_EL2; the DC alias | Arm copyright; cited, not copied | Field positions, layouts, trap encodings |
| Arm PSCI specification DEN0022F.b | 6.4.3.4 (a CPU_ON target starts with stage 1 and caches off), 6.4.3.6 (firmware initializes the generic timer frequency) | Arm copyright; cited, not copied | The state a PSCI secondary arrives in |
| [slbounce](https://github.com/TravMurav/slbounce/tree/c090a8cdafa25e4c99df90f8d6f73f3805d9b397) | `c090a8cdafa25e4c99df90f8d6f73f3805d9b397`; `src/trans.s` `tb_entry` 23-43 and `tb_longjmp` 126-182; `src/bounce_main.c` 102-114 | BSD-3-Clause | What the EL2 hand-off does and leaves behind |
| [Linux](https://github.com/torvalds/linux/tree/7475ea8b58616b33c04afb976cf0fbaad9dd63fb) | `7475ea8b58616b33c04afb976cf0fbaad9dd63fb`; `arch/arm64/kernel/cpu_errata.c` 1056-1063; `arch/arm64/kvm/arch_timer.c` 1413-1442 | GPL-2.0-only (facts only) | The "Broken CNTVOFF_EL2" erratum and its workaround; absence of any DC ZVA workaround upstream |
| [XNU](https://github.com/apple-oss-distributions/xnu/tree/xnu-12377.121.6) | tag `xnu-12377.121.6` (`ac9718fb1af618d5ce8678d0dc6e8a58f252216f`); `osfmk/arm64/bzero.s` 51-89; `osfmk/arm64/loose_ends.c` 270-297 and 428; `osfmk/arm64/proc_reg.h` 164-252 and 754-759 | APSL-2.0 (read only) | How the kernel uses DC ZVA. The guest build 26A428 is not public, so for it this is INFERRED. |
| [Community SP11 EL2 report](https://github.com/ramonjesusmiranda-debug/sp11-el2/tree/8f1726d49e57de97ac0a349a89ae894206f0fc12) | `8f1726d49e57de97ac0a349a89ae894206f0fc12`; `01-oryon-dc-zva-erratum.patch` | No licence file in the repository tree; read as a pointer only, nothing copied | The one report that Oryon X1 DC ZVA is broken. OBSERVED, one unit |
| q1n1 (sibling checkout, pinned 7872398) | `docs/A16-Q1N1-PROXY.md` | project | The `HCR_EL2` value q1n1 sees on the A16 |

### 1.3 Running the checks

```
python3 tools/test-el2-entry-transition.py            # C1 model, oracle, broken variants, mutants
python3 tools/test-el2-entry-transition.py --survey   # C1: what a VHE-native monitor would break (prints, no test)
python3 tools/test-timer-offset-model.py              # C3
python3 tools/test-dc-zva-model.py                    # C6
```

Each builds its native driver under `build/` with address and undefined-behaviour sanitizers, compares the C model
with an independent Python oracle on generated cases, seeds known bugs into the model one at a time (every one must
be caught), and checks that the model is pure, MIT-licensed, under the size limits, absent from every build, and
empty when the gate is off. The C3 driver links the unmodified A16 `compat/clock.c`. They need `cc` and, for the
instruction-encoding check in C6, `llvm-mc` (that one check reports SKIP when it is missing).

## 2. C1: the EL2 entry regime

### 2.1 What arrives and what the monitor assumes

slbounce returns at EL2 with `HCR_EL2` = TGE | E2H and the MMU on, running on UEFI's page tables reinterpreted in the
EL2&0 regime. VERIFIED from `trans.s`: after its `hvc` returns in EL2 (line 25), `tb_entry` clears `SCTLR_EL2.M` (27-31)
and masks interrupts (33-34), then `tb_longjmp` invalidates the TLB, writes `HCR_EL2` = TGE | E2H (its own choice, under
the comment `ArchInitialize()`; neighbouring comments name EDK2's `AArch64Support.S` and `ArmLibSupport.S`), copies the
saved EL1 `TCR`, `TTBR0` and `MAIR` images into the EL2
registers, sets `CNTHCTL_EL2` to 3 after E2H, restores `CPACR_EL1` (which is `CPTR_EL2` once E2H is set) and turns the
MMU back on from the saved EL1 `SCTLR`. So the boot CPU passes through an MMU-off window inside slbounce itself. Why
slbounce picks E2H = 1 is INFERRED: the EL2&0 regime has the `TCR_EL1` and `TTBR0_EL1` formats (QXCQP), so firmware
images written for EL1 are valid unchanged, and EL1-style firmware code keeps working at EL2. The A16
monitor is a non-VHE EL2 host (VERIFIED: `probes/xnuboot.S`, `prepare.c`; plan decision D4 and evidence E2): it keeps the guest's state in
EL1-named registers, programs `TCR_EL2`, `CNTHCTL_EL2` and `CPTR_EL2` in their non-VHE layouts, and starts from the
`HCR_EL2` that q1n1 sees on the A16, 0x88000010 (E2H clear, TGE set; q1n1 `docs/A16-Q1N1-PROXY.md`, OBSERVED on
the A16).

Secondaries differ. A PSCI CPU_ON target starts with the MMU off (VERIFIED: DEN0022F.b 6.4.3.4), and its `E2H` is
UNKNOWN: `HCR_EL2` has an architecturally UNKNOWN value out of warm reset (VERIFIED: DDI 0601 HCR_EL2). The TLB
contents are also UNKNOWN (VERIFIED: DDI 0487 D8.16.1).

Two ways to reconcile the regimes:

* **A. de-VHE trampoline.** Turn the MMU off, clear E2H, convert the register images to their non-VHE layouts,
  invalidate the TLB, turn the MMU on. Everything after it, q1n1 and the monitor included, sees the A16's state.
* **B. VHE-native monitor.** Keep E2H set and rewrite the monitor so that its EL1-named register accesses reach the
  guest's registers through the `_EL12` aliases and its EL2 registers use the VHE layouts.

### 2.2 The model

`compat/el2-entry-model.{h,c}`. State per CPU: `HCR_EL2`, `SCTLR_EL2`, `TCR_EL2`, `TTBR0_EL2`, `TTBR1_EL2`, `MAIR_EL2`,
`CNTHCTL_EL2`, `CPTR_EL2`, `VBAR_EL2` images; a layout tag for each (the hardware reinterprets stored bits when E2H
changes, so the model remembers which layout every image was written for); which registers were written since the
last ISB; whether the TLB may disagree with the registers; the TLBI progress (issued, DSB done, complete); whether
the executing code is identity mapped; the CPU's role. Operations are `MSR`, `TLBI ALLE2`, `DSB`, `ISB` and a data
access. A sequence declares its arrival state and goal. A refusal always carries a status and a citation
(`el2m_rule_citation`).

The effective E2H of an image is 1 whatever it holds on a CPU without FEAT_E2H0 (`ID_AA64MMFR4_EL1.E2H0` other than
0; the caller sets `e2h0_implemented` from that field and anything but 0 counts as absent, so the model fails
closed). A direct read of the bit can return 0 there (VERIFIED: D8.13.1 CDMXB; DDI 0601 HCR_EL2.E2H).

| Status | What is refused | Label and source |
| --- | --- | --- |
| `ARRIVAL_STATE` | A CPU that arrived in a state the sequence does not declare it supports | INFERRED declared precondition; boot arrival VERIFIED from slbounce, PSCI arrival VERIFIED from DEN0022F.b 6.4.3.4 |
| `E2H_CHANGE_MMU_ON` | The effective E2H changing while `SCTLR_EL2.M` is 1 (the headline negative case) | INFERRED, conservative: D8.13.1 MNWSS, QXCQP, STSYY; D8.1.4 RGGJW; K1.2.4. No public sentence names this case |
| `E2H_NOT_PROGRAMMABLE` | Clearing E2H where it is RES1 | VERIFIED: D8.13.1 CDMXB; DDI 0601 HCR_EL2.E2H |
| `LAYOUT` | A `TCR_EL2`, `TTBR0_EL2`, `CNTHCTL_EL2` or `CPTR_EL2` image written in, or left in, the layout of the other E2H | VERIFIED: D8.13.1 QXCQP and the four register descriptions |
| `IMAGE_INVALID` | An image that breaks RES0 or RES1 of its declared layout (for example `TCR_EL2` without bits 31 and 23 when E2H is 0, or an ASID in `TTBR0_EL2` when E2H is 0) | VERIFIED field positions, DDI 0601 |
| `CONFIG_WRITE_MMU_ON` | `TCR`, `TTBR0`, `TTBR1` or `MAIR` written with the MMU on | INFERRED, conservative: the registers are permitted to be cached in a TLB (DDI 0601 TCR_EL2, MAIR_EL2); K1.2.4 |
| `MMU_TOGGLE_NOT_IDENTITY` | `M` changing while the executing code is not identity mapped | VERIFIED recommendation, enforced as a rule: D8.1.4 RGGJW |
| `MMU_ON_STALE_TLB` | `M` going 0 to 1 while the TLB may hold entries from another configuration | VERIFIED: D8.16.1 BQCDZ; D8.13.1 STSYY (E2H may be cached in a TLB; `TLBI ALLE2` expected); D8.17.8 BLDZX (a TLBI is complete after a DSB and a context synchronization event) |
| `MMU_ON_UNSYNC` | `M` going 0 to 1 before an ISB made the configuration writes effective | VERIFIED: K1.2.4 |
| `TLBI_UNSYNC_HCR` | `TLBI ALLE2` while an `HCR_EL2` write is not yet synchronized (strict policy only) | INFERRED, conservative: K1.2.4; C5.5.4 says `TLBI ALLE2` covers entries of both EL2 and EL2&0, so its scope does not depend on E2H |
| `DATA_ACCESS_MMU_OFF` | A load or store while the MMU is off | VERIFIED attribute (D8.2.12.1 WFZPW: stage 1 off means Device-nGnRnE); INFERRED hazard (slbounce `bounce_main.c` 102-114: the switch loses cache lines not flushed) |
| `USE_BEFORE_SYNC` | A data access before an ISB made earlier register writes effective | VERIFIED: K1.2.4 |
| `FINAL_STATE` | A sequence that ended short of its goal (E2H set, MMU off, TLBI incomplete) | model |
| `CPU_DIVERGE` | CPUs ending in different EL2 configurations | INFERRED: the monitor assumes one regime on every CPU and `probes/secondary.S` reuses the primary's `TCR`, `TTBR0` and `MAIR` |
| `BAD_OP` | A malformed operation or register index | model |

The policy has two knobs, both strict by default: `strict_tlbi_after_config` (a configuration write with the MMU off
dirties the TLB even after a clean `TLBI`) and `strict_isb_before_tlbi`. They exist only so that two sequences known
to run on hardware can be replayed (section 2.5); neither relaxation is used by the recommended sequences.

### 2.3 The canonical de-VHE sequence (data: `el2m_seq_boot_devhe`)

| # | Operation | Why |
| --- | --- | --- |
| 1 | `SCTLR_EL2` = 0 | E2H picks the regime the live MMU walks and is cached in a TLB, so it must not change under a running stage 1 (INFERRED, conservative) |
| 2 | ISB | the MMU is off before the next instruction |
| 3 | `HCR_EL2` = 0x88000010 | the state q1n1 sees on the A16 |
| 4 | ISB | `TLBI` and every register write below see the new E2H (K1.2.4) |
| 5-7 | `MAIR_EL2`, `TCR_EL2` (non-VHE image), `TTBR0_EL2` (ASID cleared) | converted from the images found on arrival |
| 8-9 | `CNTHCTL_EL2`, `CPTR_EL2` in the non-VHE layout | both layouts differ (QXCQP); a VHE image left behind would be read as the wrong controls |
| 10-13 | DSB, `TLBI ALLE2`, DSB, ISB | the TLB is complete before the MMU is on (D8.17.8 BLDZX); the A16's `secondary_entry` uses the same shape |
| 14-15 | `SCTLR_EL2` = M, C, I; ISB | |

No load or store occurs between steps 1 and 14: the sequence must run from registers and the instruction stream (the
trampoline's own constants are `movz` and `movk`, its table is already in memory). The constants in the model
(`EL2M_CANON_*`) are test data, not measurements; a real trampoline converts what it finds with
`el2m_tcr_vhe_to_nvhe`, `el2m_ttbr0_vhe_to_nvhe` and `el2m_cnthctl_convert`, which carry every field the two
layouts share and are checked field by field. The model abstracts `SCTLR_EL2` to M, C and I; the real sequence must
read-modify-write it, as slbounce does (it masks RES0 and sets RES1, `trans.s` 176-182). It also drops TTBR1-only
fields: a VHE image whose TTBR0 walks are disabled (`EPD0`) cannot be converted, and no live mapping may sit in the
TTBR1 half, which the non-VHE EL2 regime does not have (INFERRED requirement, measured at X1 as M3 below).

### 2.4 Where the de-VHE runs, and boot CPU versus PSCI secondaries

There are two placements for the boot CPU, because slbounce already turns the MMU off (section 2.1).

| Placement | Runs | Sequence | Consequences |
| --- | --- | --- | --- |
| A1, inside slbounce's SP11 restore | In `tb_entry` and `tb_longjmp`, where the MMU is already off: the saved EL1 images are converted to the non-VHE layouts and `HCR_EL2` is written without E2H | `slbounce-devhe` (`el2m_seq_slbounce_devhe`) | No E2H change ever happens under a live stage 1 (rule I1 of section 5 is never in play), and the EFI application starts at EL2 as the A16's q1n1 does. The cost: EL1-style firmware code, such as UEFI runtime services, is not expected to work at EL2 afterwards (INFERRED). R18, the `ResetSystem` check, has to be done first, and reset or power-off must use PSCI |
| A2, after slbounce returns | The first code the EFI application runs, with the MMU on and E2H set | `boot-devhe`; `universal-devhe` (same operations, any arrival state) when the state is unknown | Needs steps 1-2 (the MMU-off window), and the code must be identity mapped. Needs no change to slbounce |

Either way the live mappings must be in the TTBR0 half, and the code that is executing when the MMU comes back on must
be identity mapped under the converted tables.

| CPU | Arrives with | Sequence | Differences |
| --- | --- | --- | --- |
| Boot CPU | MMU on, E2H and TGE set (slbounce return, A2); MMU off (A1) | as above | as above |
| PSCI secondary | MMU off; E2H UNKNOWN | `secondary-devhe` (steps 3-15) | No MMU-off step. The `TLBI` is mandatory because the TLB is UNKNOWN out of reset and E2H may be cached |
| Any CPU in the wrong state | | refused: `ARRIVAL_STATE` | The boot sequence on a secondary that arrives with the MMU off, or with E2H clear, is variants 10 and 11 below |

All CPUs must finish in one configuration (`CPU_DIVERGE`, `el2m_cpus_consistent`, tested with twelve CPUs, and with an
A1 boot CPU against a canonical secondary). The model needs no knowledge of the rest of the boot: a CPU that violates
its sequence's declared precondition is refused instead of run.

### 2.5 Sequences known to run on hardware, replayed

| Reference | Result | What it shows |
| --- | --- | --- |
| slbounce `tb_entry` and `tb_longjmp`, VHE direction (`el2m_ref_slbounce_vhe`) | Accepted only with `strict_tlbi_after_config` = 0; refused `MMU_ON_STALE_TLB` under the strict policy | It does its `TLBI` before it rewrites `TCR`, `TTBR0` and `MAIR` with the MMU off. It runs on real hardware (OBSERVED, one community unit), so the strict reading "a configuration write at M = 0 dirties the TLB" is stricter than the machine. Public text says TLB contents have no effect on translation while the MMU is off (D8.16.1 HXRSY) but not that none can be created. INFERRED |
| A16 `probes/secondary.S` `secondary_entry` (`el2m_ref_a16_secondary`) | Accepted only with `strict_isb_before_tlbi` = 0; refused `TLBI_UNSYNC_HCR` when strict | It writes `HCR_EL2` and runs `TLBI` with a DSB but no ISB between. Harmless on the A16, where the write does not change E2H. On the SP11 the write may change E2H, and the missing ISB then matters (INFERRED) |
| `secondary_entry` on a CPU that arrives with E2H set | Refused `LAYOUT` even when relaxed | It never writes `CNTHCTL_EL2` or `CPTR_EL2`; `cpu_boot.c` does later. On the SP11 anything that reads them before that sees the wrong layout. A finding, not a model artifact |
| The SP11 fork of slbounce's restore (`el2m_seq_slbounce_devhe`) | Accepted under the strict policy from five arrival states. With its second `TLBI` deleted it reverts to slbounce's own order: refused when strict, accepted when relaxed. With slbounce's `HCR_EL2` value left in, refused `LAYOUT`; on a CPU without FEAT_E2H0, refused `E2H_NOT_PROGRAMMABLE` | What the fork has to do: convert, and add a `TLBI` with its barriers after the register writes. Its end state equals a PSCI secondary's |

Mutating a reference (dropping the `TLBI`, the DSB or an ISB) breaks it again, so the relaxed replays prove the
reference is understood and not that the model accepts anything.

### 2.6 The broken variants the model must reject

`tools/test-el2-entry-transition.c` runs 24 deliberately broken sequences (at least 12 are required) and checks each is
refused for its stated reason, plus a 25th case (a `TLBI` never completed).

| # | Break | Refused as |
| --- | --- | --- |
| 1 | E2H cleared with the MMU on (steps 1-2 deleted) | `E2H_CHANGE_MMU_ON` |
| 2 | `TCR_EL2` written in the VHE layout after E2H was cleared | `LAYOUT` |
| 3 | The VHE image tagged non-VHE (IPS and TG1 land in reserved bits) | `IMAGE_INVALID` |
| 4 | Non-VHE `TCR_EL2` without its RES1 bits | `IMAGE_INVALID` |
| 5 | MMU enabled before `TLBI` and its barriers | `MMU_ON_STALE_TLB` |
| 6 | `TLBI` never completed by a DSB | `MMU_ON_STALE_TLB` |
| 7 | A missing ISB before the MMU is enabled | `MMU_ON_UNSYNC` |
| 8 | A missing ISB after the `HCR_EL2` write, before `TLBI` | `TLBI_UNSYNC_HCR` |
| 9 | `TTBR0_EL2` programmed again with the MMU on | `CONFIG_WRITE_MMU_ON` |
| 10 | The boot sequence on a PSCI secondary that arrives with the MMU off | `ARRIVAL_STATE` |
| 11 | The boot sequence on a secondary that arrives with E2H clear | `ARRIVAL_STATE` |
| 12 | MMU toggled while the code is not identity mapped | `MMU_TOGGLE_NOT_IDENTITY` |
| 13 | A load or store between MMU off and on | `DATA_ACCESS_MMU_OFF` |
| 14 | `CPTR_EL2` left in the VHE layout | `LAYOUT` |
| 15 | `CNTHCTL_EL2` written in the VHE layout | `LAYOUT` |
| 16 | An ASID in `TTBR0_EL2` where it is RES0 | `IMAGE_INVALID` |
| 17 | E2H cleared on a CPU without FEAT_E2H0 | `E2H_NOT_PROGRAMMABLE` |
| 18 | `HCR_EL2` written after `TCR_EL2` | `LAYOUT` |
| 19 | An empty sequence (E2H stays set) | `FINAL_STATE` |
| 20 | No ISB after the final `SCTLR_EL2` write | `USE_BEFORE_SYNC` |
| 21 | A data access before that ISB | `USE_BEFORE_SYNC` |
| 22 | A register index that does not exist | `BAD_OP` |
| 23 | `MAIR_EL2` written with the MMU on | `CONFIG_WRITE_MMU_ON` |
| 24 | The MMU never comes back on | `FINAL_STATE` |

A Python oracle written separately from the C reproduces the verdict on 12,000 generated sequences (random deletions,
insertions, swaps, arrival states); the one verdict it cannot reach by generation, `CPU_DIVERGE`, is covered by the
driver. 18 seeded bugs in the model (a dropped check, an inverted comparison, an accepted unknown) are each caught.

### 2.7 The VHE-native option

**Redirection (VERIFIED: D8.13.2 Table D8-113, D8.13.3, and the EL0 timer tables).** With E2H set at EL2, an access
that names an EL1 register reaches the EL2 register of the host, and the `_EL12` alias reaches the real EL1 register.
The EL0 timer registers are redirected to the EL2 physical and virtual timers, with the `_EL02` aliases. `ELR` and
`SPSR` follow. Registers such as `PAR_EL1`, `MDSCR_EL1`, `TPIDR_EL1`, `CSSELR_EL1`, `CNTFRQ_EL0`, `CNTVCT_EL0` and
`CNTPCT_EL0` are not redirected. The model holds the name table (`el2m_redirect_class`) and checks an access list for a
save and restore routine (`el2m_check_accesses`):

| Status | Meaning |
| --- | --- |
| `REDIRECTED_TO_HOST` | A guest-intent access by an EL1 or EL0 name with E2H set: it would read or clobber the host's register |
| `ALIAS_UNDEFINED` | An `_EL12` or `_EL02` name with E2H clear, or on a register that has none |
| `UNORDERED_NAMES` | One register reached through two names with no ISB between them |
| `UNKNOWN_REGISTER` | A base name the table does not know: refused, never accepted |

**Layouts under E2H.** `TCR_EL2` takes the `TCR_EL1` format (IPS, TG1, T1SZ and EPD1 appear, the RES1 bits go) and
`TTBR0_EL2` gains an ASID; `CNTHCTL_EL2` moves the EL1 physical counter and timer controls from bits 0 and 1 to bits
10 and 11 and adds the EL0 controls at 0 and 1 and 8 and 9; `CPTR_EL2` becomes the `CPACR` format (VERIFIED: D8.13.1
QXCQP; DDI 0601). Under VHE an EL2 read of `CNTVCT_EL0` uses offset 0 (VERIFIED: DDI 0601 CNTVCT_EL0).

**What the A16 monitor would break** (`python3 tools/test-el2-entry-transition.py --survey`, a textual survey of the
sources; it counts accesses and does not prove a path is exercised):

| Finding | Count and place |
| --- | --- |
| EL1-named accesses to a register that is redirected under E2H, in files linked into the monitor ELF | 55 accesses to 13 registers; 72 more in standalone probes. None uses `_EL12` today |
| EL2-named accesses to registers whose layout depends on E2H, in the monitor ELF | 7 (`CNTHCTL`, `CPTR`, `SCTLR`, `TCR`, `TTBR0`); 19 more in standalone probes |
| `cntkctl_el1` written in `vcpu_arch_prepare` (`prepare.c`), at `sysreg_clock.c:50` and at `report.c:43` | Would overwrite `CNTHCTL_EL2` and lose the guest's counter and timer trap bits |
| `sctlr_el1` and `tcr_el1` read at `xnuboot.S:87-88` | Would read the host's own EL2 registers |
| `cpacr_el1` and `cptr_el2` written together at `cpu_boot.c:109` and `:200` | Two names for one register in the VHE layout; the second write wins in the wrong format (`UNORDERED_NAMES`) |
| `msr hcr_el2` at `xnuboot.S:142` writes a constant with neither E2H nor TGE | Would clear E2H under a live MMU if q1n1 hands over un-normalised: the headline negative case again |
| q1n1 itself runs at EL2 before the monitor | Not surveyed: q1n1 is a sibling repository. INFERRED to carry the same cost |

### 2.8 Decision matrix

| Criterion | A. de-VHE trampoline | B. VHE-native monitor |
| --- | --- | --- |
| Change to A16-visible code | None to the monitor ELF. Two small SP11-gated edits to `probes/secondary.S`: an ISB after the `HCR_EL2` write, and the `CNTHCTL_EL2` and `CPTR_EL2` writes (section 2.5). The conversion lives in slbounce's SP11 fork (A1, workstream B1) or as the first code after its return (A2) | The survey above: 55 accesses in 12 files, each to be re-expressed with an alias, doubled by the A16 path that must stay byte-identical |
| Architectural dependency | FEAT_E2H0 (`E2H0` == 0): E2H cannot be cleared otherwise (`E2H_NOT_PROGRAMMABLE`) | None |
| First-run risk | A1: a few register writes in code that already has the MMU off. A2: a window of about a dozen instructions with the MMU off. Either way a mistake hangs without a console (plan R3). Mitigated by the host model, the identity-map proof (M3) and markers on the framebuffer | No mode change, but every EL1-named access changes meaning at once, and a wrong one corrupts the guest's state silently |
| Firmware services after the hand-off | EL1-style firmware code, such as UEFI runtime services, is not expected to work at EL2 once E2H is clear (INFERRED from slbounce's register copying; UNKNOWN until tried). Reset and power-off go through PSCI | The services keep working under VHE (INFERRED) |
| Timer interaction (C3) | EL2 reads of `CNTVCT_EL0` apply `CNTVOFF_EL2`: needs the section 3 policy | The EL2 read uses offset 0, so the monitor clock does not depend on `CNTVOFF_EL2` at all; the A16 equality check in `boot-relocate.h` becomes meaningless for it |
| NV2 and the guest's hypervisor state | As on the A16 | Whether FEAT_NV2 has any dependence on the host's E2H is not stated in the text read: UNKNOWN. X2's NV2 test must record the regime it ran under (M8) |
| Host testability | The whole sequence, 24 broken variants and three replayed references | The register naming of a routine (`el2m_check_accesses`); behavioural equivalence cannot be modelled |
| Evidence needed | M1 to M5 | M1; a design item of its own |

### 2.9 Recommended default, and what changes it

**Recommended (INFERRED decision): de-VHE on every CPU, before anything else at EL2 runs.** The boot CPU converts inside
slbounce's restore, where the MMU is already off (A1, `slbounce-devhe`); if the SP11 slbounce fork is not accepted, as the
first code after its return (A2, `boot-devhe`, or `universal-devhe` if the arrival state is not known). Either way q1n1
and the monitor see the state they see on the A16. Each PSCI secondary runs `secondary-devhe` as the first thing it does.
Linux's own
comment says the affected Oryon X1 hardware implements both FEAT_VHE and FEAT_E2H0 (VERIFIED, read: `arch_timer.c`
1426-1429; whether this unit does is INFERRED until M1), which makes the clear architecturally available. Arm deprecates
clearing E2H and makes it RES1 on a part without FEAT_E2H0 (VERIFIED: D8.13.1 CDMXB), so A may not outlive this
silicon generation; that is a reason to keep B's survey on file, not a reason to start B now.

Decision rule, for the X2 report:

1. `E2H0` is 0 on every CPU and the functional test M5 passes on the boot CPU and on all secondaries: adopt A.
2. `E2H0` is not 0 on any CPU: E2H is RES1 there, A does not exist, and B becomes mandatory. Open a design item for
   the survey above; do not improvise it at the gate.
3. `E2H0` is 0 but M5 fails (a hang, or the redirect probe still shows redirection): stop, analyse with the model
   (which rule does the observed behaviour contradict?), and only then fall back to B.

A read-back of `HCR_EL2.E2H` = 0 is conclusive only when `E2H0` is 0 (D8.13.1 CDMXB); M5 is conclusive either way.

### 2.10 The X1 and X2 evidence that settles it

Only M2 and M3 have been measured, in part and once (the paragraph after the table); nothing else below has been run.
Gates are those of [SURFACE-PRO-11.md](SURFACE-PRO-11.md) section 6; every row that writes
a register is a hardware step that needs its own authorization.

| ID | Measurement | Gate | Expected if A works | Changes the decision |
| --- | --- | --- | --- | --- |
| M1 | `ID_AA64MMFR4_EL1.E2H0` (bits 27:24), `ID_AA64MMFR1_EL1.VH`, `ID_AA64MMFR0_EL1.ECV`, `MIDR_EL1`, on every CPU. Read only | X2 | `E2H0` = 0 | `E2H0` other than 0: rule 2 |
| M2 | The boot CPU's `HCR`, `SCTLR`, `TCR`, `TTBR0`, `TTBR1`, `MAIR`, `VBAR`, `CPTR`, `CNTHCTL`, `MDCR` at slbounce's return; for A1 also the EL1 `TCR`, `TTBR0`, `MAIR`, `SCTLR` and `CPACR` images, read before the bounce, because those are what `tb_longjmp` restores | X1 | `HCR` = TGE and E2H, `M` = 1 | An E2H of 0 at arrival: the sequence's precondition changes, use `universal-devhe` |
| M3 | Whether the code that runs the sequence (A2) or turns the MMU back on (A1), and everything it touches, is identity mapped, and no live mapping uses TTBR1 (`AT S1E2R` with `PAR_EL1` for each address; `TCR_EL2.EPD1` and `T1SZ`). Read only | X1 | PA equals VA; no TTBR1 mapping in use | A non-identity page: copy or remap the trampoline first (`MMU_TOGGLE_NOT_IDENTITY`) |
| M4 | A PSCI secondary's `HCR`, `SCTLR`, `TCR`, `MAIR`, `TTBR0`, `CNTVOFF`, `CNTFRQ` and `DCZID` on arrival, all eleven, over three cold boots. The X1 probe in the plan records only that one arrives | X1 | MMU off, E2H either | MMU on at arrival: the secondaries use the boot sequence |
| M5 | The functional de-VHE test: run `universal-devhe` between framebuffer markers; read `HCR_EL2`; then write a distinct pattern through `CNTKCTL_EL1` and read `CNTHCTL_EL2` (and compare `SCTLR_EL1` with `SCTLR_EL2`). No redirection means E2H is clear (D8.13.2). Compare the final configuration across all CPUs with `el2m_cpus_consistent` | X2 | No redirection; all twelve CPUs identical | Rule 3 |
| M6 | Per-core `CNTVOFF_EL2` behaviour (section 3.6) | X2 | see C3 | C3 policy |
| M7 | `DCZID_EL0` and a functional DC ZVA test (section 4.6) | X2 | see C6 | C6 |
| M8 | The regime (`E2H`, `TGE`) under which the NV2 functional test ran | X2 | the post-de-VHE one | If NV2 works only with the host in VHE: reopen the matrix |

**M2 and M3, answered in part (OBSERVED, one run, 2026-11-12).** q1n1's EL2 page read the boot CPU's state right after
slbounce's VHE return: `HCR_EL2` 0x488000000 (E2H, RW, TGE), `SCTLR_EL2` 0x100030C5183D (M, C, I set), `TCR_EL2`
0x480803514 (TCR_EL1 layout, T0SZ 20, 4 KiB, EPD1 set), `TTBR0_EL2` 0xA7F2E000, `TTBR1_EL2` 0xFFFFFFFFFFFFF000, `MAIR_EL2`
0xFFBB4400, `CPTR_EL2` 0x300000, `CNTHCTL_EL2` 0x3. That is the arrival `boot-devhe` assumes (MMU on, E2H = TGE = 1), and
EPD1 set means no live mapping uses TTBR1 (M3's second half); q1n1's own vectors sit inside its image, so its code is
identity mapped (INFERRED). Not measured: the EL1 images before the bounce (M2's second half), `MDCR_EL2`, `AT S1E2R`
per address (M3's first half), and whether the firmware's EL1-format stage 1 descriptors stay valid under the non-VHE
regime (bit 54 is a single XN bit there, bit 53 and `nG` are RES0). The model driver's `test_observed_sp11` replays
these images: the TCR converts to 0x80843514 and `slbounce-devhe` with them is accepted under the strict policy. That
checks this model against one unit's inputs; it is not a hardware result for the sequence.

### 2.11 What the model does not prove

It proves a sequence against the rules in 2.2, not the hardware. It does not model speculative TLB fills, the data
caches, branch prediction, or SError and interrupt delivery during the window. It abstracts `SCTLR_EL2`, and it models
nine registers: the rest of the EL2 state that q1n1 and the monitor expect (`MDCR_EL2`, `ICC_SRE_EL2`, `CNTVOFF_EL2`,
interrupt routing, the vector table) is not checked, so "the state q1n1 sees on the A16" is INFERRED to be sufficient,
not proved. The strict rules marked INFERRED may refuse a sequence that works on silicon (two such are known and
replayed); they cannot accept one that violates a VERIFIED rule.

## 3. C3: timer offsets and ECV

### 3.1 The A16's timer assumptions, stated precisely

| # | Assumption | Where | Label |
| --- | --- | --- | --- |
| T1 | The guest clock is `CNTVCT_EL0` read at EL2. With E2H clear that is the physical count minus `CNTVOFF_EL2`; with E2H set it is the physical count | `compat/boot-relocate.h` 35-37; DDI 0601 CNTVCT_EL0 | VERIFIED |
| T2 | The monitor never programs `CNTVOFF_EL2`; it relies on what firmware left | `boot-relocate.h` 36; `probes/xnuboot/context.c` 708-710 | VERIFIED |
| T3 | The CPU that runs vCPU 0 has the same incoming `CNTVOFF_EL2` and `CNTFRQ_EL0` as q1n1's CPU, and `CNTFRQ_EL0` equals the declared host rate; otherwise the relocation stops with `BOOT_RELOCATE_ERROR_CLOCK` (0x108) | `probes/xnuboot/cpu_boot.c` 182-187; `boot-relocate.h` 77 | VERIFIED |
| T4 | Secondaries are not compared: each applies its own `CNTVOFF_EL2`, and the A16 relies on firmware having left the same value everywhere | `cpu_boot.c` (no check on the secondary path) | VERIFIED by absence |
| T5 | The guest's counter and virtual timer are trapped (`CNTHCTL_EL2.EL1TVCT` and `EL1TVT`, which need FEAT_ECV) and converted from the host rate to the declared guest rate; the monitor refuses to start without a non-zero ECV field and a matching `CNTFRQ_EL0` | `probes/xnuboot/prepare.c`; plan section 4.1 | VERIFIED |
| T6 | `compat/clock.c` `clock_rate_sample` converts a count with a quotient-and-remainder formula that wraps silently above about 0.8 x 2^64 host counts at 19.2 to 24 MHz | `compat/clock.c`; tested against 128-bit arithmetic | VERIFIED |

T1 and T6 together are the hazard: a `CNTVOFF_EL2` that is junk makes `physical - offset` wrap, and the conversion
turns that into a guest clock that jumps by a huge amount.

### 3.2 What the Oryon X1 erratum changes

Linux carries "Broken CNTVOFF_EL2" for the Oryon X1 (`cpu_errata.c` 1056-1063) and works around it in KVM by writing
`CNTVOFF_EL2` = 0, trapping the guest's virtual timer and counter (FEAT_ECV is required), and applying the offset in
software (`arch_timer.c` 1413-1442; VERIFIED, read). It enables that only when FEAT_VHE, FEAT_E2H0 and ECV are all
present. **How the register is broken is not stated** (UNKNOWN): it may not hold a written value, may not apply it,
or may differ per core. Whether it applies to this unit is INFERRED until `MIDR_EL1` is read (plan E17).

What it means for the A16 assumptions: T5 already traps every guest access, so the hardware offset is not what the
guest reads. T1 to T4 are different: they make the **monitor's own clock** depend on a register that may be
unreliable, and T3 checks only that two CPUs found the same value. It cannot see a register that does not hold what
is written, a junk value that is equal on both CPUs, or CPUs in different E2H regimes. The model's tests show each
(`test_relocation_assumptions`).

### 3.3 The model and the policy it proves

`compat/timer-offset-model.{h,c}`. `tom_admit` decides from per-CPU measurements, `tom_clock` and its functions run the
clock, `tom_count_to_guest` and `tom_guest_to_host_ceil` convert.

| Status | Refused |
| --- | --- |
| `PARAMS`, `NO_CPUS` | A rate that is zero or above `UINT32_MAX` (as `clock_rate_init` requires); no CPUs, too many, or a CPU index out of range |
| `NO_ECV`, `ECV_RESERVED` | ECV level 0 while the rates differ (guest reads cannot be trapped, so the guest would read the host rate); an ECV encoding the architecture does not define |
| `FREQ` | `CNTFRQ_EL0` not the declared host rate on some CPU (firmware initializes it, VERIFIED: DEN0022F.b 6.4.3.6) |
| `E2H_MIX` | CPUs disagreeing on E2H, hence on whether an EL2 `CNTVCT_EL0` read applies the offset |
| `CNTVOFF_READBACK` | **`CNTVOFF_EL2` read back differently from what was written, on any CPU, for either time base.** The policy to prove: the monitor must not rely on it, and must fail closed |
| `CNTPOFF_READBACK`, `CNTPOFF_ABSENT` | The same for `CNTPOFF_EL2`; a result reported on a CPU whose ECV level has no such register (VERIFIED: it exists only with the ECV value 2) |
| `CNTVOFF_SKEW` | Relied-on offsets differing across CPUs by more than the policy allows (legacy base, E2H clear only) |
| `OFFSET_RANGE`, `OVERFLOW`, `NOT_MONOTONIC` | An offset above the physical count (a negative guest time); a product above 2^64 - 1; a guest count below the previous one on that CPU |

Rounding: a count converts by floor (the guest never runs ahead of real time) and a deadline by ceiling (a timer
never fires early). Both are exact wherever `compat/clock.c` does not overflow, which the driver checks against its
formulas, and an overflow is an error, not a wrap. The skew between two CPUs equals the converted offset difference.
Invariants checked: per-CPU monotonic guest time, no wrap, exact or safe rounding, bounded cross-CPU skew. Tests inject
mismatched offsets per core (twelve cores, each in turn), read-back mismatches, offsets at the 64-bit limit and rates
that overflow.

### 3.4 The `boot-relocate.h` assumptions, re-derived

| A16 assumption | On the Oryon X1 | Model-checked response |
| --- | --- | --- |
| T3, equal incoming `CNTVOFF_EL2` | Necessary for the legacy base with E2H clear, not sufficient: equal junk passes | `tom_admit` plus a sampling check; the epoch base needs no equality |
| Reading `CNTVOFF_EL2` is trustworthy | Not if the erratum applies: a stuck or ignored register | `CNTVOFF_READBACK`, per CPU, for either base |
| One E2H regime across CPUs | The A16 never checks | `E2H_MIX` |
| T6, silent wrap in the conversion | A junk offset reaches it | `OFFSET_RANGE`, `OVERFLOW` |
| T4, secondaries uncompared | Each CPU may differ | `CNTVOFF_SKEW`, checked on all twelve |

### 3.5 Recommended design

**Time base `TOM_BASE_CNTPCT_EPOCH`: the physical count at EL2 (`CNTPCT_EL0`; an EL2 read returns the bare physical
count with no offset, VERIFIED: DDI 0601 CNTPCT_EL0 and DDI 0487 D12.2.1) minus one epoch the monitor samples once on
the boot CPU.** No per-CPU register is then relied on: skew between CPUs is zero by construction, and `CNTVOFF_EL2` could
be junk without effect on the monitor. This also needs fewer assumptions than the legacy base after C1's de-VHE, which
makes EL2 reads of `CNTVCT_EL0` apply the offset. Guest reads stay trapped (T5), so the hardware offset is never what the
guest sees. The legacy base (`TOM_BASE_CNTVCT`) remains modelled as a reference and requires a verified read-back and a
skew bound. A `CNTVOFF_EL2` that does not read back what was written is a refusal under both. This is a design
(INFERRED, model checked); it needs an SP11-gated change to the monitor's clock, which this study does not make.

With and without ECV: with ECV level 1 or 2 the monitor traps and converts, and the guest sees the declared rate; with
level 0 and different rates the guest would read an unscaled counter, so the boot is refused (`NO_ECV`); with level 0
and equal rates the hardware offset is all there is and must work (`tom_guest_view`).

### 3.6 What X2 must measure (M6)

1. `MIDR_EL1` on every core, to settle whether the erratum applies.
2. On each of the 12 cores, in the regime the monitor will run (E2H clear after C1; under E2H set the offset is not
   applied to EL2 reads and a result says nothing about E2H clear): `CNTVOFF_EL2` as found; write 0, 1 and a
   non-trivial value, read each back; compare `CNTVCT_EL0` with `CNTPCT_EL0` to see whether the offset is applied;
   restore the found value. The mechanism is UNKNOWN, so the test must distinguish "not written", "not applied" and
   "differs per core".
3. `CNTFRQ_EL0` and the ECV field on every core; `CNTHCTL_EL2.ECV` and `CNTPOFF_EL2` read-back at ECV level 2.
4. A functional trap test: an EL1 stub reads `CNTVCT_EL0` and the virtual timer registers with `EL1TVCT` and `EL1TVT`
   set; the access must reach EL2, and the converted count must advance at 24/19.2 of the physical one, on the boot
   CPU and on a secondary.

## 4. C6: DC ZVA

### 4.1 What `DCZID_EL0` = 0x14 means

Bits 3:0 are BS, the log2 of the block size in words; 4 means 4 << 4 = 64 bytes. Bit 4 is DZP, "Data Zero
Prohibited". 0x14 is therefore DZP = 1 with a 64-byte block (VERIFIED: DDI 0601 DCZID_EL0). **Only BS is a property of
the silicon** (IMPLEMENTATION DEFINED). DZP is how DC ZVA is configured for the reader: it follows `HCR_EL2.TDZ` and
`SCTLR_EL1.DZE` (`SCTLR_EL2.DZE` when {E2H, TGE} is {1, 1}). Whether an EL2 reader sees the same is INFERRED: the text
says the value is governed by the current exception level and those fields, not how. The value 0x14 can therefore mean
only "DC ZVA is switched off for this reader".

The A16 monitor requires the whole register to equal 4 (the `DCZID_EL0` check in `vcpu_arch_prepare`, `prepare.c`) and reads it before it programs
`SCTLR_EL1.DZE`. Its comment says the aim is to qualify the 64-byte physical geometry, which is BS; the test also
demands DZP = 0. On the SP11 that can refuse a working instruction, because it tests a configuration bit as if it were
a capability. Recorded as a finding; the fix is in section 6.

### 4.2 How XNU uses DC ZVA

XNU's public source (tag `xnu-12377.121.6`): `bzero` issues `dc zva` in a loop at a hard-coded 64-byte stride for
lengths of 128 bytes and more (`bzero.s` 65-89), and `bzero_phys_page` issues four `dc zva` per iteration, at
consecutive multiples of `1 << MMU_CLINE` (= 64 bytes), over a page (`loose_ends.c` 270-297; `proc_reg.h` 164-252).
Neither reads `DCZID_EL0`; `memset` uses plain stores. XNU sets `SCTLR_EL1.DZE` itself (`proc_reg.h` 754-759) and avoids
`dc zva` only for Device memory (`loose_ends.c` 428). VERIFIED, read; for the guest build 26A428 it is INFERRED. No
public source was found for the user-space zeroing loops. The only evidence of user-space use is the comment in
`prepare.c` (the comment beside the `DCZID_EL0` check in `vcpu_arch_prepare`), which says the libsystem_platform zeroing loop issues 64-byte DC ZVA (OBSERVED by the
A16 project, not re-read here).

Consequences, both checked by the model's driver on an emulated `dc zva`:

* A block size other than 64 corrupts memory (128 overwrites a neighbour, 32 leaves gaps), which is why the model
  requires BS = 4.
* A guest policy that merely disables DC ZVA does not work. `SCTLR_EL1.DZE` governs EL0 only; XNU's own EL1 `dc zva`
  executes regardless and only `HCR_EL2.TDZ` (or the FEAT_FGT control `HFGITR_EL2.DCZVA`, if FGT is implemented, which
  is UNKNOWN) stops it. VERIFIED: DDI 0487 C5.3.38 (EL1: trap only on TDZ or the FGT bit; EL0: DZE first, then TDZ).
  Stock XNU cannot be told to avoid the instruction without an in-memory kernel patch (KERNEL-PATCHES.md).

### 4.3 What is established and what is not

| Claim | Label |
| --- | --- |
| 0x14 is DZP = 1, BS = 4 | VERIFIED |
| DZP is a configuration, not a capability | VERIFIED |
| XNU executes `dc zva` unconditionally at a 64-byte stride | VERIFIED at the public tag; INFERRED for 26A428 |
| Oryon X1 DC ZVA is functionally broken | OBSERVED in one third-party Linux patch for one unit ("X1 CL5 erratum", added to the SP11 EL2 Linux kernel). It makes Linux never execute DC ZVA and emulates it for user space, and states no symptom. Mechanism UNKNOWN. Upstream Linux at the pinned commit has no such workaround |
| That patch is consistent with 0x14 being its own doing | INFERRED: it clears `SCTLR_EL1.DZE`, which makes DZP read 1. The value 0x14 quoted as OBSERVED in the C6 todo has no source in the pinned third-party tree: the patch and the README contain no register value |
| The instruction is broken on this unit | UNKNOWN until M7 |

### 4.4 Decision

C6 is **not confirmed: close as not applicable**, with the minimal model kept so that a positive X2 result has a
ready branch. The branch is decided by two X2 results:

| Raw `DCZID_EL0` (BS) | Functional test | Decision | Model status |
| --- | --- | --- | --- |
| BS other than 4, or reserved bits set | any | Refuse: XNU's stride would corrupt memory | `BLOCK_SIZE`, `RESERVED_BITS` |
| 4 | not run | Refuse: measure first | `UNTESTED` |
| 4 | works | Grant natively: `SCTLR_EL1.DZE` = 1, `TDZ` = 0. C6 closes | `OK`, mode native |
| 4 | broken | Emulate (4.5), or stop if emulation is not enabled | `OK` with emulation, else `BROKEN_NO_EMULATION` |

### 4.5 If X2 confirms the instruction is broken: what the model checks

`HCR_EL2.TDZ` = 1 and `SCTLR_EL1.DZE` = 1, so that an EL0 execution reaches EL2 (with DZE clear it would reach EL1)
and EL1 executions trap as well. At the trap (`ESR_EL2`, EC 0x18, IL = 1) the ISS holds op0 = 1, op1 = 3, CRn = 7,
CRm = 4, op2 = 1, `Rt` and direction 0, matching the instruction word 0xd50b7420 with `Rt` in bits 4:0 (VERIFIED: DDI
0601 ESR_EL2; DDI 0602 DC alias; cross-checked against `llvm-mc` in the test). The decode refuses every other system
instruction (`NOT_DC_ZVA`: `DC GVA`, `DC GZVA`, `DC CIVAC`, any `MSR`), a read direction (`READ`), `IL` = 0 (`IL`), a
non-system trap (`NOT_SYSTEM_TRAP`) and set ISS bits above 21 (`ISS_RES0`); every single-bit corruption of a valid ESR
is checked. The zeroing takes any address inside the block (no alignment is required; D7.5.10, C5.3.38), clears
exactly the naturally aligned 64 bytes with guard bytes either side checked, and refuses a block outside the window
the guest may write (`RANGE`) or Device memory, where the architecture gives an Alignment fault and not a zeroing
(`DEVICE`). The monitor would inject the guest's fault instead of zeroing.

The cost is a trap per 64 bytes: a 4 KiB page clear is 64 of them. That is a fallback, not a design to prefer. A null
or malformed argument to any of these functions is refused as `PARAMS`, never defaulted.

### 4.6 What X2 must measure (M7)

1. Raw `DCZID_EL0` on every core at EL2, with `HCR_EL2.TDZ`, `SCTLR_EL2.DZE`, `SCTLR_EL1.DZE`, E2H and TGE logged next
   to it. A bare 0x14 means nothing without them. Then read it at EL1 from a stub with TDZ clear and DZE set (expect
   0x04 if the instruction is configured on) and with TDZ set (expect 0x14).
2. At EL2, where DC ZVA is never trapped: fill 256 bytes of cacheable memory with a pattern, run `dc zva` at an
   unaligned address inside, and check that exactly the aligned 64 bytes are zero and the neighbours intact; repeat at
   several alignments, with the line dirty in the issuing core's cache, absent from it, and dirty in another core's
   cache. A "broken" instruction is unspecified, so each of those is a distinct failure mode to look for.
3. The same at EL1 with TDZ clear.

## 5. INFERRED rules and open items

Each is encoded conservatively and fails closed. None is a VERIFIED architectural statement.

| # | Item | Where |
| --- | --- | --- |
| I1 | E2H must not change under a live MMU | `E2H_CHANGE_MMU_ON` |
| I2 | TCR, TTBR and MAIR are not written with the MMU on | `CONFIG_WRITE_MMU_ON` |
| I3 | An ISB between an `HCR_EL2` write and `TLBI ALLE2` | `TLBI_UNSYNC_HCR` (relaxable) |
| I4 | A configuration write with the MMU off dirties the TLB | `strict_tlbi_after_config` (relaxable) |
| I5 | A data access with the MMU off is a hazard | `DATA_ACCESS_MMU_OFF` |
| I6 | One EL2 regime on every CPU | `CPU_DIVERGE` |
| I7 | No live mapping in the TTBR1 half at the trampoline | conversion refuses; M3 measures |
| I8 | `SCTLR_EL2` is modelled as M, C and I; the real code preserves RES1 as slbounce does | 2.3 |
| I9 | What an EL2 reader sees in `DCZID_EL0.DZP` | `dzv_dzp` |
| I10 | The Oryon X1 CNTVOFF erratum applies to this unit | 3.2 |
| I11 | The public XNU tag is the behaviour of build 26A428 | 4.2 |
| I12 | NV2 has no dependence on the host's E2H | M8 |
| I13 | The model's constants (`0x88000010`, `0x80843510`, `0x6000`, `0x22ff`) are test data, not measurements | 2.3 |
| I14 | Why slbounce chooses E2H = 1, and that EL1-style firmware code (UEFI runtime services) stops working at EL2 once E2H is clear | 2.1, 2.4 |

## 6. Changes the integrator may want, not made here

All must be SP11-gated and leave the A16 byte-identical (the monitor-baseline firewall decides).

* `probes/secondary.S`: an ISB between the `HCR_EL2` write and `TLBI`; the `CNTHCTL_EL2` and `CPTR_EL2` writes, in the
  non-VHE layout, before the MMU is enabled.
* `probes/xnuboot/prepare.c`, the `DCZID_EL0` check in `vcpu_arch_prepare`: admit on BS = 4 and on a functional result, not on equality of the whole register
  with 4, and read it only after the monitor has set `DZE` and `TDZ` itself.
* The monitor's guest clock: the `TOM_BASE_CNTPCT_EPOCH` base of section 3.5.
* slbounce's SP11 fork (workstream B1): the conversion, built from `el2m_seq_slbounce_devhe` (A1) or the trampoline
  `el2m_seq_boot_devhe` (A2).
