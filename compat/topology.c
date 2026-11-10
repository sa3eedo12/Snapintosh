/* SPDX-License-Identifier: MIT */
#include "topology.h"

/* A16: three clusters of six, affinities 0x0-0x500, 0x10000-0x10500 and
 * 0x20000-0x20500. OBSERVED: the MADT inventory described in
 * tools/secondary-runtime.py gives cluster 0 six cores and clusters 1-2 twelve
 * Prime cores between them, and the monitor's redistributor window
 * (0x17080000-0x17500000 at stride 0x40000) holds exactly 18 frames. Only
 * clusters 0 and 1 are used by existing placement code. */
const struct topology_profile topology_profile_a16 = {
    3, 26,
    {{UINT64_C(0x00000), 0x100, 6},
     {UINT64_C(0x10000), 0x100, 6},
     {UINT64_C(0x20000), 0x100, 6}}
};

/* SP11: 3 x 4 Oryon v1, same Aff2 = cluster / Aff1 = core encoding. INFERRED
 * from the public upstream devicetree, Linux arch/arm64/boot/dts/qcom/hamoa.dtsi
 * (BSD-3-Clause; values only, none of its text is copied): cpu reg values
 * 0x0-0x300, 0x10000-0x10300 and 0x20000-0x20300, one L2 per cluster. The EL2
 * probe (X2) must read MPIDR on the unit before this is trusted. The hypervisor
 * timer is PPI 10 = INTID 26 there, also INFERRED. */
const struct topology_profile topology_profile_sp11 = {
    TOPOLOGY_SP11_CLUSTERS, 26,
    {{UINT64_C(0x00000), 0x100, TOPOLOGY_SP11_CLUSTER_CORES},
     {UINT64_C(0x10000), 0x100, TOPOLOGY_SP11_CLUSTER_CORES},
     {UINT64_C(0x20000), 0x100, TOPOLOGY_SP11_CLUSTER_CORES}}
};

/* Pack: the service lock is a single test-and-set word (no queue), and v26
 * showed a vCPU in a second cluster starving a remote waiter, so vCPUs stay in
 * one cluster until N forces a second, and any span above max_span_unfair
 * needs the queued lock. EXPERIMENTAL on SP11: the limit of 1 is inherited
 * from A16 measurements and must be re-measured (plan item X3). */
const struct topology_policy topology_policy_pack = {TOPOLOGY_PACK, 1};
/* Spread: one core per cluster in turn. With a queued lock every waiter is
 * served in order, so spreading only trades cross-cluster coherence traffic
 * for per-cluster thermal headroom; it exists so X3 can measure that choice. */
const struct topology_policy topology_policy_spread = {TOPOLOGY_SPREAD, 1};

int topology_intid_is_ppi(uint32_t intid)
{
    return intid >= 16u && intid <= 31u;
}

uint32_t topology_ppi_bit(uint32_t intid)
{
    return topology_intid_is_ppi(intid) ? UINT32_C(1) << intid : 0;
}

uint32_t topology_ppi_icfgr1_edge_bit(uint32_t intid)
{
    return topology_intid_is_ppi(intid) ? UINT32_C(1) << ((intid - 16u) * 2u + 1u) : 0;
}

uint64_t topology_core_affinity(const struct topology_cluster *cluster, uint32_t index)
{
    return cluster->first_affinity + (uint64_t)index * cluster->core_step;
}

uint32_t topology_profile_cores(const struct topology_profile *profile)
{
    uint32_t total = 0;
    if (!profile)
        return 0;
    for (uint32_t c = 0; c < profile->cluster_count && c < TOPOLOGY_MAX_CLUSTERS; c++)
        total += profile->cluster[c].cores;
    return total;
}

