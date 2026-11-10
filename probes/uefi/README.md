<!-- SPDX-License-Identifier: MIT -->
# SP11 EL2 state probe (UEFI application)

A passive AArch64 UEFI application for gate X1 of the Surface Pro 11 (SP11,
Snapdragon X Elite X1E80100) port, see [SURFACE-PRO-11.md](../../docs/SURFACE-PRO-11.md).
The operator starts it after slbounce has switched the machine to EL2. It
copies the firmware tables (ACPI, SMBIOS, UEFI memory map) to the USB stick it
was started from, calls ExitBootServices itself, and then shows the EL2 hand-off
state on the GOP framebuffer as photographable pages. It reads; it never
programs anything.

**Hardware status: NOT RUN.** No image here has been started on a Surface Pro
11 or on any real machine. Every statement about the device below is INFERRED
from the plan or marked UNKNOWN. QEMU with EDK2 is only a model: a PASS there
says the logic, the dump format and the drawing work against a standard
firmware, not that the SP11's firmware, GOP, PSCI conduit or EL2 state behave
the same way.

Evidence labels used in this file: VERIFIED (reproduced by a test or tool
here), OBSERVED (seen in one run or one build), INFERRED (follows from
documents or code, not seen), EXPERIMENTAL (written, only emulator-tested),
UNIMPLEMENTED.

## Quick start

```sh
python3 tools/build-uefi-probe.py                       # shipped images -> build/uefi/
python3 tools/build-uefi-probe.py --variant all --verify-reproducible
python3 -B tools/test-uefi-probe.py                     # every host test, one command
python3 probes/uefi/verify-lines.py transcription.txt   # check rows typed from a photograph
```

The build needs clang (AArch64 COFF target) and `lld-link` and nothing else:
no EDK2 tree and no firmware file. Without them it refuses (exit status 3,
`REFUSED: ...`) and writes nothing. The QEMU tests need `qemu-system-aarch64`
and an EDK2 AArch64 code/vars pair; without them those tests print `SKIP:` lines
and the run still exits 0.

## Images

| Variant | Output | Defines | Ships |
| --- | --- | --- | --- |
| `default` | `build/uefi/sp11-el2-probe.efi` | none | yes |
| `psci` | `build/uefi/sp11-el2-probe-psci.efi` | `PROBE_PSCI=1` | yes, opt-in |
| `fast` | `build/uefi/test/sp11-el2-probe-fast.efi` | `PROBE_FAST_TEST=1` | test only |
| `fast-psci` | `build/uefi/test/sp11-el2-probe-fast-psci.efi` | `PROBE_FAST_TEST=1 PROBE_PSCI=1` | test only |
| `fast-noreset` | `build/uefi/test/sp11-el2-probe-fast-noreset.efi` | `PROBE_FAST_TEST=1 PROBE_NO_RESET=1` | test only |

* `fast*` shorten the timers (no pre-ExitBootServices stall, 2 s page dwell,
  16 s countdown) so an emulator run takes seconds. Never use them on hardware.
* `fast-noreset` holds the final screen instead of calling ResetSystem, which
  lets the emulator tests capture a long, stable recording.
* Images are reproducible: building twice gives identical bytes
  (`--verify-reproducible` checks it, `/Brepro` replaces the PE timestamp). The
  12-hex-digit build id printed on every page is a hash of the variant, its
  defines, the compiler flags and the probe sources, so it changes exactly when
  the image should. The code holds the id only as instruction immediates, so
  the image also carries it as the text `SP11PROBE-BUILD-ID=<id>` in read-only
  data (`g_build_tag` in `probe_main.c`): `strings` or `grep -a` on the file
  shows which build it is, and the manifest next to it names the same id.
  Binaries are scratch outputs; never commit them.

## What the probe does

Before ExitBootServices (boot services available, progress lines on the text
console, which the emulator tests read from the serial port):

1. Records firmware facts (UEFI revision, vendor), the configuration-table GUIDs,
   a validated copy of the memory map, and the GOP mode. Only the GOP installed
   on the console output handle is ever drawn into, and only when probe_fb_init
   accepts the mode and the memory map describes the whole framebuffer range
   (reserved, boot-services data, runtime data or MMIO, with no overlapping
   descriptor of another type). Anything else is refused, the `GOP` console line
   says `STATE=NONE WHY=<reason>`, and ExitBootServices is skipped.
