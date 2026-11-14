/* SPDX-License-Identifier: MIT */
/* Host tests of T06 in its third version (probes/uefi/tests/t_pcie.c).
 *
 * T06 reads the ECAM window of a root complex only when the firmware's own PCI I/O, collected before
 * ExitBootServices, showed an NVMe function behind it and a second source agrees, and it reads no
 * other window of the MCFG. The reason is an observation: on the Surface Pro 11 the first version
 * walked every window, a read of segment 3 never returned and the board reset itself (OBSERVED,
 * boot 3). So the properties below are properties of the addresses read, and a device model
 * records every one of them: the tests compare the recorded set with the set the evidence justifies,
 * and a defect that reads one dword more or one window more is a failure whatever the verdict.
 *
 * The MCFG used here has eight entries shaped like the unit's (segments 0 to 7, three windows of
 * 256 buses and five of two): the segment numbers and bases are plain numbers; no table bytes of
 * any machine are in this file. The register images are synthetic fixtures, none a measurement.
 */
#include "xt06.h"

/* ---- The recorders: what T06 asked the image to show before it read ---- */

#define REC_MAX 8u

typedef struct {
    const devmodel *dm;
    win bar[4];
    unsigned nbar;
    unsigned calls;
    unsigned reads[REC_MAX];     /* reads made when the call came */
    unsigned bar_reads[REC_MAX]; /* of them, reads inside an NVMe BAR window */
    unsigned seconds[REC_MAX];
    char note[REC_MAX][48];
    int fail_call; /* 1-based number of the call that fails, 0 for none */
    unsigned prog_calls;
    char prog[REC_MAX][48];
    unsigned prog_reads[REC_MAX];
} rec;

static rec g_rec;

static unsigned reads_in(const devmodel *d, uint64_t base, uint64_t len)
{
    unsigned n = 0;

    for (unsigned i = 0; i < d->nlog; i++)
        n += d->log[i] >= base && d->log[i] < base + len;
    return n;
}

static unsigned reads_in_bars(const rec *p)
{
    unsigned n = 0;

    for (unsigned k = 0; k < p->nbar; k++)
        n += reads_in(p->dm, p->bar[k].base, p->bar[k].len);
    return n;
}

static int ph_cb(void *ctx, const char *text, unsigned seconds)
{
    rec *p = ctx;
    unsigned i = p->calls++;

    if (i < REC_MAX) {
        p->reads[i] = p->dm->nlog;
        p->bar_reads[i] = reads_in_bars(p);
        p->seconds[i] = seconds;
        snprintf(p->note[i], sizeof p->note[i], "%s", text);
    }
    return p->fail_call == (int)(i + 1) ? TESTS_PHOTO_STALLED : 0;
}

static void prog_cb(void *ctx, const char *text)
{
    rec *p = ctx;
    unsigned i = p->prog_calls++;

    if (i < REC_MAX) {
        p->prog_reads[i] = p->dm->nlog;
        snprintf(p->prog[i], sizeof p->prog[i], "%s", text);
    }
}

/* ---- The fixture ---- */

typedef struct {
    world w;
    tests_pciio pre;
    tests_result r;
} fx;

static fx *fx_new(const mcfg_row *rows, unsigned n)
{
    fx *f = xalloc(sizeof *f);
    tbl t;

    w_init(&f->w);
    t_start(&t, "MCFG", 8);
    for (unsigned i = 0; i < n; i++)
        mcfg_entry(&t, rows[i].base, rows[i].seg, rows[i].start, rows[i].end);
    t_seal(&t);
    w_table(&f->w, 1, "MCFG", &t);
    memset(&g_rec, 0, sizeof g_rec);
    g_rec.dm = &f->w.dm;
    f->w.ctx.pciio = &f->pre;
    f->w.ctx.photograph = ph_cb;
    f->w.ctx.photograph_ctx = &g_rec;
    f->w.ctx.progress = prog_cb;
    f->w.ctx.progress_ctx = &g_rec;
    return f;
}

static void fx_free(fx *f)
{
    w_free(&f->w);
    free(f);
}

static void fx_run(fx *f)
{
    memset(&f->r, 0xA5, sizeof f->r);
    tests_run_pcie(&f->w.ctx, &f->r);
}

/* Reads of the model that lie in the window of any MCFG entry whose segment is not one of the
 * allowed ones: the reads that the evidence does not justify. */
static unsigned unjustified_window_reads(const fx *f, const mcfg_row *rows, unsigned n,
                                         const unsigned *allowed, unsigned nallowed)
{
    unsigned bad = 0;

    for (unsigned i = 0; i < n; i++) {
        int ok = 0;

        for (unsigned k = 0; k < nallowed; k++)
            ok |= rows[i].seg == allowed[k];
        if (!ok)
            bad += reads_in(&f->w.dm, rows[i].base, ((uint64_t)rows[i].end + 1u) << 20);
    }
    return bad;
}

/* The set of controller register dwords read in a BAR window, as a bit set over offset / 4, and
 * how many reads that was. */
static unsigned nvme_reads(const devmodel *d, uint64_t bar, unsigned *count)
{
    unsigned set = 0;

    *count = 0;
    for (unsigned i = 0; i < d->nlog; i++)
        if (d->log[i] >= bar && d->log[i] < bar + NVME_WINDOW) {
            set |= 1u << ((d->log[i] - bar) / 4u);
            (*count)++;
        }
    return set;
}

/* Nothing outside bus 0 and 1, device 0, function 0 of one ECAM window was read, and nothing but
 * configuration dwords below 0x54. */
static int walk_is_narrow(const devmodel *d, uint64_t ecam)
{
    for (unsigned i = 0; i < d->nlog; i++) {
        uint64_t off;

        if (d->log[i] < ecam || d->log[i] >= ecam + (2u << 20))
            continue;
        off = d->log[i] - ecam;
        if (((off >> 12) & 7u) != 0 || ((off >> 15) & 0x1Fu) != 0 || (off & 0xFFFu) >= 0x54u)
            return 0;
    }
    return 1;
}

/* ---- The segment the firmware used, and only that one ---- */

