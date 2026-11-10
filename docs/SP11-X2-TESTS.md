<!-- SPDX-License-Identifier: MIT -->
# SP11 X2 EL2 functional tests: operator guide

**Hardware status: NOT RUN.** The image described here has never been started on
a Surface Pro 11 or on any real machine. Everything it measures on QEMU is a
property of QEMU. This guide says what the run will look like, how to read it,
what each result means for gate X2 and kill criterion K1 of
[SURFACE-PRO-11.md](SURFACE-PRO-11.md) (sections 6.2, 6.3 and 9), and what you must
not do. Evidence labels: VERIFIED (reproduced by a test here), OBSERVED (read on
the machine that ran the image), INFERRED (follows from other results),
EXPERIMENTAL (written, emulator-tested only), UNIMPLEMENTED, UNKNOWN. The image
never claims VERIFIED: one run on one unit verifies nothing.

The image is the EL2 state probe ([probes/uefi/README.md](../probes/uefi/README.md))
with nine tests added. You run it once, after slbounce has put the machine at EL2
(the same preparation as the probe), and it decides one question the plan cannot
answer on paper: **does this core redirect an EL2-register access made from EL1
into the page named by `VNCR_EL2` (FEAT_NV2), and does the same access trap when
NV2 is off?** The other eight tests collect the device state the monitor will
meet at the same moment.

## 1. Before you start

