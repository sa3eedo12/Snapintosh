/* SPDX-License-Identifier: MIT */
/* Host tests of the second version of the X2 image's device tests: T03 without its CNTPOFF_EL2
 * read, and T05 with the redistributor frames taken from the GICC structures when the MADT has
 * no GICR discovery range. The fixtures are those of test_x_dev.c (xdev.h). Every table and
 * register image here is synthetic, shaped like what the Surface Pro 11's MADT dump showed
 * (twelve GICC structures, one GICD of version 4, one ITS, no GICR discovery range) and not a
 * measurement of any machine. */
#include "xdev.h"

#define FRAME 0x40000ull /* TESTS_GICC_FRAME_BYTES */
#define PCPU_MAX TESTS_MAX_CPUS

/* ---- T03 ---- */

static void timer_world(world *w)
{
    w_init(w);
    w->dm.sys[TR_CNTFRQ_EL0] = 1000000000ull;
    w->dm.sys[TR_CNTHCTL_EL2] = 0x3;
    w->dm.sys[TR_CNTPOFF_EL2] = 0x123;
    w->dm.pct = 1000;
}

static void set_ecv(world *w, uint64_t ecv)
{
    w->ctx.ids.v[TI_MMFR0] = (w->ctx.ids.v[TI_MMFR0] & ~(0xFull << 60)) | (ecv << 60);
}

/* One world of a matrix of timer observations, k = 0..7: a sound one and seven that decide
 * something other than PASS, none of which has anything to do with CNTPOFF_EL2. */
static void matrix_world(world *w, unsigned k)
{
    timer_world(w);
    switch (k) {
    case 1:
        w->dm.sys[TR_CNTFRQ_EL0] = 0; /* a frequency of zero */
        break;
    case 2:
        w->dm.counter_stuck = 1; /* a counter that does not advance */
        break;
    case 3:
        w->dm.sys[TR_CNTVOFF_EL2] = 500; /* the virtual count ignores the offset */
        break;
    case 4:
        w->dm.sys_fault[TR_CNTFRQ_EL0] = 1;
        break;
    case 5:
        w->dm.sys_fault[TR_CNTHCTL_EL2] = 1;
        break;
    case 6:
        w->dm.sys_fault[TR_CNTVOFF_EL2] = 1;
        break;
    case 7:
        w->dm.sys_fault[TR_CNTPCT_EL0] = 1;
        break;
    default:
        break;
    }
}

static int rows_equal_but(const tests_result *a, const tests_result *b, const char *needle)
{
    if (a->nrows != b->nrows)
        return 0;
    for (unsigned i = 0; i < a->nrows; i++)
        if (strstr(a->rows[i], needle) == NULL && strcmp(a->rows[i], b->rows[i]) != 0)
            return 0;
    return 1;
}

