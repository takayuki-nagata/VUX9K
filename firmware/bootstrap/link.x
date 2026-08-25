/*
 * Copyright (c) 2026 Takayuki Nagata
 * SPDX-License-Identifier: MIT
 */

/* Unified Linker Script for Tang Nano 9K SoC */
MEMORY
{
  I_ROM (rx)  : ORIGIN = 0x00000000, LENGTH = 8K
  D_RAM (rwx) : ORIGIN = 0x20000000, LENGTH = 4K
}

ENTRY(_start)

SECTIONS
{
  .text :
  {
    KEEP(*(.text.entry))
    *(.text)
    *(.text.*)
    *(.rodata)
    *(.rodata.*)
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
  } > D_RAM AT > I_ROM

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
  } > D_RAM

  .stack (NOLOAD) :
  {
    . = ALIGN(16);
    _stack_end = ORIGIN(D_RAM) + LENGTH(D_RAM);
  } > D_RAM

  /DISCARD/ :
  {
    *(.eh_frame)
  }
}
