/* SPDX-License-Identifier: MIT */
/* The descriptor values the tests compare ACPI against. A value is known only if
 * its field is non-zero; every one of them comes from config/boards/<board>.json
 * (build-time, tools/build-uefi-tests.py) and carries that file's provenance:
 * for the Surface Pro 11 they are INFERRED from a pinned public device tree.
 * They are cross-checks. The tests never read an address because the
 * descriptor says so; they read what ACPI says and report whether the
 * descriptor agrees (docs/SURFACE-PRO-11.md, Appendix A: "discovered at run
 * time and never hard-coded").
 */
#ifndef TESTS_DESC_H
#define TESTS_DESC_H

#include <stdint.h>

#define TESTS_DESC_MAX_CPUS 32u

typedef struct {
    uint32_t n_cpus; /* 0 = unknown */
    uint64_t mpidr[TESTS_DESC_MAX_CPUS];
    uint64_t gicd_base; /* 0 = unknown */
    uint64_t gicr_base;
    uint64_t gicr_stride;
    uint64_t gicr_size;
    uint64_t its_base;
    uint64_t pcie_smmu_base;
    uint64_t timer_hz;
    uint32_t ipa_bits; /* the monitor's stage-2 limit, 0 = unknown */
} tests_desc;

/* Defined by exactly one of: desc_none.c (all unknown) or the file the build
 * tool generates from a descriptor. */
extern const tests_desc tests_desc_data;

#endif /* TESTS_DESC_H */
