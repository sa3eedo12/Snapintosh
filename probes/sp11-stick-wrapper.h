/* SPDX-License-Identifier: MIT */
#ifndef SNAP_PROBES_SP11_STICK_WRAPPER_H
#define SNAP_PROBES_SP11_STICK_WRAPPER_H
/* Internal interface of the SP11 stick wrapper (SNAPMON.BIN): the context that lives in the payload's stack and the functions its
 * three translation units share (docs/SP11-STICK-WRAPPER.md, section 3). Not an interface of the bundle: that is
 * probes/sp11-stick-bundle.h, whose functions the wrapper calls and whose order of checks it keeps.
 *
 * This build of the wrapper contains no monitor and never calls one: nothing here arms a timer, touches the GIC or writes an EL2
 * system register. UNIMPLEMENTED: the monitor entry (work items 4b, 8 and 11). The stages PREPARED, MONITOR_ENTERED and
 * MONITOR_RETURNED are never written and the report fields that belong to them stay zero.
 *
 * Evidence: EXPERIMENTAL; hardware NOT RUN. */
#include "sp11-stick-hw.h"

#if SNAP_SP11_STICK
#include "sp11-stick-common.h"
#include "sha256.h"

/* The hash is q1n1's freestanding q1n1_sha256, the file probes/run.py already links into the monitor: this wrapper carries no hash
 * code of its own. The adapter is for the types only: the interface's callback takes uint64_t, the runner's function size_t. */
static inline void sw_sha256(const void *data, uint64_t size, uint8_t digest[32])
{
    q1n1_sha256(data, (size_t)size, digest);
}

/* The digests the wrapper pays for beyond the table digest, which is always checked: the chunk digests and the whole-file digest
 * (SP11SB_HASH_*). Both by default. Hashing speed on the unit is UNKNOWN (U6): the dry run measures it, and a cheaper policy is a
 * rebuild with a different value, no change of format. The report records the policy used. */
#ifndef SP11_STICK_HASH_POLICY
#define SP11_STICK_HASH_POLICY (SP11SB_HASH_CHUNKS | SP11SB_HASH_FILE)
#endif
_Static_assert(!(SP11_STICK_HASH_POLICY & ~(SP11SB_HASH_CHUNKS | SP11SB_HASH_FILE)), "SP11_STICK_HASH_POLICY: only the CHUNKS and FILE bits");

/* The monitor build's own GIC macros, which PIN.DAT pins (sp11sb_observed_check, GIC_MISMATCH). This wrapper has no monitor, so
 * its build must be given the values the monitor build will take: with no default, like probes/secondary.c on the SP11, because a
 * default would make the comparison with the pin trivially true. */
#if !defined(SNAP_GICD_BASE) || !defined(SNAP_GICR_FIRST) || !defined(SNAP_GICR_LIMIT) || !defined(SNAP_GICR_STRIDE) || !defined(SNAP_TIMER_INTID)
#error "SNAP_SP11_STICK=1 needs the five GIC macros of the monitor build (SNAP_GICD_BASE, SNAP_GICR_FIRST, SNAP_GICR_LIMIT, SNAP_GICR_STRIDE, SNAP_TIMER_INTID)"
#endif

/* Test-only faults (tools/test-sp11-stick-wrapper.py proves the tests can fail by building each). They exist only in the host build. */
#if (defined(SP11_STICK_SEED_EARLY_STORE) || defined(SP11_STICK_SEED_NO_CHUNK_CHECK) || defined(SP11_STICK_SEED_NO_PREPASS) || \
     defined(SP11_STICK_SEED_NO_CLEAN) || defined(SP11_STICK_SEED_VERIFY_SOURCE) || defined(SP11_STICK_SEED_NO_AT)) && !defined(SP11_STICK_HOST_TEST)
#error "the seeded faults are part of the host test build only"
#endif

