/* SPDX-License-Identifier: MIT */
#include "sys.h"

#include "cpu.h"
#include "guard.h"
#include "sysreg.h"
#include "sysstate.h"

extern char tests_el2_vectors[];
extern char tests_el1_vectors[];
extern char tests_el1_stubs_begin[];
extern char tests_el1_stubs_end[];
extern char tests_el1_prog_control[];
extern char tests_el1_prog_redirect[];
void tests_el1_enter(uint64_t entry, uint64_t spsr);
void tests_el1_resume(void);
void tests_el2_exception(struct tests_frame *f, unsigned idx);
uint32_t tests_bus_ldr32(uint64_t addr);
extern char tests_bus_ldr32_resume[];

/* SPSR_EL2 for an ERET to EL1h, AArch64, with D, A, I and F masked. */
#define SPSR_EL1H_MASKED 0x3C5ull
/* SPSR_EL2 for the return to this code: EL2h with everything masked. */
#define SPSR_EL2H_MASKED 0x3C9ull
/* SCTLR_EL1 for the stub: MMU, caches and alignment checks off, the RES1 bits
 * set (0x30D00800 is the Armv8.0 RES1 pattern: bits 11, 20, 22, 23, 28, 29). */
#define SCTLR_EL1_STUB 0x30D00800ull

static tests_guard g_guard;
static tests_el1_exit g_exit;
static volatile int g_exit_valid;
static volatile int g_el1_active;
static tests_fatal_fn g_fatal;
static tests_sysstate g_scope_state;
static tests_sysstate g_nv2_state;
static int g_nv2_with_vncr;

/* Owned by the image: the VNCR page (4 KiB aligned: BADDR is bits [56:12] of
 * VNCR_EL2), the DC ZVA buffer and a stack the EL1 stub never uses but SP_EL1
 * must point somewhere valid. */
static uint64_t g_vncr_page[TESTS_NV2_PAGE_WORDS] __attribute__((aligned(4096)));
static uint8_t g_zva_buf[TESTS_ZVA_BUFFER_BYTES] __attribute__((aligned(4096)));
static uint8_t g_el1_stack[1024] __attribute__((aligned(16)));

/* ---- Fatal path ---- */

/* An exception while the first fatal report is being made, or after the image
 * has been told to stop (tests_sys_quiesce), must not report again: it is an
 * interrupt the report's own console call let in, or a fault in the drawing, and
 * answering it would re-enter the code it interrupted. It parks. */
static volatile int g_fatal_entered;

static __attribute__((noreturn)) void park(void)
{
    for (;;)
        __asm__ volatile("wfi");
}

static __attribute__((noreturn)) void fatal(const char *why, uint64_t esr, uint64_t far,
                                            uint64_t elr, unsigned vec)
{
    if (g_fatal_entered)
        park();
    g_fatal_entered = 1;
    if (g_fatal != NULL)
        g_fatal(why, esr, far, elr, vec);
    park();
}

void tests_sys_set_fatal(tests_fatal_fn fn)
{
    g_fatal = fn;
}

/* ---- The C half of the EL2 vectors ---- */

void tests_el2_exception(struct tests_frame *f, unsigned idx)
{
    uint64_t esr, far;

    __asm__ volatile("mrs %0, esr_el2" : "=r"(esr));
    __asm__ volatile("mrs %0, far_el2" : "=r"(far));
    switch (idx) {
    case 4: /* current EL with SPx, synchronous: a guarded access failed */
        if (g_guard.armed) {
            uint32_t insn = *(const uint32_t *)(uintptr_t)f->elr;

            if (tests_guard_on_sync(&g_guard, f, esr, far, insn) == GUARD_SKIPPED)
                return;
        }
        break;
    case 7: /* current EL with SPx, SError */
        if (tests_guard_on_serror(&g_guard, f, esr) == GUARD_RETURN)
            return;
        break;
    case 8:  /* lower EL, AArch64: synchronous */
    case 9:  /* IRQ */
    case 10: /* FIQ */
    case 11: /* SError */
        if (g_el1_active) {
            tests_exit_from_syndrome(f, esr, far, idx != 8, &g_exit);
            g_exit_valid = 1;
            f->elr = (uint64_t)(uintptr_t)tests_el1_resume;
            f->spsr = SPSR_EL2H_MASKED;
            return;
        }
        break;
    default:
        break;
    }
    fatal("UNEXPECTED EXCEPTION", esr, far, f->elr, idx);
}

/* ---- Guarded reads ---- */

