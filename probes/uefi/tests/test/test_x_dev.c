/* SPDX-License-Identifier: MIT */
/* Host tests of the device-facing tests (T03 to T09) and of the ACPI parsers
 * they take their addresses from. A device model backs the guarded bus: it
 * records every address that is read, faults on anything it was not told
 * about, and the tests then check that no read left the windows the ACPI
 * tables justified. There is no write operation on the bus at all, so
 * read-only is a property of the interface, and the model checks it again by
 * having nowhere to put a write.
 */
#include "xdev.h"

/* ---- Table builders ---- */

static void t_u8(tbl *t, unsigned v) { t->b[t->len++] = (uint8_t)v; }
static void t_u16(tbl *t, unsigned v) { t_u8(t, v & 0xFF); t_u8(t, v >> 8); }
static void t_u32(tbl *t, uint32_t v) { t_u16(t, v & 0xFFFF); t_u16(t, v >> 16); }
static void t_u64(tbl *t, uint64_t v) { t_u32(t, (uint32_t)v); t_u32(t, (uint32_t)(v >> 32)); }
static void t_fill(tbl *t, size_t n) { while (n--) t_u8(t, 0); }

void t_start(tbl *t, const char *sig, size_t extra)
{
    memset(t, 0, sizeof *t);
    memcpy(t->b, sig, 4);
    t->b[8] = 1;
    memcpy(t->b + 10, "SNAPTS", 6);
    t->len = 36;
    t_fill(t, extra);
}

/* Length and checksum, as a firmware table carries them. */
void t_seal(tbl *t)
{
    uint8_t sum = 0;

    st32(t->b + 4, (uint32_t)t->len);
    t->b[9] = 0;
    for (size_t i = 0; i < t->len; i++)
        sum = (uint8_t)(sum + t->b[i]);
    t->b[9] = (uint8_t)(0u - sum);
}

void madt_gicc(tbl *t, uint32_t flags, uint64_t gicr, uint64_t mpidr)
{
    size_t at = t->len;

    t_u8(t, 0x0B);
    t_u8(t, 80);
    t_fill(t, 78);
    st32(t->b + at + 12, flags);
    st64(t->b + at + 60, gicr);
    st64(t->b + at + 68, mpidr);
}

void madt_gicd(tbl *t, uint64_t base, unsigned version)
{
    size_t at = t->len;

    t_u8(t, 0x0C);
    t_u8(t, 24);
    t_fill(t, 22);
    st64(t->b + at + 8, base);
    t->b[at + 20] = (uint8_t)version;
}

void madt_gicr(tbl *t, uint64_t base, uint32_t len)
{
    size_t at = t->len;

    t_u8(t, 0x0E);
    t_u8(t, 16);
    t_fill(t, 14);
    st64(t->b + at + 4, base);
    st32(t->b + at + 12, len);
}

void madt_its(tbl *t, uint64_t base)
{
    size_t at = t->len;

    t_u8(t, 0x0F);
    t_u8(t, 20);
    t_fill(t, 18);
    st64(t->b + at + 8, base);
}

void mcfg_entry(tbl *t, uint64_t base, unsigned seg, unsigned start, unsigned end)
{
    t_u64(t, base);
    t_u16(t, seg);
    t_u8(t, start);
    t_u8(t, end);
    t_u32(t, 0);
}

void iort_node(tbl *t, unsigned type, size_t len, uint64_t base)
{
    size_t at = t->len;

    t_u8(t, type);
    t_u16(t, (unsigned)len);
    t_fill(t, len - 3);
    if (len >= 24)
        st64(t->b + at + 16, base);
}

/* ---- ACPI parsers ---- */

tbl good_madt(void)
{
    tbl t;

    t_start(&t, "APIC", 8);
    madt_gicc(&t, 1, 0x11000000ull, 0x0);
    madt_gicc(&t, 1, 0x11020000ull, 0x1);
    madt_gicc(&t, 0, 0, 0x2); /* disabled: counted as seen, not kept */
    madt_gicd(&t, 0x10000000ull, 3);
    madt_gicr(&t, 0x11000000ull, 0x40000);
    madt_its(&t, 0x10080000ull);
    t_seal(&t);
    return t;
}

