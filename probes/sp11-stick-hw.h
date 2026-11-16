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
 * not by a write instruction (INFERRED: nothing of the runner reads PAR_EL1; the el2mem payload's note says the same). Only the
 * opt-in GIC and stop variant adds writes, in a second block of this header under SNAP_SP11_STICK_GIC=1, one function per register
 * (docs/SP11-STICK-GIC.md section 4): with that gate at 0 the block does not exist.
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
/* The GIC and stop dry run (docs/SP11-STICK-GIC.md) is a second opt-in on top of the first: with it at 0, the default, nothing below it
 * is compiled and the dry-run wrapper of docs/SP11-STICK-WRAPPER.md keeps the bytes that ran on the unit. */
#ifndef SNAP_SP11_STICK_GIC
#define SNAP_SP11_STICK_GIC 0
#endif
#if SNAP_SP11_STICK_GIC != 0 && SNAP_SP11_STICK_GIC != 1
#error "SNAP_SP11_STICK_GIC accepts only 0 and 1"
#endif
#if SNAP_SP11_STICK_GIC && !SNAP_SP11_STICK
#error "SNAP_SP11_STICK_GIC=1 needs SNAP_SP11_STICK=1 (and so SNAP_PLATFORM_SP11=1): the GIC stage is a variant of the stick wrapper"
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

#if SNAP_SP11_STICK_GIC
/* ---- The GIC and stop dry run's part of the layer (docs/SP11-STICK-GIC.md section 4) ----
 * Every register the stage touches is one function (the dry run's own writes are defined in sp11-stick-gic-dry.c), so the host harness
 * sees each access and the disassembly check (tools/test-sp11-stick-gic-build.py) finds each instruction in one place with its callers.
 * Each write is out of line: one store, or one
 * MSR and an ISB, and nothing else. The stage reads before it writes and never writes a register it did not read first. The layer has no
 * write of ICC_SRE_EL2, no write of any EL2 control register but HCR_EL2 (class E, with VBAR_EL1 and SCTLR_EL1, and only for the EL1
 * excursion), no access to the ITS, no write to another CPU's redistributor frame and no read of one but GICR_TYPER (the stage computes
 * the frame, and the harness's model refuses the rest), and no function that waits (the stage bounds every wait on the counter). The
 * window routines and the vector tables are assembly (sp11-stick-gic-vec.c): they are the only code that unmasks IRQ, the only code that
 * enters EL1 and the only code that sets VBAR_EL2 other than the retry of a put-back.
 * Evidence: EXPERIMENTAL; hardware NOT RUN. The register names and the access sizes are INFERRED from the GICv3 architecture text and
 * from q1n1's gicv3.c and probes/secondary.c, which run on the A16; the specification was not re-read here. */
struct sw_gic_rec;      /* probes/sp11-stick-gic-record.h */

/* The writes of the dry run's own registers (class T: CNTHP; class E: HCR_EL2, VBAR_EL1, SCTLR_EL1), declared for both builds and defined once:
 * in sp11-stick-gic-dry.c on the unit, by the harness's model in the host test. The arm and put-back file reaches them only through its table
 * of registers, so the one MSR of each is in one function whichever file calls it (tools/test-sp11-stick-gic-build.py counts them). */
void sp11hw_write_cnthp_ctl_el2(uint64_t value);
void sp11hw_write_cnthp_cval_el2(uint64_t value);
void sp11hw_write_hcr_el2(uint64_t value);
void sp11hw_write_vbar_el1(uint64_t value);
void sp11hw_write_sctlr_el1(uint64_t value);

#ifdef SP11_STICK_HOST_TEST
/* The harness defines these over a model of the GIC, the CPU interface and the EL2 timer. */
uint8_t sp11hw_mmio_read8(uint64_t pa);
uint32_t sp11hw_mmio_read32(uint64_t pa);
uint64_t sp11hw_mmio_read64(uint64_t pa);
void sp11hw_mmio_write8(uint64_t pa, uint8_t value);
void sp11hw_mmio_write32(uint64_t pa, uint32_t value);
uint64_t sp11hw_read_spsel(void);
uint64_t sp11hw_read_daif(void);
uint64_t sp11hw_read_vbar_el2(void);
uint64_t sp11hw_read_icc_sre_el2(void);
uint64_t sp11hw_read_icc_ctlr_el1(void);
uint64_t sp11hw_read_icc_pmr_el1(void);
uint64_t sp11hw_read_icc_bpr1_el1(void);
uint64_t sp11hw_read_icc_igrpen1_el1(void);
uint64_t sp11hw_read_icc_hppir1_el1(void);
uint64_t sp11hw_read_icc_rpr_el1(void);
uint64_t sp11hw_read_icc_iar1_el1(void);
uint64_t sp11hw_read_cnthp_ctl_el2(void);
uint64_t sp11hw_read_cnthp_cval_el2(void);
void sp11hw_write_vbar_el2(uint64_t value);
void sp11hw_write_icc_pmr_el1(uint64_t value);
void sp11hw_write_icc_bpr1_el1(uint64_t value);
void sp11hw_write_icc_igrpen1_el1(uint64_t value);
void sp11hw_write_icc_eoir1_el1(uint64_t value);
void sp11hw_write_icc_dir_el1(uint64_t value);
uint64_t sp11hw_read_cnthctl_el2(void);
uint64_t sp11hw_read_vbar_el1(void);
uint64_t sp11hw_read_sctlr_el1(void);
uint64_t sp11hw_read_esr_el1(void);
uint64_t sp11hw_read_elr_el1(void);
uint64_t sp11hw_read_far_el1(void);
void sp11hw_make_visible(uint64_t start, uint64_t bytes);
void sp11hw_irq_mask(void);
uint64_t sp11hw_gic_vectors(void);
void sp11hw_irq_window(struct sw_gic_rec *rec, uint64_t vectors, uint64_t old_vbar, uint64_t deadline);
uint64_t sp11hw_el1_vectors(void);
uint64_t sp11hw_el1_image_bytes(void);
void sp11hw_el1_excursion(struct sw_gic_rec *rec, uint64_t vectors, uint64_t old_vbar, uint64_t resume_spsr, uint64_t bound_ticks);
#else
/* MMIO is volatile and naturally aligned. A write is an out-of-line function so that the binary shows it as one store instruction. */
static inline uint8_t sp11hw_mmio_read8(uint64_t pa) { return *(volatile uint8_t *)(uintptr_t)pa; }
static inline uint32_t sp11hw_mmio_read32(uint64_t pa) { return *(volatile uint32_t *)(uintptr_t)pa; }
static inline uint64_t sp11hw_mmio_read64(uint64_t pa) { return *(volatile uint64_t *)(uintptr_t)pa; }
static inline __attribute__((noinline)) void sp11hw_mmio_write8(uint64_t pa, uint8_t value)
{
    *(volatile uint8_t *)(uintptr_t)pa = value;
}
static inline __attribute__((noinline)) void sp11hw_mmio_write32(uint64_t pa, uint32_t value)
{
    *(volatile uint32_t *)(uintptr_t)pa = value;
}
static inline uint64_t sp11hw_read_spsel(void) { return SP11HW_MRS(spsel); }
static inline uint64_t sp11hw_read_daif(void) { return SP11HW_MRS(daif); }
static inline uint64_t sp11hw_read_vbar_el2(void) { return SP11HW_MRS(vbar_el2); }
static inline uint64_t sp11hw_read_icc_sre_el2(void) { return SP11HW_MRS(icc_sre_el2); }
static inline uint64_t sp11hw_read_icc_ctlr_el1(void) { return SP11HW_MRS(icc_ctlr_el1); }
static inline uint64_t sp11hw_read_icc_pmr_el1(void) { return SP11HW_MRS(icc_pmr_el1); }
static inline uint64_t sp11hw_read_icc_bpr1_el1(void) { return SP11HW_MRS(icc_bpr1_el1); }
static inline uint64_t sp11hw_read_icc_igrpen1_el1(void) { return SP11HW_MRS(icc_igrpen1_el1); }
static inline uint64_t sp11hw_read_icc_hppir1_el1(void) { return SP11HW_MRS(icc_hppir1_el1); }
static inline uint64_t sp11hw_read_icc_rpr_el1(void) { return SP11HW_MRS(icc_rpr_el1); }
static inline uint64_t sp11hw_read_cnthp_ctl_el2(void) { return SP11HW_MRS(cnthp_ctl_el2); }
static inline uint64_t sp11hw_read_cnthp_cval_el2(void) { return SP11HW_MRS(cnthp_cval_el2); }
static inline uint64_t sp11hw_read_cnthctl_el2(void) { return SP11HW_MRS(cnthctl_el2); }
static inline uint64_t sp11hw_read_vbar_el1(void) { return SP11HW_MRS(vbar_el1); }
static inline uint64_t sp11hw_read_sctlr_el1(void) { return SP11HW_MRS(sctlr_el1); }
/* EL1's own syndrome registers, read at EL2 to say which exception an EL1 vector reported (the table's HVC #1). */
static inline uint64_t sp11hw_read_esr_el1(void) { return SP11HW_MRS(esr_el1); }
static inline uint64_t sp11hw_read_elr_el1(void) { return SP11HW_MRS(elr_el1); }
static inline uint64_t sp11hw_read_far_el1(void) { return SP11HW_MRS(far_el1); }
/* The acknowledge: reading ICC_IAR1_EL1 makes the highest priority pending interrupt active, so it is the one read that changes state.
 * The DSB follows it as the monitor's own acknowledge does (probes/xnuboot/async.c). */
static inline __attribute__((noinline)) uint64_t sp11hw_read_icc_iar1_el1(void)
{
    uint64_t value;
    __asm__ volatile("mrs %0, icc_iar1_el1\n\tdsb sy" : "=r"(value) : : "memory");
    return value;
}
#define SP11HW_MSR_ISB(name, value) __asm__ volatile("msr " #name ", %0\n\tisb" : : "r"(value) : "memory")
static inline __attribute__((noinline)) void sp11hw_write_vbar_el2(uint64_t value) { SP11HW_MSR_ISB(vbar_el2, value); }
static inline __attribute__((noinline)) void sp11hw_write_icc_pmr_el1(uint64_t value) { SP11HW_MSR_ISB(icc_pmr_el1, value); }
static inline __attribute__((noinline)) void sp11hw_write_icc_bpr1_el1(uint64_t value) { SP11HW_MSR_ISB(icc_bpr1_el1, value); }
static inline __attribute__((noinline)) void sp11hw_write_icc_igrpen1_el1(uint64_t value) { SP11HW_MSR_ISB(icc_igrpen1_el1, value); }
static inline __attribute__((noinline)) void sp11hw_write_icc_eoir1_el1(uint64_t value) { SP11HW_MSR_ISB(icc_eoir1_el1, value); }
static inline __attribute__((noinline)) void sp11hw_write_icc_dir_el1(uint64_t value) { SP11HW_MSR_ISB(icc_dir_el1, value); }
/* The EL1 stub and its vector table are fetched by a core with its MMU and caches off: clean and invalidate their data lines to the point of
 * coherency, drop their instruction lines, and synchronise. 16 bytes is the smallest cache line the architecture allows, so a step of 16
 * reaches every line whatever the unit's line size is (q1n1's el1trap does the same). */
static inline __attribute__((noinline)) void sp11hw_make_visible(uint64_t start, uint64_t bytes)
{
    uint64_t at, end = start + bytes;
    for (at = start & ~(uint64_t)15; at < end; at += 16) __asm__ volatile("dc civac, %0" : : "r"(at) : "memory");
    __asm__ volatile("dsb sy" : : : "memory");
    for (at = start & ~(uint64_t)15; at < end; at += 16) __asm__ volatile("ic ivau, %0" : : "r"(at) : "memory");
    __asm__ volatile("dsb sy\n\tisb" : : : "memory");
}
/* PSTATE.I set: the put-back of DAIF, which the window routine left masked and which this only repeats if it did not hold. */
static inline __attribute__((noinline)) void sp11hw_irq_mask(void) { __asm__ volatile("msr daifset, #2\n\tisb" : : : "memory"); }
/* sp11-stick-gic-vec.c: the vector table (2 KiB aligned, in the payload image) and the window routine. */
extern const char sw_gic_vectors[];
void sw_gic_window(struct sw_gic_rec *rec, uint64_t vectors, uint64_t old_vbar, uint64_t deadline);
static inline uint64_t sp11hw_gic_vectors(void) { return (uint64_t)(uintptr_t)sw_gic_vectors; }
static inline void sp11hw_irq_window(struct sw_gic_rec *rec, uint64_t vectors, uint64_t old_vbar, uint64_t deadline)
{
    sw_gic_window(rec, vectors, old_vbar, deadline);
}
/* The EL1 excursion: the EL1 vector table (2 KiB aligned) and the stub, one after the other, then the routine that enters EL1 (all in
 * sp11-stick-gic-vec.c). The routine returns when the excursion ended, from a handler, with the state it had at its call. */
extern const char sw_gic_el1_vectors[], sw_gic_el1_end[];
void sw_gic_excursion(struct sw_gic_rec *rec, uint64_t vectors, uint64_t old_vbar, uint64_t resume_spsr, uint64_t bound_ticks);
static inline uint64_t sp11hw_el1_vectors(void) { return (uint64_t)(uintptr_t)sw_gic_el1_vectors; }
static inline uint64_t sp11hw_el1_image_bytes(void) { return (uint64_t)(uintptr_t)sw_gic_el1_end - (uint64_t)(uintptr_t)sw_gic_el1_vectors; }
static inline void sp11hw_el1_excursion(struct sw_gic_rec *rec, uint64_t vectors, uint64_t old_vbar, uint64_t resume_spsr, uint64_t bound_ticks)
{
    sw_gic_excursion(rec, vectors, old_vbar, resume_spsr, bound_ticks);
}
#endif
#endif /* SNAP_SP11_STICK_GIC */
#endif /* SNAP_SP11_STICK */
#endif /* SNAP_PROBES_SP11_STICK_HW_H */
