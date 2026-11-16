/* SPDX-License-Identifier: MIT */
/* SNAPMON.BIN of the bundle dry run (docs/SP11-STICK-WRAPPER.md; docs/SP11-MONITOR-PORT.md, work item 4a): the wrapper that q1n1's stick
 * runner calls first. It checks the five-file bundle the way docs/SP11-STICK-BUNDLE.md section 8 orders the checks, verifies the
 * pinned placement against the final memory map and against the MMU, copies the guest chunks into that placement with cache
 * maintenance, hashes the copy again from DRAM, reports, and returns. IT NEVER CALLS A MONITOR: this build has none, and no code
 * path arms a timer, touches the GIC or writes an EL2 system register. UNIMPLEMENTED: the monitor entry, the stop timer and the GIC
 * bring-up (work items 4b, 8 and 11, each behind a later gate). The stages PREPARED, MONITOR_ENTERED and MONITOR_RETURNED are never
 * written and the report fields that belong to them stay zero.
 *
 * Order, one stage between steps, REPORT.DAT stored whole after each (so a hang is named by its last stage, on the panel by the last
 * line and in RAM by the head):
 *   CurrentEL -> arguments -> the five slots -> AT probe of REPORT.DAT -> ENTERED, ARGS_OK, FILES_OK -> PIN.DAT -> PIN_OK ->
 *   registers and pins -> OBSERVED_OK -> placement against the map, guarded reads, AT probes -> PLACED_OK -> GUEST.DAT structure and
 *   digests -> GUEST_OK -> chunk table against the placement -> COPYING -> copy, clean -> verify from DRAM -> COPIED -> DONE.
 * Until COPYING nothing but REPORT.DAT is stored to: every step before it reads and hashes, and the first store to the placement
 * comes after the stage COPYING is in REPORT.DAT. A refusal returns 0x1000 + its code and says which of the two held.
 *
 * Bounded: no recursion, no allocation, no writable global (the context and the report head are in the stack), no libc. Evidence:
 * EXPERIMENTAL; hardware NOT RUN. */
#include "sp11-stick-hw.h"

#if SNAP_SP11_STICK
#include "sp11-stick-wrapper.h"

uint64_t payload_entry(const struct q1n1_payload_args *a);

static const char *attr_name(uint8_t attr)
{
    switch (sp11hw_attr_class(attr)) {
    case SP11HW_ATTR_DEVICE: return "DEVICE";
    case SP11HW_ATTR_NC: return "NC";
    case SP11HW_ATTR_WT: return "WT";
    case SP11HW_ATTR_WB: return "WB";
    default: return "OTHER";
    }
}

static const char *sh_name(uint8_t sh)
{
    switch (sh & 3) {
    case 0: return "NSH";
    case 1: return "RES";
    case 2: return "OS";
    default: return "IS";
    }
}

static void stage_begin(struct sw_ctx *c, struct pl_line *l)
{
    sw_begin(l);
    pl_text(l, "STAGE=");
    pl_text(l, sp11sb_stage_name(c->head.r.stage));
}

/* What an AT or a guarded read said about the first page it refused, on the panel, before the refusal line. */
static void probe_diag(struct sw_ctx *c, const char *what, const struct sw_probe *p)
{
    struct pl_line l;
    sw_begin(&l);
    pl_text(&l, "PROBE=");
    pl_text(&l, what);
    sc_key_hex(c->a, &l, " ADDR=", p->at);
    if (p->load32) {
        pl_text(&l, " LOAD32 RC=");
        sc_dec(&l, p->par);
    } else {
        sc_key_hex(c->a, &l, " PAR=", p->par);
    }
    sw_emit(c, &l, 1);
}

static void probe_summary(struct sw_ctx *c, const char *what, const struct sw_probe *p)
{
    struct pl_line l;
    sw_begin(&l);
    pl_text(&l, "AT ");
    pl_text(&l, what);
    sc_key_dec(&l, " PROBES=", p->count);
    pl_text(&l, " ATTR=");
    pl_text(&l, attr_name(p->attr));
    pl_text(&l, " SH=");
    pl_text(&l, sh_name(p->sh));
    sw_emit(c, &l, 0);
}

/* The files, the map, the preload table and the stack: what the runner holds that the placement must stay off. The runner's own
 * image is not among them (the arguments do not say where it is); it is not conventional memory, which PLACE_NOT_CONV refuses. */
