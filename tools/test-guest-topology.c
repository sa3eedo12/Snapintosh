/* SPDX-License-Identifier: MIT */
/* Host test for compat/topology.c: guest vCPU placement, GICv3/v4 redistributor
 * discovery and the PPI helpers. Built and run by tools/test-guest-topology.py.
 * This file holds the group bookkeeping, the placement, identity and PPI groups
 * and the command line; the redistributor discovery groups live in
 * tools/test-guest-topology-gicr.c and are linked into the same binary.
 *
 * Every expectation is a hand-derived literal or an independent formula, never
 * a call to the function under test. The redistributor images are synthetic
 * EXPERIMENTAL stand-ins and say nothing about real SP11 silicon.
 *
 * The same binary has a small command line that the Python test uses to compare
 * the model with the live host tools:
 *   test-guest-topology --place <a16|sp11> <pack|spread> <primary> <count> <fair>
 *   test-guest-topology --assign <a16|sp11> <pack|spread> <primary> <count> <fair>
 *       <gic-version> <stride> <base> <limit> <affinity>@<redistributor>[,...]
 *   test-guest-topology --vmpidr <slot> */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "test-guest-topology.h"

struct group_record {
    const char *name;
    unsigned failures;
};

unsigned checks, failures;
static struct group_record records[128];
static unsigned record_count;
static struct group_record *current;
static int trace_groups;

/* With TOPOLOGY_TEST_TRACE set, every new group is announced on stderr before
 * it runs, so a mutant that crashes or hangs the harness is still attributed to
 * the group that was running. Without it a passing run prints nothing on stderr. */
void begin(const char *name)
{
    for (unsigned i = 0; i < record_count; i++)
        if (!strcmp(records[i].name, name)) {
            current = &records[i];
            return;
        }
    if (record_count == sizeof records / sizeof records[0]) {
        fprintf(stderr, "too many test groups\n");
        exit(2);
    }
    if (trace_groups)
        fprintf(stderr, "GROUP-BEGIN %s\n", name);
    records[record_count].name = name;
    records[record_count].failures = 0;
    current = &records[record_count++];
}

/* Only the first failures of a group are printed: a broken placement would
 * otherwise bury the group summary under thousands of lines. */
void fail(const char *file, int line, const char *text, int has_values,
          unsigned long long actual, unsigned long long expected)
{
    failures++;
    if (current->failures++ >= 3)
        return;
    if (has_values)
        fprintf(stderr, "FAIL [%s] %s:%d: %s (got %#llx, want %#llx)\n",
                current->name, file, line, text, actual, expected);
    else
        fprintf(stderr, "FAIL [%s] %s:%d: %s\n", current->name, file, line, text);
}

_Static_assert(TOPOLOGY_SP11_PHYSICAL_CPUS == 12u, "SP11 X1E80100 has 12 cores");
_Static_assert(TOPOLOGY_GICR_MAX_FRAMES >= 18u, "the A16 window holds 18 frames");

/* ---- machine tables written out by hand, independent of the profiles ---- */

struct machine sp11, a16;

static void init_machine(struct machine *m, const struct topology_profile *profile,
                         unsigned clusters, unsigned cluster_cores)
{
    m->profile = profile;
    m->clusters = clusters;
    m->cluster_cores = cluster_cores;
    m->cores = clusters * cluster_cores;
    m->max_vcpus = m->cores < TOPOLOGY_MAX_VCPUS ? m->cores : TOPOLOGY_MAX_VCPUS;
    for (unsigned c = 0; c < clusters; c++)
        for (unsigned k = 0; k < cluster_cores; k++)
            m->core[c * cluster_cores + k] = (uint64_t)c << 16 | (uint64_t)k << 8;
}

static int placement_is_clear(const struct topology_placement *p)
{
    unsigned i;
    if (p->count || p->cluster_span)
        return 0;
    for (i = 0; i < TOPOLOGY_MAX_VCPUS; i++)
        if (p->affinity[i] || p->cluster[i])
            return 0;
    for (i = 0; i < TOPOLOGY_MAX_CLUSTERS; i++)
        if (p->per_cluster[i])
            return 0;
    return 1;
}

static int placement_equal(const struct topology_placement *a, const struct topology_placement *b)
{
    unsigned i;
    if (a->count != b->count || a->cluster_span != b->cluster_span)
        return 0;
    for (i = 0; i < TOPOLOGY_MAX_VCPUS; i++)
        if (a->affinity[i] != b->affinity[i] || a->cluster[i] != b->cluster[i])
            return 0;
    for (i = 0; i < TOPOLOGY_MAX_CLUSTERS; i++)
        if (a->per_cluster[i] != b->per_cluster[i])
            return 0;
    return 1;
}

/* ---- profile and policy ---- */

