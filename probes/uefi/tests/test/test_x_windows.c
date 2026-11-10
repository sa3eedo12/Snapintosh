/* SPDX-License-Identifier: MIT */
/* Host tests of the device windows of T05 to T07: a base that comes out of a
 * table (or a BAR) is data, so it may be zero, misaligned, wrapping or the
 * address of ordinary RAM. Each case plants plausible registers at the bad
 * address, so a read there would have returned a believable observation; the
 * check is that the bus was never touched. The fixtures are those of
 * test_x_dev.c (xdev.h). */
#include "xdev.h"

static tbl madt_of(uint64_t gicd, uint64_t gicr, uint32_t gicr_len, uint64_t its)
{
    tbl t;

    t_start(&t, "APIC", 8);
    madt_gicc(&t, 1, GICR, 0x0);
    madt_gicc(&t, 1, GICR + 0x20000, 0x1);
    madt_gicd(&t, gicd, 3);
    madt_gicr(&t, gicr, gicr_len);
    madt_its(&t, its);
    t_seal(&t);
    return t;
}

static void gic_regs_for(devmodel *d, uint64_t gicd, uint64_t gicr, uint64_t its)
{
    dm_set(d, gicd + 0x0000, 0x00000050);
    dm_set(d, gicd + 0x0004, 0x00FF0408);
    dm_set(d, gicd + 0x0008, 0x0200143B);
    dm_set(d, gicd + 0xFFE8, 3u << 4);
    for (unsigned f = 0; f < 2; f++) {
        uint64_t fr = gicr + 0x20000ull * f;

        dm_set(d, fr + 0xFFE8, 3u << 4);
        dm_set(d, fr + 0x0008, f == 1 ? 0x10 : 0);
        dm_set(d, fr + 0x000C, f);
        dm_set(d, fr + 0x0014, 0x6);
    }
    dm_set(d, its + 0x0000, 1);
    dm_set(d, its + 0x0008, 0x0000C781);
    dm_set(d, its + 0xFFE8, 3u << 4);
}

static int parse_madt(const tbl *t, tests_madt *m)
{
    uint8_t *x = exact_copy(t->b, t->len);
    int rc = tests_madt_parse(x, t->len, m);

    free(x);
    return rc;
}

static int parse_mcfg(const tbl *t, tests_mcfg *m)
{
    uint8_t *x = exact_copy(t->b, t->len);
    int rc = tests_mcfg_parse(x, t->len, m);

    free(x);
    return rc;
}

static int parse_iort(const tbl *t, tests_iort *m)
{
    uint8_t *x = exact_copy(t->b, t->len);
    int rc = tests_iort_parse(x, t->len, m);

    free(x);
    return rc;
}

static tbl iort_of(unsigned n, const uint64_t *bases)
{
    tbl t;

    t_start(&t, "IORT", 12);
    st32(t.b + 36, n);
    st32(t.b + 40, 48);
    for (unsigned i = 0; i < n; i++)
        iort_node(&t, 4, 64, bases[i]);
    t_seal(&t);
    return t;
}

static tbl mcfg_of(uint64_t base, unsigned start, unsigned end)
{
    tbl t;

    t_start(&t, "MCFG", 8);
    mcfg_entry(&t, base, 0, start, end);
    t_seal(&t);
    return t;
}

