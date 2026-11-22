#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Survey words, decisions, write rows and undo for bringing up the SP11 SAM link's GENI UART (QUP wrapper 0, engine 2).

EXPERIMENTAL and host-only. Nothing here touches hardware: it decodes the raw words a read-only snapshot would print, decides
from them which writes are legitimate (or says STOP), computes the register words, and writes down the undo, so that all of it
can be checked on a host before an operator approves a boot.

Evidence: every constant names the pinned file it was read in (Linux v7.4-rc2, 7475ea8b58616b33c04afb976cf0fbaad9dd63fb;
drivers/clk/qcom/gcc-x1e80100.c, clk-rcg2.c, clk-rcg.h, clk-alpha-pll.c, common.c; include/linux/soc/qcom/geni-se.h;
drivers/soc/qcom/qcom-geni-se.c; drivers/tty/serial/qcom_geni_serial.c). Formulas were re-derived here, not copied.

The engine clock is a DFS clock in the pinned Linux: gcc_qupv3_wrap0_s2_clk_src is listed in gcc_dfs_clocks[] (gcc:7412, descriptor
7468-7469, registered through common.c:394-398). If the DFS flag (RCG+0x14, bit 0) is set, `clk_rcg2_dfs_ops` has no set_rate
(clk-rcg2.c:1736-1768): Linux never writes M/N/D/CFG of that RCG, the engine picks one of eight firmware-programmed levels, and
GENI_CLK_SEL indexes them (qcom-geni-se.c:679-716). If the flag is clear, the RCG is a plain one and the sequence below is a
rate change Linux does run (with the branch already on). `build_clock_plan` reads the flag first and builds one or the other, or
raises PlanError (STOP). Which of the two the unit has is UNKNOWN until the read-only snapshot has read the flag.

    python3 tools/geni_uart_plan.py reads             # the words the read-only snapshot must read
    python3 tools/geni_uart_plan.py plan words.json   # survey words (JSON object, hex strings or ints) -> plan or STOP
    python3 tools/geni_uart_plan.py baud 4000000 0x20050000
