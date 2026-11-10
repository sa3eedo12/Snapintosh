# SP11 display study (WS-E, item P1): reuse the firmware pipeline

**Study and host-verified decision logic only. UNIMPLEMENTED on hardware. Hardware status: NOT RUN.** Nothing here
has run on a Surface Pro 11. Every statement about the SP11 is VERIFIED against a pinned public source (it says what
that source says, not what the unit does) or INFERRED, and the second kind stays INFERRED until the X2 functional
test has read a real unit ([SURFACE-PRO-11.md](SURFACE-PRO-11.md), section 6.3). The A16 build and its behaviour are
untouched: nothing in this study is linked by any build.

What the work package delivers:

| Piece | Where | What it is |
| --- | --- | --- |
| This study | `docs/SP11-DISPLAY-STUDY.md` | A16 audit, Hamoa display facts, panel size, backlight analysis, the X2 dump list (section 8) |
| Decision logic | `compat/display-pipeline-check.{h,c}` | A pure function: register snapshot in, verdict ACTIVE / INACTIVE / UNKNOWN / INCONSISTENT out. No I/O, no globals, no libc |
| Its tests | `tools/test-display-pipeline-check.{c,py}`, `tools/test-display-pipeline-check-mutants.py` | Sanitizer-built native harness, more than 130 scenarios, properties, a check that this study's dump list is the list the code uses, and a 41-bug mutation check |
| Mode validation | `tools/sp11_display_mode.py` | GOP report plus optional EDID in, ACCEPT or REJECT out; assumes no panel size |
| Its tests | `tools/test-sp11-display-mode.py` | Known public timings, malformed inputs, a mutation check |
| Study checks | `tools/test-sp11-display-study.py` | The A16 file-and-line citations, the study's structure, and (with a pinned Linux checkout) the Linux citations and SHA-256 pins |

The conclusions, each with its label:

1. **The firmware pipeline is the only display path in scope.** The pinned Linux device tree describes one panel on
   `mdss_dp3`, 4 lanes, up to 8.1 GHz per lane, no backlight node, no panel timing (VERIFIED, section 3). The mode is
   whatever the firmware's GOP and EDID say at run time.
2. **The Hamoa MDSS is at `0x0ae00000`** (VERIFIED in the pinned device tree, section 3). The eDP controller feeds from
   DPU interface INTF_5, whose page is `0x0ae3a000`. That equals the A16's INTF_5 address by coincidence of layout,
   not because the A16 constants apply.
3. **Almost nothing of the A16 display code may be reused as constants** (section 2). The dispcc clock-gate offsets
   differ, so the A16 header would read other registers on Hamoa. The backlight path is A16-specific end to end
   (section 7).
4. **The SP11 OLED is a 2880x1920 (3:2) panel** according to two public sources (section 4). An earlier revision of
   `docs/SURFACE-PRO-11.md` showed only the A16's 2880x1800 in its section 4.1 (the A16 column, not the SP11's) and
   named no SP11 size; section 4.1 now names 2880x1920 for the SP11 (Display row, with E33 recording the sources). The
   tool decides nothing from these numbers.
5. **Backlight: leave it as the firmware set it** (section 7).
6. **Whether the pipeline is active is decided from a short, ordered, side-effect-free register read list** (sections
   5 and 8). Only a complete, consistent snapshot gives ACTIVE.

## 1. Evidence labels and pinned sources

| Label | Meaning here |
| --- | --- |
| VERIFIED | Read in a pinned public source or in this repository, or established by a host test. It never means "ran on a Surface Pro". |
| OBSERVED | Recorded by this repository from the running A16. Not re-measured here. |
| INFERRED | Reasoned from other evidence, not checked on the SP11. |
| UNKNOWN | Not established. A named measurement resolves it (section 9). |

Linux sources were read at commit `7475ea8b58616b33c04afb976cf0fbaad9dd63fb` (the pin of
[SURFACE-PRO-11.md](SURFACE-PRO-11.md) section 12). Each file keeps its own licence: the three device-tree files are
BSD-3-Clause, the two bindings are GPL-2.0-only OR BSD-2-Clause, `drm_dp_helper.c` and `panel-edp.c` carry permissive
(MIT-style) notices, and the other C files and headers are GPL-2.0 or GPL-2.0-only (each file's SPDX line). Only facts
(offsets, field positions, names, numbers) are used; no text or code is copied. The first 16 hex digits of each file's
SHA-256 identify the exact bytes read.

| File (under `arch/arm64/boot/dts/qcom/` or the path shown) | SHA-256 prefix |
| --- | --- |
| `hamoa.dtsi` | `d5bb1f7ce37c721d` |
| `x1-microsoft-denali.dtsi` | `afbea2d7b48c1725` |
| `x1e80100-microsoft-denali-oled.dts` | `d936576f711043f1` |
| `drivers/gpu/drm/msm/disp/dpu1/catalog/dpu_9_2_x1e80100.h` | `4676efe547032286` |
| `drivers/gpu/drm/msm/disp/dpu1/dpu_hw_intf.c` | `bfba5fb9089248f0` |
| `drivers/gpu/drm/msm/disp/dpu1/dpu_hw_ctl.c` | `d9c585e20e88d47a` |
| `drivers/gpu/drm/msm/disp/dpu1/dpu_encoder_phys_vid.c` | `c0d1cc205385ade1` |
| `drivers/gpu/drm/msm/disp/dpu1/dpu_kms.c` | `7ca011014b778e1c` |
| `drivers/gpu/drm/msm/msm_mdss.c` | `e6186a1bf87e87b5` |
| `drivers/gpu/drm/msm/dp/dp_reg.h` | `e01b9661ed1a7cc5` |
| `drivers/clk/qcom/dispcc-x1e80100.c` | `d1c30db5bfda8b41` |
| `drivers/clk/qcom/gcc-x1e80100.c` | `f1c3779c7bc273df` |
| `drivers/clk/qcom/clk-branch.h` | `bac7dcf51a0da5b2` |
| `drivers/clk/qcom/gdsc.c` | `a5a73fcc890f658b` |
| `drivers/gpu/drm/panel/panel-samsung-atna33xc20.c` | `2a83fe59e9f0a24e` |
| `drivers/gpu/drm/display/drm_dp_helper.c` | `ca8c3a83ed612c6f` |
| `Documentation/devicetree/bindings/display/panel/samsung,atna33xc20.yaml` | `057a2ef5d6b4d8e5` |
| `Documentation/devicetree/bindings/display/msm/dp-controller.yaml` | `87fe29b4af778bf5` |

Other sources: the Microsoft Learn page "Tech specs - Surface Pro for Business, Copilot+ PC, Snapdragon X Series
processor, 11th Edition" (`learn.microsoft.com/en-us/surface/tech-specs/surface-pro-snapdragon-tech-specs`, page
source commit `63494b316bdd533f33953b1390c02281572350ef` of MicrosoftDocs/Devices-docs-pr, dated 2025-02-26); the UEFI
Specification 2.10 (GOP mode information, EDID protocols); the VESA E-EDID 1.4 base-block layout and the CTA-861
extension layout (public standards, field positions only).

