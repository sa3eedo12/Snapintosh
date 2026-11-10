<!-- SPDX-License-Identifier: MIT -->
STUDY ONLY - nothing in it is implemented or hardware-validated

# SP11 GPU study: Adreno X1-85 (gen70500, a7xx) against today's X2 (a8xx) path

This is task F1 of workstream F in [SURFACE-PRO-11.md](SURFACE-PRO-11.md). The Surface Pro 11 (SP11, Snapdragon X Elite
X1E80100) carries an Adreno X1-85, which the Linux catalog files under `ADRENO_7XX_GEN2` (firmware family gen70500,
a7xx). Everything in this repository drives the a8xx Adreno of the Snapdragon X2. The study records, from public
sources pinned by hash plus this tree, what differs, what the repository would have to own to carry both, and what is
still unknown. It was written without touching hardware and without any Microsoft, Qualcomm or Apple firmware or other
vendor-private material. Nothing here authorizes a hardware step; the sub-gates in the plan (X8a-X8e) still apply.

Companion artefacts, all host-only and linked into no build:
[`tools/gen-a7xx-cp-protect.py`](../tools/gen-a7xx-cp-protect.py) (MIT),
[`compat/adreno-a7xx-protect-tables.h`](../compat/adreno-a7xx-protect-tables.h) (GPL-2.0-only, generated, included by
nothing) and [`tools/test-gen-a7xx-cp-protect.py`](../tools/test-gen-a7xx-cp-protect.py) (MIT).

## Key findings

Each finding is detailed, with citations, in the section named at its end.

1. **Identity** [VERIFIED]. The X1-85 is Linux chip id `0x43050c01`, family `ADRENO_7XX_GEN2`, firmware
   `gen70500_sqe.fw` and `gen70500_gmu.bin`, GMU chip-id word `0x7050001`, and the catalog names no zap firmware.
   Linux's A740 (`0x43050a01`) is a separate catalog entry. (§1)
2. **Registers** [VERIFIED; hardware UNVERIFIED]. 278 named registers exist in both generations at different offsets,
   so an a7xx register header is not a recolouring of the a8xx one. The X2 physical-address constants for `RBBM_STATUS`,
   the interrupt status, the GBIF halt pair, `CP_SQE_CNTL` and the GMU power status do not carry over; the `CP_RB_*`
   ring registers do. Derived X1-85 addresses are in §2.1.
3. **Unchanged platform facts** [VERIFIED for X1E80100, OBSERVED for the X2 code]. Same GPU base `0x03d00000`, GIC SPI
   300, RSCC at GPU base + `0x50000`, CX_MISC at `0x03d9e000`, GPU SMMU at `0x03da0000` with stream ids 0 and 1. (§2.4,
   §2.13)
4. **RPMh/PDC/RSC is the largest GMU delta** [VERIFIED + OBSERVED]. On a8xx Linux leaves RPMh/PDC programming to AOP;
   on a7xx the host driver programs the GPU RSC and two PDC registers itself. Today's X2 bring-up only wakes the RSC.
   An SP11 bring-up must add this programming or prove that the boot firmware left the RSC ready. (§2.3)
5. **HFI** [VERIFIED]. The GPU performance table travels in a fixed-layout message on a7xx and in a generic typed
   table message on a8xx; whether gen70500 firmware accepts the protocol version Linux advertises is UNVERIFIED. (§2.3)
6. **CP_PROTECT** [VERIFIED]. X1-85 uses the 48-slot `a730_protect`, shared by six catalog entries; the encoding
   macros are `A6XX_PROTECT_*`; the register XML declares only 32 slots. The generated GPL header carries the table;
   nothing includes it. (§2.6)
7. **Descriptors and PM4** [VERIFIED; hardware UNVERIFIED]. a7xx uses the a6xx texture-descriptor layout, and none of
   the 37 field names it shares with the a8xx layout sits at the same position, so an X2 texture descriptor is not an
   X1-85 one; the existing generated header carries only the a8xx texture and sampler layouts. Every packet opcode the
   IB validator dispatches (20) exists for a7xx; only the `CP_SET_MARKER` word layout differs, and the three words the
   validator accepts are the same numbers under both layouts. (§2.14)
8. **UBWC** [VERIFIED for X1E80100; INFERRED for the X2]. X1E80100 is UBWC 4.0 with highest bank bit 16; the X2 is taken to
   be the glymur entry, UBWC 5.0, also bank bit 16 (§2.5). Today's guest path runs with tiling and UBWC off. (§2.5)
9. **ZAP/SECVID** [VERIFIED; unit behaviour UNVERIFIED]. The EL2 device-tree overlay disables the zap-shader node, so
   the zap-less path used today for the X2 is the expected one; whether the unit accepts the SECVID write at EL2 is not
   known. (§2.7)
10. **Pinned Mesa** [VERIFIED]. It lists the X1-85 only as the wildcard id `0xffff43050c01`, in one entry shared with
    the A740 aliases; ir3 gives gen 7 shorter delay slots (2/5/1) than the 3/6/2 defaults that gen 8 keeps. Compiler
    support itself belongs to the air2ir3 task. (§3)
11. **Ownership** [INFERRED]. One chip-profile seam per layer, gated by `SNAP_PLATFORM_SP11`; no forks of the monitor,
    kext, plug-in, compiler, register generator or Mesa. The IB firewall's chip-specific inputs are the register
    header, the 48-word protected table and the texture-descriptor layout. (§4)
12. **Gaps** [UNVERIFIED]. The kext, plug-in and air2ir3 submodules are not initialised in the tree this study used, so
    every statement about their code is UNVERIFIED. (§0.3, §5)

## 0. How to read this document

### 0.1 Evidence labels

| Label | Meaning here |
| --- | --- |
| VERIFIED | Checked against the cited pinned public text, or against the cited line of this tree. Never a hardware proof. |
| OBSERVED | Seen in this tree or the q1n1 checkout as it behaves today, i.e. the a8xx/X2 code. It says nothing about the SP11. |
| INFERRED | Reasoned from VERIFIED or OBSERVED facts; the premise is named. |
| UNIMPLEMENTED | Nothing in the repository does this today. |
| UNVERIFIED | Not checked: sources unavailable or unread, or a property of firmware or of the physical unit. |
| EXPERIMENTAL | Not used; no experiment was run. |

A label on a heading covers every item under it that carries none of its own. Appendix A describes how the host-computed
numbers were obtained; the numbers are labelled in the sections that use them.

### 0.2 Citation keys

| Key | Meaning |
| --- | --- |
| `LNX` | Linux tag `v7.4-rc2`, commit `7475ea8b58616b33c04afb976cf0fbaad9dd63fb`. `adreno/` is `drivers/gpu/drm/msm/adreno/`; `regs/` is `drivers/gpu/drm/msm/registers/adreno/`. |
| `MESA` | Mesa commit `bbeb2dca9feed6ac88d71234a9dc5ae8aeed4275`, paths under `src/freedreno/`. This is the pin of `tools/build-adreno-arm64e-mesa.py` and of the `compat/adreno-ib-tables.h` header. |
| `FW` | linux-firmware commit `69e51bb26f3fa3e118dd770f1962a93bed690dc1`, file `WHENCE`. Text only; no firmware binary was fetched. |
| `Q1N1` | The sibling q1n1 checkout at commit `7872398694ca8322e9cdebcc4ca2380491295f24` (the plan, section 2 for the sibling checkout and section 12 for the pin), paths under `tools/`. |
| `REPO` | This tree; paths relative to the repository root. |
| `PLAN` | [SURFACE-PRO-11.md](SURFACE-PRO-11.md), cited by section number, gate or item id (N, E, F, H, R rows), never by line number, so that a cite survives edits to the plan. |

Line numbers are those of the pinned files. A file's SHA-256 is in Appendix B, so a reader can confirm the text they
check is the text cited.

### 0.3 Coverage

Read: the Linux files in Appendix B (catalog, a6xx/a7xx/a8xx GPU, GMU and HFI sources, `adreno_gpu.*`,
`adreno_device.c`, the register XML, `hamoa.dtsi`, `x1-el2.dtso`, the UBWC configuration), the Mesa device table, device
matcher and ir3 compiler setup, the Mesa descriptor and PM4 XML (only the packets and descriptors the IB validator
uses), the `WHENCE` text, and in this tree `compat/adreno*.{c,h}`, `tools/gen-a8xx-regs.py`,
`gpu/adreno-kext/include/AdrenoContract.h` and the q1n1 GPU tools named in the text.

Not read, and therefore UNVERIFIED wherever it matters: the SnapAdreno kext, SnapMetal plug-in and air2ir3 submodules
(empty in this tree); any firmware binary; Linux `gpucc-x1e80100.c`; PM4 packets the validator does not admit and the
perfcounter XML; Linux GPU-state and devfreq code; any property of the physical unit.

## 1. Target silicon identity

### 1.1 The catalog entry

All rows are [VERIFIED] against `LNX adreno/a7xx_catalog.c`. The entry spans lines 375-418 (the opening and closing
braces); the plan's cite of lines 376-380 (PLAN section 4.1, GPU row) is accurate for the chip id, family and firmware names, which
are the first rows below.

| Field | Value | Lines |
| --- | --- | --- |
| Chip id | `0x43050c01` (a comment tags it "C512v2") | 376 |
| Family | `ADRENO_7XX_GEN2` | 377 |
| Firmware | SQE `gen70500_sqe.fw`; GMU `gen70500_gmu.bin`; no zap name | 378-381 |
| GMEM | 3 MiB | 382 |
| Quirks | cached-coherent, hardware APRIV, preemption, IFPC (no 4 GiB-VA, no soft fuse) | 384-387 |
| Function table | `a7xx_gpu_funcs` | 388 |
| Clock gating | `.hwcg = a740_hwcg`; GMU CGC mode word `0x00020202` | 390, 397 |
| CP_PROTECT | `.protect = &a730_protect` | 391 |
| Power-up lists | `pwrup_reglist`, `dyn_pwrup_reglist`; IFPC list `a750_ifpc_reglist` | 392-394 |
| GBIF (CX) | `a640_gbif` | 395 |
| GMU chip-id word | `0x7050001` | 396 |
| Bus votes (BCM) | SH0 width 16, MC0 width 4, ACV fixed with perf-mode bit 3 and 16500000 kBps | 398-408 |
| Preemption record | 4192 KiB | 410 |
| Speed bins (fuse value to bin) | 0 to 0, 59 to 1, 7 to 2, 232 to 3, 146 to 4 | 411-417 |

The X2 entry (`0x44070001`, `LNX adreno/a8xx_catalog.c:1580-1621`) differs: family `ADRENO_8XX_GEN2`, firmware
`gen80100_*`, 21 MiB GMEM, a soft-fuse quirk instead of IFPC, `max_slices = 4`, GMU chip-id word `0x8010100`, ACV
perf-mode bit 2, and four speed bins (388/357/284). [VERIFIED]

### 1.2 Neighbours that must not be confused with it

- **A740, chip id `0x43050a01`** (`LNX adreno/a7xx_catalog.c:341-372`). [VERIFIED] A separate GEN2 entry with firmware
  `a740_sqe.fw` and `gmu_gen70200.bin`, a zap name (`a740_zap.mdt`, line 353), GMU chip-id word `0x7020100` (line 360),
  and no IFPC quirk (lines 349-351). It shares `a730_protect` with the X1-85. Mesa lists the same id as a different
  device ("FD740", `MESA common/freedreno_devices.py:1281`).
- **`a730_protect` has six users** [VERIFIED]: chip ids `0x07030001` (line 284), `0x43020100` (307), `0x43050a01` (341),
  `0x43050c01` (376), `0x43051401` (419, a GEN3 part) and `0x43030c00` (455); the pointer is set at lines 299, 321, 356,
  391, 434 and 469. The table is lines 137-188 with 48 slots declared at line 189, so a CP_PROTECT table does not
  identify the part.

### 1.3 How a chip id and speed bin reach the driver and userspace

