#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""SP11 NV2 launch gate (A3): the record format, the monitor's decision, and where the gate sits.

    python3 tools/test-nv2-gate.py

Offline: no hardware and no proxy. Every register value in it is made up; nothing is a
measurement of any machine, and nothing here shows that an Oryon v1 implements NV2. What it
proves is that the gate fails closed, that the host tool and the monitor agree on the format,
and that the monitor reaches the gate before it could set HCR_EL2.NV/NV2.

It builds tools/test-nv2-gate.c (address and undefined behaviour sanitizers) against
compat/nv2-gate.c and runs: a table-driven self-test with its own record packer and CRC-32;
every single-bit change of a sealed record, with and without resealing; every bit of each live
ID register; the NV x NV_frac grid; and the record embedded through
-DSNAP_NV2_RECORD_HEADER. It then runs the same thousands of (record, SoC, MIDR, REVIDR,
MMFR2, MMFR4) cases through that C code and through tools/nv2_record.py and requires the
verdict, the decoded view and the prepare status to agree for each, and the header's enum
order and values to equal the tool's tables. The text, JSON and binary forms are round-tripped
and fed malformed input; the CRC-8 snippet in the tool's documentation is compiled and
compared with the tool; the synthetic example is checked line by line; the command line is run.

Wiring: probes/run.py links the unit only with -DSNAP_PLATFORM_SP11=1 and names every status the
gate can return; probes/xnuboot/prepare.c reaches the gate before the first VNCR_EL2 write and
the first NV/NV2 bit, returns its status, reads and never writes registers on refusal, and
keeps every mention of the gate inside #if SNAP_PLATFORM_SP11; the monitor's translation unit
preprocessed without the define contains none of it (the byte-level proof is the A16 firewall,
tools/monitor_baseline.py).

Weakened copies of compat/nv2-gate.c and of the hook must FAIL: MUTANTS and HOOK_MUTANTS below
list them, and the first failure each one causes is printed. Scratch files live under
build/nv2-gate-test/ and are removed.
"""
from pathlib import Path
import contextlib
import itertools
import json
import random
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nv2_record as nv2  # noqa: E402
from source_text import read_source  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / 'build' / 'nv2-gate-test'
GATE_C = ROOT / 'compat/nv2-gate.c'
GATE_H = ROOT / 'compat/nv2-gate.h'
PREPARE_C = ROOT / 'probes/xnuboot/prepare.c'
RUN_PY = ROOT / 'probes/run.py'

LIVE = dict(midr=0xFEDCBA9876543210, revidr=0x0102030405060708,
            mmfr2=0x1122334402667788, mmfr4=0x99AABBCC0F0E0102)
# The sealed record of the registers above, the same one tools/test-nv2-gate.c builds.
FIELDS = dict(soc=1, build_id=0x0BADF00D, midr=LIVE['midr'], revidr=LIVE['revidr'],
              mmfr2=LIVE['mmfr2'], mmfr4=LIVE['mmfr4'], vncr=1, negctl=1, evidence=1)


def scratch(prefix):
    BUILD.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(prefix=prefix, dir=BUILD)


def run(command, **kwargs):
    return subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                          timeout=kwargs.pop('timeout', 300), **kwargs)


def compile_test(exe, gate=GATE_C, defines=(), sanitize=True):
    flags = ['-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror']
    if sanitize:
        flags += ['-fsanitize=address,undefined', '-fno-sanitize-recover=all']
    else:
        # A weakened copy may leave a helper unused; it must still compile.
        flags = ['-std=gnu11', '-O1', '-w']
    result = run(['cc', *flags, *defines, '-Icompat', 'tools/test-nv2-gate.c', str(gate),
                  '-o', str(exe)])
    if result.returncode:
        raise AssertionError('compile failed:\n' + result.stderr[-3000:])


def wire(*, magic=nv2.MAGIC, version=1, flags=0, soc=1, build_id=0x0BADF00D, midr=LIVE['midr'],
         revidr=LIVE['revidr'], mmfr2=LIVE['mmfr2'], mmfr4=LIVE['mmfr4'], vncr=1, negctl=1,
         evidence=1, reserved0=0, reserved1=0, tool=b'unit-test.1', reserved2=bytes(20)):
    """A sealed 96-byte record with raw field values, defined or not."""
    return nv2.seal(nv2.WIRE.pack(magic, version, flags, soc, build_id, midr, revidr, mmfr2, mmfr4,
                                  vncr, negctl, evidence, reserved0, reserved1, tool, reserved2, 0))


def make_rec(**changes):
    values = dict(soc=1, midr_el1=LIVE['midr'], revidr_el1=LIVE['revidr'],
                  id_aa64mmfr2_el1=LIVE['mmfr2'], id_aa64mmfr4_el1=LIVE['mmfr4'],
                  vncr_redirect=1, negative_control_trapped=1, evidence=1, tool='unit-test.1',
                  build_id=0x0BADF00D)
    values.update(changes)
    return nv2.Record(**values)


def verdict_text(verdict):
    return {nv2.ALLOW: 'A', nv2.PARSED: 'P'}.get(verdict, str(verdict))


def c_batch(exe, mode, lines):
    result = subprocess.run([str(exe), mode], input='\n'.join(lines) + '\n', capture_output=True,
                            text=True, timeout=300)
    if result.returncode:
        raise AssertionError(f'{mode}: exit {result.returncode}\n{result.stderr[-2000:]}')
    return result.stdout.split('\n')[:-1]


def corpus():
    """(record or None, expected SoC, MIDR, REVIDR, MMFR2, MMFR4) tuples that reach every verdict."""
    rng = random.Random(0x4E325052)
    cases = []

    def add(data, soc=1, **live):
        values = dict(LIVE, **live)
        cases.append((data, soc, values['midr'], values['revidr'], values['mmfr2'], values['mmfr4']))

    good = wire()
    add(good)
    add(None)
    add(b'')
    for length in range(1, 130):
        add(good[:length] if length <= 96 else good + bytes(length - 96))
    for soc in (0, 2, 3, 0xFFFFFFFF):
        add(good, soc=soc)
    for i in range(96):
        for bit in range(8):
            flipped = bytearray(good)
            flipped[i] ^= 1 << bit
            add(bytes(flipped))
            add(nv2.seal(flipped))
    for soc, vncr, negctl, evidence in itertools.product(range(5), range(4), range(4), range(8)):
        record = wire(soc=soc, vncr=vncr, negctl=negctl, evidence=evidence)
        add(record, soc=1)
        add(record, soc=soc)
    for name in LIVE:
        for bit in range(64):
            add(good, **{name: LIVE[name] ^ (1 << bit)})
        add(good, **{name: 0})
        add(good, **{name: (1 << 64) - 1})
    for nv, frac, e2h0 in itertools.product(range(16), range(16), (0, 0xF)):
        mmfr2 = (LIVE['mmfr2'] & ~(0xF << 24)) | (nv << 24)
        mmfr4 = (LIVE['mmfr4'] & ~(0xFF << 20)) | (e2h0 << 24) | (frac << 20)
        add(wire(mmfr2=mmfr2, mmfr4=mmfr4), mmfr2=mmfr2, mmfr4=mmfr4)
        add(good, mmfr2=mmfr2, mmfr4=mmfr4)
    add(wire(midr=0), midr=0)
    add(wire(midr=0, revidr=0, mmfr2=0, mmfr4=0), midr=0, revidr=0, mmfr2=0, mmfr4=0)
    for _ in range(800):
        damaged = bytearray(good)
        for _ in range(rng.randint(1, 6)):
            damaged[rng.randrange(nv2.CRC_OFFSET)] = rng.randrange(256)
        add(nv2.seal(damaged))
    for _ in range(100):
        add(bytes(rng.randrange(256) for _ in range(rng.choice((96, 96, 96, 95, 97, 200)))))
    return cases


# ---- an independent CRC-8 for the text form -------------------------------------------------

def _table():
    table = []
    for n in range(256):
        crc = n
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
        table.append(crc)
    return table


TABLE8 = _table()


def ref_crc8(data):
    crc = 0
    for byte in data:
        crc = TABLE8[crc ^ byte]
    return crc


def checked(body):
    return f'{body}*{ref_crc8(body.encode("utf-8", "surrogatepass")):02x}'


def text_of(fields=None, end_count=None, drop=(), extra=(), reorder=False):
    """A text block whose every line carries correct check digits."""
    values = dict(nv2.fields_of(make_rec()) if fields is None else fields)
    for key in drop:
        del values[key]
    items = list(values.items())
    if reorder:
        items.reverse()
    bodies = ['NV2R begin'] + [f'NV2R {k}={v}' for k, v in items] + list(extra)
    bodies.append(f'NV2R end lines={len(values) if end_count is None else end_count}')
    return '\n'.join(checked(b) for b in bodies) + '\n'


def field_text(key, value):
    values = nv2.fields_of(make_rec())
    values[key] = value
    return text_of(values)


# ---- source structure -----------------------------------------------------------------------

def function(name, text):
    match = re.search(r'^[^\n;]*\b' + re.escape(name) + r'\([^;{]*\)\n\{\n', text, re.M)
    if not match:
        raise AssertionError(f'{name} definition missing')
    return text[match.start():text.index('\n}\n', match.end()) + 3]


def guarded_lines(text, macro='SNAP_PLATFORM_SP11'):
    """Per line: is it in the true branch of an #if that names `macro`?"""
    stack, inside = [], []
    for line in text.split('\n'):
        stripped = line.strip()
        if stripped.startswith('#if'):
            stack.append([macro in stripped and not stripped.startswith('#ifndef'), True])
        elif stripped.startswith('#else') and stack:
            stack[-1][1] = False
        elif stripped.startswith('#endif') and stack:
            stack.pop()
        inside.append(any(on and branch for on, branch in stack))
    return inside


