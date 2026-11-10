/* SPDX-License-Identifier: MIT */
/* Native tests for compat/ipa-geometry.c, and the plan CLI that
 * tools/test-stage2-geometry.py drives for its DRAM scenarios.
 *
 * The model is checked against the code it models rather than against a second
 * hand-written description of it: the span check must accept exactly the lists
 * shadow/stage2.c accepts (stage2_build over a RAM-backed shadow), the walk
 * start level must equal shadow/mmu.c's, and the leaf and table counts of a
 * plan must equal what stage2_build really emits when its ranges are built and
 * the resulting tree is walked. Planner outcomes are held against an
 * independent oracle that uses 128-bit sums and division where the planner
 * uses masks, so the two cannot share an arithmetic mistake.
 *
 * cc -std=gnu11 -O1 -g -fsanitize=address,undefined -Ishadow -Icompat \
 *    tools/test-stage2-geometry.c compat/ipa-geometry.c shadow/mmu.c \
 *    shadow/shadow.c shadow/stage2.c -o build/test-stage2-geometry
 *
 * No argument: run every test. --plan ...: print one plan as key=value lines.
 * --defaults: print the constants the drift guard compares to the sources.
 */
#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "ipa-geometry.h"
#include "stage2.h"

#define COUNT(a) (sizeof(a) / sizeof((a)[0]))
#define KIB (1ULL << 10)
#define MIB (1ULL << 20)
#define GIB (1ULL << 30)

static int checks, failures;

static void check_at(int condition, const char *label, int line)
{
    if (condition) {
        checks++;
    } else {
        failures++;
        printf("FAIL: %s (line %d)\n", label, line);
    }
}
#define check(condition, label) check_at(!!(condition), label, __LINE__)

/* The stage-2 tables live in a model of the monitor's work area. Tables are
 * zeroed when allocated, so a reset only has to rewind the allocator. */
static const uint64_t RAM_BASE = 0x82500000ULL;
#define RAM_SIZE (4UL * 1024 * 1024)
static uint8_t ram[RAM_SIZE];
static uint64_t next_table;
static unsigned allocations;

static int ram_read(void *ctx, uint64_t pa, uint64_t *out)
{
    (void)ctx;
    if (pa < RAM_BASE || pa + 8 > RAM_BASE + RAM_SIZE) return -1;
    memcpy(out, &ram[pa - RAM_BASE], 8);
    return 0;
}

static int ram_write(void *ctx, uint64_t pa, uint64_t value)
{
    (void)ctx;
    if (pa < RAM_BASE || pa + 8 > RAM_BASE + RAM_SIZE) return -1;
    memcpy(&ram[pa - RAM_BASE], &value, 8);
    return 0;
}

static uint64_t ram_alloc(void *ctx)
{
    (void)ctx;
    if (next_table + 0x1000 > RAM_BASE + RAM_SIZE) return 0;
    uint64_t table = next_table;
    next_table += 0x1000;
    memset(&ram[table - RAM_BASE], 0, 0x1000);
    allocations++;
    return table;
}

static void new_shadow(struct shadow *s, unsigned ipa_bits)
{
    memset(s, 0, sizeof(*s));
    s->txsz = 64 - ipa_bits;
    s->alloc = ram_alloc;
    s->read = ram_read;
    s->write = ram_write;
    next_table = RAM_BASE;
    allocations = 0;
    s->root = ram_alloc(NULL);
}

static uint64_t rng_state = 0x9e3779b97f4a7c15ULL;

static uint64_t rnd(void)
{
    rng_state ^= rng_state >> 12;
    rng_state ^= rng_state << 25;
    rng_state ^= rng_state >> 27;
    return rng_state * 0x2545f4914f6cdd1dULL;
}

static uint64_t rnd_below(uint64_t n)
{
    return n ? rnd() % n : 0;
}

/* ---- the parameters the monitor and the model share ------------------------ */

static void test_parameters(void)
{
    static const unsigned bits[] = {32, 36, 40, 42, 44, 48, 48};
    for (unsigned parange = 0; parange < COUNT(bits); parange++)
        check(ipag_pa_bits(parange) == bits[parange], "PARange encodings 0..6 decode to 32..48 bits");
    check(ipag_pa_bits(7) == 0 && ipag_pa_bits(15) == 0 && ipag_pa_bits(0xffff) == 0,
          "PARange encodings above 6 are not decoded");

    for (unsigned b = 31; b <= 49; b++) {
        int want = (b < IPAG_MIN_IPA_BITS || b > IPAG_MAX_IPA_BITS) ? -1 : mmu_start_level(12, 64 - b);
        check(ipag_start_level(b) == want, "ipag_start_level agrees with mmu_start_level for every width");
    }
    check(ipag_start_level(0) == -1 && ipag_start_level(64) == -1 && ipag_start_level(0xffffffffu) == -1,
          "widths far outside the model have no start level");
    check(IPAG_DEFAULT_IPA_BITS == 64 - 28, "the default width is what S2_TXSZ=28 builds");

    for (int r = IPAG_OK; r < IPAG_REASON_COUNT; r++)
        check(strcmp(ipag_reason_name(r), "UNKNOWN") != 0, "every reason has a name");
    check(!strcmp(ipag_reason_name(IPAG_REASON_COUNT), "UNKNOWN") && !strcmp(ipag_reason_name(-1), "UNKNOWN"),
          "a reason outside the enum is named UNKNOWN, never as a success");
}

