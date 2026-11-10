/* SPDX-License-Identifier: MIT */
/* See ipa-geometry.h. */
#include "ipa-geometry.h"

#define PAGE_MASK (IPAG_PAGE_SIZE - 1)

static uint64_t bit(unsigned n)
{
    return (uint64_t)1 << n;
}

unsigned ipag_pa_bits(unsigned parange)
{
    switch (parange) {
    case 0: return 32;
    case 1: return 36;
    case 2: return 40;
    case 3: return 42;
    case 4: return 44;
    case 5:
    case 6: return 48;
    default: return 0;
    }
}

/* One span against the width limits, tightest physical fact first: a span that
 * wraps or is empty says nothing about widths, and a span beyond the PARange
 * could not be an identity mapping whatever the IPA width is. The limit test is
 * "hi > limit" because hi is exclusive. */
static int span_fit(uint64_t lo, uint64_t hi, uint64_t ipa_limit, uint64_t pa_limit)
{
    if (hi < lo) return IPAG_INVERTED;
    if (hi == lo) return IPAG_ZERO_SIZE;
    if ((lo | hi) & PAGE_MASK) return IPAG_UNALIGNED;
    if (hi > pa_limit) return IPAG_ABOVE_PARANGE;
    if (lo >= ipa_limit) return IPAG_WINDOW_ABOVE_LIMIT;
    if (hi > ipa_limit) return IPAG_WINDOW_CROSSES_LIMIT;
    return IPAG_OK;
}

int ipag_check_spans(const struct ipag_span *spans, unsigned count,
                     unsigned ipa_bits, unsigned parange, unsigned *bad_index)
{
    unsigned ignored;
    if (!bad_index) bad_index = &ignored;
    *bad_index = IPAG_NO_INDEX;
    if (ipa_bits < IPAG_MIN_IPA_BITS || ipa_bits > IPAG_MAX_IPA_BITS) return IPAG_BAD_IPA_BITS;
    unsigned pa_bits = ipag_pa_bits(parange);
    if (!pa_bits) return IPAG_BAD_PARANGE;
    if (!count) return IPAG_NO_WINDOWS;
    if (!spans) return IPAG_NULL_INPUT;
    if (count > IPAG_MAX_WINDOWS) return IPAG_TOO_MANY_WINDOWS;

    uint64_t ipa_limit = bit(ipa_bits), pa_limit = bit(pa_bits);
    for (unsigned n = 0; n < count; n++) {
        int why = span_fit(spans[n].lo, spans[n].hi, ipa_limit, pa_limit);
        /* The same disjointness stage2_build demands: shadow_map overwrites a
         * leaf it lands on, so an overlap is two maps of one address. */
        for (unsigned m = 0; !why && m < n; m++)
            if (spans[n].lo < spans[m].hi && spans[m].lo < spans[n].hi) why = IPAG_OVERLAP;
        if (why) {
            *bad_index = n;
            return why;
        }
    }
    return IPAG_OK;
}

#if IPAG_WITH_PLANNER

static void clear_plan(struct ipag_plan *out)
{
    for (unsigned n = 0; n < sizeof(*out); n++) ((uint8_t *)out)[n] = 0;
    out->bad_index = IPAG_NO_INDEX;
}

/* levels = ceil((ipa_bits - 12) / 9) tables are walked, so the root is level
 * 4 - levels. Same arithmetic as shadow/mmu.c mmu_start_level for a 4K
 * granule, kept separate so the native test can hold the two against each
 * other. */
static unsigned walk_levels(unsigned ipa_bits)
{
    return (ipa_bits - 12 + 8) / 9;
}

int ipag_start_level(unsigned ipa_bits)
{
    if (ipa_bits < IPAG_MIN_IPA_BITS || ipa_bits > IPAG_MAX_IPA_BITS) return -1;
    return (int)(4 - walk_levels(ipa_bits));
}

/* Structure of one window set, in order, and each window's place after its
 * predecessor: strictly ascending and disjoint. Touching windows are fine
 * (adjacent DRAM banks). A descending pair is UNSORTED even when disjoint;
 * the caller's order is never repaired because a firmware list that is out of
 * order is a list this model has not understood. */
