/* SPDX-License-Identifier: MIT */
/* Host test for compat/nv2-gate.c, the SP11 fail-closed gate in front of
 * HCR_EL2.NV/NV2. tools/test-nv2-gate.py builds it (address and undefined
 * behaviour sanitizers), runs the self-test, and drives the batch modes against
 * tools/nv2_record.py.
 *
 * Nothing here borrows from the code under test: records are built from literal
 * offsets and sealed with a table-driven CRC-32, so a wrong offset, byte order
 * or polynomial in compat/nv2-gate.c cannot cancel against a copy of itself.
 *
 *   test-nv2-gate                          self-test; prints PASS
 *   test-nv2-gate parse                    stdin: a hex record per line, "." for none
 *   test-nv2-gate decide                   stdin: "<hex|.> <soc> <midr> <revidr> <mmfr2> <mmfr4>"
 *   test-nv2-gate status                   stdin: a verdict number (hex) per line
 *   test-nv2-gate embedded <midr> <revidr> <mmfr2> <mmfr4>
 *
 * The values below are made up: no byte of any register is a measurement. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "nv2-gate.h"

#define CHECK(x) \
    do { \
        if (!(x)) { \
            fprintf(stderr, "%s:%d CHECK(%s) failed\n", __FILE__, __LINE__, #x); \
            exit(1); \
        } \
    } while (0)

static void verdict_eq(enum snap_nv2_verdict got, enum snap_nv2_verdict want, int line)
{
    if (got != want) {
        fprintf(stderr, "%s:%d verdict %#x, expected %#x\n", __FILE__, line,
                (unsigned)got, (unsigned)want);
        exit(1);
    }
}
#define VERDICT_EQ(got, want) verdict_eq((got), (want), __LINE__)

#define REC 96
#define SOC_X1E SNAP_NV2_SOC_X1E80100

static uint32_t crc_table[256];

static void crc_init(void)
{
    uint32_t n, c;
    int k;

    for (n = 0; n < 256; n++) {
        c = n;
        for (k = 0; k < 8; k++)
            c = (c & 1) ? 0xedb88320u ^ (c >> 1) : c >> 1;
        crc_table[n] = c;
    }
}

static uint32_t ref_crc32(const uint8_t *p, size_t n)
{
    uint32_t c = 0xffffffffu;

    while (n--)
        c = crc_table[(c ^ *p++) & 0xff] ^ (c >> 8);
    return ~c;
}

static void put_le(uint8_t *p, uint64_t v, int bytes)
{
    int i;

    for (i = 0; i < bytes; i++)
        p[i] = (uint8_t)(v >> (8 * i));
}

struct spec {
    uint32_t soc, build_id;
    uint64_t midr, revidr, mmfr2, mmfr4;
    uint8_t vncr, negctl, evidence;
    const char *tool;
};

struct live {
    uint64_t midr, revidr, mmfr2, mmfr4;
};

/* Every byte of each 64-bit value differs, so a swapped or misplaced byte
 * shows. mmfr2 has NV = 2 in [27:24]; mmfr4 has NV_frac = 0 in [23:20] and
 * E2H0 = 0xf in [27:24], which the gate must not mistake for NV_frac. */
static struct live base_live(void)
{
    struct live l = { UINT64_C(0xfedcba9876543210), UINT64_C(0x0102030405060708),
                      UINT64_C(0x1122334402667788), UINT64_C(0x99aabbcc0f0e0102) };
    return l;
}

static struct spec base_spec(void)
{
    struct live l = base_live();
    struct spec s = { SOC_X1E, 0x0badf00du, l.midr, l.revidr, l.mmfr2, l.mmfr4,
                      SNAP_NV2_PASS, SNAP_NV2_PASS, SNAP_NV2_EVIDENCE_VERIFIED, "unit-test.1" };
    return s;
}

static void reseal(uint8_t r[REC])
{
    put_le(r + 0x5c, ref_crc32(r, 0x5c), 4);
}

static void mk(uint8_t r[REC], const struct spec *s)
{
    memset(r, 0, REC);
    r[0] = 0x4e; r[1] = 0x56; r[2] = 0x32; r[3] = 0x52;
    put_le(r + 0x04, 1, 2);
    put_le(r + 0x08, s->soc, 4);
    put_le(r + 0x0c, s->build_id, 4);
    put_le(r + 0x10, s->midr, 8);
    put_le(r + 0x18, s->revidr, 8);
    put_le(r + 0x20, s->mmfr2, 8);
    put_le(r + 0x28, s->mmfr4, 8);
    r[0x30] = s->vncr;
    r[0x31] = s->negctl;
    r[0x32] = s->evidence;
    memcpy(r + 0x38, s->tool, strlen(s->tool));
    reseal(r);
}

