/* SPDX-License-Identifier: MIT */
#include "tests_hook.h"

#include "cpu.h"
#include "report_x.h"
#include "stick.h"
#include "suite.h"
#include "sys.h"
#include "sysstate.h"

#ifndef TESTS_MODEL_BUILD
#define TESTS_MODEL_BUILD 0
#endif
#ifndef TESTS_SOC
#define TESTS_SOC 1 /* NV2R_SOC_X1E80100: an explicit build constant, never guessed */
#endif
#ifndef PROBE_FAST_TEST
#define PROBE_FAST_TEST 0
#endif
#ifndef TESTS_NO_RESET
#define TESTS_NO_RESET 0
#endif
#ifndef TESTS_SELFTEST
#define TESTS_SELFTEST 0
#endif
#ifndef TESTS_SELFTEST_FATAL
#define TESTS_SELFTEST_FATAL 0
#endif
#ifndef TESTS_SELFTEST_FATAL_PRE
#define TESTS_SELFTEST_FATAL_PRE 0
#endif
#ifndef TESTS_REQUIRE_MMIO_IN_MAP
#define TESTS_REQUIRE_MMIO_IN_MAP 0
#endif

#if PROBE_FAST_TEST
#define DWELL_S 2u
#else
#define DWELL_S 12u
#endif
#define FALLBACK_SECOND_SPINS 100000000ull
#define LAYOUT_MAX_PAGES 32u

#if TESTS_MODEL_BUILD
#define KIND_TEXT "MODEL"
#else
#define KIND_TEXT "HARDWARE"
#endif

#if TESTS_REQUIRE_MMIO_IN_MAP
#define WINDOW_POLICY_TEXT "STRICT"
#else
#define WINDOW_POLICY_TEXT "NO-CONFLICT"
#endif

/* The kind of run, as bytes in .rdata: `strings` on the image says whether it is
 * the hardware build or an emulator build, and which device-window policy it
 * applies (STRICT: only windows the UEFI memory map lists wholly as MMIO;
 * NO-CONFLICT: also windows the map does not mention, never one it calls RAM or
 * anything else). */
static const char g_kind_tag[] __attribute__((used)) = "SP11X2-KIND=" KIND_TEXT;
static const char g_policy_tag[] __attribute__((used)) = "SP11X2-WINDOWS=" WINDOW_POLICY_TEXT;

static tests_result g_res[TESTS_COUNT + 1];
static tests_ctx g_tc;
static tests_report g_rep;
static uint8_t g_acpi_buf[TESTS_ACPI_BUF_BYTES];
static tests_nv2_record g_rec;
static int g_have_rec;
static int g_post;
static const tests_hook_ctx *g_hc;
static probe_layout g_layout;
static int g_have_layout;
static int g_current_test;
/* While the vector scope is entered, VBAR_EL2 is this image's table and DAIF is
 * masked. Firmware code must not run then: its RaiseTPL/RestoreTPL pair unmasks
 * IRQs, an interrupt would reach vectors that do not handle it, and the fatal
 * path would re-enter the firmware console it had just interrupted (OBSERVED
 * under QEMU: an exception storm after a console line printed inside the scope).
 * Console text produced inside the scope is held here and printed on the way out. */
static volatile int g_in_scope;
static char g_deferred[2048];
static size_t g_deferred_len;
static unsigned g_deferred_dropped;

static char g_results_text[16384];
static char g_nv2_text[1024];
static char g_nv2_json[1024];
static uint8_t g_nv2_bin[TESTS_NV2_BIN_SIZE];

/* ---- Console (before ExitBootServices only) ---- */

static void emit_console(const char *s)
{
    efi_simple_text_output *out = g_hc->st->con_out;
    efi_char16 buf[96];
    size_t n = 0;

    if (out == NULL || g_post)
        return;
    for (; *s != 0; s++) {
        if (n + 3 > sizeof buf / sizeof buf[0]) {
            buf[n] = 0;
            out->output_string(out, buf);
            n = 0;
        }
        if (*s == '\n')
            buf[n++] = '\r';
        buf[n++] = (efi_char16)(uint8_t)*s;
    }
    buf[n] = 0;
    out->output_string(out, buf);
}

