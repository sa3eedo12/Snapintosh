/* SPDX-License-Identifier: MIT */
/* The dry run's own half of the GIC and stop stage (docs/SP11-STICK-GIC.md; docs/SP11-MONITOR-PORT.md, work item 8): after the arm of
 * sp11-stick-gic.c it arms the EL2 timer and runs two bounded windows in place of the monitor, which is never called. EXPERIMENTAL;
 * hardware NOT RUN; compiled only under SNAP_SP11_STICK_GIC=1.
 *
 * Order, after the arm (the GIC is enabled, PREPARED is written, the record is in REPORT.DAT).
 *   T, V     the EL2 timer armed for the time box, overwriting whatever deadline and enable it was found with (CVAL, then CTL; a timer found
 *            asserting is refused in the reads), VBAR_EL2 and IRQ unmasked inside the window routine only.
 *   window 1 (EL2) three interrupts, each acknowledged, its level dropped by re-arming the timer a short time ahead and then ended
 *            (the order of the monitor's tick path, probes/xnuboot/async.c: HPPIR1, IAR1, the re-arm, the EOI); the fourth deadline is
 *            taken with no acknowledge at all, as the monitor's stop path takes it, and CNTHP_CTL = 0 must leave nothing pending. Each
 *            interrupt records HPPIR1 at the vector, ISTATUS, what IAR1 returned, the state after the end, and its latency against the
 *            deadline that was programmed.
 *   E        only if window 1 passed: a second announcement and eight seconds, then VBAR_EL1, SCTLR_EL1 and HCR_EL2 are written with
 *            q1n1's el1trap values (HCR_EL2 keeps IMO), each read back, the stub's lines are made visible and the timer is armed.
 *   window 2 (EL1) an ERET to a stub that spins on the counter with IRQ unmasked and then HVCs, so that a lost interrupt cannot leave
 *            the stage at EL1. The interrupt must be taken from EL1 at the lower-EL vector (+0x480), which is where the monitor takes
 *            its stop interrupt from the guest. Any other vector or exception class ends the window with a named reason.
 *   restore  E, V and T in the reverse order of the stores, each verified, one retry, then sp11-stick-gic.c puts classes C, R and G back.
 * Every wait is bounded by the counter, and the EL1 stub's loop by the counter at EL1 (CNTPCT_EL0, which CNTHCTL_EL2.EL1PCTEN lets it
 * read: refused before any store if it is clear). No heap, no writable global, no libc, no recursion. The record and the working state
 * are in the stack. */
#include "sp11-stick-hw.h"

#if SNAP_SP11_STICK_GIC
#include "sp11-stick-gic.h"

#ifndef SP11_STICK_HOST_TEST
/* The writes of the dry run's own registers, each defined here once and out of line (the host test's model defines them otherwise): one MSR
 * and an ISB, so that the binary shows each register's write in one function. Class T is the EL2 timer; class E, the EL1 excursion, is the
 * three registers that decide whether EL1 can be entered and how it is fetched. */
__attribute__((noinline)) void sp11hw_write_cnthp_ctl_el2(uint64_t value) { SP11HW_MSR_ISB(cnthp_ctl_el2, value); }
__attribute__((noinline)) void sp11hw_write_cnthp_cval_el2(uint64_t value) { SP11HW_MSR_ISB(cnthp_cval_el2, value); }
__attribute__((noinline)) void sp11hw_write_hcr_el2(uint64_t value) { SP11HW_MSR_ISB(hcr_el2, value); }
__attribute__((noinline)) void sp11hw_write_vbar_el1(uint64_t value) { SP11HW_MSR_ISB(vbar_el1, value); }
__attribute__((noinline)) void sp11hw_write_sctlr_el1(uint64_t value) { SP11HW_MSR_ISB(sctlr_el1, value); }
/* The EL1 timers, each read once by the survey: one MRS and nothing else (the monitor entry's arm half does not have them). */
__attribute__((noinline)) uint64_t sp11hw_read_cntv_ctl_el0(void) { return SP11HW_MRS(cntv_ctl_el0); }
__attribute__((noinline)) uint64_t sp11hw_read_cntv_cval_el0(void) { return SP11HW_MRS(cntv_cval_el0); }
__attribute__((noinline)) uint64_t sp11hw_read_cntp_ctl_el0(void) { return SP11HW_MRS(cntp_ctl_el0); }
__attribute__((noinline)) uint64_t sp11hw_read_cntp_cval_el0(void) { return SP11HW_MRS(cntp_cval_el0); }
#endif

