/* SPDX-License-Identifier: MIT */
#include "decode.h"

uint64_t tests_bits(uint64_t v, unsigned hi, unsigned lo)
{
    unsigned width = hi - lo + 1u;

    v >>= lo;
    return width >= 64u ? v : v & ((1ull << width) - 1ull);
}

static const int ti_sysreg[TI_COUNT] = {
    TR_MIDR_EL1,        TR_REVIDR_EL1,      TR_ID_AA64PFR0_EL1,  TR_ID_AA64PFR1_EL1,
    TR_ID_AA64MMFR0_EL1, TR_ID_AA64MMFR1_EL1, TR_ID_AA64MMFR2_EL1, TR_ID_AA64MMFR3_EL1,
    TR_ID_AA64MMFR4_EL1, TR_ID_AA64ISAR0_EL1, TR_ID_AA64ISAR1_EL1, TR_ID_AA64ISAR2_EL1,
    TR_ID_AA64DFR0_EL1,  TR_DCZID_EL0,        TR_CTR_EL0,
};

int tests_ti_sysreg(int ti)
{
    return ti >= 0 && ti < TI_COUNT ? ti_sysreg[ti] : -1;
}

const char *tests_ti_name(int ti)
{
    return tests_sysreg_name(tests_ti_sysreg(ti));
}

/* ---- Classification ---- */

enum tests_nv_class tests_nv_classify(const tests_idset *s)
{
    uint64_t mmfr2, mmfr4, nv, frac;

    if (!tests_ids_have(s, TI_MMFR2) || !tests_ids_have(s, TI_MMFR4))
        return NVC_UNREAD;
    mmfr2 = s->v[TI_MMFR2];
    mmfr4 = s->v[TI_MMFR4];
    if (mmfr2 == ~0ull || mmfr4 == ~0ull)
        return NVC_UNREAD;
    nv = tests_bits(mmfr2, 27, 24);
    frac = tests_bits(mmfr4, 23, 20);
    /* NV_frac only has a meaning next to NV == 2; any other pairing is not a
     * state the architecture defines, so it is never trusted. */
    if (frac != 0 && nv != 2)
        return NVC_RESERVED;
    switch (nv) {
    case 0:
        return NVC_NONE;
    case 1:
        return NVC_NV_ONLY;
    case 2:
        if (frac == 0)
            return NVC_NV_NV2;
        return frac == 1 ? NVC_NV2_ONLY : NVC_RESERVED;
    default:
        return NVC_RESERVED;
    }
}

const char *tests_nv_class_name(enum tests_nv_class c)
{
    switch (c) {
    case NVC_UNREAD:
        return "UNREAD";
    case NVC_NONE:
        return "NONE";
    case NVC_NV_ONLY:
        return "NV-ONLY";
    case NVC_NV_NV2:
        return "NV+NV2";
    case NVC_NV2_ONLY:
        return "NV2-ONLY";
    default:
        return "RESERVED";
    }
}

int tests_ids_plausible(const tests_idset *s, const char **reason)
{
    static const int needed[] = {TI_MIDR, TI_PFR0, TI_MMFR1, TI_MMFR2, TI_MMFR4};
    const char *why = NULL;

    for (unsigned i = 0; i < sizeof needed / sizeof needed[0] && why == NULL; i++) {
        if (!tests_ids_have(s, needed[i]))
            why = "ID READ FAULTED";
        else if (s->v[needed[i]] == ~0ull)
            why = "ID VALUE ALL ONES";
    }
    if (why == NULL && s->v[TI_MIDR] == 0)
        why = "MIDR IS ZERO";
    if (why == NULL && tests_bits(s->v[TI_PFR0], 11, 8) == 0)
        why = "EL2 FIELD ZERO AT EL2";
    if (why == NULL && tests_bits(s->v[TI_PFR0], 7, 4) == 0)
        why = "NO EL1 IMPLEMENTED";
    if (why != NULL && reason != NULL)
        *reason = why;
    return why == NULL;
}

