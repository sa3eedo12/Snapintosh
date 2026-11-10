/* SPDX-License-Identifier: MIT */
/* The NV2 functional-test record, written in the exact forms tools/nv2_record.py
 * reads: text lines with a per-line check, JSON and the 96-byte binary record
 * with its CRC-32. tools/nv2_record.py owns the format (its module docstring is
 * the specification); this file only writes it. tools/test-uefi-tests.py feeds
 * what is written here back through that tool, so a drift on either side fails
 * a test instead of surfacing at the first hardware run.
 *
 * The monitor's gate (compat/nv2-gate.h) accepts only evidence VERIFIED or
 * OBSERVED. This image never writes VERIFIED, and writes OBSERVED only when
 * both checks ran on hardware (see tests_nv2rec_evidence).
 */
#ifndef TESTS_NV2REC_H
#define TESTS_NV2REC_H

#include <stddef.h>
#include <stdint.h>

#include "fbtext.h"

#define TESTS_NV2_BIN_SIZE 96u
#define TESTS_NV2_KEYS 11u
#define TESTS_NV2_LINES (TESTS_NV2_KEYS + 2u) /* begin, the keys, end */
#define TESTS_NV2_LINE_MAX 64u
#define TESTS_NV2_TOOL_MAX 15u

enum tests_nv2_soc {
    NV2R_SOC_UNKNOWN = 0,
    NV2R_SOC_X1E80100 = 1,
    NV2R_SOC_X2E96100 = 2,
};

enum tests_nv2_result {
    NV2R_NOT_RUN = 0,
    NV2R_PASS = 1,
    NV2R_FAIL = 2,
};

typedef struct {
    uint32_t soc; /* enum tests_nv2_soc, an explicit build constant */
    uint64_t midr_el1;
    uint64_t revidr_el1;
    uint64_t id_aa64mmfr2_el1;
    uint64_t id_aa64mmfr4_el1;
    uint32_t vncr_redirect;            /* enum tests_nv2_result */
    uint32_t negative_control_trapped; /* enum tests_nv2_result */
    uint32_t evidence;                 /* enum tests_label, 0..5 */
    char tool[TESTS_NV2_TOOL_MAX + 1]; /* [A-Za-z0-9._-], 1..15 characters */
    uint32_t build_id;
} tests_nv2_record;

/* 0 if every field is in the range the format allows, else a negative code. A
 * record that fails this is never written. */
int tests_nv2rec_check(const tests_nv2_record *r);

/* The evidence a record may claim: OBSERVED only for a hardware run in which
 * both the redirect and the negative control actually ran (each result is
 * pass or fail, not not-run); EXPERIMENTAL for a model run or an incomplete
 * one that did run something; NONE when nothing ran. VERIFIED is never
 * returned. */
uint32_t tests_nv2rec_evidence(int model, uint32_t vncr_redirect, uint32_t negative_control);

uint32_t tests_crc32(const void *data, size_t n);

/* Fills the 96-byte binary form. Returns 0, or -1 for an invalid record. */
int tests_nv2rec_pack(const tests_nv2_record *r, uint8_t out[TESTS_NV2_BIN_SIZE]);

/* The 13 text lines, each "NV2R ..." followed by "*hh". Returns 13, or 0 for an
 * invalid record. */
unsigned tests_nv2rec_lines(const tests_nv2_record *r, char lines[TESTS_NV2_LINES][TESTS_NV2_LINE_MAX]);

/* The lines joined with '\n' (and a final newline), or the canonical JSON.
 * Return the length written (without the terminating NUL), or 0 when the
 * record is invalid or the buffer is too small; the buffer is then empty. */
size_t tests_nv2rec_text(const tests_nv2_record *r, char *buf, size_t cap);
size_t tests_nv2rec_json(const tests_nv2_record *r, char *buf, size_t cap);

/* Screen form of the 13 lines: upper case (the font has no lower case), one
 * row each, except a line longer than 38 characters, which takes two rows: the
 * first 32 characters, then "+" and the rest. The operator tool
 * probes/uefi/tests/host/nv2screen.py undoes both. Returns the number of rows
 * written, or 0 if rows[] (capacity max_rows) is too small. */
unsigned tests_nv2rec_screen_rows(const char lines[TESTS_NV2_LINES][TESTS_NV2_LINE_MAX],
                                  probe_payload *rows, unsigned max_rows);

#endif /* TESTS_NV2REC_H */
