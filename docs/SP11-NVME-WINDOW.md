# SP11 NVMe window: audit, safety contract and per-machine questions

**Status: EXPERIMENTAL (host-tested only). Hardware status: NOT RUN.** Nothing here has touched a Surface Pro. This
document is the evidence study for WS-D items S2 and S3 of [SURFACE-PRO-11.md](SURFACE-PRO-11.md) (section 7) and
Appendix D of that plan. It records what the A16 NVMe takeover assumes, what the SP11-gated code in
`compat/nvme-host.{c,h}` now refuses, what is still unknown, and exactly what must be measured at X2 and X6 to close
each question. It authorizes no hardware operation (see [AGENTS.md](../AGENTS.md), section 5): every measurement below
is a plan for a later, supervised and explicitly authorized session.

Labels are the ones in [SURFACE-PRO-11.md](SURFACE-PRO-11.md#reading-this-document): VERIFIED (read in this tree or a
pinned public source; never "ran on a Surface"), OBSERVED, INFERRED, UNKNOWN, EXPERIMENTAL, UNIMPLEMENTED.

## 1. What exists, and who does what

| Piece | Where | Status |
| --- | --- | --- |
| q1n1 takes the controller over after ExitBootServices (shutdown, reset, its own admin queue, Identify, I/O queue 1, GPT read, window arm) and leaves it running | q1n1 `tools/nvme_m3.py` (Mac host over the proxy; A16 only) | A16 VERIFIED (q1n1 `docs/A16-NVME-M3.md`); SP11 UNIMPLEMENTED |
| The monitor adopts the running controller, re-proves its registers, and serves the window through I/O queue 1 | `compat/nvme-host.{c,h}`, `compat/ans-window.{c,h}` | A16 qualified; unchanged by this work |
| SP11 takeover contract: descriptor and record cross-checks, window geometry, endpoint re-proof, admin and I/O allow-lists | `compat/nvme-host.{c,h}`, only under `-DSNAP_PLATFORM_SP11=1` | EXPERIMENTAL; no caller yet: until it has its inputs the monitor's adoption entry refuses with `NVMEH_SP11_STATUS_UNWIRED` (-145), see section 2 |
| Host model, GPT layout schema and loader, fixtures, differential tests | `tools/fixtures/sp11/`, `tools/test-nvme-window-hamoa.{py,c}` | EXPERIMENTAL |