"""
import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field

GCC_BASE = 0x00100000
SE2_BASE = 0x00B88000          # QUP wrapper 0 engine 2 (hamoa.dtsi uart2/i2c2/spi2: one window, protocol chosen by firmware)
WRAP0_BASE = 0x00BC0000        # the wrapper window; QUPV3_HW_VER is at +0x4 (qcom-geni-se.c:188)
SE_PAGE = 0x1000               # every FIFO-mode register lies below +0xe40 (geni-se.h:96-155)
FULL = 0xFFFFFFFF
BASES = {'gcc': GCC_BASE, 'se2': SE2_BASE, 'wrap0': WRAP0_BASE}

# gcc-x1e80100.c:1142-1154, F(freq, src, h, m, n) = {freq, src, 2h-1, m, n} (clk-rcg.h:10).
SRC_XO, SRC_GPLL0_MAIN, SRC_GPLL0_EVEN = 'xo', 'gpll0_main', 'gpll0_even'
SRC_SEL = {SRC_XO: 0, SRC_GPLL0_MAIN: 1, SRC_GPLL0_EVEN: 6}          # gcc-x1e80100.c:220-224
XO_HZ, GPLL0_HZ = 19_200_000, 600_000_000
SRC_HZ = {SRC_XO: XO_HZ, SRC_GPLL0_MAIN: GPLL0_HZ, SRC_GPLL0_EVEN: GPLL0_HZ // 2}
S2_TABLE = (
    (7_372_800, SRC_GPLL0_EVEN, 1, 384, 15625),
    (14_745_600, SRC_GPLL0_EVEN, 1, 768, 15625),
    (19_200_000, SRC_XO, 1, 0, 0),
    (29_491_200, SRC_GPLL0_EVEN, 1, 1536, 15625),
    (32_000_000, SRC_GPLL0_EVEN, 1, 8, 75),
    (48_000_000, SRC_GPLL0_EVEN, 1, 4, 25),
    (64_000_000, SRC_GPLL0_EVEN, 1, 16, 75),
    (80_000_000, SRC_GPLL0_EVEN, 1, 4, 15),
    (96_000_000, SRC_GPLL0_EVEN, 1, 8, 25),
    (150_000_000, SRC_GPLL0_EVEN, 2, 0, 0),
    (200_000_000, SRC_GPLL0_MAIN, 3, 0, 0),
)
RCG_CMD, RCG_CFG, RCG_M, RCG_N, RCG_D = 0x42288, 0x4228C, 0x42290, 0x42294, 0x42298   # +0, +4, +8, +0xc, +0x10 (clk-rcg2.c:26-47)
DFS_CMD = RCG_CMD + 0x14       # SE_CMD_DFSR_OFFSET: bit 0 = DFS enabled, bits 4:1 = current level (clk-rcg2.c:56-57,1690-1700)
DFS_LEVELS = 8                 # MAX_PERF_LEVEL (clk-rcg2.c:55)
S2_DIV = 0x42284               # gcc_qupv3_wrap0_s2_div_clk_src, read-only in Linux (firmware-owned): gcc-x1e80100.c:2237
S2_CBCR = 0x42274              # halt word; bit 31 = clock off (clk-branch)
VOTE_APPS = 0x52020            # the apps' branch-enable vote word; bit 12 = this engine's clock (gcc-x1e80100.c:4434-4439)
VOTE_S2_BIT = 1 << 12
VOTE_GPLL0 = 0x52030           # gcc-x1e80100.c:117-118
GPLL0_MODE, GPLL0_OPMODE, GPLL0_STATUS, GPLL0_L, GPLL0_ALPHA, GPLL0_USER = 0x0, 0x4, 0xC, 0x10, 0x14, 0x18   # clk-alpha-pll.c:187-201
CMD_UPDATE = 1 << 0
MND_WIDTH, HID_WIDTH = 16, 5
CFG_MODE_DUAL_EDGE = 0x2 << 12
CFG_MASK = ((1 << HID_WIDTH) - 1) | (0x7 << 8) | (0x3 << 12) | (1 << 20)   # clk-rcg2.c:550-551, 38-43 (+ src select)
CBCR_WAIT_US, UPDATE_WAIT_US, BRANCH_OFF_DELAY_US = 200, 500, 10    # clk-branch.c:62-92; clk-rcg2.c:127-135

UART_OVERSAMPLING = 32          # qcom_geni_serial.c:84; halved for a QUP version >= 2.5 (:1439-1452)
QUP_SE_VERSION_2_5 = 0x20050000  # geni-se.h:347
MAX_CLK_PERF_LEVEL = 32         # qcom-geni-se.c:95
UART_START_READ = 0x1 << 27     # S_CMD0 opcode 1 in bits 31:27 (qcom_geni_serial.c:84-90,984; geni-se.h:410-419)
S_CMD_CTRL, S_IRQ_STATUS, S_IRQ_CLEAR, S_CMD0 = 0x634, 0x640, 0x648, 0x630
S_GENI_CMD_CANCEL, S_GENI_CMD_ABORT, S_CMD_CANCEL_EN = 1 << 2, 1 << 1, 1 << 4
S_CMD_DONE_EN, S_CMD_ABORT_EN = 1 << 0, 1 << 5     # geni-se.h S_IRQ_EN fields


class PlanError(Exception):
    """A STOP: the survey words do not allow the plan, or a row or undo is not one the reviewed set allows."""


@dataclass(frozen=True)
class Row:
    """One store. kind 'rmw': new = (old & ~mask) | value, written only if the readback differs, undo = the word read first.
    'w1c': write-one-to-clear status, always written, never compared, never undone. 'latch': a self-clearing command bit
    (reads back 0 once done). 'cmd': starts a sequencer; separate step, only after every rmw row has been read back."""
    name: str
    block: str
    offset: int
    mask: int
    value: int
    why: str
    cite: str
    kind: str = 'rmw'

    @property
    def addr(self):
        return BASES[self.block] + self.offset

    @property
    def key(self):
        return (self.block, self.offset, self.mask, self.value, self.kind)


@dataclass(frozen=True)
class Poll:
    """Read until (word & mask) == want, bounded in microseconds. Never a store."""
    name: str
    block: str
    offset: int
    mask: int
    want: int
    timeout_us: int
    cite: str


# ---- RCG, PLL and perf-table arithmetic ----------------------------------------------------------------------------

def rcg2_encode(row):
    """(M, N, D, CFG-field-bits) for one freq-table row, as clk_rcg2_configure() would store them."""
    hz, src, h, m, n = row
    out = {'cfg': (2 * h - 1) | (SRC_SEL[src] << 8), 'm': 0, 'n': 0, 'd': 0}
    if n:
        mask = (1 << MND_WIDTH) - 1
        # Linux clamps D into [m, 2(n-m)] with clamp(), which returns the upper bound when the bounds cross.
        d = min(max(n, m), 2 * (n - m))
        out['m'], out['n'], out['d'] = m, ~(n - m) & mask, ~d & mask
        if m != n:
            out['cfg'] |= CFG_MODE_DUAL_EDGE
    return out


def rcg2_decode_hz(words, src_hz=None):
    """Output frequency of an RCG from raw CFG, M, N words (D does not enter the rate); None when the source select is not
    one we model. Any non-zero mode field turns the M/N counter on, as calc_rate() does (clk-rcg2.c:182-190)."""
    src_hz = src_hz or SRC_HZ
    cfg = words['cfg']
    src = {v: k for k, v in SRC_SEL.items()}.get((cfg >> 8) & 7)
    if src is None:
        return None
    hid = cfg & ((1 << HID_WIDTH) - 1)
    hz = src_hz[src]
    if hid:
        hz = hz * 2 // (hid + 1)
    if (cfg >> 12) & 3:
        mask = (1 << MND_WIDTH) - 1
        m = words['m'] & mask
        n = (~words['n'] & mask) + m
        if n == 0:
            return None
        hz = hz * m // n
    return hz


def decode_dfs_cmd(word):
    return {'dfs': bool(word & 1), 'level': (word >> 1) & 0xF}


def dfs_level_words(words):
    """Per-level (cfg, m, n) words from the survey (clk-rcg2.c:58-60: +0x1c, +0x5c, +0x9c, 4 bytes per level). A missing or
    malformed word is a STOP, never an exception of another kind."""
    return [{'cfg': _word(words, f'lv_cfg{i}'), 'm': _word(words, f'lv_m{i}'), 'n': _word(words, f'lv_n{i}')}
            for i in range(DFS_LEVELS)]


def decode_dfs_levels(levels, src_hz=None):
    """Rates of the eight levels as clk_rcg2_dfs_populate_freq() computes them; an unmodelled source reads as 0."""
    return [rcg2_decode_hz(lv, src_hz) or 0 for lv in levels]


def perf_table(table_hz):
    """geni_se_clk_tbl_get(): round up from freq + 1 until the answer repeats. A table row with rate 0 ends the table
    (qcom_find_freq stops there, common.c:32-47) and a rate above the last row answers the last row."""
    entries = []
    for hz in table_hz:
        if hz == 0:
            break
        entries.append(hz)
    tbl, freq = [], 0
    for i in range(MAX_CLK_PERF_LEVEL):
        freq = next((hz for hz in entries if freq + 1 <= hz), entries[-1] if entries else 0)
        if freq <= 0 or (i > 0 and freq == tbl[i - 1]):
            break
        tbl.append(freq)
    return tbl


def sampling_rate(qup_version):
    return UART_OVERSAMPLING // 2 if qup_version >= QUP_SE_VERSION_2_5 else UART_OVERSAMPLING


def baud_plan(baud, qup_version, perf=None):
    """Perf-table entry and in-engine divider for a baud rate: the first entry that gets closest wins and an exact match ends the
    search, as geni_se_clk_freq_match() decides (qcom-geni-se.c:738-779). `perf` is a perf table (see perf_table)."""
    perf = perf if perf is not None else perf_table([r[0] for r in S2_TABLE])
    if not perf:
        raise PlanError('empty perf table')
    want = baud * sampling_rate(qup_version)
    best = None
    for index, hz in enumerate(perf):
        div = -(-hz // want)
        delta = want - hz // div
        if best is None or delta < best['delta']:
            best = {'index': index, 'clk_hz': hz, 'div': div, 'delta': delta, 'sampling': sampling_rate(qup_version)}
            if delta == 0:
                break
    best['baud'] = best['clk_hz'] // best['div'] // best['sampling']
    best['exact'] = best['baud'] == baud
    if best['div'] > 0xFFF:
        raise PlanError('divider does not fit the 12-bit field')
    return best


def decode_gpll0(mode, l_val, alpha, user_ctl):
    """GPLL0 rate and the OUT_EVEN divider. The Lucid OLE register set has no ALPHA_VAL_U, so alpha is 16 bits wide
    (clk-alpha-pll.c:187-201,384-386,673-676); L is 16 bits (alpha_pll_lucid_evo_recalc_rate, :2536-2550). The post divider
    sits at USER_CTL bit 10, four bits wide, value 1 meaning /2 (gcc-x1e80100.c:130-150)."""
    pll = XO_HZ * (l_val & 0xFFFF) + ((XO_HZ * (alpha & 0xFFFF)) >> 16)
    post = (user_ctl >> 10) & 0xF
    return {'locked': bool(mode >> 31), 'active': bool(mode >> 30 & 1), 'vote_fsm': bool(mode >> 20 & 1),
            'outctrl': bool(mode & 1), 'reset_n': bool(mode >> 2 & 1), 'pll_hz': pll,
            'even_hz': pll // 2 if post == 1 else None}


# ---- the survey: which words, and what they decide -----------------------------------------------------------------

def _survey_reads():
    reads = [('cbcr', 'gcc', S2_CBCR, 'engine branch halt word (bit 31 = off)'),
             ('vote', 'gcc', VOTE_APPS, 'the apps vote word (bit 12 = this engine)'),
             ('gpll0_vote', 'gcc', VOTE_GPLL0, 'GPLL0 apps vote (bit 0)'),
             ('div', 'gcc', S2_DIV, 'read-only divider below the RCG (0 = /1)'),
             ('dfs', 'gcc', DFS_CMD, 'RCG+0x14: bit 0 = DFS enabled, bits 4:1 = current level'),
             ('cmd', 'gcc', RCG_CMD, 'RCG command'), ('cfg', 'gcc', RCG_CFG, 'RCG config'), ('m', 'gcc', RCG_M, 'RCG M'),
             ('n', 'gcc', RCG_N, 'RCG N'), ('d', 'gcc', RCG_D, 'RCG D')]
    for i in range(DFS_LEVELS):
        reads.append((f'lv_cfg{i}', 'gcc', RCG_CMD + 0x1C + 4 * i, f'DFS level {i} config'))
    for i in range(DFS_LEVELS):
        reads.append((f'lv_m{i}', 'gcc', RCG_CMD + 0x5C + 4 * i, f'DFS level {i} M'))
    for i in range(DFS_LEVELS):
        reads.append((f'lv_n{i}', 'gcc', RCG_CMD + 0x9C + 4 * i, f'DFS level {i} N'))
    reads += [('gpll0_mode', 'gcc', GPLL0_MODE, 'GPLL0 mode (lock 31, active 30)'),
              ('gpll0_opmode', 'gcc', GPLL0_OPMODE, 'GPLL0 opmode'), ('gpll0_status', 'gcc', GPLL0_STATUS, 'GPLL0 status'),
              ('gpll0_l', 'gcc', GPLL0_L, 'GPLL0 L'), ('gpll0_alpha', 'gcc', GPLL0_ALPHA, 'GPLL0 alpha (600 MHz = 31.25 x 19.2)'),
              ('gpll0_user', 'gcc', GPLL0_USER, 'GPLL0 user control (post divider)'),
              ('qup_ver', 'wrap0', 0x4, 'QUPV3_HW_VER: 16x oversampling needs >= 2.5')]
    return tuple(reads)


SURVEY_READS = _survey_reads()          # gated on the wrapper's own clock words reading ON, as the engine blocks are
# Engine words, read only after the engine's own branch reads ON. Never the FIFO ports (0x700-0x7FF): a receive read pops bytes.
ENGINE_READS = tuple(('se2', off) for off in (
    0x68, 0x64, 0x40, 0x258, 0xE18, 0xE1C, 0xE20, 0xE24, 0xE28, 0x48, 0x4C, 0x60, 0x7C, 0x240, 0x22C, 0x25C, 0x268, 0x26C, 0x280,
    0x28C, 0x294, 0x2A4, 0x2A8, 0x2AC, 0x800, 0x804, 0x80C, 0x810, 0x814, 0x610, 0x614, 0x640, 0x644, 0x908, 0x254, 0x260, 0x264,
    0x284, 0x288))
IDENTITY_WORDS = ('cbcr', 'vote', 'gpll0_vote', 'dfs', 'div', 'gpll0_mode', 'gpll0_l', 'qup_ver')


@dataclass
class ClockPlan:
    mode: str                         # 'plain' or 'dfs'
    steps: list                       # Rows and Polls, in execution order
    clk_sel: int                      # the value for SE_GENI_CLK_SEL
    div: int                          # the in-engine divider
    perf: list
    baud: dict
    notes: list = field(default_factory=list)

    @property
    def rows(self):
        return [s for s in self.steps if isinstance(s, Row)]


def _as_word(value, name):
    """A 32-bit survey word: an int (not a bool) or a numeric string. Anything else is a STOP, not a crash: the words come from a
    transcription of a screen, and a malformed one must leave no row behind."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise PlanError(f'survey word {name!r} is not an integer')
    if isinstance(value, str):
        try:
            value = int(value, 0)
        except ValueError:
            raise PlanError(f'survey word {name!r} is not a number') from None
    if not 0 <= value <= FULL:
        raise PlanError(f'survey word {name!r} out of range')
    return value


