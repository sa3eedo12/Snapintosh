/* SPDX-License-Identifier: MIT */
#ifndef SNAP_PROBES_SP11_STICK_MONITOR_H
#define SNAP_PROBES_SP11_STICK_MONITOR_H
/* The monitor entry of SNAPMON-M1.BIN (docs/SP11-STICK-MONITOR.md; docs/SP11-MONITOR-PORT.md, work item 4b): its parameters and its interface.
 * Compiled only under -DSNAP_SP11_STICK_MONITOR=1 (probes/sp11-stick-hw.h), which needs the GIC stage's gate; with it at 0 this header is empty
 * and the dry-run and GIC builds keep their bytes.
 *
 * What the stage is. After COPIED the wrapper synchronises the instruction caches, checks what the call will rely on, arms the GIC with the
 * arm half of the GIC stage (the quarantine of this CPU's other enabled interrupts, classes G, R and C with their read-back and the check of
 * ICC_HPPIR1_EL1 before the call; never CNTHP, VBAR_EL2 or HCR_EL2, which the monitor sets itself), calls the monitor that the build appended
 * to the file, and puts the GIC back, the quarantine's enables last (not written when the put-backs of both group 1 gates failed: the stage then ends
 * NOT_RESTORED with that quarantine still on). The call is a direct `bl` to the symbol sp11_monitor, which the build defines
 * at the offset where it put the image. The monitor owns the CPU until it returns: nothing of the wrapper runs meanwhile, so the only stop is the
 * monitor's own (the EL2 timer's interrupt) and the operator's power switch.
 *
 * The stack is option S2 of docs/SP11-STICK-MONITOR.md section 3: the call runs on the tail of REPORT.DAT, 256 KiB like the A16 host's
 * (q1n1's entry.S), not on the runner's 64 KiB, because the monitor's call graph has recursion and 324 indirect calls and no static bound
 * (tools/test-sp11-stick-monitor-build.py, H7). The area is filled with a pattern before the call and scanned after it, so the first run also
 * measures the depth; it is zeroed afterwards. The 184 KiB of REPORT.DAT below it (SM_FILLER_AT), which a stack that outgrows the area reaches
 * first, must be zero before the call and is scanned and zeroed after it: a word there is the monitor's overflow, and STACK FULL says so.
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

/* The pinned build_label of a bundle built for this run starts with these bytes: tools/sp11_stick_bundle_m1.py LABEL_PREFIX, which adds them to the
 * facts label for --m1-run and to no other kind of bundle (a facts label that carries them is refused there). The stage refuses a PIN.DAT whose label
 * lacks them before it calls anything: SNAPMON-M1.BIN paired with the PIN.DAT of a bundle whose record says that no monitor is called (the dry run,
 * the GIC and stop dry run) is the mix this catches. tools/test-sp11-stick-monitor-builder.py compares the two spellings. */
#define SP11_STICK_MONITOR_LABEL_PREFIX "M1RUN-"
_Static_assert(sizeof(SP11_STICK_MONITOR_LABEL_PREFIX) - 1 == 6, "the marker and the facts label must fit the 23 bytes that sp11sb_string_ok allows");

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
#define SM_FILLER_AT (SM_REC_AT + SG_REC_SLOT)  /* REPORT.DAT 0x13000: the 184 KiB between the monitor's record slot and the stack's area, which a stack that */
#define SM_FILLER_BYTES (SM_STACK_AT - SM_FILLER_AT)    /* outgrows its area reaches first: zero as the runner loaded it, scanned and zeroed after the call */
_Static_assert(SM_STACK_AT + SM_STACK_BYTES == SP11SB_REPORT_BYTES && SM_STACK_AT > SM_REC_AT + SG_REC_SLOT,
               "the stack is the end of REPORT.DAT, beyond the window and both extension slots");
_Static_assert(SM_STACK_BYTES % 16 == 0, "the top of the stack is 16-byte aligned");
_Static_assert(SM_FILLER_AT == 0x13000u && SM_FILLER_BYTES == 0x2E000u && SM_FILLER_BYTES % 8 == 0, "the filler is the gap between the records and the stack");