static int check_windows(const struct ipag_window *w, unsigned count, uint64_t pa_limit,
                         unsigned is_mmio, struct ipag_plan *out)
{
    for (unsigned n = 0; n < count; n++) {
        uint64_t base = w[n].base, size = w[n].size;
        int why = IPAG_OK;
        if (!size) {
            why = IPAG_ZERO_SIZE;
        } else if (size > UINT64_MAX - base) {
            why = IPAG_OVERFLOW;
        } else if ((base | size) & PAGE_MASK) {
            why = IPAG_UNALIGNED;
        } else if (base + size > pa_limit) {
            why = IPAG_ABOVE_PARANGE;
        } else if (n) {
            /* The predecessor passed these checks, so its end cannot wrap. */
            if (base < w[n - 1].base) why = IPAG_UNSORTED;
            else if (base < w[n - 1].base + w[n - 1].size) why = IPAG_OVERLAP;
        }
        if (why) {
            out->bad_index = n;
            out->bad_is_mmio = is_mmio;
            return why;
        }
    }
    return IPAG_OK;
}

/* A window already known to be well formed against the IPA width. */
static int limit_fit(const struct ipag_window *w, unsigned index, uint64_t limit,
                     unsigned is_mmio, struct ipag_plan *out)
{
    uint64_t end = w->base + w->size;
    if (end <= limit) return IPAG_OK;
    out->bad_index = index;
    out->bad_is_mmio = is_mmio;
    if (w->base >= limit) {
        out->excess = w->size;
        return IPAG_WINDOW_ABOVE_LIMIT;
    }
    out->excess = end - limit;
    return IPAG_WINDOW_CROSSES_LIMIT;
}

/* How many leaves and tables stage2_build produces, without building. Ranges
 * are visited in ascending address order, so the tables a batch of leaves
 * needs at each level are a non-decreasing run of table numbers and a table
 * already counted is exactly the one the previous batch ended in. */
struct tally {
    uint64_t leaves[4];         /* by level; index 0 unused */
    uint64_t tables;            /* every table below the root */
    uint64_t last_table[4];
    unsigned seen[4];
    unsigned start;
};

/* A level-L table maps 512 entries of the level below it: 2 MiB of leaves at
 * level 3, 1 GiB at 2, 512 GiB at 1. */
static unsigned table_shift(unsigned level)
{
    return level == 3 ? 21 : level == 2 ? 30 : 39;
}

static void note_batch(struct tally *t, unsigned leaf_level, uint64_t from,
                       uint64_t to, uint64_t leaves)
{
    t->leaves[leaf_level] += leaves;
    for (unsigned level = t->start + 1; level <= leaf_level; level++) {
        unsigned shift = table_shift(level);
        uint64_t first = from >> shift, last = (to - 1) >> shift;
        uint64_t fresh = last - first + 1;
        if (t->seen[level] && t->last_table[level] == first) fresh--;
        t->tables += fresh;
        t->last_table[level] = last;
        t->seen[level] = 1;
    }
}

/* shadow_map's rule, in runs instead of steps: at each address the largest
 * block that is aligned there and still fits, so a run of one size ends where
 * a larger one becomes possible or the range ends. */
static void tally_range(struct tally *t, uint64_t lo, uint64_t hi)
{
    for (uint64_t at = lo; at < hi;) {
        uint64_t left = hi - at, step, count;
        unsigned level;
        if (!(at & (IPAG_BLOCK_1G - 1)) && left >= IPAG_BLOCK_1G) {
            level = 1;
            step = IPAG_BLOCK_1G;
            count = left / step;
        } else if (!(at & (IPAG_BLOCK_2M - 1)) && left >= IPAG_BLOCK_2M) {
            uint64_t to_1g = (IPAG_BLOCK_1G - (at & (IPAG_BLOCK_1G - 1))) & (IPAG_BLOCK_1G - 1);
            level = 2;
            step = IPAG_BLOCK_2M;
            count = left / step;
            if (to_1g && to_1g / step < count) count = to_1g / step;
        } else {
            uint64_t to_2m = (IPAG_BLOCK_2M - (at & (IPAG_BLOCK_2M - 1))) & (IPAG_BLOCK_2M - 1);
            level = 3;
            step = IPAG_PAGE_SIZE;
            count = left / step;
            if (to_2m && to_2m / step < count) count = to_2m / step;
        }
        note_batch(t, level, at, at + count * step, count);
        at += count * step;
    }
}

