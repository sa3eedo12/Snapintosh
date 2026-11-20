#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests of tools/sp11_dispsnap_parse.py: the host side of the q1n1 payload `dispsnap` (page lines in, the study's verdict out).

    python3 tools/test-sp11-dispsnap-parse.py
    Q1N1_SP11_TREE=<a q1n1 worktree with payloads/dispsnap.c> python3 tools/test-sp11-dispsnap-parse.py     # also the pages of the payload's host model

Nothing here touches hardware or reads any private input. The register VALUES are invented here and say nothing about any unit. The pages are of two kinds: pages
written in this file from the study's own table (an independent encoder of what the payload prints, so that the parser is not tested against itself) and, when
Q1N1_SP11_TREE names a q1n1 tree that has the payload, the pages its host model prints for each scenario, which also hold the payload's register table to the
study's dump list (docs/SP11-DISPLAY-STUDY.md section 8). Without clang the native classes report SKIP; without the tree the fork classes do.
Evidence: EXPERIMENTAL (host only)."""
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import sp11_dispsnap_parse as dp  # noqa: E402

SANITIZE = ('-fsanitize=address,undefined', '-fno-sanitize-recover=undefined')
HOME_PREFIXES = ('/' + 'Users' + '/', '/' + 'home' + '/')
TREE = os.environ.get('Q1N1_SP11_TREE')
EV = dict(GATES=1, VERSION=2, TIMING=4, GEOMETRY=8, ROUTING=16, COUNTERS=32)
CORE = EV['GATES'] | EV['VERSION'] | EV['TIMING'] | EV['GEOMETRY'] | EV['ROUTING']
ALL = CORE | EV['COUNTERS']
P = dp.PREFIX
_BUILT = {}


def built():
    if 'b' not in _BUILT:
        _BUILT['b'] = dp.build_check('tests', SANITIZE)
        if _BUILT['b'] is None:
            raise unittest.SkipTest('clang not found')
    return _BUILT['b']


def _judge(text, gop=None):
    """(verdict, why with underscores, detail, evidence) of a transcript: the tool's steps with the C names (hyphens) written with underscores for the comparisons below."""
    exe, layout, names = built()
    page = dp.parse_page(text)
    registers, ctl, params = dp.study_tables()
    pbytes, snap = dp.build_inputs(page, registers, ctl, params, layout, gop)
    verdict, why, detail, evidence = dp.run_check(exe, pbytes, snap)
    return names['verdict'][verdict].upper(), names['why'][why].replace('-', '_'), detail, evidence


def num(value):
    return str(value) if value < 10 else f'0X{value:X}'


def intf_words(width, height, widebus=False, hs=32, hbp=48, hfp=80, vs=5, vbp=30, vfp=35):
    """The INTF words as Linux programs a DP interface (dpu_hw_intf.c:128-257, with the porch shift and the halving of dpu_encoder_phys_vid.c:106-123), written out here."""
    if widebus:
        hbp, hfp, vbp, vfp = hbp + hfp, 0, vbp + vfp, 0
        hs, hbp, hfp, width = hs >> 1, hbp >> 1, hfp >> 1, width >> 1
    ht, vt = hs + hbp + width + hfp, vs + vbp + height + vfp
    v0 = (vs + vbp) * ht + hs + hbp
    hctl = (hs + hbp + width - 1) << 16 | (hs + hbp)
    return dict(HS=ht << 16 | hs, VP=ht * vt, DVS=v0, DVE=vt * ht - 1 - hfp - vfp * ht, AVS=v0, AVE=v0 + height * ht - 1, DHC=hctl, AHC=hctl,
                CFG=0x60000000, CFG2=0x11 if widebus else 0x10)


def good_values(width=2880, height=1920):
    v = dict(DAHB=1, GDSC=0x8022f000, AHBC=1, MDPC=1, VER=9 << 28 | 2 << 16, ST=1, EN=1, FLC=3, FRA=1000, C0=1 << 5, C1=0, C2=0, C3=0, C4=0, C5=0)
    v.update(intf_words(width, height))
    return v