2. Reads ACPI (RSDP, XSDT or RSDT, every table) and SMBIOS only through the memory
   map: a physical range is read only if it lies entirely inside loader, boot,
   runtime, ACPI-reclaim or ACPI-NVS descriptors and no descriptor of any other
   type overlaps it (an allowed descriptor does not launder a reserved or MMIO
   one). Anything else is refused,
   because a stray read of a firewalled range is an external abort, not a
   failed read.
3. Dumps the tables to the USB stick (see "Dump format"). The dump is attempted
   only if the volume the image was loaded from has a USB node in its device
   path and no other transport node (SATA, NVMe, SCSI, UFS, SD, eMMC, ATAPI);
   a malformed or ambiguous path is a refusal. No other volume or block device
   is ever opened, and no existing file is overwritten.
4. Calls ExitBootServices (up to 8 attempts, retried only for a stale map key).
   If there is no usable GOP, no layout that fits the report or no valid memory
   map, ExitBootServices is skipped, the dump is kept, and the application
   returns an error status after a 10 s message: after the call the screen is
   the only output, so it is not made without one.

After ExitBootServices nothing but the screen is used and no UEFI service is
called except ResetSystem. The registers are read in tiers and the report is
redrawn after each tier. A tier contains only registers that every Armv8.0 EL2
may read; nothing in the passive image can recover from a fault (it never
installs a vector), so the screen is not treated as a recovery path and the ID
registers of newer architecture revisions are not read here at all (below).

| Tier | Registers read |
| --- | --- |
| 0 | `CurrentEL` only. Anything but EL2 ends the probe (see below) |
| 1 | `ID_AA64MMFR1_EL1`, `ID_AA64PFR0_EL1`, `HCR_EL2`, `SCTLR_EL2`, `TCR_EL2`, `TTBR0_EL2`, `MAIR_EL2`, `VBAR_EL2`, `CPTR_EL2`, `MDCR_EL2`, `CNTHCTL_EL2`, `CNTVOFF_EL2`, `SPSR_EL2`, `ELR_EL2`, `MPIDR_EL1`, `CTR_EL0`, `CNTFRQ_EL0`; `TTBR1_EL2` only if `MMFR1.VH` is non-zero, `ICC_SRE_EL2` only if `PFR0.GIC` is non-zero |
| 2a | `MIDR_EL1`, `REVIDR_EL1`, `ID_AA64PFR1_EL1`, `ID_AA64MMFR0_EL1`, `ID_AA64MMFR2_EL1`, `ID_AA64ISAR0_EL1`, `ID_AA64ISAR1_EL1`, `ID_AA64DFR0_EL1`, `DCZID_EL0`; `ID_AA64ZFR0_EL1` only if SVE is present, `ID_AA64SMFR0_EL1` only if SME is present |

`ID_AA64MMFR3_EL1`, `ID_AA64MMFR4_EL1`, `ID_AA64ISAR2_EL1` and `ID_AA64DFR1_EL1`
(a former tier 2b) are no longer read by this image: they were allocated by newer
architecture revisions than the others, a plain `mrs` of an encoding the core
does not accept is an exception at EL2, and this image has no vector to take it.
The report says `NOT READ HERE: MMFR3 MMFR4 ISAR2 DFR1`, and so loses the
`NV_FRAC`, `E2H0`, `APA3`/`GPA3` and `PAUTH ALGO` lines that depended on them. The
guarded X2 tests image (`probes/uefi/tests/`, `docs/SP11-X2-TESTS.md`) reads
MMFR3, MMFR4 and ISAR2 behind an exception guard (test T01); `ID_AA64DFR1_EL1`
is read nowhere. The instruction audit lists the registers the image may read,
so an `mrs` of one of the four fails `suite_image`.

A register that is gated off is shown as `NAME=SKIPPED VH=0` (or `GIC=0`,
`SVE=0`, `SME=0`). The gates are deliberately conservative: an EL2 read of an
unallocated ID encoding faulted on the A16's Oryon cores (OBSERVED, see the
note in `probes/idregs.h`), so a register whose presence depends on a feature
is read only when the feature field says it exists, and a read of
`ICC_SRE_EL2` without a GIC system-register interface may trap to EL3
(INFERRED).

