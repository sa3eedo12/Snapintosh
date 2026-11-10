/* SPDX-License-Identifier: MIT */
#include "tests_types.h"

static const char *const verdict_names[TV_COUNT] = {
    "NOT-RUN", "INFO", "PASS", "FAIL", "UNKNOWN", "GO", "NO-GO", "INCONCL",
};

static const char *const label_names[TL_COUNT] = {
    "NONE", "VERIFIED", "OBSERVED", "INFERRED", "EXPERIMENTAL", "UNIMPL", "UNKNOWN",
};

const char *tests_verdict_name(int verdict)
{
    if (verdict < 0 || verdict >= TV_COUNT)
        return "BAD";
    return verdict_names[verdict];
}

const char *tests_label_name(int label, int model)
{
    if (label < 0 || label >= TL_COUNT)
        return "BAD";
    /* A run under an emulator measured the emulator, not the unit. VERIFIED can
     * never be earned by a model either, so it is shown as the weaker label. */
    if (model && label == TL_OBSERVED)
        return "OBS-MODEL";
    if (model && label == TL_VERIFIED)
        return "OBS-MODEL";
    return label_names[label];
}

enum tests_label tests_measured_label(int model)
{
    return model ? TL_EXPERIMENTAL : TL_OBSERVED;
}

void tests_result_init(tests_result *r, int id, const char *name, const char *proves,
                       const char *not_proves)
{
    probe_memset(r, 0, sizeof *r);
    r->id = (uint8_t)id;
    r->verdict = TV_NOT_RUN;
    r->label = TL_NONE;
    r->name = name;
    r->proves = proves;
    r->not_proves = not_proves;
}

/* The builder lives next to the row it fills, so each tests_row call hands back
 * a builder whose buffer is the next row of the result. One builder is enough
 * because a row is finished before the next one starts; the owner and the
 * overflow flag let the next call (or tests_result_seal) notice a row that did
 * not fit instead of showing it truncated. */
static probe_sb g_row_sb;
static tests_result *g_row_owner;

static void row_done(tests_result *r)
{
    if (g_row_owner == r && g_row_sb.overflow)
        r->overflow = 1;
    g_row_sb.overflow = 0;
}

probe_sb *tests_row(tests_result *r)
{
    row_done(r);
    if (r->nrows >= TESTS_DETAIL_MAX) {
        r->overflow = 1;
        g_row_owner = NULL;
        return NULL;
    }
    probe_sb_init(&g_row_sb, r->rows[r->nrows], sizeof r->rows[r->nrows]);
    r->nrows++;
    g_row_owner = r;
    return &g_row_sb;
}

void tests_result_seal(tests_result *r)
{
    row_done(r);
    g_row_owner = NULL;
}
void tests_row_str(tests_result *r, const char *key, const char *value)
{
    probe_sb *sb = tests_row(r);

    if (sb == NULL)
        return;
    probe_sb_puts(sb, key);
    probe_sb_putc(sb, '=');
    probe_sb_puts(sb, value);
    row_done(r);
}

void tests_row_hex(tests_result *r, const char *key, uint64_t v, unsigned digits)
{
    probe_sb *sb = tests_row(r);

    if (sb == NULL)
        return;
    probe_sb_puts(sb, key);
    probe_sb_putc(sb, '=');
    probe_sb_hex(sb, v, digits);
    row_done(r);
}

void tests_row_dec(tests_result *r, const char *key, uint64_t v)
{
    probe_sb *sb = tests_row(r);

    if (sb == NULL)
        return;
    probe_sb_puts(sb, key);
    probe_sb_putc(sb, '=');
    probe_sb_dec(sb, v);
    row_done(r);
}

void tests_row_text(tests_result *r, const char *text)
{
    probe_sb *sb = tests_row(r);

    if (sb == NULL)
        return;
    probe_sb_puts(sb, text);
    row_done(r);
}

unsigned tests_result_rows(const tests_result *r)
{
    return 1u + r->nrows + (r->proves != NULL ? 1u : 0u) + (r->not_proves != NULL ? 1u : 0u);
}
