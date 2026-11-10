/* SPDX-License-Identifier: MIT
 * Host model test of the SP11 thermal guard (compat/hamoa-thermal.c): synthetic TSENS register windows ->
 * guard policy -> early stop -> service stop. EXPERIMENTAL: the windows, offsets and fields are INFERRED
 * from public Linux sources (see hamoa-thermal.h), so a pass proves the policy against that model and says
 * nothing about an SP11. No hardware, serial, USB or block device is opened.
 *
 * The unit under test is included rather than linked so that its private read primitive can be driven
 * directly with addresses its own sensor list can never produce (test_refusal_before_read).
 *
 * Build (tools/test-hamoa-thermal-guard.py runs it with these flags):
 *   clang -std=gnu11 -O1 -g -Wall -Wextra -Werror -fsanitize=address,undefined -fno-sanitize-recover=all \
 *     -Icompat -Ishadow tools/test-hamoa-thermal-guard.c compat/early-stop.c compat/service-lock.c \
 *     -o build/hamoa-thermal-guard/guard-test
 * Run:  guard-test             every case
 *       guard-test --layout    report layout and enum values, for the Python decoder test
 *       guard-test --dump DIR  every case, then known states as raw report bytes under DIR
 */
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "early-stop.h"
#include "hamoa-thermal.c"
#include "service-lock.h"

#define CHECK(x) do { if (!(x)) { fprintf(stderr, "line %d: %s\n", __LINE__, #x); exit(1); } } while (0)
#define U(x) ((unsigned long long)(x))
#define R(name) HAMOA_THERMAL_##name
#define D(name) HAMOA_THERMAL_DETAIL_##name

#define HZ UINT64_C(24000000)
#define TEST_VERSION UINT32_C(0x7e570001) /* synthetic: the SP11's HW_VER is not measured */
#define CEILING_MC 80000                  /* this test's own ceiling; the SP11 value is a user decision */
#define VALID (UINT32_C(1) << 21)
#define CTRL_HEALTHY (UINT32_C(1) | (UINT32_C(0xffff) << 3) | (UINT32_C(1) << 21))
#define CODE(i, n) (300u + 10u * (i) + (n)) /* deci-degrees: 30.0 C, +1.0 C per instance, +0.1 C per id */

/* Window bases are written out from the reg properties of the four tsens nodes in hamoa.dtsi rather than
 * derived from a stride, so a wrong stride in the guard cannot be mirrored here. */
static const uint64_t SROT[4] = {UINT64_C(0x0c222000), UINT64_C(0x0c223000), UINT64_C(0x0c224000),
                                 UINT64_C(0x0c225000)};
static const uint64_t TM[4] = {UINT64_C(0x0c271000), UINT64_C(0x0c272000), UINT64_C(0x0c273000),
                               UINT64_C(0x0c274000)};
static const unsigned DEFAULT_IDS[4] = {13, 11, 11, 15}; /* sensor ids the public DT thermal zones read */

#define VERSION_ADDR(i) (SROT[i])
#define CONTROL_ADDR(i) (SROT[i] + 4)
#define CONV_ADDR(i, n) (SROT[i] + 0x60 + 4 * (uint64_t)(n))
#define STATUS_ADDR(i, n) (TM[i] + 0xa0 + 4 * (uint64_t)(n))
#define TRDY_ADDR(i) (TM[i] + 0xe4)

/* The oracle of which addresses may be read: every other address must never reach the bus. */
static int oracle_allowed(uint64_t a)
{
    unsigned i, n;

    for (i = 0; i < 4; i++) {
        if (a == VERSION_ADDR(i) || a == CONTROL_ADDR(i) || a == TRDY_ADDR(i))
            return 1;
        for (n = 0; n < 16; n++)
            if (a == CONV_ADDR(i, n) || a == STATUS_ADDR(i, n))
                return 1;
    }
    return 0;
}

struct bus {
    uint32_t version[4], control[4], trdy[4], conv[4][16], status[4][16];
    uint64_t now, step, slow; /* bus_counter(): returns now + slow, then advances now by step */
    uint64_t reads, fail_addr, fail_hits;
    uint64_t flick_addr[2], flick_left[2]; /* reads of flick_addr return VALID clear, UINT64_MAX = always */
    uint64_t hot_addr, hot_from_poll, hot_word;
};
static struct bus baseline;

/* The canaries show a write outside the state, for instance by a copy loop that trusts a caller's count. */
static struct {
    uint64_t before[4];
    struct hamoa_thermal_state state;
    uint64_t after[4];
} arena;
#define guard (arena.state)
static struct early_stop_state stop;
#define CANARY UINT64_C(0xc5c5c5c5a1a1a1a1)

static void arm_canaries(void)
{
    unsigned i;

    for (i = 0; i < 4; i++)
        arena.before[i] = arena.after[i] = CANARY;
}

static int canaries_intact(void)
{
    unsigned i;

    for (i = 0; i < 4; i++)
        if (arena.before[i] != CANARY || arena.after[i] != CANARY)
            return 0;
    return 1;
}

static void serve_status(struct bus *b, uint64_t a, uint32_t *v)
{
    unsigned i;

    for (i = 0; i < 2; i++) {
        if (a == b->flick_addr[i] && b->flick_left[i]) {
            if (b->flick_left[i] != UINT64_MAX)
                b->flick_left[i]--;
            *v &= ~VALID;
        }
    }
    if (a == b->hot_addr && guard.polls >= b->hot_from_poll)
        *v = b->hot_word;
}

static int bus_read(void *ctx, uint64_t a, uint32_t *v)
{
    struct bus *b = ctx;
    unsigned i, n;

    b->reads++;
    CHECK(oracle_allowed(a));
    if (a == b->fail_addr) {
        b->fail_hits++;
        return -1;
    }
    for (i = 0; i < 4; i++) {
        if (a == VERSION_ADDR(i)) {
            *v = b->version[i];
            return 0;
        }
        if (a == CONTROL_ADDR(i)) {
            *v = b->control[i];
            return 0;
        }
        if (a == TRDY_ADDR(i)) {
            *v = b->trdy[i];
            return 0;
        }
        for (n = 0; n < 16; n++) {
            if (a == CONV_ADDR(i, n)) {
                *v = b->conv[i][n];
                return 0;
            }
            if (a == STATUS_ADDR(i, n)) {
                *v = b->status[i][n];
                serve_status(b, a, v);
                return 0;
            }
        }
    }
    CHECK(0);
    return -1;
}

static uint64_t bus_counter(void *ctx)
{
    struct bus *b = ctx;
    uint64_t t = b->now + b->slow;

    b->now += b->step;
    return t;
}

static void make_baseline(void)
{
    unsigned i, n;

    memset(&baseline, 0, sizeof baseline);
    for (i = 0; i < 4; i++) {
        baseline.version[i] = TEST_VERSION;
        baseline.control[i] = CTRL_HEALTHY;
        baseline.trdy[i] = 1;
        for (n = 0; n < 16; n++) {
            baseline.conv[i][n] = UINT32_C(0x00010000) + 16 * i + n; /* any nonzero word: programmed */
            baseline.status[i][n] = VALID | CODE(i, n);
        }
    }
}

static void default_config(struct hamoa_thermal_config *c)
{
    hamoa_thermal_default_sensors(c);
    c->limit_mc = CEILING_MC;
    c->hw_version = TEST_VERSION;
}

