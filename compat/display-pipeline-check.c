/* SPDX-License-Identifier: MIT */
/* See display-pipeline-check.h. Freestanding: no libc, no globals, no writes outside the result record and the
 * snapshot writer's buffer. Every loop is bounded by a constant (DPC_REG_COUNT, DPC_MAX_CTL), so the time taken does
 * not depend on the snapshot's contents. Linked by nothing today; tools/test-display-pipeline-check.py builds it. */
#include "display-pipeline-check.h"

#define ALL_ONES 0xffffffffu
#define GATES 4u

struct reading {
    uint64_t pa;
    uint32_t value, status;
};

struct view {
    const uint8_t *regs, *ctls;
    uint32_t intf_index, ctl_count, gap_us;
};

static uint32_t ld16(const uint8_t *p) { return (uint32_t)p[0] | (uint32_t)p[1] << 8; }
static uint32_t ld32(const uint8_t *p) { return ld16(p) | ld16(p + 2) << 16; }
static uint64_t ld64(const uint8_t *p) { return (uint64_t)ld32(p) | (uint64_t)ld32(p + 4) << 32; }

static void st32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
    p[2] = (uint8_t)(v >> 16);
    p[3] = (uint8_t)(v >> 24);
}

static void st64(uint8_t *p, uint64_t v)
{
    st32(p, (uint32_t)v);
    st32(p + 4, (uint32_t)(v >> 32));
}

/* Records the verdict and returns 1, so a stage can `return finish(...)` and its caller stops: every stage returns 1
 * when it decided and 0 when the evaluation continues. */
static uint32_t finish(struct dpc_result *out, uint32_t verdict, uint32_t why, uint32_t detail, uint32_t evidence)
{
    out->verdict = verdict;
    out->why = why;
    out->detail = detail;
    out->evidence = evidence;
    return 1;
}

static struct reading entry(const uint8_t *entries, uint32_t i)
{
    const uint8_t *e = entries + (size_t)i * DPC_SNAP_ENTRY;
    struct reading r = {ld64(e), ld32(e + 8), ld32(e + 12)};
    return r;
}

static uint32_t block_of(uint32_t reg)
{
    return reg == DPC_REG_GCC_DISP_AHB ? DPC_BLK_GCC
           : reg <= DPC_REG_MDP_CBCR   ? DPC_BLK_DISPCC
           : reg == DPC_REG_HW_VERSION ? DPC_BLK_MDSS
                                       : DPC_BLK_INTF;
}

static const struct dpc_block *block(const struct dpc_params *p, uint32_t blk)
{
    return blk == DPC_BLK_GCC ? &p->gcc : blk == DPC_BLK_DISPCC ? &p->dispcc : blk == DPC_BLK_MDSS ? &p->mdss : &p->intf;
}

static uint64_t expected_pa(const struct dpc_params *p, uint32_t reg)
{
    return block(p, block_of(reg))->base + p->reg_off[reg];
}

/* ---- Parameter validation ---------------------------------------------------------------------------------------- */

static int block_bad(const struct dpc_block *b) { return !b->length || b->base + b->length < b->base; }

static int inside(const struct dpc_block *outer, uint64_t base, uint64_t length)
{
    return length && base >= outer->base && base - outer->base <= outer->length &&
           length <= outer->length - (base - outer->base);
}

static int overlaps(uint64_t a, uint64_t alen, uint64_t b, uint64_t blen) { return a < b + blen && b < a + alen; }

static int fits(uint32_t off, uint64_t length) { return !(off & 3u) && (uint64_t)off + 4u <= length; }

static uint32_t counter_params_fault(const struct dpc_params *p)
{
    int used = p->refresh_hz_min || p->refresh_hz_max;

    if (!(p->required & DPC_EV_COUNTERS) && !used)
        return DPC_PF_NONE;
    if (!p->refresh_hz_min || p->refresh_hz_max < p->refresh_hz_min || p->refresh_hz_max > 1000u ||
        ((p->required & DPC_EV_COUNTERS) && !p->counter_gap_min_us))
        return DPC_PF_COUNTER;
    return DPC_PF_NONE;
}

