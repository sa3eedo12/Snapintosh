#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Stage-2 / IPA geometry model, SP11 hook and drift guard (plan item C5).

    python3 tools/test-stage2-geometry.py [--no-mutants] [unittest options]
    python3 tools/test-stage2-geometry.py --map FILE [--ipa-bits N] [--policy refuse|carve-low]
                                          [--want BYTES] [--align BYTES]
    python3 tools/test-stage2-geometry.py --emit-model 16|32|64

The default run needs a C compiler (SKIPs without one) and writes only under build/stage2geom/.
It compiles tools/test-stage2-geometry.c with compat/ipa-geometry.c and the real shadow/ stage-2
builder, then checks, in order:

  native      the C suite: the monitor-side span check against stage2_build, the planner against an
              exact 128-bit oracle and a rebuilt tree;
  scenarios   16/32/64 GiB DRAM models and deliberately faulty maps through the plan CLI, with
              the numbers worked out by hand, not read back from the model;
  geometry    root-table size and start level for every IPA width 32..48;
  freestanding  compat/ipa-geometry.c under the monitor's own flags defines only the span check and its
                PARange decoder, and needs no library;
  map parser  the map text format below, with synthetic input;
  drift       every place that encodes the 36-bit IPA / S2_TXSZ contract is tied to
              IPAG_DEFAULT_IPA_BITS, so changing one without the others fails here;
  A16 text    probes/xnuboot/stage2.c outside `#if SNAP_PLATFORM_SP11` never names the model, the
              SP11 link unit is opt-in in probes/run.py, and the default flags do not enable it;
  hook codes  the hook returns -(256 + reason); no other status of the monitor sits in that range, and
              the host's PREPARE_FAILURES names a code there only as a stage-2 refusal;
  mutants     one-line breakages of the model, the hook and the mirrored constants, each of which
              must be caught.

Evidence: the DRAM layouts (a 2 GiB low bank plus one high bank from 34 GiB) are an INFERRED model
of the 16/32/64 GiB parts, not a measurement; the real map is read at gate X2. Passing here is host
evidence only and says nothing about the hook on hardware.

Map text format (--map, --emit-model); the first statement must be the header, `#` starts a comment:

    ipa-geometry-map 1
    parange N                  PARange field of ID_AA64MMFR0_EL1, exactly once
    dram BASE SIZE             one DRAM bank, bytes, decimal or 0x hex
    mmio BASE SIZE             one device window the guest must reach
    efi TYPE START PAGES [ATTR]  one UEFI memory descriptor, START in bytes, PAGES of 4 KiB

