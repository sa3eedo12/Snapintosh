/* SPDX-License-Identifier: MIT */
#include "nv2.h"

#include "sysstate.h"

static const char *const reason_names[NR_COUNT] = {
    "GO",
    "ID REGS NOT A READING",
    "RESERVED NV/NV_FRAC",
    "NV NOT IMPLEMENTED",
    "NV ONLY, NO NV2",
    "NV2 ONLY, NO NV",
    "FUNCTIONAL TEST DID NOT RUN",
    "STATE RESTORE FAILED",
    "MECHANICS FAULT",
    "NOT REDIRECTED",
    "CONTROL DID NOT TRAP",
    "ID SAYS NO NV2, ACCESS LANDED",
    "VNCR PAGE ABORT",
};

const char *tests_nv2_reason_name(int reason)
{
    if (reason < 0 || reason >= NR_COUNT)
        return "?";
    return reason_names[reason];
}

/* ---- Classifiers ---- */

enum tests_ctl tests_nv2_classify_control(const tests_el1_exit *e)
{
    switch (e->kind) {
    case EXIT_SYSREG: {
        tests_sys64_iss i;

        /* MRS x1, TPIDR_EL2: op0 3, op1 4, CRn 13, CRm 0, op2 2, a read into x1. */
        tests_esr_sys64(e->esr, &i);
        if (tests_esr_is_sysreg(e->esr, 3, 4, 13, 0, 2, 1) && i.rt == 1)
            return CTL_TRAPPED_OK;
        return CTL_TRAPPED_WRONG_ISS;
    }
    case EXIT_HVC:
        if (e->imm == TESTS_HVC_DONE_CONTROL)
            return CTL_NOT_TRAPPED;
        if (e->imm >= TESTS_HVC_VECTOR_BASE && e->imm < TESTS_HVC_VECTOR_BASE + 16u)
            return CTL_UNDEF_EL1;
        return CTL_OTHER;
    case EXIT_DABT:
        return CTL_ABORT;
    default:
        return CTL_OTHER;
    }
}

enum tests_red tests_nv2_classify_redirect(const tests_el1_exit *e, const uint64_t *page,
                                           int real_changed, unsigned *landed_offset,
                                           uint64_t *readback)
{
    unsigned target = TESTS_NV2_TARGET_OFFSET / 8u;
    unsigned changed = 0;

    switch (e->kind) {
    case EXIT_SYSREG:
        return RED_TRAPPED;
    case EXIT_DABT:
        return RED_VNCR_ABORT;
    case EXIT_HVC:
        if (e->imm >= TESTS_HVC_VECTOR_BASE && e->imm < TESTS_HVC_VECTOR_BASE + 16u)
            return RED_UNDEF_EL1;
        if (e->imm != TESTS_HVC_DONE_REDIRECT)
            return RED_OTHER;
        break;
    default:
        return RED_OTHER;
    }

    /* The stub ran to its end. */
    *readback = e->x[1];
    if (real_changed)
        return RED_REAL_REG_CHANGED;
    for (unsigned i = 0; i < TESTS_NV2_PAGE_WORDS; i++)
        if (page[i] != TESTS_NV2_POISON)
            changed++;
    if (changed == 0)
        return RED_NOT_LANDED;
    if (page[target] == TESTS_NV2_PATTERN && changed == 1)
        return e->x[0] == TESTS_NV2_PATTERN && e->x[1] == TESTS_NV2_PATTERN ? RED_LANDED_OK
                                                                           : RED_VALUE_MISMATCH;
    for (unsigned i = 0; i < TESTS_NV2_PAGE_WORDS; i++) {
        if (i != target && page[i] == TESTS_NV2_PATTERN) {
            *landed_offset = i * 8u;
            return RED_LANDED_ELSEWHERE;
        }
    }
    return RED_VALUE_MISMATCH;
}

/* ---- The run ---- */

static void run_control(const tests_nv2_ops *ops, tests_nv2_obs *o)
{
    tests_el1_exit e;

    probe_memset(&e, 0, sizeof e);
    if (ops->set_hcr(ops->ctx, 1, 0, 0) != 0 ||
        ops->run(ops->ctx, EL1_PROG_CONTROL, &e) != 0 || e.kind == EXIT_MECH) {
        o->mech_fault = 1;
        return;
    }
    o->ctl_exit = e;
    o->ctl = tests_nv2_classify_control(&e);
}