static void one_sensor(struct hamoa_thermal_config *c, unsigned instance, unsigned id)
{
    default_config(c);
    c->count = 1;
    c->sensors[0].instance = (uint8_t)instance;
    c->sensors[0].id = (uint8_t)id;
}

/* A blank state and a pending guest stop request, so a thermal trip must shorten a real deadline. */
static void reset_raw(struct bus *b, uint64_t now)
{
    *b = baseline;
    b->now = now;
    memset(&arena, 0, sizeof arena);
    arm_canaries();
    memset(&stop, 0, sizeof stop);
    CHECK(early_stop_request(&stop, EARLY_STOP_SOURCE_GUEST, 1, 0, 1700, 1700, now, HZ, 0, 0, NULL) ==
          EARLY_STOP_ACCEPTED);
}

static void reset_with(struct bus *b, uint64_t now, const struct hamoa_thermal_config *c, int init_result)
{
    reset_raw(b, now);
    CHECK(hamoa_thermal_init(&guard, &stop, now, HZ, c) == init_result);
}

static void reset(struct bus *b, uint64_t now)
{
    struct hamoa_thermal_config c;

    default_config(&c);
    reset_with(b, now, &c, 0);
}

static int step_hz(struct bus *b, uint64_t hz)
{
    struct hamoa_thermal_io io = {b, bus_read, bus_counter};

    return hamoa_thermal_poll(&guard, &stop, b->now, hz, &io);
}

static int step(struct bus *b)
{
    return step_hz(b, HZ);
}

static int admit(struct bus *b)
{
    struct hamoa_thermal_io io = {b, bus_read, bus_counter};

    return hamoa_thermal_admit(&guard, &stop, HZ, &io);
}

static void admitted(const char *name, int result, uint64_t start, uint64_t end)
{
    printf("{\"admit\":\"%s\",\"result\":%d,\"reason\":%llu,\"polls\":%llu,\"invalid_samples\":%llu,"
           "\"valid\":%llu,\"elapsed_ticks\":%llu}\n",
           name, result, U(guard.reason), U(guard.polls), U(guard.invalid_samples), U(guard.valid_count),
           U(end - start));
}

/* A trip must reach the production stop path (early stop, then the service stop word) and stay final:
 * a tripped guard neither reads the bus again nor changes its verdict nor asks for a second stop. */
static void fired(struct bus *b, uint64_t reason, uint64_t detail, uint64_t where)
{
    struct guest_service_lock lock = {0};
    uint64_t reads = b->reads;

    CHECK(guard.reason == reason && guard.detail == detail && guard.location == where && !guard.ready);
    CHECK(stop.source == EARLY_STOP_SOURCE_THERMAL && stop.accepted_delay == 0 && stop.reason == reason &&
          stop.detail == where);
    CHECK(early_stop_fire(&stop, b->now + b->slow, b->now + 30 * HZ, 0));
    CHECK(guest_service_boot(&lock) == SERVICE_OK);
    guest_service_stop(&lock, 0, SERVICE_STOP_NORMAL);
    early_stop_finish(&stop, guest_service_stopped(&lock));
    CHECK(stop.outcome == EARLY_STOP_OUTCOME_STOPPED);
    CHECK(step(b) == 1 && b->reads == reads && guard.reason == reason && guard.location == where);
    CHECK(stop.duplicates == 0 && canaries_intact());
    printf("{\"reason\":%llu,\"detail\":%llu,\"where\":\"0x%llx\",\"polls\":%llu,\"reads\":%llu}\n", U(reason),
           U(detail), U(where), U(guard.polls), U(guard.reads));
}

/* ---- the register allow-list ------------------------------------------------------------------- */

static const uint64_t OUTSIDE[] = {
    0, 4, UINT64_MAX, UINT64_MAX - 3,
    SROT[0] + 8,                /* measurement period: the one SROT word the guard must never touch */
    SROT[0] + 0xc, SROT[1] + 0x5c, SROT[3] + 0xa0, /* either side of the conversion words */
    TM[2] + 0x9c, TM[2] + 0xe0, TM[2] + 0xe8,       /* either side of status and ready */
    SROT[0] + 2, STATUS_ADDR(0, 0) + 1, TRDY_ADDR(3) + 3,
    SROT[0] - 0x1000, SROT[3] + 0x1000, TM[3] + 0x1000 + 0xe4, /* no instance below 0 or above 3 */
    SROT[0] + (UINT64_C(1) << 32), TRDY_ADDR(0) + (UINT64_C(1) << 40),
    UINT64_C(0x0c22c000) + 0xa0,                   /* the A16's sensor window is not this SoC's */
};

static void test_allow_list(void)
{
    uint64_t a, allowed = 0;
    unsigned i;

    for (i = 0; i < sizeof OUTSIDE / sizeof OUTSIDE[0]; i++)
        CHECK(!hamoa_thermal_address_allowed(OUTSIDE[i]));
    /* Every byte around the windows, so no unaligned or neighbouring word slips through. */
    for (a = UINT64_C(0x0c220000); a < UINT64_C(0x0c280000); a++) {
        int expected = oracle_allowed(a);

        CHECK(hamoa_thermal_address_allowed(a) == expected);
        allowed += (uint64_t)expected;
    }
    CHECK(allowed == 4 * (3 + 16 + 16));
    for (a = UINT64_C(0x0c200000); a < UINT64_C(0x0c300000); a += 4)
        CHECK(hamoa_thermal_address_allowed(a) == oracle_allowed(a));
    for (i = 0; i < 4; i++)
        CHECK(hamoa_thermal_address_allowed(VERSION_ADDR(i)) && hamoa_thermal_address_allowed(CONTROL_ADDR(i)) &&
              hamoa_thermal_address_allowed(TRDY_ADDR(i)) && hamoa_thermal_address_allowed(CONV_ADDR(i, 15)) &&
              hamoa_thermal_address_allowed(STATUS_ADDR(i, 15)));
}

/* The allow-list is the last check before the bus. A validated sensor list can never produce an address
 * outside it, so the unit's private read primitive is driven directly: a refused address must not reach
 * the read callback at all, and must leave the failure evidence a stuck read would leave. */
static void test_refusal_before_read(void)
{
    struct hamoa_thermal_io io = {NULL, bus_read, bus_counter};
    struct bus b;
    uint32_t word;
    unsigned i;

    for (i = 0; i < sizeof OUTSIDE / sizeof OUTSIDE[0]; i++) {
        reset(&b, 0);
        io.ctx = &b;
        word = UINT32_C(0xdeadbeef);
        CHECK(read_word(&guard, &io, OUTSIDE[i], &word) == -1);
        CHECK(b.reads == 0 && word == UINT32_C(0xdeadbeef));
        CHECK(guard.location == OUTSIDE[i] && guard.reads == 1 && guard.read_failures == 1 &&
              guard.read_inflight == 1);
    }
    reset(&b, 0);
    io.ctx = &b;
    CHECK(read_word(&guard, &io, STATUS_ADDR(1, 2), &word) == 0 && word == baseline.status[1][2]);
    CHECK(b.reads == 1 && guard.read_failures == 0 && guard.read_inflight == 0);
    b.fail_addr = TRDY_ADDR(1);
    CHECK(read_word(&guard, &io, TRDY_ADDR(1), &word) == -1 && b.fail_hits == 1);
    CHECK(guard.read_failures == 1 && guard.read_inflight == 1 && guard.location == TRDY_ADDR(1));
}

/* ---- default sensor list and the refusal of an unconfigured guard -------------------------------- */

