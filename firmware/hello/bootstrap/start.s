/*
 * Copyright (c) 2026 Takayuki Nagata
 * SPDX-License-Identifier: MIT
 */

/* Entered from the second stage's `run` with a0-a2 as the first stage hands them to the
 * second stage, which go on to main. The trap vector stays the second stage's: it
 * reports an exception here too. */
.section .text.entry
.global _start
.type _start, @function

_start:
    .option push
    .option norelax
    la gp, __global_pointer$
    .option pop
    la sp, _stack_end

    la t0, _sbss
    la t1, _ebss
    bge t0, t1, 2f
1:
    sw zero, 0(t0)
    addi t0, t0, 4
    blt t0, t1, 1b
2:
    call main
3:
    j 3b
