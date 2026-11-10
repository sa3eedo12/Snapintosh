/* SPDX-License-Identifier: MIT */
/* Host model of the guest counter on the SP11. See timer-offset-model.h for
 * scope, evidence labels and the policy this proves. */
#include "timer-offset-model.h"

#if SNAP_PLATFORM_SP11

static int params_ok(const struct tom_params *p)
{
    return p && p->host_hz && p->host_hz <= UINT32_MAX && p->guest_hz && p->guest_hz <= UINT32_MAX;
}

/* floor(count * num / den) from quotient and remainder. Both rates are at most
 * 2^32 - 1, so remainder * num stays below 2^64 and only the quotient product
 * and the final sum can overflow. */
static enum tom_status scale_floor(uint64_t num, uint64_t den, uint64_t count, uint64_t *out)
{
    uint64_t whole, sum;
    if (__builtin_mul_overflow(count / den, num, &whole)) return TOM_E_OVERFLOW;
    if (__builtin_add_overflow(whole, ((count % den) * num) / den, &sum)) return TOM_E_OVERFLOW;
    *out = sum;
    return TOM_OK;
}

/* ceil(count * num / den). The remainder product plus den - 1 is below 2^64 for
 * rates below 2^32. */
static enum tom_status scale_ceil(uint64_t num, uint64_t den, uint64_t count, uint64_t *out)
{
    uint64_t whole, sum;
    if (__builtin_mul_overflow(count / den, num, &whole)) return TOM_E_OVERFLOW;
    if (__builtin_add_overflow(whole, ((count % den) * num + den - 1) / den, &sum)) return TOM_E_OVERFLOW;
    *out = sum;
    return TOM_OK;
}

enum tom_status tom_count_to_guest(const struct tom_params *p, uint64_t host_count, uint64_t *guest)
{
    if (!params_ok(p) || !guest) return TOM_E_PARAMS;
    return scale_floor(p->guest_hz, p->host_hz, host_count, guest);
}

enum tom_status tom_guest_to_host_ceil(const struct tom_params *p, uint64_t guest_ticks, uint64_t *host)
{
    if (!params_ok(p) || !host) return TOM_E_PARAMS;
    return scale_ceil(p->host_hz, p->guest_hz, guest_ticks, host);
}

uint64_t tom_legacy_sample(const struct tom_params *p, uint64_t host_count)
{
    return (host_count / p->host_hz) * p->guest_hz + ((host_count % p->host_hz) * p->guest_hz) / p->host_hz;
}

enum tom_status tom_admit(const struct tom_params *p, const struct tom_policy *pol,
                          const struct tom_cpu_obs *obs, size_t n, size_t *bad_cpu)
{
    size_t scratch;
    if (!bad_cpu) bad_cpu = &scratch;
    *bad_cpu = n;
    if (!params_ok(p) || !pol || !obs || pol->base > TOM_BASE_CNTPCT_EPOCH) return TOM_E_PARAMS;
    if (!n || n > TOM_MAX_CPUS) return TOM_E_NO_CPUS;
    uint64_t lo = UINT64_MAX, hi = 0;
    for (size_t i = 0; i < n; i++) {
        const struct tom_cpu_obs *o = &obs[i];
        *bad_cpu = i;
        if (o->ecv_level > 2) return TOM_E_ECV_RESERVED;
        if (o->ecv_level == 0 && p->host_hz != p->guest_hz) return TOM_E_NO_ECV;
        if (o->cntfrq != p->host_hz) return TOM_E_FREQ;
        if (o->e2h != obs[0].e2h) return TOM_E_E2H_MIX;
        /* Unconditional: a register that does not hold what was written puts the CPU outside the
         * verified model, whichever time base is chosen. */
        if (o->voff_written_valid && o->voff_readback != o->voff_written) return TOM_E_CNTVOFF_READBACK;
        if (o->cntpoff_tested) {
            if (o->ecv_level < 2) return TOM_E_CNTPOFF_ABSENT;
            if (o->cntpoff_readback != o->cntpoff_written) return TOM_E_CNTPOFF_READBACK;
        }
        const uint64_t relied = o->voff_written_valid ? o->voff_written : o->voff_incoming;
        if (relied < lo) lo = relied;
        if (relied > hi) hi = relied;
    }
    *bad_cpu = n;
    /* An EL2 read of CNTVCT_EL0 applies CNTVOFF_EL2 only when E2H is 0. */
    if (pol->base == TOM_BASE_CNTVCT && !obs[0].e2h) {
        uint64_t skew;
        enum tom_status s = scale_floor(p->guest_hz, p->host_hz, hi - lo, &skew);
        if (s != TOM_OK) return s;
        if (skew > pol->max_skew_guest_ticks) return TOM_E_CNTVOFF_SKEW;
    }
    return TOM_OK;
}

