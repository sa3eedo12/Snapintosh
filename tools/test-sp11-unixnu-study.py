#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Checks that docs/SP11-UNIXNU-STUDY.md and its evidence table still say what the pinned sources and this tree say.

The study surveys the public UniXNU project as evidence for, and an alternative route to, the Surface Pro 11 plan. It is a
claim-heavy document, so this test makes each claim falsifiable. The study is two files, because one file would pass the
50 KiB limit: docs/SP11-UNIXNU-STUDY.md and docs/SP11-UNIXNU-EVIDENCE.md (its section 3). The text checks read them as one
document, and the checks on the division itself (size, cross links, status lines) read them separately.

  * structure: the ten numbered sections, the anchors the plan text will link to, the status line and the labels;
  * pins: every repository pin is a full commit id and equals the value held here;
  * evidence table: every row has one allowed label and a source that names a pinned key; every evidence id the prose
    mentions exists;
  * citations: every `KEY path:lines` citation the study makes is in the tables below and, when the pinned checkouts are
    available, the cited lines hold a distinguishing token; every citation into this tree points at a range (or, for
    shared documents, a file) that holds its token;
  * headline numbers: the catalog counts the study states are present, and are recomputed from devices.toml when the
    pinned checkout is available;
  * stance: the study states that it does not recommend switching to Route B, and no sentence recommends a switch;
  * plan consistency: the plan documents still hold the decision, kill criterion, evidence and risk ids the study refers to;
  * hygiene: ASCII, no machine paths, SPDX, size, resolvable links, and no long word sequence copied from the pinned
    UniXNU documents.

The pinned checkouts are not part of this repository. Set SP11_UNIXNU_REF to a directory that holds one checkout per
repository named in the study (UniXNU, DarwinBoot, ArmCpu, ...). SP11_LINUX_TREE is a Linux checkout at the plan's pin.
Without them the checks that need them are skipped, and a skip is not a pass.

SP11_UNIXNU_STUDY_DOC and SP11_UNIXNU_EVIDENCE_DOC point the checks at other copies of the two files (used to seed bugs
into copies).

    python3 tools/test-sp11-unixnu-study.py
    SP11_UNIXNU_REF=<dir> SP11_LINUX_TREE=<checkout> python3 tools/test-sp11-unixnu-study.py