enum topology_status topology_profile_check(const struct topology_profile *profile)
{
    if (!profile)
        return TOPOLOGY_BAD_ARGUMENT;
    if (profile->cluster_count == 0 || profile->cluster_count > TOPOLOGY_MAX_CLUSTERS ||
        !topology_intid_is_ppi(profile->hyp_timer_intid))
        return TOPOLOGY_BAD_PROFILE;
    for (uint32_t c = 0; c < profile->cluster_count; c++) {
        const struct topology_cluster *cl = &profile->cluster[c];
        if (cl->cores == 0 || cl->cores > TOPOLOGY_MAX_CLUSTER_CORES ||
            cl->core_step == 0 || cl->core_step > TOPOLOGY_AFFINITY_MASK)
            return TOPOLOGY_BAD_PROFILE;
        /* Every core must stay inside the affinity bits and in the same cluster;
         * a carry from Aff1 into Aff2 would silently move a core to another
         * cluster and defeat the one-cluster policy. */
        for (uint32_t k = 0; k < cl->cores; k++) {
            uint64_t a = topology_core_affinity(cl, k);
            if ((a & ~TOPOLOGY_AFFINITY_MASK) ||
                ((a ^ cl->first_affinity) & TOPOLOGY_CLUSTER_MASK))
                return TOPOLOGY_BAD_PROFILE;
        }
        for (uint32_t j = 0; j < c; j++)
            if (!((profile->cluster[j].first_affinity ^ cl->first_affinity) &
                  TOPOLOGY_CLUSTER_MASK))
                return TOPOLOGY_BAD_PROFILE;
    }
    return TOPOLOGY_OK;
}

enum topology_status topology_policy_check(const struct topology_policy *policy)
{
    if (!policy)
        return TOPOLOGY_BAD_ARGUMENT;
    if ((policy->order != TOPOLOGY_PACK && policy->order != TOPOLOGY_SPREAD) ||
        policy->max_span_unfair == 0 || policy->max_span_unfair > TOPOLOGY_MAX_CLUSTERS)
        return TOPOLOGY_BAD_POLICY;
    return TOPOLOGY_OK;
}

static void placement_clear(struct topology_placement *p)
{
    p->count = 0;
    p->cluster_span = 0;
    for (uint32_t i = 0; i < TOPOLOGY_MAX_VCPUS; i++) {
        p->affinity[i] = 0;
        p->cluster[i] = 0;
    }
    for (uint32_t i = 0; i < TOPOLOGY_MAX_CLUSTERS; i++)
        p->per_cluster[i] = 0;
}

/* The profile must already have passed topology_profile_check(). */
static int find_core(const struct topology_profile *profile, uint64_t affinity,
                     uint32_t *cluster, uint32_t *core)
{
    for (uint32_t c = 0; c < profile->cluster_count; c++)
        for (uint32_t k = 0; k < profile->cluster[c].cores; k++)
            if (topology_core_affinity(&profile->cluster[c], k) == affinity) {
                *cluster = c;
                *core = k;
                return 1;
            }
    return 0;
}

static void push(struct topology_placement *out, uint32_t cluster, uint64_t affinity)
{
    out->affinity[out->count] = affinity;
    out->cluster[out->count] = cluster;
    out->count++;
    if (out->per_cluster[cluster]++ == 0)
        out->cluster_span++;
}

static int take_next(const struct topology_profile *profile, uint32_t cluster,
                     uint32_t cursor[TOPOLOGY_MAX_CLUSTERS], uint32_t primary_cluster,
                     uint32_t primary_core, uint64_t *affinity)
{
    while (cursor[cluster] < profile->cluster[cluster].cores) {
        uint32_t k = cursor[cluster]++;
        if (cluster == primary_cluster && k == primary_core)
            continue;
        *affinity = topology_core_affinity(&profile->cluster[cluster], k);
        return 1;
    }
    return 0;
}

enum topology_status topology_place(const struct topology_profile *profile,
    const struct topology_policy *policy, uint32_t vcpus,
    uint64_t primary_affinity, int fair_lock, struct topology_placement *out)
{
    uint32_t cursor[TOPOLOGY_MAX_CLUSTERS], order[TOPOLOGY_MAX_CLUSTERS];
    uint32_t primary_cluster, primary_core, n, i, shift;
    uint64_t affinity;
    enum topology_status st;

    if (!out)
        return TOPOLOGY_BAD_ARGUMENT;
    placement_clear(out);
    if (!profile || !policy)
        return TOPOLOGY_BAD_ARGUMENT;
    if ((st = topology_profile_check(profile)) != TOPOLOGY_OK)
        return st;
    if ((st = topology_policy_check(policy)) != TOPOLOGY_OK)
        return st;
    if (vcpus == 0)
        return TOPOLOGY_ZERO_VCPUS;
    if (vcpus > TOPOLOGY_MAX_VCPUS)
        return TOPOLOGY_TOO_MANY_VCPUS;
    if (vcpus > topology_profile_cores(profile))
        return TOPOLOGY_OVERSUBSCRIBED;
    if (!find_core(profile, primary_affinity, &primary_cluster, &primary_core))
        return TOPOLOGY_BAD_PRIMARY_AFFINITY;

    /* Spread starts the rotation after the primary's cluster because the primary
     * already holds that cluster's first slot; starting there would leave it one
     * vCPU ahead of the others. */
    n = profile->cluster_count;
    shift = policy->order == TOPOLOGY_SPREAD ? 1u : 0u;
    for (i = 0; i < n; i++) {
        order[i] = (primary_cluster + shift + i) % n;
        cursor[i] = 0;
    }
    push(out, primary_cluster, primary_affinity);
    if (policy->order == TOPOLOGY_PACK) {
        for (i = 0; i < n && out->count < vcpus; i++)
            while (out->count < vcpus &&
                   take_next(profile, order[i], cursor, primary_cluster, primary_core, &affinity))
                push(out, order[i], affinity);
    } else {
        int progressed = 1;
        while (out->count < vcpus && progressed) {
            progressed = 0;
            for (i = 0; i < n && out->count < vcpus; i++)
                if (take_next(profile, order[i], cursor, primary_cluster, primary_core, &affinity)) {
                    push(out, order[i], affinity);
                    progressed = 1;
                }
        }
    }
    /* Unreachable unless the oversubscription check above is wrong. */
    if (out->count != vcpus) {
        placement_clear(out);
        return TOPOLOGY_BAD_PLACEMENT;
    }
    if (out->cluster_span > policy->max_span_unfair && !fair_lock) {
        placement_clear(out);
        return TOPOLOGY_NEEDS_FAIR_LOCK;
    }
    return TOPOLOGY_OK;
}

