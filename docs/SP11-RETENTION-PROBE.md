<!-- SPDX-License-Identifier: MIT -->
# SP11 DRAM retention probe M10: results as files, not photographs

**EXPERIMENTAL; hardware NOT RUN.** Nothing in this document, in the tools and tests it names, or in the fact that files are staged on a stick
authorises a run: each run needs the operator's written approval of a record (section 9). Evidence labels as in the other SP11 documents:
VERIFIED, OBSERVED, INFERRED, EXPERIMENTAL, UNIMPLEMENTED, UNKNOWN. Nothing about the unit's DRAM, its firmware's reset or its Shell is
OBSERVED here: those are what the probe measures. What was run is a host model and QEMU.

## 1. The problem and the constraints

Today a hardware run ends as a page on the panel that the operator films, and a reset takes the page with it. The aim is that every run hands
its result over as a **file on the stick**. The route studied here is the one of [SP11-HOST-LINK-STUDY.md](SP11-HOST-LINK-STUDY.md)
section 8: write the report into RAM that might survive a reset, reset, and let the next boot of the stick copy that RAM to the stick as text,
with the Shell's own tools. It depends on three things nobody has measured, and the probe exists to measure them.

| Fact the design is built around | Label | Source |
| --- | --- | --- |
| The operator starts every boot of the stick by hand (power off, Volume-Down with Power); after a restart by itself the unit follows its normal boot order and does not come back to the stick | OBSERVED (operator report, after boot 4 and the restart probe) | [SP11-STICK-LOG.md](SP11-STICK-LOG.md), q1n1 `docs/SP11-PAYLOAD-RESULTS.md` |
| Changing the firmware boot order is a persistent firmware setting: not authorised | rule | AGENTS.md section 5 |
| No code at EL2 writes a xHC register or a sector of the stick (authorisation points A1 and A2 are not granted); the only stick writes are the UEFI Shell's, like the DONE markers it already writes | rule | [SP11-STICK-LOG.md](SP11-STICK-LOG.md) |
| `SYSTEM_RESET` is answered as implemented, `SYSTEM_RESET2` as `NOT_SUPPORTED`; UEFI `ResetSystem` does not work after the bounce | OBSERVED once each (boot 5; X1) | q1n1 `docs/SP11-PAYLOAD-RESULTS.md`, [SP11-STICK-LOG.md](SP11-STICK-LOG.md) |
| `SYSTEM_RESET` is the architectural cold-reset call and the warm reset is `SYSTEM_RESET2`, so a NONE after `PSCI_RESET` is a likely outcome of the first run and says nothing is wrong | INFERRED (the PSCI text was not re-read here) | the same |
| DRAM keeps its bytes across a reset; the point of coherency that `DC CVAC` reaches is DRAM; firmware does not use or clear a chosen page before the next boot | UNKNOWN | the probe |
| A held Volume-Down key makes the firmware start the stick after a restart by itself | UNKNOWN | the first run |

## 2. What was built, and where

| File | What it is |
| --- | --- |
| q1n1 `payloads/retprobe.c`, `docs/SP11-PAYLOAD-RETPROBE.md` | the probe: PHASE 1 asks `PSCI_FEATURES`; PHASE 2, only with a configuration file, writes three canary pages and resets by the path the file names |
| `tools/sp11_retention.py` | the page and configuration formats, the WHOLE, PARTIAL and NONE rule, the unit's pages, checks of pages against a memory map |
| `tools/sp11_autorun.py` | the autorun step kind `RETENTION-EXTRACT`, the plans `retention-m10` and `retention-m10-auto`, the data names those plans accept (`PAYLOAD.MAN`, `RETPROBE.BIN`, `RETPATH.DAT` and nothing else) and the manifest writer |
| `tools/stage-sp11-boot-media.py` | one new option of `autorun`: `--stick-first-authorised`, and the staging time it records in `PLAN.TXT` for the retention plans |
| `tools/sp11_retention_plan.py` | the plan builder: the configuration file, the payload manifest, the authorisation record text, the operator's actions |
| `tools/sp11_retention_check.py` | the verdict tool: reads `RET<n>.TXT` and says WHOLE, PARTIAL or NONE per page and per reset path; refuses a stale result and counts trials |

## 3. The pages, and why these