static void con_puts(const char *s)
{
    size_t len = 0;

    if (g_post)
        return;
    if (!g_in_scope) {
        emit_console(s);
        return;
    }
    while (s[len] != 0)
        len++;
    if (g_deferred_len + len + 1 > sizeof g_deferred) {
        g_deferred_dropped++;
        return;
    }
    for (size_t i = 0; i < len; i++)
        g_deferred[g_deferred_len + i] = s[i];
    g_deferred_len += len;
    g_deferred[g_deferred_len] = 0;
}

static void flush_deferred(void)
{
    if (g_deferred_len != 0) {
        g_deferred[g_deferred_len] = 0;
        emit_console(g_deferred);
    }
    g_deferred_len = 0;
    g_deferred[0] = 0;
    if (g_deferred_dropped != 0) {
        emit_console("SP11X2 DEFERRED DROPPED\n");
        g_deferred_dropped = 0;
    }
}

/* The scope brackets, with the bookkeeping the console rule above needs. The
 * deferred text is printed only after the scope has been left. */
static int scope_enter(void)
{
    int rc = tests_sys_scope_enter();

    if (rc == 0)
        g_in_scope = 1;
    return rc;
}

static int scope_leave(void)
{
    int rc = tests_sys_scope_leave();

    g_in_scope = 0;
    flush_deferred();
    return rc;
}

/* One marker line: "SP11X2 <tag> key=value ...". The emulator tests read these
 * from the serial console; on the unit they may go nowhere visible. */
static void marker(const char *tag, const char *k1, const char *v1, const char *k2, const char *v2,
                   const char *k3, const char *v3)
{
    char line[200];
    probe_sb sb;
    const char *kv[3][2] = {{k1, v1}, {k2, v2}, {k3, v3}};

    probe_sb_init(&sb, line, sizeof line);
    probe_sb_puts(&sb, "SP11X2 ");
    probe_sb_puts(&sb, tag);
    for (int i = 0; i < 3; i++) {
        if (kv[i][0] == NULL)
            continue;
        probe_sb_putc(&sb, ' ');
        probe_sb_puts(&sb, kv[i][0]);
        probe_sb_putc(&sb, '=');
        probe_sb_puts(&sb, kv[i][1]);
    }
    probe_sb_putc(&sb, '\n');
    con_puts(line);
}

/* ---- Progress and fatal output ---- */

static void draw_footer_text(const char *text)
{
    probe_payload ft;

    if (!g_post || !g_have_layout)
        return;
    tests_footer_text(ft, text);
    probe_draw_footer(g_hc->fb, &g_layout, ft);
}

static void progress(void *ctx, const char *text)
{
    char line[64];
    probe_sb sb;

    (void)ctx;
    if (g_post) {
        draw_footer_text(text);
        return;
    }
    probe_sb_init(&sb, line, sizeof line);
    probe_sb_puts(&sb, "SP11X2 PROGRESS ");
    probe_sb_puts(&sb, text);
    probe_sb_putc(&sb, '\n');
    con_puts(line);
}

/* A fatal page is drawn whenever a validated layout exists, before ExitBootServices
 * as well as after: the framebuffer is the one output that does not depend on
 * firmware services or on the console being visible on this machine. 1 if a page
 * was drawn. */
static int draw_fatal_rows(probe_payload rows[], unsigned n)
{
    probe_payload h0, h1, ft;

    if (!g_have_layout)
        return 0;
    tests_report_header0(h0, g_hc->facts->build_id, TESTS_MODEL_BUILD);
    tests_report_header1(h1, 2, 1, 1);
    tests_footer_text(ft, "FATAL - HALTED");
    probe_draw_page(g_hc->fb, &g_layout, h0, h1, rows, n, 0, ft);
    return 1;
}

/* The firmware console is only the fallback for a machine with no page: once a
 * page is on the framebuffer nothing is written to the console again. Under QEMU
 * a console line written after the page scrolled the framebuffer text console,
 * which moved the whole page up one row and cut off its header, and the call can
 * take an interrupt that this image's vectors answer by parking before the line
 * completes. */
