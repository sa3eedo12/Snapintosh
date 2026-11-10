/* SPDX-License-Identifier: MIT */
/*
 * Native driver for compat/sam-frame.c. Built with ASan and UBSan by tools/test-sam-frames.py. It contains
 * its own reference model (a bitwise CRC and a one-pass whole-buffer parser) that shares no code with the codec,
 * so the streaming parser is checked against an implementation structured differently, as well as against
 * hand-written expectations, the standard CRC check value and the Python reference.
 *
 *   test-sam-frames selftest           unit, boundary and negative tests
 *   test-sam-frames fuzz [N [SEED]]    deterministic mutation fuzz; N single-frame cases (default 150000) plus
 *                                      N/3 multi-frame stream cases
 *   test-sam-frames batch              one command per stdin line, for the Python differential
 *
 * Exit status is non-zero when any check fails. Nothing here touches hardware.
 */
#define _POSIX_C_SOURCE 200809L
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "sam-frame.h"

static unsigned long checks, failures;

#define CHECK(cond) do { checks++; if (!(cond)) { failures++; fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); } } while (0)
#define CHECK_EQ(a, b) do { long long a_ = (long long)(a), b_ = (long long)(b); checks++; if (a_ != b_) { failures++; \
    fprintf(stderr, "FAIL %s:%d: %s == %s (%lld vs %lld)\n", __FILE__, __LINE__, #a, #b, a_, b_); } } while (0)

/* ---- helpers -------------------------------------------------------------------------------------------- */

static size_t unhex(const char *s, uint8_t *out, size_t cap)
{
    size_t n = 0;

    if (s[0] == '-' && (s[1] == 0 || s[1] == '\n'))
        return 0;
    while (s[0] && s[1] && s[0] != '\n' && s[1] != '\n') {
        unsigned v;
        char pair[3] = {s[0], s[1], 0};
        if (n >= cap || sscanf(pair, "%2x", &v) != 1)
            return (size_t)-1;
        out[n++] = (uint8_t)v;
        s += 2;
    }
    return n;
}

/* A heap copy of exactly n bytes, so AddressSanitizer flags any read the codec makes past the end. */
static uint8_t *dup_exact(const uint8_t *src, size_t n)
{
    uint8_t *p = malloc(n ? n : 1);

    if (!p)
        abort();
    if (n)
        memcpy(p, src, n);
    return p;
}

static uint16_t rd16(const uint8_t *p)
{
    return (uint16_t)(p[0] | (p[1] << 8));
}

static void wr16(uint8_t *p, uint16_t v)
{
    p[0] = (uint8_t)(v & 0xFF);
    p[1] = (uint8_t)(v >> 8);
}

/* ---- reference model: shares nothing with compat/sam-frame.c ------------------------------------------- */

static uint16_t ref_crc16(const uint8_t *d, size_t n)
{
    uint16_t crc = 0xFFFF;

    for (size_t i = 0; i < n; i++) {
        crc = (uint16_t)(crc ^ (uint16_t)(d[i] << 8));
        for (int b = 0; b < 8; b++)
            crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
    }
    return crc;
}

static int ref_judge(const uint8_t *d, size_t avail, uint16_t max, uint8_t *type, uint8_t *seq, uint16_t *len)
{
    uint8_t t;
    uint16_t l;

    if (avail >= 1 && d[0] != 0xAA)
        return SAM_ERR_BAD_SYNC;
    if (avail >= 2 && d[1] != 0x55)
        return SAM_ERR_BAD_SYNC;
    if (avail < 8)
        return SAM_NEED_MORE;
    if (ref_crc16(d + 2, 4) != rd16(d + 6))
        return SAM_ERR_HEADER_CRC;
    t = d[2];
    l = rd16(d + 3);
    if (t != 0x00 && t != 0x04 && t != 0x40 && t != 0x80)
        return SAM_ERR_UNKNOWN_TYPE;
    if (l > max)
        return SAM_ERR_OVERSIZE;
    if ((t == 0x00 || t == 0x80) != (l != 0))
        return SAM_ERR_BAD_LENGTH;
    if (avail < (size_t)10 + l)
        return SAM_NEED_MORE;
    if (ref_crc16(d + 8, l) != rd16(d + 8 + l))
        return SAM_ERR_PAYLOAD_CRC;
    *type = t;
    *seq = d[5];
    *len = l;
    return SAM_OK;
}

struct ref_frame {
    uint8_t type, seq;
    uint16_t len;
    size_t off; /* where the message starts in the stream */
};

static void ref_parse_all(const uint8_t *d, size_t n, uint16_t max, struct ref_frame *frames, size_t *nframes,
                          struct sam_stats *st)
{
    size_t pos = 0;
    int in_garbage = 0;

    memset(st, 0, sizeof *st);
    *nframes = 0;
    st->bytes_in = n;
    while (pos < n) {
        size_t syn = pos;
        uint8_t t = 0, s = 0;
        uint16_t l = 0;
        int rc;

        while (syn + 1 < n && !(d[syn] == 0xAA && d[syn + 1] == 0x55))
            syn++;
        if (syn + 1 >= n)
            syn = (d[n - 1] == 0xAA && n - 1 >= pos) ? n - 1 : n;
        if (syn > pos) {
            if (!in_garbage)
                st->rej_bad_sync++;
            in_garbage = 1;
            st->bytes_skipped += syn - pos;
            pos = syn;
        }
        if (n - pos < 2)
            break;
        in_garbage = 0;
        rc = ref_judge(d + pos, n - pos, max, &t, &s, &l);
        if (rc == SAM_NEED_MORE)
            break;
        if (rc == SAM_OK) {
            frames[*nframes].type = t;
            frames[*nframes].seq = s;
            frames[*nframes].len = l;
            frames[(*nframes)++].off = pos;
            st->bytes_frames += 10u + l;
            if (t == 0x80) st->frames_data_seq++;
            else if (t == 0x00) st->frames_data_nsq++;
            else if (t == 0x40) st->frames_ack++;
            else st->frames_nak++;
            pos += 10u + l;
            continue;
        }
        if (rc == SAM_ERR_HEADER_CRC) st->rej_header_crc++;
        else if (rc == SAM_ERR_UNKNOWN_TYPE) st->rej_unknown_type++;
        else if (rc == SAM_ERR_OVERSIZE) st->rej_oversize++;
        else if (rc == SAM_ERR_BAD_LENGTH) st->rej_bad_length++;
        else st->rej_payload_crc++;
        in_garbage = 1;
        st->bytes_skipped += 2;
        pos += 2;
    }
    if (n - pos >= 2)
        st->rej_truncated++;
    else if (n - pos == 1)
        st->rej_trailing++;
    st->bytes_skipped += n - pos;
}

/* One list of (field, printed name) drives both the comparison and the canonical text the Python side predicts. */
#define STAT_FIELDS(X) \
    X(bytes_in, "bytes_in") X(bytes_frames, "bytes_frames") X(bytes_skipped, "bytes_skipped") \
    X(frames_data_seq, "data_seq") X(frames_data_nsq, "data_nsq") X(frames_ack, "ack") X(frames_nak, "nak") \
    X(rej_bad_sync, "bad_sync") X(rej_header_crc, "header_crc") X(rej_unknown_type, "unknown_type") \
    X(rej_oversize, "oversize") X(rej_bad_length, "bad_length") X(rej_payload_crc, "payload_crc") \
    X(rej_truncated, "truncated") X(rej_trailing, "trailing")

static int stats_equal(const struct sam_stats *a, const struct sam_stats *b)
{
#define SAME(field, label) a->field == b->field &&
    return STAT_FIELDS(SAME) 1;
#undef SAME
}

static void print_stats(FILE *f, const struct sam_stats *s)
{
    const char *sep = "";

#define SHOW(field, label) fprintf(f, "%s" label "=%llu", sep, (unsigned long long)s->field), sep = " ";
    STAT_FIELDS(SHOW)
#undef SHOW
}

/* ---- running the streaming parser ---------------------------------------------------------------------- */

struct got_frame {
    uint8_t type, seq;
    uint16_t len;
    uint8_t *payload;
};

struct collected {
    struct got_frame *f;
    size_t n, cap;
    struct sam_stats st;
};

static void collected_free(struct collected *c)
{
    for (size_t i = 0; i < c->n; i++)
        free(c->f[i].payload);
    free(c->f);
    c->f = NULL;
    c->n = c->cap = 0;
}

/*
 * Feeds data in pieces of chunk bytes (0 = all at once) into a parser whose storage is the smallest legal size
 * plus cap_extra, draining after every push, then declares the stream ended. Aborts if the parser ever stops
 * making progress, which would otherwise hang the test.
 */
static void run_stream(const uint8_t *data, size_t n, uint16_t max, size_t cap_extra, size_t chunk, struct collected *c)
{
    const size_t cap = SAM_MESSAGE_OVERHEAD + (size_t)max + cap_extra;
    uint8_t *storage = malloc(cap);
    struct sam_parser p;
    struct sam_frame f;
    size_t pos = 0;
    int rc;

    if (!storage)
        abort();
    c->cap = n / SAM_MESSAGE_OVERHEAD + 2;
    c->f = calloc(c->cap, sizeof *c->f);
    c->n = 0;
    CHECK_EQ(sam_parser_init(&p, storage, cap, max), SAM_OK);
    while (pos < n) {
        size_t want = chunk && n - pos > chunk ? chunk : n - pos;
        size_t took = sam_parser_push(&p, data + pos, want);
        uint64_t skipped_before = p.stats.bytes_skipped;
        size_t frames_before = c->n;

        pos += took;
        for (;;) {
            rc = sam_parser_next(&p, &f);
            if (rc == SAM_OK) {
                CHECK(c->n < c->cap);
                if (c->n < c->cap) {
                    c->f[c->n].type = f.type;
                    c->f[c->n].seq = f.seq;
                    c->f[c->n].len = f.payload_len;
                    c->f[c->n].payload = dup_exact(f.payload, f.payload_len);
                    c->n++;
                }
                continue;
            }
            CHECK_EQ(rc, SAM_NEED_MORE);
            break;
        }
        if (!took && c->n == frames_before && p.stats.bytes_skipped == skipped_before) {
            fprintf(stderr, "FAIL: parser made no progress on a full buffer\n");
            abort();
        }
    }
    CHECK_EQ(sam_parser_flush(&p), SAM_OK);
    c->st = p.stats;
    CHECK(p.stats.bytes_in == n);
    CHECK(p.stats.bytes_in == p.stats.bytes_frames + p.stats.bytes_skipped);
    free(storage);
}

static void print_canonical(FILE *out, const struct collected *c)
{
    for (size_t i = 0; i < c->n; i++) {
        fprintf(out, "F %02x %02x ", c->f[i].type, c->f[i].seq);
        if (!c->f[i].len)
            fputc('-', out);
        for (size_t k = 0; k < c->f[i].len; k++)
            fprintf(out, "%02x", c->f[i].payload[k]);
        fputc('\n', out);
    }
    fputs("S ", out);
    print_stats(out, &c->st);
    fputc('\n', out);
}

/* ---- known-answer vectors, computed independently with binascii.crc_hqx (not with this codec) ------------ */

#define EVENT_PAYLOAD "80150002011500000000040000000000" /* command header (tc 0x15, tid 0, sid 2, iid 1, rqid 0x15, cid 0) + 8-byte report */

static const struct vector {
    const char *name;
    uint8_t type, seq;
    const char *payload_hex, *msg_hex;
} vectors[] = {
    {"ack-1", 0x40, 1, "", "aa55400000017dfaffff"},
    {"ack-ff", 0x40, 0xff, "", "aa55400000ffacf4ffff"},
    {"nak", 0x04, 0, "", "aa5504000000314effff"},
    {"fw-request", 0x80, 0, "8001010000000013", "aa558008000059f080010100000000132c13"},
    {"event-nsq", 0x00, 0, EVENT_PAYLOAD, "aa5500100000a3c7" EVENT_PAYLOAD "9e4a"},
    {"event-seq", 0x80, 7, EVENT_PAYLOAD, "aa55801000077c6a" EVENT_PAYLOAD "9e4a"},
    {"ack-inside-data", 0x00, 9, "aa55400000033fdaffff", "aa55000a000928d2aa55400000033fdaffff758c"},
    {"one-byte", 0x80, 5, "5a", "aa55800100056d3e5a4f1a"},
    {"syn-in-payload", 0x80, 5, "aa55aa", "aa55800300050d50aa55aa2b53"},
};
#define NVECTORS (sizeof vectors / sizeof vectors[0])

static size_t vector_bytes(unsigned i, uint8_t *msg, uint8_t *payload, size_t *payload_len)
{
    size_t n = unhex(vectors[i].msg_hex, msg, 128);

    *payload_len = unhex(vectors[i].payload_hex, payload, 64);
    return n;
}

/* ---- tests ---------------------------------------------------------------------------------------------- */

static uint64_t rng_s;

static uint64_t rnd64(void)
{
    uint64_t z = (rng_s += 0x9E3779B97F4A7C15ull);

    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ull;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBull;
    return z ^ (z >> 31);
}

static uint32_t rnd(uint32_t n)
{
    return (uint32_t)(rnd64() % n);
}

static void test_crc(void)
{
    uint8_t buf[300];

    CHECK_EQ(sam_crc16_update(SAM_CRC_INIT, (const uint8_t *)"123456789", 9), SAM_CRC_CHECK_123456789);
    CHECK_EQ(SAM_CRC_CHECK_123456789, 0x29B1);
    CHECK_EQ(sam_crc16_update(SAM_CRC_INIT, NULL, 0), 0xFFFF);
    CHECK_EQ(ref_crc16((const uint8_t *)"123456789", 9), 0x29B1);
    {
        const uint8_t hdr[4] = {0x40, 0, 0, 1}; /* the header of an ACK for sequence 1 */
        CHECK_EQ(sam_crc16_update(SAM_CRC_INIT, hdr, 4), 0xFA7D);
    }
    for (unsigned b = 0; b < 256; b++) { /* every table entry is used by some single-byte message */
        uint8_t v = (uint8_t)b;
        CHECK_EQ(sam_crc16_update(SAM_CRC_INIT, &v, 1), ref_crc16(&v, 1));
    }
    for (unsigned i = 0; i < 3000; i++) {
        size_t n = rnd(sizeof buf + 1), split = n ? rnd((uint32_t)n + 1) : 0;
        uint16_t whole, parts;

        for (size_t k = 0; k < n; k++)
            buf[k] = (uint8_t)rnd64();
        whole = sam_crc16_update(SAM_CRC_INIT, buf, n);
        parts = sam_crc16_update(sam_crc16_update(SAM_CRC_INIT, buf, split), buf + split, n - split);
        CHECK_EQ(whole, ref_crc16(buf, n));
        CHECK_EQ(parts, whole);
    }
}

static void test_vectors(void)
{
    for (unsigned i = 0; i < NVECTORS; i++) {
        uint8_t msg[128], payload[64], built[128];
        size_t payload_len, w = 0, n = vector_bytes(i, msg, payload, &payload_len);
        struct sam_frame f;
        uint8_t *exact;

        CHECK_EQ(sam_frame_build(built, sizeof built, vectors[i].type, vectors[i].seq, payload, payload_len, &w), SAM_OK);
        CHECK(w == n && !memcmp(built, msg, n));
        exact = dup_exact(msg, n);
        CHECK_EQ(sam_frame_decode_exact(exact, n, 64, &f), SAM_OK);
        CHECK(f.type == vectors[i].type && f.seq == vectors[i].seq && f.payload_len == payload_len &&
              f.message_len == n && !memcmp(f.payload, payload, payload_len));
        /* A limit equal to the payload length is accepted: the bound is inclusive. */
        CHECK_EQ(sam_frame_decode_exact(exact, n, (uint16_t)payload_len, &f), SAM_OK);
        free(exact);
    }
    {
        uint8_t m[64], v[64];
        size_t w = 0, n;

        CHECK_EQ(sam_build_ack(m, sizeof m, 1, &w), SAM_OK);
        n = unhex(vectors[0].msg_hex, v, sizeof v);
        CHECK(w == n && !memcmp(m, v, n));
        CHECK_EQ(sam_build_nak(m, sizeof m, &w), SAM_OK);
        n = unhex(vectors[2].msg_hex, v, sizeof v);
        CHECK(w == n && !memcmp(m, v, n));
    }
}

static void test_builder_refusals(void)
{
    uint8_t out[64], sentinel[64], pl[8] = {1, 2, 3, 4, 5, 6, 7, 8};
    size_t w = 99;
    uint8_t *big = malloc(70000);

    memset(sentinel, 0xEE, sizeof sentinel);
    memset(out, 0xEE, sizeof out);
    CHECK_EQ(sam_frame_build(out, sizeof out, 0x41, 0, NULL, 0, &w), SAM_ERR_ARG);
    CHECK_EQ(w, 0);
    CHECK_EQ(sam_frame_build(out, sizeof out, SAM_FRAME_ACK, 0, pl, 1, &w), SAM_ERR_BAD_LENGTH);
    CHECK_EQ(sam_frame_build(out, sizeof out, SAM_FRAME_NAK, 0, pl, 1, &w), SAM_ERR_BAD_LENGTH);
    CHECK_EQ(sam_frame_build(out, sizeof out, SAM_FRAME_DATA_SEQ, 0, NULL, 0, &w), SAM_ERR_BAD_LENGTH);
    CHECK_EQ(sam_frame_build(out, sizeof out, SAM_FRAME_DATA_NSQ, 0, pl, 0, &w), SAM_ERR_BAD_LENGTH);
    CHECK_EQ(sam_frame_build(out, 10 + 8 - 1, SAM_FRAME_DATA_SEQ, 0, pl, 8, &w), SAM_ERR_NOSPACE);
    CHECK_EQ(sam_frame_build(out, 9, SAM_FRAME_ACK, 0, NULL, 0, &w), SAM_ERR_NOSPACE);
    CHECK_EQ(w, 0);
    CHECK_EQ(sam_frame_build(NULL, 64, SAM_FRAME_ACK, 0, NULL, 0, &w), SAM_ERR_ARG);
    CHECK_EQ(sam_frame_build(out, 64, SAM_FRAME_DATA_SEQ, 0, NULL, 8, &w), SAM_ERR_ARG);
    CHECK_EQ(sam_frame_build(out, 64, SAM_FRAME_ACK, 0, NULL, 0, NULL), SAM_ERR_ARG);
    memset(out, 0xEE, sizeof out);
    sam_frame_build(out, 17, SAM_FRAME_DATA_SEQ, 0, pl, 8, &w);
    CHECK(!memcmp(out, sentinel, sizeof out));
    if (big) {
        struct sam_frame f;
        size_t cap = 70000;

        memset(big, 0x5A, cap);
        CHECK_EQ(sam_frame_build(big + 8, cap - 8, SAM_FRAME_DATA_SEQ, 0, big, 65536, &w), SAM_ERR_OVERSIZE);
        CHECK_EQ(w, 0);
        memset(big + 8, 0x5A, cap - 8);
        CHECK_EQ(sam_frame_build(big + 8, 69000, SAM_FRAME_DATA_SEQ, 0x33, big, 65535, &w), SAM_OK);
        CHECK_EQ(w, 65545);
        CHECK_EQ(sam_frame_decode_exact(big + 8, w, 65535, &f), SAM_OK);
        CHECK_EQ(f.payload_len, 65535);
        CHECK_EQ(sam_frame_decode_exact(big + 8, w, 65534, &f), SAM_ERR_OVERSIZE);
    }
    free(big);
}

static void make_header_frame(uint8_t *m, uint8_t type, uint16_t len, uint8_t seq, const uint8_t *payload)
{
    m[0] = 0xAA;
    m[1] = 0x55;
    m[2] = type;
    wr16(m + 3, len);
    m[5] = seq;
    wr16(m + 6, ref_crc16(m + 2, 4));
    if (len)
        memcpy(m + 8, payload, len);
    wr16(m + 8 + len, ref_crc16(m + 8, len));
}

/* Judges a heap copy of exactly n bytes with sam_frame_decode_exact (exact) or sam_frame_parse. */
static int judge_heap(const uint8_t *m, size_t n, uint16_t max, int exact)
{
    uint8_t *e = dup_exact(m, n);
    struct sam_frame f;
    int rc = exact ? sam_frame_decode_exact(e, n, max, &f) : sam_frame_parse(e, n, max, &f);

    free(e);
    return rc;
}

static void test_single_shot_negatives(void)
{
    uint8_t m[128], pl[100], msg[128], payload[64];
    struct sam_frame f;
    size_t n;

    for (unsigned i = 0; i < sizeof pl; i++)
        pl[i] = (uint8_t)(i * 7 + 1);

#define EXPECT(rc_expected, len_, max_) CHECK_EQ(judge_heap(m, (len_), (max_), 1), (rc_expected))

    make_header_frame(m, 0x80, 6, 3, pl);
    EXPECT(SAM_OK, 16, 6);
    EXPECT(SAM_ERR_OVERSIZE, 16, 5);              /* limit one below the length: refused */
    m[0] = 0xAB; EXPECT(SAM_ERR_BAD_SYNC, 16, 64); make_header_frame(m, 0x80, 6, 3, pl);
    m[1] = 0x56; EXPECT(SAM_ERR_BAD_SYNC, 16, 64); make_header_frame(m, 0x80, 6, 3, pl);
    m[6] ^= 1; EXPECT(SAM_ERR_HEADER_CRC, 16, 64); make_header_frame(m, 0x80, 6, 3, pl);
    m[5] ^= 0x10; EXPECT(SAM_ERR_HEADER_CRC, 16, 64); make_header_frame(m, 0x80, 6, 3, pl);
    make_header_frame(m, 0x41, 6, 3, pl); EXPECT(SAM_ERR_UNKNOWN_TYPE, 16, 64);
    make_header_frame(m, 0x01, 6, 3, pl); EXPECT(SAM_ERR_UNKNOWN_TYPE, 16, 64);
    make_header_frame(m, 0x40, 2, 3, pl); EXPECT(SAM_ERR_BAD_LENGTH, 12, 64);   /* ACK with a payload */
    make_header_frame(m, 0x04, 1, 0, pl); EXPECT(SAM_ERR_BAD_LENGTH, 11, 64);   /* NAK with a payload */
    make_header_frame(m, 0x80, 0, 3, pl); EXPECT(SAM_ERR_BAD_LENGTH, 10, 64);   /* data without a payload */
    make_header_frame(m, 0x00, 0, 3, pl); EXPECT(SAM_ERR_BAD_LENGTH, 10, 64);
    make_header_frame(m, 0x40, 65, 3, pl); EXPECT(SAM_ERR_OVERSIZE, 75, 64);    /* the length limit is judged before type/length */
    make_header_frame(m, 0x41, 65, 3, pl); EXPECT(SAM_ERR_UNKNOWN_TYPE, 75, 64); /* and the type before the length */
    make_header_frame(m, 0x80, 6, 3, pl); m[8] ^= 0x01; EXPECT(SAM_ERR_PAYLOAD_CRC, 16, 64);
    make_header_frame(m, 0x80, 6, 3, pl); m[15] ^= 0x80; EXPECT(SAM_ERR_PAYLOAD_CRC, 16, 64);
    make_header_frame(m, 0x80, 6, 3, pl); EXPECT(SAM_ERR_TRAILING, 17, 64);
    make_header_frame(m, 0x80, 6, 3, pl); m[16] = 0xAA; EXPECT(SAM_ERR_TRAILING, 17, 64);
    make_header_frame(m, 0x80, 6, 3, pl);
    for (size_t k = 0; k < 16; k++) {               /* every strict prefix is a truncation, never a frame */
        EXPECT(SAM_ERR_TRUNCATED, k, 64);
        CHECK_EQ(judge_heap(m, k, 64, 0), SAM_NEED_MORE);
    }
    /* A header whose CRC is bad is refused as soon as its 8 bytes are present, before the payload arrives; an
     * over-limit length is refused at the same point, so a huge phantom payload never stalls the parser. */
    make_header_frame(m, 0x80, 6, 3, pl);
    CHECK_EQ(judge_heap(m, 8, 5, 0), SAM_ERR_OVERSIZE);
    CHECK_EQ(judge_heap(m, 8, 6, 0), SAM_NEED_MORE);
    m[7] ^= 1;
    CHECK_EQ(judge_heap(m, 8, 64, 0), SAM_ERR_HEADER_CRC);
    /* Prefixes that already contradict the SYN are refused early; a lone 0xAA is still a valid prefix. */
    CHECK_EQ(judge_heap((const uint8_t[]){0xAA}, 1, 64, 0), SAM_NEED_MORE);
    CHECK_EQ(judge_heap((const uint8_t[]){0x55}, 1, 64, 0), SAM_ERR_BAD_SYNC);
    CHECK_EQ(judge_heap((const uint8_t[]){0xAA, 0xAA}, 2, 64, 0), SAM_ERR_BAD_SYNC);
    CHECK_EQ(sam_frame_parse(NULL, 0, 64, &f), SAM_NEED_MORE);
    CHECK_EQ(sam_frame_parse(NULL, 4, 64, &f), SAM_ERR_ARG);
    CHECK_EQ(sam_frame_parse(m, 16, 64, NULL), SAM_ERR_ARG);
    CHECK_EQ(sam_frame_decode_exact(NULL, 0, 64, &f), SAM_ERR_TRUNCATED);

    /* The CRC catches every single-bit and every two-bit flip of the short vectors. */
    for (unsigned i = 0; i < NVECTORS; i++) {
        size_t plen, bits;
        uint8_t copy[128];

        n = vector_bytes(i, msg, payload, &plen);
        bits = n * 8;
        for (size_t a = 0; a < bits; a++)
            for (size_t b = a; b < bits; b++) {
                memcpy(copy, msg, n);
                copy[a / 8] ^= (uint8_t)(1u << (a % 8));
                if (b != a)
                    copy[b / 8] ^= (uint8_t)(1u << (b % 8));
                CHECK(judge_heap(copy, n, 64, 1) != SAM_OK);
            }
    }
#undef EXPECT
}

static void test_streaming(void)
{
    static const size_t chunks[] = {0, 1, 2, 3, 5, 7, 11, 64};
    uint8_t all[1024], msg[128], payload[64];
    size_t total = 0;

    /* Back-to-back vectors, at every chunking, with the smallest legal storage and with spare room. */
    for (unsigned i = 0; i < NVECTORS; i++) {
        size_t plen, n = vector_bytes(i, msg, payload, &plen);
        memcpy(all + total, msg, n);
        total += n;
    }
    for (size_t c = 0; c < sizeof chunks / sizeof chunks[0]; c++)
        for (size_t extra = 0; extra < 130; extra += 64) {
            struct collected got;
            uint8_t *d = dup_exact(all, total);

            run_stream(d, total, 64, extra, chunks[c], &got);
            CHECK_EQ(got.n, NVECTORS);
            for (unsigned i = 0; i < got.n && i < NVECTORS; i++) {
                size_t plen;
                vector_bytes(i, msg, payload, &plen);
                CHECK(got.f[i].type == vectors[i].type && got.f[i].seq == vectors[i].seq && got.f[i].len == plen &&
                      !memcmp(got.f[i].payload, payload, plen));
            }
            CHECK_EQ(got.st.bytes_frames, total);
            CHECK_EQ(got.st.bytes_skipped, 0);
            CHECK_EQ(sam_stats_rejects(&got.st), 0);
            CHECK_EQ(got.st.frames_ack, 2);
            CHECK_EQ(got.st.frames_nak, 1);
            CHECK_EQ(got.st.frames_data_seq, 4);
            CHECK_EQ(got.st.frames_data_nsq, 2);
            collected_free(&got);
            free(d);
        }
    /* Every two-way split of the whole stream: a frame cut at any byte is completed by the next push. */
    for (size_t cut = 0; cut <= total; cut++) {
        uint8_t *d = dup_exact(all, total);
        uint8_t storage[10 + 64];
        struct sam_parser p;
        struct sam_frame f;
        size_t got = 0;

        CHECK_EQ(sam_parser_init(&p, storage, sizeof storage, 64), SAM_OK);
        for (int half = 0; half < 2; half++) {
            const uint8_t *src = half ? d + cut : d;
            size_t len = half ? total - cut : cut;

            while (len) {
                size_t took = sam_parser_push(&p, src, len);
                src += took;
                len -= took;
                while (sam_parser_next(&p, &f) == SAM_OK)
                    got++;
            }
            while (sam_parser_next(&p, &f) == SAM_OK)
                got++;
        }
        CHECK_EQ(got, NVECTORS);
        CHECK_EQ(sam_parser_flush(&p), SAM_OK);
        CHECK_EQ(sam_stats_rejects(&p.stats), 0);
        free(d);
    }
}

/* Expected counters for a stream made of valid frames around one damaged segment. */
struct expect {
    uint64_t frames, skipped, bad_sync, header_crc, unknown_type, oversize, bad_length, payload_crc, truncated, trailing;
};

static void check_counts(const char *what, const uint8_t *d, size_t n, uint16_t max, const struct expect *e)
{
    static const size_t chunks[] = {0, 1, 3, 13};

    for (size_t c = 0; c < sizeof chunks / sizeof chunks[0]; c++) {
        struct collected got;
        uint8_t *copy = dup_exact(d, n);
        const struct sam_stats *s;
        struct ref_frame rf[128];
        struct sam_stats model;
        size_t nmodel = 0;

        run_stream(copy, n, max, 0, chunks[c], &got);
        s = &got.st;
        if (got.n != e->frames || s->bytes_skipped != e->skipped || s->rej_bad_sync != e->bad_sync ||
            s->rej_header_crc != e->header_crc || s->rej_unknown_type != e->unknown_type ||
            s->rej_oversize != e->oversize || s->rej_bad_length != e->bad_length ||
            s->rej_payload_crc != e->payload_crc || s->rej_truncated != e->truncated || s->rej_trailing != e->trailing) {
            fprintf(stderr, "FAIL %s (chunk %zu): frames=%zu ", what, chunks[c], got.n);
            print_stats(stderr, s);
            fputc('\n', stderr);
            failures++;
        }
        checks++;
        ref_parse_all(copy, n, max, rf, &nmodel, &model);
        CHECK(stats_equal(s, &model) && got.n == nmodel);
        collected_free(&got);
        free(copy);
    }
}

static size_t append(uint8_t *buf, size_t n, const uint8_t *src, size_t k)
{
    memcpy(buf + n, src, k);
    return n + k;
}

static void test_stream_incidents(void)
{
    static const uint8_t pay[6] = {'1', '2', '3', '4', '5', '6'};
    uint8_t a[16], b[16], x[300], s[1024], inner[16], outer[64], payload[300];
    size_t n, xn;

    make_header_frame(a, 0x80, 6, 1, pay); /* 16 bytes each */
    make_header_frame(b, 0x00, 6, 2, pay);
    for (unsigned i = 0; i < sizeof payload; i++)
        payload[i] = (uint8_t)(0x11 + i % 7);

    /* One damaged segment x between two good frames: both good frames survive, exactly one counter moves. */
#define INCIDENT(label, build_x, EXP) do { \
        build_x; \
        n = append(s, 0, a, 16); n = append(s, n, x, xn); n = append(s, n, b, 16); \
        struct expect e_ = EXP; check_counts(label, s, n, 64, &e_); } while (0)

    INCIDENT("header-crc", (make_header_frame(x, 0x80, 6, 9, pay), x[6] ^= 4, xn = 16),
             ((struct expect){2, 16, 0, 1, 0, 0, 0, 0, 0, 0}));
    INCIDENT("unknown-type", (make_header_frame(x, 0x41, 6, 9, pay), xn = 16),
             ((struct expect){2, 16, 0, 0, 1, 0, 0, 0, 0, 0}));
    INCIDENT("oversize", (make_header_frame(x, 0x80, 65, 9, payload), xn = 10 + 65),
             ((struct expect){2, 75, 0, 0, 0, 1, 0, 0, 0, 0}));
    INCIDENT("ack-with-payload", (make_header_frame(x, 0x40, 3, 9, pay), xn = 13),
             ((struct expect){2, 13, 0, 0, 0, 0, 1, 0, 0, 0}));
    INCIDENT("data-without-payload", (make_header_frame(x, 0x80, 0, 9, pay), xn = 10),
             ((struct expect){2, 10, 0, 0, 0, 0, 1, 0, 0, 0}));
    INCIDENT("payload-crc", (make_header_frame(x, 0x80, 6, 9, pay), x[9] ^= 0x40, xn = 16),
             ((struct expect){2, 16, 0, 0, 0, 0, 0, 1, 0, 0}));
    INCIDENT("garbage-run", (x[0] = 1, x[1] = 2, x[2] = 3, x[3] = 0xAA, x[4] = 4, xn = 5),
             ((struct expect){2, 5, 1, 0, 0, 0, 0, 0, 0, 0}));
    INCIDENT("lone-aa-before-syn", (x[0] = 0xAA, xn = 1),
             ((struct expect){2, 1, 1, 0, 0, 0, 0, 0, 0, 0}));
    INCIDENT("false-syn", (x[0] = 0xAA, x[1] = 0x55, x[2] = 1, x[3] = 2, x[4] = 3, x[5] = 4, x[6] = 5, x[7] = 6, xn = 8),
             ((struct expect){2, 8, 0, 1, 0, 0, 0, 0, 0, 0}));
#undef INCIDENT

    /* Truncation and trailing bytes at the end of the stream. */
    {
        struct expect e1 = {1, 12, 0, 0, 0, 0, 0, 0, 1, 0}, e2 = {1, 2, 0, 0, 0, 0, 0, 0, 1, 0};
        struct expect e3 = {1, 1, 0, 0, 0, 0, 0, 0, 0, 1}, e4 = {1, 2, 1, 0, 0, 0, 0, 0, 0, 0};
        struct expect e5 = {1, 3, 1, 0, 0, 0, 0, 0, 0, 1};

        n = append(s, 0, a, 16); n = append(s, n, b, 12); check_counts("partial-frame", s, n, 64, &e1);
        n = append(s, 0, a, 16); s[n++] = 0xAA; s[n++] = 0x55; check_counts("syn-only", s, n, 64, &e2);
        n = append(s, 0, a, 16); s[n++] = 0xAA; check_counts("lone-aa-at-end", s, n, 64, &e3);
        n = append(s, 0, a, 16); s[n++] = 0x01; s[n++] = 0x02; check_counts("garbage-at-end", s, n, 64, &e4);
        n = append(s, 0, a, 16); s[n++] = 0x01; s[n++] = 0x02; s[n++] = 0xAA; check_counts("garbage-then-lone-aa", s, n, 64, &e5);
    }

    /* A real frame that starts inside a rejected one is still found, however the outer one is damaged. */
    make_header_frame(inner, 0x40, 0, 3, pay); /* an ACK, 10 bytes */
    make_header_frame(outer, 0x00, 10, 9, inner);
    {
        /* The outer frame's own bytes before the inner one are skipped as part of the incident; its two trailing
         * CRC bytes after the inner frame are a fresh garbage run. */
        struct expect e = {1, 10, 1, 1, 0, 0, 0, 0, 0, 0};
        uint8_t bad_hdr[64], bad_pay[64];

        memcpy(bad_hdr, outer, 20);
        bad_hdr[7] ^= 0x01;
        check_counts("inner-after-bad-header", bad_hdr, 20, 64, &e);
        memcpy(bad_pay, outer, 20);
        bad_pay[18] ^= 0x01;
        e.header_crc = 0;
        e.payload_crc = 1;
        check_counts("inner-after-bad-payload-crc", bad_pay, 20, 64, &e);
    }

    /* The length limit is inclusive, at the smallest legal storage, for several limits. */
    for (unsigned max = 1; max <= 300; max += (max < 8 ? 1 : 97)) {
        uint8_t ok[400], over[400];
        struct collected got;
        struct expect e1 = {1, 0, 0, 0, 0, 0, 0, 0, 0, 0}, e2 = {0, 10u + max + 1u, 0, 0, 0, 1, 0, 0, 0, 0};

        make_header_frame(ok, 0x80, (uint16_t)max, 1, payload);
        make_header_frame(over, 0x80, (uint16_t)(max + 1), 1, payload);
        check_counts("at-limit", ok, 10u + max, (uint16_t)max, &e1);
        check_counts("over-limit", over, 10u + max + 1u, (uint16_t)max, &e2);
        run_stream(ok, 10u + max, (uint16_t)max, 0, 1, &got);
        CHECK_EQ(got.n, 1);
        collected_free(&got);
    }
    (void)xn;
}

