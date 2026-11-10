#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""SP11 PAuth ID-register normalization (C4): the normalizer, and where it sits in id_forge().

    python3 tools/test-oryon-idregs.py

Offline: no hardware. Every register value in it is made up. The recorded MIDRs and the
APA = 5 / GPA = 1 shape are numbers quoted from docs/SURFACE-PRO-11.md (E26, OBSERVED on
another unit) and docs/FEASIBILITY.md (the A16, VERIFIED); this test shows what the monitor
does with them and says nothing about whether they are true of any unit.

Normalizer. tools/test-oryon-idregs.c (address and undefined behaviour sanitizers) checks
compat/oryon-idregs.c against its own reference model: the golden vectors, one case per
refusal, every combination of the four PAuth nibbles that matter against API/GPI 0, 1 and 15
and twelve MIDRs (about 21 million cases), the view over every CRm/op2, and the status
mapping. Here the same C code is also run through its batch modes against a second,
independent model written in Python, on a corpus that reaches every verdict.

id_forge(). tools/test-oryon-forge.c is built from the real probes/idregs.h with only
id_register(), a table of MRS instructions that cannot run on a host, replaced by a stub. The
monitor's own id_forge() is then called for every CRm/op2 pair against a made-up register file
and compared with the model: the SVE and SME rules that were already there, plus the SP11
hook, built with -DSNAP_PLATFORM_SP11=1, =0 and without it. The A16 builds must read no ID
register and change nothing; the SP11 build must read exactly MIDR, ISAR1 and ISAR2.

Wiring: the hook and every mention of the unit stay inside #if SNAP_PLATFORM_SP11 in
probes/idregs.h and probes/xnuboot/prepare.c; probes/run.py links the unit only with the
define and describes both statuses; the monitor's translation unit preprocessed without the
define contains none of it (the byte-level proof is the A16 firewall,
tools/monitor_baseline.py).