static void t6_firmware_nvme(void)
{
    fx *f = fx_new(UNIT_MCFG, UNIT_ENTRIES);
    segcfg c = good_seg(BAR_SEG6);
    const unsigned seg6[] = {6};
    win allowed[2] = {{ECAM_SEG6, 2u << 20}, {BAR_SEG6, NVME_WINDOW}};
    unsigned count, set;

    section("t06 firmware nvme");
    seg_devices(&f->w.dm, ECAM_SEG6, BAR_SEG6, &c);
    pre_root_port(&f->pre, 6, 6);
    pre_nvme(&f->pre, 6, 6, &c);
    g_rec.bar[g_rec.nbar].base = BAR_SEG6;
    g_rec.bar[g_rec.nbar++].len = NVME_WINDOW;
    fx_run(f);

    CHECK_EQ(f->r.verdict, TV_PASS);
    CHECK_EQ(f->r.label, TL_OBSERVED);
    CHECK_EQ(f->r.overflow, 0);
    CHECK(f->r.nrows <= TESTS_DETAIL_MAX);
    CHECK(x_rows_valid(&f->r));
    /* Every read lies in the window of segment 6 or in the BAR the endpoint of segment 6 shows,
     * and no other window of the MCFG was touched, whatever the model would have answered. */
    CHECK(f->w.dm.nlog > 0);
    CHECK_EQ(stray_reads(&f->w.dm, allowed, 2), 0);
    CHECK_EQ(unjustified_window_reads(f, UNIT_MCFG, UNIT_ENTRIES, seg6, 1), 0);
    CHECK_EQ(f->w.dm.unlisted, 0);
    CHECK_EQ(f->w.dm.faults, 0);
    CHECK(walk_is_narrow(&f->w.dm, ECAM_SEG6));
    CHECK(!read_at(&f->w.dm, ECAM_SEG6 + (1u << 15))); /* a root port: device 1 of bus 0 is not read */
    /* The controller: exactly the ten dwords of the seven registers, each once. */
    set = nvme_reads(&f->w.dm, BAR_SEG6, &count);
    CHECK_EQU(set, NVME_READ_SET);
    CHECK_EQ(count, 10);
    CHECK(!read_at(&f->w.dm, BAR_SEG6 + 0x0C));
    CHECK(!read_at(&f->w.dm, BAR_SEG6 + 0x18));
    CHECK(!read_at(&f->w.dm, BAR_SEG6 + 0x20));
    CHECK(!read_at(&f->w.dm, BAR_SEG6 + 0x1000));
    /* The rows say what was done and what was seen on each side. */
    CHECK(has_row(&f->r, "FW NVME FUNCTIONS=1"));
    CHECK(has_row(&f->r, "NVME0 SEG=6 UID=6 01:00.0"));
    CHECK(has_row(&f->r, "SCANNED SEGMENTS=1 MCFG=8"));
    CHECK(has_row(&f->r, "W6 LINK GEN3 X4 DLLLA=1"));
    CHECK(has_row(&f->r, "MATCHES FIRMWARE VIEW=YES"));
    CHECK(has_row(&f->r, "CAP VS EQUAL=YES"));
    CHECK(has_row(&f->r, "CAP PRE=004008200107FFFF"));
    CHECK(has_row(&f->r, "CAP EL2=004008200107FFFF"));
    CHECK(has_row(&f->r, "VS PRE=00020000 EL2=00020000"));
    CHECK(has_row(&f->r, "CC PRE=00460001 EL2=00460001"));
    CHECK(has_row(&f->r, "CSTS PRE=00000001 EL2=00000001"));
    CHECK(has_row(&f->r, "AQA PRE=001F001F EL2=001F001F"));
    CHECK(has_row(&f->r, "ASQ PRE=00000001F6A00000"));
    CHECK(has_row(&f->r, "ACQ EL2=00000001F6A01000"));
    CHECK(has_row(&f->r, "FAULTS=0"));
    CHECK(!has_row(&f->r, "WHY:"));
    CHECK(!has_row(&f->r, "NVME CAP="));
    fx_free(f);

    /* The same machine with the decoys the unit has: the other windows answer too, and one of
     * them is the segment-3 window that never returned. Nothing there is read. */
    f = fx_new(UNIT_MCFG, UNIT_ENTRIES);
    seg_devices(&f->w.dm, ECAM_SEG6, BAR_SEG6, &c);
    for (unsigned i = 0; i < UNIT_ENTRIES; i++)
        if (UNIT_MCFG[i].seg != 6) {
            put_cfg(&f->w.dm, UNIT_MCFG[i].base, 0, 0, 0x00, 0x01201234u);
            put_cfg(&f->w.dm, UNIT_MCFG[i].base, 0, 0, 0x0C, 1u << 16);
        }
    pre_root_port(&f->pre, 6, 6);
    pre_nvme(&f->pre, 6, 6, &c);
    fx_run(f);
    CHECK_EQ(f->r.verdict, TV_PASS);
    CHECK_EQ(unjustified_window_reads(f, UNIT_MCFG, UNIT_ENTRIES, seg6, 1), 0);
    CHECK_EQ(reads_in(&f->w.dm, 0x740000000ull, 2u << 20), 0);
    fx_free(f);

    /* The segment is looked up, never used as an index: with the entries in another order the
     * window of segment 6 is still the one that is read. */
    {
        mcfg_row shuffled[UNIT_ENTRIES];

        for (unsigned i = 0; i < UNIT_ENTRIES; i++)
            shuffled[i] = UNIT_MCFG[UNIT_ENTRIES - 1u - i];
        f = fx_new(shuffled, UNIT_ENTRIES);
        seg_devices(&f->w.dm, ECAM_SEG6, BAR_SEG6, &c);
        pre_nvme(&f->pre, 6, 6, &c);
        fx_run(f);
        CHECK_EQ(f->r.verdict, TV_PASS);
        CHECK_EQ(stray_reads(&f->w.dm, allowed, 2), 0);
        CHECK(has_row(&f->r, "SCANNED SEGMENTS=1 MCFG=8"));
        CHECK(read_at(&f->w.dm, ECAM_SEG6));
        fx_free(f);
    }

    /* A function list longer than the table can hold, with the count of a corrupt record: the
     * loop is bounded by the table, never by the count. */
    f = fx_new(UNIT_MCFG, UNIT_ENTRIES);
    seg_devices(&f->w.dm, ECAM_SEG6, BAR_SEG6, &c);
    pre_nvme(&f->pre, 6, 6, &c);
    f->pre.n = 200;
    fx_run(f);
    CHECK_EQ(stray_reads(&f->w.dm, allowed, 2), 0);
    CHECK(f->r.verdict != TV_NOT_RUN);
    fx_free(f);
}

/* ---- Nothing to touch: no read, no wait ---- */

typedef struct {
    const char *what;
    const char *row; /* a row the result must carry */
} idle_case;

