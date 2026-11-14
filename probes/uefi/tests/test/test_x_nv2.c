/* SPDX-License-Identifier: MIT */
/* Host tests of the NV2 functional test: the exit classifiers, every branch of
 * the verdict function, the run orchestration against a model core, and the
 * record the run produces. The brief's rule is tested here in its strongest
 * form: no combination of inputs other than the one real success produces GO,
 * and a pass pair never reaches the record from any other verdict.
 */
#include "xtests.h"

#include "nv2rec.h"
#include "suite.h"

static tests_el1_exit mk_exit(uint32_t kind, uint32_t imm, uint64_t esr)
{
    tests_el1_exit e;

    memset(&e, 0, sizeof e);
    e.kind = kind;
    e.imm = imm;
    e.esr = esr;
    return e;
}

static uint64_t esr_mrs(unsigned op0, unsigned op1, unsigned crn, unsigned crm, unsigned op2,
                        unsigned rt, int read)
{
    return (0x18ull << 26) | (1ull << 25) | ((uint64_t)op0 << 20) | ((uint64_t)op2 << 17) |
           ((uint64_t)op1 << 14) | ((uint64_t)crn << 10) | ((uint64_t)rt << 5) |
           ((uint64_t)crm << 1) | (read ? 1u : 0u);
}

static void poison_page(uint64_t *page)
{
    for (unsigned i = 0; i < TESTS_NV2_PAGE_WORDS; i++)
        page[i] = TESTS_NV2_POISON;
}

void test_x_nv2_classify(void)
{
    tests_el1_exit e;
    uint64_t page[TESTS_NV2_PAGE_WORDS];
    unsigned landed = 0;
    uint64_t rb = 0;

    section("nv2 classifiers");
    /* The control: only the exact trap of an MRS of TPIDR_EL2 into x1 counts. */
    e = mk_exit(EXIT_SYSREG, 0, esr_mrs(3, 4, 13, 0, 2, 1, 1));
    CHECK_EQ(tests_nv2_classify_control(&e), CTL_TRAPPED_OK);
    e = mk_exit(EXIT_SYSREG, 0, esr_mrs(3, 4, 13, 0, 2, 2, 1));
    CHECK_EQ(tests_nv2_classify_control(&e), CTL_TRAPPED_WRONG_ISS); /* wrong destination */
    e = mk_exit(EXIT_SYSREG, 0, esr_mrs(3, 4, 13, 0, 2, 1, 0));
    CHECK_EQ(tests_nv2_classify_control(&e), CTL_TRAPPED_WRONG_ISS); /* a write */
    e = mk_exit(EXIT_SYSREG, 0, esr_mrs(3, 4, 12, 0, 0, 1, 1));
    CHECK_EQ(tests_nv2_classify_control(&e), CTL_TRAPPED_WRONG_ISS); /* VBAR_EL2 */
    e = mk_exit(EXIT_SYSREG, 0, esr_mrs(3, 0, 13, 0, 2, 1, 1));
    CHECK_EQ(tests_nv2_classify_control(&e), CTL_TRAPPED_WRONG_ISS); /* TPIDR_EL1 */
    e = mk_exit(EXIT_HVC, TESTS_HVC_DONE_CONTROL, 0x16ull << 26);
    CHECK_EQ(tests_nv2_classify_control(&e), CTL_NOT_TRAPPED);
    for (unsigned v = 0; v < 16; v++) {
        e = mk_exit(EXIT_HVC, TESTS_HVC_VECTOR_BASE + v, 0x16ull << 26);
        CHECK_EQ(tests_nv2_classify_control(&e), CTL_UNDEF_EL1);
    }
    e = mk_exit(EXIT_HVC, TESTS_HVC_VECTOR_BASE + 16, 0);
    CHECK_EQ(tests_nv2_classify_control(&e), CTL_OTHER);
    e = mk_exit(EXIT_HVC, TESTS_HVC_DONE_REDIRECT, 0);
    CHECK_EQ(tests_nv2_classify_control(&e), CTL_OTHER);
    e = mk_exit(EXIT_DABT, 0, 0);
    CHECK_EQ(tests_nv2_classify_control(&e), CTL_ABORT);
    e = mk_exit(EXIT_ASYNC, 0, 0);
    CHECK_EQ(tests_nv2_classify_control(&e), CTL_OTHER);
    e = mk_exit(EXIT_OTHER, 0, 0);
    CHECK_EQ(tests_nv2_classify_control(&e), CTL_OTHER);
    e = mk_exit(EXIT_NONE, 0, 0);
    CHECK_EQ(tests_nv2_classify_control(&e), CTL_OTHER);

    /* The redirect. */
    e = mk_exit(EXIT_HVC, TESTS_HVC_DONE_REDIRECT, 0);
    e.x[0] = TESTS_NV2_PATTERN;
    e.x[1] = TESTS_NV2_PATTERN;
    poison_page(page);
    page[TESTS_NV2_TARGET_OFFSET / 8] = TESTS_NV2_PATTERN;
    CHECK_EQ(tests_nv2_classify_redirect(&e, page, 0, &landed, &rb), RED_LANDED_OK);
    CHECK_EQU(rb, TESTS_NV2_PATTERN);
    /* The access ran but the read-back differs: the value is not what was stored. */
    e.x[1] = TESTS_NV2_PATTERN ^ 1;
    CHECK_EQ(tests_nv2_classify_redirect(&e, page, 0, &landed, &rb), RED_VALUE_MISMATCH);
    e.x[1] = TESTS_NV2_PATTERN;
    e.x[0] = 0;
    CHECK_EQ(tests_nv2_classify_redirect(&e, page, 0, &landed, &rb), RED_VALUE_MISMATCH);
    e.x[0] = TESTS_NV2_PATTERN;
    /* Landed at another slot: a different register's offset. */
    poison_page(page);
    page[0x78 / 8] = TESTS_NV2_PATTERN;
    CHECK_EQ(tests_nv2_classify_redirect(&e, page, 0, &landed, &rb), RED_LANDED_ELSEWHERE);
    CHECK_EQ(landed, 0x78);
    /* Pattern at the right slot but another word changed too. */
    poison_page(page);
    page[TESTS_NV2_TARGET_OFFSET / 8] = TESTS_NV2_PATTERN;
    page[7] = 1;
    CHECK_EQ(tests_nv2_classify_redirect(&e, page, 0, &landed, &rb), RED_VALUE_MISMATCH);
    /* The right slot holds something that is not the pattern. */
    poison_page(page);
    page[TESTS_NV2_TARGET_OFFSET / 8] = 0x1234;
    CHECK_EQ(tests_nv2_classify_redirect(&e, page, 0, &landed, &rb), RED_VALUE_MISMATCH);
    /* Nothing changed. */
    poison_page(page);
    CHECK_EQ(tests_nv2_classify_redirect(&e, page, 0, &landed, &rb), RED_NOT_LANDED);
    /* The real register changed, even though the page looks right. */
    poison_page(page);
    page[TESTS_NV2_TARGET_OFFSET / 8] = TESTS_NV2_PATTERN;
    CHECK_EQ(tests_nv2_classify_redirect(&e, page, 1, &landed, &rb), RED_REAL_REG_CHANGED);
    /* Other ways the run can end. */
    e = mk_exit(EXIT_SYSREG, 0, esr_mrs(3, 4, 13, 0, 2, 1, 1));
    CHECK_EQ(tests_nv2_classify_redirect(&e, page, 0, &landed, &rb), RED_TRAPPED);
    e = mk_exit(EXIT_DABT, 0, 0);
    CHECK_EQ(tests_nv2_classify_redirect(&e, page, 0, &landed, &rb), RED_VNCR_ABORT);
    e = mk_exit(EXIT_HVC, TESTS_HVC_VECTOR_BASE + 4, 0);
    CHECK_EQ(tests_nv2_classify_redirect(&e, page, 0, &landed, &rb), RED_UNDEF_EL1);
    e = mk_exit(EXIT_HVC, TESTS_HVC_DONE_CONTROL, 0);
    CHECK_EQ(tests_nv2_classify_redirect(&e, page, 0, &landed, &rb), RED_OTHER);
    e = mk_exit(EXIT_ASYNC, 0, 0);
    CHECK_EQ(tests_nv2_classify_redirect(&e, page, 0, &landed, &rb), RED_OTHER);
    e = mk_exit(EXIT_OTHER, 0, 0);
    CHECK_EQ(tests_nv2_classify_redirect(&e, page, 0, &landed, &rb), RED_OTHER);
    CHECK_EQ(TESTS_NV2_TARGET_OFFSET, 0x90); /* VNCR slot of TPIDR_EL2 */
}