void test_x_acpi(void)
{
    tbl t;
    tests_madt madt;
    tests_mcfg mcfg;
    tests_iort iort;
    uint8_t *x;

    section("acpi tables");
    t = good_madt();
    x = exact_copy(t.b, t.len);
    CHECK_EQ(tests_madt_parse(x, t.len, &madt), 0);
    CHECK(madt.valid && madt.have_gicd);
    CHECK_EQU(madt.gicd_base, 0x10000000ull);
    CHECK_EQ(madt.gicd_version, 3);
    CHECK_EQ(madt.n_gicr, 1);
    CHECK_EQU(madt.gicr[0].base, 0x11000000ull);
    CHECK_EQU(madt.gicr[0].len, 0x40000);
    CHECK_EQ(madt.n_its, 1);
    CHECK_EQ(madt.n_cpus, 2);
    CHECK_EQ(madt.n_gicc_seen, 3);
    CHECK_EQU(madt.mpidr[1], 1);
    CHECK_EQU(madt.gicc_gicr[1], 0x11020000ull);
    /* Every strict prefix and every one-byte-longer table is refused (the
     * length field no longer matches), and none reads past its buffer. */
    for (size_t cut = 0; cut < t.len; cut++) {
        uint8_t *y = exact_copy(t.b, cut);

        CHECK_EQ(tests_madt_parse(y, cut, &madt), -1);
        CHECK(!madt.valid);
        free(y);
    }
    free(x);

    /* Hostile entries. */
    {
        static const struct {
            unsigned type, len;
            const char *why;
        } bad[] = {
            {0x0B, 79, "GICC shorter than its minimum"}, {0x0C, 23, "GICD shorter than its minimum"},
            {0x0E, 15, "GICR shorter than its minimum"}, {0x0F, 19, "ITS shorter than its minimum"},
            {0x0B, 0, "zero-length entry"},             {0x0B, 1, "one-byte entry"},
            {0x20, 0, "zero-length unknown entry"},     {0x0B, 255, "entry beyond the end"},
        };

        for (unsigned i = 0; i < sizeof bad / sizeof bad[0]; i++) {
            t_start(&t, "APIC", 8);
            t_u8(&t, bad[i].type);
            t_u8(&t, bad[i].len);
            t_fill(&t, bad[i].len > 2 && bad[i].len < 100 ? bad[i].len - 2 : 4);
            t_seal(&t);
            x = exact_copy(t.b, t.len);
            if (tests_madt_parse(x, t.len, &madt) != -1)
                printf("  accepted: %s\n", bad[i].why);
            CHECK_EQ(tests_madt_parse(x, t.len, &madt), -1);
            free(x);
        }
    }
    /* Wrong signature, a length field that disagrees, trailing bytes. */
    t = good_madt();
    memcpy(t.b, "MADT", 4);
    CHECK_EQ(tests_madt_parse(t.b, t.len, &madt), -1);
    t = good_madt();
    st32(t.b + 4, (uint32_t)t.len + 1);
    CHECK_EQ(tests_madt_parse(t.b, t.len, &madt), -1);
    t = good_madt();
    t_u8(&t, 0);
    t_seal(&t);
    CHECK_EQ(tests_madt_parse(t.b, t.len, &madt), -1); /* one stray byte after the last entry */
    /* Duplicate GICD is flagged, and overflowing arrays are counted, not written past. */
    t_start(&t, "APIC", 8);
    madt_gicd(&t, 0x1000, 3);
    madt_gicd(&t, 0x2000, 3);
    for (unsigned i = 0; i < TESTS_MAX_CPUS + 3; i++)
        madt_gicc(&t, 1, 0, i);
    for (unsigned i = 0; i < TESTS_MAX_GICR + 2; i++)
        madt_gicr(&t, 0x100000ull * (i + 1), 0x20000);
    for (unsigned i = 0; i < TESTS_MAX_ITS + 2; i++)
        madt_its(&t, 0x200000ull * (i + 1));
    if (t.len > sizeof t.b - 100)
        t.len = 0;
    t_seal(&t);
    x = exact_copy(t.b, t.len);
    CHECK_EQ(tests_madt_parse(x, t.len, &madt), 0);
    CHECK_EQ(madt.duplicate_gicd, 1);
    CHECK_EQ(madt.n_cpus, TESTS_MAX_CPUS);
    CHECK_EQ(madt.n_gicr, TESTS_MAX_GICR);
    CHECK_EQ(madt.n_its, TESTS_MAX_ITS);
    CHECK_EQ(madt.truncated, 3 + 2 + 2);
    free(x);

    /* MCFG */
    t_start(&t, "MCFG", 8);
    mcfg_entry(&t, 0x60000000ull, 0, 0, 15);
    mcfg_entry(&t, 0x70000000ull, 1, 0, 255);
    t_seal(&t);
    x = exact_copy(t.b, t.len);
    CHECK_EQ(tests_mcfg_parse(x, t.len, &mcfg), 0);
    CHECK_EQ(mcfg.n, 2);
    CHECK_EQU(mcfg.e[1].base, 0x70000000ull);
    CHECK_EQ(mcfg.e[1].end_bus, 255);
    CHECK_EQ(mcfg.e[1].segment, 1);
    free(x);
    for (size_t cut = 0; cut < t.len; cut++) {
        uint8_t *y = exact_copy(t.b, cut);

        CHECK_EQ(tests_mcfg_parse(y, cut, &mcfg), -1);
        free(y);
    }
    t_start(&t, "MCFG", 8);
    mcfg_entry(&t, 0x60000000ull, 0, 5, 4); /* end bus before start bus */
    t_seal(&t);
    CHECK_EQ(tests_mcfg_parse(t.b, t.len, &mcfg), -1);
    t_start(&t, "MCFG", 8);
    mcfg_entry(&t, 0x60000000ull, 0, 0, 1);
    t_u8(&t, 0); /* not a whole number of entries */
    t_seal(&t);
    CHECK_EQ(tests_mcfg_parse(t.b, t.len, &mcfg), -1);
    t_start(&t, "MCFG", 8);
    for (unsigned i = 0; i < TESTS_MAX_ECAM + 2; i++)
        mcfg_entry(&t, 0x1000000ull * (i + 1), 0, 0, 1);
    t_seal(&t);
    CHECK_EQ(tests_mcfg_parse(t.b, t.len, &mcfg), 0);
    CHECK_EQ(mcfg.n, TESTS_MAX_ECAM);
    CHECK_EQ(mcfg.truncated, 2);

    /* IORT */
    t_start(&t, "IORT", 12);
    st32(t.b + 36, 3);  /* node count */
    st32(t.b + 40, 48); /* offset of the first node */
    iort_node(&t, 0, 24, 0);
    iort_node(&t, 4, 64, 0x6000000ull);
    iort_node(&t, 4, 40, 0x7000000ull);
    t_seal(&t);
    x = exact_copy(t.b, t.len);
    CHECK_EQ(tests_iort_parse(x, t.len, &iort), 0);
    CHECK_EQ(iort.n, 2);
    CHECK_EQU(iort.base[0], 0x6000000ull);
    CHECK_EQU(iort.base[1], 0x7000000ull);
    free(x);
    for (size_t cut = 0; cut < t.len; cut++) {
        uint8_t *y = exact_copy(t.b, cut);

        CHECK_EQ(tests_iort_parse(y, cut, &iort), -1);
        free(y);
    }
    /* Hostile IORT: a node that runs past the table, a node shorter than its
     * header, an SMMUv3 node too short to hold its base, a node offset outside
     * the table, an absurd node count. */
    for (int bad = 0; bad < 5; bad++) {
        t_start(&t, "IORT", 12);
        st32(t.b + 36, bad == 4 ? 100000 : 1);
        st32(t.b + 40, bad == 3 ? 4000 : 48);
        if (bad == 0)
            iort_node(&t, 4, 64, 1), t.len -= 30;
        else if (bad == 1)
            iort_node(&t, 0, 24, 0), st16(t.b + 49, 8);
        else if (bad == 2)
            iort_node(&t, 4, 20, 0);
        else
            iort_node(&t, 0, 24, 0);
        t_seal(&t);
        x = exact_copy(t.b, t.len);
        CHECK_EQ(tests_iort_parse(x, t.len, &iort), -1);
        free(x);
    }
}