def need(condition, message):
    if not condition:
        raise AssertionError(message)


def check_prepare(text):
    """Everything the hook in probes/xnuboot/prepare.c must satisfy; raises AssertionError."""
    lines = text.split('\n')
    inside = guarded_lines(text)
    for number, line in enumerate(lines):
        if re.search(r'nv2-gate|snap_nv2_|snap_oryon_|sp11_launch_gate|SP11_EVENT', line):
            need(inside[number], f'line {number + 1} is outside #if SNAP_PLATFORM_SP11: {line.strip()}')
    need(text.count('#include "../../compat/nv2-gate.h"') == 1, 'the gate header is not included once')

    prepare = function('vcpu_arch_prepare', text)
    need(prepare.count('sp11_launch_gate(c)') == 1, 'vcpu_arch_prepare must call the gate exactly once')
    need(re.search(r'int64_t gate = sp11_launch_gate\(c\);\s*if \(gate\) return gate;', prepare),
         'the gate status must be returned when non-zero')
    call = prepare.index('sp11_launch_gate(c)')
    need(prepare.count('msr s3_4_c2_c2_0') == 1 and prepare.count('(1UL << 42)') == 1,
         'expected exactly one VNCR_EL2 write and one NV bit set')
    need(call < prepare.index('msr s3_4_c2_c2_0'), 'the gate must come before the VNCR_EL2 write')
    need(call < prepare.index('(1UL << 42)'), 'the gate must come before HCR_EL2.NV')
    need(text.count('msr s3_4_c2_c2_0') == 1 and text.count('(1UL << 42)') == 1,
         'another VNCR_EL2 write or NV bit set exists in the file')

    gate = function('sp11_launch_gate', text)
    need('msr' not in gate, 'a refused launch must only read registers')
    need(gate.count('return 0;') == 1, 'the gate has one success return')
    need(re.search(r'if \(nv2 == SNAP_NV2_ALLOW &&\s*\(pauth == SNAP_ORYON_NORMALIZED \|\| '
                   r'pauth == SNAP_ORYON_PASSTHROUGH\)\)\s*return 0;', gate),
         'success requires both an ALLOW verdict and an accepted PAuth shape')
    need('snap_nv2_gate_decide_embedded(SNAP_NV2_PLATFORM_SOC, midr, revidr, mmfr2, mmfr4)' in gate,
         'the decision must use this CPU\'s own ID registers')
    for name in ('save_mdcr', 'save_mdscr', 'dbg_oslsr', 'save_ttbr0', 'save_ttbr1', 'save_mair'):
        need(f'v->{name}' in gate, f'{name} is not captured before a refusal returns')


def drop_text(text, old, new=''):
    need(text.count(old) == 1, f'mutation anchor not unique: {old!r}')
    return text.replace(old, new)


GATE_REGION = re.compile(r'#if SNAP_PLATFORM_SP11\n    /\* xnuboot_prepare has refused.*?\n#endif\n', re.S)
VNCR_WRITE = '        __asm__ volatile("msr s3_4_c2_c2_0, %0" :: "r"(vcpu_of(c)->vncr_va));\n'


def gate_region(text):
    match = GATE_REGION.search(text)
    need(match is not None, 'the guarded gate call is missing from vcpu_arch_prepare')
    return match.group(0)


def gate_moved_after_vncr_write(text):
    region = gate_region(text)
    return drop_text(text.replace(region, ''), VNCR_WRITE, VNCR_WRITE + region)


# name: (how to weaken probes/xnuboot/prepare.c, a fragment of the failure it must cause).
# The fragment pins WHICH rule catches it, so no mutant is killed by an unrelated one.
HOOK_MUTANTS = {
    'gate call removed': (
        lambda t: t.replace(gate_region(t), ''), 'must call the gate exactly once'),
    'gate status ignored': (
        lambda t: drop_text(t, 'if (gate) return gate;', '(void)gate;'), 'status must be returned'),
    'gate after the VNCR write': (gate_moved_after_vncr_write, 'must come before the VNCR_EL2 write'),
    'call outside the define': (
        lambda t: drop_text(t, '#if SNAP_PLATFORM_SP11\n    /* xnuboot_prepare has refused',
                            '#if 1\n    /* xnuboot_prepare has refused'), 'outside #if SNAP_PLATFORM_SP11'),
    'include outside the define': (
        lambda t: drop_text(t, '#if SNAP_PLATFORM_SP11\n#include "../../compat/nv2-gate.h"\n#endif\n',
                            '#include "../../compat/nv2-gate.h"\n'), 'outside #if SNAP_PLATFORM_SP11'),
    'second NV bit site': (
        lambda t: drop_text(t, '    if (SNAP_NATIVE_WFE) {\n        /* TWE off',
                            '    { uint64_t nv = (1UL << 42); (void)nv; }\n'
                            '    if (SNAP_NATIVE_WFE) {\n        /* TWE off'),
        'exactly one VNCR_EL2 write and one NV bit set'),
    'refusal writes a register': (
        lambda t: drop_text(t, '    return status;\n}\n',
                            '    __asm__ volatile("msr hcr_el2, xzr");\n    return status;\n}\n'),
        'only read registers'),
    'allow on the NV2 verdict alone': (
        lambda t: drop_text(
            t, '    if (nv2 == SNAP_NV2_ALLOW &&\n        (pauth == SNAP_ORYON_NORMALIZED || pauth == SNAP_ORYON_PASSTHROUGH))\n        return 0;',
            '    if (nv2 == SNAP_NV2_ALLOW)\n        return 0;'),
        'both an ALLOW verdict and an accepted PAuth shape'),
    'allow on the PAuth verdict alone': (
        lambda t: drop_text(
            t, 'if (nv2 == SNAP_NV2_ALLOW &&\n        (pauth == SNAP_ORYON_NORMALIZED || pauth == SNAP_ORYON_PASSTHROUGH))',
            'if (pauth == SNAP_ORYON_NORMALIZED || pauth == SNAP_ORYON_PASSTHROUGH)'),
        'both an ALLOW verdict and an accepted PAuth shape'),
    'record decided for another core': (
        lambda t: drop_text(
            t, 'snap_nv2_gate_decide_embedded(SNAP_NV2_PLATFORM_SOC, midr, revidr, mmfr2, mmfr4)',
            'snap_nv2_gate_decide_embedded(SNAP_NV2_PLATFORM_SOC, midr, revidr, mmfr4, mmfr2)'),
        "this CPU's own ID registers"),
    'restore field not captured': (
        lambda t: drop_text(t, '    __asm__ volatile("mrs %0, mair_el1" : "=r"(v->save_mair));\n'),
        'save_mair is not captured'),
}

