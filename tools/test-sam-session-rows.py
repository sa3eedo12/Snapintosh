#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Host tests, second file: the write rows and the undo of tools/geni_uart_plan.py pinned to the pinned C, the survey-word
and CLI robustness, and the boundary cases the first file (test-sam-session.py) leaves open.

    python3 tools/test-sam-session-rows.py

What a pass proves: every engine row and every clock row equals what the pinned Linux source says (Linux 7475ea8b), derived here
from transcribed C constants and algorithms that share no code with the plan, so a changed value, mask or kind fails; the undo
guard accepts exactly the reviewed reverse; a malformed survey word is a STOP, never another exception; the parser, scanner and
descriptor-read limits sit exactly where the policy says. What it does not prove: anything about the unit (the first read-only
survey settles that, docs/SP11-INPUT-STUDY.md section 5). The inputs are computed, never captured. No hardware, no files.
"""
import contextlib
import dataclasses
import importlib.util
import io
import json
import random
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import geni_uart_plan as gp  # noqa: E402
import sam_frames as fr  # noqa: E402
import sam_hid_desc as hd  # noqa: E402
import sam_session as ss  # noqa: E402


def _first_test_file():
    spec = importlib.util.spec_from_file_location('sam_session_tests_1', ROOT / 'tools' / 'test-sam-session.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


T1 = _first_test_file()
FULL = 0xFFFFFFFF

# ---- the pinned C, transcribed (Linux 7475ea8b); nothing in this section is imported from the plan ----------------------
# geni-se.h and qcom-geni-se.c (gse), qcom_geni_serial.c (gser), clk-rcg2.c (rcg), clk-rcg.h, gcc-x1e80100.c (gcc), hamoa.dtsi.
SE_BASE, GCC_BASE = 0x00B88000, 0x00100000                       # hamoa.dtsi uart2 reg; gcc reg
GENI_FORCE_DEFAULT_REG, GENI_OUTPUT_CTRL, SE_GENI_CGC_CTRL = 0x20, 0x24, 0x28
GENI_SER_M_CLK_CFG, GENI_SER_S_CLK_CFG, SE_GENI_CLK_SEL = 0x48, 0x4C, 0x7C
SE_GENI_BYTE_GRAN, SE_GENI_DMA_MODE_EN = 0x254, 0x258
SE_GENI_TX_PACKING_CFG0, SE_GENI_TX_PACKING_CFG1, SE_GENI_RX_PACKING_CFG0, SE_GENI_RX_PACKING_CFG1 = 0x260, 0x264, 0x284, 0x288
SE_GENI_M_IRQ_CLEAR, SE_GENI_S_CMD0, SE_GENI_S_IRQ_CLEAR = 0x618, 0x630, 0x648
SE_GENI_RX_WATERMARK_REG, SE_GENI_RX_RFR_WATERMARK_REG, SE_DMA_GENERAL_CFG = 0x810, 0x814, 0xE30
SE_UART_LOOPBACK_CFG, SE_UART_TX_TRANS_CFG, SE_UART_TX_WORD_LEN, SE_UART_TX_STOP_BIT_LEN = 0x22C, 0x25C, 0x268, 0x26C
SE_UART_RX_TRANS_CFG, SE_UART_RX_WORD_LEN, SE_UART_RX_STALE_CNT = 0x280, 0x28C, 0x294
SE_UART_TX_PARITY_CFG, SE_UART_RX_PARITY_CFG, SE_UART_MANUAL_RFR = 0x2A4, 0x2A8, 0x2AC
DEFAULT_CGC_EN = DEFAULT_IO_OUTPUT_CTRL_MSK = (1 << 7) - 1       # GENMASK(6, 0): gse:247,259
DMA_CGC_ON = 1 | 2 | 4 | 8                                        # DMA_RX/TX_CLK_CGC_ON, DMA_AHB_SLV, AHB_SEC_SLV: gse:274-277
FORCE_DEFAULT, GENI_DMA_MODE_EN, SER_CLK_EN, CLK_DIV_SHFT, CLK_SEL_MSK = 1, 1, 1, 4, 7
UART_TX_PAR_EN, UART_CTS_MASK, UART_RX_PAR_EN, PAR_CALC_EN = 1 << 0, 1 << 1, 1 << 4, 1 << 0     # gser:50-58
TX_STOP_BIT_LEN_1, UART_RX_WM, STALE_TIMEOUT, DEFAULT_BITS_PER_CHAR, CS8 = 0, 2, 16, 10, 8
UART_START_READ, S_OPCODE_SHFT, S_PARAMS_MSK = 1, 27, (1 << 27) - 1
UART_OVERSAMPLING, QUP_SE_VERSION_2_5, MAX_CLK_PERF_LEVEL = 32, 0x20050000, 32
CFG_SRC_SEL_MASK, CFG_MODE_MASK, CFG_MODE_DUAL_EDGE, CFG_HW_CLK_CTRL_MASK = 7 << 8, 3 << 12, 2 << 12, 1 << 20   # rcg:34-39
CMD_RCGR, HALT_REG, ENABLE_REG, ENABLE_MASK, MND_WIDTH, HID_WIDTH = 0x42288, 0x42274, 0x52020, 1 << 12, 16, 5   # gcc:4434,1163-1170
PARENT_SEL = {'xo': 0, 'main': 1, 'even': 6}                                                          # gcc_parent_map_0, gcc:220-224
# gcc:1142-1155, F(freq, src, h, m, n) with pre_div = 2h - 1 (clk-rcg.h:10)
S2_TABLE = [(7372800, 'even', 1, 384, 15625), (14745600, 'even', 1, 768, 15625), (19200000, 'xo', 1, 0, 0),
            (29491200, 'even', 1, 1536, 15625), (32000000, 'even', 1, 8, 75), (48000000, 'even', 1, 4, 25),
            (64000000, 'even', 1, 16, 75), (80000000, 'even', 1, 4, 15), (96000000, 'even', 1, 8, 25),
            (150000000, 'even', 2, 0, 0), (200000000, 'main', 3, 0, 0)]
ROW_128M = (128_000_000, 'main', 1, 16, 75)                      # 600 MHz x 16/75


def ref_packing(bpw=8, pack_words=4, msb_to_lsb=False):
    """geni_se_config_packing(), gse:539-591: (cfg0, cfg1, byte_gran)."""
    cfg, temp, idx_start = [0] * 4, bpw, (bpw - 1 if msb_to_lsb else 0)
    idx, idx_delta, iterations = idx_start, (-8 if msb_to_lsb else 8), ((bpw + 7) // 8 * 8) * pack_words // 8
    for i in range(iterations):
        cfg[i] = idx << 5 | int(msb_to_lsb) << 4 | (min(temp, 8) - 1) << 1
        if temp <= 8:
            idx, temp = (i + 1) * 8 + idx_start, bpw
        else:
            idx, temp = idx + idx_delta, temp - 8
    cfg[iterations - 1] |= 1
    return cfg[0] | cfg[1] << 10, cfg[2] | cfg[3] << 10, (bpw // 16 if pack_words or bpw == 32 else None)


def ref_rcg_words(row):
    """__clk_rcg2_configure_mnd() for the s2 RCG, rcg:517-562: ({'m','n','d'}, cfg field bits, cfg clear mask)."""
    _freq, src, h, m, n = row
    mask, cfg, words = (1 << MND_WIDTH) - 1, (2 * h - 1) | PARENT_SEL[src] << 8, {'m': 0, 'n': 0, 'd': 0}
    if n:
        words = {'m': m & mask, 'n': ~(n - m) & mask, 'd': ~min(max(n, m), 2 * (n - m)) & mask}
        if m != n:
            cfg |= CFG_MODE_DUAL_EDGE
    return words, cfg, (1 << HID_WIDTH) - 1 | CFG_SRC_SEL_MASK | CFG_MODE_MASK | CFG_HW_CLK_CTRL_MASK


def ref_round_rate(rates, rate):
    """qcom_find_freq() CEIL (common.c:32-46): the first row at or above, else the last row; the empty terminator ends the table."""
    live = []
    for f in rates:
        if f == 0:
            break
        live.append(f)
    return next((f for f in live if rate <= f), live[-1]) if live else 0


def ref_clk_tbl_get(rates):
    """geni_se_clk_tbl_get(), gse:679-716."""
    tbl, freq = [], 0
    for i in range(MAX_CLK_PERF_LEVEL):
        freq = ref_round_rate(rates, freq + 1)
        if freq <= 0 or (i > 0 and freq == tbl[i - 1]):
            break
        tbl.append(freq)
    return tbl


def ref_serial_rate(tbl, baud, qup_version):
    """geni_se_clk_freq_match() (gse:738-779, exact = false) and the divider of geni_serial_set_rate() (gser:1426-1450)."""
    sampling = UART_OVERSAMPLING // 2 if qup_version >= QUP_SE_VERSION_2_5 else UART_OVERSAMPLING
    req, best, best_delta = baud * sampling, None, 1 << 64
    for i, freq in enumerate(tbl):
        delta = req - freq // -(-freq // req)
        if delta < best_delta:
            best, best_delta = (i, freq), delta
            if delta == 0:
                break
    return best[0], -(-best[1] // req)


def tup(row):
    return row.name, row.addr, row.mask, row.value, row.kind


def expected_vote():
    return 's2_vote', GCC_BASE + ENABLE_REG, ENABLE_MASK, ENABLE_MASK, 'rmw'


def expected_rcg(row):
    w, cfg, cfg_mask = ref_rcg_words(row)
    mask = (1 << MND_WIDTH) - 1
    rows = [('rcg_m', GCC_BASE + CMD_RCGR + 8, mask, w['m'], 'rmw'), ('rcg_n', GCC_BASE + CMD_RCGR + 0xC, mask, w['n'], 'rmw'),
            ('rcg_d', GCC_BASE + CMD_RCGR + 0x10, mask, w['d'], 'rmw')] if row[4] else []
    return rows + [('rcg_cfg', GCC_BASE + CMD_RCGR + 4, cfg_mask, cfg, 'rmw'), ('rcg_update', GCC_BASE + CMD_RCGR, 1, 1, 'latch')]


def expected_engine(depth, idx, div):
    cfg0, cfg1, gran = ref_packing(8, 4)
    clk = SER_CLK_EN | div << CLK_DIV_SHFT
    names = [('rx_stale', SE_UART_RX_STALE_CNT, FULL, DEFAULT_BITS_PER_CHAR * STALE_TIMEOUT, 'rmw'),
             ('tx_pack0', SE_GENI_TX_PACKING_CFG0, FULL, cfg0, 'rmw'), ('tx_pack1', SE_GENI_TX_PACKING_CFG1, FULL, cfg1, 'rmw'),
             ('rx_pack0', SE_GENI_RX_PACKING_CFG0, FULL, cfg0, 'rmw'), ('rx_pack1', SE_GENI_RX_PACKING_CFG1, FULL, cfg1, 'rmw'),
             ('byte_gran', SE_GENI_BYTE_GRAN, FULL, gran, 'rmw'),
             ('m_irq_clear', SE_GENI_M_IRQ_CLEAR, FULL, FULL, 'w1c'), ('s_irq_clear', SE_GENI_S_IRQ_CLEAR, FULL, FULL, 'w1c'),
             ('cgc_ctrl', SE_GENI_CGC_CTRL, DEFAULT_CGC_EN, DEFAULT_CGC_EN, 'rmw'),
             ('dma_cgc', SE_DMA_GENERAL_CFG, DMA_CGC_ON, DMA_CGC_ON, 'rmw'),
             ('output_ctrl', GENI_OUTPUT_CTRL, FULL, DEFAULT_IO_OUTPUT_CTRL_MSK, 'rmw'),
             ('force_default', GENI_FORCE_DEFAULT_REG, FULL, FORCE_DEFAULT, 'rmw'),
             ('dma_off', SE_GENI_DMA_MODE_EN, GENI_DMA_MODE_EN, 0, 'rmw'),
             ('rx_wm', SE_GENI_RX_WATERMARK_REG, FULL, UART_RX_WM, 'rmw'),
             ('rx_rfr_wm', SE_GENI_RX_RFR_WATERMARK_REG, FULL, depth - 2, 'rmw'),
             ('m_clk', GENI_SER_M_CLK_CFG, FULL, clk, 'rmw'), ('s_clk', GENI_SER_S_CLK_CFG, FULL, clk, 'rmw'),
             ('clk_sel', SE_GENI_CLK_SEL, CLK_SEL_MSK, idx, 'rmw'),
             ('tx_cfg', SE_UART_TX_TRANS_CFG, UART_TX_PAR_EN | UART_CTS_MASK, 0, 'rmw'),
             ('rx_cfg', SE_UART_RX_TRANS_CFG, UART_RX_PAR_EN, 0, 'rmw'),
             ('tx_par', SE_UART_TX_PARITY_CFG, PAR_CALC_EN, 0, 'rmw'), ('rx_par', SE_UART_RX_PARITY_CFG, PAR_CALC_EN, 0, 'rmw'),
             ('manual_rfr', SE_UART_MANUAL_RFR, FULL, 0, 'rmw'), ('loopback', SE_UART_LOOPBACK_CFG, FULL, 0, 'rmw'),
             ('word_len_tx', SE_UART_TX_WORD_LEN, FULL, CS8, 'rmw'), ('word_len_rx', SE_UART_RX_WORD_LEN, FULL, CS8, 'rmw'),
             ('stop_tx', SE_UART_TX_STOP_BIT_LEN, FULL, TX_STOP_BIT_LEN_1, 'rmw'),
             ('start_read', SE_GENI_S_CMD0, FULL, UART_START_READ << S_OPCODE_SHFT | 0 & S_PARAMS_MSK, 'cmd')]
    return [(name, SE_BASE + off, mask, value, kind) for name, off, mask, value, kind in names]


def survey(dfs=False, levels=None, **kw):
    """A synthetic read-only snapshot whose level words come from the transcribed encoder above."""
    words = T1.survey_words(dfs=dfs, **kw)
    for i, row in enumerate(levels or S2_TABLE[:8]):
        w, cfg, _ = ref_rcg_words(row)
        words[f'lv_cfg{i}'], words[f'lv_m{i}'], words[f'lv_n{i}'] = cfg, w['m'], w['n']
    return words


DFS_128M_LEVELS = S2_TABLE[:5] + [ROW_128M] + S2_TABLE[9:11]      # 128 MHz at level 5: the in-engine divider is 2


class PinnedRows(unittest.TestCase):
    def check(self, words, depth, table, dfs):
        plan = gp.build_clock_plan(words)
        tbl = ref_clk_tbl_get([r[0] for r in table])
        idx, div = ref_serial_rate(tbl, 4_000_000, QUP_SE_VERSION_2_5)
        self.assertEqual((plan.mode, plan.clk_sel, plan.div), ('dfs' if dfs else 'plain', idx, div))
        if dfs:
            self.assertEqual(table[idx][0], tbl[idx])                       # the selected level really runs at the rate
        clock = [expected_vote()] + ([] if dfs else expected_rcg(next(r for r in S2_TABLE if r[0] == tbl[idx])))
        self.assertEqual([tup(r) for r in plan.rows], clock)
        self.assertEqual([tup(r) for r in gp.uart_rows(plan, depth)], expected_engine(depth, idx, div))
        polls = {s.name: (gp.BASES[s.block] + s.offset, s.mask, s.want, s.timeout_us) for s in plan.steps if isinstance(s, gp.Poll)}
        want = {'cbcr_on': (GCC_BASE + HALT_REG, 1 << 31, 0, 200)}
        if not dfs:
            want['rcg_updated'] = (GCC_BASE + CMD_RCGR, 1, 0, 500)
        self.assertEqual(polls, want)
        self.assertEqual([s.name for s in plan.steps][:2], ['s2_vote', 'cbcr_on'])

    def test_every_row_is_what_the_pinned_source_says(self):
        for depth in (4, 16, 64, 256):
            self.check(survey(), depth, S2_TABLE, dfs=False)
            self.check(survey(dfs=True), depth, S2_TABLE[:8], dfs=True)
        self.check(survey(dfs=True, levels=DFS_128M_LEVELS), 16, DFS_128M_LEVELS, dfs=True)      # selector 5, divider 2 -> 0x21
        self.assertEqual(expected_engine(16, 5, 2)[15][3], 0x21)
        self.assertEqual(expected_engine(16, 6, 1)[15][3], 0x11)

    def test_a_seeded_change_to_any_row_value_or_mask_fails_the_pin(self):
        # Each of the 28 engine rows and 6 clock rows is changed in turn, in both modes: the pin must notice every one.
        real = {'uart_rows': gp.uart_rows, 'clock_rows': gp.clock_rows, 'vote_row': gp.vote_row}

        def mutate(row, how):
            return dataclasses.replace(row, value=row.value ^ 1) if how == 'value' else dataclasses.replace(row, mask=row.mask ^ (1 << 31))

        def listed(name, k, how):
            return lambda *a: [mutate(r, how) if i == k else r for i, r in enumerate(real[name](*a))]

        cases = [('uart_rows', 28, survey(), False), ('uart_rows', 28, survey(dfs=True), True), ('clock_rows', 6, survey(), False)]
        for name, count, words, dfs in cases:
            for k in range(count):
                for how in ('value', 'mask'):
                    with mock.patch.object(gp, name, listed(name, k, how)), self.assertRaises(AssertionError, msg=(name, k, how)):
                        self.check(words, 16, S2_TABLE if not dfs else S2_TABLE[:8], dfs)
        for how in ('value', 'mask'):
            with mock.patch.object(gp, 'vote_row', lambda how=how: mutate(real['vote_row'](), how)), self.assertRaises(AssertionError):
                self.check(survey(dfs=True), 16, S2_TABLE[:8], True)

    def test_the_survey_and_engine_read_lists(self):
        self.assertEqual((len(gp.SURVEY_READS), len(gp.ENGINE_READS)), (41, 39))
        engine = {off for _, off in gp.ENGINE_READS}
        self.assertTrue({0xE18, 0xE1C} <= engine)                                       # SE_GSI_EVENT_EN and SE_IRQ_EN: read only
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(gp.main(['reads']), 0)
        self.assertEqual(len(out.getvalue().splitlines()), 41 + 39)


class DfsDecision(unittest.TestCase):
    def test_only_the_selected_level_must_run_at_the_rate(self):
        # Unused levels after the selected one may hold anything (here: out of order). v3 stopped on them; Linux would run.
        levels = S2_TABLE[:7] + [S2_TABLE[2]]
        plan = gp.build_clock_plan(survey(dfs=True, levels=levels))
        self.assertEqual((plan.mode, plan.clk_sel), ('dfs', 6))
        # The selector is the index in the round-up walk of the levels in their own order (gse:679-716), not in sorted order:
        # here the walk reads 64, 80, 48 MHz and names level 0.
        walk = [S2_TABLE[6], S2_TABLE[7]] + S2_TABLE[:5] + [S2_TABLE[5]]
        self.assertEqual(ref_clk_tbl_get([r[0] for r in walk])[:3], [64_000_000, 80_000_000, 48_000_000])
        self.assertEqual(gp.build_clock_plan(survey(dfs=True, levels=walk)).clk_sel, 0)
        PinnedRows.check(self, survey(dfs=True, levels=walk), 16, walk, dfs=True)
        # A level the selector would name that does not run at the rate stops, with the reason (80 MHz first: the table's
        # round-up walk then names index 1, whose level 1 is 7.37 MHz).
        scrambled = [S2_TABLE[7]] + S2_TABLE[:6] + [S2_TABLE[6]]
        with self.assertRaisesRegex(gp.PlanError, 'does not name a level'):
            gp.build_clock_plan(survey(dfs=True, levels=scrambled))

    def test_a_perf_index_beyond_the_eight_levels_is_a_stop_not_an_index_error(self):
        fake = {'index': 9, 'clk_hz': 64_000_000, 'div': 1, 'exact': True, 'baud': 4_000_000, 'sampling': 16, 'delta': 0}
        with mock.patch.object(gp, 'baud_plan', lambda *a, **k: dict(fake)), self.assertRaises(gp.PlanError):
            gp.build_clock_plan(survey(dfs=True))

    def test_a_mode_one_level_word_is_a_running_counter(self):
        # CFG mode field 1 (bit 12) turns the M/N counter on as mode 2 does (rcg:182-190: any non-zero mode).
        w, cfg, _ = ref_rcg_words(S2_TABLE[6])
        words = survey(dfs=True)
        words['lv_cfg6'] = cfg & ~CFG_MODE_MASK | 1 << 12
        self.assertEqual(gp.build_clock_plan(words).clk_sel, 6)
        self.assertEqual(gp.rcg2_decode_hz({'cfg': words['lv_cfg6'], 'm': w['m'], 'n': w['n']}), 64_000_000)
        words['lv_cfg6'] = cfg & ~CFG_MODE_MASK                                          # mode 0: the counter is off
        with self.assertRaises(gp.PlanError):
            gp.build_clock_plan(words)

    def test_gpll0_output_and_reset_are_reported_not_required(self):
        note = lambda mode: ' '.join(gp.build_clock_plan(survey(mode=mode)).notes)
        self.assertNotIn('OUTCTRL', note(1 << 31 | 1 << 2 | 1))
        self.assertIn('OUTCTRL', note(1 << 31 | 1))
        self.assertIn('OUTCTRL', note(1 << 31))


class Malformed(unittest.TestCase):
    JUNK = (None, 'GATED', '', 'zz', '1.5', 3.5, True, False, [], {}, -1, 1 << 32, '0x1ffffffff', '-1')
    READ = ('cbcr', 'vote', 'gpll0_vote', 'dfs', 'div', 'gpll0_mode', 'gpll0_l', 'gpll0_alpha', 'gpll0_user', 'qup_ver')

    def test_a_malformed_word_the_plan_reads_is_a_stop(self):
        for dfs in (False, True):
            names = self.READ + (tuple(f'lv_{k}{i}' for k in ('cfg', 'm', 'n') for i in range(8)) if dfs else ())
            for name in names:
                for junk in self.JUNK:
                    words = survey(dfs=dfs)
                    words[name] = junk
                    with self.assertRaises(gp.PlanError, msg=(name, junk)):
                        gp.build_clock_plan(words)
                words = survey(dfs=dfs)
                del words[name]
                with self.assertRaises(gp.PlanError, msg=name):
                    gp.build_clock_plan(words)
        for bad in (None, [], 'words', 3):
            with self.assertRaises(gp.PlanError):
                gp.build_clock_plan(bad)
        for good in ('0x10', 16, '0b10000', ' 16 '):
            self.assertEqual(gp._as_word(good, 'x'), 16)

    def test_gpll0_vote_all_ones_is_an_identity_failure(self):
        self.assertIn('gpll0_vote', gp.IDENTITY_WORDS)
        words = survey()
        words['gpll0_vote'] = FULL
        with self.assertRaisesRegex(gp.PlanError, 'all ones'):
            gp.build_clock_plan(words)

    def test_clock_rows_take_only_a_real_flag_word(self):
        for flag in (True, 1.0, 'x', [], FULL + 1):
            with self.assertRaises(gp.PlanError, msg=str(flag)):
                gp.clock_rows(64_000_000, flag)
        self.assertEqual(len(gp.clock_rows(64_000_000, '0')), 6)

    def test_random_near_valid_words_stop_or_plan_and_nothing_else(self):
        rng, keys = random.Random(20261122), list(survey(dfs=True))
        plans = stops = 0
        for _ in range(3000):
            words = survey(dfs=rng.random() < 0.5, mode=rng.choice((1 << 31 | 1, 1 << 31, 0)), qup=rng.choice((0x20050000, 0x20040000, 0)))
            for name in rng.sample(keys, rng.randint(1, 3)):
                how = rng.randrange(4)
                if how == 0:
                    del words[name]
                elif how == 1:
                    words[name] = rng.choice(self.JUNK)
                elif how == 2:
                    words[name] = rng.getrandbits(32)
                else:
                    words[name] = words[name] ^ (1 << rng.randrange(32)) if isinstance(words[name], int) else 0
            try:
                plan = gp.build_clock_plan(words)
            except gp.PlanError:
                stops += 1
                continue
            plans += 1
            self.assertTrue(gp.verify_rows(plan.rows))
            self.assertEqual(plan.steps[0].name, 's2_vote')
        self.assertGreater(plans, 100)
        self.assertGreater(stops, 100)


class Cli(unittest.TestCase):
    def run_plan(self, text):
        out = io.StringIO()
        with mock.patch('builtins.open', mock.mock_open(read_data=text)), contextlib.redirect_stdout(out):
            code = gp.main(['plan', 'words.json'])
        return code, out.getvalue()

    def test_a_good_survey_prints_its_steps_then_the_digest(self):
        code, text = self.run_plan(json.dumps(survey()))
        self.assertEqual(code, 0)
        self.assertIn('rcg_cfg', text)
        self.assertIn('digest', text)

    def test_every_refusal_prints_one_stop_line_and_no_step(self):
        bad = survey()
        del bad['dfs']
        for text in (json.dumps(bad), '{"dfs": ', 'null', '[]', json.dumps(dict(survey(), qup_ver=0x20040000))):
            code, out = self.run_plan(text)
            self.assertEqual(code, 1, text)
            self.assertEqual(out.count('\n'), 1, out)
            self.assertTrue(out.startswith('STOP:'), out)
        out = io.StringIO()
        with mock.patch('builtins.open', side_effect=FileNotFoundError('words.json')), contextlib.redirect_stdout(out):
            self.assertEqual(gp.main(['plan', 'words.json']), 1)
        self.assertTrue(out.getvalue().startswith('STOP:'))

    def test_rows_are_verified_before_anything_is_printed(self):
        # With the reviewed shapes weakened the guard refuses: v3 had already printed the steps by then.
        weak = dict(gp.GCC_ALLOWED)
        del weak[gp.RCG_CFG]
        with mock.patch.object(gp, 'GCC_ALLOWED', weak):
            code, out = self.run_plan(json.dumps(survey()))
        self.assertEqual(code, 1)
        self.assertEqual(out.count('\n'), 1)
        self.assertNotIn('Row(', out)


class UndoGuard(unittest.TestCase):
    def fixture(self, dfs=False):
        plan = gp.build_clock_plan(survey(dfs=dfs))
        rows = plan.rows + gp.uart_rows(plan, 16)
        recorded = {r.name: (i * 0x01010101 + 5) & FULL for i, r in enumerate(rows)}
        recorded['vote'] = 0x810223D0
        return plan, rows, recorded, gp.undo_steps(plan, rows, recorded, started_rx=True)

    @staticmethod
    def edit(steps, name, **row):
        return [dataclasses.replace(s, row=dataclasses.replace(s.row, **row)) if s.name == name else s for s in steps]

    @staticmethod
    def drop(steps, name):
        return [s for s in steps if s.name != name]

    def refused(self, plan, rows, recorded, steps, label):
        with self.assertRaises(gp.PlanError, msg=label):
            gp.verify_undo(plan, rows, steps, recorded, started_rx=True)

    def test_the_reviewed_undo_passes_and_each_hand_edit_is_refused(self):
        for dfs in (False, True):
            plan, rows, recorded, steps = self.fixture(dfs)
            self.assertTrue(gp.verify_undo(plan, rows, steps, recorded, started_rx=True))
            names = [s.name for s in steps]
            restore = [n for n in names if n.endswith('_undo') and n not in ('rcg_update_undo', 'rcg_updated_undo')][0]
            tx_trigger = gp.Undo('write', 'tx_start', gp.Row('tx_start', 'se2', 0x600, FULL, 1 << 27, '', '', 'cmd'))
            edits = {
                'cancel value all ones': self.edit(steps, 'rx_cancel', value=FULL),
                'cancel redirected to M_CMD_CTRL': self.edit(steps, 'rx_cancel', offset=0x604),
                'cancel redirected to M_CMD0 with a transmit opcode': self.edit(steps, 'rx_cancel', offset=0x600, value=1 << 27),
                'status clear value 0': self.edit(steps, 'rx_status_clear', value=0),
                'status clear on the primary status': self.edit(steps, 'rx_status_clear', offset=0x618),
                'force default value 0': self.edit(steps, 'rx_force_default', value=0),
                'a restore that is not the recorded word': self.edit(steps, restore, value=recorded[restore[:-5]] ^ 1),
                'a restore under a wider mask': self.edit(steps, 'clk_sel_undo', mask=FULL, value=recorded['clk_sel']),
                'a restore of a register nobody wrote': self.edit(steps, restore, offset=0x700),
                'an inserted M_CMD0 row': steps[:-2] + [tx_trigger] + steps[-2:],
                'the same step twice': steps[:4] + [steps[3]] + steps[4:],
                'an unknown drain': steps + [gp.Undo('drain', 'rx_flush_all')],
                'the abort fallback made unconditional': [dataclasses.replace(s, when='') if s.name == 'rx_abort' else s for s in steps],
                'a restore made conditional': [dataclasses.replace(s, when=gp.WHEN_STILL_ACTIVE) if s.name == restore else s for s in steps],
                'a poll redirected to a FIFO port': [dataclasses.replace(s, row=dataclasses.replace(s.row, offset=0x780))
                                                     if s.name == 'rx_idle' else s for s in steps],
                'an unbounded poll': [dataclasses.replace(s, row=dataclasses.replace(s.row, timeout_us=10 ** 9))
                                      if s.name == 'rx_cancelled' else s for s in steps],
                'cancel after the drain': [steps[2], steps[1], steps[0]] + steps[3:],
                'a restore before the receive command is quiet': [steps[i] for i in (0, 1, 2, 3)] + [s for s in steps if s.name == restore]
                + [s for s in steps[4:] if s.name != restore],
            }
            for label, edited in edits.items():
                self.refused(plan, rows, recorded, edited, f'{label} (dfs={dfs})')
            for name in names:
                if name not in ('branch_off_delay',):
                    self.refused(plan, rows, recorded, self.drop(steps, name), f'without {name} (dfs={dfs})')
        plan, rows, recorded, steps = self.fixture(False)
        self.refused(plan, rows, recorded, steps[:-2] + [self.edit(steps, 'vote_clear', value=gp.VOTE_S2_BIT)[-2]] + steps[-1:], 'vote set')
        dfs_plan, dfs_rows, dfs_recorded, dfs_steps = self.fixture(True)
        latch = [s for s in steps if s.name == 'rcg_update_undo']
        self.refused(dfs_plan, dfs_rows, dfs_recorded, dfs_steps[:-2] + latch + dfs_steps[-2:], 'a DFS RCG must never be written')

    def test_a_vote_another_master_held_is_not_dropped(self):
        plan, rows, recorded, steps = self.fixture()
        held = dict(recorded, vote=recorded['vote'] | gp.VOTE_S2_BIT)
        self.refused(plan, rows, held, steps, 'vote_clear although the vote was held')
        held_steps = gp.undo_steps(plan, rows, held, started_rx=True)
        self.assertNotIn('vote_clear', [s.name for s in held_steps])
        self.assertTrue(gp.verify_undo(plan, rows, held_steps, held, started_rx=True))
        self.refused(plan, rows, recorded, held_steps, 'no vote_clear although we set the vote')

    def test_missing_recorded_words_are_a_stop_not_a_default(self):
        plan, rows, recorded, steps = self.fixture()
        for bad in (dict((k, v) for k, v in recorded.items() if k != 'vote'), dict((k, v) for k, v in recorded.items() if k != 'stop_tx'),
                    dict(recorded, vote=None), dict(recorded, vote='GATED'), dict(recorded, rx_stale=-1), None):
            with self.assertRaises(gp.PlanError, msg=str(bad)[:60]):
                gp.undo_steps(plan, rows, bad, started_rx=True)
            with self.assertRaises(gp.PlanError, msg=str(bad)[:60]):
                gp.verify_undo(plan, rows, steps, bad, started_rx=True)

    def test_the_abort_fallback_is_a_set_of_steps(self):
        for dfs in (False, True):
            plan, rows, recorded, steps = self.fixture(dfs)
            names = [s.name for s in steps]
            group = ['rx_abort', 'rx_abort_done', 'rx_abort_clear', 'rx_force_default']
            self.assertEqual([n for n in names if n in group], group)
            self.assertTrue(all(s.when == gp.WHEN_STILL_ACTIVE for s in steps if s.name in group))
            self.assertTrue(all(s.when == '' for s in steps if s.name not in group))
            self.assertEqual(names.index('rx_idle') + 1, names.index('rx_abort'))
            cmd = {s.name: s.row for s in steps if s.name in ('rx_cancel', 'rx_abort')}
            self.assertEqual((cmd['rx_cancel'].value, cmd['rx_abort'].value, cmd['rx_cancel'].offset), (4, 2, 0x634))
            self.assertLess(max(names.index(n) for n in group), min(names.index(n) for n in names if n.endswith('_undo')))

    def test_the_forward_allow_list_cannot_carry_the_undo_and_the_undo_shapes_can(self):
        plan, rows, recorded, steps = self.fixture()
        forward = gp.allowlist(rows)
        writes = [s for s in steps if s.op == 'write']
        accepted = {s.name for s in writes if s.row.key in forward}                 # only where an undo store equals a forward one
        self.assertTrue({'rx_cancel', 'rx_abort', 'rx_abort_clear', 'vote_clear'} <= {s.name for s in writes} - accepted)
        self.assertGreater(len(writes) - len(accepted), 25)
        shapes = gp.undo_shapes(plan, rows)
        for s in writes:
            self.assertIn((s.row.block, s.row.offset, s.row.mask, s.row.kind), shapes, s.name)
        for wider in (('se2', 0x600, FULL, 'cmd'), ('se2', 0x604, FULL, 'cmd'), ('se2', 0x700, FULL, 'rmw'), ('gcc', gp.S2_DIV, 0xF, 'rmw')):
            self.assertNotIn(wider, shapes)
        dfs_plan, dfs_rows, _, _ = self.fixture(True)
        self.assertNotIn(('gcc', gp.RCG_CMD, gp.CMD_UPDATE, 'latch'), gp.undo_shapes(dfs_plan, dfs_rows))
        self.assertNotIn(('se2', gp.S_CMD_CTRL, FULL, 'cmd'), gp.undo_shapes(plan, rows, started_rx=False))


class EngineGate(unittest.TestCase):
    GOOD = {'fw': 0x0200, 'ifd': 0, 'status': 0, 'hw0': 16 << 16, 'hw1': 16 << 16}

    def test_the_engine_rows_exist_only_after_the_engine_reads_allow_them(self):
        plan = gp.build_clock_plan(survey())
        rows = gp.engine_rows(plan, self.GOOD, QUP_SE_VERSION_2_5)
        self.assertEqual([tup(r) for r in rows], expected_engine(16, 6, 1))
        self.assertEqual(gp.engine_rows(plan, dict(self.GOOD, hw1=32 << 16), QUP_SE_VERSION_2_5)[14].value, 30)
        for change in ({'fw': 0xFF00}, {'fw': 0x0900}, {'ifd': 1}, {'status': 1}, {'status': 1 << 12}, {'hw1': 0}, {'ifd': FULL}):
            with self.assertRaises(gp.PlanError, msg=str(change)):
                gp.engine_rows(plan, dict(self.GOOD, **change), QUP_SE_VERSION_2_5)
        for name in self.GOOD:
            with self.assertRaises(gp.PlanError, msg=name):
                gp.engine_stops({k: v for k, v in self.GOOD.items() if k != name}, QUP_SE_VERSION_2_5)
            with self.assertRaises(gp.PlanError, msg=name):
                gp.engine_stops(dict(self.GOOD, **{name: None}), QUP_SE_VERSION_2_5)


class Scanner(unittest.TestCase):
    @staticmethod
    def lying(length=3000, seq=0):
        header = bytes((fr.TYPE_DATA_NSQ,)) + fr.le16(length) + bytes((seq,))
        return fr.SYN + header + fr.le16(fr.crc16(header))

    def test_the_flush_boundary_is_inclusive_and_the_timer_measures_silence(self):
        stall = ss.STALL_FLUSH_US
        sc = ss.FrameScanner()
        sc.feed(self.lying(), 0)
        self.assertEqual(sc.poll(stall - 1), [])
        self.assertEqual(sc.poll(stall)[0], ('junk', 'stalled'))
        sc = ss.FrameScanner()
        sc.feed(self.lying(), 0)
        sc.feed(b'', stall - 1)                                       # an empty feed is not growth
        self.assertEqual(sc.poll(stall)[0], ('junk', 'stalled'))
        sc = ss.FrameScanner()
        sc.feed(self.lying(), 0)
        sc.feed(b'\x00', stall - 1)                                   # one byte at the boundary restarts the clock
        self.assertEqual(sc.poll(stall), [])
        self.assertEqual(sc.poll(2 * stall - 2), [])
        self.assertEqual(sc.poll(2 * stall - 1)[0], ('junk', 'stalled'))

    def test_the_flush_drops_exactly_the_sync_pair(self):
        # A lying header with a good frame inside it: after the flush the six remaining header bytes are skipped as one
        # incident and the inner frame is accepted. Dropping one byte instead would skip seven.
        sc = ss.FrameScanner()
        sc.feed(self.lying() + fr.build_ack(7), 0)
        self.assertEqual(sc.poll(ss.STALL_FLUSH_US),
                         [('junk', 'stalled'), ('junk', 'bad-sync'), ('frame', fr.TYPE_ACK, 7, b'')])
        self.assertEqual((sc.stats['bytes_skipped'], sc.stats['stalled'], sc.stats['bad_sync']), (6, 1, 1))
        self.assertEqual(len(sc.buf), 0)

    def test_an_overflow_trim_disarms_the_timer_of_the_bytes_it_removed(self):
        stall = ss.STALL_FLUSH_US
        sc = ss.FrameScanner()
        sc.feed(self.lying(), 0)                                      # armed at 0
        events = sc.feed(b'\x01' * 9000, 10)                          # more than the 8192-byte cap, no sync byte in it
        self.assertEqual(events[0], ('junk', 'overflow'))
        self.assertEqual(sc.poll(stall), [])                          # nothing is left to flush
        self.assertEqual(sc.stats['stalled'], 0)
        sc = ss.FrameScanner()
        sc.feed(self.lying(), 0)
        sc.feed(b'\x01' * 9000 + self.lying(), 10)                    # a new candidate begins at 10, not at 0
        self.assertEqual(sc.poll(stall), [])
        self.assertEqual(sc.poll(stall + 10)[0], ('junk', 'stalled'))


class DescriptorRead(unittest.TestCase):
    def test_a_slice_longer_than_asked_is_refused(self):
        host, ec, wire = T1.make()
        res = ss.bring_up(wire, slice_len=26)                         # the polled-kext mitigation works end to end
        self.assertEqual(res.devices[1]['report_descriptor'], T1.KBD)
        self.assertEqual(res.devices[3]['report_descriptor'], T1.PTP)
        host, ec, wire = T1.make(faults=ss.Faults(descriptor_overlong=8))
        with self.assertRaisesRegex(ss.SamError, 'lies about its size'):
            ss.bring_up(wire, slice_len=26)

    def test_the_request_budget_is_twice_what_the_descriptor_needs_plus_three(self):
        total = 334
        for slice_len in (118, 26):
            budget = 2 * -(-total // slice_len) + 3
            smallest = next(m for m in range(1, slice_len + 1) if -(-total // m) <= budget)
            for m, ok in ((smallest, True), (smallest - 1, False)):
                host, ec, wire = T1.make()
                ec.devices[1] = ss.HidDevice(1, 0x045E, 0x0C01, 1, bytes(total))
                ec.max_slice = m
                if ok:
                    self.assertEqual(len(ss.read_descriptor(wire, 1, ss.DESC_REPORT, total, 'r', slice_len)), total)
                else:
                    with self.assertRaisesRegex(ss.SamError, 'does not finish'):
                        ss.read_descriptor(wire, 1, ss.DESC_REPORT, total, 'r', slice_len)
        self.assertEqual(smallest, 12)                                  # L = 26: 29 requests; L = 118: 38-byte slices need 9


class ParserLimits(unittest.TestCase):
    @staticmethod
    def field(*fields):
        b = hd.Builder().page(1).usage(6).collection(hd.COLL_APPLICATION)
        for size, count in fields:
            b.size(size).count(count).input(hd.CONST_ABS)
        return b.end().bytes()

    def test_field_and_report_bits_are_limited_exactly(self):
        for ok in ([(1, 4096)], [(32, 1024)], [(8, 4096)], [(32, 512), (32, 512)], [(16, 1024), (16, 1024)]):
            hd.parse(self.field(*ok))                                    # 32768 bits, the limit itself
        for bad in ([(1, 4097)], [(32, 1025)], [(9, 3641)], [(32, 512), (32, 512), (1, 1)], [(16, 1024), (16, 1024), (8, 1)]):
            with self.assertRaises(hd.DescError, msg=str(bad)):
                hd.parse(self.field(*bad))                               # 32769 bits or a count over 4096

    def test_the_usage_range_limit_is_for_variable_fields_and_the_total_is_exact(self):
        def var(lo, hi, count, flags=hd.DATA_VAR_ABS):
            return hd.Builder().page(7).usage(6).collection(1).size(1).count(count).umin(lo).umax(hi).input(flags).end().bytes()
        hd.parse(var(0, 4095, 4096))
        with self.assertRaises(hd.DescError):
            hd.parse(var(0, 4096, 4096))
        wide = hd.parse(var(0, 0xFFFF, 8, flags=hd.DATA_ARRAY))         # an array keeps two usages however wide its range
        self.assertEqual(wide.fields[0].usages, (0, 0xFFFF))
        b = hd.Builder().page(7).usage(6).collection(1).size(1).count(4096)
        b.umin(0).umax(4095).input(hd.DATA_VAR_ABS).umin(0).umax(4095).input(hd.DATA_VAR_ABS)
        hd.parse(b.end().bytes())                                        # 8192 usages: at the limit
        b = hd.Builder().page(7).usage(6).collection(1).size(1).count(4096)
        b.umin(0).umax(4095).input(hd.DATA_VAR_ABS).umin(0).umax(4095).input(hd.DATA_VAR_ABS)
        b.count(1).umin(0).umax(0).input(hd.DATA_VAR_ABS)
        with self.assertRaises(hd.DescError):
            hd.parse(b.end().bytes())                                    # 8193


class Mutants(unittest.TestCase):
    """Each seeded bug must make the test that guards it fail: the new tests can fail."""

    @staticmethod
    def fails(case, name):
        result = unittest.TestResult()
        unittest.TestSuite([case(name)]).run(result)
        return not result.wasSuccessful()

    def test_seeded_bugs_are_caught(self):
        real_scanner, real_decode, real_read = ss.FrameScanner, gp.rcg2_decode_hz, ss.read_descriptor

        class GreaterScanner(real_scanner):                              # flush only when strictly older than the limit
            def poll(self, now_us):
                if self._partial_since is not None and now_us - self._partial_since > ss.STALL_FLUSH_US:
                    self._partial_since = None
                    del self.buf[:2]
                    self.stats['stalled'] += 1
                    return [('junk', 'stalled')] + self._scan(now_us)
                return []

        class DropOneScanner(real_scanner):                              # flush that discards one byte, not the sync pair
            def poll(self, now_us):
                if self._partial_since is not None and now_us - self._partial_since >= ss.STALL_FLUSH_US:
                    self._partial_since = None
                    del self.buf[:1]
                    self.stats['stalled'] += 1
                    return [('junk', 'stalled')] + self._scan(now_us)
                return []

        def lenient_read(wire, iid, entry, total, step, slice_len=ss.SLICE_DATA):   # no check that a slice is no longer than asked
            out, offset = bytearray(total), 0
            for _ in range(2 * -(-total // slice_len) + 3):
                payload = bytes((entry,)) + offset.to_bytes(4, 'little') + slice_len.to_bytes(4, 'little') + b'\x00'
                r = ss.do_request(wire, ss.TC_HID, ss.TID_KIP, iid, ss.HID_GET_DESCRIPTOR, payload, step).response
                off, length, end = int.from_bytes(r[1:5], 'little'), int.from_bytes(r[5:9], 'little'), r[9]
                out[off:off + length] = r[10:10 + length][:total - off]
                offset = off + length
                if end or offset >= total:
                    return bytes(out)
            raise ss.SamError(f'{step}: descriptor read does not finish', step)

        altered = tuple((64_000_000, gp.SRC_GPLL0_EVEN, 1, 17, 75) if r[0] == 64_000_000 else r for r in gp.S2_TABLE)
        widened = dict(gp.UNDO_FIXED, rx_cancel=('se2', gp.S_CMD_CTRL, FULL, 'cmd', (4, FULL)))
        cases = [
            (mock.patch.object(ss, 'FrameScanner', GreaterScanner), Scanner, 'test_the_flush_boundary_is_inclusive_and_the_timer_measures_silence'),
            (mock.patch.object(ss, 'FrameScanner', DropOneScanner), Scanner, 'test_the_flush_drops_exactly_the_sync_pair'),
            (mock.patch.object(ss, 'read_descriptor', lenient_read), DescriptorRead, 'test_a_slice_longer_than_asked_is_refused'),
            (mock.patch.multiple(hd, MAX_FIELD_BITS=32767), ParserLimits, 'test_field_and_report_bits_are_limited_exactly'),
            (mock.patch.multiple(hd, MAX_REPORT_BITS=32767), ParserLimits, 'test_field_and_report_bits_are_limited_exactly'),
            (mock.patch.multiple(hd, MAX_REPORT_BITS=32769), ParserLimits, 'test_field_and_report_bits_are_limited_exactly'),
            (mock.patch.multiple(hd, MAX_TOTAL_USAGES=8191), ParserLimits, 'test_the_usage_range_limit_is_for_variable_fields_and_the_total_is_exact'),
            (mock.patch.object(gp, 'rcg2_decode_hz', lambda w, s=None: real_decode(dict(w, cfg=w['cfg'] & ~(1 << 12)), s)), DfsDecision,
             'test_a_mode_one_level_word_is_a_running_counter'),
            (mock.patch.object(gp, 'UNDO_FIXED', widened), UndoGuard, 'test_the_reviewed_undo_passes_and_each_hand_edit_is_refused'),
            (mock.patch.object(gp, '_recorded', lambda recorded, name: 0), UndoGuard, 'test_missing_recorded_words_are_a_stop_not_a_default'),
            (mock.patch.object(gp, '_as_word', lambda value, name: int(value)), Malformed, 'test_a_malformed_word_the_plan_reads_is_a_stop'),
            (mock.patch.object(gp, 'engine_stops', lambda *a: []), EngineGate, 'test_the_engine_rows_exist_only_after_the_engine_reads_allow_them'),
            (mock.patch.object(gp, 'S2_TABLE', altered), PinnedRows, 'test_every_row_is_what_the_pinned_source_says'),
        ]
        for patcher, case, name in cases:
            with self.subTest(name):
                self.assertFalse(self.fails(case, name), 'the unmutated test must pass')
                with patcher:
                    self.assertTrue(self.fails(case, name), 'the seeded bug went unnoticed')


if __name__ == '__main__':
    unittest.main(verbosity=1)