enum tom_status tom_clock_init(struct tom_clock *c, const struct tom_params *p, enum tom_base base,
                               unsigned e2h, uint64_t epoch, const uint64_t *voff, size_t ncpu)
{
    if (!c || !params_ok(p) || base > TOM_BASE_CNTPCT_EPOCH || !voff) return TOM_E_PARAMS;
    if (!ncpu || ncpu > TOM_MAX_CPUS) return TOM_E_NO_CPUS;
    *c = (struct tom_clock){ .p = *p, .base = (uint8_t)base, .e2h = (uint8_t)(e2h != 0), .epoch = epoch, .ncpu = ncpu };
    for (size_t i = 0; i < ncpu; i++) c->voff[i] = voff[i];
    return TOM_OK;
}

/* The count the monitor converts: the time base minus its origin, refused when
 * the origin lies above the physical count (the virtual count would wrap). */
static enum tom_status host_count(const struct tom_clock *c, size_t cpu, uint64_t physical, uint64_t *out)
{
    if (cpu >= c->ncpu) return TOM_E_NO_CPUS;
    uint64_t origin = 0;
    if (c->base == TOM_BASE_CNTPCT_EPOCH) origin = c->epoch;
    else if (!c->e2h) origin = c->voff[cpu];
    if (physical < origin) return TOM_E_OFFSET_RANGE;
    *out = physical - origin;
    return TOM_OK;
}

static enum tom_status guest_at(const struct tom_clock *c, size_t cpu, uint64_t physical, uint64_t *guest)
{
    uint64_t count;
    enum tom_status s = host_count(c, cpu, physical, &count);
    return s != TOM_OK ? s : scale_floor(c->p.guest_hz, c->p.host_hz, count, guest);
}

enum tom_status tom_sample(struct tom_clock *c, size_t cpu, uint64_t physical, uint64_t *guest)
{
    uint64_t g;
    enum tom_status s = guest_at(c, cpu, physical, &g);
    if (s != TOM_OK) return s;
    if (g < c->last[cpu]) return TOM_E_NOT_MONOTONIC;
    c->last[cpu] = g;
    *guest = g;
    return TOM_OK;
}

enum tom_status tom_deadline(const struct tom_clock *c, size_t cpu, uint64_t guest_cval, uint64_t *physical)
{
    if (cpu >= c->ncpu) return TOM_E_NO_CPUS;
    uint64_t ticks, at, origin = 0;
    enum tom_status s = scale_ceil(c->p.host_hz, c->p.guest_hz, guest_cval, &ticks);
    if (s != TOM_OK) return s;
    if (c->base == TOM_BASE_CNTPCT_EPOCH) origin = c->epoch;
    else if (!c->e2h) origin = c->voff[cpu];
    if (__builtin_add_overflow(ticks, origin, &at)) return TOM_E_OVERFLOW;
    *physical = at;
    return TOM_OK;
}

void tom_offset_changed(struct tom_clock *c, size_t cpu, uint64_t new_voff)
{
    if (cpu < c->ncpu) c->voff[cpu] = new_voff;
}

enum tom_status tom_skew(const struct tom_clock *c, size_t a, size_t b, uint64_t physical, uint64_t *skew)
{
    uint64_t ga, gb;
    enum tom_status s = guest_at(c, a, physical, &ga);
    if (s != TOM_OK) return s;
    s = guest_at(c, b, physical, &gb);
    if (s != TOM_OK) return s;
    *skew = ga > gb ? ga - gb : gb - ga;
    return TOM_OK;
}

enum tom_status tom_guest_view(const struct tom_clock *c, unsigned ecv_level, size_t cpu, uint64_t physical,
                               uint64_t *count, uint64_t *effective_hz)
{
    if (cpu >= c->ncpu) return TOM_E_NO_CPUS;
    if (ecv_level > 2) return TOM_E_ECV_RESERVED;
    if (ecv_level == 0) {
        /* No EL1TVCT: the guest reads the hardware virtual counter, physical - CNTVOFF_EL2
         * at the host rate, whatever the monitor wants it to see (an EL1 read applies the offset). */
        if (c->p.host_hz != c->p.guest_hz) return TOM_E_NO_ECV;
        if (physical < c->voff[cpu]) return TOM_E_OFFSET_RANGE;
        *count = physical - c->voff[cpu];
        *effective_hz = c->p.host_hz;
        return TOM_OK;
    }
    enum tom_status s = guest_at(c, cpu, physical, count);
    *effective_hz = c->p.guest_hz;
    return s;
}