# Each weakening of compat/nv2-gate.c must make the C test fail.
MUTANTS = {
    'MIDR not compared': ('if (midr == 0 || v.midr_el1 != midr)', 'if (midr == 0)'),
    'zero MIDR agrees with zero': ('if (midr == 0 || v.midr_el1 != midr)', 'if (v.midr_el1 != midr)'),
    'REVIDR not compared': ('if (v.revidr_el1 != revidr)', 'if (0)'),
    'ID registers not compared': ('if (v.id_aa64mmfr2_el1 != mmfr2 || v.id_aa64mmfr4_el1 != mmfr4)',
                                  'if (0)'),
    'NV 1 advertises': ('return ((mmfr2 >> 24) & 0xf) == 2 &&', 'return ((mmfr2 >> 24) & 0xf) >= 1 &&'),
    'NV_frac read from E2H0': ('((mmfr4 >> 20) & 0xf) == 0;', '((mmfr4 >> 24) & 0xf) == 0;'),
    'NV_frac ignored': (' && ((mmfr4 >> 20) & 0xf) == 0;', ';'),
    'NV read from the wrong field': ('return ((mmfr2 >> 24) & 0xf) == 2 &&', 'return ((mmfr2 >> 28) & 0xf) == 2 &&'),
    'VNCR not-run accepted as a result': ('if (v.vncr_redirect == SNAP_NV2_NOT_RUN)\n        return SNAP_NV2_DENY_VNCR_NOT_RUN;',
                                          'if (0)\n        return SNAP_NV2_DENY_VNCR_NOT_RUN;'),
    'VNCR failure accepted': ('if (v.vncr_redirect != SNAP_NV2_PASS)', 'if (0)'),
    'control not-run accepted': ('if (v.negative_control_trapped == SNAP_NV2_NOT_RUN)\n        return SNAP_NV2_DENY_NEGCTL_NOT_RUN;',
                                 'if (0)\n        return SNAP_NV2_DENY_NEGCTL_NOT_RUN;'),
    'negative control failure accepted': ('if (v.negative_control_trapped != SNAP_NV2_PASS)', 'if (0)'),
    'inferred evidence accepted': ('v.evidence != SNAP_NV2_EVIDENCE_OBSERVED)',
                                   'v.evidence != SNAP_NV2_EVIDENCE_OBSERVED &&\n        v.evidence != SNAP_NV2_EVIDENCE_INFERRED)'),
    'evidence never checked': ('if (v.evidence != SNAP_NV2_EVIDENCE_VERIFIED &&', 'if (0 &&'),
    'SoC not compared': ('if (expect_soc == SNAP_NV2_SOC_UNKNOWN || v.soc != expect_soc)',
                         'if (expect_soc == SNAP_NV2_SOC_UNKNOWN)'),
    'unknown SoC matches itself': ('if (expect_soc == SNAP_NV2_SOC_UNKNOWN || v.soc != expect_soc)',
                                   'if (v.soc != expect_soc)'),
    'CRC not checked': ('if (snap_nv2_crc32(p, offsetof(struct snap_nv2_record, crc32)) !=',
                        'if (0 && snap_nv2_crc32(p, offsetof(struct snap_nv2_record, crc32)) !='),
    'CRC polynomial': ('0xedb88320u &', '0xedb88321u &'),
    'version not checked': ('if (load16(p + offsetof(struct snap_nv2_record, version)) != SNAP_NV2_RECORD_VERSION)',
                            'if (0)'),
    'magic not checked': ('if (load32(p + offsetof(struct snap_nv2_record, magic)) != SNAP_NV2_RECORD_MAGIC)',
                          'if (0)'),
    'longer records accepted': ('if (length != SNAP_NV2_RECORD_SIZE)', 'if (length < SNAP_NV2_RECORD_SIZE)'),
    'reserved area unchecked': ('!bytes_all_zero(p + offsetof(struct snap_nv2_record, reserved2),\n'
                                '                        SNAP_NV2_RESERVED2_LEN) ||', '0 ||'),
    'tool name unchecked': ('!tool_field_valid(p + offsetof(struct snap_nv2_record, tool)))', '0)'),
    'SoC range unchecked': ('load32(p + offsetof(struct snap_nv2_record, soc)) > SNAP_NV2_SOC_X2E_96_100 ||', '0 ||'),
    'result range unchecked': ('p[offsetof(struct snap_nv2_record, vncr_redirect)] > SNAP_NV2_FAIL ||', '0 ||'),
    'evidence range unchecked': ('p[offsetof(struct snap_nv2_record, evidence)] > SNAP_NV2_EVIDENCE_UNIMPLEMENTED)',
                                 '0)'),
    'view kept after a failed parse': ('if (view)\n        view_clear(view);', 'if (0)\n        view_clear(view);'),
    '64-bit halves swapped': ('return (uint64_t)load32(p) | (uint64_t)load32(p + 4) << 32;',
                              'return (uint64_t)load32(p + 4) | (uint64_t)load32(p) << 32;'),
    '32-bit load byte order': ('(uint32_t)p[3] << 24;', '(uint32_t)p[3] << 16;'),
    'unknown verdict reads as success': ('return SNAP_NV2_STATUS_RECORD;\n    }', 'return 0;\n    }'),
    'no-NV2 status folded into mismatch': ('return SNAP_NV2_STATUS_NO_NV2;', 'return SNAP_NV2_STATUS_MISMATCH;'),
}