static uint32_t ctl_params_fault(const struct dpc_params *p)
{
    uint32_t i, j;

    if (p->ctl_count > DPC_MAX_CTL || ((p->required & DPC_EV_ROUTING) && !p->ctl_count))
        return DPC_PF_CTL;
    if (!p->ctl_count)
        return DPC_PF_NONE;
    if (!fits(p->ctl_intf_active_off, p->ctl_length))
        return DPC_PF_CTL;
    for (i = 0; i < p->ctl_count; i++) {
        if (p->ctl_base[i] + p->ctl_length < p->ctl_base[i] || !inside(&p->mdp, p->ctl_base[i], p->ctl_length) ||
            overlaps(p->ctl_base[i], p->ctl_length, p->intf.base, p->intf.length))
            return DPC_PF_CTL;
        for (j = 0; j < i; j++)
            if (overlaps(p->ctl_base[i], p->ctl_length, p->ctl_base[j], p->ctl_length))
                return DPC_PF_CTL;
    }
    return DPC_PF_NONE;
}

static uint32_t params_fault(const struct dpc_params *p)
{
    uint32_t r, fault;

    if (p->abi != DPC_PARAMS_ABI)
        return DPC_PF_ABI;
    if (p->reserved)
        return DPC_PF_RESERVED;
    if ((p->required & ~DPC_EV_ALL) || (p->required & DPC_EV_CORE) != DPC_EV_CORE)
        return DPC_PF_REQUIRED;
    if (p->intf_index > DPC_MAX_INTF_INDEX)
        return DPC_PF_INTF_INDEX;
    /* dpu_major 0 means the version is unknown: it is then never compared and can never be required. */
    if ((!p->dpu_major && (p->required & DPC_EV_VERSION)) || p->dpu_major > 0xfu || p->dpu_minor > 0xfffu)
        return DPC_PF_VERSION;
    if (!p->gop_width || !p->gop_height || p->gop_width > DPC_MAX_DIMENSION || p->gop_height > DPC_MAX_DIMENSION)
        return DPC_PF_MODE;
    if (block_bad(&p->gcc) || block_bad(&p->dispcc) || block_bad(&p->mdss) || block_bad(&p->mdp) ||
        block_bad(&p->intf) || !inside(&p->mdp, p->intf.base, p->intf.length))
        return DPC_PF_BLOCK;
    if (overlaps(p->gcc.base, p->gcc.length, p->dispcc.base, p->dispcc.length) ||
        overlaps(p->gcc.base, p->gcc.length, p->mdss.base, p->mdss.length) ||
        overlaps(p->gcc.base, p->gcc.length, p->mdp.base, p->mdp.length) ||
        overlaps(p->dispcc.base, p->dispcc.length, p->mdss.base, p->mdss.length) ||
        overlaps(p->dispcc.base, p->dispcc.length, p->mdp.base, p->mdp.length) ||
        overlaps(p->mdss.base, p->mdss.length, p->mdp.base, p->mdp.length))
        return DPC_PF_OVERLAP;
    for (r = 0; r < DPC_REG_COUNT; r++)
        if (!fits(p->reg_off[r], block(p, block_of(r))->length))
            return DPC_PF_OFFSET;
    fault = ctl_params_fault(p);
    return fault ? fault : counter_params_fault(p);
}

/* ---- Snapshot parsing -------------------------------------------------------------------------------------------- */

/* DPC_WHY_OK with *v filled, or the reason the bytes cannot be read as a snapshot. A cut at any byte offset lands in
 * a TRUNCATED case, so no prefix of a valid snapshot can be taken for a complete one. */
