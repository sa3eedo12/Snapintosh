/* SPDX-License-Identifier: MIT */
/* The system registers the X2 image reads or writes, by encoding.
 *
 * Registers are named by their generic S<op0>_<op1>_C<CRn>_C<CRm>_<op2> form
 * rather than by mnemonic: the assembler only knows the architectural names
 * for the -march it is given, and this image deliberately builds for the plain
 * Armv8.0 base so that a newer register is never silently encodable.
 * Encodings are from the Arm Architecture Reference Manual for A-profile and
 * were cross-checked against Linux v6.12 arch/arm64/include/asm/sysreg.h and
 * arch/arm64/tools/sysreg (SYS_VNCR_EL2 line 523, SYS_TPIDR_EL2 line 604,
 * SYS_SP_EL1 line 527, SYS_CNTHCTL_EL2 line 615; HFGRTR_EL2, HFGITR_EL2,
 * HDFGRTR_EL2 and CNTPOFF_EL2 in the sysreg table) and, for the ID registers,
 * against the allocated-ID list in probes/idregs.h.
 *
 * The write list is separate and short on purpose: tests_wreg_name() and the
 * instruction audit of the built image (probes/uefi/tests/host/audit.py) both
 * work from it, so an `msr` to anything else fails a test.
 */
#ifndef TESTS_REGS_H
#define TESTS_REGS_H

#include <stdint.h>

/* X(name, op0, op1, CRn, CRm, op2) */
#define TESTS_SYSREG_LIST(X)         \
    X(MIDR_EL1, 3, 0, 0, 0, 0)       \
    X(REVIDR_EL1, 3, 0, 0, 0, 6)     \
    X(MPIDR_EL1, 3, 0, 0, 0, 5)      \
    X(ID_AA64PFR0_EL1, 3, 0, 0, 4, 0) \
    X(ID_AA64PFR1_EL1, 3, 0, 0, 4, 1) \
    X(ID_AA64DFR0_EL1, 3, 0, 0, 5, 0) \
    X(ID_AA64ISAR0_EL1, 3, 0, 0, 6, 0) \
    X(ID_AA64ISAR1_EL1, 3, 0, 0, 6, 1) \
    X(ID_AA64ISAR2_EL1, 3, 0, 0, 6, 2) \
    X(ID_AA64MMFR0_EL1, 3, 0, 0, 7, 0) \
    X(ID_AA64MMFR1_EL1, 3, 0, 0, 7, 1) \
    X(ID_AA64MMFR2_EL1, 3, 0, 0, 7, 2) \
    X(ID_AA64MMFR3_EL1, 3, 0, 0, 7, 3) \
    X(ID_AA64MMFR4_EL1, 3, 0, 0, 7, 4) \
    X(DCZID_EL0, 3, 3, 0, 0, 7)      \
    X(CTR_EL0, 3, 3, 0, 0, 1)        \
    X(CNTFRQ_EL0, 3, 3, 14, 0, 0)    \
    X(CNTPCT_EL0, 3, 3, 14, 0, 1)    \
    X(CNTVCT_EL0, 3, 3, 14, 0, 2)    \
    X(CNTHCTL_EL2, 3, 4, 14, 1, 0)   \
    X(CNTVOFF_EL2, 3, 4, 14, 0, 3)   \
    X(CNTPOFF_EL2, 3, 4, 14, 0, 6)   \
    X(HCR_EL2, 3, 4, 1, 1, 0)        \
    X(VBAR_EL2, 3, 4, 12, 0, 0)      \
    X(VNCR_EL2, 3, 4, 2, 2, 0)       \
    X(TPIDR_EL2, 3, 4, 13, 0, 2)     \
    X(SCTLR_EL2, 3, 4, 1, 0, 0)      \
    X(CURRENTEL, 3, 0, 4, 2, 2)      \
    X(SPSEL, 3, 0, 4, 2, 0)          \
    X(DAIF, 3, 3, 4, 2, 1)           \
    X(SCTLR_EL1, 3, 0, 1, 0, 0)      \
    X(SCTLR_EL12, 3, 5, 1, 0, 0)     \
    X(VBAR_EL1, 3, 0, 12, 0, 0)      \
    X(VBAR_EL12, 3, 5, 12, 0, 0)     \
    X(SP_EL1, 3, 4, 4, 1, 0)         \
    X(ELR_EL2, 3, 4, 4, 0, 1)        \
    X(SPSR_EL2, 3, 4, 4, 0, 0)       \
    X(HFGRTR_EL2, 3, 4, 1, 1, 4)     \
    X(HFGWTR_EL2, 3, 4, 1, 1, 5)     \
    X(HFGITR_EL2, 3, 4, 1, 1, 6)     \
    X(HDFGRTR_EL2, 3, 4, 3, 1, 4)    \
    X(HDFGWTR_EL2, 3, 4, 3, 1, 5)

enum tests_sysreg_id {
#define X(name, op0, op1, crn, crm, op2) TR_##name,
    TESTS_SYSREG_LIST(X)
#undef X
    TR_COUNT
};

const char *tests_sysreg_name(int id);
/* Fills enc[] with op0, op1, CRn, CRm, op2; returns 0, or -1 for a bad id. */
int tests_sysreg_encoding(int id, unsigned enc[5]);
/* The A64 word of "MRS x0, <reg>", compared with the compiler's output by the
 * image audit. 0 for a bad id. */
uint32_t tests_sysreg_mrs_word(int id);

/* Registers the image may write, every one of them saved before and restored
 * after (tests_sysstate). The "X" ones are the EL1 register through its EL12
 * encoding when HCR_EL2.E2H is set, because the plain EL1 name then means the
 * EL2 register.
 *
 * ELR_EL2 and SPSR_EL2 are on the list although no C code writes them: every
 * exception this image takes to EL2 (a guarded fault, an HVC from the EL1 stub)
 * overwrites both, and the ERET into the stub is set up by writing both. They
 * are saved before the first exception can happen, marked touched for the whole
 * scope, and put back with the interrupt masks still set. */
enum tests_wreg {
    W_VBAR_EL2 = 0,
    W_HCR_EL2,
    W_VNCR_EL2,
    W_SCTLR_EL1X,
    W_VBAR_EL1X,
    W_SP_EL1,
    W_DAIF,
    W_ELR_EL2,
    W_SPSR_EL2,
    W_COUNT
};

const char *tests_wreg_name(int id);
/* 1 only for a register in the list above. */
int tests_wreg_allowed(int id);

#endif /* TESTS_REGS_H */
