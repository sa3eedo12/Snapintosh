#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Checks of section 11 (UniXNU Ath12kWifi), Appendix B and the UniXNU pins of docs/SP11-WIFI-STUDY.md.

It reuses the helpers and the document text of tools/test-sp11-wifi-study.py, which stays the home of the checks of the rest of
the study; every `file:line` citation whose file is a UniXNU file or a file of the Ath12kWifi Linux lock belongs to this test
(the other one ignores them, and this one requires them to be exactly the table below).

What always runs: the UniXNU commits and the table of pinned files in section 1; that the study's UniXNU citations are exactly
the ones below; the ten-layer table and its classes; the licence statements; the sentence that the study does not recommend
replacing SnapQCC2072 and the four preconditions of option D; and Appendix B's arithmetic.

What needs an input (skipped, visibly, without it; a skip is never a pass; a set but incomplete input fails):

    SP11_UNIXNU_REF=<dir>    Subdirectories UniXNU, Ath12kWifi and UniXNU-sheng (read-only checkouts at the commits of section 1),
                             linux-v7.0.14 (the four files of the Ath12kWifi lock listed in section 1, at their paths under the
                             Linux root) and wpa_supplicant-2.12 (COPYING and README of the archive). Files are read, never
                             executed, and git is never run in the checkouts.
    SP11_LINUX_TREE=<dir>    Also needed by the PHY-table comparison only (see tools/test-sp11-wifi-study.py).

    python3 tools/test-sp11-wifi-unixnu.py
    SP11_UNIXNU_REF=<u> SP11_LINUX_TREE=<c> python3 tools/test-sp11-wifi-unixnu.py
