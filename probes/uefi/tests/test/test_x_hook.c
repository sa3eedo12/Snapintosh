/* SPDX-License-Identifier: MIT */
/* Integration tests of the X2 image's driver, probes/uefi/tests/hook.c.
 *
 * hook.c is #included here because its state is static. Everything it reaches
 * for on the machine is replaced by a model: a register file behind the real
 * tests_scope_* and tests_sysstate_* code (with registers that can be made to
 * ignore, refuse or misreport writes), an NV2 core model whose operations use
 * that same register file, the firmware console, a framebuffer and the USB
 * writer. The CPU parks through longjmp so a test can see that the image
 * stopped.
 *
 * What these tests pin down is the run's behaviour when a register cannot be
 * put back: it must stop (no further test, no file on the stick, no return to the
 * caller that would go on to ExitBootServices), say so on the framebuffer, and
 * never run firmware code while this image's vectors are installed.
 */
#include <setjmp.h>

#include "xtests.h"

#include "cpu.h"
#include "regs.h"
#include "report_x.h"
#include "suite.h"
#include "sys.h"
#include "tests_hook.h"

/* The stick writer has its own tests; here it only records that it was called. */
#define tests_stick_write hook_stub_stick_write
#include "stick.h"

#define H_VECTORS 0x40000ull

/* ---- The register file ---- */

typedef struct {
    uint64_t reg[W_COUNT];
    unsigned stuck;                 /* mask: writes are ignored */
    unsigned fail_write;            /* mask: writes return an error */
    unsigned lie_on_read;           /* mask: reads are wrong once the register was written */
    unsigned freeze_after[W_COUNT]; /* writes beyond this many are ignored; 0 never */
    unsigned ignore_write[W_COUNT]; /* this one write (1-based) is ignored, the rest take */
    unsigned nwrites[W_COUNT];
    unsigned written_once;
} hbank;

static hbank hb;
static tests_sysstate h_scope;
static tests_sysstate h_nv2s;

static int hb_rd(void *ctx, int w, uint64_t *v)
{
    (void)ctx;
    if (w < 0 || w >= W_COUNT)
        return -1;
    *v = hb.reg[w];
    if ((hb.lie_on_read & (1u << w)) && (hb.written_once & (1u << w)))
        *v ^= 1u;
    return 0;
}

static int hb_wr(void *ctx, int w, uint64_t v)
{
    (void)ctx;
    if (w < 0 || w >= W_COUNT)
        return -1;
    if (hb.fail_write & (1u << w))
        return -1;
    hb.nwrites[w]++;
    hb.written_once |= 1u << w;
    if ((hb.stuck & (1u << w)) || (hb.freeze_after[w] != 0 && hb.nwrites[w] > hb.freeze_after[w]) ||
        (hb.ignore_write[w] != 0 && hb.nwrites[w] == hb.ignore_write[w]))
        return 0;
    hb.reg[w] = v;
    return 0;
}

static const tests_wops h_wops = {NULL, hb_rd, hb_wr};

/* ---- The machine as the hook sees it ---- */

static unsigned h_quiesces, h_scope_enters, h_scope_leaves;
static int h_last_quiesce = -2; /* the result of the last tests_sys_quiesce, -2 if none was made */
static unsigned h_stick_calls, h_stick_files;
static char h_stick_names[16][32];
static tests_fatal_fn h_fatal_fn;
static unsigned h_parked;
static jmp_buf h_park_env;
static unsigned h_seq;                 /* orders console and flush calls */
static unsigned h_con_calls, h_con_calls_in_scope, h_con_last_seq;
static unsigned h_flush_calls, h_flush_last_seq;
static char h_con[8192];
static size_t h_con_len;
static void (*h_con_hook)(void); /* runs after every console write, to inject a late event */
static unsigned h_idreads;
static int h_restore_drops_result;     /* an NV2 restore that loses its own failure */
static int h_vncr_present;
static int h_clobber_on_read;          /* the first register read stands for an exception */
static unsigned h_resets;              /* tests_hook_post_ebs reached g_hc->reset_system */

uint64_t probe_cpu_read_sysreg(int id)
{
    (void)id;
    return 0;
}

void probe_cpu_flush_dcache(void *ctx, const void *addr, size_t len)
{
    (void)ctx;
    (void)addr;
    (void)len;
    h_flush_calls++;
    h_flush_last_seq = ++h_seq;
}

uint64_t probe_cpu_counter(void)
{
    static uint64_t t;

    return t += 7;
}

uint64_t probe_cpu_counter_freq(void)
{
    return 1000000;
}

void probe_cpu_park(void)
{
    h_parked++;
    longjmp(h_park_env, 1);
}

/* tests_hook_ctx.reset_system, the cold reset tests_hook_post_ebs calls once the
 * report's countdown ends. A real reset never returns; the model represents
 * that the same way probe_cpu_park() does, so run_post() (defined with the
 * rest of the tests' world, below) can observe that the image reached here. */
static void h_reset_system(uint32_t type, efi_status status, efi_uintn size, void *data)
{
    (void)type;
    (void)status;
    (void)size;
    (void)data;
    h_resets++;
    probe_cpu_park();
}

/* An exception handler that is entered is the fatal path: the model never takes
 * exceptions, so only the callback registration is recorded. */
void tests_sys_set_fatal(tests_fatal_fn fn)
{
    h_fatal_fn = fn;
}

