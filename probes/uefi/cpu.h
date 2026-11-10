/* SPDX-License-Identifier: MIT */
/* The only code in the probe that executes system-register or cache
 * instructions. It exists in the EFI image alone: the host tests substitute a
 * mock register reader, so nothing here can run, or be needed, off-target.
 *
 * Every system instruction the image may contain is enumerated by the PE test
 * (tools/test-uefi-probe.py) against an independent whitelist, so adding an
 * instruction here without updating that list fails the build's test.
 */
#ifndef PROBE_CPU_H
#define PROBE_CPU_H

#include <stddef.h>
#include <stdint.h>

/* Reads one register from enum probe_sysreg_id. Unknown ids return 0. The
 * caller owns the decision of whether a read is safe at the current exception
 * level and on this core; this function performs no gating. */
uint64_t probe_cpu_read_sysreg(int id);

/* Clean and invalidate [addr, addr + len) to the point of coherency, then
 * dsb sy. Matches probe_flush_fn so it can back the framebuffer flush hook.
 * The cache line size comes from CTR_EL0.DminLine, read once on first use. */
void probe_cpu_flush_dcache(void *ctx, const void *addr, size_t len);

/* Generic timer, used only after the exception level is known to be EL2. */
uint64_t probe_cpu_counter(void);
uint64_t probe_cpu_counter_freq(void);

/* Waits for interrupt forever. Never returns; touches no other state. */
void probe_cpu_park(void) __attribute__((noreturn));

#endif /* PROBE_CPU_H */
