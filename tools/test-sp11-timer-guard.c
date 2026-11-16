/* SPDX-License-Identifier: MIT */
/* Host test of compat/sp11-timer-guard.h, the SP11 monitor's CNTHCTL_EL2 read-back and CNTVOFF_EL2 refusals.
 * tools/test-sp11-timer-guard.py builds it (address and undefined-behaviour sanitizers), runs it, and builds it again
 * against weakened copies of the header, each of which must make it fail.
 *
 * The header's own code runs here, not a copy of it: the two register accessors it lets a host replace are bound to a
 * model of one CPU. A world is that CPU: CNTHCTL_EL2 with the bits that take a write (a bit outside the mask keeps
 * its value, which is how a RES0 or write-ignored bit behaves), and CNTVOFF_EL2. Every scenario first does what
 * probes/xnuboot/prepare.c does to the register (guest_value below is its expression), then calls the guard.
 *
 * The expectations share nothing with the header: the oracle takes bits with division and remainder, and each
 * scenario states its verdict as a literal. A wrong shift or mask in the header cannot cancel against a copy of itself.
 *
 *   test-sp11-timer-guard          self-test; prints PASS
 *
 * Every register value here is made up. None is a measurement of any machine; whether the traps work on a Surface Pro
 * 11 is UNKNOWN (docs/SP11-MONITOR-PORT.md section 6.4) and nothing here changes that. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CHECK(x) \
    do { \
        if (!(x)) { \
            fprintf(stderr, "%s:%d CHECK(%s) failed\n", __FILE__, __LINE__, #x); \
            exit(1); \
        } \
    } while (0)

enum world_reg { WORLD_cnthctl_el2, WORLD_cntvoff_el2 };

struct world {
    uint64_t cnthctl;
    uint64_t writable;      /* the bits of CNTHCTL_EL2 that take a write */
    uint64_t cntvoff;
    unsigned reads[2];
    unsigned cnthctl_writes;
    uint64_t written[4];    /* what was written to CNTHCTL_EL2, in order */
    unsigned cntvoff_writes;
};

static struct world W;

static uint64_t world_read(enum world_reg reg)
{
    W.reads[reg]++;
    return reg == WORLD_cnthctl_el2 ? W.cnthctl : W.cntvoff;
}

static void world_write(enum world_reg reg, uint64_t value)
{
    if (reg == WORLD_cnthctl_el2) {
        W.cnthctl = (W.cnthctl & ~W.writable) | (value & W.writable);
        if (W.cnthctl_writes < 4)
            W.written[W.cnthctl_writes] = value;
        W.cnthctl_writes++;
    } else {
        W.cntvoff_writes++;
        W.cntvoff = value;
    }
}

#define SNAP_SP11_SYSREG_READ(reg, out) ((out) = world_read(WORLD_##reg))
#define SNAP_SP11_SYSREG_WRITE_ISB(reg, value) world_write(WORLD_##reg, (value))

#include "sp11-timer-guard.h"

/* What probes/xnuboot/prepare.c writes: q1n1's value without EL1PCTEN and EL1PCEN, with EL1TVCT and EL1TVT. */
static uint64_t guest_value(uint64_t saved)
{
    return (saved & ~UINT64_C(3)) | (UINT64_C(1) << 14) | (UINT64_C(1) << 13);
}

/* The verdict from first principles: bit values by division and remainder, no masks. */
static int64_t oracle(uint64_t cnthctl, uint64_t cntvoff)
{
    uint64_t bit13 = (cnthctl / 8192) % 2, bit14 = (cnthctl / 16384) % 2;

    if (bit13 == 0 || bit14 == 0)
        return -147;
    if (cntvoff != 0)
        return -148;
    return 0;
}

#define ALL (~UINT64_C(0))

struct scenario {
    const char *name;
    uint64_t saved;         /* q1n1's CNTHCTL_EL2 on this CPU */
    uint64_t writable;
    uint64_t cntvoff;
    int64_t want;
};

