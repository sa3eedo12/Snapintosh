/* SPDX-License-Identifier: MIT */
/* Host harness for the monitor entry (docs/SP11-STICK-MONITOR.md, section 9), driven by tools/test-sp11-stick-monitor.py. It is the GIC stage's
 * harness, tools/test-sp11-stick-gic.c, INCLUDED (nothing edited), which is itself the dry-run wrapper's with a model of the GIC, the CPU
 * interface and the EL2 timer; this file adds what the monitor entry needs: a FAKE MONITOR in place of sw_monitor_call (the assembly trampoline
 * and the appended image exist only on the unit), the registers the stage compares before and after the call, the five ID registers, the
 * instruction-cache operation and a record of the points the Python tests order events by. The code under test is the REAL wrapper, GIC arm
 * and put-back, monitor stage and reporter sources compiled with -DSP11_STICK_HOST_TEST, with -fsanitize=address,undefined. What is modelled
 * is the machine and the monitor's observable contract; what the model cannot prove is the monitor's own code, the trampoline's assembly (the
 * disassembly check reads it) and the unit.
 *
 * The fake monitor does what _start does to its caller and no more: it stores status and entered into its window first, may store a word in
 * the work arena, uses a stated depth of its stack, returns a stated value and may leave registers changed or store beyond its window. What it
 * does is set per scenario by a configuration file named by $SP11_MON_CFG: one line per scenario, `name key=value ...`, keys
 *   ret=v status=v entered=v        what it returns and the window's first two words (defaults 0, 0, 1)
 *   stack=n  stackfull=1            bytes of the stack it changes (default 0x400); its lowest word too
 *   overrun=off                     one word stored at out + off (a store beyond the 64 KiB window)
 *   leave=reg:v  (repeatable)       a register left at v at its return: daif spsel vbar hcr tpidr sctlr tcr cnthctl cnthp mdcr vttbr vtcr vmpidr
 *   replay=1                        take the stop timer's interrupts as the monitor does (probes/xnuboot/async.c, clock_timer.c): H6
 *   putphase=n                      the model's phase counter goes up by n where the stage is about to put the GIC back (test point PUTBACK):
 *                                   a register marked NOHOLD from that phase stops holding for the put-back, on the refusal path too
 *   below=n                         n words stored below the stack area, at stack_lo - 8 * i: an overflow into REPORT.DAT's filler; with stack=0x3FFF8
 *                                   the area's lowest word is the one word left alone
 * Output, besides the base harness's and the GIC model's: MONMARK (IC, CALL and RET: where each came in the GIC log and the event log, the
 * counter, the arguments), MONREPLAY (one line per interrupt of the replay), MONSTATE (the model's registers at the return), MONREC and
 * MONDIRTY (the stage's observation points: the monitor record as REPORT.DAT holds it, and how many 64-byte lines of REPORT.DAT are still
 * dirty in the model's cache) and MONREADS (at the same points, the point's name first: how many times the stage read each memory-mapped GIC register of the
 * model so far, by its name, outside the fake monitor; the model's own log holds only some of those reads). Hardware: NOT RUN; nothing here touches a device. */
/* The model's three MMIO readers are renamed for the length of the include so that this file can count the reads before it passes them on. */
#define sp11hw_mmio_read8 gic_model_mmio_read8
#define sp11hw_mmio_read32 gic_model_mmio_read32
#define sp11hw_mmio_read64 gic_model_mmio_read64
#include "test-sp11-stick-gic.c"
#undef sp11hw_mmio_read8
#undef sp11hw_mmio_read32
#undef sp11hw_mmio_read64

#include "../probes/sp11-stick-monitor.h"

/* ---- the machine beyond the GIC model: synthetic values, claims about no unit ---- */
static struct {
    uint64_t tpidr, mdcr, vttbr, vtcr, vmpidr, id[5];
    unsigned n_ic;
} M = {0xCE8A0000, 0x2, 0x0, 0x0, 0x80000000, {0xA2A2, 0xA4A4, 0xAE, 0xA1A1, 0xA2A22}, 0};

uint64_t sp11hw_read_tpidr_el2(void) { return M.tpidr; }
uint64_t sp11hw_read_mdcr_el2(void) { return M.mdcr; }
uint64_t sp11hw_read_vttbr_el2(void) { return M.vttbr; }
uint64_t sp11hw_read_vtcr_el2(void) { return M.vtcr; }
uint64_t sp11hw_read_vmpidr_el2(void) { return M.vmpidr; }
uint64_t sp11hw_read_id_aa64mmfr2_el1(void) { return M.id[0]; }
uint64_t sp11hw_read_id_aa64mmfr4_el1(void) { return M.id[1]; }
uint64_t sp11hw_read_revidr_el1(void) { return M.id[2]; }
uint64_t sp11hw_read_id_aa64isar1_el1(void) { return M.id[3]; }
uint64_t sp11hw_read_id_aa64isar2_el1(void) { return M.id[4]; }