/* ---- The verdict function ---- */

void test_x_nv2_decide(void)
{
    tests_idset s;
    tests_nv2_obs o;
    unsigned go = 0, total = 0;
    static const unsigned fracs[] = {0, 1, 2, 15};
    static const unsigned nvs[] = {0, 1, 2, 3, 15};

    section("nv2 decision");
    /* The whole input space of the decision, against an independent statement
     * of the one condition that may give GO. */
    for (unsigned a = 0; a < sizeof nvs / sizeof nvs[0]; a++)
    for (unsigned b = 0; b < sizeof fracs / sizeof fracs[0]; b++)
    for (int plausible = 0; plausible < 2; plausible++)
    for (int prepared = 0; prepared < 2; prepared++)
    for (int restore_ok = 0; restore_ok < 2; restore_ok++)
    for (int mech = 0; mech < 2; mech++)
    for (int ctl = CTL_NOT_RUN; ctl <= CTL_OTHER; ctl++)
    for (int red = RED_NOT_RUN; red <= RED_OTHER; red++) {
        tests_nv2_verdict v;
        int expect_go;

        x_good_ids(&s);
        x_set_nv(&s, nvs[a], fracs[b]);
        if (!plausible)
            s.v[TI_MIDR] = 0;
        memset(&o, 0, sizeof o);
        o.attempted = prepared;
        o.prepared = prepared;
        o.restore_ok = restore_ok;
        o.mech_fault = mech;
        o.ctl = (enum tests_ctl)ctl;
        o.red = (enum tests_red)red;
        v = tests_nv2_decide(&s, &o);
        expect_go = plausible && nvs[a] == 2 && fracs[b] == 0 && prepared && restore_ok && !mech &&
                    ctl == CTL_TRAPPED_OK && red == RED_LANDED_OK;
        total++;
        if ((v.verdict == TV_GO) != expect_go) {
            printf("  decide: nv=%u frac=%u plausible=%d prepared=%d restore=%d mech=%d ctl=%d red=%d -> %s\n",
                   nvs[a], fracs[b], plausible, prepared, restore_ok, mech, ctl, red,
                   tests_verdict_name(v.verdict));
        }
        CHECK_EQ(v.verdict == TV_GO, expect_go);
        CHECK(v.verdict == TV_GO || v.verdict == TV_NO_GO || v.verdict == TV_INCONCLUSIVE);
        if (v.verdict == TV_GO) {
            go++;
            CHECK_EQ(v.reason, NR_GO);
            CHECK_EQ(v.label, TL_OBSERVED);
        } else {
            CHECK(v.reason != NR_GO);
        }
        /* A run whose state could not be restored or whose machinery faulted
         * is never a statement about NV2, in either direction. */
        if (plausible && prepared && !restore_ok)
            CHECK_EQ(v.verdict, TV_INCONCLUSIVE);
        if (plausible && mech)
            CHECK_EQ(v.verdict, TV_INCONCLUSIVE);
        /* Verdicts that rest on the ID bits alone are labelled INFERRED. */
        if (v.verdict == TV_NO_GO && (v.reason == NR_NV_NONE || v.reason == NR_NV_ONLY))
            CHECK_EQ(v.label, TL_INFERRED);
    }
    CHECK_EQ(go, 1);
    CHECK(total > 4000);

    /* Named cases, so that a failure says which statement broke. */
    x_good_ids(&s);
    memset(&o, 0, sizeof o);
    o.attempted = o.prepared = o.restore_ok = 1;
    o.ctl = CTL_TRAPPED_OK;
    o.red = RED_LANDED_OK;
    CHECK_EQ(tests_nv2_decide(&s, &o).verdict, TV_GO);
    o.ctl = CTL_NOT_TRAPPED; /* the control did not trap: the negative control failed */
    CHECK_EQ(tests_nv2_decide(&s, &o).verdict, TV_INCONCLUSIVE);
    CHECK_EQ(tests_nv2_decide(&s, &o).reason, NR_CONTROL_FAILED);
    o.ctl = CTL_NOT_RUN;
    CHECK_EQ(tests_nv2_decide(&s, &o).reason, NR_NOT_RUN);
    o.ctl = CTL_TRAPPED_OK;
    o.red = RED_NOT_LANDED;
    CHECK_EQ(tests_nv2_decide(&s, &o).verdict, TV_NO_GO);
    CHECK_EQ(tests_nv2_decide(&s, &o).reason, NR_REDIRECT_FAILED);
    CHECK_EQ(tests_nv2_decide(&s, &o).label, TL_OBSERVED);
    o.red = RED_VNCR_ABORT;
    CHECK_EQ(tests_nv2_decide(&s, &o).verdict, TV_INCONCLUSIVE);
    o.red = RED_LANDED_OK;
    x_set_nv(&s, 2, 1); /* NV2 without NV: the monitor needs both */
    CHECK_EQ(tests_nv2_decide(&s, &o).verdict, TV_INCONCLUSIVE);
    CHECK_EQ(tests_nv2_decide(&s, &o).reason, NR_NV2_ONLY);
    x_set_nv(&s, 1, 0); /* FEAT_NV only, yet the access landed: a contradiction */
    CHECK_EQ(tests_nv2_decide(&s, &o).reason, NR_CONTRADICTION);
    x_set_nv(&s, 0, 0);
    CHECK_EQ(tests_nv2_decide(&s, &o).reason, NR_CONTRADICTION);
    o.red = RED_NOT_RUN;
    o.ctl = CTL_NOT_RUN;
    o.attempted = o.prepared = o.restore_ok = 0;
    CHECK_EQ(tests_nv2_decide(&s, &o).verdict, TV_NO_GO); /* nothing advertised */
    CHECK_EQ(tests_nv2_decide(&s, &o).reason, NR_NV_NONE);
    x_set_nv(&s, 1, 0);
    CHECK_EQ(tests_nv2_decide(&s, &o).reason, NR_NV_ONLY);
    CHECK_STR(tests_nv2_reason_name(NR_GO), "GO");
    CHECK_STR(tests_nv2_reason_name(NR_COUNT), "?");
    for (int r = 0; r < NR_COUNT; r++)
        CHECK(strlen(tests_nv2_reason_name(r)) <= 30);
}

/* ---- A model core behind the operations ---- */

