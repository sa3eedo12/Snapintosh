/* SPDX-License-Identifier: MIT */
/* The arm and the put-back of the GIC and stop stage (docs/SP11-STICK-GIC.md; docs/SP11-MONITOR-PORT.md, work item 8): the half of
 * SNAPMON-GIC.BIN that the monitor entry (work item 4b) will call around the monitor. EXPERIMENTAL; hardware NOT RUN; compiled only
 * under SNAP_SP11_STICK_GIC=1. The dry run's own half, the timer, the windows and the verdict, is sp11-stick-gic-dry.c.
 *
 * Order. Reads only, then one decision, then stores.
 *   begin    CurrentEL, SPSel, DAIF, HCR_EL2.IMO and the pinned GIC facts, the counter and the page the record goes to.
 *   reads    the distributor (CTLR, TYPER, IIDR) and a bounded scan of the pinned redistributor frames for this CPU's affinity
 *            (GICR_TYPER, read-only); its WAKER and the PPI's state; ICC_SRE_EL2 and, only if SRE is set, the rest of the CPU
 *            interface. Each phase prints its line as soon as it is read, so a read that hangs is named by the last line. Any
 *            precondition that fails refuses with a named outcome and without a store. Then the survey, reads only and reporter-only:
 *            the SPIs' enable and pending registers of the distributor, the priorities of the other enabled PPIs and, in the dry run's
 *            build, the EL1 timers.
 *   announce one line, NEXT GIC STORES AFTER 8 S, and eight seconds on the counter, then the first store. A hang or a reset at a store
 *            leaves a page that was already filmed.
 *   stores   Q the quarantine, first: every enabled bit of this CPU's ISENABLER0 but PPI 26's is disabled by one write of ICENABLER0, so that
 *            no foreign interrupt can be signalled while the stage owns the CPU interface (boot 13, OBSERVED: INTID 27 was pending and enabled
 *            and ended the first window); G the distributor's group 1 enable, only when the decode says it is clear; R this CPU's
 *            redistributor, PPI 26 only (its enable only when found disabled); C the CPU interface. The class is marked begun in the
 *            record, and the record stored to REPORT.DAT, before its first store, and every register is read before it is changed and
 *            read back after: a store that does not hold is a named failure. Last, ICC_HPPIR1_EL1 is read with IRQ still masked: nothing
 *            but INTID 1023 or 26 may be next (FOREIGN_PENDING, an SPI is the case the quarantine does not cover). PREPARED is written
 *            when all of that held, not before.
 *   put back classes C, R and G in the reverse order, every register back to what was read, each verified; one more write for a
 *            register that did not hold and then a loud NOT_RESTORED; the quarantined enables last, after the CPU interface and the
 *            distributor are back, so that nothing foreign can be signalled in between.
 * Every wait is bounded by the counter, and but for the window's by a read count too (the eight seconds, which watches the counter
 * run; the RWP polls). No heap, no writable global, no libc, no recursion. Nothing here touches the ITS, another PPI's configuration, an SPI, another
 * CPU's redistributor beyond its read-only TYPER, the SMMU, PCIe, USB or the framebuffer (the quarantine disables, and later enables again, the other
 * PPIs of this CPU's redistributor and writes nothing else of theirs), and nothing here touches CNTHP, VBAR_EL2,
 * HCR_EL2 or an EL1 register unless the file is built with SW_GIC_DRY_REGS=1, the dry run's build. */
#include "sp11-stick-hw.h"

#if SNAP_SP11_STICK_GIC
#include "sp11-stick-gic.h"

_Static_assert(SNAP_TIMER_INTID >= 16 && SNAP_TIMER_INTID <= 31, "the stop timer is a PPI");

/* ---- The record and the page ---- */

void sw_gic_flush(struct sw_gic *g)
{
    uint64_t at = g->c->report_pa + SG_REC_AT;
    uint32_t i;
    for (i = 0; i < SG_REC_BYTES / 8; i++) sp11hw_store64(at + 8 * i, g->rec.w[i]);
    sp11hw_clean_range(at, SG_REC_BYTES);
    sp11hw_dsb_sy();
}

/* One line of the shared renderer (the reporter prints the same bytes with the same function), under the wrapper's prefix. */
void sw_gic_say(struct sw_gic *g, unsigned id, int essential)
{
    struct pl_line l;
    sw_begin(&l);
    if (sg_line(g->c->a, &l, id, (const uint8_t *)&g->rec.r)) sw_emit(g->c, &l, essential);
}

static void gic_say_next_stores(struct sw_gic *g)
{
    struct pl_line l;
    uint32_t p = g->rec.r.planned;
    sw_begin(&l);
    pl_text(&l, "NEXT GIC STORES AFTER ");
    sc_dec(&l, SW_GIC_WAIT_S);
    pl_text(&l, " S");
    pl_text(&l, (p & SG_CLASS_G) ? " G=1" : " G=0");
    pl_text(&l, (p & SG_CLASS_R) ? " R=1" : " R=0");
    pl_text(&l, (p & SG_CLASS_C) ? " C=1" : " C=0");
    pl_text(&l, (p & SG_CLASS_T) ? " T=1" : " T=0");
    pl_text(&l, (p & SG_CLASS_V) ? " V=1" : " V=0");
    pl_text(&l, (p & SG_CLASS_E) ? " E=1" : " E=0");
    sw_emit(g->c, &l, 1);
}

/* ---- Bounded waits ---- */

/* `seconds` on the counter; 0 when the counter does not run or runs backwards, so that a counter that lies cannot end the wait at once
 * (the photograph window is not given up for it) and cannot hold the stage forever either. */