static int sys_rd(int id, uint64_t *v)
{
    unsigned before = g_guard.faults;
    uint64_t x = 0;

    g_guard.armed = 1;
    switch (id) {
#define X(name, op0, op1, crn, crm, op2)                                                      \
    case TR_##name:                                                                           \
        __asm__ volatile("mrs %0, S" #op0 "_" #op1 "_C" #crn "_C" #crm "_" #op2 : "=r"(x)    \
                         :                                                                    \
                         : "memory");                                                         \
        break;
        TESTS_SYSREG_LIST(X)
#undef X
    default:
        g_guard.armed = 0;
        *v = ~0ull;
        return 1;
    }
    g_guard.armed = 0;
    if (g_guard.faults != before) {
        *v = ~0ull;
        return 1;
    }
    *v = x;
    return 0;
}

static int bus_rd_sys(void *ctx, int id, uint64_t *v)
{
    (void)ctx;
    return sys_rd(id, v);
}

static int bus_rd32(void *ctx, uint64_t addr, uint32_t *v)
{
    unsigned faults = g_guard.faults;
    unsigned serrors = g_guard.serrors;
    uint32_t x;

    (void)ctx;
    /* A synchronous abort skips the load; an SError, which is asynchronous and
     * may be reported against this load or the one after it, resumes at the
     * continuation registered here and never retries the load. Either way the
     * read is counted as failed below. */
    g_guard.resume = (uint64_t)(uintptr_t)tests_bus_ldr32_resume;
    g_guard.armed = 1;
    x = tests_bus_ldr32(addr);
    g_guard.armed = 0;
    g_guard.resume = 0;
    if (g_guard.faults != faults || g_guard.serrors != serrors) {
        *v = 0xFFFFFFFFu;
        return 1;
    }
    *v = x;
    return 0;
}

static unsigned bus_faults(void *ctx)
{
    (void)ctx;
    return g_guard.faults + g_guard.serrors;
}

static const tests_bus g_bus = {NULL, bus_rd32, bus_rd_sys, bus_faults};

const tests_bus *tests_sys_bus(void)
{
    return &g_bus;
}

unsigned tests_sys_fault_count(void)
{
    return g_guard.faults + g_guard.serrors;
}

uint64_t tests_sys_currentel(void)
{
    return probe_cpu_read_sysreg(PSR_CURRENTEL);
}

#if TESTS_SELFTEST
int tests_sys_selftest_undef(uint64_t *v)
{
    unsigned before = g_guard.faults;
    uint64_t x = 0;

    g_guard.armed = 1;
    __asm__ volatile("mrs %0, S3_7_C15_C15_7" : "=r"(x) : : "memory");
    g_guard.armed = 0;
    if (g_guard.faults != before) {
        *v = ~0ull;
        return 1;
    }
    *v = x;
    return 0;
}
#endif

/* ---- The registers the image may write ---- */

static int e2h_set(void)
{
    uint64_t hcr = 0;

    return sys_rd(TR_HCR_EL2, &hcr) == 0 && ((hcr >> 34) & 1u) != 0;
}

static int wop_rd(void *ctx, int w, uint64_t *v)
{
    int e2h;

    (void)ctx;
    switch (w) {
    case W_VBAR_EL2:
        return sys_rd(TR_VBAR_EL2, v);
    case W_HCR_EL2:
        return sys_rd(TR_HCR_EL2, v);
    case W_VNCR_EL2:
        return sys_rd(TR_VNCR_EL2, v);
    case W_SCTLR_EL1X:
        e2h = e2h_set();
        return sys_rd(e2h ? TR_SCTLR_EL12 : TR_SCTLR_EL1, v);
    case W_VBAR_EL1X:
        e2h = e2h_set();
        return sys_rd(e2h ? TR_VBAR_EL12 : TR_VBAR_EL1, v);
    case W_SP_EL1:
        return sys_rd(TR_SP_EL1, v);
    case W_DAIF:
        return sys_rd(TR_DAIF, v);
    case W_ELR_EL2:
        return sys_rd(TR_ELR_EL2, v);
    case W_SPSR_EL2:
        return sys_rd(TR_SPSR_EL2, v);
    default:
        return -1;
    }
}