/* ---- ipag_check_spans, one reason at a time -------------------------------- */

static int spans(unsigned ipa_bits, unsigned parange, unsigned *index, const struct ipag_span *s, unsigned n)
{
    return ipag_check_spans(s, n, ipa_bits, parange, index);
}

static void test_check_spans(void)
{
    const uint64_t limit = 1ULL << 36;
    unsigned at;
    struct ipag_span ok[] = {{0x80000000ULL, 0x90000000ULL}, {0x17000000ULL, 0x17001000ULL}};

    check(spans(36, 4, &at, ok, 2) == IPAG_OK && at == IPAG_NO_INDEX,
          "an unsorted list is accepted by the monitor check, like stage2_build");
    check(spans(36, 4, NULL, ok, 2) == IPAG_OK, "bad_index is optional");

    struct ipag_span last_page[] = {{limit - 0x1000, limit}};
    check(spans(36, 4, &at, last_page, 1) == IPAG_OK, "a span ending exactly on 1<<36 is the last mappable one");
    struct ipag_span at_limit[] = {{limit, limit + 0x1000}};
    check(spans(36, 4, &at, at_limit, 1) == IPAG_WINDOW_ABOVE_LIMIT && at == 0, "a span starting on 1<<36 is above it");
    struct ipag_span across[] = {{0x1000, 0x2000}, {limit - 0x1000, limit + 0x1000}};
    check(spans(36, 4, &at, across, 2) == IPAG_WINDOW_CROSSES_LIMIT && at == 1, "a span across 1<<36 names itself");
    check(spans(37, 4, &at, across, 2) == IPAG_OK, "the same list fits a 37-bit IPA");

    struct ipag_span empty[] = {{0x8000, 0x8000}};
    check(spans(36, 4, &at, empty, 1) == IPAG_ZERO_SIZE, "a zero-size span is refused");
    struct ipag_span wrapped[] = {{0xfffffffffffff000ULL, 0x1000}};
    check(spans(36, 4, &at, wrapped, 1) == IPAG_INVERTED, "base + size that wrapped (hi < lo) is refused");
    struct ipag_span unaligned_lo[] = {{0x80000800, 0x90000000}};
    struct ipag_span unaligned_hi[] = {{0x80000000, 0x90000800}};
    check(spans(36, 4, &at, unaligned_lo, 1) == IPAG_UNALIGNED, "an unaligned start is refused");
    check(spans(36, 4, &at, unaligned_hi, 1) == IPAG_UNALIGNED, "an unaligned end is refused");

    struct ipag_span over[] = {{0x80000000, 0x90000000}, {0x1000, 0x2000}, {0x8fff0000, 0x90010000}};
    check(spans(36, 4, &at, over, 3) == IPAG_OVERLAP && at == 2, "an overlap names the later span");
    struct ipag_span touching[] = {{0x80000000, 0x90000000}, {0x90000000, 0xa0000000}};
    check(spans(36, 4, &at, touching, 2) == IPAG_OK, "spans that only touch are disjoint");

    struct ipag_span high[] = {{0x800000000ULL, 0x1100000000ULL}};
    check(spans(40, 1, &at, high, 1) == IPAG_ABOVE_PARANGE, "a span past the 36-bit PARange is refused though the IPA holds it");
    check(spans(40, 2, &at, high, 1) == IPAG_OK, "the same span is fine with a 40-bit PARange");
    check(spans(36, 6, &at, ok, 2) == IPAG_OK, "PARange encoding 6 is treated as 48 bits");
    check(spans(36, 7, &at, ok, 2) == IPAG_BAD_PARANGE, "an undecoded PARange refuses");

    check(spans(31, 4, &at, ok, 2) == IPAG_BAD_IPA_BITS && spans(49, 4, &at, ok, 2) == IPAG_BAD_IPA_BITS &&
              spans(0, 4, &at, ok, 2) == IPAG_BAD_IPA_BITS,
          "IPA widths outside [32, 48] refuse");
    check(spans(36, 4, &at, high, 1) == IPAG_WINDOW_CROSSES_LIMIT && at == 0,
          "a span ending 4 GiB past 1<<36 crosses it");
    check(spans(32, 4, &at, high, 1) == IPAG_WINDOW_ABOVE_LIMIT && at == 0,
          "the narrowest width still refuses what it cannot hold");
    check(spans(36, 4, &at, ok, 0) == IPAG_NO_WINDOWS, "an empty list refuses");
    check(spans(36, 4, &at, NULL, 1) == IPAG_NULL_INPUT, "a null list refuses");

    struct ipag_span many[IPAG_MAX_WINDOWS + 1];
    for (unsigned n = 0; n < COUNT(many); n++) {
        many[n].lo = 0x1000ULL * n;
        many[n].hi = many[n].lo + 0x1000;
    }
    check(spans(36, 4, &at, many, IPAG_MAX_WINDOWS) == IPAG_OK, "IPAG_MAX_WINDOWS spans are accepted");
    check(spans(36, 4, &at, many, IPAG_MAX_WINDOWS + 1) == IPAG_TOO_MANY_WINDOWS, "one more refuses");
}

/* ---- ipag_check_spans against the real builder ----------------------------- */

