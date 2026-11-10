/* SPDX-License-Identifier: MIT */
/* Host model of the guest-visible counter and compare-value conversions on the
 * SP11 (plan todo C3). UNIMPLEMENTED target, EXPERIMENTAL model: pure
 * computation, nothing links it into a monitor build, and the A16 monitor
 * (compat/clock.c, probes/xnuboot/sysreg_clock.c) is unchanged.
 *
 * Why it exists. The A16 monitor presents a 24 MHz counter on a 19.2 MHz host
 * by trapping every guest counter and virtual-timer access (HCR_EL2 side:
 * CNTHCTL_EL2.{EL1TVT,EL1TVCT}, which need FEAT_ECV) and converting in
 * software. Its time base is CNTVCT_EL0 read at EL2, which is the physical
 * count minus CNTVOFF_EL2 while HCR_EL2.E2H is 0 and the bare physical count
 * while E2H is 1 (VERIFIED: DDI 0601 CNTVCT_EL0; DDI 0487 M.d D8.13.1 QXCQP and
 * XCLCK). The monitor never programs CNTVOFF_EL2, and compat/boot-relocate.h
 * only requires that q1n1's CPU and the CPU that runs vCPU 0 agree on it.
 * Linux carries an erratum "Broken CNTVOFF_EL2" for Oryon X1 (cpu_errata.c) whose
 * workaround writes CNTVOFF_EL2 = 0, traps the guest's virtual counter and
 * timer, and applies the offset in software (arch_timer.c; VERIFIED read, how
 * the hardware is broken is not stated: UNKNOWN). Whether the erratum applies
 * to this unit is INFERRED until MIDR_EL1 is read at X2.
 *
 * What the model decides. Which time base is admissible given per-CPU
 * measurements (CNTFRQ_EL0, ECV level, CNTVOFF_EL2 as found and as read back
 * after a write, CNTPOFF_EL2), and what every conversion does at its limits.
 * The policy it proves: a CNTVOFF_EL2 that does not read back what was
 * written is not relied on, and the boot is refused.
 *
 * Invariants checked by tools/test-timer-offset-model.py:
 *   - guest time is monotonic per CPU while the physical counter is;
 *   - a count never converts to a value that wrapped: an offset above the
 *     physical count, or a product above 2^64 - 1, is refused, not wrapped;
 *   - a count rounds down (the guest never runs ahead of real time) and a
 *     deadline rounds up (a timer never fires early), and both are exact on
 *     the compat/clock.c formulas wherever those do not overflow;
 *   - the skew between two CPUs equals the converted offset difference and
 *     is bounded by the policy.
 *
 * Compiled only under SNAP_PLATFORM_SP11. No I/O, no allocation, no global
 * state; callers may use it from any thread on distinct objects. */
#ifndef SNAP_COMPAT_TIMER_OFFSET_MODEL_H
#define SNAP_COMPAT_TIMER_OFFSET_MODEL_H

#include "platform.h"

#if SNAP_PLATFORM_SP11

#include <stddef.h>
#include <stdint.h>

#define TOM_MAX_CPUS 16u   /* SP11 has 12; headroom for the 10-core SKU and tests */

enum tom_status {
    TOM_OK = 0,
    TOM_E_PARAMS,            /* a rate that is zero or above UINT32_MAX (clock_rate_init's range) */
    TOM_E_NO_CPUS,           /* no CPUs, too many, or a CPU index out of range */
    TOM_E_NO_ECV,            /* ECV level 0 while the rates differ: guest reads cannot be trapped */
    TOM_E_ECV_RESERVED,      /* an ECV encoding the architecture does not define */
    TOM_E_FREQ,              /* CNTFRQ_EL0 is not the declared host rate on some CPU */
    TOM_E_E2H_MIX,           /* CPUs disagree on the effective E2H, so on what CNTVCT_EL0 returns at EL2 */
    TOM_E_CNTVOFF_READBACK,  /* CNTVOFF_EL2 read back differently from what was written: fail closed */
    TOM_E_CNTPOFF_READBACK,  /* the same for CNTPOFF_EL2 */
    TOM_E_CNTPOFF_ABSENT,    /* a CNTPOFF_EL2 result reported on a CPU whose ECV level has no such register */
    TOM_E_CNTVOFF_SKEW,      /* relied-on offsets differ across CPUs by more than the policy allows */
    TOM_E_OFFSET_RANGE,      /* an offset or epoch above the physical count: a wrapped, negative guest time */
    TOM_E_OVERFLOW,          /* a conversion does not fit in 64 bits */
    TOM_E_NOT_MONOTONIC,     /* a guest count lower than the previous one on that CPU */
    TOM_STATUS_COUNT
};