static void windows_numbers(void)
{
    /* The numbers alone: zero, then range, then alignment is the order of the reason. */
    CHECK_EQ(tests_window_check(0x10000000ull, 0x10000, TESTS_ALIGN_GICD), TESTS_WIN_OK);
    CHECK_EQ(tests_window_check(0, 0x10000, TESTS_ALIGN_GICD), TESTS_WIN_ZERO);
    CHECK_EQ(tests_window_check(0x10001000ull, 0x10000, TESTS_ALIGN_GICD), TESTS_WIN_MISALIGNED);
    CHECK_EQ(tests_window_check(0x10000800ull, 0x100, TESTS_ALIGN_NVME), TESTS_WIN_MISALIGNED);
    CHECK_EQ(tests_window_check(0x10000000ull, 0, TESTS_ALIGN_GICD), TESTS_WIN_RANGE);
    CHECK_EQ(tests_window_check(~0ull - 0xFFFFull, 0x10000, TESTS_ALIGN_GICD), TESTS_WIN_RANGE);
    CHECK_EQ(tests_window_check(~0ull - 0xFFFull, 0x2000, TESTS_ALIGN_NVME), TESTS_WIN_RANGE);
    CHECK_EQ(tests_window_check(TESTS_PA_LIMIT, 0x1000, 0x1000), TESTS_WIN_RANGE);
    CHECK_EQ(tests_window_check(TESTS_PA_LIMIT - 0x1000, 0x2000, 0x1000), TESTS_WIN_RANGE);
    CHECK_EQ(tests_window_check(TESTS_PA_LIMIT - 0x1000, 0x1000, 0x1000), TESTS_WIN_OK);
    CHECK_EQ(tests_window_check(0x1234, 4, 0), TESTS_WIN_OK); /* no alignment asked for */
    CHECK_EQ(tests_allow_add(&(tests_allow){0}, 0, 0x1000, "zero"), -1);
}

