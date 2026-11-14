<!-- SPDX-License-Identifier: MIT -->
# SP11 X2 EL2 functional tests: operator guide

**Hardware status: K1 decided — GO. NV2 is confirmed functionally usable on this unit.**
OBSERVED, X1E80100 Surface Pro 11, 2026-11-11, run 4 (build `89889B7FEB3C`, the NV2-reorder
fix below): T01 and T02 both ran to completion. T02's verdict is `K1 NV2: GO` (`VNCR_EL2`
redirect PASS, the NV2-off negative control PASS, state restored and cleanup OK), CRC-verified
row by row (90/90) and independently confirmed with `tools/nv2_record.py show --check` against
the retyped record (`gate: ALLOW`). Runs 1-3 (below) found and fixed two bugs before this
result was reachable at all; see "A phase-ordering bug" and "A second finding" for that history.
T03, T04 and items 9-13 of [SURFACE-PRO-11.md](SURFACE-PRO-11.md) section 6.3 were still NOT RUN
after run 4 (the same board reset shortly after T01/T02 that run 3 found, still unexplained, happens again
right after T02 in run 4) — none of that affects K1, which only needs T01 and T02. Run 5 (below)
reproduced K1 GO under the reviewed slbounce and froze on T03 once more. The second version of the
image that follows from it (the order of the tests, T03, T05, T02's REGIME row) ran once on the unit, in
run 6 (below; `declared` variant, one unit, one boot): T03, T04, T05 and T09 PASS, T07 UNKNOWN, K1 NV2: GO
again with `E2H` clear, and T06 never finished, because its read of an ECAM window did not return and the
board reset itself. Items 9-13 are still NOT RUN, and so is the `hardware` variant of the second
version. Evidence
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
through the same on-screen rows and CRC-8 checks as every other phase-B test. (That order was
changed twice since: see "A second finding" and "Run 5".)

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
order (for a platform where it runs normally, before ExitBootServices) is unchanged. (Run 5
changed the fallback order again: see below.)

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
[SURFACE-PRO-11.md](SURFACE-PRO-11.md) section 6.3 remained NOT RUN after this run (T03 and T04 first
ran in run 6, below); none of them bear on K1.
The record is a measurement of this one machine (`tools/nv2_record.py`'s own rule): it is kept
under `build/`, never committed, and never shown here in full; the embedding of it into an
actual monitor build is separate, later work.

## Run 5: the declared image under the reviewed slbounce, K1 GO again, and T03 froze once more

OBSERVED (operator photos; X1E80100 Surface Pro 11; build `CADF663D166C`, the `declared` variant of
section 10, started by the autorun script under the de-VHE slbounce fork build
`SLBOUNCE-DEVHE-MARK.EFI`):

* The autorun script ran with nothing typed.
* The image declared its one range, `EBSRANGES STATE=DECLARED RANGES=1 BASE=0xCEBE7000
  SIZE=0x190000 STATUS=0x0`. The scoped sweep accepted it and the bounce happened: the first page
  said `CURRENTEL=2 PAGE 1/1`.
* T01 and T02 completed with `K1 NV2: GO`: `CTL NV2 OFF: TRAPPED AS EXPECTED`, `RED NV2 ON:
  LANDED IN PAGE`, `ESR CTL=62353421 RED=5A000010`, `RESTORED=YES MECH=NO CLEANUP=OK`.
* The page then froze on the footer `RUNNING T03 TIMER`, as in four earlier runs. In those runs the
  board then reset itself after about 12 s; the operator did not report a reset for this one. T04 to
  T09 did not run: T03 came before them.
* New values from T01: `ID_AA64MMFR0_EL1=2100122100001024` (ECV=2, FGT=1, the 16 KiB granule not
  implemented), `ID_AA64MMFR3_EL1=1000000000000000`, `DCZID_EL0=4` (a 64-byte DC ZVA block, what the
  monitor assumes), `CTR_EL0=B444C004`.

This is the first run of the `declared` variant, and the first K1 result under the reviewed build.
It says nothing new about why T03 freezes.

**INFERRED, not tested: why T03 freezes.** `tests_run_timer` read `CNTPOFF_EL2` whenever
`ID_AA64MMFR0_EL1.ECV` is 2 or more, and here it is 2. EL2 access to that register is also gated at
EL3 by `SCR_EL3.ECVEn`. If the firmware leaves that bit clear, the read is taken to EL3, and a monitor
that does not handle it can hang; the board then resets some seconds later. That matches a frozen
screen with neither a `FATAL` page nor an exception page, which is what every run showed.
`CNTVOFF_EL2` has no EL3 trap control, so it is not the suspect. *Falsified by:* the second version
of the image, which never reads the register, freezing on T03 again (it did not, in run 6 below); the
cause is then elsewhere in T03 (its other register reads or its counter loop). *Supported, not proved,
by:* T03 completing in the second version, without the read (it did, in run 6 below). *Settled by:* a
deliberate, isolated read of `CNTPOFF_EL2`, which belongs in a payload of the q1n1
stick runner (the coordinator's plan) and never in this image's default flow (its first run, the `timer`
payload of boot 4, is described under run 6).

**T05 (GIC) was a test bug, not a hardware finding.** Its first version knew only the GICR
discovery-range structure (MADT type 0x0E), so on this machine it found no redistributor frame for
any CPU. The unit's MADT (OBSERVED from its dump, which is not committed) has twelve GICC structures
(type 0x0B, 80 bytes), each with a `GICR Base Address` (offset 60) that names that CPU's own frame, one
GICD (type 0x0C, base `0x17000000`, GIC version 4), one GIC ITS (type 0x0F) and no type 0x0E
structure. The per-CPU frames are `0x17080000 + 0x40000 * uid`, uid 0 to 11: 256 KiB each, the GICv4
layout. The fixed T05 did not run in this run (T03 came first); it first ran in run 6, below.

**INFERRED, not tested: the regime T02 ran in.** The page did not record `HCR_EL2`. T02's stub keeps
the E2H of the value it finds (`tests_hcr_for`), and q1n1 saw `HCR_EL2` `0x88000010` (E2H clear, TGE
set) under the same slbounce build ([SP11-EL2-ENTRY.md](SP11-EL2-ENTRY.md), OBSERVED there). So the GO
is INFERRED to have been decided with E2H clear: NV2 works in the non-VHE EL2 regime this slbounce build
produces. *Falsified by:* a REGIME row (below) that reads `E2H=1`; the GO then holds for the VHE regime
only. `E2H=0` would confirm it as an observation, not as a verification. (Run 6, below, read `E2H=0` in
its own GO. This run's GO stays INFERRED to have been decided with `E2H` clear, because its page printed no
`HCR_EL2`.)

### The second version of the image

Same two variants (`hardware` and `declared`), everything else unchanged. **Hardware status: the
`declared` variant ran once on the unit, in run 6 below (one unit, one boot); the `hardware` variant is
NOT RUN. Items 2 to 4 below were each exercised by that run (OBSERVED); of item 1 only the last place, T06,
is visible.** Everything else that was run is host tests and QEMU, which are models.

1. **The order after the bounce** is now `T01, T02, T04, T09, T05, T07, T08, T03, T06`. Where phase A
   already ran at EL2 its four tests (T01, T03, T04, T02) are marked run and skipped, and the rest
   follow in this order. Why: T01 first because T02 reads what it read; T02 next because it is what the
   image exists to decide and it has completed on the unit (OBSERVED, runs 4 and 5); T04, T09, T05, T07
   and T08 before T03 because they sat behind it, and the page froze on T03's footer in every run since
   the fallback was introduced (OBSERVED), so none of them had run since, until run 6 below (before it,
   T04 had never run there and no T08 result was recorded; T09, T05 and T07 had given results only in the
   first runs: PASS, a FAIL caused by this image's own MADT parsing, UNKNOWN; OBSERVED,
   [SURFACE-PRO-11.md](SURFACE-PRO-11.md) section 6.3);
   T03 second to last because its cause is INFERRED and untested, so removing one read is not proof that
   it is safe; T06 last because it is the only other test seen cut off by the reset (once, in its PCIe
   ECAM read, OBSERVED) and a read of an ECAM window can hang the bus (INFERRED; run 6 below saw it
   again, in segment 3's window). The page is redrawn after
   every finished test, so a freeze leaves the verdicts of all that finished on the screen (OBSERVED in
   run 6: T06 stopped the board and the verdicts of the other eight tests were already there).
2. **T03 does not read `CNTPOFF_EL2`.** Its row reads `CNTPOFF_EL2 NOT READ (ECV=2)`, its verdict comes
   from the counter and the other registers exactly as before, and what it does not prove is now `N: OTHER
   CORES OR ANY CNTPOFF ACCESS`. Where ECV is below 2 the row is still `CNTPOFF_EL2=N/A (ID ECV<2)`. The
   read is kept behind a build option, `--cntpoff` of `tools/build-uefi-tests.py`, which the emulator
   images may use so that the code path stays tested and which is refused for every image for the
   machine; such an image reads the register, shows `CNTPOFF_EL2=<16 hex>` (or `FAULT`, a FAIL) and
   says what it does not prove as `N: OTHER CORES OR CNTPOFF WRITES`. The `.rdata` tag `SP11X2-CNTPOFF=`
   says which.
3. **T05 takes the redistributor frames from the GICC structures when the MADT has no GICR
   discovery range** and its GICD structure says GIC version 3 or later. One frame of 0x40000 bytes per
   CPU, in ACPI Processor UID order, each validated by the rules of every other window (base not zero,
   aligned, no wrap, inside the physical-address limit, not RAM or firmware in the memory map, and under
   `--require-mmio-map` listed as MMIO) and read the way a frame always was: `GICR_PIDR2`, `GICR_TYPER`
   and `GICR_WAKER`. No Last bit is looked for in this layout and the stride is the table's. A MADT that
   has a type 0x0E structure, kept or dropped as bad, is read exactly as before. The GICD is read as
   before (`GICD_PIDR2` ArchRev, expected 4 here, against the MADT's GIC version).
4. **T02 ends with one more row** (screen only; the NV2 record, its 13 lines and its checks are
   untouched): `REGIME <16 hex> E2H=<0|1> TGE=<0|1>`, the `HCR_EL2` the firmware left, read before
   anything was written and before the stub's `HCR_EL2` was built, with bit 34 (E2H) and bit 27 (TGE)
   spelled out. `REGIME NOT READ` if the read failed or T02 did not start. The label `HCR=` is left out:
   with it the row would be 39 columns and a row holds 38.

The phase A order, the record, the `declared` variant's single declared range (the image, never the
stack or the framebuffer), and the rule that no register is written, are unchanged. T05 reads only the
windows the same allow-list admits; the list now holds 40 windows (it held 16) so that 32 per-CPU frames,
the distributor and the ITS frames fit.

### What the new and changed rows mean

| Row | Meaning |
| --- | --- |
| `REGIME 0000000088000010 E2H=0 TGE=1` (the form; run 6 read exactly this value) | T02 ran in this regime. `E2H=0`: EL2 in its original, non-VHE regime; `E2H=1`: a VHE host. The stub's own `HCR_EL2` keeps this E2H and clears TGE. |
| `CNTPOFF_EL2 NOT READ (ECV=2)` | The register exists (ECV says so) and this image did not touch it. T03's verdict does not depend on it. |
| `GICR GICC FRAMES=12 OF 12` | Twelve frames named by the GICC structures, twelve read as redistributors. Fewer read than named: a frame was refused (a row says why) or the reading stopped at the first frame that faulted or did not identify as GICv3/v4; nothing after it is touched. |
| `GICC GICR<k> REFUSED: <reason>` | The frame of the k-th CPU in UID order was not read, for the reason shown (`BASE IS ZERO`, `MISALIGNED`, `BAD RANGE`, `ALLOW LIST FULL`, `NO MEMORY MAP`, `MAP SAYS NOT MMIO`, `NOT MMIO IN MAP`). At most four are listed, then `GICC GICR: <n> MORE REFUSED`; the `MAP MMIO=.. REFUSED=` row counts all of them. |
| `CPU MATCH SKIPPED: FRAME NOT READ` | Some frame was not read, so no CPU is judged against a frame; the verdict is UNKNOWN, never a FAIL from a frame that was not looked at. |
| `MADT CPUS=12 MATCHED=12 NO FRAME=0` | Every CPU's MPIDR affinity was found in exactly one frame's `GICR_TYPER`. `NO FRAME` counts CPUs whose affinity was found in no frame or in more than one. |
| `GICC BASE DIFF=<n>` | In the **discovery layout** (a type 0x0E range, walked): CPUs whose GICC structure names a non-zero base that differs from the address where the walk found the frame carrying that CPU's affinity. In the **per-CPU layout** the frames are read at the GICC bases themselves, so a base compared with the frame read at it would say nothing; the count is instead how often the frame that carries a CPU's affinity was read at an address other than that CPU's own GICC base. Two bases swapped gives `BASE DIFF=2` with `NO FRAME=0`; one CPU's base naming the next CPU's frame gives `NO FRAME=2` (one CPU with no frame, the next with two) and `BASE DIFF=0`. With two-digit counts `GICR AWAKE=.. ASLEEP=..` and this count no longer fit one row (39 columns) and take one row each. |

The verdicts are as before: GICD version disagreeing with the MADT is FAIL; any window not read is
UNKNOWN; a CPU matched to no frame, or a base that differs, is FAIL; PASS only when every window was
read and everything agrees.

### What to photograph on the next run

Everything, as in section 4 of this guide, and then: the whole screen as soon as it freezes or the
report appears, footer included. The report is redrawn after every finished test, so the summary rows at
the top show which tests finished and with what verdict even on a frozen page; the footer names the test
that was running. If it freezes, the footer is the most valuable photograph. The first run of the second
version (run 6, below) did freeze, on T06, and the verdicts of the other eight tests were on the page. Then:
T02's last rows (the `NV2R END LINES=11` row that closes the record and the `REGIME ...` row under it), all of T03 (the
`CNTPOFF_EL2 NOT READ` row and its verdict), all of T05 (the rows of the table above) and, if T06 runs, every
`T06 ECAM ...` footer: the last one names the window that was being read when the board stopped.

Tests: `python3 -B tools/test-uefi-tests.py XV2SourceTests XV2BuildTests XV2MutantTests XV2QemuTests`
(and the native sections `t03 cntpoff`, `t05 gicc layout`, `nv2 regime` and `hook: post-bounce order`
of `XNativeTests`). The order after the bounce has no console channel (nothing is printed after
ExitBootServices), so QEMU shows only phase A's order on the console; the order after the bounce is
checked natively (the real `hook.c` over models, including a freeze at T03) and in the source.

## Run 6: the second version of the image on the unit: T03 completes, and T06 stops the board

OBSERVED (operator photos, read against enlarged crops; one unit, one boot, 2026-11-14; X1E80100 Surface
Pro 11; build `E5BF1A6BD94C`, the `declared` variant of section 10; image SHA-256 `6b68cab6...aaad`). This
is the first hardware run of the second version of the image; its `hardware` variant is still NOT RUN.
The stick was plugged in directly, with no hub: its UEFI path was `Pci(0x80,0x0)/USB(0x1,0x0)`, one USB
node, where the hub run of the stick survey ([SP11-STICK-LOG.md](SP11-STICK-LOG.md) section 12) had two.
T06 is the last test of the new order, so T01 to T05 and T07 to T09 were on the page before it.

| Test | Verdict | What the page showed |
| --- | --- | --- |
| T01 ID dump | INFO | The ID registers, as in runs 4 and 5. |
| T02 NV2 | FUNC GO | The new row `REGIME 0000000088000010 E2H=0 TGE=1`: the GO was obtained with `E2H` clear. |
| T03 timer | PASS | `CNTFRQ_EL0` `0x124F800` (19.2 MHz); `CNTHCTL_EL2` `0x3` (its ECV bit reads 0); `CNTVOFF_EL2` 0, readable at EL2; `CNTPOFF_EL2 NOT READ (ECV=2)`; the counter advances; `CNTVCT_EL0` plus `CNTVOFF_EL2` lies between two reads of `CNTPCT_EL0`; boot CPU only. |
| T04 DC ZVA | PASS | `DCZID_EL0` block 64 bytes, the monitor needs 4 (`MATCH`); `DC ZVA` did not fault; nothing changed outside the block. |
| T05 GIC | PASS | `GICD` `0x17000000`, `ARCH=4` (the MADT says 4), `CTLR` `0x10`, `TYPER` `0xF87F051E`, `IIDR` `0x0401043B`, `ARE_NS=1`; redistributor frames derived from the GICC structures: 12 of 12; `MADT CPUS=12 MATCHED=12`; `AWAKE=12 ASLEEP=0`; `GICC BASE DIFF=0`; `ITS0` `CTLR` `0x80000000`, `ARCH=4`; `FAULTS=0`. |
| T06 PCIe and NVMe | NOT RUN | The last footer was `T06 ECAM 0740000000`. The read never returned and the board rebooted by itself (operator report). |
| T07 PCIe SMMU | UNKNOWN | The read of the SMMUv3 at `0x15400000` faulted (guarded and recoverable): `NO STATE`. |
| T08 ADSP | UNIMPL | Nothing touched, as designed. |
| T09 DRAM | PASS | RAM total 32289 MiB in 7 extents, the highest ending at `0x1000000000` (2^36); the list is cut after 4 rows; no comparison with a descriptor was made. |

**T02 and the regime (OBSERVED).** `REGIME 0000000088000010 E2H=0 TGE=1` is the `HCR_EL2` the firmware left,
read before T02 built its own, so this GO was obtained with `E2H` clear: FEAT_NV2 works in the non-VHE EL2
regime (M8 of [SP11-EL2-ENTRY.md](SP11-EL2-ENTRY.md); one boot). K1 stays GO. This is the reading the
"regime" paragraph of Run 5 named as the confirmation; Run 5's own GO stays INFERRED to have been decided
with `E2H` clear, because that page printed no `HCR_EL2`.

**T03 completed without the read (OBSERVED). That supports, but does not prove, the explanation of the
earlier freezes (INFERRED).** T03 froze on the footer `RUNNING T03 TIMER` five times, each time with the
`CNTPOFF_EL2` read in it. Here the read was left out and T03 completed. `CNTVOFF_EL2` read 0, so reading
it is not what stopped the board. That is what the explanation of Run 5 predicts (the read is gated at EL3
by `SCR_EL3.ECVEn`, and a clear bit would send it to EL3), and it does not prove it: one boot, in another
place in the order of the tests, and the cause itself (`SCR_EL3.ECVEn` clear) stays INFERRED. The isolated
read that Run 5 named as the settling test has been tried once since, in the `timer` payload of the q1n1
stick runner (boot 4 of the same day). Its panel text was not captured; its line count reads as a stop
right after the line that announces the `CNTPOFF_EL2` read, and the board then rebooted by itself (INFERRED;
[SURFACE-PRO-11.md](SURFACE-PRO-11.md), the "Fewer boots" paragraph). Both observations point at that read
and not at the rest of T03; neither shows why it does not return. T03 is boot CPU only (`CORES: BOOT CPU
ONLY`); the other cores were not tested (the `psci` payload of boot 5 saw the other eleven cores OFF, and
`PSCI_FEATURES` returned 0 for `CPU_ON`, which means implemented, INFERRED from the PSCI specification;
none was started).

**T05: the per-CPU path works (OBSERVED).** This MADT has twelve GICC structures and no GICR discovery
range (Run 5), and the second version of T05 takes the redistributor frames from the GICC structures. It
read 12 of 12; each MADT CPU matched exactly one frame's `GICR_TYPER` (`MADT CPUS=12 MATCHED=12`); no
frame was read at an address other than its CPU's own GICC base (`GICC BASE DIFF=0`); the GICD's `ARCH=4`
agreed with the MADT. The test bug of Run 5 is fixed. T05 still does not prove that interrupts are
delivered.

**T06: the read of segment 3's ECAM window never returned (OBSERVED); the cause is INFERRED.** The last
footer was `T06 ECAM 0740000000`, the base of the MCFG entry of segment 3 (buses 0 to 1). The read did not
return and the board rebooted by itself (operator report; the time was not recorded). A hang followed by a
watchdog reset is INFERRED, as for the T03 freezes. T06 never finished, so its own page was never drawn;
the verdicts of the other eight tests were already on the screen, which is what the order of the second
version is for. T06 reads the MCFG windows in table order, so the windows before segment 3 had been handled
(read or refused) when it stopped; which, and what they returned, is not known (their rows were never
drawn). A later T06 would have to leave segment 3's window alone (UNIMPLEMENTED: no such image exists).

**T07: UNKNOWN again (OBSERVED).** The read of the SMMUv3 at `0x15400000` faulted. The fault was guarded and
recoverable (the image went on) and T07 shows `NO STATE`. The IORT gives this one SMMUv3 to all eight PCIe
segments (below), so the NVMe's translation state at EL2 is still not known. T07 was UNKNOWN in the earliest
runs too, from a faulting register read ([SURFACE-PRO-11.md](SURFACE-PRO-11.md) section 6.3). Why the window
faults is UNKNOWN.

**T09 (OBSERVED).** RAM totals 32289 MiB in 7 extents and the highest ends exactly at `0x1000000000`, so no
RAM lies at or above 2^36: the 32 GiB case of E23 in the plan is this unit, now from a test of the image
and not only from the probe's dumps. This is the firmware's view, not a DRAM controller read.

**What the unit's ACPI tables say about PCIe (OBSERVED in the dump, which is not committed; the NVMe's place
is INFERRED).** The DSDT has eight PCI roots with `_SEG` = `_UID` = 0 to 7, and the MCFG has one entry for
each:

| Segment | ECAM base | Buses | Note |
| --- | --- | --- | --- |
| 0 | `0x400000000` | 0-255 | |
| 1 | `0x500000000` | 0-255 | |
| 2 | `0x6000000000` | 0-255 | above 2^36, the monitor's IPA limit |
| 3 | `0x740000000` | 0-1 | where T06 stopped |
| 4 | `0x7c000000` | 0-1 | equal to the DBI base of `pcie4`, the Wi-Fi controller (plan, Appendix A) |
| 5 | `0x7e000000` | 0-1 | |
| 6 | `0x70000000` | 0-1 | the NVMe's segment (INFERRED, below); equal to the DBI base of `pcie6a` (plan, E14) |
| 7 | `0x74000000` | 0-1 | |

The firmware's block device path for the NVMe is `PcieRoot(0x6)/Pci(0x0,0x0)/Pci(0x0,0x0)/NVMe(...)` (the
identifier is left out), so the NVMe is behind the root with ACPI `_UID` 6: segment 6, ECAM base
`0x70000000` (INFERRED from `_UID` = `_SEG`). The IORT maps each segment n's stream ids to the single SMMUv3
node at `0x15400000`, with output base `0xn0000`; the NVMe, at bus 1, device 0, function 0, would be stream
`0x60100` (INFERRED). These agree with the device-tree values of the plan (`pcie6a` DBI `0x70000000` and
stream window `0x60000`, E14 and E24; `pcie4` DBI `0x7c000000` and the Wi-Fi's stream `0x40100`, Appendix A),
which they would if segments 4 and 6 were those controllers (INFERRED). Segment 3 is not the NVMe's window
(INFERRED); T06 reached it because it reads the windows in table order.

**What this changes.** T03, T04, T05 and T09 are the first hardware results for items 3 (boot CPU only), 4
(at EL2), 5 and 8 of [SURFACE-PRO-11.md](SURFACE-PRO-11.md) section 6.3. None bears on K1, which stays GO,
now with its regime read. Still open: whether `CNTPOFF_EL2` works (no read of it has been seen to return),
`CNTVOFF_EL2` on the other cores, the state of the NVMe and of the PCIe SMMU at EL2 (T06 stopped the board,
T07 faulted) and the ADSP (T08 is UNIMPL). T06 as built stops the board at segment 3's window on this unit
(OBSERVED once).

## 1. Before you start

* A hardware build needs the SoC named explicitly (the NV2 record carries it and
  the monitor's gate refuses a record for another SoC):

  ```sh
  python3 tools/build-uefi-tests.py --soc x1e80100          # build/uefi/sp11-el2-tests.efi
  python3 -B tools/test-uefi-tests.py                       # host tests, one command
  ```

  Only `sp11-el2-tests.efi` is for the machine, and `sp11-el2-tests-declared.efi` only under
  the reviewed scoped-sweep slbounce (section 10). The `test/` directory holds
  emulator builds (`model`, `model-fatal`, `model-fatal-pre`, `model-declared`): never run those
  on hardware, they shorten the timers and skip the reset. `--cntpoff` (T03 reads `CNTPOFF_EL2`,
  see "Run 5") is for those emulator builds only: the tool refuses it for every image for the machine.
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

**Phase B, after ExitBootServices (screen only).** One list, in this order (the second version of
the image; "Run 5" above gives the reasons): T01 ID dump, T02 NV2, T04 DC ZVA, T09 DRAM map, T05 GIC,
T07 SMMU, T08 ADSP, T03 timer, T06 PCIe and NVMe. A test phase A already ran is skipped, so none runs
twice. On a platform where phase A could not run (the Surface Pro 11 case above) that is every test,
and T01 to T04 reach you, like the device tests, only through the report pages, never the stick
(section 8). The device tests are still read-only. A page is drawn after each. The footer names
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
* The second version of the image has new or changed rows (T02's `REGIME`, T03's `CNTPOFF_EL2 NOT
  READ`, and T05's `GICR GICC FRAMES` and `GICC GICR<k> REFUSED`): the table under "Run 5" says what
  each means. The `REGIME` row is screen only; the NV2 record is unchanged.

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
| T02 NV2 | An EL1 stub runs with `HCR_EL2.NV` set: the control (NV2 off) must trap with the EC 0x18 syndrome of an `mrs` of `TPIDR_EL2`; with NV2 on and `VNCR_EL2` at a page, the stub's write of `TPIDR_EL2` must land at VNCR offset `0x090` and the real register must not change. A last row, `REGIME`, shows the `HCR_EL2` the firmware left, read before the stub's own was built (screen only; the record is unchanged) | whether NV2 redirects on this core, and that the negative control behaves | anything about nested guests beyond the two accesses |
| T03 timer | CNTFRQ, CNTHCTL_EL2.ECV, CNTVOFF_EL2, that the counter advances and that CNTVCT + CNTVOFF lies between two physical reads. `CNTPOFF_EL2` is not read by the images for the machine (`CNTPOFF_EL2 NOT READ (ECV=n)`); an emulator build made with `--cntpoff` reads it, if ID ECV >= 2 | the boot CPU's counter and offsets are coherent | other cores (see the open gap below) |
| T04 DC ZVA | The block size, then one `DC ZVA` into the middle third of an image-owned buffer: exactly one block zeroed, both neighbours untouched | DC ZVA works and the block size is what the monitor assumes (64 bytes) | behaviour on device or Device-type memory |
| T05 GIC | GICD/GICR/ITS registers at the addresses the **MADT** gives, the redistributor frames (a walk of the MADT's GICR discovery ranges or, when it has none, the frame each GICC structure names: "Run 5"), and each MADT CPU against a redistributor frame; the board descriptor is shown as a cross-check row (`D= R= I= C=` as `OK`, `DIFF` or `UNK`). A base that is zero, misaligned or wrapping, or that the memory map calls RAM or firmware, is not read: the row says `GICD REFUSED: <reason>` and the verdict is UNKNOWN, never FAIL | the MADT describes a working GIC | that interrupts are delivered |
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
* Do not run the `model`, `model-fatal`, `model-fatal-pre` or `model-declared` images, and do not
  edit tracked sources between building the image and running it.
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
tests in phase B instead (in the order of section 2), and none of these four files exists
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

## 10. The `declared` image, for the reviewed scoped-sweep slbounce

**Hardware status of this step: OBSERVED to work on the unit in runs 5 and 6 (one unit, one boot each).**
Run 5 printed `EBSRANGES STATE=DECLARED RANGES=1 ... STATUS=0x0`, the scoped sweep accepted the range and
the bounce was taken; run 6 reached EL2 as well. The host tests and the QEMU run below are models; they
say nothing about the unit's firmware or about slbounce running on it.

The image above never tells slbounce which memory it dirtied. The reviewed slbounce fork
(`SLBOUNCE_SCOPED_SWEEP=1`) flushes only the physical ranges an application declares in a volatile
UEFI variable before its Secure-Launch bounce, and returns `EFI_ABORTED` from its ExitBootServices
hook, printing nothing, when the variable is absent or names a range it cannot justify (q1n1
declares its ranges this way; see [SP11-Q1N1-BOOT-STUDY.md](SP11-Q1N1-BOOT-STUDY.md) section 4.4).
Under that build `sp11-el2-tests.efi` would simply be refused at ExitBootServices (read from the
fork's source, `src/bounce_main.c`; not run). Runs 1 to 4
above used a test-only slbounce build with its cache sweep compiled out, which is not part of the
reviewed set. The `declared` image is the same image plus that declaration, so the NV2 test can be
run under the reviewed build. It is separate and opt-in; the gate leaves the image above as it was (a
host test compared the text of `probe_main.c` and `hook.c` with the gate off against the revision
from before this variant existed; it is a SKIP once HEAD has the gate, and the second version of
the image, "Run 5", changed `hook.c` on purpose, so that comparison no longer holds against that
old revision. What still proves the gate inert is the test that drops every gated block from
`probe_main.c` and compares the text.)

```sh
python3 tools/build-uefi-tests.py --soc x1e80100 --variant declared    # build/uefi/sp11-el2-tests-declared.efi
```

It is never built by default and needs `--soc` like the hardware image. `strings` on the image
shows `SP11X2-EBS=DECLARED-IMAGE-RANGE`; the hardware image has no such string.

* **What it declares:** exactly one range, this image's whole PE image (`ImageBase` and
  `ImageSize` of its loaded-image protocol), with a 1 MiB chunk cap. Every buffer the image reads
  after the bounce (the memory-map buffers, the dump and result text, the tests' state, the EL1
  stub and the VNCR page) is static storage inside it, so one range covers them. The stack and the
  framebuffer are not declared: the stack is the firmware's, and the framebuffer is a RESERVED
  descriptor on the unit, which the fork refuses by design.
* **What it checks first:** the range against the image's own memory map by the fork's rule
  (inside exactly one descriptor, of type 1 to 6 or 9, with write-back), plus a page-aligned
  base, no wrap and at most 512 descriptors. The fork refuses silently, so a refusal here is the
  readable one: a firmware console line `SP11PROBE EBS STATE=SKIPPED REASON=...` with `BASE` and
  `SIZE` (whether that console is visible on this machine is UNKNOWN, as for the `SP11X2` lines of
  section 2), after which the image waits 10 s and returns to the Shell without calling
  ExitBootServices. The reasons are `RANGE NOT IN ONE DESCRIPTOR`, `DESCRIPTOR TYPE NOT SWEPT`,
  `DESCRIPTOR NOT WRITE-BACK`, `RANGE BASE UNALIGNED`, `RANGE SIZE ZERO`, `RANGE WRAPS`,
  `MAP EMPTY`, `MAP OVER 512 DESCRIPTORS`, `MAP BAD DESCRIPTOR`, `NO LOADED IMAGE` and
  `SET VARIABLE FAILED` (with `STATUS=`). Record the line and report it.
* **What success looks like:** `SP11PROBE EBSRANGES STATE=DECLARED RANGES=1 BASE=0x... SIZE=0x...
  STATUS=0x0000000000000000` before the usual `EBS STATE=CALLING`.
* **Limits.** INFERRED, not tested: nothing the image reads after the bounce depends on the lines
  that are not declared, the firmware's stack among them. The runs without any sweep worked, so
  declaring the image is at least as protective as them. OBSERVED in run 5 (`STATUS=0x0` and the
  bounce): this firmware's memory map holds the image in one descriptor that passes the rule. The X1 probe
  dumps (OBSERVED) list 226 to 227 descriptors, under the fork's limit of 512, and every
  descriptor of the swept types has write-back set, but they do not say which descriptor holds
  this image. The variable is volatile and the image does not delete it if ExitBootServices then
  fails; a reset clears it. A slbounce build that does not read the variable ignores it (INFERRED).

Tests: `python3 -B tools/test-uefi-tests.py XEbsRangesTests XDeclaredBuildTests XDeclaredAuditTests
XDeclaredQemuTests`. Setting `SLBOUNCE_SP11_DIR` to a checkout of the fork also compiles the
fork's own validator and range rule against the bytes the image builds; without it that test is
reported as SKIP.