int sw_gic_wait_seconds(uint64_t hz, unsigned seconds)
{
    uint64_t start = sp11hw_ticks(), last = start;
    uint32_t same = 0;
    for (;;) {
        uint64_t now = sp11hw_ticks();
        if (now < start) return 0;
        if (now - start >= hz * seconds) return 1;
        if (now == last) {
            if (++same >= SW_GIC_COUNTER_STUCK_READS) return 0;
        } else {
            last = now;
            same = 0;
        }
    }
}

/* A register bit that must clear (RWP): at most SW_GIC_POLL_READS reads and at most 50 ms. 1 when it cleared. */
static int gic_wait_clear32(const struct sw_gic *g, uint64_t pa, uint32_t mask)
{
    uint64_t start = sp11hw_ticks(), limit = g->hz / 20;
    uint32_t n;
    for (n = 0; n < SW_GIC_POLL_READS; n++) {
        if (!(sp11hw_mmio_read32(pa) & mask)) return 1;
        if (sp11hw_ticks() - start > limit) break;
    }
    return !(sp11hw_mmio_read32(pa) & mask);
}

void sw_gic_sync(void)
{
    sp11hw_dsb_sy();
    sp11hw_isb();
}

/* ---- The table of registers: read now, write for a put-back, compare ---- */

uint64_t sw_gic_current(const struct sw_gic *g, unsigned id)
{
    switch (id) {
    case SG_R_GICD_CTLR: return sp11hw_mmio_read32(g->gicd + SG_GICD_CTLR);
    case SG_R_IGROUPR0: return sp11hw_mmio_read32(g->sgi + SG_IGROUPR0);
    case SG_R_ISENABLER0: return sp11hw_mmio_read32(g->sgi + SG_ISENABLER0);
    case SG_R_ISPENDR0: return sp11hw_mmio_read32(g->sgi + SG_ISPENDR0);
    case SG_R_ISACTIVER0: return sp11hw_mmio_read32(g->sgi + SG_ISACTIVER0);
    case SG_R_IPRIORITYR: return sp11hw_mmio_read8(g->sgi + SG_IPRIORITYR + g->intid);
    case SG_R_ICFGR1: return sp11hw_mmio_read32(g->sgi + SG_ICFGR1);
    case SG_R_ICC_PMR: return sp11hw_read_icc_pmr_el1();
    case SG_R_ICC_BPR1: return sp11hw_read_icc_bpr1_el1();
    case SG_R_ICC_IGRPEN1: return sp11hw_read_icc_igrpen1_el1();
    case SG_R_ICC_RPR: return sp11hw_read_icc_rpr_el1();
#if SW_GIC_DRY_REGS
    case SG_R_CNTHP_CTL: return sp11hw_read_cnthp_ctl_el2();
    case SG_R_CNTHP_CVAL: return sp11hw_read_cnthp_cval_el2();
    case SG_R_VBAR_EL2: return sp11hw_read_vbar_el2();
    case SG_R_DAIF: return sp11hw_read_daif();
    case SG_R_HCR_EL2: return sp11hw_read_hcr_el2();
    case SG_R_VBAR_EL1: return sp11hw_read_vbar_el1();
    case SG_R_SCTLR_EL1: return sp11hw_read_sctlr_el1();
#endif
    default: return 0;
    }
}

/* The bits of a register that are the stage's to compare: not RWP, not another interrupt's bit, not the read-only ISTATUS. */
static uint64_t gic_mask(const struct sw_gic *g, unsigned id)
{
    switch (id) {
    case SG_R_GICD_CTLR: return ~(uint64_t)SG_GICD_CTLR_RWP & 0xFFFFFFFFu;
    case SG_R_IGROUPR0: case SG_R_ISENABLER0: return 0xFFFFFFFFu;
    case SG_R_ISPENDR0: case SG_R_ISACTIVER0: return g->bit;
    case SG_R_IPRIORITYR: case SG_R_ICC_PMR: case SG_R_ICC_BPR1: case SG_R_ICC_RPR: return 0xFF;
    case SG_R_ICFGR1: return (uint64_t)3 << g->shift;
    case SG_R_ICC_IGRPEN1: return 1;
    case SG_R_CNTHP_CTL: return SG_CNTHP_CTL_MASK;
    case SG_R_DAIF: return SG_DAIF_MASKED;
    default: return ~(uint64_t)0;
    }
}

/* One write of a put-back: the register goes back to `old`. ISENABLER0 goes back in two steps (`final` says which): PPI 26's own enable first, by the clear
 * register and only its bit (the stage writes the enable only when PPI 26 was found disabled, so that bit is the only one it can have set), ahead of the
 * configuration registers; and last the bits the quarantine cleared, by the set register and only those. Every other bit is as the firmware left it and is
 * never written. */
