/* SPDX-License-Identifier: MIT */
/* Tests for probe_gop_select, the framebuffer choice of the SP11 EL2 probe.
 *
 * Started by the UEFI Shell, the probe sees a console-out handle that carries
 * no GOP, so the console GOP of a direct launch cannot be identified and the
 * choice falls to the same-framebuffer rule: the one framebuffer that every
 * usable GOP agrees on, or a refusal when they disagree. The cases drive both
 * rules with the hostile shapes that matter: two different displays, Blt-only
 * and bitmask devices, ranges outside the memory map or in a memory type that
 * is not a framebuffer's, mirrors, no GOP at all, and each field in which "the
 * same framebuffer" can differ. Every check carries its case name so that a
 * mutation run can say which case catches which weakened rule.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "fbtext.h"
#include "harness.h"
#include "logic.h"

#define SEL_A 0x7C7A0000ull /* in the map as reserved: the QEMU rehearsal's framebuffer */
#define SEL_B 0xB0000000ull /* in the map as MMIO: a second, different display */
#define SEL_C 0xA0000000ull /* not in the map at all */
#define SEL_D 0x90000000ull /* in the map, but as conventional memory */
#define SEL_PAGES 768u      /* 0x300000 bytes: the size the rehearsal's GOP reported */

#define PIXEL_MASK 2u     /* UEFI PixelBitMask */
#define PIXEL_BLT_ONLY 3u /* UEFI PixelBltOnly */

#define GOPC(con, base, size, w, h, stride, fmt) {(con), (base), (size), (w), (h), (stride), (fmt)}
#define FB_AT(con, base) GOPC(con, base, 0x300000u, 800u, 600u, 800u, PROBE_PIXEL_BGRX)
#define BLT_AT(con, base) GOPC(con, base, 0x300000u, 800u, 600u, 800u, PIXEL_BLT_ONLY)
#define MASK_AT(con, base) GOPC(con, base, 0x300000u, 800u, 600u, 800u, PIXEL_MASK)

#define SEL_MAX_CASES 4

typedef struct {
    const char *name;
    unsigned n;
    probe_gop_candidate c[SEL_MAX_CASES];
    int status;
    int rule;
    unsigned index;    /* the chosen candidate; only meaningful for PROBE_GOP_OK */
    unsigned examined; /* rule 1 stops at the candidate it chooses, every refusal examines all */
} sel_case;

static const char *g_failed_names[128];
static unsigned g_failed_count;

static void note_failed_case(const char *name)
{
    for (unsigned i = 0; i < g_failed_count; i++)
        if (strcmp(g_failed_names[i], name) == 0)
            return;
    if (g_failed_count < sizeof g_failed_names / sizeof g_failed_names[0])
        g_failed_names[g_failed_count++] = name;
}

