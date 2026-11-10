/* SPDX-License-Identifier: MIT */
/* Framebuffer text for the SP11 EL2 probe, as pure code.
 *
 * After ExitBootServices the GOP framebuffer is the probe's only output, and
 * the operator reads it from a photograph. Three properties follow:
 *   - every write is clipped to the geometry the firmware reported, because
 *     a stray store into device memory can hang the machine silently;
 *   - text is integer-scaled and monochrome for contrast;
 *   - every line ends in a CRC-8, so a mis-read digit in the photo is caught
 *     by tools/verify-lines style checking instead of becoming a wrong fact.
 *
 * The framebuffer is touched only through probe_fb, which a host test backs
 * with an exactly-sized buffer under AddressSanitizer.
 */
#ifndef PROBE_FBTEXT_H
#define PROBE_FBTEXT_H

#include <stddef.h>
#include <stdint.h>

#include "font6x8.h"
#include "logic.h"

/* One report line is "<payload padded to 38> |<2 hex digits of CRC-8>". */
#define PROBE_LINE_PAYLOAD_MAX 38
#define PROBE_LINE_COLS 42

typedef char probe_payload[PROBE_LINE_PAYLOAD_MAX + 1];

enum probe_pixel_format {
    PROBE_PIXEL_RGBX = 0, /* UEFI PixelRedGreenBlueReserved8BitPerColor */
    PROBE_PIXEL_BGRX = 1, /* UEFI PixelBlueGreenRedReserved8BitPerColor */
};

enum probe_fb_status {
    PROBE_FB_OK = 0,
    PROBE_FB_NO_BASE = -1,
    PROBE_FB_BAD_GEOMETRY = -2,
    PROBE_FB_BAD_STRIDE = -3,
    PROBE_FB_TOO_SMALL = -4,
    PROBE_FB_MISALIGNED = -5,
    PROBE_FB_BAD_FORMAT = -6,
};

const char *probe_fb_status_name(int status);

/* Called with a byte range the probe has just written. The EL2 path installs a
 * data-cache clean+invalidate here; the not-at-EL2 path leaves it NULL so that
 * no cache maintenance (and no CTR_EL0 read) happens there. */
typedef void (*probe_flush_fn)(void *ctx, const void *addr, size_t len);

typedef struct {
    volatile uint8_t *base;
    uint32_t width;
    uint32_t height;
    uint32_t stride_bytes;
    uint32_t format; /* enum probe_pixel_format */
    probe_flush_fn flush;
    void *flush_ctx;
} probe_fb;

/* gop_format is the raw UEFI EFI_GRAPHICS_PIXEL_FORMAT; only the two 8-bit
 * channel formats are accepted. size_bytes must cover the last visible byte:
 * (height - 1) * stride + width * 4. */
int probe_fb_init(probe_fb *fb, uint64_t base, uint64_t size_bytes, uint32_t width, uint32_t height,
                  uint32_t pixels_per_scanline, uint32_t gop_format);

/* Memory types a linear framebuffer may be described by in the UEFI memory
 * map: reserved, boot-services data (a display driver allocates its buffer
 * there on some firmware), runtime data and the two MMIO types. Conventional,
 * loader, code, ACPI, unusable and persistent memory belong to other users:
 * a "framebuffer" inside them is a wrong address (loader data is this image's
 * own heap), and drawing into it would corrupt whatever lives there.
 * The mask says what the firmware may be describing, not how the mapping is
 * cached: descriptor attributes are capabilities, so the first hardware run
 * reads the descriptor type and attributes from memmap.txt instead. */
#define PROBE_FB_TYPES_MASK ((1u << 0) | (1u << 4) | (1u << 6) | (1u << 11) | (1u << 12))