The payload takes its three page addresses from the configuration file, not from its code. The unit's choice, in `UNIT_PAGES` of
`tools/sp11_retention.py`, comes from the memory maps of the six runs that exist (`downloads/sp11/x1-run01` to `x1-run03` and `x2-run10` to
`x2-run12`, local inputs): the big conventional extents are the same in all six (OBSERVED, one unit). A page must be inside one conventional
extent, outside what the runner and the wrapper write. The host check reads every row of the six maps (226 to 231 descriptors each, about a
hundred of them named with spaces, `BS DATA` and `RT DATA` among them, which are firmware's): each of the three pages is in exactly one extent, of
type 07, in all six. A first version skipped the rows with spaces in their names and so saw none of the extents firmware owns; the payload's own
check against the map of the boot it runs in remains the gate.

| Page | Address | Why | Risk (INFERRED) |
| --- | --- | --- | --- |
| 0 | `0x9C000000` | the middle of the 339 MiB extent `0x91480000` to `0xA6800000`: far from both ends, where allocators work | a firmware phase that uses the middle |
| 1 | `0xF8400000` | the rule of the study, section 8.2: the top 1 MiB of the highest conventional extent below 4 GiB (`0xE69C0000` to `0xF8500000`) | the top of an extent is where a top-down allocator starts |
| 2 | `0xFFFF00000` | the first page of the top 1 MiB of RAM, above where the wrapper places the guest (`0x880000000`, `0x2A000000` bytes) | the top of RAM is what EDK2 reuses first: under QEMU a page 16 MiB below the top of 1 GiB of RAM was overwritten by the next boot's pool data (OBSERVED, QEMU only, one page) |

Three pages in three places so that one run says something even if firmware took one of them: the memory map of the extraction boot, which
the extract step appends to its file, says for each page whether firmware owned it in that boot (section 5). The runner puts a big data file
at the top of the 590 MiB extent (`0xC3E65000` for 144 MiB, OBSERVED in boots 8 and 9): none of the pages is near it.

## 4. The probe and its reset paths

The payload, its page and configuration layouts, its lines, its return values and the rule that decides WHOLE, PARTIAL or NONE are in q1n1
`docs/SP11-PAYLOAD-RETPROBE.md`; this document does not repeat them. What matters here:

* **Order.** It reads the previous boot's three pages first (a second reader, independent of the Shell's dump and of the Mac), writes the new
  ones, cleans every cache line with `DC CVAC` and a `DSB SY`, verifies them by a read-back, prints `NEXT RESET AFTER <n> S`, waits, and calls the
  configured reset once. Each page carries a header (magic, layout version, boot counter, the reset path used, a counter-clock timestamp, the
  `PSCI_FEATURES` answers, the memory-map descriptor that held it, a CRC-32) and a body that increments with the boot counter, so that the page
  says by itself which write it comes from and which words are intact.
* **Reset paths.** `NONE` (the default, the negative control: the canaries stay for a reset the operator makes), `PSCI_RESET` (`SYSTEM_RESET`,
  `0x84000009`) and `PSCI_RESET2` (`SYSTEM_RESET2`, `0xC4000012`, the architectural warm reset). Each call is gated: it is made only when
  `PSCI_FEATURES` reported that function implemented, only after the pages verified and the countdown ran, and never after a failed check;
  otherwise the payload refuses before any store. On the unit `SYSTEM_RESET2` was answered `NOT_SUPPORTED`, so the plan builder refuses to build
  that run (`--expect-refusal` builds it as a test of the gate). UNIMPLEMENTED: UEFI `ResetSystem` (OBSERVED not to work after the bounce), a
  forced hang for a watchdog, and a long power-button reset (that is path `NONE`, done by the operator).
* **What it does not do.** It writes no system register but `PAR_EL1` (the answer of its two translation requests), calls no other function, and
  writes nothing to the stick or the disk. Its stores are the three pages. It reads, without any guard, the runner's preload table, its
  configuration file, the runner's memory map and, after the stores, the three pages it wrote; only the previous contents of the three pages
  are read through the guarded load. A `SYSTEM_RESET` that nothing quiesces acts on the stick's FAT and the internal NVMe as a power cut would
  (INFERRED): the one stick write before it, `DONE1.TXT`, is at least 5 s earlier (the pause of step 1) and this plan writes nothing to the internal disk.
