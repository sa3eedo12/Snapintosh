/* SPDX-License-Identifier: MIT */
/* Host tests of the declared-ranges table of the X2 image's `declared` variant
 * (probes/uefi/tests/ebs_ranges.[ch]).
 *
 * The slbounce fork that reads the table refuses silently, so every refusal and every byte is
 * asserted here, against inputs written independently of the code under test: the expected table
 * is assembled field by field with explicit little-endian stores, the checksum is checked against
 * constants computed outside this tree (FNV-1a test vectors and tables built in Python), the
 * validator below restates the shared header's documented rules rather than calling anything of
 * ebs_ranges.c, and the randomized map cases are judged by a reference that uses 128-bit
 * arithmetic, so that it cannot share a wrap-around mistake with the code.
 *
 * Built with -DX_SLBOUNCE=1 (tools/test-uefi-tests.py does so only when SLBOUNCE_SP11_DIR names the
 * fork) the same file also includes the fork's own header and sweep, and requires its validator
 * and its range rule to agree with this image's. Without it that section does not exist: an
 * absent fork is a SKIP of the Python test that builds it, never a pass.
 *
 * `--ebs-cases` (x_ebs_cases) reads cases from standard input and prints what the builder made of
 * each, for the differential run against tools/sp11_ebs_sweep_model.py.
 */
#include "xtests.h"

#include "ebs_ranges.h"

#ifdef X_SLBOUNCE
#include "sp11-ebs-ranges.h"
#include "sp11_scoped_sweep.h"
#endif

#define A_ALL 0xFull    /* UC | WC | WT | WB: what ordinary RAM descriptors carry */
#define A_NO_WB 0x7ull  /* UC | WC | WT */
#define PAGE 0x1000ull

typedef struct {
    uint32_t type;
    uint64_t phys;
    uint64_t pages;
    uint64_t attr;
} xdesc;

/* A raw map in a buffer of exactly its size, so a read past it is an ASan report. Descriptors are
 * written at the firmware's stride; the bytes between the 40 a descriptor needs and the stride
 * are zero. */
static uint8_t *raw_map(const xdesc *d, size_t n, size_t stride)
{
    uint8_t *m = xalloc(n * stride);

    for (size_t i = 0; i < n; i++) {
        st32(m + i * stride + 0, d[i].type);
        st64(m + i * stride + 8, d[i].phys);
        st64(m + i * stride + 16, d[i].phys);
        st64(m + i * stride + 24, d[i].pages);
        st64(m + i * stride + 32, d[i].attr);
    }
    return m;
}

/* The status for one map; the map is deliberately not passed through probe_memmap_init, so that
 * descriptors the image's own parser would have refused reach the check as well. */
static int check_map(const xdesc *d, size_t n, size_t stride, uint64_t base, uint64_t size)
{
    uint8_t *raw = raw_map(d, n, stride);
    probe_memmap m = {raw, stride, n};
    int rc = tests_ebs_check_range(&m, base, size);

    free(raw);
    return rc;
}

static int check_one(uint32_t type, uint64_t attr, uint64_t base, uint64_t size)
{
    xdesc d = {type, 0x80000000ull, 0x400, attr};

    return check_map(&d, 1, 48, base, size);
}

/* ---- The reference the table is checked against ---- */

static uint64_t ref_fnv(const uint8_t *p, size_t n)
{
    uint64_t h = 14695981039346656037ull;

    for (size_t i = 0; i < n; i++) {
        h ^= p[i];
        h *= 1099511628211ull;
    }
    return h;
}

/* The rules of sp11_ebs_ranges_valid() in the shared header, restated from its comments. */
static int ref_valid(const uint8_t *t)
{
    uint32_t count = (uint32_t)(t[12] | (t[13] << 8) | ((uint32_t)t[14] << 16) | ((uint32_t)t[15] << 24));
    uint32_t cap = (uint32_t)(t[16] | (t[17] << 8) | ((uint32_t)t[18] << 16) | ((uint32_t)t[19] << 24));
    uint64_t sum = 0;

    if (memcmp(t, "SP11EBS1", 8) != 0)
        return 0;
    if (t[8] != 1 || t[9] != 0 || t[10] != 0 || t[11] != 0)
        return 0;
    if (count < 1 || count > 8 || cap == 0)
        return 0;
    for (int i = 20; i < 24; i++)
        if (t[i] != 0)
            return 0;
    for (uint32_t i = 0; i < 8; i++) {
        uint64_t b = 0, s = 0;

        for (int k = 7; k >= 0; k--) {
            b = (b << 8) | t[24 + i * 16 + (unsigned)k];
            s = (s << 8) | t[24 + i * 16 + 8 + (unsigned)k];
        }
        if (i < count && s == 0)
            return 0;
        if (i >= count && (b != 0 || s != 0))
            return 0;
    }
    for (int k = 7; k >= 0; k--)
        sum = (sum << 8) | t[152 + (unsigned)k];
    return sum == ref_fnv(t, 152);
}

/* One field of a table overwritten and the checksum recomputed: whatever the validator says is then
 * the verdict of that field's own rule, never of the checksum. */
static int valid_after_poking(const tests_ebs_table *t, size_t offset, uint64_t value, unsigned width)
{
    uint8_t b[TESTS_EBS_TABLE_BYTES];

    memcpy(b, t, sizeof b);
    if (width == 4)
        st32(b + offset, (uint32_t)value);
    else
        st64(b + offset, value);
    st64(b + 152, ref_fnv(b, 152));
    return ref_valid(b);
}