## 2. A16 audit

Which existing display, backlight and timing pieces are A16-specific and which are generic. "Class" is the audit
result; "SP11" says what to do.

| Piece | Location | Class | Why, and what the SP11 does |
| --- | --- | --- | --- |
| DPU map: MDSS `0x0AE00000`, MDP `0x0AE01000`, INTF index 5, first INTF `0x34000`, stride `0x1000`, so INTF page `0x0AE3A000` | `compat/dpu-intf.h:20-26` | A16 values, numerically equal on Hamoa | Sourced from the glymur device tree and `dpu_12_2_glymur.h`. Hamoa gives the same numbers (section 3), but the INTF stride is catalog data (the header itself calls it `MDP_INTF_REV_7xxx_OFF`), so the SP11 takes bases and the index from the descriptor and the catalog, and the check cross-checks every address it reads. |
| INTF register offsets (`TIMING_ENGINE_EN` 0x000 ... `STATUS` 0x26C, `AVR_CONTROL` 0x270) | `compat/dpu-intf.h:29-47` | Generic | The same Linux file defines them for DPU 9.2 and 12.2 (`dpu_hw_intf.c:17-76`). The names carry over; the offsets still arrive as parameters. |
| dispcc offsets: MDP CBCR `0x8010`, AHB CBCR `0x80B0`, MDSS GDSCR `0x9000` | `compat/dpu-intf.h:55-58` | **A16-specific, wrong on Hamoa** | Hamoa: AHB CBCR `0x80a8` and MDP CBCR `0x800c` (`dispcc-x1e80100.c:792-808`, `1332-1348`); GDSCR `0x9000` is equal (`1512-1513`). Neither `0x80b0` nor `0x8010` is a branch clock in the Hamoa driver, so the A16 header would read unrelated registers. |
| eDP controller: DP3 AHB `0x0AF6C000`, AUX `0x0AF6C200`; DPTX3 AUX CBCR `0x80A8` | `compat/edp-backlight.h:30-33` | **A16-specific, wrong on Hamoa** | Hamoa `mdss_dp3` is at `0x0aea0000` (`hamoa.dtsi:5900-5904`). The A16's DPTX3 AUX CBCR offset `0x80A8` is Hamoa's **MDSS AHB** CBCR; Hamoa's DPTX3 AUX CBCR is `0x80a0` (`dispcc-x1e80100.c:1206-1222`). A read through the A16 constants would check the wrong clock. |
| AUX controller register sequence, interrupt bit layout | `compat/edp-backlight.h:35-56`, `compat/edp-backlight.c:23-86` | Generic to Linux's `dp_reg.h` family, but the Hamoa controller revision is UNKNOWN | Not reused: the SP11 never touches the AUX channel (the firmware owns it, section 7). |
| DPCD register numbers (0x00D, 0x700-0x726) | `compat/edp-backlight.h:58-68` | Generic (VESA eDP) | Facts only; unused on the SP11. |
| Backlight policy: 2 percent floor, 5..500 nits, level 716 | `compat/edp-backlight.h:70-94` | **A16-specific** | Derived from the A16 DSDT panel table for panel id `SDC422C` (`edp-backlight.h:10`) and one A16 run. Nothing is known of the SP11 panel's range, bit count or default level. |
| Guest beam passthrough: one read-only stage-2 page, magic `SNAPDPU1` | `compat/display-timing.h:3-12,22`, `probes/displaytiming.py:21` | A16-only feature | The SP11 reuses the firmware framebuffer and passes no MDSS page to the guest. Not needed. |
| `decode()` with default size 2880x1800 and a vtotal comment "the A16: 3840" | `tools/survey-dpu-intf.py:71,82` | A16 defaults, generic method | The model for the pipeline check (section 5). The mode comes from the GOP, never from a default. |
| Console drawing into the GOP framebuffer | `probes/fbconsole.py:10-17,40-66` | Generic | Light-grey text on black with the same value in every channel, so the pixel order never matters (lines 14-16); only the default `--device` (line 203) is machine-specific. The black background and the walking position grid (lines 40-66) are right for OLED. Usable on the SP11 if the GOP is 32 bits per pixel (X5). |
| Codeless boot-framebuffer AuxKC | `probes/bootfb.py:19-23` | J700/macOS-specific | `disp0,t8140` and a pinned kernel hash. It is the macOS side of the boot framebuffer, not part of the firmware pipeline. |
| "2880x1800 at 60 Hz" | `docs/STATUS.md:13`, `docs/ARCHITECTURE.md:83`, `docs/SURFACE-PRO-11.md:188` (a line of the plan revision this audit read; that row is now the Display row of section 4.1) | A16's panel | In `SURFACE-PRO-11.md` section 4.1 this is the A16 column. An earlier revision of the plan named no resolution in the SP11 column; corrected in section 4.1, which now names 2880x1920 there (E33). |