static uint64_t edge_address(uint64_t limit)
{
    switch (rnd_below(9)) {
    case 0: return limit - 0x1000 * (1 + rnd_below(4));
    case 1: return limit + 0x1000 * rnd_below(4);
    case 2: return rnd_below(limit >> 12) << 12;
    case 3: return (rnd_below(limit >> 12) << 12) | (1ULL << rnd_below(12));
    case 4: return 0;
    case 5: return limit >> 1;
    case 6: return UINT64_MAX - 0xfffULL * rnd_below(2);
    case 7: return rnd_below(limit >> 21) << 21;
    default: return rnd_below(limit >> 30) << 30;
    }
}

static uint64_t edge_size(void)
{
    switch (rnd_below(8)) {
    case 0: return 0;
    case 1: return 0x1000;
    case 2: return 0x1000 * (1 + rnd_below(64));
    case 3: return 2 * MIB * (1 + rnd_below(8));
    case 4: return GIB * (1 + rnd_below(16));
    case 5: return 0x1000 * (1 + rnd_below(1 << 20)) + (rnd_below(4) == 0 ? 0x10 : 0);
    case 6: return UINT64_MAX - 0xfffULL * rnd_below(3);
    default: return 2 * MIB * rnd_below(2048);
    }
}

/* The monitor check must accept exactly the lists stage2_build accepts, at
 * every width the model covers, with the PARange out of the way. */
static void test_against_stage2_build(void)
{
    static const unsigned widths[] = {32, 33, 36, 37, 38, 39, 40, 44, 48};
    unsigned accepted = 0, refused = 0;
    for (unsigned round = 0; round < 40000; round++) {
        unsigned bits = widths[rnd_below(COUNT(widths))];
        uint64_t limit = 1ULL << bits;
        unsigned n = 1 + (unsigned)rnd_below(5);
        struct ipag_span list[5];
        struct stage2_range ranges[5];
        for (unsigned i = 0; i < n; i++) {
            list[i].lo = edge_address(limit);
            list[i].hi = list[i].lo + edge_size();
            ranges[i] = (struct stage2_range){list[i].lo, list[i].hi, S2_AP_RW | S2_ATTRS, 0};
        }
        struct shadow s;
        new_shadow(&s, bits);
        int built = stage2_build(&s, ranges, n);
        int model = ipag_check_spans(list, n, bits, 5, NULL);
        if (model == IPAG_OK) accepted++;
        else refused++;
        if ((model == IPAG_OK) != (built != STAGE2_BAD_RANGES)) {
            check(0, "ipag_check_spans and stage2_build disagree");
            printf("  width %u, %u spans, model %s, stage2_build %d\n", bits, n, ipag_reason_name(model), built);
            for (unsigned i = 0; i < n; i++)
                printf("  [%#" PRIx64 ", %#" PRIx64 ")\n", list[i].lo, list[i].hi);
            return;
        }
    }
    check(accepted > 2000 && refused > 2000, "the random lists exercise both outcomes");
    printf("  %u lists accepted, %u refused, in agreement with stage2_build\n", accepted, refused);
}

/* ---- leaf and table counts against the tree stage2_build writes ------------ */

struct census {
    uint64_t leaves[4];
    int error;
};

static void walk_tree(uint64_t table, unsigned level, struct census *c)
{
    for (unsigned n = 0; n < 512; n++) {
        uint64_t d;
        if (ram_read(NULL, table + 8UL * n, &d)) {
            c->error = 1;
            return;
        }
        if (!(d & PT_VALID)) continue;
        if (level < 3 && (d & PT_TABLE)) walk_tree(d & PT_OA_MASK, level + 1, c);
        else c->leaves[level]++;
    }
}

static int plan_for(const struct ipag_input *in, struct ipag_plan *plan)
{
    return ipag_plan_stage2(in, plan);
}

/* Build the carve-out and MMIO ranges of an ALLOW plan for real and compare. */
static void check_plan_against_builder(const struct ipag_input *in, const struct ipag_plan *plan, const char *what)
{
    struct stage2_range ranges[IPAG_MAX_WINDOWS + 1];
    struct ipag_span list[IPAG_MAX_WINDOWS + 1];
    unsigned n = 0;
    ranges[n] = (struct stage2_range){plan->carve_base, plan->carve_base + plan->carve_size, S2_AP_RW | S2_ATTRS, 0};
    list[n].lo = ranges[n].lo;
    list[n].hi = ranges[n].hi;
    n++;
    for (unsigned m = 0; m < in->mmio_count; m++, n++) {
        ranges[n] = (struct stage2_range){in->mmio[m].base, in->mmio[m].base + in->mmio[m].size,
                                           S2_AP_RW | S2_ATTRS, 0};
        list[n].lo = ranges[n].lo;
        list[n].hi = ranges[n].hi;
    }
    check(ipag_check_spans(list, n, plan->ipa_bits, 5, NULL) == IPAG_OK, "an ALLOW plan's ranges pass the monitor check");

    struct shadow s;
    new_shadow(&s, plan->ipa_bits);
    int built = stage2_build(&s, ranges, n);
    if (built) {
        check(0, "an ALLOW plan's ranges build");
        printf("  %s: stage2_build returned %d\n", what, built);
        return;
    }
    struct census c = {{0}, 0};
    walk_tree(s.root, (unsigned)mmu_start_level(12, s.txsz), &c);
    int agree = !c.error && c.leaves[1] == plan->leaves_1g && c.leaves[2] == plan->leaves_2m &&
                c.leaves[3] == plan->leaves_4k && allocations - 1 == plan->table_pages &&
                plan->start_level == (unsigned)mmu_start_level(12, s.txsz) &&
                plan->root_entries == (1ULL << plan->ipa_bits) / mmu_level_size(12, plan->start_level);
    check(agree, "the plan's leaf mix, tables and root size equal what stage2_build wrote");
    if (!agree)
        printf("  %s: plan 1G %" PRIu64 " 2M %" PRIu64 " 4K %" PRIu64 " tables %" PRIu64
               "; built 1G %" PRIu64 " 2M %" PRIu64 " 4K %" PRIu64 " tables %u\n",
               what, plan->leaves_1g, plan->leaves_2m, plan->leaves_4k, plan->table_pages, c.leaves[1],
               c.leaves[2], c.leaves[3], allocations - 1);
}

