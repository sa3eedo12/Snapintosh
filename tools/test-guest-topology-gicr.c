/* SPDX-License-Identifier: MIT */
/* Redistributor discovery groups of the guest topology host test, split from
 * tools/test-guest-topology.c to keep each file small. This file has no main():
 * tools/test-guest-topology.py links it with tools/test-guest-topology.c and
 * compat/topology.c into one binary. The redistributor images are synthetic
 * EXPERIMENTAL stand-ins and say nothing about real SP11 silicon.
 * Every expectation is a hand-derived literal or an independent formula, never a
 * call to the function under test. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "test-guest-topology.h"

/* ---- synthetic redistributor memory ---- */

#define IMAGE_MAX_FRAMES 48u

struct image {
    uint64_t base, stride, fail_address;
    uint32_t frames, reads;
    uint64_t typer[IMAGE_MAX_FRAMES];
};

/* GICR_TYPER[63:32] is Aff3:Aff2:Aff1:Aff0, [23:8] Processor_Number, bit 4 Last,
 * bit 1 VLPIS. Written out from the architecture, not via the model. */
static uint64_t typer_value(uint64_t affinity, uint32_t processor, int last, int vlpis)
{
    return ((affinity >> 32) & 0xff) << 56 | ((affinity >> 16) & 0xff) << 48 |
           ((affinity >> 8) & 0xff) << 40 | (affinity & 0xff) << 32 |
           (uint64_t)processor << 8 | (last ? UINT64_C(0x10) : 0) | (vlpis ? UINT64_C(0x2) : 0);
}

static int image_read(void *ctx, uint64_t address, uint64_t *value)
{
    struct image *img = ctx;
    uint64_t offset, index;

    img->reads++;
    if (img->fail_address && address == img->fail_address)
        return 1;
    if (address < img->base)
        return 1;
    offset = address - img->base;
    index = offset / img->stride;
    if (index >= img->frames)
        return 1;
    /* Registers other than GICR_TYPER read as zero. */
    *value = offset % img->stride == 8 ? img->typer[index] : 0;
    return 0;
}

static void build_image(struct image *img, uint64_t base, uint64_t stride,
                        const uint64_t *affinity, uint32_t n, int v4)
{
    memset(img, 0, sizeof *img);
    img->base = base;
    img->stride = stride;
    img->frames = n;
    for (uint32_t i = 0; i < n; i++)
        img->typer[i] = typer_value(affinity[i], i, i == n - 1, v4);
}

static struct topology_gicr_window make_window(uint64_t base, uint64_t limit, uint64_t stride,
                                               uint32_t version)
{
    struct topology_gicr_window w;
    w.base = base;
    w.limit = limit;
    w.stride = stride;
    w.gic_version = version;
    return w;
}

static int map_is_clear(const struct topology_gicr_map *map)
{
    if (map->count || map->stride)
        return 0;
    for (unsigned i = 0; i < TOPOLOGY_GICR_MAX_FRAMES; i++)
        if (map->frame[i].base || map->frame[i].typer || map->frame[i].affinity)
            return 0;
    return 1;
}

static unsigned index_of(const uint64_t *list, unsigned n, uint64_t value)
{
    for (unsigned i = 0; i < n; i++)
        if (list[i] == value)
            return i;
    return ~0u;
}

/* ---- redistributor discovery ---- */

static void test_gicr_conversions(void)
{
    static const uint64_t aff3[] = {0x0, 0x1, 0xff}, aff2[] = {0x0, 0x1, 0x2, 0xff};
    static const uint64_t aff1[] = {0x0, 0x1, 0x5, 0xff}, aff0[] = {0x0, 0x1, 0x7f, 0xff};
    uint64_t x = UINT64_C(0x9e3779b97f4a7c15);

    begin("gicr-conversion");
    CHECK_EQ(topology_gicr_affinity_value(UINT64_C(0x0100020304)), UINT64_C(0x01020304));
    CHECK_EQ(topology_gicr_typer_affinity(UINT64_C(0x01020304) << 32), UINT64_C(0x0100020304));
    for (unsigned a = 0; a < 3; a++)
        for (unsigned b = 0; b < 4; b++)
            for (unsigned c = 0; c < 4; c++)
                for (unsigned d = 0; d < 4; d++) {
                    uint64_t affinity = aff3[a] << 32 | aff2[b] << 16 | aff1[c] << 8 | aff0[d];
                    uint64_t typer = typer_value(affinity, 0, 0, 0);
                    CHECK_EQ(topology_gicr_typer_affinity(typer), affinity);
                    CHECK_EQ(topology_gicr_affinity_value(affinity), typer >> 32);
                }
    /* probes/secondary.c compares with (a & 0xffffff) | ((a >> 32) << 24). */
    for (unsigned i = 0; i < 256; i++) {
        uint64_t affinity;
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        affinity = x & TOPOLOGY_AFFINITY_MASK;
        CHECK_EQ(topology_gicr_affinity_value(affinity),
                 (affinity & 0xffffff) | ((affinity >> 32) << 24));
        CHECK_EQ(topology_gicr_typer_affinity(topology_gicr_affinity_value(affinity) << 32), affinity);
    }
}

