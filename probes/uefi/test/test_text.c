/* SPDX-License-Identifier: MIT */
/* Tests for the text-side building blocks of the SP11 EL2 probe: the CRC that
 * guards every photographed line, the bounded string builder, the font, the
 * canonical line format, page layout and the framebuffer drawing path.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "efi_types.h"
#include "fbtext.h"
#include "font6x8.h"
#include "harness.h"
#include "logic.h"

/* ---- CRC and string builder ---- */

void test_crc(void)
{
    uint8_t table[256];

    section("crc8");
    for (unsigned i = 0; i < 256; i++) {
        uint8_t c = (uint8_t)i;
        for (int b = 0; b < 8; b++)
            c = (c & 0x80) ? (uint8_t)((c << 1) ^ 0x07) : (uint8_t)(c << 1);
        table[i] = c;
    }

    /* The CRC-8/SMBUS check value from the public CRC catalogue. */
    CHECK_EQ(probe_crc8("123456789", 9), 0xF4);
    CHECK_EQ(probe_crc8("", 0), 0);
    CHECK_EQ(probe_crc8("\x01", 1), 0x07);

    for (int iter = 0; iter < 2000; iter++) {
        uint8_t buf[64];
        size_t n = rnd_below(65);
        uint8_t ref = 0;
        for (size_t i = 0; i < n; i++) {
            buf[i] = (uint8_t)rnd();
            ref = table[ref ^ buf[i]];
        }
        CHECK_EQ(probe_crc8(buf, n), ref);
    }

    /* A line is photographed and typed back by hand, so the property that
     * matters is that one wrong character is always caught. */
    for (int iter = 0; iter < 300; iter++) {
        char p[PROBE_LINE_PAYLOAD_MAX];
        size_t n = 1 + rnd_below(PROBE_LINE_PAYLOAD_MAX);
        for (size_t i = 0; i < n; i++)
            p[i] = (char)(0x20 + rnd_below(0x40));
        size_t pos = rnd_below((uint32_t)n);
        char original = p[pos];
        uint8_t before = probe_crc8(p, n);
        for (unsigned c = 0x20; c <= 0x5F; c++) {
            if (c == (unsigned char)original)
                continue;
            p[pos] = (char)c;
            CHECK(probe_crc8(p, n) != before);
        }
    }
}

void test_builder(void)
{
    char small[8];
    char big[32];
    probe_sb sb;

    section("string builder");
    probe_sb_init(&sb, small, sizeof small);
    probe_sb_puts(&sb, "1234567");
    CHECK(!sb.overflow);
    CHECK_STR(small, "1234567");
    probe_sb_putc(&sb, '8');
    CHECK(sb.overflow);
    CHECK_STR(small, "1234567");

    probe_sb_init(&sb, small, 0);
    probe_sb_putc(&sb, 'x');
    CHECK(sb.overflow);

    probe_sb_init(&sb, big, sizeof big);
    probe_sb_hex(&sb, 0xABC, 3);
    probe_sb_putc(&sb, ' ');
    probe_sb_hex(&sb, 0x1, 4);
    probe_sb_putc(&sb, ' ');
    probe_sb_hex(&sb, 0x1122334455667788ull, 16);
    CHECK_STR(big, "ABC 0001 1122334455667788");
    CHECK(!sb.overflow);

    probe_sb_init(&sb, big, sizeof big);
    probe_sb_hex(&sb, 1, 0);
    CHECK(sb.overflow);
    probe_sb_init(&sb, big, sizeof big);
    probe_sb_hex(&sb, 1, 17);
    CHECK(sb.overflow);

    probe_sb_init(&sb, big, sizeof big);
    probe_sb_dec(&sb, 0);
    probe_sb_putc(&sb, ' ');
    probe_sb_dec(&sb, UINT64_MAX);
    CHECK_STR(big, "0 18446744073709551615");

    probe_sb_init(&sb, big, sizeof big);
    probe_sb_puts(&sb, "AB");
    probe_sb_pad(&sb, 6);
    CHECK_STR(big, "AB    ");
    probe_sb_pad(&sb, 3);
    CHECK_EQ(sb.len, 6);

    probe_sb_init(&sb, small, sizeof small);
    probe_sb_pad(&sb, 100);
    CHECK(sb.overflow);
    CHECK_EQ(small[7], 0);
}

/* ---- Font ---- */

static unsigned glyph_distance(const uint8_t *a, const uint8_t *b)
{
    unsigned d = 0;

    for (int r = 0; r < PROBE_FONT_CELL_H; r++)
        d += (unsigned)__builtin_popcount((unsigned)((a[r] ^ b[r]) & 0xF8u));
    return d;
}

static int is_alnum_code(unsigned c)
{
    return (c >= '0' && c <= '9') || (c >= 'A' && c <= 'Z');
}

void test_font(void)
{
    unsigned min_all = 99;
    unsigned min_alnum = 99;

    section("font");
    for (unsigned c = 0x20; c <= 0x5F; c++) {
        const uint8_t *g = probe_font_glyph((char)c);
        CHECK(probe_font_supported((char)c));
        CHECK(!probe_font_is_tofu(g));
        for (int r = 0; r < PROBE_FONT_CELL_H; r++)
            CHECK_EQ(g[r] & 0x07, 0);
        /* The text pitch leaves one blank scaled row below every cell. Only
         * the comma tail and the underscore reach the last glyph row, so a
         * third glyph doing so would be a deliberate design change. */
        if (c == ',' || c == '_')
            CHECK(g[PROBE_FONT_CELL_H - 1] != 0);
        else
            CHECK_EQ(g[PROBE_FONT_CELL_H - 1], 0);
    }
    for (unsigned c = 'a'; c <= 'z'; c++)
        CHECK(memcmp(probe_font_glyph((char)c), probe_font_glyph((char)(c - 32)), 8) == 0);

    const unsigned char unsupported[] = {0x00, 0x01, 0x1F, 0x7F, 0x80, 0xC3, 0xFF};
    for (size_t i = 0; i < sizeof unsupported; i++) {
        CHECK(!probe_font_supported((char)unsupported[i]));
        CHECK(probe_font_is_tofu(probe_font_glyph((char)unsupported[i])));
    }
    CHECK(probe_font_supported('|'));

    for (unsigned a = 0x20; a <= 0x5F; a++) {
        for (unsigned b = a + 1; b <= 0x5F; b++) {
            unsigned d = glyph_distance(probe_font_glyph((char)a), probe_font_glyph((char)b));
            if (d < min_all)
                min_all = d;
            if (is_alnum_code(a) && is_alnum_code(b) && d < min_alnum)
                min_alnum = d;
            CHECK(d >= 1);
        }
    }
    /* The pairs that photographs of dot-matrix text confuse most. */
    static const char pairs[][2] = {{'0', 'O'}, {'1', 'I'}, {'5', 'S'}, {'8', 'B'}, {'2', 'Z'}};
    for (size_t i = 0; i < sizeof pairs / sizeof pairs[0]; i++)
        CHECK(glyph_distance(probe_font_glyph(pairs[i][0]), probe_font_glyph(pairs[i][1])) >= 3);
    CHECK(min_alnum >= 3);
    printf("     font: minimum pixel distance %u over all glyphs, %u over 0-9 A-Z\n", min_all,
           min_alnum);
}

