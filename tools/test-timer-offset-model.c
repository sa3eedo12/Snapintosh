/* SPDX-License-Identifier: MIT */
/* compat/timer-offset-model.{h,c}, natively, linked with the A16 monitor's own
 * compat/clock.c so the model is checked against the code it re-derives:
 *
 *   cc -std=gnu11 -O1 -g -Wall -Wextra -Werror -fsanitize=address,undefined \
 *      -DSNAP_PLATFORM_SP11=1 -Icompat tools/test-timer-offset-model.c \
 *      compat/timer-offset-model.c compat/clock.c -o build/test-timer-offset-model
 *
 * With --stdin it answers one command per line for the Python oracle, which
 * recomputes every answer with arbitrary-precision integers. */
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "clock.h"
#include "timer-offset-model.h"

static unsigned long checks;
#define CHECK(x) do { checks++; if (!(x)) { \
    fprintf(stderr, "%s:%d: CHECK(%s) failed\n", __FILE__, __LINE__, #x); exit(1); } } while (0)

static uint64_t rng_state = UINT64_C(0x9e3779b97f4a7c15);
static uint64_t rnd(void)
{
    rng_state ^= rng_state >> 12; rng_state ^= rng_state << 25; rng_state ^= rng_state >> 27;
    return rng_state * UINT64_C(0x2545f4914f6cdd1d);
}

static const struct tom_params SP11 = { 19200000, 24000000 };       /* host and guest Hz */
#define EDGE_COUNT 14
static const uint64_t EDGES[EDGE_COUNT] = {
    0, 1, 2, 3, UINT64_C(19199999), UINT64_C(19200000), UINT64_C(1) << 32, (UINT64_C(1) << 63) - 1,
    UINT64_C(1) << 63, UINT64_C(14757395258967641292), UINT64_C(14757395258967641293), UINT64_MAX - 1, UINT64_MAX,
    UINT64_C(0x100000000) - 1,
};

/* --- conversions ---------------------------------------------------------------- */
static void test_conversions(void)
{
    uint64_t g, h;
    /* 19.2 MHz to 24 MHz is a factor of 1.25: the count rounds down, the deadline up. */
    CHECK(tom_count_to_guest(&SP11, 19200000, &g) == TOM_OK && g == 24000000);
    CHECK(tom_count_to_guest(&SP11, 1, &g) == TOM_OK && g == 1);
    CHECK(tom_count_to_guest(&SP11, 3, &g) == TOM_OK && g == 3);          /* 3.75 down */
    CHECK(tom_guest_to_host_ceil(&SP11, 1, &h) == TOM_OK && h == 1);      /* 0.8 up */
    CHECK(tom_guest_to_host_ceil(&SP11, 5, &h) == TOM_OK && h == 4);      /* exactly 4 */
    CHECK(tom_guest_to_host_ceil(&SP11, 6, &h) == TOM_OK && h == 5);      /* 4.8 up */
    /* The first count where scaling exceeds 64 bits is 14757395258967641293 (= floor(2^64 * 0.8) + 1). */
    CHECK(tom_count_to_guest(&SP11, UINT64_C(14757395258967641292), &g) == TOM_OK);
    CHECK(tom_count_to_guest(&SP11, UINT64_C(14757395258967641293), &g) == TOM_E_OVERFLOW);
    CHECK(tom_count_to_guest(&SP11, UINT64_MAX, &g) == TOM_E_OVERFLOW);
    /* The A16's formula wraps there and goes backwards: the hazard of a junk CNTVOFF_EL2. */
    CHECK(tom_legacy_sample(&SP11, UINT64_C(14757395258967641292)) > tom_legacy_sample(&SP11, UINT64_C(14757395258967641293)));
    /* Deadline: the largest guest tick count still has an exact host count here (0.8 * (2^64 - 1)), and a
     * host faster than the guest overflows instead of wrapping. */
    CHECK(tom_guest_to_host_ceil(&SP11, UINT64_MAX, &h) == TOM_OK && h == UINT64_C(14757395258967641292));
    CHECK(tom_guest_to_host_ceil(&(struct tom_params){ 24000000, 19200000 }, UINT64_MAX, &h) == TOM_E_OVERFLOW);
    /* Bad rates. */
    struct tom_params bad[] = { { 0, 24000000 }, { 19200000, 0 }, { UINT64_C(1) << 32, 24000000 }, { 19200000, UINT64_C(1) << 32 } };
    for (unsigned i = 0; i < 4; i++) {
        CHECK(tom_count_to_guest(&bad[i], 1, &g) == TOM_E_PARAMS);
        CHECK(tom_guest_to_host_ceil(&bad[i], 1, &h) == TOM_E_PARAMS);
    }
    CHECK(tom_count_to_guest(0, 1, &g) == TOM_E_PARAMS && tom_count_to_guest(&SP11, 1, 0) == TOM_E_PARAMS);
}

/* Rates other than the A16's: equal rates, a faster host, the largest legal rates. */
static const struct tom_params RATES[] = {
    { 19200000, 24000000 }, { 24000000, 19200000 }, { 1000000, 1000000 }, { 1000000000, 24000000 },
    { UINT32_MAX, UINT32_MAX }, { UINT32_MAX, 1 }, { 1, UINT32_MAX }, { 19200000, 1000000000 },
};

static void test_properties(void)
{
    for (unsigned r = 0; r < sizeof RATES / sizeof RATES[0]; r++) {
        const struct tom_params *p = &RATES[r];
        for (unsigned k = 0; k < 20000; k++) {
            uint64_t c = k < EDGE_COUNT ? EDGES[k] : (rnd() >> (rnd() % 64));
            uint64_t g, back;
            /* Exact against 128-bit arithmetic: the status is OVERFLOW exactly when the true floor needs more than 64 bits. */
            const unsigned __int128 exact = ((unsigned __int128)c * p->guest_hz) / p->host_hz;
            enum tom_status st = tom_count_to_guest(p, c, &g);
            CHECK((st == TOM_OK) == (exact <= UINT64_MAX));
            const unsigned __int128 exact_up = ((unsigned __int128)c * p->host_hz + p->guest_hz - 1) / p->guest_hz;
            CHECK((tom_guest_to_host_ceil(p, c, &back) == TOM_OK) == (exact_up <= UINT64_MAX));
            if (st != TOM_OK) continue;
            CHECK(g == (uint64_t)exact);
            /* Rounded down: the guest never runs ahead; converting back never exceeds the host count. */
            CHECK(tom_guest_to_host_ceil(p, g, &back) == TOM_OK && back <= c);
            /* And it is the largest such value: one more guest tick needs a later host tick. */
            uint64_t next;
            if (g < UINT64_MAX && tom_guest_to_host_ceil(p, g + 1, &next) == TOM_OK) CHECK(next > c);
            /* The deadline for g is exact: reached at `back`, not one tick earlier. */
            uint64_t at;
            CHECK(tom_count_to_guest(p, back, &at) == TOM_OK && at >= g);
            if (back) CHECK(tom_count_to_guest(p, back - 1, &at) == TOM_OK && at < g);
            /* Monotonic in the host count. */
            uint64_t g2;
            if (c < UINT64_MAX && tom_count_to_guest(p, c + 1, &g2) == TOM_OK) CHECK(g2 >= g);
        }
    }
}

/* Where compat/clock.c does not overflow, the model and the A16 monitor agree exactly. */
static void test_against_the_a16_formulas(void)
{
    for (unsigned r = 0; r < sizeof RATES / sizeof RATES[0]; r++) {
        struct clock_rate rate;
        CHECK(clock_rate_init(&rate, RATES[r].host_hz, RATES[r].guest_hz) == 0);
        for (unsigned k = 0; k < 20000; k++) {
            uint64_t c = k < EDGE_COUNT ? EDGES[k] : (rnd() >> (rnd() % 64));
            uint64_t g;
            const unsigned __int128 exact = ((unsigned __int128)c * RATES[r].guest_hz) / RATES[r].host_hz;
            if (tom_count_to_guest(&RATES[r], c, &g) == TOM_OK) {
                CHECK(clock_rate_sample(&rate, c) == g);
            } else {
                /* The A16 code wraps where the model refuses: its result is the true floor modulo 2^64. */
                CHECK(clock_rate_sample(&rate, c) == (uint64_t)exact);
            }
            /* clock_rate_wait: the earliest physical tick that reaches the deadline. */
            const uint64_t now = rnd() >> (8 + rnd() % 48);
            const uint64_t sample = clock_rate_sample(&rate, now);
            if (tom_count_to_guest(&RATES[r], now, &g) != TOM_OK) continue;
            const uint64_t deadline = sample + (rnd() >> (20 + rnd() % 40));
            if (deadline < sample) continue;
            const uint64_t budget = UINT64_MAX / 2;
            uint64_t need, wait = clock_rate_wait(&rate, now, deadline, budget);
            if (tom_guest_to_host_ceil(&RATES[r], deadline, &need) != TOM_OK) continue;
            uint64_t want = need > now ? need - now : 0;
            /* A wait longer than the budget is reported as the budget. */
            CHECK(wait == (want > budget ? budget : want));
        }
    }
}

/* --- admission ------------------------------------------------------------------- */
static struct tom_cpu_obs good(void)
{
    return (struct tom_cpu_obs){ .cntfrq = 19200000, .voff_incoming = 0, .voff_written = 0, .voff_readback = 0,
                                 .voff_written_valid = 1, .ecv_level = 2, .e2h = 0 };
}

static void test_admission(void)
{
    struct tom_cpu_obs obs[TOM_MAX_CPUS];
    size_t bad;
    for (unsigned i = 0; i < 12; i++) obs[i] = good();
    const struct tom_policy legacy = { TOM_BASE_CNTVCT, 0 }, epoch = { TOM_BASE_CNTPCT_EPOCH, 0 };
    CHECK(tom_admit(&SP11, &legacy, obs, 12, &bad) == TOM_OK && bad == 12);
    CHECK(tom_admit(&SP11, &epoch, obs, 12, &bad) == TOM_OK);

    /* A write-then-read-back mismatch on any one CPU fails closed, whichever base is chosen. */
    for (unsigned cpu = 0; cpu < 12; cpu++) {
        obs[cpu].voff_readback = 0x1000;
        CHECK(tom_admit(&SP11, &legacy, obs, 12, &bad) == TOM_E_CNTVOFF_READBACK && bad == cpu);
        CHECK(tom_admit(&SP11, &epoch, obs, 12, &bad) == TOM_E_CNTVOFF_READBACK && bad == cpu);
        obs[cpu].voff_readback = 0;
    }
    /* A register that reads back something else than what was written (the written value is not zero). */
    obs[3].voff_written = UINT64_C(0x0000fedcba987654);
    CHECK(tom_admit(&SP11, &epoch, obs, 12, &bad) == TOM_E_CNTVOFF_READBACK && bad == 3);
    obs[3].voff_readback = obs[3].voff_written;
    CHECK(tom_admit(&SP11, &epoch, obs, 12, &bad) == TOM_OK);      /* a working non-zero offset is fine for the epoch base */
    /* ... but the legacy base relies on the offsets, and one CPU differs from the others. */
    CHECK(tom_admit(&SP11, &legacy, obs, 12, &bad) == TOM_E_CNTVOFF_SKEW);
    const struct tom_policy loose = { TOM_BASE_CNTVCT, UINT64_MAX };
    CHECK(tom_admit(&SP11, &loose, obs, 12, &bad) == TOM_OK);
    /* The skew is measured in guest ticks: 4 host ticks are 5 guest ticks. */
    for (unsigned i = 0; i < 12; i++) obs[i] = good();
    obs[5].voff_written = obs[5].voff_readback = 4;
    CHECK(tom_admit(&SP11, &(struct tom_policy){ TOM_BASE_CNTVCT, 4 }, obs, 12, &bad) == TOM_E_CNTVOFF_SKEW);
    CHECK(tom_admit(&SP11, &(struct tom_policy){ TOM_BASE_CNTVCT, 5 }, obs, 12, &bad) == TOM_OK);
    /* Without a write the incoming values are what the monitor relies on (the A16 never programs it). */
    for (unsigned i = 0; i < 12; i++) { obs[i] = good(); obs[i].voff_written_valid = 0; obs[i].voff_incoming = 77; }
    CHECK(tom_admit(&SP11, &legacy, obs, 12, &bad) == TOM_OK);
    obs[11].voff_incoming = 1;
    CHECK(tom_admit(&SP11, &legacy, obs, 12, &bad) == TOM_E_CNTVOFF_SKEW);
    CHECK(tom_admit(&SP11, &epoch, obs, 12, &bad) == TOM_OK);        /* unequal incoming offsets do not matter to the epoch base */
    /* With E2H set the EL2 read ignores the offset: skew is not a concern. */
    for (unsigned i = 0; i < 12; i++) { obs[i].e2h = 1; }
    CHECK(tom_admit(&SP11, &legacy, obs, 12, &bad) == TOM_OK);
    /* CPUs that disagree on E2H return different counts. */
    obs[7].e2h = 0;
    CHECK(tom_admit(&SP11, &legacy, obs, 12, &bad) == TOM_E_E2H_MIX && bad == 7);
    CHECK(tom_admit(&SP11, &epoch, obs, 12, &bad) == TOM_E_E2H_MIX);
    for (unsigned i = 0; i < 12; i++) obs[i] = good();
    /* ECV: none cannot scale; level 3 does not exist. */
    obs[2].ecv_level = 0;
    CHECK(tom_admit(&SP11, &epoch, obs, 12, &bad) == TOM_E_NO_ECV && bad == 2);
    CHECK(tom_admit(&(struct tom_params){ 19200000, 19200000 }, &epoch, obs, 12, &bad) == TOM_OK);   /* equal rates: nothing to scale */
    obs[2].ecv_level = 3;
    CHECK(tom_admit(&SP11, &epoch, obs, 12, &bad) == TOM_E_ECV_RESERVED && bad == 2);
    obs[2] = good();
    /* CNTFRQ_EL0 must be the declared host rate on every CPU. */
    obs[9].cntfrq = 24000000;
    CHECK(tom_admit(&SP11, &epoch, obs, 12, &bad) == TOM_E_FREQ && bad == 9);
    obs[9].cntfrq = 0;
    CHECK(tom_admit(&SP11, &epoch, obs, 12, &bad) == TOM_E_FREQ);
    obs[9] = good();
    /* CNTPOFF_EL2: only at ECV level 2, and it must read back. */
    obs[1].cntpoff_tested = 1; obs[1].cntpoff_written = 99; obs[1].cntpoff_readback = 99;
    CHECK(tom_admit(&SP11, &epoch, obs, 12, &bad) == TOM_OK);
    obs[1].cntpoff_readback = 0;
    CHECK(tom_admit(&SP11, &epoch, obs, 12, &bad) == TOM_E_CNTPOFF_READBACK && bad == 1);
    obs[1].cntpoff_readback = 99; obs[1].ecv_level = 1;
    CHECK(tom_admit(&SP11, &epoch, obs, 12, &bad) == TOM_E_CNTPOFF_ABSENT && bad == 1);
    /* Shape errors. */
    obs[1] = good();
    CHECK(tom_admit(&SP11, &epoch, obs, 0, &bad) == TOM_E_NO_CPUS && tom_admit(&SP11, &epoch, obs, TOM_MAX_CPUS + 1, &bad) == TOM_E_NO_CPUS);
    CHECK(tom_admit(0, &epoch, obs, 1, &bad) == TOM_E_PARAMS && tom_admit(&SP11, 0, obs, 1, &bad) == TOM_E_PARAMS &&
          tom_admit(&SP11, &epoch, 0, 1, &bad) == TOM_E_PARAMS);
    CHECK(tom_admit(&SP11, &(struct tom_policy){ 9, 0 }, obs, 1, &bad) == TOM_E_PARAMS);
}

/* --- the A16 relocation assumptions, re-derived -----------------------------------------
 * compat/boot-relocate.h requires that the CPU that runs vCPU 0 has the same CNTVOFF_EL2 and CNTFRQ_EL0 as q1n1's
 * CPU, and probes/xnuboot/cpu_boot.c:182-187 checks exactly that (VERIFIED by reading). The check compares the values
 * found on arrival; it never writes CNTVOFF_EL2, so it cannot see a register that does not hold what is written, and
 * it says nothing about the E2H regime that decides whether an EL2 read of CNTVCT_EL0 applies the offset at all. */
static int a16_relocation_clock_ok(const struct tom_cpu_obs *boot, const struct tom_cpu_obs *target, uint64_t host_hz)
{
    return target->voff_incoming == boot->voff_incoming && target->cntfrq == boot->cntfrq && target->cntfrq == host_hz;
}

static void test_relocation_assumptions(void)
{
    const struct tom_policy legacy = { TOM_BASE_CNTVCT, 0 }, epoch = { TOM_BASE_CNTPCT_EPOCH, 0 };
    struct tom_cpu_obs pair[2];
    size_t bad;

    /* Where CNTVOFF_EL2 behaves, the A16 check and the model agree. */
    pair[0] = good(); pair[1] = good();
    CHECK(a16_relocation_clock_ok(&pair[0], &pair[1], SP11.host_hz));
    CHECK(tom_admit(&SP11, &legacy, pair, 2, &bad) == TOM_OK && tom_admit(&SP11, &epoch, pair, 2, &bad) == TOM_OK);

    /* A different incoming offset on the target: the A16 refuses; the legacy base would skew, the epoch base does not care. */
    pair[1] = good(); pair[1].voff_written_valid = 0; pair[0].voff_written_valid = 0; pair[1].voff_incoming = 5;
    CHECK(!a16_relocation_clock_ok(&pair[0], &pair[1], SP11.host_hz));
    CHECK(tom_admit(&SP11, &legacy, pair, 2, &bad) == TOM_E_CNTVOFF_SKEW);
    CHECK(tom_admit(&SP11, &epoch, pair, 2, &bad) == TOM_OK);

    /* Injected: equal on arrival, but the target's register is stuck. The A16 check cannot see it; the model
     * refuses it for either base, naming the target. */
    pair[0] = good(); pair[1] = good();
    pair[1].voff_written = UINT64_C(0x1234); pair[1].voff_readback = 0;
    CHECK(a16_relocation_clock_ok(&pair[0], &pair[1], SP11.host_hz));
    CHECK(tom_admit(&SP11, &legacy, pair, 2, &bad) == TOM_E_CNTVOFF_READBACK && bad == 1);
    CHECK(tom_admit(&SP11, &epoch, pair, 2, &bad) == TOM_E_CNTVOFF_READBACK && bad == 1);
    /* The same on q1n1's CPU. */
    pair[0].voff_written = UINT64_C(0x1234); pair[0].voff_readback = UINT64_C(0x1235); pair[1] = good();
    CHECK(a16_relocation_clock_ok(&pair[0], &pair[1], SP11.host_hz));
    CHECK(tom_admit(&SP11, &epoch, pair, 2, &bad) == TOM_E_CNTVOFF_READBACK && bad == 0);

    /* Injected: the same junk offset on both CPUs, above any count the counter reaches soon. The A16 check passes;
     * the legacy base would hand the guest a wrapped clock, and the model refuses to sample it. */
    pair[0] = good(); pair[1] = good();
    for (unsigned i = 0; i < 2; i++) { pair[i].voff_written_valid = 0; pair[i].voff_incoming = UINT64_C(0xf000000000000000); }
    CHECK(a16_relocation_clock_ok(&pair[0], &pair[1], SP11.host_hz));
    CHECK(tom_admit(&SP11, &legacy, pair, 2, &bad) == TOM_OK);                 /* equal: no skew to see */
    uint64_t voff[2] = { pair[0].voff_incoming, pair[1].voff_incoming }, g, wrapped;
    struct tom_clock c;
    CHECK(tom_clock_init(&c, &SP11, TOM_BASE_CNTVCT, 0, 0, voff, 2) == TOM_OK);
    CHECK(tom_sample(&c, 1, 19200000, &g) == TOM_E_OFFSET_RANGE);
    /* What the hardware subtraction and the A16 conversion would produce for that read: a guest clock about 2^60
     * ticks ahead of the truth, where the model fails closed instead. */
    wrapped = tom_legacy_sample(&SP11, (uint64_t)19200000 - voff[1]);
    CHECK(wrapped > (UINT64_C(1) << 59));
    CHECK(tom_clock_init(&c, &SP11, TOM_BASE_CNTPCT_EPOCH, 0, 0, voff, 2) == TOM_OK);
    CHECK(tom_sample(&c, 1, 19200000, &g) == TOM_OK && g == 24000000);        /* the epoch base never reads CNTVOFF_EL2 */

    /* Injected: the CPUs run different EL2 regimes, so only one of them applies the offset. The A16 check is blind to it. */
    pair[0] = good(); pair[1] = good(); pair[1].e2h = 1;
    CHECK(a16_relocation_clock_ok(&pair[0], &pair[1], SP11.host_hz));
    CHECK(tom_admit(&SP11, &legacy, pair, 2, &bad) == TOM_E_E2H_MIX && bad == 1);
    CHECK(tom_admit(&SP11, &epoch, pair, 2, &bad) == TOM_E_E2H_MIX && bad == 1);

    /* CNTFRQ_EL0 differing is caught by both. */
    pair[0] = good(); pair[1] = good(); pair[1].cntfrq = 24000000;
    CHECK(!a16_relocation_clock_ok(&pair[0], &pair[1], SP11.host_hz));
    CHECK(tom_admit(&SP11, &legacy, pair, 2, &bad) == TOM_E_FREQ && bad == 1);
}

/* --- the running clock ------------------------------------------------------------ */
static void test_clock(void)
{
    uint64_t voff[12], g, g2, p;
    memset(voff, 0, sizeof voff);
    struct tom_clock c;
    CHECK(tom_clock_init(&c, &SP11, TOM_BASE_CNTVCT, 0, 0, voff, 12) == TOM_OK);
    CHECK(tom_sample(&c, 0, 19200000, &g) == TOM_OK && g == 24000000);
    CHECK(tom_sample(&c, 0, 19200001, &g) == TOM_OK && g == 24000001);
    /* Equal offsets give equal clocks on every CPU. */
    CHECK(tom_skew(&c, 0, 11, 123456789, &p) == TOM_OK && p == 0);
    /* An offset on one CPU skews it by the converted difference, to within a tick. */
    voff[4] = 1920;                                      /* 100 us at 19.2 MHz */
    CHECK(tom_clock_init(&c, &SP11, TOM_BASE_CNTVCT, 0, 0, voff, 12) == TOM_OK);
    CHECK(tom_skew(&c, 0, 4, 19200000, &p) == TOM_OK && p == 2400);
    /* Negative guest time is refused, not wrapped: an offset above the physical count. */
    CHECK(tom_sample(&c, 4, 1919, &g) == TOM_E_OFFSET_RANGE);
    CHECK(tom_sample(&c, 4, 1920, &g) == TOM_OK && g == 0);
    voff[1] = UINT64_MAX;
    CHECK(tom_clock_init(&c, &SP11, TOM_BASE_CNTVCT, 0, 0, voff, 12) == TOM_OK);
    CHECK(tom_sample(&c, 1, UINT64_MAX - 1, &g) == TOM_E_OFFSET_RANGE);
    CHECK(tom_sample(&c, 1, UINT64_MAX, &g) == TOM_OK && g == 0);
    /* An offset just below the limit: the converted value stays small. */
    voff[1] = UINT64_MAX - 100;
    CHECK(tom_clock_init(&c, &SP11, TOM_BASE_CNTVCT, 0, 0, voff, 12) == TOM_OK);
    CHECK(tom_sample(&c, 1, UINT64_MAX, &g) == TOM_OK && g == 125);
    /* A huge physical count does not wrap the product. */
    memset(voff, 0, sizeof voff);
    CHECK(tom_clock_init(&c, &SP11, TOM_BASE_CNTVCT, 0, 0, voff, 12) == TOM_OK);
    CHECK(tom_sample(&c, 0, UINT64_C(14757395258967641293), &g) == TOM_E_OVERFLOW);
    /* Monotonic per CPU while the physical counter is. */
    CHECK(tom_sample(&c, 2, 1000, &g) == TOM_OK && tom_sample(&c, 2, 1001, &g2) == TOM_OK && g2 >= g);
    CHECK(tom_sample(&c, 2, 1001, &g2) == TOM_OK && tom_sample(&c, 2, 999, &g) == TOM_E_NOT_MONOTONIC);
    /* A rewritten offset can send guest time backwards; the model notices. */
    CHECK(tom_clock_init(&c, &SP11, TOM_BASE_CNTVCT, 0, 0, voff, 12) == TOM_OK);
    CHECK(tom_sample(&c, 3, 10000, &g) == TOM_OK);
    tom_offset_changed(&c, 3, 5000);
    CHECK(tom_sample(&c, 3, 10001, &g) == TOM_E_NOT_MONOTONIC);
    /* E2H set: an EL2 read has no offset, so the offsets are irrelevant. */
    voff[0] = 123456;
    CHECK(tom_clock_init(&c, &SP11, TOM_BASE_CNTVCT, 1, 0, voff, 12) == TOM_OK);
    CHECK(tom_sample(&c, 0, 100, &g) == TOM_OK && g == 125);
    /* The epoch base ignores CNTVOFF_EL2 altogether and starts the guest at zero. */
    CHECK(tom_clock_init(&c, &SP11, TOM_BASE_CNTPCT_EPOCH, 0, 5000, voff, 12) == TOM_OK);
    CHECK(tom_sample(&c, 0, 5000, &g) == TOM_OK && g == 0);
    CHECK(tom_sample(&c, 7, 5000 + 19200000, &g) == TOM_OK && g == 24000000);
    CHECK(tom_sample(&c, 0, 4999, &g) == TOM_E_OFFSET_RANGE);
    CHECK(tom_skew(&c, 0, 7, 9999999, &p) == TOM_OK && p == 0);
    /* Shape errors. */
    CHECK(tom_sample(&c, 12, 5000, &g) == TOM_E_NO_CPUS);
    CHECK(tom_clock_init(&c, &SP11, (enum tom_base)5, 0, 0, voff, 12) == TOM_E_PARAMS);
    CHECK(tom_clock_init(&c, &SP11, TOM_BASE_CNTVCT, 0, 0, voff, 0) == TOM_E_NO_CPUS);
    CHECK(tom_clock_init(&c, &SP11, TOM_BASE_CNTVCT, 0, 0, voff, TOM_MAX_CPUS + 1) == TOM_E_NO_CPUS);
    CHECK(tom_clock_init(&c, &SP11, TOM_BASE_CNTVCT, 0, 0, 0, 1) == TOM_E_PARAMS);
}

static void test_deadlines(void)
{
    uint64_t voff[12] = { 0 }, at, g, g1;
    struct tom_clock c;
    for (unsigned k = 0; k < 3; k++) {
        voff[3] = k == 0 ? 0 : (k == 1 ? 123456789 : UINT64_C(1) << 40);
        CHECK(tom_clock_init(&c, &SP11, k == 2 ? TOM_BASE_CNTPCT_EPOCH : TOM_BASE_CNTVCT, 0, k == 2 ? 777 : 0, voff, 12) == TOM_OK);
        for (unsigned i = 0; i < 20000; i++) {
            uint64_t cval = rnd() >> (4 + rnd() % 60);
            if (tom_deadline(&c, 3, cval, &at) != TOM_OK) continue;
            /* The timer fires when the guest count has reached the compare value, and not before. */
            struct tom_clock probe = c;
            probe.last[3] = 0;
            CHECK(tom_sample(&probe, 3, at, &g) == TOM_OK && g >= cval);
            probe.last[3] = 0;
            /* A compare value of zero is reached at the origin itself; there is no earlier tick. */
            if (cval) { CHECK(tom_sample(&probe, 3, at - 1, &g1) == TOM_OK && g1 < cval); }
        }
    }
    /* Overflow instead of wrap. */
    CHECK(tom_clock_init(&c, &SP11, TOM_BASE_CNTVCT, 0, 0, voff, 12) == TOM_OK);
    voff[3] = 0;
    CHECK(tom_clock_init(&c, &SP11, TOM_BASE_CNTVCT, 0, 0, voff, 12) == TOM_OK);
    CHECK(tom_deadline(&c, 3, UINT64_MAX, &at) == TOM_OK && at == UINT64_C(14757395258967641292));
    voff[3] = UINT64_MAX - 5;
    CHECK(tom_clock_init(&c, &SP11, TOM_BASE_CNTVCT, 0, 0, voff, 12) == TOM_OK);
    CHECK(tom_deadline(&c, 3, 100, &at) == TOM_E_OVERFLOW);
    CHECK(tom_deadline(&c, 12, 100, &at) == TOM_E_NO_CPUS);
}

/* --- with and without ECV ------------------------------------------------------------ */
static void test_guest_view(void)
{
    uint64_t voff[2] = { 1000, 1000 }, n, hz;
    struct tom_clock c;
    CHECK(tom_clock_init(&c, &SP11, TOM_BASE_CNTVCT, 0, 0, voff, 2) == TOM_OK);
    /* ECV: the monitor traps and converts; the guest sees 24 MHz. */
    CHECK(tom_guest_view(&c, 1, 0, 19201000, &n, &hz) == TOM_OK && n == 24000000 && hz == 24000000);
    CHECK(tom_guest_view(&c, 2, 1, 19201000, &n, &hz) == TOM_OK && n == 24000000 && hz == 24000000);
    /* No ECV and different rates: the guest would read an unscaled 19.2 MHz counter. */
    CHECK(tom_guest_view(&c, 0, 0, 19201000, &n, &hz) == TOM_E_NO_ECV);
    /* No ECV and equal rates: the hardware offset is all there is, so it must work. */
    struct tom_params same = { 19200000, 19200000 };
    CHECK(tom_clock_init(&c, &same, TOM_BASE_CNTVCT, 0, 0, voff, 2) == TOM_OK);
    CHECK(tom_guest_view(&c, 0, 0, 19201000, &n, &hz) == TOM_OK && n == 19200000 && hz == 19200000);
    CHECK(tom_guest_view(&c, 0, 0, 999, &n, &hz) == TOM_E_OFFSET_RANGE);
    CHECK(tom_guest_view(&c, 3, 0, 5, &n, &hz) == TOM_E_ECV_RESERVED && tom_guest_view(&c, 1, 5, 5, &n, &hz) == TOM_E_NO_CPUS);
}

static void test_rules(void)
{
    CHECK(tom_rule_citation(TOM_OK) == 0);
    for (unsigned s = 1; s < TOM_STATUS_COUNT; s++) {
        const char *name = tom_status_name((enum tom_status)s), *cite = tom_rule_citation((enum tom_status)s);
        CHECK(name && strcmp(name, "UNKNOWN_STATUS") && cite && strlen(cite) > 20);
        CHECK(strstr(cite, "VERIFIED") || strstr(cite, "INFERRED") || strstr(cite, "model:"));
        for (unsigned t = 1; t < s; t++) CHECK(strcmp(name, tom_status_name((enum tom_status)t)));
    }
    CHECK(!strcmp(tom_status_name((enum tom_status)99), "UNKNOWN_STATUS") && !tom_rule_citation((enum tom_status)99));
}

/* --- differential mode ------------------------------------------------------------------
 *   F H G count          floor conversion        -> OK value | ERR NAME
 *   C H G ticks          ceiling conversion
 *   S H G base e2h epoch n v0..v(n-1) cpu P         sample on a fresh clock
 *   D H G base e2h epoch n v0..v(n-1) cpu cval      deadline
 *   A H G base skew n then per CPU: cntfrq vin vwv vw vrb cpt cpw cprb ecv e2h -> OK | ERR NAME index */
static int differential(void)
{
    char line[4096];
    while (fgets(line, sizeof line, stdin)) {
        char *save, *cmd = strtok_r(line, " \t\r\n", &save);
        if (!cmd) continue;
        struct tom_params p;
        unsigned long long a, b;
        char *w;
#define NEXT(var) do { w = strtok_r(0, " \t\r\n", &save); if (!w) { puts("PARSE"); goto next; } var = strtoull(w, 0, 10); } while (0)
        NEXT(a); p.host_hz = a; NEXT(b); p.guest_hz = b;
        uint64_t out;
        enum tom_status s;
        if (*cmd == 'F' || *cmd == 'C') {
            unsigned long long v;
            NEXT(v);
            s = *cmd == 'F' ? tom_count_to_guest(&p, v, &out) : tom_guest_to_host_ceil(&p, v, &out);
            if (s) printf("ERR %s\n", tom_status_name(s)); else printf("OK %" PRIu64 "\n", out);
        } else if (*cmd == 'S' || *cmd == 'D') {
            unsigned long long base, e2h, epoch, n, cpu, x;
            uint64_t voff[TOM_MAX_CPUS] = { 0 };
            NEXT(base); NEXT(e2h); NEXT(epoch); NEXT(n);
            for (unsigned long long i = 0; i < n && i < TOM_MAX_CPUS; i++) NEXT(voff[i]);
            NEXT(cpu); NEXT(x);
            struct tom_clock c;
            s = tom_clock_init(&c, &p, (enum tom_base)base, (unsigned)e2h, epoch, voff, (size_t)n);
            if (!s) s = *cmd == 'S' ? tom_sample(&c, (size_t)cpu, x, &out) : tom_deadline(&c, (size_t)cpu, x, &out);
            if (s) printf("ERR %s\n", tom_status_name(s)); else printf("OK %" PRIu64 "\n", out);
        } else if (*cmd == 'A') {
            unsigned long long base, skew, n;
            struct tom_cpu_obs obs[TOM_MAX_CPUS];
            memset(obs, 0, sizeof obs);
            NEXT(base); NEXT(skew); NEXT(n);
            for (unsigned long long i = 0; i < n && i < TOM_MAX_CPUS; i++) {
                unsigned long long cpt, ecv, e2h, vw;
                NEXT(obs[i].cntfrq); NEXT(obs[i].voff_incoming); NEXT(obs[i].voff_written); NEXT(vw);
                NEXT(obs[i].voff_readback); NEXT(cpt); NEXT(obs[i].cntpoff_written); NEXT(obs[i].cntpoff_readback);
                NEXT(ecv); NEXT(e2h);
                obs[i].voff_written_valid = (uint8_t)vw; obs[i].cntpoff_tested = (uint8_t)cpt;
                obs[i].ecv_level = (uint8_t)ecv; obs[i].e2h = (uint8_t)e2h;
            }
            struct tom_policy pol = { (uint8_t)base, skew };
            size_t bad;
            s = tom_admit(&p, &pol, obs, (size_t)n, &bad);
            printf("%s %s %zu\n", s ? "ERR" : "OK", tom_status_name(s), bad);
        } else {
            puts("PARSE");
        }
next:;
    }
    return 0;
}

int main(int argc, char **argv)
{
    if (argc > 1 && !strcmp(argv[1], "--stdin")) return differential();
    test_conversions();
    test_properties();
    test_against_the_a16_formulas();
    test_admission();
    test_relocation_assumptions();
    test_clock();
    test_deadlines();
    test_guest_view();
    test_rules();
    printf("%lu checks\nPASS\n", checks);
    return 0;
}