- [VERIFIED] The catalog is matched on the device-tree compatible `qcom,adreno-<8 hex digits>`
  (`LNX adreno/adreno_device.c:147-170`; the lookup compares `chip_ids[]` at line 60). hamoa declares
  `qcom,adreno-43050c01` (`LNX arch/arm64/boot/dts/qcom/hamoa.dtsi:4015`).
- [VERIFIED] The speed-bin fuse is an nvmem cell, `gpu-speed-bin@119`: two bytes at offset `0x119`, an 8-bit field
  starting at bit 7 (`hamoa.dtsi:8814-8817`, inside the qfprom node at line 8808). The driver falls back to the
  value `0xffff` when the read fails or returns zero (`LNX adreno/a6xx_gpu.c:2760-2763`).
- [VERIFIED] The raw fuse value is what userspace sees: the chip-id parameter ORs it into the upper word for parts
  without a `revn` (`LNX adreno/adreno_gpu.c:390-394`). The supported-hardware mask is one bit per bin
  (`a6xx_gpu.c:2594-2604`), so fuse values 0/59/7/232/146 give masks `0x01/0x02/0x04/0x08/0x10`. An unknown fuse falls
  back to bin 0 with an error message (`a6xx_gpu.c:2644-2651`).
- [VERIFIED] The X2 run-time chip id is not the catalog id: the a8xx code ORs the number of enabled slices into bits
  7..4 (`LNX adreno/a8xx_gpu.c:112-113`), so catalog id `0x44070001` becomes `0x44070041` on four slices, which is the
  id `REPO probes/adreno.py:70` hard-codes for today's X2 path (OBSERVED, a comment there says q1n1 proved it). Nothing
  of that kind exists for a7xx: in the 14 pinned files of `LNX adreno/` (Appendix B) `chip_id` is assigned once from
  the configuration (`adreno_gpu.c:1207`) and edited once, by the a8xx slice OR, so for the X1-85 the catalog id
  `0x43050c01` is the id (INFERRED from that search), with the speed bin added in the upper word on read. The GMU does
  not use the run-time id on either part, because both catalog entries carry an explicit GMU chip-id word
  (`LNX adreno/a6xx_gmu.c:1012-1013`).
- [VERIFIED] The X2 entry carries a soft-fuse quirk, so it reads its bin from a CX_MISC soft fuse
  (`a6xx_gpu.c:2615-2619`); the X1-85 uses the nvmem cell. A host-side read-back must therefore use a different
  source.

### 1.4 What Mesa knows

[VERIFIED] `MESA common/freedreno_devices.py:1279-1299` lists the X1-85 as the wildcard id `0xffff43050c01`, named
"Adreno X1-85" (line 1283). The matcher (`MESA common/freedreno_dev_info.c:21-65`) accepts an exact id, a patch-id
wildcard, and, when the reference id carries all-ones in the fuse field (bits 47..32), ignores whatever fuse value the
kernel reports (lines 45-62). So `0xffff` is a wildcard, not "no speed bin", and no X1-85 bin is excluded. The plain id
`0x43050c01` has no entry of its own, but it matches this one too, because the matcher ORs the wildcard into the
reported id before comparing (line 50); so does an id that carries a bin in its upper word. See §3.

### 1.5 Firmware names (no binaries consulted)

[VERIFIED] `FW WHENCE` names `qcom/gen70500_gmu.bin` (line 7232, version 4.03.11), `qcom/gen70500_sqe.fw` (7234, version
1.62) and a generic `qcom/x1e80100/gen70500_zap.mbn` (7236, version 0.15). The plan names a different, Denali-specific
image, `qcdxkmsuc8380.mbn`, in the device's own device tree (PLAN N2 in section 1.1 and E25 in section 4.2). [INFERRED] Linux allows the zap
file to come from a `firmware-name` property because such images can be signed per device, and requires the property
when the catalog has no zap name (`LNX adreno/adreno_gpu.c:69-100`); the X1-85 catalog has none. Whether SP11 needs a
zap image at all at EL2 is UNVERIFIED (§2.7).

### 1.6 Facts a first read-back should establish [UNIMPLEMENTED, NOT RUN]

Nothing below has been run. Gate X8a of the plan is the place to establish them, with the operator, and each would move
a statement of this study from VERIFIED-in-text to observed-on-unit:

1. the chip id the device tree and any firmware-reported value give, against `0x43050c01`;
2. the speed-bin fuse value and the bin it selects (§1.3), which bounds the usable frequencies (§2.12);
3. the GMU chip-id word the firmware expects against `0x7050001`;
4. the GX/CX power-status words, `RBBM_STATUS` and `CP_SQE_CNTL` at their a7xx addresses (§2.1);
5. whether the RSC is already programmed (§2.3) and whether the SECVID write sticks (§2.7);
6. which UBWC version the unit's tables report (§2.5).

## 2. What differs between a7xx (X1-85) and a8xx (X2)

Each subsection names the a7xx fact, the a8xx fact, and "today" (what this repository or q1n1 does for the X2).

### 2.1 Register map and block layout

- [VERIFIED] Linux and Mesa describe a6xx, a7xx and a8xx registers in one XML file per project (`LNX regs/a6xx.xml`,
  `MESA registers/adreno/a6xx.xml`), each entry carrying a `variants` range (`A6XX`, `A6XX-A7XX`, `A7XX`, `A7XX-`,
  `A8XX`, `A8XX-`). The Mesa file imports the a7xx and a8xx enum files (`MESA registers/adreno/a6xx.xml:7-15`). There is
  no separate a7xx register file; 294 entries of the Mesa copy have an `A7XX...` variant.
- [OBSERVED; method in Appendix A] Of 1426 named registers directly under the main `A6XX` domain of the Mesa file: 497
  exist in both generations at the same offset, **278 exist in both at a different offset** (CP aperture,
  context-switch, misc-control and status registers among them), 242 are A7XX-only, 319 A8XX-only and 90 are neither.
- [OBSERVED] Today's register header is generated by selecting the A8XX variant range with the chip name written into
  the generator (`REPO tools/gen-a8xx-regs.py:71`). [INFERRED] The a7xx header needs the same generator with the chip
  as a parameter; a copy would fork a 546-line tool.
- [VERIFIED arithmetic; hardware UNVERIFIED] A physical address is GPU base `0x03d00000` plus four times the dword
  offset. The X2 constants in `REPO compat/adreno.h:56-67` agree with that rule for the a8xx offsets. Applying it to the
  a7xx offsets of the same registers (identical in the Linux and Mesa copies of the XML) gives:

| Register | a8xx dword, X2 address in `compat/adreno.h` | a7xx dword, derived X1-85 address |
| --- | --- | --- |
| `RBBM_GBIF_HALT` | `0x00a`, `0x03d00028` | `0x016`, `0x03d00058` |
| `RBBM_GBIF_HALT_ACK` | `0x00b`, `0x03d0002c` | `0x017`, `0x03d0005c` |
| `RBBM_STATUS` | `0x012`, `0x03d00048` | `0x210`, `0x03d00840` |
| `RBBM_INT_0_STATUS` | `0x06a`, `0x03d001a8` | `0x201`, `0x03d00804` |
| `CP_RB_CNTL`, `CP_RB_RPTR`, `CP_RB_WPTR` | `0x802`, `0x806`, `0x807` (`0x03d02008`, `0x03d02018`, `0x03d0201c`) | same dwords, same addresses |
| `CP_SQE_CNTL` | `0x815`, `0x03d02054` | `0x808`, `0x03d02020` |
| `CP_SQE_INSTR_BASE` | `0x816` | `0x830` |
| global interrupt status | `CP_INTERRUPT_STATUS_GLOBAL` `0x8b5`, `0x03d022d4` | none; a7xx has `CP_INTERRUPT_STATUS` `0x823` (`0x03d0208c`) and `CP_HW_FAULT` `0x821` |
| `CP_APERTURE_CNTL_HOST` | `0x081c` | `0x0a00` |
| `CP_CONTEXT_SWITCH_CNTL` | `0x08c0` | `0x08a0` |
| `CP_MISC_CNTL` | `0x084c` | `0x0840` |
| GMU power/clock status | `GMU_PWR_CLK_STATUS` `0x1f7e8`, `0x03d7dfa0` | `GMU_SPTPRAC_PWR_CLK_STATUS` `0x1f8d0`, `0x03d7e340` |
| `GMU_AHB_FENCE_STATUS` | `0x23b13`, `0x03d8ec4c` | same dword, same address |

- [VERIFIED] `RBBM_SECVID_TRUST_CNTL` (`0xf400`) and `RBBM_SECVID_TSB_CNTL` (`0xf803`) are the same in both. `0xf400`
  maps to `0x03d3d000`, inside the monitor's second passthrough window
  (`REPO compat/adreno.h:40-41`). `CP_BV_RB_RPTR_ADDR` (`0x0a98`) exists for A7XX.
- [VERIFIED] GMU scratch and counter registers moved: `GMU_GENERAL_8/9/10` are `0x1f9cd/ce/cf` for A7XX and
  `0x1f9c8/c9/ca` for A8XX; `GMU_ALWAYS_ON_COUNTER_L/H` are `0x1f888/9` (A6-A7) and `0x1f840/1` (A8);
  `GMU_GMU_PWR_COL_KEEPALIVE` is `0x1f8c3` (A6-A7) and `0x1f7e4` (A8). `GMU_CM3_CFG` (`0x1f82d`) and
  `GMU_CM3_FW_INIT_RESULT` (`0x1f81c`) are common.
- [VERIFIED arithmetic] The derived GPU addresses lie inside hamoa's GPU window `[0x03d00000, 0x03d40000)` and the GMU
  addresses inside its `gmu` window `[0x03d6a000, 0x03d9f000)` (`hamoa.dtsi:4016-4018`, `4175-4177`).
- [OBSERVED] The monitor's passthrough windows (PASS0 `0x03d00000-0x03d37000`, PASS1 `0x03d3c000-0x03d40000`, CX_MISC
  `0x03d9e000-0x03da0000`, end `0x03df0000`; `REPO compat/adreno.h:38-44`) come from an X2 block map. [UNVERIFIED] Whether
  the same holes apply to the X1-85. Until an a7xx block map is checked, the safe choice is to trap everything outside
  an explicitly checked allowlist.

### 2.2 CP, SQE and the bring-up sequence

- [VERIFIED] Both families share `a7xx_submit`, ucode loading, the GMU power-management hooks and the VM helpers, but
  each has its own `hw_init`, recovery, interrupt, busy and fault routines; only the a8xx table has a perfcounter flush
  (`LNX adreno/a6xx_gpu.c:2870` and `2908`).
- [VERIFIED] Linux's shared a6xx/a7xx `hw_init` (`a6xx_gpu.c:1179`; a8xx has its own at `a8xx_gpu.c:861`) behaves
  differently for a7xx in these places: it skips the "64-bit addressing for all blocks" writes that older parts get
  (1223-1237); it programs a GBIF client QoS word and a UCHE GBIF config (1255, 1264); it sets the UCHE trap and
  write-through bases without a write range maximum, with a different cache-ways value (1293); it skips the older
  ROQ and mem-pool writes; and its tail, in order, writes the BV read-pointer shadow (1467-1470), starts the SQE
  (1481), patches the power-up register list once (1483-1486), sends the a7xx CP init (1488) and then tries the zap
  path and falls back (1499-1517, §2.7). The CP_PROTECT table is programmed on the way (1384, §2.6).
- [OBSERVED] Today's host replica of `a8xx_hw_init` (`Q1N1 tools/a16gpu_stage3.py:1676-1712`) stops before the SQE
  start and skips UBWC, preemption, the power-up list and hardware clock gating. [INFERRED] An a7xx replica needs the
  a7xx-only writes above, in that order, and the CP init packet only after the SQE start.