static void test_profile_and_policy(void)
{
    struct topology_profile bad;
    struct topology_policy policy;

    begin("profile");
    CHECK_EQ(topology_profile_check(&topology_profile_a16), TOPOLOGY_OK);
    CHECK_EQ(topology_profile_check(&topology_profile_sp11), TOPOLOGY_OK);
    CHECK_EQ(topology_profile_cores(&topology_profile_a16), 18);
    CHECK_EQ(topology_profile_cores(&topology_profile_sp11), 12);
    CHECK_EQ(topology_profile_cores(NULL), 0);
    CHECK_EQ(topology_profile_check(NULL), TOPOLOGY_BAD_ARGUMENT);
    CHECK_EQ(topology_profile_a16.hyp_timer_intid, 26);
    CHECK_EQ(topology_profile_sp11.hyp_timer_intid, 26);

#define EXPECT_BAD_PROFILE(edit) do { \
    bad = topology_profile_sp11; \
    edit; \
    CHECK_EQ(topology_profile_check(&bad), TOPOLOGY_BAD_PROFILE); \
} while (0)
    EXPECT_BAD_PROFILE(bad.cluster_count = 0);
    EXPECT_BAD_PROFILE(bad.cluster_count = TOPOLOGY_MAX_CLUSTERS + 1);
    EXPECT_BAD_PROFILE(bad.hyp_timer_intid = 15);
    EXPECT_BAD_PROFILE(bad.hyp_timer_intid = 32);
    EXPECT_BAD_PROFILE(bad.hyp_timer_intid = 0);
    EXPECT_BAD_PROFILE(bad.cluster[1].cores = 0);
    EXPECT_BAD_PROFILE(bad.cluster[1].cores = TOPOLOGY_MAX_CLUSTER_CORES + 1);
    EXPECT_BAD_PROFILE(bad.cluster[2].core_step = 0);
    /* 0x8000 steps reach 0x10000 on the third core: Aff1 carries into Aff2. */
    EXPECT_BAD_PROFILE(bad.cluster[0].core_step = 0x8000);
    EXPECT_BAD_PROFILE(bad.cluster[1].first_affinity = 0x0);
    EXPECT_BAD_PROFILE(bad.cluster[2].first_affinity = 0x10000);
    EXPECT_BAD_PROFILE(bad.cluster[0].first_affinity = UINT64_C(0x80000000));
    EXPECT_BAD_PROFILE(bad.cluster[1].first_affinity = UINT64_C(0x1010000));
    EXPECT_BAD_PROFILE(bad.cluster[0].core_step = UINT64_MAX);
#undef EXPECT_BAD_PROFILE

    begin("policy");
    CHECK_EQ(topology_policy_check(&topology_policy_pack), TOPOLOGY_OK);
    CHECK_EQ(topology_policy_check(&topology_policy_spread), TOPOLOGY_OK);
    CHECK_EQ(topology_policy_check(NULL), TOPOLOGY_BAD_ARGUMENT);
    /* The shipped limit is the v26 lesson: one cluster until a fair lock exists. */
    CHECK_EQ(topology_policy_pack.max_span_unfair, 1);
    CHECK_EQ(topology_policy_spread.max_span_unfair, 1);
    CHECK_EQ(topology_policy_pack.order, TOPOLOGY_PACK);
    CHECK_EQ(topology_policy_spread.order, TOPOLOGY_SPREAD);
    policy = topology_policy_pack;
    policy.order = 0;
    CHECK_EQ(topology_policy_check(&policy), TOPOLOGY_BAD_POLICY);
    policy.order = 3;
    CHECK_EQ(topology_policy_check(&policy), TOPOLOGY_BAD_POLICY);
    policy = topology_policy_pack;
    policy.max_span_unfair = 0;
    CHECK_EQ(topology_policy_check(&policy), TOPOLOGY_BAD_POLICY);
    policy.max_span_unfair = TOPOLOGY_MAX_CLUSTERS + 1;
    CHECK_EQ(topology_policy_check(&policy), TOPOLOGY_BAD_POLICY);
    policy.max_span_unfair = UINT32_MAX;
    CHECK_EQ(topology_policy_check(&policy), TOPOLOGY_BAD_POLICY);
}

/* ---- placement ---- */

static unsigned expected_span(const struct machine *m, int spread, unsigned n)
{
    if (spread)
        return n < m->clusters ? n : m->clusters;
    return (n + m->cluster_cores - 1) / m->cluster_cores;
}

static void verify_placement(const struct machine *m, const struct topology_placement *p,
                             uint64_t primary, unsigned n, int spread)
{
    unsigned per[TOPOLOGY_MAX_CLUSTERS] = {0}, span = 0, lo = ~0u, hi = 0, i, j, c;
    unsigned primary_cluster = (unsigned)((primary >> 16) & 0xff);

    CHECK_EQ(p->count, n);
    CHECK_EQ(p->affinity[0], primary);
    for (i = 0; i < n && i < TOPOLOGY_MAX_VCPUS; i++) {
        unsigned cluster = (unsigned)((p->affinity[i] >> 16) & 0xff);
        int known = 0;
        for (j = 0; j < m->cores; j++)
            known |= m->core[j] == p->affinity[i];
        CHECK(known);
        CHECK_EQ(p->cluster[i], cluster);
        if (cluster < TOPOLOGY_MAX_CLUSTERS)
            per[cluster]++;
        for (j = 0; j < i; j++)
            CHECK(p->affinity[j] != p->affinity[i]);
    }
    for (c = 0; c < TOPOLOGY_MAX_CLUSTERS; c++) {
        CHECK_EQ(p->per_cluster[c], per[c]);
        if (per[c])
            span++;
    }
    CHECK_EQ(p->cluster_span, span);
    CHECK_EQ(span, expected_span(m, spread, n));
    if (spread) {
        for (c = 0; c < m->clusters; c++) {
            lo = per[c] < lo ? per[c] : lo;
            hi = per[c] > hi ? per[c] : hi;
        }
        CHECK(hi - lo <= 1);
    } else {
        CHECK_EQ(per[primary_cluster], n < m->cluster_cores ? n : m->cluster_cores);
    }
    for (i = 2; i < n && i < TOPOLOGY_MAX_VCPUS; i++)
        if (p->cluster[i] == p->cluster[i - 1])
            CHECK(p->affinity[i] > p->affinity[i - 1]);
}

