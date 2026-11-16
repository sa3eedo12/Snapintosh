/* SPDX-License-Identifier: MIT */
#ifndef SNAP_PROBES_SP11_STICK_GIC_H
#define SNAP_PROBES_SP11_STICK_GIC_H
/* The GIC and stop stage of SNAPMON-GIC.BIN (docs/SP11-STICK-GIC.md): its parameters, the GICv3 register map it uses, the interface of
 * its two halves and the C halves of the vector table. Compiled only under -DSNAP_SP11_STICK_GIC=1 (probes/sp11-stick-hw.h), which needs
 * -DSNAP_SP11_STICK=1: with it at 0 this header is empty and the dry-run wrapper keeps the bytes that ran on the unit.
 *
 * What the stage is for. The A16 monitor stops its guest with the EL2 physical timer, a level-triggered PPI (INTID 26) routed to EL2
 * by HCR_EL2.IMO and taken from the guest at EL1. On the Surface Pro 11 that has never been tried (U3, docs/SP11-MONITOR-PORT.md): no
 * register of the GIC has been written there, GICD_CTLR reads 0x10 and no interrupt has ever been enabled. The stage tests the
 * mechanism and nothing else: it reads, decides, announces its first store and waits eight seconds, arms the GIC, then runs two
 * bounded windows, one at EL2 (three acknowledged interrupts, each re-armed a short time ahead, and a final deadline taken without
 * an acknowledge, as the monitor's stop path takes it) and one that takes the interrupt from EL1 at the lower-EL vector, puts
 * everything back and reports. It never calls a monitor.
 *
 * Two halves, one source of truth. sp11-stick-gic.c is the arm and the put-back: the reads and decisions, the stores of classes G, R
 * and C with their read-back, PREPARED, and the exact put-back of the same classes. It has no window, no timer and no write of
 * VBAR_EL2, HCR_EL2 or any EL1 register: the monitor entry (work item 4b) calls sw_gic_arm, then the monitor, then sw_gic_put_back,
 * and builds the file with SW_GIC_DRY_REGS=0, which removes the dry run's own registers from the table (tools/test-sp11-stick-gic-build.py
 * proves that object has none of their writes). sp11-stick-gic-dry.c is the dry run's own: classes T, V and E, the windows, the
 * verdict and the page. The monitor sets CNTHP, VBAR_EL2 and HCR_EL2 itself (probes/xnuboot.S:172-178), so 4b must not.
 *
 * Evidence: EXPERIMENTAL; hardware NOT RUN. The register map and the access rules are INFERRED from the GICv3 architecture text as
 * remembered and from q1n1's platform/uefi/gicv3.c and probes/secondary.c, which run on the A16; the specification was not re-read. */
#include "sp11-stick-hw.h"

#if SNAP_SP11_STICK_GIC
#include "sp11-stick-wrapper.h"
#include "sp11-stick-gic-record.h"

#ifndef SW_GIC_DRY_REGS
#define SW_GIC_DRY_REGS 1               /* the table knows the dry run's registers (classes T, V, E); a client that must not contain their writes builds with 0 */
#endif

/* ---- Parameters. A different value is a different binary and so a different authorisation (the record names the SHA-256). ---- */
#ifndef SP11_STICK_GIC_TIME_BOX_S
#define SP11_STICK_GIC_TIME_BOX_S 2u    /* each window's first timer is armed this long: short, so that the stage stays short; the M1 time box is 5 s (D5) */
#endif
_Static_assert(SP11_STICK_GIC_TIME_BOX_S >= 1 && SP11_STICK_GIC_TIME_BOX_S <= 5, "the time box is 1 to 5 s");
#ifndef SP11_STICK_GIC_SLACK_US
#define SP11_STICK_GIC_SLACK_US 1000u   /* an interrupt up to this long after the programmed deadline is ON_TIME: a label, not a limit */
#endif
_Static_assert(SP11_STICK_GIC_SLACK_US >= 1 && SP11_STICK_GIC_SLACK_US <= 100000u, "the slack is 1 us to 100 ms");
#ifndef SP11_STICK_GIC_CYCLE_US
#define SP11_STICK_GIC_CYCLE_US 10000u  /* each later interrupt of the EL2 window is armed this far ahead of the previous one's handler */
#endif
_Static_assert(SP11_STICK_GIC_CYCLE_US >= 1000u && SP11_STICK_GIC_CYCLE_US <= 100000u, "the re-arm interval is 1 to 100 ms");
#define SW_GIC_WAIT_S 8u                /* the NEXT lines' wait: a hang or a reset at the first store leaves a page that was already filmed */
#define SW_GIC_PRIORITY 0x80u           /* the A16 monitor's value (probes/secondary.c); below the mask in the non-secure view */
#define SW_GIC_PMR 0xF0u
#define SW_GIC_BPR1 0u
#define SW_GIC_COUNTER_STUCK_READS 2000000u     /* identical counter reads in a row: the counter is not running (q1n1's el1trap uses the same bound) */
#define SW_GIC_POLL_READS 100000u       /* at most this many reads of a register that must clear (RWP), and at most CNTFRQ/20 ticks (50 ms) */
#define SW_GIC_SETTLE_DIV 100000u       /* the PPI's state after an interrupt is read 1/this seconds (10 us) after the change that ended it: INFERRED that a level change reaches the GIC later than the instruction that made it */
#define SW_GIC_INTID_SPURIOUS 1020u     /* ICC_IAR1_EL1 values from here up are not interrupts: no EOI */
#define SW_GIC_ENTRY_LIMIT (SG_CYCLES + 3u)     /* a window whose vector was entered this often ends: a source that cannot be silenced is a storm */