/* The words of PARAMS (struct params, probes/xnuboot/report_layout.c) that the stage reads. The offsets are those of clang's record layout of
 * the monitor build with the M1 profile (no SNAP_I2C_INPUT); tools/test-sp11-stick-monitor-build.py compares them with the build's own. */
#define SM_PARAMS_BYTES 0x2410u
#define SM_PARAMS_WATCHDOG 192u         /* watchdog_seconds: how long the guest runs before the monitor takes it back */
#define SM_PARAMS_GPU_FLAGS 0x2340u     /* gpu.flags: zero leaves every Stage-4 GPU path off */
#define SM_PARAMS_EARLY_STOP_FLAGS 0x23E0u      /* early_stop.flags: bit 3 makes setup allocate c->thermal and so opens the loads from the A16's sensor addresses */

/* ---- sp11-stick-monitor.c ---- */
/* Test-only faults (tools/test-sp11-stick-monitor.py proves each check can fail by building the stage with it; ARM_REFUSAL_CALLS and NO_ARM_PUTBACK, the two that
 * break what a refused arm half must do, are proved by tools/test-sp11-stick-monitor-arm.py). They exist only in the host build. */
#if (defined(SP11_STICK_MONITOR_SEED_NO_PARAMS_CHECK) || defined(SP11_STICK_MONITOR_SEED_NO_DIGEST) || defined(SP11_STICK_MONITOR_SEED_NO_REG_CHECK) || \
     defined(SP11_STICK_MONITOR_SEED_NO_IC_SYNC) || defined(SP11_STICK_MONITOR_SEED_STRAY_GIC) || defined(SP11_STICK_MONITOR_SEED_WRONG_ARGS) || \
     defined(SP11_STICK_MONITOR_SEED_NO_S2) || defined(SP11_STICK_MONITOR_SEED_NO_OVERRUN_CHECK) || defined(SP11_STICK_MONITOR_SEED_STORE_AFTER) || \
     defined(SP11_STICK_MONITOR_SEED_REFUSE_AFTER) || defined(SP11_STICK_MONITOR_SEED_NO_GIC_FINISH) || defined(SP11_STICK_MONITOR_SEED_NO_FILLER_SCAN) || \
     defined(SP11_STICK_MONITOR_SEED_NO_FILLER_PRECHECK) || defined(SP11_STICK_MONITOR_SEED_NO_FILLER_CLEAN) || \
     defined(SP11_STICK_MONITOR_SEED_FLUSH_UNWRITABLE) || defined(SP11_STICK_MONITOR_SEED_NO_LABEL_CHECK) || \
     defined(SP11_STICK_MONITOR_SEED_ARM_REFUSAL_CALLS) || defined(SP11_STICK_MONITOR_SEED_NO_ARM_PUTBACK) || \
     defined(SP11_STICK_MONITOR_SEED_TICKS_EARLY) || defined(SP11_STICK_MONITOR_SEED_NO_WINDOW_CLEAN) || \
     defined(SP11_STICK_MONITOR_SEED_NO_RECORD_CLEAN)) && !defined(SP11_STICK_HOST_TEST)
#error "the seeded faults are part of the host test build only"
#endif
/* The stage: after COPIED, before DONE. Returns 0 once the call has been made and returned (whatever it returned: the record and the page say)
 * and 0x1000 + GIC_NOT_READY (through sw_refuse) when a check before the call, or the arm of the GIC, did not pass: the head then names a stage
 * below MONITOR_ENTERED and refusal_detail an SM_OUT_* value (checks) or an sg_outcome (the arm). After MONITOR_ENTERED nothing is refused. */
uint64_t sw_monitor_stage(struct sw_ctx *c);
#endif /* SNAP_SP11_STICK_MONITOR */
#endif /* SNAP_PROBES_SP11_STICK_MONITOR_H */