/* The table that declares [base, base + size), assembled by hand from the documented layout. */
static void expected_table(uint8_t out[TESTS_EBS_TABLE_BYTES], uint64_t base, uint64_t size,
                           uint64_t checksum)
{
    memset(out, 0, TESTS_EBS_TABLE_BYTES);
    memcpy(out, "SP11EBS1", 8);
    st32(out + 8, 1);
    st32(out + 12, 1);
    st32(out + 16, 1u << 20);
    st32(out + 20, 0);
    st64(out + 24, base);
    st64(out + 32, size);
    st64(out + 152, checksum);
}

/* Constants computed outside this tree, from the layout in the shared header. */
static const struct {
    uint64_t base, size, checksum;
} golden[] = {
    {0x80000000ull, 0x190000ull, 0x8cf51e30a7bd2aaaull},   /* an image-sized range */
    {0x800001000ull, 0x1000ull, 0x74ce5ece6ad09543ull},    /* above 4 GiB, one page */
    {0x1000ull, 0x6c000ull, 0xb9b36529d3a37f4dull},
    {0x40000000ull, 0x10000000ull, 0xc6c769b953a5655bull}, /* 256 MiB */
};

static const uint32_t swept_types[] = {1, 2, 3, 4, 5, 6, 9};
static const uint32_t refused_types[] = {0, 7, 8, 10, 11, 12, 13, 14, 15, 16, 31, 32, 33, 0x7FFFFFFFu, 0xFFFFFFFFu};

static const struct {
    int status;
    const char *ident; /* the spelling the Python differential test expects */
} status_idents[] = {
    {TESTS_EBS_OK, "OK"},
    {TESTS_EBS_SIZE_ZERO, "SIZE_ZERO"},
    {TESTS_EBS_UNALIGNED, "UNALIGNED"},
    {TESTS_EBS_WRAPS, "WRAPS"},
    {TESTS_EBS_NO_MAP, "NO_MAP"},
    {TESTS_EBS_MAP_TOO_LARGE, "MAP_TOO_LARGE"},
    {TESTS_EBS_MAP_INVALID, "MAP_INVALID"},
    {TESTS_EBS_NOT_IN_ONE_DESCRIPTOR, "NOT_IN_ONE_DESCRIPTOR"},
    {TESTS_EBS_WRONG_TYPE, "WRONG_TYPE"},
    {TESTS_EBS_NOT_WRITE_BACK, "NOT_WRITE_BACK"},
};

static const char *status_ident(int status)
{
    for (size_t i = 0; i < sizeof status_idents / sizeof status_idents[0]; i++)
        if (status_idents[i].status == status)
            return status_idents[i].ident;
    return "UNKNOWN";
}

/* ---- Layout ---- */

void test_x_ebs_layout(void)
{
    static const struct {
        uint32_t a;
        uint16_t b, c;
        uint8_t d[8];
    } guid = TESTS_EBS_GUID_INIT;
    static const uint16_t name[] = TESTS_EBS_VARIABLE_NAME_U16;
    static const char want_name[] = "Q1N1Sp11EbsRanges";
    tests_ebs_table t;
    xdesc d = {1, 0x80000000ull, 0x190, A_ALL};
    uint8_t *raw;
    probe_memmap m;

    section("ebs table layout");
    CHECK_EQU(sizeof(tests_ebs_range), 16);
    CHECK_EQU(sizeof(tests_ebs_table), 160);
    CHECK_EQU(TESTS_EBS_TABLE_BYTES, 160);
    CHECK_EQU(offsetof(tests_ebs_table, magic), 0);
    CHECK_EQU(offsetof(tests_ebs_table, version), 8);
    CHECK_EQU(offsetof(tests_ebs_table, count), 12);
    CHECK_EQU(offsetof(tests_ebs_table, chunk_cap), 16);
    CHECK_EQU(offsetof(tests_ebs_table, reserved), 20);
    CHECK_EQU(offsetof(tests_ebs_table, range), 24);
    CHECK_EQU(offsetof(tests_ebs_table, checksum), 152);
    CHECK_EQU(TESTS_EBS_MAGIC, 0x3153424531315053ull);
    CHECK_EQU(TESTS_EBS_VERSION, 1);
    CHECK_EQU(TESTS_EBS_MAX_RANGES, 8);
    CHECK_EQU(TESTS_EBS_CHUNK_CAP, 1 << 20);
    CHECK_EQU(TESTS_EBS_MAX_DESCRIPTORS, 512);
    CHECK_EQU(TESTS_EBS_TYPES_MASK, 0x27E);
    CHECK_EQU(TESTS_EBS_ATTR_WB, 8);
    CHECK_EQU(TESTS_EBS_VARIABLE_ATTRIBUTES, 2);

    /* The variable's identity, spelled out: the fork looks it up by exactly this name and GUID. */
    CHECK_EQU(guid.a, 0x1a3c9e02);
    CHECK_EQU(guid.b, 0x7b6f);
    CHECK_EQU(guid.c, 0x4d81);
    CHECK(memcmp(guid.d, "\x9c\x2e" "SP11ER", 8) == 0);
    CHECK_EQU(sizeof name / sizeof name[0], sizeof want_name);
    for (size_t i = 0; i < sizeof want_name; i++)
        CHECK_EQU(name[i], (unsigned char)want_name[i]);

    /* The first eight bytes of a built table are the ASCII "SP11EBS1", whatever the host is. */
    raw = raw_map(&d, 1, 48);
    m = (probe_memmap){raw, 48, 1};
    CHECK_EQ(tests_ebs_build(&t, &m, 0x80000000ull, 0x190000ull), TESTS_EBS_OK);
    free(raw);
    CHECK(memcmp(&t, "SP11EBS1", 8) == 0);
    CHECK_EQU(t.version, 1);
    CHECK_EQU(t.count, 1);
    CHECK_EQU(t.chunk_cap, 0x100000);
    CHECK_EQU(t.reserved, 0);
    CHECK_EQU(t.range[0].base, 0x80000000ull);
    CHECK_EQU(t.range[0].size, 0x190000ull);
}