/* ---- Registers. Offsets from the frame base named in the comment. ---- */
#define SG_GICD_CTLR 0x000u             /* distributor */
#define SG_GICD_TYPER 0x004u
#define SG_GICD_IIDR 0x008u
#define SG_GICD_CTLR_G1 (1u << 0)       /* non-secure view (DS = 0): EnableGrp1; single security state (DS = 1): EnableGrp0 */
#define SG_GICD_CTLR_G1A (1u << 1)      /* non-secure view with ARE_NS = 1: EnableGrp1A; DS = 1: EnableGrp1. The bit the A16 monitor requires */
#define SG_GICD_CTLR_ARE (1u << 4)      /* ARE_NS in the non-secure view, ARE when DS = 1 */
#define SG_GICD_CTLR_DS (1u << 6)       /* 0: two security states (EL3 firmware owns group 0 and secure group 1); 1: one */
#define SG_GICD_CTLR_E1NWF (1u << 7)
#define SG_GICD_CTLR_RWP (1u << 31)     /* register write pending */
#define SG_GICD_CTLR_KNOWN (SG_GICD_CTLR_G1 | SG_GICD_CTLR_G1A | SG_GICD_CTLR_ARE | SG_GICD_CTLR_DS | SG_GICD_CTLR_E1NWF)
#define SG_GICR_CTLR 0x000u             /* redistributor, RD_base */
#define SG_GICR_CTLR_RWP (1u << 3)
#define SG_GICR_TYPER 0x008u            /* 64 bits: affinity in [63:32], Last in bit 4 */
#define SG_GICR_TYPER_LAST (1u << 4)
#define SG_GICR_WAKER 0x014u
#define SG_GICR_WAKER_SLEEP (1u << 1)
#define SG_GICR_WAKER_CHILDREN_ASLEEP (1u << 2)
#define SG_SGI_FRAME 0x10000u           /* SGI_base = RD_base + this */
#define SG_IGROUPR0 0x080u              /* SGI_base: one bit per INTID 0 to 31 */
#define SG_ISENABLER0 0x100u
#define SG_ICENABLER0 0x180u
#define SG_ISPENDR0 0x200u
#define SG_ICPENDR0 0x280u
#define SG_ISACTIVER0 0x300u
#define SG_IPRIORITYR 0x400u            /* one byte per INTID */
#define SG_ICFGR1 0xC04u                /* two bits per INTID 16 to 31, the upper one meaning edge-triggered */
#define SG_ICFGR_EDGE 2u
#define SG_ICC_SRE_SRE (1u << 0)
#define SG_ICC_CTLR_EOIMODE (1u << 1)   /* ICC_CTLR_EL1.EOImode: priority drop and deactivation are separate (ICC_DIR_EL1) */
#define SG_HCR_IMO (1u << 4)            /* HCR_EL2.IMO: physical IRQ routed to EL2 */
#define SW_GIC_DAIF_MASK 0x3C0          /* D, A, I and F: written without a suffix because the vector's assembly takes it as an immediate */
#define SG_DAIF_MASKED SW_GIC_DAIF_MASK
#define SG_CNTHP_CTL_ENABLE 1u
#define SG_CNTHP_CTL_MASK 3u            /* ENABLE and IMASK: ISTATUS is read only */
#define SG_CNTHP_CTL_IMASK 2u
#define SG_CNTHP_CTL_STATE (SG_CNTHP_CTL_ENABLE | SG_CNTHP_CTL_IMASK | SG_CNTHP_ISTATUS)    /* the three bits of one read that say whether the line is high */
#define SG_CNTHP_CTL_FIRING (SG_CNTHP_CTL_ENABLE | SG_CNTHP_ISTATUS)    /* ENABLE and ISTATUS set, IMASK clear: the timer asserts its output now (INFERRED: ISTATUS ignores IMASK) */
#define SG_CNTHCTL_EL1PCTEN 1u          /* CNTHCTL_EL2.EL1PCTEN: EL1 and EL0 may read the physical counter (HCR_EL2.E2H = 0); OLD is 0x3 on the unit (OBSERVED) */

