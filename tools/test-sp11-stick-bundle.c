/* SPDX-License-Identifier: MIT */
/* Host harness for probes/sp11-stick-bundle.h, driven by tools/test-sp11-stick-bundle.py and built with
 * -fsanitize=address,undefined. Built with -DSP11SB_FREESTANDING_UNIT the same file is a freestanding translation unit that
 * calls every check; the test compiles it with the monitor's flags to show the header needs no libc and no absolute pointers.
 *
 *   harness layout                 sizeof and offsetof of every record member, every constant, the error and stage tables
 *   harness sha                    SHA-256 of a fixed pattern at many lengths, with the hash this harness was built with
 *   harness batch  < scenarios     one verdict line per RUN
 *   harness fuzz   < scenario      deterministic single-bit mutations of one blob, one line each
 *
 * A scenario is text, one command per line; blobs are hex, "-" for a null pointer and "=" for an empty blob. Every blob is
 * copied into a heap block of exactly its length (a path into exactly 256 bytes, like the runner's table), so
 * AddressSanitizer reports any read past what a check was told it may read.
 *   BEGIN name | ARGS magic version size | FILE path base size | OBS <19 hex words> | PIN b | GUEST b | REPORT b |
 *   MAP stride b | DECL base size | POLICY n | RUN mode | FUZZ blob seed iterations
 * RUN modes: preflight pin pinbody files observed place guest digests report. Verdict: "name mode code NAME bad_chunk".
 * FUZZ takes an optional fifth word: the number of leading bytes of the blob that may be mutated (the head and table of a
 * GUEST.DAT, the head page of a REPORT.DAT), so the structure checks are reached more often than the data.
 * Built with -DSP11SB_Q1N1_ABI_CHECK -I<q1n1>/platform/uefi -DSNAP_PLATFORM_SP11=1 -DQ1N1_SP11_PAYLOAD=1 the file only asserts that
 * the runner constants the header repeats are the runner's own. */
#if defined(SP11SB_FREESTANDING_UNIT)
#include "../probes/sp11-stick-bundle.h"
int sp11sb_unit_entry(const struct sp11sb_env *e, const struct sp11sb_file *f, uint32_t *bad)
{
    int r = sp11sb_preflight(e, bad) | sp11sb_files_check(f, 5) | sp11sb_file_check(f, 4) | sp11sb_pin_check_nodigest(e->pin, e->pin_len);
    r |= sp11sb_guest_head_check(e->guest, e->guest_len, e->pin) | sp11sb_report_check(e->pin, e->pin_len) |
         (int)sp11sb_params_dest(e->guest, e->guest_len);
    return r + (int)(uintptr_t)sp11sb_error_name((uint32_t)r) + (int)(uintptr_t)sp11sb_stage_name((uint32_t)r);
}
#elif defined(SP11SB_Q1N1_ABI_CHECK)
#include "../probes/sp11-stick-bundle.h"
#include "sp11-payload.h"
_Static_assert(SP11SB_RUNNER_MAGIC == Q1N1_PAYLOAD_ABI_MAGIC && SP11SB_RUNNER_VERSION == Q1N1_PAYLOAD_ABI_VERSION, "runner ABI identity");
_Static_assert(SP11SB_RUNNER_ARGS_BYTES == sizeof(struct q1n1_payload_args), "runner ABI v1 size");
_Static_assert(SP11SB_FILES == Q1N1_PAYLOAD_MAX_FILES && SP11SB_FILE_MAX == Q1N1_PAYLOAD_MAX_FILE_BYTES, "runner file limits");
_Static_assert(SP11SB_LINE_MAX == Q1N1_PAYLOAD_LINE_MAX && SP11SB_REPORTER_LINES == Q1N1_PAYLOAD_LINES_MAX, "runner line limits");
_Static_assert(SP11SB_PATH_MAX == sizeof(((struct q1n1_preload_file *)0)->path), "runner path width");
_Static_assert(SP11SB_WRAPPER_LINES + SP11SB_REPORTER_LINES + 2 * 3 + SP11SB_FILES + 1 <= 89, "the unit's panel rows (docs/SP11-PAYLOAD.md)");
int main(void) { return 0; }
#else
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../probes/sp11-stick-bundle.h"
#ifdef SP11SB_HARNESS_Q1N1_SHA256
#include "sha256.h"
static void harness_sha256(const void *data, uint64_t size, uint8_t digest[32]) { q1n1_sha256(data, (size_t)size, digest); }
#else
/* An independent FIPS 180-4 implementation, so the checks can be exercised with no checkout of q1n1 (which supplies the
 * hash on the unit); the test compares both with hashlib. */