/* ---- Reads that decide the EL1 window, before any store ---- */

/* CNTHCTL_EL2.EL1PCTEN for the stub's counter reads; the image of the stub and its vector table mapped and identity-mapped, because a core with
 * its MMU off fetches them by physical address and a wrong one would lock the stage in a loop of aborts at EL1 that no counter can end; and
 * the three EL1 registers' old values. */
static unsigned gic_dry_precheck(struct sw_gic *g)
{
    struct sw_gic_rec *r = &g->rec.r;
    struct sw_probe probe;
    r->cnthctl_el2 = sp11hw_read_cnthctl_el2();
    if (!(r->cnthctl_el2 & SG_CNTHCTL_EL1PCTEN)) return SG_OUT_EL1_PCT_TRAPPED;
    if (sw_probe_range(sp11hw_el1_vectors(), sp11hw_el1_image_bytes(), 0, &probe)) return SG_OUT_EL1_IMAGE;
    r->reg[SG_R_HCR_EL2].old = sp11hw_read_hcr_el2();
    r->reg[SG_R_VBAR_EL1].old = sp11hw_read_vbar_el1();
    r->reg[SG_R_SCTLR_EL1].old = sp11hw_read_sctlr_el1();
    return 0;
}

/* ---- The announcements ---- */

/* What the stores put in place, shown before the EL2 window opens: a freeze or a reset inside it leaves this line as the last. ISEN is what the
 * quarantine and the PPI's own enable left (read back after the stores), HPPIR what the CPU interface would hand out next with IRQ still masked. The
 * line is at most 99 characters with its prefix for the values that can reach it: this line is printed only when the arm passed, and then HPPIR is 1023
 * or 26 (anything else was refused FOREIGN_PENDING first), so it has two or four digits. An INTID of eight digits would make the line 103 characters,
 * over the interface's LINE_MAX of 100, where the line buffer would cut it (the last three digits lost); that is unreachable and is not guarded here. */
static void gic_say_next_window(struct sw_gic *g)
{
    struct pl_line l;
    const struct sw_gic_rec *r = &g->rec.r;
    sw_begin(&l);
    pl_text(&l, "NEXT GIC WINDOW ");
    sc_dec(&l, SP11_STICK_GIC_TIME_BOX_S);
    pl_text(&l, " S");
    sg_key_h32(g->c->a, &l, " GICD=", r->reg[SG_R_GICD_CTLR].rb);
    sg_key_h32(g->c->a, &l, " ISEN=", r->reg[SG_R_ISENABLER0].rb);
    sg_key_h8(g->c->a, &l, " PRIO=", r->reg[SG_R_IPRIORITYR].rb);
    sg_key_h8(g->c->a, &l, " PMR=", r->reg[SG_R_ICC_PMR].rb);
    sg_key_bit(&l, " IGRPEN1=", r->reg[SG_R_ICC_IGRPEN1].rb, 0);
    sc_key_dec(&l, " HPPIR=", r->sv.hppir_pre & 0xFFFFFF);
    sw_emit(g->c, &l, 1);
}

/* The second announcement, before the first store of class E: what is about to be written and where EL1 is entered, and eight seconds. */
static void gic_say_next_el1(struct sw_gic *g)
{
    struct pl_line l;
    sw_begin(&l);
    pl_text(&l, "NEXT GIC EL1 EXCURSION AFTER ");
    sc_dec(&l, SW_GIC_WAIT_S);
    pl_text(&l, " S");
    sc_key_hex(g->c->a, &l, " HCR=", SW_GIC_EL1_HCR);
    sc_key_hex(g->c->a, &l, " SCTLR1=", SW_GIC_EL1_SCTLR);
    sc_key_hex(g->c->a, &l, " VBAR1=", sp11hw_el1_vectors());
    sg_key_bit(&l, " PCT=", g->rec.r.cnthctl_el2, 0);
    sw_emit(g->c, &l, 1);
}

/* ---- T: the timer ---- */

/* Armed for the window `w`: an absolute deadline, so that the delivery can be measured against it. Window 1's last tick leaves room for the
 * short cycles; window 2's is twice the time box, the bound of the EL1 stub's own loop. */