static void test_default_sensors(void)
{
    struct hamoa_thermal_config c;
    struct bus b;
    unsigned i, n, k = 0;

    memset(&c, 0xa5, sizeof c);
    hamoa_thermal_default_sensors(&c);
    CHECK(c.count == 50 && c.limit_mc == 0 && c.hw_version == 0);
    for (i = 0; i < 4; i++)
        for (n = 0; n < DEFAULT_IDS[i]; n++, k++)
            CHECK(c.sensors[k].instance == i && c.sensors[k].id == n);
    for (; k < HAMOA_THERMAL_SLOTS; k++)
        CHECK(c.sensors[k].instance == 0 && c.sensors[k].id == 0);
    hamoa_thermal_default_sensors(NULL);

    /* The list alone is not a configuration: no measured version, then no ceiling, each refuses. */
    reset_with(&b, 0, &c, 1);
    fired(&b, R(NO_SOURCE), D(NO_VERSION), 0);
    CHECK(b.reads == 0);
    c.hw_version = TEST_VERSION;
    reset_with(&b, 0, &c, 1);
    fired(&b, R(CONFIG), D(LIMIT), 0);
    CHECK(b.reads == 0);
    c.limit_mc = CEILING_MC;
    reset_with(&b, 0, &c, 0);
    CHECK(!step(&b) && guard.ready);
}

/* ---- healthy sampling, pacing and wait ----------------------------------------------------------- */

static void test_healthy(void)
{
    struct bus b;
    uint64_t reads;
    unsigned i, n, k = 0;

    reset(&b, 0);
    CHECK(guard.magic == HAMOA_THERMAL_MAGIC && guard.abi == HAMOA_THERMAL_ABI && guard.bytes == sizeof guard);
    CHECK(guard.current_mc == INT64_MIN && guard.peak_mc == INT64_MIN && guard.polls == 0 && !guard.ready);
    CHECK(guard.limit_mc == CEILING_MC && guard.hw_version == TEST_VERSION && guard.slot_count == 50);
    CHECK(!step(&b));
    CHECK(guard.ready && guard.valid_count == 50 && guard.missing_count == 0 && !guard.reason && !guard.detail);
    CHECK(guard.valid_slots == (UINT64_C(1) << 50) - 1 && guard.seen_slots == guard.valid_slots);
    CHECK(guard.current_mc == 34400 && guard.peak_mc == 34400); /* instance 3, id 14: 30.0 + 3.0 + 1.4 C */
    /* Per controller: three words (version, control, ready); per sensor: conversion and status. */
    CHECK(guard.polls == 1 && b.reads == 4 * 3 + 50 * 2 && guard.reads == b.reads && !guard.read_inflight);
    CHECK(guard.last_poll == 0 && guard.next_poll == HZ / 4 && guard.poll_ticks == 0);
    for (i = 0; i < 4; i++) {
        CHECK(guard.version[i] == TEST_VERSION && guard.control[i] == CTRL_HEALTHY && guard.trdy[i] == 1);
        for (n = 0; n < DEFAULT_IDS[i]; n++, k++) {
            CHECK(guard.slot[k] == (((uint64_t)i << 8) | n));
            CHECK(guard.raw[k] == baseline.status[i][n] && guard.conversion[k] == baseline.conv[i][n]);
            CHECK(guard.last_valid[k] == 0);
        }
    }
    for (; k < HAMOA_THERMAL_SLOTS; k++)
        CHECK(guard.slot[k] == 0 && guard.raw[k] == 0 && guard.conversion[k] == 0);

    /* A poll that is not due touches no register and keeps the verdict; wait says how long to sleep. */
    reads = b.reads;
    b.now = HZ / 8;
    CHECK(!step(&b) && b.reads == reads && guard.polls == 1 && guard.ready);
    CHECK(hamoa_thermal_wait(&guard, b.now, HZ, HZ) == HZ / 8);
    CHECK(hamoa_thermal_wait(&guard, b.now, HZ, 1000) == 1000);
    CHECK(hamoa_thermal_wait(&guard, b.now, 0, HZ) == 0 && hamoa_thermal_wait(&guard, b.now, 999, HZ) == 0);
    b.now = HZ / 4;
    CHECK(hamoa_thermal_wait(&guard, b.now, HZ, HZ) == 0);
    CHECK(!step(&b) && guard.polls == 2 && guard.max_gap_ticks == HZ / 4 && b.reads == 2 * reads);
    CHECK(hamoa_thermal_wait(NULL, 0, HZ, HZ) == 0);

    /* A value below, at and just over the ceiling's neighbours stays untouched by other sensors. */
    reset(&b, 0);
    b.status[3][14] = VALID | 345;
    CHECK(!step(&b) && guard.current_mc == 34500 && guard.peak_mc == 34500);
    b.now = HZ / 4;
    b.status[3][14] = VALID | 344;
    CHECK(!step(&b) && guard.current_mc == 34400 && guard.peak_mc == 34500);

    /* The poll counter and timestamps survive the 64-bit counter wrapping between two polls. */
    reset(&b, UINT64_MAX - HZ / 8);
    CHECK(!step(&b) && guard.ready);
    b.now += HZ / 4;
    CHECK(!step(&b) && guard.polls == 2 && guard.ready);

    /* hz at both ends of the accepted range. */
    reset(&b, 0);
    CHECK(step_hz(&b, 1000) == 0 && guard.ready);
    reset(&b, 0);
    CHECK(step_hz(&b, 1000000000) == 0 && guard.ready);
}

/* ---- the temperature ceiling --------------------------------------------------------------------- */

static void test_ceiling(void)
{
    struct hamoa_thermal_config c;
    struct bus b;
    uint64_t fire;

    reset(&b, 0);
    b.status[0][0] = VALID | 799;
    CHECK(!step(&b) && guard.current_mc == CEILING_MC - 100);
    b.now = HZ / 4;
    b.status[0][0] = VALID | 800;
    CHECK(step(&b));
    CHECK(guard.current_mc == CEILING_MC && guard.peak_mc == CEILING_MC && guard.trip_ticks == HZ / 4);
    fired(&b, R(HOT), D(NONE), STATUS_ADDR(0, 0));
    /* The first cause stays: a later failure neither rewrites the report nor asks for another stop. */
    fire = stop.fire_ticks;
    b.control[0] = 0;
    b.now += HZ;
    CHECK(step(&b) == 1 && stop.fire_ticks == fire && guard.reason == R(HOT) && stop.reason == R(HOT));

    /* The last sensor of the last controller, with the counter wrapping between the two polls. */
    reset(&b, UINT64_MAX - HZ / 8);
    CHECK(!step(&b));
    b.now += HZ / 4;
    b.status[3][14] = VALID | 800;
    CHECK(step(&b));
    fired(&b, R(HOT), D(NONE), STATUS_ADDR(3, 14));

    /* An invalid sensor earlier in the list must not hide a hot one after it. */
    reset(&b, 0);
    b.status[0][0] &= ~VALID;
    b.status[1][1] = VALID | 900;
    CHECK(step(&b));
    fired(&b, R(HOT), D(NONE), STATUS_ADDR(1, 1));

    /* A code is never believed without VALID, whatever it says. */
    reset(&b, 0);
    b.status[0][0] = 900;
    CHECK(!step(&b) && !guard.ready && guard.missing_count == 1 && guard.current_mc == 34400);

    /* The ceiling is exactly the configured one at both ends of the accepted window. */
    one_sensor(&c, 0, 0);
    c.limit_mc = HAMOA_THERMAL_LIMIT_MIN_MC;
    reset_with(&b, 0, &c, 0);
    b.status[0][0] = VALID | 299;
    CHECK(!step(&b));
    b.now = HZ / 4;
    b.status[0][0] = VALID | 300;
    CHECK(step(&b));
    fired(&b, R(HOT), D(NONE), STATUS_ADDR(0, 0));
    c.limit_mc = HAMOA_THERMAL_LIMIT_MAX_MC;
    reset_with(&b, 0, &c, 0);
    b.status[0][0] = VALID | 1149;
    CHECK(!step(&b));
    b.now = HZ / 4;
    b.status[0][0] = VALID | 1150;
    CHECK(step(&b));
    fired(&b, R(HOT), D(NONE), STATUS_ADDR(0, 0));
}