**Why none of the A16 backlight path may be reused on the SP11 unless a control is verified.** The path programs one
eDP controller through allow-listed MMIO at the A16's addresses, reads the panel's DPCD capability bytes over AUX,
and clamps to a floor and a nit model that came from the A16's own panel table. On the SP11 every one of those inputs
is different or unknown (above), the AUX channel belongs to the firmware's controller, and the panel's capability bytes
have never been read. Reusing it would mean writing a panel register on a unit whose panel, range and mode are
unmeasured. The default is therefore to leave the brightness exactly as the firmware set it (section 7).

## 3. Hamoa display facts (pinned public Linux)

All addresses are physical, from `hamoa.dtsi` unless another file is named. Every row is VERIFIED to be what the
pinned file says and INFERRED to hold on the unit.

| Item | Value | Source |
| --- | --- | --- |
| MDSS (`qcom,x1e80100-mdss`) | `0x0ae00000`, size `0x1000`, SPI 83; power domain `dispcc MDSS_GDSC`; `iommus = <&apps_smmu 0x1c00 0x2>` | `hamoa.dtsi:5553-5587` (reg at 5555, iommu at 5578). **The brief's `0x0ae00000` claim holds.** |
| DPU (`qcom,x1e80100-dpu`, "mdp") | `0x0ae01000`, size `0x8f000`; VBIF `0x0aeb0000` size `0x2008`; power domain `RPMHPD_MMCX` | `hamoa.dtsi:5589-5611` |
| DPU version | major 9, minor 2 | `dpu_9_2_x1e80100.h:414-417` |
| INTF_5 (feeds the eDP) | base `0x39000` from the "mdp" region = `0x0ae3a000`; type DP; `controller_id` = DP controller 3; length `0x280` | `dpu_9_2_x1e80100.h:350-356` |
| INTF-to-controller map | INTF_0 to DP0, INTF_4 to DP1, INTF_5 to DP3, INTF_6 to DP2 (plus MST pairs INTF_3, 7, 8) | `hamoa.dtsi:5617-5647` and `dpu_9_2_x1e80100.h:306-381` |
| eDP controller `mdss_dp3` (`qcom,x1e80100-dp`) | regions ahb `0x0aea0000` (0x200), aux `0x0aea0200` (0x200), link `0x0aea0400` (0xc00), p0 `0x0aea1000` (0x400), p1 `0x0aea1400` (0x400); input port from `mdss_intf5_out` | `hamoa.dtsi:5898-5954`; region order from `dp-controller.yaml:74-85` |
| eDP PHY `mdss_dp3_phy` (`qcom,x1e80100-dp-phy`) | `0x0aec5a00` (0x19c), `0x0aec5200`, `0x0aec5600`, `0x0aec5000` | `hamoa.dtsi:6000-6020` |
| Display clock controller | `dispcc` at `0x0af00000`, size `0x20000`; clocks include `GCC_DISP_AHB_CLK`; power domain `RPMHPD_MMCX` | `hamoa.dtsi:6022-6046` |
| dispcc: MDSS AHB CBCR | `0x80a8` (halt and enable register); bit 31 `CBCR_CLK_OFF` | `dispcc-x1e80100.c:792-808`; `clk-branch.h:63` |
| dispcc: MDSS MDP CBCR | `0x800c` | `dispcc-x1e80100.c:1332-1348` |
| dispcc: DPTX3 AUX, LINK, LINK_INTF, PIXEL0 CBCRs | `0x80a0`, `0x8098`, `0x809c`, `0x8094` | `dispcc-x1e80100.c:1206-1275` |
| dispcc: MDSS GDSC | `gdscr` `0x9000`; status is bit 31 `PWR_ON` | `dispcc-x1e80100.c:1512-1522`; `gdsc.c:21,61-95` |
| GCC (`qcom,x1e80100-gcc`) | `0x00100000`, size `0x200000`; `GCC_DISP_AHB_CLK` is the CBCR at `0x27004`, listed among the clocks the driver keeps always on | `hamoa.dtsi:758-760`; `gcc-x1e80100.c:7444-7453` |
| CTL paths | `ctl_0` .. `ctl_5` at `0x15000` .. `0x1a000` from "mdp" (length `0x290`); `CTL_INTF_ACTIVE` is register `0x0F4`, one bit per INTF | `dpu_9_2_x1e80100.h:28-54`; `dpu_hw_ctl.c:35,592-599,614` |
| HW version register | major 31:28, minor 27:16, step 15:0, read at the MDSS base | `msm_mdss.c:241-244,283` |
| Denali: MDSS, `mdss_dp0`, `mdss_dp1` enabled | `status = "okay"` | `x1-microsoft-denali.dtsi:923-941` |
| Denali: the eDP node | `&mdss_dp3` with an `aux-bus` child `panel` (`edp-panel`), `data-lanes = <0 1 2 3>`, `link-frequencies` 1.62, 2.7, 5.4, 8.1 GHz (RBR to HBR3), no `panel-timing`, no `backlight` | `x1-microsoft-denali.dtsi:943-978` |
| Denali OLED panel | `compatible = "samsung,atna30dw01", "samsung,atna33xc20"` | `x1e80100-microsoft-denali-oled.dts:17-19` |
| Panel power and enable | `power-supply = <&vreg_edp_3p3>` (3.3 V fixed, enable GPIO `tlmm 70`); `enable-gpios = <&pmc8380_3_gpios 4>` (pin state `edp_bl_en`) | `x1-microsoft-denali.dtsi:146-160,949-956,1074-1082,1214-1219` |
| Backlight, PWM, WLED, LPG nodes | none in `hamoa.dtsi`, `x1-microsoft-denali.dtsi` or the OLED file | searched for `backlight`, `pwm`, `wled`, `lpg`, `brightness`: no match |
| Panel driver | `panel-samsung-atna33xc20.c` (matches `samsung,atna33xc20`); `panel-edp.c` has no `atna` entry | `panel-samsung-atna33xc20.c:336`; search of `panel-edp.c` |