int dump_font(void)
{
    static volatile uint32_t pixels[6 * 8];
    probe_fb fb;

    /* '|' lies outside 0x20..0x5F but is drawn between every payload and its
     * CRC, so a decoder that did not learn it could not read a single row. */
    for (unsigned c = 0x20; c <= 0x7C; c = (c == 0x5F) ? 0x7C : c + 1) {
        char text[2] = {(char)c, 0};

        for (unsigned i = 0; i < 6 * 8; i++)
            pixels[i] = 0;
        if (probe_fb_init(&fb, (uint64_t)(uintptr_t)pixels, sizeof pixels, 6, 8, 6,
                          PROBE_PIXEL_BGRX) != PROBE_FB_OK)
            return 2;
        probe_fb_draw_text(&fb, 0, 0, 1, text, PROBE_COLOR_WHITE, PROBE_COLOR_BLACK);
        printf("G %02X ", c);
        for (unsigned y = 0; y < 8; y++) {
            for (unsigned x = 0; x < 6; x++)
                putchar(pixels[y * 6 + x] ? '#' : '.');
            if (y != 7)
                putchar('/');
        }
        putchar('\n');
    }
    return 0;
}

/* The decoder in probes/uefi/host/screen.py re-derives the page geometry on
 * its own; pointing it at pages drawn for panel sizes that no emulator offers
 * (several columns, several pages) is the only way to compare the two. */
int dump_screen(int argc, char **argv)
{
    unsigned w, h, nbody, page;
    probe_layout l;
    probe_fb fb;
    probe_payload *body;
    uint32_t *pixels;
    size_t bytes;

    if (argc != 6 || sscanf(argv[2], "%u", &w) != 1 || sscanf(argv[3], "%u", &h) != 1 ||
        sscanf(argv[4], "%u", &nbody) != 1 || sscanf(argv[5], "%u", &page) != 1)
        return 2;
    if (probe_layout_choose(w, h, nbody, &l) != PROBE_LAYOUT_OK ||
        probe_layout_fits(&l, w, h) != 0 || page >= l.pages)
        return 3;
    bytes = (size_t)w * h * 4;
    pixels = calloc(1, bytes);
    body = calloc(nbody, sizeof *body);
    if (pixels == NULL || body == NULL ||
        probe_fb_init(&fb, (uint64_t)(uintptr_t)pixels, bytes, w, h, w, PROBE_PIXEL_BGRX) !=
            PROBE_FB_OK)
        return 4;
    for (unsigned i = 0; i < nbody; i++)
        snprintf(body[i], sizeof body[i], "LINE %03u 0123456789 ABCDEFGHIJ", i);
    probe_draw_page(&fb, &l, "HEADER ZERO", "HEADER ONE", body, nbody, page, "FOOTER");
    fprintf(stderr, "LAYOUT scale=%u cols=%u body_rows=%u pages=%u total_rows=%u\n", l.scale, l.cols,
            l.body_rows, l.pages, l.total_rows);
    printf("P6\n%u %u\n255\n", w, h);
    for (size_t i = 0; i < (size_t)w * h; i++) {
        putchar((pixels[i] >> 16) & 0xFF);
        putchar((pixels[i] >> 8) & 0xFF);
        putchar(pixels[i] & 0xFF);
    }
    free(body);
    free(pixels);
    return 0;
}

/* ---- Line formatting ---- */

static void payload_of(const char *line, char payload[PROBE_LINE_PAYLOAD_MAX + 1])
{
    size_t n = PROBE_LINE_PAYLOAD_MAX;

    memcpy(payload, line, n);
    while (n && payload[n - 1] == ' ')
        n--;
    payload[n] = 0;
}