/* ---- missing VALID, ready word, frozen readings ------------------------------------------------- */

static void drive_to_one_second(struct bus *b)
{
    unsigned j;

    for (j = 1; j < 4; j++) {
        b->now = j * HZ / 4;
        CHECK(!step(b) && !guard.ready);
    }
    b->now = HZ;
}

static void test_missing_valid(void)
{
    struct bus b;
    unsigned j;

    /* Never valid with the controller ready: tolerated for a second, then INVALID. */
    reset(&b, 0);
    b.status[0][0] &= ~VALID;
    CHECK(!step(&b));
    CHECK(!guard.ready && guard.missing_count == 1 && guard.valid_count == 49 && guard.invalid_samples == 1);
    CHECK(!(guard.valid_slots & 1) && !(guard.seen_slots & 1));
    drive_to_one_second(&b);
    CHECK(step(&b));
    fired(&b, R(INVALID), D(NONE), STATUS_ADDR(0, 0));

    /* The same with the controller's ready word clear (or any value with bit 0 clear): NOT_READY. */
    reset(&b, 0);
    b.status[0][0] &= ~VALID;
    b.trdy[0] = 0;
    drive_to_one_second(&b);
    CHECK(step(&b) && guard.trdy[0] == 0);
    fired(&b, R(NOT_READY), D(NONE), STATUS_ADDR(0, 0));
    reset(&b, 0);
    b.status[2][3] &= ~VALID;
    b.trdy[2] = 2;
    drive_to_one_second(&b);
    CHECK(step(&b));
    fired(&b, R(NOT_READY), D(NONE), STATUS_ADDR(2, 3));

    /* The ready word is no freshness proof: with every sensor VALID, a clear ready word is harmless. */
    reset(&b, 0);
    b.trdy[0] = 0;
    CHECK(!step(&b) && guard.ready && guard.trdy[0] == 0);

    /* Just under a second is tolerated, the next sample after it is not. */
    reset(&b, 0);
    b.status[0][0] &= ~VALID;
    CHECK(!step(&b));
    b.now = HZ / 2;
    CHECK(!step(&b));
    b.now = HZ - 1;
    CHECK(!step(&b) && guard.missing_count == 1);
    b.now = HZ - 1 + HZ / 4;
    CHECK(step(&b));
    fired(&b, R(INVALID), D(NONE), STATUS_ADDR(0, 0));

    /* A brief clear heals. */
    reset(&b, 0);
    CHECK(!step(&b));
    b.status[0][0] &= ~VALID;
    b.now = HZ / 4;
    CHECK(!step(&b) && !guard.ready && guard.missing_count == 1);
    b.status[0][0] |= VALID;
    b.now = HZ / 2;
    CHECK(!step(&b) && guard.ready && guard.missing_count == 0 && guard.invalid_samples == 1);

    /* The second runs from the sensor's last valid sample, not from the start. */
    reset(&b, 0);
    CHECK(!step(&b));
    b.now = HZ / 4;
    CHECK(!step(&b));
    b.status[1][2] &= ~VALID;
    for (j = 2; j <= 4; j++) {
        b.now = j * HZ / 4;
        CHECK(!step(&b) && !guard.ready);
    }
    b.now = 5 * HZ / 4;
    CHECK(step(&b));
    fired(&b, R(INVALID), D(NONE), STATUS_ADDR(1, 2));

    /* The first offender in polling order is the one named. */
    reset(&b, 0);
    b.status[2][4] &= ~VALID;
    b.status[0][5] &= ~VALID;
    CHECK(!step(&b));
    drive_to_one_second(&b);
    CHECK(step(&b));
    fired(&b, R(INVALID), D(NONE), STATUS_ADDR(0, 5));
}

static void test_unchanged(void)
{
    struct hamoa_thermal_config c;
    struct bus b;
    unsigned j;

    /* Ten seconds of identical valid codes from every sensor: a frozen controller. */
    reset(&b, 0);
    CHECK(!step(&b));
    for (j = 1; j < 40; j++) {
        b.now = j * HZ / 4;
        CHECK(!step(&b));
    }
    CHECK(guard.unchanged_ticks == 39 * HZ / 4);
    b.now = 10 * HZ;
    CHECK(step(&b) && guard.unchanged_ticks == 10 * HZ);
    fired(&b, R(UNCHANGED), D(NONE), STATUS_ADDR(3, 14));

    /* One least-significant bit on any one sensor is progress and restarts the ten seconds. */
    reset(&b, 0);
    CHECK(!step(&b));
    for (j = 1; j < 60; j++) {
        b.now = j * HZ / 4;
        if (j == 20)
            b.status[2][4] ^= 1;
        CHECK(!step(&b));
    }
    b.now = 60 * HZ / 4;
    CHECK(step(&b));
    fired(&b, R(UNCHANGED), D(NONE), STATUS_ADDR(3, 14));

    /* A VALID flicker that carries a different code in its invalid words is not progress either. */
    one_sensor(&c, 2, 5);
    reset_with(&b, 0, &c, 0);
    b.status[2][5] = VALID | 300;
    CHECK(!step(&b));
    for (j = 1; j <= 40; j++) {
        b.now = j * HZ / 4;
        b.status[2][5] = (j & 1) ? 301u : (VALID | 300);
        CHECK(step(&b) == (j == 40));
    }
    fired(&b, R(UNCHANGED), D(NONE), STATUS_ADDR(2, 5));
}

/* ---- sample timing: gap, duration, counter frequency --------------------------------------------- */

static void test_timing(void)
{
    struct hamoa_thermal_config c;
    struct bus b;

    /* A gap of one tick under a second is fine; a second or more means the sensors went unwatched. */
    reset(&b, 0);
    CHECK(!step(&b));
    b.now = HZ - 1;
    CHECK(!step(&b) && guard.max_gap_ticks == HZ - 1);
    b.now = 2 * HZ - 1;
    CHECK(step(&b) && guard.max_gap_ticks == HZ);
    fired(&b, R(LATE), D(NONE), 0);
    reset(&b, 0);
    CHECK(!step(&b));
    b.now = HZ;
    CHECK(step(&b));
    fired(&b, R(LATE), D(NONE), 0);

    /* A sample that takes a whole polling interval; one tick less does not. */
    reset(&b, 0);
    b.slow = HZ / 4 - 1;
    CHECK(!step(&b) && guard.poll_ticks == HZ / 4 - 1 && guard.max_poll_ticks == HZ / 4 - 1 && guard.ready);
    reset(&b, 0);
    b.slow = HZ / 4;
    CHECK(step(&b) && guard.poll_ticks == HZ / 4 && guard.trip_ticks == HZ / 4);
    fired(&b, R(SLOW), D(NONE), STATUS_ADDR(3, 14));

    /* A counter frequency outside 1 kHz .. 1 GHz cannot pace anything. */
    reset_raw(&b, 0);
    default_config(&c);
    CHECK(hamoa_thermal_init(&guard, &stop, 0, 0, &c) == 1);
    fired(&b, R(CLOCK), D(NONE), 0);
    CHECK(b.reads == 0);
    reset(&b, 0);
    CHECK(step_hz(&b, 999) == 1);
    fired(&b, R(CLOCK), D(NONE), 0);
    CHECK(b.reads == 0);
    reset(&b, 0);
    CHECK(step_hz(&b, 1000000001) == 1);
    fired(&b, R(CLOCK), D(NONE), 0);
    CHECK(b.reads == 0);
}

