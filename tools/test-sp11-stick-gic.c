/* SPDX-License-Identifier: MIT */
/* Host harness for the GIC and stop stage (docs/SP11-STICK-GIC.md, section 9), driven by tools/test-sp11-stick-gic.py. It is the harness of the
 * dry-run wrapper, tools/test-sp11-stick-wrapper.c, INCLUDED (five of its names redirected, nothing edited), with a model of the machine
 * the stage talks to added: the distributor, one redistributor frame (this CPU's: every other frame answers GICR_TYPER and nothing else),
 * the CPU interface, the EL2 physical timer (CNTHP) with a level-triggered PPI 26, PSTATE.I with VBAR_EL2, and for the EL1 excursion HCR_EL2,
 * VBAR_EL1, SCTLR_EL1, CNTHCTL_EL2 and the routing of the interrupt by HCR_EL2.IMO. The code under test is the REAL wrapper, stage and reporter
 * sources compiled with -DSP11_STICK_HOST_TEST, with -fsanitize=address,undefined; what is modelled is the machine, and the routines written in
 * assembly on the unit, the two windows (sp11-stick-gic-vec.c), which the model replaces by loops that unmask IRQ or enter the stub, advance
 * the counter, and call the real C halves of the vectors where the machine would take the exception. So what the model cannot prove is the
 * assembly (the QEMU test and the disassembly checks do) and the unit's GIC; what it does prove is the stage's order, its bounds, its
 * put-back and its report against the model.
 *
 * Scenario lines are the base harness's plus, before RUN:
 *   GIC CTLR v | TYPER v | IIDR v | RTYPER v (the low word of every frame's GICR_TYPER) | FRAMES n | AFF idx v | WAKER v | IGROUPR0 v | ISENABLER0 v |
 *   ISPENDR0 v | ISACTIVER0 v | PRIO v |
 *   CFG1 v | SRE v | ICCCTLR v | PMR v | BPR1 v | IGRPEN1 v | RPR v | CTL v | CVAL v | DAIF v | SPSEL v | VBAR v | CURRENTEL_LATE v |
 *   NO_FIRE | NO_DELIVER | INTID v | SYNC_AT ticks | STORM | STORM_ONCE | STORM_AFTER ticks | FINAL_STUCK | DROP_AFTER n | HPPIR v | NO_ISTATUS | DELAY ticks | NS_PPI 0|1 |
 *   HIDE_PEND (the non-secure view of ISPENDR0 never shows the timer's level, as for an interrupt that is Secure: INFERRED) |
 *   GICD_WMASK v (the bits of GICD_CTLR a non-secure write changes) | FPRIO intid v (IPRIORITYR byte of another PPI) | FGROUP 0|1 (another interrupt is group 1 in
 *   the non-secure view) | SPI_EN n v | SPI_PEND n v (GICD_ISENABLER[n], GICD_ISPENDR[n], n = 1 to 31) | SPI_PRIO v | CNTV_CTL v | CNTV_CVAL v | CNTP_CTL v | CNTP_CVAL v |
 * Another interrupt is asserting when its bit is set in ISPENDR0 (this CPU's PPIs: a level source that nothing the stage writes can clear) or in an SPI_PEND
 * register; it is signalled when it is also enabled, in group 1, below PMR and the running priority and the CPU interface is on, and the lowest priority value
 * wins (ties to the lowest INTID), exactly as for PPI 26. |
 *   IGNORE reg | NOHOLD reg [windows] (windows 0: from the start of the put-back, whatever ran before it, so that the put-back of a refused arm can fail) |
 *   RWP gicd|gicr (the bit stays set) | RWP_LAG gicr n (after a write of ISENABLER0 or ICENABLER0 the bit reads set for n reads of GICR_CTLR, and the write is not
 *   visible until it clears) | ICE_HOLD v (only the bits of v clear in ICENABLER0: a partial hold) | HPPIR_IGNORES_PMR (ICC_HPPIR1_EL1 does not apply PMR) |
 *   HPPIR_EXTRA intid prio (another pending interrupt of any INTID: a special one, an LPI's) | SPI_LATE n v ticks (GICD_ISPENDR[n] gains bits v that many ticks
 *   into the run) | MAXITER n | CNTHCTL v | HCR_INIT v | VBAR1 v | SCTLR1 v | HCR_IGNORE_IMO |
 *   EL1_LOST | EL1_VECTOR n | EL1_SYNC esr | EL1_INTID v | SPSR_KEPT v | MUTATE offset hexbytes (between two RUNs: bytes of the record)
 * At END the harness prints GICREC (the record in REPORT.DAT), GICLOG (every write, every system register read and every window event, with
 * the number of page lines and the counter at that point), GICSTATE and GICVIOL. Hardware: NOT RUN; nothing here touches a device. */
#define _DARWIN_C_SOURCE
#include <ctype.h>
#include <setjmp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifdef __APPLE__
#include <pthread.h>
#endif

static char *gic_fgets(char *buf, int n, FILE *stream);
#define main wrapper_harness_main
#define fgets gic_fgets
#define sp11hw_ticks base_ticks
#define sp11hw_read_currentel base_read_currentel
#define sp11hw_read_hcr_el2 base_read_hcr_el2
#include "test-sp11-stick-wrapper.c"
#undef main
#undef fgets
#undef sp11hw_ticks
#undef sp11hw_read_currentel
#undef sp11hw_read_hcr_el2

#include "../probes/sp11-stick-gic.h"

/* ---- the model ---- */
enum gic_reg {
    GR_GICD_CTLR, GR_GICD_TYPER, GR_GICD_IIDR, GR_GICR_CTLR, GR_GICR_TYPER, GR_GICR_WAKER, GR_IGROUPR0, GR_ISENABLER0, GR_ICENABLER0, GR_ISPENDR0,
    GR_ICPENDR0, GR_ISACTIVER0, GR_IPRIORITYR, GR_ICFGR1, GR_ICC_SRE, GR_ICC_CTLR, GR_ICC_PMR, GR_ICC_BPR1, GR_ICC_IGRPEN1, GR_ICC_RPR,
    GR_ICC_HPPIR, GR_ICC_IAR, GR_ICC_EOIR, GR_ICC_DIR, GR_CNTHP_CTL, GR_CNTHP_CVAL, GR_VBAR, GR_DAIF, GR_SPSEL, GR_HCR, GR_VBAR1, GR_SCTLR1,
    GR_CNTHCTL, GR_ESR1, GR_ELR1, GR_FAR1, GR_CNTV_CTL, GR_CNTV_CVAL, GR_CNTP_CTL, GR_CNTP_CVAL, GR_GICD_ISENABLER, GR_GICD_ISPENDR, GR_COUNT
};
static const char *const gr_names[GR_COUNT] = {
    "GICD_CTLR", "GICD_TYPER", "GICD_IIDR", "GICR_CTLR", "GICR_TYPER", "GICR_WAKER", "GICR_IGROUPR0", "GICR_ISENABLER0", "GICR_ICENABLER0",
    "GICR_ISPENDR0", "GICR_ICPENDR0", "GICR_ISACTIVER0", "GICR_IPRIORITYR", "GICR_ICFGR1", "ICC_SRE_EL2", "ICC_CTLR_EL1", "ICC_PMR_EL1",
    "ICC_BPR1_EL1", "ICC_IGRPEN1_EL1", "ICC_RPR_EL1", "ICC_HPPIR1_EL1", "ICC_IAR1_EL1", "ICC_EOIR1_EL1", "ICC_DIR_EL1", "CNTHP_CTL_EL2",
    "CNTHP_CVAL_EL2", "VBAR_EL2", "DAIF", "SPSEL", "HCR_EL2", "VBAR_EL1", "SCTLR_EL1", "CNTHCTL_EL2", "ESR_EL1", "ELR_EL1", "FAR_EL1",
    "CNTV_CTL_EL0", "CNTV_CVAL_EL0", "CNTP_CTL_EL0", "CNTP_CVAL_EL0", "GICD_ISENABLER", "GICD_ISPENDR"};