static void mark(const char *kind, uint64_t a, uint64_t b, uint64_t c, uint64_t d, uint64_t e, uint64_t f)
{
    printf("MONMARK %s %u %zu %llu %llx %llx %llx %llx %llx %llx\n", kind, G.nev, W.nev, (unsigned long long)W.tick, (unsigned long long)a,
           (unsigned long long)b, (unsigned long long)c, (unsigned long long)d, (unsigned long long)e, (unsigned long long)f);
}

/* ---- the memory-mapped GIC reads, by register ----
 * The model logs GICD_CTLR, GICD_ISENABLER[n], GICD_ISPENDR[n] and the system registers it is read through, and none of its other MMIO reads. The test of the
 * record's gic text (tools/test-sp11-stick-monitor-arm.py) needs every register the stage reads, so each read of a register the model decodes is counted here,
 * per scenario (the count starts again when the scenario's name changes) and only while the fake monitor is not running. */
static struct {
    char scenario[sizeof W.name];
    unsigned long n[GR_COUNT];
    int in_monitor;
} RD;

static void reads_sync(void)
{
    if (strcmp(RD.scenario, W.name)) {
        memset(&RD, 0, sizeof RD);
        snprintf(RD.scenario, sizeof RD.scenario, "%s", W.name);
    }
}

static void count_read(uint64_t pa)
{
    unsigned frame;
    uint64_t off;
    int reg = -1;
    reads_sync();
    if (RD.in_monitor) return;
    if (in_window(pa, SNAP_GICD_BASE, 0x10000)) {
        off = pa - SNAP_GICD_BASE;
        reg = off == SG_GICD_CTLR ? GR_GICD_CTLR : off == SG_GICD_TYPER ? GR_GICD_TYPER : off == SG_GICD_IIDR ? GR_GICD_IIDR :
              (off >= SG_GICD_ISENABLER && off < SG_GICD_ISENABLER + 0x80) ? GR_GICD_ISENABLER :
              (off >= SG_GICD_ISPENDR && off < SG_GICD_ISPENDR + 0x80) ? GR_GICD_ISPENDR : -1;
    } else if (locate(pa, &frame, &off)) {
        if (off == SG_GICR_TYPER) {
            reg = GR_GICR_TYPER;
        } else if (mine(frame) && off == SG_GICR_CTLR) {
            reg = GR_GICR_CTLR;
        } else if (mine(frame) && off == SG_GICR_WAKER) {
            reg = GR_GICR_WAKER;
        } else if (mine(frame) && off >= SG_SGI_FRAME) {
            off -= SG_SGI_FRAME;
            reg = off == SG_IGROUPR0 ? GR_IGROUPR0 : off == SG_ISENABLER0 ? GR_ISENABLER0 : off == SG_ISPENDR0 ? GR_ISPENDR0 :
                  off == SG_ISACTIVER0 ? GR_ISACTIVER0 : off == SG_ICFGR1 ? GR_ICFGR1 :
                  (off >= SG_IPRIORITYR && off < SG_IPRIORITYR + 32) ? GR_IPRIORITYR : -1;
        }
    }
    if (reg >= 0) RD.n[reg]++;
}

uint8_t sp11hw_mmio_read8(uint64_t pa) { count_read(pa); return gic_model_mmio_read8(pa); }
uint32_t sp11hw_mmio_read32(uint64_t pa) { count_read(pa); return gic_model_mmio_read32(pa); }
uint64_t sp11hw_mmio_read64(uint64_t pa) { count_read(pa); return gic_model_mmio_read64(pa); }

static void print_reads(const char *point)
{
    unsigned i;
    reads_sync();
    printf("MONREADS %s", point);
    for (i = 0; i < GR_COUNT; i++)
        if (RD.n[i]) printf(" %s=%lu", gr_names[i], RD.n[i]);
    printf("\n");
}

/* IC IALLUIS: an ISB-shaped event in the base log (a = 0x1C) so that its place among the stores and cache operations is known. */
void sp11hw_ic_ialluis(void)
{
    M.n_ic++;
    event(EV_ISB, 0x1C, 0, 1);
    mark("IC", 0, 0, 0, 0, 0, 0);
}

