/*
 * Copyright (c) 2026 Takayuki Nagata
 * SPDX-License-Identifier: MIT
 */

/* The hello demo: everything in the PSRAM's first 64 KB (fw_common::map::XMEM_BASE), loaded
 * there as one image by the second stage's `run` (.data in place, nothing to copy). */
MEMORY
{
  APP (rwx) : ORIGIN = 0x80000000, LENGTH = 64K
}

ENTRY(_start)

SECTIONS
{
  .text :
  {
    KEEP(*(.text.entry))
    *(.text)
    *(.text.*)
  } > APP

  .rodata :
  {
    . = ALIGN(4);
    *(.rodata)
    *(.rodata.*)
    *(.srodata)
    *(.srodata.*)
    . = ALIGN(4);
  } > APP

  .data :
  {
    . = ALIGN(4);
    __global_pointer$ = . + 0x800;
    *(.data)
    *(.data.*)
    *(.sdata)
    *(.sdata.*)
    . = ALIGN(4);
  } > APP

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
  } > APP

  _stack_end = ORIGIN(APP) + LENGTH(APP);
  ASSERT(_ebss <= _stack_end - 4K, "hello: the image and .bss run into the stack")

  /DISCARD/ :
  {
    *(.eh_frame)
  }
}