static void test_gicr_constants_and_stride(void)
{
    uint64_t stride = 0;
    struct topology_gicr_window w;

    begin("gicr-constants");
    CHECK_EQ(TOPOLOGY_GICR_TYPER_OFFSET, 0x8);
    CHECK_EQ(TOPOLOGY_GICR_TYPER_LAST, 0x10);
    CHECK_EQ(TOPOLOGY_GICR_TYPER_VLPIS, 0x2);
    CHECK_EQ(TOPOLOGY_GICR_FRAME_ALIGN, 0x10000);
    CHECK_EQ(TOPOLOGY_GICR_STRIDE_V3, 0x20000);
    CHECK_EQ(TOPOLOGY_GICR_STRIDE_V4, 0x40000);
    CHECK_EQ(TOPOLOGY_AFFINITY_MASK, UINT64_C(0xff00ffffff));
    CHECK_EQ(TOPOLOGY_CLUSTER_MASK, UINT64_C(0xff00ff0000));

    begin("gicr-stride");
    w = make_window(0x17080000, 0x17380000, 0, 3);
    CHECK_EQ(topology_gicr_stride(&w, &stride), TOPOLOGY_GICR_OK);
    CHECK_EQ(stride, 0x20000);
    w.gic_version = 4;
    CHECK_EQ(topology_gicr_stride(&w, &stride), TOPOLOGY_GICR_OK);
    CHECK_EQ(stride, 0x40000);
    /* A devicetree may declare a v3 binding with a v4-sized stride. */
    w.gic_version = 3;
    w.stride = 0x40000;
    CHECK_EQ(topology_gicr_stride(&w, &stride), TOPOLOGY_GICR_OK);
    CHECK_EQ(stride, 0x40000);
    w.gic_version = 4;
    w.stride = 0x80000;
    CHECK_EQ(topology_gicr_stride(&w, &stride), TOPOLOGY_GICR_OK);
    CHECK_EQ(stride, 0x80000);
    stride = 0;
    w.stride = 0x20000;
    CHECK_EQ(topology_gicr_stride(&w, &stride), TOPOLOGY_GICR_BAD_STRIDE);
    CHECK_EQ(stride, 0);
    w.stride = 0x30000;
    CHECK_EQ(topology_gicr_stride(&w, &stride), TOPOLOGY_GICR_BAD_STRIDE);
    w.stride = 0x48000;
    CHECK_EQ(topology_gicr_stride(&w, &stride), TOPOLOGY_GICR_BAD_STRIDE);
    w.gic_version = 3;
    w.stride = 0x10000;
    CHECK_EQ(topology_gicr_stride(&w, &stride), TOPOLOGY_GICR_BAD_STRIDE);
    w.stride = 0x28000;
    CHECK_EQ(topology_gicr_stride(&w, &stride), TOPOLOGY_GICR_BAD_STRIDE);
    w.stride = 0;
    for (uint32_t version = 0; version < 8; version++) {
        w.gic_version = version;
        if (version != 3 && version != 4)
            CHECK_EQ(topology_gicr_stride(&w, &stride), TOPOLOGY_GICR_BAD_VERSION);
    }
    CHECK_EQ(topology_gicr_stride(NULL, &stride), TOPOLOGY_GICR_BAD_ARGUMENT);
    w.gic_version = 4;
    CHECK_EQ(topology_gicr_stride(&w, NULL), TOPOLOGY_GICR_BAD_ARGUMENT);
}

