/* SPDX-License-Identifier: MIT */
/* The monitor entry of SNAPMON-M1.BIN (docs/SP11-STICK-MONITOR.md; docs/SP11-MONITOR-PORT.md, work item 4b). EXPERIMENTAL; hardware NOT RUN;
 * compiled only under SNAP_SP11_STICK_MONITOR=1. Interface and parameters: sp11-stick-monitor.h; the record and the page lines:
 * sp11-stick-monitor-record.h.
 *
 * Order, one sub-stage between steps and the record (REPORT.DAT + 0x12000) stored whole after each, so that a hang is named by the last one:
 *   checks    reads only: REPORT.DAT's record slot and stack area probed writable, the monitor's window and the filler below the stack's area zero,
 *             PARAMS (time box, early_stop.flags and gpu.flags), the ID registers, the monitor image in the file hashed in place against the build's
 *             pin. A check that fails refuses with GIC_NOT_READY and an SM_OUT_* detail before anything is stored but REPORT.DAT; the record slot
 *             is stored with it, except for REPORT_UNWRITABLE, whose slot is what was found unwritable.
 *   sync      IC IALLUIS, DSB ISH, ISB: the guest will execute what the copy wrote with data stores.
 *   arm       the GIC stage's arm half (sp11-stick-gic.c, built with SW_GIC_DRY_REGS=0): classes G, R and C, the NEXT line and eight seconds first.
 *   call      thirteen EL2 registers read, the stack area filled, the arguments and the head stored, MONITOR_ENTERED stored, the MON CALL line, the
 *             counter read once more, then the call.
 *   return    the extension slots compared with what was stored before the call, the monitor's window cleaned to the point of coherency, x0 and
 *             the window's first two words stored, the registers read again and compared, the stack scanned for its depth and zeroed, the filler
 *             below it scanned and zeroed, then the GIC put back (classes C, R, G) and its record finished. Nothing after the call is a refusal.
 * No recursion, no allocation, no writable global, no libc; every loop has a fixed bound. The wrapper's stores are sp11hw_store64's. */
#include "sp11-stick-hw.h"

#if SNAP_SP11_STICK_MONITOR
#include "sp11-stick-monitor.h"

struct sw_mon {
    struct sw_ctx *c;
    union {
        struct sw_mon_rec r;
        uint64_t w[SM_REC_BYTES / 8];
    } rec;
    uint64_t params_pa, stack_lo, stack_top;
};
_Static_assert(sizeof(struct sw_mon) < 1024, "the stage's state is a small part of the wrapper's 32 KiB of stack");

/* ---- The record and the page ---- */

static void mon_flush(struct sw_mon *m)
{
    uint64_t at = m->c->report_pa + SM_REC_AT;
    uint32_t i;
    for (i = 0; i < SM_REC_BYTES / 8; i++) sp11hw_store64(at + 8 * i, m->rec.w[i]);
#ifndef SP11_STICK_MONITOR_SEED_NO_RECORD_CLEAN
    sp11hw_clean_range(at, SM_REC_BYTES);
#endif
    sp11hw_dsb_sy();
}

static void mon_say(struct sw_mon *m, unsigned id, int essential)
{
    struct pl_line l;
    sw_begin(&l);
    if (sm_line(m->c->a, &l, id, (const uint8_t *)&m->rec.r)) sw_emit(m->c, &l, essential);
}

static void mon_init(struct sw_mon *m, struct sw_ctx *c)
{
    struct sw_mon_rec *r = &m->rec.r;
    uint32_t i;
    m->c = c;
    for (i = 0; i < SM_REC_BYTES / 8; i++) m->rec.w[i] = 0;
    for (i = 0; i < 8; i++) r->magic[i] = (uint8_t)SM_REC_MAGIC[i];
    r->version = SM_REC_VERSION;
    r->size = SM_REC_BYTES;
    r->substage = SM_SUB_CHECKS;
    r->counter_hz = c->head.r.counter_hz;
    r->monitor_pa = c->files[SP11SB_ROLE_MONITOR].base + SP11_MONITOR_OFFSET;
    r->monitor_offset = SP11_MONITOR_OFFSET;
    r->monitor_bytes = SP11_MONITOR_BYTES;
    r->monitor_sha[0] = SP11_MONITOR_SHA0;
    r->monitor_sha[1] = SP11_MONITOR_SHA1;
    r->monitor_sha[2] = SP11_MONITOR_SHA2;
    r->monitor_sha[3] = SP11_MONITOR_SHA3;
    m->params_pa = 0;
    m->stack_lo = c->report_pa + SM_STACK_AT;
    m->stack_top = m->stack_lo + SM_STACK_BYTES;
}

