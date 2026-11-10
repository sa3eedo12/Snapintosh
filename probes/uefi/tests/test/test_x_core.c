/* SPDX-License-Identifier: MIT */
/* Host tests of the X2 image's pure core: ID decoding, the NV classification
 * and its refusals, the syndrome helpers, the guarded-access policy, the
 * save/restore bookkeeping and the read windows.
 */
#include "xtests.h"

#include "env.h"
#include "guard.h"
#include "regs.h"
#include "sysstate.h"

/* ---- ID decoding and the NV classification ---- */

/* The architecture's table, written out independently of decode.c: NV is
 * ID_AA64MMFR2_EL1[27:24], NV_frac is ID_AA64MMFR4_EL1[23:20]. */
static enum tests_nv_class expected_class(unsigned nv, unsigned frac)
{
    if (nv == 0 && frac == 0)
        return NVC_NONE;
    if (nv == 1 && frac == 0)
        return NVC_NV_ONLY;
    if (nv == 2 && frac == 0)
        return NVC_NV_NV2;
    if (nv == 2 && frac == 1)
        return NVC_NV2_ONLY;
    return NVC_RESERVED;
}

void test_x_ids(void)
{
    tests_idset s;

    section("ids and nv classes");
    x_good_ids(&s);
    CHECK_EQ(tests_id_parange(&s), 5);
    CHECK_EQ(tests_id_vmidbits(&s), 16);
    CHECK_EQ(tests_id_vh(&s), 1);
    CHECK_EQ(tests_id_ecv(&s), 2);
    CHECK_EQ(tests_id_fgt(&s), 1);
    CHECK_EQ(tests_id_gic(&s), 1);
    CHECK_EQ(tests_id_el1(&s), 1);
    CHECK_EQ(tests_id_el2(&s), 1);
    CHECK_EQ(tests_id_tgran16(&s), 1);
    CHECK_EQ(tests_dczid_block_bytes(&s), 64);
    CHECK_STR(tests_tgran4_name(15), "NO");
    CHECK_STR(tests_tgran4_name(0), "YES");
    CHECK_STR(tests_tgran16_name(0), "NO");
    CHECK_STR(tests_tgran16_name(1), "YES");
    CHECK_STR(tests_tgran64_name(15), "NO");
    s.v[TI_DCZID] = 0x14;
    CHECK_EQ(tests_dczid_block_bytes(&s), 0); /* DZP set: prohibited */
    s.have &= ~(1u << TI_DCZID);
    CHECK_EQ(tests_dczid_block_bytes(&s), -1);
    CHECK_EQ(tests_id_parange(&s), 5);
    s.have &= ~(1u << TI_MMFR0);
    CHECK_EQ(tests_id_parange(&s), -1);
    CHECK_EQ(tests_id_ecv(&s), -1);
    CHECK_EQ(tests_bits(0xF0F0, 7, 4), 0xF);
    CHECK_EQU(tests_bits(~0ull, 63, 0), ~0ull);
    CHECK_EQU(tests_bits(0x8000000000000000ull, 63, 63), 1);

    /* Every (NV, NV_frac) pair of the two 4-bit fields. */
    for (unsigned nv = 0; nv < 16; nv++) {
        for (unsigned frac = 0; frac < 16; frac++) {
            x_good_ids(&s);
            x_set_nv(&s, nv, frac);
            CHECK_EQ(tests_nv_classify(&s), expected_class(nv, frac));
        }
    }
    /* A field next to the NV one must not leak into the classification. */
    x_good_ids(&s);
    s.v[TI_MMFR2] |= 0xF0FFFFFFull; /* every bit outside [27:24] */
    s.v[TI_MMFR2] |= 0xF0ull << 56;
    s.v[TI_MMFR4] |= 0xFull | (0xFull << 24);
    CHECK_EQ(tests_nv_classify(&s), NVC_NV_NV2);

    CHECK_STR(tests_nv_class_name(NVC_NV_NV2), "NV+NV2");
    CHECK_STR(tests_ti_name(TI_MMFR2), "ID_AA64MMFR2_EL1");
    CHECK_EQ(tests_ti_sysreg(TI_COUNT), -1);
}

/* The faulty images of the brief: all ones, all zeros and contradictory
 * feature fields must never lead to a GO. */
void test_x_faulty_ids(void)
{
    tests_idset s;
    tests_nv2_obs o;
    const char *why = "(NOT SET)"; /* a sentinel, so a missing reason fails a check instead of crashing */

    section("faulty id images");
    /* All ones in every register. */
    memset(&s, 0xFF, sizeof s);
    s.have = (1u << TI_COUNT) - 1u;
    CHECK(!tests_ids_plausible(&s, &why));
    CHECK_STR(why, "ID VALUE ALL ONES");
    CHECK_EQ(tests_nv_classify(&s), NVC_UNREAD);
    memset(&o, 0, sizeof o);
    CHECK(tests_nv2_decide(&s, &o).verdict != TV_GO);
    CHECK_EQ(tests_nv2_decide(&s, &o).verdict, TV_INCONCLUSIVE);
    /* ... even if the observations claim everything worked. */
    o.attempted = o.prepared = o.restore_ok = 1;
    o.ctl = CTL_TRAPPED_OK;
    o.red = RED_LANDED_OK;
    CHECK(tests_nv2_decide(&s, &o).verdict != TV_GO);

    /* All zeros: MIDR zero, no EL1, no EL2. */
    memset(&s, 0, sizeof s);
    s.have = (1u << TI_COUNT) - 1u;
    CHECK(!tests_ids_plausible(&s, &why));
    CHECK_STR(why, "MIDR IS ZERO");
    CHECK_EQ(tests_nv_classify(&s), NVC_NONE);
    CHECK(tests_nv2_decide(&s, &o).verdict != TV_GO);
    memset(&o, 0, sizeof o);
    CHECK(tests_nv2_decide(&s, &o).verdict != TV_GO);

    /* Nothing read at all. */
    memset(&s, 0, sizeof s);
    CHECK(!tests_ids_plausible(&s, &why));
    CHECK_STR(why, "ID READ FAULTED");
    CHECK_EQ(tests_nv_classify(&s), NVC_UNREAD);

    /* Each register the decision rests on, unread and all ones, one at a time. */
    static const int needed[] = {TI_MIDR, TI_PFR0, TI_MMFR1, TI_MMFR2, TI_MMFR4};
    for (unsigned i = 0; i < sizeof needed / sizeof needed[0]; i++) {
        x_good_ids(&s);
        CHECK(tests_ids_plausible(&s, NULL));
        s.have &= ~(1u << needed[i]);
        CHECK(!tests_ids_plausible(&s, &why));
        x_good_ids(&s);
        s.v[needed[i]] = ~0ull;
        CHECK(!tests_ids_plausible(&s, &why));
        CHECK_STR(why, "ID VALUE ALL ONES");
    }
    /* An EL2 field of zero cannot be read by code running at EL2. */
    x_good_ids(&s);
    s.v[TI_PFR0] &= ~(0xFull << 8);
    CHECK(!tests_ids_plausible(&s, &why));
    CHECK_STR(why, "EL2 FIELD ZERO AT EL2");
    x_good_ids(&s);
    s.v[TI_PFR0] &= ~(0xFull << 4);
    CHECK(!tests_ids_plausible(&s, &why));
    CHECK_STR(why, "NO EL1 IMPLEMENTED");

    /* Contradictory feature fields: NV2 advertised without EL2, or NV_frac with
     * a non-NV2 NV value. The classification says so and no GO comes out even
     * when the (untrustworthy) observations all look perfect. */
    memset(&o, 0, sizeof o);
    o.attempted = o.prepared = o.restore_ok = 1;
    o.ctl = CTL_TRAPPED_OK;
    o.red = RED_LANDED_OK;
    x_good_ids(&s);
    s.v[TI_PFR0] &= ~(0xFull << 8);
    CHECK(tests_nv2_decide(&s, &o).verdict != TV_GO);
    for (unsigned nv = 0; nv < 16; nv++) {
        for (unsigned frac = 0; frac < 16; frac++) {
            x_good_ids(&s);
            x_set_nv(&s, nv, frac);
            CHECK_EQ(tests_nv2_decide(&s, &o).verdict == TV_GO, nv == 2 && frac == 0);
        }
    }
    x_good_ids(&s);
    x_set_nv(&s, 0, 1); /* NV_frac set without NV == 2 */
    CHECK_EQ(tests_nv_classify(&s), NVC_RESERVED);
    CHECK_EQ(tests_nv2_decide(&s, &o).verdict, TV_INCONCLUSIVE);
    x_set_nv(&s, 3, 0); /* a reserved NV value */
    CHECK_EQ(tests_nv2_decide(&s, &o).verdict, TV_INCONCLUSIVE);
}

