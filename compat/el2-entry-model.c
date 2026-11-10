/* SPDX-License-Identifier: MIT */
/* Host model of the Surface Pro 11 EL2 entry transition. See el2-entry-model.h
 * for scope and evidence labels, and docs/SP11-EL2-ENTRY.md for the decision. */
#include "el2-entry-model.h"

#if SNAP_PLATFORM_SP11

#define BIT(n) (UINT64_C(1) << (n))
#define RANGE(hi, lo) ((~UINT64_C(0) >> (63 - (hi))) & (~UINT64_C(0) << (lo)))

/* Registers whose change alters how EL2 translation behaves. */
#define TRANSLATION_REGS ((1u << EL2M_HCR) | (1u << EL2M_TCR) | (1u << EL2M_TTBR0) | \
                          (1u << EL2M_TTBR1) | (1u << EL2M_MAIR))

unsigned el2m_effective_e2h(const struct el2m_platform *plat, uint64_t hcr)
{
    return plat->e2h0_implemented ? (unsigned)((hcr >> 34) & 1) : 1u;
}

enum el2m_layout el2m_layout_of(const struct el2m_platform *plat, uint64_t hcr)
{
    return el2m_effective_e2h(plat, hcr) ? EL2M_VHE : EL2M_NVHE;
}

/* Field positions: DDI 0601 (2026-09) TCR_EL2, CPTR_EL2, CNTHCTL_EL2 and
 * TTBR0_EL2. Only the reserved ranges are checked; defined fields are left to
 * the feature set of the implementation. */
int el2m_image_valid(enum el2m_reg reg, enum el2m_layout layout, uint64_t v)
{
    uint64_t res0 = 0, res1 = 0;
    switch (reg) {
    case EL2M_TCR:
        if (layout == EL2M_NVHE) {
            res0 = RANGE(63, 34) | BIT(19) | RANGE(7, 6);
            res1 = BIT(31) | BIT(23);
        } else {
            res0 = RANGE(63, 62) | BIT(35) | BIT(6);
            /* TG1 0b00 is a reserved granule encoding. */
            if (!((v >> 30) & 3)) return 0;
        }
        break;
    case EL2M_TTBR0:
        /* ASID [63:48] is RES0 unless E2H is 1. */
        if (layout == EL2M_NVHE) res0 = RANGE(63, 48);
        break;
    case EL2M_CNTHCTL:
        res0 = RANGE(63, 20);
        if (layout == EL2M_NVHE) res0 |= RANGE(11, 8);
        break;
    case EL2M_CPTR:
        if (layout == EL2M_NVHE) {
            res0 = RANGE(63, 32) | RANGE(29, 21) | RANGE(19, 14) | BIT(11);
            res1 = BIT(13) | BIT(9) | RANGE(7, 0);
        } else {
            res0 = RANGE(63, 32) | RANGE(27, 26) | RANGE(23, 22) | RANGE(19, 18) | RANGE(15, 0);
        }
        break;
    default:
        return 1;
    }
    return !(v & res0) && (v & res1) == res1;
}

int el2m_tcr_vhe_to_nvhe(uint64_t vhe, uint64_t *out)
{
    if (!el2m_image_valid(EL2M_TCR, EL2M_VHE, vhe) || (vhe & BIT(7))) return -1;
    uint64_t o = BIT(31) | BIT(23);
    o |= vhe & (RANGE(15, 8) | RANGE(5, 0));     /* TG0 SH0 ORGN0 IRGN0, T0SZ */
    o |= ((vhe >> 32) & 7) << 16;                /* IPS -> PS */
    o |= ((vhe >> 37) & 1) << 20;                /* TBI0 -> TBI */
    o |= ((vhe >> 39) & 1) << 21;                /* HA */
    o |= ((vhe >> 40) & 1) << 22;                /* HD */
    o |= ((vhe >> 41) & 1) << 24;                /* HPD0 -> HPD */
    o |= ((vhe >> 43) & 15) << 25;               /* HWU059..HWU062 -> HWU59..HWU62 */
    o |= ((vhe >> 51) & 1) << 29;                /* TBID0 -> TBID */
    o |= ((vhe >> 57) & 1) << 30;                /* TCMA0 -> TCMA */
    o |= ((vhe >> 59) & 1) << 32;                /* DS */
    o |= ((vhe >> 60) & 1) << 33;                /* MTX0 -> MTX */
    *out = o;
    return 0;
}

