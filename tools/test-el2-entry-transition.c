/* SPDX-License-Identifier: MIT */
/* compat/el2-entry-model.{h,c}, natively: the canonical de-VHE sequences, the
 * arrival matrix (boot CPU after slbounce, PSCI secondaries in every E2H and
 * TGE state), deliberately broken sequences that must each be refused for the
 * stated reason, replicas of two sequences known to run on real hardware, the
 * register image conversions, the rule table, and the VHE redirection model.
 *
 *   cc -std=gnu11 -O1 -g -Wall -Wextra -Werror -fsanitize=address,undefined \
 *      -DSNAP_PLATFORM_SP11=1 -Icompat tools/test-el2-entry-transition.c \
 *      compat/el2-entry-model.c -o build/test-el2-entry-transition
 *
 * With --stdin it reads one case per line and prints the model's verdict, for
 * the differential test against the independent oracle in the Python driver. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "el2-entry-model.h"

static unsigned long checks;
#define CHECK(x) do { checks++; if (!(x)) { \
    fprintf(stderr, "%s:%d: CHECK(%s) failed\n", __FILE__, __LINE__, #x); exit(1); } } while (0)

static const struct el2m_platform PLAT = { 1 };
static const struct el2m_platform PLAT_NO_E2H0 = { 0 };

struct buf { struct el2m_op ops[48]; size_t n; };
static void load(struct buf *b, const struct el2m_seq *s)
{
    CHECK(s->n <= sizeof b->ops / sizeof b->ops[0]);
    memcpy(b->ops, s->ops, s->n * sizeof *s->ops);
    b->n = s->n;
}
static void del(struct buf *b, size_t i)
{
    CHECK(i < b->n);
    memmove(&b->ops[i], &b->ops[i + 1], (b->n - i - 1) * sizeof b->ops[0]);
    b->n--;
}
static void ins(struct buf *b, size_t i, struct el2m_op op)
{
    CHECK(i <= b->n && b->n < sizeof b->ops / sizeof b->ops[0]);
    memmove(&b->ops[i + 1], &b->ops[i], (b->n - i) * sizeof b->ops[0]);
    b->ops[i] = op;
    b->n++;
}
static struct el2m_op msr(enum el2m_reg r, uint64_t v, enum el2m_layout l)
{
    return (struct el2m_op){ EL2M_OP_MSR, (uint8_t)r, (uint8_t)l, v };
}
static struct el2m_op bare(enum el2m_op_kind k) { return (struct el2m_op){ (uint8_t)k, 0, 0, 0 }; }

struct arrival { unsigned mmu, e2h, tge, identity; };
static const struct arrival BOOT = { 1, 1, 1, 1 };     /* slbounce return */
static const struct arrival SEC_E0T0 = { 0, 0, 0, 1 };
static const struct arrival SEC_E0T1 = { 0, 0, 1, 1 };
static const struct arrival SEC_E1T0 = { 0, 1, 0, 1 };
static const struct arrival SEC_E1T1 = { 0, 1, 1, 1 };

static enum el2m_status run_buf(const struct el2m_platform *plat, const struct el2m_policy *pol,
                                const struct el2m_seq *tmpl, const struct buf *b, struct arrival a,
                                size_t *fail, struct el2m_cpu *out)
{
    struct el2m_seq s = *tmpl;
    s.ops = b->ops;
    s.n = b->n;
    struct el2m_cpu c;
    el2m_cpu_arrive(&c, plat, a.mmu ? EL2M_ROLE_BOOT : EL2M_ROLE_SECONDARY, 0, a.mmu, a.e2h, a.tge, a.identity);
    enum el2m_status st = el2m_run(&c, plat, pol, &s, fail);
    if (out) *out = c;
    return st;
}

static enum el2m_status run_seq(const struct el2m_platform *plat, const struct el2m_policy *pol,
                                const struct el2m_seq *s, struct arrival a, size_t *fail)
{
    struct buf b;
    load(&b, s);
    return run_buf(plat, pol, s, &b, a, fail, 0);
}

static const struct el2m_policy STRICT = { 1, 1 };