static void fatal_draw(const char *why, uint64_t esr, uint64_t far, uint64_t elr, unsigned vec)
{
    probe_payload rows[8];
    probe_sb sb;
    unsigned n = 0;

    probe_sb_init(&sb, rows[n++], sizeof rows[0]);
    probe_sb_puts(&sb, "FATAL: ");
    probe_sb_puts(&sb, why);
    probe_sb_init(&sb, rows[n++], sizeof rows[0]);
    probe_sb_puts(&sb, "VECTOR=");
    probe_sb_dec(&sb, vec);
    probe_sb_puts(&sb, " LAST TEST=T0");
    probe_sb_dec(&sb, (uint64_t)g_current_test);
    probe_sb_init(&sb, rows[n++], sizeof rows[0]);
    probe_sb_puts(&sb, "ESR=");
    probe_sb_hex(&sb, esr, 16);
    probe_sb_init(&sb, rows[n++], sizeof rows[0]);
    probe_sb_puts(&sb, "FAR=");
    probe_sb_hex(&sb, far, 16);
    probe_sb_init(&sb, rows[n++], sizeof rows[0]);
    probe_sb_puts(&sb, "ELR=");
    probe_sb_hex(&sb, elr, 16);
    probe_sb_init(&sb, rows[n++], sizeof rows[0]);
    probe_sb_puts(&sb, "MACHINE STATE UNKNOWN");
    probe_sb_init(&sb, rows[n++], sizeof rows[0]);
    probe_sb_puts(&sb, "POWER CYCLE. DO NOT TRUST RESULTS.");
    if (draw_fatal_rows(rows, n) || g_post)
        return;
    {
        char line[120];

        probe_sb_init(&sb, line, sizeof line);
        probe_sb_puts(&sb, "SP11X2 FATAL ");
        probe_sb_puts(&sb, why);
        probe_sb_puts(&sb, " VECTOR=");
        probe_sb_dec(&sb, vec);
        probe_sb_putc(&sb, '\n');
        emit_console(line);
    }
}

/* The end of the line after a failed set-up or restore. Some register is no
 * longer what the firmware left, so nothing more may run: no further test, no
 * file on the stick, no Boot Services call and above all no ExitBootServices.
 * The image makes one more attempt to put everything back, masks interrupts and
 * puts the page on the framebuffer. Only if there is no page to draw, and that
 * attempt did restore everything, does it call the firmware console; otherwise no
 * firmware code runs under a vector base, HCR_EL2 or interrupt state this image
 * could not restore. Then it stops. (A reset would run the firmware's reset path
 * under that same state.) */
__attribute__((noreturn)) static void cleanup_halt(const char *where)
{
    probe_payload rows[8];
    probe_sb sb;
    unsigned n = 0;
    int restored;

    restored = tests_sys_quiesce() == 0;
    g_in_scope = 0;
    probe_sb_init(&sb, rows[n++], sizeof rows[0]);
    probe_sb_puts(&sb, "FATAL: CLEANUP FAILED");
    probe_sb_init(&sb, rows[n++], sizeof rows[0]);
    probe_sb_puts(&sb, where);
    probe_sb_init(&sb, rows[n++], sizeof rows[0]);
    probe_sb_puts(&sb, "LAST TEST=T0");
    probe_sb_dec(&sb, (uint64_t)g_current_test);
    probe_sb_init(&sb, rows[n++], sizeof rows[0]);
    probe_sb_puts(&sb, restored ? "RETRY RESTORED THE FIRMWARE STATE" : "FIRMWARE STATE NOT RESTORED");
    probe_sb_init(&sb, rows[n++], sizeof rows[0]);
    probe_sb_puts(&sb, "NO RESULTS WRITTEN. NO EXITBS.");
    probe_sb_init(&sb, rows[n++], sizeof rows[0]);
    probe_sb_puts(&sb, "POWER CYCLE. DO NOT TRUST RESULTS.");
    if (!draw_fatal_rows(rows, n) && !g_post && restored) {
        char line[120];

        probe_sb_init(&sb, line, sizeof line);
        probe_sb_puts(&sb, "SP11X2 FATAL CLEANUP FAILED ");
        probe_sb_puts(&sb, where);
        probe_sb_putc(&sb, '\n');
        emit_console(line);
    }
    probe_cpu_park();
}

