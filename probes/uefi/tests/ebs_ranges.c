/* SPDX-License-Identifier: MIT */
#include "ebs_ranges.h"

const char *tests_ebs_status_name(int status)
{
    switch (status) {
    case TESTS_EBS_OK:
        return "OK";
    case TESTS_EBS_SIZE_ZERO:
        return "RANGE SIZE ZERO";
    case TESTS_EBS_UNALIGNED:
        return "RANGE BASE UNALIGNED";
    case TESTS_EBS_WRAPS:
        return "RANGE WRAPS";
    case TESTS_EBS_NO_MAP:
        return "MAP EMPTY";
    case TESTS_EBS_MAP_TOO_LARGE:
        return "MAP OVER 512 DESCRIPTORS";
    case TESTS_EBS_MAP_INVALID:
        return "MAP BAD DESCRIPTOR";
    case TESTS_EBS_NOT_IN_ONE_DESCRIPTOR:
        return "RANGE NOT IN ONE DESCRIPTOR";
    case TESTS_EBS_WRONG_TYPE:
        return "DESCRIPTOR TYPE NOT SWEPT";
    case TESTS_EBS_NOT_WRITE_BACK:
        return "DESCRIPTOR NOT WRITE-BACK";
    default:
        return "UNKNOWN";
    }
}

/* FNV-1a over 64 bits: the algorithm and constants of sp11_ebs_ranges_fnv in the shared header. */
uint64_t tests_ebs_fnv1a(const void *data, size_t n)
{
    const uint8_t *p = data;
    uint64_t h = UINT64_C(0xcbf29ce484222325);

    while (n--)
        h = (h ^ *p++) * UINT64_C(0x100000001b3);
    return h;
}

int tests_ebs_check_range(const probe_memmap *map, uint64_t base, uint64_t size)
{
    uint64_t end;
    size_t holders = 0;
    uint32_t held_type = 0;
    uint64_t held_attr = 0;

    if (size == 0)
        return TESTS_EBS_SIZE_ZERO;
    if ((base & (TESTS_EBS_PAGE_BYTES - 1u)) != 0)
        return TESTS_EBS_UNALIGNED;
    /* The fork adds base and size in 64 bits without looking for a carry, so a wrapping range
     * could look contained there. It is refused here instead. */
    if (size > UINT64_MAX - base)
        return TESTS_EBS_WRAPS;
    end = base + size;
    if (map == NULL || map->base == NULL || map->count == 0)
        return TESTS_EBS_NO_MAP;
    if (map->count > TESTS_EBS_MAX_DESCRIPTORS)
        return TESTS_EBS_MAP_TOO_LARGE;
    if (map->desc_size < PROBE_MEMMAP_MIN_DESC_SIZE || map->desc_size > PROBE_MEMMAP_MAX_DESC_SIZE ||
        (map->desc_size & 7u) != 0)
        return TESTS_EBS_MAP_INVALID;

    /* Every descriptor is counted, not just the first that contains the range: the fork refuses a
     * range that more than one (overlapping) descriptor contains, so the count matters and not
     * only whether some descriptor does. */
    for (size_t i = 0; i < map->count; i++) {
        uint32_t type;
        uint64_t phys, pages, attr, bytes;

        if (probe_memmap_get(map, i, &type, &phys, NULL, &pages, &attr) != 0)
            return TESTS_EBS_MAP_INVALID;
        if (pages == 0 || pages > UINT64_MAX / TESTS_EBS_PAGE_BYTES)
            return TESTS_EBS_MAP_INVALID;
        bytes = pages * TESTS_EBS_PAGE_BYTES;
        if (bytes > UINT64_MAX - phys)
            return TESTS_EBS_MAP_INVALID;
        if (phys <= base && end <= phys + bytes) {
            holders++;
            held_type = type;
            held_attr = attr;
        }
    }
    if (holders != 1)
        return TESTS_EBS_NOT_IN_ONE_DESCRIPTOR;
    if (held_type >= 32 || ((TESTS_EBS_TYPES_MASK >> held_type) & 1u) == 0)
        return TESTS_EBS_WRONG_TYPE;
    if ((held_attr & TESTS_EBS_ATTR_WB) == 0)
        return TESTS_EBS_NOT_WRITE_BACK;
    return TESTS_EBS_OK;
}

int tests_ebs_build(tests_ebs_table *t, const probe_memmap *map, uint64_t base, uint64_t size)
{
    int status;

    probe_memset(t, 0, sizeof *t);
    status = tests_ebs_check_range(map, base, size);
    if (status != TESTS_EBS_OK)
        return status;
    t->magic = TESTS_EBS_MAGIC;
    t->version = TESTS_EBS_VERSION;
    t->count = 1;
    t->chunk_cap = TESTS_EBS_CHUNK_CAP;
    t->reserved = 0;
    t->range[0].base = base;
    t->range[0].size = size;
    t->checksum = tests_ebs_fnv1a(t, offsetof(tests_ebs_table, checksum));
    return TESTS_EBS_OK;
}
