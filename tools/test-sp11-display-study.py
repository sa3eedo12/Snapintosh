#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Checks that docs/SP11-DISPLAY-STUDY.md still says what the tree and its pinned sources say.

The A16 audit (section 2) cites file and line ranges of this repository. Each citation must name an existing range in
which a distinguishing token occurs, so a moved or mis-numbered range fails here instead of misleading a reader. The
three shared documents it cites (STATUS, ARCHITECTURE and the plan) are checked for the text only, because other work
edits them and moves their line numbers.

The claims about the pinned Linux sources need that checkout, which the repository does not carry: set SP11_LINUX_TREE
to a Linux tree at the commit named in the study. The SHA-256 prefixes in section 1 are compared first, so a
checkout at another commit is refused instead of trusted. Without the variable those tests are skipped.

    python3 tools/test-sp11-display-study.py
    SP11_LINUX_TREE=<checkout> python3 tools/test-sp11-display-study.py
"""
import hashlib
import os
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / 'docs' / 'SP11-DISPLAY-STUDY.md'
DTS = 'arch/arm64/boot/dts/qcom/'
DPU = 'drivers/gpu/drm/msm/disp/dpu1/'
CLK = 'drivers/clk/qcom/'
SHARED_DOCS = ('docs/STATUS.md', 'docs/ARCHITECTURE.md', 'docs/SURFACE-PRO-11.md')

# (citation as the study writes it, file, first line, last line, a token that must occur in those lines)
A16 = (
    ('compat/dpu-intf.h:20-26', 'compat/dpu-intf.h', 20, 26, 'DPU_MDSS_PA'),
    ('compat/dpu-intf.h:20-26', 'compat/dpu-intf.h', 20, 26, 'DPU_INTF_PA'),
    ('compat/dpu-intf.h:29-47', 'compat/dpu-intf.h', 29, 47, 'DPU_INTF_TIMING_ENGINE_EN'),
    ('compat/dpu-intf.h:29-47', 'compat/dpu-intf.h', 29, 47, 'DPU_INTF_LAST_READ'),
    ('compat/dpu-intf.h:55-58', 'compat/dpu-intf.h', 55, 58, '0x80B0'),
    ('compat/dpu-intf.h:55-58', 'compat/dpu-intf.h', 55, 58, '0x8010'),
    ('compat/edp-backlight.h:30-33', 'compat/edp-backlight.h', 30, 33, '0x0AF6C000'),
    ('compat/edp-backlight.h:30-33', 'compat/edp-backlight.h', 30, 33, '0x80A8'),
    ('compat/edp-backlight.h:35-56', 'compat/edp-backlight.h', 35, 56, 'EDP_AUX_CTRL'),
    ('compat/edp-backlight.h:35-56', 'compat/edp-backlight.h', 35, 56, 'EDP_AUX_MAX_BYTES'),
    ('compat/edp-backlight.c:23-86', 'compat/edp-backlight.c', 23, 86, 'static uint32_t rd('),
    ('compat/edp-backlight.c:23-86', 'compat/edp-backlight.c', 23, 86, 'static int aux('),
    ('compat/edp-backlight.h:58-68', 'compat/edp-backlight.h', 58, 68, 'DPCD_EDP_CONFIGURATION_CAP'),
    ('compat/edp-backlight.h:58-68', 'compat/edp-backlight.h', 58, 68, 'DPCD_EDP_PWMGEN_BITS_MASK'),
    ('compat/edp-backlight.h:70-94', 'compat/edp-backlight.h', 70, 94, 'EDP_BL_FLOOR_PERMILLE'),
    ('compat/edp-backlight.h:70-94', 'compat/edp-backlight.h', 70, 94, 'level 716'),
    ('edp-backlight.h:10', 'compat/edp-backlight.h', 10, 10, 'SDC422C'),
    ('compat/display-timing.h:3-12,22', 'compat/display-timing.h', 3, 12, 'READ-ONLY'),
    ('compat/display-timing.h:3-12,22', 'compat/display-timing.h', 22, 22, 'SNAPDPU1'),
    ('probes/displaytiming.py:21', 'probes/displaytiming.py', 21, 21, 'SNAPDPU1'),
    ('tools/survey-dpu-intf.py:71,82', 'tools/survey-dpu-intf.py', 71, 71, 'width=2880, height=1800'),
    ('tools/survey-dpu-intf.py:71,82', 'tools/survey-dpu-intf.py', 82, 82, 'the A16: 3840'),
    ('probes/fbconsole.py:10-17,40-66', 'probes/fbconsole.py', 10, 17, 'white on black'),
    ('probes/fbconsole.py:10-17,40-66', 'probes/fbconsole.py', 40, 66, 'SHIFT_GRID'),
    ('(line 203)', 'probes/fbconsole.py', 203, 203, '--device'),
    ('probes/bootfb.py:19-23', 'probes/bootfb.py', 19, 23, 'disp0,t8140'),
    ('probes/bootfb.py:19-23', 'probes/bootfb.py', 19, 23, 'KERNEL_SHA256'),
    ('docs/STATUS.md:13', 'docs/STATUS.md', 13, 13, '2880x1800'),
    ('docs/ARCHITECTURE.md:83', 'docs/ARCHITECTURE.md', 83, 83, '2880x1800'),
    ('docs/SURFACE-PRO-11.md:188', 'docs/SURFACE-PRO-11.md', 188, 188, '2880x1800'),
)

# (file under the Linux checkout, first line, last line, token); every range is quoted in the study as it is here.
LINUX = (
    (DTS + 'hamoa.dtsi', 5553, 5556, 'qcom,x1e80100-mdss'),
    (DTS + 'hamoa.dtsi', 5555, 5555, '0x0ae00000 0 0x1000'),
    (DTS + 'hamoa.dtsi', 5589, 5592, 'qcom,x1e80100-dpu'),
    (DTS + 'hamoa.dtsi', 5591, 5591, '0x0ae01000 0 0x8f000'),
    (DTS + 'hamoa.dtsi', 758, 760, 'qcom,x1e80100-gcc'),
    (DTS + 'hamoa.dtsi', 760, 760, '0x00100000 0 0x200000'),
    (DTS + 'hamoa.dtsi', 6022, 6024, 'qcom,x1e80100-dispcc'),
    (DTS + 'hamoa.dtsi', 6024, 6024, '0x0af00000 0 0x20000'),
    (DTS + 'hamoa.dtsi', 5633, 5639, 'mdss_dp3_in'),
    (DTS + 'hamoa.dtsi', 5898, 5904, '0x0aea0000 0 0x200'),
    (DTS + 'hamoa.dtsi', 6000, 6002, '0x0aec5a00'),
    (DTS + 'hamoa.dtsi', 5578, 5578, '0x1c00'),
    (DTS + 'x1e80100-microsoft-denali-oled.dts', 17, 19, 'samsung,atna30dw01'),
    (DPU + 'catalog/dpu_9_2_x1e80100.h', 350, 353, 'intf_5'),
    (DPU + 'catalog/dpu_9_2_x1e80100.h', 351, 351, '0x39000'),
    (DPU + 'catalog/dpu_9_2_x1e80100.h', 353, 353, 'MSM_DP_CONTROLLER_3'),
    (DPU + 'catalog/dpu_9_2_x1e80100.h', 414, 417, 'core_major_ver = 9'),
    (DPU + 'catalog/dpu_9_2_x1e80100.h', 414, 417, 'core_minor_ver = 2'),
    (DPU + 'catalog/dpu_9_2_x1e80100.h', 31, 31, '.base = 0x15000, .len = 0x290'),
    (DPU + 'dpu_hw_ctl.c', 35, 35, 'CTL_INTF_ACTIVE'),
    (DPU + 'dpu_hw_ctl.c', 592, 599, 'CTL_INTF_ACTIVE'),
    (DPU + 'dpu_hw_intf.c', 17, 17, 'INTF_TIMING_ENGINE_EN'),
    (DPU + 'dpu_hw_intf.c', 49, 49, 'INTF_FRAME_COUNT'),
    (DPU + 'dpu_hw_intf.c', 75, 75, 'INTF_STATUS'),
    (DPU + 'dpu_hw_intf.c', 95, 96, 'INTF_CFG_ACTIVE_H_EN'),
    (DPU + 'dpu_hw_intf.c', 98, 98, 'INTF_CFG2_DATABUS_WIDEN'),
    (DPU + 'dpu_hw_intf.c', 167, 167, 'hsync_ctl'),
    (DPU + 'dpu_hw_intf.c', 257, 257, 'INTF_FRAME_LINE_COUNT_EN'),
    (DPU + 'dpu_hw_intf.c', 332, 333, 'INTF_STATUS'),
    (DPU + 'dpu_encoder_phys_vid.c', 117, 123, 'width >> 1'),
    (DPU.replace('/disp/dpu1/', '/') + 'msm_mdss.c', 241, 244, 'MDSS_HW_VER'),
    (DPU.replace('/disp/dpu1/', '/') + 'msm_mdss.c', 283, 283, 'REG_MDSS_HW_VERSION'),
    (CLK + 'dispcc-x1e80100.c', 792, 796, '0x80a8'),
    (CLK + 'dispcc-x1e80100.c', 1332, 1336, '0x800c'),
    (CLK + 'dispcc-x1e80100.c', 1512, 1514, '0x9000'),
    (CLK + 'dispcc-x1e80100.c', 1206, 1209, '0x80a0'),
    (CLK + 'gcc-x1e80100.c', 7444, 7447, '0x27004'),
    (CLK + 'gdsc.c', 21, 21, 'PWR_ON_MASK'),
    (CLK + 'clk-branch.h', 63, 63, 'CBCR_CLK_OFF'),
    ('drivers/gpu/drm/panel/panel-samsung-atna33xc20.c', 308, 308, 'drm_panel_dp_aux_backlight'),
    ('drivers/gpu/drm/panel/panel-samsung-atna33xc20.c', 336, 336, 'samsung,atna33xc20'),
    ('Documentation/devicetree/bindings/display/panel/samsung,atna33xc20.yaml', 22, 23, 'samsung,atna30dw01'),
)


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
        elif rel in SHARED_DOCS:
            if token not in path.read_text(errors='replace'):
                bad.append((row, 'text not in the shared document'))
        elif token not in lines_of(path, first, last):
            bad.append((row, 'token not in the cited lines'))
    return bad


def pinned_files(text):
    rows = re.findall(r'^\| `([\w./,@-]+)` \| `([0-9a-f]{16})` \|$', text, re.M)
    return {(DTS + name if '/' not in name else name): prefix for name, prefix in rows}


class StudyStructure(unittest.TestCase):
    text = DOC.read_text()

    def test_sections_are_present_in_order(self):
        found = re.findall(r'^## (\d+)\. ', self.text, re.M)
        self.assertEqual(found, [str(n) for n in range(1, 11)])

    def test_one_liftable_x2_section(self):
        self.assertEqual(self.text.count('<!-- BEGIN X2-DISPLAY-LIST'), 1)
        self.assertEqual(self.text.count('<!-- END X2-DISPLAY-LIST -->'), 1)
        self.assertLess(self.text.index('<!-- BEGIN X2-DISPLAY-LIST'), self.text.index('<!-- END X2-DISPLAY-LIST -->'))

    def test_status_and_labels(self):
        self.assertIn('Hardware status: NOT RUN', self.text)
        for label in ('VERIFIED', 'OBSERVED', 'INFERRED', 'UNKNOWN'):
            self.assertIn(label, self.text)

    def test_relative_links_resolve(self):
        for target in re.findall(r'\]\(([^)#:]+\.md)(?:#[^)]*)?\)', self.text):
            self.assertTrue((DOC.parent / target).is_file(), target)

    def test_pinned_source_table(self):
        pins = pinned_files(self.text)
        self.assertEqual(len(pins), 18)
        for name in ('hamoa.dtsi', 'x1-microsoft-denali.dtsi', 'x1e80100-microsoft-denali-oled.dts'):
            self.assertIn(DTS + name, pins)


class A16Audit(unittest.TestCase):
    def test_every_citation_points_at_what_it_cites(self):
        self.assertEqual(failures(A16, ROOT, DOC.read_text()), [])

    def test_a_moved_range_is_caught(self):
        shifted = tuple((c, f, a + 30, b + 30, t) for c, f, a, b, t in A16 if f not in SHARED_DOCS)
        self.assertGreaterEqual(len(failures(shifted, ROOT)), len(shifted) - 2)

    def test_a_citation_missing_from_the_study_is_caught(self):
        self.assertTrue(failures(A16[:1], ROOT, 'nothing cited here'))

    def test_a_missing_token_is_caught(self):
        self.assertTrue(failures((('x', 'compat/dpu-intf.h', 20, 26, 'NOT_A_REAL_TOKEN'),), ROOT, 'x'))


@unittest.skipUnless(os.environ.get('SP11_LINUX_TREE'), 'SP11_LINUX_TREE is not set: pinned Linux citations not checked')
class LinuxCitations(unittest.TestCase):
    base = Path(os.environ.get('SP11_LINUX_TREE', '.'))

    def test_the_checkout_is_the_pinned_one(self):
        for rel, prefix in pinned_files(DOC.read_text()).items():
            path = self.base / rel
            self.assertTrue(path.is_file(), rel)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest()[:16], prefix, rel)

    def test_every_linux_citation_holds(self):
        self.assertEqual(failures(LINUX, self.base), [])


class Hygiene(unittest.TestCase):
    def test_spdx_and_no_machine_strings(self):
        text = Path(__file__).read_text()
        self.assertIn('SPDX-License-Identifier: MIT', '\n'.join(text.splitlines()[:2]))
        for prefix in ('/' + 'Users' + '/', '/' + 'home' + '/'):
            self.assertNotIn(prefix, text)
        self.assertTrue(text.isascii())


if __name__ == '__main__':
    unittest.main(verbosity=1)
