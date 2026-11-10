/* SPDX-License-Identifier: MIT */
#include "secondary.h"
#include "../compat/platform.h"

/* Interrupt-controller facts the secondary CPU start depends on. The A16 values
 * are the ones tools/probe-a16-secondary.py requires of the measured A16 GICv4
 * distributor and redistributor frames, and they stay the literal tokens the
 * code always used, so the A16 object code does not change.
 *
 * SP11 (UNIMPLEMENTED) must not inherit them: its redistributor window ends
 * earlier (the public devicetree puts other MMIO behind the last frame) and the
 * timer INTID is unmeasured. An SP11 build therefore has no defaults; without
 * all five macros, supplied with -D from the board descriptor once the EL2
 * probe (plan item X2) has measured them, the start refuses to touch the GIC
 * and returns 0. See compat/topology.h for the discovery model that produces
 * them. */
#if SNAP_PLATFORM_SP11
#if defined(SNAP_GICD_BASE) && defined(SNAP_GICR_FIRST) && defined(SNAP_GICR_LIMIT) && \
    defined(SNAP_GICR_STRIDE) && defined(SNAP_TIMER_INTID)
#define SECONDARY_GIC_UNMEASURED 0
#else
#define SECONDARY_GIC_UNMEASURED 1
#endif
#else
#define SECONDARY_GIC_UNMEASURED 0
#define SNAP_GICD_BASE 0x17000000
#define SNAP_GICR_FIRST 0x17080000
#define SNAP_GICR_LIMIT 0x17500000
#define SNAP_GICR_STRIDE 0x40000
#define SNAP_TIMER_INTID 26
#endif

#if !SECONDARY_GIC_UNMEASURED
/* The timer is a per-CPU PPI: INTIDs 16..31 live in the redistributor SGI frame
 * registers this file programs. Its GICR_ICFGR1 field is two bits per INTID,
 * the upper bit meaning edge-triggered. The SGI frame sits one 64 KiB frame
 * above RD_base (g = r + 0x10000 below), so a stride under two frames would
 * alias the next CPU's RD_base. */
_Static_assert(SNAP_TIMER_INTID >= 16 && SNAP_TIMER_INTID <= 31, "secondary timer must be a PPI");
_Static_assert(SNAP_GICR_LIMIT > SNAP_GICR_FIRST && SNAP_GICR_STRIDE >= 0x20000 &&
               SNAP_GICR_STRIDE % 0x10000 == 0, "redistributor window");