static uint32_t ror(uint32_t x, unsigned n) { return x >> n | x << (32 - n); }
static void block(uint32_t h[8], const uint8_t *p)
{
    static const uint32_t k[64] = {
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be,
        0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa,
        0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85,
        0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3,
        0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f,
        0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2};
    uint32_t w[64], v[8];
    unsigned i;
    for (i = 0; i < 16; i++) w[i] = (uint32_t)p[4 * i] << 24 | (uint32_t)p[4 * i + 1] << 16 | (uint32_t)p[4 * i + 2] << 8 | p[4 * i + 3];
    for (i = 16; i < 64; i++)
        w[i] = w[i - 16] + (ror(w[i - 15], 7) ^ ror(w[i - 15], 18) ^ (w[i - 15] >> 3)) + w[i - 7] +
               (ror(w[i - 2], 17) ^ ror(w[i - 2], 19) ^ (w[i - 2] >> 10));
    memcpy(v, h, sizeof v);
    for (i = 0; i < 64; i++) {
        uint32_t t = v[7] + (ror(v[4], 6) ^ ror(v[4], 11) ^ ror(v[4], 25)) + ((v[4] & v[5]) ^ (~v[4] & v[6])) + k[i] + w[i];
        uint32_t u = (ror(v[0], 2) ^ ror(v[0], 13) ^ ror(v[0], 22)) + ((v[0] & v[1]) ^ (v[0] & v[2]) ^ (v[1] & v[2]));
        memmove(v + 1, v, 7 * sizeof v[0]);
        v[4] += t;
        v[0] = t + u;
    }
    for (i = 0; i < 8; i++) h[i] += v[i];
}
static void harness_sha256(const void *data, uint64_t size, uint8_t digest[32])
{
    uint32_t h[8] = {0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};
    const uint8_t *p = data;
    uint8_t tail[128];
    uint64_t done = size / 64 * 64, rest = size - done, i;
    unsigned n;
    for (i = 0; i < done; i += 64) block(h, p + i);
    memset(tail, 0, sizeof tail);
    if (rest) memcpy(tail, p + done, (size_t)rest);
    tail[rest] = 0x80;
    n = rest < 56 ? 64 : 128;
    for (i = 0; i < 8; i++) tail[n - 1 - i] = (uint8_t)((size * 8) >> (8 * i));
    block(h, tail);
    if (n == 128) block(h, tail + 64);
    for (i = 0; i < 32; i++) digest[i] = (uint8_t)(h[i / 4] >> (24 - 8 * (i % 4)));
}
#endif

#define SIZE(type) printf("sizeof.%s %zu\n", #type, sizeof(struct type))
#define FIELD(type, field) printf("offsetof.%s.%s %zu\nsizeof.%s.%s %zu\n", #type, #field, offsetof(struct type, field), #type, #field, \
                                  sizeof(((struct type *)0)->field))
#define VALUE(name) printf("%s %llu\n", #name, (unsigned long long)(name))
#define TEXT(name) printf("%s %s\n", #name, name)