void test_x_t03_cntpoff(void)
{
    world *w = xalloc(sizeof *w);
    tests_result r, on;
    unsigned verdicts[TV_COUNT] = {0};

    section("t03 cntpoff");

    /* The default: ECV says the register exists, and it is not read. A read would fault here
     * and be counted, so neither can have happened. */
    timer_world(w);
    w->dm.sys_fault[TR_CNTPOFF_EL2] = 1;
    CHECK_EQ(w->ctx.read_cntpoff, 0);
    tests_run_timer(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_PASS);
    CHECK_EQ(r.label, TL_OBSERVED);
    CHECK_EQ(w->dm.sys_reads[TR_CNTPOFF_EL2], 0);
    CHECK(w->dm.sys_reads[TR_CNTFRQ_EL0] != 0); /* the rest of the test did run */
    CHECK(has_row(&r, "CNTPOFF_EL2 NOT READ (ECV=2)"));
    CHECK(!has_row(&r, "CNTPOFF_EL2="));
    CHECK_STR(r.proves, "P: CNTFRQ, COUNTER, ECV BITS AS READ");
    CHECK_STR(r.not_proves, "N: OTHER CORES OR ANY CNTPOFF ACCESS");
    CHECK(strlen(r.not_proves) <= PROBE_LINE_PAYLOAD_MAX);
    CHECK_EQ(r.nrows, 9);
    CHECK_EQ(r.overflow, 0);
    CHECK(x_rows_valid(&r));
    w_free(w);

    /* A build that reads it: the value is shown, the read is made once, and a fault is a FAIL
     * as it always was. What the test does not prove changes with what it did. */
    timer_world(w);
    w->ctx.read_cntpoff = 1;
    tests_run_timer(&w->ctx, &on);
    CHECK_EQ(on.verdict, TV_PASS);
    CHECK_EQ(w->dm.sys_reads[TR_CNTPOFF_EL2], 1);
    CHECK(has_row(&on, "CNTPOFF_EL2=0000000000000123"));
    CHECK(!has_row(&on, "NOT READ"));
    CHECK_STR(on.not_proves, "N: OTHER CORES OR CNTPOFF WRITES");
    CHECK_EQ(on.nrows, 9);
    CHECK(x_rows_valid(&on));
    w_free(w);
    timer_world(w);
    w->ctx.read_cntpoff = 1;
    w->dm.sys_fault[TR_CNTPOFF_EL2] = 1;
    tests_run_timer(&w->ctx, &on);
    CHECK_EQ(on.verdict, TV_FAIL);
    CHECK_EQ(w->dm.sys_reads[TR_CNTPOFF_EL2], 1);
    CHECK(has_row(&on, "CNTPOFF_EL2=FAULT"));
    w_free(w);

    /* Where ECV says the register does not exist it is not read in either mode, and the row
     * says so as it always did; an unreadable ECV field is "not 2 or more" as well. A value of
     * 3 or more still means "exists": the row names it. */
    for (int mode = 0; mode < 2; mode++) {
        for (unsigned ecv = 0; ecv < 5; ecv++) {
            timer_world(w);
            w->ctx.read_cntpoff = mode;
            set_ecv(w, ecv);
            tests_run_timer(&w->ctx, &r);
            CHECK_EQ(r.verdict, TV_PASS);
            CHECK_EQ(w->dm.sys_reads[TR_CNTPOFF_EL2], ecv >= 2 && mode ? 1 : 0);
            if (ecv < 2) {
                CHECK(has_row(&r, "CNTPOFF_EL2=N/A (ID ECV<2)"));
            } else if (mode) {
                CHECK(has_row(&r, "CNTPOFF_EL2=0000000000000123"));
            } else {
                char want[48];

                snprintf(want, sizeof want, "CNTPOFF_EL2 NOT READ (ECV=%u)", ecv);
                CHECK(has_row(&r, want));
            }
            CHECK(x_rows_valid(&r));
            w_free(w);
        }
        timer_world(w);
        w->ctx.read_cntpoff = mode;
        w->ctx.ids.have &= ~(1u << TI_MMFR0);
        tests_run_timer(&w->ctx, &r);
        CHECK_EQ(w->dm.sys_reads[TR_CNTPOFF_EL2], 0);
        CHECK(has_row(&r, "CNTPOFF_EL2=N/A (ID ECV<2)"));
        w_free(w);
    }

    /* Whatever the rest of the test decides it decides the same with or without the read (the
     * register here is readable), and so do all of its rows but the one that names the register. */
    for (unsigned k = 0; k < 8; k++) {
        tests_result off;

        matrix_world(w, k);
        tests_run_timer(&w->ctx, &off);
        w_free(w);
        matrix_world(w, k);
        w->ctx.read_cntpoff = 1;
        tests_run_timer(&w->ctx, &on);
        w_free(w);
        CHECK_EQ(off.verdict, on.verdict);
        CHECK(rows_equal_but(&off, &on, "CNTPOFF_EL2"));
        CHECK_EQ(off.overflow, 0);
        verdicts[off.verdict]++;
    }
    CHECK(verdicts[TV_PASS] >= 1 && verdicts[TV_FAIL] >= 3 && verdicts[TV_UNKNOWN] >= 4);
    free(w);
}

/* ---- T05 ---- */

typedef struct {
    unsigned n;        /* GICC structures */
    unsigned version;  /* the GICD structure's GIC version */
    int gicr_range;    /* 0 none, 1 a valid GICR discovery range, 2 one the parser drops */
    uint64_t base[PCPU_MAX + 1]; /* GICR Base Address of GICC structure i; one more than the parser keeps */
    uint32_t uid[PCPU_MAX + 1];
    uint32_t flags[PCPU_MAX + 1];
} pcpu_spec;