class Format(unittest.TestCase):
    EXAMPLE = {
        'lines': [
            ('NV2R begin', '78'), ('NV2R schema_version=1', 'fc'), ('NV2R soc=x1e80100', '63'),
            ('NV2R midr_el1=0x00000000feed0001', '75'), ('NV2R revidr_el1=0x0000000000000000', 'e3'),
            ('NV2R id_aa64mmfr2_el1=0x0000000002001234', '9a'),
            ('NV2R id_aa64mmfr4_el1=0x0000000000000000', '14'), ('NV2R vncr_redirect=pass', '85'),
            ('NV2R negative_control_trapped=pass', '51'), ('NV2R evidence=OBSERVED', '13'),
            ('NV2R tool=synthetic-doc', 'f7'), ('NV2R build_id=0x00000001', '1c'),
            ('NV2R end lines=11', 'cb')],
        'crc32': 0x06C43036,
    }

    def doc_example(self):
        doc = nv2.__doc__
        block = doc[doc.index('SYNTHETIC EXAMPLE'):doc.index('CRC-8 (the "*hh" check)')]
        lines = [line.strip() for line in block.split('\n') if line.strip().startswith('NV2R ')]
        hexdump = doc[doc.index('The same synthetic example as bytes'):doc.index('Exit status:')]
        words = re.findall(r'^    ((?:[0-9a-f]{2} ?)+)$', hexdump, re.M)
        return '\n'.join(lines) + '\n', bytes.fromhex(''.join(words))

    def test_documented_example_check_digits_and_bytes(self):
        text, data = self.doc_example()
        for number, ((body, digits), line) in enumerate(zip(self.EXAMPLE['lines'], text.split('\n'))):
            self.assertEqual(line, f'{body}*{digits}', number)
            self.assertEqual(ref_crc8(body.encode()), int(digits, 16), body)
        self.assertEqual(len(text.strip().split('\n')), 13)
        rec = nv2.parse_text(text)
        self.assertEqual((rec.soc, rec.midr_el1, rec.revidr_el1, rec.id_aa64mmfr2_el1,
                          rec.id_aa64mmfr4_el1, rec.vncr_redirect, rec.negative_control_trapped,
                          rec.evidence, rec.tool, rec.build_id),
                         (1, 0xFEED0001, 0, 0x02001234, 0, 1, 1, 2, 'synthetic-doc', 1))
        self.assertEqual(len(data), 96)
        self.assertEqual(nv2.pack(rec), data)
        self.assertEqual(struct.unpack('<I', data[0x5C:])[0], self.EXAMPLE['crc32'])
        self.assertEqual(zlib.crc32(data[:0x5C]), self.EXAMPLE['crc32'])
        self.assertEqual(nv2.format_text(rec), text)

    def test_example_is_not_a_real_core(self):
        # Made-up values must not name a core the repository knows (and so never allow one).
        known = (0x511F0011, 0x512F0011, 0x512F0021)
        text, data = self.doc_example()
        midr = nv2.parse_text(text).midr_el1
        self.assertNotIn(midr, known)
        self.assertNotIn(midr & 0xFFFFFFFF, known)

    def test_wire_layout_matches_the_documented_offsets(self):
        fmt = nv2.WIRE.format.lstrip('<')
        offsets, at = [], 0
        for item in re.findall(r'\d*[A-Za-z]', fmt):
            count = int(item[:-1]) if item[:-1] else 1
            code = item[-1]
            size = struct.calcsize('<' + code)
            if code in 'sp':
                offsets.append(at)
                at += count
            else:
                for _ in range(count):
                    offsets.append(at)
                    at += size
        self.assertEqual(offsets, [0x00, 0x04, 0x06, 0x08, 0x0C, 0x10, 0x18, 0x20, 0x28, 0x30, 0x31,
                                   0x32, 0x33, 0x34, 0x38, 0x48, 0x5C])
        self.assertEqual(at, 96)
        self.assertEqual((nv2.SIZE, nv2.CRC_OFFSET, nv2.MAGIC, nv2.VERSION), (96, 0x5C, 0x5232564E, 1))
        self.assertEqual(struct.pack('<I', nv2.MAGIC), b'NV2R')

    def test_crc8_check_value_and_the_documented_c_snippet(self):
        self.assertEqual(nv2.crc8(b'123456789'), 0xF4)
        self.assertEqual(ref_crc8(b'123456789'), 0xF4)
        doc = nv2.__doc__
        match = re.search(r'^    (static unsigned crc8\(.*?^    \})\n', doc, re.S | re.M)
        self.assertIsNotNone(match, 'the CRC-8 snippet is missing from the documentation')
        snippet = '\n'.join(line[4:] if line.startswith('    ') else line
                            for line in match.group(1).split('\n'))
        main = ('#include <stdio.h>\n#include <string.h>\n' + snippet + '\n'
                'int main(void) { char line[512]; while (fgets(line, sizeof line, stdin)) {\n'
                '    unsigned n = strcspn(line, "\\n"); printf("%02x\\n", crc8(line, n)); } return 0; }\n')
        rng = random.Random(8)
        samples = ['123456789', '', 'a', 'NV2R begin', 'NV2R end lines=11']
        samples += [body for body, _ in self.EXAMPLE['lines']]
        samples += [''.join(chr(rng.randrange(32, 127)) for _ in range(rng.randrange(1, 90)))
                    for _ in range(200)]
        with scratch('crc8-') as directory:
            source = Path(directory) / 'crc8.c'
            source.write_text(main)
            exe = Path(directory) / 'crc8'
            result = run(['cc', '-std=gnu11', '-O1', '-Wall', '-Wextra', '-Werror', str(source), '-o', str(exe)])
            self.assertEqual(result.returncode, 0, result.stderr)
            out = subprocess.run([str(exe)], input='\n'.join(samples) + '\n', capture_output=True,
                                 text=True, timeout=60).stdout.split('\n')[:-1]
        self.assertEqual(out, [f'{nv2.crc8(s.encode()):02x}' for s in samples])
        self.assertEqual(out, [f'{ref_crc8(s.encode()):02x}' for s in samples])

    def test_round_trips_for_every_enumerated_combination(self):
        for soc, vncr, negctl, evidence in itertools.product(range(3), range(3), range(3), range(6)):
            rec = make_rec(soc=soc, vncr_redirect=vncr, negative_control_trapped=negctl, evidence=evidence)
            with self.subTest(soc=soc, vncr=vncr, negctl=negctl, evidence=evidence):
                self.assertEqual(nv2.parse_text(nv2.format_text(rec)), rec)
                self.assertEqual(nv2.parse_json(nv2.format_json(rec)), rec)
                self.assertEqual(nv2.unpack(nv2.pack(rec)), rec)
                self.assertEqual(nv2.parse_binary(nv2.pack(rec)), (nv2.PARSED, rec))
                self.assertEqual(nv2.pack(nv2.parse_text(nv2.format_text(rec))), nv2.pack(rec))

    def test_round_trips_for_edge_values(self):
        for value in (0, 1, 0x80, 0xFFFFFFFF, 1 << 32, (1 << 63), (1 << 64) - 1, 0x0123456789ABCDEF):
            rec = make_rec(midr_el1=value, revidr_el1=value, id_aa64mmfr2_el1=value,
                           id_aa64mmfr4_el1=value, build_id=value & 0xFFFFFFFF)
            with self.subTest(value=hex(value)):
                self.assertEqual(nv2.parse_text(nv2.format_text(rec)), rec)
                self.assertEqual(nv2.parse_json(nv2.format_json(rec)), rec)
                self.assertEqual(nv2.unpack(nv2.pack(rec)), rec)
        for tool in ('a', 'A.b_c-9', 'x' * 15):
            rec = make_rec(tool=tool)
            self.assertEqual(nv2.unpack(nv2.pack(rec)).tool, tool)
            self.assertEqual(nv2.parse_text(nv2.format_text(rec)).tool, tool)

    def test_text_accepts_surrounding_console_output_and_both_line_endings(self):
        rec = make_rec()
        text = nv2.format_text(rec)
        noise = 'boot: starting\nNV2 is mentioned here\n  NV2R indented is not a record line\n'
        self.assertEqual(nv2.parse_text(noise + text + 'done\n'), rec)
        self.assertEqual(nv2.parse_text(text.replace('\n', '\r\n')), rec)
        self.assertEqual(nv2.parse_text(text.rstrip('\n')), rec)
        self.assertEqual(nv2.parse_text(text_of(reorder=True)), rec)
        self.assertEqual(nv2.parse_text(text_of()), rec)

    def test_several_blocks_need_last_and_an_unfinished_block_never_parses(self):
        first, second = make_rec(build_id=1), make_rec(build_id=2)
        both = nv2.format_text(first) + 'noise\n' + nv2.format_text(second)
        with self.assertRaises(nv2.RecordError):
            nv2.parse_text(both)
        self.assertEqual(nv2.parse_text(both, last=True), second)
        unfinished = nv2.format_text(first) + '\n'.join(nv2.format_text(second).split('\n')[:-2]) + '\n'
        for last in (False, True):
            with self.assertRaises(nv2.RecordError):
                nv2.parse_text(unfinished, last=last)
        damaged = nv2.format_text(first) + nv2.format_text(second).replace('*', '*0', 1)
        with self.assertRaises(nv2.RecordError):
            nv2.parse_text(damaged, last=True)

    def test_text_rejects_every_malformed_form(self):
        good = text_of()
        lines = good.rstrip('\n').split('\n')
        cases = {}
        cases['empty'] = ''
        cases['no record'] = 'hello\nworld\n'
        cases['lower-case prefix'] = good.replace('NV2R', 'nv2r')
        for n in range(len(lines)):
            digits = lines[n][-2:]
            wrong = f'{(int(digits, 16) ^ 1):02x}'
            damaged = list(lines)
            damaged[n] = lines[n][:-2] + wrong
            cases[f'check digits of line {n}'] = '\n'.join(damaged) + '\n'
            damaged[n] = lines[n][:-3]
            cases[f'line {n} without check'] = '\n'.join(damaged) + '\n'
            damaged[n] = lines[n][:-2] + digits[0]
            cases[f'line {n} with one check digit'] = '\n'.join(damaged) + '\n'
            damaged[n] = lines[n] + '0'
            cases[f'line {n} with three check digits'] = '\n'.join(damaged) + '\n'
            damaged[n] = lines[n] + ' '
            cases[f'line {n} with trailing space'] = '\n'.join(damaged) + '\n'
            damaged[n] = lines[n].replace('NV2R ', 'NV2R  ', 1)
            cases[f'line {n} with two spaces'] = '\n'.join(damaged) + '\n'
            damaged[n] = lines[n].replace('NV2R ', 'NV2R\t', 1)
            cases[f'line {n} with a tab'] = '\n'.join(damaged) + '\n'
            damaged[n] = lines[n].replace('NV2R ', 'NV2Rx', 1)
            cases[f'line {n} glued to the prefix'] = '\n'.join(damaged) + '\n'
        cases['unknown key'] = text_of(extra=[f'NV2R vendor=1'])
        cases['key missing'] = text_of(drop=['tool'])
        cases['every key missing'] = text_of(drop=list(nv2.KEYS))
        cases['end count 10'] = text_of(end_count=10)
        cases['end count 12'] = text_of(end_count=12)
        cases['end count 0'] = text_of(end_count=0)
        cases['duplicate key'] = text_of(extra=['NV2R tool=again'])
        cases['key before begin'] = checked('NV2R tool=x') + '\n' + good
        cases['key after end'] = good + checked('NV2R tool=x') + '\n'
        cases['end without begin'] = checked('NV2R end lines=11') + '\n'
        cases['begin twice'] = checked('NV2R begin') + '\n' + good
        cases['begin without end'] = '\n'.join(lines[:-1]) + '\n'
        cases['end spelled differently'] = good.replace(lines[-1], checked('NV2R end lines = 11'))
        cases['end with a sign'] = good.replace(lines[-1], checked('NV2R end lines=+11'))
        cases['end with leading zero'] = good.replace(lines[-1], checked('NV2R end lines=011'))
        cases['non-ASCII'] = field_text('tool', 'caf\u00e9')
        for key, bad in (
                ('schema_version', ['2', '0', '01', '1.0', ' 1', '', '0x1', '-1', '1 ']),
                ('soc', ['X1E80100', 'x1e80100 ', 'x1e', '', '1', 'x1e80100x', 'x2e-96-100.']),
                ('midr_el1', ['feed', '0x', '0X00000001', '0x' + 'f' * 17, '0xg1', '-1', '+0x1',
                              '0x 1', '1', '0x-1', '0x1_0', '0b1']),
                ('revidr_el1', ['0x', '0x' + '1' * 17, 'zero']),
                ('id_aa64mmfr2_el1', ['0x', '0x' + '1' * 17, '2']),
                ('id_aa64mmfr4_el1', ['0x', '0x' + '1' * 17, '']),
                ('vncr_redirect', ['PASS', 'ok', '1', 'not_run', '', 'Pass', 'pass ', 'true']),
                ('negative_control_trapped', ['yes', 'FAIL', '2', 'notrun', '']),
                ('evidence', ['observed', 'Verified', 'MEASURED', '2', '', 'OBSERVED ', 'VERIFIED,OBSERVED']),
                ('tool', ['', 'x' * 16, 'has space', 'slash/', 'a*b', 'a=b', 'tab\t', 'x,y']),
                ('build_id', ['0x123456789', '0x', '1', '0xzz', '-0x1', ''])):
            for value in bad:
                cases[f'{key}={value!r}'] = field_text(key, value)
        for name, text in cases.items():
            with self.subTest(name):
                with self.assertRaises(nv2.RecordError):
                    nv2.parse_text(text, last=True)

    def test_text_accepts_every_documented_spelling(self):
        for key, value, expect in (
                ('midr_el1', '0x1', 1), ('midr_el1', '0xFEDCBA9876543210', 0xFEDCBA9876543210),
                ('midr_el1', '0x0000000000000000', 0), ('midr_el1', '0xffffffffffffffff', (1 << 64) - 1),
                ('revidr_el1', '0xaB', 0xAB), ('id_aa64mmfr2_el1', '0x02000000', 0x02000000),
                ('id_aa64mmfr4_el1', '0xf', 0xF), ('build_id', '0xffffffff', 0xFFFFFFFF),
                ('build_id', '0x0', 0), ('soc', 'x2e-96-100', 2), ('soc', 'unknown', 0),
                ('vncr_redirect', 'not-run', 0), ('vncr_redirect', 'fail', 2),
                ('negative_control_trapped', 'not-run', 0), ('evidence', 'UNIMPLEMENTED', 5),
                ('evidence', 'NONE', 0), ('tool', 'x' * 15, None), ('tool', 'a', None)):
            with self.subTest(key=key, value=value):
                rec = nv2.parse_text(field_text(key, value))
                got = getattr(rec, key if key not in ('vncr_redirect',) else 'vncr_redirect')
                if expect is not None:
                    self.assertEqual(got, expect)
                else:
                    self.assertEqual(rec.tool, value)
        # the check digits may be either case
        upper = '\n'.join(re.sub(r'\*([0-9a-f]{2})$', lambda m: '*' + m.group(1).upper(), line)
                          for line in nv2.format_text(make_rec()).split('\n'))
        self.assertEqual(nv2.parse_text(upper), make_rec())

    def test_json_forms(self):
        rec = make_rec()
        fields = nv2.fields_of(rec)
        as_ints = dict(fields, midr_el1=rec.midr_el1, revidr_el1=0, id_aa64mmfr2_el1=2 ** 40,
                       id_aa64mmfr4_el1=0, build_id=7)
        parsed = nv2.parse_json(json.dumps(as_ints))
        self.assertEqual((parsed.midr_el1, parsed.revidr_el1, parsed.id_aa64mmfr2_el1, parsed.build_id),
                         (rec.midr_el1, 0, 2 ** 40, 7))
        self.assertEqual(json.loads(nv2.format_json(rec)), fields)
        self.assertEqual(nv2.format_json(rec), nv2.format_json(nv2.parse_json(nv2.format_json(rec))))

    def test_json_rejects_every_malformed_form(self):
        fields = nv2.fields_of(make_rec())
        cases = {'not json': 'nonsense', 'empty': '', 'array': '[]', 'null': 'null', 'string': '"x"',
                 'empty object': '{}', 'trailing comma': json.dumps(fields)[:-1] + ',}',
                 'duplicate key': '{"tool": "a", ' + json.dumps(fields)[1:],
                 'duplicate of another': json.dumps(fields)[:-1] + ', "soc": "unknown"}'}
        for key in fields:
            cases[f'missing {key}'] = json.dumps({k: v for k, v in fields.items() if k != key})
        for extra in ('vendor', 'flags', 'reserved0', 'reserved1', 'reserved2', 'crc32', 'magic'):
            cases[f'extra {extra}'] = json.dumps(dict(fields, **{extra: 0}))
        for key, bad in (
                ('schema_version', ['1', 2, 0, 1.0, True, None, '01']),
                ('soc', [1, True, None, 'X1E80100', []]),
                ('midr_el1', [-1, 2 ** 64, 1.5, True, None, '0x', 'feed', '0x' + 'f' * 17, [1]]),
                ('revidr_el1', [-1, 2 ** 64]),
                ('id_aa64mmfr2_el1', [2 ** 64, '0x' + '1' * 17]),
                ('id_aa64mmfr4_el1', [False, 'x']),
                ('vncr_redirect', [1, True, 'PASS', None]),
                ('negative_control_trapped', [0, 'yes']),
                ('evidence', [1, 'observed', None]),
                ('tool', ['', 'x' * 16, 'a b', 5, None]),
                ('build_id', [2 ** 32, -1, '0x123456789', 1.0])):
            for value in bad:
                cases[f'{key}={value!r}'] = json.dumps(dict(fields, **{key: value}))
        for name, text in cases.items():
            with self.subTest(name):
                with self.assertRaises(nv2.RecordError):
                    nv2.parse_json(text)

    def test_binary_loader_rejects_bad_files(self):
        data = nv2.pack(make_rec())
        for blob in (b'', data[:-1], data + b'\0', b'\0' * 96, b'\xff' * 96, data[:-1] + bytes([data[-1] ^ 1])):
            with self.subTest(len(blob)):
                with self.assertRaises(nv2.RecordError):
                    nv2.unpack(blob)
        self.assertNotEqual(nv2.parse_binary(data[:-1])[0], nv2.PARSED)

    def test_every_verdict_has_a_name_and_a_status(self):
        self.assertEqual(len(nv2.DENY_NAMES), 16)
        self.assertEqual(len(set(nv2.DENY_NAMES)), 16)
        for name, number in nv2.DENY.items():
            self.assertEqual(nv2.verdict_name(number), name)
            self.assertLess(nv2.verdict_status(number), 0, name)
        self.assertEqual((nv2.verdict_name(nv2.ALLOW), nv2.verdict_name(nv2.PARSED)), ('ALLOW', 'PARSED'))
        self.assertEqual(nv2.verdict_status(nv2.ALLOW), 0)
        self.assertEqual(nv2.verdict_status(nv2.PARSED), -90)
        self.assertEqual(nv2.verdict_status(12345), -90)
        self.assertTrue(nv2.verdict_name(12345).startswith('UNKNOWN'))
        self.assertNotIn(nv2.ALLOW, range(16))
        self.assertNotIn(nv2.PARSED, range(16))