typedef struct {
    /* behaviour */
    int redirect_works;
    int control_traps;
    int undef_at_el1;
    int redirect_offset;     /* byte offset the model stores to */
    int redirect_aborts;
    int real_reg_changes;
    int stale_pattern_in_page;
    int prepare_fails, hcr_fails_at, run_fails_at, restore_fails, tpidr_fails;
    int hcr_read_fails;
    /* recorded */
    int prepared, restored, restores;
    int hcr_nv, hcr_nv2, hcr_map;
    int hcr_calls, run_calls, tpidr_calls, syncs, order_violations;
    /* HCR_EL2 as the model holds it: what the firmware left until the first set_hcr, the stub's
     * value after it, the firmware's again once restored. hcr_calls_at_read says how many set_hcr
     * calls had been made when it was read. */
    uint64_t hcr, entry_hcr;
    unsigned hcr_reads;
    int hcr_calls_at_read;
    int log[16];
    unsigned nlog;
    uint64_t page[TESTS_NV2_PAGE_WORDS] __attribute__((aligned(4096)));
    uint64_t tpidr;
} mockcore;

/* An entry value with HCR_EL2.TGE set and E2H clear, and a stub value that differs from it. */
#define MC_ENTRY_HCR 0x0000000088000010ull

static void mc_log(mockcore *m, int what)
{
    if (m->nlog < 16)
        m->log[m->nlog++] = what;
}

static int mc_prepare(void *ctx)
{
    mockcore *m = ctx;

    mc_log(m, 'P');
    /* 1: a clean failure (everything put back); 2: the roll-back failed too. */
    if (m->prepare_fails)
        return m->prepare_fails == 2 ? TESTS_CLEANUP_FAILED : TESTS_ERR;
    m->prepared = 1;
    return 0;
}

static int mc_set_hcr(void *ctx, int nv, int nv2, int map)
{
    mockcore *m = ctx;

    m->hcr_calls++;
    if (!m->prepared || m->restored)
        m->order_violations++;
    mc_log(m, nv2 ? 'H' : 'h');
    if (m->hcr_fails_at && m->hcr_calls == m->hcr_fails_at)
        return -1;
    m->hcr_nv = nv;
    m->hcr_nv2 = nv2;
    m->hcr_map = map;
    m->hcr = tests_hcr_for(m->entry_hcr, nv, nv2);
    return 0;
}

static int mc_run(void *ctx, int program, tests_el1_exit *out)
{
    mockcore *m = ctx;

    m->run_calls++;
    if (!m->prepared || m->restored)
        m->order_violations++;
    mc_log(m, program == EL1_PROG_CONTROL ? 'c' : 'r');
    if (m->run_fails_at && m->run_calls == m->run_fails_at)
        return -1;
    memset(out, 0, sizeof *out);
    if (m->undef_at_el1) {
        out->kind = EXIT_HVC;
        out->imm = TESTS_HVC_VECTOR_BASE + 4;
        return 0;
    }
    if (program == EL1_PROG_CONTROL) {
        if (m->control_traps && m->hcr_nv && !m->hcr_nv2) {
            out->kind = EXIT_SYSREG;
            out->esr = esr_mrs(3, 4, 13, 0, 2, 1, 1);
        } else {
            out->kind = EXIT_HVC;
            out->imm = TESTS_HVC_DONE_CONTROL;
            out->x[1] = m->tpidr;
        }
        return 0;
    }
    /* The redirect program: only an NV2 run with the page mapped redirects. */
    if (m->redirect_aborts) {
        out->kind = EXIT_DABT;
        return 0;
    }
    out->kind = EXIT_HVC;
    out->imm = TESTS_HVC_DONE_REDIRECT;
    out->x[0] = TESTS_NV2_PATTERN;
    out->x[1] = TESTS_NV2_PATTERN;
    if (m->redirect_works && m->hcr_nv && m->hcr_nv2 && m->hcr_map)
        m->page[m->redirect_offset / 8] = TESTS_NV2_PATTERN;
    else if (m->real_reg_changes)
        m->tpidr = TESTS_NV2_PATTERN;
    return 0;
}

static int mc_tpidr(void *ctx, uint64_t *v)
{
    mockcore *m = ctx;

    m->tpidr_calls++;
    if (m->tpidr_fails)
        return -1;
    *v = m->tpidr;
    return 0;
}

static uint64_t *mc_page(void *ctx)
{
    mockcore *m = ctx;

    if (m->stale_pattern_in_page)
        m->page[TESTS_NV2_TARGET_OFFSET / 8] = TESTS_NV2_PATTERN;
    return m->page;
}

static void mc_sync(void *ctx)
{
    ((mockcore *)ctx)->syncs++;
}

static int mc_restore(void *ctx)
{
    mockcore *m = ctx;

    mc_log(m, 'R');
    m->restores++;
    m->restored = 1;
    m->hcr = m->entry_hcr;
    return m->restore_fails ? -1 : 0;
}

static int mc_real_hcr(void *ctx, uint64_t *v)
{
    mockcore *m = ctx;

    m->hcr_reads++;
    m->hcr_calls_at_read = m->hcr_calls;
    if (m->hcr_read_fails)
        return -1;
    *v = m->hcr;
    return 0;
}

static void mc_init(mockcore *m)
{
    memset(m, 0, sizeof *m);
    m->redirect_works = 1;
    m->control_traps = 1;
    m->redirect_offset = TESTS_NV2_TARGET_OFFSET;
    m->tpidr = 0x1111;
    m->entry_hcr = m->hcr = MC_ENTRY_HCR;
}

static tests_nv2_ops mc_ops(mockcore *m)
{
    tests_nv2_ops ops = {m,        mc_prepare, mc_set_hcr, mc_run,    mc_tpidr,
                         mc_page, mc_sync,    mc_restore, mc_real_hcr};

    return ops;
}

static tests_nv2_obs sound_obs(enum tests_ctl ctl, enum tests_red red)
{
    tests_nv2_obs o;

    memset(&o, 0, sizeof o);
    o.attempted = o.prepared = o.restore_ok = 1;
    o.ctl = ctl;
    o.red = red;
    return o;
}