static void gic_restore_write(struct sw_gic *g, unsigned id, uint64_t old, uint64_t cur, int final)
{
    switch (id) {
    case SG_R_GICD_CTLR:
        sp11hw_mmio_write32(g->gicd + SG_GICD_CTLR, (uint32_t)old);
        sw_gic_sync();
        (void)gic_wait_clear32(g, g->gicd + SG_GICD_CTLR, SG_GICD_CTLR_RWP);
        break;
    case SG_R_IGROUPR0: sp11hw_mmio_write32(g->sgi + SG_IGROUPR0, (uint32_t)old); break;
    case SG_R_ISENABLER0: {
        uint32_t own = final ? 0 : (uint32_t)(cur & g->bit & ~old), back = final ? (uint32_t)(g->rec.r.sv.quarantined & old & ~cur) : 0;
        if (own) {
            sp11hw_mmio_write32(g->sgi + SG_ICENABLER0, own);
            sw_gic_sync();
            (void)gic_wait_clear32(g, g->rd + SG_GICR_CTLR, SG_GICR_CTLR_RWP);
        }
        if (back) {
            sp11hw_mmio_write32(g->sgi + SG_ISENABLER0, back);
            sw_gic_sync();
            (void)gic_wait_clear32(g, g->rd + SG_GICR_CTLR, SG_GICR_CTLR_RWP);
        }
        break;
    }
    case SG_R_ISPENDR0: sp11hw_mmio_write32(g->sgi + SG_ICPENDR0, (uint32_t)(cur & g->bit & ~old)); break;
    case SG_R_IPRIORITYR: sp11hw_mmio_write8(g->sgi + SG_IPRIORITYR + g->intid, (uint8_t)old); break;
    case SG_R_ICFGR1: {
        uint64_t field = (uint64_t)3 << g->shift;
        sp11hw_mmio_write32(g->sgi + SG_ICFGR1, (uint32_t)((cur & ~field) | (old & field)));
        break;
    }
    case SG_R_ICC_PMR: sp11hw_write_icc_pmr_el1(old); break;
    case SG_R_ICC_BPR1: sp11hw_write_icc_bpr1_el1(old); break;
    case SG_R_ICC_IGRPEN1: sp11hw_write_icc_igrpen1_el1(old); break;
#if SW_GIC_DRY_REGS
    case SG_R_CNTHP_CTL: sp11hw_write_cnthp_ctl_el2(old & SG_CNTHP_CTL_MASK); break;
    case SG_R_CNTHP_CVAL: sp11hw_write_cnthp_cval_el2(old); break;
    case SG_R_VBAR_EL2: sp11hw_write_vbar_el2(old); break;
    case SG_R_DAIF: sp11hw_irq_mask(); break;
    case SG_R_HCR_EL2: sp11hw_write_hcr_el2(old); break;
    case SG_R_VBAR_EL1: sp11hw_write_vbar_el1(old); break;
    case SG_R_SCTLR_EL1: sp11hw_write_sctlr_el1(old); break;
#endif
    default: break;                     /* ISACTIVER0 and ICC_RPR are verified, never written: an interrupt left active is NOT_RESTORED */
    }
    sw_gic_sync();
}

/* Puts the bits of `mask` of one register back: if they are not as found and the stage wrote the register, one write, a read-back, and one more write if
 * that did not hold. Only the last put-back of a register (`final`) records the value it ends with and a failure; an earlier one, PPI 26's own enable ahead
 * of the configuration registers, can only need its retry noted. A register the stage never wrote is only read, so that an unrelated change shows as
 * NOT_RESTORED and is not overwritten. */
static void gic_put_back_bits(struct sw_gic *g, unsigned id, uint64_t mask, int final)
{
    struct sw_gic_rec *r = &g->rec.r;
    struct sg_reg *e = &r->reg[id];
    uint64_t cur = sw_gic_current(g, id);
    if ((cur & mask) != (e->old & mask) && (r->wrote >> id & 1)) {
        gic_restore_write(g, id, e->old, cur, final);
        cur = sw_gic_current(g, id);
        if ((cur & mask) != (e->old & mask)) {
            r->restore_retried |= 1u << id;
            gic_restore_write(g, id, e->old, cur, final);
            cur = sw_gic_current(g, id);
        }
    }
    if (!final) return;
    e->fin = cur;
    if ((cur & mask) != (e->old & mask)) r->restore_failed |= 1u << id;
}

void sw_gic_put_back_reg(struct sw_gic *g, unsigned id) { gic_put_back_bits(g, id, gic_mask(g, id), 1); }

/* ---- Reads and the decision ---- */

/* The pins the stage uses are the ones the build was compared with (the five macros) and a geometry the stage can walk: a PIN.DAT that
 * changed after the earlier stages read it is refused here, before a frame is touched. */
static int gic_pin_ok(const uint8_t *pin)
{
    uint64_t gd = SP11SB_PIN_U64(pin, gicd_base), gf = SP11SB_PIN_U64(pin, gicr_first), gl = SP11SB_PIN_U64(pin, gicr_limit);
    uint64_t gs = SP11SB_PIN_U64(pin, gicr_stride), n = SP11SB_PIN_U32(pin, gicr_count);
    if (gd != SNAP_GICD_BASE || gf != SNAP_GICR_FIRST || gl != SNAP_GICR_LIMIT || gs != SNAP_GICR_STRIDE ||
        SP11SB_PIN_U32(pin, timer_intid) != SNAP_TIMER_INTID)
        return 0;
    if (!n || n > SP11SB_GICR_MAX || gs < 2 * SG_SGI_FRAME || (gs & 0xFFFF) || (gd & 0xFFFF) || (gf & 0xFFFF)) return 0;
    return gl == gf + n * gs;
}

static void gic_init(struct sw_gic *g, struct sw_ctx *c)
{
    uint32_t i;
    g->c = c;
    for (i = 0; i < SG_REC_BYTES / 8; i++) g->rec.w[i] = 0;
    for (i = 0; i < 8; i++) g->rec.r.magic[i] = (uint8_t)SG_REC_MAGIC[i];
    g->rec.r.version = SG_REC_VERSION;
    g->rec.r.size = SG_REC_BYTES;
    g->gicd = g->rd = g->sgi = 0;
    g->ds = 0;
    g->intid = SNAP_TIMER_INTID;
    g->bit = 1u << g->intid;
    g->shift = 2 * (g->intid - 16);
    g->hz = c->head.r.counter_hz;
    g->rec.r.intid = g->intid;
    g->rec.r.counter_hz = g->hz;
    g->rec.r.substage = SG_SUB_READS;
}