static uint32_t parse_snapshot(const uint8_t *s, size_t len, struct view *v)
{
    uint32_t regs, ctls, total, i;
    size_t need;

    if (len < DPC_SNAP_HEADER)
        return DPC_WHY_TRUNCATED;
    if (ld32(s) != DPC_SNAP_MAGIC || ld16(s + 4) != DPC_SNAP_ABI_MAJOR || ld16(s + 6) != DPC_SNAP_ABI_MINOR ||
        ld32(s + 28) != 0)
        return DPC_WHY_MALFORMED;
    total = ld32(s + DPC_SNAP_OFF_TOTAL);
    v->intf_index = ld32(s + DPC_SNAP_OFF_INTF);
    regs = ld32(s + DPC_SNAP_OFF_REGS);
    ctls = ld32(s + DPC_SNAP_OFF_CTLS);
    v->gap_us = ld32(s + DPC_SNAP_OFF_GAP);
    if (regs > DPC_REG_COUNT || ctls > DPC_MAX_CTL || v->intf_index > DPC_MAX_INTF_INDEX)
        return DPC_WHY_MALFORMED;
    if (regs < DPC_REG_COUNT)
        return DPC_WHY_TRUNCATED;
    need = DPC_SNAP_HEADER + (size_t)DPC_SNAP_ENTRY * (regs + ctls);
    if (total != need)
        return DPC_WHY_MALFORMED;
    if (len < need)
        return DPC_WHY_TRUNCATED;
    v->regs = s + DPC_SNAP_HEADER;
    v->ctls = v->regs + (size_t)DPC_SNAP_ENTRY * regs;
    v->ctl_count = ctls;
    for (i = 0; i < regs; i++)
        if (entry(v->regs, i).status > DPC_ST_FAULT)
            return DPC_WHY_MALFORMED;
    for (i = 0; i < ctls; i++)
        if (entry(v->ctls, i).status > DPC_ST_FAULT)
            return DPC_WHY_MALFORMED;
    return DPC_WHY_OK;
}

/* ---- Decision ---------------------------------------------------------------------------------------------------- */

/* DPC_WHY_OK when the reading carries a value. All-ones is what an unclocked or unpowered block returns on the
 * buses where the access completes at all (tools/survey-dpu-intf.py: an unclocked MDSS read can instead raise an
 * SError), and bit 0 of it would read as "enabled", so it is never evidence in either direction. */
static uint32_t unreadable(struct reading r)
{
    if (r.status == DPC_ST_NOT_TRIED)
        return DPC_WHY_NOT_READ;
    if (r.status == DPC_ST_FAULT)
        return DPC_WHY_READ_FAULT;
    return r.value == ALL_ONES ? DPC_WHY_ALL_ONES : DPC_WHY_OK;
}

static struct reading reg_of(const struct view *v, uint32_t reg) { return entry(v->regs, reg); }

/* The first register in order[] that cannot be read, as DPC_WHY_* with *at = its id; DPC_WHY_OK when all can. */
static uint32_t first_unreadable(const struct view *v, const uint8_t *order, uint32_t n, uint32_t *at)
{
    uint32_t i, why;

    for (i = 0; i < n; i++) {
        why = unreadable(reg_of(v, order[i]));
        if (why) {
            *at = order[i];
            return why;
        }
    }
    return DPC_WHY_OK;
}

/* The first gate in reader order that cannot be read decides UNKNOWN, and the first that reads "off" decides
 * INACTIVE: a later gate is never consulted past an earlier one, because the reader stopped there. The dispcc
 * register clock is the exception to "off means INACTIVE": with it off the gates are unreachable, not necessarily
 * off, so it can only make the verdict UNKNOWN. */
static uint32_t gates(const struct view *v, struct dpc_result *out)
{
    static const uint8_t order[GATES] = {DPC_REG_GCC_DISP_AHB, DPC_REG_GDSC, DPC_REG_AHB_CBCR, DPC_REG_MDP_CBCR};
    static const uint8_t off_why[GATES] = {DPC_WHY_DISPCC_NO_ACCESS, DPC_WHY_POWER_OFF, DPC_WHY_AHB_CLOCK_OFF,
                                           DPC_WHY_MDP_CLOCK_OFF};
    uint32_t i;

    for (i = 0; i < GATES; i++) {
        struct reading r = reg_of(v, order[i]);
        uint32_t why = unreadable(r);
        int off = order[i] == DPC_REG_GDSC ? !(r.value & DPC_GDSC_PWR_ON) : !!(r.value & DPC_CBCR_CLK_OFF);

        if (why)
            return finish(out, DPC_UNKNOWN, why, order[i], 0);
        if (off)
            return finish(out, order[i] == DPC_REG_GCC_DISP_AHB ? DPC_UNKNOWN : DPC_INACTIVE, off_why[i], order[i], 0);
    }
    return 0;
}