enum { EV_W = 'W', EV_R = 'R', EV_E = 'E', EV_V = 'V' };
enum { E_WINDOW_OPEN, E_WINDOW_CLOSE, E_IRQ, E_SYNC, E_ACK, E_EXC_OPEN, E_EXC_CLOSE, E_IRQ_EL1, E_SYNC_EL1, E_VISIBLE, E_NAMES };
static const char *const event_names[E_NAMES] = {"WINDOW_OPEN", "WINDOW_CLOSE", "IRQ_TAKEN", "SYNC_TAKEN", "ACK", "EXC_OPEN", "EXC_CLOSE", "IRQ_EL1",
                                                 "SYNC_EL1", "VISIBLE"};
struct gic_event {
    int kind, reg;
    uint64_t value, tick;
    unsigned lines;
};
#define GIC_MAX_EVENTS 40000
#define GIC_MAX_FRAMES 32
#define GIC_MAX_VIOL 16
#define HCR_IMO 0x10u
#define HCR_TGE (1u << 27)
#define HCR_RW (1u << 31)

static struct {
    /* configuration: what a scenario sets, and the initial state of the machine */
    uint32_t gicd_ctlr0, gicd_typer, gicd_iidr, waker0, igroupr0_0, isenabler0_0, ispendr0_0, isactiver0_0, icfgr1_0, rtyper_lo;
    uint32_t nframes, prio0;
    uint64_t aff[GIC_MAX_FRAMES];
    uint64_t sre, icc_ctlr, pmr0, bpr1_0, igrpen1_0, rpr0, ctl0, cval0, daif0, spsel, vbar0, currentel_late;
    uint64_t cnthctl0, vbar1_0, sctlr1_0, hcr0, spsr_kept;
    uint64_t cntv_ctl, cntv_cval, cntp_ctl, cntp_cval;
    uint32_t gicd_wmask, spi_en[32], spi_pend[32], spi_prio, fprio[32];
    uint32_t ice_hold, spi_late_val;
    uint64_t spi_late_at;
    unsigned extra_intid, extra_prio, spi_late_n;
    int fgroup_ok, rwp_lag, hppir_ignores_pmr, have_extra;
    int have_currentel_late, no_fire, no_deliver, storm, storm_once, ns_ppi, have_intid, have_sync, final_stuck, have_storm_after, hcr_ignore_imo;
    int el1_lost, have_el1_vector, have_el1_sync, have_el1_intid, have_drop, have_hppir, no_istatus, hide_pend;
    unsigned drop_after;
    uint64_t hppir_override;
    uint64_t intid_override, sync_at, delay, maxiter, storm_after, el1_vector, el1_sync_esr, el1_intid;
    int ignore[GR_COUNT], nohold[GR_COUNT];
    unsigned nohold_from[GR_COUNT];
    unsigned rwp_gicd, rwp_gicr;
    /* the live machine */
    uint32_t gicd_ctlr, waker, igroupr0, isenabler0, icfgr1, prio, rwp_left_gicd, rwp_left_gicr;
    uint64_t pmr, bpr1, igrpen1, rpr, ctl, cval, vbar, daif, hcr, vbar1, sctlr1;
    int active, ack_other, eoi_seen, ctl_off_after_eoi, i_masked, phase, hang, deliveries, currentel_reads, hcr_init, in_el1, final_off, visible_ok;
    unsigned f_active;                  /* 0, or the INTID plus one of another interrupt that has been acknowledged and not yet deactivated */
    int en_pending, en_pending_set;     /* a write of an enable register that RWP_LAG has not let through yet (one at a time: the hardware completes writes in order) */
    uint32_t en_pending_value;
    unsigned acks, excursions;
    uint64_t t_arm, fake_vectors, fake_el1_vectors, first_eoi_tick, visible_start, visible_bytes, resume_spsr_seen, hcr_first;
    /* the logs */
    struct gic_event ev[GIC_MAX_EVENTS];
    unsigned nev, nviol;
    char viol[GIC_MAX_VIOL][96];
    unsigned line_seen;
    uint64_t line_tick[256];
} G;

static void gic_reset(void)
{
    unsigned i;
    memset(&G, 0, sizeof G);
    G.gicd_ctlr0 = 0x10;
    G.gicd_typer = 0x7F;
    G.gicd_iidr = 0x0001143B;
    G.rtyper_lo = 1;
    G.nframes = 12;
    for (i = 0; i < GIC_MAX_FRAMES; i++) G.aff[i] = i;
    G.sre = 0xF;
    G.pmr0 = 0xFF;
    G.rpr0 = 0xFF;
    G.daif0 = 0x3C0;
    G.spsel = 1;
    G.vbar0 = 0xCE879000;
    G.ns_ppi = 1;
    G.gicd_wmask = SG_GICD_CTLR_G1 | SG_GICD_CTLR_G1A | SG_GICD_CTLR_ARE | SG_GICD_CTLR_E1NWF;
    G.fgroup_ok = 1;
    G.ice_hold = 0xFFFFFFFFu;
    G.spi_prio = 0xA0;
    for (i = 0; i < 32; i++) G.fprio[i] = 0xA0;
    G.maxiter = 5000000;
    G.fake_vectors = 0xFEED0800;
    G.fake_el1_vectors = 0xCE0E0800;    /* inside the model's stack region: mapped and identity-mapped, 2 KiB aligned, room for the table and the stub */
    G.cnthctl0 = 3;
    G.vbar1_0 = 0x7B000000;
    G.sctlr1_0 = 0x30D00980;
    for (i = 0; i < GR_COUNT; i++) G.nohold_from[i] = 1;
    G.nohold_from[GR_HCR] = G.nohold_from[GR_VBAR1] = G.nohold_from[GR_SCTLR1] = 2;     /* these hold during the excursion's own writes */
}

static void gic_reset_live(void)
{
    G.gicd_ctlr = G.gicd_ctlr0;
    G.waker = G.waker0;
    G.igroupr0 = G.igroupr0_0;
    G.isenabler0 = G.isenabler0_0;
    G.icfgr1 = G.icfgr1_0;
    G.prio = G.prio0;
    G.pmr = G.pmr0;
    G.bpr1 = G.bpr1_0;
    G.igrpen1 = G.igrpen1_0;
    G.rpr = G.rpr0;
    G.ctl = G.ctl0 & 3;
    G.cval = G.cval0;
    G.vbar = G.vbar0;
    G.daif = G.daif0;
    G.vbar1 = G.vbar1_0;
    G.sctlr1 = G.sctlr1_0;
    G.hcr_init = 0;
    G.i_masked = (G.daif0 & 0x80) != 0;
    G.en_pending = 0;
}