class Cli(unittest.TestCase):
    def cli(self, *args, stdin=None):
        return subprocess.run([sys.executable, 'tools/nv2_record.py', *map(str, args)], cwd=ROOT,
                              input=stdin, capture_output=True, text=True, timeout=120)

    def test_ingest_and_show(self):
        rec = make_rec()
        with scratch('cli-') as directory:
            d = Path(directory)
            log = d / 'run.log'
            log.write_text('serial noise\n' + nv2.format_text(rec) + 'more noise\n')
            out, header, js = d / 'out/record.bin', d / 'out/record.h', d / 'out/record.json'
            result = self.cli('ingest', log, '--out', out, '--header', header, '--json', js)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(out.read_bytes(), nv2.pack(rec))
            self.assertEqual(header.read_text(), nv2.emit_header(nv2.pack(rec)))
            self.assertEqual(nv2.parse_json(js.read_text()), rec)
            self.assertIn('snap_nv2_record_bytes[96]', header.read_text())
            self.assertIn('SPDX-License-Identifier: MIT', header.read_text())
            # the other input forms give the same binary
            for name, content, fmt in (('r.json', nv2.format_json(rec), 'json'), ('r.bin', None, 'bin')):
                path = d / name
                path.write_bytes(nv2.pack(rec)) if content is None else path.write_text(content)
                again = d / f'again-{name}'
                result = self.cli('ingest', path, '--out', again, '--header', d / 'again.h', '--format', fmt)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(again.read_bytes(), nv2.pack(rec))
            # standard input
            piped = d / 'piped.bin'
            result = self.cli('ingest', '-', '--out', piped, '--header', d / 'piped.h',
                              stdin=nv2.format_text(rec))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(piped.read_bytes(), nv2.pack(rec))

            live = ['--live-midr', hex(LIVE['midr']), '--live-revidr', hex(LIVE['revidr']),
                    '--live-mmfr2', hex(LIVE['mmfr2']), '--live-mmfr4', hex(LIVE['mmfr4'])]
            shown = self.cli('show', out, *live, '--check')
            self.assertEqual(shown.returncode, 0, shown.stdout + shown.stderr)
            self.assertIn('gate: ALLOW (prepare status 0)', shown.stdout)
            self.assertIn('parse: PARSED', shown.stdout)
            self.assertEqual(self.cli('show', out).returncode, 0)
            self.assertNotIn('gate:', self.cli('show', out).stdout)
            wrong = self.cli('show', out, '--live-midr', hex(LIVE['midr'] ^ 1), *live[2:], '--check')
            self.assertEqual(wrong.returncode, 3)
            self.assertIn('gate: DENY_MIDR (prepare status -91)', wrong.stdout)
            self.assertEqual(self.cli('show', out, *live, '--soc', 'x2e-96-100', '--check').returncode, 3)
            self.assertEqual(self.cli('show', out, '--live-midr', '0x1').returncode, 1)
            self.assertEqual(self.cli('show', out, *live[:2]).returncode, 1)
            self.assertEqual(self.cli('show', out, *live, '--soc', 'unknown').returncode, 2)
            denied = self.cli('show', out, *live[:6], '--live-mmfr4', '0x100000')
            self.assertIn('DENY_NOT_ADVERTISED', denied.stdout)

    def test_ingest_warns_when_no_live_register_could_help(self):
        with scratch('cli-warn-') as directory:
            d = Path(directory)
            for change, reason in ((dict(vncr_redirect=2), 'DENY_VNCR_FAIL'),
                                   (dict(vncr_redirect=0), 'DENY_VNCR_NOT_RUN'),
                                   (dict(negative_control_trapped=0), 'DENY_NEGCTL_NOT_RUN'),
                                   (dict(evidence=3), 'DENY_EVIDENCE'),
                                   (dict(id_aa64mmfr2_el1=0), 'DENY_NOT_ADVERTISED')):
                log = d / 'run.log'
                log.write_text(nv2.format_text(make_rec(**change)))
                result = self.cli('ingest', log, '--out', d / 'o.bin', '--header', d / 'o.h')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f'whatever the live registers read: {reason}', result.stdout)
            log.write_text(nv2.format_text(make_rec()))
            ok = self.cli('ingest', log, '--out', d / 'o.bin', '--header', d / 'o.h')
            self.assertNotIn('whatever the live registers read', ok.stdout)

    def test_errors_are_reported_and_nothing_is_written(self):
        with scratch('cli-err-') as directory:
            d = Path(directory)
            bad = d / 'bad.log'
            text = nv2.format_text(make_rec()).replace('*', '*0', 1)
            bad.write_text('line one\n' + text)
            out, header = d / 'x/bad.bin', d / 'x/bad.h'
            result = self.cli('ingest', bad, '--out', out, '--header', header)
            self.assertEqual(result.returncode, 1)
            self.assertIn('error: line 2:', result.stderr)
            self.assertFalse(out.exists() or header.exists())
            result = self.cli('ingest', d / 'absent.log', '--out', out, '--header', header)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(self.cli('ingest').returncode, 2)
            self.assertEqual(self.cli('bogus').returncode, 2)
            self.assertEqual(self.cli().returncode, 2)
            two = d / 'two.log'
            two.write_text(nv2.format_text(make_rec(build_id=1)) + nv2.format_text(make_rec(build_id=2)))
            self.assertEqual(self.cli('ingest', two, '--out', out, '--header', header).returncode, 1)
            self.assertEqual(self.cli('ingest', two, '--out', out, '--header', header, '--last').returncode, 0)
            self.assertEqual(nv2.unpack(out.read_bytes()).build_id, 2)