/* ---- The device model ---- */

void dm_set(devmodel *d, uint64_t addr, uint32_t val)
{
    if (d->nreg < MAX_REGS) {
        d->reg[d->nreg].addr = addr;
        d->reg[d->nreg].val = val;
        d->nreg++;
    }
}

static int dm_rd32(void *ctx, uint64_t addr, uint32_t *v)
{
    devmodel *d = ctx;

    if (d->nlog < MAX_LOG)
        d->log[d->nlog++] = addr;
    *v = 0xFFFFFFFFu;
    if (d->fault_all) {
        d->faults++;
        return -1;
    }
    for (unsigned i = 0; i < d->nfault_at; i++)
        if (d->fault_at[i] == addr) {
            d->faults++;
            return -1;
        }
    for (unsigned i = d->nreg; i-- > 0;)
        if (d->reg[i].addr == addr) {
            *v = d->reg[i].val;
            return 0;
        }
    d->unlisted++;
    d->faults++;
    return -1;
}

static int dm_rd_sys(void *ctx, int id, uint64_t *v)
{
    devmodel *d = ctx;

    *v = ~0ull;
    if (id < 0 || id >= TR_COUNT || d->sys_fault[id])
        return -1;
    if (id == TR_CNTPCT_EL0) {
        if (!d->counter_stuck)
            d->pct++;
        *v = d->pct;
    } else if (id == TR_CNTVCT_EL0) {
        *v = d->pct - d->vct_offset;
    } else {
        *v = d->sys[id];
    }
    return 0;
}

static unsigned dm_faults(void *ctx)
{
    return ((devmodel *)ctx)->faults;
}

unsigned stray_reads(const devmodel *d, const win *w, unsigned n)
{
    unsigned stray = 0;

    for (unsigned i = 0; i < d->nlog; i++) {
        int inside = 0;

        for (unsigned k = 0; k < n; k++)
            if (d->log[i] >= w[k].base && d->log[i] + 4 <= w[k].base + w[k].len)
                inside = 1;
        stray += !inside;
    }
    return stray;
}

int read_at(const devmodel *d, uint64_t addr)
{
    for (unsigned i = 0; i < d->nlog; i++)
        if (d->log[i] == addr)
            return 1;
    return 0;
}

static int fw_read(void *ctx, uint64_t addr, void *dst, size_t len)
{
    fwmem *f = ctx;

    for (unsigned i = 0; i < f->n; i++)
        if (addr >= f->t[i].addr && addr + len <= f->t[i].addr + f->t[i].len) {
            memcpy(dst, f->t[i].data + (addr - f->t[i].addr), len);
            return 0;
        }
    return -1;
}

static int fw_covers(void *ctx, uint64_t addr, uint64_t len)
{
    fwmem *f = ctx;

    for (unsigned i = 0; i < f->n; i++)
        if (addr >= f->t[i].addr && addr + len <= f->t[i].addr + f->t[i].len)
            return 1;
    return 0;
}

/* One EFI memory descriptor with a 48-byte stride, as firmware lays them out. */
unsigned md_add(uint8_t *map, unsigned n, uint32_t type, uint64_t phys, uint64_t pages)
{
    uint8_t *p = map + 48u * n;

    st32(p, type);
    st64(p + 8, phys);
    st64(p + 16, phys);
    st64(p + 24, pages);
    st64(p + 32, 0xF);
    return n + 1;
}

/* Adds a descriptor to the world's UEFI memory map and re-validates the map. */
void w_map(world *w, uint32_t type, uint64_t phys, uint64_t pages)
{
    w->nmm = md_add(w->mmbuf, w->nmm, type, phys, pages);
    CHECK_EQ(probe_memmap_init(&w->mm, w->mmbuf, w->nmm * 48u, sizeof w->mmbuf, 48), 0);
}

/* The default memory map describes RAM only: device registers are not listed,
 * as on the QEMU machine, so a window is "unlisted" unless a test says more. */
void w_init(world *w)
{
    memset(w, 0, sizeof *w);
    w->phys.read = fw_read;
    w->phys.covers = fw_covers;
    w->bus.ctx = &w->dm;
    w->bus.rd32 = dm_rd32;
    w->bus.rd_sys = dm_rd_sys;
    w->bus.faults = dm_faults;
    w->acpi_buf = xalloc(TESTS_ACPI_BUF_BYTES);
    w->ctx.bus = &w->bus;
    w->ctx.scan = &w->scan;
    w->ctx.phys = &w->phys;
    w->ctx.phys_ctx = &w->fw;
    w->ctx.desc = &w->desc;
    w->ctx.memmap = &w->mm;
    w->ctx.acpi_buf = w->acpi_buf;
    w->scan.rsdp_status = PROBE_ACPI_OK;
    x_good_ids(&w->ctx.ids);
    w_map(w, 7, W_RAM_BASE, W_RAM_PAGES);
}

void w_free(world *w)
{
    free(w->acpi_buf);
}

/* Publishes table k of the world under its signature at a fake address. */
void w_table(world *w, unsigned k, const char *sig, const tbl *t)
{
    probe_acpi_entry *e = &w->scan.entries[w->scan.count++];

    w->tables[k] = *t;
    w->fw.t[w->fw.n].addr = 0x80000000ull + 0x10000ull * k;
    w->fw.t[w->fw.n].data = w->tables[k].b;
    w->fw.t[w->fw.n].len = t->len;
    e->addr = w->fw.t[w->fw.n].addr;
    e->length = (uint32_t)t->len;
    e->status = PROBE_ACPI_OK;
    memcpy(e->sig, sig, 4);
    e->sig[4] = 0;
    w->fw.n++;
}