static uint64_t pcpu_base(unsigned i)
{
    return GICR + FRAME * i;
}

static uint64_t pcpu_mpidr(unsigned i)
{
    return 0x80000000ull | ((uint64_t)(i / 4) << 8) | (i % 4);
}

/* What GICR_TYPER's high word holds for CPU i. */
static uint32_t pcpu_aff(unsigned i)
{
    return (uint32_t)(((i / 4) << 8) | (i % 4));
}

static void pcpu_default(pcpu_spec *s, unsigned n, unsigned version)
{
    memset(s, 0, sizeof *s);
    s->n = n;
    s->version = version;
    for (unsigned i = 0; i < n; i++) {
        s->base[i] = pcpu_base(i);
        s->uid[i] = i;
        s->flags[i] = 1;
    }
}

/* The MADT: the GICC structures in the given order (NULL: 0..n-1), the GICD, one ITS. */
static tbl pcpu_madt(const pcpu_spec *s, const unsigned *order)
{
    tbl t;

    t_start(&t, "APIC", 8);
    for (unsigned p = 0; p < s->n; p++) {
        unsigned i = order != NULL ? order[p] : p;

        madt_gicc_uid(&t, s->flags[i], s->uid[i], s->base[i], pcpu_mpidr(i));
    }
    madt_gicd(&t, GICD, s->version);
    madt_its(&t, ITS);
    if (s->gicr_range == 1)
        madt_gicr(&t, GICR, (uint32_t)(FRAME * s->n));
    else if (s->gicr_range == 2)
        madt_gicr(&t, 0, 0x40000);
    t_seal(&t);
    return t;
}

/* The hardware: a distributor and an ITS of the given architecture revision, and one frame
 * per CPU where the MADT of an honest firmware says, whatever the table under test claims. No
 * frame is marked Last: that bit is not set per frame in this layout. */
static void pcpu_regs(devmodel *d, unsigned n, unsigned arch)
{
    dm_set(d, GICD + 0x0000, 0x00000050);
    dm_set(d, GICD + 0x0004, 0x00FF0408);
    dm_set(d, GICD + 0x0008, 0x0200143B);
    dm_set(d, GICD + 0xFFE8, arch << 4);
    for (unsigned i = 0; i < n; i++) {
        uint64_t fr = pcpu_base(i);

        dm_set(d, fr + 0xFFE8, arch << 4);
        dm_set(d, fr + 0x0008, 0x2); /* VLPIS */
        dm_set(d, fr + 0x000C, pcpu_aff(i));
        dm_set(d, fr + 0x0014, 0x6);
    }
    dm_set(d, ITS + 0x0000, 1);
    dm_set(d, ITS + 0x0008, 0x0000C781);
    dm_set(d, ITS + 0xFFE8, arch << 4);
}

static void pcpu_world(world *w, const pcpu_spec *s, unsigned arch, const unsigned *order)
{
    tbl t = pcpu_madt(s, order);

    w_init(w);
    gic_setup(w, &t);
    pcpu_regs(&w->dm, s->n < PCPU_MAX ? s->n : PCPU_MAX, arch);
}

/* The windows a read may fall in: the distributor, the ITS and one frame per CPU. */
static unsigned pcpu_windows(win *wins, unsigned n)
{
    wins[0] = (win){GICD, 0x10000};
    wins[1] = (win){ITS, 0x10000};
    for (unsigned i = 0; i < n; i++)
        wins[2 + i] = (win){pcpu_base(i), FRAME};
    return n + 2;
}

static int parse_madt_of(const tbl *t, tests_madt *m)
{
    uint8_t *x = exact_copy(t->b, t->len);
    int rc = tests_madt_parse(x, t->len, m);

    free(x);
    return rc;
}

static unsigned count_rows(const tests_result *r, const char *needle)
{
    unsigned n = 0;

    for (unsigned i = 0; i < r->nrows; i++)
        n += strstr(r->rows[i], needle) != NULL;
    return n;
}