static void idle_setup(fx *f, int variant, const segcfg *c)
{
    tests_pciio_fn *n;

    seg_devices(&f->w.dm, ECAM_SEG6, BAR_SEG6, c);
    switch (variant) {
    case 0: /* no table at all */
        f->w.ctx.pciio = NULL;
        break;
    case 1: /* a table nobody filled */
        break;
    case 2: /* the protocol listed no function */
        f->pre.state = TESTS_PCIIO_OK;
        break;
    case 3: /* the protocol is not there */
        f->pre.state = TESTS_PCIIO_NO_PROTOCOL;
        f->pre.status = EFI_NOT_FOUND;
        break;
    case 4: /* functions, none of them NVMe */
        pre_root_port(&f->pre, 6, 6);
        pre_root_port(&f->pre, 5, 5);
        break;
    case 5: /* an NVMe function in a segment the MCFG does not have */
        pre_nvme(&f->pre, 9, 9, c);
        break;
    case 6: /* the root bridge's UID is another segment's: the one that never answered */
        pre_nvme(&f->pre, 6, 3, c);
        break;
    case 7: /* no device path */
        n = pre_nvme(&f->pre, 6, 6, c);
        n->have_root = 0;
        break;
    case 8: /* the first node is not a PCI root bridge */
        n = pre_nvme(&f->pre, 6, 6, c);
        n->root_hid = 0x0A0141D0u;
        break;
    case 9: /* the record says the location could not be read */
        n = pre_nvme(&f->pre, 6, 6, c);
        n->located = 0;
        break;
    case 10: /* the record is partial */
        n = pre_nvme(&f->pre, 6, 6, c);
        n->cfg_ok = 0;
        break;
    default:
        pre_nvme(&f->pre, 6, 6, c);
        break;
    }
}

static const idle_case IDLE[] = {
    {"no table", "NO FIRMWARE NVME: NOTHING TOUCHED"},
    {"not collected", "NO FIRMWARE NVME: NOTHING TOUCHED"},
    {"no function", "NO FIRMWARE NVME: NOTHING TOUCHED"},
    {"no protocol", "PCI I/O: NO PROTOCOL"},
    {"no nvme", "PCI I/O: 2 FUNCTIONS, NONE NVME"},
    {"segment not in mcfg", "NVME0 SKIP: SEGMENT NOT IN MCFG"},
    {"uid differs", "NVME0 SKIP: UID DIFFERS FROM SEGMENT"},
    {"no uid", "NVME0 SKIP: UID NOT AVAILABLE"},
    {"not a root", "NVME0 SKIP: ROOT NOT A PCI ROOT"},
    {"not located", "NVME0 SKIP: RECORD INCOMPLETE"},
    {"partial record", "NVME0 SKIP: RECORD INCOMPLETE"},
};

static void t6_nothing_touched(void)
{
    segcfg c = good_seg(BAR_SEG6);
    unsigned n = sizeof IDLE / sizeof IDLE[0];

    section("t06 nothing touched");
    for (unsigned v = 0; v < n; v++) {
        fx *f = fx_new(UNIT_MCFG, UNIT_ENTRIES);

        idle_setup(f, (int)v, &c);
        fx_run(f);
        if (f->w.dm.nlog != 0 || f->r.verdict != TV_NOT_RUN)
            printf("  idle case '%s' read %u addresses, verdict %d\n", IDLE[v].what, f->w.dm.nlog,
                   f->r.verdict);
        CHECK_EQ(f->w.dm.nlog, 0);
        CHECK_EQ(f->r.verdict, TV_NOT_RUN);
        CHECK_EQ(g_rec.calls, 0);      /* nothing to touch, so nothing to photograph or wait for */
        CHECK_EQ(g_rec.prog_calls, 0);
        CHECK(has_row(&f->r, IDLE[v].row));
        CHECK(x_rows_valid(&f->r));
        fx_free(f);
    }

    /* A defect in the MCFG: the segment cannot be pinned to one window, so none is read. */
    {
        mcfg_row dup[UNIT_ENTRIES], late[UNIT_ENTRIES], many[TESTS_MAX_ECAM + 2];

        memcpy(dup, UNIT_MCFG, sizeof dup);
        dup[7].seg = 6; /* segment 6 twice, segment 7 not at all */
        memcpy(late, UNIT_MCFG, sizeof late);
        late[6].start = 1;
        for (unsigned i = 0; i < TESTS_MAX_ECAM + 2u; i++) {
            many[i] = UNIT_MCFG[i % UNIT_ENTRIES];
            many[i].seg = i;
            many[i].base = 0x7000000000ull + 0x10000000ull * i;
        }
        many[6].base = ECAM_SEG6;
        for (int variant = 0; variant < 3; variant++) {
            fx *f = variant == 0   ? fx_new(dup, UNIT_ENTRIES)
                    : variant == 1 ? fx_new(late, UNIT_ENTRIES)
                                   : fx_new(many, TESTS_MAX_ECAM + 2u);
            const char *row = variant == 0   ? "NVME0 SKIP: SEGMENT TWICE IN MCFG"
                              : variant == 1 ? "NVME0 SKIP: MCFG START BUS NOT 0"
                                             : "NVME0 SKIP: MCFG HAS TOO MANY ENTRIES";

            seg_devices(&f->w.dm, ECAM_SEG6, BAR_SEG6, &c);
            pre_nvme(&f->pre, 6, 6, &c);
            fx_run(f);
            CHECK_EQ(f->w.dm.nlog, 0);
            CHECK_EQ(f->r.verdict, TV_NOT_RUN);
            CHECK_EQ(g_rec.calls, 0);
            CHECK(has_row(&f->r, row));
            fx_free(f);
        }
    }

    /* The record is fine and the MCFG is not there, or holds nothing. */
    {
        fx *f = xalloc(sizeof *f);
        tbl t;

        w_init(&f->w);
        memset(&g_rec, 0, sizeof g_rec);
        g_rec.dm = &f->w.dm;
        f->w.ctx.pciio = &f->pre;
        f->w.ctx.photograph = ph_cb;
        f->w.ctx.photograph_ctx = &g_rec;
        seg_devices(&f->w.dm, ECAM_SEG6, BAR_SEG6, &c);
        pre_nvme(&f->pre, 6, 6, &c);
        fx_run(f);
        CHECK_EQ(f->w.dm.nlog, 0);
        CHECK_EQ(f->r.verdict, TV_NOT_RUN);
        CHECK(has_row(&f->r, "NO USABLE MCFG: NOTHING TOUCHED"));
        w_free(&f->w);
        free(f);

        f = xalloc(sizeof *f);
        w_init(&f->w);
        t_start(&t, "MCFG", 8);
        t_seal(&t);
        w_table(&f->w, 1, "MCFG", &t);
        memset(&g_rec, 0, sizeof g_rec);
        g_rec.dm = &f->w.dm;
        f->w.ctx.pciio = &f->pre;
        f->w.ctx.photograph = ph_cb;
        f->w.ctx.photograph_ctx = &g_rec;
        seg_devices(&f->w.dm, ECAM_SEG6, BAR_SEG6, &c);
        pre_nvme(&f->pre, 6, 6, &c);
        fx_run(f);
        CHECK_EQ(f->w.dm.nlog, 0);
        CHECK_EQ(g_rec.calls, 0);
        CHECK(has_row(&f->r, "NO USABLE MCFG: NOTHING TOUCHED"));
        w_free(&f->w);
        free(f);
    }
}

