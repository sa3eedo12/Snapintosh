/* SPDX-License-Identifier: MIT */
#ifndef SNAP_PROBES_SP11_STICK_HW_H
#define SNAP_PROBES_SP11_STICK_HW_H
/* Everything the SP11 stick wrapper (SNAPMON.BIN) and reporter (SNAPREP.BIN) do to the machine, in one header
 * (docs/SP11-STICK-WRAPPER.md, section 4): register reads, cache maintenance by VA, address-translation probes, the one
 * store to RAM and the read view of RAM. Two implementations of the same functions exist. The unit's is inline assembly and
 * plain pointers below; the host harness (tools/test-sp11-stick-wrapper.c, built with SP11_STICK_HOST_TEST) defines the same
 * functions over a fake machine, so the wrapper source that is flashed is the source that is exercised, and the harness sees
 * every store and every cache operation.
 *
 * What the layer does not have is the point: no function writes a system register, a GIC or an MMIO register. The store classes
 * of the dry run are RAM (the placement and REPORT.DAT, through sp11hw_store64), cache maintenance by VA, and the two AT
 * instructions, whose result lands in PAR_EL1: the one register the dry run changes, as a side effect of the instruction and
 * not by a write instruction (INFERRED: nothing of the runner reads PAR_EL1; the el2mem payload's note says the same).
 *
 * Physical and virtual addresses are the same here: EL2's stage 1 is the firmware's identity map. sw_probe_range() asks the MMU
 * about every page it samples and refuses one that is not an identity mapping, so the assumption is checked, not trusted.
 *
 * The gate. Everything of the stick wrapper is compiled only under -DSNAP_SP11_STICK=1, which needs -DSNAP_PLATFORM_SP11=1 and
 * accepts no value but 0 and 1 (docs/SP11-STICK-BUNDLE.md, section 7). Every file includes this header first, so the checks
 * sit in one place; with the gate at 0 each file compiles to nothing.
 *
 * Evidence: EXPERIMENTAL; hardware NOT RUN. */
#ifndef SNAP_PLATFORM_SP11
#define SNAP_PLATFORM_SP11 0
#endif
#ifndef SNAP_SP11_STICK
#define SNAP_SP11_STICK 0
#endif
#if SNAP_SP11_STICK != 0 && SNAP_SP11_STICK != 1
#error "SNAP_SP11_STICK accepts only 0 and 1"
#endif
#if SNAP_SP11_STICK && !SNAP_PLATFORM_SP11
#error "SNAP_SP11_STICK=1 needs SNAP_PLATFORM_SP11=1: the wrapper runs from the Surface Pro 11's boot stick"
#endif

#if SNAP_SP11_STICK
#include <stddef.h>
#include <stdint.h>

#ifdef SP11_STICK_HOST_TEST
/* The harness defines these over a fake machine. */
uint64_t sp11hw_read_currentel(void);
uint64_t sp11hw_read_midr(void);
uint64_t sp11hw_read_mpidr(void);
uint64_t sp11hw_read_cntfrq(void);
uint64_t sp11hw_read_hcr_el2(void);
uint64_t sp11hw_read_sctlr_el2(void);
uint64_t sp11hw_read_tcr_el2(void);
uint64_t sp11hw_read_ctr_el0(void);
uint64_t sp11hw_ticks(void);
void sp11hw_dc_cvac(uint64_t va);
void sp11hw_dc_civac(uint64_t va);
void sp11hw_dsb_sy(void);
void sp11hw_isb(void);
uint64_t sp11hw_at_s1e2w(uint64_t va);
uint64_t sp11hw_at_s1e2r(uint64_t va);
const void *sp11hw_view(uint64_t pa, uint64_t size);
void sp11hw_store64(uint64_t pa, uint64_t value);
void sp11hw_store8(uint64_t pa, uint8_t value);     /* only the seeded faults of the host test use it */
#else
#define SP11HW_MRS(name) ({ uint64_t value_; __asm__ volatile("mrs %0, " #name : "=r"(value_)); value_; })
static inline uint64_t sp11hw_read_currentel(void) { return SP11HW_MRS(currentel); }
static inline uint64_t sp11hw_read_midr(void) { return SP11HW_MRS(midr_el1); }
static inline uint64_t sp11hw_read_mpidr(void) { return SP11HW_MRS(mpidr_el1); }
static inline uint64_t sp11hw_read_cntfrq(void) { return SP11HW_MRS(cntfrq_el0); }
static inline uint64_t sp11hw_read_hcr_el2(void) { return SP11HW_MRS(hcr_el2); }
static inline uint64_t sp11hw_read_sctlr_el2(void) { return SP11HW_MRS(sctlr_el2); }
static inline uint64_t sp11hw_read_tcr_el2(void) { return SP11HW_MRS(tcr_el2); }
static inline uint64_t sp11hw_read_ctr_el0(void) { return SP11HW_MRS(ctr_el0); }
static inline uint64_t sp11hw_ticks(void) { return SP11HW_MRS(cntpct_el0); }
static inline void sp11hw_dc_cvac(uint64_t va) { __asm__ volatile("dc cvac, %0" : : "r"(va) : "memory"); }
static inline void sp11hw_dc_civac(uint64_t va) { __asm__ volatile("dc civac, %0" : : "r"(va) : "memory"); }
static inline void sp11hw_dsb_sy(void) { __asm__ volatile("dsb sy" : : : "memory"); }
static inline void sp11hw_isb(void) { __asm__ volatile("isb" : : : "memory"); }
/* AT translates through EL2's stage 1 as an access at EL2 would, takes no abort and makes no access at the address: a missing
 * mapping, or a walk that ends in an external abort, comes back in PAR_EL1.F. The ISB must separate it from the read of PAR_EL1. */
