<!-- SPDX-License-Identifier: MIT -->
# SP11 stick wrapper and reporter: SNAPMON.BIN and SNAPREP.BIN (work item 4a)

**Status: EXPERIMENTAL. The dry run ran once on a Surface Pro 11 and passed on the first attempt (OBSERVED, 2026-11-16, one unit, one boot; section 12); the monitor entry, the GIC, the timers and the guest are hardware NOT RUN, and so are the wrapper's refusal paths on the unit.** The other test results below are host results (a
fake machine, section 9) or, where section 10 says so, QEMU results. Labels as in [SP11-STICK-BUNDLE.md](SP11-STICK-BUNDLE.md) section 1:
VERIFIED (a test in this tree shows it), OBSERVED (seen once, on the host that built it, or on the unit where a sentence says so), INFERRED, EXPERIMENTAL, UNIMPLEMENTED (not in this
build), UNKNOWN. Test outcomes are not recorded here; they belong to the run that made them.

## 1. What this is

Two freestanding position-independent payloads for q1n1's stick payload runner (`q1n1:docs/SP11-PAYLOAD.md`): files 0 and 1 of the bundle of
[SP11-STICK-BUNDLE.md](SP11-STICK-BUNDLE.md), whose contract (`probes/sp11-stick-bundle.h`, `tools/sp11_stick_bundle.py`) this work does not change.

- **SNAPMON.BIN**, the wrapper and the loader. It checks the five files in the order of that note's section 8, checks the pinned placement
  against the final memory map and against EL2's own translation, copies the guest chunks into the placement with cache maintenance, hashes the
  copy again from DRAM, writes the report into REPORT.DAT and returns. **There is no monitor inside it and it never enters one**: no timer is
  armed, no GIC or MMIO register is touched, no system register is written (section 4). The monitor that the bundle note's section 2 also puts in
  this file is UNIMPLEMENTED (work items 4b, 8 and 11); the stages PREPARED, MONITOR_ENTERED and MONITOR_RETURNED are never written and the
  report fields that belong to them stay zero.
- **SNAPREP.BIN**, the reporter. It reads REPORT.DAT and says on the panel what happened. It stores only into its own stack and executes no
  system register, cache, translation or barrier instruction (VERIFIED on the binary, section 9).

Both are compiled only under `-DSNAP_SP11_STICK=1`: off by default, an `#error` without `-DSNAP_PLATFORM_SP11=1`, no value but 0 and 1
(VERIFIED by the gate tests). With the gate at 0 every source compiles to an object with no symbol. Nothing in the monitor build includes these
files or names the gate (VERIFIED by a scan of `compat/`, `shadow/`, `probes/` and the cycle scripts), so the A16 monitor is unchanged with the
gate off; `tools/monitor_baseline.py` is the byte check.

| File | Role |
| --- | --- |
| `probes/sp11-stick-hw.h` | the gate and the one hardware layer (section 4); a host harness supplies the same functions over a fake machine |
| `probes/sp11-stick-common.h` | the five files from the preload table; text helpers |
| `probes/sp11-stick-wrapper.h` | the context in the stack, the hash policy, the GIC macros, the internal interface |
| `probes/sp11-stick-wrapper.c` | `payload_entry` and the stages |
| `probes/sp11-stick-load.c` | probes, copy, verification |
| `probes/sp11-stick-report.c` | the report head, the panel lines, the refusal path |
| `probes/sp11-stick-reporter.c` | SNAPREP.BIN |
| `tools/sp11_stick_wrapper_build.py` | the build recipe tool |
| `tools/test-sp11-stick-wrapper.c` | the host harness: a fake machine and runner around the real sources |
| `tools/test-sp11-stick-wrapper.py`, `tools/test-sp11-stick-wrapper-refusals.py` | host-harness tests: the dry run, the copy, the reporter, the seeded-bad wrappers; and every damaged bundle (section 9) |
| `tools/test-sp11-stick-wrapper-build.py`, `tools/test-sp11-stick-wrapper-qemu.py` | the build, the binaries, the gate, this note; QEMU end to end (sections 9 and 10) |
| `tools/sp11_stick_wrapper_testlib.py`, `tools/sp11_stick_wrapper_checks.py` | shared test support: the fixtures and the harness driver; the module fixtures and the checks a run is held to |