/* ---- The EL1 excursion (class E). The values are q1n1's el1trap payload's (payloads/el1trap.c; it ran on the unit, boot 11) except
 * HCR_EL2, which keeps IMO: RW, TGE clear and IMO set is what the monitor leaves while its guest runs. ---- */
#define SW_GIC_EL1_HCR UINT64_C(0x80000010)     /* RW | IMO; E2H, TGE, VM, DC, FMO and AMO clear */
#define SW_GIC_EL1_SCTLR UINT64_C(0x30500800)   /* MMU, caches, alignment checks off, as el1trap writes it (the unit reads bits 7 and 8 back set: OBSERVED) */
#define SW_GIC_EL1_SPSR_IMM 0x345               /* EL1h, D, A and F masked, I clear: the stub spins with IRQ unmasked (written bare: an assembler immediate) */
#define SW_GIC_EL1_SPSR ((uint64_t)SW_GIC_EL1_SPSR_IMM)
#define SW_GIC_EL2_MASKED UINT64_C(0x3C9)       /* EL2h with D, A, I and F masked: where the excursion's handlers return */
#define SG_HCR_CHECKED ((UINT64_C(1) << 31) | (UINT64_C(1) << 34) | (UINT64_C(1) << 27) | (UINT64_C(1) << 29) | UINT64_C(1) | (UINT64_C(1) << 12) | (UINT64_C(7) << 3))
                                        /* RW, E2H, TGE, HCD, VM, DC and FMO, IMO, AMO: the bits of the read-back that must be as written */
#define SG_SCTLR_CHECKED (UINT64_C(0x1) | UINT64_C(0x2) | UINT64_C(0x4) | UINT64_C(0x1000) | UINT64_C(0x80000) | UINT64_C(0x1000000) | UINT64_C(0x2000000))
                                        /* M, A, C, I, WXN, E0E, EE: the bits of SCTLR_EL1 that decide how the stub is fetched */
#define SG_EC_HVC64 0x16u               /* ESR_EL2.EC of an HVC from AArch64 */
#define SG_EL1_VEC_BASE 0x100u          /* an exception vector the EL1 table reported by HVC #1 is recorded as this plus its number */

/* ---- What the vector's C halves return, for the trampoline (sp11-stick-gic-vec.c) to apply to the exception's return state. ---- */
#define SW_VEC_MASK 1u                  /* return with D, A, I and F masked in SPSR_EL2: the window ends once the handler returns */
#define SW_VEC_EXIT 2u                  /* return to the window routine's exit, not to the instruction that was interrupted: no retry */
#define SW_VEC_TO_EL2 4u                /* the exception came from EL1: end the excursion, ERET to its exit in EL2h with the resume state (implies EXIT) */
#define SW_VEC_IRQ_SLOT 5               /* the vector table's current-EL, SPx, IRQ entry: offset 0x280 (written bare: an assembler immediate) */
#define SW_VEC_EL1_SYNC_SLOT 8          /* lower EL, AArch64, synchronous: offset 0x400 (the stub's HVC) */
#define SW_VEC_EL1_IRQ_SLOT 9           /* lower EL, AArch64, IRQ: offset 0x480, where the monitor takes its stop interrupt from the guest */

/* Test-only faults (tools/test-sp11-stick-gic.py proves the tests can fail by building each). They exist only in the host test build. */
#if (defined(SP11_STICK_GIC_SEED_NO_SAVE) || defined(SP11_STICK_GIC_SEED_STRAY_STORE) || defined(SP11_STICK_GIC_SEED_NO_EOI) || \
     defined(SP11_STICK_GIC_SEED_TIMER_ON) || defined(SP11_STICK_GIC_SEED_NO_IRQ_MASK) || defined(SP11_STICK_GIC_SEED_STORE_FIRST) || \
     defined(SP11_STICK_GIC_SEED_NO_NEXT) || defined(SP11_STICK_GIC_SEED_NO_RESTORE) || defined(SP11_STICK_GIC_SEED_FINAL_ACK) || \
     defined(SP11_STICK_GIC_SEED_NO_REARM) || defined(SP11_STICK_GIC_SEED_NO_IMO) || defined(SP11_STICK_GIC_SEED_NO_EL1_RESTORE) || \
     defined(SP11_STICK_GIC_SEED_NO_NEXT_EL1) || defined(SP11_STICK_GIC_SEED_UNBOUNDED_EL1) || defined(SP11_STICK_GIC_SEED_NO_SETTLE) || \
     defined(SP11_STICK_GIC_SEED_CLEAR_OTHERS) || defined(SP11_STICK_GIC_SEED_KEEP_FOREIGN_DEADLINE) || \
     defined(SP11_STICK_GIC_SEED_RESTORE_CTL_FIRST)) && !defined(SP11_STICK_HOST_TEST)