static enum snap_nv2_verdict decide_live(const uint8_t *r, size_t len, uint32_t soc,
                                         const struct live *l)
{
    return snap_nv2_gate_decide(r, len, soc, l->midr, l->revidr, l->mmfr2, l->mmfr4);
}

static enum snap_nv2_verdict decide_base(const uint8_t *r, size_t len)
{
    struct live l = base_live();

    return decide_live(r, len, SOC_X1E, &l);
}

/* A record field changes, the live registers stay as the record was made. */
#define RECORD_CASE(change, want) \
    do { \
        struct spec s_ = base_spec(); \
        uint8_t r_[REC]; \
        change; \
        mk(r_, &s_); \
        verdict_eq(decide_base(r_, REC), (want), __LINE__); \
    } while (0)

/* The live registers change, the record stays as it was made. */
#define LIVE_CASE(change, want) \
    do { \
        struct spec s_ = base_spec(); \
        struct live l_ = base_live(); \
        uint8_t r_[REC]; \
        mk(r_, &s_); \
        change; \
        verdict_eq(decide_live(r_, REC, SOC_X1E, &l_), (want), __LINE__); \
    } while (0)

/* A byte of the finished record changes and the record is sealed again, so only
 * the field checks can object. */
#define BYTE_CASE(offset, value, want) \
    do { \
        struct spec s_ = base_spec(); \
        uint8_t r_[REC]; \
        mk(r_, &s_); \
        r_[(offset)] = (value); \
        reseal(r_); \
        verdict_eq(decide_base(r_, REC), (want), __LINE__); \
    } while (0)

static int tool_valid_ref(const uint8_t t[16])
{
    int n = 0, i;

    while (n < 16 && t[n]) {
        uint8_t c = t[n];

        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
              c == '.' || c == '_' || c == '-'))
            return 0;
        n++;
    }
    if (n == 0 || n == 16)
        return 0;
    for (i = n; i < 16; i++)
        if (t[i])
            return 0;
    return 1;
}

static void test_enums_and_constants(void)
{
    CHECK(SNAP_NV2_DENY_NO_RECORD == 0);
    CHECK(SNAP_NV2_DENY_COUNT == 16);
    CHECK((unsigned)SNAP_NV2_ALLOW == 0x4e32414cu);
    CHECK((unsigned)SNAP_NV2_PARSED == 0x4e325052u);
    CHECK((unsigned)SNAP_NV2_ALLOW >= (unsigned)SNAP_NV2_DENY_COUNT);
    CHECK((unsigned)SNAP_NV2_PARSED >= (unsigned)SNAP_NV2_DENY_COUNT);
    CHECK(SNAP_NV2_RECORD_SIZE == 96 && SNAP_NV2_RECORD_VERSION == 1);
    CHECK(SNAP_NV2_RECORD_MAGIC == 0x5232564eu);
    CHECK(SNAP_NV2_NOT_RUN == 0 && SNAP_NV2_PASS == 1 && SNAP_NV2_FAIL == 2);
    CHECK(SNAP_NV2_PLATFORM_SOC == SNAP_NV2_SOC_X1E80100);
    CHECK(SNAP_NV2_SOC_UNKNOWN == 0 && SNAP_NV2_SOC_X1E80100 == 1 &&
          SNAP_NV2_SOC_X2E_96_100 == 2);
    CHECK(SNAP_NV2_EVIDENCE_NONE == 0 && SNAP_NV2_EVIDENCE_VERIFIED == 1 &&
          SNAP_NV2_EVIDENCE_OBSERVED == 2 && SNAP_NV2_EVIDENCE_INFERRED == 3 &&
          SNAP_NV2_EVIDENCE_EXPERIMENTAL == 4 && SNAP_NV2_EVIDENCE_UNIMPLEMENTED == 5);
}

static void test_crc(void)
{
    uint8_t buf[300], zeros[32];
    uint32_t x = 0x2545f491u;
    size_t n, i;

    CHECK(snap_nv2_crc32("123456789", 9) == 0xcbf43926u);
    CHECK(snap_nv2_crc32("", 0) == 0);
    CHECK(snap_nv2_crc32("a", 1) == 0xe8b7be43u);
    memset(zeros, 0, sizeof zeros);
    CHECK(snap_nv2_crc32(zeros, 4) == 0x2144df1cu);
    CHECK(snap_nv2_crc32(zeros, 32) == 0x190a55adu);
    for (i = 0; i < sizeof buf; i++) {
        x ^= x << 13; x ^= x >> 17; x ^= x << 5;
        buf[i] = (uint8_t)x;
    }
    for (n = 0; n <= sizeof buf; n++)
        CHECK(snap_nv2_crc32(buf, n) == ref_crc32(buf, n));
    CHECK(ref_crc32((const uint8_t *)"123456789", 9) == 0xcbf43926u);
}

