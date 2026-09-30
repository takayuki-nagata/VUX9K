/*
 * Copyright (c) 2026 Takayuki Nagata
 * SPDX-License-Identifier: MIT
 */

.section .text.entry
.global _start
.type _start, @function

_start:
    /* Heartbeat: LED pattern 0x01 before anything else, so a board that gets this far
     * but no further (no UART output) still shows that the CPU came out of reset */
    li t0, 0x40003000
    li t1, 1
    sw t1, 0(t0)
    /* Set Global Pointer and Stack Pointer */
    .option push
    .option norelax
    la gp, __global_pointer$
    .option pop
    la sp, _stack_end

    /* Zero initialize .bss section */
    la t0, _sbss
    la t1, _ebss
    bge t0, t1, 2f
1:
    sw zero, 0(t0)
    addi t0, t0, 4
    blt t0, t1, 1b
2:

    /* Copy .data from ROM (_sidata) to RAM (_sdata) */
    la t0, _sdata
    la t1, _edata
    la t2, _sidata
    bge t0, t1, 4f
3:
    lw t3, 0(t2)
    sw t3, 0(t0)
    addi t0, t0, 4
    addi t2, t2, 4
    blt t0, t1, 3b
4:

    /* Any exception stops at trap_stop (the Boot Manager leaves mtvec at 0, which
     * would silently restart the test instead) */
    la t0, trap_stop
    .option push
    .option arch, +zicsr
    csrw mtvec, t0
    .option pop

    /* Jump to Rust main() */
    call main
5:
    j 5b

/* LED pattern 0x2A, then halt */
.balign 4
trap_stop:
    li t0, 0x40003000
    li t1, 0x2A
    sw t1, 0(t0)
6:
    j 6b
