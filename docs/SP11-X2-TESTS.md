<!-- SPDX-License-Identifier: MIT -->
# SP11 X2 EL2 functional tests: operator guide

**Hardware status: K1 decided — GO. NV2 is confirmed functionally usable on this unit.**
OBSERVED, X1E80100 Surface Pro 11, 2026-11-11, run 4 (build `89889B7FEB3C`, the NV2-reorder
fix below): T01 and T02 both ran to completion. T02's verdict is `K1 NV2: GO` (`VNCR_EL2`
redirect PASS, the NV2-off negative control PASS, state restored and cleanup OK), CRC-verified
row by row (90/90) and independently confirmed with `tools/nv2_record.py show --check` against
the retyped record (`gate: ALLOW`). Runs 1-3 (below) found and fixed two bugs before this
result was reachable at all; see "A phase-ordering bug" and "A second finding" for that history.
T03, T04 and items 9-13 of [SURFACE-PRO-11.md](SURFACE-PRO-11.md) section 6.3 are still NOT RUN
(the same board reset shortly after T01/T02 that run 3 found, still unexplained, happens again
right after T02 in run 4) — none of that affects K1, which only needs T01 and T02. Evidence
labels: VERIFIED (reproduced by a test here), OBSERVED (read on
the machine that ran the image), INFERRED (follows from other results),
EXPERIMENTAL (written, emulator-tested only), UNIMPLEMENTED, UNKNOWN. The image
never claims VERIFIED: one run on one unit verifies nothing. This guide
says what the run will look like, how to read it,
what each result means for gate X2 and kill criterion K1 of
[SURFACE-PRO-11.md](SURFACE-PRO-11.md) (sections 6.2, 6.3 and 9), and what you must
not do.

## A phase-ordering bug, found and fixed

The first two real runs found that phase A (T01 ID dump, T03 timer, T04 DC ZVA, T02 NV2) never
executes on this unit: `tests_hook_pre_ebs` only runs them when the CPU is already at EL2
*before* `ExitBootServices` is called, but slbounce's hook performs the actual EL1-to-EL2
switch *during* that very call (OBSERVED: both runs' `summary.txt` recorded
`preebs_currentel=0x4`, i.e. EL1, at that point). So phase A always took its `NOT_EL2` skip
path, and the on-screen `K1 NV2:` line read `NOT RUN` both times, never a verdict. Phase B
(T09, T05, T07, T06, T08), which only needs EL2 *after* `ExitBootServices`, ran normally in
both cases. `tests_hook_post_ebs` now also runs whichever of T01-T04 have not already run in
phase A, in the same order, before phase B's own tests; because nothing may reach the stick
after `ExitBootServices`, their results (including the NV2 record) reach the operator only
through the same on-screen rows and CRC-8 checks as every other phase-B test.

The image is the EL2 state probe ([probes/uefi/README.md](../probes/uefi/README.md))
with nine tests added. You run it once, after slbounce has put the machine at EL2
(the same preparation as the probe), and it decides one question the plan cannot
answer on paper: **does this core redirect an EL2-register access made from EL1
into the page named by `VNCR_EL2` (FEAT_NV2), and does the same access trap when
NV2 is off?** The other eight tests collect the device state the monitor will
meet at the same moment.

## A second finding: T03 stalls and the board resets

