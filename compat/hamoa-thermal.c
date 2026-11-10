/* SPDX-License-Identifier: MIT
 * Surface Pro 11 read-only thermal guard model. Scope, provenance and evidence labels are in
 * hamoa-thermal.h; every register fact below is INFERRED from public Linux sources until the X2 survey
 * measures it on the device. Freestanding on purpose (no libc): the monitor links this without one, so
 * state is cleared and copied with explicit loops. */
#include "hamoa-thermal.h"
#include "early-stop.h"

/* Window bases of controller instance 0 and the spacing between instances: the TM and SROT reg
 * properties of the four tsens nodes in hamoa.dtsi (instance n sits n * 0x1000 above instance 0). */
#define SROT_BASE UINT64_C(0x0c222000)
#define TM_BASE UINT64_C(0x0c271000)
#define WINDOW_STRIDE UINT64_C(0x1000)

/* SROT offsets, tsens-v2.c lines 14-17: HW_VER, control, and the per-sensor conversion words (the
 * measure-period register at 0x8 is deliberately absent). TM offsets, lines 44-45: per-sensor status
 * words and the ready word. All are 4 bytes wide, sixteen sensors per instance. */
#define SROT_HW_VER 0x0u
#define SROT_CTRL 0x4u
#define SROT_CONVERSION 0x60u
#define TM_STATUS 0xa0u
#define TM_TRDY 0xe4u

/* Control word, tsens-v2.c lines 90-93. Bit 21 set selects temperature (deci-degree Celsius) output
 * instead of raw codes, so a clear bit means the sensor words below are not temperatures. */
#define CTRL_ENABLE (1u << 0)
#define CTRL_SW_RESET (1u << 1)
#define CTRL_SENSOR_SHIFT 3u
#define CTRL_SENSOR_MASK 0xffffu
#define CTRL_TEMPERATURE_FORMAT (1u << 21)

/* Status word, tsens-v2.c lines 128-135. Bits 16-18 (min, lower, upper) are the OS driver's own
 * interrupt thresholds and say nothing about this guard's ceiling, so they are only recorded. */
#define STATUS_CODE_MASK 0xfffu
#define STATUS_CRITICAL (1u << 19)
#define STATUS_MAX (1u << 20)
#define STATUS_VALID (1u << 21)

/* Ready word, tsens-v2.c lines 137-138. It classifies a missing-VALID trip but never admits a sample:
 * the Linux v2 read path and the A16 survey both treat per-sensor VALID alone as the freshness proof. */
#define TRDY_READY 0x1u

#define MIN_HZ UINT64_C(1000)
#define MAX_HZ UINT64_C(1000000000)
#define UNCHANGED_SECONDS 10u
/* Admission waits ~10 ms between resamples by reading the counter; the cap keeps a counter that never
 * advances from hanging admission. */
#define SPIN_ITERATION_LIMIT (1u << 20)

int hamoa_thermal_address_allowed(uint64_t address)
{
    uint32_t instance;

    if (address & 3u)
        return 0;
    for (instance = 0; instance < HAMOA_THERMAL_INSTANCES; instance++) {
        uint64_t srot = SROT_BASE + instance * WINDOW_STRIDE;
        uint64_t tm = TM_BASE + instance * WINDOW_STRIDE;
        uint64_t span = 4u * HAMOA_THERMAL_SENSORS_PER_INSTANCE;

        if (address == srot + SROT_HW_VER || address == srot + SROT_CTRL || address == tm + TM_TRDY)
            return 1;
        if (address >= srot + SROT_CONVERSION && address < srot + SROT_CONVERSION + span)
            return 1;
        if (address >= tm + TM_STATUS && address < tm + TM_STATUS + span)
            return 1;
    }
    return 0;
}

/* Sensor ids the public DT thermal zones read on each instance. Ids above these exist in hardware but no
 * zone references them, so there is no evidence they are populated. */
static uint32_t default_sensor_count(uint32_t instance)
{
    return instance == 0 ? 13u : instance == 3 ? 15u : 11u;
}

void hamoa_thermal_default_sensors(struct hamoa_thermal_config *cfg)
{
    uint32_t instance, id, slot = 0;

    if (!cfg)
        return;
    cfg->limit_mc = 0;
    cfg->hw_version = 0;
    for (slot = 0; slot < HAMOA_THERMAL_SLOTS; slot++) {
        cfg->sensors[slot].instance = 0;
        cfg->sensors[slot].id = 0;
    }
    slot = 0;
    for (instance = 0; instance < HAMOA_THERMAL_INSTANCES; instance++) {
        for (id = 0; id < default_sensor_count(instance); id++) {
            cfg->sensors[slot].instance = (uint8_t)instance;
            cfg->sensors[slot].id = (uint8_t)id;
            slot++;
        }
    }
    cfg->count = slot;
}