/* ---- Syndrome helpers and the HCR_EL2 value ---- */

static uint64_t esr_sys64(unsigned op0, unsigned op1, unsigned crn, unsigned crm, unsigned op2,
                          unsigned rt, int read)
{
    return (0x18ull << 26) | (1ull << 25) | ((uint64_t)op0 << 20) | ((uint64_t)op2 << 17) |
           ((uint64_t)op1 << 14) | ((uint64_t)crn << 10) | ((uint64_t)rt << 5) |
           ((uint64_t)crm << 1) | (read ? 1u : 0u);
}

void test_x_syndrome(void)
{
    tests_sys64_iss i;
    uint64_t esr = esr_sys64(3, 4, 13, 0, 2, 1, 1); /* MRS x1, TPIDR_EL2 */

    section("syndrome and hcr");
    CHECK_EQ(tests_esr_ec(esr), TESTS_EC_SYS64);
    CHECK_EQ(tests_esr_ec(0x16ull << 26), TESTS_EC_HVC64);
    CHECK_EQ(tests_esr_ec(0x3Full << 26), 0x3F);
    tests_esr_sys64(esr, &i);
    CHECK_EQ(i.op0, 3);
    CHECK_EQ(i.op1, 4);
    CHECK_EQ(i.crn, 13);
    CHECK_EQ(i.crm, 0);
    CHECK_EQ(i.op2, 2);
    CHECK_EQ(i.rt, 1);
    CHECK_EQ(i.read, 1);
    CHECK(tests_esr_is_sysreg(esr, 3, 4, 13, 0, 2, 1));
    CHECK(!tests_esr_is_sysreg(esr, 3, 4, 13, 0, 2, 0)); /* a write is not this access */
    CHECK(!tests_esr_is_sysreg(esr, 3, 4, 13, 0, 3, 1));
    CHECK(!tests_esr_is_sysreg(esr, 3, 4, 13, 1, 2, 1));
    CHECK(!tests_esr_is_sysreg(esr, 3, 4, 12, 0, 2, 1));
    CHECK(!tests_esr_is_sysreg(esr, 3, 5, 13, 0, 2, 1));
    CHECK(!tests_esr_is_sysreg(esr, 2, 4, 13, 0, 2, 1));
    CHECK(!tests_esr_is_sysreg((esr & ~(0x3Full << 26)) | (0x16ull << 26), 3, 4, 13, 0, 2, 1));
    /* TPIDR_EL2's own encoding in the register table matches the syndrome the
     * control expects, so the control cannot be satisfied by a different register. */
    {
        unsigned e[5];

        CHECK_EQ(tests_sysreg_encoding(TR_TPIDR_EL2, e), 0);
        CHECK(tests_esr_is_sysreg(esr_sys64(e[0], e[1], e[2], e[3], e[4], 1, 1), 3, 4, 13, 0, 2, 1));
        CHECK_EQ(tests_sysreg_encoding(TR_COUNT, e), -1);
    }

    /* HCR_EL2: the firmware's value with only the fields in TESTS_HCR_STUB_CLEAR
     * cleared, RW set, and NV and NV2 as asked; everything else is preserved. */
    CHECK_EQU(tests_hcr_for(0, 0, 0), TESTS_HCR_RW);
    CHECK_EQU(tests_hcr_for(0, 1, 0), TESTS_HCR_RW | TESTS_HCR_NV);
    CHECK_EQU(tests_hcr_for(0, 1, 1), TESTS_HCR_RW | TESTS_HCR_NV | TESTS_HCR_NV2);
    CHECK_EQU(tests_hcr_for(0, 0, 1), TESTS_HCR_RW | TESTS_HCR_NV2);
    CHECK_EQU(tests_hcr_for(TESTS_HCR_E2H, 0, 0), TESTS_HCR_RW | TESTS_HCR_E2H);
    {
        uint64_t all = ~0ull;
        uint64_t h = tests_hcr_for(all, 1, 1);

        /* Every bit the stub must not inherit is cleared... */
        CHECK(!(h & TESTS_HCR_TGE));
        CHECK(!(h & TESTS_HCR_VM));
        CHECK(!(h & TESTS_HCR_NV1));
        CHECK(!(h & TESTS_HCR_AT));
        CHECK(!(h & TESTS_HCR_FMO));
        CHECK(!(h & TESTS_HCR_IMO));
        CHECK(!(h & TESTS_HCR_AMO));
        CHECK(!(h & TESTS_HCR_VF));
        CHECK(!(h & TESTS_HCR_VI));
        CHECK(!(h & TESTS_HCR_VSE));
        CHECK(!(h & TESTS_HCR_HCD));
        /* ...the three it varies or needs are as asked... */
        CHECK(h & TESTS_HCR_RW);
        CHECK(h & TESTS_HCR_NV);
        CHECK(h & TESTS_HCR_NV2);
        CHECK(h & TESTS_HCR_E2H);
        /* ...and the rest of the firmware's value survives untouched. */
        CHECK_EQU(h & ~TESTS_HCR_STUB_CLEAR & ~TESTS_HCR_RW, all & ~TESTS_HCR_STUB_CLEAR & ~TESTS_HCR_RW);
        CHECK_EQU(tests_hcr_for(all, 0, 0) & ~TESTS_HCR_STUB_CLEAR, all & ~TESTS_HCR_STUB_CLEAR);
        /* A bit the firmware set that the stub does not care about, such as TWE or
         * TVM, is preserved; one it must not inherit is not. */
        CHECK(tests_hcr_for((1ull << 14) | (1ull << 26), 0, 0) & (1ull << 14));
        CHECK(tests_hcr_for((1ull << 14) | (1ull << 26), 0, 0) & (1ull << 26));
        CHECK(!(tests_hcr_for(TESTS_HCR_TGE | TESTS_HCR_IMO, 0, 0) & (TESTS_HCR_TGE | TESTS_HCR_IMO)));
    }
    /* The control and the functional run differ in NV2 and nothing else. */
    CHECK_EQU(tests_hcr_for(0x123456789ull, 1, 1) ^ tests_hcr_for(0x123456789ull, 1, 0), TESTS_HCR_NV2);
    CHECK_EQU(TESTS_HCR_NV, 1ull << 42);
    CHECK_EQU(TESTS_HCR_NV2, 1ull << 45);
    CHECK_EQU(TESTS_HCR_E2H, 1ull << 34);
    CHECK_EQU(TESTS_HCR_HCD, 1ull << 29);
    CHECK_EQU(TESTS_HCR_IMO, 1ull << 4);
}

