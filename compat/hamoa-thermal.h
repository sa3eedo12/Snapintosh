/* SPDX-License-Identifier: MIT
 * Surface Pro 11 (Snapdragon X Elite X1E80100, "Hamoa") read-only thermal guard model.
 *
 * EXPERIMENTAL host model. It has never touched an SP11: every register fact below is INFERRED from
 * public sources until the X2 survey measures it on the device. The shape mirrors compat/a16-thermal.h
 * (state that is also the report, a read32 that is never retried, a counter callback, poll/admit/wait and
 * a register allow-list) but no code is shared, and nothing here is linked into the default A16 monitor:
 * it is built only through the -DSNAP_PLATFORM_SP11=1 tuple in probes/run.py.
 *
 * Read-only by construction. struct hamoa_thermal_io has no write callback, so the guard cannot change a
 * sensor controller's enables, thresholds, measurement period or reset; programming them stays with
 * firmware. Every condition that prevents a trustworthy temperature (no sensor list, unmeasured
 * version, controller not in temperature mode, sensor not calibrated, stale or implausible code, late or
 * slow polling, bad counter frequency, failed read) is an immutable trip that asks the monitor for an
 * immediate stop through early_stop_request(EARLY_STOP_SOURCE_THERMAL). Nothing is retried or merged
 * across samples, and a missing source is never "okay".
 *
 * Public sources the register facts come from (facts only, nothing copied), pinned to Linux v7.4-rc2,
 * commit 7475ea8b58616b33c04afb976cf0fbaad9dd63fb:
 *   arch/arm64/boot/dts/qcom/hamoa.dtsi (BSD-3-Clause): four controller instances, each with a TM and a
 *     SROT window and sixteen sensor ids (lines 2759-2817); the sensors the SoC thermal zones read
 *     (thermal-zones node from line 9123): 13, 11, 11 and 15 ids counted from 0 on instances 0..3.
 *   drivers/thermal/qcom/tsens-v2.c (GPL-2.0): SROT/TM offsets (lines 14-17, 44-45), control, status
 *     and ready fields (lines 86-138), and the absence of any calibration step in the generic v2 ops
 *     (lines 263-272, against the one op set that calibrates, lines 280-284).
 *   drivers/thermal/qcom/tsens.c (GPL-2.0): a 12-bit two's-complement field in deci-degrees Celsius
 *     (tsens_hw_to_mC, lines 376-400); per-sensor VALID, not TRDY, gates a v2 read (tsens_read_temp,
 *     lines 329-373, and get_temp_tsens_valid, lines 812-823). That driver also falls back to a "stable"
 *     value after three invalid samples; this guard deliberately does not.
 *   Documentation/devicetree/bindings/thermal/qcom-tsens.yaml: X1E80100 binds as the v2 fallback.
 * The DT trip points (critical 115 C, hot 90 C, passive 95 C) are generic SoC values, not SP11 chassis
 * limits. The SP11 cooling design is UNKNOWN (docs/SURFACE-PRO-11.md, R14).
 *
 * Still unproven on the SP11 (X2 must measure each): that EL2 may read these windows at all, the HW_VER
 * word, that firmware leaves every polled controller enabled, in temperature mode and calibrated, which
 * sensors are really populated, and whether TRDY ever reads clear while VALID is set.
 *
 * Threading: one state belongs to one caller at a time. There is no internal lock; the monitor calls this
 * under the same service lock that early_stop_request() already expects. Nothing here allocates, blocks,
 * retries a read or touches memory other than the state, the caller's io and its early-stop state.
 */
#pragma once
#include <stdint.h>

#define HAMOA_THERMAL_MAGIC UINT64_C(0x314d524548544d48) /* "HMTHERM1" in memory order */
#define HAMOA_THERMAL_ABI 1u
#define HAMOA_THERMAL_INSTANCES 4u
#define HAMOA_THERMAL_SENSORS_PER_INSTANCE 16u
#define HAMOA_THERMAL_SLOTS 64u        /* every id of every instance; a slot is one polled sensor */
#define HAMOA_THERMAL_POLL_MS 250u
#define HAMOA_THERMAL_ADMIT_SAMPLES 20u /* resamples inside half a second, as the A16 admission retry */
#define HAMOA_THERMAL_REPORT_WORDS 298u

/* The ceiling is a parameter: there is no built-in default. It is rejected outside this window so that a
 * unit slip (80 instead of 80000) or an unset zero cannot disable the guard. The upper bound is the
 * critical trip the public DT gives for these sensors. */
