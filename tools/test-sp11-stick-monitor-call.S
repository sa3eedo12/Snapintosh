/* SPDX-License-Identifier: MIT */
/* The boot stub of the execution test of sw_monitor_call, the trampoline that calls the monitor (probes/sp11-stick-monitor.c, the __asm__ block;
 * tools/test-sp11-stick-monitor-build.py, class Trampoline). Host and QEMU only: `-M virt,virtualization=on -cpu max` starts the CPU at EL2 with the
 * MMU off, and this stub runs the REAL bytes of sw_monitor_call, taken from the wrapper of the M1 build, against a fake monitor of its own.
 *
 * The flat image the test links at 0x40200000 (above QEMU's device tree) and loads with `-device loader`:
 *   0x0       this stub
 *   TRAMP_AT  the bytes of sw_monitor_call (TRAMP_FILE) at the offset they have in the wrapper's binary: the bl they end in is a PC-relative branch
 *             to MON_AT, the offset at which the build puts the monitor, so it resolves here as it does in SNAPMON-M1.BIN
 *   MON_AT    the fake monitor: an AAPCS64 leaf function that records its arguments and its SP and returns a known value
 * TRAMP_AT, MON_AT and TRAMP_FILE are assembler definitions (-D) that the test gives.
 *
 * Two calls, x0 to x4 and the caller's x19, x29 and x30 set to known values. The first hands the trampoline a stack of its own in x5, the second 0.
 * Checks, each printed as `CALLTEST CHECK <case> <name> PASS|FAIL` from what this stub finds on return:
 *   ARGS   the fake monitor saw x0 to x4 as passed       STACK  its SP was x5, or the caller's SP less the trampoline's 32-byte frame when x5 is 0
 *   ALIGN  that SP was 16-byte aligned                   RET    x0 came back as the fake monitor's value
 *   X19, X29, X30, SP  the caller's registers are as before the call (the ret went to the x30 the caller held, so X30 is also a path)
 * The caller does not take the trampoline's x19 as a given: x19 and x29 are overwritten inside it and only the saved copy brings them back. A
 * mutated trampoline fails the check that matches the mutation; a mutation that loses x30 never returns, and the test treats that as a failure.
 * This proves the instructions of the trampoline do what its comment says on a CPU model; it proves nothing about the unit, the monitor's own
 * code, or the caches and the MMU the unit has on at EL2 (here both are off). */
        .equ    UART, 0x09000000
        .text
        .global _start
_start:
        movz    x0, #0x4050, lsl #16            // the boot stack, in the RAM above the image
        mov     sp, x0
        mrs     x0, CurrentEL
        lsr     x20, x0, #2
        adr     x0, s_el
        bl      puts
        mov     x0, x20
        bl      puthex
        adr     x0, s_nl
        bl      puts
        cmp     x20, #2
        b.ne    finish
        adr     x22, s_stack
        movz    x20, #0x4040, lsl #16           // x5: a stack of its own, 16-byte aligned
        bl      run_case
        adr     x22, s_nostack
        mov     x20, #0                         // x5 = 0: the caller's stack stays in use
        bl      run_case
finish:
        adr     x0, s_done
        bl      puts
        movz    x0, #0x8400, lsl #16            // PSCI SYSTEM_OFF: the virt machine of QEMU takes it by SMC when it boots at EL2
        movk    x0, #0x0008
        smc     #0
1:      wfe
        b       1b