Weakened copies of compat/oryon-idregs.c (MUTANTS) and of probes/idregs.h (HOOK_MUTANTS) must
FAIL; the first failure each causes is printed. Scratch files live under
build/oryon-idregs-test/ and are removed.
"""
from collections import Counter
from pathlib import Path
import ast
import itertools
import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from source_text import read_source  # noqa: E402

BUILD = ROOT / 'build' / 'oryon-idregs-test'
UNIT_C = ROOT / 'compat/oryon-idregs.c'
UNIT_H = ROOT / 'compat/oryon-idregs.h'
IDREGS_H = ROOT / 'probes/idregs.h'
PREPARE_C = ROOT / 'probes/xnuboot/prepare.c'
RUN_PY = ROOT / 'probes/run.py'

MASK64 = (1 << 64) - 1
NORMALIZED, PASSTHROUGH = 0x4F524E4D, 0x4F525054
REFUSALS = ('MIDR', 'IMPDEF', 'FAMILY', 'NO_PAUTH', 'ADDRESS', 'GENERIC')
RECORDED = {0x511F0011: 'oryon', 0x512F0011: 'oryon', 0x512F0021: 'a16'}
MIDRS = [0x511F0011, 0x512F0011, 0x512F0021,
         0, 0x511F0012, 0x512F0022, 0xFEED0001, 0x1511F0011, 0x1512F0021, MASK64, 0xFFFFFFFF]


def scratch(prefix):
    BUILD.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(prefix=prefix, dir=BUILD)


def run(command, **kwargs):
    return subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                          timeout=kwargs.pop('timeout', 300), **kwargs)


def compile_unit(exe, unit=UNIT_C, sanitize=True):
    if sanitize:
        flags = ['-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                 '-fsanitize=address,undefined', '-fno-sanitize-recover=all']
    else:
        # A weakened copy may leave a helper unused; it must still compile.
        flags = ['-std=gnu11', '-O1', '-w']
    result = run(['cc', *flags, '-Icompat', 'tools/test-oryon-idregs.c', str(unit), '-o', str(exe)])
    if result.returncode:
        raise AssertionError('compile failed:\n' + result.stderr[-3000:])


def batch(exe, mode, lines):
    result = subprocess.run([str(exe), mode], input='\n'.join(lines) + '\n', capture_output=True,
                            text=True, timeout=300)
    if result.returncode:
        raise AssertionError(f'{mode}: exit {result.returncode}\n{result.stderr[-2000:]}')
    return result.stdout.split('\n')[:-1]


# ---- the independent model -------------------------------------------------------------------

def nibble(value, shift):
    return value // (1 << shift) % 16


def model(midr, isar1, isar2):
    """(verdict, out1, out2). Written from the policy in compat/oryon-idregs.h with integer
    division instead of shifts and masks, and with outputs computed by arithmetic."""
    shape = RECORDED.get(midr)
    apa, api, gpa, gpi = nibble(isar1, 4), nibble(isar1, 8), nibble(isar1, 24), nibble(isar1, 28)
    gpa3, apa3 = nibble(isar2, 8), nibble(isar2, 12)
    if shape is None:
        return 0, 0, 0
    if api or gpi:
        return 1, 0, 0
    if (apa3 or gpa3) if shape == 'oryon' else (apa or gpa):
        return 2, 0, 0
    address, generic = (apa, gpa) if shape == 'oryon' else (apa3, gpa3)
    if not address and not generic:
        return 3, 0, 0
    if address != 5:
        return 4, 0, 0
    if generic != 1:
        return 5, 0, 0
    if shape == 'a16':
        return PASSTHROUGH, isar1, isar2
    return NORMALIZED, isar1 - 5 * 16 - 1 * (1 << 24), isar2 + 5 * 4096 + 1 * 256


def model_view(crm, op2, raw, midr, isar1, isar2):
    if crm > 7 or op2 > 7:
        return 0
    if (crm, op2) not in ((6, 1), (6, 2)):
        return raw
    _, out1, out2 = model(midr, isar1, isar2)
    return out1 if op2 == 1 else out2


def model_forge(crm, op2, value, midr, isar1, isar2, sp11):
    if (crm, op2) == (4, 0):
        value &= ~(0xF << 32) & MASK64
    if (crm, op2) == (4, 1):
        value &= ~(0xF << 24) & MASK64
    if (crm, op2) in ((4, 4), (4, 5)):
        value = 0
    return model_view(crm, op2, value, midr, isar1, isar2) if sp11 else value


def model_status(verdict):
    if verdict in (NORMALIZED, PASSTHROUGH):
        return 0
    return -94 if verdict == 0 else -95


def verdict_text(verdict):
    return {NORMALIZED: 'N', PASSTHROUGH: 'P'}.get(verdict, str(verdict))


def compose(rng, apa, api, gpa, gpi, gpa3, apa3):
    isar1 = rng.getrandbits(64) & ~((0xF << 4) | (0xF << 8) | (0xF << 24) | (0xF << 28))
    isar1 |= apa << 4 | api << 8 | gpa << 24 | gpi << 28
    isar2 = rng.getrandbits(64) & ~((0xF << 8) | (0xF << 12))
    isar2 |= gpa3 << 8 | apa3 << 12
    return isar1, isar2


def pauth_corpus():
    """(midr, isar1, isar2): the PAuth nibbles over the levels that matter, with random other bits,
    plus fully random registers; every verdict is reached many times."""
    rng = random.Random(0x4F52594E)
    cases = []
    for apa, gpa, apa3, gpa3 in itertools.product((0, 1, 2, 4, 5, 6, 15), repeat=4):
        for api, gpi in ((0, 0), (1, 0), (0, 1), (15, 15)):
            isar1, isar2 = compose(rng, apa, api, gpa, gpi, gpa3, apa3)
            for midr in MIDRS[:5]:
                cases.append((midr, isar1, isar2))
    # "No PAuth" and the successes are single level combinations, so give every address/generic
    # pair of each recorded shape several patterns of unrelated register bits as well.
    for midr, oryon in ((0x511F0011, True), (0x512F0011, True), (0x512F0021, False)):
        for address, generic in itertools.product((0, 1, 2, 4, 5, 6, 15), repeat=2):
            for _ in range(24):
                if oryon:
                    isar1, isar2 = compose(rng, address, 0, generic, 0, 0, 0)
                else:
                    isar1, isar2 = compose(rng, 0, 0, 0, 0, generic, address)
                cases.append((midr, isar1, isar2))
    for _ in range(20000):
        cases.append((rng.choice(MIDRS) if rng.random() < 0.8 else rng.getrandbits(32),
                      rng.getrandbits(64), rng.getrandbits(64)))
    return cases


def hexline(*values):
    return ' '.join(f'{value:x}' for value in values)


# ---- source structure ------------------------------------------------------------------------

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


def drop_text(text, old, new=''):
    need(text.count(old) == 1, f'mutation anchor not unique: {old!r}')
    return text.replace(old, new)


HOOK_CALL = '#if SNAP_PLATFORM_SP11\n    value = id_forge_sp11(crm, op2, value);\n#endif\n'


def check_idregs(text):
    """Everything probes/idregs.h must satisfy for the SP11 hook; raises AssertionError."""
    inside = guarded_lines(text)
    for number, line in enumerate(text.split('\n')):
        if re.search(r'snap_oryon_|oryon-idregs|id_forge_sp11', line):
            need(inside[number], f'line {number + 1} is outside #if SNAP_PLATFORM_SP11: {line.strip()}')
    need(text.count('#include "../compat/oryon-idregs.h"') == 1, 'the unit header is not included once')

    forge = function('id_forge', text)
    need(forge.count('id_forge_sp11(crm, op2, value)') == 1, 'id_forge must call the SP11 hook exactly once')
    need(HOOK_CALL in forge, 'the hook call must be an assignment to value inside #if SNAP_PLATFORM_SP11')
    need(forge.index('id_forge_sp11(crm, op2, value)') < forge.index('return value;'),
         'the hook must run before id_forge returns')

    hook = function('id_forge_sp11', text)
    need('mrs' not in hook and 'asm' not in hook,
         'the hook must read through id_register(), the table of allocated encodings')
    need(sorted(re.findall(r'id_register\((\d+), (\d+)\)', hook)) == [('0', '0'), ('6', '1'), ('6', '2')],
         'the hook must read exactly MIDR, ISAR1 and ISAR2')
    need(hook.count('snap_oryon_id_view(') == 1 and 'return snap_oryon_id_view(crm, op2, value,' in hook,
         'the hook must return the unit\'s view of the register')


def check_prepare(text):
    """What probes/xnuboot/prepare.c must do with the unit; the gate's own placement is pinned by
    tools/test-nv2-gate.py."""
    inside = guarded_lines(text)
    for number, line in enumerate(text.split('\n')):
        if re.search(r'snap_oryon_|oryon-idregs', line):
            need(inside[number], f'line {number + 1} is outside #if SNAP_PLATFORM_SP11: {line.strip()}')
    gate = function('sp11_launch_gate', text)
    need(gate.count('snap_oryon_normalize_pauth(midr, isar1, isar2, 0, 0)') == 1,
         'the gate must decide from this CPU\'s own MIDR, ISAR1 and ISAR2')
    need('snap_oryon_verdict_status(pauth)' in gate, 'a PAuth refusal must return its own status')
    need(re.search(r'pauth == SNAP_ORYON_NORMALIZED \|\| pauth == SNAP_ORYON_PASSTHROUGH', gate),
         'only the two success verdicts may launch')


# ---- the unit ---------------------------------------------------------------------------------

# name: pairs of (text to replace, replacement), every `old` unique in compat/oryon-idregs.c. Each
# weakened copy must fail tools/test-oryon-idregs.c.
MUTANTS = {
    'API and GPI no longer refused': ((
        '    if (field(isar1, ISAR1_API_SHIFT) || field(isar1, ISAR1_GPI_SHIFT))\n'
        '        return SNAP_ORYON_REFUSE_IMPDEF;\n', ''),),
    'lower address levels accepted': ((
        'if (address != ADDRESS_FPACCOMBINE)', 'if (address > ADDRESS_FPACCOMBINE)'),),
    'any generic level accepted': (('if (generic != GENERIC_PACGA)', 'if (generic == 0)'),),
    'no-PAuth case lost': (('if (!address && !generic)', 'if (0)'),),
    'MIDR high bits ignored': (
        ('    if (midr >> 32)\n        return SHAPE_UNKNOWN;\n', ''),
        ('if (known_midrs[i].midr == midr)', 'if (known_midrs[i].midr == (uint32_t)midr)')),
    'MIDR revision ignored': ((
        'if (known_midrs[i].midr == midr)', 'if ((known_midrs[i].midr >> 4) == (midr >> 4))'),),
    'APA3 and GPA3 not checked on the Oryon shape': ((
        '        if (field(isar2, ISAR2_APA3_SHIFT) || field(isar2, ISAR2_GPA3_SHIFT))\n'
        '            return SNAP_ORYON_REFUSE_FAMILY;\n', ''),),
    'APA and GPA not checked on the A16 shape': ((
        '        if (field(isar1, ISAR1_APA_SHIFT) || field(isar1, ISAR1_GPA_SHIFT))\n'
        '            return SNAP_ORYON_REFUSE_FAMILY;\n', ''),),
    'A16 row dropped': (('    { SNAP_ORYON_ID_MIDR_A16_J700, SHAPE_ISAR2 },\n', ''),),
    'A16 row has the Oryon shape': ((
        '{ SNAP_ORYON_ID_MIDR_A16_J700, SHAPE_ISAR2 }', '{ SNAP_ORYON_ID_MIDR_A16_J700, SHAPE_ISAR1 }'),),
    'second Oryon variant dropped': (('    { SNAP_ORYON_ID_MIDR_V1_VARIANT2, SHAPE_ISAR1 },\n', ''),),
    'pass-through reported as normalized': ((
        'return shape == SHAPE_ISAR1 ? SNAP_ORYON_NORMALIZED : SNAP_ORYON_PASSTHROUGH;',
        'return SNAP_ORYON_NORMALIZED;'),),
    'ISAR1 copied without clearing APA and GPA': ((
        'out1 = isar1 & ~((FIELD_MASK << ISAR1_APA_SHIFT) | (FIELD_MASK << ISAR1_GPA_SHIFT));',
        'out1 = isar1;'),),
    'GPA3 written at the APA3 position': ((
        '((uint64_t)GENERIC_PACGA << ISAR2_GPA3_SHIFT)', '((uint64_t)GENERIC_PACGA << ISAR2_APA3_SHIFT)'),),
    'refusal leaves the raw registers in the outputs': ((
        '    } else if (verdict == SNAP_ORYON_PASSTHROUGH) {', '    } else {'),),
    'view rewrites every CRm 6 register': ((
        'if (crm != 6 || (op2 != 1 && op2 != 2))', 'if (crm != 6)'),),
    'out-of-range selector no longer zero': ((
        '    if (crm > 7 || op2 > 7)\n        return 0;\n', ''),),
    'MIDR refusal reports the PAuth status': ((
        '        return SNAP_ORYON_STATUS_MIDR;', '        return SNAP_ORYON_STATUS_PAUTH;'),),
    'unknown verdict reads as success': ((
        '    if (verdict == SNAP_ORYON_NORMALIZED || verdict == SNAP_ORYON_PASSTHROUGH)\n        return 0;\n',
        '    if (verdict >= SNAP_ORYON_REFUSE_COUNT)\n        return 0;\n'),),
    'refusal other than MIDR reports success': ((
        '    return SNAP_ORYON_STATUS_PAUTH;\n}', '    return 0;\n}'),),
    'address level 4 recorded': ((
        '#define ADDRESS_FPACCOMBINE 5', '#define ADDRESS_FPACCOMBINE 4'),),
    'APA3 field at the wrong shift': ((
        '#define ISAR2_APA3_SHIFT  12', '#define ISAR2_APA3_SHIFT  16'),),
    'field mask of three bits': ((
        '#define FIELD_MASK        UINT64_C(0xf)', '#define FIELD_MASK        UINT64_C(0x7)'),),
}


class Unit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = scratch('unit-')
        cls.exe = Path(cls.tmp.name) / 'test-oryon-idregs'
        compile_unit(cls.exe)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_self_test_under_sanitizers(self):
        result = subprocess.run([str(self.exe)], capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertTrue(result.stdout.rstrip().endswith('PASS'), result.stdout)

    def test_python_model_agrees_with_the_c_unit(self):
        cases = pauth_corpus()
        got = batch(self.exe, 'normalize', [hexline(*case) for case in cases])
        self.assertEqual(len(got), len(cases))
        seen = Counter()
        for case, line in zip(cases, got):
            verdict, out1, out2 = model(*case)
            seen[verdict] += 1
            self.assertEqual(line, f'{verdict_text(verdict)} {out1:x} {out2:x}', hexline(*case))
        for verdict in [*range(len(REFUSALS)), NORMALIZED, PASSTHROUGH]:
            self.assertGreater(seen[verdict], 20, f'the corpus barely reaches verdict {verdict}')

    def test_python_model_agrees_on_the_view(self):
        rng = random.Random(0x56494557)
        units = [(0x511F0011, 0xFEDCBA980100F050, 0x0ABC00F1), (0x512F0021, 0x7654321000F0F00F, 0x0ABC51F1)]
        units += rng.sample(pauth_corpus(), 40)
        cases = []
        for midr, isar1, isar2 in units:
            for crm, op2 in itertools.product((0, 1, 5, 6, 7, 8, 15, 0x10, 0xFFFFFFFF), repeat=2):
                cases.append((crm, op2, rng.getrandbits(64), midr, isar1, isar2))
        got = batch(self.exe, 'view', [hexline(*case) for case in cases])
        self.assertEqual([f'{model_view(*case):x}' for case in cases], got)

    def test_python_model_agrees_on_the_status(self):
        values = [*range(300), NORMALIZED - 1, NORMALIZED, NORMALIZED + 1, PASSTHROUGH - 1,
                  PASSTHROUGH, PASSTHROUGH + 1, 0x7FFFFFFF, 0xFFFFFFFF]
        got = batch(self.exe, 'status', [f'{value:x}' for value in values])
        self.assertEqual([str(model_status(value)) for value in values], got)

    def test_header_matches_the_tables_here(self):
        text = UNIT_H.read_text()

        def define(name):
            match = re.search(r'#define ' + name + r'\s+\(?(-?\w+\(0x[0-9a-f]+\)|-?\d+)\)?', text)
            self.assertIsNotNone(match, name)
            return match.group(1)

        self.assertEqual(define('SNAP_ORYON_STATUS_MIDR'), '-94')
        self.assertEqual(define('SNAP_ORYON_STATUS_PAUTH'), '-95')
        names = {'SNAP_ORYON_ID_MIDR_V1_VARIANT1': 0x511F0011, 'SNAP_ORYON_ID_MIDR_V1_VARIANT2': 0x512F0011,
                 'SNAP_ORYON_ID_MIDR_A16_J700': 0x512F0021}
        for name, value in names.items():
            self.assertEqual(define(name), f'UINT32_C(0x{value:08x})')
        self.assertEqual(set(names.values()), set(RECORDED))
        # Evidence labels may only move with a measurement: the Oryon rows are another unit's
        # hypervisor-filtered view, the A16 row is a read.
        for name in ('SNAP_ORYON_ID_MIDR_V1_VARIANT1', 'SNAP_ORYON_ID_MIDR_V1_VARIANT2'):
            self.assertRegex(text, re.escape(name) + r'.*/\* OBSERVED, E26 \*/')
        self.assertRegex(text, r'SNAP_ORYON_ID_MIDR_A16_J700.*/\* VERIFIED, FEASIBILITY\.md \*/')
        enum = re.search(r'enum snap_oryon_verdict \{(.*?)\};', text, re.S).group(1)
        order = re.findall(r'^\s*SNAP_ORYON_(\w+?)\s*(?:=\s*(\w+))?,', enum, re.M)
        self.assertEqual([name for name, _ in order],
                         [f'REFUSE_{name}' for name in REFUSALS] + ['REFUSE_COUNT', 'NORMALIZED', 'PASSTHROUGH'])
        self.assertEqual([value for _, value in order],
                         ['0'] + [''] * 6 + [f'0x{NORMALIZED:x}', f'0x{PASSTHROUGH:x}'])

    def test_weakened_copies_fail(self):
        original = UNIT_C.read_text()
        with scratch('mutants-') as tmp:
            for name, pairs in MUTANTS.items():
                with self.subTest(name):
                    text = original
                    for old, new in pairs:
                        text = drop_text(text, old, new)
                    self.assertNotEqual(text, original)
                    source = Path(tmp) / 'oryon-idregs.c'
                    source.write_text(text)
                    exe = Path(tmp) / 'mutant'
                    compile_unit(exe, source, sanitize=False)
                    result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=300)
                    self.assertNotEqual(result.returncode, 0, f'{name}: the C test still passes')
                    self.assertFalse(result.stdout.rstrip().endswith('PASS'), name)
                    first = (result.stderr.strip().split('\n') or [''])[0]
                    print(f'mutant {name!r}: killed by {first}', file=sys.stderr)


# ---- id_forge() with the hook, from the real header --------------------------------------------

REGISTER_TABLE = re.compile(r'static uint64_t id_register\(uint32_t crm, uint32_t op2\)\n\{\n.*?\n\}\n', re.S)
STUB = ('static uint64_t id_register(uint32_t crm, uint32_t op2)\n{\n'
        '    stub_reads |= UINT64_C(1) << ((crm & 7) * 8 + (op2 & 7));\n'
        '    return stub_register[crm & 7][op2 & 7];\n}\n')
EXPECTED_SP11_READS = (1 << 0) | (1 << (6 * 8 + 1)) | (1 << (6 * 8 + 2))


def build_forge(tmp, header_text, sp11):
    """Compile tools/test-oryon-forge.c against header_text; sp11 is 1, 0 or None (macro undefined)."""
    need(REGISTER_TABLE.search(header_text) is not None, 'id_register() not found in the header')
    text = REGISTER_TABLE.sub(lambda _: STUB, header_text, count=1)
    text = text.replace('#include "../compat/oryon-idregs.h"', '#include "oryon-idregs.h"')
    Path(tmp, 'idregs-under-test.h').write_text(text)
    exe = Path(tmp) / f'forge-{sp11}'
    command = ['cc', '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror', f'-I{tmp}', '-Icompat']
    sources = ['tools/test-oryon-forge.c']
    if sp11 is not None:
        command.append(f'-DSNAP_PLATFORM_SP11={sp11}')
    if sp11 == 1:
        sources.append('compat/oryon-idregs.c')
    result = run([*command, *sources, '-o', str(exe)])
    if result.returncode:
        raise AssertionError('compile failed:\n' + result.stderr[-3000:])
    return exe


def forge_corpus():
    rng = random.Random(0x464F5247)
    units = [(0x511F0011, 0xFEDCBA980100F050, 0x0ABC00F1), (0x512F0011, 0xFEDCBA980100F050, 0x0ABC00F1),
             (0x512F0021, 0x7654321000F0F00F, 0x0ABC51F1), (0x511F0011, 0x01000150, 0),
             (0xFEED0001, 0x01000050, 0), (0, 0, 0), (MASK64, MASK64, MASK64)]
    units += rng.sample(pauth_corpus(), 50)
    return [(crm, op2, value, *unit)
            for unit in units
            for crm, op2 in itertools.product(range(8), repeat=2)
            for value in (0, MASK64, rng.getrandbits(64))]


def forge_failures(tmp, header_text, variants):
    """Mismatches between the header's own id_forge() and the model, one string each."""
    cases = forge_corpus()
    lines = [hexline(*case) for case in cases]
    problems = []
    for sp11 in variants:
        exe = build_forge(tmp, header_text, sp11)
        got = forge_batch(exe, lines)
        for case, line in zip(cases, got):
            want = f'{model_forge(*case, sp11=bool(sp11)):x} {EXPECTED_SP11_READS if sp11 else 0:x}'
            if line != want:
                problems.append(f'SNAP_PLATFORM_SP11={sp11}: {hexline(*case)}: got {line}, expected {want}')
                break
    return problems