uint64_t el2m_ttbr0_vhe_to_nvhe(uint64_t vhe)
{
    return vhe & ~RANGE(63, 48);
}

int el2m_cnthctl_convert(enum el2m_layout from, enum el2m_layout to, uint64_t v, uint64_t *out)
{
    if (!el2m_image_valid(EL2M_CNTHCTL, from, v)) return -1;
    if (from == to) { *out = v; return 0; }
    /* EVNTEN, EVNTDIR, EVNTI and ECV..CNTPMASK sit at the same bits in both layouts. */
    uint64_t o = v & (RANGE(7, 2) | RANGE(19, 12));
    if (from == EL2M_NVHE) {
        o |= ((v >> 0) & 1) << 10;               /* EL1PCTEN */
        o |= ((v >> 1) & 1) << 11;               /* EL1PCEN -> EL1PTEN */
        o |= BIT(0) | BIT(1) | BIT(8) | BIT(9);  /* EL0 controls exist only here: not trapped */
    } else {
        o |= ((v >> 10) & 1) << 0;
        o |= ((v >> 11) & 1) << 1;
    }
    *out = o;
    return 0;
}

void el2m_cpu_arrive(struct el2m_cpu *c, const struct el2m_platform *plat, enum el2m_role role,
                     uint32_t cpu, unsigned mmu, unsigned e2h, unsigned tge, unsigned code_identity)
{
    *c = (struct el2m_cpu){ 0 };
    c->cpu = cpu;
    c->role = (uint8_t)role;
    /* With the MMU off VA == PA, so any code is identity mapped. */
    c->code_identity = (uint8_t)(code_identity || !mmu);
    c->tlb_dirty = 1;
    c->reg[EL2M_HCR] = EL2M_HCR_RW | (e2h ? EL2M_HCR_E2H : 0) | (tge ? EL2M_HCR_TGE : 0);
    c->reg[EL2M_SCTLR] = mmu ? EL2M_CANON_SCTLR_ON : 0;
    enum el2m_layout l = el2m_layout_of(plat, c->reg[EL2M_HCR]);
    for (unsigned r = 0; r < EL2M_REG_COUNT; r++) c->layout[r] = (uint8_t)l;
}

static int regs_pending(const struct el2m_cpu *c, uint32_t mask)
{
    return (c->pending_isb & mask) != 0;
}

/* A write that can leave the TLB disagreeing with the registers. Under the
 * relaxed policy a clean TLB stays clean while the MMU is off, because no
 * translation can allocate entries (INFERRED from DDI 0487 D8.16.1 HXRSY). */
static void dirty_tlb(struct el2m_cpu *c, const struct el2m_policy *pol)
{
    if (pol->strict_tlbi_after_config || (c->reg[EL2M_SCTLR] & EL2M_SCTLR_M)) {
        c->tlb_dirty = 1;
        c->tlbi_state = 0;
    }
}

