/* SPDX-License-Identifier: MIT */
#ifndef SNAP_COMPAT_TOPOLOGY_H
#define SNAP_COMPAT_TOPOLOGY_H
#include <stddef.h>
#include <stdint.h>

/* Guest vCPU placement and GICv3/v4 redistributor discovery.
 *
 * Both models are pure: no MMIO, no allocation, no global mutable state. They
 * run natively under tools/test-guest-topology.py and are written so they can
 * later run at EL2. They are linked only under SNAP_PLATFORM_SP11 (probes/run.py
 * OPTIONAL['xnuboot']) and compat/cpu.h includes this header only for that
 * platform, so an A16 build neither sees nor links any of it.
 *
 * Nothing here is wired to a boot path yet (UNIMPLEMENTED): placement results
 * and discovered frames reach the monitor only through the host/prepare tools
 * that a coordinator wires up. Evidence labels below follow AGENTS.md.
 *
 * Affinities use the representation the rest of the tree uses for
 * secondary_config.affinity[]: MPIDR_EL1 & 0xff00ffffff, Aff3 at bits 39:32,
 * Aff2 at 23:16, Aff1 at 15:8, Aff0 at 7:0. On the Oryon parts the repository
 * has seen, Aff2 is the physical cluster and Aff1 the core inside it. */

#define TOPOLOGY_AFFINITY_MASK UINT64_C(0xff00ffffff)
#define TOPOLOGY_CLUSTER_MASK UINT64_C(0xff00ff0000)

/* Model capacities, not hardware facts. A profile larger than these is refused
 * rather than truncated. */
#define TOPOLOGY_MAX_CLUSTERS 4u
#define TOPOLOGY_MAX_CLUSTER_CORES 8u
#define TOPOLOGY_MAX_VCPUS 16u

/* SP11 X1E80100: 12 Oryon v1 cores in 3 clusters of 4. INFERRED: public
 * upstream device-tree sources and docs/SURFACE-PRO-11.md; not yet measured on
 * the unit (the EL2 probe, plan item X2, must confirm MPIDR and cluster
 * layout). Lower-SKU Snapdragon X parts have fewer cores and need their own
 * profile. */
#define TOPOLOGY_SP11_CLUSTERS 3u
#define TOPOLOGY_SP11_CLUSTER_CORES 4u
#define TOPOLOGY_SP11_PHYSICAL_CPUS (TOPOLOGY_SP11_CLUSTERS * TOPOLOGY_SP11_CLUSTER_CORES)

/* The J700 guest device tree declares six cpu nodes (four efficiency, two
 * performance). VMPIDR numbering beyond that needs synthesized nodes. */
#define TOPOLOGY_GUEST_J700_CPUS 6u

enum topology_status {
    TOPOLOGY_OK = 0,
    TOPOLOGY_BAD_ARGUMENT,
    TOPOLOGY_BAD_PROFILE,
    TOPOLOGY_BAD_POLICY,
    TOPOLOGY_BAD_PRIMARY_AFFINITY,
    TOPOLOGY_ZERO_VCPUS,
    TOPOLOGY_TOO_MANY_VCPUS,   /* beyond TOPOLOGY_MAX_VCPUS (model capacity) */
    TOPOLOGY_OVERSUBSCRIBED,   /* more vCPUs than the profile has physical cores */
    TOPOLOGY_NEEDS_FAIR_LOCK,  /* spans more clusters than the policy allows without a fair lock */
    TOPOLOGY_BAD_PLACEMENT     /* topology_check_placement: not a valid placement for this policy */
};

/* One physical cluster: cores are first_affinity + k * core_step, k < cores.
 * Stepping must never carry into the cluster bits (profile check). */
struct topology_cluster {
    uint64_t first_affinity;
    uint64_t core_step;
    uint32_t cores;
};

/* The physical machine as data. hyp_timer_intid is the PPI INTID of the EL2
 * physical timer (CNTHP) that probes/secondary.c uses as its per-CPU
 * watchdog. The A16 value is the one probes/secondary.c hard-codes and
 * tools/probe-a16-secondary.py asserts (OBSERVED in code; no run record is in
 * this tree). The SP11 value is INFERRED: Arm's recommended PPI assignment and
 * the public device tree agree, and the EL2 probe (plan item X2) must measure
 * it. */
struct topology_profile {
    uint32_t cluster_count;
    uint32_t hyp_timer_intid;
    struct topology_cluster cluster[TOPOLOGY_MAX_CLUSTERS];
};

