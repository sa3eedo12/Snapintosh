/* SPDX-License-Identifier: MIT */
/* The on-screen report: a summary of every test, then each test's rows, as the
 * photographable pages of the probe (probes/uefi/fbtext.h). Every row is
 * upper case, at most 38 characters and gets its own CRC-8 from the renderer.
 *
 * The page layout is chosen once, for the largest report the builder can make,
 * so that rows added while the tests run can never overflow it; the number of
 * pages that is shown and the countdown come from the real row count.
 * tests_layout_choose extends the probe's layout rule to more than eight pages
 * because this report can be longer than the probe's on a small framebuffer.
 */
#ifndef TESTS_REPORT_X_H
#define TESTS_REPORT_X_H

#include <stddef.h>
#include <stdint.h>

#include "suite.h"

/* The most rows the report can have: the summary (title, one row per test, the
 * K1 line) plus, for every test, a title, TESTS_DETAIL_MAX detail rows and the
 * two proves / does-not-prove rows. Sized so that no test can be cut off: a
 * result that fills its detail rows to the limit still shows in full. */
#define TESTS_REPORT_MAX_ROWS (2u + TESTS_COUNT + TESTS_COUNT * (TESTS_DETAIL_MAX + 3u))
_Static_assert(TESTS_REPORT_MAX_ROWS == 254u, "the report capacity follows the test and row limits");

typedef struct {
    unsigned count;
    int overflowed;
    probe_payload row[TESTS_REPORT_MAX_ROWS];
} tests_report;

/* Builds the summary and the per-test sections from results[1..TESTS_COUNT]
 * (index 0 is unused). A test that has not run shows as NOT-RUN. */
void tests_report_build(tests_report *rep, const tests_result *results, int model);

/* The K1 line: "K1 NV2: GO", "NO-GO", "INCONCLUSIVE" or "NOT RUN". */
void tests_report_k1(const tests_result *nv2, probe_payload out);

/* Header rows. */
void tests_report_header0(probe_payload out, const char *build_id, int model);
void tests_report_header1(probe_payload out, int el, unsigned page, unsigned pages);

/* Like probe_layout_choose, but accepts any page count up to max_pages by
 * building the scale-3 geometry directly. Returns 0, or a negative
 * probe_layout_status value. */
int tests_layout_choose(uint32_t width, uint32_t height, unsigned nbody, unsigned max_pages,
                        probe_layout *out);
/* 0 if the geometry of the layout lies inside the framebuffer. */
int tests_layout_fits(const probe_layout *l, uint32_t width, uint32_t height);

/* The countdown that ends the run, scaled with the number of pages: every page
 * is shown at least twice, and never less than 60 s. */
unsigned tests_countdown_seconds(unsigned pages, unsigned dwell_seconds);

/* Footer texts. */
void tests_footer_running(probe_payload out, int id, const char *name);
void tests_footer_text(probe_payload out, const char *text);

#endif /* TESTS_REPORT_X_H */
