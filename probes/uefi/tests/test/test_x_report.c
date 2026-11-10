/* SPDX-License-Identifier: MIT */
/* Host tests of the on-screen report (rows, pages, countdown) and of the USB
 * result writer, against an in-memory model of the firmware's file protocol.
 */
#include "xtests.h"

#include "report_x.h"
#include "stick.h"
#include "suite.h"

/* ---- Report ---- */

static void fill_result(tests_result *r, int id, unsigned rows)
{
    static const char *const names[] = {"", "ID DUMP", "NV2 FUNC", "TIMER", "DC ZVA", "GIC MADT",
                                        "PCIE NVME", "PCIE SMMU", "ADSP GLINK", "DRAM MAP"};

    tests_result_init(r, id, names[id], "PROVES: ONE STATEMENT OF EVIDENCE",
                      "DOES NOT PROVE: THE REST OF IT");
    for (unsigned i = 0; i < rows; i++)
        tests_row_text(r, "ROW 0123456789 0123456789 0123456789");
    tests_result_seal(r);
    r->verdict = TV_INFO;
    r->label = TL_OBSERVED;
}

void test_x_report(void)
{
    static tests_result res[TESTS_COUNT + 1];
    static tests_report rep;
    char out[PROBE_LINE_COLS + 1];
    probe_layout l;
    probe_payload k1, h0, h1, ft;

    section("report and layout");
    /* Every test filled to its limit still fits, in full, with nothing flagged. */
    for (int id = 1; id <= (int)TESTS_COUNT; id++)
        fill_result(&res[id], id, TESTS_DETAIL_MAX);
    CHECK_EQ(res[1].nrows, TESTS_DETAIL_MAX);
    CHECK_EQ(res[1].overflow, 0);
    tests_report_build(&rep, res, 0);
    CHECK_EQ(rep.overflowed, 0);
    CHECK_EQ(rep.count, TESTS_REPORT_MAX_ROWS);
    for (unsigned i = 0; i < rep.count; i++) {
        CHECK_EQ(probe_line_format(out, rep.row[i]), 0); /* canonical: upper case, no repeats, <= 38 */
        CHECK(strlen(rep.row[i]) <= PROBE_LINE_PAYLOAD_MAX);
    }
    /* A test that did not run is shown as such, not omitted. */
    for (int id = 1; id <= (int)TESTS_COUNT; id++)
        tests_result_init(&res[id], id, "NAME", "P", "N");
    tests_report_build(&rep, res, 0);
    {
        unsigned not_run = 0;

        for (unsigned i = 0; i < rep.count; i++)
            not_run += strcmp(rep.row[i], "NOT RUN") == 0;
        CHECK_EQ(not_run, TESTS_COUNT);
    }
    /* A model run is labelled as one in the summary of every test. */
    for (int id = 1; id <= (int)TESTS_COUNT; id++)
        fill_result(&res[id], id, 2);
    tests_report_build(&rep, res, 1);
    {
        unsigned labelled = 0;

        for (unsigned i = 0; i < rep.count; i++)
            labelled += strstr(rep.row[i], "OBS-MODEL") != NULL;
        CHECK_EQ(labelled, TESTS_COUNT);
    }
    tests_report_build(&rep, res, 0);
    {
        unsigned labelled = 0;

        for (unsigned i = 0; i < rep.count; i++)
            labelled += strstr(rep.row[i], "OBS-MODEL") != NULL;
        CHECK_EQ(labelled, 0);
    }
    /* A result that overflowed its rows is reported as flagged. */
    res[3].overflow = 1;
    tests_report_build(&rep, res, 0);
    CHECK_EQ(rep.overflowed, 1);

    /* The K1 line follows the NV2 verdict exactly, and nothing else. */
    for (int v = 0; v < TV_COUNT; v++) {
        tests_result r;

        tests_result_init(&r, T_NV2, "NV2 FUNC", "P", "N");
        r.verdict = (uint8_t)v;
        tests_report_k1(&r, k1);
        CHECK_EQ(probe_line_format(out, k1), 0);
        CHECK_EQ(strstr(k1, "GO") != NULL && strstr(k1, "NO-GO") == NULL, v == TV_GO);
        CHECK_EQ(strstr(k1, "NO-GO") != NULL, v == TV_NO_GO);
        CHECK_EQ(strstr(k1, "INCONCLUSIVE") != NULL, v == TV_INCONCLUSIVE);
        if (v != TV_GO && v != TV_NO_GO && v != TV_INCONCLUSIVE)
            CHECK(strstr(k1, "NOT RUN") != NULL);
    }
    tests_report_header0(h0, "ABCDEF01", 0);
    CHECK_EQ(probe_line_format(out, h0), 0);
    CHECK(strstr(h0, "MODEL") == NULL);
    tests_report_header0(h0, "ABCDEF01", 1);
    CHECK(strstr(h0, "MODEL") != NULL);
    tests_report_header1(h1, 2, 3, 12);
    CHECK_STR(h1, "CURRENTEL=2 PAGE 3/12");
    tests_report_header1(h1, -1, 1, 1);
    CHECK(strstr(h1, "CURRENTEL=?") != NULL);
    tests_footer_running(ft, 6, "PCIE NVME");
    CHECK_EQ(probe_line_format(out, ft), 0);
    CHECK_STR(ft, "RUNNING T06 PCIE NVME");

    /* Layout: the report fits at the smallest scale on the small QEMU display, a
     * hostile geometry is refused, and every layout lies inside its framebuffer. */
    {
        static const struct {
            uint32_t w, h;
        } screens[] = {{800, 600}, {1024, 768}, {1920, 1080}, {1920, 1200}, {2880, 1920}, {3840, 2160}};

        for (unsigned i = 0; i < sizeof screens / sizeof screens[0]; i++) {
            int rc = tests_layout_choose(screens[i].w, screens[i].h, TESTS_REPORT_MAX_ROWS, 32, &l);

            CHECK_EQ(rc, PROBE_LAYOUT_OK);
            CHECK_EQ(tests_layout_fits(&l, screens[i].w, screens[i].h), 0);
            CHECK(l.pages * l.cols * l.body_rows >= TESTS_REPORT_MAX_ROWS);
            CHECK(l.pages <= 32);
            CHECK(l.scale >= PROBE_LAYOUT_MIN_SCALE);
        }
    }
    CHECK_EQ(tests_layout_choose(800, 600, TESTS_REPORT_MAX_ROWS, 32, &l), PROBE_LAYOUT_OK);
    CHECK(l.pages > PROBE_LAYOUT_MAX_PAGES); /* beyond the probe's own eight pages */
    CHECK_EQ(tests_layout_choose(800, 600, TESTS_REPORT_MAX_ROWS, 4, &l), PROBE_LAYOUT_TOO_MANY_PAGES);
    CHECK(tests_layout_choose(100, 600, 10, 32, &l) != PROBE_LAYOUT_OK);
    CHECK(tests_layout_choose(800, 30, 10, 32, &l) != PROBE_LAYOUT_OK);
    CHECK(tests_layout_choose(0, 0, 10, 32, &l) != PROBE_LAYOUT_OK);
    CHECK_EQ(tests_layout_choose(800, 600, 17, 32, &l), PROBE_LAYOUT_OK);
    CHECK_EQ(l.pages, 1);
    CHECK_EQ(tests_layout_fits(&l, 800, 600), 0);
    CHECK(tests_layout_fits(&l, 400, 300) != 0);
    l.pages = 0;
    CHECK(tests_layout_fits(&l, 800, 600) != 0);

    /* The countdown scales with the number of pages: every page at least twice,
     * never under a minute. */
    CHECK_EQ(tests_countdown_seconds(1, 5), 60);
    CHECK_EQ(tests_countdown_seconds(6, 5), 60);
    CHECK_EQ(tests_countdown_seconds(7, 5), 70);
    CHECK_EQ(tests_countdown_seconds(16, 5), 160);
    CHECK_EQ(tests_countdown_seconds(32, 8), 512);
    for (unsigned pages = 1; pages <= 32; pages++)
        CHECK(tests_countdown_seconds(pages, 5) >= 2 * pages * 5);
}

