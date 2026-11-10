/* SPDX-License-Identifier: MIT */
/* Host test for compat/oryon-idregs.c, the SP11 pointer-authentication
 * ID-register normalizer. tools/test-oryon-idregs.py builds it (address and
 * undefined-behaviour sanitizers), runs the self-test, and drives the batch
 * modes against an independent model written in Python.
 *
 * The reference model below shares nothing with the code under test: the
 * recorded MIDRs are literal numbers, fields are taken with division and
 * remainder instead of shifts and masks, and expected outputs are computed by
 * arithmetic on the field values. A wrong shift or mask in the unit therefore
 * cannot cancel against a copy of itself.
 *
 *   test-oryon-idregs                      self-test; prints PASS
 *   test-oryon-idregs normalize            stdin: "<midr> <isar1> <isar2>" (hex)
 *                                          stdout: "<N|P|refusal> <out1> <out2>"
 *   test-oryon-idregs view                 stdin: "<crm> <op2> <raw> <midr> <isar1> <isar2>" (hex)
 *                                          stdout: the guest's value (hex)
 *   test-oryon-idregs status               stdin: a verdict number (hex) per line
 *
 * Every register value here is made up. None is a measurement of any machine. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "oryon-idregs.h"

#define CHECK(x) \
    do { \
        if (!(x)) { \
            fprintf(stderr, "%s:%d CHECK(%s) failed\n", __FILE__, __LINE__, #x); \
            exit(1); \
        } \
    } while (0)

_Static_assert(SNAP_ORYON_REFUSE_MIDR == 0 && SNAP_ORYON_REFUSE_IMPDEF == 1 &&
               SNAP_ORYON_REFUSE_FAMILY == 2 && SNAP_ORYON_REFUSE_NO_PAUTH == 3 &&
               SNAP_ORYON_REFUSE_ADDRESS == 4 && SNAP_ORYON_REFUSE_GENERIC == 5 &&
               SNAP_ORYON_REFUSE_COUNT == 6, "a zeroed verdict must be the first refusal");
_Static_assert(SNAP_ORYON_NORMALIZED == 0x4f524e4d && SNAP_ORYON_PASSTHROUGH == 0x4f525054,
               "the two successes are distinctive values");
_Static_assert(SNAP_ORYON_STATUS_MIDR == -94 && SNAP_ORYON_STATUS_PAUTH == -95,
               "prepare status band");
_Static_assert(SNAP_ORYON_ID_MIDR_V1_VARIANT1 == 0x511f0011u &&
               SNAP_ORYON_ID_MIDR_V1_VARIANT2 == 0x512f0011u &&
               SNAP_ORYON_ID_MIDR_A16_J700 == 0x512f0021u, "recorded MIDR values");

/* ---- the independent reference model ---------------------------------------------------- */

#define REF_NORMALIZED 100
#define REF_PASSTHROUGH 101

static unsigned nibble(uint64_t value, unsigned bit)
{
    return (unsigned)((value / (UINT64_C(1) << bit)) % 16);
}

/* 1 = Oryon v1 shape (QARMA5 in ISAR1), 2 = the A16 shape (QARMA3 in ISAR2), 0 = not recorded. */
static int ref_shape(uint64_t midr)
{
    if (midr == UINT64_C(0x511f0011) || midr == UINT64_C(0x512f0011))
        return 1;
    if (midr == UINT64_C(0x512f0021))
        return 2;
    return 0;
}

static int ref_verdict(uint64_t midr, uint64_t isar1, uint64_t isar2)
{
    int shape = ref_shape(midr);
    unsigned apa = nibble(isar1, 4), api = nibble(isar1, 8);
    unsigned gpa = nibble(isar1, 24), gpi = nibble(isar1, 28);
    unsigned gpa3 = nibble(isar2, 8), apa3 = nibble(isar2, 12);
    unsigned address, generic;

    if (shape == 0)
        return 0;
    if (api != 0 || gpi != 0)
        return 1;
    if (shape == 1 ? (apa3 != 0 || gpa3 != 0) : (apa != 0 || gpa != 0))
        return 2;
    address = shape == 1 ? apa : apa3;
    generic = shape == 1 ? gpa : gpa3;
    if (address == 0 && generic == 0)
        return 3;
    if (address != 5)
        return 4;
    if (generic != 1)
        return 5;
    return shape == 1 ? REF_NORMALIZED : REF_PASSTHROUGH;
}

