/* SPDX-License-Identifier: MIT */
#include "fbtext.h"

#include "logic.h"

const char *probe_fb_status_name(int status)
{
    switch (status) {
    case PROBE_FB_OK:
        return "OK";
    case PROBE_FB_NO_BASE:
        return "NO BASE";
    case PROBE_FB_BAD_GEOMETRY:
        return "BAD GEOMETRY";
    case PROBE_FB_BAD_STRIDE:
        return "BAD STRIDE";
    case PROBE_FB_TOO_SMALL:
        return "BUFFER TOO SMALL";
    case PROBE_FB_MISALIGNED:
        return "MISALIGNED";
    case PROBE_FB_BAD_FORMAT:
        return "UNSUPPORTED FORMAT";
    default:
        return "UNKNOWN";
    }
}

int probe_fb_init(probe_fb *fb, uint64_t base, uint64_t size_bytes, uint32_t width, uint32_t height,
                  uint32_t pixels_per_scanline, uint32_t gop_format)
{
    probe_memset(fb, 0, sizeof *fb);
    if (gop_format != PROBE_PIXEL_RGBX && gop_format != PROBE_PIXEL_BGRX)
        return PROBE_FB_BAD_FORMAT;
    if (base == 0)
        return PROBE_FB_NO_BASE;
    if (width == 0 || height == 0 || width > 16384 || height > 16384)
        return PROBE_FB_BAD_GEOMETRY;
    if (pixels_per_scanline < width || pixels_per_scanline > 65536)
        return PROBE_FB_BAD_STRIDE;

    uint64_t stride = (uint64_t)pixels_per_scanline * 4;
    uint64_t needed = (uint64_t)(height - 1) * stride + (uint64_t)width * 4;
    if (size_bytes < needed)
        return PROBE_FB_TOO_SMALL;
    if (base & 3)
        return PROBE_FB_MISALIGNED;
    if (base > UINT64_MAX - needed)
        return PROBE_FB_BAD_GEOMETRY;

    fb->base = (volatile uint8_t *)(uintptr_t)base;
    fb->width = width;
    fb->height = height;
    fb->stride_bytes = (uint32_t)stride;
    fb->format = gop_format;
    return PROBE_FB_OK;
}

const char *probe_gop_status_name(int status)
{
    switch (status) {
    case PROBE_GOP_OK:
        return "OK";
    case PROBE_GOP_ABSENT:
        return "NO GOP";
    case PROBE_GOP_NOT_CONSOLE:
        return "NOT CONSOLE GOP";
    case PROBE_GOP_BAD_FB:
        return "BAD MODE";
    case PROBE_GOP_NOT_IN_MAP:
        return "NOT IN MEMORY MAP";
    case PROBE_GOP_AMBIGUOUS:
        return "AMBIGUOUS GOP";
    default:
        return "UNKNOWN";
    }
}

const char *probe_gop_rule_name(int rule)
{
    switch (rule) {
    case PROBE_GOP_RULE_NONE:
        return "NONE";
    case PROBE_GOP_RULE_CONSOLE:
        return "CONSOLE";
    case PROBE_GOP_RULE_SAME:
        return "SAME";
    default:
        return "UNKNOWN";
    }
}

int probe_gop_check(const probe_memmap *map, int is_console, uint64_t base, uint64_t size_bytes,
                    uint32_t width, uint32_t height, uint32_t pixels_per_scanline,
                    uint32_t gop_format, probe_fb *out)
{
    probe_fb trial;

    if (!is_console)
        return PROBE_GOP_NOT_CONSOLE;
    if (probe_fb_init(&trial, base, size_bytes, width, height, pixels_per_scanline, gop_format) !=
        PROBE_FB_OK)
        return PROBE_GOP_BAD_FB;
    /* probe_fb_init proved that the extent fits in size_bytes and cannot wrap. */
    if (!probe_memmap_covers(map, base, probe_fb_extent_bytes(&trial), PROBE_FB_TYPES_MASK))
        return PROBE_GOP_NOT_IN_MAP;
    *out = trial;
    return PROBE_GOP_OK;
}

static int gop_check_candidate(const probe_memmap *map, const probe_gop_candidate *c,
                               int as_console, probe_fb *out)
{
    return probe_gop_check(map, as_console, c->base, c->size_bytes, c->width, c->height,
                           c->pixels_per_scanline, c->format, out);
}