/* ---- The USB result writer ---- */

#define NODES 32u
#define PATH 96u

typedef struct {
    char path[PATH];
    int is_dir;
    uint64_t bytes;
} node;

typedef struct {
    node n[NODES];
    unsigned count;
    unsigned creates, dir_creates, writes, flushes, open_handles, closes;
    unsigned existing_opened_for_write;
    unsigned fail_write_at; /* 1-based */
    unsigned fail_flush_at; /* 1-based */
    unsigned fail_close_at; /* 1-based */
} mfs;

typedef struct {
    efi_file f;
    mfs *fs;
    char path[PATH];
} mh;

static mh g_handles[64];
static unsigned g_nhandles;

static int mfind(mfs *fs, const char *path)
{
    for (unsigned i = 0; i < fs->count; i++)
        if (strcmp(fs->n[i].path, path) == 0)
            return (int)i;
    return -1;
}

static void mseed(mfs *fs, const char *path, int is_dir, uint64_t bytes)
{
    strcpy(fs->n[fs->count].path, path);
    fs->n[fs->count].is_dir = is_dir;
    fs->n[fs->count].bytes = bytes;
    fs->count++;
}

static efi_file *mhandle(mfs *fs, const char *path);

static efi_status m_open(efi_file *self, efi_file **out, const efi_char16 *name, uint64_t mode,
                         uint64_t attrs)
{
    mh *h = (mh *)self;
    mfs *fs = h->fs;
    char path[PATH];
    size_t n = strlen(h->path);
    int idx;

    memcpy(path, h->path, n);
    if (n != 0)
        path[n++] = '\\';
    for (unsigned i = 0; name[i] != 0; i++)
        path[n++] = (char)name[i];
    path[n] = 0;
    idx = mfind(fs, path);
    if (idx < 0) {
        if (!(mode & EFI_FILE_MODE_CREATE))
            return EFI_NOT_FOUND;
        if (attrs & 0x10ull)
            fs->dir_creates++;
        fs->creates++;
        mseed(fs, path, (attrs & 0x10ull) != 0, 0);
    } else {
        if (mode & (EFI_FILE_MODE_WRITE | EFI_FILE_MODE_CREATE))
            if (!fs->n[idx].is_dir)
                fs->existing_opened_for_write++;
    }
    *out = mhandle(fs, path);
    fs->open_handles++;
    return EFI_SUCCESS;
}

