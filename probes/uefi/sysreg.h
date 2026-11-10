/* SPDX-License-Identifier: MIT */
/* System register inventory read by the SP11 EL2 probe.
 *
 * Registers are named by their generic S<op0>_<op1>_C<CRn>_C<CRm>_<op2>
 * encoding rather than by architectural mnemonic. The architectural names
 * are only available when the assembler is told a matching -march, and a
 * wrong feature set would silently change which registers can be encoded.
 *
 * The same list drives the enumerators, the on-screen names, the assembly
 * read switch and the host tests that check every encoding, so a typo in one
 * place cannot go unnoticed. Encodings are from the Arm Architecture
 * Reference Manual for A-profile (system register descriptions); they were
 * cross-checked against the allocated-ID-register list in probes/idregs.h.
 */
#ifndef PROBE_SYSREG_H
#define PROBE_SYSREG_H

#include <stdint.h>

/* X(name, op0, op1, CRn, CRm, op2). Order is not significant.
 *
 * ID_AA64MMFR3_EL1, ID_AA64MMFR4_EL1, ID_AA64ISAR2_EL1 and ID_AA64DFR1_EL1 are
 * deliberately absent. They belong to newer architecture revisions than the
 * rest, and a plain MRS of an ID encoding the core does not accept is taken as
 * an exception at EL2 (OBSERVED for unallocated encodings on the A16's Oryon
 * cores, see probes/idregs.h) with no vector of this image to catch it. The
 * guarded X2 tests image reads MMFR3, MMFR4 and ISAR2 behind an exception
 * guard; DFR1 is read nowhere. The instruction audit lists every register the
 * image may read (host/peinfo.py SYSREGS), so an MRS of one of them fails it. */
#define PROBE_SYSREG_LIST(X)                  \
    X(CURRENTEL, 3, 0, 4, 2, 2)               \
    X(HCR_EL2, 3, 4, 1, 1, 0)                 \
    X(SCTLR_EL2, 3, 4, 1, 0, 0)               \
    X(TCR_EL2, 3, 4, 2, 0, 2)                 \
    X(TTBR0_EL2, 3, 4, 2, 0, 0)               \
    X(TTBR1_EL2, 3, 4, 2, 0, 1)               \
    X(MAIR_EL2, 3, 4, 10, 2, 0)               \
    X(VBAR_EL2, 3, 4, 12, 0, 0)               \
    X(CPTR_EL2, 3, 4, 1, 1, 2)                \
    X(MDCR_EL2, 3, 4, 1, 1, 1)                \
    X(CNTHCTL_EL2, 3, 4, 14, 1, 0)            \
    X(CNTVOFF_EL2, 3, 4, 14, 0, 3)            \
    X(ICC_SRE_EL2, 3, 4, 12, 9, 5)            \
    X(SPSR_EL2, 3, 4, 4, 0, 0)                \
    X(ELR_EL2, 3, 4, 4, 0, 1)                 \
    X(MPIDR_EL1, 3, 0, 0, 0, 5)               \
    X(MIDR_EL1, 3, 0, 0, 0, 0)                \
    X(REVIDR_EL1, 3, 0, 0, 0, 6)              \
    X(ID_AA64PFR0_EL1, 3, 0, 0, 4, 0)         \
    X(ID_AA64PFR1_EL1, 3, 0, 0, 4, 1)         \
    X(ID_AA64ZFR0_EL1, 3, 0, 0, 4, 4)         \
    X(ID_AA64SMFR0_EL1, 3, 0, 0, 4, 5)        \
    X(ID_AA64DFR0_EL1, 3, 0, 0, 5, 0)         \
    X(ID_AA64ISAR0_EL1, 3, 0, 0, 6, 0)        \
    X(ID_AA64ISAR1_EL1, 3, 0, 0, 6, 1)        \
    X(ID_AA64MMFR0_EL1, 3, 0, 0, 7, 0)        \
    X(ID_AA64MMFR1_EL1, 3, 0, 0, 7, 1)        \
    X(ID_AA64MMFR2_EL1, 3, 0, 0, 7, 2)        \
    X(DCZID_EL0, 3, 3, 0, 0, 7)               \
    X(CTR_EL0, 3, 3, 0, 0, 1)                 \
    X(CNTFRQ_EL0, 3, 3, 14, 0, 0)             \
    X(CNTPCT_EL0, 3, 3, 14, 0, 1)

enum probe_sysreg_id {
#define X(name, op0, op1, crn, crm, op2) PSR_##name,
    PROBE_SYSREG_LIST(X)
#undef X
    PSR_COUNT
};

/* Number of encoding fields: op0, op1, CRn, CRm, op2. */
#define PROBE_SYSREG_ENC_FIELDS 5

const char *probe_sysreg_name(int id);

/* Fills enc[] with op0, op1, CRn, CRm, op2. Returns 0, or -1 for a bad id. */
int probe_sysreg_encoding(int id, unsigned enc[PROBE_SYSREG_ENC_FIELDS]);

/* The 32-bit A64 instruction word of "MRS x0, <reg>", which the PE test
 * compares with what the compiler actually emitted. Returns 0 for a bad id. */
uint32_t probe_sysreg_mrs_word(int id);

#endif /* PROBE_SYSREG_H */