static void test_parser_lifecycle(void)
{
    uint8_t storage[10 + 8], one[16], tail[3] = {0xAA, 0x55, 0x01};
    struct sam_parser p;
    struct sam_frame f, g;
    size_t w = 0;
    const uint8_t pay[1] = {9};

    /* Unconfigured and mis-configured parsers refuse everything and touch nothing. */
    memset(&p, 0, sizeof p);
    CHECK_EQ(sam_parser_push(&p, one, 4), 0);
    CHECK_EQ(sam_parser_next(&p, &f), SAM_ERR_ARG);
    CHECK_EQ(sam_parser_flush(&p), SAM_ERR_ARG);
    CHECK_EQ(sam_parser_init(NULL, storage, sizeof storage, 8), SAM_ERR_ARG);
    CHECK_EQ(sam_parser_init(&p, NULL, sizeof storage, 8), SAM_ERR_ARG);
    CHECK_EQ(sam_parser_init(&p, storage, sizeof storage, 0), SAM_ERR_ARG);
    CHECK_EQ(sam_parser_init(&p, storage, sizeof storage - 1, 8), SAM_ERR_ARG);
    CHECK_EQ(sam_parser_next(&p, &f), SAM_ERR_ARG);
    CHECK_EQ(sam_parser_init(&p, storage, sizeof storage, 8), SAM_OK);
    CHECK_EQ(sam_parser_next(&p, NULL), SAM_ERR_ARG);
    CHECK_EQ(sam_parser_push(&p, NULL, 1), 0);
    CHECK_EQ(sam_parser_init(&p, storage, sizeof storage - 1, 8), SAM_ERR_ARG); /* a failed re-init unconfigures it */
    CHECK_EQ(sam_parser_next(&p, &f), SAM_ERR_ARG);

    /* flush is only valid once next() has said there is nothing more to deliver. */
    CHECK_EQ(sam_parser_init(&p, storage, sizeof storage, 8), SAM_OK);
    CHECK_EQ(sam_frame_build(one, sizeof one, SAM_FRAME_DATA_SEQ, 4, pay, 1, &w), SAM_OK);
    CHECK_EQ(sam_parser_push(&p, one, w), w);
    CHECK_EQ(sam_parser_flush(&p), SAM_ERR_ARG);
    CHECK_EQ(p.stats.rej_truncated, 0);
    CHECK_EQ(sam_parser_next(&p, &f), SAM_OK);
    CHECK_EQ(sam_parser_flush(&p), SAM_ERR_ARG);
    CHECK_EQ(sam_parser_next(&p, &g), SAM_NEED_MORE);
    CHECK_EQ(sam_parser_flush(&p), SAM_OK);
    CHECK_EQ(p.stats.rej_truncated + p.stats.rej_trailing, 0);

    /* A payload pointer stays valid across next() calls, and the frame it came from is not rewritten. */
    CHECK_EQ(sam_parser_init(&p, storage, sizeof storage, 8), SAM_OK);
    CHECK_EQ(sam_parser_push(&p, one, w), w);
    CHECK_EQ(sam_parser_next(&p, &f), SAM_OK);
    CHECK(f.payload_len == 1 && f.payload[0] == 9 && f.seq == 4 && f.type == SAM_FRAME_DATA_SEQ);
    CHECK_EQ(sam_parser_next(&p, &g), SAM_NEED_MORE);
    CHECK(f.payload[0] == 9);

    /* Pushing into a full buffer takes only what fits, and the buffer always drains (cap == one maximal frame). */
    CHECK_EQ(sam_parser_init(&p, storage, sizeof storage, 8), SAM_OK);
    CHECK_EQ(sam_parser_push(&p, tail, 3), 3);
    {
        uint8_t junk[40];
        size_t took;

        memset(junk, 0x77, sizeof junk);
        took = sam_parser_push(&p, junk, sizeof junk);
        CHECK_EQ(took, sizeof storage - 3);
        CHECK_EQ(sam_parser_push(&p, junk, 1), 0);
        CHECK_EQ(sam_parser_next(&p, &f), SAM_NEED_MORE); /* the rejected candidate and the junk make room */
        CHECK(sam_parser_push(&p, junk, 1) == 1);
    }
}