/* ---- Two root complexes with an NVMe each ---- */

static void t6_two_segments(void)
{
    fx *f = fx_new(UNIT_MCFG, UNIT_ENTRIES);
    segcfg c6 = good_seg(BAR_SEG6), c5 = good_seg(BAR_SEG5);
    const unsigned both[] = {5, 6};
    win allowed[4] = {{ECAM_SEG6, 2u << 20}, {BAR_SEG6, NVME_WINDOW},
                      {ECAM_SEG5, 2u << 20}, {BAR_SEG5, NVME_WINDOW}};
    unsigned count;

    section("t06 two segments");
    seg_devices(&f->w.dm, ECAM_SEG6, BAR_SEG6, &c6);
    seg_devices(&f->w.dm, ECAM_SEG5, BAR_SEG5, &c5);
    pre_nvme(&f->pre, 6, 6, &c6);
    pre_nvme(&f->pre, 5, 5, &c5);
    g_rec.bar[g_rec.nbar].base = BAR_SEG6;
    g_rec.bar[g_rec.nbar++].len = NVME_WINDOW;
    g_rec.bar[g_rec.nbar].base = BAR_SEG5;
    g_rec.bar[g_rec.nbar++].len = NVME_WINDOW;
    fx_run(f);
    CHECK_EQ(f->r.verdict, TV_PASS);
    CHECK_EQ(stray_reads(&f->w.dm, allowed, 4), 0);
    CHECK_EQ(unjustified_window_reads(f, UNIT_MCFG, UNIT_ENTRIES, both, 2), 0);
    CHECK(read_at(&f->w.dm, ECAM_SEG6));
    CHECK(read_at(&f->w.dm, ECAM_SEG5));
    (void)nvme_reads(&f->w.dm, BAR_SEG6, &count);
    CHECK_EQ(count, 10);
    (void)nvme_reads(&f->w.dm, BAR_SEG5, &count);
    CHECK_EQ(count, 10);
    CHECK(has_row(&f->r, "FW NVME FUNCTIONS=2"));
    CHECK(has_row(&f->r, "SCANNED SEGMENTS=2 MCFG=8"));
    CHECK(has_row(&f->r, "SEG5 VIEW=YES CAPVS=OK"));
    CHECK(x_rows_valid(&f->r));
    CHECK_EQ(f->r.overflow, 0);
    /* Two segments are still two waits: one before the first window, one before the first BAR. */
    CHECK_EQ(g_rec.calls, 2);
    CHECK_EQ(g_rec.prog_calls, 2);
    CHECK_STR(g_rec.note[0], "T06 ECAM 0070000000");
    CHECK_STR(g_rec.note[1], "T06 NVME 0078000000");
    CHECK_STR(g_rec.prog[0], "T06 ECAM 007E000000");
    CHECK_STR(g_rec.prog[1], "T06 NVME 0079000000");
    fx_free(f);

    /* One of the two cannot be confirmed (its UID is another segment's): only the other is read,
     * and the verdict says that not everything the firmware knew was checked. */
    f = fx_new(UNIT_MCFG, UNIT_ENTRIES);
    seg_devices(&f->w.dm, ECAM_SEG6, BAR_SEG6, &c6);
    seg_devices(&f->w.dm, ECAM_SEG5, BAR_SEG5, &c5);
    pre_nvme(&f->pre, 6, 6, &c6);
    pre_nvme(&f->pre, 5, 3, &c5);
    fx_run(f);
    CHECK_EQ(stray_reads(&f->w.dm, allowed, 2), 0);
    CHECK_EQ(reads_in(&f->w.dm, ECAM_SEG5, 2u << 20), 0);
    CHECK_EQ(f->r.verdict, TV_UNKNOWN);
    CHECK(has_row(&f->r, "NVME1 SKIP: UID DIFFERS FROM SEGMENT"));
    CHECK(has_row(&f->r, "SCANNED SEGMENTS=1 MCFG=8"));
    fx_free(f);

    /* Two NVMe functions in one segment: the segment is read once. */
    f = fx_new(UNIT_MCFG, UNIT_ENTRIES);
    seg_devices(&f->w.dm, ECAM_SEG6, BAR_SEG6, &c6);
    pre_nvme(&f->pre, 6, 6, &c6);
    pre_nvme(&f->pre, 6, 6, &c6)->bus = 2;
    fx_run(f);
    CHECK(has_row(&f->r, "SCANNED SEGMENTS=1 MCFG=8"));
    CHECK_EQ(stray_reads(&f->w.dm, allowed, 2), 0);
    CHECK_EQ(f->r.verdict, TV_UNKNOWN); /* one of the two functions was not checked */
    fx_free(f);
}

/* ---- The window policy, unchanged: alignment, the memory map, the allow list ---- */