void test_x_nv2_run(void)
{
    mockcore m;
    tests_nv2_ops ops;
    tests_idset s;
    tests_nv2_obs o;
    tests_nv2_verdict v;
    tests_result r;

    section("nv2 run");
    /* A core that does everything right. */
    mc_init(&m);
    ops = mc_ops(&m);
    x_good_ids(&s);
    tests_nv2_run(&ops, &s, &o);
    v = tests_nv2_decide(&s, &o);
    CHECK_EQ(v.verdict, TV_GO);
    CHECK(o.attempted && o.prepared && o.restore_ok && !o.mech_fault);
    CHECK_EQ(o.ctl, CTL_TRAPPED_OK);
    CHECK_EQ(o.red, RED_LANDED_OK);
    CHECK_EQ(m.restores, 1);
    CHECK_EQ(m.order_violations, 0);
    CHECK_EQ(m.hcr_calls, 2);
    CHECK_EQ(m.run_calls, 2);
    CHECK_EQ(m.tpidr_calls, 2); /* before and after the redirect */
    CHECK(m.syncs >= 2);
    CHECK_EQ(m.nlog, 6);
    {
        /* prepare, control with NV only, redirect with NV and NV2, restore last. */
        static const int want[6] = {'P', 'h', 'c', 'H', 'r', 'R'};

        for (unsigned i = 0; i < 6; i++)
            CHECK_EQ(m.log[i], want[i]);
    }
    CHECK_EQ(m.hcr_nv2, 1);
    CHECK_EQ(m.hcr_map, 1);
    /* The fill-in rows of a good run fit and are not flagged. */
    tests_init_result(&r, T_NV2);
    tests_nv2_fill_rows(&s, &o, &v, &r);
    tests_result_seal(&r);
    CHECK_EQ(r.overflow, 0);
    CHECK(r.nrows > 3);

    /* A stale pattern in the page from an earlier run must not pass for a
     * landing: the page is poisoned first, so a core that does not redirect is
     * seen as such. */
    mc_init(&m);
    m.redirect_works = 0;
    m.stale_pattern_in_page = 1;
    ops = mc_ops(&m);
    tests_nv2_run(&ops, &s, &o);
    CHECK_EQ(o.red, RED_NOT_LANDED);
    CHECK_EQ(tests_nv2_decide(&s, &o).verdict, TV_NO_GO);

    /* NV2 absent although the ID bits claim it. */
    mc_init(&m);
    m.redirect_works = 0;
    ops = mc_ops(&m);
    tests_nv2_run(&ops, &s, &o);
    CHECK_EQ(tests_nv2_decide(&s, &o).verdict, TV_NO_GO);
    CHECK_EQ(tests_nv2_decide(&s, &o).reason, NR_REDIRECT_FAILED);
    CHECK_EQ(m.restores, 1);

    /* The redirect lands, but the NV2-off control does not trap. */
    mc_init(&m);
    m.control_traps = 0;
    ops = mc_ops(&m);
    tests_nv2_run(&ops, &s, &o);
    CHECK_EQ(o.ctl, CTL_NOT_TRAPPED);
    CHECK_EQ(o.red, RED_LANDED_OK);
    CHECK(tests_nv2_decide(&s, &o).verdict != TV_GO);
    CHECK_EQ(tests_nv2_decide(&s, &o).reason, NR_CONTROL_FAILED);

    /* The redirect lands at another register's slot. */
    mc_init(&m);
    m.redirect_offset = 0x78;
    ops = mc_ops(&m);
    tests_nv2_run(&ops, &s, &o);
    CHECK_EQ(o.red, RED_LANDED_ELSEWHERE);
    CHECK_EQ(tests_nv2_decide(&s, &o).verdict, TV_NO_GO);

    /* The real register changed: the access was not redirected at all. */
    mc_init(&m);
    m.redirect_works = 0;
    m.real_reg_changes = 1;
    ops = mc_ops(&m);
    tests_nv2_run(&ops, &s, &o);
    CHECK_EQ(o.red, RED_REAL_REG_CHANGED);
    CHECK(tests_nv2_decide(&s, &o).verdict != TV_GO);

    /* The EL1 access is UNDEFINED. */
    mc_init(&m);
    m.undef_at_el1 = 1;
    ops = mc_ops(&m);
    tests_nv2_run(&ops, &s, &o);
    CHECK_EQ(o.ctl, CTL_UNDEF_EL1);
    CHECK(tests_nv2_decide(&s, &o).verdict != TV_GO);

    /* A data abort of the VNCR access: inconclusive, never a verdict on NV2. */
    mc_init(&m);
    m.redirect_aborts = 1;
    ops = mc_ops(&m);
    tests_nv2_run(&ops, &s, &o);
    CHECK_EQ(o.red, RED_VNCR_ABORT);
    CHECK_EQ(tests_nv2_decide(&s, &o).verdict, TV_INCONCLUSIVE);
    CHECK_EQ(m.restores, 1);

    /* Every failure of the machinery still restores, and is never a GO. */
    mc_init(&m);
    m.prepare_fails = 1;
    ops = mc_ops(&m);
    tests_nv2_run(&ops, &s, &o);
    CHECK(o.mech_fault && !o.prepared);
    CHECK(!o.cleanup_failed); /* a clean failure: prepare() put everything back */
    CHECK_EQ(m.restores, 0); /* prepare() itself leaves nothing changed */
    CHECK_EQ(m.hcr_calls, 0);
    CHECK_EQ(m.run_calls, 0);
    CHECK(tests_nv2_decide(&s, &o).verdict != TV_GO);
    CHECK_EQ(tests_nv2_decide(&s, &o).reason, NR_MECH_FAULT);
    /* A prepare() whose own roll-back failed is a different thing: the state is
     * not what the firmware left, no statement about NV2 is possible, and the
     * observation says so. */
    mc_init(&m);
    m.prepare_fails = 2;
    ops = mc_ops(&m);
    tests_nv2_run(&ops, &s, &o);
    CHECK(o.mech_fault && !o.prepared && o.cleanup_failed);
    CHECK_EQ(m.hcr_calls, 0);
    CHECK_EQ(m.run_calls, 0);
    CHECK_EQ(tests_nv2_decide(&s, &o).verdict, TV_INCONCLUSIVE);
    CHECK_EQ(tests_nv2_decide(&s, &o).reason, NR_RESTORE_FAILED);
    for (int at = 1; at <= 2; at++) {
        mc_init(&m);
        m.hcr_fails_at = at;
        ops = mc_ops(&m);
        tests_nv2_run(&ops, &s, &o);
        CHECK(o.mech_fault);
        CHECK_EQ(m.restores, 1);
        CHECK(tests_nv2_decide(&s, &o).verdict != TV_GO);
        mc_init(&m);
        m.run_fails_at = at;
        ops = mc_ops(&m);
        tests_nv2_run(&ops, &s, &o);
        CHECK(o.mech_fault);
        CHECK_EQ(m.restores, 1);
        CHECK(tests_nv2_decide(&s, &o).verdict != TV_GO);
    }
    mc_init(&m);
    m.tpidr_fails = 1;
    ops = mc_ops(&m);
    tests_nv2_run(&ops, &s, &o);
    CHECK(o.mech_fault);
    CHECK_EQ(m.restores, 1);
    CHECK(tests_nv2_decide(&s, &o).verdict != TV_GO);
    /* Restore that does not verify: even a perfect run is then untrusted. */
    mc_init(&m);
    m.restore_fails = 1;
    ops = mc_ops(&m);
    tests_nv2_run(&ops, &s, &o);
    CHECK_EQ(o.red, RED_LANDED_OK);
    CHECK_EQ(o.ctl, CTL_TRAPPED_OK);
    CHECK(!o.restore_ok);
    CHECK(o.cleanup_failed);
    CHECK_EQ(tests_nv2_decide(&s, &o).verdict, TV_INCONCLUSIVE);
    CHECK_EQ(tests_nv2_decide(&s, &o).reason, NR_RESTORE_FAILED);
    /* The flag alone is enough: a run that says it could not clean up is never
     * trusted, whatever else the observation holds. */
    o = sound_obs(CTL_TRAPPED_OK, RED_LANDED_OK);
    CHECK_EQ(tests_nv2_decide(&s, &o).verdict, TV_GO);
    o.cleanup_failed = 1;
    CHECK_EQ(tests_nv2_decide(&s, &o).verdict, TV_INCONCLUSIVE);
    CHECK_EQ(tests_nv2_decide(&s, &o).reason, NR_RESTORE_FAILED);
    {
        tests_nv2_record rec;
        tests_nv2_verdict dv = tests_nv2_decide(&s, &o);

        /* ...and it leaves no pass in the record. */
        CHECK_EQ(tests_nv2_make_record(&s, &o, &dv, 0, NV2R_SOC_X1E80100, 1, &rec), 0);
        CHECK_EQ(rec.vncr_redirect, NV2R_NOT_RUN);
        CHECK_EQ(rec.negative_control_trapped, NV2R_NOT_RUN);
    }

    /* FEAT_NV without NV2: the control only; HCR_EL2.NV2 is never programmed. */
    mc_init(&m);
    ops = mc_ops(&m);
    x_set_nv(&s, 1, 0);
    tests_nv2_run(&ops, &s, &o);
    CHECK_EQ(o.ctl, CTL_TRAPPED_OK);
    CHECK_EQ(o.red, RED_NOT_RUN);
    CHECK_EQ(m.hcr_calls, 1);
    CHECK_EQ(m.hcr_nv2, 0);
    CHECK_EQ(m.restores, 1);
    CHECK_EQ(tests_nv2_decide(&s, &o).verdict, TV_NO_GO);
    /* No NV, a reserved pair, unreadable or all-ones IDs: not one operation is
     * made, so a RES0 bit is never written. */
    for (int variant = 0; variant < 5; variant++) {
        mc_init(&m);
        ops = mc_ops(&m);
        x_good_ids(&s);
        if (variant == 0)
            x_set_nv(&s, 0, 0);
        else if (variant == 1)
            x_set_nv(&s, 2, 1);
        else if (variant == 2)
            x_set_nv(&s, 3, 0);
        else if (variant == 3)
            s.have &= ~(1u << TI_MMFR2);
        else
            s.v[TI_MMFR4] = ~0ull;
        tests_nv2_run(&ops, &s, &o);
        CHECK(!o.attempted);
        CHECK_EQ(m.nlog, 0);
        CHECK(tests_nv2_decide(&s, &o).verdict != TV_GO);
    }

    /* The T02 entry point: a run that could not put everything back is flagged
     * fatal for the caller and leaves no record; a clean failure is not fatal. */
    {
        tests_ctx c;
        tests_nv2_record rec;
        int have = 1;
        int mention = 0;

        memset(&c, 0, sizeof c);
        x_good_ids(&c.ids);
        mc_init(&m);
        ops = mc_ops(&m);
        tests_run_nv2(&c, &ops, NV2R_SOC_X1E80100, 0x1234, &r, &rec, &have);
        CHECK_EQ(r.fatal, 0);
        CHECK_EQ(have, 1);
        CHECK_EQ(r.verdict, TV_GO);
        CHECK_EQ(r.overflow, 0);

        mc_init(&m);
        m.restore_fails = 1;
        ops = mc_ops(&m);
        tests_run_nv2(&c, &ops, NV2R_SOC_X1E80100, 0x1234, &r, &rec, &have);
        CHECK_EQ(r.fatal, 1);
        CHECK_EQ(have, 0);
        CHECK_EQ(r.verdict, TV_INCONCLUSIVE);
        CHECK_EQ(r.overflow, 0);
        for (unsigned i = 0; i < r.nrows; i++)
            mention |= strstr(r.rows[i], "NOT RESTORED") != NULL;
        CHECK(mention);

        mc_init(&m);
        m.prepare_fails = 2;
        ops = mc_ops(&m);
        tests_run_nv2(&c, &ops, NV2R_SOC_X1E80100, 0x1234, &r, &rec, &have);
        CHECK_EQ(r.fatal, 1);
        CHECK_EQ(have, 0);
        CHECK_EQ(r.verdict, TV_INCONCLUSIVE);

        mc_init(&m);
        m.prepare_fails = 1;
        ops = mc_ops(&m);
        tests_run_nv2(&c, &ops, NV2R_SOC_X1E80100, 0x1234, &r, &rec, &have);
        CHECK_EQ(r.fatal, 0);
        CHECK_EQ(r.verdict, TV_INCONCLUSIVE);
    }
}