class Gate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = scratch('gate-')
        cls.dir = Path(cls.tmp.name)
        cls.exe = cls.dir / 'test-nv2-gate'
        compile_test(cls.exe)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_c_self_test_passes_under_sanitizers(self):
        result = run([str(self.exe)])
        self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-3000:])
        self.assertTrue(result.stdout.rstrip().endswith('PASS'), result.stdout[-2000:])

    def test_c_gate_agrees_with_the_python_decision(self):
        cases = corpus()

        def line(case):
            data, soc, midr, revidr, mmfr2, mmfr4 = case
            return f'{data.hex() if data else "."} {soc:x} {midr:x} {revidr:x} {mmfr2:x} {mmfr4:x}'

        got = c_batch(self.exe, 'decide', [line(c) for c in cases])
        want = [verdict_text(nv2.decide(*case)) for case in cases]
        self.assertEqual(len(got), len(cases))
        differing = [(i, got[i], want[i]) for i in range(len(cases)) if got[i] != want[i]]
        self.assertFalse(differing, f'{len(differing)} of {len(cases)} differ, first {differing[:3]}')
        self.assertEqual(set(want), {'A'} | {str(i) for i in range(16)},
                         'the corpus must reach every verdict, and no other')
        print(f'differential: {len(cases)} cases, {want.count("A")} allowed', file=sys.stderr)

    def test_c_view_agrees_with_the_python_parse(self):
        records = [case[0] for case in corpus()]

        def expected(data):
            verdict, rec = nv2.parse_binary(data)
            if verdict != nv2.PARSED:
                return verdict_text(verdict)
            return (f'P {rec.soc:x} {rec.build_id:x} {rec.midr_el1:x} {rec.revidr_el1:x} '
                    f'{rec.id_aa64mmfr2_el1:x} {rec.id_aa64mmfr4_el1:x} {rec.vncr_redirect:x} '
                    f'{rec.negative_control_trapped:x} {rec.evidence:x}')

        got = c_batch(self.exe, 'parse', [d.hex() if d else '.' for d in records])
        self.assertEqual(got, [expected(d) for d in records])
        self.assertGreater(sum(1 for g in got if g.startswith('P ')), 100)

    def test_c_status_agrees_with_the_python_status(self):
        values = list(range(0, 40)) + [nv2.PARSED, nv2.ALLOW, 0xFFFFFFFF, nv2.ALLOW + 1,
                                       nv2.ALLOW - 1, nv2.PARSED + 1, 0x80000000]
        got = c_batch(self.exe, 'status', [f'{v:x}' for v in values])
        self.assertEqual(got, [str(nv2.verdict_status(v)) for v in values])

    def test_documented_example_through_both_gates(self):
        text, data = Format().doc_example()
        live = (0xFEED0001, 0, 0x02001234, 0)
        self.assertEqual(nv2.decide(data, 1, *live), nv2.ALLOW)
        self.assertEqual(c_batch(self.exe, 'decide', [f'{data.hex()} 1 {live[0]:x} 0 {live[2]:x} 0']), ['A'])
        for other in (0x512F0021, 0x511F0011, 0x512F0011):
            self.assertEqual(nv2.decide(data, 1, other, 0, live[2], 0), nv2.DENY['DENY_MIDR'])

    def test_header_matches_the_python_tables(self):
        text = GATE_H.read_text()

        def enum(name):
            return re.search(r'enum ' + name + r' \{(.*?)\};', text, re.S).group(1)

        verdicts = enum('snap_nv2_verdict')
        names = re.findall(r'SNAP_NV2_(DENY_[A-Z_]+)', verdicts)
        self.assertEqual(names, list(nv2.DENY_NAMES) + ['DENY_COUNT'])
        self.assertRegex(verdicts, r'SNAP_NV2_DENY_NO_RECORD\s*=\s*0\b')
        self.assertEqual(int(re.search(r'SNAP_NV2_PARSED\s*=\s*(0x[0-9a-f]+)', verdicts).group(1), 16), nv2.PARSED)
        self.assertEqual(int(re.search(r'SNAP_NV2_ALLOW\s*=\s*(0x[0-9a-f]+)', verdicts).group(1), 16), nv2.ALLOW)

        def table(name, prefix, spelling):
            return {spelling(k): int(v) for k, v in re.findall(r'SNAP_NV2_' + prefix + r'([A-Z0-9_]+)\s*=\s*(\d+)',
                                                                  enum(name))}

        dashed = lambda k: k.lower().replace('_', '-')
        self.assertEqual(table('snap_nv2_soc', 'SOC_', dashed), nv2.SOC)
        self.assertEqual(table('snap_nv2_result', '', dashed), nv2.RESULT)
        self.assertEqual(table('snap_nv2_evidence', 'EVIDENCE_', str), nv2.EVIDENCE)
        define = lambda name: re.search(r'#define SNAP_NV2_' + name + r'\s+\(?(?:UINT32_C\()?(-?\w+)', text).group(1)
        self.assertEqual(int(define('RECORD_MAGIC'), 16), nv2.MAGIC)
        self.assertEqual(int(define('RECORD_VERSION')), nv2.VERSION)
        self.assertEqual(int(define('RECORD_SIZE')), nv2.SIZE)
        self.assertEqual(int(define('TOOL_LEN')), nv2.TOOL_LEN)
        self.assertEqual(int(define('RESERVED2_LEN')), nv2.RESERVED2_LEN)
        self.assertEqual((int(define('STATUS_RECORD')), int(define('STATUS_MISMATCH')),
                          int(define('STATUS_NO_NV2')), int(define('STATUS_RESULT'))),
                         (nv2.STATUS_RECORD, nv2.STATUS_MISMATCH, nv2.STATUS_NO_NV2, nv2.STATUS_RESULT))
        self.assertIn('#define SNAP_NV2_PLATFORM_SOC', text)
        self.assertRegex(text, r'#define SNAP_NV2_PLATFORM_SOC\s+SNAP_NV2_SOC_X1E80100')

    def test_the_embedded_record_route(self):
        rec = make_rec()
        data = nv2.pack(rec)
        regs = [hex(LIVE[k]) for k in ('midr', 'revidr', 'mmfr2', 'mmfr4')]
        with scratch('embed-') as directory:
            d = Path(directory)
            header = d / 'record.h'
            header.write_text(nv2.emit_header(data))
            exe = d / 'embedded'
            compile_test(exe, defines=[f'-DSNAP_NV2_RECORD_HEADER="{header}"'])
            result = run([str(exe)])
            self.assertEqual((result.returncode, result.stdout.strip()), (0, 'PASS'), result.stderr[-2000:])
            result = run([str(exe), 'embedded', *regs])
            self.assertEqual(result.stdout.split('\n')[:2], ['record 96', 'A'], result.stderr)
            moved = list(regs)
            moved[0] = hex(LIVE['midr'] ^ 1)
            self.assertEqual(run([str(exe), 'embedded', *moved]).stdout.split('\n')[:2],
                             ['record 96', str(nv2.DENY['DENY_MIDR'])])
            moved = list(regs)
            moved[2] = hex(LIVE['mmfr2'] & ~(0xF << 24))
            self.assertEqual(run([str(exe), 'embedded', *moved]).stdout.split('\n')[:2],
                             ['record 96', str(nv2.DENY['DENY_NOT_ADVERTISED'])])
            # a record that failed its functional test embeds fine and still denies
            failed = d / 'failed.h'
            failed.write_text(nv2.emit_header(nv2.pack(make_rec(vncr_redirect=2))))
            exe2 = d / 'failed'
            compile_test(exe2, defines=[f'-DSNAP_NV2_RECORD_HEADER="{failed}"'])
            self.assertEqual(run([str(exe2), 'embedded', *regs]).stdout.split('\n')[:2],
                             ['record 96', str(nv2.DENY['DENY_VNCR_FAIL'])])
            # no define: no record, whatever the registers read
            result = run([str(self.exe), 'embedded', *regs])
            self.assertEqual(result.stdout.split('\n')[:2], ['none 0', '0'], result.stderr)
            result = run([str(self.exe), 'embedded', '0', '0', '0', '0'])
            self.assertEqual(result.stdout.split('\n')[:2], ['none 0', '0'])

    def test_weakened_gates_fail_the_c_test(self):
        source = GATE_C.read_text()
        for name, (old, new) in MUTANTS.items():
            with self.subTest(name), scratch('mutant-') as directory:
                self.assertEqual(source.count(old), 1, f'{name}: anchor not unique')
                mutant = Path(directory) / 'nv2-gate.c'
                mutant.write_text(source.replace(old, new))
                exe = Path(directory) / 'test'
                compile_test(exe, gate=mutant, sanitize=False)
                result = run([str(exe)])
                self.assertNotEqual(result.returncode, 0, f'{name}: the C test still passes')
                self.assertFalse(result.stdout.rstrip().endswith('PASS'), name)
                first = (result.stderr.strip().split('\n') or [''])[0]
                print(f'mutant {name!r}: killed by {first}', file=sys.stderr)