/* The checks that read no GIC register: the CPU state the windows need, the pins and the page the record goes to. */
static unsigned gic_check_cpu(struct sw_gic *g)
{
    struct sw_gic_rec *r = &g->rec.r;
    struct sw_probe probe;
    if (sp11hw_read_currentel() != 8) return SG_OUT_NOT_EL2;
    if (sp11hw_read_spsel() != 1) return SG_OUT_SPSEL_NOT_1;
    r->reg[SG_R_DAIF].old = sp11hw_read_daif();
    if ((r->reg[SG_R_DAIF].old & SG_DAIF_MASKED) != SG_DAIF_MASKED) return SG_OUT_DAIF_NOT_MASKED;
    if (!(g->c->head.r.hcr_el2 & SG_HCR_IMO)) return SG_OUT_IMO_CLEAR;
    if (!gic_pin_ok(g->c->pin)) return SG_OUT_PIN_GIC;
    if (g->hz < 1000000u || g->hz > 1000000000u) return SG_OUT_COUNTER;
    r->timebox_ticks = g->hz * SP11_STICK_GIC_TIME_BOX_S;       /* from here the products cannot overflow: the record check bounds all three */
    r->slack_ticks = g->hz * SP11_STICK_GIC_SLACK_US / 1000000u;
    r->cycle_ticks = g->hz * SP11_STICK_GIC_CYCLE_US / 1000000u;
    if (sw_probe_range(g->c->report_pa + SG_REC_AT, SG_REC_BYTES, 1, &probe)) return SG_OUT_WINDOW_UNWRITABLE;
    g->gicd = SP11SB_PIN_U64(g->c->pin, gicd_base);
    r->gicd_base = g->gicd;
    return 0;
}

static unsigned gic_read_distributor(struct sw_gic *g)
{
    struct sw_gic_rec *r = &g->rec.r;
    uint32_t ctlr = sp11hw_mmio_read32(g->gicd + SG_GICD_CTLR);
    r->reg[SG_R_GICD_CTLR].old = ctlr;
    r->gicd_typer = sp11hw_mmio_read32(g->gicd + SG_GICD_TYPER);
    r->gicd_iidr = sp11hw_mmio_read32(g->gicd + SG_GICD_IIDR);
    g->ds = (ctlr & SG_GICD_CTLR_DS) != 0;
    if (!(ctlr & SG_GICD_CTLR_G1A)) r->planned |= SG_CLASS_G;
    sw_gic_say(g, SG_L_GICD, 0);
    /* RWP set, an unknown bit, or affinity routing off: not a view this stage knows how to read, so it does not guess one. */
    return (ctlr & ~SG_GICD_CTLR_KNOWN) || !(ctlr & SG_GICD_CTLR_ARE) ? SG_OUT_GICD_VIEW : 0;
}

/* The bounded scan of the pinned frames for this CPU's affinity. Only GICR_TYPER of a frame is read before it matches; once it does,
 * every other access is to that frame. */
static unsigned gic_read_redistributor(struct sw_gic *g)
{
    struct sw_gic_rec *r = &g->rec.r;
    const uint8_t *pin = g->c->pin;
    uint64_t first = SP11SB_PIN_U64(pin, gicr_first), stride = SP11SB_PIN_U64(pin, gicr_stride), mpidr = sp11hw_read_mpidr();
    uint64_t affinity = ((mpidr >> 32) & 0xFF) << 24 | (mpidr & 0xFFFFFF), typer = 0, typer0 = 0;
    uint32_t i, n = SP11SB_PIN_U32(pin, gicr_count);
    for (i = 0; i < n; i++) {
        typer = sp11hw_mmio_read64(first + i * stride + SG_GICR_TYPER);
        if (!i) typer0 = typer;
        if (typer >> 32 == affinity) break;
    }
    if (i == n) {
        r->gicr_base = first;
        r->gicr_typer = typer0;
        r->gicr_index = n;
        r->outcome = SG_OUT_GICR_NOT_FOUND;     /* the renderer prints this line differently once it knows: both pages then say the same */
        sw_gic_say(g, SG_L_GICR, 0);
        return SG_OUT_GICR_NOT_FOUND;
    }
    g->rd = first + i * stride;
    g->sgi = g->rd + SG_SGI_FRAME;
    r->gicr_base = g->rd;
    r->gicr_index = i;
    r->gicr_typer = typer;
    r->gicr_waker = sp11hw_mmio_read32(g->rd + SG_GICR_WAKER);
    r->reg[SG_R_ISENABLER0].old = sw_gic_current(g, SG_R_ISENABLER0);
    r->reg[SG_R_ISPENDR0].old = sw_gic_current(g, SG_R_ISPENDR0);
    r->reg[SG_R_ISACTIVER0].old = sw_gic_current(g, SG_R_ISACTIVER0);
    r->reg[SG_R_IGROUPR0].old = sw_gic_current(g, SG_R_IGROUPR0);
    r->reg[SG_R_IPRIORITYR].old = sw_gic_current(g, SG_R_IPRIORITYR);
    r->reg[SG_R_ICFGR1].old = sw_gic_current(g, SG_R_ICFGR1);
    sw_gic_say(g, SG_L_GICR, 0);
    if (r->gicr_waker & (SG_GICR_WAKER_SLEEP | SG_GICR_WAKER_CHILDREN_ASLEEP)) return SG_OUT_GICR_ASLEEP;
    /* Only PPI 26 itself pending or active is refused: someone else's interrupt is in flight on the line the windows use. Other PPIs enabled in
     * ISENABLER0 are accepted here and quarantined by the first store (gic_store_q), and PPI 26 itself enabled is accepted and left exactly as found (boot 12,
     * OBSERVED, one unit: the firmware leaves SGI 0 and PPIs 23, 25, 26, 27 and 30 enabled; boot 13: INTID 27, one of them, was pending). What the quarantine
     * does not cover (an enabled SPI) is refused after the stores by gic_check_pending, and the windows still end on any INTID but 26 and name it (WRONG_INTID,
     * HPPIR_NOT_26, NOT_CLEAN, FINAL_PENDING); the reporter's GIC OLD line shows ISPENDR0 and ISACTIVER0 whole. */
    if ((r->reg[SG_R_ISPENDR0].old | r->reg[SG_R_ISACTIVER0].old) & g->bit) return SG_OUT_PPI_OWNED;
    return 0;
}