- [VERIFIED] GMEM is 3 MiB on the X1-85 against 21 MiB on the X2 (§1.1), and the X1-85 entry has no `max_slices`; the
  X2's runtime chip id carries a slice count (`Q1N1 tools/adreno_x2.py:742-744`, OBSERVED), the X1-85's is the
  catalog id. [UNVERIFIED] Any sizing in the plug-in that assumed the X2's GMEM.

### 2.3 GMU, HFI, RPMh, clocks and power domains

All rows are [VERIFIED] against `LNX adreno/a6xx_gmu.c` (lines in the last column) unless another file is named.

| Topic | a7xx (X1-85) | a8xx (X2) | Lines |
| --- | --- | --- | --- |
| GX-on test | reads the SPTPRAC power/clock status | reads a CX_MISC GFX status that stays valid while the GMU MX domain is off | 121, 138 |
| RPMh power-state read-out | A7XX-named register | A8XX-named register | 284-287 |
| Log write-pointer reset | `GMU_GENERAL_9`, A7XX name (`0x1f9ce`) | `GMU_GENERAL_9`, A8XX name (`0x1f9c9`) | 325-329 |
| GEMNoC qactive | `CX_FALNEXT_INTF` bit 0, A7XX name | same write, A8XX name | 569-572 |
| Slumber notify | A7XX power-counter enable | A8XX variant | 584-587 |
| **RPMh/PDC/RSC set-up** | the driver maps the `gmu_pdc` window, programs the GPU RSC (hidden TCS words, sequencer words) and writes two PDC registers | returns first: AOP configures all of it | 692-745, 798-800 |
| Cache/bus config | three one-word writes, then returns early | same | 821-827 |
| DTCM base | `0x10004000` | same | 886-894 |
| FAL10 veto | A6XX-named `CX_FALNEXT_INTF`/`CX_FAL_INTF` | A8XX names | 954-961 |
| TCM retention | CX_MISC `TCM_RET_CNTL` = 1 | skipped | 963-965 |
| Warm/cold boot test | reads `TCM_RET_CNTL` | same | 1363-1365 |
| HFI fence range | upper `0x32`, lower `0x8a0` | upper `0x32`, lower `0x8c0` | 990-997 |
| GMU chip-id word | `gmu_chipid` (X1-85 `0x7050001`), else repacked from the chip id; written to `GMU_GENERAL_10` | same mechanism (X2 `0x8010100`) | 1010-1046 |
| GBIF (CX) | configured before GMU wake | same, plus extra GBIF and QoS writes | 1048-1051 |
| RSCC status offset | `seqmem_off = 4`; TCS 4-9 polled with A7XX names | `seqmem_off = 4` | 1104-1134 |
| Debug/icache buffers | allocated | allocated | 2370-2396 |
| RSCC mapping | requires a DT resource named `rscc` | GPU base + `0x50000`, no DT resource | 2446-2463 |
| MMIO base | `mmio_offset = gmu_start - gpu_start`, so XML dword offsets count from the GPU base | same | 2446-2448 |

- [OBSERVED] Today's X2 bring-up wakes the RSC (a request/ack handshake and a sequencer-busy poll,
  `Q1N1 tools/a16gpu_stage3.py:1341-1372`, `2184-2209`) and never loads RSC sequencer or PDC contents; a search of the q1n1
  GPU tools for those register names finds only clock-branch names, the AP RSC helpers and the wake/stop handshake.
  [INFERRED] On a7xx the sequencer and PDC contents come from the host, so SP11 bring-up must either reproduce Linux's
  programming (`a6xx_gmu.c:716-745`, `798-800`) or show that the boot firmware left the RSC ready. The five sequencer
  words are firmware-adjacent constants and are deliberately not reproduced here. [UNVERIFIED] The state the SP11 boot
  firmware leaves.
- [VERIFIED] HFI: the query advertises protocol "1.10" (`LNX adreno/a6xx_hfi.c:247-255`). a7xx and older send the
  fixed-layout performance-table message with a vote, an ACD word and a frequency per level (`a6xx_hfi.c:331-353`); a8xx
  sends the generic table message typed as a GPU performance table, three words per GX level and two per CX level
  (function at 280-329, send at 323), selected by the a8xx test at 336. A second generic table is sent at line 922
  (purpose not read). [UNVERIFIED] Which protocol version gen70500 firmware negotiates.
- [OBSERVED] `REPO compat/adreno.h:1-37` states the monitor never writes the GMU, GPUCC, RSCC or GX clock controller;
  q1n1's host stages (gmu-smmu, gmu-boot, hfi, oob, cp-fence, quiesce) do. [INFERRED] The a7xx differences above all
  land in those host stages, not in the monitor.
- [VERIFIED] hamoa's GMU node (`hamoa.dtsi:4173-4223`): compatible `qcom,adreno-gmu-x185.1`; windows `gmu`
  `0x03d6a000+0x35000`, `rscc` `0x03d50000+0x10000`, `gmu_pdc` `0x0b280000+0x10000`; SPI 304 (hfi) and 305 (gmu);
  clocks ahb, gmu, cxo, axi, memnoc, hub and demet; power domains GPU_CX_GDSC and GPU_GX_GDSC; stream id 5; a QMP link to
  AOSS; GMU frequencies 550 MHz (SVS) and 220 MHz (LOW_SVS). [OBSERVED] q1n1's X2 notes list clocks ahb, gmu, cxo, memnoc,
  hub and rscc (`Q1N1 tools/adreno_x2.py:123-124`), so the clock sets differ in `axi`/`demet` against `rscc`.
- [OBSERVED] q1n1's X2 block map puts the GMU window at `0x03d6c000` (size `0x24000`) and a separate GX block at
  `0x03d6a000`, and sizes CX_MISC at `0x2000` and the clock controller at `0x9800` (`Q1N1 tools/adreno_x2.py:119-127`);
  hamoa has the GMU window at `0x03d6a000+0x35000`, CX_MISC at `0x1000` and the clock controller at `0xa000`
  (`hamoa.dtsi:4017, 4175, 4227`). [INFERRED] GMU register addresses are GPU-base-relative (table row "MMIO base"), so
  only window bounds, and therefore the monitor's allow/trap decisions, depend on the layout.
- [VERIFIED] The GPU clock controller is `qcom,x1e80100-gpucc` at `0x03d90000+0xa000` (`hamoa.dtsi:4225-4234`).
  [OBSERVED] The X2 path names `GPU_CC_*` branches from the glymur controller (`Q1N1 tools/a16gpu.py:77-87`) and a
  CX GDSC register at `0x03d99084` (`REPO compat/adreno.h:68`). That address is inside the X1E80100 window, but
  [UNVERIFIED] whether it is the same register there: `gpucc-x1e80100.c` was not read. [UNIMPLEMENTED] an X1E80100
  GPUCC/GDSC table for the host stages.

### 2.4 GPU SMMU

- [VERIFIED] The GPU's stream ids are 0 and 1 on `adreno_smmu` (`hamoa.dtsi:4026-4027`), an `arm,mmu-500`-compatible,
  `dma-coherent` node at `0x03da0000+0x40000` (`hamoa.dtsi:4236`). [OBSERVED] The X2 path uses the same base, ids 0 and 1,
  and GPU virtual addresses in [1 GiB, 4 GiB) mirrored into SMMU context bank 0 (`REPO compat/adreno.h:69, 153`, and the
  header comment).
- [VERIFIED] Neither the X1-85 nor the X2 entry sets the 4 GiB-VA quirk (defined at `LNX adreno/adreno_gpu.h:64`;
  entries in §1.1).
- [UNVERIFIED] Whether the SP11 firmware leaves the SMMU registers, context-bank layout and TTBR formats compatible
  with the mirror's assumptions, and whether the GMU's own stream id 5 needs the same treatment as the X2's.

### 2.5 UBWC

- [VERIFIED] The kernel's UBWC table maps `qcom,x1e80100` and `qcom,x1p42100` to UBWC 4.0 with highest bank bit 16
  (`LNX drivers/soc/qcom/ubwc_config.c:65-68`, `179-180`); the glymur entry is UBWC 5.0, bank bit 16, with two swizzle
  disables (lines 92-98, 121). [INFERRED] That the X2 is the glymur part follows from the glymur references in
  `REPO compat/adreno.h`. Mesa's X1-85 `highest_bank_bit` is 16 as well (`MESA common/freedreno_devices.py:1296`).
- [VERIFIED] a7xx writes the NC-mode words in `a6xx_set_ubwc_config` (`LNX adreno/a6xx_gpu.c:748`, called at 1344),
  including per-pipe BR/BV GRAS values through the aperture; a8xx has `a8xx_set_ubwc_config` (`a8xx_gpu.c:277`), which
  accepts UBWC 4.0, 4.3, 5.0, 6.0 and 7.0.
- [OBSERVED] The a8xx host replica skips UBWC unless a rendering-only flag is set (`Q1N1 tools/a16gpu_stage3.py:1676-1712`),
  and `REPO gpu/adreno-kext/include/AdrenoContract.h:75` says tiling and UBWC are off in v1. [INFERRED] If SP11 keeps
  v1's no-tiling rule, UBWC is a later stage, not an X8a requirement. [UNVERIFIED] Who programs UBWC in the kext.

### 2.6 CP_PROTECT

- [VERIFIED] The X1-85 table is `a730_protect` in `LNX adreno/a7xx_catalog.c` (§1.2), 48 slots. The encoding macros are
  `A6XX_PROTECT_NORDWR` and `A6XX_PROTECT_RDONLY` (`adreno/a6xx_gpu.h:245-255`). The consumer is `a6xx_set_cp_protect`
  (`a6xx_gpu.c:723-746`, called at 1384): it enables access protection with fault-on-violation and the last-span
  range, writes every slot but the last, skipping zero words, and writes the final word to the last slot.
- [VERIFIED] The register XML declares `CP_PROTECT` with 32 entries for A6XX-A7XX, while the driver programs 48 slots;
  the last slot's offset (`0x087f`) is below `CP_SCRATCH` (`0x0883`), so no overlap. a8xx instead has a global table
  and a per-pipe slot (`CP_PROTECT_GLOBAL`, `CP_PROTECT_PIPE`, `LNX adreno/a8xx_gpu.c:238-272`).
- [VERIFIED] The 48-slot table of the pin is in the generated header; the generator checks the pin hash, the licence of
  every input, the encoding, duplicates, the count and the consumer's semantics before writing, and refuses otherwise.
  The header is GPL-2.0-only, carries the upstream attribution, and is included by nothing.
- [OBSERVED] The IB allowlist today subtracts the X2's protected ranges (`REPO compat/adreno-ib-tables.h`, `52u`
  entries, control word `0xffff000bu`) from the known-register set (§2.9).
- [VERIFIED; computed on the host from the pinned text, method in Appendix A] The 52 words of `adreno_ib_protect` in
  `compat/adreno-ib-tables.h` equal, in order, the 52 entries of `x285_protect_regs` at `LNX adreno/a8xx_catalog.c:346-399`
  once each `A6XX_PROTECT_*` entry is encoded (first `0x0e6c0008`, last `0x80027c06`), so the X2 table is faithful to
  the Linux pin. It lives in the a8xx catalog, not in the `a6xx_catalog.c` that the repository's provenance text names
  (§6).
- [UNVERIFIED] Whether CP_PROTECT must match Linux's table exactly on the unit, and whether the firmware keeps ranges
  Linux does not list.

### 2.7 ZAP and SECVID

- [VERIFIED] The X1-85 catalog entry has no zap name; the `zap-shader` device-tree node supplies the reserved
  region (`hamoa.dtsi:4042-4044`, region at 597) and a board-level `firmware-name`. `x1-el2.dtso:15-17` sets that node
  disabled with a comment that Linux can zap the GPU itself at EL2 (BSD-3-Clause file). With no zap, Linux writes
  `RBBM_SECVID_TRUST_CNTL` = 0 and warns (`LNX adreno/a6xx_gpu.c:1507-1517`); a8xx does the same (`a8xx_gpu.c:804, 820-821`).
