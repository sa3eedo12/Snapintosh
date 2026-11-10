#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""compat/el2-entry-model.{h,c}: the SP11 EL2 entry transition model (plan todo C1).

    python3 tools/test-el2-entry-transition.py
    python3 tools/test-el2-entry-transition.py --survey     # which monitor accesses a VHE host would redirect

Offline: no hardware, no network. It builds tools/test-el2-entry-transition.c
with address and undefined-behaviour sanitizers and runs it (canonical de-VHE
sequences from every arrival state, 24 deliberately broken variants (plus a 25th case) that must
each be refused for the stated reason, replicas of slbounce's VHE entry, of the A16's secondary_entry and of the
converted slbounce restore the SP11 needs, register image conversions, the VHE register
redirection model). It then compares the C model with an independent Python
oracle over thousands of generated operation lists, checks that the header
preprocesses to nothing without SNAP_PLATFORM_SP11, that the model is pure
(no I/O, no allocation, no assembly), and seeds known bugs into the model one
at a time: every one must make the native driver or the oracle comparison fail.

Evidence labels in the rules (VERIFIED, INFERRED) are the model's own; see
docs/SP11-EL2-ENTRY.md. A pass here is a host-side proof about the model, not
about an SP11, and not a substitute for the X1/X2 measurements.
"""
from pathlib import Path
import argparse
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / 'build' / 'el2-entry'
MODEL_C = ROOT / 'compat/el2-entry-model.c'
MODEL_H = ROOT / 'compat/el2-entry-model.h'
DRIVER = ROOT / 'tools/test-el2-entry-transition.c'
NEW_FILES = [MODEL_C, MODEL_H, DRIVER, Path(__file__).resolve()]
DOC = ROOT / 'docs/SP11-EL2-ENTRY.md'
ORACLE_CASES = 12000
CFLAGS = ['-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-DSNAP_PLATFORM_SP11=1']


def compile_driver(out, model_dir=None, sanitize=True, werror=True):
    inc = Path(model_dir) if model_dir else ROOT / 'compat'
    cmd = ['cc', *CFLAGS, f'-I{inc}', f'-I{ROOT / "compat"}']
    if werror:
        cmd.append('-Werror')
    if sanitize:
        cmd += ['-fsanitize=address,undefined', '-fno-sanitize-recover=all']
    cmd += [str(DRIVER), str(inc / 'el2-entry-model.c'), '-o', str(out)]
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=300)
    if result.returncode:
        raise RuntimeError(result.stderr[-3000:])
    return out


# ---------------------------------------------------------------------------
# The independent oracle. Written from the rule descriptions in
# docs/SP11-EL2-ENTRY.md, not from the C: it shares no code with the model.
# ---------------------------------------------------------------------------
STATUS = re.findall(r'^\s*(EL2M_[A-Z0-9_]+)\b', MODEL_H.read_text().split('enum el2m_status {')[1].split('};')[0], re.M)
STATUS = [s for s in STATUS if s != 'EL2M_STATUS_COUNT']
CODE = {name: i for i, name in enumerate(STATUS)}
HCR, SCTLR, TCR, TTBR0, TTBR1, MAIR, CNTHCTL, CPTR, VBAR, NREG = range(10)
E2H, TGE, RW, M, C, I_ = 1 << 34, 1 << 27, 1 << 31, 1, 4, 1 << 12
NVHE, VHE = 0, 1
POLICY = {'strict_cfg': 1, 'strict_isb': 1}


def bits(hi, lo):
    return ((1 << (hi + 1)) - 1) & ~((1 << lo) - 1)


def eff_e2h(e2h0, hcr):
    return (hcr >> 34) & 1 if e2h0 else 1


def image_valid(reg, layout, v):
    res0 = res1 = 0
    if reg == TCR:
        if layout == NVHE:
            res0, res1 = bits(63, 34) | 1 << 19 | bits(7, 6), 1 << 31 | 1 << 23
        else:
            res0 = bits(63, 62) | 1 << 35 | 1 << 6
            if not (v >> 30) & 3:
                return False
    elif reg == TTBR0:
        res0 = bits(63, 48) if layout == NVHE else 0
    elif reg == CNTHCTL:
        res0 = bits(63, 20) | (bits(11, 8) if layout == NVHE else 0)
    elif reg == CPTR:
        if layout == NVHE:
            res0 = bits(63, 32) | bits(29, 21) | bits(19, 14) | 1 << 11
            res1 = 1 << 13 | 1 << 9 | bits(7, 0)
        else:
            res0 = bits(63, 32) | bits(27, 26) | bits(23, 22) | bits(19, 18) | bits(15, 0)
    else:
        return True
    return not (v & res0) and (v & res1) == res1


class Cpu:
    def __init__(self, e2h0, mmu, e2h, tge, identity):
        self.e2h0 = e2h0
        hcr = RW | (E2H if e2h else 0) | (TGE if tge else 0)
        self.reg = [0] * NREG
        self.reg[HCR] = hcr
        self.reg[SCTLR] = (M | C | I_) if mmu else 0
        self.layout = [VHE if eff_e2h(e2h0, hcr) else NVHE] * NREG
        self.identity = bool(identity or not mmu)
        self.dirty, self.tlbi, self.pending = True, 0, 0

    def layout_now(self):
        return VHE if eff_e2h(self.e2h0, self.reg[HCR]) else NVHE

    def mmu(self):
        return bool(self.reg[SCTLR] & M)

    def dirty_tlb(self, pol):
        if pol['strict_cfg'] or self.mmu():
            self.dirty, self.tlbi = True, 0


def step(cpu, pol, op):
    kind = op[0]
    if kind == 'M':
        _, reg, layout, v = op
        if reg >= NREG:
            return 'EL2M_E_BAD_OP'
        if reg == HCR:
            if not cpu.e2h0 and not v & E2H:
                return 'EL2M_E_E2H_NOT_PROGRAMMABLE'
            if eff_e2h(cpu.e2h0, v) != eff_e2h(cpu.e2h0, cpu.reg[HCR]):
                if cpu.mmu():
                    return 'EL2M_E_E2H_CHANGE_MMU_ON'
                cpu.dirty_tlb(pol)
        elif reg == SCTLR:
            to_on = bool(v & M)
            if to_on != cpu.mmu():
                if not cpu.identity:
                    return 'EL2M_E_MMU_TOGGLE_NOT_IDENTITY'
                if to_on:
                    translation = sum(1 << r for r in (HCR, TCR, TTBR0, TTBR1, MAIR))
                    if cpu.pending & translation:
                        return 'EL2M_E_MMU_ON_UNSYNC'
                    if cpu.dirty:
                        return 'EL2M_E_MMU_ON_STALE_TLB'
                    want = cpu.layout_now()
                    if cpu.layout[TCR] != want or cpu.layout[TTBR0] != want:
                        return 'EL2M_E_LAYOUT'
        elif reg in (TCR, TTBR0):
            if cpu.mmu():
                return 'EL2M_E_CONFIG_WRITE_MMU_ON'
            if layout != cpu.layout_now():
                return 'EL2M_E_LAYOUT'
            if not image_valid(reg, layout, v):
                return 'EL2M_E_IMAGE_INVALID'
            cpu.dirty_tlb(pol)
        elif reg in (TTBR1, MAIR):
            if cpu.mmu():
                return 'EL2M_E_CONFIG_WRITE_MMU_ON'
            cpu.dirty_tlb(pol)
        elif reg in (CNTHCTL, CPTR):
            if layout != cpu.layout_now():
                return 'EL2M_E_LAYOUT'
            if not image_valid(reg, layout, v):
                return 'EL2M_E_IMAGE_INVALID'
        cpu.reg[reg], cpu.layout[reg] = v, layout
        cpu.pending |= 1 << reg
        return None
    if kind == 'T':
        if pol['strict_isb'] and cpu.pending & (1 << HCR):
            return 'EL2M_E_TLBI_UNSYNC_HCR'
        cpu.tlbi = 1
    elif kind == 'D':
        if cpu.tlbi == 1:
            cpu.tlbi = 2
    elif kind == 'I':
        cpu.pending = 0
        if cpu.tlbi == 2:
            cpu.tlbi, cpu.dirty = 3, False
    elif kind == 'X':
        if not cpu.mmu():
            return 'EL2M_E_DATA_ACCESS_MMU_OFF'
        if cpu.pending:
            return 'EL2M_E_USE_BEFORE_SYNC'
    else:
        return 'EL2M_E_BAD_OP'
    return None


def oracle(case):
    cpu = Cpu(case['e2h0'], case['mmu'], case['e2h'], case['tge'], case['identity'])
    pre, goal, ops, pol = case['pre'], case['goal'], case['ops'], case['pol']
    cur = (int(cpu.mmu()), eff_e2h(cpu.e2h0, cpu.reg[HCR]), int(bool(cpu.reg[HCR] & TGE)))
    if any(p >= 0 and p != c for p, c in zip(pre, cur)):
        return CODE['EL2M_E_ARRIVAL_STATE'], len(ops)
    for i, op in enumerate(ops):
        s = step(cpu, pol, op)
        if s:
            return CODE[s], i
    ge, gt, gm = goal
    if (int(cpu.mmu()), eff_e2h(cpu.e2h0, cpu.reg[HCR]), int(bool(cpu.reg[HCR] & TGE))) != (gm, ge, gt):
        return CODE['EL2M_E_FINAL_STATE'], len(ops)
    want = VHE if ge else NVHE
    if any(cpu.layout[r] != want for r in (TCR, TTBR0, CNTHCTL, CPTR)):
        return CODE['EL2M_E_LAYOUT'], len(ops)
    if cpu.pending:
        return CODE['EL2M_E_USE_BEFORE_SYNC'], len(ops)
    if cpu.tlbi in (1, 2):
        return CODE['EL2M_E_FINAL_STATE'], len(ops)
    return 0, len(ops)


def encode(case):
    words = []
    for op in case['ops']:
        words.append(f'M{op[1]}:{op[2]}:{op[3]:x}' if op[0] == 'M' else op[0])
    p, g = case['pre'], case['goal']
    return (f"{case['e2h0']} {case['pol']['strict_cfg']} {case['pol']['strict_isb']} {case['mmu']} {case['e2h']} "
            f"{case['tge']} {case['identity']} | {p[0]} {p[1]} {p[2]} {g[0]} {g[1]} {g[2]} | " + ' '.join(words))


# Operation lists mirroring the C model's data, written out independently.
ON = M | C | I_
TCR_N, TCR_V, TTBR0_V, MAIR_V, CNT_N, CPTR_N = 0x80843510, 0x4b5103510, 0x80000000, 0xffbb440400, 0x6000, 0x22ff
A16_HCR = 0x88000010


def msr(reg, v, layout=NVHE):
    return ('M', reg, layout, v)


T_, D_, I__, X_ = ('T',), ('D',), ('I',), ('X',)
BOOT_OPS = [msr(SCTLR, 0), I__, msr(HCR, A16_HCR), I__, msr(MAIR, MAIR_V), msr(TCR, TCR_N), msr(TTBR0, TTBR0_V),
            msr(CNTHCTL, CNT_N), msr(CPTR, CPTR_N), D_, T_, D_, I__, msr(SCTLR, ON), I__]
SEC_OPS = BOOT_OPS[2:]
SLB_OPS = [msr(SCTLR, 0), I__, T_, D_, T_, msr(VBAR, 0x80000800), I__, msr(HCR, E2H | TGE), I__,
           msr(CNTHCTL, 3, VHE), msr(CPTR, 0x300000, VHE), I__, msr(TTBR0, TTBR0_V, VHE), I__,
           msr(TCR, TCR_V, VHE), I__, msr(MAIR, MAIR_V), I__, D_, I__, msr(SCTLR, ON), I__]
A16_OPS = [msr(VBAR, 0x80000800), msr(HCR, RW), msr(MAIR, MAIR_V), msr(TCR, TCR_N), msr(TTBR0, TTBR0_V),
           D_, T_, D_, I__, msr(SCTLR, ON), I__]
BASES = [
    (BOOT_OPS, (1, 1, 1), (0, 1, 1)), (BOOT_OPS, (-1, -1, -1), (0, 1, 1)), (SEC_OPS, (0, -1, -1), (0, 1, 1)),
    (SLB_OPS, (-1, -1, -1), (1, 1, 1)), (A16_OPS, (0, -1, -1), (0, 0, 1)), (BOOT_OPS, (-1, -1, -1), (0, 0, 1)),
]
VALUE_POOL = {
    SCTLR: [0, ON, M, C | I_, M | C, ON | 1 << 14],
    HCR: [A16_HCR, E2H | TGE, RW, E2H, TGE | RW, 0, E2H | TGE | RW],
    TCR: [TCR_N, TCR_V, 0, TCR_N & ~0x80800000, TCR_V | 1 << 62, TCR_V & ~0xc0000000, TCR_V | 0x80, 1 << 40],
    TTBR0: [TTBR0_V, TTBR0_V | 1 << 48, 0, TTBR0_V | 1],
    CNTHCTL: [CNT_N, 3, 0x6303, 0, 0x100, 1 << 40, 0x6c00],
    CPTR: [CPTR_N, 0x300000, 0, CPTR_N | 1 << 11, 0x3ff],
}


def random_op(rng):
    r = rng.random()
    if r < 0.35:
        return rng.choice([T_, D_, I__, I__, D_, X_])
    reg = rng.choice([HCR, SCTLR, TCR, TTBR0, TTBR1, MAIR, CNTHCTL, CPTR, VBAR, SCTLR, TCR, HCR, 11])
    pool = VALUE_POOL.get(reg, [0, MAIR_V, 1 << 33, 0x80000800])
    return msr(reg, rng.choice(pool) ^ (rng.choice([0, 0, 0, 1 << rng.randrange(0, 64)])), rng.choice([NVHE, VHE]))


def mutate(rng, ops):
    ops = list(ops)
    for _ in range(rng.choice([0, 1, 1, 2, 3, 5])):
        pick = rng.random()
        if pick < 0.30 and ops:
            del ops[rng.randrange(len(ops))]
        elif pick < 0.45:
            ops.insert(rng.randrange(len(ops) + 1), random_op(rng))
        elif pick < 0.55 and len(ops) > 1:
            i = rng.randrange(len(ops) - 1)
            ops[i], ops[i + 1] = ops[i + 1], ops[i]
        elif pick < 0.70 and ops:
            i = rng.randrange(len(ops))
            if ops[i][0] == 'M':
                ops[i] = ('M', ops[i][1], 1 - ops[i][2], ops[i][3])
        elif pick < 0.85 and ops:
            i = rng.randrange(len(ops))
            if ops[i][0] == 'M':
                ops[i] = ('M', ops[i][1], ops[i][2], rng.choice(VALUE_POOL.get(ops[i][1], [0, 1])))
        elif ops:
            ops[rng.randrange(len(ops))] = random_op(rng)
    return ops


def make_cases(count, seed):
    rng = random.Random(seed)
    cases = []
    for k in range(count):
        base, pre, goal = rng.choice(BASES)
        soup = rng.random() < 0.08
        ops = [random_op(rng) for _ in range(rng.randrange(0, 14))] if soup else mutate(rng, base)
        arrival = rng.choice([(1, 1, 1, 1), (0, 0, 0, 1), (0, 1, 1, 1), (0, 1, 0, 1), (0, 0, 1, 1), (1, 0, 1, 1),
                              (1, 1, 1, 0), (1, 0, 0, 1)])
        cases.append(dict(
            e2h0=int(rng.random() < 0.9), mmu=arrival[0], e2h=arrival[1], tge=arrival[2], identity=arrival[3],
            pre=pre if rng.random() < 0.85 else (-1, -1, -1), goal=goal if rng.random() < 0.9 else (rng.randrange(2),) * 3,
            ops=ops, pol=dict(strict_cfg=rng.randrange(2), strict_isb=rng.randrange(2))))
    return cases


def run_native(exe, cases):
    text = '\n'.join(encode(c) for c in cases) + '\n'
    result = subprocess.run([str(exe), '--stdin'], input=text, capture_output=True, text=True, timeout=300)
    if result.returncode:
        raise RuntimeError(result.stderr[-2000:])
    return result.stdout.splitlines()


def differential(exe, cases):
    out = run_native(exe, cases)
    if len(out) != len(cases):
        return [f'{len(out)} verdicts for {len(cases)} cases']
    bad = []
    for case, line in zip(cases, out):
        want = oracle(case)
        if line != f'{want[0]} {want[1]}':
            bad.append(f'{encode(case)} -> native {line}, oracle {want}')
            if len(bad) > 3:
                break
    return bad


# ---------------------------------------------------------------------------
# Mutants: each plausible bug, seeded into the model one at a time. The
# replaced text must occur exactly once so a refactor cannot silently turn a
# mutant into a no-op.
# ---------------------------------------------------------------------------
MUTANTS = {
    'E2H change with the MMU on accepted':
        ('if (mmu_on) return EL2M_E_E2H_CHANGE_MMU_ON;', 'if (0) return EL2M_E_E2H_CHANGE_MMU_ON;'),
    'TCR layout not compared':
        ('if (mmu_on) return EL2M_E_CONFIG_WRITE_MMU_ON;\n            if (op->layout != (uint8_t)el2m_layout_of(plat, c->reg[EL2M_HCR])) return EL2M_E_LAYOUT;',
         'if (mmu_on) return EL2M_E_CONFIG_WRITE_MMU_ON;'),
    'TLBI completes at the DSB, no ISB needed':
        ('if (c->tlbi_state == 1) c->tlbi_state = 2;', 'if (c->tlbi_state == 1) { c->tlbi_state = 3; c->tlb_dirty = 0; }'),
    'unsynchronized writes allowed at MMU on':
        ('if (regs_pending(c, TRANSLATION_REGS)) return EL2M_E_MMU_ON_UNSYNC;', ''),
    'stale TLB allowed at MMU on':
        ('if (c->tlb_dirty) return EL2M_E_MMU_ON_STALE_TLB;', ''),
    'non-VHE TCR RES1 bits not required':
        ('res1 = BIT(31) | BIT(23);', 'res1 = 0;'),
    'E2H treated as programmable without FEAT_E2H0':
        ('return plat->e2h0_implemented ? (unsigned)((hcr >> 34) & 1) : 1u;', 'return (unsigned)((hcr >> 34) & 1);'),
    'data access allowed with the MMU off':
        ('if (!mmu_on) return EL2M_E_DATA_ACCESS_MMU_OFF;', ''),
    'non-identity code allowed to toggle the MMU':
        ('if (!c->code_identity) return EL2M_E_MMU_TOGGLE_NOT_IDENTITY;', ''),
    'arrival MMU state ignored':
        ('if ((a->mmu >= 0 && (unsigned)a->mmu != mmu) ||', 'if (0 ||'),
    'final layout check skips CPTR':
        ('c->layout[EL2M_CNTHCTL] != want || c->layout[EL2M_CPTR] != want)', 'c->layout[EL2M_CNTHCTL] != want)'),
    'TLBI before an unsynchronized HCR write allowed':
        ('if (pol->strict_isb_before_tlbi && regs_pending(c, 1u << EL2M_HCR)) return EL2M_E_TLBI_UNSYNC_HCR;', ''),
    'CPU divergence in TCR not detected':
        ('a->reg[EL2M_TCR] != b->reg[EL2M_TCR] || ', ''),
    'IPS converted to the wrong PS position':
        ('o |= ((vhe >> 32) & 7) << 16;', 'o |= ((vhe >> 32) & 7) << 17;'),
    'guest EL1 name under E2H not flagged as redirected':
        ('if (a->intent == EL2M_INTENT_GUEST && e2h && cls != 0 &&', 'if (a->intent == EL2M_INTENT_GUEST && 0 && cls != 0 &&'),
    'non-VHE TTBR0 ASID allowed':
        ('if (layout == EL2M_NVHE) res0 = RANGE(63, 48);', ''),
    'MAIR write allowed with the MMU on':
        ('case EL2M_TTBR1:\n        case EL2M_MAIR:\n            if (mmu_on) return EL2M_E_CONFIG_WRITE_MMU_ON;\n            dirty_tlb(c, pol);',
         'case EL2M_TTBR1:\n        case EL2M_MAIR:\n            dirty_tlb(c, pol);'),
    'later config write does not dirty a completed TLBI':
        ('        c->tlb_dirty = 1;\n        c->tlbi_state = 0;', '        c->tlb_dirty = 1;'),
}


def mutate_model(name):
    old, new = MUTANTS[name]
    text = MODEL_C.read_text()
    if text.count(old) != 1:
        raise AssertionError(f'mutant {name!r}: pattern occurs {text.count(old)} times')
    return text.replace(old, new)


def survey():
    """Count EL1-named system register accesses in the monitor sources, classified by the model."""
    exe = BUILD / 'classify'
    BUILD.mkdir(parents=True, exist_ok=True)
    compile_driver(exe, sanitize=False)
    pattern = re.compile(r'\b(msr|mrs)\s+([A-Za-z0-9_]+)\s*,\s*([%A-Za-z0-9_\[\]]+)', re.I)
    files = sorted(set(ROOT.glob('probes/*.[chS]')) | set(ROOT.glob('probes/xnuboot/*.c')) |
                   set(ROOT.glob('compat/*.[ch]')) | set(ROOT.glob('shadow/*.[ch]')))
    hits = {}
    for path in files:
        if path.name.startswith('el2-entry-model'):
            continue
        for number, line in enumerate(path.read_text(errors='replace').splitlines(), 1):
            for m in pattern.finditer(line):
                reg = m.group(3) if m.group(1).lower() == 'mrs' else m.group(2)
                reg = reg.lower()
                if reg.endswith(('_el1', '_el0', '_el2', '_el12', '_el02')):
                    hits.setdefault(reg, []).append(f'{path.relative_to(ROOT)}:{number}')
    names = sorted({re.sub(r'_el\d+$', '', r).upper() for r in hits})
    classes = {}
    for name in names:
        out = subprocess.run([str(exe), '--class', name], capture_output=True, text=True).stdout.strip()
        classes[name] = int(out)
    return hits, classes


class OracleCheck(unittest.TestCase):
    """The oracle itself must reproduce the verdicts the brief names, so a shared misreading shows."""

    def verdict(self, ops, arrival=(1, 1, 1, 1), pre=(1, 1, 1), goal=(0, 1, 1), pol=POLICY, e2h0=1):
        case = dict(e2h0=e2h0, mmu=arrival[0], e2h=arrival[1], tge=arrival[2], identity=arrival[3], pre=pre,
                    goal=goal, ops=ops, pol=pol)
        return STATUS[oracle(case)[0]]

    def test_oracle_headline_cases(self):
        self.assertEqual(self.verdict(BOOT_OPS), 'EL2M_OK')
        self.assertEqual(self.verdict(BOOT_OPS[2:], pre=(1, 1, 1)), 'EL2M_E_E2H_CHANGE_MMU_ON')
        self.assertEqual(self.verdict(SEC_OPS, arrival=(0, 1, 1, 1), pre=(0, -1, -1)), 'EL2M_OK')
        self.assertEqual(self.verdict(BOOT_OPS, arrival=(0, 1, 1, 1)), 'EL2M_E_ARRIVAL_STATE')


class Native(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        BUILD.mkdir(parents=True, exist_ok=True)
        cls.exe = compile_driver(BUILD / 'test-el2-entry-transition')

    def test_driver_passes_under_sanitizers(self):
        result = subprocess.run([str(self.exe)], capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-2000:])
        self.assertTrue(result.stdout.rstrip().endswith('PASS'), result.stdout)
        print('driver |', result.stdout.strip().splitlines()[0], file=sys.stderr)

    def test_model_matches_independent_oracle(self):
        cases = make_cases(ORACLE_CASES, 0x5011)
        bad = differential(self.exe, cases)
        self.assertEqual(bad, [], '\n'.join(bad))
        verdicts = {}
        for c in cases:
            verdicts[STATUS[oracle(c)[0]]] = verdicts.get(STATUS[oracle(c)[0]], 0) + 1
        # Every refusal the model can give must be reached by the generated cases, or the comparison proves little.
        # CPU_DIVERGE comes from el2m_cpus_consistent, which the driver tests directly.
        missing = [s for s in STATUS if s not in verdicts and s != 'EL2M_E_CPU_DIVERGE']
        self.assertEqual(missing, [], f'unreached verdicts {missing}: {verdicts}')
        print('oracle | verdict histogram', dict(sorted(verdicts.items())), file=sys.stderr)

    def test_classification_matches_table(self):
        for name, want in {'SCTLR': 1, 'TTBR1': 1, 'CNTKCTL': 1, 'ELR': 1, 'SPSR': 1, 'CNTV_CTL': 2, 'PAR': 0, 'HCR': 0,
                           'NOT_A_REGISTER': -1}.items():
            out = subprocess.run([str(self.exe), '--class', name], capture_output=True, text=True).stdout.strip()
            self.assertEqual(int(out), want, name)

    def test_seeded_bugs_are_caught(self):
        cases = make_cases(4000, 0xbeef)
        survivors = []
        for name in MUTANTS:
            with tempfile.TemporaryDirectory(dir=BUILD) as d:
                shutil.copy(MODEL_H, d)
                shutil.copy(ROOT / 'compat/platform.h', d)
                (Path(d) / 'el2-entry-model.c').write_text(mutate_model(name))
                try:
                    exe = compile_driver(Path(d) / 'mutant', model_dir=d, sanitize=False, werror=False)
                except RuntimeError as error:
                    self.fail(f'mutant {name!r} does not compile: {error}')
                own = subprocess.run([str(exe)], capture_output=True, text=True, timeout=300)
                caught = 'driver'
                if own.returncode == 0:
                    caught = 'oracle' if differential(exe, cases) else None
                if caught is None:
                    survivors.append(name)
                print(f'mutant | {caught or "SURVIVED":7} | {name}', file=sys.stderr)
        self.assertEqual(survivors, [])
        self.assertGreaterEqual(len(MUTANTS), 12)


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
                          'fprintf', 'getenv', 'time(', 'rand(', 'socket', 'open(', 'read(', 'write(', 'memset',
                          'memcpy', 'strcmp'):
                self.assertNotIn(token, text, f'{path.name}: {token}')
            for include in re.findall(r'#include\s*[<"]([^>"]+)[>"]', text):
                self.assertIn(include, ('platform.h', 'stddef.h', 'stdint.h', 'el2-entry-model.h'), include)

    def test_gate_off_preprocesses_to_nothing(self):
        for path in (MODEL_H, MODEL_C):
            out = subprocess.run(['cc', '-E', '-P', '-I', str(ROOT / 'compat'), str(path)], capture_output=True,
                                 text=True, check=True).stdout
            body = [line for line in out.splitlines() if line.strip()]
            # Without the define only platform.h's own (declaration free) text and one typedef remain.
            self.assertTrue(all('el2m_' not in line and 'EL2M_' not in line for line in body), body[:5])
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
        self.assertNotIn('EL2M_', defined)
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
        for path in list((ROOT / 'probes').glob('run.py')) + list((ROOT / 'probes').glob('xnuboot.c')):
            text = path.read_text()
            self.assertNotIn('el2-entry-model', text, path.name)


class Survey(unittest.TestCase):
    def test_survey_finds_redirected_registers_the_monitor_touches(self):
        hits, classes = survey()
        redirected = {n for n, c in classes.items() if c == 1}
        el1 = {r for r in hits if r.endswith('_el1') and re.sub(r'_el1$', '', r).upper() in redirected}
        self.assertTrue(el1, 'the monitor accesses no redirected EL1 register: the survey is broken')
        total = sum(len(hits[r]) for r in el1)
        files = {site.split(':')[0] for r in el1 for site in hits[r]}
        print(f'survey | {total} accesses to {len(el1)} redirected EL1 registers in {len(files)} files', file=sys.stderr)


def heading_anchors(path):
    """GitHub-style anchors of the headings of a Markdown file, fenced blocks skipped."""
    seen, found, fenced = {}, set(), False
    for line in path.read_text().splitlines():
        if line.lstrip().startswith(('```', '~~~')):
            fenced = not fenced
            continue
        match = None if fenced else re.match(r'^#{1,6}\s+(.*?)\s*#*\s*$', line)
        if match:
            text = re.sub(r'`([^`]*)`', r'\1', match.group(1)).strip().lower()
            base = re.sub(r'[^\w\- ]', '', text).replace(' ', '-')
            count = seen.get(base, 0)
            seen[base] = count + 1
            found.add(base if count == 0 else f'{base}-{count}')
    return found


class Docs(unittest.TestCase):
    """docs/SP11-EL2-ENTRY.md is the record of the C1 decision; it has to stay in step with the model and its tests."""

    @classmethod
    def setUpClass(cls):
        BUILD.mkdir(parents=True, exist_ok=True)
        cls.exe = compile_driver(BUILD / 'docs-driver')

    def setUp(self):
        self.text = DOC.read_text()

    def test_every_refusal_is_explained(self):
        header = MODEL_H.read_text()
        names = set(re.findall(r'\bEL2M_E_([A-Z0-9_]+)\b', header))
        names |= set(re.findall(r'\bEL2M_ACC_([A-Z0-9_]+)\b', header)) - {'OK'}
        self.assertGreaterEqual(len(names), 19)
        missing = sorted(n for n in names if f'`{n}`' not in self.text)
        self.assertEqual(missing, [], 'refusals the document does not explain')

    def test_counts_in_the_text_match_the_tests(self):
        out = subprocess.run([str(self.exe)], capture_output=True, text=True, timeout=300).stdout
        variants = int(re.search(r'(\d+) broken variants', out).group(1))
        self.assertIn(f'runs {variants} deliberately broken sequences', self.text)
        self.assertIn(f'{ORACLE_CASES:,} generated sequences', self.text)
        self.assertIn(f'{len(MUTANTS)} seeded bugs in the model', self.text)

    def test_commands_and_decision_are_present(self):
        for command in ('python3 tools/test-el2-entry-transition.py', 'python3 tools/test-timer-offset-model.py',
                        'python3 tools/test-dc-zva-model.py', '--survey'):
            self.assertIn(command, self.text)
        for needle in ('Recommended default', 'Decision rule', 'M1', 'M5', 'E2H0'):
            self.assertIn(needle, self.text)

    def test_relative_links_resolve(self):
        for target in re.findall(r'(?<!!)\[[^\]]*\]\(([^)\s]+)\)', self.text):
            if re.match(r'[a-z]+:', target):
                continue
            name, _, anchor = target.partition('#')
            path = (DOC.parent / name) if name else DOC
            self.assertTrue(path.exists(), f'{target}: {path} does not exist')
            if anchor:
                self.assertIn(anchor, heading_anchors(path), f'{target}: no such heading')

    def test_ascii_and_nothing_machine_identifying(self):
        self.assertEqual([c for c in self.text if ord(c) > 127], [])
        # Character classes keep this file free of the literal strings it forbids elsewhere.
        personal = re.compile(r'/U[s]ers/|/h[o]me/|\b[A-Z]:\\')
        self.assertIsNone(personal.search(self.text), 'an absolute personal path in the document')
        self.assertLess(len(self.text.splitlines()), 1500)
        self.assertLess(len(self.text.encode()), 50 * 1024)
        self.assertIn('SNAP_PLATFORM_SP11', self.text)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--survey', action='store_true')
    args, rest = ap.parse_known_args()
    if args.survey:
        hits, classes = survey()
        layout_sensitive = ('tcr_el2', 'cptr_el2', 'cnthctl_el2', 'ttbr0_el2', 'sctlr_el2')
        # The monitor ELF is probes/xnuboot.{S,c} with its included files, probes/secondary.*, compat/ and shadow/
        # (the MULTI['xnuboot'] list in probes/run.py); every other probes/*.S is a standalone probe program.
        monitor = re.compile(r'^(probes/xnuboot(\.S|/)|probes/secondary\.|compat/|shadow/)')
        groups = {'EL1-named, redirected to the EL2 register under E2H=1 (needs the _EL12 alias)': [],
                  'EL0 timer, redirected to CNTHP or CNTHV under E2H=1 (needs _EL02)': [],
                  'EL2-named, layout differs with E2H': []}
        for reg in sorted(hits):
            cls = classes[re.sub(r'_el\d+$', '', reg).upper()]
            if reg.endswith('_el1') and cls == 1:
                key = 'EL1-named, redirected to the EL2 register under E2H=1 (needs the _EL12 alias)'
            elif reg.endswith('_el0') and cls == 2:
                key = 'EL0 timer, redirected to CNTHP or CNTHV under E2H=1 (needs _EL02)'
            elif reg in layout_sensitive:
                key = 'EL2-named, layout differs with E2H'
            else:
                continue
            groups[key].append((reg, hits[reg]))
        for title, rows in groups.items():
            mon = sum(1 for _, sites in rows for s in sites if monitor.match(s))
            other = sum(1 for _, sites in rows for s in sites if not monitor.match(s))
            print(f'## {title}: {mon} accesses in the monitor ELF sources, {other} in standalone probes, {len(rows)} registers')
            for reg, sites in rows:
                m = sorted({s for s in sites if monitor.match(s)})
                print(f'{reg:16} monitor {len(m):3} other {len(sites) - len(m):3}  {", ".join(m[:3])}')
        return 0
    unittest.main(argv=[sys.argv[0], *rest], verbosity=1)


if __name__ == '__main__':
    sys.exit(main())
