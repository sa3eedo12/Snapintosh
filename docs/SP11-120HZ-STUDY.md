# SP11 120 Hz study (stretch goal, gate X13): what is known, what the tools can say, what hardware must prove

**Study, host-verified tools and proposed gate text only. UNIMPLEMENTED on hardware. Hardware status: NOT RUN.**
Nothing here has run on a Surface Pro 11. Every statement about the SP11 is VERIFIED against a pinned public source (it
says what that source says, not what the unit does), OBSERVED (a public statement about a unit, not re-measured here)
or INFERRED, and the last two stay that way until a probe has read a real unit. The A16 build and its behaviour are
untouched: nothing in this study is linked by any build, and no `compat/` or `probes/` file changed.

120 Hz is a **stretch goal**: outside the finish line (a desktop with the GPU, [SURFACE-PRO-11.md](SURFACE-PRO-11.md)),
worked on after it, and only if the firmware-trained display link turns out to have the bandwidth. The 60 Hz firmware
pipeline of [SP11-DISPLAY-STUDY.md](SP11-DISPLAY-STUDY.md) stays the default and the fallback.

What the work package delivers:

| Piece | Where | What it is |
| --- | --- | --- |
| This study | `docs/SP11-120HZ-STUDY.md` | The public record, the budget, what a refresh change touches, the guest side, safety, gate X13, kill criterion K7, the dump the gate needs |
| EDID mode lister | `tools/sp11_display_mode.py modes`, `tools/sp11_edid_ext.py`, `tools/sp11_cta_vic.py` | Every timing an EDID advertises (base block, CTA-861, DisplayID 1.x and 2.x), its refresh rate, and which exceed what an 18-byte descriptor holds |
| Link-budget model | `tools/sp11_display_mode.py link-budget`, `tools/sp11_link_budget.py` | Does a timing fit a DisplayPort or eDP link at a lane count, rate and depth, with or without a DSC target. A labelled model, not a measurement |
| Their tests | `tools/test-sp11-display-120hz.py`, `tools/test-sp11-display-120hz-mutants.py` | Hand-built EDID blocks and faulty inputs; a mutation check in which every seeded bug must be caught |
| Study checks | `tools/test-sp11-120hz-study.py` | The in-tree citations, the structure and tables, and (with a Linux checkout) the Linux citations and SHA-256 pins |

The conclusions, each with its label (the sections give the evidence):

1. **The public record says "up to 120 Hz" and nothing else** (OBSERVED, section 2). No public EDID or DPCD of the SP11
   OLED was found (0 units). Two fixed timings or a variable range is UNKNOWN; the dump that settles it is the EDID and
   the DPCD of this unit (section 9).
2. **No 2880x1920 timing at 120 Hz fits an 18-byte descriptor, even with zero blanking** (VERIFIED by arithmetic and a
   host test, section 3): the active pixels alone need 663.552 MHz and the descriptor stops at 655.35 MHz. If the panel
   advertises 120 Hz it does so in an extension block or as a range, and `modes` decodes those.
3. **Only a 4-lane HBR3 link carries 2880x1920 at 120 Hz uncompressed with a margin** (MODEL, section 4): 17.8 Gbit/s
   at 8 bits per component against 25.9 Gbit/s. 4-lane HBR2 fits only with unusually tight blanking and no margin to
   trust; two lanes never fit without DSC. Which link the firmware trained is UNKNOWN and decides everything.
4. **A refresh change touches four things, and Linux never does it live** (VERIFIED for the pinned driver, INFERRED
   for the firmware, section 5): the DPU interface timing registers, the DP main-stream attributes and MVID/NVID, the
   pixel-clock divider in dispcc, and possibly the DPU core clock. The pinned Linux DP driver tears the link down and
   re-trains it at every modeset. Whether a rewrite without retraining works on this unit is UNKNOWN until read.
5. **The guest side is the A16's open roadmap item, so it must be one shared implementation** (section 6). The kext and
   plug-in sources are not in this tree (empty submodules), so their internals are UNKNOWN here.
6. **Only an EDID-advertised timing may be programmed; every switch reverts** (section 7).
7. **Gate X13 has four sub-gates and K7 says when to stop** (section 8).
8. **The X2 display list is not enough for X13a** (section 9): it lacks the DPU polarity and pulse registers, the
   dispcc DP3 divider and gate registers, the DP controller link block, the PHY link-rate registers (now pinned), all
   EDID blocks as a requirement, and a search of the DSDT the probe writes for the panel's override.

<a id="s1-evidence-labels-and-pinned-sources"></a>
## 1. Evidence labels and pinned sources

| Label | Meaning here |
| --- | --- |
| VERIFIED | Read in a pinned public source or in this repository, or established by a host test or by arithmetic shown here. It never means "ran on a Surface Pro". |
| OBSERVED | A public statement or recording about a unit or a vendor figure, read here and not re-measured. Count the units. |
| MODEL | The output of `tools/sp11_link_budget.py` under the assumptions printed with it. Not a measurement. |
| INFERRED | Reasoned from other evidence, not checked on the SP11. |
| UNKNOWN | Not established. A named measurement at a named gate resolves it (section 10). |

Linux sources were read at commit `7475ea8b58616b33c04afb976cf0fbaad9dd63fb` (the pin of the plan's pinned-sources
section in [SURFACE-PRO-11.md](SURFACE-PRO-11.md), and the pin of the display study). The first 16 hex digits of each
file's SHA-256 identify the exact bytes read. Each file keeps its own licence: GPL-2.0 or GPL-2.0-only for the MSM,
clock and PHY files, BSD-3-Clause for the device-tree files, permissive (MIT-style) notices for the DRM core and i915
files (`drm_edid.c`, `drm_modes.c`, `drm_displayid*`, `drm_dp.h`, `drm_dp_helper.c`, `intel_dp.c`); see each file's
header. Only facts are used (offsets, field widths, constants, names, control flow); no text or code is copied.