static void windows_policy(world *w)
{
    uint8_t map[48 * 8];
    probe_memmap mm, none;
    tests_allow a;
    uint32_t v;
    unsigned n = 0;

    memset(map, 0, sizeof map);
    n = md_add(map, n, 7, 0x80000000ull, 0x1000);   /* 16 MiB of RAM */
    n = md_add(map, n, 11, 0x10000000ull, 0x10);    /* 64 KiB of MMIO */
    n = md_add(map, n, 12, 0x20000000ull, 0x10);    /* MMIO port space */
    CHECK_EQ(probe_memmap_init(&mm, map, n * 48, sizeof map, 48), 0);

    /* Wholly MMIO passes under both policies; port space counts as MMIO. */
    CHECK_EQ(tests_window_policy(&mm, 0, 0x10000000ull, 0x10000), TESTS_WIN_OK);
    CHECK_EQ(tests_window_policy(&mm, 1, 0x10000000ull, 0x10000), TESTS_WIN_OK);
    CHECK_EQ(tests_window_policy(&mm, 1, 0x20000000ull, 0x1000), TESTS_WIN_OK);
    /* Not mentioned at all, and only partly mentioned: the strict policy refuses. */
    CHECK_EQ(tests_window_policy(&mm, 0, 0x30000000ull, 0x1000), TESTS_WIN_OK);
    CHECK_EQ(tests_window_policy(&mm, 1, 0x30000000ull, 0x1000), TESTS_WIN_NOT_MMIO);
    CHECK_EQ(tests_window_policy(&mm, 0, 0x10008000ull, 0x10000), TESTS_WIN_OK);
    CHECK_EQ(tests_window_policy(&mm, 1, 0x10008000ull, 0x10000), TESTS_WIN_NOT_MMIO);
    /* RAM is refused under both, however little of the window touches it. */
    CHECK_EQ(tests_window_policy(&mm, 0, 0x80000000ull, 0x1000), TESTS_WIN_CONFLICT);
    CHECK_EQ(tests_window_policy(&mm, 1, 0x80000000ull, 0x1000), TESTS_WIN_CONFLICT);
    CHECK_EQ(tests_window_policy(&mm, 0, 0x80FFF000ull, 0x2000), TESTS_WIN_CONFLICT);
    CHECK_EQ(tests_window_policy(&mm, 0, 0x7FFFF000ull, 0x2000), TESTS_WIN_CONFLICT);
    CHECK_EQ(tests_window_policy(&mm, 0, 0x7FFFF000ull, 0x1000), TESTS_WIN_OK); /* ends where RAM starts */
    CHECK_EQ(tests_window_policy(&mm, 0, 0x81000000ull, 0x1000), TESTS_WIN_OK); /* starts where RAM ends */
    /* No map to judge by. */
    memset(&none, 0, sizeof none);
    CHECK_EQ(tests_window_policy(NULL, 0, 0x10000000ull, 0x1000), TESTS_WIN_NO_MAP);
    CHECK_EQ(tests_window_policy(&none, 0, 0x10000000ull, 0x1000), TESTS_WIN_NO_MAP);

    /* The admission function: refused windows are counted and never readable. */
    tests_allow_init(&a);
    CHECK_EQ(tests_allow_add_device(&a, &mm, 0, 0x10000000ull, 0x10000, TESTS_ALIGN_GICD, "mmio"), 0);
    CHECK_EQ(tests_allow_add_device(&a, &mm, 0, 0x30000000ull, 0x10000, TESTS_ALIGN_GICD, "unlisted"), 0);
    CHECK_EQ(a.n, 2);
    CHECK_EQ(a.unlisted, 1);
    CHECK_EQ(a.refused, 0);
    CHECK_EQ(tests_allow_add_device(&a, &mm, 0, 0x80000000ull, 0x10000, TESTS_ALIGN_GICD, "ram"),
             TESTS_WIN_CONFLICT);
    CHECK_EQ(tests_allow_add_device(&a, &mm, 0, 0, 0x10000, TESTS_ALIGN_GICD, "zero"), TESTS_WIN_ZERO);
    CHECK_EQ(tests_allow_add_device(&a, &mm, 0, 0x40001000ull, 0x10000, TESTS_ALIGN_GICD, "odd"),
             TESTS_WIN_MISALIGNED);
    CHECK_EQ(tests_allow_add_device(&a, &mm, 0, ~0ull - 0xFFFull, 0x10000, TESTS_ALIGN_NVME, "top"),
             TESTS_WIN_RANGE);
    CHECK_EQ(tests_allow_add_device(&a, NULL, 0, 0x40000000ull, 0x10000, TESTS_ALIGN_GICD, "nomap"),
             TESTS_WIN_NO_MAP);
    CHECK_EQ(a.n, 2);
    CHECK_EQ(a.refused, 5);
    CHECK_EQ(tests_rd32(&w->bus, &a, 0x80000000ull, &v), RD_UNJUSTIFIED);
    CHECK_EQ(tests_rd32(&w->bus, &a, 0x40000000ull, &v), RD_UNJUSTIFIED);
    CHECK_EQ(tests_rd32(&w->bus, &a, 0, &v), RD_UNJUSTIFIED);
    CHECK_EQ(w->dm.nlog, 0);
    CHECK_EQ(tests_rd32(&w->bus, &a, 0x10000000ull, &v), RD_FAULT); /* admitted: the bus is reached */
    CHECK_EQ(w->dm.nlog, 1);

    /* An MMIO descriptor and a RAM descriptor claiming the same bytes: RAM wins. */
    n = md_add(map, n, 7, 0x10004000ull, 4);
    CHECK_EQ(probe_memmap_init(&mm, map, n * 48, sizeof map, 48), 0);
    CHECK_EQ(tests_window_policy(&mm, 0, 0x10000000ull, 0x10000), TESTS_WIN_CONFLICT);
    CHECK_EQ(tests_window_policy(&mm, 1, 0x10000000ull, 0x10000), TESTS_WIN_CONFLICT);

    /* The list is bounded: the window after the last free slot is refused and counted. */
    tests_allow_init(&a);
    for (unsigned i = 0; i < TESTS_ALLOW_MAX; i++)
        CHECK_EQ(tests_allow_add_device(&a, &mm, 0, 0x30000000ull + 0x10000ull * i, 0x10000,
                                        TESTS_ALIGN_GICD, "w"), 0);
    CHECK_EQ(tests_allow_add_device(&a, &mm, 0, 0x38000000ull, 0x10000, TESTS_ALIGN_GICD, "one more"),
             TESTS_WIN_FULL);
    CHECK_EQ(a.refused, 1);
}

