/* SPDX-License-Identifier: MIT */
#include "nv2rec.h"

#include "tests_types.h"

/* ---- Small bounded writer ---- */

typedef struct {
    char *buf;
    size_t cap;
    size_t len;
    int overflow;
} wbuf;

static void w_init(wbuf *w, char *buf, size_t cap)
{
    w->buf = buf;
    w->cap = cap;
    w->len = 0;
    w->overflow = 0;
    if (cap != 0)
        buf[0] = 0;
}

static void w_putc(wbuf *w, char c)
{
    if (w->len + 1 >= w->cap) {
        w->overflow = 1;
        return;
    }
    w->buf[w->len++] = c;
    w->buf[w->len] = 0;
}

static void w_puts(wbuf *w, const char *s)
{
    for (; *s != 0; s++)
        w_putc(w, *s);
}

/* Lower-case hex, as tools/nv2_record.py writes it ("{:#018x}"). */
static void w_hex(wbuf *w, uint64_t v, unsigned digits)
{
    static const char hex[] = "0123456789abcdef";

    w_puts(w, "0x");
    for (unsigned i = digits; i > 0; i--)
        w_putc(w, hex[(v >> (4u * (i - 1u))) & 0xFu]);
}

/* ---- Names (the spellings of tools/nv2_record.py) ---- */

static const char *const soc_names[] = {"unknown", "x1e80100", "x2e-96-100"};
static const char *const result_names[] = {"not-run", "pass", "fail"};
static const char *const evidence_names[] = {"NONE",     "VERIFIED",     "OBSERVED",
                                             "INFERRED", "EXPERIMENTAL", "UNIMPLEMENTED"};

static int tool_valid(const char *tool)
{
    size_t n = 0;

    for (; n <= TESTS_NV2_TOOL_MAX && tool[n] != 0; n++) {
        char c = tool[n];
        int ok = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
                 c == '.' || c == '_' || c == '-';

        if (!ok)
            return 0;
    }
    return n >= 1 && n <= TESTS_NV2_TOOL_MAX && tool[n] == 0;
}

int tests_nv2rec_check(const tests_nv2_record *r)
{
    if (r->soc > NV2R_SOC_X2E96100)
        return -1;
    if (r->vncr_redirect > NV2R_FAIL || r->negative_control_trapped > NV2R_FAIL)
        return -2;
    if (r->evidence > TL_UNIMPLEMENTED)
        return -3;
    if (!tool_valid(r->tool))
        return -4;
    return 0;
}

uint32_t tests_nv2rec_evidence(int model, uint32_t vncr_redirect, uint32_t negative_control)
{
    int ran_redirect = vncr_redirect == NV2R_PASS || vncr_redirect == NV2R_FAIL;
    int ran_control = negative_control == NV2R_PASS || negative_control == NV2R_FAIL;

    if (!ran_redirect && !ran_control)
        return TL_NONE;
    if (model || !(ran_redirect && ran_control))
        return TL_EXPERIMENTAL;
    return TL_OBSERVED;
}

/* ---- CRC-32 (zlib: reflected, polynomial 0xEDB88320, init and xorout 0xFFFFFFFF) ---- */

uint32_t tests_crc32(const void *data, size_t n)
{
    const uint8_t *p = data;
    uint32_t crc = 0xFFFFFFFFu;

    while (n--) {
        crc ^= *p++;
        for (unsigned bit = 0; bit < 8; bit++)
            crc = (crc & 1u) ? (crc >> 1) ^ 0xEDB88320u : crc >> 1;
    }
    return ~crc;
}

/* ---- Binary form ---- */

static void put_le(uint8_t *p, uint64_t v, unsigned bytes)
{
    for (unsigned i = 0; i < bytes; i++)
        p[i] = (uint8_t)(v >> (8u * i));
}