static void t6_window_policy(void)
{
    segcfg c = good_seg(BAR_SEG6);
    fx *f;

    section("t06 window policy");
    /* The strict policy with the map listing both windows as MMIO. */
    f = fx_new(UNIT_MCFG, UNIT_ENTRIES);
    f->w.ctx.require_mmio_map = 1;
    w_map(&f->w, 11, ECAM_SEG6, 0x200);
    w_map(&f->w, 11, BAR_SEG6, 1);
    seg_devices(&f->w.dm, ECAM_SEG6, BAR_SEG6, &c);
    pre_nvme(&f->pre, 6, 6, &c);
    fx_run(f);
    CHECK_EQ(f->r.verdict, TV_PASS);
    CHECK(has_row(&f->r, "MAP STRICT MMIO=2 REFUSED=0"));
    fx_free(f);

    /* Strict, and the map does not list the ECAM window: refused, not one read, no wait. */
    f = fx_new(UNIT_MCFG, UNIT_ENTRIES);
    f->w.ctx.require_mmio_map = 1;
    seg_devices(&f->w.dm, ECAM_SEG6, BAR_SEG6, &c);
    pre_nvme(&f->pre, 6, 6, &c);
    fx_run(f);
    CHECK_EQ(f->w.dm.nlog, 0);
    CHECK_EQ(g_rec.calls, 0);
    CHECK_EQ(f->r.verdict, TV_UNKNOWN);
    CHECK(has_row(&f->r, "W6 REFUSED: NOT MMIO IN MAP"));
    CHECK(has_row(&f->r, "SCANNED SEGMENTS=0 MCFG=8"));
    CHECK(has_row(&f->r, "WHY: A WINDOW WAS REFUSED"));
    CHECK(x_rows_valid(&f->r));
    fx_free(f);

    /* The map calls the window RAM: refused under the default policy too. */
    f = fx_new(UNIT_MCFG, UNIT_ENTRIES);
    w_map(&f->w, 7, ECAM_SEG6, 0x200);
    seg_devices(&f->w.dm, ECAM_SEG6, BAR_SEG6, &c);
    pre_nvme(&f->pre, 6, 6, &c);
    fx_run(f);
    CHECK_EQ(f->w.dm.nlog, 0);
    CHECK_EQ(g_rec.calls, 0);
    CHECK_EQ(f->r.verdict, TV_UNKNOWN);
    CHECK(has_row(&f->r, "W6 REFUSED: MAP SAYS NOT MMIO"));
    fx_free(f);

    /* A base that is not an ECAM base, a zero base and an address no processor issues: each is
     * refused with its reason, and not one read is made. */
    for (int variant = 0; variant < 3; variant++) {
        mcfg_row rows[UNIT_ENTRIES];
        const char *row;

        memcpy(rows, UNIT_MCFG, sizeof rows);
        if (variant == 0) {
            rows[6].base = ECAM_SEG6 + 0x80000ull;
            row = "W6 REFUSED: MISALIGNED";
        } else if (variant == 1) {
            rows[6].base = 0;
            row = "W6 REFUSED: BASE IS ZERO";
        } else {
            rows[6].base = 1ull << 52;
            row = "W6 REFUSED: BAD RANGE";
        }
        f = fx_new(rows, UNIT_ENTRIES);
        seg_devices(&f->w.dm, ECAM_SEG6, BAR_SEG6, &c);
        pre_nvme(&f->pre, 6, 6, &c);
        fx_run(f);
        CHECK_EQ(f->w.dm.nlog, 0);
        CHECK_EQ(g_rec.calls, 0);
        CHECK_EQ(f->r.verdict, TV_UNKNOWN);
        CHECK(has_row(&f->r, row));
        fx_free(f);
    }

    /* There is no 2^36 rule here: a window above it that the policy admits is read. (The unit's
     * segment 2 sits at 0x6000000000; the monitor's limit is not T06's business.) */
    {
        segcfg c2 = good_seg(BAR_SEG2);
        const unsigned seg2[] = {2};
        win allowed[2] = {{ECAM_SEG2, 256u << 20}, {BAR_SEG2, NVME_WINDOW}};

        f = fx_new(UNIT_MCFG, UNIT_ENTRIES);
        seg_devices(&f->w.dm, ECAM_SEG2, BAR_SEG2, &c2);
        pre_nvme(&f->pre, 2, 2, &c2);
        fx_run(f);
        CHECK_EQ(f->r.verdict, TV_PASS);
        CHECK(read_at(&f->w.dm, ECAM_SEG2));
        CHECK_EQ(stray_reads(&f->w.dm, allowed, 2), 0);
        CHECK_EQ(unjustified_window_reads(f, UNIT_MCFG, UNIT_ENTRIES, seg2, 1), 0);
        fx_free(f);
    }

    /* The ECAM window is fine and the BAR is not (RAM in the map, or not MMIO under the strict
     * policy): the configuration reads happen, the controller is not read, and the wait before
     * the BAR is not taken. */
    for (int variant = 0; variant < 2; variant++) {
        f = fx_new(UNIT_MCFG, UNIT_ENTRIES);
        if (variant == 0) {
            w_map(&f->w, 7, BAR_SEG6, 1);
        } else {
            f->w.ctx.require_mmio_map = 1;
            w_map(&f->w, 11, ECAM_SEG6, 0x200);
        }
        seg_devices(&f->w.dm, ECAM_SEG6, BAR_SEG6, &c);
        pre_nvme(&f->pre, 6, 6, &c);
        g_rec.bar[g_rec.nbar].base = BAR_SEG6;
        g_rec.bar[g_rec.nbar++].len = NVME_WINDOW;
        fx_run(f);
        CHECK(read_at(&f->w.dm, ECAM_SEG6));
        CHECK_EQ(reads_in(&f->w.dm, BAR_SEG6, NVME_WINDOW), 0);
        CHECK_EQ(g_rec.calls, 1);
        CHECK_STR(g_rec.note[0], "T06 ECAM 0070000000");
        CHECK(has_row(&f->r, "NVME BAR0 REFUSED:"));
        CHECK_EQ(f->r.verdict, TV_UNKNOWN);
        fx_free(f);
    }

    /* One of two windows refused: the other is read, the refused one is not. */
    {
        segcfg c5 = good_seg(BAR_SEG5);
        win allowed[2] = {{ECAM_SEG5, 2u << 20}, {BAR_SEG5, NVME_WINDOW}};

        f = fx_new(UNIT_MCFG, UNIT_ENTRIES);
        w_map(&f->w, 7, ECAM_SEG6, 0x200);
        seg_devices(&f->w.dm, ECAM_SEG6, BAR_SEG6, &c);
        seg_devices(&f->w.dm, ECAM_SEG5, BAR_SEG5, &c5);
        pre_nvme(&f->pre, 6, 6, &c);
        pre_nvme(&f->pre, 5, 5, &c5);
        fx_run(f);
        CHECK_EQ(stray_reads(&f->w.dm, allowed, 2), 0);
        CHECK(read_at(&f->w.dm, ECAM_SEG5));
        CHECK_EQ(f->r.verdict, TV_UNKNOWN);
        CHECK(has_row(&f->r, "W6 REFUSED: MAP SAYS NOT MMIO"));
        CHECK(has_row(&f->r, "SCANNED SEGMENTS=1 MCFG=8"));
        fx_free(f);
    }
}