static efi_status m_close(efi_file *self)
{
    mh *h = (mh *)self;

    h->fs->open_handles--;
    h->fs->closes++;
    /* The handle is gone either way: UEFI closes it in all cases. */
    return h->fs->fail_close_at != 0 && h->fs->closes == h->fs->fail_close_at ? EFI_DEVICE_ERROR
                                                                              : EFI_SUCCESS;
}

static efi_status m_write(efi_file *self, efi_uintn *size, const void *buf)
{
    mh *h = (mh *)self;
    int idx = mfind(h->fs, h->path);

    (void)buf;
    h->fs->writes++;
    if (h->fs->fail_write_at != 0 && h->fs->writes == h->fs->fail_write_at)
        return EFI_DEVICE_ERROR;
    if (idx >= 0)
        h->fs->n[idx].bytes += *size;
    return EFI_SUCCESS;
}

static efi_status m_flush(efi_file *self)
{
    mfs *fs = ((mh *)self)->fs;

    fs->flushes++;
    return fs->fail_flush_at != 0 && fs->flushes == fs->fail_flush_at ? EFI_DEVICE_ERROR
                                                                      : EFI_SUCCESS;
}

static efi_file *mhandle(mfs *fs, const char *path)
{
    mh *h = &g_handles[g_nhandles++ % 64];

    memset(h, 0, sizeof *h);
    h->f.open = m_open;
    h->f.close = m_close;
    h->f.write = m_write;
    h->f.flush = m_flush;
    h->fs = fs;
    strcpy(h->path, path);
    return &h->f;
}