static void test_mapping_plan(void)
{
    /* Alignments chosen so the same code path has to emit pages, 2 MiB blocks
     * and 1 GiB blocks, share one table between neighbours, and straddle a
     * table boundary. */
    static const struct ipag_window dram_mixed[] = {
        {0x80001000ULL, 0x40a00000ULL - 0x1000},                /* head pages, 2M blocks, tail pages */
        {0x200000000ULL, 3 * GIB},                              /* 1 GiB blocks only */
        {0x300000000ULL + 2 * MIB, GIB + 4 * MIB},              /* 2M blocks either side of a 1 GiB seam */
    };
    static const struct ipag_window mmio_mixed[] = {
        {0x17000000ULL, 0x1000},                                /* one page */
        {0x17002000ULL, 0x1000},                                /* same 2 MiB table as the page above */
        {0x17080000ULL, 0x300000},                              /* crosses a 2 MiB table boundary */
        {0x70000000ULL, 0x100000},
    };
    static const unsigned widths[] = {32 + 4, 36, 37, 39, 40, 48};
    for (unsigned w = 0; w < COUNT(widths); w++) {
        for (unsigned pick = 0; pick < COUNT(dram_mixed); pick++) {
            struct ipag_input in = {.ipa_bits = widths[w], .parange = 5, .policy = IPAG_POLICY_CARVE_LOW,
                                    .align = IPAG_MIN_CARVE_ALIGN, .dram = &dram_mixed[pick], .dram_count = 1,
                                    .mmio = mmio_mixed, .mmio_count = COUNT(mmio_mixed)};
            struct ipag_plan plan;
            int why = plan_for(&in, &plan);
            if (why != IPAG_OK) continue;   /* a width too narrow for this window */
            check_plan_against_builder(&in, &plan, "mixed");
        }
    }

    /* One page of MMIO and nothing else needs a table per level below the root. */
    struct ipag_window page = {0x17000000ULL, 0x1000};
    struct ipag_window dram = {0x80000000ULL, 0x80000000ULL};
    struct ipag_input in = {.ipa_bits = 36, .parange = 4, .dram = &dram, .dram_count = 1, .mmio = &page, .mmio_count = 1};
    struct ipag_plan plan;
    check(plan_for(&in, &plan) == IPAG_OK, "a small map plans");
    check(plan.leaves_4k == 1 && plan.leaves_1g == 2 && plan.leaves_2m == 0 && plan.table_pages == 2,
          "one MMIO page costs an L2 table and an L3 table; 2 GiB of DRAM is two 1 GiB blocks");
    check(plan.start_level == 1 && plan.root_entries == 64, "36 bits start at level 1 with 64 root entries");
}

/* ---- the planner against an independent oracle ----------------------------- */

enum oracle_class { C_PARAM, C_STRUCT, C_LIMIT, C_NOFIT, C_ALLOW };

static unsigned __int128 wide(uint64_t v)
{
    return v;
}

static int oracle_window_set(const struct ipag_window *w, unsigned n, unsigned __int128 pa_limit)
{
    for (unsigned i = 0; i < n; i++) {
        unsigned __int128 end = wide(w[i].base) + w[i].size;
        if (!w[i].size || end > UINT64_MAX || w[i].base % 4096 || w[i].size % 4096 || end > pa_limit) return 0;
        if (i && (w[i].base < w[i - 1].base || wide(w[i].base) < wide(w[i - 1].base) + w[i - 1].size)) return 0;
    }
    return 1;
}