static void layout(void)
{
    SIZE(sp11sb_pin); SIZE(sp11sb_avoid); SIZE(sp11sb_acpi); SIZE(sp11sb_guest_head); SIZE(sp11sb_chunk); SIZE(sp11sb_report);
    FIELD(sp11sb_pin, magic); FIELD(sp11sb_pin, version); FIELD(sp11sb_pin, size); FIELD(sp11sb_pin, flags);
    FIELD(sp11sb_pin, authorised_stores); FIELD(sp11sb_pin, board_id); FIELD(sp11sb_pin, build_label);
    FIELD(sp11sb_pin, descriptor_sha256); FIELD(sp11sb_pin, authorisation_sha256); FIELD(sp11sb_pin, guest_file_size);
    FIELD(sp11sb_pin, guest_chunk_count); FIELD(sp11sb_pin, reserved0); FIELD(sp11sb_pin, guest_file_sha256);
    FIELD(sp11sb_pin, guest_index_sha256); FIELD(sp11sb_pin, boot_mpidr); FIELD(sp11sb_pin, cntfrq_hz); FIELD(sp11sb_pin, midr_count);
    FIELD(sp11sb_pin, midr); FIELD(sp11sb_pin, gicd_base); FIELD(sp11sb_pin, gicr_first); FIELD(sp11sb_pin, gicr_limit);
    FIELD(sp11sb_pin, gicr_stride); FIELD(sp11sb_pin, its_base); FIELD(sp11sb_pin, gicr_count); FIELD(sp11sb_pin, timer_intid);
    FIELD(sp11sb_pin, fb_base); FIELD(sp11sb_pin, fb_bytes); FIELD(sp11sb_pin, fb_width); FIELD(sp11sb_pin, fb_height);
    FIELD(sp11sb_pin, fb_stride); FIELD(sp11sb_pin, fb_format); FIELD(sp11sb_pin, place_base); FIELD(sp11sb_pin, place_size);
    FIELD(sp11sb_pin, guest_base); FIELD(sp11sb_pin, guest_size); FIELD(sp11sb_pin, reserve_size); FIELD(sp11sb_pin, work_base);
    FIELD(sp11sb_pin, work_size); FIELD(sp11sb_pin, avoid_count); FIELD(sp11sb_pin, reserved1); FIELD(sp11sb_pin, avoid);
    FIELD(sp11sb_pin, acpi_count); FIELD(sp11sb_pin, reserved2); FIELD(sp11sb_pin, acpi); FIELD(sp11sb_pin, reserved3);
    FIELD(sp11sb_pin, record_sha256);
    FIELD(sp11sb_avoid, base); FIELD(sp11sb_avoid, size); FIELD(sp11sb_avoid, tag); FIELD(sp11sb_avoid, reserved);
    FIELD(sp11sb_acpi, sig); FIELD(sp11sb_acpi, length); FIELD(sp11sb_acpi, sha256);
    FIELD(sp11sb_guest_head, magic); FIELD(sp11sb_guest_head, version); FIELD(sp11sb_guest_head, head_size);
    FIELD(sp11sb_guest_head, flags); FIELD(sp11sb_guest_head, entry_size); FIELD(sp11sb_guest_head, entry_count);
    FIELD(sp11sb_guest_head, reserved0); FIELD(sp11sb_guest_head, table_offset); FIELD(sp11sb_guest_head, data_offset);
    FIELD(sp11sb_guest_head, file_size); FIELD(sp11sb_guest_head, load_base); FIELD(sp11sb_guest_head, load_size);
    FIELD(sp11sb_guest_head, index_sha256); FIELD(sp11sb_guest_head, reserved1);
    FIELD(sp11sb_chunk, dest); FIELD(sp11sb_chunk, size); FIELD(sp11sb_chunk, file_offset); FIELD(sp11sb_chunk, kind);
    FIELD(sp11sb_chunk, flags); FIELD(sp11sb_chunk, reserved); FIELD(sp11sb_chunk, sha256);
    FIELD(sp11sb_report, magic); FIELD(sp11sb_report, version); FIELD(sp11sb_report, head_size); FIELD(sp11sb_report, stage);
    FIELD(sp11sb_report, refusal); FIELD(sp11sb_report, refusal_detail); FIELD(sp11sb_report, hash_policy);
    FIELD(sp11sb_report, lines_printed); FIELD(sp11sb_report, current_el); FIELD(sp11sb_report, midr_el1);
    FIELD(sp11sb_report, mpidr_el1); FIELD(sp11sb_report, cntfrq_el0); FIELD(sp11sb_report, hcr_el2); FIELD(sp11sb_report, sctlr_el2);
    FIELD(sp11sb_report, tcr_el2); FIELD(sp11sb_report, pin_code); FIELD(sp11sb_report, guest_code);
    FIELD(sp11sb_report, guest_file_base); FIELD(sp11sb_report, guest_file_size); FIELD(sp11sb_report, place_base);
    FIELD(sp11sb_report, place_size); FIELD(sp11sb_report, desc_base); FIELD(sp11sb_report, desc_pages); FIELD(sp11sb_report, desc_index);
    FIELD(sp11sb_report, chunks_total); FIELD(sp11sb_report, chunks_copied); FIELD(sp11sb_report, chunks_hashed);
    FIELD(sp11sb_report, bytes_copied); FIELD(sp11sb_report, bytes_hashed); FIELD(sp11sb_report, counter_hz);
    FIELD(sp11sb_report, ticks_enter); FIELD(sp11sb_report, ticks_placed); FIELD(sp11sb_report, ticks_copied);
    FIELD(sp11sb_report, ticks_hashed); FIELD(sp11sb_report, ticks_launch); FIELD(sp11sb_report, ticks_return);
    FIELD(sp11sb_report, monitor_out); FIELD(sp11sb_report, work_base); FIELD(sp11sb_report, work_size);
    FIELD(sp11sb_report, params_pa); FIELD(sp11sb_report, monitor_ret); FIELD(sp11sb_report, monitor_status);
    FIELD(sp11sb_report, monitor_entered);

    VALUE(SP11SB_VERSION); VALUE(SP11SB_RUNNER_MAGIC); VALUE(SP11SB_RUNNER_VERSION); VALUE(SP11SB_RUNNER_ARGS_BYTES);
    VALUE(SP11SB_FILES); VALUE(SP11SB_FILE_MAX); VALUE(SP11SB_PATH_MAX); VALUE(SP11SB_LINE_MAX); VALUE(SP11SB_REPORTER_LINES);
    VALUE(SP11SB_WRAPPER_LINES); VALUE(SP11SB_MIB); VALUE(SP11SB_IPA_LIMIT); VALUE(SP11SB_GUEST_ALIGN); VALUE(SP11SB_GUEST_MIN);
    VALUE(SP11SB_RESERVE_ALIGN); VALUE(SP11SB_RESERVE_MIN); VALUE(SP11SB_WORK_ALIGN); VALUE(SP11SB_WORK_BYTES);
    VALUE(SP11SB_EFI_CONVENTIONAL); VALUE(SP11SB_EFI_WB); VALUE(SP11SB_MAP_MIN_STRIDE); VALUE(SP11SB_MAP_MAX);
    VALUE(SP11SB_DECLARED_MAX); VALUE(SP11SB_PIN_BYTES); VALUE(SP11SB_MIDR_MAX); VALUE(SP11SB_AVOID_MAX); VALUE(SP11SB_ACPI_MAX);
    VALUE(SP11SB_GICR_MAX); VALUE(SP11SB_GUEST_HEAD_BYTES); VALUE(SP11SB_CHUNK_BYTES); VALUE(SP11SB_CHUNKS_MAX);
    VALUE(SP11SB_DEST_ALIGN); VALUE(SP11SB_DATA_ALIGN); VALUE(SP11SB_REPORT_HEAD_PAGE); VALUE(SP11SB_MONITOR_OUT_BYTES);
    VALUE(SP11SB_REPORT_BYTES); VALUE(SP11SB_REPORT_HEAD_BYTES); VALUE(SP11SB_REPORT_OUT); VALUE(SP11SB_NAME_MAX);
    VALUE(SP11SB_RET_REFUSED); VALUE(SP11SB_ROLE_MONITOR); VALUE(SP11SB_ROLE_REPORTER); VALUE(SP11SB_ROLE_GUEST);
    VALUE(SP11SB_ROLE_PIN); VALUE(SP11SB_ROLE_REPORT); VALUE(SP11SB_KIND_KERNEL); VALUE(SP11SB_KIND_TABLES);
    VALUE(SP11SB_KIND_HANDOFF); VALUE(SP11SB_KIND_DEVICE_TREE); VALUE(SP11SB_KIND_PARAMS); VALUE(SP11SB_KIND_MONITOR_IMAGE);
    VALUE(SP11SB_KIND_MONITOR_DATA); VALUE(SP11SB_KIND_OTHER); VALUE(SP11SB_KIND_MAX); VALUE(SP11SB_CHUNK_ZERO);
    VALUE(SP11SB_STORE_RAM); VALUE(SP11SB_STORE_EL2_SYSREG); VALUE(SP11SB_STORE_GIC); VALUE(SP11SB_STORE_REQUIRED);
    VALUE(SP11SB_STORE_ALL); VALUE(SP11SB_HASH_CHUNKS); VALUE(SP11SB_HASH_FILE); VALUE(SP11SB_AVOID_EL2_UNREACHABLE);
    VALUE(SP11SB_AVOID_DEVICE_OWNED); VALUE(SP11SB_AVOID_OPERATOR); VALUE(SP11SB_SIDE_B); VALUE(SP11SB_SIDE_W); VALUE(SP11SB_SIDE_R);
    VALUE(SP11SB_ERROR_ROWS); VALUE(SP11SB_STAGE_COUNT);
    TEXT(SP11SB_PIN_MAGIC); TEXT(SP11SB_GUEST_MAGIC); TEXT(SP11SB_REPORT_MAGIC);
    {
        unsigned i;
        for (i = 1; i < SP11SB_ERROR_ROWS; i++)
            printf("error %u %u %s\n", sp11sb_errors[i].code, sp11sb_errors[i].sides, sp11sb_errors[i].name);
        for (i = 0; i < SP11SB_STAGE_COUNT; i++) printf("stage %u %s\n", sp11sb_stages[i].value, sp11sb_stages[i].name);
    }
}

