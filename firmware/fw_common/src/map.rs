// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! The SoC's addresses and clock as the firmware sees them (README "Memory & MMIO
//! Register Map", soc/soc_pkg.veryl).

// ----- UART (0x4000_0000) ---------------------------------------------------------
pub const UART_DATA: usize = 0x4000_0000;
/// Bit 0: RX FIFO empty, bit 1: TX FIFO full.
pub const UART_STATUS: usize = 0x4000_0004;
pub const UART_RX_EMPTY: u32 = 1 << 0;
pub const UART_TX_FULL: u32 = 1 << 1;

// ----- Timer (0x4000_1000) --------------------------------------------------------
pub const MTIME_LO: usize = 0x4000_1000;

// ----- SD SPI master (0x4000_2000) ------------------------------------------------
pub const SD_DATA: usize = 0x4000_2000;
/// 0 = CS asserted (card selected).
pub const SD_CS: usize = 0x4000_2004;
/// Bit 0: transfer in progress.
pub const SD_STATUS: usize = 0x4000_2008;
pub const SD_BUSY: u32 = 1 << 0;
/// SCLK half period in SoC clocks (bits 7:0). Resets to `SD_DIV_INIT` on power-on
/// only (a CPU soft reset keeps it); writes below `SD_DIV_FAST` set `SD_DIV_FAST`.
pub const SD_CLKDIV: usize = 0x4000_200C;
/// soc_pkg::SD_CLK_DIV_HALF: 391 kHz, for card initialization (at most 400 kHz).
pub const SD_DIV_INIT: u32 = 23;
/// soc_pkg::SD_CLK_DIV_MIN: 3 MHz, once the card is ready.
pub const SD_DIV_FAST: u32 = 3;

// ----- GPIO (0x4000_3000) ---------------------------------------------------------
pub const GPIO_LED: usize = 0x4000_3000;
pub const GPIO_BUTTON: usize = 0x4000_3004;
/// ISA for the next soft reset: `BOOT_MODE_VALID | 1` = RV32, `BOOT_MODE_VALID` = Hack.
pub const GPIO_BOOT_MODE: usize = 0x4000_3008;
pub const BOOT_MODE_VALID: u32 = 0x100;
pub const BOOT_MODE_RV32: u32 = BOOT_MODE_VALID | 1;
/// Writing `SOFT_RESET_KEY` here resets the CPU (not the peripherals or memories).
pub const GPIO_SOFT_RESET: usize = 0x4000_300C;
pub const SOFT_RESET_KEY: u32 = 0x5A5A_A55A;

// ----- Memories -------------------------------------------------------------------
/// I-RAM: the first stage at power-on; a store there replaces it (whole words: no byte
/// enables), and every soft reset into RV32 starts what is there.
pub const IRAM_BASE: usize = 0x0000_0000;
pub const IRAM_BYTES: usize = 16 * 1024;
pub const DRAM_BASE: usize = 0x2000_0000;
pub const DRAM_BYTES: usize = 8 * 1024;
/// The wait-state window (soc_pkg::RV_REGION_XMEM, XMEM_WORDS): the 8 MB PSRAM, code and
/// data, every access slower than I-RAM/D-RAM; its `XMEM_BYTES` repeat every 8 MB across
/// 0x8xxx_xxxx (the PSRAM's register space is not reachable). Contents are undefined at
/// power-up.
pub const XMEM_BASE: usize = 0x8000_0000;
pub const XMEM_BYTES: usize = 8 * 1024 * 1024;
/// The second stage's window, the top 1 MB of the PSRAM: `\VUX9K\STAGE2.BIN` and a RAM
/// boot load there (crate::boot::STAGE2_WINDOW).
pub const STAGE2_BASE: usize = 0x8070_0000;
pub const STAGE2_BYTES: usize = 1024 * 1024;
/// The first stage's data and stack: the 64 KB below the second stage's window.
pub const S1_RAM_BASE: usize = 0x806F_0000;
pub const S1_RAM_BYTES: usize = 64 * 1024;
/// Hack's instruction segment (soc_pkg::HACK_I_BASE): a Hack fetch of byte address pc
/// reads `HACK_I_BASE + pc[15:0]`, two 16-bit instructions a word, the first in the low
/// half. A Hack program starts at 0 (a soft reset into Hack).
pub const HACK_I_BASE: usize = 0x8000_0000;
pub const HACK_I_BYTES: usize = 64 * 1024;
/// Hack's data segment (soc_pkg::HACK_D_BASE): Hack data word A (A < 0x6000) is the 32-bit
/// word at `HACK_D_BASE + 4 * A[14:0]`.
pub const HACK_D_BASE: usize = 0x8002_0000;
pub const HACK_D_WORDS: usize = 0x6000;

// ----- Clock ----------------------------------------------------------------------
/// mtime ticks at the SoC clock, 18 MHz (soc_pkg::CLK_HZ).
pub const TICKS_PER_US: u32 = 18;
pub const TICKS_PER_MS: u32 = 18_000;