static void test_placement_matrix(void)
{
    const struct machine *machines[2] = {&sp11, &a16};

    begin("placement-matrix");
    for (unsigned mi = 0; mi < 2; mi++) {
        const struct machine *m = machines[mi];
        for (unsigned spread = 0; spread < 2; spread++) {
            const struct topology_policy *policy =
                spread ? &topology_policy_spread : &topology_policy_pack;
            for (unsigned pi = 0; pi < m->cores; pi++)
                for (unsigned n = 1; n <= m->max_vcpus; n++)
                    for (int fair = 0; fair < 2; fair++) {
                        struct topology_placement p, again;
                        enum topology_status want =
                            expected_span(m, (int)spread, n) > 1 && !fair
                                ? TOPOLOGY_NEEDS_FAIR_LOCK : TOPOLOGY_OK;
                        poison(&p, sizeof p);
                        CHECK_EQ(topology_place(m->profile, policy, n, m->core[pi], fair, &p), want);
                        if (want != TOPOLOGY_OK) {
                            CHECK(placement_is_clear(&p));
                            continue;
                        }
                        verify_placement(m, &p, m->core[pi], n, (int)spread);
                        CHECK_EQ(topology_check_placement(m->profile, policy, m->core[pi], fair, &p),
                                 TOPOLOGY_OK);
                        CHECK_EQ(topology_place(m->profile, policy, n, m->core[pi], fair, &again),
                                 TOPOLOGY_OK);
                        CHECK(placement_equal(&p, &again));
                    }
        }
    }
}

static void expect_vector(const struct topology_profile *profile,
                          const struct topology_policy *policy, uint64_t primary,
                          unsigned n, int fair, const uint64_t *want)
{
    struct topology_placement p;
    CHECK_EQ(topology_place(profile, policy, n, primary, fair, &p), TOPOLOGY_OK);
    CHECK_EQ(p.count, n);
    for (unsigned i = 0; i < n; i++)
        CHECK_EQ(p.affinity[i], want[i]);
}

static void expect_refusal(const struct topology_profile *profile,
                           const struct topology_policy *policy, uint64_t primary,
                           unsigned n, int fair, enum topology_status want)
{
    struct topology_placement p;
    poison(&p, sizeof p);
    CHECK_EQ(topology_place(profile, policy, n, primary, fair, &p), want);
    CHECK(placement_is_clear(&p));
}

static void test_sp11_vectors(void)
{
    static const uint64_t pack0_4[] = {0x0, 0x100, 0x200, 0x300};
    static const uint64_t pack0_6[] = {0x0, 0x100, 0x200, 0x300, 0x10000, 0x10100};
    static const uint64_t pack_mid_4[] = {0x10100, 0x10000, 0x10200, 0x10300};
    static const uint64_t pack_c2_8[] = {0x20000, 0x20100, 0x20200, 0x20300, 0x0, 0x100, 0x200, 0x300};
    static const uint64_t pack_last_5[] = {0x20300, 0x20000, 0x20100, 0x20200, 0x0};
    static const uint64_t pack_c1_12[] = {0x10300, 0x10000, 0x10100, 0x10200,
                                          0x20000, 0x20100, 0x20200, 0x20300,
                                          0x0, 0x100, 0x200, 0x300};
    static const uint64_t spread0_6[] = {0x0, 0x10000, 0x20000, 0x100, 0x10100, 0x20100};
    static const uint64_t spread_c1_5[] = {0x10000, 0x20000, 0x0, 0x10100, 0x20100};
    static const uint64_t one[] = {0x0};
    static const uint64_t two[] = {0x0, 0x10000};
    const struct topology_profile *p = &topology_profile_sp11;

    begin("sp11-vectors");
    expect_vector(p, &topology_policy_pack, 0x0, 4, 0, pack0_4);
    expect_vector(p, &topology_policy_pack, 0x0, 6, 1, pack0_6);
    expect_vector(p, &topology_policy_pack, 0x10100, 4, 0, pack_mid_4);
    expect_vector(p, &topology_policy_pack, 0x20000, 8, 1, pack_c2_8);
    expect_vector(p, &topology_policy_pack, 0x20300, 5, 1, pack_last_5);
    expect_vector(p, &topology_policy_pack, 0x10300, 12, 1, pack_c1_12);
    expect_vector(p, &topology_policy_spread, 0x0, 6, 1, spread0_6);
    expect_vector(p, &topology_policy_spread, 0x10000, 5, 1, spread_c1_5);
    expect_vector(p, &topology_policy_spread, 0x0, 1, 0, one);
    expect_vector(p, &topology_policy_spread, 0x0, 2, 1, two);
    /* Without a fair lock only a single cluster is allowed (v26). */
    expect_refusal(p, &topology_policy_pack, 0x0, 5, 0, TOPOLOGY_NEEDS_FAIR_LOCK);
    expect_refusal(p, &topology_policy_pack, 0x20300, 6, 0, TOPOLOGY_NEEDS_FAIR_LOCK);
    expect_refusal(p, &topology_policy_pack, 0x0, 12, 0, TOPOLOGY_NEEDS_FAIR_LOCK);
    expect_refusal(p, &topology_policy_spread, 0x0, 2, 0, TOPOLOGY_NEEDS_FAIR_LOCK);
    expect_refusal(p, &topology_policy_spread, 0x10000, 3, 0, TOPOLOGY_NEEDS_FAIR_LOCK);
}

