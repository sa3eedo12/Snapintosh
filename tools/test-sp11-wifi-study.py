#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Checks that docs/SP11-WIFI-STUDY.md still says what this tree and its pinned sources say.

What always runs (no input needed): the study's sections, status and labels; that every row of every labelled table
carries an evidence label; the evidence ids WE1 to WE59; the pins (commits and the file hashes) and the headline numbers;
K6 and the six X12 sub-gates; the arithmetic of the liftable read-only measurement list; the JSON of the descriptor
proposal; the relative links and anchors; every cross-reference between evidence ids and sections; and every cited
line of THIS repository (a citation must name a range in which a distinguishing token occurs, so a moved or mis-numbered range
fails here instead of misleading a reader). Every `file:line` citation in the study must also be known to a test: a
citation that nothing checks fails. The citations of UniXNU files and of the Ath12kWifi Linux lock (section 11) are checked by
tools/test-sp11-wifi-unixnu.py, which also owns the UniXNU pins, the ten-layer table, the licence statements and Appendix B.

The claims about pinned public sources need inputs that the repository does not carry. Each class is skipped, visibly,
without its variable, and a skip is never a pass:

    SP11_LINUX_TREE=<checkout>          Linux at commit 7475ea8b58616b33c04afb976cf0fbaad9dd63fb (only the files named in
                                        section 1 are needed). The SHA-256 prefixes are compared first, so a checkout at
                                        another commit is refused instead of trusted.
    SP11_WIFI_BOARD_DIR=<dir>           wcn7850-board-2.bin and qcc2072-board-2.bin, copies of
                                        ath12k/{WCN7850/hw2.0,QCC2072/hw1.0}/board-2.bin at linux-firmware commit
                                        69e51bb26f3fa3e118dd770f1962a93bed690dc1. Hashes are compared first.
    SP11_SNAPQCC2072_TREE=<checkout>    SnapQCC2072 at commit 244d5c7f4117bf42c5b1276a23d5b53aecff2895; the Appendix A
                                        census is recomputed and must equal the study's table.

    python3 tools/test-sp11-wifi-study.py
    SP11_LINUX_TREE=<c> SP11_WIFI_BOARD_DIR=<d> SP11_SNAPQCC2072_TREE=<s> python3 tools/test-sp11-wifi-study.py
