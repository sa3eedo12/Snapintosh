/* SPDX-License-Identifier: MIT */
/* The exception vector tables, the EL1 stub and the way into and out of EL1,
 * as assembly. Everything privileged the X2 image does at EL2 other than plain
 * register reads is in this file, so that the instruction audit of the built
 * image (probes/uefi/tests/host/audit.py) has one place to look.
 *
 * Memory map of this file's code, all in .text:
 *
 *   tests_el2_vectors   2 KiB aligned. VBAR_EL2 points here while a test runs.
 *                       Each of the 16 entries saves a 272-byte frame on the
 *                       stack (see struct tests_frame in guard.h) and calls
 *                       tests_el2_exception(frame, vector index) in
 *                       sys_aarch64.c; when that returns, the frame is
 *                       restored and the entry executes ERET.
 *   tests_el1_vectors   2 KiB aligned. VBAR_EL1 points here while the stub runs.
 *                       Every entry is `hvc #(0x100 + index)`, so an exception
 *                       taken at EL1 comes straight back to EL2 as an HVC and is
 *                       reported instead of vectoring into whatever VBAR_EL1
 *                       held before.
 *   tests_el1_stubs     the two EL1 programs (see nv2.h): the NV2-off control
 *                       and the redirect test. They use no stack and no memory.
 *   tests_el1_enter     saves the callee-saved registers on the EL2 stack and
 *                       ERETs to EL1. Control comes back to tests_el1_resume,
 *                       which the exception handler selects by editing the
 *                       frame's ELR and SPSR, so tests_el1_enter returns to its
 *                       caller like an ordinary function.
 *   tests_bus_ldr32     the guarded MMIO load, with SError unmasked around it
 *                       and a failure continuation (tests_bus_ldr32_resume) that
 *                       an SError taken during the load is sent to.
 *
 * The EL1 programs access TPIDR_EL2 (S3_4_C13_C0_2): a software thread
 * pointer with no side effect, and one of the registers FEAT_NV2 redirects to
 * the VNCR page, at offset 0x090 (Linux v6.12 vncr_mapping.h line 17).
 */
#include "nv2.h"

#define STR_(x) #x
#define XSTR_(x) STR_(x)