/* ---- The REGIME row ---- */

static const char *last_row(const tests_result *r)
{
    return r->nrows != 0 ? r->rows[r->nrows - 1] : "";
}

/* The REGIME row as tests_nv2_fill_regime writes it for an observation. */
static void regime_row(uint64_t hcr, int have, char out[PROBE_LINE_PAYLOAD_MAX + 1])
{
    tests_nv2_obs o;
    tests_result r;

    memset(&o, 0, sizeof o);
    o.have_entry_hcr = have;
    o.entry_hcr = hcr;
    tests_init_result(&r, T_NV2);
    tests_nv2_fill_regime(&o, &r);
    tests_result_seal(&r);
    CHECK_EQ(r.nrows, 1);
    CHECK_EQ(r.overflow, 0);
    memcpy(out, r.rows[0], PROBE_LINE_PAYLOAD_MAX + 1);
}

void test_x_nv2_regime(void)
{
    static const struct {
        uint64_t hcr;
        const char *row;
    } cases[] = {
        {0x0000000088000010ull, "REGIME 0000000088000010 E2H=0 TGE=1"},
        {0x0000000488000010ull, "REGIME 0000000488000010 E2H=1 TGE=1"},
        {0x0000000400000000ull, "REGIME 0000000400000000 E2H=1 TGE=0"},
        {0x0000000008000000ull, "REGIME 0000000008000000 E2H=0 TGE=1"},
        {0x0000000000000000ull, "REGIME 0000000000000000 E2H=0 TGE=0"},
        {0xFFFFFFFFFFFFFFFFull, "REGIME FFFFFFFFFFFFFFFF E2H=1 TGE=1"},
        {0xFFFFFFFBF7FFFFFFull, "REGIME FFFFFFFBF7FFFFFF E2H=0 TGE=0"},
    };
    char row[PROBE_LINE_PAYLOAD_MAX + 1], canon[PROBE_LINE_COLS + 1];
    mockcore m;
    tests_nv2_ops ops;
    tests_ctx c;
    tests_nv2_record rec, rec2;
    tests_result r, r2;
    uint8_t bin[TESTS_NV2_BIN_SIZE], bin2[TESTS_NV2_BIN_SIZE];
    int have = 0, have2 = 0;

    section("nv2 regime");
    /* The format, value by value. */
    for (unsigned i = 0; i < sizeof cases / sizeof cases[0]; i++) {
        regime_row(cases[i].hcr, 1, row);
        CHECK_STR(row, cases[i].row);
        CHECK_EQ(strlen(row), 35);
        CHECK_EQ(probe_line_format(canon, row), 0);
    }
    regime_row(0xFFFFFFFFFFFFFFFFull, 0, row);
    CHECK_STR(row, "REGIME NOT READ"); /* the value is never shown when it was not read */
    CHECK_EQ(probe_line_format(canon, row), 0);
    /* Any value: the row fits the page, spells out the bits it names and carries all 64. */
    for (unsigned i = 0; i < 3000; i++) {
        unsigned long long value = 0, shown = 0;
        unsigned e2h = 9, tge = 9;
        uint64_t hcr = rnd();

        if (i % 3 == 1)
            hcr &= rnd();
        regime_row(hcr, 1, row);
        CHECK_EQ(strlen(row), 35);
        CHECK_EQ(probe_line_format(canon, row), 0);
        CHECK_EQ(sscanf(row, "REGIME %16llX E2H=%u TGE=%u", &shown, &e2h, &tge), 3);
        value = hcr;
        CHECK(shown == value);
        CHECK_EQ(e2h, (hcr >> 34) & 1u);
        CHECK_EQ(tge, (hcr >> 27) & 1u);
    }

    memset(&c, 0, sizeof c);
    x_good_ids(&c.ids);

    /* A good run: the row closes T02 and shows what the firmware left, read before the first
     * write of HCR_EL2 and not the stub's value, which is not the same. */
    mc_init(&m);
    ops = mc_ops(&m);
    tests_run_nv2(&c, &ops, NV2R_SOC_X1E80100, 0x1234, &r, &rec, &have);
    CHECK_EQ(r.verdict, TV_GO);
    CHECK_STR(last_row(&r), "REGIME 0000000088000010 E2H=0 TGE=1");
    CHECK(tests_hcr_for(MC_ENTRY_HCR, 1, 1) != MC_ENTRY_HCR);
    regime_row(tests_hcr_for(MC_ENTRY_HCR, 1, 1), 1, row);
    CHECK(strcmp(last_row(&r), row) != 0);
    CHECK_EQ(m.hcr_reads, 1);
    CHECK_EQ(m.hcr_calls_at_read, 0);
    CHECK_EQU(m.hcr, MC_ENTRY_HCR); /* and the model ends where the firmware left it */
    CHECK_EQ(r.nrows, TESTS_DETAIL_MAX); /* T02's budget is exactly full: 7 + 1 + 15 + 1 */
    CHECK_EQ(r.overflow, 0);
    CHECK(x_rows_valid(&r));
    CHECK(have);

    /* The record does not depend on the regime: another entry value changes the one row, and
     * the 13 record lines, their screen rows and the binary record not at all. */
    mc_init(&m);
    m.entry_hcr = m.hcr = 0x0000000488000030ull;
    ops = mc_ops(&m);
    tests_run_nv2(&c, &ops, NV2R_SOC_X1E80100, 0x1234, &r2, &rec2, &have2);
    CHECK_STR(last_row(&r2), "REGIME 0000000488000030 E2H=1 TGE=1");
    CHECK_EQ(r2.nrows, r.nrows);
    for (unsigned i = 0; i + 1 < r.nrows && i < r2.nrows; i++)
        CHECK_STR(r.rows[i], r2.rows[i]);
    CHECK(have2);
    CHECK_EQ(tests_nv2rec_pack(&rec, bin), 0);
    CHECK_EQ(tests_nv2rec_pack(&rec2, bin2), 0);
    CHECK_EQ(memcmp(bin, bin2, sizeof bin), 0);
    CHECK_EQ(r2.verdict, r.verdict);

    /* A register that cannot be read, and an ops table from before the read existed. */
    mc_init(&m);
    m.hcr_read_fails = 1;
    ops = mc_ops(&m);
    tests_run_nv2(&c, &ops, NV2R_SOC_X1E80100, 0x1234, &r, &rec, &have);
    CHECK_STR(last_row(&r), "REGIME NOT READ");
    CHECK_EQ(r.verdict, TV_GO);
    CHECK_EQ(r.overflow, 0);
    mc_init(&m);
    ops = mc_ops(&m);
    ops.real_hcr = NULL;
    tests_run_nv2(&c, &ops, NV2R_SOC_X1E80100, 0x1234, &r, &rec, &have);
    CHECK_STR(last_row(&r), "REGIME NOT READ");
    CHECK_EQ(m.hcr_reads, 0);
    CHECK_EQ(r.verdict, TV_GO);

    /* A run that did not start (no FEAT_NV): nothing is touched, not even for this row. */
    mc_init(&m);
    ops = mc_ops(&m);
    x_set_nv(&c.ids, 0, 0);
    tests_run_nv2(&c, &ops, NV2R_SOC_X1E80100, 0x1234, &r, &rec, &have);
    CHECK_STR(last_row(&r), "REGIME NOT READ");
    CHECK_EQ(m.hcr_reads, 0);
    CHECK_EQ(m.nlog, 0);
    CHECK_EQ(r.overflow, 0);
    x_good_ids(&c.ids);

    /* A failed set-up still shows the regime it was attempted in, read before anything was
     * written; a restore that failed leaves the row in place for whoever reads the result. */
    mc_init(&m);
    m.prepare_fails = 1;
    ops = mc_ops(&m);
    tests_run_nv2(&c, &ops, NV2R_SOC_X1E80100, 0x1234, &r, &rec, &have);
    CHECK_STR(last_row(&r), "REGIME 0000000088000010 E2H=0 TGE=1");
    CHECK_EQ(m.hcr_calls_at_read, 0);
    mc_init(&m);
    m.restore_fails = 1;
    ops = mc_ops(&m);
    tests_run_nv2(&c, &ops, NV2R_SOC_X1E80100, 0x1234, &r, &rec, &have);
    CHECK_EQ(r.fatal, 1);
    CHECK_STR(last_row(&r), "REGIME 0000000088000010 E2H=0 TGE=1");
    /* A mechanics failure whose record is "not-run" twice has no screen form, and the row is
     * still there, in a result nowhere near full. */
    mc_init(&m);
    m.run_fails_at = 1;
    ops = mc_ops(&m);
    tests_run_nv2(&c, &ops, NV2R_SOC_X1E80100, 0x1234, &r, &rec, &have);
    CHECK_STR(last_row(&r), "REGIME 0000000088000010 E2H=0 TGE=1");
    CHECK_EQ(r.overflow, 0);
    CHECK(r.nrows < TESTS_DETAIL_MAX);
    /* FEAT_NV without NV2: the control only. */
    mc_init(&m);
    ops = mc_ops(&m);
    x_set_nv(&c.ids, 1, 0);
    tests_run_nv2(&c, &ops, NV2R_SOC_X1E80100, 0x1234, &r, &rec, &have);
    CHECK_STR(last_row(&r), "REGIME 0000000088000010 E2H=0 TGE=1");
    CHECK_EQ(r.overflow, 0);
    CHECK(x_rows_valid(&r));
}