"""
import hashlib
import importlib.util
import json
import os
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / 'docs' / 'SP11-WIFI-STUDY.md'
spec = importlib.util.spec_from_file_location('sp11_wifi_board', ROOT / 'tools' / 'sp11_wifi_board.py')
wb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wb)

DTS = 'arch/arm64/boot/dts/qcom/'
ATH = 'drivers/net/wireless/ath/ath12k/'
LABELS = ('VERIFIED', 'OBSERVED', 'INFERRED', 'UNKNOWN', 'EXPERIMENTAL', 'UNIMPLEMENTED')
SHARED_DOCS = ('README.md', 'docs/STATUS.md', 'docs/ARCHITECTURE.md')

COMMITS = {
    'linux': '7475ea8b58616b33c04afb976cf0fbaad9dd63fb',
    'linux-firmware': '69e51bb26f3fa3e118dd770f1962a93bed690dc1',
    'snapqcc2072': '244d5c7f4117bf42c5b1276a23d5b53aecff2895',
    'denisix': '049b1caccf153ccf7aa5d0f6b824a4dc48e8b02d',
    'ooaklee': '71524af0602cc35663cd566eb6c7e151aedea995',
    'sp11-el2': '8f1726d49e57de97ac0a349a89ae894206f0fc12',
    'microsoft-learn': '63494b316bdd533f33953b1390c02281572350ef',
}

LINUX_PINS = {
    DTS + 'hamoa.dtsi': 'd5bb1f7ce37c721d',
    DTS + 'x1-microsoft-denali.dtsi': 'afbea2d7b48c1725',
    DTS + 'x1-el2.dtso': 'd6a9666bd89e8787',
    ATH + 'core.c': '67bd3972d3d132c8',
    ATH + 'core.h': 'eedf6b200d56f520',
    ATH + 'fw.c': '6569a96a4292834b',
    ATH + 'hw.h': '27beb16b23bbb933',
    ATH + 'qmi.c': 'badbbe18bb593309',
    ATH + 'qmi.h': '104ebde26a22632f',
    ATH + 'wifi7/hw.c': 'a5b6587e02091374',
    ATH + 'wifi7/pci.c': 'da602c1e7210a710',
    ATH + 'wifi7/hal_qcc2072.c': 'bd5d1bf84229d045',
    ATH + 'wifi7/hal_wcn7850.c': '805d54fec23869ae',
    'Documentation/devicetree/bindings/net/wireless/qcom,ath12k.yaml': '088f21a8e7e68f67',
    'Documentation/devicetree/bindings/regulator/qcom,qca6390-pmu.yaml': 'b8faa2f0713b8444',
    'drivers/power/sequencing/pwrseq-qcom-wcn.c': 'c9256025ea409eb1',
    'drivers/pci/pwrctrl/pci-pwrctrl-pwrseq.c': '7863241c729e9f53',
    'drivers/pci/controller/dwc/pcie-qcom.c': '628ed3acbe38fba0',
    'drivers/pci/controller/dwc/pcie-designware.h': '64ca24764a4fa61a',
    'drivers/pci/quirks.c': '9baee6d8404adab9',
    'drivers/phy/qualcomm/phy-qcom-qmp-pcie.c': 'd073ed062dc4d125',
    'drivers/pinctrl/qcom/pinctrl-x1e80100.c': 'fd70c0b9013800a4',
    'drivers/clk/qcom/gcc-x1e80100.c': 'f1c3779c7bc273df',
    'drivers/phy/qualcomm/phy-qcom-qmp-qserdes-com-v6.h': 'b7b3e2cdfb767f36',
    'drivers/phy/qualcomm/phy-qcom-qmp-qserdes-txrx-v6.h': 'b3058d41c64f7fad',
    'drivers/phy/qualcomm/phy-qcom-qmp-pcs-v6.h': '223333ae32d6f923',
    'drivers/phy/qualcomm/phy-qcom-qmp-pcs-pcie-v6.h': '94c874146205004a',
}

# Citations whose file starts with one of these belong to tools/test-sp11-wifi-unixnu.py (UniXNU files and the four files of the
# Ath12kWifi Linux lock, written lock-7.0.14/ath12k/...).
UNIX_PREFIXES = ('UniXNU/', 'Ath12kWifi/', 'UniXNU-sheng/', 'lock-7.0.14/')

FIRMWARE_PINS = {
    'ath12k/WCN7850/hw2.0/amss.bin': (6090816, '43aadfd3df887f27de74020273aee484bac6a31dd53068f91baf2a9b094d6a68'),
    'ath12k/WCN7850/hw2.0/m3.bin': (299660, '0e72f44df7defc269fe92dcea25d4d409046c04b77d41c510c52879b3dfc1055'),
    'ath12k/WCN7850/hw2.0/board-2.bin': (2431968, '6a98237c743d864773a547e81e23f0c6dc721dcd7331926bee7381e2152e00fd'),
    'ath12k/WCN7850/hw2.0/Notice.txt': (142991, '515bf4c9d620a87458e4447fe01a0e9bc384d1c3e0037cc4c3d2037b1ff25525'),
    'ath12k/WCN7850/hw2.0/ncm865/amss.bin': (6111296, 'ffbcccbdfb19402b734132544d30c4c6dd940e71fe6c530ac11e9c6f494b3477'),
    'ath12k/WCN7850/hw2.0/ncm865/m3.bin': (299660, '0e72f44df7defc269fe92dcea25d4d409046c04b77d41c510c52879b3dfc1055'),
    'ath12k/QCC2072/hw1.0/firmware-2.bin': (7148724, '4c6a1be1f5bfad76319755ff76904abb21c4c7ece5293cc5f33a20b1f4c35254'),
    'ath12k/QCC2072/hw1.0/board-2.bin': (526720, '6880d8e6d51292f6bb5f855965b4c2d1bca3fa0d8a70e68bd693d1e73ff43bf1'),
    'LICENSES/LICENSE.qcom': (13962, 'be904cd28cb292b80cdb6cf412ab0d9159d431671e987ad433c1f62e0988a9bc'),
    'LICENSES/NOTICE.qcom': (23966, 'fa43e1b9a13b341a07adca9dbe73d0f9072d7966fdfe811c01f0dd2872d7309a'),
    'WHENCE': (532286, '3632c15fcdf114586d53fd782f4e8cbe8e526d52b0dff48b1e83c17e34fc3b38'),
}

# The two containers as the study states them (VERIFIED by tools/sp11_wifi_board.py; rechecked by FirmwareContainers).
WCN_REGDB_SHA = 'ada3f31b2e57af3e7a9afd3eab7b26a8067a0132cbad3212e02bb3f1f218c459'
QCC_REGDB_SHA = '9752107372bdefcd2dd11a394459f8eaec0166a3ca1e680f10ad9a243ac2d360'
COMMUNITY_NAME = ('bus=pci,vendor=17cb,device=1107,subsystem-vendor=17cb,subsystem-device=1107,'
                  'qmi-chip-id=2,qmi-board-id=255')
COMMUNITY_DATA_SHA = 'd4f92e9f11bc530d7287f346b96ad3f7e56e6ad6c12f2a0977b267a0ee274a6a'

HEADLINES = (
    '28 top-level elements: 27 board entries and 1 regulatory entry', '69 board names in 27 entries',
    '23 distinct data blocks', '5 top-level elements: 4 board entries and 1 regulatory entry',
    '5 board names in 4 entries', '24,310 bytes', '45 of 63 offsets differ', '18 equal and 45 different',
    '63 offsets', '79 table writes (38 SerDes, 5 TX, 25 RX, 5 PCS, 6 PCS-misc)',
    '14 of the 64 files carry at least one; 50 carry none', '50 of its 64 source files carry no', '11,332 lines',
    'three names sharing an 88,760-byte block', '27 deliberately malformed ones',
    '261 files and 13,312 lines', "The board's 79 PHY initialisation pairs (38 SerDes, 5 TX, 25 RX, 5 PCS, 6 PCS-misc)",
    'of 266 device pages, 34 name Ath12kWifi', '18 of the 34 name it as their selected component',
    '`devices.toml` has 265 device entries, 56 of them SM8550', '`lx_platform` holds 17 callbacks',
    'four board-specific, three chip-generic, two OS-ABI-specific and one', 'Four preconditions for option D',
    '23 Linux files sampled',
)

# The first two header cells of every table of the study, in document order, and the fewest rows each must keep. A table that
# is emptied stops being a table for tables(), so it shows up here as a missing entry instead of passing unnoticed.
TABLE_SHAPE = (
    (('Piece', 'Where'), 4), (('Label', 'Meaning here'), 5), (('File', 'SHA-256 prefix'), 27),
    (('Path under the linux-firmware root', 'Size in bytes'), 11), (('Container', 'Top-level elements'), 2),
    (('Source', 'Pin'), 10), (('Repository', 'File'), 35), (('#', 'Claim'), 7), (('#', 'Claim'), 8), (('#', 'Claim'), 8),
    (('Item', 'Generic to Hamoa (every X1E80100 board)'), 6), (('#', 'Claim'), 5), (('Id', 'Block'), 4),
    (('Id', 'GPIO'), 6), (('Id', 'Block'), 2), (('Id', 'Function'), 9),
    (('Step (Linux order)', 'Operation and controller'), 6), (('Parameter', 'WCN7850 hw2.0'), 16),
    (('Layer', 'A16 piece'), 10), (('A16 pin', 'Equals'), 3), (('Option', 'What it is'), 4), (('File', 'Where'), 8),
    (('#', 'Claim'), 7), (('Package file', 'Linux counterpart'), 6), (('Rule', 'Why'), 6), (('#', 'Claim'), 2),
    (('Operation', 'Why it needs authorization'), 7), (('Sub-gate', 'Goal'), 6), (('Field', 'Value once measured'), 9),
    (('#', 'Question'), 20), (('#', 'Claim'), 6), (('Layer', 'SnapQCC2072 (this project)'), 10), (('#', 'Claim'), 11),
    (('Input (its list)', 'Its SM8550 board'), 12), (('Part', 'Where it states its licence'), 10), (('#', 'Claim'), 5),
    (('File', 'Lines'), 14), (('Group', 'Files'), 8),
)

# (citation as the study writes it, first line, last line, token that must occur in those lines)
REPO = (
    ('compat/qcc2072-wifi.h', 54, 59, 'QCC2072_WIFI_ECAM_PA'),
    ('compat/qcc2072-wifi.h', 54, 64, 'QCC2072_WIFI_CONTROL_PA'),
    ('compat/qcc2072-wifi.h', 111, 111, '0x40100ULL'),
    ('compat/qcc2072-wifi.h', 111, 111, '0x111217cbULL'),
    ('probes/qcc2072wifi.py', 27, 28, 'PBL/EDL'),
    ('probes/qcc2072wifi.py', 61, 61, '0x40100'),
    ('probes/qcc2072wifi.py', 82, 84, "'firmware-2.bin': '4c6a1be1f5bfad76"),
    ('probes/qcc2072wifi.py', 82, 84, "'regdb.bin': '9752107372bdefcd"),
    ('probes/qcc2072wifi.py', 92, 92, 'SMMU = 0x15480000'),
    ('probes/qcc2072wifi.py', 301, 340, 'def walk_smmu'),
    ('probes/qcc2072wifi.py', 343, 372, 'def admit'),
    ('probes/qcc2072wifi.py', 343, 372, 'PBL/EDL'),
    ('probes/qcc2072wifi.py', 394, 399, 'def stop'),
    ('tools/cycle.py', 343, 354, '--qcc2072-wifi'),
    ('tools/cycle.py', 343, 354, '--qcc2072-auxkc'),
    ('config/boards/sp11.json', 188, 192, 'pci17cb,1107'),
    ('config/boards/sp11.json', 188, 192, 'does not imply that any existing driver applies'),
    ('config/boards/a16.json', 32, 33, 'qcc2072-bar0'),
    ('config/boards/a16.json', 32, 33, 'qcc2072-bar2'),
    ('README.md', 42, 42, 'host-proxy harness'),
    ('README.md', 144, 146, 'bdwlan_qcc2072_1p0_ncm820A.elf'),
    ('docs/ARCHITECTURE.md', 203, 203, 'ath12k/QCC2072/hw1.0'),
    ('docs/STATUS.md', 17, 17, 'host-proxy harness'),
    ('docs/STATUS.md', 38, 39, 'cannot join a network yet'),
)

# (short name in the study, path under the Linux checkout) and (short name, first, last, token)
LINUX_PATHS = {
    'hamoa.dtsi': DTS + 'hamoa.dtsi',
    'x1-microsoft-denali.dtsi': DTS + 'x1-microsoft-denali.dtsi',
    'x1-el2.dtso': DTS + 'x1-el2.dtso',
    'ath12k/core.c': ATH + 'core.c', 'ath12k/core.h': ATH + 'core.h', 'ath12k/fw.c': ATH + 'fw.c',
    'ath12k/hw.h': ATH + 'hw.h', 'ath12k/qmi.c': ATH + 'qmi.c', 'ath12k/qmi.h': ATH + 'qmi.h',
    'ath12k/wifi7/hw.c': ATH + 'wifi7/hw.c', 'ath12k/wifi7/pci.c': ATH + 'wifi7/pci.c',
    'ath12k/wifi7/hal_qcc2072.c': ATH + 'wifi7/hal_qcc2072.c', 'ath12k/wifi7/hal_wcn7850.c': ATH + 'wifi7/hal_wcn7850.c',
    'qcom,ath12k.yaml': 'Documentation/devicetree/bindings/net/wireless/qcom,ath12k.yaml',
    'qcom,qca6390-pmu.yaml': 'Documentation/devicetree/bindings/regulator/qcom,qca6390-pmu.yaml',
    'pwrseq-qcom-wcn.c': 'drivers/power/sequencing/pwrseq-qcom-wcn.c',
    'pci-pwrctrl-pwrseq.c': 'drivers/pci/pwrctrl/pci-pwrctrl-pwrseq.c',
    'pcie-qcom.c': 'drivers/pci/controller/dwc/pcie-qcom.c',
    'pcie-designware.h': 'drivers/pci/controller/dwc/pcie-designware.h',
    'quirks.c': 'drivers/pci/quirks.c',
    'phy-qcom-qmp-pcie.c': 'drivers/phy/qualcomm/phy-qcom-qmp-pcie.c',
    'pinctrl-x1e80100.c': 'drivers/pinctrl/qcom/pinctrl-x1e80100.c',
    'gcc-x1e80100.c': 'drivers/clk/qcom/gcc-x1e80100.c',
}
LINUX = (
    ('hamoa.dtsi', 758, 760, 'qcom,x1e80100-gcc'),
    ('hamoa.dtsi', 758, 760, '0x00100000 0 0x200000'),
    ('hamoa.dtsi', 3714, 3728, '0x01c08000 0 0x3000'),
    ('hamoa.dtsi', 3714, 3728, '0x7c000000 0 0xf1d'),
    ('hamoa.dtsi', 3714, 3728, '0x7c100000 0 0x100000'),
    ('hamoa.dtsi', 3714, 3728, '0x01c0b000'),
    ('hamoa.dtsi', 3731, 3732, '0x7c200000'),
    ('hamoa.dtsi', 3731, 3732, '0x7c300000 0x0 0x7c300000 0x0 0x1d00000'),
    ('hamoa.dtsi', 3737, 3738, 'linux,pci-domain = <4>'),
    ('hamoa.dtsi', 3737, 3738, 'num-lanes = <2>'),
    ('hamoa.dtsi', 3740, 3740, 'msi-map = <0x0 &gic_its 0xc0000 0x10000>'),
    ('hamoa.dtsi', 3742, 3750, 'GIC_SPI 141'),
    ('hamoa.dtsi', 3742, 3750, 'GIC_SPI 156'),
    ('hamoa.dtsi', 3763, 3766, 'GIC_SPI 149'),
    ('hamoa.dtsi', 3763, 3766, 'GIC_SPI 152'),
    ('hamoa.dtsi', 3768, 3799, 'GCC_PCIE_4_GDSC'),
    ('hamoa.dtsi', 3768, 3799, 'GCC_PCIE_4_LINK_DOWN_BCR'),
    ('hamoa.dtsi', 3768, 3799, 'required-opps = <&rpmhpd_opp_nom>'),
    ('hamoa.dtsi', 3768, 3799, 'MASTER_PCIE_4'),
    ('hamoa.dtsi', 3805, 3812, 'pcie4_port0: pcie@0'),
    ('hamoa.dtsi', 3805, 3812, 'bus-range = <0x01 0xff>'),
    ('hamoa.dtsi', 3824, 3826, 'qcom,x1e80100-qmp-gen3x2-pcie-phy'),
    ('hamoa.dtsi', 3824, 3826, '0x01c0e000 0 0x2000'),
    ('hamoa.dtsi', 3828, 3849, 'TCSR_PCIE_2L_4_CLKREF_EN'),
    ('hamoa.dtsi', 3828, 3849, 'GCC_PCIE_4_PHY_GDSC'),
    ('hamoa.dtsi', 3828, 3849, 'GCC_PCIE_4_NOCSR_COM_PHY_BCR'),
    ('hamoa.dtsi', 6119, 6121, '0x0f100000'),
    ('hamoa.dtsi', 8544, 8555, 'arm,smmu-v3'),
    ('hamoa.dtsi', 8544, 8555, '0x15400000 0 0x80000'),
    ('hamoa.dtsi', 8544, 8555, 'Controlled by Gunyah'),
    ('hamoa.dtsi', 8589, 8589, 'Reserved by Gunyah'),
    ('x1-el2.dtso', 24, 27, 'controlled by the firmware'),
    ('x1-el2.dtso', 28, 31, 'ITS emulation in Gunyah is broken'),
    ('x1-el2.dtso', 38, 40, 'iommu-map = <0 &pcie_smmu 0x40000 0x10000>'),
    ('x1-el2.dtso', 51, 53, '&pcie_smmu'),
    ('x1-microsoft-denali.dtsi', 291, 305, 'VREG_WCN_3P3'),
    ('x1-microsoft-denali.dtsi', 291, 305, 'gpio = <&tlmm 214 GPIO_ACTIVE_HIGH>'),
    ('x1-microsoft-denali.dtsi', 291, 305, 'regulator-boot-on'),
    ('x1-microsoft-denali.dtsi', 307, 325, 'VREG_WCN_0P95'),
    ('x1-microsoft-denali.dtsi', 307, 325, 'VREG_WCN_1P9'),
    ('x1-microsoft-denali.dtsi', 307, 325, 'vin-supply = <&vreg_wcn_3p3>'),
    ('x1-microsoft-denali.dtsi', 401, 459, 'qcom,wcn7850-pmu'),
    ('x1-microsoft-denali.dtsi', 401, 459, 'wlan-enable-gpios = <&tlmm 117'),
    ('x1-microsoft-denali.dtsi', 401, 459, 'bt-enable-gpios = <&tlmm 116'),
    ('x1-microsoft-denali.dtsi', 401, 459, 'vddio-supply = <&vreg_l15b_1p8>'),
    ('x1-microsoft-denali.dtsi', 401, 459, 'ldo9'),
    ('x1-microsoft-denali.dtsi', 461, 464, 'qcom,pm8550-rpmh-regulators'),
    ('x1-microsoft-denali.dtsi', 461, 464, 'qcom,pmic-id = "b"'),
    ('x1-microsoft-denali.dtsi', 561, 565, 'vreg_l15b_1p8'),
    ('x1-microsoft-denali.dtsi', 987, 989, '&pcie4 {'),
    ('x1-microsoft-denali.dtsi', 987, 989, 'status = "okay"'),
    ('x1-microsoft-denali.dtsi', 991, 996, 'vreg_l3i_0p8'),
    ('x1-microsoft-denali.dtsi', 991, 996, 'vreg_l3e_1p2'),
    ('x1-microsoft-denali.dtsi', 998, 1001, 'compatible = "pci17cb,1107"'),
    ('x1-microsoft-denali.dtsi', 998, 1001, 'reg = <0x10000 0x0 0x0 0x0 0x0>'),
    ('x1-microsoft-denali.dtsi', 998, 1013, 'wifi@0'),
    ('x1-microsoft-denali.dtsi', 1003, 1011, 'vddaon-supply'),
    ('x1-microsoft-denali.dtsi', 1003, 1011, 'vddpcie1p8-supply'),
    ('x1-microsoft-denali.dtsi', 1031, 1034, 'reset-gpios = <&tlmm 152'),
    ('x1-microsoft-denali.dtsi', 1031, 1034, 'wake-gpios = <&tlmm 154'),
    ('x1-microsoft-denali.dtsi', 1227, 1232, 'wcn_wlan_bt_en'),
    ('x1-microsoft-denali.dtsi', 1227, 1232, '"gpio116", "gpio117"'),
    ('x1-microsoft-denali.dtsi', 1234, 1256, 'pcie4_default'),
    ('x1-microsoft-denali.dtsi', 1234, 1256, 'pins = "gpio146"'),
    ('x1-microsoft-denali.dtsi', 1234, 1256, 'pins = "gpio147"'),
    ('x1-microsoft-denali.dtsi', 1234, 1256, 'pins = "gpio148"'),
    ('x1-microsoft-denali.dtsi', 1234, 1256, 'pcie4_clk'),
    ('x1-microsoft-denali.dtsi', 1301, 1306, 'wcn_sw_en'),
    ('x1-microsoft-denali.dtsi', 1301, 1306, 'pins = "gpio214"'),
    ('x1-microsoft-denali.dtsi', 1331, 1346, 'qcom,wcn7850-bt'),
    ('x1-microsoft-denali.dtsi', 1331, 1346, 'vddrfa1p8-supply'),
    ('qcom,ath12k.yaml', 14, 22, 'pci17cb,1107  # WCN7850'),
    ('qcom,ath12k.yaml', 14, 28, 'firmware-name'),
    ('qcom,qca6390-pmu.yaml', 100, 103, 'xo-clk-gpios'),
    ('qcom,qca6390-pmu.yaml', 171, 183, 'qcom,wcn7850-pmu'),
    ('qcom,qca6390-pmu.yaml', 171, 183, '- vddio-supply'),
    ('pwrseq-qcom-wcn.c', 213, 221, 'pwrseq_qcom_wcn_wlan_enable'),
    ('pwrseq-qcom-wcn.c', 289, 293, '.name = "wlan"'),
    ('pwrseq-qcom-wcn.c', 289, 293, '.post_enable = pwrseq_qcom_wcn_pwup_delay'),
    ('pwrseq-qcom-wcn.c', 417, 431, 'pwrseq_wcn7850_vregs'),
    ('pwrseq-qcom-wcn.c', 417, 431, '"vddio1p2"'),
    ('pwrseq-qcom-wcn.c', 427, 431, '.pwup_delay_ms = 50'),
    ('pci-pwrctrl-pwrseq.c', 121, 124, '"pci17cb,1107"'),
    ('pcie-qcom.c', 59, 59, 'PARF_LTSSM'),
    ('pcie-qcom.c', 75, 75, 'PARF_BDF_TO_SID_CFG'),
    ('pcie-qcom.c', 158, 158, 'PARF_LTSSM_STATE_MASK'),
    ('pcie-qcom.c', 175, 175, 'BDF_TO_SID_BYPASS'),
    ('pcie-qcom.c', 356, 361, 'PCIE_T_PVPERL_MS'),
    ('pcie-qcom.c', 1186, 1262, 'qcom_pcie_config_sid_1_9_0'),
    ('pcie-qcom.c', 1522, 1527, 'ops->config_sid'),
    ('pcie-qcom.c', 1660, 1668, '.config_sid = qcom_pcie_config_sid_1_9_0'),
    ('pcie-qcom.c', 1660, 1678, 'ops_1_21_0'),
    ('pcie-qcom.c', 1670, 1678, 'ops_1_21_0'),
    ('pcie-qcom.c', 1728, 1731, 'cfg_sc8280xp'),
    ('pcie-qcom.c', 2614, 2615, '"qcom,pcie-x1e80100", .data = &cfg_sc8280xp'),
    ('pcie-qcom.c', 2614, 2615, '"qcom,pcie-sm8550", .data = &cfg_1_9_0'),
    ('pcie-designware.h', 146, 154, 'PCIE_MSI_ADDR_LO'),
    ('pcie-designware.h', 146, 154, 'PCIE_MSI_INTR0_STATUS'),
    ('phy-qcom-qmp-pcie.c', 2822, 2914, 'sm8550_qmp_gen3x2_pcie_serdes_tbl'),
    ('phy-qcom-qmp-pcie.c', 2822, 2914, 'sm8550_qmp_gen3x2_pcie_pcs_misc_tbl'),
    ('phy-qcom-qmp-pcie.c', 4177, 4186, 'qmp_pcie_offsets_v5'),
    ('pcie-qcom.c', 1480, 1546, 'qcom_pcie_perst_assert'),
    ('pcie-qcom.c', 1480, 1546, 'pci_pwrctrl_power_on_devices'),
    ('pcie-qcom.c', 1480, 1546, 'qcom_pcie_perst_deassert'),
    ('phy-qcom-qmp-pcie.c', 5207, 5232, 'sm8550_qmp_gen3x2_pciephy_cfg'),
    ('phy-qcom-qmp-pcie.c', 6533, 6534, 'qcom,x1e80100-qmp-gen3x2-pcie-phy'),
    ('phy-qcom-qmp-pcie.c', 6533, 6534, 'sm8550_qmp_gen3x2_pciephy_cfg'),
    ('quirks.c', 3820, 3820, 'quirk_no_bus_reset'),
    ('quirks.c', 3820, 3820, 'WCN7850'),
    ('quirks.c', 4281, 4340, 'QUALCOMM_WLAN_PCIE_SOC_GLOBAL_RESET'),
    ('quirks.c', 4281, 4340, 'reset_qualcomm_wlan'),
    ('quirks.c', 4400, 4400, 'reset_qualcomm_wlan'),
    ('pinctrl-x1e80100.c', 12, 46, 'REG_SIZE'),
    ('pinctrl-x1e80100.c', 12, 46, '.oe_bit = 9'),
    ('gcc-x1e80100.c', 3614, 3627, '.halt_reg = 0x6b034'),
    ('gcc-x1e80100.c', 3693, 3705, '.halt_reg = 0x6b01c'),
    ('gcc-x1e80100.c', 6607, 6629, '.gdscr = 0x6b004'),
    ('gcc-x1e80100.c', 6607, 6629, '.gdscr = 0x6c000'),
    ('ath12k/core.c', 73, 104, '"microsoft,denali"'),
    ('ath12k/core.c', 73, 104, 'ATH12K_HW_WCN7850_HW20'),
    ('ath12k/core.c', 73, 104, 'hardware rfkill must stay'),
    ('ath12k/core.c', 256, 296, 'qmi-board-id=%d'),
    ('ath12k/core.c', 256, 296, 'ATH12K_BDF_SEARCH_BUS_AND_BOARD'),
    ('ath12k/core.c', 432, 541, 'ATH12K_BOARD_MAGIC'),
    ('ath12k/core.c', 557, 614, 'ath12k_core_fetch_bdf'),
    ('ath12k/core.c', 557, 614, 'failed to fetch board data for'),
    ('ath12k/core.c', 616, 660, 'ath12k_core_fetch_regdb'),
    ('ath12k/core.c', 616, 660, 'ath12k_core_create_bus_type_board_name'),
    ('ath12k/core.c', 741, 743, 'ATH12K_SMBIOS_BDF_EXT_MAGIC'),
    ('ath12k/core.c', 832, 848, 'qcom,calibration-variant'),
    ('ath12k/core.h', 1343, 1357, 'firmware-name'),
    ('ath12k/fw.c', 158, 170, 'ath12k_fw_map'),
    ('ath12k/hw.h', 91, 100, 'ATH12K_BOARD_API2_FILE'),
    ('ath12k/hw.h', 91, 100, 'ATH12K_REGDB_FILE_NAME'),
    ('ath12k/hw.h', 304, 319, 'ATH12K_BD_IE_REGDB'),
    ('ath12k/qmi.c', 2928, 2976, 'ath12k_qmi_request_target_cap'),
    ('ath12k/qmi.c', 2928, 2976, 'ATH12K_BOARD_ID_DEFAULT'),
    ('ath12k/qmi.c', 3130, 3156, 'ELFMAG'),
    ('ath12k/qmi.c', 3845, 3895, 'ATH12K_QMI_BDF_TYPE_REGDB'),
    ('ath12k/qmi.c', 3845, 3895, 'ath12k_qmi_wlanfw_m3_info_send'),
    ('ath12k/qmi.h', 36, 36, 'ATH12K_BOARD_ID_DEFAULT'),
    ('ath12k/qmi.h', 49, 52, 'ATH12K_QMI_BDF_TYPE_REGDB'),
    ('ath12k/wifi7/pci.c', 21, 22, 'WCN7850_DEVICE_ID'),
    ('ath12k/wifi7/pci.c', 21, 22, 'QCC2072_DEVICE_ID'),
    ('ath12k/wifi7/pci.c', 21, 21, '0x1107'),
    ('ath12k/wifi7/pci.c', 22, 22, '0x1112'),
    ('ath12k/wifi7/pci.c', 25, 29, 'ATH12K_PCI_W7_SOC_HW_VERSION_2'),
    ('ath12k/wifi7/pci.c', 25, 29, 'TCSR_SOC_HW_VERSION'),
    ('ath12k/wifi7/pci.c', 31, 31, '0x310c'),
    ('ath12k/wifi7/pci.c', 32, 32, '0x3278'),
    ('ath12k/wifi7/pci.c', 134, 157, 'ATH12K_HW_WCN7850_HW20'),
    ('ath12k/wifi7/pci.c', 134, 157, 'Unknown hardware version found for WCN7850'),
    ('ath12k/wifi7/pci.c', 138, 138, 'ath12k_wifi7_pci_ops_wcn7850'),
    ('ath12k/wifi7/pci.c', 158, 167, 'there is only one version'),
    ('ath12k/wifi7/pci.c', 162, 162, 'ath12k_wifi7_pci_ops_wcn7850'),
    ('ath12k/wifi7/pci.c', 165, 166, 'ATH12K_HW_QCC2072_HW10'),
    ('ath12k/wifi7/hw.c', 165, 185, 'qcc2072_ops'),
    ('ath12k/wifi7/hw.c', 165, 185, 'ath12k_dp_rxdma_ring_sel_config_qcc2072'),
    ('ath12k/wifi7/hw.c', 453, 457, 'wcn7850 hw2.0'),
    ('ath12k/wifi7/hw.c', 453, 546, 'wcn7850 hw2.0'),
    ('ath12k/wifi7/hw.c', 457, 457, 'WCN7850/hw2.0'),
    ('ath12k/wifi7/hw.c', 461, 461, 'download_aux_ucode = false'),
    ('ath12k/wifi7/hw.c', 466, 466, 'SERVICE_INS_ID_V01_WCN7850'),
    ('ath12k/wifi7/hw.c', 473, 473, 'host_ce_config_wcn7850'),
    ('ath12k/wifi7/hw.c', 505, 505, 'mhi_config_wcn7850'),
    ('ath12k/wifi7/hw.c', 507, 507, 'wmi_init_wcn7850'),
    ('ath12k/wifi7/hw.c', 509, 510, 'CNSS_PCIE_PERST_NO_PULL_V01'),
    ('ath12k/wifi7/hw.c', 512, 512, 'rfkill_pin = 48'),
    ('ath12k/wifi7/hw.c', 528, 528, 'ATH12K_PCIE_MAX_PAYLOAD_SIZE'),
    ('ath12k/wifi7/hw.c', 727, 822, 'qcc2072 hw1.0'),
    ('ath12k/wifi7/hw.c', 731, 731, 'QCC2072/hw1.0'),
    ('ath12k/wifi7/hw.c', 735, 735, 'download_aux_ucode = true'),
    ('ath12k/wifi7/hw.c', 740, 740, 'SERVICE_INS_ID_V01_WCN7850'),
    ('ath12k/wifi7/hw.c', 747, 747, 'host_ce_config_wcn7850'),
    ('ath12k/wifi7/hw.c', 779, 779, 'mhi_config_wcn7850'),
    ('ath12k/wifi7/hw.c', 781, 781, 'wmi_init_wcn7850'),
    ('ath12k/wifi7/hw.c', 783, 785, 'CNSS_AUX_UC_SUPPORT_V01'),
    ('ath12k/wifi7/hw.c', 787, 787, 'rfkill_pin = 0'),
    ('ath12k/wifi7/hw.c', 803, 803, 'iova_mask = 0'),
    ('ath12k/wifi7/hal_qcc2072.c', 9, 97, 'const struct ath12k_hw_regs qcc2072_regs'),
    ('ath12k/wifi7/hal_wcn7850.c', 174, 261, 'const struct ath12k_hw_regs wcn7850_regs'),
)

# (short name, first, last, token) for SnapQCC2072 sources, relative to its checkout.
SNAP = (
    ('src/qcc2072-datapath.c', 143, 143, 'hal_wcn7850'),
    ('src/qcc2072-datapath.c', 183, 183, 'MSI disabled; polled completions'),
    ('src/qcc2072-wlfw.c', 71, 71, 'WCN7850'),
)

# Appendix A as the study prints it: (file, lines, pci ids, window, absolute); and the totals.
CENSUS_TOTAL = (64, 11332, 14, 50)
CENSUS_PCI = re.compile(r'0x111217cb|0xe14f105b|QCC2072_DEVICE_ID|0x1112U?\b|QCC2072_VENDOR_ID|0x17cbU?\b', re.I)
CENSUS_WINDOW = re.compile(r'0x3278|0x310c', re.I)
CENSUS_ABS = re.compile(r'0x(?:a[0-9a-f]{5}|[1-9][0-9a-f]{6}|0[1-9a-f][0-9a-f]{5})\b', re.I)

CITATION = re.compile(r'`([A-Za-z0-9_./,@-]+\.(?:dtsi|dts|dtso|c|h|py|json|yaml|md|cpp|toml)):(\d+)(?:-(\d+))?`')


def lines_of(path, first, last):
    return '\n'.join(path.read_text(errors='replace').splitlines()[first - 1:last])


def cites_in(text):
    """Every `file:first-last` citation of a document, as (file, first, last)."""
    return {(f, int(a), int(b or a)) for f, a, b in CITATION.findall(text)}


def slugify(heading):
    """GitHub's anchor for a heading: lower case, punctuation dropped, spaces to hyphens."""
    heading = heading.strip().lower().replace('`', '')
    return re.sub(r'\s', '-', re.sub(r'[^\w\- ]', '', heading))