static void test_advertise(void)
{
    static const uint64_t junk[] = { 0, ~UINT64_C(0), UINT64_C(0x5555555555555555),
                                     UINT64_C(0xaaaaaaaaaaaaaaaa) };
    unsigned nv, frac, k;

    for (k = 0; k < 4; k++)
        for (nv = 0; nv < 16; nv++)
            for (frac = 0; frac < 16; frac++) {
                uint64_t mmfr2 = (junk[k] & ~(UINT64_C(0xf) << 24)) | ((uint64_t)nv << 24);
                uint64_t mmfr4 = (junk[k] & ~(UINT64_C(0xf) << 20)) | ((uint64_t)frac << 20);

                CHECK(snap_nv2_id_advertises_nv2(mmfr2, mmfr4) == (nv == 2 && frac == 0));
            }
    /* NV_frac is [23:20]; E2H0, the neighbouring field at [27:24], is not it. */
    CHECK(snap_nv2_id_advertises_nv2(UINT64_C(0x02000000), UINT64_C(0x0f000000)));
    CHECK(snap_nv2_id_advertises_nv2(UINT64_C(0x02000000), UINT64_C(0x01000000)));
    CHECK(!snap_nv2_id_advertises_nv2(UINT64_C(0x02000000), UINT64_C(0x00100000)));
    /* The NV field is MMFR2 [27:24], not [31:28] or [23:20]. */
    CHECK(!snap_nv2_id_advertises_nv2(UINT64_C(0x20000000), 0));
    CHECK(!snap_nv2_id_advertises_nv2(UINT64_C(0x00200000), 0));
    CHECK(snap_nv2_id_advertises_nv2(UINT64_C(0x02000000), 0));
}

static void test_allow(void)
{
    struct spec s = base_spec();
    struct live l = base_live();
    uint8_t r[REC];
    struct snap_nv2_view v;

    mk(r, &s);
    VERDICT_EQ(decide_base(r, REC), SNAP_NV2_ALLOW);
    VERDICT_EQ(snap_nv2_record_parse(r, REC, &v), SNAP_NV2_PARSED);
    CHECK(v.soc == 1 && v.build_id == 0x0badf00du);
    CHECK(v.midr_el1 == l.midr && v.revidr_el1 == l.revidr);
    CHECK(v.id_aa64mmfr2_el1 == l.mmfr2 && v.id_aa64mmfr4_el1 == l.mmfr4);
    CHECK(v.vncr_redirect == 1 && v.negative_control_trapped == 1 && v.evidence == 1);

    RECORD_CASE(s_.evidence = SNAP_NV2_EVIDENCE_OBSERVED, SNAP_NV2_ALLOW);
    RECORD_CASE(s_.build_id = 0, SNAP_NV2_ALLOW);
    RECORD_CASE(s_.tool = "a", SNAP_NV2_ALLOW);
    RECORD_CASE(s_.tool = "abcdefghijklmno", SNAP_NV2_ALLOW);
    RECORD_CASE(s_.tool = "A-b_c.9", SNAP_NV2_ALLOW);
}