/* ---- Exact bytes ---- */

static void build_one(tests_ebs_table *t, uint64_t base, uint64_t size, int *rc)
{
    xdesc d = {1, base, (size + PAGE - 1) / PAGE + 1, A_ALL};
    uint8_t *raw = raw_map(&d, 1, 48);
    probe_memmap m = {raw, 48, 1};

    *rc = tests_ebs_build(t, &m, base, size);
    free(raw);
}

void test_x_ebs_bytes(void)
{
    static const struct {
        const char *text;
        uint64_t sum;
    } fnv[] = {{"", 0xcbf29ce484222325ull}, {"a", 0xaf63dc4c8601ec8cull}, {"foobar", 0x85944171f73967e8ull}};
    uint8_t want[TESTS_EBS_TABLE_BYTES], flipped[TESTS_EBS_TABLE_BYTES];
    tests_ebs_table t, again, dirty;
    int rc;

    section("ebs table bytes");
    for (size_t i = 0; i < sizeof fnv / sizeof fnv[0]; i++) {
        CHECK_EQU(tests_ebs_fnv1a(fnv[i].text, strlen(fnv[i].text)), fnv[i].sum);
        CHECK_EQU(ref_fnv((const uint8_t *)fnv[i].text, strlen(fnv[i].text)), fnv[i].sum);
    }
    CHECK_EQU(tests_ebs_fnv1a(NULL, 0), 0xcbf29ce484222325ull);

    for (size_t i = 0; i < sizeof golden / sizeof golden[0]; i++) {
        expected_table(want, golden[i].base, golden[i].size, golden[i].checksum);
        CHECK_EQU(ref_fnv(want, 152), golden[i].checksum); /* the constants are what the layout hashes to */
        CHECK(ref_valid(want));

        build_one(&t, golden[i].base, golden[i].size, &rc);
        CHECK_EQ(rc, TESTS_EBS_OK);
        CHECK(memcmp(&t, want, sizeof want) == 0);
        CHECK(ref_valid((const uint8_t *)&t));

        /* The same bytes every time, and whatever the table held before the call. */
        build_one(&again, golden[i].base, golden[i].size, &rc);
        CHECK(memcmp(&t, &again, sizeof t) == 0);
        memset(&dirty, 0xAA, sizeof dirty);
        {
            xdesc d = {1, golden[i].base, (golden[i].size + PAGE - 1) / PAGE + 1, A_ALL};
            uint8_t *raw = raw_map(&d, 1, 48);
            probe_memmap m = {raw, 48, 1};

            CHECK_EQ(tests_ebs_build(&dirty, &m, golden[i].base, golden[i].size), TESTS_EBS_OK);
            free(raw);
        }
        CHECK(memcmp(&t, &dirty, sizeof t) == 0);
        /* Slots from count on, and the reserved word, are zero. */
        for (size_t k = 40; k < 152; k++)
            CHECK_EQU(((const uint8_t *)&t)[k], 0);
        CHECK_EQU(((const uint8_t *)&t)[20], 0);
    }

    /* The checksum covers every byte before it: no single bit of the table can change without
     * the independent validator refusing the result. */
    build_one(&t, golden[0].base, golden[0].size, &rc);
    for (size_t byte = 0; byte < TESTS_EBS_TABLE_BYTES; byte++) {
        unsigned accepted = 0;

        for (unsigned bit = 0; bit < 8; bit++) {
            memcpy(flipped, &t, sizeof flipped);
            flipped[byte] ^= (uint8_t)(1u << bit);
            accepted += (unsigned)ref_valid(flipped);
        }
        CHECK_EQU(accepted, 0);
    }

    /* The validator is not vacuous: each structural rule refuses on its own, with the checksum
     * recomputed so that only that rule can be the reason, and writing back a field's own value
     * is accepted, so the helper cannot be what refuses. */
    CHECK(valid_after_poking(&t, 12, 1, 4));
    CHECK(!valid_after_poking(&t, 12, 0, 4));            /* count 0 */
    CHECK(!valid_after_poking(&t, 12, 9, 4));            /* count above the slots */
    CHECK(!valid_after_poking(&t, 16, 0, 4));            /* chunk_cap 0 */
    CHECK(!valid_after_poking(&t, 20, 1, 4));            /* reserved */
    CHECK(!valid_after_poking(&t, 8, 2, 4));             /* version */
    CHECK(!valid_after_poking(&t, 24 + 16 * 3, 1, 8));   /* a slot from count on is not zero */
    CHECK(!valid_after_poking(&t, 24 + 8, 0, 8));        /* a range of size 0 */
    memset(flipped, 0, sizeof flipped);
    CHECK(!ref_valid(flipped));

    /* A refused call leaves an all-zero table, which no validator accepts, whatever it held. */
    memset(&dirty, 0xAA, sizeof dirty);
    CHECK_EQ(tests_ebs_build(&dirty, NULL, 0x80000000ull, 0x1000), TESTS_EBS_NO_MAP);
    memset(&want, 0, sizeof want);
    CHECK(memcmp(&dirty, want, sizeof dirty) == 0);
    CHECK(!ref_valid((const uint8_t *)&dirty));
}

/* ---- The range check ---- */