/* Where the monitor takes the host count it converts. */
enum tom_base {
    /* CNTVCT_EL0 read at EL2 (A16): physical - CNTVOFF_EL2 with E2H = 0, physical with E2H = 1.
     * Relies on CNTVOFF_EL2 being the same, and working, on every CPU. */
    TOM_BASE_CNTVCT = 0,
    /* CNTPCT_EL0 minus one monitor-owned epoch sampled once on the boot CPU. CNTPCT_EL0 at EL2
     * is the bare physical count (VERIFIED: DDI 0487 D12.2.1), so no per-CPU register is relied on. */
    TOM_BASE_CNTPCT_EPOCH = 1
};

struct tom_params { uint64_t host_hz, guest_hz; };

struct tom_policy {
    uint8_t base;                    /* enum tom_base */
    uint64_t max_skew_guest_ticks;   /* allowed cross-CPU skew of the relied-on offsets; 0 = equal */
};

/* One CPU's X2 measurements. */
struct tom_cpu_obs {
    uint64_t cntfrq;                 /* CNTFRQ_EL0 */
    uint64_t voff_incoming;          /* CNTVOFF_EL2 as found, before the monitor writes it */
    uint64_t voff_written;           /* the value the test wrote, if voff_written_valid */
    uint64_t voff_readback;          /* CNTVOFF_EL2 read after that write */
    uint64_t cntpoff_written, cntpoff_readback;
    uint8_t voff_written_valid;
    uint8_t cntpoff_tested;
    uint8_t ecv_level;               /* ID_AA64MMFR0_EL1.ECV: 0 none, 1 FEAT_ECV, 2 FEAT_ECV_POFF */
    uint8_t e2h;                     /* effective HCR_EL2.E2H when sampled */
};

/* Decide whether the measured CPUs admit the policy. On refusal *bad_cpu is
 * the index of the CPU that failed (n when the refusal is not per CPU). */
enum tom_status tom_admit(const struct tom_params *p, const struct tom_policy *pol,
                          const struct tom_cpu_obs *obs, size_t n, size_t *bad_cpu);

/* floor(count * guest_hz / host_hz): the guest-visible count for a host count.
 * Quotient and remainder, as compat/clock.c does, so a freestanding monitor
 * needs no 128-bit division. TOM_E_OVERFLOW instead of wrapping. */
enum tom_status tom_count_to_guest(const struct tom_params *p, uint64_t host_count, uint64_t *guest);
/* The smallest host count whose guest count reaches guest_ticks:
 * ceil(guest_ticks * host_hz / guest_hz). */
enum tom_status tom_guest_to_host_ceil(const struct tom_params *p, uint64_t guest_ticks, uint64_t *host);
/* compat/clock.c's clock_rate_sample, reproduced with its unchecked arithmetic, to
 * show where it silently wraps (host counts above about 0.8 * 2^64 at 19.2 to 24 MHz). */
uint64_t tom_legacy_sample(const struct tom_params *p, uint64_t host_count);

/* Running state of the monitor's guest clock. */
struct tom_clock {
    struct tom_params p;
    uint8_t base, e2h;
    uint64_t epoch;
    size_t ncpu;
    uint64_t voff[TOM_MAX_CPUS];     /* CNTVOFF_EL2 each CPU applies (read-back value) */
    uint64_t last[TOM_MAX_CPUS];     /* last guest count handed out per CPU */
};
enum tom_status tom_clock_init(struct tom_clock *c, const struct tom_params *p, enum tom_base base,
                               unsigned e2h, uint64_t epoch, const uint64_t *voff, size_t ncpu);
/* The guest count for physical count `physical` read on `cpu`. */
enum tom_status tom_sample(struct tom_clock *c, size_t cpu, uint64_t physical, uint64_t *guest);
/* The physical count at which the EL2 timer (CNTHP_CVAL_EL2, compared with the
 * physical count) must fire for a guest compare value: the earliest tick at which
 * the guest count reaches it. */
enum tom_status tom_deadline(const struct tom_clock *c, size_t cpu, uint64_t guest_cval, uint64_t *physical);
/* A CPU's applied offset changed under the monitor (a rewrite, or a thread that
 * moved to a CPU with another offset). The next sample may go backwards. */
void tom_offset_changed(struct tom_clock *c, size_t cpu, uint64_t new_voff);
/* Skew between two CPUs' guest counts at one physical count, in guest ticks. */
enum tom_status tom_skew(const struct tom_clock *c, size_t a, size_t b, uint64_t physical, uint64_t *skew);

/* What the guest would see without trapping, for the comparison "with and
 * without ECV". With ECV level 1 or more the monitor traps and converts. Without
 * it the hardware returns physical - CNTVOFF_EL2 at the host rate, so the guest
 * would run on a clock that is not the one its device tree declares. */
enum tom_status tom_guest_view(const struct tom_clock *c, unsigned ecv_level, size_t cpu, uint64_t physical,
                               uint64_t *count, uint64_t *effective_hz);

const char *tom_status_name(enum tom_status s);
/* "label: reference" for a refusal, NULL for TOM_OK. */
const char *tom_rule_citation(enum tom_status s);

#endif /* SNAP_PLATFORM_SP11 */
#endif /* SNAP_COMPAT_TIMER_OFFSET_MODEL_H */