uint64_t tests_sys_currentel(void)
{
    return 8;
}

unsigned tests_sys_fault_count(void)
{
    return 0;
}

/* tests_sys_scope_enter is defined below, after hook.c's text is included: it
 * records g_current_test, which is hook.c's own static and so is not visible
 * at this point in the file (the same reason h_reset() below touches g_res,
 * g_post and the other hook.c statics only after that #include). */

int tests_sys_scope_leave(void)
{
    h_scope_leaves++;
    return tests_scope_leave(&h_scope, &h_wops);
}

int tests_sys_quiesce(void)
{
    int rc;

    h_quiesces++;
    rc = tests_sysstate_restore(&h_nv2s, &h_wops) != 0;
    rc |= tests_sysstate_restore(&h_scope, &h_wops) != 0;
    h_last_quiesce = rc ? -1 : 0;
    return h_last_quiesce;
}

/* ---- Registers the tests read ---- */

static uint64_t h_sysval[TR_COUNT];

static int h_rd_sys(void *ctx, int id, uint64_t *v)
{
    (void)ctx;
    h_idreads++;
    if (h_clobber_on_read) {
        /* What the first exception taken to this image's vectors does. */
        hb.reg[W_ELR_EL2] = 0xDEADDEADDEADDEADull;
        hb.reg[W_SPSR_EL2] = 0xBEEFBEEFull;
        h_clobber_on_read = 0;
    }
    if (id < 0 || id >= TR_COUNT)
        return -1;
    if (id == TR_CNTPCT_EL0 || id == TR_CNTVCT_EL0) {
        *v = probe_cpu_counter();
        return 0;
    }
    *v = h_sysval[id];
    return 0;
}

static int h_rd32(void *ctx, uint64_t addr, uint32_t *v)
{
    (void)ctx;
    (void)addr;
    *v = 0xFFFFFFFFu;
    return -1;
}

static unsigned h_faults(void *ctx)
{
    (void)ctx;
    return 0;
}

static const tests_bus h_bus = {NULL, h_rd32, h_rd_sys, h_faults};

const tests_bus *tests_sys_bus(void)
{
    return &h_bus;
}

static uint8_t h_zva_buf[TESTS_ZVA_BUFFER_BYTES] __attribute__((aligned(4096)));

static int h_zva(void *ctx, uint8_t *addr)
{
    (void)ctx;
    memset((void *)((uintptr_t)addr & ~(uintptr_t)63), 0, 64);
    return 0;
}

static void h_zva_sync(void *ctx)
{
    (void)ctx;
}

static const tests_zva_ops h_zva_ops = {NULL, h_zva_buf, h_zva, h_zva_sync};

const tests_zva_ops *tests_sys_zva_ops(void)
{
    return &h_zva_ops;
}

/* ---- The NV2 core, over the same register file ---- */

static uint64_t h_page[TESTS_NV2_PAGE_WORDS] __attribute__((aligned(4096)));

static int h_prepare(void *ctx)
{
    unsigned mask = TESTS_WMASK_NV2;

    (void)ctx;
    if (h_vncr_present)
        mask |= TESTS_WMASK(W_VNCR_EL2);
    if (tests_sysstate_save(&h_nv2s, &h_wops, mask) != 0)
        return TESTS_ERR;
    if (tests_sysstate_write_verified(&h_nv2s, &h_wops, W_SCTLR_EL1X, 0x30D00800ull, 0x2081007ull) != 0 ||
        tests_sysstate_write_verified(&h_nv2s, &h_wops, W_VBAR_EL1X, 0x50000ull, ~0ull) != 0 ||
        tests_sysstate_write_verified(&h_nv2s, &h_wops, W_SP_EL1, 0x60000ull, ~0ull) != 0)
        return tests_sysstate_restore(&h_nv2s, &h_wops) == 0 ? TESTS_ERR : TESTS_CLEANUP_FAILED;
    return 0;
}

static int h_set_hcr(void *ctx, int nv, int nv2, int map)
{
    (void)ctx;
    if (map && (!h_vncr_present ||
                tests_sysstate_write_verified(&h_nv2s, &h_wops, W_VNCR_EL2,
                                              (uint64_t)(uintptr_t)h_page, ~0ull) != 0))
        return -1;
    return tests_sysstate_write_verified(&h_nv2s, &h_wops, W_HCR_EL2,
                                         tests_hcr_for(h_nv2s.saved[W_HCR_EL2], nv, nv2), ~0ull);
}

static int h_run(void *ctx, int program, tests_el1_exit *out)
{
    (void)ctx;
    memset(out, 0, sizeof *out);
    if (program == EL1_PROG_CONTROL) {
        out->kind = EXIT_SYSREG;
        out->esr = (0x18ull << 26) | (1ull << 25) | (3ull << 20) | (2ull << 17) | (4ull << 14) |
                   (13ull << 10) | (1ull << 5) | 1u;
        return 0;
    }
    h_page[TESTS_NV2_TARGET_OFFSET / 8] = TESTS_NV2_PATTERN;
    out->kind = EXIT_HVC;
    out->imm = TESTS_HVC_DONE_REDIRECT;
    out->x[0] = TESTS_NV2_PATTERN;
    out->x[1] = TESTS_NV2_PATTERN;
    return 0;
}

static int h_real_tpidr(void *ctx, uint64_t *v)
{
    (void)ctx;
    *v = 0x1111;
    return 0;
}