/* A trip is immutable: the first cause stays in the report, whatever fails afterwards. A stop is
 * requested only when a stop state exists; without one the trip is still recorded and returned. */
static int trip(struct hamoa_thermal_state *s, struct early_stop_state *stop, uint64_t now, uint64_t hz,
                uint64_t why, uint64_t detail, uint64_t where)
{
    if (s->reason)
        return 1;
    s->reason = why;
    s->detail = detail;
    s->location = where;
    s->trip_ticks = now;
    s->ready = 0;
    if (stop)
        early_stop_request(stop, EARLY_STOP_SOURCE_THERMAL, why, where, 0, 0, now, hz, 0, 0, 0);
    return 1;
}

/* The struct is nothing but 64-bit words (asserted in the header), so walking it as words clears every
 * byte, and a stale state cannot leak into a new arming. */
static void begin(struct hamoa_thermal_state *s, uint64_t now)
{
    uint64_t *word = (uint64_t *)s;
    uint32_t i;

    for (i = 0; i < HAMOA_THERMAL_REPORT_WORDS; i++)
        word[i] = 0;
    s->magic = HAMOA_THERMAL_MAGIC;
    s->abi = HAMOA_THERMAL_ABI;
    s->bytes = sizeof *s;
    s->started = now;
    s->last_change = now;
    s->current_mc = INT64_MIN;
    s->peak_mc = INT64_MIN;
}

static int clock_ok(uint64_t hz)
{
    return hz >= MIN_HZ && hz <= MAX_HZ;
}

/* Checks the sensor list, the measured version and the ceiling that the state carries, and derives the
 * sensor-enable mask each instance must show. Reads the state, not the caller's config, so a poll
 * re-proves what init stored. An empty list or unmeasured version is NO_SOURCE (nothing to trust yet);
 * a list or ceiling that is present but wrong is CONFIG. Returns 1 after recording a trip. */
static int validate(struct hamoa_thermal_state *s, struct early_stop_state *stop, uint64_t now, uint64_t hz,
                    uint32_t required[HAMOA_THERMAL_INSTANCES])
{
    uint64_t used = 0;
    uint64_t k;
    uint32_t i;

    if (!s->slot_count)
        return trip(s, stop, now, hz, HAMOA_THERMAL_NO_SOURCE, HAMOA_THERMAL_DETAIL_NO_SENSORS, 0);
    if (!s->hw_version)
        return trip(s, stop, now, hz, HAMOA_THERMAL_NO_SOURCE, HAMOA_THERMAL_DETAIL_NO_VERSION, 0);
    if (s->slot_count > HAMOA_THERMAL_SLOTS)
        return trip(s, stop, now, hz, HAMOA_THERMAL_CONFIG, HAMOA_THERMAL_DETAIL_TOO_MANY, 0);
    if (s->limit_mc < HAMOA_THERMAL_LIMIT_MIN_MC || s->limit_mc > HAMOA_THERMAL_LIMIT_MAX_MC)
        return trip(s, stop, now, hz, HAMOA_THERMAL_CONFIG, HAMOA_THERMAL_DETAIL_LIMIT, 0);
    for (i = 0; i < HAMOA_THERMAL_INSTANCES; i++)
        required[i] = 0;
    for (k = 0; k < s->slot_count; k++) {
        uint64_t instance = s->slot[k] >> 8, id = s->slot[k] & 0xffu;
        uint64_t bit;

        if ((s->slot[k] >> 16) || instance >= HAMOA_THERMAL_INSTANCES || id >= HAMOA_THERMAL_SENSORS_PER_INSTANCE)
            return trip(s, stop, now, hz, HAMOA_THERMAL_CONFIG, HAMOA_THERMAL_DETAIL_BAD_SENSOR, k);
        bit = UINT64_C(1) << (instance * HAMOA_THERMAL_SENSORS_PER_INSTANCE + id);
        if (used & bit)
            return trip(s, stop, now, hz, HAMOA_THERMAL_CONFIG, HAMOA_THERMAL_DETAIL_DUPLICATE, k);
        used |= bit;
        required[instance] |= 1u << id;
    }
    return 0;
}