/* ---- what a scenario makes the fake monitor do ---- */
#define MON_LEAVES 8
struct mon_cfg {
    uint64_t ret, status, entered, stack, overrun, below;
    int stackfull, has_overrun, replay, nleave, bumpphase, ns_ppi, has_ns_ppi, putphase;
    struct {
        char reg[12];
        uint64_t value;
    } leave[MON_LEAVES];
};

static void cfg_load(struct mon_cfg *k)
{
    const char *path = getenv("SP11_MON_CFG");
    char line[1024];
    FILE *f;
    memset(k, 0, sizeof *k);
    k->entered = 1;
    k->stack = 0x400;
    if (!path || !(f = fopen(path, "r"))) return;
    while (fgets(line, sizeof line, f)) {
        char *tok, *save, *p = line;
        int first = 1, mine_line = 0;
        while ((tok = strtok_r(p, " \t\r\n", &save))) {
            p = NULL;
            if (first) {
                first = 0;
                mine_line = !strcmp(tok, W.name);
                if (!mine_line) break;
                continue;
            }
            if (!strncmp(tok, "ret=", 4)) k->ret = strtoull(tok + 4, NULL, 0);
            else if (!strncmp(tok, "status=", 7)) k->status = strtoull(tok + 7, NULL, 0);
            else if (!strncmp(tok, "entered=", 8)) k->entered = strtoull(tok + 8, NULL, 0);
            else if (!strncmp(tok, "stack=", 6)) k->stack = strtoull(tok + 6, NULL, 0);
            else if (!strncmp(tok, "stackfull=", 10)) k->stackfull = atoi(tok + 10);
            else if (!strncmp(tok, "overrun=", 8)) k->overrun = strtoull(tok + 8, NULL, 0), k->has_overrun = 1;
            else if (!strncmp(tok, "replay=", 7)) k->replay = atoi(tok + 7);
            else if (!strncmp(tok, "bumpphase=", 10)) k->bumpphase = atoi(tok + 10);
            else if (!strncmp(tok, "putphase=", 9)) k->putphase = atoi(tok + 9);
            else if (!strncmp(tok, "below=", 6)) k->below = strtoull(tok + 6, NULL, 0);
            else if (!strncmp(tok, "ns_ppi=", 7)) k->ns_ppi = atoi(tok + 7), k->has_ns_ppi = 1;
            else if (!strncmp(tok, "leave=", 6)) {
                char *colon = strchr(tok + 6, ':');
                if (!colon || k->nleave >= MON_LEAVES) fail("bad leave= in the monitor configuration");
                *colon = 0;
                snprintf(k->leave[k->nleave].reg, sizeof k->leave[0].reg, "%s", tok + 6);
                k->leave[k->nleave++].value = strtoull(colon + 1, NULL, 0);
            } else {
                fail("unknown key in the monitor configuration");
            }
        }
        if (mine_line) break;
    }
    fclose(f);
}

/* A store by the monitor: a cached store like the wrapper's, into a region that is writable, and not in the wrapper's log of its own stores. */
static void mon_store64(uint64_t pa, uint64_t value)
{
    struct region *r = find_region(pa, 8);
    uint64_t off, i;
    if (!r) bus_fault("monitor store outside every region", pa, 8);
    if (!r->writable) bus_fault("monitor store to a region that is not writable", pa, 8);
    off = pa - r->base;
    memcpy(r->cpu + off, &value, 8);
    for (i = off / LINE; i <= (off + 7) / LINE; i++) r->dirty[i] = 1;
}

static void leave_register(const char *name, uint64_t v)
{
    if (!strcmp(name, "daif")) G.daif = v, G.i_masked = (v & 0x80) != 0;
    else if (!strcmp(name, "spsel")) G.spsel = v;
    else if (!strcmp(name, "vbar")) G.vbar = v;
    else if (!strcmp(name, "hcr")) G.hcr = v, G.hcr_init = 1;
    else if (!strcmp(name, "tpidr")) M.tpidr = v;
    else if (!strcmp(name, "sctlr")) W.reg_value[REG_SCTLR] = v;
    else if (!strcmp(name, "tcr")) W.reg_value[REG_TCR] = v;
    else if (!strcmp(name, "cnthctl")) G.cnthctl0 = v;
    else if (!strcmp(name, "cnthp")) G.ctl = v & 3;
    else if (!strcmp(name, "mdcr")) M.mdcr = v;
    else if (!strcmp(name, "vttbr")) M.vttbr = v;
    else if (!strcmp(name, "vtcr")) M.vtcr = v;
    else if (!strcmp(name, "vmpidr")) M.vmpidr = v;
    else fail("unknown register in leave=");
}