/* ---- failed reads -------------------------------------------------------------------------------- */

static void test_read_failures(void)
{
    const uint64_t addresses[] = {VERSION_ADDR(2), CONTROL_ADDR(2), TRDY_ADDR(2), CONV_ADDR(2, 5),
                                  STATUS_ADDR(2, 5)};
    struct bus b;
    unsigned i;

    for (i = 0; i < sizeof addresses / sizeof addresses[0]; i++) {
        reset(&b, 0);
        b.fail_addr = addresses[i];
        CHECK(step(&b) == 1);
        CHECK(b.fail_hits == 1 && guard.read_failures == 1 && guard.read_inflight == 1 && guard.reads == b.reads);
        fired(&b, R(READ), D(NONE), addresses[i]);
        CHECK(b.fail_hits == 1); /* a failed read is never retried */
    }
}

/* ---- decoded values and status flags ------------------------------------------------------------- */

static void test_values(void)
{
    struct bus b;

    reset(&b, 0);
    b.status[0][0] = VALID | 0xff6; /* -1.0 C: the 12-bit field is two's complement */
    CHECK(!step(&b) && guard.ready && (guard.raw[0] & 0xfff) == 0xff6 && guard.current_mc == 34400);
    reset(&b, 0);
    b.status[0][0] = VALID | ((4096u - 400u) & 0xfff); /* -40.0 C: lowest plausible reading */
    CHECK(!step(&b) && guard.ready);
    reset(&b, 0);
    b.status[0][0] = VALID | ((4096u - 401u) & 0xfff);
    CHECK(step(&b));
    fired(&b, R(RANGE), D(NONE), STATUS_ADDR(0, 0));
    reset(&b, 0);
    b.status[0][0] = VALID | 1200; /* 120.0 C is plausible, so the ceiling (not RANGE) decides */
    CHECK(step(&b));
    fired(&b, R(HOT), D(NONE), STATUS_ADDR(0, 0));
    reset(&b, 0);
    b.status[0][0] = VALID | 1201;
    CHECK(step(&b));
    fired(&b, R(RANGE), D(NONE), STATUS_ADDR(0, 0));
    reset(&b, 0);
    b.status[1][4] = VALID | 0x7ff;
    CHECK(step(&b));
    fired(&b, R(RANGE), D(NONE), STATUS_ADDR(1, 4));
    reset(&b, 0);
    b.status[1][4] = VALID | 0x800;
    CHECK(step(&b));
    fired(&b, R(RANGE), D(NONE), STATUS_ADDR(1, 4));

    /* The OS driver's min, lower and upper threshold flags say nothing about this guard's ceiling. */
    reset(&b, 0);
    b.status[0][1] |= UINT32_C(7) << 16;
    CHECK(!step(&b) && guard.ready && (guard.raw[1] >> 16 & 7) == 7);
}

static void test_critical(void)
{
    static const uint32_t flags[] = {UINT32_C(1) << 19, UINT32_C(1) << 20, (UINT32_C(1) << 19) | (UINT32_C(1) << 20)};
    static const uint32_t valid[] = {VALID, 0};
    struct bus b;
    unsigned f, v;

    /* The critical and max flags are the hardware's own report, valid word or not. */
    for (f = 0; f < 3; f++) {
        for (v = 0; v < 2; v++) {
            reset(&b, 0);
            b.status[1][10] = valid[v] | flags[f] | CODE(1, 10);
            CHECK(step(&b));
            fired(&b, R(CRITICAL), D(NONE), STATUS_ADDR(1, 10));
        }
    }
    /* A dead bus reads all ones. */
    reset(&b, 0);
    b.status[0][0] = UINT32_MAX;
    CHECK(step(&b));
    fired(&b, R(CRITICAL), D(NONE), STATUS_ADDR(0, 0));
}

/* ---- per-controller identity, mode and calibration ---------------------------------------------- */

static void expect_control_trip(unsigned instance, uint32_t word)
{
    struct bus b;

    reset(&b, 0);
    b.control[instance] = word;
    CHECK(step(&b) == 1 && guard.control[instance] == word && guard.trdy[instance] == 0);
    fired(&b, R(CONFIG), D(CONTROL), CONTROL_ADDR(instance));
}

static void test_controller_words(void)
{
    struct hamoa_thermal_config c;
    struct bus b;

    expect_control_trip(0, CTRL_HEALTHY & ~UINT32_C(1));            /* controller not enabled */
    expect_control_trip(2, CTRL_HEALTHY | UINT32_C(2));             /* software reset asserted */
    expect_control_trip(1, CTRL_HEALTHY & ~(UINT32_C(1) << 21));    /* words are raw codes, not temperatures */
    expect_control_trip(0, CTRL_HEALTHY & ~(UINT32_C(1) << (3 + 12))); /* a polled sensor is not enabled */
    expect_control_trip(1, CTRL_HEALTHY & ~(UINT32_C(1) << (3 + 10)));
    expect_control_trip(3, CTRL_HEALTHY & ~(UINT32_C(1) << (3 + 14)));
    expect_control_trip(3, 0);
    expect_control_trip(2, UINT32_MAX); /* all ones, with the reset bit set */
    /* An enabled sensor nobody polls is harmless: instance 0 polls ids 0..12 only. */
    reset(&b, 0);
    b.control[0] &= ~(UINT32_C(1) << (3 + 13));
    CHECK(!step(&b) && guard.ready);

    /* Identity: any other word, zero and all ones included, is not the measured controller. */
    reset(&b, 0);
    b.version[1] = TEST_VERSION + 1;
    CHECK(step(&b) && guard.version[1] == TEST_VERSION + 1 && guard.control[1] == 0);
    fired(&b, R(CONFIG), D(VERSION), VERSION_ADDR(1));
    reset(&b, 0);
    b.version[0] = 0;
    CHECK(step(&b));
    fired(&b, R(CONFIG), D(VERSION), VERSION_ADDR(0));
    reset(&b, 0);
    b.version[3] = UINT32_MAX;
    CHECK(step(&b));
    fired(&b, R(CONFIG), D(VERSION), VERSION_ADDR(3));

    /* Identity and mode are proved again on every sample, not once. */
    reset(&b, 0);
    CHECK(!step(&b));
    b.now = HZ / 4;
    b.control[1] &= ~UINT32_C(1);
    CHECK(step(&b));
    fired(&b, R(CONFIG), D(CONTROL), CONTROL_ADDR(1));
    reset(&b, 0);
    CHECK(!step(&b));
    b.now = HZ / 4;
    b.version[2] ^= 0x100;
    CHECK(step(&b));
    fired(&b, R(CONFIG), D(VERSION), VERSION_ADDR(2));

    /* A controller no sensor of the list lives on is never read. */
    one_sensor(&c, 2, 5);
    reset_with(&b, 0, &c, 0);
    b.version[0] = b.version[1] = b.version[3] = 0;
    CHECK(!step(&b) && b.reads == 5 && guard.ready && guard.valid_count == 1);
    CHECK(guard.version[0] == 0 && guard.control[0] == 0 && guard.trdy[1] == 0);

    /* The enable mask a controller must show covers every sensor of the list on it, however the list
     * is ordered, not only the sensors seen before its first slot. */
    default_config(&c);
    c.count = 3;
    c.sensors[0].instance = 0; c.sensors[0].id = 0;
    c.sensors[1].instance = 1; c.sensors[1].id = 0;
    c.sensors[2].instance = 0; c.sensors[2].id = 1;
    reset_with(&b, 0, &c, 0);
    b.control[0] = UINT32_C(1) | (UINT32_C(1) << 3) | (UINT32_C(1) << 21); /* only sensor 0 enabled */
    CHECK(step(&b));
    fired(&b, R(CONFIG), D(CONTROL), CONTROL_ADDR(0));
    reset_with(&b, 0, &c, 0);
    CHECK(!step(&b) && guard.valid_count == 3 && guard.slot[1] == (UINT64_C(1) << 8) && guard.slot[2] == 1);
    CHECK(b.reads == 2 * 3 + 3 * 2); /* each controller's three words once per poll, then two per sensor */
}