def forge_batch(exe, lines):
    result = subprocess.run([str(exe)], input='\n'.join(lines) + '\n', capture_output=True, text=True,
                            timeout=300)
    if result.returncode:
        raise AssertionError(f'forge harness: exit {result.returncode}\n{result.stderr[-2000:]}')
    return result.stdout.split('\n')[:-1]


# name: (how to weaken probes/idregs.h, fragment of the source check it must trip or None,
#        whether the id_forge() harness must also notice).
HOOK_MUTANTS = {
    'hook call removed': (
        lambda t: drop_text(t, HOOK_CALL), 'exactly once', True),
    'hook call not guarded': (
        lambda t: drop_text(t, HOOK_CALL, HOOK_CALL.replace('#if SNAP_PLATFORM_SP11', '#if 1')),
        'outside #if SNAP_PLATFORM_SP11', False),
    'hook result discarded': (
        lambda t: drop_text(t, '    value = id_forge_sp11(crm, op2, value);',
                            '    (void)id_forge_sp11(crm, op2, value);'),
        'an assignment to value', True),
    'hook runs after the return': (
        lambda t: drop_text(drop_text(t, HOOK_CALL), '    return value;\n}\n\n#endif', '    return value;\n' +
                            HOOK_CALL + '}\n\n#endif'),
        'before id_forge returns', True),
    'ISAR1 and ISAR2 swapped': (
        lambda t: drop_text(t, 'id_register(6, 1), id_register(6, 2));', 'id_register(6, 2), id_register(6, 1));'),
        None, True),
    'MIDR read from MPIDR': (
        lambda t: drop_text(t, 'return snap_oryon_id_view(crm, op2, value, id_register(0, 0),',
                            'return snap_oryon_id_view(crm, op2, value, id_register(0, 5),'),
        'exactly MIDR, ISAR1 and ISAR2', True),
    'decision from the value it was handed': (
        lambda t: drop_text(t, 'id_register(6, 1), id_register(6, 2));', 'value, value);'),
        'exactly MIDR, ISAR1 and ISAR2', True),
    'existing SVE rule removed': (
        lambda t: drop_text(t, '    if (crm == 4 && op2 == 0) value &= ~ID_AA64PFR0_EL1_SVE;   /* ID_AA64PFR0 */\n'),
        None, True),
}