/* ---- the stop timer's interrupts as the monitor takes them (H6) ----
 * Transcribed from probes/xnuboot/async.c (the tick path: ISTATUS and HPPIR1 == 26, IAR1 == 26, timer_sync, timer_eoi) and
 * probes/xnuboot/clock_timer.c (timer_arm, timer_eoi); tools/test-sp11-stick-monitor-build.py checks that those sources still contain the
 * sequence this one follows. Three ticks, then the deadline taken without an acknowledge (watchdog_off). A tick that is not PPI 26 stops
 * the run with EV_ASYNC, as the monitor's fall-through does. */
static void replay_ticks(void)
{
    uint64_t hz = W.reg_value[REG_CNTFRQ], cycle = hz / 100;
    unsigned k;
    uint64_t now = base_ticks();
    sp11hw_write_cnthp_cval_el2(now + cycle);                       /* timer_arm */
    sp11hw_write_cnthp_ctl_el2(1);
    for (k = 0; k < 4; k++) {
        uint64_t ctl, pending, iar = 0, post;
        unsigned guard = 0;
        while (!signalled() && !stuck() && guard++ < 2000000) W.tick += 100;
        now = base_ticks();
        ctl = sp11hw_read_cnthp_ctl_el2();
        if (k == 3) {                                               /* the deadline: no acknowledge, watchdog_off */
            sp11hw_write_cnthp_ctl_el2(0);
            printf("MONREPLAY FINAL ctl=%llx hppir=%llx pend=%d act=%d\n", (unsigned long long)ctl,
                   (unsigned long long)sp11hw_read_icc_hppir1_el1(), timer_asserted() ? 1 : 0, G.active);
            return;
        }
        pending = sp11hw_read_icc_hppir1_el1();
        if (!(ctl & 4) || pending != 26) {                          /* async.c: not the tick: EV_ASYNC and stop */
            printf("MONREPLAY ASYNC k=%u ctl=%llx hppir=%llx\n", k, (unsigned long long)ctl, (unsigned long long)pending);
            return;
        }
        iar = sp11hw_read_icc_iar1_el1();
        if (iar != 26) {
            printf("MONREPLAY ASYNC k=%u ctl=%llx iar=%llx\n", k, (unsigned long long)ctl, (unsigned long long)iar);
            if (iar < 1020) sp11hw_write_icc_eoir1_el1(iar);
            return;
        }
        sp11hw_write_cnthp_cval_el2(now + cycle);                   /* timer_sync: rearm before the end of interrupt */
        sp11hw_write_cnthp_ctl_el2(1);
        sp11hw_write_icc_eoir1_el1(iar);                            /* timer_eoi */
        if (sp11hw_read_icc_ctlr_el1() & SG_ICC_CTLR_EOIMODE) sp11hw_write_icc_dir_el1(iar);
        post = sp11hw_read_icc_hppir1_el1();
        printf("MONREPLAY TICK k=%u ctl=%llx hppir=%llx iar=%llx post=%llx act=%d\n", k, (unsigned long long)ctl, (unsigned long long)pending,
               (unsigned long long)iar, (unsigned long long)post, G.active);
    }
}

/* How many of the 64-byte lines of REPORT.DAT in [lo, hi) the model's cache still holds dirty: written by the CPU and not cleaned to the model's DRAM. */
static unsigned dirty_lines(const struct region *r, uint64_t lo, uint64_t hi)
{
    unsigned n = 0;
    uint64_t i;
    for (i = lo / LINE; i < (hi + LINE - 1) / LINE && i < r->size / LINE; i++) n += r->dirty[i] != 0;
    return n;
}

/* The stage's observation points (probes/sp11-stick-monitor.c, host build only): what REPORT.DAT holds of the monitor record at that moment, and
 * how many lines of each part of REPORT.DAT are dirty then (a model of the cache: it shows what the stage cleaned, not how the unit's caches
 * behave). PUTBACK is the point just before the GIC goes back: a scenario may move the model's phase there. */
static void point_dirty(const char *what)
{
    struct region *rep = region_named("report");
    if (!rep) return;
    printf("MONDIRTY %s total=%u head=%u window=%u gic=%u rec=%u filler=%u stack=%u\n", what, dirty_lines(rep, 0, rep->size), dirty_lines(rep, 0, 0x1000),
           dirty_lines(rep, SP11SB_REPORT_OUT, SP11SB_REPORT_OUT + SM_WINDOW_BYTES), dirty_lines(rep, SG_REC_AT, SG_REC_AT + SG_REC_BYTES),
           dirty_lines(rep, SM_REC_AT, SM_REC_AT + SM_REC_BYTES), dirty_lines(rep, SM_REC_AT + SG_REC_SLOT, SM_STACK_AT),
           dirty_lines(rep, SM_STACK_AT, SM_STACK_AT + SM_STACK_BYTES));
}