def tables(text):
    """[(header cells, [row cells])] for every pipe table in the text."""
    found, block = [], []
    for line in text.splitlines() + ['']:
        if line.startswith('|'):
            block.append(line)
            continue
        if len(block) >= 3 and set(block[1].replace('|', '').replace(' ', '')) <= {'-', ':'}:
            cells = lambda s: [c.strip() for c in s.strip().strip('|').split('|')]
            found.append((cells(block[0]), [cells(r) for r in block[2:]]))
        block = []
    return found


def failures(rows, base, shared=()):
    """The rows whose token is not in its line range (a shared document is checked for the text anywhere)."""
    bad = []
    for rel, first, last, token in rows:
        path = base / rel
        if not path.is_file():
            bad.append(((rel, first, last, token), 'no such file'))
        elif rel in shared:
            if token not in path.read_text(errors='replace'):
                bad.append(((rel, first, last, token), 'text not in the shared document'))
        elif token not in lines_of(path, first, last):
            bad.append(((rel, first, last, token), 'token not in the cited lines'))
    return bad


TEXT = DOC.read_text()


class StudyStructure(unittest.TestCase):
    text = TEXT

    def test_sections_are_present_in_order(self):
        self.assertEqual(re.findall(r'^## (\d+)\. ', self.text, re.M), [str(n) for n in range(1, 13)])
        self.assertEqual(re.findall(r'^### (11\.\d) ', self.text, re.M), [f'11.{n}' for n in range(1, 7)])
        self.assertEqual(re.findall(r'^## Appendix ([AB]): ', self.text, re.M), ['A', 'B'])
        self.assertLess(self.text.index('## 12. How to run the checks'), self.text.index('## Appendix A: '))

    def test_status_and_labels(self):
        self.assertIn('Hardware status: NOT RUN', self.text)
        self.assertIn('UNIMPLEMENTED on\nhardware', self.text)
        self.assertIn('stretch goal', self.text.lower())
        for label in LABELS:
            self.assertIn(label, self.text)

    def test_every_row_of_a_labelled_table_has_a_label_and_a_source(self):
        total = 0
        for header, rows in tables(self.text):
            if 'Label' not in header:
                continue
            col = header.index('Label')
            src = header.index('Source') if 'Source' in header else None
            for row in rows:
                total += 1
                self.assertEqual(len(row), len(header), row)
                self.assertTrue(any(label in row[col] for label in LABELS), f'no label: {row[0]}')
                if src is not None:
                    self.assertTrue(row[src], f'no source: {row[0]}')
        self.assertGreaterEqual(total, 100, 'the labelled tables shrank')

    def test_no_table_is_emptied_and_every_table_keeps_its_rows(self):
        found = [(tuple(header[:2]), len(rows)) for header, rows in tables(self.text)]
        self.assertEqual([shape for shape, _ in found], [shape for shape, _ in TABLE_SHAPE])
        for (shape, rows), (_, least) in zip(found, TABLE_SHAPE):
            self.assertGreaterEqual(rows, least, shape)

    def test_evidence_ids_are_complete_and_unique(self):
        ids = re.findall(r'^\| (WE\d+) \|', self.text, re.M)
        self.assertEqual(ids, [f'WE{n}' for n in range(1, 60)])
        ids = re.findall(r'^\| (W\d+) \|', self.text, re.M)
        self.assertEqual(ids, [f'W{n}' for n in range(1, 21)])

    def test_cross_references_resolve(self):
        defined = set(re.findall(r'^\| (WE\d+) \|', self.text, re.M))
        for ref in re.findall(r'\bWE\d+\b', self.text):
            self.assertIn(ref, defined, f'{ref} is referenced but never defined')
        for ref in re.findall(r'\b(W\d+)\b', self.text):
            self.assertIn(ref, {f'W{n}' for n in range(1, 21)}, ref)
        headings = {m.group(1) for m in re.finditer(r'^#{2,3} (\d+(?:\.\d+)?)[. ]', self.text, re.M)}
        flat = re.sub(r'\s+', ' ', self.text)
        for number in re.findall(r'(?<!plan\'s )(?<!plan )(?<!plan\) )[Ss]ections? (\d+(?:\.\d+)?)', flat):
            self.assertIn(number, headings, f'section {number} does not exist')

    def test_pins(self):
        for name, commit in COMMITS.items():
            self.assertIn(commit, self.text, name)
        linux = {m[0]: m[1] for m in re.findall(r'^\| `([\w./,@-]+)` \| `([0-9a-f]{16})` \|$', self.text, re.M)}
        self.assertEqual(linux, LINUX_PINS)
        firmware = {m[0]: (int(m[1]), m[2]) for m in
                    re.findall(r'^\| `([\w./,@-]+)` \| (\d+) \| `([0-9a-f]{64})` \|$', self.text, re.M)}
        self.assertEqual(firmware, FIRMWARE_PINS)

    def test_headline_numbers(self):
        for phrase in HEADLINES:
            self.assertIn(phrase, self.text)
        for sha in (WCN_REGDB_SHA, QCC_REGDB_SHA, COMMUNITY_DATA_SHA):
            self.assertIn(sha, self.text)

    def test_k6_and_the_gate(self):
        k6 = re.search(r'> \*\*K6\*\*(.*?)\n\n', self.text, re.S)
        self.assertIsNotNone(k6)
        flat = re.sub(r'\s*\n>\s*', ' ', k6.group(0))
        for phrase in ('stretch goal only', 'never blocks X0 to X11', 'X12a', 'separate design study'):
            self.assertIn(phrase, flat)
        self.assertIn('**K6**', self.text)
        gate = [r for h, rows in tables(self.text) if h[:1] == ['Sub-gate'] for r in rows]
        self.assertEqual([r[0] for r in gate], ['X12a', 'X12b', 'X12c', 'X12d', 'X12e', 'X12f'])
        for row in gate:
            self.assertEqual(len(row), 5)
            self.assertTrue(all(len(cell) > 5 for cell in row[1:]), row[0])
        self.assertIn('X12a to X12d need only q1n1 and the host link', self.text)

    def test_authorization_rows(self):
        rows = [r for h, rows in tables(self.text) if h[:1] == ['Operation'] for r in rows]
        self.assertEqual(len(rows), 7)
        for row in rows:
            self.assertEqual(len(row), 4)
            self.assertTrue(all(row))

    def test_liftable_block_is_one_and_its_arithmetic_holds(self):
        self.assertEqual(self.text.count('<!-- BEGIN X2-WIFI-LIST'), 1)
        self.assertEqual(self.text.count('<!-- END X2-WIFI-LIST -->'), 1)
        begin, end = self.text.index('<!-- BEGIN X2-WIFI-LIST'), self.text.index('<!-- END X2-WIFI-LIST -->')
        self.assertLess(begin, end)
        block = self.text[begin:end]
        seen = 0
        for header, rows in tables(block):
            if header[:2] == ['Id', 'Block']:
                for row in rows:
                    base, off, addr = (int(row[i], 16) for i in (2, 3, 4))
                    self.assertEqual(base + off, addr, row[0])
                    seen += 1
            elif header[:2] == ['Id', 'GPIO']:
                for row in rows:
                    gpio, ctl, io = int(row[1]), int(row[3], 16), int(row[4], 16)
                    self.assertEqual(0x0f100000 + gpio * 0x1000, ctl, row[0])
                    self.assertEqual(ctl + 4, io, row[0])
                    seen += 1
        self.assertEqual(seen, 4 + 6 + 2)
        for phrase in ('Read-only', 'PRESENT-LINKED', 'PRESENT-UNLINKED', 'ABSENT', 'FAULT', 'INCONSISTENT'):
            self.assertIn(phrase, block)

    def test_the_survey_reads_the_gates_first_and_never_writes(self):
        begin, end = self.text.index('<!-- BEGIN X2-WIFI-LIST'), self.text.index('<!-- END X2-WIFI-LIST -->')
        flat = re.sub(r'\s*\n\s*', ' ', self.text[begin:end])
        order = [flat.index(f'**{step},') for step in ('S0', 'S1', 'S2', 'S3', 'S4', 'S5')]
        self.assertEqual(order, sorted(order))
        for step in ('S3', 'S4'):
            header = re.search(r'\*\*' + step + r',[^*]*\*\*', flat).group(0)
            self.assertIn('only after G1 to G4 read as expected', header, step)
        for phrase in ('No write to any PCI configuration register', 'never write', 'Dword reads only',
                       'nothing behind a gate is read until the gate reads on',
                       'ABSENT (a gate of G1 to G4 reads the controller off'):
            self.assertIn(phrase, flat)

    def test_descriptor_proposal_is_json_with_every_field_null(self):
        block = re.search(r'```json\n(.*?)\n```', self.text, re.S).group(1)
        got = json.loads('{' + block + '}')['wifi']
        self.assertEqual(sorted(got), sorted(['chip', 'segment', 'endpoint', 'bus_range', 'pci_id', 'bars', 'smmu',
                                              'stream', 'firmware']))
        self.assertTrue(all(v is None for v in got.values()))

    def test_relative_links_and_anchors_resolve(self):
        for target, anchor in re.findall(r'\]\(([^)#:]+\.md)(?:#([^)]*))?\)', self.text):
            path = DOC.parent / target
            self.assertTrue(path.is_file(), target)
            if anchor:
                heads = re.findall(r'^#{1,6} (.*)$', path.read_text(), re.M)
                self.assertIn(anchor, {slugify(h) for h in heads}, f'{target}#{anchor}')
        for anchor in re.findall(r'\]\(#([^)]+)\)', self.text):
            heads = re.findall(r'^#{1,6} (.*)$', self.text, re.M)
            self.assertIn(anchor, {slugify(h) for h in heads}, f'#{anchor}')

    def test_every_citation_is_known_to_this_test(self):
        known = {(f, a, b) for f, a, b, _ in REPO + LINUX}
        known |= {('snap-qcc2072/' + f, a, b) for f, a, b, _ in SNAP}
        in_study = {c for c in cites_in(self.text) if not c[0].startswith(UNIX_PREFIXES)}
        self.assertEqual(in_study ^ known, set())

    def test_negative_claims_are_stated(self):
        for phrase in ('VERIFIED (negative', 'did not open the package', 'UNKNOWN'):
            self.assertIn(phrase, self.text)


