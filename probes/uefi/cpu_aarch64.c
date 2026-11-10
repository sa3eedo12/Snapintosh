/* SPDX-License-Identifier: MIT */
#include "cpu.h"

#include "logic.h"
#include "sysreg.h"

uint64_t probe_cpu_read_sysreg(int id)
{
    uint64_t v;

    switch (id) {
#define X(name, op0, op1, crn, crm, op2)                                               \
    case PSR_##name:                                                                   \
        __asm__ volatile("mrs %0, S" #op0 "_" #op1 "_C" #crn "_C" #crm "_" #op2        \
                         : "=r"(v));                                                   \
        return v;
        PROBE_SYSREG_LIST(X)
#undef X
    default:
        return 0;
    }
}

static void clean_and_invalidate_line(uint64_t va)
{
    __asm__ volatile("dc civac, %0" : : "r"(va) : "memory");
}

void probe_cpu_flush_dcache(void *ctx, const void *addr, size_t len)
{
    static uint64_t line;

    (void)ctx;
    if (line == 0) {
        uint64_t ctr = probe_cpu_read_sysreg(PSR_CTR_EL0);
        /* DminLine is log2 of the line size in 4-byte words. */
        line = 4ull << ((ctr >> 16) & 0xF);
    }
    (void)probe_cache_walk((uint64_t)(uintptr_t)addr, len, line, clean_and_invalidate_line);
    __asm__ volatile("dsb sy" : : : "memory");
}

uint64_t probe_cpu_counter(void)
{
    return probe_cpu_read_sysreg(PSR_CNTPCT_EL0);
}

uint64_t probe_cpu_counter_freq(void)
{
    return probe_cpu_read_sysreg(PSR_CNTFRQ_EL0);
}

void probe_cpu_park(void)
{
    for (;;)
        __asm__ volatile("wfi");
}