/* ---- The guarded access policy ---- */

typedef struct {
    uint32_t word;
    enum tests_insn_class cls;
    unsigned rt;
    const char *text;
} insn_case;

/* Encodings produced by an independent assembler (LLVM MC) from the quoted
 * source, so the classifier is judged against real instruction words. */
static const insn_case insn_cases[] = {
    {0xB9400441u, INSN_LOAD, 1, "ldr w1, [x2, #4]"},
    {0xF9400083u, INSN_LOAD, 3, "ldr x3, [x4]"},
    {0xF9400483u, INSN_LOAD, 3, "ldr x3, [x4, #8]"},
    {0xB97FFC5Eu, INSN_LOAD, 30, "ldr w30, [x2, #16380]"},
    {0xF940003Fu, INSN_LOAD, 31, "ldr xzr, [x1]"},
    {0xD5380000u, INSN_MRS, 0, "mrs x0, midr_el1"},
    {0xD53FFFE5u, INSN_MRS, 5, "mrs x5, s3_7_c15_c15_7"},
    {0xD5380747u, INSN_MRS, 7, "mrs x7, id_aa64mmfr2_el1"},
    {0xD538001Fu, INSN_MRS, 31, "mrs xzr, midr_el1"},
    {0xD50B7421u, INSN_DCZVA, 1, "dc zva, x1"},
    {0xD50B7422u, INSN_DCZVA, 2, "dc zva, x2"},
    {0xD50B743Fu, INSN_DCZVA, 31, "dc zva, xzr"},
    /* Everything below must be refused: stores, other widths and addressing
     * modes, pair and atomic accesses, system writes, other cache maintenance. */
    {0xB9000041u, INSN_OTHER, 1, "str w1, [x2]"},
    {0xF9000441u, INSN_OTHER, 1, "str x1, [x2, #8]"},
    {0x39400041u, INSN_OTHER, 1, "ldrb w1, [x2]"},
    {0x79400041u, INSN_OTHER, 1, "ldrh w1, [x2]"},
    {0xB9800041u, INSN_OTHER, 1, "ldrsw x1, [x2]"},
    {0xA9400861u, INSN_OTHER, 1, "ldp x1, x2, [x3]"},
    {0xF8636841u, INSN_OTHER, 1, "ldr x1, [x2, x3]"},
    {0xF8408441u, INSN_OTHER, 1, "ldr x1, [x2], #8"},
    {0xF8408C41u, INSN_OTHER, 1, "ldr x1, [x2, #8]!"},
    {0x58000041u, INSN_OTHER, 1, "ldr x1, #8"},
    {0xC8DFFC41u, INSN_OTHER, 1, "ldar x1, [x2]"},
    {0xC85F7C41u, INSN_OTHER, 1, "ldxr x1, [x2]"},
    {0xF8218062u, INSN_OTHER, 2, "swp x1, x2, [x3]"},
    {0xF8210062u, INSN_OTHER, 2, "ldadd x1, x2, [x3]"},
    {0xD51CC000u, INSN_OTHER, 0, "msr vbar_el2, x0"},
    {0xD51C1103u, INSN_OTHER, 3, "msr hcr_el2, x3"},
    {0xD50B7E21u, INSN_OTHER, 1, "dc civac, x1"},
    {0xD50B7A21u, INSN_OTHER, 1, "dc cvac, x1"},
    {0xD5087621u, INSN_OTHER, 1, "dc ivac, x1"},
    {0xD50B7441u, INSN_OTHER, 1, "sys #3, c7, c4, #2, x1"},
    {0x3DC00020u, INSN_OTHER, 0, "ldr q0, [x1]"},
    {0xBD400020u, INSN_OTHER, 0, "ldr s0, [x1]"},
    {0xF9800020u, INSN_OTHER, 0, "prfm pldl1keep, [x1]"},
    {0xF8400841u, INSN_OTHER, 1, "ldtr x1, [x2]"},
    {0xF8401041u, INSN_OTHER, 1, "ldur x1, [x2, #1]"},
    {0xD4000202u, INSN_OTHER, 2, "hvc #0x10"},
    {0xD69F03E0u, INSN_OTHER, 0, "eret"},
    {0xD503201Fu, INSN_OTHER, 31, "nop"},
    {0xD4000001u, INSN_OTHER, 1, "svc #0"},
};

