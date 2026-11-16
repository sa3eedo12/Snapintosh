/* SPDX-License-Identifier: MIT */
#ifndef SNAP_PROBES_SP11_STICK_MONITOR_H
#define SNAP_PROBES_SP11_STICK_MONITOR_H
/* The monitor entry of SNAPMON-M1.BIN (docs/SP11-STICK-MONITOR.md; docs/SP11-MONITOR-PORT.md, work item 4b): its parameters and its interface.
 * Compiled only under -DSNAP_SP11_STICK_MONITOR=1 (probes/sp11-stick-hw.h), which needs the GIC stage's gate; with it at 0 this header is empty
 * and the dry-run and GIC builds keep their bytes.
 *
 * What the stage is. After COPIED the wrapper synchronises the instruction caches, checks what the call will rely on, arms the GIC with the
 * arm half of the GIC stage (classes G, R and C only: never CNTHP, VBAR_EL2 or HCR_EL2, which the monitor sets itself), calls the monitor that
 * the build appended to the file, and puts the GIC back. The call is a direct `bl` to the symbol sp11_monitor, which the build defines at the
 * offset where it put the image. The monitor owns the CPU until it returns: nothing of the wrapper runs meanwhile, so the only stop is the
 * monitor's own (the EL2 timer's interrupt) and the operator's power switch.
 *
 * The stack is option S2 of docs/SP11-STICK-MONITOR.md section 3: the call runs on the tail of REPORT.DAT, 256 KiB like the A16 host's
 * (q1n1's entry.S), not on the runner's 64 KiB, because the monitor's call graph has recursion and 324 indirect calls and no static bound
 * (tools/test-sp11-stick-monitor-build.py, H7). The area is filled with a pattern before the call and scanned after it, so the first run also
 * measures the depth; it is zeroed afterwards.
 *
 * Evidence: EXPERIMENTAL; hardware NOT RUN. The layout of the PARAMS words the stage reads is VERIFIED against clang's record layout of the
 * monitor build (tools/test-sp11-stick-monitor-build.py); what the monitor does on the unit is UNKNOWN. */
#include "sp11-stick-hw.h"

#if SNAP_SP11_STICK_MONITOR
#include "sp11-stick-gic.h"
#include "sp11-stick-monitor-record.h"

/* The build pins the image it appended: where it starts in the file, how long it is and its SHA-256 as four little-endian words (the
 * tool's report names the same). With no default, because a default would make the check of the image trivially true. */
#if !defined(SP11_MONITOR_OFFSET) || !defined(SP11_MONITOR_BYTES) || !defined(SP11_MONITOR_SHA0) || !defined(SP11_MONITOR_SHA1) || \
    !defined(SP11_MONITOR_SHA2) || !defined(SP11_MONITOR_SHA3)
#error "SNAP_SP11_STICK_MONITOR=1 needs SP11_MONITOR_OFFSET, SP11_MONITOR_BYTES and SP11_MONITOR_SHA0 to SHA3 (tools/sp11_stick_wrapper_build.py passes them)"
#endif
_Static_assert((SP11_MONITOR_OFFSET) % 4096 == 0 && (SP11_MONITOR_OFFSET) > 0 && (SP11_MONITOR_BYTES) >= 0x1000,
               "the monitor starts on a page and its vectors, 2 KiB into the image, are then 2 KiB aligned (VBAR_EL2)");

/* ---- Parameters. A different value is a different binary and so a different authorisation. ---- */
#ifndef SP11_STICK_MONITOR_BOX_MAX_S
#define SP11_STICK_MONITOR_BOX_MAX_S 5u         /* the longest time box PARAMS may carry (D5: 5 s); the 20 s UPTIME precaution is not tested beyond it */
#endif
_Static_assert(SP11_STICK_MONITOR_BOX_MAX_S >= 1 && SP11_STICK_MONITOR_BOX_MAX_S <= 10, "the time box is 1 to 10 s");
#define SM_WINDOW_BYTES 0x10000u        /* the monitor stores nothing at or above out + this (probes/xnuboot/report_layout.c): the extension slots follow */
#define SM_EXT_SLOTS 2u                 /* the GIC record's slot and this one's */
#define SM_STACK_AT 0x41000u            /* REPORT.DAT offset of the call's stack: [AT, AT + BYTES), the file's last 256 KiB */
#define SM_STACK_BYTES 0x40000u
#define SM_STACK_PATTERN UINT64_C(0xC3A5C3A5C3A5C3A5)
_Static_assert(SM_STACK_AT + SM_STACK_BYTES == SP11SB_REPORT_BYTES && SM_STACK_AT > SM_REC_AT + SG_REC_SLOT,
               "the stack is the end of REPORT.DAT, beyond the window and both extension slots");
_Static_assert(SM_STACK_BYTES % 16 == 0, "the top of the stack is 16-byte aligned");

/* The words of PARAMS (struct params, probes/xnuboot/report_layout.c) that the stage reads. The offsets are those of clang's record layout of
 * the monitor build with the M1 profile (no SNAP_I2C_INPUT); tools/test-sp11-stick-monitor-build.py compares them with the build's own. */
#define SM_PARAMS_BYTES 0x2410u
#define SM_PARAMS_WATCHDOG 192u         /* watchdog_seconds: how long the guest runs before the monitor takes it back */
#define SM_PARAMS_GPU_FLAGS 0x2340u     /* gpu.flags: zero leaves every Stage-4 GPU path off */
#define SM_PARAMS_EARLY_STOP_FLAGS 0x23E0u      /* early_stop.flags: bit 3 would read the A16's thermal sensors at addresses that are not this machine's */

/* ---- sp11-stick-monitor.c ---- */
/* Test-only faults (tools/test-sp11-stick-monitor.py proves each check can fail by building the stage with it). They exist only in the host build. */
#if (defined(SP11_STICK_MONITOR_SEED_NO_PARAMS_CHECK) || defined(SP11_STICK_MONITOR_SEED_NO_DIGEST) || defined(SP11_STICK_MONITOR_SEED_NO_REG_CHECK) || \
     defined(SP11_STICK_MONITOR_SEED_NO_IC_SYNC) || defined(SP11_STICK_MONITOR_SEED_STRAY_GIC) || defined(SP11_STICK_MONITOR_SEED_WRONG_ARGS) || \
     defined(SP11_STICK_MONITOR_SEED_NO_S2) || defined(SP11_STICK_MONITOR_SEED_NO_OVERRUN_CHECK) || defined(SP11_STICK_MONITOR_SEED_STORE_AFTER) || \
     defined(SP11_STICK_MONITOR_SEED_REFUSE_AFTER)) && !defined(SP11_STICK_HOST_TEST)
#error "the seeded faults are part of the host test build only"
#endif
/* The stage: after COPIED, before DONE. Returns 0 once the call has been made and returned (whatever it returned: the record and the page say)
 * and 0x1000 + GIC_NOT_READY (through sw_refuse) when a check before the call, or the arm of the GIC, did not pass: the head then names a stage
 * below MONITOR_ENTERED and refusal_detail an SM_OUT_* value (checks) or an sg_outcome (the arm). After MONITOR_ENTERED nothing is refused. */
uint64_t sw_monitor_stage(struct sw_ctx *c);
#endif /* SNAP_SP11_STICK_MONITOR */
#endif /* SNAP_PROBES_SP11_STICK_MONITOR_H */
