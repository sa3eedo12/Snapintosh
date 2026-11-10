/* SPDX-License-Identifier: MIT */
/* How the X2 tests attach to the probe's entry point (probes/uefi/probe_main.c,
 * under -DPROBE_TESTS_IMAGE=1). The probe has already gathered the firmware
 * facts, read ACPI, written its dump to the USB stick and chosen a framebuffer;
 * the two hooks below add the tests:
 *
 *   tests_hook_pre_ebs   before ExitBootServices: the tests that touch no device
 *                        (ID dump, timer, DC ZVA, NV2), then their results to the
 *                        stick, next to the probe's dump;
 *   tests_hook_post_ebs  in place of the probe's post-exit stage: the device
 *                        tests, then the report as photographable pages.
 */
#ifndef TESTS_HOOK_H
#define TESTS_HOOK_H

#include "acpi.h"
#include "efi_types.h"
#include "fbtext.h"
#include "logic.h"
#include "report.h"

typedef struct {
    efi_system_table *st;
    efi_handle image;
    const probe_facts *facts;
    const probe_acpi_scan *scan;
    const probe_memmap *memmap;
    const probe_phys_ops *ops; /* firmware memory, checked against the memory map */
    void *ops_ctx;
    probe_fb *fb;
    void (*reset_system)(uint32_t, efi_status, efi_uintn, void *);
} tests_hook_ctx;

/* Returns 0 to go on to ExitBootServices, non-zero to refuse it (the report
 * could not be shown afterwards, and after ExitBootServices the screen is the
 * only output). */
int tests_hook_pre_ebs(const tests_hook_ctx *c);

__attribute__((noreturn)) void tests_hook_post_ebs(const tests_hook_ctx *c);

#endif /* TESTS_HOOK_H */