static void windows_parsers(void)
{
    tests_madt madt;
    tests_mcfg mcfg;
    tests_iort iort;
    tbl t;
    uint64_t smmu[3];

    /* MADT: the GICD entry carries the reason, the list entries are dropped and counted. */
    t = madt_of(GICD, GICR, 0x40000, ITS);
    CHECK_EQ(parse_madt(&t, &madt), 0);
    CHECK_EQ(madt.gicd_status, TESTS_WIN_OK);
    CHECK_EQ(madt.rejected, 0);
    t = madt_of(0, GICR, 0x40000, ITS);
    CHECK_EQ(parse_madt(&t, &madt), 0);
    CHECK_EQ(madt.gicd_status, TESTS_WIN_ZERO);
    t = madt_of(GICD + 0x1000, GICR, 0x40000, ITS);
    CHECK_EQ(parse_madt(&t, &madt), 0);
    CHECK_EQ(madt.gicd_status, TESTS_WIN_MISALIGNED);
    t = madt_of(0xFFFFFFFFFFFF0000ull, GICR, 0x40000, ITS);
    CHECK_EQ(parse_madt(&t, &madt), 0);
    CHECK_EQ(madt.gicd_status, TESTS_WIN_RANGE);

    {
        static const struct {
            uint64_t gicr, its;
            uint32_t len;
            const char *why;
        } bad[] = {
            {0, ITS, 0x40000, "GICR base zero"},
            {GICR + 0x800, ITS, 0x40000, "GICR base not page aligned"},
            {GICR, ITS, 0, "GICR empty"},
            {GICR, ITS, 0x41800, "GICR length not whole pages"},
            {0xFFFFFFFFFFFF0000ull, ITS, 0x40000, "GICR wraps the address space"},
            {GICR, 0, 0x40000, "ITS base zero"},
            {GICR, ITS + 0x1000, 0x40000, "ITS base not 64 KiB aligned"},
            {GICR, 0xFFFFFFFFFFFF0000ull, 0x40000, "ITS beyond the physical address range"},
        };

        for (unsigned i = 0; i < sizeof bad / sizeof bad[0]; i++) {
            t = madt_of(GICD, bad[i].gicr, bad[i].len, bad[i].its);
            if (parse_madt(&t, &madt) != 0 || madt.rejected != 1)
                printf("  not rejected: %s\n", bad[i].why);
            CHECK_EQ(parse_madt(&t, &madt), 0);
            CHECK_EQ(madt.rejected, 1);
            CHECK_EQ(madt.truncated, 0); /* a bad entry is not an overflowing list */
            CHECK_EQ(madt.n_gicr + madt.n_its, 1);
        }
    }
    /* A bad entry does not displace a good one beside it. */
    t_start(&t, "APIC", 8);
    madt_gicd(&t, GICD, 3);
    madt_gicr(&t, 0, 0x40000);
    madt_gicr(&t, GICR, 0x40000);
    madt_its(&t, ITS + 0x8000);
    madt_its(&t, ITS);
    t_seal(&t);
    CHECK_EQ(parse_madt(&t, &madt), 0);
    CHECK_EQ(madt.rejected, 2);
    CHECK_EQ(madt.n_gicr, 1);
    CHECK_EQU(madt.gicr[0].base, GICR);
    CHECK_EQ(madt.n_its, 1);
    CHECK_EQU(madt.its[0], ITS);

    /* MCFG: every entry keeps its place and carries its own status. */
    t = mcfg_of(0x60000000ull, 0, 3);
    CHECK_EQ(parse_mcfg(&t, &mcfg), 0);
    CHECK_EQ(mcfg.e[0].status, TESTS_WIN_OK);
    t = mcfg_of(0, 0, 3);
    CHECK_EQ(parse_mcfg(&t, &mcfg), 0);
    CHECK_EQ(mcfg.n, 1);
    CHECK_EQ(mcfg.e[0].status, TESTS_WIN_ZERO);
    t = mcfg_of(0x60080000ull, 0, 3); /* 512 KiB: not a bus boundary */
    CHECK_EQ(parse_mcfg(&t, &mcfg), 0);
    CHECK_EQ(mcfg.e[0].status, TESTS_WIN_MISALIGNED);
    t = mcfg_of(0xFFFFFFFFFFF00000ull, 0, 3); /* four buses run off the end of the address space */
    CHECK_EQ(parse_mcfg(&t, &mcfg), 0);
    CHECK_EQ(mcfg.e[0].status, TESTS_WIN_RANGE);
    t = mcfg_of(0, 4, 7); /* a window that is never read is still checked */
    CHECK_EQ(parse_mcfg(&t, &mcfg), 0);
    CHECK_EQ(mcfg.e[0].status, TESTS_WIN_ZERO);
    t_start(&t, "MCFG", 8);
    mcfg_entry(&t, 0x60000000ull, 0, 0, 3);
    mcfg_entry(&t, 0, 1, 0, 3);
    mcfg_entry(&t, 0x70000000ull, 2, 0, 255);
    t_seal(&t);
    CHECK_EQ(parse_mcfg(&t, &mcfg), 0);
    CHECK_EQ(mcfg.n, 3);
    CHECK_EQ(mcfg.e[0].status, TESTS_WIN_OK);
    CHECK_EQ(mcfg.e[1].status, TESTS_WIN_ZERO);
    CHECK_EQ(mcfg.e[2].status, TESTS_WIN_OK);

    /* IORT: SMMUv3 nodes with a bad base are dropped and counted. */
    smmu[0] = SMMU0;
    t = iort_of(1, smmu);
    CHECK_EQ(parse_iort(&t, &iort), 0);
    CHECK_EQ(iort.n, 1);
    CHECK_EQ(iort.rejected, 0);
    smmu[0] = 0;
    smmu[1] = SMMU1 + 0x1000;
    smmu[2] = 0xFFFFFFFFFFFF0000ull;
    for (unsigned i = 0; i < 3; i++) {
        t = iort_of(1, &smmu[i]);
        CHECK_EQ(parse_iort(&t, &iort), 0);
        CHECK(iort.valid);
        CHECK_EQ(iort.n, 0);
        CHECK_EQ(iort.rejected, 1);
        CHECK_EQ(iort.truncated, 0);
    }
    smmu[0] = SMMU0;
    smmu[1] = 0;
    smmu[2] = SMMU1;
    t = iort_of(3, smmu);
    CHECK_EQ(parse_iort(&t, &iort), 0);
    CHECK_EQ(iort.n, 2);
    CHECK_EQ(iort.rejected, 1);
    CHECK_EQU(iort.base[1], SMMU1);
}