static void log_ev(int kind, int reg, uint64_t value)
{
    if (G.nev >= GIC_MAX_EVENTS) return;
    G.ev[G.nev++] = (struct gic_event){kind, reg, value, W.tick, W.nlines};
}

static void violation(const char *what, uint64_t pa)
{
    if (G.nviol < GIC_MAX_VIOL) snprintf(G.viol[G.nviol], sizeof G.viol[0], "%s %llx", what, (unsigned long long)pa);
    G.nviol++;
    log_ev(EV_V, 0, pa);
}

/* The counter, with the tick of every page line recorded the first time the counter is read after it was printed: the stage reads the
 * counter first thing after its NEXT line, so that is the line's own tick to within one step. */
uint64_t sp11hw_ticks(void)
{
    uint64_t t = base_ticks();
    while (G.line_seen < W.nlines && G.line_seen < 256) G.line_tick[G.line_seen++] = t;
    return t;
}

/* CurrentEL: the base's value, or another one from the second read on, which is how a stage that reads it again can be refused. */
uint64_t sp11hw_read_currentel(void)
{
    uint64_t v = base_read_currentel();
    return ++G.currentel_reads > 1 && G.have_currentel_late ? G.currentel_late : v;
}

/* HCR_EL2: the base's register until the stage writes it, then the live value (the read still goes through the base so that its log is whole). */
uint64_t sp11hw_read_hcr_el2(void)
{
    uint64_t v = base_read_hcr_el2();
    if (!G.hcr_init) {
        G.hcr = G.hcr0 ? G.hcr0 : v;
        G.hcr_first = G.hcr;
        G.hcr_init = 1;
    }
    return G.hcr;
}

/* ---- the timer, the PPI line and the CPU interface ---- */
/* A source that is high after the end of interrupt: STORM stays high whatever the timer's enable says, so that the stage sees a second
 * interrupt that it cannot take (IRQ is masked in the return state) and cannot clear (a level interrupt's pending state is the line);
 * STORM_ONCE stays high until the timer is disabled once more after the end of interrupt; STORM_AFTER goes high that many ticks after the
 * first end of interrupt and stays; FINAL_STUCK goes high when the timer is disabled after the third acknowledged interrupt, which is the
 * deadline the stage takes without an acknowledge, and stays. */
static int stuck(void)
{
    if (G.eoi_seen && (G.storm || (G.storm_once && !G.ctl_off_after_eoi))) return 1;
    if (G.have_storm_after && G.eoi_seen && W.tick >= G.first_eoi_tick + G.storm_after) return 1;
    return G.final_stuck && G.final_off;
}
static int timer_asserted(void)
{
    if (stuck()) return 1;
    return !G.no_fire && (G.ctl & 1) && !(G.ctl & 2) && W.tick >= G.cval + G.delay;
}
static unsigned prio_bit(void) { return 1u << SNAP_TIMER_INTID; }
static int group_ok(void) { return (G.gicd_ctlr & SG_GICD_CTLR_DS) ? (G.igroupr0 & prio_bit()) != 0 : G.ns_ppi; }
/* PPI 26 itself: asserting, enabled, in group 1 and not active, with the distributor and the CPU interface on. */
static int eligible(void)
{
    return timer_asserted() && !G.active && (G.isenabler0 & prio_bit()) && group_ok() && (G.gicd_ctlr & SG_GICD_CTLR_ARE) &&
           (G.gicd_ctlr & SG_GICD_CTLR_G1A) && (G.igrpen1 & 1) && !G.no_deliver && !(G.have_drop && G.acks >= G.drop_after);
}
/* The other interrupts: the same switches. This CPU's PPIs assert while their bit is set in ISPENDR0 (a level source: nothing the stage writes
 * clears it), the SPIs while it is set in SPI_PEND. */
static int others_ready(void)
{
    return G.fgroup_ok && (G.gicd_ctlr & SG_GICD_CTLR_ARE) && (G.gicd_ctlr & SG_GICD_CTLR_G1A) && (G.igrpen1 & 1);
}
/* The interrupt the CPU interface would hand out next, mask aside: the lowest priority value, ties to the lowest INTID. 1023 and priority 0x100 when none. */
static uint32_t spi_pending(unsigned n)
{
    return G.spi_pend[n] | (G.spi_late_val && n == G.spi_late_n && W.tick >= G.spi_late_at ? G.spi_late_val : 0);
}
static unsigned best_pending(unsigned *prio)
{
    unsigned id = 1023, best = 0x100, i, n;
    if (eligible()) {
        id = SNAP_TIMER_INTID;
        best = G.prio & 0xFF;
    }
    if (others_ready()) {
        for (i = 0; i < 32; i++) {
            if (i == SNAP_TIMER_INTID || !(G.ispendr0_0 >> i & 1) || !(G.isenabler0 >> i & 1) || G.f_active == i + 1) continue;
            if (G.fprio[i] < best || (G.fprio[i] == best && i < id)) {
                id = i;
                best = G.fprio[i];
            }
        }
        for (n = 1; n < 32; n++) {
            uint32_t both = spi_pending(n) & G.spi_en[n];
            for (i = 0; i < 32 && both; i++) {
                unsigned intid = 32 * n + i;
                if (!(both >> i & 1) || G.f_active == intid + 1) continue;
                if (G.spi_prio < best || (G.spi_prio == best && intid < id)) {
                    id = intid;
                    best = G.spi_prio;
                }
            }
        }
        if (G.have_extra && G.f_active != G.extra_intid + 1 && (G.extra_prio < best || (G.extra_prio == best && G.extra_intid < id))) {
            id = G.extra_intid;         /* a source the stage cannot quarantine: no enable bit of this CPU's, a special INTID, an LPI's */
            best = G.extra_prio;
        }
    }
    *prio = best;
    return id;
}
static int signalled(void)
{
    unsigned prio, id = best_pending(&prio);
    return id != 1023 && prio < (G.pmr & 0xFF) && prio < (G.rpr & 0xFF);
}
/* ICC_HPPIR1_EL1: the interrupt that would be handed out next, or 1023 when none; with PMR applied unless HPPIR_IGNORES_PMR (whether the unit's does is UNKNOWN). */
static uint64_t hppir1_value(void)
{
    unsigned prio, id = best_pending(&prio);
    if (id == 1023 || (!G.hppir_ignores_pmr && prio >= (G.pmr & 0xFF))) return 1023;
    return id == SNAP_TIMER_INTID && G.have_hppir ? G.hppir_override : id;
}
static void deactivate(uint64_t intid)
{
    if (G.f_active && intid == (uint64_t)G.f_active - 1) G.f_active = 0;
    else G.active = 0;
}

/* ---- the memory-mapped registers ---- */
static int mine(unsigned frame) { return G.aff[frame] == 0; }
static int in_window(uint64_t pa, uint64_t base, uint64_t size) { return pa >= base && pa < base + size; }

/* Which frame and offset an address names: 1 and the frame, the offset from RD_base, when it is inside the pinned window. */
static int locate(uint64_t pa, unsigned *frame, uint64_t *off)
{
    uint64_t rel;
    if (pa < SNAP_GICR_FIRST || pa >= SNAP_GICR_LIMIT) return 0;
    rel = pa - SNAP_GICR_FIRST;
    *frame = (unsigned)(rel / SNAP_GICR_STRIDE);
    *off = rel % SNAP_GICR_STRIDE;
    return *frame < G.nframes;
}