void test_line_format(void)
{
    char out[PROBE_LINE_COLS + 1];
    char again[PROBE_LINE_COLS + 1];
    char payload[PROBE_LINE_PAYLOAD_MAX + 1];

    section("line format");
    CHECK_EQ(probe_line_format(out, "HELLO 123"), 0);
    CHECK_EQ(strlen(out), PROBE_LINE_COLS);
    CHECK(memcmp(out, "HELLO 123", 9) == 0);
    CHECK_EQ(out[PROBE_LINE_PAYLOAD_MAX], ' ');
    CHECK_EQ(out[PROBE_LINE_PAYLOAD_MAX + 1], '|');
    CHECK_EQU(strtoul(out + PROBE_LINE_PAYLOAD_MAX + 2, NULL, 16), probe_crc8("HELLO 123", 9));

    CHECK(probe_line_format(out, "hello") > 0);
    CHECK(memcmp(out, "?????", 5) == 0);
    CHECK(probe_line_format(out, "A|B") > 0);
    CHECK(memcmp(out, "A?B ", 4) == 0);
    CHECK(probe_line_format(out, "  A   B  ") > 0);
    CHECK(memcmp(out, "A B ", 4) == 0);
    CHECK(probe_line_format(out, "A\tB\n") > 0);
    CHECK(memcmp(out, "A?B?", 4) == 0);
    CHECK(probe_line_format(out, "\xC3\xA9") > 0);
    CHECK(memcmp(out, "??", 2) == 0);
    CHECK_EQ(probe_line_format(out, ""), 0);
    CHECK_EQ(strlen(out), PROBE_LINE_COLS);
    CHECK_EQU(strtoul(out + PROBE_LINE_PAYLOAD_MAX + 2, NULL, 16), 0);

    /* Exactly 38 characters is the widest canonical payload; 39 is cut. */
    CHECK_EQ(probe_line_format(out, "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789AB"), 0);
    CHECK(probe_line_format(out, "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789ABC") > 0);
    CHECK_EQ(strlen(out), PROBE_LINE_COLS);

    for (int iter = 0; iter < 3000; iter++) {
        char raw[80];
        size_t n = rnd_below(sizeof raw - 1);
        for (size_t i = 0; i < n; i++)
            raw[i] = (char)(1 + rnd_below(254));
        raw[n] = 0;
        probe_line_format(out, raw);
        CHECK_EQ(strlen(out), PROBE_LINE_COLS);
        payload_of(out, payload);
        CHECK_EQ(probe_line_format(again, payload), 0);
        CHECK_STR(again, out);
        CHECK_EQU(strtoul(out + PROBE_LINE_PAYLOAD_MAX + 2, NULL, 16),
                  probe_crc8(payload, strlen(payload)));
        CHECK(strchr(out, '|') == out + PROBE_LINE_PAYLOAD_MAX + 1);
    }
}

/* ---- Layout ---- */

typedef struct {
    int status;
    uint32_t scale;
    uint32_t cols;
    uint32_t pages;
} ref_layout_t;

/* Independent derivation from the documented geometry: a one-cell margin, 42
 * cells per column with a two-cell gap, and a row pitch of nine scaled
 * pixels. */
static ref_layout_t ref_layout(uint32_t w, uint32_t h, unsigned nbody)
{
    ref_layout_t r = {PROBE_LAYOUT_BAD_ARGS, 0, 0, 0};

    if (nbody == 0 || w == 0 || h == 0 || w > 65535 || h > 65535)
        return r;
    for (uint32_t s = 12; s >= 3; s--) {
        uint32_t cell = 6 * s;
        uint32_t colw = 42 * cell;
        uint32_t gap = 2 * cell;
        uint32_t pitch = 9 * s;
        if (w < 2 * cell + colw || h < 2 * cell)
            continue;
        uint32_t maxc = (w - 2 * cell + gap) / (colw + gap);
        if (maxc > 3)
            maxc = 3;
        uint32_t rows = (h - 2 * cell) / pitch;
        if (rows <= 3)
            continue;
        for (uint32_t c = 1; c <= maxc; c++) {
            if (c * (rows - 3) >= nbody) {
                r.status = 0;
                r.scale = s;
                r.cols = c;
                r.pages = 1;
                return r;
            }
        }
    }

    {
        uint32_t cell = 18;
        uint32_t colw = 42 * cell;
        uint32_t gap = 2 * cell;
        uint32_t pitch = 27;
        uint32_t maxc = w >= 2 * cell + colw ? (w - 2 * cell + gap) / (colw + gap) : 0;
        if (maxc > 3)
            maxc = 3;
        uint32_t rows = h >= 2 * cell ? (h - 2 * cell) / pitch : 0;
        uint32_t body = rows > 3 ? rows - 3 : 0;
        if (maxc == 0) {
            r.status = PROBE_LAYOUT_TOO_NARROW;
            return r;
        }
        if (body == 0) {
            r.status = PROBE_LAYOUT_TOO_SHORT;
            return r;
        }
        uint32_t pages = (nbody + maxc * body - 1) / (maxc * body);
        if (pages > 8) {
            r.status = PROBE_LAYOUT_TOO_MANY_PAGES;
            return r;
        }
        r.status = 0;
        r.scale = 3;
        r.cols = maxc;
        r.pages = pages;
    }
    return r;
}

static void check_layout_case(uint32_t w, uint32_t h, unsigned nbody)
{
    probe_layout l;
    ref_layout_t ref = ref_layout(w, h, nbody);
    int rc = probe_layout_choose(w, h, nbody, &l);

    CHECK_EQ(rc, ref.status);
    if (rc != 0 || ref.status != 0)
        return;
    CHECK_EQ(l.scale, ref.scale);
    CHECK_EQ(l.cols, ref.cols);
    CHECK_EQ(l.pages, ref.pages);
    CHECK_EQ(probe_layout_fits(&l, w, h), 0);
    CHECK(l.cols * l.body_rows * l.pages >= nbody);

    unsigned total = 0;
    for (unsigned p = 0; p < l.pages; p++)
        total += probe_layout_page_count(&l, nbody, p);
    CHECK_EQ(total, nbody);
    CHECK_EQ(probe_layout_page_count(&l, nbody, l.pages), 0);
}

