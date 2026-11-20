<!-- SPDX-License-Identifier: MIT -->
# SP11 stick bundle: the interface between the bundle builder, the wrapper and the reporter

**Status: EXPERIMENTAL; the dry run ran once on the unit and passed (OBSERVED, [SP11-STICK-WRAPPER.md](SP11-STICK-WRAPPER.md) section 12); the monitor entry, the GIC, the timers and the guest are hardware NOT RUN.** This note freezes work item 1 of the monitor port plan
([SP11-MONITOR-PORT.md](SP11-MONITOR-PORT.md), section 13): the placement rule, the chunk container, the pinned-facts record, the
report buffer and the gate name. A C header, a Python module, host tests, the builder and the dry-run wrapper and reporter now exist, and
none of the operator decisions D1 to D10 of the audit (its section 15) is taken for the port (the bank was approved for the dry run only): section 11 lists what the format carries for each.

## 1. Reading this document

Labels (AGENTS.md): **VERIFIED** read in this repository or a reproducible host fact (a build, a test), never "ran on the Surface";
**OBSERVED** measured on the unit (boots 3 to 9, the restart probe and control, the dry run, the X1 and X2 runs) or reported by the operator, one unit, one boot
each; **INFERRED** reasoned, not checked; **EXPERIMENTAL** code exists, unqualified; **UNIMPLEMENTED** no code; **UNKNOWN** not established.
Citations: `path:line` is this repository, `q1n1:path:line` the sibling q1n1 checkout (e448f9a); a quoted fragment is written
`fragment`[path:line]. Facts about the unit come from the committed documents, not from a dump quoted here. Test outcomes are not recorded in
this note: they belong to the run that made them.

| Part | Where | Label |
| --- | --- | --- |
| This note | `docs/SP11-STICK-BUNDLE.md` | EXPERIMENTAL |
| Layouts, constants, refusal table and the pure checks, freestanding C | `probes/sp11-stick-bundle.h` | EXPERIMENTAL |
| Pack, unpack and the same checks in Python, an independent implementation | `tools/sp11_stick_bundle.py` | EXPERIMENTAL |
| Host tests, native harness under AddressSanitizer and UndefinedBehaviorSanitizer | `tools/test-sp11-stick-bundle.py`, `tools/test-sp11-stick-bundle-edges.py`, `tools/test-sp11-stick-bundle.c` | EXPERIMENTAL |
| Dry-run wrapper (`SNAPMON.BIN`), reporter (`SNAPREP.BIN`), bundle builder tool, the `SNAP_SP11_STICK` sources | `tools/sp11_stick_bundle_build.py`, [SP11-STICK-WRAPPER.md](SP11-STICK-WRAPPER.md) | EXPERIMENTAL (the dry run ran once on the unit) |

## 2. The five files

The bundle is the manifest of q1n1's stick runner: at most 5 files of at most 256 MiB, called in manifest order, the first a payload,
a name `<stem>.BIN` marking a payload and every other file data that is never called (`#define Q1N1_PAYLOAD_MAX_FILES`[q1n1:platform/uefi/sp11-payload.h:78],
`#define Q1N1_PAYLOAD_MAX_FILE_BYTES`[q1n1:platform/uefi/sp11-payload.h:112], its value with the big-file gate off; VERIFIED). The position in the manifest is the role. With the runner's own opt-in `Q1N1_SP11_BIGFILE` the limit is 256
operations of an 8 MiB chunk, 2 GiB (`#define Q1N1_PAYLOAD_MAX_FILE_BYTES`[q1n1:platform/uefi/sp11-payload.h:92]), and the wrapper of the root carry (`SNAP_SP11_STICK_ROOT_CARRY=1`,
[SP11-STICK-ROOT.md](SP11-STICK-ROOT.md)) accepts the same: every size in this note is then 2 GiB where it says 256 MiB, in the C header (`SP11SB_FILE_MAX`) and in the Python twin
(`sb.FILE_MAX_BIG`, `with sb.bigfile():`). That is a different pair from the one the records pin: with the gate at 0, the default, nothing below changes.

| Index | File | Kind | Content | Size rule |
| --- | --- | --- | --- | --- |
| 0 | `SNAPMON.BIN` | payload, called first | `BTI c; B payload_entry`, the wrapper, the loader and the monitor (the monitor alone was 311,359 bytes in audit section 4 and is 314,871 with the M1 profile; the dry-run wrapper, which has no monitor, is 35,164) | 1 byte to 256 MiB |
| 1 | `SNAPREP.BIN` | payload, called second | the report renderer, read-only | 1 byte to 256 MiB |
| 2 | `GUEST.DAT` | data | the chunk container of section 4 | 192 bytes to 256 MiB |
| 3 | `PIN.DAT` | data | the pinned facts of section 3 | exactly 1024 bytes |
| 4 | `REPORT.DAT` | data | zero bytes; the wrapper writes the report into its memory (section 6) | exactly 528384 bytes (0x81000) |

Paths are `\SP11\` plus the name, in any case; names are 8.3, as the manifest tool requires. The payloads are hashed by the runner
before the bounce and again at EL2 before each call; the data files are hashed once, before the bounce, and the runner's check at EL2
compares only the table's own fields for them (`though only the payload's bytes are hashed`[q1n1:platform/uefi/sp11-payload-run.c:106]; VERIFIED).
Consequence, the design rule of this note: **the wrapper hashes and checks what it uses**. The runner passes a payload no RAM beyond its own stack and
the preload table; a payload finds the others through that table, whose entry for a path holds its base and size
(`char path[256]`[q1n1:platform/uefi/preload.h:9]).

