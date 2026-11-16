/* SPDX-License-Identifier: MIT */
/* The vector tables, the window routines and the EL1 stub of the GIC and stop stage (docs/SP11-STICK-GIC.md, section 4): the only assembly of
 * SNAPMON-GIC.BIN, written as file-scope assembly in the way q1n1's payloads/el1trap.c writes its own EL2 and EL1 tables, which ran on the
 * unit as the first q1n1 runner payload below EL2 (OBSERVED, boot 11, one unit; the X2 image's T02 had run an EL1 stub with vector tables and an
 * ERET path on 2026-11-11, OBSERVED, docs/SURFACE-PRO-11.md). Compiled only under SNAP_SP11_STICK_GIC=1 and only for the unit: the host harness
 * replaces the routines with a model that calls the same C halves (sw_gic_vec_irq, sw_gic_vec_irq_el1, sw_gic_vec_sync_el1, sw_gic_vec_other).
 *
 * The window routine (window 1). It is the only code that changes VBAR_EL2 on purpose for the EL2 window, the only code that unmasks IRQ, and the
 * only code that runs while the table is installed, so that every exception taken under the table is one of its own and the record pointer it
 * keeps in x19 is valid by construction. Order: save x19 to x22, install the table, ISB, unmask IRQ, poll a flag the vector sets and CNTPCT_EL0
 * against the deadline, mask IRQ, ISB, put VBAR_EL2 back, ISB, return. There is no WFI: an interrupt that never comes ends the window at the
 * deadline. The poll uses no register but x0 to x5, x19 and x20, and the vector saves every register the C ABI lets a callee change, so the
 * handler cannot damage the loop.
 *
 * The excursion routine (window 2) is the same with an ERET to EL1 in place of the unmasked loop: it saves SPSR_EL2 and ELR_EL2 (scratch at EL2,
 * put back at its exit as el1trap does), installs the table, points ELR_EL2 at the stub and SPSR_EL2 at EL1h with D, A and F masked and I clear,
 * and ERETs with the stub's bound in x1. Every way back is a handler that ends the excursion by an ERET to the routine's exit at EL2h with the
 * resume state the caller computed (x21), so a lost interrupt ends at the stub's own counter bound, an HVC #0, and the stage is never left at EL1.
 *
 * The EL1 table and the stub. Sixteen entries each of `mov x16, #n; hvc #1`, so that any exception of EL1 itself is reported to EL2 by number;
 * the stub is position independent, makes no memory access and uses no stack, reads CNTPCT_EL0 (CNTHCTL_EL2.EL1PCTEN, checked by the stage
 * before any store), spins on it up to its bound from x1, and ends with HVC #0. It runs with the MMU and caches off, so the stage makes its lines
 * visible (DC CIVAC, IC IVAU) after the writes that make EL1 runnable.
 *
 * The EL2 table. Sixteen entries 128 bytes apart in the payload image, 2 KiB aligned (VBAR_ELx keeps bits 10:0 reserved; the runner loads the
 * file at a page and has synchronised its instruction cache over the whole image before the call, so the table is as visible to the fetch as the
 * code is). Each entry saves x0 and x1, numbers itself in x0 and joins one body. The body saves the rest of the caller-saved registers on the
 * stack the exception arrived on (SPSel is 1, a precondition), calls the C half and applies what it returns to the return state: bit 0 sets D,
 * A, I and F in SPSR_EL2, bit 1 sends the return to the window's exit (x22) instead of the interrupted instruction, bit 2 (an exception from
 * EL1) ends the excursion by returning to x22 with the resume state in x21, and then it restores the registers and ERETs. The entries expected
 * are slot 5 (current EL, IRQ, offset 0x280) in window 1, slot 9 (lower EL, IRQ, 0x480) and slot 8 (lower EL, synchronous, 0x400: the stub's
 * HVC) in window 2; any other entry is recorded and ends the window, with no retry. Written registers: VBAR_EL2, PSTATE.DAIF (the immediate
 * forms), SPSR_EL2 and ELR_EL2 (in the body, after the exception entry has overwritten both, and in the excursion's entry and exit).
 *
 * Evidence: EXPERIMENTAL; hardware NOT RUN. Under QEMU's EDK2 at EL2 the sequence runs (tools/test-sp11-stick-gic-qemu.py); that shows the
 * sequence, not the unit's EL3. */
#include "sp11-stick-hw.h"

#if SNAP_SP11_STICK_GIC && !defined(SP11_STICK_HOST_TEST)
#include "sp11-stick-gic.h"

#define STR_(x) #x
#define STR(x) STR_(x)
#define FRAME_BYTES 160         /* x0 to x18 and x30: ten pairs, a multiple of 16 so that SP stays aligned */