/* --- canonical sequences, accepted from every arrival they claim --------------- */
static void test_canonical(void)
{
    size_t f;
    CHECK(run_seq(&PLAT, &STRICT, &el2m_seq_boot_devhe, BOOT, &f) == EL2M_OK);
    const struct arrival sec[] = { SEC_E0T0, SEC_E0T1, SEC_E1T0, SEC_E1T1 };
    for (unsigned i = 0; i < 4; i++) {
        CHECK(run_seq(&PLAT, &STRICT, &el2m_seq_secondary_devhe, sec[i], &f) == EL2M_OK);
        CHECK(run_seq(&PLAT, &STRICT, &el2m_seq_universal_devhe, sec[i], &f) == EL2M_OK);
        /* The boot sequence assumes the slbounce state: any secondary arrival is a mismatch. */
        CHECK(run_seq(&PLAT, &STRICT, &el2m_seq_boot_devhe, sec[i], &f) == EL2M_E_ARRIVAL_STATE);
        CHECK(f == el2m_seq_boot_devhe.n);
    }
    CHECK(run_seq(&PLAT, &STRICT, &el2m_seq_universal_devhe, BOOT, &f) == EL2M_OK);
    /* A secondary sequence assumes the MMU is off: the boot CPU's state is a mismatch. */
    CHECK(run_seq(&PLAT, &STRICT, &el2m_seq_secondary_devhe, BOOT, &f) == EL2M_E_ARRIVAL_STATE);
    /* The universal sequence also works from the A16's own state (E2H clear, TGE set, MMU on). */
    CHECK(run_seq(&PLAT, &STRICT, &el2m_seq_universal_devhe, (struct arrival){ 1, 0, 1, 1 }, &f) == EL2M_OK);
}

/* After de-VHE the end state is the one q1n1 sees on the A16 (HCR_EL2 0x88000010). */
static void test_end_state(void)
{
    struct el2m_cpu c;
    size_t f;
    struct buf b;
    load(&b, &el2m_seq_boot_devhe);
    CHECK(run_buf(&PLAT, &STRICT, &el2m_seq_boot_devhe, &b, BOOT, &f, &c) == EL2M_OK);
    CHECK(c.reg[EL2M_HCR] == EL2M_HCR_A16_Q1N1);
    CHECK(c.reg[EL2M_SCTLR] == EL2M_CANON_SCTLR_ON);
    CHECK(c.reg[EL2M_TCR] == EL2M_CANON_TCR_NVHE && c.layout[EL2M_TCR] == EL2M_NVHE);
    CHECK(c.pending_isb == 0 && c.tlb_dirty == 0 && c.tlbi_state == 3);
    CHECK(el2m_image_valid(EL2M_TCR, EL2M_NVHE, c.reg[EL2M_TCR]));
}

/* --- deliberately broken sequences ------------------------------------------- */
/* boot_devhe indices: 0 SCTLR=0, 1 ISB, 2 HCR, 3 ISB, 4 MAIR, 5 TCR, 6 TTBR0, 7 CNTHCTL, 8 CPTR,
 * 9 DSB, 10 TLBI, 11 DSB, 12 ISB, 13 SCTLR on, 14 ISB. */
struct variant {
    const char *name;
    enum el2m_status expect;
};
static unsigned variants_run;

#define EXPECT(name_, status_, ...) do { \
    struct buf b; load(&b, &el2m_seq_boot_devhe); \
    struct arrival arr = BOOT; const struct el2m_platform *plat = &PLAT; \
    struct el2m_policy pol = STRICT; (void)arr; (void)plat; (void)pol; \
    __VA_ARGS__; \
    size_t f; \
    enum el2m_status st = run_buf(plat, &pol, &el2m_seq_boot_devhe, &b, arr, &f, 0); \
    if (st != (status_)) { fprintf(stderr, "variant %s: got %s, want %s\n", name_, el2m_status_name(st), \
                                    el2m_status_name(status_)); exit(1); } \
    checks++; variants_run++; } while (0)