Run 3 (the image with the phase-ordering fix above) reached EL2 through phase B, as expected,
and ran T01 to completion: a full ID-register dump, CRC-verified against the photographed
rows, agreeing with the earlier probe's passive read (`NV=2 NV_FRAC=0`). The fallback then
started T03 (the timer test) next, in phase A's original least-intrusive-first order. A 120
fps video recording of the screen, taken from a fresh attempt, showed the footer holding on
`RUNNING T03 TIMER` for the entire recorded window (frame-by-frame identical from 0.25 s to
12.0 s), then going dark within 0.2-0.3 s; no `FATAL:` page ever appeared. `tests_run_timer`
(`probes/uefi/tests/t_misc.c`) polls `CNTPCT_EL0` in a loop bounded by a plain software
counter (up to 200000 guarded single-register reads, not by elapsed time), waiting for the
value to change; each guarded read is one `mrs` instruction plus a volatile flag check
(`probes/uefi/tests/sys_aarch64.c`), so the loop is not expected to cost more than a few
milliseconds even in the worst case it is built to detect (a counter that never advances).
**The exact cause of the multi-second stall is NOT CONFIRMED** (OBSERVED: the stall and the
reset; INFERRED only: what inside or around the loop is actually slow, and whether the reset
is time-based or triggered by something this code does). What the project's own checks did
confirm, by inspection, is that T02 (the NV2 test that answers K1) depends only on T01's
already-filled ID context, not on T03 or T04's results, so it can safely move earlier.
`tests_hook_post_ebs`'s fallback order is therefore now `T_ID, T_NV2, T_TIMER, T_ZVA`: T02
gets its chance, and is drawn to the screen, before T03 is reached at all. Phase A's own
order (for a platform where it runs normally, before ExitBootServices) is unchanged.

**Update, 2026-11-12 (OBSERVED, one run).** q1n1's `--el2` page (B1; [SURFACE-PRO-11.md](SURFACE-PRO-11.md), Status of
B1), reached through the same slbounce bounce with a scoped sweep instead of the test-only no-sweep variant, was
still running about 40 minutes after it appeared (operator report; its heartbeat block was still blinking), with no
reset. So the reset is not a fixed-time property of the post-bounce environment: it follows something this image
does. Candidates, all INFERRED and none tested: the guarded-read machinery's exception handling or vector setup;
interrupts left unmasked after the hook (q1n1 masks all of DAIF at entry; this hook's state is unrecorded); an access
to a timer register that wedges the core, with the reset then a watchdog bite (T03 touches `CNTVOFF_EL2`, which
Linux treats as broken on Oryon X1, E17); the no-sweep slbounce variant itself. q1n1's page reads `CNTFRQ_EL0` once and
polls `CNTPCT_EL0` continuously at EL2 without trouble, so those two registers are not the cause.

## Run 4: K1 decided — GO

With T02 moved ahead of T03, run 4 (same unit, build `89889B7FEB3C`) reached T02 before the
reset that stops run 3 at T03. T01 and T02 both ran to completion and rendered their full
results; the board then held on `RUNNING T03 TIMER` and reset again, as run 3 found, but by
then T02's verdict was already on screen. Transcribed and CRC-verified row by row (90/90 OK,
`probes/uefi/verify-lines.py`), cross-checked against `tools/nv2_record.py`:

* T01: `ID_AA64MMFR2_EL1.NV` == 2, `NV_FRAC` == 0 (matching the earlier passive-probe read and
  T01's own two prior runs), `PARANGE=4` (44-bit), `VMIDBITS=16`, `ECV=2`, `FGT=1`, `VH=1`.
* T02: `CTL NV2 OFF: TRAPPED AS EXPECTED` (the negative control), `RED NV2 ON: LANDED IN PAGE`
  (the redirect), `RESTORED=YES MECH=NO CLEANUP=OK`, `WHY GO`, `K1 CLEAR: NV2 USABLE`. The NV2
  record's own fields (`MIDR_EL1`, `REVIDR_EL1`, `ID_AA64MMFR2_EL1`, `ID_AA64MMFR4_EL1`) agree
  exactly with T01's independently rendered copies of the same registers.
* The record's embedded `*hh` check (the format `tools/nv2_record.py` itself reads, computed
  over the lowercase, unsplit body before the screen's own uppercase rendering and 38-column
  wrap) matches on every one of its 13 lines once reconstructed in that form.
* `python3 tools/nv2_record.py show --soc x1e80100 --live-midr 0x00000000512F0011
  --live-revidr 0x0 --live-mmfr2 0x1201011112101011 --live-mmfr4 0x0 --check` on the
  reconstructed record: `parse: PARSED`, `gate: ALLOW`, exit 0.