/* ---- Running a test ---- */

static uint32_t build_id_number(const char *id)
{
    uint32_t v = 0;

    for (unsigned i = 0; i < 8 && id[i] != 0; i++) {
        char c = id[i];
        unsigned d = (c >= '0' && c <= '9') ? (unsigned)(c - '0')
                     : (c >= 'A' && c <= 'F') ? (unsigned)(c - 'A' + 10)
                     : (c >= 'a' && c <= 'f') ? (unsigned)(c - 'a' + 10)
                                              : 0u;
        v = (v << 4) | d;
    }
    return v;
}

static void ensure_init(const tests_hook_ctx *c)
{
    if (g_hc != NULL)
        return;
    g_hc = c;
    for (unsigned id = 1; id <= TESTS_COUNT; id++)
        tests_init_result(&g_res[id], (int)id);
    g_tc.bus = tests_sys_bus();
    g_tc.scan = c->scan;
    g_tc.phys = c->ops;
    g_tc.phys_ctx = c->ops_ctx;
    g_tc.memmap = c->memmap;
    g_tc.desc = &tests_desc_data;
    g_tc.zva = tests_sys_zva_ops();
    g_tc.model = TESTS_MODEL_BUILD;
    g_tc.require_mmio_map = TESTS_REQUIRE_MMIO_IN_MAP;
    g_tc.acpi_buf = g_acpi_buf;
    g_tc.progress = progress;
    tests_sys_set_fatal(fatal_draw);
}

static void run_test(int id, int phase)
{
    tests_result *r = &g_res[id];
    probe_payload head;
    char num[8];
    probe_sb sb;
    int scope_rc;

    g_current_test = id;
    tests_init_result(r, id);
    tests_footer_running(head, id, r->name);
    progress(NULL, head);
    probe_sb_init(&sb, num, sizeof num);
    probe_sb_dec(&sb, (uint64_t)id);
    marker("RUN", "ID", num, "NAME", r->name, NULL, NULL);

    scope_rc = scope_enter();
    if (scope_rc == TESTS_CLEANUP_FAILED)
        cleanup_halt("SCOPE ENTER ROLLBACK FAILED");
    if (scope_rc != 0) {
        r->verdict = TV_UNKNOWN;
        tests_row_text(r, "VECTOR SCOPE FAILED: NOT RUN");
        tests_result_seal(r);
        return;
    }
    switch (id) {
    case T_ID:
        tests_run_id(&g_tc, r);
        break;
    case T_NV2:
        tests_run_nv2(&g_tc, tests_sys_nv2_ops(), TESTS_SOC, build_id_number(g_hc->facts->build_id),
                      r, &g_rec, &g_have_rec);
        break;
    case T_TIMER:
        tests_run_timer(&g_tc, r);
        break;
    case T_ZVA:
        tests_run_zva(&g_tc, r);
        break;
    case T_GIC:
        tests_run_gic(&g_tc, r);
        break;
    case T_PCIE:
        tests_run_pcie(&g_tc, r);
        break;
    case T_SMMU:
        tests_run_smmu(&g_tc, r);
        break;
    case T_ADSP:
        tests_run_adsp(&g_tc, r);
        break;
    case T_DRAM:
        tests_run_dram(&g_tc, r);
        break;
    default:
        break;
    }
    r->phase = (uint8_t)phase;
    /* Whatever the test reported, a failed restore ends the run here: nothing is
     * persisted, no later test runs and ExitBootServices is never reached. The
     * sticky flag also covers a restore whose result an inner step dropped. */
    if (scope_leave() != 0)
        cleanup_halt("VBAR/DAIF/ELR/SPSR RESTORE FAILED");
    if (r->fatal || tests_sysstate_cleanup_failed())
        cleanup_halt("NV2/EL1 STATE RESTORE FAILED");
    probe_sb_init(&sb, num, sizeof num);
    probe_sb_dec(&sb, (uint64_t)id);
    marker("RESULT", "ID", num, "VERDICT", tests_verdict_name(r->verdict), "LABEL",
           tests_label_name(r->label, TESTS_MODEL_BUILD));
}

