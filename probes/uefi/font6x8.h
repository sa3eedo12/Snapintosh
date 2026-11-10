/* SPDX-License-Identifier: MIT */
/* Original 6x8 bitmap font for the SP11 EL2 probe screen.
 *
 * Provenance: hand-authored for this project as pixel art, one glyph at a
 * time. No data was copied or converted from any font file, font generator or
 * third-party source. The glyph shapes are the conventional dot-matrix forms
 * of the Latin capitals and digits, which is also why they are legible.
 *
 * The probe prints an uppercase alphabet only. That keeps the font small and,
 * more importantly, lets every pair of confusable symbols (0/O, 1/I, 5/S, 8/B,
 * 2/Z) differ by at least three pixels, which the host test enforces so a
 * photograph of the screen can be transcribed without guessing.
 */
#ifndef PROBE_FONT6X8_H
#define PROBE_FONT6X8_H

#include <stdint.h>

#define PROBE_FONT_CELL_W 6
#define PROBE_FONT_CELL_H 8

/* Eight row bytes for c; bit 7 is the leftmost pixel and only bits 7..3 are
 * used (the sixth column is inter-character spacing). Lowercase letters map to
 * their uppercase glyph. Characters outside the supported set return a solid
 * block, which cannot be mistaken for any real symbol. */
const uint8_t *probe_font_glyph(char c);

/* 1 if c has its own glyph (space..'_', lowercase letters and '|'). */
int probe_font_supported(char c);

/* Glyph for c is the solid "unsupported" block. */
int probe_font_is_tofu(const uint8_t *glyph);

#endif /* PROBE_FONT6X8_H */