def _word(words, name):
    if not isinstance(words, dict):
        raise PlanError('survey words must be an object of name -> word')
    if name not in words:
        raise PlanError(f'survey word {name!r} missing')
    return _as_word(words[name], name)


def clock_rows(target_hz, dfs_word):
    """The plain-RCG stores: the vote first (Linux changes the rate of a clock that is already on), then M, N, D, CFG and the
    latch. Raises when the DFS flag is set or was not read: a DFS RCG is never written."""
    if dfs_word is None:
        raise PlanError('the DFS flag (RCG+0x14) was not read')
    if decode_dfs_cmd(_as_word(dfs_word, 'dfs'))['dfs']:
        raise PlanError('DFS RCG: Linux never writes M/N/D/CFG of it (clk_rcg2_dfs_ops has no set_rate)')
    entry = next((r for r in S2_TABLE if r[0] == target_hz), None)
    if entry is None:
        raise PlanError(f'{target_hz} Hz is not in the pinned engine-clock table')
    w = rcg2_encode(entry)
    mask = (1 << MND_WIDTH) - 1
    rows = [vote_row()]
    if entry[4]:
        rows += [Row('rcg_m', 'gcc', RCG_M, mask, w['m'], 'MND numerator', 'clk-rcg2.c:525-528'),
                 Row('rcg_n', 'gcc', RCG_N, mask, w['n'], 'MND ~(n - m)', 'clk-rcg2.c:530-533'),
                 Row('rcg_d', 'gcc', RCG_D, mask, w['d'], 'MND ~d', 'clk-rcg2.c:535-547')]
    rows += [Row('rcg_cfg', 'gcc', RCG_CFG, CFG_MASK, w['cfg'], 'source, divider and mode', 'clk-rcg2.c:550-559,577-596'),
             Row('rcg_update', 'gcc', RCG_CMD, CMD_UPDATE, CMD_UPDATE, 'latch the new configuration; self-clears', 'clk-rcg2.c:114-138,597',
                 'latch')]
    return rows