Notes.

- The pinned device tree gives **no** panel mode: the Linux panel driver reads the EDID over AUX
  (`panel-samsung-atna33xc20.c:223-237`). That is consistent with the plan's "mode from GOP/EDID".
- INTF_5 is also the eDP interface on the A16 (`compat/dpu-intf.h:22`), so the INTF index is the same; the base of the
  INTF pages is the same only because both DPU generations place `intf_0` at `0x34000`.
- The display reaches memory through `apps_smmu` stream `0x1c00` (`hamoa.dtsi:5578`). Whether that stream translates or
  bypasses at the firmware handoff is UNKNOWN, so the study never equates a scanout address with the GOP base
  (section 9).

## 4. The panel size

**The disagreement (history).** When this study was written the repository stated two sizes. The EFI probe's README
(`probes/uefi/README.md`, at that time a patch not yet in the tree) and its tests assumed 2880x1920 for the SP11
(INFERRED there). An earlier revision of `docs/SURFACE-PRO-11.md` (line 188, the Display row of section 4.1) showed
"2880x1800 at 60 Hz", which the probe's report read as the SP11's. It was not: it sat in the **A16** column of the
layer table, and `docs/STATUS.md:13` and `docs/ARCHITECTURE.md:83` state the same figure for the A16's own panel. The
SP11 column of that row named no resolution. That is corrected in section 4.1 of the plan: the SP11 column now names
2880x1920 with its label, and E33 of the plan records the sources listed below.

**Public evidence for the SP11.**

| Source | Statement | Label |
| --- | --- | --- |
| Linux binding `samsung,atna33xc20.yaml:22-23`, the compatible that `x1e80100-microsoft-denali-oled.dts:17-19` selects | The binding's entry for `samsung,atna30dw01` describes a 13-inch, 3K, AMOLED eDP panel of 2880x1920 pixels. The same file lists other Samsung panels at 2880x1800 (`atna40cu11` and others), so the two figures are told apart in the source itself. | VERIFIED (public source, pinned) |
| Microsoft Learn tech specs, Snapdragon X Series, 11th Edition | The OLED model is listed with a 13-inch PixelSense Flow display of 2880 x 1920 pixels (267 PPI), 3:2, with dynamic refresh up to 120 Hz. The page lists the LCD model at 2880 x 1920 too. | VERIFIED (public source) |

**Conclusion.** The Surface Pro 11 OLED panel is a 13-inch 2880x1920 (3:2) panel. 2880x1920 is right for the SP11
and 2880x1800 is the A16's. That is the *product's* size. This unit's GOP mode could still differ (firmware may select
another mode), so the size is **INFERRED** for the unit and `tools/sp11_display_mode.py` assumes neither number: it
reports what the GOP report and the EDID say and refuses a mismatch (section 6). Its tests contain both numbers as
fixtures only, and a test proves the tool's source names neither.

**A note on 120 Hz (INFERRED).** A detailed timing descriptor holds a 16-bit pixel clock in 10 kHz units, at most
655.35 MHz. A 2880x1920 mode at 120 Hz needs about 692 MHz at modest blanking (120 x 2960 x 1950), so a 120 Hz timing
cannot sit in a base-block descriptor. If the panel's EDID carries one, it is in another block type, a CTA-861 or
DisplayID extension, which the `modes` subcommand of `sp11_display_mode.py` decodes and lists (section 6) but the `--gop`
verdict does not use. The preferred base-block timing and the GOP size are what the `--gop` flow compares, and size is
independent of refresh rate.

The 120 Hz question has its own study, [SP11-120HZ-STUDY.md](SP11-120HZ-STUDY.md): even with zero blanking a 2880x1920
120 Hz timing exceeds the 18-byte descriptor, and `tools/sp11_display_mode.py modes` decodes the extension blocks that
can carry it.

**Proposed text for the coordinator** is in the report (section "PROPOSED SHARED-DOC TEXT"); this study edits no
shared document.

## 5. Is the firmware pipeline active? The indicators and the decision logic

### 5.1 Candidate indicators

"Side effects" means a register read that can change hardware state (read-to-clear status) or that can fault if the
block is unclocked. Only reads are ever considered; the check never writes.

