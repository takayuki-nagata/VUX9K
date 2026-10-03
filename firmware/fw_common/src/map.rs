// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! The SoC's addresses and clock as the firmware sees them (README "Memory & MMIO
//! Register Map", soc/soc_pkg.veryl). Constants only: the Resident Loader uses them
//! too without growing.

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
/// The Resident Loader's entry, in upper I-RAM (0x3800-0x3FFF).
pub const RL_ENTRY: usize = 0x0000_3800;
pub const DRAM_BASE: usize = 0x2000_0000;
/// The last 8 bytes of D-RAM belong to the loaders: the mailbox (below) and the
/// word before it.
pub const LOADER_WORDS: usize = 0x2000_1FF8;
/// The Boot Manager -> Resident Loader mailbox (crate::mailbox).
pub const MAILBOX: usize = 0x2000_1FFC;

// ----- Clock ----------------------------------------------------------------------
/// mtime ticks at the SoC clock, 18 MHz (soc_pkg::CLK_HZ).
pub const TICKS_PER_US: u32 = 18;
pub const TICKS_PER_MS: u32 = 18_000;
