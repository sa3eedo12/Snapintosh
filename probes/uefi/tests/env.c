/* SPDX-License-Identifier: MIT */
#include "env.h"

void tests_allow_init(tests_allow *a)
{
    a->n = 0;
    a->refused = 0;
    a->unlisted = 0;
}

int tests_allow_add(tests_allow *a, uint64_t base, uint64_t len, const char *why)
{
    if (a->n >= TESTS_ALLOW_MAX || base == 0 || len == 0 || base >= TESTS_PA_LIMIT ||
        len > TESTS_PA_LIMIT - base)
        return -1;
    a->r[a->n].base = base;
    a->r[a->n].len = len;
    a->r[a->n].why = why;
    a->n++;
    return 0;
}

int tests_allow_covers(const tests_allow *a, uint64_t addr, uint64_t len)
{
    if (len == 0 || addr + len < addr)
        return 0;
    for (unsigned i = 0; i < a->n; i++) {
        if (addr >= a->r[i].base && addr - a->r[i].base <= a->r[i].len &&
            len <= a->r[i].len - (addr - a->r[i].base))
            return 1;
    }
    return 0;
}

const char *tests_window_name(int status)
{
    switch (status) {
    case TESTS_WIN_OK:
        return "OK";
    case TESTS_WIN_ZERO:
        return "BASE IS ZERO";
    case TESTS_WIN_MISALIGNED:
        return "MISALIGNED";
    case TESTS_WIN_RANGE:
        return "BAD RANGE";
    case TESTS_WIN_FULL:
        return "ALLOW LIST FULL";
    case TESTS_WIN_NO_MAP:
        return "NO MEMORY MAP";
    case TESTS_WIN_CONFLICT:
        return "MAP SAYS NOT MMIO";
    case TESTS_WIN_NOT_MMIO:
        return "NOT MMIO IN MAP";
    default:
        return "?";
    }
}

int tests_window_check(uint64_t base, uint64_t len, uint64_t align)
{
    if (base == 0)
        return TESTS_WIN_ZERO;
    if (len == 0 || base >= TESTS_PA_LIMIT || len > TESTS_PA_LIMIT - base)
        return TESTS_WIN_RANGE;
    if (align != 0 && (base & (align - 1u)) != 0)
        return TESTS_WIN_MISALIGNED;
    return TESTS_WIN_OK;
}

int tests_window_policy(const probe_memmap *map, int strict_mmio, uint64_t base, uint64_t len)
{
    if (map == NULL || map->count == 0)
        return TESTS_WIN_NO_MAP;
    if (probe_memmap_overlaps_other(map, base, len, TESTS_MMIO_TYPES_MASK))
        return TESTS_WIN_CONFLICT;
    if (strict_mmio && !probe_memmap_covers(map, base, len, TESTS_MMIO_TYPES_MASK))
        return TESTS_WIN_NOT_MMIO;
    return TESTS_WIN_OK;
}

int tests_allow_add_device(tests_allow *a, const probe_memmap *map, int strict_mmio, uint64_t base,
                           uint64_t len, uint64_t align, const char *why)
{
    int st = tests_window_check(base, len, align);

    if (st == TESTS_WIN_OK)
        st = tests_window_policy(map, strict_mmio, base, len);
    if (st == TESTS_WIN_OK && tests_allow_add(a, base, len, why) != 0)
        st = TESTS_WIN_FULL;
    if (st != TESTS_WIN_OK) {
        a->refused++;
        return st;
    }
    if (!probe_memmap_covers(map, base, len, TESTS_MMIO_TYPES_MASK))
        a->unlisted++;
    return TESTS_WIN_OK;
}

int tests_rd32(const tests_bus *bus, const tests_allow *allow, uint64_t addr, uint32_t *v)
{
    *v = 0xFFFFFFFFu;
    if ((addr & 3u) != 0 || !tests_allow_covers(allow, addr, 4))
        return RD_UNJUSTIFIED;
    return bus->rd32(bus->ctx, addr, v) == 0 ? RD_OK : RD_FAULT;
}

int tests_rd64(const tests_bus *bus, const tests_allow *allow, uint64_t addr, uint64_t *v)
{
    uint32_t lo, hi;
    int rc;

    *v = ~0ull;
    /* Both halves are justified before the first one is read: a register that
     * straddles the end of a window is not touched at all. */
    if ((addr & 3u) != 0 || !tests_allow_covers(allow, addr, 8))
        return RD_UNJUSTIFIED;
    rc = tests_rd32(bus, allow, addr, &lo);
    if (rc != RD_OK)
        return rc;
    rc = tests_rd32(bus, allow, addr + 4u, &hi);
    if (rc != RD_OK)
        return rc;
    *v = ((uint64_t)hi << 32) | lo;
    return RD_OK;
}