enum topology_order {
    TOPOLOGY_PACK = 1,   /* fill the primary's cluster, then the next clusters in cyclic order */
    TOPOLOGY_SPREAD = 2  /* round-robin one core per cluster, starting at the primary's cluster */
};

/* Placement policy as data, so X2/X3 measurements can change it without code
 * edits. max_span_unfair is the most clusters the vCPUs may span while the
 * service lock is not fair; v26 (compat/boot-relocate.h) measured a remote
 * cluster starving a waiter on the single test-and-set lock, so the shipped
 * value is 1 until a queued lock (SNAP_FAIR_TOKEN) has been measured on SP11. */
struct topology_policy {
    enum topology_order order;
    uint32_t max_span_unfair;
};

struct topology_placement {
    uint32_t count;
    uint32_t cluster_span;                      /* clusters holding at least one vCPU */
    uint64_t affinity[TOPOLOGY_MAX_VCPUS];      /* slot 0 is the primary (boot) vCPU */
    uint32_t cluster[TOPOLOGY_MAX_VCPUS];       /* index into the profile */
    uint32_t per_cluster[TOPOLOGY_MAX_CLUSTERS];
};

extern const struct topology_profile topology_profile_a16;
extern const struct topology_profile topology_profile_sp11;
extern const struct topology_policy topology_policy_pack;
extern const struct topology_policy topology_policy_spread;

enum topology_status topology_profile_check(const struct topology_profile *profile);
enum topology_status topology_policy_check(const struct topology_policy *policy);
uint32_t topology_profile_cores(const struct topology_profile *profile);
uint64_t topology_core_affinity(const struct topology_cluster *cluster, uint32_t index);

/* Places `vcpus` vCPUs. Slot 0 runs on primary_affinity, which must be a core
 * of the profile (the CPU q1n1 handed to the monitor, or the relocation
 * target). `fair_lock` says whether the build has the queued service lock
 * (SNAP_FAIR_TOKEN). Every failure zeroes *out, so a caller cannot use a
 * partial result. */
enum topology_status topology_place(const struct topology_profile *profile,
    const struct topology_policy *policy, uint32_t vcpus,
    uint64_t primary_affinity, int fair_lock, struct topology_placement *out);

/* Re-verifies a placement from properties alone (no recomputation), so it also
 * rejects hand-edited or legacy placements: distinct profile cores, slot 0 =
 * primary, per-cluster bookkeeping consistent, the fair-lock span rule, and
 * the policy's shape (pack: clusters filled in order; spread: balanced). */
enum topology_status topology_check_placement(const struct topology_profile *profile,
    const struct topology_policy *policy, uint64_t primary_affinity, int fair_lock,
    const struct topology_placement *placement);

/* VMPIDR_EL2 the J700 guest sees for vCPU `slot` (probes/xnuboot/setup.c): four
 * efficiency cores at 0x0-0x3, two performance cores at 0x100-0x101, bit 31
 * set. Returns 0 for slots without a J700 device-tree node. */
uint64_t topology_guest_vmpidr(uint32_t slot);

/* PPI INTIDs are 16..31. Extended PPIs (1056..1119) are not modeled and are
 * refused. The helpers return the bit in the 32-bit per-PPI registers of the
 * redistributor SGI frame (GICR_ISENABLER0 and friends), or 0 for a non-PPI. */
int topology_intid_is_ppi(uint32_t intid);
uint32_t topology_ppi_bit(uint32_t intid);
/* GICR_ICFGR1 holds two configuration bits per PPI; the upper one set means
 * edge-triggered. */
uint32_t topology_ppi_icfgr1_edge_bit(uint32_t intid);

/* ---- GICv3/v4 redistributor discovery ---- */

#define TOPOLOGY_GICR_TYPER_OFFSET UINT64_C(0x8)
#define TOPOLOGY_GICR_TYPER_VLPIS (UINT64_C(1) << 1)
#define TOPOLOGY_GICR_TYPER_LAST (UINT64_C(1) << 4)
#define TOPOLOGY_GICR_FRAME_ALIGN UINT64_C(0x10000)
#define TOPOLOGY_GICR_STRIDE_V3 UINT64_C(0x20000)
#define TOPOLOGY_GICR_STRIDE_V4 UINT64_C(0x40000)
#define TOPOLOGY_GICR_MAX_FRAMES 32u