static void test_commands_and_ids(void)
{
    uint8_t hdr[SAM_COMMAND_HEADER_LEN], msg[128], expect[128], payload[64];
    struct sam_command c, d;
    const uint8_t *data = NULL;
    size_t dlen = 0, w = 0, n, plen;
    uint8_t *big = malloc(70000);

    c.tc = 0x15; c.tid = 0; c.sid = 2; c.iid = 1; c.rqid = 0x0015; c.cid = 0;
    sam_command_encode(hdr, &c);
    CHECK(!memcmp(hdr, (const uint8_t[]){0x80, 0x15, 0x00, 0x02, 0x01, 0x15, 0x00, 0x00}, 8));
    c.tc = 1; c.tid = 1; c.sid = 0; c.iid = 0; c.rqid = 0xA1B2; c.cid = 0x13;
    sam_command_encode(hdr, &c);
    CHECK(!memcmp(hdr, (const uint8_t[]){0x80, 0x01, 0x01, 0x00, 0x00, 0xB2, 0xA1, 0x13}, 8)); /* rqid is little-endian */
    CHECK_EQ(sam_command_decode(hdr, 8, &d, &data, &dlen), SAM_OK);
    CHECK(d.tc == 1 && d.tid == 1 && d.sid == 0 && d.iid == 0 && d.rqid == 0xA1B2 && d.cid == 0x13 && dlen == 0 && data == hdr + 8);
    for (size_t k = 0; k < 8; k++)
        CHECK_EQ(sam_command_decode(hdr, k, &d, &data, &dlen), SAM_ERR_CMD_SHORT);
    for (unsigned t = 0; t < 256; t++) {
        uint8_t bad[9];
        memcpy(bad, hdr, 8);
        bad[0] = (uint8_t)t;
        bad[8] = 0x42;
        CHECK_EQ(sam_command_decode(bad, 9, &d, &data, &dlen), t == 0x80 ? SAM_OK : SAM_ERR_CMD_TYPE);
        if (t == 0x80)
            CHECK(dlen == 1 && data[0] == 0x42);
    }
    CHECK_EQ(sam_command_decode(NULL, 8, &d, &data, &dlen), SAM_ERR_ARG);
    CHECK_EQ(sam_command_decode(hdr, 8, NULL, &data, &dlen), SAM_ERR_ARG);

    /* The two command vectors, rebuilt from fields. */
    c.tc = 1; c.tid = 1; c.sid = 0; c.iid = 0; c.rqid = 0; c.cid = 0x13;
    CHECK_EQ(sam_build_command(msg, sizeof msg, SAM_FRAME_DATA_SEQ, 0, &c, NULL, 0, &w), SAM_OK);
    n = unhex(vectors[3].msg_hex, expect, sizeof expect);
    CHECK(w == n && !memcmp(msg, expect, n));
    c.tc = 0x15; c.tid = 0; c.sid = 2; c.iid = 1; c.rqid = 0x15; c.cid = 0;
    plen = unhex("0000040000000000", payload, sizeof payload);
    CHECK_EQ(sam_build_command(msg, sizeof msg, SAM_FRAME_DATA_NSQ, 0, &c, payload, plen, &w), SAM_OK);
    n = unhex(vectors[4].msg_hex, expect, sizeof expect);
    CHECK(w == n && !memcmp(msg, expect, n));
    CHECK_EQ(sam_build_command(msg, 10 + 8 + 8 - 1, SAM_FRAME_DATA_NSQ, 0, &c, payload, plen, &w), SAM_ERR_NOSPACE);
    CHECK_EQ(w, 0);
    CHECK_EQ(sam_build_command(msg, sizeof msg, SAM_FRAME_ACK, 0, &c, NULL, 0, &w), SAM_ERR_ARG);
    CHECK_EQ(sam_build_command(msg, sizeof msg, SAM_FRAME_NAK, 0, &c, NULL, 0, &w), SAM_ERR_ARG);
    CHECK_EQ(sam_build_command(msg, sizeof msg, 0x13, 0, &c, NULL, 0, &w), SAM_ERR_ARG);
    CHECK_EQ(sam_build_command(msg, sizeof msg, SAM_FRAME_DATA_SEQ, 0, NULL, NULL, 0, &w), SAM_ERR_ARG);
    CHECK_EQ(sam_build_command(msg, sizeof msg, SAM_FRAME_DATA_SEQ, 0, &c, NULL, 4, &w), SAM_ERR_ARG);
    if (big) {
        struct sam_frame f;

        memset(big, 0x11, 70000);
        CHECK_EQ(sam_build_command(big + 8, 69000, SAM_FRAME_DATA_SEQ, 1, &c, big, SAM_COMMAND_DATA_MAX + 1, &w), SAM_ERR_OVERSIZE);
        CHECK_EQ(sam_build_command(big + 8, 69000, SAM_FRAME_DATA_SEQ, 1, &c, big, SAM_COMMAND_DATA_MAX, &w), SAM_OK);
        CHECK_EQ(w, 10 + 65535);
        CHECK_EQ(sam_frame_decode_exact(big + 8, w, 65535, &f), SAM_OK);
        CHECK_EQ(sam_command_decode(f.payload, f.payload_len, &d, &data, &dlen), SAM_OK);
        CHECK_EQ(dlen, SAM_COMMAND_DATA_MAX);
    }
    free(big);

    /* Request ids: 1..38 are events, 0 and everything else are requests. */
    CHECK(!sam_rqid_is_event(0));
    for (unsigned r = 1; r <= 38; r++)
        CHECK(sam_rqid_is_event((uint16_t)r));
    CHECK(!sam_rqid_is_event(39) && !sam_rqid_is_event(0x8000) && !sam_rqid_is_event(0xFFFF));
    CHECK_EQ(sam_rqid_next_request(0), 39);
    CHECK_EQ(sam_rqid_next_request(39), 40);
    CHECK_EQ(sam_rqid_next_request(0xFFFE), 0xFFFF);
    CHECK_EQ(sam_rqid_next_request(0xFFFF), 0);
    CHECK_EQ(sam_rqid_next_request(38), 39);
    CHECK_EQ(sam_rqid_next_request(1), 39);
    {
        uint16_t r = 0;
        unsigned wraps = 0;

        for (unsigned i = 0; i < 140000; i++) { /* the cycle 0, 39..65535 has 65498 states: two wraps in 140000 steps */
            uint16_t next = sam_rqid_next_request(r);

            CHECK(!sam_rqid_is_event(next) && (next == 39 || next == 0 || next == (uint16_t)(r + 1)));
            wraps += next < r;
            r = next;
        }
        CHECK_EQ(wraps, 2);
    }
}