static uint64_t *h_page_fn(void *ctx)
{
    (void)ctx;
    return h_page;
}

static void h_page_sync(void *ctx)
{
    (void)ctx;
}

static int h_restore(void *ctx)
{
    int rc;

    (void)ctx;
    rc = tests_sysstate_restore(&h_nv2s, &h_wops);
    return h_restore_drops_result ? 0 : rc;
}

static const tests_nv2_ops h_nv2_ops = {NULL,       h_prepare,   h_set_hcr,    h_run,
                                        h_real_tpidr, h_page_fn, h_page_sync, h_restore};

const tests_nv2_ops *tests_sys_nv2_ops(void)
{
    return &h_nv2_ops;
}

/* ---- The USB writer ---- */

int hook_stub_stick_write(efi_system_table *st, efi_handle image, unsigned run,
                          const tests_file *files, unsigned n, unsigned *written)
{
    (void)st;
    (void)image;
    (void)run;
    h_stick_calls++;
    h_stick_files = n;
    for (unsigned i = 0; i < n && i < 16; i++) {
        strncpy(h_stick_names[i], files[i].name, sizeof h_stick_names[i] - 1);
        h_stick_names[i][sizeof h_stick_names[i] - 1] = 0;
    }
    *written = n;
    return STICK_OK;
}

/* ---- The firmware console ---- */

static efi_status h_output_string(efi_simple_text_output *self, const efi_char16 *text)
{
    (void)self;
    h_con_calls++;
    h_con_last_seq = ++h_seq;
    /* Firmware code is running: this image's vector table must not be installed. */
    if (hb.reg[W_VBAR_EL2] == H_VECTORS)
        h_con_calls_in_scope++;
    for (; *text != 0 && h_con_len + 1 < sizeof h_con; text++)
        h_con[h_con_len++] = (char)(*text & 0x7F);
    h_con[h_con_len] = 0;
    if (h_con_hook != NULL)
        h_con_hook();
    return EFI_SUCCESS;
}

static efi_simple_text_output h_conout = {.output_string = h_output_string};

/* hook.c itself: its state is static, so the tests live in this translation unit. */
#include "../hook.c"

/* Defined here, not with tests_sys_scope_leave above: run_test() sets
 * g_current_test (hook.c's own static) immediately before calling scope_enter(),
 * in both phases, so recording it here gives the exact order tests actually ran
 * in, whichever phase ran each one. */
static int h_run_seq[TESTS_COUNT + 4]; /* +4: room to catch an accidental repeat */
static unsigned h_run_seq_n;

int tests_sys_scope_enter(void)
{
    h_scope_enters++;
    if (h_run_seq_n < sizeof h_run_seq / sizeof h_run_seq[0])
        h_run_seq[h_run_seq_n++] = g_current_test;
    return tests_scope_enter(&h_scope, &h_wops, H_VECTORS);
}

/* ---- The tests' world ---- */

#define H_W 800u
#define H_H 600u

static uint8_t h_fbmem[(size_t)H_W * H_H * 4];
static probe_fb h_fb;
static probe_facts h_facts;
static probe_acpi_scan h_scan;
static probe_memmap h_mm;
static probe_phys_ops h_pops;
static efi_system_table h_st;
static tests_hook_ctx h_ctx;

static void h_reset(void)
{
    tests_idset ids;

    memset(&hb, 0, sizeof hb);
    for (int i = 0; i < W_COUNT; i++)
        hb.reg[i] = 0x1000ull + (uint64_t)i * 0x111;
    hb.reg[W_DAIF] = 0; /* interrupts unmasked, as firmware leaves them */
    memset(&h_scope, 0, sizeof h_scope);
    memset(&h_nv2s, 0, sizeof h_nv2s);
    tests_sysstate_cleanup_reset();
    h_quiesces = h_scope_enters = h_scope_leaves = 0;
    h_last_quiesce = -2;
    h_stick_calls = h_stick_files = 0;
    memset(h_stick_names, 0, sizeof h_stick_names);
    h_fatal_fn = NULL;
    h_parked = 0;
    h_seq = 0;
    h_con_calls = h_con_calls_in_scope = h_con_last_seq = 0;
    h_flush_calls = h_flush_last_seq = 0;
    h_con_len = 0;
    h_con[0] = 0;
    h_con_hook = NULL;
    h_idreads = 0;
    h_restore_drops_result = 0;
    h_clobber_on_read = 0;
    h_vncr_present = 1;
    h_resets = 0;
    h_run_seq_n = 0;
    memset(h_run_seq, 0, sizeof h_run_seq);
    memset(h_page, 0, sizeof h_page);

    /* A plausible core: ID registers of a core with NV2, a counter that advances. */
    x_good_ids(&ids);
    memset(h_sysval, 0, sizeof h_sysval);
    for (int ti = 0; ti < TI_COUNT; ti++)
        h_sysval[tests_ti_sysreg(ti)] = ids.v[ti];
    h_sysval[TR_CNTFRQ_EL0] = 1000000;

    /* hook.c's own state. */
    memset(g_res, 0, sizeof g_res);
    memset(&g_tc, 0, sizeof g_tc);
    memset(&g_rep, 0, sizeof g_rep);
    memset(&g_rec, 0, sizeof g_rec);
    g_have_rec = 0;
    g_post = 0;
    g_hc = NULL;
    memset(&g_layout, 0, sizeof g_layout);
    g_have_layout = 0;
    g_current_test = 0;
    g_in_scope = 0;
    g_deferred_len = 0;
    g_deferred_dropped = 0;
    g_deferred[0] = 0;

    memset(h_fbmem, 0, sizeof h_fbmem);
    CHECK_EQ(probe_fb_init(&h_fb, (uint64_t)(uintptr_t)h_fbmem, sizeof h_fbmem, H_W, H_H, H_W,
                           PROBE_PIXEL_BGRX), 0);
    memset(&h_facts, 0, sizeof h_facts);
    memcpy(h_facts.build_id, "TESTBUILD001", 12);
    h_facts.have_preebs_el = 1;
    h_facts.preebs_el_raw = 8;
    h_facts.dump_state = PROBE_DUMP_OK;
    h_facts.dump_run = 1;
    memset(&h_scan, 0, sizeof h_scan);
    memset(&h_mm, 0, sizeof h_mm);
    memset(&h_pops, 0, sizeof h_pops);
    memset(&h_st, 0, sizeof h_st);
    h_st.con_out = &h_conout;
    memset(&h_ctx, 0, sizeof h_ctx);
    h_ctx.st = &h_st;
    h_ctx.image = (efi_handle)(uintptr_t)1;
    h_ctx.facts = &h_facts;
    h_ctx.scan = &h_scan;
    h_ctx.memmap = &h_mm;
    h_ctx.ops = &h_pops;
    h_ctx.fb = &h_fb;
    h_ctx.reset_system = h_reset_system;
}