typedef struct {
    efi_simple_fs sfs;
    mfs *fs;
} msfs;

static efi_status m_open_volume(efi_simple_fs *self, efi_file **root)
{
    msfs *s = (msfs *)self;

    *root = mhandle(s->fs, "");
    s->fs->open_handles++;
    return EFI_SUCCESS;
}

typedef struct {
    efi_loaded_image li;
    msfs sfs;
    uint8_t dp[64];
    int no_loaded_image;
    int no_device_path;
    int no_fs;
} board;

static board *g_board;

static efi_status m_handle_protocol(efi_handle handle, const efi_guid *guid, void **iface)
{
    static const efi_guid li = EFI_GUID_LOADED_IMAGE;
    static const efi_guid dp = EFI_GUID_DEVICE_PATH;
    static const efi_guid fsg = EFI_GUID_SIMPLE_FS;

    (void)handle;
    *iface = NULL;
    if (memcmp(guid, &li, sizeof li) == 0 && !g_board->no_loaded_image)
        *iface = &g_board->li;
    else if (memcmp(guid, &dp, sizeof dp) == 0 && !g_board->no_device_path)
        *iface = g_board->dp;
    else if (memcmp(guid, &fsg, sizeof fsg) == 0 && !g_board->no_fs)
        *iface = &g_board->sfs;
    return *iface != NULL ? EFI_SUCCESS : EFI_NOT_FOUND;
}

static size_t dp_nodes(uint8_t *out, const unsigned (*nodes)[3], unsigned n)
{
    size_t off = 0;

    for (unsigned i = 0; i < n; i++) {
        out[off] = (uint8_t)nodes[i][0];
        out[off + 1] = (uint8_t)nodes[i][1];
        st16(out + off + 2, (uint16_t)nodes[i][2]);
        memset(out + off + 4, 0, nodes[i][2] - 4);
        off += nodes[i][2];
    }
    out[off] = 0x7F;
    out[off + 1] = 0xFF;
    st16(out + off + 2, 4);
    return off + 4;
}

static const unsigned DP_ACPI[3] = {2, 1, 12};
static const unsigned DP_PCI[3] = {1, 1, 6};
static const unsigned DP_USB[3] = {3, 5, 6};
static const unsigned DP_NVME[3] = {3, 0x17, 16};

static void board_init(board *b, mfs *fs, int usb_dp_kind)
{
    unsigned nodes[4][3];
    unsigned n = 0;

    memset(b, 0, sizeof *b);
    memset(fs, 0, sizeof *fs);
    memcpy(nodes[n++], DP_ACPI, sizeof DP_ACPI);
    memcpy(nodes[n++], DP_PCI, sizeof DP_PCI);
    if (usb_dp_kind == 0 || usb_dp_kind == 2)
        memcpy(nodes[n++], DP_USB, sizeof DP_USB);
    if (usb_dp_kind == 1 || usb_dp_kind == 2)
        memcpy(nodes[n++], DP_NVME, sizeof DP_NVME);
    dp_nodes(b->dp, (const unsigned (*)[3])nodes, n);
    b->li.device_handle = (efi_handle)(uintptr_t)0x1000;
    b->sfs.sfs.open_volume = m_open_volume;
    b->sfs.fs = fs;
    g_board = b;
    g_nhandles = 0;
}

static efi_system_table *make_st(efi_boot_services *bs, efi_system_table *st)
{
    memset(bs, 0, sizeof *bs);
    memset(st, 0, sizeof *st);
    bs->handle_protocol = m_handle_protocol;
    st->boot_services = bs;
    return st;
}