def vote_row():
    return Row('s2_vote', 'gcc', VOTE_APPS, VOTE_S2_BIT, VOTE_S2_BIT, 'the engine clock branch (read-modify-write: other bits '
               'belong to other clocks); read fresh at execution, never taken from the snapshot', 'gcc-x1e80100.c:4434-4450')


def build_clock_plan(words, baud=4_000_000):
    """From the read-only snapshot's words to the clock steps, or STOP. The DFS flag decides everything else."""
    for name in IDENTITY_WORDS:
        if _word(words, name) == FULL:
            raise PlanError(f'{name} reads all ones: bus fault suspected, nothing is written')
    if _word(words, 'div') & 0xF:
        raise PlanError('the read-only divider below the RCG is not /1: the pinned rate table does not apply')
    pll = decode_gpll0(_word(words, 'gpll0_mode'), _word(words, 'gpll0_l'), _word(words, 'gpll0_alpha'), _word(words, 'gpll0_user'))
    if not pll['locked'] or pll['pll_hz'] != GPLL0_HZ or pll['even_hz'] != GPLL0_HZ // 2:
        raise PlanError(f'GPLL0 is not a locked 600 MHz with a /2 output: {pll}')
    if not _word(words, 'gpll0_vote') & 1:
        raise PlanError('GPLL0 is not voted on by the apps: the plan never writes it')
    qup = _word(words, 'qup_ver')
    dfs = decode_dfs_cmd(_word(words, 'dfs'))
    notes = []
    if not (pll['outctrl'] and pll['reset_n']):
        # Linux's trion_pll_is_enabled() wants RESET_N and OPMODE run, and the output enabled. Not required until the first
        # survey shows what a running GPLL0 reads on this unit (U19): a locked PLL with these clear is reported, not refused.
        notes.append('GPLL0 locked but OUTCTRL or RESET_N clear in the mode word: compare with a running PLL before I1a')
    if dfs['dfs']:
        if dfs['level'] >= DFS_LEVELS:
            raise PlanError(f"DFS current level {dfs['level']} is out of range")
        hz = decode_dfs_levels(dfs_level_words(words))
        perf = perf_table(hz)
        plan = baud_plan(baud, qup, perf)
        if not plan['exact']:
            raise PlanError(f"no DFS level gives {baud} baud exactly (nearest {plan['baud']}): {hz}")
        # What matters is that the level the selector names runs at the rate the divider was computed for: Linux's perf-table
        # index is only a level number when the levels before it are ascending, so the selected level is checked itself.
        if plan['index'] >= DFS_LEVELS or hz[plan['index']] != perf[plan['index']]:
            raise PlanError(f"perf-table index {plan['index']} does not name a level running at {plan['clk_hz']} Hz: {hz}")
        steps = [vote_row(), Poll('cbcr_on', 'gcc', S2_CBCR, 1 << 31, 0, CBCR_WAIT_US, 'clk-branch.c:62-92')]
        notes.append(f'DFS: no RCG row; GENI_CLK_SEL selects level {plan["index"]} ({hz[plan["index"]]} Hz); current level {dfs["level"]}')
        return ClockPlan('dfs', steps, plan['index'], plan['div'], perf, plan, notes)
    perf = perf_table([r[0] for r in S2_TABLE])
    plan = baud_plan(baud, qup, perf)
    if not plan['exact']:
        raise PlanError(f"{baud} baud is not reachable at QUP version {qup:#x} (nearest {plan['baud']})")
    rows = clock_rows(plan['clk_hz'], _word(words, 'dfs'))
    steps = [rows[0], Poll('cbcr_on', 'gcc', S2_CBCR, 1 << 31, 0, CBCR_WAIT_US, 'clk-branch.c:62-92')] + rows[1:] + \
        [Poll('rcg_updated', 'gcc', RCG_CMD, CMD_UPDATE, 0, UPDATE_WAIT_US, 'clk-rcg2.c:126-135')]
    notes.append('plain RCG: whether UPDATE completes on an idle RCG is UNKNOWN; the branch is already on, as in Linux')
    return ClockPlan('plain', steps, plan['index'], plan['div'], perf, plan, notes)


# ---- engine rows ---------------------------------------------------------------------------------------------------

def packing_words(bits_per_word=8, words_per_entry=4):
    """TX/RX packing vectors for plain bytes, four to a FIFO word, least significant first (qcom-geni-se.c:539-591)."""
    vectors = []
    for i in range(words_per_entry):
        vec = ((8 * i) << 5) | (7 << 1)
        if i == words_per_entry - 1:
            vec |= 1
        vectors.append(vec)
    vectors += [0] * (4 - len(vectors))
    return vectors[0] | (vectors[1] << 10), vectors[2] | (vectors[3] << 10)