enum el2m_status el2m_step(struct el2m_cpu *c, const struct el2m_platform *plat,
                           const struct el2m_policy *pol, const struct el2m_op *op)
{
    const int mmu_on = !!(c->reg[EL2M_SCTLR] & EL2M_SCTLR_M);
    switch (op->kind) {
    case EL2M_OP_MSR: {
        if (op->reg >= EL2M_REG_COUNT) return EL2M_E_BAD_OP;
        const uint64_t v = op->value;
        switch (op->reg) {
        case EL2M_HCR: {
            if (!plat->e2h0_implemented && !(v & EL2M_HCR_E2H)) return EL2M_E_E2H_NOT_PROGRAMMABLE;
            if (el2m_effective_e2h(plat, v) != el2m_effective_e2h(plat, c->reg[EL2M_HCR])) {
                if (mmu_on) return EL2M_E_E2H_CHANGE_MMU_ON;
                dirty_tlb(c, pol);
            }
            break;
        }
        case EL2M_SCTLR: {
            const int to_on = !!(v & EL2M_SCTLR_M);
            if (to_on != mmu_on) {
                if (!c->code_identity) return EL2M_E_MMU_TOGGLE_NOT_IDENTITY;
                if (to_on) {
                    if (regs_pending(c, TRANSLATION_REGS)) return EL2M_E_MMU_ON_UNSYNC;
                    if (c->tlb_dirty) return EL2M_E_MMU_ON_STALE_TLB;
                    const uint8_t want = (uint8_t)el2m_layout_of(plat, c->reg[EL2M_HCR]);
                    if (c->layout[EL2M_TCR] != want || c->layout[EL2M_TTBR0] != want)
                        return EL2M_E_LAYOUT;
                }
            }
            break;
        }
        case EL2M_TCR:
        case EL2M_TTBR0:
            if (mmu_on) return EL2M_E_CONFIG_WRITE_MMU_ON;
            if (op->layout != (uint8_t)el2m_layout_of(plat, c->reg[EL2M_HCR])) return EL2M_E_LAYOUT;
            if (!el2m_image_valid((enum el2m_reg)op->reg, (enum el2m_layout)op->layout, v))
                return EL2M_E_IMAGE_INVALID;
            dirty_tlb(c, pol);
            break;
        case EL2M_TTBR1:
        case EL2M_MAIR:
            if (mmu_on) return EL2M_E_CONFIG_WRITE_MMU_ON;
            dirty_tlb(c, pol);
            break;
        case EL2M_CNTHCTL:
        case EL2M_CPTR:
            if (op->layout != (uint8_t)el2m_layout_of(plat, c->reg[EL2M_HCR])) return EL2M_E_LAYOUT;
            if (!el2m_image_valid((enum el2m_reg)op->reg, (enum el2m_layout)op->layout, v))
                return EL2M_E_IMAGE_INVALID;
            break;
        default:
            break;
        }
        c->reg[op->reg] = v;
        c->layout[op->reg] = op->layout;
        c->pending_isb |= 1u << op->reg;
        return EL2M_OK;
    }
    case EL2M_OP_TLBI_ALLE2:
        if (pol->strict_isb_before_tlbi && regs_pending(c, 1u << EL2M_HCR)) return EL2M_E_TLBI_UNSYNC_HCR;
        c->tlbi_state = 1;
        return EL2M_OK;
    case EL2M_OP_DSB:
        if (c->tlbi_state == 1) c->tlbi_state = 2;
        return EL2M_OK;
    case EL2M_OP_ISB:
        c->pending_isb = 0;
        /* A TLB maintenance instruction is guaranteed complete only after a DSB
         * and then a context synchronization event (DDI 0487 D8.17.8 BLDZX). */
        if (c->tlbi_state == 2) { c->tlbi_state = 3; c->tlb_dirty = 0; }
        return EL2M_OK;
    case EL2M_OP_DATA:
        if (!mmu_on) return EL2M_E_DATA_ACCESS_MMU_OFF;
        if (c->pending_isb) return EL2M_E_USE_BEFORE_SYNC;
        return EL2M_OK;
    default:
        return EL2M_E_BAD_OP;
    }
}

static enum el2m_status final_check(const struct el2m_cpu *c, const struct el2m_platform *plat,
                                    const struct el2m_goal *goal)
{
    const uint64_t hcr = c->reg[EL2M_HCR];
    if ((unsigned)(!!(c->reg[EL2M_SCTLR] & EL2M_SCTLR_M)) != goal->mmu ||
        el2m_effective_e2h(plat, hcr) != goal->e2h || (unsigned)(!!(hcr & EL2M_HCR_TGE)) != goal->tge)
        return EL2M_E_FINAL_STATE;
    const uint8_t want = goal->e2h ? EL2M_VHE : EL2M_NVHE;
    if (c->layout[EL2M_TCR] != want || c->layout[EL2M_TTBR0] != want ||
        c->layout[EL2M_CNTHCTL] != want || c->layout[EL2M_CPTR] != want)
        return EL2M_E_LAYOUT;
    if (c->pending_isb) return EL2M_E_USE_BEFORE_SYNC;
    if (c->tlbi_state == 1 || c->tlbi_state == 2) return EL2M_E_FINAL_STATE;
    return EL2M_OK;
}