static void sha_selftest(void)
{
    static const unsigned lengths[] = {0, 1, 2, 3, 55, 56, 57, 63, 64, 65, 119, 120, 127, 128, 129, 255, 256, 1000, 4096, 65537};
    uint8_t *data = malloc(65537), digest[32];
    unsigned i, j, n;
    for (i = 0; i < 65537; i++) data[i] = (uint8_t)(i * 131u + 7u);
    for (n = 0; n < sizeof lengths / sizeof lengths[0]; n++) {
        uint8_t *exact = malloc(lengths[n] ? lengths[n] : 1);
        memcpy(exact, data, lengths[n]);
        harness_sha256(exact, lengths[n], digest);
        printf("%u ", lengths[n]);
        for (j = 0; j < 32; j++) printf("%02x", digest[j]);
        printf("\n");
        free(exact);
    }
    free(data);
}

/* ---- scenarios */
struct blob { uint8_t *p; uint64_t n; };
struct scenario {
    char name[96];
    uint64_t magic;
    uint32_t version, size, nfiles, ndecl, policy;
    struct sp11sb_file file[16];
    char *path[16];
    struct blob pin, guest, report, map;
    uint64_t stride;
    struct sp11sb_range declared[SP11SB_DECLARED_MAX + 8];
    struct sp11sb_observed obs;
};