static void test_uncalibrated(void)
{
    struct bus b;

    reset(&b, 0);
    b.conv[2][4] = 0;
    CHECK(step(&b));
    CHECK(guard.conversion[13 + 11 + 4] == 0 && guard.raw[13 + 11 + 4] == 0); /* its status was never read */
    fired(&b, R(UNCALIBRATED), D(NONE), CONV_ADDR(2, 4));
    reset(&b, 0);
    CHECK(!step(&b));
    b.now = HZ / 4;
    b.conv[0][0] = 0;
    CHECK(step(&b));
    fired(&b, R(UNCALIBRATED), D(NONE), CONV_ADDR(0, 0));
}

/* ---- configuration and state integrity ----------------------------------------------------------- */

#define INIT_TRIP(statement, reason, detail, where) \
    do {                                            \
        default_config(&c);                         \
        statement;                                  \
        reset_with(&b, 0, &c, 1);                   \
        fired(&b, reason, detail, where);           \
        CHECK(b.reads == 0 && guard.polls == 0);    \
    } while (0)

#define TAMPERED(statement, reason, detail, where) \
    do {                                           \
        reset(&b, 0);                              \
        statement;                                 \
        CHECK(step(&b) == 1 && b.reads == 0);      \
        fired(&b, reason, detail, where);          \
    } while (0)

static void test_configuration(void)
{
    struct hamoa_thermal_config c;
    struct bus b;

    INIT_TRIP(c.count = 0, R(NO_SOURCE), D(NO_SENSORS), 0);
    INIT_TRIP(c.hw_version = 0, R(NO_SOURCE), D(NO_VERSION), 0);
    INIT_TRIP(c.count = 0; c.hw_version = 0, R(NO_SOURCE), D(NO_SENSORS), 0);
    INIT_TRIP(c.limit_mc = 0, R(CONFIG), D(LIMIT), 0);
    INIT_TRIP(c.limit_mc = 80, R(CONFIG), D(LIMIT), 0); /* degrees where millidegrees are required */
    INIT_TRIP(c.limit_mc = HAMOA_THERMAL_LIMIT_MIN_MC - 1, R(CONFIG), D(LIMIT), 0);
    INIT_TRIP(c.limit_mc = HAMOA_THERMAL_LIMIT_MAX_MC + 1, R(CONFIG), D(LIMIT), 0);
    INIT_TRIP(c.limit_mc = INT64_MIN, R(CONFIG), D(LIMIT), 0);
    INIT_TRIP(c.limit_mc = INT64_MAX, R(CONFIG), D(LIMIT), 0);
    INIT_TRIP(c.count = HAMOA_THERMAL_SLOTS + 1, R(CONFIG), D(TOO_MANY), 0);
    INIT_TRIP(c.count = UINT32_MAX, R(CONFIG), D(TOO_MANY), 0);
    INIT_TRIP(c.sensors[1] = c.sensors[0], R(CONFIG), D(DUPLICATE), 1);
    INIT_TRIP(c.sensors[40] = c.sensors[3], R(CONFIG), D(DUPLICATE), 40);
    INIT_TRIP(c.sensors[7].instance = 4, R(CONFIG), D(BAD_SENSOR), 7);
    INIT_TRIP(c.sensors[7].id = 16, R(CONFIG), D(BAD_SENSOR), 7);
    INIT_TRIP(c.sensors[49].instance = 255; c.sensors[49].id = 255, R(CONFIG), D(BAD_SENSOR), 49);
    /* A caller's oversized count must not write past the state. */
    default_config(&c);
    c.count = UINT32_MAX;
    reset_with(&b, 0, &c, 1);
    CHECK(canaries_intact());
    {
        unsigned k;

        for (k = 0; k < HAMOA_THERMAL_SLOTS; k++)
            CHECK(guard.raw[k] == 0 && guard.conversion[k] == 0 && guard.last_valid[k] == 0);
    }

    /* The accepted window includes both of its ends, and a full list of 64 distinct sensors. */
    default_config(&c);
    c.limit_mc = HAMOA_THERMAL_LIMIT_MIN_MC;
    reset_with(&b, 0, &c, 0);
    default_config(&c);
    c.limit_mc = HAMOA_THERMAL_LIMIT_MAX_MC;
    reset_with(&b, 0, &c, 0);
    default_config(&c);
    {
        unsigned k;

        c.count = HAMOA_THERMAL_SLOTS;
        for (k = 0; k < HAMOA_THERMAL_SLOTS; k++) {
            c.sensors[k].instance = (uint8_t)(k / 16);
            c.sensors[k].id = (uint8_t)(k % 16);
        }
    }
    reset_with(&b, 0, &c, 0);
    CHECK(!step(&b) && guard.valid_count == 64 && guard.valid_slots == UINT64_MAX && guard.ready);
    CHECK(b.reads == 4 * 3 + 64 * 2 && canaries_intact());

    /* poll re-proves the state it finds; a state changed after init is a configuration trip. */
    TAMPERED(guard.slot_count = 65, R(CONFIG), D(TOO_MANY), 0);
    TAMPERED(guard.slot_count = 0, R(NO_SOURCE), D(NO_SENSORS), 0);
    TAMPERED(guard.hw_version = 0, R(NO_SOURCE), D(NO_VERSION), 0);
    TAMPERED(guard.limit_mc = 0, R(CONFIG), D(LIMIT), 0);
    TAMPERED(guard.limit_mc = HAMOA_THERMAL_LIMIT_MAX_MC + 1, R(CONFIG), D(LIMIT), 0);
    TAMPERED(guard.slot[3] |= UINT64_C(1) << 16, R(CONFIG), D(BAD_SENSOR), 3);
    TAMPERED(guard.slot[0] = UINT64_C(4) << 8, R(CONFIG), D(BAD_SENSOR), 0);
    TAMPERED(guard.slot[9] = (UINT64_C(1) << 8) | 16, R(CONFIG), D(BAD_SENSOR), 9);
    TAMPERED(guard.slot[7] = guard.slot[2], R(CONFIG), D(DUPLICATE), 7);
    TAMPERED(guard.abi = 2, R(CONFIG), D(BAD_HEADER), 0);
    TAMPERED(guard.bytes = 8, R(CONFIG), D(BAD_HEADER), 0);
    TAMPERED(guard.magic = 1, R(CONFIG), D(BAD_HEADER), 0);
}

