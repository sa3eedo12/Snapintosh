/* SPDX-License-Identifier: MIT */
/* Host-native tests for the pure logic of the SP11 X2 EL2 tests image
 * (probes/uefi/tests).
 *
 * The image runs once, on a machine nobody here has, and decides kill criterion
 * K1 (NV2). So every decision it makes is exercised here with hostile input: ID
 * images that are all ones, all zeros or self-contradictory, an NV2 test whose
 * model core fails at each step, a register file that refuses to take a write
 * back, a guarded-read policy fed every kind of instruction, ACPI tables with
 * bad lengths, device models that fault or advertise windows that do not
 * contain what they claim, and a USB volume that errors out. Buffers are
 * allocated at their exact size where a parser could run past them, so the
 * sanitizer build reports any such access.
 *
 * This file owns the counters and main(); the tests live in
 * probes/uefi/tests/test/. The plain build is the command below.
 * tools/test-uefi-tests.py builds it plain and under ASan+UBSan from the same
 * line (it parses the line below, and fails if a probes/uefi/tests/test/test_*.c
 * file is missing from it).
 *
 *   clang -std=c11 -O1 -g -Wall -Wextra -Werror -Iprobes/uefi -Iprobes/uefi/tests \
 *       tools/test-uefi-tests-logic.c probes/uefi/tests/test/test_x_core.c \
 *       probes/uefi/tests/test/test_x_nv2.c probes/uefi/tests/test/test_x_dev.c \
 *       probes/uefi/tests/test/test_x_windows.c \
 *       probes/uefi/tests/test/test_x_report.c probes/uefi/tests/test/test_x_hook.c \
 *       probes/uefi/tests/test/test_x_ebs.c \
 *       probes/uefi/tests/test/test_x_v2.c \
 *       probes/uefi/tests/tests_types.c \
 *       probes/uefi/tests/nv2rec.c probes/uefi/tests/regs.c probes/uefi/tests/decode.c \
 *       probes/uefi/tests/nv2.c probes/uefi/tests/sysstate.c probes/uefi/tests/guard.c \
 *       probes/uefi/tests/acpix.c probes/uefi/tests/env.c probes/uefi/tests/t_misc.c \
 *       probes/uefi/tests/t_gic.c probes/uefi/tests/t_pcie.c probes/uefi/tests/t_smmu.c \
 *       probes/uefi/tests/report_x.c probes/uefi/tests/stick.c probes/uefi/tests/desc_none.c \
 *       probes/uefi/tests/ebs_ranges.c \
 *       probes/uefi/logic.c probes/uefi/acpi.c probes/uefi/fbtext.c probes/uefi/font6x8.c \
 *       probes/uefi/report.c probes/uefi/sysreg.c \
 *       -o build/test-uefi-tests-logic && build/test-uefi-tests-logic
 *
 * `--emit-records` prints the NV2 record fixtures in all their forms (text, JSON,
 * binary and screen rows, hex-encoded) for the round trip through
 * tools/nv2_record.py that tools/test-uefi-tests.py performs. `--ebs-cases` reads
 * declared-range cases on standard input and prints the table the builder makes of
 * each, for the differential run against tools/sp11_ebs_sweep_model.py. Adding
 * -DX_SLBOUNCE=1 -DSLBOUNCE_SCOPED_SWEEP=1 -I<slbounce>/src <slbounce>/src/sp11_scoped_sweep.c
 * adds the section that checks the builder against that fork's validator and range rule;
 * tools/test-uefi-tests.py does so only when SLBOUNCE_SP11_DIR names the fork.
 */
#include <stdio.h>
#include <string.h>

#include "test/xtests.h"

#if defined(__has_feature)
#if __has_feature(address_sanitizer)
#define BUILT_WITH_ASAN 1
#endif
#endif
#ifndef BUILT_WITH_ASAN
#define BUILT_WITH_ASAN 0
#endif

unsigned g_checks;
unsigned g_failures;
static const char *g_section;
static unsigned g_section_checks;
static unsigned g_section_failures;

void section_done(void)
{
    if (g_section == NULL)
        return;
    printf("%s %-26s %u checks\n", g_failures == g_section_failures ? "ok  " : "FAIL", g_section,
           g_checks - g_section_checks);
    g_section = NULL;
}

void section(const char *name)
{
    section_done();
    g_section = name;
    g_section_checks = g_checks;
    g_section_failures = g_failures;
}

/* Only the first failures are printed so that one systematic defect does not
 * bury the log; the count still covers every failure. */
void failed(int line, const char *text)
{
    g_failures++;
    if (g_failures <= 60)
        printf("FAIL line %d [%s]: %s\n", line, g_section ? g_section : "?", text);
}

int main(int argc, char **argv)
{
    if (argc > 1 && strcmp(argv[1], "--emit-records") == 0)
        return x_emit_records();
    if (argc > 1 && strcmp(argv[1], "--ebs-cases") == 0)
        return x_ebs_cases();

    printf("sanitizers: %s\n", BUILT_WITH_ASAN ? "AddressSanitizer + UndefinedBehaviorSanitizer"
                                              : "none (plain build)");
    test_x_ids();
    test_x_faulty_ids();
    test_x_syndrome();
    test_x_guard();
    test_x_sysstate();
    test_x_window();
    test_x_nv2_classify();
    test_x_nv2_decide();
    test_x_nv2_run();
    test_x_nv2_regime();
    test_x_record_evidence();
    test_x_record_format();
    test_x_acpi();
    test_x_gic();
    test_x_t03_cntpoff();
    test_x_t05_gicc();
    test_x_pcie();
    test_x_smmu_misc();
    test_x_windows();
    test_x_report();
    test_x_stick();
    test_x_hook_clean();
    test_x_hook_cleanup_failures();
    test_x_hook_console();
    test_x_hook_fatal_page();
    test_x_hook_phase_b_fallback();
    test_x_hook_post_order();
    test_x_ebs_layout();
    test_x_ebs_bytes();
    test_x_ebs_check();
    test_x_ebs_model();
#ifdef X_SLBOUNCE
    test_x_ebs_slbounce();
#endif
    section_done();

    printf("%s: %u checks, %u failures\n", g_failures ? "FAIL" : "PASS", g_checks, g_failures);
    return g_failures ? 1 : 0;
}
