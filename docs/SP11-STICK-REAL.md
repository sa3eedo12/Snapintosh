<!-- SPDX-License-Identifier: MIT -->
# SP11 stick bundle: the kernelcache path (monitor port work item 3, `real`)

Status: EXPERIMENTAL host-side tooling. Hardware: NOT RUN for the tooling; its guests ran in boots 20, 21 and 23 (docs/SP11-STICK-LOG.md sections 18, 19 and 21). `real` ran end to end on the J700 26A428 kernelcache, on one machine, for the bank placement (base 0x880000000, 384 MiB guest), for all four D9 choices; every file passes the interface's checks in Python and in C. On 2026-11-16 the operator decided D9 (panel withheld, stock kernel, `--sptm-hvc-calls 0`), the time box (5 s), the stop (the GIC timer) and the board file of the first run; `tools/sp11_m1_descriptor.py` derives that file reproducibly and `real` accepts it (section 3). It is a GUEST VIEW: the adapter's MCFG equality rule (D11 proper) is not satisfied by it and `from_hamoa` would refuse it. The monitor and reporter of the last run are placeholders, a pipeline check. Nothing here authorises a run or relaxes a rule.

Labels are those of [SP11-STICK-BUNDLE.md](SP11-STICK-BUNDLE.md): VERIFIED (a host fact that the commands of section 8 reproduce, never "ran on the Surface"), OBSERVED, INFERRED, EXPERIMENTAL, UNIMPLEMENTED, UNKNOWN. This note adds to [SP11-STICK-BUILD.md](SP11-STICK-BUILD.md) and changes neither it nor the frozen interface. The kernelcache and everything made from it stay under the git-ignored `build/`; nothing of Apple's is written here, only sizes, digests, counts and names. The plan is `docs/SP11-MONITOR-PORT.md` section 13, WI 3.

## 1. Result