/* ---- The checks: reads only ---- */

/* The slots and the area this stage stores to are probed writable first (the wrapper probed REPORT.DAT's first and last page only), and the
 * monitor's window and the filler below the stack's area must be zero, as the runner loaded them: a stale word in the window could be taken for the
 * monitor's own, and one in the filler for the stack's overflow. */
static unsigned mon_report_ready(struct sw_mon *m)
{
    const struct sw_ctx *c = m->c;
    struct sw_probe probe;
    if (c->files[SP11SB_ROLE_REPORT].size < SP11SB_REPORT_BYTES) return SM_OUT_REPORT_UNWRITABLE;
    if (sw_probe_range(c->report_pa + SM_REC_AT, SM_REC_BYTES, 1, &probe) || sw_probe_range(m->stack_lo, SM_STACK_BYTES, 1, &probe))
        return SM_OUT_REPORT_UNWRITABLE;
    if (!sp11sb_zero((const uint8_t *)sp11hw_view(c->report_pa + SP11SB_REPORT_OUT, SM_WINDOW_BYTES), SM_WINDOW_BYTES)) return SM_OUT_WINDOW_DIRTY;
#ifndef SP11_STICK_MONITOR_SEED_NO_FILLER_PRECHECK
    if (!sp11sb_zero((const uint8_t *)sp11hw_view(c->report_pa + SM_FILLER_AT, SM_FILLER_BYTES), SM_FILLER_BYTES)) return SM_OUT_WINDOW_DIRTY;
#endif
    return 0;
}

/* The one PARAMS chunk's destination and size, from the table that sp11sb_guest_check has passed (exactly one exists). */
static int mon_params_chunk(const struct sw_ctx *c, uint64_t *dest, uint64_t *size)
{
    uint32_t i, n = SP11SB_GUEST_U32(c->guest, entry_count);
    for (i = 0; i < n; i++) {
        const uint8_t *e = SP11SB_CHUNK_AT(c->guest, i);
        if (SP11SB_CHUNK_U16(e, kind) == SP11SB_KIND_PARAMS) {
            *dest = SP11SB_CHUNK_U64(e, dest);
            *size = SP11SB_CHUNK_U64(e, size);
            return 1;
        }
    }
    return 0;
}

/* PARAMS as the copy left it in the placement (the digests of the chunks were checked): the time box must be one the plan allows, and the
 * two blocks of the A16's that would reach for hardware this machine does not have must be off. */
static unsigned mon_params(struct sw_mon *m)
{
    struct sw_mon_rec *r = &m->rec.r;
    uint64_t dest, size;
    const uint8_t *p;
    if (!mon_params_chunk(m->c, &dest, &size) || !dest) return SM_OUT_PARAMS_PA;
    if (size < SM_PARAMS_BYTES) return SM_OUT_PARAMS_SIZE;
    m->params_pa = dest;
    p = (const uint8_t *)sp11hw_view(dest, SM_PARAMS_BYTES);
    r->box_s = sp11sb_rd64(p + SM_PARAMS_WATCHDOG);
    r->early_stop_flags = sp11sb_rd64(p + SM_PARAMS_EARLY_STOP_FLAGS);
    r->gpu_flags = sp11sb_rd64(p + SM_PARAMS_GPU_FLAGS);
#ifdef SP11_STICK_MONITOR_SEED_NO_PARAMS_CHECK
    return 0;
#endif
    if (r->early_stop_flags) return SM_OUT_PARAMS_EARLY_STOP;
    if (r->gpu_flags) return SM_OUT_PARAMS_GPU;
    return !r->box_s || r->box_s > SP11_STICK_MONITOR_BOX_MAX_S ? SM_OUT_PARAMS_BOX : 0;
}

/* The monitor image in the file, hashed where it will run and compared with the digest the build pinned: a wrapper paired with another monitor,
 * or a file damaged after the runner's own check, is refused here and not called. */
