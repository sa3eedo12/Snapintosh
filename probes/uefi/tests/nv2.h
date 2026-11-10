/* SPDX-License-Identifier: MIT */
/* The functional NV2 test, as pure decision logic over a small set of
 * operations that the EFI image implements with real exception levels
 * (sys_aarch64.c) and the host tests implement with a model.
 *
 * What it decides (kill criterion K1 of docs/SURFACE-PRO-11.md): whether the
 * core redirects an EL2-register access made from EL1 into the page named by
 * VNCR_EL2, and whether the same access traps to EL2 when NV2 is off. The ID
 * registers alone never produce a GO: they are filtered, hypervised and
 * mis-reported often enough in the field that only behaviour counts
 * (docs/SURFACE-PRO-11.md, section 6.3 item 2).
 *
 * The register accessed from the EL1 stub is TPIDR_EL2. It is a plain
 * software thread-pointer register with no side effect, and its VNCR slot is
 * architectural: offset 0x090 (Linux v6.12 arch/arm64/include/asm/vncr_mapping.h
 * line 17). Reading it back at EL2 before and after shows that the access never
 * reached the real register.
 */
#ifndef TESTS_NV2_H
#define TESTS_NV2_H

#include <stdint.h>

#include "decode.h"
#include "nv2rec.h"
#include "tests_types.h"

#define TESTS_NV2_TARGET_OFFSET 0x090u
/* The pattern the redirect stub writes, as the four 16-bit pieces the stub's
 * movz/movk instructions carry (vectors_aarch64.c builds the assembly from
 * these same macros, so the two cannot drift). */
#define TESTS_NV2_PATTERN_W3 0x5A17
#define TESTS_NV2_PATTERN_W2 0xC0DE
#define TESTS_NV2_PATTERN_W1 0xC0FF
#define TESTS_NV2_PATTERN_W0 0xEE01
#define TESTS_NV2_PATTERN                                                                     \
    (((uint64_t)TESTS_NV2_PATTERN_W3 << 48) | ((uint64_t)TESTS_NV2_PATTERN_W2 << 32) |        \
     ((uint64_t)TESTS_NV2_PATTERN_W1 << 16) | (uint64_t)TESTS_NV2_PATTERN_W0)
#define TESTS_NV2_POISON 0xA5A5A5A5A5A5A5A5ull
#define TESTS_NV2_PAGE_WORDS 512u

/* The EL1 stub's two programs. */
enum tests_el1_program {
    EL1_PROG_CONTROL = 1,  /* mrs x1, tpidr_el2 ; hvc #DONE_CONTROL */
    EL1_PROG_REDIRECT = 2, /* msr tpidr_el2, x0 ; mrs x1, tpidr_el2 ; hvc #DONE_REDIRECT */
};

/* HVC immediates the stub uses to hand control back. An exception taken at EL1
 * lands in a vector table whose entries are `hvc #(VECTOR_BASE + index)`. They
 * are plain literals because the assembly builds its text from them. */
#define TESTS_HVC_DONE_REDIRECT 0x10
#define TESTS_HVC_DONE_CONTROL 0x11
#define TESTS_HVC_VECTOR_BASE 0x100

enum tests_exit_kind {
    EXIT_NONE = 0,
    EXIT_HVC,    /* EC 0x16: the stub finished, or an EL1 vector entry ran */
    EXIT_SYSREG, /* EC 0x18: an EL2-register access trapped to EL2 */
    EXIT_DABT,   /* EC 0x24: a data abort from EL1 (e.g. the VNCR page) */
    EXIT_ASYNC,  /* IRQ, FIQ or SError while the stub ran */
    EXIT_OTHER,  /* any other synchronous syndrome */
    EXIT_MECH    /* the machinery itself failed; nothing about NV2 can be said */
};

typedef struct {
    uint32_t kind; /* enum tests_exit_kind */
    uint32_t imm;  /* HVC immediate */
    uint64_t esr;
    uint64_t far;
    uint64_t elr;
    uint64_t x[4]; /* the stub's x0..x3 at the exit */
} tests_el1_exit;

typedef struct {
    void *ctx;
    /* Saves every register the test will change, installs the exception
     * vectors and the EL1 context. 0 on success; TESTS_ERR when it failed after
     * putting everything back (verified); TESTS_CLEANUP_FAILED when the
     * roll-back itself failed. */
    int (*prepare)(void *ctx);
    /* HCR_EL2 for the next run: NV and NV2 as asked; map_vncr programs
     * VNCR_EL2 with the page's address. Both registers are read back and the
     * step fails unless they hold what was written, so the ERET to EL1 never
     * happens under a register the hardware ignored. 0 on success. */
    int (*set_hcr)(void *ctx, int nv, int nv2, int map_vncr);
    /* Runs one stub program at EL1 and reports how control came back. */
    int (*run)(void *ctx, int program, tests_el1_exit *out);
    /* A guarded read of the real TPIDR_EL2; 0 on success. */
    int (*real_tpidr)(void *ctx, uint64_t *value);
    /* The 4 KiB, page-aligned VNCR page (TESTS_NV2_PAGE_WORDS words). */
    uint64_t *(*page)(void *ctx);
    /* Makes the page's contents visible to the other side: clean and
     * invalidate to the point of coherency and a barrier. */
    void (*page_sync)(void *ctx);
    /* Restores everything prepare() saved and reads each register back.
     * 0 only if every one matches what was saved. Called on every path after
     * a successful prepare(). */
    int (*restore)(void *ctx);
} tests_nv2_ops;

