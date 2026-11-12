#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Host tests for tools/sp11_ebs_sweep_model.py (docs/SP11-Q1N1-BOOT-STUDY.md section 4).

One command, deterministic, writes nothing: `python3 tools/test-sp11-ebs-sweep-model.py`.
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import sp11_ebs_sweep_model as m

MODULE_PATH = pathlib.Path(__file__).resolve().parent / "sp11_ebs_sweep_model.py"


class ClassicSweep(unittest.TestCase):
    """classic_sweep() must reproduce bounce_main.c:117-134 exactly: the six
    EfiLoaderCode/.../EfiRuntimeServicesData types plus EfiACPIReclaimMemory, full
    descriptor extent, Attribute never consulted."""

    def test_every_swept_type_is_included_whole(self):
        for t in sorted(m.SWEPT_TYPES):
            d = m.Descriptor(t, base=0x1000, pages=3)
            self.assertEqual(m.classic_sweep([d]), [(0x1000, 3 * m.PAGE_BYTES)])

    def test_non_swept_types_are_excluded(self):
        excluded = {m.EFI_RESERVED_MEMORY_TYPE, m.EFI_CONVENTIONAL_MEMORY, m.EFI_UNUSABLE_MEMORY,
                    m.EFI_ACPI_MEMORY_NVS, m.EFI_MEMORY_MAPPED_IO, m.EFI_MEMORY_MAPPED_IO_PORT_SPACE,
                    m.EFI_PAL_CODE}
        self.assertFalse(excluded & m.SWEPT_TYPES)
        for t in sorted(excluded):
            d = m.Descriptor(t, base=0x2000, pages=1)
            self.assertEqual(m.classic_sweep([d]), [])

    def test_seven_swept_types_exactly(self):
        # bounce_main.c:124-130 lists exactly seven case labels.
        self.assertEqual(len(m.SWEPT_TYPES), 7)

    def test_attribute_is_never_consulted(self):
        wb = m.Descriptor(m.EFI_BOOT_SERVICES_DATA, base=0x3000, pages=1, attribute=m.EFI_MEMORY_WB)
        non_wb = m.Descriptor(m.EFI_BOOT_SERVICES_DATA, base=0x3000, pages=1, attribute=0)
        self.assertEqual(m.classic_sweep([wb]), m.classic_sweep([non_wb]))

    def test_reproduces_the_issue_13_shaped_range_blindly(self):
        # TravMurav/slbounce#13's "unrelated note": an ordinary EfiBootServicesData
        # range, 231 pages at 0xa7f19000, is fatal to touch on that unit's firmware.
        # classic_sweep, faithfully reproducing upstream, includes it with no way
        # to exclude it short of knowing its address in advance.
        fatal_shaped = m.Descriptor(m.EFI_BOOT_SERVICES_DATA, base=0xA7F19000, pages=231)
        ops = m.classic_sweep([fatal_shaped])
        self.assertIn((0xA7F19000, 231 * m.PAGE_BYTES), ops)


