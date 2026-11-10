/* SPDX-License-Identifier: MIT */
/* Pure decoding for the X2 tests: ID register fields, the NV2 classification
 * the monitor's gate uses, the HCR_EL2 value the functional test programs and
 * the exception syndrome fields the vector handler reports.
 *
 * Field positions come from the public Linux v6.12 register description
 * (arch/arm64/tools/sysreg; no text copied): ID_AA64MMFR0_EL1 PARange [3:0],
 * TGRAN16 [23:20], TGRAN64 [27:24], TGRAN4 [31:28], FGT [59:56], ECV [63:60];
 * ID_AA64MMFR1_EL1 VMIDBits [7:4], VH [11:8]; ID_AA64MMFR2_EL1 NV [27:24];
 * ID_AA64MMFR4_EL1 E2H0 [27:24], NV_frac [23:20]; ID_AA64PFR0_EL1 EL1 [7:4],
 * EL2 [11:8], GIC [27:24], SVE [35:32], AMU [47:44]. The ESR layout is
 * arch/arm64/include/asm/esr.h (EC at bit 26; SYS64 ISS lines 193-221). The
 * HCR_EL2 bit numbers NV 42 and NV2 45 are the ones the monitor already
 * programs (probes/xnuboot/prepare.c) and Linux KVM sets for a nested guest
 * (arch/arm64/kvm/hyp/vhe/switch.c, __compute_hcr).
 */
#ifndef TESTS_DECODE_H
#define TESTS_DECODE_H

#include <stdint.h>

#include "regs.h"
#include "tests_types.h"

uint64_t tests_bits(uint64_t v, unsigned hi, unsigned lo);

/* The ID registers of the raw dump, in the order the brief lists them. */
enum tests_ti {
    TI_MIDR = 0,
    TI_REVIDR,
    TI_PFR0,
    TI_PFR1,
    TI_MMFR0,
    TI_MMFR1,
    TI_MMFR2,
    TI_MMFR3,
    TI_MMFR4,
    TI_ISAR0,
    TI_ISAR1,
    TI_ISAR2,
    TI_DFR0,
    TI_DCZID,
    TI_CTR,
    TI_COUNT
};

typedef struct {
    uint32_t have; /* bit i: register TI_i was read without a fault */
    uint64_t v[TI_COUNT];
} tests_idset;

const char *tests_ti_name(int ti);
int tests_ti_sysreg(int ti); /* the enum tests_sysreg_id behind a TI_ index */

static inline int tests_ids_have(const tests_idset *s, int ti)
{
    return (s->have >> ti) & 1u;
}

/* What the monitor's launch gate can use, by the ID registers alone. This is
 * stricter than the architecture on purpose and is the same rule as
 * compat/nv2-gate.c: only NV == 2 with NV_frac == 0 is the combination the
 * monitor programs (it sets HCR_EL2.NV and NV2 together). */
enum tests_nv_class {
    NVC_UNREAD = 0, /* ID_AA64MMFR2_EL1 or ID_AA64MMFR4_EL1 not read, or all ones */
    NVC_NONE,       /* NV == 0, NV_frac == 0 */
    NVC_NV_ONLY,    /* NV == 1: FEAT_NV without FEAT_NV2 */
    NVC_NV_NV2,     /* NV == 2, NV_frac == 0: both */
    NVC_NV2_ONLY,   /* NV == 2, NV_frac == 1: NV2 without HCR_EL2.NV */
    NVC_RESERVED    /* a reserved value or a contradictory pair */
};

enum tests_nv_class tests_nv_classify(const tests_idset *s);
const char *tests_nv_class_name(enum tests_nv_class c);

/* 1 if the registers the NV2 decision rests on look like real readings. On 0,
 * *reason names the first problem. A faulted read, an all-ones value (what a
 * masked or failed read gives), MIDR_EL1 == 0, an EL2 field of 0 while the code
 * runs at EL2, or no EL1 are all "not a reading". */
int tests_ids_plausible(const tests_idset *s, const char **reason);

/* Field helpers; each returns -1 when the register was not read. */
int tests_id_parange(const tests_idset *s);
int tests_id_vmidbits(const tests_idset *s); /* 8, 16 or -1 */
int tests_id_vh(const tests_idset *s);
int tests_id_ecv(const tests_idset *s);
int tests_id_fgt(const tests_idset *s);
int tests_id_amu(const tests_idset *s);
int tests_id_gic(const tests_idset *s);
int tests_id_el1(const tests_idset *s);
int tests_id_el2(const tests_idset *s);
int tests_id_e2h0(const tests_idset *s);
/* 4K, 16K and 64K granule support as the field values; names via the next. */
int tests_id_tgran4(const tests_idset *s);
int tests_id_tgran16(const tests_idset *s);
int tests_id_tgran64(const tests_idset *s);
const char *tests_tgran4_name(int field);
const char *tests_tgran16_name(int field);
const char *tests_tgran64_name(int field);