static int wop_wr(void *ctx, int w, uint64_t v)
{
    int e2h;

    (void)ctx;
    switch (w) {
    case W_VBAR_EL2:
        __asm__ volatile("msr S3_4_C12_C0_0, %0\n\tisb" : : "r"(v) : "memory");
        return 0;
    case W_HCR_EL2:
        __asm__ volatile("msr S3_4_C1_C1_0, %0\n\tisb" : : "r"(v) : "memory");
        return 0;
    case W_VNCR_EL2:
        __asm__ volatile("msr S3_4_C2_C2_0, %0\n\tisb" : : "r"(v) : "memory");
        return 0;
    case W_SCTLR_EL1X:
        e2h = e2h_set();
        if (e2h)
            __asm__ volatile("msr S3_5_C1_C0_0, %0\n\tisb" : : "r"(v) : "memory");
        else
            __asm__ volatile("msr S3_0_C1_C0_0, %0\n\tisb" : : "r"(v) : "memory");
        return 0;
    case W_VBAR_EL1X:
        e2h = e2h_set();
        if (e2h)
            __asm__ volatile("msr S3_5_C12_C0_0, %0\n\tisb" : : "r"(v) : "memory");
        else
            __asm__ volatile("msr S3_0_C12_C0_0, %0\n\tisb" : : "r"(v) : "memory");
        return 0;
    case W_SP_EL1:
        __asm__ volatile("msr S3_4_C4_C1_0, %0\n\tisb" : : "r"(v) : "memory");
        return 0;
    case W_DAIF:
        __asm__ volatile("msr S3_3_C4_C2_1, %0\n\tisb" : : "r"(v) : "memory");
        return 0;
    case W_ELR_EL2:
        __asm__ volatile("msr S3_4_C4_C0_1, %0\n\tisb" : : "r"(v) : "memory");
        return 0;
    case W_SPSR_EL2:
        __asm__ volatile("msr S3_4_C4_C0_0, %0\n\tisb" : : "r"(v) : "memory");
        return 0;
    default:
        return -1;
    }
}

static const tests_wops g_wops = {NULL, wop_rd, wop_wr};

int tests_sys_scope_enter(void)
{
    if (((uintptr_t)tests_el2_vectors & 0x7FFu) != 0)
        return TESTS_ERR; /* VBAR_EL2 needs a 2 KiB aligned table; nothing was changed */
    return tests_scope_enter(&g_scope_state, &g_wops, (uint64_t)(uintptr_t)tests_el2_vectors);
}

int tests_sys_scope_leave(void)
{
    return tests_scope_leave(&g_scope_state, &g_wops);
}

int tests_sys_quiesce(void)
{
    int failed;

    /* From here on an exception to this image's vectors parks without calling
     * anything, even if the vector base turns out not to have gone back. */
    g_fatal_entered = 1;
    failed = tests_sysstate_restore(&g_nv2_state, &g_wops) != 0;
    failed |= tests_sysstate_restore(&g_scope_state, &g_wops) != 0;
    (void)wop_wr(NULL, W_DAIF, TESTS_DAIF_MASKED);
    return failed ? -1 : 0;
}

/* ---- NV2 operations ---- */

/* The SCTLR_EL1 bits that decide how the stub runs: MMU (M), alignment check (A),
 * data cache (C), instruction cache (I), write-implies-XN (WXN) and endianness
 * (EE). The register's other bits are RES0/RES1 or feature bits whose read-back
 * value is the hardware's business, not the image's. */
#define SCTLR_STUB_VERIFIED                                                                   \
    ((1ull << 0) | (1ull << 1) | (1ull << 2) | (1ull << 12) | (1ull << 19) | (1ull << 25))