static void test_a16_bit_exact(void)
{
    /* tools/secondary-runtime.py: targets[slot] = slot * 0x100, or with
     * SNAP_SMP_PRIME 0x10000 + slot * 0x100 and vCPU 0 on 0x10000. */
    static const uint64_t cluster0[] = {0x0, 0x100, 0x200, 0x300, 0x400, 0x500};
    static const uint64_t cluster1[] = {0x10000, 0x10100, 0x10200, 0x10300, 0x10400, 0x10500};
    static const uint64_t seven[] = {0x0, 0x100, 0x200, 0x300, 0x400, 0x500, 0x10000};
    static const uint64_t three_spread[] = {0x0, 0x10000, 0x20000};
    const struct topology_profile *p = &topology_profile_a16;
    struct topology_placement placement;

    begin("a16-bit-exact");
    for (unsigned n = 1; n <= 6; n++) {
        expect_vector(p, &topology_policy_pack, 0x0, n, 0, cluster0);
        expect_vector(p, &topology_policy_pack, 0x10000, n, 0, cluster1);
        expect_vector(p, &topology_policy_pack, 0x0, n, 1, cluster0);
    }
    expect_refusal(p, &topology_policy_pack, 0x0, 7, 0, TOPOLOGY_NEEDS_FAIR_LOCK);
    expect_vector(p, &topology_policy_pack, 0x0, 7, 1, seven);
    expect_vector(p, &topology_policy_spread, 0x0, 3, 1, three_spread);
    expect_refusal(p, &topology_policy_pack, 0x600, 6, 0, TOPOLOGY_BAD_PRIMARY_AFFINITY);
    CHECK_EQ(topology_place(p, &topology_policy_pack, 6, 0x500, 0, &placement), TOPOLOGY_OK);
    CHECK_EQ(placement.affinity[0], 0x500);
    CHECK_EQ(placement.affinity[1], 0x0);

    /* test_refuse_a_layout_spanning_clusters in tools/test-secondary-runtime.py:
     * q1n1 on a Prime core, default secondaries on cluster 0. */
    memset(&placement, 0, sizeof placement);
    placement.count = 6;
    placement.cluster_span = 2;
    placement.affinity[0] = 0x10000;
    placement.cluster[0] = 1;
    for (unsigned i = 1; i < 6; i++)
        placement.affinity[i] = (uint64_t)i * 0x100;
    placement.per_cluster[0] = 5;
    placement.per_cluster[1] = 1;
    CHECK_EQ(topology_check_placement(p, &topology_policy_pack, 0x10000, 0, &placement),
             TOPOLOGY_NEEDS_FAIR_LOCK);
    CHECK_EQ(topology_check_placement(p, &topology_policy_spread, 0x10000, 0, &placement),
             TOPOLOGY_NEEDS_FAIR_LOCK);
    /* A fair lock lifts the span refusal but this still is not a pack or spread layout. */
    CHECK_EQ(topology_check_placement(p, &topology_policy_pack, 0x10000, 1, &placement),
             TOPOLOGY_BAD_PLACEMENT);
    CHECK_EQ(topology_check_placement(p, &topology_policy_spread, 0x10000, 1, &placement),
             TOPOLOGY_BAD_PLACEMENT);
}

