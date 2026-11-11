/* SPDX-License-Identifier: MIT */
/* Entry points of the host-native tests of the X2 image logic
 * (probes/uefi/tests). tools/test-uefi-tests-logic.c owns the counters, main()
 * and the build line; the check macros are the probe's own.
 *
 * Every register image below is a synthetic fixture built to exercise a
 * decision. None of them is a measurement of any machine.
 */
#ifndef TESTS_XTESTS_H
#define TESTS_XTESTS_H

#include "../../test/harness.h"

#include "decode.h"
#include "nv2.h"
#include "sysstate.h"

void test_x_ids(void);
void test_x_faulty_ids(void);
void test_x_syndrome(void);
void test_x_guard(void);
void test_x_sysstate(void);
void test_x_window(void);
void test_x_nv2_classify(void);
void test_x_nv2_decide(void);
void test_x_nv2_run(void);
void test_x_record_format(void);
void test_x_record_evidence(void);
void test_x_acpi(void);
void test_x_gic(void);
void test_x_pcie(void);
void test_x_smmu_misc(void);
void test_x_windows(void);
void test_x_report(void);
void test_x_stick(void);
void test_x_hook_clean(void);
void test_x_hook_cleanup_failures(void);
void test_x_hook_console(void);
void test_x_hook_fatal_page(void);
void test_x_hook_phase_b_fallback(void);

/* `--emit-records`: one block per fixture record, for the Python round trip
 * through tools/nv2_record.py. */
int x_emit_records(void);

/* A plausible ID image of a core with FEAT_NV2: NV == 2, NV_frac == 0, EL1 and
 * EL2 implemented, 16-bit VMIDs, a 64-byte DC ZVA block. */
static inline void x_good_ids(tests_idset *s)
{
    memset(s, 0, sizeof *s);
    s->have = (1u << TI_COUNT) - 1u;
    s->v[TI_MIDR] = 0x00000000510F0010ull;
    s->v[TI_REVIDR] = 0x0000000000000003ull;
    s->v[TI_PFR0] = (1ull << 24) | (1ull << 12) | (1ull << 8) | (1ull << 4) | 1ull;
    s->v[TI_MMFR0] = (2ull << 60) | (1ull << 56) | (1ull << 20) | 5ull;
    s->v[TI_MMFR1] = (1ull << 8) | (2ull << 4);
    s->v[TI_MMFR2] = 2ull << 24;
    s->v[TI_MMFR4] = 0;
    s->v[TI_DCZID] = 4;
    s->v[TI_CTR] = 0x00000000B444C004ull;
}

static inline void x_set_nv(tests_idset *s, unsigned nv, unsigned frac)
{
    s->v[TI_MMFR2] = (s->v[TI_MMFR2] & ~(0xFull << 24)) | ((uint64_t)(nv & 0xF) << 24);
    s->v[TI_MMFR4] = (s->v[TI_MMFR4] & ~(0xFull << 20)) | ((uint64_t)(frac & 0xF) << 20);
}

/* 1 if every row of a result is canonical for the renderer (upper case, within
 * the font, at most 38 characters, no repeated spaces) and none was dropped. */
static inline int x_rows_valid(const tests_result *r)
{
    char out[PROBE_LINE_COLS + 1];

    if (r->overflow)
        return 0;
    for (unsigned i = 0; i < r->nrows; i++)
        if (probe_line_format(out, r->rows[i]) != 0)
            return 0;
    return 1;
}

#endif /* TESTS_XTESTS_H */