/* The mapping plan over the carve-out and the MMIO windows, ascending. */
static void plan_mapping(const struct ipag_input *in, struct ipag_plan *out)
{
    struct ipag_span ranges[IPAG_MAX_WINDOWS + 1];
    unsigned count = 1;
    ranges[0].lo = out->carve_base;
    ranges[0].hi = out->carve_base + out->carve_size;
    for (unsigned n = 0; n < in->mmio_count; n++, count++) {
        ranges[count].lo = in->mmio[n].base;
        ranges[count].hi = in->mmio[n].base + in->mmio[n].size;
    }
    for (unsigned n = 1; n < count; n++) {
        struct ipag_span moving = ranges[n];
        unsigned at = n;
        for (; at && ranges[at - 1].lo > moving.lo; at--) ranges[at] = ranges[at - 1];
        ranges[at] = moving;
    }

    struct tally t;
    for (unsigned n = 0; n < sizeof(t); n++) ((uint8_t *)&t)[n] = 0;
    t.start = (unsigned)ipag_start_level(in->ipa_bits);
    for (unsigned n = 0; n < count; n++) tally_range(&t, ranges[n].lo, ranges[n].hi);

    out->start_level = t.start;
    out->root_entries = 1u << (in->ipa_bits - 12 - 9 * (walk_levels(in->ipa_bits) - 1));
    out->leaves_1g = t.leaves[1];
    out->leaves_2m = t.leaves[2];
    out->leaves_4k = t.leaves[3];
    out->table_pages = t.tables;
}

/* How much of the DRAM the IPA can address. Filled before the limit check
 * decides anything, so a refusal reports what a CARVE_LOW run would give up. */
static void account_dram(const struct ipag_input *in, uint64_t limit, struct ipag_plan *out)
{
    for (unsigned n = 0; n < in->dram_count; n++) {
        uint64_t base = in->dram[n].base, size = in->dram[n].size, end = base + size;
        out->dram_total += size;
        if (base >= limit) {
            out->dram_dropped += size;
            out->trimmed_windows++;
            continue;
        }
        if (end > limit) {
            out->dram_dropped += end - limit;
            out->trimmed_windows++;
            end = limit;
        }
        out->dram_usable += end - base;
    }
}

/* The carve-out among the DRAM that lies below the limit. A window's usable
 * part is [base, min(end, limit)); the carve-out has to start and end on the
 * alignment inside one such part, because XNU takes its guest DRAM as one
 * range and rounds it to that alignment. */
static int place_carve(const struct ipag_input *in, uint64_t align, uint64_t limit,
                       struct ipag_plan *out)
{
    unsigned found = 0;
    for (unsigned n = 0; n < in->dram_count; n++) {
        uint64_t base = in->dram[n].base, end = base + in->dram[n].size;
        if (base >= limit) continue;
        if (end > limit) end = limit;

        uint64_t lo = base + ((align - (base & (align - 1))) & (align - 1));
        uint64_t hi = end - (end & (align - 1));
        if (hi <= lo) continue;
        if (in->want_bytes) {
            /* Windows ascend, so the first that fits is the lowest. */
            if (!found && hi - lo >= in->want_bytes) {
                out->carve_base = lo;
                out->carve_size = in->want_bytes;
                found = 1;
            }
        } else if (hi - lo > out->carve_size) {
            /* Strictly larger: a tie keeps the lower window. */
            out->carve_base = lo;
            out->carve_size = hi - lo;
            found = 1;
        }
    }
    return found ? IPAG_OK : IPAG_CARVE_NO_FIT;
}