static void test_broken_variants(void)
{
    /* 1. E2H cleared with the MMU on: the headline negative case. */
    EXPECT("e2h-cleared-mmu-on", EL2M_E_E2H_CHANGE_MMU_ON, del(&b, 1); del(&b, 0));
    /* 2. TCR written in the VHE layout after E2H was cleared. */
    EXPECT("tcr-vhe-layout-after-devhe", EL2M_E_LAYOUT, b.ops[5] = msr(EL2M_TCR, EL2M_TEST_TCR_VHE, EL2M_VHE));
    /* 3. The VHE image tagged as non-VHE: IPS and TG1 land in reserved bits. */
    EXPECT("tcr-vhe-image-tagged-nvhe", EL2M_E_IMAGE_INVALID,
           b.ops[5] = msr(EL2M_TCR, EL2M_TEST_TCR_VHE, EL2M_NVHE));
    /* 4. Non-VHE image without its RES1 bits. */
    EXPECT("tcr-nvhe-missing-res1", EL2M_E_IMAGE_INVALID,
           b.ops[5] = msr(EL2M_TCR, EL2M_CANON_TCR_NVHE & ~UINT64_C(0x80800000), EL2M_NVHE));
    /* 5. MMU enabled before TLBI and its barriers. */
    EXPECT("mmu-on-no-tlbi", EL2M_E_MMU_ON_STALE_TLB, del(&b, 11); del(&b, 10); del(&b, 9));
    /* 6. TLBI never completed by a DSB. */
    EXPECT("mmu-on-tlbi-no-dsb", EL2M_E_MMU_ON_STALE_TLB, del(&b, 11));
    /* 7. A missing isb before the MMU is enabled. */
    EXPECT("mmu-on-no-isb", EL2M_E_MMU_ON_UNSYNC, del(&b, 12));
    /* 8. A missing isb after the HCR_EL2 write, before TLBI ALLE2. */
    EXPECT("tlbi-before-hcr-isb", EL2M_E_TLBI_UNSYNC_HCR, del(&b, 3));
    /* 9. TTBR0 programmed again while the MMU is on and a stale TLB can be used. */
    EXPECT("ttbr0-with-mmu-on", EL2M_E_CONFIG_WRITE_MMU_ON,
           ins(&b, 15, msr(EL2M_TTBR0, EL2M_CANON_TTBR0, EL2M_NVHE)));
    /* 10. The boot sequence on a PSCI secondary that arrives with the MMU off. */
    EXPECT("secondary-mmu-off-boot-sequence", EL2M_E_ARRIVAL_STATE, arr = SEC_E1T1);
    /* 11. The boot sequence on a secondary that arrives with E2H clear. */
    EXPECT("secondary-e2h-clear-boot-assumed-vhe", EL2M_E_ARRIVAL_STATE, arr = SEC_E0T1);
    /* 12. MMU toggled while the executing code is not identity mapped. */
    EXPECT("mmu-off-not-identity", EL2M_E_MMU_TOGGLE_NOT_IDENTITY, arr.identity = 0);
    /* 13. A load or store between MMU off and MMU on. */
    EXPECT("data-access-mmu-off", EL2M_E_DATA_ACCESS_MMU_OFF, ins(&b, 2, bare(EL2M_OP_DATA)));
    /* 14. CPTR_EL2 left in the VHE layout. */
    EXPECT("cptr-left-vhe", EL2M_E_LAYOUT, del(&b, 8));
    /* 15. CNTHCTL_EL2 written in the VHE layout. */
    EXPECT("cnthctl-vhe-layout", EL2M_E_LAYOUT, b.ops[7] = msr(EL2M_CNTHCTL, 0x3, EL2M_VHE));
    /* 16. TTBR0_EL2 with an ASID in the non-VHE layout, where it is RES0. */
    EXPECT("ttbr0-asid-nvhe", EL2M_E_IMAGE_INVALID,
           b.ops[6] = msr(EL2M_TTBR0, EL2M_CANON_TTBR0 | (UINT64_C(1) << 48), EL2M_NVHE));
    /* 17. E2H cannot be cleared on a CPU without FEAT_E2H0. */
    EXPECT("e2h-not-programmable", EL2M_E_E2H_NOT_PROGRAMMABLE, plat = &PLAT_NO_E2H0);
    /* 18. HCR_EL2 written after TCR_EL2: the TCR image meets the VHE layout. */
    EXPECT("hcr-after-tcr", EL2M_E_LAYOUT, { struct el2m_op h = b.ops[2]; del(&b, 3); del(&b, 2); ins(&b, 5, h); });
    /* 19. An empty sequence leaves E2H set. */
    EXPECT("empty-sequence", EL2M_E_FINAL_STATE, b.n = 0);
    /* 20. No isb after the final SCTLR_EL2 write. */
    EXPECT("no-final-isb", EL2M_E_USE_BEFORE_SYNC, del(&b, 14));
    /* 21. A data access before the isb that follows SCTLR_EL2. */
    EXPECT("data-before-isb", EL2M_E_USE_BEFORE_SYNC, ins(&b, 14, bare(EL2M_OP_DATA)));
    /* 22. A register index that does not exist. */
    EXPECT("bad-register", EL2M_E_BAD_OP, b.ops[4] = msr((enum el2m_reg)99, 0, 0));
    /* 23. MAIR_EL2 written with the MMU on. */
    EXPECT("mair-with-mmu-on", EL2M_E_CONFIG_WRITE_MMU_ON,
           { struct el2m_op m = b.ops[4]; del(&b, 4); ins(&b, 13, m); });
    /* 24. The MMU never comes back on. */
    EXPECT("mmu-left-off", EL2M_E_FINAL_STATE, del(&b, 14); del(&b, 13));
    CHECK(variants_run >= 12);
    /* 25. A TLBI that is issued and never completed, on a CPU that already has the goal. */
    {
        struct buf b = { { bare(EL2M_OP_TLBI_ALLE2) }, 1 };
        size_t f;
        CHECK(run_buf(&PLAT, &STRICT, &el2m_seq_universal_devhe, &b, (struct arrival){ 1, 0, 1, 1 }, &f, 0) ==
              EL2M_E_FINAL_STATE && f == 1);
        b.ops[1] = bare(EL2M_OP_DSB);
        b.n = 2;
        CHECK(run_buf(&PLAT, &STRICT, &el2m_seq_universal_devhe, &b, (struct arrival){ 1, 0, 1, 1 }, &f, 0) ==
              EL2M_E_FINAL_STATE);
        b.ops[2] = bare(EL2M_OP_ISB);
        b.n = 3;
        CHECK(run_buf(&PLAT, &STRICT, &el2m_seq_universal_devhe, &b, (struct arrival){ 1, 0, 1, 1 }, &f, 0) == EL2M_OK);
    }
}

