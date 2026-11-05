/*
 * Copyright (c) 2026 Takayuki Nagata
 * SPDX-License-Identifier: MIT
 */

/* Entered from the first stage with a0-a2 (stage1's main.rs), which go on to main.
 * {UART_DATA}, {UART_STATUS} and {UART_TX_FULL} are main.rs's global_asm! operands: the
 * UART's registers before the liteuart layout (map::LEGACY_UART_STATUS), for the message
 * below. */
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

    /* The stage is built for RV32IM: on a CPU without M (misa bit 12, the v0.3.x
     * bitstreams) say so and stop before main, whose code may hold M instructions.
     * This loop is assembly, so it has none. Those bitstreams also have the UART's older
     * registers (data +0, status +4 with tx_full in bit 1), so it writes through them:
     * on a bitstream with RV32M the loop never runs. */
    csrr t0, misa
    .option pop
    srli t0, t0, 12
    andi t0, t0, 1
    bnez t0, 7f
    la t0, no_m_msg
    li t1, {UART_DATA}
    li t2, {UART_STATUS}
6:
    lbu t3, 0(t0)
    beqz t3, 3f
8:
    lw t4, 0(t2)
    andi t4, t4, {UART_TX_FULL}
    bnez t4, 8b
    sb t3, 0(t1)
    addi t0, t0, 1
    j 6b
7:
    call main
3:
    j 3b

.pushsection .rodata.no_m_msg
no_m_msg:
    .asciz "\nVUX9K Stage 2 needs a CPU with RV32M: program the v0.4.0 bitstream or later. Stopped.\n"
.popsection

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