def uart_rows(plan, rx_fifo_words):
    """The engine's own initialisation for 8N1, hardware CTS flow control, FIFO mode, in the order of Linux's
    qcom_geni_serial_port_setup() (stale count, packing, then geni_se_init(): clear stale status first, then IO init) followed
    by set_termios() (rate, format); the reception command is last (Linux starts it earlier, in startup: POLICY). Needs the
    clock plan's selector and divider and the FIFO depth read from HW_PARAM after clock-on. Interrupt enables (SE_IRQ_EN,
    M_IRQ_EN, S_IRQ_EN) are left as found: reception is polled."""
    if not 4 <= rx_fifo_words <= 256:
        raise PlanError('FIFO depth outside 4..256 words')
    if not 0 <= plan.clk_sel <= 7:
        raise PlanError('CLK_SEL does not fit its three bits')
    p0, p1 = packing_words()
    clk_cfg = 1 | (plan.div << 4)
    r = lambda name, off, value, why, cite, mask=FULL, kind='rmw': Row(name, 'se2', off, mask, value, why, cite, kind)
    return [
        r('rx_stale', 0x294, 160, 'flush a partial last word after 16 character times (10 bits each) of silence',
          'qcom_geni_serial.c:1338,1361'),
        r('tx_pack0', 0x260, p0, 'bytes, four to a word', 'qcom-geni-se.c:574'),
        r('tx_pack1', 0x264, p1, 'bytes, four to a word', 'qcom-geni-se.c:575'),
        r('rx_pack0', 0x284, p0, 'bytes, four to a word', 'qcom-geni-se.c:578'),
        r('rx_pack1', 0x288, p1, 'bytes, four to a word', 'qcom-geni-se.c:579'),
        r('byte_gran', 0x254, 0, 'four 8-bit words per entry', 'qcom-geni-se.c:589-590'),
        r('m_irq_clear', 0x618, FULL, 'clear stale primary status before anything else', 'qcom-geni-se.c:345-352', kind='w1c'),
        r('s_irq_clear', 0x648, FULL, 'clear stale secondary status', 'qcom-geni-se.c:345-352', kind='w1c'),
        r('cgc_ctrl', 0x28, 0x7F, 'clock gating on, as geni_se_init() sets', 'qcom-geni-se.c:332-334', mask=0x7F),
        r('dma_cgc', 0xE30, 0xF, 'DMA clock gating on', 'qcom-geni-se.c:336-339', mask=0xF),
        r('output_ctrl', 0x24, 0x7F, 'default output control', 'qcom-geni-se.c:341'),
        # Linux forces the IO outputs to their defaults here; the effect on RTS and TX while the EC is attached is UNKNOWN.
        r('force_default', 0x20, 1, 'as geni_se_io_init() does', 'qcom-geni-se.c:342'),
        r('dma_off', 0x258, 0, 'FIFO mode, not DMA', 'qcom-geni-se.c:321-323', mask=1),
        r('rx_wm', 0x810, 2, 'receive watermark in words', 'qcom_geni_serial.c:91,1384; qcom-geni-se.c:372'),
        r('rx_rfr_wm', 0x814, rx_fifo_words - 2, 'RTS drops two words before the FIFO is full',
          'qcom_geni_serial.c:1384; qcom-geni-se.c:373'),
        r('m_clk', 0x48, clk_cfg, f'divider {plan.div}, enabled', 'qcom_geni_serial.c:1477'),
        r('s_clk', 0x4C, clk_cfg, f'divider {plan.div}, enabled', 'qcom_geni_serial.c:1478'),
        r('clk_sel', 0x7C, plan.clk_sel, f'the {plan.mode} perf-table index: a DFS level number or the plain table index',
          'qcom_geni_serial.c:1480; qcom-geni-se.c:679-716', mask=7),
        r('tx_cfg', 0x25C, 0, 'no parity, hardware CTS flow control (bit 1 clear)', 'qcom_geni_serial.c:1527,1551,1576', mask=0x3),
        r('rx_cfg', 0x280, 0, 'no parity', 'qcom_geni_serial.c:1528,1578', mask=0x10),
        r('tx_par', 0x2A4, 0, 'no parity calculation', 'qcom_geni_serial.c:1529,1577', mask=1),
        r('rx_par', 0x2A8, 0, 'no parity calculation', 'qcom_geni_serial.c:1530,1579', mask=1),
        r('manual_rfr', 0x2AC, 0, 'RTS follows the FIFO (hardware flow control), not software', 'qcom_geni_serial.c:253-270'),
        r('loopback', 0x22C, 0, 'no loopback', 'qcom_geni_serial.c:1574-1575'),
        r('word_len_tx', 0x268, 8, '8 data bits', 'qcom_geni_serial.c:1580'),
        r('word_len_rx', 0x28C, 8, '8 data bits', 'qcom_geni_serial.c:1581'),
        r('stop_tx', 0x26C, 0, 'one stop bit', 'qcom_geni_serial.c:1582'),
        r('start_read', S_CMD0, UART_START_READ, 'secondary sequencer: start reading (UART_START_READ), FIFO mode',
          'qcom_geni_serial.c:984; geni-se.h:410-419', kind='cmd'),
    ]


# The reviewed shapes, written separately from the generators above so that a generator cannot widen its own guard.
GCC_ALLOWED = {RCG_M: (0xFFFF, 'rmw'), RCG_N: (0xFFFF, 'rmw'), RCG_D: (0xFFFF, 'rmw'), RCG_CFG: (CFG_MASK, 'rmw'),
               RCG_CMD: (CMD_UPDATE, 'latch'), VOTE_APPS: (VOTE_S2_BIT, 'rmw')}
ENGINE_ALLOWED = {
    0x294: (FULL, 'rmw'), 0x260: (FULL, 'rmw'), 0x264: (FULL, 'rmw'), 0x284: (FULL, 'rmw'), 0x288: (FULL, 'rmw'),
    0x254: (FULL, 'rmw'), 0x618: (FULL, 'w1c'), 0x648: (FULL, 'w1c'), 0x28: (0x7F, 'rmw'), 0xE30: (0xF, 'rmw'),
    0x24: (FULL, 'rmw'), 0x20: (FULL, 'rmw'), 0x258: (1, 'rmw'), 0x810: (FULL, 'rmw'), 0x814: (FULL, 'rmw'),
    0x48: (FULL, 'rmw'), 0x4C: (FULL, 'rmw'), 0x7C: (7, 'rmw'), 0x25C: (0x3, 'rmw'), 0x280: (0x10, 'rmw'),
    0x2A4: (1, 'rmw'), 0x2A8: (1, 'rmw'), 0x2AC: (FULL, 'rmw'), 0x22C: (FULL, 'rmw'), 0x268: (FULL, 'rmw'),
    0x28C: (FULL, 'rmw'), 0x26C: (FULL, 'rmw'), S_CMD0: (FULL, 'cmd')}
# Never an initialisation row: the primary command registers, the abort controls and the data ports (receive reads pop bytes).
ENGINE_FORBIDDEN = (0x600, 0x604, S_CMD_CTRL) + tuple(range(0x700, 0x800, 4))


