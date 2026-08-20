/* Harvard Architecture Linker Script for Tang Nano 9K SoC */
MEMORY
{
  I_ROM (rx)  : ORIGIN = 0x00000000, LENGTH = 256K
  D_RAM (rwx) : ORIGIN = 0x20000000, LENGTH = 128K
}

ENTRY(_start)

SECTIONS
{
  .text :
  {
    KEEP(*(.text.entry))
    *(.text .text.*)
    *(.rodata .rodata.*)
  } > I_ROM

  _sidata = LOADADDR(.data);

  .data :
  {
    . = ALIGN(4);
    _sdata = .;
    *(.data .data.*)
    *(.sdata .sdata.*)
    . = ALIGN(4);
    _edata = .;
  } > D_RAM AT > I_ROM

  .bss (NOLOAD) :
  {
    . = ALIGN(4);
    _sbss = .;
    *(.bss .bss.*)
    *(.sbss .sbss.*)
    *(COMMON)
    . = ALIGN(4);
    _ebss = .;
  } > D_RAM

  .heap (NOLOAD) :
  {
    . = ALIGN(4);
    _heap_start = .;
    . = . + 96K; /* 96 KB reserved heap for Rust alloc / num-bigint */
    _heap_end = .;
  } > D_RAM

  _estack = ORIGIN(D_RAM) + LENGTH(D_RAM);
}
