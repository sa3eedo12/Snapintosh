#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Plan, and later verify, the one GPT change for the Snapintosh macOS window.

Pure computation over GPT survey JSON (q1n1 tools/gpt_survey.py --json); it
reads files and writes only the plan/verdict JSON you name. Nothing here can
reach the A16 or any disk.

  plan         --survey BEFORE.json [--out plan.json]
  verify       --before BEFORE.json --after AFTER.json --plan plan.json
  verify-undo  --before BEFORE.json --after UNDONE.json
  convert-reader --from {sgdisk,gpt-show} --in TEXT --label NAME --out reader.json   (sp11)

The plan pins the disk (size, sector size, GPT disk GUID, NVMe EUI64), every
existing partition (index, type, GUID, LBAs, attributes, name) and the two
neighbours of the free extent, and places one partition inside that extent
with a 1 MiB guard at each end. `verify` passes only when every existing entry
is byte-for-byte unchanged in meaning and exactly one new entry matches the
plan; `verify-undo` passes only when the layout equals BEFORE again.

--profile a16 (the default) is the behaviour above, unchanged: it pins the A16
disk and prints the Windows command lines. --profile sp11 is the Surface Pro 11
Windows disk. It has no constant that belongs to a unit: disk size, sector size
(512e or 4Kn), GPT disk GUID, NVMe EUI64, every partition, the free extent and
its two neighbours are read from the BEFORE survey and pinned into the plan.
Options that only sp11 understands are refused under a16.

sp11 plan: the free extents are recomputed from the partitions and must match
the survey's own list. One extent is used: the one named with --extent-start/
--extent-end (LBA, must be a whole free extent), or the only one that holds
--min-bytes (default 128 GiB, a policy value) plus two 1 MiB guards; none or
several are refused with the list. Refused: survey errors, a missing or
CRC-failed header or array, primary/backup headers that disagree, entries that
overlap, leave the usable range or share a GUID or slot, an MBR-only or
non-protective MBR, a disk that is not 128 x 128-byte 512e/4Kn GPT, an extent
that starts at the first usable LBA or reaches the last one (the backup GPT), a
new offset or size that is not 16384-byte aligned, a new type that is nil, a
Windows type or already present, no ESP or basic-data partition, a neighbour
before the extent that is not basic data. A hybrid MBR cannot be seen in the
survey JSON; a `gdisk -l` text converted with convert-reader can show it.
The plan emits no command, names the operator as the actor, and carries
`safety`: REQUIRES AUTHORIZATION (one GPT entry, docs/SURFACE-PRO-11.md section 8,
gate X6), REQUIRES A VERIFIED BACKUP (sections 1.1 N7 and 6.1 X0), BitLocker
recovery key off the device, tool_writes_disk false. The slot is a hint, never
identity; the name is a label (Windows' partition manager cannot set a GPT
name), recorded by `verify` and never required.

sp11 verify/verify-undo take `--after` once per reader document (the X6 gate
wants four) and `--min-readers` (default 4; fewer prints a "partial" note).
Reader documents: a gpt_survey JSON (a "full" reader: every strict check runs on
it, at least one is required), a Windows Get-Disk/Get-Partition record (Disk
Size/LogicalSectorSize/Guid/UniqueId/PartitionStyle/LargestFreeExtent and
Partitions Guid/Offset/Size/GptType; the in-repo consumer is VERIFIED, the
PowerShell schema is INFERRED), or a normalized reader (below). Every reader
is projected onto the expected layout (BEFORE plus the one planned entry):
partitions are matched by (first_lba, last_lba), so an off-by-one LBA is a
disagreement, and every optional field a reader carries is compared. A reader
is named by a top-level "reader" label in its document (required in the
normalized form) or else by its file name; the same document twice, or two
readers with one label, count once and fail, so two byte-identical Windows
records taken at different times need distinct "reader" labels. The label does
not make a reader independent: documents that differ only in their label are one
observation, and the gate needs all but one of its readers (3 of 4) to differ in
content, so one record copied under four labels never meets it (the plan's
fourth reader, a Windows record after a normal boot, may repeat the first). The
verdict (--out) records readers, whether four distinct documents agree, how many
differ in content ("independent"), and which readers could see a hybrid MBR
("mbr_checked", only a `gdisk -l` text with its scan block, converted with
convert-reader) and which could not ("mbr_unchecked"); a hybrid MBR is invisible
to every other reader, and four agreeing readers do not exclude it. The verdict
also records the new entry's GUID, slot, attributes and name. Exit 0 ok, 1 verify failed,
2 refused (an unusable BEFORE or plan, an unreadable file, an internal error:
a crash never exits 1). Files may be UTF-8, UTF-8 with BOM or UTF-16 with BOM
(Windows PowerShell). How the fourth X6 reader (a normal Windows boot) is recorded is the
operator's call: supply the record taken after that boot as its own --after.

Normalized reader, format "snapmac-reader/1" (unknown keys are refused):
  {"format": "snapmac-reader/1", "reader": "<label [A-Za-z0-9._-]{1,48}>",
   optional "mbr": "protective" | "unchecked" (absent means unchecked),
   "disk": {"block_size": 512|4096, optional "disk_bytes", "disk_guid",
            "nvme_eui" (16 hex digits, "eui." prefix and either byte order ok),
            "first_usable", "last_usable", "partition_style": "GPT"},
   "partitions": [{"first_lba": N, "last_lba": N, optional "guid", "type",
                   "attributes", "name", "gdisk_type_code" (EF00 ESP, 0C01 MSR,
                   0700 basic data, 2700 recovery; for any other type the code
                   must not be one of those), "index" (ignored)}]}