/* --- reference sequences from source: accepted only with a stated relaxation ------ */
static void test_references(void)
{
    size_t f;
    const struct el2m_policy relaxed_cfg = { 0, 1 };       /* a clean TLB stays clean while M = 0 */
    const struct el2m_policy relaxed_isb = { 1, 0 };       /* TLBI ALLE2 may follow an unsynchronized HCR write */
    const struct arrival tcb = { 1, 0, 0, 1 };             /* tcblaunch's EL2: E2H assumed clear */
    /* slbounce tb_longjmp enters VHE with TLBI before the register writes and none after. */
    CHECK(run_seq(&PLAT, &relaxed_cfg, &el2m_ref_slbounce_vhe, tcb, &f) == EL2M_OK);
    CHECK(run_seq(&PLAT, &STRICT, &el2m_ref_slbounce_vhe, tcb, &f) == EL2M_E_MMU_ON_STALE_TLB);
    /* The A16's secondary_entry: HCR_EL2 write then TLBI with no isb between. */
    CHECK(run_seq(&PLAT, &relaxed_isb, &el2m_ref_a16_secondary, SEC_E0T0, &f) == EL2M_OK);
    CHECK(run_seq(&PLAT, &STRICT, &el2m_ref_a16_secondary, SEC_E0T0, &f) == EL2M_E_TLBI_UNSYNC_HCR);
    /* Arriving with E2H set, it leaves CNTHCTL_EL2 and CPTR_EL2 in the wrong layout: a
     * secondary on the SP11 needs the later writes in cpu_boot.c before anything reads them. */
    CHECK(run_seq(&PLAT, &relaxed_isb, &el2m_ref_a16_secondary, SEC_E1T1, &f) == EL2M_E_LAYOUT);
    /* Mutating a reference must break it. */
    struct buf b;
    load(&b, &el2m_ref_slbounce_vhe);
    del(&b, 2); del(&b, 2); del(&b, 2);                    /* no TLBI at all */
    CHECK(run_buf(&PLAT, &relaxed_cfg, &el2m_ref_slbounce_vhe, &b, tcb, &f, 0) == EL2M_E_MMU_ON_STALE_TLB);
    load(&b, &el2m_ref_slbounce_vhe);
    del(&b, 19);                                           /* the isb that completes the last TLBI */
    CHECK(run_buf(&PLAT, &relaxed_cfg, &el2m_ref_slbounce_vhe, &b, tcb, &f, 0) == EL2M_E_MMU_ON_STALE_TLB);
    load(&b, &el2m_ref_a16_secondary);
    del(&b, 7);                                            /* the dsb that completes the TLBI */
    CHECK(run_buf(&PLAT, &relaxed_isb, &el2m_ref_a16_secondary, &b, SEC_E0T0, &f, 0) == EL2M_E_MMU_ON_STALE_TLB);

    /* The SP11 fork of slbounce's restore (tb_entry has the MMU off already, trans.s 27-31): clean under the strict
     * policy from every arrival, because its second TLBI follows the register writes. */
    const struct arrival any[] = { tcb, BOOT, SEC_E0T0, SEC_E1T1, { 1, 1, 0, 1 } };
    for (unsigned i = 0; i < sizeof any / sizeof any[0]; i++)
        CHECK(run_seq(&PLAT, &STRICT, &el2m_seq_slbounce_devhe, any[i], &f) == EL2M_OK);
    /* Without that second TLBI it is slbounce's own order: refused when strict, accepted when relaxed. */
    load(&b, &el2m_seq_slbounce_devhe);
    del(&b, 15);
    CHECK(run_buf(&PLAT, &STRICT, &el2m_seq_slbounce_devhe, &b, tcb, &f, 0) == EL2M_E_MMU_ON_STALE_TLB);
    CHECK(run_buf(&PLAT, &relaxed_cfg, &el2m_seq_slbounce_devhe, &b, tcb, &f, 0) == EL2M_OK);
    /* Leaving the HCR_EL2 write as slbounce makes it (TGE and E2H) ends in the VHE regime, not the goal. */
    load(&b, &el2m_seq_slbounce_devhe);
    b.ops[7] = msr(EL2M_HCR, EL2M_HCR_SLBOUNCE, 0);
    CHECK(run_buf(&PLAT, &STRICT, &el2m_seq_slbounce_devhe, &b, tcb, &f, 0) == EL2M_E_LAYOUT);
    /* E2H cannot be cleared where it is RES1, wherever the clear is placed. */
    CHECK(run_seq(&PLAT_NO_E2H0, &STRICT, &el2m_seq_slbounce_devhe, tcb, &f) == EL2M_E_E2H_NOT_PROGRAMMABLE);
    /* A boot CPU converted this way ends where a PSCI secondary running the canonical secondary sequence ends. */
    struct el2m_cpu boot, sec;
    load(&b, &el2m_seq_slbounce_devhe);
    CHECK(run_buf(&PLAT, &STRICT, &el2m_seq_slbounce_devhe, &b, tcb, &f, &boot) == EL2M_OK);
    load(&b, &el2m_seq_secondary_devhe);
    CHECK(run_buf(&PLAT, &STRICT, &el2m_seq_secondary_devhe, &b, SEC_E1T1, &f, &sec) == EL2M_OK);
    struct el2m_cpu pair[2] = { boot, sec };
    CHECK(el2m_cpus_consistent(pair, 2) == EL2M_OK);
}