/* A world that holds a refusal's registers must also give the guest value back: see the restore checks below. */
static const struct scenario scenarios[] = {
    /* the traps stick and the offset is zero: the launch goes on */
    {"traps stick, q1n1 had 0x3 (the unit's handoff value)", 0x3, ALL, 0, 0},
    {"traps stick, q1n1 had nothing set", 0, ALL, 0, 0},
    {"traps stick, q1n1 already had both set", 0x6003, ALL, 0, 0},
    {"traps stick, every other bit set", ALL, ALL, 0, 0},
    {"only the two trap bits and the two access bits take a write", 0x3, 0x6003, 0, 0},
    /* the write did not stick: inert bits */
    {"EL1TVT and EL1TVCT do not stick", 0x3, ~UINT64_C(0x6000), 0, -147},
    {"EL1TVT alone does not stick", 0x3, ~UINT64_C(0x2000), 0, -147},
    {"EL1TVCT alone does not stick", 0x3, ~UINT64_C(0x4000), 0, -147},
    {"no bit of the register takes a write", 0x3, 0, 0, -147},
    {"the whole register ignores writes and q1n1 had it clear", 0, 0, 0, -147},
    /* the offset is not zero */
    {"traps stick, CNTVOFF_EL2 is 1", 0x3, ALL, 1, -148},
    {"traps stick, CNTVOFF_EL2 is 0xffffffff", 0x3, ALL, 0xffffffff, -148},
    {"traps stick, CNTVOFF_EL2 is 1 << 32", 0x3, ALL, UINT64_C(1) << 32, -148},
    {"traps stick, CNTVOFF_EL2 is 1 << 63", 0x3, ALL, UINT64_C(1) << 63, -148},
    {"traps stick, CNTVOFF_EL2 is all ones", 0x3, ALL, ALL, -148},
    /* both: the CNTHCTL refusal wins, the record keeps the offset */
    {"bits inert and CNTVOFF_EL2 is 5", 0x3, ~UINT64_C(0x6000), 5, -147},
    {"nothing writable and CNTVOFF_EL2 is all ones", 0, 0, ALL, -147},
    /* KNOWN LIMIT, not a defect: bits that read set and do nothing look like bits that work. Only an EL1 round trip
     * (T-HW2) can tell them apart, which is why the guard's header says it is never evidence that the traps work. */
    {"LIMIT: bits read set, writes ignored (stuck but inert)", 0x6003, 0, 0, 0},
};

static int64_t run_scenario(const struct scenario *s, struct snap_sp11_timer_view *seen, uint64_t *before)
{
    memset(&W, 0, sizeof W);
    W.cnthctl = s->saved;
    W.writable = s->writable;
    W.cntvoff = s->cntvoff;
    world_write(WORLD_cnthctl_el2, guest_value(s->saved));      /* the monitor's own write */
    memset(W.reads, 0, sizeof W.reads);
    W.cnthctl_writes = 0;
    memset(W.written, 0, sizeof W.written);
    *before = W.cnthctl;
    seen->cnthctl = seen->cntvoff = 0xdeadbeefdeadbeefULL;       /* a guard that stores nothing leaves this */
    return snap_sp11_timer_guard(s->saved, seen);
}