/* ---- What EL2 sees against what the firmware saw ---- */

typedef struct {
    const char *what;
    int verdict;
    int bar_reads; /* the controller registers are read */
    const char *row;
} view_case;

static void view_setup(fx *f, int variant, segcfg *c)
{
    segcfg fw = *c;

    switch (variant) {
    case 0: /* another vendor at EL2 */
        c->ep_id = 0x00111234u;
        break;
    case 1: /* another class code */
        c->ep_class = 0x01060101u;
        break;
    case 2: /* another BAR0 base, still inside the bridge's window */
        c->bar0_lo = (uint32_t)(BAR_SEG6 + 0x4000u) | 0x4u;
        break;
    case 3: /* CAP differs */
        c->cap_lo ^= 0x1u;
        break;
    case 4: /* CAP, the upper half, differs */
        c->cap_hi ^= 0x100u;
        break;
    case 5: /* VS differs */
        c->vs = 0x00020100u;
        break;
    case 6: /* what the firmware's ExitBootServices handler may have changed */
        c->cc = 0;
        c->csts = 0;
        c->aqa = 0;
        c->asq_lo = 0;
        c->acq_hi = 0;
        break;
    default:
        break;
    }
    seg_devices(&f->w.dm, ECAM_SEG6, BAR_SEG6, c);
    if (variant == 2)
        dm_set(&f->w.dm, BAR_SEG6 + 0x4000u, 0); /* the model has registers there too */
    pre_nvme(&f->pre, 6, 6, &fw);
}

static const view_case VIEWS[] = {
    {"vendor differs", TV_FAIL, 0, "MATCHES FIRMWARE VIEW=NO"},
    {"class differs", TV_FAIL, 0, "MATCHES FIRMWARE VIEW=NO"},
    {"bar0 differs", TV_FAIL, 0, "NVME REGS NOT READ: VIEW DIFFERS"},
    {"cap low differs", TV_FAIL, 1, "CAP VS EQUAL=NO"},
    {"cap high differs", TV_FAIL, 1, "CAP VS EQUAL=NO"},
    {"vs differs", TV_FAIL, 1, "CAP VS EQUAL=NO"},
    {"cc csts aqa asq acq differ", TV_PASS, 1, "CAP VS EQUAL=YES"},
};

static void t6_view_and_registers(void)
{
    section("t06 view and registers");
    for (unsigned v = 0; v < sizeof VIEWS / sizeof VIEWS[0]; v++) {
        fx *f = fx_new(UNIT_MCFG, UNIT_ENTRIES);
        segcfg c = good_seg(BAR_SEG6);
        win allowed[3] = {{ECAM_SEG6, 2u << 20}, {BAR_SEG6, NVME_WINDOW},
                          {BAR_SEG6 + 0x4000u, NVME_WINDOW}};
        unsigned count;

        view_setup(f, (int)v, &c);
        g_rec.bar[g_rec.nbar].base = BAR_SEG6;
        g_rec.bar[g_rec.nbar++].len = NVME_WINDOW;
        g_rec.bar[g_rec.nbar].base = BAR_SEG6 + 0x4000u;
        g_rec.bar[g_rec.nbar++].len = NVME_WINDOW;
        fx_run(f);
        if (f->r.verdict != VIEWS[v].verdict)
            printf("  view case '%s': verdict %d\n", VIEWS[v].what, f->r.verdict);
        CHECK_EQ(f->r.verdict, VIEWS[v].verdict);
        CHECK(has_row(&f->r, VIEWS[v].row));
        CHECK(x_rows_valid(&f->r));
        CHECK_EQ(stray_reads(&f->w.dm, allowed, 2), 0);
        (void)nvme_reads(&f->w.dm, BAR_SEG6, &count);
        CHECK_EQ(count, VIEWS[v].bar_reads ? 10 : 0);
        CHECK_EQ(reads_in(&f->w.dm, BAR_SEG6 + 0x4000u, NVME_WINDOW), 0);
        /* The wait before the controller is taken only when the controller is read. */
        CHECK_EQ(g_rec.calls, VIEWS[v].bar_reads ? 2 : 1);
        if (v == 6) {
            CHECK(has_row(&f->r, "CC PRE=00460001 EL2=00000000"));
            CHECK(has_row(&f->r, "CSTS PRE=00000001 EL2=00000000"));
            CHECK(has_row(&f->r, "AQA PRE=001F001F EL2=00000000"));
            CHECK(has_row(&f->r, "ASQ EL2=0000000100000000"));
            CHECK(!has_row(&f->r, "WHY:"));
        } else {
            CHECK(has_row(&f->r, "WHY:"));
        }
        fx_free(f);
    }

    {
        fx *f;
        segcfg c = good_seg(BAR_SEG6);
        win allowed[2] = {{ECAM_SEG6, 2u << 20}, {BAR_SEG6, NVME_WINDOW}};

        /* A read of the controller that faults: UNKNOWN, with the fault counted. */
        f = fx_new(UNIT_MCFG, UNIT_ENTRIES);
        seg_devices(&f->w.dm, ECAM_SEG6, BAR_SEG6, &c);
        pre_nvme(&f->pre, 6, 6, &c);
        f->w.dm.fault_at[f->w.dm.nfault_at++] = BAR_SEG6 + 0x1C;
        fx_run(f);
        CHECK_EQ(f->r.verdict, TV_UNKNOWN);
        CHECK(f->w.dm.faults >= 1);
        CHECK(has_row(&f->r, "NVME REGS: READ FAULTED"));
        CHECK(has_row(&f->r, "WHY: A READ FAULTED"));
        CHECK_EQ(stray_reads(&f->w.dm, allowed, 2), 0);
        fx_free(f);

        /* A configuration read that faults, in the endpoint: the controller is not read. */
        f = fx_new(UNIT_MCFG, UNIT_ENTRIES);
        seg_devices(&f->w.dm, ECAM_SEG6, BAR_SEG6, &c);
        pre_nvme(&f->pre, 6, 6, &c);
        f->w.dm.fault_at[f->w.dm.nfault_at++] = ECAM_SEG6 + (1u << 20) + 0x08;
        fx_run(f);
        CHECK_EQ(f->r.verdict, TV_UNKNOWN);
        CHECK_EQ(reads_in(&f->w.dm, BAR_SEG6, NVME_WINDOW), 0);
        fx_free(f);

        /* Everything faults: nothing is learnt and the verdict says so. */
        f = fx_new(UNIT_MCFG, UNIT_ENTRIES);
        seg_devices(&f->w.dm, ECAM_SEG6, BAR_SEG6, &c);
        pre_nvme(&f->pre, 6, 6, &c);
        f->w.dm.fault_all = 1;
        fx_run(f);
        CHECK_EQ(f->r.verdict, TV_UNKNOWN);
        CHECK_EQ(stray_reads(&f->w.dm, allowed, 2), 0);
        CHECK_EQ(reads_in(&f->w.dm, BAR_SEG6, NVME_WINDOW), 0);
        fx_free(f);

        /* The link is down at EL2: the endpoint does not answer, the root port still does. */
        f = fx_new(UNIT_MCFG, UNIT_ENTRIES);
        seg_devices(&f->w.dm, ECAM_SEG6, BAR_SEG6, &c);
        put_cfg(&f->w.dm, ECAM_SEG6, 1, 0, 0x00, 0xFFFFFFFFu);
        pre_nvme(&f->pre, 6, 6, &c);
        fx_run(f);
        CHECK_EQ(f->r.verdict, TV_FAIL);
        CHECK(has_row(&f->r, "WHY: NO ENDPOINT AT EL2"));
        CHECK_EQ(reads_in(&f->w.dm, BAR_SEG6, NVME_WINDOW), 0);
        fx_free(f);

        /* Memory decode off in the endpoint or in the bridge: the controller is not read. */
        for (int variant = 0; variant < 2; variant++) {
            segcfg off = c;

            if (variant == 0)
                off.ep_cmd = 0x0004;
            else
                off.bridge_cmd = 0x0004;
            f = fx_new(UNIT_MCFG, UNIT_ENTRIES);
            seg_devices(&f->w.dm, ECAM_SEG6, BAR_SEG6, &off);
            pre_nvme(&f->pre, 6, 6, &c);
            fx_run(f);
            CHECK_EQ(reads_in(&f->w.dm, BAR_SEG6, NVME_WINDOW), 0);
            CHECK_EQ(g_rec.calls, 1);
            CHECK(has_row(&f->r, "NVME REGS NOT READ: DECODE OFF") ||
                  has_row(&f->r, "NVME REGS NOT READ: VIEW DIFFERS"));
            CHECK(f->r.verdict != TV_PASS);
            fx_free(f);
        }

        /* The firmware's own read of the registers failed: EL2 reads them, but there is nothing
         * to compare with, and the verdict says that. */
        f = fx_new(UNIT_MCFG, UNIT_ENTRIES);
        seg_devices(&f->w.dm, ECAM_SEG6, BAR_SEG6, &c);
        pre_nvme(&f->pre, 6, 6, &c)->regs_state = TESTS_PCIIO_REGS_DECODE_OFF;
        fx_run(f);
        CHECK_EQ(f->r.verdict, TV_UNKNOWN);
        CHECK(has_row(&f->r, "CAP VS EQUAL=N/A"));
        CHECK(has_row(&f->r, "CC PRE=N/A EL2=00460001"));
        CHECK(has_row(&f->r, "WHY: NVME REGISTERS NOT READ"));
        fx_free(f);

        /* A function at EL2 that is a plain endpoint at a bus the firmware has no NVMe at. */
        f = fx_new(UNIT_MCFG, UNIT_ENTRIES);
        seg_devices(&f->w.dm, ECAM_SEG6, BAR_SEG6, &c);
        pre_nvme(&f->pre, 6, 6, &c)->bus = 3;
        fx_run(f);
        CHECK(f->r.verdict != TV_PASS);
        CHECK_EQ(reads_in(&f->w.dm, BAR_SEG6, NVME_WINDOW), 0);
        fx_free(f);
    }
}