enum probe_gop_status {
    PROBE_GOP_OK = 0,
    PROBE_GOP_ABSENT = 1,      /* no handle offered a GOP with a mode */
    PROBE_GOP_NOT_CONSOLE = 2, /* not the GOP of the console handle: never drawn into */
    PROBE_GOP_BAD_FB = 3,      /* probe_fb_init refused the mode */
    PROBE_GOP_NOT_IN_MAP = 4,  /* the range is not wholly described by an allowed type */
    PROBE_GOP_AMBIGUOUS = 5,   /* no console GOP, and different framebuffers qualify */
};

/* The refusals are ordered by how much they say about the machine: a caller that
 * saw several candidates reports the numerically greatest. */
const char *probe_gop_status_name(int status);

/* The policy for one GOP, in one place so that a host test can drive it with
 * hostile input. A mode is usable only if the caller vouches for it as the
 * console's display (is_console: rule 1 says whether its handle is the console-out
 * handle, rule 2 vouches for every candidate and judges them by comparing
 * framebuffers instead), probe_fb_init accepts it, and its
 * full byte range [base, base + extent) lies inside the UEFI memory map in
 * descriptors of PROBE_FB_TYPES_MASK and nothing else. With no valid memory map
 * nothing is usable. On PROBE_GOP_OK *out is initialised, otherwise it is not
 * touched. probe_gop_select applies it to every GOP the firmware offers. */
int probe_gop_check(const probe_memmap *map, int is_console, uint64_t base, uint64_t size_bytes,
                    uint32_t width, uint32_t height, uint32_t pixels_per_scanline,
                    uint32_t gop_format, probe_fb *out);

/* One GOP instance exactly as the firmware reported it. is_console says that
 * its handle is the console-out handle the system table carries. */
typedef struct {
    int is_console;
    uint64_t base;
    uint64_t size_bytes;
    uint32_t width;
    uint32_t height;
    uint32_t pixels_per_scanline;
    uint32_t format;
} probe_gop_candidate;

/* Which rule chose the framebuffer. It is printed on the GOP console line, on
 * the page and in summary.txt so that a photograph says which rule fired. */
enum probe_gop_rule {
    PROBE_GOP_RULE_NONE = 0,
    PROBE_GOP_RULE_CONSOLE = 1, /* the GOP of the system table's console-out handle */
    PROBE_GOP_RULE_SAME = 2,    /* no GOP is on that handle; every usable GOP is one framebuffer */
};

const char *probe_gop_rule_name(int rule);

typedef struct {
    int status;      /* enum probe_gop_status; PROBE_GOP_OK when a framebuffer was chosen */
    int rule;        /* enum probe_gop_rule; PROBE_GOP_RULE_NONE unless status is PROBE_GOP_OK */
    size_t index;    /* the chosen candidate, the lowest index among identical ones; OK only */
    size_t examined; /* candidates looked at: rule 1 stops at the one it chooses, the rest all */
    probe_fb fb;     /* initialised on PROBE_GOP_OK, all zero otherwise */
} probe_gop_choice;

/* Picks the one framebuffer this image may draw into, or refuses.
 *
 * Rule 1 (PROBE_GOP_RULE_CONSOLE): the GOP of the console-out handle that
 * passes probe_gop_check. This is the only rule when the image is started by
 * firmware directly, and its outcome, refusals included, is unchanged by rule 2.
 *
 * Rule 2 (PROBE_GOP_RULE_SAME) applies only when NO candidate is on the console
 * handle, which is what the UEFI Shell causes: it hands every application a
 * system table whose console-out handle is its own and carries no GOP. Every
 * candidate that passes all of probe_gop_check except the console test is
 * collected. If at least one qualifies and all of them are the identical
 * framebuffer (same base, size, width, height, stride and format), that
 * framebuffer is chosen: a console splitter's virtual GOP and the physical GOP
 * under it mirror one framebuffer, so it is the one the firmware console draws
 * into. Qualifying GOPs that disagree give PROBE_GOP_AMBIGUOUS and nothing is
 * chosen. A GOP that fails a mode or memory-map check does not qualify, so it
 * is never chosen and never counts against the others. If none qualifies the
 * status is the greatest probe_gop_check refusal, or PROBE_GOP_ABSENT for an
 * empty list. A console handle that is present but refused is not second-guessed
 * by rule 2, whatever else is on offer. */