static int oracle_class(const struct ipag_input *in, uint64_t *carve_base, uint64_t *carve_size)
{
    unsigned pa_bits = ipag_pa_bits(in->parange);
    uint64_t align = in->align ? in->align : 32 * MIB;
    int align_ok = align >= 2 * MIB && align <= GIB && (align & (align - 1)) == 0 && in->want_bytes % align == 0;
    if (in->ipa_bits < 32 || in->ipa_bits > 48 || !pa_bits || in->policy > 1 || !in->dram_count ||
        in->dram_count > 64 || in->mmio_count > 64 || !in->dram || (in->mmio_count && !in->mmio) || !align_ok)
        return C_PARAM;

    unsigned __int128 pa_limit = (unsigned __int128)1 << pa_bits, limit = (unsigned __int128)1 << in->ipa_bits;
    if (!oracle_window_set(in->dram, in->dram_count, pa_limit) || !oracle_window_set(in->mmio, in->mmio_count, pa_limit))
        return C_STRUCT;
    for (unsigned m = 0; m < in->mmio_count; m++)
        for (unsigned d = 0; d < in->dram_count; d++)
            if (wide(in->mmio[m].base) < wide(in->dram[d].base) + in->dram[d].size &&
                wide(in->dram[d].base) < wide(in->mmio[m].base) + in->mmio[m].size)
                return C_STRUCT;
    for (unsigned m = 0; m < in->mmio_count; m++)
        if (wide(in->mmio[m].base) + in->mmio[m].size > limit) return C_LIMIT;
    if (in->policy == IPAG_POLICY_REFUSE)
        for (unsigned d = 0; d < in->dram_count; d++)
            if (wide(in->dram[d].base) + in->dram[d].size > limit) return C_LIMIT;

    /* Whole `align` units inside each window's part below the limit. */
    uint64_t best_units = 0, best_base = 0, found_base = 0;
    int found = 0;
    for (unsigned d = 0; d < in->dram_count; d++) {
        unsigned __int128 end = wide(in->dram[d].base) + in->dram[d].size;
        if (end > limit) end = limit;
        if (in->dram[d].base >= end) continue;
        uint64_t first = (in->dram[d].base + align - 1) / align, last = (uint64_t)(end / align);
        uint64_t units = last > first ? last - first : 0;
        if (!units) continue;
        if (in->want_bytes) {
            if (!found && units * align >= in->want_bytes) {
                found = 1;
                found_base = first * align;
            }
        } else if (units > best_units) {
            best_units = units;
            best_base = first * align;
        }
    }
    if (in->want_bytes) {
        if (!found) return C_NOFIT;
        *carve_base = found_base;
        *carve_size = in->want_bytes;
    } else {
        if (!best_units) return C_NOFIT;
        *carve_base = best_base;
        *carve_size = best_units * align;
    }
    return C_ALLOW;
}

static int reason_class(int reason)
{
    switch (reason) {
    case IPAG_OK: return C_ALLOW;
    case IPAG_NULL_INPUT: case IPAG_BAD_IPA_BITS: case IPAG_BAD_PARANGE: case IPAG_BAD_POLICY:
    case IPAG_NO_WINDOWS: case IPAG_TOO_MANY_WINDOWS: case IPAG_CARVE_UNALIGNED:
        return C_PARAM;
    case IPAG_ZERO_SIZE: case IPAG_OVERFLOW: case IPAG_UNALIGNED: case IPAG_UNSORTED: case IPAG_OVERLAP:
    case IPAG_MMIO_IN_DRAM: case IPAG_ABOVE_PARANGE:
        return C_STRUCT;
    case IPAG_WINDOW_ABOVE_LIMIT: case IPAG_WINDOW_CROSSES_LIMIT: return C_LIMIT;
    case IPAG_CARVE_NO_FIT: return C_NOFIT;
    default: return -1;
    }
}

static uint64_t round_to(uint64_t v, uint64_t unit)
{
    return v - v % unit;
}

static void make_windows(struct ipag_window *w, unsigned *count, uint64_t cursor, unsigned min, unsigned max,
                         int fault_ok)
{
    unsigned n = min + (unsigned)rnd_below(max - min + 1);
    for (unsigned i = 0; i < n; i++) {
        uint64_t size;
        switch (rnd_below(4)) {
        case 0: size = 0x1000 * (1 + rnd_below(256)); break;
        case 1: size = 32 * MIB * (1 + rnd_below(64)); break;
        case 2: size = GIB * (1 + rnd_below(20)); break;
        default: size = 2 * MIB * (1 + rnd_below(512)); break;
        }
        w[i].base = cursor;
        w[i].size = size;
        cursor += size + 0x1000 * rnd_below(1 << 18) * (rnd_below(3) == 0);
        if (fault_ok && rnd_below(16) == 0) {
            switch (rnd_below(6)) {
            case 0: w[i].size = 0; break;
            case 1: w[i].base |= 0x10; break;
            case 2: w[i].size = 0 - w[i].base; break;   /* ends on 2^64: aligned, so only the overflow check can refuse it */
            case 3: if (i) w[i].base = w[i - 1].base - 0x1000; break;
            case 4: if (i) w[i].base = w[i - 1].base + 0x1000; break;
            default: w[i].base += 1ULL << 47; break;
            }
        }
    }
    *count = n;
}