static void test_gicr_discovery(void)
{
    struct image img;
    struct topology_gicr_map map;
    struct topology_gicr_window w;
    uint64_t frame = 0, order[12], a16_order[18];
    const uint64_t base = 0x17080000;

    begin("gicr-discovery");
    build_image(&img, base, 0x40000, sp11.core, 12, 1);
    w = make_window(base, base + 12 * 0x40000, 0x40000, 4);
    poison(&map, sizeof map);
    CHECK_EQ(topology_gicr_discover(&w, image_read, &img, &map), TOPOLOGY_GICR_OK);
    CHECK_EQ(map.count, 12);
    CHECK_EQ(map.stride, 0x40000);
    CHECK_EQ(img.reads, 12);
    for (unsigned i = 0; i < 12; i++) {
        CHECK_EQ(map.frame[i].base, base + (uint64_t)i * 0x40000);
        CHECK_EQ(map.frame[i].affinity, sp11.core[i]);
        CHECK_EQ(map.frame[i].typer, img.typer[i]);
        CHECK_EQ(topology_gicr_find(&map, sp11.core[i], &frame), TOPOLOGY_GICR_OK);
        CHECK_EQ(frame, base + (uint64_t)i * 0x40000);
    }
    CHECK_EQ(topology_gicr_find(&map, 0x30000, &frame), TOPOLOGY_GICR_AFFINITY_NOT_FOUND);
    CHECK_EQ(topology_gicr_find(&map, 0x400, &frame), TOPOLOGY_GICR_AFFINITY_NOT_FOUND);
    CHECK_EQ(topology_gicr_find(NULL, 0x0, &frame), TOPOLOGY_GICR_BAD_ARGUMENT);
    CHECK_EQ(topology_gicr_find(&map, 0x0, NULL), TOPOLOGY_GICR_BAD_ARGUMENT);
    CHECK_EQ(topology_gicr_check_window(&w, 12), TOPOLOGY_GICR_OK);

    /* The default stride of the architecture revision gives the same answer. */
    w = make_window(base, base + 12 * 0x40000, 0, 4);
    CHECK_EQ(topology_gicr_discover(&w, image_read, &img, &map), TOPOLOGY_GICR_OK);
    CHECK_EQ(map.count, 12);

    /* GICv3: two 64 KiB frames per redistributor. */
    build_image(&img, base, 0x20000, sp11.core, 12, 0);
    w = make_window(base, base + 12 * 0x20000, 0, 3);
    CHECK_EQ(topology_gicr_discover(&w, image_read, &img, &map), TOPOLOGY_GICR_OK);
    CHECK_EQ(map.count, 12);
    CHECK_EQ(map.stride, 0x20000);
    CHECK_EQ(map.frame[11].base, base + 11 * 0x20000);

    /* v3 binding with the v4 stride the public devicetree declares. */
    build_image(&img, base, 0x40000, sp11.core, 12, 1);
    w = make_window(base, base + 12 * 0x40000, 0x40000, 3);
    CHECK_EQ(topology_gicr_discover(&w, image_read, &img, &map), TOPOLOGY_GICR_OK);
    CHECK_EQ(map.count, 12);

    /* Frames in an order that differs from the affinities: lookup is by affinity. */
    for (unsigned i = 0; i < 12; i++)
        order[i] = sp11.core[(i + 5) % 12];
    build_image(&img, base, 0x40000, order, 12, 1);
    w = make_window(base, base + 12 * 0x40000, 0x40000, 4);
    CHECK_EQ(topology_gicr_discover(&w, image_read, &img, &map), TOPOLOGY_GICR_OK);
    for (unsigned k = 0; k < 12; k++) {
        CHECK_EQ(topology_gicr_find(&map, sp11.core[k], &frame), TOPOLOGY_GICR_OK);
        CHECK_EQ(frame, base + (uint64_t)((k + 7) % 12) * 0x40000);
    }

    /* The A16 window: 18 frames, the last in the last slot. */
    for (unsigned i = 0; i < 18; i++)
        a16_order[i] = a16.core[i];
    build_image(&img, base, 0x40000, a16_order, 18, 1);
    w = make_window(base, 0x17500000, 0, 4);
    CHECK_EQ(topology_gicr_discover(&w, image_read, &img, &map), TOPOLOGY_GICR_OK);
    CHECK_EQ(map.count, 18);
    CHECK_EQ(map.frame[17].base, 0x17500000 - 0x40000);

    /* The frame limit itself is allowed: 32 frames with Last on the 32nd. */
    {
        uint64_t many[40];
        for (unsigned i = 0; i < 40; i++)
            many[i] = (uint64_t)(i / 4) << 16 | (uint64_t)(i % 4) << 8;
        build_image(&img, base, 0x40000, many, TOPOLOGY_GICR_MAX_FRAMES, 1);
        w = make_window(base, base + (uint64_t)TOPOLOGY_GICR_MAX_FRAMES * 0x40000, 0x40000, 4);
        CHECK_EQ(topology_gicr_discover(&w, image_read, &img, &map), TOPOLOGY_GICR_OK);
        CHECK_EQ(map.count, TOPOLOGY_GICR_MAX_FRAMES);
    }
}