static void test_named_denials(void)
{
    /* The functional result. */
    RECORD_CASE(s_.vncr = SNAP_NV2_FAIL, SNAP_NV2_DENY_VNCR_FAIL);
    RECORD_CASE(s_.vncr = SNAP_NV2_NOT_RUN, SNAP_NV2_DENY_VNCR_NOT_RUN);
    RECORD_CASE(s_.negctl = SNAP_NV2_NOT_RUN, SNAP_NV2_DENY_NEGCTL_NOT_RUN);
    RECORD_CASE(s_.negctl = SNAP_NV2_FAIL, SNAP_NV2_DENY_NEGCTL_FAIL);
    RECORD_CASE(s_.evidence = SNAP_NV2_EVIDENCE_NONE, SNAP_NV2_DENY_EVIDENCE);
    RECORD_CASE(s_.evidence = SNAP_NV2_EVIDENCE_INFERRED, SNAP_NV2_DENY_EVIDENCE);
    RECORD_CASE(s_.evidence = SNAP_NV2_EVIDENCE_EXPERIMENTAL, SNAP_NV2_DENY_EVIDENCE);
    RECORD_CASE(s_.evidence = SNAP_NV2_EVIDENCE_UNIMPLEMENTED, SNAP_NV2_DENY_EVIDENCE);
    RECORD_CASE(s_.vncr = 3, SNAP_NV2_DENY_RESERVED);
    RECORD_CASE(s_.negctl = 0xff, SNAP_NV2_DENY_RESERVED);
    RECORD_CASE(s_.evidence = 6, SNAP_NV2_DENY_RESERVED);

    /* Another SoC, core or ID state than the one recorded. */
    RECORD_CASE(s_.soc = SNAP_NV2_SOC_X2E_96_100, SNAP_NV2_DENY_SOC);
    RECORD_CASE(s_.soc = SNAP_NV2_SOC_UNKNOWN, SNAP_NV2_DENY_SOC);
    RECORD_CASE(s_.soc = 3, SNAP_NV2_DENY_RESERVED);
    RECORD_CASE(s_.midr ^= 1, SNAP_NV2_DENY_MIDR);
    RECORD_CASE(s_.midr ^= UINT64_C(1) << 63, SNAP_NV2_DENY_MIDR);
    RECORD_CASE(s_.revidr ^= 1, SNAP_NV2_DENY_REVIDR);
    RECORD_CASE(s_.mmfr2 ^= 1, SNAP_NV2_DENY_ID_MISMATCH);
    RECORD_CASE(s_.mmfr4 ^= UINT64_C(1) << 63, SNAP_NV2_DENY_ID_MISMATCH);
    /* A record of a core that did not advertise NV2, read on one that does. */
    RECORD_CASE(s_.mmfr2 &= ~(UINT64_C(0xf) << 24), SNAP_NV2_DENY_ID_MISMATCH);

    /* The live ID registers do not advertise NV2: the record cannot make up for it. */
    LIVE_CASE(l_.mmfr2 &= ~(UINT64_C(0xf) << 24), SNAP_NV2_DENY_NOT_ADVERTISED);
    LIVE_CASE(l_.mmfr2 = (l_.mmfr2 & ~(UINT64_C(0xf) << 24)) | (UINT64_C(1) << 24),
              SNAP_NV2_DENY_NOT_ADVERTISED);
    LIVE_CASE(l_.mmfr2 = (l_.mmfr2 & ~(UINT64_C(0xf) << 24)) | (UINT64_C(3) << 24),
              SNAP_NV2_DENY_NOT_ADVERTISED);
    LIVE_CASE(l_.mmfr2 |= UINT64_C(0xf) << 24, SNAP_NV2_DENY_NOT_ADVERTISED);
    LIVE_CASE(l_.mmfr4 = (l_.mmfr4 & ~(UINT64_C(0xf) << 20)) | (UINT64_C(1) << 20),
              SNAP_NV2_DENY_NOT_ADVERTISED);
    LIVE_CASE(l_.mmfr4 |= UINT64_C(0xf) << 20, SNAP_NV2_DENY_NOT_ADVERTISED);
    /* E2H0 is not NV_frac: another value there is a mismatch with the record, not a refusal to advertise. */
    LIVE_CASE(l_.mmfr4 = (l_.mmfr4 & ~(UINT64_C(0xf) << 24)) | (UINT64_C(0xe) << 24),
              SNAP_NV2_DENY_ID_MISMATCH);
    LIVE_CASE(l_.midr ^= 1, SNAP_NV2_DENY_MIDR);
    LIVE_CASE(l_.revidr ^= UINT64_C(1) << 40, SNAP_NV2_DENY_REVIDR);

    /* An unread register is not a core: zero never agrees with zero. */
    LIVE_CASE(l_.midr = 0, SNAP_NV2_DENY_MIDR);
    {
        struct spec s = base_spec();
        struct live l = base_live();
        uint8_t r[REC];

        s.midr = 0;
        l.midr = 0;
        mk(r, &s);
        VERDICT_EQ(decide_live(r, REC, SOC_X1E, &l), SNAP_NV2_DENY_MIDR);
    }
    /* The monitor build names its SoC; a build that names none allows nothing. */
    {
        struct spec s = base_spec();
        struct live l = base_live();
        uint8_t r[REC];

        mk(r, &s);
        VERDICT_EQ(decide_live(r, REC, SNAP_NV2_SOC_UNKNOWN, &l), SNAP_NV2_DENY_SOC);
        VERDICT_EQ(decide_live(r, REC, SNAP_NV2_SOC_X2E_96_100, &l), SNAP_NV2_DENY_SOC);
        s.soc = SNAP_NV2_SOC_UNKNOWN;
        mk(r, &s);
        VERDICT_EQ(decide_live(r, REC, SNAP_NV2_SOC_UNKNOWN, &l), SNAP_NV2_DENY_SOC);
    }
}