static void test_refusals(void)
{
    /* Not a core of either machine. */
    static const uint64_t bad_primaries[] = {0x600, 0x10600, 0x30000, 0x80000000, 0x1,
                                             UINT64_C(0x300000000), UINT64_C(0xff00ffffff),
                                             UINT64_MAX};
    /* Cores 4 and 5 of a cluster exist on the A16 only. */
    static const uint64_t a16_only_primaries[] = {0x400, 0x500, 0x10400, 0x20500};
    struct topology_placement p;
    struct topology_profile bad_profile = topology_profile_sp11;
    struct topology_policy bad_policy = topology_policy_pack;

    begin("refusals");
    for (unsigned s = 0; s < 2; s++) {
        const struct topology_policy *policy = s ? &topology_policy_spread : &topology_policy_pack;
        expect_refusal(&topology_profile_sp11, policy, 0x0, 0, 1, TOPOLOGY_ZERO_VCPUS);
        expect_refusal(&topology_profile_sp11, policy, 0x0, 13, 1, TOPOLOGY_OVERSUBSCRIBED);
        expect_refusal(&topology_profile_sp11, policy, 0x0, 16, 1, TOPOLOGY_OVERSUBSCRIBED);
        expect_refusal(&topology_profile_sp11, policy, 0x0, 17, 1, TOPOLOGY_TOO_MANY_VCPUS);
        expect_refusal(&topology_profile_sp11, policy, 0x0, UINT32_MAX, 1, TOPOLOGY_TOO_MANY_VCPUS);
        expect_refusal(&topology_profile_a16, policy, 0x0, 17, 1, TOPOLOGY_TOO_MANY_VCPUS);
        expect_refusal(&topology_profile_a16, policy, 0x0, 0, 0, TOPOLOGY_ZERO_VCPUS);
        for (unsigned i = 0; i < sizeof bad_primaries / sizeof bad_primaries[0]; i++) {
            expect_refusal(&topology_profile_sp11, policy, bad_primaries[i], 4, 1,
                           TOPOLOGY_BAD_PRIMARY_AFFINITY);
            expect_refusal(&topology_profile_a16, policy, bad_primaries[i], 4, 1,
                           TOPOLOGY_BAD_PRIMARY_AFFINITY);
        }
        for (unsigned i = 0; i < sizeof a16_only_primaries / sizeof a16_only_primaries[0]; i++) {
            expect_refusal(&topology_profile_sp11, policy, a16_only_primaries[i], 4, 1,
                           TOPOLOGY_BAD_PRIMARY_AFFINITY);
            CHECK_EQ(topology_place(&topology_profile_a16, policy, 4, a16_only_primaries[i], 1, &p),
                     TOPOLOGY_OK);
            CHECK_EQ(p.affinity[0], a16_only_primaries[i]);
        }
        bad_profile.cluster[1].cores = 0;
        expect_refusal(&bad_profile, policy, 0x0, 4, 1, TOPOLOGY_BAD_PROFILE);
        bad_profile = topology_profile_sp11;
        bad_profile.hyp_timer_intid = 40;
        expect_refusal(&bad_profile, policy, 0x0, 4, 1, TOPOLOGY_BAD_PROFILE);
        bad_profile = topology_profile_sp11;
    }
    bad_policy.order = 9;
    expect_refusal(&topology_profile_sp11, &bad_policy, 0x0, 4, 1, TOPOLOGY_BAD_POLICY);
    bad_policy = topology_policy_pack;
    bad_policy.max_span_unfair = 0;
    expect_refusal(&topology_profile_sp11, &bad_policy, 0x0, 4, 1, TOPOLOGY_BAD_POLICY);

    CHECK_EQ(topology_place(NULL, &topology_policy_pack, 4, 0x0, 1, &p), TOPOLOGY_BAD_ARGUMENT);
    CHECK_EQ(topology_place(&topology_profile_sp11, NULL, 4, 0x0, 1, &p), TOPOLOGY_BAD_ARGUMENT);
    CHECK_EQ(topology_place(&topology_profile_sp11, &topology_policy_pack, 4, 0x0, 1, NULL),
             TOPOLOGY_BAD_ARGUMENT);

    /* A policy that tolerates two clusters admits the same layouts the fair lock does. */
    bad_policy = topology_policy_pack;
    bad_policy.max_span_unfair = 2;
    CHECK_EQ(topology_place(&topology_profile_sp11, &bad_policy, 8, 0x0, 0, &p), TOPOLOGY_OK);
    CHECK_EQ(topology_place(&topology_profile_sp11, &bad_policy, 9, 0x0, 0, &p),
             TOPOLOGY_NEEDS_FAIR_LOCK);
}

