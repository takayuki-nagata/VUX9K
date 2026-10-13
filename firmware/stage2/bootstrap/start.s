/*
 * Copyright (c) 2026 Takayuki Nagata
 * SPDX-License-Identifier: MIT
 */

/* Entered from the first stage with a0-a2 (stage1's main.rs), which go on to main. */
.section .text.entry
.global _start
.type _start, @function

_start:
    .option push
    .option norelax
    la gp, __global_pointer$
    .option pop
    la sp, _stack_end

    /* Zero the stack's 8 KB and .bss: the PSRAM holds whatever was there (X in 4-state
     * simulation, which the PSRAM model flags when it is stored back) */
    la t0, _stack_zero
    la t1, _stack_end
5:
    sw zero, 0(t0)
    addi t0, t0, 4
    blt t0, t1, 5b

    la t0, _sbss
    la t1, _ebss
    bge t0, t1, 2f
1:
    sw zero, 0(t0)
    addi t0, t0, 4
    blt t0, t1, 1b
2:
    la t0, trap_entry
    .option push
    .option arch, +zicsr
    csrw mtvec, t0
    .option pop

    call main
3:
    j 3b

.balign 4
trap_entry:
    la sp, _stack_end
    .option push
    .option arch, +zicsr
    csrr a0, mcause
    csrr a1, mepc
    csrr a2, mtval
    .option pop
    call trap_report
4:
    j 4b