static void expect_discover_failure(const char *name, const struct topology_gicr_window *w,
                                    struct image *img, enum topology_gicr_status want,
                                    unsigned max_reads)
{
    struct topology_gicr_map map;
    begin(name);
    poison(&map, sizeof map);
    img->reads = 0;
    CHECK_EQ(topology_gicr_discover(w, image_read, img, &map), want);
    CHECK(map_is_clear(&map));
    CHECK(img->reads <= max_reads);
}

static void test_gicr_faulty_images(void)
{
    struct image img;
    struct topology_gicr_window w;
    struct topology_gicr_map map;
    uint64_t many[40];
    const uint64_t base = 0x17080000, stride = 0x40000;
    const uint64_t end = base + 12 * stride;

    build_image(&img, base, stride, sp11.core, 12, 1);
    img.typer[11] &= ~UINT64_C(0x10);
    w = make_window(base, end, stride, 4);
    expect_discover_failure("gicr-faulty/no-last-window-ends-on-boundary", &w, &img,
                            TOPOLOGY_GICR_NO_LAST, 12);

    build_image(&img, base, stride, sp11.core, 12, 1);
    w = make_window(base, base + 11 * stride, stride, 4);
    expect_discover_failure("gicr-faulty/no-last-window-too-short", &w, &img,
                            TOPOLOGY_GICR_NO_LAST, 11);

    w = make_window(base, end - 0x10000, stride, 4);
    expect_discover_failure("gicr-faulty/partial-trailing-frame", &w, &img,
                            TOPOLOGY_GICR_WALK_PAST_LIMIT, 11);
    w = make_window(base, base + 11 * stride + 0x10000, stride, 4);
    expect_discover_failure("gicr-faulty/partial-trailing-frame", &w, &img,
                            TOPOLOGY_GICR_WALK_PAST_LIMIT, 11);

    img.typer[5] = typer_value(sp11.core[3], 5, 0, 1);
    w = make_window(base, end, stride, 4);
    expect_discover_failure("gicr-faulty/duplicate-affinity", &w, &img,
                            TOPOLOGY_GICR_DUPLICATE_AFFINITY, 6);

    /* A v3 stride on a v4 image reads the middle of each frame, which is zero. */
    build_image(&img, base, stride, sp11.core, 12, 1);
    w = make_window(base, end, 0, 3);
    expect_discover_failure("gicr-faulty/v3-stride-on-v4-image", &w, &img,
                            TOPOLOGY_GICR_DUPLICATE_AFFINITY, 2);

    /* A v4 stride on a v3 image skips every other frame, including the last. */
    build_image(&img, base, 0x20000, sp11.core, 12, 0);
    w = make_window(base, base + 12 * 0x20000, 0, 4);
    expect_discover_failure("gicr-faulty/v4-stride-on-v3-image", &w, &img,
                            TOPOLOGY_GICR_NO_LAST, 6);
    CHECK_EQ(topology_gicr_check_window(&w, 12), TOPOLOGY_GICR_WINDOW_MISMATCH);

    w = make_window(base, end, 0x20000, 4);
    expect_discover_failure("gicr-faulty/explicit-v4-with-v3-stride", &w, &img,
                            TOPOLOGY_GICR_BAD_STRIDE, 0);
    w = make_window(base, end, 0x48000, 4);
    expect_discover_failure("gicr-faulty/explicit-v4-with-v3-stride", &w, &img,
                            TOPOLOGY_GICR_BAD_STRIDE, 0);

    build_image(&img, base, stride, sp11.core, 12, 1);
    for (uint32_t version = 0; version < 8; version++) {
        w = make_window(base, end, stride, version);
        if (version != 3 && version != 4)
            expect_discover_failure("gicr-faulty/bad-version", &w, &img,
                                    TOPOLOGY_GICR_BAD_VERSION, 0);
    }

    w = make_window(base + 0x8000, end, stride, 4);
    expect_discover_failure("gicr-faulty/unaligned-base", &w, &img,
                            TOPOLOGY_GICR_UNALIGNED_BASE, 0);

    w = make_window(base, base, stride, 4);
    expect_discover_failure("gicr-faulty/bad-range", &w, &img, TOPOLOGY_GICR_BAD_RANGE, 0);
    w = make_window(base, base - 0x10000, stride, 4);
    expect_discover_failure("gicr-faulty/bad-range", &w, &img, TOPOLOGY_GICR_BAD_RANGE, 0);
    w = make_window(UINT64_C(0xfffffffffffc0000), UINT64_C(0xfffffffffffc0000) + stride, stride, 4);
    expect_discover_failure("gicr-faulty/bad-range", &w, &img, TOPOLOGY_GICR_BAD_RANGE, 0);

    /* Windows at the top of the address space must neither wrap nor read. */
    w = make_window(UINT64_C(0xffffffffffff0000), UINT64_MAX, stride, 4);
    expect_discover_failure("gicr-faulty/address-overflow", &w, &img,
                            TOPOLOGY_GICR_WALK_PAST_LIMIT, 0);
    w = make_window(UINT64_C(0xfffffffffffc0000), UINT64_MAX, stride, 4);
    expect_discover_failure("gicr-faulty/address-overflow", &w, &img,
                            TOPOLOGY_GICR_WALK_PAST_LIMIT, 0);

    build_image(&img, base, stride, sp11.core, 12, 1);
    img.typer[4] = ~UINT64_C(0);
    w = make_window(base, end, stride, 4);
    expect_discover_failure("gicr-faulty/all-ones-typer", &w, &img, TOPOLOGY_GICR_BAD_TYPER, 5);

    build_image(&img, base, stride, sp11.core, 12, 1);
    img.fail_address = base + 3 * stride + 8;
    expect_discover_failure("gicr-faulty/read-failure", &w, &img, TOPOLOGY_GICR_READ_FAILED, 4);
    build_image(&img, base, stride, sp11.core, 0, 1);
    expect_discover_failure("gicr-faulty/read-failure", &w, &img, TOPOLOGY_GICR_READ_FAILED, 1);

    for (unsigned i = 0; i < 40; i++)
        many[i] = (uint64_t)(i / 4) << 16 | (uint64_t)(i % 4) << 8;
    build_image(&img, base, stride, many, 40, 1);
    img.typer[39] &= ~UINT64_C(0x10);
    w = make_window(base, base + 40 * stride, stride, 4);
    expect_discover_failure("gicr-faulty/too-many-frames", &w, &img, TOPOLOGY_GICR_TOO_MANY,
                            TOPOLOGY_GICR_MAX_FRAMES);

    /* Missing arguments leave nothing behind and read nothing. */
    begin("gicr-faulty/null-arguments");
    build_image(&img, base, stride, sp11.core, 12, 1);
    w = make_window(base, end, stride, 4);
    CHECK_EQ(topology_gicr_discover(NULL, image_read, &img, &map), TOPOLOGY_GICR_BAD_ARGUMENT);
    CHECK_EQ(topology_gicr_discover(&w, NULL, &img, &map), TOPOLOGY_GICR_BAD_ARGUMENT);
    CHECK_EQ(topology_gicr_discover(&w, image_read, &img, NULL), TOPOLOGY_GICR_BAD_ARGUMENT);

    /* A map left over from a good run must not survive a later failure. */
    begin("gicr-faulty/stale-map");
    CHECK_EQ(topology_gicr_discover(&w, image_read, &img, &map), TOPOLOGY_GICR_OK);
    CHECK_EQ(map.count, 12);
    w = make_window(base + 0x8000, end, stride, 4);
    CHECK_EQ(topology_gicr_discover(&w, image_read, &img, &map), TOPOLOGY_GICR_UNALIGNED_BASE);
    CHECK(map_is_clear(&map));
}