enum tests_ctl {
    CTL_NOT_RUN = 0,
    CTL_TRAPPED_OK,        /* EC 0x18, TPIDR_EL2 read: the control behaved */
    CTL_TRAPPED_WRONG_ISS, /* trapped, but for another register or direction */
    CTL_NOT_TRAPPED,       /* the access completed at EL1 with NV2 off */
    CTL_UNDEF_EL1,         /* taken as UNDEFINED at EL1: NV traps do not work */
    CTL_ABORT,
    CTL_OTHER
};

enum tests_red {
    RED_NOT_RUN = 0,
    RED_LANDED_OK,        /* the value is in the page at the architectural offset */
    RED_LANDED_ELSEWHERE, /* redirected, but to another offset */
    RED_VALUE_MISMATCH,   /* the page or the read-back holds something else */
    RED_NOT_LANDED,       /* the stub finished and the page is untouched */
    RED_TRAPPED,          /* trapped to EL2 although NV2 was on */
    RED_UNDEF_EL1,
    RED_REAL_REG_CHANGED, /* the real TPIDR_EL2 changed: the access was not redirected */
    RED_VNCR_ABORT,       /* a data abort: the page is not mapped as assumed */
    RED_OTHER
};

typedef struct {
    int attempted;   /* the functional part was started (ID bits allowed it) */
    int prepared;    /* prepare() succeeded */
    int restore_ok;  /* restore() verified every register; only valid if prepared */
    int mech_fault;  /* a mechanics failure: no statement about NV2 */
    /* A roll-back or restore failed: some register is no longer what the
     * firmware left. Distinct from mech_fault (which may be a clean failure):
     * the image must stop, and no result of this run is trustworthy or kept. */
    int cleanup_failed;
    enum tests_ctl ctl;
    enum tests_red red;
    tests_el1_exit ctl_exit;
    tests_el1_exit red_exit;
    unsigned landed_offset; /* byte offset of the pattern for RED_LANDED_ELSEWHERE */
    uint64_t readback;      /* what the stub read back through its access */
} tests_nv2_obs;

enum tests_nv2_reason {
    NR_GO = 0,
    NR_IDS_UNUSABLE,    /* the ID registers are not a reading */
    NR_RESERVED_ID,     /* a reserved or contradictory NV / NV_frac pair */
    NR_NV_NONE,         /* NV == 0 */
    NR_NV_ONLY,         /* NV == 1: no NV2 */
    NR_NV2_ONLY,        /* NV2 without HCR_EL2.NV: the monitor needs both */
    NR_NOT_RUN,         /* advertised, but the functional test did not complete */
    NR_RESTORE_FAILED,  /* a register did not read back as saved: do not trust the run */
    NR_MECH_FAULT,
    NR_REDIRECT_FAILED, /* advertised and not redirected (K1 candidate) */
    NR_CONTROL_FAILED,  /* the redirect worked but the NV2-off control did not trap */
    NR_CONTRADICTION,   /* the ID bits say no NV2 and the access landed in the page */
    NR_VNCR_ABORT,      /* the VNCR page could not be used: re-run, do not decide K1 */
    NR_COUNT
};

typedef struct {
    int verdict; /* TV_GO, TV_NO_GO or TV_INCONCLUSIVE */
    int reason;  /* enum tests_nv2_reason */
    int label;   /* TL_INFERRED when only the ID bits decided, TL_OBSERVED otherwise */
} tests_nv2_verdict;

const char *tests_nv2_reason_name(int reason);

/* The decision. GO only for NV == 2, NV_frac == 0, plausible ID registers, a
 * sound run (state restored, no mechanics fault), the access landed at the
 * architectural offset, and the NV2-off control trapped. Every other input is
 * NO_GO or INCONCLUSIVE. */
tests_nv2_verdict tests_nv2_decide(const tests_idset *ids, const tests_nv2_obs *o);

/* Runs the control and the functional test through the operations and fills
 * the observations. It calls restore() on every path after prepare() succeeds,
 * and does not start the functional part unless the ID registers advertise
 * FEAT_NV (writing HCR_EL2.NV on a core without it would write a RES0 bit). */
void tests_nv2_run(const tests_nv2_ops *ops, const tests_idset *ids, tests_nv2_obs *out);

/* Classifiers, exposed so that every outcome can be tested on its own. */
enum tests_ctl tests_nv2_classify_control(const tests_el1_exit *e);
enum tests_red tests_nv2_classify_redirect(const tests_el1_exit *e, const uint64_t *page,
                                           int real_changed, unsigned *landed_offset,
                                           uint64_t *readback);

/* The record to emit for a run. A "pass" field is written only for a GO
 * verdict, and a run whose state restore failed or whose machinery faulted
 * reports not-run for both checks: the gate must never see a pass from a run
 * it should not have trusted. Returns 0 and fills *rec, or -1 when there is
 * nothing to record (the ID registers were not read). */
int tests_nv2_make_record(const tests_idset *ids, const tests_nv2_obs *o,
                          const tests_nv2_verdict *v, int model, uint32_t soc, uint32_t build_id,
                          tests_nv2_record *rec);

/* Fills the result rows of T02 (everything but the record lines). */
void tests_nv2_fill_rows(const tests_idset *ids, const tests_nv2_obs *o,
                         const tests_nv2_verdict *v, tests_result *r);

#endif /* TESTS_NV2_H */