void test_layout(void)
{
    static const uint32_t widths[] = {1,    100,  251,  252,  253,  300,   500,   640,  800,
                                      1024, 1280, 1366, 1920, 2560, 2880,  3840,  5000, 65535,
                                      65536};
    static const uint32_t heights[] = {1,   50,   100,  200,  300,  480,  600,  768,
                                       800, 1080, 1200, 1920, 2160, 4000, 65535};
    static const unsigned bodies[] = {0, 1, 2, 17, 40, 41, 80, 96};
    probe_layout l;

    section("layout");
    for (size_t wi = 0; wi < sizeof widths / sizeof widths[0]; wi++)
        for (size_t hi = 0; hi < sizeof heights / sizeof heights[0]; hi++)
            for (size_t bi = 0; bi < sizeof bodies / sizeof bodies[0]; bi++)
                check_layout_case(widths[wi], heights[hi], bodies[bi]);
    for (int iter = 0; iter < 3000; iter++)
        check_layout_case(1 + rnd_below(4000), 1 + rnd_below(3000), 1 + rnd_below(120));

    /* fits() is the last gate before drawing, so its boundaries are exact. */
    CHECK_EQ(probe_layout_choose(800, 600, 17, &l), 0);
    uint32_t need_w = 2 * l.margin + l.cols * l.col_w + (l.cols - 1) * l.col_gap;
    uint32_t need_h = 2 * l.margin + l.total_rows * l.pitch;
    CHECK_EQ(probe_layout_fits(&l, need_w, need_h), 0);
    CHECK_EQ(probe_layout_fits(&l, need_w - 1, need_h), -1);
    CHECK_EQ(probe_layout_fits(&l, need_w, need_h - 1), -1);

    probe_layout bad = l;
    bad.scale = 2;
    CHECK_EQ(probe_layout_fits(&bad, 4000, 4000), -1);
    bad = l;
    bad.scale = 13;
    CHECK_EQ(probe_layout_fits(&bad, 4000, 4000), -1);
    bad = l;
    bad.cols = 0;
    CHECK_EQ(probe_layout_fits(&bad, 4000, 4000), -1);
    bad = l;
    bad.cols = 4;
    CHECK_EQ(probe_layout_fits(&bad, 4000, 4000), -1);
    bad = l;
    bad.pages = 0;
    CHECK_EQ(probe_layout_fits(&bad, 4000, 4000), -1);
    bad = l;
    bad.pages = 9;
    CHECK_EQ(probe_layout_fits(&bad, 4000, 4000), -1);
    bad = l;
    bad.body_rows = 0;
    CHECK_EQ(probe_layout_fits(&bad, 4000, 4000), -1);
    bad = l;
    bad.total_rows += 1;
    CHECK_EQ(probe_layout_fits(&bad, 4000, 4000), -1);
}

/* ---- Framebuffer ---- */

typedef struct {
    uint64_t addr[512];
    size_t len[512];
    unsigned n;
    unsigned dropped;
} flush_log;

static void flush_hook(void *ctx, const void *addr, size_t len)
{
    flush_log *log = ctx;

    if (log->n < 512) {
        log->addr[log->n] = (uint64_t)(uintptr_t)addr;
        log->len[log->n] = len;
        log->n++;
    } else {
        log->dropped++;
    }
}

static void test_fb_init(void)
{
    uint32_t cell[16];
    probe_fb fb;
    uint64_t b = (uint64_t)(uintptr_t)cell;

    CHECK_EQ(probe_fb_init(&fb, b, 16, 2, 2, 2, PROBE_PIXEL_BGRX), PROBE_FB_OK);
    CHECK_EQ(probe_fb_init(&fb, b, 16, 2, 2, 2, PROBE_PIXEL_RGBX), PROBE_FB_OK);
    CHECK_EQ(probe_fb_init(&fb, b, 15, 2, 2, 2, PROBE_PIXEL_BGRX), PROBE_FB_TOO_SMALL);
    CHECK_EQ(probe_fb_init(&fb, b, 24, 2, 2, 3, PROBE_PIXEL_BGRX), PROBE_FB_OK);
    CHECK_EQ(probe_fb_init(&fb, b, 19, 2, 2, 3, PROBE_PIXEL_BGRX), PROBE_FB_TOO_SMALL);
    CHECK_EQ(probe_fb_init(&fb, b, 64, 2, 2, 2, 2), PROBE_FB_BAD_FORMAT);
    CHECK_EQ(probe_fb_init(&fb, b, 64, 2, 2, 2, 3), PROBE_FB_BAD_FORMAT);
    CHECK_EQ(probe_fb_init(&fb, b, 64, 2, 2, 2, 0xFFFFFFFFu), PROBE_FB_BAD_FORMAT);
    CHECK_EQ(probe_fb_init(&fb, 0, 64, 2, 2, 2, PROBE_PIXEL_BGRX), PROBE_FB_NO_BASE);
    CHECK_EQ(probe_fb_init(&fb, b, 64, 0, 2, 2, PROBE_PIXEL_BGRX), PROBE_FB_BAD_GEOMETRY);
    CHECK_EQ(probe_fb_init(&fb, b, 64, 2, 0, 2, PROBE_PIXEL_BGRX), PROBE_FB_BAD_GEOMETRY);
    CHECK_EQ(probe_fb_init(&fb, b, 64, 16385, 2, 16385, PROBE_PIXEL_BGRX), PROBE_FB_BAD_GEOMETRY);
    CHECK_EQ(probe_fb_init(&fb, b, 64, 2, 16385, 2, PROBE_PIXEL_BGRX), PROBE_FB_BAD_GEOMETRY);
    CHECK_EQ(probe_fb_init(&fb, b, 64, 4, 2, 3, PROBE_PIXEL_BGRX), PROBE_FB_BAD_STRIDE);
    CHECK_EQ(probe_fb_init(&fb, b, 64, 2, 2, 65537, PROBE_PIXEL_BGRX), PROBE_FB_BAD_STRIDE);
    CHECK_EQ(probe_fb_init(&fb, b + 1, 64, 2, 2, 2, PROBE_PIXEL_BGRX), PROBE_FB_MISALIGNED);
    CHECK_EQ(probe_fb_init(&fb, b + 2, 64, 2, 2, 2, PROBE_PIXEL_BGRX), PROBE_FB_MISALIGNED);
    CHECK_EQ(probe_fb_init(&fb, 0xFFFFFFFFFFFFFFF0ull, 1ull << 40, 100, 100, 100, PROBE_PIXEL_BGRX),
             PROBE_FB_BAD_GEOMETRY);
    CHECK(fb.base == NULL);
    CHECK_STR(probe_fb_status_name(PROBE_FB_TOO_SMALL), "BUFFER TOO SMALL");
}

/* Counts non-sentinel bytes in the inter-scanline padding. */
static unsigned padding_damage(const uint8_t *buf, uint32_t w, uint32_t h, uint32_t stride_px)
{
    unsigned damaged = 0;

    for (uint32_t y = 0; y + 1 < h; y++)
        for (size_t x = (size_t)w * 4; x < (size_t)stride_px * 4; x++)
            damaged += buf[(size_t)y * stride_px * 4 + x] != 0xA5;
    return damaged;
}