static void test_damage(void)
{
    struct spec s = base_spec();
    uint8_t r[REC], big[4096], moved[REC + 8];
    size_t n, off;
    struct snap_nv2_view v;

    mk(r, &s);
    /* Nothing, and nothing of the right size. */
    VERDICT_EQ(snap_nv2_gate_decide(NULL, 0, SOC_X1E, 1, 1, 1, 1), SNAP_NV2_DENY_NO_RECORD);
    VERDICT_EQ(snap_nv2_gate_decide(NULL, REC, SOC_X1E, 1, 1, 1, 1), SNAP_NV2_DENY_NO_RECORD);
    VERDICT_EQ(decide_base(r, 0), SNAP_NV2_DENY_NO_RECORD);
    VERDICT_EQ(snap_nv2_record_parse(r, REC, NULL), SNAP_NV2_DENY_NO_RECORD);
    for (n = 1; n < REC; n++)
        VERDICT_EQ(decide_base(r, n), SNAP_NV2_DENY_LENGTH);
    memset(big, 0, sizeof big);
    memcpy(big, r, REC);
    for (n = REC + 1; n <= REC + 40; n++)
        VERDICT_EQ(decide_base(big, n), SNAP_NV2_DENY_LENGTH);
    VERDICT_EQ(decide_base(big, sizeof big), SNAP_NV2_DENY_LENGTH);

    /* A parse that fails leaves nothing readable in the view. */
    memset(&v, 0xa5, sizeof v);
    VERDICT_EQ(snap_nv2_record_parse(r, 95, &v), SNAP_NV2_DENY_LENGTH);
    CHECK(v.soc == 0 && v.build_id == 0 && v.midr_el1 == 0 && v.revidr_el1 == 0 &&
          v.id_aa64mmfr2_el1 == 0 && v.id_aa64mmfr4_el1 == 0 && v.vncr_redirect == 0 &&
          v.negative_control_trapped == 0 && v.evidence == 0);
    memset(&v, 0xa5, sizeof v);
    BYTE_CASE(0x30, 7, SNAP_NV2_DENY_RESERVED);
    {
        uint8_t bad[REC];

        mk(bad, &s);
        bad[0x30] = 7;
        reseal(bad);
        VERDICT_EQ(snap_nv2_record_parse(bad, REC, &v), SNAP_NV2_DENY_RESERVED);
        CHECK(v.vncr_redirect == 0 && v.midr_el1 == 0);
    }

    /* Zero and all-ones are not records. */
    memset(r, 0, REC);
    VERDICT_EQ(decide_base(r, REC), SNAP_NV2_DENY_MAGIC);
    memset(r, 0xff, REC);
    VERDICT_EQ(decide_base(r, REC), SNAP_NV2_DENY_MAGIC);

    /* The magic, the version and the checksum. */
    mk(r, &s);
    BYTE_CASE(0x00, 0x4f, SNAP_NV2_DENY_MAGIC);
    BYTE_CASE(0x03, 0x00, SNAP_NV2_DENY_MAGIC);
    BYTE_CASE(0x04, 0, SNAP_NV2_DENY_VERSION);
    BYTE_CASE(0x04, 2, SNAP_NV2_DENY_VERSION);
    BYTE_CASE(0x05, 1, SNAP_NV2_DENY_VERSION);
    {
        uint8_t bad[REC];
        unsigned i;

        mk(bad, &s);
        bad[0x10] ^= 1;
        VERDICT_EQ(decide_base(bad, REC), SNAP_NV2_DENY_CRC);
        /* Each stored checksum byte matters, and a zeroed checksum is wrong. */
        for (i = 0x5c; i < REC; i++) {
            mk(bad, &s);
            bad[i] ^= 0x80;
            VERDICT_EQ(decide_base(bad, REC), SNAP_NV2_DENY_CRC);
        }
        mk(bad, &s);
        memset(bad + 0x5c, 0, 4);
        VERDICT_EQ(decide_base(bad, REC), SNAP_NV2_DENY_CRC);
    }

    /* Reserved bytes and the tool name. */
    BYTE_CASE(0x06, 1, SNAP_NV2_DENY_RESERVED);
    BYTE_CASE(0x07, 0x80, SNAP_NV2_DENY_RESERVED);
    BYTE_CASE(0x33, 1, SNAP_NV2_DENY_RESERVED);
    BYTE_CASE(0x34, 1, SNAP_NV2_DENY_RESERVED);
    BYTE_CASE(0x37, 0x80, SNAP_NV2_DENY_RESERVED);
    BYTE_CASE(0x48, 1, SNAP_NV2_DENY_RESERVED);
    BYTE_CASE(0x5b, 0x80, SNAP_NV2_DENY_RESERVED);
    BYTE_CASE(0x38, 0, SNAP_NV2_DENY_RESERVED);
    BYTE_CASE(0x38, ' ', SNAP_NV2_DENY_RESERVED);
    BYTE_CASE(0x39, '/', SNAP_NV2_DENY_RESERVED);
    BYTE_CASE(0x3a, 0x80, SNAP_NV2_DENY_RESERVED);
    BYTE_CASE(0x47, 'x', SNAP_NV2_DENY_RESERVED);
    BYTE_CASE(0x45, 'x', SNAP_NV2_DENY_RESERVED);
    RECORD_CASE(s_.tool = "", SNAP_NV2_DENY_RESERVED);
    {
        uint8_t full[REC];

        /* Sixteen name characters and no terminating NUL. */
        mk(full, &s);
        memset(full + 0x38, 'x', 16);
        reseal(full);
        VERDICT_EQ(decide_base(full, REC), SNAP_NV2_DENY_RESERVED);
        /* A name, a NUL, and more name. */
        mk(full, &s);
        full[0x38 + 3] = 0;
        reseal(full);
        VERDICT_EQ(decide_base(full, REC), SNAP_NV2_DENY_RESERVED);
    }

    /* The loads are bytewise: where the record sits in memory changes nothing. */
    for (off = 0; off < 8; off++) {
        mk(r, &s);
        memcpy(moved + off, r, REC);
        VERDICT_EQ(decide_base(moved + off, REC), SNAP_NV2_ALLOW);
    }
}