static unsigned mon_image(struct sw_mon *m)
{
    const struct sw_mon_rec *r = &m->rec.r;
    struct sw_probe probe;
    uint8_t digest[32];
    uint32_t i;
    if (m->c->files[SP11SB_ROLE_MONITOR].size != (uint64_t)SP11_MONITOR_OFFSET + SP11_MONITOR_BYTES) return SM_OUT_IMAGE_SIZE;
    if (sw_probe_range(r->monitor_pa, SP11_MONITOR_BYTES, 0, &probe)) return SM_OUT_IMAGE_UNREADABLE;
#ifdef SP11_STICK_MONITOR_SEED_NO_DIGEST
    return 0;
#endif
    sw_sha256(sp11hw_view(r->monitor_pa, SP11_MONITOR_BYTES), SP11_MONITOR_BYTES, digest);
    for (i = 0; i < 4; i++)
        if (sp11sb_rd64(digest + 8 * i) != r->monitor_sha[i]) return SM_OUT_IMAGE_DIGEST;
    return 0;
}

static unsigned mon_checks(struct sw_mon *m)
{
    struct sw_mon_rec *r = &m->rec.r;
    unsigned out;
    r->id[0] = sp11hw_read_id_aa64mmfr2_el1();
    r->id[1] = sp11hw_read_id_aa64mmfr4_el1();
    r->id[2] = sp11hw_read_revidr_el1();
    r->id[3] = sp11hw_read_id_aa64isar1_el1();
    r->id[4] = sp11hw_read_id_aa64isar2_el1();
    if ((out = mon_report_ready(m)) != 0 || (out = mon_params(m)) != 0) return out;
    return mon_image(m);
}

/* ---- The registers the monitor puts back ---- */

static uint64_t mon_read_row(unsigned row)
{
    switch (row) {
    case SM_ROW_DAIF: return sp11hw_read_daif();
    case SM_ROW_SPSEL: return sp11hw_read_spsel();
    case SM_ROW_VBAR_EL2: return sp11hw_read_vbar_el2();
    case SM_ROW_HCR_EL2: return sp11hw_read_hcr_el2();
    case SM_ROW_TPIDR_EL2: return sp11hw_read_tpidr_el2();
    case SM_ROW_SCTLR_EL2: return sp11hw_read_sctlr_el2();
    case SM_ROW_TCR_EL2: return sp11hw_read_tcr_el2();
    case SM_ROW_CNTHCTL_EL2: return sp11hw_read_cnthctl_el2();
    case SM_ROW_CNTHP_CTL_EL2: return sp11hw_read_cnthp_ctl_el2();
    case SM_ROW_MDCR_EL2: return sp11hw_read_mdcr_el2();
    case SM_ROW_VTTBR_EL2: return sp11hw_read_vttbr_el2();
    case SM_ROW_VTCR_EL2: return sp11hw_read_vtcr_el2();
    default: return sp11hw_read_vmpidr_el2();
    }
}

/* Before the call (post = 0) the rows' `pre`; after it the `post` and the mask of the rows that differ. A difference is a finding about the
 * monitor, which puts each of these back at its exit (probes/xnuboot/report.c): it is recorded and printed and never a refusal. */
static void mon_snapshot(struct sw_mon *m, int post)
{
    struct sw_mon_rec *r = &m->rec.r;
    uint32_t changed = 0;
    unsigned i;
    for (i = 0; i < SM_ROWS; i++) {
        uint64_t value = mon_read_row(i);
        if (!post) {
            r->row[i].pre = value;
            continue;
        }
        r->row[i].post = value;
        if ((value ^ r->row[i].pre) & sm_row_mask(i)) changed |= 1u << i;
    }
    if (!post) return;
#ifdef SP11_STICK_MONITOR_SEED_NO_REG_CHECK
    changed = 0;
#endif
    r->changed = changed;
    if (changed) r->flags |= SM_F_REGS_CHANGED;
}

/* ---- The stack: filled before the call, scanned and zeroed after it ---- */

static void mon_fill_stack(const struct sw_mon *m)
{
    uint64_t at;
    for (at = m->stack_lo; at < m->stack_top; at += 8) sp11hw_store64(at, SM_STACK_PATTERN);
}

/* The filler is zero before the call (mon_report_ready), so a word in it afterwards is the monitor's: a stack that outgrew its area, or a frame
 * that skipped the area's lowest word and so left the watermark's last word unchanged. It sets STACK FULL, and is zeroed and cleaned like the
 * area, so that the filler is what the runner loaded again. An overflow that goes on past the filler reaches the extension slots, which
 * mon_extension_intact compares. */