"""
import hashlib
import os
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REAL_DOC = ROOT / 'docs' / 'SP11-UNIXNU-STUDY.md'
REAL_LEDGER = ROOT / 'docs' / 'SP11-UNIXNU-EVIDENCE.md'
DOC = Path(os.environ.get('SP11_UNIXNU_STUDY_DOC') or REAL_DOC)
LEDGER = Path(os.environ.get('SP11_UNIXNU_EVIDENCE_DOC') or REAL_LEDGER)
REF = os.environ.get('SP11_UNIXNU_REF')
LINUX = os.environ.get('SP11_LINUX_TREE')

LABELS = ('VERIFIED', 'OBSERVED', 'INFERRED', 'UNKNOWN', 'EXPERIMENTAL', 'UNIMPLEMENTED')
SECTION_COUNT = 10
EVIDENCE_ROWS = 58
SHARED_DOCS_PREFIX = 'docs/'
FILE_BYTES_LIMIT = 50 * 1024
FILE_LINES_LIMIT = 1500

# key: (repository, commit, directory under SP11_UNIXNU_REF or None, text the licence cell must contain)
PINS = {
    'UX': ('UniXNU/UniXNU', 'd830d23b0d19c9322d906c21afeca1d6c54aeffe', 'UniXNU', 'BSD-2-Clause-Patent'),
    'DB': ('UniXNU/DarwinBoot', '8544957e04bdc43e2aac9243764ac16979f15c42', 'DarwinBoot', 'BSD-2-Clause-Patent'),
    'AC': ('UniXNU/ArmCpu', '16040839b17e55e01ab1ec0c2b0f73f431fd472b', 'ArmCpu', 'BSD-2-Clause-Patent'),
    'AD': ('UniXNU/ArmDTPlatform', 'e8bd8d5de395c6cbc00d762c1bef57dbd15a448d', 'ArmDTPlatform', 'BSD-2-Clause-Patent'),
    'AS': ('UniXNU/ArmSmmu4K', '83969cd874503e65997ff7639f7dcf3d0a92cd17', 'ArmSmmu4K', 'MIT'),
    'GF': ('UniXNU/GopFramebuffer', 'd711e8c9de58825c9a758907cb15c409a804a537', 'GopFramebuffer', 'BSD-2-Clause-Patent'),
    'GS': ('UniXNU/QcomGeniSpi', 'eac47baefe6a5d2368a70351e6ad1b6361bedbe2', 'QcomGeniSpi', 'BSD-2-Clause-Patent'),
    'VR': ('UniXNU/VerifiedRoot', '974f043d0c71ba2e60331483a41c6c6d35274ee3', 'VerifiedRoot', 'BSD-2-Clause-Patent'),
    'XP': ('UniXNU/XnuPrelink', '8f4f3866545727e2dc3d2c8cb60f8a87a9539c12', 'XnuPrelink', 'BSD-2-Clause-Patent'),
    'SC': ('UniXNU/UniXNU-sm8550-common', '97665e28578483082b674e8375a8fa65f4cf49cf', 'UniXNU-sm8550-common',
           'BSD-2-Clause-Patent'),
    'UD': ('UniXNU/UniXNU-sheng', '00ba90984e831e4c6ce30ec3d8a56e6434e9f1af', 'UniXNU-sheng', 'BSD-2-Clause-Patent'),
    'DW': ('UniXNU/QcomDwc3', 'e4efe95c903a737e0f5955e5b50a8fb4d2a7d718', 'QcomDwc3', 'GPL-2.0'),
    'SU': ('UniXNU/ShengUfs', 'b11de87c234cf4b8f80a8160fb15e6f37a8e5d0c', 'ShengUfs', 'BSD-2-Clause'),
    'RB': ('UniXNU/RamBlock', '60a54ce8f86161db958fd8e6edf577081b3c9ae7', 'RamBlock', 'BSD-2-Clause-Patent'),
    'NT': ('UniXNU/ShengNovatekTouch', '69fdd6223ed2572aa8bd8351e77c53a238276130', 'ShengNovatekTouch', 'Apache-2.0'),
    'HI': ('UniXNU/ShengHidInput', '114061452959a547aaa08f9ca5aa54033394ca2f', 'ShengHidInput', 'BSD-2-Clause-Patent'),
    'FS': ('CrossDarwin/macOS-ForSheng', 'ca57db73c066162323e77156a9f0f1d72b1de4ff', None, 'GPL-3.0'),
    'DV': ('Droid-VM/macOS-DroidVM', '428a62be9eb5bb209de33c4f2b5168765ed98b33', None, 'none declared'),
    'LX': ('torvalds/linux', '7475ea8b58616b33c04afb976cf0fbaad9dd63fb', None, 'BSD-3-Clause'),
}
LX_OVERLAY = 'arch/arm64/boot/dts/qcom/x1-el2.dtso'
LX_OVERLAY_SHA256_PREFIX = 'd6a9666bd89e8787'

PARITY = 'profiles/os/macos-27.3-26C5035r/patches/xnu-12377/macos-droidvm-k27-parity.patch'
MACOS_PROFILE = 'profiles/os/macos-27.3-26C5035r/profile.toml'

# (key, path, first line, last line, a token that must occur in those lines; None = not mirrored locally)
CITES = (
    ('UX', 'README.md', 24, 24, 'An independent project to run iOS and macOS on non-Apple ARM64 hardware through UEFI'),
    ('UX', 'README.md', 47, 47, 'The macOS 27 profile follows'),
    ('UX', 'README.md', 51, 51, 'BSD-2-Clause-Patent'),
    ('UX', 'devices.toml', 1, 1, 'schema_version = 2'),
    ('UX', 'devices.toml', 2854, 2861, 'graphical Recovery pending'),
    ('UX', 'devices.toml', 3588, 3588, 'Microsoft Surface Duo 2'),
    ('UX', '.gitignore', 31, 31, '*.ipsw'),
    ('UX', '.gitignore', 33, 33, '*.im4p'),
    ('UX', '.gitignore', 36, 36, '*.macho'),
    ('UX', '.gitignore', 46, 46, '*.mbn'),
    ('UX', 'docs/xnu/source-and-patches.md', 4, 4, 'xnu-10063.141.1'),
    ('UX', 'docs/xnu/source-and-patches.md', 95, 97, 'BCM2837'),
    ('UX', 'docs/xnu/source-and-patches.md', 95, 97, 'ARM64_LINK_ADDR'),
    ('UX', 'docs/xnu/four-k-page-tables.md', 84, 84, '16 KiB hash page'),
    ('UX', 'docs/xnu/four-k-page-tables.md', 140, 141, 'non-apple-iommu-bootstrap'),
    ('UX', 'docs/xnu/patch-reference.md', 86, 86, 'native-union-statfs-fsid'),
    ('UX', 'docs/concepts/prelink.md', 3, 3, 'kernel built from source'),
    ('UX', 'docs/concepts/prelink.md', 72, 72, 'kernelcache_sha256'),
    ('UX', 'docs/concepts/boot-chain.md', 73, 73, 'kernel.macho.adt'),
    ('UX', 'docs/concepts/boot-chain.md', 77, 77, 'contiguous physical window'),
    ('UX', 'docs/concepts/boot-chain.md', 79, 82, 'Disable the MMU and caches'),
    ('UX', 'docs/concepts/boot-chain.md', 79, 82, 'Jump to the entry point in EL1'),
    ('UX', 'docs/concepts/security.md', 3, 3, 'keeps the original Apple security components'),
    ('UX', 'docs/concepts/security.md', 20, 20, 'native-trust-cache-runtime'),
    ('UX', 'docs/concepts/security.md', 38, 38, 'amfi-only-platform-code'),
    ('UX', 'profiles/arch/arm64e/patches/xnu/series', 53, 53, 'zzyc-owner-skip-launch-constraints'),
    ('UX', 'docs/porting/arm64e.md', 34, 34, 'HCR_EL2.APK'),
    ('UX', 'docs/porting/arm64e.md', 41, 41, 'In development'),
    ('UX', 'docs/porting/arm64e.md', 59, 59, 'does not need to match'),
    ('UX', 'docs/porting/arm64e.md', 67, 68, 'share this key set'),
    ('UX', 'docs/porting/arm64e.md', 94, 96, 'non-SPTM pmap'),
    ('UX', 'docs/guide/mobilegestalt.md', 3, 3, 'Apple device tree (ADT)'),
    ('UX', 'docs/guide/mobilegestalt.md', 59, 59, 'describes the hardware to the userland'),
    ('UX', 'docs/guide/requirements.md', 95, 95, 'iOS or an iPadOS restore image'),
    ('UX', MACOS_PROFILE, 7, 8, 'xnu-13432.60.57'),
    ('UX', MACOS_PROFILE, 16, 16, 'macosx'),
    ('UX', MACOS_PROFILE, 24, 24, 'Mac17,11'),
    ('UX', MACOS_PROFILE, 51, 51, 'early_security_init'),
    ('UX', MACOS_PROFILE, 92, 99, 'lp-sip1'),
    ('UX', MACOS_PROFILE, 193, 194, 'Sealed: No, no snapshot'),
    ('UX', PARITY, 11, 11, 'max_rt_quantum'),
    ('UX', PARITY, 12, 12, 'timebase-hz=24000000'),
    ('UX', PARITY, 13, 13, '16K boundaries'),
    ('UX', PARITY, 15, 15, 'deny-exec'),
    ('UX', PARITY, 830, 835, 'ns * 3 / 125'),
    ('UX', PARITY, 833, 833, '1.25 times'),
    ('UX', 'docs/porting/power.md', 28, 28, 'AdspProbe.cpp'),
    ('UX', 'docs/porting/power.md', 104, 104, 'CoreAudio assumes a 24 MHz host clock'),
    ('UX', 'docs/porting/uefi-firmware.md', 3, 3, 'Mu-Silicium'),
    ('UX', 'docs/porting/uefi-firmware.md', 84, 84, 'EFI_GRAPHICS_OUTPUT_PROTOCOL'),
    ('UX', 'docs/porting/uefi-firmware.md', 89, 89, 'EFI_ACPI_TABLE_PROTOCOL'),
    ('UX', 'docs/porting/display.md', 26, 27, 'Preserve the active scanout allocation'),
    ('UX', 'docs/porting/display.md', 54, 54, 'vsync=120'),
    ('UX', 'docs/porting/network.md', 111, 111, 'MSI receiver'),
    ('UX', 'docs/porting/usb.md', 4, 4, 'AppleUSBXDCI'),
    ('UX', 'docs/porting/usb.md', 21, 21, 'mainline Linux'),
    ('UX', 'docs/porting/usb.md', 77, 78, 'usb-xdci-24A446.json'),
    ('UX', 'docs/porting/ufs.md', 86, 86, 'Capacity, GPT and root-window bounds'),
    ('UX', 'docs/porting/log-capture.md', 67, 68, 'OneSend'),
    ('UX', 'docs/porting/log-capture.md', 118, 118, 'persistent reader'),
    ('UX', 'docs/porting/records.md', 73, 73, 'device-verified'),
    ('UX', 'docs/porting/migration.md', 334, 340, 'proprietary-files.txt'),
    ('UX', 'docs/porting/device-tree.md', 79, 79, 'One enabled memory node provides'),
    ('UX', 'docs/reference/qemu.md', 17, 17, 'AAVMF'),
    ('UX', 'docs/devices/qemu.md', 7, 7, 'iPadOS 17 home screen'),
    ('UX', 'docs/devices/droidvm.md', 3, 3, 'Gunyah'),
    ('UX', 'docs/devices/droidvm.md', 16, 16, 'lock screen'),
    ('UX', 'docs/devices/zeta.md', 12, 12, 'mounts its APFS root'),
    ('UX', 'docs/devices/sheng-macos.md', 11, 13, 'WindowServer repeatedly exits'),
    ('UX', 'docs/devices/sheng.md', 533, 533, 'the hypervisor keeps those context banks'),
    ('UX', 'docs/devices/oneplus13t.md', 7, 7, 'device-confirmed'),
    ('UX', 'docs/devices/oneplus13t.md', 16, 27, 'device-verified'),
    ('UX', 'docs/devices/oneplus13t.md', 75, 75, 'physical panel refresh'),
    ('UX', 'docs/devices/oneplus13t.md', 114, 114, 'exposed Apple-only'),
    ('UX', 'docs/devices/oneplus13t-shad0w23333.md', 747, 747, 'hypervisor-emulated'),
    ('UX', 'docs/devices/pandora.md', 11, 11, 'iOS 16.7 home screen'),
    ('UX', 'docs/devices/erhai-xingxing19035.md', 223, 223, 'Oryon cores need'),
    ('UX', 'docs/devices/erhai-xingxing19035.md', 297, 297, 'hypervisor region at 0x80000000'),
    ('UX', 'docs/devices/erhai-xingxing19035.md', 319, 319, 'hypervisor region at gunyah'),
    ('UX', 'docs/devices/odin3.md', 117, 117, 'Native architectural timer and PAN'),
    ('UX', 'docs/devices/marble-milacy.md', 96, 96, 'DarwinBoot also detects EL2'),
    ('UX', 'docs/devices/marble-milacy.md', 140, 140, 'UNDEF'),
    ('UX', 'docs/devices/asteroids.md', 64, 64, 'same Qualcomm hypervisor'),
    ('UX', 'docs/devices/asteroids.md', 136, 136, 'SGIs are not delivered'),
    ('UX', 'docs/devices/asteroids.md', 284, 284, 'qcom,virt-smmu'),
    ('UX', 'docs/devices/zizhan.md', 153, 153, 'Under Gunyah'),
    ('UX', 'docs/devices/zorn.md', 436, 436, 'independent Gunyah watchdog'),
    ('UX', 'docs/devices/starlte.md', 114, 114, 'Carbon Hypervisor'),
    ('UX', 'docs/devices/uke-tonghuaqwq.md', 394, 394, 'aborts on DC ZVA'),
    ('DB', 'DarwinBootPkg/Driver/Entry.S', 9, 9, 'EL1 only'),
    ('DB', 'DarwinBootPkg/Driver/DarwinBoot.c', 609, 610, 'DarwinCurrentEl () != 4'),
    ('DB', 'DarwinBootPkg/Driver/DarwinBoot.c', 609, 610, 'unsupported inputs or exception level'),
    ('DB', 'README.md', 7, 7, 'MH_FILESET'),
    ('AC', 'ArmCpu.cpp', 34, 34, 'ICC_SGI1R_EL1 generates nothing'),
    ('AC', 'ArmCpu.cpp', 37, 39, 'under a hypervisor'),
    ('AC', 'ArmCpu.cpp', 97, 97, '1U << 27'),
    ('AC', 'ArmCpu.cpp', 145, 145, 'cntvct_el0'),
    ('AC', 'ArmCpu.cpp', 165, 165, 'Gunyah traps WFI'),
    ('AC', 'ArmCpu.cpp', 527, 527, 'PSCI conduit comes from'),
    ('AS', 'ArmSmmu4KMmio.c', 4, 4, 'SMMUv2'),
    ('AS', 'ArmSmmu4KContext.c', 45, 45, 'stage2 bypass'),
    ('AS', 'test_streams.c', 2, 2, 'no device or active hardware proof'),
    ('AS', 'LICENSE', 1, 1, 'MIT License'),
    ('GF', 'NativeLegacyDisplay.cpp', 99, 103, 'no vblank interrupt'),
    ('GF', 'NativeLegacyDisplay.cpp', 105, 108, 'native_legacy_refresh_hz'),
    ('GF', 'LICENSE', 3, 3, 'BSD-2-Clause-Patent'),
    ('GS', 'README.md', 3, 3, 'Qualcomm GENI SPI controller operations'),
    ('SC', 'dts/sm8550.dtsi', 12, 12, 'timebase-frequency'),
    ('SC', 'dts/sm8550.dtsi', 23, 23, 'method = "smc"'),
    ('SC', 'dts/sm8550.dtsi', 32, 32, 'arm,gic-v3'),
    ('SC', 'dts/sm8550.dtsi', 75, 75, 'arm,mmu-500'),
    ('SC', 'soc.toml', 6, 6, 'sm8550-mainline'),
    ('SC', 'kits/Sm8550Platform/Sm8550GeniUart.c', 16, 16, 'case 4000000'),
    ('UD', 'dts/sheng.dts', 36, 36, 'cont_splash_region'),
    ('UD', 'scripts/gen-qmp-usb3-table.py', 42, 42, 'GPL-2.0-only'),
    ('DW', 'README.md', 9, 9, 'GPL-2.0-only'),
    ('SU', 'LICENSE', 1, 1, 'BSD 2-Clause License'),
    ('NT', 'NOTICE', 3, 4, 'Apache License 2.0'),
    # Fetched through the GitHub API at the pinned commit, not mirrored locally: syntax only.
    ('FS', 'docs/status.md', 10, 16, None),
    ('FS', 'docs/status.md', 20, 20, None),
    ('FS', 'README.md', 5, 5, None),
    ('DV', 'README.md', 14, 14, None),
    # The plan's own Linux pin (SP11_LINUX_TREE).
    ('LX', LX_OVERLAY, 4, 4, 'boot in EL2'),
    ('LX', LX_OVERLAY, 14, 14, 'zap shader in EL2'),
    ('LX', LX_OVERLAY, 24, 32, 'ITS emulation in Gunyah is broken'),
    ('LX', LX_OVERLAY, 63, 66, 'implemented in software in Gunyah'),
)

# Citations into this tree: (path, first line, last line, token). Code files are checked in a +-OWN_SLACK window because
# other work edits them; documents under docs/ are checked for the token anywhere in the file.
OWN_SLACK = 10
OWN_CITES = (
    ('docs/ARCHITECTURE.md', 56, 56, '16 KiB-granule'),
    ('docs/ARCHITECTURE.md', 58, 58, 'Interrupts and timers'),
    ('docs/ARCHITECTURE.md', 59, 59, 'J700 device tree'),
    ('docs/ARCHITECTURE.md', 73, 73, 'stock J700/T8140 kernelcache'),
    ('docs/COMPATIBILITY.md', 14, 20, 'Guest identity'),
    ('docs/COMPATIBILITY.md', 81, 81, 'not full SPTM security enforcement'),
    ('docs/COMPATIBILITY.md', 178, 180, '24 MHz guest domain'),
    ('docs/COMPATIBILITY.md', 190, 192, 'A SEP handshake'),
    ('docs/FULL-INSTALL.md', 47, 47, 'Virtualization.framework'),
    ('docs/KERNEL-PATCHES.md', 15, 15, 'stock kernels unmodified'),
    ('docs/KERNEL-PATCHES.md', 264, 264, 'Pointer authentication'),
    ('docs/KERNEL-PATCHES.md', 279, 282, 'SNAP_KERNEL_NATIVE_COUNTER'),
    ('compat/lean-trap.h', 11, 12, '24 MHz guest on the 19.2 MHz'),
    ('compat/timer-offset-model.h', 7, 10, '24 MHz counter on a 19.2 MHz host'),
)

# Text the plan documents must still hold (checked anywhere in the file: other work moves their lines).
PLAN_TOKENS = (
    ('docs/SURFACE-PRO-11.md', '| D4 |'),
    ('docs/SURFACE-PRO-11.md', 'separate design study'),
    ('docs/SURFACE-PRO-11.md', '**K1** NV2 is absent or functionally unusable'),
    ('docs/SURFACE-PRO-11.md', '**K2** No obtainable'),
    ('docs/SURFACE-PRO-11.md', 'UEFI at EL1 under Gunyah'),
    ('docs/SURFACE-PRO-11.md', "ITS emulation"),
    ('docs/SURFACE-PRO-11.md', '| E36 |'),
    ('docs/SURFACE-PRO-11.md', '| R21 |'),
    ('AGENTS.md', 'Never commit vendor or locally reconstructed private inputs'),
    ('AGENTS.md', 'Honor upstream licenses and attribution'),
    ('AGENTS.md', 'Standalone-First Runtime Design'),
)

REQUIRED_ANCHORS = (
    '1-summary-of-findings', '2-method-labels-and-pinned-sources', '23-pinned-sources', '3-evidence-table',
    '4-architecture-comparison', '5-route-b-feasibility', '55-decision-table-what-each-route-needs-at-x0-to-x3',
    '57-what-the-plan-should-record', '6-reusable-ideas-outside-gpu-and-wi-fi', '7-facts-that-bear-on-plan-unknowns',
    '8-licensing-and-provenance', '9-open-questions-and-what-would-settle-them',
    '10-what-this-study-does-not-establish-and-how-it-is-checked',
)

# (name, regex over the whitespace-normalised text)
HEADLINES = (
    ('records', r'\*\*265\*\* device records: 150 legacy \(format 1\) and 115 format 2'),
    ('os_profile', r'\*\*115\*\* records carry an `os_profile` \(nine distinct iOS or iPadOS profiles and one empty value\)'),
    ('macos profile', r'and \*\*0\*\* name a macOS profile'),
    ('hamoa', r'\*\*0\*\* records name X1E80100, Hamoa, SC8380, a Snapdragon X part or an Oryon v1 part'),
    ('surface', r'only Surface-branded record is the \*\*Surface Duo 2\*\* \(SM8350, an Android phone\)'),
    ('oryon phones', r'\*\*12\*\* SM8750 records, \*\*1\*\* SM8750P record and \*\*9\*\* SM8850 records'),
    ('macos record', r'\*\*1\*\* record names macOS: `sheng-macos`'),
    ('patches', r'73 patches for xnu-12377 and 59 for xnu-10063'),
    ('soc port size', r'81-line DTSI .* plus 911 lines of kit C and headers'),
    ('device port size', r'448 files: `package/` has 33,359 lines \(60 `\.c`, 45 `\.cpp`, 42 `\.h`, 20 `\.mm`\), `scripts/` 37,869 lines '
                         r'and `patches/` 3,022 lines'),
)

STANCE_REQUIRED = (
    'This study does not recommend switching to Route B.',
    'This study recommends that the plan **record** Route B and **not** adopt it.',
    '**not recommended**',
)
PLAN_ID = re.compile(r'\b([EDRC])(\d{1,2})\b')
KILL_ID = re.compile(r'\bK([1-5])\b')

KEYED = re.compile(r'`([A-Z]{2}) ([\w./+-]+):(\d+)(?:-(\d+))?`')
OWN = re.compile(r'`((?:compat|probes|tools|docs|config|shadow)/[\w./+-]+|AGENTS\.md):(\d+)(?:-(\d+))?`')


def squash(text):
    return re.sub(r'\s+', ' ', text)


def slug(heading):
    kept = re.sub(r'[^\w\- ]', '', heading.strip().lower())
    return kept.replace(' ', '-')


def headings(text):
    return re.findall(r'^(#{2,3}) (.+)$', text, re.M)


def section(text, number):
    match = re.search(r'^## %d\. .*?(?=^## \d+\. |\Z)' % number, text, re.M | re.S)
    return match.group(0) if match else ''


def table_rows(block):
    rows = []
    for line in block.splitlines():
        if line.startswith('|') and not re.match(r'^\|[ -]+\|', line):
            rows.append([cell.strip() for cell in line.strip().strip('|').split(' | ')])
    return rows


def parse_pins(text):
    pins = {}
    for cells in table_rows(section(text, 2)):
        if len(cells) == 5 and re.fullmatch(r'[A-Z]{2}', cells[0]):
            sha = re.fullmatch(r'`([0-9a-f]{40})`', cells[2])
            pins[cells[0]] = (cells[1], sha.group(1) if sha else None, cells[3])
    return pins


def parse_evidence(text):
    rows = []
    for cells in table_rows(section(text, 3)):
        if len(cells) == 4 and re.fullmatch(r'U\d\d', cells[0]):
            rows.append(cells)
    return rows


def cites_in(text):
    keyed = {(k, p, int(a), int(b or a)) for k, p, a, b in KEYED.findall(text)}
    own = {(p, int(a), int(b or a)) for p, a, b in OWN.findall(text)}
    return keyed, own


def lines_of(path, first, last):
    return '\n'.join(path.read_text(errors='replace').splitlines()[first - 1:last])


def token_failures(rows, resolve):
    """Rows whose token is not in the cited range of the file `resolve(key)` gives (None = not mirrored: skipped)."""
    bad = []
    for key, rel, first, last, token in rows:
        if token is None:
            continue
        base = resolve(key)
        if base is None:
            continue
        path = base / rel
        if not path.is_file():
            bad.append(((key, rel, first, last), 'no such file'))
        elif token not in lines_of(path, first, last):
            bad.append(((key, rel, first, last), 'token not in the cited lines'))
    return bad


def own_failures(rows, root):
    bad = []
    for rel, first, last, token in rows:
        path = root / rel
        if not path.is_file():
            bad.append(((rel, first, last), 'no such file'))
        elif rel.startswith(SHARED_DOCS_PREFIX) or rel == 'AGENTS.md':
            if token not in path.read_text(errors='replace'):
                bad.append(((rel, first, last), 'text not in the document'))
        elif token not in lines_of(path, max(1, first - OWN_SLACK), last + OWN_SLACK):
            bad.append(((rel, first, last), 'token not in the cited window'))
    return bad


def plan_failures(text, plan_text):
    bad = []
    for kind, number in sorted(set(PLAN_ID.findall(text))):
        if kind in 'EDR' and '| %s%s |' % (kind, number) not in plan_text:
            bad.append('plan id %s%s is not in docs/SURFACE-PRO-11.md' % (kind, number))
        if kind == 'C' and '| C%s |' % number not in plan_text:
            bad.append('plan item C%s is not in docs/SURFACE-PRO-11.md' % number)
    for number in sorted(set(KILL_ID.findall(text))):
        if '**K%s**' % number not in plan_text:
            bad.append('kill criterion K%s is not in docs/SURFACE-PRO-11.md' % number)
    return bad


def stance_failures(text):
    flat = squash(text)
    bad = ['missing stance sentence: %s' % need for need in STANCE_REQUIRED if need not in flat]
    for sentence in re.split(r'(?<=[.!?])\s+', flat):
        if re.search(r'\brecommend', sentence, re.I) and re.search(r'\b(switch|adopt|migrat|replac|move to)', sentence, re.I) \
                and not re.search(r'\bnot\b', sentence, re.I):
            bad.append('a sentence recommends a switch: %s' % sentence[:80])
    return bad


def study_failures(text, plan_text=None):
    """Every structural problem the text-only checks find in the study (empty when it is sound)."""
    bad = []
    flat = squash(text)

    numbers = re.findall(r'^## (\d+)\. ', text, re.M)
    if numbers != [str(n) for n in range(1, SECTION_COUNT + 1)]:
        bad.append('section numbers are %s' % numbers)
    anchors = {slug(title) for _, title in headings(text)}
    bad += ['missing anchor %s' % a for a in REQUIRED_ANCHORS if a not in anchors]
    for target in re.findall(r'\]\(#([^)]+)\)', text):
        if target not in anchors:
            bad.append('dangling in-document link #%s' % target)

    if 'Hardware status: NOT RUN' not in text:
        bad.append('no "Hardware status: NOT RUN" line')
    for label in ('VERIFIED', 'OBSERVED', 'INFERRED', 'UNKNOWN'):
        if label not in text:
            bad.append('label %s never used' % label)

    for name, pattern in HEADLINES:
        if not re.search(pattern, flat):
            bad.append('headline number missing or changed: %s' % name)
    bad += stance_failures(text)

    pins = parse_pins(text)
    if set(pins) != set(PINS):
        bad.append('pin table keys are %s' % sorted(pins))
    for key, (repo, sha, _directory, licence) in PINS.items():
        if key in pins:
            if pins[key][0] != repo:
                bad.append('pin %s names %s, not %s' % (key, pins[key][0], repo))
            if pins[key][1] != sha:
                bad.append('pin %s commit is %s, not %s' % (key, pins[key][1], sha))
            if licence not in pins[key][2]:
                bad.append('pin %s licence cell lacks %s' % (key, licence))

    evidence = parse_evidence(text)
    ids = [row[0] for row in evidence]
    if ids != ['U%02d' % n for n in range(1, EVIDENCE_ROWS + 1)]:
        bad.append('evidence ids are %s' % ids)
    for ident, claim, label, source in evidence:
        if label not in LABELS:
            bad.append('%s has label "%s"' % (ident, label))
        if not claim:
            bad.append('%s has no claim' % ident)
        if not any(re.search(r'\b%s\b' % key, source) for key in PINS):
            bad.append('%s names no pinned source' % ident)
    for ident in sorted(set(re.findall(r'\bU(\d\d)\b', text))):
        if 'U' + ident not in ids:
            bad.append('U%s is mentioned but is not an evidence row' % ident)

    keyed, own = cites_in(text)
    table_keyed = {(k, p, a, b) for k, p, a, b, _t in CITES}
    table_own = {(p, a, b) for p, a, b, _t in OWN_CITES}
    bad += ['citation not in the test table: %s' % (c,) for c in sorted(keyed - table_keyed)]
    bad += ['test-table citation not made by the study: %s' % (c,) for c in sorted(table_keyed - keyed)]
    bad += ['own-tree citation not in the test table: %s' % (c,) for c in sorted(own - table_own)]
    bad += ['test-table own citation not made by the study: %s' % (c,) for c in sorted(table_own - own)]
    bad += ['citation key %s is not pinned' % k for k in sorted({k for k, *_ in keyed} - set(PINS))]

    block = section(text, 5)
    for gate in ('X0', 'X1', 'X2', 'X3'):
        if not re.search(r'^\| %s \| .+ \| .+ \|$' % gate, block, re.M):
            bad.append('decision table has no %s row with both routes' % gate)
    if 'Route A (current plan)' not in block or 'Route B (B1)' not in block:
        bad.append('decision table lost a route column')

    if plan_text is not None:
        bad += plan_failures(text, plan_text)
    for prefix in ('/' + 'Users' + '/', '/' + 'home' + '/'):
        if prefix in text:
            bad.append('machine path %s in the study' % prefix)
    if not text.isascii():
        bad.append('non-ASCII text')
    if len(text.splitlines()) >= 1500:
        bad.append('study is 1,500 lines or longer')
    return bad


def combine(main, ledger):
    """The study with the evidence table put back where its section 3 stands, so the text checks see one document."""
    stub, table = section(main, 3), section(ledger, 3)
    if not stub or not table:
        return main
    return main.replace(stub, table.rstrip('\n') + '\n\n', 1)


def file_failures(name, text):
    """Problems each of the two files must be free of on its own."""
    bad = []
    for prefix in ('/' + 'Users' + '/', '/' + 'home' + '/'):
        if prefix in text:
            bad.append('%s: machine path %s' % (name, prefix))
    if not text.isascii():
        bad.append('%s: non-ASCII text' % name)
    if len(text.splitlines()) >= FILE_LINES_LIMIT:
        bad.append('%s: 1,500 lines or longer' % name)
    if len(text.encode()) > FILE_BYTES_LIMIT:
        bad.append('%s: larger than 50 KiB' % name)
    if 'Hardware status: NOT RUN' not in text:
        bad.append('%s: no "Hardware status: NOT RUN" line' % name)
    return bad


def split_failures(main, ledger):
    """Problems with how the study is divided between its two files."""
    bad = file_failures('study', main) + file_failures('evidence table', ledger)
    if '](SP11-UNIXNU-EVIDENCE.md)' not in main:
        bad.append('the study does not link to the evidence table')
    if '](SP11-UNIXNU-STUDY.md' not in ledger:
        bad.append('the evidence table does not link back to the study')
    for text, other, name in ((main, ledger, 'SP11-UNIXNU-EVIDENCE.md'), (ledger, main, 'SP11-UNIXNU-STUDY.md')):
        anchors = {slug(title) for _, title in headings(other)}
        for target in re.findall(r'\]\(%s#([^)]+)\)' % re.escape(name), text):
            if target not in anchors:
                bad.append('dangling link %s#%s' % (name, target))
    return bad


MAIN_TEXT = DOC.read_text()
LEDGER_TEXT = LEDGER.read_text()
TEXT = combine(MAIN_TEXT, LEDGER_TEXT)
PLAN = (ROOT / 'docs' / 'SURFACE-PRO-11.md').read_text()


def resolver():
    base = Path(REF) if REF else None

    def resolve(key):
        directory = PINS[key][2]
        return base / directory if base and directory and (base / directory).is_dir() else None
    return resolve


def mutate_row(text, ident, cell, value):
    """Replace one cell of one evidence row (cell: 2 = label, 3 = source)."""
    out = []
    for line in text.splitlines(keepends=True):
        if line.startswith('| %s | ' % ident):
            parts = line.rstrip('\n').split(' | ')
            parts[cell] = value if cell != 3 else value + ' |'
            line = ' | '.join(parts) + '\n'
        out.append(line)
    return ''.join(out)


MUTANTS = (
    ('corrupt the UniXNU pin', lambda t: t.replace('d830d23b0d19c9322d906c21afeca1d6c54aeffe',
                                                     'd830d23b0d19c9322d906c21afeca1d6c54aefff', 1)),
    ('drop a pin row', lambda t: re.sub(r'^\| DB \| .*\n', '', t, count=1, flags=re.M)),
    ('give an evidence row an invented label', lambda t: mutate_row(t, 'U04', 2, 'PROVEN')),
    ('blank an evidence row label', lambda t: mutate_row(t, 'U28', 2, '')),
    ('blank an evidence row source', lambda t: mutate_row(t, 'U10', 3, '')),
    ('change a headline number', lambda t: t.replace('**265**', '**264**', 1)),
    ('delete the no-switch sentence', lambda t: re.sub(r'\*\*This study does\s+not\s+recommend switching to Route B\.\*\*', '', t)),
    ('add a recommending sentence', lambda t: t.replace('## 2. Method', 'This study recommends switching to Route B.\n\n'
                                                                       '## 2. Method', 1)),
    ('remove the hardware status line', lambda t: t.replace('Hardware status: NOT RUN', 'Hardware status: ok')),
    ('shift a cited range', lambda t: t.replace('`UX docs/porting/arm64e.md:94-96`', '`UX docs/porting/arm64e.md:94-97`', 1)),
    ('cite an unpinned key', lambda t: t.replace('`AC ArmCpu.cpp:97`', '`ZZ ArmCpu.cpp:97`', 1)),
    ('drop a section heading', lambda t: t.replace('## 8. Licensing and provenance\n', '', 1)),
    ('point at an unknown evidence id', lambda t: t.replace('U28 to U33, U37', 'U98 to U33, U37', 1)),
    ('delete the X2 decision row', lambda t: re.sub(r'^\| X2 \| .*\n', '', t, count=1, flags=re.M)),
    ('break an in-document link', lambda t: t.replace('(#5-route-b-feasibility)', '(#5-route-c-feasibility)', 1)),
    ('cite a plan risk that does not exist', lambda t: t.replace('R19', 'R91', 1)),
    ('leave a machine path in the study', lambda t: t + '\n/' + 'Users' + '/example/scratch\n'),
)

SPLIT_MUTANTS = (
    ('pad the study past 50 KiB', lambda m, e: (m + '\n' + 'padding text ' * 4000, e)),
    ('pad the evidence table past 50 KiB', lambda m, e: (m, e + '\n' + 'padding text ' * 4000)),
    ('cut the study link to the evidence table',
     lambda m, e: (m.replace('](SP11-UNIXNU-EVIDENCE.md)', '](SP11-UNIXNU-NOTES.md)'), e)),
    ('cut the evidence table link back to the study',
     lambda m, e: (m, e.replace('](SP11-UNIXNU-STUDY.md', '](SP11-UNIXNU-OTHER.md'))),
    ('point the evidence table at a missing study anchor',
     lambda m, e: (m, e.replace('SP11-UNIXNU-STUDY.md#23-pinned-sources', 'SP11-UNIXNU-STUDY.md#23-pinned-sourcez'))),
    ('remove the status line from the evidence table', lambda m, e: (m, e.replace('Hardware status: NOT RUN', 'Hardware status: ok'))),
    ('lose the section 3 heading of the evidence table', lambda m, e: (m, e.replace('## 3. Evidence table', '## Evidence table'))),
    ('lose one evidence row', lambda m, e: (m, re.sub(r'^\| U30 \| .*\n', '', e, count=1, flags=re.M))),
)


class StudyChecks(unittest.TestCase):
    def test_the_study_is_sound(self):
        self.assertEqual(study_failures(TEXT, PLAN), [])

    def test_the_two_files_are_divided_soundly(self):
        self.assertEqual(split_failures(MAIN_TEXT, LEDGER_TEXT), [])

    def test_each_seeded_bug_is_caught(self):
        for name, mutate in MUTANTS:
            with self.subTest(name):
                broken = mutate(TEXT)
                self.assertNotEqual(broken, TEXT, 'the mutation did not change the study')
                self.assertTrue(study_failures(broken, PLAN), 'not caught: %s' % name)

    def test_each_seeded_division_bug_is_caught(self):
        for name, mutate in SPLIT_MUTANTS:
            with self.subTest(name):
                main, ledger = mutate(MAIN_TEXT, LEDGER_TEXT)
                self.assertNotEqual((main, ledger), (MAIN_TEXT, LEDGER_TEXT), 'the mutation did not change the files')
                self.assertTrue(split_failures(main, ledger) or study_failures(combine(main, ledger), PLAN),
                                'not caught: %s' % name)

    def test_evidence_labels_cover_the_claim_kinds(self):
        counts = {label: 0 for label in LABELS}
        for ident, _claim, label, _source in parse_evidence(TEXT):
            self.assertIn(label, counts, '%s has an unknown label' % ident)
            counts[label] += 1
        self.assertGreater(counts['VERIFIED'], 10)
        self.assertGreater(counts['OBSERVED'], 10)

    def test_relative_links_resolve(self):
        for text in (MAIN_TEXT, LEDGER_TEXT):
            for target in re.findall(r'\]\(([^)#:]+\.(?:md|py))(?:#[^)]*)?\)', text):
                self.assertTrue((ROOT / 'docs' / target).resolve().is_file(), target)

    def test_the_study_names_each_workstream_it_answers(self):
        for word in ('P1', 'G1', 'B2', 'S2', 'B3', 'R19', 'D4', 'K1', 'K2', 'X0', 'X3'):
            self.assertIn(word, TEXT)


class OwnTreeCitations(unittest.TestCase):
    def test_every_citation_into_this_tree_holds(self):
        self.assertEqual(own_failures(OWN_CITES, ROOT), [])

    def test_a_moved_range_is_caught(self):
        shifted = tuple((p, a + 400, b + 400, t) for p, a, b, t in OWN_CITES if p.startswith('compat/'))
        self.assertEqual(len(own_failures(shifted, ROOT)), len(shifted))

    def test_a_missing_token_is_caught(self):
        self.assertTrue(own_failures((('compat/lean-trap.h', 11, 12, 'NOT_A_REAL_TOKEN'),), ROOT))
        self.assertTrue(own_failures((('docs/ARCHITECTURE.md', 1, 1, 'NOT_A_REAL_TOKEN'),), ROOT))

    def test_the_plan_documents_hold_what_the_study_refers_to(self):
        for rel, token in PLAN_TOKENS:
            self.assertIn(token, (ROOT / rel).read_text(), '%s: %s' % (rel, token))


@unittest.skipUnless(REF, 'SP11_UNIXNU_REF is not set: UniXNU citations, catalog numbers and licences not checked')
class PinnedCheckouts(unittest.TestCase):
    resolve = staticmethod(resolver())

    def test_the_checkouts_are_the_pinned_commits(self):
        for key, (_repo, sha, directory, _licence) in PINS.items():
            if not directory:
                continue
            with self.subTest(key):
                base = self.resolve(key)
                self.assertIsNotNone(base, '%s: no checkout under SP11_UNIXNU_REF' % directory)
                if not (base / '.git').exists():
                    continue
                head = subprocess.run(['git', '-C', str(base), 'rev-parse', 'HEAD'], capture_output=True, text=True,
                                      timeout=60).stdout.strip()
                self.assertEqual(head, sha)

    def test_every_citation_holds_in_the_pinned_files(self):
        self.assertEqual(token_failures(CITES, self.resolve), [])

    def test_a_moved_range_is_caught(self):
        shifted = tuple((k, p, a + 300, b + 300, t) for k, p, a, b, t in CITES if k == 'UX' and t)
        self.assertGreaterEqual(len(token_failures(shifted, self.resolve)), len(shifted) - 6)

    def test_a_missing_token_is_caught(self):
        self.assertTrue(token_failures((('UX', 'README.md', 24, 24, 'NOT_A_REAL_TOKEN'),), self.resolve))

    def test_the_catalog_numbers_are_the_stated_ones(self):
        import tomllib
        base = self.resolve('UX')
        records = tomllib.loads((base / 'devices.toml').read_text())['device']
        self.assertEqual(len(records), 265)
        self.assertEqual(sum(1 for r in records if r['format'] == 1), 150)
        self.assertEqual(sum(1 for r in records if r['format'] == 2), 115)
        profiles = [r['os_profile'] for r in records if 'os_profile' in r]
        self.assertEqual(len(profiles), 115)
        self.assertEqual(len({p for p in profiles if p}), 9)
        self.assertEqual(sum(1 for p in profiles if not p), 1)
        self.assertFalse([p for p in profiles if 'macos' in p.lower()])
        flat = [' '.join(str(v) for v in r.values()) for r in records]
        self.assertFalse([f for f in flat if re.search(r'x1e|x1p|hamoa|sc8380|x elite|snapdragon x\b|oryon', f, re.I)])
        self.assertEqual([r['name'] for r, f in zip(records, flat) if re.search('surface', f, re.I)], ['zeta'])
        self.assertEqual([r['name'] for r, f in zip(records, flat) if re.search('macos', f, re.I)], ['sheng-macos'])
        self.assertEqual({s: sum(1 for r in records if r['soc'] == s) for s in ('SM8750', 'SM8750P', 'SM8850')},
                         {'SM8750': 12, 'SM8750P': 1, 'SM8850': 9})

    def test_the_size_figures_are_the_counted_ones(self):
        def newlines(path):
            return path.read_bytes().count(b'\n')

        def tracked(base, prefix=''):
            out = subprocess.run(['git', '-C', str(base), 'ls-files'], capture_output=True, text=True, timeout=120).stdout
            return [Path(base, p) for p in out.splitlines() if p.startswith(prefix)]

        sheng = self.resolve('UD')
        self.assertEqual(len(tracked(sheng)), 448)
        package = [p for p in tracked(sheng, 'package/') if p.is_file()]
        self.assertEqual(sum(newlines(p) for p in package), 33359)
        self.assertEqual({e: sum(1 for p in package if p.suffix == '.' + e) for e in ('c', 'cpp', 'h', 'mm')},
                         {'c': 60, 'cpp': 45, 'h': 42, 'mm': 20})
        self.assertEqual(sum(newlines(p) for p in tracked(sheng, 'scripts/') if p.is_file()), 37869)
        self.assertEqual(sum(newlines(p) for p in tracked(sheng, 'patches/') if p.is_file()), 3022)
        soc = self.resolve('SC')
        self.assertEqual(newlines(soc / 'dts' / 'sm8550.dtsi'), 81)
        self.assertEqual(sum(newlines(p) for p in (soc / 'kits' / 'Sm8550Platform').iterdir()), 911)
        main = self.resolve('UX')
        self.assertEqual(len(list((main / 'patches' / 'xnu-12377').iterdir())), 73)
        self.assertEqual(len(list((main / 'patches' / 'xnu').iterdir())), 59)

    def test_the_licence_verdicts_hold(self):
        for key, (_repo, _sha, directory, licence) in PINS.items():
            if not directory:
                continue
            with self.subTest(key):
                text = (self.resolve(key) / 'LICENSE').read_text(errors='replace')
                expected = {'AS': 'MIT License', 'SU': 'BSD 2-Clause License', 'NT': 'Apache License',
                            'DW': 'GNU GENERAL PUBLIC LICENSE'}.get(key, 'SPDX-License-Identifier: ' + licence)
                self.assertIn(expected, text)
        shengufs = self.resolve('SU')
        sources = [p for p in shengufs.iterdir() if p.suffix in ('.cpp', '.h', '.c')]
        self.assertTrue(sources)
        for path in sources:
            self.assertIn('SPDX-License-Identifier: BSD-2-Clause-Patent', path.read_text(errors='replace'), path.name)
        self.assertTrue((self.resolve('DW') / 'LICENSES' / 'BSD-2-Clause-Patent.txt').is_file())

    def test_no_long_word_run_is_copied_from_the_pinned_documents(self):
        main = self.resolve('UX')
        # The guides and concept pages, plus every page the study cites (the generated device pages are large).
        sources = [main / 'README.md', main / MACOS_PROFILE, main / PARITY]
        for part in ('concepts', 'xnu', 'porting', 'guide', 'reference', 'drivers'):
            sources += [p for p in (main / 'docs' / part).rglob('*.md')]
        sources += [main / rel for key, rel, _a, _b, _t in CITES if key == 'UX' and rel.startswith('docs/devices/')]

        def words(text):
            return re.findall(r'[a-z0-9]+', text.lower())

        run = 9
        seen = set()
        for path in sources:
            tokens = words(path.read_text(errors='replace'))
            seen.update(' '.join(tokens[i:i + run]) for i in range(len(tokens) - run + 1))
        # Attributed quotations and code spans are not prose of ours: take them out before comparing.
        own = re.sub(r'"[^"\n]*"|`[^`\n]*`', ' ', MAIN_TEXT + '\n' + LEDGER_TEXT)
        tokens = words(own)
        copied = [' '.join(tokens[i:i + run]) for i in range(len(tokens) - run + 1) if ' '.join(tokens[i:i + run]) in seen]
        self.assertEqual(copied[:3], [])


@unittest.skipUnless(LINUX, 'SP11_LINUX_TREE is not set: Linux citations not checked')
class LinuxCitations(unittest.TestCase):
    base = Path(LINUX or '.')

    def test_the_checkout_is_the_pinned_one(self):
        path = self.base / LX_OVERLAY
        self.assertTrue(path.is_file())
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest()[:16], LX_OVERLAY_SHA256_PREFIX)

    def test_every_linux_citation_holds(self):
        rows = tuple(r for r in CITES if r[0] == 'LX')
        self.assertEqual(token_failures(rows, lambda key: self.base), [])


class Hygiene(unittest.TestCase):
    def test_spdx_and_no_machine_strings(self):
        text = Path(__file__).read_text()
        self.assertIn('SPDX-License-Identifier: MIT', '\n'.join(text.splitlines()[:2]))
        for prefix in ('/' + 'Users' + '/', '/' + 'home' + '/'):
            self.assertNotIn(prefix, text)
        self.assertTrue(text.isascii())

    def test_the_pin_table_here_holds_full_commit_ids(self):
        for key, (_repo, sha, _directory, _licence) in PINS.items():
            self.assertRegex(sha, r'^[0-9a-f]{40}$', key)


if __name__ == '__main__':
    unittest.main(verbosity=1)