/* The size is part of the identity: two GOPs that disagree about how large the
 * framebuffer is are not shown to be one device, so they are not mirrors. */
static int gop_same_framebuffer(const probe_gop_candidate *a, const probe_gop_candidate *b)
{
    return a->base == b->base && a->size_bytes == b->size_bytes && a->width == b->width &&
           a->height == b->height && a->pixels_per_scanline == b->pixels_per_scanline &&
           a->format == b->format;
}

void probe_gop_select(const probe_memmap *map, const probe_gop_candidate *cand, size_t count,
                      probe_gop_choice *out)
{
    probe_fb trial;
    size_t first = 0;
    size_t qualifying = 0;
    int console_seen = 0;
    int differ = 0;
    int status = PROBE_GOP_ABSENT;

    probe_memset(out, 0, sizeof *out);
    out->examined = count;

    for (size_t i = 0; i < count; i++) {
        int s = gop_check_candidate(map, &cand[i], cand[i].is_console, &trial);

        if (cand[i].is_console)
            console_seen = 1;
        if (s == PROBE_GOP_OK) {
            out->status = PROBE_GOP_OK;
            out->rule = PROBE_GOP_RULE_CONSOLE;
            out->index = i;
            out->examined = i + 1;
            out->fb = trial;
            return;
        }
        if (s > status)
            status = s;
    }
    if (console_seen) {
        out->status = status;
        return;
    }

    /* The reasons of rule 1 only said "not the console"; now every candidate is
     * judged on its mode and on the memory map alone. */
    status = PROBE_GOP_ABSENT;
    for (size_t i = 0; i < count; i++) {
        int s = gop_check_candidate(map, &cand[i], 1, &trial);

        if (s != PROBE_GOP_OK) {
            if (s > status)
                status = s;
            continue;
        }
        if (qualifying++ == 0) {
            first = i;
            out->fb = trial;
        } else if (!gop_same_framebuffer(&cand[first], &cand[i])) {
            differ = 1;
        }
    }
    if (qualifying == 0) {
        out->status = status;
    } else if (differ) {
        probe_memset(&out->fb, 0, sizeof out->fb);
        out->status = PROBE_GOP_AMBIGUOUS;
    } else {
        out->status = PROBE_GOP_OK;
        out->rule = PROBE_GOP_RULE_SAME;
        out->index = first;
    }
}

uint32_t probe_fb_pack(const probe_fb *fb, uint8_t r, uint8_t g, uint8_t b)
{
    if (fb->format == PROBE_PIXEL_BGRX)
        return ((uint32_t)r << 16) | ((uint32_t)g << 8) | b;
    return ((uint32_t)b << 16) | ((uint32_t)g << 8) | r;
}

size_t probe_fb_extent_bytes(const probe_fb *fb)
{
    return (size_t)(fb->height - 1) * fb->stride_bytes + (size_t)fb->width * 4;
}

void probe_fb_fill_rect(probe_fb *fb, uint32_t x, uint32_t y, uint32_t w, uint32_t h,
                        uint32_t pixel)
{
    if (x >= fb->width || y >= fb->height || w == 0 || h == 0)
        return;
    if (w > fb->width - x)
        w = fb->width - x;
    if (h > fb->height - y)
        h = fb->height - y;

    for (uint32_t row = 0; row < h; row++) {
        volatile uint32_t *p =
            (volatile uint32_t *)(fb->base + (size_t)(y + row) * fb->stride_bytes) + x;
        for (uint32_t col = 0; col < w; col++)
            p[col] = pixel;
    }
}

void probe_fb_draw_glyph(probe_fb *fb, uint32_t x, uint32_t y, uint32_t scale, const uint8_t *glyph,
                         uint32_t fg, uint32_t bg)
{
    for (uint32_t row = 0; row < PROBE_FONT_CELL_H; row++) {
        for (uint32_t col = 0; col < PROBE_FONT_CELL_W; col++) {
            /* Column 5 is spacing and has no bit in the glyph byte. */
            int on = col < 5 && (glyph[row] & (0x80u >> col));
            probe_fb_fill_rect(fb, x + col * scale, y + row * scale, scale, scale, on ? fg : bg);
        }
    }
}

void probe_fb_draw_text(probe_fb *fb, uint32_t x, uint32_t y, uint32_t scale, const char *s,
                        uint32_t fg, uint32_t bg)
{
    for (; *s; s++, x += PROBE_FONT_CELL_W * scale)
        probe_fb_draw_glyph(fb, x, y, scale, probe_font_glyph(*s), fg, bg);
}