enum topology_status topology_check_placement(const struct topology_profile *profile,
    const struct topology_policy *policy, uint64_t primary_affinity, int fair_lock,
    const struct topology_placement *pl)
{
    uint8_t used[TOPOLOGY_MAX_CLUSTERS][TOPOLOGY_MAX_CLUSTER_CORES];
    uint32_t per[TOPOLOGY_MAX_CLUSTERS], rank[TOPOLOGY_MAX_CLUSTERS];
    uint32_t pc, pk, n, c, k, i, span = 0, last = 0;
    enum topology_status st;

    if (!profile || !policy || !pl)
        return TOPOLOGY_BAD_ARGUMENT;
    if ((st = topology_profile_check(profile)) != TOPOLOGY_OK)
        return st;
    if ((st = topology_policy_check(policy)) != TOPOLOGY_OK)
        return st;
    if (pl->count == 0)
        return TOPOLOGY_ZERO_VCPUS;
    if (pl->count > TOPOLOGY_MAX_VCPUS)
        return TOPOLOGY_TOO_MANY_VCPUS;
    if (pl->count > topology_profile_cores(profile))
        return TOPOLOGY_OVERSUBSCRIBED;
    if (!find_core(profile, primary_affinity, &pc, &pk))
        return TOPOLOGY_BAD_PRIMARY_AFFINITY;

    n = profile->cluster_count;
    for (c = 0; c < TOPOLOGY_MAX_CLUSTERS; c++) {
        per[c] = 0;
        rank[c] = 0;
        for (k = 0; k < TOPOLOGY_MAX_CLUSTER_CORES; k++)
            used[c][k] = 0;
    }
    for (i = 0; i < pl->count; i++) {
        if (!find_core(profile, pl->affinity[i], &c, &k) || pl->cluster[i] != c || used[c][k])
            return TOPOLOGY_BAD_PLACEMENT;
        used[c][k] = 1;
        per[c]++;
    }
    if (pl->affinity[0] != primary_affinity)
        return TOPOLOGY_BAD_PLACEMENT;
    for (c = 0; c < TOPOLOGY_MAX_CLUSTERS; c++) {
        if (pl->per_cluster[c] != per[c])
            return TOPOLOGY_BAD_PLACEMENT;
        if (per[c])
            span++;
    }
    if (pl->cluster_span != span)
        return TOPOLOGY_BAD_PLACEMENT;
    /* Safety rule first: a span the lock cannot tolerate is reported as such,
     * not as a shape error, so legacy placements get the same refusal that
     * tools/secondary-runtime.py gives them. */
    if (span > policy->max_span_unfair && !fair_lock)
        return TOPOLOGY_NEEDS_FAIR_LOCK;

    /* Inside a cluster the vCPUs take cores in ascending order, skipping the
     * primary, with no gaps; ascending slots keep the layout reproducible. */
    for (c = 0; c < n; c++) {
        int gap = 0;
        for (k = 0; k < profile->cluster[c].cores; k++) {
            if (c == pc && k == pk)
                continue;
            if (!used[c][k])
                gap = 1;
            else if (gap)
                return TOPOLOGY_BAD_PLACEMENT;
        }
    }
    {
        uint32_t seen[TOPOLOGY_MAX_CLUSTERS];
        for (c = 0; c < TOPOLOGY_MAX_CLUSTERS; c++)
            seen[c] = 0;
        for (i = 1; i < pl->count; i++) {
            (void)find_core(profile, pl->affinity[i], &c, &k);
            if (seen[c] && k <= seen[c] - 1u)
                return TOPOLOGY_BAD_PLACEMENT;
            seen[c] = k + 1u;
        }
    }
    for (i = 0; i < n; i++)
        rank[(pc + i) % n] = i;
    if (policy->order == TOPOLOGY_PACK) {
        for (i = 0; i < n; i++)
            if (per[(pc + i) % n])
                last = i;
        /* Every cluster before the last one used is full: nothing is spread. */
        for (i = 0; i < last; i++)
            if (per[(pc + i) % n] != profile->cluster[(pc + i) % n].cores)
                return TOPOLOGY_BAD_PLACEMENT;
        for (i = 2; i < pl->count; i++)
            if (rank[pl->cluster[i]] < rank[pl->cluster[i - 1]])
                return TOPOLOGY_BAD_PLACEMENT;
    } else {
        /* A cluster may lead another by more than one vCPU only if the other
         * cluster has no core left to give. */
        for (c = 0; c < n; c++)
            for (k = 0; k < n; k++)
                if (per[c] > per[k] + 1u && per[k] != profile->cluster[k].cores)
                    return TOPOLOGY_BAD_PLACEMENT;
    }
    return TOPOLOGY_OK;
}

