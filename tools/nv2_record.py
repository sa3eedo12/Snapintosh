#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""NV2 functional-test records: the format, the converter and an inspector.

Why this exists: the SP11 monitor may set HCR_EL2.NV/NV2 only when the ID
registers advertise FEAT_NV2 AND a recorded functional test on this exact core
says that VNCR_EL2 redirection worked and an NV2-off control trapped
(compat/nv2-gate.h). The functional test is a hardware-side program that
cannot be run from this repository. It prints the TEXT form below; this tool
turns it into the compact BINARY form the monitor validates, and into a C
header the monitor build can embed. The decision logic here (parse_binary,
decide) mirrors compat/nv2-gate.c value for value, and tools/test-nv2-gate.py
proves they agree.

    python3 tools/nv2_record.py ingest run.log [--format auto|text|json|bin]
            [--last] [--out build/nv2-record.bin] [--header build/nv2-record.h]
            [--json build/nv2-record.json]
    python3 tools/nv2_record.py show run.log [--format ...] [--last]
            [--soc x1e80100] [--live-midr 0x.. --live-revidr 0x..
             --live-mmfr2 0x.. --live-mmfr4 0x..] [--check]

Use "-" as the input to read standard input. `ingest` writes the binary record
and the header (and, with --json, the canonical JSON); `show` prints the fields,
whether the record parses, and, when all four --live-* values are given, what
the monitor's gate would decide on a core that reads those values. `show --check`
turns that decision into the exit status; without the four values it exits 3 for a
record that no register value could make the gate allow, and 2 for any other (there
is no decision to check, so it never answers with 0). An input file must be a regular
file, not a link, of at most 32 MiB; it is inspected before it is opened.

Embedding: build the SP11 monitor with the header's absolute path as a quoted
define, for example
    -DSNAP_PLATFORM_SP11=1 '-DSNAP_NV2_RECORD_HEADER="/abs/build/nv2-record.h"'
With no define the monitor carries no record, which the gate treats as deny.
The record is a measurement of one machine: keep it under build/ and never
commit it.

Trust model: the checks below detect accidental damage, truncation and unknown
layouts. They are not authentication; anyone who can edit the record can forge a
pass. The record is local, operator-trusted input. The tool copies the evidence
label it is given and never upgrades it; the gate accepts VERIFIED and OBSERVED.

TEXT FORM
---------
One record is a block of lines. A record line starts with "NV2R " and ends with
"*hh": hh is two hex digits (either case), the CRC-8 of every byte of the line
before the "*" (from the "N" through the last value byte). Lines that do not
start with "NV2R" are ignored, so the block can sit in a console log; a line that
starts with "NV2R" and is not exactly well formed is an error, reported with its
line number. "\\r\\n" line ends are accepted, nothing else is.

    NV2R begin*hh
    NV2R <key>=<value>*hh          eleven key lines, each key exactly once,
                                   in any order (canonical order below)
    NV2R end lines=11*hh           the number of key lines

Keys and values (strict spelling; hex digits may be either case):

    schema_version            decimal, must be 1
    soc                       x1e80100 | x2e-96-100 | unknown
    midr_el1                  0x + 1..16 hex digits (MIDR_EL1 of the core tested)
    revidr_el1                0x + 1..16 hex digits (REVIDR_EL1)
    id_aa64mmfr2_el1          0x + 1..16 hex digits (raw, unfiltered)
    id_aa64mmfr4_el1          0x + 1..16 hex digits (raw, unfiltered)
    vncr_redirect             pass | fail | not-run
    negative_control_trapped  pass | fail | not-run
    evidence                  NONE | VERIFIED | OBSERVED | INFERRED |
                              EXPERIMENTAL | UNIMPLEMENTED
    tool                      1..15 characters of [A-Za-z0-9._-]
    build_id                  0x + 1..8 hex digits (test program build id)

"vncr_redirect=pass" means: with HCR_EL2.NV and NV2 set, an EL2-register access
made from EL1 landed in the VNCR_EL2 page. "negative_control_trapped=pass" means:
the same access with NV2 clear trapped instead. Each of them is "not-run" until
it was actually run on the register values recorded in the same block.

