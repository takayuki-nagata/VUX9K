/*
 * Copyright (c) 2026 Takayuki Nagata
 * SPDX-License-Identifier: MIT
 */

/* The first stage: code in I-RAM (the bitstream's boot ROM), data and stack in the
 * PSRAM below the second stage's window (fw_common::map::S1_RAM_BASE/BYTES). D-RAM is
 * not used: its preload must stay all zero (scripts/hw_smoke.py). */
MEMORY
{
  I_ROM (rx)  : ORIGIN = 0x00000000, LENGTH = 16K
  S1_RAM (rw) : ORIGIN = 0x806F0000, LENGTH = 64K
}

ENTRY(_start)

SECTIONS
{
  .text :
  {
    KEEP(*(.text.entry))
    *(.text)
    *(.text.*)
  } > I_ROM

  .rodata :
  {
    . = ALIGN(4);
    *(.rodata)
    *(.rodata.*)
    *(.srodata)
    *(.srodata.*)
    . = ALIGN(4);
  } > I_ROM

  .data :
  {
    . = ALIGN(4);
    _sdata = .;
    __global_pointer$ = . + 0x800;
    *(.data)
    *(.data.*)
    *(.sdata)
    *(.sdata.*)
    . = ALIGN(4);
    _edata = .;
  } > S1_RAM AT > I_ROM

  _sidata = LOADADDR(.data);

  .bss (NOLOAD) :
  {
    . = ALIGN(4);
    _sbss = .;
    *(.bss)
    *(.bss.*)
    *(.sbss)
    *(.sbss.*)
    *(COMMON)
    . = ALIGN(4);
    _ebss = .;
  } > S1_RAM

  .stack (NOLOAD) :
  {
    . = ALIGN(16);
    _stack_end = ORIGIN(S1_RAM) + LENGTH(S1_RAM);
    /* start.s zeroes the stack from here (the lockstep's XMEM_USED covers it too) */
    _stack_zero = _stack_end - 8K;
  } > S1_RAM

  /DISCARD/ :
  {
    *(.eh_frame)
  }
}
