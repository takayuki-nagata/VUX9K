/*
 * Copyright (c) 2026 Takayuki Nagata
 * SPDX-License-Identifier: MIT
 */

OUTPUT_ARCH( "riscv" )
ENTRY( rvtest_entry_point )

SECTIONS
{
  . = 0x00000000;
  .text : {
    *(.text*)
  }
  .data : {
    *(.data*)
  }
  .tohost : {
    *(.tohost*)
  }
  .bss : {
    *(.bss*)
  }
}
