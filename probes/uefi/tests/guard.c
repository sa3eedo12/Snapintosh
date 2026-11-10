/* SPDX-License-Identifier: MIT */
#include "guard.h"

enum tests_insn_class tests_guard_classify(uint32_t insn, unsigned *rt)
{
    *rt = insn & 0x1Fu;
    /* LDR Wt, [Xn, #imm12] and LDR Xt, [Xn, #imm12]: size 10 or 11, opc 01. */
    if ((insn & 0xFFC00000u) == 0xB9400000u || (insn & 0xFFC00000u) == 0xF9400000u)
        return INSN_LOAD;
    /* MRS Xt, <S op0 op1 CRn CRm op2>, op0 2 or 3. */
    if ((insn & 0xFFF00000u) == 0xD5300000u)
        return INSN_MRS;
    /* SYS #3, C7, C4, #1, Xt is DC ZVA, Xt. */
    if ((insn & 0xFFFFFFE0u) == 0xD50B7420u)
        return INSN_DCZVA;
    return INSN_OTHER;
}

enum tests_guard_action tests_guard_on_sync(tests_guard *g, struct tests_frame *f, uint64_t esr,
                                            uint64_t far, uint32_t insn)
{
    unsigned ec = tests_esr_ec(esr);
    unsigned rt;
    enum tests_insn_class cls;

    if (!g->armed)
        return GUARD_FATAL;
    cls = tests_guard_classify(insn, &rt);
    /* A load or a DC ZVA may only fail as a data abort; an ID-register read only
     * as an UNDEFINED instruction (EC 0) or a trapped system access (EC 0x18).
     * Any other pairing means the exception is not the one that was armed for. */
    if ((cls == INSN_LOAD || cls == INSN_DCZVA) && ec != TESTS_EC_DABT_CUR)
        return GUARD_FATAL;
    if (cls == INSN_MRS && ec != TESTS_EC_UNKNOWN && ec != TESTS_EC_SYS64)
        return GUARD_FATAL;
    if (cls == INSN_OTHER)
        return GUARD_FATAL;

    g->faults++;
    g->last_esr = esr;
    g->last_far = far;
    g->last_elr = f->elr;
    if (g->logged < TESTS_GUARD_LOG) {
        g->log_esr[g->logged] = esr;
        g->log_far[g->logged] = far;
        g->logged++;
    }
    /* A load or an MRS leaves all ones in its destination; DC ZVA has none (its
     * register names the address and keeps it). */
    if (cls != INSN_DCZVA && rt != 31u)
        f->x[rt] = ~0ull;
    f->elr += 4u;
    return GUARD_SKIPPED;
}

enum tests_guard_action tests_guard_on_serror(tests_guard *g, struct tests_frame *f, uint64_t esr)
{
    if (!g->armed || g->resume == 0)
        return GUARD_FATAL;
    g->serrors++;
    g->last_esr = esr;
    f->elr = g->resume;
    f->spsr |= TESTS_SPSR_A;
    return GUARD_RETURN;
}

void tests_exit_from_syndrome(const struct tests_frame *f, uint64_t esr, uint64_t far, int async,
                              tests_el1_exit *out)
{
    probe_memset(out, 0, sizeof *out);
    out->esr = esr;
    out->far = far;
    out->elr = f->elr;
    for (unsigned i = 0; i < 4; i++)
        out->x[i] = f->x[i];
    if (async) {
        out->kind = EXIT_ASYNC;
        return;
    }
    switch (tests_esr_ec(esr)) {
    case TESTS_EC_HVC64:
        out->kind = EXIT_HVC;
        out->imm = (uint32_t)(esr & 0xFFFFu);
        break;
    case TESTS_EC_SYS64:
        out->kind = EXIT_SYSREG;
        break;
    case TESTS_EC_DABT_LOW:
        out->kind = EXIT_DABT;
        break;
    default:
        out->kind = EXIT_OTHER;
        break;
    }
}