static void test_state_and_arguments(void)
{
    struct hamoa_thermal_state copy;
    struct hamoa_thermal_config c;
    struct bus b;
    struct hamoa_thermal_io io = {&b, bus_read, bus_counter};
    struct hamoa_thermal_io broken[3] = {{&b, NULL, bus_counter}, {&b, bus_read, NULL}, {NULL, NULL, NULL}};
    unsigned i;

    /* A state init never prepared is not silently treated as armed. */
    reset_raw(&b, 0);
    CHECK(step(&b) == 1 && guard.magic == HAMOA_THERMAL_MAGIC);
    fired(&b, R(NO_SOURCE), D(NO_STATE), 0);
    CHECK(b.reads == 0);

    /* init prepares a pristine state only; a used or tripped one is refused and left byte for byte. */
    default_config(&c);
    reset(&b, 0);
    CHECK(!step(&b));
    memcpy(&copy, &guard, sizeof copy);
    CHECK(hamoa_thermal_init(&guard, &stop, 5, HZ, &c) == -1 && !memcmp(&copy, &guard, sizeof copy));
    reset(&b, 0);
    b.status[0][0] = VALID | 800;
    CHECK(step(&b));
    memcpy(&copy, &guard, sizeof copy);
    CHECK(hamoa_thermal_init(&guard, &stop, 5, HZ, &c) == -1 && !memcmp(&copy, &guard, sizeof copy));
    CHECK(hamoa_thermal_init(NULL, &stop, 0, HZ, &c) == -1);

    /* No configuration at all, and no early-stop state: both trip, neither crashes. */
    reset_raw(&b, 0);
    CHECK(hamoa_thermal_init(&guard, &stop, 0, HZ, NULL) == 1);
    fired(&b, R(NO_SOURCE), D(NO_CONFIG), 0);
    reset_raw(&b, 0);
    CHECK(hamoa_thermal_init(&guard, NULL, 0, HZ, &c) == 1);
    CHECK(guard.reason == R(CONFIG) && guard.detail == D(NO_STOP) && stop.source == EARLY_STOP_SOURCE_GUEST);
    CHECK(step(&b) == 1 && guard.reason == R(CONFIG) && b.reads == 0);
    reset(&b, 0);
    CHECK(hamoa_thermal_poll(&guard, NULL, 0, HZ, &io) == 1);
    CHECK(guard.reason == R(CONFIG) && guard.detail == D(NO_STOP) && b.reads == 0);

    /* Missing callbacks. */
    for (i = 0; i < 3; i++) {
        reset(&b, 0);
        CHECK(hamoa_thermal_poll(&guard, &stop, 0, HZ, &broken[i]) == 1);
        fired(&b, R(CONFIG), D(NO_IO), 0);
        CHECK(b.reads == 0);
    }
    reset(&b, 0);
    CHECK(hamoa_thermal_poll(&guard, &stop, 0, HZ, NULL) == 1);
    fired(&b, R(CONFIG), D(NO_IO), 0);

    /* No state: a poll or an admission reports a trip instead of dereferencing it. */
    CHECK(hamoa_thermal_poll(NULL, &stop, 0, HZ, &io) == 1);
    CHECK(hamoa_thermal_admit(NULL, &stop, HZ, &io) == 1);
    CHECK(hamoa_thermal_admit(NULL, &stop, HZ, NULL) == 1);
    CHECK(hamoa_thermal_wait(NULL, 0, HZ, HZ) == 0);
}

/* The public calls reach trip() only with a clean state, so its own guard against a second cause is
 * exercised directly: the first cause, its location and its single stop request must stand. */
static void test_trip_is_final(void)
{
    struct bus b;

    reset(&b, 100 * HZ);
    CHECK(trip(&guard, &stop, b.now, HZ, R(HOT), D(NONE), 0x1000) == 1);
    CHECK(trip(&guard, &stop, b.now + HZ, HZ, R(READ), D(CONTROL), 0x2000) == 1);
    CHECK(guard.reason == R(HOT) && guard.detail == D(NONE) && guard.location == 0x1000 &&
          guard.trip_ticks == b.now);
    CHECK(stop.reason == R(HOT) && stop.detail == 0x1000 && stop.duplicates == 0 && canaries_intact());
}

/* ---- initial admission --------------------------------------------------------------------------- */

static void test_admit(void)
{
    struct bus b;
    uint64_t t0, reads;
    int r;

    /* One brief clear refuses a single-sample preflight; the resample admits. */
    reset(&b, 0);
    b.flick_addr[0] = STATUS_ADDR(1, 3);
    b.flick_left[0] = 1;
    CHECK(!step(&b) && !guard.ready && guard.valid_count == 49);
    reset(&b, 0);
    b.step = HZ / 1000;
    b.flick_addr[0] = STATUS_ADDR(1, 3);
    b.flick_left[0] = 1;
    t0 = b.now;
    r = admit(&b);
    admitted("single-clear", r, t0, b.now);
    CHECK(r == 0 && guard.ready && guard.valid_count == 50 && guard.polls == 2 && guard.invalid_samples == 1);
    CHECK(!guard.reason && guard.next_poll == guard.last_poll + HZ / 4 && stop.source == EARLY_STOP_SOURCE_GUEST);

    reset(&b, 0);
    b.step = HZ / 1000;
    b.flick_addr[0] = STATUS_ADDR(0, 6);
    b.flick_left[0] = 3;
    b.flick_addr[1] = STATUS_ADDR(2, 7);
    b.flick_left[1] = 2;
    t0 = b.now;
    r = admit(&b);
    admitted("two-sensors", r, t0, b.now);
    CHECK(r == 0 && guard.ready && guard.polls == 4 && guard.invalid_samples == 5);

    /* A sensor that never returns VALID: refuse at the sample bound, then the runtime trip. */
    reset(&b, 0);
    b.step = HZ / 1000;
    b.flick_addr[0] = STATUS_ADDR(2, 10);
    b.flick_left[0] = UINT64_MAX;
    t0 = b.now;
    r = admit(&b);
    admitted("never-valid", r, t0, b.now);
    CHECK(r == -1 && !guard.ready && guard.polls == HAMOA_THERMAL_ADMIT_SAMPLES && !guard.reason);
    CHECK(guard.last_poll - t0 < HZ / 2);
    b.step = 0;
    b.now = guard.started + HZ;
    CHECK(step(&b));
    fired(&b, R(INVALID), D(NONE), STATUS_ADDR(2, 10));

    /* A slow counter ends admission by time before the sample bound. */
    reset(&b, 0);
    b.step = HZ / 20;
    b.flick_addr[0] = STATUS_ADDR(2, 10);
    b.flick_left[0] = UINT64_MAX;
    t0 = b.now;
    r = admit(&b);
    admitted("time-bound", r, t0, b.now);
    CHECK(r == -1 && guard.polls >= 2 && guard.polls < HAMOA_THERMAL_ADMIT_SAMPLES && guard.last_poll - t0 < HZ / 2);
    CHECK(!guard.reason);

    /* A counter that never advances cannot hang admission. */
    reset(&b, 0);
    b.flick_addr[0] = STATUS_ADDR(2, 10);
    b.flick_left[0] = UINT64_MAX;
    r = admit(&b);
    admitted("stuck-counter", r, 0, b.now);
    CHECK(r == -1 && guard.polls == HAMOA_THERMAL_ADMIT_SAMPLES);

    /* Every attempt is the full policy: a hot second sample trips, it is not retried. */
    reset(&b, 0);
    b.step = HZ / 1000;
    b.flick_addr[0] = STATUS_ADDR(1, 3);
    b.flick_left[0] = 1;
    b.hot_addr = STATUS_ADDR(0, 0);
    b.hot_from_poll = 2;
    b.hot_word = VALID | 800;
    t0 = b.now;
    r = admit(&b);
    admitted("hot-retry", r, t0, b.now);
    CHECK(r == 1 && guard.polls == 2);
    fired(&b, R(HOT), D(NONE), STATUS_ADDR(0, 0));

    /* Admission is a fresh sample even right after a complete one, and twice in a row. */
    reset(&b, 0);
    CHECK(!step(&b) && guard.ready);
    reads = b.reads;
    b.now = HZ / 8;
    CHECK(admit(&b) == 0 && guard.polls == 2 && b.reads == 2 * reads && guard.ready);
    b.now += HZ / 8;
    CHECK(admit(&b) == 0 && guard.polls == 3);

    /* Long preparation is not exempt from the gap policy. */
    reset(&b, 0);
    CHECK(!step(&b));
    b.now = HZ;
    r = admit(&b);
    CHECK(r == 1);
    fired(&b, R(LATE), D(NONE), 0);

    /* The counter may wrap during admission. */
    reset(&b, UINT64_MAX - HZ / 100);
    b.step = HZ / 1000;
    b.flick_addr[0] = STATUS_ADDR(0, 0);
    b.flick_left[0] = 2;
    t0 = b.now;
    r = admit(&b);
    admitted("counter-wrap", r, t0, b.now);
    CHECK(r == 0 && guard.ready && guard.polls == 3);

    /* An invalid frequency, a missing io, and a guard that already tripped all refuse without reads. */
    reset(&b, 0);
    {
        struct hamoa_thermal_io io = {&b, bus_read, bus_counter};

        CHECK(hamoa_thermal_admit(&guard, &stop, 0, &io) == 1 && !b.reads && guard.reason == R(CLOCK));
    }
    reset(&b, 0);
    CHECK(hamoa_thermal_admit(&guard, &stop, HZ, NULL) == 1);
    fired(&b, R(CONFIG), D(NO_IO), 0);
    {
        struct hamoa_thermal_config c;

        default_config(&c);
        c.count = 0;
        reset_with(&b, 0, &c, 1);
        CHECK(admit(&b) == 1 && b.reads == 0);
        fired(&b, R(NO_SOURCE), D(NO_SENSORS), 0);
    }
}