void test_x_guard(void)
{
    tests_guard g;
    struct tests_frame f;
    tests_el1_exit e;

    section("guard policy");
    for (unsigned i = 0; i < sizeof insn_cases / sizeof insn_cases[0]; i++) {
        unsigned rt = 99;
        enum tests_insn_class cls = tests_guard_classify(insn_cases[i].word, &rt);

        if (cls != insn_cases[i].cls)
            printf("  classify mismatch: %s (0x%08X): %d\n", insn_cases[i].text, insn_cases[i].word,
                   (int)cls);
        CHECK_EQ(cls, insn_cases[i].cls);
        CHECK_EQ(rt, insn_cases[i].rt);
    }

    /* No store-class word is ever a LOAD: sweep every size/opc/V combination of
     * the load/store unsigned-immediate group with assorted registers. */
    for (unsigned size = 0; size < 4; size++)
        for (unsigned v = 0; v < 2; v++)
            for (unsigned opc = 0; opc < 4; opc++) {
                uint32_t w = (size << 30) | (7u << 27) | (v << 26) | (1u << 24) | (opc << 22) |
                             (0x123u << 10) | (5u << 5) | 9u;
                unsigned rt;
                enum tests_insn_class cls = tests_guard_classify(w, &rt);
                int is_ldr_wx = (size >= 2) && v == 0 && opc == 1;

                CHECK_EQ(cls == INSN_LOAD, is_ldr_wx);
            }
    /* A word with the MRS pattern but L == 0 is a write. */
    {
        unsigned rt;

        CHECK_EQ(tests_guard_classify(0xD5100000u | (3u << 19), &rt), INSN_OTHER);
    }

    /* A load that faults, armed: skipped, the destination becomes all ones and
     * the link register moves one instruction. */
    memset(&g, 0, sizeof g);
    memset(&f, 0x11, sizeof f);
    f.elr = 0x1000;
    g.armed = 1;
    CHECK_EQ(tests_guard_on_sync(&g, &f, 0x25ull << 26, 0xDEAD0000ull, 0xB9400441u), GUARD_SKIPPED);
    CHECK_EQU(f.x[1], ~0ull);
    CHECK_EQU(f.x[0], 0x1111111111111111ull);
    CHECK_EQU(f.x[2], 0x1111111111111111ull);
    CHECK_EQU(f.elr, 0x1004);
    CHECK_EQ(g.faults, 1);
    CHECK_EQU(g.last_far, 0xDEAD0000ull);
    CHECK_EQU(g.last_esr, 0x25ull << 26);
    CHECK_EQ(g.logged, 1);

    /* Destination xzr: nothing is written and the frame stays intact. */
    memset(&f, 0x22, sizeof f);
    f.elr = 0x2000;
    f.pad = 0x77;
    CHECK_EQ(tests_guard_on_sync(&g, &f, 0x25ull << 26, 0, 0xF940003Fu), GUARD_SKIPPED);
    CHECK_EQU(f.elr, 0x2004); /* x[31] would alias elr if rt 31 were written */
    CHECK_EQU(f.pad, 0x77);
    for (unsigned i = 0; i < 31; i++)
        CHECK_EQU(f.x[i], 0x2222222222222222ull);

    /* An MRS may fail as UNDEFINED (EC 0) or a trapped access (EC 0x18). */
    memset(&f, 0, sizeof f);
    f.elr = 0x3000;
    CHECK_EQ(tests_guard_on_sync(&g, &f, 0, 0, 0xD53FFFE5u), GUARD_SKIPPED);
    CHECK_EQU(f.x[5], ~0ull);
    CHECK_EQ(tests_guard_on_sync(&g, &f, 0x18ull << 26, 0, 0xD53FFFE5u), GUARD_SKIPPED);
    /* ... but not as a data abort, and a load not as an UNDEFINED. */
    CHECK_EQ(tests_guard_on_sync(&g, &f, 0x25ull << 26, 0, 0xD53FFFE5u), GUARD_FATAL);
    CHECK_EQ(tests_guard_on_sync(&g, &f, 0, 0, 0xB9400441u), GUARD_FATAL);
    CHECK_EQ(tests_guard_on_sync(&g, &f, 0x18ull << 26, 0, 0xB9400441u), GUARD_FATAL);
    /* DC ZVA: a data abort only, and its register is left alone. */
    memset(&f, 0x33, sizeof f);
    f.elr = 0x4000;
    CHECK_EQ(tests_guard_on_sync(&g, &f, 0x25ull << 26, 0x5000, 0xD50B7422u), GUARD_SKIPPED);
    CHECK_EQU(f.x[2], 0x3333333333333333ull);
    CHECK_EQU(f.elr, 0x4004);
    CHECK_EQ(tests_guard_on_sync(&g, &f, 0, 0, 0xD50B7422u), GUARD_FATAL);

    /* A store, a system write or an unknown word is never skipped, armed or not. */
    for (unsigned i = 0; i < sizeof insn_cases / sizeof insn_cases[0]; i++) {
        if (insn_cases[i].cls != INSN_OTHER)
            continue;
        memset(&f, 0, sizeof f);
        f.elr = 0x6000;
        CHECK_EQ(tests_guard_on_sync(&g, &f, 0x25ull << 26, 0, insn_cases[i].word), GUARD_FATAL);
        CHECK_EQ(tests_guard_on_sync(&g, &f, 0, 0, insn_cases[i].word), GUARD_FATAL);
        CHECK_EQU(f.elr, 0x6000); /* a refused exception edits nothing */
    }
    /* Not armed: every exception is fatal, even a perfect-looking load fault. */
    g.armed = 0;
    memset(&f, 0, sizeof f);
    CHECK_EQ(tests_guard_on_sync(&g, &f, 0x25ull << 26, 0, 0xB9400441u), GUARD_FATAL);

    /* SError. An asynchronous error is never answered by returning to the
     * instruction that was running: it goes to the failure continuation the armed
     * access registered, with SError masked in the saved status, so a source that
     * persists cannot be taken again and again. */
    memset(&g, 0, sizeof g);
    memset(&f, 0x44, sizeof f);
    f.elr = 0x9000;
    f.spsr = 0x3C5 & ~TESTS_SPSR_A; /* EL1h, SError unmasked, as around the guarded load */
    CHECK_EQ(tests_guard_on_serror(&g, &f, 0xBF000000ull), GUARD_FATAL); /* not armed */
    CHECK_EQU(f.elr, 0x9000);
    CHECK_EQU(f.spsr & TESTS_SPSR_A, 0);
    CHECK_EQ(g.serrors, 0);
    g.armed = 1; /* armed, but the access has no failure continuation: fatal */
    CHECK_EQ(tests_guard_on_serror(&g, &f, 0xBF000000ull), GUARD_FATAL);
    CHECK_EQU(f.elr, 0x9000);
    CHECK_EQU(f.spsr & TESTS_SPSR_A, 0);
    CHECK_EQ(g.serrors, 0);
    g.resume = 0xA000;
    CHECK_EQ(tests_guard_on_serror(&g, &f, 0xBF000000ull), GUARD_RETURN);
    CHECK_EQ(g.serrors, 1);
    CHECK_EQU(g.last_esr, 0xBF000000ull);
    CHECK_EQU(f.elr, 0xA000);                 /* the continuation, not the interrupted instruction */
    CHECK(f.spsr & TESTS_SPSR_A);             /* SError stays masked after the return */
    CHECK_EQU(f.x[7], 0x4444444444444444ull); /* no register is edited */
    /* The same SError again, as a persistent source would raise it: the frame
     * keeps pointing at the continuation and the count shows every one. */
    f.elr = 0x9000;
    CHECK_EQ(tests_guard_on_serror(&g, &f, 0xBF000000ull), GUARD_RETURN);
    CHECK_EQU(f.elr, 0xA000);
    CHECK_EQ(g.serrors, 2);
    CHECK_EQU(TESTS_SPSR_A, 1ull << 8);
    g.armed = 1;

    /* The log is bounded. */
    memset(&g, 0, sizeof g);
    g.armed = 1;
    for (unsigned i = 0; i < TESTS_GUARD_LOG + 5; i++) {
        memset(&f, 0, sizeof f);
        CHECK_EQ(tests_guard_on_sync(&g, &f, 0x25ull << 26, i, 0xB9400441u), GUARD_SKIPPED);
    }
    CHECK_EQ(g.logged, TESTS_GUARD_LOG);
    CHECK_EQ(g.faults, TESTS_GUARD_LOG + 5);

    /* How control came back from EL1. */
    memset(&f, 0, sizeof f);
    f.x[0] = 10;
    f.x[1] = 11;
    f.x[2] = 12;
    f.x[3] = 13;
    f.elr = 0x7000;
    tests_exit_from_syndrome(&f, (0x16ull << 26) | 0x11, 0, 0, &e);
    CHECK_EQ(e.kind, EXIT_HVC);
    CHECK_EQ(e.imm, 0x11);
    CHECK_EQU(e.x[1], 11);
    CHECK_EQU(e.elr, 0x7000);
    tests_exit_from_syndrome(&f, esr_sys64(3, 4, 13, 0, 2, 1, 1), 0, 0, &e);
    CHECK_EQ(e.kind, EXIT_SYSREG);
    tests_exit_from_syndrome(&f, 0x24ull << 26, 0x1234, 0, &e);
    CHECK_EQ(e.kind, EXIT_DABT);
    CHECK_EQU(e.far, 0x1234);
    tests_exit_from_syndrome(&f, 0x24ull << 26, 0, 1, &e);
    CHECK_EQ(e.kind, EXIT_ASYNC); /* an interrupt wins over whatever the syndrome says */
    tests_exit_from_syndrome(&f, 0x20ull << 26, 0, 0, &e);
    CHECK_EQ(e.kind, EXIT_OTHER);
    tests_exit_from_syndrome(&f, 0, 0, 0, &e);
    CHECK_EQ(e.kind, EXIT_OTHER);
}