#define SECONDARY_TIMER_BIT (1U<<(SNAP_TIMER_INTID))
#define SECONDARY_TIMER_EDGE_BIT (1U<<(((SNAP_TIMER_INTID)-16)*2+1))
static uint32_t r32(uint64_t p) { return *(volatile uint32_t *)p; }
static void w32(uint64_t p,uint32_t v) { *(volatile uint32_t *)p=v; }
#endif
static void barrier(void) { __asm__ volatile("dsb sy; isb" ::: "memory"); }
int64_t secondary_psci(uint64_t fn,uint64_t a,uint64_t b,uint64_t c)
{
    register uint64_t x0 __asm__("x0")=fn, x1 __asm__("x1")=a;
    register uint64_t x2 __asm__("x2")=b, x3 __asm__("x3")=c;
    __asm__ volatile("smc #0" : "+r"(x0),"+r"(x1),"+r"(x2),"+r"(x3)
        : : "x4","x5","x6","x7","x8","x9","x10","x11","x12",
            "x13","x14","x15","x16","x17","cc","memory");
    return (int64_t)x0;
}
#if SECONDARY_GIC_UNMEASURED
int secondary_gic_start(struct secondary *s)
{
    (void)s;
    return 0;
}
int secondary_gic_stop(struct secondary *s)
{
    __asm__ volatile("msr daifset, #15; msr cnthp_ctl_el2, xzr; isb");
    (void)s;
    return 1; /* start never saved any GIC state */
}
#else
static int wait_clear(uint64_t p,uint32_t mask)
{
    for (unsigned n=0;n<65535;n++) if (!(r32(p)&mask)) return 1;
    return 0;
}
int secondary_gic_start(struct secondary *s)
{
    uint64_t r=s->redistributor,g=r+0x10000,mpidr;
    __asm__ volatile("mrs %0, mpidr_el1":"=r"(mpidr));
    s->observed_affinity=mpidr&UINT64_C(0xff00ffffff);
    __asm__ volatile("mov %0, sp":"=r"(s->observed_sp));
    __asm__ volatile("mrs %0, sctlr_el2":"=r"(s->observed_regime[0]));
    __asm__ volatile("mrs %0, tcr_el2":"=r"(s->observed_regime[1]));
    __asm__ volatile("mrs %0, ttbr0_el2":"=r"(s->observed_regime[2]));
    __asm__ volatile("mrs %0, mair_el2":"=r"(s->observed_regime[3]));
    for (unsigned i=0;i<4;i++) if (s->observed_regime[i]!=s->regime[i]) return 0;
    if ((s->observed_sp&15) || s->observed_sp>=s->stack || s->stack-s->observed_sp>0x10000)
        return 0;
    if (s->observed_affinity!=s->affinity || r<SNAP_GICR_FIRST || r>=SNAP_GICR_LIMIT ||
        (r-SNAP_GICR_FIRST)%SNAP_GICR_STRIDE || (r32(SNAP_GICD_BASE)&0x80000012)!=0x12 ||
        (*(volatile uint64_t *)(r+8)>>32)!=((s->affinity&0xffffff)|((s->affinity>>32)<<24)))
        return 0;
    s->waker=r32(r+0x14); s->group=r32(g+0x80);
    s->priority=*(volatile uint8_t *)(g+0x41a);
    if (r32(g+0x100) || ((r32(g+0x200)|r32(g+0x300))&SECONDARY_TIMER_BIT) ||
        (r32(g+0xc04)&SECONDARY_TIMER_EDGE_BIT)) return 0;
    s->gic_saved=1;
    w32(r+0x14,(uint32_t)s->waker&~2U); barrier();
    if (!wait_clear(r+0x14,4)) return 0;
    w32(g+0x80,(uint32_t)s->group|SECONDARY_TIMER_BIT);
    *(volatile uint8_t *)(g+0x41a)=0x80;
    w32(g+0x280,SECONDARY_TIMER_BIT); w32(g+0x100,SECONDARY_TIMER_BIT); barrier();
    if (!wait_clear(r,8)) return 0;
    uint64_t sre;
    __asm__ volatile("mrs %0, icc_sre_el2":"=r"(sre));
    __asm__ volatile("msr icc_sre_el2, %0; isb"::"r"(sre|1));
    __asm__ volatile("mrs %0, icc_sre_el2":"=r"(sre));
    if (!(sre&1)) return 0;
    __asm__ volatile("msr icc_pmr_el1, %0; msr icc_bpr1_el1, xzr; "
                     "msr icc_igrpen1_el1, %1; isb"::"r"(UINT64_C(0xf0)),"r"(UINT64_C(1)));
    return 1;
}
int secondary_gic_stop(struct secondary *s)
{
    __asm__ volatile("msr daifset, #15; msr cnthp_ctl_el2, xzr; isb");
    if (!s->gic_saved) return 1;
    uint64_t r=s->redistributor,g=r+0x10000;
    w32(g+0x180,SECONDARY_TIMER_BIT);w32(g+0x280,SECONDARY_TIMER_BIT);barrier();
    if (!wait_clear(r,8)) return 0;
    /* A normal timer service already EOIs its interrupt. A terminal watchdog
     * may still be pending; no unrelated interrupt is acknowledged here. */
    uint64_t pending;
    __asm__ volatile("mrs %0, icc_hppir1_el1":"=r"(pending));
    if (pending==SNAP_TIMER_INTID) {
        uint64_t id,ctl;
        __asm__ volatile("mrs %0, icc_iar1_el1; dsb sy":"=r"(id)::"memory");
        if (id<1020) {
            __asm__ volatile("mrs %0, icc_ctlr_el1":"=r"(ctl));
            __asm__ volatile("msr icc_eoir1_el1, %0; isb"::"r"(id));
            if (ctl&2) __asm__ volatile("msr icc_dir_el1, %0; isb"::"r"(id));
        }
    }
    w32(g+0x280,SECONDARY_TIMER_BIT);w32(g+0x80,(uint32_t)s->group);
    *(volatile uint8_t *)(g+0x41a)=(uint8_t)s->priority;
    w32(r+0x14,(uint32_t)s->waker);barrier();
    s->cleanup=(r32(g+0x100)==0 && !(r32(g+0x300)&SECONDARY_TIMER_BIT) &&
        !(r32(g+0x200)&SECONDARY_TIMER_BIT) && r32(g+0x80)==s->group &&
        *(volatile uint8_t *)(g+0x41a)==s->priority &&
        (r32(r+0x14)&2)==(s->waker&2));
    return s->cleanup==1;
}
#endif
void secondary_power_off(struct secondary *s)
{
    barrier();
    for (uint64_t p=(uint64_t)s;p<(uint64_t)(s+1);p+=64)
        __asm__ volatile("dc cvac, %0"::"r"(p):"memory");
    __asm__ volatile("dsb sy":::"memory");
    s->off_result=(uint64_t)secondary_psci(0x84000002,0,0,0);
    __atomic_store_n(&s->phase,5,__ATOMIC_RELEASE); /* CPU_OFF returned: never reusable */
    for (;;) __asm__ volatile("wfe":::"memory");
}