K1 did not fire: NV2 is functionally usable on this unit. T03, T04 and items 9-13 of
[SURFACE-PRO-11.md](SURFACE-PRO-11.md) section 6.3 remain NOT RUN; none of them bear on K1.
The record is a measurement of this one machine (`tools/nv2_record.py`'s own rule): it is kept
under `build/`, never committed, and never shown here in full; the embedding of it into an
actual monitor build is separate, later work.

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

**Phase A, before ExitBootServices, on a platform already at EL2 there.** The
probe's usual stage (firmware facts, ACPI, SMBIOS, memory map, its dump to the
stick), then the tests that touch no device, least intrusive first: T01 ID
dump, T03 timer, T04 DC ZVA, T02 NV2. Each writes `SP11X2 PROGRESS RUNNING ...`
and `SP11X2 RESULT ID=n VERDICT=... LABEL=...` lines to the firmware console
(whether you can see that console on this machine is UNKNOWN; the same lines
appear on a serial log when there is one). The results and the NV2 record are
then written to the stick (`SP11X2 STICK STATE=OK FILES=4`).

Whether a platform is at EL2 yet when this image reaches this point is a
property of its own boot chain, not of this image, and is not the same on every
platform: OBSERVED (twice, on the runs that found this gap): on a Surface Pro 11
X1E80100 it is not — `preebs_currentel` decoded to EL1 both times — because the
Secure-Launch SMC sequence that performs the EL1-to-EL2 switch runs inside
slbounce's own ExitBootServices hook, after this firmware's ExitBootServices call
has already returned success. On such a platform phase A runs none of these four
tests here and writes none of `x2-results.txt`/`x2-nv2.*`: it logs `SP11X2 SKIP
REASON=NOT_EL2` and goes straight on to ExitBootServices. They run in phase B
instead (below), screen only. Whichever phase actually ran them, the summary and
K1 lines of the one report (section 3) reflect the real result; only the stick
files differ, per platform.

