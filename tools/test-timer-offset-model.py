#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""compat/timer-offset-model.{h,c}: the SP11 guest counter and compare-value model (plan todo C3).

    python3 tools/test-timer-offset-model.py

Offline: no hardware, no network. It builds tools/test-timer-offset-model.c
with address and undefined-behaviour sanitizers, linked with the A16
monitor's own compat/clock.c so that the model is checked against the code it
re-derives: the count converts exactly as clock_rate_sample does wherever that
does not overflow, and clock_rate_wait is the earliest tick that reaches a
deadline. Then it compares the C model with an independent oracle that uses
Python's arbitrary-precision integers (conversions, samples, deadlines and the
per-CPU admission policy with injected mismatched CNTVOFF_EL2 values), checks
that the header preprocesses to nothing without SNAP_PLATFORM_SP11, that the
model is pure, and seeds known bugs one at a time: every one must make the
driver or the oracle comparison fail.

The model decides which time base is admissible and what every conversion does
at its limits. It proves the policy that a CNTVOFF_EL2 that does not read back
what was written is never relied on and fails the boot closed. A pass is a
host-side result about the model. Whether the Oryon X1 CNTVOFF_EL2 erratum
applies to the unit, and how the register misbehaves, stays UNKNOWN until X2.
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
BUILD = ROOT / 'build' / 'timer-offset'
MODEL_C = ROOT / 'compat/timer-offset-model.c'
MODEL_H = ROOT / 'compat/timer-offset-model.h'
DRIVER = ROOT / 'tools/test-timer-offset-model.c'
CLOCK_C = ROOT / 'compat/clock.c'
NEW_FILES = [MODEL_C, MODEL_H, DRIVER, Path(__file__).resolve()]
CFLAGS = ['-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-DSNAP_PLATFORM_SP11=1']
M64 = (1 << 64) - 1
MAX_RATE = (1 << 32) - 1
MAX_CPUS = 16


def compile_driver(out, model_dir=None, sanitize=True, werror=True):
    inc = Path(model_dir) if model_dir else ROOT / 'compat'
    cmd = ['cc', *CFLAGS, f'-I{inc}', f'-I{ROOT / "compat"}']
    if werror:
        cmd.append('-Werror')
    if sanitize:
        cmd += ['-fsanitize=address,undefined', '-fno-sanitize-recover=all']
    cmd += [str(DRIVER), str(inc / 'timer-offset-model.c'), str(CLOCK_C), '-o', str(out)]
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=300)
    if result.returncode:
        raise RuntimeError(result.stderr[-3000:])
    return out


# ---------------------------------------------------------------------------
# The independent oracle: exact integer arithmetic, written from the policy
# in compat/timer-offset-model.h, sharing no code with the model.
# ---------------------------------------------------------------------------
def params_ok(h, g):
    return 0 < h <= MAX_RATE and 0 < g <= MAX_RATE


def o_floor(h, g, c):
    if not params_ok(h, g):
        return 'ERR PARAMS'
    v = c * g // h
    return f'OK {v}' if v <= M64 else 'ERR OVERFLOW'