/* ---- T05: the GIC against the MADT ---- */

void gic_good_regs(devmodel *d, unsigned arch, int last_on_second)
{
    dm_set(d, GICD + 0x0000, 0x00000050);
    dm_set(d, GICD + 0x0004, 0x00FF0408);
    dm_set(d, GICD + 0x0008, 0x0200143B);
    dm_set(d, GICD + 0xFFE8, arch << 4);
    for (unsigned f = 0; f < 2; f++) {
        uint64_t fr = GICR + 0x20000ull * f;

        dm_set(d, fr + 0xFFE8, arch << 4);
        dm_set(d, fr + 0x0008, f == 1 && last_on_second ? 0x10 : 0);
        dm_set(d, fr + 0x000C, f);
        dm_set(d, fr + 0x0014, 0x6);
    }
    dm_set(d, ITS + 0x0000, 1);
    dm_set(d, ITS + 0x0008, 0x0000C781);
    dm_set(d, ITS + 0xFFE8, arch << 4);
}

void gic_setup(world *w, tbl *madt)
{
    w_table(w, 0, "APIC", madt);
}

void test_x_gic(void)
{
    world *w = xalloc(sizeof *w);
    tests_result r;
    tbl t;
    win wins[3] = {{GICD, 0x10000}, {GICR, 0x40000}, {ITS, 0x10000}};

    section("gic against madt");
    w_init(w);
    t = good_madt();
    gic_setup(w, &t);
    gic_good_regs(&w->dm, 3, 1);
    tests_run_gic(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_PASS);
    CHECK_EQ(r.label, TL_OBSERVED);
    CHECK_EQ(r.overflow, 0);
    CHECK_EQ(w->dm.unlisted, 0);
    CHECK_EQ(stray_reads(&w->dm, wins, 3), 0);
    CHECK(x_rows_valid(&r));
    CHECK(read_at(&w->dm, GICD));
    CHECK(read_at(&w->dm, ITS));
    w_free(w);

    /* The MADT states version 3, the hardware says 4. */
    w_init(w);
    t = good_madt();
    gic_setup(w, &t);
    gic_good_regs(&w->dm, 4, 1);
    tests_run_gic(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_FAIL);
    w_free(w);

    /* A CPU of the MADT has no redistributor frame. */
    w_init(w);
    t = good_madt();
    gic_setup(w, &t);
    gic_good_regs(&w->dm, 3, 1);
    dm_set(&w->dm, GICR + 0x20000 + 0x000C, 9); /* frame 1 belongs to another affinity */
    tests_run_gic(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_FAIL);
    w_free(w);

    /* No frame marked Last: the walk ends with the range, and that is not a PASS. */
    w_init(w);
    t = good_madt();
    gic_setup(w, &t);
    gic_good_regs(&w->dm, 3, 0);
    tests_run_gic(&w->ctx, &r);
    CHECK(r.verdict != TV_PASS);
    CHECK_EQ(stray_reads(&w->dm, wins, 3), 0);
    w_free(w);

    /* A frame whose PIDR2 is not a GICv3/v4: the walk stops, never PASS. */
    w_init(w);
    t = good_madt();
    gic_setup(w, &t);
    gic_good_regs(&w->dm, 3, 1);
    dm_set(&w->dm, GICR + 0xFFE8, 0x20);
    tests_run_gic(&w->ctx, &r);
    CHECK(r.verdict != TV_PASS);
    w_free(w);

    /* The distributor faults: nothing else is read, UNKNOWN, fault counted. */
    w_init(w);
    t = good_madt();
    gic_setup(w, &t);
    gic_good_regs(&w->dm, 3, 1);
    w->dm.fault_at[w->dm.nfault_at++] = GICD + 0x0004;
    tests_run_gic(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_UNKNOWN);
    CHECK(!read_at(&w->dm, GICR + 0xFFE8));
    CHECK(w->dm.faults >= 1);
    w_free(w);

    /* No MADT, a duplicate GICD, a missing GICD: not one device read. */
    w_init(w);
    tests_run_gic(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_UNKNOWN);
    CHECK_EQ(w->dm.nlog, 0);
    w_free(w);
    w_init(w);
    t_start(&t, "APIC", 8);
    madt_gicd(&t, GICD, 3);
    madt_gicd(&t, GICD + 0x1000000, 3);
    t_seal(&t);
    gic_setup(w, &t);
    tests_run_gic(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_UNKNOWN);
    CHECK_EQ(w->dm.nlog, 0);
    w_free(w);
    w_init(w);
    t_start(&t, "APIC", 8);
    madt_gicr(&t, GICR, 0x40000);
    t_seal(&t);
    gic_setup(w, &t);
    tests_run_gic(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_UNKNOWN);
    CHECK_EQ(w->dm.nlog, 0);
    w_free(w);

    /* A redistributor range that claims to be enormous: the walk is bounded and
     * stays inside the range the table gave. */
    w_init(w);
    t_start(&t, "APIC", 8);
    madt_gicd(&t, GICD, 3);
    madt_gicr(&t, GICR, 0xF0000000u);
    t_seal(&t);
    gic_setup(w, &t);
    for (unsigned f = 0; f < 200; f++) {
        dm_set(&w->dm, GICR + 0x20000ull * f + 0xFFE8, 0x30);
        dm_set(&w->dm, GICR + 0x20000ull * f + 0x0008, 0);
        dm_set(&w->dm, GICR + 0x20000ull * f + 0x000C, f);
        dm_set(&w->dm, GICR + 0x20000ull * f + 0x0014, 0x6);
    }
    gic_good_regs(&w->dm, 3, 0);
    tests_run_gic(&w->ctx, &r);
    CHECK(w->dm.nlog < 64 * 4 + 16);
    CHECK(r.verdict != TV_PASS);
    w_free(w);

    /* The descriptor is a cross-check only: its (wrong) addresses are never read,
     * and a disagreement shows in a row without changing the verdict. */
    w_init(w);
    t = good_madt();
    gic_setup(w, &t);
    gic_good_regs(&w->dm, 3, 1);
    w->desc.gicd_base = 0x20000000ull;
    w->desc.gicr_base = 0x21000000ull;
    w->desc.its_base = 0x22000000ull;
    w->desc.n_cpus = 2;
    tests_run_gic(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_PASS);
    CHECK(!read_at(&w->dm, 0x20000000ull) && !read_at(&w->dm, 0x21000000ull + 0xFFE8));
    CHECK_EQ(stray_reads(&w->dm, wins, 3), 0);
    {
        int diff_row = 0;

        for (unsigned i = 0; i < r.nrows; i++)
            diff_row |= strstr(r.rows[i], "D=DIFF") != NULL;
        CHECK(diff_row);
    }
    w_free(w);
    free(w);
}

