/* Copyright (c) 2026 Takayuki Nagata */
/* SPDX-License-Identifier: MIT */
/* ACT4 tests in sim/tb_hex_runner.veryl's 256 KB RAM. The CPU starts at 0, the tests at
 * TEST_BASE (below it, Sail keeps its device tree at 0x1000): the word at 0 jumps there.
 * memory.regions in sail.json must match RAM. Based on the upstream example
 * config/cores/cve2/cv32e20/link.ld. */
RAM_ORIGIN = 0x00000000;
RAM_LENGTH = 0x40000;
TEST_BASE = 0x00004000;
NUM_HARTS = 1;
STACK_SIZE = 0x2000;

OUTPUT_ARCH( "riscv" )
ENTRY(rvtest_entry_point)

MEMORY
{
  ram (rwx) : ORIGIN = RAM_ORIGIN, LENGTH = RAM_LENGTH
}

PROVIDE(__stack_size = STACK_SIZE);
PROVIDE(__num_harts = NUM_HARTS);

SECTIONS
{
  /* jal x0, TEST_BASE (0x4000) */
  .boot RAM_ORIGIN : { LONG(0x0000406F) } > ram

  .text.init   TEST_BASE : { *(.text.init) } > ram
  ASSERT(rvtest_entry_point == TEST_BASE, "the jump at 0 expects the entry point at TEST_BASE")
  .text.rvtest . : { *(.text.rvtest) *(.text.rvtest.*) } > ram

  . = ALIGN(0x4000);
  .rodata . : { *(.rodata) *(.rodata.*) *(.srodata) *(.srodata.*) } > ram
  .data   . : { *(.data) *(.data.*) *(.sdata) *(.sdata.*) } > ram

  . = ALIGN(16);
  .bss . : {
    __bss_start = .;
    *(.sbss) *(.sbss.*) *(.bss) *(.bss.*) *(COMMON)
    . = ALIGN(16);
    __bss_end = .;
  } > ram

  . = ALIGN(16);
  __stack_bottom = .;
  . += __stack_size * __num_harts;
  __stack_top = .;

  . = ALIGN(0x1000);
  .text.rvmodel . : { *(.text.rvmodel) *(.text.rvmodel.*) *(.text) *(.text.*) } > ram

  . = ALIGN(0x1000);
  _end = .;

  ASSERT(_end <= ORIGIN(ram) + LENGTH(ram), "ACT ELF exceeds the testbench RAM")
}