/* ---- Save and restore bookkeeping ---- */

typedef struct {
    uint64_t reg[W_COUNT];
    unsigned fail_read;  /* mask of registers whose read fails */
    unsigned fail_write; /* mask of registers whose write returns an error */
    unsigned stuck;      /* mask of registers that silently ignore writes */
    unsigned lie_on_read;   /* mask of registers that read back wrongly once written */
    unsigned written_once;  /* mask of registers written at least once */
    int clobber_on_vbar;    /* installing other vectors rewrites ELR_EL2 and SPSR_EL2,
                             * as the first exception taken to them would */
    int writes[64];
    unsigned nwrites;
    uint64_t vbar_at_daif_write; /* VBAR_EL2 when DAIF was written */
    unsigned daif_writes;
    uint64_t vbar_original;
    int irq_on_our_vectors; /* DAIF was unmasked while the test vectors were installed */
} mockregs;

static int mr_rd(void *ctx, int w, uint64_t *v)
{
    mockregs *m = ctx;

    if (w < 0 || w >= W_COUNT || (m->fail_read & (1u << w)))
        return -1;
    *v = m->reg[w];
    if ((m->lie_on_read & (1u << w)) && (m->written_once & (1u << w)))
        *v ^= 1u;
    return 0;
}

static int mr_wr(void *ctx, int w, uint64_t v)
{
    mockregs *m = ctx;

    if (w < 0 || w >= W_COUNT)
        return -1;
    if (m->nwrites < 64)
        m->writes[m->nwrites++] = w;
    if (m->fail_write & (1u << w))
        return -1;
    m->written_once |= 1u << w;
    if (w == W_DAIF) {
        m->vbar_at_daif_write = m->reg[W_VBAR_EL2];
        m->daif_writes++;
        /* Unmasking interrupts while this image's vectors are still installed
         * is the defect the restore order exists to prevent (the vectors do not
         * handle IRQ). */
        if ((v & TESTS_DAIF_MASKED) != TESTS_DAIF_MASKED && m->reg[W_VBAR_EL2] != m->vbar_original)
            m->irq_on_our_vectors = 1;
    }
    if (w == W_VBAR_EL2 && m->clobber_on_vbar && v != m->vbar_original) {
        m->reg[W_ELR_EL2] = 0xE1E1E1E1E1E1E1E1ull;
        m->reg[W_SPSR_EL2] = 0x5A5A5A5Aull;
    }
    if (!(m->stuck & (1u << w)))
        m->reg[w] = v;
    return 0;
}

static void mr_init(mockregs *m)
{
    memset(m, 0, sizeof *m);
    for (int i = 0; i < W_COUNT; i++)
        m->reg[i] = 0x1000ull + (uint64_t)i * 0x111;
    m->reg[W_DAIF] = 0x00;
    m->vbar_original = m->reg[W_VBAR_EL2];
}