A block may sit among other console output. Several complete blocks in one log
(repeated runs, say) are an error unless --last selects the final one, and a
"begin" that never reaches its "end" is always an error: the tool never guesses.

SYNTHETIC EXAMPLE, not a measurement (every value is made up; the MIDR matches none
of the cores this repository knows); tools/test-nv2-gate.py checks every check
digit and the bytes below against this tool:

    NV2R begin*78
    NV2R schema_version=1*fc
    NV2R soc=x1e80100*63
    NV2R midr_el1=0x00000000feed0001*75
    NV2R revidr_el1=0x0000000000000000*e3
    NV2R id_aa64mmfr2_el1=0x0000000002001234*9a
    NV2R id_aa64mmfr4_el1=0x0000000000000000*14
    NV2R vncr_redirect=pass*85
    NV2R negative_control_trapped=pass*51
    NV2R evidence=OBSERVED*13
    NV2R tool=synthetic-doc*f7
    NV2R build_id=0x00000001*1c
    NV2R end lines=11*cb

CRC-8 (the "*hh" check): polynomial 0x07, initial value 0x00, no input or output
reflection, no final xor. Its check value is crc8("123456789") == 0xf4. In C:

    static unsigned crc8(const char *s, unsigned n)
    {
        unsigned crc = 0, bit;
        while (n--) {
            crc ^= (unsigned char)*s++;
            for (bit = 0; bit < 8; bit++)
                crc = (crc & 0x80) ? ((crc << 1) ^ 0x07) & 0xff : (crc << 1) & 0xff;
        }
        return crc;
    }

A program that prints a line formats the body ("NV2R midr_el1=0x...") into a
buffer, then prints the body, "*", crc8(body) as two hex digits and a newline.
There is no whole-record checksum line: the BINARY form below carries a CRC-32,
which this tool computes.

JSON FORM
---------
One object with exactly the eleven keys above and no others. Integers may be
JSON integers or "0x.." strings (strings are recommended: JSON tools silently
lose precision above 2**53). Enumerations are the strings listed above.
schema_version is the integer 1. The reserved area of the binary form cannot be
set from JSON.

BINARY FORM (version 1)
-----------------------
Little-endian, 96 bytes, naturally aligned, no padding; compat/nv2-gate.h holds
the same table as static assertions.

    0x00 u32  magic                 bytes 4e 56 32 52 ("NV2R")
    0x04 u16  version               1
    0x06 u16  flags                 must be 0
    0x08 u32  soc                   0 unknown, 1 x1e80100, 2 x2e-96-100
    0x0c u32  build_id
    0x10 u64  midr_el1
    0x18 u64  revidr_el1
    0x20 u64  id_aa64mmfr2_el1
    0x28 u64  id_aa64mmfr4_el1
    0x30 u8   vncr_redirect         0 not-run, 1 pass, 2 fail
    0x31 u8   negative_control_trapped   same values
    0x32 u8   evidence              0 NONE, 1 VERIFIED, 2 OBSERVED,
                                    3 INFERRED, 4 EXPERIMENTAL, 5 UNIMPLEMENTED
    0x33 u8   reserved0             must be 0
    0x34 u32  reserved1             must be 0
    0x38 char tool[16]              the name, NUL padded; byte 15 is 0
    0x48 u8   reserved2[20]         must be 0
    0x5c u32  crc32                 CRC-32 (zlib) of bytes 0x00..0x5b

The same synthetic example as bytes (crc32 0x06c43036):

    4e 56 32 52 01 00 00 00 01 00 00 00 01 00 00 00
    01 00 ed fe 00 00 00 00 00 00 00 00 00 00 00 00
    34 12 00 02 00 00 00 00 00 00 00 00 00 00 00 00
    01 01 02 00 00 00 00 00 73 79 6e 74 68 65 74 69
    63 2d 64 6f 63 00 00 00 00 00 00 00 00 00 00 00
    00 00 00 00 00 00 00 00 00 00 00 00 36 30 c4 06

