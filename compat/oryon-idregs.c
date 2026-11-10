/* SPDX-License-Identifier: MIT */
/* PAuth ID-register normalization for the Oryon v1 cores. The shapes and the
 * policy are described in oryon-idregs.h; this file implements them.
 *
 * Freestanding integer code. It is linked into the position-independent probe
 * blob, where there is no libc and absolute pointers must not appear: the MIDR
 * table holds integers only. */
#include "oryon-idregs.h"

/* Where each recorded MIDR keeps its pointer authentication. */
#define SHAPE_UNKNOWN 0
#define SHAPE_ISAR1   1   /* QARMA5 fields in ID_AA64ISAR1_EL1: mapped to the J700 shape */
#define SHAPE_ISAR2   2   /* already the J700 shape: left alone */

struct known_midr {
    uint32_t midr;
    uint32_t shape;
};

static const struct known_midr known_midrs[] = {
    { SNAP_ORYON_ID_MIDR_V1_VARIANT1, SHAPE_ISAR1 },
    { SNAP_ORYON_ID_MIDR_V1_VARIANT2, SHAPE_ISAR1 },
    { SNAP_ORYON_ID_MIDR_A16_J700, SHAPE_ISAR2 },
};

/* ID_AA64ISAR1_EL1 and ID_AA64ISAR2_EL1 PAuth fields, 4 bits each. */
#define ISAR1_APA_SHIFT   4
#define ISAR1_API_SHIFT   8
#define ISAR1_GPA_SHIFT   24
#define ISAR1_GPI_SHIFT   28
#define ISAR2_GPA3_SHIFT  8
#define ISAR2_APA3_SHIFT  12

#define FIELD_MASK        UINT64_C(0xf)
#define ADDRESS_FPACCOMBINE 5
#define GENERIC_PACGA       1

static unsigned field(uint64_t reg, unsigned shift)
{
    return (unsigned)((reg >> shift) & FIELD_MASK);
}

static unsigned shape_of(uint64_t midr)
{
    unsigned i;

    /* MIDR_EL1 [63:32] is RES0: a value with any of it set is not a CPU read. */
    if (midr >> 32)
        return SHAPE_UNKNOWN;
    for (i = 0; i < sizeof(known_midrs) / sizeof(known_midrs[0]); i++)
        if (known_midrs[i].midr == midr)
            return known_midrs[i].shape;
    return SHAPE_UNKNOWN;
}

static enum snap_oryon_verdict classify(uint64_t midr, uint64_t isar1, uint64_t isar2)
{
    unsigned shape = shape_of(midr);
    unsigned address, generic;

    if (shape == SHAPE_UNKNOWN)
        return SNAP_ORYON_REFUSE_MIDR;
    if (field(isar1, ISAR1_API_SHIFT) || field(isar1, ISAR1_GPI_SHIFT))
        return SNAP_ORYON_REFUSE_IMPDEF;

    /* Both families advertised, or the unrecorded one: the monitor would have
     * two answers to "which algorithm", so it takes neither. */
    if (shape == SHAPE_ISAR1) {
        if (field(isar2, ISAR2_APA3_SHIFT) || field(isar2, ISAR2_GPA3_SHIFT))
            return SNAP_ORYON_REFUSE_FAMILY;
        address = field(isar1, ISAR1_APA_SHIFT);
        generic = field(isar1, ISAR1_GPA_SHIFT);
    } else {
        if (field(isar1, ISAR1_APA_SHIFT) || field(isar1, ISAR1_GPA_SHIFT))
            return SNAP_ORYON_REFUSE_FAMILY;
        address = field(isar2, ISAR2_APA3_SHIFT);
        generic = field(isar2, ISAR2_GPA3_SHIFT);
    }

    if (!address && !generic)
        return SNAP_ORYON_REFUSE_NO_PAUTH;
    /* Only APA/APA3 = 5 has ever been seen (the A16 measured, the Oryon v1
     * observed). The lower levels (PAuth, EPAC, PAuth2, FPAC) differ in whether
     * a failed authentication faults, which nothing here has been shown to
     * handle, so they are refused rather than guessed. The monitor's PACGA path
     * also needs generic authentication. */
    if (address != ADDRESS_FPACCOMBINE)
        return SNAP_ORYON_REFUSE_ADDRESS;
    if (generic != GENERIC_PACGA)
        return SNAP_ORYON_REFUSE_GENERIC;
    return shape == SHAPE_ISAR1 ? SNAP_ORYON_NORMALIZED : SNAP_ORYON_PASSTHROUGH;
}

enum snap_oryon_verdict snap_oryon_normalize_pauth(uint64_t midr, uint64_t isar1,
                                                   uint64_t isar2,
                                                   uint64_t *guest_isar1,
                                                   uint64_t *guest_isar2)
{
    enum snap_oryon_verdict verdict = classify(midr, isar1, isar2);
    uint64_t out1 = 0, out2 = 0;

    if (verdict == SNAP_ORYON_NORMALIZED) {
        out1 = isar1 & ~((FIELD_MASK << ISAR1_APA_SHIFT) | (FIELD_MASK << ISAR1_GPA_SHIFT));
        out2 = (isar2 & ~((FIELD_MASK << ISAR2_APA3_SHIFT) | (FIELD_MASK << ISAR2_GPA3_SHIFT))) |
               ((uint64_t)ADDRESS_FPACCOMBINE << ISAR2_APA3_SHIFT) |
               ((uint64_t)GENERIC_PACGA << ISAR2_GPA3_SHIFT);
    } else if (verdict == SNAP_ORYON_PASSTHROUGH) {
        out1 = isar1;
        out2 = isar2;
    }
    if (guest_isar1)
        *guest_isar1 = out1;
    if (guest_isar2)
        *guest_isar2 = out2;
    return verdict;
}

uint64_t snap_oryon_id_view(uint32_t crm, uint32_t op2, uint64_t raw,
                            uint64_t midr, uint64_t isar1, uint64_t isar2)
{
    uint64_t guest_isar1, guest_isar2;

    if (crm > 7 || op2 > 7)
        return 0;
    if (crm != 6 || (op2 != 1 && op2 != 2))
        return raw;
    /* A refusal leaves both outputs zero, which is the answer. */
    snap_oryon_normalize_pauth(midr, isar1, isar2, &guest_isar1, &guest_isar2);
    return op2 == 1 ? guest_isar1 : guest_isar2;
}

int64_t snap_oryon_verdict_status(enum snap_oryon_verdict verdict)
{
    if (verdict == SNAP_ORYON_NORMALIZED || verdict == SNAP_ORYON_PASSTHROUGH)
        return 0;
    if (verdict == SNAP_ORYON_REFUSE_MIDR)
        return SNAP_ORYON_STATUS_MIDR;
    return SNAP_ORYON_STATUS_PAUTH;
}