int ipag_plan_stage2(const struct ipag_input *in, struct ipag_plan *out)
{
    if (!out) return IPAG_NULL_INPUT;
    clear_plan(out);
    if (!in) return IPAG_NULL_INPUT;
    if (in->ipa_bits < IPAG_MIN_IPA_BITS || in->ipa_bits > IPAG_MAX_IPA_BITS) return IPAG_BAD_IPA_BITS;
    unsigned pa_bits = ipag_pa_bits(in->parange);
    if (!pa_bits) return IPAG_BAD_PARANGE;
    if (in->policy != IPAG_POLICY_REFUSE && in->policy != IPAG_POLICY_CARVE_LOW) return IPAG_BAD_POLICY;
    if (!in->dram_count) return IPAG_NO_WINDOWS;
    if (in->dram_count > IPAG_MAX_WINDOWS || in->mmio_count > IPAG_MAX_WINDOWS) return IPAG_TOO_MANY_WINDOWS;
    if (!in->dram || (in->mmio_count && !in->mmio)) return IPAG_NULL_INPUT;

    uint64_t align = in->align ? in->align : IPAG_DEFAULT_CARVE_ALIGN;
    if (align < IPAG_MIN_CARVE_ALIGN || align > IPAG_MAX_CARVE_ALIGN || (align & (align - 1)) ||
        (in->want_bytes & (align - 1)))
        return IPAG_CARVE_UNALIGNED;

    out->ipa_bits = in->ipa_bits;
    out->pa_bits = pa_bits;
    out->ipa_limit = bit(in->ipa_bits);
    out->pa_limit = bit(pa_bits);

    int why = check_windows(in->dram, in->dram_count, out->pa_limit, 0, out);
    if (!why) why = check_windows(in->mmio, in->mmio_count, out->pa_limit, 1, out);
    if (why) return why;

    for (unsigned m = 0; m < in->mmio_count; m++)
        for (unsigned d = 0; d < in->dram_count; d++)
            if (in->mmio[m].base < in->dram[d].base + in->dram[d].size &&
                in->dram[d].base < in->mmio[m].base + in->mmio[m].size) {
                out->bad_index = m;
                out->bad_is_mmio = 1;
                return IPAG_MMIO_IN_DRAM;
            }

    account_dram(in, out->ipa_limit, out);
    if (in->policy == IPAG_POLICY_REFUSE)
        for (unsigned d = 0; !why && d < in->dram_count; d++)
            why = limit_fit(&in->dram[d], d, out->ipa_limit, 0, out);
    for (unsigned m = 0; !why && m < in->mmio_count; m++)
        why = limit_fit(&in->mmio[m], m, out->ipa_limit, 1, out);
    if (why) return why;

    why = place_carve(in, align, out->ipa_limit, out);
    if (why) return why;

    plan_mapping(in, out);
    return IPAG_OK;
}

const char *ipag_reason_name(int reason)
{
    switch (reason) {
    case IPAG_OK: return "OK";
    case IPAG_NULL_INPUT: return "NULL_INPUT";
    case IPAG_BAD_IPA_BITS: return "BAD_IPA_BITS";
    case IPAG_BAD_PARANGE: return "BAD_PARANGE";
    case IPAG_BAD_POLICY: return "BAD_POLICY";
    case IPAG_NO_WINDOWS: return "NO_WINDOWS";
    case IPAG_TOO_MANY_WINDOWS: return "TOO_MANY_WINDOWS";
    case IPAG_ZERO_SIZE: return "ZERO_SIZE";
    case IPAG_INVERTED: return "INVERTED";
    case IPAG_OVERFLOW: return "OVERFLOW";
    case IPAG_UNALIGNED: return "UNALIGNED";
    case IPAG_UNSORTED: return "UNSORTED";
    case IPAG_OVERLAP: return "OVERLAP";
    case IPAG_MMIO_IN_DRAM: return "MMIO_IN_DRAM";
    case IPAG_ABOVE_PARANGE: return "ABOVE_PARANGE";
    case IPAG_WINDOW_ABOVE_LIMIT: return "WINDOW_ABOVE_LIMIT";
    case IPAG_WINDOW_CROSSES_LIMIT: return "WINDOW_CROSSES_LIMIT";
    case IPAG_CARVE_UNALIGNED: return "CARVE_UNALIGNED";
    case IPAG_CARVE_NO_FIT: return "CARVE_NO_FIT";
    default: return "UNKNOWN";
    }
}

#endif /* IPAG_WITH_PLANNER */