def clock(key, value):
    return f'{key}={num(value)}' + ('.ON' if value & 1 and not value >> 31 else '.OFF' if value >> 31 else '.ODD')


def page_lines(values, gop='GOP W=2880 H=1920 STRIDE=2880 FMT=1', map_lines=None, frb=1480, dt=154176000, hz=0x124F800, gated=(), end=True):
    """The lines the payload prints for `values` ({key: int | 'FAULT:6' | 'SKIP' | 'NOFLC'}), in its order and its notation, from the study's own table."""
    def f(key):
        value = values.get(key)
        if value is None:
            return None
        if isinstance(value, str):
            return f'{key}={value}'
        return clock(key, value) if key in ('DAHB', 'AHBC', 'MDPC') else (f'{key}={num(value)}.' + ('ON' if value >> 31 else 'OFF') if key == 'GDSC' else f'{key}={num(value)}')

    def line(head, *keys, extra=()):
        fields = [x for x in (f(k) for k in keys) if x is not None]
        return f'{head} ' + ' '.join(fields + list(extra)) if fields else None

    out = [f'READ ONLY EL=2 HZ=0X{hz:X} GAP=8 S', 'KEY=RAW WORD .ON/.OFF=BIT 31 .ODD=BIT 31 NOT TRUSTED NO STORE NO AUX NO IRQ STATUS']
    if gop:
        out.append(gop)
    out += map_lines or ['MAP GCC=0X100000 DISPCC=0XAF00000 MDSS=0XAE00000 DPU=0XAE01000', 'MAP INTF=5 AT=0X39000 CTL=0X15000 STEP=0X1000 REG=0XF4']
    body = [('GCC', ('DAHB',)), ('DISPCC', ('GDSC',)), ('DISPCC', ('AHBC', 'MDPC')), ('MDSS', ('VER',)), ('INTF', ('ST', 'EN', 'CFG', 'CFG2')), ('INTF', ('HS', 'VP', 'DVS', 'DVE')),
            ('INTF', ('AVS', 'AVE', 'DHC', 'AHC')), ('INTF', ('FLC', 'FRA')), ('CTL', ('C0', 'C1', 'C2')), ('CTL', ('C3', 'C4', 'C5'))]
    for head, keys in body:
        if head in gated:
            if not any(l.endswith(f'{head} GATED BY DISP=OFF') for l in out):
                out.append(f'{head} GATED BY DISP=OFF')
            continue
        text = line(head, *keys, extra=['SC=0', 'HW=0'] if keys == ('GDSC',) else [])
        if text:
            out.append(text)
    if frb is not None:
        out.append(f'S2 INTF ST=1 EN=1 FRB={num(frb)}' + (f' DT=0X{dt:X}' if dt else ''))
    if end:
        out.append('END LOADS=32 FAULTS=0 SKIPS=0 BLK=0 CUTS=0 GAP_MS=8030 LINES=27')
    return out


def text_of(lines, noise=True):
    rows = []
    for i, l in enumerate(lines):
        if noise and i % 5 == 0:
            rows.append(f'UART> some other output {i}')
        rows.append(P + l)
    return '\n'.join(rows)