enum el2m_status el2m_run(struct el2m_cpu *c, const struct el2m_platform *plat,
                          const struct el2m_policy *pol, const struct el2m_seq *seq, size_t *fail_index)
{
    size_t scratch;
    if (!fail_index) fail_index = &scratch;
    const struct el2m_arrival *a = &seq->pre;
    const unsigned mmu = !!(c->reg[EL2M_SCTLR] & EL2M_SCTLR_M);
    const unsigned e2h = el2m_effective_e2h(plat, c->reg[EL2M_HCR]);
    const unsigned tge = !!(c->reg[EL2M_HCR] & EL2M_HCR_TGE);
    *fail_index = seq->n;
    if ((a->mmu >= 0 && (unsigned)a->mmu != mmu) || (a->e2h >= 0 && (unsigned)a->e2h != e2h) ||
        (a->tge >= 0 && (unsigned)a->tge != tge))
        return EL2M_E_ARRIVAL_STATE;
    for (size_t i = 0; i < seq->n; i++) {
        enum el2m_status s = el2m_step(c, plat, pol, &seq->ops[i]);
        if (s != EL2M_OK) { *fail_index = i; return s; }
    }
    return final_check(c, plat, &seq->goal);
}

enum el2m_status el2m_cpus_consistent(const struct el2m_cpu *cpus, size_t n)
{
    for (size_t i = 1; i < n; i++) {
        const struct el2m_cpu *a = &cpus[0], *b = &cpus[i];
        if (((a->reg[EL2M_HCR] ^ b->reg[EL2M_HCR]) & (EL2M_HCR_E2H | EL2M_HCR_TGE)) ||
            a->reg[EL2M_TCR] != b->reg[EL2M_TCR] || a->reg[EL2M_TTBR0] != b->reg[EL2M_TTBR0] ||
            a->reg[EL2M_MAIR] != b->reg[EL2M_MAIR] || a->layout[EL2M_TCR] != b->layout[EL2M_TCR] ||
            a->layout[EL2M_CPTR] != b->layout[EL2M_CPTR] || a->layout[EL2M_CNTHCTL] != b->layout[EL2M_CNTHCTL])
            return EL2M_E_CPU_DIVERGE;
    }
    return EL2M_OK;
}

/* ---- Sequences as data -------------------------------------------------- */

#define MSR(r, v, l) { EL2M_OP_MSR, (r), (l), (v) }
#define TLBI { EL2M_OP_TLBI_ALLE2, 0, 0, 0 }
#define DSB { EL2M_OP_DSB, 0, 0, 0 }
#define ISB { EL2M_OP_ISB, 0, 0, 0 }

/* The de-VHE trampoline. Order is the A16 secondary_entry's (configuration
 * writes, then DSB, TLBI, DSB, ISB, then the MMU) plus the steps SP11 adds:
 * the MMU goes off first, because E2H selects the regime the live MMU walks,
 * and an ISB follows the HCR_EL2 write so that TLBI ALLE2 and every later
 * write see the new E2H. Nothing between MMU off and MMU on touches memory. */
static const struct el2m_op devhe_ops[] = {
    MSR(EL2M_SCTLR, 0, 0),
    ISB,
    MSR(EL2M_HCR, EL2M_HCR_A16_Q1N1, 0),
    ISB,
    MSR(EL2M_MAIR, EL2M_CANON_MAIR, 0),
    MSR(EL2M_TCR, EL2M_CANON_TCR_NVHE, EL2M_NVHE),
    MSR(EL2M_TTBR0, EL2M_CANON_TTBR0, EL2M_NVHE),
    MSR(EL2M_CNTHCTL, EL2M_CANON_CNTHCTL, EL2M_NVHE),
    MSR(EL2M_CPTR, EL2M_CANON_CPTR, EL2M_NVHE),
    DSB, TLBI, DSB, ISB,
    MSR(EL2M_SCTLR, EL2M_CANON_SCTLR_ON, 0),
    ISB,
};