/* Each field is checked before the ones after it, so a record that is wrong in
 * two ways always names the first. */
static void test_order(void)
{
    struct spec s = base_spec();
    struct live l = base_live();
    uint8_t r[REC];

    mk(r, &s);
    r[0x10] ^= 1;
    VERDICT_EQ(decide_live(r, REC, SOC_X1E, &l), SNAP_NV2_DENY_CRC);

    RECORD_CASE((s_.soc = SNAP_NV2_SOC_X2E_96_100, s_.midr ^= 1), SNAP_NV2_DENY_SOC);
    RECORD_CASE((s_.midr ^= 1, s_.revidr ^= 1), SNAP_NV2_DENY_MIDR);
    RECORD_CASE((s_.revidr ^= 1, s_.mmfr2 ^= 1), SNAP_NV2_DENY_REVIDR);
    RECORD_CASE((s_.mmfr2 ^= 1, s_.vncr = SNAP_NV2_NOT_RUN), SNAP_NV2_DENY_ID_MISMATCH);
    RECORD_CASE((s_.vncr = SNAP_NV2_FAIL, s_.negctl = SNAP_NV2_FAIL), SNAP_NV2_DENY_VNCR_FAIL);
    RECORD_CASE((s_.vncr = SNAP_NV2_NOT_RUN, s_.negctl = SNAP_NV2_FAIL), SNAP_NV2_DENY_VNCR_NOT_RUN);
    RECORD_CASE((s_.negctl = SNAP_NV2_NOT_RUN, s_.evidence = SNAP_NV2_EVIDENCE_INFERRED),
                SNAP_NV2_DENY_NEGCTL_NOT_RUN);
    LIVE_CASE((l_.revidr ^= 1, l_.mmfr2 &= ~(UINT64_C(0xf) << 24)), SNAP_NV2_DENY_REVIDR);
    LIVE_CASE(l_.mmfr2 &= ~(UINT64_C(0xf) << 24), SNAP_NV2_DENY_NOT_ADVERTISED);
}

/* A single changed bit anywhere must never turn into permission it did not
 * have, and the verdict must be the one the field's meaning gives. */
static void test_record_bit_flips(void)
{
    struct spec s = base_spec();
    uint8_t base[REC], r[REC];
    unsigned i, b;

    mk(base, &s);
    for (i = 0; i < REC; i++)
        for (b = 0; b < 8; b++) {
            enum snap_nv2_verdict want;

            memcpy(r, base, REC);
            r[i] ^= (uint8_t)(1u << b);
            if (i < 4)
                want = SNAP_NV2_DENY_MAGIC;
            else if (i < 6)
                want = SNAP_NV2_DENY_VERSION;
            else
                want = SNAP_NV2_DENY_CRC;
            verdict_eq(decide_base(r, REC), want, __LINE__);

            if (i >= 0x5c)
                continue;
            reseal(r);
            if (i < 4)
                want = SNAP_NV2_DENY_MAGIC;
            else if (i < 6)
                want = SNAP_NV2_DENY_VERSION;
            else if (i < 8)
                want = SNAP_NV2_DENY_RESERVED;
            else if (i < 0x0c)
                want = (i == 8 && b == 0) ? SNAP_NV2_DENY_SOC : SNAP_NV2_DENY_RESERVED;
            else if (i < 0x10)
                want = SNAP_NV2_ALLOW;
            else if (i < 0x18)
                want = SNAP_NV2_DENY_MIDR;
            else if (i < 0x20)
                want = SNAP_NV2_DENY_REVIDR;
            else if (i < 0x30)
                want = SNAP_NV2_DENY_ID_MISMATCH;
            else if (i == 0x30 || i == 0x31)
                want = (1u << b) == 1 ? (i == 0x30 ? SNAP_NV2_DENY_VNCR_NOT_RUN
                                                    : SNAP_NV2_DENY_NEGCTL_NOT_RUN)
                                      : SNAP_NV2_DENY_RESERVED;
            else if (i == 0x32)
                want = (1u << b) <= 4 ? SNAP_NV2_DENY_EVIDENCE : SNAP_NV2_DENY_RESERVED;
            else if (i < 0x38)
                want = SNAP_NV2_DENY_RESERVED;
            else if (i < 0x48)
                want = tool_valid_ref(r + 0x38) ? SNAP_NV2_ALLOW : SNAP_NV2_DENY_RESERVED;
            else
                want = SNAP_NV2_DENY_RESERVED;
            verdict_eq(decide_base(r, REC), want, __LINE__);
        }
}