/* --- two CPUs ---------------------------------------------------------------- */
static void test_cpus(void)
{
    size_t f;
    struct el2m_cpu boot, sec;
    struct buf b;
    load(&b, &el2m_seq_boot_devhe);
    CHECK(run_buf(&PLAT, &STRICT, &el2m_seq_boot_devhe, &b, BOOT, &f, &boot) == EL2M_OK);
    load(&b, &el2m_seq_secondary_devhe);
    CHECK(run_buf(&PLAT, &STRICT, &el2m_seq_secondary_devhe, &b, SEC_E1T1, &f, &sec) == EL2M_OK);
    struct el2m_cpu both[2] = { boot, sec };
    CHECK(el2m_cpus_consistent(both, 2) == EL2M_OK);
    /* A secondary that kept E2H set, or built a different regime, diverges. */
    both[1].reg[EL2M_HCR] |= EL2M_HCR_E2H;
    CHECK(el2m_cpus_consistent(both, 2) == EL2M_E_CPU_DIVERGE);
    both[1] = sec;
    both[1].reg[EL2M_TCR] ^= 1;
    CHECK(el2m_cpus_consistent(both, 2) == EL2M_E_CPU_DIVERGE);
    both[1] = sec;
    both[1].layout[EL2M_CPTR] = EL2M_VHE;
    CHECK(el2m_cpus_consistent(both, 2) == EL2M_E_CPU_DIVERGE);
    /* Twelve CPUs, one of them different. */
    struct el2m_cpu twelve[12];
    for (unsigned i = 0; i < 12; i++) twelve[i] = i ? sec : boot;
    CHECK(el2m_cpus_consistent(twelve, 12) == EL2M_OK);
    twelve[11].reg[EL2M_MAIR] ^= 0x100;
    CHECK(el2m_cpus_consistent(twelve, 12) == EL2M_E_CPU_DIVERGE);
}