/* A PSCI arrival has the MMU off (DEN0022F.b 6.4.3.4), so it skips the first
 * two steps. HCR_EL2.E2H on arrival is UNKNOWN (DDI 0601 HCR_EL2, warm reset),
 * which is why the sequence is correct for either value. */
static const struct el2m_op secondary_ops[] = {
    MSR(EL2M_HCR, EL2M_HCR_A16_Q1N1, 0),
    ISB,
    MSR(EL2M_MAIR, EL2M_CANON_MAIR, 0),
    MSR(EL2M_TCR, EL2M_CANON_TCR_NVHE, EL2M_NVHE),
    MSR(EL2M_TTBR0, EL2M_CANON_TTBR0, EL2M_NVHE),
    MSR(EL2M_CNTHCTL, EL2M_CANON_CNTHCTL, EL2M_NVHE),
    MSR(EL2M_CPTR, EL2M_CANON_CPTR, EL2M_NVHE),
    DSB, TLBI, DSB, ISB,
    MSR(EL2M_SCTLR, EL2M_CANON_SCTLR_ON, 0),
    ISB,
};

/* slbounce tb_longjmp (BSD-3-Clause, c090a8c, src/trans.s), VHE direction:
 * MMU off, TLBI, HCR_EL2 = TGE|E2H, CNTHCTL_EL2 = 3 (EL0PCTEN and EL0VCTEN in
 * the VHE layout), CPACR_EL1 (which is CPTR_EL2 once E2H is 1), then TTBR0,
 * TCR and MAIR copied from the EL1 registers, DSB, ISB, SCTLR_EL2 with M. The
 * values are the shapes UEFI leaves, not captured data. */
static const struct el2m_op slbounce_ops[] = {
    MSR(EL2M_SCTLR, 0, 0),
    ISB,
    TLBI, DSB, TLBI,
    MSR(EL2M_VBAR, 0x80000800, 0), ISB,
    MSR(EL2M_HCR, EL2M_HCR_SLBOUNCE, 0), ISB,
    MSR(EL2M_CNTHCTL, 0x3, EL2M_VHE),
    MSR(EL2M_CPTR, UINT64_C(0x300000), EL2M_VHE), ISB,   /* FPEN = 0b11 */
    MSR(EL2M_TTBR0, EL2M_CANON_TTBR0, EL2M_VHE), ISB,
    MSR(EL2M_TCR, EL2M_TEST_TCR_VHE, EL2M_VHE), ISB,
    MSR(EL2M_MAIR, EL2M_CANON_MAIR, 0), ISB,
    DSB, ISB,
    MSR(EL2M_SCTLR, EL2M_CANON_SCTLR_ON, 0), ISB,
};

/* The slbounce restore with the SP11 conversion applied: what an SP11 fork of tb_entry and tb_longjmp has to do to hand
 * the EFI application a non-VHE EL2 instead of a VHE one. tb_entry already turns the MMU off (trans.s 27-31), so no E2H
 * change ever happens under a live stage 1. slbounce's own TLBI comes before the HCR_EL2 write, so a second one, with
 * its barriers, follows the register writes. The register values are the canonical test data. */
static const struct el2m_op slbounce_devhe_ops[] = {
    MSR(EL2M_SCTLR, 0, 0),
    ISB,
    TLBI, DSB, TLBI,
    MSR(EL2M_VBAR, 0x80000800, 0), ISB,
    MSR(EL2M_HCR, EL2M_HCR_A16_Q1N1, 0), ISB,
    MSR(EL2M_CNTHCTL, EL2M_CANON_CNTHCTL, EL2M_NVHE),
    MSR(EL2M_CPTR, EL2M_CANON_CPTR, EL2M_NVHE),
    MSR(EL2M_TTBR0, EL2M_CANON_TTBR0, EL2M_NVHE),
    MSR(EL2M_TCR, EL2M_CANON_TCR_NVHE, EL2M_NVHE),
    MSR(EL2M_MAIR, EL2M_CANON_MAIR, 0),
    DSB, TLBI, DSB, ISB,
    MSR(EL2M_SCTLR, EL2M_CANON_SCTLR_ON, 0),
    ISB,
};