/* ---- Self-tests of the machinery (emulator builds only) ---- */

#if TESTS_SELFTEST
static void selftest(void)
{
    uint32_t v = 0;
    uint64_t q = 0;
    tests_el1_exit e;
    const tests_nv2_ops *ops = tests_sys_nv2_ops();
    char tmp[24];
    probe_sb sb;
    unsigned f0;
    int rc;

    /* Every marker below is produced inside the scope and so is held back until
     * the scope is left (see con_puts). */
    rc = scope_enter();
    if (rc == TESTS_CLEANUP_FAILED)
        cleanup_halt("SELFTEST SCOPE ROLLBACK FAILED");
    if (rc != 0) {
        marker("SELFTEST", "NAME", "SCOPE", "RESULT", "FAILED", NULL, NULL);
        return;
    }
    f0 = tests_sys_fault_count();
    (void)tests_sys_bus()->rd32(NULL, 0x0b000000ull, &v);
    probe_sb_init(&sb, tmp, sizeof tmp);
    probe_sb_hex(&sb, v, 8);
    marker("SELFTEST", "NAME", "UNMAPPED_READ", "VALUE", tmp, "FAULTS",
           tests_sys_fault_count() - f0 == 1 ? "1" : "OTHER");
    f0 = tests_sys_fault_count();
    (void)tests_sys_selftest_undef(&q);
    probe_sb_init(&sb, tmp, sizeof tmp);
    probe_sb_hex(&sb, q, 16);
    marker("SELFTEST", "NAME", "UNDEF_MRS", "VALUE", tmp, "FAULTS",
           tests_sys_fault_count() - f0 == 1 ? "1" : "OTHER");
    if (scope_leave() != 0)
        cleanup_halt("SELFTEST SCOPE RESTORE FAILED");

    /* An access to an EL2 register from EL1 with NV off is UNDEFINED at EL1: the
     * EL1 vector must hand it back as HVC #0x104 (vector 4, current EL, sync). */
    rc = scope_enter();
    if (rc == TESTS_CLEANUP_FAILED)
        cleanup_halt("SELFTEST SCOPE ROLLBACK FAILED");
    if (rc == 0) {
        probe_memset(&e, 0, sizeof e);
        rc = ops->prepare(NULL);
        if (rc == TESTS_CLEANUP_FAILED)
            cleanup_halt("SELFTEST EL1 PREPARE ROLLBACK FAILED");
        if (rc == 0) {
            int run = ops->set_hcr(NULL, 0, 0, 0) == 0 ? ops->run(NULL, EL1_PROG_CONTROL, &e) : -1;
            int rest = ops->restore(NULL);

            probe_sb_init(&sb, tmp, sizeof tmp);
            probe_sb_hex(&sb, e.imm, 3);
            marker("SELFTEST", "NAME", "EL1_UNDEF", "RUN", run == 0 ? "OK" : "FAILED", "IMM", tmp);
            marker("SELFTEST", "NAME", "EL1_RESTORE", "RESULT", rest == 0 ? "OK" : "FAILED", NULL,
                   NULL);
            if (rest != 0)
                cleanup_halt("SELFTEST EL1 RESTORE FAILED");
        } else {
            marker("SELFTEST", "NAME", "EL1_UNDEF", "RESULT", "PREPARE_FAILED", NULL, NULL);
        }
        if (scope_leave() != 0)
            cleanup_halt("SELFTEST SCOPE RESTORE FAILED");
    }
}
#endif

/* ---- Results on the stick ---- */

static void put_result_text(probe_sb *sb, const tests_result *r)
{
    probe_sb_puts(sb, "test=T0");
    probe_sb_dec(sb, r->id);
    probe_sb_puts(sb, " name=");
    probe_sb_puts(sb, r->name);
    probe_sb_puts(sb, " verdict=");
    probe_sb_puts(sb, tests_verdict_name(r->verdict));
    probe_sb_puts(sb, " label=");
    probe_sb_puts(sb, tests_label_name(r->label, TESTS_MODEL_BUILD));
    probe_sb_puts(sb, " phase=");
    probe_sb_dec(sb, r->phase);
    probe_sb_putc(sb, '\n');
    for (unsigned i = 0; i < r->nrows; i++) {
        probe_sb_puts(sb, "  ");
        probe_sb_puts(sb, r->rows[i]);
        probe_sb_putc(sb, '\n');
    }
    if (r->proves != NULL) {
        probe_sb_puts(sb, "  ");
        probe_sb_puts(sb, r->proves);
        probe_sb_putc(sb, '\n');
    }
    if (r->not_proves != NULL) {
        probe_sb_puts(sb, "  ");
        probe_sb_puts(sb, r->not_proves);
        probe_sb_putc(sb, '\n');
    }
}