static uint32_t rwp_read(unsigned *left, uint32_t bit)
{
    if (*left) {
        (*left)--;
        return bit;
    }
    return 0;
}

/* The enable registers of this CPU's redistributor. A write is visible at once, unless RWP_LAG says it takes effect only when RWP has cleared (the
 * architecture's RWP: the write is still pending): the write is then held until a read of GICR_CTLR finds RWP clear, and an earlier one is let through first,
 * as the hardware completes writes in order. A stage that reads ISENABLER0 back without waiting for RWP sees the old value. */
static void enable_apply(int set, uint32_t value)
{
    if (set) {
        if (G.ns_ppi || (G.gicd_ctlr & SG_GICD_CTLR_DS)) G.isenabler0 |= value;
    } else {
        G.isenabler0 &= ~value;
    }
}
static void enable_flush(void)
{
    if (G.en_pending) enable_apply(G.en_pending_set, G.en_pending_value);
    G.en_pending = 0;
}
static void enable_write(int set, uint32_t value)
{
    enable_flush();
    if (G.rwp_lag) {
        G.en_pending = 1;
        G.en_pending_set = set;
        G.en_pending_value = value;
    } else {
        enable_apply(set, value);
    }
    G.rwp_left_gicr = G.rwp_gicr;
}

static uint64_t mmio_read(uint64_t pa, unsigned size)
{
    unsigned frame;
    uint64_t off;
    if (in_window(pa, SNAP_GICD_BASE, 0x10000)) {
        off = pa - SNAP_GICD_BASE;
        if (off == SG_GICD_CTLR) {
            log_ev(EV_R, GR_GICD_CTLR, 0);
            return G.gicd_ctlr | rwp_read(&G.rwp_left_gicd, SG_GICD_CTLR_RWP);
        }
        if (off == SG_GICD_TYPER) return G.gicd_typer;
        if (off == SG_GICD_IIDR) return G.gicd_iidr;
        if ((off >= SG_GICD_ISENABLER && off < SG_GICD_ISENABLER + 0x80) || (off >= SG_GICD_ISPENDR && off < SG_GICD_ISPENDR + 0x80)) {
            int pend = off >= SG_GICD_ISPENDR;
            unsigned n = (unsigned)((off - (pend ? SG_GICD_ISPENDR : SG_GICD_ISENABLER)) / 4);
            if ((off & 3) || !n) {              /* register 0 is banked in each redistributor under affinity routing: the stage must not read the distributor's */
                violation(pend ? "GICD_ISPENDR[0] or an unaligned read" : "GICD_ISENABLER[0] or an unaligned read", pa);
                return 0;
            }
            log_ev(EV_R, pend ? GR_GICD_ISPENDR : GR_GICD_ISENABLER, n);
            return pend ? spi_pending(n) : G.spi_en[n];
        }
        violation("distributor read", pa);
        return 0;
    }
    if (!locate(pa, &frame, &off)) {
        violation("MMIO read outside the GIC frames", pa);
        return 0;
    }
    if (off == SG_GICR_TYPER && size == 8) return (G.aff[frame] << 32) | (frame + 1 == G.nframes ? SG_GICR_TYPER_LAST : 0) | G.rtyper_lo;
    if (!mine(frame)) {
        violation("read of a redistributor that is not this CPU's", pa);
        return 0;
    }
    if (off == SG_GICR_CTLR) {
        uint32_t v = rwp_read(&G.rwp_left_gicr, SG_GICR_CTLR_RWP);
        if (!v) enable_flush();         /* RWP is clear: a write of an enable register has gone through */
        return v;
    }
    if (off == SG_GICR_WAKER) return G.waker;
    if (off >= SG_SGI_FRAME) {
        off -= SG_SGI_FRAME;
        if (off == SG_IGROUPR0) return G.igroupr0;
        if (off == SG_ISENABLER0) return G.isenabler0;
        if (off == SG_ISPENDR0) return ((timer_asserted() && !G.hide_pend) ? prio_bit() : 0) | G.ispendr0_0;
        if (off == SG_ISACTIVER0) return (G.active ? prio_bit() : 0) | (G.f_active && G.f_active <= 32 ? 1u << (G.f_active - 1) : 0) | G.isactiver0_0;
        if (off == SG_ICFGR1) return G.icfgr1;
        if (off >= SG_IPRIORITYR && off < SG_IPRIORITYR + 32) return off - SG_IPRIORITYR == SNAP_TIMER_INTID ? G.prio : G.fprio[off - SG_IPRIORITYR];
    }
    violation("read of an unmodelled redistributor register", pa);
    return 0;
}

uint8_t sp11hw_mmio_read8(uint64_t pa) { return (uint8_t)mmio_read(pa, 1); }
uint32_t sp11hw_mmio_read32(uint64_t pa) { return (uint32_t)mmio_read(pa, 4); }
uint64_t sp11hw_mmio_read64(uint64_t pa) { return mmio_read(pa, 8); }

/* The stage's put-back has begun: the record's sub-stage in REPORT.DAT, as the CPU stored it, says RESTORING (the dry run's own put-back sets it too). Read from
 * the record so that it holds for a refused arm as well, in which no window ever closed. */
static int putting_back(void)
{
    struct region *rep = region_named("report");
    uint32_t sub;
    if (!rep || rep->size < SG_REC_AT + SG_REC_BYTES) return 0;
    memcpy(&sub, rep->cpu + SG_REC_AT + offsetof(struct sw_gic_rec, substage), sizeof sub);
    return sub == SG_SUB_RESTORING;
}
/* NOHOLD from window 1 on (or 2 for the EL1 registers), or from the start of the put-back when the scenario says 0. */
static int write_ignored(int reg)
{
    if (G.ignore[reg]) return 1;
    if (!G.nohold[reg]) return 0;
    return G.nohold_from[reg] ? (unsigned)G.phase >= G.nohold_from[reg] : putting_back();
}

static void mmio_write(uint64_t pa, uint64_t value, unsigned size)
{
    unsigned frame;
    uint64_t off;
    int reg = -1;
    (void)size;
    if (in_window(pa, SNAP_GICD_BASE, 0x10000)) {
        if (pa - SNAP_GICD_BASE != SG_GICD_CTLR) {
            violation("distributor write", pa);
            return;
        }
        log_ev(EV_W, GR_GICD_CTLR, value);
        if (!write_ignored(GR_GICD_CTLR)) {
            /* DS and RWP are read only; the writable bits are G.gicd_wmask (the enables, ARE and E1NWF of either view unless a scenario says otherwise). */
            G.gicd_ctlr = (G.gicd_ctlr & (SG_GICD_CTLR_DS)) | ((uint32_t)value & G.gicd_wmask);
            G.rwp_left_gicd = G.rwp_gicd;
        }
        return;
    }
    if (!locate(pa, &frame, &off) || !mine(frame) || off < SG_SGI_FRAME) {
        violation("MMIO write that is not to this CPU's SGI/PPI frame or the distributor's control register", pa);
        log_ev(EV_W, GR_COUNT, value);
        return;
    }
    off -= SG_SGI_FRAME;
    if (off == SG_IGROUPR0) reg = GR_IGROUPR0;
    else if (off == SG_ISENABLER0) reg = GR_ISENABLER0;
    else if (off == SG_ICENABLER0) reg = GR_ICENABLER0;
    else if (off == SG_ICPENDR0) reg = GR_ICPENDR0;
    else if (off == SG_ICFGR1) reg = GR_ICFGR1;
    else if (off == SG_IPRIORITYR + SNAP_TIMER_INTID) reg = GR_IPRIORITYR;
    if (reg < 0) {
        violation("write to an unmodelled or unauthorised redistributor register", pa);
        log_ev(EV_W, GR_COUNT, value);
        return;
    }
    log_ev(EV_W, reg, value);
    if (write_ignored(reg)) return;
    switch (reg) {
    case GR_IGROUPR0: if (G.gicd_ctlr & SG_GICD_CTLR_DS) G.igroupr0 = (uint32_t)value; break;     /* RAZ/WI to the non-secure view when DS = 0 */
    case GR_ISENABLER0: enable_write(1, (uint32_t)value); break;
    case GR_ICENABLER0: enable_write(0, (uint32_t)value & G.ice_hold); break;
    case GR_ICFGR1: G.icfgr1 = (uint32_t)value; break;
    case GR_IPRIORITYR: G.prio = (uint8_t)value; break;
    default: break;                                                                                 /* ICPENDR0: the line is the pending state */
    }
}
void sp11hw_mmio_write8(uint64_t pa, uint8_t value) { mmio_write(pa, value, 1); }
void sp11hw_mmio_write32(uint64_t pa, uint32_t value) { mmio_write(pa, value, 4); }