/* probes/secondary.S secondary_entry: HCR_EL2 = RW (E2H clear), then MAIR, TCR
 * and TTBR0 from the primary's record, DSB, TLBI, DSB, ISB, SCTLR_EL2. It does
 * not write CNTHCTL_EL2 or CPTR_EL2 itself; cpu_boot.c does, later. */
static const struct el2m_op a16_secondary_ops[] = {
    MSR(EL2M_VBAR, 0x80000800, 0),
    MSR(EL2M_HCR, EL2M_HCR_RW, 0),
    MSR(EL2M_MAIR, EL2M_CANON_MAIR, 0),
    MSR(EL2M_TCR, EL2M_CANON_TCR_NVHE, EL2M_NVHE),
    MSR(EL2M_TTBR0, EL2M_CANON_TTBR0, EL2M_NVHE),
    DSB, TLBI, DSB, ISB,
    MSR(EL2M_SCTLR, EL2M_CANON_SCTLR_ON, 0),
    ISB,
};

#define SEQ(nm, pre_mmu, pre_e2h, pre_tge, g_e2h, g_tge, ops) \
    { nm, { pre_mmu, pre_e2h, pre_tge }, { g_e2h, g_tge, 1 }, ops, sizeof ops / sizeof ops[0] }

const struct el2m_seq el2m_seq_boot_devhe = SEQ("boot-devhe", 1, 1, 1, 0, 1, devhe_ops);
const struct el2m_seq el2m_seq_universal_devhe = SEQ("universal-devhe", -1, -1, -1, 0, 1, devhe_ops);
const struct el2m_seq el2m_seq_secondary_devhe = SEQ("secondary-devhe", 0, -1, -1, 0, 1, secondary_ops);
const struct el2m_seq el2m_ref_slbounce_vhe = SEQ("ref-slbounce-vhe", -1, -1, -1, 1, 1, slbounce_ops);
const struct el2m_seq el2m_seq_slbounce_devhe = SEQ("slbounce-devhe", -1, -1, -1, 0, 1, slbounce_devhe_ops);
const struct el2m_seq el2m_ref_a16_secondary = SEQ("ref-a16-secondary", 0, -1, -1, 0, 0, a16_secondary_ops);

/* ---- Rule table ---------------------------------------------------------- */