Exit status: 0 success, 1 malformed input or an unwritable output, 2 bad usage (including `show --check` without
the four --live-* values for a record the gate could still allow: there is no decision to check),
3 `show --check` when the gate would not allow, or, without --live-*, when no live register values could make it.
"""
import argparse
import dataclasses
import json
import os
import re
import stat
import struct
import sys
import zlib

MAGIC = 0x5232564E
VERSION = 1
SIZE = 96
TOOL_LEN = 16
RESERVED2_LEN = 20
CRC_OFFSET = 0x5C
LINE_PREFIX = 'NV2R'
# A record sits in a console log, which may be megabytes but never gigabytes; the bound also keeps a mistaken path
# (a disk image, a core file) from being read whole.
MAX_INPUT_BYTES = 32 << 20

SOC = {'unknown': 0, 'x1e80100': 1, 'x2e-96-100': 2}
RESULT = {'not-run': 0, 'pass': 1, 'fail': 2}
EVIDENCE = {'NONE': 0, 'VERIFIED': 1, 'OBSERVED': 2, 'INFERRED': 3,
            'EXPERIMENTAL': 4, 'UNIMPLEMENTED': 5}
KEYS = ('schema_version', 'soc', 'midr_el1', 'revidr_el1', 'id_aa64mmfr2_el1',
        'id_aa64mmfr4_el1', 'vncr_redirect', 'negative_control_trapped',
        'evidence', 'tool', 'build_id')

# The values are compat/nv2-gate.h's enum snap_nv2_verdict.
DENY_NAMES = ('DENY_NO_RECORD', 'DENY_LENGTH', 'DENY_MAGIC', 'DENY_VERSION',
              'DENY_CRC', 'DENY_RESERVED', 'DENY_SOC', 'DENY_MIDR',
              'DENY_REVIDR', 'DENY_NOT_ADVERTISED', 'DENY_ID_MISMATCH',
              'DENY_VNCR_NOT_RUN', 'DENY_VNCR_FAIL', 'DENY_NEGCTL_NOT_RUN',
              'DENY_NEGCTL_FAIL', 'DENY_EVIDENCE')
DENY = {name: i for i, name in enumerate(DENY_NAMES)}
PARSED = 0x4E325052
ALLOW = 0x4E32414C
STATUS_RECORD, STATUS_MISMATCH, STATUS_NO_NV2, STATUS_RESULT = -90, -91, -92, -93

WIRE = struct.Struct('<IHHIIQQQQBBBBI16s20sI')
assert WIRE.size == SIZE

_TOOL_RE = re.compile(r'[A-Za-z0-9._-]{1,15}')
_CHECK_RE = re.compile(r'[0-9a-fA-F]{2}')
_KEY_LINE_RE = re.compile(r'([a-z0-9_]+)=(.*)')
_END_RE = re.compile(r'end lines=(0|[1-9][0-9]*)')


class RecordError(Exception):
    """Input that is not exactly one well-formed record."""


class UsageError(RecordError):
    """A command line that cannot produce the answer it asks for; reported with exit status 2."""


@dataclasses.dataclass(frozen=True)
class Record:
    soc: int
    midr_el1: int
    revidr_el1: int
    id_aa64mmfr2_el1: int
    id_aa64mmfr4_el1: int
    vncr_redirect: int
    negative_control_trapped: int
    evidence: int
    tool: str
    build_id: int


def crc8(data):
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def crc32(data):
    return zlib.crc32(data) & 0xFFFFFFFF


def verdict_name(verdict):
    if verdict == ALLOW:
        return 'ALLOW'
    if verdict == PARSED:
        return 'PARSED'
    if 0 <= verdict < len(DENY_NAMES):
        return DENY_NAMES[verdict]
    return f'UNKNOWN({verdict:#x})'


def verdict_status(verdict):
    if verdict == ALLOW:
        return 0
    if verdict in (DENY['DENY_SOC'], DENY['DENY_MIDR'], DENY['DENY_REVIDR'],
                   DENY['DENY_ID_MISMATCH']):
        return STATUS_MISMATCH
    if verdict == DENY['DENY_NOT_ADVERTISED']:
        return STATUS_NO_NV2
    if verdict in (DENY['DENY_VNCR_NOT_RUN'], DENY['DENY_VNCR_FAIL'],
                   DENY['DENY_NEGCTL_NOT_RUN'], DENY['DENY_NEGCTL_FAIL'],
                   DENY['DENY_EVIDENCE']):
        return STATUS_RESULT
    return STATUS_RECORD


def _name_of(table, value):
    for name, number in table.items():
        if number == value:
            return name
    raise RecordError(f'value {value} has no name')


# ---- field conversion (text and JSON share it) ----------------------------

def _to_int(key, value, bits):
    if isinstance(value, bool):
        raise RecordError(f'{key}: a boolean is not a number')
    if isinstance(value, int):
        number = value
    elif isinstance(value, str):
        if not re.fullmatch(r'0x[0-9a-fA-F]{1,%d}' % (bits // 4), value):
            raise RecordError(f'{key}: {value!r} is not 0x followed by 1..{bits // 4} hex digits')
        number = int(value, 16)
    else:
        raise RecordError(f'{key}: {value!r} is not a number')
    if not 0 <= number < (1 << bits):
        raise RecordError(f'{key}: {number} does not fit in {bits} bits')
    return number


def _to_enum(key, value, table):
    if not isinstance(value, str) or value not in table:
        raise RecordError(f'{key}: {value!r} is not one of {", ".join(table)}')
    return table[value]


def _to_schema(value):
    if isinstance(value, bool):
        raise RecordError('schema_version: a boolean is not a number')
    if isinstance(value, str) and re.fullmatch(r'0|[1-9][0-9]*', value):
        value = int(value)
    if not isinstance(value, int):
        raise RecordError(f'schema_version: {value!r} is not a decimal number')
    if value != VERSION:
        raise RecordError(f'schema_version: {value} is not supported (only {VERSION})')


def record_from_fields(fields):
    """Build a Record from a dict of the eleven keys. Strict: every key exactly
    once, no extra keys, every value in range."""
    if not isinstance(fields, dict):
        raise RecordError('a record is a set of key/value pairs')
    unknown = sorted(set(fields) - set(KEYS))
    if unknown:
        raise RecordError(f'unknown key {unknown[0]!r}')
    missing = [k for k in KEYS if k not in fields]
    if missing:
        raise RecordError(f'missing key {missing[0]!r}')
    _to_schema(fields['schema_version'])
    tool = fields['tool']
    if not isinstance(tool, str) or not _TOOL_RE.fullmatch(tool):
        raise RecordError(f'tool: {tool!r} is not 1..15 characters of [A-Za-z0-9._-]')
    return Record(
        soc=_to_enum('soc', fields['soc'], SOC),
        midr_el1=_to_int('midr_el1', fields['midr_el1'], 64),
        revidr_el1=_to_int('revidr_el1', fields['revidr_el1'], 64),
        id_aa64mmfr2_el1=_to_int('id_aa64mmfr2_el1', fields['id_aa64mmfr2_el1'], 64),
        id_aa64mmfr4_el1=_to_int('id_aa64mmfr4_el1', fields['id_aa64mmfr4_el1'], 64),
        vncr_redirect=_to_enum('vncr_redirect', fields['vncr_redirect'], RESULT),
        negative_control_trapped=_to_enum('negative_control_trapped',
                                          fields['negative_control_trapped'], RESULT),
        evidence=_to_enum('evidence', fields['evidence'], EVIDENCE),
        tool=tool,
        build_id=_to_int('build_id', fields['build_id'], 32))


def fields_of(rec):
    return {
        'schema_version': VERSION,
        'soc': _name_of(SOC, rec.soc),
        'midr_el1': f'{rec.midr_el1:#018x}',
        'revidr_el1': f'{rec.revidr_el1:#018x}',
        'id_aa64mmfr2_el1': f'{rec.id_aa64mmfr2_el1:#018x}',
        'id_aa64mmfr4_el1': f'{rec.id_aa64mmfr4_el1:#018x}',
        'vncr_redirect': _name_of(RESULT, rec.vncr_redirect),
        'negative_control_trapped': _name_of(RESULT, rec.negative_control_trapped),
        'evidence': _name_of(EVIDENCE, rec.evidence),
        'tool': rec.tool,
        'build_id': f'{rec.build_id:#010x}',
    }


# ---- text form --------------------------------------------------------------

def _checked_line(body):
    return f'{body}*{crc8(body.encode("ascii")):02x}'


def format_text(rec):
    lines = [_checked_line(f'{LINE_PREFIX} begin')]
    lines += [_checked_line(f'{LINE_PREFIX} {key}={value}')
              for key, value in fields_of(rec).items()]
    lines.append(_checked_line(f'{LINE_PREFIX} end lines={len(KEYS)}'))
    return '\n'.join(lines) + '\n'


def parse_text(text, last=False):
    blocks = []
    current = None
    for number, raw in enumerate(text.split('\n'), 1):
        line = raw[:-1] if raw.endswith('\r') else raw
        if not line.startswith(LINE_PREFIX):
            continue

        def fail(message):
            raise RecordError(f'line {number}: {message}')

        star = line.rfind('*')
        if star < 0 or not _CHECK_RE.fullmatch(line[star + 1:]):
            fail('no "*hh" check digits at the end')
        body = line[:star]
        try:
            body_bytes = body.encode('ascii')
        except UnicodeEncodeError:
            fail('not ASCII')
        if crc8(body_bytes) != int(line[star + 1:], 16):
            fail('check digits do not match the line')
        if not body.startswith(LINE_PREFIX + ' '):
            fail('"NV2R" is not followed by a space')
        rest = body[len(LINE_PREFIX) + 1:]

        if rest == 'begin':
            if current is not None:
                fail(f'"begin" inside the record that began at line {current["line"]}')
            current = {'line': number, 'fields': {}}
            continue
        end = _END_RE.fullmatch(rest)
        if end:
            if current is None:
                fail('"end" with no "begin"')
            count = int(end.group(1))
            if count != len(current['fields']) or count != len(KEYS):
                fail(f'"end lines={count}" but the block has {len(current["fields"])} '
                     f'key lines and a record has {len(KEYS)}')
            blocks.append(current['fields'])
            current = None
            continue
        pair = _KEY_LINE_RE.fullmatch(rest)
        if not pair:
            fail(f'not "begin", "end lines=N" or key=value: {rest!r}')
        key, value = pair.groups()
        if current is None:
            fail(f'key {key!r} outside a begin/end block')
        if key not in KEYS:
            fail(f'unknown key {key!r}')
        if key in current['fields']:
            fail(f'duplicate key {key!r}')
        current['fields'][key] = value
    if current is not None:
        raise RecordError(f'the record that began at line {current["line"]} has no "end"')
    if not blocks:
        raise RecordError('no NV2R record found')
    if len(blocks) > 1 and not last:
        raise RecordError(f'{len(blocks)} records found; use --last to take the final one')
    return record_from_fields(blocks[-1])


# ---- JSON form --------------------------------------------------------------

def format_json(rec):
    return json.dumps(fields_of(rec), indent=2) + '\n'


def parse_json(text):
    def reject_duplicates(pairs):
        keys = [k for k, _ in pairs]
        for key in keys:
            if keys.count(key) > 1:
                raise RecordError(f'duplicate key {key!r}')
        return dict(pairs)

    try:
        obj = json.loads(text, object_pairs_hook=reject_duplicates)
    except json.JSONDecodeError as exc:
        raise RecordError(f'not JSON: {exc}') from exc
    # The text form necessarily carries schema_version as a decimal string; the
    # JSON form is documented as the integer, so a string there is a different
    # document than the one this tool writes.
    if isinstance(obj, dict) and isinstance(obj.get('schema_version'), str):
        raise RecordError('schema_version: a JSON string is not the integer 1')
    return record_from_fields(obj)


# ---- binary form ------------------------------------------------------------

def pack(rec):
    body = WIRE.pack(MAGIC, VERSION, 0, rec.soc, rec.build_id, rec.midr_el1,
                     rec.revidr_el1, rec.id_aa64mmfr2_el1, rec.id_aa64mmfr4_el1,
                     rec.vncr_redirect, rec.negative_control_trapped, rec.evidence,
                     0, 0, rec.tool.encode('ascii'), bytes(RESERVED2_LEN), 0)[:CRC_OFFSET]
    return body + struct.pack('<I', crc32(body))


def seal(data):
    """The record with its CRC-32 recomputed: how a test builds a record that is
    wrong in exactly one field."""
    data = bytes(data)
    return data[:CRC_OFFSET] + struct.pack('<I', crc32(data[:CRC_OFFSET])) + data[SIZE:]


def _tool_field_valid(tool):
    i = 0
    while i < TOOL_LEN and tool[i] != 0:
        if not re.fullmatch(rb'[A-Za-z0-9._-]', tool[i:i + 1]):
            return False
        i += 1
    if i == 0 or i == TOOL_LEN:
        return False
    return not any(tool[i:])


def parse_binary(data):
    """Structural validation, identical to snap_nv2_record_parse(): returns
    (PARSED, Record) or (a DENY value, None)."""
    if not data:
        return DENY['DENY_NO_RECORD'], None
    if len(data) != SIZE:
        return DENY['DENY_LENGTH'], None
    (magic, version, flags, soc, build_id, midr, revidr, mmfr2, mmfr4, vncr,
     negctl, evidence, reserved0, reserved1, tool, reserved2, crc) = WIRE.unpack(data)
    if magic != MAGIC:
        return DENY['DENY_MAGIC'], None
    if version != VERSION:
        return DENY['DENY_VERSION'], None
    if crc32(data[:CRC_OFFSET]) != crc:
        return DENY['DENY_CRC'], None
    if (flags or reserved0 or reserved1 or any(reserved2)
            or not _tool_field_valid(tool)):
        return DENY['DENY_RESERVED'], None
    if soc > SOC['x2e-96-100'] or vncr > RESULT['fail'] or negctl > RESULT['fail'] \
            or evidence > EVIDENCE['UNIMPLEMENTED']:
        return DENY['DENY_RESERVED'], None
    name = tool.split(b'\0', 1)[0].decode('ascii')
    return PARSED, Record(soc, midr, revidr, mmfr2, mmfr4, vncr, negctl, evidence,
                          name, build_id)


def unpack(data):
    verdict, rec = parse_binary(data)
    if verdict != PARSED:
        raise RecordError(f'binary record rejected: {verdict_name(verdict)}')
    return rec


def id_advertises_nv2(mmfr2, mmfr4):
    """ID_AA64MMFR2_EL1.NV [27:24] == 2 and ID_AA64MMFR4_EL1.NV_frac [23:20] == 0;
    see compat/nv2-gate.h for why nothing else counts."""
    return ((mmfr2 >> 24) & 0xF) == 2 and ((mmfr4 >> 20) & 0xF) == 0


def decide(data, expect_soc, midr, revidr, mmfr2, mmfr4):
    """snap_nv2_gate_decide(): the gate's verdict for a record (bytes or None)
    on a core that reads the given registers. `expect_soc` is a SOC number."""
    verdict, rec = parse_binary(data)
    if verdict != PARSED:
        return verdict
    if expect_soc == SOC['unknown'] or rec.soc != expect_soc:
        return DENY['DENY_SOC']
    if midr == 0 or rec.midr_el1 != midr:
        return DENY['DENY_MIDR']
    if rec.revidr_el1 != revidr:
        return DENY['DENY_REVIDR']
    if not id_advertises_nv2(mmfr2, mmfr4):
        return DENY['DENY_NOT_ADVERTISED']
    if rec.id_aa64mmfr2_el1 != mmfr2 or rec.id_aa64mmfr4_el1 != mmfr4:
        return DENY['DENY_ID_MISMATCH']
    if rec.vncr_redirect == RESULT['not-run']:
        return DENY['DENY_VNCR_NOT_RUN']
    if rec.vncr_redirect != RESULT['pass']:
        return DENY['DENY_VNCR_FAIL']
    if rec.negative_control_trapped == RESULT['not-run']:
        return DENY['DENY_NEGCTL_NOT_RUN']
    if rec.negative_control_trapped != RESULT['pass']:
        return DENY['DENY_NEGCTL_FAIL']
    if rec.evidence not in (EVIDENCE['VERIFIED'], EVIDENCE['OBSERVED']):
        return DENY['DENY_EVIDENCE']
    return ALLOW


def record_only_denial(rec):
    """The deny reason that no live register values can change, or None."""
    for verdict, condition in (
            (DENY['DENY_VNCR_NOT_RUN'], rec.vncr_redirect == RESULT['not-run']),
            (DENY['DENY_VNCR_FAIL'], rec.vncr_redirect == RESULT['fail']),
            (DENY['DENY_NEGCTL_NOT_RUN'], rec.negative_control_trapped == RESULT['not-run']),
            (DENY['DENY_NEGCTL_FAIL'], rec.negative_control_trapped == RESULT['fail']),
            (DENY['DENY_EVIDENCE'], rec.evidence not in (EVIDENCE['VERIFIED'],
                                                          EVIDENCE['OBSERVED'])),
            (DENY['DENY_NOT_ADVERTISED'], not id_advertises_nv2(rec.id_aa64mmfr2_el1,
                                                                rec.id_aa64mmfr4_el1))):
        if condition:
            return verdict
    return None


def emit_header(data):
    lines = ['/* SPDX-License-Identifier: MIT */',
             '/* Generated by tools/nv2_record.py. It carries a measured record of one',
             ' * machine: do not commit it. */',
             f'static const unsigned char snap_nv2_record_bytes[{len(data)}] = {{']
    for offset in range(0, len(data), 12):
        chunk = data[offset:offset + 12]
        lines.append('    ' + ', '.join(f'0x{b:02x}' for b in chunk) + ',')
    lines.append('};')
    return '\n'.join(lines) + '\n'


# ---- loading ----------------------------------------------------------------

def _read_source(source):
    """The bytes of a record source: standard input ('-') or a regular file, at most MAX_INPUT_BYTES.

    The path is inspected before it is opened: opening a device node or a terminal can act on the device, and a
    FIFO without a writer blocks the open for ever. Only a regular file that is not itself a link is opened, and
    the descriptor must still be the file that was inspected.
    """
    if source == '-':
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise RecordError(f'standard input is larger than {MAX_INPUT_BYTES} bytes')
        return raw
    listed = os.lstat(source)
    if not stat.S_ISREG(listed.st_mode):
        raise RecordError(f'{source}: not a regular file')
    if listed.st_size > MAX_INPUT_BYTES:
        raise RecordError(f'{source}: larger than {MAX_INPUT_BYTES} bytes')
    fd = os.open(source, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (listed.st_dev, listed.st_ino):
            raise RecordError(f'{source}: not a regular file')
        chunks, total = [], 0
        while True:
            block = os.read(fd, 1 << 20)
            if not block:
                return b''.join(chunks)
            total += len(block)
            if total > MAX_INPUT_BYTES:
                raise RecordError(f'{source}: larger than {MAX_INPUT_BYTES} bytes')
            chunks.append(block)
    finally:
        os.close(fd)


def load(source, fmt='auto', last=False):
    """Read a record in any form. Returns (Record, binary bytes)."""
    raw = _read_source(source)
    if fmt == 'auto':
        if len(raw) == SIZE and raw[:4] == b'NV2R':
            fmt = 'bin'
        elif raw.lstrip()[:1] == b'{':
            fmt = 'json'
        else:
            fmt = 'text'
    if fmt == 'bin':
        rec = unpack(raw)
        return rec, pack(rec)
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise RecordError(f'input is not UTF-8 text: {exc}') from exc
    rec = parse_json(text) if fmt == 'json' else parse_text(text, last)
    return rec, pack(rec)


def _write(path, data, binary):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, 'wb' if binary else 'w', **({} if binary else {'newline': '\n'})) as out:
        out.write(data)


def describe(rec, data):
    fields = fields_of(rec)
    lines = [f'NV2 record version {VERSION}, {len(data)} bytes, crc32 '
             f'{struct.unpack("<I", data[CRC_OFFSET:])[0]:#010x}']
    lines += [f'  {key:<26}{value}' for key, value in fields.items()
              if key != 'schema_version']
    return '\n'.join(lines)


def _live_values(args):
    given = [args.live_midr, args.live_revidr, args.live_mmfr2, args.live_mmfr4]
    if all(v is None for v in given):
        return None
    if any(v is None for v in given):
        raise RecordError('give all of --live-midr, --live-revidr, --live-mmfr2 and --live-mmfr4')
    return tuple(_to_int(name, value, 64) for name, value in zip(
        ('--live-midr', '--live-revidr', '--live-mmfr2', '--live-mmfr4'), given))


def cmd_ingest(args):
    rec, data = load(args.input, args.format, args.last)
    _write(args.out, data, True)
    _write(args.header, emit_header(data), False)
    if args.json:
        _write(args.json, format_json(rec), False)
    print(describe(rec, data))
    print(f'wrote {args.out} and {args.header}' + (f' and {args.json}' if args.json else ''))
    blocker = record_only_denial(rec)
    if blocker is not None:
        print(f'note: the gate will deny this record whatever the live registers read: '
              f'{verdict_name(blocker)}')
    print('note: the gate also compares MIDR, REVIDR and both ID registers with the core it '
          'runs on; check with `show --live-*`')
    return 0


def cmd_show(args):
    rec, data = load(args.input, args.format, args.last)
    print(describe(rec, data))
    print(f'parse: {verdict_name(PARSED)}')
    live = _live_values(args)
    if live is None:
        if not args.check:
            return 0
        # --check promises a gate decision. Without live registers only a record that no register value could
        # rescue has one (deny); for any other record there is none, and returning 0 would read as a pass.
        blocker = record_only_denial(rec)
        if blocker is None:
            raise UsageError('--check needs --live-midr, --live-revidr, --live-mmfr2 and --live-mmfr4: without '
                             'them there is no gate decision for this record')
        print(f'gate: {verdict_name(blocker)} (prepare status {verdict_status(blocker)}), '
              'whatever the live registers read')
        return 3
    verdict = decide(data, SOC[args.soc], *live)
    print(f'gate: {verdict_name(verdict)} (prepare status {verdict_status(verdict)})')
    return 3 if args.check and verdict != ALLOW else 0


def build_parser():
    parser = argparse.ArgumentParser(description='NV2 functional-test records')
    sub = parser.add_subparsers(dest='command', required=True)

    def common(p):
        p.add_argument('input', help='record file, or - for standard input')
        p.add_argument('--format', choices=('auto', 'text', 'json', 'bin'), default='auto')
        p.add_argument('--last', action='store_true',
                       help='text form: take the last complete block when there are several')

    ingest = sub.add_parser('ingest', help='convert a record to the binary form and a C header')
    common(ingest)
    ingest.add_argument('--out', default='build/nv2-record.bin')
    ingest.add_argument('--header', default='build/nv2-record.h')
    ingest.add_argument('--json', help='also write the canonical JSON form here')
    ingest.set_defaults(func=cmd_ingest)

    show = sub.add_parser('show', help='print a record and, with live registers, the gate verdict')
    common(show)
    show.add_argument('--soc', choices=[n for n in SOC if n != 'unknown'], default='x1e80100',
                      help='the SoC the monitor build is for')
    show.add_argument('--live-midr')
    show.add_argument('--live-revidr')
    show.add_argument('--live-mmfr2')
    show.add_argument('--live-mmfr4')
    show.add_argument('--check', action='store_true',
                      help='exit 3 unless the gate would allow (needs all four --live-* values, except for a '
                           'record the gate can never allow, which exits 3 without them)')
    show.set_defaults(func=cmd_show)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except UsageError as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2
    except (RecordError, OSError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