| Indicator | Evidence | Side effects and risk | In the verdict |
| --- | --- | --- | --- |
| `GCC_DISP_AHB_CLK` CBCR `CLK_OFF` clear | `gcc-x1e80100.c:7444-7453` keeps it on; CBCR bit: `clk-branch.h:63` | A GCC read. GCC is the root clock controller, always reachable. Gives the first read a verified-safe footing | Yes (gate 1) |
| MDSS GDSC `PWR_ON` | `dispcc-x1e80100.c:1512-1522`, `gdsc.c:21,87-92` | A dispcc read; needs the register clock above | Yes (gate 2) |
| MDSS AHB and MDP CBCR `CLK_OFF` clear | `dispcc-x1e80100.c:792-808,1332-1348` | A dispcc read | Yes (gates 3, 4) |
| DPU version at the MDSS base reads 9.2 | `msm_mdss.c:241-244,283`; `dpu_9_2_x1e80100.h:414-417` | An MDSS read: faults if the MDSS clocks or power are off, which is why the gates come first. Reading it needs the MDP clock (`msm_mdss.c:276-283` comment) | Yes (version evidence) |
| INTF_5 `TIMING_ENGINE_EN` bit 0 and `STATUS` bit 0 agree and are set | `dpu_hw_intf.c:17,75,275-282,326-345` (on DPU major 5 and above, `STATUS` bit 0 is the running state) | Plain status reads | Yes (timing evidence) |
| INTF_5 timing registers describe a coherent frame whose active window equals the GOP mode | `dpu_hw_intf.c:128-257`, `dpu_encoder_phys_vid.c:106-123` | Plain reads | Yes (timing and geometry evidence) |
| A CTL path lists INTF_5 in `CTL_INTF_ACTIVE` | `dpu_hw_ctl.c:35,592-599,614` | Plain reads of six CTL pages inside the MDP region | Yes (routing evidence; the semantics on the firmware's driver are INFERRED) |
| `FRAME_COUNT` advances | `dpu_hw_intf.c:49,339`; `dpu_encoder_phys_vid.c:712-736` | Plain reads. The counters only count if `FRAME_LINE_COUNT_EN` is set, which Linux writes (`dpu_hw_intf.c:257`) and the firmware may not | Optional corroboration; contradicts only if it claims to run and does not |
| eDP controller `MAINLINK_CTRL` bit 0, `STATE_CTRL` bit 7 | `dp_reg.h:123-141` | Reads of the `link` region need the DPTX3 link clocks on; not gated by anything in the list | **No: UNKNOWN**, would need the DPTX3 CBCRs (`0x8098`, `0x809c`) as extra gates first |
| `INTF_MUX` low nibble (pingpong bound) | `dpu_hw_intf.c:308-324`, set by `dpu_encoder_phys_vid.c:322-325` | Plain read | **No: UNKNOWN**, Linux binds a pingpong block; whether the firmware does is unproven |
| eDP PHY lock or status | PHY block `0x0aec5a00`..., a different IP | Possible read-to-clear fields | **No: UNKNOWN**, no side-effect-free status register is established from public source |
| SSPP source address equals the GOP base | `dpu_hw_sspp` | The display goes through `apps_smmu` stream `0x1c00`, so the address may be an IOVA | **No: UNKNOWN** |

Everything marked No is listed with what would settle it in section 9. A smaller verified check beats a larger guessed
one.

### 5.2 The decision (`compat/display-pipeline-check.{h,c}`)

`display_pipeline_check(params, snapshot, length, &result)` returns one of four verdicts and a reason:

| Verdict | Meaning | Allows reuse |
| --- | --- | --- |
| ACTIVE | Every required evidence class was affirmatively established and nothing contradicts it | yes |
| INACTIVE | An affirmative reading says the pipeline is off (power domain, a clock, or the timing engine) | no |
| UNKNOWN | The snapshot cannot decide: a register was unreadable, not read or all-ones; the dispcc register clock was off; evidence is missing; the snapshot or the parameters are malformed or truncated | no |
| INCONSISTENT | Two readings, or a reading and the parameters, contradict each other | no |

Evidence classes: `GATES` (all four gates), `VERSION`, `TIMING`, `GEOMETRY`, `ROUTING`, `COUNTERS`. The caller names the
classes it requires; `GATES`, `TIMING` and `GEOMETRY` can never be waived. Rules, in order, first decision wins:

1. Parameters invalid (ABI, reserved bits, required mask, INTF index, version, mode, a block that is empty or wraps or
   overlaps another, an INTF or CTL page outside the MDP block, a register offset that is unaligned or outside its block,
   a CTL or counter setting that is unusable): UNKNOWN `bad-params`.
2. Snapshot unusable: shorter than its header or than its own length field, wrong magic or ABI, counts out of range,
   fewer entries than the register list, a status value out of range: UNKNOWN `truncated` or `malformed`. A cut at any
   byte offset is `truncated`, so no prefix of a good snapshot can be taken for a whole one.
3. Cross-check: the snapshot's INTF index, its CTL count and the address of every register it attempted must equal what
   the parameters compute (base plus offset). A mismatch is INCONSISTENT `intf-mismatch` or `address-mismatch`.
4. Gates, in reader order: the first that cannot be read gives UNKNOWN (`not-read`, `read-fault`, `all-ones`); the
   dispcc register clock reading off gives UNKNOWN `dispcc-no-access` (the gates are then unreachable, not necessarily
   off); GDSC, AHB or MDP reading off gives INACTIVE.
5. Version: the major and minor must equal the parameters' (step is ignored); otherwise INCONSISTENT.
6. Timing engine: `TIMING_ENGINE_EN` and `STATUS` both clear is INACTIVE `timing-off`; one set and one clear is
   INCONSISTENT `enable-status`; both set but no timing programmed (a zero line length, zero frame period, or every
   window register zero) is INCONSISTENT `no-timing`.
7. Timing coherence: the frame period must be a whole number of lines; the active vertical window must be increasing,
   inside the frame, with blanking; the horizontal window must end inside the line (INCONSISTENT `timing-incoherent`).
8. Geometry: the programmed active width and height must equal the GOP mode. With `DATABUS_WIDEN` set the width must be
   half the mode's (horizontal values count pixel pairs, `dpu_hw_intf.c:98`, `dpu_encoder_phys_vid.c:117-123`); without
   it, the full width. Otherwise INCONSISTENT `geometry-mismatch`.
9. Routing: if every CTL page was read and none lists the INTF, INCONSISTENT `routing-mismatch`; an unread page only
   blocks a caller that requires routing.
10. Counters: if they claim to run and the count did not move over two minimum frame periods, or moved faster than the
    maximum refresh rate allows, INCONSISTENT; otherwise they add evidence. A disabled or briefly sampled counter adds
    nothing and contradicts nothing.
11. Any required class not established: UNKNOWN `missing-evidence`. Otherwise ACTIVE.

The brief's faulty snapshots map as follows (each is a named scenario in the test):

| Faulty snapshot | Verdict |
| --- | --- |
| All-ones read from an unclocked INTF block (bit 0 of `0xffffffff` would look "enabled") | UNKNOWN `all-ones` |
| Enable bit set with no timing programmed | INCONSISTENT `no-timing` |
| Mismatched interface selection (header INTF differs, INTF page read at the wrong address, or no CTL path lists the INTF) | INCONSISTENT `intf-mismatch`, `address-mismatch` or `routing-mismatch` |
| Truncated snapshot, at every byte offset | UNKNOWN `truncated` |

**How it differs from `tools/survey-dpu-intf.py` `decode()`** (on purpose, fail closed): an enabled ACTIVE window is
always used, never silently replaced by the DISPLAY window when its end precedes its start; a window start equal to its
end is refused; `DATABUS_WIDEN` set with a full-width window is a contradiction, not a match; there is no upper bound of
eight times the height on the frame (that bound was an A16 heuristic); and the mode is a parameter, not 2880x1800.

**Parameters, never constants.** Every base and offset, the INTF index, the DPU version and the mode arrive in
`struct dpc_params`. The test run proves it: a second platform with different bases, offsets, INTF index, CTL pages and
version is accepted with its own parameters and refused with Hamoa's, and a test rejects any hexadecimal address
literal in the C source.

**Files and limits.** About 560 lines of C. A freestanding AArch64 object builds with no undefined symbols and no
writable data (`-O2` text is about 5.6 KiB). Nothing links it. A future monitor caller would have to fit the code
budget noted in `compat/edp-backlight.c`.

## 6. Mode validation (`tools/sp11_display_mode.py`)

```sh
python3 tools/sp11_display_mode.py --gop summary.txt --edid panel.edid     # exit 0 ACCEPT, 1 REJECT, 2 unusable
python3 tools/sp11_display_mode.py --gop gop.json --expect-size WxH        # an explicit operator expectation
```

- **GOP report**: a JSON object or the EFI probe's `summary.txt` (`gop_width`, `gop_height`, `gop_stride_pixels`,
  `gop_format`, `gop_base`, `gop_size`). Refused: a missing or unknown field, a boolean or float where an integer is
  required, a dimension of zero or above 16384, a stride below the width, a pixel format other than the two 32-bit
  linear ones (`BITMASK` needs masks the report lacks; `BLT_ONLY` has no framebuffer), a framebuffer smaller than
  stride x 4 x height, a wrapping address range, duplicate keys.