/* Stages below return 1 after filling *out (the verdict is decided) and 0 to let the next stage run. */
static uint32_t version(const struct dpc_params *p, const struct view *v, struct dpc_result *out, uint32_t *ev)
{
    struct reading r = reg_of(v, DPC_REG_HW_VERSION);
    uint32_t why;

    if (!p->dpu_major || (r.status == DPC_ST_NOT_TRIED && !(p->required & DPC_EV_VERSION)))
        return 0;
    why = unreadable(r);
    if (why)
        return finish(out, DPC_UNKNOWN, why, DPC_REG_HW_VERSION, *ev);
    if ((r.value >> 28) != p->dpu_major || ((r.value >> 16) & 0xfffu) != p->dpu_minor)
        return finish(out, DPC_INCONSISTENT, DPC_WHY_VERSION_MISMATCH, r.value >> 16, *ev);
    *ev |= DPC_EV_VERSION;
    return 0;
}

/* Timing-engine state, then the programmed geometry against the mode; adds TIMING and GEOMETRY to *ev. The geometry
 * follows how Linux programs a DP interface (dpu_hw_intf.c:210-224, dpu_encoder_phys_vid.c:106-123): HSYNC_CTL holds
 * the total line length, the active window is in full-line units of that length, and with DATABUS_WIDEN every
 * horizontal value counts pixel pairs. That the Windows firmware programs the same fields the same way is INFERRED. */
static uint32_t timing(const struct dpc_params *p, const struct view *v, struct dpc_result *out, uint32_t *ev)
{
    static const uint8_t regs[] = {DPC_REG_INTF_EN, DPC_REG_INTF_STATUS, DPC_REG_INTF_CONFIG, DPC_REG_INTF_HSYNC_CTL,
                                   DPC_REG_INTF_VSYNC_PERIOD, DPC_REG_INTF_DISP_V_START, DPC_REG_INTF_DISP_V_END,
                                   DPC_REG_INTF_ACT_V_START, DPC_REG_INTF_ACT_V_END, DPC_REG_INTF_DISP_HCTL,
                                   DPC_REG_INTF_ACT_HCTL, DPC_REG_INTF_CONFIG2};
    uint32_t at = 0, why = first_unreadable(v, regs, (uint32_t)sizeof regs, &at), en, st, cfg, cfg2, hctl, h0, h1;
    uint64_t ht, period, vt, vs, ve, lines, first, width, want;
    uint32_t bad_dims;

    if (why)
        return finish(out, DPC_UNKNOWN, why, at, *ev);
    en = reg_of(v, DPC_REG_INTF_EN).value & DPC_INTF_EN_BIT;
    st = reg_of(v, DPC_REG_INTF_STATUS).value & DPC_INTF_STATUS_EN;
    if (!en && !st)
        return finish(out, DPC_INACTIVE, DPC_WHY_TIMING_OFF, 0, *ev);
    if (en != st)
        return finish(out, DPC_INCONSISTENT, DPC_WHY_ENABLE_STATUS, en << 1 | st, *ev);

    cfg = reg_of(v, DPC_REG_INTF_CONFIG).value;
    cfg2 = reg_of(v, DPC_REG_INTF_CONFIG2).value;
    ht = reg_of(v, DPC_REG_INTF_HSYNC_CTL).value >> 16;
    period = reg_of(v, DPC_REG_INTF_VSYNC_PERIOD).value;
    if (!ht || !period ||
        !(reg_of(v, DPC_REG_INTF_DISP_V_START).value | reg_of(v, DPC_REG_INTF_DISP_V_END).value |
          reg_of(v, DPC_REG_INTF_ACT_V_START).value | reg_of(v, DPC_REG_INTF_ACT_V_END).value |
          reg_of(v, DPC_REG_INTF_DISP_HCTL).value | reg_of(v, DPC_REG_INTF_ACT_HCTL).value))
        return finish(out, DPC_INCONSISTENT, DPC_WHY_NO_TIMING, 0, *ev);
    if (period % ht)
        return finish(out, DPC_INCONSISTENT, DPC_WHY_TIMING_INCOHERENT, DPC_TF_PERIOD, *ev);
    vt = period / ht;

    vs = reg_of(v, DPC_REG_INTF_DISP_V_START).value;
    ve = reg_of(v, DPC_REG_INTF_DISP_V_END).value;
    if (cfg & DPC_INTF_CFG_ACTIVE_V_EN) {
        vs = reg_of(v, DPC_REG_INTF_ACT_V_START).value;
        ve = reg_of(v, DPC_REG_INTF_ACT_V_END).value;
    }
    if (ve <= vs)
        return finish(out, DPC_INCONSISTENT, DPC_WHY_TIMING_INCOHERENT, DPC_TF_V_WINDOW, *ev);
    lines = (ve - vs + ht) / ht;
    first = vs / ht;
    if (vt <= lines || first + lines > vt)
        return finish(out, DPC_INCONSISTENT, DPC_WHY_TIMING_INCOHERENT, DPC_TF_OUTSIDE_FRAME, *ev);

    hctl = (cfg & DPC_INTF_CFG_ACTIVE_H_EN) ? reg_of(v, DPC_REG_INTF_ACT_HCTL).value
                                            : reg_of(v, DPC_REG_INTF_DISP_HCTL).value;
    h0 = hctl & 0xffffu;
    h1 = hctl >> 16;
    if (h1 < h0)
        return finish(out, DPC_INCONSISTENT, DPC_WHY_TIMING_INCOHERENT, DPC_TF_H_WINDOW, *ev);
    if (h1 >= ht)
        return finish(out, DPC_INCONSISTENT, DPC_WHY_TIMING_INCOHERENT, DPC_TF_OUTSIDE_FRAME, *ev);
    width = (uint64_t)(h1 - h0) + 1u;
    *ev |= DPC_EV_TIMING;

    /* With DATABUS_WIDEN every horizontal value counts pixel pairs, so the window must be half the mode's width; a
     * window of the full width with the flag set describes twice the pixels and is a contradiction, not a match. */
    want = (cfg2 & DPC_INTF_CFG2_WIDEN) ? ((p->gop_width & 1u) ? 0u : p->gop_width / 2u) : p->gop_width;
    bad_dims = (width == want ? 0u : 1u) | (lines == p->gop_height ? 0u : 2u);
    if (bad_dims)
        return finish(out, DPC_INCONSISTENT, DPC_WHY_GEOMETRY_MISMATCH, bad_dims, *ev);
    *ev |= DPC_EV_GEOMETRY;
    return 0;
}