static void declare_ranges(struct sw_ctx *c)
{
    const struct q1n1_payload_args *a = c->a;
    uint32_t i, n = 0;
    for (i = 0; i < SP11SB_FILES; i++) {
        c->declared[n].base = c->files[i].base;
        c->declared[n].size = (c->files[i].size + 4095) & ~(uint64_t)4095;
        n++;
    }
    c->declared[n].base = a->map_base;
    c->declared[n++].size = a->map_size;
    c->declared[n].base = a->preload_table;
    c->declared[n++].size = a->preload_size;
    c->declared[n].base = a->stack_base;
    c->declared[n++].size = a->stack_size;
    c->ndeclared = n;
}

/* The conventional descriptor that held the placement, for the report: the check has passed, so exactly one qualifies. */
static void find_descriptor(struct sw_ctx *c, const uint8_t *map, uint64_t count, uint64_t stride, uint64_t pb, uint64_t pe)
{
    uint64_t i;
    for (i = 0; i < count; i++) {
        const uint8_t *d = map + i * stride;
        uint64_t start = sp11sb_rd64(d + 8), pages = sp11sb_rd64(d + 24);
        if (sp11sb_rd32(d) == SP11SB_EFI_CONVENTIONAL && start <= pb && pe <= start + (pages << 12)) {
            c->head.r.desc_base = start;
            c->head.r.desc_pages = pages;
            c->head.r.desc_index = (uint32_t)i;
            return;
        }
    }
}

/* The value an observed-class refusal is about, for REPORT.DAT's detail word and the panel. */
static uint64_t observed_detail(int code, const struct sp11sb_observed *o)
{
    switch (code) {
    case SP11SB_E_NOT_EL2: return o->current_el;
    case SP11SB_E_E2H_SET: return o->hcr_el2;
    case SP11SB_E_EL2_REGIME: return (o->sctlr_el2 & 1) ? o->tcr_el2 : o->sctlr_el2;
    case SP11SB_E_MIDR_MISMATCH: return o->midr_el1;
    case SP11SB_E_MPIDR_MISMATCH: return o->mpidr_el1;
    case SP11SB_E_CNTFRQ_MISMATCH: return o->cntfrq_el0;
    case SP11SB_E_GOP_MISMATCH: return o->fb_base;
    case SP11SB_E_STORE_NOT_AUTH: return o->build_stores;
    default: return 0;
    }
}

/* ARGS_OK is the stage of the arguments the caller has already checked; this one adds the slots. A refusal about a slot other than
 * REPORT.DAT's, or about its content, can be recorded: REPORT.DAT's own slot is what locates the report, so a refusal that leaves it
 * unknown is printed and returned and nothing is stored anywhere. */
static uint64_t stage_files(struct sw_ctx *c)
{
    struct sw_probe probe;
    struct pl_line l;
    const struct sp11sb_file *report = &c->files[SP11SB_ROLE_REPORT];
    uint32_t self;
    int r, all;
    if ((r = sc_files(c->a, c->files, &c->nfiles, &self)) != 0) return sw_refuse(c, r, 0);
    if (self != SP11SB_ROLE_MONITOR) return sw_refuse(c, SP11SB_E_ARGS_BAD, self);
    all = sp11sb_files_check(c->files, c->nfiles);
    if (c->nfiles != SP11SB_FILES || sp11sb_file_check(report, SP11SB_ROLE_REPORT)) return sw_refuse(c, all, sc_bad_slot(c->files, c->nfiles));
    if ((r = sw_probe_range(report->base, report->size, 1, &probe)) != 0) {
        probe_diag(c, "REPORT.DAT", &probe);
        return sw_refuse(c, r, probe.at);
    }
    c->report_pa = report->base;
    sw_head_init(c);
    sw_say_stage(c);
    sw_set_stage(c, SP11SB_STAGE_ARGS_OK);
    sw_say_stage(c);
    if (all) return sw_refuse(c, all, sc_bad_slot(c->files, c->nfiles));
    c->head.r.guest_file_base = c->files[SP11SB_ROLE_GUEST].base;
    c->head.r.guest_file_size = c->files[SP11SB_ROLE_GUEST].size;
    sw_set_stage(c, SP11SB_STAGE_FILES_OK);
    stage_begin(c, &l);
    sc_key_hex(c->a, &l, " GUEST=", c->head.r.guest_file_base);
    sc_key_dec(&l, " BYTES=", c->head.r.guest_file_size);
    sw_emit(c, &l, 0);
    return 0;
}

/* PIN.DAT is checked with the runner's own SHA-256; its first page is probed first because nothing has read that memory at EL2 yet
 * (the runner hashed it before the bounce). Only the bytes the check reads are probed. */