static void check_statuses_and_names(void)
{
    char seen[16][40] = {{0}};
    size_t n = sizeof status_idents / sizeof status_idents[0];

    for (size_t i = 0; i < n; i++) {
        const char *name = tests_ebs_status_name(status_idents[i].status);

        CHECK(strlen(name) > 0 && strlen(name) <= 32);
        CHECK(strcmp(name, "UNKNOWN") != 0);
        /* The console grammar is "KEY=value KEY=value": a reason must not contain '=' or lower
         * case, and its words are separated by single spaces. */
        for (const char *c = name; *c != 0; c++)
            CHECK((*c >= 'A' && *c <= 'Z') || (*c >= '0' && *c <= '9') || *c == ' ' || *c == '-');
        CHECK(strstr(name, "  ") == NULL);
        for (size_t k = 0; k < i; k++)
            CHECK(strcmp(seen[k], name) != 0);
        snprintf(seen[i], sizeof seen[i], "%s", name);
    }
    CHECK_STR(tests_ebs_status_name(-1), "UNKNOWN");
    CHECK_STR(tests_ebs_status_name(99), "UNKNOWN");
}

void test_x_ebs_check(void)
{
    /* A map shaped like a firmware's: this image, a neighbour right after it, free memory, a
     * reserved region (a framebuffer's descriptor), runtime and ACPI descriptors, MMIO. */
    xdesc map[] = {
        {7, 0x40000000ull, 0x100, A_ALL},
        {1, 0x80000000ull, 0x190, 0x100F},
        {4, 0x80190000ull, 0x10, 0x100F},
        {0, 0x90000000ull, 0x1000, 0x100F},
        {5, 0xA0000000ull, 0x10, 0x800000000000100Full},
        {9, 0xA0010000ull, 0x8, 0x100F},
        {10, 0xA0020000ull, 0x8, 0x100F},
        {11, 0xB0000000ull, 0x100, 0x8000000000000001ull},
    };
    const size_t n = sizeof map / sizeof map[0];
    const size_t strides[] = {40, 48, 56, 64, 256};

    section("ebs range check");
    check_statuses_and_names();

    /* Accepted: the whole image descriptor, a part of it, one page at each end, a size that is not
     * a whole number of pages, and a descriptor of every type the fork sweeps. */
    for (size_t s = 0; s < sizeof strides / sizeof strides[0]; s++) {
        CHECK_EQ(check_map(map, n, strides[s], 0x80000000ull, 0x190000ull), TESTS_EBS_OK);
        CHECK_EQ(check_map(map, n, strides[s], 0x80001000ull, 0x2000ull), TESTS_EBS_OK);
        CHECK_EQ(check_map(map, n, strides[s], 0x80000000ull, 0x1000ull), TESTS_EBS_OK);
        CHECK_EQ(check_map(map, n, strides[s], 0x8018F000ull, 0x1000ull), TESTS_EBS_OK);
        CHECK_EQ(check_map(map, n, strides[s], 0x80000000ull, 0x18F123ull), TESTS_EBS_OK);
        CHECK_EQ(check_map(map, n, strides[s], 0x80190000ull, 0x10000ull), TESTS_EBS_OK); /* BS data */
        CHECK_EQ(check_map(map, n, strides[s], 0xA0000000ull, 0x10000ull), TESTS_EBS_OK); /* RT, attr bit 63 */
        CHECK_EQ(check_map(map, n, strides[s], 0xA0010000ull, 0x8000ull), TESTS_EBS_OK);  /* ACPI reclaim */
    }
    for (size_t i = 0; i < sizeof swept_types / sizeof swept_types[0]; i++)
        CHECK_EQ(check_one(swept_types[i], A_ALL, 0x80000000ull, 0x190000ull), TESTS_EBS_OK);

    /* Types the fork never sweeps are refused even when the descriptor is perfect otherwise. */
    for (size_t i = 0; i < sizeof refused_types / sizeof refused_types[0]; i++)
        CHECK_EQ(check_one(refused_types[i], A_ALL, 0x80000000ull, 0x190000ull), TESTS_EBS_WRONG_TYPE);
    CHECK_EQ(check_map(map, n, 48, 0x90000000ull, 0x1000), TESTS_EBS_WRONG_TYPE); /* reserved, as a framebuffer */
    CHECK_EQ(check_map(map, n, 48, 0x40000000ull, 0x1000), TESTS_EBS_WRONG_TYPE); /* conventional */
    CHECK_EQ(check_map(map, n, 48, 0xA0020000ull, 0x1000), TESTS_EBS_WRONG_TYPE); /* ACPI NVS */
    CHECK_EQ(check_map(map, n, 48, 0xB0000000ull, 0x1000), TESTS_EBS_WRONG_TYPE); /* MMIO */

    /* Write-back: bit 3 and nothing else decides. */
    CHECK_EQ(check_one(1, 0, 0x80000000ull, 0x1000), TESTS_EBS_NOT_WRITE_BACK);
    CHECK_EQ(check_one(1, A_NO_WB, 0x80000000ull, 0x1000), TESTS_EBS_NOT_WRITE_BACK);
    CHECK_EQ(check_one(1, 0x1000ull, 0x80000000ull, 0x1000), TESTS_EBS_NOT_WRITE_BACK);
    CHECK_EQ(check_one(1, ~0x8ull, 0x80000000ull, 0x1000), TESTS_EBS_NOT_WRITE_BACK);
    CHECK_EQ(check_one(1, 0x8ull, 0x80000000ull, 0x1000), TESTS_EBS_OK);
    CHECK_EQ(check_one(1, ~0ull, 0x80000000ull, 0x1000), TESTS_EBS_OK);

    /* Containment: the range must lie wholly in one descriptor. A page before it, a page past
     * it, a range spanning two adjacent descriptors of the same type, a hole and an address in no
     * descriptor at all are each refused. */
    CHECK_EQ(check_map(map, n, 48, 0x7FFFF000ull, 0x2000), TESTS_EBS_NOT_IN_ONE_DESCRIPTOR);
    CHECK_EQ(check_map(map, n, 48, 0x80000000ull, 0x191000ull), TESTS_EBS_NOT_IN_ONE_DESCRIPTOR);
    CHECK_EQ(check_map(map, n, 48, 0x8018F000ull, 0x2000), TESTS_EBS_NOT_IN_ONE_DESCRIPTOR); /* image + neighbour */
    CHECK_EQ(check_map(map, n, 48, 0x60000000ull, 0x1000), TESTS_EBS_NOT_IN_ONE_DESCRIPTOR); /* in no descriptor */
    CHECK_EQ(check_map(map, n, 48, 0x80000000ull, 0x1ull << 40), TESTS_EBS_NOT_IN_ONE_DESCRIPTOR);
    {
        xdesc same_type[] = {{2, 0x1000, 1, A_ALL}, {2, 0x2000, 1, A_ALL}};

        CHECK_EQ(check_map(same_type, 2, 48, 0x1000, 0x1000), TESTS_EBS_OK);
        CHECK_EQ(check_map(same_type, 2, 48, 0x1000, 0x2000), TESTS_EBS_NOT_IN_ONE_DESCRIPTOR);
    }
    {
        /* Overlap: two descriptors that both contain the range, each acceptable alone, are refused;
         * one that only overlaps it, without containing it, is not counted. */
        xdesc both[] = {{2, 0x10000, 0x10, A_ALL}, {4, 0x10000, 0x10, A_ALL}};
        xdesc partial[] = {{2, 0x10000, 0x10, A_ALL}, {4, 0x18000, 0x10, A_ALL}};

        CHECK_EQ(check_map(both, 2, 48, 0x11000, 0x1000), TESTS_EBS_NOT_IN_ONE_DESCRIPTOR);
        CHECK_EQ(check_map(partial, 2, 48, 0x11000, 0x1000), TESTS_EBS_OK);
        CHECK_EQ(check_map(partial, 2, 48, 0x17000, 0x2000), TESTS_EBS_OK);
        CHECK_EQ(check_map(partial, 2, 48, 0x18000, 0x1000), TESTS_EBS_NOT_IN_ONE_DESCRIPTOR);
    }

    /* The range itself: empty, off a page boundary, wrapping. */
    CHECK_EQ(check_map(map, n, 48, 0x80000000ull, 0), TESTS_EBS_SIZE_ZERO);
    CHECK_EQ(check_map(map, n, 48, 0, 0), TESTS_EBS_SIZE_ZERO);
    CHECK_EQ(check_map(map, n, 48, 0x80000001ull, 0x1000), TESTS_EBS_UNALIGNED);
    CHECK_EQ(check_map(map, n, 48, 0x80000800ull, 0x1000), TESTS_EBS_UNALIGNED);
    CHECK_EQ(check_map(map, n, 48, 0x80000FFFull, 0x1000), TESTS_EBS_UNALIGNED);
    CHECK_EQ(check_map(map, n, 48, 0xFFFFFFFFFFFFF000ull, 0x2000), TESTS_EBS_WRAPS);
    CHECK_EQ(check_map(map, n, 48, 0x1000ull, ~0ull), TESTS_EBS_WRAPS);
    CHECK_EQ(check_map(map, n, 48, 0xFFFFFFFFFFFFF000ull, 0x1000), TESTS_EBS_WRAPS); /* ends at 2^64 */
    CHECK_EQ(check_map(map, n, 48, 0xFFFFFFFFFFFFE000ull, 0x1FFFull), TESTS_EBS_NOT_IN_ONE_DESCRIPTOR); /* ends at 2^64 - 1 */
    CHECK_EQ(check_map(map, n, 48, 0x80000000ull, ~0ull - 0x80000000ull + 1), TESTS_EBS_WRAPS);
    /* The first defect found is the one reported. */
    CHECK_EQ(check_map(map, n, 48, 0x80000001ull, 0), TESTS_EBS_SIZE_ZERO);
    CHECK_EQ(check_map(map, n, 48, 0xFFFFFFFFFFFFF001ull, 0x2000), TESTS_EBS_UNALIGNED);
    CHECK_EQ(check_map(map, 0, 48, 0xFFFFFFFFFFFFF000ull, 0x2000), TESTS_EBS_WRAPS);

    /* The map: absent, empty, one descriptor over the fork's limit, a bad stride and a
     * descriptor that cannot be right anywhere in it. */
    CHECK_EQ(tests_ebs_check_range(NULL, 0x80000000ull, 0x1000), TESTS_EBS_NO_MAP);
    {
        probe_memmap no_base = {NULL, 48, 8};

        CHECK_EQ(tests_ebs_check_range(&no_base, 0x80000000ull, 0x1000), TESTS_EBS_NO_MAP);
    }
    CHECK_EQ(check_map(map, 0, 48, 0x80000000ull, 0x1000), TESTS_EBS_NO_MAP);
    {
        xdesc *many = xalloc(513 * sizeof *many);
        int rc;

        for (size_t i = 0; i < 513; i++)
            many[i] = (xdesc){2, 0x100000ull + i * 0x10000ull, 0x10, A_ALL};
        rc = check_map(many, 512, 48, 0x100000ull, 0x1000);
        CHECK_EQ(rc, TESTS_EBS_OK);
        rc = check_map(many, 513, 48, 0x100000ull, 0x1000);
        CHECK_EQ(rc, TESTS_EBS_MAP_TOO_LARGE);
        CHECK_EQ(check_map(many, 513, 48, 0x100000ull, 0), TESTS_EBS_SIZE_ZERO);
        free(many);
    }
    {
        uint8_t *raw = raw_map(map, n, 48);
        probe_memmap odd = {raw, 44, n};
        const size_t bad_strides[] = {0, 8, 32, 39, 44, 264, 4096};

        for (size_t i = 0; i < sizeof bad_strides / sizeof bad_strides[0]; i++) {
            odd.desc_size = bad_strides[i];
            CHECK_EQ(tests_ebs_check_range(&odd, 0x80000000ull, 0x1000), TESTS_EBS_MAP_INVALID);
        }
        free(raw);
    }
    {
        /* A descriptor with no pages, one too large to have an end and one whose end wraps each
         * make the map unusable, wherever they sit: the descriptor that would hold the range is
         * fine, and the check must still refuse. */
        xdesc holder = {1, 0x80000000ull, 0x190, A_ALL};
        xdesc bad[3] = {{2, 0x1000, 0, A_ALL},
                        {2, 0x1000, ~0ull, A_ALL},
                        {2, 0xFFFFFFFFFFFFF000ull, 2, A_ALL}};

        for (size_t i = 0; i < 3; i++) {
            xdesc pair[2];

            pair[0] = holder;
            pair[1] = bad[i];
            CHECK_EQ(check_map(pair, 2, 48, 0x80000000ull, 0x1000), TESTS_EBS_MAP_INVALID);
            pair[0] = bad[i];
            pair[1] = holder;
            CHECK_EQ(check_map(pair, 2, 48, 0x80000000ull, 0x1000), TESTS_EBS_MAP_INVALID);
        }
    }
}

