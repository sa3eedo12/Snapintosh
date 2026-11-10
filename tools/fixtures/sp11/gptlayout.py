#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""GPT layout schema, strict loader and window model for the SP11 NVMe window (docs/SP11-NVME-WINDOW.md).

A layout is what a reader recorded of one disk's GUID Partition Table: the block size, the two headers' facts and
every partition entry. It is data about a disk and nothing here opens one. The shipped fixtures in this directory are
synthetic; a sanitized real layout of the same schema replaces them without touching the tests.

    python3 tools/fixtures/sp11/gptlayout.py check LAYOUT.json FIRST_LBA LAST_LBA [--partition-guid GUID]
    python3 tools/fixtures/sp11/gptlayout.py from-survey SURVEY.json [--disk N]      # JSON to stdout

Schema v1: one JSON object. Unknown keys are refused, never ignored; every integer is a JSON integer (no float, no
bool, no string, no hex), and the LBAs of one layout are in units of its own `block_size`.

    schema          "snapintosh-gpt-layout"
    schema_version  1
    block_size      512 or 4096: the NVMe LBA format the disk is in use with (Identify Namespace, current format)
    last_block      LBA of the last block of the namespace (NSZE - 1); the backup header lives there
    first_usable    GPT FirstUsableLBA;  last_usable  GPT LastUsableLBA
    primary, backup {my_lba, alternate_lba, entries_lba, count, entry_size}: the two GPT headers' facts
    partitions      [{index, type, guid, first_lba, last_lba, attributes, name}, ...], first_lba and last_lba inclusive
    note            optional text; disk_guid optional canonical GUID
    blocks, disk_bytes     optional, informational, checked against last_block and block_size when present
    partitions[].type_name, bytes, offset    optional, informational, checked when present (they are what
                    q1n1 tools/gpt_survey.py --json emits, so a sanitized survey disk entry loads unchanged after
                    `from-survey` drops the keys this schema does not carry)

The loader refuses what no real GPT can be: headers that do not point at each other, an entry array under the 16,384
bytes UEFI reserves, a usable range that overlaps either array, partitions outside the usable range or overlapping each
other, duplicate or all-zero GUIDs, non-canonical GUIDs, indexes outside the array, and duplicate JSON keys.