If `CurrentEL` is not EL2 the probe reads nothing else and does not reset: it
shows the pre-ExitBootServices facts with the footer `HALTED - NO RESET` and
cycles through the pages forever, so that "slbounce did not land in EL2" is
itself a photographable result.

The shipped images then rotate through the pages (12 s each) with a 60 s
countdown and call ResetSystem. The countdown covers five pages exactly, so a
layout of six to eight pages would show only the first five before the reset
(INFERRED from the constants). That needs a framebuffer smaller than 800x600
(the QEMU size, five pages, OBSERVED); at the expected 2880x1920 the layout
function gives one page (VERIFIED on the host for a report of 69 to 85 rows),
and at 640x480 there is no layout at all (VERIFIED on the host), so
ExitBootServices is skipped.

What the probe never does (VERIFIED on the built images by `suite_image`
through the PE and instruction audits, OBSERVED on the audited build):
no `msr`, no `svc`, no `eret`, no TLB or address-translation maintenance,
no floating-point/SIMD/SVE/SME instruction (an FP trap would hide the report),
no import, no writable-and-executable section; `smc` exists only in the PSCI
image and only with immediate 0, and no image contains an `hvc`.

The audit excuses one thing that is not an instruction. lld-link starts the PSCI
object's `.text` on the 64-byte boundary its aligned stub needs and fills the gap
with zero bytes; `llvm-objdump` elides a gap of two or more words and prints a
one-word gap as `udf #0x0` (OBSERVED with the pinned toolchain). That would fail
the audit on about one code change in sixteen, so `peinfo.alignment_fill`
accepts a run of at most 15 such words, only directly after a function end and
only when it ends on the matching boundary. Every other `udf` still fails the
audit. `suite_image` links a fixture with exactly such a gap, so the rule stays
tied to the real linker and disassembler (VERIFIED on the host).

Two things at EL2 are not register reads of the tiers above and are listed here
so that nothing is hidden. The only cache maintenance is `DC CIVAC` over the
framebuffer lines the probe has just drawn, followed by `DSB SY`
(`cpu_aarch64.c`); its first use reads `CTR_EL0` for the line size, and it is
never installed on the not-at-EL2 path. The 12 s page dwell and the countdown
read `CNTFRQ_EL0` and the physical counter `CNTPCT_EL0` after the tiers have
finished; a zero frequency falls back to the uncalibrated spin loop. Neither is
reached unless `CurrentEL` is EL2.

### PSCI variant (EXPERIMENTAL)

`sp11-el2-probe-psci.efi` additionally picks a secondary CPU from the MADT,
issues one `PSCI_VERSION` and one `CPU_ON`, and shows `CPU_ON RET` plus
`SECONDARY ARRIVED`/`EL`/`MPIDR`. It is a separate binary because `CPU_ON` changes
machine state and `SMC` goes to whatever owns EL3. The conduit comes
from the FADT: none if the revision is below 5, the table is shorter than 131
bytes, `ARM_BOOT_ARCH` bit 0 is clear or any of bits 2-15 is set; otherwise
`HVC` if bit 1 is set and `SMC` if not. Only `SMC` is ever issued: an `HVC` at
EL2 is an exception taken by this exception level's own vector, which no probe
image installs, so a declared HVC conduit gives `PSCI RUN=HVC REFUSED` and no
call at all. Whether the conduit matches the SP11 is
UNKNOWN; under QEMU only the `HVC`-at-EL1 path was exercised, and
`-cpu max` is not an Oryon. Treat the result as a lead, not a measurement.

## Operator procedure

1. Build (`python3 tools/build-uefi-probe.py`) and copy the default image onto
   a USB stick formatted FAT, in whatever path the X1 procedure in
   [SURFACE-PRO-11.md](../../docs/SURFACE-PRO-11.md) uses to start EFI
   applications after the EL2 switch. The image must be started *from that
   stick*: started from anywhere else, the dump is refused (and the screen part
   still works).
2. Start it. The screen shows the pages in turn. Photograph every page,
   including the header (build id, `CURRENTEL=`, `PAGE k/N`) and the footer.
