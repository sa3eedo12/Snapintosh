<!-- SPDX-License-Identifier: MIT -->
# SP11 storage provisioning: the offline flow, its tools and the first storage hardware sessions

**Status: EXPERIMENTAL (host only). Hardware status: NOT RUN for the tooling and for sessions 2 and 3; session 1, a read-only survey, ran once on 2026-11-18 (section 9).** Nothing here has touched a Surface Pro except the read-only payloads of that session, and nothing in this
document, in the tools it describes or in the q1n1 payload it plans authorises a run ([AGENTS.md](../AGENTS.md), section 5:
access to the repository is not authorisation). It is the design for the storage items of [SURFACE-PRO-11.md](SURFACE-PRO-11.md)
(WS-D S1, S3 and S5, gate X6) under decision D3 (macOS lives in a partition window of the unit's internal NVMe; this document
does not propose the spare SSD), and the host tooling behind it. Labels are the plan's: VERIFIED, OBSERVED, INFERRED,
EXPERIMENTAL, UNIMPLEMENTED, UNKNOWN. A host test is not hardware evidence.

## 1. What this is, and is not

The A16 flow provisions the window with q1n1 over a live USB link ([FULL-INSTALL.md](FULL-INSTALL.md#provisioning)). The SP11 has
no live link yet, but its SSD is user-removable and the operator has attached it to a Mac once (a read-only layout capture on
2026-11-10, OBSERVED once; the capture stays in ignored local files). The plan below: Windows' own partition manager makes the one
change to the disk, the Mac checks it with four independent readers, digests every byte that must not change, and writes the
macOS window into the one new partition.

| Piece | State |
| --- | --- |
| `tools/snapmac-partition-plan.py --profile sp11`: plan, four-reader `verify`, `verify-undo` | reused as it is, neither copied nor grown ([FULL-INSTALL.md](FULL-INSTALL.md#partition-plan)); synthetic tests |
| `tools/snapmac_provision.py` with `tools/snapmac_image.py` and `tools/snapmac_window.py`: `plan`, `pre-image`, `write`, `post-check`, `verify-undo`, `auth-check` | EXPERIMENTAL; **image files only**; tests on synthetic 128 MiB images (section 7) |
| The per-run authorised device mode and its record | designed (section 6); `open_device_for_write` **refuses always**; UNIMPLEMENTED |
| A write to the real SSD | UNIMPLEMENTED: until the device mode is built, reviewed and authorised there is no executor for step M2b on the unit |
| Reading the A16's packed payload directory (`tools/snapmac-window-payload.py`) as the container | UNIMPLEMENTED: the writer takes the container as one file |
| The record producer for the monitor's adoption (session 2 of section 9) | UNIMPLEMENTED ([SP11-NVME-WINDOW.md](SP11-NVME-WINDOW.md#2-the-sp11-contract-in-compatnvme-hostc)) |

No tool here opens a block device. Every path goes through `tools/snapmac_image.py`, which resolves it (symbolic links, `..`,
case, and identity with `/dev` and `/Volumes` by `samefile`) and refuses anything at or below `/dev` or `/Volumes` and anything
that is not a regular file. The one `os.open` of all three modules is in that file, after that refusal, and the tests audit the
sources for it (section 7).

## 2. The flow

Five exchanges between Windows and the Mac. The operator is the only actor in Windows; the tools run on the Mac.

| Step | Where, who | What | What is written | Checks and stops |
| --- | --- | --- | --- | --- |
| W1 | Windows, operator | Recovery key off the device and a verified full-SSD backup first (X0, N7). BitLocker: section 2.3. Shrink the Windows volume with Windows' own partition manager and nothing else; shut down fully, not hibernate or Fast Startup (INFERRED: so the NTFS volume is clean) | the Windows volume and the GPT, by Windows | free space of at least the planner's minimum (a policy value, 128 GiB by default; `--min-bytes`) |
| M1 | Mac, read-only | The SSD in an enclosure; capture the layout: the first and last MiB through a read-only handle, decoded by q1n1's `tools/gpt_survey.py`, a `gdisk -l` listing as a second view. This is the BEFORE survey. `snapmac_provision.py plan` | `plan.json`, a new file | the planner refuses a survey with errors, a failed CRC, overlaps, no free extent that holds the minimum plus two 1 MiB guards, or several candidates (it lists them); the plan pins the disk (size, sector size, GPT GUID, NVMe EUI64), every partition and the window's offset and size |
| W2 | Windows, operator | Create exactly ONE partition at the plan's offset and size with Windows' own partition manager (the plan emits no command); boot Windows normally once; export `Get-Disk` and `Get-Partition` after creating it and again after that boot | one GPT entry, by Windows | none by these tools |
| M2a | Mac, read-only | Four readers of the disk after the change (the Windows record, the Mac raw survey, `gdisk -l` or `sgdisk -p` converted, the Windows record after the normal boot); `snapmac_provision.py pre-image` | `preimage.json`, a new file | the planner's gate (section 3); the image's own GPT is the plan's entries plus exactly the one new entry; a SHA-256 of every protected extent |
| M2b | Mac, the authorised write | `snapmac_provision.py write` on an image file today; the device mode is designed (section 6) | the new partition only: the ISC region and the container, read back, the header last | the plan's rules for the new entry and the four-reader gate are checked again on the BEFORE survey and the readers whose digests the pre-image holds; the pre-image is recomputed before the first write; any difference refuses (`StalePreimage`); `post-check` after |
| W3 | Windows, operator | The SSD back in the Surface; BitLocker protection back on (section 2.3); boot Windows normally; export the records again | nothing | Windows boots; every old partition's record is unchanged; any difference is a stop |
| undo | either | Remove the one entry with Windows' own partition manager; read four ways; `verify-undo` against BEFORE | one GPT entry removed; the bytes are not erased and the space returns to Windows | the planner's `verify-undo`, and every journaled write lies inside the free space that came back |

The first outline of this flow (suspend BitLocker and shrink; the Mac's read-only capture, plan and gate; the write; the SSD back into
the Surface with a boot check) has no step for creating the entry after the shrink. The planner needs a BEFORE survey of the
shrunk disk and an entry the operator made, so W2 is added and the survey is taken after W1.

### 2.1 What carries over from the A16 flow

| A16 piece | In the SP11 offline flow |
| --- | --- |
| M1 builds the install image on the Mac; the main APFS container is the partition of GPT type `7C3457EF` in `build/full-install/configured.img` ([FULL-INSTALL.md](FULL-INSTALL.md#the-window-what-the-guest-sees)) | carries over: the container and the ISC image are the same artefacts, built the same way |
| M5, `tools/snapmac-window-payload.py`: the container in 8 MiB chunks, zero chunks not packed, a manifest with the SHA-256 of the whole range ("every byte of the container, holes included") | the container is ONE FILE here (`--container`, a sparse file works: holes are written as zeros, as the A16 writer does); the chunk is the same 8 MiB; reading the packed directory directly is UNIMPLEMENTED |
| `tools/snapmac-window-header.py`: the 4 KiB `SNAPWIN1` header, the ISC region, the container region, state 1 | `tools/snapmac_window.py` builds the same header; a test compares the bytes with the A16 script on the A16's numbers, so the two cannot drift; the numbers are parameters |
| Order: ISC and container first, read back, header last | carries over; `post-check` fails when the header is not the last write |
| The Windows-side writer and q1n1's native writes through the armed window | do not carry over: the Mac writes the removed SSD (device mode, designed, not enabled) |
| The partition planner and its four-reader gate | reused as it is (not copied, not grown) |
| The monitor arms the window by reading both GPT copies, one entry with the pinned GUID, type and LBA range, then the header | unchanged; the SP11 contract is in [SP11-NVME-WINDOW.md](SP11-NVME-WINDOW.md) |

### 2.2 The commands

```
python3 tools/snapmac_provision.py plan --survey before.json --out plan.json [--min-bytes N] [--isc-region BYTES] [--extent-start LBA --extent-end LBA] [--no-eui]
python3 tools/snapmac_provision.py pre-image --image sp11.img --plan plan.json --before before.json --after r1.json --after r2.json --after r3.json --after r4.json --out preimage.json
python3 tools/snapmac_provision.py write --image sp11.img --plan plan.json --pre-image preimage.json --before before.json --after r1.json --after r2.json --after r3.json --after r4.json --container container.img --isc isc.img --confirm-image sp11.img --out journal.json
python3 tools/snapmac_provision.py post-check --image sp11.img --plan plan.json --pre-image preimage.json --journal journal.json
python3 tools/snapmac_provision.py verify-undo --before before.json --plan plan.json --journal journal.json --undone-survey undone.json --after u1.json --after u2.json --after u3.json --after u4.json
python3 tools/snapmac_provision.py auth-check --record auth.json --plan plan.json --pre-image preimage.json [--device PATH]
```

`--confirm-image` must equal the image file's own name. `write` takes the same `--before` survey and `--after` readers (and `--no-eui`, if
`pre-image` was given it) as `pre-image`, and refuses any that are not the documents whose digests the pre-image holds. Exit status: 0 ok, 1 a check failed, 2 refused. A failed check prints its
problems; a refusal means nothing was done. The image is never created or truncated, and no output file is ever overwritten.

### 2.3 BitLocker

[SURFACE-PRO-11.md](SURFACE-PRO-11.md#8-hardware-safety-and-authorization) section 8 lists "Suspend BitLocker" as needed only for
way B of its section 8.1, and says a removed SSD needs no suspension: the recorded `suspend_bitlocker` permission is a permission,
not an instruction. The assignment's step (1) names suspension. This document keeps it as an OPTIONAL precaution for W1 and W2,
under that standing permission, and never as a substitute for the recovery key off the device (N7). If it is used, the operator
suspends it for the whole procedure (for example `Suspend-BitLocker -MountPoint C: -RebootCount 0`, INFERRED from Microsoft
documentation, not tried on the unit) and resumes it in W3 (`Resume-BitLocker`), then confirms that protection reads On. The
authorisation record of section 6 has an acknowledgement for either choice (`bitlocker_suspended_or_key_off_device`). Proposed
text to reconcile section 8 is in the report that came with this document.

### 2.4 Cautions on the Mac (INFERRED; not verified on macOS 27)

macOS may auto-mount the volumes it recognises on an attached disk (an ESP in FAT, and NTFS read-only), and mounting a FAT volume can
write index and trash folders into it. It may also offer to initialise a disk it cannot read. So: attach the enclosure with the
volumes not mounted, or eject them before the capture, never accept an offer to initialise or erase, and treat a volume that
mounted read-write as a change that the pre-image gate would then have to explain. The tools read image files, never volumes (they
refuse `/Volumes`).

### 2.5 Cost

The pre-image and the post-check digest every protected extent: nearly the whole disk, twice. For a disk of about 1 TB that is about
0.9 TB read twice, plus the container written once, plus a full-disk image file when the flow is rehearsed on a capture (one disk's
size of free space on the Mac). The time is the enclosure's read rate times that; the tools report no estimate and this document
claims none (UNKNOWN until measured).

## 3. The tools

| Command | Reads | Writes | Notes |
| --- | --- | --- | --- |
| `plan` | the BEFORE survey (JSON) | `snapmac-window-plan/1`: the planner's plan, its SHA-256, the window layout and every protected extent | the extents tile the disk outside the new partition: both GPT zones, every partition, both guards and every unallocated gap |
| `pre-image` | an image, the plan, the BEFORE survey, N reader documents | `snapmac-preimage/1`: the gate verdict, the new entry, a SHA-256 per protected extent, and the SHA-256 of the survey and of every reader document (`inputs`) | needs at least 4 readers (`--min-readers` below 4 is refused); the planner's `verify_sp11` runs in the same process |
| `write` | an image, the plan, the pre-image, the BEFORE survey and the readers it names by digest, the container and the ISC image | the image, inside the new partition only; `snapmac-write-journal/1`, which holds the manifest (`snapmac-window-manifest/1`) whose digest the header carries | first the plan's rules and the gate again (below), then the order: ISC region (zero-padded), container, `fsync`, read-back, the header, `fsync`, read-back; chunks of 8 MiB |
| `post-check` | an image, the plan, the pre-image, the journal | nothing | every protected extent against the pre-image, every journaled write inside the partition, the header last, the window reading back as its header says |
| `verify-undo` | BEFORE, the readers of the undone disk, the plan, the journal, the undone survey | nothing | the planner's `verify_undo_sp11` and: every journaled write lies in the free space of the undone disk |
| `auth-check` | a record, the plan, the pre-image | nothing | validates the record of section 6; with `--device` it ends at the refusal of the device mode |

The four-reader gate is the planner's ([SURFACE-PRO-11.md](SURFACE-PRO-11.md#64-x3-to-x11), the X6 reader note): at least 3 of the 4
documents differ in content, and it does not require a reader that can see a hybrid MBR (a decided limitation; add a `gdisk -l`
reader whenever it could matter). The plan, the pre-image and the journal are JSON with a `format` string; the plan's digest, layout
and extents are recomputed whenever a file is read, never trusted, so an edited plan is refused even when its digest was fixed up.

**A plan or a pre-image read from a file is data, not a witness.** The first version of the tools checked the plan, the pre-image and the
image only against one another, and an independent review wrote into the ESP extent of a synthetic image with a hand-built plan whose new
entry was the ESP (guard 0, the ESP left out of the protected entries) and a hand-built pre-image (EXPERIMENTAL: host, a synthetic image;
reproduced by `tools/test-snapmac-provision-sp11-gate.py`, which builds that forgery and refuses it). Three layers now stand between such
documents and the writer, and the tests switch the other two off to show that each stops it alone:

1. Every command that reads a plan runs `check_plan_rules` on it: the new entry's type is the private Snapintosh type and not one of the
   four Windows types (EFI system, reserved, basic data, recovery); the guard is at least the planner's 1 MiB; its offset and size are
   whole 16 KiB units, not empty; its LBAs agree with its bytes; it lies inside the GPT's usable range; no protected entry has the new
   type; and the plan protects an EFI system partition and a Windows data partition (a Windows disk always has both).
2. `write` runs the four-reader gate again, on the BEFORE survey and the readers it is given, which must be the documents whose SHA-256
   the pre-image holds (`inputs`): the pre-image's own `gate` entry is a claim written in a file, not a result. The planner's `verify_sp11`
   also reproduces the plan from the BEFORE survey, so a plan the planner would not have made from a survey of this disk is refused.
3. `image_state` requires the image's own entry to be of the private type, at the planned LBAs, with allowed attributes, and every other
   entry to be the plan's protected entry.

## 4. The window

A partition of P bytes at offset O (both multiples of 16 KiB, the guest's page size), with an ISC region of R bytes:

| Range inside the partition | Content |
| --- | --- |
| first 1 MiB | the monitor synthesizes the GPT it shows macOS; the 4 KiB header sits on media at +512 KiB and is written last (state 1) |
| +1 MiB, R bytes | the ISC region: the ISC image, then zeros to the end of the region (512 MiB by default, as on the A16; a parameter) |
| after it, up to the last MiB | the container region: the container file (whole 4 KiB blocks), nothing after it is written |
| last 1 MiB | reserved: the monitor synthesizes the backup GPT; the writer never writes it |

The header carries the partition's GUID and LBA range, the virtual block size (4096), the two regions, the byte counts and SHA-256 of the
ISC image and the container, the SHA-256 of the manifest the journal holds, and a time stamp. A window that does not fit (the
container larger than its region, an ISC image larger than R, a size that is not whole 4 KiB blocks) is refused before the first write.

## 5. What is refused

Each row has a deliberately faulty case in `tools/test-snapmac-image-sp11.py` (the image layer), `tools/test-snapmac-provision-sp11.py` (the
flow) or `tools/test-snapmac-provision-sp11-gate.py` (what a plan or a pre-image is not trusted for) that must trip it.

| Refusal | Raised as | When |
| --- | --- | --- |
| a path at or below `/dev` or `/Volumes` | `ImageError`: "device node or a volume" | any input or output path, in any spelling: case, `..`, a relative path, a symbolic link, an identity match |
| not a regular file; empty or NUL path; a file that is not the one checked; a symbolic link put in place of the checked file | `ImageError`: "changed between the check and the open", "cannot open" | every open; the descriptor is checked again after `os.open`, which does not follow a symbolic link |
| an output that exists, or whose directory is missing or on a device or volume | `ImageError`: "never overwritten" | checked before any work, so that a refusal cannot come after a write (the journal is the only record of one); the exclusive create refuses again an output or a symbolic link that appeared after the check |
| a write outside the partition, one sector past its end or before its start | `WriteRefused`: "leaves the partition" | every write |
| a write onto a protected extent; a writer whose extent overlaps one | `WriteRefused`: "touches the protected extent" | every write, and at the writer's construction |
| an empty, unaligned or oversized (over 8 MiB) write; a read-only image | `WriteRefused` | every write |
| a stale pre-image: one byte of any protected extent differs | `StalePreimage`: "stale pre-image" | before the first write |
| no or wrong `--confirm-image` | `ProvisionError`: "missing confirmation" | `write` |
| fewer than four agreeing readers, a reader that disagrees, a reader that cannot be read | `ProvisionError`: "gate" | `pre-image`, and `write` again |
| a plan whose new entry has a Windows type, is not the private type, has a guard under 1 MiB, is empty or not whole 16 KiB units, disagrees with its own LBAs, lies outside the usable range, shares its type with a protected entry, or leaves no EFI system or Windows data partition protected | `ProvisionError`: "Windows type", "not of the private Snapintosh type", "the guard is", "bytes and LBAs disagree", "usable range", "already has the new type", "does not protect" | every command that reads a plan |
| a survey or readers that are not the ones the pre-image names by digest (one differs, is missing, is extra, or cannot be digested); a pre-image with no, or malformed, `inputs` | `ProvisionError`: "BEFORE survey is not the one", "readers are not the ones", "does not bind" | `write`, `post-check` |
| an image that is not the plan's disk: another size, GUID or usable range; not exactly one new entry at the planned LBAs; an entry that is not of the private type; attributes the plan does not allow; a protected entry that differs | `ProvisionError`: "not exactly one entry" and its siblings | `pre-image`, `write`, `post-check` |
| a plan, a pre-image or a journal that was edited, or belongs to another plan | `ProvisionError` | every command |
| a container or ISC image that does not read back | `ProvisionError`: "does not read back" | `write`: no header is written, so the partition is not a window |
| the device mode: no record, an invalid record, and a valid record | `DeviceModeRefused` | `open_device_for_write` and `auth-check --device` |

## 6. The authorised device mode (designed, NOT enabled)

The real SSD is reached only by a separate mode that needs a record for each run. `snapmac_provision.py` implements the record and
its validation, and **no code in any of the three modules opens a device node**: `open_device_for_write` validates the record and then
refuses, always (`DEVICE_MODE_ENABLED = False`; a test flips the flag and proves that nothing is opened or stat-ed). The record,
`snapmac-write-auth/1`, is made by the operator for ONE run:

| Field | Meaning |
| --- | --- |
| `format`, `mode` | `snapmac-write-auth/1`, `device` |
| `run_id` | 8 to 64 characters of `[A-Za-z0-9._-]` |
| `issued_utc`, `expires_utc` | `YYYY-MM-DDTHH:MM:SSZ`; valid now, for at most 30 minutes |
| `disk` | size, GPT GUID and NVMe EUI64 of the disk the plan was made for |
| `plan_sha256`, `preimage_sha256` | the digests of THIS plan and THIS pre-image |
| `partition` | offset and size of the one new partition |
| `acknowledged` | six booleans, every one `true`: `backup_verified`, `recovery_key_off_device`, `bitlocker_suspended_or_key_off_device`, `windows_shut_down_cleanly`, `recovery_media_ready`, `one_hardware_client_only` |
| `tool_source_sha256` | the SHA-256 of the three modules: editing a tool voids the record |

`auth-check` refuses an unknown or missing key, an expired or not-yet-valid or too-long record, another plan, another pre-image,
another disk or partition, a false or non-boolean acknowledgement, another version of the tools, a bad run id and malformed times.
A valid record is still refused by the device mode today. What enabling it would have to add, each with its own host test and a
review before any hardware time: the device open itself (read-only first: the image checks `image_state` and the pre-image digests
must pass on the device before a second, writable open), a check that the device is the recorded disk, the consumption of the record
(a ledger that refuses a second use of a run id), the same bounds and journal as the image writer, and the per-run authorisation
that [SURFACE-PRO-11.md](SURFACE-PRO-11.md#8-hardware-safety-and-authorization) section 8 requires for NVMe writes. A record never
authorises anything by itself ([AGENTS.md](../AGENTS.md), section 5).

## 7. Validation, and what it does not show

```
python3 tools/test-snapmac-image-sp11.py        # the image layer: paths, writer, GPT reader, header against the A16 script, source audit
python3 tools/test-snapmac-provision-sp11.py    # the flow, every refusal, the authorisation record
python3 tools/test-snapmac-provision-sp11-gate.py   # a plan or a pre-image from a file is not believed: the forged plan, each rule, the gate at write time
python3 tools/test-snapmac-provision-sp11-doc.py   # this document against the tools and the probe's document
```

All run on synthetic images under `build/`, with the planner test's fixtures (obviously fake identifiers, decoded by q1n1's
`tools/gpt_survey.py`). The required refusals each have a faulty case: a protected extent touched, a write one sector past the
end, a stale pre-image, a missing confirmation, a missing, wrong or valid-but-not-enabled authorisation, a device or volume path in
every position. The source audit proves that the tools import no shell, network, `ctypes` or `fcntl` module, call no `os` function
off an explicit list, hold exactly one `os.open` and one `os.pwrite`, name no disk tool, and that `open_device_for_write` calls
nothing but its validator; each check has a seeded fault it must find.

They do not show: that any of it works on the unit's SSD, on a real APFS container or through a USB enclosure; what macOS does to an
attached disk (section 2.4); that BitLocker or Windows accept the changed disk; the time of a full-disk digest; or that a written
window is served correctly by the monitor (session 3 of section 9). Hardware: NOT RUN.

## 8. Appendix D: which question needs what

The sixteen per-machine questions are those of [SP11-NVME-WINDOW.md](SP11-NVME-WINDOW.md#7-appendix-d-answered-the-per-machine-questions)
(Appendix D of the plan). "Offline" means answered, or answerable, with the SSD on the Mac and the Windows exports, with no run on the
unit. "Unit" names the probe or session that must read it from the unit. "Authorisation" names what a run needs beyond the operator's
explicit approval of the run itself. "Today" is the state: OBSERVED once means one unit and one boot, and OBSERVED in two boots means two boots of that unit.

| # | Question | Offline | Unit (probe or session) | Authorisation | Today |
| --- | --- | --- | --- | --- | --- |
| D1 | GPT matches the Windows record | **yes**: M1 and M2a (the four readers; the capture of 2026-11-10 is the first view) | none | none for reading; the operator's backup and the recovery key off the device | the layout is OBSERVED once (no free space before the shrink); the four-reader agreement is not run |
| D2 | PCIe segment | yes, the MCFG of the dumped ACPI tables | session 1 prints the constants it reads | none | OBSERVED once: 6 |
| D3 | BDF | no | session 1 reads both headers (00:00.0, 01:00.0) | none | OBSERVED in two boots: 01:00.0, RID `0x0100` |
| D4 | ECAM base | yes, the MCFG | session 1 reads through it | none | OBSERVED in two boots: `0x70000000`, buses 0 to 1 |
| D5 | which SMMU | **yes**: the IORT of the dumped tables has one SMMUv3 node at `0x15400000` | session 1 reads its ID registers; a fault is itself the finding | none | the node is OBSERVED in the dump; the registers UNKNOWN (the read faulted at EL2 in two boots, runs 6 and 7 of the X2 image: [SP11-X2-TESTS.md](SP11-X2-TESTS.md), test T07; session 1's `smmuprobe` found the page unmapped at EL2 and read nothing) |
| D6 | StreamID | **yes**: the IORT maps the segment-6 root complex RIDs `0..0xFFFF` to `0x60000 + RID`, so `0x60100` for RID `0x0100` | session 1 reads the entry of that StreamID | none | the declared map is OBSERVED in the dump; the SMMU's own state UNKNOWN |
| D7 | translating, bypass or abort | no | session 1 (`smmuprobe`: verdict) | none to read; a verdict that ends at an SMMU write needs its own, named authorisation (K4, first bullet) | UNKNOWN (session 1 ran: the SMMU's page was unmapped at EL2, so nothing was read) |
| D8 | controller state after ExitBootServices | no | any later read of CAP to ACQ (session 2 reads them again) | none | OBSERVED in two boots: enabled, ready, two-entry firmware queues, the registers identical |
| D9 | Identify facts | no | session 2 (Identify controller and namespace) | the controller takeover (section 9) | UNKNOWN |
| D10 | LBA format never switched | host: the monitor's admin allow-list refuses Format NVM, the only way to switch it (VERIFIED on the host model by `test_identify_supplies_the_current_format_and_never_a_better_one` in `tools/test-nvme-window-hamoa.py`; that the device keeps its format is UNKNOWN) | session 2 (FLBAS before and after) | the controller takeover | UNKNOWN on the device |
| D11 | Windows boots after a controller reset | no | session 2, last step | the controller takeover; the Windows boot itself is a normal boot | UNKNOWN |
| D12 | slot and attributes Windows picks | **yes**, after W2: `verify` records the entry's slot, attributes and name | none | the operator's one GPT entry (section 8 of the plan: "Create the Snapintosh GPT entry", X6) | UNKNOWN |
| D13 | interrupts needed | host: INFERRED from the design (the window polls its queue and creates its completion queue without interrupts, `compat/nvme-host.h`); no host test asserts it | none | none | INFERRED (design); UNKNOWN on the device |
| D14 | the SSD reaches the DMA window | no | session 1 was to decide whether it holds by construction (bypass) and did not, the SMMU's page being unmapped at EL2; a translating verdict needs the stage-2 walk that follows; sessions 2 and 3 show it by use | none to read | UNKNOWN |
| D15 | link up, SSD powered | no | session 1 reads the link status of both ends again, every run | none | OBSERVED in two boots: Gen4 x4, link active |
| D16 | volatile write cache, HMB | no | session 2 (Identify controller VWC, HMPRE, HMMIN) | the controller takeover | UNKNOWN |

## 9. The first three storage hardware sessions

Three ordered sessions; none is authorised by this document. Each needs the operator's explicit authorisation for the run, one
hardware client at a time, AC power or at least 50% battery, the thermal guard on, Windows first in the boot order and a verified
backup with the recovery key off the device ([AGENTS.md](../AGENTS.md), section 5; section 6.1 of the plan). Media: **nothing is
written to the SSD in any of the three.** The controller: nothing is written in session 1; sessions 2 and 3 write its registers
(the takeover), which is why each needs its own authorisation.

**Session 1: the read-only probe (the q1n1 `smmuprobe` payload).** Reads PCIe configuration space of the root port and the endpoint
and the PCIe SMMU's registers, in this order and no other, each read guarded and bounded; the group stops at its first read that
does not return. The payload has no store but to its own stack, no doorbell, no CC write, no admin command (the q1n1 fork's
`docs/SP11-PAYLOAD-SMMUPROBE.md`; the binary is audited by the memory checker's read-only policy). It asks EL2's stage 1 about each page
first (AT S1E2R, which makes no access) and reads none that the MMU does not map, or, for a device page, does not map as Device memory.

| Order | Reads |
| --- | --- |
| 1 | `START`; the pages of the root port, the endpoint and the SMMU are asked of EL2's stage 1 (`MAP`): a page it does not map as Device memory is not read |
| 2 | the headers of 00:00.0 and 01:00.0 (ids, command, class, header type, bars, capability pointer) |
| 3 | `NEXT PCIE_CAPS AFTER 8 S`, an 8 s wait; then the PCIe capability, link status and error status of both ends (and the AER block where present) |
| 4 | `NEXT SMMU_REGISTERS AFTER 8 S`, an 8 s wait; then the SMMU registers in the order of the table below; nothing at or above `0x90`; not run at all (no `NEXT`, no wait) if the MMU refused the SMMU's page: the verdict is then `UNKNOWN` with `SMMU_PAGE_UNMAPPED` or `SMMU_PAGE_NOT_DEVICE` |
| 5 | the stream table: `STRTAB_BASE` and `STRTAB_CFG`, the level-1 descriptor if two-level, the entry of StreamID `0x60100`, its stage-2 fields; a page the memory map or the MMU refuses is not read |
| 6 | `VERDICT` and `DONE` |

<!-- storage-registers -->
| # | SMMU register | Offset |
| --- | --- | --- |
| 1 | `IDR0` | `0x00` |
| 2 | `IDR1` | `0x04` |
| 3 | `IDR2` | `0x08` |
| 4 | `IDR3` | `0x0c` |
| 5 | `IDR4` | `0x10` |
| 6 | `IDR5` | `0x14` |
| 7 | `IIDR` | `0x18` |
| 8 | `AIDR` | `0x1c` |
| 9 | `CR0` | `0x20` |
| 10 | `CR0ACK` | `0x24` |
| 11 | `GBPA` | `0x44` |
| 12 | `GERROR` | `0x60` |
| 13 | `STRTAB_BASE` | `0x80` |
| 14 | `STRTAB_CFG` | `0x88` |
<!-- /storage-registers -->

What the verdict means for S2 and S3 (the full table is in the payload's document): bypass (`BYPASS`, `BYPASS_GLOBAL`): the SSD
addresses memory directly, so the window model's allow-lists are the whole safety argument and D14 holds by construction; translating
(`S1`, `S2`, `S1S2`): the DMA window exists only through the tables the entry names, so a stage-2 walk over the window follows and,
if they do not map it, the monitor needs a stream table of its own (an SMMU write: K4, first bullet, its own authorisation); abort
(`ABORT`, `INVALID`, `RESERVED`, `NO_STE`): the controller cannot DMA at all, which stops here (K4 if the monitor cannot write the
SMMU); `UNKNOWN`: the SMMU cannot be read (a read faulted, the MMU does not map its page, or `CR0` differs from its acknowledgement), which
decides nothing and is never treated as bypass.

- Operator: boot the stick with the runner and `smmuprobe.bin` last in its chain (a read that hangs takes the earlier lines with it);
  photograph the page and its second screen; note the run stamp; remove the stick; boot Windows normally once.
- Written: nothing to the SSD, the SMMU or any device register by the payload (it has no store but to its own stack). What a run does
  write is outside the payload: the stick's Shell script puts its marker file `SP11\DONE<n>.TXT` on the stick, as every autorun step does,
  and the runner draws the page on the framebuffer.
- Stop: no `DONE` line within 60 s of `CALL` (power off by the power button, no second try before review); any `FAULT` on the first
  read of a group (the payload stops that group; do not retry); a verdict of abort; Windows not booting normally afterwards; battery
  under 50% off AC; a thermal-guard trip.
- Closes: D5 to D7 and D14's first half; the hardware half of X6's first sub-step (read-only analysis), the layout half being the
  offline capture. It decides whether K4's first bullet can fire. That was the design; boot 22 read no SMMU register and so closed none of them from the unit (the result below).

**Result of session 1 (boot 22, 2026-11-18; OBSERVED, one unit, one boot, from a film that started late; [SP11-STICK-LOG.md](SP11-STICK-LOG.md) section 20).** It ran as a chain of two read-only payloads, `nvmesnap` and then `smmuprobe` (the q1n1 fork, commit `241b7ca`), under one approval by SHA-256 for one boot; both ended with no fault and no refused read. `nvmesnap` read the SSD's link (Gen4 x4, the data link layer link active), the endpoint (D0, memory space and bus mastering on), the controller (enabled and ready, version 1.4.0, admin queues of two entries at `0xCF3E7000` and `0xCF3E8000`) and the two queue slots (two Identify commands with success completions): the registers equal the X2 image's reading of 2026-11-15, so the state the firmware leaves repeats across two boots. `smmuprobe` asked EL2's stage 1 about the SMMU's page `0x15400000` and got a translation fault at level 3: the page has no stage-1 mapping at EL2, no SMMU register was read, and the verdict is `UNKNOWN` with `SMMU_PAGE_UNMAPPED`; it is not treated as bypass. **Not settled:** the SMMU's mode (D7), why its page is unmapped, whether the SSD's DMA reaches a monitor-owned extent after a takeover (D14), anything about writes, D11, and anything about the Wi-Fi beyond the shared SMMU. No stop rule fired (no `FAULT`; no hang on the film; the time from `CALL` to `DONE` is not on it). The approval is spent. Reading the SMMU now needs an EL2 stage-1 mapping of its page (a q1n1 or monitor change, not a payload), and session 2's prerequisite, a verdict of bypass or a proven stage 2, is not met.

**Session 2: the controller adoption read path.** The takeover quiesces the controller (`CC.SHN` normal shutdown, `CC.EN` 0), gives it
queues of its own and enables it again; then Identify controller and namespace, the error, SMART and firmware-slot log pages,
Get Features, and a read of both GPT copies through an I/O queue; the monitor adopts the running controller with the host-placed
target and record and re-proves the endpoint, with the window read-only. Prerequisites that do not exist yet (UNIMPLEMENTED): the SP11
takeover tool (q1n1's `tools/nvme_m3.py` is A16 only), the record producer, and the stick bundle's placement of the two blocks (the
monitor's end is wired and tested on the host: [SP11-NVME-WINDOW.md](SP11-NVME-WINDOW.md#2-the-sp11-contract-in-compatnvme-hostc)).
It needs session 1's verdict to be bypass or a proven stage 2.

- Operator: the offline flow complete through M2a (the entry exists and four readers agree); run the stick; at the end power off and
  boot Windows normally; export the records and the SMART counters (a read) before and after.
- Written: to the controller: `CC`, `AQA`, `ASQ`, `ACQ` and the queue doorbells, and queue memory in host RAM. To the SSD media:
  nothing (Identify, log pages, Get Features and Reads only; Format, Sanitize, firmware and namespace commands cannot be expressed).
- Authorisation: a new row of section 8 of the plan, proposed in the report: the controller takeover is a write to the system disk's
  controller, gated by D11.
- Stop: any named refusal of the contract (the -96 to -145 band: a mismatch of segment, BDF, class, stream, EUI64, LBA format or window
  is a stop, not a retry); a 4096-byte or metadata format, or a namespace without an EUI64 (K4); `FLBAS` different after the run;
  `CSTS.CFS` or a timeout in the quiesce (power off); Windows not booting normally, or the unsafe-shutdown counter rising by more than the
  resets performed (K4, D11).
- Closes: D8 again, D9, D10, D11 and D16; a further reader for the X6 gate (the q1n1 survey of the same GPT); the monitor's adoption
  on real hardware (S2).

**Session 3: the first windowed read.** The window provisioned by M2b exists on the SSD (the entry, the header, the ISC image and the
container, all digested). The monitor adopts as in session 2, arms the window read-only, and reads window-relative LBAs through the
queue: the header, the first and the last chunk of the ISC region and of the container. The digests of those reads are compared with
the journal's, which is the first time the Mac's write and the monitor's read meet.

- Operator: as session 2, with the provisioned window; boot Windows normally at the end; export the records.
- Written: as session 2; every guest write of the window is refused and counted. Nothing to the SSD media.
- Stop: any read outside the window or any command outside the lists (a recorded violation is K4); any digest that differs from the
  journal's; any refusal; Windows not booting normally; a change of any old partition's record.
- Closes: X6's "a read-only window with Windows still booting". The later X6 sub-steps (per-run write enable with persistence
  capture; the install provisioned by native q1n1 writes) are not part of these three.

Offline steps that close X6's other early sub-steps need no session: the operator's one entry (W2), the four readers agreeing (M2a)
and `verify-undo` (the undo row of section 2), each with the authorisations of section 8 of the plan.

## 10. What is not done

The device mode (section 6); the container as the A16's packed directory; a sparse-aware write (the dense write sends the holes of
the container as zeros, which is what the A16 writer does too, so the cost is the container's whole range); the record producer, the
SP11 takeover and the stick placement of session 2; any measurement of time or of the Mac's behaviour with the enclosure; Windows
Fast Startup, BitLocker and macOS auto-mount behaviour, which are INFERRED here. Hardware: NOT RUN.

### 10.1 What the tools do not do, as they are today

These are properties of the code as written. The first three must be reviewed again, with host tests of their own, before
`DEVICE_MODE_ENABLED` is ever set: until then `check_auth`, `verify-undo` and the alignment gate nothing on a real disk.

* **`verify-undo` restores nothing.** It is the planner's survey-level check (`verify_undo_sp11`: the entry is gone, and the free extents and
  every protected partition's record equal BEFORE, as the readers see them) plus a check that every journaled write lies in the free space
  of the undone survey. The journal is an unauthenticated file. Nothing compares the GPT header, the backup header or their CRCs byte for
  byte, nor any byte of the freed space, and the header the writer put there, whose state says "provisioned", stays in the freed space:
  a partition made over it later would find a header that describes nothing (UNIMPLEMENTED: an undo that erases it, or proves it gone).
* **`check_auth` is stateless.** A valid record can be shown any number of times within its 30 minutes (nothing consumes it; the ledger of
  section 6 is UNIMPLEMENTED). Its tool-source digest covers three modules (`snapmac_image.py`, `snapmac_window.py` and
  `snapmac_provision.py`), not the planner (`snapmac-partition-plan.py`) whose rules and gate decide the plan. Its six acknowledgements are
  honour statements: no tool can know that the backup exists, that the recovery key is off the device or that Windows shut down cleanly.
* **The alignment is 16 KiB here and 1 MiB in the monitor.** The planner, and `check_plan_rules` after it, want the window's start and
  size in whole 16 KiB units (the guest's page); the monitor's contract wants both on 1 MiB (`NVMEH_SP11_ALIGN_LBAS` in
  `compat/nvme-host.h`). A plan can pass every tool here and be refused by the monitor at adoption, which is a refusal and not an unsafe
  write. Nothing here checks the 1 MiB rule (UNIMPLEMENTED).
* **The write-time gate is bound to documents, not to a disk.** `write` reruns the gate on the survey and readers it is given and holds
  them to the digests in the pre-image, but all of them are files. A forger who makes a consistent survey, readers, plan, pre-image and
  image GPT for a disk that is not the unit's is stopped only by the planner's own refusals, the plan's rules and the image's GPT; what
  the pre-image's digests of the protected extents prove is that the image has not changed since the pre-image was made
  (EXPERIMENTAL: host, synthetic images).

### 10.2 What the monitor does not know (accepted limitations of the contract)

The monitor's NVMe window ([SP11-NVME-WINDOW.md](SP11-NVME-WINDOW.md#2-the-sp11-contract-in-compatnvme-hostc)) checks that the record the
host placed is consistent with the board descriptor and the prepared run, not that it is true:

* The protected extents, the write-authorisation flag (`NVMEH_SP11_R_WRITE_AUTH`), the flag that the DMA window was proven reachable
  (`NVMEH_SP11_R_DMA_PROVEN`) and `smmu_mode` come from that record. A host that places a false one is believed as far as the checks
  of the contract go; the monitor reads nothing of the SMMU itself.
* `nvmeh_sp11_prove_endpoint` has no vendor or device id check (it requires an id that is not all ones, the NVM Express class, Memory
  Space and Bus Master, header type 0 and a BAR0 equal to the record's), and no SError guard (q1n1's A16 reads run under one; the
  monitor's read is a plain load at EL2). It takes BAR0, a PCI address, as the CPU physical address of the controller registers, which
  holds only while the firmware's root complex window is an identity (INFERRED; session 1 does not show it).
* Under bypass (`BYPASS`, `BYPASS_GLOBAL`) the SSD's DMA reaches all of DRAM, not only the DMA window: the shadow queues and the checks
  of `compat/nvme-host.c` are then the whole confinement (section 9, session 1, and the payload's document).