int hamoa_thermal_init(struct hamoa_thermal_state *s, struct early_stop_state *stop, uint64_t now, uint64_t hz,
                       const struct hamoa_thermal_config *cfg)
{
    uint32_t required[HAMOA_THERMAL_INSTANCES];
    uint32_t i;

    if (!s || s->magic)
        return -1;
    begin(s, now);
    if (!cfg)
        return trip(s, stop, now, hz, HAMOA_THERMAL_NO_SOURCE, HAMOA_THERMAL_DETAIL_NO_CONFIG, 0);
    s->limit_mc = cfg->limit_mc;
    s->hw_version = cfg->hw_version;
    s->slot_count = cfg->count;
    for (i = 0; i < cfg->count && i < HAMOA_THERMAL_SLOTS; i++)
        s->slot[i] = ((uint64_t)cfg->sensors[i].instance << 8) | cfg->sensors[i].id;
    if (!stop)
        return trip(s, stop, now, hz, HAMOA_THERMAL_CONFIG, HAMOA_THERMAL_DETAIL_NO_STOP, 0);
    if (validate(s, stop, now, hz, required))
        return 1;
    if (!clock_ok(hz))
        return trip(s, stop, now, hz, HAMOA_THERMAL_CLOCK, HAMOA_THERMAL_DETAIL_NONE, 0);
    return 0;
}

/* The allow-list is checked before the bus is touched, so a bug elsewhere in this file cannot reach an
 * address outside the read-only set. read_inflight stays set after a failed read to show which access
 * was interrupted; a success clears it. */
static int read_word(struct hamoa_thermal_state *s, const struct hamoa_thermal_io *io, uint64_t address,
                     uint32_t *value)
{
    s->location = address;
    s->reads++;
    s->read_inflight = 1;
    __asm__ volatile("" ::: "memory");
    if (!hamoa_thermal_address_allowed(address) || io->read32(io->ctx, address, value)) {
        s->read_failures++;
        return -1;
    }
    __asm__ volatile("" ::: "memory");
    s->read_inflight = 0;
    return 0;
}

/* Two's-complement 12-bit field in deci-degrees Celsius, widened to millidegrees. */
static int64_t millidegrees(uint32_t status)
{
    int32_t code = (int32_t)(status & STATUS_CODE_MASK);

    if (code & 0x800)
        code -= 0x1000;
    return (int64_t)code * 100;
}

/* Proves one instance is the controller the caller measured and is producing temperatures, once per
 * poll before any of its sensor words are believed. Returns 1 after recording a trip. */
static int check_instance(struct hamoa_thermal_state *s, struct early_stop_state *stop, uint64_t now, uint64_t hz,
                          const struct hamoa_thermal_io *io, uint32_t instance, uint32_t required)
{
    uint64_t srot = SROT_BASE + instance * WINDOW_STRIDE;
    uint64_t tm = TM_BASE + instance * WINDOW_STRIDE;
    uint32_t word;

    if (read_word(s, io, srot + SROT_HW_VER, &word))
        return trip(s, stop, now, hz, HAMOA_THERMAL_READ, HAMOA_THERMAL_DETAIL_NONE, s->location);
    s->version[instance] = word;
    if ((uint64_t)word != s->hw_version)
        return trip(s, stop, now, hz, HAMOA_THERMAL_CONFIG, HAMOA_THERMAL_DETAIL_VERSION, srot + SROT_HW_VER);

    if (read_word(s, io, srot + SROT_CTRL, &word))
        return trip(s, stop, now, hz, HAMOA_THERMAL_READ, HAMOA_THERMAL_DETAIL_NONE, s->location);
    s->control[instance] = word;
    /* Extra enabled sensors are harmless; a polled sensor that is not enabled has no live word. */
    if (!(word & CTRL_ENABLE) || (word & CTRL_SW_RESET) || !(word & CTRL_TEMPERATURE_FORMAT) ||
        ((((word >> CTRL_SENSOR_SHIFT) & CTRL_SENSOR_MASK) & required) != required))
        return trip(s, stop, now, hz, HAMOA_THERMAL_CONFIG, HAMOA_THERMAL_DETAIL_CONTROL, srot + SROT_CTRL);

    if (read_word(s, io, tm + TM_TRDY, &word))
        return trip(s, stop, now, hz, HAMOA_THERMAL_READ, HAMOA_THERMAL_DETAIL_NONE, s->location);
    s->trdy[instance] = word;
    return 0;
}