3. Type each row into a text file as shown, including the trailing `|XX`, and run
   `python3 probes/uefi/verify-lines.py transcription.txt`. A `BAD` row is a
   misread character or a wrong CRC: re-read it from the photograph. Only `OK`
   rows are evidence.
4. Read the dump from the stick (`SP11DUMP/RUNnn/`). It holds raw firmware
   tables, including SMBIOS strings that can carry serial numbers and UUIDs:
   keep it out of version control and out of reports.

## Screen format

Every row is `<payload, at most 38 upper-case columns> |<CRC-8>`, 42 columns in
all. The CRC (polynomial 0x07, initial value 0, no reflection) is taken over the
payload as shown and printed as two upper-case hex digits. Examples:

```
SP11 EL2 PROBE B=0123456789AB          |25
CURRENTEL=2 PAGE 1/5                   |81
HCR_EL2=0123456789ABCDEF               |50
TTBR1_EL2=SKIPPED VH=0                 |50
ALL STAGES DONE                        |0C
```

(The build id and the register value are made up; the rows only show the format.)

* Row 0 of every page is `SP11 EL2 PROBE[+PSCI] B=<build id>`, row 1 is
  `CURRENTEL=<n> PAGE k/N`, the last row is the footer, and the rows between are
  the report.
* The layout picks the largest integer font scale from 3 to 12 at which the whole
  report fits on one page in 1 to 3 columns; if none does, scale 3 and up to 8
  pages. The font is a 6x8 cell with a 5x7 glyph; upper-case only.
* Footers, in order of appearance: `STAGE 0 DONE - READING 1 NOW`,
  `STAGE 1 DONE - READING 2A NOW`,
  `ALL STAGES DONE`, `PSCI CALL IN PROGRESS` (PSCI image), `RESET IN <n> S`,
  `RESETTING NOW`, `RESETSYSTEM DID NOT RESET`; `HALTED - NO RESET` for a
  not-EL2 start and `RESET DISABLED - HOLDING` for `fast-noreset`.
* Raw register values are printed in hex and are authoritative. The decoded
  lines (MMU and HCR bits, address and granule sizes, VH, NV, pointer
  authentication and so on) use the bit positions listed in
  `host/checks.py` (`DECODED_FIELDS`), which the tests compare with the C table.

## Dump format

`\SP11DUMP\RUNnn\` on the USB stick, `nn` the first free number from 01 to 99
(nothing is overwritten). A stick that already holds earlier runs gets the first
number not in use, RUN99 is the last slot, and a stick that holds 01 to 99 gets
no dump and `DUMP STATE=NO FREE RUN SLOT` (`suite_rotation` boots all three
cases and checks that every earlier file is byte-identical afterwards).

Every file is flushed and closed, then the run directory, `SP11DUMP` and the
volume root are flushed and closed, before ExitBootServices (that flush-on-close
does what it should on a real FAT driver is INFERRED; under QEMU the host reads
back the files).

| File | Content |
| --- | --- |
| `RSDP.bin` | the RSDP as found, when it validated |
| `<SIG>.bin` | one validated ACPI table per four-character signature, for example `FACP.bin`, `APIC.bin`, `DSDT.bin` |
| `<SIG>_<k>.bin` | the further copies of a signature seen more than once |
| `smbios-ep.raw`, `smbios-table.raw` | the SMBIOS entry point and structure table |
| `memmap.raw` | the UEFI memory map exactly as the firmware returned it |
| `memmap.txt` | one fixed-width row per descriptor, `# SP11PROBE memmap v1` |
| `tables.txt` | one row per ACPI table found with its status, `# SP11PROBE tables v1` |
| `summary.txt` | `key=value` facts: build, variant, firmware, GOP, layout, FADT and PSCI conduit, SMBIOS version, RAM bounds, dump totals |

* A table that failed validation or had a bad checksum is listed in
  `tables.txt` but its bytes are not copied. Whether such tables should be kept
  under a separate name is an open question.
* `summary.txt` and `tables.txt` carry no SMBIOS strings, serial numbers or UUIDs.
  The `smbios-*.raw` files do.