static unsigned fb_lit_bytes(void)
{
    unsigned n = 0;

    for (size_t i = 0; i < sizeof h_fbmem; i++)
        n += h_fbmem[i] != 0;
    return n;
}

static int con_has(const char *text)
{
    return strstr(h_con, text) != NULL;
}

static unsigned con_count(const char *text)
{
    unsigned n = 0;

    for (const char *p = h_con; (p = strstr(p, text)) != NULL; p += strlen(text))
        n++;
    return n;
}

/* Runs the pre-ExitBootServices phase. Returns 1 if the image parked (it never
 * returned to its caller, which is what would go on to ExitBootServices), else 0
 * with the hook's return value in *ret. */
static int run_pre(int *ret)
{
    *ret = -99;
    if (setjmp(h_park_env) == 0) {
        *ret = tests_hook_pre_ebs(&h_ctx);
        return 0;
    }
    return 1;
}

/* Runs the post-ExitBootServices phase to its end. It is declared noreturn and
 * never gives control back by itself (a fatal halt parks directly; a clean run
 * ends in h_reset_system, which parks in its place): the caller checks
 * h_parked == 1 afterwards to confirm it actually reached one of those halts
 * instead of silently falling off the end of the function. */
static void run_post(void)
{
    h_parked = 0;
    if (setjmp(h_park_env) == 0)
        tests_hook_post_ebs(&h_ctx);
}

/* ---- A clean run ---- */

void test_x_hook_clean(void)
{
    int ret;

    section("hook: clean run");
    h_reset();
    h_clobber_on_read = 1; /* an exception rewrites ELR_EL2 and SPSR_EL2 during the first test */
    CHECK_EQ(run_pre(&ret), 0);
    CHECK_EQ(ret, 0);
    CHECK_EQ(h_parked, 0);
    CHECK_EQ(h_stick_calls, 1);
    CHECK_STR(h_stick_names[0], "x2-results.txt");
    CHECK(h_stick_files >= 2); /* the results and the NV2 record */
    CHECK_EQ(h_scope_enters, 4); /* T01, T03, T04, T02 */
    CHECK_EQ(h_scope_leaves, 4);
    CHECK_EQ(h_quiesces, 0);
    CHECK_EQ(g_res[T_NV2].fatal, 0);
    CHECK_EQ(g_res[T_NV2].verdict, TV_GO);
    CHECK_EQ(g_have_rec, 1);
    CHECK_EQ(tests_sysstate_cleanup_failed(), 0);
    /* The registers are exactly as the firmware left them. */
    CHECK_EQU(hb.reg[W_VBAR_EL2], 0x1000);
    CHECK_EQU(hb.reg[W_HCR_EL2], 0x1000 + 0x111);
    CHECK_EQU(hb.reg[W_ELR_EL2], 0x1000 + 7 * 0x111);
    CHECK_EQU(hb.reg[W_SPSR_EL2], 0x1000 + 8 * 0x111);
    /* The layout was validated for the report and kept, and the framebuffer was
     * given its flush hook before ExitBootServices. */
    CHECK_EQ(g_have_layout, 1);
    CHECK(h_fb.flush == probe_cpu_flush_dcache);
    CHECK_EQ(g_post, 0);
    /* Nothing was written to the screen by a good run before ExitBootServices. */
    CHECK_EQ(fb_lit_bytes(), 0);
    /* Console output was held while this image's vectors were installed. */
    CHECK_EQ(h_con_calls_in_scope, 0);
    CHECK(h_con_calls > 0);
    CHECK_EQ(con_count("SP11X2 RUN"), 4);
    CHECK_EQ(con_count("SP11X2 RESULT"), 4);
    CHECK(con_has("SP11X2 STICK STATE=OK"));
}

/* ---- Cleanup failures end the run ---- */