static void test_corrupted_placements(void)
{
    struct topology_placement good, p;
    const struct topology_profile *sp = &topology_profile_sp11;
    const struct topology_policy *pack = &topology_policy_pack, *spread = &topology_policy_spread;
    uint64_t tmp;

#define CHECK_PACK(pl, primary, fair) topology_check_placement(sp, pack, primary, fair, &(pl))
#define CHECK_SPREAD(pl, primary, fair) topology_check_placement(sp, spread, primary, fair, &(pl))

    begin("check-placement");
    CHECK_EQ(topology_place(sp, pack, 6, 0x0, 1, &good), TOPOLOGY_OK);
    CHECK_EQ(CHECK_PACK(good, 0x0, 1), TOPOLOGY_OK);

    p = good;
    tmp = p.affinity[0];
    p.affinity[0] = p.affinity[1];
    p.affinity[1] = tmp;
    CHECK_EQ(CHECK_PACK(p, 0x0, 1), TOPOLOGY_BAD_PLACEMENT);       /* slot 0 is not the primary */

    p = good;
    p.affinity[2] = p.affinity[1];
    CHECK_EQ(CHECK_PACK(p, 0x0, 1), TOPOLOGY_BAD_PLACEMENT);       /* duplicate core */

    p = good;
    p.affinity[3] = 0x400;
    CHECK_EQ(CHECK_PACK(p, 0x0, 1), TOPOLOGY_BAD_PLACEMENT);       /* not a core of the profile */

    p = good;
    p.cluster[2] = 1;
    CHECK_EQ(CHECK_PACK(p, 0x0, 1), TOPOLOGY_BAD_PLACEMENT);       /* wrong cluster index */

    p = good;
    p.per_cluster[0]++;
    CHECK_EQ(CHECK_PACK(p, 0x0, 1), TOPOLOGY_BAD_PLACEMENT);       /* stale per-cluster count */

    p = good;
    p.per_cluster[2] = 1;
    CHECK_EQ(CHECK_PACK(p, 0x0, 1), TOPOLOGY_BAD_PLACEMENT);

    p = good;
    p.cluster_span = 3;
    CHECK_EQ(CHECK_PACK(p, 0x0, 1), TOPOLOGY_BAD_PLACEMENT);       /* stale span */

    p = good;
    p.cluster_span = 1;
    CHECK_EQ(CHECK_PACK(p, 0x0, 1), TOPOLOGY_BAD_PLACEMENT);

    p = good;
    p.count = 7;                                                   /* seventh entry is core 0 again */
    CHECK_EQ(CHECK_PACK(p, 0x0, 1), TOPOLOGY_BAD_PLACEMENT);

    p = good;
    p.count = 5;
    CHECK_EQ(CHECK_PACK(p, 0x0, 1), TOPOLOGY_BAD_PLACEMENT);

    p = good;
    p.count = 0;
    CHECK_EQ(CHECK_PACK(p, 0x0, 1), TOPOLOGY_ZERO_VCPUS);
    p.count = 13;
    CHECK_EQ(CHECK_PACK(p, 0x0, 1), TOPOLOGY_OVERSUBSCRIBED);
    p.count = TOPOLOGY_MAX_VCPUS + 1;
    CHECK_EQ(CHECK_PACK(p, 0x0, 1), TOPOLOGY_TOO_MANY_VCPUS);

    CHECK_EQ(CHECK_PACK(good, 0x100, 1), TOPOLOGY_BAD_PLACEMENT);  /* another core as primary */
    CHECK_EQ(CHECK_PACK(good, 0x400, 1), TOPOLOGY_BAD_PRIMARY_AFFINITY);

    /* Core 2 of cluster 0 is skipped: the vCPUs do not form a gap-free prefix. */
    memset(&p, 0, sizeof p);
    p.count = 3;
    p.cluster_span = 1;
    p.affinity[0] = 0x0;
    p.affinity[1] = 0x100;
    p.affinity[2] = 0x300;
    p.per_cluster[0] = 3;
    CHECK_EQ(CHECK_PACK(p, 0x0, 0), TOPOLOGY_BAD_PLACEMENT);
    CHECK_EQ(CHECK_SPREAD(p, 0x0, 0), TOPOLOGY_BAD_PLACEMENT);

    /* Same cores in descending order. */
    p.affinity[1] = 0x200;
    p.affinity[2] = 0x100;
    CHECK_EQ(CHECK_PACK(p, 0x0, 0), TOPOLOGY_BAD_PLACEMENT);

    /* Pack: the second cluster is entered while the first still has free cores. */
    memset(&p, 0, sizeof p);
    p.count = 3;
    p.cluster_span = 2;
    p.affinity[0] = 0x0;
    p.affinity[1] = 0x100;
    p.affinity[2] = 0x10000;
    p.cluster[2] = 1;
    p.per_cluster[0] = 2;
    p.per_cluster[1] = 1;
    CHECK_EQ(CHECK_PACK(p, 0x0, 1), TOPOLOGY_BAD_PLACEMENT);

    /* Pack: clusters are not visited in order, even though each is gap-free. */
    memset(&p, 0, sizeof p);
    p.count = 5;
    p.cluster_span = 2;
    p.affinity[0] = 0x0;
    p.affinity[1] = 0x10000;
    p.affinity[2] = 0x100;
    p.affinity[3] = 0x200;
    p.affinity[4] = 0x300;
    p.cluster[1] = 1;
    p.per_cluster[0] = 4;
    p.per_cluster[1] = 1;
    CHECK_EQ(CHECK_PACK(p, 0x0, 1), TOPOLOGY_BAD_PLACEMENT);

    /* Spread: three vCPUs in cluster 0 while cluster 2 is empty. */
    memset(&p, 0, sizeof p);
    p.count = 4;
    p.cluster_span = 2;
    p.affinity[0] = 0x0;
    p.affinity[1] = 0x100;
    p.affinity[2] = 0x200;
    p.affinity[3] = 0x10000;
    p.cluster[3] = 1;
    p.per_cluster[0] = 3;
    p.per_cluster[1] = 1;
    CHECK_EQ(CHECK_SPREAD(p, 0x0, 1), TOPOLOGY_BAD_PLACEMENT);

    /* A valid spread is not a valid pack and the other way round. */
    CHECK_EQ(topology_place(sp, spread, 6, 0x0, 1, &p), TOPOLOGY_OK);
    CHECK_EQ(CHECK_SPREAD(p, 0x0, 1), TOPOLOGY_OK);
    CHECK_EQ(CHECK_PACK(p, 0x0, 1), TOPOLOGY_BAD_PLACEMENT);
    CHECK_EQ(CHECK_SPREAD(good, 0x0, 1), TOPOLOGY_BAD_PLACEMENT);
    CHECK_EQ(CHECK_PACK(good, 0x0, 0), TOPOLOGY_NEEDS_FAIR_LOCK);

    CHECK_EQ(topology_check_placement(NULL, pack, 0x0, 1, &good), TOPOLOGY_BAD_ARGUMENT);
    CHECK_EQ(topology_check_placement(sp, NULL, 0x0, 1, &good), TOPOLOGY_BAD_ARGUMENT);
    CHECK_EQ(topology_check_placement(sp, pack, 0x0, 1, NULL), TOPOLOGY_BAD_ARGUMENT);
    {
        struct topology_profile bad = topology_profile_sp11;
        struct topology_policy bad_policy = topology_policy_pack;
        bad.cluster[0].cores = 0;
        CHECK_EQ(topology_check_placement(&bad, pack, 0x0, 1, &good), TOPOLOGY_BAD_PROFILE);
        bad_policy.max_span_unfair = 0;
        CHECK_EQ(topology_check_placement(sp, &bad_policy, 0x0, 1, &good), TOPOLOGY_BAD_POLICY);
    }
#undef CHECK_PACK
#undef CHECK_SPREAD
}