/* ---- fuzz ---------------------------------------------------------------------------------------------- */

#define FUZZ_MAX 300u
#define FUZZ_BUF 4096u

struct coverage {
    unsigned long cases, delivered, exact_roundtrips, any_rejection, single_ok;
    unsigned long type[4];       /* delivered frames by type: data_nsq, nak, ack, data_seq */
    unsigned long reason[8];     /* cases that hit each stream rejection reason, in sam_stats order */
    unsigned long single_err[16]; /* single-shot refusals by status */
};

static size_t gen_frame(uint8_t *out, size_t cap)
{
    static const uint8_t types[4] = {0x00, 0x04, 0x40, 0x80};
    uint8_t type = types[rnd(4)], payload[FUZZ_MAX];
    size_t len = 0, w = 0;

    if (type == 0x00 || type == 0x80) {
        uint32_t r = rnd(10);

        len = r < 6 ? 1 + rnd(24) : r < 9 ? 1 + rnd(FUZZ_MAX) : FUZZ_MAX;
        for (size_t i = 0; i < len; i++)
            payload[i] = (uint8_t)(rnd(8) == 0 ? (rnd(2) ? 0xAA : 0x55) : rnd64()); /* SYN bytes inside payloads on purpose */
    }
    CHECK_EQ(sam_frame_build(out, cap, type, (uint8_t)rnd64(), payload, len, &w), SAM_OK);
    return w;
}