static int sample(struct hamoa_thermal_state *s, struct early_stop_state *stop, uint64_t now, uint64_t hz,
                  const struct hamoa_thermal_io *io, const uint32_t required[HAMOA_THERMAL_INSTANCES])
{
    uint32_t checked = 0;
    uint64_t k, done;

    s->current_mc = INT64_MIN;
    s->valid_count = 0;
    s->missing_count = 0;
    s->valid_slots = 0;
    for (k = 0; k < s->slot_count; k++) {
        uint32_t instance = (uint32_t)(s->slot[k] >> 8), id = (uint32_t)(s->slot[k] & 0xffu);
        uint64_t srot = SROT_BASE + instance * WINDOW_STRIDE;
        uint64_t tm = TM_BASE + instance * WINDOW_STRIDE;
        uint64_t status_address = tm + TM_STATUS + 4u * id;
        uint64_t conversion_address = srot + SROT_CONVERSION + 4u * id;
        uint64_t seen = (s->seen_slots >> k) & 1u;
        uint32_t word, before;
        int64_t mc;

        if (!(checked & (1u << instance))) {
            if (check_instance(s, stop, now, hz, io, instance, required[instance]))
                return 1;
            checked |= 1u << instance;
        }
        if (read_word(s, io, conversion_address, &word))
            return trip(s, stop, now, hz, HAMOA_THERMAL_READ, HAMOA_THERMAL_DETAIL_NONE, s->location);
        s->conversion[k] = word;
        /* A zero word means no shift, slope or offset was programmed, so the status code below would
         * not be a temperature (INFERRED; whether firmware programs it is an X2 measurement). */
        if (!word)
            return trip(s, stop, now, hz, HAMOA_THERMAL_UNCALIBRATED, HAMOA_THERMAL_DETAIL_NONE, conversion_address);

        if (read_word(s, io, status_address, &word))
            return trip(s, stop, now, hz, HAMOA_THERMAL_READ, HAMOA_THERMAL_DETAIL_NONE, s->location);
        before = (uint32_t)s->raw[k];
        s->raw[k] = word;
        /* A violated critical or max threshold stops even if VALID is clear: the flag is the hardware's
         * own report and does not depend on this sample being fresh. */
        if (word & (STATUS_CRITICAL | STATUS_MAX))
            return trip(s, stop, now, hz, HAMOA_THERMAL_CRITICAL, HAMOA_THERMAL_DETAIL_NONE, status_address);
        if (!(word & STATUS_VALID)) {
            uint64_t since = seen ? s->last_valid[k] : s->started;

            s->missing_count++;
            s->invalid_samples++;
            if (now - since >= hz)
                return trip(s, stop, now, hz,
                            (s->trdy[instance] & TRDY_READY) ? HAMOA_THERMAL_INVALID : HAMOA_THERMAL_NOT_READY,
                            HAMOA_THERMAL_DETAIL_NONE, status_address);
            continue;
        }
        mc = millidegrees(word);
        if (mc < HAMOA_THERMAL_PLAUSIBLE_MIN_MC || mc > HAMOA_THERMAL_PLAUSIBLE_MAX_MC)
            return trip(s, stop, now, hz, HAMOA_THERMAL_RANGE, HAMOA_THERMAL_DETAIL_NONE, status_address);
        if (mc > s->current_mc)
            s->current_mc = mc;
        if (mc > s->peak_mc)
            s->peak_mc = mc;
        s->valid_count++;
        s->valid_slots |= UINT64_C(1) << k;
        s->seen_slots |= UINT64_C(1) << k;
        s->last_valid[k] = now;
        /* Progress needs two consecutive valid words that differ: a word that follows a missing VALID
         * has no trustworthy predecessor, so it cannot keep a frozen controller looking alive. */
        if (!seen || ((before & STATUS_VALID) && ((before ^ word) & STATUS_CODE_MASK)))
            s->last_change = now;
        if (mc >= s->limit_mc)
            return trip(s, stop, now, hz, HAMOA_THERMAL_HOT, HAMOA_THERMAL_DETAIL_NONE, status_address);
    }
    done = io->counter(io->ctx);
    s->poll_ticks = done - now;
    if (s->poll_ticks > s->max_poll_ticks)
        s->max_poll_ticks = s->poll_ticks;
    if (s->poll_ticks >= hz / 4u)
        return trip(s, stop, done, hz, HAMOA_THERMAL_SLOW, HAMOA_THERMAL_DETAIL_NONE, s->location);
    s->unchanged_ticks = done - s->last_change;
    if (s->unchanged_ticks >= (uint64_t)UNCHANGED_SECONDS * hz)
        return trip(s, stop, done, hz, HAMOA_THERMAL_UNCHANGED, HAMOA_THERMAL_DETAIL_NONE, s->location);
    s->ready = s->valid_count == s->slot_count;
    return 0;
}