uint64_t topology_guest_vmpidr(uint32_t slot)
{
    if (slot >= TOPOLOGY_GUEST_J700_CPUS)
        return 0;
    return UINT64_C(0x80000000) | (slot < 4u ? slot : UINT64_C(0x100) + slot - 4u);
}

/* ---- GICv3/v4 redistributor discovery ---- */

uint64_t topology_gicr_affinity_value(uint64_t affinity)
{
    return (affinity & UINT64_C(0xffffff)) | ((affinity >> 32) << 24);
}

uint64_t topology_gicr_typer_affinity(uint64_t typer)
{
    uint64_t value = typer >> 32;
    return (value & UINT64_C(0xffffff)) | ((value >> 24) << 32);
}

static void gicr_map_clear(struct topology_gicr_map *map)
{
    map->count = 0;
    map->stride = 0;
    for (uint32_t i = 0; i < TOPOLOGY_GICR_MAX_FRAMES; i++) {
        map->frame[i].base = 0;
        map->frame[i].typer = 0;
        map->frame[i].affinity = 0;
    }
}

enum topology_gicr_status topology_gicr_stride(const struct topology_gicr_window *window,
    uint64_t *stride)
{
    uint64_t minimum, chosen;
    if (!window || !stride)
        return TOPOLOGY_GICR_BAD_ARGUMENT;
    if (window->gic_version != 3 && window->gic_version != 4)
        return TOPOLOGY_GICR_BAD_VERSION;
    minimum = window->gic_version == 4 ? TOPOLOGY_GICR_STRIDE_V4 : TOPOLOGY_GICR_STRIDE_V3;
    chosen = window->stride ? window->stride : minimum;
    if (chosen < minimum || (chosen % TOPOLOGY_GICR_FRAME_ALIGN))
        return TOPOLOGY_GICR_BAD_STRIDE;
    *stride = chosen;
    return TOPOLOGY_GICR_OK;
}