/* ---- T06: PCIe and NVMe ---- */

uint64_t ecam_at(unsigned bus, unsigned dev, unsigned off)
{
    return ECAM + ((uint64_t)bus << 20) + ((uint64_t)dev << 15) + off;
}

static void pcie_regs(devmodel *d, const pcie_cfg *p)
{
    /* The root port: vendor/device, command/status with a capability list,
     * class 0604, header type, secondary bus 1, windows, capability chain with
     * a PCIe capability whose link status says Gen3 x4, link up. */
    dm_set(d, ecam_at(0, 0, 0x00), 0x01201234);
    dm_set(d, ecam_at(0, 0, 0x04), (0x0010u << 16) | p->bridge_cmd);
    dm_set(d, ecam_at(0, 0, 0x08), 0x06040001);
    dm_set(d, ecam_at(0, 0, 0x0C), (uint32_t)p->bridge_type << 16);
    dm_set(d, ecam_at(0, 0, 0x18), 0x00010100);
    dm_set(d, ecam_at(0, 0, 0x20), p->memw);
    dm_set(d, ecam_at(0, 0, 0x24), 0x0000FFF0);
    dm_set(d, ecam_at(0, 0, 0x28), 0);
    dm_set(d, ecam_at(0, 0, 0x2C), 0);
    dm_set(d, ecam_at(0, 0, 0x34), 0x40);
    dm_set(d, ecam_at(0, 0, 0x40), 0x00005010); /* PCIe capability, no next */
    dm_set(d, ecam_at(0, 0, 0x50), (0x2043u << 16) | 0x0040); /* link status: gen3 x4, DLLLA */
    /* The endpoint behind it. */
    dm_set(d, ecam_at(1, 0, 0x00), 0x00101B36);
    dm_set(d, ecam_at(1, 0, 0x04), p->ep_cmd);
    dm_set(d, ecam_at(1, 0, 0x08), 0x01080201);
    dm_set(d, ecam_at(1, 0, 0x10), p->bar0);
    dm_set(d, ecam_at(1, 0, 0x14), 0);
    /* The NVMe registers. */
    dm_set(d, NVME_BAR + 0x00, 0x0107FFFF);
    dm_set(d, NVME_BAR + 0x04, 0x00400820);
    dm_set(d, NVME_BAR + 0x08, 0x00010400);
    dm_set(d, NVME_BAR + 0x14, p->cc);
    dm_set(d, NVME_BAR + 0x1C, p->csts);
}

pcie_cfg good_pcie(void)
{
    pcie_cfg p = {1, 0x0006, 0x0006, 0x7000 | (0x7000u << 16) , 0x70000004u, 0x00460001, 0x1};

    /* Bridge memory window 0x70000000..0x700FFFFF; BAR0 is a 64-bit memory BAR. */
    return p;
}

void pcie_world(world *w, const pcie_cfg *p, unsigned start_bus)
{
    tbl t;

    w_init(w);
    t_start(&t, "MCFG", 8);
    mcfg_entry(&t, ECAM, 0, start_bus, 3);
    t_seal(&t);
    w_table(w, 1, "MCFG", &t);
    pcie_regs(&w->dm, p);
}

