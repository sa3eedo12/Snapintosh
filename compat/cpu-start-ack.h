/* SPDX-License-Identifier: MIT */
#ifndef SNAP_CPU_START_ACK_H
#define SNAP_CPU_START_ACK_H
/* The opt-in CPU-start acknowledge of the SP11 M1 profile (SNAP_SP11_CPU_START_ACK; docs/SP11-MONITOR-PROFILE.md section 10). EXPERIMENTAL, hardware NOT RUN
 * for this code.
 *
 * What the stock kernel does. After it has registered its CPUs it starts the others by writing PMGR's CPU-start block with ApplePMGR's register-write helper: PMGR
 * register block 24 + 0x4000 (compat/boot-relocate.h names the first write of the A16 route, value 0x2 for CPU 1). The word at +4 is the global core mask, the words at +8
 * and +12 are the E-cluster and P-cluster local masks (compat/cpu-release.c), all written with 32-bit stores. The M1 profile starts the guest with x4 = 0, so there is no
 * secondary configuration and compat/cpu-release.c is never reached (probes/xnuboot/trap_dispatch.c routes the block there only when host_cpus is set); the block has
 * no stage-2 mapping and no device model, and the first such store stopped the run in boot 21 (OBSERVED once: IPA 0x300734004, ESR_EL2 0x93970045, which is EC 0x24,
 * ISV 1, SAS word, SRT 23, WnR 1, DFSC 0x05, a translation fault; the decode is INFERRED from the architecture).
 *
 * What this does. With the gate on, a 32-bit store to +4, +8 or +12 of that block, taken as a stage-2 translation fault with a valid syndrome, is accepted, the
 * value is ignored and the guest resumes after the store. NO CPU IS STARTED. That is a deliberate diagnostic and NOT A MODEL OF THE HARDWARE: on the silicon the write
 * starts a core, and the stock kernel then waits for that core to announce itself (INFERRED from the public xnu source; not read in the 26A428 kernelcache). Nothing here
 * answers "started", so the guest's wait can only end in its own timeout, and what the diagnostic is for is the stop or panic that follows. Reads, any other width, any other
 * offset, any other address and any other kind of fault keep the monitor's behaviour of every build: the run stops with the stage-2 fault.
 *
 * Why each condition. EC 0x24 and ISV: only a data abort whose syndrome describes the access says its width, direction and register (shadow/dockchannel.h dc_decode),
 * and an access that does not describe itself (a writeback form, a pair) must stop rather than be guessed. S1PTW clear: a fault in the stage-1 walk is not the store.
 * DFSC a translation fault of any level: the block is unmapped; a permission or access-flag fault would mean another state of the monitor's stage 2 than the one observed.
 * The IPA exactly one of three words: the three registers are the whole documented interface, the neighbouring words are not. HPFAR_EL2 agrees with the IPA whenever it
 * holds one: the IPA is otherwise the monitor's own stage-1 re-walk (HPFAR read back as zero for a permission fault on the A16 silicon, so a zero is not a disagreement). */
#include <stdint.h>
#include "dockchannel.h"

#define CPU_START_ACK_BLOCK     UINT64_C(0x300734000)   /* PMGR register block 24 + 0x4000 on the J700 tree the guest carries */
#define CPU_START_ACK_GLOBAL    4u                      /* global core mask */
#define CPU_START_ACK_CLUSTER_E 8u                      /* E-cluster local mask */
#define CPU_START_ACK_CLUSTER_P 12u                     /* P-cluster local mask */
#define CPU_START_ACK_EC_DABT   0x24u                   /* data abort from a lower exception level */
#define CPU_START_ACK_ISS_S1PTW (UINT32_C(1) << 7)
#define CPU_START_ACK_DFSC_MASK 0x3cu                   /* DFSC[5:2] */
#define CPU_START_ACK_DFSC_TRANSLATION 0x04u            /* 0b0001LL: a translation fault at level LL */
#define CPU_START_ACK_HPFAR_FIPA UINT64_C(0x0000fffffffffff0)