Line budget (VERIFIED arithmetic, the panel's row count INFERRED): the runner prints files + 1 = 6 lines and 3 per payload; the wrapper may print 30
lines and the reporter 40 (`#define Q1N1_PAYLOAD_LINES_MAX 40u`[q1n1:platform/uefi/sp11-payload-abi.h:37]): 6 + 6 + 30 + 40 = 82, inside the 89 rows
the runner's layout function computes for the unit's panel (INFERRED; the photographs of boot 4 held 58 lines).

## 3. PIN.DAT: the pinned facts

One record of 1024 bytes, little-endian, built once per bundle and checked by the wrapper at EL2 before it trusts a field. The last 32 bytes are the
SHA-256 of the first 992, so one hash covers every other byte. "Changes" says who may change a field: *constant* (a version bump), *unit* (only a new
measurement of the unit), *builder* (per bundle), *decision* (the operator). An `OBSERVED` value is one unit, one boot each. The wrapper compares the
unit rows against its own reads (section 8); the `descriptor_sha256` and `acpi` rows bind the bundle to the dump it was built from and cannot be checked at
EL2 in ABI v1, which passes no RSDP.

| Offset | Width | Field | Unit | Source | Label | Changes |
| --- | --- | --- | --- | --- | --- | --- |
| 0x000 | 8 | `magic` | bytes | `SNAPBPIN` | EXPERIMENTAL | constant |
| 0x008 | 4 | `version` | - | 1 | EXPERIMENTAL | constant |
| 0x00C | 4 | `size` | bytes | 1024 | EXPERIMENTAL | constant |
| 0x010 | 4 | `flags` | - | zero in version 1; a set bit is refused | EXPERIMENTAL | constant |
| 0x014 | 4 | `authorised_stores` | mask | store classes the operator authorised: RAM 1, EL2_SYSREG 2, GIC 4; RAM and EL2_SYSREG are required | UNKNOWN (D4) | decision |
| 0x018 | 16 | `board_id` | string | id of the board descriptor, `sp11` (`config/boards/sp11.json`) | EXPERIMENTAL | builder |
| 0x028 | 24 | `build_label` | string | free text shown on the panel to match a photograph to a bundle | EXPERIMENTAL | builder |
| 0x040 | 32 | `descriptor_sha256` | SHA-256 | the boot-ready board descriptor (the committed one is a template that `require_boot_ready` refuses) | UNIMPLEMENTED | builder |
| 0x060 | 32 | `authorisation_sha256` | SHA-256 | the operator's authorisation record naming the payload digests (work item 6); zero is refused | UNKNOWN (D7) | decision |
| 0x080 | 8 | `guest_file_size` | bytes | exact length of GUEST.DAT | EXPERIMENTAL | builder |
| 0x088 | 4 | `guest_chunk_count` | - | entries in its table | EXPERIMENTAL | builder |
| 0x08C | 4 | `reserved0` | - | zero | EXPERIMENTAL | constant |
| 0x090 | 32 | `guest_file_sha256` | SHA-256 | whole GUEST.DAT, equal to the runner manifest's digest | EXPERIMENTAL | builder |
| 0x0B0 | 32 | `guest_index_sha256` | SHA-256 | its chunk table (section 4) | EXPERIMENTAL | builder |
| 0x0D0 | 8 | `boot_mpidr` | MPIDR_EL1 | 0x80000000, the boot CPU (`docs/SP11-STICK-LOG.md:890`) | OBSERVED (boots 4, 5, 7) | unit |
| 0x0D8 | 4 | `cntfrq_hz` | Hz | 19,200,000 (`CNTFRQ_EL0` 0x124F800, `docs/SURFACE-PRO-11.md:1008`) | OBSERVED (boots 4, 5, 7, X2 T03) | unit |
| 0x0DC | 4 | `midr_count` | - | number of values in `midr` | EXPERIMENTAL | builder |
| 0x0E0 | 16 | `midr` | MIDR_EL1 | 0x511F0011 (clusters 1 and 2) and 0x512F0011 (cluster 0), ascending (`docs/SP11-STICK-LOG.md:916`) | OBSERVED (boot 7, restart probe step 3) | unit |
| 0x0F0 | 8 | `gicd_base` | PA | 0x17000000, MADT of the boot 6 dump (audit section 7, `docs/SP11-MONITOR-PORT.md:192`) | OBSERVED (boot 6 dump) | unit |
| 0x0F8 | 8 | `gicr_first` | PA | 0x17080000 | OBSERVED (boot 6 dump) | unit |
| 0x100 | 8 | `gicr_limit` | PA | 0x17380000 = first + 12 x stride; the monitor's `SNAP_GICR_LIMIT` | OBSERVED (boot 6 dump) | unit |
| 0x108 | 8 | `gicr_stride` | bytes | 0x40000 | OBSERVED (boot 6 dump) | unit |
| 0x110 | 8 | `its_base` | PA | 0x17040000, zero when absent | OBSERVED (boot 6 dump) | unit |
| 0x118 | 4 | `gicr_count` | - | 12 redistributor frames, all read in T05 | OBSERVED (boot 3, T05) | unit |
| 0x11C | 4 | `timer_intid` | INTID | 26, the non-secure EL2 timer PPI of the GTDT; the monitor's `SNAP_TIMER_INTID` | OBSERVED (boot 6 dump) | unit |
| 0x120 | 8 | `fb_base` | PA | 0xE4800000, the GOP framebuffer, inside a RESERVED descriptor (`docs/SURFACE-PRO-11.md:955`) | OBSERVED (B1 attempts 1 to 5, operator report) | unit |
| 0x128 | 8 | `fb_bytes` | bytes | 0x1518000 = 2880 x 1920 x 4 | OBSERVED (B1 attempts 1 to 5, operator report) | unit |
| 0x130 | 4 | `fb_width` | pixels | 2880 | OBSERVED (B1 attempts 1 to 5, operator report) | unit |
| 0x134 | 4 | `fb_height` | pixels | 1920 | OBSERVED (B1 attempts 1 to 5, operator report) | unit |
| 0x138 | 4 | `fb_stride` | pixels | 2880 per scanline | OBSERVED (B1 attempts 1 to 5, operator report) | unit |
| 0x13C | 4 | `fb_format` | - | 1, BGRX (0 is RGBX) | OBSERVED (B1 attempts 1 to 5, operator report) | unit |
| 0x140 | 8 | `place_base` | PA | start of the one placement (section 5), equal to `guest_base` | UNKNOWN (D1) | decision |
| 0x148 | 8 | `place_size` | bytes | `guest_size + reserve_size + work_size` | UNKNOWN (D1) | decision |
| 0x150 | 8 | `guest_base` | PA | the DRAM declared to the guest, a multiple of 32 MiB | UNKNOWN (D1) | decision |
| 0x158 | 8 | `guest_size` | bytes | at least 192 MiB, a multiple of 32 MiB | UNKNOWN (D1) | decision |
| 0x160 | 8 | `reserve_size` | bytes | held back directly above the guest, at least 48 MiB, a multiple of 16 MiB | EXPERIMENTAL | builder |
| 0x168 | 8 | `work_base` | PA | the end of the reserve: x1 of the monitor call | EXPERIMENTAL | builder |
| 0x170 | 8 | `work_size` | bytes | exactly 240 MiB: x2 | VERIFIED | constant |
| 0x178 | 4 | `avoid_count` | - | 0 to 4 | EXPERIMENTAL | builder |
| 0x17C | 4 | `reserved1` | - | zero | EXPERIMENTAL | constant |
| 0x180 | 96 | `avoid` | 4 x 24 | ranges the placement must stay out of although the map calls them free: base, size, tag (1 EL2_UNREACHABLE, 2 DEVICE_OWNED, 3 OPERATOR) | UNKNOWN (U1) | builder |
| 0x1E0 | 4 | `acpi_count` | - | 0 to 8 | EXPERIMENTAL | builder |
| 0x1E4 | 4 | `reserved2` | - | zero | EXPERIMENTAL | constant |
| 0x1E8 | 320 | `acpi` | 8 x 40 | tables the facts came from: signature, length, SHA-256, ascending by signature | UNIMPLEMENTED | builder |
| 0x328 | 184 | `reserved3` | - | zero | EXPERIMENTAL | constant |
| 0x3E0 | 32 | `record_sha256` | SHA-256 | bytes 0 to 0x3DF | EXPERIMENTAL | constant |

Nested layouts: an `avoid` entry is base (u64 at 0), size (u64 at 8), tag (u32 at 16), reserved (u32 at 20); an `acpi` entry is signature (4 bytes at 0), length (u32 at 4)
and SHA-256 (32 bytes at 8). Unused slots are zero and a nonzero one is refused. `reserve_size` is at least 48 MiB because `return max(0x3000000`[probes/xnuboot.py:436]
is the loader's floor, and `work_size` is `WORK_PAGES = 61440`[probes/monitor_work.py:6] pages of 4 KiB.

## 4. GUEST.DAT: the chunk container

What `xnuboot.build()` makes is a dictionary of physical address to bytes (`probes/xnuboot.py`), uploaded over the host link on the A16. The container carries the same
chunks to the stick: a 128-byte head, a table of 64-byte entries and the chunk data, in **one canonical layout**, so that a container has exactly one encoding and every byte of
the file is covered by a digest or required to be zero. Little-endian throughout; the file is at most 256 MiB, which holds the 115.5 MiB image slot, 16 MiB of tables and 5 MiB of SPTM data
of the A16 layout (`probes/xnuboot.py:314`, `probes/xnuboot.py:322`; VERIFIED arithmetic: 136.5 MiB).

| Offset | Width | Head field | Meaning |
| --- | --- | --- | --- |
| 0x00 | 8 | `magic` | `SNAPBGST` |
| 0x08 | 4 | `version` | 1 |
| 0x0C | 4 | `head_size` | 128 |
| 0x10 | 4 | `flags` | zero in version 1 |
| 0x14 | 4 | `entry_size` | 64 |
| 0x18 | 4 | `entry_count` | 1 to 1024 chunks (a 64 KiB table) |
| 0x1C | 4 | `reserved0` | zero |
| 0x20 | 8 | `table_offset` | 128 |
| 0x28 | 8 | `data_offset` | `table_offset + entry_count x 64` |
| 0x30 | 8 | `file_size` | exact length of the file |
| 0x38 | 8 | `load_base` | the pinned `guest_base`: every chunk lies inside `[load_base, load_base + load_size)` |
| 0x40 | 8 | `load_size` | the pinned `guest_size` |
| 0x48 | 32 | `index_sha256` | SHA-256 of the table bytes: a digest of digests |
| 0x68 | 24 | `reserved1` | zero |

| Offset | Width | Entry field | Meaning |
| --- | --- | --- | --- |
| 0x00 | 8 | `dest` | physical destination, a multiple of 16; entries ascend and never overlap |
| 0x08 | 8 | `size` | bytes at the destination, a multiple of 16, at least 16 |
| 0x10 | 8 | `file_offset` | where the data starts in the file; 0 for a ZERO chunk |
| 0x18 | 2 | `kind` | the kinds below |
| 0x1A | 2 | `flags` | 0 or ZERO (1): no file bytes, the wrapper clears the destination. The only flag of version 1 |
| 0x1C | 4 | `reserved` | zero |
| 0x20 | 32 | `sha256` | SHA-256 of the `size` data bytes; all zero for a ZERO chunk, and only for one |

| Kind | Name | Rule | Made by `xnuboot.build()` |
| --- | --- | --- | --- |
| 1 | KERNEL | at least one | the rebased image segments |
| 2 | TABLES | at least one | the 16 KiB guest page tables |
| 3 | HANDOFF | at least one | the SPTM handoff and boot-data pages |
| 4 | DEVICE_TREE | exactly one | the device tree at `DEVICETREE_PA` |
| 5 | PARAMS | exactly one; its destination is x3 of the monitor call | `struct params` at `PARAMS_PA` |
| 6 | MONITOR_IMAGE | any number | the SPTM and TXM images |
| 7 | MONITOR_DATA | any number | SPTM and TXM data, frame table and map, scratch, stacks |
| 8 | OTHER | any number | any other chunk the profile makes |

Kinds name what the bytes are for accounting and for these rules; the wrapper copies every kind alike. The builder pads a data chunk with zeros to a multiple of 16 and
decides what to zero; the container never invents a byte. Rules, all refused rather than ignored: an unknown version, flag or kind; a nonzero reserved byte or pad byte; `entry_count` 0, over 1024
or a table that does not fit the file; `file_size` other than the file's length; a destination outside the load window, unsorted, overlapping or unaligned; a data offset other than the next multiple of 64
after the previous data (the first starts at `data_offset`); bytes after the last chunk.

**Digests, in the order the wrapper may choose to pay for them.** (1) The table digest `index_sha256`, always checked: it commits to every entry's destination, size, offset and digest, and PIN.DAT pins it
(`guest_index_sha256`), so a GUEST.DAT built for another PIN.DAT is refused for the price of hashing at most 64 KiB. (2) Each data chunk against its entry's `sha256`. (3) The whole file against
`guest_file_sha256`, which equals the manifest's digest. The wrapper's policy (CHUNKS, FILE, both or neither) is not in the bundle, so a cheaper check can be chosen after measurement without a format change; the
report records the policy used. A read-only payload hashed 144 MiB at EL2 in 0.74 s, 194 MiB/s (boots 8 and 9), and the wrapper's policy 3 took 1.90 s for its two passes, about 145 MiB/s (U6; OBSERVED, dry run), so CHUNKS|FILE costs about 0.7 s a pass and policy 0 stays a wrapper choice. Without CHUNKS or FILE a corrupted data byte is not seen at EL2; the runner's check before the bounce still saw it. The hash is q1n1's freestanding `q1n1_sha256`: the monitor build already links `'../q1n1/platform/uefi/sha256.c'`[probes/run.py:127] and searches `ROOT.parent / "q1n1/platform/uefi"`[probes/run.py:168] for its header (VERIFIED), so the wrapper adds no hash code and the header takes the function as a callback.

## 5. The placement rule

**One placement** `P = [guest_base, guest_base + guest_size + reserve_size + work_size)`: the DRAM declared to the guest, the reserve the kernel places its structures in
(the A16's stage 2 maps both as one range, `DRAM + reserve`), and the monitor's work area, contiguous, as `probes/run.py` lays them out. The builder pins it; the wrapper verifies it.

| Rule | Check | Code |
| --- | --- | --- |
| Grid | `guest_base` and `guest_size` multiples of 32 MiB (`REGION_ALIGN = 0x2000000`[probes/xnuboot.py:451]); `reserve_size` of 16 MiB; `work_base` of 64 KiB; `place_base` and `place_size` of 16 MiB (the Shape rule implies it, but this rule runs first: a misaligned one is PLACE_ALIGN, not PLACE_LAYOUT, `(pb \| ps) & (SP11SB_RESERVE_ALIGN - 1)`[probes/sp11-stick-bundle.h:532]) | PLACE_ALIGN |
| Shape | `guest_size` >= 192 MiB (a floor: the SPTM data of the small layout ends at 167 MiB, `probes/xnuboot.py:323`; `place()` is the authority and its `large` branch needs 256 MiB), `reserve_size` >= 48 MiB, `work_size` = 240 MiB, `work_base` = the end of the reserve, `place_base` = `guest_base`, `place_size` = the sum | PLACE_LAYOUT |
| Stage-2 limit | the end is at most 2^36 (`IPA_LIMIT = 1 << 36`[probes/monitor_work.py:12]); ending exactly there is allowed and starting at or beyond it is not, as `shadow/stage2.c` checks | PLACE_LIMIT |
| One descriptor | inside exactly one EFI descriptor of type 7 (conventional) of the final map, and no other descriptor touches it | PLACE_NOT_CONV |
| Write-back | that descriptor has EFI_MEMORY_WB (attribute bit 3); every conventional descriptor of the local dumps reads 0x100F | PLACE_ATTR |
| Declared ranges | outside every range the runner declared and the payload stack: the files, the map buffer, the preload table | PLACE_DECLARED |
| Framebuffer | outside the runner's framebuffer range | PLACE_FB |
| Avoid list | outside every pinned `avoid` range | PLACE_AVOID |
| Reachable | a guarded read of the first and last word of `P` at EL2 does not abort (runtime, wrapper only) | PLACE_UNREADABLE |

The first three rows are arithmetic on PIN.DAT and the builder checks them too; the others read the final map the runner passes (`map_base`, `map_size`, `map_stride`) and the builder runs
them against the recorded map. The map's descriptors are read as the EFI layout: type u32 at 0, physical start u64 at 8, pages u64 at 24, attribute u64 at 32, at most 1024 of them.

**Low and bank both fit; D1 is open.** A low placement under 4 GiB: the largest low extent is 590 MiB (576 MiB on the grid), which holds a 256 MiB guest (256 + 48 + 240 = 544 MiB) and not the A16's 384 MiB
smoke (672 MiB) (OBSERVED, boot 6 map, audit section 5). A bank placement at 0x880000000: the bank is 30720 MiB and RAM ends at 2^36, so 384 MiB + 48 + 240 = 672 MiB fits with room (OBSERVED, boot 3 T09). No rule
above depends on the address except the 2^36 limit, and the test builds a bundle for each. EL2's own map reaches the bank and the bank is writable at EL2 stage 1 (U1; OBSERVED): `AT S1E2R` found all 15,361 probes of its 30720 MiB mapped, Normal write-back, outer shareable (boots 8 and 9); in the dry run `AT S1E2W` accepted 337 probes of the 672 MiB placement and 148,284,944 copied bytes were read back from DRAM, 13 of 13 data digests equal (plan DRYRUN-A step 1). The runner put a 144 MiB file at 0xC3E65000 and the 138 MiB GUEST.DAT at 0xC446E000, both at the top of the low extent (U6; OBSERVED, INFERRED for the dry run's file), so a low placement must be declared (`PLACE_DECLARED` in the builder, `PLACE_NOT_CONV` against the final map). What stays UNKNOWN: whether DC CVAC alone reaches DRAM (CIVAC cleans first, so the read-back cannot tell).

## 6. The report buffer

**What it is.** `REPORT.DAT`, a zero file of 528384 bytes the runner loads, hashes and declares like any data file, so its memory is the runner's allocation (type LoaderData), found through the preload table by its
name. Page 0 is the head the wrapper writes and the reporter reads; from 0x1000, 512 KiB (`OUT_HEADROOM = SECONDARY_CONFIG_AT - OUT_AT`[probes/run.py:238]: the most the A16 layout leaves the monitor's report, whose real size is
under the 64 KiB of `REPORT_RESERVE = 0x10000`[probes/run.py:233]) is the monitor's own report window, `x0` of the monitor call, opaque to this interface; its first two words are `status` and `entered`
(`probes/xnuboot.S:12`). The head is 296 bytes; the rest of page 0 is zero and checked.

| Offset | Width | Report field | Meaning |
| --- | --- | --- | --- |
| 0x000 | 8 | `magic` | `SNAPBRPT`; all zero means never written |
| 0x008 | 4 | `version` | 1 |
| 0x00C | 4 | `head_size` | 296 |
| 0x010 | 4 | `stage` | the stage table below |
| 0x014 | 4 | `refusal` | the refusal code, 0 when none |
| 0x018 | 8 | `refusal_detail` | a chunk index, an offending value or a runtime code's argument |
| 0x020 | 4 | `hash_policy` | CHUNKS (1) and FILE (2) bits actually used |
| 0x024 | 4 | `lines_printed` | wrapper lines, at most 30 |
| 0x028 | 8 | `current_el` | raw read |
| 0x030 | 8 | `midr_el1` | raw read |
| 0x038 | 8 | `mpidr_el1` | raw read |
| 0x040 | 8 | `cntfrq_el0` | raw read |
| 0x048 | 8 | `hcr_el2` | raw read |
| 0x050 | 8 | `sctlr_el2` | raw read |
| 0x058 | 8 | `tcr_el2` | raw read |
| 0x060 | 4 | `pin_code` | the wrapper's verdict on PIN.DAT |
| 0x064 | 4 | `guest_code` | and on GUEST.DAT |
| 0x068 | 8 | `guest_file_base` | where the runner put GUEST.DAT |
| 0x070 | 8 | `guest_file_size` | its size |
| 0x078 | 8 | `place_base` | the placement as verified against the final map |
| 0x080 | 8 | `place_size` | its size |
| 0x088 | 8 | `desc_base` | the conventional descriptor that held it |
| 0x090 | 8 | `desc_pages` | its pages |
| 0x098 | 4 | `desc_index` | its index in the map |
| 0x09C | 4 | `chunks_total` | entries |
| 0x0A0 | 4 | `chunks_copied` | at most `chunks_total` |
| 0x0A4 | 4 | `chunks_hashed` | at most `chunks_total` |
| 0x0A8 | 8 | `bytes_copied` | bytes written to the placement |
| 0x0B0 | 8 | `bytes_hashed` | bytes hashed |
| 0x0B8 | 8 | `counter_hz` | `CNTFRQ_EL0` as read |
| 0x0C0 | 8 | `ticks_enter` | `CNTPCT_EL0` at payload entry |
| 0x0C8 | 8 | `ticks_placed` | after the placement was verified |
| 0x0D0 | 8 | `ticks_copied` | after the copy and cache maintenance |
| 0x0D8 | 8 | `ticks_hashed` | after the hashing |
| 0x0E0 | 8 | `ticks_launch` | just before the monitor call |
| 0x0E8 | 8 | `ticks_return` | just after it returned |
| 0x0F0 | 8 | `monitor_out` | x0 of the monitor call |
| 0x0F8 | 8 | `work_base` | x1 |
| 0x100 | 8 | `work_size` | x2 |
| 0x108 | 8 | `params_pa` | x3, the PARAMS chunk's destination |
| 0x110 | 8 | `monitor_ret` | what the monitor returned in x0 (a nonzero value is its quarantine result) |
| 0x118 | 8 | `monitor_status` | word 0 of its window: 0 entered and returned, negative a prepare failure |
| 0x120 | 8 | `monitor_entered` | word 1 |

| Stage | Name | Meaning: the last point reached, so a refusal is the check that follows it |
| --- | --- | --- |
| 0 | NONE | nothing written |
| 1 | ENTERED | the head is initialised; REPORT.DAT passed its own file check |
| 2 | ARGS_OK | the runner's arguments are ABI v1 |
| 3 | FILES_OK | the five slots passed |
| 4 | PIN_OK | PIN.DAT passed, digest included |
| 5 | OBSERVED_OK | registers, GIC macros, framebuffer and store classes agree with the pins |
| 6 | PLACED_OK | the placement passed against the final map and the guarded reads |
| 7 | GUEST_OK | GUEST.DAT structure and the chosen digests passed: every refusal of the preflight has been raised |
| 8 | COPYING | set before the first store to the placement |
| 9 | COPIED | chunks copied, caches cleaned, copy verified |
| 10 | PREPARED | the stop is armed (GIC or poll) |
| 11 | MONITOR_ENTERED | set just before the call |
| 12 | MONITOR_RETURNED | the monitor came back |
| 13 | DONE | the wrapper is returning |

**Wrapper return value** (printed by the runner as `RETURN=`): 0 when the wrapper ran to its end, `0x1000 + code` when it refused, 1 (the runner's) for arguments it cannot read. What a payload returns never stops the chain.

**Where the wrapper stores.** Before stage COPYING nothing but REPORT.DAT is stored to: every check of section 8 up to the digests reads and hashes only. So a refusal raised at stage 7 or below leaves the placement, the work area and every register as the
runner left them. ARGS_BAD, FILE_COUNT and a bad slot for REPORT.DAT itself cannot be recorded (they are what locates it): the wrapper prints them and returns the code.

| Claim | Why | Label |
| --- | --- | --- |
| The runner leaves the file alone between payloads: it rebuilds its arguments from nothing and writes only the stack's two guard words (`for (unsigned i = 0; i < GUARD_WORDS; i++) guard[i] = GUARD_PATTERN;`[q1n1:platform/uefi/sp11-payload-run.c:263]) | read | VERIFIED |
| The report is outside the guest's reach: the A16 stage 2 maps the DRAM and reserve, the panel and the shadow-table arena only (`probes/xnuboot.py:233`), and REPORT.DAT is outside the placement by `PLACE_DECLARED` and `PLACE_NOT_CONV` | read, host test | VERIFIED |
| A declared data file survives the bounce intact | the payload files did: the second hash passed after the bounce (boots 4, 5, 7); a data file takes the same path | INFERRED |
| The memory is reachable and writable at EL2 | the payload stack, also LoaderData, was written at EL2 on every payload call (boots 4, 5, 7) | INFERRED |
| The report survives a board restart | a restart was seen after two of three `cpuon` boots and after neither boot of the read-only control (audit section 10), so it goes with the secondaries' power cycling (INFERRED, cause UNKNOWN); whether RAM survives a restart was never checked | UNKNOWN |

**How SNAPREP finds it and what it shows.** SNAPREP reads the preload table named by its own arguments, checks the five slots and takes the slot of `\SP11\REPORT.DAT`; it needs no address from PIN.DAT, so a damaged PIN.DAT
cannot hide the report. It prints at most 40 lines, whatever the failure:

| Situation | What it prints |
| --- | --- |
| arguments or REPORT.DAT's slot refused | one line naming the refusal (ARGS_BAD, FILE_*), nothing else |
| the first word is zero | "report empty": the wrapper wrote nothing |
| the head fails its check | the refusal name (REPORT_*) and the first eight words in hex |
| a refusal below stage COPYING | the refusal, the stage, the detail, the raw reads and the pinned values they were compared with, and "RAM untouched beyond REPORT.DAT" |
| a refusal at COPYING or after | the same, the copy counters and the placement, and "placement written" |
| the monitor ran | the head's summary, the monitor's `status` and `entered`, then the decode of the window (work item 4) |
| PIN.DAT damaged | its own check of PIN.DAT beside `pin_code`: both are printed, and a difference is named |

A wrapper that never returns (a hang or a fault) is not followed by SNAPREP: the chain ends and the last `PAYLOAD CALL` line names it.

## 7. The gate `SNAP_SP11_STICK`

The wrapper, loader and reporter sources are built only under `-DSNAP_SP11_STICK=1`. It is off by default, needs `-DSNAP_PLATFORM_SP11=1` (an `#error` otherwise, the form of
`#error "Q1N1_SP11_PAYLOAD=1 needs SNAP_PLATFORM_SP11=1`[q1n1:platform/uefi/sp11-payload.h:53]) and accepts no value but 0 and 1. The sources enter the link through the `OPTIONAL` table of `probes/run.py`, whose rule is
`Units linked only when their opt-in define is in FLAGS`[probes/run.py:129]. `probes/sp11-stick-bundle.h` itself has no gate: it declares a contract and no existing build includes it (the test checks the build files).

With the gate off the A16 monitor is byte-identical: `tools/monitor_baseline.py` compares the size, the binary and the linked ELF for its four flag sets (`SETS = ('bare', 'base', 'control', 'fast')`[tools/monitor_baseline.py:40]) and must say IDENTICAL.
The gate must not change: any existing object or its flags; `probes/xnuboot.S` and the call `x0 = out buffer, x1 = work area, x2 = work size, x3 = params (physical),`[probes/xnuboot.S:5]; the A16 report layout; q1n1's payload ABI v1;
`tools/cycle.py`, which implements no active SP11 flow. With the gate on and a refusal, nothing outside REPORT.DAT has been written (section 6).

## 8. Refusal codes

Names are capitals, digits and `_`, at most 24 characters (the panel font); the number never changes meaning inside a version. Side: B the host builder, W the wrapper, R the reporter. The wrapper takes the checks in the order below and the first
failure wins, so a record that is wrong twice gets the same code from the host tool and the wrapper (one exception, item 2). The rows of the table are in code order, not in check order.
Codes 80 to 82 are raised by the wrapper's runtime steps; no pure check returns them.

1. Runner arguments: ARGS_BAD.
2. The five slots: FILE_COUNT, then slot 0 to slot 4 and in each slot FILE_ROLE, FILE_BASE, FILE_SIZE, so slot 0's size is refused before slot 1's role. The wrapper checks REPORT.DAT's slot alone first (section 6): when that slot and an earlier one are both bad, the wrapper returns the REPORT.DAT slot's code and the host tool the earlier slot's.
3. PIN.DAT: the header (PIN_TRUNCATED, PIN_MAGIC, PIN_VERSION, PIN_SIZE), PIN_DIGEST, then PIN_FLAGS (an undefined store bit too), PIN_STRING, PIN_COUNT, PIN_RESERVED, PIN_ORDER, PIN_VALUE, then the placement arithmetic (PLACE_ALIGN; PLACE_LIMIT when a sum wraps; PLACE_LAYOUT; PLACE_LIMIT when the end is over 2^36), then STORE_NOT_AUTH, last NO_AUTHORISATION.
4. The registers and pins: NOT_EL2, E2H_SET, EL2_REGIME, MIDR_MISMATCH, MPIDR_MISMATCH, CNTFRQ_MISMATCH, GIC_MISMATCH, GOP_MISMATCH, then STORE_NOT_AUTH for the wrapper build's own store classes.
5. The placement against the map: PLACE_LAYOUT for a placement of zero bytes or one that wraps (PIN.DAT's checks have refused it already), then MAP_BAD (the whole map is read first, so a bad descriptor after the one that holds the placement still counts), PLACE_NOT_CONV, PLACE_ATTR, PLACE_DECLARED, PLACE_FB, PLACE_AVOID.
6. GUEST.DAT, the head: GUEST_TRUNCATED (under 128 bytes), GUEST_MAGIC, GUEST_VERSION, GUEST_FLAGS, GUEST_SIZE (`head_size`, `entry_size`, `table_offset`), GUEST_RESERVED, GUEST_TRUNCATED (shorter than `file_size`), GUEST_SIZE (longer than `file_size`, or over 256 MiB),
   GUEST_COUNT (also when the table does not fit), GUEST_SIZE (`data_offset`), GUEST_LOAD, GUEST_PIN. Then the chunks one at a time in table order, each through CHUNK_KIND, CHUNK_FLAGS, CHUNK_RESERVED, CHUNK_ZERO, CHUNK_GEOMETRY, CHUNK_RANGE, CHUNK_ORDER, CHUNK_OVERLAP and, for a data chunk,
   CHUNK_EOF, CHUNK_OFFSET, PAD_NONZERO: the first bad chunk decides, so chunk 0's offset is refused before chunk 3's kind. Last GUEST_SIZE for bytes after the last chunk, CHUNK_DUP, CHUNK_MISSING.
7. The digests: HASH_POLICY, GUEST_COUNT (the step reads the count again so that it is safe on its own), INDEX_DIGEST, then each data chunk in table order through CHUNK_EOF (its range must lie inside the file) and CHUNK_DIGEST (the first failing index goes to `refusal_detail`), last FILE_DIGEST.

SNAPREP reads REPORT.DAT in the order REPORT_SIZE (the length), REPORT_MAGIC, REPORT_VERSION, REPORT_SIZE (`head_size`), REPORT_RESERVED, REPORT_VALUE.

| Code | Name | Side | Check |
| --- | --- | --- | --- |
| 1 | `ARGS_BAD` | -WR | the arguments' magic is not `Q1N1PAY1`, the version is not 1 or the size is under 192 |
| 2 | `FILE_COUNT` | BWR | the table lists other than 5 files (the runner itself refuses more than 5) |
| 3 | `FILE_ROLE` | BWR | slot i is not `\SP11\` plus the role name of i, any case, nothing after it |
| 4 | `FILE_BASE` | -WR | a base is zero, not page aligned, or base + size wraps |
| 5 | `FILE_SIZE` | BWR | a size is zero or over 256 MiB (2 GiB with the root carry), or REPORT.DAT is not 528384 bytes |
| 10 | `PIN_TRUNCATED` | BWR | PIN.DAT is shorter than 1024 bytes |
| 11 | `PIN_MAGIC` | BWR | the magic is not `SNAPBPIN` |
| 12 | `PIN_VERSION` | BWR | the version is not 1 |
| 13 | `PIN_SIZE` | BWR | longer than 1024 bytes, or the `size` field is not 1024 |
| 14 | `PIN_DIGEST` | BWR | SHA-256 of bytes 0 to 991 is not `record_sha256` |
| 15 | `PIN_FLAGS` | BWR | `flags` is nonzero, or `authorised_stores` has a bit other than RAM, EL2_SYSREG and GIC |
| 16 | `PIN_STRING` | BWR | `board_id` or `build_label` is empty, has no NUL inside its width, has a character outside A-Z, a-z, 0-9, `.`, `_`, `-`, or a nonzero byte after the NUL |
| 17 | `PIN_COUNT` | BWR | `midr_count` not 1 to 4, `avoid_count` over 4, `acpi_count` over 8, `gicr_count` not 1 to 32, `guest_chunk_count` not 1 to 1024 |
| 18 | `PIN_RESERVED` | BWR | a reserved word, the reserved tail, an unused array slot or an avoid entry's reserved word is nonzero |
| 19 | `PIN_ORDER` | BWR | `midr`, the `acpi` signatures or the `avoid` bases are not strictly ascending |
| 20 | `PIN_VALUE` | BWR | a zero MIDR, CNTFRQ or pinned digest; a GIC base unaligned, a stride under 0x20000 or not a multiple of 0x10000, a limit other than first + count x stride, the distributor inside the window, a timer INTID outside 16 to 31; a framebuffer base zero or unaligned, a zero or inconsistent geometry, a format over 1, bytes under stride x height x 4; a `guest_file_size` under 192 bytes or over 256 MiB; an avoid range unaligned, empty, wrapping, overlapping or with an unknown tag; an ACPI signature outside A-Z and 0-9, a length under 36 or a zero digest |
| 21 | `NO_AUTHORISATION` | BWR | `authorisation_sha256` is all zero |
| 22 | `STORE_NOT_AUTH` | BWR | `authorised_stores` lacks RAM or EL2_SYSREG; at EL2 (W), the wrapper build uses a store class the bundle does not list |
| 24 | `PLACE_ALIGN` | BWR | the grid rule of section 5 |
| 25 | `PLACE_LAYOUT` | BWR | the shape rule |
| 26 | `PLACE_LIMIT` | BWR | the end is over 2^36, or a sum wraps |
| 30 | `NOT_EL2` | -W- | `CurrentEL` is not 8 |
| 31 | `E2H_SET` | -W- | `HCR_EL2` bit 34 is set |
| 32 | `EL2_REGIME` | -W- | `SCTLR_EL2.M` is clear, the TG0 field of `TCR_EL2` is not 4 KiB or T0SZ is outside 17 to 24 (`tbnz x9,#34,.Lreturn`[compat/pauth-native.S:25] and the lines after it) |
| 33 | `MIDR_MISMATCH` | -W- | `MIDR_EL1` is not in `midr` |
| 34 | `MPIDR_MISMATCH` | -W- | `MPIDR_EL1` is not `boot_mpidr` |
| 35 | `CNTFRQ_MISMATCH` | -W- | `CNTFRQ_EL0` is not `cntfrq_hz` |
| 36 | `GIC_MISMATCH` | -W- | one of the monitor build's five macros (`SNAP_GICD_BASE`, `SNAP_GICR_FIRST`, `SNAP_GICR_LIMIT`, `SNAP_GICR_STRIDE`, `SNAP_TIMER_INTID`) differs from its pin |
| 37 | `GOP_MISMATCH` | -W- | the runner's framebuffer base, size, width, height, stride or format differs from its pin |
| 40 | `MAP_BAD` | BW- | no map, a stride under 40, a size not a multiple of it, over 1024 descriptors, or a descriptor unaligned, empty or wrapping |
| 41 | `PLACE_NOT_CONV` | BW- | the placement is not inside exactly one type 7 descriptor, or another descriptor touches it |
| 42 | `PLACE_ATTR` | BW- | that descriptor lacks EFI_MEMORY_WB |
| 43 | `PLACE_DECLARED` | BW- | it overlaps a declared range, a range wraps or more than 24 are given |
| 44 | `PLACE_FB` | BW- | it overlaps the framebuffer |
| 45 | `PLACE_AVOID` | BW- | it overlaps a pinned avoid range |
| 50 | `GUEST_TRUNCATED` | BW- | shorter than 128 bytes or than its `file_size` |
| 51 | `GUEST_MAGIC` | BW- | the magic is not `SNAPBGST` |
| 52 | `GUEST_VERSION` | BW- | the version is not 1 |
| 53 | `GUEST_FLAGS` | BW- | `flags` is nonzero |
| 54 | `GUEST_SIZE` | BW- | `head_size`, `entry_size`, `table_offset` or `data_offset` is not the version-1 value, the file is longer than `file_size` or over 256 MiB, or bytes follow the last chunk |
| 55 | `GUEST_RESERVED` | BW- | a reserved word or byte of the head is nonzero |
| 56 | `GUEST_COUNT` | BW- | `entry_count` is 0 or over 1024, or the table does not fit in the file |
| 57 | `GUEST_LOAD` | BW- | the load window is not the pinned guest range |
| 58 | `GUEST_PIN` | BW- | the file size, chunk count or table digest differs from PIN.DAT |
| 59 | `CHUNK_KIND` | BW- | a kind outside 1 to 8 |
| 60 | `CHUNK_FLAGS` | BW- | a flag other than ZERO |
| 61 | `CHUNK_RESERVED` | BW- | an entry's reserved word is nonzero |
| 62 | `CHUNK_ZERO` | BW- | a ZERO chunk with a file offset or a digest, or a data chunk with an all-zero digest |
| 63 | `CHUNK_GEOMETRY` | BW- | a destination or size not a multiple of 16, or a size of 0 |
| 64 | `CHUNK_RANGE` | BW- | the chunk is not inside the load window, or wraps |
| 65 | `CHUNK_ORDER` | BW- | the destination is below the previous chunk's |
| 66 | `CHUNK_OVERLAP` | BW- | it starts before the previous chunk ends |
| 67 | `CHUNK_EOF` | BW- | its data extends past the end of the file |
| 68 | `CHUNK_OFFSET` | BW- | its offset is not the next multiple of 64 after the previous data |
| 69 | `PAD_NONZERO` | BW- | a pad byte before it is nonzero |
| 70 | `CHUNK_DUP` | BW- | more than one PARAMS or DEVICE_TREE chunk |
| 71 | `CHUNK_MISSING` | BW- | no KERNEL, TABLES, HANDOFF, DEVICE_TREE or PARAMS chunk |
| 72 | `HASH_POLICY` | BW- | the digest policy has a bit other than CHUNKS and FILE |
| 73 | `INDEX_DIGEST` | BW- | SHA-256 of the table is not `index_sha256` |
| 74 | `CHUNK_DIGEST` | BW- | SHA-256 of a chunk's data is not its entry's (the failing index goes to `refusal_detail`) |
| 75 | `FILE_DIGEST` | BW- | SHA-256 of the file is not `guest_file_sha256` |
| 80 | `PLACE_UNREADABLE` | -W- | a guarded read of the placement's first or last word aborted (`ops.load32` error 6; U1) |
| 81 | `COPY_VERIFY` | -W- | a chunk read back from its destination differs from its source |
| 82 | `GIC_NOT_READY` | -W- | any refusal or failure of the GIC and stop stage; `refusal_detail` is its outcome (SP11-STICK-GIC.md 5, 10; D2, D4) |
| 90 | `REPORT_SIZE` | --R | REPORT.DAT is not 528384 bytes or `head_size` is not 296 |
| 91 | `REPORT_MAGIC` | --R | the magic is not `SNAPBRPT` |
| 92 | `REPORT_VERSION` | --R | the version is not 1 |
| 93 | `REPORT_RESERVED` | --R | a byte of page 0 after the head is nonzero |
| 94 | `REPORT_VALUE` | --R | an unknown stage, refusal or verdict code; `chunks_copied` or `chunks_hashed` over `chunks_total`, or `chunks_total` over 1024; a policy bit; more than 30 lines; a refusal with stage MONITOR_RETURNED or DONE, `(refusal && stage > SP11SB_STAGE_MONITOR_ENTERED)`[probes/sp11-stick-bundle.h:773] (a refusal that carries MONITOR_ENTERED itself is accepted; no tick or byte counter is checked) |

## 9. Versioning

One integer, `SP11SB_VERSION` = 1, in PIN.DAT, GUEST.DAT and the report head. It is bumped by any change to a layout, a constant, a magic, a kind or flag, the refusal table (a code added, renamed or renumbered), the check order,
the placement rule or the meaning of a stage. It is not bumped for a different value in PIN.DAT, a new test or a note. A bump means the builder, `SNAPMON.BIN` and `SNAPREP.BIN` are rebuilt together, a new manifest and new digests follow, and
every reader refuses the other versions with a `*_VERSION` code: nothing converts, and no bundle of one version is run by the other's wrapper. The test's golden digests of the synthetic records change in the same commit.

## 10. Non-goals

No wrapper, builder, reporter, monitor change or runner change; no hardware run; no secondary CPU (x4 of the monitor call is 0, so physical launch stays off); no compression, signing or encryption; no second placement; no ACPI parsing at EL2 in ABI v1
(an RSDP pointer would be a runner change); no knowledge of the kernel's bytes (U9). A bundle is not an attestation: the manifest digests and the operator's authorisation bind prepared to booted (audit section 12).

## 11. Decisions

None of D1 to D10 is taken here. The format carries what each would need.

| Decision | What the format carries | Label |
| --- | --- | --- |
| D1 placement: low with a smaller guest, or the bank | `place_*`, `guest_*`, `reserve_size`, `avoid`; both accepted by every check; `PLACE_UNREADABLE` for U1 | UNKNOWN |
| D2 stop mechanism: GIC timer or polling | the GIC pins and `GIC_MISMATCH`; the GIC store class in `authorised_stores` (clear for a polling stop); `GIC_NOT_READY` | UNKNOWN |
| D3 timer: O1 to O3 | `cntfrq_hz` (the host rate, checked); the guest rate lives in the PARAMS and DEVICE_TREE chunks, opaque here | UNKNOWN |
| D4 GIC stores by a payload | the GIC bit of `authorised_stores`; `STORE_NOT_AUTH` | UNKNOWN |
| D5 time box | the PARAMS chunk's watchdog; `counter_hz` and the `ticks_*` fields give the elapsed time; no PIN.DAT field | UNKNOWN |
| D6 NV2 record | embedded in `SNAPMON.BIN` (its digest only) | UNKNOWN |
| D7 authorisation of the SHA-named payloads | `authorisation_sha256`, nonzero or refused | UNKNOWN |
| D8 thermal | nothing; the authorisation record names the exception | UNKNOWN |
| D9 `SNAP_NO_FB` and the HVC patch | the kernel chunks and their digests; `fb_*` pinned either way | UNKNOWN |
| D10 `SNAP_FAIR_TOKEN` | a property of the monitor build in `SNAPMON.BIN` | UNKNOWN |

**Choices I made that the coordinator may veto.** (1) The report buffer is a fifth file, `REPORT.DAT`, not an address inside the placement: it is reachable at EL2 by the mapping the stack already uses, it can be written before anything is verified,
it needs no PIN.DAT field to be found and no verified placement to exist; it costs the last manifest slot. The alternative is a pinned report range inside the placement (528 KiB more), a `report_base` in PIN.DAT, and a locator in the payload stack that
only the runner's current behaviour (VERIFIED, not promised) keeps for SNAPREP. (2) `authorisation_sha256` must be nonzero. (3) Digests are SHA-256 only, the table digest is the digest of digests, and the policy is the wrapper's. (4) The line budgets are
30 and 40. (5) Chunk granularity is 16 bytes, data alignment 64.

## 12. What is left

| Work item | Left to do with this interface |
| --- | --- |
| 3 builder | BUILT for the synthetic path: `tools/sp11_stick_bundle_build.py` builds PIN.DAT, GUEST.DAT, REPORT.DAT and the manifest with this interface, takes the facts from the dumps and refuses a disagreement ([SP11-STICK-BUILD.md](SP11-STICK-BUILD.md)). Left: the `real` path (`xnuboot.build()`'s chunks from the Mac's kernelcache, U9) and a boot-ready descriptor |
| 4 wrapper, reporter | BUILT for the dry run (`probes/sp11-stick-*`, [SP11-STICK-WRAPPER.md](SP11-STICK-WRAPPER.md)): the checks in the order of section 8 with the stage written between them, the copy with cache maintenance, the guarded probe of the placement, the reporter's lines. Left (4b): the GIC, the monitor call, the head's remaining stages, and the `SNAP_SP11_STICK` sources through the `OPTIONAL` table; no `memcpy` or `memset` call: the header compiles without any, but `= {0}` or a by-value copy of one of its structures can compile to one even under `-ffreestanding` (with the monitor's flags clang calls `memcpy` for a copy of the 296-byte report head and `memset` for its `= {0}`; GCC does so for the environment structure too), so freestanding code initialises and stores such structures word by word, as the wrapper does |
| 6 authorisation | the record whose digest PIN.DAT pins; the store classes; the digests of the two payloads (`authorisation-template` prints it, with an optional note that is part of the digest: SP11-STICK-BUILD.md section 6) |
| 7 dry run | RAN once on the unit and passed (OBSERVED, [SP11-STICK-WRAPPER.md](SP11-STICK-WRAPPER.md) section 12): the hashes agreed, the copy was read back from DRAM, SNAPREP read REPORT.DAT after the wrapper returned. Left: the wrapper's refusal paths on the unit, and whether DC CVAC alone reaches DRAM |

## 13. Checking this note

`python3 tools/test-sp11-stick-bundle.py` builds the harness with AddressSanitizer and UndefinedBehaviorSanitizer and holds both implementations to the same bytes: the layout of every record, every constant and the refusal table; more than 30 seeded
bad records, each refused with the same code by the C and the Python checks; deterministic single-bit mutations of every record; SHA-256 against `hashlib` and, with `Q1N1_SP11_TREE` (or a checkout the test finds), the runner's own `sha256.c`
and constants; a freestanding compile with the monitor's flags that needs no libc and no absolute pointer; and this note's tables and citations. Without clang the native tests SKIP; without a q1n1 checkout the runner comparisons SKIP.

`python3 tools/test-sp11-stick-bundle-edges.py` adds the boundaries, each case through the same harness and the Python checks: every limit at, one below and one above (a file of 256 MiB, 1024 chunks, a 192 MiB guest, an end at 2^36, the INTID, T0SZ and TG0 bounds, 1024 map
descriptors); the wrap guards of the placement sums; a hand-built GUEST.DAT whose table digest is right and whose entries point past the end of the file, which the digest step must refuse without reading outside it; a sparse 256 MiB GUEST.DAT; the order of section 8 as pairs of
faults; and the bound of `unpack_guest`, which refuses a hostile `entry_count` before it loops.