static void test_gicr_window_check(void)
{
    struct topology_gicr_window w;

    begin("gicr-window");
    /* The A16 window holds 18 frames; on a 12-core machine its limit would
     * admit six frames of unrelated MMIO. */
    w = make_window(0x17080000, 0x17500000, 0, 4);
    CHECK_EQ(topology_gicr_check_window(&w, 18), TOPOLOGY_GICR_OK);
    CHECK_EQ(topology_gicr_check_window(&w, 12), TOPOLOGY_GICR_WINDOW_MISMATCH);
    w = make_window(0x17080000, 0x17380000, 0x40000, 4);
    CHECK_EQ(topology_gicr_check_window(&w, 12), TOPOLOGY_GICR_OK);
    CHECK_EQ(topology_gicr_check_window(&w, 11), TOPOLOGY_GICR_WINDOW_MISMATCH);
    CHECK_EQ(topology_gicr_check_window(&w, 13), TOPOLOGY_GICR_WINDOW_MISMATCH);
    CHECK_EQ(topology_gicr_check_window(&w, 18), TOPOLOGY_GICR_WINDOW_MISMATCH);
    CHECK_EQ(topology_gicr_check_window(&w, 0), TOPOLOGY_GICR_WINDOW_MISMATCH);
    /* The window size alone does not fix the stride: a v3 revision with the
     * default stride reads the same 3 MiB as 24 frames. */
    w = make_window(0x17080000, 0x17380000, 0, 3);
    CHECK_EQ(topology_gicr_check_window(&w, 12), TOPOLOGY_GICR_WINDOW_MISMATCH);
    CHECK_EQ(topology_gicr_check_window(&w, 24), TOPOLOGY_GICR_OK);
    w = make_window(0x17080000, 0x17380000 + 0x10000, 0x40000, 4);
    CHECK_EQ(topology_gicr_check_window(&w, 12), TOPOLOGY_GICR_WINDOW_MISMATCH);
    w = make_window(0x17088000, 0x17388000, 0x40000, 4);
    CHECK_EQ(topology_gicr_check_window(&w, 12), TOPOLOGY_GICR_UNALIGNED_BASE);
    w = make_window(0x17380000, 0x17080000, 0x40000, 4);
    CHECK_EQ(topology_gicr_check_window(&w, 12), TOPOLOGY_GICR_BAD_RANGE);
    w = make_window(0x17080000, 0x17380000, 0x40000, 2);
    CHECK_EQ(topology_gicr_check_window(&w, 12), TOPOLOGY_GICR_BAD_VERSION);
    w = make_window(0x17080000, 0x17380000, 0x20000, 4);
    CHECK_EQ(topology_gicr_check_window(&w, 12), TOPOLOGY_GICR_BAD_STRIDE);
    CHECK_EQ(topology_gicr_check_window(NULL, 12), TOPOLOGY_GICR_BAD_ARGUMENT);
}