static uint8_t garbage_byte(void)
{
    switch (rnd(5)) {
    case 0: return 0xAA;
    case 1: return 0x55;
    default: return (uint8_t)rnd64();
    }
}

static size_t mutate(uint8_t *m, size_t n, size_t frame_off)
{
    size_t i, k;

    switch (rnd(14)) {
    case 0: if (n) m[rnd((uint32_t)n)] ^= (uint8_t)(1u << rnd(8)); break;
    case 1: if (n) m[rnd((uint32_t)n)] = (uint8_t)rnd64(); break;
    case 2: if (n) n = rnd((uint32_t)n); break;
    case 3: if (n) { i = rnd((uint32_t)n); memmove(m + i, m + i + 1, n - i - 1); n--; } break;
    case 4: if (n + 1 < FUZZ_BUF) { i = rnd((uint32_t)n + 1); memmove(m + i + 1, m + i, n - i); m[i] = garbage_byte(); n++; } break;
    case 5: k = 1 + rnd(16); for (i = 0; i < k && n < FUZZ_BUF; i++) m[n++] = garbage_byte(); break;
    case 6: k = 1 + rnd(8); if (n + k < FUZZ_BUF) { memmove(m + k, m, n); for (i = 0; i < k; i++) m[i] = garbage_byte(); n += k; } break;
    case 7: if (2 * n <= FUZZ_BUF) { memcpy(m + n, m, n); n *= 2; } break;
    case 8: if (frame_off + 8 <= n) { wr16(m + frame_off + 3, (uint16_t)rnd(FUZZ_MAX + 4)); wr16(m + frame_off + 6, ref_crc16(m + frame_off + 2, 4)); } break;
    case 9: if (frame_off + 8 <= n) { m[frame_off + 2] = (uint8_t)rnd64(); wr16(m + frame_off + 6, ref_crc16(m + frame_off + 2, 4)); } break;
    case 10: /* damage the payload and keep its CRC valid: the result is a different but legal frame */
        if (frame_off + 10 <= n) {
            size_t len = rd16(m + frame_off + 3);

            if (len && frame_off + 10 + len <= n) {
                m[frame_off + 8 + rnd((uint32_t)len)] ^= (uint8_t)(1 + rnd(255));
                wr16(m + frame_off + 8 + len, ref_crc16(m + frame_off + 8, len));
            }
        }
        break;
    case 11: if (n > 4) { k = 1 + rnd((uint32_t)n / 2); i = rnd((uint32_t)(n - k + 1)); memmove(m + rnd((uint32_t)(n - k + 1)), m + i, k); } break;
    case 12: if (frame_off + 8 <= n) wr16(m + frame_off + 6, (uint16_t)rnd64()); break;
    default: if (n > 1) { i = rnd((uint32_t)n - 1); uint8_t t = m[i]; m[i] = m[i + 1]; m[i + 1] = t; } break;
    }
    return n;
}