static int as_ref(enum snap_oryon_verdict verdict)
{
    if (verdict == SNAP_ORYON_NORMALIZED)
        return REF_NORMALIZED;
    if (verdict == SNAP_ORYON_PASSTHROUGH)
        return REF_PASSTHROUGH;
    return (int)verdict;
}

static uint64_t xorshift(uint64_t *state)
{
    uint64_t x = *state;

    x ^= x << 13;
    x ^= x >> 7;
    x ^= x << 17;
    return *state = x;
}

/* The two registers with every PAuth nibble set to `f` and the rest to `other`. */
static void compose(uint64_t other1, uint64_t other2, unsigned apa, unsigned api, unsigned gpa,
                    unsigned gpi, unsigned gpa3, unsigned apa3, uint64_t *isar1, uint64_t *isar2)
{
    const uint64_t mask1 = (UINT64_C(0xf) << 4) | (UINT64_C(0xf) << 8) | (UINT64_C(0xf) << 24) |
                           (UINT64_C(0xf) << 28);
    const uint64_t mask2 = (UINT64_C(0xf) << 8) | (UINT64_C(0xf) << 12);

    *isar1 = (other1 & ~mask1) | ((uint64_t)apa << 4) | ((uint64_t)api << 8) |
             ((uint64_t)gpa << 24) | ((uint64_t)gpi << 28);
    *isar2 = (other2 & ~mask2) | ((uint64_t)gpa3 << 8) | ((uint64_t)apa3 << 12);
}

static const uint64_t midrs[] = {
    0x511f0011, 0x512f0011, 0x512f0021,
    /* Not recorded: zero, a neighbouring revision of each recorded part, a
     * made-up value, one with a bit above 31 set (a recorded MIDR plus a bit
     * the register cannot have), and all ones. */
    0, 0x511f0012, 0x512f0022, 0x00000000feed0001ull, 0x1511f0011ull, 0x1512f0021ull,
    0x8000000512f0021ull, UINT64_C(0xffffffffffffffff), 0xffffffff,
};

/* ---- tests ------------------------------------------------------------------------------ */

static void test_golden_vectors(void)
{
    uint64_t out1, out2;

    /* The Oryon shape: APA 5, GPA 1 in ISAR1, APA3 = GPA3 = 0. The values in the
     * other nibbles are made up so that a stray clear shows. */
    for (unsigned i = 0; i < 2; i++) {
        out1 = out2 = 0xdead;
        CHECK(snap_oryon_normalize_pauth(i ? 0x512f0011 : 0x511f0011, UINT64_C(0xfedcba980100f050),
                                         UINT64_C(0x0abc00f1), &out1, &out2) ==
              SNAP_ORYON_NORMALIZED);
        CHECK(out1 == UINT64_C(0xfedcba980000f000));
        CHECK(out2 == UINT64_C(0x0abc51f1));
    }

    /* The J700 shape passes through untouched. */
    out1 = out2 = 0xdead;
    CHECK(snap_oryon_normalize_pauth(0x512f0021, UINT64_C(0x7654321000f0f00f),
                                     UINT64_C(0x0abc51f1), &out1, &out2) == SNAP_ORYON_PASSTHROUGH);
    CHECK(out1 == UINT64_C(0x7654321000f0f00f) && out2 == UINT64_C(0x0abc51f1));

    /* What the Oryon normalizes to is what the A16 profile accepts, unchanged. */
    CHECK(snap_oryon_normalize_pauth(0x512f0021, UINT64_C(0xfedcba980000f000),
                                     UINT64_C(0x0abc51f1), &out1, &out2) == SNAP_ORYON_PASSTHROUGH);
    CHECK(out1 == UINT64_C(0xfedcba980000f000) && out2 == UINT64_C(0x0abc51f1));
}