static uint64_t stage_pin(struct sw_ctx *c)
{
    const struct sp11sb_file *f = &c->files[SP11SB_ROLE_PIN];
    struct sw_probe probe;
    struct pl_line l;
    int r;
    c->pin_len = f->size;
    r = sw_probe_range(f->base, f->size < SP11SB_PIN_BYTES ? f->size : SP11SB_PIN_BYTES, 0, &probe);
    if (r) {
        probe_diag(c, "PIN.DAT", &probe);
        return sw_refuse(c, r, probe.at);
    }
    c->pin = (const uint8_t *)sp11hw_view(f->base, f->size);
    r = sp11sb_pin_check(c->pin, c->pin_len, sw_sha256);
    c->head.r.pin_code = (uint32_t)r;
    if (r) return sw_refuse(c, r, 0);
    sw_set_stage(c, SP11SB_STAGE_PIN_OK);
    stage_begin(c, &l);
    pl_text(&l, " LABEL=");
    pl_text(&l, (const char *)SP11SB_PIN_AT(c->pin, build_label));
    sc_key_dec(&l, " CHUNKS=", SP11SB_PIN_U32(c->pin, guest_chunk_count));
    sw_emit(c, &l, 0);
    return 0;
}

/* Raw reads first, stored and printed, then compared: a refusal then shows what the unit said. Only reads: CurrentEL was read at
 * entry, and nothing here writes a register. The GIC macros are the monitor build's (this build has no monitor and no GIC code), and
 * the store classes this build uses are RAM and nothing else. */
static uint64_t stage_observed(struct sw_ctx *c)
{
    const struct q1n1_payload_args *a = c->a;
    struct sp11sb_observed o;
    struct pl_line l;
    uint64_t ctr = sp11hw_read_ctr_el0();
    int r;
    o.current_el = c->head.r.current_el;
    o.hcr_el2 = sp11hw_read_hcr_el2();
    o.sctlr_el2 = sp11hw_read_sctlr_el2();
    o.tcr_el2 = sp11hw_read_tcr_el2();
    o.midr_el1 = sp11hw_read_midr();
    o.mpidr_el1 = sp11hw_read_mpidr();
    o.cntfrq_el0 = sp11hw_read_cntfrq();
    o.build_gicd_base = SNAP_GICD_BASE;
    o.build_gicr_first = SNAP_GICR_FIRST;
    o.build_gicr_limit = SNAP_GICR_LIMIT;
    o.build_gicr_stride = SNAP_GICR_STRIDE;
    o.build_timer_intid = SNAP_TIMER_INTID;
    o.build_stores = SP11SB_STORE_RAM;
    o.fb_base = a->fb_base;
    o.fb_bytes = a->fb_bytes;
    o.fb_width = a->fb_width;
    o.fb_height = a->fb_height;
    o.fb_stride = a->fb_stride;
    o.fb_format = a->fb_format;
    c->head.r.midr_el1 = o.midr_el1;
    c->head.r.mpidr_el1 = o.mpidr_el1;
    c->head.r.cntfrq_el0 = o.cntfrq_el0;
    c->head.r.hcr_el2 = o.hcr_el2;
    c->head.r.sctlr_el2 = o.sctlr_el2;
    c->head.r.tcr_el2 = o.tcr_el2;
    c->head.r.counter_hz = o.cntfrq_el0;
    sw_head_flush(c);
    sw_begin(&l);
    pl_text(&l, "REGS");
    sc_key_hex(a, &l, " MIDR=", o.midr_el1);
    sc_key_hex(a, &l, " MPIDR=", o.mpidr_el1);
    sc_key_dec(&l, " CNTFRQ=", o.cntfrq_el0);
    sw_emit(c, &l, 0);
    sw_begin(&l);
    pl_text(&l, "REGS");
    sc_key_hex(a, &l, " HCR=", o.hcr_el2);
    sc_key_hex(a, &l, " SCTLR=", o.sctlr_el2);
    sc_key_hex(a, &l, " TCR=", o.tcr_el2);
    sc_key_hex(a, &l, " CTR=", ctr);
    sc_key_dec(&l, " DLINE=", sp11hw_line_from_ctr(ctr));
    sw_emit(c, &l, 0);
    sw_begin(&l);
    pl_text(&l, "GOP");
    sc_key_hex(a, &l, " BASE=", o.fb_base);
    sc_key_hex(a, &l, " BYTES=", o.fb_bytes);
    sc_key_dec(&l, " W=", o.fb_width);
    sc_key_dec(&l, " H=", o.fb_height);
    sc_key_dec(&l, " STRIDE=", o.fb_stride);
    sc_key_dec(&l, " FMT=", o.fb_format);
    sw_emit(c, &l, 0);
    r = sp11sb_observed_check(c->pin, &o);
    if (r) return sw_refuse(c, r, observed_detail(r, &o));
    sw_set_stage(c, SP11SB_STAGE_OBSERVED_OK);
    sw_say_stage(c);
    return 0;
}