void probe_fb_flush_rect(probe_fb *fb, uint32_t x, uint32_t y, uint32_t w, uint32_t h)
{
    if (fb->flush == NULL || x >= fb->width || y >= fb->height || w == 0 || h == 0)
        return;
    if (w > fb->width - x)
        w = fb->width - x;
    if (h > fb->height - y)
        h = fb->height - y;
    for (uint32_t row = 0; row < h; row++)
        fb->flush(fb->flush_ctx, (const void *)(fb->base + (size_t)(y + row) * fb->stride_bytes + (size_t)x * 4),
                  (size_t)w * 4);
}

void probe_fb_flush_all(probe_fb *fb)
{
    if (fb->flush != NULL)
        fb->flush(fb->flush_ctx, (const void *)fb->base, probe_fb_extent_bytes(fb));
}

int probe_line_format(char out[PROBE_LINE_COLS + 1], const char *payload)
{
    char clean[PROBE_LINE_PAYLOAD_MAX + 1];
    size_t n = 0;
    int fixes = 0;
    int prev_space = 1; /* drops leading spaces */

    for (size_t i = 0; payload[i]; i++) {
        unsigned char c = (unsigned char)payload[i];
        if (n >= PROBE_LINE_PAYLOAD_MAX) {
            fixes++;
            break;
        }
        if (c < 0x20 || c > 0x5F || c == '|') {
            c = '?';
            fixes++;
        }
        if (c == ' ') {
            if (prev_space) {
                fixes++;
                continue;
            }
            prev_space = 1;
        } else {
            prev_space = 0;
        }
        clean[n++] = (char)c;
    }
    while (n && clean[n - 1] == ' ') {
        n--;
        fixes++;
    }
    clean[n] = 0;

    probe_sb sb;
    probe_sb_init(&sb, out, PROBE_LINE_COLS + 1);
    probe_sb_puts(&sb, clean);
    probe_sb_pad(&sb, PROBE_LINE_PAYLOAD_MAX);
    probe_sb_puts(&sb, " |");
    probe_sb_hex(&sb, probe_crc8(clean, n), 2);
    return fixes;
}

/* Geometry for one candidate scale. Returns non-zero when at least one column
 * and one body row fit. The margin is one character cell so that text stays
 * clear of rounded panel corners and bezels. */
static int geometry_for(uint32_t w, uint32_t h, uint32_t scale, probe_layout *g)
{
    uint32_t cell_w = PROBE_FONT_CELL_W * scale;

    probe_memset(g, 0, sizeof *g);
    g->scale = scale;
    g->margin = cell_w;
    g->col_w = PROBE_LINE_COLS * cell_w;
    g->col_gap = 2 * cell_w;
    g->pitch = (PROBE_FONT_CELL_H + 1) * scale;

    if (w >= 2 * g->margin + g->col_w) {
        g->cols = (w - 2 * g->margin + g->col_gap) / (g->col_w + g->col_gap);
        if (g->cols > PROBE_LAYOUT_MAX_COLS)
            g->cols = PROBE_LAYOUT_MAX_COLS;
    }
    g->total_rows = h >= 2 * g->margin ? (h - 2 * g->margin) / g->pitch : 0;
    if (g->total_rows > PROBE_HEADER_ROWS + PROBE_FOOTER_ROWS)
        g->body_rows = g->total_rows - PROBE_HEADER_ROWS - PROBE_FOOTER_ROWS;
    return g->cols != 0 && g->body_rows != 0;
}

int probe_layout_choose(uint32_t width, uint32_t height, unsigned nbody, probe_layout *out)
{
    probe_layout g;

    probe_memset(out, 0, sizeof *out);
    if (nbody == 0 || width == 0 || height == 0 || width > 65535 || height > 65535)
        return PROBE_LAYOUT_BAD_ARGS;

    for (uint32_t scale = PROBE_LAYOUT_MAX_SCALE; scale >= PROBE_LAYOUT_MIN_SCALE; scale--) {
        if (!geometry_for(width, height, scale, &g))
            continue;
        uint32_t widest = g.cols;
        for (uint32_t cols = 1; cols <= widest; cols++) {
            if ((uint64_t)cols * g.body_rows >= nbody) {
                g.cols = cols;
                g.pages = 1;
                *out = g;
                return PROBE_LAYOUT_OK;
            }
        }
    }

    if (!geometry_for(width, height, PROBE_LAYOUT_MIN_SCALE, &g))
        return g.cols == 0 ? PROBE_LAYOUT_TOO_NARROW : PROBE_LAYOUT_TOO_SHORT;
    uint32_t per_page = g.cols * g.body_rows;
    uint32_t pages = (nbody + per_page - 1) / per_page;
    if (pages > PROBE_LAYOUT_MAX_PAGES)
        return PROBE_LAYOUT_TOO_MANY_PAGES;
    g.pages = pages;
    *out = g;
    return PROBE_LAYOUT_OK;
}

