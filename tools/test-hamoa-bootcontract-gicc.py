#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tests for the redistributor ranges probes/bootcontract.py takes from the per-CPU GICC bases.

The one unit's firmware writes its MADT without a GICR structure and gives every CPU interface its own redistributor base
in the GICC structure (OBSERVED in every local dump); ACPI allows that form. The adapter used to refuse such a table, so
no flow through from_hamoa could accept this unit's real tables. This file pins what is accepted (a base on every enabled
GICC that together make exactly the descriptor's contiguous range) and every way it is still refused. It builds its tables
with the synthetic fixtures of tools/test-hamoa-bootcontract.py; nothing touches hardware.

    python3 tools/test-hamoa-bootcontract-gicc.py

Evidence: EXPERIMENTAL. Agreement with synthetic tables says nothing about what any firmware really emits.
"""
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'probes'))
sys.path.insert(0, str(ROOT / 'tools'))
import bootcontract as bc


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fx = _load('hamoa_fixtures', 'test-hamoa-bootcontract.py')


def per_cpu_only(bases=None):
    """The baseline MADT without its GICR structure; each GICC carries a base (the contiguous frames by default)."""
    entries = fx.without(fx.baseline_entries(per_cpu_gicr=True), fx.I_GICR)
    for uid, base in enumerate(bases or ()):
        entries[fx.I_CPU0 + uid] = fx.gicc(uid, fx.MPIDRS[uid], gicr=base)
    return entries


def frames():
    return [fx.GICR_BASE + uid * fx.STRIDE for uid in range(len(fx.MPIDRS))]


class RedistributorsFromGicc(fx.Base):
    def test_per_cpu_bases_that_make_the_descriptors_range_are_accepted(self):
        result = self.accepted(fx.good(per_cpu_only()))
        self.assertEqual((result.gicr_base, result.gicr_stride, result.gicr_size), (fx.GICR_BASE, fx.STRIDE, fx.GICR_SIZE))
        self.assertTrue(any('per-CPU GICC bases' in note for note in result.notes), result.notes)

    def test_the_order_of_the_gicc_entries_does_not_matter(self):
        bases = frames()
        bases[0], bases[-1] = bases[-1], bases[0]
        self.accepted(fx.good(per_cpu_only(bases)))

    def test_a_gicr_structure_still_wins_and_adds_no_note(self):
        result = self.accepted(fx.good(fx.baseline_entries(per_cpu_gicr=True)))
        self.assertFalse(any('per-CPU GICC bases' in note for note in result.notes), result.notes)

    def test_one_cpu_without_a_base_is_refused(self):
        bases = frames()
        bases[5] = 0
        exc = self.refused('gicr-mismatch', fx.good(per_cpu_only(bases)))
        self.assertIn('UID 5 carries no redistributor base', exc.reason)

    def test_no_bases_at_all_is_still_the_old_refusal(self):
        self.refused('gicr-mismatch', fx.good(fx.without(fx.baseline_entries(), fx.I_GICR)), reason='carries no redistributor base')

    def test_two_cpus_on_one_frame_are_refused(self):
        bases = frames()
        bases[7] = bases[3]
        self.refused('gic-overlap', fx.good(per_cpu_only(bases)))

    def test_a_base_inside_another_frame_is_refused(self):
        bases = frames()
        bases[4] += 0x10000
        self.refused('gic-overlap', fx.good(per_cpu_only(bases)))

    def test_a_gap_between_frames_is_refused(self):
        bases = frames()
        bases[-1] += fx.STRIDE
        self.refused('gicr-mismatch', fx.good(per_cpu_only(bases)), reason='gap')

    def test_frames_shifted_from_the_descriptor_are_refused(self):
        shifted = [base + fx.STRIDE for base in frames()]
        self.refused('gicr-mismatch', fx.good(per_cpu_only(shifted)), reason='differs from descriptor')

    def test_the_stride_comes_from_the_gic_version(self):
        self.assertEqual(bc.GICR_FRAME_STRIDE, {3: 0x20000, 4: 0x40000})


if __name__ == '__main__':
    unittest.main()
