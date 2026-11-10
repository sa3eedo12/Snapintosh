/* SPDX-License-Identifier: MIT */
/* Host-native tests for the pure logic of the SP11 EL2 probe (probes/uefi).
 *
 * The probe runs once, on a machine whose firmware nobody here has seen, and
 * after ExitBootServices its only output is a screen photograph. So every
 * policy that keeps it passive and fail-closed is exercised here with hostile
 * input: hand-built firmware tables with bad checksums, hostile lengths and
 * overlaps, device paths that must not be mistaken for a USB stick, memory
 * maps with wrapping descriptors, framebuffers one byte too small, a USB dump
 * pushed to each of its caps and through every write error, and a mock
 * register reader that proves nothing is read when CurrentEL is not EL2.
 * Buffers are allocated at their exact size so AddressSanitizer reports any
 * read or write past them.
 *
 * This file owns the counters and main(); the tests live in
 * probes/uefi/test/. The plain build is the command below. The sanitizer
 * build is made by tools/test-uefi-probe.py from the same command (it parses
 * the line below, and fails if a probes/uefi/test/test_*.c file is missing
 * from it). dump.c is not on the line: test_dump.c #includes it, because the
 * functions it tests are static.
 *
 *   clang -std=c11 -O1 -g -Wall -Wextra -Werror -Iprobes/uefi \
 *       tools/test-uefi-probe-logic.c probes/uefi/test/test_text.c \
 *       probes/uefi/test/test_logic.c probes/uefi/test/test_acpi.c \
 *       probes/uefi/test/test_report.c probes/uefi/test/test_psci.c \
 *       probes/uefi/test/test_dump.c \
 *       probes/uefi/logic.c probes/uefi/acpi.c probes/uefi/fbtext.c \
 *       probes/uefi/font6x8.c probes/uefi/report.c probes/uefi/sysreg.c \
 *       probes/uefi/psci.c \
 *       -o build/test-uefi-probe-logic && build/test-uefi-probe-logic
 *
 * `--dump-font` prints every glyph as it is rendered by the probe's own draw
 * path, one "G <hex code> <8 rows of # and . joined by />" line per code. The
 * host screen decoder (probes/uefi/host/screen.py) learns the font from it, so
 * the decoder never carries a second copy of the glyph data.
 *
 * `--dump-screen WIDTH HEIGHT BODY_LINES PAGE` writes one drawn page as a PPM,
 * for pointing that decoder at panel sizes QEMU cannot provide.
 */
#include <stdio.h>
#include <string.h>

#include "test/harness.h"

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
    if (argc > 1 && strcmp(argv[1], "--dump-font") == 0)
        return dump_font();
    if (argc > 1 && strcmp(argv[1], "--dump-screen") == 0)
        return dump_screen(argc, argv);

    printf("sanitizers: %s\n", BUILT_WITH_ASAN ? "AddressSanitizer + UndefinedBehaviorSanitizer"
                                              : "none (plain build)");
    test_crc();
    test_builder();
    test_font();
    test_line_format();
    test_layout();
    test_framebuffer();
    test_gop_policy();
    test_cache_walk();
    test_device_path();
    test_memmap();
    test_decisions();
    test_sysreg();
    test_acpi();
    test_smbios();
    test_fadt_madt();
    test_collect();
    test_report();
    test_psci_policy();
    test_dump_caps();
    test_dump_overwrite();
    test_dump_errors();
    test_dump_run();
    section_done();

    printf("%s: %u checks, %u failures\n", g_failures ? "FAIL" : "PASS", g_checks, g_failures);
    return g_failures ? 1 : 0;
}
