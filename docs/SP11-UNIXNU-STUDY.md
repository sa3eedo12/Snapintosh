# SP11 survey of UniXNU: evidence and an alternative route

**Study only. UNIMPLEMENTED. Hardware status: NOT RUN.** Nothing here has run on a Surface Pro 11, and this study ran no
UniXNU code: it built no kernel and booted nothing. It reads one public project at pinned revisions, labels every claim,
and records one alternative route ("Route B", [section 5](#5-route-b-feasibility)) so that the project owner can decide on
it separately under decision D4 and kill criteria K1 and K2 of [SURFACE-PRO-11.md](SURFACE-PRO-11.md). **This study does
not recommend switching to Route B.** The A16 monitor, the plan and every build are unchanged: nothing in this document is
linked by any build, no UniXNU code or substantial text is copied, and the facts below are cited, not reproduced.

UniXNU is "an independent project to run iOS and macOS on non-Apple ARM64 hardware through UEFI" (`UX README.md:24`). It is
a different architecture from Snapintosh: it builds XNU from Apple's open source, patches it, and boots it at EL1 through
UEFI under whatever hypervisor the device has, while Snapintosh runs Apple's stock kernel at EL1 under its own EL2 monitor
with FEAT_NV2 (`docs/ARCHITECTURE.md:73`, `docs/SURFACE-PRO-11.md` section 3). Two other work packages cover UniXNU's Wi-Fi
driver and its GPU stack; this study only points at them. The evidence table (section 3) is the separate file
[SP11-UNIXNU-EVIDENCE.md](SP11-UNIXNU-EVIDENCE.md).

## 1. Summary of findings

| # | Question | Answer in one line | Where |
| --- | --- | --- | --- |
| 1 | Architecture comparison | Opposite on one axis (who owns EL2 and which kernel runs at EL1). Convergent on several contracts: the Apple device tree carries the identity, a 19.2 MHz counter is presented to userland as 24 MHz, and Apple's SPTM/TXM enforcement is replaced rather than reproduced. | [section 4](#4-architecture-comparison) |
| 2 | Route B (a UniXNU-style source-built EL1 kernel under the existing hypervisor) | Possible in principle and unproven for the Surface Pro 11. Every Hamoa driver would be new, bare-metal macOS is shown only up to Recovery and `launchd` on one SM8550 tablet, and the project would give up the stock-kernel principle. Recorded for the owner's decision; **not recommended**. | [section 5](#5-route-b-feasibility) |
| 3 | Reusable ideas outside GPU and Wi-Fi | Almost no reusable code (OS-side iOS and iPadOS drivers for SM8550). Reusable as cited facts and process ideas: firmware scanout reuse, persistent logs, completion levels. | [section 6](#6-reusable-ideas-outside-gpu-and-wi-fi) |
| 4 | Facts that settle plan UNKNOWNs | None of the plan's silicon questions (NV2, ECV, `DCZID_EL0`, the PCIe SMMUv3 state) is settled. Several hypervisor-interplay facts are OBSERVED on other SoCs and corroborate the plan's assumptions. | [section 7](#7-facts-that-bear-on-plan-unknowns) |
| 5 | Licensing and provenance | Permissive overall (BSD-2-Clause-Patent, MIT, Apache-2.0), with GPL-2.0-only and GPL-3.0-or-later islands, one ambiguous licence, and committed ABI records that our AGENTS.md rule 1 would forbid. No code reuse is proposed. | [section 8](#8-licensing-and-provenance) |
| 6 | Plan text | Ready-to-paste proposals for D4, K1, K2, an "Alternatives considered" paragraph, evidence rows and a risk row are in the work-package report; the shared documents stay coordinator-owned. | [section 5.7](#57-what-the-plan-should-record) |

Headline numbers, re-derived with a read-only script over the pinned `devices.toml` (`UX devices.toml:1`, schema 2):

- The catalog has **265** device records: 150 legacy (format 1) and 115 format 2.
- **115** records carry an `os_profile` (nine distinct iOS or iPadOS profiles and one empty value) and **0** name a macOS
  profile. The macOS 27.3 profile exists in the repository, but no catalog record selects it.
- **0** records name X1E80100, Hamoa, SC8380, a Snapdragon X part or an Oryon v1 part. The only Surface-branded record is
  the **Surface Duo 2** (SM8350, an Android phone).
- Oryon-class phone SoCs: **12** SM8750 records, **1** SM8750P record and **9** SM8850 records. That SM8850 shares the
  A16's core family is the coordinator's statement; the pinned catalog does not say it (INFERRED).
- **1** record names macOS: `sheng-macos` (SM8550, legacy format, a community repository), whose summary reads "macOS 26.5
  launchd on sheng hardware; graphical Recovery pending".

The three findings most worth acting on are these. First, the OBSERVED hypervisor-interplay facts of section 7 (a kernel at
EL1 under Gunyah cannot use the physical timer, may get no SGIs, and does not own some SMMU context banks) describe what the
Surface Pro 11's firmware environment may look like, and they matter only if Route B is ever entertained. Second, UniXNU
documents that Apple userland assumes a 24 MHz timebase (CoreAudio, Swift clocks), which is a direct warning for the
deferred `SNAP_KERNEL_NATIVE_COUNTER` proposal of [KERNEL-PATCHES.md](KERNEL-PATCHES.md). Third, bare-metal macOS beyond
Recovery is not demonstrated anywhere in the catalog.

## 2. Method, labels and pinned sources

### 2.1 Method

The `docs/` trees of the pinned repository (concepts, xnu, porting, drivers, guide, reference and the device pages), its
`devices.toml` catalog, its profiles and patch series, and the sources of the component repositories named in the table
below were read. Numbers were counted with read-only scripts (`git ls-files`, `wc`, a TOML read of the catalog). Nothing was
built, run or installed, and nothing was copied: every claim is a fact with a pinned citation or an explicit UNKNOWN. Our
own side was read in this tree (`AGENTS.md`, [SURFACE-PRO-11.md](SURFACE-PRO-11.md), [ARCHITECTURE.md](ARCHITECTURE.md),
[KERNEL-PATCHES.md](KERNEL-PATCHES.md), [COMPATIBILITY.md](COMPATIBILITY.md), [FULL-INSTALL.md](FULL-INSTALL.md)).

### 2.2 Labels

| Label | Meaning in this study |
| --- | --- |
| VERIFIED | Read in a pinned public source or in this tree, or counted by a script over a pinned file. It says what that source says. It never means that UniXNU was built or run by us, and never means anything ran on a Surface Pro. |
| OBSERVED | A third party's hardware or emulator report inside the pinned records or source comments. One unit unless stated. Treat as a lead, as the plan treats its community reports (R20). |
| INFERRED | Reasoned here from other evidence and not checked. |
| UNKNOWN | Not established. A named measurement or source would settle it. |
| EXPERIMENTAL / UNIMPLEMENTED | As in [AGENTS.md](../AGENTS.md): code exists but is unqualified, or no code exists. |

### 2.3 Pinned sources

The licence column is the repository's `LICENSE` file as read at the pin. GitHub's API reports `NOASSERTION` for most of
these repositories (BSD-2-Clause-Patent is not an identifier it matches), MIT for ArmSmmu4K, BSD-2-Clause for ShengUfs and
Apache-2.0 for ShengNovatekTouch. [Section 8](#8-licensing-and-provenance) gives the file-level detail.

| Key | Source | Pin (commit) | Licence | Read for |
| --- | --- | --- | --- | --- |
| UX | UniXNU/UniXNU | `d830d23b0d19c9322d906c21afeca1d6c54aeffe` | BSD-2-Clause-Patent | docs, catalog, profiles, patches |
| DB | UniXNU/DarwinBoot | `8544957e04bdc43e2aac9243764ac16979f15c42` | BSD-2-Clause-Patent | the XNU loader |
| AC | UniXNU/ArmCpu | `16040839b17e55e01ab1ec0c2b0f73f431fd472b` | BSD-2-Clause-Patent | GIC, timer, PSCI under a hypervisor |
| AD | UniXNU/ArmDTPlatform | `e8bd8d5de395c6cbc00d762c1bef57dbd15a448d` | BSD-2-Clause-Patent | the platform expert |
| AS | UniXNU/ArmSmmu4K | `83969cd874503e65997ff7639f7dcf3d0a92cd17` | MIT | SMMU tables |
| GF | UniXNU/GopFramebuffer | `d711e8c9de58825c9a758907cb15c409a804a537` | BSD-2-Clause-Patent | display |
| GS | UniXNU/QcomGeniSpi | `eac47baefe6a5d2368a70351e6ad1b6361bedbe2` | BSD-2-Clause-Patent | GENI SPI |
| VR | UniXNU/VerifiedRoot | `974f043d0c71ba2e60331483a41c6c6d35274ee3` | BSD-2-Clause-Patent | root policy tooling |
| XP | UniXNU/XnuPrelink | `8f4f3866545727e2dc3d2c8cb60f8a87a9539c12` | BSD-2-Clause-Patent | kernel and kext binding |
| SC | UniXNU/UniXNU-sm8550-common | `97665e28578483082b674e8375a8fa65f4cf49cf` | BSD-2-Clause-Patent (DTSI: BSD-3-Clause) | the size of a SoC port |
| UD | UniXNU/UniXNU-sheng | `00ba90984e831e4c6ce30ec3d8a56e6434e9f1af` | BSD-2-Clause-Patent (one script: GPL-2.0-only) | the size of a device port |
| DW | UniXNU/QcomDwc3 | `e4efe95c903a737e0f5955e5b50a8fb4d2a7d718` | GPL-2.0 (`LICENSE`) and BSD-2-Clause-Patent (`LICENSES/`) | USB |
| SU | UniXNU/ShengUfs | `b11de87c234cf4b8f80a8160fb15e6f37a8e5d0c` | BSD-2-Clause (`LICENSE`); sources say BSD-2-Clause-Patent | storage |
| RB | UniXNU/RamBlock | `60a54ce8f86161db958fd8e6edf577081b3c9ae7` | BSD-2-Clause-Patent | block storage |
| NT | UniXNU/ShengNovatekTouch | `69fdd6223ed2572aa8bd8351e77c53a238276130` | Apache-2.0 (with a `NOTICE`) | touch |
| HI | UniXNU/ShengHidInput | `114061452959a547aaa08f9ca5aa54033394ca2f` | BSD-2-Clause-Patent | HID |
| FS | CrossDarwin/macOS-ForSheng | `ca57db73c066162323e77156a9f0f1d72b1de4ff` | GPL-3.0 | bare-metal macOS record (fetched through the GitHub API, not mirrored locally) |
| DV | Droid-VM/macOS-DroidVM | `428a62be9eb5bb209de33c4f2b5168765ed98b33` | none declared | CI artifact practice (fetched through the GitHub API, not mirrored locally) |
| LX | torvalds/linux | `7475ea8b58616b33c04afb976cf0fbaad9dd63fb` | BSD-3-Clause (`x1-el2.dtso`, SPDX line 1) | the upstream EL2 overlay; the plan's own pin |

The main-repository pin is a shallow clone dated 2026-11-10. The catalog pins other revisions of some components (for
example ArmCpu at `93a5030c5d983d0ef90f0fca93879d6e36ad1ba8`); this study read the heads named above, and says so wherever
it matters. The first 16 hex digits of the SHA-256 of `arch/arm64/boot/dts/qcom/x1-el2.dtso` as read at the Linux pin are
`d6a9666bd89e8787`; the test uses them to refuse a Linux checkout at another commit.

## 3. Evidence table

The evidence table has 58 rows, U01 to U58. Each row is one claim with a label and a pinned source, and the table lives in
[SP11-UNIXNU-EVIDENCE.md](SP11-UNIXNU-EVIDENCE.md) so that each document stays under the 50 KiB file limit. A source is
written `KEY path:lines`, with the key from [section 2.3](#23-pinned-sources); a path without a key is in this tree. The host
test [`tools/test-sp11-unixnu-study.py`](../tools/test-sp11-unixnu-study.py) reads the two files as one study and checks every
such citation against the pinned checkout when one is available. The rows by subject:

| Rows | Subject |
| --- | --- |
| U01 to U06 | What UniXNU is, how its kernel is built, and the EL1 boot chain |
| U07 to U13 | The security model, pointer authentication and the macOS root volume |
| U14 to U20 | Userland identity, the 24 MHz timebase and the other kernel patch families |
| U21 to U25 | The device catalog and the macOS records |
| U26 to U38 | Behaviour reported on phones under a hypervisor: timer, GIC, SMMU, PAC, PSCI |
| U39 to U42 | The size of a SoC port and the firmware environment |
| U43 to U50 | Reusable pieces: SMMU, display, GENI, USB, UFS, power, logging |
| U51 to U56 | Process records, inputs never shipped, committed ABI records, licences |
| U57 to U58 | The upstream EL2 overlay and the Oryon bootstrap code |

## 4. Architecture comparison

### 4.1 Side by side

| Dimension | UniXNU | Snapintosh | What it means for the Surface Pro 11 |
| --- | --- | --- | --- |
| Kernel provenance | Built from Apple's open XNU (xnu-10063.141.1 for iPadOS 17, xnu-12377.121.6 for the 27 profiles) with ordered patch series; Apple's kexts are grafted in unchanged (U02, U03). | Apple's stock J700/T8140 kernelcache of build 26A428, unmodified by default; a few opt-in patches found by instruction pattern (`docs/ARCHITECTURE.md:73`; `docs/KERNEL-PATCHES.md:15`). | The stock-kernel principle is the project's central difference and the reason Route B is not a small variation. |
| Which Apple userland | iOS and iPadOS 16, 17, 18, 26 and 27 images; a macOS 27.3 profile built on an older kernel with Darwin 26 calls patched in (U12). | macOS 27 build 26A428 userland with its matching kernelcache (`docs/ARCHITECTURE.md:73`). | Our kernel and userland are one Apple build, so there is no kernel-to-userland skew to patch over. |
| Exception levels | The kernel runs at EL1; the loader refuses any other level (U04). No EL2 code of its own. | The stock kernel runs at EL1 believing it is at EL2 through FEAT_NV2; our monitor owns EL2 (`docs/SURFACE-PRO-11.md` section 3). | Route B removes the NV2 gate (K1) and the slbounce gate (K2) by construction, and adds an EL1-under-hypervisor qualification instead (section 5). |
| Hypervisor interplay | Lives with the vendor hypervisor: virtual timer only, GIC and PSCI used as the hypervisor allows, some SMMU banks not ours (U28 to U33). | Replaces the hypervisor's role: on the A16 the firmware enters at EL2; on the SP11 EL2 is reached only through slbounce (`docs/SURFACE-PRO-11.md` section 4.1). | The facts of section 7 matter for Route B and are mostly moot for Route A. |
| Page size and MMU | 4 KiB kernel (BCM2837 configuration); patches for bootstrap tables, user ABI, hash cache, allocator alignment (U02, U20). | 16 KiB guest tables, 4 KiB shadow tables built by `shadow/`; the cores support 4 KiB and 64 KiB, not 16 KiB (`docs/ARCHITECTURE.md:56`). | Route B needs no shadow MMU, a real simplification, but it makes userland 16 KiB assumptions a kernel problem (section 4.2). |
| SPTM, TXM, PPL | The source-built kernel uses the non-SPTM pmap and no PPL-style monitor; there is no SPTM or TXM at all (U07). The original `launchd` and system services run on it far enough to reach a Recovery screen or a home screen (U24, U26), so Apple's userland tolerates the absence; what else userland sees (launch constraints are skipped in owner mode, U08) is not enumerated in the pinned documents (UNKNOWN). | The monitor implements the SPTM and TXM call interface so the stock kernel runs; "boot-compatibility semantics, not full SPTM security enforcement" (`docs/COMPATIBILITY.md:81`). | Both give up Apple's monitor-enforced trust. Ours keeps the call interface and drops the enforcement; theirs removes the callers. |
| Code signing and trust cache | Original AMFI and AppleImage4 stay; a runtime trust-cache path, owner-signed root policies and a platform-code policy replace Apple's boot inputs (U08, U09). | The stock AMFI path runs against emulated TXM answers; signing-related monitor calls are outside the public documentation (`docs/COMPATIBILITY.md:81`). | Not comparable in detail; neither reproduces Apple's enforcement. Our side is under-documented on purpose. |
| Boot chain | Android bootloader, BootShim, Mu-Silicium UEFI, `UniXNU.efi`, DarwinBoot, XNU at EL1 (U04, U41); on stock EDK2 the boot manager scans volumes for its configuration (U42). | UEFI, q1n1, the monitor, then the stock kernel; on the SP11 slbounce comes first ([BOOT-CHAIN.md](BOOT-CHAIN.md); `docs/SURFACE-PRO-11.md` section 4.1). | Route B would not need q1n1, a Mac host link or slbounce to boot, which is a standalone-first advantage (AGENTS.md section 5). |
| Root volume | Image windows in the userdata partition with a manifest; root matched by UUID; the macOS root is unsealed (U13). | An installed APFS container produced on a Mac by Apple's own installer, placed in one GPT window and read through a virtual ANS2 (`docs/FULL-INSTALL.md:47`). | Route B needs its own image-window tooling and a Hamoa storage driver; ours reuses the stock installed system. |
| Userland identity | Synthesised Apple device tree from a Linux-style DTS plus profile tables (U14); the macOS profile claims Mac17,11. | Starts from Apple's J700 device tree and adapts it (`docs/ARCHITECTURE.md:59`). | Same mechanism, opposite direction. Neither claims an SP11 identity. |
| Timebase | Kernel scales the 19.2 MHz counter by 5/4 and sends userspace through the `mach_absolute_time` trap (U16). | The monitor traps counter reads and converts 19.2 MHz to 24 MHz in software (`compat/timer-offset-model.h:7-10`; `docs/COMPATIBILITY.md:178-180`). | Same 5/4 conversion, different place. See section 4.2 for the lesson for the deferred native-counter patch. |
| Pointer authentication | Architectural keys on every CPU, one shared set for kernel and processes (U10); depends on EL1 key access (U11). | The monitor handles PAC and normalises ID registers on Oryon; no kernel words change (`docs/KERNEL-PATCHES.md:264`). | UniXNU's statement that the CPU's PAC algorithm can differ from Apple's supports the plan's INFERRED QARMA consequence (C4) but is not proof for our design. |
| Update path | Pinned kernelcache hash and ABI records; a new Apple build needs re-qualification and new maps (U03, U54). | A pinned guest profile of one audited bundle; another build needs a new profile and the same acceptance gates (`docs/COMPATIBILITY.md:14-20`). | Neither follows Apple updates for free. |

### 4.2 Patch families mapped to our contracts

UniXNU carries 73 patches for xnu-12377 and 59 for xnu-10063 (`UX docs/xnu/patch-reference.md` lists them by purpose). The
verdict column says whether Snapintosh already mediates the same contract, does not need it under the stock-kernel
principle, or has a contract to consider.

| UniXNU patch family | Purpose | Snapintosh counterpart | Verdict |
| --- | --- | --- | --- |
| Build and toolchain (flag dependencies, SDK root, entitlement headers, machine layer) | Build open XNU outside Apple's tree. | None: we do not build the kernel. | Not needed. |
| 24 MHz timebase (`timebase-hz`, parity patch 0042) | Present Apple's timebase over a 19.2 MHz counter, for CoreAudio and Swift clocks (U16 to U18). | The rate adapter in the monitor already presents 24 MHz (`compat/lean-trap.h:11-12`; `docs/COMPATIBILITY.md:178-180`). | Already mediated. Contract to consider for the deferred `SNAP_KERNEL_NATIVE_COUNTER` (`docs/KERNEL-PATCHES.md:279-282`): any 19.2 MHz guest timebase must stay out of userland. |
| Realtime computation clamp (0041) | Keep coreaudiod's realtime thread realtime on the scaled counter. | The guest already sees 24 MHz. | Contract to consider: the monitor's trap latency inflates measured computation time; audio is out of scope, so low priority (INFERRED). |
| Code signatures on a 4 KiB kernel (`native-4k-signature-hash-cache`, 0017) | Validate 16 KiB-hashed CodeDirectories at 4 KiB. | The guest kernel keeps 16 KiB pages; user 4 KiB pmaps follow Apple's own 4K switch (`docs/KERNEL-PATCHES.md:15`). | Not needed (INFERRED for the opt-in 4 KiB user-pmap patch, which relies on the stock kernel's own 4K support). |
| `mmap` on 16K boundaries (0047) | Userland such as V8 and PartitionAlloc assumes 16K-aligned kernel-chosen mappings. | The opt-in `SNAP_KERNEL_USER_4K` makes every process a 4 KiB user pmap (`docs/KERNEL-PATCHES.md:15`). | **Contract not yet considered:** whether browser engines that assume 16K behave in 4K-forced processes is not recorded (INFERRED relevance; no test exists). |
| Union root, one device at "/" (`native-union-*`, `macos-union-one-volume`) | Compose the OS and SystemOS images into one root volume (INFERRED: because Apple's own boot policy is absent). | None: the installed APFS container is mounted as the stock kernel expects (`docs/FULL-INSTALL.md:47`). | Not needed. |
| Exec denial (0035) | Stop `mobileactivationd` and `cryptexd`, which need a Secure Enclave identity and cryptex grafting. | A SEP handshake without Secure Enclave services (`docs/COMPATIBILITY.md:190-192`); the A16 reaches the desktop (`docs/STATUS.md`). | Already exercised on the A16; whether the SP11 guest differs is UNKNOWN until X7. |
| Trust cache, AMFI, owner root (`native-trust-cache-runtime`, `offline-owner-*`, launch-constraint skip) | Replace the roles of TXM and boot-time trust inputs. | Emulated TXM answers (`docs/COMPATIBILITY.md:81`). | Already mediated, differently; both give up enforcement. |
| PAC (`native-pac-keys`, `native-pac-arm64-user-jop`) | Program architectural keys and keep arm64 processes working. | Monitor-side handling (`docs/KERNEL-PATCHES.md:264`). | Already mediated. |
| Platform and CPU (`native-smp-secondary`, `native-arm-pan`, timer entry, panic region) | CPU count, PAN and timer on non-Apple cores. | vCPU placement and PSCI start (plan item C2), AIC and timer emulation (`docs/ARCHITECTURE.md:58`). | Already mediated. Apple-only CPU override registers in the reset and resume assembly (a UniXNU config knob for an Oryon-class SoC, U58) correspond to the register-answering the monitor already does (INFERRED; the stock reset path is not read here). |
| macOS target kernel (Darwin 26 syscalls, KPIs, shared region size, kqworkloop limits, service-port checks) | Run macOS 27.3 userland on an older XNU. | None: our kernel is Apple's own for the userland (U12). | Not needed; this is the structural advantage of the stock-kernel principle. |
| GPU add-on mount, sandbox grant, work-interval fixes | Let a source-built GPU stack work under Apple's sandbox and Core Animation. | Our GPU path is the AdrenoMetal and AdrenoX2 stack (`docs/ARCHITECTURE.md:73`). | Pointer to the GPU-stack study; not analysed here. |

## 5. Route B feasibility

### 5.1 Definition

**Route B** means: boot a UniXNU-style source-built XNU at EL1 under the hypervisor the Surface Pro 11's firmware already
runs (Gunyah, by the plan's reading), with no slbounce, no `tcblaunch.exe`, no EL2 code of ours and no NV2. The chain would
be the Surface UEFI, an unsigned UniXNU boot manager and DarwinBoot-style loader on the boot media, a synthesised device
tree built from a Hamoa DTS, the 4 KiB kernel with in-kernel drivers for Hamoa, and a root volume in a storage window.

Two variants are named here so that the plan can keep them apart. **B1** is the one just described. **B2** is a
source-built kernel at EL1 under our own EL2 monitor without NV2: it would still need slbounce (K2) but not NV2 (K1). B2 is
the kind of redesign decision D4 already contemplates ("UNDEF interposition, in-memory kernel patching"); it is named, not
analysed.

### 5.2 How the plan knows the firmware state

The plan's claim that the Surface UEFI runs at EL1 under Gunyah is the "Firmware entry" row of `docs/SURFACE-PRO-11.md`
section 4.1, labelled VERIFIED from slbounce's source and README and OBSERVED on one community unit. It rests on E6 and E7
(slbounce hooks `ExitBootServices` and resumes at EL2; Secure Boot off is mandatory), on E10 (one community Linux-at-EL2
report), and on the upstream overlay facts E24 and E36, which U57 re-reads at the same pin: the base device tree is the EL1
description and Gunyah owns the PCIe SMMU, the ITS emulation and a software watchdog. No `CurrentEL` has been read on this
unit: the plan's X1 probe reads it after slbounce. For this unit the EL1-under-Gunyah premise is therefore INFERRED. The
plan names Gunyah, not QHEE; UniXNU's records say "Qualcomm hypervisor" and Gunyah (U34, U28).

### 5.3 Evidence for Route B

| # | Evidence | Label | Ids |
| --- | --- | --- | --- |
| RF1 | A source-built XNU at EL1 under the vendor hypervisor runs on Qualcomm hardware: macOS Recovery services reach `launchd` on an SM8550 tablet, an SM8850 phone reaches an iOS 16.7 home screen with eight CPUs, and the OnePlus 13T (SM8750) has device-verified boot, CPU, memory and display areas. | OBSERVED | U24, U26 |
| RF2 | The loader chain is not tied to Mu-Silicium: it runs on stock EDK2 under QEMU, which is the shape a Surface UEFI stick would take. | OBSERVED | U42 |
| RF3 | The hypervisor-owned resources that UniXNU met under Gunyah have workable answers: the virtual timer, SPI IPIs, hypervisor-attached SMMU banks, MSI through the PCIe controller's own receiver. | OBSERVED | U28 to U33, U37 |
| RF4 | The upstream base device tree already describes Hamoa and the Denali at EL1, so a Hamoa DTS exists to feed a synthesised device tree. | VERIFIED | U57 |
| RF5 | Route B needs no shadow MMU, no SPTM or TXM emulation and no host link to boot, which matches AGENTS.md's standalone-first direction. | INFERRED | U04, U07 |
| RF6 | A SoC port is modest in UniXNU's architecture: the SM8550 SoC repository is 81 lines of DTSI and 911 lines of kit C and headers, though the sheng device repository around it is large. | VERIFIED | U39, U40 |

### 5.4 Evidence against Route B

| # | Evidence | Label | Ids |
| --- | --- | --- | --- |
| RA1 | Nothing in the catalog is a Hamoa, X1E or Snapdragon X device; every Hamoa driver (GENI, clocks, PCIe with an SMMUv3, NVMe, DPU and eDP, a7xx GPU, Surface Aggregator on UART) would be new, and UniXNU's storage and display stacks are UFS and DSI based. | VERIFIED | U22, U43 |
| RA2 | Bare-metal macOS is shown only as Recovery and `launchd` on one SM8550 tablet, with the graphical Recovery, input, activation and installation unaccepted. No catalog record shows a macOS login window or desktop on hardware. | OBSERVED | U23, U24 |
| RA3 | The macOS 27 profile runs macOS 27.3 userland on xnu-12377 with patched-in Darwin 26 calls, because the matching XNU is not public. A build 26A428 profile does not exist. | VERIFIED | U12 |
| RA4 | The source-built kernel replaces Apple's SPTM and TXM semantics with a non-SPTM pmap, an in-kernel trust-cache path, a platform-code AMFI policy and owner-signed roots, and its PAC does not isolate processes. This is a different security model, not a port of the A16's. | VERIFIED | U07 to U10 |
| RA5 | The update path is a pinned kernelcache and generated binding maps; a new macOS build needs a new qualified profile, and the arm64e fileset path is "In development" on its only supported device. | VERIFIED | U03, U11 |
| RA6 | Hypervisor-owned resources on the Surface Pro 11 are UNKNOWN: the PCIe SMMUv3 and ITS state (U57), the software watchdog's servicing, the GPU zap shader (needs secure-world services at EL1, U57) and whether the Type Cover UART is available at EL1. | UNKNOWN | U57 |
| RA7 | The A16 investment (monitor, shadow tables, SPTM and TXM emulation, the GPU kext that depends on a monitor-owned ring and SMMU) would not carry over; the GPU would be a different EL1 driver stack. | INFERRED | `docs/ARCHITECTURE.md:73` |
| RA8 | Several inputs UniXNU needs are Apple-derived and would stay outside the repository by our rule 1, and some of its own committed records are ones that rule forbids (section 8). | VERIFIED | U52 to U55 |
| RA9 | The device tree synthesis path takes one contiguous DRAM range and the loader allocates one contiguous window; the Hamoa map has a low bank and a high bank (plan E23), so a window would have to be carved from the high bank. | VERIFIED / INFERRED | `UX docs/porting/device-tree.md:79`; `UX docs/concepts/boot-chain.md:77` |

### 5.5 Decision table: what each route needs at X0 to X3

Route A is the current plan (stock kernel at EL1 under our EL2 monitor with NV2). Route B is B1 of section 5.1. Both
routes keep the same authorizations for Secure Boot and BitLocker handling, because the boot media's EFI binaries are not
Microsoft-signed (`docs/SURFACE-PRO-11.md` section 1.3).

| Gate | Route A (current plan) | Route B (B1) |
| --- | --- | --- |
| X0 | Backup, recovery key, boot stick with slbounce, `sltest` and the five staged `tcblaunch.exe` candidates (N1). | Backup, recovery key, boot stick with an unsigned boot manager and loader; no `tcblaunch.exe`. A Mac with Xcode, the matching SDK and an Apple restore image to build the kernel and the root image. |
| X1 | EL2 through slbounce; the green line; handoff registers recorded. Stop: K2. | No EL2 step. The existing EFI probe already reports `CurrentEL` and stops at `NOT AT EL2` when it is not at EL2; recording the rest of the EL1 environment (ID registers, timer, GIC, PSCI conduit, SMMU and watchdog state, framebuffer) would need a new read-only EL1 probe (UNIMPLEMENTED). K2 does not apply. |
| X2 | The NV2 functional test with its negative control (T02), timer, `DC ZVA`, GIC, PCIe and SMMU reads. Stop: K1. | No NV2 test. The EL1-under-hypervisor equivalents: virtual timer and `CNTFRQ_EL0`, SGI delivery, PSCI, PAC key access at EL1 (`HCR_EL2.API` and `APK`), PAN, 4 KiB granule, PCIe link and NVMe DMA with the SMMU as Gunyah leaves it. K1 does not apply. |
| X3 | q1n1 on Hamoa and a USB host link to the Mac. Stop: K3. | No host link is needed to boot. A log path is: framebuffer text, an on-stick persistent log or QR export (section 6). K3 changes meaning. |
| What it can reach | Up to X11 as planned, with the stock kernel, gated by K1 and K2. | Not demonstrated. The best catalog evidence is Recovery and `launchd` on one SM8550 tablet. Every Hamoa driver, the root-volume tooling and the GPU stack are new work, and K4 (storage window) and K5 (GPU) have Route B counterparts that are UNKNOWN. |
| Main risks | K1 and K2 are silicon and firmware go or no-go items; R3 (VHE versus de-VHE) and R18 (`ResetSystem`) sit on this path. | Hypervisor-owned resources (RA6), macOS maturity (RA2), the changed security model (RA4), re-qualification per macOS build (RA5). |

### 5.6 What each route gives up

| Principle | Route A | Route B |
| --- | --- | --- |
| Stock kernel, normally unmodified | Kept. | Given up: a source-built, patched, older XNU. |
| Apple's SPTM and TXM semantics | Interface emulated, enforcement not reproduced. | Removed; a different trust model. |
| Standalone-first runtime | Not met today: every boot needs the Mac host (D8). | Closer: no host needed to boot. |
| Update path | Pinned bundle; a new macOS build needs a new profile. | Pinned kernelcache and maps; a new build needs re-qualification. |
| Reuse of A16 work | Maximal. | Little: most of it is monitor, shadow and SPTM or TXM specific. |
| Provenance (AGENTS.md rules 1 and 4) | Stock kernel and Apple inputs stay local. | Patches against APSL-2.0 code, grafted Apple kexts, committed ABI records in UniXNU (section 8). |

### 5.7 What the plan should record

This study recommends that the plan **record** Route B and **not** adopt it. The owner decides later, and only after a
trigger. Ready-to-paste wording for D4, K1, K2, an "Alternatives considered" paragraph, evidence rows and a risk row is in
the work-package report under `PROPOSED SHARED-DOC TEXT`. In outline:

- D4 gains a named candidate in its "separate design study": a UniXNU-style source-built EL1 kernel under the existing
  hypervisor (Route B, B1), with B2 noted, both subject to the owner's explicit approval.
- K1 and K2 each gain a sentence saying that Route B is the recorded alternative, that it is a different project rather
  than a fallback, and that nothing is switched silently.
- The study's decision table (section 5.5) is the starting point for that design study's gates and kill criteria.

## 6. Reusable ideas outside GPU and Wi-Fi

"Maturity" says what evidence the pinned UniXNU records give: a device record with a hardware result (device-verified in
UniXNU's own terms, U51) or host tests only. "Reusable as" separates cited facts from code. No code reuse is proposed, so
no `THIRD_PARTY_NOTICES.md` row is proposed.

| Workstream | UniXNU piece | What it provides | Licence | Maturity | Reusable as | Recommendation |
| --- | --- | --- | --- | --- | --- | --- |
| P1, P2 display | GopFramebuffer (`GF`) and the display guide | An iPadOS display backend over the firmware GOP scanout: bounded present buffers, swap retirement, a timer standing in for vblank (U44, U45). | BSD-2-Clause-Patent (one MIT header) | Device records for sheng and the OnePlus 13T; physical refresh not established | Facts | **Cite.** It confirms the plan's P1 choice (reuse the firmware pipeline, leave the panel's refresh alone). It has no refresh control, which fits the 120 Hz study's "only panel-advertised timings" stance. The code is bound to the iPadOS display ABI and does not apply to our guest. |
| G1 to G3 input | QcomGeniSpi (`GS`), ShengHidInput, the I2C keyboard cover protocol | A GENI SPI library (571 lines), touch and HID publication for iPadOS, an I2C cover (U46). | BSD-2-Clause-Patent | sheng records; touch and USB HID unaccepted in the macOS port (U24) | Facts | **Cite.** There is no Surface Aggregator, no UART frame handling and no Type Cover protocol. The SPI library is relevant only to the touchscreen stretch goal (a GENI SPI engine at `0x00a88000`), and then as a reference to adapt with attribution. |
| B2 USB | QcomDwc3 (`DW`) | A DWC3 driver for SM8550 behind Apple's own USB stacks, with PHY and retimer sequences from Linux and UCSI over GLINK (U47). | GPL-2.0 and BSD-2-Clause-Patent; PHY headers GPL-2.0-only | One tablet; "native DWC3" community variants exist | Structure only | **Ignore as code** (copyleft, Apple-stack dependent, SM8550 only). **Cite** the bring-up order as a checklist; the same facts are in Linux. |
| S2 storage | ArmSmmu4K (`AS`), ShengUfs (`SU`), RamBlock (`RB`), VerifiedRoot (`VR`) | SMMUv2 4 KiB tables, a UFS driver with SMMU takeover and GPT window bounds, a RAM-backed block device, owner root policies (U43, U48). | MIT; ambiguous (BSD-2-Clause or -Patent); BSD-2-Clause-Patent | SMMU: host tests only; UFS: sheng device records | Ideas | **Ignore as code**: UFS is not NVMe and SMMUv2 is not the PCIe SMMUv3. **Cite** the discipline of U48. |
| Power and thermal | `docs/porting/power.md`, ADSP and PMIC-GLINK probes | OS-side governors on idle time and GMU busy counters, a battery manager, ADSP bring-up (U49). | BSD-2-Clause-Patent | sheng records; throttling "recorded" on SM8850 | Facts | **Ignore** for H1 (our guard is stricter and read-only). **Cite** U49 for R19: it shows an OS-side driver loading the ADSP itself. |
| Device-tree matching | Device-tree conversion and matching guides, `ArmDTPlatform` (`AD`) | A DTS to Apple device tree generator and the nodes the platform expert requires (U06). | BSD-2-Clause-Patent | QEMU and phones | Facts | **Cite** as the Route B counterpart of our board descriptor; for Route A we adapt Apple's own device tree. |
| Porting checklists | `docs/porting/*.md`, records, bring-up order | Completion levels, a bring-up ladder and per-area evidence (U51). | n/a (prose) | Used by all device records | Process ideas | **Adapt as process** in our own words: a per-gate evidence record with a command, a full commit and a result matches the plan's gate reports. No text is copied. |
| Persistent log (B3) | The log-capture guide | A reserved storage region read back by a reader, and a QR export of the firmware log (U50). | BSD-2-Clause-Patent (QR path uses Mu libraries) | sheng records | Ideas | **Cite.** A reserved region on the boot stick, read back on the Mac, fits D9 (no internal-disk writes) and complements the DRAM-retention test (R5). The QR path needs Mu's logger libraries and is not proposed. |
| Host-first testing | QEMU entry fixtures with an HVF observation | A generated device tree and a register stop-point report for a kernel under QEMU (U42). | BSD-2-Clause-Patent | Several QEMU scenarios | Ideas | **Cite** as an idea for Route B only; our own oracles live in `tools/`. |

## 7. Facts that bear on plan unknowns

"Settles" says whether the fact closes a plan question. None does for the silicon questions: every fact below is from
another SoC, mostly a phone, and several concern an OS at EL1 that our design does not use.

| # | Fact | Source ids | Label | Plan item | Settles? |
| --- | --- | --- | --- | --- | --- |
| HF1 | An EL1 kernel under Gunyah cannot use the physical timer (write to `CNTP_CTL_EL0` is UNDEF) and runs on the virtual timer. | U28, U29 | OBSERVED (one device) | Timer row of the plan's section 4.1; Route B | No for Route A (our monitor owns the timer at EL2; X2 T03 measures ECV and `CNTVOFF_EL2`). For Route B it fixes the kernel's timer choice. |
| HF2 | Qualcomm SoCs run the architected counter at 19.2 MHz. | U16, U39 | OBSERVED (other SoCs) | E26 (`CNTFRQ_EL0` 19.2 MHz on another X1E80100) | No: consistent with E26; this unit is unread. |
| HF3 | Apple userland assumes a 24 MHz timebase (CoreAudio, Swift deadlines at 1.25 times on 19.2 MHz). | U17, U18 | OBSERVED | C3; `SNAP_KERNEL_NATIVE_COUNTER` | Yes, as a warning: a native 19.2 MHz guest timebase must not reach userland. |
| HF4 | SGIs may not be delivered to an EL1 kernel under Gunyah, so IPIs can be software-pended SPIs. | U30 | OBSERVED (one phone) | GIC row; E24 | No: at EL2 our monitor owns the GIC. Relevant to Route B only. |
| HF5 | MSI can avoid the ITS by using the PCIe controller's own receiver; upstream says Gunyah's ITS emulation is broken at EL1. | U37, U57 | OBSERVED; VERIFIED | E24; the Wi-Fi study | No: consistent with E24, and a pointer to the Wi-Fi study. |
| HF6 | A hypervisor can keep SMMU context banks, virtualise an apps SMMU and emulate a GPU SMMU's global space. | U33 | OBSERVED (three records) | R9, E36, K4 | No: all three are SMMUv2 or MMU-500 instances on phones. The SP11's PCIe SMMUv3 state stays UNKNOWN (X2 T07). |
| HF7 | EL1 can write PAC keys under the hypervisor when `HCR_EL2.API` and `APK` are set. | U34, U11 | OBSERVED (one phone, Cortex cores) | C4 | No: not an Oryon v1 core, and at EL2 our monitor sets those bits itself. |
| HF8 | Source-built XNU runs at EL1 on Oryon-class phone SoCs (SM8750 and SM8850); the Oryon configuration needs PAN handling. | U26, U27 | OBSERVED (single units) | WS-C, D4 | No: plausibility only. No ID-register value (NV, ECV, DCZID) is published in the pinned records, so those stay UNKNOWN for Oryon v1. |
| HF9 | `DC ZVA` to a plain (non write-combine) mapping of GPU memory aborts. | `UX docs/devices/uke-tonghuaqwq.md:394` | OBSERVED (one phone) | E16, E30, R7 | No: it says nothing about `DCZID_EL0` or XNU's `bzero`. UNKNOWN stays. |
| HF10 | PSCI conduit is read from the device tree and is `smc` on SM8550. | U29, U39 | VERIFIED; OBSERVED | E4 | No: consistent with E4; not measured on Hamoa. |
| HF11 | The loader needs EL1 with the firmware's tables on entry and turns the MMU and caches off itself. | U04, U05 | VERIFIED; OBSERVED | Firmware entry row | No: it describes Mu-Silicium phones, not the Surface UEFI. |
| HF12 | A software watchdog in Gunyah must be serviced or disabled by an EL1 OS; one UniXNU port lists an "independent Gunyah watchdog" as a gap. | U57, `UX docs/devices/zorn.md:436` | VERIFIED; OBSERVED | X2 item 9 | No: X2 item 9 is about the APSS watchdog at EL2; the EL1 case is UNKNOWN. |

## 8. Licensing and provenance

### 8.1 Licence of each component cited

| Repository | `LICENSE` file | Other licences in the tree | Derived from | Could be reused as code? |
| --- | --- | --- | --- | --- |
| UniXNU (`UX`) | BSD-2-Clause-Patent (`UX README.md:51`) | MIT files; GPL-3.0-or-later in `scripts/appsync/`; Apache-2.0 WITH LLVM-exception in an LLVM patch | n/a | Permissive parts, with notice. Nothing proposed. |
| Kernel patch series | Unified diffs against Apple's XNU; the two sampled carry no licence header (`zzz-boot-progress.patch` carries an MIT SPDX line, and the Mesa patches carry MIT) | n/a | Apple's open XNU (APSL-2.0, the plan's section 12 row for XNU) | A patched kernel is an APSL-2.0 derivative; UniXNU's docs do not say so (a search of the pinned tree finds no APSL statement). INFERRED. Never copy. |
| DarwinBoot, ArmCpu, ArmDTPlatform, GopFramebuffer, QcomGeniSpi, VerifiedRoot, XnuPrelink, RamBlock, ShengHidInput | BSD-2-Clause-Patent (line 3 of each `LICENSE`; `GF LICENSE:3`) | GopFramebuffer has one MIT header | Original | Yes with notice; no use is proposed. |
| ArmSmmu4K (`AS`) | MIT (`AS LICENSE:1`) | n/a | Original | Yes; SMMUv2 only, so not useful. |
| UniXNU-sm8550-common (`SC`) | BSD-2-Clause-Patent | `dts/sm8550.dtsi` is BSD-3-Clause (Linaro) | A Linux-derived SM8550 tree (`SC soc.toml:6`) | Facts only; re-derive from Linux. |
| UniXNU-sheng (`UD`) | BSD-2-Clause-Patent | `scripts/gen-qmp-usb3-table.py` is GPL-2.0-only (`UD scripts/gen-qmp-usb3-table.py:42`) | Original; Linux tables | Facts only. |
| QcomDwc3 (`DW`) | GPL-2.0 text, plus BSD-2-Clause-Patent in `LICENSES/` | PHY headers GPL-2.0-only (`DW README.md:9`) | Linux drivers; Apple's USB class interfaces | No: copyleft islands. |
| ShengUfs (`SU`) | BSD-2-Clause text (`SU LICENSE:1`) | Every source carries BSD-2-Clause-Patent | Original | Ambiguous: the two readings differ in the patent grant. Treat as the narrower reading, or ask. |
| ShengNovatekTouch (`NT`) | Apache-2.0 with a `NOTICE` (`NT NOTICE:3-4`) | n/a | A study of an Apache-2.0 project | Yes with the `NOTICE`; not relevant. |
| macOS-ForSheng (`FS`) | GPL-3.0 | n/a | UniXNU, Snapintosh, DroidVM (`FS README.md:5`) | No. |
| macOS-DroidVM (`DV`) | None declared | n/a | UniXNU | No licence means no permission. |

### 8.2 What the survey implies for AGENTS.md rules 1 and 4

- **Rule 1 (vendor and reconstructed private inputs).** UniXNU keeps restore images, `.im4p`, `.macho`, firmware and keys out
  of Git (U52), which matches our rule. It also commits derived ABI records and pins kernelcache hashes and function
  addresses (U54, `UX profiles/os/macos-27.3-26C5035r/profile.toml:51`); under our rule 1 these are "disassembly, vtable and
  ABI dumps" that Snapintosh must not commit, and for another Apple build (ours is 26A428) they are not evidence either. The
  macOS-DroidVM CI uploads artifacts that contain Apple kernelcache components, encrypted (U55); that practice is exactly
  what rule 1 forbids for us. None of this data was copied or recorded here beyond file names.
- **Rule 4 (licences and attribution).** The permissive licences allow reuse with notices, but the GPL-2.0-only headers
  (PHY tables), the GPL-3.0-or-later AppSync scripts and the GPL-3.0 macOS-ForSheng repository must never enter MIT files.
  The ambiguous ShengUfs licence needs a decision before any reuse.
- **What stays a cited fact.** Every OBSERVED hardware statement, every Linux-derived register number (re-derive from
  Linux, as the other studies do), and every device record. **What could be code:** nothing is proposed. If the owner later
  approves reuse of a permissive piece, it needs a `THIRD_PARTY_NOTICES.md` row with the source, commit and licence, kept
  in a separate file as rule 4 requires.
- **Inputs a Route B build would require and UniXNU never ships:** the Apple restore image members, the original
  kernelcache, trust caches, a Data seed, Xcode with the iPhoneOS SDK, and board firmware (U53). All would stay under
  ignored local paths.

## 9. Open questions and what would settle them

| # | Question | What would settle it | Recommended default |
| --- | --- | --- | --- |
| OQ1 | Does the owner want a Route B design study at all? | An owner decision after K1 or K2 fires. | No. Record only, as section 5.7 says. |
| OQ2 | Is `CurrentEL` at the Surface UEFI really EL1, and what does the hypervisor own (timer, GIC, SMMU, watchdog, PAC keys)? | Running the existing EFI probe without slbounce, which prints `CurrentEL` and stops at `NOT AT EL2` (richer EL1 reads would be a new probe); it needs the same X1 authorizations. | Do not run anything for this study; the item belongs to X1. |
| OQ3 | Is the software watchdog in Gunyah armed at hand-off and serviced by firmware? | A read-only measurement at EL1 (Route B) or the plan's X2 item 9 (EL2). | UNKNOWN until measured. |
| OQ4 | Do userland programs that assume 16K pages (V8, PartitionAlloc) misbehave in 4 KiB-forced processes under `SNAP_KERNEL_USER_4K`? | A host or A16 test that runs such a program with the option on. | Add to the A16 backlog; not an SP11 gate. |
| OQ5 | Would a 19.2 MHz guest timebase ever be exposed to userland by the deferred native-counter patch? | The patch's own design review against U16 to U18. | Keep the userland timebase at 24 MHz. |
| OQ6 | What is the real licence of ShengUfs? | Asking its author, or a decision to avoid it. | Avoid it; no reuse is proposed. |
| OQ7 | Is the SM8850 core family the same as the A16's? | The pinned catalog does not say; Qualcomm public documentation would. | Treat as INFERRED. |
| OQ8 | Does any record show macOS beyond Recovery on bare metal? | A newer status record of macOS-ForSheng, or another repository. | Re-check at the next pin. |

## 10. What this study does not establish, and how it is checked

This study establishes what the pinned sources say. It does **not** establish that UniXNU builds or boots anything, that
any UniXNU result reproduces, anything about the Surface Pro 11's firmware beyond what the plan already infers, or whether
Route B would work. Every OBSERVED row is a third party's report, usually one unit.

`python3 tools/test-sp11-unixnu-study.py` reads this file and [SP11-UNIXNU-EVIDENCE.md](SP11-UNIXNU-EVIDENCE.md) as one study
and checks, without hardware or network: the section headings and anchors; that every
evidence row has exactly one allowed label and a source with a pinned key; that the pin table is complete and well formed;
that the headline numbers are present; that the study keeps its "does not recommend switching" stance and contains no
recommending phrase; that every citation into this tree points at a range holding a distinguishing token; that the plan
documents still hold the decision, kill-criterion and risk text this study refers to; that the two files link to each other,
stay under 50 KiB and carry the status line; and that links and hygiene hold. With
`SP11_UNIXNU_REF=<directory holding the pinned checkouts>` it also checks every UniXNU citation against the pinned file, the
LICENSE verdicts, and recomputes the headline numbers from `devices.toml`; with `SP11_LINUX_TREE=<checkout>` it checks the
Linux citations. Without those variables the checks that need them are reported SKIP, and a SKIP is not a pass.
Hardware status: NOT RUN.