static int late_rd(void *ctx, int w, uint64_t *v)
{
    (void)ctx;
    (void)w;
    *v = 0;
    return 0;
}

static int late_wr(void *ctx, int w, uint64_t v)
{
    (void)ctx;
    (void)w;
    (void)v;
    return -1;
}

/* A restore that fails somewhere no run_test check looks: it only sets the sticky
 * flag, which the hook must still notice before it writes anything. */
static void late_cleanup_failure(void)
{
    static const tests_wops refusing = {NULL, late_rd, late_wr};
    tests_sysstate s;

    if (strstr(h_con, "SP11X2 RESULT ID=2") == NULL)
        return;
    memset(&s, 0, sizeof s);
    s.have[W_DAIF] = 1;
    s.touched[W_DAIF] = 1;
    (void)tests_sysstate_restore(&s, &refusing);
}
/* Every assertion that says the image stopped for good. retry_restored says
 * whether the final restore attempt put the state back; it changes what the image
 * may do afterwards in principle (call firmware again), but with a page on the
 * framebuffer it does not call the console in either case: a console line after
 * the page scrolls it. When the retry could not restore the state no firmware code
 * runs with a vector base, HCR_EL2 or interrupt state this image could not
 * restore. */
static void expect_stopped(int parked, const char *why, int retry_restored)
{
    CHECK_EQ(parked, 1);
    CHECK_EQ(h_parked, 1);
    CHECK_EQ(h_stick_calls, 0);       /* no file was written */
    CHECK_EQ(h_quiesces, 1);          /* one more attempt to put everything back */
    CHECK_EQ(h_last_quiesce, retry_restored ? 0 : -1);
    CHECK(fb_lit_bytes() > 1000);     /* the fatal page is on the framebuffer */
    CHECK_EQ(g_in_scope, 0);
    CHECK_EQ(g_post, 0);
    CHECK_EQ(con_has("SP11X2 EBS"), 0);
    CHECK(!con_has("SP11X2 FATAL"));
    CHECK(!con_has(why));
    CHECK(h_con_last_seq < h_flush_last_seq); /* the page is the last output */
    CHECK_EQ(con_count("SP11X2 RUN"), con_count("SP11X2 RESULT") + 1);
}