static unsigned gic_arm_timer(struct sw_gic *g, unsigned w)
{
    struct sw_gic_rec *r = &g->rec.r;
    struct sg_win *win = &r->win[w];
    uint64_t now;
    sw_gic_begin_class(g, SG_CLASS_T);
    now = sp11hw_ticks();
    win->t_arm = now;
    r->irq[w == SG_WIN_EL2 ? 0 : SG_IRQ_EL1].cval = now + r->timebox_ticks;
    win->deadline = now + 2 * r->timebox_ticks + (w == SG_WIN_EL2 ? 2 * (SG_CYCLES + 1) * r->cycle_ticks : 0);
    r->reg[SG_R_CNTHP_CVAL].new = now + r->timebox_ticks;
    r->reg[SG_R_CNTHP_CTL].new = SG_CNTHP_CTL_ENABLE;
    r->wrote |= 1u << SG_R_CNTHP_CVAL | 1u << SG_R_CNTHP_CTL;
#ifdef SP11_STICK_GIC_SEED_KEEP_FOREIGN_DEADLINE
    if (!(r->reg[SG_R_CNTHP_CTL].old & SG_CNTHP_CTL_ENABLE))     /* a stage that trusts the deadline it found when the timer was enabled */
#endif
    sp11hw_write_cnthp_cval_el2(r->reg[SG_R_CNTHP_CVAL].new);       /* the deadline first: a foreign enable stays quiet while it is overwritten */
    sp11hw_write_cnthp_ctl_el2(SG_CNTHP_CTL_ENABLE);
    r->reg[SG_R_CNTHP_CVAL].rb = sw_gic_current(g, SG_R_CNTHP_CVAL);
    r->reg[SG_R_CNTHP_CTL].rb = sw_gic_current(g, SG_R_CNTHP_CTL);
    return (r->reg[SG_R_CNTHP_CTL].rb & SG_CNTHP_CTL_ENABLE) && r->reg[SG_R_CNTHP_CVAL].rb == r->reg[SG_R_CNTHP_CVAL].new ? 0 : SG_OUT_T_NOT_HELD;
}

/* ---- What a window showed ---- */

/* The registers that say why nothing arrived, read before the timer is disabled so that ISTATUS and HPPIR are as they were. Used for window 1
 * after the window and by the EL1 vector at the stub's HVC, with the SPSR_EL2 it was entered with. */
static void gic_diag_read(struct sw_gic_rec *rec, unsigned w, uint64_t spsr)
{
    struct sg_win *win = &rec->win[w];
    uint64_t sgi = rec->gicr_base + SG_SGI_FRAME;
    win->dg_ctl = sp11hw_read_cnthp_ctl_el2();
    win->dg_hppir1 = sp11hw_read_icc_hppir1_el1();
    win->dg_ispendr0 = sp11hw_mmio_read32(sgi + SG_ISPENDR0);
    win->dg_isenabler0 = sp11hw_mmio_read32(sgi + SG_ISENABLER0);
    win->dg_gicd_ctlr = sp11hw_mmio_read32(rec->gicd_base + SG_GICD_CTLR);
    win->dg_daif = (uint32_t)sp11hw_read_daif();
    win->dg_rpr = sp11hw_read_icc_rpr_el1();
    win->dg_pmr = sp11hw_read_icc_pmr_el1();
    win->dg_hcr = sp11hw_read_hcr_el2();
    win->dg_spsr = spsr;
}

static void gic_latency(const struct sw_gic *g, struct sg_irq *q)
{
    int64_t lat = (int64_t)(q->t_irq - q->cval), us;
    if (lat > SG_LATENCY_CLAMP) lat = SG_LATENCY_CLAMP;
    if (lat < -SG_LATENCY_CLAMP) lat = -SG_LATENCY_CLAMP;
    us = lat < 0 ? -(int64_t)((uint64_t)(-lat) * 1000000u / g->hz) : (int64_t)((uint64_t)lat * 1000000u / g->hz);
    if (us > SG_LAT_US_CLAMP) us = SG_LAT_US_CLAMP;
    if (us < -SG_LAT_US_CLAMP) us = -SG_LAT_US_CLAMP;
    q->lat_ticks = lat;
    q->lat_us = us;
}

/* The checks every interrupt passes in the order of how wrong it was; 0 when it passed. `final` is the deadline taken without an
 * acknowledge, whose state after the timer was disabled is what must be clear. */