class Wiring(unittest.TestCase):
    @staticmethod
    def literal(name):
        import ast
        for node in ast.parse(RUN_PY.read_text()).body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name
                                                    for t in node.targets):
                return ast.literal_eval(node.value)
        raise AssertionError(f'{name} missing from probes/run.py')

    def test_run_py_links_the_gate_only_with_the_define(self):
        optional = self.literal('OPTIONAL')['xnuboot']
        self.assertIn(('-DSNAP_PLATFORM_SP11=1', 'compat/nv2-gate.c'), optional)
        always = self.literal('MULTI')['xnuboot']
        self.assertNotIn('compat/nv2-gate.c', always)
        self.assertEqual([define for define, rel in optional if rel == 'compat/nv2-gate.c'],
                         ['-DSNAP_PLATFORM_SP11=1'])

    def test_every_gate_status_has_a_prepare_failure_sentence(self):
        failures = self.literal('PREPARE_FAILURES')
        texts = list(failures.values())
        self.assertEqual(len(set(texts)), len(texts))
        for status in {nv2.verdict_status(v) for v in list(range(16)) + [nv2.PARSED]}:
            self.assertIn(status, failures)
            self.assertGreater(len(failures[status]), 10)
        self.assertEqual({nv2.verdict_status(v) for v in range(16)}, {-90, -91, -92, -93})

    def test_prepare_hook_is_where_it_must_be(self):
        check_prepare(PREPARE_C.read_text())

    def test_weakened_hooks_fail_the_source_check(self):
        text = PREPARE_C.read_text()
        for name, (mutate, reason) in HOOK_MUTANTS.items():
            with self.subTest(name):
                mutant = mutate(text)
                self.assertNotEqual(mutant, text)
                with self.assertRaises(AssertionError) as caught:
                    check_prepare(mutant)
                self.assertIn(reason, str(caught.exception))
                print(f'hook mutant {name!r}: {str(caught.exception)[:100]}', file=sys.stderr)

    def test_unity_translation_unit_has_one_nv_site(self):
        unity = read_source(ROOT / 'probes/xnuboot.c')
        self.assertEqual(unity.count('msr s3_4_c2_c2_0'), 1)
        self.assertEqual(len(re.findall(r'1UL ?<< ?42\) ?\| ?\(1UL ?<< ?45', unity)), 1)

    def test_a16_translation_unit_does_not_mention_the_gate(self):
        clang = shutil.which('clang') or '/opt/homebrew/opt/llvm/bin/clang'
        uefi = ROOT.parent / 'q1n1/platform/uefi'
        if not Path(clang).exists() or not uefi.is_dir():
            self.skipTest('clang or the shared q1n1 checkout is absent')
        flags = json.loads((ROOT / 'config/monitor-flags.json').read_text())['flags']
        keep = [f for f in flags if f.startswith('-D')] + ['--target=aarch64-unknown-none-elf', '-ffreestanding']

        def preprocess(*extra):
            result = run([clang, *keep, *extra, f'-I{ROOT / "shadow"}', f'-I{uefi}', '-E', '-P', 'probes/xnuboot.c'])
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            return result.stdout

        pattern = re.compile(r'snap_nv2_|nv2-gate|sp11_launch_gate|SP11_EVENT')
        self.assertIsNone(pattern.search(preprocess()))
        self.assertIsNone(pattern.search(preprocess('-DSNAP_PLATFORM_SP11=0')))
        sp11 = preprocess('-DSNAP_PLATFORM_SP11=1')
        for token in ('snap_nv2_gate_decide_embedded', 'sp11_launch_gate', '0x4e56', '0x5041'):
            self.assertIn(token, sp11)


if __name__ == '__main__':
    unittest.main(verbosity=2)