- **EDID**: a whole number of 128-byte blocks; the header pattern; every block's checksum; version 1.3 or 1.4; the
  extension count in byte 126 equal to the blocks given; a manufacturer id of three letters; the four base-block
  descriptors (a timing needs a non-zero pixel clock, a non-empty active area, sync and front porch inside blanking, a
  refresh rate between 20 and 500 Hz; a display descriptor must have its reserved byte clear); CTA-861 extensions with a
  valid timing offset, tiling data blocks and valid timings. Since the 120 Hz tools (`tools/sp11_edid_ext.py`,
  [SP11-120HZ-STUDY.md](SP11-120HZ-STUDY.md) section 3) the CTA-861 video data blocks, the DisplayID 1.x Type I and
  2.x Type VII timings and the 0xFD range-limits descriptor are decoded too. In the `--gop` flow they only add
  information: a malformed one is listed under `edid.partial` and never changes the verdict; only the `modes`
  subcommand refuses it. Other extension types are checksummed and reported as partial, not decoded. The preferred
  timing is the first descriptor if byte 24 bit 1 declares it.
- **Verdict**: ACCEPT only if everything is well formed and, when an EDID is given, it declares a preferred timing that
  is not interlaced and whose active size equals the GOP size. An EDID that cannot confirm the GOP is REJECT, not a
  shrug. With no EDID the verdict is ACCEPT with a warning that the panel has not confirmed the mode.
- VERIFIED by test: two literal public timings (CTA-861 VIC 16 1080p60 and VIC 4 720p60) decode to their published
  parameters; an independent encoder round-trips every bit field; 31 seeded bugs are caught.

## 7. Backlight

**Analysis of the public sources.**

- The pinned device tree has no backlight, PWM, WLED, LPG or brightness node for this board (section 3).
- The only brightness mechanism in the Linux chain for this panel is the panel driver's `drm_panel_dp_aux_backlight()`
  (`panel-samsung-atna33xc20.c:308`), which drives the VESA eDP DPCD backlight registers over AUX
  (`drm_dp_helper.c:4660-4730`). It reads the panel's own capability bytes first (DPCD 0x700 onward) and registers a
  backlight only if they advertise AUX, PWM or luminance control (`drm_dp_helper.c:4573-4600,4693-4701`).
- **VERIFIED** therefore: *if* the SP11 panel advertises DPCD brightness, Linux would control it that way. **UNKNOWN**:
  whether this unit's panel does, its bit count, range and current mode, because nothing public records the panel's
  DPCD bytes.
- The panel needs care. The Linux driver's own comments record two hazards: once the enable line has dropped, the panel
  must be power-cycled before the next enable or its backlight does not come back (`panel-samsung-atna33xc20.c:169-176`),
  and its timing controller can crash if the data stream stops (`195-201`). Both are the driver authors' observations on
  other units, not measurements here. The enable is `enable-gpios` on a PMIC GPIO and the supply is a 3.3 V regulator
  (section 3); both belong to the firmware.

**Conclusion.** No control is verified for this unit, so the backlight stays as the firmware set it. The X2 test reads no
panel register and no AUX transaction is made. If a later gate finds the panel's DPCD capability bytes (a study at X5,
with its own authorization and an AUX path that does not fight the firmware's controller), the verified mechanism is the
DPCD one above; even then this work does not use it.