static void test_gicr_assign(void)
{
    struct image img;
    struct topology_gicr_map map;
    struct topology_gicr_window w;
    struct topology_placement p;
    uint64_t out[TOPOLOGY_MAX_VCPUS], a16_order[18];
    static const uint64_t primaries[] = {0x0, 0x10100, 0x20300};
    const uint64_t base = 0x17080000, stride = 0x40000;

    begin("gicr-assign");
    build_image(&img, base, stride, sp11.core, 12, 1);
    w = make_window(base, base + 12 * stride, stride, 4);
    CHECK_EQ(topology_gicr_discover(&w, image_read, &img, &map), TOPOLOGY_GICR_OK);
    for (unsigned s = 0; s < 2; s++)
        for (unsigned n = 1; n <= 12; n++)
            for (unsigned pi = 0; pi < 3; pi++) {
                const struct topology_policy *policy =
                    s ? &topology_policy_spread : &topology_policy_pack;
                CHECK_EQ(topology_place(&topology_profile_sp11, policy, n, primaries[pi], 1, &p),
                         TOPOLOGY_OK);
                poison(out, sizeof out);
                CHECK_EQ(topology_gicr_assign(&map, &p, out, TOPOLOGY_MAX_VCPUS), TOPOLOGY_GICR_OK);
                for (unsigned i = 0; i < n; i++) {
                    CHECK_EQ(out[i], base + index_of(sp11.core, 12, p.affinity[i]) * stride);
                    CHECK_EQ(topology_gicr_check_assignment(&map, p.affinity[i], out[i]),
                             TOPOLOGY_GICR_OK);
                }
            }

    /* tools/test-secondary-runtime.py: Prime layout, vCPU i on frame 6 + i. */
    for (unsigned i = 0; i < 18; i++)
        a16_order[i] = a16.core[i];
    build_image(&img, base, stride, a16_order, 18, 1);
    w = make_window(base, 0x17500000, 0, 4);
    CHECK_EQ(topology_gicr_discover(&w, image_read, &img, &map), TOPOLOGY_GICR_OK);
    CHECK_EQ(topology_place(&topology_profile_a16, &topology_policy_pack, 6, 0x10000, 0, &p),
             TOPOLOGY_OK);
    CHECK_EQ(topology_gicr_assign(&map, &p, out, TOPOLOGY_MAX_VCPUS), TOPOLOGY_GICR_OK);
    for (unsigned i = 0; i < 6; i++)
        CHECK_EQ(out[i], base + (6 + i) * stride);
    CHECK_EQ(out[0], 0x17200000);
    CHECK_EQ(out[5], 0x17340000);
    CHECK_EQ(topology_place(&topology_profile_a16, &topology_policy_pack, 6, 0x0, 0, &p),
             TOPOLOGY_OK);
    CHECK_EQ(topology_gicr_assign(&map, &p, out, TOPOLOGY_MAX_VCPUS), TOPOLOGY_GICR_OK);
    for (unsigned i = 0; i < 6; i++)
        CHECK_EQ(out[i], base + i * stride);

    /* The window only holds 12 frames of SP11: an A16 layout has cores it lacks. */
    build_image(&img, base, stride, sp11.core, 12, 1);
    w = make_window(base, base + 12 * stride, stride, 4);
    CHECK_EQ(topology_gicr_discover(&w, image_read, &img, &map), TOPOLOGY_GICR_OK);
    CHECK_EQ(topology_place(&topology_profile_a16, &topology_policy_pack, 6, 0x0, 0, &p), TOPOLOGY_OK);
    poison(out, sizeof out);
    CHECK_EQ(topology_gicr_assign(&map, &p, out, TOPOLOGY_MAX_VCPUS),
             TOPOLOGY_GICR_AFFINITY_NOT_FOUND);
    for (unsigned i = 0; i < 6; i++)
        CHECK_EQ(out[i], 0);

    /* A frame whose TYPER holds garbage is unique, so discovery accepts it, but
     * the core that should have been there cannot be assigned. */
    build_image(&img, base, stride, sp11.core, 12, 1);
    img.typer[5] = UINT64_C(0x0123456789abcdef);
    CHECK_EQ(topology_gicr_discover(&w, image_read, &img, &map), TOPOLOGY_GICR_OK);
    CHECK_EQ(topology_place(&topology_profile_sp11, &topology_policy_pack, 12, 0x0, 1, &p), TOPOLOGY_OK);
    poison(out, sizeof out);
    CHECK_EQ(topology_gicr_assign(&map, &p, out, TOPOLOGY_MAX_VCPUS),
             TOPOLOGY_GICR_AFFINITY_NOT_FOUND);
    for (unsigned i = 0; i < 12; i++)
        CHECK_EQ(out[i], 0);
    CHECK_EQ(topology_place(&topology_profile_sp11, &topology_policy_pack, 4, 0x0, 0, &p), TOPOLOGY_OK);
    CHECK_EQ(topology_gicr_assign(&map, &p, out, TOPOLOGY_MAX_VCPUS), TOPOLOGY_GICR_OK);

    /* Two cores mapped to one frame (an aliased redistributor). */
    memset(&map, 0, sizeof map);
    map.count = 2;
    map.stride = stride;
    map.frame[0].base = base;
    map.frame[0].affinity = 0x0;
    map.frame[1].base = base;
    map.frame[1].affinity = 0x100;
    CHECK_EQ(topology_place(&topology_profile_sp11, &topology_policy_pack, 2, 0x0, 0, &p), TOPOLOGY_OK);
    CHECK_EQ(topology_gicr_assign(&map, &p, out, TOPOLOGY_MAX_VCPUS),
             TOPOLOGY_GICR_DUPLICATE_AFFINITY);
    CHECK_EQ(out[0], 0);
    CHECK_EQ(out[1], 0);

    CHECK_EQ(topology_gicr_assign(&map, &p, out, 1), TOPOLOGY_GICR_BAD_ARGUMENT);
    CHECK_EQ(topology_gicr_assign(NULL, &p, out, TOPOLOGY_MAX_VCPUS), TOPOLOGY_GICR_BAD_ARGUMENT);
    CHECK_EQ(topology_gicr_assign(&map, NULL, out, TOPOLOGY_MAX_VCPUS), TOPOLOGY_GICR_BAD_ARGUMENT);
    CHECK_EQ(topology_gicr_assign(&map, &p, NULL, TOPOLOGY_MAX_VCPUS), TOPOLOGY_GICR_BAD_ARGUMENT);
    p.count = 0;
    CHECK_EQ(topology_gicr_assign(&map, &p, out, TOPOLOGY_MAX_VCPUS), TOPOLOGY_GICR_BAD_ARGUMENT);
    map.count = TOPOLOGY_GICR_MAX_FRAMES + 1;
    CHECK_EQ(topology_place(&topology_profile_sp11, &topology_policy_pack, 2, 0x0, 0, &p), TOPOLOGY_OK);
    CHECK_EQ(topology_gicr_assign(&map, &p, out, TOPOLOGY_MAX_VCPUS), TOPOLOGY_GICR_BAD_ARGUMENT);
}

