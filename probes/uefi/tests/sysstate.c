/* SPDX-License-Identifier: MIT */
#include "sysstate.h"

#include "logic.h"

int tests_sysstate_save(tests_sysstate *s, const tests_wops *ops, unsigned mask)
{
    probe_memset(s, 0, sizeof *s);
    for (int r = 0; r < W_COUNT; r++) {
        if (!(mask & TESTS_WMASK(r)))
            continue;
        if (ops->rd(ops->ctx, r, &s->saved[r]) != 0) {
            probe_memset(s, 0, sizeof *s);
            return -1;
        }
        s->have[r] = 1;
    }
    return 0;
}

int tests_sysstate_write(tests_sysstate *s, const tests_wops *ops, int wreg, uint64_t value)
{
    if (!tests_wreg_allowed(wreg) || !s->have[wreg])
        return -1;
    s->touched[wreg] = 1;
    return ops->wr(ops->ctx, wreg, value);
}

int tests_sysstate_write_verified(tests_sysstate *s, const tests_wops *ops, int wreg,
                                  uint64_t value, uint64_t compare_mask)
{
    uint64_t now = 0;

    if (tests_sysstate_write(s, ops, wreg, value) != 0)
        return -1;
    if (ops->rd(ops->ctx, wreg, &now) != 0 || ((now ^ value) & compare_mask) != 0)
        return -1;
    return 0;
}

int tests_sysstate_mark_clobbered(tests_sysstate *s, int wreg)
{
    if (!tests_wreg_allowed(wreg) || !s->have[wreg])
        return -1;
    s->touched[wreg] = 1;
    return 0;
}

/* HCR_EL2 and VNCR_EL2 first, so that no NV bit is left set while other
 * registers are put back. ELR_EL2 and SPSR_EL2 go back while this image's
 * vectors are still installed and everything is still masked, so that no
 * exception can overwrite them again. The vector base goes back before the
 * interrupt masks: an IRQ that was pending during the test is taken the moment
 * DAIF is restored, and it must then reach the firmware's own vectors, not this
 * image's, which do not handle interrupts. The vector base is not last of all
 * the registers only because DAIF has to follow it. */
static const int restore_order[W_COUNT] = {
    W_HCR_EL2, W_VNCR_EL2, W_SCTLR_EL1X, W_VBAR_EL1X, W_SP_EL1,
    W_ELR_EL2, W_SPSR_EL2, W_VBAR_EL2,   W_DAIF,
};

/* Sticky: a restore that fails leaves the machine in a state this image cannot
 * vouch for, and the image must stop. It is a flag of the module rather than a
 * return value alone so that no caller can lose it by ignoring one. */
static volatile int g_cleanup_failed;

int tests_sysstate_cleanup_failed(void)
{
    return g_cleanup_failed;
}

void tests_sysstate_cleanup_reset(void)
{
    g_cleanup_failed = 0;
}

int tests_sysstate_restore(tests_sysstate *s, const tests_wops *ops)
{
    int failed = 0;

    for (int i = 0; i < W_COUNT; i++) {
        int r = restore_order[i];
        uint64_t now;

        if (!s->touched[r])
            continue;
        if (ops->wr(ops->ctx, r, s->saved[r]) != 0)
            failed = 1;
        if (ops->rd(ops->ctx, r, &now) != 0 || now != s->saved[r])
            failed = 1;
    }
    if (failed)
        g_cleanup_failed = 1;
    return failed ? -1 : 0;
}

int tests_scope_enter(tests_sysstate *s, const tests_wops *ops, uint64_t vectors)
{
    /* ELR_EL2 and SPSR_EL2 are saved here, before the vector base changes and so
     * before any exception can reach this image's vectors. */
    if (tests_sysstate_save(s, ops, TESTS_WMASK_SCOPE) != 0)
        return TESTS_ERR;
    if (tests_sysstate_mark_clobbered(s, W_ELR_EL2) != 0 ||
        tests_sysstate_mark_clobbered(s, W_SPSR_EL2) != 0 ||
        tests_sysstate_write_verified(s, ops, W_DAIF, TESTS_DAIF_MASKED, TESTS_DAIF_MASKED) != 0 ||
        tests_sysstate_write_verified(s, ops, W_VBAR_EL2, vectors, ~0ull) != 0)
        return tests_sysstate_restore(s, ops) == 0 ? TESTS_ERR : TESTS_CLEANUP_FAILED;
    return 0;
}

int tests_scope_leave(tests_sysstate *s, const tests_wops *ops)
{
    return tests_sysstate_restore(s, ops);
}