convert-reader turns captured text into that schema and never reads a device.
`sgdisk -p`/`gdisk -l` (capture with 2>&1): block size, GUID, usable range,
128 entries and table position are cross-checked, partition sizes are checked
against their printed sizes, names are dropped; warnings, hybrid/other MBRs
and any unrecognised line are refused. Only `gdisk -l` prints the "Partition
table scan:" block that says the MBR is protective, so only its conversion
carries "mbr": "protective"; the text of `sgdisk -p` says nothing about the MBR
and its reader is MBR-unchecked. Layout recalled from gptfdisk's public
output, INFERRED not VERIFIED. `gpt show` (macOS): rows must be contiguous from
sector 0 with the PMBR, both GPT headers and both tables; the UUID printed on a
"GPT part" row is the partition TYPE, so no partition GUID is claimed; format
INFERRED from recalled output of older macOS, not VERIFIED on macOS 27. Any
other reader (diskutil, sfdisk, Disk Management) is "operator supplies
normalized JSON".
"""
import argparse
import json
from pathlib import Path
import re
import stat
import sys
import uuid

# Pinned from the Windows storage records of 2026-09-21 and the pre-change GPT
# capture (build/a16-storage-backup-20260921); the survey must agree.
DISK_BYTES = 1024209543168
BLOCK = 512
DISK_GUID = '1c98f6ba-8bab-4cae-8c35-bf82870d8cf2'
EUI = '002538F261C3C291'
BEFORE_NEIGHBOUR = '17cd69a6-eb33-406a-86fd-6d54fac2d2d1'   # T: SNAPASSETS, ends at the extent
AFTER_NEIGHBOUR = '0e71a636-b076-4ab6-9f50-6425f2a618a5'    # Windows recovery, starts after it
ESP = '939a0a66-c61e-42b7-b975-5be3c2b80280'
WINDOWS_C = '419baed0-542c-4292-a670-caaabe34e2fa'
TYPE = 'af494c0f-7849-4186-8d2c-9b05cf6ab8b8'               # Snapintosh macOS window (private)
NAME = 'Snapintosh macOS'
# The free extent PROPOSAL.md was approved against. A survey showing anything
# else stops the plan; the proposal is then re-issued, not silently adapted.
APPROVED_EXTENT = (1170661376, 1996189695)
# The 18 partitions PROPOSAL.md section 1 pins, from Windows' final record of
# 2026-09-21 (staging-final.json): GUID -> (type, byte offset, byte size).
# `plan` refuses unless the survey shows exactly this set, nothing more.
PINNED = {
    'dc0777a3-2209-4316-8970-29eb200511d1': ('97745aba-135a-44c3-9adc-05616173c24c', 1048576, 1048576),
    'a642d24c-0c1c-4a11-99eb-84737f211431': ('bc0330eb-3410-4951-a617-03898dbe3372', 2097152, 16777216),
    'ff899570-2688-4192-8e02-90cd0c62ea61': ('b7524b2d-0a91-49a2-aa5e-bda266699d49', 18874368, 1048576),
    '5c92f10b-51bd-410b-9a42-48d746a3f775': ('a3381699-350c-465e-bd5d-fa3ab901a39a', 19922944, 1048576),
    '00c8adb3-028c-451b-9acf-c345b3f723c6': ('c80325ba-7ec2-4a30-b2f7-18f646086185', 20971520, 73400320),
    '9d8159d5-5938-4440-9caf-65c21392d5c2': ('fa4cbe53-31b5-4c23-9c1e-a01feb966856', 94371840, 125829120),
    '4ac52e5f-1aa6-409f-b12c-023520982261': ('6690b4ce-70e9-4817-b9f1-25d64d888357', 220200960, 125829120),
    '9c817ddb-0373-4d63-92f7-ed3bce38636d': ('aa9a5c4c-4f1f-7d3a-014a-22bd33bf7191', 346030080, 1048576),
    'aea9defe-5f13-4258-9f49-6eb36df1883e': ('80c23c26-c3f9-4a19-bb38-1e457daceb09', 347078656, 1048576),
    '5a514971-8e3b-47cf-a12b-fd95dec307f8': ('e12d830b-7f62-4f0b-b48a-8178c5bf3ac1', 348127232, 1048576),
    '5e3fcd32-6617-4a21-90bd-ed4ba1580b14': ('5af80809-aabb-4943-9168-cdfc38742598', 349175808, 536870912),
    '939a0a66-c61e-42b7-b975-5be3c2b80280': ('c12a7328-f81f-11d2-ba4b-00a0c93ec93b', 886046720, 471859200),
    '7723b707-46b5-4404-b74f-4e3dffe17d62': ('e3c9e316-0b5c-4db8-817d-f92df00215ae', 1357905920, 16777216),
    '419baed0-542c-4292-a670-caaabe34e2fa': ('ebd0a0a2-b9e5-4433-87c0-68b6b72699c7', 1374683136, 512104595456),
    '1b0d0e57-9a6f-498e-a189-264e9107bc36': ('ebd0a0a2-b9e5-4433-87c0-68b6b72699c7', 513479278592, 17179869184),
    '17cd69a6-eb33-406a-86fd-6d54fac2d2d1': ('ebd0a0a2-b9e5-4433-87c0-68b6b72699c7', 530659147776, 68719476736),
    '0e71a636-b076-4ab6-9f50-6425f2a618a5': ('de94bba4-06d1-4d40-a16a-bfd50179d6ac', 1022049124352, 1887436800),
    '4dcd54be-0986-4010-b44c-ae1fa3f853a7': ('de94bba4-06d1-4d40-a16a-bfd50179d6ac', 1023936561152, 272629760),
}
GUARD_LBAS = (1 << 20) // BLOCK
MIN_BYTES = 256 << 30
ALLOWED_ATTRS = (0, 1 << 63)
ENTRY_KEYS = ('index', 'type', 'guid', 'first_lba', 'last_lba', 'attributes', 'name')
# What "unchanged" means for a partition. The GPT slot index is not part of a
# partition's identity (Windows addresses partitions by GUID); a slot change is
# reported as a note, never silently accepted as "nothing changed".
IDENTITY_KEYS = ENTRY_KEYS[1:]


class PlanError(ValueError):
    pass


def the_disk(survey):
    disks = survey['disks'] if 'disks' in survey else [survey]
    nvme = [d for d in disks if (d.get('path_info') or {}).get('nvme_eui_bytes')]
    matches = [d for d in (nvme or disks) if d.get('disk_guid') == DISK_GUID]
    if len(matches) != 1:
        raise PlanError(f'expected exactly one disk with GPT GUID {DISK_GUID}, found {len(matches)}')
    return matches[0]


def check_disk(d, require_eui=True):
    problems = list(d.get('errors', []))
    if d.get('disk_bytes') != DISK_BYTES or d.get('block_size') != BLOCK:
        problems.append(f"disk is {d.get('disk_bytes')} bytes / {d.get('block_size')}-byte blocks")
    for which in ('primary', 'backup'):
        h = d.get(which) or {}
        if not (h.get('header_crc_ok') and h.get('array_crc_ok')):
            problems.append(f'{which} GPT header or array failed its CRC')
        if (h.get('count'), h.get('entry_size')) != (128, 128):
            problems.append(f'{which} GPT is not 128 x 128-byte entries')
    eui = (d.get('path_info') or {}).get('nvme_eui_bytes')
    if require_eui and eui not in (EUI, bytes.fromhex(EUI)[::-1].hex().upper()):
        problems.append(f'NVMe EUI64 {eui} is not the pinned Samsung {EUI}')
    if problems:
        raise PlanError('; '.join(problems))


def entries(d):
    return {p['guid']: {k: p[k] for k in ENTRY_KEYS} for p in d['partitions']}


def plan(survey, require_eui=True):
    d = the_disk(survey)
    check_disk(d, require_eui)
    parts = entries(d)
    if any(p['type'] == TYPE for p in parts.values()):
        raise PlanError('a Snapintosh window partition already exists; nothing to plan')
    for g in (BEFORE_NEIGHBOUR, AFTER_NEIGHBOUR, ESP, WINDOWS_C):
        if g not in parts:
            raise PlanError(f'pinned partition {g} is missing')
    if set(parts) != set(PINNED):
        raise PlanError(f'partition set differs from PROPOSAL.md: extra {sorted(set(parts) - set(PINNED))}, '
                        f'missing {sorted(set(PINNED) - set(parts))}')
    for g, (t, off, size) in PINNED.items():
        q = parts[g]
        if (q['type'], q['first_lba'] * BLOCK, (q['last_lba'] - q['first_lba'] + 1) * BLOCK) != (t, off, size):
            raise PlanError(f'partition {g} is not where PROPOSAL.md pins it: {q}')
    lo, hi = parts[BEFORE_NEIGHBOUR]['last_lba'] + 1, parts[AFTER_NEIGHBOUR]['first_lba'] - 1
    if (lo, hi) != APPROVED_EXTENT:
        raise PlanError(f'free extent is LBA {lo}..{hi}, the approved one is {APPROVED_EXTENT}')
    extent = [u for u in d['unallocated'] if u['first_lba'] == lo and u['last_lba'] == hi]
    if len(extent) != 1:
        raise PlanError(f'no free extent exactly between the pinned neighbours (LBA {lo}..{hi})')
    if any(p['first_lba'] <= hi and p['last_lba'] >= lo for p in parts.values()):
        raise PlanError('a partition overlaps the free extent')
    first, last = lo + GUARD_LBAS, hi - GUARD_LBAS
    if first % GUARD_LBAS or (last + 1) % GUARD_LBAS:
        raise PlanError('guarded partition is not 1 MiB aligned')
    size = (last - first + 1) * BLOCK
    if size < MIN_BYTES:
        raise PlanError(f'free extent gives only {size} bytes')
    used = {p['index'] for p in parts.values()}
    index = min(i for i in range(128) if i not in used)
    return dict(
        disk=dict(bytes=DISK_BYTES, block=BLOCK, gpt_guid=DISK_GUID, eui=EUI),
        protected=sorted(parts.values(), key=lambda p: p['first_lba']),
        extent=dict(first_lba=lo, last_lba=hi, offset=lo * BLOCK, bytes=(hi - lo + 1) * BLOCK),
        new=dict(expected_index=index, type=TYPE, name=NAME, first_lba=first, last_lba=last,
                 offset=first * BLOCK, bytes=size, end=(last + 1) * BLOCK, guard_bytes=GUARD_LBAS * BLOCK),
        windows=dict(new_partition=f"New-Partition -DiskNumber <pinned by UniqueId eui.{EUI}> "
                                   f"-Offset {first * BLOCK} -Size {size} -GptType '{{{TYPE}}}' "
                                   f"-NoDefaultDriveLetter",
                     diskpart=f"create partition primary offset={first * BLOCK // 1024} "
                              f"size={size >> 20} id={TYPE}"),
    )


def same(a, b):
    return a is not None and b is not None and all(a[k] == b[k] for k in IDENTITY_KEYS)


def verify(before, after, p, require_eui=True, notes=None):
    """Every problem found; empty means the change is exactly the plan."""
    problems = []
    notes = [] if notes is None else notes
    a = the_disk(after)
    try:
        check_disk(a, require_eui)
    except PlanError as e:
        problems.append(str(e))
    old, new = {q['guid']: q for q in p['protected']}, entries(a)
    if entries(the_disk(before)) != old:
        problems.append('BEFORE survey differs from the plan it was made from')
    for g, q in old.items():
        if not same(new.get(g), q):
            problems.append(f'existing partition {g} changed: {q} -> {new.get(g)}')
        elif new[g]['index'] != q['index']:
            notes.append(f"partition {g} moved from GPT slot {q['index']} to {new[g]['index']} (identity unchanged)")
    added = [q for g, q in new.items() if g not in old]
    if len(added) != 1:
        problems.append(f'expected one new partition, found {len(added)}')
    else:
        n, want = added[0], p['new']
        if (n['type'], n['first_lba'], n['last_lba']) != (want['type'], want['first_lba'], want['last_lba']):
            problems.append(f'new partition is {n}, planned {want}')
        if n['attributes'] not in ALLOWED_ATTRS:
            problems.append(f"new partition has attributes {n['attributes']:#x}")
        # The slot Windows picks is not part of the contract; the GUID and LBAs are.
        free = [(u['first_lba'], u['last_lba']) for u in a['unallocated']
                if p['extent']['first_lba'] <= u['first_lba'] <= p['extent']['last_lba']]
        guards = [(p['extent']['first_lba'], want['first_lba'] - 1), (want['last_lba'] + 1, p['extent']['last_lba'])]
        if free != guards:
            problems.append(f'free space inside the old extent is {free}, expected only the guards {guards}')
    for key in ('disk_guid', 'first_usable', 'last_usable', 'disk_bytes', 'block_size'):
        if the_disk(before).get(key) != a.get(key):
            problems.append(f'disk {key} changed')
    return problems


def verify_undo(before, undone):
    b, u = entries(the_disk(before)), entries(the_disk(undone))
    problems = [] if b.keys() == u.keys() and all(same(b[g], u[g]) for g in b) else ['partition set differs from BEFORE']
    b, u = the_disk(before), the_disk(undone)
    if [(x['first_lba'], x['last_lba']) for x in b['unallocated']] != [(x['first_lba'], x['last_lba']) for x in u['unallocated']]:
        problems.append('free extents differ from BEFORE')
    return problems + list(u.get('errors', []))


# --------------------------------------------------------------------------
# sp11 profile. Shares only PlanError, ENTRY_KEYS, IDENTITY_KEYS, ALLOWED_ATTRS,
# TYPE, NAME and same() with the a16 code above; nothing a16 pins is used here.

MIB = 1 << 20
GUARD_BYTES = MIB
# The macOS guest's page size: a partition that starts or ends inside a page
# would make the guest's whole-page view of its own storage straddle Windows data.
ALIGN_BYTES = 16384
SP11_MIN_BYTES = 128 << 30       # policy default, INFERRED; override with --min-bytes
SP11_MIN_READERS = 4             # the X6 gate: docs/SURFACE-PRO-11.md section 6.4
READER_FORMAT = 'snapmac-reader/1'
MBR_STATES = ('protective', 'unchecked')    # a reader's own statement about the MBR; absent means unchecked
NIL = '00000000-0000-0000-0000-000000000000'
T_ESP = 'c12a7328-f81f-11d2-ba4b-00a0c93ec93b'
T_MSR = 'e3c9e316-0b5c-4db8-817d-f92df00215ae'
T_BASIC = 'ebd0a0a2-b9e5-4433-87c0-68b6b72699c7'
T_RECOVERY = 'de94bba4-06d1-4d40-a16a-bfd50179d6ac'
WINDOWS_TYPES = (T_ESP, T_MSR, T_BASIC, T_RECOVERY)
GDISK_CODES = {'EF00': T_ESP, '0C01': T_MSR, '0700': T_BASIC, '2700': T_RECOVERY}
SAFETY = dict(
    requires_authorization=True,
    authorization='ONE GPT entry on the internal NVMe: docs/SURFACE-PRO-11.md section 8 (gate X6)',
    requires_verified_backup=True,
    backup='a verified full-SSD backup and Windows recovery media: docs/SURFACE-PRO-11.md sections 1.1 (N7) and '
           '6.1 (X0), AGENTS.md section 5 "Preserve Recoverability"',
    executor='operator',
    executor_note='operator creates the entry with the Windows partition manager; this tool does not',
    operator_note='create it at exactly new.offset with exactly new.bytes: a tool that fills the whole free '
                  'extent leaves no guards and fails verify',
    tool_writes_disk=False,
    emits_commands=False,
    bitlocker_recovery_key_off_device=True,
    bitlocker_note='keep the BitLocker recovery key for C: off the device before anything is shrunk or changed',
)


class AmbiguousExtent(PlanError):
    def __init__(self, message, extents):
        super().__init__(message)
        self.extents = extents


def _int(v, what):
    if isinstance(v, bool) or not isinstance(v, int):
        raise PlanError(f'{what} is {v!r}, not an integer')
    return v


def _guid(v, what):
    try:
        return str(uuid.UUID(v))
    except (ValueError, AttributeError, TypeError):
        raise PlanError(f'{what} is {v!r}, not a GUID') from None


def _eui(v):
    s = str(v).strip().upper()
    s = s[4:] if s.startswith('EUI.') else s
    if not re.fullmatch(r'[0-9A-F]{16}', s):
        raise PlanError(f'{v!r} is not an NVMe EUI64 (16 hex digits)')
    return s


def _eui_eq(a, b):
    a, b = _eui(a), _eui(b)
    return b in (a, bytes.fromhex(a)[::-1].hex().upper())    # Windows and firmware may print opposite byte orders


def _label(text):
    if not isinstance(text, str) or not re.fullmatch(r'[A-Za-z0-9._-]{1,48}', text):
        raise PlanError(f'reader label {text!r} is not [A-Za-z0-9._-]{{1,48}}')
    return text


def _keys(obj, required, optional, what):
    if not isinstance(obj, dict):
        raise PlanError(f'{what} is not an object')
    missing, extra = set(required) - set(obj), set(obj) - set(required) - set(optional)
    if missing or extra:
        raise PlanError(f'{what}: missing {sorted(missing)}, unknown {sorted(extra)}')
    return obj


def free_extents(parts, first_usable, last_usable):
    out, cursor = [], first_usable
    for q in sorted(parts, key=lambda q: q['first_lba']):
        if q['first_lba'] > cursor:
            out.append((cursor, q['first_lba'] - 1))
        cursor = max(cursor, q['last_lba'] + 1)
    if cursor <= last_usable:
        out.append((cursor, last_usable))
    return out


def largest_aligned_free(extents, block):
    """Windows' LargestFreeExtent: free space counted in whole MiB-aligned bytes."""
    return max([max(0, (b + 1) * block // MIB * MIB - -(-a * block // MIB) * MIB) for a, b in extents], default=0)


def select_disk(survey, disk_guid=None, nvme_eui=None):
    if not isinstance(survey, dict):
        raise PlanError('survey is not a JSON object')
    disks = survey['disks'] if 'disks' in survey else [survey]
    if not isinstance(disks, list) or not all(isinstance(d, dict) for d in disks):
        raise PlanError('survey "disks" is not a list of objects')

    def guid_of(d):
        try:
            return str(uuid.UUID(d.get('disk_guid')))
        except (ValueError, AttributeError, TypeError):
            return None

    def eui_of(d):
        info = d.get('path_info')
        return info.get('nvme_eui_bytes') if isinstance(info, dict) else None

    if disk_guid is not None:
        disk_guid = _guid(disk_guid, 'disk GUID')
    if nvme_eui is not None:
        nvme_eui = _eui(nvme_eui)
    if len(disks) > 1 and disk_guid is None and nvme_eui is None:
        gpt = [f"{d.get('disk_bytes')} bytes, GPT GUID {guid_of(d)}" for d in disks if guid_of(d)]
        raise PlanError(f'{len(disks)} disks in the survey; name the target with --disk-guid and/or --nvme-eui '
                        f'(GPT disks: {"; ".join(gpt[:8]) or "none"})')
    hits = [d for d in disks if (disk_guid is None or guid_of(d) == disk_guid)
            and (nvme_eui is None or (eui_of(d) and _eui_eq(eui_of(d), nvme_eui)))]
    if len(hits) != 1:
        raise PlanError(f'expected exactly one disk matching the pins, found {len(hits)} of {len(disks)}')
    return hits[0]


def sp11_disk(d):
    """Validate one decoded disk on its own terms and return its facts, or raise PlanError.

    The decoder's `errors` are only the first line of defence: the primary/backup
    agreement, usable range, entry sanity and free-extent list are checked again
    here so that a survey edited to hide a problem is still refused."""
    try:
        return _sp11_disk(d)
    except (AttributeError, KeyError, TypeError, ValueError) as e:
        if isinstance(e, PlanError):
            raise
        raise PlanError(f'malformed survey: {type(e).__name__}: {e}') from None


def _sp11_disk(d):
    bad = []
    errs = d.get('errors')
    bad += ['survey carries no errors list'] if not isinstance(errs, list) else [f'survey error: {e}' for e in errs]
    bs, blocks, size, last_block = (_int(d.get(k), k) for k in ('block_size', 'blocks', 'disk_bytes', 'last_block'))
    if bs not in (512, 4096):
        bad.append(f'block size {bs} is neither 512 nor 4096')
    if blocks < 1 or size != blocks * bs or last_block != blocks - 1:
        bad.append('disk_bytes, blocks and last_block are inconsistent')
    hdr = {}
    for which in ('primary', 'backup'):
        h = d.get(which)
        if not isinstance(h, dict):
            bad.append(f'{which} GPT header is missing')
            continue
        hdr[which] = h
        if h.get('header_crc_ok') is not True or h.get('array_crc_ok') is not True:
            bad.append(f'{which} GPT header or array failed its CRC check, or was never checked')
        if (h.get('count'), h.get('entry_size')) != (128, 128):
            bad.append(f'{which} GPT is not 128 x 128-byte entries')
    if len(hdr) == 2 and not bad:
        p, b = hdr['primary'], hdr['backup']
        bad += [f'primary and backup GPT disagree on {k}'
                for k in ('disk_guid', 'first_usable', 'last_usable', 'count', 'entry_size', 'array_crc') if p.get(k) != b.get(k)]
        bad += [f'survey {k} differs from the primary header' for k in ('disk_guid', 'first_usable', 'last_usable') if d.get(k) != p.get(k)]
        if (p.get('my_lba'), p.get('alternate_lba'), b.get('my_lba'), b.get('alternate_lba')) != (1, last_block, last_block, 1):
            bad.append('the GPT headers do not sit at LBA 1 and the last LBA')
        first, last = _int(p.get('first_usable'), 'first_usable'), _int(p.get('last_usable'), 'last_usable')
        e1, e2, array = _int(p.get('entries_lba'), 'entries_lba'), _int(b.get('entries_lba'), 'backup entries_lba'), 16384 // bs
        if not (2 <= e1 and e1 + array <= first <= last < e2 and e2 + array <= last_block):
            bad.append('the usable LBA range is empty or overlaps a GPT header or array')
    if bad:
        raise PlanError('; '.join(bad))
    guid = _guid(p['disk_guid'], 'disk GUID')
    if guid == NIL:
        raise PlanError('the disk GUID is nil')
    raw = d.get('partitions')
    if not isinstance(raw, list):
        raise PlanError('survey carries no partition list')
    parts = []
    for q in raw:
        row = {k: q[k] for k in ENTRY_KEYS}
        for k in ('index', 'first_lba', 'last_lba', 'attributes'):
            _int(row[k], f'partition {k}')
        row['type'], row['guid'] = _guid(row['type'], 'partition type'), _guid(row['guid'], 'partition GUID')
        if not isinstance(row['name'], str):
            raise PlanError(f"partition {row['guid']}: name is not text")
        parts.append(row)
    parts.sort(key=lambda q: q['first_lba'])
    for q in parts:
        tag = f"partition {q['guid']}"
        if q['first_lba'] > q['last_lba']:
            bad.append(f'{tag}: first LBA after last LBA')
        if q['first_lba'] < first or q['last_lba'] > last:
            bad.append(f'{tag}: outside the usable LBA range {first}..{last}')
        if NIL in (q['guid'], q['type']):
            bad.append(f'{tag}: nil GUID or type')
        if not (0 <= q['index'] < 128 and 0 <= q['attributes'] < 1 << 64):
            bad.append(f'{tag}: slot or attributes out of range')
    bad += [f"partitions {a['guid']} and {c['guid']} overlap" for a, c in zip(parts, parts[1:]) if c['first_lba'] <= a['last_lba']]
    if len({q['guid'] for q in parts}) != len(parts):
        bad.append('two partitions share a GUID')
    if len({q['index'] for q in parts}) != len(parts):
        bad.append('two partitions claim the same GPT slot')
    free = free_extents(parts, first, last)
    if [(u['first_lba'], u['last_lba']) for u in d['unallocated']] != free:
        bad.append("the survey's unallocated list differs from the gaps between its partitions")
    info = d.get('path_info')
    raw_eui = info.get('nvme_eui_bytes') if isinstance(info, dict) else None
    if bad:
        raise PlanError('; '.join(bad))
    return dict(bs=bs, bytes=size, guid=guid, first_usable=first, last_usable=last, parts=parts, free=free,
                eui=_eui(raw_eui) if raw_eui else None, warnings=[str(w) for w in d.get('warnings') or []])


def plan_sp11(survey, extent=None, min_bytes=SP11_MIN_BYTES, new_type=TYPE, new_name=NAME,
              disk_guid=None, nvme_eui=None, require_eui=True):
    f = sp11_disk(select_disk(survey, disk_guid, nvme_eui))
    bs, parts = f['bs'], f['parts']
    if require_eui and f['eui'] is None:
        raise PlanError('the survey carries no NVMe EUI64 (a raw capture has none), so the disk cannot be pinned by '
                        'device; pass --no-eui to rely on the GPT disk GUID alone')
    if _int(min_bytes, 'minimum size') < 1:
        raise PlanError('the minimum size must be a positive number of bytes')
    new_type = _guid(new_type, 'new partition type')
    if new_type == NIL or new_type in WINDOWS_TYPES:
        raise PlanError('the new partition type must not be nil or a Windows ESP/MSR/basic-data/recovery type')
    if any(q['type'] == new_type for q in parts):
        raise PlanError('a partition of the new type already exists; nothing to plan')
    if not isinstance(new_name, str) or not new_name or not new_name.isprintable() or len(new_name.encode('utf-16-le')) > 72:
        raise PlanError('the new partition name must be 1..36 printable UTF-16 code units')
    types = {q['type'] for q in parts}
    if T_ESP not in types or T_BASIC not in types:
        raise PlanError('no EFI system partition or no basic-data partition: this is not a Windows boot disk')
    free = f['free']
    span = lambda e: (e[1] - e[0] + 1) * bs
    listing = lambda es: '; '.join(f'LBA {a}..{b} ({span((a, b)) / (1 << 30):.3f} GiB)' for a, b in es) or 'none'
    if extent is not None:
        extent = tuple(_int(x, 'extent bound') for x in extent)
        if extent not in free:
            raise PlanError(f'LBA {extent[0]}..{extent[1]} is not a whole free extent; free extents: {listing(free)}')
        lo, hi = extent
    else:
        fits = [e for e in free if span(e) - 2 * GUARD_BYTES >= min_bytes]
        if not fits:
            raise PlanError(f'no free extent holds {min_bytes} bytes plus two guards; free extents: {listing(free)}')
        if len(fits) > 1:
            raise AmbiguousExtent(f'{len(fits)} free extents qualify ({listing(fits)}); name one with '
                                  f'--extent-start/--extent-end', fits)
        lo, hi = fits[0]
    if lo <= f['first_usable']:
        raise PlanError(f"the extent starts at the first usable LBA {f['first_usable']}")
    if hi >= f['last_usable']:
        raise PlanError(f"the extent reaches the last usable LBA {f['last_usable']}, beside the backup GPT")
    guard = GUARD_BYTES // bs
    first, last = lo + guard, hi - guard
    size = (last - first + 1) * bs
    if first > last or size < min_bytes:
        raise PlanError(f'the extent gives {max(size, 0)} bytes inside its guards, {min_bytes} required')
    if first * bs % ALIGN_BYTES or size % ALIGN_BYTES:
        raise PlanError(f'the guarded partition (offset {first * bs}, size {size}) is not {ALIGN_BYTES}-byte aligned')
    before, after = [q for q in parts if q['last_lba'] == lo - 1], [q for q in parts if q['first_lba'] == hi + 1]
    if len(before) != 1 or len(after) != 1:
        raise PlanError('the extent is not bounded by one partition on each side')
    if before[0]['type'] != T_BASIC:
        raise PlanError('the partition before the extent is not basic data: the extent does not come from shrinking a Windows volume')
    slots = [i for i in range(128) if i not in {q['index'] for q in parts}]
    if not slots:
        raise PlanError('all 128 GPT slots are in use')
    notes = []
    if T_MSR not in types:
        notes.append('no Microsoft reserved partition (unusual for a Windows GPT disk)')
    if T_RECOVERY not in types:
        notes.append('no Windows recovery partition')
    elif parts[-1]['type'] != T_RECOVERY:
        notes.append('the last partition is not a Windows recovery partition')
    if sum(q['type'] == T_BASIC for q in parts) > 1:
        notes.append('several basic-data partitions: confirm the one before the extent is the volume that was shrunk')
    if after[0]['type'] != T_RECOVERY:
        notes.append('the partition after the extent is not a Windows recovery partition')
    notes += [f'survey warning: {w[:200]}' for w in f['warnings']]
    return dict(
        profile='sp11', format=1,
        disk=dict(bytes=f['bytes'], block=bs, gpt_guid=f['guid'], eui=f['eui'],
                  first_usable=f['first_usable'], last_usable=f['last_usable']),
        protected=[dict(q) for q in parts],
        extent=dict(first_lba=lo, last_lba=hi, offset=lo * bs, bytes=span((lo, hi)),
                    before_neighbour=before[0]['guid'], after_neighbour=after[0]['guid']),
        new=dict(expected_index=slots[0], type=new_type, name=new_name, first_lba=first, last_lba=last,
                 offset=first * bs, bytes=size, end=(last + 1) * bs, guard_bytes=GUARD_BYTES, alignment_bytes=ALIGN_BYTES),
        constraints=dict(min_bytes=min_bytes, alignment_bytes=ALIGN_BYTES, guard_bytes=GUARD_BYTES),
        layout=dict(notes=notes),
        safety=dict(SAFETY),
    )


def plan_text(p):
    n, e, s = p['new'], p['extent'], p['safety']
    gib = lambda b: f'{b / (1 << 30):.3f} GiB'
    return [f"new partition: LBA {n['first_lba']}..{n['last_lba']} ({n['last_lba'] - n['first_lba'] + 1} blocks of "
            f"{p['disk']['block']} bytes), offset {n['offset']}, {n['bytes']} bytes ({gib(n['bytes'])}), type {n['type']}, "
            f"slot hint {n['expected_index']}",
            f"guards: {n['guard_bytes']} bytes before and after; {n['alignment_bytes']}-byte alignment; "
            f"extent LBA {e['first_lba']}..{e['last_lba']} ({gib(e['bytes'])})"] + \
           [f'note: {x}' for x in p['layout']['notes']] + \
           ['REQUIRES AUTHORIZATION: ' + s['authorization'], 'REQUIRES A VERIFIED BACKUP: ' + s['backup'],
            'BitLocker: ' + s['bitlocker_note'], s['executor_note'] + '; ' + s['operator_note'],
            'This plan is numbers to check against, not a script: it emits no command.']


def _read_file(path, limit):
    """Read one regular file as text. A device node, pipe or oversized file is refused unopened.

    Windows PowerShell 5.1 redirects write UTF-16 with a BOM, Set-Content -Encoding UTF8 a UTF-8 BOM."""
    path = Path(path)
    try:
        st = path.stat()
        if not stat.S_ISREG(st.st_mode):
            raise PlanError(f'{path.name}: not a regular file')
        if st.st_size > limit:
            raise PlanError(f'{path.name}: larger than {limit} bytes')
        data = path.read_bytes()
    except OSError as e:
        raise PlanError(f'{path.name}: cannot be read ({type(e).__name__})') from None
    return data.decode('utf-16' if data[:2] in (b'\xff\xfe', b'\xfe\xff') else 'utf-8-sig', errors='replace')


def _load_json(path):
    # Read outside the try: PlanError is a ValueError, and the read errors must keep their own message.
    text = _read_file(path, 32 << 20)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise PlanError(f'{Path(path).name}: not JSON ({e.msg}, line {e.lineno})') from None
    except (RecursionError, ValueError):
        raise PlanError(f'{Path(path).name}: JSON nested too deeply or a number too long') from None


def _write_file(path, text):
    path = Path(path)
    target = path.resolve()
    if target.parts[:2] == ('/', 'dev') or (target.exists() and not target.is_file()):
        raise PlanError(f'{path.name}: refusing to write to a device or special file')
    try:
        path.write_text(text)
    except OSError as e:
        raise PlanError(f'{path.name}: cannot be written ({type(e).__name__})') from None


# --- reader records ---------------------------------------------------------
# Every reader document becomes {kind, label, disk, parts, facts}: `disk` and each
# part hold only what that reader actually states, and only stated facts are compared.

def _record_full(doc, label, plan_guid):
    f = sp11_disk(select_disk(doc, plan_guid))
    disk = dict(block_size=f['bs'], disk_bytes=f['bytes'], disk_guid=f['guid'],
                first_usable=f['first_usable'], last_usable=f['last_usable'])
    if f['eui']:
        disk['nvme_eui'] = f['eui']
    parts = [{k: q[k] for k in ('first_lba', 'last_lba', 'guid', 'type', 'attributes', 'name')} for q in f['parts']]
    return dict(kind='full', label=label, disk=disk, parts=parts, facts=f)


def _record_windows(doc, label):
    disk, rows = doc.get('Disk'), doc.get('Partitions')
    if not isinstance(disk, dict) or not isinstance(rows, list):
        raise PlanError('a Windows record needs "Disk" and "Partitions"')
    bs = _int(disk.get('LogicalSectorSize'), 'Disk.LogicalSectorSize')
    if bs not in (512, 4096):
        raise PlanError(f'Disk.LogicalSectorSize {bs} is neither 512 nor 4096')
    out = dict(block_size=bs)
    for key, name in (('Size', 'disk_bytes'), ('LargestFreeExtent', 'largest_free_extent')):
        if disk.get(key) is not None:
            out[name] = _int(disk[key], f'Disk.{key}')
    if disk.get('Guid') is not None:
        out['disk_guid'] = _guid(disk['Guid'], 'Disk.Guid')
    if disk.get('PartitionStyle') not in (None, 'GPT', 2):
        raise PlanError(f"Disk.PartitionStyle {disk['PartitionStyle']!r} is not GPT")
    uid = disk.get('UniqueId')
    if isinstance(uid, str) and uid.lower().startswith('eui.'):
        out['nvme_eui'] = _eui(uid)
    parts = []
    for i, q in enumerate(rows):
        if not isinstance(q, dict):
            raise PlanError(f'Partitions[{i}] is not an object')
        offset, size = _int(q.get('Offset'), f'Partitions[{i}].Offset'), _int(q.get('Size'), f'Partitions[{i}].Size')
        if offset < 0 or size < 1 or offset % bs or size % bs:
            raise PlanError(f'Partitions[{i}]: offset {offset} and size {size} are not whole sectors of {bs} bytes')
        parts.append(dict(first_lba=offset // bs, last_lba=(offset + size) // bs - 1,
                          guid=_guid(q.get('Guid'), f'Partitions[{i}].Guid'),
                          type=_guid(q.get('GptType'), f'Partitions[{i}].GptType')))
    return dict(kind='windows', label=label, disk=out, parts=parts, facts=None)


def _record_normalized(doc):
    _keys(doc, ('format', 'reader', 'disk', 'partitions'), ('mbr',), 'reader document')
    mbr = doc.get('mbr', 'unchecked')
    if mbr not in MBR_STATES:
        raise PlanError(f'the reader "mbr" must be one of {list(MBR_STATES)}, not {mbr!r}')
    d = _keys(doc['disk'], ('block_size',), ('disk_bytes', 'disk_guid', 'nvme_eui', 'first_usable', 'last_usable',
                                             'partition_style'), 'reader "disk"')
    if d.get('partition_style', 'GPT') != 'GPT':
        raise PlanError('the reader disk is not GPT')
    disk = dict(block_size=_int(d['block_size'], 'block_size'))
    if disk['block_size'] not in (512, 4096):
        raise PlanError(f"the reader block size {disk['block_size']} is neither 512 nor 4096")
    for k in ('disk_bytes', 'first_usable', 'last_usable'):
        if k in d:
            disk[k] = _int(d[k], k)
    if 'disk_guid' in d:
        disk['disk_guid'] = _guid(d['disk_guid'], 'disk_guid')
    if 'nvme_eui' in d:
        disk['nvme_eui'] = _eui(d['nvme_eui'])
    if not isinstance(doc['partitions'], list):
        raise PlanError('the reader "partitions" is not a list')
    parts = []
    for i, q in enumerate(doc['partitions']):
        q = _keys(q, ('first_lba', 'last_lba'), ('index', 'guid', 'type', 'attributes', 'name', 'gdisk_type_code'),
                  f'reader partition {i}')
        row = dict(first_lba=_int(q['first_lba'], 'first_lba'), last_lba=_int(q['last_lba'], 'last_lba'))
        if not 0 <= row['first_lba'] <= row['last_lba']:
            raise PlanError(f"reader partition {i}: LBA range {row['first_lba']}..{row['last_lba']} is not valid")
        for k in ('guid', 'type'):
            if k in q:
                row[k] = _guid(q[k], f'reader partition {i} {k}')
        if 'attributes' in q:
            row['attributes'] = _int(q['attributes'], 'attributes')
            if not 0 <= row['attributes'] < 1 << 64:
                raise PlanError(f'reader partition {i}: attributes out of range')
        if 'name' in q:
            if not isinstance(q['name'], str):
                raise PlanError(f'reader partition {i}: name is not text')
            row['name'] = q['name']
        if 'gdisk_type_code' in q:
            if not (isinstance(q['gdisk_type_code'], str) and re.fullmatch(r'[0-9A-F]{4}', q['gdisk_type_code'])):
                raise PlanError(f'reader partition {i}: gdisk_type_code is not four hex digits')
            row['gdisk_type_code'] = q['gdisk_type_code']
        if 'index' in q:
            _int(q['index'], 'index')
        parts.append(row)
    return dict(kind='normalized', label=_label(doc['reader']), disk=disk, parts=parts, facts=None, mbr=mbr)


def load_reader(doc, label, plan_guid):
    """Classify and parse one reader document; its own "reader" field, when present, names it."""
    if not isinstance(doc, dict):
        raise PlanError('not a JSON object')
    try:
        if doc.get('format') == READER_FORMAT:
            return _record_normalized(doc)
        if 'Disk' in doc or 'Partitions' in doc:
            rec = _record_windows(doc, label)
        elif 'disks' in doc or 'partitions' in doc:
            rec = _record_full(doc, label, plan_guid)
        else:
            raise PlanError('not a gpt_survey JSON, a Windows record or a snapmac-reader/1 document')
        if 'reader' in doc:
            rec['label'] = _label(doc['reader'])
        return rec
    except (AttributeError, KeyError, TypeError, ValueError) as e:
        if isinstance(e, PlanError):
            raise
        raise PlanError(f'malformed reader document: {type(e).__name__}: {e}') from None


def _code_ok(code, type_guid):
    expected = next((c for c, t in GDISK_CODES.items() if t == type_guid), None)
    return code == expected if expected else code not in GDISK_CODES


def _project(rec, want):
    """Disagreements between one reader and the expected layout; empty means it agrees."""
    bad, d, bs = [], rec['disk'], want['bs']
    for k, w in (('block_size', bs), ('disk_bytes', want['bytes']), ('disk_guid', want['guid']),
                 ('first_usable', want['first_usable']), ('last_usable', want['last_usable'])):
        if k in d and d[k] != w:
            bad.append(f'{k} is {d[k]}, expected {w}')
    if want['eui'] and 'nvme_eui' in d and not _eui_eq(want['eui'], d['nvme_eui']):
        bad.append('NVMe EUI64 differs from the plan')
    largest = largest_aligned_free(want['free'], bs)
    if 'largest_free_extent' in d and d['largest_free_extent'] != largest:
        bad.append(f"largest free extent is {d['largest_free_extent']} bytes, expected {largest}")
    have, twice = {}, set()
    for q in rec['parts']:
        key = (q['first_lba'], q['last_lba'])
        twice |= {key} if key in have else set()
        have[key] = q
    expect = {(q['first_lba'], q['last_lba']): q for q in want['parts']}
    span = lambda k: f'LBA {k[0]}..{k[1]}'
    bad += [f'two entries at {span(k)}' for k in sorted(twice)]
    bad += [f'no entry at {span(k)}' for k in sorted(set(expect) - set(have))]
    bad += [f'unexpected entry at {span(k)}' for k in sorted(set(have) - set(expect))]
    for k in sorted(set(expect) & set(have)):
        e, g = expect[k], have[k]
        bad += [f'entry at {span(k)}: {f} differs' for f in ('guid', 'type', 'attributes', 'name')
                if f in e and f in g and e[f] != g[f]]
        if 'gdisk_type_code' in g and not _code_ok(g['gdisk_type_code'], e['type']):
            bad.append(f"entry at {span(k)}: gdisk type code {g['gdisk_type_code']} does not fit type {e['type']}")
    return bad


def _strict(mode, f, bf, p):
    """Checks only a full survey allows: identity by GUID, one new entry, the guards, slot moves."""
    bad, notes = [], []
    old, new = {q['guid']: q for q in bf['parts']}, {q['guid']: q for q in f['parts']}
    for g, q in old.items():
        n = new.get(g)
        if n is None:
            bad.append(f'existing partition {g} is gone')
        elif not same(n, q):
            bad.append(f"existing partition {g} changed: {', '.join(k for k in IDENTITY_KEYS if n[k] != q[k])}")
        elif n['index'] != q['index']:
            notes.append(f"partition {g} moved from GPT slot {q['index']} to {n['index']} (identity unchanged)")
    added = [q for g, q in new.items() if g not in old]
    if mode == 'verify-undo':
        if added:
            bad.append(f'{len(added)} partition(s) that BEFORE does not have remain')
        if f['free'] != bf['free']:
            bad.append('free extents differ from BEFORE')
        return bad, notes, added
    if len(added) != 1:
        bad.append(f'expected one new partition, found {len(added)}')
        return bad, notes, added
    n, want = added[0], p['new']
    if (n['type'], n['first_lba'], n['last_lba']) != (want['type'], want['first_lba'], want['last_lba']):
        bad.append(f"the new partition is LBA {n['first_lba']}..{n['last_lba']} of type {n['type']}, planned "
                   f"LBA {want['first_lba']}..{want['last_lba']} of type {want['type']}")
    if n['attributes'] not in ALLOWED_ATTRS:
        bad.append(f"the new partition has attributes {n['attributes']:#x}")
    lo, hi = p['extent']['first_lba'], p['extent']['last_lba']
    inside = [e for e in f['free'] if lo <= e[0] <= hi]
    guards = [(lo, want['first_lba'] - 1), (want['last_lba'] + 1, hi)]
    if inside != guards:
        bad.append(f'free space inside the old extent is {inside}, expected only the guards {guards}')
    return bad, notes, added


def _plan_mismatch(before, p):
    """The plan must be exactly what BEFORE yields for the plan's own parameters."""
    try:
        again = plan_sp11(before, (p['extent']['first_lba'], p['extent']['last_lba']), p['constraints']['min_bytes'],
                          p['new']['type'], p['new']['name'], p['disk']['gpt_guid'], p['disk']['eui'],
                          p['disk']['eui'] is not None)
    except (KeyError, TypeError, AttributeError) as e:
        return [f'the plan is malformed: {type(e).__name__}: {e}']
    except PlanError as e:
        return [f'the plan cannot be reproduced from BEFORE: {e}']
    if json.loads(json.dumps(again)) != p:
        sections = sorted(k for k in set(again) | set(p) if json.loads(json.dumps(again.get(k))) != p.get(k))
        return [f"the plan differs from what BEFORE yields for its own parameters (sections: {', '.join(sections)})"]
    return []


def independent_documents_needed(min_readers):
    """How many reader documents must differ in content (their label aside) for `min_readers` readers to count.

    All but one: the plan's fourth reader, a Windows record taken after a normal boot, can legitimately repeat the
    first byte for byte. Copies of one document under different labels are one observation, however many labels."""
    return max(1, min_readers - 1)


def _verify_core(mode, before, readers, p, min_readers, require_eui, notes, verdict, disk_guid=None, nvme_eui=None):
    notes = [] if notes is None else notes
    verdict = {} if verdict is None else verdict
    if _int(min_readers, 'the minimum reader count') < 1:
        raise PlanError('the minimum reader count must be at least 1')
    if p is not None:
        if not isinstance(p, dict) or p.get('profile') != 'sp11' or not isinstance(p.get('disk'), dict):
            raise PlanError('the plan is not an sp11 plan')
        disk_guid, nvme_eui = p['disk'].get('gpt_guid'), p['disk'].get('eui')
    bf = sp11_disk(select_disk(before, disk_guid, nvme_eui))
    info = dict(supplied=len(readers), distinct=0, agreeing=0, required=min_readers, labels=[], full=[],
                four_reader_gate_met=False)

    def finish(problems, new=None):
        verdict.update(profile='sp11', format=1, mode=mode, ok=not problems, problems=list(problems),
                       notes=list(notes), readers=info, new_partition=new)
        return problems

    mismatch = _plan_mismatch(before, p) if p is not None else []
    if mismatch:
        return finish(mismatch)
    entries, seen_docs, seen_labels = [], {}, {}
    for n, item in enumerate(readers, 1):
        label, doc = item if isinstance(item, tuple) else (f'reader-{n}', item)
        r = dict(label=label, rec=None, bad=[])
        entries.append(r)
        if isinstance(doc, PlanError):
            r['bad'].append(str(doc))
            continue
        try:
            rec = load_reader(doc, _label(label), bf['guid'])
        except PlanError as e:
            r['bad'].append(str(e))
            continue
        r['label'] = rec['label']
        key = json.dumps(doc, sort_keys=True, default=repr)
        if key in seen_docs:
            r['bad'].append(f'the same document as reader {seen_docs[key]}; a document counts once')
        elif rec['label'] in seen_labels:
            r['bad'].append('its label is already used by another reader; give each reader its own')
        else:
            seen_docs[key] = seen_labels[rec['label']] = rec['label']
            # Independence is judged by content: the same record under another label is one observation.
            r['content'] = json.dumps({k: v for k, v in doc.items() if k != 'reader'}, sort_keys=True, default=repr)
            r['rec'] = rec
    recs = [r for r in entries if r['rec']]
    fulls = [r for r in recs if r['rec']['kind'] == 'full']
    expected = [dict(q) for q in bf['parts']]
    new_row = new_info = None
    if mode == 'verify':
        new_row = dict(first_lba=p['new']['first_lba'], last_lba=p['new']['last_lba'], type=p['new']['type'])
        expected.append(new_row)
    for r in fulls:
        bad, moved, added = _strict(mode, r['rec']['facts'], bf, p)
        r['bad'] += bad
        notes.extend(f"reader {r['label']}: {x}" for x in moved)
        if mode == 'verify' and len(added) == 1 and new_info is None:
            a = added[0]
            new_row.update(guid=a['guid'], attributes=a['attributes'], name=a['name'])
            new_info = {k: a[k] for k in ('guid', 'index', 'attributes', 'name', 'first_lba', 'last_lba')}
    want = dict(bs=bf['bs'], bytes=bf['bytes'], guid=bf['guid'], first_usable=bf['first_usable'],
                last_usable=bf['last_usable'], eui=bf['eui'], parts=expected,
                free=free_extents(expected, bf['first_usable'], bf['last_usable']))
    for r in recs:
        r['bad'] += _project(r['rec'], want)
    problems = [f"reader {r['label']}: {x}" for r in entries for x in r['bad']]
    if not fulls:
        problems.append('no full reader: at least one gpt_survey JSON is needed for the strict checks')
    if len(recs) < min_readers:
        problems.append(f'{len(recs)} distinct reader document(s), {min_readers} required')
    contents = {r['content'] for r in recs}
    independent_needed = independent_documents_needed(min_readers)
    if len(recs) >= min_readers and len(contents) < independent_needed:
        problems.append(f'only {len(contents)} of the {len(recs)} reader documents differ in content (copies that '
                        f'differ only in their label count once), {independent_needed} required')
    if bf['eui'] and not any('nvme_eui' in r['rec']['disk'] for r in recs):
        msg = 'no reader carries the NVMe EUI64, so the disk is pinned by its GPT GUID alone'
        if require_eui:
            problems.append(msg + ' (--no-eui accepts that)')
        else:
            notes.append(msg)
    if min_readers < SP11_MIN_READERS:
        notes.append(f'partial: --min-readers {min_readers} is below the {SP11_MIN_READERS} readers the X6 gate wants')
    agreeing = sum(1 for r in recs if not r['bad'])
    checked = [r['label'] for r in recs if r['rec'].get('mbr') == 'protective']
    info.update(distinct=len(recs), agreeing=agreeing, labels=[r['label'] for r in recs],
                full=[r['label'] for r in fulls], independent=len(contents),
                mbr_checked=checked, mbr_unchecked=[r['label'] for r in recs if r['label'] not in checked],
                four_reader_gate_met=(not problems and agreeing >= SP11_MIN_READERS
                                      and len(contents) >= independent_documents_needed(SP11_MIN_READERS)))
    return finish(problems, new_info)


def verify_sp11(before, readers, p, min_readers=SP11_MIN_READERS, require_eui=True, notes=None, verdict=None):
    """Problems found, [] meaning every reader agrees the change is exactly `p`.

    `readers` is a list of (label, document) pairs; a document may also be the PlanError
    that reading its file raised. A refusal (unusable BEFORE or plan) raises PlanError."""
    if p is None:
        raise PlanError('verify needs the plan')
    return _verify_core('verify', before, readers, p, min_readers, require_eui, notes, verdict)


def verify_undo_sp11(before, readers, p=None, min_readers=SP11_MIN_READERS, require_eui=True, notes=None,
                     verdict=None, disk_guid=None, nvme_eui=None):
    return _verify_core('verify-undo', before, readers, p, min_readers, require_eui, notes, verdict, disk_guid, nvme_eui)


# --- text converters --------------------------------------------------------
# They parse captured text only, refuse any line they do not recognise, and never
# store names, device paths or models. The output is checked through the same
# parser that reads it back.

UNITS = {'bytes': 1, 'KiB': 1 << 10, 'MiB': 1 << 20, 'GiB': 1 << 30, 'TiB': 1 << 40, 'PiB': 1 << 50, 'EiB': 1 << 60}
UNIT_RE = r'([0-9]+(?:\.[0-9]+)?) (bytes|[KMGTPE]iB)'


def _printed_size_ok(number, unit, exact_bytes):
    """gdisk prints sizes to one decimal place: this catches a wrong sector size, not an off-by-one LBA."""
    return abs(float(number) * UNITS[unit] - exact_bytes) <= 0.101 * UNITS[unit]


def _reconcile_block_size(stated, given):
    if stated is not None and given is not None and stated != given:
        raise PlanError(f'the text says {stated}-byte sectors but --block-size is {given}')
    bs = stated if stated is not None else given
    if bs is None:
        raise PlanError('the text does not state the sector size: pass --block-size 512 or 4096')
    if bs not in (512, 4096):
        raise PlanError(f'sector size {bs} is neither 512 nor 4096')
    return bs


def convert_sgdisk(text, label, block_size=None):
    """`sgdisk -p` / `gdisk -l` text (capture with 2>&1) to a snapmac-reader/1 document."""
    seen, rows, in_table = {}, [], False
    patterns = (('disk', rf'Disk (.+): (\d+) sectors, {UNIT_RE}'),
                ('sector', r'Sector size \(logical/physical\): (\d+)/\d+ bytes'),
                ('sector', r'Sector size \(logical\): (\d+) bytes'),
                ('sector', r'Logical sector size: (\d+) bytes'),
                ('guid', r'Disk identifier \(GUID\): ([0-9A-Fa-f-]{36})'),
                ('entries', r'Partition table holds up to (\d+) entries'),
                ('table', r'Main partition table begins at sector (\d+) and ends at sector (\d+)'),
                ('usable', r'First usable sector is (\d+), last usable sector is (\d+)'),
                ('align', r'Partitions will be aligned on (\d+)-sector boundaries'),
                ('free', rf'Total free space is (\d+) sectors \({UNIT_RE}\)'))
    scan_block = ('Partition table scan:', 'MBR: protective', 'BSD: not present', 'APM: not present', 'GPT: present')
    ignored = ('Found valid GPT with protective MBR; using GPT.',)
    scan = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if in_table:
            m = re.fullmatch(rf'(\d+)\s+(\d+)\s+(\d+)\s+{UNIT_RE}\s+([0-9A-F]{{4}})(?:\s+.*)?', line)
            if not m:
                raise PlanError(f'sgdisk text: unrecognised partition row "{line[:60]}"')
            rows.append(m.groups())
        elif line in scan_block:
            scan.append(line)
        elif line in ignored or re.fullmatch(r'GPT fdisk \((?:gdisk|sgdisk)\) version \S+|Model: .*', line):
            continue
        elif re.fullmatch(r'Number\s+Start \(sector\)\s+End \(sector\)\s+Size\s+Code\s+Name', line):
            in_table = True
        elif line.startswith(('MBR:', 'BSD:', 'APM:', 'GPT:')):
            raise PlanError(f'sgdisk text: "{line}": only a protective MBR over a valid GPT is accepted '
                            f'(a hybrid or other MBR is refused)')
        elif re.match(r'(Warning|Caution|Problem|Error|Found )', line):
            raise PlanError(f'sgdisk text reports "{line[:60]}"; resolve that first')
        else:
            for key, pattern in patterns:
                m = re.fullmatch(pattern, line)
                if m:
                    if key in seen:
                        raise PlanError(f'sgdisk text: "{key}" appears twice')
                    seen[key] = m.groups()
                    break
            else:
                raise PlanError(f'sgdisk text: unrecognised line "{line[:60]}"')
    if scan and sorted(scan) != sorted(scan_block):
        # A dropped "MBR:" line would hide a hybrid MBR; text without the block says nothing about the MBR at all.
        raise PlanError('sgdisk text: the "Partition table scan:" block is incomplete or repeated (it must show a '
                        'protective MBR, no BSD or APM label, and a GPT, once each)')
    missing = [k for k in ('disk', 'guid', 'entries', 'table', 'usable', 'free') if k not in seen]
    if missing or not in_table:
        raise PlanError(f"sgdisk text lacks {missing + ([] if in_table else ['the partition table header'])}")
    bs = _reconcile_block_size(int(seen['sector'][0]) if 'sector' in seen else None, block_size)
    sectors = int(seen['disk'][1])
    if not _printed_size_ok(seen['disk'][2], seen['disk'][3], sectors * bs):
        raise PlanError(f'the printed disk size does not fit {sectors} sectors of {bs} bytes')
    array = 16384 // bs
    start, end = (int(x) for x in seen['table'])
    first, last = (int(x) for x in seen['usable'])
    if int(seen['entries'][0]) != 128 or end - start + 1 != array:
        raise PlanError('the table is not 128 entries of 128 bytes')
    if not 2 <= start <= end < first <= last <= sectors - 2 - array:
        raise PlanError('the table position or usable range does not fit the disk')
    parts, used = [], 0
    for n, s, e, number, unit, code in sorted(rows, key=lambda r: int(r[1])):
        s, e = int(s), int(e)
        if not (first <= s <= e <= last) or (parts and s <= parts[-1]['last_lba']):
            raise PlanError(f'partition {n} lies outside the usable range or overlaps another')
        if not _printed_size_ok(number, unit, (e - s + 1) * bs):
            raise PlanError(f'partition {n}: the printed size does not fit {e - s + 1} sectors of {bs} bytes')
        parts.append(dict(first_lba=s, last_lba=e, gdisk_type_code=code))
        used += e - s + 1
    if len({r[0] for r in rows}) != len(rows) or not all(1 <= int(r[0]) <= 128 for r in rows):
        raise PlanError('partition numbers repeat or fall outside 1..128')
    if int(seen['free'][0]) != last - first + 1 - used or not _printed_size_ok(
            seen['free'][1], seen['free'][2], int(seen['free'][0]) * bs):
        raise PlanError('the stated free space does not match the partitions')
    doc = dict(format=READER_FORMAT, reader=_label(label),
               disk=dict(block_size=bs, disk_bytes=sectors * bs, disk_guid=_guid(seen['guid'][0], 'disk identifier'),
                         first_usable=first, last_usable=last, partition_style='GPT'),
               partitions=parts)
    if scan:
        # Only the scan block of `gdisk -l` states that the MBR is protective (a hybrid MBR is refused above);
        # `sgdisk -p` prints no such block, so its text says nothing about the MBR and the reader stays unchecked.
        doc['mbr'] = 'protective'
    _record_normalized(doc)
    return doc


def convert_gpt_show(text, label, block_size=None):
    """macOS `gpt show` text (read-only listing) to a snapmac-reader/1 document."""
    rows, stated, header = [], None, False
    uuid_re = r'[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}'
    anchors = ('PMBR', 'Pri GPT header', 'Pri GPT table', 'Sec GPT table', 'Sec GPT header')
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        if re.fullmatch(r'start\s+size\s+index\s+contents', s):
            if header or rows:
                raise PlanError('gpt show text: the column header is repeated or late')
            header = True
        elif (m := re.fullmatch(r'gpt show: .+: mediasize=(\d+); sectorsize=(\d+); blocks=(\d+)', s)):
            if stated:
                raise PlanError('gpt show text: the media line appears twice')
            stated = tuple(int(x) for x in m.groups())
        elif re.fullmatch(r'(?:PMBR|Pri GPT|Sec GPT) at sector \d+', s):
            continue
        elif (m := re.fullmatch(rf'(\d+)\s+(\d+)\s+(\d+)\s+GPT part - ({uuid_re})', s)):
            rows.append(('part', int(m[1]), int(m[2]), int(m[3]), m[4]))
        elif (m := re.fullmatch(rf"(\d+)\s+(\d+)\s+({'|'.join(anchors)})", s)):
            rows.append((m[3], int(m[1]), int(m[2])))
        elif (m := re.fullmatch(r'(\d+)\s+(\d+)', s)):
            rows.append(('free', int(m[1]), int(m[2])))
        else:
            raise PlanError(f'gpt show text: unrecognised line "{s[:60]}" (an MBR row or a label listing is refused)')
    if not header or not rows:
        raise PlanError('gpt show text: no column header or no rows')
    position = 0
    for r in rows:
        if r[1] != position or r[2] < 1:
            raise PlanError('gpt show text: the rows are not contiguous from sector 0')
        position += r[2]
    total = position
    bs = _reconcile_block_size(stated[1] if stated else None, block_size)
    if stated and (stated[0] != total * bs or stated[2] != total):
        raise PlanError('gpt show text: the media line does not match the rows')
    if [r[0] for r in rows if r[0] in anchors] != list(anchors):
        raise PlanError('gpt show text: PMBR, both GPT headers and both tables must each appear once, in order')
    array = 16384 // bs
    at = {r[0]: (r[1], r[2]) for r in rows if r[0] in anchors}
    if at != {'PMBR': (0, 1), 'Pri GPT header': (1, 1), 'Pri GPT table': (2, array),
              'Sec GPT table': (total - 1 - array, array), 'Sec GPT header': (total - 1, 1)}:
        raise PlanError('gpt show text: the GPT headers and tables are not where a 128-entry GPT puts them')
    parts = []
    for r in (r for r in rows if r[0] == 'part'):
        if not (2 + array <= r[1] and r[1] + r[2] <= total - 1 - array) or not 1 <= r[3] <= 128:
            raise PlanError('gpt show text: a partition lies outside the usable area or has a bad index')
        parts.append(dict(first_lba=r[1], last_lba=r[1] + r[2] - 1, type=_guid(r[4], 'partition type')))
    if len({r[3] for r in rows if r[0] == 'part'}) != len(parts):
        raise PlanError('gpt show text: a partition index repeats')
    doc = dict(format=READER_FORMAT, reader=_label(label),
               disk=dict(block_size=bs, disk_bytes=total * bs, partition_style='GPT'), partitions=parts)
    _record_normalized(doc)
    return doc


CONVERTERS = {'sgdisk': convert_sgdisk, 'gpt-show': convert_gpt_show}


# --- command line -----------------------------------------------------------

SP11_ONLY = {'plan': ('extent_start', 'extent_end', 'min_bytes', 'new_type', 'new_name', 'disk_guid', 'nvme_eui'),
             'verify': ('min_readers', 'out'),
             'verify-undo': ('min_readers', 'out', 'plan', 'no_eui', 'disk_guid', 'nvme_eui')}


def add_sp11_arguments(pp, vp, up, cp):
    for p in (pp, vp, up):
        p.add_argument('--profile', choices=('a16', 'sp11'), default='a16',
                       help='a16 (default): the A16 disk, unchanged. sp11: a Windows GPT disk read from the survey')
    for p in (pp, up):
        p.add_argument('--disk-guid', help='sp11: GPT disk GUID, to pick the disk when the survey holds several')
        p.add_argument('--nvme-eui', help='sp11: NVMe EUI64, to pick the disk when the survey holds several')
    pp.add_argument('--extent-start', type=int, help='sp11: first LBA of the free extent to use (with --extent-end)')
    pp.add_argument('--extent-end', type=int, help='sp11: last LBA of the free extent to use (with --extent-start)')
    pp.add_argument('--min-bytes', type=int, help=f'sp11: smallest new partition in bytes (default {SP11_MIN_BYTES})')
    pp.add_argument('--new-type', help='sp11: GPT type GUID of the new partition (default: the Snapintosh type)')
    pp.add_argument('--new-name', help='sp11: label for the new partition (default: the Snapintosh name)')
    for p in (vp, up):
        p.add_argument('--min-readers', type=int, help=f'sp11: distinct reader documents required (default {SP11_MIN_READERS})')
        p.add_argument('--out', type=Path, help='sp11: write the verdict JSON here')
    up.add_argument('--plan', type=Path, help='sp11: the plan, to take the disk pins from it')
    up.add_argument('--no-eui', action='store_true', help='sp11: accept readers that carry no NVMe EUI64')
    cp.add_argument('--from', dest='fmt', choices=sorted(CONVERTERS), required=True)
    cp.add_argument('--in', dest='source', type=Path, required=True, help='captured text file (never a device)')
    cp.add_argument('--block-size', type=int, help='sector size, when the text does not state it')
    cp.add_argument('--label', required=True, help='name of this reader: [A-Za-z0-9._-]{1,48}')
    cp.add_argument('--out', type=Path, required=True)


def convert_main(args):
    try:
        doc = CONVERTERS[args.fmt](_read_file(args.source, 1 << 20), args.label, args.block_size)
        _write_file(args.out, json.dumps(doc, indent=1) + '\n')
    except PlanError as e:
        print(f'REFUSED: {e}', file=sys.stderr)
        return 2
    except Exception as e:     # a crash must not look like success
        print(f'REFUSED: unexpected {type(e).__name__}: {e}', file=sys.stderr)
        return 2
    print(f"{args.out.name}: reader {doc['reader']}, {len(doc['partitions'])} partitions, "
          f"{doc['disk']['block_size']}-byte sectors")
    return 0


def _file_readers(paths):
    readers, used = [], set()
    for path in paths:
        base = re.sub(r'[^A-Za-z0-9._-]', '-', Path(path).stem)[:40] or 'reader'
        label, n = base, 1
        while label in used:
            n += 1
            label = f'{base}-{n}'
        used.add(label)
        try:
            readers.append((label, _load_json(path)))
        except PlanError as e:
            readers.append((label, e))
    return readers


def main_sp11(args, ap):
    """Exit 0 ok, 1 verification failed, 2 refused or crashed: only a verified FAIL may exit 1."""
    try:
        if args.mode == 'plan':
            if (args.extent_start is None) != (args.extent_end is None):
                ap.error('--extent-start and --extent-end go together')
            extent = None if args.extent_start is None else (args.extent_start, args.extent_end)
            p = plan_sp11(_load_json(args.survey), extent, SP11_MIN_BYTES if args.min_bytes is None else args.min_bytes,
                          args.new_type or TYPE, args.new_name or NAME, args.disk_guid, args.nvme_eui, not args.no_eui)
            if args.out:
                _write_file(args.out, json.dumps(p, indent=1) + '\n')
            print('\n'.join(plan_text(p)))
            return 0
        notes, verdict = [], {}
        minimum = SP11_MIN_READERS if args.min_readers is None else args.min_readers
        before, readers = _load_json(args.before), _file_readers(args.after)
        if args.mode == 'verify':
            problems = verify_sp11(before, readers, _load_json(args.plan), minimum, not args.no_eui, notes, verdict)
        else:
            problems = verify_undo_sp11(before, readers, _load_json(args.plan) if args.plan else None, minimum,
                                        not args.no_eui, notes, verdict, args.disk_guid, args.nvme_eui)
        if args.out:
            _write_file(args.out, json.dumps(verdict, indent=1) + '\n')
        for n in notes:
            print('note:', n)
        for p in problems:
            print('FAIL:', p)
        print('PASS' if not problems else f'{len(problems)} problem(s)')
        return 1 if problems else 0
    except PlanError as e:
        print(f'REFUSED: {e}', file=sys.stderr)
        return 2
    except Exception as e:     # a crash must not look like a verification failure
        print(f'REFUSED: unexpected {type(e).__name__}: {e}', file=sys.stderr)
        return 2


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='mode', required=True)
    pp = sub.add_parser('plan')
    pp.add_argument('--survey', type=Path, required=True)
    pp.add_argument('--out', type=Path)
    vp = sub.add_parser('verify')
    vp.add_argument('--before', type=Path, required=True)
    vp.add_argument('--after', type=Path, required=True, action='append')
    vp.add_argument('--plan', type=Path, required=True)
    up = sub.add_parser('verify-undo')
    up.add_argument('--before', type=Path, required=True)
    up.add_argument('--after', type=Path, required=True, action='append')
    for p in (pp, vp):
        p.add_argument('--no-eui', action='store_true', help='for Windows raw captures, which carry no device path')
    add_sp11_arguments(pp, vp, up, sub.add_parser('convert-reader'))
    args = ap.parse_args(argv)
    if args.mode == 'convert-reader':
        return convert_main(args)
    if args.profile == 'sp11':
        return main_sp11(args, ap)
    for k in SP11_ONLY[args.mode]:
        # `is`, not `in`: 0 == False, and `--min-readers 0` must not slip through unrefused.
        if getattr(args, k) is not None and getattr(args, k) is not False:
            ap.error(f"--{k.replace('_', '-')} needs --profile sp11")
    if args.mode != 'plan':
        # A repeated --after used to keep only the last file, which would pass off one reader as several.
        if len(args.after) != 1:
            ap.error('--after is given once with --profile a16; several readers need --profile sp11')
        args.after = args.after[0]
    load = lambda path: json.loads(path.read_text())
    if args.mode == 'plan':
        result = plan(load(args.survey), not args.no_eui)
        n = result['new']
        print(f"new partition: LBA {n['first_lba']}..{n['last_lba']} ({n['last_lba'] - n['first_lba'] + 1} blocks), "
              f"offset {n['offset']}, {n['bytes']} bytes ({n['bytes'] / (1 << 30):.3f} GiB), type {n['type']}, "
              f"slot {n['expected_index']}")
        print(f"guards: {n['guard_bytes']} bytes before and after; extent {result['extent']}")
        print(result['windows']['new_partition'])
        if args.out:
            args.out.write_text(json.dumps(result, indent=1) + '\n')
        return 0
    if args.mode == 'verify':
        notes = []
        problems = verify(load(args.before), load(args.after), load(args.plan), not args.no_eui, notes)
        for n in notes:
            print('note:', n)
    else:
        problems = verify_undo(load(args.before), load(args.after))
    for p in problems:
        print('FAIL:', p)
    print('PASS' if not problems else f'{len(problems)} problem(s)')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