static void test_gicr_check_assignment(void)
{
    struct image img;
    struct topology_gicr_map map;
    struct topology_gicr_window w;
    const uint64_t base = 0x17080000, stride = 0x40000;

    begin("gicr-check-assignment");
    build_image(&img, base, stride, sp11.core, 12, 1);
    w = make_window(base, base + 12 * stride, stride, 4);
    CHECK_EQ(topology_gicr_discover(&w, image_read, &img, &map), TOPOLOGY_GICR_OK);
    CHECK_EQ(topology_gicr_check_assignment(&map, 0x10100, base + 5 * stride), TOPOLOGY_GICR_OK);
    CHECK_EQ(topology_gicr_check_assignment(&map, 0x10100, base + 4 * stride),
             TOPOLOGY_GICR_AFFINITY_MISMATCH);
    CHECK_EQ(topology_gicr_check_assignment(&map, 0x10100, base + 5 * stride + 0x10000),
             TOPOLOGY_GICR_AFFINITY_MISMATCH);
    CHECK_EQ(topology_gicr_check_assignment(&map, 0x30000, base), TOPOLOGY_GICR_AFFINITY_NOT_FOUND);
    CHECK_EQ(topology_gicr_check_assignment(NULL, 0x0, base), TOPOLOGY_GICR_BAD_ARGUMENT);
}