/* The pure part: what the parser records and which frames come out, in which order. */
static void gicc_frames_unit(void)
{
    pcpu_spec s;
    tbl t;
    tests_madt m;
    tests_gicc_frame f[TESTS_MAX_CPUS];
    unsigned order[PCPU_MAX];
    unsigned n;

    pcpu_default(&s, 12, 4);
    t = pcpu_madt(&s, NULL);
    CHECK_EQ(parse_madt_of(&t, &m), 0);
    CHECK_EQ(m.n_gicr_seen, 0);
    CHECK_EQ(m.n_gicr, 0);
    CHECK_EQ(m.n_cpus, 12);
    CHECK_EQ(m.gicd_version, 4);
    CHECK_EQ(m.uid[7], 7);
    n = tests_madt_gicc_frames(&m, f);
    CHECK_EQ(n, 12);
    for (unsigned i = 0; i < 12 && i < n; i++) {
        CHECK_EQU(f[i].base, pcpu_base(i));
        CHECK_EQ(f[i].cpu, i);
    }

    /* UID order, whatever the table's own order is. */
    for (unsigned i = 0; i < 12; i++)
        order[i] = 11 - i;
    t = pcpu_madt(&s, order);
    CHECK_EQ(parse_madt_of(&t, &m), 0);
    CHECK_EQ(m.uid[0], 11); /* the table lists UID 11 first */
    n = tests_madt_gicc_frames(&m, f);
    CHECK_EQ(n, 12);
    for (unsigned i = 0; i < 12 && i < n; i++) {
        CHECK_EQ(m.uid[f[i].cpu], i);
        CHECK_EQU(f[i].base, m.gicc_gicr[f[i].cpu]);
        CHECK_EQU(f[i].base, pcpu_base(i));
    }

    /* Equal UIDs keep the table's order. */
    pcpu_default(&s, 8, 4);
    s.uid[3] = 5;
    t = pcpu_madt(&s, NULL);
    CHECK_EQ(parse_madt_of(&t, &m), 0);
    n = tests_madt_gicc_frames(&m, f);
    CHECK_EQ(n, 8);
    CHECK_EQ(f[3].cpu, 4); /* UID 4 */
    CHECK_EQ(f[4].cpu, 3); /* the two with UID 5, in table order */
    CHECK_EQ(f[5].cpu, 5);

    /* A disabled GICC is not a CPU and has no frame; a zero base is kept for the caller to refuse. */
    pcpu_default(&s, 6, 4);
    s.flags[2] = 0;
    s.base[4] = 0;
    t = pcpu_madt(&s, NULL);
    CHECK_EQ(parse_madt_of(&t, &m), 0);
    CHECK_EQ(m.n_gicc_seen, 6);
    n = tests_madt_gicc_frames(&m, f);
    CHECK_EQ(n, 5);
    CHECK_EQ(f[2].cpu, 2); /* CPU index 2 of the kept ones is UID 3 */
    CHECK_EQ(m.uid[f[2].cpu], 3);
    CHECK_EQU(f[3].base, 0);

    /* Not the per-CPU layout: any discovery range at all, kept or dropped, a GIC version
     * below 3 or not stated, no GICD, no CPU, an unparsed table. */
    for (int range = 1; range <= 2; range++) {
        pcpu_default(&s, 12, 4);
        s.gicr_range = range;
        t = pcpu_madt(&s, NULL);
        CHECK_EQ(parse_madt_of(&t, &m), 0);
        CHECK_EQ(m.n_gicr_seen, 1);
        CHECK_EQ(m.n_gicr, range == 1 ? 1 : 0); /* dropped for its zero base: still seen */
        CHECK_EQ(tests_madt_gicc_frames(&m, f), 0);
    }
    for (unsigned version = 0; version < 5; version++) {
        pcpu_default(&s, 12, version);
        t = pcpu_madt(&s, NULL);
        CHECK_EQ(parse_madt_of(&t, &m), 0);
        CHECK_EQ(tests_madt_gicc_frames(&m, f), version >= 3 ? 12 : 0);
    }
    pcpu_default(&s, 0, 4);
    t = pcpu_madt(&s, NULL);
    CHECK_EQ(parse_madt_of(&t, &m), 0);
    CHECK_EQ(tests_madt_gicc_frames(&m, f), 0);
    t_start(&t, "APIC", 8);
    madt_gicc_uid(&t, 1, 0, pcpu_base(0), pcpu_mpidr(0));
    t_seal(&t);
    CHECK_EQ(parse_madt_of(&t, &m), 0);
    CHECK_EQ(tests_madt_gicc_frames(&m, f), 0); /* no GICD */
    memset(&m, 0, sizeof m);
    CHECK_EQ(tests_madt_gicc_frames(&m, f), 0);
    st32(t.b + 4, (uint32_t)t.len + 1);
    CHECK_EQ(parse_madt_of(&t, &m), -1);
    CHECK_EQ(tests_madt_gicc_frames(&m, f), 0);

    /* More GICC structures than there is room for: the extra ones are counted, not listed. */
    pcpu_default(&s, PCPU_MAX + 1, 4);
    t = pcpu_madt(&s, NULL);
    CHECK_EQ(parse_madt_of(&t, &m), 0);
    CHECK_EQ(m.n_cpus, TESTS_MAX_CPUS);
    CHECK_EQ(m.truncated, 1);
    CHECK_EQ(tests_madt_gicc_frames(&m, f), TESTS_MAX_CPUS);

    /* The MADT of the first version's fixtures still parses as before, with its range seen. */
    t = good_madt();
    CHECK_EQ(parse_madt_of(&t, &m), 0);
    CHECK_EQ(m.n_gicr_seen, 1);
    CHECK_EQ(m.n_gicr, 1);
    CHECK_EQ(m.uid[1], 0); /* madt_gicc leaves the UID zero */
}