class Hook(unittest.TestCase):
    def test_real_id_forge_matches_the_model_in_every_build(self):
        with scratch('forge-') as tmp:
            problems = forge_failures(tmp, IDREGS_H.read_text(), (1, 0, None))
        self.assertEqual(problems, [])

    def test_the_sp11_hook_changes_the_two_registers_and_only_those(self):
        # The golden vector through the real id_forge(), in the SP11 build and the A16 build.
        with scratch('golden-') as tmp:
            text = IDREGS_H.read_text()
            lines = [hexline(6, 1, 0x0100F050, 0x511F0011, 0xFEDCBA980100F050, 0x0ABC00F1),
                     hexline(6, 2, 0x0ABC00F1, 0x511F0011, 0xFEDCBA980100F050, 0x0ABC00F1),
                     hexline(6, 1, 0x0100F050, 0x1511F0011, 0xFEDCBA980100F050, 0x0ABC00F1),
                     hexline(4, 0, MASK64, 0x511F0011, 0xFEDCBA980100F050, 0x0ABC00F1)]
            sp11 = forge_batch(build_forge(tmp, text, 1), lines)
            a16 = forge_batch(build_forge(tmp, text, None), lines)
        reads = f'{EXPECTED_SP11_READS:x}'
        self.assertEqual(sp11, [f'fedcba980000f000 {reads}', f'abc51f1 {reads}', f'0 {reads}',
                                f'fffffff0ffffffff {reads}'])
        self.assertEqual(a16, ['100f050 0', 'abc00f1 0', '100f050 0', 'fffffff0ffffffff 0'])

    def test_source_check_accepts_the_real_files(self):
        check_idregs(IDREGS_H.read_text())
        check_prepare(PREPARE_C.read_text())

    def test_weakened_hooks_fail(self):
        original = IDREGS_H.read_text()
        for name, (mutate, fragment, harness) in HOOK_MUTANTS.items():
            with self.subTest(name):
                mutant = mutate(original)
                self.assertNotEqual(mutant, original)
                if fragment is not None:
                    with self.assertRaises(AssertionError) as caught:
                        check_idregs(mutant)
                    self.assertIn(fragment, str(caught.exception))
                    print(f'hook mutant {name!r}: source check: {str(caught.exception)[:90]}', file=sys.stderr)
                if harness:
                    with scratch('hook-mutant-') as tmp:
                        problems = forge_failures(tmp, mutant, (1, None))
                    self.assertTrue(problems, f'{name}: the id_forge() harness still agrees with the model')
                    print(f'hook mutant {name!r}: harness: {problems[0][:110]}', file=sys.stderr)

    def test_weakened_gate_uses_fail_the_source_check(self):
        original = PREPARE_C.read_text()
        mutants = {
            'PAuth refusal not reported': ('snap_oryon_verdict_status(pauth)', '-1', 'its own status'),
            'PAuth check dropped from the gate': (
                'snap_oryon_normalize_pauth(midr, isar1, isar2, 0, 0)',
                'SNAP_ORYON_NORMALIZED', 'this CPU\'s own MIDR, ISAR1 and ISAR2'),
            'any verdict launches': (
                'pauth == SNAP_ORYON_NORMALIZED || pauth == SNAP_ORYON_PASSTHROUGH', 'pauth >= 0',
                'only the two success verdicts'),
        }
        for name, (old, new, fragment) in mutants.items():
            with self.subTest(name):
                with self.assertRaises(AssertionError) as caught:
                    check_prepare(drop_text(original, old, new))
                self.assertIn(fragment, str(caught.exception))