void test_x_sysstate(void)
{
    mockregs m;
    tests_wops ops = {&m, mr_rd, mr_wr};
    tests_sysstate s;
    uint64_t orig[W_COUNT];

    section("sysstate and scope");
    tests_sysstate_cleanup_reset();
    mr_init(&m);
    memcpy(orig, m.reg, sizeof orig);

    /* Nothing is writable before its old value was saved. */
    memset(&s, 0, sizeof s);
    CHECK_EQ(tests_sysstate_write(&s, &ops, W_HCR_EL2, 5), -1);
    CHECK_EQ(m.nwrites, 0);
    CHECK_EQ(tests_sysstate_save(&s, &ops, TESTS_WMASK(W_HCR_EL2)), 0);
    CHECK_EQ(tests_sysstate_write(&s, &ops, W_VBAR_EL2, 5), -1); /* not in the saved set */
    CHECK_EQ(tests_sysstate_write(&s, &ops, -1, 5), -1);
    CHECK_EQ(tests_sysstate_write(&s, &ops, W_COUNT, 5), -1);
    CHECK_EQ(m.nwrites, 0);
    CHECK_EQ(tests_sysstate_write(&s, &ops, W_HCR_EL2, 0x1234), 0);
    CHECK_EQU(m.reg[W_HCR_EL2], 0x1234);
    CHECK_EQ(tests_sysstate_restore(&s, &ops), 0);
    CHECK_EQU(m.reg[W_HCR_EL2], orig[W_HCR_EL2]);
    CHECK_EQ(tests_sysstate_restore(&s, &ops), 0); /* repeatable */
    CHECK_EQU(m.reg[W_HCR_EL2], orig[W_HCR_EL2]);

    /* A register that was only saved is not written back. */
    mr_init(&m);
    CHECK_EQ(tests_sysstate_save(&s, &ops, TESTS_WMASK_ALL), 0);
    m.nwrites = 0;
    CHECK_EQ(tests_sysstate_restore(&s, &ops), 0);
    CHECK_EQ(m.nwrites, 0);

    /* A failed save leaves the state empty and writes nothing. */
    mr_init(&m);
    m.fail_read = TESTS_WMASK(W_SP_EL1);
    CHECK_EQ(tests_sysstate_save(&s, &ops, TESTS_WMASK_ALL), -1);
    CHECK_EQ(tests_sysstate_write(&s, &ops, W_HCR_EL2, 1), -1);
    CHECK_EQ(m.nwrites, 0);

    /* Restore order: the vector base before the interrupt masks, DAIF last. */
    mr_init(&m);
    CHECK_EQ(tests_sysstate_save(&s, &ops, TESTS_WMASK_ALL), 0);
    for (int w = 0; w < W_COUNT; w++)
        CHECK_EQ(tests_sysstate_write(&s, &ops, w, 0xAAAA0000ull + (unsigned)w), 0);
    m.nwrites = 0;
    CHECK_EQ(tests_sysstate_restore(&s, &ops), 0);
    CHECK_EQ(m.nwrites, W_COUNT);
    CHECK_EQ(m.writes[m.nwrites - 1], W_DAIF);
    {
        int pos_vbar = -1, pos_daif = -1, pos_hcr = -1, pos_other = -1;

        for (unsigned i = 0; i < m.nwrites; i++) {
            if (m.writes[i] == W_VBAR_EL2)
                pos_vbar = (int)i;
            if (m.writes[i] == W_DAIF)
                pos_daif = (int)i;
            if (m.writes[i] == W_HCR_EL2)
                pos_hcr = (int)i;
            if (m.writes[i] == W_SCTLR_EL1X)
                pos_other = (int)i;
        }
        CHECK(pos_vbar >= 0 && pos_vbar < pos_daif);
        CHECK(pos_hcr >= 0 && pos_hcr < pos_other); /* NV bits go first */
    }
    for (int w = 0; w < W_COUNT; w++)
        CHECK_EQU(m.reg[w], orig[w]);

    /* A register that ignores writes is reported, and the others still go back. */
    mr_init(&m);
    CHECK_EQ(tests_sysstate_save(&s, &ops, TESTS_WMASK_ALL), 0);
    for (int w = 0; w < W_COUNT; w++)
        CHECK_EQ(tests_sysstate_write(&s, &ops, w, 0xBBBB0000ull + (unsigned)w), 0);
    m.stuck = TESTS_WMASK(W_HCR_EL2);
    CHECK_EQ(tests_sysstate_restore(&s, &ops), -1);
    CHECK_EQU(m.reg[W_VBAR_EL2], orig[W_VBAR_EL2]);
    CHECK_EQU(m.reg[W_DAIF], orig[W_DAIF]);
    CHECK_EQU(m.reg[W_SP_EL1], orig[W_SP_EL1]);
    CHECK(m.reg[W_HCR_EL2] != orig[W_HCR_EL2]);
    /* A write that errors out is reported too. */
    mr_init(&m);
    CHECK_EQ(tests_sysstate_save(&s, &ops, TESTS_WMASK_ALL), 0);
    CHECK_EQ(tests_sysstate_write(&s, &ops, W_VNCR_EL2, 0xCCCC), 0);
    m.fail_write = TESTS_WMASK(W_VNCR_EL2);
    CHECK_EQ(tests_sysstate_restore(&s, &ops), -1);
    /* A read-back that fails is a failed restore. */
    mr_init(&m);
    CHECK_EQ(tests_sysstate_save(&s, &ops, TESTS_WMASK_ALL), 0);
    CHECK_EQ(tests_sysstate_write(&s, &ops, W_SP_EL1, 0xDDDD), 0);
    m.fail_read = TESTS_WMASK(W_SP_EL1);
    CHECK_EQ(tests_sysstate_restore(&s, &ops), -1);

    /* The per-test scope: masks DAIF, installs the vectors, puts both back and
     * never lets an interrupt reach the image's vectors. */
    mr_init(&m);
    CHECK_EQ(tests_scope_enter(&s, &ops, 0x8000), 0);
    CHECK_EQU(m.reg[W_VBAR_EL2], 0x8000);
    CHECK_EQU(m.reg[W_DAIF], TESTS_DAIF_MASKED);
    CHECK_EQ(tests_scope_leave(&s, &ops), 0);
    CHECK_EQU(m.reg[W_VBAR_EL2], orig[W_VBAR_EL2]);
    CHECK_EQU(m.reg[W_DAIF], orig[W_DAIF]);
    CHECK_EQ(m.irq_on_our_vectors, 0);
    /* The same with interrupts enabled before the test (DAIF.I clear), the case
     * where the order matters. */
    mr_init(&m);
    m.reg[W_DAIF] = 0x000;
    m.vbar_original = m.reg[W_VBAR_EL2];
    CHECK_EQ(tests_scope_enter(&s, &ops, 0x8000), 0);
    CHECK_EQ(tests_scope_leave(&s, &ops), 0);
    CHECK_EQ(m.irq_on_our_vectors, 0);
    CHECK_EQ(m.daif_writes, 2);
    CHECK_EQU(m.vbar_at_daif_write, m.vbar_original); /* VBAR was already back */
    /* A vector base that does not take is undone, with DAIF restored. */
    mr_init(&m);
    m.stuck = TESTS_WMASK(W_VBAR_EL2);
    CHECK_EQ(tests_scope_enter(&s, &ops, 0x8000), -1);
    CHECK_EQU(m.reg[W_DAIF], orig[W_DAIF]);
    CHECK_EQU(m.reg[W_VBAR_EL2], orig[W_VBAR_EL2]);
    /* A failing save in enter changes nothing. */
    mr_init(&m);
    m.fail_read = TESTS_WMASK(W_DAIF);
    CHECK_EQ(tests_scope_enter(&s, &ops, 0x8000), -1);
    CHECK_EQ(m.nwrites, 0);

    /* ELR_EL2 and SPSR_EL2 are part of the scope. They are saved before the vector
     * base is touched (installing the table is what lets an exception reach it, and
     * the first exception rewrites both), marked touched for the whole scope, and
     * put back before the vector base and the interrupt masks. */
    mr_init(&m);
    memcpy(orig, m.reg, sizeof orig);
    m.clobber_on_vbar = 1;
    CHECK_EQ(tests_scope_enter(&s, &ops, 0x8000), 0);
    CHECK(s.have[W_ELR_EL2] && s.have[W_SPSR_EL2]);
    CHECK(s.touched[W_ELR_EL2] && s.touched[W_SPSR_EL2]);
    CHECK_EQU(s.saved[W_ELR_EL2], orig[W_ELR_EL2]);
    CHECK_EQU(s.saved[W_SPSR_EL2], orig[W_SPSR_EL2]);
    CHECK_EQU(m.reg[W_ELR_EL2], 0xE1E1E1E1E1E1E1E1ull); /* an exception overwrote them */
    m.nwrites = 0;
    CHECK_EQ(tests_scope_leave(&s, &ops), 0);
    CHECK_EQU(m.reg[W_ELR_EL2], orig[W_ELR_EL2]);
    CHECK_EQU(m.reg[W_SPSR_EL2], orig[W_SPSR_EL2]);
    {
        int pos_elr = -1, pos_spsr = -1, pos_vbar = -1, pos_daif = -1;

        for (unsigned i = 0; i < m.nwrites; i++) {
            pos_elr = m.writes[i] == W_ELR_EL2 ? (int)i : pos_elr;
            pos_spsr = m.writes[i] == W_SPSR_EL2 ? (int)i : pos_spsr;
            pos_vbar = m.writes[i] == W_VBAR_EL2 ? (int)i : pos_vbar;
            pos_daif = m.writes[i] == W_DAIF ? (int)i : pos_daif;
        }
        CHECK(pos_elr >= 0 && pos_spsr >= 0 && pos_elr < pos_vbar && pos_spsr < pos_vbar);
        CHECK(pos_vbar < pos_daif);
    }
    /* A SPSR_EL2 that does not go back is a failed leave, like any other register. */
    tests_sysstate_cleanup_reset();
    mr_init(&m);
    m.clobber_on_vbar = 1;
    CHECK_EQ(tests_scope_enter(&s, &ops, 0x8000), 0);
    m.stuck = TESTS_WMASK(W_SPSR_EL2);
    CHECK_EQ(tests_scope_leave(&s, &ops), -1);
    CHECK_EQ(tests_sysstate_cleanup_failed(), 1);
    CHECK_EQU(m.reg[W_VBAR_EL2], m.vbar_original); /* the others still went back */
    tests_sysstate_cleanup_reset();

    /* mark_clobbered: only a saved register, and it writes nothing. */
    mr_init(&m);
    memset(&s, 0, sizeof s);
    CHECK_EQ(tests_sysstate_mark_clobbered(&s, W_ELR_EL2), -1);
    CHECK_EQ(tests_sysstate_save(&s, &ops, TESTS_WMASK(W_ELR_EL2)), 0);
    CHECK_EQ(tests_sysstate_mark_clobbered(&s, W_SPSR_EL2), -1); /* not in the saved set */
    CHECK_EQ(s.touched[W_ELR_EL2], 0);
    CHECK_EQ(tests_sysstate_mark_clobbered(&s, W_ELR_EL2), 0);
    CHECK_EQ(s.touched[W_ELR_EL2], 1);
    CHECK_EQ(tests_sysstate_mark_clobbered(&s, W_COUNT), -1);
    CHECK_EQ(tests_sysstate_mark_clobbered(&s, -1), -1);
    CHECK_EQ(m.nwrites, 0);

    /* write_verified: the register is read back and must hold what was written
     * (under the compare mask), or the step stops. */
    mr_init(&m);
    CHECK_EQ(tests_sysstate_save(&s, &ops, TESTS_WMASK_ALL), 0);
    CHECK_EQ(tests_sysstate_write_verified(&s, &ops, W_HCR_EL2, 0x1234, ~0ull), 0);
    CHECK_EQU(m.reg[W_HCR_EL2], 0x1234);
    m.stuck = TESTS_WMASK(W_HCR_EL2); /* a write the hardware ignores */
    CHECK_EQ(tests_sysstate_write_verified(&s, &ops, W_HCR_EL2, 0x5678, ~0ull), -1);
    CHECK_EQ(s.touched[W_HCR_EL2], 1); /* still marked, so restore puts it back */
    m.stuck = 0;
    m.lie_on_read = TESTS_WMASK(W_VNCR_EL2); /* a write that reads back differently */
    CHECK_EQ(tests_sysstate_write_verified(&s, &ops, W_VNCR_EL2, 0x9000, ~0ull), -1);
    /* ...unless the differing bit is outside the compare mask. */
    CHECK_EQ(tests_sysstate_write_verified(&s, &ops, W_VNCR_EL2, 0x9000, ~1ull), 0);
    m.fail_read = TESTS_WMASK(W_SP_EL1); /* a read-back that fails */
    CHECK_EQ(tests_sysstate_write_verified(&s, &ops, W_SP_EL1, 1, ~0ull), -1);
    m.fail_write = TESTS_WMASK(W_VBAR_EL1X); /* a write that errors out */
    CHECK_EQ(tests_sysstate_write_verified(&s, &ops, W_VBAR_EL1X, 1, ~0ull), -1);
    CHECK_EQ(tests_sysstate_write_verified(&s, &ops, W_COUNT, 1, ~0ull), -1);
    memset(&s, 0, sizeof s); /* not saved: refused before any write */
    m.nwrites = 0;
    CHECK_EQ(tests_sysstate_write_verified(&s, &ops, W_HCR_EL2, 1, ~0ull), -1);
    CHECK_EQ(m.nwrites, 0);

    /* The sticky cleanup flag: set by a failed restore, not by a good one, and not
     * cleared by a later good one. */
    tests_sysstate_cleanup_reset();
    CHECK_EQ(tests_sysstate_cleanup_failed(), 0);
    mr_init(&m);
    CHECK_EQ(tests_sysstate_save(&s, &ops, TESTS_WMASK_ALL), 0);
    CHECK_EQ(tests_sysstate_write(&s, &ops, W_HCR_EL2, 0x77), 0);
    CHECK_EQ(tests_sysstate_restore(&s, &ops), 0);
    CHECK_EQ(tests_sysstate_cleanup_failed(), 0);
    CHECK_EQ(tests_sysstate_write(&s, &ops, W_HCR_EL2, 0x77), 0);
    m.stuck = TESTS_WMASK(W_HCR_EL2);
    CHECK_EQ(tests_sysstate_restore(&s, &ops), -1);
    CHECK_EQ(tests_sysstate_cleanup_failed(), 1);
    m.stuck = 0;
    CHECK_EQ(tests_sysstate_restore(&s, &ops), 0);
    CHECK_EQ(tests_sysstate_cleanup_failed(), 1);
    tests_sysstate_cleanup_reset();
    CHECK_EQ(tests_sysstate_cleanup_failed(), 0);

    /* The set-up steps tell a clean failure from a failed roll-back. A vector base
     * that does not take is rolled back and verified: TESTS_ERR, nothing latched. */
    mr_init(&m);
    m.stuck = TESTS_WMASK(W_VBAR_EL2);
    CHECK_EQ(tests_scope_enter(&s, &ops, 0x8000), TESTS_ERR);
    CHECK_EQ(tests_sysstate_cleanup_failed(), 0);
    /* A vector base that reads back wrongly after the write cannot be put back
     * either: the roll-back fails, and that is not the same as a clean failure. */
    mr_init(&m);
    m.lie_on_read = TESTS_WMASK(W_VBAR_EL2);
    CHECK_EQ(tests_scope_enter(&s, &ops, 0x8000), TESTS_CLEANUP_FAILED);
    CHECK_EQ(tests_sysstate_cleanup_failed(), 1);
    tests_sysstate_cleanup_reset();
    /* A DAIF write that errors out leaves DAIF unrestorable by the same route. */
    mr_init(&m);
    m.fail_write = TESTS_WMASK(W_DAIF);
    CHECK_EQ(tests_scope_enter(&s, &ops, 0x8000), TESTS_CLEANUP_FAILED);
    CHECK_EQ(tests_sysstate_cleanup_failed(), 1);
    tests_sysstate_cleanup_reset();
    /* DAIF that does not take the mask: undone, verified, a clean failure. */
    mr_init(&m);
    m.stuck = TESTS_WMASK(W_DAIF);
    CHECK_EQ(tests_scope_enter(&s, &ops, 0x8000), TESTS_ERR);
    CHECK_EQU(m.reg[W_VBAR_EL2], m.vbar_original);
    CHECK_EQ(tests_sysstate_cleanup_failed(), 0);
    CHECK_EQ(TESTS_ERR, -1);
    CHECK_EQ(TESTS_CLEANUP_FAILED, -2);

    /* The register lists. */
    CHECK_EQ(tests_wreg_allowed(W_DAIF), 1);
    CHECK_EQ(tests_wreg_allowed(W_ELR_EL2), 1);
    CHECK_EQ(tests_wreg_allowed(W_SPSR_EL2), 1);
    CHECK_EQ(tests_wreg_allowed(W_COUNT), 0);
    CHECK_EQ(tests_wreg_allowed(-1), 0);
    CHECK_STR(tests_wreg_name(W_VBAR_EL2), "VBAR_EL2");
    CHECK_STR(tests_wreg_name(W_ELR_EL2), "ELR_EL2");
    CHECK_STR(tests_wreg_name(W_SPSR_EL2), "SPSR_EL2");
    CHECK_STR(tests_wreg_name(W_COUNT), "?");
    /* Seven registers the NV2 test and the scope write by name, plus ELR_EL2 and
     * SPSR_EL2, which every exception to EL2 and every ERET to the stub overwrite.
     * The instruction audit (probes/uefi/tests/host/audit.py) lists the same nine. */
    CHECK_EQ(W_COUNT, 9);
    CHECK_EQU(tests_sysreg_mrs_word(TR_MIDR_EL1), 0xD5380000u);
    CHECK_EQU(tests_sysreg_mrs_word(TR_ID_AA64MMFR2_EL1), 0xD5380740u);
    CHECK_EQU(tests_sysreg_mrs_word(TR_COUNT), 0);
    CHECK_EQU(tests_sysreg_mrs_word(TR_TPIDR_EL2), 0xD53CD040u);
    CHECK_EQU(tests_sysreg_mrs_word(TR_ELR_EL2), 0xD53C4020u);
    CHECK_EQU(tests_sysreg_mrs_word(TR_SPSR_EL2), 0xD53C4000u);
}