/* ---- the CPU interface, the timer and the CPU ---- */
#define SYSREAD(fn, reg, expr) \
    uint64_t fn(void) { log_ev(EV_R, reg, 0); return (expr); }
SYSREAD(sp11hw_read_spsel, GR_SPSEL, G.spsel)
SYSREAD(sp11hw_read_daif, GR_DAIF, (G.daif & ~(uint64_t)0x80) | (G.i_masked ? 0x80 : 0))
SYSREAD(sp11hw_read_vbar_el2, GR_VBAR, G.vbar)
SYSREAD(sp11hw_read_icc_sre_el2, GR_ICC_SRE, G.sre)
SYSREAD(sp11hw_read_icc_ctlr_el1, GR_ICC_CTLR, G.icc_ctlr)
SYSREAD(sp11hw_read_icc_pmr_el1, GR_ICC_PMR, G.pmr)
SYSREAD(sp11hw_read_icc_bpr1_el1, GR_ICC_BPR1, G.bpr1)
SYSREAD(sp11hw_read_icc_igrpen1_el1, GR_ICC_IGRPEN1, G.igrpen1)
SYSREAD(sp11hw_read_icc_rpr_el1, GR_ICC_RPR, G.rpr)
SYSREAD(sp11hw_read_icc_hppir1_el1, GR_ICC_HPPIR, hppir1_value())
/* ISTATUS ignores IMASK; a timer that never fires (NO_FIRE) never sets it. */
static uint64_t istatus(void) { return G.no_istatus ? 0u : ((G.ctl & 1) && !G.no_fire && W.tick >= G.cval) || stuck() ? 4u : 0u; }
SYSREAD(sp11hw_read_cnthp_ctl_el2, GR_CNTHP_CTL, G.ctl | istatus())
SYSREAD(sp11hw_read_cnthp_cval_el2, GR_CNTHP_CVAL, G.cval)
SYSREAD(sp11hw_read_cnthctl_el2, GR_CNTHCTL, G.cnthctl0)
SYSREAD(sp11hw_read_vbar_el1, GR_VBAR1, G.vbar1)
SYSREAD(sp11hw_read_sctlr_el1, GR_SCTLR1, G.sctlr1)
SYSREAD(sp11hw_read_esr_el1, GR_ESR1, 0x02000000)
SYSREAD(sp11hw_read_elr_el1, GR_ELR1, 0xE1E00F00)
SYSREAD(sp11hw_read_far_el1, GR_FAR1, 0xE1E0FA00)
SYSREAD(sp11hw_read_cntv_ctl_el0, GR_CNTV_CTL, G.cntv_ctl)
SYSREAD(sp11hw_read_cntv_cval_el0, GR_CNTV_CVAL, G.cntv_cval)
SYSREAD(sp11hw_read_cntp_ctl_el0, GR_CNTP_CTL, G.cntp_ctl)
SYSREAD(sp11hw_read_cntp_cval_el0, GR_CNTP_CVAL, G.cntp_cval)

/* The acknowledge: the highest priority pending interrupt becomes active and the running priority is its priority. */
uint64_t sp11hw_read_icc_iar1_el1(void)
{
    uint64_t id = 1023;
    unsigned best, prio;
    log_ev(EV_R, GR_ICC_IAR, 0);
    if (signalled()) {
        best = best_pending(&prio);
        if (best != SNAP_TIMER_INTID) {         /* another interrupt: tracked on its own, so that PPI 26's own state stays what the timer made it */
            id = best;
            G.ack_other = 1;
            G.f_active = best + 1;
            G.rpr = prio;
            G.acks++;
        } else {
            id = G.in_el1 && G.have_el1_intid ? G.el1_intid : G.have_intid ? G.intid_override : SNAP_TIMER_INTID;
            G.ack_other = id != SNAP_TIMER_INTID;
            if (id < 1020) {                /* a spurious INTID acknowledges nothing: there is nothing to end */
                G.active = 1;
                G.rpr = G.prio;
                G.acks++;
            }
        }
    }
    log_ev(EV_E, E_ACK, id);
    return id;
}

#define SYSWRITE(fn, reg, stmt) \
    void fn(uint64_t value) { log_ev(EV_W, reg, value); if (!write_ignored(reg)) { stmt; } }
SYSWRITE(sp11hw_write_vbar_el2, GR_VBAR, G.vbar = value)
SYSWRITE(sp11hw_write_icc_pmr_el1, GR_ICC_PMR, G.pmr = value & 0xFF)
SYSWRITE(sp11hw_write_icc_bpr1_el1, GR_ICC_BPR1, G.bpr1 = value & 7)
SYSWRITE(sp11hw_write_icc_igrpen1_el1, GR_ICC_IGRPEN1, G.igrpen1 = value & 1)
SYSWRITE(sp11hw_write_cnthp_cval_el2, GR_CNTHP_CVAL, G.cval = value)
SYSWRITE(sp11hw_write_hcr_el2, GR_HCR, { G.hcr_init = 1; G.visible_ok = 0; G.hcr = G.hcr_ignore_imo ? value & ~(uint64_t)HCR_IMO : value; })
SYSWRITE(sp11hw_write_vbar_el1, GR_VBAR1, { G.visible_ok = 0; G.vbar1 = value; })
SYSWRITE(sp11hw_write_sctlr_el1, GR_SCTLR1, { G.visible_ok = 0; G.sctlr1 = value; })
/* Enabling the timer for the first time is the arming: the sync injection counts from it. A disable after the third acknowledged interrupt is the
 * final deadline's. */