int tests_nv2rec_pack(const tests_nv2_record *r, uint8_t out[TESTS_NV2_BIN_SIZE])
{
    if (tests_nv2rec_check(r) != 0)
        return -1;
    probe_memset(out, 0, TESTS_NV2_BIN_SIZE);
    put_le(out + 0x00, 0x5232564Eu, 4); /* "NV2R" */
    put_le(out + 0x04, 1, 2);           /* version */
    put_le(out + 0x06, 0, 2);           /* flags */
    put_le(out + 0x08, r->soc, 4);
    put_le(out + 0x0c, r->build_id, 4);
    put_le(out + 0x10, r->midr_el1, 8);
    put_le(out + 0x18, r->revidr_el1, 8);
    put_le(out + 0x20, r->id_aa64mmfr2_el1, 8);
    put_le(out + 0x28, r->id_aa64mmfr4_el1, 8);
    out[0x30] = (uint8_t)r->vncr_redirect;
    out[0x31] = (uint8_t)r->negative_control_trapped;
    out[0x32] = (uint8_t)r->evidence;
    for (size_t i = 0; r->tool[i] != 0; i++)
        out[0x38 + i] = (uint8_t)r->tool[i];
    put_le(out + 0x5c, tests_crc32(out, 0x5c), 4);
    return 0;
}

/* ---- Text form ---- */

static void put_line(char *dst, const char *body_str)
{
    wbuf w;
    size_t n = probe_strlen(body_str);
    static const char hex[] = "0123456789abcdef";
    uint8_t crc = probe_crc8(body_str, n);

    w_init(&w, dst, TESTS_NV2_LINE_MAX);
    w_puts(&w, body_str);
    w_putc(&w, '*');
    w_putc(&w, hex[crc >> 4]);
    w_putc(&w, hex[crc & 0xF]);
}

static void kv_line(char *dst, const char *key, const char *value_text)
{
    char body[TESTS_NV2_LINE_MAX];
    wbuf w;

    w_init(&w, body, sizeof body);
    w_puts(&w, "NV2R ");
    w_puts(&w, key);
    w_putc(&w, '=');
    w_puts(&w, value_text);
    put_line(dst, body);
}

static void hex_value(char *out, size_t cap, uint64_t v, unsigned digits)
{
    wbuf w;

    w_init(&w, out, cap);
    w_hex(&w, v, digits);
}

unsigned tests_nv2rec_lines(const tests_nv2_record *r, char lines[TESTS_NV2_LINES][TESTS_NV2_LINE_MAX])
{
    char v[32];
    unsigned n = 0;

    if (tests_nv2rec_check(r) != 0)
        return 0;
    put_line(lines[n++], "NV2R begin");
    kv_line(lines[n++], "schema_version", "1");
    kv_line(lines[n++], "soc", soc_names[r->soc]);
    hex_value(v, sizeof v, r->midr_el1, 16);
    kv_line(lines[n++], "midr_el1", v);
    hex_value(v, sizeof v, r->revidr_el1, 16);
    kv_line(lines[n++], "revidr_el1", v);
    hex_value(v, sizeof v, r->id_aa64mmfr2_el1, 16);
    kv_line(lines[n++], "id_aa64mmfr2_el1", v);
    hex_value(v, sizeof v, r->id_aa64mmfr4_el1, 16);
    kv_line(lines[n++], "id_aa64mmfr4_el1", v);
    kv_line(lines[n++], "vncr_redirect", result_names[r->vncr_redirect]);
    kv_line(lines[n++], "negative_control_trapped", result_names[r->negative_control_trapped]);
    kv_line(lines[n++], "evidence", evidence_names[r->evidence]);
    kv_line(lines[n++], "tool", r->tool);
    hex_value(v, sizeof v, r->build_id, 8);
    kv_line(lines[n++], "build_id", v);
    put_line(lines[n++], "NV2R end lines=11");
    return n;
}

