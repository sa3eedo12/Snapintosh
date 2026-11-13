<!-- SPDX-License-Identifier: MIT -->
# SP11 q1n1 boot study: entry, chainload and console on Hamoa (WS-B item B1)

**Status: this design is now implemented and host-verified (2026-11-12), still untried on a Surface Pro 11.**
[sa3eedo12/slbounce](https://github.com/sa3eedo12/slbounce) branch `sa3eedo12-sp11-boot` and
[sa3eedo12/q1n1](https://github.com/sa3eedo12/q1n1) branch `sa3eedo12-sp11-boot` carry it as real, default-off,
host-tested code (31/31 and 14/14 tests respectively, independently re-verified from fresh checkouts; see
[SURFACE-PRO-11.md](SURFACE-PRO-11.md)'s "Status of B1"). Nothing has run on a Surface Pro 11, and no code here is
linked into any default build. It answers WS-B item B1 of
[SURFACE-PRO-11.md](SURFACE-PRO-11.md) as far as public sources, the pinned q1n1 tree and a host can: which parts of
q1n1's A16 entry/chainload/console path are A16-specific, firmware-coupled or generic; a design for q1n1's SP11 boot
flow that treats every real-hardware fact [SP11-X2-TESTS.md](SP11-X2-TESTS.md) and
[SURFACE-PRO-11.md](SURFACE-PRO-11.md) section 6.2/6.3 record this week as a hard constraint; a principled (not
`#if 0`) design for slbounce's `ExitBootServices` cache sweep; where early EL2 normalization runs, reconciled against
[SP11-EL2-ENTRY.md](SP11-EL2-ENTRY.md) section 2.4; the MMIO the flow must resolve before `ExitBootServices`; a
framebuffer console design; a chainload design and what extended native tests should cover; and what a host model can
and cannot prove when the thing under test (slbounce, Secure-Launch) cannot run under QEMU at all. B1's own acceptance
criteria, quoted from [SURFACE-PRO-11.md](SURFACE-PRO-11.md) section 7 (WS-B): "slbounce at the pinned commit with the
`tcblaunch.exe` build that X1 shows slbounce accepts; an SP11 board config; early EL2 normalization per the VHE
decision; the MMIO needed after ExitBootServices mapped beforehand (as `usb-ebs-prep.c` does for the A16); a
framebuffer console; chainload; extended native tests. QEMU cannot model slbounce, so the trampoline can be checked
only against a host model, which is not hardware evidence."

B2 and B3 (the USB host link) are [SP11-HOST-LINK-STUDY.md](SP11-HOST-LINK-STUDY.md)'s subject, not this document's;
section 7 below only states where this design must leave the machine for that study's own `q1n1_usb_ports_init()` to
take over. A small, host-tested, illustrative tool accompanies this study (section 4.6); it is a model of a proposed
slbounce-side change, not a patch to slbounce or to q1n1, and it has not run on any hardware.

Contents: [1](#1-scope-labels-sources-and-how-to-run-the-checks) scope, labels, sources;
[2](#2-q1n1s-a16-entrychainloadconsole-inventory) the A16 inventory;
[3](#3-hard-constraints-this-design-treats-as-fixed) the hard constraints;
[4](#4-the-dcache-sweep-hang-a-principled-scoped-fix) the dcache-sweep fix;
[5](#5-boot-sequence-design-el2-appears-only-inside-slbounces-own-hook) the boot sequence;
[6](#6-early-el2-normalization-placement) EL2 normalization placement;
[7](#7-mmio-to-resolve-before-exitbootservices) pre-EBS MMIO;
[8](#8-framebuffer-console-design) the framebuffer console;
[9](#9-chainload-design-and-extended-native-tests) chainload and tests;
[10](#10-what-a-host-model-can-and-cannot-prove) host-model limits;
[11](#11-evidence-summary) evidence summary; [12](#12-open-questions) open questions;
[13](#13-where-this-study-disagrees-with-or-sharpens-the-background-brief) discrepancies found while researching this.

## 1. Scope, labels, sources and how to run the checks

### 1.1 Scope

This document covers q1n1's **entry, chainload and console path** only: what happens from the moment slbounce has
already been loaded and has hooked `BS->ExitBootServices` (an operator step, unchanged from X1/X2:
[SURFACE-PRO-11.md](SURFACE-PRO-11.md) section 6.2, steps 1-2) to the point a stage is running and can hand off to
[SP11-HOST-LINK-STUDY.md](SP11-HOST-LINK-STUDY.md)'s USB work. It does not re-derive C1/C3/C6 (the EL2 entry regime,
timer offsets, `DC ZVA`: [SP11-EL2-ENTRY.md](SP11-EL2-ENTRY.md)) or B2/B3 (the USB host link and persistent log:
[SP11-HOST-LINK-STUDY.md](SP11-HOST-LINK-STUDY.md)); it reconciles with both where B1 touches them (sections 6 and 7).

### 1.2 Evidence labels

Identical to the other SP11 studies: **VERIFIED** (read in a pinned source, cited by file and line, or checked by a
host test that ran here); **OBSERVED** (recorded by this project or a third party as seen on real hardware, not
re-measured here); **INFERRED** (reasoned from the above, not itself checked); **EXPERIMENTAL** (a host model or a
QEMU build; passing proves the model against its own rules, not the hardware); **UNIMPLEMENTED** (no code exists);
**UNKNOWN** (not established; the measurement that would settle it is named).

### 1.3 Pinned sources

| Source | Pin | Licence | Used for |
| --- | --- | --- | --- |
| [q1n1](https://github.com/Snapintosh/q1n1/tree/7872398694ca8322e9cdebcc4ca2380491295f24) | `7872398694ca8322e9cdebcc4ca2380491295f24` | project | `platform/uefi/{main,entry,boot-window,fbcon,usb-ebs,usb-ebs-prep,gicv3}.c/.h/.S`, `tools/build-slbounce.sh`, `tools/test-uefi.py` |
| [sa3eedo12/q1n1](https://github.com/sa3eedo12/q1n1) branch `sa3eedo12-sp11-host-link` | `4ab6d8e` (board-info carrier) then `4a21503` (USB host-link gate), 2 commits on the pinned commit above; not yet merged to `Snapintosh/q1n1` main | project | `docs/BOARD-INFO.md`, `platform/uefi/sp11-usb-ports.{h,c}`, the `SP11=1 SP11_USB_TABLE_C=...` `platform/uefi/Makefile` gate: the established `SNAP_PLATFORM_SP11` pattern this study's own illustrative code and every proposal below follow |
| [slbounce](https://github.com/TravMurav/slbounce/tree/c090a8cdafa25e4c99df90f8d6f73f3805d9b397) | `c090a8cdafa25e4c99df90f8d6f73f3805d9b397` (the exact commit `tools/build-slbounce.sh` builds, confirmed by reading that script; matches the pin already recorded in [SP11-EL2-ENTRY.md](SP11-EL2-ENTRY.md) section 1.2) | BSD-3-Clause | `src/{bounce_main.c,trans.s,arch.c,sl.c}`, `README.md`, `theory_of_operation.md`, read directly from a clone at this exact commit, not from the project's `master` branch |
| [TravMurav/slbounce issue #13](https://github.com/TravMurav/slbounce/issues/13) | read 2026-11-12 | public issue tracker | A third-party report on a *different* unit (section 4.3); read in full, not from a paraphrase |
| [SURFACE-PRO-11.md](SURFACE-PRO-11.md), [SP11-EL2-ENTRY.md](SP11-EL2-ENTRY.md), [SP11-X2-TESTS.md](SP11-X2-TESTS.md), [SP11-HOST-LINK-STUDY.md](SP11-HOST-LINK-STUDY.md) | this repository | project | the hard constraints of section 3; the EL2-entry placement reconciled in section 6 |
| `probes/uefi/{probe_main,fbtext}.c` | this repository, pinned commit of this checkout | project | the GOP two-tier selection this study asks q1n1 to adopt (section 8) |

`tools/build-slbounce.sh` already passes `SLBOUNCE_ALWAYS_SWITCH=1 DEBUG=1` to slbounce's `make` (VERIFIED, read: the
script). This is load-bearing, not incidental (section 5.1): without it, `sl_is_allowed_by_fdt()`
(`bounce_main.c:30-67`) looks for an `EFI_DTB_TABLE_GUID` configuration table and a `qcom,adreno`/`zap-shader` node
inside it (`bounce_main.c:42-65`), and a pure-ACPI platform such as Surface Pro 11 publishes no such table at all, so
that lookup fails closed to `EFI_UNSUPPORTED` and `sl_ExitBootServices` (`bounce_main.c:98-99`) would call the real
`ExitBootServices` directly, with no bounce at all. Nothing in this study changes that flag; it records that it is
already correctly set.

### 1.4 Running the checks

```sh
python3 tools/test-sp11-ebs-sweep-model.py        # section 4.6: the dcache-sweep model, its oracle and mutants
python3 tools/sp11_ebs_sweep_model.py demo         # a worked example (q1n1's footprint vs. a firmware range shaped
                                                   #  like the one TravMurav/slbounce#13 reports as fatal)
```

Both are pure Python (no q1n1 or slbounce build needed), write nothing, and take under a second.

## 2. q1n1's A16 entry/chainload/console inventory

### 2.1 The A16 flow in four steps (not the USB path, already covered by B2/B3)

`efi_main` (`main.c:1255`) reads `before_el = current_el()` (`:1260`), finds the console framebuffer and an ACPI SPCR
UART (`find_framebuffer`/`find_uart`, `:1261-1262`), then dispatches on the image's own command-line option
(`:1267`). The `--boot`/`--uart-proxy` path (`proxy_efi_main`, `:1135`) does boot-services-only work (the A16 USB boot
window, heap/stage/GPU-arena allocation, the SSD preload check, the GPT survey: `:1137-1253`), then calls
`leave_firmware` (`:1104`), which gets the final memory map and calls `st->boot->exit_boot_services` (`:1121`); on
success it jumps to `q1n1_enter` (`entry.S:52-69`), which installs `q1n1_vectors` at `VBAR_EL2` or `VBAR_EL1`
according to a **fresh** `CurrentEL` read taken right there (`entry.S:60-65`, not `before_el`), then calls
`q1n1_main` (`main.c:1071`). `q1n1_main` calls `proxy_main` (`:1074`) whenever `mode != MODE_FOOTHOLD`, i.e.
unconditionally for the real `--boot`/`--uart-proxy` path; `proxy_main` (`:916`) re-reads `current_el()` again
(`:918`), prints a cosmetic EL2/EL1 line (`:957`), then runs ACPI/SMBIOS/memory-map reporting, USB-port or UART-proxy
setup, the GPU DMA-exclusion setup (`gpu_setup`, `:671-684`), GIC bring-up (`q1n1_gic_init`/`q1n1_gic_start`,
`:1012-1024`) and an unconditional, infinite proxy-run/chainload loop (`:1034-1068`, never returns in normal
operation). A separate `--el2` diagnostic dispatch (`:1270-1276`, `leave_firmware` called straight from `efi_main`)
exists purely to answer "did this platform reach EL2", and is where the only existing slbounce-aware text in q1n1
already lives (section 2.3).

### 2.2 Inventory

Classes as in [SP11-HOST-LINK-STUDY.md](SP11-HOST-LINK-STUDY.md) section 2: **A16-SPECIFIC** (a number or path that
is the A16's), **FIRMWARE-COUPLED** (needs the ASUS BIOS312 image or its drivers), **GENERIC** (carries over, caveat
stated). IDs are local to this document (`F` for flow-specific rows, `G` for generic).

| ID | Class | Where | What it is | Consequence for SP11 |
| --- | --- | --- | --- | --- |
| F01 | A16-SPECIFIC | `boot-window.c:1-33`, `boot-window.c` throughout | The entire boot-window UI rides the firmware's USB Function driver and an FNV-fingerprinted BIOS312 `UsbConfigDxe` image (`usb-role-change.c:13`, already I14 in the host-link study) to offer "proxy / proxy once / windows / shell" over a USB-CDC serial port before any chainload happens | No equivalent exists on Surface firmware (UNKNOWN); B1 must replace this UI, or accept not having one until B2's link exists (section 9.1) |
| F02 | A16-SPECIFIC | `main.c:1205` (`stage_candidates[]`) | Fixed stage load addresses `0xb0000000`, `0x4c000000`, `0xa0000000`, chosen to fit the A16 memory map | The SP11 DRAM map is UNKNOWN until X2 item 8 runs; a board-config table (section 2.4) must supply SP11-specific candidates, never reuse these blindly |
| F03 | A16-SPECIFIC | `main.c:1182-1183` (`info.usb_dwc3 = USB_EBS_DWC3_BASE; info.usb_qscratch = USB_EBS_QSCRATCH_BASE;`) | bootinfo carries the A16 USB takeover bases | Already superseded for SP11 by the host-link study's descriptor-driven table (`sp11-usb-ports.h`); B1 need not touch it |
| F04 | FIRMWARE-COUPLED | `main.c:1154-1186` (the `usb_mode` branch of `proxy_efi_main`) | The whole dispatch assumes the A16 boot window ran and left a DWC3 snapshot variable behind | Does not apply to SP11 at all until B2 exists; B1's flow must have a path that skips it cleanly (`MODE_UART_PROXY`-shaped, or a new no-op mode, section 9.1) |
| F05 | GENERIC | `main.c:124` (`current_el()`) | A bare `mrs CurrentEL` read, legal at any EL | Reusable unchanged |
| F06 | GENERIC | `entry.S:35-43` (the `Q1N1_STAGE` path), `entry.S:60-65` (`q1n1_enter`) | Vector-table installation picks `VBAR_EL2` or `VBAR_EL1` from a **fresh** `CurrentEL` read at the moment `q1n1_enter`/a stage starts, not from any value captured earlier | Reusable unchanged; this is already correctly EL-agnostic and already matches the "EL2 may not exist until this exact instant" reality slbounce creates (section 5) |
| F07 | GENERIC | `main.c:843-913` (`q1n1_exception`) | The synchronous/IRQ/SError handler reads `ESR_EL2`/`ESR_EL1` etc. by branching on a fresh `current_el() == 2` check (`:845`), so it works whichever EL actually faulted | Reusable unchanged. It is also why a fault at EL1 (section 2.3) does not silently corrupt state: it reaches `q1n1_fault` (`:813-839`) and halts with ESR/ELR/FAR on screen, rather than continuing on broken assumptions |
| F08 | A16-SPECIFIC / INFERRED absent | nowhere in `main.c`, `proxy_efi_main`, or `leave_firmware` | No EL2-register access occurs anywhere **before** `leave_firmware`'s own `exit_boot_services` call (VERIFIED by reading every line of `efi_main` and `proxy_efi_main`, `main.c:1135-1282`; confirmed independently by a regex search of the tree for `_el2` registers outside `gicv3.c`, `q1n1-proxy.c` and the post-EBS parts of `main.c`) | This is the single most important fact for B1: q1n1's **existing** `--boot` shape already has the right contract for a platform where EL2 does not exist until inside the hook (section 5.2). Nothing here needs to change to accommodate that; what needs to change is what happens **right after** the call (F09) |
| F09 | A16-SPECIFIC assumption, present but latent | `main.c:957` (the EL2/EL1 message), `main.c:1012` (`q1n1_gic_init`) | `proxy_main` prints a **cosmetic warning** ("WARNING - running at EL1, not EL2") if `current_el() != 2` after `leave_firmware`'s EBS call, but does **not** stop: it proceeds unconditionally, 55 lines later, into `q1n1_gic_init`, which executes `mrs/msr icc_sre_el2` (`gicv3.c:156-157`) and `msr cnthp_*_el2` (`gicv3.c:170-171`, `:190`) | This is the "assumption baked into q1n1's own A16 boot flow" the brief asked this study to find. It has never fired on the A16 (that platform reaches EL2 with no bounce, INFERRED from F08 plus the fact that no A16 run has ever shown the warning, OBSERVED by absence in every A16 doc read for this study), so it is latent, not previously discovered. On SP11, if slbounce's SMC sequence fails or the cache-sweep hang (section 4) recurs after a partial "fix", this path would reach EL1 execution of an EL2-only system-register instruction, trapping as an architecturally UNDEFINED access from EL1 (INFERRED: general AArch64 behaviour for an encoding restricted to EL2 or above, not re-derived from a fresh Arm ARM citation in this pass; see section 13). F07 means this *is* caught (not a silent hang) and ends on the existing `q1n1_fault` halt page with ESR/ELR/FAR -- but that is a slower, less informative failure than an explicit, immediate refusal would be, and it burns part of the unexplained post-bounce time budget (section 3) on a fault round-trip instead of reporting cleanly. Section 5.3 proposes the fix: replace the warning with a fail-closed refusal, under `SNAP_PLATFORM_SP11` only |
| F10 | A16-SPECIFIC, but already generic in shape | `main.c:357-379` (`find_uart`), `main.c:366` (`info.acpi_rsdp = ...`) | Pre-EBS ACPI RSDP/XSDT discovery, with RSDP and XSDT checksum/length validation, looking specifically for SPCR; caches `info.acpi_rsdp` for later, reused post-EBS at `main.c:994`/`:1045` | The RSDP-discovery *mechanism* is generic (any ACPI platform); only the SPCR-specific inner loop is A16-tuned (it was written to find the one UART the A16 exposes this way). q1n1 already does the right thing by caching the address pre-EBS rather than re-walking `st->tables` post-EBS (section 7.2), even though re-walking would also work (the config-table array survives EBS) |
| F11 | A16-SPECIFIC, naive | `main.c:387-403` (`find_framebuffer`) | A single `st->boot->locate_protocol(&gop_guid, NULL, (void **)&gop)` call: whichever GOP instance the firmware happens to return first, no console-handle check, no UEFI-memory-map cross-check, no ambiguity handling | Section 8 recommends replacing this, under the SP11 gate only, with the two-tier, memory-map-validated selection `probes/uefi/probe_main.c:289-337`/`probes/uefi/fbtext.c` already uses and that the passive probe already proved under the UEFI Shell (`SEL=SAME`, [SURFACE-PRO-11.md](SURFACE-PRO-11.md) section 6.2) |
| F12 | GENERIC | `fbcon.c` (q1n1), whole file | A small, independently authored framebuffer-text module, deliberately kept separate from `main.c`'s own console so "the physically tested EL2 payload source is unchanged" (`fbcon.c:2-4`, its own header comment) | Already the right pattern for anything that must stay provably untouched; section 8 keeps this split |
| F13 | A16-SPECIFIC pattern, reusable | `usb-ebs-prep.c` / `usb-ebs.c` / `usb-ebs.h`, whole files | A two-image, two-step pattern: capture and validate state **while boot services are still available** (`usb-ebs-prep.c`, a boot-services-only build), store a magic/size/FNV-checksummed snapshot in a volatile `EFI_VARIABLE_BOOTSERVICE_ACCESS` variable (`usb-ebs.h:46-82`), then a second image reloads and re-validates that snapshot against **live** register state before trusting it (`usb-ebs.c:167-184`, `load_snapshot`) | This is the pattern section 7 generalizes for B1: capture what can only be discovered pre-EBS, validate it again post-EBS before relying on it, refuse rather than guess. The specific USB/firmware-driver mechanics are A16-only (FIRMWARE-COUPLED); the *shape* is generic |
| F14 | GENERIC | `main.c:671-684` (`gpu_setup`, the `ranges[7][2]` array) | q1n1's own complete, already-existing self-accounting of what it has allocated since boot: heap, DMA arena, stage region, its own image, the memory-map buffer, the preload table, the framebuffer | Built for a different purpose (excluding q1n1's own memory from a GPU DMA claim), but it is **exactly** the "payload-declared ranges" list section 4's scoped sweep design needs; no new enumeration logic is required, only a new consumer of the same list |
| F15 | GENERIC (chainload mechanism), A16-SPECIFIC (addresses) | `main.c:1034-1068` (the chainload call site), `entry.S:1-48` (`Q1N1_STAGE` flat-binary entry), `main.c:1292-1349` (`q1n1_stage_main`) | A chainloaded stage is a flat binary at a fixed, pre-reserved address (F02), entered with `x0` = inherited bootinfo, sets up its own stack/BSS/vectors and calls `q1n1_stage_main`, which re-derives the UART base from ACPI if needed (`:1318-1332`) since it is not carried in bootinfo | The mechanism (flat entry, bootinfo inheritance, GLINK-session adoption) is generic; only the stage *addresses* (F02) are A16-specific. Section 9.1 elaborates |
| F16 | FIRMWARE-COUPLED, already slbounce-aware | `main.c:1270-1276` | The `--el2` diagnostic's own printed guidance ("At EL1: qualify sltest before using slbounce. Use `SLBOUNCE_ALWAYS_SWITCH=1` for this ACPI target, then run `q1n1.efi --el2`") | VERIFIED by reading it; it predates this Surface Pro 11 effort (introduced by commit `5642cfd`, "q1n1: Snapdragon X2 Elite (ASUS A16) port", 2026-10-21, the very first A16 port commit, alongside `tools/build-slbounce.sh`). q1n1's author already anticipated that *some* AArch64 UEFI target needing diagnosis might require slbounce; this text is for a human operator running three separate images by hand (`sltest`, `slbounce`, `q1n1.efi --el2`), not an in-flow integration, and it is not reached by the production `--boot` path at all (F08) |
| G01 | GENERIC | `q1n1-bootinfo.h` whole file | The bootinfo ABI (`system_table`, `runtime_services`, `config_tables`, `acpi_rsdp`, `fb_*`, `heap_*`, `stage_*`, `mode`, `entry_el`, ...) | Carries over as-is; it already has an `entry_el` field (F10) and already has no A16-specific field this study's needs do not already use |
| G02 | GENERIC | `main.c:1104-1131` (`leave_firmware`) | `GetMemoryMap` then `ExitBootServices`, retried up to 3 times on `EFI_INVALID_PARAMETER` (a map-key race), watchdog disabled first, no logging/allocation/console calls inside the retry loop | Reusable unchanged. This is precisely the call site slbounce's hook intercepts (section 5.1); nothing about it needs to know slbounce exists |
| G03 | GENERIC | `main.c:813-839` (`q1n1_fault`) | A framebuffer-only fault page with ESR/ELR/FAR, entered from `q1n1_exception` | Reusable unchanged; this is the backstop F09's current warning-not-refusal gap relies on today, and the reason a latent EL1-vs-EL2 mistake has not previously caused a silent hang |
| G04 | GENERIC | `tools/test-uefi.py:1-9` (module docstring) | "QEMU boots the real EFI binary... QEMU validates the UEFI target, not Qualcomm Secure Launch or the GENI driver" | q1n1's own existing test-harness convention already states, in its own words, exactly what section 10 restates in more detail for slbounce specifically |

### 2.3 What this inventory does not need to re-derive

B2/B3's own inventory (I01-I24, G01-G14, N01-N02 of [SP11-HOST-LINK-STUDY.md](SP11-HOST-LINK-STUDY.md) section 2) already
covers every USB-path row; this document does not repeat them. The only overlap is F03/F04 above, noted because they
sit in the same dispatch function B1's own flow must also pass through or bypass.

## 3. Hard constraints this design treats as fixed

Each of these is OBSERVED on the real Surface Pro 11 unit this week (2026-11-11), cited to
[SP11-X2-TESTS.md](SP11-X2-TESTS.md) and [SURFACE-PRO-11.md](SURFACE-PRO-11.md) section 6.2/6.3. This design does not
revisit them; it is built to fit them.

1. **Build 28144's `tcblaunch.exe` reaches EL2 under slbounce.** Confirmed via `SLTEST.EFI`'s green line, then the
   full `SLBOUNCE.EFI` + payload path (section 6.2). This is the pinned `tcblaunch.exe` build B1's own acceptance
   criterion names.
2. **The blanket `clear_dcache_range()` sweep in slbounce's `ExitBootServices` hook hangs this unit's firmware**,
   matching the general shape of a public report on a different Surface model (section 4.3). The only fix tried so
   far is a test-only, never-upstreamed, never-pinned `#if 0` of the whole sweep. Section 4 is this study's answer.
3. **EL2 is reached only during/after the firmware's own `ExitBootServices` call returns, inside slbounce's hook
   itself, never before it.** `preebs_currentel` decoded to EL1 in all three real hardware dumps taken this week.
   Section 5 is built around this exactly, including the mechanical reason (section 5.1).
4. **`ResetSystem()` does not work after the bounce** (R18 answered: no, OBSERVED). Any design that relies on a
   software-requested reset to recover from a stuck state is wrong for this platform; PSCI `SYSTEM_RESET` is the only
   OBSERVED-working path (used by q1n1's own `usb-ebs.c:38-42` already, which falls back to a raw `smc` after
   `ResetSystem` returns).
5. **The board resets itself a short, fixed, unconfirmed time after a certain point post-bounce** (OBSERVED: past
   T01/T02, during T03). No SBSA Generic Watchdog, no WDAT table, no DSDT watchdog device was found; the mechanism is
   UNKNOWN. This design treats "a durably useful console state must be reached as fast as possible" as a real,
   binding requirement (section 5.5), not a nice-to-have.
6. **The GOP-selection fix (tier-2 `SEL=SAME`)** is proven on hardware in the passive probe under the UEFI Shell
   (section 8.2); this design reuses it rather than inventing a new rule.
7. **The device facts of [Appendix A](SURFACE-PRO-11.md#appendix-a-hamoa-and-surface-pro-11-constants)** (GICD/GICR
   `0x17000000`/`0x17080000`, PSCI conduit SMC, GICv4.1, PARange 44-bit, VMIDBits 16, NV2 confirmed usable) are taken
   as given context, not re-derived here; none of them is an input to this document's own designs.

## 4. The dcache-sweep hang: a principled, scoped fix

### 4.1 What the sweep is for

`sl_ExitBootServices` (`bounce_main.c:94-153`) is slbounce's hook for `BS->ExitBootServices`. Before calling the real
`ExitBootServices`, it walks the last memory map captured by its own `GetMemoryMap` hook (`sl_GetMemoryMap`,
`bounce_main.c:79-92`; this is whatever map the **payload itself** last fetched, typically right before its own
`ExitBootServices` call, per the standard UEFI two-call convention `main.c:1117-1121`/`:1104-1131` already follows)
and, for every descriptor of type `EfiLoaderCode`, `EfiLoaderData`, `EfiBootServicesCode`, `EfiBootServicesData`,
`EfiRuntimeServicesCode`, `EfiRuntimeServicesData` or `EfiACPIReclaimMemory` (exactly seven types,
`bounce_main.c:124-130`; **not** `EfiConventionalMemory`, which can be the largest part of the map by far, and not
`EfiReservedMemoryType`/`EfiACPIMemoryNVS`/`EfiMemoryMappedIO`), calls `clear_dcache_range(start, size)` over the
descriptor's **full extent** (`:131`). The code's own comment explains why (`:101-115`, VERIFIED, read verbatim):
switching to EL2 "will corrupt the caches", the loader may have written memory the hook cannot individually identify,
so it defensively flushes every descriptor of a type a loader is likely to own, accepting this is "suboptimal". The
comment also states the opposite hazard: flushing hyp-owned memory also crashes, which is why `EfiReservedMemoryType`
is excluded and why AUTH runs only after this sweep. `clear_dcache_range` itself (`arch.c:18-33`) loops `dc civac`
once per cache line (from `CTR_EL0.DminLine`) over the whole range, with a single `dsb ish; isb` **after the entire
loop**, not per descriptor and not per chunk (VERIFIED, read).

Crucially, **slbounce's own code already individually flushes the buffers it specifically knows about**: the
Secure-Launch parameter block (`sl.c:127`, one page), and the `tcblaunch.exe` PE image, the argument buffer and the
boot-parameters block (`sl.c:313-315`, each its own `clear_dcache_range` call sized to the exact allocation), plus a
168-byte flush of its own `tb_jmp_buf` right before the LAUNCH SMC (`bounce_main.c:146`). The blanket sweep is
**additional, defensive coverage for an arbitrary, unknown loader** (Linux's EFI stub, systemd-boot, GRUB) that
slbounce cannot enumerate; it is not protecting anything slbounce already tracks by name.

### 4.2 Why "just delete it" is wrong, and what it is actually protecting for us

q1n1, unlike an arbitrary Linux bootloader, is not unknown to itself: at the point it is about to call
`ExitBootServices`, it already has a complete, explicit list of every range it has written since boot (F14,
`main.c:671-684`: its own loaded image, its heap, its DMA arena, its stage slots, the memory-map buffer it just
fetched, the preload table, the GPU arena). Simply removing the sweep (the test-only fix used so far) throws away the
one thing it is legitimately for: **our own writes to Loader/BootServicesData memory surviving the cache-incoherent
jump to EL2.** If the sweep is gone entirely and any of those writes is still dirty only in a cache line at the
moment the SMC AUTH/LAUNCH sequence runs, a reader at EL2 (or whatever intermediate stage inspects memory during
AUTH) could see stale bytes. slbounce's own individually-flushed buffers (tcblaunch.exe's image, the argument blob)
are unaffected either way, since those are flushed regardless of whether the blanket sweep runs (`sl.c:313-315`, not
touched by this study's proposal). What is at risk from outright removal is specifically **q1n1's own writes**, which
the blanket sweep happened to cover as a side effect of walking Loader/BootServicesData generally.

### 4.3 What is actually known about why it hangs

**On this unit (OBSERVED, [SURFACE-PRO-11.md](SURFACE-PRO-11.md) section 6.5, row X1):** the sweep hangs; the exact
address or descriptor involved was not captured (no diagnostic ran before the hang). **Matches the general shape of**
[TravMurav/slbounce issue #13](https://github.com/TravMurav/slbounce/issues/13) -- but that issue, read in full for
this study (not from a paraphrase; see section 13), is primarily about something unrelated (no ITS/LPI delivery at
EL2 on a *Microsoft Surface Pro 12*, SoC **X1P42100**, a different and newer SoC from our X1E80100). Its one relevant
paragraph is a trailing "unrelated note" (quoted here because it is short, precise and directly load-bearing for this
design; the issue is public and BSD-3-Clause/CC-licensed discussion, not reproduced source code):

> "The blanket cache sweep in `sl_ExitBootServices()`... is fatal on this firmware: it takes the machine down inside
> `clear_dcache_range()` on an ordinary `EfiBootServicesData` range (`0xA7F19000`, 231 pages), before AUTH has even
> run. Removing it entirely has no ill effect here -- the buffers that matter are flushed individually, and `sltest`
> never had the sweep at all while reaching EL2 fine on this machine."

This is OBSERVED, third-party, on a **different unit and a different SoC**, not our Surface Pro 11 -- it is corroborating
evidence that the blanket sweep is a recurring, platform-class problem on Microsoft Surface/Pluton firmware generally,
not proof of the same root cause on our unit. Two things in that quote matter for this design:

- **The size is small** (231 pages, 0x0E7000 bytes, well under 1 MiB): the hang is very unlikely to be a matter of
  total bytes swept taking too long. A size-only explanation ("too much to flush in time") does not fit a report this
  small. **INFERRED, not confirmed on our unit.**
- **It is "an ordinary `EfiBootServicesData` range"**: nothing marks it as unusual in the memory map's `Type` field.
  The architecturally plausible explanation this study proposes (INFERRED, not confirmed by any register dump on
  either unit) is that `clear_dcache_range`'s `dc civac` loop is being executed over a physical range whose actual
  page-table memory attribute is **not** write-back cacheable, despite its UEFI `Type` looking like ordinary loader
  memory -- for example firmware-internal scratch, a DMA-coherent buffer, or glue state for the Secure-Launch
  transition itself, mapped Device or non-cacheable regardless of what `Type` reports. `clear_dcache_range` never
  reads `MemoryEntry->Attribute` (`bounce_main.c:117-134`, confirmed by reading every line of the loop): **the field
  that would let it tell cacheable memory from merely-the-right-`Type`-but-not-cacheable memory is ignored.** Cache
  maintenance instructions on Device memory are, at minimum, pointless (nothing to flush) and are the kind of
  operation that varies most by implementation when misapplied -- the single most concrete, checkable difference
  between "this will hang" and "this is safe" that this study can identify without a register dump from our own unit.
- **What remains genuinely UNKNOWN**: whether our unit's hang shares this exact mechanism, a different address, or a
  different cause (size, ordering, something else about the SMC path). No diagnostic captured the faulting address on
  our unit. This design does not claim to have found *our* root cause; it designs a replacement that removes the
  class of risk the third-party report demonstrates is real on related hardware, while preserving the sweep's stated
  purpose for exactly the memory we ourselves might dirty.

### 4.4 The proposed design: scoped, attribute-filtered, chunked

Replace the blanket walk-every-matching-descriptor sweep, **under a Snapintosh SP11 fork of slbounce only** (never
touching the generic/upstream behaviour other slbounce users rely on), with:

1. **Payload-declared ranges.** q1n1, built with `SNAP_PLATFORM_SP11=1`, exposes the exact list `gpu_setup()` already
   builds (F14) to the hook, through a small, versioned, checksummed contract analogous to `usb-ebs.h`'s existing
   snapshot struct (section 7.1) -- a fixed, bounded number of (base, size) pairs, not a re-walk of the whole memory
   map.
2. **Containment, not blind extent.** Each declared range must sit wholly inside exactly one memory-map descriptor of
   one of the seven upstream-swept types (preserving the existing, already-reasoned type list unchanged: this design
   does not second-guess which types a loader might dirty, only which *bytes within them* are provably ours).
3. **The attribute check upstream never makes.** The covering descriptor must also advertise `EFI_MEMORY_WB` (bit
   index 3 of `Attribute`). A descriptor that matches by `Type` alone but is not write-back is refused, not swept --
   this is the one-line check this study believes would have prevented the third-party unit's hang, applied
   conservatively here since our own unit's faulting address is unknown.
4. **Bounded chunks, not one unbroken loop.** Every emitted flush operation is capped at a fixed byte size (not a
   measurement of this hang's cause: an INFERRED, conservative default reusing the project's own existing "never
   submit a single `CP_MEMCPY` over 1 MiB" rule, [AGENTS.md](../AGENTS.md), for the same reason -- an unmeasured but
   deliberately bounded unit of hardware-adjacent work), each able to carry its own synchronization point if a future
   fork wants one, rather than slbounce's current single `dsb ish; isb` after every matching descriptor in the whole
   map.
5. **Fail closed.** A declared range that is not fully contained in one matching, write-back descriptor is refused
   outright (an explicit error, not a silently-widened or silently-dropped sweep) -- consistent with every other
   SP11-gated refusal in this project ([AGENTS.md](../AGENTS.md): "fail closed... never return fabricated success").
6. **The non-SP11 path is untouched.** With the gate off (or on any other slbounce user's build), `sl_ExitBootServices`
   keeps its exact current behaviour, including for loaders this design has no visibility into. This design adds a
   scoped alternative; it does not claim the general-loader problem slbounce's comment describes is solved for anyone
   else.

This still needs a Snapintosh fork of slbounce to exist (the scoped sweep is `bounce_main.c`-side code, and B1's own
acceptance criterion already names "slbounce at the pinned commit" as something this project ships its own build
of). That fork is not a new cost this design introduces: one is already implied by the hang itself, since the only
fix tried so far (`#if 0`) is itself an unpinned, uncommitted edit to a local slbounce checkout. This design asks for
the same fork to carry a principled change instead of a blanket deletion.

### 4.5 Should this go upstream?

Yes, and this study recommends it as a build-time option rather than a permanent, un-upstreamed local patch. slbounce
already demonstrates exactly this shape of extensibility point: `SLBOUNCE_ALWAYS_SWITCH` is a `#ifdef`-gated
compile-time override of a different piece of default behaviour (`bounce_main.c:38-40`), and `tools/build-slbounce.sh`
already opts into it. A `SLBOUNCE_SCOPED_SWEEP`-shaped option (or an equivalent runtime hook the payload installs
before calling `ExitBootServices`, so no slbounce-side rebuild is needed per payload) would let any payload that can
enumerate its own footprint opt into the narrower sweep, while every other slbounce user keeps today's behaviour
unconditionally. The third-party report (section 4.3) independently suggests the upstream maintainer would welcome
this: they removed the sweep locally and wrote "Happy to open a separate issue if that is useful" about exactly this
problem. This design does not open that issue or write that patch (out of scope for this pass, and it would need the
maintainer's own review of a mechanism neither this study's author nor the coordinator has run on hardware); it
records the concrete, testable shape a proposal could take, grounded in the pinned source actually read for this
study rather than in the unpinned `#if 0` variant.

### 4.6 The illustrative host model

`tools/sp11_ebs_sweep_model.py` (and its test, `tools/test-sp11-ebs-sweep-model.py`) is a pure-Python, host-only model
of sections 4.1 and 4.4: `classic_sweep()` reproduces `bounce_main.c:117-134` exactly (an oracle this study can check
its own understanding against, not a claim about what the real firmware does); `scoped_sweep()` implements the
containment/attribute/chunk-cap design above and raises `SweepRefused` wherever section 4.4's rules say to refuse. 23
tests cover: every upstream type included/excluded exactly as `bounce_main.c` does; the attribute field genuinely
ignored by `classic_sweep` (matching upstream, not a design flaw of the model); coverage, strict boundedness (scoped
is always `<=` classic's write-back-only total, and strictly less whenever anything is excluded), the attribute
refusal, the chunk-cap enforcement, five distinct fail-closed refusal cases (malformed range, cross-descriptor-boundary
range, non-swept type, non-write-back type, an undeclared range entirely outside the map), and an equivalence test
proving that declaring every write-back swept-type byte reproduces exactly classic's write-back-only coverage (the
quantitative claim that this is a strict narrowing, not a different policy). Six seeded mutations (dropping the
attribute filter, accepting more than one covering descriptor, dropping the non-swept-type refusal, accepting a
non-positive chunk cap, widening `classic_sweep` past the seven real types, and an off-by-one in the chunk splitter)
are each confirmed caught. `python3 tools/sp11_ebs_sweep_model.py demo` runs a worked example: a synthetic memory map
with q1n1's own image and heap next to a descriptor shaped exactly like the one TravMurav/slbounce#13 reports as
fatal (`0xA7F19000`, 231 pages); `classic_sweep` includes it, `scoped_sweep` does not, because it was never declared.

**What this proves and does not prove** (see also section 10): it proves the *containment, attribute-filtering and
chunking logic* behaves as designed against synthetic inputs, including the exact shape of the one third-party report
available. It does not prove this is why our own unit hangs (UNKNOWN, section 4.3), does not implement slbounce's
actual hook (no q1n1 or slbounce source is modified by this patch), and cannot be run against a real UEFI memory map
without hardware. It is offered, as the brief invited, to ground the design numerically rather than only in prose --
not as evidence this fix has been built, applied, or tried.

## 5. Boot sequence design: EL2 appears only inside slbounce's own hook

### 5.1 The mechanical reality, read from the real source

`sl_install` (`bounce_main.c:155-192`) replaces `BS->ExitBootServices` with `sl_ExitBootServices` and `BS->GetMemoryMap`
with `sl_GetMemoryMap`, then returns -- at this point execution is still ordinary EL1 UEFI code, nothing has moved.
When the **payload** (whoever calls `BS->ExitBootServices`, unmodified by slbounce) makes that call, it reaches
`sl_ExitBootServices` instead. After the sweep (section 4) and the real `ExitBootServices` (`bounce_main.c:136-138`),
it calls `SL_CMD_AUTH` over SMC (`:140-142`, rebooting via PSCI on failure: `psci_reboot`, `arch.c:88-94`), then
**`tb_setjmp(tb_jmp_buf)`** (`:144-150`), which is a classic C `setjmp`: it saves the EL1 callee-saved registers,
stack pointer and several EL1 system-register images (`trans.s:86-122`) and returns 0 the first time. The code then
flushes that save buffer and issues `SL_CMD_LAUNCH`, handing control to `tcblaunch.exe`. By design
([theory_of_operation.md](https://github.com/TravMurav/slbounce), read for this study), `tcblaunch.exe` is made to
suffer an intentional early failure, using its own "transition back" feature to call `tb_entry` (`trans.s:22-44`) with
a pointer to a `tb_data` struct. `tb_entry` runs an `hvc 0x1` that "returns in EL2" (`trans.s:25`, its own comment),
clears `SCTLR_EL2.M` (`:28-31`), masks interrupts, and calls `tb_longjmp(jmp_buf_ptr, 1)` (`:40-42`). **`tb_longjmp`
itself now executes at EL2** (`trans.s:130`, its own comment: "the registers are restored in EL2"): it issues `tlbi
alle2` *before* writing any register (`:149-151`, confirmed independently by reading the source -- matching
[SP11-EL2-ENTRY.md](SP11-EL2-ENTRY.md) section 2.5's own, separately derived finding about this exact sequence),
writes `HCR_EL2 = TGE|E2H` (`:160-161`), `CNTHCTL_EL2` (`:164-165`), the EL1-saved `cpacr_el1`/`ttbr0_el1`/`tcr_el1`/
`mair_el1` images reinterpreted at their EL2 register names (`:168-171`), a `dsb ish; isb` (`:173-174`), then the
EL1-saved `sctlr_el1` image, RES0/RES1-masked, written to `sctlr_el2` (`:176-182`) -- **turning the MMU back on at
EL2**. It then restores the EL1 callee-saved registers and stack pointer **that `tb_setjmp` saved back inside
`sl_ExitBootServices`'s own stack frame**, and does `br x30` (`:198-200`): execution resumes **inside the same C
function, at the same point, on the same stack** -- but now at EL2, because the processor mode changed underneath it
mid-call. `sl_ExitBootServices` then returns normally, handing control back to whatever called
`BS->ExitBootServices()` in the first place.

**The consequence for B1's design:** EL2 does not merely "appear after" the payload's `ExitBootServices()` call
returns; it appears **mid-way through that one C function's own execution**, invisibly, between two statements of a
function the payload never sees. From the payload's point of view there is exactly one way to observe this: read
`CurrentEL` again, freshly, the instant the call returns -- never trust any EL value read before the call.

### 5.2 q1n1's existing `--boot` shape already fits this

F08 (section 2.2) already established that nothing in `efi_main`/`proxy_efi_main` touches an EL2-only register before
`leave_firmware`'s own `exit_boot_services` call. That call *is* the one slbounce's hook intercepts (G02). So the
production chain already has the right shape: **no code runs that could care which EL it is, until after the one call
that might change it.** Nothing in this design needs to restructure `proxy_efi_main`'s pre-EBS work (the boot window,
allocations, preload, GPT survey) to accommodate slbounce -- it is already EL-agnostic, because it is boot-services
work, not hardware-register work.

What *is* missing is a fail-closed check immediately after that call returns, before the first EL2-only register
access (F09). Today's behaviour (a cosmetic warning, then an unconditional `q1n1_gic_init` call 55 lines later) is a
latent gap this study found by reading the code, not something observed to fail on hardware yet (it has never fired,
since the A16 never takes this path, INFERRED from F08/F16). On SP11 it is a real risk: any slbounce failure mode
that leaves the platform at EL1 (a failed AUTH, a LAUNCH that reboots instead of transitioning, or -- until section
4's fix lands and is proven -- a recurrence of the cache-sweep hang that somehow does not actually hang but also does
not transition) would reach this gap.

### 5.3 The fix: a fail-closed refusal, `SNAP_PLATFORM_SP11`-gated

Immediately after `leave_firmware`'s `exit_boot_services` call returns (i.e. the first instruction of `q1n1_enter`/
`q1n1_main` under the gate), read `CurrentEL` fresh (as `entry.S` already does for vector installation, F06) and, if
it is not 2, **do not call `proxy_main` at all**: draw a framebuffer-only page (reusing the existing fault-page
mechanism, G03, which already needs no boot-services call) stating plainly that EL2 was not reached, and halt (no
`ResetSystem` -- constraint 4 of section 3 -- and no automatic PSCI reset either, since an automatic retry loop into
the same failing bounce is not obviously safer than a diagnosable halt; this mirrors the X2 test image's own decision
to halt rather than reset on an unrestorable state, [SURFACE-PRO-11.md](SURFACE-PRO-11.md) section 6.3). This is
precisely the same shape as the X2 test image's own, already-proven fix for the mirror-image problem found this week
(`tests_hook_post_ebs`'s explicit phase-ordering fallback, [SP11-X2-TESTS.md](SP11-X2-TESTS.md), "A phase-ordering bug,
found and fixed") -- an explicit, early, informative check instead of relying on a general-purpose exception handler
to catch the consequence several steps later. Gated under `SNAP_PLATFORM_SP11` only; the A16 path (F09 today) is
unchanged, since F09 has never actually failed there (INFERRED: F08 and F16 together).

### 5.4 Phase model

Reusing the same three-phase shape [SP11-X2-TESTS.md](SP11-X2-TESTS.md) converged on after finding its own
phase-ordering bug (constraint 3 of section 3), restated for q1n1's own flow rather than the test image's:

| Phase | Bounded by | What may run | What must never run here |
| --- | --- | --- | --- |
| **Pre-hook** | `efi_main` entry to the `exit_boot_services` call inside `leave_firmware` | Everything `proxy_efi_main` already does today (F08): boot-services calls, allocation, the pre-EBS MMIO capture of section 7, the board-config load of section 6 | Any EL2-only register access (there is no guarantee EL2 exists yet, and on SP11 it provably does not, constraint 3) |
| **Inside the hook (opaque)** | The single `exit_boot_services` call itself | Nothing -- q1n1 has no code here at all; this is entirely slbounce's own `sl_ExitBootServices`/`tb_entry`/`tb_longjmp` (section 5.1) | N/A: q1n1 does not run during this phase by construction |
| **Post-hook, EL unconfirmed** | The instant the call returns, before any other instruction | Exactly one thing: read `CurrentEL` fresh and branch (section 5.3) | Anything else, including `proxy_main`'s own diagnostic prints (today's `main.c:957`, which runs *after* several lines of other work) |
| **Post-hook, EL2 confirmed** | After the section 5.3 check passes | Early EL2 normalization (section 6), then everything `proxy_main` does today, reordered per section 5.5 | — |

### 5.5 Fast-to-useful ordering, given the unexplained post-bounce reset risk

Constraint 5 (section 3) means this design cannot assume ample time exists after EL2 is confirmed. The X2 test image's
own second finding -- discovering that T03 stalls and the board resets a short, fixed time after T01, and fixing it
by moving T02 (the test that actually answers K1) ahead of T03 rather than investigating the stall further first --
is the direct precedent this design follows, not a coincidence to note in passing. Applied to q1n1's own flow, the
ordering `proxy_main` uses today (ACPI/SMBIOS/memmap text, USB or UART setup, GPU arena setup, GIC bring-up, *then*
the proxy loop, `main.c:916-1069`) should be reordered, under the gate, so that:

1. The section 5.3 EL2 check (unavoidably first).
2. Early EL2 normalization (section 6), since every later step's correctness depends on the monitor seeing the
   expected non-VHE regime.
3. **The framebuffer console comes up and shows something durable** (section 8) -- a build id, the entry/current EL,
   and whatever of the diagnostic text in `proxy_main` is cheapest to produce -- *before* GIC bring-up or any
   USB-link attempt, so that if the unexplained reset recurs, whatever got drawn survives as a photographable record,
   the same way T01's and T02's results survived being drawn before T03's reset on this unit this week.
4. GIC bring-up, then the USB-link handoff to [SP11-HOST-LINK-STUDY.md](SP11-HOST-LINK-STUDY.md)'s own code.

This is a reordering of q1n1's own existing steps, not new functionality; nothing here is UNIMPLEMENTED that was not
already UNIMPLEMENTED before this study. It is marked EXPERIMENTAL and UNTRIED: the actual reset budget is UNKNOWN
(constraint 5), so "framebuffer before GIC" is a reasoned precaution, not a measured sufficient fix.

## 6. Early EL2 normalization placement

[SP11-EL2-ENTRY.md](SP11-EL2-ENTRY.md) section 2.4 already names two placements and already recommends one
(section 2.9): **A1**, inside slbounce's own SP11-forked restore, where the MMU is already off; or **A2**, as the
first code q1n1 runs after slbounce returns, with the MMU on and E2H set, needing its own brief MMU-off window. That
document's recommendation is **A1 primary, A2 as the fallback if the slbounce fork is not accepted** -- this study
agrees with that ordering and, having now read slbounce's actual pinned source (section 5.1), can make the
integration concrete rather than abstract, and can correct one premise behind treating A1 as the *costlier* option.

**The premise worth re-examining:** A1 was framed as needing "the SP11 fork of slbounce" as a cost particular to that
placement. Section 4 of this study establishes that **a Snapintosh fork of slbounce is needed regardless of which
placement is chosen**, to carry the scoped cache-sweep fix (section 4.4) -- that fix lives in `bounce_main.c`, a file
slbounce itself owns, and there is no way to avoid forking it if the sweep hang is to be fixed in a principled way
rather than by an unpinned local `#if 0`. Once that fork exists, A1's own cost is only the marginal one of also
editing `trans.s`, a file in the *same* already-forked repository, built by the *same* already-modified
`tools/build-slbounce.sh`. A2's claimed advantage ("no change to slbounce") no longer holds once section 4 is adopted.

**Where, precisely, in the real `tb_longjmp` A1 would run** (VERIFIED by reading `trans.s:132-200`, not previously
available to the sister document, which correctly described the *shape* of the sequence from its own earlier reading
of the same lines but without this placement-level detail): the canonical de-VHE sequence's steps 1-13 of
[SP11-EL2-ENTRY.md](SP11-EL2-ENTRY.md) section 2.3 (MMU off, `HCR_EL2` without E2H, converted `MAIR`/`TCR`/`TTBR0` in
non-VHE layout, `TLBI ALLE2` with its barriers) map onto `trans.s:149-174` almost line for line: the `TLBI
alle2`/`dsb ish`/`tlbi alle2` already there (`:149-151`) is the model's step 10-13 (section 2.3), already in the
*first* position the sister document's own replay of this exact sequence found necessary to relax
(`strict_tlbi_after_config = 0`, section 2.5) to accept as-is. The natural insertion point is to **replace**
`trans.s:160-171` (the VHE-shaped `HCR_EL2`/`CNTHCTL_EL2`/`cpacr_el1`→`CPTR_EL2`/`ttbr0_el2`/`tcr_el2`/`mair_el2`
writes) with the model's converted, non-VHE-layout versions of the same EL1-saved images, leaving the TLBI-first
ordering and the final `sctlr_el2`-turns-MMU-on step (`:176-182`) structurally unchanged. This is now a concrete,
file-and-line-level target for whoever implements C1's model as real code (not this pass: `compat/el2-entry-model.c`
is a pure model with no caller yet, [SP11-EL2-ENTRY.md](SP11-EL2-ENTRY.md) sections 2.2/2.9, and wiring it into an
actual `trans.s` edit is its own, separately-authorized, hardware-risky step this study does not take).

**Recommendation (unchanged from [SP11-EL2-ENTRY.md](SP11-EL2-ENTRY.md) section 2.9, now with a concrete target and
one premise corrected):** A1, inside the same already-necessary Snapintosh fork of slbounce's `trans.s`, replacing
`trans.s:160-171`'s VHE-shaped writes with the converted non-VHE images; A2 (first code after slbounce returns, inside
q1n1's own SP11-gated flow, immediately after the section 5.3 EL2 check and before anything else in section 5.4's
"EL2 confirmed" phase) remains the fallback if A1's slbounce-side change is not accepted or not yet built. Both
remain X1/X2-measurement-gated per [SP11-EL2-ENTRY.md](SP11-EL2-ENTRY.md) section 2.10's M1-M5 (`E2H0`, the hand-off
registers, the identity map, a PSCI secondary's arrival state, the functional de-VHE test); nothing in this study
changes that decision rule, and no normalization code is written in this pass (`compat/el2-entry-model.c` already
exists and is unmodified; this section only places where its eventual real implementation would run).

## 7. MMIO to resolve before `ExitBootServices`

### 7.1 What the `usb-ebs-prep.c` pattern actually is

Read literally, `usb-ebs-prep.c` performs no page-table operation at all (`grep`-confirmed: no `ArmConfigureMmu`,
no page-table manipulation anywhere in the file); its `mmio()` helper (`:12`) is a bare `*(volatile uint32_t
*)(uintptr_t)address` dereference, identical in spirit to every other raw MMIO access in this project. **What the
pattern actually provides is not a page-table mapping; it is a boot-services-only capture-and-validate step, handed
across the `ExitBootServices` boundary through a magic/size/FNV-checksummed structure (`usb_ebs_snapshot`,
`usb-ebs.h:46-55`), re-validated against live register state before being trusted (`usb-ebs.c:167-184`,
`load_snapshot`'s own `REFUSED: live DWC3 ID differs from the prep snapshot` check).** This document's title for B1's
acceptance criterion ("the MMIO needed after ExitBootServices mapped beforehand") is read here as: *whatever
addresses or register-derived facts can only be discovered or validated while boot services are still available must
be captured and validated before `ExitBootServices`, because no boot-services call can be made afterward* -- not as
a claim that AArch64 UEFI page tables need new identity-map entries added. Whether the existing UEFI identity map
already covers every physical range this flow reads post-EBS, or whether some range is unmapped and would fault, is
itself UNKNOWN for this platform (X2's own device-window policy already treats this as an open question for GIC/PCIe/
SMMU, [SURFACE-PRO-11.md](SURFACE-PRO-11.md) section 6.3; this document does not re-open that for the narrower set of
MMIO B1 itself needs, listed below, since none of it is new relative to what X1/X2 already read successfully).

### 7.2 What B1 needs pre-EBS

| Need | Already captured pre-EBS today? | Action |
| --- | --- | --- |
| GOP mode info (base, stride, format, size) for the framebuffer console | Partially: `find_framebuffer` (F11) calls `locate_protocol` once, with none of the validation section 8 proposes | Replace with the hardened, memory-map-validated, two-tier selection of section 8, still entirely pre-EBS (boot-services calls: `locate_handle`/`handle_protocol`, exactly as the passive probe already does) |
| ACPI RSDP/XSDT pointer, for later GIC/SPCR/board-info use | **Yes already** (F10, `find_uart`, `main.c:357-379`); cached into `info.acpi_rsdp` and reused post-EBS without re-walking `st->tables` | None needed; this is already the right pattern and this study found no gap here |
| The final UEFI memory map, for the scoped cache-sweep's declared-range handoff (section 4.4) | **Yes already**, as a side effect of `leave_firmware`'s own `get_memory_map` call (`main.c:1117`) *and* of slbounce's own `GetMemoryMap` hook (section 4.1) -- the same call site both already need | Only a new field in the bootinfo-adjacent handoff structure that carries the declared-range list itself (F14's data, not new MMIO) |
| q1n1's own heap/stage/image/DMA footprint, to populate the declared-range list | **Yes already** (F14, `gpu_setup`, `main.c:671-684`) | Expose the same array to the (forked) slbounce hook; no new discovery code |
| A board descriptor identifying this as the SP11 (B1's own "an SP11 board config" criterion) | No board-descriptor consumer exists in q1n1 today (confirmed: no file in `platform/uefi/` reads `config/boards/*.json`-shaped data); the host-link branch's board-info carrier (`docs/BOARD-INFO.md`) places an opaque blob in q1n1's heap but nothing in q1n1 parses it | A pre-EBS step that places (or validates an already-placed) board-info blob is the natural fit, reusing the already-landed placement contract (section 1.3); **this document does not add that consumer code**, since the brief asks for a design, and the host-link branch's own "Monitor wiring... UNIMPLEMENTED" note already states the next concrete step honestly |

### 7.3 What B1 explicitly does not need pre-EBS

**GIC.** `ICC_SRE_EL2`, `CNTHP_*_EL2` (`gicv3.c:156-171`) are EL2-only registers by construction: there is no way to
read or validate them before EL2 exists, and on this platform EL2 does not exist until inside the hook (constraint 3).
Any reading of B1's acceptance criterion that implies "map the GIC before EBS" is not meaningful here, and this study
explicitly does not attempt it; GIC bring-up stays where q1n1 already puts it, post-hook, reordered per section 5.5.
**PCIe/SMMU/NVMe.** Out of this document's scope (X2's own device-window policy already covers them,
[SURFACE-PRO-11.md](SURFACE-PRO-11.md) section 6.3); B1's entry/chainload/console path does not touch any of them.

## 8. Framebuffer console design

### 8.1 Two existing modules, two different jobs

q1n1 already has two framebuffer-text implementations (F12): `console.c`'s `con_*` family, used by `proxy_main` for
the real production boot log (scrolling, an emblem/logo, a live status panel, `main.c:916-1069`), and `fbcon.c`'s
simpler `fbcon_*` family, used by the small, single-purpose `usb-ebs`/`usb-ebs-prep` boot-services-only test images
(section 2.2, F13), deliberately kept separate so "the physically tested EL2 payload source is unchanged"
(`fbcon.c:2-4`). This design keeps that separation: the production SP11 flow should use `console.c`'s existing `con_*`
family (it is the module that already has the richness `proxy_main` needs -- scrolling, field display, a status
panel -- and it already renders the diagnostic text section 5.5 asks to appear first), not `fbcon.c`, which stays
exactly what it is today: the minimal module a small, self-contained boot-services-only test image reaches for. What
this study ports from the GOP-selection work proven on the passive probe (constraint 6, section 3) is **the
selection algorithm**, not a wholesale replacement of either console module.

### 8.2 The GOP-selection fix to port

`find_framebuffer` (F11, `main.c:387-403`) takes whichever GOP `locate_protocol` returns first, with no check that it
is the console's own GOP, no check that the firmware's memory map actually describes that range as something safe
to draw into, and no handling at all if multiple GOP instances describe different framebuffers. Snapintosh's own
passive probe already solved exactly this problem, on this exact hardware, under the exact UEFI Shell environment B1
must also run in (the staged boot order is `SLTEST.EFI`, then `SLBOUNCE.EFI`, then the payload, all from the Shell,
[SURFACE-PRO-11.md](SURFACE-PRO-11.md) section 6.2): `gather_gop` (`probes/uefi/probe_main.c:289-337`) enumerates
every handle implementing the GOP protocol (`locate_handle(EFI_LOCATE_BY_PROTOCOL, ...)`, `:298`), tags each one
`is_console` by direct pointer comparison against `st->console_out_handle` (`:312`), and hands the list to
`probe_gop_select` (`fbtext.c:126-188`), which:

1. **Tier 1 ("CONSOLE"):** picks the first candidate that is both the console's own handle and passes
   `probe_gop_check` (valid geometry, and the UEFI memory map actually covers its extent as a type the probe trusts,
   `fbtext.c:92-108`). Stops immediately if found.
2. **Tier 2 ("SAME"), reached only if no candidate was ever flagged as the console at all** (exactly the Shell's
   case, since it replaces the console-out handle's own GOP): every candidate is re-examined as if it were the
   console; if every one that passes validation agrees on one identical framebuffer (base, size, geometry, format,
   `fbtext.c:117-124`), that is accepted (`SEL=SAME`); if more than one genuinely different framebuffer qualifies, the
   result is `AMBIGUOUS GOP` and nothing is drawn, rather than guessing.

This is exactly the fix already proven on hardware (constraint 6): `SEL=SAME` was the rule that actually fired on this
unit under the Shell. Porting it to q1n1, under `SNAP_PLATFORM_SP11` only, means: replace `find_framebuffer`'s single
`locate_protocol` call, for the SP11 build only, with the same enumerate-then-select shape, reusing
`probe_gop_select`'s pure selection logic conceptually (it is Snapintosh's own code, in `probes/uefi/`, not q1n1's; a
real implementation would either vendor a small, equivalent pure function into q1n1 under the gate, or keep the two
copies independently maintained the way `fbcon.c`'s own header comment already does for the font and GOP-validation
code relative to `main.c` -- this study does not choose between those two options, both being reasonable and neither
changing the A16 default build). The result the Shell is expected to need (`SEL=SAME`) and the `AMBIGUOUS GOP`/`NO
GOP`/`NOT IN MAP` refusal reasons should be shown on whatever the earliest possible diagnostic line is (section 5.5),
since a wrongly-chosen or unvalidated framebuffer is exactly the kind of silent failure that would consume the
unexplained post-bounce time budget (constraint 5) without producing any photographable evidence at all.

### 8.3 What stays A16-only versus what is generic

The GOP *protocol* interaction (`locate_handle`, `handle_protocol`, `EFI_GRAPHICS_OUTPUT_PROTOCOL`'s layout) is
entirely generic UEFI and already shared between q1n1 and Snapintosh's probe. The *policy* of which candidate to
trust (console-handle-first, then same-framebuffer-fallback, memory-map-validated) is also generic, not A16-specific
-- it was designed and proven for the exact "Shell replaces the console handle" problem that applies to any UEFI
platform booted the same way, not anything about the A16's own firmware. Nothing in this section is A16-SPECIFIC.

## 9. Chainload design and extended native tests

### 9.1 Chainload

F15's existing mechanism (a flat binary at a fixed, reserved address, entered with inherited bootinfo in `x0`,
self-contained stack/BSS/vector setup, `entry.S`'s `Q1N1_STAGE` path and `main.c:1292-1349`'s `q1n1_stage_main`) is
already generic in shape; the only A16-specific input is the candidate address list (F02). For SP11, this design
proposes: (a) a board-config-supplied candidate list (section 7.2's "SP11 board config" need), refusing to fall back
to the A16 addresses under the gate (the same fail-closed posture [SP11-HOST-LINK-STUDY.md](SP11-HOST-LINK-STUDY.md)
already uses for the USB table: "nothing here supplies a default... unknown values fail closed"), with the real DRAM
map (X2 item 8) replacing an UNKNOWN placeholder once measured; (b) the dispatch gap F04 leaves (q1n1's current
`usb_mode` branch assumes a firmware USB boot window that does not exist on SP11, section 2.2): until B2 lands, the
SP11 production path should take the shape of the existing `MODE_UART_PROXY`/`no_cdc` branches already in
`proxy_efi_main` (a path that already skips the firmware-USB-window code entirely, `main.c:1186-1193`), not a new
third mode, so q1n1 reaches its chainload-capable, framebuffer-console state even with no working link at all --
consistent with B3's "framebuffer console always on" criterion and with constraint 5's urgency. The stage's own
`q1n1_stage_main` already re-derives anything not carried in bootinfo from ACPI (`:1318-1332`, for `MODE_UART_PROXY`);
an equivalent re-derivation for whatever SP11-specific state a stage needs (the board descriptor's slot, if the
host-link placement contract is adopted, section 1.3) is this design's recommended shape, not new machinery.

### 9.2 What extended native tests should cover

Following q1n1's own established dual-harness convention exactly (a native C driver plus a Python test runner, as the
host-link branch's `tools/test-q1n1-sp11-table.c`/`.py` and `tools/test-q1n1-sp11-usb-ports.c`/`.py` already do, and
as `tools/test-q1n1-boardinfo.py` does for a pure-Python carrier): this study specifies what these tests should prove,
without writing them (the brief's own stop condition: B1 is too large to implement sight-unseen in one pass). A future
`tools/test-q1n1-sp11-entry.c`/`.py` pair should cover, entirely on the host (none of this needs slbounce or hardware,
see section 10):

- The section 5.3 fail-closed check: given a modelled `CurrentEL` of 1, 2, and an invalid value, confirm the refusal
  path is taken for everything but 2, and that it is taken **before** any call the model marks as "EL2-only"
  (mirroring how the host-link study's native driver models a lifecycle and asserts ordering, not just end state).
- The section 8.2 GOP selection, ported into this test exactly as `probes/uefi/tests` already verifies it for the
  probe: tier-1/tier-2/ambiguous/none, with the UEFI-memory-map validation, run against the same kind of synthetic
  candidate sets the probe's own existing test suite uses (not duplicated here; cross-checked against it).
  the chainload mechanism, modelled: bootinfo inheritance across a simulated chainload, the board-config-supplied
  candidate selection refusing an A16 address without independent evidence (the same refusal shape
  `tools/sp11_usb_ports.py` already established for the USB table, section 1.3), and a stage correctly re-deriving
  what the F15 pattern says it must re-derive.
- The section 4 scoped-sweep model's own native-C mirror, if the slbounce fork is ever actually written: a C
  reimplementation cross-checked against `tools/sp11_ebs_sweep_model.py`'s Python oracle, the same cross-language
  pattern `tools/test-sp11-usb-ports.py`'s `CrossLanguage` class already uses for the USB table validator.
- The A16 regression firewall ([AGENTS.md](../AGENTS.md), [BUILDING.md](BUILDING.md)): any of the above that touches
  a file linked into the default build must leave the A16 ELF byte-identical with the gate off, proven the same way
  every other SP11-gated change in this project already is.

## 10. What a host model can and cannot prove

### 10.1 What `tools/test-uefi.py` (QEMU) does check today

Its own module docstring, read verbatim (`tools/test-uefi.py:1-4`): *"Boot the real EFI binary in QEMU; inspect
serial and capture its framebuffer. QEMU validates the UEFI target, not Qualcomm Secure Launch or the GENI driver."*
It boots the actual q1n1 EFI binary under EDK2/QEMU, drives it through a UEFI environment, and can exercise: the GOP
protocol and framebuffer capture (synthetic but a real `EFI_GRAPHICS_OUTPUT_PROTOCOL` implementation), ACPI/SMBIOS/
memory-map parsing against synthetic tables it builds (its own `synthetic_smem`, NVMe/GPT fixtures), the
`GetMemoryMap`/`ExitBootServices` two-call sequence itself (QEMU's own firmware really does call both), and anything
that is pure boot-services-era logic with no dependency on real silicon identity.

### 10.2 What it provably cannot check

**slbounce itself cannot run under QEMU at all.** Its entire mechanism (section 5.1) depends on Qualcomm's QHEE/Gunyah
honouring a vendor-specific SMC protocol and a `tcblaunch.exe` binary whose signature and TPM measurement chain QEMU's
EDK2 firmware has no analogue for; there is no "QEMU Secure-Launch" to validate against, consistent with
`test-uefi.py`'s own stated scope. Concretely, this means a host model/QEMU build **cannot** check: whether
`SL_CMD_IS_AVAILABLE`/`SL_CMD_AUTH`/`SL_CMD_LAUNCH` succeed on real firmware; whether the cache-sweep fix of section 4
actually avoids the real hang (the model in section 4.6 checks the *logic*, not the *firmware's reaction to it*);
whether `tb_entry`'s `hvc 0x1` really returns at EL2 on this unit (it is QHEE/Gunyah-mediated, not a QEMU-modellable
hypervisor call); whether the section 5.3 fail-closed check is ever actually reached by a real slbounce failure mode,
as opposed to only by a deliberately-injected test condition; whether the section 6 EL2-normalization placement
(A1 inside a forked `trans.s`, or A2) behaves as its host model predicts once real `HCR_EL2`/`TCR_EL2` writes execute
on Oryon v1 silicon; or the unexplained post-bounce reset timing of constraint 5, which by definition cannot be
reproduced by anything that is not the real firmware.

### 10.3 What remains EXPERIMENTAL/UNTRIED until real hardware

Every item in this list is a design or a model in this patch; none of it has run on a Surface Pro 11, and nothing
claims otherwise:

- The section 4.4 scoped-sweep design and its section 4.6 model: logic-checked only; not applied to any slbounce
  checkout; whether it avoids this unit's actual hang is UNKNOWN until a forked slbounce built with it is tried.
- The section 5.3 fail-closed EL check: not written as code in this pass (a design and its placement only).
- The section 6 EL2-normalization placement detail (the `trans.s:160-171` target): a reading of the pinned source,
  not an edit to it; C1's own model (`compat/el2-entry-model.c`) remains unwired to any real trampoline
  ([SP11-EL2-ENTRY.md](SP11-EL2-ENTRY.md) section 6 already says so, unchanged by this study).
- The section 8.2 GOP-selection port: a design reusing proven logic, not new q1n1 code in this pass.
- The section 9 chainload/test specification: a specification, not test code, per the brief's own stop condition.

## 11. Evidence summary

| Claim | Label |
| --- | --- |
| q1n1's `--boot` production path touches no EL2-only register before its own `ExitBootServices` call (F08) | VERIFIED, read every line of `efi_main`/`proxy_efi_main` |
| `proxy_main` warns but does not refuse when `CurrentEL != 2` post-EBS, then proceeds into `q1n1_gic_init` (F09) | VERIFIED, read `main.c:957`, `:1012`, `gicv3.c:156-171` |
| This gap has never fired on the A16 | INFERRED (from F08/F16; no A16 doc read for this study ever shows the warning) |
| slbounce's sweep covers exactly seven `Type`s, never reads `Attribute`, single end-of-loop barrier | VERIFIED, read `bounce_main.c:117-134`, `arch.c:18-33` at the exact pinned commit |
| slbounce individually flushes its own known buffers regardless of the blanket sweep | VERIFIED, read `sl.c:127`, `:313-315` |
| A third-party unit (different Surface model, different SoC) hung on an ordinary-looking, small `EfiBootServicesData` range, and removing the sweep fixed it there | OBSERVED, third party, one unit, read in full from the issue, not a paraphrase |
| Our unit's exact faulting address/mechanism | UNKNOWN |
| A non-write-back attribute is an architecturally plausible, checkable explanation for a `Type`-only false match | INFERRED, not confirmed on either unit |
| `tb_longjmp` runs at EL2, MMU off, TLBI before any register write, matching the sister document's independently-derived finding | VERIFIED, read `trans.s:132-200` directly, cross-confirms [SP11-EL2-ENTRY.md](SP11-EL2-ENTRY.md) section 2.5 |
| EL2 appears mid-way through `sl_ExitBootServices`'s own C execution via a setjmp/longjmp-shaped mechanism | VERIFIED, read `bounce_main.c:94-153` and `trans.s` together |
| `SLBOUNCE_ALWAYS_SWITCH=1` is required on any pure-ACPI platform (no DTB config table to probe) and q1n1's own `tools/build-slbounce.sh` already sets it | VERIFIED, read `bounce_main.c:30-67` and `tools/build-slbounce.sh` |
| q1n1 already anticipated slbounce generically, before this Surface Pro 11 effort, in its `--el2` diagnostic text | VERIFIED, `main.c:1270-1276`, authored by commit `5642cfd` (2026-10-21) |
| `probe_gop_select`'s two-tier rule is what actually selects a framebuffer under the UEFI Shell on this unit | OBSERVED (`SEL=SAME`, this week's hardware run) combined with VERIFIED code reading |
| The scoped-sweep model's properties (coverage, boundedness, attribute filter, chunking, fail-closed refusals, equivalence to classic on full declaration) | EXPERIMENTAL, host test only, this patch |
| Everything in sections 5-9 as an applied, working q1n1 boot flow | UNIMPLEMENTED |

## 12. Open questions

1. **Does a Snapintosh fork of slbounce's `bounce_main.c` (section 4.4) actually avoid this unit's hang?** Settled
   only by building it and trying it on hardware, under the same safety rules X1/X2 already used (AC power, one
   client, photograph every page). If it still hangs, the next diagnostic step this study recommends is capturing
   the faulting descriptor's `Type`/`Attribute`/address the way the third-party report did, before trying a different
   theory.
2. **Which placement (A1 or A2) does the actual X1/X2 measurement set (M1-M5, [SP11-EL2-ENTRY.md](SP11-EL2-ENTRY.md)
   section 2.10) select, and does A1's concrete `trans.s:160-171` target (section 6) survive contact with real
   register writes?** Unchanged open question from the sister document; this study only sharpens where A1 would land.
3. **Should the board-info placement contract (section 1.3) or a new, simpler pre-EBS blob be the carrier for "an SP11
   board config"?** This study did not decide between extending the already-landed placement contract and a
   purpose-built alternative; whichever is chosen must still fail closed on an absent or malformed blob exactly as
   `board_info_validate()` already does by design.
4. **Does the firmware's own identity map already cover every physical range this flow reads post-EBS** (section
   7.1), or could some range this design has not considered fault? X1/X2's own device-window policy is the
   established precedent for answering this per-window; this study did not re-run that analysis for anything beyond
   the GOP/ACPI/memory-map needs already listed in section 7.2, since nothing else in B1's narrow scope reads new
   MMIO.
5. **Should the `SLBOUNCE_SCOPED_SWEEP` option of section 4.5 actually be proposed upstream, and on what timeline
   relative to getting one working hardware boot first?** This study recommends it but does not open the issue or
   write the patch; that is a decision for whoever owns the hardware schedule, not a blocking dependency of trying
   the scoped sweep as a local, pinned fork first.
6. **What exactly should happen in the section 5.3 refusal page if the reset recurs before an operator can
   photograph it?** The X2 image's own answer (halt, draw once, never retry automatically) is this study's borrowed
   answer too, but whether a single drawn frame reliably survives the unexplained reset the way T01/T02's results did
   is itself untested for this specific, earlier point in the boot (constraint 5 is UNKNOWN in general, not merely
   at the one point X2 happened to measure it).

## 13. Where this study disagrees with, or sharpens, the background brief

The brief invited this: "If something in the background section above seems to conflict with what you read in the
actual source, say so explicitly... rather than silently picking one."

- **TravMurav/slbounce#13 is not primarily a report about the dcache-sweep hang.** Its title and the great majority
  of its body are about ITS/LPI interrupt delivery at EL2 on a *Surface Pro 12* (SoC X1P42100, not our X1E80100); the
  dcache-sweep note this study relies on (section 4.3) is a short, trailing "Unrelated note" at the very end, about a
  *different* unit than ours. This study read the issue in full rather than trusting the earlier paraphrase
  ("matching a public upstream issue... reported on a different Surface model") and confirms the paraphrase's core
  claim (a different Surface model, the sweep hangs there too) while correcting what the issue is actually about, so
  a reader does not go looking in it for a root-cause investigation of the sweep that is not there.
- **"a blanket `clear_dcache_range()` sweep... over Loader/BootServices/RuntimeServices/ACPI-reclaim memory" is
  accurate but can read as "every descriptor":** the real sweep is precisely seven `EFI_MEMORY_DESCRIPTOR.Type`
  values, never the whole memory map, and never `EfiConventionalMemory` (typically the largest part of any map) --
  this document is careful to state the exact seven types (section 4.1) because the proposed fix's soundness depends
  on not changing that list.
- **"the MMIO needed after ExitBootServices mapped beforehand (as `usb-ebs-prep.c` does for the A16)"**: read
  literally, `usb-ebs-prep.c` performs no page-table mapping operation at all; this study interprets the acceptance
  criterion as the capture-and-validate-across-the-EBS-boundary pattern that file actually implements (section 7.1),
  since no page-table code exists anywhere in it to generalize from. If the brief's author meant something more
  literal (a genuine identity-map gap this unit's firmware has), that is UNKNOWN and not addressed by this pass.
- **The q1n1 checkout the brief named (its own local working copy, outside this study's scratch area) has its
  `origin` remote set to** `https://github.com/sa3eedo12/q1n1.git`, not `https://github.com/Snapintosh/q1n1.git` as
  the task background states, and its `main` branch is 20 commits ahead of the pinned
  `7872398694ca8322e9cdebcc4ca2380491295f24` (recent USB-gadget/NCM work, unrelated to this study). A separate,
  pristine clone at the pinned commit, with `origin` genuinely pointed at `Snapintosh/q1n1`, was available in the
  shared scratch area and is what this study actually reads and cites throughout; the brief's own checkout was never
  written to, and only read-only `git log`/`git show`/`git diff` (no working-tree or branch changes) were ever run
  against it, to read the `sa3eedo12-sp11-host-link` branch's two commits.
- **[SP11-EL2-ENTRY.md](SP11-EL2-ENTRY.md) section 2.4/2.9's recommendation (A1 primary)** is not contradicted by
  this study; it is confirmed and sharpened (section 6), with one premise corrected (A1's "cost" of forking slbounce
  is shared with, not additional to, the fork section 4 already requires).

## 14. Addendum, 2026-11-12: what the first hardware attempts and a QEMU run corrected

Everything above was written before q1n1 ran on the unit. Three attempts and a QEMU run of the implemented flow
corrected it in four places. Evidence labels: the hardware observations are OBSERVED, the code facts are VERIFIED by
reading the cited source, and nothing here is hardware evidence for the sweep itself (still NOT RUN).

- **Section 9.1 / F01 / F04 were not carried through.** The text above says the SP11 production path should skip the
  A16 USB boot window; the delivered flow did not. `proxy_efi_main`'s `usb_mode` branch still calls
  `q1n1_boot_window`, which looks for the BIOS312 `UsbConfigDxe` image by an exact fingerprint and refuses with
  `REFUSED: UsbConfigDxe USB0 core/qscratch table differs from BIOS312` before any USB register is touched, so
  `--boot` cannot reach ExitBootServices on the Surface. `--uart-proxy` refuses without a PL011. Skipping the window
  would make the post-EBS proxy touch the fixture USB table's invented bases and a PL011 driver on a GENI UART, so the
  refusal is left in place until B2 supplies a real table. The existing `--el2` diagnostic (post-EBS it draws only
  to the framebuffer) is the step-1 vehicle instead.
- **Section 4.4 / 7.2: the framebuffer is not a declarable range.** The "seven ranges" were taken from `gpu_setup()`'s
  exclusion list, which includes the framebuffer. On this unit the framebuffer is wholly inside one RESERVED (type 0)
  descriptor, attribute `0x100F` (probe dumps `x1-run01..03`), and the scoped sweep accepts only types 1-6 and 9, so
  declaring it makes ExitBootServices fail on every attempt. The list is six ranges (heap, DMA arena, stage slot,
  image, map buffer, preload table), each listed only if populated; `--el2` declares its image and map buffer.
  `tools/test-sp11-ebs-sweep-model.py` pins the refusal. Nothing reads the framebuffer after ExitBootServices, so
  leaving it out can at worst leave stale pixels (INFERRED).
- **Section 5.5 / 7.2: a refusal has to explain itself.** The scoped sweep returns a bare `EFI_ABORTED` and prints
  nothing, and every cause (no table, a malformed table, more than 512 descriptors, a range split across descriptors,
  a wrong type, no WB bit) collapses into it. q1n1 therefore prints each declared range against the map
  (descriptor count, type, attribute, containment) before ExitBootServices and again on the failure and success
  pages. It reports facts and leaves the policy in slbounce.
- **Section 8.2: the selection logic was right; attempt 3's refusal was the map buffer (explained by attempt 6,
  below).** The unit's recorded maps run through q1n1's ported selection give the same answer as the probe, yet attempt 3
  refused the only GOP as `NOT IN MEMORY MAP`. Two real defects in the gathering code were fixed (a copy silently
  truncated at 512 entries, and a buffer a fifth the probe's size), and the second of them was the cause. The earlier
  reading of attempt 4, that neither could have mattered because the unit's map is 228 descriptors (233 with slbounce
  loaded), about 11 KiB, under both limits, compared the size of the map the firmware returns with the buffer. The
  firmware checks the buffer against a larger figure, 33,504 bytes on this unit, and the old code read a failed call as
  an empty map. The plain text console
  prints the map status, size and entry count and, per candidate,
  its verdict and first overlapping descriptor on every run.

**Update, 2026-11-12 (attempt 4, OBSERVED, one run).** With the fixes above and a scoped-sweep slbounce loaded, `--el2`
got through ExitBootServices and reached EL2 on the unit, and q1n1's framebuffer console drew its page (the numbers are
in SURFACE-PRO-11.md, Status of B1). So the section 4.4 design survived its first contact with hardware for the case it
was built for (the blanket sweep hangs; two declared ranges, an image and a map buffer, both inside single
loader-typed descriptors with WB, do not). The page then stayed up with its heartbeat still blinking roughly 40
minutes later (operator report). That is one run: it does not show the sweep suffices once more than a framebuffer
page runs afterwards, and it does not explain the hang.

Open after this addendum: the cause of attempt 3's refusal; what the X2 image does that q1n1's page does not, since
that image's board reset did not recur here; and `SLBOUNCE_SP11_DEVHE`, untried.

**Update, 2026-11-12 (attempt 5, OBSERVED, one fresh boot; the numbers are in SURFACE-PRO-11.md, Status of B1).**
Three things changed in this study's account.

- *Attempt 3's refusal belongs to the old code.* Attempt 3's commit, rebuilt, printed "No usable GOP framebuffer."
  twice in the one boot (before and after slbounce was loaded) while the current build captured the same GOP in that
  boot, so session state is ruled out. The selection functions are identical in both builds (the diff only adds to
  `sp11-fbgop.c`), the old glue's disassembly matches its source, and its buffer was aligned, writable and inside the
  image; this unit's map is under its limits. The cause is therefore not in anything read so far (UNKNOWN). Two
  preflight-only variants would separate it and have not been run.
- *The post-bounce EL2 state is now measured, not inferred.* q1n1's page read it right after the bounce: `HCR_EL2`
  `0x488000000` (E2H, RW, TGE), `SCTLR_EL2` `0x100030C5183D` (MMU and caches on), `TCR_EL2` `0x480803514` (TTBR0 walk of
  44 bits, 4 KiB, TTBR1 walks off), `TTBR0_EL2` `0xA7F2E000`, `MAIR_EL2` `0xFFBB4400`, `CPTR_EL2` `0x300000`,
  `CNTHCTL_EL2` `0x3`. Section 6's de-VHE sequence was replayed against these on the host: it is accepted, and the
  TCR conversion gives `0x80843514`. One assumption it still makes is unmeasured: that the firmware's EL1-format
  stage 1 descriptors stay valid under the non-VHE regime (a single XN bit, `nG` and bit 53 RES0).
- *The converter's placement was wrong.* The A1 trampoline (section 6) calls a TCR converter from `tb_longjmp`, and the
  call went to code 37 KiB away, outside the two pages `sl.c` gives tcblaunch as the transition-back image. The fork
  now links it straight after
  `trans.o`, inside that window, and a link-map test pins it. Everything the sequence executes with the MMU off is
  again inside the window upstream's design relies on. Hardware: NOT RUN.
- *The de-VHE run gets progress markers, qualified first.* With the MMU off and no console a hang in the trampoline
  shows nothing, so the fork can draw squares in a framebuffer strip (opt-in, `SLBOUNCE_SP11_MARKERS`). Their stores are
  data accesses with the MMU off, which section 6's model refuses and no hardware has qualified, so a dry-run build that
  draws and halts before any configuration write is to be run first. Plan and evidence labels: SURFACE-PRO-11.md, Status
  of B1. Hardware: NOT RUN.

**Update, 2026-11-13 (attempt 6, OBSERVED, one boot; the numbers are in SURFACE-PRO-11.md, Status of B1).** Two
questions closed, one opened.

- *Attempt 3's refusal is explained.* The preflight-only variants separated it. The new build with the old 24 KiB map
  buffer printed "No usable GOP framebuffer." with `map status 0x8000000000000005` (`EFI_BUFFER_TOO_SMALL`), 33,504
  bytes wanted and no entries; `bdff824` with a 144,000-byte buffer captured the framebuffer. The firmware asks for
  about three times the size of the map it returns (698 against 226 to 232 descriptors; why is INFERRED, not
  checked), the old call treated its failure as an empty map, and the selection then reported the framebuffer as not
  in the memory map. The 128 KiB buffer added with the first fixes was already the cure, although the analysis at the
  time judged it irrelevant, and q1n1 `ce21912` now keeps a
  test on its size.
- *The progress markers work on this unit.* Run A, the dry run, drew all five of its squares. Data stores to the
  framebuffer with the MMU off, from `tb_longjmp` at EL2, landed; slot 3's closing barrier returned. That removes the
  one unmeasured assumption the review had flagged in the markers themselves. It says nothing about the de-VHE
  configuration writes, which the dry run skips by construction.
- *Open: the de-VHE run.* Run B (markers) and, after any hang, run C (no markers) are the next hardware steps.
  Hardware: B and C NOT RUN. (Closed by attempt 7, below.)

**Update, 2026-11-13 (attempt 7, OBSERVED, one boot; the numbers are in SURFACE-PRO-11.md, Status of B1).** The de-VHE
entry works on this unit's boot CPU, once.

- *Run B succeeded.* With the marker build of the de-VHE gate loaded, `--el2` reached q1n1's page and q1n1 read `HCR_EL2`
  `0x88000010` (E2H clear), `TCR_EL2` `0x80843514` (the converted image), `TTBR0_EL2` and `MAIR_EL2` unchanged, `CPTR_EL2`
  `0x22FF`, `CNTHCTL_EL2` `0x3` and `SCTLR_EL2` with M, C and I set: the values the trampoline writes, with the MMU on and
  q1n1 executing. Section 6's sequence, accepted by the host model, therefore also ran on the hardware, so q1n1 and the
  monitor can keep the non-VHE EL2 state they have on the A16 on the boot CPU. Run C (the unmarked control) was not needed.
- *The firmware-descriptor risk did not show.* The one assumption the replay in attempt 5 could not check, that the
  firmware's EL1-format stage 1 descriptors stay valid under the non-VHE regime, held for the pages q1n1 ran (OBSERVED);
  untouched pages are unmeasured.
- *Still open.* Whether it repeats; the other CPUs (a PSCI secondary's arrival, M4); and NV2 with E2H clear (M8), which is
  the measurement the whole mediation model leans on: X2's functional test kept the firmware's E2H (1) in the stub's
  `HCR_EL2`, so it ran in the VHE regime. A re-run of that test in this regime needs the X2 image to declare its sweep
  range, because the reviewed scoped-sweep slbounce refuses an application that does not (X2's runs used a test-only build
  with the sweep removed). Hardware: M8 NOT RUN.