EFI types 1, 2, 3, 4 and 7 are DRAM and 11 and 12 are MMIO; physically adjacent descriptors of the
same class merge into one window; every other type is a hole and is ignored. Windows are never
sorted: a map out of address order is passed on as it is and the planner refuses it. Exit status:
0 plan allowed, 1 plan refused, 2 unreadable map. Nothing here writes the map; a probe that dumps
one can emit this format or be converted to it.
"""
import argparse
import ast
import json
import re
import shutil
import subprocess
import sys
import unittest
from itertools import chain
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / 'build' / 'stage2geom'
NATIVE_SOURCE = 'tools/test-stage2-geometry.c'
GEOMETRY_C = 'compat/ipa-geometry.c'
GEOMETRY_H = 'compat/ipa-geometry.h'
LEGACY_SOURCES = ('shadow/mmu.c', 'shadow/shadow.c', 'shadow/stage2.c')
STAGE2_C = 'probes/xnuboot/stage2.c'
LLVM_DIRS = ('/opt/homebrew/opt/llvm/bin', '/usr/local/opt/llvm/bin')
MONITOR_FLAGS = ['--target=aarch64-unknown-none-elf', '-ffreestanding', '-fno-pic', '-mgeneral-regs-only', '-O1',
                 '-Wall', '-Wextra', '-Werror', '-fno-stack-protector', '-mcmodel=tiny']

GIB = 1 << 30
LOW_BASE = 0x80000000
HIGH_BASE = 0x880000000
MODEL_MMIO = ((0x17000000, 0x400000), (0x70000000, 0x200000))
MODEL_HIGH_GIB = {16: 14, 32: 30, 64: 62}

# The reason numbering is part of the contract between the monitor hook (it returns
# -(IPAG_HOOK_RETURN_BASE + reason)) and the host tools that decode it.
REASONS = ['OK', 'NULL_INPUT', 'BAD_IPA_BITS', 'BAD_PARANGE', 'BAD_POLICY', 'NO_WINDOWS', 'TOO_MANY_WINDOWS',
           'ZERO_SIZE', 'INVERTED', 'OVERFLOW', 'UNALIGNED', 'UNSORTED', 'OVERLAP', 'MMIO_IN_DRAM',
           'ABOVE_PARANGE', 'WINDOW_ABOVE_LIMIT', 'WINDOW_CROSSES_LIMIT', 'CARVE_UNALIGNED', 'CARVE_NO_FIT']
EXPECTED_DEFAULTS = {'default_ipa_bits': 36, 'default_carve_align': 0x2000000, 'max_windows': 64,
                     'hook_return_base': 256, 'reason_count': len(REASONS)}

NO_MUTANTS = False


def find_tool(*names):
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    for name in names:
        for directory in LLVM_DIRS:
            candidate = Path(directory) / name
            if candidate.is_file():
                return str(candidate)
    return None


def run(command, timeout=180):
    return subprocess.run([str(part) for part in command], capture_output=True, text=True, timeout=timeout, cwd=ROOT)


def build_native(out, geometry_dir=None, sanitize=True, reuse=False):
    """Compile the native suite. geometry_dir holds the ipa-geometry.{c,h} under test."""
    geometry_dir = Path(geometry_dir or ROOT / 'compat')
    sources = [ROOT / NATIVE_SOURCE, geometry_dir / 'ipa-geometry.c', geometry_dir / 'ipa-geometry.h',
               *(ROOT / rel for rel in LEGACY_SOURCES), *sorted((ROOT / 'shadow').glob('*.h'))]
    out.parent.mkdir(parents=True, exist_ok=True)
    if reuse and out.exists() and all(out.stat().st_mtime >= source.stat().st_mtime for source in sources):
        return out
    cc = find_tool('cc', 'clang', 'gcc')
    if not cc:
        raise unittest.SkipTest('no C compiler found')
    flags = ['-O1', '-g', '-fsanitize=address,undefined'] if sanitize else ['-O0']
    proc = run([cc, '-std=gnu11', '-Wall', '-Wextra', *flags, '-I', geometry_dir, '-I', ROOT / 'shadow', '-I',
                ROOT / 'compat', sources[0], sources[1], *(ROOT / rel for rel in LEGACY_SOURCES), '-o', out])
    if proc.returncode:
        raise RuntimeError(f'native build failed:\n{proc.stdout}{proc.stderr}')
    return out


def native_summary(proc):
    found = re.search(r'stage2-geometry native: (\d+) checks, (\d+) failures', proc.stdout)
    return (int(found.group(1)), int(found.group(2))) if found else None


def plan(binary, args):
    proc = run([binary, '--plan', *args], timeout=60)
    if proc.returncode:
        raise RuntimeError(f'--plan exited {proc.returncode}: {proc.stderr.strip()}')
    return dict(line.split('=', 1) for line in proc.stdout.splitlines() if '=' in line)


def defaults(binary):
    proc = run([binary, '--defaults'], timeout=60)
    if proc.returncode:
        raise RuntimeError(f'--defaults exited {proc.returncode}')
    return dict(line.split('=', 1) for line in proc.stdout.splitlines() if '=' in line)


def compare(got, expect):
    problems = []
    for key, want in expect.items():
        have = got.get(key)
        if isinstance(want, int):
            try:
                have = int(have, 0)
            except (TypeError, ValueError):
                pass
        if have != want:
            problems.append(f'{key} = {have!r}, expected {want!r}')
    return problems


# ---- scenarios -------------------------------------------------------------------------------------------------

def dram(base, size):
    return ('--dram', f'{base:#x}:{size:#x}')


LOW = dram(LOW_BASE, 2 * GIB)
MMIO = ('--mmio', '0x17000000:0x400000', '--mmio', '0x70000000:0x200000')


def sku(gib):
    return (*LOW, *dram(HIGH_BASE, MODEL_HIGH_GIB[gib] * GIB), *MMIO)


def allowed(carve_gib, carve_base=HIGH_BASE, **more):
    """A plan that maps `carve_gib` of guest RAM in 1 GiB blocks plus the two MMIO windows in 2 MiB blocks.

    Hand count at IPA 36: the carve-out is 1 GiB aligned, so it is one level-1 block per GiB; the GIC window
    (4 MiB) is two level-2 blocks and the PCIe window (2 MiB) one, in two different 1 GiB slots, so two
    level-2 tables hang off the 64-entry root.
    """
    return dict(result='ALLOW', reason='OK', code=0, bad_index=-1, excess=0, carve_base=carve_base,
                carve_size=carve_gib * GIB, start_level=1, root_entries=64, leaves_1g=carve_gib, leaves_2m=3,
                leaves_4k=0, table_pages=2, **more)


def refused(reason, **more):
    return dict(result='REFUSE', reason=reason, code=REASONS.index(reason), **more)


# Hand derivation of the three SKU models (INFERRED layout, see compat/ipa-geometry.h): a 2 GiB low bank
# [0x80000000, 0x100000000) and one high bank from 0x880000000 (34 GiB) of 14, 30 or 62 GiB. The limit is
# 1 << 36 = 64 GiB = 0x1000000000.
#   16 GiB  the high bank ends at 34 + 14 = 48 GiB = 0xc00000000, below the limit: ALLOW. Guest RAM is the largest
#           aligned span, the whole high bank: base 0x880000000, 14 GiB = 0x380000000, 14 level-1 blocks.
#   32 GiB  34 + 30 = 64 GiB ends exactly on the limit, the last mappable address: ALLOW with 30 GiB = 0x780000000.
#   64 GiB  34 + 62 = 96 GiB = 0x1800000000 is 32 GiB = 0x800000000 past the limit: REFUSE (WINDOW_CROSSES_LIMIT on
#           bank 1). Carve-low keeps the 30 GiB below the limit and reports the 32 GiB it gave up. A 37-bit IPA is
#           128 GiB: the part fits, the root grows to 128 entries and all 62 GiB are carved.
SCENARIOS = [
    ('16 GiB: both banks lie below 1 << 36', sku(16),
     allowed(14, dram_total=16 * GIB, dram_usable=16 * GIB, dram_dropped=0, trimmed_windows=0)),
    ('32 GiB: the high bank ends exactly on 1 << 36', sku(32),
     allowed(30, dram_total=32 * GIB, dram_usable=32 * GIB, dram_dropped=0, trimmed_windows=0)),
    ('64 GiB: the high bank crosses 1 << 36, refused by default', sku(64),
     refused('WINDOW_CROSSES_LIMIT', bad_index=1, excess=32 * GIB, dram_total=64 * GIB, dram_usable=32 * GIB,
             dram_dropped=32 * GIB, trimmed_windows=1)),
    ('64 GiB: carve-low gives up the 32 GiB above the limit', (*sku(64), '--policy', 'carve-low'),
     allowed(30, dram_total=64 * GIB, dram_usable=32 * GIB, dram_dropped=32 * GIB, trimmed_windows=1)),
    ('64 GiB: a 37-bit IPA holds it, with a 128-entry root', (*sku(64), '--ipa-bits', '37'),
     dict(allowed(62), ipa_limit=1 << 37, root_entries=128, dram_total=64 * GIB, dram_usable=64 * GIB,
          dram_dropped=0, trimmed_windows=0)),
    ('64 GiB in three banks: the third is wholly above the limit',
     (*LOW, *dram(HIGH_BASE, 30 * GIB), *dram(1 << 36, 32 * GIB), *MMIO),
     refused('WINDOW_ABOVE_LIMIT', bad_index=2, excess=32 * GIB)),
    ('64 GiB in three banks: the second crosses, the third is above',
     (*LOW, *dram(HIGH_BASE, 38 * GIB), *dram(0x1200000000, 24 * GIB), *MMIO),
     refused('WINDOW_CROSSES_LIMIT', bad_index=1, excess=8 * GIB, dram_dropped=32 * GIB, trimmed_windows=2)),
    ('a guest RAM size larger than any bank', (*sku(16), '--want', hex(20 * GIB)), refused('CARVE_NO_FIT')),
    ('a carve-low guest RAM size above the 30 GiB that is addressable',
     (*sku(64), '--policy', 'carve-low', '--want', hex(40 * GIB)), refused('CARVE_NO_FIT')),
    ('a 2 GiB guest RAM takes the lowest bank that holds it', (*sku(16), '--want', hex(2 * GIB)),
     dict(result='ALLOW', carve_base=LOW_BASE, carve_size=2 * GIB)),
    ('a bank ending exactly on the limit is not "crossing"', dram(0xff0000000, 0x10000000),
     dict(result='ALLOW', carve_base=0xff0000000, carve_size=0x10000000)),
    ('a bank straddling the limit is refused', dram(0xff8000000, 0x10000000),
     refused('WINDOW_CROSSES_LIMIT', bad_index=0, excess=0x8000000, dram_dropped=0x8000000)),
    ('a bank straddling the limit under carve-low keeps the part below',
     (*dram(0xff8000000, 0x10000000), '--policy', 'carve-low', '--align', '0x200000'),
     dict(result='ALLOW', carve_base=0xff8000000, carve_size=0x8000000, dram_dropped=0x8000000)),
    ('MMIO above the limit is refused even under carve-low',
     (*LOW, '--mmio', '0x1000000000:0x1000', '--policy', 'carve-low'),
     refused('WINDOW_ABOVE_LIMIT', bad_index=0, bad_is_mmio=1, excess=0x1000)),
    ('MMIO crossing the limit is refused',
     ('--ipa-bits', '32', '--dram', '0x80000000:0x40000000', '--mmio', '0xfffff000:0x2000'),
     refused('WINDOW_CROSSES_LIMIT', bad_index=0, bad_is_mmio=1, excess=0x1000)),
    ('PARange 6 is read as 48 bits like the monitor does', ('--parange', '6', *LOW),
     dict(result='ALLOW', pa_bits=48)),
]

FAULTS = [
    ('overlapping banks', (*LOW, *dram(0xc0000000, 0x40000000)), 'OVERLAP', 1),
    ('zero-size bank', dram(LOW_BASE, 0), 'ZERO_SIZE', 0),
    ('base + size wraps 2^64', dram(0xfffffffffffff000, 0x2000), 'OVERFLOW', 0),
    ('bank beyond PARange', ('--ipa-bits', '40', '--parange', '1', *LOW, *dram(1 << 36, 0x1000)),
     'ABOVE_PARANGE', 1),
    ('PARange 0 with a bank above 4 GiB', ('--parange', '0', *LOW, *dram(1 << 32, 0x1000)), 'ABOVE_PARANGE', 1),
    ('bank on the 44-bit PARange limit under a 48-bit IPA',
     ('--ipa-bits', '48', '--parange', '4', *LOW, *dram(1 << 44, 0x1000)), 'ABOVE_PARANGE', 1),
    ('unaligned bank', dram(0x80000800, 2 * GIB), 'UNALIGNED', 0),
    ('MMIO inside DRAM', (*LOW, '--mmio', '0x90000000:0x1000'), 'MMIO_IN_DRAM', 0),
    ('descending banks', (*dram(HIGH_BASE, 14 * GIB), *LOW), 'UNSORTED', 1),
    ('IPA width below 32', ('--ipa-bits', '31', *LOW), 'BAD_IPA_BITS', -1),
    ('IPA width above 48', ('--ipa-bits', '49', *LOW), 'BAD_IPA_BITS', -1),
    ('reserved PARange encoding', ('--parange', '7', *LOW), 'BAD_PARANGE', -1),
    ('unknown policy', ('--policy', 'invalid', *LOW), 'BAD_POLICY', -1),
    ('no DRAM at all', (), 'NO_WINDOWS', -1),
    ('65 banks', tuple(chain.from_iterable(dram(LOW_BASE + n * 0x2000, 0x1000) for n in range(65))),
     'TOO_MANY_WINDOWS', -1),
    ('64 banks pass the structure checks but none holds 32 MiB',
     tuple(chain.from_iterable(dram(LOW_BASE + n * 0x2000, 0x1000) for n in range(64))), 'CARVE_NO_FIT', -1),
    ('carve alignment not a power of two', (*LOW, '--align', '0x3000'), 'CARVE_UNALIGNED', -1),
    ('carve alignment below 2 MiB', (*LOW, '--align', '0x1000'), 'CARVE_UNALIGNED', -1),
    ('carve alignment above 1 GiB', (*LOW, '--align', '0x80000000'), 'CARVE_UNALIGNED', -1),
    ('guest RAM size not a multiple of the alignment', (*LOW, '--want', '0x1000'), 'CARVE_UNALIGNED', -1),
]


def scenario_failures(binary):
    problems = []
    cases = list(SCENARIOS)
    cases += [(name, args, refused(reason, bad_index=index)) for name, args, reason, index in FAULTS]
    for name, args, expect in cases:
        try:
            got = plan(binary, args)
        except (RuntimeError, OSError) as exc:
            problems.append(f'{name}: {exc}')
            continue
        problems += [f'{name}: {problem}' for problem in compare(got, expect)]
    return problems


def width_failures(binary):
    """Root-table geometry at every IPA width, from the 4 KiB-granule walk arithmetic."""
    problems = []
    for bits in range(32, 49):
        levels = (bits - 12 + 8) // 9
        expect = dict(result='ALLOW', start_level=4 - levels, root_entries=1 << (bits - 12 - 9 * (levels - 1)),
                      leaves_1g=2, table_pages=0 if levels == 3 else 1, ipa_limit=1 << bits)
        try:
            got = plan(binary, ('--ipa-bits', str(bits), '--parange', '5', *LOW))
        except RuntimeError as exc:
            problems.append(f'IPA {bits}: {exc}')
            continue
        problems += [f'IPA {bits}: {problem}' for problem in compare(got, expect)]
    return problems


# ---- map text --------------------------------------------------------------------------------------------------

class MapError(ValueError):
    pass


U64_MAX = (1 << 64) - 1
NUMBER = re.compile(r'(?:0[xX][0-9a-fA-F]+|0|[1-9][0-9]*)\Z')
EFI_PAGE = 4096
EFI_DRAM_TYPES = frozenset({1, 2, 3, 4, 7})
EFI_MMIO_TYPES = frozenset({11, 12})
MAX_MAP_WINDOWS = 65


def parse_number(token, what, line_no, limit=U64_MAX):
    if not NUMBER.match(token):
        raise MapError(f'line {line_no}: {what} {token!r} is not a decimal or 0x-hex number')
    value = int(token, 0)
    if value > limit:
        raise MapError(f'line {line_no}: {what} {token} is out of range')
    return value


def add_efi_window(windows, base, size):
    if windows and windows[-1][2] and windows[-1][0] + windows[-1][1] == base:
        windows[-1][1] += size
    else:
        windows.append([base, size, True])


def parse_map(text):
    """Returns (parange, [(base, size)] dram, [(base, size)] mmio); raises MapError on anything unclear."""
    parange, saw_header = None, False
    dram_windows, mmio_windows = [], []
    for line_no, raw in enumerate(text.splitlines(), 1):
        tokens = raw.split('#', 1)[0].split()
        if not tokens:
            continue
        if not saw_header:
            if tokens != ['ipa-geometry-map', '1']:
                raise MapError(f'line {line_no}: the first statement must be "ipa-geometry-map 1"')
            saw_header = True
            continue
        key, args = tokens[0], tokens[1:]
        if key == 'parange' and len(args) == 1:
            if parange is not None:
                raise MapError(f'line {line_no}: parange given twice')
            parange = parse_number(args[0], 'parange', line_no, 0xffffffff)
        elif key in ('dram', 'mmio') and len(args) == 2:
            window = [parse_number(args[0], f'{key} base', line_no), parse_number(args[1], f'{key} size', line_no), False]
            (dram_windows if key == 'dram' else mmio_windows).append(window)
        elif key == 'efi' and len(args) in (3, 4):
            kind = parse_number(args[0], 'efi type', line_no, 0xffffffff)
            start = parse_number(args[1], 'efi physical start', line_no)
            pages = parse_number(args[2], 'efi page count', line_no)
            if len(args) == 4:
                parse_number(args[3], 'efi attribute', line_no)
            size = pages * EFI_PAGE
            if not pages or size > U64_MAX or start + size > 1 << 64:
                raise MapError(f'line {line_no}: efi descriptor is empty or runs past 2^64')
            if kind in EFI_DRAM_TYPES:
                add_efi_window(dram_windows, start, size)
            elif kind in EFI_MMIO_TYPES:
                add_efi_window(mmio_windows, start, size)
        elif key in ('parange', 'dram', 'mmio', 'efi'):
            raise MapError(f'line {line_no}: wrong number of operands for {key}')
        else:
            raise MapError(f'line {line_no}: unknown statement {key!r}')
    if not saw_header:
        raise MapError('no "ipa-geometry-map 1" header')
    if parange is None:
        raise MapError('no parange statement')
    for name, windows in (('dram', dram_windows), ('mmio', mmio_windows)):
        if len(windows) > MAX_MAP_WINDOWS or any(w[1] > U64_MAX for w in windows):
            raise MapError(f'too many or too large {name} windows')
    return parange, [(w[0], w[1]) for w in dram_windows], [(w[0], w[1]) for w in mmio_windows]


def model_text(gib):
    lines = ['ipa-geometry-map 1',
             f'# MODEL (INFERRED), {gib} GiB part: a 2 GiB low bank and one high bank from 34 GiB.',
             '# The measured map replaces this at gate X2.',
             'parange 4',
             f'dram {LOW_BASE:#x} {2 * GIB:#x}',
             f'dram {HIGH_BASE:#x} {MODEL_HIGH_GIB[gib] * GIB:#x}']
    lines += [f'mmio {base:#x} {size:#x}' for base, size in MODEL_MMIO]
    return '\n'.join(lines) + '\n'


def plan_args(model, ipa_bits=None, policy=None, want=None, align=None):
    parange, dram_windows, mmio_windows = model
    args = ['--parange', str(parange)]
    for flag, value in (('--ipa-bits', ipa_bits), ('--policy', policy), ('--want', want), ('--align', align)):
        if value is not None:
            args += [flag, str(value)]
    for flag, windows in (('--dram', dram_windows), ('--mmio', mmio_windows)):
        for base, size in windows:
            args += [flag, f'{base:#x}:{size:#x}']
    return args


def map_mode(options):
    try:
        model = parse_map(Path(options.map).read_text(encoding='utf-8'))
        got = plan(build_native(BUILD / 'native', sanitize=True, reuse=True),
                   plan_args(model, options.ipa_bits, options.policy, options.want, options.align))
    except (OSError, MapError, RuntimeError, unittest.SkipTest) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2
    for key, value in got.items():
        print(f'{key}={value}')
    return 0 if got.get('result') == 'ALLOW' else 1


# ---- source drift ----------------------------------------------------------------------------------------------

class Tree:
    """A source tree, optionally with some files replaced by text, so a check can run on a broken copy."""

    def __init__(self, root=ROOT, overrides=None):
        self.root = Path(root)
        self.overrides = dict(overrides or {})

    def text(self, rel):
        if rel in self.overrides:
            return self.overrides[rel]
        return (self.root / rel).read_text(encoding='utf-8', errors='replace')

    def mutate(self, rel, old, new):
        text = self.text(rel)
        if text.count(old) != 1:
            raise AssertionError(f'{rel}: mutation anchor found {text.count(old)} times: {old!r}')
        return Tree(self.root, {**self.overrides, rel: text.replace(old, new)})

    def sources(self, directories, suffixes):
        found = {path.relative_to(self.root).as_posix() for directory in directories
                 for path in (self.root / directory).rglob('*') if path.suffix in suffixes and path.is_file()}
        return sorted(found | {rel for rel in self.overrides if Path(rel).suffix in suffixes})


def line_of(text, index):
    return text.count('\n', 0, index) + 1


NON_CODE = re.compile(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'', re.S)


def blank_comments_and_strings(text):
    """Blank what the compiler does not execute, keeping every newline, so a message that says "1<<36" is not a site."""
    return NON_CODE.sub(lambda m: re.sub(r'[^\n]', ' ', m.group(0)), text)


SHIFT = re.compile(r'(?<![\w.])1(?:[uU][lL]{0,2}|[lL]{1,2}[uU]?)?\s*<<\s*(\d+)\b')

# Sites that shift by the IPA width in the emulated T8140 guest contract. They are the same width on the SP11
# (the guest is the same XNU), which is why the hook leaves them alone, but each one is pinned so that a width
# change cannot reach some of them and miss the others. TCR_EL1.AS (bit 36, a guest_tcr assignment) is an
# architectural bit position and is skipped. compat/nvme-host.h is not a guest contract: its one site is the SP11
# NVMe window's copy of the monitor's stage-2 limit (NVMEH_SP11_IPA_LIMIT), pinned here so that it follows the width.
GUEST_WIDTH_SITES = {
    'compat/dart.c': 2,
    'compat/sptm-dart.c': 1,
    'compat/device/device_core.c': 3,
    'compat/sptm.c': 2,
    'compat/sptm.h': 1,
    'compat/txm-native.c': 1,
    'compat/nvme-host.h': 1,
}
S2_TXSZ_FILES = ('probes/xnuboot/stage2.c', 'probes/shadowmmu.c')
HOST_MIRRORS = (
    ('probes/xnuboot.py', r'^STAGE2_IPA_BITS\s*=\s*(\d+)\b', 'STAGE2_IPA_BITS'),
    ('probes/monitor_work.py', r'^IPA_LIMIT\s*=\s*1\s*<<\s*(\d+)\b', 'IPA_LIMIT'),
    ('tools/stage2audit.py', r'hi\s*<=\s*1\s*<<\s*(\d+)\b', 'the stage-2 range limit'),
)
SL0_FILES = ('probes/shadowmmu.c', 'probes/xnuboot/prepare.c')
# The opt-in SNAP_S2_FAULT_RETRY helper repeats the programmed VTCR and walks the tree from level 1 for T0SZ 25..33.
RETRY_HEADER = 'compat/s2-fault-retry.h'


def level_for_txsz(txsz):
    return 4 - (64 - txsz - 12 + 8) // 9


def drift_failures(tree):
    problems = []
    found = re.search(r'#\s*define\s+IPAG_DEFAULT_IPA_BITS\s+(\d+)u?\b', tree.text(GEOMETRY_H))
    if not found:
        return [f'{GEOMETRY_H}: IPAG_DEFAULT_IPA_BITS not found']
    bits = int(found.group(1))

    for rel in S2_TXSZ_FILES:
        text = tree.text(rel)
        values = [int(v) for v in re.findall(r'^[ \t]*#[ \t]*define[ \t]+S2_TXSZ[ \t]+(\d+)u?\b', text, re.M)]
        if len(values) != 1:
            problems.append(f'{rel}: expected one S2_TXSZ definition, found {len(values)}')
        elif 64 - values[0] != bits:
            problems.append(f'{rel}: S2_TXSZ {values[0]} is a {64 - values[0]}-bit IPA, the model says {bits}')

    for rel, pattern, what in HOST_MIRRORS:
        values = [int(v) for v in re.findall(pattern, tree.text(rel), re.M)]
        if len(values) != 1:
            problems.append(f'{rel}: expected one definition of {what}, found {len(values)}')
        elif values[0] != bits:
            problems.append(f'{rel}: {what} is {values[0]} bits, the model says {bits}')

    seen = {}
    for rel in tree.sources(('compat', 'probes', 'shadow'), {'.c', '.h'}):
        if rel in (GEOMETRY_C, GEOMETRY_H):
            continue
        text = tree.text(rel)
        lines = text.split('\n')
        code = blank_comments_and_strings(text)
        for match in SHIFT.finditer(code):
            line = line_of(code, match.start())
            if int(match.group(1)) == bits and 'guest_tcr' not in lines[line - 1]:
                seen.setdefault(rel, []).append(line)
    for rel in sorted(set(seen) | set(GUEST_WIDTH_SITES)):
        want, have = GUEST_WIDTH_SITES.get(rel, 0), seen.get(rel, [])
        if len(have) != want:
            where = ', '.join(f'{rel}:{n}' for n in have) or 'none'
            problems.append(f'{rel}: {len(have)} shift(s) by {bits}, audited inventory has {want} ({where})')

    start_level = 4 - (bits - 12 + 8) // 9
    for rel in SL0_FILES:
        if not re.search(r'S2_TXSZ\s*\|\s*\(1UL\s*<<\s*6\)', tree.text(rel)):
            problems.append(f'{rel}: the hard-coded VTCR SL0 = 1 was not found; re-audit the start level')
        elif start_level != 1:
            problems.append(f'{rel}: VTCR SL0 is hard-coded to level 1 but a {bits}-bit IPA starts at {start_level}')

    retry = tree.text(RETRY_HEADER)
    if not re.search(r'txsz\s*\|\s*\(UINT64_C\(1\)\s*<<\s*6\)', retry):
        problems.append(f'{RETRY_HEADER}: the hard-coded VTCR SL0 = 1 was not found; re-audit the start level')
    window = re.search(r'txsz\s*<\s*(\d+)\s*\|\|\s*txsz\s*>\s*(\d+)', retry)
    if not window:
        problems.append(f'{RETRY_HEADER}: the T0SZ window of s2_retry_walk was not found')
    else:
        low, high = int(window.group(1)), int(window.group(2))
        if not low <= 64 - bits <= high:
            problems.append(f'{RETRY_HEADER}: s2_retry_walk accepts T0SZ {low}..{high}, the model says {64 - bits}')
        problems += [f'{RETRY_HEADER}: T0SZ {edge} at the edge of the s2_retry_walk window does not start at level 1'
                     for edge in (low, high) if level_for_txsz(edge) != 1]
    return problems


DIRECTIVE = re.compile(r'\s*#\s*(ifdef|ifndef|if|elif|else|endif)\b(.*)')


def split_gated(text):
    """(inside, outside) as lists of (line number, text); inside is the positive branch of `#if SNAP_PLATFORM_SP11`."""
    inside, outside, stack = [], [], []
    for number, line in enumerate(text.split('\n'), 1):
        found = DIRECTIVE.match(line)
        if found:
            keyword, condition = found.group(1), re.sub(r'/\*.*?\*/|//.*', '', found.group(2)).strip()
            if keyword in ('if', 'ifdef', 'ifndef'):
                stack.append('sp11' if keyword == 'if' and condition == 'SNAP_PLATFORM_SP11' else 'other')
            elif keyword in ('elif', 'else') and stack and stack[-1] == 'sp11':
                stack[-1] = 'a16'
                outside.append((number, line))
                continue
            (inside if 'sp11' in stack else outside).append((number, line))
            if keyword == 'endif' and stack:
                stack.pop()
            continue
        (inside if 'sp11' in stack else outside).append((number, line))
    return inside, outside


HOOK_SHAPE = (
    (r'#\s*include\s+"\.\./\.\./compat/ipa-geometry\.h"', 'the include of compat/ipa-geometry.h'),
    (r'id_aa64mmfr0_el1', 'the PARange read'),
    (r'_Static_assert\s*\(\s*sizeof\(ranges\)\s*/\s*sizeof\(ranges\[0\]\)\s*<=\s*IPAG_MAX_WINDOWS', 'the list-length assertion'),
    (r'ipag_check_spans\s*\(\s*spans\s*,\s*count\s*,\s*64u\s*-\s*S2_TXSZ\s*,\s*\(unsigned\)\s*\(\s*parange\s*&\s*0xf\s*\)',
     'the span check against 64 - S2_TXSZ and the read PARange'),
    (r'if\s*\(\s*why\s*\)\s*return\s*-\s*\(\s*IPAG_HOOK_RETURN_BASE\s*\+\s*why\s*\)\s*;', 'the refusal return'),
)


def a16_text_failures(tree):
    text = tree.text(STAGE2_C)
    inside, outside = split_gated(text)
    problems = [f'{STAGE2_C}:{number}: names the SP11 geometry outside #if SNAP_PLATFORM_SP11: {line.strip()}'
                for number, line in outside if re.search(r'ipag_|IPAG_|ipa-geometry', line)]
    gated = '\n'.join(line for _, line in inside)
    problems += [f'{STAGE2_C}: gated block lacks {what}' for pattern, what in HOOK_SHAPE if not re.search(pattern, gated)]
    call, build = text.find('ipag_check_spans('), text.find('int built = stage2_build(&s2, ranges, count);')
    if build < 0 or not 0 <= call < build:
        problems.append(f'{STAGE2_C}: the span check must come before stage2_build')

    try:
        module = ast.parse(tree.text('probes/run.py'))
        optional = next((ast.literal_eval(node.value) for node in module.body if isinstance(node, ast.Assign)
                         and any(isinstance(t, ast.Name) and t.id == 'OPTIONAL' for t in node.targets)), None)
    except (SyntaxError, ValueError) as exc:
        return problems + [f'probes/run.py: OPTIONAL cannot be read as a literal ({exc})']
    if optional is None or ('-DSNAP_PLATFORM_SP11=1', GEOMETRY_C) not in tuple(optional.get('xnuboot', ())):
        problems.append(f"probes/run.py: OPTIONAL['xnuboot'] lacks ('-DSNAP_PLATFORM_SP11=1', '{GEOMETRY_C}')")

    flags = json.loads(tree.text('config/monitor-flags.json')).get('flags', [])
    problems += [f'config/monitor-flags.json: default flag {flag} turns the SP11 hook on' for flag in flags
                 if 'SNAP_PLATFORM_SP11' in flag]
    return problems


def return_code_failures(tree, hook_base, reason_count):
    """The hook returns -(hook_base + reason) for reason 1 .. reason_count - 1. No other status the monitor
    returns may sit in [hook_base, hook_base + reason_count), and the host's PREPARE_FAILURES may name a code
    there only as a stage-2 refusal, so a number in that range always decodes to the hook's reason."""
    problems = []
    in_range = range(hook_base, hook_base + reason_count)
    for rel in ['probes/xnuboot.c', *tree.sources(('probes/xnuboot',), {'.c'})]:
        for match in re.finditer(r'\breturn\s*-\s*(\d+)\s*;', blank_comments_and_strings(tree.text(rel))):
            if int(match.group(1)) in in_range:
                problems.append(f'{rel}: return -{match.group(1)} collides with the hook range')
    module = ast.parse(tree.text('probes/run.py'))
    for node in module.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'PREPARE_FAILURES' for t in node.targets):
            for key, value in zip(node.value.keys, node.value.values):
                if key is None:
                    continue
                code = ast.literal_eval(key)
                reason = -code - hook_base
                text = value.value if isinstance(value, ast.Constant) and isinstance(value.value, str) else ''
                if -code in in_range and not (0 < reason and 'stage-2' in text.lower()):
                    problems.append(f'probes/run.py: PREPARE_FAILURES names {code} inside the hook range '
                                    'without describing a stage-2 refusal')
    return problems