static void test_each_refusal_reason_once(void)
{
    static const struct {
        uint64_t midr, isar1, isar2;
        enum snap_oryon_verdict want;
    } cases[] = {
        { 0x00000000, 0x01000050, 0x0, SNAP_ORYON_REFUSE_MIDR },
        { 0x511f0011, 0x01000150, 0x0, SNAP_ORYON_REFUSE_IMPDEF },   /* API 1 */
        { 0x511f0011, 0x11000050, 0x0, SNAP_ORYON_REFUSE_IMPDEF },   /* GPI 1 */
        { 0x512f0021, 0x00000100, 0x5100, SNAP_ORYON_REFUSE_IMPDEF },
        { 0x511f0011, 0x01000050, 0x1000, SNAP_ORYON_REFUSE_FAMILY },   /* APA3 and APA */
        { 0x511f0011, 0x01000050, 0x0100, SNAP_ORYON_REFUSE_FAMILY },   /* GPA3 and GPA */
        { 0x512f0021, 0x00000050, 0x5100, SNAP_ORYON_REFUSE_FAMILY },   /* APA on the A16 */
        { 0x512f0021, 0x01000000, 0x5100, SNAP_ORYON_REFUSE_FAMILY },   /* GPA on the A16 */
        { 0x511f0011, 0x00000000, 0x0, SNAP_ORYON_REFUSE_NO_PAUTH },
        { 0x512f0021, 0x00000000, 0x0, SNAP_ORYON_REFUSE_NO_PAUTH },
        { 0x511f0011, 0x01000040, 0x0, SNAP_ORYON_REFUSE_ADDRESS },     /* APA 4 */
        { 0x511f0011, 0x01000060, 0x0, SNAP_ORYON_REFUSE_ADDRESS },
        { 0x511f0011, 0x01000000, 0x0, SNAP_ORYON_REFUSE_ADDRESS },     /* generic only */
        { 0x512f0021, 0x00000000, 0x4100, SNAP_ORYON_REFUSE_ADDRESS },
        { 0x511f0011, 0x00000050, 0x0, SNAP_ORYON_REFUSE_GENERIC },     /* address only */
        { 0x511f0011, 0x02000050, 0x0, SNAP_ORYON_REFUSE_GENERIC },     /* GPA 2 */
        { 0x512f0021, 0x00000000, 0x5200, SNAP_ORYON_REFUSE_GENERIC },
    };

    for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {
        uint64_t out1 = 0x1111, out2 = 0x2222;

        CHECK(snap_oryon_normalize_pauth(cases[i].midr, cases[i].isar1, cases[i].isar2, &out1,
                                         &out2) == cases[i].want);
        CHECK(out1 == 0 && out2 == 0);
        CHECK(snap_oryon_verdict_status(cases[i].want) ==
              (cases[i].want == SNAP_ORYON_REFUSE_MIDR ? -94 : -95));
    }
}

static void test_unrecorded_midrs_refuse_a_perfect_shape(void)
{
    uint64_t out1 = 1, out2 = 1;

    for (size_t i = 3; i < sizeof(midrs) / sizeof(midrs[0]); i++) {
        CHECK(snap_oryon_normalize_pauth(midrs[i], UINT64_C(0x01000050), 0, &out1, &out2) ==
              SNAP_ORYON_REFUSE_MIDR);
        CHECK(out1 == 0 && out2 == 0);
        CHECK(snap_oryon_normalize_pauth(midrs[i], 0, UINT64_C(0x5100), &out1, &out2) ==
              SNAP_ORYON_REFUSE_MIDR);
        CHECK(out1 == 0 && out2 == 0);
    }
    /* a recorded MIDR with the other family's shape is refused as a family mix, not accepted */
    CHECK(snap_oryon_normalize_pauth(0x511f0011, 0, UINT64_C(0x5100), &out1, &out2) ==
          SNAP_ORYON_REFUSE_FAMILY);
    CHECK(snap_oryon_normalize_pauth(0x512f0021, UINT64_C(0x01000050), 0, &out1, &out2) ==
          SNAP_ORYON_REFUSE_FAMILY);
}