void sp11hw_write_cnthp_ctl_el2(uint64_t value)
{
    log_ev(EV_W, GR_CNTHP_CTL, value);
    if (write_ignored(GR_CNTHP_CTL)) return;
    if (G.eoi_seen && !(value & 1)) G.ctl_off_after_eoi = 1;
    if (!(value & 1) && G.acks >= SG_CYCLES) G.final_off = 1;
    if ((value & 1) && !G.t_arm) G.t_arm = W.tick;
    G.ctl = value & 3;
}
/* The end of interrupt: the running priority drops, and with EOImode clear the interrupt is deactivated; with it set it waits for DIR. */
SYSWRITE(sp11hw_write_icc_eoir1_el1, GR_ICC_EOIR, { G.rpr = 0xFF; if (!G.eoi_seen) G.first_eoi_tick = W.tick; G.eoi_seen = 1; if (!(G.icc_ctlr & SG_ICC_CTLR_EOIMODE)) deactivate(value); })
SYSWRITE(sp11hw_write_icc_dir_el1, GR_ICC_DIR, { deactivate(value); })

void sp11hw_irq_mask(void)
{
    log_ev(EV_W, GR_DAIF, 1);
    G.i_masked = 1;
}

/* The stub and its table are fetched with the MMU and caches off: the stage must clean them after the last of the writes that make EL1 runnable. */
void sp11hw_make_visible(uint64_t start, uint64_t bytes)
{
    log_ev(EV_E, E_VISIBLE, start);
    G.visible_start = start;
    G.visible_bytes = bytes;
    G.visible_ok = start == G.vbar1 && bytes >= 0x800;
}

uint64_t sp11hw_gic_vectors(void) { return G.fake_vectors; }
uint64_t sp11hw_el1_vectors(void) { return G.fake_el1_vectors; }
uint64_t sp11hw_el1_image_bytes(void) { return 0x850; }

/* ---- the windows: what sp11-stick-gic-vec.c does on the unit, over the model ---- */
#define EL2_IRQ_SPSR (0x349u | G.spsr_kept)     /* EL2h, D, A and F masked, I clear, as window 1's loop is when the interrupt interrupts it */
#define EL1_IRQ_SPSR 0x345u                     /* EL1h with I clear, as the stub runs */
void sp11hw_irq_window(struct sw_gic_rec *rec, uint64_t vectors, uint64_t old_vbar, uint64_t deadline)
{
    uint64_t iter;
    unsigned act;
    log_ev(EV_E, E_WINDOW_OPEN, vectors);
    log_ev(EV_W, GR_VBAR, vectors);
    G.vbar = vectors;
    log_ev(EV_W, GR_DAIF, 0);
    G.i_masked = 0;
    for (iter = 0;; iter++) {
        uint64_t now;
        if (iter >= G.maxiter) {
            G.hang = 1;
            break;
        }
        now = sp11hw_ticks();
        /* With IRQ unmasked an interrupt that is signalled is taken at the next instruction boundary, so before the routine looks at its
         * flag: a handler that returns with IRQ still unmasked into a source that stays asserted is entered again and again. */
        if (!G.i_masked) {
            if (G.have_sync && G.t_arm && now >= G.t_arm + G.sync_at) {
                G.have_sync = 0;
                log_ev(EV_E, E_SYNC, 0);
                G.i_masked = 1;
                act = sw_gic_vec_other(rec, 4, 0x96000004u, 0xE850, 0xDEAD0000u, EL2_IRQ_SPSR);
                G.i_masked = (act & SW_VEC_MASK) != 0;
                if (act & SW_VEC_EXIT) break;
                continue;
            }
            if (signalled()) {
                log_ev(EV_E, E_IRQ, G.vbar);
                if (G.vbar != G.fake_vectors) violation("an IRQ taken with VBAR_EL2 not the stage's table", G.vbar);
                if (++G.deliveries > 64) {
                    G.hang = 1;
                    break;
                }
                G.i_masked = 1;
                act = sw_gic_vec_irq(rec, 0xE850, EL2_IRQ_SPSR);
                G.i_masked = (act & SW_VEC_MASK) != 0;
                if (act & SW_VEC_EXIT) break;
                continue;
            }
        }
        if (rec->done) break;
        if ((int64_t)(now - deadline) >= 0) break;
    }
    G.i_masked = 1;
    log_ev(EV_W, GR_DAIF, 1);
    G.phase++;
    if (!write_ignored(GR_VBAR)) G.vbar = old_vbar;
    log_ev(EV_W, GR_VBAR, old_vbar);
    log_ev(EV_E, E_WINDOW_CLOSE, 0);
}

/* Window 2: the ERET to the stub with IRQ unmasked at EL1, the stub's counter loop, and whatever takes it back. Each way back is a handler of
 * the stage that ends the excursion; the model checks the state the stub would run in and that no handler returns into EL1. */
void sp11hw_el1_excursion(struct sw_gic_rec *rec, uint64_t vectors, uint64_t old_vbar, uint64_t resume_spsr, uint64_t bound)
{
    uint64_t iter, start;
    unsigned act = 0;
    int took = 0;
    G.excursions++;
    G.resume_spsr_seen = resume_spsr;
    log_ev(EV_E, E_EXC_OPEN, vectors);
    if (vectors != G.fake_vectors) violation("the excursion installed a table that is not the stage's", vectors);
    log_ev(EV_W, GR_VBAR, vectors);
    G.vbar = vectors;
    if (G.vbar1 != G.fake_el1_vectors) violation("EL1 entered with VBAR_EL1 not the stage's table", G.vbar1);
    if (!(G.hcr & HCR_RW) || (G.hcr & HCR_TGE)) violation("EL1 entered with HCR_EL2.RW clear or TGE set", G.hcr);
    if (G.sctlr1 & 0x1005) violation("EL1 entered with SCTLR_EL1.M, C or I set: its stub is fetched uncached", G.sctlr1);
    if (!G.visible_ok) violation("EL1 entered before the stub and its table were made visible after the last write", G.visible_start);
    if ((resume_spsr & 0x1F) != 9 || (resume_spsr & 0x3C0) != 0x3C0) violation("the resume state is not EL2h with D, A, I and F masked", resume_spsr);
    G.in_el1 = 1;
    start = sp11hw_ticks();
    for (iter = 0; !took; iter++) {
        uint64_t now;
        if (iter >= G.maxiter) {
            G.hang = 1;
            break;
        }
        now = sp11hw_ticks();
        if (!(G.cnthctl0 & 1)) {            /* the stub's counter read traps to EL2: EC 0x18 at the lower-EL synchronous vector */
            log_ev(EV_E, E_SYNC_EL1, 0x18);
            act = sw_gic_vec_sync_el1(rec, (uint64_t)0x18 << 26, 0xE1E00010, EL1_IRQ_SPSR, 0, 0);
            took = 1;
        } else if (G.have_el1_sync) {
            G.have_el1_sync = 0;
            log_ev(EV_E, E_SYNC_EL1, G.el1_sync_esr);
            act = sw_gic_vec_sync_el1(rec, G.el1_sync_esr, 0xE1E00018, EL1_IRQ_SPSR, 0xFA, 0);
            took = 1;
        } else if (!G.el1_lost && signalled()) {
            if (G.have_el1_vector && G.el1_vector != SW_VEC_EL1_IRQ_SLOT) {     /* the interrupt reaches a vector that is not the lower-EL IRQ one */
                log_ev(EV_E, E_SYNC_EL1, 0x100 + G.el1_vector);
                act = G.el1_vector == SW_VEC_EL1_SYNC_SLOT ? sw_gic_vec_sync_el1(rec, (uint64_t)0x25 << 26, 0xE1E00020, EL1_IRQ_SPSR, 0xFA, 0)
                                                           : sw_gic_vec_other(rec, G.el1_vector, 0, 0xE1E00020, 0, EL1_IRQ_SPSR);
                if (!(act & SW_VEC_TO_EL2)) act = SW_VEC_TO_EL2;       /* a slot that is not the lower EL's ends the window at its exit, as the table does */
            } else if (G.hcr & HCR_IMO) {
                log_ev(EV_E, E_IRQ_EL1, G.vbar);
                if (G.vbar != G.fake_vectors) violation("an IRQ taken from EL1 with VBAR_EL2 not the stage's table", G.vbar);
                act = sw_gic_vec_irq_el1(rec, 0xE1E00024, EL1_IRQ_SPSR);
            } else {                        /* IMO clear: EL1's own table takes it, and its entry reports by HVC #1 with its number in x16 */
                log_ev(EV_E, E_SYNC_EL1, 0x105);
                act = sw_gic_vec_sync_el1(rec, ((uint64_t)SG_EC_HVC64 << 26) | 1, 0xE1E00028, EL1_IRQ_SPSR, 0, 5);
            }
            took = 1;
        } else if ((int64_t)(now - (start + bound)) >= 0) {
            log_ev(EV_E, E_SYNC_EL1, 0x16);
            act = sw_gic_vec_sync_el1(rec, (uint64_t)SG_EC_HVC64 << 26, 0xE1E00030, EL1_IRQ_SPSR, 0, 0);
            took = 1;
        }
    }
    if (took && !(act & SW_VEC_TO_EL2)) violation("a handler returned into EL1 after the excursion was over", act);
    G.in_el1 = 0;
    G.i_masked = 1;
    G.phase++;
    if (!write_ignored(GR_VBAR)) G.vbar = old_vbar;
    log_ev(EV_W, GR_VBAR, old_vbar);
    log_ev(EV_E, E_EXC_CLOSE, 0);
}