static unsigned gic_irq_verdict(const struct sw_gic *g, const struct sg_irq *q, int final)
{
    if ((q->flags & SG_IRQF_ACKED) && (q->iar & 0xFFFFFF) != g->intid) return SG_OUT_WRONG_INTID;
    if (q->lat_ticks < -(int64_t)g->rec.r.slack_ticks) return SG_OUT_STORM;      /* it came before the deadline that was programmed: a source that re-fired on its own */
    if (q->hppir_vec != g->intid) return SG_OUT_HPPIR_NOT_26;
    if (!(q->ctl & SG_CNTHP_ISTATUS)) return SG_OUT_NO_ISTATUS;
    if (q->hppir_post != SG_INTID_NONE || (q->flags & (SG_IRQF_PEND | SG_IRQF_ACT))) return final ? SG_OUT_FINAL_PENDING : SG_OUT_NOT_CLEAN;
    return 0;
}

/* Window 1: what the vector recorded, in the order of how wrong it was. */
static unsigned gic_classify_el2(struct sw_gic *g)
{
    struct sw_gic_rec *r = &g->rec.r;
    struct sg_win *w = &r->win[SG_WIN_EL2];
    unsigned k, bad;
    gic_diag_read(r, SG_WIN_EL2, 0);
    for (k = 0; k <= SG_IRQ_FINAL; k++)
        if (r->irq[k].flags & SG_IRQF_ENTERED) gic_latency(g, &r->irq[k]);
    if (w->other_entries) return SG_OUT_EXCEPTION;
    if (!w->irq_entries) return SG_OUT_NO_IRQ;
    if (w->irq_entries > SG_CYCLES + 1) return SG_OUT_STORM;
    for (k = 0; k <= SG_IRQ_FINAL; k++) {
        if (!(r->irq[k].flags & SG_IRQF_ENTERED)) return SG_OUT_CYCLE_LOST;
        if ((bad = gic_irq_verdict(g, &r->irq[k], k == SG_IRQ_FINAL)) != 0) return bad;
        r->irq[k].flags |= SG_IRQF_OK;
    }
    return SG_OUT_IRQ_OK;
}

/* Window 2: the same checks for the one interrupt, after the entries that are not an interrupt. */
static unsigned gic_classify_el1(struct sw_gic *g)
{
    struct sw_gic_rec *r = &g->rec.r;
    struct sg_win *w = &r->win[SG_WIN_EL1];
    struct sg_irq *q = &r->irq[SG_IRQ_EL1];
    unsigned bad;
    if (w->other_entries) return SG_OUT_EL1_EXCEPTION;
    if (!w->irq_entries) return SG_OUT_NO_IRQ_FROM_EL1;
    gic_latency(g, q);
    if ((q->flags & SG_IRQF_ACKED) && (q->iar & 0xFFFFFF) != g->intid) return SG_OUT_EL1_WRONG_INTID;
    if ((bad = gic_irq_verdict(g, q, 0)) != 0) return bad;
    q->flags |= SG_IRQF_OK;
    return SG_OUT_IRQ_OK;
}

/* ---- The windows ---- */

static unsigned gic_el2_window(struct sw_gic *g)
{
    struct sw_gic_rec *r = &g->rec.r;
    unsigned out = gic_arm_timer(g, SG_WIN_EL2);
    if (out) return out;
    r->substage = SG_SUB_ARMED;
    sw_gic_flush(g);
    gic_say_next_window(g);
    r->reg[SG_R_VBAR_EL2].new = sp11hw_gic_vectors();
    r->wrote |= 1u << SG_R_VBAR_EL2 | 1u << SG_R_DAIF;
    r->substage = SG_SUB_WINDOW;
    r->done = 0;
    r->cycle = 0;
    sw_gic_begin_class(g, SG_CLASS_V);
    sp11hw_irq_window(r, r->reg[SG_R_VBAR_EL2].new, r->reg[SG_R_VBAR_EL2].old, r->win[SG_WIN_EL2].deadline);
    r->substage = SG_SUB_WINDOW_DONE;
    out = gic_classify_el2(g);
    sw_gic_say(g, SG_L_IRQ, 0);         /* window 1's result is on the page before the excursion is announced: a freeze in window 2 leaves it filmed */
    sw_gic_say(g, SG_L_DIAG, 0);
    return out;
}