/* Every CTL page that was read: a bit for the expected INTF in any of them is the affirmative evidence. A contradiction
 * needs every page read and none listing the INTF: an unread or unreadable page could hold the bit, so it only blocks
 * a caller that requires routing (through the missing-evidence rule, or here for a page that was attempted and
 * failed). */
static uint32_t routing(const struct dpc_params *p, const struct view *v, struct dpc_result *out, uint32_t *ev)
{
    uint32_t i, hit = 0, readable = 0, bad = 0, bad_at = 0;

    for (i = 0; i < v->ctl_count; i++) {
        struct reading r = entry(v->ctls, i);
        uint32_t why = unreadable(r);

        if (!why) {
            readable++;
            hit |= (r.value >> p->intf_index) & 1u;
        } else if (r.status != DPC_ST_NOT_TRIED && !bad) {
            bad = why;
            bad_at = DPC_REG_COUNT + i;
        }
    }
    if (hit) {
        *ev |= DPC_EV_ROUTING;
        return 0;
    }
    if (v->ctl_count && readable == v->ctl_count)
        return finish(out, DPC_INCONSISTENT, DPC_WHY_ROUTING_MISMATCH, p->intf_index, *ev);
    if (bad && (p->required & DPC_EV_ROUTING))
        return finish(out, DPC_UNKNOWN, bad, bad_at, *ev);
    return 0;
}

/* The counter is corroboration, never a requirement by itself: a reader that found the counters disabled
 * (FRAME_LINE_COUNT_EN zero) or sampled too briefly to judge adds no evidence and no contradiction. A counter that
 * claims to run and does not move over at least two minimum frame periods, or that moves faster than the maximum
 * refresh rate allows, contradicts an enabled engine. Whether FRAME_COUNT counts frames on the Hamoa DPU is
 * VERIFIED only for Linux's own use (dpu_encoder_phys_vid.c:712-736); that the firmware enabled it is not assumed. */