## 8. The X2 dump list for display

<!-- BEGIN X2-DISPLAY-LIST (the uefi-func-tests agent may lift this section verbatim) -->

**Scope.** Read-only. No write to any MDSS, dispcc, GCC, DP controller, PHY, SMMU, clock, power, GPIO, PMIC or panel
register. No AUX transaction, no DPCD access, no interrupt-status register (they can clear on read), no mapping or
unmapping. The display must not change in any visible way during these reads. The reads are issued from EL2 under the
test image's exception vector table, at the point where the test image does its other read-only MMIO reads (the plan
reads firmware-left state after ExitBootServices), and each one is recorded as a value, or as a fault with its ESR and
the faulting address. A page the firmware did not map faults: record it and carry on; never map it.

**S0, before any MMIO (from the probe's existing facts).** Record the GOP mode (width, height, pixels per scan line,
pixel format, base, size) exactly as the probe's `summary.txt` has it. If the firmware publishes
`EFI_EDID_ACTIVE_PROTOCOL` (or `EFI_EDID_DISCOVERED_PROTOCOL`) on the GOP handle, write its `Edid` bytes to the stick
as `edid.bin`; if not, say so (INFERRED that it exists). Never read EDID over AUX.

**S1 to S4, the gates, in this order, stopping at the first one that is off or unreadable.** The blocks and offsets
below are Hamoa values taken from the pinned device tree (INFERRED to hold on the unit); the test must obtain them
from the board descriptor or the parameters table, not from literals (the descriptor currently carries only the MDSS
top block, so the others are an open question for the coordinator).

**S5, the version. S6, the INTF registers. S7, the CTL registers. S8, the counter sample.**

Registers (`compat/display-pipeline-check.h` `enum dpc_reg`, in reader order; every register is 32 bits wide):

| Order | Id | Block | Block base | Offset | Bits used |
| --- | --- | --- | --- | --- | --- |
| 1 | GCC_DISP_AHB | gcc | 0x00100000 | 0x27004 | 31 CLK_OFF (clear = running) |
| 2 | GDSC | dispcc | 0x0af00000 | 0x9000 | 31 PWR_ON (set = powered) |
| 3 | AHB_CBCR | dispcc | 0x0af00000 | 0x80a8 | 31 CLK_OFF |
| 4 | MDP_CBCR | dispcc | 0x0af00000 | 0x800c | 31 CLK_OFF |
| 5 | HW_VERSION | mdss | 0x0ae00000 | 0x0 | 31:28 major, 27:16 minor |
| 6 | INTF_EN | intf | 0x0ae3a000 | 0x000 | 0 |
| 7 | INTF_STATUS | intf | 0x0ae3a000 | 0x26c | 0 |
| 8 | INTF_CONFIG | intf | 0x0ae3a000 | 0x004 | 29 ACTIVE_H_EN, 30 ACTIVE_V_EN |
| 9 | INTF_HSYNC_CTL | intf | 0x0ae3a000 | 0x008 | 31:16 line length |
| 10 | INTF_VSYNC_PERIOD | intf | 0x0ae3a000 | 0x00c | all |
| 11 | INTF_DISP_V_START | intf | 0x0ae3a000 | 0x01c | all |
| 12 | INTF_DISP_V_END | intf | 0x0ae3a000 | 0x024 | all |
| 13 | INTF_ACT_V_START | intf | 0x0ae3a000 | 0x02c | all |
| 14 | INTF_ACT_V_END | intf | 0x0ae3a000 | 0x034 | all |
| 15 | INTF_DISP_HCTL | intf | 0x0ae3a000 | 0x03c | 31:16 end, 15:0 start |
| 16 | INTF_ACT_HCTL | intf | 0x0ae3a000 | 0x040 | 31:16 end, 15:0 start |
| 17 | INTF_CONFIG2 | intf | 0x0ae3a000 | 0x060 | 0 DATABUS_WIDEN |
| 18 | INTF_FLC_EN | intf | 0x0ae3a000 | 0x0a8 | nonzero = counters claimed |
| 19 | INTF_FRAME_A | intf | 0x0ae3a000 | 0x0ac | first FRAME_COUNT sample |
| 20 | INTF_FRAME_B | intf | 0x0ae3a000 | 0x0ac | second sample, 100 ms or more later |

Then, for each of the six CTL pages, `CTL_INTF_ACTIVE` (bit 5 set means INTF_5 is driven by that path):

| CTL page | Base | Offset |
| --- | --- | --- |
| 0 | 0x0ae16000 | 0xf4 |
| 1 | 0x0ae17000 | 0xf4 |
| 2 | 0x0ae18000 | 0xf4 |
| 3 | 0x0ae19000 | 0xf4 |
| 4 | 0x0ae1a000 | 0xf4 |
| 5 | 0x0ae1b000 | 0xf4 |

Parameters for `struct dpc_params` (Hamoa, INFERRED):

