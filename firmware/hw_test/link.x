/*
 * Copyright (c) 2026 Takayuki Nagata
 * SPDX-License-Identifier: MIT
 */

/* hw_test: code in the boot ROM (in place of the first stage), data and stack in 8 KB of
 * the PSRAM's upper half, which its window tests leave alone: t_xmem's first and last 2 KB
 * and its address-line words (0x8000_0000 + 4 << b and 0x807F_FFFC - 4 << b: the nearest
 * are 0x805F_FFFC and 0x806F_FFFC), t_xmem_lanes' words (the lower 4 MB), t_xmem_exec's
 * and t_xmem_time's (the first 8 KB) */
MEMORY
{
  I_ROM (rx)  : ORIGIN = 0x00000000, LENGTH = 16K
  RAM (rwx)   : ORIGIN = 0x80600000, LENGTH = 8K
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
  } > RAM AT > I_ROM

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
  } > RAM

  .stack (NOLOAD) :
  {
    . = ALIGN(16);
    _stack_end = ORIGIN(RAM) + LENGTH(RAM) - 32;
  } > RAM

  /DISCARD/ :
  {
    *(.eh_frame)
  }
}