def verify_rows(rows, allow=None):
    """Every row must be one of the reviewed shapes: the same (block, offset) with exactly the reviewed mask and kind, a value
    inside its mask, a command row carrying exactly the reception opcode, no duplicates. With `allow` (a frozenset of
    Row.key made from the reviewed table, see allowlist()) every row must also match it exactly. Raises PlanError."""
    seen = set()
    for row in rows:
        if row.value & ~row.mask:
            raise PlanError(f'{row.name}: value has bits outside its mask')
        if row.addr & 3:
            raise PlanError(f'{row.name}: unaligned')
        if (row.block, row.offset) in seen:
            raise PlanError(f'{row.name}: duplicate row')
        seen.add((row.block, row.offset))
        if row.block == 'se2':
            if row.offset + 4 > SE_PAGE:
                raise PlanError(f'{row.name}: outside the one 4 KiB page the stage-2 grant would map')
            if row.offset in ENGINE_FORBIDDEN:
                raise PlanError(f'{row.name}: a command, abort or data-port register is never an initialisation row')
            shape = ENGINE_ALLOWED.get(row.offset)
        elif row.block == 'gcc':
            # The divider just below the RCG is firmware-owned and read-only in Linux, so it is deliberately absent.
            shape = GCC_ALLOWED.get(row.offset)
        else:
            raise PlanError(f'{row.name}: unknown block')
        if shape is None:
            raise PlanError(f'{row.name}: {row.block}+{row.offset:#x} is not a reviewed register')
        if (row.mask, row.kind) != shape:
            raise PlanError(f'{row.name}: mask {row.mask:#x} kind {row.kind} differs from the reviewed {shape[0]:#x} {shape[1]}')
        if row.kind == 'cmd' and row.value != UART_START_READ:
            raise PlanError(f'{row.name}: a command row carries only the reception opcode')
        if row.kind == 'w1c' and row.value == 0:
            raise PlanError(f'{row.name}: a write-one-to-clear row that clears nothing')
        if allow is not None and row.key not in allow:
            raise PlanError(f'{row.name}: not in the reviewed exact allow-list')
    return True


def allowlist(rows):
    """The exact allow-list a reviewed table commits to: what the write table's guard must contain, nothing wider."""
    verify_rows(rows)
    return frozenset(r.key for r in rows)


def rows_digest(rows):
    """SHA-256 of the ordered rows: the name an approval record can carry."""
    text = ''.join(f'{r.block} {r.offset:#x} {r.mask:#x} {r.value:#x} {r.kind}\n' for r in rows)
    return hashlib.sha256(text.encode()).hexdigest()


# ---- undo ----------------------------------------------------------------------------------------------------------

IRREVERSIBLE = ('write-one-to-clear status of an active transfer', 'the effect of FORCE_DEFAULT on the RTS and TX pads',
                'bytes already read from the receive FIFO', 'the controller\'s own event and power state (U11)',
                'a command the firmware left active, if the engine was not idle')


@dataclass(frozen=True)
class Undo:
    op: str            # 'write' | 'poll' | 'drain' | 'wait'
    name: str
    row: object = None
    detail: str = ''
    when: str = ''     # a step that runs only on a condition (the abort fallback, when the receive command stays active)


WHEN_STILL_ACTIVE = 'rx_idle did not read 0 within its timeout'


def _recorded(recorded, name):
    """The full register word read before the run for `name`; missing or malformed is a STOP, never a default."""
    if not isinstance(recorded, dict) or name not in recorded:
        raise PlanError(f'the word read before the run is missing for {name!r}')
    return _as_word(recorded[name], name)


def undo_steps(plan, rows, recorded, started_rx=False):
    """The complete reverse of a run: clearing the vote undoes only the gate. The engine's rows and the RCG keep what was written
    unless rewritten from the words read first (`recorded`: row name -> the full register word before the write; 'vote' is the
    apps vote word, required: without it the vote would be dropped blind)."""
    steps = []
    vote_held = _recorded(recorded, 'vote') & VOTE_S2_BIT
    if started_rx:
        cite = 'qcom_geni_serial.c'
        steps += [Undo('write', 'rx_cancel', Row('rx_cancel', 'se2', S_CMD_CTRL, FULL, S_GENI_CMD_CANCEL, 'cancel the receive command',
                                                 cite + ':959', 'cmd')),
                  Undo('poll', 'rx_cancelled', Poll('rx_cancelled', 'se2', S_IRQ_STATUS, S_CMD_CANCEL_EN, S_CMD_CANCEL_EN, 5000,
                                                    cite + ':960-961')),
                  Undo('drain', 'rx_drain', detail='read the receive FIFO until its word count is zero (reads pop)'),
                  Undo('write', 'rx_status_clear', Row('rx_status_clear', 'se2', S_IRQ_CLEAR, FULL, FULL, 'clear the secondary status',
                                                       cite + ':970', 'w1c')),
                  Undo('poll', 'rx_idle', Poll('rx_idle', 'se2', 0x40, 1 << 12, 0, 1000, cite + ':972-973')),
                  # qcom_geni_serial_abort_rx() (:381-391), reached only when the command is still active after the cancel
                  Undo('write', 'rx_abort', Row('rx_abort', 'se2', S_CMD_CTRL, FULL, S_GENI_CMD_ABORT, 'abort the receive command',
                                                cite + ':385', 'cmd'), when=WHEN_STILL_ACTIVE),
                  Undo('poll', 'rx_abort_done', Poll('rx_abort_done', 'se2', S_CMD_CTRL, S_GENI_CMD_ABORT, 0, 1000, cite + ':386-387'),
                       when=WHEN_STILL_ACTIVE),
                  Undo('write', 'rx_abort_clear', Row('rx_abort_clear', 'se2', S_IRQ_CLEAR, FULL, S_CMD_DONE_EN | S_CMD_ABORT_EN,
                                                      'clear done and abort', cite + ':388', 'w1c'), when=WHEN_STILL_ACTIVE),
                  Undo('write', 'rx_force_default', Row('rx_force_default', 'se2', 0x20, FULL, 1, 'FORCE_DEFAULT, as Linux does',
                                                        cite + ':389'), when=WHEN_STILL_ACTIVE)]
    engine = [r for r in rows if r.block == 'se2' and r.kind == 'rmw']
    for r in reversed(engine):
        steps.append(Undo('write', r.name + '_undo', Row(r.name + '_undo', r.block, r.offset, r.mask,
                                                         _recorded(recorded, r.name) & r.mask, 'restore the recorded word', r.cite)))
    if plan.mode == 'plain':
        for r in rows:
            if r.block == 'gcc' and r.kind == 'rmw' and r.offset != VOTE_APPS:
                steps.append(Undo('write', r.name + '_undo', Row(r.name + '_undo', r.block, r.offset, r.mask,
                                                                 _recorded(recorded, r.name) & r.mask, 'restore the recorded word',
                                                                 r.cite)))
        steps.append(Undo('write', 'rcg_update_undo', Row('rcg_update_undo', 'gcc', RCG_CMD, CMD_UPDATE, CMD_UPDATE,
                                                         'second latch for the restored words', 'clk-rcg2.c:114-138', 'latch')))
        steps.append(Undo('poll', 'rcg_updated_undo', Poll('rcg_updated_undo', 'gcc', RCG_CMD, CMD_UPDATE, 0, UPDATE_WAIT_US,
                                                           'clk-rcg2.c:126-135')))
    if not vote_held:
        steps.append(Undo('write', 'vote_clear', Row('vote_clear', 'gcc', VOTE_APPS, VOTE_S2_BIT, 0,
                                                     'drop the vote last (only if it was not set when the run began)',
                                                     'gcc-x1e80100.c:4434-4450')))
        steps.append(Undo('wait', 'branch_off_delay', detail=f'{BRANCH_OFF_DELAY_US} us; the halt word may still read ON if '
                                                             'another master votes (clk-branch.c:75-76)'))
    return steps