/* ---- The record a run produces ---- */

void test_x_record_evidence(void)
{
    tests_idset s;
    tests_nv2_record rec;
    tests_nv2_obs o;
    tests_nv2_verdict v;

    section("record evidence");
    x_good_ids(&s);
    /* GO on hardware: a pass pair, OBSERVED, never VERIFIED. */
    o = sound_obs(CTL_TRAPPED_OK, RED_LANDED_OK);
    v = tests_nv2_decide(&s, &o);
    CHECK_EQ(tests_nv2_make_record(&s, &o, &v, 0, NV2R_SOC_X1E80100, 0x1234, &rec), 0);
    CHECK_EQ(rec.vncr_redirect, NV2R_PASS);
    CHECK_EQ(rec.negative_control_trapped, NV2R_PASS);
    CHECK_EQ(rec.evidence, TL_OBSERVED);
    CHECK_STR(rec.tool, "SP11-X2-TESTS");
    CHECK_EQ(tests_nv2rec_check(&rec), 0);
    CHECK_EQU(rec.midr_el1, s.v[TI_MIDR]);
    CHECK_EQU(rec.revidr_el1, s.v[TI_REVIDR]);
    CHECK_EQU(rec.id_aa64mmfr2_el1, s.v[TI_MMFR2]);
    CHECK_EQU(rec.id_aa64mmfr4_el1, s.v[TI_MMFR4]);
    CHECK_EQ(rec.build_id, 0x1234);
    /* The same run in a model: a pass pair is still written (the monitor's tool
     * rejects it as model evidence), but it is EXPERIMENTAL and says so. */
    CHECK_EQ(tests_nv2_make_record(&s, &o, &v, 1, NV2R_SOC_X1E80100, 0x1234, &rec), 0);
    CHECK_EQ(rec.evidence, TL_EXPERIMENTAL);
    CHECK_STR(rec.tool, "SP11-X2-MODEL");
    CHECK(rec.evidence != TL_OBSERVED && rec.evidence != TL_VERIFIED);

    /* The redirect failed: a fail result, observed, no pass anywhere. */
    o = sound_obs(CTL_TRAPPED_OK, RED_NOT_LANDED);
    v = tests_nv2_decide(&s, &o);
    CHECK_EQ(tests_nv2_make_record(&s, &o, &v, 0, NV2R_SOC_X1E80100, 1, &rec), 0);
    CHECK_EQ(rec.vncr_redirect, NV2R_FAIL);
    CHECK_EQ(rec.negative_control_trapped, NV2R_PASS);
    CHECK_EQ(rec.evidence, TL_OBSERVED);
    /* The control failed. */
    o = sound_obs(CTL_NOT_TRAPPED, RED_LANDED_OK);
    v = tests_nv2_decide(&s, &o);
    CHECK_EQ(tests_nv2_make_record(&s, &o, &v, 0, NV2R_SOC_X1E80100, 1, &rec), 0);
    CHECK_EQ(rec.negative_control_trapped, NV2R_FAIL);
    CHECK(!(rec.vncr_redirect == NV2R_PASS && rec.negative_control_trapped == NV2R_PASS));
    /* A run that could not restore, or whose machinery faulted, says not-run. */
    o = sound_obs(CTL_TRAPPED_OK, RED_LANDED_OK);
    o.restore_ok = 0;
    v = tests_nv2_decide(&s, &o);
    CHECK_EQ(tests_nv2_make_record(&s, &o, &v, 0, NV2R_SOC_X1E80100, 1, &rec), 0);
    CHECK_EQ(rec.vncr_redirect, NV2R_NOT_RUN);
    CHECK_EQ(rec.negative_control_trapped, NV2R_NOT_RUN);
    CHECK_EQ(rec.evidence, TL_NONE);
    o = sound_obs(CTL_TRAPPED_OK, RED_LANDED_OK);
    o.mech_fault = 1;
    v = tests_nv2_decide(&s, &o);
    CHECK_EQ(tests_nv2_make_record(&s, &o, &v, 0, NV2R_SOC_X1E80100, 1, &rec), 0);
    CHECK_EQ(rec.vncr_redirect, NV2R_NOT_RUN);
    /* Observations that look perfect under ID bits that say "no NV2" must not
     * become a pass pair. */
    x_set_nv(&s, 1, 0);
    o = sound_obs(CTL_TRAPPED_OK, RED_LANDED_OK);
    v = tests_nv2_decide(&s, &o);
    CHECK(v.verdict != TV_GO);
    CHECK_EQ(tests_nv2_make_record(&s, &o, &v, 0, NV2R_SOC_X1E80100, 1, &rec), 0);
    CHECK(!(rec.vncr_redirect == NV2R_PASS && rec.negative_control_trapped == NV2R_PASS));
    /* No ID reading, no record. */
    x_good_ids(&s);
    s.have &= ~(1u << TI_REVIDR);
    CHECK_EQ(tests_nv2_make_record(&s, &o, &v, 0, NV2R_SOC_X1E80100, 1, &rec), -1);
    x_good_ids(&s);
    s.v[TI_MIDR] = 0;
    CHECK_EQ(tests_nv2_make_record(&s, &o, &v, 0, NV2R_SOC_X1E80100, 1, &rec), -1);

    /* A pass pair appears only with a GO verdict, over the whole input space. */
    for (int ctl = CTL_NOT_RUN; ctl <= CTL_OTHER; ctl++)
        for (int red = RED_NOT_RUN; red <= RED_OTHER; red++)
            for (unsigned nv = 0; nv < 4; nv++)
                for (int flags = 0; flags < 4; flags++)
                    for (int model = 0; model < 2; model++) {
                        x_good_ids(&s);
                        x_set_nv(&s, nv, 0);
                        o = sound_obs((enum tests_ctl)ctl, (enum tests_red)red);
                        o.restore_ok = flags & 1;
                        o.mech_fault = (flags >> 1) & 1;
                        v = tests_nv2_decide(&s, &o);
                        CHECK_EQ(tests_nv2_make_record(&s, &o, &v, model, NV2R_SOC_X1E80100, 7, &rec),
                                 0);
                        CHECK_EQ(tests_nv2rec_check(&rec), 0);
                        CHECK(rec.evidence != TL_VERIFIED);
                        if (rec.vncr_redirect == NV2R_PASS && rec.negative_control_trapped == NV2R_PASS)
                            CHECK_EQ(v.verdict, TV_GO);
                        if (model)
                            CHECK(rec.evidence != TL_OBSERVED);
                    }

    /* The evidence function itself. */
    CHECK_EQ(tests_nv2rec_evidence(0, NV2R_PASS, NV2R_PASS), TL_OBSERVED);
    CHECK_EQ(tests_nv2rec_evidence(0, NV2R_FAIL, NV2R_PASS), TL_OBSERVED);
    CHECK_EQ(tests_nv2rec_evidence(0, NV2R_PASS, NV2R_NOT_RUN), TL_EXPERIMENTAL);
    CHECK_EQ(tests_nv2rec_evidence(0, NV2R_NOT_RUN, NV2R_FAIL), TL_EXPERIMENTAL);
    CHECK_EQ(tests_nv2rec_evidence(0, NV2R_NOT_RUN, NV2R_NOT_RUN), TL_NONE);
    CHECK_EQ(tests_nv2rec_evidence(1, NV2R_PASS, NV2R_PASS), TL_EXPERIMENTAL);
    CHECK_EQ(tests_nv2rec_evidence(1, NV2R_NOT_RUN, NV2R_NOT_RUN), TL_NONE);
    for (int model = 0; model < 2; model++)
        for (uint32_t a = 0; a < 4; a++)
            for (uint32_t b = 0; b < 4; b++)
                CHECK(tests_nv2rec_evidence(model, a, b) != TL_VERIFIED);
}