- [OBSERVED] The X2 path is already zap-less: it writes `RBBM_SECVID_TRUST_CNTL` = 0, reads it back, zeroes the TSB
  and trusted-range registers, and refuses to continue if the trusted size is non-zero (`Q1N1 tools/a16gpu_stage3.py`,
  1691-1693, 1835-1836, 2085-2089).
- [UNVERIFIED] Whether the SECVID write sticks on the SP11 at EL2, and whether the firmware's Denali image
  (`qcdxkmsuc8380.mbn`, PLAN N2 and E25) is needed. The Linux comment says that if the guess is wrong the
  SECVID register is blocked and a permissions violation follows (`LNX adreno/a6xx_gpu.c:1495-1497`), so a refused
  write must stop the sequence, as q1n1 does today.

### 2.8 Performance counters

- [OBSERVED] The X2 replica writes a8xx counter and mask registers (`Q1N1 tools/adreno_x2.py:514-540`,
  `a16gpu_stage3.py:1694-1699`). [VERIFIED] a7xx has no a8xx-style counter flush and the register layout differs (XML).
  [UNVERIFIED] The exact a7xx register set the replica would need; nothing in the first bring-up depends on it.

### 2.9 Ring and IB validation

- [VERIFIED] The ring registers have the same offsets in both generations (§2.1), so the monitor-owned ring's addresses
  would not change; [UNVERIFIED] on the unit.
- [OBSERVED] The IB validator admits PKT4/PKT7 only, 17 opcodes, and registers that are in the known set and not in the
  protected set, with relocations for every address register (`REPO compat/adreno-ib-command.h:1-40`); it is defence in
  depth, not isolation. It types no register number: every table comes from two generated headers, the register
  header (Mesa XML: registers, fields, enums, PM4 headers) and the protected-range header
  (`REPO compat/adreno-ib-command.c:1-8`; the bitmap builder is `compat/adreno-ib-command/stage_relocate.c:105`).
- [INFERRED] The chip dependence of the validator is therefore the register header and the protected-range header, plus
  the generation-specific names `validate.c` takes from the first one (the texture-descriptor fields, §2.14). For a7xx
  the known set comes from an A7XX register header and the protected set from the generated 48-word table; 278 shared
  registers moved, so reusing the a8xx bitmaps would be wrong. [VERIFIED] Every opcode the dispatcher handles (the 17 in
  the policy comment plus three later ones, §2.14) is defined for A7XX in the pinned Mesa XML, and only the
  `CP_SET_MARKER` word layout differs (§2.14). [UNVERIFIED] That the gen70500 firmware executes them as the X2's does.

### 2.10 Coherency

- [OBSERVED] The monitor proves IO-coherence on the machine at prepare time before mapping coherent leaves
  (`REPO compat/adreno-coherent.h:1-25`, gated by `SNAP_ADRENO_COHERENT`, default 0, `compat/adreno.h:131-135`). The
  reason is a disagreement between Linux's device tree (coherent) and the machine's ACPI IORT (non-coherent). The test
  counts the architectural cache-refill event `0x17` on CPU PMU counter 0 (`compat/adreno-coherent.h:20, 78`), reads
  the PMU version first and needs at least one event counter (`compat/adreno-coherent.c:115-116`). AGENTS.md rule 5
  caps any single `CP_MEMCPY` at 1 MiB because a 4 MiB copy once hung the GPU.
- [VERIFIED] For the X1-85 the catalog has the cached-coherent quirk and `adreno_smmu` is `dma-coherent` in hamoa; the
  CPU PMU is the generic `arm,armv8-pmuv3` (`hamoa.dtsi:371-372`) on `qcom,oryon` cores (line 73), the same kind of
  node the header cites for the X2. [UNVERIFIED] The unit's firmware tables, and whether its PMU exposes that event.
  [INFERRED] Reuse the gated self-test and keep non-coherent leaves unless it passes.

### 2.11 Display

[OBSERVED] The plan reuses the firmware's eDP/DPU pipeline for the OLED panel (PLAN section 3.1 "In scope", the Display row
of section 4.1, and WS-E items P1 and P2), so no GPU-side display programming is studied here. [UNVERIFIED] The display
kext side and the compositor handoff.

### 2.12 Operating points, speed bins and thermal

[VERIFIED] hamoa's GPU operating-point table (`hamoa.dtsi:4046-4170`) gates each point by a supported-hardware mask
(bit n = speed bin n of §1.3). The 1100 and 687 MHz points appear twice, for different bins:

| MHz | Mask | Level | Peak kBps |
| --- | --- | --- | --- |
| 1500 | `0x03` | TURBO_L5 | 16500000 |
| 1375 | `0x03` | TURBO_L4 | 16500000 |
| 1250 | `0x07` | TURBO_L3 | 16500000 |
| 1175 | `0x07` | TURBO_L2 | 14398438 |
| 1100 | `0x07` | TURBO_L1 | 14398438 |
| 1100 | `0x08` | TURBO_L1 | 16500000 |
| 1000 | `0x0f` | TURBO | 14398438 |
| 925 | `0x0f` | NOM_L1 | 14398438 |
| 800 | `0x0f` | NOM | 12449219 |
| 744 | `0x0f` | SVS_L2 | 10687500 |
| 687 | `0x0f` | SVS_L1 | 8171875 |
| 687 | `0x10` | SVS_L1 | 16500000 |
| 550 | `0x1f` | SVS | 6074219 |
| 390 | `0x1f` | LOW_SVS | 3000000 |
| 300 | `0x1f` | LOW_SVS_D1 | 2136719 |

- [INFERRED] Only 550, 390 and 300 MHz are valid for every bin, so they are the natural candidates for a conservative
  `--gpu-dvfs-ceiling` on a unit whose bin is not yet read. Which ceiling to use, and any raise, is an operator
  decision under AGENTS.md rule 5 and PLAN section 8 (the row for any raise of GPU or CPU clock or voltage ceilings).
- [UNVERIFIED] The X1E80100 GPU thermal sensors and trip points were not read. The thermal guard of PLAN H1 (WS-H)
  stays mandatory.

### 2.13 Platform resources of the GPU node

[VERIFIED] `hamoa.dtsi:4014-4044`: `qcom,adreno-43050c01`; windows GPU `0x03d00000+0x40000`, `cx_mem`
`0x03d9e000+0x1000`, `cx_dbgc` `0x03d61000+0x800`; GIC SPI 300; stream ids 0 and 1; nvmem speed bin; interconnect
GFX3D to EBI1; disabled by default. [OBSERVED] The X2 path shares the GPU base, SPI 300, the `+0x50000` RSCC, CX_MISC
and the SMMU base (key finding 3), which is why most platform constants in `compat/adreno.h` can stay and the register
offsets cannot.

### 2.14 Descriptors and PM4 packets

- [VERIFIED] Mesa's register directory has one descriptor file for a6xx (`a6xx_descriptors.xml`: the texture sampler
  `A6XX_TEX_SAMP`, the texture memory object `A6XX_TEX_MEMOBJ` and the uniform-buffer descriptor `A6XX_UBO`) and one for
  a8xx (`a8xx_descriptors.xml`: `A8XX_TEX_SAMP`, `A8XX_TEX_MEMOBJ`). It has none for a7xx, and the a6xx file carries no
  per-generation variant marker (`MESA registers/adreno/a6xx_descriptors.xml:10, 48, 163`, `a8xx_descriptors.xml:11, 42`;
  directory listing of the pinned commit). [INFERRED] a7xx uses the a6xx layouts, since nothing else is defined for it;
  which layout Mesa's drivers select for a7xx was not read.
- [VERIFIED; method in Appendix A] The texture memory object has 46 named fields in the a6xx layout and 53 in the a8xx
  layout. 37 names are common, and none of the 37 sits at the same dword in both. The image base address is dwords 4-5 on
  a6xx (low part from bit 5) and 0-1 on a8xx (low part from bit 6); format, swap and swizzle are in dword 0 on a6xx and
  in dword 3 on a8xx. An X2 texture descriptor is therefore not an X1-85 one.
- [VERIFIED] The sampler has 20 names in each layout and 18 in common. Two (the border-colour index and the near
  mip-filter bit) are at identical positions, 14 are in the same dword at different bits (wrap modes, filters, LOD
  bounds, compare function, anisotropy and others), and two moved to another dword.
- [VERIFIED] The uniform-buffer descriptor is defined once, as two dwords (low base; a 17-bit high base plus a size in
  16-byte units), and the X2 path already uses that one definition (`MESA registers/adreno/a6xx_descriptors.xml:163-171`;
  `REPO compat/adreno-ib-command/validate.c:226` uses the `A8XX_DESC_A6XX_UBO_*` names).
- [OBSERVED] The generated header `REPO gpu/adreno-kext/include/AdrenoA8xxRegs.h` carries the a8xx texture and sampler
  layouts (237 and 84 definitions under the `A8XX_DESC_A8XX_TEX_MEMOBJ` and `A8XX_DESC_A8XX_TEX_SAMP` prefixes) and the
  shared uniform-buffer layout (14), but no a6xx texture or sampler layout; the generator hard-codes the three domains
  (`REPO tools/gen-a8xx-regs.py:162`). The IB validator hard-codes the a8xx texture layout when it checks the T# arrays
  of a package (`REPO compat/adreno-ib-command/validate.c:39-43, 91-125`) and names no sampler layout. [OBSERVED] The
  notices file records a sampler-descriptor packer in air2ir3, `gpu/air2ir3/src/a8xx_sampler.c`, that follows the a8xx
  variant of Mesa's sampler-state creation (the `gpu/air2ir3/src/a8xx_sampler.c` row of `REPO THIRD_PARTY_NOTICES.md`). [INFERRED] An a7xx profile needs the a6xx
  texture layout generated as well, and the descriptor packers (that sampler one, and whichever code encodes textures)
  need a6xx-layout counterparts; whether that is a parameter of the existing packer or belongs in the plug-in is for
  the F6 and F7 owners (§4.6). [UNVERIFIED] The code of both submodules.
- [VERIFIED] Every opcode the validator's policy comment lists (17; `REPO compat/adreno-ib-command.h:9-18`) is defined for
  A7XX in `MESA registers/adreno/adreno_pm4.xml:261-665`: three have no restriction (`CP_NOP`, `CP_WAIT_FOR_IDLE`,
  `CP_DRAW_INDX_OFFSET`), `CP_SET_DRAW_STATE` is A4XX and later, five are A5XX and later (the two skip-IB2 packets,
  visibility override, context register bunch, blit), four are A6XX and later (`CP_SET_MODE`, `CP_SET_MARKER` and the two
  load-state packets) and four are A7XX and later (`CP_CCHE_INVALIDATE`, `CP_THREAD_CONTROL`, `CP_EVENT_WRITE7`,
  `CP_NON_CONTEXT_REG_BUNCH`). [OBSERVED] The dispatcher handles three more (`CP_WAIT_MEM_WRITES`, `CP_EXEC_CS`,
  `CP_MEMCPY`; `REPO compat/adreno-ib-command/validate.c:385, 413-414`), so it has 20 opcodes against the comment's 17;
  [VERIFIED] they are defined for A7XX too (`adreno_pm4.xml:464, 473, 586`; the memory copy is A6XX and later, the other
  two have no restriction). The ten event ids the dispatcher admits for `CP_EVENT_WRITE7` (`validate.c:342-344`) are
  A7XX and later, except the debug label (A6XX and later) (`adreno_pm4.xml:120-155`). The file gives one value per name,
  so no opcode or event id changes between generations.
- [VERIFIED] Across the whole PM4 file only seven elements are restricted to A8XX and later: three event ids, two opcodes
  (memory-map update and barrier), one poll-type value and one `CP_SET_MARKER` stripe (`adreno_pm4.xml:156-158, 671, 673,
  1493, 1889`); the validator admits none of the events or opcodes. Only one element is limited to a range ending at A7XX:
  that same marker's other stripe (`adreno_pm4.xml:1849`).