enum topology_gicr_status topology_gicr_discover(const struct topology_gicr_window *window,
    topology_gicr_read64_fn read64, void *ctx, struct topology_gicr_map *map)
{
    uint64_t stride, address;
    enum topology_gicr_status st;

    if (!map)
        return TOPOLOGY_GICR_BAD_ARGUMENT;
    gicr_map_clear(map);
    if (!window || !read64)
        return TOPOLOGY_GICR_BAD_ARGUMENT;
    if ((st = topology_gicr_stride(window, &stride)) != TOPOLOGY_GICR_OK)
        return st;
    if (window->limit <= window->base)
        return TOPOLOGY_GICR_BAD_RANGE;
    if (window->base & (TOPOLOGY_GICR_FRAME_ALIGN - 1))
        return TOPOLOGY_GICR_UNALIGNED_BASE;

    map->stride = stride;
    address = window->base;
    for (;;) {
        uint64_t typer, affinity;
        /* The checks compare against the remaining length so that no address
         * computation can wrap, whatever base and limit hold. */
        if (address >= window->limit) {
            st = TOPOLOGY_GICR_NO_LAST;
            break;
        }
        if (window->limit - address < stride) {
            st = TOPOLOGY_GICR_WALK_PAST_LIMIT;
            break;
        }
        if (map->count == TOPOLOGY_GICR_MAX_FRAMES) {
            st = TOPOLOGY_GICR_TOO_MANY;
            break;
        }
        if (read64(ctx, address + TOPOLOGY_GICR_TYPER_OFFSET, &typer)) {
            st = TOPOLOGY_GICR_READ_FAILED;
            break;
        }
        /* All ones is what a faulting or unclaimed read returns; it would
         * otherwise parse as affinity 0xffffffff with Last set. */
        if (typer == ~UINT64_C(0)) {
            st = TOPOLOGY_GICR_BAD_TYPER;
            break;
        }
        affinity = topology_gicr_typer_affinity(typer);
        for (uint32_t i = 0; i < map->count; i++)
            if (map->frame[i].affinity == affinity) {
                st = TOPOLOGY_GICR_DUPLICATE_AFFINITY;
                goto fail;
            }
        map->frame[map->count].base = address;
        map->frame[map->count].typer = typer;
        map->frame[map->count].affinity = affinity;
        map->count++;
        if (typer & TOPOLOGY_GICR_TYPER_LAST)
            return TOPOLOGY_GICR_OK;
        address += stride;
    }
fail:
    gicr_map_clear(map);
    return st;
}

enum topology_gicr_status topology_gicr_find(const struct topology_gicr_map *map,
    uint64_t affinity, uint64_t *frame_base)
{
    if (!map || !frame_base || map->count > TOPOLOGY_GICR_MAX_FRAMES)
        return TOPOLOGY_GICR_BAD_ARGUMENT;
    for (uint32_t i = 0; i < map->count; i++)
        if (map->frame[i].affinity == affinity) {
            *frame_base = map->frame[i].base;
            return TOPOLOGY_GICR_OK;
        }
    return TOPOLOGY_GICR_AFFINITY_NOT_FOUND;
}

enum topology_gicr_status topology_gicr_check_window(const struct topology_gicr_window *window,
    uint32_t cores)
{
    uint64_t stride, span;
    enum topology_gicr_status st;
    if (!window)
        return TOPOLOGY_GICR_BAD_ARGUMENT;
    if ((st = topology_gicr_stride(window, &stride)) != TOPOLOGY_GICR_OK)
        return st;
    if (window->limit <= window->base)
        return TOPOLOGY_GICR_BAD_RANGE;
    if (window->base & (TOPOLOGY_GICR_FRAME_ALIGN - 1))
        return TOPOLOGY_GICR_UNALIGNED_BASE;
    span = window->limit - window->base;
    if (cores == 0 || (span % stride) || span / stride != cores)
        return TOPOLOGY_GICR_WINDOW_MISMATCH;
    return TOPOLOGY_GICR_OK;
}

enum topology_gicr_status topology_gicr_assign(const struct topology_gicr_map *map,
    const struct topology_placement *placement, uint64_t *out, uint32_t out_cap)
{
    enum topology_gicr_status st = TOPOLOGY_GICR_OK;
    uint32_t i, j;

    if (!map || !placement || !out || placement->count == 0 ||
        placement->count > TOPOLOGY_MAX_VCPUS || out_cap < placement->count)
        return TOPOLOGY_GICR_BAD_ARGUMENT;
    for (i = 0; i < placement->count && st == TOPOLOGY_GICR_OK; i++)
        st = topology_gicr_find(map, placement->affinity[i], &out[i]);
    for (i = 0; i < placement->count && st == TOPOLOGY_GICR_OK; i++)
        for (j = 0; j < i; j++)
            if (out[i] == out[j])
                st = TOPOLOGY_GICR_DUPLICATE_AFFINITY;
    if (st != TOPOLOGY_GICR_OK)
        for (i = 0; i < placement->count; i++)
            out[i] = 0;
    return st;
}

enum topology_gicr_status topology_gicr_check_assignment(const struct topology_gicr_map *map,
    uint64_t affinity, uint64_t redistributor)
{
    uint64_t found;
    enum topology_gicr_status st = topology_gicr_find(map, affinity, &found);
    if (st != TOPOLOGY_GICR_OK)
        return st;
    return found == redistributor ? TOPOLOGY_GICR_OK : TOPOLOGY_GICR_AFFINITY_MISMATCH;
}