static void test_planner_against_oracle(void)
{
    static const unsigned widths[] = {31, 32, 36, 36, 36, 36, 36, 37, 39, 40, 48, 49};
    static const unsigned pa_pool[] = {4, 4, 4, 4, 5, 5, 1, 2, 3, 0, 6, 7};
    static const uint64_t aligns[] = {0, 0, 0, 0, 0, 0, 2 * MIB, 32 * MIB, 64 * MIB, GIB, 0x1000, 3 * MIB, 2 * GIB};
    static const uint64_t starts[] = {0x80000000ULL, 0x100000000ULL, 0x880000000ULL, 0x1000};
    unsigned allowed = 0, by_class[5] = {0, 0, 0, 0, 0}, built_checked = 0, trimmed_allowed = 0;

    for (unsigned round = 0; round < 60000; round++) {
        struct ipag_window dram[IPAG_MAX_WINDOWS], mmio[IPAG_MAX_WINDOWS];
        struct ipag_input in;
        memset(&in, 0, sizeof(in));
        in.ipa_bits = widths[rnd_below(COUNT(widths))];
        in.parange = pa_pool[rnd_below(COUNT(pa_pool))];
        in.policy = (unsigned)(rnd_below(40) == 0 ? 2 : rnd_below(5) < 3 ? 0 : 1);
        in.align = aligns[rnd_below(COUNT(aligns))];
        uint64_t unit = in.align ? in.align : 32 * MIB;
        in.want_bytes = rnd_below(2) ? 0 : unit * rnd_below(rnd_below(2) ? 8 : 700);
        if (rnd_below(60) == 0) in.want_bytes += 0x1000;
        make_windows(dram, &in.dram_count, starts[rnd_below(COUNT(starts))] + round_to(rnd_below(8 * GIB), 0x1000), 1, 4, 1);
        make_windows(mmio, &in.mmio_count, 0x1000000ULL + 0x1000 * rnd_below(1 << 14), 0, 3, 1);
        if (rnd_below(4) == 0 && in.ipa_bits <= 40) {
            /* A low window and a high one that ends at, or runs past, the limit. */
            uint64_t limit = 1ULL << in.ipa_bits, below = GIB * (1 + rnd_below(6)), above = GIB * rnd_below(4);
            uint64_t low = GIB * (1 + rnd_below(2));
            if (limit > 0x80000000ULL + 2 * GIB + below) {
                dram[0] = (struct ipag_window){0x80000000ULL, low};
                dram[1] = (struct ipag_window){limit - below, below + above};
                in.dram_count = 2;
            }
        }
        if (rnd_below(40) == 0) in.dram_count = 0;
        if (rnd_below(24) == 0 && in.mmio_count && in.dram_count) mmio[0].base = dram[0].base + 0x1000 * rnd_below(4);
        if (rnd_below(24) == 0 && in.mmio_count) mmio[in.mmio_count - 1].base = (1ULL << in.ipa_bits) - 0x1000 * rnd_below(3);
        in.dram = in.dram_count ? dram : NULL;
        in.mmio = in.mmio_count ? mmio : NULL;

        struct ipag_plan plan;
        int why = ipag_plan_stage2(&in, &plan);
        uint64_t want_base = 0, want_size = 0;
        int expect = oracle_class(&in, &want_base, &want_size);
        by_class[expect]++;
        if (reason_class(why) != expect) {
            check(0, "the planner's outcome class matches the oracle");
            printf("  round %u: planner %s, oracle class %d (bits %u parange %u policy %u align %#" PRIx64
                   " want %#" PRIx64 ", %u dram, %u mmio)\n",
                   round, ipag_reason_name(why), expect, in.ipa_bits, in.parange, in.policy, in.align,
                   in.want_bytes, in.dram_count, in.mmio_count);
            return;
        }
        if (why != IPAG_OK) {
            if (why == IPAG_WINDOW_ABOVE_LIMIT || why == IPAG_WINDOW_CROSSES_LIMIT)
                check(plan.excess != 0 && plan.bad_index != IPAG_NO_INDEX, "a limit refusal names the window and the excess");
            check(plan.carve_size == 0 || why == IPAG_CARVE_NO_FIT, "a refusal leaves no carve-out");
            continue;
        }
        allowed++;
        uint64_t align = in.align ? in.align : 32 * MIB;
        uint64_t limit = 1ULL << in.ipa_bits;
        check(plan.carve_base == want_base && plan.carve_size == want_size, "the carve-out is where the oracle puts it");
        check(plan.carve_size && plan.carve_size % align == 0 && plan.carve_base % align == 0 &&
                  plan.carve_base + plan.carve_size <= limit && plan.carve_base + plan.carve_size <= plan.pa_limit,
              "the carve-out is aligned, non-empty and inside both address spaces");
        int inside = 0;
        for (unsigned d = 0; d < in.dram_count; d++)
            if (plan.carve_base >= dram[d].base && plan.carve_base + plan.carve_size <= dram[d].base + dram[d].size) inside++;
        check(inside == 1, "the carve-out lies inside exactly one DRAM window");
        uint64_t total = 0, usable = 0;
        unsigned cut = 0;
        for (unsigned d = 0; d < in.dram_count; d++) {
            unsigned __int128 end = wide(dram[d].base) + dram[d].size;
            total += dram[d].size;
            if (end > limit) cut++;
            if (end > limit) end = limit;
            if (end > dram[d].base) usable += (uint64_t)(end - dram[d].base);
        }
        check(plan.dram_total == total && plan.dram_usable == usable && plan.dram_usable + plan.dram_dropped == total &&
                  plan.trimmed_windows == cut,
              "DRAM accounting equals the oracle's");
        if (cut) trimmed_allowed++;
        if (in.policy == IPAG_POLICY_REFUSE)
            check(plan.dram_dropped == 0 && plan.trimmed_windows == 0, "REFUSE never drops DRAM");
        else
            check((plan.dram_dropped != 0) == (plan.trimmed_windows != 0), "CARVE_LOW reports exactly the DRAM it gave up");
        if (built_checked < 4000 && plan.carve_size <= 24 * GIB) {
            built_checked++;
            check_plan_against_builder(&in, &plan, "random");
        }
    }
    check(allowed > 3000 && by_class[C_PARAM] > 1000 && by_class[C_STRUCT] > 1000 && by_class[C_LIMIT] > 1000 &&
              by_class[C_NOFIT] > 100 && trimmed_allowed > 200,
          "the random inputs reach every outcome class, including CARVE_LOW plans that cut DRAM");
    printf("  planner/oracle: %u allowed (%u cut DRAM), %u param, %u structure, %u limit, %u no-fit; %u plans rebuilt\n",
           allowed, trimmed_allowed, by_class[C_PARAM], by_class[C_STRUCT], by_class[C_LIMIT], by_class[C_NOFIT],
           built_checked);
}