#define VECTOR(n) \
    "    .balign 128\n" \
    "    sub sp, sp, #" STR(FRAME_BYTES) "\n" \
    "    stp x0, x1, [sp, #0]\n" \
    "    mov x0, #" #n "\n" \
    "    b sw_gic_vec_common\n"

#define VECTOR_EL1(n) \
    "    .balign 128\n" \
    "    mov x16, #" #n "\n" \
    "    hvc #1\n"

__asm__(
    ".pushsection .text.sp11gic_machine,\"ax\",%progbits\n"
    "    .balign 2048\n"
    "    .globl sw_gic_vectors\n"
    "sw_gic_vectors:\n"
    VECTOR(0) VECTOR(1) VECTOR(2) VECTOR(3) VECTOR(4) VECTOR(5) VECTOR(6) VECTOR(7)
    VECTOR(8) VECTOR(9) VECTOR(10) VECTOR(11) VECTOR(12) VECTOR(13) VECTOR(14) VECTOR(15)

    /* The body. x0 = the entry's number, x0 and x1 are already in the frame, x19 = the record, x21 = the resume SPSR and x22 = the exit address
     * (the routines' contract: a C callee keeps them). Window 1's IRQ gets (record, ELR_EL2, SPSR_EL2) and so does window 2's; a lower-EL
     * synchronous entry gets (record, ESR_EL2, ELR_EL2, SPSR_EL2, FAR_EL2, x16, the EL1 table's number); any other gets (record, number, ESR_EL2,
     * ELR_EL2, FAR_EL2, SPSR_EL2). */
    "    .balign 16\n"
    "sw_gic_vec_common:\n"
    "    stp x2, x3, [sp, #16]\n"
    "    stp x4, x5, [sp, #32]\n"
    "    stp x6, x7, [sp, #48]\n"
    "    stp x8, x9, [sp, #64]\n"
    "    stp x10, x11, [sp, #80]\n"
    "    stp x12, x13, [sp, #96]\n"
    "    stp x14, x15, [sp, #112]\n"
    "    stp x16, x17, [sp, #128]\n"
    "    stp x18, x30, [sp, #144]\n"
    "    cmp x0, #" STR(SW_VEC_IRQ_SLOT) "\n"
    "    b.ne 1f\n"
    "    mov x0, x19\n"
    "    mrs x1, elr_el2\n"
    "    mrs x2, spsr_el2\n"
    "    bl sw_gic_vec_irq\n"
    "    b 5f\n"
    "1:  cmp x0, #" STR(SW_VEC_EL1_IRQ_SLOT) "\n"
    "    b.ne 2f\n"
    "    mov x0, x19\n"
    "    mrs x1, elr_el2\n"
    "    mrs x2, spsr_el2\n"
    "    bl sw_gic_vec_irq_el1\n"
    "    b 5f\n"
    "2:  cmp x0, #" STR(SW_VEC_EL1_SYNC_SLOT) "\n"
    "    b.ne 3f\n"
    "    mov x0, x19\n"
    "    mrs x1, esr_el2\n"
    "    mrs x2, elr_el2\n"
    "    mrs x3, spsr_el2\n"
    "    mrs x4, far_el2\n"
    "    mov x5, x16\n"
    "    bl sw_gic_vec_sync_el1\n"
    "    b 5f\n"
    "3:  mov x1, x0\n"
    "    mov x0, x19\n"
    "    mrs x2, esr_el2\n"
    "    mrs x3, elr_el2\n"
    "    mrs x4, far_el2\n"
    "    mrs x5, spsr_el2\n"
    "    bl sw_gic_vec_other\n"
    "5:  tbz x0, #2, 6f\n"
    "    msr spsr_el2, x21\n"
    "    msr elr_el2, x22\n"
    "    b 8f\n"
    "6:  tbz x0, #0, 7f\n"
    "    mrs x1, spsr_el2\n"
    "    orr x1, x1, #" STR(SW_GIC_DAIF_MASK) "\n"
    "    msr spsr_el2, x1\n"
    "7:  tbz x0, #1, 8f\n"
    "    msr elr_el2, x22\n"
    "8:  ldp x2, x3, [sp, #16]\n"
    "    ldp x4, x5, [sp, #32]\n"
    "    ldp x6, x7, [sp, #48]\n"
    "    ldp x8, x9, [sp, #64]\n"
    "    ldp x10, x11, [sp, #80]\n"
    "    ldp x12, x13, [sp, #96]\n"
    "    ldp x14, x15, [sp, #112]\n"
    "    ldp x16, x17, [sp, #128]\n"
    "    ldp x18, x30, [sp, #144]\n"
    "    ldp x0, x1, [sp, #0]\n"
    "    add sp, sp, #" STR(FRAME_BYTES) "\n"
    "    eret\n"

    /* void sw_gic_window(struct sw_gic_rec *rec /x0/, uint64_t vectors /x1/, uint64_t old_vbar /x2/, uint64_t deadline /x3/). */
    "    .balign 16\n"
    "    .globl sw_gic_window\n"
    "sw_gic_window:\n"
    "    stp x29, x30, [sp, #-48]!\n"
    "    stp x19, x20, [sp, #16]\n"
    "    stp x21, x22, [sp, #32]\n"
    "    mov x29, sp\n"
    "    mov x19, x0\n"
    "    mov x20, x2\n"
    "    adr x22, sw_gic_window_exit\n"
    "    msr vbar_el2, x1\n"
    "    isb\n"
    "    msr daifclr, #2\n"
    "1:  ldr w4, [x19, #" STR(SW_GIC_REC_DONE) "]\n"
    "    cbnz w4, 2f\n"
    "    mrs x5, cntpct_el0\n"
    "    sub x5, x5, x3\n"
    "    tbnz x5, #63, 1b\n"
    "2:  msr daifset, #2\n"
    "    isb\n"
    "    .globl sw_gic_window_exit\n"
    "sw_gic_window_exit:\n"
    "    msr vbar_el2, x20\n"
    "    isb\n"
    "    ldp x21, x22, [sp, #32]\n"
    "    ldp x19, x20, [sp, #16]\n"
    "    ldp x29, x30, [sp], #48\n"
    "    ret\n"

    /* void sw_gic_excursion(struct sw_gic_rec *rec /x0/, uint64_t vectors /x1/, uint64_t old_vbar /x2/, uint64_t resume_spsr /x3/,
     * uint64_t bound_ticks /x4/). */
    "    .balign 16\n"
    "    .globl sw_gic_excursion\n"
    "sw_gic_excursion:\n"
    "    stp x29, x30, [sp, #-64]!\n"
    "    stp x19, x20, [sp, #16]\n"
    "    stp x21, x22, [sp, #32]\n"
    "    mrs x5, spsr_el2\n"
    "    mrs x6, elr_el2\n"
    "    stp x5, x6, [sp, #48]\n"
    "    mov x29, sp\n"
    "    mov x19, x0\n"
    "    mov x20, x2\n"
    "    mov x21, x3\n"
    "    adr x22, sw_gic_excursion_exit\n"
    "    msr vbar_el2, x1\n"
    "    isb\n"
    "    adr x5, sw_gic_el1_stub\n"
    "    msr elr_el2, x5\n"
    "    mov x5, #" STR(SW_GIC_EL1_SPSR_IMM) "\n"
    "    msr spsr_el2, x5\n"
    "    mov x1, x4\n"
    "    eret\n"
    "    .globl sw_gic_excursion_exit\n"
    "sw_gic_excursion_exit:\n"
    "    msr vbar_el2, x20\n"
    "    isb\n"
    "    ldp x5, x6, [sp, #48]\n"
    "    msr spsr_el2, x5\n"
    "    msr elr_el2, x6\n"
    "    ldp x21, x22, [sp, #32]\n"
    "    ldp x19, x20, [sp, #16]\n"
    "    ldp x29, x30, [sp], #64\n"
    "    ret\n"

    /* The EL1 table and the stub, in one range that the stage makes visible: sw_gic_el1_vectors to sw_gic_el1_end. */
    "    .balign 2048\n"
    "    .globl sw_gic_el1_vectors\n"
    "sw_gic_el1_vectors:\n"
    VECTOR_EL1(0) VECTOR_EL1(1) VECTOR_EL1(2) VECTOR_EL1(3) VECTOR_EL1(4) VECTOR_EL1(5) VECTOR_EL1(6) VECTOR_EL1(7)
    VECTOR_EL1(8) VECTOR_EL1(9) VECTOR_EL1(10) VECTOR_EL1(11) VECTOR_EL1(12) VECTOR_EL1(13) VECTOR_EL1(14) VECTOR_EL1(15)
    "    .balign 16\n"
    "    .globl sw_gic_el1_stub\n"
    "sw_gic_el1_stub:\n"
    "    mrs x2, cntpct_el0\n"
    "    add x3, x2, x1\n"
    "1:  mrs x4, cntpct_el0\n"
    "    sub x4, x4, x3\n"
    "    tbnz x4, #63, 1b\n"
    "    hvc #0\n"
    "    udf #0\n"
    "    .globl sw_gic_el1_end\n"
    "sw_gic_el1_end:\n"
    ".popsection\n");
#endif /* SNAP_SP11_STICK_GIC && !SP11_STICK_HOST_TEST */