void test_x_hook_cleanup_failures(void)
{
    int ret;
    int parked;

    section("hook: cleanup failures");

    /* The inner NV2 restore cannot put HCR_EL2 back, although both runs went well:
     * a verdict of "restore failed" is not enough, the image stops. The state could
     * not be restored on the retry either, so no firmware code is called again. */
    h_reset();
    hb.freeze_after[W_HCR_EL2] = 2; /* the control and the redirect take, the restore does not */
    parked = run_pre(&ret);
    expect_stopped(parked, "NV2/EL1 STATE RESTORE FAILED", 0);
    CHECK_EQ(g_res[T_NV2].fatal, 1);
    CHECK_EQ(g_have_rec, 0);
    CHECK_EQ(tests_sysstate_cleanup_failed(), 1);
    CHECK_EQU(hb.reg[W_VBAR_EL2], 0x1000); /* the scope itself was left cleanly */

    /* The same failure, but the retry puts HCR_EL2 back: the run still stops, and
     * the console line may now be printed (the page first). */
    h_reset();
    hb.ignore_write[W_HCR_EL2] = 3; /* the third write, the restore, is lost once */
    parked = run_pre(&ret);
    expect_stopped(parked, "NV2/EL1 STATE RESTORE FAILED", 1);
    CHECK_EQ(g_res[T_NV2].fatal, 1);
    CHECK_EQU(hb.reg[W_HCR_EL2], 0x1000 + 0x111); /* back as the firmware had it */

    /* The same failure when the NV2 operation hides its own result: the sticky
     * flag still stops the run. */
    h_reset();
    hb.freeze_after[W_HCR_EL2] = 2;
    h_restore_drops_result = 1;
    parked = run_pre(&ret);
    expect_stopped(parked, "NV2/EL1 STATE RESTORE FAILED", 0);
    CHECK_EQ(tests_sysstate_cleanup_failed(), 1);

    /* The roll-back of a failed scope entry fails too (the vector base reads back
     * wrongly and cannot be put back). No test body ran, nothing more runs. */
    h_reset();
    hb.lie_on_read = TESTS_WMASK(W_VBAR_EL2);
    parked = run_pre(&ret);
    expect_stopped(parked, "SCOPE ENTER ROLLBACK FAILED", 0);
    CHECK_EQ(h_idreads, 0);
    CHECK_EQ(con_count("SP11X2 RUN"), 1);
    CHECK_EQ(g_res[T_ID].verdict, TV_NOT_RUN);

    /* The scope's own restore fails after the first test: no later test starts. */
    h_reset();
    hb.freeze_after[W_VBAR_EL2] = 1;
    parked = run_pre(&ret);
    expect_stopped(parked, "VBAR/DAIF/ELR/SPSR RESTORE FAILED", 0);
    CHECK_EQ(con_count("SP11X2 RUN"), 1);
    CHECK_EQ(h_scope_enters, 1);

    /* The same, lost once: the retry restores it, so the console line is printed,
     * after the vector base is back. */
    h_reset();
    hb.ignore_write[W_VBAR_EL2] = 2; /* the leave's write; the scope's own was the first */
    parked = run_pre(&ret);
    expect_stopped(parked, "VBAR/DAIF/ELR/SPSR RESTORE FAILED", 1);
    CHECK_EQ(con_count("SP11X2 RUN"), 1);
    CHECK_EQU(hb.reg[W_VBAR_EL2], 0x1000);

    /* ELR_EL2 / SPSR_EL2 that do not go back are part of the same rule: an
     * exception rewrote them and the firmware's values cannot be restored. */
    h_reset();
    hb.stuck = TESTS_WMASK(W_SPSR_EL2);
    h_clobber_on_read = 1;
    parked = run_pre(&ret);
    expect_stopped(parked, "VBAR/DAIF/ELR/SPSR RESTORE FAILED", 0);
    CHECK_EQ(con_count("SP11X2 RUN"), 1);

    /* A clean failure is not a cleanup failure: the scope cannot be entered
     * (the vector base does not take), everything is verified back, and the run
     * carries on with every test marked not run. */
    h_reset();
    hb.stuck = TESTS_WMASK(W_VBAR_EL2);
    parked = run_pre(&ret);
    CHECK_EQ(parked, 0);
    CHECK_EQ(ret, 0);
    CHECK_EQ(h_parked, 0);
    CHECK_EQ(h_quiesces, 0);
    CHECK_EQ(tests_sysstate_cleanup_failed(), 0);
    CHECK_EQ(h_stick_calls, 1);
    CHECK_EQ(g_res[T_ID].verdict, TV_UNKNOWN);
    CHECK_EQ(h_idreads, 0);
    CHECK_EQ(fb_lit_bytes(), 0);
    CHECK(!con_has("FATAL"));

    /* An NV2 set-up that fails cleanly (the EL1 vector base does not take) is
     * rolled back, reported as a mechanics fault, and the run goes on. */
    h_reset();
    hb.stuck = TESTS_WMASK(W_VBAR_EL1X);
    parked = run_pre(&ret);
    CHECK_EQ(parked, 0);
    CHECK_EQ(g_res[T_NV2].fatal, 0);
    CHECK_EQ(g_res[T_NV2].verdict, TV_INCONCLUSIVE);
    CHECK_EQ(h_stick_calls, 1);
    CHECK_EQ(tests_sysstate_cleanup_failed(), 0);

    /* An NV2 set-up whose roll-back fails: the image stops. */
    h_reset();
    hb.lie_on_read = TESTS_WMASK(W_VBAR_EL1X);
    parked = run_pre(&ret);
    expect_stopped(parked, "NV2/EL1 STATE RESTORE FAILED", 0);
    CHECK_EQ(g_res[T_NV2].fatal, 1);

    /* A cleanup failure that surfaces after the last test's own checks (here while
     * its RESULT line is being printed) is still caught before anything is written
     * to the stick, and the image never returns to the caller that would go on to
     * ExitBootServices. */
    h_reset();
    h_con_hook = late_cleanup_failure;
    parked = run_pre(&ret);
    h_con_hook = NULL;
    CHECK_EQ(parked, 1);
    CHECK_EQ(h_parked, 1);
    CHECK_EQ(h_stick_calls, 0);
    CHECK_EQ(h_quiesces, 1);
    CHECK_EQ(tests_sysstate_cleanup_failed(), 1);
    CHECK_EQ(con_count("SP11X2 RESULT"), 4); /* every test had finished */
    CHECK_EQ(con_has("SP11X2 EBS"), 0);
    CHECK(fb_lit_bytes() > 1000);
}

/* ---- Console output is never produced inside the scope ---- */

void test_x_hook_console(void)
{
    section("hook: console deferral");
    h_reset();
    ensure_init(&h_ctx);
    /* Outside the scope a line goes straight out. */
    con_puts("A\n");
    CHECK_EQ(h_con_calls, 1);
    /* Inside it, nothing reaches the firmware until the scope has been left. */
    CHECK_EQ(scope_enter(), 0);
    CHECK_EQ(g_in_scope, 1);
    con_puts("B\n");
    marker("X", "K", "V", NULL, NULL, NULL, NULL);
    progress(NULL, "WAITING");
    CHECK_EQ(h_con_calls, 1);
    CHECK_EQ(h_con_calls_in_scope, 0);
    CHECK_EQ(scope_leave(), 0);
    CHECK_EQ(g_in_scope, 0);
    CHECK(con_has("B\n") || con_has("B\r\n"));
    CHECK(con_has("SP11X2 X K=V"));
    CHECK(con_has("SP11X2 PROGRESS WAITING"));
    CHECK_EQ(h_con_calls_in_scope, 0);
    /* The held text is printed once. */
    CHECK_EQ(con_count("SP11X2 PROGRESS WAITING"), 1);
    CHECK_EQ(g_deferred_len, 0);

    /* The hold is bounded; what does not fit is counted and announced. */
    h_reset();
    ensure_init(&h_ctx);
    CHECK_EQ(scope_enter(), 0);
    for (unsigned i = 0; i < 400; i++)
        con_puts("0123456789ABCDEF\n");
    CHECK(g_deferred_len < sizeof g_deferred);
    CHECK(g_deferred_dropped > 0);
    CHECK_EQ(h_con_calls, 0);
    CHECK_EQ(scope_leave(), 0);
    CHECK(con_has("0123456789ABCDEF"));
    CHECK(con_has("SP11X2 DEFERRED DROPPED"));
    CHECK_EQ(g_deferred_dropped, 0);
    CHECK_EQ(h_con_calls_in_scope, 0);

    /* After ExitBootServices there is no console at all. */
    h_reset();
    ensure_init(&h_ctx);
    g_post = 1;
    con_puts("Z\n");
    marker("Y", NULL, NULL, NULL, NULL, NULL, NULL);
    CHECK_EQ(h_con_calls, 0);

    /* Even a failed scope leave releases nothing into the firmware by itself:
     * the caller decides, and the halt path prints after quiescing. */
    h_reset();
    ensure_init(&h_ctx);
    hb.freeze_after[W_VBAR_EL2] = 1;
    CHECK_EQ(scope_enter(), 0);
    con_puts("held\n");
    CHECK(scope_leave() != 0);
    CHECK_EQ(g_in_scope, 0);
}