/* ---- Randomized maps against the 128-bit reference ---- */

typedef struct {
    xdesc d[48];
    size_t n;
    size_t stride;
    uint64_t base, size;
} xcase;

static uint64_t rnd_attr(void)
{
    static const uint64_t attrs[] = {0, A_NO_WB, 0x8, A_ALL, 0x100F, 0x800000000000100Full, 0x8000000000000000ull,
                                     0x1000};

    return attrs[rnd_below(sizeof attrs / sizeof attrs[0])];
}

static void random_case(xcase *c)
{
    static const size_t strides[] = {40, 48, 56, 64};
    uint64_t cursor = (uint64_t)rnd_below(1u << 20) * PAGE;
    const xdesc *d;

    c->n = rnd_below(24);
    c->stride = strides[rnd_below(4)];
    for (size_t i = 0; i < c->n; i++) {
        uint64_t pages = 1 + rnd_below(64);

        c->d[i].type = rnd_below(10) < 6 ? swept_types[rnd_below(7)] : rnd_below(16);
        c->d[i].pages = pages;
        c->d[i].attr = rnd_attr();
        if (i > 0 && rnd_below(8) == 0)
            c->d[i].phys = c->d[rnd_below((uint32_t)i)].phys + (uint64_t)rnd_below(8) * PAGE; /* overlaps */
        else {
            cursor += (uint64_t)rnd_below(3) * PAGE; /* 0: adjacent to the one before */
            c->d[i].phys = cursor;
            cursor += pages * PAGE;
        }
    }

    if (c->n == 0) {
        /* No map: the range is ordinary, so that the empty map is the only defect most of the time. */
        c->base = (uint64_t)rnd_below(1u << 20) * PAGE;
        c->size = (uint64_t)(1 + rnd_below(1u << 12)) * PAGE;
        if (rnd_below(10) == 0)
            c->size = 0;
        return;
    }
    switch (rnd_below(8)) {
    case 0:
    case 1:
    case 2:
    case 3:
        /* Around a descriptor: inside it, past either end, off a page boundary, or empty. */
        d = &c->d[rnd_below((uint32_t)c->n)];
        c->base = d->phys + (uint64_t)rnd_below((uint32_t)d->pages) * PAGE;
        c->size = 1 + (uint64_t)rnd_below((uint32_t)(d->phys + d->pages * PAGE - c->base + 2 * PAGE));
        if (rnd_below(6) == 0)
            c->base -= PAGE;
        if (rnd_below(8) == 0)
            c->base += rnd_below(4096);
        if (rnd_below(10) == 0)
            c->size = 0;
        break;
    case 4:
    case 5:
        c->base = (uint64_t)rnd_below((1u << 20) + 2048u) * PAGE + (rnd_below(4) == 0 ? rnd_below(4096) : 0);
        c->size = (uint64_t)rnd_below(1u << 20) * 16;
        break;
    default:
        /* Within 256 pages of the top of the address space, where a sum can wrap. */
        c->base = 0ull - (uint64_t)(1 + rnd_below(256)) * PAGE + (rnd_below(3) == 0 ? rnd_below(4096) : 0);
        c->size = (uint64_t)rnd_below(1u << 16) * 512;
        break;
    }
}