* **What a run cannot show.** `SYSTEM_RESET` is the architectural cold-reset call and the warm reset is `SYSTEM_RESET2`, which the unit answered
  `NOT_SUPPORTED` once (OBSERVED; the PSCI text was not re-read here: INFERRED), so a NONE after path `PSCI_RESET` is a likely outcome and says
  nothing is wrong. `DC CVAC` and `DC CIVAC` reach the point of coherency and no further: a system cache (LLCC), a buffer of the memory
  controller, DRAM's self-refresh and a scrub of memory at boot are not named by any instruction of the probe, and a lost page does not say which
  of them lost it. `AT S1E2R` and `AT S1E2W` show that EL2's stage 1 maps the page, not that TrustZone, an XPU or QHEE leave the physical page
  alone outside UEFI's memory map (INFERRED: nothing here can see their use): a store the hardware rejected would be an external abort, which only
  a power cycle ends. A counter that runs backwards is refused like one that stops: the unsigned difference would otherwise read as an endless wait.

## 5. The stick side: the extract step

The plan `retention-m10` is two steps: the payload runner after `load` of the slbounce build (step 1, which ends in the reset), and the extract
step (step 2). The extract step is a new kind of step of `tools/sp11_autorun.py`; in `PLAN.TXT` it reads
`extract  RETENTION-EXTRACT 0x9C000000 0xF8400000 0xFFFF00000`. Its block of the Shell script is generated and checked line by line:

<!-- retention-extract -->
```
:step2
if exist SP11\DONE2.TXT then
  goto done
endif
if exist SP11\RET2.TXT then
  echo "AUTORUN REFUSED: SP11\RET2.TXT exists and is never overwritten, nothing was run"
  goto end
endif
dmem 0x9C000000 0x1000 >a SP11\RET2.TXT
dmem 0xF8400000 0x1000 >>a SP11\RET2.TXT
dmem 0xFFFF00000 0x1000 >>a SP11\RET2.TXT
memmap >>a SP11\RET2.TXT
date >>a SP11\RET2.TXT
time >>a SP11\RET2.TXT
echo "RETENTION-EXTRACT-END plan=retention-m10 step=2 pages=3" >>a SP11\RET2.TXT
echo "AUTORUN retention-m10 STEP 2 EXTRACTED" >a SP11\DONE2.TXT
if not exist SP11\DONE2.TXT then
  echo "AUTORUN REFUSED: cannot write SP11\DONE2.TXT, nothing was run"
  goto end
endif
echo "AUTORUN retention-m10 STEP 2 OF 2: SP11\RET2.TXT is written, power off and take the stick to the Mac"
goto end
```
<!-- /retention-extract -->

* **The dump comes first.** Between the guards and the first `dmem` there is no stall, no `load`, no run, no marker and no console line; the
  line checker (`script_problems`) rejects any block where something else stands there, and the generator cannot emit one. The pause of
  step 1 ("Power off now to cancel") has no counterpart here: ESC at the Shell's own countdown is the way to stop it.
* **The marker comes last.** Other steps write their marker before they run, so that a hang is not repeated; here a marker written before the
  dump would be a write to the stick before the experiment, and a marker after a dump that did not finish would claim it did. The end line is
  written before the marker, so a file without it is a truncated one, and the guard refuses to start over an existing result: `--clear`
  deletes only the markers, never a result.
* **The clock.** After the memory map the block writes the Shell's `date` and `time`, and the stager records its own staging time in `PLAN.TXT`:
  that pair is how the Mac tool tells a result of this run from one an earlier run left on the stick (section 6). The two lines come after the
  dumps and before the end line, so the experiment does not wait for them. A file without them is still judged by `check`, which says that
  nothing tells when it was extracted, and refused by `stick` (`NO_CLOCK`), which could not tell it from a stale one.
* **Why `dmem` and not an EFI application.** `dmem` is in the pinned Shell, which is already hashed, staged and trusted for the DONE markers; the
  Shell stays the only writer of the stick, and no new binary has to be built, reviewed and hashed. A minimal application could write the
  pages as binary and be faster, and would be the answer if the Shell cannot (for example, if `dmem` aborts on a page firmware left unmapped:
  UNKNOWN on the unit). `>a` writes ASCII (a plain `>` writes UCS-2, which the checker and the tool refuse).
* **Limits (OBSERVED under QEMU with the pinned Shell unless said otherwise).** The text is about five times the data: a page is 256 lines
  and 20.8 KB, so the file is about 62 KB of pages plus the memory map (about 80 bytes a descriptor; the unit's maps have 226 to 231, INFERRED
  about 18 KB): roughly 85 KB; the QEMU files are about 66 KB. A line's address is the **low 32 bits** of the address (`80000000:` for
  `0x180000000`), while the heading `Memory Address 0000000180000000 1000 Bytes` has all 64 bits: the tool reads both. The speed on the unit
  is UNKNOWN. The Shell may use a page of free memory for its own buffers between the start of the boot and the dump; the memory map is taken
  after the dumps and says only who owns a page then.