#define SW_RESERVED_LINES 3u            /* the last lines are for the refusal and the end, whatever else was said */
#define SW_PROBE_STEP (UINT64_C(2) << 20)
#define SW_PAGE UINT64_C(4096)

/* What probing a range found: the first failing address and what the machine said about it (the raw PAR_EL1 of an AT, or the result
 * code of the guarded read when load32 is set), the number of ATs issued, and the memory attribute and shareability of the first
 * page (what U6 asks about the big file: is it cacheable). */
struct sw_probe {
    uint64_t at, par;
    uint32_t count;
    uint8_t attr, sh, load32;
};

struct sw_ctx {
    const struct q1n1_payload_args *a;
    union {
        struct sp11sb_report r;
        uint64_t w[SP11SB_REPORT_HEAD_BYTES / 8];
    } head;                             /* the report head as it is in REPORT.DAT: kept here, stored whole after every stage */
    struct sp11sb_file files[SP11SB_FILES];
    struct sp11sb_range declared[SP11SB_DECLARED_MAX];
    uint64_t report_pa;                 /* 0 until REPORT.DAT's slot passed and its pages probed writable: nothing is stored before */
    uint64_t pin_len, guest_len, ticks_enter;
    const uint8_t *pin;
    const uint8_t *guest;
    uint32_t nfiles, ndeclared, lines, hash_policy;
};
_Static_assert(sizeof(struct sw_ctx) < 2048, "the wrapper's stack budget is 32 KiB; its context is a small part of it");

/* sp11-stick-report.c: the report head and the panel lines. */
void sw_ctx_init(struct sw_ctx *c, const struct q1n1_payload_args *a, uint64_t ticks_enter);
/* Writes page 0 of REPORT.DAT (zero tail first, then the head) and cleans it to the point of coherency. */
void sw_head_init(struct sw_ctx *c);
/* Stores the head whole (37 words) and cleans its lines, so a hang or a restart leaves the last stage in RAM; no-op before report_pa. */
void sw_head_flush(struct sw_ctx *c);
void sw_set_stage(struct sw_ctx *c, uint32_t stage);
/* "SNAPMON " in front of a line, and the line out through the runner, counted against the 30 the interface allows. */
void sw_begin(struct pl_line *l);
void sw_emit(struct sw_ctx *c, const struct pl_line *l, int essential);
void sw_say(struct sw_ctx *c, const char *text);
void sw_say_stage(struct sw_ctx *c);
/* Records the refusal (when REPORT.DAT is usable), prints it and returns what payload_entry returns: 0x1000 + code. */
uint64_t sw_refuse(struct sw_ctx *c, uint32_t code, uint64_t detail);

/* sp11-stick-load.c: probes, the copy and its verification. Each returns 0 or a refusal code of the interface. */
/* AT over the pages of [base, base + size) the wrapper will touch: the first, the last and every 2 MiB boundary between. A page
 * passes when it is mapped, identity-mapped and, for a write probe, Normal write-back (and so writable: a read-only page is a
 * permission fault for AT S1E2W). Otherwise PLACE_UNREADABLE with p->at the probe address. */
int sw_probe_range(uint64_t base, uint64_t size, int write, struct sw_probe *p);
/* PLACE_UNREADABLE row: the guarded read of the placement's first and last word, then the write probes. *detail is the address. */
int sw_place_runtime(const struct sw_ctx *c, uint64_t place_base, uint64_t place_size, uint64_t *detail, struct sw_probe *p);
/* The chunk table against the placement and the file, with no store: the check that opens the copy, and the one before each store. */
int sw_chunks_check(const struct sw_ctx *c, uint32_t *bad);
int sw_copy_all(struct sw_ctx *c, uint32_t *bad);
int sw_verify_all(struct sw_ctx *c, uint32_t *bad);
#endif /* SNAP_SP11_STICK */
#endif /* SNAP_PROBES_SP11_STICK_WRAPPER_H */
