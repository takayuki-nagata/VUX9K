/*
 * Copyright (c) 2026 Takayuki Nagata
 * SPDX-License-Identifier: MIT
 */

OUTPUT_ARCH( "riscv" )
ENTRY( rvmodel_boot )

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