static unsigned bad_pixels(const uint8_t *buf, uint32_t w, uint32_t h, uint32_t stride_px)
{
    unsigned bad = 0;

    for (uint32_t y = 0; y < h; y++) {
        for (uint32_t x = 0; x < w; x++) {
            uint32_t px;
            memcpy(&px, buf + ((size_t)y * stride_px + x) * 4, 4);
            bad += px != 0 && px != 0x00FFFFFFu;
        }
    }
    return bad;
}

static void render_case(uint32_t w, uint32_t h, uint32_t stride_px, unsigned nbody)
{
    size_t needed = (size_t)(h - 1) * stride_px * 4 + (size_t)w * 4;
    uint8_t *buf = xalloc(needed);
    uint8_t *before = xalloc(needed);
    probe_fb fb;
    probe_layout l;
    probe_payload *body = xalloc(sizeof(probe_payload) * nbody);
    flush_log *log = xalloc(sizeof *log);

    memset(buf, 0xA5, needed);
    CHECK_EQ(probe_fb_init(&fb, (uint64_t)(uintptr_t)buf, needed, w, h, stride_px, PROBE_PIXEL_BGRX),
             PROBE_FB_OK);
    CHECK_EQ(probe_layout_choose(w, h, nbody, &l), 0);
    CHECK_EQ(probe_layout_fits(&l, w, h), 0);
    for (unsigned i = 0; i < nbody; i++)
        snprintf(body[i], sizeof body[i], "LINE %u ABCDEFGHIJKLMNOPQRSTUVWXYZ", i);

    for (unsigned page = 0; page < l.pages; page++) {
        probe_draw_page(&fb, &l, "HEADER ZERO", "HEADER ONE", body, nbody, page, "FOOTER");
        CHECK_EQ(padding_damage(buf, w, h, stride_px), 0);
        CHECK_EQ(bad_pixels(buf, w, h, stride_px), 0);
    }

    fb.flush = flush_hook;
    fb.flush_ctx = log;
    probe_draw_page(&fb, &l, "HEADER ZERO", "HEADER ONE", body, nbody, 0, "ABCDEFGHIJKLMNOPQRSTUVWXYZ");
    CHECK_EQ(log->n, 1);
    CHECK_EQU(log->addr[0], (uint64_t)(uintptr_t)buf);
    CHECK_EQ(log->len[0], needed);

    /* The countdown redraws only the footer, so every pixel it changes must be
     * inside a range that was flushed, and no flushed range may leave the
     * footer band or run past the framebuffer. */
    memcpy(before, buf, needed);
    log->n = 0;
    probe_draw_footer(&fb, &l, "RESET IN 7 S");
    CHECK(log->n > 0);
    CHECK_EQ(log->dropped, 0);
    uint32_t band_y0 = l.margin + (l.total_rows - 1) * l.pitch;
    for (unsigned i = 0; i < log->n; i++) {
        uint64_t off = log->addr[i] - (uint64_t)(uintptr_t)buf;
        CHECK(log->addr[i] >= (uint64_t)(uintptr_t)buf);
        CHECK(off + log->len[i] <= needed);
        uint64_t row = off / ((uint64_t)stride_px * 4);
        uint64_t col = off % ((uint64_t)stride_px * 4);
        CHECK(row >= band_y0 && row < band_y0 + l.pitch);
        CHECK(col >= (uint64_t)l.margin * 4 && col + log->len[i] <= (uint64_t)(l.margin + l.col_w) * 4);
    }
    unsigned changed = 0;
    unsigned uncovered = 0;
    for (uint32_t y = 0; y < h; y++) {
        for (uint32_t x = 0; x < w; x++) {
            size_t off = ((size_t)y * stride_px + x) * 4;
            if (memcmp(buf + off, before + off, 4) == 0)
                continue;
            changed++;
            int covered = 0;
            for (unsigned i = 0; i < log->n; i++) {
                uint64_t a = (uint64_t)(uintptr_t)buf + off;
                covered |= a >= log->addr[i] && a + 4 <= log->addr[i] + log->len[i];
            }
            uncovered += !covered;
        }
    }
    CHECK(changed > 0);
    CHECK_EQ(uncovered, 0);
    CHECK_EQ(padding_damage(buf, w, h, stride_px), 0);

    free(log);
    free(body);
    free(before);
    free(buf);
}

void test_framebuffer(void)
{
    section("framebuffer");
    test_fb_init();

    render_case(800, 600, 800, 17);
    render_case(800, 600, 832, 40);
    render_case(1024, 768, 1024, 96);
    render_case(1366, 768, 1376, 60);
    render_case(2880, 1920, 2880, 100);
    render_case(800, 480, 864, 30);

    /* Clipping at the right and bottom edges of an exact-size buffer. */
    {
        uint32_t w = 37;
        uint32_t h = 23;
        uint32_t stride_px = 41;
        size_t needed = (size_t)(h - 1) * stride_px * 4 + (size_t)w * 4;
        uint8_t *buf = xalloc(needed);
        probe_fb fb;
        memset(buf, 0xA5, needed);
        CHECK_EQ(probe_fb_init(&fb, (uint64_t)(uintptr_t)buf, needed, w, h, stride_px,
                               PROBE_PIXEL_RGBX), 0);
        probe_fb_fill_rect(&fb, 0xFFFFFFF0u, 0, 10, 10, 0x00FFFFFFu);
        probe_fb_fill_rect(&fb, 0, 0xFFFFFFF0u, 10, 10, 0x00FFFFFFu);
        probe_fb_fill_rect(&fb, w, 0, 5, 5, 0x00FFFFFFu);
        probe_fb_fill_rect(&fb, 0, h, 5, 5, 0x00FFFFFFu);
        probe_fb_fill_rect(&fb, 0, 0, 0, 5, 0x00FFFFFFu);
        CHECK_EQ(padding_damage(buf, w, h, stride_px), 0);
        CHECK_EQ(buf[0], 0xA5);
        probe_fb_fill_rect(&fb, w - 1, h - 1, 0xFFFFFFFFu, 0xFFFFFFFFu, 0x00FFFFFFu);
        uint32_t last;
        memcpy(&last, buf + ((size_t)(h - 1) * stride_px + (w - 1)) * 4, 4);
        CHECK_EQU(last, 0x00FFFFFFu);
        CHECK_EQ(padding_damage(buf, w, h, stride_px), 0);
        probe_fb_draw_text(&fb, w - 10, h - 5, 4, "ABC", 0x00FFFFFFu, 0);
        probe_fb_draw_text(&fb, 0, 0, 12, "WWWW", 0x00FFFFFFu, 0);
        probe_fb_draw_text(&fb, 0xFFFFFF00u, 0xFFFFFF00u, 12, "WWWW", 0x00FFFFFFu, 0);
        CHECK_EQ(padding_damage(buf, w, h, stride_px), 0);
        fb.flush = NULL;
        probe_fb_flush_rect(&fb, 0, 0, w, h);
        probe_fb_flush_all(&fb);
        CHECK_EQU(probe_fb_pack(&fb, 0x11, 0x22, 0x33), 0x00332211u);
        fb.format = PROBE_PIXEL_BGRX;
        CHECK_EQU(probe_fb_pack(&fb, 0x11, 0x22, 0x33), 0x00112233u);
        free(buf);
    }
}