/* ---- scenario lines and output ---- */
static uint64_t maybe_number(const char *token)
{
    char *end;
    unsigned long long v = strtoull(token, &end, 0);
    return *end ? 0 : v;        /* a register name for IGNORE, NOHOLD and RWP is not a number */
}

static void gic_command(char *line)
{
    char *tok[5], *save, *p = line;
    int n = 0, i;
    while (n < 5 && (tok[n] = strtok_r(p, " \t\r\n", &save))) {
        n++;
        p = NULL;
    }
    if (n < 2) fail("GIC line without a key");
    if (!strcmp(tok[1], "MUTATE")) {    /* between two runs: bytes of the record in REPORT.DAT, as a damaged page would have them; the machine is left alone */
        struct region *rep = region_named("report");
        uint8_t bytes[64];
        unsigned count, at;
        if (n < 4 || !rep) fail("GIC MUTATE needs an offset, bytes and a report");
        at = (unsigned)num(tok[2]);
        count = hexbytes(tok[3], bytes, sizeof bytes);
        if (at + count > SG_REC_BYTES) fail("GIC MUTATE beyond the record");
        memcpy(rep->cpu + SG_REC_AT + at, bytes, count);
        memcpy(rep->dram + SG_REC_AT + at, bytes, count);
        return;
    }
    {
        const char *k = tok[1];
        uint64_t v = n > 2 ? maybe_number(tok[2]) : 0;
        if (!strcmp(k, "CTLR")) G.gicd_ctlr0 = (uint32_t)v;
        else if (!strcmp(k, "TYPER")) G.gicd_typer = (uint32_t)v;
        else if (!strcmp(k, "IIDR")) G.gicd_iidr = (uint32_t)v;
        else if (!strcmp(k, "RTYPER")) G.rtyper_lo = (uint32_t)v;
        else if (!strcmp(k, "FRAMES")) G.nframes = (uint32_t)v;
        else if (!strcmp(k, "AFF")) G.aff[num(tok[2]) % GIC_MAX_FRAMES] = num(tok[3]);
        else if (!strcmp(k, "WAKER")) G.waker0 = (uint32_t)v;
        else if (!strcmp(k, "IGROUPR0")) G.igroupr0_0 = (uint32_t)v;
        else if (!strcmp(k, "ISENABLER0")) G.isenabler0_0 = (uint32_t)v;
        else if (!strcmp(k, "ISPENDR0")) G.ispendr0_0 = (uint32_t)v;
        else if (!strcmp(k, "ISACTIVER0")) G.isactiver0_0 = (uint32_t)v;
        else if (!strcmp(k, "PRIO")) G.prio0 = (uint32_t)v;
        else if (!strcmp(k, "CFG1")) G.icfgr1_0 = (uint32_t)v;
        else if (!strcmp(k, "SRE")) G.sre = v;
        else if (!strcmp(k, "ICCCTLR")) G.icc_ctlr = v;
        else if (!strcmp(k, "PMR")) G.pmr0 = v;
        else if (!strcmp(k, "BPR1")) G.bpr1_0 = v;
        else if (!strcmp(k, "IGRPEN1")) G.igrpen1_0 = v;
        else if (!strcmp(k, "RPR")) G.rpr0 = v;
        else if (!strcmp(k, "CTL")) G.ctl0 = v;
        else if (!strcmp(k, "CVAL")) G.cval0 = v;
        else if (!strcmp(k, "DAIF")) G.daif0 = v;
        else if (!strcmp(k, "SPSEL")) G.spsel = v;
        else if (!strcmp(k, "VBAR")) G.vbar0 = v;
        else if (!strcmp(k, "CURRENTEL_LATE")) G.currentel_late = v, G.have_currentel_late = 1;
        else if (!strcmp(k, "NO_FIRE")) G.no_fire = 1;
        else if (!strcmp(k, "NO_DELIVER")) G.no_deliver = 1;
        else if (!strcmp(k, "INTID")) G.intid_override = v, G.have_intid = 1;
        else if (!strcmp(k, "SYNC_AT")) G.sync_at = v, G.have_sync = 1;
        else if (!strcmp(k, "STORM")) G.storm = 1;
        else if (!strcmp(k, "STORM_ONCE")) G.storm_once = 1;
        else if (!strcmp(k, "STORM_AFTER")) G.storm_after = v, G.have_storm_after = 1;
        else if (!strcmp(k, "FINAL_STUCK")) G.final_stuck = 1;
        else if (!strcmp(k, "DROP_AFTER")) G.drop_after = (unsigned)v, G.have_drop = 1;
        else if (!strcmp(k, "HPPIR")) G.hppir_override = v, G.have_hppir = 1;
        else if (!strcmp(k, "NO_ISTATUS")) G.no_istatus = 1;
        else if (!strcmp(k, "HIDE_PEND")) G.hide_pend = 1;
        else if (!strcmp(k, "DELAY")) G.delay = v;
        else if (!strcmp(k, "NS_PPI")) G.ns_ppi = (int)v;
        else if (!strcmp(k, "GICD_WMASK")) G.gicd_wmask = (uint32_t)v;
        else if (!strcmp(k, "FPRIO")) G.fprio[num(tok[2]) % 32] = (uint32_t)num(tok[3]);
        else if (!strcmp(k, "FGROUP")) G.fgroup_ok = (int)v;
        else if (!strcmp(k, "SPI_EN")) G.spi_en[num(tok[2]) % 32] = (uint32_t)num(tok[3]);
        else if (!strcmp(k, "SPI_PEND")) G.spi_pend[num(tok[2]) % 32] = (uint32_t)num(tok[3]);
        else if (!strcmp(k, "SPI_PRIO")) G.spi_prio = (uint32_t)v;
        else if (!strcmp(k, "SPI_LATE")) {
            if (n < 5) fail("GIC SPI_LATE needs a register, bits and a tick");
            G.spi_late_n = (unsigned)num(tok[2]) % 32, G.spi_late_val = (uint32_t)num(tok[3]), G.spi_late_at = num(tok[4]);
        }
        else if (!strcmp(k, "ICE_HOLD")) G.ice_hold = (uint32_t)v;
        else if (!strcmp(k, "HPPIR_IGNORES_PMR")) G.hppir_ignores_pmr = 1;
        else if (!strcmp(k, "HPPIR_EXTRA")) {
            if (n < 4) fail("GIC HPPIR_EXTRA needs an INTID and a priority");
            G.extra_intid = (unsigned)num(tok[2]), G.extra_prio = (unsigned)num(tok[3]), G.have_extra = 1;
        }
        else if (!strcmp(k, "CNTV_CTL")) G.cntv_ctl = v;
        else if (!strcmp(k, "CNTV_CVAL")) G.cntv_cval = v;
        else if (!strcmp(k, "CNTP_CTL")) G.cntp_ctl = v;
        else if (!strcmp(k, "CNTP_CVAL")) G.cntp_cval = v;
        else if (!strcmp(k, "MAXITER")) G.maxiter = v;
        else if (!strcmp(k, "CNTHCTL")) G.cnthctl0 = v;
        else if (!strcmp(k, "HCR_INIT")) G.hcr0 = v;
        else if (!strcmp(k, "VBAR1")) G.vbar1_0 = v;
        else if (!strcmp(k, "SCTLR1")) G.sctlr1_0 = v;
        else if (!strcmp(k, "HCR_IGNORE_IMO")) G.hcr_ignore_imo = 1;
        else if (!strcmp(k, "EL1_LOST")) G.el1_lost = 1;
        else if (!strcmp(k, "EL1_VECTOR")) G.el1_vector = v, G.have_el1_vector = 1;
        else if (!strcmp(k, "EL1_SYNC")) G.el1_sync_esr = v, G.have_el1_sync = 1;
        else if (!strcmp(k, "EL1_INTID")) G.el1_intid = v, G.have_el1_intid = 1;
        else if (!strcmp(k, "SPSR_KEPT")) G.spsr_kept = v;
        else if (!strcmp(k, "IGNORE") || !strcmp(k, "NOHOLD")) {
            for (i = 0; i < GR_COUNT && strcmp(tok[2], gr_names[i]); i++) {}
            if (i == GR_COUNT) fail("GIC IGNORE/NOHOLD: unknown register");
            if (k[0] == 'I') {
                G.ignore[i] = 1;
            } else {
                G.nohold[i] = 1;
                if (n > 3) G.nohold_from[i] = (unsigned)num(tok[3]);
            }
        } else if (!strcmp(k, "RWP")) {
            if (!strcmp(tok[2], "gicd")) G.rwp_gicd = 1000000;
            else G.rwp_gicr = 1000000;
        } else if (!strcmp(k, "RWP_LAG")) {
            if (n < 4 || strcmp(tok[2], "gicr")) fail("GIC RWP_LAG gicr n");
            G.rwp_gicr = (unsigned)num(tok[3]);
            G.rwp_lag = 1;
        } else {
            fail("unknown GIC key");
        }
    }
    gic_reset_live();
}