/* ---- structural refusals, by exact reason ---------------------------------- */

/* The oracle compares outcome classes, and several checks refuse the same bad list (a descending bank also
 * overlaps, a wrapping window also fails alignment), so a break in one of them can hide behind the other. These
 * pin the reason and the window it names. */
static void expect_plan(const struct ipag_window *dram, unsigned dram_count, const struct ipag_window *mmio,
                        unsigned mmio_count, unsigned parange, int reason, unsigned index, unsigned is_mmio,
                        const char *label)
{
    struct ipag_input in = {.ipa_bits = 36, .parange = parange, .dram = dram, .dram_count = dram_count,
                            .mmio = mmio, .mmio_count = mmio_count};
    struct ipag_plan plan;
    int why = ipag_plan_stage2(&in, &plan);
    check(why == reason, label);
    if (why != reason) {
        printf("  %s: got %s, expected %s\n", label, ipag_reason_name(why), ipag_reason_name(reason));
        return;
    }
    if (reason != IPAG_OK) check(plan.bad_index == index && plan.bad_is_mmio == is_mmio, label);
}

static void test_planner_exact_reasons(void)
{
    const struct ipag_window low = {0x80000000ULL, 2 * GIB};
    const struct ipag_window touching[] = {low, {0x100000000ULL, GIB}};
    const struct ipag_window overlapping[] = {low, {0xc0000000ULL, GIB}};
    const struct ipag_window descending_apart[] = {{0x880000000ULL, GIB}, low};
    const struct ipag_window descending_touching[] = {{0x100000000ULL, GIB}, low};
    const struct ipag_window empty[] = {{0x80000000ULL, 0}};
    const struct ipag_window wraps[] = {{0xfffffffffffff000ULL, 0x2000}};
    const struct ipag_window ends_on_2_64[] = {{0x1000, 0 - 0x1000ULL}};
    const struct ipag_window unaligned_base[] = {{0x80000800ULL, 2 * GIB}};
    const struct ipag_window unaligned_size[] = {{0x80000000ULL, 0x1800}};
    const struct ipag_window ends_on_pa_limit[] = {low, {(1ULL << 44) - 0x1000, 0x1000}};
    const struct ipag_window past_pa_limit[] = {low, {(1ULL << 44) - 0x1000, 0x2000}};
    const struct ipag_window gic_in_dram[] = {{0x90000000ULL, 0x1000}};
    const struct ipag_window mmio_descending[] = {{0x70000000ULL, 0x1000}, {0x17000000ULL, 0x1000}};
    const struct ipag_window mmio_overlapping[] = {{0x17000000ULL, 0x2000}, {0x17001000ULL, 0x1000}};
    const struct ipag_window mmio_touching[] = {{0x17000000ULL, 0x1000}, {0x17001000ULL, 0x1000}};

    expect_plan(touching, 2, NULL, 0, 4, IPAG_OK, 0, 0, "adjacent banks are fine");
    expect_plan(overlapping, 2, NULL, 0, 4, IPAG_OVERLAP, 1, 0, "overlapping banks name the second");
    expect_plan(descending_apart, 2, NULL, 0, 4, IPAG_UNSORTED, 1, 0, "descending banks are unsorted, not overlapping");
    expect_plan(descending_touching, 2, NULL, 0, 4, IPAG_UNSORTED, 1, 0, "descending adjacent banks are unsorted");
    expect_plan(empty, 1, NULL, 0, 4, IPAG_ZERO_SIZE, 0, 0, "an empty bank");
    expect_plan(wraps, 1, NULL, 0, 4, IPAG_OVERFLOW, 0, 0, "a bank whose end wraps past 2^64");
    expect_plan(ends_on_2_64, 1, NULL, 0, 4, IPAG_OVERFLOW, 0, 0, "an aligned bank ending exactly on 2^64");
    expect_plan(unaligned_base, 1, NULL, 0, 4, IPAG_UNALIGNED, 0, 0, "a bank base off the 4 KiB grid");
    expect_plan(unaligned_size, 1, NULL, 0, 4, IPAG_UNALIGNED, 0, 0, "a bank size off the 4 KiB grid");
    expect_plan(ends_on_pa_limit, 2, NULL, 0, 4, IPAG_WINDOW_ABOVE_LIMIT, 1, 0,
                "a bank ending exactly on the PARange limit is only past the IPA limit");
    expect_plan(past_pa_limit, 2, NULL, 0, 4, IPAG_ABOVE_PARANGE, 1, 0, "a bank ending past the PARange limit");
    expect_plan(&low, 1, gic_in_dram, 1, 4, IPAG_MMIO_IN_DRAM, 0, 1, "MMIO inside DRAM names the MMIO window");
    expect_plan(&low, 1, mmio_descending, 2, 4, IPAG_UNSORTED, 1, 1, "descending MMIO windows");
    expect_plan(&low, 1, mmio_overlapping, 2, 4, IPAG_OVERLAP, 1, 1, "overlapping MMIO windows");
    expect_plan(&low, 1, mmio_touching, 2, 4, IPAG_OK, 0, 0, "adjacent MMIO windows are fine");
}

/* ---- the plan CLI ---------------------------------------------------------- */

static int parse_u64(const char *text, uint64_t *out)
{
    char *end;
    if (!*text || *text == '-' || *text == '+' || *text == ' ') return -1;
    errno = 0;
    unsigned long long v = strtoull(text, &end, 0);
    if (errno || *end) return -1;
    *out = v;
    return 0;
}