| File (under the Linux tree) | SHA-256 prefix |
| --- | --- |
| `arch/arm64/boot/dts/qcom/hamoa.dtsi` | `d5bb1f7ce37c721d` |
| `arch/arm64/boot/dts/qcom/x1-microsoft-denali.dtsi` | `afbea2d7b48c1725` |
| `arch/arm64/boot/dts/qcom/x1e80100-microsoft-denali-oled.dts` | `d936576f711043f1` |
| `Documentation/devicetree/bindings/display/msm/dp-controller.yaml` | `87fe29b4af778bf5` |
| `Documentation/devicetree/bindings/display/panel/samsung,atna33xc20.yaml` | `057a2ef5d6b4d8e5` |
| `Documentation/devicetree/bindings/phy/qcom,edp-phy.yaml` | `41b2dc6101e63171` |
| `drivers/clk/qcom/clk-rcg2.c` | `b173e0f4d0b962f7` |
| `drivers/clk/qcom/dispcc-x1e80100.c` | `d1c30db5bfda8b41` |
| `drivers/gpu/drm/display/drm_dp_helper.c` | `ca8c3a83ed612c6f` |
| `drivers/gpu/drm/drm_displayid.c` | `0d9f59ede6769080` |
| `drivers/gpu/drm/drm_displayid_internal.h` | `928daa7f153a7787` |
| `drivers/gpu/drm/drm_edid.c` | `85dbe1d5f088949a` |
| `drivers/gpu/drm/drm_modes.c` | `8dd1fc47c14b15ca` |
| `drivers/gpu/drm/i915/display/intel_dp.c` | `50ed792e4ffe5691` |
| `drivers/gpu/drm/msm/disp/dpu1/catalog/dpu_9_2_x1e80100.h` | `4676efe547032286` |
| `drivers/gpu/drm/msm/disp/dpu1/dpu_core_perf.c` | `def0a013e376ad87` |
| `drivers/gpu/drm/msm/disp/dpu1/dpu_hw_catalog.c` | `0c1f8671013722fe` |
| `drivers/gpu/drm/msm/disp/dpu1/dpu_hw_intf.c` | `bfba5fb9089248f0` |
| `drivers/gpu/drm/msm/dp/dp_ctrl.c` | `f12464883204c372` |
| `drivers/gpu/drm/msm/dp/dp_display.c` | `cc1e282302ad0ff7` |
| `drivers/gpu/drm/msm/dp/dp_display.h` | `bae4851587f87f72` |
| `drivers/gpu/drm/msm/dp/dp_panel.c` | `54dad3796f0ccf7a` |
| `drivers/gpu/drm/msm/dp/dp_reg.h` | `e01b9661ed1a7cc5` |
| `drivers/gpu/drm/panel/panel-samsung-atna33xc20.c` | `2a83fe59e9f0a24e` |
| `drivers/phy/qualcomm/phy-qcom-edp.c` | `761aef8c335ae63e` |
| `drivers/phy/qualcomm/phy-qcom-qmp-dp-phy.h` | `727ff7bc45b642ea` |
| `drivers/phy/qualcomm/phy-qcom-qmp-qserdes-com-v6.h` | `b7b3e2cdfb767f36` |
| `include/drm/display/drm_dp.h` | `c16caea8a4bd02c2` |

Other public sources, with what each is pinned by:

