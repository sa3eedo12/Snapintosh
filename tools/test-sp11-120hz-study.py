#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Checks that docs/SP11-120HZ-STUDY.md still says what the tree and its pinned sources say.

The study cites file and line ranges of this repository (section 6) and of the pinned Linux sources (sections 1, 4, 5
and 9). Each citation must name an existing range in which a distinguishing token occurs, so a moved or mis-numbered
range fails here instead of misleading a reader. Shared documents and tools/cycle.py are checked for the text only,
because other work edits them and moves their line numbers. The tables with numbers (the link budget and the core
clock estimate) are recomputed from the model; the evidence ids, the gate text and the markers that other documents may
lift are checked for presence.

The Linux claims need that checkout, which the repository does not carry: set SP11_LINUX_TREE to a Linux tree at the
commit named in the study. The SHA-256 prefixes in section 1 are compared first, so a checkout at another commit is
refused instead of trusted. SP11_EDID_DECODE_TREE does the same for the pinned v4l-utils checkout. Without a variable
the matching tests are skipped, never passed.

    python3 tools/test-sp11-120hz-study.py
    SP11_LINUX_TREE=<checkout> SP11_EDID_DECODE_TREE=<checkout> python3 tools/test-sp11-120hz-study.py
"""
import hashlib
import os
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / 'docs' / 'SP11-120HZ-STUDY.md'
sys.path.insert(0, str(ROOT / 'tools'))
import sp11_link_budget as lb               # noqa: E402

DTS = 'arch/arm64/boot/dts/qcom/'
DPU = 'drivers/gpu/drm/msm/disp/dpu1/'
DP = 'drivers/gpu/drm/msm/dp/'
DRM = 'drivers/gpu/drm/'
PHY = 'drivers/phy/qualcomm/'
CLK = 'drivers/clk/qcom/'
TEXT_ONLY = ('docs/STATUS.md', 'docs/ARCHITECTURE.md', 'docs/GUI-BOOT-ROADMAP.md', 'docs/SURFACE-PRO-11.md', 'tools/cycle.py')
PANEL_WIDTH = 2880                          # the width the study's tables use; the tools themselves assume none
CORE_CLOCK_FACTOR = 105                     # clk_inefficiency_factor of the pinned DPU catalog
CORE_CLOCK_TABLE_MAX_MHZ = 575

# (citation as the study writes it, file, first line, last line, a token that must occur in those lines)
IN_TREE = (
    ('compat/display-timing.h:3-12', 'compat/display-timing.h', 3, 12, 'READ-ONLY'),
    ('compat/display-timing.h:3-12', 'compat/display-timing.h', 3, 12, 'LINE_COUNT'),
    ('compat/dpu-intf.h:31-42', 'compat/dpu-intf.h', 31, 42, 'DPU_INTF_HSYNC_CTL'),
    ('compat/dpu-intf.h:31-42', 'compat/dpu-intf.h', 31, 42, 'DPU_INTF_LINE_COUNT'),
    ('tools/summarize-present-pacing.py:10-11', 'tools/summarize-present-pacing.py', 10, 11, '<= 17'),
    ('tools/summarize-present-pacing.py:10-11', 'tools/summarize-present-pacing.py', 10, 11, 'one refresh shown twice'),
    ('tools/cycle.py', 'tools/cycle.py', 0, 0, '16,666,667 ns'),
    ('docs/ARCHITECTURE.md', 'docs/ARCHITECTURE.md', 0, 0, 'reprogram the display controller'),
    ('docs/STATUS.md', 'docs/STATUS.md', 0, 0, 'Drive the panel at its native refresh rate'),
    ('docs/STATUS.md', 'docs/STATUS.md', 0, 0, 'the 120 Hz panel mode'),
    ('docs/GUI-BOOT-ROADMAP.md', 'docs/GUI-BOOT-ROADMAP.md', 0, 0, "panel's 120 Hz mode after a steady 60 Hz"),
    ('docs/GUI-BOOT-ROADMAP.md', 'docs/GUI-BOOT-ROADMAP.md', 0, 0, 'from about 67 ms to under 3 ms'),
)

# (file under the Linux checkout, first line, last line, token); every range is written in the study as it is here.
LINUX = (
    (DTS + 'x1-microsoft-denali.dtsi', 943, 978, 'edp-panel'),
    (DTS + 'x1-microsoft-denali.dtsi', 943, 978, 'data-lanes = <0 1 2 3>'),
    (DTS + 'x1-microsoft-denali.dtsi', 943, 978, '8100000000'),
    (DTS + 'x1e80100-microsoft-denali-oled.dts', 17, 19, 'samsung,atna30dw01'),
    ('Documentation/devicetree/bindings/display/panel/samsung,atna33xc20.yaml', 22, 23, 'samsung,atna30dw01'),
    (DRM + 'panel/panel-samsung-atna33xc20.c', 223, 237, 'drm_edid_read_ddc'),
    (DRM + 'panel/panel-samsung-atna33xc20.c', 223, 237, 'drm_edid_connector_add_modes'),
    (DTS + 'hamoa.dtsi', 5898, 5904, '0x0aea0400 0 0xc00'),
    (DTS + 'hamoa.dtsi', 5898, 5904, '0x0aea1000 0 0x400'),
    (DTS + 'hamoa.dtsi', 5650, 5677, 'opp-575000000'),
    (DTS + 'hamoa.dtsi', 6000, 6000, 'mdss_dp3_phy'),
    (DTS + 'hamoa.dtsi', 6000, 6007, 'qcom,x1e80100-dp-phy'),
    (DTS + 'hamoa.dtsi', 6000, 6007, '0x0aec5a00 0 0x19c'),
    (DTS + 'hamoa.dtsi', 6000, 6007, '0x0aec5000 0 0x1c8'),
    ('Documentation/devicetree/bindings/phy/qcom,edp-phy.yaml', 27, 27, 'qcom,x1e80100-dp-phy'),
    ('Documentation/devicetree/bindings/phy/qcom,edp-phy.yaml', 33, 38, 'PLL register block'),
    ('Documentation/devicetree/bindings/display/msm/dp-controller.yaml', 74, 86, 'link register block'),
    ('Documentation/devicetree/bindings/display/msm/dp-controller.yaml', 74, 86, 'p0 register block'),
    (DP + 'dp_ctrl.c', 534, 542, 'msm_dp_panel_timing_cfg'),
    (DP + 'dp_ctrl.c', 1310, 1361, 'msm_dp_ctrl_calc_tu_parameters'),
    (DP + 'dp_ctrl.c', 1310, 1361, 'REG_DP_TU'),
    (DP + 'dp_ctrl.c', 1699, 1780, 'msm_dp_ctrl_link_train'),
    (DP + 'dp_ctrl.c', 2245, 2257, 'WARN_ON_ONCE(ctrl->stream_clks_on'),
    (DP + 'dp_ctrl.c', 2379, 2480, 'msm_dp_ctrl_enable_mainlink_clocks'),
    (DP + 'dp_ctrl.c', 2516, 2582, 'pixel_div = 6'),
    (DP + 'dp_ctrl.c', 2516, 2582, 'REG_DP_SOFTWARE_NVID'),
    (DP + 'dp_ctrl.c', 2611, 2611, 'force_link_train || !msm_dp_ctrl_channel_eq_ok'),
    (DP + 'dp_ctrl.c', 2630, 2639, 'pixel_rate >>= 1'),
    (DP + 'dp_display.c', 632, 665, 'force_link_train = true'),
    (DP + 'dp_display.c', 640, 641, 'msm_dp_hpd_plug_handle'),
    (DP + 'dp_display.c', 672, 676, 'Link already setup, return'),
    (DP + 'dp_display.c', 779, 783, 'DP_MAX_PIXEL_CLK_KHZ'),
    (DP + 'dp_display.c', 792, 793, 'supported_rate_khz'),
    (DP + 'dp_display.c', 1504, 1565, 'glymur'),
    (DP + 'dp_display.c', 1556, 1557, 'msm_dp_hpd_unplug_handle'),
    (DP + 'dp_display.h', 12, 12, '675000'),
    (DP + 'dp_panel.c', 667, 735, 'REG_DP_TOTAL_HOR_VER'),
    (DP + 'dp_panel.c', 667, 735, 'DP_INTF_CONFIG_DATABUS_WIDEN'),
    (DP + 'dp_reg.h', 123, 194, 'REG_DP_MAINLINK_CTRL'),
    (DP + 'dp_reg.h', 123, 194, 'REG_DP_TU'),
    (DP + 'dp_reg.h', 340, 340, 'MMSS_DP_INTF_CONFIG'),
    (DPU + 'dpu_hw_intf.c', 19, 46, 'INTF_HSYNC_CTL'),
    (DPU + 'dpu_hw_intf.c', 19, 46, 'INTF_PANEL_FORMAT'),
    (DPU + 'dpu_hw_intf.c', 76, 79, 'INTF_AVR_CONTROL'),
    (DPU + 'dpu_hw_intf.c', 200, 200, 'TODO: handle DSC+DP case'),
    (DPU + 'dpu_hw_intf.c', 243, 269, 'INTF_VSYNC_PULSE_WIDTH_F0'),
    (DPU + 'dpu_hw_intf.c', 243, 269, 'INTF_CONFIG2'),
    (DPU + 'dpu_core_perf.c', 40, 51, 'clk_inefficiency_factor'),
    (DPU + 'dpu_core_perf.c', 97, 97, 'mode->vtotal * mode->hdisplay * drm_mode_vrefresh'),
    (DPU + 'dpu_core_perf.c', 396, 396, 'min(clk_rate, kms->perf.max_core_clk_rate)'),
    (DPU + 'catalog/dpu_9_2_x1e80100.h', 10, 10, 'max_mixer_width'),
    (DPU + 'catalog/dpu_9_2_x1e80100.h', 410, 410, 'clk_inefficiency_factor = 105'),
    (DPU + 'dpu_hw_catalog.c', 103, 103, 'DEFAULT_DPU_OUTPUT_LINE_WIDTH\t2560'),
    (CLK + 'dispcc-x1e80100.c', 512, 526, 'disp_cc_mdss_dptx3_pixel0_clk_src'),
    (CLK + 'dispcc-x1e80100.c', 512, 526, '0x82a0'),
    (CLK + 'dispcc-x1e80100.c', 557, 583, '575000000'),
    (CLK + 'dispcc-x1e80100.c', 557, 583, '0x80dc'),
    (CLK + 'clk-rcg2.c', 26, 47, 'CMD_UPDATE'),
    (CLK + 'clk-rcg2.c', 114, 135, 'update_config'),
    (CLK + 'clk-rcg2.c', 1785, 1827, 'rational_best_approximation'),
    (PHY + 'phy-qcom-edp.c', 32, 46, 'DP_PHY_CFG_1'),
    (PHY + 'phy-qcom-edp.c', 32, 46, 'DP_PHY_PD_CTL'),
    (PHY + 'phy-qcom-edp.c', 32, 46, 'DP_PHY_VCO_DIV'),
    (PHY + 'phy-qcom-edp.c', 32, 46, 'DP_PHY_TX2_TX3_LANE_CTL'),
    (PHY + 'phy-qcom-edp.c', 32, 46, 'DP_PHY_STATUS'),
    (PHY + 'phy-qcom-edp.c', 235, 237, '0x01, 0x01, 0x02, 0x00'),
    (PHY + 'phy-qcom-edp.c', 419, 453, 'writel(vco_div, edp->edp + DP_PHY_VCO_DIV)'),
    (PHY + 'phy-qcom-edp.c', 419, 453, '5400000000UL / 4'),
    (PHY + 'phy-qcom-edp.c', 789, 833, 'hsclk_sel = 0x5'),
    (PHY + 'phy-qcom-edp.c', 789, 833, 'hsclk_sel = 0x3'),
    (PHY + 'phy-qcom-edp.c', 789, 833, 'hsclk_sel = 0x1'),
    (PHY + 'phy-qcom-edp.c', 789, 833, 'hsclk_sel = 0x0'),
    (PHY + 'phy-qcom-edp.c', 789, 833, 'lock_cmp1_mode0 = 0x37'),
    (PHY + 'phy-qcom-edp.c', 789, 833, 'lock_cmp1_mode0 = 0x07'),
    (PHY + 'phy-qcom-edp.c', 789, 833, 'lock_cmp1_mode0 = 0x0f'),
    (PHY + 'phy-qcom-edp.c', 789, 833, 'lock_cmp1_mode0 = 0x17'),
    (PHY + 'phy-qcom-edp.c', 903, 909, 'qcom_edp_phy_ops_v6'),
    (PHY + 'phy-qcom-edp.c', 1177, 1196, 'cfg1 = 0xf'),
    (PHY + 'phy-qcom-edp.c', 1177, 1196, 'writel(cfg1, edp->edp + DP_PHY_CFG_1)'),
    (PHY + 'phy-qcom-edp.c', 1203, 1204, 'DP_PHY_STATUS'),
    (PHY + 'phy-qcom-edp.c', 1430, 1442, 'edp->edp = devm_platform_ioremap_resource(pdev, 0)'),
    (PHY + 'phy-qcom-edp.c', 1430, 1442, 'edp->pll = devm_platform_ioremap_resource(pdev, 3)'),
    (PHY + 'phy-qcom-edp.c', 1491, 1491, 'qcom,x1e80100-dp-phy'),
    (PHY + 'phy-qcom-qmp-qserdes-com-v6.h', 24, 24, 'QSERDES_V6_COM_HSCLK_SEL_1\t\t\t\t0x3c'),
    (PHY + 'phy-qcom-qmp-qserdes-com-v6.h', 39, 41, 'QSERDES_V6_COM_LOCK_CMP1_MODE0\t\t\t\t0x80'),
    (PHY + 'phy-qcom-qmp-qserdes-com-v6.h', 39, 41, 'QSERDES_V6_COM_DEC_START_MODE0\t\t\t\t0x88'),
    (PHY + 'phy-qcom-qmp-qserdes-com-v6.h', 44, 45, 'QSERDES_V6_COM_DIV_FRAC_START3_MODE0\t\t\t0x98'),
    (PHY + 'phy-qcom-qmp-dp-phy.h', 47, 53, 'DP_PHY_PD_CTL_LANE_2_3_PWRDN'),
    (PHY + 'phy-qcom-qmp-dp-phy.h', 47, 53, 'DP_PHY_PD_CTL_PLL_PWRDN'),
    (CLK + 'dispcc-x1e80100.c', 1206, 1222, '0x80a0'),
    (DRM + 'i915/display/intel_dp.c', 555, 563, 'icl_rates'),
    (DRM + 'i915/display/intel_dp.c', 555, 563, 'skl_rates'),
    (DRM + 'i915/display/intel_dp.c', 1673, 1695, 'has_seamless_m_n'),
    (DRM + 'i915/display/intel_dp.c', 3257, 3320, 'can_enable_drrs'),
    (DRM + 'drm_modes.c', 635, 637, 'CVT_CLOCK_STEP'),
    (DRM + 'drm_modes.c', 772, 778, 'CVT_RB_MIN_VBLANK'),
    (DRM + 'drm_modes.c', 772, 778, 'CVT_RB_H_BLANK'),
    (DRM + 'drm_modes.c', 806, 806, 'CVT_CLOCK_STEP'),
    (DRM + 'drm_edid.c', 532, 532, '182750'),
    (DRM + 'drm_edid.c', 580, 580, '245500'),
    (DRM + 'drm_edid.c', 628, 628, '317000'),
    (DRM + 'drm_edid.c', 664, 664, '552750'),
    (DRM + 'drm_edid.c', 815, 815, 'edid_cea_modes_1'),
    (DRM + 'drm_edid.c', 1483, 1483, 'edid_cea_modes_193'),
    (DRM + 'drm_edid.c', 7054, 7100, 'type_7 ? pixel_clock : pixel_clock * 10'),
    (DRM + 'drm_displayid_internal.h', 40, 71, 'DATA_BLOCK_2_TYPE_7_DETAILED_TIMING'),
    (DRM + 'drm_displayid_internal.h', 93, 130, 'struct displayid_detailed_timings_1'),
    (DRM + 'drm_displayid.c', 56, 72, 'dispid_length'),
    ('include/drm/display/drm_dp.h', 107, 180, 'DP_MSA_TIMING_PAR_IGNORED'),
    ('include/drm/display/drm_dp.h', 107, 180, 'DP_SUPPORTED_LINK_RATES'),
    ('include/drm/display/drm_dp.h', 244, 244, 'DP_DSC_SUPPORT'),
    ('include/drm/display/drm_dp.h', 590, 598, 'DP_LINK_BW_8_1'),
    ('include/drm/display/drm_dp.h', 590, 598, 'DP_LANE_COUNT_SET'),
    ('include/drm/display/drm_dp.h', 1013, 1013, 'DP_SINK_OUI'),
)

# Tokens the study says are absent from a pinned file (H10).
ABSENT = (
    (DRM + 'display/drm_dp_helper.c', 'CAN_DO_MAX_LINK_RATE_8_1_GBPS'),
    (DP + 'dp_panel.c', 'CAN_DO_MAX_LINK_RATE_8_1_GBPS'),
)

EDID_DECODE_COMMIT = '95ad25f6a77a0a6650f5f657ac2c5046efcd04a0'
EDID_DECODE_FILES = (('edid-decode.cpp', '68cbd25c83b486eb'), ('edid-decode.h', 'f910f4012249452e'),
                     ('parse-base-block.cpp', '59de42561868e97a'), ('parse-cta-block.cpp', 'db44d2730481a616'),
                     ('parse-displayid-block.cpp', 'ffd93405cda81af2'))


def lines_of(path, first, last):
    return '\n'.join(path.read_text(errors='replace').splitlines()[first - 1:last])


def failures(rows, base, text=None):
    """The rows whose token is not in its line range (or whose citation is not in the study when `text` is given)."""
    bad = []
    for row in rows:
        cite, (rel, first, last, token) = (row[0], row[1:]) if len(row) == 5 else (None, row)
        path = base / rel
        if not path.is_file():
            bad.append((row, 'no such file'))
        elif text is not None and cite not in text:
            bad.append((row, 'citation not in the study'))
        elif rel in TEXT_ONLY:
            if token not in path.read_text(errors='replace'):
                bad.append((row, 'text not in the document'))
        elif token not in lines_of(path, first, last):
            bad.append((row, 'token not in the cited lines'))
    return bad


def pinned_files(text):
    return dict(re.findall(r'^\| `([\w./,@-]+)` \| `([0-9a-f]{16})` \|$', text, re.M))


def section(text, name):
    match = re.search(r'<!-- BEGIN %s.*?-->(.*?)<!-- END %s -->' % (name, name), text, re.S)
    return match.group(1) if match else None


class StudyStructure(unittest.TestCase):
    text = DOC.read_text()

    def test_sections_are_present_in_order_with_stable_anchors(self):
        self.assertEqual(re.findall(r'^## (\d+)\. ', self.text, re.M), [str(n) for n in range(1, 12)])
        anchors = re.findall(r'^<a id="(s(\d+)-[a-z0-9]+(?:-[a-z0-9]+)*)"></a>\n## \2\. ', self.text, re.M)
        self.assertEqual([n for _, n in anchors], [str(n) for n in range(1, 12)])
        self.assertEqual(len({a for a, _ in anchors}), 11)

    def test_each_of_the_eight_questions_has_its_section(self):
        headings = re.findall(r'^## (\d+)\. (.*)$', self.text, re.M)
        wanted = {'2': 'public record', '3': 'decode', '4': 'link budget', '5': 'would touch', '6': 'guest side',
                  '7': 'Safety', '8': 'Gate X13', '9': 'X2 dump list'}
        for number, words in wanted.items():
            self.assertIn(words, dict(headings)[number], number)

    def test_markers_that_other_documents_lift_occur_once(self):
        for name in ('LINK-BUDGET-TABLE', 'CORE-CLOCK-ESTIMATE', 'X13A-DUMP-ADDITIONS'):
            self.assertEqual(self.text.count('<!-- BEGIN ' + name), 1, name)
            self.assertEqual(self.text.count('<!-- END ' + name + ' -->'), 1, name)
            self.assertLess(self.text.index('<!-- BEGIN ' + name), self.text.index('<!-- END ' + name + ' -->'))

    def test_status_and_labels(self):
        self.assertIn('Hardware status: NOT RUN', self.text)
        for label in ('VERIFIED', 'OBSERVED', 'INFERRED', 'UNKNOWN', 'MODEL'):
            self.assertIn(label, self.text)

    def test_every_evidence_id_is_a_row_with_a_label(self):
        for prefix, count in (('H', 12), ('T', 8), ('G', 7)):
            for n in range(1, count + 1):
                rows = re.findall(r'^\| %s%d \| (.*)$' % (prefix, n), self.text, re.M)
                self.assertEqual(len(rows), 1, f'{prefix}{n}')
                self.assertRegex(rows[0], r'VERIFIED|OBSERVED|INFERRED|UNKNOWN', f'{prefix}{n}')

    def test_the_gate_and_the_kill_criterion_are_stated(self):
        for needle in ('**X13a**', '**X13b**', '**X13c**', '**X13d**', '**K7 (proposed wording).**',
                       'Only advertised timings.', 'Revert automatically and manually.', 'Authorization row.'):
            self.assertIn(needle, self.text, needle)
        k7 = re.search(r'\*\*K7 \(proposed wording\)\.\*\* \*(.*?)\*\n', self.text, re.S).group(1)
        for needle in ('no 120 Hz', 'does not fit the link the firmware trained', 'cannot be proven safe at', '60 Hz'):
            self.assertIn(needle, ' '.join(k7.split()))

    def test_relative_links_resolve(self):
        for target in re.findall(r'\]\(([^)#:]+\.md)(?:#[^)]*)?\)', self.text):
            self.assertTrue((DOC.parent / target).is_file(), target)

    def test_named_tests_exist(self):
        sources = ''.join((ROOT / 'tools' / name).read_text() for name in ('test-sp11-display-120hz.py', Path(__file__).name))
        for name in set(re.findall(r'`(test_[a-z0-9_]+)`', self.text)):
            self.assertIn('def %s(' % name, sources, name)

    def test_the_pinned_table_and_the_commit(self):
        self.assertIn('7475ea8b58616b33c04afb976cf0fbaad9dd63fb', self.text)
        self.assertEqual(len(pinned_files(self.text)), 28)
        self.assertNotIn('phy-qcom-qmp-combo', self.text)                              # the DP3 PHY is bound by phy-qcom-edp.c
        self.assertNotIn('PIN TASK', self.text)                                        # the PHY offsets are pinned
        for rel, _, _, _ in LINUX:
            self.assertIn(rel, pinned_files(self.text), rel)
        for rel, _ in ABSENT:
            self.assertIn(rel, pinned_files(self.text), rel)
        self.assertIn(EDID_DECODE_COMMIT, self.text)
        for name, prefix in EDID_DECODE_FILES:
            self.assertIn(prefix, self.text, name)


class Tables(unittest.TestCase):
    text = DOC.read_text()

    def test_the_core_clock_estimate_table(self):
        rows = re.findall(r'^\| (\d+) \| (\d+) \| ([0-9.]+) \| (yes|no) \|$', section(self.text, 'CORE-CLOCK-ESTIMATE'), re.M)
        self.assertEqual(len(rows), 4)
        vtotals = {60: lb.cvt_rb_timing(PANEL_WIDTH, 1920, 60).vtotal, 120: lb.cvt_rb_timing(PANEL_WIDTH, 1920, 120).vtotal}
        explicit = lb.explicit_timing(PANEL_WIDTH, 1920, 80, 30, refresh_hz=120).vtotal
        self.assertEqual({(int(hz), int(v)) for hz, v, _, _ in rows},
                         {(60, vtotals[60]), (120, explicit), (120, vtotals[120]), (120, 1920)})
        for hz, vtotal, estimate, above in rows:
            mhz = int(vtotal) * PANEL_WIDTH * int(hz) * CORE_CLOCK_FACTOR / 100 / 1e6
            self.assertEqual(float(estimate), round(mhz, 1), (hz, vtotal))
            self.assertEqual(above, 'yes' if mhz > CORE_CLOCK_TABLE_MAX_MHZ else 'no', (hz, vtotal))

    def test_the_halved_pixel_clock_is_under_the_linux_limit(self):
        clock = lb.cvt_rb_timing(PANEL_WIDTH, 1920, 120).pixel_clock_khz
        self.assertEqual(clock / 2, 370750)
        self.assertLess(clock / 2, 675000)
        self.assertIn('370.75 MHz', self.text)

    def test_the_link_budget_table_has_both_blanking_assumptions_and_both_rates(self):
        body = section(self.text, 'LINK-BUDGET-TABLE')
        rows = re.findall(r'^\| (\d+) \| (cvt-rb-v1|explicit-80x30) \|', body, re.M)
        self.assertGreaterEqual(len(rows), 12)
        self.assertEqual({r for r in rows}, {(hz, kind) for hz in ('60', '120') for kind in ('cvt-rb-v1', 'explicit-80x30')})

    def test_the_numbers_quoted_in_the_prose_come_from_the_model(self):
        t = lb.cvt_rb_timing(PANEL_WIDTH, 1920, 120)
        best = {(lanes, rate): lb.link_budget(t, 8, lanes, rate)['max_bits_per_pixel_that_fits']
                for lanes, rate in ((4, 'HBR3'), (4, 'HBR2'), (2, 'HBR3'), (4, 'HBR'))}
        self.assertEqual(best, {(4, 'HBR3'): 34.9375, (4, 'HBR2'): 23.25, (2, 'HBR3'): 17.4375, (4, 'HBR'): 11.625})
        flat = ' '.join(self.text.split())                                             # the prose wraps lines
        for quoted in ('34.9 on 4 x HBR3', '23.25 on 4 x HBR2', '17.4 on 2 x HBR3', '11.6 on 4 x HBR'):
            self.assertIn(quoted, flat)
        dsc = lb.link_budget(t, 8, 2, 'HBR3', dsc_target_bpp=8)
        self.assertEqual((dsc['fits'], round(dsc['margin_percent'])), (True, 54))
        self.assertIn('54 % margin', flat)
        self.assertEqual(PANEL_WIDTH * 1920 * 120, 663_552_000)
        self.assertIn('663,552,000', flat)


class InTreeCitations(unittest.TestCase):
    def test_every_citation_points_at_what_it_cites(self):
        self.assertEqual(failures(IN_TREE, ROOT, DOC.read_text()), [])

    def test_a_moved_range_is_caught(self):
        ranged = tuple(r for r in IN_TREE if r[1] not in TEXT_ONLY)
        shifted = tuple((c, f, a + 30, b + 30, t) for c, f, a, b, t in ranged)
        self.assertGreaterEqual(len(failures(shifted, ROOT)), len(shifted) - 1)

    def test_a_citation_missing_from_the_study_is_caught(self):
        self.assertTrue(failures(IN_TREE[:1], ROOT, 'nothing cited here'))

    def test_a_missing_token_is_caught(self):
        self.assertTrue(failures((('x', 'compat/dpu-intf.h', 31, 42, 'NOT_A_REAL_TOKEN'),), ROOT, 'x'))
        self.assertTrue(failures((('x', 'docs/STATUS.md', 0, 0, 'NOT A REAL SENTENCE'),), ROOT, 'x'))


@unittest.skipUnless(os.environ.get('SP11_LINUX_TREE'), 'SP11_LINUX_TREE is not set: the Linux citations are not checked')
class LinuxCitations(unittest.TestCase):
    base = Path(os.environ.get('SP11_LINUX_TREE', '.'))

    def test_the_checkout_is_the_pinned_one(self):
        for rel, prefix in pinned_files(DOC.read_text()).items():
            path = self.base / rel
            self.assertTrue(path.is_file(), rel)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest()[:16], prefix, rel)

    def test_every_linux_citation_holds(self):
        self.assertEqual(failures(LINUX, self.base), [])

    def test_the_quirk_the_study_calls_absent_is_absent(self):
        for rel, token in ABSENT:
            self.assertNotIn(token, (self.base / rel).read_text(errors='replace'), rel)

    def test_a_moved_linux_range_is_caught(self):
        shifted = tuple((rel, a + 400, b + 400, t) for rel, a, b, t in LINUX)
        self.assertGreaterEqual(len(failures(shifted, self.base)), len(shifted) * 9 // 10)


@unittest.skipUnless(os.environ.get('SP11_EDID_DECODE_TREE'), 'SP11_EDID_DECODE_TREE is not set: the edid-decode pins are not checked')
class EdidDecodePins(unittest.TestCase):
    def test_the_checkout_is_the_pinned_one(self):
        base = Path(os.environ['SP11_EDID_DECODE_TREE']) / 'utils' / 'edid-decode'
        for name, prefix in EDID_DECODE_FILES:
            self.assertEqual(hashlib.sha256((base / name).read_bytes()).hexdigest()[:16], prefix, name)


class Hygiene(unittest.TestCase):
    def test_spdx_no_machine_strings_and_sizes(self):
        for path in (DOC, Path(__file__)):
            text = path.read_text()
            if path.suffix == '.py':
                self.assertIn('SPDX-License-Identifier: MIT', '\n'.join(text.splitlines()[:2]))
            for prefix in ('/' + 'Users' + '/', '/' + 'home' + '/'):
                self.assertNotIn(prefix, text, path.name)
            self.assertTrue(text.isascii(), path.name)
            self.assertLess(len(text.splitlines()), 1500, path.name)
            self.assertLess(path.stat().st_size, 50 * 1024, path.name)


if __name__ == '__main__':
    unittest.main(verbosity=1)
