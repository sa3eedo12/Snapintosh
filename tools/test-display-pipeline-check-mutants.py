#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Mutation sanity check for compat/display-pipeline-check.c.

A separate file only because tools/test-display-pipeline-check.py is near the 50 KiB size limit. It seeds one plausible
bug at a time into a copy of the C source under build/, rebuilds the sanitizer harness from that copy, and requires the
scenario table of tools/test-display-pipeline-check.py to fail on it (by a wrong verdict, or by a sanitizer crash on a
memory-safety bug). The mutant is built without -Werror so that a new compiler warning cannot kill it: only behaviour
counts. The result table goes to build/test-display-pipeline-check-mutants/mutants.tsv.

    python3 tools/test-display-pipeline-check-mutants.py
"""
import importlib.util
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('display_pipeline_check_tests', HERE / 'test-display-pipeline-check.py')
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)
# Its own build directory: the host suite runs this file and test-display-pipeline-check.py at the same time, and two
# processes must never rewrite the same copy of the sources while the other is compiling it.
t.BUILD = t.ROOT / 'build' / 'test-display-pipeline-check-mutants'

# (name, old text, new text), all in compat/display-pipeline-check.c. Each is a plausible bug in the decision logic.
MUTANTS = (
    ('all-ones treated as a value', 'return r.value == ALL_ONES ? DPC_WHY_ALL_ONES : DPC_WHY_OK;', 'return DPC_WHY_OK;'),
    ('never-read register treated as read', 'if (r.status == DPC_ST_NOT_TRIED)\n        return DPC_WHY_NOT_READ;', ''),
    ('faulted register treated as read', 'if (r.status == DPC_ST_FAULT)\n        return DPC_WHY_READ_FAULT;', ''),
    ('enable/status contradiction ignored', 'if (en != st)', 'if (0)'),
    ('geometry never compared', 'if (bad_dims)', 'if (0)'),
    ('widebus flag ignored', 'want = (cfg2 & DPC_INTF_CFG2_WIDEN) ?', 'want = 0 ?'),
    ('widebus width not halved', 'p->gop_width / 2u) : p->gop_width;', 'p->gop_width) : p->gop_width;'),
    ('truncation off by one entry', 'if (len < need)', 'if (len + DPC_SNAP_ENTRY < need)'),
    ('truncation never checked', 'if (len < need)', 'if (0)'),
    ('hard-coded INTF base', 'return block(p, block_of(reg))->base + p->reg_off[reg];',
     'return (block_of(reg) == DPC_BLK_INTF ? 0x0ae3a000ull : block(p, block_of(reg))->base) + p->reg_off[reg];'),
    ('hard-coded register offset', 'return block(p, block_of(reg))->base + p->reg_off[reg];',
     'return block(p, block_of(reg))->base + (reg == DPC_REG_INTF_STATUS ? 0x26cu : p->reg_off[reg]);'),
    ('interface index not compared', 'if (v->intf_index != p->intf_index)', 'if (0)'),
    ('register addresses not compared',
     'x.pa != expected_pa(p, r))\n            return finish(out, DPC_INCONSISTENT, DPC_WHY_ADDRESS_MISMATCH, r, 0);',
     '0)\n            return finish(out, DPC_INCONSISTENT, DPC_WHY_ADDRESS_MISMATCH, r, 0);'),
    ('ctl addresses not compared', 'x.pa != p->ctl_base[r] + p->ctl_intf_active_off', '0'),
    ('ctl count not compared', 'if (v->ctl_count != p->ctl_count)', 'if (0)'),
    ('routing contradiction ignored', 'if (v->ctl_count && readable == v->ctl_count)', 'if (0)'),
    ('routing contradiction on a partial read', 'if (v->ctl_count && readable == v->ctl_count)',
     'if (v->ctl_count && readable)'),
    ('power-off ignored', 'int off = order[i] == DPC_REG_GDSC ? !(r.value & DPC_GDSC_PWR_ON) :',
     'int off = order[i] == DPC_REG_GDSC ? 0 :'),
    ('clock-off ignored', '!!(r.value & DPC_CBCR_CLK_OFF);', '0;'),
    ('required evidence ignored', 'if (p->required & ~ev) {', 'if (0) {'),
    ('no-timing check weakened', 'if (!ht || !period ||', 'if (!ht ||'),
    ('period remainder ignored', 'if (period % ht)', 'if (0)'),
    ('window end before start ignored', 'if (ve <= vs)', 'if (0)'),
    ('equal window start and end accepted', 'if (ve <= vs)', 'if (ve < vs)'),
    ('frame without blanking accepted', 'if (vt <= lines || first + lines > vt)', 'if (first + lines > vt)'),
    ('window past the line accepted', 'if (h1 >= ht)', 'if (0)'),
    ('version mismatch ignored', '(r.value >> 28) != p->dpu_major ||', '0 &&'),
    ('stalled counter accepted',
     'frames_min_x1e6 >= 2000000u ? finish(out, DPC_INCONSISTENT, DPC_WHY_COUNTER_STALLED, 0, *ev) : 0', '0'),
    ('runaway counter accepted', 'if (delta > frames_max)', 'if (0)'),
    ('core evidence waivable', '(p->required & DPC_EV_CORE) != DPC_EV_CORE', '0'),
    ('offset bounds dropped', 'return !(off & 3u) && (uint64_t)off + 4u <= length;', 'return !(off & 3u);'),
    ('active-V select ignored', 'if (cfg & DPC_INTF_CFG_ACTIVE_V_EN) {', 'if (0) {'),
    ('active-V window always used', 'if (cfg & DPC_INTF_CFG_ACTIVE_V_EN) {', 'if (1) {'),
    ('active-H select ignored', 'hctl = (cfg & DPC_INTF_CFG_ACTIVE_H_EN) ?', 'hctl = 0 ?'),
    ('active-H window always used', 'hctl = (cfg & DPC_INTF_CFG_ACTIVE_H_EN) ?', 'hctl = 1 ?'),
    ('status value not validated', 'if (entry(v->regs, i).status > DPC_ST_FAULT)', 'if (0)'),
    ('magic not checked', 'if (ld32(s) != DPC_SNAP_MAGIC ||', 'if ('),
    ('intf page may leave mdp', 'block_bad(&p->intf) || !inside(&p->mdp, p->intf.base, p->intf.length))',
     'block_bad(&p->intf) || 0)'),
    ('dispcc register clock off reported as INACTIVE',
     'order[i] == DPC_REG_GCC_DISP_AHB ? DPC_UNKNOWN : DPC_INACTIVE', 'DPC_INACTIVE'),
    ('last gate never consulted', '#define GATES 4u', '#define GATES 3u'),
    ('gcc block not validated', 'block_bad(&p->gcc) || ', ''),
)


class Mutants(unittest.TestCase):
    def test_every_seeded_bug_is_caught(self):
        exe, L = t.native()
        self.assertEqual(t.evaluate(exe, L)[0], [], 'the unmutated build must pass first')
        source = t.SOURCE.read_text()
        rows = []
        for i, (name, old, new) in enumerate(MUTANTS):
            self.assertEqual(source.count(old), 1, f'mutant {name!r}: the text to mutate must occur exactly once')
            try:
                wrong, _ = t.evaluate(t.build(f'mutant-{i:02d}', source_text=source.replace(old, new), werror=False), L)
                killer = wrong[0][0] if wrong else 'SURVIVED'
            except t.HarnessFailure as error:
                killer = 'harness crash: ' + str(error).splitlines()[0][:70]
            except AssertionError as error:
                killer = 'DOES NOT BUILD: ' + str(error).splitlines()[0][:70]
            rows.append((i, name, killer))
        t.BUILD.mkdir(parents=True, exist_ok=True)
        (t.BUILD / 'mutants.tsv').write_text('id\tmutant\tfirst failing scenario\n' + ''.join('%d\t%s\t%s\n' % r for r in rows))
        self.assertEqual([r for r in rows if r[2] == 'SURVIVED' or r[2].startswith('DOES NOT')], [])
        self.assertGreaterEqual(len(rows), 30)

    def test_the_mutation_file_is_hygienic(self):
        text = Path(__file__).read_text()
        self.assertIn('SPDX-License-Identifier: MIT', '\n'.join(text.splitlines()[:2]))
        for prefix in t.HOME_PREFIXES:
            self.assertNotIn(prefix, text)


if __name__ == '__main__':
    unittest.main(verbosity=1)
