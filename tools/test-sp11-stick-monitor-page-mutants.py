#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Mutants of the source of SNAPREP-M1.BIN (probes/sp11-stick-reporter.c): each is the reporter with one fault seeded, built in the harness of
tools/test-sp11-stick-monitor-page.py, and each must fail the check that matches its fault on at least one window: the registers in another order, x30 and the SP
exchanged, a histogram one bucket off, two counters' names swapped, a width guard removed (the clock line, the counters, the guest timer's line: a value cut at the runner's
100 columns is a wrong value), a capture word taken for a class the monitor never stores, a clock sample above the call's end taken for a number, the fields of the syndrome
of a stage-2 fault read from the wrong bits, the value written taken from the wrong register, a load or a table walk shown as a store, and the budget's reserve for the first
event and the first console line. A second family builds the reporter with three lines less room for the window and holds the budget's own mutants to it. The windows are the
page test's fixtures, a failed prepare and the syndromes of ESR_CASES. A mutant that survives is a fault no window exposes.

    python3 tools/test-sp11-stick-monitor-page-mutants.py

The tests need clang and a q1n1 checkout (Q1N1_SP11_TREE); without them they SKIP, which is not a pass. No hardware, nothing under /Volumes, no network. EXPERIMENTAL; hardware NOT RUN."""
import importlib.util
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
_load = importlib.util.spec_from_file_location('sp11_page_tests', Path(__file__).with_name('test-sp11-stick-monitor-page.py'))
page = importlib.util.module_from_spec(_load)
_load.loader.exec_module(page)
P, T, M, FIX, REPORTER_C = page.P, page.T, page.M, page.FIX, page.REPORTER_C


def setUpModule():
    page.setUpModule()


def tearDownModule():
    page.tearDownModule()


REGISTER_WORD = 'rp_word(w, RP_W_ASYNC + 8 * (1 + 4 * i + j))'
CAPTURE_CHECK = 'if (captured - 1 >= RP_CAPTURE_MAX) {'
MARKER_CHECK = 'if (captured && status == 0) {'
ESR_DECODE = '&& ((esr >> 6) & 1) && !((esr >> 7) & 1);'
RESERVE_CNT = '(used == budget && k + 1 < RP_COUNTERS ? RP_MORE_WIDTH : 0)'      # the room the last line keeps for MORE while counters follow
RESERVE_TIM = '(used == budget && k + 1 < RP_TIM_FIELDS ? RP_MORE_WIDTH : 0)'
RESERVE_ACK = '(total - (k + 1) ? 6 + rp_dec_len(total - (k + 1)) : 0)'      # the room the acknowledge line keeps for ` MORE=n` while stores follow
TIM_SECOND = 'rp_room(*lines) - keep >= RP_TIM_LINES_MAX + RP_CNT_LINES + ack'
ACK_MATCH = 'ack = rp_word(w, RP_W_ACK) == RP_ACK_MAGIC;'
ACK_LAYOUT_CHECK = 'if (rp_word(w, RP_W_ACK + 8) != RP_ACK_LAYOUT) {'
ACK_CALL = 'if (ack && rp_room(*lines) >= 1 + keep) rp_ack('
STOP_SPLIT = 'if (l.len + 5 + rp_hex_len(sp11sb_rd64(w + RP_W_TERMINAL + 32)) > Q1N1_PAYLOAD_LINE_MAX) {'
NEXT_LINE = '        if (used == budget) break;\n        rp_emit(a, &l, lines);\n        used++;\n        rp_begin(&l);\n        rp_put(&l, "MON %s");'
MUTANTS = {
    'the registers of a line in reverse order': [(REGISTER_WORD, 'rp_word(w, RP_W_ASYNC + 8 * (1 + 4 * i + (3 - j)))')],
    'x30 and the SP exchanged': [(REGISTER_WORD, 'rp_word(w, RP_W_ASYNC + 8 * (1 + 4 * i + j) + (i == 7 && j == 2 ? 8 : (i == 7 && j == 3 ? (uint32_t)-8 : 0)))')],
    'the lines of registers start at x1': [(REGISTER_WORD, 'rp_word(w, RP_W_ASYNC + 8 * (4 * i + j + 2))')],
    'the histogram one bucket late': [('calls = rp_word(w, RP_W_ENDPOINTS + 8 * n);', 'calls = rp_word(w, RP_W_ENDPOINTS + 8 * (n + 1));')],
    'the histogram one bucket early': [('calls = rp_word(w, RP_W_ENDPOINTS + 8 * n);', 'calls = rp_word(w, RP_W_ENDPOINTS + 8 * (n ? n - 1 : 0));')],
    'the endpoint number off by one': [('rp_put_dec(&l, " ", n);', 'rp_put_dec(&l, " ", n + 1);')],
    'the other bucket left out of the total': [('uint64_t other = rp_word(w, RP_W_ENDPOINTS + 8 * RP_ENDPOINTS), total = other, calls;',
                                                'uint64_t other = rp_word(w, RP_W_ENDPOINTS + 8 * RP_ENDPOINTS), total = 0, calls;')],
    'two counters\' names swapped': [('case 10: return "MMIORD";', 'case 10: return "MMIOWR";'), ('case 11: return "MMIOWR";', 'case 11: return "MMIORD";')],
    'a counter read from the next word': [('rp_put_dec(&l, "=", rp_word(w, RP_W_COUNTERS + 8 * k));', 'rp_put_dec(&l, "=", rp_word(w, RP_W_COUNTERS + 8 * (k + 1)));')],
    'the second counter line starts one early': [('#define RP_CNT_SPLIT 7u', '#define RP_CNT_SPLIT 6u')],
    'the counters not split at seven': [('if (k == RP_CNT_SPLIT && used == 1) {', 'if (0) {')],
    'the counters never go on to a next line': [(NEXT_LINE % 'CNT', '        break;')],
    'the counters\' MORE one too few': [('rp_put_dec(&l, " MORE=", RP_COUNTERS - k);', 'rp_put_dec(&l, " MORE=", RP_COUNTERS - k - 1);')],
    'the counters without a width guard': [('if (l.len + ' + RESERVE_CNT + ' <= RP_WIDE) {', 'if (1) {')],
    'the counters without the room MORE needs': [(RESERVE_CNT, '0')],
    'a third counter line whether or not the page has it': [('rp_room(*lines) - keep > RP_CNT_LINES + 1 ? RP_CNT_LINES_MAX : RP_CNT_LINES', 'RP_CNT_LINES_MAX')],
    'the clock after the first sample': [('rp_put_dec(&l, " AFTER=", last - call);', 'rp_put_dec(&l, " AFTER=", first - call);')],
    'the clock without the guard on the call start': [('if (last && last >= call && last <= back) rp_put_dec', 'if (last && last <= back) rp_put_dec')],
    'the clock without the guard on the call end': [('if (last && last >= call && last <= back) rp_put_dec', 'if (last && last >= call && ((void)back, 1)) rp_put_dec')],
    'the clock with the call end left out': [('last >= call && last <= back', 'last >= call && last < back')],
    'the clock without a width guard': [('if (l.len + (field + 1 < RP_CLK_FIELDS ? RP_MORE_WIDTH : 0) > RP_WIDE) {', 'if (0) {')],
    'the clock without the room MORE needs': [('(field + 1 < RP_CLK_FIELDS ? RP_MORE_WIDTH : 0) > RP_WIDE', '0 > RP_WIDE')],
    'the reads taken from the first sample': [('rp_put_dec(&l, "MON CLK READS=", rp_word(w, RP_W_CLOCK_READS));', 'rp_put_dec(&l, "MON CLK READS=", rp_word(w, RP_W_CLOCK_RAW));')],
    'the timer without a width guard': [('if (l.len + ' + RESERVE_TIM + ' <= RP_WIDE) {', 'if (1) {')],
    'the timer without the room MORE needs': [(RESERVE_TIM, '0')],
    'the timer never goes on to a second line': [(NEXT_LINE % 'TIM', '        break;')],
    'the timer\'s MORE one too few': [('rp_put_dec(&l, " MORE=", RP_TIM_FIELDS - k);', 'rp_put_dec(&l, " MORE=", RP_TIM_FIELDS - k - 1);')],
    'the timer\'s second line whether or not the counters and the acknowledge keep theirs': [(TIM_SECOND + ' ? RP_TIM_LINES_MAX : 1', 'RP_TIM_LINES_MAX')],
    'IRQS read from the writes word': [('case 0: *word = 2; return "IRQS";', 'case 0: *word = 1; return "IRQS";')],
    'WRITES read from the reads word': [('case 6: *word = 1; return "WRITES";', 'case 6: *word = 0; return "WRITES";')],
    'READS read from the writes word': [('default: *word = 0; return "READS";', 'default: *word = 1; return "READS";')],
    'the guest timer one word late': [('#define RP_W_TIMER 0x7C80u', '#define RP_W_TIMER 0x7C88u')],
    'the clock\'s raw samples one word late': [('#define RP_W_CLOCK_RAW 0x8520u', '#define RP_W_CLOCK_RAW 0x8528u')],
    'CVAL and IRQPC read from each other\'s word': [('case 5: *word = 7; return "CVAL";', 'case 5: *word = 8; return "CVAL";'), ('case 7: *word = 8; return "IRQPC";', 'case 7: *word = 7; return "IRQPC";')],
    'CTL in decimal': [('*hex = k == 4 || k == 5 || k == 7;', '*hex = k == 5 || k == 7;')],
    'READS and VECTORS exchanged': [('case 3: *word = 5; return "VECTORS";', 'case 3: *word = 0; return "VECTORS";'), ('default: *word = 0; return "READS";', 'default: *word = 5; return "READS";')],
    'the GIC lines not kept': [('#define RP_GIC_LINES 5u', '#define RP_GIC_LINES 0u')],
    'the new lines on a failed prepare': [('    if (status == 0) {\n        esr = ', '    if (1) {\n        esr = ')],
    'the registers without a capture': [('if (captured && rp_room(*lines) >= RP_REG_LINES + keep)', 'if (rp_room(*lines) >= RP_REG_LINES + keep)')],
    'the acknowledge without its magic': [(ACK_MATCH, 'ack = 1;')],
    'the acknowledge for any first word': [(ACK_MATCH, 'ack = rp_word(w, RP_W_ACK) != 0;')],
    'the acknowledge\'s layout word not checked': [(ACK_LAYOUT_CHECK, 'if (0) {')],
    'the acknowledge\'s layout word taken from the total': [(ACK_LAYOUT_CHECK, 'if (rp_word(w, RP_W_ACK + 16) != RP_ACK_LAYOUT) {')],
    'the acknowledge one word late': [('#define RP_W_ACK 0xFA10u', '#define RP_W_ACK 0xFA18u')],
    'the count from the last PC\'s word': [('uint64_t total = rp_word(w, RP_W_ACK + 16), what;', 'uint64_t total = rp_word(w, RP_W_ACK + 24), what;')],
    'the stores from the PC words': [('what = rp_word(w, RP_W_ACK + 72 + 16 * k);', 'what = rp_word(w, RP_W_ACK + 64 + 16 * k);')],
    'the store\'s offset from the low half': [('rp_put_dec(&l, " ", what >> 32);', 'rp_put_dec(&l, " ", what & 0xFFFFFFFFu);')],
    'the store\'s value from the high half': [('rp_put_hex(a, &l, "", what & 0xFFFFFFFFu);', 'rp_put_hex(a, &l, "", what >> 32);')],
    'the store\'s value with its offset': [('rp_put_hex(a, &l, "", what & 0xFFFFFFFFu);', 'rp_put_hex(a, &l, "", what);')],
    'a ninth store': [('#define RP_ACK_FIRST 8u', '#define RP_ACK_FIRST 9u')],
    'the acknowledge\'s MORE left out': [('if (total - k) rp_put_dec(&l, " MORE=", total - k);', 'if (0) rp_put_dec(&l, " MORE=", total - k);')],
    'the acknowledge\'s MORE one too few': [('if (total - k) rp_put_dec(&l, " MORE=", total - k);', 'if (total - k) rp_put_dec(&l, " MORE=", total - k - 1);')],
    'the acknowledge without a width guard': [('if (l.len + ' + RESERVE_ACK + ' > RP_WIDE) {', 'if (0) {')],
    'the acknowledge without the room MORE needs': [(RESERVE_ACK, '0')],
    'the stop\'s C= not moved to a line of its own': [(STOP_SPLIT, 'if (rp_hex_len(0) > 99) {')],
    'the stop\'s C= moved one column early': [(STOP_SPLIT, 'if (l.len + 5 + rp_hex_len(sp11sb_rd64(w + RP_W_TERMINAL + 32)) >= Q1N1_PAYLOAD_LINE_MAX) {')],
    'the stop\'s C= moved one column late': [(STOP_SPLIT, 'if (l.len + 5 + rp_hex_len(sp11sb_rd64(w + RP_W_TERMINAL + 32)) > Q1N1_PAYLOAD_LINE_MAX + 1) {')],
    'the stop\'s C= on a line without its key': [('rp_put(&l, "MON STOP");', 'rp_put(&l, "MON");')],
    'the timer without a capture': [('if (captured && rp_room(*lines) >= 1 + keep) rp_timer(', 'if (rp_room(*lines) >= 1 + keep) rp_timer(')],
    'a capture word of any value taken for a capture': [(CAPTURE_CHECK, 'if (0) {')],
    'a capture word of 4 taken for a capture': [(CAPTURE_CHECK, 'if (captured - 1 > RP_CAPTURE_MAX) {')],
    'the capture word cut to 32 bits': [(CAPTURE_CHECK, 'if ((uint32_t)captured - 1 >= RP_CAPTURE_MAX) {')],
    'the marker for a capture word of zero': [(MARKER_CHECK, 'if (status == 0) {')],
    'the marker for a window that is not status OK': [(MARKER_CHECK, 'if (captured) {')],
    'the marker without the word': [('rp_put_hex(a, &l, "MON CAPTURE WORD=", captured);', 'rp_put(&l, "MON CAPTURE WORD=?");')],
    'the marker\'s word cut to 32 bits': [('rp_put_hex(a, &l, "MON CAPTURE WORD=", captured);', 'rp_put_hex(a, &l, "MON CAPTURE WORD=", captured & 0xFFFFFFFFu);')],
    'the acknowledge only with a capture': [(ACK_MATCH, ACK_MATCH + '\n        if (!captured) ack = 0;')],
    'the syndrome on every kind of stop': [('if (kind == RP_EV_S2_FAULT && rp_room(*lines) >= 1 + keep) rp_esr(a, esr, lines);', 'if (rp_room(*lines) >= 1 + keep) rp_esr(a, esr, lines);')],
    'the value written on every kind of stop': [('if (kind == RP_EV_S2_FAULT && captured == RP_CAPTURE_S2 && rp_store_described(esr)', 'if (captured == RP_CAPTURE_S2 && rp_store_described(esr)')],
    'the value written from a capture that is not class 3': [('captured == RP_CAPTURE_S2 && rp_store_described', 'captured && rp_store_described')],
    'SRT from the wrong bits': [('rp_esr_field(&l, " SRT=", isv, (esr >> 16) & 0x1F);', 'rp_esr_field(&l, " SRT=", isv, (esr >> 17) & 0x1F);')],
    'SIZE from the wrong bits': [('rp_esr_field(&l, " SIZE=", isv, 1u << ((esr >> 22) & 3));', 'rp_esr_field(&l, " SIZE=", isv, 1u << ((esr >> 21) & 3));')],
    'SIZE as the code of the syndrome and not the bytes': [('rp_esr_field(&l, " SIZE=", isv, 1u << ((esr >> 22) & 3));', 'rp_esr_field(&l, " SIZE=", isv, (esr >> 22) & 3);')],
    'WnR from the wrong bit': [('rp_esr_field(&l, " WNR=", data, (esr >> 6) & 1);', 'rp_esr_field(&l, " WNR=", data, (esr >> 7) & 1);')],
    'S1PTW from the wrong bit': [('rp_esr_field(&l, " S1PTW=", abort, (esr >> 7) & 1);', 'rp_esr_field(&l, " S1PTW=", abort, (esr >> 6) & 1);')],
    'the fault status of seven bits': [('if (abort) rp_put_hex(a, &l, "", esr & 0x3F);', 'if (abort) rp_put_hex(a, &l, "", esr & 0x7F);')],
    'SIZE shown with ISV clear': [('rp_esr_field(&l, " SIZE=", isv,', 'rp_esr_field(&l, " SIZE=", data,')],
    'SRT shown with ISV clear': [('rp_esr_field(&l, " SRT=", isv,', 'rp_esr_field(&l, " SRT=", data,')],
    'the access fields of an instruction abort': [('data = ec == RP_EC_ABORT_DATA, abort', 'data = ec == RP_EC_ABORT_DATA || ec == RP_EC_ABORT_INSN, abort')],
    'the register after the one SRT names': [('uint64_t value = srt == 31 ? 0 : rp_word(w, RP_W_ASYNC + 8 * (1 + srt));', 'uint64_t value = srt == 31 ? 0 : rp_word(w, RP_W_ASYNC + 8 * (2 + srt));')],
    'the zero register read as a register': [('uint64_t value = srt == 31 ? 0 : rp_word(w, RP_W_ASYNC + 8 * (1 + srt));', 'uint64_t value = rp_word(w, RP_W_ASYNC + 8 * (1 + srt));')],
    'the value written not narrowed to its size': [('rp_put_hex(a, &l, "", bits < 64 ? value & ((UINT64_C(1) << bits) - 1) : value);', 'rp_put_hex(a, &l, "", ((void)bits, value));')],
    'the value written narrowed to the wrong size': [('bits = 8u << ((esr >> 22) & 3);', 'bits = 8u << ((esr >> 22) & 1);')],
    'a load shown as a store': [(ESR_DECODE, '&& !((esr >> 7) & 1);')],
    'a table walk shown as a store': [(ESR_DECODE, '&& ((esr >> 6) & 1);')],
    'ISV clear shown as a store': [('== RP_EC_ABORT_DATA && ((esr >> 24) & 1) && ((esr >> 6) & 1)', '== RP_EC_ABORT_DATA && ((esr >> 6) & 1)')],
    'any EC shown as a data abort\'s store': [('((esr >> 26) & 0x3F) == RP_EC_ABORT_DATA && ((esr >> 24) & 1) && ((esr >> 6) & 1)', '((esr >> 24) & 1) && ((esr >> 6) & 1)')],
    'the events before the console': [('shown_events = rp_min(events, rp_min(1, room));', 'shown_events = rp_min(events, room);'),
                                      ('shown_events += rp_min(events - shown_events, room - shown_events - shown_console);', 'shown_events += 0;')],
    'the console lines counted from the wrong end': [('shown_console = rp_min(console, room - shown_events);', 'shown_console = rp_min(console, 1);')],
}
# A reporter that keeps more lines for what follows the window, as the page would if its head grew: the first event and the first console line must still be there, and
# each group's room check must fail where there is no room. Every mutant is built with the room it needs to show its fault: a group's check fails only when the lines before
# it have used what the window has (g = RP_GIC_LINES: 8 is three lines more than the shipped 5; 17 to 20 leave a page without registers room for a group or two only).
def tight(g):
    return [('#define RP_GIC_LINES 5u', f'#define RP_GIC_LINES {g}u')]


TIGHT_MUTANTS = {
    'no line kept for the first event and the first console line': (8, [('keep = (events ? 1u : 0u) + (console ? 1u : 0u);', 'keep = 0;')]),
    'no line kept for the first console line': (8, [('keep = (events ? 1u : 0u) + (console ? 1u : 0u);', 'keep = (events ? 1u : 0u);')]),
    'the histogram does not leave the first event and the first console line': (8, [('rp_min(rp_room(*lines) - keep, RP_EP_LINES)', 'rp_min(rp_room(*lines), RP_EP_LINES)')]),
    'the syndrome takes the line kept for the first console line': (20, [('kind == RP_EV_S2_FAULT && rp_room(*lines) >= 1 + keep) rp_esr', 'kind == RP_EV_S2_FAULT) rp_esr')]),
    'the value written takes the line kept for the first console line': (18, [('rp_store_described(esr) && rp_room(*lines) >= 1 + keep) rp_wval', 'rp_store_described(esr)) rp_wval')]),
    'the clock takes the line kept for the first console line': (20, [('if (rp_room(*lines) >= 1 + keep) rp_clock(', 'if (1) rp_clock(')]),
    'the timer takes the line kept for the first console line': (19, [('if (captured && rp_room(*lines) >= 1 + keep) rp_timer(', 'if (captured) rp_timer(')]),
    'the counters take the lines kept for the first console line': (17, [('if (rp_room(*lines) >= RP_CNT_LINES + keep) rp_counters(', 'if (1) rp_counters(')]),
    # The edges: a group that needs one line more than it takes (a room check of `>` for `>=`), built with the room at which that check is the last one a window passes.
    'the registers need a line more than they take': (11, [('if (captured && rp_room(*lines) >= RP_REG_LINES + keep)', 'if (captured && rp_room(*lines) > RP_REG_LINES + keep)')]),
    'the syndrome needs a line more than it takes': (19, [('kind == RP_EV_S2_FAULT && rp_room(*lines) >= 1 + keep) rp_esr', 'kind == RP_EV_S2_FAULT && rp_room(*lines) > 1 + keep) rp_esr')]),
    'the value written needs a line more than it takes': (17, [('rp_store_described(esr) && rp_room(*lines) >= 1 + keep) rp_wval', 'rp_store_described(esr) && rp_room(*lines) > 1 + keep) rp_wval')]),
    'the clock needs a line more than it takes': (19, [('if (rp_room(*lines) >= 1 + keep) rp_clock(', 'if (rp_room(*lines) > 1 + keep) rp_clock(')]),
    'the timer needs a line more than it takes': (17, [('if (captured && rp_room(*lines) >= 1 + keep) rp_timer(', 'if (captured && rp_room(*lines) > 1 + keep) rp_timer(')]),
    'the timer\'s second line needs a line more than it takes': (12, [(TIM_SECOND, 'rp_room(*lines) - keep > RP_TIM_LINES_MAX + RP_CNT_LINES + ack')]),
    'the timer\'s second line keeps no line for the acknowledge': (6, [(TIM_SECOND, 'rp_room(*lines) - keep >= RP_TIM_LINES_MAX + RP_CNT_LINES')]),
    'the acknowledge takes the line kept for the first console line': (18, [(ACK_CALL, 'if (ack) rp_ack(')]),
    'the acknowledge needs a line more than it takes': (18, [(ACK_CALL, 'if (ack && rp_room(*lines) > 1 + keep) rp_ack(')]),
    'the counters need a line more than they take': (17, [('if (rp_room(*lines) >= RP_CNT_LINES + keep) rp_counters(', 'if (rp_room(*lines) > RP_CNT_LINES + keep) rp_counters(')]),
    'the counters\' third line leaves the histogram nothing': (6, [('rp_room(*lines) - keep > RP_CNT_LINES + 1 ?', 'rp_room(*lines) - keep >= RP_CNT_LINES + 1 ?')]),
    'the histogram needs a line more than it takes': (16, [('if (rp_room(*lines) > keep) rp_endpoints(', 'if (rp_room(*lines) > keep + 1) rp_endpoints(')]),
}


class Mutants(page.Base):
    def mutant_exe(self, name, patches):
        """The harness with the reporter of this tree changed by `patches` ((old, new) text replacements, each `old` exactly once), compiled in a copy below build/."""
        base = M.harness(FIX['tree'])
        out = T.BUILD / 'monitor-harness' / ('page-mutant-' + T.sha(name.encode())[:8])
        out.mkdir(parents=True, exist_ok=True)
        text = REPORTER_C.read_text()
        for old, new in patches:
            if text.count(old) != 1:
                raise AssertionError(f'the mutant {name!r} does not match the reporter exactly once: {old!r}')
            text = text.replace(old, new)
        source = out / 'sp11-stick-reporter.c'
        source.write_text(text)
        T.clang([*M.flags(FIX['tree']), f'-I{page.ROOT / "probes"}', '-Dpayload_entry=snaprep_entry', '-c', str(source), '-o', str(out / 'reporter.o')], f'compiling the mutant {name}')
        shared = [s for s in M.shared(FIX['tree']) if not s.endswith('reporter.o')]
        exe = out / 'harness'
        T.clang([*T.SANITIZE, *sorted(str(p) for p in base.parent.glob('*.o')), *shared, str(out / 'reporter.o'), '-o', str(exe)], f'linking the mutant {name}')
        return exe

    @classmethod
    def windows(cls):
        """(specs, REPORT.DAT files, the fixtures' own wrapper records): the page test's fixtures, a failed prepare, which has registers, a clock and counters in its window
        that the page must not show, and the syndromes of ESR_CASES."""
        specs, reports, recs = page.fixture_reports()
        specs['prepare'] = P.Spec(status=-90, entered=0, words=(0, 0, 0, 1, 0, 0, 0, 0), events=((5, 0x4E56, 1, 2, 0, 0),), capture=1, clock=(5, 9, 99), counters=(1,) * 13,
                                  endpoints=(3,) * 64, timer=(7,) * 9)
        reports['prepare'] = page.report_with(P.build(specs['prepare']))
        for key, (text, esr, spec) in page.esr_specs().items():
            specs[key], reports[key] = spec, page.report_with(P.build(spec))
        return specs, reports, recs

    def test_the_real_reporter_passes_every_check_the_mutants_are_held_to(self):
        specs, reports, recs = self.windows()
        results = page.run_reporters(FIX['exe'], reports)
        for name, spec in specs.items():
            self.assertEqual(page.violations(results[name], spec, recs.get(name)), [], name)

    def test_every_mutant_is_rejected_by_a_window_that_exposes_it(self):
        specs, reports, recs = self.windows()
        survivors = []
        for name, patches in MUTANTS.items():
            results = page.run_reporters(self.mutant_exe(name, patches), reports)
            if not [window for window, spec in specs.items() if page.violations(results[window], spec, recs.get(window))]:
                survivors.append(name)
        self.assertEqual(survivors, [], 'a mutant of the reporter that no window rejects')

    def test_a_page_with_less_room_keeps_the_first_event_and_console_line_and_its_mutants_do_not(self):
        specs, reports, recs = self.windows()
        for g in sorted({8, *(g for g, patches in TIGHT_MUTANTS.values())}):
            results = page.run_reporters(self.mutant_exe(f'tight {g}', tight(g)), reports)
            for name, spec in specs.items():
                r = results[name]
                texts = r.texts()
                start, got = page.window_part(texts)
                with self.subTest(room=g, window=name):
                    self.assertEqual(got, page.render(spec, start, recs.get(name), gic_lines=g))
                    self.assertEqual(page.held(r), [])
                    if g < 17:
                        self.assertEqual(bool([t for t in texts if t.startswith('SNAPREP MON EV 0 ')]), bool(spec.words[3]))
                        self.assertEqual(bool([t for t in texts if t.startswith('SNAPREP MON CON ')]), bool(spec.words[7] >> 32))
        survivors = []
        for name, (g, patches) in TIGHT_MUTANTS.items():
            results = page.run_reporters(self.mutant_exe(f'tight {g} {name}', [*tight(g), *patches]), reports)
            wrong = []
            for window, spec in specs.items():
                start, got = page.window_part(results[window].texts())
                if got != page.render(spec, start, recs.get(window), gic_lines=g):
                    wrong.append(window)
            if not wrong:
                survivors.append(name)
        self.assertEqual(survivors, [])


if __name__ == '__main__':
    unittest.main()