## 2. Build

```
export Q1N1_SP11_TREE=<q1n1 checkout with platform/uefi/Makefile and payloads/>
python3 tools/sp11_stick_wrapper_build.py --out build/sp11-wrapper [--profile config/monitor-profile-sp11-m1.json] [--force]
```

The recipe is q1n1's, read from its Makefile and not copied: `PAYLOAD_CFLAGS` (without `-MMD -MP`) and `PAYLOAD_LDFLAGS` (`--emit-relocs` among
them), `payloads/start.S` first, `payloads/payload.ld`, `llvm-objcopy -O binary`, then q1n1's `q1n1:tools/sp11_payload_elfcheck.py` on each ELF and an
empty `llvm-nm -u`. Added: `-DSNAP_PLATFORM_SP11=1 -DSNAP_SP11_STICK=1`, the include directories and, for SNAPMON only, the five GIC macros
(`SNAP_GICD_BASE`, `SNAP_GICR_FIRST`, `SNAP_GICR_LIMIT`, `SNAP_GICR_STRIDE`, `SNAP_TIMER_INTID`) that PIN.DAT pins. This build has no monitor, so it
takes them from the `defines` of the profile of the monitor build it stands for, each exactly once and a plain number; there is no default value
in the source, because one would make the comparison with the pin true by construction. SNAPMON links q1n1's `platform/uefi/sha256.c`, the
freestanding SHA-256 the runner already uses; no hash code is carried here.

- A refusal prints the reason on standard error, `REFUSED=<NAME>` as the last line of standard output, and exits 1: `Q1N1_TREE_ABSENT` (a set
  `Q1N1_SP11_TREE` is never replaced by another tree), `PROFILE_BAD`, `RECIPE_CHANGED` (a variable the tool cannot read without expanding it, or
  a flag it relies on gone), `TOOL_MISSING`, `OUT_DIR_REFUSED` (outside `build/`, a `..`, a link, a volume), `OUT_NOT_EMPTY`, `BUILD_FAILED`,
  `ELF_REFUSED`, `UNDEFINED_SYMBOL`, `REPORTER_TOO_BIG` (64 KiB or more). A build ends with `HARDWARE=NOT RUN`, exit 0.
- Same inputs, same bytes in any directory: no debug information, no `__FILE__`. `build-report.json` records the sizes, SHA-256, the flags and the
  digest of every q1n1 file used. **A change of the recipe changes the bytes**, and the authorisation (PIN.DAT) names the digests of the build
  it was given: after a q1n1 update, rebuild and re-authorise.
- OBSERVED on one host (clang 23.1.5): SNAPMON.BIN about 35 KB, SNAPREP.BIN about 21 KB; the deepest stack use by the call graph is 2048 bytes
  against the 48 KiB the runner leaves a payload.

## 3. The wrapper

**Order.** One stage between steps, the head of REPORT.DAT stored whole after each (37 words, then a clean of its lines and a DSB), so that a
hang is named by the last stage in RAM and by the last line on the panel. A data abort in a payload lands in q1n1's fault page (ESR, ELR and FAR, then a
halt), not in the wrapper's report, which is why every probe comes before the first access it guards.

| Step | Reads | Stores | Stage when it passes |
| --- | --- | --- | --- |
| entry | CurrentEL first (the EL2 registers are UNDEFINED below EL2), the runner's arguments (magic, version, size, `puts`, `hex`, `load32`) | none | |
| slots | the preload table: five files, this payload's own slot must be 0; AT S1E2W over REPORT.DAT | page 0 of REPORT.DAT: zero tail first, then the head | ENTERED, ARGS_OK |
| files | all five slots by `sp11sb_files_check` | head | FILES_OK |
| PIN.DAT | AT S1E2R over its first 1 KiB, `sp11sb_pin_check` with the runner's SHA-256 | head | PIN_OK |
| registers | MIDR, MPIDR, CNTFRQ, HCR_EL2, SCTLR_EL2, TCR_EL2, CTR_EL0; the framebuffer from the arguments | head: the raw reads are stored and printed before the compare | OBSERVED_OK |
| placement | AT S1E2R over the map; `sp11sb_place_check`; `load32` of the first and last placement word; AT S1E2W over the placement | head | PLACED_OK |
| GUEST.DAT | AT S1E2R over it; structure, table digest, chunk and file digests per the policy | head | GUEST_OK |
| table | each chunk against the placement, the pinned guest window and the file, with no store | none | |
| copy | per chunk: the same check again, 8-byte stores, DC CVAC; the head after each chunk; then DSB, ISB | the chunks; head | COPYING (set before the first chunk), COPIED |
| verify | DC CIVAC over every chunk, DSB, ISB, then each destination hashed again from DRAM with the chunk's digest; zero chunks compared to zero | head | COPIED, DONE |

