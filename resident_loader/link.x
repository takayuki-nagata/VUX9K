/*
 * Copyright (c) 2026 Takayuki Nagata
 * SPDX-License-Identifier: MIT
 */

MEMORY
{
  I_RAM (rx)  : ORIGIN = 0x00003800, LENGTH = 2K
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
    . = ALIGN(4);
  } > I_RAM

  /DISCARD/ :
  {
    *(.data)
    *(.data.*)
    *(.sdata)
    *(.sdata.*)
    *(.bss)
    *(.bss.*)
    *(.sbss)
    *(.sbss.*)
    *(.eh_frame)
  }
}