static void persist_phase_a(void)
{
    const probe_facts *f = g_hc->facts;
    tests_file files[4];
    unsigned nfiles = 0, written = 0;
    probe_sb sb;
    char num[8];
    int state;
    size_t len;

    if (f->dump_state != PROBE_DUMP_OK && f->dump_state != PROBE_DUMP_PARTIAL) {
        marker("STICK", "STATE", "NOT ATTEMPTED", "REASON", "NO DUMP RUN DIRECTORY", NULL, NULL);
        return;
    }
    probe_sb_init(&sb, g_results_text, sizeof g_results_text);
    probe_sb_puts(&sb, "# SP11X2 results v1 (tests run before ExitBootServices)\n");
    probe_sb_puts(&sb, "build=");
    probe_sb_puts(&sb, f->build_id);
    probe_sb_puts(&sb, "\nkind=" KIND_TEXT "\n");
    for (unsigned id = 1; id <= TESTS_COUNT; id++)
        if ((TESTS_PHASE_A_MASK >> id) & 1u)
            put_result_text(&sb, &g_res[id]);
    files[nfiles].name = "x2-results.txt";
    files[nfiles].data = g_results_text;
    files[nfiles].len = sb.overflow ? 0 : sb.len;
    nfiles++;
    if (g_have_rec) {
        len = tests_nv2rec_text(&g_rec, g_nv2_text, sizeof g_nv2_text);
        if (len != 0) {
            files[nfiles].name = "x2-nv2.txt";
            files[nfiles].data = g_nv2_text;
            files[nfiles].len = len;
            nfiles++;
        }
        len = tests_nv2rec_json(&g_rec, g_nv2_json, sizeof g_nv2_json);
        if (len != 0) {
            files[nfiles].name = "x2-nv2.json";
            files[nfiles].data = g_nv2_json;
            files[nfiles].len = len;
            nfiles++;
        }
        if (tests_nv2rec_pack(&g_rec, g_nv2_bin) == 0) {
            files[nfiles].name = "x2-nv2.bin";
            files[nfiles].data = g_nv2_bin;
            files[nfiles].len = sizeof g_nv2_bin;
            nfiles++;
        }
    }
    state = tests_stick_write(g_hc->st, g_hc->image, f->dump_run, files, nfiles, &written);
    probe_sb_init(&sb, num, sizeof num);
    probe_sb_dec(&sb, written);
    marker("STICK", "STATE", tests_stick_state_name(state), "FILES", num, NULL, NULL);
}

/* ---- Phase A ---- */

