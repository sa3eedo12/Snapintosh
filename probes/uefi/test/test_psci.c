/* SPDX-License-Identifier: MIT */
/* Tests of the PSCI experiment's entry policy (probes/uefi/psci.c).
 *
 * probe_psci_run is compiled natively here, with mocks standing in for the
 * system-register layer. Only paths that make no firmware call are exercised:
 * a native SMC would be an illegal instruction. The point is the refusal rule:
 * an HVC executed at EL2 is taken by this exception level's own vector, which
 * no probe image installs, so a declared HVC conduit must end in
 * PROBE_PSCI_RUN_HVC_REFUSED before anything is called, touched or timed.
 */
#include <setjmp.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cpu.h"
#include "harness.h"
#include "psci.h"

static struct {
    unsigned reads;
    unsigned flushes;
    unsigned counters;
} g_cpu;

#if defined(__aarch64__)
static sigjmp_buf g_illegal;

static void on_illegal_instruction(int sig)
{
    (void)sig;
    siglongjmp(g_illegal, 1);
}

/* Runs probe_psci_run for the HVC conduit. A refusal makes no call at all, so
 * the only way to see an smc is a policy that no longer refuses: a user process
 * cannot execute one and gets SIGILL, which is turned into a failed check here
 * instead of ending the test binary. Returns 1 if the function came back. */
static int run_hvc_conduit(probe_psci_facts *f, const probe_cpu_list *cpus, probe_psci_cell *cell)
{
    struct sigaction sa, old;
    int came_back;

    memset(&sa, 0, sizeof sa);
    sa.sa_handler = on_illegal_instruction;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGILL, &sa, &old);
    came_back = sigsetjmp(g_illegal, 1) == 0;
    if (came_back)
        probe_psci_run(f, PROBE_PSCI_HVC, cpus, 0x81000000ull, cell);
    sigaction(SIGILL, &old, NULL);
    return came_back;
}
#endif

uint64_t probe_cpu_read_sysreg(int id)
{
    (void)id;
    g_cpu.reads++;
    return 0;
}

void probe_cpu_flush_dcache(void *ctx, const void *addr, size_t len)
{
    (void)ctx;
    (void)addr;
    (void)len;
    g_cpu.flushes++;
}

uint64_t probe_cpu_counter(void)
{
    g_cpu.counters++;
    return 0;
}

uint64_t probe_cpu_counter_freq(void)
{
    g_cpu.counters++;
    return 1000;
}

void probe_cpu_park(void)
{
    fputs("probe_cpu_park reached in a policy test\n", stderr);
    exit(3);
}

static probe_cpu_list one_target(void)
{
    probe_cpu_list cpus;

    memset(&cpus, 0, sizeof cpus);
    cpus.count = 2;
    cpus.total_seen = 2;
    cpus.cpu[0].mpidr = 0x81000000ull;
    cpus.cpu[0].flags = 1;
    cpus.cpu[1].mpidr = 0x81000100ull;
    cpus.cpu[1].flags = 1;
    return cpus;
}

void test_psci_policy(void)
{
    static probe_psci_cell cell __attribute__((aligned(64)));
    probe_psci_facts f;
    probe_cpu_list cpus = one_target();

    section("psci entry policy");

#if !defined(__aarch64__)
    /* psci.c's SMC and secondary stub exist only for AArch64; the policy has
     * nothing to run on another host. Reported as skipped, never as passed. */
    puts("     SKIP: psci.c is AArch64-only");
    return;
#else
    /* The HVC conduit is refused before any call, target lookup or cache work. */
    memset(&g_cpu, 0, sizeof g_cpu);
    memset(&f, 0xA5, sizeof f);
    CHECK(run_hvc_conduit(&f, &cpus, &cell));
    CHECK_EQ(f.state, PROBE_PSCI_RUN_HVC_REFUSED);
    CHECK_EQ(f.enabled, 1);
    CHECK_EQ(f.version_called, 0);
    CHECK_EQ(f.have_target, 0);
    CHECK_EQ(f.cpu_on_called, 0);
    CHECK_EQ(f.arrived, 0);
    CHECK_EQ(g_cpu.reads + g_cpu.flushes + g_cpu.counters, 0);
    CHECK_EQ(cell.arrived, 0);

    /* No conduit, or one this image does not know, is a different refusal. */
    for (int conduit = 0; conduit < 8; conduit++) {
        if (conduit == PROBE_PSCI_SMC || conduit == PROBE_PSCI_HVC)
            continue;
        memset(&g_cpu, 0, sizeof g_cpu);
        probe_psci_run(&f, conduit, &cpus, 0x81000000ull, &cell);
        CHECK_EQ(f.state, PROBE_PSCI_RUN_NO_CONDUIT);
        CHECK_EQ(f.version_called, 0);
        CHECK_EQ(f.cpu_on_called, 0);
        CHECK_EQ(g_cpu.reads + g_cpu.flushes + g_cpu.counters, 0);
    }
    probe_psci_run(&f, -1, &cpus, 0x81000000ull, &cell);
    CHECK_EQ(f.state, PROBE_PSCI_RUN_NO_CONDUIT);
    probe_psci_run(&f, 0x7FFFFFFF, &cpus, 0x81000000ull, &cell);
    CHECK_EQ(f.state, PROBE_PSCI_RUN_NO_CONDUIT);

    /* The result has a name, and it is not the name of another state. */
    CHECK(PROBE_PSCI_RUN_HVC_REFUSED < PROBE_PSCI_RUN_STATE_COUNT);
    CHECK(PROBE_PSCI_RUN_HVC_REFUSED != PROBE_PSCI_RUN_NO_CONDUIT);
    CHECK(PROBE_PSCI_RUN_HVC_REFUSED != PROBE_PSCI_RUN_NOT_EL2);
#endif
}