static void test_live_bit_flips(void)
{
    struct spec s = base_spec();
    struct live base = base_live();
    uint8_t r[REC];
    unsigned reg, b;

    mk(r, &s);
    for (reg = 0; reg < 4; reg++)
        for (b = 0; b < 64; b++) {
            struct live l = base;
            uint64_t bit = UINT64_C(1) << b;
            enum snap_nv2_verdict want;
            uint64_t mmfr2, mmfr4;

            switch (reg) {
            case 0: l.midr ^= bit; break;
            case 1: l.revidr ^= bit; break;
            case 2: l.mmfr2 ^= bit; break;
            default: l.mmfr4 ^= bit; break;
            }
            mmfr2 = l.mmfr2;
            mmfr4 = l.mmfr4;
            if (reg == 0)
                want = SNAP_NV2_DENY_MIDR;
            else if (reg == 1)
                want = SNAP_NV2_DENY_REVIDR;
            else if (!(((mmfr2 >> 24) & 0xf) == 2 && ((mmfr4 >> 20) & 0xf) == 0))
                want = SNAP_NV2_DENY_NOT_ADVERTISED;
            else
                want = SNAP_NV2_DENY_ID_MISMATCH;
            verdict_eq(decide_live(r, REC, SOC_X1E, &l), want, __LINE__);
        }
}

static void test_status(void)
{
    enum snap_nv2_verdict v;

    CHECK(SNAP_NV2_STATUS_RECORD == -90 && SNAP_NV2_STATUS_MISMATCH == -91 &&
          SNAP_NV2_STATUS_NO_NV2 == -92 && SNAP_NV2_STATUS_RESULT == -93);
    CHECK(snap_nv2_verdict_status(SNAP_NV2_ALLOW) == 0);
    for (v = SNAP_NV2_DENY_NO_RECORD; v < SNAP_NV2_DENY_COUNT; v++) {
        int64_t want;

        switch (v) {
        case SNAP_NV2_DENY_SOC:
        case SNAP_NV2_DENY_MIDR:
        case SNAP_NV2_DENY_REVIDR:
        case SNAP_NV2_DENY_ID_MISMATCH:
            want = -91;
            break;
        case SNAP_NV2_DENY_NOT_ADVERTISED:
            want = -92;
            break;
        case SNAP_NV2_DENY_VNCR_NOT_RUN:
        case SNAP_NV2_DENY_VNCR_FAIL:
        case SNAP_NV2_DENY_NEGCTL_NOT_RUN:
        case SNAP_NV2_DENY_NEGCTL_FAIL:
        case SNAP_NV2_DENY_EVIDENCE:
            want = -93;
            break;
        default:
            want = -90;
            break;
        }
        CHECK(snap_nv2_verdict_status(v) == want);
        CHECK(snap_nv2_verdict_status(v) != 0);
    }
    /* Parsed-only, the count, and values that are not verdicts at all are refusals. */
    CHECK(snap_nv2_verdict_status(SNAP_NV2_PARSED) == -90);
    CHECK(snap_nv2_verdict_status(SNAP_NV2_DENY_COUNT) == -90);
    CHECK(snap_nv2_verdict_status((enum snap_nv2_verdict)0xffffffffu) == -90);
    CHECK(snap_nv2_verdict_status((enum snap_nv2_verdict)0x4e32414du) == -90);
}

static void test_embedded(void)
{
    size_t length = 12345;
    const uint8_t *record = snap_nv2_embedded_record(&length);

#ifdef SNAP_NV2_RECORD_HEADER
    struct snap_nv2_view view;

    CHECK(record != NULL && length == REC);
    CHECK(snap_nv2_record_parse(record, length, &view) == SNAP_NV2_PARSED);
#else
    CHECK(record == NULL && length == 0);
    /* No record: nothing allows, whatever the registers read. */
    {
        struct live l = base_live();

        VERDICT_EQ(snap_nv2_gate_decide_embedded(SOC_X1E, l.midr, l.revidr, l.mmfr2, l.mmfr4),
                   SNAP_NV2_DENY_NO_RECORD);
        VERDICT_EQ(snap_nv2_gate_decide_embedded(SOC_X1E, 0, 0, 0, 0), SNAP_NV2_DENY_NO_RECORD);
    }
#endif
}