size_t tests_nv2rec_text(const tests_nv2_record *r, char *buf, size_t cap)
{
    char lines[TESTS_NV2_LINES][TESTS_NV2_LINE_MAX];
    wbuf w;
    unsigned n = tests_nv2rec_lines(r, lines);

    w_init(&w, buf, cap);
    for (unsigned i = 0; i < n; i++) {
        w_puts(&w, lines[i]);
        w_putc(&w, '\n');
    }
    if (n == 0 || w.overflow) {
        if (cap != 0)
            buf[0] = 0;
        return 0;
    }
    return w.len;
}

/* ---- JSON form (what json.dumps(fields, indent=2) + "\n" prints) ---- */

static void json_kv(wbuf *w, const char *key, const char *value, int quoted, int last)
{
    w_puts(w, "  \"");
    w_puts(w, key);
    w_puts(w, "\": ");
    if (quoted)
        w_putc(w, '"');
    w_puts(w, value);
    if (quoted)
        w_putc(w, '"');
    w_puts(w, last ? "\n" : ",\n");
}

size_t tests_nv2rec_json(const tests_nv2_record *r, char *buf, size_t cap)
{
    wbuf w;
    char v[32];

    w_init(&w, buf, cap);
    if (tests_nv2rec_check(r) != 0) {
        if (cap != 0)
            buf[0] = 0;
        return 0;
    }
    w_puts(&w, "{\n");
    json_kv(&w, "schema_version", "1", 0, 0);
    json_kv(&w, "soc", soc_names[r->soc], 1, 0);
    hex_value(v, sizeof v, r->midr_el1, 16);
    json_kv(&w, "midr_el1", v, 1, 0);
    hex_value(v, sizeof v, r->revidr_el1, 16);
    json_kv(&w, "revidr_el1", v, 1, 0);
    hex_value(v, sizeof v, r->id_aa64mmfr2_el1, 16);
    json_kv(&w, "id_aa64mmfr2_el1", v, 1, 0);
    hex_value(v, sizeof v, r->id_aa64mmfr4_el1, 16);
    json_kv(&w, "id_aa64mmfr4_el1", v, 1, 0);
    json_kv(&w, "vncr_redirect", result_names[r->vncr_redirect], 1, 0);
    json_kv(&w, "negative_control_trapped", result_names[r->negative_control_trapped], 1, 0);
    json_kv(&w, "evidence", evidence_names[r->evidence], 1, 0);
    json_kv(&w, "tool", r->tool, 1, 0);
    hex_value(v, sizeof v, r->build_id, 8);
    json_kv(&w, "build_id", v, 1, 1);
    w_puts(&w, "}\n");
    if (w.overflow) {
        if (cap != 0)
            buf[0] = 0;
        return 0;
    }
    return w.len;
}

/* ---- Screen form ---- */

#define SCREEN_SPLIT 32u

unsigned tests_nv2rec_screen_rows(const char lines[TESTS_NV2_LINES][TESTS_NV2_LINE_MAX],
                                  probe_payload *rows, unsigned max_rows)
{
    unsigned n = 0;

    for (unsigned i = 0; i < TESTS_NV2_LINES; i++) {
        char up[TESTS_NV2_LINE_MAX];
        size_t len = probe_strlen(lines[i]);

        if (len >= sizeof up)
            return 0;
        for (size_t k = 0; k <= len; k++) {
            char c = lines[i][k];

            up[k] = (c >= 'a' && c <= 'z') ? (char)(c - 'a' + 'A') : c;
        }
        if (len <= PROBE_LINE_PAYLOAD_MAX) {
            if (n >= max_rows)
                return 0;
            probe_memcpy(rows[n], up, len + 1);
            n++;
            continue;
        }
        if (n + 2 > max_rows || len - SCREEN_SPLIT + 1 > PROBE_LINE_PAYLOAD_MAX)
            return 0;
        probe_memcpy(rows[n], up, SCREEN_SPLIT);
        rows[n][SCREEN_SPLIT] = 0;
        n++;
        rows[n][0] = '+';
        probe_memcpy(rows[n] + 1, up + SCREEN_SPLIT, len - SCREEN_SPLIT + 1);
        n++;
    }
    return n;
}