/* --- images and conversions --------------------------------------------------- */
static void test_images(void)
{
    uint64_t o;
    CHECK(el2m_tcr_vhe_to_nvhe(EL2M_TEST_TCR_VHE, &o) == 0 && o == EL2M_CANON_TCR_NVHE);
    CHECK(el2m_image_valid(EL2M_TCR, EL2M_NVHE, o));
    CHECK(el2m_image_valid(EL2M_TCR, EL2M_VHE, EL2M_TEST_TCR_VHE));
    /* A VHE image read as non-VHE is invalid; a non-VHE image read as VHE has TG1 = 0b10 only by accident. */
    CHECK(!el2m_image_valid(EL2M_TCR, EL2M_NVHE, EL2M_TEST_TCR_VHE));
    /* EPD0 set: TTBR0 walks are off, nothing to carry over. */
    CHECK(el2m_tcr_vhe_to_nvhe(EL2M_TEST_TCR_VHE | 0x80, &o) == -1);
    CHECK(el2m_tcr_vhe_to_nvhe(EL2M_TEST_TCR_VHE | (UINT64_C(1) << 62), &o) == -1);      /* RES0 */
    CHECK(el2m_tcr_vhe_to_nvhe(EL2M_TEST_TCR_VHE & ~UINT64_C(0xc0000000), &o) == -1);    /* TG1 reserved */
    /* Every field the conversion carries lands where the non-VHE layout puts it. */
    const struct { unsigned from, to, width; } map[] = {
        { 0, 0, 6 }, { 8, 8, 8 }, { 32, 16, 3 }, { 37, 20, 1 }, { 39, 21, 1 }, { 40, 22, 1 },
        { 41, 24, 1 }, { 43, 25, 4 }, { 51, 29, 1 }, { 57, 30, 1 }, { 59, 32, 1 }, { 60, 33, 1 },
    };
    for (unsigned i = 0; i < sizeof map / sizeof map[0]; i++) {
        const uint64_t mask = (UINT64_C(1) << map[i].width) - 1;
        for (uint64_t v = 0; v <= mask; v++) {
            uint64_t vhe = (EL2M_TEST_TCR_VHE & ~(mask << map[i].from)) | (v << map[i].from);
            uint64_t nv;
            if (el2m_tcr_vhe_to_nvhe(vhe, &nv)) continue;       /* TG1 or EPD0 made it unconvertible */
            CHECK(((nv >> map[i].to) & mask) == v);
            CHECK(el2m_image_valid(EL2M_TCR, EL2M_NVHE, nv));
        }
    }
    CHECK(el2m_ttbr0_vhe_to_nvhe(UINT64_C(0xabcd000080001001)) == UINT64_C(0x80001001));
    CHECK(el2m_image_valid(EL2M_TTBR0, EL2M_VHE, UINT64_C(0xabcd000080001001)));
    CHECK(!el2m_image_valid(EL2M_TTBR0, EL2M_NVHE, UINT64_C(0xabcd000080001001)));
    /* CNTHCTL_EL2: the physical counter control moves from bit 0 to bit 10. */
    CHECK(el2m_cnthctl_convert(EL2M_NVHE, EL2M_VHE, 0x6000, &o) == 0 && o == 0x6303);
    CHECK(el2m_cnthctl_convert(EL2M_NVHE, EL2M_VHE, 0x6003, &o) == 0 && o == 0x6f03);
    CHECK(el2m_cnthctl_convert(EL2M_VHE, EL2M_NVHE, 0x6c00, &o) == 0 && o == 0x6003);
    CHECK(el2m_cnthctl_convert(EL2M_VHE, EL2M_NVHE, 0x6303, &o) == 0 && o == 0x6000);
    CHECK(el2m_cnthctl_convert(EL2M_NVHE, EL2M_VHE, 0x100, &o) == -1);      /* bit 8 is RES0 */
    CHECK(el2m_cnthctl_convert(EL2M_VHE, EL2M_NVHE, UINT64_C(1) << 40, &o) == -1);
    /* A value copied without conversion changes meaning: 3 means EL1 may read the physical
     * counter and timer in the non-VHE layout, and EL0 may in the VHE layout. */
    CHECK(el2m_cnthctl_convert(EL2M_VHE, EL2M_NVHE, 0x3, &o) == 0 && o == 0);
    /* CPTR_EL2: the non-VHE RES1 pattern is not a VHE image, and conversely. */
    CHECK(el2m_image_valid(EL2M_CPTR, EL2M_NVHE, EL2M_CANON_CPTR));
    CHECK(!el2m_image_valid(EL2M_CPTR, EL2M_VHE, EL2M_CANON_CPTR));
    CHECK(el2m_image_valid(EL2M_CPTR, EL2M_VHE, UINT64_C(0x300000)));
    CHECK(!el2m_image_valid(EL2M_CPTR, EL2M_NVHE, UINT64_C(0x300000)));
    CHECK(!el2m_image_valid(EL2M_CPTR, EL2M_NVHE, EL2M_CANON_CPTR | (UINT64_C(1) << 11)));
    /* Registers without a layout rule are not judged. */
    CHECK(el2m_image_valid(EL2M_MAIR, EL2M_NVHE, ~UINT64_C(0)) && el2m_image_valid(EL2M_HCR, EL2M_VHE, 0));
}

/* --- platform capability and effective E2H ------------------------------------ */
static void test_e2h0(void)
{
    CHECK(el2m_effective_e2h(&PLAT, 0) == 0 && el2m_effective_e2h(&PLAT, EL2M_HCR_E2H) == 1);
    /* RES1 without FEAT_E2H0: a read-back of 0 does not mean E2H is clear. */
    CHECK(el2m_effective_e2h(&PLAT_NO_E2H0, 0) == 1 && el2m_layout_of(&PLAT_NO_E2H0, 0) == EL2M_VHE);
    struct el2m_cpu c;
    el2m_cpu_arrive(&c, &PLAT_NO_E2H0, EL2M_ROLE_BOOT, 0, 1, 0, 1, 1);
    CHECK(el2m_effective_e2h(&PLAT_NO_E2H0, c.reg[EL2M_HCR]) == 1);
    /* On such a CPU only the VHE-native path exists: the slbounce reference is its entry. */
    size_t f;
    CHECK(run_seq(&PLAT_NO_E2H0, &(struct el2m_policy){ 0, 1 }, &el2m_ref_slbounce_vhe, (struct arrival){ 1, 1, 0, 1 }, &f)
          == EL2M_OK);
}