/* The CPU interface is read only when ICC_SRE_EL2.SRE says the system register interface is on: with it off these reads are UNDEFINED. The
 * dry run's build also reads VBAR_EL2 and the timer. It refuses the timer only when it is asserting right now (ENABLE, not IMASK, ISTATUS: an
 * interrupt is firing); the PPI check above normally sees that first, as PPI 26 pending, and this one is for a view in which the pending bit is
 * not visible (INFERRED: an interrupt that is Secure reads as zero). Any other state, enabled and masked, enabled with its deadline ahead, or
 * disabled, is accepted: nothing else runs at EL2 and IRQ is masked, the stage arms CNTHP itself, overwriting the deadline (CVAL, then CTL), and
 * puts the saved CTL and CVAL back at the end (CVAL first, with the timer disabled, so that a past deadline cannot assert the line). */
static unsigned gic_read_cpu_and_timer(struct sw_gic *g)
{
    struct sw_gic_rec *r = &g->rec.r;
    r->icc_sre_el2 = sp11hw_read_icc_sre_el2();
    if (!(r->icc_sre_el2 & SG_ICC_SRE_SRE)) {
        sw_gic_say(g, SG_L_CPU, 0);
        return SG_OUT_SRE_CLEAR;
    }
    r->icc_ctlr_el1 = sp11hw_read_icc_ctlr_el1();
    r->reg[SG_R_ICC_PMR].old = sp11hw_read_icc_pmr_el1();
    r->reg[SG_R_ICC_BPR1].old = sp11hw_read_icc_bpr1_el1();
    r->reg[SG_R_ICC_IGRPEN1].old = sp11hw_read_icc_igrpen1_el1();
    r->reg[SG_R_ICC_RPR].old = sp11hw_read_icc_rpr_el1();
#if SW_GIC_DRY_REGS
    r->reg[SG_R_VBAR_EL2].old = sp11hw_read_vbar_el2();
    r->reg[SG_R_CNTHP_CTL].old = sp11hw_read_cnthp_ctl_el2();
    r->reg[SG_R_CNTHP_CVAL].old = sp11hw_read_cnthp_cval_el2();
#endif
    sw_gic_say(g, SG_L_CPU, 0);
#if SW_GIC_DRY_REGS
    return (r->reg[SG_R_CNTHP_CTL].old & SG_CNTHP_CTL_STATE) == SG_CNTHP_CTL_FIRING ? SG_OUT_TIMER_OWNED : 0;
#else
    return 0;
#endif
}

unsigned sw_gic_arm_begin(struct sw_gic *g, struct sw_ctx *c)
{
    gic_init(g, c);
    return gic_check_cpu(g);
}

static uint32_t gic_popcount(uint32_t v)
{
    uint32_t n = 0;
    for (; v; v &= v - 1) n++;          /* at most 32 turns */
    return n;
}

/* The survey, after every read that a decision rests on and before any store; it decides nothing, and the reporter alone prints it. The SPIs: GICD_ISENABLER[n]
 * and GICD_ISPENDR[n] for n = 1 to the ITLinesNumber of GICD_TYPER, a field of five bits, so that the loop is at most 31 turns and never reads past offset
 * 0x17C and 0x27C of a frame the PIN.DAT sizes at 64 KiB; register 0 is banked in each redistributor under affinity routing and is not read here. The
 * counts are of INTIDs; the first two that are enabled and pending are named, in INTID order. The other PPIs: the priority byte of each INTID the firmware
 * left enabled besides PPI 26 (boot 12, OBSERVED), in this CPU's own frame. The EL1 timers, in the dry run's build only (the monitor entry's arm half is built
 * with SW_GIC_DRY_REGS=0 and must not gain a system register read): the register behind INTID 27 is INFERRED to be CNTV_CTL_EL0 until these are read. */