void test_x_pcie(void)
{
    world *w = xalloc(sizeof *w);
    tests_result r;
    pcie_cfg p;
    win wins[2] = {{ECAM, 4u << 20}, {NVME_BAR, 0x1000}};
    int cap_row;

    section("pcie and nvme");
    p = good_pcie();
    pcie_world(w, &p, 0);
    tests_run_pcie(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_INFO);
    CHECK_EQ(r.overflow, 0);
    CHECK_EQ(stray_reads(&w->dm, wins, 2), 0);
    CHECK_EQ(w->dm.unlisted, 0);
    CHECK(x_rows_valid(&r));
    CHECK(read_at(&w->dm, NVME_BAR + 0x14)); /* CC and CSTS are read ... */
    CHECK(read_at(&w->dm, NVME_BAR + 0x1C));
    CHECK(!read_at(&w->dm, NVME_BAR + 0x18)); /* ... and nothing else of the controller */
    CHECK(!read_at(&w->dm, NVME_BAR + 0x24));
    CHECK(!read_at(&w->dm, NVME_BAR + 0x1000));
    CHECK(!read_at(&w->dm, ecam_at(0, 1, 0))); /* a root port: device 1 of the root bus is not read */
    cap_row = 0;
    for (unsigned i = 0; i < r.nrows; i++)
        cap_row |= strstr(r.rows[i], "NVME CAP=") != NULL;
    CHECK(cap_row);
    {
        int link = 0;

        for (unsigned i = 0; i < r.nrows; i++)
            link |= strstr(r.rows[i], "LINK GEN3 X4 DLLLA=1") != NULL;
        CHECK(link);
    }
    w_free(w);

    /* Each justification removed in turn: the registers behind the BAR are not read. */
    for (int variant = 0; variant < 6; variant++) {
        p = good_pcie();
        if (variant == 0)
            p.ep_cmd = 0x0004; /* endpoint memory decode off */
        else if (variant == 1)
            p.bridge_cmd = 0x0004; /* the bridge does not forward memory */
        else if (variant == 2)
            p.memw = 0x0000FFF0; /* the bridge window is empty/elsewhere */
        else if (variant == 3)
            p.bar0 = 0; /* unassigned BAR */
        else if (variant == 4)
            p.bar0 = 0x70000104u; /* not 4 KiB aligned */
        else
            p.bar0 = 0x71000004u; /* outside the bridge's window */
        pcie_world(w, &p, 0);
        tests_run_pcie(&w->ctx, &r);
        if (read_at(&w->dm, NVME_BAR + 0x14) || read_at(&w->dm, NVME_BAR))
            printf("  pcie variant %d read the controller\n", variant);
        CHECK(!read_at(&w->dm, NVME_BAR + 0x14));
        CHECK(!read_at(&w->dm, NVME_BAR + 0x1C));
        CHECK(!read_at(&w->dm, NVME_BAR));
        CHECK_EQ(stray_reads(&w->dm, wins, 2), 0);
        CHECK(r.verdict != TV_PASS);
        w_free(w);
    }

    /* A configuration read that faults: UNKNOWN, the controller is not read. */
    p = good_pcie();
    pcie_world(w, &p, 0);
    w->dm.fault_at[w->dm.nfault_at++] = ecam_at(1, 0, 0x08);
    tests_run_pcie(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_UNKNOWN);
    CHECK(!read_at(&w->dm, NVME_BAR + 0x14));
    w_free(w);
    /* The NVMe register read faults. */
    pcie_world(w, &p, 0);
    w->dm.fault_at[w->dm.nfault_at++] = NVME_BAR + 0x1C;
    tests_run_pcie(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_UNKNOWN);
    CHECK(w->dm.faults >= 1);
    w_free(w);
    /* Everything faults: nothing is learnt, and the verdict says so. */
    pcie_world(w, &p, 0);
    w->dm.fault_all = 1;
    tests_run_pcie(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_UNKNOWN);
    w_free(w);

    /* An ECAM window that does not start at bus 0 is not read at all. */
    pcie_world(w, &p, 1);
    tests_run_pcie(&w->ctx, &r);
    CHECK_EQ(w->dm.nlog, 0);
    CHECK_EQ(r.verdict, TV_UNKNOWN);
    w_free(w);
    /* No MCFG. */
    w_init(w);
    tests_run_pcie(&w->ctx, &r);
    CHECK_EQ(w->dm.nlog, 0);
    CHECK_EQ(r.verdict, TV_UNKNOWN);
    w_free(w);

    /* A generic host bridge (a type 0 device at 00:00.0): other root-bus devices
     * are read too, nothing beyond device 7, nothing outside the ECAM window. */
    p = good_pcie();
    pcie_world(w, &p, 0);
    dm_set(&w->dm, ecam_at(0, 0, 0x0C), 0); /* header type 0 */
    dm_set(&w->dm, ecam_at(0, 0, 0x08), 0x06000000);
    dm_set(&w->dm, ecam_at(0, 3, 0x00), 0xFFFFFFFF);
    tests_run_pcie(&w->ctx, &r);
    CHECK(read_at(&w->dm, ecam_at(0, 3, 0)));
    CHECK(!read_at(&w->dm, ecam_at(0, 8, 0)));
    CHECK_EQ(stray_reads(&w->dm, wins, 2), 0);
    w_free(w);
    free(w);
}

/* ---- T07 SMMU, T03 timer, T04 DC ZVA, T08 ADSP, T09 DRAM ---- */

void smmu_regs(devmodel *d, uint64_t base, uint32_t cr0, uint32_t ack, uint32_t gbpa)
{
    dm_set(d, base + 0x00, 0x0C0A0F4D);
    dm_set(d, base + 0x04, 0x0E739FE4);
    dm_set(d, base + 0x14, 0x00000F8B);
    dm_set(d, base + 0x20, cr0);
    dm_set(d, base + 0x24, ack);
    dm_set(d, base + 0x44, gbpa);
    dm_set(d, base + 0x60, 0);
    dm_set(d, base + 0x88, 0);
}

int has_row(const tests_result *r, const char *needle)
{
    for (unsigned i = 0; i < r->nrows; i++)
        if (strstr(r->rows[i], needle) != NULL)
            return 1;
    return 0;
}

void iort_world(world *w, unsigned n)
{
    tbl t;

    w_init(w);
    t_start(&t, "IORT", 12);
    st32(t.b + 36, n);
    st32(t.b + 40, 48);
    for (unsigned i = 0; i < n; i++)
        iort_node(&t, 4, 64, i == 0 ? SMMU0 : SMMU1);
    t_seal(&t);
    w_table(w, 2, "IORT", &t);
}

static const uint64_t smmu_offsets[] = {0x00, 0x04, 0x14, 0x20, 0x24, 0x44, 0x60, 0x88};

typedef struct {
    int mode; /* 0 zeroes the block, 1 the whole buffer, 2 nothing, 3 faults */
    unsigned calls;
    unsigned block;
    uint8_t *buf;
    uint8_t *last_addr;
} zvamodel;

static int zm_zva(void *ctx, uint8_t *addr)
{
    zvamodel *m = ctx;

    m->calls++;
    m->last_addr = addr;
    if (m->mode == 3)
        return -1;
    if (m->mode == 1)
        memset(m->buf, 0, 3u * m->block);
    else if (m->mode == 0)
        memset(addr, 0, m->block);
    return 0;
}

static void zm_sync(void *ctx)
{
    (void)ctx;
}