static int nv2_prepare(void *ctx)
{
    uint64_t mmfr2 = 0, spsel = 0;
    unsigned mask = TESTS_WMASK_NV2;

    (void)ctx;
    if (((uintptr_t)g_vncr_page & 0xFFFu) != 0 || ((uintptr_t)tests_el1_vectors & 0x7FFu) != 0)
        return TESTS_ERR;
    /* The EL1 stub returns by exception entry, which uses SP_EL2: the code must
     * be running on it already (SPSel == 1). */
    if (sys_rd(TR_SPSEL, &spsel) != 0 || (spsel & 1u) != 1u)
        return TESTS_ERR;
    g_nv2_with_vncr = sys_rd(TR_ID_AA64MMFR2_EL1, &mmfr2) == 0 && ((mmfr2 >> 24) & 0xFu) == 2u;
    if (g_nv2_with_vncr)
        mask |= TESTS_WMASK(W_VNCR_EL2);
    if (tests_sysstate_save(&g_nv2_state, &g_wops, mask) != 0)
        return TESTS_ERR;

    /* The stub runs with the stage 1 MMU off and fetches uncached, so its code
     * must be in memory, not just in this core's data cache. */
    probe_cpu_flush_dcache(NULL, tests_el1_stubs_begin,
                           (size_t)(tests_el1_stubs_end - tests_el1_stubs_begin));
    probe_cpu_flush_dcache(NULL, tests_el1_vectors, 2048);

    /* Every EL1 register is read back: an EL1 exception that missed these
     * vectors, or a stub running with its MMU on, would run wild, so the stub is
     * not started unless the writes took. A failed roll-back is reported as such. */
    if (tests_sysstate_write_verified(&g_nv2_state, &g_wops, W_SCTLR_EL1X, SCTLR_EL1_STUB,
                                      SCTLR_STUB_VERIFIED) != 0 ||
        tests_sysstate_write_verified(&g_nv2_state, &g_wops, W_VBAR_EL1X,
                                      (uint64_t)(uintptr_t)tests_el1_vectors, ~0ull) != 0 ||
        tests_sysstate_write_verified(&g_nv2_state, &g_wops, W_SP_EL1,
                                      (uint64_t)(uintptr_t)(g_el1_stack + sizeof g_el1_stack),
                                      ~0ull) != 0)
        return tests_sysstate_restore(&g_nv2_state, &g_wops) == 0 ? TESTS_ERR
                                                                  : TESTS_CLEANUP_FAILED;
    return 0;
}

/* HCR_EL2 and VNCR_EL2 are read back before the ERET to EL1: a bit the hardware
 * ignored (or a firmware that changed the register under us) must stop the run,
 * not send the stub into EL1 under a configuration nobody chose. */
static int nv2_set_hcr(void *ctx, int nv, int nv2, int map_vncr)
{
    (void)ctx;
    if (map_vncr) {
        if (!g_nv2_with_vncr ||
            tests_sysstate_write_verified(&g_nv2_state, &g_wops, W_VNCR_EL2,
                                          (uint64_t)(uintptr_t)g_vncr_page, ~0ull) != 0)
            return -1;
    }
    return tests_sysstate_write_verified(&g_nv2_state, &g_wops, W_HCR_EL2,
                                         tests_hcr_for(g_nv2_state.saved[W_HCR_EL2], nv, nv2),
                                         ~0ull);
}

static int nv2_run(void *ctx, int program, tests_el1_exit *out)
{
    const char *entry = program == EL1_PROG_CONTROL    ? tests_el1_prog_control
                        : program == EL1_PROG_REDIRECT ? tests_el1_prog_redirect
                                                       : NULL;

    (void)ctx;
    if (entry == NULL)
        return -1;
    g_exit_valid = 0;
    g_el1_active = 1;
    tests_el1_enter((uint64_t)(uintptr_t)entry, SPSR_EL1H_MASKED);
    g_el1_active = 0;
    if (!g_exit_valid)
        return -1;
    *out = g_exit;
    return 0;
}

static int nv2_real_tpidr(void *ctx, uint64_t *v)
{
    (void)ctx;
    return sys_rd(TR_TPIDR_EL2, v);
}

static uint64_t *nv2_page(void *ctx)
{
    (void)ctx;
    return g_vncr_page;
}

static void nv2_page_sync(void *ctx)
{
    (void)ctx;
    probe_cpu_flush_dcache(NULL, g_vncr_page, sizeof g_vncr_page);
}

static int nv2_restore(void *ctx)
{
    (void)ctx;
    return tests_sysstate_restore(&g_nv2_state, &g_wops);
}

static const tests_nv2_ops g_nv2_ops = {NULL,         nv2_prepare, nv2_set_hcr, nv2_run,
                                        nv2_real_tpidr, nv2_page,  nv2_page_sync, nv2_restore};

const tests_nv2_ops *tests_sys_nv2_ops(void)
{
    return &g_nv2_ops;
}

/* ---- DC ZVA ---- */

static int zva_do(void *ctx, uint8_t *addr)
{
    unsigned before = g_guard.faults;

    (void)ctx;
    g_guard.armed = 1;
    __asm__ volatile("dc zva, %0" : : "r"(addr) : "memory");
    g_guard.armed = 0;
    return g_guard.faults != before;
}

static void zva_sync(void *ctx)
{
    (void)ctx;
    probe_cpu_flush_dcache(NULL, g_zva_buf, sizeof g_zva_buf);
}

static const tests_zva_ops g_zva_ops = {NULL, g_zva_buf, zva_do, zva_sync};

const tests_zva_ops *tests_sys_zva_ops(void)
{
    return &g_zva_ops;
}