void sp11hw_test_point(const char *what)
{
    struct region *rep = region_named("report");
    unsigned i;
    printf("MONREC %s ", what);
    if (rep && rep->size >= SM_REC_AT + SM_REC_BYTES)
        for (i = 0; i < SM_REC_BYTES; i++) printf("%02x", rep->cpu[SM_REC_AT + i]);
    printf("\n");
    point_dirty(what);
    print_reads(what);
    if (!strcmp(what, "PUTBACK")) {
        struct mon_cfg k;
        cfg_load(&k);
        G.phase += k.putphase;
    }
}

/* ---- the fake monitor ---- */
uint64_t sw_monitor_call(uint64_t out, uint64_t work, uint64_t size, uint64_t params, uint64_t host_cpus, uint64_t stack_top)
{
    struct mon_cfg k;
    uint64_t i;
    cfg_load(&k);
    reads_sync();
    RD.in_monitor = 1;
    mark("CALL", out, work, size, params, host_cpus, stack_top);
    sp11hw_test_point("CALL");                                      /* what a hang here would leave in DRAM: the record as stored, and what is still dirty */
    mon_store64(out, k.status);                                     /* _start stores status and entered first */
    mon_store64(out + 8, k.entered);
    mon_store64(work, 0x4D4F4E5745524B21ull);                       /* and the work arena is the monitor's */
    if (stack_top) {
        for (i = 1; i <= k.stack / 8; i++) mon_store64(stack_top - 8 * i, 0x1111111111111111ull + i);
        if (k.stackfull) mon_store64(stack_top - SM_STACK_BYTES, 0x2222222222222222ull);
        for (i = 1; i <= k.below; i++) mon_store64(stack_top - SM_STACK_BYTES - 8 * i, 0x3333333333333333ull + i);     /* an overflow below the area */
    }
    if (k.has_overrun) mon_store64(out + k.overrun, 0xBAD0BAD0BAD0BAD0ull);
    if (k.has_ns_ppi) G.ns_ppi = k.ns_ppi;                          /* the PPI's group changed under the monitor: its interrupt is no longer an IRQ */
    if (k.replay) replay_ticks();
    for (i = 0; i < (uint64_t)k.nleave; i++) leave_register(k.leave[i].reg, k.leave[i].value);
    if (k.bumpphase) G.phase++;                                     /* the model's phase counter: writes marked NOHOLD stop holding from here */
    W.tick += 1000000;                                              /* the monitor's run time, in counter ticks */
    mark("RET", k.ret, 0, 0, 0, 0, 0);
    printf("MONSTATE hcr=%llx vbar=%llx tpidr=%llx daif=%llx ic=%u\n", (unsigned long long)G.hcr, (unsigned long long)G.vbar,
           (unsigned long long)M.tpidr, (unsigned long long)((G.daif & ~(uint64_t)0x80) | (G.i_masked ? 0x80 : 0)), M.n_ic);
    RD.in_monitor = 0;
    return k.ret;
}

/* The dry run's vector halves are not in the monitor build: the model's windows would call them, and nothing here opens a window. */
unsigned sw_gic_vec_irq(struct sw_gic_rec *rec, uint64_t elr, uint64_t spsr) { (void)rec; (void)elr; (void)spsr; fail("a dry-run vector was entered"); return 0; }
unsigned sw_gic_vec_irq_el1(struct sw_gic_rec *rec, uint64_t elr, uint64_t spsr) { (void)rec; (void)elr; (void)spsr; fail("a dry-run vector was entered"); return 0; }
unsigned sw_gic_vec_sync_el1(struct sw_gic_rec *rec, uint64_t esr, uint64_t elr, uint64_t spsr, uint64_t far, uint64_t el1_vector)
{
    (void)rec; (void)esr; (void)elr; (void)spsr; (void)far; (void)el1_vector;
    fail("a dry-run vector was entered");
    return 0;
}
unsigned sw_gic_vec_other(struct sw_gic_rec *rec, uint64_t vector, uint64_t esr, uint64_t elr, uint64_t far, uint64_t spsr)
{
    (void)rec; (void)vector; (void)esr; (void)elr; (void)far; (void)spsr;
    fail("a dry-run vector was entered");
    return 0;
}
