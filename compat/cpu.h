/* SPDX-License-Identifier: MIT */
#ifndef SNAP_COMPAT_CPU_H
#define SNAP_COMPAT_CPU_H
#include <stdint.h>
#include "platform.h"
#if SNAP_PLATFORM_SP11
#include "topology.h"
#endif

/* Number of guest vCPUs. A16 keeps six (the J700 device tree's four E and two P
 * cores) and is the only qualified count. The A16 count is the literal below and
 * is not overridable: a -DGUEST_CPU_COUNT on an A16 build is the macro
 * redefinition it always was, an error under the monitor's -Werror.
 *
 * SP11 (UNIMPLEMENTED): no count has been validated on hardware. The default of
 * six is EXPERIMENTAL, inherited from the J700 guest rather than measured.
 * Change it only after the first bring-up (plan item X4) shows the J700 guest
 * boots with that many vCPUs, the fixed-six sites below follow the count, and
 * topology_place() (compat/topology.h) accepts it with the build's service-lock
 * setting.
 *
 * A count above one cluster's four cores spans two clusters, and on the single
 * test-and-set service lock a remote cluster starved a waiter (v26,
 * compat/boot-relocate.h), so topology_place() refuses it unless the build has
 * the queued lock (SNAP_FAIR_TOKEN). GUEST_CPU_NEEDS_FAIR_LOCK is that refusal for
 * the pack policy (max_span_unfair is 1), and xnuboot_prepare returns
 * GUEST_CPU_STATUS_FAIR_LOCK for such a build before it builds the devices, the
 * shadow or stage 2.
 * tools/test-monitor-hardening.py holds the macro equal to the model for every
 * count. It is a launch refusal and not an #error because the bare SP11 gate
 * must still compile and link (tools/monitor_build_offline.py).
 *
 * Overriding the count alone does not make a bootable monitor. These sites
 * hard-code six and a static assertion stops a build at another count:
 * PAUTH_BANK_CONTEXT (compat/pauth-switch.h), SPRR_VIEW_CPUS (shadow/sprr-view.h,
 * under SNAP_USER_VIEW_BATCH), S2_RETRY_CPUS (compat/s2-fault-retry.h, under
 * SNAP_S2_FAULT_RETRY) and, under SNAP_PLATFORM_SP11, SPTM_ROOT_CPUS
 * (compat/sptm-root.h), panic_state's per-CPU entries (compat/panic-state.h) and
 * struct secondary_config (probes/secondary.h). These hard-code six with no such
 * assertion and must be changed together with the count: the boot-relocate
 * records and the host layout constants in probes/run.py. */
#if SNAP_PLATFORM_SP11
#ifndef GUEST_CPU_COUNT
#define GUEST_CPU_COUNT 6U
#endif
#if GUEST_CPU_COUNT < 1U || GUEST_CPU_COUNT > TOPOLOGY_SP11_PHYSICAL_CPUS
#error "GUEST_CPU_COUNT must be between 1 and the 12 physical cores of SNAP_PLATFORM_SP11"
#endif
#if GUEST_CPU_COUNT > TOPOLOGY_SP11_CLUSTER_CORES && !SNAP_FAIR_TOKEN
#define GUEST_CPU_NEEDS_FAIR_LOCK 1
#else
#define GUEST_CPU_NEEDS_FAIR_LOCK 0
#endif
/* The prepare status of a build with GUEST_CPU_NEEDS_FAIR_LOCK; probes/run.py names it (status band -146). */
#define GUEST_CPU_STATUS_FAIR_LOCK (-146)
#else
#define GUEST_CPU_COUNT 6U
#endif
#define GUEST_CPU_SCRATCH_STRIDE UINT64_C(0x8000)
#define GUEST_CPU_SCRATCH_SPAN (GUEST_CPU_COUNT * GUEST_CPU_SCRATCH_STRIDE)

/* J700's four E affinities and two P affinities. Registration is metadata;
 * it does not start a CPU or claim native machine-initialization completion.
 * The caller serializes these operations with the runtime service lock.
 * Execution identity comes from CPU-local
 * state, not this registry's active_slot diagnostic. */
struct guest_cpu_registry {
    uint64_t registered, registrations, id_queries, output_queries;
    uint64_t active_slot, last_affinity, last_slot, reserved;
};
struct guest_cpu_buffer { uint64_t pa, va; };
struct guest_cpu_address_space { uint64_t ttbr0, ttbr1; };
/* Conservative liveness check across every CPU, including a prepared CPU
 * whose root has been assigned before entry. ASID and CnP bits are not PA. */
int guest_cpu_root_busy(const struct guest_cpu_address_space spaces[GUEST_CPU_COUNT],
                        uint64_t root);
enum guest_cpu_result {
    CPU_OK, CPU_BAD_STATE, CPU_BAD_AFFINITY, CPU_NOT_REGISTERED,
    CPU_BAD_SLOT, CPU_BAD_BUFFER
};
enum guest_cpu_result guest_cpu_register(struct guest_cpu_registry *cpus, uint64_t affinity);
enum guest_cpu_result guest_cpu_id(struct guest_cpu_registry *cpus, uint64_t affinity,
                                  uint64_t *slot);
enum guest_cpu_result guest_cpu_buffer(const struct guest_cpu_registry *cpus,
    uint64_t slot, uint64_t base_pa, uint64_t base_va, uint64_t mem_low,
    uint64_t mem_high, struct guest_cpu_buffer *out);
enum guest_cpu_result guest_cpu_output(struct guest_cpu_registry *cpus,
    uint64_t slot, uint64_t base_pa, uint64_t base_va, uint64_t mem_low,
    uint64_t mem_high, struct guest_cpu_buffer *out);
int guest_cpu_buffers_valid(uint64_t base_pa, uint64_t base_va,
                            uint64_t mem_low, uint64_t mem_high);
#endif