Until COPYING nothing but REPORT.DAT is stored to (VERIFIED: the harness logs every store with the stage the report held). The stores after it
are exactly the chunk ranges of the table: the harness shows the merged ranges equal the union of the chunks and no byte more.

**The policy.** The table digest is always checked; `SP11_STICK_HASH_POLICY` (default `CHUNKS|FILE` = 3, a compile-time value 0 to 3) adds the
chunk digests and the whole-file digest. The report records the policy used. On the unit the default took 1.90 s for the two digest passes over 275.9 MiB, about 145 MiB/s,
0.96 s for the verification and 2.92 s for the whole wrapper (OBSERVED, section 12); the q1n1 payload `hashbig` had hashed 144 MiB at 194 MiB/s (OBSERVED, section 8). A cheaper
policy such as 0 stays a wrapper choice, a rebuild and no change of format.

**Refusals.** `REFUSED=<NAME> STAGE=<S> DETAIL=0X..` is printed (and stored, when REPORT.DAT is usable), then one of: `NOTHING STORED:
REPORT.DAT NOT USED YET`, `RAM UNTOUCHED BEYOND REPORT.DAT` (stage below COPYING), `PLACEMENT WRITTEN CHUNKS_COPIED=k OF=n`. The return
value is `0x1000 + code`; `0x1001` silently when the arguments' magic, version or size is wrong or `puts` is absent; one literal line when `hex`
or `load32` is absent; 1 for a null pointer. The stage a refusal names is the last point reached.

| Stage named | Refusals raised there | Detail word |
| --- | --- | --- |
| NONE (nothing stored) | NOT_EL2; ARGS_BAD (a table that cannot be read: null, misaligned, wrong magic, version or entry size; or this payload is not slot 0); FILE_COUNT, FILE_ROLE, FILE_SIZE, FILE_BASE for REPORT.DAT's slot; PLACE_UNREADABLE for REPORT.DAT | CurrentEL; the slot (or 0); the probe address |
| ARGS_OK | FILE_* for any other slot | the first failing slot, or the count |
| FILES_OK | every PIN_* code, NO_AUTHORISATION, STORE_NOT_AUTH, PLACE_ALIGN, PLACE_LAYOUT, PLACE_LIMIT; PLACE_UNREADABLE for PIN.DAT | 0; the probe address |
| PIN_OK | MIDR_MISMATCH, MPIDR_MISMATCH, CNTFRQ_MISMATCH, E2H_SET, EL2_REGIME, GOP_MISMATCH, GIC_MISMATCH, STORE_NOT_AUTH | the value observed |
| OBSERVED_OK | MAP_BAD, PLACE_NOT_CONV, PLACE_ATTR, PLACE_DECLARED, PLACE_FB, PLACE_AVOID; PLACE_UNREADABLE (map, guarded read, AT) | the pinned `place_base`; the address, which is the first failing probe |
| PLACED_OK | GUEST_*, CHUNK_*, PAD_NONZERO, INDEX_DIGEST, FILE_DIGEST; CHUNK_DIGEST; PLACE_UNREADABLE for GUEST.DAT | 0; the chunk index; the probe address |
| GUEST_OK | CHUNK_RANGE, CHUNK_GEOMETRY, CHUNK_EOF from the pre-pass | the chunk index |
| COPYING | the same three if the table changed since the pre-pass; COPY_VERIFY | the chunk index |

`HASH_POLICY` is a compile-time static assertion, and `GIC_NOT_READY` belongs to the monitor entry (UNIMPLEMENTED); neither is raised. A
transcript of a complete host run (fake machine, not the unit; 19 lines of the 30 allowed):

