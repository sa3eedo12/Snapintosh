/* SPDX-License-Identifier: MIT */
/* The three ACPI tables the device tests take their addresses from: the MADT
 * (GIC distributor, redistributors, ITS and the CPU list), the MCFG (PCIe
 * ECAM windows) and the IORT (SMMUv3 nodes). Pure parsers over a buffer the
 * caller has already read through the memory-map-checked path of the probe
 * (probe_phys_ops), so a hostile table can only produce a refusal.
 *
 * Layouts: ACPI 6.5 sections 5.2.12 (MADT), the PCI Firmware Specification
 * (MCFG) and the IORT specification, read here as the ACPICA structure
 * definitions of Linux v6.12 include/acpi/actbl2.h: GICC lines 1100-1119,
 * GICD 1131-1139, GICR 1170-1176, ITS 1182-1189, MCFG 1461-1474, IORT table
 * 460-465, IORT node 470-478, SMMUv3 node 602-614, node types 482-490.
 * Nothing is copied from those files; the offsets below are facts.
 */
#ifndef TESTS_ACPIX_H
#define TESTS_ACPIX_H

#include <stddef.h>
#include <stdint.h>

#include "acpi.h"
#include "env.h"

#define TESTS_MAX_GICR 4u
#define TESTS_MAX_ITS 4u
#define TESTS_MAX_CPUS 32u
#define TESTS_MAX_ECAM 8u
#define TESTS_MAX_SMMU 4u
#define TESTS_ACPI_BUF_BYTES (64u * 1024u)

/* The register frames the device tests read, which is also what the parsers
 * validate each table base against (alignment from env.h, extent from here). */
#define TESTS_GIC_FRAME_BYTES 0x10000u
#define TESTS_SMMU_WINDOW_BYTES 0x100u

typedef struct {
    uint64_t base;
    uint64_t len;
} tests_range;

/* A base or length that fails tests_window_check (zero, misaligned, empty,
 * wrapping, or past TESTS_PA_LIMIT) is never kept as a usable entry: the GICR,
 * ITS and SMMU entries are dropped and counted in "rejected", the GICD and each
 * ECAM entry carry the reason in a status field. */
typedef struct {
    int valid;
    int have_gicd;
    uint64_t gicd_base;
    int gicd_status;      /* TESTS_WIN_OK, or why gicd_base must not be read */
    uint8_t gicd_version; /* 0 = not stated, else the MADT value (2, 3 or 4) */
    unsigned n_gicr;
    tests_range gicr[TESTS_MAX_GICR]; /* redistributor discovery ranges */
    unsigned n_its;
    uint64_t its[TESTS_MAX_ITS];
    unsigned n_cpus; /* enabled GICC entries kept */
    uint64_t mpidr[TESTS_MAX_CPUS];
    uint64_t gicc_gicr[TESTS_MAX_CPUS]; /* per-CPU redistributor base, 0 if absent */
    unsigned n_gicc_seen;               /* every GICC entry, enabled or not */
    unsigned truncated;                 /* entries dropped because an array was full */
    unsigned rejected;                  /* GICR and ITS entries dropped for a bad base or length */
    unsigned duplicate_gicd;            /* more than one GICD entry: refused */
} tests_madt;

/* 0 when the MADT parsed, -1 for a malformed table (every entry length is
 * checked before the entry is read, and an entry shorter than its minimum is a
 * refusal rather than a skip). */
int tests_madt_parse(const uint8_t *t, size_t len, tests_madt *out);

typedef struct {
    uint64_t base; /* processor-relative address of the ECAM window */
    uint16_t segment;
    uint8_t start_bus;
    uint8_t end_bus;
    uint8_t status; /* TESTS_WIN_OK, or why the window must not be read */
} tests_ecam;

typedef struct {
    int valid;
    unsigned n;
    tests_ecam e[TESTS_MAX_ECAM];
    unsigned truncated;
} tests_mcfg;

int tests_mcfg_parse(const uint8_t *t, size_t len, tests_mcfg *out);

typedef struct {
    int valid;
    unsigned n;
    uint64_t base[TESTS_MAX_SMMU]; /* SMMUv3 register page 0 */
    unsigned truncated;
    unsigned rejected; /* SMMUv3 nodes dropped for a bad base */
} tests_iort;

int tests_iort_parse(const uint8_t *t, size_t len, tests_iort *out);

/* Reads the first validated table with this signature into buf (cap bytes) and
 * returns its length, or 0 if there is none or it does not fit. */
size_t tests_acpi_load(const probe_acpi_scan *scan, const probe_phys_ops *ops, void *ops_ctx,
                       const char sig[4], uint8_t *buf, size_t cap);

#endif /* TESTS_ACPIX_H */