int hamoa_thermal_poll(struct hamoa_thermal_state *s, struct early_stop_state *stop, uint64_t now, uint64_t hz,
                       const struct hamoa_thermal_io *io)
{
    uint32_t required[HAMOA_THERMAL_INSTANCES];
    uint64_t gap;

    if (!s)
        return 1;
    if (!s->magic) {
        begin(s, now);
        return trip(s, stop, now, hz, HAMOA_THERMAL_NO_SOURCE, HAMOA_THERMAL_DETAIL_NO_STATE, 0);
    }
    if (s->reason)
        return 1;
    if (s->magic != HAMOA_THERMAL_MAGIC || s->abi != HAMOA_THERMAL_ABI || s->bytes != sizeof *s)
        return trip(s, stop, now, hz, HAMOA_THERMAL_CONFIG, HAMOA_THERMAL_DETAIL_BAD_HEADER, 0);
    if (!io || !io->read32 || !io->counter)
        return trip(s, stop, now, hz, HAMOA_THERMAL_CONFIG, HAMOA_THERMAL_DETAIL_NO_IO, 0);
    if (!stop)
        return trip(s, stop, now, hz, HAMOA_THERMAL_CONFIG, HAMOA_THERMAL_DETAIL_NO_STOP, 0);
    if (validate(s, stop, now, hz, required))
        return 1;
    if (!clock_ok(hz))
        return trip(s, stop, now, hz, HAMOA_THERMAL_CLOCK, HAMOA_THERMAL_DETAIL_NONE, 0);
    if (s->polls) {
        gap = now - s->last_poll;
        if (gap > s->max_gap_ticks)
            s->max_gap_ticks = gap;
        /* A gap of a second or more means the sensors went unwatched for that long: the guard cannot
         * vouch for what happened, so it stops rather than resume. */
        if (gap >= hz)
            return trip(s, stop, now, hz, HAMOA_THERMAL_LATE, HAMOA_THERMAL_DETAIL_NONE, 0);
        if ((int64_t)(now - s->next_poll) < 0)
            return 0;
    }
    s->polls++;
    s->last_poll = now;
    s->next_poll = now + hz / 4u;
    return sample(s, stop, now, hz, io, required);
}

uint64_t hamoa_thermal_wait(const struct hamoa_thermal_state *s, uint64_t now, uint64_t hz, uint64_t limit)
{
    int64_t left;

    if (!s || s->magic != HAMOA_THERMAL_MAGIC || s->reason || !clock_ok(hz))
        return 0;
    left = (int64_t)(s->next_poll - now);
    if (left <= 0)
        return 0;
    return (uint64_t)left < limit ? (uint64_t)left : limit;
}

static void spin(const struct hamoa_thermal_io *io, uint64_t ticks)
{
    uint64_t start = io->counter(io->ctx);
    uint32_t guard;

    for (guard = 0; guard < SPIN_ITERATION_LIMIT; guard++)
        if (io->counter(io->ctx) - start >= ticks)
            return;
}

int hamoa_thermal_admit(struct hamoa_thermal_state *s, struct early_stop_state *stop, uint64_t hz,
                        const struct hamoa_thermal_io *io)
{
    uint64_t begin_ticks;
    uint32_t n;

    if (!io || !io->counter)
        return hamoa_thermal_poll(s, stop, 0, hz, io) ? 1 : -1;
    begin_ticks = io->counter(io->ctx);
    for (n = 0; n < HAMOA_THERMAL_ADMIT_SAMPLES; n++) {
        uint64_t now = io->counter(io->ctx);

        if (n && now - begin_ticks >= hz / 2u)
            break;
        /* Every attempt must be a real sample, not the previous poll's verdict, so it is made due. */
        if (s && s->magic == HAMOA_THERMAL_MAGIC && !s->reason)
            s->next_poll = now;
        if (hamoa_thermal_poll(s, stop, now, hz, io))
            return 1;
        if (s->ready)
            return 0;
        spin(io, hz / 100u);
    }
    return -1;
}
