/*
 * Copyright (c) 2026 Takayuki Nagata
 * SPDX-License-Identifier: MIT
 */

/* The second stage: everything in its PSRAM window (fw_common::map::STAGE2_BASE/BYTES),
 * loaded there as one image (.data in place, nothing to copy). */
MEMORY
{
  S2 (rwx) : ORIGIN = 0x80700000, LENGTH = 1M
}

ENTRY(_start)

SECTIONS
{
  .text :
  {
    KEEP(*(.text.entry))
    *(.text)
    *(.text.*)
  } > S2

  .rodata :
  {
    . = ALIGN(4);
    *(.rodata)
    *(.rodata.*)
    *(.srodata)
    *(.srodata.*)
    . = ALIGN(4);
  } > S2

  .data :
  {
    . = ALIGN(4);
    __global_pointer$ = . + 0x800;
    *(.data)
    *(.data.*)
    *(.sdata)
    *(.sdata.*)
    . = ALIGN(4);
  } > S2

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
  } > S2

  _stack_end = ORIGIN(S2) + LENGTH(S2);

  /DISCARD/ :
  {
    *(.eh_frame)
  }
}