static void blob_free(struct blob *b) { free(b->p); b->p = NULL; b->n = 0; }
static void scenario_reset(struct scenario *s)
{
    unsigned i;
    blob_free(&s->pin); blob_free(&s->guest); blob_free(&s->report); blob_free(&s->map);
    for (i = 0; i < s->nfiles; i++) free(s->path[i]);
    memset(s, 0, sizeof *s);
}
static int hexval(int c) { return isdigit(c) ? c - '0' : (c >= 'a' && c <= 'f') ? c - 'a' + 10 : (c >= 'A' && c <= 'F') ? c - 'A' + 10 : -1; }
static void blob_set(struct blob *b, const char *text)
{
    size_t len = strlen(text), i;
    blob_free(b);
    if (!strcmp(text, "-")) return;
    if (!strcmp(text, "=")) { b->p = malloc(1); return; }
    if (len % 2) { fprintf(stderr, "odd hex length\n"); exit(2); }
    b->n = len / 2;
    b->p = malloc(b->n);
    for (i = 0; i < b->n; i++) {
        int hi = hexval(text[2 * i]), lo = hexval(text[2 * i + 1]);
        if (hi < 0 || lo < 0) { fprintf(stderr, "bad hex\n"); exit(2); }
        b->p[i] = (uint8_t)(hi << 4 | lo);
    }
}