/* ---- The record formats ---- */

static tests_nv2_record fixture(unsigned which)
{
    tests_nv2_record r;

    memset(&r, 0, sizeof r);
    r.soc = NV2R_SOC_X1E80100;
    r.midr_el1 = 0x00000000510F0010ull;
    r.revidr_el1 = 0x3;
    r.id_aa64mmfr2_el1 = 0x0000000002001234ull;
    r.id_aa64mmfr4_el1 = 0x0000000000000000ull;
    r.vncr_redirect = NV2R_PASS;
    r.negative_control_trapped = NV2R_PASS;
    r.evidence = TL_OBSERVED;
    r.build_id = 0xC0FFEE01u;
    strcpy(r.tool, "SP11-X2-TESTS");
    switch (which) {
    case 1: /* model */
        r.evidence = TL_EXPERIMENTAL;
        strcpy(r.tool, "SP11-X2-MODEL");
        break;
    case 2: /* redirect failed */
        r.vncr_redirect = NV2R_FAIL;
        break;
    case 3: /* nothing ran */
        r.vncr_redirect = r.negative_control_trapped = NV2R_NOT_RUN;
        r.evidence = TL_NONE;
        r.id_aa64mmfr2_el1 = 0;
        break;
    case 4: /* extremes */
        r.soc = NV2R_SOC_X2E96100;
        r.midr_el1 = ~0ull;
        r.revidr_el1 = ~0ull;
        r.id_aa64mmfr2_el1 = ~0ull;
        r.id_aa64mmfr4_el1 = ~0ull;
        r.build_id = 0xFFFFFFFFu;
        r.negative_control_trapped = NV2R_FAIL;
        strcpy(r.tool, "ABCDEFGHIJKLMNO");
        break;
    case 5: /* every allowed tool character */
        r.soc = NV2R_SOC_UNKNOWN;
        strcpy(r.tool, "a.b_c-Z9");
        r.evidence = TL_UNIMPLEMENTED;
        break;
    default:
        break;
    }
    return r;
}

