#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Generate compat/adreno-a7xx-protect-tables.h, the CP_PROTECT table Linux programs on the Adreno X1-85
(gen70500, chip id 0x43050c01) of the Snapdragon X Elite X1E80100 in the Surface Pro 11.

STUDY ARTEFACT (docs/SP11-GPU-A7XX-STUDY.md): nothing includes the output and nothing in it has run on hardware.

Licensing: this script is MIT and carries no upstream table data. It parses the pinned Linux sources
(GPL-2.0-only) at run time, so the generated header is a derivative of them and is GPL-2.0-only. Never merge it
into an MIT file: a monitor build that included it would be a combined GPL-2.0 work (THIRD_PARTY_NOTICES.md).

Inputs: four blobs of Linux v7.4-rc2 (commit 7475ea8b58616b33c04afb976cf0fbaad9dd63fb) under
drivers/gpu/drm/msm/adreno/, fetched by hand into the git-ignored build/ref/linux-<commit>/ and each required to
match the SHA-256 pinned below (the git blob id of each is listed in docs/SP11-GPU-A7XX-STUDY.md):
  a7xx_catalog.c  the chip's `.protect = &NAME` entry, the NAME_regs[] initialisers, DECLARE_ADRENO_PROTECT(NAME, N)
  a6xx_gpu.h      the A6XX_PROTECT_NORDWR / A6XX_PROTECT_RDONLY macro bodies: the word encoding is evaluated from them
  adreno_gpu.h    the DECLARE_ADRENO_PROTECT body: NAME_regs is the array, ARRAY_SIZE its count, N the slot limit
  a6xx_gpu.c      a6xx_set_cp_protect(): zero words are skipped, the last word goes to slot N-1, LAST_SPAN_INF is set
Fetch one with: curl -fsSL https://raw.githubusercontent.com/torvalds/linux/<commit>/drivers/gpu/drm/msm/adreno/<file>

  python3 tools/gen-a7xx-cp-protect.py [--linux-root DIR] [--chip-id 0x43050c01] [--out PATH] [--check]