static void fuzz_check(const uint8_t *m, size_t n, struct coverage *cov)
{
    uint8_t *d = dup_exact(m, n);
    struct sam_frame f;
    uint8_t type = 0, seq = 0;
    uint16_t len = 0;
    int rc, want;
    struct collected got;
    struct ref_frame *rf = calloc(n / SAM_MESSAGE_OVERHEAD + 2, sizeof *rf);
    size_t nrf = 0, mode = rnd(10), chunk = mode < 3 ? 0 : mode < 5 ? 1 : 1 + rnd(16), extra = rnd(3) ? 0 : rnd(70);
    struct sam_stats rst;
    uint8_t *encoded = malloc(n + 16);
    size_t enc = 0;

    /* Single-shot functions against the model, and an exact round trip whenever a frame is accepted. */
    want = ref_judge(d, n, FUZZ_MAX, &type, &seq, &len);
    rc = sam_frame_parse(d, n, FUZZ_MAX, &f);
    CHECK_EQ(rc, want);
    rc = sam_frame_decode_exact(d, n, FUZZ_MAX, &f);
    if (want == SAM_NEED_MORE)
        want = SAM_ERR_TRUNCATED;
    else if (want == SAM_OK && 10u + len != n)
        want = SAM_ERR_TRAILING;
    CHECK_EQ(rc, want);
    if (rc == SAM_OK) {
        uint8_t again[FUZZ_BUF];
        size_t w = 0;

        cov->single_ok++;
        CHECK_EQ(sam_frame_build(again, sizeof again, f.type, f.seq, f.payload, f.payload_len, &w), SAM_OK);
        CHECK(w == n && !memcmp(again, d, n));
    } else if (rc < 0 && -rc < 16) {
        cov->single_err[-rc]++;
    }

    /* Streaming parser, random chunking, against the model; then the "rejected or round-trips" property. */
    run_stream(d, n, FUZZ_MAX, extra, chunk, &got);
    ref_parse_all(d, n, FUZZ_MAX, rf, &nrf, &rst);
    CHECK(stats_equal(&got.st, &rst));
    CHECK_EQ(got.n, nrf);
    for (size_t i = 0; i < got.n && i < nrf; i++) {
        size_t w = 0;
        uint8_t again[FUZZ_BUF];

        CHECK(got.f[i].type == rf[i].type && got.f[i].seq == rf[i].seq && got.f[i].len == rf[i].len);
        CHECK_EQ(sam_frame_build(again, sizeof again, got.f[i].type, got.f[i].seq, got.f[i].payload, got.f[i].len, &w), SAM_OK);
        CHECK(w == 10u + rf[i].len && !memcmp(again, d + rf[i].off, w));
        if (enc + w <= n + 16) {
            memcpy(encoded + enc, again, w);
            enc += w;
        }
        cov->type[got.f[i].type == 0x00 ? 0 : got.f[i].type == 0x04 ? 1 : got.f[i].type == 0x40 ? 2 : 3]++;
    }
    cov->delivered += got.n;
    if (sam_stats_rejects(&got.st)) {
        cov->any_rejection++;
    } else {
        CHECK(enc == n && !memcmp(encoded, d, n)); /* no rejection means the input was exactly the delivered frames */
        cov->exact_roundtrips++;
    }
    {
        const uint64_t hit[8] = {got.st.rej_bad_sync, got.st.rej_header_crc, got.st.rej_unknown_type, got.st.rej_oversize,
                                 got.st.rej_bad_length, got.st.rej_payload_crc, got.st.rej_truncated, got.st.rej_trailing};

        for (int r = 0; r < 8; r++)
            cov->reason[r] += hit[r] > 0;
    }
    cov->cases++;
    collected_free(&got);
    free(rf);
    free(encoded);
    free(d);
}