#define HAMOA_THERMAL_LIMIT_MIN_MC 30000
#define HAMOA_THERMAL_LIMIT_MAX_MC 115000
/* PROPOSED, NOT A DECISION: 80 C sits below every SoC trip in the public DT (hot 90 C, passive 95 C,
 * critical 115 C), and the SP11's cooling and chassis limits are UNKNOWN, so it is only a conservative
 * placeholder; no SP11 reading supports it. The user chooses the real value; nothing in this unit applies
 * this constant on its own. */
#define HAMOA_THERMAL_LIMIT_MC_PROPOSED 80000
/* Plausibility window of a decoded code, independent of the ceiling. A reading outside it means the
 * sensor, the controller mode or the decode is wrong, not that the SoC is that temperature. */
#define HAMOA_THERMAL_PLAUSIBLE_MIN_MC (-40000)
#define HAMOA_THERMAL_PLAUSIBLE_MAX_MC 120000

_Static_assert(HAMOA_THERMAL_LIMIT_MC_PROPOSED >= HAMOA_THERMAL_LIMIT_MIN_MC &&
               HAMOA_THERMAL_LIMIT_MC_PROPOSED <= HAMOA_THERMAL_LIMIT_MAX_MC, "proposed ceiling inside the accepted window");
_Static_assert(HAMOA_THERMAL_LIMIT_MAX_MC <= HAMOA_THERMAL_PLAUSIBLE_MAX_MC, "a legal ceiling is a plausible reading");
_Static_assert(HAMOA_THERMAL_INSTANCES * HAMOA_THERMAL_SENSORS_PER_INSTANCE == HAMOA_THERMAL_SLOTS,
               "one report bit per sensor id of every instance");

/* Values 0-10 keep the A16 numbering so one decoder habit serves both; 11-13 are SP11 additions. */
enum hamoa_thermal_reason {
    HAMOA_THERMAL_OK, HAMOA_THERMAL_HOT, HAMOA_THERMAL_CONFIG, HAMOA_THERMAL_INVALID,
    HAMOA_THERMAL_LATE, HAMOA_THERMAL_READ, HAMOA_THERMAL_RANGE, HAMOA_THERMAL_CRITICAL,
    HAMOA_THERMAL_UNCHANGED, HAMOA_THERMAL_SLOW, HAMOA_THERMAL_CLOCK,
    HAMOA_THERMAL_NO_SOURCE,    /* nothing configured to read: no sensors, no measured version, no state */
    HAMOA_THERMAL_NOT_READY,    /* VALID absent for a second while the controller's TRDY is clear */
    HAMOA_THERMAL_UNCALIBRATED  /* a polled sensor's conversion word reads zero (INFERRED: unprogrammed) */
};

/* Cause of a configuration-level trip, in the state's detail word; zero for trips on a sensor, which
 * location identifies instead. */
enum hamoa_thermal_detail {
    HAMOA_THERMAL_DETAIL_NONE,
    HAMOA_THERMAL_DETAIL_NO_CONFIG,   /* init without a configuration */
    HAMOA_THERMAL_DETAIL_NO_STATE,    /* poll on a state init never prepared */
    HAMOA_THERMAL_DETAIL_BAD_HEADER,  /* magic, ABI or size word changed */
    HAMOA_THERMAL_DETAIL_NO_IO,       /* read32 or counter missing */
    HAMOA_THERMAL_DETAIL_NO_STOP,     /* no early-stop state to request a stop on */
    HAMOA_THERMAL_DETAIL_LIMIT,       /* ceiling outside the accepted window, or unset */
    HAMOA_THERMAL_DETAIL_NO_SENSORS,  /* empty sensor list */
    HAMOA_THERMAL_DETAIL_TOO_MANY,    /* more sensors than slots */
    HAMOA_THERMAL_DETAIL_BAD_SENSOR,  /* instance or id out of range */
    HAMOA_THERMAL_DETAIL_DUPLICATE,   /* the same instance and id listed twice */
    HAMOA_THERMAL_DETAIL_NO_VERSION,  /* expected HW_VER not measured yet */
    HAMOA_THERMAL_DETAIL_VERSION,     /* SROT HW_VER differs from the expected word */
    HAMOA_THERMAL_DETAIL_CONTROL      /* SROT control word not in the required state */
};

/* One polled sensor: controller instance 0..3 and hardware sensor id 0..15. */
struct hamoa_thermal_sensor {
    uint8_t instance;
    uint8_t id;
};