static void test_every_pauth_nibble_combination(void)
{
    static const unsigned impdef[] = { 0, 1, 15 };
    static const uint64_t others[][2] = {
        { 0, 0 },
        { UINT64_C(0xffffffffffffffff), UINT64_C(0xffffffffffffffff) },
        { UINT64_C(0x5a5aa5a53c3cc3c3), UINT64_C(0xc3c33c3ca5a55a5a) },
    };
    uint64_t rng = UINT64_C(0x9e3779b97f4a7c15);
    unsigned long checked = 0, normalized = 0, passed = 0;

    for (unsigned all = 0; all < 0x10000; all++) {
        unsigned apa = all & 15, gpa = (all >> 4) & 15, apa3 = (all >> 8) & 15, gpa3 = (all >> 12) & 15;

        for (size_t a = 0; a < sizeof(impdef) / sizeof(impdef[0]); a++)
        for (size_t b = 0; b < sizeof(impdef) / sizeof(impdef[0]); b++)
        for (size_t o = 0; o < sizeof(others) / sizeof(others[0]); o++) {
            uint64_t other1 = others[o][0], other2 = others[o][1];
            uint64_t isar1, isar2;

            if (o == 2) {
                other1 ^= xorshift(&rng);
                other2 ^= xorshift(&rng);
            }
            compose(other1, other2, apa, impdef[a], gpa, impdef[b], gpa3, apa3, &isar1, &isar2);

            for (size_t m = 0; m < sizeof(midrs) / sizeof(midrs[0]); m++) {
                uint64_t out1 = 0x77, out2 = 0x88;
                int want = ref_verdict(midrs[m], isar1, isar2);
                enum snap_oryon_verdict got =
                    snap_oryon_normalize_pauth(midrs[m], isar1, isar2, &out1, &out2);

                checked++;
                if (as_ref(got) != want) {
                    fprintf(stderr, "midr %#llx isar1 %#llx isar2 %#llx: verdict %d, expected %d\n",
                            (unsigned long long)midrs[m], (unsigned long long)isar1,
                            (unsigned long long)isar2, as_ref(got), want);
                    exit(1);
                }
                if (want == REF_NORMALIZED) {
                    /* Arithmetic, not the unit's masks: APA 5 and GPA 1 come out of ISAR1
                     * and APA3 5 and GPA3 1 go into the (zero) APA3/GPA3 nibbles. */
                    CHECK(out1 == isar1 - UINT64_C(5) * 16 - UINT64_C(1) * (UINT64_C(1) << 24));
                    CHECK(out2 == isar2 + UINT64_C(5) * 4096 + UINT64_C(1) * 256);
                    CHECK(ref_verdict(0x512f0021, out1, out2) == REF_PASSTHROUGH);
                    normalized++;
                } else if (want == REF_PASSTHROUGH) {
                    CHECK(out1 == isar1 && out2 == isar2);
                    passed++;
                } else {
                    CHECK(out1 == 0 && out2 == 0);
                }
                CHECK(snap_oryon_normalize_pauth(midrs[m], isar1, isar2, NULL, NULL) == got);
            }
        }
    }
    /* One nibble combination, one API/GPI pair and three other-bit patterns succeed
     * per MIDR: two Oryon MIDRs normalize, the one A16 MIDR passes through. */
    CHECK(normalized == 2UL * 3);
    CHECK(passed == 1UL * 3);
    fprintf(stderr, "nibble sweep: %lu cases, %lu normalized, %lu passed through\n", checked,
            normalized, passed);
}