static void gic_read_survey(struct sw_gic *g)
{
    struct sg_survey *s = &g->rec.r.sv;
    uint32_t lines = g->rec.r.gicd_typer & SG_GICD_TYPER_ITLINES, n, i;
    s->spi_lines = lines;
    for (n = 1; n <= lines; n++) {
        uint32_t en = sp11hw_mmio_read32(g->gicd + SG_GICD_ISENABLER + 4u * n), pend = sp11hw_mmio_read32(g->gicd + SG_GICD_ISPENDR + 4u * n), both = en & pend;
        s->spi_enabled += gic_popcount(en);
        s->spi_pending += gic_popcount(pend);
        s->spi_both += gic_popcount(both);
        for (i = 0; both && i < 32; i++) {
            if (!(both >> i & 1)) continue;
            if (!s->spi_first) s->spi_first = 32u * n + i;
            else if (!s->spi_second) s->spi_second = 32u * n + i;
        }
    }
    s->flags |= SG_SV_SPI;
#define SG_PPI_PRIO(intid) ((uint32_t)sp11hw_mmio_read8(g->sgi + SG_IPRIORITYR + (intid)))
    s->ppi_prio = SG_PPI_PRIO(SG_SURVEY_PPI0) | SG_PPI_PRIO(SG_SURVEY_PPI1) << 8 | SG_PPI_PRIO(SG_SURVEY_PPI2) << 16 | SG_PPI_PRIO(SG_SURVEY_PPI3) << 24;
#undef SG_PPI_PRIO
    s->flags |= SG_SV_PPI;
#if SW_GIC_DRY_REGS
    s->cntv_ctl = sp11hw_read_cntv_ctl_el0();
    s->cntv_cval = sp11hw_read_cntv_cval_el0();
    s->cntp_ctl = sp11hw_read_cntp_ctl_el0();
    s->cntp_cval = sp11hw_read_cntp_cval_el0();
    s->flags |= SG_SV_TIMERS;
#endif
}

unsigned sw_gic_arm_reads(struct sw_gic *g)
{
    unsigned out;
    if ((out = gic_read_distributor(g)) != 0 || (out = gic_read_redistributor(g)) != 0 || (out = gic_read_cpu_and_timer(g)) != 0) {
        g->rec.r.outcome = g->rec.r.first_outcome = out;
        sw_gic_flush(g);
        return out;
    }
    gic_read_survey(g);
    return 0;
}

/* ---- Stores. The class is marked begun, and the record stored, before its first store: a hang names the class in RAM. ---- */

void sw_gic_begin_class(struct sw_gic *g, uint32_t cls)
{
    g->rec.r.begun |= cls;
    sw_gic_flush(g);
}

/* G: the distributor's group 1 enable, the one distributor-wide store. In the non-secure view (DS = 0) the write sets both group 1
 * bits as q1n1's gicv3.c does on the A16; in the single security state's view bit 0 is group 0's enable, which is left alone. */
static unsigned gic_store_g(struct sw_gic *g)
{
    struct sg_reg *e = &g->rec.r.reg[SG_R_GICD_CTLR];
    int rwp;
    sw_gic_begin_class(g, SG_CLASS_G);
#ifdef SP11_STICK_GIC_SEED_NO_SAVE
    e->old = 0;
#endif
    e->new = (uint32_t)e->old | SG_GICD_CTLR_G1A | (g->ds ? 0 : SG_GICD_CTLR_G1);
    g->rec.r.wrote |= 1u << SG_R_GICD_CTLR;
    sp11hw_mmio_write32(g->gicd + SG_GICD_CTLR, (uint32_t)e->new);
    sw_gic_sync();
    rwp = gic_wait_clear32(g, g->gicd + SG_GICD_CTLR, SG_GICD_CTLR_RWP);
    e->rb = sw_gic_current(g, SG_R_GICD_CTLR);
    if (!rwp) return SG_OUT_G_NOT_HELD;     /* a write that does not complete is not a write that held */
    return (e->rb & (SG_GICD_CTLR_ARE | SG_GICD_CTLR_G1A)) == (SG_GICD_CTLR_ARE | SG_GICD_CTLR_G1A) ? 0 : SG_OUT_G_NOT_HELD;
}

/* Q, the first store, a member of class R: this CPU's other enabled interrupts are disabled, and stay so until the put-back's last step. Boot 13 (OBSERVED, one
 * unit, one boot): INTID 27 was pending and enabled before any store and, with group 1 enabled at the distributor and the CPU interface, was the first interrupt
 * of window 1; the monitor stops on any interrupt that is not PPI 26 (probes/xnuboot/async.c), so the same one would end its run at the first IRQ unmask.
 * ICENABLER0 takes the mask of the bits to disable: every bit the reads found enabled but PPI 26's (SGI 0 and PPIs 23, 25, 27 and 30 on the unit), in one write,
 * then the RWP wait and a read of ISENABLER0 that must show exactly PPI 26's bit as found. The pending state of those interrupts is never written: a source that
 * stays asserting stays pending and is simply not forwarded. With nothing to disable there is no store, and nothing begun. */
static unsigned gic_store_q(struct sw_gic *g)
{
    struct sw_gic_rec *r = &g->rec.r;
    struct sg_survey *s = &r->sv;
    uint32_t mask = (uint32_t)r->reg[SG_R_ISENABLER0].old & ~g->bit;
    int rwp;
#ifdef SP11_STICK_GIC_SEED_QUARANTINE_BIT26
    mask = (uint32_t)r->reg[SG_R_ISENABLER0].old;       /* a quarantine that takes PPI 26's own enable with the rest */
#endif
#ifdef SP11_STICK_GIC_SEED_NO_QUARANTINE
    mask = 0;                           /* the stage that ran in boot 13 */
#endif
    if (!mask) return 0;
    sw_gic_begin_class(g, SG_CLASS_R);
    s->quarantined = mask;
    r->wrote |= 1u << SG_R_ISENABLER0;
    sp11hw_mmio_write32(g->sgi + SG_ICENABLER0, mask);
#ifdef SP11_STICK_GIC_SEED_QUARANTINE_STRAY_WRITE
    sp11hw_mmio_write32(g->rd + SP11SB_PIN_U64(g->c->pin, gicr_stride) + SG_SGI_FRAME + SG_ICENABLER0, mask);       /* the next CPU's redistributor frame */
#endif
    sw_gic_sync();
    rwp = gic_wait_clear32(g, g->rd + SG_GICR_CTLR, SG_GICR_CTLR_RWP);
    s->quar_rb = (uint32_t)sw_gic_current(g, SG_R_ISENABLER0);
    s->flags |= SG_SV_QUAR;
    return rwp && s->quar_rb == ((uint32_t)r->reg[SG_R_ISENABLER0].old & g->bit) ? 0 : SG_OUT_Q_NOT_HELD;
}