static void run_redirect(const tests_nv2_ops *ops, tests_nv2_obs *o)
{
    tests_el1_exit e;
    uint64_t *page = ops->page(ops->ctx);
    uint64_t before = 0, after = 0;

    for (unsigned i = 0; i < TESTS_NV2_PAGE_WORDS; i++)
        page[i] = TESTS_NV2_POISON;
    ops->page_sync(ops->ctx);
    probe_memset(&e, 0, sizeof e);
    if (ops->real_tpidr(ops->ctx, &before) != 0 || ops->set_hcr(ops->ctx, 1, 1, 1) != 0 ||
        ops->run(ops->ctx, EL1_PROG_REDIRECT, &e) != 0 || e.kind == EXIT_MECH) {
        o->mech_fault = 1;
        return;
    }
    ops->page_sync(ops->ctx);
    if (ops->real_tpidr(ops->ctx, &after) != 0) {
        o->mech_fault = 1;
        return;
    }
    o->red_exit = e;
    o->red = tests_nv2_classify_redirect(&e, page, before != after, &o->landed_offset,
                                         &o->readback);
}

void tests_nv2_run(const tests_nv2_ops *ops, const tests_idset *ids, tests_nv2_obs *o)
{
    enum tests_nv_class cls;
    int rc;

    probe_memset(o, 0, sizeof *o);
    if (!tests_ids_plausible(ids, NULL))
        return;
    cls = tests_nv_classify(ids);
    if (cls != NVC_NV_ONLY && cls != NVC_NV_NV2)
        return;

    o->attempted = 1;
    /* Before prepare(), and so before any write: the stub's HCR is built from this value
     * (tests_hcr_for keeps its E2H), and the page has to say which regime that was. */
    if (ops->real_hcr != NULL)
        o->have_entry_hcr = ops->real_hcr(ops->ctx, &o->entry_hcr) == 0;
    rc = ops->prepare(ops->ctx);
    if (rc != 0) {
        /* A failed prepare has already rolled itself back; only a failed
         * roll-back is a state this image cannot vouch for. */
        o->mech_fault = 1;
        o->cleanup_failed = rc == TESTS_CLEANUP_FAILED;
        return;
    }
    o->prepared = 1;
    run_control(ops, o);
    if (!o->mech_fault && cls == NVC_NV_NV2)
        run_redirect(ops, o);
    o->restore_ok = ops->restore(ops->ctx) == 0;
    if (!o->restore_ok)
        o->cleanup_failed = 1;
}

/* ---- The decision ---- */

static tests_nv2_verdict mk(int verdict, int reason, int label)
{
    tests_nv2_verdict v;

    v.verdict = verdict;
    v.reason = reason;
    v.label = label;
    return v;
}

tests_nv2_verdict tests_nv2_decide(const tests_idset *ids, const tests_nv2_obs *o)
{
    enum tests_nv_class cls;

    if (!tests_ids_plausible(ids, NULL))
        return mk(TV_INCONCLUSIVE, NR_IDS_UNUSABLE, TL_UNKNOWN);
    cls = tests_nv_classify(ids);
    if (cls == NVC_UNREAD || cls == NVC_RESERVED)
        return mk(TV_INCONCLUSIVE, NR_RESERVED_ID, TL_OBSERVED);
    if (o->cleanup_failed || (o->prepared && !o->restore_ok))
        return mk(TV_INCONCLUSIVE, NR_RESTORE_FAILED, TL_OBSERVED);
    if (o->mech_fault)
        return mk(TV_INCONCLUSIVE, NR_MECH_FAULT, TL_OBSERVED);

    switch (cls) {
    case NVC_NONE:
        if (o->red == RED_LANDED_OK)
            return mk(TV_INCONCLUSIVE, NR_CONTRADICTION, TL_OBSERVED);
        return mk(TV_NO_GO, NR_NV_NONE, TL_INFERRED);
    case NVC_NV_ONLY:
        if (o->red == RED_LANDED_OK)
            return mk(TV_INCONCLUSIVE, NR_CONTRADICTION, TL_OBSERVED);
        return mk(TV_NO_GO, NR_NV_ONLY, TL_INFERRED);
    case NVC_NV2_ONLY:
        return mk(TV_INCONCLUSIVE, NR_NV2_ONLY, TL_INFERRED);
    default:
        break;
    }

    /* NV == 2, NV_frac == 0: the monitor's combination. Only behaviour counts. */
    if (!o->prepared || o->red == RED_NOT_RUN)
        return mk(TV_INCONCLUSIVE, NR_NOT_RUN, TL_OBSERVED);
    switch (o->red) {
    case RED_LANDED_OK:
        if (o->ctl == CTL_TRAPPED_OK)
            return mk(TV_GO, NR_GO, TL_OBSERVED);
        if (o->ctl == CTL_NOT_RUN)
            return mk(TV_INCONCLUSIVE, NR_NOT_RUN, TL_OBSERVED);
        return mk(TV_INCONCLUSIVE, NR_CONTROL_FAILED, TL_OBSERVED);
    case RED_LANDED_ELSEWHERE:
    case RED_VALUE_MISMATCH:
    case RED_NOT_LANDED:
    case RED_TRAPPED:
    case RED_UNDEF_EL1:
        return mk(TV_NO_GO, NR_REDIRECT_FAILED, TL_OBSERVED);
    case RED_VNCR_ABORT:
        return mk(TV_INCONCLUSIVE, NR_VNCR_ABORT, TL_OBSERVED);
    default:
        return mk(TV_INCONCLUSIVE, NR_MECH_FAULT, TL_OBSERVED);
    }
}