static void mon_filler_after(struct sw_mon *m)
{
#ifndef SP11_STICK_MONITOR_SEED_NO_FILLER_SCAN
    const uint64_t lo = m->c->report_pa + SM_FILLER_AT;
    uint64_t at;
    if (sp11sb_zero((const uint8_t *)sp11hw_view(lo, SM_FILLER_BYTES), SM_FILLER_BYTES)) return;
    m->rec.r.flags |= SM_F_STACK_FULL;
    for (at = lo; at < lo + SM_FILLER_BYTES; at += 8) sp11hw_store64(at, 0);
#ifndef SP11_STICK_MONITOR_SEED_NO_FILLER_CLEAN
    sp11hw_clean_range(lo, SM_FILLER_BYTES);
#endif
#else
    (void)m;
#endif
}

/* The depth is the distance from the top to the lowest word the monitor changed, exact to a word as long as the monitor never writes the
 * pattern itself. STACK FULL says that the figure is only a lower bound and the stack may have overrun: the monitor wrote the area's lowest word,
 * or anything below the area (mon_filler_after). The area is zeroed and cleaned afterwards. */
static void mon_stack_after(struct sw_mon *m)
{
    const uint8_t *p = (const uint8_t *)sp11hw_view(m->stack_lo, SM_STACK_BYTES);
    uint64_t at, free_bytes = 0;
    while (free_bytes < SM_STACK_BYTES && sp11sb_rd64(p + free_bytes) == SM_STACK_PATTERN) free_bytes += 8;
    m->rec.r.stack_used = SM_STACK_BYTES - free_bytes;
    if (!free_bytes) m->rec.r.flags |= SM_F_STACK_FULL;
    mon_filler_after(m);
    for (at = m->stack_lo; at < m->stack_top; at += 8) sp11hw_store64(at, 0);
    sp11hw_clean_range(m->stack_lo, SM_STACK_BYTES);
    sp11hw_dsb_sy();
}

/* Whether the two slots after the monitor's window are still what this stage and the GIC stage stored there before the call, byte for byte: a
 * monitor that stores beyond its window (it is built never to) would show here, and this is the only place it could. */
static int mon_extension_intact(const struct sw_mon *m, const struct sw_gic *g)
{
    const uint8_t *p = (const uint8_t *)sp11hw_view(m->c->report_pa + SG_REC_AT, SM_EXT_SLOTS * SG_REC_SLOT);
    uint32_t i;
    for (i = 0; i < SM_EXT_SLOTS * SG_REC_SLOT / 8; i++) {
        uint64_t want = 0;
        if (i < SG_REC_BYTES / 8) want = g->rec.w[i];
        else if (i >= SG_REC_SLOT / 8 && i < SG_REC_SLOT / 8 + SM_REC_BYTES / 8) want = m->rec.w[i - SG_REC_SLOT / 8];
        if (sp11sb_rd64(p + 8 * i) != want) return 0;
    }
    return 1;
}

/* ---- Refusals before the call ---- */

/* The page names a refusal. The record is stored with it, except for REPORT_UNWRITABLE: its slot is what was found unwritable (or lies beyond
 * the file), and a store there is the one thing the check said not to do. */
static uint64_t mon_refuse(struct sw_mon *m, unsigned outcome, uint64_t detail)
{
    m->rec.r.outcome = outcome;
#ifdef SP11_STICK_MONITOR_SEED_FLUSH_UNWRITABLE
    mon_flush(m);
#else
    if (outcome != SM_OUT_REPORT_UNWRITABLE) mon_flush(m);
#endif
#ifdef SP11_STICK_HOST_TEST
    sp11hw_test_point("REFUSED");
#endif
    mon_say(m, SM_L_VERDICT, 1);
    return sw_refuse(m->c, SP11SB_E_GIC_NOT_READY, detail);
}

/* The GIC record after the put-back, finished the way the dry run finishes its own (sp11-stick-gic-dry.c, gic_finish). sw_gic_put_back stores the
 * record when it begins, with the sub-stage RESTORING, and the registers that did not go back are only in the stage's copy: without this the copy
 * in REPORT.DAT, which SNAPREP-M1 prints after a restart, says CLEAN=YES over a register that was not put back. `first` is what the arm decided:
 * zero when it armed, else its refusal. */