class RepositoryCitations(unittest.TestCase):
    def test_every_citation_points_at_what_it_cites(self):
        self.assertEqual(failures(REPO, ROOT, SHARED_DOCS), [])

    def test_a_moved_range_is_caught(self):
        shifted = tuple((f, a + 400, b + 400, t) for f, a, b, t in REPO if f not in SHARED_DOCS)
        self.assertGreaterEqual(len(failures(shifted, ROOT)), len(shifted) - 2)

    def test_a_missing_token_and_a_missing_file_are_caught(self):
        self.assertTrue(failures((('compat/qcc2072-wifi.h', 54, 59, 'NOT_A_REAL_TOKEN'),), ROOT))
        self.assertTrue(failures((('compat/no-such-file.h', 1, 2, 'x'),), ROOT))
        self.assertTrue(failures((('README.md', 1, 1, 'NOT_A_REAL_TOKEN'),), ROOT, SHARED_DOCS))

    def test_the_a16_pins_are_the_public_files_the_study_names(self):
        loader = (ROOT / 'probes' / 'qcc2072wifi.py').read_text()
        pins = dict(re.findall(r"'(firmware-2\.bin|board\.elf|regdb\.bin)': '([0-9a-f]{64})'", loader))
        self.assertEqual(pins['firmware-2.bin'], FIRMWARE_PINS['ath12k/QCC2072/hw1.0/firmware-2.bin'][1])
        self.assertEqual(pins['regdb.bin'], QCC_REGDB_SHA)
        for prefix in (pins['board.elf'][:8], pins['firmware-2.bin'][:8], pins['regdb.bin'][:8]):
            self.assertIn(prefix, TEXT)

    def test_the_a16_and_sp11_facts_the_study_builds_on(self):
        plan = (ROOT / 'docs' / 'SURFACE-PRO-11.md').read_text()
        for token in ('0x15480000', '0x15400000', 'x1-el2.dtso', 'x1-microsoft-denali.dtsi'):
            self.assertIn(token, plan)
        header = (ROOT / 'compat' / 'qcc2072-wifi.h').read_text()
        self.assertIn('0x780100000ULL', header)
        self.assertIn('0xdc000000ULL', header)