# The fixed undo writes, by step name, exactly: (block, offset, mask, kind, the only values). The forward allow-list cannot carry
# these (it holds forward values), and a restore's value is known only at execution: it must equal the word read first.
UNDO_FIXED = {
    'rx_cancel': ('se2', S_CMD_CTRL, FULL, 'cmd', (S_GENI_CMD_CANCEL,)),
    'rx_abort': ('se2', S_CMD_CTRL, FULL, 'cmd', (S_GENI_CMD_ABORT,)),
    'rx_status_clear': ('se2', S_IRQ_CLEAR, FULL, 'w1c', (FULL,)),
    'rx_abort_clear': ('se2', S_IRQ_CLEAR, FULL, 'w1c', (S_CMD_DONE_EN | S_CMD_ABORT_EN,)),
    'rx_force_default': ('se2', 0x20, FULL, 'rmw', (1,)),
    'rcg_update_undo': ('gcc', RCG_CMD, CMD_UPDATE, 'latch', (CMD_UPDATE,)),
    'vote_clear': ('gcc', VOTE_APPS, VOTE_S2_BIT, 'rmw', (0,)),
}
UNDO_POLLS = {'rx_cancelled': ('se2', S_IRQ_STATUS), 'rx_idle': ('se2', 0x40), 'rx_abort_done': ('se2', S_CMD_CTRL),
              'rcg_updated_undo': ('gcc', RCG_CMD)}
UNDO_OTHER = {'rx_drain': 'drain', 'branch_off_delay': 'wait'}
RX_UNDO_ORDER = ('rx_cancel', 'rx_cancelled', 'rx_drain', 'rx_status_clear', 'rx_idle', 'rx_abort', 'rx_abort_done',
                 'rx_abort_clear', 'rx_force_default')
RX_ABORT_GROUP = ('rx_abort', 'rx_abort_done', 'rx_abort_clear', 'rx_force_default')
UNDO_POLL_MAX_US = 10_000


def undo_shapes(plan, rows, started_rx=True):
    """(block, offset, mask, kind) of every store the undo may make: what the write table's undo guard must allow. Values are
    checked separately (fixed values above, restores equal to the recorded word)."""
    shapes = {(b, o, m, k) for name, (b, o, m, k, _v) in UNDO_FIXED.items()
              if (started_rx or not name.startswith('rx_')) and (plan.mode == 'plain' or name != 'rcg_update_undo')}
    shapes |= {(r.block, r.offset, r.mask, 'rmw') for r in rows
               if r.kind == 'rmw' and r.name != 's2_vote' and (r.block == 'se2' or plan.mode == 'plain')}
    return frozenset(shapes)


def _verify_undo_step(plan, step, forward, recorded):
    name = step.name
    if (step.when != '') != (name in RX_ABORT_GROUP) or (step.when and step.when != WHEN_STILL_ACTIVE):
        raise PlanError(f'{name}: only the abort fallback is conditional')
    if step.op == 'write':
        row = step.row
        if name in UNDO_FIXED:
            block, offset, mask, kind, values = UNDO_FIXED[name]
            if (row.block, row.offset, row.mask, row.kind) != (block, offset, mask, kind) or row.value not in values:
                raise PlanError(f'{name}: not the reviewed undo store')
            if name == 'rcg_update_undo' and plan.mode != 'plain':
                raise PlanError(f'{name}: a DFS RCG is never written')
        elif name.endswith('_undo') and name[:-5] in forward:
            fwd = forward[name[:-5]]
            want = (fwd.block, fwd.offset, fwd.mask, 'rmw', _recorded(recorded, fwd.name) & fwd.mask)
            if (row.block, row.offset, row.mask, row.kind, row.value) != want:
                raise PlanError(f'{name}: a restore must rewrite the word read first, under the forward mask')
        else:
            raise PlanError(f'undo step {name!r} is not one the reviewed set allows')
        if row.kind == 'rmw':
            verify_rows([row])
    elif step.op == 'poll':
        poll = step.row
        if UNDO_POLLS.get(name) != (poll.block, poll.offset) or not 0 < poll.timeout_us <= UNDO_POLL_MAX_US:
            raise PlanError(f'{name}: not a reviewed bounded poll')
    elif UNDO_OTHER.get(name) != step.op:
        raise PlanError(f'undo step {name!r} ({step.op}) is not one the reviewed set allows')


def verify_undo(plan, rows, steps, recorded, started_rx=False):
    """Refuse an undo that is not exactly the reviewed reverse: every store is checked against UNDO_FIXED or against the word
    read first (a redirected cancel, a stray row, a wrong restore value all fail), every forward rmw row is restored once,
    the plain RCG is latched again after its words are back, the receive command is cancelled, drained and quiet before any
    word is restored, and the vote is dropped last, and only if it was not held when the run began. Raises PlanError."""
    vote_held = bool(_recorded(recorded, 'vote') & VOTE_S2_BIT)
    forward = {r.name: r for r in rows if r.kind == 'rmw' and r.name != 's2_vote'}
    names = [s.name for s in steps]
    if len(set(names)) != len(names):
        raise PlanError('an undo step appears twice')
    for step in steps:
        _verify_undo_step(plan, step, forward, recorded)
    pos = {n: i for i, n in enumerate(names)}
    for r in forward.values():
        if (r.block == 'se2' or plan.mode == 'plain') and r.name + '_undo' not in pos:
            raise PlanError(f'undo does not restore {r.name}')
    if plan.mode == 'plain':
        if 'rcg_update_undo' not in pos or 'rcg_updated_undo' not in pos or pos['rcg_update_undo'] > pos['rcg_updated_undo']:
            raise PlanError('undo restores the RCG words but does not latch them')
        if not all(pos[n] < pos['rcg_update_undo'] for n in ('rcg_m_undo', 'rcg_n_undo', 'rcg_d_undo', 'rcg_cfg_undo') if n in pos):
            raise PlanError('the RCG words must be restored before the latch')
    rx = [n for n in RX_UNDO_ORDER if n in pos]
    if started_rx and rx != list(RX_UNDO_ORDER):
        raise PlanError('undo does not cancel, drain and, if need be, abort the receive command')
    if rx:
        if [pos[n] for n in rx] != sorted(pos[n] for n in rx):
            raise PlanError('the receive command undo steps are out of order')
        if any(n.endswith('_undo') and n not in ('rcg_update_undo', 'rcg_updated_undo') and pos[n] < pos[rx[-1]] for n in names):
            raise PlanError('the receive command must be cancelled before any word is restored')
    writes = [n for n in names if n != 'branch_off_delay']
    if any(r.name == 's2_vote' for r in rows) and not vote_held and 'vote_clear' not in pos:
        raise PlanError('undo does not drop the vote')
    if vote_held and 'vote_clear' in pos:
        raise PlanError('the vote was already held by another master: it is not ours to drop')
    if 'vote_clear' in pos and writes[-1] != 'vote_clear':
        raise PlanError('the vote must be dropped last')
    return True