/* E: the writes, their read-back, the visibility of the stub, the timer, and the excursion. Nothing is entered unless every write held. */
static unsigned gic_el1_window(struct sw_gic *g)
{
    struct sw_gic_rec *r = &g->rec.r;
    struct sg_reg *hcr = &r->reg[SG_R_HCR_EL2], *vbar = &r->reg[SG_R_VBAR_EL1], *sctlr = &r->reg[SG_R_SCTLR_EL1];
    uint64_t bound = 2 * r->timebox_ticks;
    unsigned out;
    r->substage = SG_SUB_EL1_PREP;
#ifndef SP11_STICK_GIC_SEED_NO_NEXT_EL1
    gic_say_next_el1(g);
    if (!sw_gic_wait_seconds(g->hz, SW_GIC_WAIT_S)) return SG_OUT_COUNTER;
#else
    (void)gic_say_next_el1;
#endif
    sw_gic_begin_class(g, SG_CLASS_E);
    vbar->new = sp11hw_el1_vectors();
    sctlr->new = SW_GIC_EL1_SCTLR;
#ifdef SP11_STICK_GIC_SEED_NO_IMO
    hcr->new = SW_GIC_EL1_HCR & ~(uint64_t)SG_HCR_IMO;
#else
    hcr->new = SW_GIC_EL1_HCR;
#endif
    r->wrote |= 1u << SG_R_VBAR_EL1 | 1u << SG_R_SCTLR_EL1 | 1u << SG_R_HCR_EL2;
    sp11hw_write_vbar_el1(vbar->new);
    sp11hw_write_sctlr_el1(sctlr->new);
    sp11hw_write_hcr_el2(hcr->new);
    hcr->rb = sw_gic_current(g, SG_R_HCR_EL2);
    vbar->rb = sw_gic_current(g, SG_R_VBAR_EL1);
    sctlr->rb = sw_gic_current(g, SG_R_SCTLR_EL1);
    if ((hcr->rb & SG_HCR_CHECKED) != (hcr->new & SG_HCR_CHECKED) || (sctlr->rb & SG_SCTLR_CHECKED) != (sctlr->new & SG_SCTLR_CHECKED) ||
        vbar->rb != vbar->new)
        return SG_OUT_EL1_NOT_HELD;
#ifndef SP11_STICK_GIC_SEED_NO_IMO
    if (!(hcr->rb & SG_HCR_IMO)) return SG_OUT_EL1_NOT_HELD;       /* the interrupt would be taken by EL1's own table, not by EL2 */
#endif
    sp11hw_make_visible(vbar->new, sp11hw_el1_image_bytes());
    if ((out = gic_arm_timer(g, SG_WIN_EL1)) != 0) return out;
    r->resume_spsr = (r->irq[0].spsr & ~(uint64_t)0x3DF) | SW_GIC_EL2_MASKED;     /* the caller's PSTATE as window 1 saw it, EL2h, all masked */
    r->done = 0;
    r->substage = SG_SUB_EL1_WINDOW;
    sw_gic_flush(g);
#ifdef SP11_STICK_GIC_SEED_UNBOUNDED_EL1
    bound = ~(uint64_t)0 >> 2;
#endif
    sp11hw_el1_excursion(r, sp11hw_gic_vectors(), r->reg[SG_R_VBAR_EL2].old, r->resume_spsr, bound);
    r->substage = SG_SUB_EL1_DONE;
    return gic_classify_el1(g);
}

/* Window 1, and only if it passed, window 2: the first failure is the answer. */
static unsigned gic_dry_run(struct sw_gic *g)
{
    unsigned out = gic_el2_window(g);
    if (out == SG_OUT_IRQ_OK) out = gic_el1_window(g);
    return out;
}

/* ---- The put-back of E, V and T, in the reverse order of the stores; C, R and G are sw_gic_put_back's ---- */