static void test_outputs_may_be_null(void)
{
    uint64_t out = 0x55;

    CHECK(snap_oryon_normalize_pauth(0x511f0011, 0x01000050, 0, NULL, NULL) == SNAP_ORYON_NORMALIZED);
    CHECK(snap_oryon_normalize_pauth(0x511f0011, 0x01000050, 0, &out, NULL) == SNAP_ORYON_NORMALIZED);
    CHECK(out == 0);
    out = 0x55;
    CHECK(snap_oryon_normalize_pauth(0x511f0011, 0x01000050, 0, NULL, &out) == SNAP_ORYON_NORMALIZED);
    CHECK(out == UINT64_C(0x5100));
    out = 0x55;
    CHECK(snap_oryon_normalize_pauth(1, 0, 0, NULL, &out) == SNAP_ORYON_REFUSE_MIDR);
    CHECK(out == 0);
}

static void test_the_view_changes_only_the_two_pauth_registers(void)
{
    static const struct {
        uint64_t midr, isar1, isar2;
    } units[] = {
        { 0x511f0011, UINT64_C(0xfedcba980100f050), UINT64_C(0x0abc00f1) },
        { 0x512f0021, UINT64_C(0x7654321000f0f00f), UINT64_C(0x0abc51f1) },
        { 0x511f0011, UINT64_C(0x00000000), UINT64_C(0x0) },
        { 0xdead0000, UINT64_C(0x01000050), UINT64_C(0x0) },
    };
    static const uint64_t raws[] = { 0, 1, UINT64_C(0xffffffffffffffff), UINT64_C(0x0123456789abcdef) };
    static const uint32_t selectors[] = { 0, 1, 5, 6, 7, 8, 9, 0x10, 0xffffffffu };

    for (size_t u = 0; u < sizeof(units) / sizeof(units[0]); u++) {
        uint64_t out1, out2;

        (void)snap_oryon_normalize_pauth(units[u].midr, units[u].isar1, units[u].isar2, &out1, &out2);
        for (size_t c = 0; c < sizeof(selectors) / sizeof(selectors[0]); c++)
        for (size_t p = 0; p < sizeof(selectors) / sizeof(selectors[0]); p++)
        for (size_t r = 0; r < sizeof(raws) / sizeof(raws[0]); r++) {
            uint32_t crm = selectors[c], op2 = selectors[p];
            uint64_t got = snap_oryon_id_view(crm, op2, raws[r], units[u].midr, units[u].isar1,
                                              units[u].isar2);
            uint64_t want;

            if (crm > 7 || op2 > 7)
                want = 0;
            else if (crm == 6 && op2 == 1)
                want = out1;
            else if (crm == 6 && op2 == 2)
                want = out2;
            else
                want = raws[r];
            CHECK(got == want);
        }
    }
    /* Every in-range encoding other than ISAR1/ISAR2 comes back as read, including the RAZ ones. */
    for (uint32_t crm = 0; crm < 8; crm++)
        for (uint32_t op2 = 0; op2 < 8; op2++) {
            if (crm == 6 && (op2 == 1 || op2 == 2))
                continue;
            CHECK(snap_oryon_id_view(crm, op2, 0, 0x511f0011, 0x01000050, 0) == 0);
            CHECK(snap_oryon_id_view(crm, op2, UINT64_C(0xa5a5), 0, 0, 0) == UINT64_C(0xa5a5));
        }
    /* ISAR1 and ISAR2 are answered from the registers handed in, not from `raw`. */
    CHECK(snap_oryon_id_view(6, 1, UINT64_C(0x1234), 0x511f0011, UINT64_C(0x01000050), 0) == 0);
    CHECK(snap_oryon_id_view(6, 2, UINT64_C(0x1234), 0x511f0011, UINT64_C(0x01000050), 0) ==
          UINT64_C(0x5100));
    /* A refused CPU reads zero in both, so a guest cannot see half a profile. */
    CHECK(snap_oryon_id_view(6, 1, UINT64_C(0x01000050), 1, UINT64_C(0x01000050), 0) == 0);
    CHECK(snap_oryon_id_view(6, 2, UINT64_C(0x5100), 1, 0, UINT64_C(0x5100)) == 0);
}