void test_x_t05_gicc(void)
{
    world *w = xalloc(sizeof *w);
    tests_result r;
    pcpu_spec s;
    tbl t;
    win wins[PCPU_MAX + 2];
    unsigned order[PCPU_MAX], nw;

    section("t05 gicc layout");
    gicc_frames_unit();

    /* A MADT shaped like the unit's: twelve CPUs, each with its own frame. Every frame is read
     * once (identification, affinity, wake state), nothing outside the windows the table gave,
     * nothing is looked for in the way of a Last bit, and the per-CPU comparison is made. */
    pcpu_default(&s, 12, 4);
    pcpu_world(w, &s, 4, NULL);
    nw = pcpu_windows(wins, 12);
    tests_run_gic(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_PASS);
    CHECK_EQ(r.label, TL_OBSERVED);
    CHECK_EQ(r.overflow, 0);
    CHECK(x_rows_valid(&r));
    CHECK_EQ(w->dm.unlisted, 0);
    CHECK_EQ(stray_reads(&w->dm, wins, nw), 0);
    CHECK_EQ(w->dm.nlog, 4 + 12 * 4 + 3); /* GICD, four reads per frame, ITS */
    for (unsigned i = 0; i < 12; i++) {
        CHECK(read_at(&w->dm, pcpu_base(i) + 0xFFE8));
        CHECK(read_at(&w->dm, pcpu_base(i) + 0x0C));
        CHECK(read_at(&w->dm, pcpu_base(i) + 0x14));
    }
    CHECK(has_row(&r, "GICD @0010000000 ARCH=4 MADT=4"));
    CHECK(has_row(&r, "GICR GICC FRAMES=12 OF 12"));
    CHECK(has_row(&r, "MADT CPUS=12 MATCHED=12 NO FRAME=0"));
    /* Two-digit counts: the wake row and the comparison do not fit one row, and take two. */
    CHECK(has_row(&r, "GICR AWAKE=0 ASLEEP=12"));
    CHECK(has_row(&r, "GICC BASE DIFF=0"));
    CHECK(!has_row(&r, "ASLEEP=12 GICC"));
    CHECK(has_row(&r, "MAP MMIO=0 UNLISTED=14 REFUSED=0"));
    CHECK(!has_row(&r, "LAST="));
    CHECK(!has_row(&r, "RANGES="));
    CHECK_EQ(count_rows(&r, "REFUSED:"), 0);
    /* The descriptor stays a cross-check: it compares with the frame of the first CPU. */
    w->desc.gicd_base = GICD;
    w->desc.gicr_base = pcpu_base(0);
    w->desc.n_cpus = 12;
    tests_run_gic(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_PASS);
    CHECK(has_row(&r, "DESC D=OK R=OK I=UNK C=OK"));
    w->desc.gicr_base = pcpu_base(0) + FRAME;
    tests_run_gic(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_PASS);
    CHECK(has_row(&r, "R=DIFF"));
    w_free(w);

    /* The same table with the GICC structures in the opposite order. */
    pcpu_default(&s, 12, 4);
    for (unsigned i = 0; i < 12; i++)
        order[i] = 11 - i;
    pcpu_world(w, &s, 4, order);
    tests_run_gic(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_PASS);
    CHECK(has_row(&r, "MADT CPUS=12 MATCHED=12 NO FRAME=0"));
    w_free(w);

    /* The old layout is unchanged: a discovery range, walked to the frame marked Last. A table
     * that has both (GICC bases and a range) is walked, not derived from. */
    w_init(w);
    t = good_madt();
    gic_setup(w, &t);
    gic_good_regs(&w->dm, 3, 1);
    tests_run_gic(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_PASS);
    CHECK(has_row(&r, "GICR RANGES=1 FRAMES=2 LAST=Y"));
    CHECK(has_row(&r, "GICC BASE DIFF=0"));
    CHECK_EQ(count_rows(&r, "GICC GICR"), 0);
    CHECK(!has_row(&r, "GICC FRAMES"));
    w_free(w);

    /* The old layout with twelve CPUs, where the wake row used to be 39 columns and was flagged
     * as dropped: now the comparison has a row of its own, and everything else is as it was. */
    w_init(w);
    t_start(&t, "APIC", 8);
    for (unsigned i = 0; i < 12; i++)
        madt_gicc_uid(&t, 1, i, GICR + 0x20000ull * i, pcpu_mpidr(i));
    madt_gicd(&t, GICD, 3);
    madt_gicr(&t, GICR, 12 * 0x20000);
    madt_its(&t, ITS);
    t_seal(&t);
    gic_setup(w, &t);
    pcpu_regs(&w->dm, 0, 3); /* the distributor and the ITS */
    for (unsigned i = 0; i < 12; i++) {
        uint64_t fr = GICR + 0x20000ull * i;

        dm_set(&w->dm, fr + 0xFFE8, 3u << 4);
        dm_set(&w->dm, fr + 0x0008, i == 11 ? 0x10 : 0);
        dm_set(&w->dm, fr + 0x000C, pcpu_aff(i));
        dm_set(&w->dm, fr + 0x0014, 0x6);
    }
    tests_run_gic(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_PASS);
    CHECK_EQ(r.overflow, 0);
    CHECK(x_rows_valid(&r));
    CHECK(has_row(&r, "GICR RANGES=1 FRAMES=12 LAST=Y"));
    CHECK(has_row(&r, "MADT CPUS=12 MATCHED=12 NO FRAME=0"));
    CHECK(has_row(&r, "GICR AWAKE=0 ASLEEP=12"));
    CHECK(has_row(&r, "GICC BASE DIFF=0"));
    w_free(w);

    /* The version has to say GICv3 or later: from a MADT that states 2, or nothing, frames are
     * not derived. GICv2 hardware under a table whose GICC structures carry no base still passes. */
    pcpu_default(&s, 2, 2);
    s.base[0] = s.base[1] = 0;
    pcpu_world(w, &s, 2, NULL);
    tests_run_gic(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_PASS);
    CHECK(has_row(&r, "GICV2 OR OLDER"));
    CHECK_EQ(count_rows(&r, "REFUSED:"), 0);
    CHECK_EQ(w->dm.nlog, 4 + 3); /* the distributor and the ITS, no frame */
    w_free(w);
    pcpu_default(&s, 12, 0);
    pcpu_world(w, &s, 4, NULL); /* not stated: the old rule, which finds no frame for any CPU */
    tests_run_gic(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_FAIL);
    CHECK(!has_row(&r, "GICC FRAMES"));
    CHECK(has_row(&r, "NO FRAME=12"));
    w_free(w);

    /* The table and the hardware disagree about the version: a FAIL in either direction. */
    for (unsigned arch = 3; arch <= 4; arch++) {
        pcpu_default(&s, 12, 7 - arch);
        pcpu_world(w, &s, arch, NULL);
        tests_run_gic(&w->ctx, &r);
        CHECK_EQ(r.verdict, TV_FAIL);
        w_free(w);
    }

    /* A GICC base one frame off: CPU 5 names the frame of CPU 6, so no frame carries CPU 5's
     * affinity and two CPUs claim one. The frame list is never shifted to hide it. */
    pcpu_default(&s, 12, 4);
    s.base[5] = pcpu_base(6);
    pcpu_world(w, &s, 4, NULL);
    tests_run_gic(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_FAIL);
    CHECK(has_row(&r, "MADT CPUS=12 MATCHED=10 NO FRAME=2"));
    CHECK(has_row(&r, "GICC BASE DIFF=0")); /* each CPU's own base is where its frame was read */
    CHECK(!read_at(&w->dm, pcpu_base(5) + 0xFFE8)); /* the frame of CPU 5 was never named */
    w_free(w);

    /* Two bases swapped: every CPU still finds exactly one frame carrying its affinity, but not
     * at the address its own GICC structure names. This is what the BASE DIFF count is for. */
    pcpu_default(&s, 12, 4);
    s.base[3] = pcpu_base(4);
    s.base[4] = pcpu_base(3);
    pcpu_world(w, &s, 4, NULL);
    tests_run_gic(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_FAIL);
    CHECK(has_row(&r, "MATCHED=12 NO FRAME=0"));
    CHECK(has_row(&r, "GICC BASE DIFF=2"));
    w_free(w);

    /* A frame whose affinity belongs to nobody. */
    pcpu_default(&s, 12, 4);
    pcpu_world(w, &s, 4, NULL);
    dm_set(&w->dm, pcpu_base(8) + 0x0C, 0x00FF00FF);
    tests_run_gic(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_FAIL);
    CHECK(has_row(&r, "NO FRAME=1"));
    w_free(w);

    /* A base that fails the window rules is refused by name and never read; the others are,
     * the comparison is skipped and the verdict is UNKNOWN, never a FAIL from a CPU whose frame
     * was not looked at. */
    for (int bad = 0; bad < 3; bad++) {
        static const char *const reason[3] = {"GICC GICR5 REFUSED: BASE IS ZERO",
                                              "GICC GICR5 REFUSED: MISALIGNED",
                                              "GICC GICR5 REFUSED: MAP SAYS NOT MMIO"};
        uint64_t base = bad == 0 ? 0 : bad == 1 ? pcpu_base(5) + 0x800 : W_RAM_BASE + 0x40000;

        pcpu_default(&s, 12, 4);
        s.base[5] = base;
        pcpu_world(w, &s, 4, NULL);
        dm_set(&w->dm, base + 0xFFE8, 0x40); /* a believable frame where it must not be read */
        dm_set(&w->dm, base + 0x0C, pcpu_aff(5));
        dm_set(&w->dm, base + 0x08, 0);
        dm_set(&w->dm, base + 0x14, 6);
        tests_run_gic(&w->ctx, &r);
        CHECK_EQ(r.verdict, TV_UNKNOWN);
        CHECK(has_row(&r, reason[bad]));
        CHECK(has_row(&r, "CPU MATCH SKIPPED: FRAME NOT READ"));
        CHECK(has_row(&r, "GICR GICC FRAMES=11 OF 12"));
        CHECK(!has_row(&r, "NO FRAME="));
        CHECK(!read_at(&w->dm, base + 0xFFE8));
        CHECK(has_row(&r, "REFUSED=1"));
        CHECK_EQ(r.overflow, 0);
        CHECK(x_rows_valid(&r));
        w_free(w);
    }

    /* Every frame refused: four are listed, the rest counted in one row, the windows row has
     * all twelve, the distributor is still read and the result is nowhere near full. */
    pcpu_default(&s, 12, 4);
    for (unsigned i = 0; i < 12; i++)
        s.base[i] = 0;
    pcpu_world(w, &s, 4, NULL);
    tests_run_gic(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_UNKNOWN);
    CHECK_EQ(count_rows(&r, "BASE IS ZERO"), 4);
    CHECK(has_row(&r, "GICC GICR0 REFUSED: BASE IS ZERO"));
    CHECK(has_row(&r, "GICC GICR3 REFUSED: BASE IS ZERO"));
    CHECK(!has_row(&r, "GICC GICR4 REFUSED"));
    CHECK(has_row(&r, "GICC GICR: 8 MORE REFUSED"));
    CHECK(has_row(&r, "MAP MMIO=0 UNLISTED=2 REFUSED=12"));
    CHECK(has_row(&r, "GICR GICC FRAMES=0 OF 12"));
    CHECK(read_at(&w->dm, GICD));
    CHECK_EQ(r.overflow, 0);
    CHECK(x_rows_valid(&r));
    CHECK(r.nrows < TESTS_DETAIL_MAX);
    w_free(w);

    /* The strict policy: the windows the map lists as MMIO are read, the others refused by name. */
    pcpu_default(&s, 12, 4);
    pcpu_world(w, &s, 4, NULL);
    w->ctx.require_mmio_map = 1;
    w_map(w, 11, GICD, 0x10);
    w_map(w, 11, ITS, 0x10);
    tests_run_gic(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_UNKNOWN);
    CHECK(has_row(&r, "GICC GICR0 REFUSED: NOT MMIO IN MAP"));
    CHECK(has_row(&r, "GICC GICR: 8 MORE REFUSED"));
    CHECK(!read_at(&w->dm, pcpu_base(0) + 0xFFE8));
    w_map(w, 11, GICR, 0x300); /* twelve frames of 0x40 pages */
    tests_run_gic(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_PASS);
    CHECK(has_row(&r, "MAP STRICT MMIO=14 REFUSED=0"));
    w_free(w);

    /* A frame that faults stops the reading there: nothing after it is touched, the verdict
     * is UNKNOWN and the fault is counted. */
    pcpu_default(&s, 12, 4);
    pcpu_world(w, &s, 4, NULL);
    w->dm.fault_at[w->dm.nfault_at++] = pcpu_base(3) + 0xFFE8;
    tests_run_gic(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_UNKNOWN);
    CHECK(has_row(&r, "GICR GICC FRAMES=3 OF 12"));
    CHECK(has_row(&r, "CPU MATCH SKIPPED: FRAME NOT READ"));
    CHECK(!has_row(&r, "NO FRAME="));
    CHECK(!read_at(&w->dm, pcpu_base(4) + 0xFFE8));
    CHECK(has_row(&r, "FAULTS=1"));
    w_free(w);

    /* A frame whose identification is not a GICv3 or v4 does the same. */
    pcpu_default(&s, 12, 4);
    pcpu_world(w, &s, 4, NULL);
    dm_set(&w->dm, pcpu_base(7) + 0xFFE8, 0x20);
    tests_run_gic(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_UNKNOWN);
    CHECK(has_row(&r, "GICR GICC FRAMES=7 OF 12"));
    CHECK(!read_at(&w->dm, pcpu_base(8) + 0xFFE8));
    w_free(w);

    /* The distributor faults: no frame is looked at. */
    pcpu_default(&s, 12, 4);
    pcpu_world(w, &s, 4, NULL);
    w->dm.fault_at[w->dm.nfault_at++] = GICD + 0x0004;
    tests_run_gic(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_UNKNOWN);
    CHECK(!read_at(&w->dm, pcpu_base(0) + 0xFFE8));
    w_free(w);

    /* As many CPUs as the parser keeps: every window is admitted, every frame read. One more is
     * counted as dropped, which is a frame nobody listed, so the match is not made. */
    pcpu_default(&s, PCPU_MAX, 4);
    pcpu_world(w, &s, 4, NULL);
    nw = pcpu_windows(wins, PCPU_MAX);
    tests_run_gic(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_PASS);
    CHECK(has_row(&r, "GICR GICC FRAMES=32 OF 32"));
    CHECK_EQ(stray_reads(&w->dm, wins, nw), 0);
    CHECK_EQ(r.overflow, 0);
    w_free(w);
    pcpu_default(&s, PCPU_MAX + 1, 4);
    pcpu_world(w, &s, 4, NULL);
    tests_run_gic(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_UNKNOWN);
    CHECK(has_row(&r, "GICR GICC FRAMES=32 OF 32"));
    CHECK(has_row(&r, "CPU MATCH SKIPPED: FRAME NOT READ"));
    w_free(w);

    /* An ITS refused does not stop the frames being read, and is itself named. */
    pcpu_default(&s, 12, 4);
    pcpu_world(w, &s, 4, NULL);
    w_map(w, 7, ITS, 0x10);
    tests_run_gic(&w->ctx, &r);
    CHECK_EQ(r.verdict, TV_UNKNOWN);
    CHECK(has_row(&r, "ITS0 REFUSED: MAP SAYS NOT MMIO"));
    CHECK(has_row(&r, "GICR GICC FRAMES=12 OF 12"));
    w_free(w);
    free(w);
}
