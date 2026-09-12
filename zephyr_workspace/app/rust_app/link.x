/*
 * Copyright (c) 2026 Takayuki Nagata
 * SPDX-License-Identifier: Apache-2.0
 */

MEMORY
{
  I_RAM (rx)  : ORIGIN = 0x00000000, LENGTH = 18K
  D_RAM (rwx) : ORIGIN = 0x20000000, LENGTH = 8K
}

ENTRY(_start)

SECTIONS
{
  .text :
  {
    KEEP(*(.text.entry))
    *(.text)
    *(.text.*)
  } > I_RAM

  .rodata :
  {
    . = ALIGN(4);
    *(.rodata)
    *(.rodata.*)
    *(.srodata)
    *(.srodata.*)
    . = ALIGN(4);
  } > I_RAM

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
  } > D_RAM AT > I_RAM

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
    _stack_end = ORIGIN(D_RAM) + LENGTH(D_RAM) - 16;
  } > D_RAM

  /DISCARD/ :
  {
    *(.eh_frame)
  }
}