Exit status: 0 written or up to date, 1 --check found a difference, 2 refused (nothing is written on a refusal).
Every refusal is deliberate: a table that cannot be parsed, counted or verified must not become a header."""
from pathlib import Path
import argparse
import ast
import hashlib
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
PIN_TAG = 'v7.4-rc2'
PIN_COMMIT = '7475ea8b58616b33c04afb976cf0fbaad9dd63fb'
ADRENO_DIR = 'drivers/gpu/drm/msm/adreno'
PIN_SHA256 = {
    'a7xx_catalog.c': '6941242f183b76143464237896f8b0b97f9a6533cb1efb6f834f2da4dcb6c799',
    'a6xx_gpu.h': '3d11d36a6ec3f16876baaf6f8b76fcfcaa8e0b2991892e886fbe850682f87c70',
    'adreno_gpu.h': 'c8fe321c6df3a4b2fc2f3f186cf6b3952fd0196f7e5c32b5d7b2740405dd3be6',
    'a6xx_gpu.c': '9b725c1c56a9b6b804de0daa899fa03f51ddb6c8253904c77814540d5142f4cd',
}
DEFAULT_CHIP_ID = 0x43050c01
DEFAULT_OUT = ROOT / 'compat' / 'adreno-a7xx-protect-tables.h'
# GPL-2.0 is the deprecated SPDX spelling of GPL-2.0-only (a6xx_gpu.h uses it). Anything else, such as an
# "or later" or dual licence, changes what the generated header may be combined with, so it is refused.
ALLOWED_LICENCES = ('GPL-2.0-only', 'GPL-2.0')
MACROS = {'A6XX_PROTECT_NORDWR': 1, 'A6XX_PROTECT_RDONLY': 0}
NUMBER = r'(?:0[xX][0-9a-fA-F]+|0|[1-9][0-9]*)'
CALL = re.compile(r'([A-Za-z_]\w*)\s*\(\s*(' + NUMBER + r')\s*,\s*(' + NUMBER + r')\s*\)')


class Refusal(Exception):
    pass


def refuse(message):
    raise Refusal(message)


def strip_comments(text):
    """Blank C comments and the contents of string and character constants, keeping newlines so offsets still map to
    line numbers; text inside either can then never be mistaken for code. An unterminated comment, string or
    character constant is what a truncated file looks like, so it is refused."""
    out = []
    i, n = 0, len(text)
    while i < n:
        pair = text[i:i + 2]
        if pair == '/*':
            end = text.find('*/', i + 2)
            if end < 0:
                refuse('unterminated comment: the input looks truncated')
            out.append(re.sub(r'[^\n]', ' ', text[i:end + 2]))
            i = end + 2
        elif pair == '//':
            end = text.find('\n', i)
            end = n if end < 0 else end
            out.append(' ' * (end - i))
            i = end
        elif text[i] in '"\'':
            quote, j = text[i], i + 1
            while j < n and text[j] != quote:
                if text[j] == '\n':
                    refuse('unterminated string or character constant')
                j += 2 if text[j] == '\\' else 1
            if j >= n:
                refuse('unterminated string or character constant')
            out.append(quote + ' ' * (j - i - 1) + quote)
            i = j + 1
        else:
            out.append(text[i])
            i += 1
    return ''.join(out)


def line_of(text, offset):
    return text.count('\n', 0, offset) + 1


def read_notice(name, raw):
    """The Copyright and Author lines of a file header, read from the file itself so no attribution is typed here."""
    licence, holders = None, []
    for line in raw.split('\n')[:20]:
        text = re.sub(r'^\s*(?:/\*+|\*+|//)\s?', '', line)
        text = re.sub(r'\s*\*+/\s*$', '', text).strip()
        found = re.fullmatch(r'SPDX-License-Identifier:\s*(.+)', text)
        if found:
            licence = found.group(1).strip()
        elif re.match(r'(?:Copyright\b|Author:)', text):
            holders.append(text)
    if licence not in ALLOWED_LICENCES:
        refuse(f'{name}: licence {licence!r} is not GPL-2.0-only, so the licence of the output is not known')
    if not any(h.startswith('Copyright') for h in holders):
        refuse(f'{name}: no Copyright line in the file header, the attribution could not be reproduced')
    if any('*/' in h for h in holders):
        refuse(f'{name}: a notice line contains a comment terminator')
    return licence, holders


def split_items(body):
    items, depth, start = [], 0, 0
    for i, ch in enumerate(body):
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth < 0:
                refuse('unbalanced parentheses in the table')
        elif ch == ',' and depth == 0:
            items.append(body[start:i].strip())
            start = i + 1
    if depth:
        refuse('unbalanced parentheses in the table: the input looks truncated')
    tail = body[start:].strip()
    if tail:
        items.append(tail)
    return items


def function_body(code, signature, what):
    hits = list(re.finditer(signature, code))
    if len(hits) != 1:
        refuse(f'{what}: expected exactly one definition, found {len(hits)}')
    depth, i = 1, hits[0].end()
    while i < len(code) and depth:
        depth += {'{': 1, '}': -1}.get(code[i], 0)
        i += 1
    if depth:
        refuse(f'{what}: the body is not closed, the input looks truncated')
    return code[hits[0].end():i], line_of(code, hits[0].start()), line_of(code, i)


def macro_definition(code, name):
    pattern = re.compile(r'^[ \t]*#[ \t]*define[ \t]+' + name + r'[ \t]*\(([^)\n]*)\)((?:\\\n|[^\n])*)', re.M)
    hits = list(pattern.finditer(code))
    if len(hits) != 1:
        refuse(f'{name}: ' + ('not defined' if not hits else 'defined more than once'))
    params = [p.strip() for p in hits[0].group(1).split(',')]
    body = hits[0].group(2).replace('\\\n', ' ').strip()
    return params, body, line_of(code, hits[0].start()), line_of(code, hits[0].end())


def compile_macro(name, params, body):
    """The macro as a Python function of (register, length), evaluated from its pinned body over a whitelist of
    operators (| & <<, integer literals, its two parameters). Anything else is refused rather than guessed."""
    if len(params) != 2 or not all(re.fullmatch(r'[A-Za-z_]\w*', p) for p in params):
        refuse(f'{name}: expected a two-parameter (register, length) macro')
    try:
        tree = ast.parse(body, mode='eval')
    except SyntaxError:
        refuse(f'{name}: the body is not a plain integer expression')

    def evaluate(node, env):
        if isinstance(node, ast.Expression):
            return evaluate(node.body, env)
        if isinstance(node, ast.Constant) and type(node.value) is int:
            return node.value
        if isinstance(node, ast.Name) and node.id in env:
            return env[node.id]
        if isinstance(node, ast.BinOp):
            left, right = evaluate(node.left, env), evaluate(node.right, env)
            if isinstance(node.op, ast.BitOr):
                return left | right
            if isinstance(node.op, ast.BitAnd):
                return left & right
            if isinstance(node.op, ast.LShift) and 0 <= right < 32:
                return left << right
        refuse(f'{name}: unsupported construct in the macro body')

    def encode(register, length):
        word = evaluate(tree, {params[0]: register, params[1]: length})
        if not 0 <= word <= 0xffffffff:
            refuse(f'{name}({register:#x}, {length:#x}) does not fit 32 bits')
        return word

    return encode


def decode(word):
    """The layout the header documents: register bits 0..17, length bits 18..30, NORDWR bit 31."""
    return word & 0x3ffff, (word >> 18) & 0x1fff, word >> 31


def protect_table_of(code, chip_id):
    """Name of the CP_PROTECT table the catalog entry of chip_id points at, with its two line numbers."""
    hits = []
    for call in re.finditer(r'\bADRENO_CHIP_IDS\s*\(([^)]*)\)', code):
        if chip_id in [int(v, 0) for v in re.findall(NUMBER, call.group(1))]:
            hits.append(call)
    if len(hits) != 1:
        refuse(f'chip id {chip_id:#x}: ' + ('not in the catalog' if not hits else 'listed by more than one entry'))
    end = hits[0].end()
    following = re.search(r'\.chip_ids\s*=', code[end:])
    entry = code[hits[0].start():end + following.start() if following else len(code)]
    pointers = list(re.finditer(r'\.protect\s*=\s*&\s*([A-Za-z_]\w*)\s*[,}]', entry))
    if len(pointers) != 1:
        refuse(f'chip id {chip_id:#x}: its catalog entry has {len(pointers)} .protect pointers, expected one')
    return (pointers[0].group(1), line_of(code, hits[0].start()),
            line_of(code, hits[0].start() + pointers[0].start()))


def regs_array(code, table):
    """(initialiser items, first line, last line, offset after the closing brace) of static const u32 TABLE_regs[]."""
    name = table + '_regs'
    opening = list(re.finditer(r'\bstatic\s+const\s+u32\s+' + re.escape(name) + r'\s*\[\s*\]\s*=\s*\{', code))
    if len(opening) != 1:
        refuse(f'{name}[]: expected exactly one definition, found {len(opening)}')
    start = opening[0].end()
    close = code.find('}', start)
    if close < 0:
        refuse(f'{name}[]: no closing brace, the input looks truncated')
    if '{' in code[start:close]:
        refuse(f'{name}[]: nested initialisers are not supported')
    if not code[close + 1:].lstrip().startswith(';'):
        refuse(f'{name}[]: the closing brace is not followed by a semicolon, the input looks truncated')
    items = split_items(code[start:close])
    if items and not items[-1]:
        items.pop()
    if not items or '' in items:
        refuse(f'{name}[]: ' + ('empty table' if not items else 'empty initialiser'))
    return items, line_of(code, opening[0].start()), line_of(code, close), close + 1


def declared_limit(code, table, after):
    pattern = r'\bDECLARE_ADRENO_PROTECT\s*\(\s*' + re.escape(table) + r'\s*,\s*(' + NUMBER + r')\s*\)'
    hits = list(re.finditer(pattern, code))
    if len(hits) != 1:
        refuse(f'DECLARE_ADRENO_PROTECT({table}, N): expected exactly one declaration, found {len(hits)}')
    if hits[0].start() < after:
        refuse(f'DECLARE_ADRENO_PROTECT({table}, N) comes before the table it declares')
    return int(hits[0].group(1), 0), line_of(code, hits[0].start())


def check_declare_macro(code):
    pattern = re.compile(r'^[ \t]*#[ \t]*define[ \t]+DECLARE_ADRENO_PROTECT\s*\(\s*(\w+)\s*,\s*(\w+)\s*\)'
                         r'((?:\\\n|[^\n])*)', re.M)
    hits = list(pattern.finditer(code))
    if len(hits) != 1:
        refuse(f'DECLARE_ADRENO_PROTECT: expected exactly one definition, found {len(hits)}')
    name, limit, body = hits[0].group(1), hits[0].group(2), hits[0].group(3).replace('\\\n', ' ')
    for what, rule in (('uses NAME_regs as the array', rf'\.regs\s*=\s*{name}\s*##\s*_regs\s*,'),
                       ('counts it with ARRAY_SIZE', rf'\.count\s*=\s*ARRAY_SIZE\s*\(\s*{name}\s*##\s*_regs\s*\)\s*,'),
                       ('takes the slot limit from its second argument', rf'\.count_max\s*=\s*{limit}\s*,')):
        if not re.search(rule, body):
            refuse(f'DECLARE_ADRENO_PROTECT no longer {what}: the table cannot be located the way this tool does')
    return line_of(code, hits[0].start()), line_of(code, hits[0].end())


def check_consumer(code):
    """a6xx_set_cp_protect() at the pin: the facts the header's consumption note states, checked rather than typed."""
    body, first, last = function_body(
        code, r'\bstatic\s+void\s+a6xx_set_cp_protect\s*\(\s*struct\s+msm_gpu\s*\*\s*gpu\s*\)\s*\{',
        'a6xx_set_cp_protect()')
    required = (
        ('writes CP_PROTECT_CNTL', r'gpu_write\s*\(\s*gpu\s*,\s*REG_A6XX_CP_PROTECT_CNTL\s*,'),
        ('sets LAST_SPAN_INF', r'\bA6XX_CP_PROTECT_CNTL_LAST_SPAN_INF_RANGE\b'),
        ('writes count - 1 entries in the loop', r'\bi\s*<\s*protect->count\s*-\s*1\s*;'),
        ('skips zero words', r'\bif\s*\(\s*protect->regs\s*\[\s*i\s*\]\s*\)'),
        ('writes the last word to slot count_max - 1',
         r'REG_A6XX_CP_PROTECT\s*\(\s*protect->count_max\s*-\s*1\s*\)\s*,\s*protect->regs\s*\[\s*i\s*\]'),
    )
    for what, rule in required:
        if not re.search(rule, body):
            refuse(f'a6xx_set_cp_protect() no longer {what}: the consumption note would be unsupported')
    return first, last