static void windows_gic(world *w)
{
    tests_result r;
    tbl t;
    win wins[2] = {{GICD, 0x10000}, {ITS, 0x10000}};
    static const struct {
        uint64_t gicd;
        const char *row;
    } bad_gicd[] = {
        {0, "GICD REFUSED: BASE IS ZERO"},
        {GICD + 0x1000, "GICD REFUSED: MISALIGNED"},
        {0xFFFFFFFFFFFF0000ull, "GICD REFUSED: BAD RANGE"},
        {W_RAM_BASE + 0x10000, "GICD REFUSED: MAP SAYS NOT MMIO"},
    };

    /* A bad distributor base: not one read, the reason on the screen. */
    for (unsigned i = 0; i < sizeof bad_gicd / sizeof bad_gicd[0]; i++) {
        w_init(w);
        t = madt_of(bad_gicd[i].gicd, GICR, 0x40000, ITS);
        gic_setup(w, &t);
        gic_regs_for(&w->dm, bad_gicd[i].gicd, GICR, ITS);
        tests_run_gic(&w->ctx, &r);
        CHECK_EQ(r.verdict, TV_UNKNOWN);
        CHECK_EQ(w->dm.nlog, 0);
        CHECK(has_row(&r, bad_gicd[i].row));
        CHECK(has_row(&r, "NOTHING TOUCHED"));
        CHECK(x_rows_valid(&r));
        w_free(w);
    }

    /* A redistributor range inside RAM: the distributor is still read, the range
     * never, and the per-CPU match is not made (a range that was not read looks
     * like a CPU without a frame, which would be reported as a contradiction). */
    w_init(w);
    t = madt_of(GICD, W_RAM_BASE, 0x40000, ITS);
    gic_setup(w, &t);
    gic_regs_for(&w->dm, GICD, W_RAM_BASE, ITS);
    tests_run_gic(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_UNKNOWN);
    CHECK(read_at(&w->dm, GICD));
    CHECK(!read_at(&w->dm, W_RAM_BASE + 0xFFE8));
    CHECK_EQ(stray_reads(&w->dm, wins, 2), 0);
    CHECK(has_row(&r, "GICR0 REFUSED: MAP SAYS NOT MMIO"));
    CHECK(has_row(&r, "CPU MATCH SKIPPED: RANGE NOT READ"));
    CHECK(!has_row(&r, "NO FRAME="));
    CHECK(has_row(&r, "MAP MMIO=0 UNLISTED=2 REFUSED=1"));
    CHECK(x_rows_valid(&r));
    w_free(w);

    /* An ITS inside RAM. */
    w_init(w);
    t = madt_of(GICD, GICR, 0x40000, W_RAM_BASE + 0x40000);
    gic_setup(w, &t);
    gic_regs_for(&w->dm, GICD, GICR, W_RAM_BASE + 0x40000);
    tests_run_gic(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_UNKNOWN);
    CHECK(!read_at(&w->dm, W_RAM_BASE + 0x40000));
    CHECK(has_row(&r, "ITS0 REFUSED: MAP SAYS NOT MMIO"));
    CHECK(read_at(&w->dm, GICR + 0xFFE8)); /* the redistributors were read */
    w_free(w);

    /* A redistributor entry the parser dropped (zero base): UNKNOWN, never FAIL. */
    w_init(w);
    t = madt_of(GICD, 0, 0x40000, ITS);
    gic_setup(w, &t);
    gic_regs_for(&w->dm, GICD, 0, ITS);
    tests_run_gic(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_UNKNOWN);
    CHECK(!read_at(&w->dm, 0xFFE8));
    CHECK(has_row(&r, "MAP MMIO=0 UNLISTED=2 REFUSED=1"));
    CHECK(has_row(&r, "CPU MATCH SKIPPED: RANGE NOT READ"));
    w_free(w);

    /* Strict policy: nothing is described as MMIO, so nothing is read. */
    w_init(w);
    w->ctx.require_mmio_map = 1;
    t = good_madt();
    gic_setup(w, &t);
    gic_good_regs(&w->dm, 3, 1);
    tests_run_gic(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_UNKNOWN);
    CHECK_EQ(w->dm.nlog, 0);
    CHECK(has_row(&r, "GICD REFUSED: NOT MMIO IN MAP"));
    /* The map now lists the windows as MMIO: the same run reads everything. */
    w_map(w, 11, GICD, 0x10);
    w_map(w, 11, GICR, 0x40);
    w_map(w, 11, ITS, 0x10);
    tests_run_gic(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_PASS);
    CHECK(has_row(&r, "MAP STRICT MMIO=3 REFUSED=0"));
    CHECK_EQ(w->dm.unlisted, 0);
    /* The relaxed policy reads the same listed windows and says so. */
    w->ctx.require_mmio_map = 0;
    tests_run_gic(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_PASS);
    CHECK(has_row(&r, "MAP MMIO=3 UNLISTED=0 REFUSED=0"));
    w_free(w);

    /* An MMIO descriptor that a RAM descriptor overlaps does not make the window readable. */
    w_init(w);
    t = good_madt();
    gic_setup(w, &t);
    gic_good_regs(&w->dm, 3, 1);
    w_map(w, 11, GICD, 0x10);
    w_map(w, 7, GICD + 0x8000, 1);
    tests_run_gic(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_UNKNOWN);
    CHECK(!read_at(&w->dm, GICD));
    CHECK(has_row(&r, "GICD REFUSED: MAP SAYS NOT MMIO"));
    w_free(w);

    /* No memory map at all: no window is read. */
    for (int variant = 0; variant < 2; variant++) {
        w_init(w);
        t = good_madt();
        gic_setup(w, &t);
        gic_good_regs(&w->dm, 3, 1);
        if (variant == 0)
            w->ctx.memmap = NULL;
        else
            memset(&w->mm, 0, sizeof w->mm);
        tests_run_gic(&w->ctx, &r);
        CHECK_EQ(r.verdict, TV_UNKNOWN);
        CHECK_EQ(w->dm.nlog, 0);
        CHECK(has_row(&r, "GICD REFUSED: NO MEMORY MAP"));
        w_free(w);
    }
}