## 6. The verdict tool

`python3 tools/sp11_retention_check.py check RET2.TXT --config RETPATH.DAT` (or `stick <the stick's mount point>`, which reads
`SP11/RET*.TXT`, `SP11/RETPATH.DAT`, `SP11/PLAN.TXT` and the times of the markers; `series` takes several files) reads only. A page is **WHOLE** when its header names this page
(magic, version, index, address, a counter from 1), its CRC holds and no body word is wrong; **PARTIAL** when the header names it and something is wrong
(the wrong words and their bytes are listed) or when the header is lost and at least 16 words of one write survive (which bytes); **NONE**
otherwise. It prints the reset path (the configuration's request and what the pages record), the `PSCI_FEATURES` answers the probe read in PHASE 1 (they
travel in the pages' headers, so they reach the Mac as data), what the memory map of the extraction boot says
about each page, and the boot counter step. For a result in which one page is torn and one was lost:

<!-- retention-report -->
```
SP11 RETENTION RESULT RET2.TXT plan=retention-m10 step=2
extracted: 11/17/2026 01:20:58 (LOCAL), the unit's clock as its Shell printed it
reset path: PSCI_RESET (requested by the configuration: PSCI_RESET; recorded by the pages: PSCI_RESET)
PSCI_FEATURES as the probe read them: SYSTEM_RESET=0x0 (implemented), SYSTEM_RESET2=0xFFFFFFFF (NOT_SUPPORTED(-1))
page 0  0x9C000000  WHOLE   counter 4, CRC ok, 992 of 992 body words; map: free in the extraction boot (Available)
page 1  0xF8400000  PARTIAL header names the page (counter 4), CRC BAD, 1 of 992 body words wrong, damaged bytes 0x12C-0x130; map: free in the extraction boot (Available)
page 2  0xFFFF00000  NONE    no canary found; map: owned by firmware in the extraction boot (BS_Data): damage or loss here is no evidence against retention
boot counter: this boot 4, previous 3, step +1 (the probe found whole pages of boot 3 when it started)
verdict for path PSCI_RESET: WHOLE (WHOLE x1, PARTIAL x1, NONE x1)
```
<!-- /retention-report -->

The boot counter step is `this boot` minus `previous`: the probe took the highest WHOLE counter it found when it started, so a step of +1 with a
previous above 0 says that the pages of an earlier run were still whole at the start of this one. The tool **refuses** a file it will not judge,
with a named reason and exit status 2: <!-- retention-reasons -->`EMPTY_FILE`, `UCS2_OUTPUT`, `NOT_ASCII`, `NO_END_LINE` (truncated),
`END_LINE_MALFORMED`, `END_LINE_PAGE_COUNT`, `END_LINE_STEP_MISMATCH`, `BLOCK_SIZE`, `BLOCK_ADDRESS_DUPLICATE`, `BLOCK_SHORT`, `BLOCK_LONG`,
`LINE_MALFORMED`, `LINE_ADDRESS`, `ASCII_COLUMN`, `BLOCK_COUNT`, `UNEXPECTED_LINE`, `MEMMAP_MALFORMED`, `NO_CLOCK`, `PAGES_DIFFER_FROM_CONFIG`,
`CONFIG_REFUSED`, `NO_RESULT_FILE`, `NO_MEMORY_MAP`, `SERIES_FILE_REPEATED`, `SERIES_TRIAL_REPEATED`, `NO_STAGING_TIME`, `NO_STEP_MARKER`,
`STALE_STEP_MARKER` and `STALE_RESULT`<!-- /retention-reasons -->. The last seven are the checks of a series and of a stick (below).

**What a verdict says.** It is a statement about the bytes in the file. NONE on every page does not say that the probe wrote anything (a probe
that refused leaves no trace): only the last line of its panel does, and the tool says so. NONE on a page the memory map shows as owned by
firmware is not evidence against retention. The study's pass rule (section 8.3) is applied by `series`: a path passes when a page is whole in
ten consecutive trials, each with every whole page free memory in the extraction boot; any trial without a whole page resets the run.

**A series counts trials, and only trials it can judge.** `series` refuses (`SERIES_FILE_REPEATED`) a file that is byte for byte one it has already
read, and (`SERIES_TRIAL_REPEATED`) a file whose whole page carries the boot counter and the timestamp of one it has read: the probe stamps the
counter clock into its pages, and the boot counter restarts at 1 whenever the pages were lost, so only that pair tells one write from another, and
one dump counted ten times is not ten trials. It refuses (`NO_MEMORY_MAP`) a file with no memory map, because without the map nothing says whether firmware owned a
page (`check` still judges such a file alone and says so), and a whole page that the map puts in no extent is not "free there": the trial is not kept.
A file whose whole pages carry different boot counters (a stale page beside new ones) warns, and is not kept either.

**The stick, and the clock.** The extract step writes the Shell's `date` and `time` after the memory map (OBSERVED under QEMU with the pinned Shell:
`11/17/2026` and `01:20:58 (LOCAL)`; QEMU's clock reads UTC, the unit's read local time on 2026-11-15, OBSERVED once, in
[SP11-STICK-LOG.md](SP11-STICK-LOG.md)), and the stager records the staging computer's local time in `PLAN.TXT` (`staged: 2026-11-17 05:20:33`, for
the retention plans only; no other plan's `PLAN.TXT` changes). `stick` prints both and the file times of `DONE1.TXT`, `RET2.TXT` and `DONE2.TXT`, and
refuses a stick that cannot be judged: no staging time (`NO_STAGING_TIME`), no `DONE1.TXT` (`NO_STEP_MARKER`: the probe never started), a `DONE1.TXT`
that is not later than the staging (`STALE_STEP_MARKER`: step 1 was skipped, and the extraction is a dump of RAM that nothing wrote, a NONE that means
nothing), a result with no date and time (`NO_CLOCK`) or a result whose own clock is not later than the staging (`STALE_RESULT`: step 2 refused to
overwrite an old result, and the old one would be read as a false WHOLE). It warns when the file time of the result is not the time written in it
(the file was copied or edited). The comparison
assumes the unit's clock is right and in the staging computer's zone (UNKNOWN for the next run, INFERRED from one observation); if it is not, `stick`
refuses a good result and `check` judges the file alone, without any of this. A hand-written `PLAN.TXT` can carry any time: this is a defence against a
stale file, not against an operator who edits the record.

