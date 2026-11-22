<!-- SPDX-License-Identifier: MIT -->
EXPERIMENTAL - a plan and a host-side preparation; nothing in it is hardware-validated and nothing in it authorizes a hardware step

Version 3 of this plan ([SP11-GPU-PORT-PLAN-V3.md](SP11-GPU-PORT-PLAN-V3.md), with the hardware stages E1 to E7 in [SP11-GPU-BRINGUP-STAGES.md](SP11-GPU-BRINGUP-STAGES.md)) applies an independent fact-check to this one. This document stays the inventory (the K, P and W ids); where v3 supersedes a statement here it is marked "v3" in place, and v3's section 0 lists them. Hardware status is unchanged: boots 1 to 31, nothing newer run.

# SP11 GPU port plan: the kext, the Metal plug-in and the first contact with the X1-85

This is the planning half of workstream F items F5 (the Adreno kext) and F6 (the Metal plug-in) of
[SURFACE-PRO-11.md](SURFACE-PRO-11.md), written from the code of the two component repositories, plus the
first-contact safety plan for the gates X8a to X8e (section 7). It builds on
[SP11-GPU-A7XX-STUDY.md](SP11-GPU-A7XX-STUDY.md) (the a7xx against a8xx delta, called "the study" below) and
[SP11-GPU-UNIXNU.md](SP11-GPU-UNIXNU.md) (an independent a7xx stack). It was written without hardware: every hardware
status is NOT RUN, except the one read-only payload run of [section 7.1.1](#711-what-boot-30-showed-stage-s0-reads-r2-to-r4-observed-once)
(boot 30), and the ABI checks that need the Apple KDK are SKIP, because the KDK is not on the host that
wrote this.

## Key findings

1. **The kext has no GPU register code** [OBSERVED]. SnapAdreno contains no GMU, HFI, RSC, SQE or microcode, UBWC
   configuration, CP_PROTECT table, DVFS table, zap or thermal code: a text search of its sources, profiles, plist and
   build script finds none (section 2.3 lists the hits and why each is not one). It reaches the GPU only through the
   monitor's guest ABI. Its X2 coupling is the chip-id gate, the IOKit names, the GPU address window, the display
   panel and what it compiles from the umbrella (the IB firewall and its generated tables, the contract header).
2. **The plug-in's command builder is a function-for-function port of Mesa's `<A8XX>` code** [OBSERVED]. Its comments
   name 22 distinct Mesa functions instantiated for A8XX, and it programs 315 distinct registers. Against the
   pinned Mesa XML [OBSERVED, host-computed, method in Appendix A]: 202 sit at the same offset on A7XX (193 with
   identical bit fields, 9 with different fields), 88 moved, 25 have no A7XX register of that name. 122 of 315 (39 %)
   therefore need a per-register decision, and a header generated for A8XX cannot be reused for A7XX.
3. **The reference for every function exists in public source** [VERIFIED]. Mesa at the pin instantiates the same
   functions for `CHIP == A7XX`; the files that hold them carry 5 to 26 chip-conditional branches each (section 2.2).
   The a8xx builder was "hardware-proven" against q1n1's python builder; no such proof exists for a7xx, so the a7xx
   builder needs a q1n1 reference proven on the unit first (risk R1).
4. **Seven constants name the X2 outright** [OBSERVED]: the kext's chip id, the plug-in's device name and runtime chip
   id, the sidecar chip id the plug-in demands from the compiler, the compiler chip it creates (0 = default profile),
   8 CCUs and 21 MiB of GMEM in the cache-offset calculation, and the 2880 x 1800 at 60 Hz panel (section 2).
5. **The a7xx descriptors differ** [VERIFIED]: T# and S# use the A6XX layouts; none of the 37 shared T# field names
   sits at the same position (study section 2.14). The compiler-side half of the S# encoder is now implemented and
   host-tested (section 4); the plug-in's T#/S# packers are plan items.
6. **Most shared seams are not in these repositories and have no owner** [OBSERVED]. The register-header generator, the
   contract header's static assertions and the IB firewall's tables live in the umbrella; the brief for this work
   forbids changes under `compat/`, so those items are plan items with measured sizes (section 3), not code.
7. **Host baseline** [OBSERVED]: of the 31 umbrella tests that name the three components, 11 pass outright, 13 need a built
   plug-in (the Metal toolchain is absent) and the rest pass in part, fail for umbrella reasons, skip on the missing KDK or
   cannot run alone (section 8). No test ran on hardware.

## 0. How to read this document

### 0.1 Evidence labels and work classes

The labels are those of the study (section 0.1): VERIFIED (read in a cited public source or measured on the host,
never a hardware result), OBSERVED (seen in the code or measured on the host), INFERRED (reasoned; the premise is
named), EXPERIMENTAL, UNIMPLEMENTED, UNKNOWN. A label on a heading covers the rows under it.

Every work item of section 3 carries one class, the first thing that blocks finishing it:

| Class | Meaning |
| --- | --- |
| (a) | Host-testable now from VERIFIED public sources (Mesa, Linux), no private input needed |
| (b) | Blocked on the Apple KDK or on private ABI headers (the `*26A428.h` set), which are not on this host: ABI checks are SKIP |
| (c) | Blocked on hardware (a read-back or a run on the unit), or on a value only the unit can give |

### 0.2 Sources

| Key | What |
| --- | --- |
| `KEXT` | SnapAdreno at `a2d43d0c9d9d6b0035d3da9f361d7ccfa5bfd37e` (the umbrella's `gpu/adreno-kext/kext` pointer at `b619097`) |
| `PLUG` | SnapMetal at `8fe64cb8599dcb37e6d922eb9204477d60a7c719` (`gpu/adreno-kext/plugin`) |
| `A2I` | air2ir3 at `55865cc84c1523d48fa469fff693c2164bf9c1b9` (`gpu/air2ir3`) |
| `REPO` | The umbrella at `b619097`; paths from its root |
| `LNX`, `MESA`, `FW` | As in the study (section 0.2): Linux `7475ea8b58616b33c04afb976cf0fbaad9dd63fb`, Mesa `bbeb2dca9feed6ac88d71234a9dc5ae8aeed4275` (paths under `src/freedreno/`; gallium files under `src/gallium/drivers/freedreno/a6xx/`), linux-firmware text |
| `UNX` | [SP11-GPU-UNIXNU.md](SP11-GPU-UNIXNU.md) |
| `STUDY` | [SP11-GPU-A7XX-STUDY.md](SP11-GPU-A7XX-STUDY.md), by section |

Files read for the first time here (Linux `drivers/clk/qcom/`: `gpucc-x1e80100.c`, `gcc-x1e80100.c`, `gdsc.c`, `gdsc.h`,
`clk-branch.h`, `clk-rcg2.c`; `drivers/iommu/arm/arm-smmu/arm-smmu.h`) have their SHA-256 in Appendix B; every other pinned file
is in the study's Appendix B and was re-fetched and re-hashed: the hashes matched. No Apple, Qualcomm, Microsoft or ASUS
material was read or is reproduced; comments in the kext that quote private kernelcache offsets are named, not quoted.

## 1. What each repository owns

| Repository | Owns | Does not own |
| --- | --- | --- |
| `KEXT` (10,604 lines, 49 files) | The IOGPU accelerator subclass, the draw and fence submission to the monitor, resource residency and the GPU address allocator over the monitor's mapped window, GPU recovery policy, the IOMobileFramebuffer display with beam-synchronised presentation, the optional backlight service | Any register of the GPU, the GMU, the SMMU or the display engine (the monitor and q1n1 write them); the IB validator, its tables and the register header (compiled from the umbrella) |
| `PLUG` (23,275 lines, 109 files; `harness/` has 44 files, 40 of them programs: v3 3.3) | The Metal device, command buffers and encoders; the PM4 builder (sysmem draw, blit, compute, clear, fill); descriptors; the shader-cache glue to air2ir3; staging arenas and the UBO mirror | The compiler (air2ir3), the validator that checks its output, the kext |
| `A2I` | AIR to ir3, the chip profiles | Everything else |

## 2. The inventory

Notation: `file:line` of the pinned repository. "a7xx" means A7XX variants in the pinned Mesa XML and the Linux a7xx
driver. Each row ends with its label and the work item of section 3.

### 2.1 SnapAdreno (the kext)

| # | Where | Assumption | a8xx / X2 value | a7xx / X1-85 equivalent | Label, item |
| --- | --- | --- | --- | --- | --- |
| K1 | `KEXT src/AdrenoX2/device.cpp:35` | Chip qualification: the guest GPU node's `chip-id` property must equal one constant, otherwise the device is refused | `0x44070041` (the X2-90 runtime id, four slices) | `0x43050c01`, the catalog id and the id Mesa's wildcard row `0xffff43050c01` matches (`LNX adreno/a7xx_catalog.c:376`; `MESA common/freedreno_devices.py:1283`; STUDY 1.3). That the loader publishes it as the node property is the tree builder's job: `REPO probes/adreno.py:70` hard-codes the X2 value | VERIFIED id; INFERRED property; W1 |
| K2 | `KEXT Info.plist:7,20,23,26-27`; `REPO gpu/adreno-kext/include/AdrenoContract.h:27-31` | IOKit identity: bundle id `org.snapintosh.driver.AdrenoX2`, class `AdrenoAcceleratorX2`, accelerator name `AdrenoX2`, match name `snapintosh,adreno-x2` (the node the stage-4 tree supplies) | X2 names | One kext with a profile, not a second bundle (STUDY 4.6). Whether the names stay is a decision of the owners (STUDY 5 #13). A new IOKit class or personality changes the ABI surface: KDK check needed | UNIMPLEMENTED; W1 (b) |
| K3 | `KEXT src/AdrenoX2.h:31-33`; `REPO compat/adreno.h:100-116`; `KEXT src/AdrenoX2/device.cpp:41-48` | The GPU virtual-address window the kext allocates from: the monitor's CB0 mirror. Default `[1 GiB, 4 GiB)` (T0SZ 32); `ADRENO_VA_WIDE` `[1 GiB, 16 GiB)`, i.e. 15 GiB (T0SZ 30), which also needs the wide monitor and a tree that names the width (`ADRENO_GPU_VA_BITS_PROPERTY`, `REPO compat/adreno-guest.h:73-75`). 16 KiB allocation alignment (`kAdrenoGPUVAAlign`) | Both windows are X2 choices over the X2's SMMU context bank 0 | The X1-85's `adreno_smmu` is an `arm,mmu-500`-compatible `dma-coherent` node at `0x03da0000` with stream ids 0 and 1, and neither catalog entry sets the 4 GiB-VA quirk (STUDY 2.4). Which window the SP11 SMMU leaves usable is UNVERIFIED: an X8a read-back | UNVERIFIED; W2 (c) |
| K4 | `KEXT src/AdrenoMemoryMap.cpp:165`; `PLUG AdrenoMetal/AdrenoStagingPolicy.h:9`, `AdrenoDrawCommands/UniformBuffers.m:8`, `AdrenoMetal/AdrenoResources.m:339` | Numbers tuned to a "3 GiB window": a 128 MiB pool, staging-arena sizes, the idle and hot budgets, the retry on exhaustion | 3 GiB window shared by all processes | Follow K3: a different window moves these budgets; they are policy numbers, not hardware facts | INFERRED; W2 (c) |
| K5 | `KEXT src/AdrenoX2/device.cpp:15-16,34-35,57-70` | The mapper: `iommu-parent` identifies a device mapper of 16 KiB pages (the Apple DART model of the A16); the kext never touches an SMMU register | A16 DART | The SP11 guest sees 16 KiB pages over a 4 KiB SMMU through the shadow layer (`REPO shadow/`); the kext-side contract is unchanged if the monitor presents the same mapper. Who owns the GPU SMMU at EL2 (Gunyah is present) is UNKNOWN (UNX Q04) | INFERRED; W2 (c) |
| K6 | `KEXT` builds `REPO compat/adreno-ib-command.c`, `adreno-ib.c`, `adreno-submit.c` (`build.sh`, `profiles/*.sh`) | The IB firewall: 20 admitted opcodes, 316 register names (284 draw + 32 compute) built from the A8XX header, the X2's 52-word protect table (`REPO compat/adreno-ib-tables.h`), the a8xx T# layout (`REPO compat/adreno-ib-command/validate.c:39-43,91-125`), the marker set | X2 | A7XX header, the 48-word `a730_protect` (generated, GPL-2.0-only: `REPO compat/adreno-a7xx-protect-tables.h`, included by nothing), the A6XX T# layout, the same three marker words. 90 of the 316 names moved and 23 have no A7XX register (STUDY 4.4). Owner unassigned (STUDY 5 #14); the first inclusion of the GPL header into a monitor or kext build is a licence decision | VERIFIED tables; W4 (a), kext link check W4 (b) |
| K7 | `REPO gpu/adreno-kext/include/AdrenoContract.h:105-134` (shared by kext and plug-in) | Static assertions tie the contract to A8XX names: T# base alignment, the `CP_RB_CNTL` BUFSZ range, `ADRENO_IB_RBBM_INT_ERRORS` (10 `RBBM_INT_0_STATUS` bits), `ADRENO_IB_CP_GLOBAL_FAULTS` (14 `CP_INTERRUPT_STATUS_GLOBAL` bits), the RBBM idle rule | A8XX | a7xx has no `CP_INTERRUPT_STATUS_GLOBAL`; it has `CP_INTERRUPT_STATUS` `0x823` and `CP_HW_FAULT` `0x821`; the RBBM status offsets moved (`RBBM_STATUS` `0x210`, `RBBM_INT_0_STATUS` `0x201`; STUDY 2.1) | VERIFIED; W5 (a) |
| K8 | `KEXT src/AdrenoDisplay.h:13,197-204`; `src/AdrenoDisplay.h:68-70` and `src/AdrenoDisplayBeam.h:2,52`; `src/AdrenoDisplay.h:140-142` | Display coupling: a fixed 2880 x 1800, 60 Hz, 32-bit BGRA mode (`kWidth`, `kHeight`, `kRefreshHz`), a 302 x 189 mm panel, the scanout position read from DPU INTF_5 (`DPU_INTF_PA == 0x0AE3A000`), the backlight over DPCD through the monitor | The A16's eDP panel and DPU | The SP11 reuses the firmware's eDP and DPU pipeline (PLAN E and P rows); its panel is 2880 x 1920 and the 120 Hz item is a stretch goal (X13). The INTF index, its base and the mode must come from the guest tree, not constants: UNKNOWN until X13a/X5 read them | UNKNOWN; W6 (c) |
| K9 | `KEXT src/AdrenoMemoryMap.cpp` (coherent-maintenance skipping), `REPO compat/adreno-coherent.h:1-25` | Cache maintenance is skipped only after the monitor proves IO-coherence at prepare time (`SNAP_ADRENO_COHERENT`, default 0) | X2 test | The X1-85 has the cached-coherent quirk and a `dma-coherent` SMMU node; the unit's own IORT says non-coherent (OBSERVED once, a table; v3 3.2); reuse the gated self-test and keep non-coherent leaves unless it passes (STUDY 2.10) | INFERRED; W7 (c) |
| K10 | `KEXT src/AdrenoX2/submission.cpp`, `pipeline.cpp`, `lifecycle.cpp`; `REPO compat/adreno-guest.h` | The guest ABI: HVC fence, FILL32, cache, IB, recover, stop. No register appears; the ring and the CP are the monitor's | chip-independent | Unchanged for a7xx as long as the monitor keeps the ABI; the a7xx CP work (SQE start, CP init, protect programming) is monitor and q1n1 work (F2, F3), not kext work | OBSERVED; none in the kext |

### 2.2 SnapMetal (the plug-in)

| # | Where | Assumption | a8xx / X2 value | a7xx / X1-85 equivalent | Label, item |
| --- | --- | --- | --- | --- | --- |
| P1 | `PLUG AdrenoMetal/AdrenoMTLDevice.m:155,158,243` | Device and product name; the target data handed to Metal's compiler object | `Adreno X2-90`; runtime chip id `0x44070041` ("opaque to Metal") | `Adreno X1-85`; `0x43050c01` (catalog id; the speed bin goes in the upper word on read, STUDY 1.3) | VERIFIED id, INFERRED use; W9 (a) |
| P2 | `PLUG AdrenoMetal/AdrenoShader.m:78` | The compiler is created for chip `0`, i.e. the default profile | air2ir3 default `x2-90` | `p_air2ir3_compiler_create(0x43050c01)` selects the `x1-85` profile that exists in `A2I` on the clone's branch `sp11-prep` (commit `cd56b66`, `air2ir3: add chip profiles and the opt-in x1-85 profile`) | VERIFIED; W9 (a) |
| P3 | `PLUG AdrenoMetal/AdrenoDrawCommands/Compute.m:180-182,229`, `ProgramExports.m:154,156` | The plug-in refuses any sidecar whose `chip_id` is not the string `"0x44070041"`; compute requires ABI 6 and the THREAD64 or THREAD128 variant; the UBO table is 16 entries ("a8xx honours") | X2 | The sidecar carries `0x43050c01` for `x1-85` (A2I); gen 7 passes compute driver params in constants, not a UBO (`A2I BINDING-ABI.md`, x1-85 note, OBSERVED there); the 16-entry table is assumed, not shown, on a7xx | EXPERIMENTAL; W9, W16 (a) |
| P4 | `PLUG AdrenoMetal/AdrenoDrawCommands.h:4-9`, `.m:2-5` and the parts it includes | The builder is "a function-for-function port of q1n1's hardware-proven command builder" (`tools/a16gpu_render.py`, `a16gpu_p6p7.py`, `a16gpu_bindings.py`); packages equal `RD.build_ib` dword for dword once addresses are normalised | a8xx sysmem batch | Re-derive from Mesa's `<A7XX>` instantiation of each function (the list below), against a q1n1 a7xx reference proven on the unit first | OBSERVED; W11-W17 |
| P5 | `PLUG AdrenoDrawCommands/SharedPassState.m:10-31` | `fd6_calc_gmem_cache_offsets` for the X2-90: 8 CCUs, 21 MiB GMEM, colour 128 KiB, depth 192 KiB, attr 128 KiB, pos 64 KiB, bv-pos 32 KiB; writes `RB_CCU_CACHE_CNTL` and nine `*_BUF_GMEM_*` registers | X2-90 | The X1-85: 6 CCUs (Mesa `num_ccu`, STUDY 3.1), 3 MiB GMEM (STUDY 1.1). Six of the nine `*_BUF_GMEM_*` registers (`VPC_POS_BUF_GMEM_BASE/SIZE`, `VPC_BV_POS_BUF_GMEM_BASE/SIZE`, `PC_POS_BUF_GMEM_SIZE`, `PC_BV_POS_BUF_GMEM_SIZE`) have no A7XX register of that name | VERIFIED; W12 (a) |
| P6 | `PLUG SharedPassState.m:72-98` | Static state: Mesa's `a8xx_base_raw_magic_regs` (22 raw writes), then the static non-context and context sets (`RB_UNKNOWN_8818..881E`, `SP_UNKNOWN_AB01/AB23`, `PC_UNKNOWN_980B` ...) | a8xx magic | `a740_magic_regs` (3 named, all zero) and `a740_raw_magic_regs` (36 raw writes), none inside the 48-word protect table (STUDY 3.4; `MESA common/freedreno_devices.py:965-1012`). `SP_UNKNOWN_AB23` and `PC_UNKNOWN_980B` have no A7XX register of that name | VERIFIED; W11 (a) |
| P7 | `PLUG Textures.m:6,34-86,125-149,334,470`; `Compute.m:415-422` | S# and T# are packed with the A8XX layouts: `A8XX_TEX_SAMP_0..2` (flags in dwords 0 and 1: `MIPMAPING_DIS`, `REDUCTION_MODE`, `COMPARE_FUNC`, ...) and `A8XX_TEX_MEMOBJ_0..7` (base in dwords 0-1, format in dword 3); the storage descriptor reuses the T# | a8xx | A6XX layouts: S# as built by `A2I src/a7xx_sampler.c` (dwords 0-2, `MIPFILTER_LINEAR_FAR`, reduction and fast border colour in dword 2); T# base in dwords 4-5, format in dword 0 (STUDY 2.14) | VERIFIED; W10 (a) |
| P8 | `PLUG Textures.m:334,470`; `ProgramExports.m:118,129` | The sidecar key `a8xx_tex_samp` carries the compiler's constexpr-sampler words | a8xx | A key for A6XX-layout words (proposed `a6xx_tex_samp`) or one chip-neutral key; it is an interface between A2I and `PLUG` | UNIMPLEMENTED; W18 (a) |
| P9 | `PLUG CommandStream.m:54-70,170-176` | Every register, field, packet dword and descriptor field is reached through `A8XX_REG_<NAME>__<FIELD>__SHIFT/WIDTH/SHR`, `A8XX_PKT_*`, `A8XX_DESC_*` and `A8XX_E_*`, from `REPO gpu/adreno-kext/include/AdrenoA8xxRegs.h`; `REG32/REG64/REGADDR` assert the register's bit size | A8XX header | A header generated for A7XX (W3) and a prefix switch in these macros. `perf/capture.py:120-132` parses the `A8XX_` names textually and needs the same switch | OBSERVED; W3, W11, W20 (a) |
| P10 | `PLUG CommandStream.m:213-215,266-292` | `PKT4`/`PKT7` header packers and counts, `CP_CONTEXT_REG_BUNCH`, `CP_NON_CONTEXT_REG_BUNCH` | Same bit layout in every generation (STUDY 2.14, `LNX adreno/adreno_gpu.h:733-757`) | Unchanged | VERIFIED; none |
| P11 | `PLUG CommandStream.m:299-308`; `RenderPass.m:37,399-411,516`; `DrawEncoding.m:383` | `CP_SET_MARKER` word, the BR-thread patchpoint, sysmem prep, sysmem, fini; the kext ring selects `CP_SET_THREAD_BOTH` before the IB | A8XX layout: mode in bits 6..0, uses-GMEM in bit 7 | A6XX-A7XX layout: mode bits 3..0, uses-GMEM bit 4, three ray-tracing workaround bits at 9-11; the three marker words the validator accepts (modes 1, 8, 12) are the same numbers (STUDY 2.14) | VERIFIED; W13 (a) |
| P12 | `PLUG AdrenoResources.m:137`; `RenderPass.m:459`; `SharedPassState.m:260` | v1 renders linear, no UBWC, no tiling (`adrenoFormat` 0 = "linear, no UBWC"; depth TILE6_3 without UBWC) | Tiling and UBWC off | Same plan: UBWC is a later stage (X1E80100 is UBWC 4.0, bank bit 16; STUDY 2.5) | INFERRED; W22 (c) |
| P13 | `PLUG AdrenoResources.m:335` | `didModifyRange:` does nothing: "coherent CPU/GPU on X2E (IO-coherent GBIF)" | X2 | Valid only if the X1-85 path proves coherence (K9); otherwise the shadow-surface maintenance applies | INFERRED; W7 (c) |
| P14 | `PLUG AdrenoCodeScan.h:2-30` | The store classifier for the UBO mirror reads ir3 category-6 encodings (Mesa `ir3-cat6.xml`, gen >= 600); its ground truth `perf/host/test-codescan.c` decodes with `gpu_id` 800 | a8xx decoder | The text says gen >= 600 for every form; the check must be re-run with `gpu_id` 700 against Mesa's decoder, which needs the Mesa build (Mako, section 8) | INFERRED; W19 (a) |
| P15 | `PLUG perf/costmodel.py:3`; `PLUG harness/*` (44 files) | Cost-model parameters are guesses for the X2-90; the harnesses drive the A16 (guest probes) or compare against the host's own Metal | X2 | Re-calibrate after X8b; the host-Metal comparison harnesses need the Metal toolchain | EXPERIMENTAL; W20, W21 |

Mesa functions the plug-in's comments name as the a8xx source of each part (22): `fd6_lrz_inv`, `fd6_emit_restore`,
`fd6_emit_static_regs`, `fd6_emit_static_non_context_regs`, `fd6_emit_static_context_regs`,
`fd7_emit_static_binning_regs`, `fd6_emit_flushes`, `fd6_clear_surface`, `emit_blit_setup`, `emit_blit_dst`
(all in `SharedPassState.m`); `setup_config_stateobj`, `emit_shader_regs`, `emit_fs_outputs`, `setup_stateobj`,
`emit_fs_inputs`, `emit_interp_state` (`ProgramState.m`); `fd6_emit_sysmem_prep`, `fd6_emit_sysmem`,
`fd6_emit_sysmem_fini` (`RenderPass.m`, `DrawEncoding.m`); `fd6_zsa_state_create` (`DrawEncoding.m`);
`fdl6_view_init` and `fd6_sampler_state_create` (`Textures.m`). The chip-conditional branches (`CHIP >=/<=/==
A7XX|A8XX`) per Mesa file at the pin [VERIFIED, counted on the host]: `fd6_emit.cc` 26, `fd6_program.cc` 26,
`fd6_gmem.cc` 24, `fd6_rasterizer.cc` 11, `fd6_image.cc` 8, `fd6_barrier.cc` 6, `fd6_texture.cc` 4, `fd6_zsa.cc` 4,
`fd6_compute.cc` 3, `fd6_blend.cc` 2, `fd6_blitter.cc` 2, `fd6_query.cc` 2, one each in `fd6_const.cc`,
`fd6_context.cc`, `fd6_draw.cc`, and 11 more in the headers. They size the port; they say nothing about which
branch the X1-85 takes, which `fd_dev_info` properties decide (STUDY 3.3).

Registers the plug-in programs, by block [OBSERVED, host-computed]: SP 98, RB 80, GRAS 64, VPC 22, TPL1 18, VFD 18, PC 15
(315 distinct names used through `REG32/REG64/REGADDR/REGI`, Appendix A; the same block totals as the study's 316 IB names
except one RB name). The 25
with no A7XX name: `GRAS_BIN_FOVEAT_XY_FDM_OFFSET`, `GRAS_LRZ_BUFFER_SLICE_PITCH`, `GRAS_RAST_CNTL`,
`PC_BV_POS_BUF_GMEM_SIZE`, `PC_POS_BUF_GMEM_SIZE`, `PC_UNKNOWN_980B`, `PC_VS_INPUT_CNTL`, `RB_DEPTH_GMEM_DIMENSION`,
`RB_LB_PARAM_LIMIT`, `RB_MRT_GMEM_DIMENSION_REG`, `RB_PS_CNTL`, `RB_RESOLVE_CNTL_5`, `RB_STENCIL_GMEM_DIMENSION`,
`RB_VIEWPORT_ZCLAMP_MAX_REG`, `RB_VIEWPORT_ZCLAMP_MIN_REG`, `SP_ALPHA_TEST_CNTL`, `SP_BIN_SIZE`,
`SP_MRT_BLEND_CNTL_REG`, `SP_UNKNOWN_AB23`, `TPL1_A2D_BIN_SIZE`, `TPL1_BIN_SIZE`, `VPC_BV_POS_BUF_GMEM_BASE`,
`VPC_BV_POS_BUF_GMEM_SIZE`, `VPC_POS_BUF_GMEM_BASE`, `VPC_POS_BUF_GMEM_SIZE`. Whether an equivalent exists under another
name is not determined (the same limit as STUDY 4.4). The 9 same-offset registers whose bit fields differ: `RB_BUFFER_CNTL`,
`RB_CCU_CACHE_CNTL`, `RB_DEPTH_CNTL`, `RB_RESOLVE_CNTL_3`, `SP_BLEND_CNTL`, `SP_PS_WAVE_CNTL`, `SP_UPDATE_CNTL`,
`TPL1_A2D_BLT_CNTL`, `TPL1_MODE_CNTL`. By file, the registers written per source file (distinct per file): `SharedPassState.m` 89,
`ProgramState.m` 78, `DrawEncoding.m` 71, `RenderPass.m` 53, `Compute.m` 36, `Blit.m` 22, `Textures.m` 3.

### 2.3 The ten categories of the brief

Where each lives and what its a7xx counterpart is. "Not in the two repositories" is an OBSERVED text search (hits and
why they are not code: `HFI` and `GMU` none; `DVFS` and `governor` appear only in comments about a presentation finding
(`AdrenoDisplay.h:78`, `AdrenoDisplayPresent.h:32`); `protect` is a C++ access specifier or an IOGPU ivar name
(`AdrenoDisplay.cpp:106`, `IOGPU.tbd`); `firmware` means the display firmware (GOP, backlight); `UBWC` appears only in
"no UBWC" notes (P12)).

| Category | In `KEXT` / `PLUG`? | Where it lives today | a7xx counterpart and source | Item |
| --- | --- | --- | --- | --- |
| Register offsets and blocks | `PLUG` programs 315 names by macro (P9); `KEXT` none | `REPO compat/adreno.h:52-72` (monitor PAs: `RBBM_STATUS` `0x03d00048`, `CP_SQE_CNTL` `0x03d02054`, GMU power status `0x03d7dfa0`, CX GDSC `0x03d99084`); q1n1 `tools/adreno_x2.py` | `RBBM_STATUS` `0x03d00840`, `RBBM_INT_0_STATUS` `0x03d00804`, `CP_SQE_CNTL` `0x03d02020`, `GMU_SPTPRAC_PWR_CLK_STATUS` `0x03d7e340`; the CX GDSC is GPUCC base `0x03d90000` + `0x9108` and the GX GDSC + `0x905c` (STUDY 2.1; `LNX drivers/clk/qcom/gpucc-x1e80100.c` gdsc table; arithmetic, INFERRED for the sum) | W3, F2 |
| GMU and HFI handshake | none | Monitor (`REPO compat/gpu-dvfs.h:14-17`: HFI `GX_BW_PERF_VOTE`), q1n1 `tools/a16gpu_stage3.py` (gmu-smmu, gmu-boot, hfi, oob) | a7xx: the host programs the GPU RSC and two PDC registers (19 writes with a8xx returning first, UNX), fixed-layout HFI performance table, protocol "1.10" only on the legacy path, none on the non-legacy start (v3 3.1 row 7) (STUDY 2.3; `LNX adreno/a6xx_gmu.c:692-745,798-800`, `a6xx_hfi.c:247-255,331-353`) | F2 (c); X8a stages in section 7 |
| CP microcode and firmware names | none | q1n1 loads GMU and SQE; README "Device firmware" | `gen70500_sqe.fw` 1.62, `gen70500_gmu.bin` 4.03.11 (WHENCE text); no zap name in the catalog, the Denali image `qcdxkmsuc8380.mbn` is device-bound (STUDY 1.5, 2.7). Supplied locally, never committed | F2 (c) |
| UBWC configuration | none (off in v1, P12) | `LNX` is the only writer on a7xx (`a6xx_gpu.c:748`, called at 1344); the A16 path skips it | X1E80100: UBWC 4.0, bank bit 16 (`LNX drivers/soc/qcom/ubwc_config.c:65-68,179-180`); Mesa `highest_bank_bit` 16 (STUDY 2.5) | W22 (c) |
| IB and command-stream formats | `PLUG` (P4-P11); `KEXT` compiles the validator | `PLUG` builder, `REPO compat/adreno-submit.h` wire format, the validator | PKT4/PKT7 unchanged; the opcode set (20) is defined for A7XX; `CP_SET_MARKER` layout differs, same three words (STUDY 2.14). Register content is the port (section 3) | W3-W17 |
| Protect table | none | `REPO compat/adreno-ib-tables.h` (52 words, GPL-2.0-only); generated 48-word table (`compat/adreno-a7xx-protect-tables.h`, `tools/gen-a7xx-cp-protect.py`) | `a730_protect`, 48 slots, shared by six catalog entries (STUDY 2.6). Programmed by the CP init path, not by the kext | W4; licence decision |
| Chip and revision ids | K1, P1-P3 | `REPO probes/adreno.py:70`, `tools/cycle.py` (`--gpu-x290` help), `tools/test-adreno-image.py:149`, `tools/plan-adreno-native-triangle.py:46` | `0x43050c01` (Linux, Mesa); GMU chip-id word `0x7050001`; the run-time id has no slice OR on a7xx (STUDY 1.3, 3.2) | W1, W9 |
| DVFS tables | none | `REPO compat/gpu-dvfs.h` (monitor governor; the A16 table stops below 1850 MHz, default ceiling 1700 MHz), q1n1 `--max-opps` | 15 OPPs in `hamoa.dtsi:4046-4170` gated by speed bin; only 550, 390 and 300 MHz are valid for every bin (STUDY 2.12); the ceiling is the operator's decision | F2 (c); section 7 |
| Display coupling | K8 | `KEXT src/AdrenoDisplay*`; `REPO compat/dpu-intf.h`, `edp-backlight.h` | PLAN E and P rows; 2880 x 1920, OLED, 120 Hz stretch | W6 (c) |
| The 15 GiB window | K3, K4 | `REPO compat/adreno.h:100-116`, `compat/sptm-dart.h:8-15`, `compat/dart-wiredcnt.h:92-98` | UNVERIFIED for the SP11 SMMU (STUDY 2.4) | W2 (c) |

## 3. Work breakdown

Size: S = under a day, M = a few days, L = a week or more of focused work. "Depends" names items of this table. An item
whose implementation is class (a) but whose qualification needs the unit says so in the last column.

| Item | What | Files | Size | Depends | Class | Hardware gate |
| --- | --- | --- | --- | --- | --- | --- |
| W1 | Kext identity seam: the qualified chip id and the IOKit names selected by a profile under `SNAP_PLATFORM_SP11`, A16 text in the `#else`; byte-equality at the preprocessor level | `KEXT src/AdrenoX2/device.cpp:35`, `Info.plist`, `AdrenoContract.h:27-31` | S | owners' decision on names (STUDY 5 #13) | (b) | X8c |
| W2 | GPU window and pool budgets per profile; the SMMU readback that says which window the unit gives | `KEXT AdrenoX2.h:31-33`, `AdrenoMemoryMap.cpp:165`, `PLUG AdrenoStagingPolicy.h`; `REPO compat/adreno.h` | M | X8a SMMU read | (c) | X8a |
| W3 | Register-header generator parameterised by chip, descriptor domains and name list; a generated `AdrenoA7xxRegs.h` with a prefix of its own; the A8 header regenerated byte-identically (`tools/test-draw-contract.sh` step 1) | `REPO tools/gen-a8xx-regs.py`, `gpu/adreno-kext/include/` (outside the two repositories; no owner) | M | none | (a) | none |
| W4 | IB firewall profile seam: 48-word protect words, A7XX known-register set, A6XX T# layout, marker family, address policy (22 of 23 offsets equal), negative tests (protected register, X2-only register, a8xx T#, unknown opcode, unknown chip) | `REPO compat/adreno-ib-command*`, `adreno-ib-tables.h` | L | W3, GPL decision | (a) tables and tests; (b) kext link | X8b |
| W5 | Contract header: per-profile static assertions (RBBM/CP fault bits, T# alignment, idle rule) | `REPO gpu/adreno-kext/include/AdrenoContract.h:105-134` | S | W3 | (a) | none |
| W6 | Display: mode, panel size and the INTF page from the tree; a pacing replay at the new geometry | `KEXT AdrenoDisplay*.h/.cpp`; `REPO compat/dpu-intf.h` | M | WS-E reads (X5, X13a) | (c) | X5 |
| W7 | Coherency: reuse the gated self-test; the plug-in's `didModifyRange` follows it | `KEXT AdrenoMemoryMap.cpp`; `PLUG AdrenoResources.m:335`; `REPO compat/adreno-coherent*` | S | X8a | (c) | X8b |
| W8 | (merged into W1: the kext start-up probe and fence path need no change) | | | | | |
| W9 | Plug-in identity: device name, runtime chip id, expected sidecar chip id, compiler chip, selected by a profile with the A16 text in the `#else`; object equality with the gate off | `PLUG AdrenoMTLDevice.m`, `AdrenoShader.m:78`, `Compute.m:180`, `ProgramExports.m:154` | S | A2I `x1-85` (done), W1 names | (a) | X8c |
| W10 | A6XX T# and S# packers (T# incl. the storage form), host-tested against the XML like the compiler-side S# encoder | `PLUG Textures.m`, `Compute.m:415-422` | M | W3 | (a) | X8c |
| W11 | Static state for A7XX: magic registers, static non-context and context sets, restore | `PLUG SharedPassState.m:57-150` | L | W3, W10 | (a) | X8b |
| W12 | GMEM and CCU cache configuration for 6 CCUs and 3 MiB; the registers that do not exist at A7XX are dropped by the Mesa rule | `PLUG SharedPassState.m:10-31` | M | W11 | (a) | X8b |
| W13 | Sysmem pass prologue and epilogue, markers, the 2D clear path | `PLUG RenderPass.m`, `SharedPassState.m:234-270` | L | W11, W12 | (a) | X8c |
| W14 | Program state and linkage (`setup_stateobj`, shader regs, outputs, interpolation) | `PLUG ProgramState.m` | L | W3, W13 | (a) | X8c |
| W15 | Draw, depth-stencil, blend and rasteriser state | `PLUG DrawEncoding.m`, `RenderPass.m` | M | W13 | (a) | X8c |
| W16 | Compute: `SP_CS_*` set, THREAD64 and THREAD128 variants, driver parameters in constants | `PLUG Compute.m` (747 lines) | L | W9, W14 | (a) | X8d |
| W17 | Blit, mipmap and 2D engine: `A2D_*` registers, `CP_MEMCPY` chunking at 1 MiB (kept) | `PLUG Blit.m`, `AdrenoDrawCommands.h:195` | M | W11 | (a) | X8b for the copy |
| W18 | Compiler wiring of the a7xx S# encoder: sidecar key, `mesa-ninja.sh` target, lifting the refusal, `test-chip-profiles.py` in a full build; the plug-in side of the key | `A2I`; `PLUG ProgramExports.m` | S | the Mesa build (Mako, section 8), W10 | (a) | X8d |
| W19 | Code-scan ground truth with `gpu_id` 700 | `PLUG perf/host/test-codescan.c`, `AdrenoCodeScan.h` | S | the Mesa build | (a) | none |
| W20 | Perf tools: prefix switch in `capture.py`, cost-model parameters | `PLUG perf/*.py` | S | W3 | (a) | X11 |
| W21 | Offline host replays of the a7xx encodings against the host's Metal (F6) | `PLUG harness/*` | M | W10-W17, the Metal toolchain | (a) | before X8c |
| W22 | UBWC and tiling for a7xx (later stage; v1 stays linear) | `PLUG`, `REPO` | L | X8e | (c) | after X8e |
| W23 | Kext ABI checks for any new IOKit name or class (the AGENTS.md recipe) | `KEXT include/` (private, not distributed) | S | W1 | (b) | none |
| W24 | EL2 and q1n1 GPU service: clocks, GDSCs, RSC and PDC, GMU, HFI, firmware, SECVID, DVFS (F2, F3). Not in the two repositories; the first host model is section 7 | monitor, q1n1 | L | X8a | (c) | X8a, X8b |

Counts: 23 items (W8 is merged). Class (a): W3, W4 (tables and tests), W5, W9, W10, W11, W12, W13, W14, W15, W16, W17, W18, W19, W20, W21 = 16.
Class (b): W1, W23 (W4 has a (b) part) = 2. Class (c): W2, W6, W7, W22, W24 = 5. Of the 16 class-(a) items one is implemented
here, in part (the compiler-side S# encoder, the first half of W18); the rest are not, for the reasons of section 4.

## 4. What was implemented, and why only that

| What | Where | Gate and default | Proof | Status |
| --- | --- | --- | --- | --- |
| `air_sampler_a7xx`, the A6XX-layout TEX_SAMP encoder, and `tests/test-a7xx-sampler.py`: every word against an independent model built from the pinned XML for 30,658 sampler states, the a8xx encoder as the control, 11 seeded faults caught | `A2I` branch `sp11-prep`, commit `air2ir3: add the a7xx TEX_SAMP encoder, tested against Mesa's XML` | Not referenced by `src/build.sh`, `tools/mesa-ninja.sh`, `tools/compiler_id.py` or any translation unit, so no profile's output and not the compiler id change; the `x1-85` refusal stays | 5 PASS on the host with a sparse checkout of the pinned Mesa; `tools/cachekey.py` prints the same key before and after (`84d0e040...`); hardware NOT RUN | host-verified; wiring (W18) NOT RUN: needs a Mesa build |
| Four negative kernels (`atomicrmw`, `cmpxchg`, `fence`, a call to an undefined function) and the check `unsupported_opcodes_rejected` | `A2I` `tests/` | Test only | The 10 + 4 fixtures assemble with `llvm-as` (PASS); the check's plumbing ran against a stand-in compiler that accepts it and one that does not (PASS); the compiler itself: NOT RUN (no Mesa build) | NOT RUN on the compiler |

The earlier chip-profile patch (the air2ir3 task's `air2ir3-gen70500.patch`, 27 files, SHA-256 `2c62cf1a8c40...`) applies without
change to the pinned commit and is the first commit of the branch. What it proved when it was written (a compiled compiler,
22 checks PASS, `tools/chipcompare.py` 182 of 183 cases identical, 17 seeded bugs caught) is that task's OBSERVED record, not
re-measured here.

Why no other class-(a) item was implemented. W3, W4 and W5 live in the umbrella, and this work may not change `compat/`
(the firewall) and was asked to implement in the three clones; W9 and W1 select names and constants that the study
leaves to the owners (STUDY 5 #13, #14), and a seam built before that decision would pre-empt it; W10 to W17 need the
a7xx header of W3 (no register number is typed by hand in this project) and, for the builder, a q1n1 a7xx reference
proven on the unit (R1). Each is sized and ordered above so that the owners can start with W3, which blocks the most.

## 5. Risks

| # | Risk | Why it matters | Mitigation |
| --- | --- | --- | --- |
| R1 | **No hardware-proven a7xx command-stream reference.** The a8xx builder is a port of a python builder proven on the A16; for the SP11 nothing is proven, and the plug-in port (W11-W17, about 3,500 lines of builder code) would be written against Mesa's source alone | A wrong register sequence on a GPU the project cannot yet submit to is found only on the unit; every misstep costs a supervised session | Write the q1n1 a7xx reference first (X8b, the NOP, WFI and bounded-copy steps, then a 2D clear, then one draw), prove it on the unit, then port function by function with dword-for-dword package tests |
| R2 | **Who owns RPMh, the RSC and the GPU SMMU at EL2 with Gunyah present is UNKNOWN** (UNX Q04). On a7xx the host programs the GPU RSC and two PDC registers; a wrong write can leave AOP or the RSC in a state only a power cycle clears; programming an SMMU context that the hypervisor owns reset another board (UNX, audio) | The main damage-adjacent risk of the first contact (section 7); it decides X8a's shape | Read state first and write nothing until the read-back is reviewed; the tool's S2 only reads, and a write stage needs every RSC or PDC word cited and its own authorisation |
| R3 | **Everything in the kext and anything touching IOKit is unverifiable here**: the KDK is absent, so no slot, signature or object check can run | A wrong virtual-slot signature panics at load | Keep kext changes to constants and names behind the gate; run the AGENTS.md recipe on a host with the KDK before any boot |
| R4 | The shared seams (generator, firewall tables, contract header) have no owner, and the 48-word table is GPL-2.0-only | Stage 1 of the study's cutover plan cannot start; including the header makes a built monitor or kext a combined GPL work | A decision of the repository owners first (STUDY 5 #14); then W3 |
| R5 | The compiler tests need Mesa's NIR and ir3, whose build needs Python Mako; the network allowed for this work did not cover it | W18, W19 and the profile checks stay NOT RUN | One authorisation (a scratch virtual environment) and the commands of Appendix C |

## 6. Order of work, and what needs a decision

1. Decide the owners of W3 and W4 and the licence question of the generated 48-word table (STUDY 5 #14). W3 unblocks W5 and W9 to W17.
2. Start the q1n1 side in parallel: the X8a toolkit (section 7, host model in `q1n1` `tools/sp11_gpu_x8a.py`) needs no kext or plug-in.
3. Authorise one scratch environment for Mako so that A2I's profile tests, W18 and W19 can run (R5).
4. Do not start W11 to W17 before the q1n1 a7xx reference of R1 has passed X8b.
5. Decisions for the owners: the IOKit names (W1), the window (W2) after the X8a read-back, the sidecar key (W18), whether the a7xx header carries all resolvable names or only the plug-in's.

## 7. First-contact safety plan (X8a to X8e)

Everything here is a plan [EXPERIMENTAL]; each step is a separate operator authorisation (7.5). The standing rules are
AGENTS.md non-negotiable 5: one hardware client at a time; AC power or at least 50 % battery; the thermal guard on;
`--gpu-dvfs-ceiling` respected; never one `CP_MEMCPY` over 1 MiB (a 4 MiB copy hung the A16 GPU); tracked sources unchanged
between prepare and boot; explicit permission for persistent firmware writes and any clock or voltage outside the limits.

**Superseded in part by v3.** The stage ladder S0 to S7 of this section is replaced by E1 to E7 ([SP11-GPU-BRINGUP-STAGES.md](SP11-GPU-BRINGUP-STAGES.md) sections 2 and 3): S0 is boot 30 (7.1.1, unchanged); S1 and the R5 reads are inside E2; the GMU image and its clocks become E3a to E3c, and the GMU's own SMMU mapping is a prerequisite that this section does not name. Sections 7.2 and 7.3 stay valid and 7.4 holds except where marked; the authorisation table of 7.5 and the list of 7.7 are superseded.

### 7.1 Order of reads before the first write (stage S0, read-only)

| Step | Read | Why this position | Stop rule |
| --- | --- | --- | --- |
| R0 | Host only: the board descriptor and the tool's self-test | Every address comes from the descriptor or a cited source; a null is refused | Any null needed by a later stage is listed; nothing is sent |
| R1 | The link and the SError guard (strict mode) | An access that raises an SError must stop the run, as on the A16 | No guard: stop (exit 4) |
| R2 | GCC (always on): the GPU CFG AHB branch `0x71004`, QMIP `0x71008`, MEMNOC_GFX `0x71010`, SNOC_DVM_GFX `0x71018`, DDRSS_GPU_AXI `0x7115c`, the GPLL0 votes `0x52000` bits 15-16 (`LNX gcc-x1e80100.c`) | GPUCC is reachable only with its AHB branch enabled; Linux keeps `0x71004` always on (its critical-CBCR list) | AHB branch off: stop; no GPUCC read |
| R3 | GPUCC: CX GDSCR `0x9108` and its status word GDS_HW_CTRL `0x953c`, GX GDSCR `0x905c` and CFG word, the branches the GMU and SMMU nodes name (AHB `0x911c`, CX_GMU `0x913c`, CXO `0x9144`, HUB_CX_INT `0x9148`, DEMET `0x900c`, HLOS1 vote `0x7000`), the AON ones (CXO_AON `0x9004`, HUB_AON `0x93e8`), GX_GMU `0x90bc`, the GMU, HUB and XO RCG registers (`LNX gpucc-x1e80100.c`; GPUCC at `0x03d90000`) | A GDSC and clock status read never needs the block powered | An SError or abort: stop |
| R4 | The gate: CX GDS_HW_CTRL bit 31 set (the CX GDSC status, `gdsc.c:61-91`), `SW_COLLAPSE` (GDSCR bit 0) and `HW_CONTROL` (bit 1) clear, and the seven clocks of the GMU node running (ahb, gmu, cxo, axi, memnoc, hub, demet; `hamoa.dtsi:4184-4197`) | Under HW control the GMU or RSC may collapse the GDSC between the status read and the next read (A16 experience) | Not proven: the survey ends with "CX off", having read nothing behind the GDSC |
| R5 | Only if R4 holds: CX_MISC (`0x03d9e000`), the GMU window (`0x03d6a000`), the RSCC (`0x03d50000`): the GMU power-status words, the RSC sequencer state, `TCM_RET_CNTL`; the SMMU identity (`0x03da0000`) only if its own four clocks run too (hlos vote, memnoc, snoc dvm, ahb) | The RSC and PDC state decides whether S2 is needed (STUDY 2.3); the SMMU state decides W2 | Any fault: stop |
| R6 | Never in S0: any GX register (`RBBM_*`, `CP_*`), any write, any reset | The GX-on state comes only from the GMU's own status words (CX side) | A GX request is refused before it is sent |

#### 7.1.1 What boot 30 showed (stage S0, reads R2 to R4; OBSERVED once)

Boot 30 (2026-11-20; [SP11-STICK-LOG.md](SP11-STICK-LOG.md) section 27) ran the read-only payload `GPUSNAP.BIN` of the q1n1
fork (v1.2, `q1n1:docs/SP11-PAYLOAD-GPUSNAP.md`) alone, through the stick's payload runner at EL2. It followed R2, R3 and R4 above
in that order, and its own rule is R4's: nothing behind the CX domain is read before something proves the domain on. It is
not the X8a tool, which has not run on hardware (NOT RUN), and it is not the S0 record of the cold state that X8a asks for,
because R5 was not reached. It wrote nothing (by its document and its review). The page is transcribed by eye from one
hand-held film, so a digit may be misread; the words are in the log, not repeated here. Everything below is OBSERVED
(one unit, one boot) unless a label says otherwise, and the readings of the words are INFERRED.

| Step | What boot 30 showed | Label |
| --- | --- | --- |
| R2 (GCC) | the GCC words answered at EL2, no fault; the GPU configuration AHB branch (`0x71004`) is on, so GPUCC could be read; the memory-NoC word has bit 31 and its enable bit both set (printed `ODD`), the SNOC branch and the `QMIP` word read OFF | OBSERVED (the words); INFERRED (the readings) |
| R3 (GPUCC) | the first read of GPUCC on the unit answered with no fault and no hang: the CX GDSC word is `0x22F801` (bit 31 clear, bit 0 set, no hardware control: powered down by request); the GX word is the same with `UP=0 DN=1`; the GMU, XO, hub and HLOS branches read OFF, `DMT` reads ON and `AHB` reads 0 (printed `ODD`) | OBSERVED (the words); INFERRED (the readings) |
| R4 (the gate) | not proven, and the stop rule above is what happened: the payload printed `R4 CX=OFF PWR=0 SC=1 HW=0 CLK=0X58 SM=0X2`, and the survey ended with "CX off", having read nothing behind the GDSC; 52 loads of the nominal 78 | OBSERVED (the line, the count); INFERRED (the verdict) |
| R5 (CX_MISC, GMU, RSCC, SMMU) | not read: four `GATED BY CX=OFF` lines. The GMU power-status words, the RSC sequencer state, `TCM_RET_CNTL` and the SMMU identity are all still unread | OBSERVED (not read); UNKNOWN (the values) |
| R6 | not in S0 by design: no GX register, no read of the GPU's own window at `0x03d00000`, no write | OBSERVED (the payload's own lines) |
| 7.2's first hazard (a block that is powered down or unclocked) | the R2 to R4 ordering held: nothing behind the closed gate was read, and no fault or hang came; that says the ordering is sound here, not that the hazard is real | OBSERVED (no fault); INFERRED (the rest) |

What it settles for the plan (INFERRED; the stage names and their order stay as they are; nothing is authorised).

- R2 and R3 are answered and R4's outcome is "CX off"; R5 and R6 are not, and R5 cannot be read until the CX domain is on.
  X8a stays open: its closing evidence is the tool's S0 record and the S1 to S5 records (7.6), none of which exists.
- The cold state is a powered-down GPU: the CX domain off, the GX domain not up, the GMU branches off, and no GMU firmware
  running at the hand-off. Stage S1 (CX power-on: GDSC and clocks) therefore starts from nothing, and S2 (the RSC and PDC
  read-back) can only follow a successful S1, because the RSCC lies behind the domain that S1 switches on.
- Whether the power-on can be made from EL2, and by which votes (the RPMh and GMU path of the study's section 2.3), is UNKNOWN;
  the S1 stage keeps its own review and its own authorisation record.

### 7.2 What could hang or damage

| Hazard | Mechanism and evidence | Prevention |
| --- | --- | --- |
| Reading a block that is powered down or unclocked | SError or a bus hang the proxy cannot recover from (A16 stage 1; a hang is not an SError) | R2-R4 ordering; the strict guard; a tool that names every register and its gate |
| Enabling a GDSC with a stuck RCG | "A GDSC can wedge if turned on with the RCG stuck" (A16 notes quoting `clk-rcg2.c`) | Park the shared RCG on XO first; bound every poll |
| A wrong RSC or PDC word | Can leave AOP or the RSC in a state only a power cycle clears; the plan's F2 must add the host programming a7xx needs (STUDY 2.3) | Read-back first; every word a null until supplied; its own authorisation |
| A wrong or oversize GMU image | A hung GMU, found only as a missing init result | Size and hash checked against the descriptor before any write |
| SECVID or trust write refused | A permissions violation (`LNX a6xx_gpu.c:1495-1497`) | Read back after the write; refusal stops the run (as on the A16) |
| GX power-up without the OOB sequence | GX hang | Only through the GMU's OOB request, after S4 |
| SMMU programming that Gunyah owns | A donor board reset when a hypervisor-held context bank was programmed (UNX) | The survey reads only; any SMMU write needs its own authorisation after R5 |
| An oversize copy | A 4 MiB `CP_MEMCPY` hung the A16 GPU | Section 7.3 |
| Heat | GPU thermal sensors and trips UNREAD (STUDY 2.12) | Section 7.4 |

### 7.3 Bounded submits (X8b)

Only after S5, and in this order, each with a timeout that means recovery, never success: `CP_NOP`; `CP_WAIT_FOR_IDLE`; a
fence the host can read; then copies of 4 KiB, 64 KiB, 256 KiB and at most 1 MiB, each step starting only after the one before it
completed with the error registers clean (`RBBM_INT_0_STATUS`, `CP_INTERRUPT_STATUS`, `CP_HW_FAULT`). The planner refuses any
copy above 1 MiB, or not dword aligned, before a word is written; the plug-in's `ADRENO_BLIT_CHUNK_BYTES` already caps its own at 1 MiB.

### 7.4 DVFS ceiling and thermal guard

The first vote is the lowest level of the descriptor's table (v3: Linux's own first DDR bandwidth vote is at the highest OPP, so this is a recorded deviation: v3 3.1 row 16). Candidates valid for every speed bin are 550, 390 and 300 MHz
(STUDY 2.12); the ceiling is the operator's choice and any raise needs a separate authorisation. The thermal guard of PLAN H1
stays on; its sensor and trip values were UNKNOWN until read (v3: the public tree maps the GPU to tsens3 sensors 5 to 12 with a passive trip of 95 C and a critical trip of 115 C, VERIFIED; the chassis limit stays UNKNOWN: v3 8.3), and S5 does not run without them in the descriptor. Boot 29's
thermal payload ([SP11-STICK-LOG.md](SP11-STICK-LOG.md) section 27.2) read the four temperature controllers at EL2 once (50 sensors enabled; by
hand the raw words read about 38 C to 43 C, INFERRED to be tenths of a degree); it did not say which sensor belongs to the
GPU, and the trip values are still unread.

### 7.5 Quiesce and recovery

Quiesce (S7) runs after a clean run and after every stop that is not an SError or a lost link, in reverse order and never
ahead of the GMU's own status: halt the CP and the GBIF (descriptor steps `ring.halt_steps`; the pair `RBBM_GBIF_HALT` `0x016`
and ack `0x017`, STUDY 2.1) if S6 ran, clear the GPU_SET request and wait until the GMU status shows the GX GDSC off, only then
hold the GMU in reset, disable what S1 enabled and give back the CX vote (a VOTABLE GDSC is not polled: the read-back is
recorded). Its first failure stops it where it is, leaves the GMU running and CX on (never a reset under a powered GX) and
ends the run as exit 2. On a hang or a silent link nothing more is sent: the operator power-cycles; DRAM retention across a
reset is UNKNOWN (PLAN X3), so no log is assumed to survive.

| Step | The operator authorises | The tool also needs |
| --- | --- | --- |
| S0 | A read-only run, AC power, no other hardware client | The descriptor; the guard |
| S1 | CX power-on (GDSC and clocks) after reading the S0 record | An authorisation record naming the stage and the descriptor hash |
| S2 | A review of the RSC and PDC read-back (`rsc.reviewed`); programming them word by word is a later stage the tool does not have | Every word of that stage non-null with a cited source |
| S3 | A GMU image (hash and size recorded; supplied locally, never committed) | The image path, hash, size and segment list; the HFI and log arenas; `gmu.gbif_cx` stated empty |
| S4 | HFI and the first vote with a stated ceiling | The ceiling; the message list; a raise is a new authorisation |
| S5 | GX power-up with thermal thresholds | The thermal sensor and limits |
| S6 | The ring steps of 7.3 | The SQE image, the 48-word table and the halt steps S7 runs |

### 7.6 Evidence that closes each gate

| Gate | Closed by | Not accepted |
| --- | --- | --- |
| X8a | The S0 record of the cold state; the S1 to S5 records (GDSC `PWR_ON`, GMU init result, HFI acknowledgements, the first vote acknowledged with RPMh success, GX on by the GMU's status); the quiesce record equal to the cold state; thermal readings before and after | "It did not hang" without the values |
| X8b | The SQE start with the protect table read back; the SECVID outcome; NOP, WFI and fence timestamps; the copy ladder to 1 MiB with clean error registers; a recovery drill executed once on purpose | A copy ladder that skips a step; a timeout reported as success |
| X8c | Kext ABI checks PASS against the KDK; offline host replays of the a7xx encodings against the host's Metal; the compiler's profile tests in a full build; one IB admitted by the a7xx firewall and one refused for each negative case | Compile success alone |
| X8d | A clear and a triangle on a linear surface read back and compared with the host Metal | A frame during GPU recovery |
| X8e | WindowServer composites over a measured window (median, tail, pacing, counters), no recovery events, thermal guard on | Numbers from a recovery or latched window |

### 7.7 What the X8a host model covers

`q1n1` `tools/sp11_gpu_x8a.py` (stages), `sp11_gpu_session.py`, `sp11_gpu_regs.py`, the fake device `sp11_gpu_fake.py` and its test
`test-sp11-gpu-x8a.py` run S0 to S7 against a register model and have no command that connects to hardware. They follow Linux's
a7xx cold-boot order for the GMU (`a6xx_gmu.c:953-1058`) and the HFI table of `a6xx_hfi.c:1155-1198`. Not modelled
[UNIMPLEMENTED], and each a reason a first S1 or S3 may stop on the unit: the GMU RCG rate (Linux asks 200 MHz, rounded to
220 MHz from GPU_CC_PLL1; S1 leaves it parked on XO) and PLL1, the GCC GPLL0 votes (S1 refuses a hub RCG whose parent is not
ready), the RSC wake and the RSC and PDC programming, the GMU-stream SMMU context, the a7xx GBIF CX table, Linux's HFI message
list (the descriptor supplies the messages) and thermal sensing (the cycle.py guard). v3 adds to this list the CX GBIF table written through the GPU window, the ACD message, the DDR bandwidth vote, the full `hw_init` list and the order of its reglist (v3 3.1).

## 8. Host validation of this work

Run on the host that wrote this, in a scratch copy of the umbrella with the clones at the pinned commits (Appendix C).
Outcomes: PASS, FAIL, SKIP (a missing input), NOT RUN. A skip is not a pass.

| Suite | Result | Note |
| --- | --- | --- |
| `tools/test-draw-contract.sh` (8 steps) | PASS 8/8 with a sparse pinned Mesa; FAIL without one | Step 1 shows the A8 header equals its generator's output |
| `test-adreno-cs-samplers`, `-display-trace`, `-display --output`, `-edp-backlight`, `-present-early-done`, `-dart-wiredcnt`, `-gen-adreno-recover-table` | PASS | 5 of them with skips: the KDK or kernelcache, a 26A428 host, the private headers, Mesa's `a6xx.xml` cross-check |
| `-adreno-early-done.c`, `-live-copy.c`, `-staging-pool.c` | PASS (37, 7,616 and 508,815 checks) | The session runner reported the first as FAIL: it ran the shell builtin `test` |
| `test-adreno-residency.py` | PASS 38 of 39; the `Kext` class SKIP | The kext build refuses without the private headers |
| `test-adreno-ib-pipeline.c` | PASS under Apple clang 21 (55,328 checks); FAIL to compile under Homebrew clang 23 (`-Werror` on an unused global) | Toolchain dependent |
| `test-adreno-ring.c` | FAIL, 1 of 5,179 checks (#1702, `loaded == 0`), identical under both compilers | Umbrella only (no component header); not caused by this work |
| `test-adreno-iogpu-stamps.c`, `test-kext-early-stop.py` | SKIP | Needs the KDK and the private headers |
| 13 `--bundle` tests (`ca-apl`, `ca-generic`, `ci-compute`, `depth-stencil`, `draw-package`, `icon-render`, `mrt`, `present-sync`, `settings-ci-encoding`, `settings-icons`, `shadow-surface`, `staging-arenas`, `targets`) | NOT RUN | Need `AdrenoMetal.bundle`; `xcrun metal` is missing the Metal toolchain |
| `test-adreno-ib-command.c`, `test-adreno-display-present.c` | NOT RUN alone | Driven by `qualify-*` scripts (captures) and by `test-adreno-display.py` |
| `A2I tools/cachekey.py` | PASS, key `84d0e040...` before and after | |
| `A2I tests/test-chip-profiles.py` | 2 PASS, 21 SKIP | No compiler build: Mesa's build needs Mako |
| `A2I tests/test-a7xx-sampler.py` | 5 PASS | Sparse pinned Mesa; no build |
| `A2I tests/regress.py`, `tools/compiler_id.py` | SKIP | The research scratch tree and `build/mesa` are absent |
| `KEXT`, `PLUG` own checks | none exist; `perf/*` checkers need run captures: SKIP | The kext's only tests are the umbrella's |
| `q1n1 tools/test-sp11-gpu-x8a.py`, `test-sp11-gpu-x8a-mutants.py` | 164 PASS; 45 of 47 faulty tools caught | Fake device only; the 2 others are rules enforced twice |
| Hardware | NOT RUN | The plan's tools and gates have not run on hardware; the one payload reading of boot 30 is in 7.1.1 |

## Appendix A. Method notes

- **Registers the plug-in programs (315).** Names are the first macro argument of `REG32`, `REG64`, `REGADDR` and `REGI` in
  every `.m` and `.h` under `AdrenoMetal/`. Each was resolved in the main `A6XX` domain of the pinned `a6xx.xml`, with array
  children named `<array>_<child>` at array offset plus child offset and a failed lookup retried without a `_REG` suffix. A
  definition covers a chip when its `variants` range does. "Same offset" compares the offset sets for A7XX and A8XX; "identical
  fields" compares the `(name, low, high, type)` sets of the bit fields that cover the chip. Zero names were unresolved at A8XX.
- **Mesa chip branches.** Lines of `src/gallium/drivers/freedreno/a6xx/*.cc,h` matching `CHIP` compared with `A6XX`, `A7XX` or `A8XX`.
- **Window numbers and file counts** are `wc` and text searches of the clones at the pinned commits.

## Appendix B. Pins and hashes

SHA-256 of the files first read here (every other pin: STUDY Appendix B, re-fetched and equal). Linux, from
`LNX drivers/clk/qcom/`: `gpucc-x1e80100.c` `c2914ffd395e82984c5c46e9d44239cc148c7fa7b6f72c2637ba58322fc6131b`; `gcc-x1e80100.c`
`f1c3779c7bc273dfe170ea2ff4d40f975083ee149e3b02a4f42d5939596eb775`; `gdsc.c`
`a5a73fcc890f658b48a1e2dc015f3d47316d77b8c7853bbf999828f25a8c86b1`; `gdsc.h`
`bea8e934482c13e1318ebe59bfd881cb91536e32afd9dae2676da9514d140ae9`; `clk-branch.h`
`bac7dcf51a0da5b27eaa0d4dc276f540fec80013bd4862cfbbc115a50161ef72`; `clk-rcg2.c`
`b173e0f4d0b962f777c668422f5b1ca22137334b62de766f01f3ef5faf5d93b6`; `drivers/iommu/arm/arm-smmu/arm-smmu.h`
`b7221ee9b767594ba79cfc139fca838236f81ba949fb5e0be81e31ef42b1911c`. Mesa, from `src/freedreno/registers/`: `gen_header.py`
`81763fc03ea0ebf254ab7427af2113dc0c7c0d80dceab61232b6e371463d1f90`; `freedreno_copyright.xml`
`16e6fda4963d01a6d2fa6c938a26082d6f0afd0988db7f953c9e4dea38739f2f`; `adreno/a6xx_enums.xml`
`08d56c3ee45ebaa151a8adc7adf7c372ecf89ca72fabc09da1d64645c31f06d4`; `adreno/adreno_common.xml`
`f898e4eee3de5f92d25597f003e5550a24fe50552d25555d6916dcd1b922d6f6`.

## Appendix C. Commands

```sh
git clone https://github.com/Snapintosh/air2ir3.git   # then SnapAdreno, SnapMetal; check out the pins of 0.2
# a sparse, blobless Mesa at the pin (no build): git fetch --depth 1 --filter=blob:none origin bbeb2dca9feed6ac88d71234a9dc5ae8aeed4275
AIR2IR3_MESA=<mesa> python3 tests/test-a7xx-sampler.py            # in the air2ir3 clone
MESA_TREE=<mesa> Q1N1_TOOLS=<q1n1>/tools bash tools/test-draw-contract.sh   # in a scratch umbrella with the clones in place
```

A full compiler build adds `tools/mesa-setup.sh`, `tools/mesa-ninja.sh`, `src/build.sh` and a Python with Mako and PyYAML (R5).