static const struct { const char *name, *cite; } rules[EL2M_STATUS_COUNT] = {
    [EL2M_OK] = { "OK", 0 },
    [EL2M_E_ARRIVAL_STATE] = { "ARRIVAL_STATE",
        "INFERRED: the sequence's declared precondition. Boot arrival (MMU on, E2H and TGE set) VERIFIED from slbounce "
        "src/trans.s c090a8c; PSCI arrival has the MMU off, VERIFIED DEN0022F.b 6.4.3.4, with E2H UNKNOWN, DDI 0601 HCR_EL2" },
    [EL2M_E_E2H_CHANGE_MMU_ON] = { "E2H_CHANGE_MMU_ON",
        "INFERRED (conservative): DDI 0487 M.d D8.13.1 MNWSS, QXCQP, STSYY (E2H picks the regime the MMU walks and is "
        "cached in a TLB), D8.1.4 RGGJW, K1.2.4; no public sentence names this case" },
    [EL2M_E_E2H_NOT_PROGRAMMABLE] = { "E2H_NOT_PROGRAMMABLE",
        "VERIFIED: DDI 0487 D8.13.1 CDMXB and DDI 0601 HCR_EL2.E2H: RES1 and behaves as 1 without FEAT_E2H0, even where a "
        "direct read returns 0 (ID_AA64MMFR4_EL1.E2H0)" },
    [EL2M_E_LAYOUT] = { "LAYOUT",
        "VERIFIED: DDI 0487 D8.13.1 QXCQP; DDI 0601 TCR_EL2, TTBR0_EL2, CNTHCTL_EL2, CPTR_EL2 have different layouts for "
        "E2H 0 and 1" },
    [EL2M_E_IMAGE_INVALID] = { "IMAGE_INVALID",
        "VERIFIED field positions: DDI 0601 TCR_EL2 (RES1 bits 31 and 23 when E2H is 0), CPTR_EL2, CNTHCTL_EL2, TTBR0_EL2 "
        "(ASID is RES0 unless E2H is 1)" },
    [EL2M_E_CONFIG_WRITE_MMU_ON] = { "CONFIG_WRITE_MMU_ON",
        "INFERRED (conservative): DDI 0601 TCR_EL2 and MAIR_EL2 'permitted to be cached in a TLB'; DDI 0487 K1.2.4" },
    [EL2M_E_MMU_TOGGLE_NOT_IDENTITY] = { "MMU_TOGGLE_NOT_IDENTITY",
        "VERIFIED recommendation, enforced: DDI 0487 D8.1.4 RGGJW (use PA == VA when enabling or disabling a stage)" },
    [EL2M_E_MMU_ON_STALE_TLB] = { "MMU_ON_STALE_TLB",
        "VERIFIED: DDI 0487 D8.16.1 BQCDZ (TLB state unknown after reset), D8.13.1 STSYY (E2H cached in a TLB, TLBI ALLE2 "
        "expected), D8.17.8 BLDZX (complete after DSB and a context synchronization event)" },
    [EL2M_E_MMU_ON_UNSYNC] = { "MMU_ON_UNSYNC",
        "VERIFIED: DDI 0487 K1.2.4 (an unsynchronized System register change is independently old or new at each use)" },
    [EL2M_E_TLBI_UNSYNC_HCR] = { "TLBI_UNSYNC_HCR",
        "INFERRED (conservative): K1.2.4; C5.5.4 says TLBI ALLE2 covers both EL2 and EL2&0 entries, so the scope may not "
        "depend on E2H" },
    [EL2M_E_DATA_ACCESS_MMU_OFF] = { "DATA_ACCESS_MMU_OFF",
        "VERIFIED attribute, INFERRED hazard: DDI 0487 D8.2.12.1 WFZPW (data accesses with stage 1 off are Device-nGnRnE); "
        "slbounce src/bounce_main.c says the EL2 switch loses cache lines not flushed to RAM" },
    [EL2M_E_USE_BEFORE_SYNC] = { "USE_BEFORE_SYNC",
        "VERIFIED: DDI 0487 K1.2.4 (use before context synchronization sees the old or the new value)" },
    [EL2M_E_FINAL_STATE] = { "FINAL_STATE", "model: the sequence's declared goal" },
    [EL2M_E_CPU_DIVERGE] = { "CPU_DIVERGE",
        "INFERRED: the monitor runs one EL2 regime on every CPU; probes/secondary.S reuses the primary's TCR, TTBR0, MAIR" },
    [EL2M_E_BAD_OP] = { "BAD_OP", "model: malformed operation" },
};

const char *el2m_status_name(enum el2m_status s)
{
    return (unsigned)s < EL2M_STATUS_COUNT && rules[s].name ? rules[s].name : "UNKNOWN_STATUS";
}

const char *el2m_rule_citation(enum el2m_status s)
{
    return (unsigned)s < EL2M_STATUS_COUNT ? rules[s].cite : 0;
}

/* ---- VHE-native track ---------------------------------------------------- */

/* DDI 0487 M.d D8.13.2 Table D8-113 (EL1 names redirected to EL2 when E2H is 1
 * at EL2), D8.13.3 Table D8-118 (the _EL12 aliases), Table D8-116 for ELR and
 * SPSR, and Tables D8-114 and D8-115 for the EL0 timers (NS = 1 shown). */