The window model (`window_verdict`) answers one question for any LBA size: may this extent be the Snapintosh window of
this disk? It is the host planner's rule and the reference the monitor's C code (compat/nvme-host.c, SP11 gate) is
held to for 512-byte LBAs by tools/test-nvme-window-hamoa.py.
"""
import argparse
import json
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

SCHEMA = 'snapintosh-gpt-layout'
SCHEMA_VERSION = 1
BLOCK_SIZES = (512, 4096)
MIB = 1 << 20
U64 = (1 << 64) - 1
U63 = (1 << 63) - 1
SNAPINTOSH_TYPE = 'af494c0f-7849-4186-8d2c-9b05cf6ab8b8'      # tools/snapmac-partition-plan.py TYPE (the project's own)
MIN_ARRAY_BYTES = 16384         # UEFI reserves at least this for the entry array
MAX_ARRAY_BYTES = MIB           # sanity bound; q1n1's survey reads at most 64 KiB
MAX_ENTRIES = 4096
MIN_WINDOW_BYTES = 2 * MIB      # two synthesized edges (compat/ans.h ANS_WINDOW_EDGE_BLOCKS), nothing in between
ALIGN_BYTES = MIB               # window start and size
GUARD_BYTES = MIB               # free gap to every other extent and GPT zone (tools/snapmac-partition-plan.py GUARD_LBAS)

TOP_REQUIRED = ('schema', 'schema_version', 'block_size', 'last_block', 'first_usable', 'last_usable', 'primary',
                'backup', 'partitions')
TOP_OPTIONAL = ('note', 'disk_guid', 'blocks', 'disk_bytes')
HEADER_KEYS = ('my_lba', 'alternate_lba', 'entries_lba', 'count', 'entry_size')
PART_REQUIRED = ('index', 'type', 'guid', 'first_lba', 'last_lba', 'attributes', 'name')
PART_OPTIONAL = ('type_name', 'bytes', 'offset')
ZERO_GUID = '00000000-0000-0000-0000-000000000000'


class LayoutError(ValueError):
    """The layout is malformed, inconsistent or cannot be a real GPT."""


def _fail(what, why):
    raise LayoutError(f'{what}: {why}')


def _int(value, what, low=0, high=U63):
    if type(value) is not int:          # excludes bool and float
        _fail(what, f'expected an integer, got {value!r}')
    if not low <= value <= high:
        _fail(what, f'{value} outside {low}..{high}')
    return value


def _keys(doc, what, required, optional=()):
    if not isinstance(doc, dict):
        _fail(what, 'expected an object')
    unknown = sorted(set(doc) - set(required) - set(optional))
    if unknown:
        _fail(what, f'unknown key(s) {unknown}; refusing rather than ignoring')
    missing = [k for k in required if k not in doc]
    if missing:
        _fail(what, f'missing key(s) {missing}')
    return doc


def _guid(value, what):
    if not isinstance(value, str):
        _fail(what, f'expected a GUID string, got {value!r}')
    try:
        canonical = str(uuid.UUID(value))
    except ValueError:
        _fail(what, f'not a GUID: {value!r}')
    if canonical != value:
        _fail(what, f'not in canonical lowercase form: {value!r}')
    return value


@dataclass(frozen=True)
class Part:
    index: int
    type: str
    guid: str
    first: int          # inclusive
    last: int           # inclusive
    attributes: int
    name: str


@dataclass(frozen=True)
class Layout:
    block_size: int
    last_block: int
    first_usable: int
    last_usable: int
    array_blocks: int
    disk_guid: object
    partitions: tuple

    @property
    def nsze(self):
        return self.last_block + 1


def _header(doc, what, entries_floor):
    h = _keys(doc, what, HEADER_KEYS)
    v = {k: _int(h[k], f'{what}.{k}') for k in HEADER_KEYS}
    if not 1 <= v['count'] <= MAX_ENTRIES:
        _fail(what, f'entry count {v["count"]} outside 1..{MAX_ENTRIES}')
    size = v['entry_size']
    if size < 128 or size & (size - 1):
        _fail(what, f'entry size {size} is not 128 * 2**n')      # UEFI: SizeOfPartitionEntry = 128 * 2**n
    total = v['count'] * size
    if not MIN_ARRAY_BYTES <= total <= MAX_ARRAY_BYTES:
        _fail(what, f'entry array of {total} bytes outside {MIN_ARRAY_BYTES}..{MAX_ARRAY_BYTES}')
    if v['entries_lba'] < entries_floor:
        _fail(what, f'entries_lba {v["entries_lba"]} below {entries_floor}')
    return v


def validate(doc):
    """Check a parsed layout against the schema and return a Layout; LayoutError on anything wrong."""
    d = _keys(doc, 'layout', TOP_REQUIRED, TOP_OPTIONAL)
    if d['schema'] != SCHEMA:
        _fail('schema', f'expected {SCHEMA!r}, got {d["schema"]!r}')
    if type(d['schema_version']) is not int or d['schema_version'] != SCHEMA_VERSION:
        _fail('schema_version', f'unknown version {d["schema_version"]!r}; this loader reads {SCHEMA_VERSION}')
    bs = d['block_size']
    if type(bs) is not int or bs not in BLOCK_SIZES:
        _fail('block_size', f'{bs!r} is not one of {BLOCK_SIZES}')
    last_block = _int(d['last_block'], 'last_block', 1, U63 // bs - 1)
    first_usable = _int(d['first_usable'], 'first_usable', 0, last_block)
    last_usable = _int(d['last_usable'], 'last_usable', 0, last_block)
    if 'note' in d and (not isinstance(d['note'], str) or len(d['note']) > 600):
        _fail('note', 'expected text of at most 600 characters')
    disk_guid = _guid(d['disk_guid'], 'disk_guid') if 'disk_guid' in d else None
    if 'blocks' in d and _int(d['blocks'], 'blocks') != last_block + 1:
        _fail('blocks', 'does not equal last_block + 1')
    if 'disk_bytes' in d and _int(d['disk_bytes'], 'disk_bytes') != (last_block + 1) * bs:
        _fail('disk_bytes', 'does not equal (last_block + 1) * block_size')

    primary = _header(d['primary'], 'primary', 2)
    backup = _header(d['backup'], 'backup', 2)
    if (primary['count'], primary['entry_size']) != (backup['count'], backup['entry_size']):
        _fail('headers', 'primary and backup disagree on the entry array shape')
    if (primary['my_lba'], primary['alternate_lba']) != (1, last_block):
        _fail('primary', f'my_lba/alternate_lba must be 1/{last_block}')
    if (backup['my_lba'], backup['alternate_lba']) != (last_block, 1):
        _fail('backup', f'my_lba/alternate_lba must be {last_block}/1')
    array_blocks = -(-primary['count'] * primary['entry_size'] // bs)
    if first_usable < primary['entries_lba'] + array_blocks:
        _fail('first_usable', 'overlaps the primary entry array')
    if backup['entries_lba'] + array_blocks > last_block:
        _fail('backup', 'entry array reaches the backup header')
    if last_usable >= backup['entries_lba']:
        _fail('last_usable', 'overlaps the backup entry array')
    if first_usable > last_usable:
        _fail('usable range', 'first_usable is after last_usable')

    listed = d['partitions']
    if not isinstance(listed, list):
        _fail('partitions', 'expected a list')
    if len(listed) > primary['count']:
        _fail('partitions', f'{len(listed)} entries exceed the array\'s {primary["count"]}')
    parts, indexes, guids = [], set(), set()
    for i, item in enumerate(listed):
        what = f'partitions[{i}]'
        e = _keys(item, what, PART_REQUIRED, PART_OPTIONAL)
        index = _int(e['index'], f'{what}.index', 0, primary['count'] - 1)
        if index in indexes:
            _fail(what, f'duplicate index {index}')
        indexes.add(index)
        ptype, guid = _guid(e['type'], f'{what}.type'), _guid(e['guid'], f'{what}.guid')
        if ZERO_GUID in (ptype, guid):
            _fail(what, 'an all-zero GUID marks an unused entry, not a partition')
        if guid in guids:
            _fail(what, f'duplicate partition GUID {guid}')
        guids.add(guid)
        first, last = _int(e['first_lba'], f'{what}.first_lba'), _int(e['last_lba'], f'{what}.last_lba')
        if first > last:
            _fail(what, 'first_lba is after last_lba')
        if first < first_usable or last > last_usable:
            _fail(what, 'outside the usable range')
        attributes = _int(e['attributes'], f'{what}.attributes', 0, U64)
        name = e['name']
        if not isinstance(name, str) or len(name) > 36 or '\0' in name:
            _fail(what, 'name must be text of at most 36 characters without NUL')
        if 'type_name' in e and not isinstance(e['type_name'], str):
            _fail(what, 'type_name must be text')
        if 'bytes' in e and _int(e['bytes'], f'{what}.bytes') != (last - first + 1) * bs:
            _fail(what, 'bytes does not equal the extent')
        if 'offset' in e and _int(e['offset'], f'{what}.offset') != first * bs:
            _fail(what, 'offset does not equal first_lba * block_size')
        parts.append(Part(index, ptype, guid, first, last, attributes, name))
    ordered = sorted(parts, key=lambda p: p.first)
    for a, b in zip(ordered, ordered[1:]):
        if b.first <= a.last:
            _fail('partitions', f'entries {a.index} and {b.index} overlap')
    return Layout(bs, last_block, first_usable, last_usable, array_blocks, disk_guid, tuple(ordered))


def _no_duplicates(pairs):
    seen = {}
    for key, value in pairs:
        if key in seen:
            _fail('json', f'duplicate key {key!r}')
        seen[key] = value
    return seen


def _reject_constant(token):
    _fail('json', f'{token} is not a number')


def _reject_float(token):
    _fail('json', f'{token} is a float; LBAs are integers')


def loads(text):
    """Parse layout JSON text. Duplicate keys, NaN, Infinity and floats are refused."""
    try:
        doc = json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_reject_constant,
                         parse_float=_reject_float)
    except (ValueError, RecursionError) as problem:
        if isinstance(problem, LayoutError):
            raise
        raise LayoutError(f'invalid JSON: {problem}') from None
    return validate(doc)


def load(path):
    try:
        return loads(Path(path).read_text(encoding='utf-8'))
    except OSError as problem:
        raise LayoutError(f'cannot read {path}: {problem.strerror}') from None


def survey_doc(disk):
    """One `disks[]` entry of q1n1 tools/gpt_survey.py --json as a layout document (not yet validated). A disk the
    survey flagged is refused."""
    if not isinstance(disk, dict):
        _fail('survey disk', 'expected an object')
    if disk.get('errors'):
        _fail('survey disk', f'the survey reported errors: {disk["errors"]}')
    doc = {'schema': SCHEMA, 'schema_version': SCHEMA_VERSION, 'note': 'converted from a gpt_survey disk entry'}
    for key in ('block_size', 'last_block', 'first_usable', 'last_usable'):
        if key not in disk:
            _fail('survey disk', f'missing {key}')
        doc[key] = disk[key]
    for key in ('primary', 'backup'):
        header = disk.get(key)
        if not isinstance(header, dict) or any(k not in header for k in HEADER_KEYS):
            _fail('survey disk', f'{key} header unavailable')
        doc[key] = {k: header[k] for k in HEADER_KEYS}
    doc['partitions'] = [{k: p[k] for k in PART_REQUIRED if k in p} for p in disk.get('partitions') or []]
    if disk.get('disk_guid'):
        doc['disk_guid'] = disk['disk_guid']
    return doc


def from_survey_disk(disk):
    return validate(survey_doc(disk))


def free_extents(layout):
    """Gaps in the usable range, as (first, last) inclusive LBAs, in order."""
    gaps, cursor = [], layout.first_usable
    for p in layout.partitions:
        if p.first > cursor:
            gaps.append((cursor, p.first - 1))
        cursor = max(cursor, p.last + 1)
    if cursor <= layout.last_usable:
        gaps.append((cursor, layout.last_usable))
    return gaps


def window_verdict(layout, first, last, partition_guid=None):
    """'OK' or the first reason this extent may not be the window. LBAs are in the layout's block size.

    Reasons are the NVMEH_E_SP11_* names without the prefix, so the C implementation and this model share a vocabulary;
    PARTITION_* exist only here (the monitor is handed the extent, not the partition). With `partition_guid` the
    window must be exactly that Snapintosh-typed partition; without it the window must lie in free space. The order
    is the C code's: order, size, range, GPT zones, overlap, alignment, guard to the usable edges, guard to the others.
    """
    for value in (first, last):
        if type(value) is not int or not 0 <= value <= U64:
            raise TypeError('window LBAs are unsigned 64-bit integers')
    bs = layout.block_size
    others = layout.partitions
    if partition_guid is not None:
        match = [p for p in layout.partitions if p.guid == partition_guid]
        if len(match) != 1:
            return 'PARTITION_MISSING'
        if match[0].type != SNAPINTOSH_TYPE:
            return 'PARTITION_TYPE'
        if (match[0].first, match[0].last) != (first, last):
            return 'PARTITION_EXTENT'
        others = tuple(p for p in layout.partitions if p is not match[0])
    if first > last or last == U64:
        return 'WINDOW_ORDER'
    size = last - first + 1
    if size * bs < MIN_WINDOW_BYTES:
        return 'WINDOW_SIZE'
    if last >= layout.nsze:
        return 'WINDOW_RANGE'
    if first < layout.first_usable or last > layout.last_usable:
        return 'WINDOW_GPT'
    if any(first <= p.last and p.first <= last for p in others):
        return 'OVERLAP'
    if (first * bs) % ALIGN_BYTES or (size * bs) % ALIGN_BYTES:
        return 'WINDOW_ALIGN'
    guard = GUARD_BYTES // bs
    if first - layout.first_usable < guard or layout.last_usable - last < guard:
        return 'WINDOW_GUARD'
    for p in others:
        gap = first - p.last - 1 if p.last < first else p.first - last - 1
        if gap < guard:
            return 'WINDOW_GUARD'
    return 'OK'


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    sub = ap.add_subparsers(dest='mode', required=True)
    check = sub.add_parser('check', help='verdict for one candidate window; exit 0 OK, 1 refused, 2 bad input')
    check.add_argument('layout', type=Path)
    check.add_argument('first_lba', type=int)
    check.add_argument('last_lba', type=int)
    check.add_argument('--partition-guid')
    survey = sub.add_parser('from-survey', help='print the layout of one gpt_survey.py --json disk entry')
    survey.add_argument('survey', type=Path)
    survey.add_argument('--disk', type=int, default=0)
    args = ap.parse_args(argv)
    try:
        if args.mode == 'check':
            verdict = window_verdict(load(args.layout), args.first_lba, args.last_lba, args.partition_guid)
            print(verdict)
            return 0 if verdict == 'OK' else 1
        doc = json.loads(args.survey.read_text(encoding='utf-8'))
        converted = survey_doc(doc['disks'][args.disk])
        validate(converted)
        print(json.dumps(converted, indent=1))
        return 0
    except (LayoutError, TypeError, KeyError, IndexError, OSError, ValueError) as problem:
        print(f'refused: {problem}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