@unittest.skipUnless(os.environ.get('SP11_LINUX_TREE'), 'SP11_LINUX_TREE is not set: pinned Linux citations not checked')
class LinuxCitations(unittest.TestCase):
    base = Path(os.environ.get('SP11_LINUX_TREE', '.'))

    def test_the_checkout_is_the_pinned_one(self):
        for rel, prefix in LINUX_PINS.items():
            path = self.base / rel
            self.assertTrue(path.is_file(), rel)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest()[:16], prefix, rel)

    def test_every_linux_citation_holds(self):
        rows = tuple((LINUX_PATHS[short], a, b, t) for short, a, b, t in LINUX)
        self.assertEqual(failures(rows, self.base), [])

    def test_negative_claims_hold(self):
        denali = (self.base / DTS / 'x1-microsoft-denali.dtsi').read_text()
        self.assertEqual(denali.count('pcie4_default'), 1, 'defined and never referenced')
        self.assertNotIn('perst-gpios', denali)
        self.assertEqual(denali.count('wake-gpios'), 1, 'only pcie6a_port0 has one')
        self.assertEqual(lines_of(self.base / DTS / 'x1-microsoft-denali.dtsi', 987, 989).count('='), 1)
        node = lines_of(self.base / DTS / 'x1-microsoft-denali.dtsi', 998, 1013)
        for absent in ('qcom,calibration-variant', 'firmware-name'):
            self.assertNotIn(absent, node)
        yaml = (self.base / 'Documentation/devicetree/bindings/net/wireless/qcom,ath12k.yaml').read_text()
        self.assertNotIn('calibration-variant', yaml)
        quirks = (self.base / 'drivers/pci/quirks.c').read_text()
        self.assertEqual(len(re.findall(r'PCI_VENDOR_ID_QCOM, 0x1112', quirks)), 0)
        self.assertEqual(len(re.findall(r'0x1107, quirk_no_bus_reset', quirks)), 1)

    def test_the_hal_tables_differ_in_45_of_63_offsets(self):
        def table(rel, name):
            body = re.search(r'const struct ath12k_hw_regs ' + name + r' = \{(.*?)\n\};',
                             (self.base / ATH / rel).read_text(), re.S).group(1)
            return dict(re.findall(r'\.(\w+)\s*=\s*([^,\n]+),', body))
        w, q = table('wifi7/hal_wcn7850.c', 'wcn7850_regs'), table('wifi7/hal_qcc2072.c', 'qcc2072_regs')
        self.assertEqual((len(w), len(q), set(w) == set(q)), (63, 63, True))
        self.assertEqual(sum(w[k].strip() == q[k].strip() for k in w), 18)
        self.assertEqual(sum(w[k].strip() != q[k].strip() for k in w), 45)

    def test_the_phy_tables_have_79_writes(self):
        text = (self.base / 'drivers/phy/qualcomm/phy-qcom-qmp-pcie.c').read_text()
        counts = []
        for kind in ('serdes', 'tx', 'rx', 'pcs', 'pcs_misc'):
            body = re.search(r'sm8550_qmp_gen3x2_pcie_' + kind + r'_tbl\[\] = \{(.*?)\n\};', text, re.S).group(1)
            counts.append(body.count('QMP_PHY_INIT_CFG'))
        self.assertEqual(counts, [38, 5, 25, 5, 6])
        self.assertEqual(sum(counts), 79)

    def test_the_stream_configuration_step_belongs_to_the_sm8550_operations_only(self):
        path = self.base / 'drivers/pci/controller/dwc/pcie-qcom.c'
        text = path.read_text()
        self.assertIn('.config_sid', lines_of(path, 1660, 1668))
        self.assertNotIn('config_sid', lines_of(path, 1671, 1678))
        self.assertRegex(text, r'cfg_1_9_0 = \{\s*\.ops = &ops_1_9_0,')
        self.assertRegex(text, r'cfg_sc8280xp = \{\s*\.ops = &ops_1_21_0,')
        self.assertEqual(len(re.findall(r'ops->config_sid\(', text)), 1, 'called once, behind its presence check')

    def test_the_two_hardware_entries_share_what_the_study_says(self):
        text = (self.base / ATH / 'wifi7/hw.c').read_text().splitlines()
        wcn, qcc = '\n'.join(text[452:546]), '\n'.join(text[726:822])
        for shared in ('SERVICE_INS_ID_V01_WCN7850', 'host_ce_config_wcn7850', 'mhi_config_wcn7850', 'wmi_init_wcn7850',
                       'ath12k_wifi7_hw_ring_mask_wcn7850', 'rddm_size = 0x780000', 'acpi_guid = &wcn7850_uuid'):
            self.assertIn(shared, wcn)
            self.assertIn(shared, qcc)
        for differing in ('.dir =', 'download_aux_ucode', '.hw_ops', 'rfkill_pin', 'iova_mask'):
            self.assertNotEqual([l.strip() for l in wcn.splitlines() if differing in l],
                                [l.strip() for l in qcc.splitlines() if differing in l], differing)