void probe_gop_select(const probe_memmap *map, const probe_gop_candidate *cand, size_t count,
                      probe_gop_choice *out);


uint32_t probe_fb_pack(const probe_fb *fb, uint8_t r, uint8_t g, uint8_t b);
void probe_fb_fill_rect(probe_fb *fb, uint32_t x, uint32_t y, uint32_t w, uint32_t h,
                        uint32_t pixel);
void probe_fb_draw_glyph(probe_fb *fb, uint32_t x, uint32_t y, uint32_t scale, const uint8_t *glyph,
                         uint32_t fg, uint32_t bg);
void probe_fb_draw_text(probe_fb *fb, uint32_t x, uint32_t y, uint32_t scale, const char *s,
                        uint32_t fg, uint32_t bg);
size_t probe_fb_extent_bytes(const probe_fb *fb);
void probe_fb_flush_rect(probe_fb *fb, uint32_t x, uint32_t y, uint32_t w, uint32_t h);
void probe_fb_flush_all(probe_fb *fb);

/* Normalises payload into out (PROBE_LINE_COLS + 1 bytes): characters outside
 * the font become '?', '|' (reserved as the CRC separator) becomes '?',
 * leading, trailing and repeated spaces are removed, and the result is
 * padded, followed by " |" and the CRC-8 of the cleaned payload. Returns the
 * number of corrections made; 0 means the payload was already canonical,
 * which the host test requires of every line the probe produces. */
int probe_line_format(char out[PROBE_LINE_COLS + 1], const char *payload);

/* Page layout. The header is two rows, the footer one row. */
#define PROBE_HEADER_ROWS 2u
#define PROBE_FOOTER_ROWS 1u
#define PROBE_LAYOUT_MIN_SCALE 3u
#define PROBE_LAYOUT_MAX_SCALE 12u
#define PROBE_LAYOUT_MAX_COLS 3u
#define PROBE_LAYOUT_MAX_PAGES 8u

enum probe_layout_status {
    PROBE_LAYOUT_OK = 0,
    PROBE_LAYOUT_BAD_ARGS = -1,
    PROBE_LAYOUT_TOO_NARROW = -2,
    PROBE_LAYOUT_TOO_MANY_PAGES = -3,
    PROBE_LAYOUT_TOO_SHORT = -4,
};

typedef struct {
    uint32_t scale;
    uint32_t cols;
    uint32_t body_rows; /* rows per column on one page */
    uint32_t pages;
    uint32_t margin;
    uint32_t col_w;
    uint32_t col_gap;
    uint32_t pitch;
    uint32_t total_rows; /* header + body + footer */
} probe_layout;

/* Chooses the largest integer scale (3..12) that shows nbody lines on a single
 * page, using as few columns as possible. If none does, falls back to scale 3
 * with the widest column count and several pages. */
int probe_layout_choose(uint32_t width, uint32_t height, unsigned nbody, probe_layout *out);

/* 0 if every cell the layout can address lies inside width x height. */
int probe_layout_fits(const probe_layout *layout, uint32_t width, uint32_t height);

unsigned probe_layout_page_start(const probe_layout *layout, unsigned page);
unsigned probe_layout_page_count(const probe_layout *layout, unsigned nbody, unsigned page);

#define PROBE_COLOR_WHITE 0xFFFFFFu
#define PROBE_COLOR_BLACK 0x000000u

/* Draws one complete page: clears the screen, writes the two header rows
 * (inverse video), the body lines of the page and the footer, then flushes.
 * Every row carries its own CRC. */
void probe_draw_page(probe_fb *fb, const probe_layout *layout, const char *header0,
                     const char *header1, const probe_payload *body, unsigned nbody,
                     unsigned page, const char *footer);

/* Redraws only the footer row, for the reset countdown. */
void probe_draw_footer(probe_fb *fb, const probe_layout *layout, const char *footer);

#endif /* PROBE_FBTEXT_H */