| Statement | Label | Evidence |
| --- | --- | --- |
| `real` builds GUEST.DAT, PIN.DAT, PAYLOAD.MAN, REPORT.DAT and the stage tree from the kernelcache, for `--panel` handed and withheld and `--sptm-hvc-calls` 0 and 1, and the stager's `verify` says COMPLETE with `shell_pin: match` | VERIFIED | host run (section 2) and `Kernelcache` in `tools/test-sp11-stick-bundle-real.py` |
| The interface's Python checks and its C twin (AddressSanitizer) accept the real PIN.DAT and GUEST.DAT; `place-check` passes 6 of 6 local maps for the bank | VERIFIED | the same run; the maps are the six local dumps of the unit |
| Classification needed no change: the real `built` fell into the interface's chunk kinds with no `BUILT_SHAPE` (the expected first failure did not occur) | VERIFIED | section 4 |
| The first real run found two defects the synthetic path cannot show, both fixed (the loader's output on stdout, a fresh random boot seed per build) | VERIFIED | section 4 |
| With `--sptm-hvc-calls 0` the kernel in GUEST.DAT is the kernelcache with only the loader's pointer rebase applied; with 1, 348 words more are replaced | VERIFIED | section 5 |
| A boot-ready descriptor for the first monitor run, derived from the template, the unit's facts and the operator's decisions of 2026-11-16; `real` accepts it | VERIFIED | `tools/sp11_m1_descriptor.py`, `tools/test-sp11-m1-descriptor.py` and the run of section 2 |
| That descriptor describes the Surface, or satisfies the adapter's MCFG equality rule (D11 proper) | UNIMPLEMENTED | it is a guest view: its ECAM list is empty and `check_hamoa_platform` refuses it at `ecam-mismatch` (section 3.5) |
| Anything the guest does under the monitor beyond the stops of boots 21 and 23 | UNKNOWN | the monitor entry ran with a guest of this tool in boot 20 (M1 rung 1, panel withheld; OBSERVED, one boot: the kernel executed for the 5 s box, INFERRED stuck in an early loop; `docs/SP11-STICK-LOG.md` section 18), in boot 21 (rung 2, panel as the console; OBSERVED, one boot: one vCPU, 2.04 s, to a stage-2 fault at what is INFERRED, by address, to be the first CPU-start write; section 19) and in boot 23 (rung 3, the console guest with `cpus=1`; OBSERVED, one boot: one vCPU, 2.17 s, to an exception at EL1 that the monitor stopped on, INFERRED to be the kernel's own debugger trap; section 21) |
| The unit's MIDR (0x512F0011 boot cluster, 0x511F0011 the others), GICD version 4, CNTFRQ 19.2 MHz, eight MCFG allocations | OBSERVED | boot 7 and restart probe step 3 (`docs/SP11-STICK-LOG.md`); the MADT, GTDT and MCFG of the six local dumps |

## 2. What ran

Inputs: the boot set of `downloads/extracted/j700ap/` (the kernelcache is 120,930,304 bytes, SHA-256 dc9019ae39187b36936c2c513b485e513f15cd966b5c887ac88ffd2925976c62, the digest the profile pins; DeviceTree, SPTM and TXM equal their pins), the local facts file, the six dumps, the dry run's SNAPMON.BIN and SNAPREP.BIN (placeholders for the monitor, WI 4b), `--stores ram,el2_sysreg,gic` (GUEST.DAT does not depend on the store classes; PIN.DAT and the record do, and the record's text for `gic` was a template at this run: do not approve that class before WI 4b and WI 6 exist), `--time-box-s 5`, base 0x880000000, 384 MiB (the reserve is 48 MiB, the work area 240 MiB, the place ends at 0x8aa000000), and the descriptor of section 3 (the first pass used a scratch stand-in).

| Item | Value |
| --- | --- |
| Time | the loader's `build()` 4 to 7 s; the template 9 to 12 s; a full build with the stage tree 10 to 13 s |
| GUEST.DAT, panel withheld | 118,870,336 bytes, 44 chunks (the dry run's synthetic filler was 144,665,408 bytes; the cap is 256 MiB) |
| GUEST.DAT, panel handed | 118,908,480 bytes, 46 chunks (the panel lies below the bank, so the frame map widens: two more chunks, one data and one zero, and one more table page) |
| GUEST.DAT, panel console | 118,908,480 bytes, 46 chunks (the guest of the panel handed with `serial=2` where its boot_args page has `serial=3`: one byte of one chunk, as `probes/run.py` makes it for `--console panel`; built by `--panel console`, `--m1-run` only) |
| GUEST.DAT, panel console with `cpus=1` (rung 3) | 118,908,480 bytes, 46 chunks (the console guest with ` cpus=1` after its command line: seven bytes of the same chunk; built by `--panel console --boot-arg cpus=1`, `--m1-run` only: `docs/SP11-STICK-MONITOR.md` section 20, K10) |
| Chunk kinds present | KERNEL, TABLES, HANDOFF, DEVICE_TREE, PARAMS, MONITOR_IMAGE, MONITOR_DATA; OTHER is absent (no RAM disk, SEP preload or trust-cache fallback) |
| Withheld, by kind (data and zero chunks) | KERNEL 13 and 12; MONITOR_DATA 4 and 4; MONITOR_IMAGE 4 and 2; HANDOFF 2; DEVICE_TREE 1; PARAMS 1; TABLES 1 |
| PARAMS, from the placement | `mem_low` 0x880000000, `mem_high` 0x89b000000 (guest plus reserve: the work base), `watchdog_seconds` 5; destination (x3 of the monitor call) 0x88000c000 = base + 0xC000 |
| GUEST.DAT SHA-256, withheld, 0 | 9e69e7b5612802f00948465fb620e879d36c9b1ac419be1b9cf079dcda463c15 |
| GUEST.DAT SHA-256, withheld, 1 | d8a71856becb48df98cfcd8184177be9743d0aae9dfbb3850f5f6439426a6947 |
| GUEST.DAT SHA-256, handed, 0 | b74826c66352c55737bb1bcb3a9e747c926dd4c9871581dab9a4d77ae9b0ed5c |
| GUEST.DAT SHA-256, handed, 1 | 5391ea4fc992a3f1ae96e2df8491ea62ac2e3b336dfbf90d3354f1c2b70bdae6 |
| GUEST.DAT SHA-256, console, 0 | acad28623aebb068320baf3e826f0f53d6a17ace17af602afe286ee9be77a34f |
| GUEST.DAT SHA-256, console, cpus=1, 0 | a7e159ad6c18832c60264f81763db6e7d5b4c3f3d3d5bafc7efcf6a073752f50 |
| Determinism | two builds, separate processes (one with another `PYTHONHASHSEED`): all 26 files identical, `build-report.json` and the stage tree included |
| Stage tree | 18 files, `verify` COMPLETE, `shell_pin: match` (the dry run's base tree and EFI images) |
| `probes/xnuboot.py --check` | exit 0 at its default base (328 chunks, 12 of 12 addresses resolve); the same check at the bank base with 384 MiB, with no SNAP variable and with the native-call and HVC variables, exit 0 each |

The digests hold for the facts file of the unit (CNTFRQ 19.2 MHz and the GOP, which only the handed and console guests read), the default seed label and the placement above. GUEST.DAT does not depend on the descriptor, the store classes or the payloads.

The run with the final choices (D9 withheld and stock, default seed label, `--time-box-s 5`, `--stores ram,el2_sysreg,gic`, the descriptor of section 3, the dry run's placeholders) is a pipeline check, not the bundle for hardware: the placeholders will be replaced by the GIC wrapper, whose digests differ, and the record, PIN.DAT and PAYLOAD.MAN follow them. The commands are in section 8; the 118 MB bundle was deleted afterwards.

| Item (final choices, placeholders) | Value |
| --- | --- |
| Descriptor (14,937 bytes, derived by `tools/sp11_m1_descriptor.py`); its SHA-256 is PIN.DAT's `descriptor_sha256` | 310cf3974326aa223aa5405395071d9b76673545d42ca191cebc7c437c581ed7 |
| GUEST.DAT, withheld, 0 (as in the table above) | 9e69e7b5612802f00948465fb620e879d36c9b1ac419be1b9cf079dcda463c15 |
| PIN.DAT (1,024 bytes) | bd127b3605c620de3130b48bef3670aa8c8aa944b4af19907c403cb4d96c2deb |
| PAYLOAD.MAN (1,512 bytes) | 72589b81df3159dae740c1a8fee4abdb09ef0089706c1e73ed64bedf00d3eada |
| The record (4,035 bytes with section 1a, section 7; PIN.DAT's `authorisation_sha256`; a template, no decision recorded) | 48d3dc25be6762c385cae18104176bb66fc5133948414e3287932546dc54bb04 |
| SNAPMON.BIN (35,164 bytes), SNAPREP.BIN (21,076 bytes), the placeholders | 6137984fbc61a48845e1ca7283911c6a87a4955802eebdb58ef941660bed2d15, 940c774f1377980f378929400571ed223f00c5fb87a054fbcd2a092f3c0b9aab |
| Result | two builds in separate processes (the second with another `PYTHONHASHSEED`): all 26 files identical; stage tree 18 files, `verify` COMPLETE, `shell_pin: match`; `probes/xnuboot.py --check` exit 0 (328 chunks, 12 `ok` lines) |

## 3. The descriptor

### 3.1 What `--descriptor` does in `real` (VERIFIED, read in `descriptor_digest`)

1. `real` demands one: without it, `DESCRIPTOR_NOT_READY`. The synthetic path pins a stand-in digest instead.
2. It loads the file with `tools/board_descriptor.py` and calls `require_boot_ready`. Any schema error or unknown field is `DESCRIPTOR_NOT_READY` too; the detail names the fields.
3. It holds the facts to the descriptor: GICD base, redistributor first, stride and limit, ITS base (zero for a board without one), CNTFRQ, the hypervisor timer's INTID, every MIDR value of the facts under some pair's mask, and the boot MPIDR's affinity among the descriptor's MPIDRs. Any difference is `FACTS_DISAGREE`, the detail names the fact.
4. It pins the SHA-256 of the file as `descriptor_sha256` of PIN.DAT.
5. Nothing else. `xnuboot.build()` takes no descriptor. The builder never calls `from_hamoa` or `check_hamoa_platform`, so the adapter's MCFG equality rule (D11) is not a rule of `real`. What `real` needs of the ECAM is that `pcie.ecam` is not null (`require_boot_ready`; an empty list is a claim: none handed over). The descriptor's ECAM is compared with no MCFG; only a facts file that states an `ecam` has it compared with the dumps.

### 3.2 What `require_boot_ready` demands

`board.status` not UNIMPLEMENTED; `soc.name`, `family`, `midr`; `clusters` count, cores, `mpidr`, `mpidrs`; `gic` version, `gicd`, `gicr`, `its`; `timer` frequency and all four interrupts; the `smmu` list; `pcie.ecam`; `limits.ipa_bits`; a `ranges` list with at least one DRAM window of known geometry; and no device whose state is `unknown`. A null is never defaulted and a populated claim cannot be UNIMPLEMENTED.

### 3.3 The operator's decisions of 2026-11-16: a guest view

Decided by the operator on 2026-11-16, for the board file the first monitor run uses (quoted from the app):

> Five devices with no established way to reach them (debug UART, keyboard, touchpad, touchscreen, NVMe storage) are marked absent, meaning withheld from the guest. Nothing is removed from the Surface. The list of PCIe config windows is empty: it holds only what this build gives the guest, and the Surface has eight that we are not handing over; the strict equality rule of the board adapter is left for later. Its status is set to EXPERIMENTAL so the builder accepts it.

The same day D9, D5 and D2 were decided (section 7).

| Field | First pass | Guest view |
| --- | --- | --- |
| `board.status` | UNIMPLEMENTED, the template's | EXPERIMENTAL; `board.name` gets the suffix " - GUEST VIEW, first monitor run" |
| `pcie.ecam` | null: blocked on D11 | `[]`: nothing handed over, none of the unit's eight MCFG allocations listed |
| `devices.uart-debug`, `keyboard`, `touchpad`, `touchscreen`, `nvme` | unknown: no established way to reach them | `absent`: withheld from the guest |

The four fields the unit settles (`soc.midr`, `gic.version`, `timer.frequency_hz`, `ranges.dram0`) are those of the first pass, now derived instead of written by hand. Each decision carries the provenance "operator decision 2026-11-16, guest view for the first monitor run" with this section, evidence EXPERIMENTAL (a decision is not a measurement); the four unit fields carry OBSERVED.

### 3.4 How it is derived (`tools/sp11_m1_descriptor.py`)

`python3 tools/sp11_m1_descriptor.py derive --facts build/sp11-facts.local.json --out build/sp11-m1-descriptor.json` reads the committed template `config/boards/sp11.json`, which stays unchanged, and the local facts file:

- The facts file holds neither the GIC version nor the DRAM bank, so the dumps are read too (`--dump`, default `downloads/sp11/x*-run*`), after the facts are held to them as `facts-check` does. The version is the GICD structure's in the MADT (3 or 4, the same in every dump, agreeing with the facts' redistributor stride: `GIC_VERSION_UNKNOWN`, `FACTS_DISAGREE`); the bank is the one conventional write-back descriptor at or above 4 GiB, identical in every dump (`DRAM_BANK_UNCLEAR`). `soc.midr` takes the facts' values with exact masks and `timer.frequency_hz` the facts' CNTFRQ.
- It fills only what the template leaves unknown. A template that already states one of these fields was promoted and the tool is out of date: `TEMPLATE_CHANGED`, nothing is overwritten.
- It validates the result as `real` will: `require_boot_ready`, the facts-versus-descriptor comparison (`DESCRIPTOR_NOT_READY`, `FACTS_DISAGREE`), and reads the written file back through the builder's `descriptor_digest`. No rule of `tools/board_descriptor.py`, `probes/bootcontract.py` or `tools/check-hamoa-acpi.py` is edited, relaxed or bypassed.
- The bytes are deterministic: sorted keys, two-space indent, ASCII, one final line feed and fixed provenance text. Two derivations, and one under another `PYTHONHASHSEED`, are byte-identical. On the unit's facts the file is 14,937 bytes with SHA-256 310cf3974326aa223aa5405395071d9b76673545d42ca191cebc7c437c581ed7, which `real` pinned as `descriptor_sha256` (section 2).
- It never overwrites (`OUT_NOT_EMPTY`) and writes a new file below `build/` only; `--allow-under DIR` names another directory. Anything else, a `..` or a symbolic link is `OUT_DIR_REFUSED`, and a path under `/Volumes` or `/dev` is judged by its spelling before the filesystem is looked at.
- `check --descriptor FILE --facts F` re-derives and refuses a file that differs in any byte (`GUEST_VIEW_MISMATCH`, naming the fields), after the board's own rules: a device back to unknown, a present device without a window, status UNIMPLEMENTED or a duplicate segment is refused by `require_boot_ready` or `validate` themselves; a wrong MIDR, GICD, ITS or timer pin by the facts comparison.
- The output says GUEST VIEW, names the decisions, states the adapter refusal of section 3.5, prints the digest and ends with `HARDWARE=NOT RUN`. A refusal prints `REFUSED=<NAME>` last on standard output and the detail on standard error.

The first pass's hand-written observed-only file `build/sp11-descriptor.local.json` is superseded by this derivation and may be deleted.

### 3.5 What the guest view is not: D11 proper, N2, X6 and `from_hamoa`

It is not the Surface's description: the Surface has eight config windows and the five devices are present on it; the file says only what this build gives the guest.

- The adapter's MCFG equality rule (D11 proper) is NOT satisfied. `from_hamoa` and `check_hamoa_platform` (`probes/bootcontract.py`, `_cross_check`, the comparison of the MCFG's allocations with `pcie.ecam`) refuse this descriptor with `ecam-mismatch` on the unit's tables, after the GICD, version, CPU, redistributor and ITS checks pass. That is expected: the MCFG lists eight allocations and the file lists none. `python3 tools/check-hamoa-acpi.py downloads/sp11/x2-run10 --descriptor build/sp11-m1-descriptor.json` prints `descriptor boot gate : ready` and then `REFUSED: ecam-mismatch` (VERIFIED, host); the tests pin it on synthetic tables. `real` does not apply that rule (section 3.1), so the builder accepts the file.
- While the list is empty no table can satisfy it: an MCFG with no allocation is `table-structure`. A list of four or fewer windows (`MAX_ECAM`) cannot equal the unit's eight either, and one of them lies above the 2^36 bound. D11 proper, the rule and those two limits, is the operator's, left for later; it must be decided before any use of `from_hamoa` or `check_hamoa_platform` on this board and before the guest is given a config window.
- N2, the five device states, is settled for M1 by withholding. It stays open for the Surface: X6 measures the NVMe window (`docs/SP11-NVME-WINDOW.md` section 7.1) and needs a config window handed to the guest, so a non-empty ECAM list and D11 proper; the input study (`docs/SP11-INPUT-STUDY.md`) picks the keyboard and touchpad paths; the touchscreen and the debug UART have no established way either. When a device is given to the guest its state and window change, the tool's `ABSENT` list and its tests change with it, and the operator decides again.
- Not reconciled, and unchanged: the IORT's model field of both SMMUv1/2 nodes is 3 in every dump while the template writes kind `mmu-500` from the Linux device tree, so the SMMU kinds stay INFERRED.

## 4. What the first real run found

| Finding | Label | Fix |
| --- | --- | --- |
| The loader's `print` lines went to standard output. `authorisation-template real` printed four of them before the record, so a saved record file was not the record and `--authorisation FILE` would have been `AUTH_RECORD_MISMATCH`; the lines also carry Apple-derived layout facts | VERIFIED | `real_built` captures the loader's standard output; the record is the record alone (a test pins it) |
| `build()` draws a fresh random boot seed per call (`secrets`), so two builds of the same inputs differed and the record's GUEST.DAT digest could never be regenerated: `real` could not have succeeded with an approved record | VERIFIED | `real` sets `SNAP_SEED` from `--seed` (default `sp11-stick-bundle`); the same label, the same bytes. Only the handoff chunk holds the seed (a test pins it). An inherited `SNAP_SEED` is refused like every `SNAP_*` |
| `probes/run.py` checks, before an upload, that no APF device slice of the guest's tree overlaps owned memory; `real` did not | VERIFIED | `real_built` runs the loader's `validate_apf_memory` against the stage-2 ranges and the work area (it passes for the bank, panel handed or not); an overlap is `BUILT_SHAPE` and the node is not quoted |
| `build()` refusals (a firmware set that is not the pinned one, a damaged or truncated kernelcache) are `BUILT_SHAPE`; a missing file is `KERNELCACHE_MISSING` | VERIFIED | no new refusal name |

The kernelcache is read from `downloads/extracted/j700ap/` only; DeviceTree, SPTM and TXM have no argument. Nothing else needed a change: `chunks_from_built` classified every chunk of the real `built` and the table pages came only from the tables. `real_built` and `place_layout` now live in `tools/sp11_stick_bundle_chunks.py`, beside `synthetic_built`, with the descriptor comparison of the facts: the builder had reached its 50 KiB limit (it keeps aliases). The authorisation record's text then moved to `tools/sp11_stick_bundle_record.py` (section 7), which left the builder at 43,300 bytes.

## 5. What is in the guest

| Step of `build()` | A16 path | `real` | Label |
| --- | --- | --- | --- |
| Chained-fixup rebase of the kernelcache (pointer resolution, always) | applies | applies; the only change to stock bytes with `--sptm-hvc-calls 0` | VERIFIED |
| Native-call admission (`SNAP_NATIVE_CALLS`, no byte changes in stock mode) | opt-in | always on, as the A16 bootstrap smoke | VERIFIED |
| SPTM HVC call patch (`SNAP_SPTM_HVC_CALLS`, replaces 348 private call words) | opt-in | only with `--sptm-hvc-calls 1` (D9) | VERIFIED |
| Kernel sysreg-zero, refmod-skip, user-4K, hide-AMX | opt-in by variable | never: any inherited `SNAP_*` is `ENV_SNAP_SET` | VERIFIED |

The test compares every segment of the guest with the kernelcache after the rebase: 0 words differ with the patch off, exactly the 348 listed call words with it on.

## 6. The first monitor run and `build()`'s options

`real` calls `build()` with its defaults, as the A16 bootstrap smoke recipe does (`tools/cycle_lib/bootstrap_smoke.py`: profile `j700ap-26A428`, no Adreno, no ANS, no installed root).

| Option | First monitor run | Label |
| --- | --- | --- |
| RAM disk, root hash, ISC image, RAM disk mode | not needed: M1 has no root device | VERIFIED |
| AuxKC, trust additions | not applicable: the profile links no AuxKC and pins the plain Apple trust cache, so `build()` would refuse either | VERIFIED |
| Guest profile other than the default | not needed | INFERRED |
| Sysreg-zero, refmod-skip, user-4K, hide-AMX | not used by the smoke recipe; whether the first guest on this core needs one: settled by the first stage-2 fault or trap, with its IPA | UNKNOWN |
| Native calls on, HVC patch 0 or 1, panel handed or withheld | native calls always on; D9 is decided (2026-11-16): panel withheld and `--sptm-hvc-calls 0`, a stock kernel; the other three combinations stay buildable (the console one ran in boot 21: D9, 2026-11-18, and with `cpus=1` in boot 23). With the panel handed a stock kernel got as far as a stage-2 fault with an IPA, one vCPU, 2.04 s (boot 21: OBSERVED, one boot), and with `cpus=1` to an exception at EL1 that the monitor stopped on, one vCPU, 2.17 s (boot 23: OBSERVED, one boot; INFERRED to be the kernel's own debugger trap); withheld, it was, INFERRED, stuck in a loop (boot 20) | OBSERVED / INFERRED |
| Boot seed | the fixed label `sp11-stick-bundle` is the tool's default (N1), not an operator decision; entropy drawn at run time is UNIMPLEMENTED in the wrapper, and whether the first run needs it is settled only by a run | UNKNOWN |
| Console command line | the loader's default, kept; the panel photograph shows whether it suffices | UNKNOWN |

## 7. Decisions and what stays open

- D9 (panel, HVC patch): decided by the operator on 2026-11-16: panel withheld, stock kernel, `--sptm-hvc-calls 0`; its GUEST.DAT is 9e69e7b5612802f00948465fb620e879d36c9b1ac419be1b9cf079dcda463c15. The other three combinations stay buildable and are different files; building one is a new decision.
- D11 (the ECAM rule): D11 proper is left for later (section 3.5). The guest view lists no window, so `real` needs no decision on it, and the adapter refuses the view at `ecam-mismatch`.
- D5 (time box): decided on 2026-11-16: 5 s, `--time-box-s 5`, carried in PARAMS as `watchdog_seconds`.
- D2 (the stop): decided on 2026-11-16: the GIC-timer stop. The run used `--stores ram,el2_sysreg,gic`; the record's text for those classes and the registers they name are the GIC stop work's to settle (WI 4b, WI 6).
- N1 (new): the boot seed. The fixed label `sp11-stick-bundle` is this tool's default so that the record can name GUEST.DAT; it is a default, not an operator decision. Entropy at run time is UNIMPLEMENTED.
- N2 (new): the five device states are settled for M1 by withholding (section 3.3) and open for the Surface itself (section 3.5).
- The authorisation record of a `real` build says what the build is. `tools/sp11_stick_bundle_record.py` holds the record text (the builder imports it); for kind `real` it adds a section 1a between sections 1 and 2, so that the digest the operator approves also covers the kernelcache (guest profile and SHA-256), the D9 choices as used and not as decided (panel handed or withheld; SPTM HVC call patch 0, a stock kernel, or 1), the boot seed label (a default, not an operator decision, when it is `sp11-stick-bundle`; boot entropy UNIMPLEMENTED) and the descriptor (SHA-256, board id, status), with `guest view: pcie.ecam empty; D11 proper open` when it is EXPERIMENTAL and lists no config window. Another kernelcache, choice, seed or descriptor is another record: a record approved for one descriptor no longer matches a build with another (`AUTH_RECORD_MISMATCH`). A synthetic record has no section 1a and is byte for byte what it was before. `build-report.json` names the descriptor PIN.DAT pins (`descriptor_sha256`, `board_id`, `board_status`; null for a synthetic build without one) and, for `real`, the `real` section. As printed for the final choices of section 2:

```
1a. What this build is (the host-side real path of docs/SP11-STICK-REAL.md: GUEST.DAT is made from a kernelcache)
   kernelcache  guest profile j700ap-26A428  sha256 dc9019ae39187b36936c2c513b485e513f15cd966b5c887ac88ffd2925976c62
   D9 as built  panel withheld: the guest is given no framebuffer
                SPTM HVC call patch 0: a stock kernel
   boot seed    label sp11-stick-bundle (a default, not an operator decision; boot entropy UNIMPLEMENTED)
   descriptor   sha256 310cf3974326aa223aa5405395071d9b76673545d42ca191cebc7c437c581ed7 (PIN.DAT descriptor_sha256)
                board sp11  status EXPERIMENTAL
                guest view: pcie.ecam empty; D11 proper open
```

## 8. Commands

```sh
export Q1N1_SP11_TREE=<q1n1 checkout with tools/sp11_payload_manifest.py>
python3 tools/sp11_m1_descriptor.py derive --facts build/sp11-facts.local.json --out build/sp11-m1-descriptor.json
python3 tools/sp11_m1_descriptor.py check --descriptor build/sp11-m1-descriptor.json --facts build/sp11-facts.local.json
python3 tools/board_descriptor.py check build/sp11-m1-descriptor.json --boot-ready
python3 tools/check-hamoa-acpi.py downloads/sp11/x2-run10 --descriptor build/sp11-m1-descriptor.json     # REFUSED: ecam-mismatch, by design (3.5)
ARGS="--facts build/sp11-facts.local.json --base 0x880000000 --guest-mib 384 --monitor <SNAPMON.BIN> --reporter <SNAPREP.BIN> \
      --stores ram,el2_sysreg,gic --time-box-s 5 --kernelcache downloads/extracted/j700ap/kernelcache.release.mac17p \
      --descriptor build/sp11-m1-descriptor.json --panel withheld --sptm-hvc-calls 0"
python3 tools/sp11_stick_bundle_build.py place-check --base 0x880000000 --guest-mib 384 --facts build/sp11-facts.local.json
python3 tools/sp11_stick_bundle_build.py authorisation-template real $ARGS > build/sp11-record.txt
python3 tools/sp11_stick_bundle_build.py real $ARGS --out build/sp11-bundle-real --authorisation build/sp11-record.txt \
    --base-tree <staged base tree> --file SLBOUNCE-DEVHE-MARK.EFI <build> <sha256> --file Q1N1-PAYLOAD.EFI <image> <sha256>
python3 tools/stage-sp11-boot-media.py verify --dest build/sp11-bundle-real/stage
python3 probes/xnuboot.py --check
```

The final run of section 2 used the dry run's SNAPMON.BIN, SNAPREP.BIN, staged base tree and two EFI images (all placeholders, under `build/sp11-dryrun/`). The options are those of [SP11-STICK-BUILD.md](SP11-STICK-BUILD.md) sections 3 and 7, plus `--seed` (default label). No `SNAP_*` variable may be set. `derive` never overwrites: name a new file, or remove the old one yourself.

## 9. Tests

`python3 tools/test-sp11-stick-bundle-real.py`: `Pipeline` (the loader driven in the order of `probes/run.py` for the four D9 choices; the record alone on stdout; byte-identical rebuilds; the seed; section 1a of the record: the kernelcache, each D9 choice, the seed line, the descriptor and the guest view, an approval that covers the descriptor, no line in a synthetic record; the report's descriptor fields; every loader refusal named; the APF rule; the cheap refusals, `DESCRIPTOR_NOT_READY`, `FACTS_DISAGREE`, `OPTIONS_BAD`, `PLACE_DECLARED`, `KERNELCACHE_MISSING` and `ENV_SNAP_SET`, all before the loader is asked to build), `Refusals` (a descriptor refused for each field `require_boot_ready` names, each device state, each pinned fact; the builder never applies the adapter's rule), `Kernelcache` (the real build for four D9 choices, the unmodified-kernelcache invariant, determinism and the seed, the A16 `--check` at the bank base, damaged kernelcaches, the CLI, the C twin, the stage tree) and `Hygiene` and `Document`. The `Kernelcache` tests SKIP without the boot set; a skip is not a pass. `tools/monitor_baseline.py` builds the monitor from `probes/`, `compat/`, `shadow/` and `config/monitor-flags.json`; no file this work touches is among them. `tools/test-sp11-stick-bundle-record.py` pins the text of the synthetic records by digest (four fixed inputs), so that neither its move to its own module nor the lines of a real build changed one.

`python3 tools/test-sp11-m1-descriptor.py` (no Apple input, scratch under `build/`): `Derive` (boot-ready and accepted by the builder's own gate; the four unit fields and the three decisions; only the named claims differ from the template; the provenance words; two derivations byte-identical, one in another process under another hash seed; the template untouched and still refused), `Policy` (each policy field altered in turn and refused by the right layer: a device back to unknown or present without a window, status UNIMPLEMENTED, an unknown ECAM and a duplicate segment by the board's own rules; a non-empty ECAM, another GIC version, bank, template claim or provenance, or a formatting change by the comparison with the derivation; a wrong MIDR, GICD, ITS or timer pin by the facts), `Inputs` (a promoted template, one more unresolved device, facts against a dump and against the template, a GIC version the MADT does not state or the facts contradict, banks that are not one conventional write-back descriptor alike in every dump), `Output` (never overwrites; below `build/` only unless allowed, no `..`, no link, `/Volumes` and `/dev` by spelling), `Adapter` (`check_hamoa_platform` and `tools/check-hamoa-acpi.py` refuse the view at `ecam-mismatch` and only there: the same descriptor with the tables' one allocation listed is accepted; no table satisfies an empty list) and `Hygiene`. A spot check with 20 in-memory mutants of the tool (a dropped guard or gate, a widened mask, an overwrite, an unsorted dump, a removed comparison, ...) left none alive; the first round left one, a redundant stride comparison that a direct test of `gic_version` now covers.

## 10. How this connects

- WI 4b (the wrapper with the monitor entry) reads this GUEST.DAT: the chunk table, PARAMS at base + 0xC000 as x3, the watchdog as the time box. The file is 118.9 MB, smaller than the dry run's 144.7 MB, so the runner's placement of it (top of the low extent, INFERRED) and the copy and hash timings differ; give `--declared` the base a boot reports.
- WI 6 (authorisation package) names GUEST.DAT's digest and, for a real build, what it is made from (section 7). The monitor payload is not named yet.
- WI 11 (the M1 run) needs, from this item: a boot-ready descriptor (done, as a guest view: section 3), the D9 choice (decided) and a monitor; the record now says what the build is (section 7) and the bundle builder is no longer the blocker. M1 needs neither D11 proper nor N2 for the Surface; both come due when a config window or a device is handed to the guest (X6) and for any use of `from_hamoa`.
