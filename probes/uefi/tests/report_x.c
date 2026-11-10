/* SPDX-License-Identifier: MIT */
#include "report_x.h"

typedef struct {
    tests_report *rep;
} builder;

static probe_sb *new_row(tests_report *rep)
{
    static probe_sb sb;

    if (rep->count >= TESTS_REPORT_MAX_ROWS) {
        rep->overflowed = 1;
        return NULL;
    }
    probe_sb_init(&sb, rep->row[rep->count], sizeof rep->row[rep->count]);
    rep->count++;
    return &sb;
}

static void put_text(tests_report *rep, const char *text)
{
    probe_sb *sb = new_row(rep);

    if (sb == NULL)
        return;
    probe_sb_puts(sb, text);
    if (sb->overflow)
        rep->overflowed = 1;
}

void tests_report_k1(const tests_result *nv2, probe_payload out)
{
    probe_sb sb;

    probe_sb_init(&sb, out, sizeof(probe_payload));
    probe_sb_puts(&sb, "K1 NV2: ");
    switch (nv2->verdict) {
    case TV_GO:
        probe_sb_puts(&sb, "GO");
        break;
    case TV_NO_GO:
        probe_sb_puts(&sb, "NO-GO (SEE GUIDE)");
        break;
    case TV_INCONCLUSIVE:
        probe_sb_puts(&sb, "INCONCLUSIVE");
        break;
    default:
        probe_sb_puts(&sb, "NOT RUN");
        break;
    }
}

void tests_report_build(tests_report *rep, const tests_result *results, int model)
{
    probe_payload k1;

    probe_memset(rep, 0, sizeof *rep);
    put_text(rep, "== SUMMARY ==");
    for (unsigned id = 1; id <= TESTS_COUNT; id++) {
        const tests_result *r = &results[id];
        probe_sb *sb = new_row(rep);

        if (sb == NULL)
            break;
        probe_sb_puts(sb, "T0");
        probe_sb_dec(sb, id);
        probe_sb_putc(sb, ' ');
        probe_sb_puts(sb, r->name != NULL ? r->name : "?");
        probe_sb_putc(sb, ' ');
        probe_sb_puts(sb, tests_verdict_name(r->verdict));
        probe_sb_putc(sb, ' ');
        probe_sb_puts(sb, tests_label_name(r->label, model));
        if (sb->overflow)
            rep->overflowed = 1;
    }
    tests_report_k1(&results[T_NV2], k1);
    put_text(rep, k1);

    for (unsigned id = 1; id <= TESTS_COUNT; id++) {
        const tests_result *r = &results[id];
        probe_sb *sb = new_row(rep);

        if (sb == NULL)
            break;
        probe_sb_puts(sb, "== T0");
        probe_sb_dec(sb, id);
        probe_sb_putc(sb, ' ');
        probe_sb_puts(sb, r->name != NULL ? r->name : "?");
        probe_sb_puts(sb, " ==");
        if (sb->overflow)
            rep->overflowed = 1;
        if (r->verdict == TV_NOT_RUN && r->nrows == 0)
            put_text(rep, "NOT RUN");
        for (unsigned i = 0; i < r->nrows; i++)
            put_text(rep, r->rows[i]);
        if (r->overflow)
            rep->overflowed = 1;
        if (r->proves != NULL)
            put_text(rep, r->proves);
        if (r->not_proves != NULL)
            put_text(rep, r->not_proves);
    }
}

void tests_report_header0(probe_payload out, const char *build_id, int model)
{
    probe_sb sb;

    probe_sb_init(&sb, out, sizeof(probe_payload));
    probe_sb_puts(&sb, "SP11 EL2 TESTS B=");
    probe_sb_puts(&sb, build_id);
    if (model)
        probe_sb_puts(&sb, " MODEL");
}