class Parse(unittest.TestCase):
    def test_a_good_page_is_read_back_value_by_value(self):
        page = dp.parse_page(text_of(page_lines(good_values())))
        self.assertEqual(page['hz'], 0x124F800)
        self.assertEqual(page['gop'], (2880, 1920, 2880, 1))
        self.assertEqual(page['map'], {'GCC': 0x100000, 'DISPCC': 0xAF00000, 'MDSS': 0xAE00000, 'DPU': 0xAE01000, 'INTF': 5, 'AT': 0x39000, 'CTL': 0x15000, 'STEP': 0x1000, 'REG': 0xF4})
        self.assertEqual(page['rows']['GDSC'], ('ok', 0x8022f000))
        self.assertEqual(page['rows']['C0'], ('ok', 0x20))
        self.assertEqual(page['rows']['FRA'], ('ok', 1000))
        self.assertEqual(page['frb'], ('ok', 1480))
        self.assertEqual(page['dt'], 154176000)
        self.assertEqual(set(page['rows']), (set(dp.KEYS) - {'FRB'}) | set(dp.CTL_KEYS))

    def test_tokens_that_are_not_values_are_not_attempted_or_aborted(self):
        v = good_values()
        v.update(ST='FAULT:6', EN='SKIP', CFG='NA', CFG2='BUDGET', FRA='NOFLC')
        page = dp.parse_page(text_of(page_lines(v)))
        self.assertEqual(page['rows']['ST'], ('fault', 6))
        self.assertEqual([page['rows'][k][0] for k in ('EN', 'CFG', 'CFG2', 'FRA')], ['not_tried'] * 4)

    def test_a_gated_block_is_recorded_and_has_no_values(self):
        page = dp.parse_page(text_of(page_lines(good_values(), gated=('MDSS', 'INTF', 'CTL'))))
        self.assertEqual(sorted(page['gated']), ['CTL', 'INTF', 'MDSS'])
        self.assertNotIn('VER', page['rows'])
        self.assertNotIn('C0', page['rows'])

    def test_lines_without_the_prefix_are_ignored_and_the_prefix_may_follow_other_text(self):
        text = 'noise\nGCC DAHB=1.ON\n12:00:01 ' + P + 'GCC DAHB=1.ON\n'
        self.assertEqual(dp.parse_page(text)['rows']['DAHB'], ('ok', 1))

    def test_unusable_pages_are_errors_and_never_verdicts(self):
        good = page_lines(good_values())
        cases = {
            'a key twice': good + ['INTF ST=1'],
            'a key under another line': good[:5] + ['GCC ST=1'] + good[5:],
            'not a number': [l.replace('DAHB=1.ON', 'DAHB=ONE') for l in good],
            'a value above 32 bits': [l.replace('DAHB=1.ON', 'DAHB=0X1FFFFFFFF') for l in good],
            'a MAP key twice': good + ['MAP GCC=0X100000'],
            'a header without HZ': ['READ ONLY EL=2'] + good[1:],
        }
        for name, lines in cases.items():
            with self.subTest(name):
                with self.assertRaises(dp.PageError):
                    dp.parse_page(text_of(lines, noise=False))