/* ---- The record ---- */

static uint32_t result_of_ctl(enum tests_ctl c)
{
    switch (c) {
    case CTL_TRAPPED_OK:
        return NV2R_PASS;
    case CTL_TRAPPED_WRONG_ISS:
    case CTL_NOT_TRAPPED:
    case CTL_UNDEF_EL1:
        return NV2R_FAIL;
    default:
        return NV2R_NOT_RUN;
    }
}

static uint32_t result_of_red(enum tests_red r)
{
    switch (r) {
    case RED_LANDED_OK:
        return NV2R_PASS;
    case RED_LANDED_ELSEWHERE:
    case RED_VALUE_MISMATCH:
    case RED_NOT_LANDED:
    case RED_TRAPPED:
    case RED_UNDEF_EL1:
        return NV2R_FAIL;
    default:
        return NV2R_NOT_RUN;
    }
}

int tests_nv2_make_record(const tests_idset *ids, const tests_nv2_obs *o,
                          const tests_nv2_verdict *v, int model, uint32_t soc, uint32_t build_id,
                          tests_nv2_record *rec)
{
    const char *tool = model ? "SP11-X2-MODEL" : "SP11-X2-TESTS";
    uint32_t red = NV2R_NOT_RUN, ctl = NV2R_NOT_RUN;

    probe_memset(rec, 0, sizeof *rec);
    if (!tests_ids_plausible(ids, NULL) || !tests_ids_have(ids, TI_REVIDR))
        return -1;
    if (o->prepared && o->restore_ok && !o->mech_fault && !o->cleanup_failed) {
        red = result_of_red(o->red);
        ctl = result_of_ctl(o->ctl);
    }
    /* A pass pair is the one thing the monitor's gate trusts, so it is written
     * only by a run that ended in GO. */
    if (v->verdict != TV_GO && red == NV2R_PASS && ctl == NV2R_PASS)
        red = NV2R_NOT_RUN;

    rec->soc = soc;
    rec->midr_el1 = ids->v[TI_MIDR];
    rec->revidr_el1 = ids->v[TI_REVIDR];
    rec->id_aa64mmfr2_el1 = ids->v[TI_MMFR2];
    rec->id_aa64mmfr4_el1 = ids->v[TI_MMFR4];
    rec->vncr_redirect = red;
    rec->negative_control_trapped = ctl;
    rec->evidence = tests_nv2rec_evidence(model, red, ctl);
    for (unsigned i = 0; tool[i] != 0 && i < TESTS_NV2_TOOL_MAX; i++)
        rec->tool[i] = tool[i];
    rec->build_id = build_id;
    return 0;
}

/* ---- Rows ---- */

static const char *ctl_name(enum tests_ctl c)
{
    switch (c) {
    case CTL_NOT_RUN:
        return "NOT RUN";
    case CTL_TRAPPED_OK:
        return "TRAPPED AS EXPECTED";
    case CTL_TRAPPED_WRONG_ISS:
        return "TRAPPED, WRONG ISS";
    case CTL_NOT_TRAPPED:
        return "DID NOT TRAP";
    case CTL_UNDEF_EL1:
        return "UNDEF AT EL1";
    case CTL_ABORT:
        return "DATA ABORT";
    default:
        return "OTHER EXIT";
    }
}