```
SNAPMON START VERSION=1 EL=0X8 POLICY=0X3            SNAPMON STAGE=OBSERVED_OK
SNAPMON STAGE=ENTERED                                SNAPMON PLACE BASE=0X98000000 SIZE=0X22000000 DESC=1 DBASE=0X98000000 DPAGES=0X24E00
SNAPMON STAGE=ARGS_OK                                SNAPMON AT PLACEMENT PROBES=273 ATTR=WB SH=IS
SNAPMON STAGE=FILES_OK GUEST=0XCE200000 BYTES=...    SNAPMON STAGE=PLACED_OK
SNAPMON STAGE=PIN_OK LABEL=... CHUNKS=14             SNAPMON AT GUEST.DAT PROBES=9 ATTR=WB SH=IS
SNAPMON REGS MIDR=.. MPIDR=.. CNTFRQ=..              SNAPMON STAGE=GUEST_OK CHUNKS=14 POLICY=0X3 TICKS=..
SNAPMON REGS HCR=.. SCTLR=.. TCR=.. CTR=.. DLINE=64  SNAPMON STAGE=COPYING
SNAPMON GOP BASE=.. BYTES=.. W=.. H=.. STRIDE=.. FMT=..   SNAPMON STAGE=COPIED COPIED=14 BYTES=.. HASHED=11 HASHED_BYTES=..
SNAPMON TICKS HZ=.. PLACED=.. COPIED=.. HASHED=..    SNAPMON STAGE=DONE RETURN=0
SNAPMON MONITOR NOT CALLED: UNIMPLEMENTED IN THIS BUILD
```

The `AT ... ATTR= SH=` lines answer U6 for the big file: is GUEST.DAT cacheable at EL2. The unit's lines are in section 12 (`WB`, outer shareable: `SH=OS` where the fake machine
says `SH=IS`). `TICKS=` on the GUEST_OK line is the time of the stage-7
hashing; the head has no field for it (section 11).

## 4. What the unit does to the machine

All of it is in `probes/sp11-stick-hw.h`; the sources call nothing else. VERIFIED on the binaries (`tools/test-sp11-stick-wrapper-build.py`, which
reads the encodings of every code word and, separately, the disassembly):

- **System register reads (MRS):** CurrentEL, MIDR_EL1, MPIDR_EL1, CNTFRQ_EL0, CNTPCT_EL0, CTR_EL0, HCR_EL2, SCTLR_EL2, TCR_EL2, PAR_EL1. **No MSR**
  of any kind, no TLBI, no IC, no SMC, HVC, SVC or ERET, no BR, no wait or event hint. The reporter has no MRS at all.
- **Cache maintenance by VA:** DC CVAC and DC CIVAC, DSB SY and ISB; the stride is CTR_EL0.DminLine clamped to 16..64 bytes (this core's value is
  64 bytes, OBSERVED, section 12; a stride above the real line would skip lines, so an implausible one is clamped down). No DC IVAC: a pure invalidate could drop dirty
  bytes of an edge line the wrapper does not own. No IC: nothing in the placement is executed in this build.
- **Translation probes:** AT S1E2W and AT S1E2R, each followed by ISB and a read of PAR_EL1. PAR_EL1 is the one register this build changes, as a
  side effect of the instruction (INFERRED harmless: nothing of the runner reads it).
- **Stores to memory:** one function, `sp11hw_store64` (a single `str` that the disassembly check finds in two copies, one per translation unit),
  called only from the report head and the copy; every other store is to the stack or through a pointer argument. The indirect calls are the
  runner's `puts`, `hex` and `load32`. No data section, no libc call, no writable global.
- **Not touched:** EL2 system registers, timers, interrupts, the GIC, any MMIO. The store classes this build uses are **RAM** (REPORT.DAT and the
  placement) and nothing else, and it passes exactly that as the `build_stores` of the observed check. The format nevertheless requires the
  authorisation to carry RAM and EL2_SYSREG (`STORE_REQUIRED`, `sp11sb_pin_check`): the builder refuses `--stores ram`, so even this dry run is
  authorised with `--stores ram,el2_sysreg`, one class more than it uses (a finding for section 11).