@unittest.skipUnless(os.environ.get('SP11_WIFI_BOARD_DIR'), 'SP11_WIFI_BOARD_DIR is not set: board-2.bin facts not checked')
class FirmwareContainers(unittest.TestCase):
    base = Path(os.environ.get('SP11_WIFI_BOARD_DIR', '.'))

    @classmethod
    def setUpClass(cls):
        cls.files = {}
        for name, key in (('wcn7850-board-2.bin', 'ath12k/WCN7850/hw2.0/board-2.bin'),
                          ('qcc2072-board-2.bin', 'ath12k/QCC2072/hw1.0/board-2.bin')):
            data = (cls.base / name).read_bytes()
            cls.files[name] = (key, data)

    def test_the_files_are_the_pinned_ones(self):
        for name, (key, data) in self.files.items():
            size, sha = FIRMWARE_PINS[key]
            self.assertEqual((len(data), hashlib.sha256(data).hexdigest()), (size, sha), name)

    def test_the_wcn7850_container(self):
        entries = wb.parse(self.files['wcn7850-board-2.bin'][1])
        boards = [e for e in entries if e.kind == 'board']
        regdb = [e for e in entries if e.kind == 'regdb']
        self.assertEqual((len(entries), len(boards), len(regdb)), (28, 27, 1))
        self.assertEqual(sum(len(e.names) for e in boards), 69)
        self.assertEqual(len({e.data for e in boards}), 23)
        names = [n for e in boards for n in e.names]
        self.assertEqual(set(re.findall(r'subsystem-vendor=([0-9a-f]+)', ' '.join(names))),
                         {'105b', '17cb', '17aa', '14cd', '1eac', '103c', '1a3b'})
        variants = {n.split('variant=')[1] for n in names if 'variant=' in n}
        self.assertEqual(len(variants), 12)
        self.assertFalse([v for v in variants if re.search('microsoft|surface|denali', v, re.I)])
        self.assertFalse([n for n in names if 'subsystem-vendor=1414' in n])
        self.assertEqual((regdb[0].names, len(regdb[0].data), regdb[0].sha256), (('bus=pci',), 24310, WCN_REGDB_SHA))

    def test_the_community_name_is_an_entry_sharing_a_block_with_two_others(self):
        entries = wb.parse(self.files['wcn7850-board-2.bin'][1])
        entry, name = wb.lookup_board(entries, 0x17cb, 0x1107, 0x17cb, 0x1107, 2, 255)
        self.assertEqual(name, COMMUNITY_NAME)
        self.assertEqual((len(entry.names), len(entry.data), entry.sha256), (3, 88760, COMMUNITY_DATA_SHA))
        self.assertIsNone(wb.lookup_board(entries, 0x17cb, 0x1107, 0x1414, 0x0001, 2, 255)[0])

    def test_the_qcc2072_container_and_the_a16_pin(self):
        entries = wb.parse(self.files['qcc2072-board-2.bin'][1])
        boards = [e for e in entries if e.kind == 'board']
        regdb = [e for e in entries if e.kind == 'regdb']
        self.assertEqual((len(entries), len(boards), len(regdb)), (5, 4, 1))
        self.assertEqual(sum(len(e.names) for e in boards), 5)
        self.assertEqual(regdb[0].sha256, QCC_REGDB_SHA)
        self.assertFalse([n for e in boards for n in e.names if 'subsystem-device=e14f' in n])
        self.assertIsNone(wb.lookup_board(entries, 0x17cb, 0x1112, 0x105b, 0xe14f, 33, 24)[0])
        self.assertIsNotNone(wb.lookup_board(entries, 0x17cb, 0x1112, 0x105b, 0xe15a, 33, 24)[0])