# ---- decoders for the words a snapshot prints ----------------------------------------------------------------------

def decode_cbcr(word):
    return {'off': bool(word >> 31), 'raw': word}


def decode_qup_version(word):
    return {'major': word >> 28, 'minor': (word >> 16) & 0xFFF, 'step': word & 0xFFFF, 'sampling': sampling_rate(word)}


def decode_fw_revision(word):
    proto = (word >> 8) & 0xFF
    return {'protocol': proto, 'uart': proto == 2, 'no_firmware': proto == 255, 'version': word & 0xFF}


def decode_pll_mode(word):
    return {'locked': bool(word >> 31), 'active': bool(word >> 30 & 1), 'vote_fsm': bool(word >> 20 & 1),
            'outctrl': bool(word & 1), 'reset_n': bool(word >> 2 & 1)}


def decode_fifo_status(word):
    """Word count and byte count of the receive FIFO status. Linux assumes only the LAST word can be partial and reads the status
    from the RX_LAST interrupt (qcom_geni_serial.c:412-422,519-531); the stale timer is 160 bit times = 40 us at 4 Mbps, so a
    poll slower than that can see a partial word in the middle of the FIFO (UNKNOWN: read the status once per drain)."""
    count, partial, last_valid = word & ((1 << 25) - 1), bool(word >> 31), (word >> 28) & 7
    nbytes = 0
    if count:
        nbytes = 4 * (count - 1) + (last_valid if partial and last_valid else 4)
    return {'words': count, 'bytes': nbytes, 'last_partial': partial}


def decode_hw_params(hw0, hw1, qup_version):
    """FIFO geometry from SE_HW_PARAM_0 (transmit) and SE_HW_PARAM_1 (receive): depth in bits 21:16 (23:16 from QUP 3.10),
    width in 29:24 (geni-se.h:152-155,319-331,528-544). 16 words is only the early-console default (qcom_geni_serial.c:1920-1922)."""
    major, minor = qup_version >> 28, (qup_version >> 16) & 0xFFF
    wide = (major == 3 and minor >= 10) or major > 3
    mask = 0xFF if wide else 0x3F
    return {'tx_depth': (hw0 >> 16) & mask, 'tx_width': (hw0 >> 24) & 0x3F, 'rx_depth': (hw1 >> 16) & mask, 'rx_width': (hw1 >> 24) & 0x3F}


def engine_stops(words, qup_version):
    """The abort rules for the first engine read after clock-on: every reason not to go on. Empty means the plan may continue.
    This is the second guard group of the write table (the first is read before the clock write): STATUS, the protocol and
    IF_DISABLE exist only once the engine's own clock runs. Missing or malformed words are a STOP."""
    fw, ifd, status = (_word(words, n) for n in ('fw', 'ifd', 'status'))
    hw0, hw1 = _word(words, 'hw0'), _word(words, 'hw1')
    stops = [f'{name} reads all ones: the engine is not answering' for name, w in (('fw', fw), ('ifd', ifd), ('status', status))
             if w == FULL]
    if stops:
        return stops
    fw = decode_fw_revision(fw)
    if fw['no_firmware']:
        stops.append('protocol 0xFF: no firmware loaded (Linux loads one only with a firmware-name; none is described)')
    elif not fw['uart']:
        stops.append(f"protocol {fw['protocol']}: not the UART")
    if ifd & 1:
        stops.append('IF_DISABLE bit 0: the FIFO interface is disabled and its enable (+0x2008) lies outside the granted page '
                     '(qcom-geni-se.c:1538-1541)')
    if status & (1 | (1 << 12)):
        stops.append('STATUS shows the main or secondary command active: a firmware-owned engine')
    geometry = decode_hw_params(hw0, hw1, qup_version)
    if not 4 <= geometry['rx_depth'] <= 256:
        stops.append(f"receive FIFO depth {geometry['rx_depth']} words is implausible")
    return stops


def engine_rows(plan, words, qup_version):
    """The engine rows, only if the first engine reads after clock-on allow them: fails closed (PlanError naming every stop)
    instead of leaving the carrier to remember to call engine_stops()."""
    stops = engine_stops(words, qup_version)
    if stops:
        raise PlanError('; '.join(stops))
    return uart_rows(plan, decode_hw_params(_word(words, 'hw0'), _word(words, 'hw1'), qup_version)['rx_depth'])


def fifo_fill_us(baud=4_000_000, fifo_bytes=64, bits_per_char=10):
    """Time for a silent host to see a FIFO overrun if the sender ignores RTS: the bound on a poll interval without flow control."""
    return fifo_bytes * bits_per_char * 1_000_000 // baud


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('reads')
    p = sub.add_parser('plan')
    p.add_argument('words')
    b = sub.add_parser('baud')
    b.add_argument('baud', type=lambda s: int(s, 0))
    b.add_argument('qup_version', type=lambda s: int(s, 0))
    args = ap.parse_args(argv)
    if args.cmd == 'baud':
        print(baud_plan(args.baud, args.qup_version))
        return 0
    if args.cmd == 'reads':
        for name, block, off, why in SURVEY_READS:
            print(f'{name:12} {BASES[block] + off:#010x}  {why}')
        for block, off in ENGINE_READS:
            print(f'engine       {BASES[block] + off:#010x}')
        return 0
    try:
        with open(args.words) as f:
            words = json.load(f)
        plan = build_clock_plan(words)
        verify_rows(plan.rows)           # before anything is printed: a printed step is a step someone may copy
    except (PlanError, OSError, ValueError) as exc:
        print('STOP:', exc)
        return 1
    for step in plan.steps:
        print(step)
    print('digest', rows_digest(plan.rows), *plan.notes, sep='\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
