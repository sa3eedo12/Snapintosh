#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""compat/dc-zva-model.{h,c}: the SP11 DC ZVA decision and emulation model (plan todo C6).

    python3 tools/test-dc-zva-model.py

Offline: no hardware, no network. It builds tools/test-dc-zva-model.c with
address and undefined-behaviour sanitizers and runs it: the trap decode against
every other system instruction and every single-bit corruption of a valid ESR,
the block geometry, block-confined zeroing with guard bytes, the DZP truth
table, the admission decision, and XNU's own use of DC ZVA (bzero and the page
clear, run on emulated DC ZVA: exact for a 64-byte block, corrupting for any
other). It then compares the C model with an independent oracle, cross-checks
the DC ZVA encoding and the ESR layout against LLVM's assembler when llvm-mc is
present (SKIP otherwise, never a pass), checks purity and the SP11 gate, and
seeds known bugs one at a time: every one must be caught.

What it supports is a decision, not a fix: DCZID_EL0 = 0x14 is DZP set with a
64-byte block, so the A16 test "DCZID_EL0 == 4" can refuse a working
instruction. Whether Oryon X1's DC ZVA is functionally broken is OBSERVED in
one third-party patch only and stays UNKNOWN until the X2 functional test.
"""
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / 'build' / 'dc-zva'
MODEL_C = ROOT / 'compat/dc-zva-model.c'
MODEL_H = ROOT / 'compat/dc-zva-model.h'
DRIVER = ROOT / 'tools/test-dc-zva-model.c'
NEW_FILES = [MODEL_C, MODEL_H, DRIVER, Path(__file__).resolve()]
CFLAGS = ['-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-DSNAP_PLATFORM_SP11=1']
M64 = (1 << 64) - 1


def compile_driver(out, model_dir=None, sanitize=True, werror=True):
    inc = Path(model_dir) if model_dir else ROOT / 'compat'
    cmd = ['cc', *CFLAGS, f'-I{inc}', f'-I{ROOT / "compat"}']
    if werror:
        cmd.append('-Werror')
    if sanitize:
        cmd += ['-fsanitize=address,undefined', '-fno-sanitize-recover=all']
    cmd += [str(DRIVER), str(inc / 'dc-zva-model.c'), '-o', str(out)]
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=300)
    if result.returncode:
        raise RuntimeError(result.stderr[-3000:])
    return out


# ---------------------------------------------------------------------------
# The independent oracle, written from the ESR_EL2 and DC ZVA descriptions.
# ---------------------------------------------------------------------------
ISS_DC_ZVA = (1 << 20) | (1 << 17) | (3 << 14) | (7 << 10) | (4 << 1)
INSN_DC_ZVA = 0xd50b7420


def o_decode(esr):
    if (esr >> 26) & 0x3f != 0x18:
        return 'NOT_SYSTEM_TRAP'
    if not (esr >> 25) & 1:
        return 'IL'
    iss = esr & 0x1ffffff
    if esr >> 32 or iss >> 22:
        return 'ISS_RES0'
    if (iss & 0x3fffff & ~(0x1f << 5) & ~1) != ISS_DC_ZVA:
        return 'NOT_DC_ZVA'
    if iss & 1:
        return 'READ'
    return f'OK {(iss >> 5) & 0x1f}'


def o_insn(insn):
    return 'NOT_DC_ZVA' if insn & ~0x1f != INSN_DC_ZVA else f'OK {insn & 0x1f}'


def o_block(dczid):
    if dczid >> 5:
        return 'RESERVED_BITS'
    bs = dczid & 0xf
    return 'BLOCK_SIZE' if bs > 9 else f'OK {4 << bs}'


def o_dzp(e2h, tge, tdz, d1, d2):
    if e2h and tge:
        return 0 if d2 else 1
    if tdz:
        return 1
    return 0 if d1 else 1


def o_admit(dczid, f, emu):
    b = o_block(dczid)
    if not b.startswith('OK'):
        return b
    if dczid & 0xf != 4:
        return 'BLOCK_SIZE'
    if f == 0:
        return 'UNTESTED'
    if f not in (1, 2):
        return 'PARAMS'
    if f == 1:
        return 'OK 0 1 0 64'
    return 'OK 1 1 1 64' if emu else 'BROKEN_NO_EMULATION'


def o_emulate(block, base, length, va, device):
    length = min(length, 4096)
    if block < 16 or block > 2048 or block & (block - 1):
        return 'PARAMS 0'
    aligned = va & ~(block - 1)
    if aligned < base or aligned - base > length or length - (aligned - base) < block:
        return 'RANGE 0'
    if device:
        return 'DEVICE 0'
    return f'OK {aligned} {block}'


def make_commands(count, seed):
    rng = random.Random(seed)
    out = []
    valid_esr = (0x18 << 26) | (1 << 25) | ISS_DC_ZVA
    for _ in range(count):
        kind = rng.choice('EEEIBAZZZ')
        if kind == 'E':
            esr = valid_esr | (rng.randrange(32) << 5)
            for _ in range(rng.choice([0, 0, 1, 1, 2, 3])):
                esr ^= 1 << rng.randrange(64)
            if rng.random() < 0.1:
                esr = rng.getrandbits(64)
            out.append((f'E {esr:x}', o_decode(esr)))
        elif kind == 'I':
            insn = INSN_DC_ZVA | rng.randrange(32)
            for _ in range(rng.choice([0, 0, 1, 2])):
                insn ^= 1 << rng.randrange(32)
            out.append((f'I {insn:x}', o_insn(insn)))
        elif kind == 'B':
            v = rng.choice([rng.randrange(0x40), rng.getrandbits(64), 4 | 1 << rng.randrange(64)])
            out.append((f'B {v:x}', o_block(v)))
        elif kind == 'A':
            v = rng.choice([4, 0x14, rng.randrange(0x40), 0x104, 4 | 1 << rng.randrange(64)])
            f, emu = rng.choice([0, 1, 2, 2, 3]), rng.randrange(2)
            out.append((f'A {v:x} {f} {emu}', o_admit(v, f, emu)))
        else:
            block = rng.choice([16, 32, 64, 64, 128, 256, 2048, 8, 24, 4096, 4 << rng.randrange(10)])
            base = rng.choice([0, 0x1000, rng.getrandbits(48), M64 - 127, M64 - 4095])
            length = rng.choice([0, 128, 256, 4096, 5000, rng.randrange(1, 4200)])
            va = base + rng.choice([0, 1, 63, 64, 127, 200, rng.randrange(0, 4300)])
            va = rng.choice([va & M64, rng.getrandbits(64), base - 1 if base else 0, M64])
            device = int(rng.random() < 0.1)
            out.append((f'Z {block} {base} {length} {va} {device}', o_emulate(block, base, length, va, device)))
    for m in range(32):
        out.append((f'P {m & 1} {(m >> 1) & 1} {(m >> 2) & 1} {(m >> 3) & 1} {(m >> 4) & 1}',
                    str(o_dzp(m & 1, (m >> 1) & 1, (m >> 2) & 1, (m >> 3) & 1, (m >> 4) & 1))))
    return out


def differential(exe, commands):
    text = '\n'.join(c for c, _ in commands) + '\n'
    result = subprocess.run([str(exe), '--stdin'], input=text, capture_output=True, text=True, timeout=300)
    if result.returncode:
        raise RuntimeError(result.stderr[-2000:])
    lines = result.stdout.splitlines()
    if len(lines) != len(commands):
        return [f'{len(lines)} answers for {len(commands)} commands']
    bad = []
    for (cmd, want), line in zip(commands, lines):
        if line != want:
            bad.append(f'{cmd} -> native {line!r}, oracle {want!r}')
            if len(bad) > 3:
                break
    return bad


MUTANTS = {
    'the block address is not aligned down':
        ('const uint64_t block = va & ~((uint64_t)block_bytes - 1);', 'const uint64_t block = va;'),
    'BS is read as log2 of bytes instead of words':
        ('*bytes = UINT32_C(4) << bs;', '*bytes = UINT32_C(1) << bs;'),
    'a read-direction system instruction is accepted':
        ('if (iss & 1) return DZV_E_READ;', ''),
    'any system instruction is accepted as DC ZVA':
        ('if ((iss & DZV_ISS_FIELDS & ~UINT32_C(1)) != DZV_ISS_DC_ZVA) return DZV_E_NOT_DC_ZVA;', ''),
    'a 16-bit instruction class (IL = 0) is accepted':
        ('if (!((esr >> 25) & 1)) return DZV_E_IL;', ''),
    'a block outside the window is zeroed':
        ('    if (block < w->base_va || block - w->base_va > w->len || w->len - (block - w->base_va) < block_bytes)\n'
         '        return DZV_E_RANGE;\n', ''),
    'the zeroing writes one byte past the block':
        ('for (uint32_t i = 0; i < block_bytes; i++) p[i] = 0;', 'for (uint32_t i = 0; i <= block_bytes; i++) p[i] = 0;'),
    'a block size other than 64 bytes is admitted':
        ('if ((dczid_seen & 0xf) != DZV_BS_REQUIRED) return DZV_E_BLOCK_SIZE;', ''),
    'an untested instruction is admitted':
        ('if (functional == DZV_ZVA_UNTESTED) return DZV_E_UNTESTED;', ''),
    'TDZ is ignored when computing DZP':
        ('if (!host && tdz) return 1;', ''),
    'Device memory is zeroed instead of faulting':
        ('if (w->device) return DZV_E_DEVICE;', ''),
    'reserved DCZID_EL0 bits are accepted':
        ('if (dczid >> 5) return DZV_E_RESERVED_BITS;', ''),
    'a broken instruction is admitted without emulation':
        ('if (!emulation_enabled) return DZV_E_BROKEN_NO_EMULATION;', ''),
    'ISS2 bits are ignored':
        ('if ((esr >> 32) || (iss >> 22)) return DZV_E_ISS_RES0;', 'if (iss >> 22) return DZV_E_ISS_RES0;'),
    'DZP in the host regime reads SCTLR_EL1.DZE':
        ('return host ? !sctlr_el2_dze : !sctlr_el1_dze;', 'return !sctlr_el1_dze;'),
}


def mutate_model(name):
    old, new = MUTANTS[name]
    text = MODEL_C.read_text()
    if text.count(old) != 1:
        raise AssertionError(f'mutant {name!r}: pattern occurs {text.count(old)} times')
    return text.replace(old, new)


LLVM_MC = shutil.which('llvm-mc') or '/opt/homebrew/opt/llvm/bin/llvm-mc'


def assemble(line, attrs='+mte'):
    out = subprocess.run([LLVM_MC, '--triple=aarch64', f'-mattr={attrs}', '--show-encoding'], input=line + '\n',
                         capture_output=True, text=True)
    match = re.search(r'encoding: \[(0x[0-9a-f]{2}),(0x[0-9a-f]{2}),(0x[0-9a-f]{2}),(0x[0-9a-f]{2})\]', out.stdout)
    if not match:
        raise RuntimeError(out.stderr)
    return int.from_bytes(bytes(int(b, 16) for b in match.groups()), 'little')


class Native(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        BUILD.mkdir(parents=True, exist_ok=True)
        cls.exe = compile_driver(BUILD / 'test-dc-zva-model')

    def test_driver_passes_under_sanitizers(self):
        result = subprocess.run([str(self.exe)], capture_output=True, text=True, timeout=600)
        self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-2000:])
        self.assertTrue(result.stdout.rstrip().endswith('PASS'), result.stdout)
        print('driver |', result.stdout.strip().splitlines()[0], file=sys.stderr)

    def test_model_matches_independent_oracle(self):
        commands = make_commands(20000, 0xc6c6)
        bad = differential(self.exe, commands)
        self.assertEqual(bad, [], '\n'.join(bad))
        kinds = {c[0].split()[0] + ' ' + a.split()[0] for c, a in commands}
        for needed in ('E OK', 'E NOT_DC_ZVA', 'E ISS_RES0', 'E READ', 'E IL', 'E NOT_SYSTEM_TRAP', 'Z OK', 'Z RANGE',
                       'Z DEVICE', 'Z PARAMS', 'A OK', 'A BLOCK_SIZE', 'A UNTESTED', 'A BROKEN_NO_EMULATION',
                       'B OK', 'B RESERVED_BITS', 'I OK', 'I NOT_DC_ZVA'):
            self.assertIn(needed, kinds, f'the generated commands never reach {needed!r}')

    @unittest.skipUnless(Path(LLVM_MC).exists(), 'SKIP: llvm-mc is not installed')
    def test_encoding_and_esr_layout_agree_with_the_assembler(self):
        header = MODEL_H.read_text()
        self.assertEqual(int(re.search(r'DZV_INSN_DC_ZVA UINT32_C\((0x[0-9a-f]+)\)', header).group(1), 16), INSN_DC_ZVA)
        for rt in (0, 1, 5, 17, 30):
            insn = assemble(f'dc zva, x{rt}')
            self.assertEqual(insn, INSN_DC_ZVA | rt)
            # Field extraction from the assembled word, then the ESR_EL2 ISS layout: Op0 [21:20], Op2 [19:17], Op1 [16:14],
            # CRn [13:10], Rt [9:5], CRm [4:1], Direction [0] (DDI 0601 ESR_EL2).
            op0, op1, crn, crm, op2 = (insn >> 19) & 3, (insn >> 16) & 7, (insn >> 12) & 15, (insn >> 8) & 15, (insn >> 5) & 7
            iss = (op0 << 20) | (op2 << 17) | (op1 << 14) | (crn << 10) | (rt << 5) | (crm << 1)
            esr = (0x18 << 26) | (1 << 25) | iss
            out = subprocess.run([str(self.exe), '--stdin'], input=f'E {esr:x}\n', capture_output=True, text=True).stdout
            self.assertEqual(out.strip(), f'OK {rt}')
        self.assertEqual(assemble('dc zva, xzr'), INSN_DC_ZVA | 31)
        # The other block-zeroing operations are different encodings, and the decoder refuses them.
        for name, op2 in (('gva', 3), ('gzva', 4)):
            self.assertEqual(assemble(f'dc {name}, x5'), 0xd50b7405 | (4 << 8) | (op2 << 5))
            out = subprocess.run([str(self.exe), '--stdin'], input=f'I {assemble(f"dc {name}, x5"):x}\n', capture_output=True,
                                 text=True).stdout
            self.assertEqual(out.strip(), 'NOT_DC_ZVA')
        for other in ('dc civac, x5', 'dc cvac, x5', 'dc cvau, x5', 'dc ivac, x5'):
            out = subprocess.run([str(self.exe), '--stdin'], input=f'I {assemble(other):x}\n', capture_output=True,
                                 text=True).stdout
            self.assertEqual(out.strip(), 'NOT_DC_ZVA', other)

    def test_seeded_bugs_are_caught(self):
        commands = make_commands(6000, 0xdead)
        survivors = []
        for name in MUTANTS:
            with tempfile.TemporaryDirectory(dir=BUILD) as d:
                shutil.copy(MODEL_H, d)
                shutil.copy(ROOT / 'compat/platform.h', d)
                (Path(d) / 'dc-zva-model.c').write_text(mutate_model(name))
                try:
                    exe = compile_driver(Path(d) / 'mutant', model_dir=d, sanitize=False, werror=False)
                except RuntimeError as error:
                    self.fail(f'mutant {name!r} does not compile: {error}')
                own = subprocess.run([str(exe)], capture_output=True, text=True, timeout=600)
                caught = 'driver'
                if own.returncode == 0:
                    caught = 'oracle' if differential(exe, commands) else None
                if caught is None:
                    survivors.append(name)
                print(f'mutant | {caught or "SURVIVED":7} | {name}', file=sys.stderr)
        self.assertEqual(survivors, [])
        self.assertGreaterEqual(len(MUTANTS), 6)


class Hygiene(unittest.TestCase):
    def test_spdx_and_size(self):
        for path in NEW_FILES:
            text = path.read_text()
            self.assertIn('SPDX-License-Identifier: MIT', '\n'.join(text.splitlines()[:3]), path.name)
            self.assertLess(len(text.splitlines()), 1500, path.name)
            self.assertLess(len(text.encode()), 50 * 1024, path.name)

    def test_model_is_pure(self):
        for path in (MODEL_C, MODEL_H):
            text = re.sub(r'/\*.*?\*/', '', path.read_text(), flags=re.S)
            for token in ('printf', 'fopen', 'malloc', 'calloc', 'free(', '__asm__', 'asm(', 'asm volatile', 'system(',
                          'getenv', 'time(', 'rand(', 'socket', 'open(', 'read(', 'write(', 'memset', 'memcpy'):
                self.assertNotIn(token, text, f'{path.name}: {token}')
            for include in re.findall(r'#include\s*[<"]([^>"]+)[>"]', text):
                self.assertIn(include, ('platform.h', 'stddef.h', 'stdint.h', 'dc-zva-model.h'), include)

    def test_gate_off_preprocesses_to_nothing(self):
        for path in (MODEL_H, MODEL_C):
            out = subprocess.run(['cc', '-E', '-P', '-I', str(ROOT / 'compat'), str(path)], capture_output=True,
                                 text=True, check=True).stdout
            body = [line for line in out.splitlines() if line.strip()]
            self.assertTrue(all('dzv_' not in line and 'DZV_' not in line for line in body), body[:5])
            self.assertLessEqual(len(body), 1, body)
        BUILD.mkdir(parents=True, exist_ok=True)
        obj = BUILD / 'gate-off.o'
        subprocess.run(['cc', '-std=gnu11', '-c', '-Wall', '-Wextra', '-Werror', '-Wpedantic', '-I', str(ROOT / 'compat'),
                        str(MODEL_C), '-o', str(obj)], check=True)
        symbols = subprocess.run(['nm', '-g', str(obj)], capture_output=True, text=True).stdout.strip()
        self.assertEqual(symbols, '', 'the gate-off object must define nothing')

    def test_default_build_flags_do_not_define_the_gate(self):
        defined = subprocess.run(['cc', '-E', '-dM', '-I', str(ROOT / 'compat'), str(MODEL_H)], capture_output=True,
                                 text=True, check=True).stdout
        self.assertNotIn('DZV_', defined)
        self.assertIn('SNAP_PLATFORM_SP11 0', defined)

    def test_freestanding_aarch64_object_needs_no_libc(self):
        llvm_nm = shutil.which('llvm-nm') or '/opt/homebrew/opt/llvm/bin/llvm-nm'
        if not Path(llvm_nm).exists():
            self.skipTest('SKIP: llvm-nm is not installed')
        BUILD.mkdir(parents=True, exist_ok=True)
        obj = BUILD / 'freestanding.o'
        subprocess.run(['cc', '--target=aarch64-none-elf', '-ffreestanding', '-O2', '-std=gnu11', '-Wall', '-Wextra',
                        '-Werror', '-DSNAP_PLATFORM_SP11=1', '-I', str(ROOT / 'compat'), '-c', str(MODEL_C), '-o',
                        str(obj)], check=True)
        undefined = subprocess.run([llvm_nm, '-u', str(obj)], capture_output=True, text=True, check=True).stdout.split()
        self.assertEqual(undefined, [], 'the model must link into a freestanding monitor without libc')

    def test_not_linked_into_any_build(self):
        for path in (ROOT / 'probes/run.py', ROOT / 'probes/xnuboot.c'):
            self.assertNotIn('dc-zva-model', path.read_text(), path.name)

    def test_a16_admission_is_untouched(self):
        # The A16 monitor still demands DCZID_EL0 == 4 before granting DC ZVA; this model does not change that.
        text = (ROOT / 'probes/xnuboot/prepare.c').read_text()
        self.assertIn('if(dczid!=4)return -26;', text)


class Docs(unittest.TestCase):
    """docs/SP11-EL2-ENTRY.md section 4 is the record of the C6 decision; it must explain every refusal the model gives."""

    def test_every_refusal_is_explained(self):
        text = (ROOT / 'docs/SP11-EL2-ENTRY.md').read_text()
        names = set(re.findall(r'\bDZV_E_([A-Z0-9_]+)\b', MODEL_H.read_text()))
        self.assertGreaterEqual(len(names), 12)
        missing = sorted(n for n in names if f'`{n}`' not in text)
        self.assertEqual(missing, [], 'refusals the document does not explain')
        for needle in ('0x14', 'DZP', 'xnu-12377.121.6', 'not applicable'):
            self.assertIn(needle, text)


if __name__ == '__main__':
    unittest.main(verbosity=1)
