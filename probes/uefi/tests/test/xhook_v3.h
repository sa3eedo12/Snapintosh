/* SPDX-License-Identifier: MIT */
/* The hook tests of the third version of the X2 image: the firmware's PCI I/O view that phase A
 * collects and writes, and the photograph T06 takes before a read that may not return. Included at
 * the end of test_x_hook.c, because hook.c's state is static and its fakes (the counter, the bus,
 * the stick writer, the vector scope) live in that translation unit.
 *
 * The counter is the point of the second half. The image waits by polling the physical counter, so
 * the tests make the counter a model: each read adds a step, the frequency is a variable, and the
 * moment of every read the device model answers is recorded in counter ticks. A wait is then an
 * inequality between two recorded moments, not a sleep the test would have to sit through.
 */
#ifndef TESTS_XHOOK_V3_H
#define TESTS_XHOOK_V3_H

/* ---- Phase A: the firmware's PCI I/O view ---- */

void test_x_hook_pciio(void)
{
    int ret;
    char want[64];

    section("hook: pciio record");

    /* The platform is not at EL2 before ExitBootServices (the Surface Pro 11): no test runs, and
     * the firmware's view is still collected and written. */
    h_reset();
    h_facts.preebs_el_raw = 4;
    h_st.boot_services = x_pciio_fake_bs(1);
    CHECK_EQ(run_pre(&ret), 0);
    CHECK(con_has("SP11X2 PCIIO STATE=OK HANDLES=3 NVME=1"));
    CHECK_EQ(g_pciio.state, TESTS_PCIIO_OK);
    CHECK_EQ(g_pciio.n_nvme, 1);
    CHECK_EQ(g_tc.pciio == &g_pciio, 1); /* T06 is given the table the hook filled */
    CHECK_EQ(h_stick_calls, 1);
    CHECK_EQ(h_stick_files, 1);
    CHECK_STR(h_stick_names[0], "pciio.txt");
    CHECK(h_stick_pciio_len > 0);
    CHECK(strncmp(h_stick_pciio, "# SP11X2 pciio v1", 17) == 0);
    CHECK(strstr(h_stick_pciio, "build=TESTBUILD001\nkind=HARDWARE\n") != NULL);
    CHECK(strstr(h_stick_pciio, "state=OK status=0x0000000000000000\n") != NULL);
    CHECK(strstr(h_stick_pciio, "located=3 kept=3 nvme=1 skipped=0 truncated=0\n") != NULL);
    CHECK(strstr(h_stick_pciio, "pci idx=1 seg=6 bus=01 dev=00 func=0 root=PcieRoot(0x6)") != NULL);
    CHECK(strstr(h_stick_pciio, "id=1B36:0010 class=010802") != NULL);
    CHECK(strstr(h_stick_pciio, "nvme idx=1 bar0=000000007A000000 regs=OK") != NULL);
    CHECK(strstr(h_stick_pciio, "root=PcieRoot(0x0)") != NULL); /* the unrelated function, segment 0 */
    CHECK_STR(h_stick_pciio + h_stick_pciio_len - 4, "end\n");
    snprintf(want, sizeof want, "SP11X2 STICK STATE=OK FILES=1");
    CHECK(con_has(want));
    CHECK_EQ(h_run_seq_n, 0);
    CHECK(!con_has("SP11X2 RUN "));

    /* At EL2 before ExitBootServices: the results and the K1 record go first, the firmware's
     * view last, all in the one call. A file already on the stick stops the later ones, never the
     * record. */
    h_reset();
    h_st.boot_services = x_pciio_fake_bs(1);
    CHECK_EQ(run_pre(&ret), 0);
    CHECK_EQ(h_stick_calls, 1);
    CHECK_STR(h_stick_names[0], "x2-results.txt");
    CHECK(h_stick_files >= 3);
    CHECK_STR(h_stick_names[h_stick_files - 1], "pciio.txt");
    CHECK(strstr(h_stick_pciio, "nvme idx=1 bar0=000000007A000000 regs=OK") != NULL);
    for (unsigned i = 0; i + 1 < h_stick_files; i++)
        CHECK(strcmp(h_stick_names[i], "pciio.txt") != 0);

    /* The firmware has no PCI I/O at all, or the image has no boot services: said so, written. */
    h_reset();
    h_facts.preebs_el_raw = 4;
    CHECK_EQ(run_pre(&ret), 0);
    CHECK(con_has("SP11X2 PCIIO STATE=NO BOOT SERVICES HANDLES=0 NVME=0"));
    CHECK_EQ(g_pciio.state, TESTS_PCIIO_NO_BOOT_SERVICES);
    CHECK_EQ(h_stick_calls, 1);
    CHECK(strstr(h_stick_pciio, "state=NO BOOT SERVICES") != NULL);
    CHECK(strstr(h_stick_pciio, "nvme=0") != NULL);
    CHECK(strstr(h_stick_pciio, "\nnvme idx=") == NULL);

    /* No NVMe among the functions: recorded, and T06 has nothing to read. */
    h_reset();
    h_facts.preebs_el_raw = 4;
    h_st.boot_services = x_pciio_fake_bs(0);
    CHECK_EQ(run_pre(&ret), 0);
    CHECK(con_has("SP11X2 PCIIO STATE=OK HANDLES=2 NVME=0"));
    CHECK_EQ(g_pciio.n_nvme, 0);
    CHECK(strstr(h_stick_pciio, "\nnvme idx=") == NULL);

    /* No dump run directory on the stick: nothing is written, and the marker says why. */
    h_reset();
    h_facts.preebs_el_raw = 4;
    h_facts.dump_state = PROBE_DUMP_REFUSED_NO_VOLUME;
    h_st.boot_services = x_pciio_fake_bs(1);
    CHECK_EQ(run_pre(&ret), 0);
    CHECK_EQ(h_stick_calls, 0);
    CHECK(con_has("SP11X2 STICK STATE=NOT ATTEMPTED"));
    CHECK_EQ(g_pciio.n_nvme, 1); /* collected all the same: T06 needs it, the stick or not */
}