static void mon_gic_finish(struct sw_gic *g, unsigned first, unsigned put)
{
#ifndef SP11_STICK_MONITOR_SEED_NO_GIC_FINISH
    struct sw_gic_rec *r = &g->rec.r;
    r->first_outcome = first;
    r->outcome = put ? SG_OUT_NOT_RESTORED : first;
    r->substage = SG_SUB_DONE;
    sw_gic_flush(g);
#else
    (void)g;
    (void)first;
    (void)put;
#endif
}

/* The arm refused or a store did not hold: the GIC goes back (a no-op when no class began) and the refusal carries the GIC's outcome, the one
 * convention of docs/SP11-STICK-GIC.md, so that the same reporter line explains it. */
static uint64_t mon_arm_failed(struct sw_mon *m, struct sw_gic *g, unsigned out)
{
    unsigned put;
#ifdef SP11_STICK_HOST_TEST
    sp11hw_test_point("PUTBACK");
#endif
    put = sw_gic_put_back(g);
    mon_gic_finish(g, out, put);
    sw_gic_say(g, SG_L_RESTORE, 1);
    sw_gic_say(g, SG_L_VERDICT, 1);
    return mon_refuse(m, SM_OUT_GIC_NOT_READY, put ? SG_OUT_NOT_RESTORED : out);
}