static int run_mode(struct scenario *s, const char *mode, uint32_t *bad)
{
    struct sp11sb_env env;
    memset(&env, 0, sizeof env);
    env.args_magic = s->magic; env.args_version = s->version; env.args_size = s->size;
    env.files = s->file; env.nfiles = s->nfiles;
    env.pin = s->pin.p; env.pin_len = s->pin.n; env.guest = s->guest.p; env.guest_len = s->guest.n;
    env.obs = s->obs; env.map = s->map.p; env.map_size = s->map.n; env.map_stride = s->stride;
    env.declared = s->declared; env.ndeclared = s->ndecl; env.hash_policy = s->policy; env.sha256 = harness_sha256;
    if (!strcmp(mode, "preflight")) return sp11sb_preflight(&env, bad);
    if (!strcmp(mode, "pin")) return sp11sb_pin_check(s->pin.p, s->pin.n, harness_sha256);
    if (!strcmp(mode, "pinbody")) return sp11sb_pin_check_nodigest(s->pin.p, s->pin.n);
    if (!strcmp(mode, "files")) return sp11sb_files_check(s->file, s->nfiles);
    if (!strcmp(mode, "observed")) return sp11sb_observed_check(s->pin.p, &s->obs);
    if (!strcmp(mode, "place"))
        return sp11sb_place_check(s->pin.p, s->map.p, s->map.n, s->stride, s->obs.fb_base, s->obs.fb_bytes, s->declared, s->ndecl);
    if (!strcmp(mode, "guest")) return sp11sb_guest_check(s->guest.p, s->guest.n, s->pin.p);
    if (!strcmp(mode, "digests")) return sp11sb_guest_digests(s->guest.p, s->guest.n, s->pin.p, s->policy, harness_sha256, bad);
    if (!strcmp(mode, "report")) return sp11sb_report_check(s->report.p, s->report.n);
    fprintf(stderr, "unknown mode %s\n", mode);
    exit(2);
}

/* xorshift64*, the generator tools/sp11_stick_bundle.py implements too. */
static uint64_t next_random(uint64_t *state)
{
    uint64_t x = *state;
    x ^= x >> 12; x ^= x << 25; x ^= x >> 27;
    *state = x;
    return x * UINT64_C(0x2545F4914F6CDD1D);
}

/* The checks that read a blob: every one is run on every mutation, and their codes are printed in this order. */
static void fuzz_one(struct scenario *s, const char *which, uint64_t seed, unsigned iterations, uint64_t limit)
{
    uint64_t state = seed;
    unsigned i;
    struct blob *target = !strcmp(which, "pin") ? &s->pin : !strcmp(which, "guest") ? &s->guest : !strcmp(which, "map") ? &s->map :
                          !strcmp(which, "report") ? &s->report : NULL;
    int path = !strcmp(which, "path");
    for (i = 0; i < iterations; i++) {
        uint64_t r = next_random(&state), pos = 0;
        unsigned bit = (unsigned)(r & 7), idx = 0;
        uint8_t *buf;
        uint64_t len;
        uint32_t bad;
        int codes[8], n = 0, k;
        if (path) { idx = i % s->nfiles; buf = (uint8_t *)s->path[idx]; len = SP11SB_PATH_MAX; }
        else if (target && target->n) { buf = target->p; len = target->n; }
        else { fprintf(stderr, "nothing to mutate\n"); exit(2); }
        pos = (r >> 8) % (limit && limit < len ? limit : len);
        buf[pos] ^= (uint8_t)(1u << bit);
        if (!strcmp(which, "pin")) {
            codes[n++] = run_mode(s, "pin", &bad); codes[n++] = run_mode(s, "pinbody", &bad); codes[n++] = run_mode(s, "observed", &bad);
            codes[n++] = run_mode(s, "place", &bad); codes[n++] = run_mode(s, "guest", &bad);
            s->policy = SP11SB_HASH_CHUNKS | SP11SB_HASH_FILE;
            codes[n++] = run_mode(s, "digests", &bad);
        } else if (!strcmp(which, "guest")) {
            codes[n++] = run_mode(s, "guest", &bad);
            s->policy = SP11SB_HASH_CHUNKS | SP11SB_HASH_FILE;
            codes[n++] = run_mode(s, "digests", &bad);
        } else if (!strcmp(which, "map")) {
            codes[n++] = run_mode(s, "place", &bad);
        } else if (!strcmp(which, "report")) {
            codes[n++] = run_mode(s, "report", &bad);
        } else {
            codes[n++] = run_mode(s, "files", &bad);
        }
        printf("fuzz %s %u %llu %u", which, i, (unsigned long long)pos, bit);
        for (k = 0; k < n; k++) printf(" %d", codes[k]);
        printf("\n");
        buf[pos] ^= (uint8_t)(1u << bit);
    }
}