static void print_verdict(enum snap_nv2_verdict v)
{
    if (v == SNAP_NV2_ALLOW)
        printf("A\n");
    else if (v == SNAP_NV2_PARSED)
        printf("P\n");
    else if ((unsigned)v < (unsigned)SNAP_NV2_DENY_COUNT)
        printf("%u\n", (unsigned)v);
    else
        printf("?%x\n", (unsigned)v);
}

static int hex_digit(int c)
{
    if (c >= '0' && c <= '9')
        return c - '0';
    if (c >= 'a' && c <= 'f')
        return c - 'a' + 10;
    if (c >= 'A' && c <= 'F')
        return c - 'A' + 10;
    return -1;
}

static size_t parse_hex(const char *s, uint8_t *out, size_t max)
{
    size_t n = 0;

    if (s[0] == '.' && s[1] == 0)
        return 0;
    while (s[0] && s[1] && n < max) {
        int hi = hex_digit(s[0]), lo = hex_digit(s[1]);

        if (hi < 0 || lo < 0) {
            fprintf(stderr, "bad hex in the input\n");
            exit(2);
        }
        out[n++] = (uint8_t)(hi << 4 | lo);
        s += 2;
    }
    if (s[0]) {
        fprintf(stderr, "odd or oversized hex in the input\n");
        exit(2);
    }
    return n;
}

static char line_buffer[1 << 15];
static uint8_t record_buffer[1 << 14];

static int batch(const char *mode)
{
    while (fgets(line_buffer, sizeof line_buffer, stdin)) {
        char hex[1 << 15];
        unsigned long long soc, midr, revidr, mmfr2, mmfr4;
        size_t n;

        line_buffer[strcspn(line_buffer, "\r\n")] = 0;
        if (!strcmp(mode, "status")) {
            if (sscanf(line_buffer, "%llx", &soc) != 1)
                return 2;
            printf("%lld\n", (long long)snap_nv2_verdict_status((enum snap_nv2_verdict)(uint32_t)soc));
        } else if (!strcmp(mode, "parse")) {
            struct snap_nv2_view v;
            enum snap_nv2_verdict verdict;

            n = parse_hex(line_buffer, record_buffer, sizeof record_buffer);
            verdict = snap_nv2_record_parse(record_buffer, n, &v);
            if (verdict != SNAP_NV2_PARSED) {
                print_verdict(verdict);
                continue;
            }
            printf("P %x %x %llx %llx %llx %llx %x %x %x\n", (unsigned)v.soc, (unsigned)v.build_id,
                   (unsigned long long)v.midr_el1, (unsigned long long)v.revidr_el1,
                   (unsigned long long)v.id_aa64mmfr2_el1, (unsigned long long)v.id_aa64mmfr4_el1,
                   v.vncr_redirect, v.negative_control_trapped, v.evidence);
        } else if (!strcmp(mode, "decide")) {
            if (sscanf(line_buffer, "%32767s %llx %llx %llx %llx %llx", hex, &soc, &midr, &revidr,
                       &mmfr2, &mmfr4) != 6)
                return 2;
            n = parse_hex(hex, record_buffer, sizeof record_buffer);
            print_verdict(snap_nv2_gate_decide(record_buffer, n, (uint32_t)soc, midr, revidr, mmfr2,
                                               mmfr4));
        } else {
            return 2;
        }
    }
    return 0;
}

int main(int argc, char **argv)
{
    crc_init();
    if (argc == 6 && !strcmp(argv[1], "embedded")) {
        size_t length;
        const uint8_t *record = snap_nv2_embedded_record(&length);

        printf("%s %zu\n", record ? "record" : "none", length);
        print_verdict(snap_nv2_gate_decide_embedded(SOC_X1E, strtoull(argv[2], 0, 0),
                                                    strtoull(argv[3], 0, 0),
                                                    strtoull(argv[4], 0, 0),
                                                    strtoull(argv[5], 0, 0)));
        return 0;
    }
    if (argc == 2)
        return batch(argv[1]);
    if (argc != 1) {
        fprintf(stderr, "usage: see the head of this file\n");
        return 2;
    }
    test_enums_and_constants();
    test_crc();
    test_advertise();
    test_allow();
    test_named_denials();
    test_damage();
    test_order();
    test_record_bit_flips();
    test_live_bit_flips();
    test_status();
    test_embedded();
    puts("PASS");
    return 0;
}