/* DCZID_EL0: block size in bytes (4 << BS), or 0 if DZP says DC ZVA is
 * prohibited; -1 when unread. */
int tests_dczid_block_bytes(const tests_idset *s);

/* HCR_EL2 bits the functional test programs or must clear. */
#define TESTS_HCR_VM (1ull << 0)
#define TESTS_HCR_FMO (1ull << 3)
#define TESTS_HCR_IMO (1ull << 4)
#define TESTS_HCR_AMO (1ull << 5)
#define TESTS_HCR_VF (1ull << 6)
#define TESTS_HCR_VI (1ull << 7)
#define TESTS_HCR_VSE (1ull << 8)
#define TESTS_HCR_TGE (1ull << 27)
#define TESTS_HCR_HCD (1ull << 29)
#define TESTS_HCR_RW (1ull << 31)
#define TESTS_HCR_E2H (1ull << 34)
#define TESTS_HCR_NV (1ull << 42)
#define TESTS_HCR_NV1 (1ull << 43)
#define TESTS_HCR_AT (1ull << 44)
#define TESTS_HCR_NV2 (1ull << 45)

/* The fields of the firmware's HCR_EL2 that the EL1 stub run may not inherit,
 * and why each one cannot be left as the firmware set it:
 *   VM                 stage 2 would translate the stub's fetches with the
 *                      firmware's VTTBR_EL2;
 *   FMO, IMO, AMO      with a routing bit set a physical interrupt is taken to
 *                      EL2 whatever PSTATE.I/F/A says at EL1, so the masks the
 *                      stub is entered with would no longer hold;
 *   VF, VI, VSE        virtual interrupts must not be pending into the stub;
 *   TGE                an exception return to EL1 is not usable with it set;
 *   HCD                it disables the HVC instruction the stub hands control
 *                      back with;
 *   NV, NV1, NV2, AT  what the test itself varies: the control and the
 *                      functional run differ only in NV2, and NV1 and AT stay
 *                      clear so that nothing else does. */
#define TESTS_HCR_STUB_CLEAR                                                                  \
    (TESTS_HCR_VM | TESTS_HCR_FMO | TESTS_HCR_IMO | TESTS_HCR_AMO | TESTS_HCR_VF |            \
     TESTS_HCR_VI | TESTS_HCR_VSE | TESTS_HCR_TGE | TESTS_HCR_HCD | TESTS_HCR_NV |            \
     TESTS_HCR_NV1 | TESTS_HCR_AT | TESTS_HCR_NV2)

/* The HCR_EL2 the EL1 stub runs under: the firmware's value with the fields in
 * TESTS_HCR_STUB_CLEAR cleared, RW set so that EL1 is AArch64, and NV and NV2 as
 * asked. Every other bit is the firmware's, because the firmware's own EL2 code
 * keeps running under this register for the whole run (E2H above all: clearing it
 * would change EL2's translation regime under the code's feet) and because the
 * restore must be able to put back exactly what it found. The caller reads the
 * register back before the ERET and stops on a mismatch. */
uint64_t tests_hcr_for(uint64_t firmware_hcr, int nv, int nv2);

/* Exception syndrome helpers. */
#define TESTS_EC_UNKNOWN 0x00u
#define TESTS_EC_HVC64 0x16u
#define TESTS_EC_SYS64 0x18u
#define TESTS_EC_DABT_LOW 0x24u
#define TESTS_EC_DABT_CUR 0x25u
#define TESTS_EC_SERROR 0x2Fu

unsigned tests_esr_ec(uint64_t esr);

typedef struct {
    unsigned op0, op1, crn, crm, op2, rt;
    int read; /* 1 for MRS, 0 for MSR */
} tests_sys64_iss;

/* Decodes the ISS of an EC 0x18 syndrome. */
void tests_esr_sys64(uint64_t esr, tests_sys64_iss *out);
/* 1 if esr is EC 0x18 for exactly this encoding and direction. */
int tests_esr_is_sysreg(uint64_t esr, unsigned op0, unsigned op1, unsigned crn, unsigned crm,
                        unsigned op2, int read);

/* Fills the T01 detail rows (the raw dump and the decoded fields). */
void tests_ids_fill_rows(const tests_idset *s, tests_result *r);

#endif /* TESTS_DECODE_H */