/* ---- The fatal page ---- */

void test_x_hook_fatal_page(void)
{
    int ret;

    section("hook: fatal page");

    /* Before ExitBootServices: the layout validated by tests_hook_pre_ebs is kept,
     * and a fatal exception puts a page on the framebuffer and writes nothing to
     * the console afterwards (a console line scrolls the page). */
    h_reset();
    CHECK_EQ(run_pre(&ret), 0);
    CHECK_EQ(g_post, 0);
    CHECK_EQ(g_have_layout, 1);
    CHECK_EQ(fb_lit_bytes(), 0);
    CHECK(h_fatal_fn == fatal_draw);
    h_con_calls = 0;
    h_flush_last_seq = 0;
    g_in_scope = 1; /* an exception taken inside a scope */
    fatal_draw("UNEXPECTED EXCEPTION", 0x96000010ull, 0xB000000ull, 0x7C21207Cull, 4);
    CHECK(fb_lit_bytes() > 1000);
    CHECK(h_flush_calls > 0);
    CHECK_EQ(h_con_calls, 0);
    CHECK(!con_has("SP11X2 FATAL"));
    g_in_scope = 0;

    /* Without a validated layout there is no page; the console line is all. */
    h_reset();
    ensure_init(&h_ctx);
    g_have_layout = 0;
    fatal_draw("UNEXPECTED EXCEPTION", 1, 2, 3, 5);
    CHECK_EQ(fb_lit_bytes(), 0);
    CHECK(con_has("SP11X2 FATAL UNEXPECTED EXCEPTION VECTOR=5"));
    CHECK_EQ(h_con_calls, 1);

    /* After ExitBootServices: the page, and no console. */
    h_reset();
    CHECK_EQ(run_pre(&ret), 0);
    g_post = 1;
    memset(h_fbmem, 0, sizeof h_fbmem);
    h_con_calls = 0;
    fatal_draw("UNEXPECTED EXCEPTION", 1, 2, 3, 4);
    CHECK(fb_lit_bytes() > 1000);
    CHECK_EQ(h_con_calls, 0);

    /* A cleanup failure after ExitBootServices: the page, no console, stop. */
    h_reset();
    CHECK_EQ(run_pre(&ret), 0);
    g_post = 1;
    memset(h_fbmem, 0, sizeof h_fbmem);
    h_con_calls = 0;
    h_stick_calls = 0;
    h_quiesces = 0;
    hb.freeze_after[W_VBAR_EL2] = hb.nwrites[W_VBAR_EL2] + 1;
    h_parked = 0;
    if (setjmp(h_park_env) == 0)
        run_test(T_DRAM, 2); /* must not return */
    CHECK_EQ(h_parked, 1);
    CHECK(fb_lit_bytes() > 1000);
    CHECK_EQ(h_con_calls, 0);
    CHECK_EQ(h_quiesces, 1);
    CHECK_EQ(h_stick_calls, 0);

    /* The cleanup page names the failure and the test, and every row fits the
     * renderer: nothing is silently truncated. */
    h_reset();
    ensure_init(&h_ctx);
    g_have_layout = 1;
    CHECK_EQ(tests_layout_choose(H_W, H_H, TESTS_REPORT_MAX_ROWS, LAYOUT_MAX_PAGES, &g_layout), 0);
    g_current_test = T_NV2;
    h_parked = 0;
    if (setjmp(h_park_env) == 0)
        cleanup_halt("SCOPE ENTER ROLLBACK FAILED");
    CHECK_EQ(h_parked, 1);
    CHECK(!con_has("SP11X2 FATAL"));
    CHECK(fb_lit_bytes() > 1000);

    /* With no page to draw the console line is the only report, and only when the
     * retry put the firmware's state back. */
    h_reset();
    ensure_init(&h_ctx);
    g_have_layout = 0;
    g_current_test = T_NV2;
    h_parked = 0;
    if (setjmp(h_park_env) == 0)
        cleanup_halt("SCOPE ENTER ROLLBACK FAILED");
    CHECK_EQ(h_parked, 1);
    CHECK_EQ(fb_lit_bytes(), 0);
    CHECK(con_has("SP11X2 FATAL CLEANUP FAILED SCOPE ENTER ROLLBACK FAILED"));
}

/* ---- Phase B runs whatever phase A's own EL2 check skipped ---- */

static int result_has_row(const tests_result *r, const char *needle)
{
    for (unsigned i = 0; i < r->nrows; i++)
        if (strstr(r->rows[i], needle) != NULL)
            return 1;
    return 0;
}

static int report_has_row(const char *needle)
{
    for (unsigned i = 0; i < g_rep.count; i++)
        if (strcmp(g_rep.row[i], needle) == 0)
            return 1;
    return 0;
}