@unittest.skipUnless(os.environ.get('SP11_SNAPQCC2072_TREE'),
                     'SP11_SNAPQCC2072_TREE is not set: Appendix A census and citations not checked')
class SnapCensus(unittest.TestCase):
    base = Path(os.environ.get('SP11_SNAPQCC2072_TREE', '.'))

    def census(self):
        rows = {}
        for path in sorted(self.base.glob('src/**/*')):
            if path.suffix in ('.c', '.h', '.cpp') and path.is_file():
                text = path.read_text(errors='replace')
                rows[path.relative_to(self.base).as_posix()] = (
                    text.count('\n') + 1, len(CENSUS_PCI.findall(text)), len(CENSUS_WINDOW.findall(text)),
                    len(CENSUS_ABS.findall(text)))
        return rows

    def test_the_citations_hold(self):
        self.assertEqual(failures(SNAP, self.base), [])

    def test_the_census_is_the_study_s_table(self):
        rows = self.census()
        carrying = {k: v for k, v in rows.items() if any(v[1:])}
        self.assertEqual((len(rows), sum(v[0] for v in rows.values()), len(carrying), len(rows) - len(carrying)),
                         CENSUS_TOTAL)
        doc = {m[0]: tuple(int(x) for x in m[1:]) for m in
               re.findall(r'^\| `(src/[\w./-]+)` \| (\d+) \| (\d+) \| (\d+) \| (\d+) \|$', TEXT, re.M)}
        self.assertEqual(doc, carrying)


class Hygiene(unittest.TestCase):
    def test_spdx_and_no_machine_strings(self):
        text = Path(__file__).read_text()
        self.assertIn('SPDX-License-Identifier: MIT', '\n'.join(text.splitlines()[:3]))
        for prefix in ('/' + 'Users' + '/', '/' + 'home' + '/'):
            self.assertNotIn(prefix, text)
            self.assertNotIn(prefix, TEXT)
        self.assertTrue(text.isascii())
        self.assertTrue(TEXT.isascii())

    def test_no_secret_or_machine_identifier_shapes_in_the_study(self):
        self.assertIsNone(re.search(r'\b[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}\b', TEXT), 'a MAC address shape')
        self.assertIsNone(re.search(r'\b\d{1,3}(\.\d{1,3}){3}\b', TEXT), 'an IPv4 address shape')

    def test_size_limits(self):
        # An evidence document, not code: the 1,500-line and 50 KiB limits of AGENTS.md are for C and C++ implementation
        # files. The ceiling below only stops runaway growth (the plan itself is about 122 KB).
        self.assertLess(len(TEXT.splitlines()), 1500)
        self.assertLess(len(TEXT.encode()), 112 * 1024)


if __name__ == '__main__':
    unittest.main(verbosity=1)
