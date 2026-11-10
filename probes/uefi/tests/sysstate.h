/* SPDX-License-Identifier: MIT */
/* Bookkeeping for the few registers the NV2 test and the exception vectors
 * change: nothing is written unless its old value was saved first, and
 * restore() puts every written register back and reads it again.
 *
 * Why this is separate from the assembly: the rule "every write is saved and
 * restored on every path" is the safety property of the whole image, so it is
 * kept in plain C that the host tests drive with a register model, including a
 * model that fails to take a write.
 */
#ifndef TESTS_SYSSTATE_H
#define TESTS_SYSSTATE_H

#include <stdint.h>

#include "regs.h"

typedef struct {
    void *ctx;
    /* 0 on success. wr must refuse (return non-zero) for a register outside
     * enum tests_wreg; the host test checks that the real one does. */
    int (*rd)(void *ctx, int wreg, uint64_t *value);
    int (*wr)(void *ctx, int wreg, uint64_t value);
} tests_wops;

typedef struct {
    uint64_t saved[W_COUNT];
    uint8_t have[W_COUNT];    /* the old value was read */
    uint8_t touched[W_COUNT]; /* the test wrote the register */
} tests_sysstate;

#define TESTS_WMASK(r) (1u << (r))
#define TESTS_WMASK_ALL ((1u << W_COUNT) - 1u)
/* The registers every test brackets: the vector base, the interrupt masks and
 * the two exception-return registers that every exception to EL2 overwrites. */
#define TESTS_WMASK_SCOPE                                                                     \
    (TESTS_WMASK(W_VBAR_EL2) | TESTS_WMASK(W_DAIF) | TESTS_WMASK(W_ELR_EL2) |                 \
     TESTS_WMASK(W_SPSR_EL2))
/* The registers the NV2 test changes, without VNCR_EL2: on a core without
 * FEAT_NV2 that register is UNDEFINED, so it joins the mask only when the ID
 * registers say it exists. */
#define TESTS_WMASK_NV2                                                                       \
    (TESTS_WMASK(W_HCR_EL2) | TESTS_WMASK(W_SCTLR_EL1X) | TESTS_WMASK(W_VBAR_EL1X) |          \
     TESTS_WMASK(W_SP_EL1))

/* All DAIF bits set: debug, SError, IRQ and FIQ masked. */
#define TESTS_DAIF_MASKED 0x3C0u

/* Result of a setup step that can fail after it has already changed something.
 * 0 is success. TESTS_ERR means it failed and everything it had changed was put
 * back and read back as saved: the caller may report the test as not run and
 * carry on. TESTS_CLEANUP_FAILED means the roll-back itself failed: some register
 * is no longer what the firmware left, so nothing more may run, nothing may be
 * persisted and ExitBootServices must not be called. Every setup step in this
 * image returns one of the three. */
#define TESTS_ERR (-1)
#define TESTS_CLEANUP_FAILED (-2)

/* Reads and keeps every register whose bit is set in mask. Returns 0, or -1 if
 * any read failed (the state is then not usable and nothing has been written). */
int tests_sysstate_save(tests_sysstate *s, const tests_wops *ops, unsigned mask);

/* Writes a saved register. Refused (-1) for a register whose old value was not
 * saved, so that nothing can be changed without a way back. */
int tests_sysstate_write(tests_sysstate *s, const tests_wops *ops, int wreg, uint64_t value);

/* tests_sysstate_write, then reads the register back and requires the bits of
 * compare_mask to equal those of value. -1 if the write was refused or did not
 * take; the register stays marked touched, so restore() still puts it back. A
 * write the hardware silently ignores must stop the step that depends on it. */
int tests_sysstate_write_verified(tests_sysstate *s, const tests_wops *ops, int wreg,
                                  uint64_t value, uint64_t compare_mask);

/* Marks a saved register as changed by something other than this module: the
 * hardware rewrites ELR_EL2 and SPSR_EL2 at every exception taken to EL2. -1 if
 * the register was not saved. */
int tests_sysstate_mark_clobbered(tests_sysstate *s, int wreg);

/* Puts every touched register back, the interrupt masks last so that no
 * interrupt can arrive while this image's vectors are installed, then reads
 * each back. Returns 0 only if every one matches what was saved. Restores all
 * of them even when one fails, and may be called again. A failure also sets the
 * sticky flag below, so a caller that drops the return value cannot hide it. */
int tests_sysstate_restore(tests_sysstate *s, const tests_wops *ops);

/* 1 once any restore has failed since the last reset. The image halts when this
 * is set; only host tests clear it. */
int tests_sysstate_cleanup_failed(void);
void tests_sysstate_cleanup_reset(void);

/* The per-test bracket: saves the scope registers (before anything can raise an
 * exception), masks DAIF and installs the vector table at `vectors` (2 KiB
 * aligned, checked by the caller). Returns 0, TESTS_ERR with everything already
 * put back, or TESTS_CLEANUP_FAILED. tests_scope_leave restores and verifies. */
int tests_scope_enter(tests_sysstate *s, const tests_wops *ops, uint64_t vectors);
int tests_scope_leave(tests_sysstate *s, const tests_wops *ops);

#endif /* TESTS_SYSSTATE_H */
