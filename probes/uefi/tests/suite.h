/* SPDX-License-Identifier: MIT */
/* The context every test of the X2 image receives, and the test entry points.
 *
 * Each run_* function fills one tests_result: a verdict, a label, the detail
 * rows and the one-line "proves" / "does not prove" statements. A test that
 * cannot justify an address does not touch it: it says UNKNOWN and why.
 */
#ifndef TESTS_SUITE_H
#define TESTS_SUITE_H

#include <stddef.h>
#include <stdint.h>

#include "acpi.h"
#include "acpix.h"
#include "decode.h"
#include "desc.h"
#include "env.h"
#include "nv2.h"
#include "tests_types.h"

/* DC ZVA test operations: one buffer owned by the image and a guarded DC ZVA. */
typedef struct {
    void *ctx;
    uint8_t *buffer;     /* TESTS_ZVA_BUFFER_BYTES, aligned to TESTS_ZVA_BUFFER_BYTES / 3 or better */
    int (*zva)(void *ctx, uint8_t *addr); /* 0 on success, non-zero if it faulted */
    void (*sync)(void *ctx);              /* make the buffer visible: clean, invalidate, dsb */
} tests_zva_ops;

#define TESTS_ZVA_MAX_BLOCK 2048u
#define TESTS_ZVA_BUFFER_BYTES (3u * TESTS_ZVA_MAX_BLOCK)

typedef struct {
    const tests_bus *bus;
    const probe_acpi_scan *scan;
    const probe_phys_ops *phys;
    void *phys_ctx;
    const probe_memmap *memmap;
    const tests_desc *desc;
    const tests_zva_ops *zva;
    int model; /* a run under an emulator */
    /* Strict device-window policy: a window is read only if the UEFI memory map
     * lists all of it as MMIO (see tests_window_policy). 0 admits a window the
     * map does not mention, and still refuses one the map says is something else. */
    int require_mmio_map;
    /* T03 reads CNTPOFF_EL2 only if this is set, and then only when ID_AA64MMFR0_EL1.ECV says the
     * register exists. EL2 access to it is also gated at EL3 (SCR_EL3.ECVEn): where firmware
     * leaves that bit clear the access is taken to EL3, and a monitor that does not handle it can
     * hang the machine. INFERRED from one frozen page on the Surface Pro 11, not tested there, so
     * the default is never to read it. Zero unless the build asked (TESTS_READ_CNTPOFF, which only
     * the emulator images may carry). */
    int read_cntpoff;
    uint8_t *acpi_buf; /* TESTS_ACPI_BUF_BYTES scratch */
    tests_idset ids;   /* filled by T01, read by T02 and T04 */
    /* Called before each device window is read, with a short text naming it, so
     * that a hang can be attributed to one address (the image draws it as the
     * footer). May be NULL. */
    void (*progress)(void *ctx, const char *text);
    void *progress_ctx;
} tests_ctx;

/* T01: the raw ID dump. Also fills c->ids. */
void tests_run_id(tests_ctx *c, tests_result *r);
/* T03 timer, T04 DC ZVA, T05 GIC plus MADT, T06 PCIe and NVMe state, T07 PCIe SMMU
 * state, T08 ADSP and PMIC-GLINK liveness, T09 DRAM map. */
void tests_run_timer(tests_ctx *c, tests_result *r);
void tests_run_zva(tests_ctx *c, tests_result *r);
void tests_run_gic(tests_ctx *c, tests_result *r);
void tests_run_pcie(tests_ctx *c, tests_result *r);
void tests_run_smmu(tests_ctx *c, tests_result *r);
void tests_run_adsp(tests_ctx *c, tests_result *r);
void tests_run_dram(tests_ctx *c, tests_result *r);

/* T02 fills the row part of the result; the record is returned separately, and
 * *have_record says whether there is one. */
void tests_run_nv2(tests_ctx *c, const tests_nv2_ops *ops, uint32_t soc, uint32_t build_id,
                   tests_result *r, tests_nv2_record *rec, int *have_record);

/* The static statements each test carries (proves, does not prove). */
void tests_init_result(tests_result *r, int id);

/* Rows shared by T05 to T07 for device windows. tests_row_refused writes
 * "<what><index> REFUSED: <reason>" (no index when index < 0); tests_row_windows
 * writes how many admitted windows the memory map lists as MMIO, how many it
 * does not mention (never any under the strict policy), and how many were
 * refused (those the policy turned away plus parse_refused, the table entries
 * dropped before the policy saw them). */
void tests_row_refused(tests_result *r, const char *what, int index, int status);
void tests_row_windows(tests_result *r, const tests_allow *a, unsigned parse_refused, int strict);

/* Pure helpers exposed for the host tests. */
typedef struct {
    int smem; /* -1 not observed, 0 bad, 1 good */
    int glink;
    int ipcc;
} tests_adsp_obs;
int tests_adsp_decide(const tests_adsp_obs *o, int *label);

typedef struct {
    int ok;
    uint64_t total_bytes;
    uint64_t low;
    uint64_t high;
    unsigned extents;
    uint64_t ext_base[8];
    uint64_t ext_end[8];
    unsigned truncated;
} tests_dram;
void tests_dram_summarize(const probe_memmap *m, tests_dram *out);
/* PASS when every RAM byte is below 1 << ipa_bits, FAIL when not. */
int tests_dram_verdict(const tests_dram *d, unsigned ipa_bits);

#endif /* TESTS_SUITE_H */