static void gic_dry_restore(struct sw_gic *g)
{
    struct sw_gic_rec *r = &g->rec.r;
    r->substage = SG_SUB_RESTORING;
    sw_gic_flush(g);
#ifndef SP11_STICK_GIC_SEED_TIMER_ON
    if (r->begun & SG_CLASS_T) sp11hw_write_cnthp_ctl_el2(0);       /* a vector did when an interrupt came; with none, this is what stops it */
#endif
    if (r->begun & SG_CLASS_E) {
#ifndef SP11_STICK_GIC_SEED_NO_EL1_RESTORE
        sw_gic_put_back_reg(g, SG_R_HCR_EL2);                       /* first: the reverse of the order it was set in */
#endif
        sw_gic_put_back_reg(g, SG_R_SCTLR_EL1);
        sw_gic_put_back_reg(g, SG_R_VBAR_EL1);
    }
    if (r->begun & SG_CLASS_V) {
        sw_gic_put_back_reg(g, SG_R_VBAR_EL2);
        sw_gic_put_back_reg(g, SG_R_DAIF);
    }
    if (r->begun & SG_CLASS_T) {
        /* The deadline first, with the timer disabled (CTL = 0 above): a foreign enable put back over the deadline of the last window, which is in
         * the past, would assert the line until the next write. After both writes the timer is as it was found, and asserts again only if its own
         * deadline has passed during the run, as it would have without the stage. */
#ifdef SP11_STICK_GIC_SEED_RESTORE_CTL_FIRST
        sw_gic_put_back_reg(g, SG_R_CNTHP_CTL);
        sw_gic_put_back_reg(g, SG_R_CNTHP_CVAL);
#else
        sw_gic_put_back_reg(g, SG_R_CNTHP_CVAL);
        sw_gic_put_back_reg(g, SG_R_CNTHP_CTL);
#endif
    }
}

/* ---- The verdict and the page ---- */

static uint64_t gic_finish(struct sw_gic *g, unsigned first, unsigned put)
{
    struct sw_gic_rec *r = &g->rec.r;
    uint32_t out = put ? SG_OUT_NOT_RESTORED : first;
    r->first_outcome = first;
    r->outcome = out;
    r->substage = SG_SUB_DONE;
    sw_gic_flush(g);
    sw_gic_say(g, SG_L_EL1, 0);
    sw_gic_say(g, SG_L_DIAG1, 0);
    sw_gic_say(g, SG_L_RESTORE, 1);
    sw_gic_say(g, SG_L_VERDICT, 1);
    return SG_OUT_IS_VERDICT(out) ? 0 : sw_refuse(g->c, SP11SB_E_GIC_NOT_READY, out);
}

/* A refusal before any store: the reads are in the record, nothing was begun, and no put-back is needed. */
static uint64_t gic_refuse(struct sw_gic *g, unsigned outcome)
{
    g->rec.r.outcome = g->rec.r.first_outcome = outcome;
    sw_gic_flush(g);
    sw_gic_say(g, SG_L_VERDICT, 1);
    return sw_refuse(g->c, SP11SB_E_GIC_NOT_READY, outcome);
}

uint64_t sw_gic_stage(struct sw_ctx *c)
{
    struct sw_gic g;
    unsigned out, put;
    if ((out = sw_gic_arm_begin(&g, c)) != 0 || (out = gic_dry_precheck(&g)) != 0 || (out = sw_gic_arm_reads(&g)) != 0) return gic_refuse(&g, out);
    out = sw_gic_arm_stores(&g, SG_CLASS_T | SG_CLASS_V | SG_CLASS_E);
    if (SG_OUT_IS_REFUSAL(out) && !g.rec.r.begun) return gic_refuse(&g, out);
    if (!out) out = gic_dry_run(&g);
    gic_dry_restore(&g);
    put = sw_gic_put_back(&g);
#ifdef SP11_STICK_GIC_SEED_STRAY_STORE
    sp11hw_mmio_write32(g.rd + SG_GICR_WAKER, 0);
#endif
    return gic_finish(&g, out, put);
}

/* ---- The vector's C halves ---- */

/* The end of an interrupt: the acknowledge returned `iar`, the level is already low, and the EOI is written for any INTID below 1020, whose it is
 * (probes/xnuboot/async.c), with ICC_DIR_EL1 after it when EOImode is set (probes/xnuboot/clock_timer.c). */
static void gic_eoi(const struct sw_gic_rec *rec, uint64_t iar)
{
    (void)rec;
    if ((iar & 0xFFFFFF) >= SW_GIC_INTID_SPURIOUS) return;
    sp11hw_dsb_sy();
#ifndef SP11_STICK_GIC_SEED_NO_EOI
    sp11hw_write_icc_eoir1_el1(iar);
    if (rec->icc_ctlr_el1 & SG_ICC_CTLR_EOIMODE) sp11hw_write_icc_dir_el1(iar);
#endif
}