static void gic_dump(void)
{
    struct region *rep = region_named("report");
    unsigned i;
    enable_flush();                     /* a write that RWP_LAG still held goes through: the state printed is the one the machine ends in */
    if (rep && rep->size >= SG_REC_AT + SG_REC_BYTES) {
        printf("GICREC ");
        for (i = 0; i < SG_REC_BYTES; i++) printf("%02x", rep->cpu[SG_REC_AT + i]);
        printf("\n");
    }
    for (i = 0; i < G.nev; i++) {
        const struct gic_event *e = &G.ev[i];
        const char *name = e->kind == EV_E ? event_names[e->reg] : e->reg >= 0 && e->reg < GR_COUNT ? gr_names[e->reg] : "UNMODELLED";
        printf("GICLOG %u %c %s %llx %u %llu\n", i, e->kind, name, (unsigned long long)e->value, e->lines, (unsigned long long)e->tick);
    }
    for (i = 0; i < 256 && i < G.line_seen; i++) printf("GICLINE %u %llu\n", i, (unsigned long long)G.line_tick[i]);
    printf("GICSTATE gicd_ctlr=%x/%x waker=%x/%x igroupr0=%x/%x isenabler0=%x/%x icfgr1=%x/%x prio=%x/%x pmr=%llx/%llx bpr1=%llx/%llx "
           "igrpen1=%llx/%llx rpr=%llx/%llx ctl=%llx/%llx cval=%llx/%llx vbar=%llx/%llx masked=%d/%d active=%d fact=%u eoi=%d hang=%d deliveries=%d phase=%d "
           "hcr=%llx/%llx vbar1=%llx/%llx sctlr1=%llx/%llx acks=%u excursions=%u resume=%llx\n",
           G.gicd_ctlr0, G.gicd_ctlr, G.waker0, G.waker, G.igroupr0_0, G.igroupr0, G.isenabler0_0, G.isenabler0, G.icfgr1_0, G.icfgr1, G.prio0,
           G.prio, (unsigned long long)G.pmr0, (unsigned long long)G.pmr, (unsigned long long)G.bpr1_0, (unsigned long long)G.bpr1,
           (unsigned long long)G.igrpen1_0, (unsigned long long)G.igrpen1, (unsigned long long)G.rpr0, (unsigned long long)G.rpr,
           (unsigned long long)(G.ctl0 & 3), (unsigned long long)G.ctl, (unsigned long long)G.cval0, (unsigned long long)G.cval,
           (unsigned long long)G.vbar0, (unsigned long long)G.vbar, (G.daif0 & 0x80) != 0, G.i_masked, G.active, G.f_active, G.eoi_seen, G.hang, G.deliveries,
           G.phase, (unsigned long long)G.hcr_first, (unsigned long long)G.hcr, (unsigned long long)G.vbar1_0, (unsigned long long)G.vbar1,
           (unsigned long long)G.sctlr1_0, (unsigned long long)G.sctlr1, G.acks, G.excursions, (unsigned long long)G.resume_spsr_seen);
    for (i = 0; i < G.nviol && i < GIC_MAX_VIOL; i++) printf("GICVIOL %s\n", G.viol[i]);
    printf("GICVIOLS %u\n", G.nviol);
}

/* The base harness reads its scenario through this: a line for the model is applied and not passed on; BEGIN resets the model; the model's
 * state is printed just before END, while the world it describes still exists. */
static char *gic_fgets(char *buf, int n, FILE *stream)
{
    for (;;) {
        char *r = fgets(buf, n, stream);
        if (!r) return NULL;
        if (!strncmp(buf, "GIC ", 4)) {
            char copy[1024];
            snprintf(copy, sizeof copy, "%s", buf);
            gic_command(copy);
            continue;
        }
        if (!strncmp(buf, "BEGIN ", 6)) {
            gic_reset();
            gic_reset_live();
        } else if (!strncmp(buf, "END", 3)) {
            gic_dump();
        }
        return r;
    }
}

int main(void)
{
    gic_reset();
    gic_reset_live();
    return wrapper_harness_main();
}