/* --- rule table --------------------------------------------------------------- */
static void test_rules(void)
{
    CHECK(el2m_rule_citation(EL2M_OK) == 0);
    for (unsigned s = 1; s < EL2M_STATUS_COUNT; s++) {
        const char *name = el2m_status_name((enum el2m_status)s), *cite = el2m_rule_citation((enum el2m_status)s);
        CHECK(name && strcmp(name, "UNKNOWN_STATUS") && cite && strlen(cite) > 20);
        CHECK(strstr(cite, "VERIFIED") || strstr(cite, "INFERRED") || strstr(cite, "model:"));
        for (unsigned t = 1; t < s; t++) CHECK(strcmp(name, el2m_status_name((enum el2m_status)t)));
    }
    CHECK(!strcmp(el2m_status_name((enum el2m_status)200), "UNKNOWN_STATUS") && !el2m_rule_citation((enum el2m_status)200));
}

/* --- VHE-native register redirection ------------------------------------------ */
static const char *const EL1_STATE[] = {
    "SCTLR", "TTBR0", "TTBR1", "TCR", "MAIR", "AMAIR", "VBAR", "ESR", "FAR", "AFSR0", "AFSR1",
    "CONTEXTIDR", "CPACR", "CNTKCTL", "ELR", "SPSR",
};

static void test_redirection(void)
{
    CHECK(el2m_redirect_class("SCTLR") == 1 && el2m_redirect_class("CNTKCTL") == 1 && el2m_redirect_class("ELR") == 1);
    CHECK(el2m_redirect_class("CNTV_CTL") == 2 && el2m_redirect_class("PAR") == 0 && el2m_redirect_class("NOPE") == -1);
    CHECK(el2m_redirect_class(0) == -1);
    struct el2m_acc acc[32];
    size_t n = sizeof EL1_STATE / sizeof EL1_STATE[0], f;
    /* A VHE host saving the guest's EL1 state must use the _EL12 names. */
    for (size_t i = 0; i < n; i++) acc[i] = (struct el2m_acc){ EL1_STATE[i], EL2M_NAME_EL12, EL2M_INTENT_GUEST, 1 };
    CHECK(el2m_check_accesses(1, acc, n, &f) == EL2M_ACC_OK && f == n);
    /* The same routine written for a non-VHE host reaches the host's own EL2 registers. */
    for (size_t i = 0; i < n; i++) acc[i].name = EL2M_NAME_EL1;
    CHECK(el2m_check_accesses(1, acc, n, &f) == EL2M_ACC_REDIRECTED_TO_HOST && f == 0);
    CHECK(el2m_check_accesses(0, acc, n, &f) == EL2M_ACC_OK);       /* with E2H clear it is correct */
    for (size_t i = 0; i < n; i++) acc[i].name = EL2M_NAME_EL12;
    CHECK(el2m_check_accesses(0, acc, n, &f) == EL2M_ACC_ALIAS_UNDEFINED && f == 0);
    /* Registers that are not redirected keep their EL1 names, and have no alias. */
    acc[0] = (struct el2m_acc){ "PAR", EL2M_NAME_EL1, EL2M_INTENT_GUEST, 1 };
    CHECK(el2m_check_accesses(1, acc, 1, &f) == EL2M_ACC_OK);
    acc[0].name = EL2M_NAME_EL12;
    CHECK(el2m_check_accesses(1, acc, 1, &f) == EL2M_ACC_ALIAS_UNDEFINED);
    acc[0] = (struct el2m_acc){ "FOO", EL2M_NAME_EL1, EL2M_INTENT_GUEST, 1 };
    CHECK(el2m_check_accesses(1, acc, 1, &f) == EL2M_ACC_UNKNOWN_REGISTER);
    /* EL0 timers: the EL0 name is redirected to CNTHP or CNTHV; the _EL02 alias reaches the guest's. */
    acc[0] = (struct el2m_acc){ "CNTV_CTL", EL2M_NAME_EL0, EL2M_INTENT_GUEST, 1 };
    CHECK(el2m_check_accesses(1, acc, 1, &f) == EL2M_ACC_REDIRECTED_TO_HOST);
    acc[0].name = EL2M_NAME_EL02;
    CHECK(el2m_check_accesses(1, acc, 1, &f) == EL2M_ACC_OK);
    acc[0].name = EL2M_NAME_EL12;
    CHECK(el2m_check_accesses(1, acc, 1, &f) == EL2M_ACC_ALIAS_UNDEFINED);
    acc[0] = (struct el2m_acc){ "SCTLR", EL2M_NAME_EL02, EL2M_INTENT_GUEST, 1 };
    CHECK(el2m_check_accesses(1, acc, 1, &f) == EL2M_ACC_ALIAS_UNDEFINED);
    /* Host intent through EL2 names is always fine. */
    acc[0] = (struct el2m_acc){ "TCR", EL2M_NAME_EL2, EL2M_INTENT_HOST, 0 };
    CHECK(el2m_check_accesses(1, acc, 1, &f) == EL2M_ACC_OK);
    /* One register through two names needs an isb (TCR_EL2 and CNTHCTL_EL2 pages). */
    acc[0] = (struct el2m_acc){ "CNTKCTL", EL2M_NAME_EL1, EL2M_INTENT_HOST, 0 };
    acc[1] = (struct el2m_acc){ "CNTKCTL", EL2M_NAME_EL2, EL2M_INTENT_HOST, 0 };
    CHECK(el2m_check_accesses(1, acc, 2, &f) == EL2M_ACC_UNORDERED_NAMES && f == 1);
    acc[1].isb_before = 1;
    CHECK(el2m_check_accesses(1, acc, 2, &f) == EL2M_ACC_OK);
    CHECK(el2m_check_accesses(0, (acc[1].isb_before = 0, acc), 2, &f) == EL2M_ACC_OK);
}