static void windows_pcie(world *w)
{
    tests_result r;
    tbl t;
    pcie_cfg p = good_pcie();
    win wins[1] = {{ECAM, 4u << 20}};
    static const struct {
        uint64_t base;
        unsigned end_bus;
        const char *row;
    } bad_ecam[] = {
        {0, 3, "W0 REFUSED: BASE IS ZERO"},
        {ECAM + 0x80000, 3, "W0 REFUSED: MISALIGNED"},
        {0xFFFFFFFFFFF00000ull, 3, "W0 REFUSED: BAD RANGE"},
        {W_RAM_BASE, 3, "W0 REFUSED: MAP SAYS NOT MMIO"},
        /* 256 MiB of buses whose last megabyte is RAM: one overlapping page is enough. */
        {W_RAM_BASE - 0xFF00000ull, 255, "W0 REFUSED: MAP SAYS NOT MMIO"},
    };

    for (unsigned i = 0; i < sizeof bad_ecam / sizeof bad_ecam[0]; i++) {
        w_init(w);
        t = mcfg_of(bad_ecam[i].base, 0, bad_ecam[i].end_bus);
        w_table(w, 1, "MCFG", &t);
        dm_set(&w->dm, bad_ecam[i].base + 0x00, 0x01201234);
        dm_set(&w->dm, bad_ecam[i].base + 0x08, 0x06040001);
        tests_run_pcie(&w->ctx, &r);
        CHECK_EQ(r.verdict, TV_UNKNOWN);
        CHECK_EQ(w->dm.nlog, 0);
        CHECK(has_row(&r, bad_ecam[i].row));
        CHECK(!has_row(&r, "NO NVME ENDPOINT FOUND")); /* nothing was looked at */
        CHECK(x_rows_valid(&r));
        w_free(w);
    }

    /* The NVMe BAR lies in RAM according to the map: configuration space is read
     * (the ECAM window is fine), the controller registers are not. */
    pcie_world(w, &p, 0);
    w_map(w, 7, NVME_BAR, 1);
    tests_run_pcie(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_UNKNOWN);
    CHECK(read_at(&w->dm, ecam_at(1, 0, 0x10)));
    CHECK(!read_at(&w->dm, NVME_BAR));
    CHECK(!read_at(&w->dm, NVME_BAR + 0x14));
    CHECK_EQ(stray_reads(&w->dm, wins, 1), 0);
    CHECK(has_row(&r, "NVME BAR0 REFUSED: MAP SAYS NOT MMIO"));
    CHECK(x_rows_valid(&r));
    w_free(w);

    /* Strict policy: undescribed windows are not read; described ones are. */
    pcie_world(w, &p, 0);
    w->ctx.require_mmio_map = 1;
    tests_run_pcie(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_UNKNOWN);
    CHECK_EQ(w->dm.nlog, 0);
    CHECK(has_row(&r, "W0 REFUSED: NOT MMIO IN MAP"));
    w_map(w, 11, ECAM, 0x400);
    tests_run_pcie(&w->ctx, &r); /* the BAR is still undescribed */
    CHECK_EQ(r.verdict, TV_UNKNOWN);
    CHECK(read_at(&w->dm, ecam_at(1, 0, 0x10)));
    CHECK(!read_at(&w->dm, NVME_BAR + 0x14));
    CHECK(has_row(&r, "NVME BAR0 REFUSED: NOT MMIO IN MAP"));
    w_map(w, 11, NVME_BAR, 1);
    tests_run_pcie(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_INFO);
    CHECK(read_at(&w->dm, NVME_BAR + 0x14));
    CHECK(has_row(&r, "MAP STRICT MMIO=2 REFUSED=0"));
    w_free(w);

    /* The default policy says how many windows the map did not describe. */
    pcie_world(w, &p, 0);
    tests_run_pcie(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_INFO);
    CHECK(has_row(&r, "MAP MMIO=0 UNLISTED=2 REFUSED=0"));
    CHECK(x_rows_valid(&r));
    w_free(w);
}