def parse_table(items, encoders, count_max):
    entries = []
    for index, item in enumerate(items):
        if re.fullmatch(r'0[xX]0+|0', item):
            entries.append((0, None, 0, 0))
            continue
        call = CALL.fullmatch(item)
        if not call:
            refuse(f'entry {index}: unrecognised initialiser {item[:40]!r}')
        macro = call.group(1)
        if macro not in encoders:
            refuse(f'entry {index}: unknown macro {macro}')
        register, length = int(call.group(2), 0), int(call.group(3), 0)
        word = encoders[macro](register, length)
        if decode(word) != (register, length, MACROS[macro]):
            refuse(f'entry {index}: {macro}({register:#x}, {length:#x}) encodes to {word:#010x}, which does not '
                   'decode as register bits 0..17, length bits 18..30, NORDWR bit 31')
        entries.append((word, 'NORDWR' if MACROS[macro] else 'RDONLY', register, length))
    if len(entries) > count_max:
        refuse(f'{len(entries)} entries overflow the {count_max} CP_PROTECT slots DECLARE_ADRENO_PROTECT allows')
    words = [word for word, *_ in entries if word]
    if len(words) != len(set(words)):
        refuse('duplicate entry: the same CP_PROTECT word appears twice')
    if not entries[-1][0]:
        refuse('the last entry is a zero placeholder; the pinned driver would write it to the final slot, whose '
               'range extends to the end of the register space')
    return entries