EL2's stage 1 is taken to be the firmware's identity map; the probes check it rather than trust it.

## 5. The loader

The placement is free memory by the final map's account (`sp11sb_place_check`) and nobody else's: the wrapper writes it, cleans it and leaves it as
written. A range is probed at its first page, its last page and every 2 MiB boundary between; a page passes when AT reports it mapped, an identity
mapping and, for a write probe, Normal write-back. Sampled, not exhaustive (UNKNOWN: whether a hole can lie between two samples; the 2 MiB step
follows the firmware's block size, INFERRED). `load32` reads the first and last placement word first. Before every chunk is copied, its geometry
(16-byte granular), its place inside the placement and the pinned guest window, and its source inside the file are checked again from the file.

The verification reads the copy back after DC CIVAC, which writes back before it invalidates, so it proves the bytes are in DRAM and cannot tell a
missing DC CVAC from a working one: the clean after each chunk is verified on the host by the cache model of the harness, not on the unit (on the unit the placement was writable and the copy read back from DRAM matched, OBSERVED, section 12; UNKNOWN there: whether a CVAC alone reaches DRAM).

## 6. The reporter

It reads the five slots from its own arguments (its slot must be 1) and needs nothing from PIN.DAT. It reads the first word of REPORT.DAT with the
guarded `load32`, then the head. Lines carry the prefix `SNAPREP `; at most 9 are printed (the limit is 40).

| Situation | Output | Return |
| --- | --- | --- |
| arguments or slots refused | `REFUSED=<NAME> DETAIL=0X..` | `0x1000 + code` |
| the guarded read fails | `REPORT UNREADABLE ADDR=0X.. RC=n` | 2 |
| first word zero | `REPORT EMPTY: THE WRAPPER WROTE NOTHING` | 0 |
| the head fails `sp11sb_report_check` | `REFUSED=REPORT_<NAME> DETAIL=0X0`, then two `WORDS` lines of the first eight words | `0x1000 + code` |
| a head that passes | below | 0 |

For a head that passes, in this order: `REFUSED=<NAME> STAGE=<S> DETAIL=0X..` or `DRY RUN COMPLETE STAGE=DONE: THE MONITOR WAS NOT CALLED` or
`WRAPPER DID NOT FINISH STAGE=<S>`; `VERDICTS PIN=<NAME> GUEST=<NAME> POLICY=0X.. LINES=n`; when MIDR is not zero `REGS EL=.. MIDR=.. MPIDR=..
CNTFRQ=n` and `REGS HCR=.. SCTLR=.. TCR=..`; for a refusal below COPYING `RAM UNTOUCHED BEYOND REPORT.DAT` and nothing more; otherwise `PLACEMENT
WRITTEN` (refusals only), `CHUNKS TOTAL= COPIED= HASHED= BYTES_COPIED= BYTES_HASHED=`, `TICKS HZ= PLACED= COPIED= HASHED=` (ticks since the wrapper
entered, 0 for a point not reached), `PLACE BASE= SIZE= DESC= DBASE= DPAGES=`, `GUEST.DAT BASE= BYTES=`. The monitor's window is never decoded.

## 7. Using it on the stick

The manifest order is the bundle note's: SNAPMON.BIN, SNAPREP.BIN, GUEST.DAT, PIN.DAT, REPORT.DAT. The bundle builder takes the two built files as
`--monitor build/sp11-wrapper/SNAPMON.BIN --reporter build/sp11-wrapper/SNAPREP.BIN`, and the facts and authorisation record as its note says
(`docs/SP11-STICK-BUILD.md`), with `--stores ram,el2_sysreg` (the format's minimum) and a `--note FILE` that says in the record's section 6 what this
wrapper does and does not do (the record is the operator's approval, so the note is part of what is approved). **This note authorises no run.** A run on the unit
needs the operator's authorisation record, the single hardware client and the safety rules of AGENTS.md; the one run of section 12 had its own record, approved by the operator for that dry run only.

## 8. UNIMPLEMENTED and UNKNOWN

- UNIMPLEMENTED: the monitor entry, the stop timer, the GIC bring-up (and so GIC_NOT_READY), the decode of the monitor's window, and the reporter's
  comparison of the raw reads with PIN.DAT's pinned values and its "PIN.DAT damaged" row (the reporter does not read PIN.DAT).
- Answered on the unit (OBSERVED, one unit: the `memhash` plan of 2026-11-16, boots 8 and 9, with the read-only q1n1 payloads `hashbig` and `el2mem` and a 144 MiB data file, and the dry
  run of section 12; read from videos of the panel, not CRC-verified; the ID registers read are in [SURFACE-PRO-11.md](SURFACE-PRO-11.md), E65):
  - **U1, does EL2's own stage 1 reach the bank: yes, and the bank is writable.** `AT S1E2R` found all 15,361 probes of the 30,720 MiB bank at 0x880000000 mapped, and all 16,014 probes of the
    17 conventional extents (31,961 MiB) mapped Normal write-back, outer shareable, non-secure and identity; the dry run's `AT S1E2W` accepted all 337 probes of its 672 MiB placement. This
    wrapper's probe passes a page that AT reports mapped, an identity mapping and, for a write probe, Normal write-back (section 5), whatever its shareability.
  - **U6, hash throughput.** `hashbig`: 144 MiB in 0.74 s, 194 MiB/s, the digest equal to the runner's. The wrapper's own policy 3: about 145 MiB/s (section 3).
  - **U6, where the runner puts the big file.** A 144 MiB data file at 0xC3E65000 in both memhash boots and the 144,665,408-byte GUEST.DAT at 0xC446E000 in the dry run, each at the top of the
    590 MiB low extent (INFERRED for the dry run; 0xA8000000 to 0xCCE65000 before the allocation); the other files at 0xCEA7E000 to 0xCEA8D000 and REPORT.DAT at 0xCE9FD000 (one boot's allocations).
- UNKNOWN: whether DC CVAC alone reaches DRAM (the verification's CIVAC cleans first); every refusal path of the wrapper and the reporter on the unit (only the happy path ran); whether
  the report survives a restart; whether 2 MiB sampling finds every hole; whether the runner's placement repeats on another boot.
- Host evidence cannot show: real cache or MMU behaviour, memory attributes, timing, the runner's fault page, EL3.

## 9. Tests and what they show

```
export Q1N1_SP11_TREE=<q1n1 checkout>
python3 tools/test-sp11-stick-wrapper.py           # host harness: the dry run, the copy and its faults, the reporter, seeded-bad wrappers (SP11_WRAPPER_SKIP_SLOW=1 skips the default-size bundle)
python3 tools/test-sp11-stick-wrapper-refusals.py  # host harness: every damaged bundle refused with its code, at its stage
python3 tools/test-sp11-stick-wrapper-build.py     # build tool, binaries, gate, hygiene, this document
python3 tools/test-sp11-stick-wrapper-qemu.py      # QEMU end to end (section 10); SKIP without QEMU, EDK2 or the UEFI shell
```

Each test file has a scratch directory of its own below `build/`, so two can run at once; each file, Python included, stays under 1500 lines and
50 KiB (a hygiene test checks it).

The first two compile the REAL wrapper and reporter sources, q1n1's `sha256.c` and q1n1's text layer with AddressSanitizer and UndefinedBehaviorSanitizer
around a fake machine (`tools/test-sp11-stick-wrapper.c`): regions with a CPU view and a DRAM view, a write-back cache model with dirty bits, the
runner's `puts`, `hex` and `load32`, an ordered log of every store, cache operation, barrier, translation and register read, and fault injection (a
flipped bit in DRAM, a lost clean, a changed table, translation results). Together they cover the happy path on two placements (the low extent and the
bank), about a hundred damaged bundles (the second file), each refusal at its stage, the copy and its post-copy faults, the four hash policies and five
CTR_EL0 line sizes, and the reporter on every head. Bundles are made by the real builder. A test that skips is reported SKIP, never PASS: without clang
or a q1n1 checkout the native tests skip.

**The tests can fail.** Six seeded-bad wrappers (a store before COPYING, no chunk range check, no table pre-pass, no clean, a re-read of the source
instead of DRAM, no translation probe) each break the check that matches their fault while the real one passes it, and the disassembly analyzers
are run on synthetic code and on a compiled seeded unit (`msr`, `tlbi`, `hvc`, `br`, a store through a global) that they must flag.

**What they do not show.** The harness is a model written by the same hand as the wrapper: it proves the wrapper's logic, ordering and bounds against
that model, not the behaviour of the Oryon core's caches or MMU. A passing run is not hardware qualification.

## 10. QEMU end to end

`python3 tools/test-sp11-stick-wrapper-qemu.py` boots the REAL runner (q1n1's `Q1N1-PAYLOAD.EFI`, built with its `PAYLOAD=1` recipe into this tree's
`build/`) under QEMU's EDK2 and the pinned UEFI Shell (`SP11_UEFI_SHELL`), with the REAL SNAPMON.BIN and SNAPREP.BIN and a small synthetic bundle from
the real builder, through q1n1's own harness (`q1n1:tools/test-q1n1-sp11-payload-qemu.py`, imported, not copied, not edited). A first boot of a small
diagnostic payload reads what the bundle is pinned to: MIDR, MPIDR, CNTFRQ_EL0, the framebuffer the runner passes, the conventional descriptors of
the final map; the PIN.DAT facts and the placement (the foot of the largest conventional extent) are those. Every result is read from the panel text
the harness captures. It SKIPs, never passes, without QEMU, an EDK2 image, the shell, the LLVM tools or a q1n1 checkout.

| Case | Result (OBSERVED, QEMU 11.1.3, TCG) |
| --- | --- |
| happy path | all stages to DONE, return 0; the reporter's page (8 lines) agrees with the wrapper's 19; `AT PLACEMENT PROBES=241 ATTR=WB SH=IS` |
| one chunk byte damaged, the runner's manifest rebuilt over it | the runner passes both payloads; the wrapper refuses `CHUNK_DIGEST` at PLACED_OK, detail the chunk, `RAM UNTOUCHED BEYOND REPORT.DAT`, returns 0x104A |
| the same byte, the manifest of the good file | the runner refuses `HASH INDEX=2` before the bounce and calls nothing (the control) |
| wrong MIDR fact | `MIDR_MISMATCH` at PIN_OK, detail the CPU's MIDR, printed in the REGS line |
| placement over what the runner allocated | `PLACE_NOT_CONV` at OBSERVED_OK, not `PLACE_DECLARED` (below) |

What it showed that the host could not: the real runner's arguments, `puts`, `hex` and `load32` and a real EL2 accept the wrapper; AT S1E2W/R, DC CVAC
and DC CIVAC, the register reads and the 8-byte stores work there; the placement and GUEST.DAT probe as Normal write-back, inner shareable; the runner's
allocator (`AllocateAnyPages`) put the 15 MB file at the top of conventional memory, just under the map buffer, so a placement at the foot of the
extent was never in its way (OBSERVED on QEMU; on the unit the same runner put a 144 MiB data file at the top of the low extent, 0xC3E65000, in both boots: OBSERVED,
section 8; the unit's 144,665,408-byte GUEST.DAT went to 0xC446E000, section 12).

Two findings. (1) `-cpu max`, the harness's default, has EDK2 set TCR_EL2.T0SZ to 16 (OBSERVED in q1n1's logs), which the interface's rule (T0SZ 17 to 24,
`EL2_REGIME`) refuses: the wrapper is right to refuse, and the test runs `-cpu cortex-a76` (T0SZ 24) and `-m 2G` (the smallest placement is 480 MiB)
by rewriting those two options of the harness's command line. (2) `PLACE_DECLARED` cannot be reached with a truthful map: a declared range is memory the
runner allocated, the final map shows it as a non-conventional descriptor, and `PLACE_NOT_CONV` comes first in `sp11sb_place_check`. Only the host
harness reaches `PLACE_DECLARED`, by editing the map.

**What QEMU cannot show.** Oryon facts (the unit's MIDR, MPIDR, DminLine, cache and MMU behaviour); the unit's memory attributes and its real EL2
translation regime; EL3 and the firmware's behaviour after the Secure-Launch bounce (QEMU's `ExitBootServices` is the real one, slbounce is absent);
real throughput (the ticks in the report are TCG's: `PLACED=276312 COPIED=18335062 HASHED=27287125` at 62.5 MHz say nothing about the unit); whether
a clean reaches DRAM (QEMU has no cache); and the RAM after the run (nothing inspects it: the wrapper's own re-hash from DRAM is the evidence).

## 11. Decisions the coordinator may veto, and requests

- ARGS_BAD is reused for a preload table that cannot be read and for "this payload is not slot 0 (reporter: slot 1)"; the frozen table has no code for
  either. PLACE_UNREADABLE is reused for a failed AT or `load32` probe of REPORT.DAT, PIN.DAT, the map and GUEST.DAT, with the probe address as the
  detail; the frozen table describes only the placement. A header change request: a code for each.
- The head has no field for the stage-7 hashing time; it is on the GUEST_OK line only. `chunks_hashed` and `bytes_hashed` count data chunks read
  back from DRAM after the copy; `hash_policy` is 0 until the digest step.
- The authorisation must carry EL2_SYSREG as well as RAM (`STORE_REQUIRED`), so the dry run, which writes no EL2 system register, cannot be
  authorised for less than the monitor: a change request if the operator is to authorise the dry run alone.
- The sources are not in `probes/run.py`'s `OPTIONAL` table, as the bundle note's section 7 expects: this wrapper is a separate payload with its own
  build tool, and a gate-on monitor link would not put the head at offset 0 of the file. 4b needs that hunk and, then, a decision on the file layout.
- The dry run takes the GIC macros from a monitor profile instead of from a monitor build, because it has none.

## 12. The dry run on the unit (OBSERVED, one unit, one boot, 2026-11-16)

Plan DRYRUN-A, one autorun step: slbounce's de-VHE build, then the runner with SNAPMON.BIN (35,164 bytes), SNAPREP.BIN (21,076 bytes), the 144,665,408-byte GUEST.DAT, PIN.DAT and
REPORT.DAT, for the bank placement 0x880000000 with a 384 MiB guest, under an authorisation record (SHA-256 prefix 45c36225) that the operator approved for this dry run only. The
numbers were read from the video of the panel and are not CRC-verified; each SNAPREP line that repeats a SNAPMON line agrees with it. **It passed on the first attempt**: no refusal,
fault page or restart.

- Runner: `READY FILES=5 RANGES=8`, `VERIFY=OK` before each call. Wrapper: ENTERED to DONE, return 0, `LOADS=2 FAULTS=0 REFUSED=0`, then `MONITOR NOT CALLED: UNIMPLEMENTED IN THIS
  BUILD`. Reporter: `DRY RUN COMPLETE STAGE=DONE`, `PIN=OK GUEST=OK POLICY=0X3`, read from REPORT.DAT in RAM after the wrapper returned.
- The pins held: MIDR 0x512F0011, MPIDR 0x80000000, CNTFRQ 19200000, HCR_EL2 0x88000010, TCR_EL2 0x80843514, the framebuffer and the EL2 regime.
- Placement: one 30 GiB conventional descriptor (`DESC=215`), writable (337 `AT S1E2W` probes, Normal write-back, outer shareable). 16 chunks, 148,284,944 bytes, were copied into
  0x880000000 and 13 of 13 data digests matched when re-read from DRAM after DC CIVAC. GUEST.DAT sat at 0xC446E000, ending at the low extent's end (INFERRED).
- This core: `CTR_EL0` 0xB444C004, DminLine 64 bytes, which is the stride of the cleans (X2 T01 read the same value). INFERRED decode, Arm ARM not re-read: IDC 1, DIC 1.
- Ticks at 19.2 MHz from the wrapper's entry: placement checks 37,503; digests 36,519,004 (1.90 s); copy 1,120,002 (58 ms); verification 18,437,817 (0.96 s); total 56,114,326 (2.92 s).
- The stick afterwards: every staged file unchanged, REPORT.DAT all zero, one new file, the Shell's marker. The unit wrote nothing to it (A2, the first stick sector write, not exercised).

**NOT RUN on the unit:** every refusal path of the wrapper and the reporter (only the happy path ran); the report's survival across a restart (none happened); whether DC CVAC alone reaches
DRAM; the monitor entry, the GIC (U3), the timer traps (U2), SMP and the restart cause (U4), PAuth (U8) and the real kernelcache bundle (U9).