# ---- mutants ---------------------------------------------------------------------------------------------------

GEOMETRY_MUTANTS = [
    ('span-limit-inclusive', GEOMETRY_C, 'if (hi > ipa_limit) return IPAG_WINDOW_CROSSES_LIMIT;',
     'if (hi >= ipa_limit) return IPAG_WINDOW_CROSSES_LIMIT;'),
    ('span-ignores-parange', GEOMETRY_C, 'if (hi > pa_limit) return IPAG_ABOVE_PARANGE;',
     'if (hi > ~(uint64_t)0) return IPAG_ABOVE_PARANGE;'),
    ('span-parange-inclusive', GEOMETRY_C, 'if (hi > pa_limit) return IPAG_ABOVE_PARANGE;',
     'if (hi >= pa_limit) return IPAG_ABOVE_PARANGE;'),
    ('span-skips-overlap', GEOMETRY_C, 'spans[n].lo < spans[m].hi && spans[m].lo < spans[n].hi', '0'),
    ('plan-limit-inclusive', GEOMETRY_C, 'if (end <= limit) return IPAG_OK;', 'if (end < limit) return IPAG_OK;'),
    ('plan-ignores-parange', GEOMETRY_C, '} else if (base + size > pa_limit) {', '} else if (0) {'),
    ('plan-parange-inclusive', GEOMETRY_C, '} else if (base + size > pa_limit) {',
     '} else if (base + size >= pa_limit) {'),
    ('plan-drops-overflow-guard', GEOMETRY_C, '} else if (size > UINT64_MAX - base) {', '} else if (0) {'),
    ('plan-accepts-unsorted', GEOMETRY_C, 'if (base < w[n - 1].base) why = IPAG_UNSORTED;', 'if (0) why = IPAG_UNSORTED;'),
    ('plan-refuse-acts-as-carve', GEOMETRY_C, 'if (in->policy == IPAG_POLICY_REFUSE)', 'if (0)'),
    ('plan-mmio-in-dram-blind', GEOMETRY_C, 'if (in->mmio[m].base < in->dram[d].base + in->dram[d].size &&',
     'if (0 && in->mmio[m].base < in->dram[d].base + in->dram[d].size &&'),
    ('plan-carves-highest-fit', GEOMETRY_C, 'if (!found && hi - lo >= in->want_bytes) {',
     'if (hi - lo >= in->want_bytes) {'),
    ('parange-4-as-48-bits', GEOMETRY_C, 'case 4: return 44;', 'case 4: return 48;'),
    ('default-width-37', GEOMETRY_H, '#define IPAG_DEFAULT_IPA_BITS 36u', '#define IPAG_DEFAULT_IPA_BITS 37u'),
]