def render(ctx):
    pinned = ctx['pinned']
    entries, count_max, table = ctx['entries'], ctx['count_max'], ctx['table']
    count = len(entries)
    lines = [
        '/* SPDX-License-Identifier: GPL-2.0-only */',
        '/*',
        f' * adreno_a7xx_protect[] is {table}_regs of Linux {ADRENO_DIR}/a7xx_catalog.c,',
        ' * encoded by the A6XX_PROTECT_* macros of a6xx_gpu.h in the same directory, so this file is GPL-2.0-only',
        ' * (LICENSES/GPL-2.0-only.txt). Attribution, read from the headers of the files it is derived from:',
    ]
    for name in PIN_SHA256:
        lines.append(f' *   {name} ({ctx["licences"][name]}):')
        lines.extend(f' *     {holder}' for holder in ctx['holders'][name])
    lines += [
        ' * A monitor binary built with this table would be a combined work, distributable under GPL-2.0 (see LICENSE',
        ' * and THIRD_PARTY_NOTICES.md). Nothing includes this file: it is a study artefact (docs/SP11-GPU-A7XX-STUDY.md).',
        ' */',
        '/*',
        ' * adreno-a7xx-protect-tables.h - GENERATED by tools/gen-a7xx-cp-protect.py. Do not edit; regenerate with',
        ' *   python3 tools/gen-a7xx-cp-protect.py',
    ]
    if pinned:
        lines.append(f' * from Linux {PIN_TAG} (commit {PIN_COMMIT}), {ADRENO_DIR}/ (sha256):')
    else:
        lines += [' * UNPINNED: --unpinned skipped the SHA-256 pin check, so this output is for fixtures only and must',
                  ' * never be committed. Inputs (sha256):']
    lines += [f' *   {name} {ctx["digests"][name]}' for name in PIN_SHA256]
    lines += [
        f' * Selection: chip id {ctx["chip_id"]:#x} is listed at a7xx_catalog.c:{ctx["chip_line"]}; its entry points at',
        f' * .protect = &{table} (line {ctx["protect_line"]}).',
        f' * Table: {table}_regs, a7xx_catalog.c:{ctx["array_lines"][0]}-{ctx["array_lines"][1]}; '
        f'DECLARE_ADRENO_PROTECT({table}, {count_max}) at line {ctx["declare_line"]}.',
        f' * Encoding, evaluated from a6xx_gpu.h:{ctx["macro_lines"][0]}-{ctx["macro_lines"][1]} rather than assumed: '
        'register = bits 0..17,',
        ' * length = bits 18..30, NORDWR = bit 31 (RDONLY leaves it clear). NORDWR blocks reads and writes of',
        ' * [register, register + length], inclusive; RDONLY blocks writes only. EXPERIMENTAL for gen70500:',
        ' * compat/adreno-ib-tables.h documents the same layout for the a8xx table, nothing has confirmed it on an X1-85.',
        f' * Consumption at the pin (checked by the generator, a6xx_gpu.c:{ctx["consumer_lines"][0]}-'
        f'{ctx["consumer_lines"][1]}): CP_PROTECT_CNTL is written',
        f' * with LAST_SPAN_INF set; words 0..{count - 2} go to CP_PROTECT(i) and a zero word is skipped; the final word goes to',
        f' * CP_PROTECT({count_max - 1}) and then protects from its start register to the end of the register space.',
        f' * {count} words, {sum(1 for e in entries if e[0])} of them ranges; the zero words are slots the pinned driver '
        'leaves untouched.',
    ]
    if ctx['sorted']:
        lines.append(' * OBSERVED, not an upstream guarantee: the start registers of the ranges strictly increase.')
    lines += [
        ' * Status: UNIMPLEMENTED in the monitor and kext, EXPERIMENTAL for gen70500: this table has never been written',
        ' * to an Adreno X1-85 and no hardware has validated it.',
        ' */',
        '#ifndef ADRENO_A7XX_PROTECT_TABLES_H',
        '#define ADRENO_A7XX_PROTECT_TABLES_H',
        '#include <stdint.h>',
        '',
        f'#define ADRENO_A7XX_PROTECT_COUNT {count}u',
        f'#define ADRENO_A7XX_PROTECT_COUNT_MAX {count_max}u',
        '#define ADRENO_A7XX_PROTECT_LAST_SPAN_INF 1u',
        '#define ADRENO_A7XX_PROTECT_REG(w) ((w) & 0x3ffffu)',
        '#define ADRENO_A7XX_PROTECT_LEN(w) (((w) >> 18) & 0x1fffu)',
        '#define ADRENO_A7XX_PROTECT_NORDWR(w) (((w) >> 31) & 1u)',
        'static const uint32_t adreno_a7xx_protect[ADRENO_A7XX_PROTECT_COUNT] = {',
    ]
    for index, (word, kind, register, length) in enumerate(entries):
        if not word:
            note = 'zero: not written'
        elif index == count - 1:
            note = f'{kind} {register:#07x}-end of register space'
        else:
            note = f'{kind} {register:#07x}-{register + length:#07x}'
        lines.append(f'    0x{word:08x}u, /* [{index:2d}] {note} */')
    lines += ['};', '', '#endif /* ADRENO_A7XX_PROTECT_TABLES_H */', '']
    return '\n'.join(lines)