/* What the check must say, in 128-bit arithmetic so that no wrap-around can be shared with it. */
static int reference_status(const xcase *c)
{
    unsigned __int128 lo = c->base, hi = (unsigned __int128)c->base + c->size;
    unsigned holders = 0;
    uint32_t type = 0;
    uint64_t attr = 0;

    if (c->size == 0)
        return TESTS_EBS_SIZE_ZERO;
    if (c->base % 4096 != 0)
        return TESTS_EBS_UNALIGNED;
    if (hi > (unsigned __int128)~0ull)
        return TESTS_EBS_WRAPS;
    if (c->n == 0)
        return TESTS_EBS_NO_MAP;
    for (size_t i = 0; i < c->n; i++) {
        unsigned __int128 dlo = c->d[i].phys, dhi = dlo + (unsigned __int128)c->d[i].pages * 4096;

        if (c->d[i].pages == 0 || dhi > (unsigned __int128)~0ull)
            return TESTS_EBS_MAP_INVALID;
        if (dlo <= lo && hi <= dhi) {
            holders++;
            type = c->d[i].type;
            attr = c->d[i].attr;
        }
    }
    if (holders != 1)
        return TESTS_EBS_NOT_IN_ONE_DESCRIPTOR;
    if (!(type == 1 || type == 2 || type == 3 || type == 4 || type == 5 || type == 6 || type == 9))
        return TESTS_EBS_WRONG_TYPE;
    if (((attr >> 3) & 1) == 0)
        return TESTS_EBS_NOT_WRITE_BACK;
    return TESTS_EBS_OK;
}