static int run_stream(void)
{
    struct scenario s;
    char *line = NULL;
    size_t cap = 0;
    memset(&s, 0, sizeof s);
    while (getline(&line, &cap, stdin) > 0) {
        char *w[24], *save = NULL, *tok;
        int n = 0;
        for (tok = strtok_r(line, " \r\n", &save); tok && n < 24; tok = strtok_r(NULL, " \r\n", &save)) w[n++] = tok;
        if (!n) continue;
        if (!strcmp(w[0], "BEGIN") && n == 2) { scenario_reset(&s); snprintf(s.name, sizeof s.name, "%s", w[1]); }
        else if (!strcmp(w[0], "ARGS") && n == 4) { s.magic = strtoull(w[1], NULL, 16); s.version = (uint32_t)atoi(w[2]); s.size = (uint32_t)atoi(w[3]); }
        else if (!strcmp(w[0], "FILE") && n == 4 && s.nfiles < 16) {
            struct blob p = {0, 0};
            blob_set(&p, w[1]);
            s.path[s.nfiles] = calloc(1, SP11SB_PATH_MAX);
            memcpy(s.path[s.nfiles], p.p, p.n > SP11SB_PATH_MAX ? SP11SB_PATH_MAX : p.n);
            blob_free(&p);
            s.file[s.nfiles].path = s.path[s.nfiles];
            s.file[s.nfiles].base = strtoull(w[2], NULL, 16);
            s.file[s.nfiles].size = strtoull(w[3], NULL, 16);
            s.nfiles++;
        } else if (!strcmp(w[0], "OBS") && n == 20) {
            uint64_t v[19];
            int i;
            for (i = 0; i < 19; i++) v[i] = strtoull(w[i + 1], NULL, 16);
            s.obs = (struct sp11sb_observed){v[0], v[1], v[2], v[3], v[4], v[5], v[6], v[7], v[8], v[9], v[10], v[11], v[12], v[13], v[14],
                                             (uint32_t)v[15], (uint32_t)v[16], (uint32_t)v[17], (uint32_t)v[18]};
        }
        else if (!strcmp(w[0], "PIN") && n == 2) blob_set(&s.pin, w[1]);
        else if (!strcmp(w[0], "GUEST") && n == 2) blob_set(&s.guest, w[1]);
        else if (!strcmp(w[0], "REPORT") && n == 2) blob_set(&s.report, w[1]);
        else if (!strcmp(w[0], "MAP") && n == 3) { s.stride = strtoull(w[1], NULL, 16); blob_set(&s.map, w[2]); }
        else if (!strcmp(w[0], "DECL") && n == 3 && s.ndecl < sizeof s.declared / sizeof s.declared[0]) {
            s.declared[s.ndecl].base = strtoull(w[1], NULL, 16);
            s.declared[s.ndecl++].size = strtoull(w[2], NULL, 16);
        } else if (!strcmp(w[0], "POLICY") && n == 2) s.policy = (uint32_t)strtoul(w[1], NULL, 16);
        else if (!strcmp(w[0], "RUN") && n == 2 && !strcmp(w[1], "params"))
            printf("%s params %llu\n", s.name, (unsigned long long)sp11sb_params_dest(s.guest.p, s.guest.n));
        else if (!strcmp(w[0], "RUN") && n == 2) {
            uint32_t bad = 0xFFFFFFFFu;
            int code = run_mode(&s, w[1], &bad);
            printf("%s %s %d %s %d\n", s.name, w[1], code, sp11sb_error_name((uint32_t)code), bad == 0xFFFFFFFFu ? -1 : (int)bad);
        } else if (!strcmp(w[0], "FUZZ") && (n == 4 || n == 5))
            fuzz_one(&s, w[1], strtoull(w[2], NULL, 16), (unsigned)atoi(w[3]), n == 5 ? strtoull(w[4], NULL, 10) : 0);
        else { fprintf(stderr, "bad scenario line: %s\n", w[0]); return 2; }
    }
    scenario_reset(&s);
    free(line);
    return 0;
}

int main(int argc, char **argv)
{
    if (argc == 2 && !strcmp(argv[1], "layout")) { layout(); return 0; }
    if (argc == 2 && !strcmp(argv[1], "sha")) { sha_selftest(); return 0; }
    if (argc == 2 && (!strcmp(argv[1], "batch") || !strcmp(argv[1], "fuzz"))) return run_stream();
    fprintf(stderr, "usage: %s layout|sha|batch|fuzz\n", argv[0]);
    return 2;
}
#endif