* A hardware build needs the SoC named explicitly (the NV2 record carries it and
  the monitor's gate refuses a record for another SoC):

  ```sh
  python3 tools/build-uefi-tests.py --soc x1e80100          # build/uefi/sp11-el2-tests.efi
  python3 -B tools/test-uefi-tests.py                       # host tests, one command
  ```

  Only `sp11-el2-tests.efi` is for the machine. The `test/` directory holds
  emulator builds (`model`, `model-fatal`, `model-fatal-pre`): never run those on
  hardware, they shorten the timers and skip the reset.
* The device tests (T05 to T07) refuse a window whose base is zero, misaligned,
  wrapping or in memory the UEFI map calls RAM, firmware or reserved. A window the
  map does not mention at all is read by default (the map of the QEMU machine lists
  none of its GIC, PCIe or SMMU windows; whether the SP11's does is unknown until
  the probe's `memmap.txt` is read). To read only windows the map lists as MMIO:

  ```sh
  python3 tools/build-uefi-tests.py --soc x1e80100 --require-mmio-map
  ```

  This changes the build id, and each of T05 to T07 then shows `MAP STRICT`.
* Prepare the stick exactly as for the probe. The image writes next to the probe's
  dump, into the run directory the probe created (`\SP11DUMP\RUNnn\`). It never
  creates a directory, never overwrites a file, and writes only to the volume it
  was loaded from, and only if that volume is a USB stick.
* Start it the way you start the probe: from the UEFI Shell. The framebuffer is
  chosen by the probe's own rule (see "Framebuffer selection" in
  [probes/uefi/README.md](../probes/uefi/README.md)): the GOP of the console output
  handle, or, because the Shell replaces that handle for the programs it starts,
  the one framebuffer that every usable GOP describes. Which rule fired is on the
  `GOP` console line (`SEL=CONSOLE` or `SEL=SAME`) and in the dump's `summary.txt`
  (`gop_select=`); the X2 pages do not show it. If no framebuffer can be chosen
  (`GOP STATE=NONE WHY=...`, for example `AMBIGUOUS GOP` when usable GOPs describe
  different framebuffers), the image runs no test: it prints `SP11X2 REFUSE
  REASON=NO LAYOUT FOR THE REPORT` and `EBS STATE=SKIPPED REASON=TESTS REFUSED`,
  never calls ExitBootServices, writes none of its `x2-*.txt` files and leaves only
  the probe's own dump on the stick. The `GOP STATE=NONE WHY=...` line says why.
* AC power, as for any hardware experiment. One hardware client at a time.
* Have a phone or camera ready: after ExitBootServices the screen is the only
  output.

## 2. What you will see, in order

**Phase A, before ExitBootServices.** The probe's usual stage (firmware facts,
ACPI, SMBIOS, memory map, its dump to the stick), then the tests that touch no
device, least intrusive first: T01 ID dump, T03 timer, T04 DC ZVA, T02 NV2. Each
writes `SP11X2 PROGRESS RUNNING ...` and `SP11X2 RESULT ID=n VERDICT=... LABEL=...`
lines to the firmware console (whether you can see that console on this machine
is UNKNOWN; the same lines appear on a serial log when there is one). The results
and the NV2 record are then written to the stick (`SP11X2 STICK STATE=OK
FILES=4`). If the image is not at EL2 it runs no test and writes no result file;
the report then consists of one page saying `NOT AT EL2: NO TEST WAS RUN`.

**Phase B, after ExitBootServices (screen only).** The device tests, still
read-only: T09 DRAM map, T05 GIC, T07 SMMU, T06 PCIe and NVMe, T08 ADSP. A page
is drawn after each. The footer names the running test, and while a device
window is being read it names that window (`T06 ECAM 0x...`, `T06 NVME 0x...`):
if the machine hangs, the last footer tells you which address was being read.

**The report.** The summary page, then one section per test, as photographable
pages that rotate every 12 seconds, with a countdown in the footer that scales
with the number of pages (every page is shown at least twice, and never less than
60 seconds). Then a cold reset (`RESETTING NOW`).

## 3. Reading a page

```
SP11 EL2 TESTS B=<build id>              header: the build, and MODEL if it is an emulator build
CURRENTEL=2 PAGE 3/9                     header: the exception level it read, and the page
...rows...  | 5A                         every row ends with " | hh", a CRC-8 of the row
RESET IN 42 S                            footer: what the image is doing now
```

* Everything is upper case, at most 38 characters per row. The check after `|` is
  the CRC-8 of the row as shown; a misread character shows up as a mismatch.
* The summary page lists `T01` to `T09` with a verdict and a label, then the
  K1 line: `K1 NV2: GO`, `NO-GO (SEE GUIDE)`, `INCONCLUSIVE` or `NOT RUN`.
* Each test section ends with two fixed rows: what a good result proves and what
  it does not prove. Read them before drawing a conclusion.
* A label `OBS-MODEL` means the image was built for an emulator. It must never
  appear on hardware; if it does, stop and tell the coordinator.
* Rows ending in `UNK` or `UNKNOWN` mean "no address could be justified" or "the
  read faulted". They are results, not omissions.

## 4. Retyping a page

Prefer the files on the stick (section 8): they are exact. If you only have
photographs, type each row (the part before ` |`) into a text file, then:

```sh
python3 probes/uefi/verify-lines.py transcription.txt          # checks every row against its CRC-8
python3 probes/uefi/tests/host/nv2screen.py nv2-rows.txt > record.txt
python3 tools/nv2_record.py show record.txt                    # the record, decoded and checked
```

`nv2screen.py` joins the continuation rows of the NV2 record (a line longer than
38 characters is split: the first 32 characters, then a row starting with `+`),
restores the lower case the screen cannot show, and checks each line's own `*hh`
check digits. A row that does not check is refused, never repaired: retype that
row from the photograph.

## 5. What each test means

| Test | What it measures | What it proves | What it does not |
| --- | --- | --- | --- |
| T01 ID dump | The ID registers of the brief (MIDR, REVIDR, PFR0/1, MMFR0-4, ISAR0-2, DFR0, DCZID, CTR) and the decoded fields the plan depends on (NV, NV_frac, E2H0, ECV, FGT, VH, VMIDBits, PARange, granules, AMU, GIC) | what this core advertises, as read at EL2 | that a feature works; ID bits are filtered or mis-reported often enough that only behaviour counts |
| T02 NV2 | An EL1 stub runs with `HCR_EL2.NV` set: the control (NV2 off) must trap with the EC 0x18 syndrome of an `mrs` of `TPIDR_EL2`; with NV2 on and `VNCR_EL2` at a page, the stub's write of `TPIDR_EL2` must land at VNCR offset `0x090` and the real register must not change | whether NV2 redirects on this core, and that the negative control behaves | anything about nested guests beyond the two accesses |
| T03 timer | CNTFRQ, CNTHCTL_EL2.ECV, CNTPOFF_EL2 (only if ID ECV >= 2), CNTVOFF_EL2, that the counter advances and that CNTVCT + CNTVOFF lies between two physical reads | the boot CPU's counter and offsets are coherent | other cores (see the open gap below) |
| T04 DC ZVA | The block size, then one `DC ZVA` into the middle third of an image-owned buffer: exactly one block zeroed, both neighbours untouched | DC ZVA works and the block size is what the monitor assumes (64 bytes) | behaviour on device or Device-type memory |
| T05 GIC | GICD/GICR/ITS registers at the addresses the **MADT** gives, the redistributor walk, and each MADT CPU against a redistributor frame; the board descriptor is shown as a cross-check row (`D= R= I= C=` as `OK`, `DIFF` or `UNK`). A base that is zero, misaligned or wrapping, or that the memory map calls RAM or firmware, is not read: the row says `GICD REFUSED: <reason>` and the verdict is UNKNOWN, never FAIL | the MADT describes a working GIC | that interrupts are delivered |
| T06 PCIe/NVMe | Configuration reads at the **MCFG** windows, the root port's link status, and, only if the endpoint's BAR is justified (decode on at both ends, inside the bridge's window, not RAM in the memory map), NVMe CAP, VS, CC and CSTS; `CC.EN` and `CSTS` are read, never written. A refused window shows `W<n> REFUSED: <reason>` or `NVME BAR0 REFUSED: <reason>` | the link state and whether firmware left the controller enabled | that the controller works; no register is written |
| T07 PCIe SMMU | SMMUv3 identification, CR0/CR0ACK, GBPA and GERROR at the **IORT** nodes (a node with a bad base shows `SMMUV3 BASE BAD` or `SMMU<n> REFUSED`) | the translation state the monitor will inherit (bypass, abort or enabled) | the stream table contents |
| T08 ADSP/GLINK | Nothing is touched: no address can be justified from ACPI, so the result is UNKNOWN with label UNIMPL | only that there is no address source yet | any liveness. A later change must supply an address from the DSDT dumped on the stick |
| T09 DRAM | The UEFI memory map's RAM extents against the monitor's IPA limit (2^36 unless the descriptor says otherwise) | the RAM the firmware reports fits the monitor's stage-2 | the physical DRAM population |

## 6. What the result means for the gate and for K1

The K1 line is the NV2 verdict, decided by behaviour and never by the ID bits
alone. `GO` needs all of: ID NV == 2 and NV_frac == 0 (the combination the
monitor programs), plausible ID registers, a run whose state was restored and
read back, the write landing at VNCR offset `0x090`, and the NV2-off control
trapping with exactly the expected syndrome.

| K1 line | Reasons shown in T02 | What to do |
| --- | --- | --- |
| `GO` | `GO` | The record on the stick (`x2-nv2.bin`) is the one the monitor's gate reads: ingest it with `tools/nv2_record.py` for the gate's own decision. A hardware GO record carries evidence OBSERVED. The NV2 item of gate X2 (section 6.3) is then evidenced for this unit and revision, by one run. |
| `NO-GO` | `NV NOT IMPLEMENTED`, `NV ONLY, NO NV2`, `NOT REDIRECTED`, `ID SAYS...` | Candidate for kill criterion K1 (SURFACE-PRO-11.md section 3.3: stop and report, do not improvise). Photograph T01 and T02 in full. A repeat after a cold boot is a cheap confirmation; that is this guide's recommendation, not plan text. |
| `INCONCLUSIVE` | `MECHANICS FAULT`, `VNCR PAGE ABORT`, `CONTROL DID NOT TRAP`, `NV2 ONLY, NO NV`, `RESERVED NV/NV_FRAC`, `FUNCTIONAL TEST DID NOT RUN`, `ID REGS NOT A READING` | Do **not** decide K1 from this run. Photograph T02 in full and report it. A control that did not trap while the redirect landed is a contradiction worth a second look, not a GO. A state that could not be restored is not a T02 reason: the image halts on the `FATAL: CLEANUP FAILED` page before any result is shown (section 7). |
| `NOT RUN` | the image was not at EL2 | Fix the slbounce step first. |

A record is only ever written as a pass pair (redirect pass, control pass) by a
run that ended in GO. A run whose machinery faulted records both checks as
not-run. A run whose state could not be restored writes no record at all: it ends
on the `FATAL: CLEANUP FAILED` page (section 7). A model run records evidence
EXPERIMENTAL under the tool name `SP11-X2-MODEL`, and the gate refuses it.

## 7. What you must never do

* Do **not** power-cycle, close the lid or unplug anything while a test runs. The
  NV2 test changes `HCR_EL2` and the vector base and restores them within
  milliseconds; the device tests are reads. Interrupting changes nothing on the
  disk, but it destroys the evidence of where the machine stopped.
* Do **not** remove the USB stick until the report is on screen. Phase A writes
  the result files before ExitBootServices (`SP11X2 STICK STATE=OK` where the
  console is visible); after that the stick is never touched again.
* If the screen freezes, wait at least 10 minutes (a recommendation of this
  guide), **photograph the footer** (it names the test and the address being
  read), and only then power off. Report the footer text verbatim.
* If a page says `FATAL: ...` and `POWER CYCLE. DO NOT TRUST RESULTS.`, an
  exception arrived that nothing was expecting. Photograph it, then power cycle,
  and do not use any result from that run. A page that says `FATAL: CLEANUP FAILED`
  means a register could not be put back after a test (the second row says which
  step, the fourth whether one more attempt restored it): the image stopped, wrote
  nothing to the stick and did not call ExitBootServices. The page is drawn on the
  framebuffer only, before ExitBootServices as well as after, and nothing is
  written to the console after it.
* Do not run the `model`, `model-fatal` or `model-fatal-pre` images, and do not edit
  tracked sources between building the image and running it.
* Do not run it on another machine than the one you are reporting about: the
  NV2 record names the SoC, MIDR and REVIDR, and the monitor's gate compares them.

## 8. Files on the stick

In the probe's run directory (`\SP11DUMP\RUNnn\`), written before ExitBootServices
and never overwritten:

| File | Content |
| --- | --- |
| `x2-results.txt` | one block per phase A test: verdict, label, rows, what it proves and does not prove |
| `x2-nv2.txt` | the NV2 record as 13 text lines, each with its `*hh` check (the format of `tools/nv2_record.py`) |
| `x2-nv2.json` | the same record as JSON |
| `x2-nv2.bin` | the same record, 96 bytes with a CRC-32: the form the monitor's gate consumes |

Phase B results exist only on the screen (nothing may write to the stick after
ExitBootServices), so photograph every page of the report.

## 9. What the image will not do, and what the first run must still tell us

* It writes no PCIe, NVMe, SMMU, GIC, display, clock, power or voltage register
  and no configuration space. `CC.EN` and `CSTS` are read. A host test and the
  audit of the built image check this.
* Every address comes from ACPI (MADT, MCFG, IORT) or from a register it has just
  read; the board descriptor is only compared. An address that cannot be
  justified is skipped and labelled UNKNOWN. So is one that is zero, misaligned,
  wrapping, or that the UEFI memory map calls RAM or firmware: it is refused
  without being touched, and the row says why.
* Every test runs inside an EL2 vector table the image installs and removes. A
  fault becomes a result (with ESR and address), not a hang; an exception nobody
  expected ends in the FATAL page. `ELR_EL2` and `SPSR_EL2` are saved and restored
  with the vector base and the interrupt masks, and a restore that fails ends the
  run (`FATAL: CLEANUP FAILED`) before anything is written or ExitBootServices is
  called. No firmware function is called while the table is installed: console
  text produced there is held back until it has been removed.
* **Not implemented:** CNTVOFF across cores (needs a PSCI-started second CPU; the
  T03 row says `CORES: BOOT CPU ONLY`), ADSP/PMIC-GLINK liveness (no address
  source), the display register list (no study existed when this was written).
* **The first run must observe:** the real exception syndrome of the control
  (EC 0x18 with the `TPIDR_EL2` encoding), whether the redirect lands at `0x090`,
  that the vector table and the ERET to EL1 behave under the SP11's firmware
  (EXPERIMENTAL: QEMU exercises them, the SP11 has never been tried), which
  framebuffer rule fired (`SEL=` on the `GOP` console line and `gop_select=` in
  `summary.txt`; the rule works under QEMU with the pinned Shell, OBSERVED, but
  the unit's console stack is UNKNOWN until X1), whether
  the page rows fit the panel (the layout is chosen from the framebuffer
  size), whether the UEFI memory map lists the GIC, PCIe and SMMU windows as MMIO
  (the `MAP MMIO=n UNLISTED=n` row of T05 to T07 and the probe's `memmap.txt`;
  if they are not listed, only a build without `--require-mmio-map` reads them),
  that no `FATAL: CLEANUP FAILED` page appears, and whether an asynchronous SError
  ever arrives during a guarded read (T05 to T07 `FAULTS=` rows; the continuation
  masks it and gives up on that register, but no run has ever taken one).