| Source | Pin | Licence | Used for |
| --- | --- | --- | --- |
| `edid-decode` in v4l-utils, `utils/edid-decode/{edid-decode.cpp,edid-decode.h,parse-base-block.cpp,parse-cta-block.cpp,parse-displayid-block.cpp}` | commit `95ad25f6a77a0a6650f5f657ac2c5046efcd04a0` (SHA-256 prefixes `68cbd25c83b486eb`, `f910f4012249452e`, `59de42561868e97a`, `db44d2730481a616`, `ffd93405cda81af2`) | MIT | Cross-check of the DisplayID and CTA-861 field layouts and of the video-code table; a fact source only, no code reused |
| Microsoft Learn, [Tech specs - Surface Pro, Snapdragon X Series, 11th Edition](https://learn.microsoft.com/en-us/surface/tech-specs/surface-pro-snapdragon-tech-specs) | MicrosoftDocs/Devices-docs-pr commit `63494b316bdd533f33953b1390c02281572350ef` | vendor page, no text copied | The panel size and the published refresh figure |
| Microsoft DirectX Developer Blog, "Dynamic refresh rate" (2021) | not pinned (a web page); read for this study | vendor page, no text copied | What Windows needs from a display for its dynamic refresh mode |
| Linux RFC patch series, v2 patch 5 of 8, "drm/msm/dp: Add dpcd link_rate quirk for Surface Pro 11 OLED" (2025-12-01, Message-ID `20251201011457.17422-6-daleyo@gmail.com`, archived by LWN) | not in the pinned tree: checked absent (section 2) | GPL-2.0 patch text, facts only | A third-party report of this panel's DPCD maximum link rate |
| `ooaklee/linux-surface-pro-11-oe`, `README.md` | commit `71524af0602cc35663cd566eb6c7e151aedea995` | not stated, facts only | One community statement of a unit's panel name and size |
| `linuxhw/EDID`, `Digital/Samsung/SDC4169/D157E71133CE` | not pinned (default branch when read) | public data | A public EDID of a different Samsung OLED panel, for shape only |

<a id="s2-the-public-record"></a>
## 2. What the public record says about the panel's refresh modes

| Id | Claim | Label | Source |
| --- | --- | --- | --- |
| H1 | The Surface Pro 11 OLED is 13-inch, 2880 x 1920 (267 PPI), 3:2, with "dynamic refresh rate: up to 120Hz". Two published figures; the wording does not say fixed or variable. | OBSERVED (vendor statement) | Microsoft tech specs, commit `63494b31` |
| H2 | Windows' dynamic refresh mode needs a display that supports variable refresh and reaches at least 120 Hz, plus a driver at WDDM 3.0 or later; it moves between a low and a high rate without a visible mode switch. | OBSERVED (vendor statement, page not pinned) | DirectX Developer Blog, "Dynamic refresh rate" |
| H3 | Taken together, H1 and H2 make "the panel can vary its refresh rate" likely, but they do not say how the panel's EDID or DPCD describe it. | INFERRED | H1, H2 |
| H4 | The pinned device tree puts one eDP panel on `mdss_dp3`, 4 data lanes, `link-frequencies` 1.62, 2.7, 5.4 and 8.1 GHz. It names `samsung,atna30dw01` with the fallback `samsung,atna33xc20`, and gives no panel timing or refresh. | VERIFIED (negative, as read) | `x1-microsoft-denali.dtsi:943-978`, `x1e80100-microsoft-denali-oled.dts:17-19`, binding `samsung,atna33xc20.yaml:22-23` |
| H5 | The pinned panel driver has no mode table: it reads the panel's EDID over AUX and adds the modes the EDID lists. Any 120 Hz mode Linux offers therefore comes from the EDID. | VERIFIED | `panel-samsung-atna33xc20.c:223-237` |
| H6 | No public EDID, `edid-decode` output or DPCD dump of the SP11 OLED was found. Units with a public dump: 0. | OBSERVED (a search result, not a proof of absence) | searches of the sources in section 1 |
| H7 | A public EDID of a different Samsung panel (manufacturer SDC, product 16745, 29 x 17 cm, made 2020) has the same name string `ATNA33XC21-0` and is 1920 x 1080 at 59.997 Hz with a CTA block that lists only colorimetry and HDR metadata. It is not the SP11 panel. It shows that such a panel's EDID can be one base block plus one CTA block with no video data block, and that a name string is not a panel identity. | OBSERVED (one unit, not an SP11) | `linuxhw/EDID` sample |
| H8 | One community document lists an X1E/OLED unit's display as Samsung `ATNA33XC21-0`, 2880 x 1920, with no EDID and no refresh information. The Linux patch of H9 names the same product's panel `ATNA30DW01-1`. The two public names disagree. | OBSERVED (one unit, third party) | `sp11-oe` README, commit `71524af0` |
| H9 | A Linux RFC patch reports that the SP11 OLED panel reads a DPCD maximum link rate of 0, so the panel fails to probe, and forces 8.1 Gbit/s (HBR3), citing the value 0x1E in an `EDPOverrideDPCDCaps` block of the ACPI DSDT. The quirk is keyed on the sink OUI BA:41:59. | OBSERVED (one third-party unit) | Linux RFC patch v2 5/8 |
| H10 | The patch of H9 is not in the pinned tree: the quirk constant for 8.1 Gbit/s does not occur in `drm_dp_helper.c` or `dp_panel.c` at the pin. | VERIFIED (negative) | the two pinned files |
| H11 | So the DPCD maximum link rate cannot be trusted on this panel, and the 4 x HBR3 figure comes from the ACPI override and a third party, not from a DPCD read. | INFERRED | H9, H10 |
| H12 | Whether the 60 and 120 Hz behaviour is two fixed timings or a variable range (eDP adaptive sync or refresh-rate switching) | UNKNOWN | Settled by the EDID blocks (the display range limits descriptor, DisplayID range and dynamic-timing blocks), the DPCD capability bytes and the ACPI override of this unit: section 9 |

How the dump reads: two fixed timings are two detailed timings of the native size in an extension block (never a
base-block descriptor, section 3); a variable range is a range-limits descriptor (`0xFD`) or a DisplayID range or
dynamic-timing block whose upper limit is 120 Hz or more. `modes` prints both lists (`modes`, `range_limits`).

<a id="s3-what-the-tool-can-decode"></a>
## 3. What the tool can decode (`tools/sp11_display_mode.py modes`)

The base-block descriptor limit is arithmetic, not a guess: the pixel clock field is 16 bits in units of 10 kHz, so it
tops out at 655.35 MHz. The active pixels of 2880 x 1920 at 120 Hz are 2880 x 1920 x 120 = 663,552,000 per second, so
no timing of that size and rate fits an 18-byte descriptor even with no blanking at all (VERIFIED; host test
`test_no_native_120_hz_timing_fits_an_18_byte_descriptor`). Modest blanking (80 pixels, 30 lines) makes it 692.64 MHz.

| Source of timings | Decoded by | What it fails closed on |
| --- | --- | --- |
| Base block detailed descriptors | the existing code | as before; the first descriptor is marked preferred only when the EDID says so |
| Base block display range limits descriptor (`0xFD`) | `edid_ext.parse_base_range_limits` | a maximum below a minimum, a zero minimum, offset flags in EDID older than 1.4, reserved flag values, an inconsistent horizontal range |
| Established and standard timings | not decoded | reported: `partial` says why, and `modes_complete` is false |
| CTA-861 extension | `edid_ext.parse_cta_extension` | non-zero padding after the descriptors, more native timings than descriptors, a reserved or zero video code, a data block that overruns; video data blocks only from revision 3; an unknown video code is reported and makes the list partial |
| CTA-861 video timing block (Type VII) | the same | a block of the wrong length or revision, a reserved aspect code; Type VIII and Type X blocks are reported, not decoded |
| DisplayID 1.x extension | `edid_ext.parse_displayid_extension` | checksum, a section that overruns the block, bytes after the section, blocks that do not tile it, an empty block, a length that is not a whole number of 20-byte timings, reserved revision bits, a stereo code that is reserved, sync that does not fit the blanking, a refresh outside 20 to 1000 Hz |
| DisplayID 2.x extension | the same | the same, and a 1.x block tag in a 2.x section (or the reverse); Type VII uses 1 kHz units, Type I uses 10 kHz; the dynamic-range block's seamless bit and its two high bits of the maximum rate are decoded |
| Unrecognised DisplayID version or extension tag | not decoded | reported as partial, never as an error and never as an empty list |

Every mode carries its source, its refresh rate computed from the timing (a rational number, never a rounded input),
whether it is interlaced (an interlaced mode's rate is the field rate and is never returned for a progressive
request), the video code if it came from one, and `exceeds_base_block_limit`. `--require WxH@HZ` exits 0 only if the
EDID decodes and advertises that exact size at that rate within `--tolerance`; when the decode was partial it says
UNKNOWN, not "absent", because a block it did not read might carry the mode. The tool decides nothing from the panel
size. The video-code table (154 rows, codes 1 to 127 and 193 to 219) is a transcription from the Linux
`drm_edid.c` tables (lines 815 and 1483), checked equal to them by `test_every_row_equals_the_pinned_linux_table`
(runs with `SP11_LINUX_TREE`) and cross-read against `edid-decode`; the two sources differ only in how pixel-repeated
codes are written and in one interlaced vertical total (code 39), none of them a 120 Hz mode (VERIFIED). The DisplayID
field layouts (the block header, the 20-byte timing, the units of 10 kHz for Type I and 1 kHz for Type VII, the section
length and checksum rule) were read at `drm_displayid_internal.h:40-71,93-130`, `drm_displayid.c:56-72` and
`drm_edid.c:7054-7100`. The reduced-blanking vectors of the budget model are the VESA timings listed at
`drm_edid.c:532,580,628,664`.

**Two flows, two strictness levels.** `modes` is strict: it exits 1 on anything malformed or undecoded and never
concludes absence from a partial decode. The original `--gop` confirmation is not made stricter by any of this: it rests
on the base block's preferred timing and the GOP size, as approved and tested before. In that flow a refusal from the
new extension decoders (reasons `edid-displayid`, `edid-range-limits`, `edid-cta`) is listed under `edid.partial` in the
JSON as `code: message` (the operator has to read it), with `modes_complete` false, and never turns ACCEPT into REJECT; a base-block fault and the CTA
structure checks that existed before still REJECT as before (tests `test_the_gop_flow_reports_a_malformed_extension_and_still_accepts`,
`test_a_base_block_fault_or_a_cta_structure_fault_still_rejects_the_gop_flow`). Limits of the tool: checked against
hand-built blocks and public layouts, never a real SP11 EDID; Type VIII, IX and X timings, the video format preference
block and the established and standard timings are reported as partial, not decoded.

<a id="s4-the-link-budget"></a>
## 4. The link budget (`tools/sp11_display_mode.py link-budget`)

The question: does a stream fit the main link? The model's assumptions are printed with every answer.

| Assumption | Value | Label |
| --- | --- | --- |
| Line rates per lane | RBR 1.62, HBR 2.7, HBR2 5.4, HBR3 8.1 Gbit/s; the eDP 1.4 intermediate rates 2.16, 2.43, 3.24, 4.32 and 6.48 Gbit/s are accepted by name only, from the i915 rate arrays (`intel_dp.c:555-563`); UHBR rates are refused | VERIFIED (constants), INFERRED (that the panel offers the intermediate rates) |
| Payload | 8 of every 10 bits (8b/10b): capacity = lanes x rate x 0.8. Linux's own mode check computes the same product with 8 payload bits per symbol (`dp_display.c:792-793`) | VERIFIED |
| Pixel format | RGB 4:4:4, 3 x bits per component (6, 8 or 10); no 4:2:0 or 4:2:2 | MODEL |
| Blanking | explicit (the caller says) or CVT reduced blanking v1, a labelled assumption: 160 pixels horizontal, at least 460 microseconds vertical, 3-line front porch, minimum 6-line back porch, 250 kHz clock step (constants at `drm_modes.c:635-637,772-778,806`) | MODEL |
| Overheads | none: no secondary data packets, audio, MSA or transfer-unit rounding. "Fits" is a necessary condition. Treat a margin under about 5 % as "does not fit" until hardware says otherwise | MODEL; the 5 % is an engineering choice, INFERRED |
| DSC | only as an explicit target in bits per pixel, a multiple of 1/16 between 6 and 3 x bpc; never assumed | MODEL |

Results for 2880 x 1920 (margin = (capacity - required) / capacity; yes means it fits). The table is read by
`test_the_study_table_is_what_the_model_computes`, so a number that drifts from the model fails the suite.

<!-- BEGIN LINK-BUDGET-TABLE (MODEL; rows are checked against tools/sp11_link_budget.py) -->
| Hz | Blanking | Pixel clock kHz | Htotal | Vtotal | Rate | Lanes | Depth | Margin % | Fits |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 60 | cvt-rb-v1 | 360000 | 3040 | 1975 | HBR3 | 4 | 8 bpc | 66.7 | yes |
| 60 | cvt-rb-v1 | 360000 | 3040 | 1975 | HBR3 | 4 | 10 bpc | 58.3 | yes |
| 60 | cvt-rb-v1 | 360000 | 3040 | 1975 | HBR2 | 4 | 8 bpc | 50.0 | yes |
| 60 | cvt-rb-v1 | 360000 | 3040 | 1975 | HBR2 | 4 | 10 bpc | 37.5 | yes |
| 60 | cvt-rb-v1 | 360000 | 3040 | 1975 | HBR3 | 2 | 8 bpc | 33.3 | yes |
| 60 | cvt-rb-v1 | 360000 | 3040 | 1975 | HBR | 4 | 8 bpc | 0.0 | yes |
| 120 | cvt-rb-v1 | 741500 | 3040 | 2033 | HBR3 | 4 | 8 bpc | 31.3 | yes |
| 120 | cvt-rb-v1 | 741500 | 3040 | 2033 | HBR3 | 4 | 10 bpc | 14.2 | yes |
| 120 | cvt-rb-v1 | 741500 | 3040 | 2033 | HBR2 | 4 | 8 bpc | -3.0 | no |
| 120 | cvt-rb-v1 | 741500 | 3040 | 2033 | HBR2 | 4 | 10 bpc | -28.7 | no |
| 120 | cvt-rb-v1 | 741500 | 3040 | 2033 | HBR3 | 2 | 8 bpc | -37.3 | no |
| 120 | cvt-rb-v1 | 741500 | 3040 | 2033 | HBR | 4 | 8 bpc | -106.0 | no |
| 60 | explicit-80x30 | 346320 | 2960 | 1950 | HBR3 | 4 | 8 bpc | 67.9 | yes |
| 60 | explicit-80x30 | 346320 | 2960 | 1950 | HBR2 | 4 | 8 bpc | 51.9 | yes |
| 60 | explicit-80x30 | 346320 | 2960 | 1950 | HBR | 4 | 8 bpc | 3.8 | yes |
| 120 | explicit-80x30 | 692640 | 2960 | 1950 | HBR3 | 4 | 8 bpc | 35.9 | yes |
| 120 | explicit-80x30 | 692640 | 2960 | 1950 | HBR3 | 4 | 10 bpc | 19.8 | yes |
| 120 | explicit-80x30 | 692640 | 2960 | 1950 | HBR2 | 4 | 8 bpc | 3.8 | yes |
| 120 | explicit-80x30 | 692640 | 2960 | 1950 | HBR2 | 4 | 10 bpc | -20.2 | no |
| 120 | explicit-80x30 | 692640 | 2960 | 1950 | HBR3 | 2 | 8 bpc | -28.3 | no |
| 120 | explicit-80x30 | 692640 | 2960 | 1950 | HBR | 4 | 8 bpc | -92.4 | no |
<!-- END LINK-BUDGET-TABLE -->

Reading it (all MODEL, with the blanking named):

- **60 Hz is easy.** Anything at or above 4 lanes of HBR or 2 lanes of HBR2 carries it at 8 bits; the reduced-blanking
  stream is exactly the capacity of 4 x HBR (8.64 Gbit/s), so that link has no margin at all.
- **120 Hz at 8 bits needs 17.8 Gbit/s (reduced blanking) or 16.6 Gbit/s (80 x 30 blanking).** 4 x HBR3 (25.9) fits both
  depths with 14 to 36 % margin. 4 x HBR2 (17.28) misses by 3 % with reduced blanking and fits by 3.8 % with 80 x 30
  blanking at 8 bits: not a margin to rely on, and the real blanking is the panel's, which only its EDID says.
- **Two lanes never carry 120 Hz uncompressed** at any rate up to HBR3 (12.96 Gbit/s at most), and 4 x HBR (8.64) and
  4 x HBR2 at 10 bits do not either.
- **What DSC would need.** The most bits per pixel that fit at 120 Hz with reduced blanking: 34.9 on 4 x HBR3, 23.25 on
  4 x HBR2, 17.4 on 2 x HBR3, 11.6 on 4 x HBR; so 24 bits per pixel does not fit 4 x HBR2, and a DSC target of 8 bits per
  pixel fits 2 x HBR3 with 54 % margin (MODEL). DSC is not proposed: the pinned DPU interface code handles DSC for DSI
  only (`dpu_hw_intf.c:200`), and whether the firmware uses DSC on this panel is UNKNOWN.
- **The deciding unknown is the link the firmware trained** (lanes and rate), not the panel's capability: a firmware
  that trained 4 x HBR3 leaves room for 120 Hz; a firmware that trained something smaller to save power does not, and
  then 120 Hz needs retraining (kill criterion K7). The DPCD maximum rate cannot settle it (H9 to H11).

<a id="s5-what-a-60-to-120-hz-change-would-touch"></a>
## 5. What a 60 to 120 Hz change would touch

Facts from the pinned Linux sources for this SoC family, then what they mean for a firmware-owned, already running
pipeline. Everything about this unit stays INFERRED or UNKNOWN until read (section 9).

| Id | What | Fact at the pin | Class | Label |
| --- | --- | --- | --- | --- |
| T1 | DPU interface timing engine (INTF_5) | `dpu_hw_intf_setup_timing_engine` derives the line period, frame period (in pixel clocks), pulse widths, display and active windows, polarity, panel format and the wide-bus bit from the totals, then writes HSYNC_CTL, VSYNC_PERIOD_F0, VSYNC_PULSE_WIDTH_F0, DISPLAY_HCTL, DISPLAY_V_START/END_F0, ACTIVE_HCTL, ACTIVE_V_START/END_F0, POLARITY_CTL, PANEL_FORMAT and CONFIG/CONFIG2 (`dpu_hw_intf.c:243-269`; offsets at `:19-46`). All are plain registers of a 32-bit page. | A: firmware-owned; needs a timing rewrite | VERIFIED (code); INFERRED (that the firmware's INTF is this IP) |
| T2 | DP controller main-stream attributes | `msm_dp_panel_timing_cfg` writes TOTAL_HOR_VER, START_HOR_VER_FROM_SYNC, HSYNC_VSYNC_WIDTH_POLARITY and ACTIVE_HOR_VER of the link block and DATABUS_WIDEN of the p0 block; MISC1_MISC0 carries the colour depth (`dp_panel.c:667-735`, `dp_ctrl.c:534-542`; offsets `dp_reg.h:123-194,340`). | A: timing rewrite | VERIFIED (code); INFERRED (unit) |
| T3 | MVID, NVID and transfer-unit values | `msm_dp_ctrl_config_msa` derives them from the link rate and the stream clock (a rational approximation, `dp_ctrl.c:2516-2582`); `msm_dp_ctrl_setup_tr_unit` writes VALID_BOUNDARY, TU and VALID_BOUNDARY_2 from the stream, lanes and depth (`dp_ctrl.c:1310-1361`). A refresh change changes both; a stale pair makes the sink mis-recover the clock or the link under-run. | A: register rewrite, but the values need a correct transfer-unit calculation | VERIFIED (code); INFERRED (consequence) |
| T4 | Pixel clock | The DP3 pixel clock is the dispcc root `disp_cc_mdss_dptx3_pixel0_clk_src` (command register 0x82a0, 16-bit M/N, parent index 3 = the DP3 PHY's VCO-divided clock), set by a rational approximation of parent over rate (`dispcc-x1e80100.c:512-526`, `clk-rcg2.c:1785-1827`) and latched by an update bit with a bounded wait (`clk-rcg2.c:114-135`). The PHY's VCO-divided output is a fixed function of the link rate (1.35 GHz for HBR, HBR2 and HBR3; 810 MHz for RBR; `phy-qcom-edp.c:419-453`, the driver the DP3 PHY binds to, section 9 item E). With wide bus the controller is given half the pixel rate (`dp_ctrl.c:2630-2639`). | B: a clock change; no PLL change if the link rate is unchanged | VERIFIED (code); INFERRED (that the firmware uses this clock tree) |
| T5 | Link rate, lanes and PHY | Linux sets these only through link training. `msm_dp_display_prepare_link` initialises the PHY and forces training whenever the display is not already powered, then trains with rate and lane fallbacks (`dp_display.c:632-665`, `dp_ctrl.c:1699-1780,2379-2480`). It retrains an existing link only if channel equalisation fails or training was forced (`dp_ctrl.c:2611`). | C: retraining or PHY work, needed only if the trained link is too small | VERIFIED (code); UNKNOWN (what the firmware trained) |
| T6 | DPU core clock and bandwidth | Linux sizes the per-CRTC core clock as total lines x active width x refresh, times a catalog inefficiency factor (105 for this DPU), then clamps it to the table maximum instead of rejecting the mode (`dpu_core_perf.c:40-51,97,396`, `dpu_9_2_x1e80100.h:410`). The dispcc MDP clock table ends at 575 MHz and the device-tree operating points at 575 MHz (`dispcc-x1e80100.c:557-583`, `hamoa.dtsi:5650-5676`). | B and D: a clock change that also needs a power-domain level, which EL2 cannot set safely | VERIFIED (code and tables); UNKNOWN (what the firmware runs) |
| T7 | DPU mixers and fetch | The catalog limits one mixer to 2560 pixels of width (`dpu_hw_catalog.c:103`, `dpu_9_2_x1e80100.h:10`), so a 2880-wide output cannot be one mixer's. The firmware's arrangement is not known. | not touched by a timing rewrite; sets the throughput | VERIFIED (limit); INFERRED (a multi-mixer path); UNKNOWN (arrangement) |
| T8 | The panel itself | The sink may or may not ignore the MSA timing (`DP_MSA_TIMING_PAR_IGNORED`, DPCD 0x007 bit 6); whether its timing controller re-locks, blanks or follows a live clock change is not documented. The pinned panel driver warns that the controller can crash if the data stops and that a lost enable line needs a power cycle. | D: outside any register we can read | VERIFIED (driver comments, paraphrased); UNKNOWN (behaviour) |

The pinned numbers behind T6, from the formula above (VERIFIED arithmetic; host-checked by
`test_the_core_clock_estimate_table`):

<!-- BEGIN CORE-CLOCK-ESTIMATE (vtotal x 2880 x Hz x 1.05, in MHz; table maximum 575) -->
| Hz | Vtotal | Estimate MHz | Above 575 |
| --- | --- | --- | --- |
| 60 | 1975 | 358.3 | no |
| 120 | 1950 | 707.6 | yes |
| 120 | 2033 | 737.7 | yes |
| 120 | 1920 | 696.7 | yes |
<!-- END CORE-CLOCK-ESTIMATE -->

The last row has no blanking at all. The estimate is a single-pipe figure; with two mixers it may be half. Linux clamps
rather than refuses, so the pinned driver cannot tell us whether the stream underruns. Linux's DP mode check does not
stop a 120 Hz stream either: it refuses a pixel clock (halved for wide bus) above 675 MHz (`dp_display.h:12`,
`dp_display.c:779-783`), and the halved 120 Hz clock is 370.75 MHz (VERIFIED arithmetic). X13a reads the MDP clock the
firmware actually runs (T6, section 9).

**What Linux does for a refresh change: a full modeset, always.** A change of mode is a disable followed by an enable.
Disable pushes the idle pattern (and, at the pin, deliberately does nothing when the stream never started, because
pushing idle into a controller that never came up ends in a platform reset on the Snapdragon X2 Elite), turns the pixel
clock off, tears the main link down (`msm_dp_ctrl_off_link`), exits the PHY and drops the runtime-power reference
(`dp_display.c:1504-1565`). Enable then initialises the PHY, trains the link and starts the stream. The enable path
returns early if the display is already on (`dp_display.c:672-676`), and the pixel-clock enable routine fails with a
warning when the stream clock is already on (`dp_ctrl.c:2245-2257`): it only ever runs on a stopped stream, and there is
no live-retiming path in this driver. For an eDP panel each enable and disable is also reported to the driver as a plug
and an unplug (`dp_display.c:640-641,1556-1557`).
(VERIFIED for the pinned driver.) So Linux is no precedent for a seamless switch on this SoC.

**A precedent exists elsewhere.** The i915 driver changes refresh on a running eDP link: it sizes the link for the
highest mode, then reprograms the double-buffered link M/N values and the frame length without a full modeset, for panels
that declare a seamless refresh type (`intel_dp.c:1673-1695,3257-3320`). VERIFIED for that driver and silicon; it says
nothing about this unit.

**Can a rewrite work without retraining when the firmware-trained link already has the bandwidth?** INFERRED yes in
principle: the training state (rate, lanes, voltage swing, pre-emphasis) does not depend on the video timing, and T4
changes only a divider after the PHY's fixed output. Three things decide it and none is known: whether the sink's
timing controller accepts a live clock change (T8), whether the INTF timing registers take effect immediately or at
the next frame (the pinned driver writes them with the timing engine off), and whether the DPU core clock has headroom
(T6). The reads that settle the first two are indirect, so X13b exists to test them with a revert; the reads that
settle the bandwidth question are named in section 9, by block, offset and pinned source.

<a id="s6-the-guest-side"></a>
## 6. The guest side

What this tree shows (the kext and plug-in submodules `gpu/adreno-kext/{kext,plugin}` are empty here, so their code is
not inspected: UNKNOWN):

| Id | Fact | Label | Source |
| --- | --- | --- | --- |
| G1 | On the A16 the guest copies each finished frame into the scanout buffer that the display controller is already scanning. Snapintosh does not reprogram the display controller. | VERIFIED (repository text) | `docs/ARCHITECTURE.md`, "Present path and frame pacing" |
| G2 | With `--present-beam` the guest reads one read-only page of the DPU interface block (the line counter, the frame counter and the timing registers) and locks its present tick to the real vblank, so the tick follows the panel's actual period and not a constant. | VERIFIED (repository text); UNKNOWN (how the kext turns the registers into a period) | `compat/display-timing.h:3-12`, `compat/dpu-intf.h:31-42` |
| G3 | The frame tick of `--present-vsync60` is an absolute 16,666,667 ns grid: 60 Hz is a constant in the tooling and the kext it arms. | VERIFIED (repository text) | `tools/cycle.py` (`--present-vsync60`) |
| G4 | The pacing summary reads its intervals in 60 Hz buckets (17, 20, 34, 50 ms; 20 ms or less is on time, 20 to 34 ms is one refresh shown twice). At 120 Hz these buckets mean nothing and must be re-derived from the 8.33 ms period. | VERIFIED (repository text) | `tools/summarize-present-pacing.py:10-11` |
| G5 | The A16 already lists the same item: "Drive the panel at its native refresh rate once 60 Hz is steady", "the panel's 120 Hz mode after a steady 60 Hz" (open), and "the 120 Hz panel mode" under not supported. | VERIFIED (repository text) | `docs/STATUS.md`, `docs/GUI-BOOT-ROADMAP.md` |
| G6 | The present copy fell from 67 ms to under 3 ms per frame on the A16. At 120 Hz the budget is 8.33 ms, so a 3 ms copy is 36 % of it; the copy moves 2880 x 1920 x 4 bytes = 22.1 MB, about 2.65 GB/s at 120 Hz. | OBSERVED (A16 figure, recorded by the repository); INFERRED (the ratio at the SP11) | `docs/GUI-BOOT-ROADMAP.md`; arithmetic |
| G7 | The GOP report that `tools/sp11_display_mode.py` validates carries width, height, stride and format, no refresh rate. The refresh therefore comes only from the EDID timing, the interface registers or a measurement. | VERIFIED | `tools/sp11_display_mode.py` (the `mode` it emits) |

**What would change for 120 Hz, and why it is one implementation.** The guest needs (1) the mode's refresh from one
place (the EDID timing the monitor advertises, confirmed by the interface registers), (2) a present tick whose period
comes from that value or from the beam, not from the 60 Hz constant of G3, (3) a copy cost inside the shorter budget
(G6; INFERRED: this may need a partial copy or direct scanout), and (4) a pacing summary with buckets derived from the
period (G4). These are the same four items for the A16's own 120 Hz item (G5), carried by the SnapAdreno kext and
SnapMetal plug-in submodules and by `tools/cycle.py`, which the A16 roadmap owns. AGENTS.md forbids parallel
implementations, so the SP11 contributes only what is SP11-specific: the panel's advertised timings (these tools), the EL2
mode switch (gate X13b) and the register facts. A refresh parameter on the present-tick flag is the proposed change to the
shared tooling, filed against the A16 item and not made here.

**Presentation-pacing acceptance (gate X13d).** A claim needs a before and an after from the same build and workload.
Measure only windows in which the GPU is not recovering and the run is complete; report, for the 60 Hz baseline and the
120 Hz run: the median and the 95th and 99th percentile of the present interval, the share of intervals within 1.5
periods (12.5 ms at 120 Hz), the count of missed refreshes (an interval of two periods or more), the achieved
presents per second against the 120 or 60 target, and the correctness counters the present path already prints (stale
or torn frames, GPU hangs and recoveries). An improvement that adds corruption, a missed deadline or a hang is not an
improvement. **GPU load for 2880 x 1920 at 120 frames per second on the X1-85 is UNKNOWN until the GPU gates (X8e and
X11) have run**; the study does not predict it.

<a id="s7-safety-authorization-and-recovery"></a>
## 7. Safety, authorization and recovery

1. **Only advertised timings.** The mode switch is given a timing that `tools/sp11_display_mode.py modes --require`
   found in the unit's own EDID, with the same active size, the same colour depth and the same polarity as the running
   mode. A timing computed by the link-budget model (reduced blanking is an assumption) is never programmed; the model
   answers "could it fit", not "what to write". An EDID that decodes partially gives UNKNOWN, which is not permission.
2. **Capture before change.** X13b first records every register it will write (the values X13a read, read again), and
   the revert writes those captured values back, not recomputed ones.
3. **Revert automatically and manually.** A dead-man timer at EL2, independent of the host link, restores the captured
   state after a fixed hold unless the host confirms in time; the host can also order the revert at any moment. A failed
   revert verification (a register that does not read back as captured) stops the gate and is a K7 result.
4. **If the panel goes dark.** The operator sees a black screen and the console's text page is gone. Recovery: (a) wait
   for the revert time plus a margin; the 60 Hz timing returns if the panel re-locks; (b) if it stays dark, force a
   shutdown with the power button and boot again, which restores the firmware's own initialisation because X13 writes
   only volatile display-controller and clock registers (a requirement of the authorization; INFERRED to be sufficient);
   (c) at most one switch per boot; a second attempt after a dark panel needs a new authorization. The pinned panel
   driver warns that this timing controller can crash if its data stops (T8), which is why the step is bounded.
5. **Thermal and power.** The plan's hardware-safety rules apply unchanged (the hardware safety and authorization
   section of [SURFACE-PRO-11.md](SURFACE-PRO-11.md)): AC power or enough battery, the thermal guard on, no change to
   any clock or voltage outside what the
   authorization names. A 120 Hz stream raises the DPU clock and the link load (T6), so X13b stays bounded in time and
   aborts to the revert on a guard trip. Raising the DPU core clock above what the firmware set is outside X13: it needs
   a power-domain level that EL2 cannot set safely, so if the firmware's clock has no headroom the gate stops (K7).
6. **Authorization row.** A mode switch changes the panel's timing, so it is explicit and per run: the run names the
   timing (its source block and refresh), the hold time, the revert time and the unit; it needs a passing X13a of the
   same unit and boot; it is never combined with another experiment; it covers volatile registers only. The matching
   row belongs in the plan's hardware safety and authorization section, with first gate X13b.

<a id="s8-gate-x13-and-kill-criterion-k7"></a>
## 8. Gate X13 and kill criterion K7

Each exit is falsifiable on one unit; a gate that is not passed is reported with the raw values.

| Sub-gate | Work | Exit (all must hold) | Stop |
| --- | --- | --- | --- |
| **X13a**, read-only qualification, after X2, needs only the probe | Dump every EDID block (not only the first) and the registers of section 9; run `modes --edid edid.bin --require <native>@120`; run `link-budget` with the lane count and rate read from hardware and the depth read from MISC0; cross-check the running timing against an advertised 60 Hz timing | (1) every EDID block is checksum-valid and the decode is complete, or the missing block is named; (2) an advertised progressive timing of the native size at 120 Hz exists (or the answer is recorded as K7); (3) `link-budget` says the 120 Hz timing fits the lanes and rate the hardware shows, with at least 5 % margin; (4) the running INTF totals and pixel clock equal an advertised 60 Hz timing; (5) the MDP clock the firmware runs is recorded against the 120 Hz estimate; (6) every read is recorded as a value or a fault, none is a write, and the display did not change | any fault on a DP or PHY read ends the DP reads; no retry |
| **X13b**, bounded mode switch at EL2 in q1n1, with revert, separately authorized | Apply the rewrite plan of one advertised timing in the order the plan lists; hold; verify; revert to the captured state; verify | (1) the panel shows the test pattern at the new timing for the hold time, confirmed by the operator and by a measured frame period of 8.33 ms within 2 % over at least 60 frames; (2) the revert restores every captured register and the 16.67 ms period; (3) the link was not retrained (no training sequence, no PHY re-initialisation observed); (4) no underflow or error status; (5) no persistent write | first anomaly: revert at once; a second anomaly, a dark panel after the revert time, or a failed revert verification stops the gate (K7) |
| **X13c**, the guest sees 120 Hz | Make the shared guest mechanism report the 120 Hz timing and tick at it | the guest reads 120 Hz from the shared mechanism; a 60 second run shows the guest tick at 8.33 ms within 2 %; the revert to 60 Hz is exercised | a guest hang or a tick that does not follow the panel: revert to 60 Hz |
| **X13d**, presentation pacing at 120 | Run the section 6 acceptance with before and after data | the measured windows meet the targets the A16 owners set for the shared path, with the correctness counters at zero; the numbers are filled from measured windows only | no claim without both runs |

**K7 (proposed wording).** *120 Hz needs more than a timing rewrite. If X13a finds that the EDID advertises no 120 Hz
progressive timing of the native size, or that the 120 Hz stream does not fit the link the firmware trained (so it
would need link retraining, PHY re-initialisation, DSC or a DPU clock or voltage change that cannot be proven safe at
EL2), or if X13b cannot restore the captured 60 Hz state after its first anomaly: stop. The panel stays at the
firmware's 60 Hz, the finish line is unaffected, and the report says which condition fired and the values read.*

<a id="s9-does-the-x2-dump-list-suffice-for-x13a"></a>
## 9. Does the X2 dump list suffice for X13a?

No. Section 8 of [SP11-DISPLAY-STUDY.md](SP11-DISPLAY-STUDY.md) (the `X2-DISPLAY-LIST` block) covers the GOP mode, the
EDID bytes if a firmware protocol publishes them, the gates, the INTF page's timing windows and the frame counter. For
X13a it falls short in these places, and it forbids by design the DP controller, the PHY, AUX and DPCD. The requests
below go to the owner of the functional-test and probe code, who owns those files; this study changes neither.

<!-- BEGIN X13A-DUMP-ADDITIONS (the uefi-func-tests agent may lift this section; every offset is a Hamoa value from the pinned sources, INFERRED to hold on the unit) -->
**A. Software facts (no register access).**

1. Every EDID byte the firmware publishes: `SizeOfEdid` bytes, base block and all extension blocks, from
   `EFI_EDID_ACTIVE_PROTOCOL` and also from `EFI_EDID_DISCOVERED_PROTOCOL` if both exist (the active one can be an
   override). A 128-byte first block alone cannot answer the 120 Hz question (section 3). Record the sizes and say if
   the protocol is absent.
2. The GOP's mode list (`MaxMode` and each `QueryMode`), to see whether the firmware offers a second mode.
3. The ACPI tables the probe already writes (`DSDT.bin` among them, `probes/uefi/dump.h`), searched offline for the
   `EDPOverrideDPCDCaps` object (H9).

**B. DPU interface registers (INTF_5, base `0x0ae3a000`), additions to the existing list**, offsets from `dpu_hw_intf.c:19-46`:

| Offset | Register | Why |
| --- | --- | --- |
| 0x014 | INTF_VSYNC_PULSE_WIDTH_F0 | vertical pulse width, needed to reconstruct the running timing |
| 0x050 | INTF_POLARITY_CTL | sync polarity, must be kept by any rewrite |
| 0x090 | INTF_PANEL_FORMAT | the depth the interface carries |
| 0x064, 0x068 | INTF_DISPLAY_DATA_HCTL, INTF_ACTIVE_DATA_HCTL | only if INTF_CONFIG2 bit 4 is set |
| 0x270 to 0x27c | INTF_AVR_CONTROL, MODE, TRIGGER, VTOTAL (`dpu_hw_intf.c:76-79`) | meaning UNKNOWN; names only. Record, do not interpret |

**C. dispcc (base `0x0af00000`), only after the existing gates read as running**, offsets from `dispcc-x1e80100.c` and
`clk-rcg2.c:26-47`:

| Offset | Register | Why |
| --- | --- | --- |
| 0x82a0, 0x82a4, 0x82a8, 0x82ac, 0x82b0 | DP3 pixel RCG: CMD, CFG, M, N, D | the pixel clock the firmware runs and its source selection |
| 0x82b8, 0x82bc, 0x82d0 | DP3 link RCG CMD and CFG, link divider | link clock source |
| 0x8094, 0x8098, 0x809c | DP3 pixel, link and link-interface CBCRs (bit 31 `CLK_OFF`) | gates for the next block: read the DP3 controller only if all three read running |
| 0x80a0 | DP3 AUX CBCR (bit 31 `CLK_OFF`; `dispcc-x1e80100.c:1206-1222`) | a PHY gate, with the AHB CBCR `0x80a8` of the existing list |
| 0x80dc, 0x80e0 | MDP RCG CMD and CFG | the DPU core clock source and divider (T6) |

**D. DP3 controller, only if section C says its clocks run** (reads can fault when a clock is off). The link block is
the third register range of the controller node, `0x0aea0400`; the p0 block is the fourth, `0x0aea1000`
(`hamoa.dtsi:5898-5904`, binding order `dp-controller.yaml:74-86`); offsets from `dp_reg.h:123-194,340`:

| Block | Offset | Register | Why |
| --- | --- | --- | --- |
| link | 0x00, 0x04, 0x08 | MAINLINK_CTRL, STATE_CTRL, CONFIGURATION_CTRL | stream state, lane count |
| link | 0x10, 0x18 | SOFTWARE_MVID, SOFTWARE_NVID | the running clock ratio |
| link | 0x1c, 0x20, 0x24, 0x28 | TOTAL_HOR_VER, START_HOR_VER_FROM_SYNC, HSYNC_VSYNC_WIDTH_POLARITY, ACTIVE_HOR_VER | the running main-stream attributes |
| link | 0x2c | MISC1_MISC0 | colour depth |
| link | 0x30, 0x34, 0x4c | VALID_BOUNDARY, VALID_BOUNDARY_2, TU | transfer-unit state |
| link | 0x40 | MAINLINK_READY | ready for video |
| p0 | 0x14 | MMSS_DP_INTF_CONFIG | wide-bus bit |

**E. DP3 PHY, only after the AUX and AHB gates of section C (`0x80a0`, `0x80a8`) read as running.** The PHY also needs
the TCSR eDP reference clock, which no dispcc read shows (UNKNOWN). The node `mdss_dp3_phy` (`hamoa.dtsi:6000-6007`,
compatible `qcom,x1e80100-dp-phy`) is bound by the eDP PHY driver (`phy-qcom-edp.c:1491`), not by the USB-C combo driver.
Its four ranges are, in order, the PHY block `0x0aec5a00`, tx0 `0x0aec5200`, tx1 `0x0aec5600` and the PLL block
`0x0aec5000` (`qcom,edp-phy.yaml:33-38`, `phy-qcom-edp.c:1430-1442`); the x1e80100 uses the v6 PLL layout
(`phy-qcom-edp.c:903-909`). The link rate and the lane count, with what Linux writes and so what a read should show:

| Block | Offset | Register | Value by Linux's sequence |
| --- | --- | --- | --- |
| PLL | 0x3c | QSERDES_V6_COM_HSCLK_SEL_1 (`qserdes-com-v6.h:24`) | 0x5 RBR, 0x3 HBR, 0x1 HBR2, 0x0 HBR3 (`phy-qcom-edp.c:789-833`) |
| PLL | 0x80 | QSERDES_V6_COM_LOCK_CMP1_MODE0 (`v6.h:39`) | 0x37, 0x07, 0x0f, 0x17 in that order: the cross-check |
| PLL | 0x84, 0x88, 0x94, 0x98 | LOCK_CMP2_MODE0, DEC_START_MODE0, DIV_FRAC_START2_MODE0, DIV_FRAC_START3_MODE0 (`v6.h:40-45`) | record |
| PHY | 0x74 | DP_PHY_VCO_DIV (`phy-qcom-edp.c:42,235-237,419-453`) | 0x01 for RBR and for HBR alike, 0x02 HBR2, 0x00 HBR3 |
| PHY | 0x14 | DP_PHY_CFG_1 (`phy-qcom-edp.c:33,1177-1196`) | 0x1 one lane, 0x3 two, 0xf four |
| PHY | 0x1c | DP_PHY_PD_CTL (`phy-qcom-edp.c:34`, bits `phy-qcom-qmp-dp-phy.h:47-53`) | power-down bits: lanes 0-1 `0x008`, lanes 2-3 `0x010`, PLL `0x020`, clamp `0x040` |
| PHY | 0x7c, 0xa0 | DP_PHY_TX0_TX1_LANE_CTL, DP_PHY_TX2_TX3_LANE_CTL (`phy-qcom-edp.c:43-44`) | record |
| PHY | 0xe0 | DP_PHY_STATUS (`phy-qcom-edp.c:46,1203-1204`) | bit 1 set: ready, the bit the driver polls |

A rate is established only when HSCLK_SEL_1 and LOCK_CMP1_MODE0 match the same row; any other pair is recorded and the
rate stays UNKNOWN (these are Linux's values, INFERRED for the firmware). The lanes are established only when
`DP_PHY_CFG_1` and the controller's lane field (item D) agree. The rate also follows from MVID, NVID and the pixel
divider by the pinned formula (`dp_ctrl.c:2516-2582`): INFERRED, more error-prone.

**F. DPCD (not an X13a read).** LINK_BW_SET (0x100), LANE_COUNT_SET (0x101), the maximum rate and lanes (0x001, 0x002),
the rate table (0x010), `MSA_TIMING_PAR_IGNORED` (0x007 bit 6), DSC support (0x060) and the sink OUI (0x400) need an AUX
transaction, which the X2 list forbids and which touches the panel. They need their own explicit authorization (a
read-only AUX read is not a pure memory read). Offsets: `drm_dp.h:107-180,244,590-598,1013`. Until then the link rate
comes from item E.
<!-- END X13A-DUMP-ADDITIONS -->

<a id="s10-what-stays-unknown-and-what-would-settle-it"></a>
## 10. What stays UNKNOWN and what would settle it

| Question | Why it is open | Evidence that resolves it |
| --- | --- | --- |
| Does the panel advertise 120 Hz at all, and as two timings or a range? | no public EDID (H6, H12) | the EDID blocks at X13a (section 9, item A1) |
| What link did the firmware train (lanes, rate, depth)? | the DPCD maximum rate is unreliable on this panel (H9 to H11) | X13a items D and E, then F if authorized |
| Does the 120 Hz stream fit it? | depends on the line above and on the panel's real blanking | `link-budget` on the X13a values, at least 5 % margin |
| Does the sink accept a live clock change? | the panel's timing controller is undocumented (T8) | X13b, with revert |
| Do the INTF registers take effect live? | the pinned driver writes them with the engine off | X13b |
| Does the DPU have core-clock headroom at 120 Hz? | the firmware's clock and the mixer arrangement are unknown (T6, T7) | X13a item C and a read-only mixer survey |
| Is the DP link ever retrained by the firmware on its own? | unknown firmware behaviour | the link-status registers before and after X13b |
| GPU load at 2880 x 1920 and 120 fps on the X1-85 | no GPU gate has run | X8e and X11 |
| Guest-side kext and plug-in behaviour at 120 Hz | submodule sources absent here | the A16 owners, on the shared item (G5) |
| Which public panel name is right (H8) | two public names | the EDID name descriptor at X13a |

<a id="s11-how-to-run-the-checks"></a>
## 11. How to run the checks

```sh
python3 tools/test-sp11-display-120hz.py             # about 3 s: the decoders, the model, the subcommands, the tables
python3 tools/test-sp11-display-120hz-mutants.py     # about 2 min: every seeded bug must be caught
python3 tools/test-sp11-display-mode.py              # the original mode validator (and its own mutation check)
python3 tools/test-sp11-120hz-study.py               # this study's citations, structure and tables
SP11_LINUX_TREE=<checkout> python3 tools/test-sp11-display-120hz.py       # also compares the video-code table with Linux
SP11_LINUX_TREE=<checkout> python3 tools/test-sp11-120hz-study.py         # also the Linux citations and SHA-256 pins
python3 tools/sp11_display_mode.py modes --edid FILE --require 2880x1920@120
python3 tools/sp11_display_mode.py link-budget --h-active 2880 --v-active 1920 --refresh 120 --bpc 8 --lanes 4 --rate HBR3
```

The tests need only Python 3. Without `SP11_LINUX_TREE` the Linux-citation tests are skipped, never passed. They write
only under `build/` and open no device; the tools open an operator-named file only through the hardened reader that
refuses links, devices and oversized files.
