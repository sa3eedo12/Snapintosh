/* SPDX-License-Identifier: MIT */
/* Opt-in PSCI experiment, built only into sp11-el2-probe-psci.efi.
 *
 * It asks the firmware to start one secondary CPU at a tiny stub that records
 * the exception level and MPIDR it starts with, then parks. That answers, on
 * the real machine, which conduit (SMC or HVC) PSCI uses at EL2 and what state
 * a CPU_ON'd core arrives in: facts the monitor needs before it can bring up
 * secondary cores.
 *
 * It is a separate binary because it is the one thing the probe does that is
 * not passive: CPU_ON changes machine state. Only the SMC conduit is ever
 * issued. An HVC executed at EL2 is taken at EL2 itself, and this image installs
 * no vector to take it, so a firmware that declares the HVC conduit gets the
 * result PROBE_PSCI_RUN_HVC_REFUSED and no call at all. The default image
 * contains neither instruction, and no image contains an hvc (the PE test
 * checks both).
 */
#ifndef PROBE_PSCI_H
#define PROBE_PSCI_H

#include <stddef.h>
#include <stdint.h>

#include "acpi.h"
#include "report.h"

/* Mailbox shared with the secondary. The secondary runs with its MMU and
 * caches off, so the primary keeps the line clean and polls with
 * clean+invalidate; arrived is written last so that a non-zero value implies
 * the other two fields are valid. */
typedef struct {
    volatile uint64_t arrived;
    volatile uint64_t currentel;
    volatile uint64_t mpidr;
    uint64_t reserved[5];
} probe_psci_cell;

_Static_assert(sizeof(probe_psci_cell) == 64, "the mailbox must be exactly one cache line");
_Static_assert(offsetof(probe_psci_cell, currentel) == 8, "stub stores CurrentEL at +8");
_Static_assert(offsetof(probe_psci_cell, mpidr) == 16, "stub stores MPIDR at +16");

/* Runs PSCI_VERSION and, if a target exists, CPU_ON, filling *out. `cell`
 * must be 64-byte aligned and stay allocated; own_mpidr is the calling
 * core's MPIDR_EL1. */
void probe_psci_run(probe_psci_facts *out, int conduit, const probe_cpu_list *cpus,
                    uint64_t own_mpidr, probe_psci_cell *cell);

#endif /* PROBE_PSCI_H */