"""
import hashlib
import importlib.util
import json
import os
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('sp11_wifi_study', ROOT / 'tools' / 'test-sp11-wifi-study.py')
study = importlib.util.module_from_spec(spec)
spec.loader.exec_module(study)
TEXT, tables, cites_in, failures, lines_of = study.TEXT, study.tables, study.cites_in, study.failures, study.lines_of
ATH, FIRMWARE_PINS, UNIX_PREFIXES = study.ATH, study.FIRMWARE_PINS, study.UNIX_PREFIXES

COMMITS = {
    'unixnu': 'd830d23b0d19c9322d906c21afeca1d6c54aeffe',
    'ath12kwifi': '48e23bf15fc21bbd30d5583b42b9c09ad65537db',
    'ath12kwifi-catalog-pin': '28de7b91d7a880682b777e0e3564245a99dbc3f5',
    'unixnu-sheng': '00ba90984e831e4c6ce30ec3d8a56e6434e9f1af',
    'ath12kwifi-linux-lock': '458c6079fc1d41d564c37679c8ace02cd83ee817',
    'ath12kwifi-freebsd-lock': '95719d8a895bf8170851e9844d9b4092b1d2b6fe',
    'ath12kwifi-supplicant-archive': '08e23937e16d0155e55cab2b51f51fbe10d80a1aa91c4e15442645059b737ef6',
}

# The table of section 1: (repository cell, file) -> first 16 hex digits of its SHA-256.
UNIX_PINS = {
    ('UniXNU', 'LICENSE'): '5b1d3d911103cb0f',
    ('UniXNU', 'AGENTS.md'): 'ea11e40ead1e83cb',
    ('UniXNU', 'README.md'): '37c34314132525af',
    ('UniXNU', 'components.toml'): 'af721e73ce192367',
    ('UniXNU', 'devices.toml'): 'c9027a64d2e4672a',
    ('UniXNU', 'docs/drivers/ath12k-wifi.md'): 'd244345074872669',
    ('UniXNU', 'docs/porting/network.md'): 'c37984b970b23034',
    ('UniXNU', 'docs/devices/sheng.md'): 'ba183f462e8670d7',
    ('UniXNU', 'docs/devices/fuxi.md'): '5a2e95db8a81f72e',
    ('UniXNU', 'docs/devices/nx769j.md'): 'a6d0fb43a490e4f1',
    ('UniXNU', 'docs/devices/venus.md'): 'c049e0a17d02dc74',
    ('UniXNU', 'profiles/os/macos-27.3-26C5035r/profile.toml'): '66ffded31e80cf9d',
    ('Ath12kWifi', 'README.md'): '48f152e94a43c644',
    ('Ath12kWifi', 'AGENTS.md'): '9bdd1f18868114e3',
    ('Ath12kWifi', '.gitignore'): '55e7205a3da609e0',
    ('Ath12kWifi', 'include/nw_abi.h'): '508f5e8f70cb7fc4',
    ('Ath12kWifi', 'src/nw_pci.c'): 'db549a0909d25e10',
    ('Ath12kWifi', 'tools/build-supplicant.py'): '53c8ada0293dd4aa',
    ('Ath12kWifi', 'tools/source-lock.json'): 'f23882695f1c136c',
    ('Ath12kWifi', 'shim/include/linux/math.h'): '0a1a58e364b3c8c2',
    ('UniXNU-sheng', 'LICENSE'): '5b1d3d911103cb0f',
    ('UniXNU-sheng', 'AGENTS.md'): '8e95e5e049ee4705',
    ('UniXNU-sheng', 'package/WifiProbe.cpp'): '72f46e8229865eff',
    ('UniXNU-sheng', 'package/WifiNet.cpp'): 'a4738ae766ded21d',
    ('UniXNU-sheng', 'package/WifiPcieTables.h'): 'f21a8bda7c217cd2',
    ('UniXNU-sheng', 'package/WifiFirmware.h'): 'dccf8dbb118bfaae',
    ('UniXNU-sheng', 'firmware/wcn7850/manifest.json'): 'f474f2a5a157bc59',
    ('UniXNU-sheng', 'reports/ipados27-wifi-mainline-migration.md'): '3337d97384940695',
    ('UniXNU-sheng', 'reports/ipados27-wifi-association.md'): '866928739fec703c',
    ('Linux v7.0.14', ATH + 'core.c'): 'a2a0cd02e05ff3b8',
    ('Linux v7.0.14', ATH + 'wifi7/hal_wcn7850.c'): 'a5cbf2f7e535eb7f',
    ('Linux v7.0.14', ATH + 'wifi7/hal_qcc2072.c'): '02f02cf20fecdb5c',
    ('Linux v7.0.14', ATH + 'wifi7/hal_qcn9274.c'): '3ada2d8cdcd7ecbe',
    ('wpa_supplicant 2.12', 'COPYING'): 'a87ac4e333d0f120',
    ('wpa_supplicant 2.12', 'README'): '83be1b142c59ccf0',
}
UNIX_DIRS = {'UniXNU': 'UniXNU', 'Ath12kWifi': 'Ath12kWifi', 'UniXNU-sheng': 'UniXNU-sheng',
             'Linux v7.0.14': 'linux-v7.0.14', 'wpa_supplicant 2.12': 'wpa_supplicant-2.12'}
LOCK_PREFIX = 'lock-7.0.14/ath12k/'

# (citation as the study writes it, first, last, token that must occur in those lines), relative to SP11_UNIXNU_REF.
UNIX = (
    ('UniXNU/README.md', 24, 24, 'independent project'),
    ('UniXNU/README.md', 24, 24, 'run iOS and macOS on non-Apple ARM64 hardware'),
    ('UniXNU/components.toml', 474, 493, 'id = "Ath12kWifi"'),
    ('UniXNU/components.toml', 474, 493, 'qcc2072, qcn9274, and wcn7850 build inputs'),
    ('UniXNU/components.toml', 474, 493, 'nw_abi platform interface'),
    ('UniXNU/components.toml', 474, 493, 'PCI configuration, BAR, MSI, and DMA operations'),
    ('UniXNU/components.toml', 474, 493, 'build_stage = "embedded"'),
    ('UniXNU/components.toml', 487, 487, 'verified_devices = []'),
    ('UniXNU/devices.toml', 3, 15, 'os_profile = "ipados-27.0.1-24A446"'),
    ('UniXNU/docs/drivers/ath12k-wifi.md', 3, 13, 'wpa_supplicant 2.12'),
    ('UniXNU/docs/drivers/ath12k-wifi.md', 3, 13, 'Open and WPA2-PSK with PMF'),
    ('UniXNU/docs/drivers/ath12k-wifi.md', 3, 13, 'Ordinary network packets stay in the kernel'),
    ('UniXNU/docs/drivers/ath12k-wifi.md', 48, 51, 'ath12k/WCN7850/hw2.0'),
    ('UniXNU/docs/drivers/ath12k-wifi.md', 53, 57, '540 initialized bytes'),
    ('UniXNU/docs/drivers/ath12k-wifi.md', 82, 91, '287 tracked allocations'),
    ('UniXNU/docs/drivers/ath12k-wifi.md', 82, 91, '125 objects and 13 XNU imports'),
    ('UniXNU/docs/porting/network.md', 26, 29, 'QCC2072'),
    ('UniXNU/docs/porting/network.md', 31, 51, 'WLAN enable, PERST'),
    ('UniXNU/docs/porting/network.md', 46, 51, 'stage 4 inspects the endpoint'),
    ('UniXNU/docs/porting/network.md', 53, 62, 'native_ufs_extra'),
    ('UniXNU/docs/porting/network.md', 59, 61, 'native_ufs_part'),
    ('UniXNU/docs/porting/network.md', 80, 85, 'device MAC identity'),
    ('UniXNU/docs/porting/network.md', 87, 96, 'Rebuild the kernel, payload and boot image'),
    ('UniXNU/docs/porting/network.md', 109, 109, 'six PMU rails'),
    ('UniXNU/docs/porting/network.md', 110, 110, 'Gen2 x1'),
    ('UniXNU/docs/porting/network.md', 113, 113, '59 passive channels'),
    ('UniXNU/docs/devices/sheng.md', 267, 267, 'WiFi endpoint'),
    ('UniXNU/docs/devices/sheng.md', 272, 272, '128 MiB'),
    ('UniXNU/docs/devices/sheng.md', 457, 495, 'Device verification covers Wi-Fi toggling'),
    ('UniXNU/docs/devices/sheng.md', 457, 495, 'Current-image device checks pending: cancellation/timeout completion'),
    ('UniXNU/docs/devices/sheng.md', 480, 480, 'Device verification covers'),
    ('UniXNU/docs/devices/fuxi.md', 36, 40, 'ipados-26.5.2-23F84'),
    ('UniXNU/docs/devices/fuxi.md', 469, 469, 'under 1 percent transmit loss'),
    ('UniXNU/docs/devices/nx769j.md', 490, 490, 'which is not the ipados-17.7.11-21H461 ABI'),
    ('UniXNU/docs/devices/venus.md', 558, 558, 'not evidence for the Mi 11 endpoint'),
    ('UniXNU/profiles/os/macos-27.3-26C5035r/profile.toml', 1, 18, '"name" = "macos-27.3-26C5035r"'),
    ('UniXNU/profiles/os/macos-27.3-26C5035r/profile.toml', 1, 18, 'the macOS KPIs'),
    ('UniXNU/profiles/os/macos-27.3-26C5035r/profile.toml', 1, 18, 'xnu-13432.60.57'),
    ('UniXNU/profiles/os/macos-27.3-26C5035r/profile.toml', 1, 18, '"target" = "macosx"'),
    ('Ath12kWifi/README.md', 3, 5, 'Board callbacks provide power, PCIe'),
    ('Ath12kWifi/README.md', 13, 19, 'pins Linux v7.0.14'),
    ('Ath12kWifi/README.md', 13, 19, '`deps/` and `out/`'),
    ('Ath12kWifi/README.md', 13, 19, 'the retired `nw_sta.c`'),
    ('Ath12kWifi/README.md', 83, 84, 'The consuming kernel build compiles the production manifest'),
    ('Ath12kWifi/README.md', 95, 97, '287 tracked allocations'),
    ('Ath12kWifi/include/nw_abi.h', 9, 35, 'struct lx_platform'),
    ('Ath12kWifi/include/nw_abi.h', 9, 35, 'struct nw_board'),
    ('Ath12kWifi/include/nw_abi.h', 9, 35, 'dma_map'),
    ('Ath12kWifi/include/nw_abi.h', 46, 89, 'nw_wifi_connect'),
    ('Ath12kWifi/include/nw_abi.h', 46, 89, 'struct nw_wifi_events'),
    ('Ath12kWifi/shim/include/linux/math.h', 1, 1, 'SPDX-License-Identifier: GPL-2.0'),
    ('Ath12kWifi/src/nw_pci.c', 53, 64, 'g_dev.vendor = 0x17cb'),
    ('Ath12kWifi/src/nw_pci.c', 53, 64, 'dma_mask = 0xffffffffull'),
    ('Ath12kWifi/src/nw_pci.c', 53, 64, 'smallest (128 bytes)'),
    ('Ath12kWifi/tools/build-supplicant.py', 13, 13, '"macos"'),
    ('Ath12kWifi/tools/build-supplicant.py', 29, 29, 'arm64e-apple-macos27.0'),
    ('Ath12kWifi/tools/build-supplicant.py', 46, 46, '"hardware_verified": False'),
    ('Ath12kWifi/tools/source-lock.json', 3, 37, '"ref": "v7.0.14"'),
    ('Ath12kWifi/tools/source-lock.json', 3, 37, COMMITS['ath12kwifi-linux-lock']),
    ('Ath12kWifi/tools/source-lock.json', 3, 37, COMMITS['ath12kwifi-supplicant-archive']),
    ('Ath12kWifi/tools/source-lock.json', 56, 59, COMMITS['ath12kwifi-freebsd-lock']),
    ('Ath12kWifi/tools/source-lock.json', 56, 59, '"ref": "releng/15.1"'),
    ('UniXNU-sheng/firmware/wcn7850/manifest.json', 1, 17, FIRMWARE_PINS['ath12k/WCN7850/hw2.0/amss.bin'][1]),
    ('UniXNU-sheng/firmware/wcn7850/manifest.json', 1, 17, 'Matches installed sheng WFW1 firmware'),
    ('UniXNU-sheng/package/WifiFirmware.h', 6, 8, 'firmware/ath12k/WCN7850/hw2.0'),
    ('UniXNU-sheng/package/WifiNet.cpp', 2, 5, 'nw_abi.h'),
    ('UniXNU-sheng/package/WifiNet.cpp', 40, 50, 'XNU_TARGET_OS_OSX'),
    ('UniXNU-sheng/package/WifiNet.cpp', 40, 50, 'IOEthernetController'),
    ('UniXNU-sheng/package/WifiPcieTables.h', 1, 37, 'constexpr Init kSerdes[]'),
    ('UniXNU-sheng/package/WifiProbe.cpp', 1, 1, 'SPDX-License-Identifier: BSD-2-Clause-Patent'),
    ('UniXNU-sheng/package/WifiProbe.cpp', 2, 12, 'read-only look at what the boot loaders left'),
    ('UniXNU-sheng/package/WifiProbe.cpp', 7, 8, 'PERST# GPIO 94'),
    ('UniXNU-sheng/package/WifiProbe.cpp', 9, 12, 'stalls the bus'),
    ('UniXNU-sheng/package/WifiProbe.cpp', 147, 152, 'PMK8550 gpio3'),
    ('UniXNU-sheng/package/WifiProbe.cpp', 265, 268, 'sleep clock pin'),
    ('UniXNU-sheng/package/WifiProbe.cpp', 296, 301, 'constexpr Rail kRails[]'),
    ('UniXNU-sheng/package/WifiProbe.cpp', 362, 366, '"SN7"'),
    ('UniXNU-sheng/package/WifiProbe.cpp', 440, 441, 'carved in order'),
    ('UniXNU-sheng/package/WifiProbe.cpp', 644, 647, 'aligned to their size rounded up to a power of two'),
    ('UniXNU-sheng/package/WifiProbe.cpp', 951, 966, 'unique-chip-id'),
    ('UniXNU-sheng/package/WifiProbe.cpp', 977, 990, 'local-mac-address'),
    ('UniXNU-sheng/package/WifiProbe.cpp', 1063, 1068, 'kMsiSpi = 141'),
    ('UniXNU-sheng/package/WifiProbe.cpp', 1087, 1091, 'armcpu_spi_attach'),
    ('UniXNU-sheng/package/WifiProbe.cpp', 1407, 1409, 'Stage 4: forward the window to bus 1'),
    ('UniXNU-sheng/package/WifiProbe.cpp', 1430, 1443, 'c[0x10 / 4] = 0xffffffffu;'),
    ('UniXNU-sheng/package/WifiProbe.cpp', 1477, 1484, 'WLAN_EN goes low first'),
    ('UniXNU-sheng/reports/ipados27-wifi-mainline-migration.md', 90, 98,
     'Fastboot has accepted the image for temporary boot'),
    (LOCK_PREFIX + 'core.c', 1, 1, 'BSD-3-Clause-Clear'),
    (LOCK_PREFIX + 'core.c', 72, 94, 'ath12k_acpi_get_disable_rfkill'),
    (LOCK_PREFIX + 'wifi7/hal_wcn7850.c', 1, 1, 'BSD-3-Clause-Clear'),
    (LOCK_PREFIX + 'wifi7/hal_qcc2072.c', 1, 1, 'BSD-3-Clause-Clear'),
    (LOCK_PREFIX + 'wifi7/hal_qcn9274.c', 1, 1, 'BSD-3-Clause-Clear'),
)

# The ten-layer table of section 11.2: the class of the UniXNU piece, by the first words of its cell.
LAYER_CLASSES = ('board-specific', 'board-specific', 'board-specific', 'board-specific', 'chip-data', 'chip-generic',
                 'chip-generic', 'chip-generic', 'OS-ABI-specific', 'OS-ABI-specific')

# Appendix B as the study prints it: group -> (files, lines), and the board-layer line counts it states.
ATH12K_CENSUS = {'(root)': (3, 144), 'include': (3, 235), 'shim': (149, 2924), 'src': (35, 4353), 'tests': (18, 2804),
                 'tools': (43, 1803), 'userspace': (10, 1049)}
ATH12K_SPDX_FILES = ('shim/include/linux/math.h', 'tools/pin-headers.sh', 'tools/prepare-cfg80211-events.py',
                     'tools/prepare-cfg80211-native.py')
BOARD_LINES = {'package/WifiProbe.cpp': 1734, 'package/WifiNet.cpp': 872, 'package/WifiPcieTables.h': 37}


def unix_target(name):
    """Where a citation of the UniXNU section lives under SP11_UNIXNU_REF."""
    if name.startswith(LOCK_PREFIX):
        return 'linux-v7.0.14/' + ATH + name[len(LOCK_PREFIX):]
    return name


def git_head(checkout):
    """The commit that HEAD of a checkout names, read from its .git files without running git (None without .git)."""
    git = checkout / '.git'
    if not (git / 'HEAD').is_file():
        return None
    head = (git / 'HEAD').read_text().strip()
    if not head.startswith('ref: '):
        return head
    ref = head[5:]
    if (git / ref).is_file():
        return (git / ref).read_text().strip()
    packed = git / 'packed-refs'
    if packed.is_file():
        for line in packed.read_text().splitlines():
            if line.endswith(' ' + ref):
                return line.split()[0]
    return None


class UniXNUSection(unittest.TestCase):
    text = TEXT

    def test_this_file_has_spdx_is_ascii_and_names_no_machine(self):
        source = Path(__file__).read_text()
        self.assertIn('SPDX-License-Identifier: MIT', '\n'.join(source.splitlines()[:3]))
        self.assertTrue(source.isascii())
        for prefix in ('/' + 'Users' + '/', '/' + 'home' + '/'):
            self.assertNotIn(prefix, source)

    def test_the_commits_are_stated(self):
        for name, commit in COMMITS.items():
            self.assertIn(commit, self.text, name)

    def test_the_table_of_section_1_is_the_pinned_set(self):
        rows = re.findall(r'^\| (UniXNU|Ath12kWifi|UniXNU-sheng|Linux v7\.0\.14|wpa_supplicant 2\.12) \| `([^`]+)` \| '
                          r'`([0-9a-f]{16})` \|$', self.text, re.M)
        self.assertEqual({(repo, rel): prefix for repo, rel, prefix in rows}, UNIX_PINS)
        self.assertEqual(len(rows), len(UNIX_PINS), 'a row is repeated')

    def test_the_studys_unixnu_citations_are_exactly_the_checked_ones(self):
        in_study = {c for c in cites_in(self.text) if c[0].startswith(UNIX_PREFIXES)}
        self.assertEqual(in_study ^ {(f, a, b) for f, a, b, _ in UNIX}, set())

    def test_the_layer_table_has_ten_layers_with_the_stated_classes(self):
        found = [rows for header, rows in tables(self.text) if header[:2] == ['Layer', 'SnapQCC2072 (this project)']]
        self.assertEqual(len(found), 1)
        rows = found[0]
        self.assertEqual([r[0].split('.')[0] for r in rows], [str(n) for n in range(1, 11)])
        classes = []
        for row in rows:
            self.assertEqual(len(row), 6, row[0])
            match = re.match(r'(board-specific|chip-data|chip-generic|OS-ABI-specific)\b', row[3])
            self.assertIsNotNone(match, row[0])
            classes.append(match.group(1))
        self.assertEqual(classes, list(LAYER_CLASSES))
        for word, count in (('board-specific', 4), ('chip-generic', 3), ('OS-ABI-specific', 2), ('chip-data', 1)):
            self.assertEqual(classes.count(word), count, word)

    def test_the_licence_statements_and_the_rule_they_meet(self):
        section = self.text[self.text.index('### 11.4 '):self.text.index('### 11.5 ')]
        rows = [r for header, rs in tables(section) if header[:2] == ['Part', 'Where it states its licence'] for r in rs]
        self.assertEqual(len(rows), 10)
        glue = [r for r in rows if r[0].startswith('Ath12kWifi glue')]
        self.assertEqual(len(glue), 1)
        self.assertIn('Not stated', glue[0][2])
        self.assertEqual(glue[0][3], 'UNKNOWN')
        project = [r for r in rows if r[0].startswith('UniXNU and')]
        self.assertEqual((len(project), project[0][2]), (1, 'BSD-2-Clause-Patent'))
        flat = re.sub(r'\s+', ' ', section)
        for phrase in ('BSD-3-Clause-Clear', 'GPL-2.0-only', 'GPL-2.0-or-later', 'GPL-2.0 OR BSD-3-Clause',
                       'No licence, copying or notice file', 'AGENTS.md rule 4', 'never merged into MIT files',
                       'its own component', 'neither assumes nor grants an answer'):
            self.assertIn(phrase, flat)

    def test_the_recommendation_does_not_replace_snapqcc2072_and_keeps_four_preconditions(self):
        flat = re.sub(r'\s+', ' ', self.text)
        self.assertGreaterEqual(flat.count('does not recommend replacing SnapQCC2072'), 3)
        self.assertIn('the owner decides', flat)
        part = self.text[self.text.index('### 11.6 '):self.text.index('## 12. ')]
        self.assertEqual(len(re.findall(r'^\d\. \*\*', part, re.M)), 4)
        self.assertIn('neither recommended nor rejected', re.sub(r'\s+', ' ', part))
        options = [r for header, rs in tables(self.text) if header[:2] == ['Option', 'What it is'] for r in rs]
        self.assertEqual([r[0] for r in options], ['A (recommended)', 'B', 'C', 'D'])
        self.assertIn('(section 11)', options[3][1])

    def test_appendix_b_is_consistent(self):
        rows = [r for header, rs in tables(self.text) if header[:2] == ['Group', 'Files'] for r in rs]
        self.assertEqual(len(rows), 8)
        number = lambda cell: int(cell.replace(',', ''))
        self.assertEqual(rows[-1][0], 'Total')
        self.assertEqual((sum(number(r[1]) for r in rows[:-1]), sum(number(r[2]) for r in rows[:-1])),
                         (number(rows[-1][1]), number(rows[-1][2])))
        got = {('(root)' if r[0].startswith('(root') else r[0].strip('`').rstrip('/')): (number(r[1]), number(r[2]))
               for r in rows[:-1]}
        self.assertEqual(got, ATH12K_CENSUS)


@unittest.skipUnless(os.environ.get('SP11_UNIXNU_REF'),
                     'SP11_UNIXNU_REF is not set: the UniXNU section, Appendix B and the licence facts are not checked')
class UniXNURef(unittest.TestCase):
    base = Path(os.environ.get('SP11_UNIXNU_REF', '.'))

    @classmethod
    def setUpClass(cls):
        missing = sorted(d for d in UNIX_DIRS.values() if not (cls.base / d).is_dir())
        if missing:
            raise AssertionError(f'SP11_UNIXNU_REF is set but lacks {missing}: an incomplete input fails, it never skips')

    def path(self, repo, rel):
        return self.base / UNIX_DIRS[repo] / rel

    def pages(self):
        return sorted((self.base / 'UniXNU' / 'docs' / 'devices').glob('*.md'))

    def network_sections(self):
        found = {}
        for page in self.pages():
            match = re.search(r'^## Network[^\n]*\n(.*?)(?=^## |\Z)', page.read_text(encoding='utf-8', errors='replace'),
                              re.S | re.M)
            if match and 'Ath12kWifi' in match.group(1):
                found[page.name] = match.group(1)
        return found

    def test_the_files_are_the_pinned_ones(self):
        for (repo, rel), prefix in UNIX_PINS.items():
            path = self.path(repo, rel)
            self.assertTrue(path.is_file(), (repo, rel))
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest()[:16], prefix, (repo, rel))

    def test_the_checkouts_are_at_the_pinned_commits_when_they_say_so(self):
        seen = 0
        for repo, key in (('UniXNU', 'unixnu'), ('Ath12kWifi', 'ath12kwifi'), ('UniXNU-sheng', 'unixnu-sheng')):
            head = git_head(self.base / UNIX_DIRS[repo])
            if head is not None:
                seen += 1
                self.assertEqual(head, COMMITS[key], repo)
        if not seen:
            self.skipTest('no checkout carries .git: the file hashes alone identify the bytes')

    def test_every_unixnu_citation_holds(self):
        rows = tuple((unix_target(name), a, b, token) for name, a, b, token in UNIX)
        self.assertEqual(failures(rows, self.base), [])

    def test_a_moved_unixnu_range_is_caught(self):
        rows = tuple((unix_target(name), a + 700, b + 700, token) for name, a, b, token in UNIX)
        self.assertGreaterEqual(len(failures(rows, self.base)), len(rows) - 8)

    def test_the_two_license_files_are_identical_and_say_bsd_2_clause_patent(self):
        first, second = self.path('UniXNU', 'LICENSE').read_bytes(), self.path('UniXNU-sheng', 'LICENSE').read_bytes()
        self.assertEqual(first, second)
        self.assertIn(b'SPDX-License-Identifier: BSD-2-Clause-Patent', first)

    def test_the_ath12kwifi_glue_states_no_licence_and_has_appendix_b_s_inventory(self):
        groups, spdx, licence_named, copyright_text = {}, [], [], []
        root = self.base / 'Ath12kWifi'
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d != '.git')
            for name in sorted(filenames):
                path = Path(dirpath) / name
                rel = path.relative_to(root).as_posix()
                data = path.read_bytes()
                group = rel.split('/')[0] if '/' in rel else '(root)'
                files, lines = groups.get(group, (0, 0))
                groups[group] = (files + 1, lines + data.count(b'\n'))
                if b'SPDX-License-Identifier' in data:
                    spdx.append(rel)
                if name.lower().startswith(('license', 'licence', 'copying', 'notice', 'copyright', 'unlicense')):
                    licence_named.append(rel)
                if re.search(rb'copyright|\(c\) *20', data, re.I):
                    copyright_text.append(rel)
        self.assertEqual(groups, ATH12K_CENSUS)
        self.assertEqual((sum(v[0] for v in groups.values()), sum(v[1] for v in groups.values())), (261, 13312))
        self.assertEqual(sorted(spdx), sorted(ATH12K_SPDX_FILES))
        self.assertEqual((licence_named, copyright_text), ([], []))
        self.assertEqual(set((root / '.gitignore').read_text().split()), {'deps/', 'out/'})

    def test_the_board_layer_sizes(self):
        for rel, lines in BOARD_LINES.items():
            self.assertEqual(self.path('UniXNU-sheng', rel).read_bytes().count(b'\n'), lines, rel)

    def test_the_abi_boundary_has_17_platform_callbacks(self):
        self.assertEqual(lines_of(self.path('Ath12kWifi', 'include/nw_abi.h'), 9, 27).count('(*'), 17)

    def test_the_firmware_manifest_equals_the_study_pins(self):
        manifest = json.loads(self.path('UniXNU-sheng', 'firmware/wcn7850/manifest.json').read_text())['files']
        self.assertEqual(sorted(manifest), ['amss.bin', 'board-2.bin', 'm3.bin'])
        for name, entry in manifest.items():
            size, sha = FIRMWARE_PINS['ath12k/WCN7850/hw2.0/' + name]
            self.assertEqual((entry['bytes'], entry['sha256']), (size, sha), name)

    def test_the_catalog_and_the_device_records(self):
        pages = self.pages()
        self.assertEqual(len(pages), 266)
        sections = self.network_sections()
        self.assertEqual(len(sections), 34)
        chosen = set()
        for name, section in sections.items():
            components = [line for line in section.splitlines() if line.startswith('Components:')]
            if components and 'Ath12kWifi' in components[0]:
                chosen.add(name)
        self.assertEqual(len(chosen), 18)
        verified = {n for n in chosen if re.search(r'^- Device verification covers', sections[n], re.M)}
        self.assertEqual(sorted(verified), ['sheng-enlixk.md', 'sheng-ks114514.md', 'sheng.md'])
        kinds, revisions = {}, {}
        for name in sorted(chosen):
            lines = sections[name].splitlines()
            bullets = [i for i, line in enumerate(lines) if line.strip().startswith('- `device`')]
            if not bullets:
                kinds['source only'] = kinds.get('source only', 0) + 1
                continue
            first = lines[bullets[0]]
            kind = ('verification' if 'Wi-Fi toggling' in first else
                    'measurement' if 'Firmware c7-00108' in first else 'other')
            kinds[kind] = kinds.get(kind, 0) + 1
            found = re.search(r'Revision: `([0-9a-f]{40})`', ' '.join(lines[bullets[0]:bullets[0] + 2]))
            key = found.group(1) if found else None
            revisions[key] = revisions.get(key, 0) + 1
        self.assertEqual(kinds, {'verification': 3, 'measurement': 12, 'source only': 3})
        self.assertEqual(revisions, {'fa9af675a5c56e27e1aaf0693c77939a4641a3ef': 3,
                                     'a57341b4028514aedb961fe8ce636bb05f5e5925': 12})
        self.assertEqual([n for n, s in sections.items() if re.search('macos', s, re.I)], ['zizhan.md'])
        self.assertNotIn('zizhan.md', chosen)
        texts = {p.name: p.read_text(encoding='utf-8', errors='replace') for p in pages}
        self.assertEqual([n for n, t in texts.items() if re.search('qcc2072', t, re.I)], ['nx769j.md'])
        self.assertEqual([n for n, t in texts.items() if re.search(r'x1e80100|snapdragon x|x elite|sc8380', t, re.I)], [])
        toml = self.path('UniXNU', 'devices.toml').read_text()
        socs = re.findall(r'^soc = "([^"]*)"', toml, re.M)
        self.assertEqual((toml.count('[[device]]'), len(socs), socs.count('SM8550')), (265, 265, 56))
        self.assertEqual([s for s in socs if re.search(r'X1[EPD]|X Elite|X Plus|SC8380|SC8480|Snapdragon X', s, re.I)], [])

    def test_the_lock_files_carry_the_licence_and_the_rfkill_difference(self):
        ath = self.base / 'linux-v7.0.14' / ATH
        for rel in ('core.c', 'wifi7/hal_wcn7850.c', 'wifi7/hal_qcc2072.c', 'wifi7/hal_qcn9274.c'):
            self.assertEqual((ath / rel).read_text().splitlines()[0], '// SPDX-License-Identifier: BSD-3-Clause-Clear')
        core = (ath / 'core.c').read_text()
        self.assertIn('ath12k_acpi_get_disable_rfkill', core)
        self.assertNotIn('microsoft,denali', core)
        self.assertNotIn('broken_rfkill', core)

    def test_the_supplicant_licence_statements(self):
        copying = re.sub(r'\s+', ' ', (self.base / 'wpa_supplicant-2.12' / 'COPYING').read_text())
        readme = re.sub(r'\s+', ' ', (self.base / 'wpa_supplicant-2.12' / 'README').read_text())
        self.assertIn('As of February 11, 2012, the project has chosen to use only the BSD license option', copying)
        self.assertIn('licensed under the BSD license (the one with advertisement clause removed)', readme)

    def test_the_supplicant_build_script_has_a_macos_target_and_never_claims_hardware_verification(self):
        script = self.path('Ath12kWifi', 'tools/build-supplicant.py').read_text()
        self.assertIn('choices=("host", "ios", "macos")', script)
        self.assertIn('"hardware_verified": False', script)

    def test_the_phy_table_equals_linuxs_for_the_x1e80100_phy(self):
        tree = os.environ.get('SP11_LINUX_TREE')
        if not tree:
            self.skipTest('SP11_LINUX_TREE is not set: the PHY comparison needs the pinned Linux tree')
        phy = Path(tree) / 'drivers' / 'phy' / 'qualcomm'
        symbols = {}
        for header in ('phy-qcom-qmp-qserdes-com-v6.h', 'phy-qcom-qmp-qserdes-txrx-v6.h', 'phy-qcom-qmp-pcs-v6.h',
                       'phy-qcom-qmp-pcs-pcie-v6.h'):
            for name, value in re.findall(r'#define\s+(\w+)\s+\(?(0x[0-9a-fA-F]+)\)?', (phy / header).read_text()):
                symbols[name] = int(value, 16)
        text = (phy / 'phy-qcom-qmp-pcie.c').read_text()
        offsets = re.search(r'qmp_pcie_offsets_v5 = \{(.*?)\};', text, re.S).group(1)
        base = {key: int(value, 0) for key, value in re.findall(r'\.(\w+)\s*=\s*(0x[0-9a-fA-F]+|0),', offsets)}
        theirs_text = self.path('UniXNU-sheng', 'package/WifiPcieTables.h').read_text()
        total = 0
        for kind, array in (('serdes', 'kSerdes'), ('tx', 'kTx'), ('rx', 'kRx'), ('pcs', 'kPcs'), ('pcs_misc', 'kPcsMisc')):
            body = re.search(r'sm8550_qmp_gen3x2_pcie_' + kind + r'_tbl\[\] = \{(.*?)\n\};', text, re.S).group(1)
            ours = [(base[kind] + symbols[name], int(value, 16)) for name, value in
                    re.findall(r'QMP_PHY_INIT_CFG(?:_LANE)?\((\w+),\s*(0x[0-9a-fA-F]+)', body)]
            block = re.search(r'constexpr Init ' + array + r'\[\] = \{(.*?)\};', theirs_text, re.S).group(1)
            theirs = [(int(a, 16), int(b, 16)) for a, b in re.findall(r'\{(0x[0-9a-fA-F]+),\s*(0x[0-9a-fA-F]+)\}', block)]
            self.assertEqual(ours, theirs, kind)
            total += len(ours)
        self.assertEqual(total, 79)


if __name__ == '__main__':
    unittest.main(verbosity=1)