DRIFT_MUTANTS = [
    ('stage2-txsz-27', [(STAGE2_C, 'S2_TXSZ    28u', 'S2_TXSZ    27u')]),
    ('shadowmmu-txsz-27', [('probes/shadowmmu.c', '#define S2_TXSZ 28u', '#define S2_TXSZ 27u')]),
    ('dart-pa-limit-37', [('compat/dart.c', '#define PA_LIMIT (1UL<<36)', '#define PA_LIMIT (1UL<<37)')]),
    ('sptm-dart-pa-limit-37', [('compat/sptm-dart.c', '#define PA_LIMIT (1UL<<36)', '#define PA_LIMIT (1UL<<37)')]),
    ('device-core-window-37', [('compat/device/device_core.c', 'd->base >= (1UL << 36) || d->span > (1UL << 36) - d->base',
                                'd->base >= (1UL << 37) || d->span > (1UL << 37) - d->base')]),
    ('nvme-window-ipa-limit-37', [('compat/nvme-host.h', '#define NVMEH_SP11_IPA_LIMIT (1ull << 36)',
                                   '#define NVMEH_SP11_IPA_LIMIT (1ull << 37)')]),
    ('new-36-bit-site', [('compat/dart.c', '#define ERROR_FLAG (1U<<31)',
                          '#define ERROR_FLAG (1U<<31)\n#define NEW_LIMIT (1UL<<36)')]),
    ('host-ipa-bits-37', [('probes/xnuboot.py', 'STAGE2_IPA_BITS = 36', 'STAGE2_IPA_BITS = 37')]),
    ('host-ipa-limit-37', [('probes/monitor_work.py', 'IPA_LIMIT = 1 << 36', 'IPA_LIMIT = 1 << 37')]),
    ('header-default-37', [(GEOMETRY_H, '#define IPAG_DEFAULT_IPA_BITS 36u', '#define IPAG_DEFAULT_IPA_BITS 37u')]),
    ('sl0-hard-code-moved', [('probes/shadowmmu.c', 'S2_TXSZ | (1UL << 6)', 'S2_TXSZ | (1UL << 7)')]),
    ('retry-vtcr-sl0-moved', [(RETRY_HEADER, 'txsz | (UINT64_C(1) << 6)', 'txsz | (UINT64_C(2) << 6)')]),
    ('retry-walk-window-excludes-txsz', [(RETRY_HEADER, 'txsz < 25 || txsz > 33', 'txsz < 25 || txsz > 27')]),
    ('retry-walk-window-leaves-level-1', [(RETRY_HEADER, 'txsz < 25 || txsz > 33', 'txsz < 20 || txsz > 33')]),
    ('hook-ungated', [(STAGE2_C, '#if SNAP_PLATFORM_SP11\n#include "../../compat/ipa-geometry.h"\n#endif\n',
                       '#include "../../compat/ipa-geometry.h"\n')]),
    ('hook-reports-success', [(STAGE2_C, 'if (why) return -(IPAG_HOOK_RETURN_BASE + why);', 'if (why) return 0;')]),
    ('hook-ignores-parange', [(STAGE2_C, '(unsigned)(parange & 0xf), 0);', '4, 0);')]),
    ('monitor-code-collides-with-hook', [(STAGE2_C, 'if (built) return -11;', 'if (built) return -258;')]),
    ('host-names-hook-code-for-something-else', [('probes/run.py', 'PREPARE_FAILURES = {',
                                                  "PREPARE_FAILURES = {\n    -258: 'the thermal sensor stalled',")]),
    ('sp11-unit-not-linked', [('probes/run.py', "('-DSNAP_PLATFORM_SP11=1', 'compat/ipa-geometry.c')",
                              "('-DSNAP_PLATFORM_SP11=1', 'compat/ipa-geometry-missing.c')")]),
    ('sp11-in-default-flags', [('config/monitor-flags.json', '"flags": [', '"flags": ["-DSNAP_PLATFORM_SP11=1", ')]),
]