/* The placement against the final map, then the part only the unit can answer (U1: does EL2's own map reach it?): a guarded read of
 * its first and last word, and AT S1E2W over its pages. A data abort in a payload lands in q1n1's fault page and reports nothing, so
 * the answer has to come before the first store. The map itself is probed before it is read. */
static uint64_t stage_place(struct sw_ctx *c)
{
    const struct q1n1_payload_args *a = c->a;
    uint64_t pb = SP11SB_PIN_U64(c->pin, place_base), ps = SP11SB_PIN_U64(c->pin, place_size), detail = 0;
    const uint8_t *map;
    struct sw_probe probe;
    struct pl_line l;
    int r;
    declare_ranges(c);
    if (a->map_base && a->map_size && sw_probe_range(a->map_base, a->map_size, 0, &probe)) {
        probe_diag(c, "MAP", &probe);
        return sw_refuse(c, SP11SB_E_PLACE_UNREADABLE, probe.at);
    }
    map = (const uint8_t *)sp11hw_view(a->map_base, a->map_size);
    r = sp11sb_place_check(c->pin, map, a->map_size, a->map_stride, a->fb_base, a->fb_bytes, c->declared, c->ndeclared);
    if (r) return sw_refuse(c, r, pb);
    c->head.r.place_base = pb;
    c->head.r.place_size = ps;
    find_descriptor(c, map, a->map_size / a->map_stride, a->map_stride, pb, pb + ps);
    sw_head_flush(c);
    r = sw_place_runtime(c, pb, ps, &detail, &probe);
    if (r) {
        probe_diag(c, "PLACEMENT", &probe);
        return sw_refuse(c, r, detail);
    }
    c->head.r.ticks_placed = sp11hw_ticks();
    sw_set_stage(c, SP11SB_STAGE_PLACED_OK);
    sw_begin(&l);
    pl_text(&l, "PLACE");
    sc_key_hex(a, &l, " BASE=", pb);
    sc_key_hex(a, &l, " SIZE=", ps);
    sc_key_dec(&l, " DESC=", c->head.r.desc_index);
    sc_key_hex(a, &l, " DBASE=", c->head.r.desc_base);
    sc_key_hex(a, &l, " DPAGES=", c->head.r.desc_pages);
    sw_emit(c, &l, 0);
    probe_summary(c, "PLACEMENT", &probe);
    sw_say_stage(c);
    return 0;
}

/* GUEST.DAT: probed (the biggest unknown of the dry run is whether EL2 reads it at all, and how fast), structure, then the digests of
 * the policy. The table digest is always checked; the report records the policy. */
static uint64_t stage_guest(struct sw_ctx *c)
{
    const struct sp11sb_file *f = &c->files[SP11SB_ROLE_GUEST];
    struct sw_probe probe;
    struct pl_line l;
    uint32_t bad = 0;
    int r;
    c->guest_len = f->size;
    r = sw_probe_range(f->base, f->size, 0, &probe);
    if (r) {
        probe_diag(c, "GUEST.DAT", &probe);
        return sw_refuse(c, r, probe.at);
    }
    probe_summary(c, "GUEST.DAT", &probe);
    c->guest = (const uint8_t *)sp11hw_view(f->base, f->size);
    r = sp11sb_guest_check(c->guest, c->guest_len, c->pin);
    c->head.r.guest_code = (uint32_t)r;
    if (r) return sw_refuse(c, r, 0);
    c->head.r.chunks_total = SP11SB_GUEST_U32(c->guest, entry_count);
    c->hash_policy = SP11_STICK_HASH_POLICY;
    c->head.r.hash_policy = c->hash_policy;
    sw_head_flush(c);
    r = sp11sb_guest_digests(c->guest, c->guest_len, c->pin, c->hash_policy, sw_sha256, &bad);
    if (r) {
        c->head.r.guest_code = (uint32_t)r;
        return sw_refuse(c, r, r == SP11SB_E_CHUNK_DIGEST ? bad : 0);
    }
    sw_set_stage(c, SP11SB_STAGE_GUEST_OK);
    stage_begin(c, &l);
    sc_key_dec(&l, " CHUNKS=", c->head.r.chunks_total);
    sc_key_hex(c->a, &l, " POLICY=", c->hash_policy);
    sc_key_dec(&l, " TICKS=", sp11hw_ticks() - c->head.r.ticks_placed);
    sw_emit(c, &l, 0);
    return 0;
}