/* ---- Field helpers ---- */

static int field(const tests_idset *s, int ti, unsigned hi, unsigned lo)
{
    if (!tests_ids_have(s, ti))
        return -1;
    return (int)tests_bits(s->v[ti], hi, lo);
}

int tests_id_parange(const tests_idset *s) { return field(s, TI_MMFR0, 3, 0); }
int tests_id_vh(const tests_idset *s) { return field(s, TI_MMFR1, 11, 8); }
int tests_id_ecv(const tests_idset *s) { return field(s, TI_MMFR0, 63, 60); }
int tests_id_fgt(const tests_idset *s) { return field(s, TI_MMFR0, 59, 56); }
int tests_id_amu(const tests_idset *s) { return field(s, TI_PFR0, 47, 44); }
int tests_id_gic(const tests_idset *s) { return field(s, TI_PFR0, 27, 24); }
int tests_id_el1(const tests_idset *s) { return field(s, TI_PFR0, 7, 4); }
int tests_id_el2(const tests_idset *s) { return field(s, TI_PFR0, 11, 8); }
int tests_id_e2h0(const tests_idset *s) { return field(s, TI_MMFR4, 27, 24); }
int tests_id_tgran4(const tests_idset *s) { return field(s, TI_MMFR0, 31, 28); }
int tests_id_tgran16(const tests_idset *s) { return field(s, TI_MMFR0, 23, 20); }
int tests_id_tgran64(const tests_idset *s) { return field(s, TI_MMFR0, 27, 24); }

int tests_id_vmidbits(const tests_idset *s)
{
    int f = field(s, TI_MMFR1, 7, 4);

    if (f == 0)
        return 8;
    return f == 2 ? 16 : -1;
}

/* TGRAN4 and TGRAN64 are signed fields (0xF means "not implemented"); TGRAN16
 * is unsigned with 0 meaning "not implemented". */
const char *tests_tgran4_name(int f)
{
    if (f < 0)
        return "?";
    if (f == 0)
        return "YES";
    if (f == 1)
        return "LPA2";
    return f == 15 ? "NO" : "RSVD";
}

const char *tests_tgran16_name(int f)
{
    if (f < 0)
        return "?";
    if (f == 0)
        return "NO";
    if (f == 1)
        return "YES";
    return f == 2 ? "LPA2" : "RSVD";
}

const char *tests_tgran64_name(int f)
{
    if (f < 0)
        return "?";
    if (f == 0)
        return "YES";
    return f == 15 ? "NO" : "RSVD";
}

int tests_dczid_block_bytes(const tests_idset *s)
{
    uint64_t v;

    if (!tests_ids_have(s, TI_DCZID))
        return -1;
    v = s->v[TI_DCZID];
    if (v & (1ull << 4))
        return 0; /* DZP: DC ZVA is prohibited */
    return (int)(4u << (v & 0xFu));
}

uint64_t tests_hcr_for(uint64_t firmware_hcr, int nv, int nv2)
{
    uint64_t h = (firmware_hcr & ~TESTS_HCR_STUB_CLEAR) | TESTS_HCR_RW;

    if (nv)
        h |= TESTS_HCR_NV;
    if (nv2)
        h |= TESTS_HCR_NV2;
    return h;
}

/* ---- Exception syndrome ---- */

unsigned tests_esr_ec(uint64_t esr)
{
    return (unsigned)((esr >> 26) & 0x3Fu);
}

void tests_esr_sys64(uint64_t esr, tests_sys64_iss *out)
{
    out->op0 = (unsigned)tests_bits(esr, 21, 20);
    out->op2 = (unsigned)tests_bits(esr, 19, 17);
    out->op1 = (unsigned)tests_bits(esr, 16, 14);
    out->crn = (unsigned)tests_bits(esr, 13, 10);
    out->rt = (unsigned)tests_bits(esr, 9, 5);
    out->crm = (unsigned)tests_bits(esr, 4, 1);
    out->read = (int)(esr & 1u);
}