/* ---- Phase B: the photograph before the first read of a window ---- */

/* A hook with its layout validated and the page ready to draw, and no test run. */
static void photo_ready(void)
{
    int ret;

    h_reset();
    h_facts.preebs_el_raw = 4;
    CHECK_EQ(run_pre(&ret), 0);
    CHECK_EQ(g_have_layout, 1);
}

/* Lit bytes in the top quarter of the framebuffer: the header bars, which only a whole page draws
 * (a footer update never touches them). */
static unsigned fb_lit_top(void)
{
    unsigned n = 0;

    for (size_t i = 0; i < (size_t)H_W * (H_H / 4u) * 4u; i++)
        n += h_fbmem[i] != 0;
    return n;
}

/* Counter ticks one call of photograph_wait consumed, and its result. */
static uint64_t photo_ticks(uint64_t freq, uint64_t step, unsigned seconds, int *rc)
{
    uint64_t t0;

    h_freq = freq;
    h_counter_step = step;
    t0 = h_counter;
    *rc = photograph_wait(NULL, "T06 ECAM 0070000000", seconds);
    return h_counter - t0;
}

/* One T06 over the unit-shaped MCFG: the firmware's NVMe is in segment 6, its ECAM window and its
 * BAR are modelled, the memory map and the firmware's record are as the other tests build them. */
#define H_BAR 0x7A000000ull

static void photo_world(world *w, const segcfg *c)
{
    tbl t;

    w_init(w);
    t_start(&t, "MCFG", 8);
    for (unsigned i = 0; i < UNIT_ENTRIES; i++)
        mcfg_entry(&t, UNIT_MCFG[i].base, UNIT_MCFG[i].seg, UNIT_MCFG[i].start, UNIT_MCFG[i].end);
    t_seal(&t);
    w_table(w, 1, "MCFG", &t);
    seg_devices(&w->dm, ECAM_SEG6, H_BAR, c);
    h_ctx.scan = &w->scan;
    h_ctx.ops = &w->phys;
    h_ctx.ops_ctx = &w->fw;
    h_ctx.memmap = &w->mm;
    h_model = w;
}

static unsigned reads_in_bar(const world *w)
{
    unsigned n = 0;

    for (unsigned i = 0; i < w->dm.nlog; i++)
        n += w->dm.log[i] >= H_BAR && w->dm.log[i] < H_BAR + 0x1000;
    return n;
}

/* Runs the whole post-bounce phase over the world, with the firmware showing an NVMe or not, and
 * one of the faults a run can have:
 *   0 none,
 *   1 the memory map calls the ECAM window RAM,
 *   2 the memory map calls the NVMe BAR RAM (the window is fine, the controller is refused),
 *   3 the counter stands still while T06 runs, so no wait can be measured. */
static void photo_run(world *w, const segcfg *c, int with_nvme, int variant)
{
    int ret;

    h_reset();
    photo_world(w, c);
    if (variant == 1)
        w_map(w, 7, ECAM_SEG6, 0x200);
    if (variant == 2)
        w_map(w, 7, H_BAR, 1);
    if (variant == 3)
        h_stall_test = T_PCIE;
    h_facts.preebs_el_raw = 4;
    h_st.boot_services = x_pciio_fake_bs(with_nvme);
    CHECK_EQ(run_pre(&ret), 0);
    run_post();
    CHECK_EQ(h_parked, 1);
}