#define FIXTURES 6u
static const char *const fixture_names[FIXTURES] = {"go-hw", "go-model", "redirect-fail",
                                                    "not-run", "extremes", "tool-chars"};

void test_x_record_format(void)
{
    tests_nv2_record r;
    uint8_t bin[TESTS_NV2_BIN_SIZE];
    char lines[TESTS_NV2_LINES][TESTS_NV2_LINE_MAX];
    char text[1024], json[1024], small[32];
    probe_payload rows[64];

    section("record format");
    CHECK_EQU(tests_crc32("123456789", 9), 0xCBF43926u);
    CHECK_EQU(tests_crc32("", 0), 0);
    CHECK_EQ(probe_crc8("123456789", 9), 0xF4);
    CHECK_EQ(TESTS_NV2_BIN_SIZE, 96);
    CHECK_EQ(TESTS_NV2_LINES, 13);

    for (unsigned k = 0; k < FIXTURES; k++) {
        size_t tl, jl;
        unsigned n, nrows;
        uint32_t crc;

        r = fixture(k);
        CHECK_EQ(tests_nv2rec_check(&r), 0);
        CHECK_EQ(tests_nv2rec_pack(&r, bin), 0);
        CHECK_EQ(bin[0], 'N');
        CHECK_EQ(bin[1], 'V');
        CHECK_EQ(bin[2], '2');
        CHECK_EQ(bin[3], 'R');
        CHECK_EQ(bin[4], 1);
        CHECK_EQ(bin[0x30], r.vncr_redirect);
        CHECK_EQ(bin[0x31], r.negative_control_trapped);
        CHECK_EQ(bin[0x32], r.evidence);
        CHECK_EQ(bin[0x38], (uint8_t)r.tool[0]);
        crc = bin[0x5c] | (bin[0x5d] << 8) | (bin[0x5e] << 16) | ((uint32_t)bin[0x5f] << 24);
        CHECK_EQU(crc, tests_crc32(bin, 0x5c));
        /* A flipped bit anywhere in the body changes the check. */
        for (unsigned bit = 0; bit < 0x5c * 8; bit += 37) {
            uint8_t copy[TESTS_NV2_BIN_SIZE];

            memcpy(copy, bin, sizeof copy);
            copy[bit / 8] ^= (uint8_t)(1u << (bit % 8));
            CHECK(tests_crc32(copy, 0x5c) != crc);
        }

        n = tests_nv2rec_lines(&r, lines);
        CHECK_EQ(n, TESTS_NV2_LINES);
        for (unsigned i = 0; i < n; i++) {
            const char *star = strrchr(lines[i], '*');
            unsigned want;

            CHECK(strncmp(lines[i], "NV2R", 4) == 0);
            CHECK(star != NULL);
            if (star == NULL)
                continue;
            CHECK_EQ(strlen(star), 3);
            want = probe_crc8(lines[i], (size_t)(star - lines[i]));
            CHECK_EQ((unsigned)strtoul(star + 1, NULL, 16), want);
            CHECK(strspn(star + 1, "0123456789abcdef") == 2); /* lower case, as the tool writes it */
            CHECK(strlen(lines[i]) < TESTS_NV2_LINE_MAX);
        }

        tl = tests_nv2rec_text(&r, text, sizeof text);
        CHECK(tl > 0);
        CHECK_EQ(strlen(text), tl);
        CHECK_EQ(text[tl - 1], '\n');
        jl = tests_nv2rec_json(&r, json, sizeof json);
        CHECK(jl > 0);
        CHECK_EQ(strlen(json), jl);
        /* A buffer one byte short yields nothing, and leaves nothing behind. */
        CHECK_EQ(tests_nv2rec_text(&r, small, sizeof small), 0);
        CHECK_EQ(small[0], 0);
        CHECK_EQ(tests_nv2rec_text(&r, text, tl), 0);
        CHECK_EQ(tests_nv2rec_text(&r, text, tl + 1), tl);
        CHECK_EQ(tests_nv2rec_json(&r, json, jl), 0);
        CHECK_EQ(tests_nv2rec_json(&r, json, jl + 1), jl);

        /* The screen form: upper case, at most 38 characters per row, and no row
         * carries a lower-case letter, which the font cannot show. */
        nrows = tests_nv2rec_screen_rows(lines, rows, 64);
        CHECK(nrows >= TESTS_NV2_LINES);
        CHECK_EQ(tests_nv2rec_screen_rows(lines, rows, 3), 0);
        for (unsigned i = 0; i < nrows; i++) {
            CHECK(strlen(rows[i]) <= 38);
            for (const char *p = rows[i]; *p != 0; p++)
                CHECK(!(*p >= 'a' && *p <= 'z'));
        }
    }

    /* Invalid records are never written in any form. */
    for (int bad = 0; bad < 8; bad++) {
        r = fixture(0);
        switch (bad) {
        case 0: r.soc = 3; break;
        case 1: r.vncr_redirect = 3; break;
        case 2: r.negative_control_trapped = 9; break;
        case 3: r.evidence = TL_UNKNOWN; break;
        case 4: r.tool[0] = 0; break;
        case 5: strcpy(r.tool, "has space"); break;
        case 6: memset(r.tool, 'A', 16); break; /* 16 characters, no terminator inside the limit */
        default: strcpy(r.tool, "bad/char"); break;
        }
        CHECK(tests_nv2rec_check(&r) != 0);
        CHECK_EQ(tests_nv2rec_pack(&r, bin), -1);
        CHECK_EQ(tests_nv2rec_lines(&r, lines), 0);
        CHECK_EQ(tests_nv2rec_text(&r, text, sizeof text), 0);
        CHECK_EQ(tests_nv2rec_json(&r, json, sizeof json), 0);
    }
}

/* ---- Records for the Python round trip ---- */

static void put_hex(const char *label, const void *data, size_t n)
{
    const uint8_t *p = data;

    printf("%s ", label);
    for (size_t i = 0; i < n; i++)
        printf("%02x", p[i]);
    printf("\n");
}

int x_emit_records(void)
{
    for (unsigned k = 0; k < FIXTURES; k++) {
        tests_nv2_record r = fixture(k);
        uint8_t bin[TESTS_NV2_BIN_SIZE];
        char text[1024], json[1024];
        char lines[TESTS_NV2_LINES][TESTS_NV2_LINE_MAX];
        probe_payload rows[64];
        unsigned nrows;
        size_t tl = tests_nv2rec_text(&r, text, sizeof text);
        size_t jl = tests_nv2rec_json(&r, json, sizeof json);

        if (tl == 0 || jl == 0 || tests_nv2rec_pack(&r, bin) != 0)
            return 1;
        printf("RECORD %s\n", fixture_names[k]);
        put_hex("TEXT", text, tl);
        put_hex("JSON", json, jl);
        put_hex("BIN", bin, sizeof bin);
        if (tests_nv2rec_lines(&r, lines) != TESTS_NV2_LINES)
            return 1;
        nrows = tests_nv2rec_screen_rows(lines, rows, 64);
        for (unsigned i = 0; i < nrows; i++)
            printf("ROW %s\n", rows[i]);
        printf("END\n");
    }
    return 0;
}
