/* SPDX-License-Identifier: MIT */
/* Fault handling policy for the X2 image's EL2 exception vectors, as pure code.
 *
 * Two kinds of exception are expected, and nothing else is:
 *
 *  1. A fault in a guarded access made at EL2 (a load from a device address or
 *     an MRS of an ID register). It is recorded, the faulting instruction is
 *     skipped and its destination register is set to all ones, which is what
 *     a failed PCIe read returns, so the test sees a value that cannot be taken
 *     for data and a fault count that says it was not.
 *  2. The EL1 stub handing control back (an HVC, or a trapped EL2-register
 *     access, or a data abort of the redirected access), which the caller
 *     classifies with tests_exit_from_syndrome.
 *
 * Anything else, or any exception while no guarded access is armed, is fatal:
 * the vector code draws a page saying so and stops. The image never guesses
 * how to resume from a state it did not create.
 */
#ifndef TESTS_GUARD_H
#define TESTS_GUARD_H

#include <stdint.h>

#include "nv2.h"

/* Register image the vector entry saves: x0..x30, the exception link register
 * and the saved program status. 272 bytes keeps the stack 16-byte aligned. */
struct tests_frame {
    uint64_t x[31];
    uint64_t elr;
    uint64_t spsr;
    uint64_t pad;
};
_Static_assert(sizeof(struct tests_frame) == 272, "the vector entry code assumes a 272-byte frame");

#define TESTS_GUARD_LOG 8u

/* PSTATE.A in SPSR_EL2: SError masked. */
#define TESTS_SPSR_A (1ull << 8)

typedef struct {
    volatile int armed;           /* a guarded access is in progress */
    /* Where an SError taken during the armed access resumes: the failure
     * continuation of that one access (the instruction after it), or 0 when the
     * armed access has none and an SError is therefore fatal. */
    volatile uint64_t resume;
    volatile unsigned faults;     /* synchronous faults skipped, ever */
    volatile unsigned serrors;    /* SErrors taken while armed, ever */
    volatile uint64_t last_esr;
    volatile uint64_t last_far;
    volatile uint64_t last_elr;
    volatile unsigned logged;     /* entries used in log[] */
    volatile uint64_t log_esr[TESTS_GUARD_LOG];
    volatile uint64_t log_far[TESTS_GUARD_LOG];
} tests_guard;

enum tests_insn_class {
    INSN_OTHER = 0,
    INSN_LOAD, /* LDR Wt/Xt, [Xn, #imm] (unsigned offset): the guarded MMIO read */
    INSN_MRS,  /* MRS Xt, <system register>: the guarded register read */
    INSN_DCZVA /* DC ZVA, Xt: the DC ZVA test's one cache-maintenance store */
};

/* Classifies one A64 word. *rt receives the destination register number. */
enum tests_insn_class tests_guard_classify(uint32_t insn, unsigned *rt);

enum tests_guard_action {
    GUARD_SKIPPED = 0, /* resume after the faulting instruction */
    GUARD_RETURN,      /* resume at the failure continuation, SError masked (SError) */
    GUARD_FATAL
};

/* An exception taken at EL2 from EL2 (vector offset 0x200). insn is the word at
 * f->elr. On GUARD_SKIPPED the frame has been edited: the destination register
 * is all ones and elr is advanced by one instruction. */
enum tests_guard_action tests_guard_on_sync(tests_guard *g, struct tests_frame *f, uint64_t esr,
                                            uint64_t far, uint32_t insn);

/* An SError taken at EL2 (vector offset 0x280). Only an access that registered a
 * failure continuation in g->resume may take one: the frame is edited so that the
 * return goes to that continuation, never back to the instruction that was
 * running (an asynchronous error that persists would otherwise be taken again and
 * again), with PSTATE.A set in the saved status so that it stays masked until the
 * continuation's own code ends. The access is counted as failed through
 * g->serrors. Otherwise GUARD_FATAL and the frame is untouched. */
enum tests_guard_action tests_guard_on_serror(tests_guard *g, struct tests_frame *f,
                                              uint64_t esr);

/* How control came back from EL1. async is 1 for an IRQ, FIQ or SError. */
void tests_exit_from_syndrome(const struct tests_frame *f, uint64_t esr, uint64_t far, int async,
                              tests_el1_exit *out);

#endif /* TESTS_GUARD_H */