/* A platform whose only path to EL2 is slbounce's ExitBootServices hook itself
 * (OBSERVED twice on a Surface Pro 11 X1E80100: preebs_currentel decoded to EL1
 * both times) takes tests_hook_pre_ebs's SKIP branch: order_a never runs there,
 * and before this fallback existed it never ran anywhere, so T01, T02, T03 and
 * T04 stayed "NOT-RUN" and K1 stayed "NOT RUN" forever, on every such platform.
 * These checks pin down tests_hook_post_ebs's fallback against that, and that a
 * platform which does reach EL2 before ExitBootServices (every other scenario
 * here, and QEMU) is unaffected: phase A's own run and stick write stay exactly
 * as they were, and phase B does not repeat them. */
void test_x_hook_phase_b_fallback(void)
{
    static const int order_a_ids[] = {T_ID, T_TIMER, T_ZVA, T_NV2};
    static const int order_b_ids[] = {T_DRAM, T_GIC, T_SMMU, T_PCIE, T_ADSP};
    int ret;

    section("hook: phase b fallback");

    /* ---- preebs_el_raw decodes to EL1: the exact value both real runs recorded ---- */
    h_reset();
    h_facts.preebs_el_raw = 4;
    CHECK_EQ(run_pre(&ret), 0);
    CHECK_EQ(ret, 0);
    CHECK_EQ(h_parked, 0);
    CHECK(con_has("SP11X2 SKIP REASON=NOT_EL2"));
    CHECK_EQ(h_stick_calls, 0); /* phase A wrote nothing: there was nothing to write */
    CHECK_EQ(h_run_seq_n, 0);   /* and ran nothing */
    for (unsigned i = 0; i < 4; i++)
        CHECK_EQ(g_res[order_a_ids[i]].phase, 0);

    run_post();
    CHECK_EQ(h_parked, 1);
    CHECK_EQ(h_resets, 1);
    CHECK_EQ(h_stick_calls, 0); /* still nothing: no phase-B test may reach the stick */
    CHECK_EQ(h_run_seq_n, 9);
    for (unsigned i = 0; i < 4; i++)
        CHECK_EQ(h_run_seq[i], order_a_ids[i]); /* least intrusive first, and before order_b */
    for (unsigned i = 0; i < 5; i++)
        CHECK_EQ(h_run_seq[4 + i], order_b_ids[i]); /* order_b itself unchanged */
    for (unsigned i = 0; i < 4; i++) {
        CHECK_EQ(g_res[order_a_ids[i]].phase, 2); /* ran in phase B this time */
        CHECK(g_res[order_a_ids[i]].verdict != TV_NOT_RUN); /* a real result, not the bug's NOT-RUN */
    }
    CHECK_EQ(g_res[T_NV2].verdict, TV_GO);
    /* tests_run_nv2 (t_misc.c) already appends tests_nv2rec_screen_rows()'s lines
     * to T02's own rows whenever it has a record, in either phase: no separate
     * wiring exists to lose, but the row text must actually be there. */
    CHECK(result_has_row(&g_res[T_NV2], "NV2 RECORD (RETYPE: SEE GUIDE):"));
    CHECK(result_has_row(&g_res[T_NV2], "NV2R"));
    CHECK(report_has_row("K1 NV2: GO")); /* K1 follows whichever phase actually ran T02 */

    /* ---- have_preebs_el == 0 (CurrentEL could not even be read before EBS)
     * takes the same SKIP branch, so the same fallback must still apply ---- */
    h_reset();
    h_facts.have_preebs_el = 0;
    CHECK_EQ(run_pre(&ret), 0);
    CHECK_EQ(h_stick_calls, 0);
    run_post();
    CHECK_EQ(h_parked, 1);
    for (unsigned i = 0; i < 4; i++)
        CHECK_EQ(g_res[order_a_ids[i]].phase, 2);
    CHECK_EQ(g_res[T_NV2].verdict, TV_GO);
    CHECK(report_has_row("K1 NV2: GO"));

    /* ---- preebs_el_raw decodes to EL2 (h_reset's own default, and every other
     * scenario in this file): unaffected by the fallback existing ---- */
    h_reset();
    CHECK_EQ(run_pre(&ret), 0);
    CHECK_EQ(h_stick_calls, 1); /* persisted once, in phase A, exactly as before */
    CHECK_EQ(h_run_seq_n, 4);
    for (unsigned i = 0; i < 4; i++) {
        CHECK_EQ(h_run_seq[i], order_a_ids[i]);
        CHECK_EQ(g_res[order_a_ids[i]].phase, 1); /* phase A, not phase B */
    }

    run_post();
    CHECK_EQ(h_parked, 1);
    CHECK_EQ(h_stick_calls, 1); /* still 1: phase B attempted no second write */
    CHECK_EQ(h_run_seq_n, 9);   /* 4 (phase A) + 5 (phase B): no repeat of order_a */
    for (unsigned i = 0; i < 4; i++)
        CHECK_EQ(g_res[order_a_ids[i]].phase, 1); /* still phase A's result, not overwritten */
    for (unsigned i = 0; i < 5; i++) {
        CHECK_EQ(h_run_seq[4 + i], order_b_ids[i]);
        CHECK_EQ(g_res[order_b_ids[i]].phase, 2);
    }
}