/* --- gate: this file is only meaningful with the SP11 define --------------------- */
static void test_misc(void)
{
    struct el2m_cpu c;
    el2m_cpu_arrive(&c, &PLAT, EL2M_ROLE_SECONDARY, 7, 0, 1, 0, 0);
    CHECK(c.cpu == 7 && c.code_identity == 1 && c.tlb_dirty == 1 && c.layout[EL2M_TCR] == EL2M_VHE);
    CHECK(el2m_step(&c, &PLAT, &STRICT, &(struct el2m_op){ 77, 0, 0, 0 }) == EL2M_E_BAD_OP);
}

/* --- differential mode: one case per line ------------------------------------------------------
 * e2h0 strict_cfg strict_isb mmu e2h tge identity | pre_mmu pre_e2h pre_tge goal_e2h goal_tge goal_mmu | ops
 * ops are words: M<reg>:<layout>:<hex value>, T, D, I, X. Output: status index, fail index. */
static int parse_op(char *word, struct el2m_op *op)
{
    memset(op, 0, sizeof *op);
    switch (word[0]) {
    case 'T': op->kind = EL2M_OP_TLBI_ALLE2; return 0;
    case 'D': op->kind = EL2M_OP_DSB; return 0;
    case 'I': op->kind = EL2M_OP_ISB; return 0;
    case 'X': op->kind = EL2M_OP_DATA; return 0;
    case 'M': {
        unsigned reg, layout;
        unsigned long long v;
        if (sscanf(word + 1, "%u:%u:%llx", &reg, &layout, &v) != 3) return -1;
        op->kind = EL2M_OP_MSR; op->reg = (uint8_t)reg; op->layout = (uint8_t)layout; op->value = v;
        return 0;
    }
    default: return -1;
    }
}

static int differential(void)
{
    char line[8192];
    while (fgets(line, sizeof line, stdin)) {
        unsigned e2h0, sc, si, mmu, e2h, tge, id;
        int pm, pe, pt, ge, gt, gm;
        int used = 0;
        if (sscanf(line, "%u %u %u %u %u %u %u | %d %d %d %d %d %d |%n", &e2h0, &sc, &si, &mmu, &e2h, &tge,
                   &id, &pm, &pe, &pt, &ge, &gt, &gm, &used) < 13 || !used) { puts("PARSE"); continue; }
        struct el2m_op ops[64];
        size_t n = 0;
        int bad = 0;
        for (char *w = strtok(line + used, " \t\r\n"); w; w = strtok(0, " \t\r\n")) {
            if (n >= 64 || parse_op(w, &ops[n++])) { bad = 1; break; }
        }
        if (bad) { puts("PARSE"); continue; }
        struct el2m_platform plat = { (uint8_t)e2h0 };
        struct el2m_policy pol = { (uint8_t)sc, (uint8_t)si };
        struct el2m_seq seq = { "diff", { (int8_t)pm, (int8_t)pe, (int8_t)pt }, { (uint8_t)ge, (uint8_t)gt, (uint8_t)gm }, ops, n };
        struct el2m_cpu c;
        size_t f;
        el2m_cpu_arrive(&c, &plat, mmu ? EL2M_ROLE_BOOT : EL2M_ROLE_SECONDARY, 0, mmu, e2h, tge, id);
        enum el2m_status st = el2m_run(&c, &plat, &pol, &seq, &f);
        printf("%d %zu\n", (int)st, f);
    }
    return 0;
}

int main(int argc, char **argv)
{
    if (argc > 1 && !strcmp(argv[1], "--stdin")) return differential();
    if (argc > 2 && !strcmp(argv[1], "--class")) { printf("%d\n", el2m_redirect_class(argv[2])); return 0; }
    test_canonical();
    test_end_state();
    test_broken_variants();
    test_references();
    test_cpus();
    test_images();
    test_e2h0();
    test_rules();
    test_redirection();
    test_misc();
    printf("%lu checks, %u broken variants\nPASS\n", checks, variants_run);
    return 0;
}