**Phase B, after ExitBootServices (screen only).** First, whichever of T01, T02,
T03 and T04 did not already run in phase A (the Surface Pro 11 case above): the
same four tests, but T02 (NV2) right after T01 instead of last (see "A second
finding" above), now reaching you only through the report pages, never the stick
(section 8). Then the device tests, still read-only: T09 DRAM map, T05 GIC, T07
SMMU, T06 PCIe and NVMe, T08 ADSP. A page is drawn after each. The footer names
the running test, and while a device window is being read it names that window
(`T06 ECAM 0x...`, `T06 NVME 0x...`): if the machine hangs, the last footer tells
you which address was being read.

If the image is not at EL2 even here, it runs no test at all (not even the
device tests) and writes no result file: the report is one page saying `NOT AT
EL2: NO TEST WAS RUN`, and the first thing to fix is the slbounce step, not this
image.

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

When T02 ran in phase A (section 2), `x2-nv2.bin`/`.txt`/`.json` on the stick are
the exact record and this retyping is only a cross-check. When T02 ran in phase B
instead (the Surface Pro 11 case), there is no stick file to prefer: the T02
section's `NV2R ...` rows on the report pages are the only record, so photograph
every one of them and retype with the same care, checking every `*hh` digit
before trusting `tools/nv2_record.py show`'s output.

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
| `GO` | `GO` | When T02 ran in phase A (section 2), the record on the stick (`x2-nv2.bin`) is the one the monitor's gate reads: ingest it with `tools/nv2_record.py` for the gate's own decision. When T02 ran in phase B instead (no stick file for it: section 8), retype its `NV2R ...` rows (section 4) into text and ingest that form the same way. A hardware GO record carries evidence OBSERVED either way. The NV2 item of gate X2 (section 6.3) is then evidenced for this unit and revision, by one run. |
| `NO-GO` | `NV NOT IMPLEMENTED`, `NV ONLY, NO NV2`, `NOT REDIRECTED`, `ID SAYS...` | Candidate for kill criterion K1 (SURFACE-PRO-11.md section 3.3: stop and report, do not improvise). Photograph T01 and T02 in full. A repeat after a cold boot is a cheap confirmation; that is this guide's recommendation, not plan text. |
| `INCONCLUSIVE` | `MECHANICS FAULT`, `VNCR PAGE ABORT`, `CONTROL DID NOT TRAP`, `NV2 ONLY, NO NV`, `RESERVED NV/NV_FRAC`, `FUNCTIONAL TEST DID NOT RUN`, `ID REGS NOT A READING` | Do **not** decide K1 from this run. Photograph T02 in full and report it. A control that did not trap while the redirect landed is a contradiction worth a second look, not a GO. A state that could not be restored is not a T02 reason: the image halts on the `FATAL: CLEANUP FAILED` page before any result is shown (section 7). |
| `NOT RUN` | the image was never at EL2, in either phase (the `NOT AT EL2: NO TEST WAS RUN` page of section 2, not the usual summary page) | Fix the slbounce step first. A platform that reaches EL2 only during/after ExitBootServices, like the Surface Pro 11 (section 2), still gets a K1 line from that phase's run: this row means neither phase ran T02, not merely that phase A did not. |

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
* Do **not** remove the USB stick until the report is on screen. On a platform
  that is at EL2 before ExitBootServices, phase A writes the result files there
  (`SP11X2 STICK STATE=OK` where the console is visible); on one that is not
  (section 2), phase A writes nothing for these four tests and there is no such
  line to wait for, so wait for the report itself. Either way, nothing is
  written to the stick after ExitBootServices.
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
and never overwritten, **only by a platform that is at EL2 before
ExitBootServices and so ran T01, T03, T04 and T02 in phase A** (section 2):

| File | Content |
| --- | --- |
| `x2-results.txt` | one block per phase A test: verdict, label, rows, what it proves and does not prove |
| `x2-nv2.txt` | the NV2 record as 13 text lines, each with its `*hh` check (the format of `tools/nv2_record.py`) |
| `x2-nv2.json` | the same record as JSON |
| `x2-nv2.bin` | the same record, 96 bytes with a CRC-32: the form the monitor's gate consumes |

A platform that only reaches EL2 during or after ExitBootServices (OBSERVED,
three times now, on a Surface Pro 11 X1E80100: section 2) runs these same four
tests in phase B instead (T01, then T02, T03, T04 in whatever order they have
not already run: "A second finding" above), and none of these four files exists
for that run: T01 to T04 (with T02's NV2 record, as the `NV2R ...` screen rows
of section 4) are on the report pages only. Phase B's five device tests are always screen only
(nothing may write to the stick after ExitBootServices); photograph every page of
the report, and on a platform like the Surface Pro 11 that includes T01 to T04,
not only T05 to T09.

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
  and whether T02 itself runs and what it decides (OBSERVED, three times now: it
  has not yet run at all. Twice due to the phase-ordering bug above; a third time
  because phase B's fallback, in its original order, ran T03 before T02 and T03
  stalled then the board reset ("A second finding" above) before T02's turn came.
  EXPERIMENTAL otherwise, QEMU cannot reach EL2 this way at all). T01, run
  through the phase-B fallback in that third attempt, is the one phase-A test
  OBSERVED to complete and produce a real, CRC-verified result on this unit
  (`NV=2 NV_FRAC=0`); whether T02 can do the same, now that it runs right after
  T01, is what the fallback's reorder exists to find out. The
  EL2 vector table and the ERET to EL1 mechanism itself is OBSERVED to work on
  the unit's firmware for the phase-B tests that did run (T01, T05, T07, T09 all
  produced a real, non-FATAL result); whether it behaves the same way for T02's
  specific trap is still untried. Also open: which
  framebuffer rule fired (`SEL=` on the `GOP` console line and `gop_select=` in
  `summary.txt`; OBSERVED on the unit both times: `SEL=SAME`), whether
  the page rows fit the panel (the layout is chosen from the framebuffer
  size), whether the UEFI memory map lists the GIC, PCIe and SMMU windows as MMIO
  (the `MAP MMIO=n UNLISTED=n` row of T05 to T07 and the probe's `memmap.txt`;
  if they are not listed, only a build without `--require-mmio-map` reads them),
  that no `FATAL: CLEANUP FAILED` page appears, and whether an asynchronous SError
  ever arrives during a guarded read (T05 to T07 `FAULTS=` rows; the continuation
  masks it and gives up on that register, but no run has ever taken one).