/* R: this CPU's PPI. The group is writable only in the single security state's view (DS = 1); with two security states EL3's firmware
 * owns it and a non-secure write is ignored, so none is made and the enable's read-back says whether the PPI is the OS's to enable. */
static unsigned gic_store_r(struct sw_gic *g)
{
    struct sw_gic_rec *r = &g->rec.r;
    struct sg_reg *grp = &r->reg[SG_R_IGROUPR0], *pri = &r->reg[SG_R_IPRIORITYR], *cfg = &r->reg[SG_R_ICFGR1], *en = &r->reg[SG_R_ISENABLER0];
    uint64_t edge = (uint64_t)SG_ICFGR_EDGE << g->shift;
    int rwp;
    sw_gic_begin_class(g, SG_CLASS_R);
    if (g->ds && !(grp->old & g->bit)) {
        grp->new = grp->old | g->bit;
        r->wrote |= 1u << SG_R_IGROUPR0;
        sp11hw_mmio_write32(g->sgi + SG_IGROUPR0, (uint32_t)grp->new);
    }
    if (pri->old != SW_GIC_PRIORITY) {
        pri->new = SW_GIC_PRIORITY;
        r->wrote |= 1u << SG_R_IPRIORITYR;
        sp11hw_mmio_write8(g->sgi + SG_IPRIORITYR + g->intid, (uint8_t)pri->new);
    }
    if (cfg->old & edge) {              /* the GTDT says level; an edge field is made level, or the read-back below refuses */
        cfg->new = cfg->old & ~edge;
        r->wrote |= 1u << SG_R_ICFGR1;
        sp11hw_mmio_write32(g->sgi + SG_ICFGR1, (uint32_t)cfg->new);
    }
    if (!(en->old & g->bit)) {          /* PPI 26's enable is written only when it was found disabled, and no other bit of ISENABLER0 is ever set by the stage */
        en->new = g->bit;
        r->wrote |= 1u << SG_R_ISENABLER0;
        sp11hw_mmio_write32(g->sgi + SG_ISENABLER0, (uint32_t)en->new);
    }
    sw_gic_sync();
    rwp = gic_wait_clear32(g, g->rd + SG_GICR_CTLR, SG_GICR_CTLR_RWP);
    en->rb = sw_gic_current(g, SG_R_ISENABLER0);
    pri->rb = sw_gic_current(g, SG_R_IPRIORITYR);
    cfg->rb = sw_gic_current(g, SG_R_ICFGR1);
    grp->rb = sw_gic_current(g, SG_R_IGROUPR0);
    /* PPI 26's bit is set, and the register is exactly what the quarantine and this store leave: PPI 26's bit as found or written, and nothing else
     * that the quarantine disabled. */
    if (!rwp || !(en->rb & g->bit) || (uint32_t)en->rb != (((uint32_t)en->old & ~r->sv.quarantined) | g->bit) || (cfg->rb & edge) ||
        (g->ds && !(grp->rb & g->bit)))
        return SG_OUT_R_NOT_HELD;
    return 0;
}

/* C: the CPU interface, enabled last. BPR1 is not read back against the value written: its minimum is the implementation's. What has to
 * hold is group 1 enabled and the PPI's priority below the mask, both as the non-secure view reads them. */
static unsigned gic_store_c(struct sw_gic *g)
{
    struct sw_gic_rec *r = &g->rec.r;
    sw_gic_begin_class(g, SG_CLASS_C);
    r->reg[SG_R_ICC_PMR].new = SW_GIC_PMR;
    r->reg[SG_R_ICC_BPR1].new = SW_GIC_BPR1;
    r->reg[SG_R_ICC_IGRPEN1].new = 1;
    r->wrote |= 1u << SG_R_ICC_PMR | 1u << SG_R_ICC_BPR1 | 1u << SG_R_ICC_IGRPEN1;
    sp11hw_write_icc_pmr_el1(SW_GIC_PMR);
    sp11hw_write_icc_bpr1_el1(SW_GIC_BPR1);
    sp11hw_write_icc_igrpen1_el1(1);
    r->reg[SG_R_ICC_PMR].rb = sw_gic_current(g, SG_R_ICC_PMR);
    r->reg[SG_R_ICC_BPR1].rb = sw_gic_current(g, SG_R_ICC_BPR1);
    r->reg[SG_R_ICC_IGRPEN1].rb = sw_gic_current(g, SG_R_ICC_IGRPEN1);
    if (!(r->reg[SG_R_ICC_IGRPEN1].rb & 1)) return SG_OUT_C_NOT_HELD;
    return (r->reg[SG_R_IPRIORITYR].rb & 0xFF) < (r->reg[SG_R_ICC_PMR].rb & 0xFF) ? 0 : SG_OUT_PRIO_NOT_BELOW_PMR;
}

/* The last check of the arm, with IRQ still masked and so without a side effect (ICC_HPPIR1_EL1, unlike ICC_IAR1_EL1, acknowledges nothing): what the CPU
 * interface would hand out next is nothing (1023) or PPI 26, which the timer has not armed yet. Anything else is a foreign interrupt that the quarantine does
 * not cover (an SPI, or a PPI that did not disable), and a window or a monitor would take it first. Inside the arm so that the monitor entry refuses here
 * too, and does not call a monitor that would stop at its first IRQ unmask. */