def generate_from_blobs(blobs, chip_id=DEFAULT_CHIP_ID, unpinned=False):
    if set(blobs) != set(PIN_SHA256):
        refuse(f'expected exactly the inputs {sorted(PIN_SHA256)}')
    digests = {name: hashlib.sha256(blob).hexdigest() for name, blob in blobs.items()}
    if not unpinned:
        for name, want in PIN_SHA256.items():
            if digests[name] != want:
                refuse(f'{name}: sha256 {digests[name]} is not the {want} pinned for Linux {PIN_TAG}')
    raw = {}
    for name, blob in blobs.items():
        try:
            raw[name] = blob.decode('utf-8')
        except UnicodeDecodeError:
            refuse(f'{name}: not UTF-8')
    code = {name: strip_comments(text) for name, text in raw.items()}
    notices = {name: read_notice(name, text) for name, text in raw.items()}

    table, chip_line, protect_line = protect_table_of(code['a7xx_catalog.c'], chip_id)
    items, first, last, after = regs_array(code['a7xx_catalog.c'], table)
    count_max, declare_line = declared_limit(code['a7xx_catalog.c'], table, after)
    check_declare_macro(code['adreno_gpu.h'])
    consumer_lines = check_consumer(code['a6xx_gpu.c'])
    encoders, macro_lines = {}, []
    for name in MACROS:
        params, body, begin, end = macro_definition(code['a6xx_gpu.h'], name)
        encoders[name] = compile_macro(name, params, body)
        macro_lines += [begin, end]
    entries = parse_table(items, encoders, count_max)
    starts = [register for word, _, register, _ in entries if word]
    return render({
        'pinned': not unpinned, 'chip_id': chip_id, 'table': table, 'entries': entries, 'count_max': count_max,
        'digests': digests, 'licences': {n: v[0] for n, v in notices.items()},
        'holders': {n: v[1] for n, v in notices.items()}, 'chip_line': chip_line, 'protect_line': protect_line,
        'array_lines': (first, last), 'declare_line': declare_line, 'consumer_lines': consumer_lines,
        'macro_lines': (min(macro_lines), max(macro_lines)),
        'sorted': all(a < b for a, b in zip(starts, starts[1:])),
    })