class ScopedSweep(unittest.TestCase):
    def test_coverage_of_a_single_declared_range(self):
        d = m.Descriptor(m.EFI_LOADER_DATA, base=0x90000000, pages=(64 << 20) // m.PAGE_BYTES)
        ops = m.scoped_sweep([d], [(0x90000000, 64 << 20)], chunk_cap=1 << 30)
        self.assertEqual(m.total_bytes(ops), 64 << 20)
        self.assertEqual(m.merged_intervals(ops), [(0x90000000, 0x90000000 + (64 << 20))])

    def test_a_sub_range_of_a_larger_descriptor_is_covered(self):
        d = m.Descriptor(m.EFI_LOADER_DATA, base=0x90000000, pages=(64 << 20) // m.PAGE_BYTES)
        ops = m.scoped_sweep([d], [(0x90001000, 0x1000)], chunk_cap=1 << 30)
        self.assertEqual(ops, [(0x90001000, 0x1000)])

    def test_boundedness_never_exceeds_declared_total(self):
        d = m.Descriptor(m.EFI_BOOT_SERVICES_DATA, base=0x1000, pages=4096)  # 16 MiB descriptor
        declared = [(0x1000, 4096), (0x100000, 8192)]
        ops = m.scoped_sweep([d], declared, chunk_cap=1 << 30)
        self.assertEqual(m.total_bytes(ops), 4096 + 8192)
        self.assertLess(m.total_bytes(ops), d.size)  # strictly less than the classic whole-descriptor sweep

    def test_firmware_owned_memory_never_declared_is_never_touched(self):
        # Exactly the point of the design: a descriptor we never declared a range
        # inside of (because q1n1 never wrote to it) does not appear at all,
        # unlike classic_sweep which would walk into it regardless.
        ours = m.Descriptor(m.EFI_LOADER_DATA, base=0x90000000, pages=16)
        fatal_shaped = m.Descriptor(m.EFI_BOOT_SERVICES_DATA, base=0xA7F19000, pages=231)
        ops = m.scoped_sweep([ours, fatal_shaped], [(0x90000000, 16 * m.PAGE_BYTES)])
        self.assertFalse(any(b == 0xA7F19000 for b, _ in ops))

    def test_attribute_filter_refuses_non_write_back(self):
        d = m.Descriptor(m.EFI_BOOT_SERVICES_DATA, base=0x5000, pages=1, attribute=0)
        with self.assertRaises(m.SweepRefused):
            m.scoped_sweep([d], [(0x5000, 0x1000)])

    def test_non_swept_type_is_refused_even_if_declared(self):
        d = m.Descriptor(m.EFI_CONVENTIONAL_MEMORY, base=0x6000, pages=1)
        with self.assertRaises(m.SweepRefused):
            m.scoped_sweep([d], [(0x6000, 0x1000)])

    def test_a_range_split_across_a_descriptor_boundary_is_refused(self):
        a = m.Descriptor(m.EFI_LOADER_DATA, base=0x1000, pages=1)
        b = m.Descriptor(m.EFI_LOADER_DATA, base=0x2000, pages=1)
        with self.assertRaises(m.SweepRefused):
            m.scoped_sweep([a, b], [(0x1800, 0x1000)])  # straddles the 0x2000 boundary

    def test_an_undeclared_range_outside_any_descriptor_is_refused(self):
        with self.assertRaises(m.SweepRefused):
            m.scoped_sweep([], [(0x7000, 0x1000)])

    def test_malformed_declared_range_is_refused(self):
        d = m.Descriptor(m.EFI_LOADER_DATA, base=0x1000, pages=1)
        for base, size in [(-1, 0x1000), (0x1000, 0), (0x1000, -1)]:
            with self.assertRaises(m.SweepRefused):
                m.scoped_sweep([d], [(base, size)])

    def test_chunk_cap_bounds_every_op(self):
        pages = (8 << 20) // m.PAGE_BYTES
        d = m.Descriptor(m.EFI_LOADER_DATA, base=0x90000000, pages=pages)
        ops = m.scoped_sweep([d], [(0x90000000, 8 << 20)], chunk_cap=1 << 20)
        self.assertTrue(len(ops) > 1)
        self.assertTrue(all(size <= (1 << 20) for _, size in ops))
        self.assertEqual(m.total_bytes(ops), 8 << 20)
        self.assertEqual(m.merged_intervals(ops), [(0x90000000, 0x90000000 + (8 << 20))])

    def test_chunk_cap_must_be_positive(self):
        d = m.Descriptor(m.EFI_LOADER_DATA, base=0x1000, pages=1)
        with self.assertRaises(m.SweepRefused):
            m.scoped_sweep([d], [(0x1000, 0x1000)], chunk_cap=0)

    def test_empty_declared_ranges_is_the_empty_sweep(self):
        d = m.Descriptor(m.EFI_LOADER_DATA, base=0x1000, pages=1)
        self.assertEqual(m.scoped_sweep([d], []), [])


class Equivalence(unittest.TestCase):
    """The quantitative claim of section 4: scoped coverage is always a subset of
    (never larger than) classic's write-back-attributed coverage, with equality
    only when every WB swept-type byte is declared."""

    def _mixed_map(self):
        return [
            m.Descriptor(m.EFI_LOADER_CODE, base=0x80000000, pages=4),
            m.Descriptor(m.EFI_LOADER_DATA, base=0x90000000, pages=(64 << 20) // m.PAGE_BYTES),
            m.Descriptor(m.EFI_BOOT_SERVICES_DATA, base=0xA7F19000, pages=231, attribute=0),  # non-WB
            m.Descriptor(m.EFI_RUNTIME_SERVICES_DATA, base=0xA8000000, pages=16),
            m.Descriptor(m.EFI_CONVENTIONAL_MEMORY, base=0xB0000000, pages=256),
        ]

    def test_scoped_total_never_exceeds_classic_total(self):
        descriptors = self._mixed_map()
        declared = [(0x80000000, 4 * m.PAGE_BYTES), (0x90000000, 64 << 20)]
        classic_total = m.total_bytes(m.classic_sweep(descriptors))
        scoped_total = m.total_bytes(m.scoped_sweep(descriptors, declared, chunk_cap=1 << 30))
        self.assertLessEqual(scoped_total, classic_total)
        self.assertLess(scoped_total, classic_total)  # strictly: the 231-page range and RT data are excluded

    def test_declaring_every_wb_extent_matches_classic_minus_non_wb(self):
        descriptors = self._mixed_map()
        declared = m.wb_swept_extents(descriptors)
        scoped_ops = m.scoped_sweep(descriptors, declared, chunk_cap=1 << 30)
        classic_ops = m.classic_sweep(descriptors)
        wb_classic_ops = [(d.base, d.size) for d in descriptors
                          if d.type in m.SWEPT_TYPES and (d.attribute & m.EFI_MEMORY_WB)]
        self.assertEqual(m.merged_intervals(scoped_ops), m.merged_intervals(wb_classic_ops))
        self.assertLess(m.total_bytes(scoped_ops), m.total_bytes(classic_ops))  # the non-WB range is excluded


class Hygiene(unittest.TestCase):
    def test_spdx_header(self):
        text = MODULE_PATH.read_text()
        self.assertIn("SPDX-License-Identifier: MIT", text.splitlines()[1])

    def test_size_limits(self):
        text = MODULE_PATH.read_text()
        self.assertLess(len(text.splitlines()), 1500)
        self.assertLess(len(text.encode()), 50 * 1024)

    def test_no_machine_identifying_paths(self):
        # Built, not a literal, so this check's own source does not match itself.
        needle = "/" + "Users/"
        for p in (MODULE_PATH, pathlib.Path(__file__).resolve()):
            self.assertNotIn(needle, p.read_text())


class Mutations(unittest.TestCase):
    """Each entry seeds one textual change into the model source, execs the
    mutated text in an isolated namespace (no file is written), and checks that
    at least one existing assertion above would then fail. A test that cannot
    fail proves nothing (project convention)."""

    MUTATIONS = [
        ("drop the attribute filter",
         "if not (d.attribute & EFI_MEMORY_WB):\n            raise SweepRefused(",
         "if False:\n            raise SweepRefused("),
        ("accept more than one covering descriptor",
         "if len(covering) != 1:",
         "if len(covering) > 100:"),
        ("stop refusing non-swept types",
         "if d.type not in SWEPT_TYPES:\n            raise SweepRefused(",
         "if False:\n            raise SweepRefused("),
        ("let chunk_cap be zero",
         "if chunk_cap <= 0:",
         "if chunk_cap < 0:"),
        ("widen classic_sweep to ignore the type filter",
         "return [(d.base, d.size) for d in descriptors if d.type in SWEPT_TYPES]",
         "return [(d.base, d.size) for d in descriptors]"),
        ("off-by-one the chunk size",
         "take = min(remaining, chunk_cap)",
         "take = min(remaining, chunk_cap + 1)"),
    ]

    def _load_mutated(self, needle, replacement):
        text = MODULE_PATH.read_text()
        self.assertIn(needle, text, "mutation target text not found (model source changed?)")
        mutated = text.replace(needle, replacement, 1)
        ns = {"__name__": "sp11_ebs_sweep_model_mutant"}
        exec(compile(mutated, str(MODULE_PATH) + " (mutant)", "exec"), ns)
        return ns

    @staticmethod
    def _probe(ns, call):
        """Run `call(ns)`. Returns ("refused", None) if it raised exactly the
        mutant's own SweepRefused, ("crashed", the exception) if it raised
        anything else (a mutation that turns a refusal into an unhandled
        exception is still a broken safety net, not a pass), or
        ("returned", the value) otherwise."""
        try:
            return "returned", call(ns)
        except ns["SweepRefused"]:
            return "refused", None
        except Exception as exc:  # noqa: BLE001 - deliberately broad, see docstring
            return "crashed", exc

    # Each probe calls code that must be refused (SweepRefused) under the
    # correct model; `expect_refusal` tests exactly that, so for these entries
    # "returned" (no refusal) is the broken outcome, "crashed" counts as broken
    # too (an uncaught exception is not a graceful refusal either), and only
    # "refused" is the correct, unbroken outcome.
    PROBES = {
        "drop the attribute filter":
            lambda ns: ns["scoped_sweep"](
                [ns["Descriptor"](ns["EFI_BOOT_SERVICES_DATA"], base=0x5000, pages=1, attribute=0)],
                [(0x5000, 0x1000)]),
        "accept more than one covering descriptor":
            lambda ns: ns["scoped_sweep"](
                [ns["Descriptor"](ns["EFI_LOADER_DATA"], base=0x1000, pages=1),
                 ns["Descriptor"](ns["EFI_LOADER_DATA"], base=0x2000, pages=1)],
                [(0x1800, 0x1000)]),  # straddles the boundary: 0 descriptors cover it alone
        "stop refusing non-swept types":
            lambda ns: ns["scoped_sweep"](
                [ns["Descriptor"](ns["EFI_CONVENTIONAL_MEMORY"], base=0x6000, pages=1)],
                [(0x6000, 0x1000)]),
        "let chunk_cap be zero":
            # Empty declared_ranges: the chunk_cap guard is the first statement
            # in scoped_sweep, before any loop, so this probes only that guard
            # and cannot loop forever under the mutant (which would otherwise
            # divide work into zero-size chunks that never drain `remaining`).
            lambda ns: ns["scoped_sweep"]([], [], chunk_cap=0),
    }

    def test_each_seeded_bug_breaks_at_least_one_check(self):
        for name, needle, replacement in self.MUTATIONS:
            with self.subTest(mutation=name):
                ns = self._load_mutated(needle, replacement)
                if name in self.PROBES:
                    outcome, extra = self._probe(ns, self.PROBES[name])
                    self.assertNotEqual(outcome, "refused",
                                        f"mutation {name!r} was still refused; it did not break anything")
                elif name == "widen classic_sweep to ignore the type filter":
                    d = ns["Descriptor"](ns["EFI_CONVENTIONAL_MEMORY"], base=0x6000, pages=1)
                    ops = ns["classic_sweep"]([d])
                    self.assertTrue(ops, f"mutation {name!r} did not change classic_sweep's output")
                elif name == "off-by-one the chunk size":
                    d = ns["Descriptor"](ns["EFI_LOADER_DATA"], base=0x90000000,
                                          pages=(8 << 20) // ns["PAGE_BYTES"])
                    ops = ns["scoped_sweep"]([d], [(0x90000000, 8 << 20)], chunk_cap=1 << 20)
                    self.assertTrue(any(size > (1 << 20) for _, size in ops),
                                    f"mutation {name!r} did not exceed the chunk cap")
                else:
                    self.fail(f"no check wired for mutation {name!r}")


if __name__ == "__main__":
    unittest.main()