/* x20 is the x5 of the call and x22 the name of the case; x21 and x23 to x28 are this routine's. The trampoline and the fake monitor keep x20 to x28. */
run_case:
        stp     x30, xzr, [sp, #-16]!
        adr     x0, s_case
        bl      puts
        mov     x0, x22
        bl      puts
        adr     x0, s_nl
        bl      puts
        cbnz    x20, 1f
        sub     x21, sp, #32                    // the frame of the trampoline: x29, x30, x19 and a pad
        b       2f
1:      mov     x21, x20
2:      mov     x23, sp
        adr     x9, consts
        ldr     x10, [x9, #72]
        stp     x10, x10, [sp, #-16]            // the words that the frame of the trampoline will cover are not zero: a save it skips is a stale word
        stp     x10, x10, [sp, #-32]
        stp     x10, x10, [sp, #-48]
        adr     x11, results
        stp     xzr, xzr, [x11]
        stp     xzr, xzr, [x11, #16]
        stp     xzr, xzr, [x11, #32]
        stp     xzr, xzr, [x11, #48]
        ldp     x0, x1, [x9]
        ldp     x2, x3, [x9, #16]
        ldr     x4, [x9, #32]
        ldp     x19, x29, [x9, #48]
        mov     x5, x20
        adr     x30, after_call
        adr     x16, tramp
        br      x16
after_call:
        adr     x9, after_call
        cmp     x30, x9
        cset    x24, eq                         // X30, before any bl overwrites it
        adr     x9, consts
        ldr     x10, [x9, #64]
        cmp     x0, x10
        cset    x25, eq                         // RET
        mov     x10, sp
        cmp     x10, x23
        cset    x26, eq                         // SP
        ldr     x10, [x9, #48]
        cmp     x19, x10
        cset    x27, eq                         // X19
        ldr     x10, [x9, #56]
        cmp     x29, x10
        cset    x28, eq                         // X29
        mov     sp, x23                         // a trampoline that left SP elsewhere does not take the frame of this routine with it
        adr     x9, results
        adr     x10, consts
        ldp     x11, x12, [x9]
        ldp     x13, x14, [x10]
        cmp     x11, x13
        ccmp    x12, x14, #0, eq
        ldp     x11, x12, [x9, #16]
        ldp     x13, x14, [x10, #16]
        ccmp    x11, x13, #0, eq
        ccmp    x12, x14, #0, eq
        ldr     x11, [x9, #32]
        ldr     x13, [x10, #32]
        ccmp    x11, x13, #0, eq
        cset    x1, eq
        adr     x0, n_args
        bl      report
        adr     x9, results                     // puts uses x9
        ldr     x11, [x9, #40]
        cmp     x11, x21
        cset    x1, eq
        adr     x0, n_stack
        bl      report
        adr     x9, results
        ldr     x11, [x9, #40]
        tst     x11, #15
        cset    x1, eq
        adr     x0, n_align
        bl      report
        mov     x1, x25
        adr     x0, n_ret
        bl      report
        mov     x1, x27
        adr     x0, n_x19
        bl      report
        mov     x1, x28
        adr     x0, n_x29
        bl      report
        mov     x1, x24
        adr     x0, n_x30
        bl      report
        mov     x1, x26
        adr     x0, n_sp
        bl      report
        adr     x0, s_seen
        bl      puts
        mov     x0, x22
        bl      puts
        adr     x0, s_sp
        bl      puts
        adr     x9, results
        ldr     x0, [x9, #40]
        bl      puthex
        adr     x0, s_want
        bl      puts
        mov     x0, x21
        bl      puthex
        adr     x0, s_nl
        bl      puts
        ldp     x30, xzr, [sp], #16
        ret

/* x0 = the check's name, x1 = 1 for a pass; keeps x19 to x29. */
report:
        stp     x30, x1, [sp, #-16]!
        mov     x3, x0
        adr     x0, s_check
        bl      puts
        mov     x0, x22
        bl      puts
        adr     x0, s_space
        bl      puts
        mov     x0, x3
        bl      puts
        ldr     x1, [sp, #8]
        cmp     x1, #0
        adr     x0, s_pass
        adr     x2, s_fail
        csel    x0, x0, x2, ne
        bl      puts
        ldp     x30, x1, [sp], #16
        ret

/* x0 = a string; the PL011 of QEMU's virt machine sends a byte at once, with or without the setup a real one needs. Clobbers x0, x1 and x9. */
puts:
        movz    x9, #0x0900, lsl #16
1:      ldrb    w1, [x0], #1
        cbz     w1, 2f
        strb    w1, [x9]
        b       1b
2:      ret

/* x0 = a value, as sixteen hex digits. Clobbers x0 to x4 and x9. */
puthex:
        movz    x9, #0x0900, lsl #16
        mov     x2, #60
1:      lsr     x1, x0, x2
        and     x1, x1, #15
        add     x3, x1, #0x30
        add     x4, x1, #0x57
        cmp     x1, #10
        csel    x1, x3, x4, lt
        strb    w1, [x9]
        subs    x2, x2, #4
        b.ge    1b
        ret

s_el:       .asciz "CALLTEST EL "
s_case:     .asciz "CALLTEST CASE "
s_check:    .asciz "CALLTEST CHECK "
s_seen:     .asciz "CALLTEST SEEN "
s_sp:       .asciz " SP "
s_want:     .asciz " WANT "
s_done:     .asciz "CALLTEST DONE\n"
s_space:    .asciz " "
s_pass:     .asciz " PASS\n"
s_fail:     .asciz " FAIL\n"
s_nl:       .asciz "\n"
s_stack:    .asciz "stack"
s_nostack:  .asciz "nostack"
n_args:     .asciz "ARGS"
n_stack:    .asciz "STACK"
n_align:    .asciz "ALIGN"
n_ret:      .asciz "RET"
n_x19:      .asciz "X19"
n_x29:      .asciz "X29"
n_x30:      .asciz "X30"
n_sp:       .asciz "SP"
        .balign 8
consts:
        .quad   0x1111111111111110              // x0
        .quad   0x2222222222222221              // x1
        .quad   0x3333333333333332              // x2
        .quad   0x4444444444444443              // x3
        .quad   0x5555555555555554              // x4
        .quad   0
        .quad   0x1919191919191919              // the caller's x19
        .quad   0x2929292929292929              // the caller's x29
        .quad   0x600D600D600D600D              // what the fake monitor returns
        .quad   0xDEADDEADDEADDEAD              // fills the words below the caller's SP

        .org    TRAMP_AT
tramp:
        .incbin TRAMP_FILE

/* The fake monitor, entered by the trampoline's bl: x0 to x4 and SP are recorded, x19 to x30 are not touched (AAPCS64). */
        .org    MON_AT
fake_monitor:
        adr     x9, results
        stp     x0, x1, [x9]
        stp     x2, x3, [x9, #16]
        str     x4, [x9, #32]
        mov     x10, sp
        str     x10, [x9, #40]
        adr     x0, consts
        ldr     x0, [x0, #64]
        ret
        .balign 16
results:
        .skip   64
