/* SPDX-License-Identifier: MIT */
#include "psci.h"

#include "cpu.h"
#include "logic.h"

/* Function IDs from the Arm PSCI specification (DEN0022): PSCI_VERSION and
 * the SMC64 calling-convention CPU_ON. */
#define PSCI_FN_VERSION 0x84000000ull
#define PSCI_FN_CPU_ON64 0xC4000003ull

/* Seconds to wait for the secondary, and the fixed spin bound used when the
 * generic timer frequency reads as zero. */
#define PSCI_WAIT_SECONDS 2u
#define PSCI_FALLBACK_SPINS 20000000ull

static uint64_t psci_call(uint64_t fn, uint64_t a1, uint64_t a2, uint64_t a3)
{
#if defined(__aarch64__)
    register uint64_t x0 __asm__("x0") = fn;
    register uint64_t x1 __asm__("x1") = a1;
    register uint64_t x2 __asm__("x2") = a2;
    register uint64_t x3 __asm__("x3") = a3;

    /* SMCCC lets the callee clobber x4-x17. Only the SMC conduit is issued: an
     * HVC taken at EL2 goes to this exception level's own vector, which this
     * image never installs, so the HVC conduit is refused before any call. */
    __asm__ volatile("smc #0"
                     : "+r"(x0), "+r"(x1), "+r"(x2), "+r"(x3)
                     :
                     : "x4", "x5", "x6", "x7", "x8", "x9", "x10", "x11", "x12", "x13", "x14",
                       "x15", "x16", "x17", "memory");
    return x0;
#else
    /* Host builds of the policy tests link this; it is never called there. */
    (void)fn;
    (void)a1;
    (void)a2;
    (void)a3;
    return (uint64_t)-1;
#endif
}

/* Entered by the secondary with the MMU and caches off and no stack. x0 holds
 * the context id, which is the mailbox address. x18 and the stack pointer are
 * never touched. The aligned(64) keeps the whole stub inside one cache line so
 * that cleaning one line to the point of coherency covers it. */
#if defined(__aarch64__)
__attribute__((naked, noreturn, aligned(64))) static void secondary_entry(void)
{
    __asm__ volatile("mrs x1, CurrentEL\n"
                     "mrs x2, MPIDR_EL1\n"
                     "str x1, [x0, #8]\n"
                     "str x2, [x0, #16]\n"
                     "dsb sy\n"
                     "mov x3, #1\n"
                     "str x3, [x0]\n"
                     "dsb sy\n"
                     "1: wfi\n"
                     "b 1b\n");
}
#else
static void secondary_entry(void)
{
}
#endif

static int wait_for_secondary(probe_psci_cell *cell)
{
    uint64_t freq = probe_cpu_counter_freq();
    uint64_t start = probe_cpu_counter();
    uint64_t spins = 0;

    for (;;) {
        probe_cpu_flush_dcache(NULL, cell, sizeof *cell);
        if (cell->arrived != 0)
            return 1;
        if (freq != 0) {
            if (probe_cpu_counter() - start > freq * PSCI_WAIT_SECONDS)
                return 0;
        } else if (++spins > PSCI_FALLBACK_SPINS) {
            return 0;
        }
    }
}

void probe_psci_run(probe_psci_facts *out, int conduit, const probe_cpu_list *cpus,
                    uint64_t own_mpidr, probe_psci_cell *cell)
{
    uint64_t target = 0;
    uint64_t ret;

    probe_memset(out, 0, sizeof *out);
    out->enabled = 1;
    out->state = PROBE_PSCI_RUN_NOT_ATTEMPTED;

    /* Refused before anything else happens: PSCI_USE_HVC describes a hypervisor
     * that this image would have to call from EL2, where an HVC is an exception
     * to ourselves and no vector of ours exists to take it. */
    if (conduit == PROBE_PSCI_HVC) {
        out->state = PROBE_PSCI_RUN_HVC_REFUSED;
        return;
    }
    if (conduit != PROBE_PSCI_SMC) {
        out->state = PROBE_PSCI_RUN_NO_CONDUIT;
        return;
    }

    out->version = (uint32_t)psci_call(PSCI_FN_VERSION, 0, 0, 0);
    out->version_called = 1;

    if (!probe_pick_psci_target(cpus, own_mpidr, &target)) {
        out->state = PROBE_PSCI_RUN_NO_TARGET;
        return;
    }
    out->have_target = 1;
    out->target_mpidr = target;

    probe_memset(cell, 0, sizeof *cell);
    probe_cpu_flush_dcache(NULL, cell, sizeof *cell);
    probe_cpu_flush_dcache(NULL, (const void *)secondary_entry, 64);

    ret = psci_call(PSCI_FN_CPU_ON64, target, (uint64_t)(uintptr_t)secondary_entry,
                    (uint64_t)(uintptr_t)cell);
    out->cpu_on_called = 1;
    out->cpu_on_ret = (uint32_t)ret;
    if ((int32_t)ret == 0 && wait_for_secondary(cell)) {
        out->arrived = 1;
        out->arrived_el_raw = cell->currentel;
        out->arrived_mpidr = cell->mpidr;
    }
    out->state = PROBE_PSCI_RUN_DONE;
}