static void fuzz(unsigned long iterations, uint64_t seed)
{
    struct coverage cov;
    uint8_t m[FUZZ_BUF];

    memset(&cov, 0, sizeof cov);
    rng_s = seed;
    for (unsigned long it = 0; it < iterations + iterations / 3; it++) {
        int multi = it >= iterations;
        size_t n = 0, offs[4] = {0, 0, 0, 0}, nf = multi ? 1 + rnd(4) : 1, nm = rnd(4);

        for (size_t f = 0; f < nf; f++) {
            if (f && rnd(3) == 0)
                for (unsigned g = 1 + rnd(6); g--;)
                    m[n++] = garbage_byte();
            offs[f] = n;
            n += gen_frame(m + n, sizeof m - n);
        }
        while (nm--)
            n = mutate(m, n, offs[rnd((uint32_t)nf)]);
        fuzz_check(m, n, &cov);
    }
    printf("fuzz: cases=%lu delivered=%lu exact_roundtrips=%lu with_rejection=%lu single_ok=%lu\n", cov.cases,
           cov.delivered, cov.exact_roundtrips, cov.any_rejection, cov.single_ok);
    printf("fuzz: frames data_nsq=%lu nak=%lu ack=%lu data_seq=%lu\n", cov.type[0], cov.type[1], cov.type[2], cov.type[3]);
    printf("fuzz: cases_with sync=%lu hdr_crc=%lu type=%lu oversize=%lu length=%lu pay_crc=%lu trunc=%lu trail=%lu\n",
           cov.reason[0], cov.reason[1], cov.reason[2], cov.reason[3], cov.reason[4], cov.reason[5], cov.reason[6], cov.reason[7]);
    /* The fuzz is only worth its name if it reaches every outcome; short runs (the determinism test) skip this. */
    if (iterations < 100000)
        return;
    CHECK(cov.cases >= iterations);
    CHECK(cov.exact_roundtrips > 100 && cov.any_rejection > 100 && cov.single_ok > 100);
    for (int t = 0; t < 4; t++)
        CHECK(cov.type[t] > 100);
    for (int r = 0; r < 8; r++)
        CHECK(cov.reason[r] > 10);
    for (int r = -SAM_ERR_BAD_SYNC; r <= -SAM_ERR_TRAILING; r++)
        CHECK(cov.single_err[r] > 10);
}

