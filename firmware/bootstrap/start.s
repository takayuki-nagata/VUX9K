/* RISC-V RV32I Bare-Metal Assembly Entry Point & Trap Handler */
.section .text.entry
.global _start
.type _start, @function

_start:
    /* Initialize Stack Pointer to top of Data RAM (0x20020000) - LUI opcode 0x37 */
    lui sp, 0x20020

    /* Disable interrupts */
    csrw mstatus, zero

    /* Set trap vector */
    la t0, trap_handler
    csrw mtvec, t0

    /* Zero out .bss section */
    la t0, _sbss
    la t1, _ebss
1:
    bgeu t0, t1, 2f
    sw zero, 0(t0)
    addi t0, t0, 4
    j 1b
2:

    /* Copy .data section from ROM to RAM */
    la t0, _sidata
    la t1, _sdata
    la t2, _edata
3:
    bgeu t1, t2, 4f
    lw t3, 0(t0)
    sw t3, 0(t1)
    addi t0, t0, 4
    addi t1, t1, 4
    j 3b
4:

    /* Call Rust main() function */
    call main

    /* Loop forever if main returns */
5:
    wfi
    j 5b

.global trap_handler
.type trap_handler, @function
trap_handler:
    /* Minimal exception/interrupt handler */
    mret