static uint32_t counters(const struct dpc_params *p, const struct view *v, struct dpc_result *out, uint32_t *ev)
{
    static const uint8_t samples[2] = {DPC_REG_INTF_FRAME_A, DPC_REG_INTF_FRAME_B};
    struct reading flc = reg_of(v, DPC_REG_INTF_FLC_EN);
    uint32_t at = 0, why = unreadable(flc), delta;
    uint64_t gap = v->gap_us, frames_min_x1e6, frames_max;

    if (flc.status == DPC_ST_NOT_TRIED)
        return 0;
    if (why)
        return finish(out, DPC_UNKNOWN, why, DPC_REG_INTF_FLC_EN, *ev);
    if (!flc.value)
        return 0;
    why = first_unreadable(v, samples, 2, &at);
    if (why)
        return finish(out, DPC_UNKNOWN, why, at, *ev);
    if (!p->refresh_hz_max || !gap || gap < p->counter_gap_min_us)
        return 0;
    delta = reg_of(v, DPC_REG_INTF_FRAME_B).value - reg_of(v, DPC_REG_INTF_FRAME_A).value;
    frames_min_x1e6 = gap * p->refresh_hz_min;
    frames_max = (gap * p->refresh_hz_max + 999999u) / 1000000u + 1u;
    if (delta > frames_max)
        return finish(out, DPC_INCONSISTENT, DPC_WHY_COUNTER_FAST, delta, *ev);
    if (!delta)
        return frames_min_x1e6 >= 2000000u ? finish(out, DPC_INCONSISTENT, DPC_WHY_COUNTER_STALLED, 0, *ev) : 0;
    *ev |= DPC_EV_COUNTERS;
    return 0;
}

/* The snapshot must describe the hardware the parameters name: the same INTF, the same CTL set, and every register
 * that was attempted read at the address the parameters compute (base + offset). A reader that targeted another
 * INTF page, or an old offset table, is caught here before any value is interpreted. */
static uint32_t cross_check(const struct dpc_params *p, const struct view *v, struct dpc_result *out)
{
    uint32_t r;

    if (v->intf_index != p->intf_index)
        return finish(out, DPC_INCONSISTENT, DPC_WHY_INTF_MISMATCH, v->intf_index, 0);
    if (v->ctl_count != p->ctl_count)
        return finish(out, DPC_INCONSISTENT, DPC_WHY_ADDRESS_MISMATCH, DPC_REG_COUNT, 0);
    for (r = 0; r < DPC_REG_COUNT; r++) {
        struct reading x = reg_of(v, r);

        if (x.status != DPC_ST_NOT_TRIED && x.pa != expected_pa(p, r))
            return finish(out, DPC_INCONSISTENT, DPC_WHY_ADDRESS_MISMATCH, r, 0);
    }
    for (r = 0; r < v->ctl_count; r++) {
        struct reading x = entry(v->ctls, r);

        if (x.status != DPC_ST_NOT_TRIED && x.pa != p->ctl_base[r] + p->ctl_intf_active_off)
            return finish(out, DPC_INCONSISTENT, DPC_WHY_ADDRESS_MISMATCH, DPC_REG_COUNT + r, 0);
    }
    return 0;
}

int display_pipeline_check(const struct dpc_params *p, const void *snap, size_t snap_len, struct dpc_result *out)
{
    struct view v = {0, 0, 0, 0, 0};
    uint32_t ev = 0, why;

    if (!out)
        return -1;
    finish(out, DPC_UNKNOWN, DPC_WHY_BAD_PARAMS, DPC_PF_NULL, 0);
    if (!p)
        return 0;
    why = params_fault(p);
    if (why) {
        finish(out, DPC_UNKNOWN, DPC_WHY_BAD_PARAMS, why, 0);
        return 0;
    }
    if (!snap) {
        finish(out, DPC_UNKNOWN, DPC_WHY_TRUNCATED, 0, 0);
        return 0;
    }
    why = parse_snapshot((const uint8_t *)snap, snap_len, &v);
    if (why) {
        finish(out, DPC_UNKNOWN, why, 0, 0);
        return 0;
    }
    if (cross_check(p, &v, out) || gates(&v, out))
        return 0;
    ev |= DPC_EV_GATES;
    if (version(p, &v, out, &ev) || timing(p, &v, out, &ev) || routing(p, &v, out, &ev) ||
        counters(p, &v, out, &ev))
        return 0;
    if (p->required & ~ev) {
        finish(out, DPC_UNKNOWN, DPC_WHY_MISSING_EVIDENCE, p->required & ~ev, ev);
        return 0;
    }
    finish(out, DPC_ACTIVE, DPC_WHY_OK, 0, ev);
    return 0;
}

/* ---- Names ------------------------------------------------------------------------------------------------------- */