static inline uint64_t sp11hw_at_s1e2w(uint64_t va)
{
    uint64_t par;
    __asm__ volatile("at s1e2w, %1\n\tisb\n\tmrs %0, par_el1" : "=r"(par) : "r"(va) : "memory");
    return par;
}
static inline uint64_t sp11hw_at_s1e2r(uint64_t va)
{
    uint64_t par;
    __asm__ volatile("at s1e2r, %1\n\tisb\n\tmrs %0, par_el1" : "=r"(par) : "r"(va) : "memory");
    return par;
}
static inline const void *sp11hw_view(uint64_t pa, uint64_t size)
{
    (void)size;
    return (const void *)(uintptr_t)pa;
}
/* The only instruction of the binary that stores through a pointer made from an address: one function, so the disassembly check
 * (tools/test-sp11-stick-wrapper-build.py) can show that the wrapper's stores to RAM are these and the stack's. volatile keeps the
 * compiler from merging, widening or reordering the stores it is meant to emit one by one. */
static inline __attribute__((noinline)) void sp11hw_store64(uint64_t pa, uint64_t value)
{
    *(volatile uint64_t *)(uintptr_t)pa = value;
}
#endif

/* ---- Shared by both implementations ---- */

/* The stride of a maintenance loop: the smallest data cache line CTR_EL0.DminLine reports, held between 16 and 64 bytes. A stride
 * above the real line would skip lines without any sign, so a large or implausible value is clamped; a stride below it only costs
 * time. This core's DminLine is UNKNOWN (never read on the unit). */
static inline uint64_t sp11hw_line_from_ctr(uint64_t ctr)
{
    uint64_t bytes = (uint64_t)4 << ((ctr >> 16) & 15);
    return bytes < 16 ? 16 : bytes > 64 ? 64 : bytes;
}

/* Clean (DC CVAC) or clean and invalidate (DC CIVAC) every line [pa, pa + size) touches, by VA to the point of coherency. The caller
 * has bounded the range, so pa + size cannot wrap. */
static inline void sp11hw_clean_range(uint64_t pa, uint64_t size)
{
    uint64_t line = sp11hw_line_from_ctr(sp11hw_read_ctr_el0()), at = pa & ~(line - 1), end = pa + size;
    for (; size && at < end; at += line) sp11hw_dc_cvac(at);
}
static inline void sp11hw_flush_range(uint64_t pa, uint64_t size)
{
    uint64_t line = sp11hw_line_from_ctr(sp11hw_read_ctr_el0()), at = pa & ~(line - 1), end = pa + size;
    for (; size && at < end; at += line) sp11hw_dc_civac(at);
}

/* PAR_EL1 as an AT leaves it: bit 0 F; with F set the fault status is bits 6:1; with F clear the memory attribute is bits 63:56
 * (a MAIR byte already, not an index), the shareability bits 8:7 and the output address bits 51:12. Decoded as the el2mem payload
 * does, which checked the fields against Linux's arch/arm64/include/asm/sysreg.h; the Arm ARM was not re-read here. */
struct sp11hw_walk {
    uint64_t pa;
    uint8_t mapped, attr, sh, fst, ident;
};
static inline void sp11hw_walk_decode(uint64_t par, uint64_t va, struct sp11hw_walk *w)
{
    w->mapped = !(par & 1);
    w->pa = w->mapped ? par & UINT64_C(0x000FFFFFFFFFF000) : 0;
    w->attr = w->mapped ? (uint8_t)(par >> 56) : 0;
    w->sh = w->mapped ? (uint8_t)(par >> 7 & 3) : 0;
    w->fst = w->mapped ? 0 : (uint8_t)(par >> 1 & 63);
    w->ident = w->mapped && w->pa == (va & UINT64_C(0x000FFFFFFFFFF000));
}
/* Normal write-back: both nibbles of the MAIR byte are 0b11xx (non-transient write-back). Device has a zero high nibble. */
enum { SP11HW_ATTR_DEVICE, SP11HW_ATTR_NC, SP11HW_ATTR_WT, SP11HW_ATTR_WB, SP11HW_ATTR_OTHER };
static inline unsigned sp11hw_attr_class(uint8_t attr)
{
    unsigned outer = attr >> 4, inner = attr & 15u;
    if (outer == 0) return (inner & 3u) == 0 ? SP11HW_ATTR_DEVICE : SP11HW_ATTR_OTHER;
    if (outer == 4 && inner == 4) return SP11HW_ATTR_NC;
    if (outer >> 2 == 2 && inner >> 2 == 2) return SP11HW_ATTR_WT;
    if (outer >> 2 == 3 && inner >> 2 == 3) return SP11HW_ATTR_WB;
    return SP11HW_ATTR_OTHER;
}
#endif /* SNAP_SP11_STICK */
#endif /* SNAP_PROBES_SP11_STICK_HW_H */