/* ---- Python differential: one command per line on stdin -------------------------------------------------- */

static void print_hex(const uint8_t *p, size_t n)
{
    if (!n)
        putchar('-');
    for (size_t i = 0; i < n; i++)
        printf("%02x", p[i]);
    putchar('\n');
}

/* One command, already split into tokens. Every answer is a few lines that tools/test-sam-frames.py predicts. */
static void batch_command(char **t, size_t nt, uint8_t *buf, size_t cap, uint8_t *out, size_t out_cap)
{
    const char *c = t[0];
    size_t n = 0, w = 0, args = nt - 1;
    int rc = SAM_ERR_ARG;
    uint8_t *d;

    if (!strcmp(c, "rqid") && args == 1) {
        unsigned r = (unsigned)strtoul(t[1], NULL, 16);
        printf("event=%d next=%04x\n", sam_rqid_is_event((uint16_t)r) ? 1 : 0, sam_rqid_next_request((uint16_t)r));
        return;
    }
    if (!args || (n = unhex(t[nt - 1], buf, cap)) == (size_t)-1) {
        puts("ERR input");
        return;
    }
    d = dup_exact(buf, n);
    if (!strcmp(c, "parse") && args == 4) { /* parse MAX CHUNK EXTRA HEX */
        struct collected col;

        run_stream(d, n, (uint16_t)strtoul(t[1], NULL, 10), strtoul(t[3], NULL, 10), strtoul(t[2], NULL, 10), &col);
        print_canonical(stdout, &col);
        collected_free(&col);
    } else if (!strcmp(c, "crc") && args == 1) {
        printf("crc=%04x\n", sam_crc16_update(SAM_CRC_INIT, d, n));
    } else if (!strcmp(c, "build") && args == 3) { /* build TYPE SEQ PAYLOAD */
        rc = sam_frame_build(out, out_cap, (uint8_t)strtoul(t[1], NULL, 16), (uint8_t)strtoul(t[2], NULL, 16), d, n, &w);
        if (rc == SAM_OK) {
            fputs("OK ", stdout);
            print_hex(out, w);
        } else {
            printf("ERR %s\n", sam_status_name(rc));
        }
    } else if (!strcmp(c, "cmdbuild") && args == 9) { /* cmdbuild TYPE SEQ TC TID SID IID RQID CID DATA */
        struct sam_command cmd = {(uint8_t)strtoul(t[3], NULL, 10), (uint8_t)strtoul(t[4], NULL, 10), (uint8_t)strtoul(t[5], NULL, 10),
                                  (uint8_t)strtoul(t[6], NULL, 10), (uint16_t)strtoul(t[7], NULL, 16), (uint8_t)strtoul(t[8], NULL, 10)};

        rc = sam_build_command(out, out_cap, (uint8_t)strtoul(t[1], NULL, 16), (uint8_t)strtoul(t[2], NULL, 16), &cmd, d, n, &w);
        if (rc == SAM_OK) {
            fputs("OK ", stdout);
            print_hex(out, w);
        } else {
            printf("ERR %s\n", sam_status_name(rc));
        }
    } else if (!strcmp(c, "cmddecode") && args == 1) {
        struct sam_command cmd;
        const uint8_t *data;
        size_t dlen;

        rc = sam_command_decode(d, n, &cmd, &data, &dlen);
        if (rc != SAM_OK) {
            printf("ERR %s\n", sam_status_name(rc));
        } else {
            printf("OK %u %u %u %u %u %u ", cmd.tc, cmd.tid, cmd.sid, cmd.iid, cmd.rqid, cmd.cid);
            print_hex(data, dlen);
        }
    } else if (!strcmp(c, "decode") && args == 2) { /* decode MAX HEX */
        struct sam_frame f;

        rc = sam_frame_decode_exact(d, n, (uint16_t)strtoul(t[1], NULL, 10), &f);
        if (rc != SAM_OK) {
            printf("ERR %s\n", sam_status_name(rc));
        } else {
            printf("OK %02x %02x ", f.type, f.seq);
            print_hex(f.payload, f.payload_len);
        }
    } else {
        puts("ERR command");
    }
    free(d);
}

static void batch(void)
{
    char *line = NULL;
    size_t cap = 0;
    ssize_t got;

    while ((got = getline(&line, &cap, stdin)) > 0) {
        char *tok[12], *save = NULL;
        size_t nt = 0, hexcap = (size_t)got / 2 + 16;
        uint8_t *buf = malloc(hexcap), *out = malloc(hexcap + 70000);

        for (char *s = strtok_r(line, " \n", &save); s && nt < 12; s = strtok_r(NULL, " \n", &save))
            tok[nt++] = s;
        if (nt)
            batch_command(tok, nt, buf, hexcap, out, hexcap + 70000);
        puts("END");
        free(buf);
        free(out);
    }
    free(line);
}

static void consts(void)
{
    printf("syn0 %u\nsyn1 %u\noverhead %u\npayload_max %u\ncrc_init %u\ncrc_check %u\ntype_data_nsq %u\ntype_nak %u\n"
           "type_ack %u\ntype_data_seq %u\npayload_type_cmd %u\ncommand_header_len %u\nevent_id_count %u\n",
           SAM_SYN_0, SAM_SYN_1, SAM_MESSAGE_OVERHEAD, SAM_PAYLOAD_MAX, SAM_CRC_INIT, SAM_CRC_CHECK_123456789,
           SAM_FRAME_DATA_NSQ, SAM_FRAME_NAK, SAM_FRAME_ACK, SAM_FRAME_DATA_SEQ, SAM_PAYLOAD_TYPE_CMD,
           SAM_COMMAND_HEADER_LEN, SAM_EVENT_ID_COUNT);
}

int main(int argc, char **argv)
{
    const char *mode = argc > 1 ? argv[1] : "selftest";

    if (!strcmp(mode, "batch")) {
        batch();
        return 0;
    }
    if (!strcmp(mode, "consts")) {
        consts();
        return 0;
    }
    if (!strcmp(mode, "selftest")) {
        rng_s = 0x5EED0001ull;
        test_crc();
        test_vectors();
        test_builder_refusals();
        test_single_shot_negatives();
        test_streaming();
        test_stream_incidents();
        test_parser_lifecycle();
        test_commands_and_ids();
    } else if (!strcmp(mode, "fuzz")) {
        unsigned long iterations = argc > 2 ? strtoul(argv[2], NULL, 0) : 150000ul;
        uint64_t seed = argc > 3 ? strtoull(argv[3], NULL, 0) : 0x5EED0002ull;

        fuzz(iterations, seed);
    } else {
        fprintf(stderr, "usage: %s selftest | fuzz [N [SEED]] | batch | consts\n", argv[0]);
        return 2;
    }
    printf("%s: %lu checks, %lu failures\n", mode, checks, failures);
    return failures ? 1 : 0;
}