/* The table is checked against the placement once more, with no store, so that a refusal there leaves the stage at GUEST_OK and the
 * placement untouched; then the stage COPYING is stored, and only then the first chunk. */
static uint64_t stage_copy(struct sw_ctx *c)
{
    struct pl_line l;
    uint32_t bad = 0;
    int r = sw_chunks_check(c, &bad);
    if (r) return sw_refuse(c, r, bad);
#ifdef SP11_STICK_SEED_EARLY_STORE
    sp11hw_store8(SP11SB_PIN_U64(c->pin, place_base), 0x5A);
#endif
    sw_set_stage(c, SP11SB_STAGE_COPYING);
    sw_say_stage(c);
    if ((r = sw_copy_all(c, &bad)) != 0) return sw_refuse(c, r, bad);
    c->head.r.ticks_copied = sp11hw_ticks();
    if ((r = sw_verify_all(c, &bad)) != 0) return sw_refuse(c, r, bad);
    c->head.r.ticks_hashed = sp11hw_ticks();
    sw_set_stage(c, SP11SB_STAGE_COPIED);
    stage_begin(c, &l);
    sc_key_dec(&l, " COPIED=", c->head.r.chunks_copied);
    sc_key_dec(&l, " BYTES=", c->head.r.bytes_copied);
    sc_key_dec(&l, " HASHED=", c->head.r.chunks_hashed);
    sc_key_dec(&l, " HASHED_BYTES=", c->head.r.bytes_hashed);
    sw_emit(c, &l, 0);
    return 0;
}

uint64_t payload_entry(const struct q1n1_payload_args *a)
{
    struct sw_ctx c;
    struct pl_line l;
    uint64_t ticks, el, ret;
    if (!a) return Q1N1_PAYLOAD_RET_BAD_ARGS;
    ticks = sp11hw_ticks();
    el = sp11hw_read_currentel();       /* first: HCR_EL2, SCTLR_EL2 and TCR_EL2, read later, are UNDEFINED below EL2 */
    if (sp11sb_args_check(a->magic, a->version, a->size) || !a->ops.puts) return SP11SB_RET_REFUSED + SP11SB_E_ARGS_BAD;
    if (!a->ops.hex || !a->ops.load32) {
        a->ops.puts("SNAPMON REFUSED=ARGS_BAD STAGE=NONE DETAIL=OPS");
        return SP11SB_RET_REFUSED + SP11SB_E_ARGS_BAD;
    }
    sw_ctx_init(&c, a, ticks);
    if (el != 8) return sw_refuse(&c, SP11SB_E_NOT_EL2, el);
    c.head.r.current_el = el;
    sw_begin(&l);
    pl_text(&l, "START");
    sc_key_dec(&l, " VERSION=", SP11SB_VERSION);
    sc_key_hex(a, &l, " EL=", el);
    sc_key_hex(a, &l, " POLICY=", SP11_STICK_HASH_POLICY);
    sw_emit(&c, &l, 0);
    if ((ret = stage_files(&c)) != 0 || (ret = stage_pin(&c)) != 0 || (ret = stage_observed(&c)) != 0 ||
        (ret = stage_place(&c)) != 0 || (ret = stage_guest(&c)) != 0 || (ret = stage_copy(&c)) != 0)
        return ret;
    sw_set_stage(&c, SP11SB_STAGE_DONE);
    sw_begin(&l);
    pl_text(&l, "TICKS");
    sc_key_dec(&l, " HZ=", c.head.r.counter_hz);
    sc_key_dec(&l, " PLACED=", c.head.r.ticks_placed - c.ticks_enter);
    sc_key_dec(&l, " COPIED=", c.head.r.ticks_copied - c.ticks_enter);
    sc_key_dec(&l, " HASHED=", c.head.r.ticks_hashed - c.ticks_enter);
    sw_emit(&c, &l, 0);
    stage_begin(&c, &l);
    pl_text(&l, " RETURN=0");
    sw_emit(&c, &l, 1);
    sw_say(&c, "MONITOR NOT CALLED: UNIMPLEMENTED IN THIS BUILD");
    sw_head_flush(&c);
    return 0;
}
#endif /* SNAP_SP11_STICK */
