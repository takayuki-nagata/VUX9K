/*
 * Copyright (c) 2026 Takayuki Nagata
 * SPDX-License-Identifier: MIT
 */

.section .text.entry
.global _start
.type _start, @function

_start:
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

    /* Exceptions go to trap_entry, which reports them and stops (at its reset value
     * 0, mtvec sent them to _start: a silent restart over half-initialized state) */
    la t0, trap_entry
    .option push
    .option arch, +zicsr
    csrw mtvec, t0
    .option pop

    /* Jump to Rust main() */
    call main
5:
    j 5b

/* mtvec (direct mode) needs a 4-byte aligned handler. It never returns, so it takes
 * a fresh stack (the fault may have been the stack's) and saves nothing. */
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
6:
    j 6b