__asm__(
    ".section .text,\"xr\"\n"
    ".balign 2048\n"
    ".globl tests_el2_vectors\n"
    "tests_el2_vectors:\n"
    ".set vec_index, 0\n"
    ".rept 16\n"
    "    .balign 128\n"
    "    sub sp, sp, #272\n"
    "    stp x0, x1, [sp, #0]\n"
    "    mov x0, #vec_index\n"
    "    b tests_vec_common\n"
    "    .set vec_index, vec_index + 1\n"
    ".endr\n"
    ".balign 128\n"
    "tests_vec_common:\n"
    "    stp x2, x3, [sp, #16]\n"
    "    stp x4, x5, [sp, #32]\n"
    "    stp x6, x7, [sp, #48]\n"
    "    stp x8, x9, [sp, #64]\n"
    "    stp x10, x11, [sp, #80]\n"
    "    stp x12, x13, [sp, #96]\n"
    "    stp x14, x15, [sp, #112]\n"
    "    stp x16, x17, [sp, #128]\n"
    "    stp x18, x19, [sp, #144]\n"
    "    stp x20, x21, [sp, #160]\n"
    "    stp x22, x23, [sp, #176]\n"
    "    stp x24, x25, [sp, #192]\n"
    "    stp x26, x27, [sp, #208]\n"
    "    stp x28, x29, [sp, #224]\n"
    "    str x30, [sp, #240]\n"
    "    mrs x2, elr_el2\n"
    "    mrs x3, spsr_el2\n"
    "    stp x2, x3, [sp, #248]\n"
    "    mov x1, x0\n"
    "    mov x0, sp\n"
    "    bl tests_el2_exception\n"
    "    ldp x2, x3, [sp, #248]\n"
    "    msr elr_el2, x2\n"
    "    msr spsr_el2, x3\n"
    "    ldp x2, x3, [sp, #16]\n"
    "    ldp x4, x5, [sp, #32]\n"
    "    ldp x6, x7, [sp, #48]\n"
    "    ldp x8, x9, [sp, #64]\n"
    "    ldp x10, x11, [sp, #80]\n"
    "    ldp x12, x13, [sp, #96]\n"
    "    ldp x14, x15, [sp, #112]\n"
    "    ldp x16, x17, [sp, #128]\n"
    "    ldp x18, x19, [sp, #144]\n"
    "    ldp x20, x21, [sp, #160]\n"
    "    ldp x22, x23, [sp, #176]\n"
    "    ldp x24, x25, [sp, #192]\n"
    "    ldp x26, x27, [sp, #208]\n"
    "    ldp x28, x29, [sp, #224]\n"
    "    ldr x30, [sp, #240]\n"
    "    ldp x0, x1, [sp, #0]\n"
    "    add sp, sp, #272\n"
    "    eret\n"

    ".balign 2048\n"
    ".globl tests_el1_vectors\n"
    "tests_el1_vectors:\n"
    ".set el1_index, 0\n"
    ".rept 16\n"
    "    .balign 128\n"
    "    hvc #(0x100 + el1_index)\n"
    "    .set el1_index, el1_index + 1\n"
    ".endr\n"

    ".balign 16\n"
    ".globl tests_el1_stubs_begin\n"
    "tests_el1_stubs_begin:\n"
    ".globl tests_el1_prog_control\n"
    "tests_el1_prog_control:\n"
    "    mrs x1, tpidr_el2\n"
    "    hvc #" XSTR_(TESTS_HVC_DONE_CONTROL) "\n"
    "    b .\n"
    ".balign 16\n"
    ".globl tests_el1_prog_redirect\n"
    "tests_el1_prog_redirect:\n"
    "    movz x0, #" XSTR_(TESTS_NV2_PATTERN_W3) ", lsl #48\n"
    "    movk x0, #" XSTR_(TESTS_NV2_PATTERN_W2) ", lsl #32\n"
    "    movk x0, #" XSTR_(TESTS_NV2_PATTERN_W1) ", lsl #16\n"
    "    movk x0, #" XSTR_(TESTS_NV2_PATTERN_W0) "\n"
    "    msr tpidr_el2, x0\n"
    "    mrs x1, tpidr_el2\n"
    "    hvc #" XSTR_(TESTS_HVC_DONE_REDIRECT) "\n"
    "    b .\n"
    ".balign 16\n"
    ".globl tests_el1_stubs_end\n"
    "tests_el1_stubs_end:\n"

    ".balign 16\n"
    ".globl tests_el1_enter\n"
    "tests_el1_enter:\n"
    "    stp x29, x30, [sp, #-96]!\n"
    "    stp x19, x20, [sp, #16]\n"
    "    stp x21, x22, [sp, #32]\n"
    "    stp x23, x24, [sp, #48]\n"
    "    stp x25, x26, [sp, #64]\n"
    "    stp x27, x28, [sp, #80]\n"
    "    msr elr_el2, x0\n"
    "    msr spsr_el2, x1\n"
    "    eret\n"
    ".globl tests_el1_resume\n"
    "tests_el1_resume:\n"
    "    ldp x27, x28, [sp, #80]\n"
    "    ldp x25, x26, [sp, #64]\n"
    "    ldp x23, x24, [sp, #48]\n"
    "    ldp x21, x22, [sp, #32]\n"
    "    ldp x19, x20, [sp, #16]\n"
    "    ldp x29, x30, [sp], #96\n"
    "    ret\n"

    /* The guarded 32-bit MMIO read: SError is unmasked only around the load, so
     * that an error the bus reports asynchronously is taken here and attributed
     * to this access instead of arriving later in firmware code. If it is taken,
     * tests_el2_exception returns to tests_bus_ldr32_resume with SError masked
     * (never to the load, which an error that persists would hit again), so the
     * function always ends with the mask set. The result in w0 is meaningless
     * then; the C caller counts the SError as a failed read. */
    ".balign 16\n"
    ".globl tests_bus_ldr32\n"
    "tests_bus_ldr32:\n"
    "    msr daifclr, #4\n"
    "    ldr w0, [x0]\n"
    "    dsb sy\n"
    "    isb\n"
    ".globl tests_bus_ldr32_resume\n"
    "tests_bus_ldr32_resume:\n"
    "    msr daifset, #4\n"
    "    ret\n");
