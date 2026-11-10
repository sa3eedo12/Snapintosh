# SP11 Wi-Fi study (WS-J, gate X12): a stretch goal on a different chip

**Study and host-verified checks only. A stretch goal: nothing here is on the path to the finish line. UNIMPLEMENTED on
hardware. Hardware status: NOT RUN.** Nothing in this document has run on a Surface Pro 11. Every statement about the SP11
is VERIFIED against a pinned public source (it says what that source says, not what the unit does), OBSERVED in a
third-party report, or INFERRED, and the last two kinds stay unproven until a read-only measurement on the unit
([section 3](#3-hand-off-state-and-the-read-only-measurement)) has settled them. The A16 build and its behaviour are
untouched: nothing in this study is linked by any build, and no code here reads, powers or programs a radio.

The operator asked whether Wi-Fi can be a stretch goal. This study is the evidence base for the answer: it identifies the
part, says what carries over from the A16's QCC2072 work and what does not, says what the firmware may have left behind,
and defines the gate X12, its stop criterion K6 and the authorizations it would need. Bluetooth, the other half of the same
module, stays out of scope. Section 11 adds a public project, UniXNU, whose Ath12kWifi component runs Linux's ath12k on XNU
with the same chip as its reference: it compares that path with SnapQCC2072 layer by layer, maps its board inputs onto what the
plan can supply, and reads its licences and its maturity.

What the work package delivers:

| Piece | Where | What it is |
| --- | --- | --- |
| This study | `docs/SP11-WIFI-STUDY.md` | Evidence, the carry-over audit, the firmware and board-data analysis, safety, the X12 sub-gates, K6, the descriptor proposal, the per-machine questions, and the UniXNU Ath12kWifi comparison (section 11) |
| Board-container reader | `tools/sp11_wifi_board.py` | A strict reader of the ath12k `board-2.bin` container and the driver's board-name lookup. Read-only, host-only: it answers "does this staged file hold an entry for the module I measured?" before any hardware run |
| Its tests | `tools/test-sp11-wifi-board.py` | Synthetic containers, 27 deliberately malformed ones, a cut-at-every-byte property and the command line; no hardware, no network |
| Study checks | `tools/test-sp11-wifi-study.py`, `tools/test-sp11-wifi-unixnu.py` | The study's structure, labels, pins and headline numbers, every cited line of this repository, the section 11 comparison (its ten-layer table, licence statements and Appendix B), and (with the pinned inputs supplied) every cited line of the Linux tree, the linux-firmware containers, the SnapQCC2072 census and the UniXNU repositories |

The conclusions, each with its label:

1. **The SP11's Wi-Fi is not the A16's chip.** The Denali device tree describes a WCN7850-class PCIe function
   (`pci17cb,1107`) on the `pcie4` controller, Microsoft's public driver package names its board files `wcn785x_2p0`, and two
   community repositories report `17cb:1107` (section 2). VERIFIED (tree, binding, driver id table; package file names as
   relayed to this study); OBSERVED (community, one unit each); INFERRED for the operator's unit until X12a reads it.
2. **Upstream Linux treats it as a sibling of the QCC2072, not as the same part.** Linux gives the QCC2072 its own hardware
   entry, firmware directory, HAL register table (45 of 63 offsets differ), PCI window register and auxiliary-microcode
   download, and shares the WCN7850's pipe map, MHI, WMI and QMI parameters (section 5). VERIFIED. What that means for code
   reuse: INFERRED.
3. **What the Surface firmware leaves powered and trained is UNKNOWN.** No public source says. One read-only measurement
   list settles it (section 3). UNKNOWN.
4. **EL2 cannot power or link a module the firmware left off without writing a driver-sized stack.** The two enable GPIOs are
   plain register writes; the 1.8 V rail and the PCIe controller's votes go through RPMh, and the controller, PHY and SMMU
   bring-up is a Linux-sized job with no verified EL2 sequence. K6 stops the stretch if the module is not already readable
   (section 4). VERIFIED (what Linux does); INFERRED (reachability).
5. **Firmware is a linux-firmware job; nothing comes from the unit's disk.** `amss.bin`, `m3.bin` and `board-2.bin` at a
   pinned commit; the board name the community saw exists in that `board-2.bin`; the regulatory database is an element of the
   same file (section 6). VERIFIED (names, sizes, hashes, entries); UNKNOWN (this unit's identifiers).
6. **The radio needs rules before it transmits.** Upstream ignores the WCN7850 hardware RF-kill input on `microsoft,denali`
   because the OS cannot control it and it reads "radio off"; the regulatory database is loaded and the country code is never
   ours to set (section 7). VERIFIED (the upstream table); OBSERVED (community); the rules themselves are proposals.
7. **Recommended default unchanged: a second chip profile inside SnapQCC2072 (option A), decided by the operator.**
   50 of its 64 source files carry no chip literal (section 5 and Appendix A). The public UniXNU Ath12kWifi (Linux ath12k on
   XNU) is compared layer by layer in section 11 and kept as option D, with four preconditions. This study does not recommend
   replacing SnapQCC2072. INFERRED from a reproducible scan and from reading the UniXNU sources.
8. **Gate X12 has six sub-gates; X12a to X12d need only q1n1 and the host link.** K6 ends the stretch without touching X0 to
   X11 (section 8). The checks of this study cannot say whether any of it works.
9. **UniXNU shows the powered-off branch of K6 as a finite, known sequence on another SoC, and says nothing about the SP11's
   hand-off.** Its own board code powers and trains the module on an SM8550 tablet; its PHY table equals the table Linux uses
   for the SP11's controller; its three firmware files are byte-identical to this study's pins. Whether any of that is
   reachable at EL2 on the SP11 is unchanged: UNKNOWN (section 11.3). VERIFIED (code read, tables compared); OBSERVED (one
   board model).

## 1. Evidence labels and pinned sources

| Label | Meaning here |
| --- | --- |
| VERIFIED | Read in a pinned public source or in this repository, or established by a host test. It never means "ran on a Surface Pro". |
| OBSERVED | Reported by a third party on real hardware (usually one unit, possibly AI-assisted: R20 of the plan), or recorded by this repository from the running A16. Not re-measured here. |
| INFERRED | Reasoned from other evidence, not checked on the SP11. |
| UNKNOWN | Not established. A named measurement resolves it (sections 3 and 10). |
| EXPERIMENTAL / UNIMPLEMENTED | As in [AGENTS.md](../AGENTS.md): code exists but is unqualified, or no code exists. |

**Linux.** Read at commit `7475ea8b58616b33c04afb976cf0fbaad9dd63fb` ("Linux 7.4-rc2", committed 2026-11-09), the pin of
[SURFACE-PRO-11.md](SURFACE-PRO-11.md#12-pinned-sources). Each file keeps its own licence: the three device-tree files are
BSD-3-Clause; the ath12k files are BSD-3-Clause-Clear; the two bindings are GPL-2.0-only (or GPL-2.0) OR BSD-2-Clause; the
power-sequencing, PCI, PHY, pinctrl, clock and quirk files are GPL-2.0 or GPL-2.0-only (each file's SPDX line). Only facts
(offsets, field positions, names, numbers) are used; no text or code is copied. The first 16 hex digits of each file's
SHA-256 identify the exact bytes read.

| File | SHA-256 prefix |
| --- | --- |
| `arch/arm64/boot/dts/qcom/hamoa.dtsi` | `d5bb1f7ce37c721d` |
| `arch/arm64/boot/dts/qcom/x1-microsoft-denali.dtsi` | `afbea2d7b48c1725` |
| `arch/arm64/boot/dts/qcom/x1-el2.dtso` | `d6a9666bd89e8787` |
| `drivers/net/wireless/ath/ath12k/core.c` | `67bd3972d3d132c8` |
| `drivers/net/wireless/ath/ath12k/core.h` | `eedf6b200d56f520` |
| `drivers/net/wireless/ath/ath12k/fw.c` | `6569a96a4292834b` |
| `drivers/net/wireless/ath/ath12k/hw.h` | `27beb16b23bbb933` |
| `drivers/net/wireless/ath/ath12k/qmi.c` | `badbbe18bb593309` |
| `drivers/net/wireless/ath/ath12k/qmi.h` | `104ebde26a22632f` |
| `drivers/net/wireless/ath/ath12k/wifi7/hw.c` | `a5b6587e02091374` |
| `drivers/net/wireless/ath/ath12k/wifi7/pci.c` | `da602c1e7210a710` |
| `drivers/net/wireless/ath/ath12k/wifi7/hal_qcc2072.c` | `bd5d1bf84229d045` |
| `drivers/net/wireless/ath/ath12k/wifi7/hal_wcn7850.c` | `805d54fec23869ae` |
| `Documentation/devicetree/bindings/net/wireless/qcom,ath12k.yaml` | `088f21a8e7e68f67` |
| `Documentation/devicetree/bindings/regulator/qcom,qca6390-pmu.yaml` | `b8faa2f0713b8444` |
| `drivers/power/sequencing/pwrseq-qcom-wcn.c` | `c9256025ea409eb1` |
| `drivers/pci/pwrctrl/pci-pwrctrl-pwrseq.c` | `7863241c729e9f53` |
| `drivers/pci/controller/dwc/pcie-qcom.c` | `628ed3acbe38fba0` |
| `drivers/pci/controller/dwc/pcie-designware.h` | `64ca24764a4fa61a` |
| `drivers/pci/quirks.c` | `9baee6d8404adab9` |
| `drivers/phy/qualcomm/phy-qcom-qmp-pcie.c` | `d073ed062dc4d125` |
| `drivers/pinctrl/qcom/pinctrl-x1e80100.c` | `fd70c0b9013800a4` |
| `drivers/clk/qcom/gcc-x1e80100.c` | `f1c3779c7bc273df` |
| `drivers/phy/qualcomm/phy-qcom-qmp-qserdes-com-v6.h` | `b7b3e2cdfb767f36` |
| `drivers/phy/qualcomm/phy-qcom-qmp-qserdes-txrx-v6.h` | `b3058d41c64f7fad` |
| `drivers/phy/qualcomm/phy-qcom-qmp-pcs-v6.h` | `223333ae32d6f923` |
| `drivers/phy/qualcomm/phy-qcom-qmp-pcs-pcie-v6.h` | `94c874146205004a` |

**linux-firmware.** Read at commit `69e51bb26f3fa3e118dd770f1962a93bed690dc1` (committed 2026-11-09), the pin of the plan's
section 12. Names, sizes and SHA-256 come from the hosting service's file headers; the two `board-2.bin` files and `WHENCE`
were also downloaded and hashed (the hashes agree). Nothing below is committed or distributed by this repository: the
firmware stays in ignored local paths. `WHENCE` (lines 4189-4219 at this commit) gives the ath12k block the licence
`LicenseRef-LICENSE.qcom` ("Redistributable. See LICENSE.qcom and NOTICE.qcom for details"); read the licence before staging.

| Path under the linux-firmware root | Size in bytes | SHA-256 |
| --- | --- | --- |
| `ath12k/WCN7850/hw2.0/amss.bin` | 6090816 | `43aadfd3df887f27de74020273aee484bac6a31dd53068f91baf2a9b094d6a68` |
| `ath12k/WCN7850/hw2.0/m3.bin` | 299660 | `0e72f44df7defc269fe92dcea25d4d409046c04b77d41c510c52879b3dfc1055` |
| `ath12k/WCN7850/hw2.0/board-2.bin` | 2431968 | `6a98237c743d864773a547e81e23f0c6dc721dcd7331926bee7381e2152e00fd` |
| `ath12k/WCN7850/hw2.0/Notice.txt` | 142991 | `515bf4c9d620a87458e4447fe01a0e9bc384d1c3e0037cc4c3d2037b1ff25525` |
| `ath12k/WCN7850/hw2.0/ncm865/amss.bin` | 6111296 | `ffbcccbdfb19402b734132544d30c4c6dd940e71fe6c530ac11e9c6f494b3477` |
| `ath12k/WCN7850/hw2.0/ncm865/m3.bin` | 299660 | `0e72f44df7defc269fe92dcea25d4d409046c04b77d41c510c52879b3dfc1055` |
| `ath12k/QCC2072/hw1.0/firmware-2.bin` | 7148724 | `4c6a1be1f5bfad76319755ff76904abb21c4c7ece5293cc5f33a20b1f4c35254` |
| `ath12k/QCC2072/hw1.0/board-2.bin` | 526720 | `6880d8e6d51292f6bb5f855965b4c2d1bca3fa0d8a70e68bd693d1e73ff43bf1` |
| `LICENSES/LICENSE.qcom` | 13962 | `be904cd28cb292b80cdb6cf412ab0d9159d431671e987ad433c1f62e0988a9bc` |
| `LICENSES/NOTICE.qcom` | 23966 | `fa43e1b9a13b341a07adca9dbe73d0f9072d7966fdfe811c01f0dd2872d7309a` |
| `WHENCE` | 532286 | `3632c15fcdf114586d53fd782f4e8cbe8e526d52b0dff48b1e83c17e34fc3b38` |

**What the two `board-2.bin` containers hold** (read with `tools/sp11_wifi_board.py`; VERIFIED, and re-checked by
`tools/test-sp11-wifi-study.py` when the two files are supplied):

| Container | Top-level elements | Board entries and names | Regulatory entry |
| --- | --- | --- | --- |
| WCN7850 hw2.0, 2431968 bytes | 28 top-level elements: 27 board entries and 1 regulatory entry | 69 board names in 27 entries, 23 distinct data blocks of about 88 KiB; subsystem vendors `105b`, `17cb`, `17aa`, `14cd`, `1eac`, `103c`, `1a3b`; 12 `variant=` strings; no name has subsystem vendor `1414` and no variant spells Microsoft, Surface or Denali | name `bus=pci`, 24,310 bytes, SHA-256 `ada3f31b2e57af3e7a9afd3eab7b26a8067a0132cbad3212e02bb3f1f218c459` |
| QCC2072 hw1.0, 526720 bytes | 5 top-level elements: 4 board entries and 1 regulatory entry | 5 board names in 4 entries; no name has subsystem device `e14f` | name `bus=pci`, 24,310 bytes, SHA-256 `9752107372bdefcd2dd11a394459f8eaec0166a3ca1e680f10ad9a243ac2d360` |

**Other sources.**

| Source | Pin | Used for |
| --- | --- | --- |
| SnapQCC2072 (this project's own driver; MIT) | commit `244d5c7f4117bf42c5b1276a23d5b53aecff2895` (main as read, 2026-10-23); the submodule pointer of the integrated tree names the same commit | The carry-over audit (section 5, Appendix A). Its README says the Linux ath12k and MHI drivers were used as references only and no Linux code is included |
| denisix/ubuntu-surface-pro-11 `WIFI.md` | commit `049b1caccf153ccf7aa5d0f6b824a4dc48e8b02d` (the plan's pin), file SHA-256 prefix `4fe0dc3812787080` | OBSERVED: one unit's PCI identification, hardware RF-kill block and board-file fixup. No licence stated |
| ooaklee/linux-surface-pro-11-oe `docs/` | commit `71524af0602cc35663cd566eb6c7e151aedea995`: `adr/adr-0005-wifi-board-fixup.md` (`78d9d5d989427429`), `adr/adr-0018-wifi-rfkill-bring-up-gate.md` (`595d8202557aace2`), `installed-wifi-windows-firmware-cold-boot-test-20260613.md` (`93dda40fb0bd9208`) | OBSERVED: one unit's probe log, interface name, firmware version, RF-kill state and the warm-reboot failure. No licence stated |
| ramonjesusmiranda-debug/sp11-el2 `README.md` | commit `8f1726d49e57de97ac0a349a89ae894206f0fc12` (the plan's pin), file SHA-256 prefix `6165523a766f6a73` | OBSERVED: Linux at EL2 on one unit runs ath12k, and what its PCIe interconnect votes had to include |
| Microsoft Learn, tech specs, Surface Pro Snapdragon X Series 11th Edition | MicrosoftDocs/Devices-docs-pr commit `63494b316bdd533f33953b1390c02281572350ef` (the plan's pin) | VERIFIED: "Wi-Fi 7", Wi-Fi-only and Wi-Fi + 5G models, footnote 17 "6GHz band not available in all regions". No text copied |
| Microsoft Surface Pro 11 driver package `SurfacePro11_ARM_Win11_26100_26.094.41119.0.msi` | the plan's section 1.2 | File **names** only, relayed to this study from the package listing: the WLAN driver `qcwlanhmt8380` with board files named `wcn785x_2p0`. **This study did not open the package** and the unit's own disk is not needed |
| UniXNU `UniXNU/UniXNU` (BSD-2-Clause-Patent) | commit `d830d23b0d19c9322d906c21afeca1d6c54aeffe` (main as read, 2026-11-10) | Section 11: the component catalog, the Ath12kWifi and network porting pages, the device records |
| UniXNU `UniXNU/Ath12kWifi` (no licence file) | commit `48e23bf15fc21bbd30d5583b42b9c09ad65537db` (main as read, 2026-11-10). The catalog above pins `28de7b91d7a880682b777e0e3564245a99dbc3f5` (2026-11-04), which differs from it in two files only (`tools/build-supplicant.py`, `userspace/supplicant.config`); the board repository's gitlink names the first | Section 11 and Appendix B: the glue, the `nw_abi.h` interface and the lock file |
| UniXNU `UniXNU/UniXNU-sheng` (BSD-2-Clause-Patent) | commit `00ba90984e831e4c6ce30ec3d8a56e6434e9f1af` (main as read, 2026-11-10) | Section 11: the board layer (`WifiProbe`, `WifiNet`, the PHY table), the firmware manifest and the Wi-Fi reports |
| What Ath12kWifi locks | Linux stable v7.0.14, commit `458c6079fc1d41d564c37679c8ace02cd83ee817` (2026-06-27); `wpa_supplicant-2.12.tar.gz` with SHA-256 `08e23937e16d0155e55cab2b51f51fbe10d80a1aa91c4e15442645059b737ef6` (the downloaded archive hashes to the same value); FreeBSD `releng/15.1` commit `95719d8a895bf8170851e9844d9b4092b1d2b6fe` | Section 11.4 only: licence lines of 23 Linux files and one FreeBSD file sampled through the GitHub API, and the archive's `COPYING` and `README`. The tests re-check the four Linux files and the two archive files listed below, not the other samples |

**UniXNU files cited.** The first 16 hex digits of each file's SHA-256 identify the bytes read at the commits above. The two
`LICENSE` files are identical. The rows "Linux v7.0.14" are four files of the Ath12kWifi lock (paths under the Linux root) and
the rows "wpa_supplicant 2.12" are two files of its archive; they exist to check WE43, WE52 and section 11.4.

| Repository | File | SHA-256 prefix |
| --- | --- | --- |
| UniXNU | `LICENSE` | `5b1d3d911103cb0f` |
| UniXNU | `AGENTS.md` | `ea11e40ead1e83cb` |
| UniXNU | `README.md` | `37c34314132525af` |
| UniXNU | `components.toml` | `af721e73ce192367` |
| UniXNU | `devices.toml` | `c9027a64d2e4672a` |
| UniXNU | `docs/drivers/ath12k-wifi.md` | `d244345074872669` |
| UniXNU | `docs/porting/network.md` | `c37984b970b23034` |
| UniXNU | `docs/devices/sheng.md` | `ba183f462e8670d7` |
| UniXNU | `docs/devices/fuxi.md` | `5a2e95db8a81f72e` |
| UniXNU | `docs/devices/nx769j.md` | `a6d0fb43a490e4f1` |
| UniXNU | `docs/devices/venus.md` | `c049e0a17d02dc74` |
| UniXNU | `profiles/os/macos-27.3-26C5035r/profile.toml` | `66ffded31e80cf9d` |
| Ath12kWifi | `README.md` | `48f152e94a43c644` |
| Ath12kWifi | `AGENTS.md` | `9bdd1f18868114e3` |
| Ath12kWifi | `.gitignore` | `55e7205a3da609e0` |
| Ath12kWifi | `include/nw_abi.h` | `508f5e8f70cb7fc4` |
| Ath12kWifi | `src/nw_pci.c` | `db549a0909d25e10` |
| Ath12kWifi | `tools/build-supplicant.py` | `53c8ada0293dd4aa` |
| Ath12kWifi | `tools/source-lock.json` | `f23882695f1c136c` |
| Ath12kWifi | `shim/include/linux/math.h` | `0a1a58e364b3c8c2` |
| UniXNU-sheng | `LICENSE` | `5b1d3d911103cb0f` |
| UniXNU-sheng | `AGENTS.md` | `8e95e5e049ee4705` |
| UniXNU-sheng | `package/WifiProbe.cpp` | `72f46e8229865eff` |
| UniXNU-sheng | `package/WifiNet.cpp` | `a4738ae766ded21d` |
| UniXNU-sheng | `package/WifiPcieTables.h` | `f21a8bda7c217cd2` |
| UniXNU-sheng | `package/WifiFirmware.h` | `dccf8dbb118bfaae` |
| UniXNU-sheng | `firmware/wcn7850/manifest.json` | `f474f2a5a157bc59` |
| UniXNU-sheng | `reports/ipados27-wifi-mainline-migration.md` | `3337d97384940695` |
| UniXNU-sheng | `reports/ipados27-wifi-association.md` | `866928739fec703c` |
| Linux v7.0.14 | `drivers/net/wireless/ath/ath12k/core.c` | `a2a0cd02e05ff3b8` |
| Linux v7.0.14 | `drivers/net/wireless/ath/ath12k/wifi7/hal_wcn7850.c` | `a5cbf2f7e535eb7f` |
| Linux v7.0.14 | `drivers/net/wireless/ath/ath12k/wifi7/hal_qcc2072.c` | `02f02cf20fecdb5c` |
| Linux v7.0.14 | `drivers/net/wireless/ath/ath12k/wifi7/hal_qcn9274.c` | `3ada2d8cdcd7ecbe` |
| wpa_supplicant 2.12 | `COPYING` | `a87ac4e333d0f120` |
| wpa_supplicant 2.12 | `README` | `83be1b142c59ccf0` |

## 2. Identification: what the Denali tree says about the Wi-Fi

### 2.1 The device

| # | Claim | Label | Source |
| --- | --- | --- | --- |
| WE1 | The Denali tree enables `pcie4` and describes one endpoint under its root port: `wifi@0`, compatible `pci17cb,1107`, `reg = <0x10000 ...>` (bus 1, device 0, function 0). | VERIFIED | `x1-microsoft-denali.dtsi:998-1001` |
| WE2 | The binding names `pci17cb,1107` as the WCN7850, and the ath12k PCI id table lists device ids `0x1107` (WCN7850) and `0x1112` (QCC2072). | VERIFIED | `qcom,ath12k.yaml:14-22`; `ath12k/wifi7/pci.c:21-22` |
| WE3 | ath12k accepts a `0x1107` function only if the SoC hardware version, read at address `0x1B00000` through the BAR0 window, has major number 2 ("wcn7850 hw2.0"); any other major refuses the probe. The QCC2072 path reads no version ("only one version till now"). | VERIFIED | `ath12k/wifi7/pci.c:134-157`; `ath12k/wifi7/pci.c:158-167`; `ath12k/wifi7/hw.c:453-457` |
| WE4 | Microsoft's package names its WLAN board files `wcn785x_2p0` (WCN7850 hardware 2.0), agreeing with WE3. No board-file name is that of Surface or Denali, so the Surface selection is made by something other than the file name (an INF or a board id) that this study did not read. | VERIFIED (names relayed, not re-read); UNKNOWN (the Windows selection rule) | The assignment's package listing |
| WE5 | The product has Wi-Fi-only and Wi-Fi + 5G models, Wi-Fi 7 on both, and the 6 GHz band is not available in all regions. Which model the operator's unit is, is not recorded. | VERIFIED (specs); UNKNOWN (unit) | Microsoft Learn tech specs, footnote 17 |
| WE6 | One community unit reports PCI `17cb:1107`, hardware name "wcn7850 hw2.0", firmware version `0x1103006c`, the interface `wlP4p1s0` at PCI address `0004:01:00.0`, subsystem `17cb:1107`, QMI chip id 2 and board id 255. A second repository reports the same `17cb:1107`. | OBSERVED (one unit each) | ooaklee cold-boot test and `adr-0005`; denisix `WIFI.md` |
| WE7 | The operator's unit has the same function. | INFERRED | WE1 to WE6; read at X12a |

### 2.2 The controller, the port and the stream

| # | Claim | Label | Source |
| --- | --- | --- | --- |
| WE8 | The controller is `pcie4` (`qcom,pcie-x1e80100`): PARF `0x01c08000` (size `0x3000`), DBI `0x7c000000`, ELBI `0x7c000f40`, ATU `0x7c001000`, config window `0x7c100000` (size `0x100000`), MHI `0x01c0b000`. These are Linux's windows, not an ACPI MCFG allocation. | VERIFIED | `hamoa.dtsi:3714-3728` |
| WE9 | The outbound windows are I/O `0x7c200000` (1 MiB) and memory `0x7c300000` (size `0x1d00000`), for a bus range 0 to 255 with one root port at device 0 whose secondary range is 1 to 255. | VERIFIED | `hamoa.dtsi:3731-3732`; `hamoa.dtsi:3805-3812` |
| WE10 | Linux's PCI domain is 4, with 2 lanes; the PHY is `qcom,x1e80100-qmp-gen3x2-pcie-phy` at `0x01c0e000` (Gen3 x2). The domain number is Linux's, not an ACPI segment. | VERIFIED | `hamoa.dtsi:3737-3738`; `hamoa.dtsi:3824-3826` |
| WE11 | MSI goes through the ITS with DeviceID base `0xc0000` (`0x10000` ids); the controller has eight MSI interrupts (SPI 141 to 148), a global interrupt (SPI 156) and four INTx lines (SPI 149 to 152). The ITS node is "Reserved by Gunyah" and the upstream EL2 overlay notes that Gunyah's ITS emulation seems broken. | VERIFIED | `hamoa.dtsi:3740-3740`; `hamoa.dtsi:3742-3750`; `hamoa.dtsi:3763-3766`; `hamoa.dtsi:8589-8589`; `x1-el2.dtso:28-31` |
| WE12 | The PCIe SMMU is the SMMUv3 `pcie_smmu` at `0x15400000` (size `0x80000`), "Controlled by Gunyah" and reserved. The EL2 overlay enables it and maps `pcie4` requester ids to StreamIDs from `0x40000`, window `0x10000`. | VERIFIED | `hamoa.dtsi:8544-8555`; `x1-el2.dtso:24-27`; `x1-el2.dtso:38-40`; `x1-el2.dtso:51-53` |
| WE13 | For the function at 01:00.0 (requester id `0x0100`) the overlay's map gives StreamID `0x40100`. The A16's admission record carries the same number for its QCC2072 (`SID = 0x40100`), by the same shape of rule (a `0x10000`-sized window per root complex plus the requester id). Whether the firmware's own map on the SP11 is the same is not known. | INFERRED (SP11); OBSERVED (A16) | `probes/qcc2072wifi.py:61`; WE12 |
| WE14 | The controller's power and reset inputs are: seven GCC clocks, two controller resets, `GCC_PCIE_4_GDSC`, an RPMh performance-state request (`required-opps`) and two interconnect paths; the PHY adds six clocks (one of them the TCSR reference-clock enable), two resets and `GCC_PCIE_4_PHY_GDSC`. | VERIFIED | `hamoa.dtsi:3768-3799`; `hamoa.dtsi:3828-3849` |
| WE15 | The Denali enables `pcie4` with `status = "okay"` and nothing else: no `perst-gpios`, `wake-gpios` or pin-control reference exists for the Wi-Fi port, while `pcie6a_port0` gets a `reset-gpios` and `wake-gpios`. The file does define an unreferenced pin state `pcie4_default` naming GPIO 146 (`perst-n`), 147 (`clkreq-n`, function `pcie4_clk`) and 148 (`wake-n`). Whether PERST# is wired to GPIO 146 is not established. | VERIFIED (negative, as read); UNKNOWN (wiring) | `x1-microsoft-denali.dtsi:987-989`; `x1-microsoft-denali.dtsi:1031-1034`; `x1-microsoft-denali.dtsi:1234-1256` |

### 2.3 The power tree

| # | Claim | Label | Source |
| --- | --- | --- | --- |
| WE16 | `VREG_WCN_3P3` is a fixed 3.3 V regulator enabled by GPIO 214 (pin state `wcn_sw_en`), marked `regulator-boot-on`. `VREG_WCN_0P95` and `VREG_WCN_1P9` are fixed regulators fed from it, with no GPIO of their own. | VERIFIED | `x1-microsoft-denali.dtsi:291-305`; `x1-microsoft-denali.dtsi:307-325`; `x1-microsoft-denali.dtsi:1301-1306` |
| WE17 | `boot-on` records that the board expects the 3.3 V switch to be on at boot. That the Surface firmware actually leaves it on is not established. | INFERRED | WE16 |
| WE18 | The module's power-management unit is the node `wcn7850-pmu` (`qcom,wcn7850-pmu`): inputs `vdd`, `vddaon`, `vdddig` from the 0.95 V rail, `vddrfa1p2` and `vddrfa1p8` from the 1.9 V rail, `vddio` from `vreg_l15b_1p8`; enables `wlan-enable-gpios` = GPIO 117 and `bt-enable-gpios` = GPIO 116 (pin state `wcn_wlan_bt_en`); ten internal outputs `ldo0` to `ldo9` that feed the Wi-Fi function and the Bluetooth function. The node gives the PMU no clock and no `xo-clk` GPIO (the binding offers `xo-clk-gpios` for other chips), so the module has no host-driven reference clock: the PCIe reference clock is the controller side's (WE14). | VERIFIED | `x1-microsoft-denali.dtsi:401-459`; `x1-microsoft-denali.dtsi:1227-1232`; `qcom,qca6390-pmu.yaml:100-103` |
| WE19 | `vreg_l15b_1p8` is LDO 15 of the PM8550B group under the RPMh RSC (`qcom,pm8550-rpmh-regulators`, `qcom,pmic-id = "b"`): an RPMh-controlled rail, not a GPIO. | VERIFIED | `x1-microsoft-denali.dtsi:461-464`; `x1-microsoft-denali.dtsi:561-565` |
| WE20 | The binding requires six supplies for `qcom,wcn7850-pmu` (`vdd`, `vddio`, `vddaon`, `vdddig`, `vddrfa1p2`, `vddrfa1p8`); the Linux sequencer asks for seven (it adds the optional `vddio1p2`). The Denali node supplies the six. | VERIFIED | `qcom,qca6390-pmu.yaml:171-183`; `pwrseq-qcom-wcn.c:417-431` |
| WE21 | The `wifi@0` node consumes nine of the PMU's outputs (`vddaon`, `vddwlcx`, `vddwlmx`, `vddrfacmn`, `vddrfa0p8`, `vddrfa1p2`, `vddrfa1p8`, `vddpcie0p9`, `vddpcie1p8`). The Bluetooth node on `uart14` consumes seven of the same outputs. | VERIFIED | `x1-microsoft-denali.dtsi:1003-1011`; `x1-microsoft-denali.dtsi:1331-1346` |
| WE22 | The Linux order for a WCN7850 is: enable the regulators, drive `WLAN_EN` high, wait 50 ms (`pwup_delay_ms`), with no extra delay between enable edges. In the PCIe host initialisation this happens after PERST# is asserted and the controller and PHY are up, and before PERST# is released (held asserted at least 100 ms). After that a stream-configuration step runs only for controller generations whose operations table has one: the SM8550's has it, the X1E80100's does not at this pin (section 4). | VERIFIED | `pwrseq-qcom-wcn.c:213-221`; `pwrseq-qcom-wcn.c:289-293`; `pwrseq-qcom-wcn.c:427-431`; `pcie-qcom.c:356-361`; `pcie-qcom.c:1480-1546`; `pcie-qcom.c:1522-1527`; `pcie-qcom.c:1660-1678`; `pcie-qcom.c:1728-1731`; `pcie-qcom.c:2614-2615` |
| WE23 | The PCI power-control wrapper binds `pci17cb,1107` to the "wlan" sequencer target. | VERIFIED | `pci-pwrctrl-pwrseq.c:121-124` |

### 2.4 Denali-specific and Hamoa-generic

| Item | Generic to Hamoa (every X1E80100 board) | Specific to the Denali | Label |
| --- | --- | --- | --- |
| Controller, PHY, windows, interrupts, MSI map | `pcie4` and `pcie4_phy` in `hamoa.dtsi` (`3714-3857`) | enabled by the Denali; PHY supplies `vreg_l3i_0p8`, `vreg_l3e_1p2` (`x1-microsoft-denali.dtsi:991-996`) | VERIFIED |
| SMMU and stream window | `pcie_smmu`; the EL2 overlay's `0x40000` window | none | VERIFIED |
| Endpoint | none (the SoC has no Wi-Fi) | `wifi@0` and its nine supplies | VERIFIED |
| Power tree | the TLMM pins and the RPMh PM8550B group | the 3.3 V switch on GPIO 214, the two derived rails, the PMU node and its GPIOs 116 and 117, `vddio` on `l15b` | VERIFIED |
| PERST#, WAKE#, CLKREQ# | the pin function `pcie4_clk` | an unreferenced `pcie4_default` state; nothing wires PERST# or WAKE# in the node | VERIFIED (negative); UNKNOWN (board) |
| Hardware RF-kill | none | upstream ath12k ignores the WCN7850 hardware RF-kill input on `microsoft,denali` (`ath12k/core.c:73-104`); see section 7 | VERIFIED |

## 3. Hand-off state and the read-only measurement

### 3.1 What public evidence says

| # | Claim | Label | Source |
| --- | --- | --- | --- |
| WE24 | On the A16 the firmware left the QCC2072 enumerated at ECAM bus 1 with its BAR0 and BAR2 assigned and its link up, and the admission refuses unless the chip is still in its boot ROM (PBL or EDL) with bus mastering off. That is one unit of another SoC with another firmware: it is not evidence for the SP11. | OBSERVED (A16, one unit) | `compat/qcc2072-wifi.h:54-59`; `probes/qcc2072wifi.py:27-28`; `probes/qcc2072wifi.py:343-372` |
| WE25 | Linux on the SP11 powers the module and trains the link itself (the sequence of WE22), so a Linux probe log says nothing about what the firmware left. The same holds for Linux at EL2 under slbounce, where ath12k works on one unit once the PCIe interconnect votes include a `gic-mem` path (otherwise MSIs are lost when DDR idles). | OBSERVED (community, one unit each) | ooaklee cold-boot test; sp11-el2 `README.md` item 16 |
| WE26 | After firmware was installed on one unit, the first reboot found the function present but the ath12k probe failed with MHI and global-reset errors; a full cold boot returned it to the earlier state. That points to the module keeping running firmware across a warm reset. | OBSERVED (one unit); INFERRED (the mechanism) | ooaklee cold-boot test |
| WE27 | Upstream Linux marks the `0x1107` function as unable to take a secondary bus reset and gives it a device-specific reset instead: a "SoC global reset" through BAR0 (register `0x3008`, bit 0, then the MHI control register `0x38`). No such quirk exists for `0x1112` at this commit. | VERIFIED | `quirks.c:3820-3820`; `quirks.c:4281-4340`; `quirks.c:4400-4400` |
| WE28 | What the Surface UEFI does for the `pcie4` controller (clocks, link, enumeration) and for the module (rails, enables) is not public. | UNKNOWN | none |

### 3.2 The measurement

<!-- BEGIN X2-WIFI-LIST (the uefi-func-tests agent may lift this section verbatim) -->

**Scope.** Read-only. No write to any PCI configuration register (so no BAR sizing and no command-register change), PCIe
controller, SMMU, GCC, TCSR, TLMM, PMIC or regulator register. No MHI or BHI access: those live behind BAR0 and need memory
decode, which the plan's X12b authorizes separately; if the firmware left memory decode on, X12b may read them first. No
interrupt-status register, no mapping or unmapping. The reads are issued where the X2 test image does its other read-only
MMIO reads, each recorded as a value, or as a fault with its ESR and address. A page the firmware did not map faults:
record it and carry on; never map it.

**S0, boot services (no MMIO).** List every `EFI_PCI_IO_PROTOCOL` handle: segment, bus, device, function, and the first
64 configuration bytes. A handle with vendor `0x17cb` and device `0x1107` means the firmware itself enumerated the module.
If the firmware publishes no such handle, say so. This step writes nothing.

**S1, the gates, in this order, stopping at the first that is off or unreadable.** Every block, offset and bit below is a
Linux fact read at the pin (the GCC base `hamoa.dtsi:758-760`, the clock and GDSC definitions `gcc-x1e80100.c:3614-3627`,
`gcc-x1e80100.c:3693-3705` and `gcc-x1e80100.c:6607-6629`) and INFERRED to hold on the unit; the test image takes them from
the board descriptor and the parameters, not from literals. The order is a safety rule: a read of a block whose clock or
power domain is off can fault or stall the interconnect (INFERRED for this unit; another project's code for the same controller
family on SM8550 records that a read of an unclocked block stalls the bus, `UniXNU-sheng/package/WifiProbe.cpp:9-12`), so
nothing behind a gate is read until the gate reads on. A controller whose gates read off is ABSENT for the
verdict below (nothing behind the gates was read), and one whose page faults is FAULT; neither is retried.

| Id | Block | Base | Offset | Address | Bits used | Expected if the module is up |
| --- | --- | --- | --- | --- | --- | --- |
| G1 | gcc | 0x00100000 | 0x6b004 | 0x0016b004 | 31 PWR_ON (`GCC_PCIE_4_GDSC`) | set |
| G2 | gcc | 0x00100000 | 0x6c000 | 0x0016c000 | 31 PWR_ON (`GCC_PCIE_4_PHY_GDSC`) | set |
| G3 | gcc | 0x00100000 | 0x6b034 | 0x0016b034 | 31 CLK_OFF (`GCC_PCIE_4_CFG_AHB_CLK`) | clear |
| G4 | gcc | 0x00100000 | 0x6b01c | 0x0016b01c | 31 CLK_OFF (`GCC_PCIE_4_SLV_AXI_CLK`) | clear |

**S2, the GPIO snapshot.** Each GPIO has a control register at `0x0f100000 + n * 0x1000` and an I/O register 4 bytes
above it (function select from bit 2, output enable bit 9; input bit 0, output bit 1: `pinctrl-x1e80100.c:12-46`; the
TLMM base is `hamoa.dtsi:6119-6121`). Read both; never write; do not read the interrupt-status register at +0xc.

| Id | GPIO | Role in the pinned tree | Control address | I/O address |
| --- | --- | --- | --- | --- |
| T1 | 214 | 3.3 V switch `VREG_WCN_3P3` | 0x0f1d6000 | 0x0f1d6004 |
| T2 | 117 | `WLAN_EN` | 0x0f175000 | 0x0f175004 |
| T3 | 116 | `BT_EN` (context only; Bluetooth stays out of scope) | 0x0f174000 | 0x0f174004 |
| T4 | 146 | PERST# candidate (`pcie4_default`, unreferenced) | 0x0f192000 | 0x0f192004 |
| T5 | 147 | CLKREQ# (`pcie4_clk`) | 0x0f193000 | 0x0f193004 |
| T6 | 148 | WAKE# candidate | 0x0f194000 | 0x0f194004 |

**S3, the controller, only after G1 to G4 read as expected.** The offsets are `pcie-qcom.c:59-59` (`PARF_LTSSM`),
`pcie-qcom.c:75-75` (`PARF_BDF_TO_SID_CFG`), `pcie-qcom.c:158-158` (the state field) and `pcie-qcom.c:175-175` (the bypass
bit); the PARF base is `hamoa.dtsi:3714-3728`. Linux's X1E80100 controller operations have no stream-configuration step
(section 4), so P2 is read to learn what the firmware left and not because Linux writes it for this SoC.

| Id | Block | Base | Offset | Address | Bits used | Meaning |
| --- | --- | --- | --- | --- | --- | --- |
| P1 | parf | 0x01c08000 | 0x1b0 | 0x01c081b0 | 5:0 state, 8 LTSSM_EN | link training state machine |
| P2 | parf | 0x01c08000 | 0x2c00 | 0x01c0ac00 | 0 BDF_TO_SID bypass | whether the controller translates requester ids to StreamIDs |

**S4, configuration space, only after G1 to G4 read as expected, at the MCFG window of the segment that hosts the Wi-Fi (the
segment is read from ACPI, never assumed: Linux's domain 4 is not an ACPI segment).** Dword reads only. The controller serves
the window, so a read with its clocks off runs into the hazard named under S1 (INFERRED).

| Id | Function | Offset | Meaning | Expected |
| --- | --- | --- | --- | --- |
| C1 | root port 00:00.0 | 0x00 | vendor and device | a Qualcomm root port |
| C2 | root port 00:00.0 | 0x18 | primary, secondary, subordinate bus | secondary 1 |
| C3 | root port 00:00.0 | PCIe capability, link status | bit 13 DLLLA; bits 3:0 speed; bits 9:4 width | DLLLA set; Gen3 x2 |
| C4 | endpoint 01:00.0 | 0x00 | vendor and device | `0x110717cb` |
| C5 | endpoint 01:00.0 | 0x04 | command and status | recorded, not interpreted |
| C6 | endpoint 01:00.0 | 0x08 | class code and revision | recorded |
| C7 | endpoint 01:00.0 | 0x10, 0x14, 0x18, 0x1c | BARs as the firmware programmed them (never sized) | recorded |
| C8 | endpoint 01:00.0 | 0x2c | subsystem vendor and device; selects the board entry | recorded |
| C9 | endpoint 01:00.0 | PCIe capability: link status and device control 2 | link up; whether LTR is enabled | recorded |

**S5, the PCIe SMMU, at the IORT node.** Dword reads at `0x15400000` plus: `0x20` CR0, `0x24` CR0ACK, `0x44` GBPA,
`0x80` STRTAB_BASE, `0x88` STRTAB_BASE_CFG. If CR0 enables translation, walk the stream table for StreamID `0x40100`
(the requester id from C2 and the window from WE12) as `probes/qcc2072wifi.py` `walk_smmu` does: read-only, every table
pointer inside DRAM, and record bypass, abort or stage-2 translation and whether a 64 MiB arena would be identity-mapped
read/write. The A16 walker refuses anything but a stage-2-only entry, so a bypass or abort result is new information.

**Verdict.** One of: PRESENT-LINKED (C4 reads `0x110717cb` and C3 shows DLLLA set); PRESENT-UNLINKED (the root port answers
and DLLLA is clear); ABSENT (a gate of G1 to G4 reads the controller off, or no root port answers, or C4 reads all-ones with
the link down, or S0 found no handle and nothing answers); FAULT (any read faulted: keep the ESR and the address); INCONSISTENT
(the endpoint answers with another id, or answers while the gates, DLLLA or `WLAN_EN` say the module is off). Raw values are
always kept.

**What each verdict means.** PRESENT-LINKED: X12b may be planned. PRESENT-UNLINKED, ABSENT or FAULT: the firmware did not
leave a usable module; K6 applies (section 4). INCONSISTENT: record everything and stop; the unit does not behave as the
pinned sources describe, which is a result and not a failure of the test.

<!-- END X2-WIFI-LIST -->

## 4. A powered-off chip: what EL2 could do, and K6

If the module is off at hand-off, the Linux order of WE22 is what would have to be reproduced. Reachability is an assessment
of this study, INFERRED, and never a verified sequence.

| Step (Linux order) | Operation and controller | Reachable at EL2 without a Linux-style driver stack | Label |
| --- | --- | --- | --- |
| 1. Assert PERST# | TLMM write; the GPIO is not established (WE15) | A plain register write once the TLMM page is mapped and the pin is known; the wiring is UNKNOWN | INFERRED |
| 2. Controller initialisation | seven GCC clocks, two resets, `GCC_PCIE_4_GDSC`, an RPMh performance level and two interconnect votes (WE14) | The GCC and TCSR parts are register writes with no verified order for this SoC; the RPMh level and the interconnect votes need the RSC and are UNIMPLEMENTED (the A16 path never programs them beyond waking the GPU RSC, plan F2) | INFERRED |
| 3. PHY power-on | `GCC_PCIE_4_PHY_GDSC`, PHY resets, the TCSR reference clock, then 79 table writes (38 SerDes, 5 TX, 25 RX, 5 PCS, 6 PCS-misc) and a status poll | Bounded: the tables are Linux's, which Linux also uses for this PHY, and another project applied the same 79 values on an SM8550 tablet (section 11.3) | VERIFIED (the count and the equality with UniXNU's table); OBSERVED (a link trained on SM8550); INFERRED (reachability at EL2) |
| 4. Power the module | the 3.3 V switch (GPIO 214), `vddio` on `l15b` through RPMh, `WLAN_EN` (GPIO 117), 50 ms (WE16, WE19, WE22) | The two GPIOs yes in principle; `vddio` no, unless it is already on because other consumers share it (INFERRED) | INFERRED |
| 5. Release PERST#, train the link | TLMM write after at least 100 ms; poll the controller for the link | Yes in principle, after steps 2 and 3 | INFERRED |
| 6. Stream configuration | Linux's X1E80100 controller operations have no stream-configuration step (`cfg_sc8280xp` uses `ops_1_21_0`: `pcie-qcom.c:1670-1678`, `pcie-qcom.c:1728-1731`); the SM8550's operations (`pcie-qcom.c:1660-1668`) write the controller's requester-id hash table (`pcie-qcom.c:1186-1262`). On the SP11 the StreamID is the requester id plus the overlay's window (WE12), so the work is the SMMU's stream table entry, and whether the controller's own bypass bit must be set or cleared is UNKNOWN | A new hardware write needing the SMMU state (UNKNOWN) and its own authorization, as K4 does for the NVMe | VERIFIED (Linux's tables); INFERRED (the SP11 consequence) |

The PHY count is from `phy-qcom-qmp-pcie.c:5207-5232` (the configuration the X1E80100 Gen3 x2 PHY uses,
`phy-qcom-qmp-pcie.c:6533-6534`). Steps 2, 3 and 6 are the size of the Linux drivers they come from (the controller driver
alone is about 70 KB), and none has a verified EL2 form. The first run would also be the first time this project powers a
radio module and trains a second PCIe root port. The rule of AGENTS.md section 8 (fail closed; never fabricate a hardware
contract) therefore applies to the whole branch.

**What UniXNU adds.** Another project performs steps 1 to 5 itself, in its kernel, on an SM8550 tablet. Its sequence is the
Linux order of this table plus three things the table does not list: a PMIC pin that supplies the module's sleep clock, an RPMh
mode request for each rail, and a bus-clock-manager vote for the PCIe path (section 11.3). That makes the branch a known,
finite sequence and not an open-ended one. It does not make it reachable at EL2 on the SP11: the sequence ran in a driver with
the whole SoC mapped, the SP11's PCIe SMMU belongs to Gunyah, and the sleep clock, the PERST# wiring and the number of
interconnect votes are UNKNOWN for the SP11. K6 is unchanged.

**Decision: K6, proposed exact wording** (for the plan's section 3.3):

> **K6** (stretch goal only; it ends WS-J and never blocks X0 to X11). At X12a the Wi-Fi function is not readable at
> hand-off (its configuration space reads absent or all-ones, its link is down, or a read faults), or it is not the device
> the evidence names (vendor `0x17cb`, device `0x1107`, hardware version major 2), and the only way forward is for q1n1 or
> the monitor to power the module, train the PCIe link, or program the PCIe controller, its PHY, the SMMU or an RPMh rail
> itself; or no board-data entry matches the module's measured identifiers and the owner declines the Microsoft-package
> fallback; or the owner declines the RF-kill policy that X12d needs. Stop, keep the logs, report what was measured and ask
> the owner. Any EL2 power-up or link-training path is a separate design study with its own approval, as D4 does for NV2.

## 5. Carry-over from the A16

### 5.1 Is it the same family in upstream Linux?

At the pin ath12k handles both parts in one driver and gives the QCC2072 its own entries where it differs. The two hardware
entries are `ath12k/wifi7/hw.c:453-546` (WCN7850 hw2.0) and `ath12k/wifi7/hw.c:727-822` (QCC2072 hw1.0).

| Parameter | WCN7850 hw2.0 | QCC2072 hw1.0 | Same | Label |
| --- | --- | --- | --- | --- |
| PCI device id | `0x1107` (`ath12k/wifi7/pci.c:21-21`) | `0x1112` (`ath12k/wifi7/pci.c:22-22`) | no | VERIFIED |
| Hardware revision rule | version major 2 read from BAR0 (`ath12k/wifi7/pci.c:25-29`) | none: fixed `HW10` (`ath12k/wifi7/pci.c:165-166`) | no | VERIFIED |
| PCI window register | `0x310c` (`ath12k/wifi7/pci.c:31-31`) | `0x3278` (`ath12k/wifi7/pci.c:32-32`) | no | VERIFIED |
| Bus operations (MHI wake and release) | the WCN7850 set (`ath12k/wifi7/pci.c:138-138`) | the same set (`ath12k/wifi7/pci.c:162-162`) | yes | VERIFIED |
| Firmware directory | `WCN7850/hw2.0` (`ath12k/wifi7/hw.c:457-457`) | `QCC2072/hw1.0` (`ath12k/wifi7/hw.c:731-731`) | no | VERIFIED |
| Auxiliary-microcode download | off (`ath12k/wifi7/hw.c:461-461`) | on (`ath12k/wifi7/hw.c:735-735`) | no | VERIFIED |
| QMI service instance | the WCN7850 id (`ath12k/wifi7/hw.c:466-466`) | the same id (`ath12k/wifi7/hw.c:740-740`) | yes | VERIFIED |
| Copy-engine configuration and pipe map | the WCN7850 tables (`ath12k/wifi7/hw.c:473-473`) | the same tables (`ath12k/wifi7/hw.c:747-747`) | yes | VERIFIED |
| MHI configuration | the WCN7850 one (`ath12k/wifi7/hw.c:505-505`) | the same (`ath12k/wifi7/hw.c:779-779`) | yes | VERIFIED |
| WMI initialisation | the WCN7850 one (`ath12k/wifi7/hw.c:507-507`) | the same (`ath12k/wifi7/hw.c:781-781`) | yes | VERIFIED |
| Hardware operations | `wcn7850_ops`; the two sets differ in one slot, the RX DMA ring selection (`ath12k/wifi7/hw.c:165-185`) | `qcc2072_ops` | one slot | VERIFIED |
| HAL register table | `wcn7850_regs` (`ath12k/wifi7/hal_wcn7850.c:174-261`): 63 offsets | `qcc2072_regs` (`ath12k/wifi7/hal_qcc2072.c:9-97`): 63 offsets, 18 equal and 45 different (TCL, REO, WBM, PPE and PCIe offsets) | no | VERIFIED (counted from the two tables) |
| CNSS feature bits | QDSS-config-missing and PERST-no-pull (`ath12k/wifi7/hw.c:509-510`) | the same two plus AUX-UC support (`ath12k/wifi7/hw.c:783-785`) | no | VERIFIED |
| Hardware RF-kill pin | 48 (`ath12k/wifi7/hw.c:512-512`) | 0 (`ath12k/wifi7/hw.c:787-787`) | no | VERIFIED |
| IOVA mask | the payload-size mask (`ath12k/wifi7/hw.c:528-528`) | 0 (`ath12k/wifi7/hw.c:803-803`) | no | VERIFIED |
| Board-name search | bus and board (`ath12k/core.c:256-296`) | the same | yes | VERIFIED |

What this implies for reuse is INFERRED: the host-side protocol layers (MHI, QMI/WLFW, copy engine, HTC, WMI) are shared by
Linux and are therefore likely shared by a driver that follows Linux; the HAL register map, the PCI window and the identity
gates are chip-specific; the auxiliary-microcode step and the firmware container differ. None of it has run on this module.

### 5.2 Layer by layer

| Layer | A16 piece | Class | Why, and what the SP11 needs |
| --- | --- | --- | --- |
| Monitor device contract | `compat/qcc2072-wifi.h`: five allow-listed windows (BAR0, 64 MiB DMA arena, BAR2, one read-only ECAM page, a read-only control page), the command-register stores of 2 and 6, and the admission record | **needs-parameterization** | The design (stage-2 allow-list, read-only endpoint page, monitor-performed command stores, record check) carries over. Every number is an A16 constant: `compat/qcc2072-wifi.h:54-64` and the fixed record words with the PCI ids and the stream (`compat/qcc2072-wifi.h:111-111`). The SP11 needs descriptor-derived windows and a chip-neutral record. Needed only from X12e |
| Window and SMMU handling | `probes/qcc2072wifi.py`: `SMMU = 0x15480000` and `SID = 0x40100`, `walk_smmu`, the reservation at `0xdc000000` | **needs-parameterization** | The SMMU is at `0x15400000` here (WE12); the DRAM map is not yet measured; `walk_smmu` accepts only a stage-2-only entry (`probes/qcc2072wifi.py:92-92`, `probes/qcc2072wifi.py:301-340`) and so refuses a bypass state |
| q1n1 and host bring-up | `probes/qcc2072wifi.py` `admit` (identity, BARs, command register, SMMU, MHI snapshot, PBL/EDL check) and `stop` | **needs-parameterization** | The MHI snapshot and the PBL/EDL rule are generic; the identity check pins the ASUS ids (`probes/qcc2072wifi.py:61-61`, `probes/qcc2072wifi.py:343-372`) |
| Host harness | the "host-proxy harness" that scans and connects from the Mac | **unknown** | `probes/qcc2072wifi.py` and its test name `re/host-proxy/run.py`, which is not in the public SnapQCC2072 tree at the commit read; its source is therefore unavailable to this study |
| Firmware and board-data staging | the three pinned inputs of `probes/qcc2072wifi.py:82-84`; `tools/inspect-qcc2072-asus-board.py`; `tools/build-qcc2072-native.py` | **chip-specific-rewrite** (data); the staging idea carries over | Every pin is a QCC2072 file (section 5.3). The SP11 needs its own pins (section 6). `README.md:144-146` records the ASUS board file as `bdwlan_qcc2072_1p0_ncm820A.elf` |
| Native macOS driver, protocol layers | SnapQCC2072 MHI, QRTR, QMI/WLFW, copy engine, HTC, WMI, scan, connect, WPA2 | **carries-over** (INFERRED) | 50 of 64 source files carry no chip literal; Linux shares the same parameters between the two chips (5.1) |
| Native macOS driver, chip-specific files | SnapQCC2072 `qcc2072-datapath.c`, `-reo.c`, `-ce.c`, `-dp-io.c`, `-transport.h` and the identity gates `-firmware.{c,h}`, `-qmi.c`, `-wlfw.c`, `-dma.c`, `-provider.cpp`, `-provider-record.h` | **chip-specific-rewrite** or **needs-parameterization** | 14 of 64 files carry the window register, absolute ring addresses or the A16 PCI ids (Appendix A) |
| Skywalk and IO80211 glue | `SnapintoshQCC2072{,Interface}.{cpp,h}`, `native-*`, `Info.plist` | **carries-over** (INFERRED) | No chip literal. Bound by the macOS 26A428 class headers, not by the chip |
| `cycle.py` | `--qcc2072-wifi`, `--qcc2072-auxkc`, `--qcc2072-auxkc-sha256` | **unknown** | A16-only options (`tools/cycle.py:343-354`); the `sp11` target runs only passive checks, so they cannot run there |
| Board descriptor | `config/boards/a16.json` lists the two BAR ranges as `qcc2072-bar0` and `-bar2` | **needs-parameterization** | `config/boards/sp11.json` lists `wifi` as present with no range, and says presence does not imply that any existing driver applies (`config/boards/sp11.json:188-192`; the A16 side is `config/boards/a16.json:32-33`) |

### 5.3 The A16 pins, checked against linux-firmware

The A16 loader pins three inputs by SHA-256 (`probes/qcc2072wifi.py:82-84`). Two of them are public files, and the
comparison with the pinned linux-firmware commit is a reproducible fact:

| A16 pin | Equals | Label |
| --- | --- | --- |
| `firmware-2.bin`, `4c6a1be1f5bfad76...` | `ath12k/QCC2072/hw1.0/firmware-2.bin` at the pinned commit (7148724 bytes). The comment beside `SOURCES` in the loader names `WCN7850/hw2.0` instead; at this revision it is stale, and `docs/ARCHITECTURE.md:203` (which names `QCC2072/hw1.0`) is right | VERIFIED |
| `regdb.bin`, `97521073...` | the regulatory entry (`bus=pci`, 24,310 bytes) of the QCC2072 `board-2.bin` | VERIFIED |
| `board.elf`, `eeb1311e...` | the ASUS board file from ASUS's Windows driver: not public, not compared | UNKNOWN |

The QCC2072 `board-2.bin` has no entry for the A16's subsystem (vendor `0x105b`, device `0xe14f`): the entry it has for
vendor `0x105b` is device `0xe15a`. That is why the A16 flow brings its own board file, and it shows the same gap can occur
on the SP11. The SP11's pins would all differ: WCN7850 has no `firmware-2.bin` at this commit and its regulatory entry is a
different 24,310-byte block.

### 5.4 Recommendation: extend SnapQCC2072 with a second chip profile (the operator decides)

| Option | What it is | Against AGENTS.md | Cost and risk |
| --- | --- | --- | --- |
| A (recommended) | A chip profile in SnapQCC2072: a table keyed by PCI device id and hardware version (`wcn7850-hw2.0`, `qcc2072-hw1.0`) that carries the identity gate, the window register, the HAL register offsets, the firmware names and the auxiliary-microcode flag. The QCC2072 profile is the default and byte-identical | Extends the existing implementation ("No Parallel Implementation Proliferation"); keeps the driver in its own repository ("Respect Subsystem Ownership"; change SnapQCC2072 first, then a separate pointer bump) | 14 files to parameterize; the repository name becomes a misnomer (renaming is a separate decision); the A16 build must be proved unchanged |
| B | A new component for the WCN7850 | Duplicates about 11,000 lines of mostly chip-neutral code: a parallel implementation, allowed only with an explicit migration plan | Cleaner naming; two copies to maintain; the generic layers drift |
| C | Factor the chip-neutral layers into a shared library, with one thin package per chip | Satisfies both rules | The largest refactor of a driver whose macOS menu integration is still unfinished (`docs/STATUS.md:38-39`); revisit after the A16's menu join works |
| D | Reuse or adopt UniXNU's Ath12kWifi (Linux ath12k, mac80211 and cfg80211 on XNU) for the WCN7850 path, as a separate component (section 11) | A third Wi-Fi path beside SnapQCC2072: allowed only with an explicit migration plan ("No Parallel Implementation Proliferation"); its GPL-2.0 pieces stay in a separate component and never enter an MIT file (rule 4); its glue carries no licence | Unknowns to settle first: the licence of the glue, the OS interface of macOS 26A428 (its layouts are for iPadOS 24A446), an embedded kernel build against this project's stock-kernel rule, and no macOS verification. Gains: Linux's own WCN7850 layers, which have run on a device, and a worked power-up sequence |

The recommendation for code that is this project's own stays A, and the decision is the operator's. D is neither recommended
nor rejected here: section 11.6 lists the four preconditions the owner would settle first. Neither A, B nor C needs the SP11
hardware to start: the chip-neutral work can be done and tested against the A16. The facts of section 11 (the K6 sequence on
SM8550, the PHY table that equals Linux's, the three firmware files, the shape of the `nw_abi.h` boundary) are reading
material for every option, and none of them replaces SnapQCC2072.

## 6. Firmware and board data

### 6.1 What the Linux flow needs for a WCN7850 hw2.0

| File | Where | Needed | Why | Label |
| --- | --- | --- | --- | --- |
| `amss.bin` | `ath12k/WCN7850/hw2.0/` (6090816 bytes) | required | the main firmware image, downloaded over MHI; Linux tries `firmware-2.bin` first and falls back to the separate files (`ath12k/fw.c:158-170`; `ath12k/hw.h:91-100`) | VERIFIED |
| `m3.bin` | the same directory (299660 bytes) | required | the M3 image, handed to the firmware by QMI (`ath12k/qmi.c:3845-3895`) | VERIFIED |
| `board-2.bin` | the same directory (2431968 bytes) | required | the board-data and regulatory-database container (section 6.2) | VERIFIED |
| `firmware-2.bin` | none at this commit for WCN7850 | not present | of the four chip directories under `ath12k/` at the pinned commit (IPQ5424, QCC2072, QCN9274, WCN7850) only QCC2072 and QCN9274 carry one | VERIFIED |
| auxiliary microcode | none | not needed | the WCN7850 entry sets the download off (`ath12k/wifi7/hw.c:461-461`) | VERIFIED |
| calibration data | none | not needed | the entry does not download calibration | INFERRED |
| `ncm865/amss.bin`, `ncm865/m3.bin` | `ath12k/WCN7850/hw2.0/ncm865/` | not needed unless the unit's tree says so | a different firmware build, selected only when the device node has `firmware-name = "ncm865"` (`ath12k/core.h:1343-1357`; the Denali node has none, `x1-microsoft-denali.dtsi:998-1001`) | VERIFIED |
| `LICENSES/LICENSE.qcom`, `NOTICE.qcom` | the repository root | read before staging | `WHENCE` points the ath12k block at them | VERIFIED |

The firmware versions `WHENCE` lists are `WLAN.HMT.1.1.c7-00108-QCAHMTSWPL_V1.0_V2.0_SILICONZ_UPSTREAM-3` for the default
set, `WLAN.IOE_HMT.1.1-00018-QCAHMTSWPL_V1.0_V2.0_SILICONZ-1` for `ncm865`, and `WLAN.COL.1.0.c2-00277-QCACOLSWPL_V1_TO_SILICONZ-1`
for QCC2072. The community unit reports firmware version `0x1103006c` (WE6); this study does not map that number to any of them.

### 6.2 How the board data and the regulatory database are chosen

| # | Claim | Label | Source |
| --- | --- | --- | --- |
| WE29 | For a WCN7850 or QCC2072 the board name is `bus=pci,vendor=VVVV,device=DDDD,subsystem-vendor=SSSS,subsystem-device=TTTT,qmi-chip-id=N,qmi-board-id=M`, with `,variant=NAME` appended when a calibration variant is known. The subsystem ids come from configuration space; the chip and board ids come from the firmware's QMI target-capability reply (the board id is 255 when the reply has none). | VERIFIED | `ath12k/core.c:256-296`; `ath12k/qmi.c:2928-2976`; `ath12k/qmi.h:36-36` |
| WE30 | The variant comes from the device node's `qcom,calibration-variant` or from SMBIOS. The Denali node has neither, and the WCN7850 binding does not allow the property: the Denali is looked up without a variant. | VERIFIED | `ath12k/core.c:741-743`; `ath12k/core.c:832-848`; `qcom,ath12k.yaml:14-28`; `x1-microsoft-denali.dtsi:998-1013` |
| WE31 | The lookup in `board-2.bin` is by exact name, first with the variant (if any), then without it, and last the file `board.bin`. The container is the magic `QCA-ATH12K-BOARD` plus a NUL, then elements of an id, a length and padded data. | VERIFIED | `ath12k/core.c:432-541`; `ath12k/core.c:557-614`; `ath12k/hw.h:304-319` |
| WE32 | The board data is sent to the firmware as an ELF image if it starts with the ELF magic, otherwise as a binary; the regulatory database is sent first, as its own type. A Windows `bdwlan*.elf` file is therefore already in a form the firmware accepts. | VERIFIED (the code path); INFERRED (that a Windows file is accepted) | `ath12k/qmi.c:3130-3156`; `ath12k/qmi.c:3845-3895`; `ath12k/qmi.h:49-52` |
| WE33 | The regulatory database is looked up in the same `board-2.bin`: first under the full board name, then under the name `bus=pci`, then in `regdb.bin`. At the pinned commit `WCN7850/hw2.0` has no `regdb.bin`; its regulatory entry is the `bus=pci` element of `board-2.bin` (24,310 bytes). | VERIFIED | `ath12k/core.c:616-660`; section 1 |
| WE34 | The exact name the community unit reports, `bus=pci,vendor=17cb,device=1107,subsystem-vendor=17cb,subsystem-device=1107,qmi-chip-id=2,qmi-board-id=255`, **is** an entry of the pinned WCN7850 `board-2.bin` (one of three names sharing an 88,760-byte block, SHA-256 `d4f92e9f11bc530d7287f346b96ad3f7e56e6ad6c12f2a0977b267a0ee274a6a`). The community note that the container "may not include an exact match" does not hold at this commit. | VERIFIED (the entry); OBSERVED (the unit's ids) | `tools/sp11_wifi_board.py` on the pinned file; denisix `WIFI.md` |
| WE35 | No entry of the WCN7850 `board-2.bin` is named for Microsoft, Surface or Denali. A unit whose subsystem or QMI ids differ from WE34 has no entry and Linux then fails to initialise (`failed to fetch board data`). | VERIFIED (the contents); UNKNOWN (this unit's ids) | section 1; `ath12k/core.c:557-614` |

### 6.3 The Microsoft package

Names only, relayed to this study: `qcwlanhmt/{wlanfw20.mbn, phy_ucode20.elf, regdb.bin, Data20.msc,
qdss_trace_config_v1.cfg, qdss_trace_config_v2.cfg, bdwlan.elf, bdwlan.e36, bdwlan.e3b, bdwlan01.e02 to e12,
bdwlan_wcn785x_2p0_ncm825*.elf (about 13 variants named after OEM boards), bdwlan_wcn785x_2p0_ncm865a*.elf}`, the extension
INF `qcwlanhmtext/qcwlanhmt_ext8380.inf` and `miscwlanext/miscWlanExt.inf`.

| Package file | Linux counterpart | Needed | Label |
| --- | --- | --- | --- |
| `wlanfw20.mbn` | `amss.bin` | no: the Linux flow loads its own image; whether the firmware pairs with the Linux host flow is UNKNOWN | UNKNOWN |
| `phy_ucode20.elf` | none for WCN7850 (the auxiliary microcode is QCC2072's) | no | INFERRED |
| `regdb.bin` | the `bus=pci` regulatory element | no | INFERRED |
| `bdwlan_wcn785x_2p0_*.elf` | a board entry (WE32) | **only as a fallback**, if WE34's name is not the unit's and no other entry matches | VERIFIED (the format path); UNKNOWN (which file fits the unit) |
| `Data20.msc`, `qdss_trace_config_*.cfg` | none (tracing and Windows data) | no | INFERRED |
| the extension INF | the selection rule on Windows | not read by this study | UNKNOWN |

**Nothing from the operator's own disk is needed.** Every required file is public and comes from linux-firmware. The
Microsoft package, which the plan already treats as the public alternative to the unit's driver store (plan section 1.2), is
only the conditional source of a board file.

### 6.4 The plan input row and the regulatory policy

Proposed plan row N9 is in the report ("PROPOSED SHARED-DOC TEXT"). The regulatory database policy, proposed:

1. The database is the `bus=pci` element of the pinned `board-2.bin` (WE33), loaded before the board data, never edited,
   never replaced by a file from another chip or a hand-built one.
2. The country code is never set by this project. Whatever the firmware and the database give is what applies.
3. If the database did not load, or the regulatory domain is unknown or the world domain, nothing transmits: not a scan
   probe, not an association (section 7).
4. The 6 GHz band stays off unless the owner names the region (WE5).

## 7. Safety, authorization and recovery

### 7.1 Rules before the radio transmits (proposed)

| Rule | Why | Evidence |
| --- | --- | --- |
| Transmit nothing until the regulatory database is loaded and the domain is known | a transmitting radio is a regulated emitter; an active scan transmits probe requests | WE33; WE5 |
| Never override the country code | the owner's region is a legal fact the project cannot know | section 6.4 |
| Honour the hardware RF-kill input unless the owner records otherwise | upstream ignores it for `microsoft,denali` because the OS cannot control it and it reads "radio off" (VERIFIED, `ath12k/core.c:73-104`); two community repositories saw "hard blocked" (OBSERVED). Ignoring a kill input is a safety decision, and the stretch can end at X12c without it | WE36 |
| Keep the thermal guard on, on AC power or at least 50% battery | AGENTS.md section 5; a radio adds heat the CPU and GPU model does not see | plan H1 |
| One session, one firmware boot, bounded timeouts, no retry loop | a hung module is recovered by reset or cold boot, never by retrying | WE26; WE27 |
| Bluetooth is never touched: no write to `BT_EN` (GPIO 116), no access to `uart14` | the half of the same module that stays out of scope; the two functions share the PMU | WE18; WE21 |

| # | Claim | Label | Source |
| --- | --- | --- | --- |
| WE36 | Upstream ath12k disables hardware RF-kill for WCN7850 hardware 2.0 on machines compatible with `microsoft,denali` (and two `romulus` boards). Its comment: the OS cannot control the input, which reads "radio off". | VERIFIED | `ath12k/core.c:73-104` |
| WE37 | Two community repositories report the Wi-Fi radio "hard blocked" on the SP11 until a patch ignores the input; one adds the missing `disable-rfkill` device-tree property. | OBSERVED (one unit each) | denisix `WIFI.md`; ooaklee `adr-0018` |

### 7.2 Authorizations proposed (rows for the plan's section 8)

| Operation | Why it needs authorization | First gate | Executor |
| --- | --- | --- | --- |
| Write the Wi-Fi function's PCI command register (memory decode, then bus master) | A configuration write to a device the firmware owns; bus mastering lets the card DMA, and the SMMU offers no isolation beyond the firmware's table (the A16 records the same limit) | X12b (memory decode); X12c (bus master) | Per run, explicit |
| Load firmware onto the Wi-Fi SoC (BHI/BHIe download, QMI handshake) and run it | Runs Qualcomm firmware from local files; the module then needs a reset or a cold boot to return to its boot ROM | X12c | Per run |
| Radio transmit: any scan, association or data frame | Regulatory; the rules of 7.1 | X12d | Per run, explicit; the owner names the region |
| Ignore the hardware RF-kill input | A safety decision; upstream's table is a precedent, not an authorization | X12d | The owner, recorded |
| Reset the module through BAR0 (the SoC global reset of WE27) | A device write that resets a radio SoC; EXPERIMENTAL on this module | X12c | Per run |
| Pass the function through to the guest (stage-2 windows for BAR0, BAR2, an ECAM page and a DMA arena) | The card's DMA reaches whatever the firmware's SMMU state allows | X12e | Per run |
| Power the module, train the PCIe link or program the PCIe controller, PHY, SMMU or an RPMh rail from q1n1 or the monitor | New hardware writes on a part the firmware left off; this is the K6 branch and needs a separate design study first | not before that study | The owner |

### 7.3 Stop, quiesce and recovery

- **Stop.** K6 (section 4). Any all-ones read after the first successful one, any unexpected execution environment, a
  timeout, or a thermal trip ends the session at once; there is no retry.
- **Quiesce.** After a session: bus mastering off with memory decode left on, read back (the A16's `stop`,
  `probes/qcc2072wifi.py:394-399`); then, only if authorized, the SoC global reset of WE27. Both are EXPERIMENTAL here.
- **Recovery.** A cold boot returns the module to its boot ROM. The A16 flow already requires it after any run that started
  firmware (`probes/qcc2072wifi.py:27-28`), and the community unit needed it after a warm reboot (WE26). A bus reset is not a
  recovery step for this device (WE27).
- **Windows.** Windows must boot normally after every X12 session, the same criterion as the NVMe window (plan S3 and
  `SP11-NVME-WINDOW.md` D11). Whether it does is UNKNOWN.
- **First power-up of a device the firmware left off** is not authorized by any row here: it is the K6 branch.

## 8. Parity target and gate X12

The parity target is whatever the A16 does at the time. Today (`docs/STATUS.md:17`, `README.md:42`): scan, a WPA2-PSK
connection, DHCP and ping through the host-proxy harness; the native controller loads in the guest; joining from the macOS
Wi-Fi menu does not work yet.

| Sub-gate | Goal | Needs | Exit (falsifiable) | Stop |
| --- | --- | --- | --- | --- |
| X12a | The read-only hand-off survey of section 3 | the X2 image (or the proxy session after X3); no guest | A record of S0 to S5 with a verdict of PRESENT-LINKED, PRESENT-UNLINKED, ABSENT, FAULT or INCONSISTENT, the raw values, and the X2 image's own no-write audit unchanged | K6 on any verdict but PRESENT-LINKED |
| X12b | Admission through q1n1 and the host link | q1n1 and the host link (after X3) | The chip profile resolves from vendor, device and a hardware-version major of 2; the command-register memory-decode write reads back; the MHI registers read; the execution environment is PBL or EDL; the DMA arena is proven reachable or the SMMU state documented; every faulty case of the admission tests refuses | K6 (another chip), any unexplained state: cold boot and report |
| X12c | Firmware boot through the host-proxy harness | the same | `tools/sp11_wifi_board.py find` found the entry for the measured name before the run; the firmware downloads; the QMI target reply reproduces the measured board name; the ready event arrives; the module is quiesced and Windows still boots | the entry is missing and the owner declines the fallback; no ready event within the bound |
| X12d | Scan, WPA2-PSK, DHCP and ping through the harness (the A16's current parity) | the same, plus the radio and RF-kill authorizations | The regulatory database loaded, the country untouched, the thermal guard on, a scan listing networks, an association, a DHCP lease and a ping answered; the passphrase from the Mac's keychain, never the command line | the RF-kill decision is withheld; the regulatory domain is unknown; a thermal trip |
| X12e | The controller loads and attaches in the guest | the guest after X7 | The monitor's stage-2 allow-list is built from descriptor values and refuses everything else; the kext attaches the function; the A16 default build is byte-identical (the firewall compare) | a DMA reach or isolation finding |
| X12f | Parity with the A16's macOS state | the desktop after X10 | Whatever `docs/STATUS.md` says the A16 does at that time; if the A16 has not reached a menu join, X12f closes at X12e | none beyond K6 |

X12a to X12d need only q1n1 and the host link, so they may start once X3 closes (the plan's overlap rule), each in its own
supervised session; X12e and X12f need the guest (X7 and X10). One hardware client at a time still applies.

## 9. Descriptor proposal

A proposal only: this work edits neither `config/boards/*.json` nor `tools/board_descriptor.py`. The shipped SP11 descriptor
lists `wifi` as present with no range (`config/boards/sp11.json:188-192`). A `wifi` group would carry the fields the monitor
or q1n1 must know to check a measurement; GPIOs, rails and the DMA arena are not descriptor material (below).

```json
"wifi": {
  "chip": null,
  "segment": null,
  "endpoint": null,
  "bus_range": null,
  "pci_id": null,
  "bars": null,
  "smmu": null,
  "stream": null,
  "firmware": null
}
```

| Field | Value once measured | Label |
| --- | --- | --- |
| `chip` | a profile id, `wcn7850-hw2.0`, resolved from the measured vendor and device and the hardware-version major (never taken from the descriptor alone) | INFERRED until X12a and X12b |
| `segment` | the `pcie.ecam[].segment` that hosts the module | UNKNOWN until X12a |
| `endpoint` | `{"bus": 1, "device": 0, "function": 0}` | INFERRED (WE1, WE9) |
| `bus_range` | the root port's secondary to subordinate bus as the firmware assigned them | UNKNOWN until X12a |
| `pci_id` | `{"vendor": "0x17cb", "device": "0x1107", "subsystem_vendor": null, "subsystem_device": null}`; the subsystem ids are measured | UNKNOWN until X12a |
| `bars` | the BARs as programmed (index, base, size), never sized by us; the A16's analogue is `qcc2072-bar0` and `-bar2` | UNKNOWN until X12a |
| `smmu` | the `smmu[].name` that serves it (`pcie-smmu`), so ambiguity is stated | VERIFIED (name); UNKNOWN (state) |
| `stream` | `{"rid_base": "0x0", "rid_count": "0x10000", "stream_base": "0x40000"}` | INFERRED (WE12) |
| `firmware` | file names only (`amss.bin`, `m3.bin`, `board-2.bin`); hashes belong to the per-run pins, as for the A16 | VERIFIED (names) |

**Not descriptor material.** The enable and reset GPIOs (214, 117, 116, 146) and the rails: EL2 does not drive them in any
sub-gate except the K6 branch, which needs its own study; if the owner approves that branch they become a `power` group with
provenance. The DMA arena and the control page are monitor reservations chosen per run from the measured DRAM map, as the
A16's `RESERVATION` is. The ECAM base is `pcie.ecam` from the MCFG. The record, its generation and cookie are per-run pins.
Interrupts: the A16 driver polls (`snap-qcc2072/src/qcc2072-datapath.c:183-183`), which avoids the ITS question of WE11.
Board-info ABI 1.0 has no room for this group: a minor bump would add a `KNOWN_WIFI` bit and a group sized when the fields
settle, appended after the 1.0 layout.

## 10. Per-machine questions

Nothing below was measured on a Surface Pro. "Gate" is where the plan would measure it.

| # | Question | Public evidence | Measurement (gate) | Expected | Label |
| --- | --- | --- | --- | --- | --- |
| W1 | Is the module a WCN7850? | WE1 to WE6 | C4 and the hardware-version major (X12a, X12b) | `0x110717cb`; major 2 | VERIFIED (tree); INFERRED (unit) |
| W2 | Which segment, ECAM base and BDF? | domain 4 is Linux's number; `reg = <0x10000 ...>` | MCFG, then the root-port walk (X12a) | endpoint at bus 1, device 0, function 0 | INFERRED |
| W3 | Which subsystem ids? | the community's `17cb:1107` (one unit) | C8 (X12a) | `0x17cb`, `0x1107` | OBSERVED |
| W4 | Is it powered? | the 3.3 V switch is `boot-on` | T1, T2, G1 to G4 (X12a) | GPIO 214 and 117 high | UNKNOWN |
| W5 | Is the link trained? | none for the SP11 | C3 (X12a) | DLLLA set, Gen3 x2 | UNKNOWN |
| W6 | Which BARs, and is memory decode on? | none | C5, C7 (X12a) | recorded | UNKNOWN |
| W7 | Are PERST#, CLKREQ# and WAKE# on GPIO 146 to 148? | an unreferenced pin state names them (WE15) | T4 to T6 (X12a) | recorded | UNKNOWN |
| W8 | What does the PCIe SMMU do for StreamID `0x40100`? | "Controlled by Gunyah"; the overlay's map (WE12) | S5 (X12a) | translate, bypass or abort | UNKNOWN |
| W9 | Can a 64 MiB arena be reached by the card? | none | the S5 walk, then the arena check (X12b) | identity read/write below `1 << 36` | UNKNOWN |
| W10 | Is the module in its boot ROM? | the A16 refuses otherwise; a warm reboot may leave firmware running (WE26) | MHI and BHI reads after the authorized memory-decode write (X12b) | PBL or EDL | UNKNOWN |
| W11 | Which board entry will the firmware ask for? | the community's chip 2, board 255 | the QMI target reply (X12c) | chip 2, board 255 | OBSERVED |
| W12 | Does the pinned `board-2.bin` hold that name? | yes for the community's name (WE34) | `tools/sp11_wifi_board.py find` (host) | FOUND | VERIFIED (name); UNKNOWN (unit) |
| W13 | What does hardware RF-kill read? | upstream ignores it on Denali (WE36) | the firmware's RF-kill state after the ready event (X12c) | radio off | OBSERVED |
| W14 | Does Windows boot after a session? | none | after the quiesce (X12c) | yes | UNKNOWN |
| W15 | Is a WLAN-only power-up independent of Bluetooth? | separate enables, shared PMU (WE18, WE21) | none: out of scope | n/a | UNKNOWN |
| W16 | What does the radio add to the thermal load? | none | the thermal guard's log during X12d | below the owner's ceiling | UNKNOWN |
| W17 | Does the module need a host-supplied sleep clock, and who supplies it? | the Denali PMU node describes none (WE18); UniXNU's SM8550 board switches on a PMIC pin for it (WE47) | none in the survey (it reads no PMIC state); the K6-branch study | recorded | UNKNOWN |
| W18 | Which Wi-Fi address does the firmware report? | UniXNU's board: the firmware reports none, so the board derives one (WE50) | the ready reply (X12c) | a non-zero address, or none | UNKNOWN |
| W19 | How much DMA memory does the firmware ask the host for, and does a 64 MiB arena suffice? | UniXNU's 48 MiB window was too small for the mainline firmware, copy-engine and data-path allocations and 128 MiB works (WE49); the A16's arena is 64 MiB for another chip | the firmware's memory requests (X12c); the arena is chosen from the DRAM map (X12b) | at most the arena | OBSERVED (another board); UNKNOWN (this unit) |
| W20 | Do the endpoint's MSIs arrive through the controller's own receiver, or can the driver poll? | the SP11 tree lists eight own-receiver lines, SPI 141 to 148 (WE11); UniXNU's board uses the receiver at SPI 141 (WE48); the A16 driver polls | the receiver's registers (X12b), delivery (X12e) | delivery observed, or polling sufficient | UNKNOWN |

## 11. UniXNU Ath12kWifi as a reference or reuse path for the SP11 WCN7850

UniXNU, a public project the operator pointed at, has a component, `Ath12kWifi`, that runs Linux's ath12k, mac80211 and
cfg80211 on XNU for the same chip family. This section reads it as evidence and as a candidate path, at the commits of
section 1. No code and no prose is copied: every statement is a fact read from a named file, or a count that
`tools/test-sp11-wifi-unixnu.py` recomputes. **This study does not recommend replacing SnapQCC2072; the owner decides (section
11.6).**

### 11.1 What it is

| # | Claim | Label | Source |
| --- | --- | --- | --- |
| WE38 | UniXNU describes itself as an independent project that runs iOS and macOS on non-Apple ARM64 hardware through UEFI. Its component catalog lists `Ath12kWifi` as a driver for "qcc2072, qcn9274, and wcn7850 build inputs", with the OS interface `nw_abi platform interface`, the board dependencies "PCI configuration, BAR, MSI, and DMA operations", the build stage `embedded` and an empty `verified_devices` list. The stack is compiled into the project's own kernel build. | VERIFIED (what the catalog and the docs say) | `UniXNU/README.md:24-24`; `UniXNU/components.toml:474-493`; `Ath12kWifi/README.md:83-84`; `UniXNU/docs/porting/network.md:87-96` |
| WE39 | The project's own summary: ath12k, mac80211 and cfg80211 from Linux run on XNU; MHI, QRTR and QMI start the device; wpa_supplicant 2.12 authenticates in userspace (selected profiles: open and WPA2-PSK with PMF); ordinary packets stay in the kernel; the board supplies power, PCIe, BARs, MSI, DMA, firmware and the network interface. | VERIFIED (the statement, not the behaviour) | `UniXNU/docs/drivers/ath12k-wifi.md:3-13`; `Ath12kWifi/README.md:3-5` |
| WE40 | The upstream sources are fetched at build time, not stored: the lock file names Linux stable v7.0.14 (commit `458c6079fc1d41d564c37679c8ace02cd83ee817`), the wpa_supplicant 2.12 archive (the SHA-256 of section 1) and FreeBSD `releng/15.1` headers (commit `95719d8a895bf8170851e9844d9b4092b1d2b6fe`); the fetched trees and the build output live in `deps/` and `out/`. The project's earlier hand-written station, mac80211 and crypto code is retired for production and kept for legacy fixtures. | VERIFIED | `Ath12kWifi/README.md:13-19`; `Ath12kWifi/tools/source-lock.json:3-37`; `Ath12kWifi/tools/source-lock.json:56-59` |
| WE41 | The boundary between the stack and a board is plain C. `lx_platform` holds 17 callbacks (three clocks, sleep, poll, log, halt, heap allocate and free, four DMA operations, firmware by file name, a busy delay and two preemption hooks). `nw_board` holds the mapped BAR, the PCI device id, the MSI capability offset and first vector, config-space read and write, the MSI target and a physical mapping. The station calls are scan, connect, disconnect, transmit, power and status, with events for scan done, connect done, link loss, receive and link update. | VERIFIED | `Ath12kWifi/include/nw_abi.h:9-35`; `Ath12kWifi/include/nw_abi.h:46-89` |
| WE42 | Linux's PCI layer is replaced by a shim of the project's own: the driver is probed once with the endpoint the board found; the shim fixes the vendor id `0x17cb`, takes the device id from the board, sets a 32-bit DMA mask, reads the subsystem ids that select the board data and asks the device for the smallest maximum read request (128 bytes). The glue repository is 261 files and 13,312 lines (Appendix B). | VERIFIED | `Ath12kWifi/src/nw_pci.c:53-64`; Appendix B |
| WE43 | The project says the component contains QCC2072 and QCN9274 definitions besides the WCN7850 reference, and that the endpoint's hardware revision and firmware support must match a definition before it is selected. At the Linux commit it locks, ath12k's `wifi7/` directory holds the register tables of all three chips, so one tree covers the A16's chip and the SP11's. No record this study read shows the component running on a QCC2072 or on an X1E80100 machine. | VERIFIED (statement, files); INFERRED (that one tree serves both through the shim); UNKNOWN (any such run) | `UniXNU/docs/porting/network.md:26-29`; `lock-7.0.14/ath12k/wifi7/hal_wcn7850.c:1-1`; `lock-7.0.14/ath12k/wifi7/hal_qcc2072.c:1-1`; `lock-7.0.14/ath12k/wifi7/hal_qcn9274.c:1-1` |

### 11.2 Layer by layer against SnapQCC2072

The layers are those of section 5.2. The class names are the ones the comparison needs: **chip-generic** is code that
follows the chip family and comes from Linux unchanged; **board-specific** is what a given SoC and board must supply (power,
PCIe, SMMU, interrupts, DMA, where the firmware is stored); **OS-ABI-specific** is what binds to one XNU build and its
userland. Evidence ids point at sections 11.1 and 11.3.

| Layer | SnapQCC2072 (this project) | UniXNU Ath12kWifi | Class of its piece | For the SP11 | Label |
| --- | --- | --- | --- | --- | --- |
| 1. Device contract and access | The monitor admits one function through an allow-list of stage-2 windows and a record; q1n1 checks identity first (section 5.2) | No hypervisor: the board code maps the controller, configuration space and BAR itself and hands the stack `nw_board` (WE41, WE44) | board-specific | Nothing carries over. The SP11 needs the monitor's contract (needs-parameterization, from X12e) to play the part `nw_board` plays here | INFERRED |
| 2. Power-up, reset and link training | None: the A16 firmware leaves the card enumerated and admission refuses anything else (WE24) | The board's own sequence: WLAN_EN, rails through RPMh, clocks, PHY tables, PERST#, link training (WE44, WE45) | board-specific | The K6 branch (section 4). It shows the sequence is finite and what it held on an SM8550 tablet; its addresses, six rails and PMIC sleep clock are that board's | VERIFIED (their code); INFERRED (relevance) |
| 3. PCIe SMMU, DMA arena and MSI | The monitor reserves a 64 MiB arena and allows its stage 2; the driver polls (section 9) | The OS driver owns an SMMU stage-1 window (128 MiB) and the controller's own MSI receiver (WE48, WE49) | board-specific | The SMMU is Gunyah's (WE12): table ownership, arena size and the MSI path are open (W8, W9, W19, W20) | UNKNOWN |
| 4. Bring-up stages and host harness | q1n1 `admit` and `stop`, and the Mac-side proxy harness (section 5.2) | Boot-argument stages (a read-only look, power and link, the endpoint, then the stack) and host fixtures with simulated radio operations (WE44, WE58) | board-specific | No code carries over. The staged order matches the survey of section 3, and a simulated radio is a pattern for X12 host tests | INFERRED |
| 5. Firmware and board-data staging | Three pinned QCC2072 inputs staged through the host (section 5.3) | The three WCN7850 hw2.0 files under their Linux names, read from the data volume; a manifest whose sizes and hashes equal this study's pins (WE51) | chip-data (the files) and an OS-ABI-specific loader | The files are the study's pins; staging stays host-side through q1n1 for X12c | VERIFIED |
| 6. Protocol layers: MHI, QRTR, QMI/WLFW, copy engine, HTC, WMI | The project's own C code, in files that carry no chip literal (Appendix A) | Linux's ath12k, MHI, QRTR and QMI code from the pinned tree, with the shims of WE42 | chip-generic | Would replace SnapQCC2072's layers, not extend them: this is where option D differs from option A (section 5.4). The lock's tree carries the QCC2072 too (WE43) | INFERRED |
| 7. Chip-specific tables and identity gates | 14 of the 64 files carry chip literals (Appendix A) | Linux's per-chip tables, chosen by the device id the board reports (WE42, WE43) | chip-generic | WCN7850 hardware 2.0 is covered by Linux (section 5.1); nothing of this project's to write | VERIFIED (tables exist); INFERRED (through the shim) |
| 8. 802.11 station, authentication and crypto | Native station, scan, connect, WPA2 and RSN code in the kext | mac80211 and cfg80211 in the kernel and wpa_supplicant 2.12 in a userspace service behind a control endpoint (WE39) | chip-generic (the protocol) with an OS-ABI-specific service | Gains Linux's aggregation and reordering; costs a signed userspace service on macOS (UniXNU packages one for iPadOS, WE53) | INFERRED |
| 9. OS network interface: Skywalk, IO80211, Apple80211 | `SnapintoshQCC2072` and its interface class, bound to the macOS 26A428 headers (section 5.2) | `WifiNet.cpp`: `en0` and Apple80211 requests with iPadOS 24A446 layouts, and a macOS branch (WE53) | OS-ABI-specific | Not usable for 26A428 as it is: layouts and the daemon's conditions are per build, and no record verifies the macOS branch (WE57) | VERIFIED (code); UNKNOWN (macOS run) |
| 10. Packaging and loading | A kext in the auxiliary collection against the stock kernel (section 5.2) | Linked into the kernel image the project builds (`embedded`), plus a signed service and a launchd cache merge (WE38, WE53, WE54) | OS-ABI-specific | Conflicts with the stock-kernel design. A kext form of the glue is new work with an arm64e kext ABI proof; the 13 kernel imports its audit reports (WE58) are a count, not a compatibility result | INFERRED |

By class of the UniXNU piece the ten rows split into four board-specific, three chip-generic, two OS-ABI-specific and one
mixed row (row 5). Three things follow, all INFERRED. First, the layers that are expensive and risky for the SP11 (rows 1 to
3 and row 10) are the same for both paths: neither SnapQCC2072 nor Ath12kWifi has a board or OS layer for the SP11, and no
UniXNU board layer is usable at EL2 as it stands. Second, the two paths differ in rows 6 to 8: SnapQCC2072 reimplements the
protocol layers, Ath12kWifi takes them from Linux at the price of a large shim layer, a userspace daemon and the licences of
section 11.4. Third, UniXNU's firmware files (row 5) are byte-identical to this study's pins for the SP11, so firmware is the one
input on which the two sources already agree.

### 11.3 Board inputs: what its procedure needs, and what the SP11 plan can supply at EL2

The procedure's own list (`UniXNU/docs/porting/network.md:31-51`) is: controller and PHY windows, lane count, reset sequence,
WLAN enable, PERST#, reference clock, regulators, interconnect votes, SMMU streams and MSI routing; then link training, BAR
assignment, bus-master enabling, a DMA range proved reachable, and MSI delivery confirmed. Its callbacks are time, sleep, poll,
log, memory, DMA and firmware by name, and a mapped BAR, the PCI identity, configuration access and the MSI target
(`UniXNU/docs/porting/network.md:53-62`). The operating-system side adds the Wi-Fi address, channel and regulatory data and the
request layouts the userland reads (`UniXNU/docs/porting/network.md:80-85`).

| # | Claim | Label | Source |
| --- | --- | --- | --- |
| WE44 | On the SM8550 tablet the module is powered and enumerated by UniXNU's own code, not by firmware. A boot argument selects stages: stage 1 is a read-only look at what the boot loaders left; stage 2 powers the module and trains the link; stage 4 programs an outbound window to bus 1, sizes BAR0, gives it an address and turns on memory decode and bus mastering; stage 5 and above hand the endpoint to the stack. Stage 2 drives WLAN_EN low for 150 ms first because "the chip may still run what an earlier start left" (compare WE26). | VERIFIED (code read) | `UniXNU-sheng/package/WifiProbe.cpp:2-12`; `UniXNU-sheng/package/WifiProbe.cpp:1407-1409`; `UniXNU-sheng/package/WifiProbe.cpp:1430-1443`; `UniXNU-sheng/package/WifiProbe.cpp:1477-1484`; `UniXNU/docs/porting/network.md:46-51` |
| WE45 | Power: the board votes six supply rails of the module's PMU itself through RPMh (mode, voltage and enable) and votes the bus clock manager `SN7` for the PCIe path, which Linux's interconnect driver does. The project's reference table says that without the RPMh mode request two rails stay off and the link never trains. | VERIFIED (code); OBSERVED (the table, one board) | `UniXNU-sheng/package/WifiProbe.cpp:296-301`; `UniXNU-sheng/package/WifiProbe.cpp:362-366`; `UniXNU/docs/porting/network.md:109-109` |
| WE46 | The board's 79 PHY initialisation pairs (38 SerDes, 5 TX, 25 RX, 5 PCS, 6 PCS-misc) equal, offset for offset and value for value and in order, the `sm8550_qmp_gen3x2_pcie_*_tbl` tables of the pinned Linux. Linux binds those tables to `qcom,x1e80100-qmp-gen3x2-pcie-phy`, so they are also the tables Linux uses for the SP11's PHY. | VERIFIED (compared by this study's test) | `UniXNU-sheng/package/WifiPcieTables.h:1-37`; `phy-qcom-qmp-pcie.c:2822-2914`; `phy-qcom-qmp-pcie.c:4177-4186`; `phy-qcom-qmp-pcie.c:6533-6534` |
| WE47 | Sleep clock: on that board the PMU's pin control selects GPIO 3 of the PMK8550 as the module's sleep clock, and the board code switches that pin on over SPMI before the PMU is powered. The Denali's PMU node gives the PMU no clock and no `xo-clk` GPIO (WE18), so whether the SP11 module needs a host-supplied sleep clock, and who supplies it, is not established. | VERIFIED (the board's code); UNKNOWN (the SP11) | `UniXNU-sheng/package/WifiProbe.cpp:147-152`; `UniXNU-sheng/package/WifiProbe.cpp:265-268` |
| WE48 | MSI: the board uses the PCIe controller's own MSI receiver (DesignWare registers at `0x820` to `0x830`, the offsets Linux's DesignWare header defines) and attaches the controller's `msi0` line, SPI 141, as a level interrupt. The SP11's `pcie4` lists eight own-receiver lines (SPI 141 to 148) but routes MSIs through an ITS that Gunyah reserves and whose emulation the upstream EL2 overlay calls broken (WE11): whether the receiver is usable there, and whether polling suffices, is not established. | VERIFIED (code, Linux header, tree); UNKNOWN (the SP11) | `UniXNU-sheng/package/WifiProbe.cpp:1063-1068`; `UniXNU-sheng/package/WifiProbe.cpp:1087-1091`; `pcie-designware.h:146-154`; `hamoa.dtsi:3742-3750`; `x1-el2.dtso:28-31` |
| WE49 | DMA and SMMU: the board carves the endpoint's arena, in order, out of a buffer its storage driver mapped for the endpoint's stream, and that driver owns the apps SMMU stage-1 window for the endpoint, which is 128 MiB because the mainline firmware, copy-engine and data-path allocations outgrew 48 MiB. Buffers the endpoint touches sit at addresses aligned to their size rounded up to a power of two, or the module's boot loader refuses the image address. On the SP11 the PCIe SMMU belongs to Gunyah (WE12): who owns its tables, and how large an arena the firmware needs there, is not established. | VERIFIED (code, record); OBSERVED (one board); UNKNOWN (the SP11) | `UniXNU-sheng/package/WifiProbe.cpp:440-441`; `UniXNU-sheng/package/WifiProbe.cpp:644-647`; `UniXNU/docs/devices/sheng.md:267-267`; `UniXNU/docs/devices/sheng.md:272-272`; `UniXNU/docs/porting/network.md:59-61` |
| WE50 | Address and device-tree conventions: the firmware reports no Wi-Fi address on that board, so the board derives a locally administered unicast one from a chip identifier, serves it to the stack as the `wlan_mac.bin` content and publishes it in the device tree, because the wireless daemon aborts a join without it. A value derived from a chip identifier is machine-identifying runtime data and never belongs in a repository. | VERIFIED (code) | `UniXNU-sheng/package/WifiProbe.cpp:951-966`; `UniXNU-sheng/package/WifiProbe.cpp:977-990` |
| WE51 | Firmware: the board's manifest pins `amss.bin` (6090816 bytes), `m3.bin` (299660) and `board-2.bin` (2431968) of `ath12k/WCN7850/hw2.0` with the same SHA-256 values as this study's linux-firmware pins (section 1), and says they match the firmware installed on that board by a read-only comparison. They are read from the data volume under the directory names Linux uses. | VERIFIED (equality, tested); OBSERVED (the provenance sentence) | `UniXNU-sheng/firmware/wcn7850/manifest.json:1-17`; `UniXNU-sheng/package/WifiFirmware.h:6-8`; `UniXNU/docs/drivers/ath12k-wifi.md:48-51` |
| WE52 | Hardware RF-kill: at the Linux commit that Ath12kWifi locks, `core.c` configures ath12k's RF-kill from the firmware's capability bit and an ACPI property only and has no machine table. The table that disables the WCN7850 hardware RF-kill input on `microsoft,denali` (WE36) is in this study's later pin. A build at the lock would therefore leave the input active on the SP11, where two community repositories saw the radio hard-blocked (WE37). | VERIFIED (both files); INFERRED (the consequence) | `lock-7.0.14/ath12k/core.c:72-94`; `ath12k/core.c:73-104` |
| WE53 | OS interface: the board's network layer binds the station calls of `nw_abi.h` to `en0` and to the system's Apple80211 request interface, with iPadOS 27.0.1 (24A446) layouts (association requests of 592 and 656 bytes and a 540-byte asynchronous result). It has a macOS branch, `XNU_TARGET_OS_OSX`, in which `en0` is an `IOEthernetController` of the original IONetworkingFamily and the family's own interface, stack and IO80211 classes are used. The supplicant's build script has a `macos` target (`arm64e-apple-macos27.0`) that the catalog's pinned revision lacks (read at both revisions; only the later one is re-checked), and every build report it writes says `hardware_verified` is false. | VERIFIED (code, script); UNKNOWN (that any of it runs on macOS) | `UniXNU-sheng/package/WifiNet.cpp:2-5`; `UniXNU-sheng/package/WifiNet.cpp:40-50`; `UniXNU/docs/drivers/ath12k-wifi.md:53-57`; `Ath12kWifi/tools/build-supplicant.py:13-13`; `Ath12kWifi/tools/build-supplicant.py:29-29`; `Ath12kWifi/tools/build-supplicant.py:46-46` |
| WE54 | UniXNU's macOS profile is macOS 27.3 build 26C5035r. Its kernel is the project's own build of the XNU 12377.121.6 sources with the Darwin 26 system calls, the macOS KPIs and a macOS build target added, because no Darwin 26 XNU source was published; the profile notes that Apple's own kernel for that release is another version (xnu-13432.60.57). The SP11 plan targets build 26A428 on Apple's unmodified kernel. | VERIFIED (statements) | `UniXNU/profiles/os/macos-27.3-26C5035r/profile.toml:1-18` |

UniXNU's stage 4 sizes BAR0 by writing all ones to it. The survey of section 3 never does: it records the BARs as the firmware
programmed them (C7), so that stage is a pattern, not something to copy.

The table below maps each input onto the SP11. "At EL2" says what the plan's monitor and q1n1 could supply; "UNIMPLEMENTED"
means no code exists and no verified sequence is known.

| Input (its list) | Its SM8550 board | The SP11, public evidence | At EL2 on the SP11 | Settled by | Label |
| --- | --- | --- | --- | --- | --- |
| Controller and PHY windows, lanes | PCIe0, two lanes, trains at Gen2 x1 (`UniXNU/docs/porting/network.md:110-110`) | `pcie4`, Gen3 x2 in the tree (WE8, WE10) | Static facts for the allow-list and the descriptor; nothing programmed | X12a (C3) | VERIFIED (tree); UNKNOWN (state) |
| PHY initial values | 79 pairs, equal to Linux's (WE46) | The same Linux table binds the SP11's PHY (WE46) | Written only in the K6 branch | the K6 study | VERIFIED |
| WLAN_EN, PERST#, WAKE#, 3.3 V switch | GPIO 80, 94 and 96 (`UniXNU-sheng/package/WifiProbe.cpp:7-8`) | GPIO 117 and the GPIO 214 switch (WE16, WE18); PERST# and WAKE# are not wired in the node, GPIO 146 to 148 are candidates (WE15) | GPIO writes, K6 branch only | X12a (T1 to T6) | VERIFIED (tree); UNKNOWN (state, wiring) |
| Reference and sleep clocks | PCIe reference clock on the controller side; a PMIC pin for the sleep clock (WE47) | Reference clock controller-side (WE14); no sleep clock described (WE18) | An SPMI write to a PMIC: no EL2 path exists | the K6 study (W17) | UNKNOWN |
| Regulators | Six PMU rails by RPMh request (WE45) | One RPMh LDO shared with other consumers, a GPIO-switched 3.3 V rail and two fixed regulators (WE16, WE19) | The RPMh request needs the RSC: UNIMPLEMENTED; the GPIO part is a register write | the K6 study | VERIFIED (both trees); INFERRED (the comparison) |
| Interconnect votes | One bus-clock-manager vote, `SN7` (WE45) | Linux lists two paths (WE14); a community EL2 run needed a third for MSIs (WE25) | Votes need the RSC: UNIMPLEMENTED | the K6 study | OBSERVED (community); UNKNOWN (count) |
| SMMU streams | The OS driver programs a stage-1 window (WE49) | Gunyah's SMMU; StreamID `0x40100` by the overlay's map (WE12, WE13) | The monitor must learn the firmware's entry or make its own: UNIMPLEMENTED | X12a (S5), X12b | UNKNOWN |
| MSI routing | The controller's own receiver, SPI 141 (WE48) | Eight own-receiver lines and a reserved ITS route (WE11) | The A16 driver polls; routing an SPI into the guest has no design | X12b (receiver registers), X12e | UNKNOWN |
| BAR and command register | Sized and assigned by the board code (WE44) | The firmware's BARs, measured and never sized (C7); command-register stores are authorized per run (section 7.2) | The monitor or q1n1, as on the A16 | X12b | INFERRED |
| DMA arena | 128 MiB, from a storage driver's buffer (WE49) | A monitor reservation chosen from the measured DRAM map; 64 MiB on the A16 | The monitor (the A16's reservation pattern) | X12b, X12c (W19) | UNKNOWN |
| Wi-Fi address | Derived from a chip identifier and published in the device tree (WE50) | The address the firmware reports is not known (W18) | Outside EL2 | X12c (W18) | UNKNOWN |
| Regulatory data, scan policy, RF-kill | 6 GHz left out of scans (`UniXNU/docs/porting/network.md:113-113`); RF-kill not mentioned | The `bus=pci` regulatory element and no country override (sections 6.4, 7.1); the Denali's RF-kill entry exists at the study's pin, not at the lock (WE52) | Host policy and an owner decision (section 7.2) | X12d | VERIFIED (our rules); OBSERVED (their choice) |

**What this sharpens.** Hand-off (question 2): UniXNU does not depend on what the loaders left. It looks (stage 1), then resets
the module through WLAN_EN and starts again (WE44), so it says nothing about what the Surface firmware leaves, and the survey
of section 3 is unchanged: UNKNOWN until X12a. A powered-off module (question 3): the sequence of section 4 is confirmed as
finite, with five changes. The PHY table is a checked fact (WE46). The rails are another problem: that board needs six RPMh
rails and the SP11 tree shows one RPMh LDO plus GPIO switches (WE16, WE19). The sleep clock (WE47) and the number of
interconnect votes (WE25, WE45) are new unknowns. The stream configuration is the SMMU's, not a Linux controller step (WE22).
Today the plan can supply at EL2 only static facts and the A16-style contract pieces (windows, command-register stores, an
arena reservation, host-side firmware staging); every power, clock, interconnect and interrupt item belongs to the K6 branch,
which no authorization row opens. K6 stays as worded.

### 11.4 Licences

No legal conclusion is drawn here: the table says what each part states about itself. The Linux and FreeBSD lines were read
through the hosting API (23 Linux files sampled, one FreeBSD file); the tests recheck the two UniXNU `LICENSE` files, the glue
inventory, the four v7.0.14 files named in section 1 and the two wpa_supplicant files.

| Part | Where it states its licence | Licence as stated | Label |
| --- | --- | --- | --- |
| UniXNU and its `sheng` board repository | The `LICENSE` file (the same bytes in both) and an SPDX line in the board sources (`UniXNU-sheng/package/WifiProbe.cpp:1-1`) | BSD-2-Clause-Patent | VERIFIED |
| Ath12kWifi glue (`src/`, `shim/`, `include/`, `userspace/`, `tests/`, `tools/`) | No licence, copying or notice file and no copyright text; the only SPDX literals are in `shim/include/linux/math.h` (`Ath12kWifi/shim/include/linux/math.h:1-1`, GPL-2.0) and in three tool scripts that write GPL headers into generated files | Not stated: the repository grants nothing by itself | UNKNOWN |
| Linux ath12k (v7.0.14, the lock) | The SPDX line of each file (`lock-7.0.14/ath12k/core.c:1-1`; six files sampled) | BSD-3-Clause-Clear | VERIFIED |
| Linux MHI host, QMI, QRTR `mhi.c` and `qrtr.h`, cfg80211 `scan.c` | SPDX lines | GPL-2.0 | VERIFIED (sampled) |
| Linux mac80211, `nl80211.c`, `mac80211.h`, `cfg80211.h`, QRTR `af_qrtr.c` | SPDX lines | GPL-2.0-only | VERIFIED (sampled) |
| Linux QRTR name service `ns.c` | SPDX line | GPL-2.0 OR BSD-3-Clause | VERIFIED (sampled) |
| Linux `skbuff.c`, `rbtree.c`, `arc4.c` | SPDX lines | GPL-2.0-or-later | VERIFIED (sampled) |
| wpa_supplicant 2.12 | The archive's `COPYING` and `README` | The BSD licence without the advertising clause only; the GPL option was dropped on 2012-02-11 | VERIFIED |
| FreeBSD headers (lock `releng/15.1`) | The notice in the one required file, `sys/compat/linuxkpi/common/include/linux/types.h` | A BSD two-condition notice and no SPDX tag; the other files were not read | VERIFIED (one file); UNKNOWN (the rest) |
| Firmware under `ath12k/WCN7850/hw2.0/` | `WHENCE` of linux-firmware (section 1) | `LicenseRef-LICENSE.qcom`, redistributable under its terms | VERIFIED |

**What this means for this project's rules.** AGENTS.md rule 4 keeps GPL-2.0 material in separate files and separate
components, never merged into MIT files. Ath12kWifi builds a different kind of artifact: the kernel image it is linked into
holds GPL-2.0 and GPL-2.0-only code next to BSD code and the project's own. Nothing of that may enter SnapQCC2072's MIT files
or the monitor. If option D were taken, the glue would be its own component with its own provenance row in
`THIRD_PARTY_NOTICES.md`, and the fetched Linux sources would stay out of this repository, as UniXNU itself keeps them out of its
own (WE40). Whether combining those parts is permissible for this project, and what the glue's own licence is, are questions
for the owner and for the glue's authors; this study neither assumes nor grants an answer (UNKNOWN). The treatment this project
already gives GPL-2.0-only generated material (a separate file with its provenance in the notices) is the pattern.

### 11.5 Maturity: what was verified on hardware, and what only on a host

| # | Claim | Label | Source |
| --- | --- | --- | --- |
| WE55 | The `sheng` record (Xiaomi Pad 6S Pro, SM8550, iPadOS 27.0.1 build 24A446) is `implemented`: device verification covers Wi-Fi toggling, SSID scanning, WPA2 association, DHCP, traffic and join-result delivery. Pending on the current image: cancellation and timeout completion, runtime signal display, firmware start from the data volume, throughput, jitter and heat. The image under test is a temporary boot (fastboot accepted it). | OBSERVED (one board, as the project reports it; not reproduced) | `UniXNU/docs/devices/sheng.md:457-495`; `UniXNU/devices.toml:3-15`; `UniXNU-sheng/reports/ipados27-wifi-mainline-migration.md:90-98` |
| WE56 | Twelve records (`fuxi`, listed as a Xiaomi 13, and eleven pages named `sheng-<contributor>`) carry the same sentence of device evidence with the same evidence revision (`a57341b4028514aedb961fe8ce636bb05f5e5925`): firmware c7-00108, a WPA2-Personal access point on 5 GHz at 80 MHz and -73 dBm, a DHCP address within 31 s of boot, about 12 Mbit/s down and 24 Mbit/s up, under 1 percent transmit loss. Identical text and revision mean one inherited measurement, not twelve, and nothing shows that a Xiaomi 13 was measured. The `fuxi` configuration selects iPadOS 26.5.2 (23F84). | OBSERVED (one measurement, as reported); INFERRED (that it is inherited) | `UniXNU/docs/devices/fuxi.md:469-469`; `UniXNU/docs/devices/fuxi.md:36-40` |
| WE57 | Spread: of 266 device pages, 34 name Ath12kWifi in their Network section. 18 of the 34 name it as their selected component: three carry the sheng verification sentence of WE55, twelve carry the measurement of WE56 and three carry source evidence only. The other 16 mention it without selecting it (for example `nx769j`, whose OS interface does not match the component's declared one, and `venus`, where sheng's result is "not evidence" for another endpoint). `devices.toml` has 265 device entries, 56 of them SM8550, and none names an X1E80100 or another Snapdragon X part. No selecting record mentions macOS; the only mention in the 34 Network sections is a host-tool command on a page that does not select the component. | VERIFIED (counts, recomputed by the test); INFERRED (that no other hardware evidence exists: only these records were read) | `UniXNU/docs/devices/nx769j.md:490-490`; `UniXNU/docs/devices/venus.md:558-558`; `UniXNU/devices.toml:3-15` |
| WE58 | The host-side checks the project reports: sanitizer fixtures with simulated radio operations, a lifecycle check that releases all 287 tracked allocations, and a link audit of 125 objects and 13 kernel imports with none missing or duplicated, plus kernel, payload, signature, firmware-hash and packed-image checks. This study did not run them. | OBSERVED (as reported); UNKNOWN (a rerun at the pinned commit) | `UniXNU/docs/drivers/ath12k-wifi.md:82-91`; `Ath12kWifi/README.md:95-97` |
| WE59 | The catalog lists no verified device for `Ath12kWifi` (`verified_devices = []`) while the `sheng` record reports device verification (WE55); the two are stated side by side and not reconciled here. All three repositories this study read carry an `AGENTS.md` for coding agents, so plan risk R20 applies: this is what the project states, not an independent reproduction. | VERIFIED (both statements); INFERRED (provenance) | `UniXNU/components.toml:487-487`; `UniXNU/docs/devices/sheng.md:480-480` |

In short: on hardware (OBSERVED) the project reports, for one board family (the Xiaomi Pad 6S Pro `sheng` and the records that
inherit its evidence), scan, WPA2 association, DHCP and traffic under iPadOS builds, on temporary boots. Host-only: the
sanitizer fixtures with simulated radios, the link audit and the packaging checks. Not claimed anywhere this study read: a run
under a macOS profile, on an X1E80100 machine or on a QCC2072, WPA3, 6 GHz scanning or Bluetooth integration. The component's
own selected profiles are open and WPA2-PSK with PMF (WE39).

### 11.6 Updated recommendation and preconditions

This study does not recommend replacing SnapQCC2072, and the decision is the owner's. What the UniXNU evidence changes:

- **Option A stays the default for code that is this project's own.** It needs no licence answer, no kernel change and no new
  OS interface, and it keeps the A16 driver byte-identical by default.
- **Option D becomes a named alternative for the WCN7850 path**, neither recommended nor rejected: it would bring Linux's
  complete stack, which has run on a device (WE55), and a worked power-up sequence.
- **Reading material for every option:** the shape of the K6 sequence (WE44, WE45), the PHY table equal to Linux's (WE46), the
  three firmware files equal to this study's pins (WE51), the plain-C boundary (WE41) and the staged bring-up that begins with a
  read-only look.

Four preconditions for option D, each a decision or a measurement; none holds today:

1. **The licence of the glue.** Ath12kWifi states none (section 11.4). Until its authors answer, only facts may be used, and
   the owner may wish to ask them.
2. **The OS interface of macOS 26A428.** The layouts and the daemon conditions are those of iPadOS 24A446; its macOS branch has
   no hardware record and its macOS profile is another build on its own kernel (WE53, WE54, WE57).
3. **The stock-kernel rule.** Its build is `embedded` in a kernel the project builds; this project loads kexts against Apple's
   unmodified kernel. A kext form of the glue is new work with the arm64e kext ABI proof of the acceptance matrix in AGENTS.md.
4. **A migration plan.** AGENTS.md forbids a parallel implementation without one: ownership, equivalence tests, cutover and
   retirement conditions against SnapQCC2072, which stays the A16's driver until then. The Linux release it locks also lacks
   the Denali RF-kill entry (WE52), so a pin newer than v7.0.14 or a local policy would be needed.

The board and EL2 layers (rows 1 to 3 of section 11.2) are the same blocker for every option. K6, the X12 sub-gates and the
descriptor proposal are unchanged by this section.

## 12. How to run the checks

```sh
python3 tools/test-sp11-wifi-board.py                  # the board-container reader: 19 cases, under 1 s
python3 tools/test-sp11-wifi-study.py                  # the study's structure, labels, pins, numbers and this repository's citations
SP11_LINUX_TREE=<checkout> python3 tools/test-sp11-wifi-study.py            # also every Linux citation and SHA-256 pin
SP11_WIFI_BOARD_DIR=<dir> python3 tools/test-sp11-wifi-study.py             # also the two board-2.bin containers (wcn7850-board-2.bin, qcc2072-board-2.bin)
SP11_SNAPQCC2072_TREE=<checkout> python3 tools/test-sp11-wifi-study.py      # also the Appendix A census
python3 tools/test-sp11-wifi-unixnu.py                 # section 11 (UniXNU): its pins, ten-layer table, licence statements, Appendix B
SP11_UNIXNU_REF=<dir> python3 tools/test-sp11-wifi-unixnu.py                # also every UniXNU citation, the licence facts and the counts (layout below)
SP11_UNIXNU_REF=<dir> SP11_LINUX_TREE=<checkout> python3 tools/test-sp11-wifi-unixnu.py   # also the PHY-table comparison
python3 tools/sp11_wifi_board.py list FILE             # read-only: the entries of a staged board-2.bin
python3 tools/sp11_wifi_board.py find FILE --vendor 17cb --device 1107 --subsystem-vendor 17cb --subsystem-device 1107 --chip-id 2 --board-id 255
```

They need only the Python standard library (run under Python 3.13 to 3.15; the sources parse with the 3.10 grammar). A class
whose input is absent reports SKIP, never PASS. They write only under `build/` and open no device. The pinned Linux files are
fetched with `curl` or `gh api` from the commit of section 1; once `SP11_LINUX_TREE` is set, a missing or different file fails
the class instead of skipping it. The firmware files are not needed for the study checks except the two containers.
`tools/test-sp11-wifi-unixnu.py` imports the helpers of `tools/test-sp11-wifi-study.py` and owns every citation of a UniXNU file
(the study test ignores those and requires this one to list them exactly).

`SP11_UNIXNU_REF` names a directory with five subdirectories: `UniXNU`, `Ath12kWifi` and `UniXNU-sheng` (shallow clones at the
commits of section 1, read-only), `linux-v7.0.14` (the four files of the lock listed in section 1, at their paths under the Linux
root) and `wpa_supplicant-2.12` (the archive's `COPYING` and `README`). The tests read these files and never run `git` in the
clones; a missing subdirectory or a different file hash fails the class. The PHY-table comparison also needs `SP11_LINUX_TREE`
and reports SKIP without it.

## Appendix A: SnapQCC2072 file census at 244d5c7f

A reproducible scan of the 64 `.c`, `.h` and `.cpp` files under `src/` of SnapQCC2072 at commit
`244d5c7f4117bf42c5b1276a23d5b53aecff2895` (11,332 lines). It counts three kinds of chip literal per file: PCI-id literals
(`0x111217cb`, `0xe14f105b`, `QCC2072_DEVICE_ID`, `QCC2072_VENDOR_ID`, `0x1112`, `0x17cb`), the two PCI window registers
(`0x3278`, `0x310c`) and absolute register addresses (the literals matched by
`0x(a[0-9a-f]{5}|[1-9][0-9a-f]{6}|0[1-9a-f][0-9a-f]{5})`, case-insensitively and followed by a word boundary).
14 of the 64 files carry at least one; 50 carry none. The classification in section 5.2
from these counts is INFERRED: a literal's absence does not prove a file is chip-neutral. `tools/test-sp11-wifi-study.py`
recomputes this table from a checkout given in `SP11_SNAPQCC2072_TREE`.

| File | Lines | PCI ids | Window | Absolute |
| --- | --- | --- | --- | --- |
| `src/qcc2072-ce.c` | 104 | 0 | 3 | 4 |
| `src/qcc2072-datapath.c` | 194 | 0 | 3 | 51 |
| `src/qcc2072-datapath.h` | 44 | 0 | 0 | 2 |
| `src/qcc2072-dma.c` | 69 | 2 | 0 | 0 |
| `src/qcc2072-dp-io.c` | 814 | 0 | 2 | 4 |
| `src/qcc2072-firmware.c` | 254 | 2 | 0 | 0 |
| `src/qcc2072-firmware.h` | 76 | 4 | 0 | 0 |
| `src/qcc2072-provider-record.h` | 21 | 0 | 0 | 1 |
| `src/qcc2072-provider.cpp` | 915 | 4 | 0 | 0 |
| `src/qcc2072-qmi.c` | 216 | 4 | 0 | 0 |
| `src/qcc2072-reo.c` | 181 | 0 | 3 | 24 |
| `src/qcc2072-transport.c` | 122 | 2 | 0 | 0 |
| `src/qcc2072-transport.h` | 60 | 0 | 1 | 0 |
| `src/qcc2072-wlfw.c` | 208 | 2 | 0 | 0 |

The other 50 files carry no such literal: `SnapintoshQCC2072.{cpp,h}`, `SnapintoshQCC2072Interface.{cpp,h}`, `kmod_info.c`,
`native-interface-state.h`, `native-rsn.{c,h}`, `native-station.{c,h}`, `native-wcl-event.{c,h}`, `native-wcl-key.{c,h}`,
`native-wcl-scan.{c,h}`, `native-wpa2-crypto.{c,h}`, `native-wpa2.{c,h}`, `qcc2072-assoc.{c,h}`, the five files under
`qcc2072-assoc/`, `qcc2072-ce.h`, `qcc2072-connect.{c,h}`, `qcc2072-dma.h`, `qcc2072-dp-io.h`, `qcc2072-htc.{c,h}`,
`qcc2072-mhi.{c,h}`, `qcc2072-provider.h`, `qcc2072-qmi.h`, `qcc2072-qrtr.{c,h}`, `qcc2072-reo.h`, `qcc2072-runtime.{c,h}`,
`qcc2072-scan.{c,h}`, `qcc2072-service.{c,h}`, `qcc2072-wlfw.h` and `qcc2072-wmi.{c,h}`. Four files mention "WCN7850" in a
comment: two of the 50 (`qcc2072-assoc.h` and `qcc2072-assoc/state-machine.c`) and two of the 14 (`qcc2072-datapath.c` and
`qcc2072-wlfw.c`). That is the author's own record that the driver follows the WCN7850 family
(`snap-qcc2072/src/qcc2072-wlfw.c:71-71`; `snap-qcc2072/src/qcc2072-datapath.c:143-143`).

## Appendix B: Ath12kWifi file census at 48e23bf1

A reproducible walk of the `Ath12kWifi` checkout at commit `48e23bf15fc21bbd30d5583b42b9c09ad65537db`, leaving out `.git`: each
file is counted once and lines are newline bytes. The glue is what UniXNU wrote around the Linux, wpa_supplicant and FreeBSD
sources it fetches (WE40); none of those sources is in the repository. `tools/test-sp11-wifi-unixnu.py` recomputes this table
from the checkout in `SP11_UNIXNU_REF`.

| Group | Files | Lines |
| --- | --- | --- |
| (root: `.gitignore`, `AGENTS.md`, `README.md`) | 3 | 144 |
| `include/` | 3 | 235 |
| `shim/` | 149 | 2,924 |
| `src/` | 35 | 4,353 |
| `tests/` | 18 | 2,804 |
| `tools/` | 43 | 1,803 |
| `userspace/` | 10 | 1,049 |
| Total | 261 | 13,312 |

The board layer is in the board repository: at `UniXNU-sheng` commit `00ba90984e831e4c6ce30ec3d8a56e6434e9f1af`,
`package/WifiProbe.cpp` is 1,734 lines, `package/WifiNet.cpp` 872 and `package/WifiPcieTables.h` 37. No file of the glue has a
licence-like name (`LICENSE`, `COPYING`, `NOTICE`), and four contain an SPDX literal: `shim/include/linux/math.h`,
`tools/pin-headers.sh`, `tools/prepare-cfg80211-events.py` and `tools/prepare-cfg80211-native.py` (section 11.4).