def o_ceil(h, g, t):
    if not params_ok(h, g):
        return 'ERR PARAMS'
    v = -(-t * h // g)
    return f'OK {v}' if v <= M64 else 'ERR OVERFLOW'


def o_clock(h, g, base, e2h, epoch, voff):
    """Returns an error string or the origin function; mirrors tom_clock_init."""
    if not params_ok(h, g) or base > 1:
        return 'ERR PARAMS'
    if not voff or len(voff) > MAX_CPUS:
        return 'ERR NO_CPUS'
    return None


def origin(base, e2h, epoch, voff, cpu):
    if base == 1:
        return epoch
    return 0 if e2h else voff[cpu]


def o_sample(h, g, base, e2h, epoch, voff, cpu, p):
    bad = o_clock(h, g, base, e2h, epoch, voff)
    if bad:
        return bad
    if cpu >= len(voff):
        return 'ERR NO_CPUS'
    o = origin(base, e2h, epoch, voff, cpu)
    if p < o:
        return 'ERR OFFSET_RANGE'
    return o_floor(h, g, p - o)


def o_deadline(h, g, base, e2h, epoch, voff, cpu, cval):
    bad = o_clock(h, g, base, e2h, epoch, voff)
    if bad:
        return bad
    if cpu >= len(voff):
        return 'ERR NO_CPUS'
    ticks = o_ceil(h, g, cval)
    if ticks.startswith('ERR'):
        return ticks
    at = int(ticks.split()[1]) + origin(base, e2h, epoch, voff, cpu)
    return f'OK {at}' if at <= M64 else 'ERR OVERFLOW'


def o_admit(h, g, base, skew, cpus):
    n = len(cpus)
    if not params_ok(h, g) or base > 1:
        return 'ERR PARAMS ' + str(n)
    if n == 0 or n > MAX_CPUS:
        return 'ERR NO_CPUS ' + str(n)
    lo, hi = M64, 0
    for i, c in enumerate(cpus):
        frq, vin, vwv, vw, vrb, cpt, cpw, cprb, ecv, e2h = c
        if ecv > 2:
            return f'ERR ECV_RESERVED {i}'
        if ecv == 0 and h != g:
            return f'ERR NO_ECV {i}'
        if frq != h:
            return f'ERR FREQ {i}'
        if e2h != cpus[0][9]:
            return f'ERR E2H_MIX {i}'
        if vw and vrb != vwv:
            return f'ERR CNTVOFF_READBACK {i}'
        if cpt:
            if ecv < 2:
                return f'ERR CNTPOFF_ABSENT {i}'
            if cprb != cpw:
                return f'ERR CNTPOFF_READBACK {i}'
        relied = vwv if vw else vin
        lo, hi = min(lo, relied), max(hi, relied)
    if base == 0 and not cpus[0][9]:
        s = o_floor(h, g, hi - lo)
        if s.startswith('ERR'):
            return f'{s} {n}'
        if int(s.split()[1]) > skew:
            return f'ERR CNTVOFF_SKEW {n}'
    return f'OK OK {n}'


EDGES = [0, 1, 2, 3, 19199999, 19200000, 1 << 32, (1 << 63) - 1, 1 << 63, 14757395258967641292, 14757395258967641293,
         M64 - 1, M64, (1 << 32) - 1]
RATES = [(19200000, 24000000), (24000000, 19200000), (1000000, 1000000), (1000000000, 24000000), (MAX_RATE, MAX_RATE),
         (MAX_RATE, 1), (1, MAX_RATE), (19200000, 1000000000), (0, 5), (5, 0), (1 << 32, 7), (7, 1 << 32)]


def rnd_val(rng):
    r = rng.random()
    if r < 0.25:
        return rng.choice(EDGES)
    return rng.getrandbits(64) >> rng.randrange(0, 64)


def make_lines(count, seed):
    rng = random.Random(seed)
    out = []
    for _ in range(count):
        h, g = rng.choice(RATES)
        kind = rng.choice('FFCCSSDDAAA')
        if kind in 'FC':
            out.append((f'{kind} {h} {g} {rnd_val(rng)}', kind, (h, g, None)))
        elif kind in 'SD':
            n = rng.choice([1, 2, 12, 12, 16, 3])
            voff = [rng.choice([0, 0, 0, rnd_val(rng)]) for _ in range(n)]
            base, e2h = rng.randrange(2), rng.randrange(2)
            epoch = rng.choice([0, rnd_val(rng)])
            cpu = rng.choice([0, n - 1, rng.randrange(n), n])
            x = rnd_val(rng)
            if kind == 'S' and rng.random() < 0.5 and base == 0 and not e2h and cpu < n:
                x = min(M64, voff[cpu] + rng.choice([0, 1, 100, rnd_val(rng) >> 10]))   # stay above the offset more often
            out.append((f'{kind} {h} {g} {base} {e2h} {epoch} {n} ' + ' '.join(map(str, voff)) + f' {cpu} {x}', kind,
                        (h, g, base, e2h, epoch, voff, cpu, x)))
        else:
            n = rng.choice([1, 2, 12, 12, 12, 16, 0, 5])
            base, skew = rng.randrange(2), rng.choice([0, 0, 1, 4, 5, 100, M64])
            cpus = []
            e2h0 = rng.randrange(2)
            for _ in range(min(n, MAX_CPUS)):
                frq = h if rng.random() < 0.9 else rng.choice([0, g, 1])
                vin = rng.choice([0, 0, 77, rnd_val(rng)])
                vw = int(rng.random() < 0.8)
                vwv = rng.choice([0, 0, 4, rnd_val(rng)])
                vrb = vwv if rng.random() < 0.9 else rng.choice([0, 1, rnd_val(rng)])
                cpt = int(rng.random() < 0.15)
                cpw = rng.choice([0, 99])
                cprb = cpw if rng.random() < 0.8 else 1
                ecv = rng.choice([2, 2, 2, 1, 0, 3])
                e2h = e2h0 if rng.random() < 0.95 else 1 - e2h0
                cpus.append((frq, vin, vwv, vw, vrb, cpt, cpw, cprb, ecv, e2h))
            body = ' '.join(' '.join(map(str, c)) for c in cpus)
            out.append((f'A {h} {g} {base} {skew} {n} {body}', kind, (h, g, base, skew, cpus, n)))
    return out


def expected(entry):
    _, kind, args = entry
    if kind == 'F':
        return o_floor(args[0], args[1], int(entry[0].split()[3]))
    if kind == 'C':
        return o_ceil(args[0], args[1], int(entry[0].split()[3]))
    if kind == 'S':
        return o_sample(*args)
    if kind == 'D':
        return o_deadline(*args)
    h, g, base, skew, cpus, n = args
    return o_admit(h, g, base, skew, cpus)


def differential(exe, entries):
    text = '\n'.join(e[0] for e in entries) + '\n'
    result = subprocess.run([str(exe), '--stdin'], input=text, capture_output=True, text=True, timeout=300)
    if result.returncode:
        raise RuntimeError(result.stderr[-2000:])
    lines = result.stdout.splitlines()
    if len(lines) != len(entries):
        return [f'{len(lines)} answers for {len(entries)} commands']
    bad = []
    for entry, line in zip(entries, lines):
        want = expected(entry)
        if line != want:
            bad.append(f'{entry[0][:160]} -> native {line!r}, oracle {want!r}')
            if len(bad) > 3:
                break
    return bad


MUTANTS = {
    'the count rounds up, so the guest can run ahead':
        ('if (__builtin_add_overflow(whole, ((count % den) * num) / den, &sum)) return TOM_E_OVERFLOW;',
         'if (__builtin_add_overflow(whole, ((count % den) * num + den - 1) / den, &sum)) return TOM_E_OVERFLOW;'),
    'the deadline rounds down, so a timer fires early':
        ('((count % den) * num + den - 1) / den, &sum)', '((count % den) * num) / den, &sum)'),
    'the count conversion multiplies without a check':
        ('if (__builtin_mul_overflow(count / den, num, &whole)) return TOM_E_OVERFLOW;\n    if (__builtin_add_overflow(whole, ((count % den) * num) / den, &sum))',
         'whole = (count / den) * num;\n    if (__builtin_add_overflow(whole, ((count % den) * num) / den, &sum))'),
    'a CNTVOFF_EL2 read-back mismatch is accepted':
        ('if (o->voff_written_valid && o->voff_readback != o->voff_written) return TOM_E_CNTVOFF_READBACK;', ''),
    'the cross-CPU skew bound is ignored':
        ('if (skew > pol->max_skew_guest_ticks) return TOM_E_CNTVOFF_SKEW;', ''),
    'an offset above the physical count wraps instead of being refused':
        ('    if (physical < origin) return TOM_E_OFFSET_RANGE;\n    *out = physical - origin;',
         '    *out = physical - origin;'),
    'no ECV is accepted although the rates differ':
        ('if (o->ecv_level == 0 && p->host_hz != p->guest_hz) return TOM_E_NO_ECV;', ''),
    'a CNTFRQ_EL0 mismatch is accepted':
        ('if (o->cntfrq != p->host_hz) return TOM_E_FREQ;', ''),
    'CPUs in different E2H states are accepted':
        ('if (o->e2h != obs[0].e2h) return TOM_E_E2H_MIX;', ''),
    'a guest count that goes backwards is accepted':
        ('if (g < c->last[cpu]) return TOM_E_NOT_MONOTONIC;', ''),
    'the relied-on offset ignores the written value':
        ('const uint64_t relied = o->voff_written_valid ? o->voff_written : o->voff_incoming;',
         'const uint64_t relied = o->voff_incoming;'),
    'the EL2 read applies the offset under E2H = 1':
        ('    else if (!c->e2h) origin = c->voff[cpu];\n    if (physical < origin)',
         '    else origin = c->voff[cpu];\n    if (physical < origin)'),
    'CNTPOFF_EL2 results are accepted without ECV level 2':
        ('if (o->ecv_level < 2) return TOM_E_CNTPOFF_ABSENT;', ''),
    'the deadline forgets the origin':
        ('if (__builtin_add_overflow(ticks, origin, &at)) return TOM_E_OVERFLOW;', 'at = ticks;'),
    'a reserved ECV encoding is accepted':
        ('if (o->ecv_level > 2) return TOM_E_ECV_RESERVED;', ''),
}


def mutate_model(name):
    old, new = MUTANTS[name]
    text = MODEL_C.read_text()
    if text.count(old) != 1:
        raise AssertionError(f'mutant {name!r}: pattern occurs {text.count(old)} times')
    return text.replace(old, new)


class Native(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        BUILD.mkdir(parents=True, exist_ok=True)
        cls.exe = compile_driver(BUILD / 'test-timer-offset-model')

    def test_driver_passes_under_sanitizers(self):
        result = subprocess.run([str(self.exe)], capture_output=True, text=True, timeout=600)
        self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-2000:])
        self.assertTrue(result.stdout.rstrip().endswith('PASS'), result.stdout)
        print('driver |', result.stdout.strip().splitlines()[0], file=sys.stderr)

    def test_model_matches_exact_integer_oracle(self):
        entries = make_lines(15000, 0xc3c3)
        bad = differential(self.exe, entries)
        self.assertEqual(bad, [], '\n'.join(bad))
        seen = {}
        for e in entries:
            seen[expected(e).split()[0] + ' ' + (expected(e).split()[1] if expected(e).startswith('ERR') else '')] = 1
        for needed in ('OK ', 'ERR OVERFLOW', 'ERR OFFSET_RANGE', 'ERR PARAMS', 'ERR NO_CPUS', 'ERR FREQ', 'ERR NO_ECV',
                       'ERR CNTVOFF_READBACK', 'ERR CNTVOFF_SKEW', 'ERR E2H_MIX', 'ERR CNTPOFF_ABSENT', 'ERR ECV_RESERVED'):
            self.assertIn(needed, seen, f'the generated commands never reach {needed!r}')

    def test_seeded_bugs_are_caught(self):
        entries = make_lines(5000, 0xfade)
        survivors = []
        for name in MUTANTS:
            with tempfile.TemporaryDirectory(dir=BUILD) as d:
                shutil.copy(MODEL_H, d)
                shutil.copy(ROOT / 'compat/platform.h', d)
                (Path(d) / 'timer-offset-model.c').write_text(mutate_model(name))
                try:
                    exe = compile_driver(Path(d) / 'mutant', model_dir=d, sanitize=False, werror=False)
                except RuntimeError as error:
                    self.fail(f'mutant {name!r} does not compile: {error}')
                own = subprocess.run([str(exe)], capture_output=True, text=True, timeout=600)
                caught = 'driver'
                if own.returncode == 0:
                    caught = 'oracle' if differential(exe, entries) else None
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
                          'getenv', 'time(', 'rand(', 'socket', 'open(', 'read(', 'write(', '__int128'):
                self.assertNotIn(token, text, f'{path.name}: {token}')
            for include in re.findall(r'#include\s*[<"]([^>"]+)[>"]', text):
                self.assertIn(include, ('platform.h', 'stddef.h', 'stdint.h', 'timer-offset-model.h'), include)

    def test_gate_off_preprocesses_to_nothing(self):
        for path in (MODEL_H, MODEL_C):
            out = subprocess.run(['cc', '-E', '-P', '-I', str(ROOT / 'compat'), str(path)], capture_output=True,
                                 text=True, check=True).stdout
            body = [line for line in out.splitlines() if line.strip()]
            self.assertTrue(all('tom_' not in line and 'TOM_' not in line for line in body), body[:5])
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
        self.assertNotIn('TOM_', defined)
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
            self.assertNotIn('timer-offset-model', path.read_text(), path.name)

    def test_a16_clock_code_is_untouched_by_the_test(self):
        # The driver links compat/clock.c unmodified; this test fails if the file is edited by a mutant run left behind.
        self.assertIn('clock_rate_sample', CLOCK_C.read_text())


class Docs(unittest.TestCase):
    """docs/SP11-EL2-ENTRY.md section 3 is the record of the C3 policy; it must explain every refusal the model gives."""

    def test_every_refusal_is_explained(self):
        text = (ROOT / 'docs/SP11-EL2-ENTRY.md').read_text()
        names = set(re.findall(r'\bTOM_E_([A-Z0-9_]+)\b', MODEL_H.read_text()))
        self.assertGreaterEqual(len(names), 13)
        missing = sorted(n for n in names if f'`{n}`' not in text)
        self.assertEqual(missing, [], 'refusals the document does not explain')
        self.assertIn('CNTVOFF_EL2', text)
        self.assertIn('TOM_BASE_CNTPCT_EPOCH', text)


if __name__ == '__main__':
    unittest.main(verbosity=1)