/* The first things of an entry, read before anything is changed: the interrupted state, ISTATUS and what the CPU interface would hand out. */
static void gic_irq_enter(struct sg_irq *q, uint64_t now, uint64_t elr, uint64_t spsr)
{
    q->t_irq = now;
    q->elr = elr;
    q->spsr = spsr;
    q->ctl = (uint32_t)sp11hw_read_cnthp_ctl_el2();
    q->hppir_vec = (uint32_t)(sp11hw_read_icc_hppir1_el1() & 0xFFFFFF);
    q->flags |= SG_IRQF_ENTERED;
}

/* A short wait on the counter before the PPI's state is read. Whether a level change (the re-arm, CNTHP_CTL = 0) has reached the GIC when the
 * instruction that made it completes is not architectural (INFERRED), and a read taken too early would report a pending interrupt that is
 * only the signal still on its way. Bounded by a read count as well, so that a counter that does not run cannot hold it. */
static void gic_settle(uint64_t hz)
{
    uint64_t start = sp11hw_ticks(), want = hz / SW_GIC_SETTLE_DIV;
    uint32_t n;
    sp11hw_dsb_sy();
    for (n = 0; n < SW_GIC_POLL_READS; n++)
        if (sp11hw_ticks() - start >= want) break;
}

/* The state of the PPI after the end of the interrupt, or after the timer was disabled for the deadline taken without an acknowledge. */
static void gic_irq_after(const struct sw_gic_rec *rec, struct sg_irq *q)
{
    uint64_t sgi = rec->gicr_base + SG_SGI_FRAME;
#ifndef SP11_STICK_GIC_SEED_NO_SETTLE
    gic_settle(rec->counter_hz);
#else
    (void)gic_settle;
#endif
    q->hppir_post = (uint32_t)(sp11hw_read_icc_hppir1_el1() & 0xFFFFFF);
    if (sp11hw_mmio_read32(sgi + SG_ISPENDR0) & (1u << rec->intid)) q->flags |= SG_IRQF_PEND;
    if (sp11hw_mmio_read32(sgi + SG_ISACTIVER0) & (1u << rec->intid)) q->flags |= SG_IRQF_ACT;
}

static void gic_timer_off(void)
{
#ifndef SP11_STICK_GIC_SEED_TIMER_ON
    sp11hw_write_cnthp_ctl_el2(0);
#endif
}

/* The interrupt of window 1, taken from EL2 at the current-EL vector. Cycles 0 to 2 are acknowledged, re-armed a short time ahead before the
 * EOI so that the level is low when the interrupt is deactivated (compat/fast-timer-irq.h), and left with IRQ unmasked for the next; the
 * fourth is the deadline taken as the monitor's stop path takes it: no read of ICC_IAR1_EL1, CNTHP_CTL = 0, and what is left is recorded. Any
 * acknowledge that is not the PPI, or more entries than the contract allows, silences the timer and ends the window. */
unsigned sw_gic_vec_irq(struct sw_gic_rec *rec, uint64_t elr, uint64_t spsr)
{
    struct sg_win *w = &rec->win[SG_WIN_EL2];
    uint64_t now = sp11hw_ticks(), iar;
    uint32_t k = rec->cycle;
    struct sg_irq *q = &rec->irq[k <= SG_IRQ_FINAL ? k : SG_IRQ_FINAL];
    if (w->irq_entries < 0xFFFF) w->irq_entries++;
    if (w->irq_entries > SW_GIC_ENTRY_LIMIT || k > SG_IRQ_FINAL) {
        gic_timer_off();
        rec->done = 1;
        return SW_VEC_MASK | SW_VEC_EXIT;
    }
    gic_irq_enter(q, now, elr, spsr);
    if (k >= SG_CYCLES) {
        q->flags |= SG_IRQF_FINAL;
#ifdef SP11_STICK_GIC_SEED_FINAL_ACK
        iar = sp11hw_read_icc_iar1_el1();
        q->iar = iar;
        q->flags |= SG_IRQF_ACKED;
        gic_eoi(rec, iar);
#endif
        gic_timer_off();
        gic_irq_after(rec, q);
        rec->cycle = k + 1;
        rec->done = 1;
#ifdef SP11_STICK_GIC_SEED_NO_IRQ_MASK
        return 0;
#else
        return SW_VEC_MASK | SW_VEC_EXIT;
#endif
    }
    iar = sp11hw_read_icc_iar1_el1();
    q->iar = iar;
    q->flags |= SG_IRQF_ACKED;
    if ((iar & 0xFFFFFF) != rec->intid) {
        gic_timer_off();
        gic_eoi(rec, iar);
        gic_irq_after(rec, q);
        rec->done = 1;
        return SW_VEC_MASK | SW_VEC_EXIT;
    }
#ifndef SP11_STICK_GIC_SEED_NO_REARM
    rec->irq[k + 1].cval = now + rec->cycle_ticks;
    sp11hw_write_cnthp_cval_el2(rec->irq[k + 1].cval);
#else
    rec->irq[k + 1].cval = now + rec->cycle_ticks;
#endif
    gic_eoi(rec, iar);
    gic_irq_after(rec, q);
    rec->cycle = k + 1;
    return 0;
}