## 7. The plans and the operator's actions

The builder prints these, with the record of section 9; the plan `retention-m10` has two operator actions and no photograph:

<!-- retention-actions -->
```
OPERATOR ACTIONS FOR PLAN retention-m10
Reset path of this run: PSCI_RESET (the probe calls SYSTEM_RESET after a countdown of 8 s).

Before you start
  - The unit is on AC power, or the battery is at 50 percent or more. Nothing but the stick is plugged in.
  - You approved RECORD.TXT and wrote down its SHA-256. The stick was staged from the Mac by the tool and holds no SP11\RET2.TXT.
  - A used stick needs `autorun --clear` on the staged tree and a fresh copy of it: the markers DONE1.TXT and DONE2.TXT must be gone.
    A marker left by an earlier run does harm: a DONE1.TXT makes step 1 skip, so the probe does not run and step 2 dumps RAM that nothing
    wrote (a NONE that means nothing); a RET2.TXT makes step 2 refuse and leaves the old result to be read as this one (a false WHOLE).
    The Mac tool refuses both by the times it records (STALE_STEP_MARKER, STALE_RESULT), but only after the run: start clean.

ACTION 1: start the stick and wait
  1. Power the unit off (hold the power button until the screen is dark).
  2. Start the stick as you always do: hold Volume-Down, press Power, keep Volume-Down held until the Shell countdown shows.
  3. Press no key. Step 1 starts by itself; after about 5 s the runner shows the probe's page.
  4. Wait until the page shows the line NEXT RESET AFTER 8 S.
     If the page shows a line that starts with REFUSED or VERIFY FAILED instead, or a run of lines that ends before that one: stop. Nothing
     was reset. Power off and tell the coordinator the last line you saw, in words.

ACTION 2: hold Volume-Down through the restart
  5. From the moment the page shows NEXT RESET AFTER 8 S, HOLD Volume-Down and keep holding it. The unit resets by itself
     8 s later; the screen goes dark and the unit restarts. UNKNOWN until a run: whether the firmware honours a held key at a
     restart. Keep holding Volume-Down until the Shell countdown shows, then release it and press nothing.
  6. Step 2 starts by itself. It writes SP11\RET2.TXT and shows
     AUTORUN retention-m10 STEP 2 OF 2: SP11\RET2.TXT is written, power off and take the stick to the Mac.
  If the Shell countdown does not show within 60 s of the restart (the unit started something else, or nothing): stop. Do NOT
  power-cycle to try again in this run: a power cycle loses what the probe wrote. Touch nothing and tell the coordinator what the unit did,
  in words. The power button is for the end of the run (step 7) and for danger (heat, a fan at full speed, a smell): then hold it and say so.

AFTER: take the stick to the Mac
  7. Hold the power button until the screen is dark. Unplug the stick and plug it into the Mac.
  8. On the Mac, read the result (it only reads): python3 tools/sp11_retention_check.py stick <the stick's mount point>
  9. Copy SP11/RET2.TXT off the stick and keep it, with what the tool printed. The next run starts from a fresh copy of the staged tree,
     never from a used stick.
```
<!-- /retention-actions -->