static void seed_run_dir(mfs *fs)
{
    mseed(fs, "SP11DUMP", 1, 0);
    mseed(fs, "SP11DUMP\\RUN01", 1, 0);
}

void test_x_stick(void)
{
    static board b;
    static mfs fs;
    efi_boot_services bs;
    efi_system_table st;
    uint8_t blob[256];
    tests_file files[3];
    unsigned written = 99;
    int rc;

    section("stick writer");
    memset(blob, 0x5A, sizeof blob);
    files[0] = (tests_file){"x2-results.txt", blob, 100};
    files[1] = (tests_file){"x2-nv2.txt", blob, 200};
    files[2] = (tests_file){"x2-nv2.bin", blob, 96};
    make_st(&bs, &st);

    /* The normal case: the run directory exists, three files are written, every
     * handle is closed, no directory is created. */
    board_init(&b, &fs, 0);
    seed_run_dir(&fs);
    rc = tests_stick_write(&st, (efi_handle)1, 1, files, 3, &written);
    CHECK_EQ(rc, STICK_OK);
    CHECK_EQ(written, 3);
    CHECK_EQ(fs.open_handles, 0);
    CHECK_EQ(fs.dir_creates, 0);
    CHECK_EQ(fs.creates, 3);
    CHECK_EQ(fs.existing_opened_for_write, 0);
    CHECK_EQ(fs.writes, 3);
    CHECK_EQ(fs.flushes >= 3, 1);
    CHECK_EQU(fs.n[mfind(&fs, "SP11DUMP\\RUN01\\x2-nv2.txt")].bytes, 200);
    CHECK_STR(tests_stick_state_name(rc), "OK");

    /* The volume gate: only a path with a USB node and no other transport. */
    for (int kind = 1; kind <= 2; kind++) {
        board_init(&b, &fs, kind);
        seed_run_dir(&fs);
        rc = tests_stick_write(&st, (efi_handle)1, 1, files, 3, &written);
        CHECK_EQ(rc, kind == 1 ? STICK_REFUSED_NOT_USB : STICK_REFUSED_AMBIGUOUS);
        CHECK_EQ(written, 0);
        CHECK_EQ(fs.creates, 0);
        CHECK_EQ(fs.writes, 0);
        CHECK_EQ(fs.open_handles, 0);
    }
    board_init(&b, &fs, 0);
    seed_run_dir(&fs);
    memset(b.dp, 0, sizeof b.dp); /* a path of one zero-length node: malformed */
    rc = tests_stick_write(&st, (efi_handle)1, 1, files, 3, &written);
    CHECK_EQ(rc, STICK_REFUSED_MALFORMED);
    CHECK_EQ(fs.creates + fs.writes, 0);
    for (int variant = 0; variant < 3; variant++) {
        board_init(&b, &fs, 0);
        seed_run_dir(&fs);
        b.no_loaded_image = variant == 0;
        b.no_device_path = variant == 1;
        b.no_fs = variant == 2;
        rc = tests_stick_write(&st, (efi_handle)1, 1, files, 3, &written);
        CHECK_EQ(rc, STICK_REFUSED_NO_VOLUME);
        CHECK_EQ(fs.creates + fs.writes, 0);
        CHECK_EQ(fs.open_handles, 0);
    }

    /* The run directory is never created: no SP11DUMP, or no RUNnn inside it. */
    board_init(&b, &fs, 0);
    rc = tests_stick_write(&st, (efi_handle)1, 1, files, 3, &written);
    CHECK_EQ(rc, STICK_NO_RUN_DIR);
    CHECK_EQ(fs.creates, 0);
    CHECK_EQ(fs.count, 0);
    CHECK_EQ(fs.open_handles, 0);
    board_init(&b, &fs, 0);
    mseed(&fs, "SP11DUMP", 1, 0);
    rc = tests_stick_write(&st, (efi_handle)1, 1, files, 3, &written);
    CHECK_EQ(rc, STICK_NO_RUN_DIR);
    CHECK_EQ(fs.creates, 0);
    CHECK_EQ(fs.open_handles, 0);
    board_init(&b, &fs, 0);
    seed_run_dir(&fs);
    rc = tests_stick_write(&st, (efi_handle)1, 7, files, 3, &written); /* RUN07 is not there */
    CHECK_EQ(rc, STICK_NO_RUN_DIR);
    CHECK_EQ(fs.creates, 0);

    /* A file that is already on the stick is never opened for writing. */
    board_init(&b, &fs, 0);
    seed_run_dir(&fs);
    mseed(&fs, "SP11DUMP\\RUN01\\x2-results.txt", 0, 1234);
    rc = tests_stick_write(&st, (efi_handle)1, 1, files, 3, &written);
    CHECK_EQ(rc, STICK_EXISTS);
    CHECK_EQ(written, 0);
    CHECK_EQ(fs.existing_opened_for_write, 0);
    CHECK_EQ(fs.writes, 0);
    CHECK_EQU(fs.n[mfind(&fs, "SP11DUMP\\RUN01\\x2-results.txt")].bytes, 1234);
    CHECK_EQ(fs.open_handles, 0);
    board_init(&b, &fs, 0);
    seed_run_dir(&fs);
    mseed(&fs, "SP11DUMP\\RUN01\\x2-nv2.txt", 0, 55);
    rc = tests_stick_write(&st, (efi_handle)1, 1, files, 3, &written);
    CHECK_EQ(rc, STICK_PARTIAL); /* the first file went in, the second was refused */
    CHECK_EQ(written, 1);
    CHECK_EQ(fs.existing_opened_for_write, 0);
    CHECK_EQU(fs.n[mfind(&fs, "SP11DUMP\\RUN01\\x2-nv2.txt")].bytes, 55);

    /* A write error ends the writing: nothing further is attempted. */
    for (unsigned at = 1; at <= 2; at++) {
        board_init(&b, &fs, 0);
        seed_run_dir(&fs);
        fs.fail_write_at = at;
        rc = tests_stick_write(&st, (efi_handle)1, 1, files, 3, &written);
        CHECK_EQ(rc, at == 1 ? STICK_ERROR : STICK_PARTIAL);
        CHECK_EQ(written, at - 1);
        CHECK_EQ(fs.writes, at);
        CHECK_EQ(fs.open_handles, 0); /* handles are closed on the error path too */
    }

    /* The directory entries and the volume root are flushed and closed after the
     * last file. A failure there means the files may not be on the stick, so it is
     * never STICK OK: the files themselves went in, which makes it PARTIAL. */
    board_init(&b, &fs, 0);
    seed_run_dir(&fs);
    rc = tests_stick_write(&st, (efi_handle)1, 1, files, 3, &written);
    CHECK_EQ(rc, STICK_OK);
    {
        unsigned flushes = fs.flushes; /* one per file, then run dir, SP11DUMP, root */
        unsigned closes = fs.closes;

        CHECK_EQ(flushes, 3 + 3);
        CHECK_EQ(closes, 3 + 3);
        for (unsigned k = 1; k <= 3; k++) {
            board_init(&b, &fs, 0);
            seed_run_dir(&fs);
            fs.fail_flush_at = 3 + k;
            rc = tests_stick_write(&st, (efi_handle)1, 1, files, 3, &written);
            CHECK_EQ(rc, STICK_PARTIAL);
            CHECK_EQ(written, 3);
            CHECK_EQ(fs.open_handles, 0);
            CHECK_STR(tests_stick_state_name(rc), "PARTIAL");

            board_init(&b, &fs, 0);
            seed_run_dir(&fs);
            fs.fail_close_at = 3 + k;
            rc = tests_stick_write(&st, (efi_handle)1, 1, files, 3, &written);
            CHECK_EQ(rc, STICK_PARTIAL);
            CHECK_EQ(written, 3);
            CHECK_EQ(fs.open_handles, 0);
        }
        /* A file whose own flush or close fails is a failed write, not a success. */
        for (unsigned k = 1; k <= 3; k++) {
            board_init(&b, &fs, 0);
            seed_run_dir(&fs);
            fs.fail_flush_at = k;
            rc = tests_stick_write(&st, (efi_handle)1, 1, files, 3, &written);
            CHECK_EQ(rc, k == 1 ? STICK_ERROR : STICK_PARTIAL);
            CHECK_EQ(written, k - 1);

            board_init(&b, &fs, 0);
            seed_run_dir(&fs);
            fs.fail_close_at = k;
            rc = tests_stick_write(&st, (efi_handle)1, 1, files, 3, &written);
            CHECK_EQ(rc, k == 1 ? STICK_ERROR : STICK_PARTIAL);
            CHECK_EQ(written, k - 1);
        }
    }

    /* Argument refusals: nothing on the stick is touched. */
    {
        tests_file bad[1];
        static uint8_t big[TESTS_STICK_MAX_FILE_BYTES + 1];
        tests_file many[TESTS_STICK_MAX_FILES + 1];

        board_init(&b, &fs, 0);
        seed_run_dir(&fs);
        CHECK_EQ(tests_stick_write(&st, (efi_handle)1, 0, files, 3, &written), STICK_REFUSED_ARGS);
        CHECK_EQ(tests_stick_write(&st, (efi_handle)1, 100, files, 3, &written), STICK_REFUSED_ARGS);
        CHECK_EQ(tests_stick_write(&st, (efi_handle)1, 1, files, 0, &written), STICK_REFUSED_ARGS);
        for (unsigned i = 0; i <= TESTS_STICK_MAX_FILES; i++)
            many[i] = files[0];
        CHECK_EQ(tests_stick_write(&st, (efi_handle)1, 1, many, TESTS_STICK_MAX_FILES + 1, &written),
                 STICK_REFUSED_ARGS);
        bad[0] = (tests_file){"a-name-that-is-far-too-long-for-fat", blob, 1};
        CHECK_EQ(tests_stick_write(&st, (efi_handle)1, 1, bad, 1, &written), STICK_REFUSED_ARGS);
        bad[0] = (tests_file){"", blob, 1};
        CHECK_EQ(tests_stick_write(&st, (efi_handle)1, 1, bad, 1, &written), STICK_REFUSED_ARGS);
        bad[0] = (tests_file){NULL, blob, 1};
        CHECK_EQ(tests_stick_write(&st, (efi_handle)1, 1, bad, 1, &written), STICK_REFUSED_ARGS);
        bad[0] = (tests_file){"big.bin", big, sizeof big};
        CHECK_EQ(tests_stick_write(&st, (efi_handle)1, 1, bad, 1, &written), STICK_REFUSED_ARGS);
        bad[0] = (tests_file){"null.bin", NULL, 5};
        CHECK_EQ(tests_stick_write(&st, (efi_handle)1, 1, bad, 1, &written), STICK_REFUSED_ARGS);
        for (unsigned i = 0; i < 3; i++) /* each within the file cap, together over the total cap */
            many[i] = (tests_file){i == 0 ? "t1.bin" : i == 1 ? "t2.bin" : "t3.bin", big,
                                   TESTS_STICK_MAX_FILE_BYTES};
        for (unsigned i = 3; i < 5; i++)
            many[i] = (tests_file){i == 3 ? "t4.bin" : "t5.bin", big, TESTS_STICK_MAX_FILE_BYTES};
        CHECK_EQ(tests_stick_write(&st, (efi_handle)1, 1, many, 5, &written), STICK_REFUSED_ARGS);
        CHECK_EQ(fs.creates + fs.writes, 0);
        CHECK_EQ(fs.open_handles, 0);
    }
    CHECK_STR(tests_stick_state_name(STICK_NO_RUN_DIR), "NO RUN DIR");
    CHECK_STR(tests_stick_state_name(STICK_STATE_COUNT), "?");
    CHECK_STR(tests_stick_state_name(-1), "?");
}