int tests_hook_pre_ebs(const tests_hook_ctx *c)
{
    static const int order_a[] = {T_ID, T_TIMER, T_ZVA, T_NV2};

    ensure_init(c);
    marker("BEGIN", "KIND", KIND_TEXT, "BUILD", c->facts->build_id, "FAST",
           PROBE_FAST_TEST ? "1" : "0");
    /* The report must be showable afterwards: with no layout for the largest
     * report there is no point in exiting boot services. The validated layout is
     * kept, so that a failure before ExitBootServices can put a fatal page on the
     * framebuffer instead of a console line that may be invisible on this
     * machine. */
    g_have_layout = 0;
    if (c->fb == NULL ||
        tests_layout_choose(c->fb->width, c->fb->height, TESTS_REPORT_MAX_ROWS, LAYOUT_MAX_PAGES,
                            &g_layout) != PROBE_LAYOUT_OK ||
        tests_layout_fits(&g_layout, c->fb->width, c->fb->height) != 0) {
        marker("REFUSE", "REASON", "NO LAYOUT FOR THE REPORT", NULL, NULL, NULL, NULL);
        return 1;
    }
    g_have_layout = 1;
    c->fb->flush = probe_cpu_flush_dcache;
    c->fb->flush_ctx = NULL;
    if (!c->facts->have_preebs_el || probe_currentel_decode(c->facts->preebs_el_raw) != 2) {
        marker("SKIP", "REASON", "NOT_EL2", NULL, NULL, NULL, NULL);
        return 0;
    }
#if TESTS_SELFTEST
    selftest();
#endif
    for (unsigned i = 0; i < sizeof order_a / sizeof order_a[0]; i++)
        run_test(order_a[i], 1);
#if TESTS_SELFTEST_FATAL_PRE
    /* A load from an unmapped address with no guard armed, before ExitBootServices,
     * must end in the fatal page on the screen, never in a silent hang. */
    if (scope_enter() == 0) {
        g_current_test = 98;
        (void)*(volatile uint32_t *)(uintptr_t)0x0b000000ull;
    }
#endif
    if (tests_sysstate_cleanup_failed())
        cleanup_halt("BEFORE THE RESULTS ARE WRITTEN");
    persist_phase_a();
    return 0;
}

/* ---- Phase B and the display ---- */

static void rebuild_report(void)
{
    tests_report_build(&g_rep, g_res, TESTS_MODEL_BUILD);
}

static void draw_page(unsigned page, unsigned pages, const char *footer)
{
    probe_payload h0, h1, ft;

    tests_report_header0(h0, g_hc->facts->build_id, TESTS_MODEL_BUILD);
    tests_report_header1(h1, 2, page + 1, pages);
    tests_footer_text(ft, footer);
    probe_draw_page(g_hc->fb, &g_layout, h0, h1, g_rep.row, g_rep.count, page, ft);
}

static void spin_one_second(void)
{
    for (volatile uint64_t i = 0; i < FALLBACK_SECOND_SPINS; i++)
        ;
}

static void wait_one_second(uint64_t freq)
{
    if (freq != 0) {
        uint64_t start = probe_cpu_counter();

        while (probe_cpu_counter() - start < freq)
            ;
    } else {
        spin_one_second();
    }
}

__attribute__((noreturn)) static void halt_not_el2(uint64_t raw)
{
    probe_payload rows[3];
    probe_payload h0, h1, ft;
    probe_layout l;
    probe_sb sb;

    probe_sb_init(&sb, rows[0], sizeof rows[0]);
    probe_sb_puts(&sb, "NOT AT EL2: NO TEST WAS RUN");
    probe_sb_init(&sb, rows[1], sizeof rows[1]);
    probe_sb_puts(&sb, "CURRENTEL RAW=");
    probe_sb_hex(&sb, raw, 16);
    probe_sb_init(&sb, rows[2], sizeof rows[2]);
    probe_sb_puts(&sb, "NOTHING WAS READ OR WRITTEN");
    tests_report_header0(h0, g_hc->facts->build_id, TESTS_MODEL_BUILD);
    tests_report_header1(h1, -1, 1, 1);
    probe_report_footer(ft, PROBE_FOOTER_HALTED, 0);
    g_hc->fb->flush = NULL; /* no cache maintenance on this path */
    if (probe_layout_choose(g_hc->fb->width, g_hc->fb->height, 3, &l) == PROBE_LAYOUT_OK)
        probe_draw_page(g_hc->fb, &l, h0, h1, rows, 3, 0, ft);
    probe_cpu_park();
}