int probe_layout_fits(const probe_layout *l, uint32_t width, uint32_t height)
{
    if (l->scale < PROBE_LAYOUT_MIN_SCALE || l->scale > PROBE_LAYOUT_MAX_SCALE || l->cols == 0 ||
        l->cols > PROBE_LAYOUT_MAX_COLS || l->body_rows == 0 || l->pages == 0 ||
        l->pages > PROBE_LAYOUT_MAX_PAGES)
        return -1;
    if (l->total_rows != l->body_rows + PROBE_HEADER_ROWS + PROBE_FOOTER_ROWS)
        return -1;
    uint64_t right = (uint64_t)l->margin + (uint64_t)l->cols * l->col_w +
                     (uint64_t)(l->cols - 1) * l->col_gap;
    uint64_t bottom = (uint64_t)l->margin + (uint64_t)l->total_rows * l->pitch;
    if (right + l->margin > width || bottom + l->margin > height)
        return -1;
    return 0;
}

unsigned probe_layout_page_start(const probe_layout *l, unsigned page)
{
    return page * l->cols * l->body_rows;
}

unsigned probe_layout_page_count(const probe_layout *l, unsigned nbody, unsigned page)
{
    unsigned start = probe_layout_page_start(l, page);
    unsigned per_page = l->cols * l->body_rows;
    if (start >= nbody)
        return 0;
    return nbody - start < per_page ? nbody - start : per_page;
}

static void draw_line(probe_fb *fb, const probe_layout *l, uint32_t x, uint32_t y,
                      const char *payload, int inverse)
{
    char text[PROBE_LINE_COLS + 1];
    uint32_t fg = inverse ? PROBE_COLOR_BLACK : PROBE_COLOR_WHITE;
    uint32_t bg = inverse ? PROBE_COLOR_WHITE : PROBE_COLOR_BLACK;

    probe_line_format(text, payload);
    probe_fb_draw_text(fb, x, y, l->scale, text, fg, bg);
    /* The pitch is one scaled pixel taller than a glyph cell; paint that strip
     * so an inverse-video bar is solid across two adjacent rows. */
    probe_fb_fill_rect(fb, x, y + PROBE_FONT_CELL_H * l->scale, l->col_w, l->scale, bg);
}

static uint32_t row_y(const probe_layout *l, uint32_t row)
{
    return l->margin + row * l->pitch;
}

void probe_draw_footer(probe_fb *fb, const probe_layout *l, const char *footer)
{
    uint32_t y = row_y(l, l->total_rows - 1);
    draw_line(fb, l, l->margin, y, footer, 0);
    probe_fb_flush_rect(fb, l->margin, y, l->col_w, l->pitch);
}

void probe_draw_page(probe_fb *fb, const probe_layout *l, const char *header0, const char *header1,
                     const probe_payload *body, unsigned nbody, unsigned page, const char *footer)
{
    probe_fb_fill_rect(fb, 0, 0, fb->width, fb->height, PROBE_COLOR_BLACK);
    draw_line(fb, l, l->margin, row_y(l, 0), header0, 1);
    draw_line(fb, l, l->margin, row_y(l, 1), header1, 1);

    unsigned start = probe_layout_page_start(l, page);
    unsigned count = probe_layout_page_count(l, nbody, page);
    for (unsigned i = 0; i < count; i++) {
        uint32_t col = i / l->body_rows;
        uint32_t row = i % l->body_rows;
        draw_line(fb, l, l->margin + col * (l->col_w + l->col_gap),
                  row_y(l, PROBE_HEADER_ROWS + row), body[start + i], 0);
    }
    draw_line(fb, l, l->margin, row_y(l, l->total_rows - 1), footer, 0);
    probe_fb_flush_all(fb);
}
