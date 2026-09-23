/*
 * Copyright (c) 2026 Takayuki Nagata
 * SPDX-License-Identifier: MIT
 */

/*
 * Linker script for riscv-tests (env/p) on sim/tb_hex_runner.veryl.
 * The image is loaded at address 0 of the testbench's flat 256 KB RAM.
 * `tohost` is placed at the testbench's TOHOST_ADDR, outside the RAM window,
 * and marked NOLOAD so it never ends up in the flat binary image.
 */

OUTPUT_ARCH( "riscv" )
ENTRY( _start )

SECTIONS
{
  . = 0x00000000;
  .text.init : { *(.text.init) }
  .text : { *(.text*) }
  .data : { *(.data*) *(.sdata*) *(.rodata*) *(.srodata*) }
  .bss : { *(.bss*) *(.sbss*) }
  _end = .;

  .tohost 0xF0000000 (NOLOAD) : { *(.tohost) }
}