/* ---- Framebuffer selection policy and cache maintenance ---- */

#define GOP_BASE 0x7C7A0000ull
#define GOP_W 1024u
#define GOP_H 600u
#define GOP_STRIDE 1024u
#define GOP_EXTENT ((uint64_t)(GOP_H - 1) * GOP_STRIDE * 4 + (uint64_t)GOP_W * 4)
#define GOP_PAGES ((GOP_EXTENT + 4095) / 4096)

static int gop_with(const probe_memmap *map, int console, uint64_t base, uint32_t format,
                    probe_fb *out)
{
    return probe_gop_check(map, console, base, GOP_EXTENT, GOP_W, GOP_H, GOP_STRIDE, format, out);
}

static int gop_with_one_desc(uint32_t type, int console)
{
    uint8_t *map = xalloc(48);
    probe_memmap mm;
    probe_fb out;
    int rc;

    put_desc(map, 48, 0, type, GOP_BASE, GOP_PAGES);
    CHECK_EQ(probe_memmap_init(&mm, map, 48, 48, 48), PROBE_MM_OK);
    rc = gop_with(&mm, console, GOP_BASE, PROBE_PIXEL_BGRX, &out);
    free(map);
    return rc;
}

static struct {
    uint64_t calls[512];
    unsigned n;       /* calls recorded (the first 512) */
    uint64_t total;   /* calls made */
} g_walk;

static void walk_op(uint64_t va)
{
    if (g_walk.n < sizeof g_walk.calls / sizeof g_walk.calls[0])
        g_walk.calls[g_walk.n++] = va;
    /* A loop bounded by an address comparison runs on for ever at the top of the
     * address space; fail loudly instead of hanging the suite. */
    if (++g_walk.total > (1ull << 23)) {
        fputs("cache maintenance walk did not terminate\n", stderr);
        exit(3);
    }
}

static uint64_t walk(uint64_t addr, uint64_t len, uint64_t line)
{
    memset(&g_walk, 0, sizeof g_walk);
    return probe_cache_walk(addr, len, line, walk_op);
}

