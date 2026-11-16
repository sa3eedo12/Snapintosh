/* SPDX-License-Identifier: MIT */
/* The report head and the panel lines of the SP11 stick wrapper (docs/SP11-STICK-WRAPPER.md, section 3).
 *
 * The head lives in the stack as a struct sp11sb_report and is stored whole into REPORT.DAT after every stage (37 words and a clean
 * of their five cache lines): the RAM copy then names the last stage reached even if the wrapper hangs afterwards, and a restart of
 * the board that keeps RAM finds the clean lines instead of a dirty cache that died with the power. Whether RAM survives a restart
 * at all is UNKNOWN (docs/SP11-STICK-BUNDLE.md, section 6). Stores go through sp11hw_store64 only. */
#include "sp11-stick-hw.h"

#if SNAP_SP11_STICK
#include "sp11-stick-wrapper.h"

void sw_ctx_init(struct sw_ctx *c, const struct q1n1_payload_args *a, uint64_t ticks_enter)
{
    uint32_t i;
    c->a = a;
    for (i = 0; i < SP11SB_REPORT_HEAD_BYTES / 8; i++) c->head.w[i] = 0;
    c->report_pa = 0;
    c->pin_len = c->guest_len = 0;
    c->ticks_enter = ticks_enter;
    c->pin = c->guest = 0;
    c->nfiles = c->ndeclared = c->lines = c->hash_policy = 0;
}

void sw_head_flush(struct sw_ctx *c)
{
    uint32_t i;
    if (!c->report_pa) return;
    for (i = 0; i < SP11SB_REPORT_HEAD_BYTES / 8; i++) sp11hw_store64(c->report_pa + 8 * i, c->head.w[i]);
    sp11hw_clean_range(c->report_pa, SP11SB_REPORT_HEAD_BYTES);
    sp11hw_dsb_sy();
}

/* The runner's file may hold anything and the reporter requires the rest of page 0 to be zero, so the whole page is written. The tail
 * goes first: no reader can then see the magic over bytes the wrapper has not written yet. */
void sw_head_init(struct sw_ctx *c)
{
    uint32_t i;
    uint64_t off;
    for (i = 0; i < 8; i++) c->head.r.magic[i] = (uint8_t)SP11SB_REPORT_MAGIC[i];
    c->head.r.version = SP11SB_VERSION;
    c->head.r.head_size = SP11SB_REPORT_HEAD_BYTES;
    c->head.r.stage = SP11SB_STAGE_ENTERED;
    c->head.r.ticks_enter = c->ticks_enter;
    for (off = SP11SB_REPORT_HEAD_BYTES; off < SP11SB_REPORT_HEAD_PAGE; off += 8) sp11hw_store64(c->report_pa + off, 0);
    sp11hw_clean_range(c->report_pa + SP11SB_REPORT_HEAD_BYTES, SP11SB_REPORT_HEAD_PAGE - SP11SB_REPORT_HEAD_BYTES);
    sw_head_flush(c);
}

void sw_set_stage(struct sw_ctx *c, uint32_t stage)
{
    c->head.r.stage = stage;
    sw_head_flush(c);
}

void sw_begin(struct pl_line *l)
{
    pl_begin(l);
    pl_text(l, "SNAPMON ");
}

/* A line that is not essential is dropped when only the reserve is left, so the refusal and the last line always fit. */
void sw_emit(struct sw_ctx *c, const struct pl_line *l, int essential)
{
    if (c->lines >= SP11SB_WRAPPER_LINES - (essential ? 0 : SW_RESERVED_LINES)) return;
    pl_emit(c->a, l);
    c->lines++;
    c->head.r.lines_printed = c->lines;
}

void sw_say(struct sw_ctx *c, const char *text)
{
    struct pl_line l;
    sw_begin(&l);
    pl_text(&l, text);
    sw_emit(c, &l, 0);
}

void sw_say_stage(struct sw_ctx *c)
{
    struct pl_line l;
    sw_begin(&l);
    pl_text(&l, "STAGE=");
    pl_text(&l, sp11sb_stage_name(c->head.r.stage));
    sw_emit(c, &l, 0);
}

uint64_t sw_refuse(struct sw_ctx *c, uint32_t code, uint64_t detail)
{
    struct pl_line l;
    int recorded = c->report_pa != 0;
    c->head.r.refusal = code;
    c->head.r.refusal_detail = detail;
    sw_head_flush(c);
    sw_begin(&l);
    pl_text(&l, "REFUSED=");
    pl_text(&l, sp11sb_error_name(code));
    pl_text(&l, " STAGE=");
    pl_text(&l, sp11sb_stage_name(c->head.r.stage));
    sc_key_hex(c->a, &l, " DETAIL=", detail);
    sw_emit(c, &l, 1);
    sw_begin(&l);
    if (!recorded) {
        pl_text(&l, "NOTHING STORED: REPORT.DAT NOT USED YET");
    } else if (c->head.r.stage < SP11SB_STAGE_COPYING) {
        pl_text(&l, "RAM UNTOUCHED BEYOND REPORT.DAT");
    } else {
        pl_text(&l, "PLACEMENT WRITTEN");
        sc_key_dec(&l, " CHUNKS_COPIED=", c->head.r.chunks_copied);
        sc_key_dec(&l, " OF=", c->head.r.chunks_total);
    }
    sw_emit(c, &l, 1);
    sw_head_flush(c);
    return SP11SB_RET_REFUSED + code;
}
#endif /* SNAP_SP11_STICK */