class Wiring(unittest.TestCase):
    @staticmethod
    def literal(name):
        for node in ast.parse(RUN_PY.read_text()).body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name
                                                    for t in node.targets):
                return ast.literal_eval(node.value)
        raise AssertionError(f'{name} missing from probes/run.py')

    def test_run_py_links_the_unit_only_with_the_define(self):
        optional = self.literal('OPTIONAL')['xnuboot']
        self.assertEqual([define for define, rel in optional if rel == 'compat/oryon-idregs.c'],
                         ['-DSNAP_PLATFORM_SP11=1'])
        self.assertNotIn('compat/oryon-idregs.c', self.literal('MULTI')['xnuboot'])

    def test_both_statuses_have_a_prepare_failure_sentence(self):
        failures = self.literal('PREPARE_FAILURES')
        for status in (-94, -95):
            self.assertIn(status, failures)
            self.assertGreater(len(failures[status]), 10)
        texts = list(failures.values())
        self.assertEqual(len(set(texts)), len(texts))

    def test_the_unit_is_freestanding_integer_code(self):
        # It is linked into the position-independent blob: no libc, no string or pointer data, and
        # no writable file-scope variable (the linker script rejects non-const .data and .bss).
        source = UNIT_C.read_text()
        self.assertEqual(re.findall(r'#include\s*[<"]([^>"]+)[>"]', source), ['oryon-idregs.h'])
        self.assertEqual(re.findall(r'#include\s*[<"]([^>"]+)[>"]', UNIT_H.read_text()), ['stdint.h'])
        body = re.sub(r'/\*.*?\*/', '', source, flags=re.S)
        self.assertNotRegex(body, r'\b(malloc|printf|memcpy|memset|strlen)\b')
        self.assertNotIn('"', re.sub(r'#include\s*"[^"]*"', '', body))
        self.assertEqual(re.findall(r'^static (?!const)[^(\n]*[=;]', body, re.M), [])

    def test_a16_translation_unit_does_not_mention_the_unit(self):
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

        pattern = re.compile(r'snap_oryon_|oryon-idregs|id_forge_sp11')
        self.assertIsNone(pattern.search(preprocess()))
        self.assertIsNone(pattern.search(preprocess('-DSNAP_PLATFORM_SP11=0')))
        sp11 = preprocess('-DSNAP_PLATFORM_SP11=1')
        for token in ('snap_oryon_id_view', 'snap_oryon_normalize_pauth', 'id_forge_sp11'):
            self.assertIn(token, sp11)

    def test_the_unit_reaches_the_gate_through_idregs_h_ahead_of_prepare_c(self):
        # prepare.c is a part of the unity translation unit and relies on what idregs.h included
        # before it; the header is pragma-once, and nothing else includes it.
        main = (ROOT / 'probes/xnuboot.c').read_text()
        self.assertEqual(main.count('#include "idregs.h"'), 1)
        self.assertLess(main.index('#include "idregs.h"'), main.index('#include "xnuboot/prepare.c"'))
        self.assertIn('#pragma once', UNIT_H.read_text())
        self.assertEqual(IDREGS_H.read_text().count('id_forge_sp11(crm, op2, value)'), 1)
        unity = read_source(ROOT / 'probes/xnuboot.c')
        self.assertEqual(unity.count('snap_oryon_normalize_pauth('), 1)
        include = re.compile(r'^[ \t]*#[ \t]*include[ \t]+"[^"]*oryon-idregs\.h"', re.M)
        users = [str(path.relative_to(ROOT)) for path in sorted(ROOT.glob('probes/**/*.[ch]'))
                 if include.search(path.read_text())]
        self.assertEqual(users, ['probes/idregs.h'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