uint64_t sw_monitor_stage(struct sw_ctx *c)
{
    struct sw_mon m;
    struct sw_gic g;
    struct sw_mon_rec *r = &m.rec.r;
    const uint8_t *window;
    uint64_t out_pa = c->report_pa + SP11SB_REPORT_OUT, work = SP11SB_PIN_U64(c->pin, work_base), work_size = SP11SB_PIN_U64(c->pin, work_size), ret, ticks_call, ticks_back;
    unsigned out, put;
    int intact;
    mon_init(&m, c);
    out = mon_checks(&m);
    mon_say(&m, SM_L_IDS0, 0);
    mon_say(&m, SM_L_IDS1, 0);
    mon_say(&m, SM_L_IMAGE, 0);
    if (out) return mon_refuse(&m, out, out);
#ifndef SP11_STICK_MONITOR_SEED_NO_IC_SYNC
    sp11hw_ic_ialluis();
    r->flags |= SM_F_IC_SYNC;
#endif
    r->substage = SM_SUB_SYNCED;
    mon_flush(&m);
    r->substage = SM_SUB_ARMING;
    mon_flush(&m);
    if ((out = sw_gic_arm(&g, c, 0)) != 0) return mon_arm_failed(&m, &g, out);
#ifdef SP11_STICK_MONITOR_SEED_STRAY_GIC
    sp11hw_mmio_write32(SNAP_GICR_FIRST + SNAP_GICR_STRIDE + SG_SGI_FRAME + SG_ISENABLER0, 0);
#endif
    r->substage = SM_SUB_ARMED;
    mon_snapshot(&m, 0);
    r->call_x[0] = out_pa;
    r->call_x[1] = work;
    r->call_x[2] = work_size;
    r->call_x[3] = m.params_pa;
    r->call_x[4] = 0;
#ifdef SP11_STICK_MONITOR_SEED_WRONG_ARGS
    r->call_x[1] = work_size;
    r->call_x[2] = work;
#endif
    r->stack_top = m.stack_top;
    r->stack_bytes = SM_STACK_BYTES;
    r->flags |= SM_F_STACK_S2;
    mon_fill_stack(&m);
#ifdef SP11_STICK_MONITOR_SEED_NO_S2
    r->stack_top = 0;
    r->stack_bytes = 0;
    r->flags &= ~(uint32_t)SM_F_STACK_S2;
#endif
    r->flags |= SM_F_CALLED;
    r->substage = SM_SUB_CALLING;
    c->head.r.monitor_out = out_pa;
    c->head.r.work_base = work;
    c->head.r.work_size = work_size;
    c->head.r.params_pa = m.params_pa;
    r->ticks_call = c->head.r.ticks_launch = sp11hw_ticks();
    mon_flush(&m);
    mon_say(&m, SM_L_CALL, 1);
    sw_set_stage(c, SP11SB_STAGE_MONITOR_ENTERED);
#ifdef SP11_STICK_MONITOR_SEED_TICKS_EARLY
    ticks_call = r->ticks_call;
#else
    ticks_call = sp11hw_ticks();                /* the record and the head stored above hold the tick before the page line; TICKS is measured from this one */
#endif
    ret = sw_monitor_call(r->call_x[0], r->call_x[1], r->call_x[2], r->call_x[3], r->call_x[4], r->stack_top);
    ticks_back = sp11hw_ticks();
    intact = mon_extension_intact(&m, &g);      /* before anything of the record or the GIC record changes: they are compared with what was stored */
#ifdef SP11_STICK_MONITOR_SEED_NO_OVERRUN_CHECK
    intact = 1;
#endif
    r->ticks_call = c->head.r.ticks_launch = ticks_call;
    c->head.r.ticks_return = r->ticks_return = ticks_back;
#ifndef SP11_STICK_MONITOR_SEED_NO_WINDOW_CLEAN
    sp11hw_clean_range(out_pa, SM_WINDOW_BYTES);
#endif
    sp11hw_dsb_sy();
    window = (const uint8_t *)sp11hw_view(out_pa, 16);
    r->ret = c->head.r.monitor_ret = ret;
    r->status = c->head.r.monitor_status = sp11sb_rd64(window);
    r->entered = c->head.r.monitor_entered = sp11sb_rd64(window + 8);
    r->flags |= SM_F_RETURNED | (intact ? 0 : SM_F_WINDOW_OVERRUN);
    mon_snapshot(&m, 1);
#ifdef SP11_STICK_MONITOR_SEED_STORE_AFTER
    sp11hw_store8(SP11SB_PIN_U64(c->pin, place_base), 0x5A);
#endif
    if (r->flags & SM_F_STACK_S2) mon_stack_after(&m);
    r->outcome = !ret ? SM_OUT_RETURNED : ret == SM_QUARANTINE_OWNERSHIP || ret == SM_QUARANTINE_GPU ? SM_OUT_QUARANTINE : SM_OUT_RET_OTHER;
    r->substage = SM_SUB_RETURNED;
    sw_set_stage(c, SP11SB_STAGE_MONITOR_RETURNED);
#ifdef SP11_STICK_MONITOR_SEED_REFUSE_AFTER
    if (r->changed) return sw_refuse(c, SP11SB_E_GIC_NOT_READY, SM_OUT_RET_OTHER);
#endif
    mon_flush(&m);
    mon_say(&m, SM_L_RET, 1);
    mon_say(&m, SM_L_REGS, 1);
    mon_say(&m, SM_L_QUARANTINE, 1);
#ifdef SP11_STICK_HOST_TEST
    sp11hw_test_point("PUTBACK");
#endif
    put = sw_gic_put_back(&g);
    if (put) r->flags |= SM_F_GIC_NOT_RESTORED;
    mon_gic_finish(&g, 0, put);
    r->substage = SM_SUB_DONE;
    mon_flush(&m);
#ifdef SP11_STICK_HOST_TEST
    sp11hw_test_point("FINAL");
#endif
    sw_gic_say(&g, SG_L_RESTORE, 1);
    mon_say(&m, SM_L_VERDICT, 1);
    return 0;
}

#ifndef SP11_STICK_HOST_TEST
/* The call: x0 to x4 are the monitor's arguments and x5 the stack to run it on (0: stay on the caller's). The monitor is an AAPCS64 function
 * and keeps x19 to x30 and SP, so the caller's SP waits in x19, saved with x29 and x30 first. A direct bl: the build defines sp11_monitor
 * at the monitor's offset in the file (a PC-relative CALL26 against an absolute symbol, which the runner's ELF check allows). */
__asm__(".text\n"
        ".balign 16\n"
        ".global sw_monitor_call\n"
        ".type sw_monitor_call, %function\n"
        "sw_monitor_call:\n"
        "    stp x29, x30, [sp, #-32]!\n"
        "    str x19, [sp, #16]\n"
        "    mov x29, sp\n"
        "    mov x19, sp\n"
        "    cbz x5, 1f\n"
        "    mov sp, x5\n"
        "1:  bl sp11_monitor\n"
        "    mov sp, x19\n"
        "    ldr x19, [sp, #16]\n"
        "    ldp x29, x30, [sp], #32\n"
        "    ret\n"
        ".size sw_monitor_call, .-sw_monitor_call\n");
#endif
#endif /* SNAP_SP11_STICK_MONITOR */