void test_gop_policy(void)
{
    uint8_t *map;
    probe_memmap mm;
    probe_fb out;

    section("framebuffer selection policy");
    /* The console-owned GOP whose whole range is a firmware-owned type passes. */
    CHECK_EQ(gop_with_one_desc(0, 1), PROBE_GOP_OK);
    CHECK_EQ(gop_with_one_desc(4, 1), PROBE_GOP_OK);
    CHECK_EQ(gop_with_one_desc(6, 1), PROBE_GOP_OK);
    CHECK_EQ(gop_with_one_desc(11, 1), PROBE_GOP_OK);
    CHECK_EQ(gop_with_one_desc(12, 1), PROBE_GOP_OK);
    /* Everything else is somebody else's memory, including this image's own heap. */
    CHECK_EQ(gop_with_one_desc(1, 1), PROBE_GOP_NOT_IN_MAP);
    CHECK_EQ(gop_with_one_desc(2, 1), PROBE_GOP_NOT_IN_MAP);
    CHECK_EQ(gop_with_one_desc(3, 1), PROBE_GOP_NOT_IN_MAP);
    CHECK_EQ(gop_with_one_desc(5, 1), PROBE_GOP_NOT_IN_MAP);
    CHECK_EQ(gop_with_one_desc(7, 1), PROBE_GOP_NOT_IN_MAP);
    CHECK_EQ(gop_with_one_desc(8, 1), PROBE_GOP_NOT_IN_MAP);
    CHECK_EQ(gop_with_one_desc(9, 1), PROBE_GOP_NOT_IN_MAP);
    CHECK_EQ(gop_with_one_desc(10, 1), PROBE_GOP_NOT_IN_MAP);
    CHECK_EQ(gop_with_one_desc(13, 1), PROBE_GOP_NOT_IN_MAP);
    CHECK_EQ(gop_with_one_desc(14, 1), PROBE_GOP_NOT_IN_MAP);
    /* An unidentified GOP is never drawn into, however plausible it looks. */
    CHECK_EQ(gop_with_one_desc(0, 0), PROBE_GOP_NOT_CONSOLE);
    CHECK_EQ(gop_with_one_desc(11, 0), PROBE_GOP_NOT_CONSOLE);

    map = xalloc(48 * 3);
    {
        int ok_two_types;
        int tail_conventional;
        int gap;
        int overlap;

        /* One framebuffer spread over two allowed descriptors is fine. */
        put_desc(map, 48, 0, 0, GOP_BASE, GOP_PAGES / 2);
        put_desc(map, 48, 1, 11, GOP_BASE + (GOP_PAGES / 2) * 4096, GOP_PAGES - GOP_PAGES / 2);
        CHECK_EQ(probe_memmap_init(&mm, map, 96, 144, 48), PROBE_MM_OK);
        ok_two_types = gop_with(&mm, 1, GOP_BASE, PROBE_PIXEL_BGRX, &out);
        CHECK_EQ(ok_two_types, PROBE_GOP_OK);

        /* The tail falls into conventional memory. */
        put_desc(map, 48, 1, 7, GOP_BASE + (GOP_PAGES / 2) * 4096, GOP_PAGES - GOP_PAGES / 2);
        CHECK_EQ(probe_memmap_init(&mm, map, 96, 144, 48), PROBE_MM_OK);
        tail_conventional = gop_with(&mm, 1, GOP_BASE, PROBE_PIXEL_BGRX, &out);
        CHECK_EQ(tail_conventional, PROBE_GOP_NOT_IN_MAP);

        /* A hole in the middle of the described range. */
        put_desc(map, 48, 0, 0, GOP_BASE, GOP_PAGES / 2 - 1);
        put_desc(map, 48, 1, 0, GOP_BASE + (GOP_PAGES / 2) * 4096, GOP_PAGES - GOP_PAGES / 2);
        CHECK_EQ(probe_memmap_init(&mm, map, 96, 144, 48), PROBE_MM_OK);
        gap = gop_with(&mm, 1, GOP_BASE, PROBE_PIXEL_BGRX, &out);
        CHECK_EQ(gap, PROBE_GOP_NOT_IN_MAP);

        /* The map describes the first page twice, once as reserved and once as
         * conventional: the first matching descriptor must not win. */
        put_desc(map, 48, 0, 0, GOP_BASE, GOP_PAGES);
        put_desc(map, 48, 1, 7, GOP_BASE, 1);
        CHECK_EQ(probe_memmap_init(&mm, map, 96, 144, 48), PROBE_MM_OK);
        overlap = gop_with(&mm, 1, GOP_BASE, PROBE_PIXEL_BGRX, &out);
        CHECK_EQ(overlap, PROBE_GOP_NOT_IN_MAP);
        put_desc(map, 48, 0, 7, GOP_BASE, 1);
        put_desc(map, 48, 1, 0, GOP_BASE, GOP_PAGES);
        CHECK_EQ(probe_memmap_init(&mm, map, 96, 144, 48), PROBE_MM_OK);
        CHECK_EQ(gop_with(&mm, 1, GOP_BASE, PROBE_PIXEL_BGRX, &out), PROBE_GOP_NOT_IN_MAP);
    }

    /* One page short of the extent. */
    put_desc(map, 48, 0, 0, GOP_BASE, GOP_PAGES - 1);
    CHECK_EQ(probe_memmap_init(&mm, map, 48, 144, 48), PROBE_MM_OK);
    CHECK_EQ(gop_with(&mm, 1, GOP_BASE, PROBE_PIXEL_BGRX, &out), PROBE_GOP_NOT_IN_MAP);
    /* Exactly the extent rounded up to pages. */
    put_desc(map, 48, 0, 0, GOP_BASE, GOP_PAGES);
    CHECK_EQ(probe_memmap_init(&mm, map, 48, 144, 48), PROBE_MM_OK);
    CHECK_EQ(gop_with(&mm, 1, GOP_BASE, PROBE_PIXEL_BGRX, &out), PROBE_GOP_OK);
    CHECK(out.base == (volatile uint8_t *)(uintptr_t)GOP_BASE);
    CHECK_EQ(out.stride_bytes, GOP_STRIDE * 4);
    CHECK_EQ(out.format, PROBE_PIXEL_BGRX);
    /* A base elsewhere in the map (RAM) is refused even though the mode is sane. */
    CHECK_EQ(gop_with(&mm, 1, GOP_BASE + 0x100000, PROBE_PIXEL_BGRX, &out), PROBE_GOP_NOT_IN_MAP);
    CHECK_EQ(gop_with(&mm, 1, GOP_BASE - 0x1000, PROBE_PIXEL_BGRX, &out), PROBE_GOP_NOT_IN_MAP);

    /* Mode problems are refused before the map is consulted. */
    CHECK_EQ(gop_with(&mm, 1, 0, PROBE_PIXEL_BGRX, &out), PROBE_GOP_BAD_FB);
    CHECK_EQ(gop_with(&mm, 1, GOP_BASE + 2, PROBE_PIXEL_BGRX, &out), PROBE_GOP_BAD_FB);
    CHECK_EQ(gop_with(&mm, 1, GOP_BASE, 2, &out), PROBE_GOP_BAD_FB);
    CHECK_EQ(gop_with(&mm, 1, GOP_BASE, 3, &out), PROBE_GOP_BAD_FB);
    CHECK_EQ(probe_gop_check(&mm, 1, GOP_BASE, GOP_EXTENT - 1, GOP_W, GOP_H, GOP_STRIDE,
                             PROBE_PIXEL_BGRX, &out), PROBE_GOP_BAD_FB);
    CHECK_EQ(probe_gop_check(&mm, 1, 0xFFFFFFFFFFFFFFF0ull, 1ull << 40, GOP_W, GOP_H, GOP_STRIDE,
                             PROBE_PIXEL_BGRX, &out), PROBE_GOP_BAD_FB);

    /* No map at all: nothing is usable. */
    {
        probe_memmap empty;

        memset(&empty, 0, sizeof empty);
        CHECK_EQ(gop_with(&empty, 1, GOP_BASE, PROBE_PIXEL_BGRX, &out), PROBE_GOP_NOT_IN_MAP);
        CHECK_EQ(gop_with(NULL, 1, GOP_BASE, PROBE_PIXEL_BGRX, &out), PROBE_GOP_NOT_IN_MAP);
    }

    /* A refusal leaves the caller's framebuffer untouched. */
    memset(&out, 0x5A, sizeof out);
    {
        probe_fb sentinel = out;

        CHECK_EQ(gop_with(&mm, 0, GOP_BASE, PROBE_PIXEL_BGRX, &out), PROBE_GOP_NOT_CONSOLE);
        CHECK_EQ(gop_with(&mm, 1, GOP_BASE + 0x100000, PROBE_PIXEL_BGRX, &out),
                 PROBE_GOP_NOT_IN_MAP);
        CHECK_EQ(memcmp(&sentinel, &out, sizeof out), 0);
    }
    free(map);

    CHECK_STR(probe_gop_status_name(PROBE_GOP_OK), "OK");
    CHECK_STR(probe_gop_status_name(PROBE_GOP_ABSENT), "NO GOP");
    CHECK_STR(probe_gop_status_name(PROBE_GOP_NOT_CONSOLE), "NOT CONSOLE GOP");
    CHECK_STR(probe_gop_status_name(PROBE_GOP_BAD_FB), "BAD MODE");
    CHECK_STR(probe_gop_status_name(PROBE_GOP_NOT_IN_MAP), "NOT IN MEMORY MAP");
    CHECK_STR(probe_gop_status_name(99), "UNKNOWN");
    /* The refusals are ranked by how much they say. */
    CHECK(PROBE_GOP_OK < PROBE_GOP_ABSENT && PROBE_GOP_ABSENT < PROBE_GOP_NOT_CONSOLE &&
          PROBE_GOP_NOT_CONSOLE < PROBE_GOP_BAD_FB && PROBE_GOP_BAD_FB < PROBE_GOP_NOT_IN_MAP);
}