def geometry_mutant_layers(name, rel, old, new):
    work = BUILD / f'mut-{name}'
    shutil.rmtree(work, ignore_errors=True)
    texts = {path: (ROOT / path).read_text(encoding='utf-8') for path in (GEOMETRY_C, GEOMETRY_H)}
    if texts[rel].count(old) != 1:
        raise AssertionError(f'{name}: anchor found {texts[rel].count(old)} times in {rel}')
    texts[rel] = texts[rel].replace(old, new)
    (work / 'compat').mkdir(parents=True)
    for path, text in texts.items():
        (work / path).write_text(text, encoding='utf-8')
    binary = build_native(work / 'native', work / 'compat', sanitize=False)
    layers = []
    try:
        proc = run([binary], timeout=90)
        summary = native_summary(proc)
        if proc.returncode or summary is None or summary[1]:
            layers.append('native')
    except subprocess.TimeoutExpired:
        layers.append('native (timeout)')
    try:
        if scenario_failures(binary) or width_failures(binary):
            layers.append('scenarios')
        found = defaults(binary)
        if any(int(found[key], 0) != want for key, want in EXPECTED_DEFAULTS.items()):
            layers.append('defaults')
    except (RuntimeError, subprocess.TimeoutExpired, KeyError):
        layers.append('scenarios (error)')
    if drift_failures(Tree(ROOT, {rel: texts[rel]})):
        layers.append('drift')
    shutil.rmtree(work, ignore_errors=True)
    return layers