#define EXPECT(name, cond)                                                                      \
    do {                                                                                        \
        g_checks++;                                                                             \
        if (!(cond)) {                                                                          \
            char msg_[200];                                                                     \
            snprintf(msg_, sizeof msg_, "[%s] %s", (name), #cond);                              \
            failed(__LINE__, msg_);                                                             \
            note_failed_case(name);                                                             \
        }                                                                                       \
    } while (0)

static int fb_is_zero(const probe_fb *fb)
{
    static const probe_fb zero;

    return memcmp(fb, &zero, sizeof zero) == 0;
}

static void check_outcome(const char *name, const probe_gop_candidate *cand,
                          const probe_gop_choice *ch, int status, int rule, size_t index,
                          size_t examined)
{
    EXPECT(name, ch->status == status);
    EXPECT(name, ch->rule == rule);
    EXPECT(name, ch->examined == examined);
    if (status == PROBE_GOP_OK) {
        const probe_gop_candidate *c = &cand[index];

        EXPECT(name, ch->index == index);
        EXPECT(name, ch->fb.base == (volatile uint8_t *)(uintptr_t)c->base);
        EXPECT(name, ch->fb.width == c->width);
        EXPECT(name, ch->fb.height == c->height);
        EXPECT(name, ch->fb.stride_bytes == c->pixels_per_scanline * 4);
        EXPECT(name, ch->fb.format == c->format);
    } else {
        /* A refusal hands nothing over: no index, no rule, no framebuffer. */
        EXPECT(name, ch->index == 0);
        EXPECT(name, fb_is_zero(&ch->fb));
    }
}

static void run_select(const char *name, const probe_memmap *map, const probe_gop_candidate *cand,
                       size_t n, int status, int rule, size_t index, size_t examined)
{
    probe_gop_choice ch;

    /* Garbage in the result proves that every field is written. */
    memset(&ch, 0x5A, sizeof ch);
    probe_gop_select(map, cand, n, &ch);
    check_outcome(name, cand, &ch, status, rule, index, examined);
}

static const sel_case g_cases[] = {
    /* Rule 1: the GOP of the console handle, as a direct launch behaves today. */
    {"console only", 1, {FB_AT(1, SEL_A)}, PROBE_GOP_OK, PROBE_GOP_RULE_CONSOLE, 0, 1},
    {"direct launch order: physical then console", 2, {FB_AT(0, SEL_A), FB_AT(1, SEL_A)},
     PROBE_GOP_OK, PROBE_GOP_RULE_CONSOLE, 1, 2},
    {"console first stops the scan", 2, {FB_AT(1, SEL_A), FB_AT(0, SEL_A)}, PROBE_GOP_OK,
     PROBE_GOP_RULE_CONSOLE, 0, 1},
    {"console beats a different display", 2, {FB_AT(0, SEL_B), FB_AT(1, SEL_A)}, PROBE_GOP_OK,
     PROBE_GOP_RULE_CONSOLE, 1, 2},
    {"console first beats a different display", 2, {FB_AT(1, SEL_A), FB_AT(0, SEL_B)},
     PROBE_GOP_OK, PROBE_GOP_RULE_CONSOLE, 0, 1},
    {"console beats disagreeing others", 3, {FB_AT(0, SEL_B), FB_AT(0, SEL_A), FB_AT(1, SEL_A)},
     PROBE_GOP_OK, PROBE_GOP_RULE_CONSOLE, 2, 3},

    /* A console GOP that is present but refused is not second-guessed. */
    {"refused console, Blt-only", 2, {BLT_AT(1, SEL_A), FB_AT(0, SEL_A)}, PROBE_GOP_BAD_FB,
     PROBE_GOP_RULE_NONE, 0, 2},
    {"refused console, bitmask", 2, {MASK_AT(1, SEL_A), FB_AT(0, SEL_A)}, PROBE_GOP_BAD_FB,
     PROBE_GOP_RULE_NONE, 0, 2},
    {"refused console, outside the map", 2, {FB_AT(1, SEL_C), FB_AT(0, SEL_A)},
     PROBE_GOP_NOT_IN_MAP, PROBE_GOP_RULE_NONE, 0, 2},
    {"refused console, wrong memory type", 2, {FB_AT(1, SEL_D), FB_AT(0, SEL_A)},
     PROBE_GOP_NOT_IN_MAP, PROBE_GOP_RULE_NONE, 0, 2},
    {"refused console, usable one listed first", 2, {FB_AT(0, SEL_A), FB_AT(1, SEL_C)},
     PROBE_GOP_NOT_IN_MAP, PROBE_GOP_RULE_NONE, 0, 2},

    /* Rule 2: no GOP on the console handle (the UEFI Shell). */
    {"shell: one usable GOP", 1, {FB_AT(0, SEL_A)}, PROBE_GOP_OK, PROBE_GOP_RULE_SAME, 0, 1},
    {"shell: splitter and physical mirror one framebuffer", 2, {FB_AT(0, SEL_A), FB_AT(0, SEL_A)},
     PROBE_GOP_OK, PROBE_GOP_RULE_SAME, 0, 2},
    {"shell: three identical GOPs", 3, {FB_AT(0, SEL_A), FB_AT(0, SEL_A), FB_AT(0, SEL_A)},
     PROBE_GOP_OK, PROBE_GOP_RULE_SAME, 0, 3},
    {"shell: usable GOP after an unusable one", 2, {FB_AT(0, SEL_C), FB_AT(0, SEL_A)},
     PROBE_GOP_OK, PROBE_GOP_RULE_SAME, 1, 2},
    {"shell: usable GOP before an unusable one", 2, {FB_AT(0, SEL_A), FB_AT(0, SEL_C)},
     PROBE_GOP_OK, PROBE_GOP_RULE_SAME, 0, 2},

    /* Usable GOPs that disagree: refuse. */
    {"two distinct framebuffers", 2, {FB_AT(0, SEL_A), FB_AT(0, SEL_B)}, PROBE_GOP_AMBIGUOUS,
     PROBE_GOP_RULE_NONE, 0, 2},
    {"two distinct framebuffers, reversed", 2, {FB_AT(0, SEL_B), FB_AT(0, SEL_A)},
     PROBE_GOP_AMBIGUOUS, PROBE_GOP_RULE_NONE, 0, 2},
    {"distinct framebuffer after a mirror pair", 3,
     {FB_AT(0, SEL_A), FB_AT(0, SEL_A), FB_AT(0, SEL_B)}, PROBE_GOP_AMBIGUOUS,
     PROBE_GOP_RULE_NONE, 0, 3},
    {"distinct framebuffer between mirrors", 3,
     {FB_AT(0, SEL_A), FB_AT(0, SEL_B), FB_AT(0, SEL_A)}, PROBE_GOP_AMBIGUOUS,
     PROBE_GOP_RULE_NONE, 0, 3},
    {"non-mirroring display: other base and geometry", 2,
     {FB_AT(0, SEL_A), GOPC(0, SEL_B, 0x200000u, 640u, 480u, 640u, PROBE_PIXEL_BGRX)},
     PROBE_GOP_AMBIGUOUS, PROBE_GOP_RULE_NONE, 0, 2},
    {"same base, size differs", 2,
     {FB_AT(0, SEL_A), GOPC(0, SEL_A, 0x400000u, 800u, 600u, 800u, PROBE_PIXEL_BGRX)},
     PROBE_GOP_AMBIGUOUS, PROBE_GOP_RULE_NONE, 0, 2},
    {"same base, width differs", 2,
     {FB_AT(0, SEL_A), GOPC(0, SEL_A, 0x300000u, 640u, 600u, 800u, PROBE_PIXEL_BGRX)},
     PROBE_GOP_AMBIGUOUS, PROBE_GOP_RULE_NONE, 0, 2},
    {"same base, height differs", 2,
     {FB_AT(0, SEL_A), GOPC(0, SEL_A, 0x300000u, 800u, 480u, 800u, PROBE_PIXEL_BGRX)},
     PROBE_GOP_AMBIGUOUS, PROBE_GOP_RULE_NONE, 0, 2},
    {"same base, stride differs", 2,
     {FB_AT(0, SEL_A), GOPC(0, SEL_A, 0x300000u, 800u, 600u, 832u, PROBE_PIXEL_BGRX)},
     PROBE_GOP_AMBIGUOUS, PROBE_GOP_RULE_NONE, 0, 2},
    {"same base, format differs", 2,
     {FB_AT(0, SEL_A), GOPC(0, SEL_A, 0x300000u, 800u, 600u, 800u, PROBE_PIXEL_RGBX)},
     PROBE_GOP_AMBIGUOUS, PROBE_GOP_RULE_NONE, 0, 2},

    /* A GOP that fails a mode or memory-map check is neither chosen nor counted. */
    {"one usable and one Blt-only", 2, {BLT_AT(0, SEL_B), FB_AT(0, SEL_A)}, PROBE_GOP_OK,
     PROBE_GOP_RULE_SAME, 1, 2},
    {"one usable and one Blt-only, reversed", 2, {FB_AT(0, SEL_A), BLT_AT(0, SEL_B)},
     PROBE_GOP_OK, PROBE_GOP_RULE_SAME, 0, 2},
    {"one usable and a Blt-only on its own base", 2, {BLT_AT(0, SEL_A), FB_AT(0, SEL_A)},
     PROBE_GOP_OK, PROBE_GOP_RULE_SAME, 1, 2},
    {"one usable and one bitmask", 2, {MASK_AT(0, SEL_B), FB_AT(0, SEL_A)}, PROBE_GOP_OK,
     PROBE_GOP_RULE_SAME, 1, 2},
    {"one usable and one bitmask, reversed", 2, {FB_AT(0, SEL_A), MASK_AT(0, SEL_B)},
     PROBE_GOP_OK, PROBE_GOP_RULE_SAME, 0, 2},
    {"one usable and one in the wrong memory type", 2, {FB_AT(0, SEL_D), FB_AT(0, SEL_A)},
     PROBE_GOP_OK, PROBE_GOP_RULE_SAME, 1, 2},
    {"one usable and one only partly in the map", 2,
     {GOPC(0, SEL_A, 0x600000u, 1024u, 1000u, 1024u, PROBE_PIXEL_BGRX), FB_AT(0, SEL_A)},
     PROBE_GOP_OK, PROBE_GOP_RULE_SAME, 1, 2},
    {"one usable and one misaligned", 2, {FB_AT(0, SEL_A + 2), FB_AT(0, SEL_A)}, PROBE_GOP_OK,
     PROBE_GOP_RULE_SAME, 1, 2},
    {"one usable and one with zero width", 2,
     {GOPC(0, SEL_A, 0x300000u, 0u, 600u, 800u, PROBE_PIXEL_BGRX), FB_AT(0, SEL_A)}, PROBE_GOP_OK,
     PROBE_GOP_RULE_SAME, 1, 2},
    {"one usable among three refusals", 3, {BLT_AT(0, SEL_B), FB_AT(0, SEL_C), FB_AT(0, SEL_A)},
     PROBE_GOP_OK, PROBE_GOP_RULE_SAME, 2, 3},

    /* Nothing usable: the refusal says why, the worst reason first. */
    {"Blt-only alone", 1, {BLT_AT(0, SEL_A)}, PROBE_GOP_BAD_FB, PROBE_GOP_RULE_NONE, 0, 1},
    {"bitmask alone", 1, {MASK_AT(0, SEL_A)}, PROBE_GOP_BAD_FB, PROBE_GOP_RULE_NONE, 0, 1},
    {"misaligned alone", 1, {FB_AT(0, SEL_A + 2)}, PROBE_GOP_BAD_FB, PROBE_GOP_RULE_NONE, 0, 1},
    {"null base alone", 1, {FB_AT(0, 0)}, PROBE_GOP_BAD_FB, PROBE_GOP_RULE_NONE, 0, 1},
    {"outside the map alone", 1, {FB_AT(0, SEL_C)}, PROBE_GOP_NOT_IN_MAP, PROBE_GOP_RULE_NONE, 0,
     1},
    {"wrong memory type alone", 1, {FB_AT(0, SEL_D)}, PROBE_GOP_NOT_IN_MAP, PROBE_GOP_RULE_NONE, 0,
     1},
    {"identical pair outside the map", 2, {FB_AT(0, SEL_C), FB_AT(0, SEL_C)},
     PROBE_GOP_NOT_IN_MAP, PROBE_GOP_RULE_NONE, 0, 2},
    {"worst refusal wins", 2, {BLT_AT(0, SEL_A), FB_AT(0, SEL_C)}, PROBE_GOP_NOT_IN_MAP,
     PROBE_GOP_RULE_NONE, 0, 2},
    {"worst refusal wins, reversed", 2, {FB_AT(0, SEL_C), BLT_AT(0, SEL_A)},
     PROBE_GOP_NOT_IN_MAP, PROBE_GOP_RULE_NONE, 0, 2},
};

/* The permutations of a small set: the result must not depend on the order in
 * which the firmware lists its handles. */
static int next_permutation(unsigned *a, unsigned n)
{
    unsigned i = n - 1;
    unsigned j = n;

    while (i > 0 && a[i - 1] >= a[i])
        i--;
    if (i == 0)
        return 0;
    while (a[j - 1] <= a[i - 1])
        j--;
    {
        unsigned t = a[i - 1];

        a[i - 1] = a[j - 1];
        a[j - 1] = t;
    }
    for (j = n - 1; i < j; i++, j--) {
        unsigned t = a[i];

        a[i] = a[j];
        a[j] = t;
    }
    return 1;
}

typedef struct {
    const char *name;
    unsigned n;
    probe_gop_candidate set[SEL_MAX_CASES];
    int status;
    int rule;
    uint64_t base; /* the chosen framebuffer's base when status is PROBE_GOP_OK */
} perm_case;

static const perm_case g_perms[] = {
    {"any order: mirrors beside refusals", 4,
     {FB_AT(0, SEL_A), FB_AT(0, SEL_A), BLT_AT(0, SEL_B), FB_AT(0, SEL_C)}, PROBE_GOP_OK,
     PROBE_GOP_RULE_SAME, SEL_A},
    {"any order: two displays and a Blt-only on one of them", 3,
     {FB_AT(0, SEL_A), FB_AT(0, SEL_B), BLT_AT(0, SEL_A)}, PROBE_GOP_AMBIGUOUS,
     PROBE_GOP_RULE_NONE, 0},
    {"any order: three displays and a bitmask", 4,
     {FB_AT(0, SEL_A), FB_AT(0, SEL_B), FB_AT(0, SEL_C), MASK_AT(0, SEL_A)}, PROBE_GOP_AMBIGUOUS,
     PROBE_GOP_RULE_NONE, 0},
    {"any order: the console GOP among others", 3,
     {FB_AT(1, SEL_A), FB_AT(0, SEL_B), BLT_AT(0, SEL_B)}, PROBE_GOP_OK, PROBE_GOP_RULE_CONSOLE,
     SEL_A},
    {"any order: a refused console among usable GOPs", 3,
     {FB_AT(1, SEL_C), FB_AT(0, SEL_A), FB_AT(0, SEL_A)}, PROBE_GOP_NOT_IN_MAP,
     PROBE_GOP_RULE_NONE, 0},
};

static void check_permutations(const probe_memmap *mm)
{
    for (size_t k = 0; k < sizeof g_perms / sizeof g_perms[0]; k++) {
        const perm_case *p = &g_perms[k];
        unsigned order[SEL_MAX_CASES];
        unsigned seen = 0;

        for (unsigned i = 0; i < p->n; i++)
            order[i] = i;
        do {
            probe_gop_candidate cand[SEL_MAX_CASES];
            probe_gop_choice ch;

            for (unsigned i = 0; i < p->n; i++)
                cand[i] = p->set[order[i]];
            memset(&ch, 0x5A, sizeof ch);
            probe_gop_select(mm, cand, p->n, &ch);
            EXPECT(p->name, ch.status == p->status);
            EXPECT(p->name, ch.rule == p->rule);
            if (p->status == PROBE_GOP_OK) {
                EXPECT(p->name, ch.fb.base == (volatile uint8_t *)(uintptr_t)p->base);
                EXPECT(p->name, cand[ch.index].base == p->base);
            } else {
                EXPECT(p->name, fb_is_zero(&ch.fb));
            }
            seen++;
        } while (next_permutation(order, p->n));
        {
            unsigned expect = 1;

            for (unsigned i = 2; i <= p->n; i++)
                expect *= i;
            EXPECT(p->name, seen == expect);
        }
    }
}

/* A second statement of the policy, written as a set of distinct framebuffers
 * instead of the pairwise comparison of the code under test. */
static void reference_choice(const probe_memmap *mm, const probe_gop_candidate *c, size_t n,
                             int *status, int *rule, size_t *index, size_t *examined)
{
    int console_listed = 0;
    int worst = PROBE_GOP_ABSENT;
    size_t first = 0;
    size_t distinct = 0;
    size_t usable = 0;
    probe_fb fb;

    *status = PROBE_GOP_ABSENT;
    *rule = PROBE_GOP_RULE_NONE;
    *index = 0;
    *examined = n;
    for (size_t i = 0; i < n; i++) {
        int s = probe_gop_check(mm, c[i].is_console, c[i].base, c[i].size_bytes, c[i].width,
                                c[i].height, c[i].pixels_per_scanline, c[i].format, &fb);

        console_listed |= c[i].is_console != 0;
        if (s == PROBE_GOP_OK) {
            *status = PROBE_GOP_OK;
            *rule = PROBE_GOP_RULE_CONSOLE;
            *index = i;
            *examined = i + 1;
            return;
        }
        if (s > worst)
            worst = s;
    }
    if (console_listed) {
        *status = worst;
        return;
    }
    worst = PROBE_GOP_ABSENT;
    for (size_t i = 0; i < n; i++) {
        int s = probe_gop_check(mm, 1, c[i].base, c[i].size_bytes, c[i].width, c[i].height,
                                c[i].pixels_per_scanline, c[i].format, &fb);
        int seen_before = 0;

        if (s != PROBE_GOP_OK) {
            if (s > worst)
                worst = s;
            continue;
        }
        for (size_t j = 0; j < i && !seen_before; j++) {
            int sj = probe_gop_check(mm, 1, c[j].base, c[j].size_bytes, c[j].width, c[j].height,
                                     c[j].pixels_per_scanline, c[j].format, &fb);

            seen_before = sj == PROBE_GOP_OK && c[j].base == c[i].base &&
                          c[j].size_bytes == c[i].size_bytes && c[j].width == c[i].width &&
                          c[j].height == c[i].height &&
                          c[j].pixels_per_scanline == c[i].pixels_per_scanline &&
                          c[j].format == c[i].format;
        }
        if (usable++ == 0)
            first = i;
        if (!seen_before)
            distinct++;
    }
    if (usable == 0) {
        *status = worst;
    } else if (distinct > 1) {
        *status = PROBE_GOP_AMBIGUOUS;
    } else {
        *status = PROBE_GOP_OK;
        *rule = PROBE_GOP_RULE_SAME;
        *index = first;
    }
}

static probe_gop_candidate random_candidate(void)
{
    static const uint64_t bases[] = {SEL_A, SEL_A, SEL_B, SEL_C, SEL_D, SEL_A + 2, 0};
    static const uint32_t formats[] = {PROBE_PIXEL_BGRX, PROBE_PIXEL_BGRX, PROBE_PIXEL_RGBX,
                                       PIXEL_MASK,       PIXEL_BLT_ONLY,   7};
    static const uint32_t widths[] = {800, 800, 640, 0, 20000};
    static const uint32_t heights[] = {600, 600, 480};
    static const uint32_t strides[] = {800, 800, 832, 640};
    static const uint32_t sizes[] = {0x300000, 0x300000, 0x400000, 0x1000};
    probe_gop_candidate c;

    c.is_console = rnd_below(5) == 0;
    c.base = bases[rnd_below(sizeof bases / sizeof bases[0])];
    c.format = formats[rnd_below(sizeof formats / sizeof formats[0])];
    c.width = widths[rnd_below(sizeof widths / sizeof widths[0])];
    c.height = heights[rnd_below(sizeof heights / sizeof heights[0])];
    c.pixels_per_scanline = strides[rnd_below(sizeof strides / sizeof strides[0])];
    c.size_bytes = sizes[rnd_below(sizeof sizes / sizeof sizes[0])];
    return c;
}

static void check_random_lists(const probe_memmap *mm)
{
    for (unsigned round = 0; round < 6000; round++) {
        probe_gop_candidate cand[6];
        probe_gop_choice ch;
        size_t n = rnd_below(7);
        int status;
        int rule;
        size_t index;
        size_t examined;

        for (size_t i = 0; i < n; i++)
            cand[i] = random_candidate();
        reference_choice(mm, cand, n, &status, &rule, &index, &examined);
        memset(&ch, 0x5A, sizeof ch);
        probe_gop_select(mm, cand, n, &ch);
        check_outcome("random lists against the reference", cand, &ch, status, rule, index,
                      examined);
    }
}

static void check_names(void)
{
    CHECK_STR(probe_gop_status_name(PROBE_GOP_AMBIGUOUS), "AMBIGUOUS GOP");
    CHECK_STR(probe_gop_rule_name(PROBE_GOP_RULE_NONE), "NONE");
    CHECK_STR(probe_gop_rule_name(PROBE_GOP_RULE_CONSOLE), "CONSOLE");
    CHECK_STR(probe_gop_rule_name(PROBE_GOP_RULE_SAME), "SAME");
    CHECK_STR(probe_gop_rule_name(99), "UNKNOWN");
    CHECK_STR(probe_gop_rule_name(-1), "UNKNOWN");
    /* Saying that usable framebuffers disagree is the strongest refusal. */
    CHECK(PROBE_GOP_NOT_IN_MAP < PROBE_GOP_AMBIGUOUS);
    /* The page row "GOP <w>X<h> SEL=<rule>" must stay inside a line's payload
     * whatever the rule value is. */
    for (int rule = -2; rule < 10; rule++)
        CHECK(strlen(probe_gop_rule_name(rule)) <= 7);
}

void test_gop_select(void)
{
    uint8_t map[3 * 48];
    probe_memmap mm;
    probe_memmap empty;
    probe_gop_candidate many[32];
    probe_gop_choice ch;

    section("framebuffer choice");
    put_desc(map, 48, 0, 0, SEL_A, SEL_PAGES);
    put_desc(map, 48, 1, 7, SEL_D, SEL_PAGES);
    put_desc(map, 48, 2, 11, SEL_B, SEL_PAGES);
    CHECK_EQ(probe_memmap_init(&mm, map, sizeof map, sizeof map, 48), PROBE_MM_OK);

    for (size_t k = 0; k < sizeof g_cases / sizeof g_cases[0]; k++) {
        const sel_case *s = &g_cases[k];

        run_select(s->name, &mm, s->c, s->n, s->status, s->rule, s->index, s->examined);
    }

    run_select("zero candidates", &mm, g_cases[0].c, 0, PROBE_GOP_ABSENT, PROBE_GOP_RULE_NONE, 0, 0);
    run_select("zero candidates, no list", &mm, NULL, 0, PROBE_GOP_ABSENT, PROBE_GOP_RULE_NONE, 0,
               0);

    /* Without a valid memory map nothing is usable, so there is nothing to
     * choose between and nothing to call ambiguous. */
    memset(&empty, 0, sizeof empty);
    for (size_t k = 0; k < 2; k++) {
        const probe_memmap *none = k ? NULL : &empty;
        const probe_gop_candidate console = FB_AT(1, SEL_A);
        const probe_gop_candidate shell_pair[2] = {FB_AT(0, SEL_A), FB_AT(0, SEL_B)};

        run_select("no memory map, console GOP", none, &console, 1, PROBE_GOP_NOT_IN_MAP,
                   PROBE_GOP_RULE_NONE, 0, 1);
        run_select("no memory map, shell GOPs", none, shell_pair, 2, PROBE_GOP_NOT_IN_MAP,
                   PROBE_GOP_RULE_NONE, 0, 2);
    }

    /* The most handles the probe collects. */
    for (size_t i = 0; i < 32; i++)
        many[i] = (probe_gop_candidate)FB_AT(0, SEL_A);
    run_select("32 identical GOPs", &mm, many, 32, PROBE_GOP_OK, PROBE_GOP_RULE_SAME, 0, 32);
    many[31] = (probe_gop_candidate)FB_AT(0, SEL_B);
    run_select("32 GOPs, the last one different", &mm, many, 32, PROBE_GOP_AMBIGUOUS,
               PROBE_GOP_RULE_NONE, 0, 32);

    /* The choice owns its framebuffer: a later call cannot alias an earlier one. */
    {
        const probe_gop_candidate pair[2] = {FB_AT(0, SEL_A), FB_AT(0, SEL_B)};
        probe_gop_choice first;

        probe_gop_select(&mm, pair, 1, &first);
        probe_gop_select(&mm, pair, 2, &ch);
        EXPECT("choices do not alias", first.status == PROBE_GOP_OK);
        EXPECT("choices do not alias", ch.status == PROBE_GOP_AMBIGUOUS);
        EXPECT("choices do not alias", first.fb.base == (volatile uint8_t *)(uintptr_t)SEL_A);
    }

    check_permutations(&mm);
    check_random_lists(&mm);
    check_names();

    if (g_failed_count != 0) {
        /* One line for the mutation runner: every case that caught a fault. */
        printf("GOPSEL FAILED CASES:");
        for (unsigned i = 0; i < g_failed_count; i++)
            printf("%s%s", i ? " | " : " ", g_failed_names[i]);
        printf("\n");
    }
}