void test_gicr_groups(void)
{
    test_gicr_conversions();
    test_gicr_constants_and_stride();
    test_gicr_discovery();
    test_gicr_faulty_images();
    test_gicr_window_check();
    test_gicr_assign();
    test_gicr_check_assignment();
}

/* ---- command line: redistributor assignment ---- */

/* "<affinity>@<redistributor>,..." lists the inventory of a host tool. */
static int cli_parse_inventory(const char *list, uint64_t base, uint64_t stride,
                               uint64_t *affinity, uint64_t *redistributor, unsigned *count)
{
    const char *cursor = list;

    *count = 0;
    while (*cursor) {
        const char *end = strchr(cursor, ',');
        size_t length = end ? (size_t)(end - cursor) : strlen(cursor);
        char item[64], *at;
        uint64_t a, rd;

        if (!length || length >= sizeof item || *count >= IMAGE_MAX_FRAMES)
            return 0;
        memcpy(item, cursor, length);
        item[length] = 0;
        at = strchr(item, '@');
        if (!at)
            return 0;
        *at++ = 0;
        if (!parse_number(item, &a) || !parse_number(at, &rd) || rd < base ||
            (rd - base) % stride || (rd - base) / stride >= IMAGE_MAX_FRAMES)
            return 0;
        affinity[*count] = a;
        redistributor[*count] = rd;
        (*count)++;
        cursor += length + (end ? 1 : 0);
    }
    return *count != 0;
}

/* Frames between base and the highest listed redistributor that no entry names
 * keep affinity 0: the model must reject such an inventory as a duplicate. */
static void cli_build_image(struct image *img, uint64_t base, uint64_t stride, uint32_t version,
                            const uint64_t *affinity, const uint64_t *redistributor,
                            unsigned count)
{
    unsigned max_index = 0, i;

    memset(img, 0, sizeof *img);
    img->base = base;
    img->stride = stride;
    for (i = 0; i < count; i++)
        if ((redistributor[i] - base) / stride > max_index)
            max_index = (unsigned)((redistributor[i] - base) / stride);
    img->frames = max_index + 1;
    for (i = 0; i < count; i++) {
        unsigned index = (unsigned)((redistributor[i] - base) / stride);
        img->typer[index] = typer_value(affinity[i], index, index == max_index, version == 4);
    }
}

int gicr_cli_assign(const struct topology_placement *p, char **argv)
{
    struct image img;
    struct topology_gicr_map map;
    struct topology_gicr_window w;
    uint64_t version, stride, base, limit, affinity[IMAGE_MAX_FRAMES],
             redistributor[IMAGE_MAX_FRAMES], out[TOPOLOGY_MAX_VCPUS];
    unsigned listed;
    enum topology_gicr_status gst;

    if (!parse_number(argv[7], &version) || !parse_number(argv[8], &stride) || !stride ||
        !parse_number(argv[9], &base) || !parse_number(argv[10], &limit) ||
        !cli_parse_inventory(argv[11], base, stride, affinity, redistributor, &listed)) {
        fprintf(stderr, "usage error\n");
        return 2;
    }
    cli_build_image(&img, base, stride, (uint32_t)version, affinity, redistributor, listed);
    w = make_window(base, limit, stride, (uint32_t)version);
    gst = topology_gicr_discover(&w, image_read, &img, &map);
    printf("gicr=%d\n", (int)gst);
    if (gst != TOPOLOGY_GICR_OK)
        return 1;
    gst = topology_gicr_assign(&map, p, out, TOPOLOGY_MAX_VCPUS);
    printf("assign=%d\n", (int)gst);
    for (unsigned i = 0; i < p->count; i++)
        printf("rd %u %#llx\n", i, (unsigned long long)out[i]);
    return gst == TOPOLOGY_GICR_OK ? 0 : 1;
}