static const char *const redirected[] = {
    "ACTLRMASK", "AFSR0", "AFSR1", "AMAIR", "AMAIR2", "BRBCR", "CPACRMASK", "CNTKCTL", "CONTEXTIDR",
    "CPACR", "ESR", "FAR", "GCSCR", "GCSPR", "MAIR", "MAIR2", "MPAM1", "MPAMBW1", "PFAR", "PIR", "PIRE0",
    "PMSCR", "POR", "SCTLR", "SCTLR2", "SCTLRMASK", "SCTLR2MASK", "SCXTNUM", "SMCR", "SPMACCESSR", "TCR",
    "TCR2", "TCRMASK", "TCR2MASK", "TFSR", "TRCITECR", "TRFCR", "TTBR0", "TTBR1", "VBAR", "ZCR", "ELR", "SPSR",
};
static const char *const el0_timers[] = {
    "CNTP_CTL", "CNTP_CVAL", "CNTP_TVAL", "CNTV_CTL", "CNTV_CVAL", "CNTV_TVAL",
};
/* Registers a hypervisor commonly touches that are not redirected. */
static const char *const plain[] = {
    "PAR", "MDSCR", "TPIDR", "SP", "CSSELR", "CNTFRQ", "CNTVCT", "CNTPCT", "MPIDR", "MIDR", "VMPIDR", "VPIDR",
    "HCR", "VTTBR", "VTCR", "CNTVOFF", "CNTHCTL", "CPTR", "CNTHP_CTL", "CNTHP_CVAL", "CNTHV_CTL", "CNTHV_CVAL",
};

/* No libc: the monitor is freestanding, so a model that is wired in later must not drag in string.h. */
static int same_name(const char *a, const char *b)
{
    while (*a && *a == *b) { a++; b++; }
    return *a == *b;
}

static int listed(const char *const *list, size_t n, const char *reg)
{
    for (size_t i = 0; i < n; i++)
        if (same_name(list[i], reg)) return 1;
    return 0;
}

int el2m_redirect_class(const char *reg)
{
    if (!reg) return -1;
    if (listed(redirected, sizeof redirected / sizeof *redirected, reg)) return 1;
    if (listed(el0_timers, sizeof el0_timers / sizeof *el0_timers, reg)) return 2;
    if (listed(plain, sizeof plain / sizeof *plain, reg)) return 0;
    return -1;
}

enum el2m_acc_status el2m_check_accesses(unsigned e2h, const struct el2m_acc *acc, size_t n, size_t *fail_index)
{
    size_t scratch;
    if (!fail_index) fail_index = &scratch;
    for (size_t i = 0; i < n; i++) {
        const struct el2m_acc *a = &acc[i];
        const int cls = el2m_redirect_class(a->reg);
        *fail_index = i;
        if (cls < 0) return EL2M_ACC_UNKNOWN_REGISTER;
        const int alias = a->name == EL2M_NAME_EL12 || a->name == EL2M_NAME_EL02;
        if (alias && (!e2h || cls == 0 || (a->name == EL2M_NAME_EL12 && cls == 2) ||
                      (a->name == EL2M_NAME_EL02 && cls == 1)))
            return EL2M_ACC_ALIAS_UNDEFINED;
        if (a->intent == EL2M_INTENT_GUEST && e2h && cls != 0 &&
            (a->name == EL2M_NAME_EL1 || a->name == EL2M_NAME_EL0))
            return EL2M_ACC_REDIRECTED_TO_HOST;
        /* Two names for one register are not ordered against each other
         * without an ISB (DDI 0601 TCR_EL2, CNTHCTL_EL2, accessing sections). */
        if (e2h && i && !a->isb_before && same_name(acc[i - 1].reg, a->reg) && acc[i - 1].name != a->name)
            return EL2M_ACC_UNORDERED_NAMES;
    }
    *fail_index = n;
    return EL2M_ACC_OK;
}

#else
typedef int snap_el2_entry_model_gate_off;
#endif /* SNAP_PLATFORM_SP11 */