/* Counter ticks T06 took, from the entry of its vector scope to its exit. */
static uint64_t t06_ticks(void)
{
    int pos = run_position_of(T_PCIE);

    return pos >= 0 ? h_run_leave[pos] - h_run_enter[pos] : ~0ull;
}

void test_x_hook_photograph(void)
{
    static const uint64_t freq = 1000000;
    segcfg c = good_seg(H_BAR);
    world *w = xalloc(sizeof *w);
    probe_payload ft;
    char out[PROBE_LINE_COLS + 1];
    uint64_t t;
    int rc, pos;

    section("hook: photograph before risk");

    /* The length of the wait is a named constant, and the footer that carries it is a line the
     * renderer accepts as it is (nothing is truncated or corrected). */
    CHECK_EQ(TESTS_PHOTOGRAPH_WAIT_SECONDS, 8);
    photo_footer(ft, "T06 ECAM 0070000000", 8);
    CHECK_STR(ft, "PHOTO NOW: T06 ECAM 0070000000 IN 8 S");
    CHECK_EQ(probe_line_format(out, ft), 0);
    photo_footer(ft, "T06 NVME 0078000000", 1);
    CHECK_STR(ft, "PHOTO NOW: T06 NVME 0078000000 IN 1 S");
    CHECK_EQ(probe_line_format(out, ft), 0);
    CHECK(strlen(ft) <= PROBE_LINE_PAYLOAD_MAX);

    /* The wait is the number of seconds asked for on the counter: not less, and returned from
     * promptly. The page is drawn first. */
    photo_ready();
    CHECK_EQ(fb_lit_bytes(), 0);
    CHECK_EQ(fb_lit_top(), 0);
    t = photo_ticks(freq, 7, TESTS_PHOTOGRAPH_WAIT_SECONDS, &rc);
    CHECK_EQ(rc, 0);
    CHECK(t >= 8u * freq);
    CHECK(t < 8u * freq + 100u);
    CHECK(fb_lit_bytes() > 1000);
    CHECK(fb_lit_top() > 1000); /* the whole page was drawn, not only the footer */
    t = photo_ticks(freq, 7, 1, &rc);
    CHECK_EQ(rc, 0);
    CHECK(t >= freq && t < freq + 100u);

    /* The frequency is the register's when it is one, and 19.2 MHz when it is not. */
    t = photo_ticks(19200000, 1000, 8, &rc);
    CHECK(rc == 0 && t >= 8u * 19200000u && t < 8u * 19200000u + 5000u);
    t = photo_ticks(24000000, 1000, 8, &rc);
    CHECK(rc == 0 && t >= 8u * 24000000u && t < 8u * 24000000u + 5000u);
    t = photo_ticks(1000000000, 1000000, 8, &rc);
    CHECK(rc == 0 && t >= 8000000000ull && t < 8000000000ull + 5000000u);
    for (int variant = 0; variant < 3; variant++) {
        uint64_t odd = variant == 0 ? 0 : variant == 1 ? 999999 : 1000000001;

        t = photo_ticks(odd, 1000, 8, &rc);
        CHECK(rc == 0 && t >= 8u * 19200000u && t < 8u * 19200000u + 5000u);
    }

    /* A counter that does not run, or runs backwards by more than a second, cannot be waited on:
     * the call fails, and says which of the two it was, so the caller does not read. */
    t = photo_ticks(freq, 0, 8, &rc);
    CHECK_EQ(rc, TESTS_PHOTO_STALLED);
    CHECK_EQ(t, 0);
    (void)photo_ticks(freq, (uint64_t)0 - 3u, 8, &rc);
    CHECK_EQ(rc, TESTS_PHOTO_BACKWARDS);

    /* A read satisfied out of order comes back a little behind the one before it: that is no
     * reason to stop, and the wait is still its eight seconds (to within the reversal). The jitter
     * models a reversal of five steps on every third read, and then of nearly a millisecond. */
    for (uint64_t reversal = 5u * 7u; reversal < freq / 1000u; reversal *= 20u) {
        h_jitter = reversal;
        t = photo_ticks(freq, 7, 8, &rc);
        CHECK_EQ(rc, TESTS_PHOTO_OK);
        CHECK(t + reversal >= 8u * freq && t < 8u * freq + 100u);
        h_jitter = 0;
    }

    /* No seconds, too many seconds, no layout: refused before the counter is read. */
    (void)photo_ticks(freq, 7, 0, &rc);
    CHECK_EQ(rc, TESTS_PHOTO_REFUSED);
    (void)photo_ticks(freq, 7, 61, &rc);
    CHECK_EQ(rc, TESTS_PHOTO_REFUSED);
    g_have_layout = 0;
    t = photo_ticks(freq, 7, 8, &rc);
    CHECK_EQ(rc, TESTS_PHOTO_REFUSED);
    CHECK_EQ(t, 0);
    g_have_layout = 1;

    /* T06 inside the real driver. The firmware's NVMe is in segment 6; every moment a read is made
     * is recorded in counter ticks. The first read of the ECAM window comes at least 8 s after T06
     * started, the first read of the controller at least 8 s after the last read of the window,
     * and the reads of the window are made back to back: two waits and no more. */
    photo_run(w, &c, 1, 0);
    pos = run_position_of(T_PCIE);
    CHECK_EQ(pos, 8); /* last of the nine */
    CHECK_EQ(g_res[T_PCIE].verdict, TV_PASS);
    CHECK(w->dm.nlog > 10);
    CHECK_EQ(h_read_n, w->dm.nlog);
    if (pos >= 1 && w->dm.nlog > 10) {
        unsigned first_bar = 0;

        while (first_bar < w->dm.nlog &&
               !(w->dm.log[first_bar] >= H_BAR && w->dm.log[first_bar] < H_BAR + 0x1000))
            first_bar++;
        CHECK(first_bar > 0 && first_bar < w->dm.nlog);
        CHECK(h_read_time[0] - h_run_enter[pos] >= 8u * freq);
        CHECK(h_read_time[first_bar] - h_read_time[first_bar - 1] >= 8u * freq);
        CHECK_EQ(h_read_time[first_bar - 1] - h_read_time[0], 0); /* the window is read without a wait */
        CHECK_EQ(h_read_time[w->dm.nlog - 1] - h_read_time[first_bar], 0);
        CHECK(t06_ticks() >= 16u * freq);
        CHECK(t06_ticks() < 16u * freq + 2000u); /* and nothing else in T06 waited */
        /* The tests before it did not wait: T01..T05 and T07..T09 together cost no second. */
        CHECK(h_run_leave[pos - 1] - h_run_enter[0] < freq);
    }
    w_free(w);

    /* Nothing for T06 to touch: the firmware showed no NVMe, so there is no read and no wait. */
    photo_run(w, &c, 0, 0);
    CHECK_EQ(g_res[T_PCIE].verdict, TV_NOT_RUN);
    CHECK_EQ(h_read_n, 0);
    CHECK_EQ(t06_ticks(), 0);
    CHECK(result_has_row(&g_res[T_PCIE], "PCI I/O: 2 FUNCTIONS, NONE NVME"));
    w_free(w);

    /* The window is refused (the map calls it RAM): no read and no wait either. */
    photo_run(w, &c, 1, 1);
    CHECK_EQ(g_res[T_PCIE].verdict, TV_UNKNOWN);
    CHECK_EQ(h_read_n, 0);
    CHECK_EQ(t06_ticks(), 0);
    CHECK(result_has_row(&g_res[T_PCIE], "W6 REFUSED: MAP SAYS NOT MMIO"));
    w_free(w);

    /* The window is fine and the controller's BAR is refused: the window is read after its wait,
     * the controller is not read and not waited for. One wait, not two. */
    photo_run(w, &c, 1, 2);
    CHECK_EQ(g_res[T_PCIE].verdict, TV_UNKNOWN);
    CHECK(h_read_n > 0);
    CHECK(h_read_time[0] - h_run_enter[run_position_of(T_PCIE)] >= 8u * freq);
    CHECK(t06_ticks() >= 8u * freq && t06_ticks() < 8u * freq + 1000u);
    CHECK_EQ(reads_in_bar(w), 0);
    w_free(w);

    /* The counter does not advance while T06 runs: the wait cannot be made, so nothing is read. */
    photo_run(w, &c, 1, 3);
    CHECK_EQ(g_res[T_PCIE].verdict, TV_UNKNOWN);
    CHECK_EQ(h_read_n, 0);
    CHECK(result_has_row(&g_res[T_PCIE], "PHOTOGRAPH WAIT FAILED: CODE=3")); /* TESTS_PHOTO_STALLED */
    CHECK(result_has_row(&g_res[T_PCIE], "WHY: PHOTOGRAPH WAIT FAILED"));
    w_free(w);

    free(w);
}

#endif /* TESTS_XHOOK_V3_H */
