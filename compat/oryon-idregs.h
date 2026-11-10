/* SPDX-License-Identifier: MIT */
/* SNAP_PLATFORM_SP11: pointer-authentication ID-register normalization for the
 * Oryon v1 cores of the Surface Pro 11 (X1E80100).
 *
 * The J700 profile that the stock kernel was brought up against reports
 * pointer authentication in ID_AA64ISAR2_EL1: APA3 = 5 and GPA3 = 1, the QARMA3
 * fields (VERIFIED on the A16, docs/FEASIBILITY.md). A third-party profile of an
 * X1E80100 machine that is not ours reports the older place instead:
 * ID_AA64ISAR1_EL1 APA = 5 and GPA = 1, the QARMA5 fields, with APA3 = GPA3 = 0
 * (docs/SURFACE-PRO-11.md E26: OBSERVED on another unit, through a view a
 * hypervisor may have filtered; never read on ours). The guest is told the
 * J700 shape, and anything this file does not recognise is refused: the monitor
 * would otherwise present a PAuth configuration nobody has measured.
 *
 * The monitor never computes a PAC itself. The PACGA emulation in compat/pauth.c
 * takes the cryptographic operation from its caller, and probes/xnuboot/pauth.c
 * supplies it with the hardware PACGA instruction and a software-held key
 * bank; there is no software cipher in the tree. So the algorithm identity is
 * invisible to the monitor and only the ID registers differ. Whether a
 * different algorithm changes anything else the guest can observe is not
 * known: only measurements on the unit (docs/SURFACE-PRO-11.md section 6.3)
 * can answer it.
 *
 * Fields (Arm ARM DDI 0487, the AArch64 ID register index; positions as listed
 * in Linux arch/arm64/tools/sysreg):
 *   ID_AA64ISAR1_EL1  APA [7:4]  API [11:8]  GPA [27:24]  GPI [31:28]
 *   ID_AA64ISAR2_EL1  GPA3 [11:8]  APA3 [15:12]
 * APA/APA3 5 is FEAT_FPACCOMBINE (with PAuth2 and FPAC); GPA/GPA3 1 is
 * FEAT_PACGA. API and GPI name an implementation-defined algorithm.
 *
 * Not examined and passed through untouched, because no measurement says what
 * they mean here: ID_AA64ISAR2_EL1.PAC_frac, ID_AA64ISAR3_EL1.PACM and any
 * other PAuth-adjacent field.
 *
 * Pure integer code: no libc, no state, no tables of pointers; callable from
 * any context. Linked only into SNAP_PLATFORM_SP11 builds. */
#pragma once
#include <stdint.h>

/* Prepare status for the two refusals, in the band shared with
 * compat/nv2-gate.h (-90..-93). */
#define SNAP_ORYON_STATUS_MIDR   (-94)  /* the MIDR is not a recorded core */
#define SNAP_ORYON_STATUS_PAUTH  (-95)  /* the PAuth fields are not a recognised shape */

/* MIDR_EL1 values the normalizer knows, each with its evidence. Exact matches
 * only: an unseen variant or revision is refused until it is measured and added
 * to the table in oryon-idregs.c. */
#define SNAP_ORYON_ID_MIDR_V1_VARIANT1  UINT32_C(0x511f0011)  /* OBSERVED, E26 */
#define SNAP_ORYON_ID_MIDR_V1_VARIANT2  UINT32_C(0x512f0011)  /* OBSERVED, E26 */
#define SNAP_ORYON_ID_MIDR_A16_J700     UINT32_C(0x512f0021)  /* VERIFIED, FEASIBILITY.md */

/* Every refusal is a small value starting at zero and the two successes are
 * distinctive, so a zeroed or uninitialised verdict can never read as success. */
enum snap_oryon_verdict {
    SNAP_ORYON_REFUSE_MIDR = 0,    /* not a recorded Oryon v1 or A16 MIDR */
    SNAP_ORYON_REFUSE_IMPDEF,      /* API or GPI set: an algorithm the monitor does not cover */
    SNAP_ORYON_REFUSE_FAMILY,      /* PAuth advertised in the register this MIDR leaves clear */
    SNAP_ORYON_REFUSE_NO_PAUTH,    /* no PAuth advertised at all */
    SNAP_ORYON_REFUSE_ADDRESS,     /* address authentication is not APA/APA3 = 5 */
    SNAP_ORYON_REFUSE_GENERIC,     /* generic authentication is not GPA/GPA3 = 1 */
    SNAP_ORYON_REFUSE_COUNT,       /* number of refusals, not a verdict */
    SNAP_ORYON_NORMALIZED = 0x4f524e4d,   /* QARMA5-in-ISAR1 mapped to the J700 shape */
    SNAP_ORYON_PASSTHROUGH = 0x4f525054,  /* the recorded J700 shape, unchanged */
};

/* Decides what the guest reads in ID_AA64ISAR1_EL1 and ID_AA64ISAR2_EL1.
 *
 * `midr`, `isar1` and `isar2` are the raw MIDR_EL1, ID_AA64ISAR1_EL1 and
 * ID_AA64ISAR2_EL1 of the CPU that is asking, read by the caller; every CPU
 * decides for itself. All three are untrusted.
 *
 * SNAP_ORYON_NORMALIZED: the MIDR is a recorded Oryon v1 variant and the raw
 * registers are exactly the recorded shape (APA = 5, GPA = 1, API = GPI = 0,
 * APA3 = GPA3 = 0). *guest_isar1 has APA and GPA cleared, *guest_isar2 has APA3
 * = 5 and GPA3 = 1, and every other bit of both is the raw bit.
 * SNAP_ORYON_PASSTHROUGH: the MIDR is the recorded A16 and the registers are
 * the J700 shape (APA3 = 5, GPA3 = 1, APA = API = GPA = GPI = 0). Unchanged.
 * Any refusal: both outputs are zero (RAZ) and the caller must not launch the
 * guest. Either output pointer may be NULL. */
enum snap_oryon_verdict snap_oryon_normalize_pauth(uint64_t midr, uint64_t isar1,
                                                   uint64_t isar2,
                                                   uint64_t *guest_isar1,
                                                   uint64_t *guest_isar2);

/* What the guest reads for the ID register at op0 = 3, op1 = 0, CRn = 0, CRm =
 * `crm`, op2 = `op2`. `raw` is the hardware value of that register as the
 * monitor reads it (zero for an unallocated encoding). Only ID_AA64ISAR1_EL1
 * (CRm 6, op2 1) and ID_AA64ISAR2_EL1 (CRm 6, op2 2) change; for those two the
 * result comes from isar1 and isar2, which must be their own raw values, and is
 * zero under a refusal. Every other in-range encoding returns `raw`, so an
 * unallocated one stays RAZ. An out-of-range encoding (CRm or op2 above 7) is
 * zero. */
uint64_t snap_oryon_id_view(uint32_t crm, uint32_t op2, uint64_t raw,
                            uint64_t midr, uint64_t isar1, uint64_t isar2);

/* The prepare status for a verdict: 0 for the two successes,
 * SNAP_ORYON_STATUS_MIDR for SNAP_ORYON_REFUSE_MIDR, and SNAP_ORYON_STATUS_PAUTH
 * for every other value, including one that is not a known verdict. */
int64_t snap_oryon_verdict_status(enum snap_oryon_verdict verdict);