def generate(adreno_dir, chip_id=DEFAULT_CHIP_ID, unpinned=False):
    blobs = {}
    for name in PIN_SHA256:
        try:
            blobs[name] = (Path(adreno_dir) / name).read_bytes()
        except OSError as error:
            refuse(f'cannot read {name}: {error.strerror}; fetch the pinned blob first (see the module docstring)')
    return generate_from_blobs(blobs, chip_id, unpinned)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--linux-root', type=Path, default=ROOT / 'build' / 'ref' / f'linux-{PIN_COMMIT}',
                        help='directory holding drivers/gpu/drm/msm/adreno/ of the pinned Linux commit')
    parser.add_argument('--chip-id', type=lambda v: int(v, 0), default=DEFAULT_CHIP_ID)
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    parser.add_argument('--check', action='store_true', help='compare with --out instead of writing it')
    parser.add_argument('--unpinned', action='store_true', help='skip the SHA-256 pin check (fixtures only)')
    args = parser.parse_args(argv)
    try:
        text = generate(args.linux_root / ADRENO_DIR, args.chip_id, args.unpinned)
    except Refusal as error:
        print(f'gen-a7xx-cp-protect: REFUSED: {error}', file=sys.stderr)
        return 2
    if args.check:
        current = args.out.read_text(encoding='utf-8') if args.out.is_file() else None
        if current != text:
            print(f'gen-a7xx-cp-protect: {args.out.name} is {"missing" if current is None else "stale"}; '
                  'regenerate it', file=sys.stderr)
            return 1
        print(f'gen-a7xx-cp-protect: {args.out.name} is up to date')
        return 0
    args.out.write_text(text, encoding='utf-8')
    print(f'gen-a7xx-cp-protect: wrote {args.out.name}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