int tests_esr_is_sysreg(uint64_t esr, unsigned op0, unsigned op1, unsigned crn, unsigned crm,
                        unsigned op2, int read)
{
    tests_sys64_iss i;

    if (tests_esr_ec(esr) != TESTS_EC_SYS64)
        return 0;
    tests_esr_sys64(esr, &i);
    return i.op0 == op0 && i.op1 == op1 && i.crn == crn && i.crm == crm && i.op2 == op2 &&
           i.read == (read != 0);
}

/* ---- T01 rows ---- */

static void put_int(probe_sb *sb, int v)
{
    if (v < 0)
        probe_sb_putc(sb, '?');
    else
        probe_sb_dec(sb, (uint64_t)v);
}

void tests_ids_fill_rows(const tests_idset *s, tests_result *r)
{
    probe_sb *sb;

    for (int ti = 0; ti < TI_COUNT; ti++) {
        if (tests_ids_have(s, ti))
            tests_row_hex(r, tests_ti_name(ti), s->v[ti], 16);
        else
            tests_row_str(r, tests_ti_name(ti), "FAULT");
    }
    if ((sb = tests_row(r)) != NULL) {
        probe_sb_puts(sb, "PARANGE=");
        put_int(sb, tests_id_parange(s));
        probe_sb_puts(sb, " VMIDBITS=");
        put_int(sb, tests_id_vmidbits(s));
        probe_sb_puts(sb, " VH=");
        put_int(sb, tests_id_vh(s));
    }
    if ((sb = tests_row(r)) != NULL) {
        probe_sb_puts(sb, "TGRAN 4K=");
        probe_sb_puts(sb, tests_tgran4_name(tests_id_tgran4(s)));
        probe_sb_puts(sb, " 16K=");
        probe_sb_puts(sb, tests_tgran16_name(tests_id_tgran16(s)));
        probe_sb_puts(sb, " 64K=");
        probe_sb_puts(sb, tests_tgran64_name(tests_id_tgran64(s)));
    }
    if ((sb = tests_row(r)) != NULL) {
        const tests_idset *p = s;
        int nv = tests_ids_have(p, TI_MMFR2) ? (int)tests_bits(p->v[TI_MMFR2], 27, 24) : -1;
        int frac = tests_ids_have(p, TI_MMFR4) ? (int)tests_bits(p->v[TI_MMFR4], 23, 20) : -1;

        probe_sb_puts(sb, "NV=");
        put_int(sb, nv);
        probe_sb_puts(sb, " NV_FRAC=");
        put_int(sb, frac);
        probe_sb_puts(sb, " E2H0=");
        put_int(sb, tests_id_e2h0(s));
        probe_sb_putc(sb, ' ');
        probe_sb_puts(sb, tests_nv_class_name(tests_nv_classify(s)));
    }
    if ((sb = tests_row(r)) != NULL) {
        probe_sb_puts(sb, "ECV=");
        put_int(sb, tests_id_ecv(s));
        probe_sb_puts(sb, " FGT=");
        put_int(sb, tests_id_fgt(s));
        probe_sb_puts(sb, " AMU=");
        put_int(sb, tests_id_amu(s));
        probe_sb_puts(sb, " GIC=");
        put_int(sb, tests_id_gic(s));
    }
    if ((sb = tests_row(r)) != NULL) {
        int zva = tests_dczid_block_bytes(s);

        probe_sb_puts(sb, "EL1=");
        put_int(sb, tests_id_el1(s));
        probe_sb_puts(sb, " EL2=");
        put_int(sb, tests_id_el2(s));
        probe_sb_puts(sb, " ZVA=");
        if (zva < 0)
            probe_sb_putc(sb, '?');
        else if (zva == 0)
            probe_sb_puts(sb, "PROHIBITED");
        else {
            probe_sb_dec(sb, (uint64_t)zva);
            probe_sb_puts(sb, "B");
        }
    }
}