/* Corrupting any single field of a valid placement must make the checker refuse
 * it. The explicit cases above name the interesting ones; this sweep rules out
 * the combinations nobody thought of, including cluster indexes outside the
 * profile, which must be refused before they are used as array subscripts.
 * Cross-cluster slot swaps are only expected to fail for pack: the spread shape
 * the checker verifies is balance plus per-cluster order, not the slot sequence. */
static void test_single_field_corruption(void)
{
    static const uint32_t wrong_index[] = {0, 1, 2, 3, 4, 7, UINT32_MAX};
    const struct machine *machines[2] = {&sp11, &a16};

    begin("check-placement/field-corruption");
    for (unsigned mi = 0; mi < 2; mi++) {
        const struct machine *m = machines[mi];
        for (unsigned spread = 0; spread < 2; spread++) {
            const struct topology_policy *policy =
                spread ? &topology_policy_spread : &topology_policy_pack;
            for (unsigned pi = 0; pi < m->cores; pi++)
                for (unsigned n = 1; n <= m->max_vcpus; n++) {
                    const uint64_t primary = m->core[pi];
#define CHECK_CORRUPT(pl) CHECK_EQ(topology_check_placement(m->profile, policy, primary, 1, &(pl)), \
                                   TOPOLOGY_BAD_PLACEMENT)
                    struct topology_placement good, p;

                    CHECK_EQ(topology_place(m->profile, policy, n, primary, 1, &good), TOPOLOGY_OK);
                    CHECK_EQ(topology_check_placement(m->profile, policy, primary, 1, &good),
                             TOPOLOGY_OK);
                    for (unsigned i = 0; i < n; i++) {
                        for (unsigned w = 0; w < sizeof wrong_index / sizeof wrong_index[0]; w++) {
                            if (wrong_index[w] == good.cluster[i])
                                continue;
                            p = good;
                            p.cluster[i] = wrong_index[w];
                            CHECK_CORRUPT(p);
                        }
                        for (unsigned x = 0; x < m->cores; x++) {
                            if (m->core[x] == good.affinity[i])
                                continue;
                            p = good;
                            p.affinity[i] = m->core[x];
                            CHECK_CORRUPT(p);
                        }
                        p = good;
                        p.affinity[i] |= 0x1;                       /* not a core of any profile */
                        CHECK_CORRUPT(p);
                        p = good;
                        p.affinity[i] ^= UINT64_C(1) << 32;
                        CHECK_CORRUPT(p);
                        p = good;
                        p.affinity[i] = UINT64_MAX;
                        CHECK_CORRUPT(p);
                    }
                    for (unsigned c = 0; c < TOPOLOGY_MAX_CLUSTERS; c++) {
                        p = good;
                        p.per_cluster[c]++;
                        CHECK_CORRUPT(p);
                        if (good.per_cluster[c]) {
                            p = good;
                            p.per_cluster[c]--;
                            CHECK_CORRUPT(p);
                        }
                        p = good;
                        p.per_cluster[c] = UINT32_MAX;
                        CHECK_CORRUPT(p);
                    }
                    p = good;
                    p.cluster_span++;
                    CHECK_CORRUPT(p);
                    p = good;
                    p.cluster_span = UINT32_MAX;
                    CHECK_CORRUPT(p);
                    if (good.cluster_span > 1) {
                        p = good;
                        p.cluster_span--;
                        CHECK_CORRUPT(p);
                    }
                    if (n < m->max_vcpus) {                         /* an extra, unaccounted slot */
                        p = good;
                        p.count = n + 1;
                        CHECK_CORRUPT(p);
                    }
                    if (n > 1) {                                    /* a slot dropped from the count only */
                        p = good;
                        p.count = n - 1;
                        CHECK_CORRUPT(p);
                    }
                    for (unsigned i = 1; i < n; i++)
                        for (unsigned j = i + 1; j < n; j++) {
                            if (spread && good.cluster[i] != good.cluster[j])
                                continue;
                            p = good;
                            p.affinity[i] = good.affinity[j];
                            p.affinity[j] = good.affinity[i];
                            p.cluster[i] = good.cluster[j];
                            p.cluster[j] = good.cluster[i];
                            CHECK_CORRUPT(p);
                        }
#undef CHECK_CORRUPT
                }
        }
    }
}

/* ---- guest numbering and PPI helpers ---- */