/* The interrupt of window 2, taken from EL1 at the lower-EL vector: acknowledged once, the level dropped, ended, recorded, and the excursion
 * is over: the return is to the excursion's exit at EL2h, not to the stub. */
unsigned sw_gic_vec_irq_el1(struct sw_gic_rec *rec, uint64_t elr, uint64_t spsr)
{
    struct sg_win *w = &rec->win[SG_WIN_EL1];
    struct sg_irq *q = &rec->irq[SG_IRQ_EL1];
    uint64_t now = sp11hw_ticks(), iar;
    int first = !w->irq_entries;
    if (w->irq_entries < 0xFFFF) w->irq_entries++;
    if (first) {
        gic_irq_enter(q, now, elr, spsr);
        iar = sp11hw_read_icc_iar1_el1();
        q->iar = iar;
        q->flags |= SG_IRQF_ACKED;
        gic_timer_off();
        gic_eoi(rec, iar);
        gic_irq_after(rec, q);
    } else {
        gic_timer_off();
    }
    rec->done = 1;
    return SW_VEC_TO_EL2;
}

/* A synchronous entry from EL1: the stub's HVC #0 is its counter bound ending with no interrupt (the registers that say why are read before
 * the timer is disabled); HVC #1 is an EL1 vector reporting an exception of EL1 itself, whose own syndrome registers are read here; anything
 * else is recorded as it is. Each ends the excursion. */
unsigned sw_gic_vec_sync_el1(struct sw_gic_rec *rec, uint64_t esr, uint64_t elr, uint64_t spsr, uint64_t far, uint64_t el1_vector)
{
    struct sg_win *w = &rec->win[SG_WIN_EL1];
    uint64_t ec = esr >> 26 & 0x3F, imm = esr & 0xFFFF;
    if (ec == SG_EC_HVC64 && imm == 0 && !w->other_entries && !w->hvc_end) {
        w->hvc_end = 1;
        gic_diag_read(rec, SG_WIN_EL1, spsr);
    } else if (!w->other_entries) {
        w->other_entries = 1;
        w->exc_spsr = spsr;
        if (ec == SG_EC_HVC64 && imm == 1) {
            w->exc_vector = SG_EL1_VEC_BASE + el1_vector;
            w->exc_esr = sp11hw_read_esr_el1();
            w->exc_elr = sp11hw_read_elr_el1();
            w->exc_far = sp11hw_read_far_el1();
        } else {
            w->exc_vector = SW_VEC_EL1_SYNC_SLOT;
            w->exc_esr = esr;
            w->exc_elr = elr;
            w->exc_far = far;
        }
    } else if (w->other_entries < 0xFFFF) {
        w->other_entries++;
    }
    gic_timer_off();
    rec->done = 1;
    return SW_VEC_TO_EL2;
}

/* Any other entry: recorded in the window that is open, the timer stopped (once: a second entry means the first handler faulted, so it
 * touches nothing), and the window ended at its exit with IRQ masked. No retry: the instruction that raised it is not run again. */
unsigned sw_gic_vec_other(struct sw_gic_rec *rec, uint64_t vector, uint64_t esr, uint64_t elr, uint64_t far, uint64_t spsr)
{
    struct sg_win *w = &rec->win[rec->substage >= SG_SUB_EL1_PREP ? SG_WIN_EL1 : SG_WIN_EL2];
    if (!w->other_entries) {
        w->exc_vector = vector;
        w->exc_esr = esr;
        w->exc_elr = elr;
        w->exc_far = far;
        w->exc_spsr = spsr;
        w->other_entries = 1;
        gic_timer_off();
    } else if (w->other_entries < 0xFFFF) {
        w->other_entries++;
    }
    rec->done = 1;
    return SW_VEC_MASK | SW_VEC_EXIT;
}
#endif /* SNAP_SP11_STICK_GIC */