static const char *red_name(enum tests_red r)
{
    switch (r) {
    case RED_NOT_RUN:
        return "NOT RUN";
    case RED_LANDED_OK:
        return "LANDED IN PAGE";
    case RED_LANDED_ELSEWHERE:
        return "LANDED AT OTHER OFFSET";
    case RED_VALUE_MISMATCH:
        return "VALUE MISMATCH";
    case RED_NOT_LANDED:
        return "PAGE UNTOUCHED";
    case RED_TRAPPED:
        return "TRAPPED TO EL2";
    case RED_UNDEF_EL1:
        return "UNDEF AT EL1";
    case RED_REAL_REG_CHANGED:
        return "REAL TPIDR_EL2 CHANGED";
    case RED_VNCR_ABORT:
        return "DATA ABORT";
    default:
        return "OTHER EXIT";
    }
}

static const char *k1_text(const tests_nv2_verdict *v)
{
    if (v->verdict == TV_GO)
        return "K1 CLEAR: NV2 USABLE";
    if (v->verdict == TV_NO_GO)
        return "K1 CANDIDATE: NV2 ABSENT/UNUSABLE";
    return "K1 NOT DECIDED: RE-RUN OR REVIEW";
}

void tests_nv2_fill_rows(const tests_idset *ids, const tests_nv2_obs *o,
                         const tests_nv2_verdict *v, tests_result *r)
{
    probe_sb *sb;
    uint64_t nv = tests_ids_have(ids, TI_MMFR2) ? tests_bits(ids->v[TI_MMFR2], 27, 24) : 0;
    uint64_t frac = tests_ids_have(ids, TI_MMFR4) ? tests_bits(ids->v[TI_MMFR4], 23, 20) : 0;

    if ((sb = tests_row(r)) != NULL) {
        probe_sb_puts(sb, "ID NV=");
        probe_sb_dec(sb, nv);
        probe_sb_puts(sb, " NV_FRAC=");
        probe_sb_dec(sb, frac);
        probe_sb_putc(sb, ' ');
        probe_sb_puts(sb, tests_nv_class_name(tests_nv_classify(ids)));
    }
    if ((sb = tests_row(r)) != NULL) {
        probe_sb_puts(sb, "CTL NV2 OFF: ");
        probe_sb_puts(sb, ctl_name(o->ctl));
    }
    if ((sb = tests_row(r)) != NULL) {
        probe_sb_puts(sb, "RED NV2 ON: ");
        probe_sb_puts(sb, red_name(o->red));
        if (o->red == RED_LANDED_ELSEWHERE) {
            probe_sb_puts(sb, " ");
            probe_sb_hex(sb, o->landed_offset, 3);
        }
    }
    if ((sb = tests_row(r)) != NULL) {
        probe_sb_puts(sb, "ESR CTL=");
        probe_sb_hex(sb, o->ctl_exit.esr, 8);
        probe_sb_puts(sb, " RED=");
        probe_sb_hex(sb, o->red_exit.esr, 8);
    }
    if ((sb = tests_row(r)) != NULL) {
        probe_sb_puts(sb, "RESTORED=");
        probe_sb_puts(sb, !o->prepared ? "N/A" : o->restore_ok ? "YES" : "NO");
        probe_sb_puts(sb, " MECH=");
        probe_sb_puts(sb, o->mech_fault ? "YES" : "NO");
        probe_sb_puts(sb, " CLEANUP=");
        probe_sb_puts(sb, o->cleanup_failed ? "FAILED" : "OK");
    }
    if ((sb = tests_row(r)) != NULL) {
        probe_sb_puts(sb, "WHY ");
        probe_sb_puts(sb, tests_nv2_reason_name(v->reason));
    }
    tests_row_text(r, k1_text(v));
}

void tests_nv2_fill_regime(const tests_nv2_obs *o, tests_result *r)
{
    probe_sb *sb = tests_row(r);

    if (sb == NULL)
        return;
    probe_sb_puts(sb, "REGIME ");
    if (!o->have_entry_hcr) {
        probe_sb_puts(sb, "NOT READ");
        return;
    }
    probe_sb_hex(sb, o->entry_hcr, 16);
    probe_sb_puts(sb, " E2H=");
    probe_sb_dec(sb, (o->entry_hcr & TESTS_HCR_E2H) != 0);
    probe_sb_puts(sb, " TGE=");
    probe_sb_dec(sb, (o->entry_hcr & TESTS_HCR_TGE) != 0);
}