void test_cache_walk(void)
{
    const uint64_t top = 0xFFFFFFFFFFFFFFFFull;

    section("cache maintenance walk");
    /* Plain ranges: one call per line touched, line-aligned, in order. */
    CHECK_EQU(walk(0x1000, 64, 64), 1);
    CHECK_EQU(g_walk.calls[0], 0x1000);
    CHECK_EQU(walk(0x1001, 64, 64), 2);
    CHECK_EQU(g_walk.calls[0], 0x1000);
    CHECK_EQU(g_walk.calls[1], 0x1040);
    CHECK_EQU(walk(0x103F, 1, 64), 1);
    CHECK_EQU(g_walk.calls[0], 0x1000);
    CHECK_EQU(walk(0x103F, 2, 64), 2);
    CHECK_EQU(walk(0x2000, 0x1000, 64), 64);
    CHECK_EQU(g_walk.calls[63], 0x2000 + 63 * 64);
    CHECK_EQU(walk(0x2000, 0x1000, 128), 32);

    /* Refusals make no call at all. */
    CHECK_EQU(walk(0x1000, 0, 64), 0);
    CHECK_EQU(g_walk.n, 0);
    CHECK_EQU(walk(0x1000, 64, 0), 0);
    CHECK_EQU(walk(0x1000, 64, 8), 0);
    CHECK_EQU(walk(0x1000, 64, 48), 0);
    CHECK_EQU(walk(0x1000, 64, 8192), 0);
    CHECK_EQU(g_walk.n, 0);
    CHECK_EQU(probe_cache_walk(0x1000, 64, 64, NULL), 0);
    CHECK_EQU(walk(0x1000, PROBE_CACHE_MAX_BYTES + 1, 64), 0);
    CHECK_EQU(g_walk.n, 0);
    CHECK_EQU(walk(0, PROBE_CACHE_MAX_BYTES, 64), PROBE_CACHE_MAX_BYTES / 64);
    CHECK_EQU(g_walk.total, PROBE_CACHE_MAX_BYTES / 64);

    /* At the top of the address space the range is clipped, and the loop ends:
     * an address comparison would wrap and run for ever here. The range
     * (top - 7, 4 bytes) ends inside the last cache line without wrapping,
     * which is the input that sends an "address < end" loop around to zero. */
    CHECK_EQU(walk(top - 7, 4, 64), 1);
    CHECK_EQU(g_walk.calls[0], top & ~63ull);
    CHECK_EQU(walk(top - 0xF, 0x100, 64), 1);
    CHECK_EQU(g_walk.calls[0], top & ~63ull);
    CHECK_EQU(walk(top, 1, 64), 1);
    CHECK_EQU(g_walk.calls[0], top & ~63ull);
    CHECK_EQU(walk(top, 2, 64), 1);
    CHECK_EQU(walk(top - 63, 64, 64), 1);
    CHECK_EQU(walk(top - 64, 64, 64), 2);
    CHECK_EQU(g_walk.calls[0], (top & ~63ull) - 64);
    CHECK_EQU(g_walk.calls[1], top & ~63ull);
    CHECK_EQU(walk(top - 127, 128, 64), 2);
    CHECK_EQU(g_walk.calls[0], (top & ~63ull) - 64);
    CHECK_EQU(g_walk.calls[1], top & ~63ull);
    CHECK_EQU(walk(top - 127, PROBE_CACHE_MAX_BYTES, 64), 2);
    CHECK_EQU(walk(0x1000, UINT64_MAX, 64), 0);

    /* Property: the count is exact and the first and last calls are the first
     * and last lines of the (clipped) range. */
    for (int i = 0; i < 2000; i++) {
        uint64_t line = 16ull << rnd_below(9);
        uint64_t addr = rnd();
        uint64_t len = 1 + (rnd() % 5000);
        uint64_t n;
        unsigned __int128 last;
        unsigned __int128 want_first;
        unsigned __int128 want_last;

        if (i % 3 == 0)
            addr = top - (rnd() % 3000);
        n = walk(addr, len, line);
        last = (unsigned __int128)addr + len - 1;
        if (last > top)
            last = top;
        want_first = addr & ~(line - 1);
        want_last = last & ~(unsigned __int128)(line - 1);
        CHECK(n >= 1);
        CHECK_EQU(n, (uint64_t)((want_last - want_first) / line + 1));
        CHECK_EQU(g_walk.total, n);
        CHECK(g_walk.n >= 1 && g_walk.calls[0] == (uint64_t)want_first);
        CHECK(g_walk.calls[g_walk.n - 1] == (uint64_t)(want_first + (g_walk.n - 1) * line));
    }
}