/* What a caller must supply; init copies it. Nothing here is defaulted for the caller. */
struct hamoa_thermal_config {
    int64_t limit_mc;     /* ceiling in millidegrees C, within HAMOA_THERMAL_LIMIT_{MIN,MAX}_MC */
    uint32_t hw_version;  /* SROT HW_VER word every polled instance must report; 0 = not measured yet */
    uint32_t count;       /* valid leading entries of sensors[] */
    struct hamoa_thermal_sensor sensors[HAMOA_THERMAL_SLOTS];
};

/* Exactly HAMOA_THERMAL_REPORT_WORDS u64 words and no padding. Signed temperature words are sign
 * extended; a never-sampled current_mc/peak_mc is INT64_MIN. The state is also the report: the monitor
 * copies it out and tools/hamoa_thermal.py decodes it, so the order below is the ABI. */
struct hamoa_thermal_state {
    uint64_t magic, abi, bytes, polls, reads, started, last_poll, next_poll;
    uint64_t last_change, trip_ticks, reason, location;
    int64_t current_mc, peak_mc;
    uint64_t valid_count, missing_count, max_gap_ticks, invalid_samples, read_failures;
    uint64_t poll_ticks, max_poll_ticks, ready, unchanged_ticks, read_inflight;
    int64_t limit_mc;
    uint64_t hw_version;
    uint64_t slot_count;
    uint64_t valid_slots, seen_slots; /* bit k = slot k: valid in the latest poll / ever valid */
    uint64_t detail;                  /* enum hamoa_thermal_detail */
    uint64_t version[4], control[4], trdy[4]; /* latest raw words per instance; 0 until read */
    uint64_t slot[64];       /* instance << 8 | sensor id, in polling order */
    uint64_t raw[64];        /* latest status word of each slot */
    uint64_t conversion[64]; /* latest SROT conversion word of each slot */
    uint64_t last_valid[64]; /* counter value of each slot's latest valid code */
};
_Static_assert(sizeof(struct hamoa_thermal_state) == HAMOA_THERMAL_REPORT_WORDS * 8, "thermal report ABI");
_Static_assert(sizeof(struct hamoa_thermal_state) <= 4096, "one monitor-only page");

/* read32 returns nonzero when the read did not complete; that stops the poll and is never retried.
 * counter ticks at the hz the caller passes to poll/admit. There is deliberately no write member. */
struct hamoa_thermal_io {
    void *ctx;
    int (*read32)(void *ctx, uint64_t physical_address, uint32_t *value);
    uint64_t (*counter)(void *ctx);
};
_Static_assert(sizeof(struct hamoa_thermal_io) == 3 * sizeof(void *), "the I/O struct has exactly ctx, read32, counter");

struct early_stop_state;

/* 1 when address is a 4-byte-aligned register the guard reads: per instance the SROT HW_VER and control
 * words, the sixteen SROT conversion words, the sixteen TM status words and the TM ready word. Anything
 * else, including every threshold, interrupt and measurement-period register, is 0. */
int hamoa_thermal_address_allowed(uint64_t address);

/* Fills cfg with the 50 sensors the public DT thermal zones read (INFERRED for the SP11) and leaves
 * limit_mc and hw_version at 0, so the result is refused until a ceiling and a measured version are set. */
void hamoa_thermal_default_sensors(struct hamoa_thermal_config *cfg);

/* Prepares a pristine state (magic 0); touches no device. Returns 0 when armed, 1 when it recorded a trip
 * (see reason and detail), -1 when refused because state is NULL or already used; a refused state is left
 * unchanged. A NULL cfg is a NO_SOURCE trip. stop receives the early-stop request of a trip and must be
 * non-NULL for the guard to be usable. */
int hamoa_thermal_init(struct hamoa_thermal_state *state, struct early_stop_state *stop, uint64_t now,
                       uint64_t hz, const struct hamoa_thermal_config *cfg);

/* Ticks until the next poll is due, capped at limit; 0 when the state is unusable or hz invalid. */
uint64_t hamoa_thermal_wait(const struct hamoa_thermal_state *, uint64_t now, uint64_t hz, uint64_t limit);

/* One paced sample of every configured sensor at counter value now, hz ticks per second (1000..1e9).
 * Returns 0 while healthy (including when not yet due, with no device access) and 1 once tripped. */
int hamoa_thermal_poll(struct hamoa_thermal_state *, struct early_stop_state *, uint64_t now, uint64_t hz,
                       const struct hamoa_thermal_io *);

/* Initial admission. 0: one complete sample of every configured sensor. 1: tripped. -1: no complete
 * sample within HAMOA_THERMAL_ADMIT_SAMPLES attempts or half a second. */
int hamoa_thermal_admit(struct hamoa_thermal_state *, struct early_stop_state *, uint64_t hz,
                        const struct hamoa_thermal_io *);