def drift_mutant_layers(edits):
    tree = Tree(ROOT)
    for rel, old, new in edits:
        tree = tree.mutate(rel, old, new)
    layers = []
    if drift_failures(tree):
        layers.append('drift')
    if a16_text_failures(tree):
        layers.append('a16-text')
    if return_code_failures(tree, EXPECTED_DEFAULTS['hook_return_base'], EXPECTED_DEFAULTS['reason_count']):
        layers.append('hook-codes')
    return layers


# ---- tests -----------------------------------------------------------------------------------------------------

NATIVE = BUILD / 'native'


def setUpModule():
    build_native(NATIVE, sanitize=True, reuse=True)


class NativeModel(unittest.TestCase):
    def test_native_suite_passes(self):
        proc = run([NATIVE], timeout=300)
        summary = native_summary(proc)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIsNotNone(summary, proc.stdout)
        self.assertEqual(summary[1], 0)
        self.assertGreater(summary[0], 50000, 'the native suite ran suspiciously few checks')

    def test_constants_and_reason_numbering_are_pinned(self):
        found = defaults(NATIVE)
        self.assertEqual({key: int(found[key], 0) for key in EXPECTED_DEFAULTS}, EXPECTED_DEFAULTS)
        self.assertEqual([found[f'reason.{n}'] for n in range(len(REASONS))], REASONS)
        self.assertEqual(64 - 28, EXPECTED_DEFAULTS['default_ipa_bits'])