__attribute__((noreturn)) void tests_hook_post_ebs(const tests_hook_ctx *c)
{
    /* Mirrors tests_hook_pre_ebs's order_a (same "least intrusive first" reason:
     * ID dump, timer, DC ZVA, then the NV2 mechanics). A platform whose only path
     * to EL2 is slbounce's ExitBootServices hook itself (OBSERVED twice on a
     * Surface Pro 11 X1E80100: preebs_currentel decoded to EL1 both times,
     * because the Secure-Launch SMC sequence runs inside that hook, after the
     * firmware's own ExitBootServices has already returned) never gets to run
     * these four in phase A: tests_hook_pre_ebs's CurrentEL check takes the SKIP
     * branch there, before order_a's loop, so g_res[id].phase is still 0 (never
     * run) for each of them. Phase B has just proven EL2 itself (the check
     * above), so this is its first chance. A platform that did reach EL2 before
     * ExitBootServices already ran and persisted these to the stick in phase A
     * (g_res[id].phase is 1 there); this loop must not repeat that work. */
    static const int order_a[] = {T_ID, T_TIMER, T_ZVA, T_NV2};
    static const int order_b[] = {T_DRAM, T_GIC, T_SMMU, T_PCIE, T_ADSP};
    uint64_t el_raw = tests_sys_currentel();
    uint64_t freq;
    unsigned pages, per_page, countdown;

    ensure_init(c);
    g_post = 1;
    if (probe_currentel_decode(el_raw) != 2)
        halt_not_el2(el_raw);

    c->fb->flush = probe_cpu_flush_dcache;
    c->fb->flush_ctx = NULL;
    if (tests_layout_choose(c->fb->width, c->fb->height, TESTS_REPORT_MAX_ROWS, LAYOUT_MAX_PAGES,
                            &g_layout) != PROBE_LAYOUT_OK)
        probe_cpu_park();
    g_have_layout = 1;

    rebuild_report();
    draw_page(0, 1, "PHASE B STARTING");
    /* Nothing below may reach the stick: persist_phase_a() is phase-A-only and
     * is never called from here. T_NV2's record still reaches the operator when
     * it runs here, through its own per-test rows: tests_run_nv2 (t_misc.c)
     * already appends tests_nv2rec_screen_rows()'s 13 "NV2R ..." lines to
     * r->rows[] whenever it has a record, in either phase, so rebuild_report()
     * picks them up with no separate wiring and no risk of duplicating a record
     * phase A already wrote to x2-nv2.bin. */
    for (unsigned i = 0; i < sizeof order_a / sizeof order_a[0]; i++) {
        if (g_res[order_a[i]].phase != 0)
            continue;
        run_test(order_a[i], 2);
        rebuild_report();
        draw_page(0, 1, "TEST DONE");
    }
    for (unsigned i = 0; i < sizeof order_b / sizeof order_b[0]; i++) {
        run_test(order_b[i], 2);
        rebuild_report();
        draw_page(0, 1, "TEST DONE");
    }
#if TESTS_SELFTEST_FATAL
    /* A load from an unmapped address with no guard armed must end in the fatal
     * page, never in a silent skip. */
    if (scope_enter() == 0) {
        g_current_test = 99;
        (void)*(volatile uint32_t *)(uintptr_t)0x0b000000ull;
    }
#endif
    rebuild_report();
    per_page = g_layout.cols * g_layout.body_rows;
    pages = (g_rep.count + per_page - 1u) / per_page;
    if (pages == 0)
        pages = 1;
    countdown = tests_countdown_seconds(pages, DWELL_S);
    freq = probe_cpu_counter_freq();
    {
        unsigned shown = ~0u;

        for (unsigned sec = 0;; sec++) {
            unsigned page = (sec / DWELL_S) % pages;
            probe_payload ft;

            if (TESTS_NO_RESET) {
                probe_report_footer(ft, PROBE_FOOTER_HOLD, 0);
            } else {
                if (sec >= countdown)
                    break;
                probe_report_footer(ft, PROBE_FOOTER_COUNTDOWN, countdown - sec);
            }
            if (page != shown) {
                draw_page(page, pages, ft);
                shown = page;
            } else {
                probe_draw_footer(g_hc->fb, &g_layout, ft);
            }
            wait_one_second(freq);
        }
    }
    {
        probe_payload ft;

        probe_report_footer(ft, PROBE_FOOTER_RESETTING, 0);
        probe_draw_footer(g_hc->fb, &g_layout, ft);
        g_hc->reset_system(EFI_RESET_COLD, EFI_SUCCESS, 0, NULL);
        probe_report_footer(ft, PROBE_FOOTER_RESET_FAILED, 0);
        probe_draw_footer(g_hc->fb, &g_layout, ft);
    }
    probe_cpu_park();
}