const char *dpc_verdict_name(uint32_t verdict)
{
    static const char *const names[] = {"unknown", "active", "inactive", "unknown", "inconsistent"};

    return verdict >= DPC_ACTIVE && verdict <= DPC_INCONSISTENT ? names[verdict] : "unknown";
}

const char *dpc_why_name(uint32_t why)
{
    static const char *const names[DPC_WHY_COUNT] = {
        "ok", "bad-params", "truncated", "malformed", "not-read", "read-fault", "all-ones", "missing-evidence",
        "dispcc-no-access", "power-off", "ahb-clock-off", "mdp-clock-off", "timing-off", "address-mismatch",
        "intf-mismatch", "version-mismatch", "enable-status", "no-timing", "timing-incoherent", "geometry-mismatch",
        "routing-mismatch", "counter-stalled", "counter-fast"};

    return why < DPC_WHY_COUNT ? names[why] : "unknown";
}

/* ---- Snapshot writer --------------------------------------------------------------------------------------------- */

size_t dpc_snapshot_init(void *buf, size_t cap, uint32_t intf_index, uint32_t ctl_count)
{
    size_t total = DPC_SNAP_HEADER + (size_t)DPC_SNAP_ENTRY * (DPC_REG_COUNT + (size_t)ctl_count), i;
    uint8_t *b = (uint8_t *)buf;

    if (!buf || ctl_count > DPC_MAX_CTL || intf_index > DPC_MAX_INTF_INDEX || cap < total)
        return 0;
    for (i = 0; i < total; i++)
        b[i] = 0;
    st32(b, DPC_SNAP_MAGIC);
    b[4] = (uint8_t)DPC_SNAP_ABI_MAJOR;
    b[6] = (uint8_t)DPC_SNAP_ABI_MINOR;
    st32(b + DPC_SNAP_OFF_TOTAL, (uint32_t)total);
    st32(b + DPC_SNAP_OFF_INTF, intf_index);
    st32(b + DPC_SNAP_OFF_REGS, DPC_REG_COUNT);
    st32(b + DPC_SNAP_OFF_CTLS, ctl_count);
    return total;
}

/* The snapshot's own header says how long it is; refuse a buffer that does not hold all of it. */
static uint8_t *writable(void *buf, size_t len, uint32_t *ctl_count)
{
    uint8_t *b = (uint8_t *)buf;

    if (!buf || len < DPC_SNAP_HEADER || ld32(b) != DPC_SNAP_MAGIC || ld32(b + DPC_SNAP_OFF_REGS) != DPC_REG_COUNT ||
        ld32(b + DPC_SNAP_OFF_CTLS) > DPC_MAX_CTL)
        return 0;
    *ctl_count = ld32(b + DPC_SNAP_OFF_CTLS);
    return len >= DPC_SNAP_HEADER + (size_t)DPC_SNAP_ENTRY * (DPC_REG_COUNT + (size_t)*ctl_count) ? b : 0;
}

static int put_entry(uint8_t *e, uint64_t pa, uint32_t value, uint32_t status)
{
    if (status > DPC_ST_FAULT)
        return -1;
    st64(e, pa);
    st32(e + 8, value);
    st32(e + 12, status);
    return 0;
}

int dpc_snapshot_put(void *buf, size_t len, uint32_t reg, uint64_t pa, uint32_t value, uint32_t status)
{
    uint32_t ctls;
    uint8_t *b = writable(buf, len, &ctls);

    return b && reg < DPC_REG_COUNT ? put_entry(b + DPC_SNAP_HEADER + (size_t)DPC_SNAP_ENTRY * reg, pa, value, status) : -1;
}

int dpc_snapshot_put_ctl(void *buf, size_t len, uint32_t ctl, uint64_t pa, uint32_t value, uint32_t status)
{
    uint32_t ctls;
    uint8_t *b = writable(buf, len, &ctls);

    return b && ctl < ctls
               ? put_entry(b + DPC_SNAP_HEADER + (size_t)DPC_SNAP_ENTRY * (DPC_REG_COUNT + (size_t)ctl), pa, value, status)
               : -1;
}

int dpc_snapshot_set_gap(void *buf, size_t len, uint32_t gap_us)
{
    uint32_t ctls;
    uint8_t *b = writable(buf, len, &ctls);

    if (!b)
        return -1;
    st32(b + DPC_SNAP_OFF_GAP, gap_us);
    return 0;
}
