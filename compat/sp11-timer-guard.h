/* SPDX-License-Identifier: MIT */
/* SNAP_PLATFORM_SP11: the two refusals that stand behind the guest's counter and virtual timer.
 *
 * The A16 recipe gives the guest a 24 MHz counter on a 19.2 MHz host by trapping every guest read of the architectural
 * counter and virtual timer (probes/xnuboot/prepare.c): it sets CNTHCTL_EL2.EL1TVCT [14] and EL1TVT [13] (FEAT_ECV
 * level 1) and clears EL1PCTEN and EL1PCEN. It never read the register back, and the ECV test that precedes it
 * (ID_AA64MMFR0_EL1.ECV non-zero) passes whether or not those two bits do anything.
 *
 * What the Surface Pro 11 shows (OBSERVED, one unit): ECV field 2 (the X1 probe page), CNTHCTL_EL2 0x3 at the hand-off
 * (boots 4, 5 and 7), and CNTVOFF_EL2 0 on the boot CPU (boot 3) and on all eleven secondaries (boot 7). Whether bits 13
 * and 14 can be set at EL2, whether they trap EL1 and EL0 reads, and whether SCR_EL3.ECVEn gates them: UNKNOWN until
 * hardware tests T-HW1 and T-HW2 (docs/SP11-MONITOR-PORT.md section 6.4) run. Linux's "Broken CNTVOFF_EL2" erratum names
 * this MIDR (INFERRED prior; how the register is broken is UNKNOWN).
 *
 * Guard 1, CNTHCTL read-back: the register is read after every other register has been armed. Either bit clear means
 * the write did not stick; the monitor would then hand the guest an unscaled count, and the launch is refused with
 * SNAP_SP11_TIMER_STATUS_CNTHCTL. This catches "does not stick" and nothing else. A bit that reads back set and traps
 * nothing ("stuck but inert") passes here by construction: only an EL1 round trip that reads CNTVCT_EL0 and the virtual
 * timer under the traps (T-HW2) can show it, so this guard is never evidence that the traps work.
 *
 * Guard 2, CNTVOFF_EL2: the monitor's own clock is CNTVCT_EL0 read at EL2 (probes/xnuboot/sysreg_clock.c), which is the
 * physical count minus CNTVOFF_EL2 while HCR_EL2.E2H is 0, and timer_arm (probes/xnuboot/clock_timer.c) mixes it with
 * CNTPCT_EL0 absolutes. That is sound only while the offset is zero, and the monitor never programs it. A non-zero value
 * on this CPU refuses the launch with SNAP_SP11_TIMER_STATUS_CNTVOFF. Each CPU decides from its own register: a core
 * whose offset differs is refused rather than assumed equal to the boot CPU's.
 *
 * Refusal restores CNTHCTL_EL2 to the value q1n1 had (host_cnthctl). The report's own restore writes the same value
 * again, so the two are idempotent; this one keeps the guest value from outliving the decision that rejected it.
 * With both guards failing, the CNTHCTL status is returned and the EV_OTHER record keeps both values.
 *
 * Status numbers are the first two free ones after SNAP_PLATFORM_SP11's vCPU-count refusal (-146); probes/run.py
 * holds the band table and the sentences, and tools/test-sp11-integration.py pins them.
 *
 * The register accesses are two macros so that tools/test-sp11-timer-guard.c can run this very code against a modelled
 * world (bits that do not stick, an offset that is not zero) instead of a copy of it: a host that defines
 * SNAP_SP11_SYSREG_READ and SNAP_SP11_SYSREG_WRITE_ISB first replaces them. The monitor takes the instructions below.
 *
 * Compiled only under SNAP_PLATFORM_SP11: an A16 build preprocesses this file to nothing, and the monitor ELF stays
 * byte-identical (tools/monitor_baseline.py). No I/O, no allocation, no pointer tables. */
#ifndef SNAP_COMPAT_SP11_TIMER_GUARD_H
#define SNAP_COMPAT_SP11_TIMER_GUARD_H

#include "platform.h"

#if SNAP_PLATFORM_SP11

#include <stdint.h>

#define SNAP_SP11_TIMER_STATUS_CNTHCTL (-147)   /* EL1TVT or EL1TVCT did not read back set */
#define SNAP_SP11_TIMER_STATUS_CNTVOFF (-148)   /* CNTVOFF_EL2 is not zero on this CPU */

/* EV_OTHER pc of the record a refusal leaves: a = CNTHCTL_EL2 as read back, b = CNTVOFF_EL2, c = status | slot << 32.
 * Its own number, clear of the NV2 gate's 0x4e56, 0x4e57 and 0x5041 and of every small pc the A16 monitor logs. */
#define SNAP_SP11_EVENT_TIMER 0x5443

/* CNTHCTL_EL2 with HCR_EL2.E2H = 0 (DDI 0487, CNTHCTL_EL2): EL1TVT [13] traps EL0 and EL1 virtual timer accesses and
 * EL1TVCT [14] the virtual counter reads, both FEAT_ECV level 1. */
#define SNAP_SP11_CNTHCTL_EL1TVT   (UINT64_C(1) << 13)
#define SNAP_SP11_CNTHCTL_EL1TVCT  (UINT64_C(1) << 14)
#define SNAP_SP11_CNTHCTL_TRAPS    (SNAP_SP11_CNTHCTL_EL1TVT | SNAP_SP11_CNTHCTL_EL1TVCT)

/* What the guards saw, for the event record. */
struct snap_sp11_timer_view {
    uint64_t cnthctl;
    uint64_t cntvoff;
};

/* 0, or the refusal for these two register values. Both trap bits are required: one that sticks leaves the other
 * access path unscaled. */
static inline int64_t snap_sp11_timer_decide(uint64_t cnthctl, uint64_t cntvoff)
{
    if ((cnthctl & SNAP_SP11_CNTHCTL_TRAPS) != SNAP_SP11_CNTHCTL_TRAPS)
        return SNAP_SP11_TIMER_STATUS_CNTHCTL;
    if (cntvoff != 0)
        return SNAP_SP11_TIMER_STATUS_CNTVOFF;
    return 0;
}

#ifndef SNAP_SP11_SYSREG_READ
#define SNAP_SP11_SYSREG_READ(reg, out) __asm__ volatile("mrs %0, " #reg : "=r"(out))
#define SNAP_SP11_SYSREG_WRITE_ISB(reg, value) __asm__ volatile("msr " #reg ", %0; isb" :: "r"(value) : "memory")
#endif

/* Reads CNTHCTL_EL2 and CNTVOFF_EL2 on this CPU, stores both in *seen, and returns 0 or a SNAP_SP11_TIMER_STATUS_*.
 * On a refusal CNTHCTL_EL2 is written back to host_cnthctl, q1n1's value on this CPU, with an ISB. Call it once the
 * guest CNTHCTL_EL2 value is programmed and before the CPU is marked prepared. */
static inline int64_t snap_sp11_timer_guard(uint64_t host_cnthctl, struct snap_sp11_timer_view *seen)
{
    uint64_t cnthctl, cntvoff;
    int64_t refused;

    SNAP_SP11_SYSREG_READ(cnthctl_el2, cnthctl);
    SNAP_SP11_SYSREG_READ(cntvoff_el2, cntvoff);
    seen->cnthctl = cnthctl;
    seen->cntvoff = cntvoff;
    refused = snap_sp11_timer_decide(cnthctl, cntvoff);
    if (refused)
        SNAP_SP11_SYSREG_WRITE_ISB(cnthctl_el2, host_cnthctl);
    return refused;
}

#endif /* SNAP_PLATFORM_SP11 */
#endif
