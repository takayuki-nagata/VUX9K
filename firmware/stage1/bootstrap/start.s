/*
 * Copyright (c) 2026 Takayuki Nagata
 * SPDX-License-Identifier: MIT
 */

.section .text.entry
.global _start
.type _start, @function

_start:
    /* The first stage: POR and every soft reset start here. The register file is not
     * reset and the PSRAM holds whatever was there (both X in 4-state simulation, where
     * the PSRAM model flags X written to it): zero every register, the stack's 8 KB (its
     * deepest use is ~6 KB) and .bss, so nothing undefined is ever stored. */
    li x1, 0
    li x3, 0
    li x4, 0
    li x5, 0
    li x6, 0
    li x7, 0
    li x8, 0
    li x9, 0
    li x10, 0
    li x11, 0
    li x12, 0
    li x13, 0
    li x14, 0
    li x15, 0
    li x16, 0
    li x17, 0
    li x18, 0
    li x19, 0
    li x20, 0
    li x21, 0
    li x22, 0
    li x23, 0
    li x24, 0
    li x25, 0
    li x26, 0
    li x27, 0
    li x28, 0
    li x29, 0
    li x30, 0
    li x31, 0

    /* Set Global Pointer and Stack Pointer */
    .option push
    .option norelax
    la gp, __global_pointer$
    .option pop
    la sp, _stack_end

    /* Zero the stack */
    la t0, _stack_zero
    la t1, _stack_end
7:
    sw zero, 0(t0)
    addi t0, t0, 4
    blt t0, t1, 7b

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