static unsigned gic_check_pending(struct sw_gic *g)
{
    struct sg_survey *s = &g->rec.r.sv;
    uint32_t id;
#ifdef SP11_STICK_GIC_SEED_NO_QUARANTINE
    return 0;                           /* the stage that ran in boot 13 had no such check */
#endif
#ifdef SP11_STICK_GIC_SEED_NO_HPPIR_CHECK
    return 0;
#endif
    sw_gic_sync();
    id = (uint32_t)(sp11hw_read_icc_hppir1_el1() & 0xFFFFFF);
    s->hppir_pre = id;
    s->flags |= SG_SV_HPPIR;
    return id == SG_INTID_NONE || id == g->intid ? 0 : SG_OUT_FOREIGN_PENDING;
}

unsigned sw_gic_arm_stores(struct sw_gic *g, uint32_t extra)
{
    unsigned out = 0;
    g->rec.r.planned |= SG_CLASS_R | SG_CLASS_C | extra;
    g->rec.r.substage = SG_SUB_PRE_OK;
    sw_gic_flush(g);
#ifdef SP11_STICK_GIC_SEED_STORE_FIRST
    sp11hw_write_icc_pmr_el1(SW_GIC_PMR);
#endif
#ifndef SP11_STICK_GIC_SEED_NO_NEXT
    gic_say_next_stores(g);
    if (!sw_gic_wait_seconds(g->hz, SW_GIC_WAIT_S)) {
        g->rec.r.outcome = g->rec.r.first_outcome = SG_OUT_COUNTER;
        sw_gic_flush(g);
        return SG_OUT_COUNTER;
    }
#else
    (void)gic_say_next_stores;
    (void)sw_gic_wait_seconds;
#endif
    g->rec.r.substage = SG_SUB_STORING;
#ifndef SP11_STICK_GIC_SEED_QUARANTINE_AFTER_G
    out = gic_store_q(g);               /* first: nothing foreign may be signalled once group 1 is enabled */
#endif
    if (!out && (g->rec.r.planned & SG_CLASS_G)) out = gic_store_g(g);
#ifdef SP11_STICK_GIC_SEED_QUARANTINE_AFTER_G
    if (!out) out = gic_store_q(g);
#endif
    if (!out) out = gic_store_r(g);
    if (!out) out = gic_store_c(g);
    if (!out) out = gic_check_pending(g);
    if (out) {
        g->rec.r.first_outcome = out;
        if (out == SG_OUT_Q_NOT_HELD || out == SG_OUT_FOREIGN_PENDING) sw_gic_say(g, SG_L_QUAR, 1);     /* the INTID, or what the quarantine left, is on the page */
        return out;
    }
    g->rec.r.substage = SG_SUB_ARMED;
    sw_set_stage(g->c, SP11SB_STAGE_PREPARED);
    sw_gic_flush(g);
    return 0;
}

unsigned sw_gic_arm(struct sw_gic *g, struct sw_ctx *c, uint32_t extra)
{
    unsigned out;
    if ((out = sw_gic_arm_begin(g, c)) != 0) {
        g->rec.r.outcome = g->rec.r.first_outcome = out;
        sw_gic_flush(g);
        return out;
    }
    if ((out = sw_gic_arm_reads(g)) != 0) return out;
    return sw_gic_arm_stores(g, extra);
}

/* ---- The put-back of classes C, R and G, and the quarantine's last ---- */

unsigned sw_gic_put_back(struct sw_gic *g)
{
    struct sw_gic_rec *r = &g->rec.r;
    r->substage = SG_SUB_RESTORING;
    sw_gic_flush(g);
    if (r->begun & SG_CLASS_C) {
        sw_gic_put_back_reg(g, SG_R_ICC_IGRPEN1);
        sw_gic_put_back_reg(g, SG_R_ICC_BPR1);
        sw_gic_put_back_reg(g, SG_R_ICC_PMR);
    }
    if (r->begun & SG_CLASS_R) {
        if (sw_gic_current(g, SG_R_ISPENDR0) & g->bit & ~r->reg[SG_R_ISPENDR0].old) r->wrote |= 1u << SG_R_ISPENDR0;
        gic_put_back_bits(g, SG_R_ISENABLER0, g->bit, 0);       /* PPI 26's own enable first, ahead of the configuration registers (a configuration is not changed under an enabled interrupt) */
        sw_gic_put_back_reg(g, SG_R_ISPENDR0);
        sw_gic_put_back_reg(g, SG_R_IPRIORITYR);
        sw_gic_put_back_reg(g, SG_R_ICFGR1);
        sw_gic_put_back_reg(g, SG_R_IGROUPR0);
        sw_gic_put_back_reg(g, SG_R_ISACTIVER0);
    }
#ifndef SP11_STICK_GIC_SEED_NO_RESTORE
    if (r->begun & SG_CLASS_G) sw_gic_put_back_reg(g, SG_R_GICD_CTLR);
#endif
    if (r->begun) sw_gic_put_back_reg(g, SG_R_ICC_RPR);
#ifndef SP11_STICK_GIC_SEED_QUARANTINE_NO_PUTBACK
    /* Last: the enables the quarantine cleared go back after the CPU interface and the distributor are, so that nothing foreign can be signalled in between,
     * and ISENABLER0 is verified whole against what the reads found. */
    if (r->begun & SG_CLASS_R) sw_gic_put_back_reg(g, SG_R_ISENABLER0);
#endif
    return r->restore_failed ? SG_OUT_NOT_RESTORED : 0;
}
#endif /* SNAP_SP11_STICK_GIC */