static void test_guest_vmpidr(void)
{
    /* probes/xnuboot/setup.c: four efficiency cores at 0x0-0x3, two performance
     * cores at 0x100-0x101, bit 31 set. */
    static const uint64_t want[6] = {0x80000000, 0x80000001, 0x80000002, 0x80000003,
                                     0x80000100, 0x80000101};
    begin("guest-vmpidr");
    for (unsigned i = 0; i < 6; i++)
        CHECK_EQ(topology_guest_vmpidr(i), want[i]);
    CHECK_EQ(topology_guest_vmpidr(6), 0);
    CHECK_EQ(topology_guest_vmpidr(7), 0);
    CHECK_EQ(topology_guest_vmpidr(UINT32_MAX), 0);
}

static void test_ppi_helpers(void)
{
    static const uint32_t not_ppi[] = {0, 15, 32, 1019, 1020, 1056, 1119, UINT32_MAX};

    begin("ppi");
    CHECK(topology_intid_is_ppi(16));
    CHECK(topology_intid_is_ppi(26));
    CHECK(topology_intid_is_ppi(31));
    for (unsigned i = 0; i < sizeof not_ppi / sizeof not_ppi[0]; i++) {
        CHECK(!topology_intid_is_ppi(not_ppi[i]));
        CHECK_EQ(topology_ppi_bit(not_ppi[i]), 0);
        CHECK_EQ(topology_ppi_icfgr1_edge_bit(not_ppi[i]), 0);
    }
    /* probes/secondary.c on the A16: 1U << 26 and ICFGR1 bit 21 for INTID 26. */
    CHECK_EQ(topology_ppi_bit(26), UINT32_C(0x04000000));
    CHECK_EQ(topology_ppi_icfgr1_edge_bit(26), UINT32_C(0x00200000));
    CHECK_EQ(topology_ppi_bit(16), UINT32_C(0x00010000));
    CHECK_EQ(topology_ppi_bit(31), UINT32_C(0x80000000));
    CHECK_EQ(topology_ppi_icfgr1_edge_bit(16), UINT32_C(0x2));
    CHECK_EQ(topology_ppi_icfgr1_edge_bit(17), UINT32_C(0x8));
    CHECK_EQ(topology_ppi_icfgr1_edge_bit(31), UINT32_C(0x80000000));
}

/* ---- command line used by the Python test ---- */

static const struct topology_profile *profile_by_name(const char *name)
{
    if (!strcmp(name, "a16"))
        return &topology_profile_a16;
    if (!strcmp(name, "sp11"))
        return &topology_profile_sp11;
    return NULL;
}

static const struct topology_policy *policy_by_name(const char *name)
{
    if (!strcmp(name, "pack"))
        return &topology_policy_pack;
    if (!strcmp(name, "spread"))
        return &topology_policy_spread;
    return NULL;
}

int parse_number(const char *text, uint64_t *value)
{
    char *end = NULL;
    *value = strtoull(text, &end, 0);
    return text[0] && end && !*end;
}

static int cli_usage(void)
{
    fprintf(stderr, "usage error\n");
    return 2;
}

static int cli(int argc, char **argv)
{
    const struct topology_profile *profile;
    const struct topology_policy *policy;
    struct topology_placement p;
    uint64_t primary, count, fair;
    enum topology_status st;
    int assign;

    if (argc == 3 && !strcmp(argv[1], "--vmpidr")) {
        if (!parse_number(argv[2], &primary) || primary > UINT32_MAX)
            return cli_usage();
        printf("vmpidr=%#llx\n", (unsigned long long)topology_guest_vmpidr((uint32_t)primary));
        return 0;
    }
    if (argc < 7)
        return cli_usage();
    assign = !strcmp(argv[1], "--assign");
    if ((!assign && strcmp(argv[1], "--place")) || (assign ? argc != 12 : argc != 7) ||
        !(profile = profile_by_name(argv[2])) || !(policy = policy_by_name(argv[3])) ||
        !parse_number(argv[4], &primary) || !parse_number(argv[5], &count) ||
        !parse_number(argv[6], &fair) || count > UINT32_MAX)
        return cli_usage();
    st = topology_place(profile, policy, (uint32_t)count, primary, fair != 0, &p);
    printf("place=%d count=%u\n", (int)st, p.count);
    for (unsigned i = 0; i < p.count; i++)
        printf("slot %u %#llx %u\n", i, (unsigned long long)p.affinity[i], p.cluster[i]);
    if (assign && st == TOPOLOGY_OK)
        return gicr_cli_assign(&p, argv);
    return st == TOPOLOGY_OK ? 0 : 1;
}

int main(int argc, char **argv)
{
    if (argc > 1)
        return cli(argc, argv);
    trace_groups = getenv("TOPOLOGY_TEST_TRACE") != NULL;
    init_machine(&sp11, &topology_profile_sp11, 3, 4);
    init_machine(&a16, &topology_profile_a16, 3, 6);
    test_profile_and_policy();
    test_placement_matrix();
    test_sp11_vectors();
    test_a16_bit_exact();
    test_refusals();
    test_corrupted_placements();
    test_single_field_corruption();
    test_guest_vmpidr();
    test_ppi_helpers();
    test_gicr_groups();
    for (unsigned i = 0; i < record_count; i++)
        if (records[i].failures)
            fprintf(stderr, "GROUP-FAIL %s %u\n", records[i].name, records[i].failures);
    printf("test-guest-topology: %u checks, %u failures, %u groups\n", checks, failures, record_count);
    printf(failures ? "FAIL\n" : "PASS\n");
    return failures ? 1 : 0;
}