void test_x_ebs_model(void)
{
    unsigned by_status[16] = {0};
    xcase *c = xalloc(sizeof *c);

    section("ebs range model");
    for (unsigned round = 0; round < 6000; round++) {
        int want, got;
        uint8_t *raw;
        probe_memmap m;
        tests_ebs_table t;

        random_case(c);
        raw = raw_map(c->d, c->n, c->stride);
        m = (probe_memmap){raw, c->stride, c->n};
        want = reference_status(c);
        got = tests_ebs_check_range(&m, c->base, c->size);
        CHECK_EQ(got, want);
        by_status[want < 16 ? want : 15]++;
        /* The builder agrees with the check and writes a valid table only for an accepted range. */
        CHECK_EQ(tests_ebs_build(&t, &m, c->base, c->size), want);
        if (want == TESTS_EBS_OK) {
            CHECK(ref_valid((const uint8_t *)&t));
            CHECK_EQU(t.range[0].base, c->base);
            CHECK_EQU(t.range[0].size, c->size);
        } else {
            CHECK(!ref_valid((const uint8_t *)&t));
        }
        free(raw);
    }
    /* The generator reaches every verdict, or the comparison above would be missing a branch. */
    for (size_t i = 0; i < sizeof status_idents / sizeof status_idents[0]; i++) {
        int s = status_idents[i].status;

        if (s == TESTS_EBS_MAP_TOO_LARGE || s == TESTS_EBS_MAP_INVALID)
            continue; /* the fixed cases of ebs range check cover these; the generator stays below 512 */
        CHECK(by_status[s] >= 20);
    }
    free(c);
}

/* ---- The fork's own validator and range rule, when it is available ---- */

#ifdef X_SLBOUNCE
static const char *slbounce_name(int rc)
{
    return sp11_sweep_status_name(rc);
}

static int slbounce_status(const xcase *c, const xdesc *d, size_t n)
{
    struct sp11_sweep_descriptor *sd = xalloc((n ? n : 1) * sizeof *sd);
    struct sp11_sweep_op ops[256];
    uint32_t n_ops = 0;
    int rc;

    for (size_t i = 0; i < n; i++)
        sd[i] = (struct sp11_sweep_descriptor){d[i].type, d[i].attr, d[i].phys, d[i].pages};
    rc = sp11_sweep_range(sd, (uint32_t)n, c->base, c->size, TESTS_EBS_CHUNK_CAP, ops, 256, &n_ops);
    free(sd);
    return rc;
}