| Parameter | Value | Source |
| --- | --- | --- |
| intf_index | 5 | catalog `controller_id` and the device-tree port: `dpu_9_2_x1e80100.h:350-353`, `hamoa.dtsi:5633-5639` |
| dpu_major | 9 | `dpu_9_2_x1e80100.h:414-417` |
| dpu_minor | 2 | `dpu_9_2_x1e80100.h:414-417` |
| gcc.base | 0x00100000 | `hamoa.dtsi:760` |
| gcc.length | 0x200000 | `hamoa.dtsi:760` |
| dispcc.base | 0x0af00000 | `hamoa.dtsi:6024` |
| dispcc.length | 0x20000 | `hamoa.dtsi:6024` |
| mdss.base | 0x0ae00000 | `hamoa.dtsi:5555` |
| mdss.length | 0x1000 | `hamoa.dtsi:5555` |
| mdp.base | 0x0ae01000 | `hamoa.dtsi:5591` |
| mdp.length | 0x8f000 | `hamoa.dtsi:5591` |
| intf.base | 0x0ae3a000 | mdp base plus `intf_5` base 0x39000 |
| intf.length | 0x280 | `dpu_9_2_x1e80100.h:351` |
| ctl_count | 6 | `dpu_9_2_x1e80100.h:28-54` |
| ctl_length | 0x290 | `dpu_9_2_x1e80100.h:31` |
| ctl_intf_active_off | 0xf4 | `dpu_hw_ctl.c:35` |
| required | 0x1f | GATES, VERSION, TIMING, GEOMETRY, ROUTING; COUNTERS is optional |
| refresh_hz_min | 24 | plausibility floor chosen here, not a hardware fact |
| refresh_hz_max | 240 | plausibility ceiling chosen here (the panel is specified up to 120 Hz) |
| counter_gap_min_us | 100000 | two frame periods at the floor, so a stalled counter can be judged |
| gop_width, gop_height | from S0 (`tools/sp11_display_mode.py`) | never a constant |

**Reader rules.** Read in the order above. After S1, a clear `CLK_OFF` is required to continue; a set one (or any
fault) ends the reads and the verdict is UNKNOWN. After S2 to S4, a gate that reads off ends the reads: leave the
remaining slots NOT_TRIED. Record every register's physical address with its value (the checker cross-checks them).
For S8 read INTF_FLC_EN first; only if it is non-zero read FRAME_COUNT, wait at least `counter_gap_min_us` using the
generic counter, read FRAME_COUNT again and record the elapsed microseconds. Feed the snapshot to
`display_pipeline_check` (`dpc_snapshot_init` and `dpc_snapshot_put` build it) and print the verdict, the reason,
the detail and the evidence mask on the page.

**What each verdict means for X5.** ACTIVE: the firmware pipeline is running the GOP mode through INTF_5; the console
may draw into the GOP framebuffer. INACTIVE: the display is off; stop and report which gate. UNKNOWN: the reads could
not decide; keep the raw values and do not draw. INCONSISTENT: record the raw values; the unit does not behave as the
pinned sources describe, which is a result, not a failure of the test.

**What not to add.** The DP controller `link` registers, the PHY, `INTF_MUX`, the SSPP and mixer blocks, SMMU state and
any interrupt-status register stay off the list until section 9 says what makes them safe.

<!-- END X2-DISPLAY-LIST -->

## 9. What stays UNKNOWN and what would settle it

| Question | Why it is open | Evidence that resolves it |
| --- | --- | --- |
| Every register value above | Nothing has read an SP11 | The X2 run |
| Whether the firmware maps the MDSS, dispcc and GCC pages at EL2 after ExitBootServices | The probe installs no vectors; a missing mapping faults | The X2 faults, recorded with ESR and address |
| Whether the version register reads 9.2 at `0x0ae00000` | The catalog says 9.2; the register read is unproven | The raw HW_VERSION value |
| Whether the Windows display driver sets `CTL_INTF_ACTIVE` as Linux does | Same IP, different driver | The six CTL reads; routing is evidence only if one lists INTF_5 |
| Whether the firmware enables the frame counters | Linux writes `FRAME_LINE_COUNT_EN`; the firmware may not | `INTF_FLC_EN` raw value |
| Whether `apps_smmu` stream `0x1c00` translates the scanout | Only the device tree is read | A later SMMU read-only survey (X2 item 6 covers the PCIe SMMU, not this stream) |
| DP controller link state (`MAINLINK_CTRL`, `STATE_CTRL`) and PHY lock | Needs extra clock gates first and PHY status semantics | Public status-register semantics plus the DPTX3 link CBCRs as gates |
| `INTF_MUX`, SSPP source address, mixer state | Firmware's use unknown | A read-only X5 study |
| The panel's DPCD brightness capability and EDID | AUX belongs to the firmware | The EDID protocol dump at S0; DPCD only at a later, authorized gate |
| Which block holds a 120 Hz timing | A base-block descriptor cannot (section 4) | The dumped EDID, read with `sp11_display_mode.py modes` ([SP11-120HZ-STUDY.md](SP11-120HZ-STUDY.md) section 3) |
| Whether the firmware's mode is 2880x1920 | Product spec is public, the unit's GOP is not | S0 and `tools/sp11_display_mode.py` |
| Burn-in on a static OLED page | The probe's pages dwell 12 s each | Nothing in this study changes it; `probes/fbconsole.py` has the anti-burn-in grid |

## 10. How to run the checks

```sh
python3 tools/test-display-pipeline-check.py          # about 3 s: scenarios, properties, writer, doc cross-check
python3 tools/test-display-pipeline-check-mutants.py  # about 40 s: 41 seeded bugs, each must be caught
python3 tools/test-sp11-display-mode.py               # about 20 s including its 31-bug mutation check
python3 tools/test-sp11-display-study.py              # under 1 s: the A16 citations and the study's structure
SP11_LINUX_TREE=<checkout> python3 tools/test-sp11-display-study.py   # also the Linux citations and SHA-256 pins
python3 tools/test-sp11-display-120hz.py              # a few s: the 120 Hz tools (EDID extension decoding, link budget, subcommands)
python3 tools/test-sp11-display-120hz-mutants.py      # about 2 min: its seeded bugs, each must be caught
python3 tools/test-sp11-120hz-study.py                # under 1 s: the 120 Hz study's citations, tables and structure
python3 tools/sp11_display_mode.py --gop FILE [--edid FILE]
```

The tests need only Python 3 and, for the native harness, clang with AddressSanitizer. Without clang the native
classes report SKIP, never PASS; without `SP11_LINUX_TREE` the Linux-citation tests are skipped (the 120 Hz study test
also skips its edid-decode pins without `SP11_EDID_DECODE_TREE`). They write only under `build/` and open no device.