#error "the seeded faults are part of the host test build only"
#endif

/* The working state of the stage: the record as the vectors see it and as it is stored to REPORT.DAT, and what the reads decided. The
 * caller owns it (the dry run's lives in sw_gic_stage's frame; the monitor entry keeps one around the monitor call). */
struct sw_gic {
    struct sw_ctx *c;
    union {
        struct sw_gic_rec r;
        uint64_t w[SG_REC_BYTES / 8];
    } rec;
    uint64_t gicd, rd, sgi;             /* the distributor, this CPU's redistributor and its SGI/PPI frame */
    uint32_t ds, intid, bit, shift;     /* GICD_CTLR.DS; the PPI, its bit in the 32-bit registers, its field in ICFGR1 */
    uint64_t hz;
};
_Static_assert(sizeof(struct sw_gic) < 2048, "the stage's state is a small part of the wrapper's 32 KiB of stack");

/* ---- sp11-stick-gic.c: the arm and the put-back. ---- */
/* sw_gic_arm: reads, decisions, the NEXT line and the wait, then the stores of classes G, R and C with their read-back, and PREPARED. Returns
 * 0 (armed: the record's sub-stage is ARMED) or an SG_OUT_* refusal or failure, with the record's outcome and first_outcome set and nothing
 * printed but the read lines and the NEXT line. `extra` is the classes the caller will add (the dry run's T, V and E): the NEXT line names
 * them and the record plans them. After any non-zero return the caller calls sw_gic_put_back (a no-op when no class began) and then
 * refuses with GIC_NOT_READY and the outcome as refusal_detail. The three steps are public so that the dry run can put its own read-only
 * checks between the first and the second; a client that has none calls sw_gic_arm. */
unsigned sw_gic_arm_begin(struct sw_gic *g, struct sw_ctx *c);
unsigned sw_gic_arm_reads(struct sw_gic *g);
unsigned sw_gic_arm_stores(struct sw_gic *g, uint32_t extra);
unsigned sw_gic_arm(struct sw_gic *g, struct sw_ctx *c, uint32_t extra);
/* sw_gic_put_back: classes C, R and G exactly (each register written only if the stage wrote it, read back, one retry, then NOT_RESTORED in
 * restore_failed), then ICC_RPR_EL1 and the PPI's active state as checks. Returns 0, or SG_OUT_NOT_RESTORED. */
unsigned sw_gic_put_back(struct sw_gic *g);
/* The helpers the dry run shares with it, so that the record, the page and the put-back have one implementation. */
void sw_gic_flush(struct sw_gic *g);
void sw_gic_say(struct sw_gic *g, unsigned id, int essential);
void sw_gic_begin_class(struct sw_gic *g, uint32_t cls);
int sw_gic_wait_seconds(uint64_t hz, unsigned seconds);
void sw_gic_sync(void);
uint64_t sw_gic_current(const struct sw_gic *g, unsigned id);
void sw_gic_put_back_reg(struct sw_gic *g, unsigned id);

/* ---- sp11-stick-gic-dry.c: the dry run's own. ---- */
/* The stage: after COPIED, before DONE. Returns 0 when it ran to its end and put everything back, whatever the windows showed (the
 * verdict is in the record and on the page), and 0x1000 + GIC_NOT_READY (through sw_refuse) when it refused or could not hold or put
 * back a register: the head then names the stage reached and refusal_detail the outcome of sg_outcome. */
uint64_t sw_gic_stage(struct sw_ctx *c);
/* The vector table's C halves. The trampoline calls them with the record pointer the window routine keeps in x19; the host harness's
 * model calls them where the machine would take the exception. */
unsigned sw_gic_vec_irq(struct sw_gic_rec *rec, uint64_t elr, uint64_t spsr);
unsigned sw_gic_vec_irq_el1(struct sw_gic_rec *rec, uint64_t elr, uint64_t spsr);
unsigned sw_gic_vec_sync_el1(struct sw_gic_rec *rec, uint64_t esr, uint64_t elr, uint64_t spsr, uint64_t far, uint64_t el1_vector);
unsigned sw_gic_vec_other(struct sw_gic_rec *rec, uint64_t vector, uint64_t esr, uint64_t elr, uint64_t far, uint64_t spsr);
#endif /* SNAP_SP11_STICK_GIC */
#endif /* SNAP_PROBES_SP11_STICK_GIC_H */