/* ---- The photograph before a read that may not return ---- */

static void t6_photograph(void)
{
    segcfg c = good_seg(BAR_SEG6);
    win allowed[2] = {{ECAM_SEG6, 2u << 20}, {BAR_SEG6, NVME_WINDOW}};
    fx *f;
    unsigned count;

    section("t06 photograph");
    /* The length of the wait is one named constant, and it is what T06 asks for. */
    CHECK_EQ(TESTS_PHOTOGRAPH_WAIT_SECONDS, 8);
    f = fx_new(UNIT_MCFG, UNIT_ENTRIES);
    seg_devices(&f->w.dm, ECAM_SEG6, BAR_SEG6, &c);
    pre_nvme(&f->pre, 6, 6, &c);
    g_rec.bar[g_rec.nbar].base = BAR_SEG6;
    g_rec.bar[g_rec.nbar++].len = NVME_WINDOW;
    fx_run(f);
    CHECK_EQ(f->r.verdict, TV_PASS);
    CHECK_EQ(g_rec.calls, 2);
    CHECK_EQ(g_rec.seconds[0], WAIT_S);
    CHECK_EQ(g_rec.seconds[1], WAIT_S);
    /* The first wait comes before the first read of anything; the second after the whole walk of
     * the window and before the first read of the controller. */
    CHECK_EQ(g_rec.reads[0], 0);
    CHECK_EQ(g_rec.bar_reads[0], 0);
    CHECK(g_rec.reads[1] > 0);
    CHECK_EQ(g_rec.bar_reads[1], 0);
    CHECK(g_rec.reads[1] < f->w.dm.nlog);
    (void)nvme_reads(&f->w.dm, BAR_SEG6, &count);
    CHECK_EQ(count, 10);
    CHECK_STR(g_rec.note[0], "T06 ECAM 0070000000");
    CHECK_STR(g_rec.note[1], "T06 NVME 0078000000");
    CHECK_EQ(g_rec.prog_calls, 0); /* the photograph's own footer is the note */
    CHECK(strlen(g_rec.note[0]) + strlen("PHOTO NOW:  IN 8 S") <= 38);
    CHECK(strlen(g_rec.note[1]) + strlen("PHOTO NOW:  IN 8 S") <= 38);
    fx_free(f);

    /* The photograph cannot be made before the first window: nothing at all is read. */
    f = fx_new(UNIT_MCFG, UNIT_ENTRIES);
    seg_devices(&f->w.dm, ECAM_SEG6, BAR_SEG6, &c);
    pre_nvme(&f->pre, 6, 6, &c);
    g_rec.fail_call = 1;
    fx_run(f);
    CHECK_EQ(f->w.dm.nlog, 0);
    CHECK_EQ(g_rec.calls, 1);
    CHECK_EQ(f->r.verdict, TV_UNKNOWN);
    CHECK(has_row(&f->r, "PHOTOGRAPH WAIT FAILED: CODE=3"));
    CHECK(has_row(&f->r, "WHY: PHOTOGRAPH WAIT FAILED"));
    CHECK(has_row(&f->r, "SCANNED SEGMENTS=0 MCFG=8")); /* the count says what was read, not what was planned */
    CHECK(!has_row(&f->r, "SCANNED SEGMENTS=1"));
    CHECK(x_rows_valid(&f->r));
    fx_free(f);

    /* It cannot be made before the controller: the window was read, the controller was not. */
    f = fx_new(UNIT_MCFG, UNIT_ENTRIES);
    seg_devices(&f->w.dm, ECAM_SEG6, BAR_SEG6, &c);
    pre_nvme(&f->pre, 6, 6, &c);
    g_rec.fail_call = 2;
    fx_run(f);
    CHECK(read_at(&f->w.dm, ECAM_SEG6));
    CHECK_EQ(reads_in(&f->w.dm, BAR_SEG6, NVME_WINDOW), 0);
    CHECK_EQ(stray_reads(&f->w.dm, allowed, 1), 0);
    CHECK_EQ(g_rec.calls, 2);
    CHECK_EQ(f->r.verdict, TV_UNKNOWN);
    CHECK(has_row(&f->r, "PHOTOGRAPH WAIT FAILED: CODE=3"));
    CHECK(f->r.overflow == 0 && x_rows_valid(&f->r));
    fx_free(f);

    /* Without a photograph callback (a host that models no screen) there is no wait, and the
     * footers are the progress notes of the earlier versions. */
    f = fx_new(UNIT_MCFG, UNIT_ENTRIES);
    seg_devices(&f->w.dm, ECAM_SEG6, BAR_SEG6, &c);
    pre_nvme(&f->pre, 6, 6, &c);
    f->w.ctx.photograph = NULL;
    fx_run(f);
    CHECK_EQ(f->r.verdict, TV_PASS);
    CHECK_EQ(g_rec.calls, 0);
    CHECK_EQ(g_rec.prog_calls, 2);
    CHECK_STR(g_rec.prog[0], "T06 ECAM 0070000000");
    CHECK_STR(g_rec.prog[1], "T06 NVME 0078000000");
    CHECK_EQ(g_rec.prog_reads[0], 0);
    CHECK(g_rec.prog_reads[1] > 0);
    fx_free(f);

    /* Neither callback: the test still runs, and still reads exactly what it is justified to. */
    f = fx_new(UNIT_MCFG, UNIT_ENTRIES);
    seg_devices(&f->w.dm, ECAM_SEG6, BAR_SEG6, &c);
    pre_nvme(&f->pre, 6, 6, &c);
    f->w.ctx.photograph = NULL;
    f->w.ctx.progress = NULL;
    fx_run(f);
    CHECK_EQ(f->r.verdict, TV_PASS);
    CHECK_EQ(stray_reads(&f->w.dm, allowed, 2), 0);
    fx_free(f);
}

