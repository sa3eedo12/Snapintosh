/* SPDX-License-Identifier: MIT */
/* The image's only contact with the machine: guarded register and memory
 * reads, the per-test vector scope, the EL1 stub runner and DC ZVA. Everything
 * the tests do is expressed through the interfaces in suite.h, nv2.h and
 * env.h; this file backs them with real instructions. The policy (what may be
 * written, what a fault means) lives in the pure modules next to it
 * (sysstate.c, guard.c, nv2.c) and is tested on the host.
 */
#ifndef TESTS_SYS_H
#define TESTS_SYS_H

#include <stdint.h>

#include "suite.h"

const tests_bus *tests_sys_bus(void);
const tests_nv2_ops *tests_sys_nv2_ops(void);
const tests_zva_ops *tests_sys_zva_ops(void);

/* Brackets one test: masks DAIF and installs the EL2 vector table, so a fault in
 * the test is recorded instead of reaching whatever VBAR_EL2 held. enter returns
 * 0, TESTS_ERR (failed, everything put back and verified) or TESTS_CLEANUP_FAILED
 * (the roll-back itself failed: the caller must stop). leave returns 0 only if
 * every register reads back as it was.
 *
 * No firmware service may be called between the two: its RaiseTPL/RestoreTPL pair
 * unmasks interrupts, and an interrupt would reach vectors that do not handle it. */
int tests_sys_scope_enter(void);
int tests_sys_scope_leave(void);

/* The end of the line after a failed set-up or restore: one more attempt to put
 * back everything the image changed, then every interrupt masked. Returns 0 if
 * every register then reads back as the firmware left it, -1 if one does not.
 * From then on any exception that reaches this image's vectors parks the CPU
 * without calling anything. Returns, so the caller can draw its page. */
int tests_sys_quiesce(void);

/* Called, then the image parks, when an exception arrives that no guarded access
 * or EL1 run was expecting. May draw a page; never returns control to the test. */
typedef void (*tests_fatal_fn)(const char *why, uint64_t esr, uint64_t far, uint64_t elr,
                               unsigned vector);
void tests_sys_set_fatal(tests_fatal_fn fn);

/* Raw CurrentEL. Needs no vectors. */
uint64_t tests_sys_currentel(void);

/* Facts for the self-test builds and the audit. */
unsigned tests_sys_fault_count(void);

#ifndef TESTS_SELFTEST
#define TESTS_SELFTEST 0
#endif
#if TESTS_SELFTEST
/* Emulator builds only: a guarded read of an implementation-defined register
 * that the emulator does not implement, which must fault as UNDEFINED. */
int tests_sys_selftest_undef(uint64_t *v);
#endif

#endif /* TESTS_SYS_H */