static void check_scenarios(void)
{
    unsigned i;

    for (i = 0; i < sizeof scenarios / sizeof scenarios[0]; i++) {
        const struct scenario *s = &scenarios[i];
        struct snap_sp11_timer_view seen;
        uint64_t before;
        int64_t got = run_scenario(s, &seen, &before);

        if (got != s->want)
            fprintf(stderr, "scenario %u (%s): status %lld, wanted %lld\n", i, s->name, (long long)got,
                    (long long)s->want);
        CHECK(got == s->want);
        /* what the guard saw is what the CPU held when it looked */
        CHECK(seen.cnthctl == before);
        CHECK(seen.cntvoff == s->cntvoff);
        CHECK(W.reads[WORLD_cnthctl_el2] == 1 && W.reads[WORLD_cntvoff_el2] == 1);
        CHECK(W.cntvoff_writes == 0);           /* the monitor never programs the offset */
        if (s->want == 0) {
            CHECK(W.cnthctl_writes == 0);       /* a launch that goes on leaves the registers alone */
            CHECK(W.cnthctl == before);
        } else {
            /* exactly one restore, of q1n1's value (not the guest value, not the read-back), and a negative status */
            CHECK(got < 0);
            CHECK(W.cnthctl_writes == 1 && W.written[0] == s->saved);
            CHECK(W.cnthctl == ((before & ~s->writable) | (s->saved & s->writable)));
            if (s->writable == ALL)
                CHECK(W.cnthctl == s->saved);   /* where the register obeys, the guest value does not outlive a refusal */
        }
    }
}

static void check_oracle_sweep(void)
{
    static const uint64_t background[] = {0, ALL & ~UINT64_C(0x6000), UINT64_C(0x5555555555555555),
                                          UINT64_C(0xaaaaaaaaaaaaaaaa), 0x3, 0x1000, 0x8000};
    static const uint64_t offsets[] = {0, 1, 2, 0xffff, UINT64_C(1) << 32, UINT64_C(1) << 63, ALL};
    unsigned b, o, trap;
    uint64_t state = 0x5350313154494d52ULL;
    unsigned n;

    for (b = 0; b < sizeof background / sizeof background[0]; b++)
        for (trap = 0; trap < 4; trap++)
            for (o = 0; o < sizeof offsets / sizeof offsets[0]; o++) {
                uint64_t cnthctl = (background[b] & ~UINT64_C(0x6000)) | ((uint64_t)(trap & 1) << 13) |
                                   ((uint64_t)(trap >> 1) << 14);
                struct snap_sp11_timer_view seen;

                CHECK(snap_sp11_timer_decide(cnthctl, offsets[o]) == oracle(cnthctl, offsets[o]));
                memset(&W, 0, sizeof W);
                W.cnthctl = cnthctl;
                W.writable = ALL;
                W.cntvoff = offsets[o];
                CHECK(snap_sp11_timer_guard(0x3, &seen) == oracle(cnthctl, offsets[o]));
            }
    /* xorshift64*: a fixed stream, so a failure reproduces */
    for (n = 0; n < 200000; n++) {
        uint64_t cnthctl, cntvoff;

        state ^= state >> 12;
        state ^= state << 25;
        state ^= state >> 27;
        cnthctl = state * UINT64_C(2685821657736338717);
        state ^= state >> 12;
        state ^= state << 25;
        state ^= state >> 27;
        cntvoff = (n & 1) ? 0 : state * UINT64_C(2685821657736338717);
        CHECK(snap_sp11_timer_decide(cnthctl, cntvoff) == oracle(cnthctl, cntvoff));
    }
}

static void check_constants(void)
{
    CHECK(SNAP_SP11_TIMER_STATUS_CNTHCTL == -147);
    CHECK(SNAP_SP11_TIMER_STATUS_CNTVOFF == -148);
    CHECK(SNAP_SP11_CNTHCTL_EL1TVT == 8192);        /* bit 13 */
    CHECK(SNAP_SP11_CNTHCTL_EL1TVCT == 16384);      /* bit 14 */
    CHECK(SNAP_SP11_CNTHCTL_TRAPS == 8192 + 16384);
    CHECK(SNAP_SP11_EVENT_TIMER == 0x5443);
}

int main(void)
{
    check_constants();
    check_scenarios();
    check_oracle_sweep();
    printf("%zu scenarios, 200000 random and %d structured decisions\n", sizeof scenarios / sizeof scenarios[0],
           7 * 4 * 7);
    printf("PASS\n");
    return 0;
}