static void windows_smmu(world *w)
{
    tests_result r;
    tbl t;
    uint64_t nodes[2];
    win wins[1] = {{SMMU0, 0x100}};

    /* Bases the parser drops: the node is not touched and the row says why. */
    nodes[0] = 0;
    nodes[1] = SMMU1 + 0x1000;
    for (unsigned i = 0; i < 3; i++) {
        uint64_t b = i < 2 ? nodes[i] : 0xFFFFFFFFFFFF0000ull;

        w_init(w);
        t = iort_of(1, &b);
        w_table(w, 2, "IORT", &t);
        smmu_regs(&w->dm, b, 0, 0, 0);
        tests_run_smmu(&w->ctx, &r);
        CHECK_EQ(r.verdict, TV_UNKNOWN);
        CHECK_EQ(w->dm.nlog, 0);
        CHECK(has_row(&r, "SMMUV3 BASE BAD: NOT TOUCHED"));
        CHECK(x_rows_valid(&r));
        w_free(w);
    }

    /* A node inside RAM. */
    nodes[0] = W_RAM_BASE + 0x10000;
    w_init(w);
    t = iort_of(1, nodes);
    w_table(w, 2, "IORT", &t);
    smmu_regs(&w->dm, nodes[0], 0, 0, 0);
    tests_run_smmu(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_UNKNOWN);
    CHECK_EQ(w->dm.nlog, 0);
    CHECK(has_row(&r, "SMMU0 REFUSED: MAP SAYS NOT MMIO"));
    CHECK(x_rows_valid(&r));
    w_free(w);

    /* Two nodes, the second in RAM: the first is read, the second is not. */
    nodes[0] = SMMU0;
    nodes[1] = W_RAM_BASE + 0x10000;
    w_init(w);
    t = iort_of(2, nodes);
    w_table(w, 2, "IORT", &t);
    smmu_regs(&w->dm, SMMU0, 0, 0, 0);
    smmu_regs(&w->dm, nodes[1], 0, 0, 0);
    tests_run_smmu(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_UNKNOWN);
    CHECK_EQ(w->dm.nlog, 8);
    CHECK_EQ(stray_reads(&w->dm, wins, 1), 0);
    CHECK(has_row(&r, "SMMU1 REFUSED: MAP SAYS NOT MMIO"));
    CHECK(has_row(&r, "MAP MMIO=0 UNLISTED=1 REFUSED=1"));
    w_free(w);

    /* Strict policy. */
    iort_world(w, 1);
    w->ctx.require_mmio_map = 1;
    smmu_regs(&w->dm, SMMU0, 0, 0, 0);
    tests_run_smmu(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_UNKNOWN);
    CHECK_EQ(w->dm.nlog, 0);
    CHECK(has_row(&r, "SMMU0 REFUSED: NOT MMIO IN MAP"));
    w_map(w, 11, SMMU0, 1);
    tests_run_smmu(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_INFO);
    CHECK_EQ(w->dm.nlog, 8);
    CHECK(has_row(&r, "MAP STRICT MMIO=1 REFUSED=0"));
    w_free(w);

    /* No map. */
    iort_world(w, 1);
    w->ctx.memmap = NULL;
    smmu_regs(&w->dm, SMMU0, 0, 0, 0);
    tests_run_smmu(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_UNKNOWN);
    CHECK_EQ(w->dm.nlog, 0);
    CHECK(has_row(&r, "SMMU0 REFUSED: NO MEMORY MAP"));
    w_free(w);
}

void test_x_windows(void)
{
    world *w = xalloc(sizeof *w);

    section("device windows");
    w_init(w);
    windows_numbers();
    windows_policy(w);
    w_free(w);
    windows_parsers();
    windows_gic(w);
    windows_pcie(w);
    windows_smmu(w);
    free(w);
}