void tests_report_header1(probe_payload out, int el, unsigned page, unsigned pages)
{
    probe_sb sb;

    probe_sb_init(&sb, out, sizeof(probe_payload));
    probe_sb_puts(&sb, "CURRENTEL=");
    if (el < 0)
        probe_sb_putc(&sb, '?');
    else
        probe_sb_dec(&sb, (uint64_t)el);
    probe_sb_puts(&sb, " PAGE ");
    probe_sb_dec(&sb, page);
    probe_sb_putc(&sb, '/');
    probe_sb_dec(&sb, pages);
}

/* ---- Layout ---- */

int tests_layout_choose(uint32_t width, uint32_t height, unsigned nbody, unsigned max_pages,
                        probe_layout *out)
{
    int rc = probe_layout_choose(width, height, nbody, out);
    probe_layout g;
    uint32_t cell_w, per_page;
    unsigned pages;

    if (rc != PROBE_LAYOUT_TOO_MANY_PAGES)
        return rc;
    probe_memset(&g, 0, sizeof g);
    g.scale = PROBE_LAYOUT_MIN_SCALE;
    cell_w = PROBE_FONT_CELL_W * g.scale;
    g.margin = cell_w;
    g.col_w = PROBE_LINE_COLS * cell_w;
    g.col_gap = 2u * cell_w;
    g.pitch = (PROBE_FONT_CELL_H + 1u) * g.scale;
    if (width < 2u * g.margin + g.col_w)
        return PROBE_LAYOUT_TOO_NARROW;
    g.cols = (width - 2u * g.margin + g.col_gap) / (g.col_w + g.col_gap);
    if (g.cols > PROBE_LAYOUT_MAX_COLS)
        g.cols = PROBE_LAYOUT_MAX_COLS;
    g.total_rows = height >= 2u * g.margin ? (height - 2u * g.margin) / g.pitch : 0;
    if (g.total_rows <= PROBE_HEADER_ROWS + PROBE_FOOTER_ROWS)
        return PROBE_LAYOUT_TOO_SHORT;
    g.body_rows = g.total_rows - PROBE_HEADER_ROWS - PROBE_FOOTER_ROWS;
    per_page = g.cols * g.body_rows;
    pages = (nbody + per_page - 1u) / per_page;
    if (pages > max_pages)
        return PROBE_LAYOUT_TOO_MANY_PAGES;
    g.pages = pages;
    *out = g;
    return PROBE_LAYOUT_OK;
}

int tests_layout_fits(const probe_layout *l, uint32_t width, uint32_t height)
{
    uint64_t right, bottom;

    if (l->scale < PROBE_LAYOUT_MIN_SCALE || l->scale > PROBE_LAYOUT_MAX_SCALE || l->cols == 0 ||
        l->cols > PROBE_LAYOUT_MAX_COLS || l->body_rows == 0 || l->pages == 0)
        return -1;
    if (l->total_rows != l->body_rows + PROBE_HEADER_ROWS + PROBE_FOOTER_ROWS)
        return -1;
    right = (uint64_t)l->margin + (uint64_t)l->cols * l->col_w +
            (uint64_t)(l->cols - 1u) * l->col_gap;
    bottom = (uint64_t)l->margin + (uint64_t)l->total_rows * l->pitch;
    if (right + l->margin > width || bottom + l->margin > height)
        return -1;
    return 0;
}

unsigned tests_countdown_seconds(unsigned pages, unsigned dwell_seconds)
{
    unsigned twice = 2u * pages * dwell_seconds;

    return twice > 60u ? twice : 60u;
}

void tests_footer_running(probe_payload out, int id, const char *name)
{
    probe_sb sb;

    probe_sb_init(&sb, out, sizeof(probe_payload));
    probe_sb_puts(&sb, "RUNNING T0");
    probe_sb_dec(&sb, (uint64_t)id);
    probe_sb_putc(&sb, ' ');
    probe_sb_puts(&sb, name);
}

void tests_footer_text(probe_payload out, const char *text)
{
    probe_sb sb;

    probe_sb_init(&sb, out, sizeof(probe_payload));
    probe_sb_puts(&sb, text);
}
