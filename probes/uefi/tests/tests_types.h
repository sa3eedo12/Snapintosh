/* SPDX-License-Identifier: MIT */
/* Vocabulary shared by every test of the SP11 X2 image: evidence labels,
 * verdicts and the one result record each test fills in.
 *
 * Why the labels look like this: the image is the instrument that decides kill
 * criterion K1 (NV2) of docs/SURFACE-PRO-11.md, so a result must say how it
 * was obtained. A value read from a register on the machine that runs the
 * image is OBSERVED; it is never VERIFIED, because one run on one unit does
 * not verify anything. A conclusion drawn from ID bits alone is INFERRED. A
 * run under an emulator is a model: its results are labelled as such and never
 * reach the NV2 record as evidence (see tests_label_name and nv2rec.h).
 */
#ifndef TESTS_TYPES_H
#define TESTS_TYPES_H

#include <stddef.h>
#include <stdint.h>

#include "fbtext.h"
#include "logic.h"

/* Values 0-5 are the evidence codes of the NV2 record (tools/nv2_record.py,
 * EVIDENCE), so a label becomes a record field without a lookup table. */
enum tests_label {
    TL_NONE = 0,
    TL_VERIFIED = 1,
    TL_OBSERVED = 2,
    TL_INFERRED = 3,
    TL_EXPERIMENTAL = 4,
    TL_UNIMPLEMENTED = 5,
    TL_UNKNOWN = 6,
    TL_COUNT
};

enum tests_verdict {
    TV_NOT_RUN = 0, /* the test never started */
    TV_INFO,        /* data only; there is nothing to pass or fail */
    TV_PASS,
    TV_FAIL,
    TV_UNKNOWN,      /* ran, but could not decide: unjustified address, fault, contradiction */
    TV_GO,           /* NV2 only: usable on this core */
    TV_NO_GO,        /* NV2 only: absent or unusable (kill criterion K1 candidate) */
    TV_INCONCLUSIVE, /* NV2 only: do not decide K1 from this run */
    TV_COUNT
};

/* Test numbers are stable: they appear on the screen, in the stick files and in
 * the operator guide. */
enum tests_id {
    T_ID = 1,
    T_NV2 = 2,
    T_TIMER = 3,
    T_ZVA = 4,
    T_GIC = 5,
    T_PCIE = 6,
    T_SMMU = 7,
    T_ADSP = 8,
    T_DRAM = 9,
    T_LAST = T_DRAM
};
#define TESTS_COUNT 9u

/* Which tests may run before ExitBootServices (no device is touched there) and
 * which only afterwards: the device state that matters for the monitor is the
 * state after ExitBootServices (docs/SURFACE-PRO-11.md, risk R9). */
#define TESTS_PHASE_A_MASK ((1u << T_ID) | (1u << T_NV2) | (1u << T_TIMER) | (1u << T_ZVA))

#define TESTS_DETAIL_MAX 24u
#define TESTS_NAME_MAX 12u

typedef struct {
    uint8_t id;      /* enum tests_id */
    uint8_t verdict; /* enum tests_verdict */
    uint8_t label;   /* enum tests_label */
    uint8_t phase;   /* 1 before ExitBootServices, 2 after, 0 not run */
    uint8_t fatal;   /* the test left machine state this image cannot vouch for: the
                      * caller must stop, and must not persist or show anything more */
    int overflow;    /* a row was dropped: a bug in a fixed-format row, never silent */
    const char *name;       /* static, at most TESTS_NAME_MAX characters */
    const char *proves;     /* static, one screen row: what a good result establishes */
    const char *not_proves; /* static, one screen row: what it does not establish */
    unsigned nrows;
    probe_payload rows[TESTS_DETAIL_MAX];
} tests_result;

const char *tests_verdict_name(int verdict);
/* model != 0 marks a run under an emulator: OBSERVED is then shown as
 * OBS-MODEL so that a photograph of a model run cannot pass for hardware. */
const char *tests_label_name(int label, int model);
/* The label a completed measurement may carry in this kind of run. */
enum tests_label tests_measured_label(int model);

void tests_result_init(tests_result *r, int id, const char *name, const char *proves,
                       const char *not_proves);
/* Starts a new detail row and returns its builder, or NULL when the result is
 * full (the result is then flagged overflow). The builder writes into r. */
probe_sb *tests_row(tests_result *r);
/* Call once after the last row is built: a row that did not fit is only
 * noticed when the next row starts or the result is sealed. */
void tests_result_seal(tests_result *r);
void tests_row_str(tests_result *r, const char *key, const char *value);
void tests_row_hex(tests_result *r, const char *key, uint64_t v, unsigned digits);
void tests_row_dec(tests_result *r, const char *key, uint64_t v);
/* One row carrying a bare message. */
void tests_row_text(tests_result *r, const char *text);

/* Number of display rows the result occupies: title, detail, proves, not-proves. */
unsigned tests_result_rows(const tests_result *r);

#endif /* TESTS_TYPES_H */