static const struct { const char *name, *cite; } rules[TOM_STATUS_COUNT] = {
    [TOM_OK] = { "OK", 0 },
    [TOM_E_PARAMS] = { "PARAMS", "model: rates must be 1..UINT32_MAX, as clock_rate_init (compat/clock.c) requires" },
    [TOM_E_NO_CPUS] = { "NO_CPUS", "model: one to TOM_MAX_CPUS CPUs, and a CPU index inside that range" },
    [TOM_E_NO_ECV] = { "NO_ECV",
        "VERIFIED: DDI 0601 ID_AA64MMFR0_EL1.ECV and CNTHCTL_EL2.{EL1TVT,EL1TVCT} exist only with FEAT_ECV; without them "
        "the guest's CNTVCT_EL0 and virtual timer cannot be trapped, so 19.2 MHz to 24 MHz scaling is impossible "
        "(probes/xnuboot/prepare.c refuses the same case)" },
    [TOM_E_ECV_RESERVED] = { "ECV_RESERVED",
        "VERIFIED: DDI 0601 ID_AA64MMFR0_EL1.ECV defines 0b0000, 0b0001 and 0b0010; every other value is reserved" },
    [TOM_E_FREQ] = { "FREQ",
        "VERIFIED: DDI 0487 M.d D12.1.2.1, CNTFRQ_EL0 holds the effective frequency and firmware sets it; the A16 monitor "
        "refuses a mismatch (probes/xnuboot/prepare.c)" },
    [TOM_E_E2H_MIX] = { "E2H_MIX",
        "VERIFIED: DDI 0601 CNTVCT_EL0, an EL2 read applies CNTVOFF_EL2 only when E2H is 0, so CPUs in different E2H "
        "states return different counts" },
    [TOM_E_CNTVOFF_READBACK] = { "CNTVOFF_READBACK",
        "INFERRED (policy): DDI 0601 CNTVOFF_EL2 is a plain 64-bit register, so a differing read-back is outside the "
        "architecture. Linux 7475ea8 cpu_errata.c 'Broken CNTVOFF_EL2' (Oryon X1) is OBSERVED to need a workaround; "
        "the mechanism is not stated (UNKNOWN)" },
    [TOM_E_CNTPOFF_READBACK] = { "CNTPOFF_READBACK", "INFERRED (policy): as CNTVOFF_READBACK, for CNTPOFF_EL2 (DDI 0601)" },
    [TOM_E_CNTPOFF_ABSENT] = { "CNTPOFF_ABSENT",
        "VERIFIED: DDI 0601 CNTPOFF_EL2 is present only with FEAT_ECV_POFF (ID_AA64MMFR0_EL1.ECV == 2)" },
    [TOM_E_CNTVOFF_SKEW] = { "CNTVOFF_SKEW",
        "VERIFIED arithmetic, INFERRED requirement: each CPU applies its own CNTVOFF_EL2 (DDI 0601 CNTVCT_EL0), so unequal "
        "offsets skew guest time across CPUs; compat/boot-relocate.h asks for equality only between q1n1's CPU and vCPU 0's" },
    [TOM_E_OFFSET_RANGE] = { "OFFSET_RANGE",
        "VERIFIED: DDI 0601 CNTVOFF_EL2 and CNTVCT_EL0, the virtual count is a 64-bit difference, so an offset above the "
        "physical count wraps to a count near 2^64; D12.1.2 the system counter starts from zero" },
    [TOM_E_OVERFLOW] = { "OVERFLOW",
        "model: a product above 2^64 - 1; compat/clock.c's clock_rate_sample wraps silently there" },
    [TOM_E_NOT_MONOTONIC] = { "NOT_MONOTONIC",
        "VERIFIED invariant: DDI 0487 D12.1.2, system time never goes backwards; guest time is monotonic only while the "
        "offset is constant" },
};

const char *tom_status_name(enum tom_status s)
{
    return (unsigned)s < TOM_STATUS_COUNT && rules[s].name ? rules[s].name : "UNKNOWN_STATUS";
}

const char *tom_rule_citation(enum tom_status s)
{
    return (unsigned)s < TOM_STATUS_COUNT ? rules[s].cite : 0;
}

#else
typedef int snap_timer_offset_model_gate_off;
#endif /* SNAP_PLATFORM_SP11 */