/* The first CPU_START_ACK_FIRST acknowledged stores are kept whole, in the report window and in the event log. The log of 512 events is usually full by the time the
 * kernel starts its CPUs (boot 21: 512 events, 96,475 dropped before the stop), so the window is the evidence and the log a convenience. */
#define CPU_START_ACK_FIRST 8u
#define CPU_START_ACK_VERSION 1u
#define CPU_START_ACK_MAGIC UINT64_C(0x31304b4341555043)   /* "CPUACK01" */

/* Where it lives: not in struct context (probes/xnuboot/context.c cpu_start_ack_of): it sits in the slack of the context's last page, after sizeof(struct context),
 * so that no offset and no size of the context depends on the gate, and xnuboot_setup zeroes it (the host does not clear the work area). */
struct cpu_start_ack {
    uint64_t total;                         /* stores acknowledged and ignored */
    uint64_t at[3];                         /* ... of them at +4, +8 and +12 */
    uint64_t last_pc, last_what;            /* the latest: guest PC, offset << 32 | value */
    struct { uint64_t pc, what; } first[CPU_START_ACK_FIRST];
};
/* The report window block (probes/xnuboot/report_layout.c OUT_CPU_START_ACK): this struct's words after two header words. */
#define CPU_START_ACK_REPORT_WORDS (2u + sizeof(struct cpu_start_ack) / 8)
_Static_assert(sizeof(struct cpu_start_ack) == 8 * 22, "the window block is 24 words: magic, layout, then these 22");

/* 1 when `esr` and `ipa` (the guest's physical address of the access, from the monitor's stage-1 re-walk) describe a 32-bit store to one of the three registers, with
 * *offset the register's offset (4, 8 or 12); 0 otherwise, *offset untouched. `access` is dc_decode(esr). Pure: no state, no I/O. */
static inline int cpu_start_ack_match(uint64_t esr, uint64_t ipa, uint64_t hpfar, const struct dc_access *access, unsigned *offset)
{
    uint64_t iss = esr & 0x1ffffffu;
    if ((esr >> 26) != CPU_START_ACK_EC_DABT || (iss & CPU_START_ACK_ISS_S1PTW) ||
        (iss & CPU_START_ACK_DFSC_MASK) != CPU_START_ACK_DFSC_TRANSLATION)
        return 0;
    if (!access->valid || !access->write || access->size != 4)
        return 0;
    if (ipa < CPU_START_ACK_BLOCK || ipa - CPU_START_ACK_BLOCK > CPU_START_ACK_CLUSTER_P)
        return 0;
    uint64_t at = ipa - CPU_START_ACK_BLOCK;
    if (at != CPU_START_ACK_GLOBAL && at != CPU_START_ACK_CLUSTER_E && at != CPU_START_ACK_CLUSTER_P)
        return 0;
    if ((hpfar & CPU_START_ACK_HPFAR_FIPA) && (((hpfar >> 4) << 12) != (ipa & ~UINT64_C(0xfff))))
        return 0;
    *offset = (unsigned)at;
    return 1;
}

/* Account one acknowledged store: `pc` the guest instruction, `offset` as cpu_start_ack_match gave it, `value` the 32 bits written (ignored by the monitor). An offset
 * that match cannot have returned counts nothing, so a caller's mistake cannot index outside `at`. */
static inline void cpu_start_ack_note(struct cpu_start_ack *s, uint64_t pc, unsigned offset, uint64_t value)
{
    if (offset != CPU_START_ACK_GLOBAL && offset != CPU_START_ACK_CLUSTER_E && offset != CPU_START_ACK_CLUSTER_P)
        return;
    uint64_t what = (uint64_t)offset << 32 | (value & UINT64_C(0xffffffff));
    if (s->total < CPU_START_ACK_FIRST) {
        s->first[s->total].pc = pc;
        s->first[s->total].what = what;
    }
    s->total++;
    s->at[offset / 4 - 1]++;
    s->last_pc = pc;
    s->last_what = what;
}
#endif