/* ---- The verdict as a table ---- */

typedef struct {
    tests_pcie_tally t;
    int verdict;
    const char *why; /* NULL for PASS */
} verdict_case;

static void t6_verdicts(void)
{
    static const verdict_case CASES[] = {
        {{0, 0, 0, 0, 0, 0, 0, 0, 0}, TV_NOT_RUN, "NO SEGMENT WAS JUSTIFIED"},
        {{0, 0, 0, 0, 0, 0, 0, 1, 0}, TV_UNKNOWN, "A WINDOW WAS REFUSED"},
        {{0, 0, 0, 0, 0, 0, 0, 0, 1}, TV_UNKNOWN, "PHOTOGRAPH WAIT FAILED"},
        {{0, 1, 0, 0, 0, 0, 0, 0, 0}, TV_NOT_RUN, "NO SEGMENT WAS JUSTIFIED"}, /* faults, no window */
        {{1, 0, 0, 0, 0, 0, 0, 0, 0}, TV_PASS, NULL},
        {{2, 0, 0, 0, 0, 0, 0, 0, 0}, TV_PASS, NULL},
        {{1, 1, 0, 0, 0, 0, 0, 0, 0}, TV_UNKNOWN, "A READ FAULTED"},
        {{1, 1, 1, 0, 0, 0, 0, 0, 0}, TV_UNKNOWN, "A READ FAULTED"}, /* a fault outranks a mismatch */
        {{1, 0, 1, 0, 0, 0, 0, 0, 0}, TV_FAIL, "NO ENDPOINT AT EL2"},
        {{1, 0, 0, 1, 0, 0, 0, 0, 0}, TV_FAIL, "IDS OR BAR0 DIFFER FROM FW"},
        {{1, 0, 0, 0, 1, 0, 0, 0, 0}, TV_FAIL, "CAP OR VS DIFFER FROM FW"},
        {{1, 0, 0, 0, 0, 1, 0, 0, 0}, TV_UNKNOWN, "NVME REGISTERS NOT READ"},
        {{1, 0, 0, 0, 0, 0, 1, 0, 0}, TV_UNKNOWN, "NOT EVERY FW NVME CHECKED"},
        {{1, 0, 0, 0, 0, 0, 0, 1, 0}, TV_UNKNOWN, "NOT EVERY FW NVME CHECKED"},
        {{1, 0, 0, 0, 0, 0, 0, 0, 1}, TV_UNKNOWN, "PHOTOGRAPH WAIT FAILED"},
        {{2, 0, 0, 1, 1, 0, 0, 0, 0}, TV_FAIL, "IDS OR BAR0 DIFFER FROM FW"}, /* the first reason wins */
    };

    section("t06 verdicts");
    for (unsigned i = 0; i < sizeof CASES / sizeof CASES[0]; i++) {
        const char *why = "unset";
        int v = tests_pcie_decide(&CASES[i].t, &why);

        if (v != CASES[i].verdict)
            printf("  verdict case %u: %d, want %d\n", i, v, CASES[i].verdict);
        CHECK_EQ(v, CASES[i].verdict);
        if (CASES[i].why == NULL)
            CHECK(why == NULL);
        else
            CHECK(why != NULL && strcmp(why, CASES[i].why) == 0);
    }
}

void test_x_t06(void)
{
    t6_firmware_nvme();
    t6_nothing_touched();
    t6_two_segments();
    t6_window_policy();
    t6_view_and_registers();
    t6_photograph();
    t6_verdicts();
}