void test_x_smmu_misc(void)
{
    world *w = xalloc(sizeof *w);
    tests_result r;
    win wins[2] = {{SMMU0, 0x100}, {SMMU1, 0x100}};
    tests_zva_ops zops;
    static uint8_t zbuf[TESTS_ZVA_BUFFER_BYTES] __attribute__((aligned(2048)));

    section("smmu timer zva misc");
    /* T07: bypass, translating and the transient state. */
    iort_world(w, 1);
    smmu_regs(&w->dm, SMMU0, 0, 0, 0);
    tests_run_smmu(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_INFO);
    CHECK(has_row(&r, "DISABLED, GBPA BYPASS"));
    CHECK(x_rows_valid(&r));
    CHECK_EQ(stray_reads(&w->dm, wins, 2), 0);
    CHECK_EQ(w->dm.nlog, 8);
    for (unsigned i = 0; i < 8; i++)
        CHECK(read_at(&w->dm, SMMU0 + smmu_offsets[i]));
    w_free(w);
    iort_world(w, 1);
    smmu_regs(&w->dm, SMMU0, 1, 1, 0);
    tests_run_smmu(&w->ctx, &r);
    CHECK(has_row(&r, "SMMUEN SET"));
    w_free(w);
    iort_world(w, 1);
    smmu_regs(&w->dm, SMMU0, 1, 0, 0);
    tests_run_smmu(&w->ctx, &r);
    CHECK(has_row(&r, "CR0 AND CR0ACK DISAGREE"));
    w_free(w);
    iort_world(w, 1);
    smmu_regs(&w->dm, SMMU0, 0, 0, 1u << 20);
    tests_run_smmu(&w->ctx, &r);
    CHECK(has_row(&r, "GBPA ABORT"));
    w_free(w);
    /* Two nodes, one faulting: UNKNOWN, and the second is still bounded to its window. */
    iort_world(w, 2);
    smmu_regs(&w->dm, SMMU0, 0, 0, 0);
    smmu_regs(&w->dm, SMMU1, 0, 0, 0);
    w->dm.fault_at[w->dm.nfault_at++] = SMMU1 + 0x24;
    tests_run_smmu(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_UNKNOWN);
    CHECK_EQ(stray_reads(&w->dm, wins, 2), 0);
    w_free(w);
    /* No SMMUv3 node: nothing read, the descriptor's candidate is only mentioned. */
    w_init(w);
    w->desc.pcie_smmu_base = 0x15200000ull;
    tests_run_smmu(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_UNKNOWN);
    CHECK_EQ(w->dm.nlog, 0);
    CHECK(has_row(&r, "DESC HAS ONE: INFERRED, NOT USED"));
    w_free(w);
    /* A descriptor that disagrees with the IORT is never read. */
    iort_world(w, 1);
    smmu_regs(&w->dm, SMMU0, 0, 0, 0);
    w->desc.pcie_smmu_base = 0x15200000ull;
    tests_run_smmu(&w->ctx, &r);
    CHECK(has_row(&r, "DESC PCIE SMMU: DIFF"));
    CHECK(!read_at(&w->dm, 0x15200000ull));
    w_free(w);

    /* T03: the timer. */
    w_init(w);
    w->dm.sys[TR_CNTFRQ_EL0] = 1000000000ull;
    w->dm.sys[TR_CNTHCTL_EL2] = 0x3;
    w->dm.sys[TR_CNTVOFF_EL2] = 0;
    w->dm.sys[TR_CNTPOFF_EL2] = 0;
    w->dm.pct = 1000;
    tests_run_timer(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_PASS);
    CHECK_EQ(r.overflow, 0);
    CHECK(has_row(&r, "COUNTER ADVANCES=YES"));
    CHECK(x_rows_valid(&r));
    w_free(w);
    w_init(w); /* a frequency of zero */
    w->dm.sys[TR_CNTHCTL_EL2] = 3;
    w->dm.pct = 5;
    tests_run_timer(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_FAIL);
    w_free(w);
    w_init(w); /* a counter that does not advance */
    w->dm.sys[TR_CNTFRQ_EL0] = 1000000000ull;
    w->dm.counter_stuck = 1;
    w->dm.pct = 77;
    tests_run_timer(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_FAIL);
    w_free(w);
    w_init(w); /* the virtual count is not the physical count minus the offset */
    w->dm.sys[TR_CNTFRQ_EL0] = 1000000000ull;
    w->dm.sys[TR_CNTVOFF_EL2] = 500;
    w->dm.vct_offset = 0; /* the model ignores the offset */
    w->dm.pct = 10000;
    tests_run_timer(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_FAIL);
    w_free(w);
    for (int reg = 0; reg < 4; reg++) { /* an unreadable register: UNKNOWN, never PASS */
        static const int regs[] = {TR_CNTFRQ_EL0, TR_CNTHCTL_EL2, TR_CNTVOFF_EL2, TR_CNTPCT_EL0};

        w_init(w);
        w->dm.sys[TR_CNTFRQ_EL0] = 1000000000ull;
        w->dm.sys_fault[regs[reg]] = 1;
        tests_run_timer(&w->ctx, &r);
        CHECK_EQ(r.verdict, TV_UNKNOWN);
        w_free(w);
    }
    w_init(w); /* ECV >= 2 but CNTPOFF_EL2 faults */
    w->dm.sys[TR_CNTFRQ_EL0] = 1000000000ull;
    w->dm.sys_fault[TR_CNTPOFF_EL2] = 1;
    tests_run_timer(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_FAIL);
    w_free(w);
    w_init(w); /* no ECV: CNTPOFF_EL2 is not read at all */
    w->dm.sys[TR_CNTFRQ_EL0] = 1000000000ull;
    w->ctx.ids.v[TI_MMFR0] &= ~(0xFull << 60);
    w->dm.sys_fault[TR_CNTPOFF_EL2] = 1;
    tests_run_timer(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_PASS);
    w_free(w);

    /* T08: no address source, so nothing can pass. */
    {
        tests_adsp_obs o = {-1, -1, -1};
        int label = 0;

        CHECK_EQ(tests_adsp_decide(&o, &label), TV_UNKNOWN);
        o.smem = 1;
        CHECK_EQ(tests_adsp_decide(&o, &label), TV_UNKNOWN);
        o.glink = 1;
        o.ipcc = 1;
        CHECK_EQ(tests_adsp_decide(&o, &label), TV_PASS);
        CHECK(label != TL_VERIFIED && label != TL_OBSERVED);
        o.glink = 0;
        CHECK_EQ(tests_adsp_decide(&o, &label), TV_FAIL);
        w_init(w);
        tests_run_adsp(&w->ctx, &r);
        CHECK_EQ(r.verdict, TV_UNKNOWN);
        CHECK_EQ(r.label, TL_UNIMPLEMENTED);
        CHECK_EQ(w->dm.nlog, 0);
        w_free(w);
    }

    /* T04: DC ZVA against a model of the instruction. */
    {
        zvamodel zm;
        static const unsigned dczid_cases[] = {4, 6}; /* 64 and 256 bytes */

        memset(&zops, 0, sizeof zops);
        zops.ctx = &zm;
        zops.buffer = zbuf;
        zops.zva = zm_zva;
        zops.sync = zm_sync;
        for (unsigned k = 0; k < 2; k++) {
            for (int mode = 0; mode < 4; mode++) {
                memset(&zm, 0, sizeof zm);
                zm.mode = mode;
                zm.block = 4u << dczid_cases[k];
                zm.buf = zbuf;
                w_init(w);
                w->ctx.zva = &zops;
                w->ctx.ids.v[TI_DCZID] = dczid_cases[k];
                tests_run_zva(&w->ctx, &r);
                CHECK_EQ(zm.calls, 1);
                CHECK_EQ(r.verdict, mode == 0 ? TV_PASS : TV_FAIL);
                CHECK_EQ(r.overflow, 0);
                /* The block is the middle third: both neighbours must be untouched. */
                CHECK(zm.last_addr == zbuf + zm.block);
                CHECK_EQU((uintptr_t)zm.last_addr % zm.block, 0);
                w_free(w);
            }
        }
        /* Prohibited, unread, implausible or without a buffer: DC ZVA is not executed. */
        for (int variant = 0; variant < 4; variant++) {
            memset(&zm, 0, sizeof zm);
            zm.block = 64;
            zm.buf = zbuf;
            w_init(w);
            w->ctx.zva = &zops;
            if (variant == 0)
                w->ctx.ids.v[TI_DCZID] = 0x14;
            else if (variant == 1)
                w->ctx.ids.have &= ~(1u << TI_DCZID);
            else if (variant == 2)
                w->ctx.ids.v[TI_DCZID] = 0xF;
            else
                w->ctx.zva = NULL;
            tests_run_zva(&w->ctx, &r);
            CHECK_EQ(zm.calls, 0);
            CHECK_EQ(r.verdict, variant == 0 ? TV_INFO : TV_UNKNOWN);
            w_free(w);
        }
    }

    /* T09: the DRAM map. */
    {
        uint8_t map[48 * 16];
        probe_memmap mm;
        tests_dram d;
        unsigned n = 0;

        memset(map, 0, sizeof map);
        n = md_add(map, n, 7, 0x80000000ull, 0x80000);       /* 2 GiB of conventional RAM */
        n = md_add(map, n, 1, 0x180000000ull, 0x1000);       /* loader code: RAM too */
        n = md_add(map, n, 7, 0x181000000ull, 0x7F000);      /* adjacent conventional memory */
        n = md_add(map, n, 11, 0x1000000000ull, 0x10000);    /* MMIO: not RAM */
        CHECK_EQ(probe_memmap_init(&mm, map, n * 48, sizeof map, 48), 0);
        tests_dram_summarize(&mm, &d);
        CHECK(d.ok);
        CHECK_EQ(d.extents, 2);
        CHECK_EQU(d.low, 0x80000000ull);
        CHECK_EQU(d.high, 0x200000000ull);
        CHECK_EQU(d.total_bytes, 0x100000ull * 4096);
        CHECK_EQU(d.ext_base[0], 0x80000000ull);
        CHECK_EQU(d.ext_end[0], 0x100000000ull);
        CHECK_EQU(d.ext_base[1], 0x180000000ull);
        CHECK_EQU(d.ext_end[1], 0x200000000ull);
        CHECK_EQ(tests_dram_verdict(&d, 36), TV_PASS);
        CHECK_EQ(tests_dram_verdict(&d, 33), TV_PASS);  /* exactly 8 GiB */
        CHECK_EQ(tests_dram_verdict(&d, 32), TV_FAIL);  /* RAM above 4 GiB */
        CHECK_EQ(tests_dram_verdict(&d, 0), TV_UNKNOWN);
        CHECK_EQ(tests_dram_verdict(&d, 64), TV_UNKNOWN);
        w_init(w);
        w->ctx.memmap = &mm;
        w->desc.ipa_bits = 32;
        tests_run_dram(&w->ctx, &r);
        CHECK_EQ(r.verdict, TV_FAIL);
        CHECK_EQ(r.overflow, 0);
        w->desc.ipa_bits = 36;
        tests_run_dram(&w->ctx, &r);
        CHECK_EQ(r.verdict, TV_PASS);
        CHECK_EQ(w->dm.nlog, 0); /* the DRAM map needs no device */
        w_free(w);

        /* No RAM in the map, no map, and too many extents. */
        memset(map, 0, sizeof map);
        n = md_add(map, 0, 11, 0x1000000000ull, 0x10000);
        CHECK_EQ(probe_memmap_init(&mm, map, n * 48, sizeof map, 48), 0);
        tests_dram_summarize(&mm, &d);
        CHECK(!d.ok);
        CHECK_EQ(tests_dram_verdict(&d, 36), TV_UNKNOWN);
        tests_dram_summarize(NULL, &d);
        CHECK(!d.ok);
        n = 0;
        memset(map, 0, sizeof map);
        for (unsigned i = 0; i < 10; i++)
            n = md_add(map, n, 7, 0x80000000ull + 0x10000000ull * i, 0x100);
        CHECK_EQ(probe_memmap_init(&mm, map, n * 48, sizeof map, 48), 0);
        tests_dram_summarize(&mm, &d);
        CHECK_EQ(d.extents, 8);
        CHECK_EQ(d.truncated, 2);
        for (unsigned i = 1; i < d.extents; i++)
            CHECK(d.ext_base[i - 1] < d.ext_base[i]);
    }
    free(w);
}