- [VERIFIED] `CP_SET_MARKER` word 0 has two layouts (`adreno_pm4.xml:1849-1898`): A6XX-A7XX has the mode in bits 3..0,
  uses-GMEM in bit 4 and three ray-tracing workaround bits at 9-11; A8XX and later has the mode in bits 6..0 and
  uses-GMEM in bit 7. The three words the validator accepts are the render modes 1, 8 and 12 with no other bit set
  (`REPO compat/adreno-ib-command/validate.c:392-395`, values at `AdrenoA8xxRegs.h:310, 317-318`). They are the same numbers
  under both layouts because the mode field starts at bit 0 in both. The generated header already holds both families of
  marker macros (`AdrenoA8xxRegs.h:7418-7479`).
- [VERIFIED] The repository's packet-header packers (`REPO tools/gen-a8xx-regs.py:360-374`, emitted at
  `AdrenoA8xxRegs.h:736-750`) produce the same bit layout as the one `PKT4`/`PKT7` macro pair in Linux's common Adreno
  header: a type tag in the top bits, a parity bit for the count, an 18-bit register or 7-bit opcode field, and a parity
  bit for that field (`LNX adreno/adreno_gpu.h:733-757`). The header has no per-generation variant, and the a6xx driver
  file, which also serves a7xx, emits its ring packets through it (53 uses in `adreno/a6xx_gpu.c`). [UNVERIFIED] That the
  gen70500 SQE firmware accepts exactly this, beyond Linux's own use.

## 3. What the pinned Mesa contains for gen70500

This section reports what the pinned Mesa text says about the X1-85. No Mesa tool, build or test was run and no compiler
output was examined. Whether the compiler produces correct code for gen70500 is task F7's question (PLAN row F7 and risk
R13); a separate worker verifies Mesa support for it, so this section stays on the text.

### 3.1 Device-table entries

- [VERIFIED] The X1-85 block (`MESA common/freedreno_devices.py:1279-1299`) gives one property record four reference ids:
  the legacy number `GPUId(740)` (line 1280; a comment calls it deprecated and kept for development kernels),
  `0x43050a01` named "FD740" (1281; a comment ties it to KGSL, which supplies no speed-bin data), `0xffff43050a01`, also
  "FD740" (1282; a comment calls it the default when no bin is known) and `0xffff43050c01`, "Adreno X1-85" (1283). Mesa
  therefore gives the A740 and the X1-85 identical properties, although Linux keeps two catalog entries with different
  firmware (§1.2). Mesa's generator allows that when the differences are not visible to software or are parameters the
  kernel reports, such as the GMEM size (`MESA common/freedreno_dev_info.py:58-61`).
- [VERIFIED] Side by side with the two X2 entries (the X2 ids are 3 and 4 slices; §3.2):

| Property | X1-85 (`freedreno_devices.py:1279-1299`) | X2-85 (1631-1648) | X2-90 (1650-1667) |
| --- | --- | --- | --- |
| Reference id | `0xffff43050c01` | `0xffff44070031` | `0xffff44070041` |
| Name | "Adreno X1-85" | "Adreno (TM) X2-85" | "Adreno (TM) X2-90" |
| Chip enum | `CHIP.A7XX` | `CHIP.A8XX` | `CHIP.A8XX` |
| Property sets, in order | `a7xx_base`, `a7xx_gen2` | `a7xx_base`, `a7xx_gen3`, `a8xx_base`, `a8xx_gen2` | the same four |
| `num_ccu` (also the SP-core count, `freedreno_dev_info.py:174`) | 6 | 6 | 8 |
| `num_slices` | not set; default 0 | 3 | 4 |
| Tile alignment, width x height | 96 x 32 | 96 x 32 | 64 x 64 |
| Largest tile, width x height | 2016 x 2032 | 16416 x 16384 | 16384 x 16384 |
| VSC pipes / compute shared memory / wave granularity | 32 / 32 KiB / 2 | 32 / 32 KiB / 2 | 32 / 32 KiB / 2 |
| `highest_bank_bit` | 16 (line 1296) | not set; default 15 | not set; default 15 |
| Named magic registers | 3 (`a740_magic_regs`, 965-969) | none (1646) | none (1665) |
| Raw magic registers | 36 (`a740_raw_magic_regs`, 971-1012) | 22 (`a8xx_base_raw_magic_regs`, 1471-1498) | the same 22 |

- [VERIFIED] Properties the sets do not name come from the record constructor's defaults
  (`MESA common/freedreno_dev_info.py:154-190`): bank bit 15, swizzle `0x6`, macrotile mode 1, thread-size base 64, 16
  waves, zero slices, and a `compute_lb_size` of 40 KiB for a7xx and later. The X2 entries set no bank bit, so Mesa uses
  15 for them while the Linux UBWC entry that §2.5 takes to be the X2's says 16. [INFERRED] That is irrelevant while the
  guest path keeps UBWC off (`REPO gpu/adreno-kext/include/AdrenoContract.h:75`); it is an X2 item, not an SP11 one.

### 3.2 How an id reaches an entry

- [VERIFIED] The matcher (`MESA common/freedreno_dev_info.c:21-65`) is applied to each record in table order and the
  first match wins (loop at 67-76; `fd_dev_info`, 78-89, copies the record and applies debug overrides). If both ids carry
  a legacy `gpu_id`, only those are compared (27-28); otherwise a device id with a zero chip id never matches (30-31).
  After that come, in order: an exact match (36-37); a reference whose patch byte is `0xff`, which matches any patch
  level with the same core, major and minor (41-44); and, only when the reference has all of bits 47..32 set (the
  wildcard fuse field, 45 and 49), the same two rules again after the device id has been ORed with that wildcard (50,
  53-54, 58-61).
- [VERIFIED, from those rules] `0xffff43050c01` therefore matches a bare `0x43050c01` and any id that carries a speed-bin
  value in bits 47..32, provided bits 63..48 are zero. It does not match the A740 ids, whose low bits differ.
- [VERIFIED] The X2 reference ids carry the enabled-slice count in bits 7..4 (3 and 4), the same OR the Linux a8xx code
  performs at run time (§1.3), and each entry's `num_slices` agrees.