static void test_status(void)
{
    CHECK(snap_oryon_verdict_status(SNAP_ORYON_NORMALIZED) == 0);
    CHECK(snap_oryon_verdict_status(SNAP_ORYON_PASSTHROUGH) == 0);
    CHECK(snap_oryon_verdict_status(SNAP_ORYON_REFUSE_MIDR) == -94);
    for (int v = SNAP_ORYON_REFUSE_IMPDEF; v <= SNAP_ORYON_REFUSE_COUNT; v++)
        CHECK(snap_oryon_verdict_status((enum snap_oryon_verdict)v) == -95);
    for (uint32_t v = 7; v < 300; v++)
        CHECK(snap_oryon_verdict_status((enum snap_oryon_verdict)v) == -95);
    CHECK(snap_oryon_verdict_status((enum snap_oryon_verdict)0xffffffffu) == -95);
    CHECK(snap_oryon_verdict_status((enum snap_oryon_verdict)(SNAP_ORYON_NORMALIZED + 1)) == -95);
    CHECK(snap_oryon_verdict_status((enum snap_oryon_verdict)(SNAP_ORYON_PASSTHROUGH - 1)) == -95);
    CHECK(snap_oryon_verdict_status((enum snap_oryon_verdict)0x4f524e4c) == -95);
}

static uint64_t parse_hex(const char *text)
{
    return strtoull(text, NULL, 16);
}

static int batch(const char *mode)
{
    char line[512];

    while (fgets(line, sizeof line, stdin)) {
        char *words[8] = { 0 };
        size_t count = 0;

        for (char *w = strtok(line, " \t\r\n"); w && count < 8; w = strtok(NULL, " \t\r\n"))
            words[count++] = w;
        if (!strcmp(mode, "normalize") && count == 3) {
            uint64_t out1 = 0, out2 = 0;
            enum snap_oryon_verdict v = snap_oryon_normalize_pauth(
                parse_hex(words[0]), parse_hex(words[1]), parse_hex(words[2]), &out1, &out2);

            if (v == SNAP_ORYON_NORMALIZED)
                printf("N %llx %llx\n", (unsigned long long)out1, (unsigned long long)out2);
            else if (v == SNAP_ORYON_PASSTHROUGH)
                printf("P %llx %llx\n", (unsigned long long)out1, (unsigned long long)out2);
            else
                printf("%u %llx %llx\n", (unsigned)v, (unsigned long long)out1,
                       (unsigned long long)out2);
        } else if (!strcmp(mode, "view") && count == 6) {
            printf("%llx\n", (unsigned long long)snap_oryon_id_view(
                                 (uint32_t)parse_hex(words[0]), (uint32_t)parse_hex(words[1]),
                                 parse_hex(words[2]), parse_hex(words[3]), parse_hex(words[4]),
                                 parse_hex(words[5])));
        } else if (!strcmp(mode, "status") && count == 1) {
            printf("%lld\n", (long long)snap_oryon_verdict_status(
                                 (enum snap_oryon_verdict)parse_hex(words[0])));
        } else {
            fprintf(stderr, "bad line for mode %s\n", mode);
            return 2;
        }
    }
    return 0;
}

int main(int argc, char **argv)
{
    if (argc == 2)
        return batch(argv[1]);
    if (argc != 1) {
        fprintf(stderr, "usage: %s [normalize|view|status]\n", argv[0]);
        return 2;
    }
    test_golden_vectors();
    test_each_refusal_reason_once();
    test_unrecorded_midrs_refuse_a_perfect_shape();
    test_every_pauth_nibble_combination();
    test_outputs_may_be_null();
    test_the_view_changes_only_the_two_pauth_registers();
    test_status();
    puts("PASS");
    return 0;
}