The monitor never programs the PCIe controller (PARF, DBI, ELBI, ATU) or the SMMU. It reads the endpoint's
configuration header through the ECAM window and talks to BAR0. The Hamoa controller addresses (`pcie6a` PARF
`0x01bf8000`, DBI `0x70000000`, config window `0x70100000`; `pcie_smmu` `0x15400000`) are reference evidence for the
ACPI cross-checks and are not used by any code ([section 3](#3-audit-of-the-a16-assumptions)).

## 2. The SP11 contract in compat/nvme-host.c

Everything is compiled only under `SNAP_PLATFORM_SP11`. The default A16 build preprocesses without it
(`test_default_preprocessing_has_no_sp11_text`) and its ELF is byte-identical (the firewall,
[section 8](#8-validation-and-what-it-does-not-show)). `nvmeh_adopt()` and every A16 function are unchanged.

| Function | What it does |
| --- | --- |
| `nvmeh_sp11_check(target, record, params)` | Pure. Compares the descriptor-derived target, the adoption record and the A16 params, and checks the window against the namespace and GPT geometry. One distinct refusal code per cause. |
| `nvmeh_sp11_prove_endpoint(ops, target, params)` | Reads the endpoint's config header at `ecam_base` plus the pinned BDF's ECAM offset, 32-bit reads only, and requires an NVM Express endpoint with Memory Space and Bus Master on and BAR0 equal to `params.bar`. Never writes config space. |
| `nvmeh_sp11_adopt(...)` | check, then prove, then the unchanged A16 `nvmeh_adopt`. A refusal is stored in `state.prepare`, so the monitor stays "not adopted" and every later read, write and flush returns `NVMEH_E_DEAD` before any doorbell. |
| `nvmeh_sp11_target_from_board(blob, segment, target)` | ECAM window for `segment` and the one SMMUv3 from a validated board-info blob; requires `nvme` present. BDF, stream map, namespace identity and run generation stay unknown. |
| `nvmeh_sp11_admin_verdict(phase, opcode, cdw10)`, `nvmeh_sp11_io_verdict(opcode, writable)` | The gate any physical admin or I/O command must pass ([section 2.2](#22-admin-and-io-allow-lists)). The monitor issues no admin command today. |

**The monitor's adoption entry.** Nothing calls `nvmeh_sp11_adopt` yet, so on an SP11 build the monitor does not adopt the SSD at
all: `ans_window_prepare` (`probes/xnuboot/ans_glue.c`) returns `NVMEH_SP11_STATUS_UNWIRED` (-145) and stores it in `state.prepare`
whenever the launch asks for the SSD window, instead of calling the A16 `nvmeh_adopt`, which re-proves the controller registers
and nothing else and would adopt a disk the descriptor never named. The state stays "not adopted", so a later read, write or
flush returns `NVMEH_E_DEAD` before any doorbell. The A16 side of that function is unchanged. Replacing the refusal with a call
needs the target, the adoption record and the params to reach the monitor, and the descriptor has no segment, BDF or stream
field yet ([section 4](#4-proposed-descriptor-and-board-info-additions)); the refusal is never to be replaced by `nvmeh_adopt`.
Host decoding: `probes/run.py` reserves -96 to -145 for this contract, names every code with the text of `nvmeh_sp11_strerror`
(`tools/test-sp11-integration.py`, class `StatusBands`, holds them equal), and also names -83, the A16 `NVMEH_E_QUEUES` that
`nvmeh_adopt` returns through the same entry. The entry is held by `tools/test-monitor-hardening.py` (class
`NvmeWindowWiring`: the SP11 object never references `nvmeh_adopt`, the A16 object still does).

### 2.1 What a record must prove (check order)

First failure wins; the order is part of the contract and both implementations are held to it.

1. ABI revision 1 on both blocks; no reserved word, unknown `known` bit or unknown flag.
2. Every descriptor group measured: ECAM, BDF, SMMU, stream map, namespace EUI64, run generation. An unmeasured group
   refuses with its own code (`UNKNOWN_*`), never a default.
3. The pins are possible: 16-bit segment and RID, buses 0 to 255, ECAM base 1 MiB aligned and its whole window and the
   SMMU pages below the monitor's 36-bit map, 32-bit StreamIDs, nonzero EUI64 and generation.
4. The hand-off was clean, and the record's generation equals the run's (`STALE`).
5. Identity: same segment, ECAM base, bus range, BDF and config address; class code `0x010802`; same EUI64 (a swapped
   disk is refused); same SMMU base; SMMU mode bypass or identity stage 2 with the DMA window proven; StreamID equal to
   the descriptor's map applied to the RID.
6. LBA format 512 bytes, no metadata, same in the params. A 4096-byte format is refused, never converted.
7. Geometry: namespace size and GPT usable range possible (usable LBAs lie in `[34, nsze - 34]` at the least); between 1
   and 32 protected extents (an empty list is a survey that never ran), each inside the usable range.
8. Window `[first_lba, last_lba]`, in 512-byte LBAs: not empty or wrapped; at least 2 MiB; inside the namespace; outside
   both GPT zones; sharing no LBA with any protected extent (reported before alignment, so a one-block straddle is an
   overlap); start and size whole MiB; one MiB of guard to the usable edges and to every other extent.
9. Write policy: write words must be zero without `NVMEH_F_WRITE`; with it the record must carry the per-run
   authorization, and the write window must lie strictly inside the window, one MiB in from each end (the first MiB holds
   the window header and the synthesized GPT; the last MiB the backup GPT).

The window rules reuse the A16 plan's conventions: 1 MiB alignment and guard (`tools/snapmac-partition-plan.py`,
`GUARD_LBAS` and the guard and alignment check in `plan`), whole-MiB windows (`probes/answindow.py:53`), and the virtual ANS2's synthesized first and last MiB
(`compat/ans.h:54`). Minimum window size, the guard and the edge MiB are policy of this contract, not hardware facts.

### 2.2 Admin and I/O allow-lists

Default deny. Opcodes and identifiers are the NVMe base specification's, VERIFIED as read in Linux
`include/linux/nvme.h` at `7475ea8b58616b33c04afb976cf0fbaad9dd63fb` (admin opcodes `:1306-1335`, Identify CNS `:561-562`,
features `:1375-1396`, log pages `:1406-1408`, I/O opcodes `:956-974`, FUA `:1120`).

| Command | Takeover phase (q1n1) | Adopted phase (monitor) |
| --- | --- | --- |
| Identify | CNS 0 (namespace), 1 (controller) | same |
| Get Log Page | log 1 (error), 2 (SMART), 3 (firmware slot) | same |
| Get Features | features `0x01..0x14`, `0x16`, `0x17` | same |
| Set Features | Number of Queues (`0x07`), no Save bit | refused |
| Create/Delete I/O SQ and CQ | allowed | refused |
| Format NVM, Sanitize, Firmware Commit and Image Download, Namespace Management and Attachment, Security Send and Receive, Device Self-test, Directives, Virtualization, Doorbell Buffer Config, Abort, AER, Keep Alive, Get LBA Status, vendor opcodes, anything unknown | refused | refused |
| I/O | Read, Flush; Write only when the window is writable | same |

The takeover column equals the A16 tool's lists (`nvme_m3.py:56-60`) and the host test compares them
(`test_the_lists_equal_the_a16_takeover_tool_s`). Format NVM is the only command that switches the LBA format, so the LBA
format is never switched by construction. Set Features of anything that describes geometry (for example LBA Range
Type, `0x03`) is refused. Get Features and Get Log Page cannot change the media.

## 3. Audit of the A16 assumptions

"Where" cites this tree, or the pinned q1n1 (`7872398`, read-only) as `q1n1 path`. "Field" is the descriptor field that
carries the value on the SP11, or the proposal in [section 4](#4-proposed-descriptor-and-board-info-additions).

| # | A16 assumption | Where | On the SP11 | Field |
| --- | --- | --- | --- | --- |
| 1 | PCI segment 5 (`Acpi(PNP0A08, 5)`) | q1n1 `tools/nvme_m3.py:49` | Segment of the SSD's root complex is unknown. The pinned device tree puts the NVMe supply and slot on `pcie6a` (`linux,pci-domain = <6>` is Linux's number, not an ACPI segment). | `storage.nvme.segment` (proposed) |
| 2 | ECAM from the ACPI MCFG at run time, never a constant | q1n1 `tools/nvme_m3.py:182-190` | Same method; MCFG not yet dumped. | `pcie.ecam` (exists, null in `sp11.json`) and the A5 cross-check |
| 3 | Root port at 0:0.0, endpoint on its secondary bus at device 0 function 0 | q1n1 `tools/nvme_m3.py:192-215` | The device tree has one root port at device 0 (`hamoa.dtsi:3526-3529`). The bus number is firmware's. | `storage.nvme.endpoint` (proposed); re-proved by `nvmeh_sp11_prove_endpoint` |
| 4 | Vendor `0x144D`, class `0x010802`, the A16 disk's EUI64 | q1n1 `tools/nvme_m3.py:51-53`; `tools/snapmac-partition-plan.py` (the A16 pins `DISK_BYTES`, `DISK_GUID` and `EUI`); `tools/qualify-nvme-writeback.py:75-76` | Class kept (`NVMEH_SP11_CLASS_NVME`). Vendor is not pinned. EUI64 is a per-run pin, never a constant. The A16 qualifier and plan cannot be reused as written. | per-run pins; `nvmeh_sp11_target.eui64` |
| 5 | SMMUv3 at `0x15480000` | q1n1 `tools/nvme_m3.py:50` | `pcie_smmu` is `arm,smmu-v3` at `0x15400000` (`hamoa.dtsi:8544-8546`). | `smmu[]` kind `smmu-v3` (exists) |
| 6 | StreamID = `segment << 16 \| RID` | q1n1 `tools/nvme_m3.py:214-215` | The EL2 overlay maps `pcie6a` RIDs to StreamID `0x60000 + RID` (`x1-el2.dtso:47-49`), for Linux at EL2; the firmware's own map is UNKNOWN. | `storage.nvme.stream` (proposed) |
| 7 | The SMMU is already bypass or an identity stage 2 that q1n1 may only read | q1n1 `tools/nvme_m3.py:220-257,704` | The SMMU is "Controlled by Gunyah" (`hamoa.dtsi:8555`) and an EL2 owner must configure it itself (`x1-el2.dtso:25-27`). State at hand-off UNKNOWN. | record `smmu_mode`, `DMA_PROVEN`; see D7 |
| 8 | ANS register offsets | `compat/ans/nvme_model.c` (doorbells `0x2490c/0x24910`, boot status `0x1300`) | Those are the virtual ANS2 the guest sees, not hardware. The physical side is plain NVMe: CAP, CC, CSTS, AQA, ASQ, ACQ and doorbells at `0x1000 + (2q + k) * (4 << DSTRD)` (`compat/nvme-host.c:78`). q1n1 `src/nvme.c` is the Apple ANS driver and is not on this path. | none |
| 9 | Doorbell stride from CAP.DSTRD | `compat/nvme-host.c:48` (at most 4); q1n1 `tools/nvme_m3.py` takeover | Generic. | none |
| 10 | Polled, no interrupts (Create I/O CQ without IEN) | `compat/nvme-host.h:13` | No GIC, ITS or MSI dependency; the "ITS emulation seems broken" note (`x1-el2.dtso:29-31`) does not apply. | none |
| 11 | Reset: CC.SHN normal, CC.EN 0, own admin queue, CC.EN 1; needs MPSMIN at most 4 KiB, NVM command set, MQES at least 64 | q1n1 `tools/nvme_m3.py:375-407` | Generic NVMe. Its effect on a Windows boot is D11. | none |
| 12 | 512-byte LBAs; 4 KiB format exists and is never selected | `compat/nvme-host.c:50`; `compat/ans-window.c:11,36-37,52-53,70-71,106-107` (`ANS_BLOCK / 512`); `probes/answindow.py:119-120`; q1n1 `docs/A16-NVME-M3.md` | INFERRED 512 until X6. A 4096-byte format is refused with `LBA_FORMAT`; supporting it needs the window arithmetic generalized, which is out of scope. | measured, in the record |
| 13 | Queues and transfers: admin 32 entries, I/O queue 64, 1 MiB DMA window, 128 KiB data buffer, one PRP list page | `compat/nvme-host.h:25-34` | Unchanged. MDTS and CAP.MQES come from Identify and CAP; the producer sets `max_transfer` from MDTS, and the monitor refuses a larger transfer before a doorbell (`test_transfer_limit_and_data_buffer_bounds_hold`). | none |
| 14 | DMA window inside the monitor's reservation, in Conventional memory, identity-mapped read/write for the SSD | `compat/ans.h:51`; `probes/answindow.py:108`; q1n1 `tools/nvme_m3.py:307-322` | DRAM map UNKNOWN (X2 item 8). All addresses must stay below `1 << 36`. | `ranges.dram0` (exists, null) |
| 15 | Window policy: whole MiB, at least 1 GiB, 1 MiB guard, synthesized edge MiB | `probes/answindow.py:53`; `tools/snapmac-partition-plan.py` (`GUARD_LBAS`; the guard and alignment check in `plan`); `compat/ans.h:54` | Reused as `NVMEH_SP11_ALIGN_LBAS`, `GUARD_LBAS`, `EDGE_LBAS`. The 1 GiB minimum stays the planner's. | none |
| 16 | The producer of the adoption record | `probes/run.py`, the `ans_window` block (q1n1 `nvme_m3` adopt, then `answindow.nvmeh_params`) | No producer of the SP11 target and record exists. | UNIMPLEMENTED |
| 17 | The monitor's adoption entry | `probes/xnuboot/ans_glue.c`, `ans_window_prepare` | An SP11 build refuses with `NVMEH_SP11_STATUS_UNWIRED` instead of calling the A16 `nvmeh_adopt`; it must call `nvmeh_sp11_adopt` once the target, record and params reach the monitor. | WIRING (UNIMPLEMENTED); the refusal is host-tested |

The A16 path is not entangled: `nvme-host.c` is already generic. The A16 assumptions live in q1n1's Python and in the
host helpers, so nothing in the A16 code had to change.

## 4. Proposed descriptor and board-info additions

The shipped schema v1 has `pcie.ecam`, `smmu[]` and `devices.nvme`, but no field for the SSD's segment, BDF or stream
map. Descriptor files belong to platform-gate; this is a proposal, not an edit. Until it exists
`nvmeh_sp11_check` refuses with `UNKNOWN_BDF` / `UNKNOWN_STREAM`, which is the intended fail-closed state.

```json
"storage": {
  "nvme": {
    "segment": null,
    "endpoint": null,
    "smmu": null,
    "stream": null
  }
}
```

| Field | Value once measured | Label |
| --- | --- | --- |
| `segment` | the `pcie.ecam[].segment` that hosts the SSD | UNKNOWN until X2 |
| `endpoint` | `{"bus": 1, "device": 0, "function": 0}` (INFERRED from the A16 and the device tree) | UNKNOWN until X2 |
| `smmu` | the `smmu[].name` that serves it, so ambiguity is stated, not guessed | UNKNOWN until X2 |
| `stream` | `{"rid_base": "0x0", "rid_count": "0x10000", "stream_base": "0x60000"}` (the EL2 overlay's map, INFERRED to hold under the firmware) | UNKNOWN until X2 |

Each field needs provenance like every other claim (a null must be `UNKNOWN`/`UNIMPLEMENTED`). Board-info ABI 1.0 has no
room for it: a minor bump would add a `KNOWN_STORAGE` bit and a 24-byte group, appended after the 1.0 layout. The namespace
EUI64 and the run generation are not board facts; they belong in the per-run pins and the record.

## 5. GPT layout schema and fixtures

`tools/fixtures/sp11/gptlayout.py` documents schema v1 in its docstring and loads, validates and queries layouts:

```sh
python3 tools/fixtures/sp11/gptlayout.py check LAYOUT.json FIRST_LBA LAST_LBA [--partition-guid GUID]
python3 tools/fixtures/sp11/gptlayout.py from-survey SURVEY.json [--disk N]      # JSON to stdout, sanitize before use
```

The loader refuses unknown keys, duplicate JSON keys, NaN, floats, booleans and strings where integers belong, headers
that do not point at each other, an entry array under 16,384 bytes, a usable range overlapping an array, partitions
outside the usable range or overlapping, duplicate, all-zero or non-canonical GUIDs, and indexes outside the array
(`test_malformed_layouts_are_refused`, `test_json_text_refusals`). The key names are the ones `q1n1/tools/gpt_survey.py
--json` already emits, so a sanitized survey disk entry converts with `from-survey`
(`test_a_survey_disk_entry_converts_and_a_flagged_one_is_refused`).

Fixtures are synthetic stand-ins for a typical 1 TB Surface disk; every GUID and name is made up:

| File | Layout |
| --- | --- |
| `gpt-512-shrunk.json` | 512-byte LBAs: ESP 260 MiB, reserved 16 MiB, Windows 640 GiB, then about 290 GiB free, then recovery 1200 MiB (what Windows' own shrink leaves) |
| `gpt-512-free-at-end.json` | the same partitions with recovery right after Windows and the free space to the end |
| `gpt-4096-shrunk.json` | the 4096-byte-LBA twin of the first, same byte offsets |

A real sanitized layout of the same schema replaces them; the tests read the fixtures through the loader and the
verdict tables are literals in `tools/fixtures/sp11/nvmewindow.py`, so a replacement needs the tables' LBAs updated, not
the logic.

## 6. Safety invariants and the tests that enforce them

"Model" is the Python reference; "C" is `compat/nvme-host.c` built with ASan and UBSan. Every row runs both unless
noted. A host test is not hardware evidence.

| # | Invariant | Enforced by |
| --- | --- | --- |
| I1 | A window never shares an LBA with another partition, including by one block | `test_expected_verdicts_model_and_c`, `test_layout_model_monitor_model_and_c_agree_on_every_boundary`, `test_all_accepted_windows_stay_inside_the_free_extent_on_the_fake_controller` |
| I2 | A window never reaches the primary or backup GPT zones | `test_expected_verdicts_model_and_c`, `test_layout_b_windows_model_c_and_generic_agree` |
| I3 | Zero size, wrapped size and a start beyond the disk end are refused | `test_expected_verdicts_model_and_c`, `test_random_corruption_model_equals_c` |
| I4 | Windows are whole MiB, with a 1 MiB guard to every extent and to the usable edges (counted in blocks, so a neighbour off the MiB grid still gets the full guard), at least 2 MiB, in bytes whatever the LBA size | `test_expected_verdicts_model_and_c`, `test_neighbours_off_the_mib_grid_still_get_a_full_guard`, `test_the_4096_layout_uses_bytes_for_alignment_and_guard`, `test_the_guard_to_the_usable_edges_is_exact` |
| I5 | Only 512-byte LBAs are accepted; 4096 is refused, never converted | `test_the_monitor_refuses_a_4096_byte_disk_whatever_the_window` |
| I6 | The LBA format is never switched; Identify supplies the current format only | `test_identify_supplies_the_current_format_and_never_a_better_one`, `test_format_facts_flow_into_the_verdict`, `test_written_out_verdicts_model_and_c` |
| I7 | Identify is read-only; the adopted monitor can send only Identify, Get Log Page and Get Features | `test_identify_is_read_only`, `test_model_equals_c_on_every_opcode_phase_and_probe` |
| I8 | Admin commands are default-deny; Format NVM, Sanitize, Firmware Commit, Namespace Management and unknown opcodes are refused; the lists equal the A16 tool's | `test_written_out_verdicts_model_and_c`, `test_the_lists_equal_the_a16_takeover_tool_s` |
| I9 | I/O is Read, Flush, and Write only when writable | `test_io_verdicts_model_and_c`, `test_a_read_only_window_is_confined` |
| I10 | Reads and writes outside the window are refused before any doorbell, and so are transfers over the controller's limit and data offsets outside the 128 KiB buffer | `test_a_read_only_window_is_confined`, `test_a_writable_window_never_reaches_an_edge_megabyte`, `test_transfer_limit_and_data_buffer_bounds_hold` |
| I11 | A write while the window is read-only is refused; write words without the flag are refused | `test_a_read_only_window_is_confined`, `test_expected_verdicts_model_and_c` |
| I12 | Writes need the per-run authorization and stay one MiB inside the window | `test_a_writable_window_never_reaches_an_edge_megabyte`, `test_expected_verdicts_model_and_c` |
| I13 | A record from another run (stale) is refused | `test_expected_verdicts_model_and_c`, `test_every_refusal_sends_nothing_and_leaves_the_controller_dead_to_the_monitor` |
| I14 | A record whose BDF, segment, ECAM, config address or SMMU differs from the descriptor's is refused | `test_expected_verdicts_model_and_c`, `test_reads_land_only_in_the_configuration_header_of_the_pinned_bdf` |
| I15 | A disk other than the pinned namespace is refused | `test_expected_verdicts_model_and_c` |
| I16 | Any group the descriptor leaves unknown refuses with a reason of its own | `test_expected_verdicts_model_and_c`, `test_the_shipped_sp11_template_cannot_become_a_target`, `test_pins_the_descriptor_cannot_carry_refuse_until_supplied`, `test_model_and_c_agree_and_leave_the_rest_unknown` |
| I17 | The endpoint is re-proven from config space, reads touch only the pinned header | `test_config_space_proof_model_and_c`, `test_endpoint_refusals_stop_the_adoption_before_the_controller` |
| I18 | A refused adoption leaves the monitor unable to send a command | `test_every_refusal_sends_nothing_and_leaves_the_controller_dead_to_the_monitor` |
| I19 | The A16 controller re-proof still runs after the SP11 checks | `test_the_a16_adoption_checks_still_run_after_the_sp11_ones` |
| I20 | The C code and the model give one answer on every case and on 9,000 random corruptions | `test_random_corruption_model_equals_c`, `test_layout_model_monitor_model_and_c_agree_on_every_boundary`, `test_code_table_is_complete`, `test_every_refusal_reason_of_the_check_is_exercised` |
| I21 | Malformed GPT layouts are refused by the loader | `test_malformed_layouts_are_refused`, `test_json_text_refusals`, `test_informational_keys_are_accepted_only_when_consistent` |
| I22 | The SP11 code is absent from the default build, refuses a conflicting gate, and the monitor object needs no libc and holds no state | `test_default_preprocessing_has_no_sp11_text`, `test_gate_conflicts_are_refused`, `test_the_monitor_object_has_no_libc_and_no_state`; the A16 ELF by `tools/monitor_baseline.py compare` |
| I23 | The cited lines say what this document claims | `test_cited_lines_say_what_the_documents_claim` (SKIP without `build/refs`) |
| I24 | An SP11 monitor never adopts the SSD on the A16 controller re-proof alone: the adoption entry refuses with a named, host-decodable status | `tools/test-monitor-hardening.py` (`NvmeWindowWiring`, `Mutants`), `tools/test-sp11-integration.py` (`StatusBands`) |

## 7. Appendix D answered: the per-machine questions

Each question from [Appendix D](SURFACE-PRO-11.md#appendix-d-per-machine-hardware-questions), split so that every item
is either answerable from cited public evidence or names the exact measurement and the value it should read. Nothing
below was measured on a Surface Pro. "Gate" is where the plan measures it; X2 and X6 are in section 6 of the plan.

| # | Question | Public evidence | Hardware measurement (gate) | Expected | Label |
| --- | --- | --- | --- | --- | --- |
| D1 | Does the GPT match the Windows record? | none | Read-only GPT survey (q1n1 `gpt_survey.py`, plan S1) plus a Windows `Get-Partition` export and two more independent readers; they must agree on every entry, both GPT copies and the usable range (X6, before any change) | ESP about 260 MiB, reserved 16 MiB, Windows, recovery about 1.2 GiB | INFERRED (operator survey) |
| D2 | Which PCIe segment hosts the SSD? | The Denali tree enables `pcie6a` with the 3.3 V NVMe supply and the root port at device 0 (`x1-microsoft-denali.dtsi:264-267,1015-1016,1031-1033`; `hamoa.dtsi:3434,3526`) | ACPI MCFG allocations, then config-space class `0x010802` per segment (X2 item 6, with the A5 adapter) | the segment of `pcie6a`; its number is firmware's, not Linux's `6` | UNKNOWN |
| D3 | Which BDF? | Root port at device 0 with `bus-range 0x01 0xff` (`hamoa.dtsi:3526-3529`) | Walk from the root port to its secondary bus and read the endpoint header (X2 item 6) | endpoint at bus 1, device 0, function 0, RID `0x0100` | INFERRED |
| D4 | Which ECAM base? | none: the device tree's `config` window (`hamoa.dtsi:3441`, `0x70100000`, 1 MiB) is Linux's, not an MCFG allocation | MCFG from the ACPI dump (X2 item 5, N6) | one allocation for the segment; value unknown | UNKNOWN |
| D5 | Which SMMU serves it? | `pcie_smmu` is `arm,smmu-v3` at `0x15400000`, size `0x80000` (`hamoa.dtsi:8544-8546`); `pcie6a` maps into it (`x1-el2.dtso:47-49`) | IORT from the ACPI dump; `SMMU_IDR0` read (X2 item 6) | `0x15400000`, SMMUv3 | VERIFIED (device tree); INFERRED (that firmware agrees) |
| D6 | Which StreamID? | EL2 overlay: `iommu-map = <0 &pcie_smmu 0x60000 0x10000>` for `pcie6a` (`x1-el2.dtso:48`) | IORT ID mappings, then the stream table entry read for that SID (X2 item 6) | `0x60100` for RID `0x0100` if the overlay holds; the A16 formula would give another value | INFERRED |
| D7 | Is the PCIe SMMU translating, bypassing or aborting at hand-off? | "Controlled by Gunyah" (`hamoa.dtsi:8555`); an EL2 owner "needs to configure it properly" (`x1-el2.dtso:25-27`) | `SMMU_CR0` (`+0x20`), `GBPA` (`+0x44`), `STRTAB_BASE` (`+0x80`), `STRTAB_BASE_CFG` (`+0x88`) and the stream's STE, read only, as q1n1 `nvme_m3.py:220-257` does (X2 item 6) | CR0.SMMUEN 1, STE Config 6 (stage 2 only), identity map over DRAM, as on the A16 | UNKNOWN (A16: VERIFIED) |
| D8 | What state is the controller in after ExitBootServices? | none | CAP (MQES, DSTRD, MPSMIN, TO), VS, CC, CSTS, AQA, ASQ, ACQ, read only (X2 item 6) | CC.EN 1, CSTS.RDY 1, firmware admin queue in firmware memory, DSTRD 0, MPSMIN 4 KiB, MQES at least 64 | INFERRED (A16) |
| D9 | Identify facts | none | Identify controller and namespace 1 through q1n1's own queues: MDTS, NN, ONCS, VWC, HMPRE/HMMIN, NSZE/NCAP/NUSE, NLBAF, FLBAS, the LBAF table, DLFEAT, EUI64 and NGUID (X6, read only) | 512-byte current format, no metadata, EUI64 present; an SSD with NGUID only cannot be pinned by this contract and is refused | INFERRED |
| D10 | Is the LBA format never switched? | By construction: Format NVM is on no list (`nvmeh_sp11_admin_verdict`) and a 4096-byte format is refused (I5, I6) | FLBAS identical before and after a whole session (X6) | unchanged | VERIFIED (host); UNKNOWN (device) |
| D11 | Does Windows boot normally after q1n1 resets the controller? | A16 never ran this (H6 skipped: q1n1 `docs/A16-NVME-M3.md`, "Not checked"). The A16 SSD counted 80 unsafe shutdowns in 184 power cycles. | After a takeover and the documented quiesce (`CC.SHN`, `CC.EN` 0) and a reset, boot Windows from the firmware menu; compare SMART unsafe-shutdown and error counters read-only (X6, plan S3) | Windows boots; counters rise by at most the resets performed | UNKNOWN |
| D12 | Which slot and attributes does Windows pick for the custom partition type? | none | After the operator creates exactly one entry with Windows' own partition manager, survey the GPT and record the entry index, type GUID and attribute bits (X6, plan S1) | any free slot; the monitor does not depend on slot or attributes, but `verify` must accept what Windows chose | UNKNOWN |
| D13 | Does the path need interrupts? | No: queue 1 is polled (`compat/nvme-host.h:13`) | none | none | VERIFIED (design) |
| D14 | Can the SSD reach the DMA window? | none | q1n1's reachability walk over the window against the SMMU state, `Smmu.reaches` (X6) with the DRAM map (X2 item 8) | every page identity-mapped read/write below `1 << 36` | UNKNOWN |
| D15 | Is the link up and the SSD powered? | The 3.3 V supply is `regulator-boot-on` (`x1-microsoft-denali.dtsi:264-267,277`); firmware has trained the link to boot Windows | The endpoint header must read as a device, not all ones (X2 item 6); this code refuses `NO_DEVICE` otherwise | link up | INFERRED |
| D16 | Volatile write cache, HMB | none | Identify controller VWC, HMPRE, HMMIN (X6); a DRAM-less SSD that Windows gave a host memory buffer runs without it after the reset | VWC present (writes use FUA and an explicit Flush); HMB state unknown | UNKNOWN |

## 8. Validation and what it does not show

`python3 tools/test-nvme-window-hamoa.py` runs the loader, the model and the C code against written-out verdicts
(114 contract cases, 7 free-at-end, 8 off-grid-neighbour and 9 4096-byte layout cases, 104 admin cases, 20 endpoint
cases) and against each other on every boundary of two layouts and on 9,000 random corruptions. It needs `clang` (SKIP
otherwise) and, for `test_cited_lines_say_what_the_documents_claim`, the pinned Linux files under `build/refs/`. The A16
default build is proved unchanged by `tools/monitor_baseline.py compare` (see
[BUILDING.md](BUILDING.md#reproducing-the-a16-monitor-baseline)). `tools/test-nvme-host.c` and
`tools/test-nvme-async.py` still exercise the A16 command path.

This work does not show: that an SP11 SSD, SMMU or firmware behaves as modelled; that the monitor's EL2 map reaches the
ECAM page, BAR0 and DMA window (the A16 relies on q1n1's identity map, `compat/nvme-host.c:6`; X4); that a Windows boot
survives a controller reset; or that anything calls `nvmeh_sp11_adopt` (wiring is UNIMPLEMENTED: until it exists the monitor's adoption
entry refuses, section 2).

## 9. K4

K4 ([section 3.3 of the plan](SURFACE-PRO-11.md#33-kill-criteria)) fires when the NVMe window cannot be proven safe at
X6. Concretely, any one of:

- the SMMU is not bypass or an identity stage 2 for the SSD's stream and the only way forward is for q1n1 to write the
  SMMU (a new hardware write that needs its own authorization), or the DMA window cannot be proven reachable (D7, D14);
- the endpoint cannot be pinned repeatably: BDF, stream map or EUI64 differ between cold boots (D3, D6, D9);
- the namespace is in a 4096-byte or metadata format, so the monitor refuses it (D9);
- the four GPT readers disagree, or no free extent fits a window with the 1 MiB alignment and guard (D1);
- Windows does not boot normally after a controller reset, or no quiesce leaves a state it accepts (D11);
- a read-only window run records any violation (a command outside the window), or any write run changes a byte outside it.

Then: stop, keep the logs, report what was measured, and ask the owner. The alternative is the spare SSD, but revisiting
D3 is the owner's explicit decision; the agents and tools never switch to it silently.

## 10. Hardware plan (not authorized)

In this order, each a separate supervised session with its own authorization, one hardware client at a time, AC power or
at least 50% battery, no writes before step 6: (1) X2 read-only measurements D2 to D8, D15; (2) the S1 survey and the
operator's single GPT entry with a verified backup and the BitLocker key off the device; (3) four-reader agreement and
verify-undo; (4) a read-only takeover and adoption with Windows still bootable, D9 to D14; (5) D11, Windows boots after the
reset; (6) per-run write enable with persistence capture, which needs the explicit per-run authorization this contract
checks for. Restore Secure Boot and BitLocker protection after experiments that no longer need them.