/* ---- Read windows ---- */

typedef struct {
    unsigned calls;
    uint64_t last;
    int fault;
} mockbus;

static int mb_rd32(void *ctx, uint64_t addr, uint32_t *v)
{
    mockbus *b = ctx;

    b->calls++;
    b->last = addr;
    if (b->fault) {
        *v = 0xFFFFFFFFu;
        return -1;
    }
    *v = (uint32_t)(addr >> 2) ^ 0x5A5A0000u;
    return 0;
}

static unsigned mb_faults(void *ctx)
{
    return ((mockbus *)ctx)->fault ? ((mockbus *)ctx)->calls : 0;
}

void test_x_window(void)
{
    mockbus mb;
    tests_bus bus = {&mb, mb_rd32, NULL, mb_faults};
    tests_allow a;
    uint32_t v;
    uint64_t v64;

    section("read windows");
    memset(&mb, 0, sizeof mb);
    tests_allow_init(&a);
    CHECK_EQ(tests_allow_add(&a, 0x1000, 0x100, "A"), 0);
    CHECK_EQ(tests_allow_add(&a, 0x1000, 0, "empty"), -1);
    CHECK_EQ(tests_allow_add(&a, ~0ull - 3, 0x10, "wraps"), -1);
    CHECK_EQ(a.n, 1);
    CHECK(tests_allow_covers(&a, 0x1000, 4));
    CHECK(tests_allow_covers(&a, 0x10FC, 4));
    CHECK(!tests_allow_covers(&a, 0x10FD, 4));
    CHECK(!tests_allow_covers(&a, 0x10FC, 8));
    CHECK(!tests_allow_covers(&a, 0xFFC, 4));
    CHECK(!tests_allow_covers(&a, 0x1000, 0));
    CHECK(!tests_allow_covers(&a, ~0ull - 1, 4));
    CHECK(!tests_allow_covers(&a, 0x1100, 4));

    /* An address outside every window never reaches the bus. */
    CHECK_EQ(tests_rd32(&bus, &a, 0x2000, &v), RD_UNJUSTIFIED);
    CHECK_EQU(v, 0xFFFFFFFFu);
    CHECK_EQ(tests_rd32(&bus, &a, 0x1001, &v), RD_UNJUSTIFIED); /* misaligned */
    CHECK_EQ(tests_rd32(&bus, &a, 0x1100, &v), RD_UNJUSTIFIED); /* one past the end */
    CHECK_EQ(tests_rd64(&bus, &a, 0x10FC, &v64), RD_UNJUSTIFIED); /* straddles the end */
    CHECK_EQU(v64, ~0ull);
    CHECK_EQ(mb.calls, 0);
    /* The first half of a straddling 64-bit read must not be made either. */
    CHECK_EQ(tests_rd64(&bus, &a, 0x10F8, &v64), RD_OK);
    CHECK_EQ(mb.calls, 2);

    mb.calls = 0;
    CHECK_EQ(tests_rd32(&bus, &a, 0x1004, &v), RD_OK);
    CHECK_EQU(v, ((0x1004u >> 2) ^ 0x5A5A0000u));
    CHECK_EQ(mb.calls, 1);
    CHECK_EQ(tests_rd64(&bus, &a, 0x1008, &v64), RD_OK);
    CHECK_EQU(v64, ((uint64_t)((0x100Cu >> 2) ^ 0x5A5A0000u) << 32) | ((0x1008u >> 2) ^ 0x5A5A0000u));
    mb.fault = 1;
    CHECK_EQ(tests_rd32(&bus, &a, 0x1004, &v), RD_FAULT);
    CHECK_EQU(v, 0xFFFFFFFFu);
    CHECK_EQ(tests_rd64(&bus, &a, 0x1008, &v64), RD_FAULT);
    CHECK_EQU(v64, ~0ull);

    /* The window list is bounded. */
    tests_allow_init(&a);
    for (unsigned i = 0; i < TESTS_ALLOW_MAX; i++)
        CHECK_EQ(tests_allow_add(&a, 0x10000ull * (i + 1), 0x1000, "w"), 0);
    CHECK_EQ(tests_allow_add(&a, 0x900000, 0x1000, "one too many"), -1);
    CHECK(!tests_allow_covers(&a, 0x900000, 4));
}