- [OBSERVED] The X2 chip id is hard-coded at four sites in this tree: `REPO probes/adreno.py:70` (`CHIP_ID`, whose
  comment says it is the catalog id with four slices ORed in and was proven in q1n1), `tools/cycle.py` (the help
  text of `--gpu-x290` in `parse`), `tools/test-adreno-image.py:149` (asserts the guest GPU node's `chip-id`) and
  `tools/plan-adreno-native-triangle.py:46` (asserts a fixture's `chip_id`). The plan names the value too
  (PLAN section 4.1, GPU row, and row F7).
- [INFERRED] F7's `chip_id` default for the X1-85 would be `0x43050c01`, on which Linux and Mesa agree; the tests that pin
  `0x44070041` need a second pinned value rather than a changed one. [UNVERIFIED] Whether air2ir3 and the plug-in carry
  further sites of their own (empty submodules).

### 3.3 Property sets

- [VERIFIED] A property no set names is zero or false. The generated record is a C designated initialiser holding only
  the names that were set (`MESA common/freedreno_dev_info.py:104-108`, template at 241-243, `apply_props` at 211-223), so
  C's zero-initialisation applies to the rest.
- [VERIFIED, from that rule] `has_dual_wave_dispatch` is named only in `a8xx_base` (line 1459), so it is false on the
  X1-85. ir3 sizes its branch stack from it, 512 with the flag and 256 without (`MESA ir3/ir3_compiler.c:242`), so
  the X1-85 gets 256 and the X2 512.
- [VERIFIED] `a7xx_base` (`freedreno_devices.py`, starting at line 783) enables double thread size (791), a register file of
  96 vec4 (802), the 2- and 4-component dot-product accumulates (816-817), LPAC, fiber-id reads and MOVS (813-815). The a8xx
  layers undo or replace several of these: no 2-component dot-product accumulate (1455), no double thread size (1458),
  dual-wave dispatch (1459), no round-robin erratum (1460), larger buffer-range limits (1461-1462), and in `a8xx_gen2` a
  128-vec4 register file (1521), the VPC and CCU buffer sizes (1522-1535), no fragment-shader texture prefetch (1536) and
  an integer-narrowing quirk (1538). [INFERRED] The property meanings were not read; the names are the only basis for
  treating them as hardware differences.
- [VERIFIED; computed on the host from the pinned text, method in Appendix A] The X1-85's `a7xx_gen2` set (861-879) has 14
  names and the X2's `a7xx_gen3` (881-914) has 31. They share 11 names, 10 with equal values; `QCTDD08901551` is true in
  gen2 and false in gen3. Gen2-only (3): `QCTDD08517960`, `enable_tp_ubwc_flag_hint`,
  `reading_shading_rate_requires_smask_quirk`. Gen3-only (20): `QCTDD12766770`, `QCTDD13523866`,
  `gmem_vpc_attr_buf_size`, `gs_vpc_adjacency_quirk`, `has_abs_bin_mask`, `has_alias_rt`, `has_compliant_dp4acc`,
  `has_generic_clear`, `has_gmem_vpc_attr_buf`, `has_persistent_counter`, `has_rt_workaround`, `has_sw_fuse`,
  `load_inline_uniforms_via_preamble_ldgk`, `load_shader_consts_via_preamble`, `max_draw_states`, `new_control_regs`,
  `subpass_fence_cleans_resolve`, `sysmem_vpc_attr_buf_size`, `ubwc_all_formats_compatible`, `ubwc_coherency_quirk`.
  The X2 then layers `a8xx_base` and `a8xx_gen2` on top, so the X1-85 profile is not a subset of the X2's and a compiler
  or encoder tuned to the X2's properties is not tuned to the X1-85's.

### 3.4 Magic registers and CP_PROTECT

- [VERIFIED] The X1-85 has 3 named magic registers, all zero, and 36 raw register writes; the X2 has no named ones and 22 raw
  writes (§3.1 table). A comment above the a8xx list says that on that generation the chicken bit and most other
  non-context programming move into the kernel (`MESA common/freedreno_devices.py:1468-1470`).
- [VERIFIED; computed on the host from the pinned text, method in Appendix A] None of the 36 raw X1-85 registers lies in a
  range of the generated 48-word table (36 resolved, 0 unresolved, 0 protected). Control: the X2's 22 raw registers
  against the X2's own 52-word table give 0 protected as well. The check can detect a hit; probes on the a7xx table:

| Register | Dword offset | Result against the 48-word table |
| --- | --- | --- |
| `CP_SQE_CNTL` | `0x808` | slot 6, no read or write |
| `CP_RB_CNTL` | `0x802` | slot 6, no read or write |
| `CP_PROTECT_CNTL` | `0x84f` | slot 6, no read or write |
| `RBBM_SECVID_TRUST_CNTL` | `0xf400` | slot 34, no read or write |
| `RBBM_STATUS` | `0x210` | slot 0, read-only |
| `UCHE_CACHE_WAYS` | `0xe17` | not protected |

- [INFERRED] None of the registers stock Mesa initialises on the X1-85 would be refused by the protected table. Whether
  the plug-in emits them, whether an a7xx known-register set would admit them, and whether Mesa's drivers consume the
  tables as initial state were not checked (the consumer code was not read).
- [VERIFIED; computed on the host, method in Appendix A] Of the 316 names the X2 IB list holds (§4.4), the 293 that exist
  for A7XX expand to 858 dwords, and none lies in a range of the 48-word table. A control that adds one synthetic range
  (`0x8000-0x80ff`) makes 37 of the names hit. The same expansion applied to A8XX gives 930 dwords for the 316 names, the
  count `ADRENO_IB_KNOWN_OFFSETS` holds (`REPO compat/adreno-ib-tables.h:40`), which supports the expansion rule. [INFERRED]
  A known-register set built from these names would again lose nothing to the protected table, as today
  (`ADRENO_IB_KNOWN_PROTECTED 0u`, line 41); that says nothing about the registers an a7xx plug-in would actually emit.

### 3.5 ir3 compiler set-up

- [VERIFIED] The set-up reads the generation from the device id (`MESA ir3/ir3_compiler.c:236`; `CHIP.A7XX` and
  `CHIP.A8XX` suggest 7 and 8, INFERRED because the mapping function was not read) and keeps the info record (239).
  Before any generation switch the defaults are one predicate register, no predicate writes from bit operations, no
  branch-and/or, no repeat-bary, no alias texture, and delay slots of 3 (ALU to ALU), 6 (non-ALU) and 2 (cat3 source 2
  read) (247-254). Alias texture is enabled from generation 7 (319). Generation 7 only (the test is `== 7`, lines
  321-325) sets the delay slots to 2, 5 and 1. [INFERRED] Generation 8 therefore keeps 3/6/2: the X1-85 and the X2 get
  different instruction timing, so the generation must come from the profile, never from a constant.
- [VERIFIED] The compute constant limit is 512 for every generation 7 and later (`max_const_compute`, line 285). A comment
  just above it (279-284) says compute shaders have a constant file of their own, smaller than the fragment one, that the
  size was doubled for a7xx and that this does not work on the X1-85, and carries a TODO about earlier generations. The
  assignment does not special-case the X1-85. [UNVERIFIED] Whether a driver-level override adjusts it (not searched).
  [INFERRED] The X1-85's usable compute constants may be below the compiler's 512; F7 must decide.

### 3.6 What this tree pins, and what the submodules patch

- [OBSERVED] The Mesa pin `bbeb2dca9fee` is recorded in the tree at `REPO tools/build-adreno-arm64e-mesa.py:15`,
  `tools/gen-adreno-ib-tables.py:64`, `tools/test-draw-contract.sh:149`, `tools/gen-a8xx-regs.py:7` and in the generated
  headers (`gpu/adreno-kext/include/AdrenoA8xxRegs.h:33, 60`; `compat/adreno-ib-tables.h:25`).
- [OBSERVED] The notices file says air2ir3 carries patches against that commit (the `gpu/adreno-kext/include/AdrenoA8xxRegs.h`
  and `gpu/air2ir3/patches/*.patch` rows of `REPO THIRD_PARTY_NOTICES.md`; their content is UNVERIFIED, the submodule is
  empty) and is built against Mesa at the pinned revision (the `gpu/air2ir3/` (compiler) row). q1n1's Stage 6
  uses the same commit plus its own patch to ir3's register allocation (`Q1N1 testdata/a16gpu-render/README.md:28`).

### 3.7 Plan risk R13

[INFERRED] PLAN risk R13 (section 10: the pinned Mesa lacks gen70500) is partly answered by this text. For the device
table and the register XML no pin move is needed: an X1-85 entry exists, in wildcard form, and the XML has A7XX variants
(§3.1, §3.2, §2.1). For AIR-to-ir3 code generation the question is open here, because no compiler was run and the
generation-7 differences of §3.5 are not a verdict on correctness; that is F7's work.

## 4. Ownership and cutover plan [INFERRED; nothing in it is implemented]

This is the "ownership and cutover plan" PLAN row F1 (WS-F) asks for. It follows the repository rules: no
parallel implementation (AGENTS.md heading "No Parallel Implementation Proliferation", line 118), each layer owns its own
contract (heading "Respect Subsystem Ownership", line 124), opt-in semantics (non-negotiable 3, line 63) and the SP11
gate (`REPO compat/platform.h:12-16`: every SP11 path under `#if SNAP_PLATFORM_SP11` with the A16 behaviour in the
`#else`, nothing in the SP11 branch reachable from an A16 build).

### 4.1 Layer owners

| Layer | Owner | Where it lives today | What an X1-85 profile changes |
| --- | --- | --- | --- |
| EL2 GPU service | F2 (power, clocks, GMU and firmware load), F3 (first submissions) | `REPO compat/adreno.c`, `adreno.h`, `gpu-dvfs.h`, `adreno-submit.*` | Physical constants and register addresses (§2.1), RSC/PDC and SECVID handling (§2.3, §2.7), the DVFS table (§2.12): all X2-specific today |
| IB firewall | No F row names it (§5) | `REPO compat/adreno-ib-command.{c,h}`, `adreno-ib-command/*.c`, `adreno-ib-tables.h` | Register rows and bitmaps, the protected table (48 words against 52), the descriptor layout, the packet and marker set, the address-register policy (§4.4) |
| Coherency | F2/F3 (part of the EL2 GPU service); reuse the gated self-test (§2.10) | `REPO compat/adreno-coherent.{h,c}` | Nothing beyond what §2.10 lists |
| Kext | F5 (SnapAdreno first, then a separate `repo:` pointer bump) | `gpu/adreno-kext/kext` (empty here) | UNVERIFIED |
| Plug-in | F6 (SnapMetal first) | `gpu/adreno-kext/plugin` (empty here) | Descriptor layout, register list, markers, address kinds (§4.6) |
| Compiler | F7 | `gpu/air2ir3` (empty here) | `chip_id`, generation-7 timing, compute constants (§3.5) |
| Host tools | No F row names an owner | `REPO tools/gen-a8xx-regs.py`, `tools/gen-adreno-ib-tables.py`, `tools/cycle.py`, `probes/adreno.py` | Chip and descriptor-domain parameters (§4.4), the hard-coded chip id (§3.2) |

### 4.2 Rules

1. One profile seam per layer, selected under `SNAP_PLATFORM_SP11` with the A16 behaviour in the `#else`
   (`REPO compat/platform.h:12-16`). `platform.h` itself must stay free of code and data, because
   `tools/monitor_baseline.py` depends on that (lines 23-24), so profile data lives in the layer's own header, one
   header per subsystem interface and not one per feature.
2. With the gate off the A16 monitor ELF stays byte-identical, checked with `tools/monitor_baseline.py`
   (`REPO docs/BUILDING.md`, "Reproducing the A16 monitor baseline"; PLAN section 9.2), and the kext sections are compared where the build is
   deterministic (AGENTS.md non-negotiable 3).
3. No second copy of any module, header, kext bundle, plug-in, compiler, generator or Mesa fork (AGENTS.md line 118).
4. GPL isolation. The generated 48-word header is GPL-2.0-only. The notices file says a binary built with the X2's
   GPL table is a combined work (the `compat/adreno-ib-tables.h` row of `REPO THIRD_PARTY_NOTICES.md`), so the first inclusion of the new header in a monitor
   or kext build would, on the same reading, have the same effect. That is a decision for the repository owners (§5);
   until it is made nothing includes the header.
5. Fail closed: an unknown profile, chip id or GMU chip-id word is refused, never defaulted (AGENTS.md section 8, rule 4,
   "Make errors explicit").
6. Nothing is deleted. The X2 path stays, and the retained `gpu/agx*` reference material is untouched (AGENTS.md
   non-negotiable 7, line 92).

### 4.3 Stages

| Stage | Scope | Evidence needed before the next stage | Hardware |
| --- | --- | --- | --- |
| 0 | Today: X2 only, no profile seam | Baseline hashes captured (`tools/monitor_baseline.py capture before`) | none |
| 1 | Profile accessors whose values are today's a8xx constants; no SP11 content | Gate-off byte identity of the monitor ELF and, where deterministic, the kext sections; the host tests of the firewall unchanged; static assertions that each accessor equals today's `#define`; the allowlist and address bitmaps byte-compared; the a8xx header regenerated byte-identically through the parameterised generator (§4.4) | none |
| 2 | The a7xx profile under `SNAP_PLATFORM_SP11`: an A7XX register header with the A6XX descriptor layouts from the same Mesa pin, the generated 48-word table, negative tests | Tests that refuse a protected a7xx register, an X2-only register, an a8xx texture descriptor, an unknown opcode and an unknown chip id | none; the X8a read-back is an operator step |
| 3 | Plug-in encodings and compiler (F6, F7) | The X8 sub-gates of the plan | per gate |

What a profile carries (each is a constant or table of today's firewall): the protected words and their count (52 against
48), the last-span flag, the known-offset counts (930 = 893 draw + 37 compute, protected 0, `compat/adreno-ib-tables.h:38-41`),
the register rows (316 = 284 draw + 32 compute, plus 15 monitor registers), the descriptor-layout family, the marker
family, the opcode set (20) and the address-register policy (23 registers).

### 4.4 The IB-firewall seam

- [OBSERVED] `adreno_ib_tables_init` (`REPO compat/adreno-ib-command/stage_relocate.c:105-146`) builds the allowed bitmap
  from the generated rows (`A8XX_REGS_IB_COUNT`, 316, line 111) and fails closed when an address row is not classified by
  `register_field` (116-125), when the number of known dwords differs from `ADRENO_IB_KNOWN_OFFSETS` (134, "the two
  generators no longer agree") or when the number removed by the protected table differs from
  `ADRENO_IB_KNOWN_PROTECTED` (143). Those constants and the protected table are chip-specific
  (`compat/adreno-ib-tables.h:33-42`), so the checks are per-chip by construction. The register limit
  (`ADRENO_IB_REG_LIMIT`, `0x10000`) and the address-row capacity (32) are in `compat/adreno-ib-command.h:50-51`.
- [OBSERVED; computed on the host from the pinned text, method in Appendix A] Of the X2 IB list's 316 names (284 draw
  plus 32 compute), 203 sit at identical offsets in A7XX, 90 at different offsets and 23 have no register of that name at
  A7XX (an equivalent under another name is not determined); 0 are unresolved at A8XX. All 32 compute names are the same.

| Block | Same | Moved | No A7XX name | Total |
| --- | --- | --- | --- | --- |
| GRAS | 4 | 57 | 3 | 64 |
| PC | 0 | 11 | 4 | 15 |
| RB | 73 | 2 | 6 | 81 |
| SP | 92 | 2 | 4 | 98 |
| TPL1 | 16 | 0 | 2 | 18 |
| VFD | 18 | 0 | 0 | 18 |
| VPC | 0 | 18 | 4 | 22 |
| Total | 203 | 90 | 23 | 316 |

  The 23 names with no A7XX register: `GRAS_BIN_FOVEAT_XY_FDM_OFFSET`, `GRAS_LRZ_BUFFER_SLICE_PITCH`, `GRAS_RAST_CNTL`,
  `PC_BV_POS_BUF_GMEM_SIZE`, `PC_POS_BUF_GMEM_SIZE`, `PC_UNKNOWN_980B`, `PC_VS_INPUT_CNTL`, `RB_DEPTH_GMEM_DIMENSION`,
  `RB_LB_PARAM_LIMIT`, `RB_MRT_GMEM_DIMENSION_REG`, `RB_PS_CNTL`, `RB_RESOLVE_CNTL_5`, `RB_STENCIL_GMEM_DIMENSION`,
  `SP_ALPHA_TEST_CNTL`, `SP_BIN_SIZE`, `SP_MRT_BLEND_CNTL_REG`, `SP_UNKNOWN_AB23`, `TPL1_A2D_BIN_SIZE`, `TPL1_BIN_SIZE`,
  `VPC_BV_POS_BUF_GMEM_BASE`, `VPC_BV_POS_BUF_GMEM_SIZE`, `VPC_POS_BUF_GMEM_BASE`, `VPC_POS_BUF_GMEM_SIZE`. An example of
  the duplicate-name pattern: the XML defines `GRAS_CL_CNTL` twice, at `0x8000` for A6XX-A7XX and `0x8200` for A8XX-
  (`MESA registers/adreno/a6xx.xml:1436-1437`).
- [OBSERVED; same method] Of the 15 monitor register names the generator also emits, 5 are at the same offset, 7 moved
  (`RBBM_GBIF_HALT`, `RBBM_GBIF_HALT_ACK`, `RBBM_STATUS`, `RBBM_INT_0_MASK`, `RBBM_INT_0_STATUS`, `CP_SQE_CNTL`,
  `CP_SQE_INSTR_BASE`) and 3 have no A7XX register of that name (`CP_APRIV_CNTL_PIPE`, `CP_PROTECT_CNTL_PIPE`,
  `CP_INTERRUPT_STATUS_GLOBAL`).
- [INFERRED] The a7xx IB list is not a recolouring of the a8xx one. It has to be derived from what the a7xx plug-in
  emits; the 242 names that exist only for A7XX (§2.1) cannot appear in a header generated for A8XX. None of this is a
  claim about what the firewall should admit.
- [VERIFIED; computed on the host from the pinned text, method in Appendix A] The address-register policy ports with one
  offset change. `register_field` (`REPO compat/adreno-ib-command/stage_relocate.c:45-89`) names 23 registers. 22 of them
  are at the same offset in A7XX and A8XX and the one that moved is `GRAS_LRZ_BUFFER_BASE` (`0x8104` for A8XX-, `0x8103`
  for A6XX-A7XX; `MESA registers/adreno/a6xx.xml:2060-2061`). 22 of the 23 are typed as address or write-address under A7XX
  and under A8XX, with the same split; the 23rd, the bindless descriptor base, is a 64-bit register with bit fields whose
  definition is the same from A7XX on (`a6xx.xml:4321-4326`; the A6XX variant is a different array at 4315-4320). The
  generated table flags 23 rows as address registers and 9 as GPU-written
  (`REPO gpu/adreno-kext/include/AdrenoA8xxRegs.h`, `a8xx_ib_regs` from line 8426; flag values at lines 66-67). [UNVERIFIED]
  Whether the plug-in's a7xx packages use the same address kinds. The validator's own comment still says "18 address
  registers" (§6).
- [OBSERVED] The generator that makes the register header has the chip written into it: `CHIP = 'A8XX'` (`REPO
  tools/gen-a8xx-regs.py:71`), the descriptor domains as a fixed tuple (162), `covers(variant, chip=CHIP)` binding that
  default when the function is defined (186), `XML_FILES` already listing `a6xx_descriptors.xml` (182-183), the descriptor
  loop over named files (405-409) and the name lists (`DRAW_REG_NAMES` 74, `CS_REG_NAMES` 133, `IB_REG_NAMES` 143,
  `MONITOR_REG_NAMES` 147). The generator was not run (it imports Mesa's `gen_header.py`, which was not fetched; §0.3).
  [INFERRED] The a7xx header needs the chip, the domains and the name lists as parameters, not a copy of the generator.
- [OBSERVED] `validate.c` hard-codes the a8xx texture layout (`REPO compat/adreno-ib-command/validate.c:39-43, 91-125`) and
  the packet and marker set (377-417), so the descriptor layout is a firewall dependency as well as a plug-in one
  (§2.14). [INFERRED] An a7xx profile needs the layout accessors chosen by the profile.
- [OBSERVED] The notices row says the AdrenoX2 kext compiles `compat/adreno-ib-command.c` and that a monitor build may
  include it (the `compat/adreno-ib-tables.h` row of `REPO THIRD_PARTY_NOTICES.md`). [INFERRED] A profile seam in the firewall therefore has to hold in both
  builds, and the byte-equivalence evidence of stage 1 has to cover both.

### 4.5 Retirement

[INFERRED] Nothing is retired. A profile is not a replacement; any removal of X2 code needs the repository owners'
approval, an equivalence test and a stated cutover, as AGENTS.md section 1 requires for replacements.

### 4.6 Hand-offs

- **F5 (kext, SnapAdreno).** One kext with a profile, not a second bundle. The bundle id `org.snapintosh.driver.AdrenoX2`,
  class `AdrenoAcceleratorX2` and name `AdrenoX2` are X2-named (`REPO gpu/adreno-kext/include/AdrenoContract.h:27-31`);
  whether they stay is a decision for the repository owners (§5), and whether the plug-in matches on them is UNVERIFIED.
- **F6 (plug-in, SnapMetal).** The a6xx texture and sampler layouts (§2.14), the a7xx register list (§4.4), the marker
  words (the same three numbers), and the address kinds (22 of 23 registers at the same offset).
- **F7 (air2ir3).** `chip_id` as a parameter with `0x43050c01` for the X1-85 (§3.2), generation-7 delay slots and branch
  stack (§3.5), the compute constant limit (§3.5), and the sampler packer's a6xx counterpart (§2.14).
- **Owner of `tools/gen-a8xx-regs.py`.** The chip and descriptor-domain parameters (§4.4). No F row names this owner.
- **Unassigned.** The IB-firewall seam of §4.4: F3's submissions would pass through it and F5 compiles it into the kext,
  but no row asks anyone to port it.

## 5. Open questions and falsifiers [UNVERIFIED]

Each row is something this study could not settle from public text and this tree. The last column says what would settle
it. Rows that need the unit are operator steps under the plan's X8 gates and need their own authorization; nothing in this
document authorizes them.

| # | Question | Settled by |
| --- | --- | --- |
| 1 | Which GMU and SQE firmware versions the unit provides, and whether it accepts the HFI version Linux advertises (§1.5, §2.3) | X8a, on the unit; this study read only the public `WHENCE` text |
| 2 | Whether the boot firmware leaves the GPU RSC and PDC programmed, or EL2 must program them as Linux does for a7xx (§2.3) | An X8a read-back of that state before any write |
| 3 | Whether the unit needs the SECVID write, and whether the zap image is needed at EL2 (§2.7) | X8a and X8b observation (PLAN row F2, note N2) |
| 4 | Whether the unit requires the CP_PROTECT table to equal Linux's, and whether its firmware keeps ranges Linux does not list (§2.6) | A supervised read-back; nothing in this study proves it |
| 5 | The conservative DVFS ceiling: the device tree's 550, 390 and 300 MHz points are valid for every speed bin (§2.12) | An operator choice; any raise needs explicit authorization (PLAN section 8) and the thermal guard stays on (PLAN H1) |
| 6 | Which passthrough-window holes the X1-85 needs (§2.1) | Checking the X1-85 block map against the monitor's windows; until then trap everything outside an allowlist |
| 7 | The register identity of the X1E80100 GPU clock controller | Reading Linux `gpucc-x1e80100.c`, which was not read |
| 8 | Whether the coherency self-test and PMU event `0x17` behave on this unit (§2.10) | The gated self-test at X8 |
| 9 | Whether PM4 behaves on the unit as the XML says (§2.14) | The NOP, WFI and bounded-copy steps of F3 |
| 10 | Which texture-descriptor layout the plug-in emits and the kext expects, and which address kinds the a7xx packages use (§4.4) | Reading SnapMetal and SnapAdreno, whose submodules are empty here |
| 11 | Whether an a7xx known-register set admits the 36 raw magic registers (§3.4), and whether the 23 IB names with no A7XX register have equivalents under other names (§4.4) | Building the a7xx list from the plug-in's output and reading the XML for each name |
| 12 | Whether a driver-level override adjusts the X1-85's compute-constant limit, and whether Mesa's drivers consume the magic-register tables as initial state (§3.4, §3.5) | Reading the Mesa driver code, which was not searched |
| 13 | Whether the kext bundle id and class keep their X2 names (§4.6) | A decision of the repository owners |
| 14 | Who owns the IB firewall's profile seam and the chip parameters of the register generator (§4.4, §4.6) | A decision of the repository owners |
| 15 | The minimum silicon read-back at X8a (§1.6) | The operator, per the plan |
| 16 | Everything in the kext, plug-in and air2ir3 submodules (§0.3) | Initialising them in the tree used for the follow-up work |

Standing constraints that no answer above relaxes: one hardware client at a time; AC power or at least 50 % battery with
the thermal guard enabled; a single `CP_MEMCPY` never larger than 1 MiB; tracked sources unchanged between `--prepare`
and `--boot` (AGENTS.md non-negotiable 5).

## 6. Corrections and clarifications [VERIFIED]

Statements in this tree, the plan or the q1n1 checkout that differ from the pinned sources. Each concerns wording or
provenance; none changes behaviour.

1. **CP_PROTECT macro names.** `ADRENO_PROTECT_RW` and `ADRENO_PROTECT_RDONLY` are older a5xx-era macros with their own
   layout (flag bits 30 and 29, a log2 length field; `LNX adreno/adreno_gpu.h:782-793`). The a6xx driver and the a7xx and
   a8xx catalogs use `A6XX_PROTECT_NORDWR` and `A6XX_PROTECT_RDONLY` (`adreno/a6xx_gpu.h:245-255`), 46 times in
   `a7xx_catalog.c`, 147 in `a6xx_catalog.c` and 258 in `a8xx_catalog.c`; none of the three uses the older names. No
   text in this repository or in the q1n1 tools uses them either (searched).
2. **The X1-85 table name.** It is `a730_protect` (`LNX adreno/a7xx_catalog.c:137-189`), shared by six catalog entries
   (§1.2). None of the three catalog files contains `a740_protect`.
3. **Provenance of the X2 table.** Text in this tree and in q1n1 names `a6xx_catalog.c` as the source of the X2's
   `x285_protect_regs` and of the X2 catalog entry (`REPO compat/adreno-ib-tables.h:3-4, 20`;
   `tools/gen-adreno-ib-tables.py:3, 30, 89`; the `compat/adreno-ib-tables.h` row of `THIRD_PARTY_NOTICES.md`; `Q1N1 adreno_x2.py:734, 735, 738, 861, 1003,
   1008`). At the pin both are in `LNX adreno/a8xx_catalog.c` (the table at 346-399, the entry at 1580-1621), and the
   repository's 52 words equal that table (§2.6). The data are right; only the file name is stale at this pin.
   [UNVERIFIED] Whether the Linux release the text was written against kept the table in `a6xx_catalog.c`.
4. **Slot count.** The register XML declares 32 `CP_PROTECT` entries for A6XX-A7XX while the driver programs 48 slots
   (§2.6); the extra slots do not overlap another register.
5. **Missing documents.** `docs/ADRENO-STAGE4-20260925.md` is cited at `REPO compat/adreno.h:2` and `probes/adreno.py:4`;
   `docs/ADRENO-COMPUTE-PACKETS-20261019.md` at `compat/adreno-ib-command.h:124`,
   `tools/qualify-a8xx-compute-admission.py:5, 94` and `tools/test-draw-contract.sh:25`. Neither file exists under
   `docs/` in the tree this study used.
6. **The `0xffff` in Mesa's X1-85 ids.** A comment calls `0xffff43050a01` the default when no speed bin is known; the
   matcher treats the `0xffff` field as a wildcard that also matches an id carrying any bin (`MESA
   common/freedreno_dev_info.c:45-62`; `freedreno_devices.py:1282-1283`). It is not a "no speed bin" marker (§3.2).
7. **Line span of the X1-85 entry.** PLAN section 4.1 (GPU row) cites `a7xx_catalog.c:376-380`, which covers the chip id, the family
   and the firmware names; the entry spans 375-418 (§1.1). An earlier revision of the plan did not say so; section 4.1
   now states the span.
8. **Validator comment against validator.** The policy comment in `REPO compat/adreno-ib-command.h:8-35` lists 17
   opcodes (9-18), two marker modes (11-12) and "the 18 address registers" (21). The dispatcher handles 20 opcodes and
   accepts three marker words (`compat/adreno-ib-command/validate.c:377-417, 392-396`), and `register_field` names 23
   address registers (`compat/adreno-ib-command/stage_relocate.c:45-89`), matching the 23 flagged rows of the generated
   table (§2.9, §4.4).
9. **GPU naming.** README, ARCHITECTURE, STATUS and `gpu/adreno-kext/include/AdrenoContract.h:3` call the X2 GPU "Adreno
   X2-90"; an earlier revision of the plan said "X2-85" for chip `0x44070041` (section 4.1, GPU row; corrected there to
   X2-90). Mesa's X2-90 reference id is `0xffff44070041` (four slices)
   and its X2-85 id `0xffff44070031` (three; `MESA common/freedreno_devices.py:1631-1667`), and `REPO
   tools/cycle.py` (the help text of `--gpu-x290` in `parse`) already says X2-90.
10. **Licence identifiers.** `LNX adreno/a6xx_gpu.h` and `a6xx_gpu.c` carry the deprecated SPDX identifier `GPL-2.0`; the
    generated header repeats each source's own notice and is itself GPL-2.0-only, the identifier the existing
    `compat/adreno-ib-tables.h` and the plan use.

## Appendix A. Method notes

The cross-checks marked "computed on the host" were run from scratch scripts that are not part of this change; the F4
generator and its test are. Each can be repeated from the pinned files of Appendix B with Python's standard library.
PLAN cites name a section, gate or item id of `docs/SURFACE-PRO-11.md`, never a line, so they survive edits to it.

- **Register statistics (§2.1).** `MESA registers/adreno/a6xx.xml` was parsed with ElementTree. The population is the
  direct `reg32`, `reg64` and `array` children of the main `A6XX` domain that carry a `name` and an `offset`; other
  domains are excluded. A child's coverage is its `variants` attribute (a range such as `A6XX-A7XX` or `A8XX-`; no
  attribute means every generation). For each name the set of offsets covering A7XX was compared with the set covering
  A8XX: equal is "same", different is "moved", present on one side is "only", present on neither is "neither". The
  counts are 1426 names, 497 same, 278 moved, 242 A7XX-only, 319 A8XX-only and 90 neither.
- **Physical addresses (§2.1).** Address = `0x03d00000` + 4 x dword offset.
- **Descriptor statistics (§2.14).** ElementTree over the `reg32` and `bitfield` children of the named domains of
  `a6xx_descriptors.xml` and `a8xx_descriptors.xml`. A field's position is its dword plus its low and high bit; an
  element with a `pos` attribute has low equal to high. Names were compared between the two files.
- **PM4 variant scan (§2.14).** A text scan of `adreno_pm4.xml` for elements whose `variants` attribute names A8XX (7
  elements) or an A7XX range with an upper bound (1 element); the restriction of each opcode the validator lists was read
  from its own `variants` attribute.
- **IB register-name classification (§4.4).** The names come from the generator's lists (`REPO tools/gen-a8xx-regs.py:74,
  133, 147`). Each was resolved among the registers of the main `A6XX` domain that cover A7XX or A8XX. Array children are
  named `<array>_<child>`, and the generator names array registers with a `_REG` suffix, so a failed lookup was retried
  with the suffix removed. Names resolved at both generations were classed "same" or "moved" by offset; names that do not
  resolve at A7XX are "no A7XX name". None failed to resolve at A8XX.
- **IB names against the protected table (§3.4).** Arrays were expanded by `length` and `stride` and a 64-bit register
  counts as two dwords. The generated table was decoded as in `REPO compat/adreno-ib-command/stage_relocate.c:136-142`:
  inclusive `[register, register + length]`, with the last non-zero word extended to the end of the register space. Two
  controls: one synthetic range added (it makes 37 names hit), and the same expansion applied to A8XX (930 dwords, equal
  to `ADRENO_IB_KNOWN_OFFSETS`).
- **Magic-register cross-check (§3.4).** The raw register names were read from `freedreno_devices.py:972-1011` (X1-85,
  36 entries) and `1472-1497` (X2, 22 entries), resolved through the same XML and tested against the decoded tables.
  The control is the X2 list against the X2's 52-word table; the probe table of §3.4 shows the method detects hits.
- **Address-register policy (§4.4).** The 23 names were parsed from the switch in `stage_relocate.c:45-89`; offsets were
  compared as in the classification above, the type of each register's bit fields was read from the XML, and the flag
  values 2 and 4 were counted in `a8xx_ib_regs` of `AdrenoA8xxRegs.h`.
- **Property sets (§3.3).** The text of the `a7xx_gen2` and `a7xx_gen3` blocks (`freedreno_devices.py:861-879,
  881-914`) was split into name and value pairs and compared. Entry counts in the device table (36, 22) were made from
  the line ranges named in §3.1.
- **X2 table (§2.6).** The entries of `x285_protect_regs` were parsed, encoded with the `A6XX_PROTECT_*` rules and compared
  word for word with the 52 words of `adreno_ib_protect`: the sequences are identical.
- **Availability of the pins.** The Linux files were fetched at the commit through the GitHub API, the Mesa files at its
  commit from the project's GitLab (the GitHub mirror did not serve that commit), and `WHENCE` from the linux-firmware
  GitLab at its commit. Every file was hashed (Appendix B).

## Appendix B. Pinned inputs

In this table `LNX` is `linux-7475ea8b58616b33c04afb976cf0fbaad9dd63fb/`, `adreno/` is `drivers/gpu/drm/msm/adreno/`,
`regs/` is `drivers/gpu/drm/msm/registers/adreno/` and `MESA` is
`mesa-bbeb2dca9feed6ac88d71234a9dc5ae8aeed4275/src/freedreno/`. Sizes are in bytes. The files were fetched into the
git-ignored `build/ref/`; none is part of this change. Mesa's `gen_header.py` and the two Mesa files the register header
also records (`adreno_common.xml`, `a6xx_enums.xml`) were not fetched and are not listed.

| File | Bytes | SHA-256 |
| --- | --- | --- |
| `LNX arch/arm64/boot/dts/qcom/hamoa.dtsi` | 233339 | `d5bb1f7ce37c721d75fb5b4cffc8f58bed4d3ef9786a231a0deca05e917529e0` |
| `LNX arch/arm64/boot/dts/qcom/x1-el2.dtso` | 1352 | `d6a9666bd89e8787223ea2d14b8cb798669647da6bbf0f87b835719d409e2df1` |
| `LNX adreno/a6xx_catalog.c` | 44014 | `18381bd47f9376bd47c69fc3bfd67e7f2baa625e97ce28f4dcd0fba1aa180e99` |
| `LNX adreno/a6xx_gmu.c` | 70934 | `22bc34dafafff83827901221873c6aa770e20f9c17c60294593245324eee8272` |
| `LNX adreno/a6xx_gmu.h` | 6526 | `b9af7b6c0bdf0f4f3fabf70f59ba821bc783109b48cf7a8c04e6b7d07fef1b92` |
| `LNX adreno/a6xx_gpu.c` | 87809 | `9b725c1c56a9b6b804de0daa899fa03f51ddb6c8253904c77814540d5142f4cd` |
| `LNX adreno/a6xx_gpu.h` | 11390 | `3d11d36a6ec3f16876baaf6f8b76fcfcaa8e0b2991892e886fbe850682f87c70` |
| `LNX adreno/a6xx_hfi.c` | 31877 | `1758d381934a5ce8131fa500d4e02601e07af5c0b810eccd23a7d5b642204da9` |
| `LNX adreno/a6xx_hfi.h` | 7298 | `1e53d022fccaec3d658a4e939d0572c5711597fc8db5c4dec97ca08ee365c7e2` |
| `LNX adreno/a6xx_preempt.c` | 11639 | `3975a996c49fc756cc58a7cbf546914a78632638241e39a752214ab0ec64941f` |
| `LNX adreno/a7xx_catalog.c` | 16422 | `6941242f183b76143464237896f8b0b97f9a6533cb1efb6f834f2da4dcb6c799` |
| `LNX adreno/a8xx_catalog.c` | 72458 | `a53ef7a20e0c0c9e765a78b9510efa2039671607b5dde7bbfc82f4521aa74afb` |
| `LNX adreno/a8xx_gpu.c` | 42360 | `939e265b9cfb7bbf6afb821947d79c901c324ffb949f55ac5f2f2204aee09ff4` |
| `LNX adreno/adreno_device.c` | 10280 | `8d5fe7c653a0448eb23ea2cca435e6f9a0f85985452c7d1f036461932d650e17` |
| `LNX adreno/adreno_gpu.c` | 33870 | `1029397337cd20dcc4687ab852674d6d5409d76c880f829eab860bec1a70326f` |
| `LNX adreno/adreno_gpu.h` | 20885 | `c8fe321c6df3a4b2fc2f3f186cf6b3952fd0196f7e5c32b5d7b2740405dd3be6` |
| `LNX regs/a6xx.xml` | 281374 | `6e20d0347b06f26220f6b87d8600e94790331fec45363a167c5e63ea6ad8f9c2` |
| `LNX regs/a6xx_gmu.xml` | 14906 | `f13f07aa3c975c1d671c1a7f5ba62ddaaf525aedb7a0e481186cbfffe461f88b` |
| `LNX drivers/soc/qcom/ubwc_config.c` | 8022 | `ee1093dfa6799c452c26b7264d516acc688e33b8674a09a4a5f333ecf64c4f11` |
| `LNX include/linux/soc/qcom/ubwc.h` | 3419 | `50e76e143eb78157fdc952cbfc1b6241d790bf99725e31bf976ee4fbc29665b5` |
| `linux-firmware-69e51bb26f3fa3e118dd770f1962a93bed690dc1/WHENCE` | 532286 | `3632c15fcdf114586d53fd782f4e8cbe8e526d52b0dff48b1e83c17e34fc3b38` |
| `MESA common/freedreno_dev_info.c` | 2865 | `c26ac1937306ac15d5537ca8e3cfed1517cd644783ce88f7afbc75352175e6f7` |
| `MESA common/freedreno_dev_info.h` | 20118 | `d4f04e59ab4c20667d4a042bc30ae933360b13d5f48b32ceb940ff3b610e92f2` |
| `MESA common/freedreno_dev_info.py` | 10620 | `2daac8f970692e943b2bae323ff8058c93ba0b1c82204e8cc73634c406e8c798` |
| `MESA common/freedreno_devices.py` | 61810 | `279d60dac8558d56108fc7548bab2c5aab5ec8c18c5297f42cdf33211b82ca1d` |
| `MESA common/meson.build` | 1224 | `1b6e317c327bae665779d67d87135dd9b3d5cb2d413943e7b3f9072365105601` |
| `MESA ir3/ir3_compiler.c` | 16808 | `f936720fa2bea659b2911505739f3275325f4c34387d96a05e72298024d81687` |
| `MESA ir3/ir3_compiler.h` | 12246 | `37c47da07a7c601d767c1c8f75112e056fdb985914edea4e7407dac2d6e027cb` |
| `MESA registers/adreno/a6xx.xml` | 280781 | `c437285de46ddd5c054203615c941677cd1b30e5a5566b34b62ba653de6de246` |
| `MESA registers/adreno/a6xx_descriptors.xml` | 8874 | `6eb6ecd53653a3beb1d09c5855e5550bedc318b410eac870b5a99d8a3c61ce7d` |
| `MESA registers/adreno/a7xx_enums.xml` | 10053 | `8649a48eb99b71d3d882ed32021c0afb3400eb6623dc0a10afc2389bef40c497` |
| `MESA registers/adreno/a8xx_descriptors.xml` | 8703 | `9cd1aef6e79b34e70ab302357b1bf38cebe37fb375aad15b8a06520399eabf51` |
| `MESA registers/adreno/a8xx_enums.xml` | 15388 | `18d5d8f4871125be7a0fc6d99c33269779532180ced872f33e508be02fc6ceeb` |
| `MESA registers/adreno/adreno_pm4.xml` | 95214 | `ff1e308f0900f93e2601393bfd5bd7a7a2ec1c606644952f826ff91e530c8b77` |

[VERIFIED] The Mesa `a6xx.xml` hash equals the one the repository records for it, and so do five more of the Mesa files:
`gpu/adreno-kext/include/AdrenoA8xxRegs.h:35-42` stores 12-hex-digit prefixes (`c437285de46d`, `6eb6ecd53653`,
`9cd1aef6e79b`, `ff1e308f0900`, `8649a48eb99b`, `18d5d8f48711`) of `a6xx.xml`, `a6xx_descriptors.xml`,
`a8xx_descriptors.xml`, `adreno_pm4.xml`, `a7xx_enums.xml` and `a8xx_enums.xml`, and each equals the prefix of the pinned
file. The Linux copy of `a6xx.xml` differs from the Mesa copy because they are separate files.
