#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Ties of the page that SNAPREP-M1.BIN prints (docs/SP11-STICK-REPORT-LINES.md) to the monitor's sources and to the host's tools, with no compiler and no q1n1 checkout: which
counter is stored where and under which name, that the capture word is 0 or a class the monitor stores (1, 2 or 3) and a stage-2 fault is class 3, which words of the guest
timer the page reads and in which order, that the endpoint names of the document are probes/run.py's, and that the new files keep to the repository's limits and the reporter
stays a reader of the window and a printer. The page itself is tested by tools/test-sp11-stick-monitor-page.py and the mutants of the reporter by
tools/test-sp11-stick-monitor-page-mutants.py.

    python3 tools/test-sp11-stick-monitor-page-sources.py

No hardware, nothing under /Volumes, no network. EXPERIMENTAL; hardware NOT RUN."""
import ast
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sp11_stick_monitor_pagelib as P  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REPORTER_C = ROOT / 'probes/sp11-stick-reporter.c'
NEW_FILES = ('tools/sp11_stick_monitor_pagelib.py', 'tools/sp11_stick_monitor_pagefix.py', 'tools/test-sp11-stick-monitor-page.py', 'tools/test-sp11-stick-monitor-page-ack.py',
             'tools/test-sp11-stick-monitor-page-mutants.py', 'tools/test-sp11-stick-monitor-page-sources.py', 'probes/sp11-stick-reporter.c', 'docs/SP11-STICK-REPORT-LINES.md')


class Sources(unittest.TestCase):
    """The page's names and offsets against the monitor's sources, without the compiler."""

    def test_the_counters_are_stored_in_the_order_the_page_names_them(self):
        text = (ROOT / 'probes/xnuboot/report.c').read_text()
        stored = dict(re.findall(r'counters\[(\d+)\] = (?:vcpu_of\(c\)->|c->)(\w+);', text))
        self.assertEqual([stored.get(str(k)) for k in range(13)], list(P.COUNTER_FIELDS))
        reporter = REPORTER_C.read_text().split('rp_counter_name')[1].split('rp_registers')[0]
        self.assertEqual([re.search(rf'case {k}: return "(\w+)";\s*/\* {field} \*/', reporter).group(1) for k, field in enumerate(P.COUNTER_FIELDS[:12])], list(P.COUNTER_NAMES[:12]))
        self.assertEqual(re.search(rf'default: return "(\w+)";\s*/\* {P.COUNTER_FIELDS[12]} \*/', reporter).group(1), P.COUNTER_NAMES[12])

    def test_the_capture_classes_are_the_ones_the_monitor_stores_and_a_stage_2_fault_is_class_3(self):
        stored = set()
        for path in sorted((ROOT / 'probes/xnuboot').glob('*.c')):
            for value in re.findall(r'async_captured\s*=\s*(\S+?)\s*;', path.read_text()):
                self.assertRegex(value, r'^\d+$', f'{path.name} stores something that is not a literal class')
                stored.add(int(value))
        reporter = REPORTER_C.read_text()
        self.assertEqual(stored, set(range(1, P.CAPTURE_MAX + 1)), 'the classes of every `async_captured =` of the monitor')
        self.assertIn(f'#define RP_CAPTURE_MAX {P.CAPTURE_MAX}u', reporter)
        self.assertIn(f'#define RP_CAPTURE_S2 {P.CAPTURE_S2}u', reporter)
        text = (ROOT / 'probes/xnuboot/trap_dispatch.c').read_text()
        self.assertRegex(text, r'record\(c, EV_S2_FAULT, \*elr, ipa, esr, far\);(?:.|\n){0,400}?fill_regs\(&vcpu_of\(c\)->async_regs, frame, esr, \*elr, far\);\s*'
                               r'vcpu_of\(c\)->async_captured = 3;', 'a stage-2 fault stop: a = IPA, b = ESR_EL2, c = FAR, and the capture of class 3 with that ESR')
        self.assertIn(f'#define RP_EV_S2_FAULT {P.EV_S2_FAULT}u', reporter)
        self.assertRegex((ROOT / 'probes/xnuboot/report_layout.c').read_text(), rf'#define EV_S2_FAULT\s+{P.EV_S2_FAULT}\b')

    def test_the_guest_timer_words_are_stored_where_and_in_the_order_the_page_reads_them(self):
        text = (ROOT / 'probes/xnuboot/report.c').read_text()
        stored = dict(re.findall(r'out\[OUT_TIMER_FLOW \+ (\d)\] = (?:vcpu_of\(c\)->)?([\w.]+);', text))
        self.assertEqual([stored.get(str(k)) for k in range(9)], list(P.TIMER_STORED), 'OUT_TIMER_FLOW words 0 to 8: reads, writes, irqs, asserts, clears, vectors, ctl, cval, irq_pc')
        field = {'IRQS': 'timer_irqs', 'ASSERTS': 'timer_asserts', 'CLEARS': 'timer_clears', 'VECTORS': 'timer_vectors', 'CTL': 'timer_ctl', 'CVAL': 'timer.cval',
                 'WRITES': 'timer.writes', 'IRQPC': 'timer_irq_pc', 'READS': 'timer.reads'}
        self.assertEqual([P.TIMER_STORED[place] for name, place, hexadecimal in P.TIMER_FIELDS], [field[name] for name, place, hexadecimal in P.TIMER_FIELDS])
        body = REPORTER_C.read_text().split('static const char *rp_timer_field')[1].split('SC_INLINE void rp_timer')[0]
        self.assertEqual(re.findall(r'(?:case \d+|default): \*word = (\d+); return "(\w+)";', body), [(str(place), name) for name, place, hexadecimal in P.TIMER_FIELDS])
        self.assertEqual(re.search(r'\*hex = ([^;]+);', body).group(1), ' || '.join(f'k == {k}' for k, (name, place, hexadecimal) in enumerate(P.TIMER_FIELDS) if hexadecimal))
        self.assertIn(f'#define RP_W_TIMER 0x{P.TIMER:X}u', REPORTER_C.read_text())

    def test_the_monitor_stores_the_words_the_page_reads(self):
        text = (ROOT / 'probes/xnuboot/report.c').read_text()
        for statement in ('out[OUT_CLOCK_FLOW + 0] = vcpu_of(c)->clock.reads;', 'out[OUT_CLOCK_ADAPTER + 8] = vcpu_of(c)->clock_raw_first;',
                          'out[OUT_CLOCK_ADAPTER + 9] = vcpu_of(c)->clock_raw_last;', 'out[OUT_ENDPOINTS + n] = c->endpoint_calls[n];',
                          'out[OUT_ENDPOINTS + ENDPOINT_BUCKETS] = c->endpoint_other;', 'out[OUT_ASYNC_REGS] = vcpu_of(c)->async_captured;',
                          'out[OUT_ASYNC_REGS + 1 + n] = async[n];'):
            self.assertIn(statement, text)
        sptm = (ROOT / 'probes/xnuboot/sptm_dispatch.c').read_text()
        self.assertIn('c->endpoint_calls[endpoint & (ENDPOINT_BUCKETS - 1)]++;', sptm, 'bucket n counts endpoint n')

    def test_the_acknowledge_block_is_the_monitors_and_the_page_reads_its_words_where_it_stores_them(self):
        header = (ROOT / 'compat/cpu-start-ack.h').read_text()
        magic = int(re.search(r'#define CPU_START_ACK_MAGIC UINT64_C\((0x[0-9a-f]+)\)', header).group(1), 16)
        first, version = (int(re.search(rf'#define CPU_START_ACK_{name} (\d+)u', header).group(1)) for name in ('FIRST', 'VERSION'))
        body = re.search(r'struct cpu_start_ack \{(.*?)\n\};', header, re.S).group(1)
        self.assertEqual([m.group(1) for m in re.finditer(r'(?:uint64_t |struct \{ uint64_t pc, what; \} )([a-z_, \[\]0-9A-Z]+?);', body)], ['total', 'at[3]', 'last_pc, last_what', 'first[CPU_START_ACK_FIRST]'],
                         'the order of the words after the two header words: total, at +4 +8 +12, the last store, the first stores as PC and what')
        size = 8 * (1 + 3 + 2 + 2 * first)
        self.assertIn(f'_Static_assert(sizeof(struct cpu_start_ack) == 8 * {size // 8}', header)
        self.assertEqual((P.ACK_MAGIC, P.ACK_FIRST, P.ACK_LAYOUT, P.ACK_WORDS), (magic, first, size << 32 | version, 2 + size // 8))
        text = (ROOT / 'probes/xnuboot/report.c').read_text()
        for statement in ('out[OUT_CPU_START_ACK] = CPU_START_ACK_MAGIC;', 'out[OUT_CPU_START_ACK + 1] = (uint64_t)sizeof(struct cpu_start_ack) << 32 | CPU_START_ACK_VERSION;',
                          'out[OUT_CPU_START_ACK + 2 + n] = ((const uint64_t *)cpu_start_ack_of(c))[n];'):
            self.assertIn(statement, text)
        reporter = REPORTER_C.read_text()
        for define in (f'#define RP_W_ACK 0x{P.ACK:X}u', f'#define RP_ACK_MAGIC UINT64_C(0x{magic:X})', f'#define RP_ACK_LAYOUT ((UINT64_C({size}) << 32) | {version}u)', f'#define RP_ACK_FIRST {first}u',
                       f'#define RP_ACK_WORDS {2 + size // 8}u'):
            self.assertIn(define, reporter)
        self.assertIn('uint64_t total = rp_word(w, RP_W_ACK + 16)', reporter, 'the total is the first word after the two of the header')
        self.assertIn(f'rp_word(w, RP_W_ACK + {8 * (2 + 6 + 1)} + 16 * k)', reporter, 'the what of the first store: after the header, the total, the three counts and the last store')

    def test_the_endpoint_names_of_the_document_are_the_hosts(self):
        doc = (ROOT / 'docs/SP11-STICK-REPORT-LINES.md').read_text()
        module = ast.parse((ROOT / 'probes/run.py').read_text())
        names = next(ast.literal_eval(n.value) for n in module.body if isinstance(n, ast.Assign) and any(getattr(t, 'id', None) == 'SPTM_ENDPOINTS' for t in n.targets))
        rows = {int(m.group(1)): m.group(3) for m in re.finditer(r'^\| (\d+) \| 0x([0-9A-F]+) \| ([A-Z0-9_]+) \|', doc, re.M)}
        self.assertEqual(rows, {number: name for number, name in names.items()}, 'the table of docs/SP11-STICK-REPORT-LINES.md is probes/run.py SPTM_ENDPOINTS')
        for number in names:
            self.assertEqual(int(re.search(rf'^\| {number} \| 0x([0-9A-F]+) \|', doc, re.M).group(1), 16), number)

    def test_the_new_files_are_ascii_with_an_spdx_line_within_the_size_limits(self):
        for rel in NEW_FILES:
            data = (ROOT / rel).read_bytes()
            with self.subTest(rel):
                self.assertFalse([b for b in data if b > 127], 'ASCII only')
                if not rel.endswith('.md'):
                    self.assertIn(b'SPDX-License-Identifier: MIT', data[:400])
                self.assertLess(len(data), 50 * 1024)
                self.assertLess(data.count(b'\n'), 1500)

    def test_the_reporter_keeps_the_page_a_reader_of_the_window_and_nothing_else(self):
        text = REPORTER_C.read_text()
        self.assertEqual([m for m in re.findall(r'\b(?:asm|__asm__)\b', text)], [], 'no inline assembly in the reporter')
        self.assertNotIn('sp11hw_store64', text)
        self.assertNotIn('sp11hw_dc_', text)


if __name__ == '__main__':
    unittest.main()