**The finding the actions are built on.** A power cycle destroys DRAM, so the second start of the stick cannot be the usual power-off and
chord: it has to be the restart the probe causes, with Volume-Down held through it. Whether the Surface firmware honours a key held at a
restart (as it does at power-on) is UNKNOWN: the restart probe showed only that a restart by itself does not come back to the stick. If it
does not, the run is void for retention (the unit started something else, which overwrites free memory) and the operator stops: this is the
case for the **unattended plan**.

**The unattended plan `retention-m10-auto`** has the same steps and no manual restart: with the stick first in the boot order the unit starts
the stick after the reset by itself. The builder and the stager **refuse** it unless `--stick-first-authorised` says that the operator decided
that; the plan then records `unattended: stick first in the boot order, authorised by the operator (this tool changes no firmware setting)` in
`PLAN.TXT`, and a `PLAN.TXT` without that line is refused. That line is a **record of the operator's decision, not an interlock**: the refusal is
real in the builder and the stager, but a `PLAN.TXT` that carries the line and parses is the same plan whoever wrote it, and nothing here reads or
touches the boot order. Changing the boot order is the operator's own act in the firmware setup, a persistent setting; this work does not assume
it and changes nothing.

**Staging, for the coordinator.** (1) Build the runner and `retprobe.bin` in the q1n1 tree. (2) `python3 tools/sp11_retention_plan.py --payload
<retprobe.bin> --path psci-reset --out build/retention` writes `RETPATH.DAT`, `PAYLOAD.MAN` (the manifest that lists `RETPROBE.BIN` and `RETPATH.DAT`
and nothing else: the runner calls every `.BIN` it lists; the q1n1 tree's `tools/sp11_payload_manifest.py` writes the same bytes for the same two
files, which a test checks) and `RECORD.TXT` (which pins the manifest's SHA-256 beside the payload's and the configuration's), and prints the step
texts, the stage command with the three hashes it made, and the actions. (3) `autorun --name retention-m10 --file ... --data PAYLOAD.MAN ... --data
RETPROBE.BIN ... --data RETPATH.DAT ... --step ... --step ...` stages the tree: a retention plan stages those three data names and no others, and
records the staging time in `PLAN.TXT` (`RET<n>.TXT` is a reserved name, so no data file can take it). (4) The operator approves the record. (5) The
run. (6) `tools/sp11_retention_check.py stick`.

## 8. What each outcome means and what follows

<!-- retention-outcomes -->
| Outcome | What it means | What follows |
| --- | --- | --- |
| The probe's page shows `REFUSED` or `VERIFY FAILED`; nothing restarts | no reset was called; the line says which check failed | fix the cause named by the line; no retry by habit |
| `RESET RETURNED`, the unit stays up | the call came back (not implemented, or denied); no second call | the path is dead for now; record the answer |
| The unit restarts and shows the Shell countdown; the tool says WHOLE | one reset path kept at least one page, once | repeat for ten consecutive trials (`series`) before anything is built on it |
| WHOLE for ten consecutive trials, pages free memory in the extraction boot | the study's M10 pass for that path | test the log region itself (section 10), then build the writer |
| PARTIAL | something survived, something did not; the tool names the bytes | other pages; check the memory map; a cache that does not reach DRAM would show as a lost page of recent writes |
| NONE, the pages free memory in the extraction boot, the probe's last line `RESET CALL` | DRAM, or a system cache in front of it, did not keep the page across that path, or firmware cleared it | try the other path; if none retains, the fallbacks of section 11 |
| NONE, the pages owned by firmware in the extraction boot | inconclusive: firmware used the page first | other pages |
| NONE, last panel line unknown | the probe may not have written anything | the operator reports the last line, in words; repeat |
| The unit does not show the Shell countdown after the restart | a held key is not honoured | the unattended plan, or the fallbacks |
| Path `NONE` (a power cycle) says WHOLE | a surprise: DRAM survives even a power cycle, or the pipeline is wrong | keep the file; repeat before believing it |
| The tool refuses the file (a named reason) | the dump is truncated or damaged | repeat the extraction; the file is not trusted |
<!-- /retention-outcomes -->

## 9. The authorisation record

The probe writes memory the runner does not own and calls the firmware to reset the machine, so it is authorised by name and SHA-256 for one
boot, in writing by the operator, as `cnthctl` and `el1trap` were. `tools/sp11_retention_plan.py` writes the record text (`RECORD.TXT`):
the payload, the configuration and the payload manifest by size and SHA-256, the reset call and its function id, the countdown and the three
pages; what the payload reads (the guarded loads, the plain ones that nothing guards, the registers, `PAR_EL1` as the one it writes), stores and
calls; what the cache maintenance and the MMU check do not show; that the Shell alone writes the stick (the DONE markers and `RET2.TXT`) and that
no code at EL2 writes it, and what an un-quiesced reset does to it (a power cut, INFERRED); what is not written (the internal disk and its
partition table, NVRAM and UEFI variables, any firmware setting, any device register, any clock or voltage); the stop rules (the power button
answers a surprise before the reset call and danger at any time, and not a dark screen after it, which a power cycle would turn into a lost run);
what approving does not cover (another path, `SYSTEM_RESET2`, a second reset call, other pages, a boot-order change, a second payload in the
boot); and the approval line. The text is wrapped by hand so that its digest is the approval: a changed byte is another record, and
`tools/test-sp11-retention.py` pins the digest of the text for fixed arguments. Generating it approves nothing.

## 10. Design: the result log for later runs (UNIMPLEMENTED)

If a path retains, every later run can end by leaving its report in RAM, resetting, and being extracted. This section specifies it; nothing in
it is built, and the wrapper and the reporter (other owners' files) are not edited.

* **Region.** 64 KiB, 4 KiB aligned, chosen by the probe's outcome: the pages that were whole for ten trials, and a second M10 run (M10b) with
  three pages at the start, the middle and the end of that 64 KiB, because four kibibytes held says less than sixty-four. It must be conventional
  memory in the map of the boot that writes it and of the boot that extracts it, and no part of the wrapper's or the guest's placement.
* **Records.** One header page (the layout of the canary header with a log magic: layout version, boot counter, region address, ring size,
  head, the CRC of the header) and a ring of fixed 64-byte records in the other fifteen pages: a sequence number (u32), the CRC-32 of the record
  (u32), a counter-clock timestamp (u64), a source id and a length (u16 each) and 44 bytes of text or a typed event. A torn write is a record
  whose CRC fails; the ring holds 960 records and overwrites the oldest. A single slot would lose the lines before a hang; a ring keeps them.
* **Writer.** Single producer, bounded work, no lock, no allocation: store the record, `DC CVAC` its line and `DSB SY`, then advance the head
  the same way (AGENTS.md sections 2 and 3). The interface the wrapper and the reporter would call: `open(region, size)` reads the previous
  header with guarded loads, validates it, takes the boot counter and starts a fresh header when it is not valid; `put(source, text, length)`
  appends one record; `close()` is the last record. Nothing blocks in a trap handler, a timer handler or a presentation path.
* **After a reset.** The extract step would take a region and a size (`RETENTION-LOG <address> <bytes>`, a second step kind with the same
  guards: the dump first, the end line, the marker last, a result never overwritten); the file is `LOG<n>.TXT`. The Mac-side reader would
  print the records in sequence with their times, name the gaps and torn records, and refuse a damaged file as `check` does.
* **If the writer cannot reset** (a hang), the next start of the stick (power cycle) loses the region: the log covers runs that end in a reset.

## 11. If retention fails: the fallbacks

If no path keeps a page, the route above is closed, and the options are these, none of them built:

* **Stick first in the boot order.** It does not create retention. It removes the operator's second action (the unattended plan) and is needed
  anyway if a held key is not honoured. Cost: the operator's decision to change a persistent firmware setting; risk: the unit starts the stick
  whenever it is plugged in until the order is put back.
* **The UEFI network route.** Boot services can send a report over a network adapter the firmware has a driver for, before the bounce. It
  covers only what is known before the bounce (nothing of the EL2 stages), needs an adapter the Surface firmware can use (UNKNOWN), a receiver
  on the Mac, and a few hundred lines. Risk: unknown drivers; no EL2 output at all.
* **An automatic camera capture on the Mac.** A fixed camera on the panel, a script that grabs a frame per page and decodes the text (the glyph
  set is small and known), no photograph by the operator. No device risk; cost: a mount, the capture script, a decoder to validate on real
  frames (none exist here); risk: shows only what is on the screen when it is grabbed, and nothing after a hang.
* **A write to the stick from EL2** is the persistent log of the study (section 8 there and the stick log): it needs authorisation points A1
  and A2, which are not granted.

## 12. Tests, and what they do not show

| Command | What it holds |
| --- | --- |
| `python3 tools/test-sp11-retention.py` | the page and configuration formats, the rule against an independent oracle and against the payload's own reader (SKIP without `Q1N1_SP11_TREE`), the unit pages against the six local maps (every row of them, the spaced names included), the plan builder and its manifest (against the q1n1 manifest tool, SKIP without `Q1N1_SP11_TREE`), the actions and the record |
| `python3 tools/test-sp11-retention-check.py` | the verdict tool on files written by an independent writer of the Shell's text: every named refusal, the verdicts, the memory map, the clock, `series` (a dump counted ten times, the same write twice, no map, mixed boots), the stale marker and result refusals of `stick`, the command line |
| `python3 tools/test-sp11-retention-autorun.py` | the plan rules, the script and its line checker against a table of slipped scripts (a command before the dump, the marker first, no guard, a UCS-2 redirect, a repeated page, a clock line out of place ...), `PLAN.TXT` and its staging time, the data names a retention plan accepts, staged trees, old plans byte for byte |
| `python3 tools/test-sp11-retention-mutants.py` | 145 seeded bugs in the modules above, each of which the suites must fail on, and a control run |
| `python3 tools/test-sp11-retention-qemu.py` | the whole route under QEMU (SKIP without a q1n1 tree, QEMU, EDK2 firmware and the pinned Shell): the real runner and probe, a PSCI reset that keeps RAM, the Shell's extract step with its date and time, and the verdict tool; pages above 4 GiB; a stick whose probe never ran; a finished stick; a result never overwritten |
| `python3 tools/test-sp11-retention-doc.py` | this document against the tools |

QEMU keeps the guest's RAM across a reset, its PSCI answers `SYSTEM_RESET` and not `SYSTEM_RESET2`, and its Shell is the pinned binary: the
test shows that the route works when RAM is retained, and that the Shell's text is what the tool reads. It shows nothing about the unit's DRAM,
its cache hierarchy, its firmware's reset or its Shell.

## 13. Open risks

* Every UNKNOWN of section 1; the first run decides them, one at a time.
* A `dmem` of a page the unit's firmware left unmapped or in a bad state could abort the Shell (UNKNOWN); the answer would be a minimal EFI
  application, built and hashed like the other stick files.
* The Shell's own allocations before the dump could overwrite a free page; the memory map after the dump shows ownership then, not earlier.
* The probe's panel text is the only evidence of whether the probe wrote anything: a file that says NONE everywhere cannot tell that apart.
* The unit's Shell prints `dmem` lines as the pinned binary does under QEMU (low 32 bits of the address): INFERRED, same binary.
* The pages are given twice, in `RETPATH.DAT` for the probe and in the extract step for the Shell. The builder writes both from one list, and the
  tool refuses a file whose pages differ from the configuration's (`PAGES_DIFFER_FROM_CONFIG`); the stager does not compare them (it never reads a
  data file), so a hand-staged stick could dump other pages than the probe wrote.
* The stale-result checks compare the staging computer's clock with the unit's: INFERRED to agree (one observation of the unit's clock, local time,
  2026-11-15), and a unit whose clock is wrong makes `stick` refuse a good result (the file is then judged alone with `check`). The Shell's `date`
  and `time` print as under QEMU on the unit: INFERRED, same binary, and the zone text after the time is read as printed and not interpreted.
* The first run is one trial: a measurement, not proof of retention. The study's pass rule needs ten trials, and `series` refuses ten copies of one.