enum topology_gicr_status {
    TOPOLOGY_GICR_OK = 0,
    TOPOLOGY_GICR_BAD_ARGUMENT,
    TOPOLOGY_GICR_BAD_VERSION,
    TOPOLOGY_GICR_BAD_STRIDE,
    TOPOLOGY_GICR_BAD_RANGE,
    TOPOLOGY_GICR_UNALIGNED_BASE,
    TOPOLOGY_GICR_READ_FAILED,
    TOPOLOGY_GICR_BAD_TYPER,
    TOPOLOGY_GICR_DUPLICATE_AFFINITY,
    TOPOLOGY_GICR_NO_LAST,           /* window ended exactly on a frame without a Last frame */
    TOPOLOGY_GICR_WALK_PAST_LIMIT,   /* a partial frame would be read at the end of the window */
    TOPOLOGY_GICR_TOO_MANY,
    TOPOLOGY_GICR_AFFINITY_NOT_FOUND,
    TOPOLOGY_GICR_AFFINITY_MISMATCH,
    TOPOLOGY_GICR_WINDOW_MISMATCH
};

/* The window comes from the board descriptor or the ACPI MADT, never from this
 * file. [base, limit) must hold every redistributor and nothing else: the A16
 * limit 0x17500000 would admit unrelated MMIO on SP11. gic_version is the
 * architecture revision (3 or 4) and selects the default stride; `stride`
 * overrides it when nonzero because a devicetree can declare a v3 binding with
 * a v4-sized stride (redistributor-stride). A stride smaller than the revision
 * needs, or not a multiple of 64 KiB, is refused. */
struct topology_gicr_window {
    uint64_t base, limit;
    uint64_t stride;
    uint32_t gic_version;
};

struct topology_gicr_frame {
    uint64_t base;      /* RD_base of this redistributor */
    uint64_t typer;     /* raw GICR_TYPER */
    uint64_t affinity;  /* TYPER Affinity_Value converted to the MPIDR form above */
};

struct topology_gicr_map {
    uint32_t count;
    uint64_t stride;
    struct topology_gicr_frame frame[TOPOLOGY_GICR_MAX_FRAMES];
};

/* Reads a 64-bit register. Returns nonzero if the read cannot be performed. */
typedef int (*topology_gicr_read64_fn)(void *ctx, uint64_t address, uint64_t *value);

/* GICR_TYPER[63:32] is Aff3:Aff2:Aff1:Aff0 packed in 32 bits; MPIDR_EL1 keeps
 * Aff3 at bits 39:32. These convert between the two (the EL2 secondary start
 * code compares the same expression). */
uint64_t topology_gicr_affinity_value(uint64_t affinity);
uint64_t topology_gicr_typer_affinity(uint64_t typer);

enum topology_gicr_status topology_gicr_stride(const struct topology_gicr_window *window,
    uint64_t *stride);

/* Walks redistributor frames from window->base, one stride at a time, reading
 * GICR_TYPER, until the frame with TYPER.Last. Refuses windows that end
 * without a Last frame, partial trailing frames, duplicate affinities, unreadable
 * or all-ones TYPER values, and more than TOPOLOGY_GICR_MAX_FRAMES frames.
 * *map is zeroed on every failure. Discovery alone cannot prove the stride
 * (a wrong stride reads another frame's bytes); always cross-check with
 * topology_gicr_assign() against the placement's affinities. */
enum topology_gicr_status topology_gicr_discover(const struct topology_gicr_window *window,
    topology_gicr_read64_fn read64, void *ctx, struct topology_gicr_map *map);

enum topology_gicr_status topology_gicr_find(const struct topology_gicr_map *map,
    uint64_t affinity, uint64_t *frame_base);

/* Window sanity: it must hold exactly `cores` whole frames. */
enum topology_gicr_status topology_gicr_check_window(const struct topology_gicr_window *window,
    uint32_t cores);

/* One redistributor per slot of a placement: out[i] = frame base of
 * placement->affinity[i]. out_cap must cover placement->count. */
enum topology_gicr_status topology_gicr_assign(const struct topology_gicr_map *map,
    const struct topology_placement *placement, uint64_t *out, uint32_t out_cap);

/* Cross-check of an already chosen (affinity, redistributor) pair, the way the
 * EL2 secondary start verifies the frame it was handed. */
enum topology_gicr_status topology_gicr_check_assignment(const struct topology_gicr_map *map,
    uint64_t affinity, uint64_t redistributor);

#endif