/* ---- report layout for the Python decoder, and known states as bytes ----------------------------- */

#define FIELD(name) printf("field %s %zu\n", #name, offsetof(struct hamoa_thermal_state, name) / sizeof(uint64_t))
#define REASON(name) printf("reason %s %d\n", #name, (int)R(name))
#define DETAIL(name) printf("detail %s %d\n", #name, (int)D(name))

static void print_layout(void)
{
    FIELD(magic); FIELD(abi); FIELD(bytes); FIELD(polls); FIELD(reads); FIELD(started); FIELD(last_poll);
    FIELD(next_poll); FIELD(last_change); FIELD(trip_ticks); FIELD(reason); FIELD(location);
    FIELD(current_mc); FIELD(peak_mc); FIELD(valid_count); FIELD(missing_count); FIELD(max_gap_ticks);
    FIELD(invalid_samples); FIELD(read_failures); FIELD(poll_ticks); FIELD(max_poll_ticks); FIELD(ready);
    FIELD(unchanged_ticks); FIELD(read_inflight); FIELD(limit_mc); FIELD(hw_version); FIELD(slot_count);
    FIELD(valid_slots); FIELD(seen_slots); FIELD(detail);
    FIELD(version); FIELD(control); FIELD(trdy); FIELD(slot); FIELD(raw); FIELD(conversion); FIELD(last_valid);
    REASON(OK); REASON(HOT); REASON(CONFIG); REASON(INVALID); REASON(LATE); REASON(READ); REASON(RANGE);
    REASON(CRITICAL); REASON(UNCHANGED); REASON(SLOW); REASON(CLOCK); REASON(NO_SOURCE); REASON(NOT_READY);
    REASON(UNCALIBRATED);
    DETAIL(NONE); DETAIL(NO_CONFIG); DETAIL(NO_STATE); DETAIL(BAD_HEADER); DETAIL(NO_IO); DETAIL(NO_STOP);
    DETAIL(LIMIT); DETAIL(NO_SENSORS); DETAIL(TOO_MANY); DETAIL(BAD_SENSOR); DETAIL(DUPLICATE);
    DETAIL(NO_VERSION); DETAIL(VERSION); DETAIL(CONTROL);
    printf("const size %zu\nconst magic 0x%llx\nconst abi %u\nconst slots %u\nconst hz %llu\n",
           sizeof(struct hamoa_thermal_state), U(HAMOA_THERMAL_MAGIC), HAMOA_THERMAL_ABI, HAMOA_THERMAL_SLOTS,
           U(HZ));
    printf("const test_version 0x%x\nconst ceiling_mc %d\n", (unsigned)TEST_VERSION, CEILING_MC);
}

static void write_dump(const char *dir, const char *name)
{
    char path[512];
    FILE *f;

    CHECK(snprintf(path, sizeof path, "%s/%s", dir, name) < (int)sizeof path);
    f = fopen(path, "wb");
    CHECK(f && fwrite(&guard, sizeof guard, 1, f) == 1 && fclose(f) == 0);
}

static void write_dumps(const char *dir)
{
    struct hamoa_thermal_config c;
    struct bus b;

    reset(&b, 1000);
    b.now = 1000;
    write_dump(dir, "unsampled.bin");
    CHECK(!step(&b));
    write_dump(dir, "healthy.bin");

    reset(&b, 0);
    CHECK(!step(&b));
    b.now = HZ / 4;
    b.status[2][7] = VALID | 800;
    CHECK(step(&b));
    write_dump(dir, "hot.bin");

    one_sensor(&c, 2, 5);
    b.status[2][5] = VALID | 0xff6;
    reset_with(&b, 0, &c, 0);
    b.status[2][5] = VALID | 0xff6;
    CHECK(!step(&b));
    write_dump(dir, "negative.bin");

    default_config(&c);
    c.count = 0;
    reset_with(&b, 0, &c, 1);
    write_dump(dir, "nosource.bin");

    memset(&arena, 0, sizeof arena);
    write_dump(dir, "zero.bin");
}

int main(int argc, char **argv)
{
    if (argc == 2 && !strcmp(argv[1], "--layout")) {
        print_layout();
        return 0;
    }
    CHECK(argc == 1 || (argc == 3 && !strcmp(argv[1], "--dump")));
    make_baseline();
    test_allow_list();
    test_refusal_before_read();
    test_default_sensors();
    test_healthy();
    test_ceiling();
    test_missing_valid();
    test_unchanged();
    test_timing();
    test_read_failures();
    test_values();
    test_critical();
    test_controller_words();
    test_uncalibrated();
    test_configuration();
    test_state_and_arguments();
    test_trip_is_final();
    test_admit();
    if (argc == 3)
        write_dumps(argv[2]);
    puts("PASS: SP11 thermal guard host model (policy against INFERRED register facts; no hardware)");
    return 0;
}