class Verdicts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        built()

    def verdict(self, lines, **kw):
        return _judge(text_of(lines), **kw)

    def test_the_good_page_is_active_with_every_evidence_class(self):
        self.assertEqual(self.verdict(page_lines(good_values())), ('ACTIVE', 'ok', 0, ALL))

    def test_the_gop_mode_is_the_pages_unless_given(self):
        lines = page_lines(good_values(2880, 1800), gop='GOP W=2880 H=1800 STRIDE=2880 FMT=1')
        self.assertEqual(self.verdict(lines)[:2], ('ACTIVE', 'ok'))
        self.assertEqual(self.verdict(lines, gop=(2880, 1920))[:2], ('INCONSISTENT', 'geometry_mismatch'))
        with self.assertRaises(dp.PageError):
            self.verdict(page_lines(good_values(), gop='GOP W=0 H=0 STRIDE=0 FMT=0'))
        with self.assertRaises(dp.PageError):
            self.verdict(page_lines(good_values(), gop=None))
        self.assertEqual(self.verdict(page_lines(good_values(), gop=None), gop=(2880, 1920))[:2], ('ACTIVE', 'ok'))

    def test_a_mode_that_is_not_the_gops_is_inconsistent(self):
        self.assertEqual(self.verdict(page_lines(good_values(2880, 1800)))[:2], ('INCONSISTENT', 'geometry_mismatch'))

    def test_the_counter_is_optional_evidence(self):
        v = good_values()
        v.update(FLC=0, FRA='NOFLC')
        self.assertEqual(self.verdict(page_lines(v, frb=None, dt=0)), ('ACTIVE', 'ok', 0, CORE))
        self.assertEqual(self.verdict(page_lines(good_values(), frb=None, dt=0))[3], CORE)

    def test_a_stalled_counter_and_a_too_fast_one_are_inconsistent(self):
        self.assertEqual(self.verdict(page_lines(good_values(), frb=1000))[:2], ('INCONSISTENT', 'counter_stalled'))
        self.assertEqual(self.verdict(page_lines(good_values(), frb=1000 + 8 * 2000))[:2], ('INCONSISTENT', 'counter_fast'))

    def test_a_gap_too_short_to_judge_the_counter_adds_nothing(self):
        self.assertEqual(self.verdict(page_lines(good_values(), frb=1000, dt=0x124F800 // 100))[3], CORE)

    def test_gates_that_read_off_are_inactive_and_an_unreachable_one_is_unknown(self):
        cases = [({'GDSC': 0x00222001}, 'INACTIVE', 'power_off'), ({'AHBC': 0x80000000}, 'INACTIVE', 'ahb_clock_off'), ({'MDPC': 0x80000000}, 'INACTIVE', 'mdp_clock_off'),
                 ({'DAHB': 0x80000000}, 'UNKNOWN', 'dispcc_no_access'), ({'DAHB': 0xffffffff}, 'UNKNOWN', 'all_ones')]
        for change, verdict, why in cases:
            with self.subTest(change=change):
                v = good_values()
                v.update(change)
                self.assertEqual(self.verdict(page_lines(v))[:2], (verdict, why))

    def test_a_blocked_page_is_unknown_not_inactive_and_never_active(self):
        v = good_values()
        for key in ('VER', 'ST', 'EN', 'CFG', 'CFG2', 'HS', 'VP', 'DVS', 'DVE', 'AVS', 'AVE', 'DHC', 'AHC', 'FLC', 'FRA', 'C0', 'C1', 'C2', 'C3', 'C4', 'C5'):
            v.pop(key)
        result = self.verdict(page_lines(v, gated=('MDSS', 'INTF', 'CTL'), frb=None, dt=0))
        self.assertEqual(result[0], 'UNKNOWN')
        self.assertNotEqual(result[1], 'ok')

    def test_an_aborted_word_is_unknown(self):
        for key in ('GDSC', 'VER', 'ST', 'EN', 'HS', 'C0'):
            with self.subTest(key=key):
                v = good_values()
                v[key] = 'FAULT:6'
                self.assertEqual(self.verdict(page_lines(v))[0], 'UNKNOWN')

    def test_a_stopped_timing_engine_is_inactive_and_disagreeing_words_are_inconsistent(self):
        v = good_values()
        v.update(EN=0, ST=0)
        self.assertEqual(self.verdict(page_lines(v))[:2], ('INACTIVE', 'timing_off'))
        v = good_values()
        v.update(EN=1, ST=0)
        self.assertEqual(self.verdict(page_lines(v))[:2], ('INCONSISTENT', 'enable_status'))
        v = good_values()
        v.update(HS=0, VP=0)
        self.assertEqual(self.verdict(page_lines(v))[:2], ('INCONSISTENT', 'no_timing'))

    def test_a_ctl_path_must_list_the_interface(self):
        v = good_values()
        v['C0'] = 0
        self.assertEqual(self.verdict(page_lines(v))[:2], ('INCONSISTENT', 'routing_mismatch'))
        v['C5'] = 1 << 5
        self.assertEqual(self.verdict(page_lines(v))[:2], ('ACTIVE', 'ok'))

    def test_a_payload_that_read_somewhere_else_than_the_study_says_is_inconsistent(self):
        wrong_gcc = ['MAP GCC=0X101000 DISPCC=0XAF00000 MDSS=0XAE00000 DPU=0XAE01000', 'MAP INTF=5 AT=0X39000 CTL=0X15000 STEP=0X1000 REG=0XF4']
        self.assertEqual(self.verdict(page_lines(good_values(), map_lines=wrong_gcc))[:2], ('INCONSISTENT', 'address_mismatch'))
        intf6 = ['MAP GCC=0X100000 DISPCC=0XAF00000 MDSS=0XAE00000 DPU=0XAE01000', 'MAP INTF=5 AT=0X3A000 CTL=0X15000 STEP=0X1000 REG=0XF4']
        self.assertEqual(self.verdict(page_lines(good_values(), map_lines=intf6))[:2], ('INCONSISTENT', 'address_mismatch'))
        index4 = ['MAP GCC=0X100000 DISPCC=0XAF00000 MDSS=0XAE00000 DPU=0XAE01000', 'MAP INTF=4 AT=0X39000 CTL=0X15000 STEP=0X1000 REG=0XF4']
        self.assertEqual(self.verdict(page_lines(good_values(), map_lines=index4))[:2], ('INCONSISTENT', 'intf_mismatch'))
        wrong_ctl = ['MAP GCC=0X100000 DISPCC=0XAF00000 MDSS=0XAE00000 DPU=0XAE01000', 'MAP INTF=5 AT=0X39000 CTL=0X15000 STEP=0X2000 REG=0XF4']
        self.assertEqual(self.verdict(page_lines(good_values(), map_lines=wrong_ctl))[:2], ('INCONSISTENT', 'address_mismatch'))

    def test_a_film_that_ends_early_never_gives_active_without_the_evidence(self):
        full = page_lines(good_values())
        needed_end = max(i for i, l in enumerate(full) if l.startswith('S2 INTF'))      # the second counter reading is evidence the check needs (FLC is nonzero)
        for cut in range(1, len(full) + 1):
            with self.subTest(lines=cut):
                lines = full[:cut]
                if not any(l.startswith('MAP INTF') for l in lines):
                    with self.assertRaises(dp.PageError):
                        self.verdict(lines)
                    continue
                result = self.verdict(lines)
                self.assertEqual(result[0] == 'ACTIVE', cut > needed_end, (cut, result))

    def test_the_command_line_gives_the_exit_codes_and_a_json_verdict(self):
        with tempfile.TemporaryDirectory(dir=str(ROOT / 'build')) as tmp:
            for name, values, code in (('good', good_values(), 0), ('off', {**good_values(), 'GDSC': 0x222001}, 1), ('unknown', {**good_values(), 'DAHB': 0x80000000}, 2),
                                       ('inconsistent', {**good_values(), 'EN': 1, 'ST': 0}, 3)):
                path = Path(tmp) / f'{name}.txt'
                path.write_text(text_of(page_lines(values)))
                done = subprocess.run([sys.executable, '-B', str(ROOT / 'tools' / 'sp11_dispsnap_parse.py'), str(path), '--json'], capture_output=True, text=True, timeout=120)
                self.assertEqual(done.returncode, code, done.stderr + done.stdout)
            bad = Path(tmp) / 'bad.txt'
            bad.write_text(P + 'GCC DAHB=ONE\n')
            self.assertEqual(subprocess.run([sys.executable, '-B', str(ROOT / 'tools' / 'sp11_dispsnap_parse.py'), str(bad)], capture_output=True, text=True).returncode, 4)


class Tables(unittest.TestCase):
    def test_the_study_tables_give_the_twenty_registers_the_six_ctl_pages_and_the_parameters(self):
        registers, ctl, params = dp.study_tables()
        self.assertEqual(list(registers), dp.reg_order())
        self.assertEqual(len(registers), 20)
        self.assertEqual(len(ctl), 6)
        for name in ('intf_index', 'dpu_major', 'dpu_minor', 'gcc.base', 'dispcc.base', 'mdss.base', 'mdp.base', 'mdp.length', 'intf.base', 'intf.length', 'ctl_count', 'ctl_length',
                     'ctl_intf_active_off', 'required', 'refresh_hz_min', 'refresh_hz_max', 'counter_gap_min_us'):
            self.assertIn(name, params)

    def test_the_page_keys_are_the_study_registers_and_the_study_map_is_the_one_the_payload_prints(self):
        registers, ctl, params = dp.study_tables()
        self.assertEqual(set(dp.KEYS.values()), set(registers))
        self.assertEqual(dp.study_map(registers, ctl, params), {'GCC': 0x100000, 'DISPCC': 0xAF00000, 'MDSS': 0xAE00000, 'DPU': 0xAE01000, 'INTF': 5, 'AT': 0x39000, 'CTL': 0x15000,
                                                                'STEP': 0x1000, 'REG': 0xF4})
        for blk_head, keys in dp.HEADS.items():
            self.assertTrue(keys)

    def test_the_sources_name_no_machine_and_carry_spdx(self):
        for path in (ROOT / 'tools' / 'sp11_dispsnap_parse.py', Path(__file__)):
            text = path.read_text()
            self.assertTrue(text.splitlines()[1].startswith('# SPDX-License-Identifier: MIT'), path.name)
            for prefix in HOME_PREFIXES:
                self.assertNotIn(prefix, text, path.name)
            self.assertLess(len(text.splitlines()), 1500)
            self.assertLess(len(text.encode()), 51200)


@unittest.skipUnless(TREE and (Path(TREE) / 'payloads' / 'dispsnap.c').exists(), 'Q1N1_SP11_TREE does not name a q1n1 tree with payloads/dispsnap.c')
class ForkPages(unittest.TestCase):
    """The pages the payload's own host model prints, through the parser and the study's function."""
    # An enable that says the counter runs needs both readings (the check: not-read at INTF_FRAME_B): a page without snapshot 2 is UNKNOWN, never ACTIVE.
    SCENARIOS = {'unit': ('ACTIVE', 'ok', ALL), 'drift': ('ACTIVE', 'ok', ALL), 'cold': ('INACTIVE', 'power_off', None), 'dahboff': ('UNKNOWN', 'dispcc_no_access', None),
                 'hwctl': ('UNKNOWN', None, None), 'part': ('INACTIVE', 'mdp_clock_off', None), 'dispccabort': ('UNKNOWN', 'read_fault', None),
                 'mdssabort': ('UNKNOWN', None, None), 'dpuabort': ('UNKNOWN', None, None), 'noflc': ('ACTIVE', 'ok', CORE), 'nocounter': ('UNKNOWN', 'not_read', CORE),
                 'stalled': ('INCONSISTENT', 'counter_stalled', None), 'wrongmode': ('INCONSISTENT', 'geometry_mismatch', None), 'idle': ('INACTIVE', 'timing_off', None),
                 's2gated': ('UNKNOWN', 'not_read', CORE)}

    @classmethod
    def setUpClass(cls):
        built()
        tree = Path(TREE)
        out = ROOT / 'build' / 'sp11-dispsnap-parse' / 'fork-model'
        out.parent.mkdir(parents=True, exist_ok=True)
        done = subprocess.run(['clang', '-std=gnu11', '-O1', '-w', '-DQ1N1_PAYLOAD_HOST_TEST=1', '-I' + str(tree / 'platform' / 'uefi'), '-I' + str(tree / 'payloads'),
                               '-o', str(out), str(tree / 'tools' / 'test-q1n1-sp11-payload-dispsnap.c')], capture_output=True, text=True, cwd=str(tree))
        if done.returncode:
            raise unittest.SkipTest('the payload\'s host model did not build: ' + done.stderr[-300:])
        cls.model = out

    def page(self, scenario):
        done = subprocess.run([str(self.model), '--page', scenario], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def test_every_scenario_page_gets_the_verdict_its_situation_has(self):
        for scenario, (verdict, why, evidence) in self.SCENARIOS.items():
            with self.subTest(scenario=scenario):
                got = _judge(self.page(scenario))
                self.assertEqual(got[0], verdict, got)
                if why:
                    self.assertEqual(got[1], why, got)
                if evidence is not None:
                    self.assertEqual(got[3], evidence, got)

    def test_the_maps_of_the_pages_are_the_studys_and_the_gop_line_is_the_arguments(self):
        registers, ctl, params = dp.study_tables()
        want = dp.study_map(registers, ctl, params)
        for scenario in ('unit', 'cold', 'dahboff'):
            page = dp.parse_page(self.page(scenario))
            self.assertEqual(page['map'], want, scenario)
            self.assertEqual(page['gop'], (2880, 1920, 2880, 1))

    def test_the_gap_of_the_page_is_the_counter_ticks_between_the_readings(self):
        page = dp.parse_page(self.page('unit'))
        self.assertEqual(page['dt'], 803 * 192000)
        self.assertEqual(page['dt'] * 1000000 // page['hz'], 8030000)

    def test_the_payloads_register_table_is_the_studys_dump_list(self):
        regs = (Path(TREE) / 'payloads' / 'dispsnap-regs.h').read_text()
        defines = {n: int(v, 0) for n, v in re.findall(r'^#define (\w+) (0x[0-9a-fA-F]+|\d+)u?\b', regs, re.M)}
        windows = [(int(b, 16), int(s, 16)) for b, s in re.findall(r'\{(0x[0-9a-fA-F]+)u, (0x[0-9a-fA-F]+)u, 0\}', re.search(r'windows\[WINDOWS\] = \{(.*?)\};', regs, re.S).group(1))]
        registers, ctl, params = dp.study_tables()
        self.assertEqual([b for b, _ in windows], [params['gcc.base'], params['dispcc.base'], params['mdss.base'], params['mdp.base']])
        self.assertEqual([s for _, s in windows], [params['gcc.length'], params['dispcc.length'], params['mdss.length'], params['mdp.length']])
        self.assertEqual(defines['INTF_PAGE_OFF'], params['intf.base'] - params['mdp.base'])
        self.assertEqual(defines['INTF_INDEX'], params['intf_index'])
        self.assertEqual(defines['CTL_FIRST_OFF'], ctl[0][0] - params['mdp.base'])
        self.assertEqual(defines['CTL_STEP'], ctl[1][0] - ctl[0][0])
        self.assertEqual(defines['CTL_INTF_ACTIVE_OFF'], params['ctl_intf_active_off'])
        self.assertEqual(defines['CTL_COUNT'], params['ctl_count'])
        rows = re.findall(r'^\s*ROW\((WIN_\w+), ([^,]+(?:\([^)]*\))?[^,]*), (D_\w+), ([^,]+), "(\w+)"\),', regs, re.M)
        self.assertEqual(len(rows), 25)
        for window, expression, _, _, key in rows:
            with self.subTest(key=key):
                found = re.fullmatch(r'INTF\((\w+)\)', expression)
                if found:
                    offset = defines[found.group(1)]
                    study = registers[dp.KEYS[key]]
                    self.assertEqual((window, offset), ('WIN_DPU', study[2]))
                elif re.fullmatch(r'C\d', key):
                    self.assertEqual(expression, f'CTL({key[1]})')
                    self.assertEqual(ctl[int(key[1])][1], defines['CTL_INTF_ACTIVE_OFF'])
                else:
                    study = registers[dp.KEYS[key]]
                    self.assertEqual(defines[expression], study[2])
                    self.assertEqual(window, {'gcc': 'WIN_GCC', 'dispcc': 'WIN_DISPCC', 'mdss': 'WIN_MDSS'}[study[0]])


if __name__ == '__main__':
    unittest.main(verbosity=2)