static int parse_window(const char *text, struct ipag_window *w)
{
    char copy[96];
    char *colon;
    if (strlen(text) >= sizeof(copy)) return -1;
    strcpy(copy, text);
    colon = strchr(copy, ':');
    if (!colon) return -1;
    *colon = 0;
    return parse_u64(copy, &w->base) || parse_u64(colon + 1, &w->size) ? -1 : 0;
}

static int run_plan(int argc, char **argv)
{
    struct ipag_window dram[IPAG_MAX_WINDOWS + 1], mmio[IPAG_MAX_WINDOWS + 1];
    struct ipag_input in;
    memset(&in, 0, sizeof(in));
    in.ipa_bits = IPAG_DEFAULT_IPA_BITS;
    in.parange = 4;
    for (int i = 2; i < argc; i++) {
        const char *flag = argv[i], *value = i + 1 < argc ? argv[i + 1] : NULL;
        uint64_t n;
        if (!value) goto usage;
        i++;
        if (!strcmp(flag, "--ipa-bits") && !parse_u64(value, &n) && n <= 0xffffffffu) in.ipa_bits = (unsigned)n;
        else if (!strcmp(flag, "--parange") && !parse_u64(value, &n) && n <= 0xffffffffu) in.parange = (unsigned)n;
        else if (!strcmp(flag, "--policy") && !strcmp(value, "refuse")) in.policy = IPAG_POLICY_REFUSE;
        else if (!strcmp(flag, "--policy") && !strcmp(value, "carve-low")) in.policy = IPAG_POLICY_CARVE_LOW;
        else if (!strcmp(flag, "--policy") && !strcmp(value, "invalid")) in.policy = 99;
        else if (!strcmp(flag, "--want") && !parse_u64(value, &n)) in.want_bytes = n;
        else if (!strcmp(flag, "--align") && !parse_u64(value, &n)) in.align = n;
        else if (!strcmp(flag, "--dram") && in.dram_count <= IPAG_MAX_WINDOWS && !parse_window(value, &dram[in.dram_count])) in.dram_count++;
        else if (!strcmp(flag, "--mmio") && in.mmio_count <= IPAG_MAX_WINDOWS && !parse_window(value, &mmio[in.mmio_count])) in.mmio_count++;
        else goto usage;
    }
    in.dram = in.dram_count ? dram : NULL;
    in.mmio = in.mmio_count ? mmio : NULL;

    struct ipag_plan p;
    int why = ipag_plan_stage2(&in, &p);
    printf("result=%s\nreason=%s\ncode=%d\n", why ? "REFUSE" : "ALLOW", ipag_reason_name(why), why);
    printf("bad_index=%d\nbad_is_mmio=%u\nexcess=%#" PRIx64 "\n", p.bad_index == IPAG_NO_INDEX ? -1 : (int)p.bad_index,
           p.bad_is_mmio, p.excess);
    printf("ipa_bits=%u\npa_bits=%u\nipa_limit=%#" PRIx64 "\npa_limit=%#" PRIx64 "\n", p.ipa_bits, p.pa_bits, p.ipa_limit,
           p.pa_limit);
    printf("carve_base=%#" PRIx64 "\ncarve_size=%#" PRIx64 "\n", p.carve_base, p.carve_size);
    printf("dram_total=%#" PRIx64 "\ndram_usable=%#" PRIx64 "\ndram_dropped=%#" PRIx64 "\ntrimmed_windows=%u\n", p.dram_total,
           p.dram_usable, p.dram_dropped, p.trimmed_windows);
    printf("start_level=%u\nroot_entries=%u\nleaves_1g=%" PRIu64 "\nleaves_2m=%" PRIu64 "\nleaves_4k=%" PRIu64
           "\ntable_pages=%" PRIu64 "\n",
           p.start_level, p.root_entries, p.leaves_1g, p.leaves_2m, p.leaves_4k, p.table_pages);
    return 0;
usage:
    fprintf(stderr, "usage: %s --plan [--ipa-bits N] [--parange N] [--policy refuse|carve-low] [--want BYTES]\n"
                    "       [--align BYTES] [--dram BASE:SIZE]... [--mmio BASE:SIZE]...\n", argv[0]);
    return 2;
}

static int run_defaults(void)
{
    printf("default_ipa_bits=%u\ndefault_carve_align=%#lx\nmax_windows=%u\nhook_return_base=%d\nreason_count=%d\n",
           IPAG_DEFAULT_IPA_BITS, IPAG_DEFAULT_CARVE_ALIGN, IPAG_MAX_WINDOWS, IPAG_HOOK_RETURN_BASE, IPAG_REASON_COUNT);
    for (int r = 0; r < IPAG_REASON_COUNT; r++) printf("reason.%d=%s\n", r, ipag_reason_name(r));
    return 0;
}

int main(int argc, char **argv)
{
    if (argc > 1 && !strcmp(argv[1], "--plan")) return run_plan(argc, argv);
    if (argc > 1 && !strcmp(argv[1], "--defaults")) return run_defaults();
    if (argc > 1) {
        fprintf(stderr, "usage: %s [--plan ... | --defaults]\n", argv[0]);
        return 2;
    }
    test_parameters();
    test_check_spans();
    test_against_stage2_build();
    test_mapping_plan();
    test_planner_against_oracle();
    test_planner_exact_reasons();
    printf("stage2-geometry native: %d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
