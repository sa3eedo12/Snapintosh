#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""tools/gen-a7xx-cp-protect.py must turn the pinned Linux CP_PROTECT table into a header exactly, and refuse
anything it cannot verify.

Two groups. The fixture tests run offline on small synthetic inputs written here (their register values are
invented, not Linux's) and cover the encoding, the table selection and every refusal. The pinned tests need the
four Linux v7.4-rc2 blobs: they are read from build/ref/ (git-ignored), fetched once from raw.githubusercontent.com
when absent, and reported as SKIP, never as success, when neither works (A7XX_PROTECT_OFFLINE=1 forces that).
The only upstream values written below are the first and last words of the table, transcribed by hand, as the
golden check of the generated header; everything else is computed from the inputs.

  python3 tools/test-gen-a7xx-cp-protect.py"""
from pathlib import Path
import contextlib
import hashlib
import importlib.util
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / 'build'
SOURCE = ROOT / 'tools' / 'gen-a7xx-cp-protect.py'
HEADER_NAME = 'adreno-a7xx-protect-tables.h'
COMMITTED = ROOT / 'compat' / HEADER_NAME
LINUX_ROOT = Path(os.environ.get('A7XX_PROTECT_LINUX_ROOT') or ROOT / 'build' / 'ref' / 'linux-7475ea8b58616b33c04afb976cf0fbaad9dd63fb')
NESTED = bool(os.environ.get('A7XX_PROTECT_NESTED'))

spec = importlib.util.spec_from_file_location('gen_a7xx_cp_protect', SOURCE)
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)

# Git blob ids of the four pinned files, as the GitHub contents API reported them for the pinned commit. They tie the
# SHA-256 pins in the generator to the upstream objects instead of to whatever was downloaded.
BLOB_IDS = {
    'a7xx_catalog.c': '6f704898cea6e10f732b38ae03be2ee4bffcc83f',
    'a6xx_gpu.h': '79912a33e17cf2ff769143259353c6b19124a6de',
    'adreno_gpu.h': '4b2156825bdf2ac2679acba20ecb6146c3fe74d3',
    'a6xx_gpu.c': '92e8c8abb86da54bbdf918175ad6a9a1c467f7d1',
}
# Hand transcription of A6XX_PROTECT_RDONLY(0x00000, 0x04ff) and A6XX_PROTECT_NORDWR(0x1f8c0, 0x00000) from the
# pinned a730_protect_regs: 0x04ff << 18 = 0x13fc0000, and 0x80000000 | 0x1f8c0 = 0x8001f8c0.
GOLDEN_FIRST, GOLDEN_LAST = 0x13fc0000, 0x8001f8c0

CHIP = '0x43050c01'
GOOD_ITEMS = ['A6XX_PROTECT_RDONLY(0x00010, 0x0003)', 'A6XX_PROTECT_NORDWR(0x00100, 0x0000)', '0',
              'A6XX_PROTECT_NORDWR(0x00200, 0x0000)']
GOOD_WORDS = [0x000c0010, 0x80000100, 0, 0x80000200]
NORDWR_BODY = '((1 << 31) | (((_len) & 0x3fff) << 18) | ((_reg) & 0x3ffff))'
RDONLY_BODY = '((((_len) & 0x3fff) << 18) | ((_reg) & 0x3ffff))'
GPU_HEADER_HEAD = '/* SPDX-License-Identifier: GPL-2.0 */\n/* Copyright (c) 2002 Example Org */\n\n'
CATALOG_HEAD = ('// SPDX-License-Identifier: GPL-2.0-only\n/*\n * Copyright (C) 2001 Example Org\n'
                ' * Author: A. Writer <writer@example.org>\n */\n\n')
DECLARE_MACRO = ('#define DECLARE_ADRENO_PROTECT(name, __count_max) \\\n'
                 'static const struct adreno_protect name = { \\\n'
                 '\t.regs = name ## _regs, \\\n'
                 '\t.count = ARRAY_SIZE(name ## _regs), \\\n'
                 '\t.count_max = __count_max, \\\n'
                 '};\n')
CONSUMER = '''static void a6xx_set_cp_protect(struct msm_gpu *gpu)
{
	const struct adreno_protect *protect = table_of(gpu);
	unsigned i;

	gpu_write(gpu, REG_A6XX_CP_PROTECT_CNTL,
		  A6XX_CP_PROTECT_CNTL_ACCESS_PROT_EN | A6XX_CP_PROTECT_CNTL_LAST_SPAN_INF_RANGE);

	for (i = 0; i < protect->count - 1; i++) {
		if (protect->regs[i])
			gpu_write(gpu, REG_A6XX_CP_PROTECT(i), protect->regs[i]);
	}
	gpu_write(gpu, REG_A6XX_CP_PROTECT(protect->count_max - 1), protect->regs[i]);
}
'''


def catalog(tables=(('fx_protect', GOOD_ITEMS, 4),), entries=((CHIP, 'fx_protect'),), head=CATALOG_HEAD,
            inside='', entries_first=False):
    """A synthetic a7xx_catalog.c: the tables, then the chip entries (or the other way round, which lets a test cut
    the file inside a table while the entry that selects it is still intact)."""
    listing = 'static const struct adreno_info a7xx_gpus[] = {\n'
    for chip, protect in entries:
        pointer = f'.protect = &{protect},' if protect else ''
        listing += (f'\t{{\n\t\t.chip_ids = ADRENO_CHIP_IDS({chip}),\n\t\t.fw = {{ [ADRENO_FW_SQE] = "fx.fw" }},\n'
                    f'\t\t.a6xx = &(const struct a6xx_info) {{\n\t\t\t.hwcg = fx_hwcg, {pointer}\n\t\t}},\n\t}},\n')
    listing += '};\n'
    arrays = ''
    for name, items, limit in tables:
        arrays += f'static const u32 {name}_regs[] = {{\n{inside}' + ''.join(f'\t{i},\n' for i in items) + '};\n'
        arrays += f'DECLARE_ADRENO_PROTECT({name}, {limit});\n\n'
    return head + (listing + arrays if entries_first else arrays + listing)


def gpu_header(nordwr=NORDWR_BODY, rdonly=RDONLY_BODY, head=GPU_HEADER_HEAD, params='_reg, _len'):
    return (head + f'#define A6XX_PROTECT_NORDWR({params}) \\\n\t{nordwr}\n'
            f'#define A6XX_PROTECT_RDONLY({params}) {rdonly}\n')


def inputs():
    return {'a7xx_catalog.c': catalog(), 'a6xx_gpu.h': gpu_header(),
            'adreno_gpu.h': CATALOG_HEAD + DECLARE_MACRO, 'a6xx_gpu.c': GPU_HEADER_HEAD + CONSUMER}


def swap(text, old, new):
    assert old in text, f'fixture does not contain {old!r}'
    return text.replace(old, new, 1)


def build(chip=CHIP, **replacements):
    """Generator output for the synthetic inputs; replacements maps an input file name to its replacement text."""
    texts = inputs()
    texts.update(replacements)
    blobs = {name: text.encode() for name, text in texts.items()}
    return gen.generate_from_blobs(blobs, chip_id=int(chip, 0), unpinned=True)


def words_of(header):
    table = header.split('adreno_a7xx_protect[ADRENO_A7XX_PROTECT_COUNT] = {', 1)[1].split('};', 1)[0]
    return [int(w, 16) for w in re.findall(r'0x([0-9a-f]{8})u,', table)]


def spans(path):
    """Independent of the generator: (flag, register, length) of every A6XX_PROTECT_ line of a730_protect_regs and
    None for each bare zero, read line by line from the raw source."""
    inside, found = False, []
    for line in Path(path).read_text().split('\n'):
        if line.startswith('static const u32 a730_protect_regs[]'):
            inside = True
        elif inside and line.startswith('};'):
            break
        elif inside:
            call = re.fullmatch(r'\s*A6XX_PROTECT_(NORDWR|RDONLY)\((0x[0-9a-f]+), (0x[0-9a-f]+)\),', line)
            if call:
                found.append((call.group(1) == 'NORDWR', int(call.group(2), 16), int(call.group(3), 16)))
            elif re.fullmatch(r'\s*0,', line):
                found.append(None)
    return found


def word_of(span):
    return 0 if span is None else (int(span[0]) << 31) | (span[2] << 18) | span[1]


_PINNED = []


def pinned_dir():
    """The directory holding the four pinned blobs, fetching them once when absent; (None, why) when impossible."""
    if _PINNED:
        return _PINNED[0]
    adreno = LINUX_ROOT / gen.ADRENO_DIR
    result = (adreno, None)
    for name in gen.PIN_SHA256:
        if (adreno / name).is_file():
            continue
        if os.environ.get('A7XX_PROTECT_OFFLINE'):
            result = (None, f'{name} is not in the pinned checkout and A7XX_PROTECT_OFFLINE is set')
            break
        url = f'https://raw.githubusercontent.com/torvalds/linux/{gen.PIN_COMMIT}/{gen.ADRENO_DIR}/{name}'
        try:
            with urllib.request.urlopen(url, timeout=30) as reply:
                data = reply.read()
        except OSError as error:
            result = (None, f'cannot fetch {name} ({error}); the network may be absent')
            break
        adreno.mkdir(parents=True, exist_ok=True)
        (adreno / name).write_bytes(data)
    _PINNED.append(result)
    return result


def run_cli(*args, expect=None):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = gen.main([str(a) for a in args])
    if expect is not None:
        assert code == expect, f'exit {code}, wanted {expect}: {err.getvalue()}'
    return code, out.getvalue(), err.getvalue()


@contextlib.contextmanager
def workdir():
    BUILD.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=BUILD, prefix='test-gen-a7xx-') as scratch:
        yield Path(scratch)


def write_root(base, texts):
    adreno = base / gen.ADRENO_DIR
    adreno.mkdir(parents=True)
    for name, text in texts.items():
        (adreno / name).write_text(text)
    return base


def c_decode(header_dir, indexes):
    """Compile the generated header with strict flags and print COUNT, COUNT_MAX and the decoders of some words."""
    cc = shutil.which('cc') or shutil.which('clang')
    if not cc:
        raise unittest.SkipTest('no C compiler on PATH')
    probes = ''.join(
        f'printf("%u %u %u\\n", ADRENO_A7XX_PROTECT_REG(adreno_a7xx_protect[{i}]), '
        f'ADRENO_A7XX_PROTECT_LEN(adreno_a7xx_protect[{i}]), '
        f'ADRENO_A7XX_PROTECT_NORDWR(adreno_a7xx_protect[{i}]));\n' for i in indexes)
    program = ('#include <stdio.h>\n#include "' + HEADER_NAME + '"\nint main(void)\n{\n'
               'printf("%u %u %u\\n", ADRENO_A7XX_PROTECT_COUNT, ADRENO_A7XX_PROTECT_COUNT_MAX, '
               'ADRENO_A7XX_PROTECT_LAST_SPAN_INF);\n' + probes + 'return 0;\n}\n')
    with workdir() as scratch:
        (scratch / 'probe.c').write_text(program)
        built = subprocess.run([cc, '-std=c11', '-Wall', '-Wextra', '-Werror', '-I', str(header_dir),
                                '-o', str(scratch / 'probe'), str(scratch / 'probe.c')],
                               capture_output=True, text=True)
        assert built.returncode == 0, f'the header does not compile cleanly: {built.stderr[-1500:]}'
        done = subprocess.run([str(scratch / 'probe')], check=True, capture_output=True, text=True)
    return [tuple(int(v) for v in line.split()) for line in done.stdout.splitlines()]


class Encoding(unittest.TestCase):
    def test_reference_fixture_encodes_by_hand_computed_words(self):
        header = build()
        self.assertEqual(words_of(header), GOOD_WORDS)
        self.assertIn('RDONLY 0x00010-0x00013', header)
        self.assertIn('NORDWR 0x00200-end of register space', header)

    def test_extreme_register_and_length_values_fill_the_word_exactly(self):
        items = ['A6XX_PROTECT_RDONLY(0x3ffff, 0x1fff)', 'A6XX_PROTECT_NORDWR(0x00001, 0x0001)',
                 'A6XX_PROTECT_RDONLY(0x0, 0x0)', 'A6XX_PROTECT_NORDWR(0x3ffff, 0x1fff)']
        header = build(**{'a7xx_catalog.c': catalog(tables=(('fx_protect', items, 4),))})
        self.assertEqual(words_of(header), [0x7fffffff, 0x80040001, 0, 0xffffffff])
        self.assertNotIn('strictly increase', header)

    def test_the_word_comes_from_the_macro_body_not_from_a_built_in_layout(self):
        same_layout_other_spelling = gpu_header(nordwr='(0x80000000 | (((_len) & 0x1fff) << 18) | ((_reg) & 0x3ffff))')
        self.assertEqual(words_of(build(**{'a6xx_gpu.h': same_layout_other_spelling})), GOOD_WORDS)

    def test_macro_declared_over_continuation_lines_is_read_whole(self):
        split = gpu_header(nordwr='((1 << 31) | \\\n\t(((_len) & 0x3fff) << 18) | \\\n\t((_reg) & 0x3ffff))')
        self.assertEqual(words_of(build(**{'a6xx_gpu.h': split})), GOOD_WORDS)

    def test_zero_placeholders_are_kept_and_never_count_as_duplicates(self):
        items = ['A6XX_PROTECT_RDONLY(0x00010, 0x0003)', '0', '0x0', '0', 'A6XX_PROTECT_NORDWR(0x00200, 0x0000)']
        header = build(**{'a7xx_catalog.c': catalog(tables=(('fx_protect', items, 5),))})
        self.assertEqual(words_of(header), [0x000c0010, 0, 0, 0, 0x80000200])
        self.assertEqual(header.count('zero: not written'), 3)

    def test_table_shorter_than_the_slot_limit_is_accepted(self):
        header = build(**{'a7xx_catalog.c': catalog(tables=(('fx_protect', GOOD_ITEMS, 48),))})
        self.assertIn('#define ADRENO_A7XX_PROTECT_COUNT 4u', header)
        self.assertIn('#define ADRENO_A7XX_PROTECT_COUNT_MAX 48u', header)
        self.assertIn('CP_PROTECT(47)', header)

    def test_sorted_observation_is_stated_only_when_it_is_true(self):
        self.assertIn('strictly increase', build())
        unsorted = ['A6XX_PROTECT_NORDWR(0x00200, 0x0000)', 'A6XX_PROTECT_NORDWR(0x00100, 0x0000)']
        self.assertNotIn('strictly increase', build(**{'a7xx_catalog.c': catalog(tables=(('fx_protect', unsorted, 2),))}))

    def test_attribution_is_read_from_the_inputs_not_typed_into_the_tool(self):
        header = build()
        self.assertIn('Copyright (C) 2001 Example Org', header)
        self.assertIn('Author: A. Writer <writer@example.org>', header)
        self.assertIn('Copyright (c) 2002 Example Org', header)
        self.assertNotIn('Red Hat', header)
        self.assertNotIn('Linux Foundation', header)

    def test_output_is_deterministic(self):
        self.assertEqual(build(), build())

    def test_unpinned_output_is_marked_and_says_so(self):
        header = build()
        self.assertIn('UNPINNED', header)
        self.assertIn('never be committed', header)

    def test_left_shift_beyond_the_word_is_refused(self):
        with self.assertRaisesRegex(gen.Refusal, 'unsupported construct'):
            build(**{'a6xx_gpu.h': gpu_header(nordwr='((1 << 40) | ((_reg) & 0x3ffff))')})


class Selection(unittest.TestCase):
    TWO = (('first_protect', ['A6XX_PROTECT_NORDWR(0x00001, 0x0000)', 'A6XX_PROTECT_NORDWR(0x00002, 0x0000)'], 2),
           ('second_protect', GOOD_ITEMS, 4))

    def test_the_chip_entry_decides_which_table_is_used(self):
        entries = (('0x11', 'first_protect'), (CHIP, 'second_protect'))
        text = catalog(tables=self.TWO, entries=entries)
        self.assertEqual(words_of(build(**{'a7xx_catalog.c': text})), GOOD_WORDS)
        self.assertEqual(words_of(build(chip='0x11', **{'a7xx_catalog.c': text})), [0x80000001, 0x80000002])

    def test_entry_listing_several_ids_resolves_each_of_them(self):
        text = catalog(tables=self.TWO, entries=((f'0x11, {CHIP}', 'second_protect'),))
        self.assertEqual(words_of(build(**{'a7xx_catalog.c': text})), GOOD_WORDS)
        self.assertEqual(words_of(build(chip='0x11', **{'a7xx_catalog.c': text})), GOOD_WORDS)

    def test_entry_without_a_protect_pointer_does_not_borrow_the_next_ones(self):
        text = catalog(tables=self.TWO, entries=((CHIP, None), ('0x11', 'first_protect')))
        with self.assertRaisesRegex(gen.Refusal, 'has 0 .protect pointers'):
            build(**{'a7xx_catalog.c': text})

    def test_unknown_chip_is_refused(self):
        with self.assertRaisesRegex(gen.Refusal, 'not in the catalog'):
            build(chip='0x12345678')

    def test_chip_listed_twice_is_refused(self):
        text = catalog(entries=((CHIP, 'fx_protect'), (CHIP, 'fx_protect')))
        with self.assertRaisesRegex(gen.Refusal, 'more than one entry'):
            build(**{'a7xx_catalog.c': text})

    def test_comments_and_strings_cannot_inject_entries_or_arrays(self):
        decoy = ('\t/* A6XX_PROTECT_NORDWR(0x00999, 0x0000), } ; */\n\t// A6XX_PROTECT_NORDWR(0x00998, 0x0000),\n')
        text = catalog(inside=decoy)
        text += '/* static const u32 fx_protect_regs[] = { A6XX_PROTECT_NORDWR(0x00997, 0x0000), }; */\n'
        text += 'static const char *note = "static const u32 fx_protect_regs[] = {";\n'
        header = build(**{'a7xx_catalog.c': text})
        self.assertEqual(words_of(header), GOOD_WORDS)


class Refusals(unittest.TestCase):
    def refused(self, fragment, **replacements):
        with self.assertRaisesRegex(gen.Refusal, fragment):
            build(**replacements)

    def test_missing_array_is_refused(self):
        self.refused(r'fx_protect_regs\[\]: expected exactly one definition, found 0',
                     **{'a7xx_catalog.c': swap(catalog(), 'fx_protect_regs[]', 'other_regs[]')})

    def test_array_defined_twice_is_refused(self):
        text = catalog()
        text += 'static const u32 fx_protect_regs[] = { 0, };\n'
        self.refused('found 2', **{'a7xx_catalog.c': text})

    def test_truncated_table_is_refused_wherever_it_is_cut(self):
        text = catalog(inside='\t/* note */\n', entries_first=True)
        cuts = {
            'right after the opening brace': (text.index('{\n', text.index('fx_protect_regs')) + 1, 'no closing brace'),
            'inside an initialiser': (text.index('0x00100') + 3, 'no closing brace'),
            'before the closing brace': (text.index('};', text.index('fx_protect_regs')), 'no closing brace'),
            'between brace and semicolon': (text.index('};', text.index('fx_protect_regs')) + 1, 'not followed by a semicolon'),
            'before the limit declaration': (text.index('DECLARE_ADRENO_PROTECT'), 'found 0'),
            'inside the limit declaration': (text.index('DECLARE_ADRENO_PROTECT') + 25, 'found 0'),
            'inside a comment': (text.index('note') + 2, 'unterminated comment'),
        }
        self.assertEqual(words_of(build(**{'a7xx_catalog.c': text})), GOOD_WORDS)
        for where, (cut, fragment) in cuts.items():
            with self.subTest(cut=where):
                self.refused(fragment, **{'a7xx_catalog.c': text[:cut]})

    def test_truncation_inside_the_chip_listing_is_refused(self):
        text = catalog()
        for where, cut in {'before the entry': text.index('ADRENO_CHIP_IDS'),
                           'inside the id list': text.index('ADRENO_CHIP_IDS') + 20,
                           'before the pointer': text.index('.protect')}.items():
            with self.subTest(cut=where):
                self.refused('not in the catalog|has 0 .protect pointers', **{'a7xx_catalog.c': text[:cut]})

    def test_unterminated_string_is_refused(self):
        self.refused('unterminated string', **{'a7xx_catalog.c': catalog() + 'char *s = "abc\n'})

    def test_count_overflow_is_refused(self):
        self.refused('overflow the 3 CP_PROTECT slots',
                     **{'a7xx_catalog.c': catalog(tables=(('fx_protect', GOOD_ITEMS, 3),))})

    def test_missing_limit_declaration_is_refused(self):
        self.refused(r'DECLARE_ADRENO_PROTECT\(fx_protect, N\): expected exactly one declaration, found 0',
                     **{'a7xx_catalog.c': swap(catalog(), 'DECLARE_ADRENO_PROTECT(fx_protect, 4);', '')})

    def test_limit_declared_before_the_table_is_refused(self):
        text = swap(catalog(), 'DECLARE_ADRENO_PROTECT(fx_protect, 4);\n', '')
        self.refused('comes before the table', **{'a7xx_catalog.c': 'DECLARE_ADRENO_PROTECT(fx_protect, 4);\n' + text})

    def test_unknown_macro_is_refused(self):
        items = ['ADRENO_PROTECT_RW(0x00010, 4)'] + GOOD_ITEMS[1:]
        self.refused('unknown macro ADRENO_PROTECT_RW',
                     **{'a7xx_catalog.c': catalog(tables=(('fx_protect', items, 4),))})

    def test_initialisers_that_are_not_plain_calls_or_zero_are_refused(self):
        for item in ('REG_FOO', '0x12345678', 'A6XX_PROTECT_NORDWR(REG_FOO, 0)', 'A6XX_PROTECT_NORDWR(0x10 + 1, 0)',
                     'A6XX_PROTECT_NORDWR(0x10, 0, 1)', 'A6XX_PROTECT_NORDWR(010, 0)', '(0x80000010)'):
            with self.subTest(item=item):
                items = [item] + GOOD_ITEMS[1:]
                self.refused('unrecognised initialiser',
                             **{'a7xx_catalog.c': catalog(tables=(('fx_protect', items, 4),))})

    def test_empty_initialiser_and_empty_table_are_refused(self):
        self.refused('empty initialiser', **{'a7xx_catalog.c': catalog(tables=(('fx_protect', ['0', '', GOOD_ITEMS[1]], 4),))})
        self.refused('empty table', **{'a7xx_catalog.c': catalog(tables=(('fx_protect', [], 4),))})

    def test_duplicate_entry_is_refused(self):
        items = GOOD_ITEMS + [GOOD_ITEMS[1]]
        self.refused('duplicate entry', **{'a7xx_catalog.c': catalog(tables=(('fx_protect', items, 8),))})

    def test_zero_final_entry_is_refused(self):
        items = GOOD_ITEMS[:3]
        self.refused('last entry is a zero placeholder', **{'a7xx_catalog.c': catalog(tables=(('fx_protect', items, 4),))})

    def test_length_that_would_alias_the_nordwr_bit_is_refused(self):
        items = ['A6XX_PROTECT_RDONLY(0x00010, 0x2000)'] + GOOD_ITEMS[1:]
        self.refused('does not decode', **{'a7xx_catalog.c': catalog(tables=(('fx_protect', items, 4),))})

    def test_register_beyond_eighteen_bits_is_refused(self):
        items = ['A6XX_PROTECT_NORDWR(0x40000, 0x0000)'] + GOOD_ITEMS[1:]
        self.refused('does not decode', **{'a7xx_catalog.c': catalog(tables=(('fx_protect', items, 4),))})

    def test_macro_layout_drift_is_refused(self):
        items = ['A6XX_PROTECT_RDONLY(0x00010, 0x0003)', 'A6XX_PROTECT_NORDWR(0x01100, 0x0005)',
                 'A6XX_PROTECT_RDONLY(0x00200, 0x0040)', 'A6XX_PROTECT_NORDWR(0x00300, 0x0000)']
        text = catalog(tables=(('fx_protect', items, 4),))
        self.assertEqual(words_of(build(**{'a7xx_catalog.c': text})), [0x000c0010, 0x80141100, 0x01000200, 0x80000300])
        drifts = {
            'flag moved to bit 30': gpu_header(nordwr=NORDWR_BODY.replace('1 << 31', '1 << 30')),
            'length shifted by 19': gpu_header(nordwr=NORDWR_BODY.replace('<< 18', '<< 19')),
            'rdonly length shifted by 17': gpu_header(rdonly=RDONLY_BODY.replace('<< 18', '<< 17')),
            'rdonly sets the flag': gpu_header(rdonly='((1 << 31) | ' + RDONLY_BODY[1:]),
            'parameters swapped': gpu_header(params='_len, _reg'),
            'register mask narrowed': gpu_header(nordwr=NORDWR_BODY.replace('0x3ffff', '0xfff')),
        }
        for what, header in drifts.items():
            with self.subTest(drift=what):
                self.refused('does not decode', **{'a7xx_catalog.c': text, 'a6xx_gpu.h': header})

    def test_macro_bodies_that_are_not_plain_integer_expressions_are_refused(self):
        for what, body in {'call': 'ilog2(_len)', 'sum': '((_reg) + (_len))', 'suffix': '(0x80000000u | (_reg))',
                           'unknown name': '(FLAG | (_reg))', 'cast': '((u32)(_reg))'}.items():
            with self.subTest(body=what):
                self.refused('plain integer expression|unsupported construct', **{'a6xx_gpu.h': gpu_header(nordwr=body)})

    def test_missing_or_repeated_macro_is_refused(self):
        self.refused('A6XX_PROTECT_RDONLY: not defined',
                     **{'a6xx_gpu.h': swap(gpu_header(), '#define A6XX_PROTECT_RDONLY', '#define A6XX_PROTECT_RDONLYX')})
        self.refused('defined more than once', **{'a6xx_gpu.h': gpu_header() + gpu_header().split('\n\n')[1]})

    def test_licence_other_than_gpl_2_only_is_refused_in_every_input(self):
        for licence in ('GPL-2.0-or-later', 'GPL-2.0+', 'MIT', 'GPL-2.0-only OR MIT', 'GPL-2.0-only WITH Linux-syscall-note'):
            for name, text in inputs().items():
                with self.subTest(licence=licence, file=name):
                    other = re.sub(r'SPDX-License-Identifier: [^\n*]+?( \*/)?\n',
                                   lambda m: f'SPDX-License-Identifier: {licence}{m.group(1) or ""}\n', text, count=1)
                    self.assertNotEqual(other, text)
                    self.refused(f'{name}: licence .* is not GPL-2.0-only', **{name: other})

    def test_deprecated_spelling_of_the_same_licence_is_accepted(self):
        header = build()
        self.assertIn('a6xx_gpu.h (GPL-2.0):', header)
        self.assertIn('a7xx_catalog.c (GPL-2.0-only):', header)

    def test_missing_licence_or_copyright_is_refused(self):
        self.refused('is not GPL-2.0-only', **{'a7xx_catalog.c': catalog(head='/* nothing here */\n')})
        self.refused('no Copyright line', **{'a7xx_catalog.c': catalog(head='// SPDX-License-Identifier: GPL-2.0-only\n')})

    def test_consumption_semantics_are_verified_not_assumed(self):
        removals = {
            'CP_PROTECT_CNTL write': 'gpu_write(gpu, REG_A6XX_CP_PROTECT_CNTL,',
            'LAST_SPAN_INF': 'A6XX_CP_PROTECT_CNTL_LAST_SPAN_INF_RANGE',
            'loop bound': 'protect->count - 1',
            'zero skip': 'if (protect->regs[i])',
            'final slot': 'REG_A6XX_CP_PROTECT(protect->count_max - 1)',
        }
        for what, old in removals.items():
            with self.subTest(removed=what):
                new = {'CP_PROTECT_CNTL write': 'gpu_write(gpu, REG_A6XX_OTHER,', 'zero skip': 'if (1)'}.get(what, 'X')
                self.refused('no longer', **{'a6xx_gpu.c': swap(GPU_HEADER_HEAD + CONSUMER, old, new)})

    def test_consumer_function_must_be_present_and_closed(self):
        self.refused('a6xx_set_cp_protect\\(\\): expected exactly one definition, found 0',
                     **{'a6xx_gpu.c': GPU_HEADER_HEAD + 'static void other(void) {}\n'})
        self.refused('body is not closed', **{'a6xx_gpu.c': GPU_HEADER_HEAD + CONSUMER[:-30]})

    def test_declare_macro_shape_is_verified(self):
        for what, old, new in (('array name', 'name ## _regs, \\', 'name ## _other, \\'),
                               ('count', 'ARRAY_SIZE(name ## _regs),', 'ARRAY_SIZE(name ## _regs) + 1,'),
                               ('limit', '.count_max = __count_max,', '.count_max = 48,')):
            with self.subTest(changed=what):
                self.refused('DECLARE_ADRENO_PROTECT no longer',
                             **{'adreno_gpu.h': CATALOG_HEAD + swap(DECLARE_MACRO, old, new)})

    def test_every_input_must_be_supplied(self):
        texts = inputs()
        del texts['a6xx_gpu.c']
        with self.assertRaisesRegex(gen.Refusal, 'expected exactly the inputs'):
            gen.generate_from_blobs({n: t.encode() for n, t in texts.items()}, unpinned=True)


class Command(unittest.TestCase):
    def fixture_root(self, base, **changes):
        texts = inputs()
        texts.update(changes)
        return write_root(base / 'linux', texts)

    def test_sha256_pin_is_enforced_unless_the_run_is_explicitly_unpinned(self):
        with workdir() as base:
            root = self.fixture_root(base)
            code, _, err = run_cli('--linux-root', root, '--out', base / 'out.h')
            self.assertEqual(code, 2)
            self.assertIn('sha256', err)
            self.assertFalse((base / 'out.h').exists())
            run_cli('--linux-root', root, '--out', base / 'out.h', '--unpinned', expect=0)
            self.assertTrue((base / 'out.h').is_file())

    def test_a_refusal_writes_nothing_and_leaves_an_existing_file_alone(self):
        with workdir() as base:
            root = self.fixture_root(base, **{'a7xx_catalog.c': catalog()[:-40]})
            out = base / 'out.h'
            out.write_text('previous\n')
            code, _, err = run_cli('--linux-root', root, '--out', out, '--unpinned')
            self.assertEqual(code, 2)
            self.assertIn('REFUSED', err)
            self.assertEqual(out.read_text(), 'previous\n')

    def test_missing_input_is_refused_with_a_hint(self):
        with workdir() as base:
            code, _, err = run_cli('--linux-root', base / 'nothing', '--out', base / 'out.h')
            self.assertEqual(code, 2)
            self.assertIn('cannot read', err)

    def test_check_mode_reports_missing_stale_and_current(self):
        with workdir() as base:
            root, out = self.fixture_root(base), base / 'out.h'
            run_cli('--linux-root', root, '--out', out, '--unpinned', '--check', expect=1)
            run_cli('--linux-root', root, '--out', out, '--unpinned', expect=0)
            run_cli('--linux-root', root, '--out', out, '--unpinned', '--check', expect=0)
            out.write_text(out.read_text().replace('0x80000100u', '0x80000101u'))
            run_cli('--linux-root', root, '--out', out, '--unpinned', '--check', expect=1)
            self.assertIn('0x80000101u', out.read_text())

    def test_written_file_is_deterministic_across_runs(self):
        with workdir() as base:
            root = self.fixture_root(base)
            first, second = base / 'one.h', base / 'two.h'
            run_cli('--linux-root', root, '--out', first, '--unpinned', expect=0)
            run_cli('--linux-root', root, '--out', second, '--unpinned', expect=0)
            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_generated_header_compiles_strictly_and_its_decoders_agree(self):
        with workdir() as base:
            header = build()
            (base / HEADER_NAME).write_text(header)
            rows = c_decode(base, (0, 1, 3))
        self.assertEqual(rows, [(4, 4, 1), (0x10, 3, 0), (0x100, 0, 1), (0x200, 0, 1)])

    def test_nothing_in_the_repository_includes_or_builds_the_generated_header(self):
        own = {SOURCE, Path(__file__).resolve(), COMMITTED}
        suffixes = {'.c', '.h', '.cc', '.cpp', '.m', '.mm', '.s', '.mk', '.sh', '.json', '.py', '.toml', '.cmake', '.txt'}
        offenders = []
        for folder, dirs, names in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in {'.git', 'build', 'downloads', '__pycache__', 'node_modules'}]
            for name in names:
                path = Path(folder) / name
                if path in own or (path.suffix.lower() not in suffixes and name != 'Makefile'):
                    continue
                if path.stat().st_size < 4_000_000 and 'adreno-a7xx-protect-tables' in path.read_text(errors='ignore'):
                    offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [], 'the GPL-2.0-only header must stay included by nothing')

    @unittest.skipIf(NESTED, 'already the nested run')
    def test_offline_run_skips_the_pinned_group_and_still_passes_the_rest(self):
        with workdir() as base:
            env = dict(os.environ, A7XX_PROTECT_NESTED='1', A7XX_PROTECT_OFFLINE='1',
                       A7XX_PROTECT_LINUX_ROOT=str(base / 'empty'), PYTHONDONTWRITEBYTECODE='1')
            done = subprocess.run([sys.executable, str(Path(__file__).resolve())], env=env, capture_output=True,
                                  text=True, timeout=600)
        self.assertEqual(done.returncode, 0, done.stderr[-2000:])
        self.assertRegex(done.stdout, r'(?m)^SKIP .*Pinned.*A7XX_PROTECT_OFFLINE')
        self.assertRegex(done.stderr, r'OK \(skipped=\d+\)')


class Pinned(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.adreno, why = pinned_dir()
        if cls.adreno is None:
            raise unittest.SkipTest(why)
        cls.texts = {name: (cls.adreno / name).read_text() for name in gen.PIN_SHA256}
        cls.header = gen.generate(cls.adreno)

    def test_inputs_match_the_recorded_sha256_and_the_upstream_git_blob_ids(self):
        for name, want in gen.PIN_SHA256.items():
            data = (self.adreno / name).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), want, name)
            self.assertEqual(hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest(), BLOB_IDS[name], name)

    def test_first_and_last_words_match_the_hand_transcription(self):
        words = words_of(self.header)
        self.assertEqual((words[0], words[-1]), (GOLDEN_FIRST, GOLDEN_LAST))

    def test_table_shape_is_what_the_pinned_driver_declares(self):
        words = words_of(self.header)
        self.assertEqual(len(words), 48)
        self.assertIn('DECLARE_ADRENO_PROTECT(a730_protect, 48)', self.header)
        self.assertEqual([i for i, w in enumerate(words) if w == 0], [45, 46])
        self.assertEqual(len(set(words)) - 1, 46)

    def test_every_word_matches_a_second_line_by_line_reading_of_the_source(self):
        spans_found = spans(self.adreno / 'a7xx_catalog.c')
        self.assertEqual(len(spans_found), 48)
        self.assertEqual(words_of(self.header), [word_of(s) for s in spans_found])

    def test_both_a7xx_x1_85_and_a740_use_the_same_table(self):
        for chip, line in ((0x43050c01, 376), (0x43050a01, 341)):
            with self.subTest(chip=hex(chip)):
                header = gen.generate(self.adreno, chip_id=chip)
                self.assertIn(f'chip id {chip:#x} is listed at a7xx_catalog.c:{line}', header)
                self.assertEqual(words_of(header), words_of(self.header))

    def test_pinned_header_is_not_marked_unpinned_and_says_what_it_is(self):
        self.assertNotIn('UNPINNED', self.header)
        self.assertTrue(self.header.startswith('/* SPDX-License-Identifier: GPL-2.0-only */\n'))
        for needle in (gen.PIN_TAG, gen.PIN_COMMIT, 'Red Hat', 'Linux Foundation', 'UNIMPLEMENTED', 'EXPERIMENTAL',
                       'a730_protect', 'LAST_SPAN_INF', *gen.PIN_SHA256.values()):
            self.assertIn(needle, self.header)

    def test_each_input_is_pinned_on_its_own(self):
        with workdir() as base:
            for name in gen.PIN_SHA256:
                with self.subTest(tampered=name):
                    texts = dict(self.texts)
                    texts[name] += '\n'
                    root = write_root(base / name, texts)
                    code, _, err = run_cli('--linux-root', root, '--out', base / f'{name}.h')
                    self.assertEqual(code, 2)
                    self.assertIn(f'{name}: sha256', err)

    def test_generator_embeds_none_of_the_upstream_table(self):
        source = SOURCE.read_text()
        self.assertIn('SPDX-License-Identifier: MIT', source)
        for span in spans(self.adreno / 'a7xx_catalog.c'):
            if span is not None:
                self.assertNotIn(f'0x{span[1]:05x}', source)
                self.assertNotIn(f'{word_of(span):#010x}', source)

    def test_committed_header_is_what_the_generator_produces_now(self):
        if not COMMITTED.is_file():
            self.fail(f'{COMMITTED.name} has not been generated')
        self.assertEqual(COMMITTED.read_text(), self.header)
        self.assertEqual(run_cli('--linux-root', LINUX_ROOT, '--check')[0], 0)

    def test_committed_header_compiles_and_decodes_its_golden_words(self):
        rows = c_decode(COMMITTED.parent, (0, 47))
        self.assertEqual(rows[0], (48, 48, 1))
        self.assertEqual(rows[1:], [(0x00000, 0x4ff, 0), (0x1f8c0, 0, 1)])

    def test_committed_header_carries_no_machine_identifying_text(self):
        text = COMMITTED.read_text()
        # Assembled from pieces so this file does not match a repository scan for the same prefixes.
        needles = ['/' + 'Users' + '/', '/' + 'home' + '/', 'C:' + '\\']
        home = str(Path.home())
        if len(home) > 6:
            needles.append(home)
        for needle in needles:
            self.assertNotIn(needle, text)


class Report(unittest.TextTestResult):
    def addSkip(self, test, reason):
        print(f'SKIP {test}: {reason}')
        super().addSkip(test, reason)


if __name__ == '__main__':
    unittest.main(testRunner=unittest.TextTestRunner(resultclass=Report, verbosity=1))