class Scenarios(unittest.TestCase):
    def test_sku_models_and_faulty_maps(self):
        self.assertEqual(scenario_failures(NATIVE), [])

    def test_root_geometry_at_every_ipa_width(self):
        self.assertEqual(width_failures(NATIVE), [])


class Freestanding(unittest.TestCase):
    OBJECT = BUILD / 'ipa-geometry-freestanding.o'

    def test_compiles_with_the_monitor_flags(self):
        clang = find_tool('clang')
        if not clang:
            self.skipTest('clang not found')
        self.OBJECT.parent.mkdir(parents=True, exist_ok=True)
        proc = run([clang, *MONITOR_FLAGS, '-I', ROOT / 'shadow', '-I', ROOT / 'compat', '-c', ROOT / GEOMETRY_C,
                    '-o', self.OBJECT])
        if proc.returncode and re.search(r'unknown target triple|No available targets', proc.stderr):
            self.skipTest('this clang cannot target aarch64-unknown-none-elf')
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_defines_only_the_span_check_and_its_decoder_and_needs_no_library(self):
        nm = find_tool('llvm-nm')
        if not nm or not self.OBJECT.exists():
            self.skipTest('llvm-nm or the freestanding object is missing')
        symbols = [line.split() for line in run([nm, '-g', self.OBJECT]).stdout.splitlines() if line.strip()]
        undefined = {fields[-1] for fields in symbols if fields[-2] == 'U'}
        defined = {fields[-1] for fields in symbols if fields[-2] != 'U'}
        self.assertEqual(undefined, set(), 'the monitor has no libc to resolve these')
        self.assertEqual(defined, {'ipag_check_spans', 'ipag_pa_bits'}, 'the planner must stay out of the monitor')


class MapText(unittest.TestCase):
    BAD = [
        ('no header', 'parange 4\n'),
        ('wrong version', 'ipa-geometry-map 2\nparange 4\n'),
        ('empty', '# nothing\n'),
        ('no parange', 'ipa-geometry-map 1\ndram 0x80000000 0x1000\n'),
        ('parange twice', 'ipa-geometry-map 1\nparange 4\nparange 4\n'),
        ('header twice', 'ipa-geometry-map 1\nipa-geometry-map 1\nparange 4\n'),
        ('unknown statement', 'ipa-geometry-map 1\nparange 4\nswap 0x1000 0x1000\n'),
        ('missing operand', 'ipa-geometry-map 1\nparange 4\ndram 0x80000000\n'),
        ('trailing operand', 'ipa-geometry-map 1\nparange 4\ndram 0x80000000 0x1000 0x1000\n'),
        ('not a number', 'ipa-geometry-map 1\nparange 4\ndram 0x80000000 big\n'),
        ('negative', 'ipa-geometry-map 1\nparange 4\ndram 0x80000000 -4096\n'),
        ('bare 0x', 'ipa-geometry-map 1\nparange 4\ndram 0x 0x1000\n'),
        ('leading zero', 'ipa-geometry-map 1\nparange 4\ndram 0x80000000 010\n'),
        ('underscore digits', 'ipa-geometry-map 1\nparange 4\ndram 0x8000_0000 0x1000\n'),
        ('wider than 64 bits', 'ipa-geometry-map 1\nparange 4\ndram 0x10000000000000000 0x1000\n'),
        ('parange wider than 32 bits', 'ipa-geometry-map 1\nparange 0x100000000\n'),
        ('efi zero pages', 'ipa-geometry-map 1\nparange 4\nefi 7 0x1000 0\n'),
        ('efi past 2^64', 'ipa-geometry-map 1\nparange 4\nefi 7 0xfffffffffffff000 2\n'),
        ('efi operands', 'ipa-geometry-map 1\nparange 4\nefi 7 0x1000\n'),
        ('66 banks', 'ipa-geometry-map 1\nparange 4\n' + ''.join(f'dram {0x80000000 + n * 0x2000:#x} 0x1000\n' for n in range(66))),
    ]

    # A 16 GiB map as firmware lists it: descriptors split at every change of type, MMIO in its own class.
    EFI_16 = (
        'ipa-geometry-map 1\nparange 4\n'
        'efi 11 0x17000000 0x400\n'          # 4 MiB, GIC
        'efi 11 0x70000000 0x200 0x1\n'      # 2 MiB, PCIe 6A
        'efi 7 0x80000000 0x1000\n'          # 16 MiB conventional
        'efi 2 0x81000000 0x7f000\n'         # loader data, adjacent: one 2 GiB bank with the line above
        'efi 6 0x100000000 0x10\n'           # runtime services: a hole, ignored
        'efi 7 0x880000000 0x1c0000\n'       # 7 GiB
        'efi 4 0xa40000000 0x1c0000\n'       # boot services data, adjacent: 14 GiB bank
    )

    def test_bad_maps_are_rejected(self):
        for name, text in self.BAD:
            with self.subTest(name):
                with self.assertRaises(MapError):
                    parse_map(text)

    def test_efi_descriptors_merge_by_class(self):
        parange, dram_windows, mmio_windows = parse_map(self.EFI_16)
        self.assertEqual(parange, 4)
        self.assertEqual(dram_windows, [(LOW_BASE, 2 * GIB), (HIGH_BASE, 14 * GIB)])
        self.assertEqual(mmio_windows, list(MODEL_MMIO))

    def test_explicit_banks_are_not_merged(self):
        text = 'ipa-geometry-map 1\nparange 4\ndram 0x80000000 0x40000000\ndram 0xc0000000 0x40000000\n'
        self.assertEqual(parse_map(text)[1], [(0x80000000, 0x40000000), (0xc0000000, 0x40000000)])

    def test_models_round_trip_to_the_scenario_results(self):
        for gib, args in ((16, sku(16)), (32, sku(32)), (64, sku(64))):
            with self.subTest(gib=gib):
                text = model_text(gib)
                self.assertEqual(plan(NATIVE, plan_args(parse_map(text))), plan(NATIVE, ('--parange', '4', *args)))

    def test_efi_map_plans_like_the_16_gib_model(self):
        got = plan(NATIVE, plan_args(parse_map(self.EFI_16)))
        self.assertEqual(compare(got, allowed(14)), [])

    def test_unsorted_map_is_passed_on_and_refused(self):
        text = 'ipa-geometry-map 1\nparange 4\ndram 0x880000000 0x380000000\ndram 0x80000000 0x80000000\n'
        got = plan(NATIVE, plan_args(parse_map(text)))
        self.assertEqual((got['reason'], got['bad_index']), ('UNSORTED', '1'))

    def test_command_line_exit_status(self):
        BUILD.mkdir(parents=True, exist_ok=True)
        cases = [('allow', model_text(16), 0, 'result=ALLOW'), ('refuse', model_text(64), 1, 'reason=WINDOW_CROSSES_LIMIT'),
                 ('unreadable', 'not a map\n', 2, '')]
        for name, text, status, expect in cases:
            with self.subTest(name):
                path = BUILD / f'cli-{name}.map'
                path.write_text(text, encoding='utf-8')
                proc = run([sys.executable, Path(__file__).resolve(), '--map', path])
                self.assertEqual(proc.returncode, status, proc.stdout + proc.stderr)
                self.assertIn(expect, proc.stdout)
        proc = run([sys.executable, Path(__file__).resolve(), '--map', BUILD / 'cli-refuse.map', '--policy', 'carve-low'])
        self.assertEqual((proc.returncode, 'carve_size=0x780000000' in proc.stdout), (0, True), proc.stdout)
        proc = run([sys.executable, Path(__file__).resolve(), '--emit-model', '32'])
        self.assertEqual((proc.returncode, proc.stdout), (0, model_text(32)))