void test_x_ebs_slbounce(void)
{
    static const struct {
        uint32_t a;
        uint16_t b, c;
        uint8_t d[8];
    } ours_guid = TESTS_EBS_GUID_INIT, theirs_guid = SP11_EBS_RANGES_GUID_INIT;
    static const uint16_t ours_name[] = TESTS_EBS_VARIABLE_NAME_U16, theirs_name[] = SP11_EBS_RANGES_VARIABLE_NAME_U16;
    uint8_t data[300];
    xcase *c = xalloc(sizeof *c);
    struct sp11_ebs_ranges_table theirs;
    tests_ebs_table ours;
    unsigned agreed = 0;

    section("ebs against slbounce");
    /* The identity and the layout. */
    CHECK_EQU(SP11_EBS_RANGES_MAGIC, TESTS_EBS_MAGIC);
    CHECK_EQU(SP11_EBS_RANGES_VERSION, TESTS_EBS_VERSION);
    CHECK_EQU(SP11_EBS_MAX_RANGES, TESTS_EBS_MAX_RANGES);
    CHECK_EQU(sizeof(struct sp11_ebs_range), sizeof(tests_ebs_range));
    CHECK_EQU(sizeof(struct sp11_ebs_ranges_table), sizeof(tests_ebs_table));
    CHECK_EQU(offsetof(struct sp11_ebs_ranges_table, magic), offsetof(tests_ebs_table, magic));
    CHECK_EQU(offsetof(struct sp11_ebs_ranges_table, version), offsetof(tests_ebs_table, version));
    CHECK_EQU(offsetof(struct sp11_ebs_ranges_table, count), offsetof(tests_ebs_table, count));
    CHECK_EQU(offsetof(struct sp11_ebs_ranges_table, chunk_cap), offsetof(tests_ebs_table, chunk_cap));
    CHECK_EQU(offsetof(struct sp11_ebs_ranges_table, reserved), offsetof(tests_ebs_table, reserved));
    CHECK_EQU(offsetof(struct sp11_ebs_ranges_table, range), offsetof(tests_ebs_table, range));
    CHECK_EQU(offsetof(struct sp11_ebs_ranges_table, checksum), offsetof(tests_ebs_table, checksum));
    CHECK(memcmp(&ours_guid, &theirs_guid, sizeof ours_guid) == 0);
    CHECK_EQU(sizeof ours_name, sizeof theirs_name);
    CHECK(memcmp(ours_name, theirs_name, sizeof ours_name) == 0);
    for (size_t i = 0; i < sizeof data; i++)
        data[i] = (uint8_t)(rnd() >> 7);
    for (size_t n = 0; n <= sizeof data; n += 37)
        CHECK_EQU(tests_ebs_fnv1a(data, n), sp11_ebs_ranges_fnv(data, n));

    /* Its validator accepts what this image builds and refuses every single-bit corruption. */
    for (size_t i = 0; i < sizeof golden / sizeof golden[0]; i++) {
        int rc;

        build_one(&ours, golden[i].base, golden[i].size, &rc);
        memcpy(&theirs, &ours, sizeof theirs);
        CHECK_EQ(rc, TESTS_EBS_OK);
        CHECK(sp11_ebs_ranges_valid(&theirs));
        for (size_t byte = 0; byte < sizeof theirs; byte++)
            for (unsigned bit = 0; bit < 8; bit++) {
                struct sp11_ebs_ranges_table broken = theirs;

                ((uint8_t *)&broken)[byte] ^= (uint8_t)(1u << bit);
                CHECK(!sp11_ebs_ranges_valid(&broken));
            }
    }
    memset(&theirs, 0, sizeof theirs);
    CHECK(!sp11_ebs_ranges_valid(&theirs));

    /* Its range rule: whatever this image accepts the fork's sweep accepts, and where both name
     * the same defect they name the same one. The three refusals that are this image's own
     * (alignment, wrap, map size and shape) have no counterpart there. */
    for (unsigned round = 0; round < 6000; round++) {
        int mine, theirs_rc;
        uint8_t *raw;
        probe_memmap m;

        random_case(c);
        raw = raw_map(c->d, c->n, c->stride);
        m = (probe_memmap){raw, c->stride, c->n};
        mine = tests_ebs_check_range(&m, c->base, c->size);
        theirs_rc = slbounce_status(c, c->d, c->n);
        free(raw);
        if (mine == TESTS_EBS_OK)
            CHECK_EQ(theirs_rc, SP11_SWEEP_OK);
        switch (mine) {
        case TESTS_EBS_SIZE_ZERO:
            CHECK_EQ(theirs_rc, SP11_SWEEP_ERR_BAD_RANGE);
            break;
        case TESTS_EBS_NOT_IN_ONE_DESCRIPTOR:
            CHECK_EQ(theirs_rc, SP11_SWEEP_ERR_NOT_CONTAINED);
            break;
        case TESTS_EBS_WRONG_TYPE:
            CHECK_EQ(theirs_rc, SP11_SWEEP_ERR_WRONG_TYPE);
            break;
        case TESTS_EBS_NOT_WRITE_BACK:
            CHECK_EQ(theirs_rc, SP11_SWEEP_ERR_NOT_WRITEBACK);
            break;
        default:
            break;
        }
        agreed += mine == TESTS_EBS_OK && theirs_rc == SP11_SWEEP_OK;
    }
    CHECK(agreed >= 20);
    CHECK_STR(slbounce_name(SP11_SWEEP_OK), "OK");
    free(c);
}
#endif /* X_SLBOUNCE */

/* ---- `--ebs-cases`: the differential run against the Python model ---- */

/* Input: the number of cases, then per case `base size n` and n descriptors `type phys pages attr`
 * (all hexadecimal except the counts). Output: one `CASE` line per case with the builder's verdict
 * and the 160 table bytes in hex (all zero for a refused case). */
int x_ebs_cases(void)
{
    unsigned count;

    if (scanf("%u", &count) != 1)
        return 2;
    for (unsigned i = 0; i < count; i++) {
        unsigned long long base, size;
        unsigned n;
        xdesc *d;
        uint8_t *raw;
        probe_memmap m;
        tests_ebs_table t;
        int rc;

        if (scanf("%llx %llx %u", &base, &size, &n) != 3)
            return 2;
        d = xalloc((n ? n : 1) * sizeof *d);
        for (unsigned k = 0; k < n; k++) {
            unsigned type;
            unsigned long long phys, pages, attr;

            if (scanf("%x %llx %llx %llx", &type, &phys, &pages, &attr) != 4)
                return 2;
            d[k] = (xdesc){type, phys, pages, attr};
        }
        raw = raw_map(d, n, 48);
        m = (probe_memmap){raw, 48, n};
        memset(&t, 0xAA, sizeof t);
        rc = tests_ebs_build(&t, &m, base, size);
        printf("CASE %u VERDICT=%s TABLE=", i, status_ident(rc));
        for (size_t k = 0; k < sizeof t; k++)
            printf("%02x", ((const uint8_t *)&t)[k]);
#ifdef X_SLBOUNCE
        {
            xcase one = {.base = base, .size = size};
            int theirs = n <= 512 ? slbounce_status(&one, d, n) : -1;
            struct sp11_ebs_ranges_table copy;

            memcpy(&copy, &t, sizeof copy);
            printf(" SLB=%s VALID=%d", theirs < 0 ? "-" : slbounce_name(theirs),
                   rc == TESTS_EBS_OK ? sp11_ebs_ranges_valid(&copy) : -1);
        }
#endif
        printf("\n");
        free(raw);
        free(d);
    }
    return 0;
}