* Caps: 200 files, 16 MiB per file, 32 MiB overall. Hitting a cap skips that one
  file and makes the dump `PARTIAL`; it is never silent, and later files that
  still fit are written (a skipped file reserves nothing). The caps are
  compile-time constants (`PROBE_DUMP_MAX_*` in `dump.h`) that no firmware in
  the emulator can reach, so `test_dump.c` checks them natively at their exact
  boundaries against an in-memory model of the file protocol (VERIFIED on the
  host; a real FAT driver's behaviour on a full volume is not exercised). The
  text files share the 32 MiB budget with the tables: a scan that used all of it
  would lose `memmap.txt`, `tables.txt` and `summary.txt`. Real ACPI sets are a
  few MiB, so this is not expected; the test covers the near-full case.
* Existing files are never opened for writing. The first UEFI create, write or
  flush error ends the dump: nothing further is attempted, a file whose write or
  flush failed stays on the stick uncounted, and the state is `PARTIAL` if any
  file was completed and `ERROR` if none was.
* The `DUMP` progress line reports the state: `OK`, `PARTIAL`, `REFUSED NO VOLUME`,
  `REFUSED NOT USB`, `REFUSED AMBIGUOUS`, `REFUSED MALFORMED PATH`,
  `NO FREE RUN SLOT` or `ERROR`.
* Intended consumers: the offline ACPI checker (`tools/check-hamoa-acpi.py`, a
  separate task) reads the `<SIG>.bin` files; a stage-2 `--map` option may read
  `memmap.txt` and `memmap.raw`. `host/checks.py` (`dump_problems`) is the
  verifier this project's own tests apply to every dump.

## Progress lines and serial markers

The text console gets one `KEY field=value ...` line per phase, in this order:
`BEGIN`, `PREEBS`, `FW`, `GOP`, `LAYOUT`, `CFG`, `ACPI`, `SMBIOS`, `MEMMAP`,
`DUMP`, then (shipped images only) `EBS IN_SECONDS=3`, and `EBS STATE=CALLING`.
`host/qemu_probe.py` parses them (`parse_markers`); on the SP11 they may go
nowhere visible, which is why the screen and the stick carry the same facts.

## Known unknowns (not guessed)

| Question | Why it matters | Status |
| --- | --- | --- |
| Does ExitBootServices return normally under slbounce? | the screen part depends on it | INFERRED yes; the app prints `EBS STATE=FAILED` and returns otherwise |
| What does the firmware leave in `CPTR_EL2`, `MDCR_EL2`, `CNTHCTL_EL2`? | FP/SIMD, debug and timer traps | the probe avoids FP/SIMD and never writes; the values are what it reports |
| Does the framebuffer stay visible after the EL2 switch and after ExitBootServices? | the register tiers are shown only there | INFERRED yes; the dump on the stick does not depend on it |
| Real panel resolution | page count | INFERRED 2880x1920; read back from `summary.txt` |
| Real PSCI conduit | the PSCI image | UNKNOWN; the FADT value is in `summary.txt` |
| ResetSystem after slbounce (R18) | the shipped images end with it | UNKNOWN; if it does not return, the footer says `RESETSYSTEM DID NOT RESET` |
| Pace of the not-EL2 page rotation | a spin loop, not calibrated | UNKNOWN on hardware (QEMU OBSERVED 0.9 to 2 s per page) |

## Tests

One command from the repository root: `python3 -B tools/test-uefi-probe.py`.
It exits 0 only if nothing failed, prints one PASS, SKIP, FAIL or ERROR line per
test and a `Ran N tests ...` summary, and writes only below `build/`. A class or
a single test can be named: `python3 -B tools/test-uefi-probe.py NativeTests`
or `... HygieneTests.test_no_machine_specific_text`.

| Suite module | Checks |
| --- | --- |
| `suite_native` | the C logic tests `tools/test-uefi-probe-logic.c` plus `probes/uefi/test/test_*.c` (`test_dump.c` covers the dump's caps, never-overwrite rule, error handling and result states against an in-memory model of the file protocol), with and without sanitizers, and the font and page renderers |
| `suite_build` | reproducible builds, build ids, the exact flag set, refusal without a toolchain |
| `suite_image` | PE structure, the instruction audit (with its linker-fill rule) and the embedded `SP11PROBE-BUILD-ID=<id>` text of the built images |
| `suite_hosttools` | FAT writer/reader, the line CRC, the operator CLI, this README, repository hygiene |
| `suite_dump` | the independent dump verifier against synthetic ACPI, SMBIOS and memory-map data, including a brute-force bit-flip sweep |
| `suite_qemu` | the real images under QEMU with EDK2: positive, no-USB, EL1, PSCI, no-GOP, no-reset and shipped-timing scenarios, each with its own timeout |
| `suite_rotation` | three more QEMU scenarios that start from a stick that already holds dump runs: the first free `RUNnn` is used, `RUN99` is used, and a full stick gets no dump while every earlier file stays byte-identical |

Environment variables:

| Variable | Effect |
| --- | --- |
| `UEFI_PROBE_SKIP_QEMU=1` | skip the QEMU scenarios (SKIP lines, exit 0) |
| `UEFI_PROBE_QEMU_WORKERS=N` | guests booted in parallel, default 3 |
| `UEFI_PROBE_KEEP=1` | keep `build/uefi/test-work` for inspection |
| `UEFI_PROBE_QEMU`, `UEFI_PROBE_EDK2_CODE`, `UEFI_PROBE_EDK2_VARS` | emulator binary and firmware files |

QEMU scenarios and what they can and cannot show (OBSERVED unless stated):

* They check the probe against firmware it was not written for: standard EDK2
  tables, a RAM-backed framebuffer, a virtual USB stick (the dump is read back
  from the stick image), and a screen decoded from the emulator's display.
* `-cpu max` has no Oryon ID-register values, no firmware firewall and no slbounce:
  register *values* are a model. The secondary-CPU result of the PSCI scenario is
  EXPERIMENTAL.
* QEMU's FADT has no FACS pointer, so the FACS checks are exercised only by the
  host tests on synthetic data.
* Only the default shipped image runs with its real timers (scenario `shipped`);
  the `psci` image is covered through its `fast-psci` twin.

## Extending the harness

`probes/uefi/host/` holds reusable, dependency-free helpers; `probes/uefi/test/`
holds the suites. A new suite is a module `suite_<area>.py` there.

| Module | Role |
| --- | --- |
| `host/checks.py` | register-line expectations, ACPI/SMBIOS/memory-map/dump verifiers returning lists of problems; the layer to reuse for any new functional test |
| `host/fatimg.py` | FAT16 image writer (the emulator's stick) and reader (what the firmware wrote) |
| `host/lines.py` | the row format and CRC-8 shared with the C code |
| `host/screen.py` | decodes a captured frame into headers, body rows and footer |
| `host/peinfo.py` | PE header and instruction audits of an image |
| `host/qemu_probe.py` | boots one image under QEMU and returns markers, stick contents and frames (`RunConfig`, `run`) |
| `test/uefi_testkit.py` | shared test machinery: `Base.check`, `images()`, `qemu_assets()`, `run_cmd`, `Reporter`, the work directory |
| `test/synth_dump.py` | builds synthetic dumps (tables, FADT, RSDP, SMBIOS, memory map) and mutates them |

* Derive test classes from `kit.Base` (`import uefi_testkit as kit`), assert with
  `self.check(problems)` so every problem is listed, and use `kit.images()` for
  the built variants. A missing tool must raise `unittest.SkipTest`.
* Register the module in `SUITES` in `tools/test-uefi-probe.py`; `DocsTests`
  fails if a `suite_*.py` file is not registered there or not named in this file.
* A suite may define `start_background(tests) -> finish` to start slow work early
  (`suite_qemu` boots its guests that way); `finish` is called after the run.
* `suite_qemu.scenario(name)` returns the recorded result of one emulator
  scenario (booted once, shared by every test that needs it); `SCENARIOS` lists
  the names. Add a scenario there rather than booting a guest in a test. A
  scenario can start from files already on the stick (`Scenario.seed`);
  `suite_rotation` registers its scenarios in `SCENARIOS` and `ORDER` when it is
  imported.
* Keep every authored file under 1,500 lines and 50 KiB: `HygieneTests` (in
  `suite_hosttools`) enforces it.

## Provenance and licensing

All files here are MIT. The 6x8 font is hand-authored pixel art for this
project (conventional dot-matrix letter forms, as any 5x7 font has); no data
was copied from any font file. The ID-register field positions were checked
against the Linux register description (v6.12), used as facts only; no source
text was taken. The firmware tables
a run produces belong to the machine's vendor and are never part of this tree.