class SourceDrift(unittest.TestCase):
    def test_every_36_bit_site_agrees_with_the_model(self):
        self.assertEqual(drift_failures(Tree()), [])
        text_only = 'static const char *why = "beyond 1<<36"; /* 1UL << 36 */ // 1UL << 36\n'
        self.assertEqual(drift_failures(Tree(ROOT, {'compat/synthetic.c': text_only})), [])
        code = '#define LIMIT (1UL << 36) /* "quoted" */\n'
        self.assertEqual(len(drift_failures(Tree(ROOT, {'compat/synthetic.c': code}))), 1)

    def test_native_default_matches_the_header(self):
        header = re.search(r'#\s*define\s+IPAG_DEFAULT_IPA_BITS\s+(\d+)u?\b', Tree().text(GEOMETRY_H))
        self.assertEqual(int(defaults(NATIVE)['default_ipa_bits']), int(header.group(1)))

    def test_default_build_text_is_unchanged_and_hook_is_opt_in(self):
        self.assertEqual(a16_text_failures(Tree()), [])

    def test_hook_return_codes_leave_existing_codes_alone(self):
        self.assertEqual(return_code_failures(Tree(), EXPECTED_DEFAULTS['hook_return_base'],
                                              EXPECTED_DEFAULTS['reason_count']), [])

    def test_the_host_table_may_name_hook_codes_and_nothing_else_there(self):
        base, count = EXPECTED_DEFAULTS['hook_return_base'], EXPECTED_DEFAULTS['reason_count']

        def with_entry(code, text):
            return Tree().mutate('probes/run.py', 'PREPARE_FAILURES = {', f'PREPARE_FAILURES = {{\n    {code}: {text!r},')

        def with_return(code):
            return Tree(ROOT, {'probes/xnuboot/synthetic.c': f'int f(void)\n{{\n    return -{code};\n}}\n'})

        named = 'SP11 stage-2 geometry: the DRAM map does not fit'
        self.assertEqual(return_code_failures(with_entry(-(base + 2), named), base, count), [])
        self.assertEqual(return_code_failures(with_entry(-(base + count - 1), named), base, count), [])
        self.assertEqual(return_code_failures(with_entry(-(base + count), 'another agent'), base, count), [])
        for code, text in ((-(base + 2), 'the thermal sensor stalled'), (-base, named)):
            with self.subTest(code=code):
                self.assertEqual(len(return_code_failures(with_entry(code, text), base, count)), 1)
        for code in (base, base + 2, base + count - 1):
            with self.subTest(source_code=code):
                self.assertEqual(len(return_code_failures(with_return(code), base, count)), 1)
        for code in (base - 1, base + count):
            with self.subTest(source_code=code):
                self.assertEqual(return_code_failures(with_return(code), base, count), [])

    def test_the_split_between_gated_and_default_text(self):
        text = '#if A\nx\n#endif\n#if SNAP_PLATFORM_SP11 /* gate */\ng1\n#ifdef Z\ng2\n#endif\n#else\nd1\n#endif\nd2\n'
        inside, outside = split_gated(text)
        self.assertEqual([line for _, line in inside], ['#if SNAP_PLATFORM_SP11 /* gate */', 'g1', '#ifdef Z', 'g2', '#endif'])
        self.assertEqual([line for _, line in outside if line and not line.startswith('#')], ['x', 'd1', 'd2'])


class Mutants(unittest.TestCase):
    def setUp(self):
        if NO_MUTANTS:
            self.skipTest('--no-mutants')

    def test_geometry_breakages_are_caught(self):
        for name, rel, old, new in GEOMETRY_MUTANTS:
            with self.subTest(name):
                layers = geometry_mutant_layers(name, rel, old, new)
                print(f'  mutant {name}: caught by {", ".join(layers) or "NOTHING"}')
                self.assertTrue(layers, f'{name} survived every layer')

    def test_drift_and_hook_breakages_are_caught(self):
        for name, edits in DRIFT_MUTANTS:
            with self.subTest(name):
                layers = drift_mutant_layers(edits)
                print(f'  mutant {name}: caught by {", ".join(layers) or "NOTHING"}')
                self.assertTrue(layers, f'{name} survived every layer')

    def test_a_clean_tree_survives_nothing(self):
        self.assertEqual(drift_mutant_layers([]), [])


def main():
    global NO_MUTANTS
    parser = argparse.ArgumentParser(description='Stage-2 / IPA geometry tests; unknown options go to unittest.')
    parser.add_argument('--no-mutants', action='store_true', help='skip the mutation checks')
    parser.add_argument('--map', help='plan a map text file instead of running the tests')
    parser.add_argument('--emit-model', type=int, choices=sorted(MODEL_HIGH_GIB), help='print the INFERRED model map')
    for flag in ('--ipa-bits', '--want', '--align'):
        parser.add_argument(flag, type=lambda text: int(text, 0))
    parser.add_argument('--policy', choices=('refuse', 'carve-low'))
    options, rest = parser.parse_known_args()
    if options.emit_model:
        sys.stdout.write(model_text(options.emit_model))
        return 0
    if options.map:
        return map_mode(options)
    NO_MUTANTS = options.no_mutants
    unittest.main(argv=[sys.argv[0], *rest], verbosity=2)


if __name__ == '__main__':
    sys.exit(main())
