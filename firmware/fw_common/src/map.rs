// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! The SoC's addresses and clock as the firmware sees them (README "Memory & MMIO
//! Register Map", soc/soc_pkg.veryl).

// ----- UART (0x4000_0000; LiteX liteuart's registers, soc/uart/uart_regs.veryl) -----
/// RXTX: a read pops the RX FIFO's head, a write pushes a byte to send.
pub const UART_RXTX: usize = 0x4000_0000;
/// Bit 0: the TX FIFO is full (a byte written then is dropped).
pub const UART_TXFULL: usize = 0x4000_0004;
/// Bit 0: the RX FIFO is empty.
pub const UART_RXEMPTY: usize = 0x4000_0008;
/// Bit 0 TX (the TX FIFO isn't full), bit 1 RX (the RX FIFO isn't empty): levels.
pub const UART_EV_STATUS: usize = 0x4000_000C;
/// `UART_EV_STATUS` while `UART_EV_ENABLE` is 0, `UART_EV_STATUS & UART_EV_ENABLE` otherwise;
/// writes are ignored.
pub const UART_EV_PENDING: usize = 0x4000_0010;
/// Bits 1:0: the events that raise the machine external interrupt (mip.MEIP: EV_STATUS &
/// EV_ENABLE) and that EV_PENDING keeps once it isn't 0. Cleared by
/// the power-on reset and the soft reset.
pub const UART_EV_ENABLE: usize = 0x4000_0014;
/// Bit 0: the TX FIFO is empty (the byte being sent isn't in it).
pub const UART_TXEMPTY: usize = 0x4000_0018;
/// Bit 0: the RX FIFO is full.
pub const UART_RXFULL: usize = 0x4000_001C;
pub const UART_EV_TX: u32 = 1 << 0;
pub const UART_EV_RX: u32 = 1 << 1;
/// The UART's status register before the liteuart registers (v0.3.x's bitstreams: data at
/// +0, status at +4, bit 1 the TX FIFO full). Only the second stage's start-up uses it: on a
/// CPU without RV32M (misa), the bitstream is one of those, and the message that it needs a
/// newer one has to be written through the UART it has (stage2/bootstrap/start.s).
pub const LEGACY_UART_STATUS: usize = 0x4000_0004;
pub const LEGACY_TX_FULL: u32 = 1 << 1;

// ----- CLINT (0x2000_0000, RV32 region 2; soc_pkg::RV_REGION_CLINT) ----------------
/// The core-local interruptor's layout (riscv,clint0): msip, mtimecmp, mtime. Each 32-bit
/// word on its own; the 64 KB repeat across the region.
pub const CLINT_BASE: usize = 0x2000_0000;
/// Bit 0: the machine software interrupt (mip.MSIP).
pub const CLINT_MSIP: usize = CLINT_BASE;
pub const MTIMECMP_LO: usize = CLINT_BASE + 0x4000;
pub const MTIMECMP_HI: usize = CLINT_BASE + 0x4004;
/// mtime counts SoC clocks (`TICKS_PER_US`).
pub const MTIME_LO: usize = CLINT_BASE + 0xBFF8;
pub const MTIME_HI: usize = CLINT_BASE + 0xBFFC;

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
/// The boot ROM (soc_rom): the first stage, from the bitstream; power-on and every soft
/// reset into RV32 start it. Read-only: a store there is ignored, as is one to an unassigned
/// region (0x2000_0000, D-RAM until it went, is one).
pub const ROM_BASE: usize = 0x0000_0000;
pub const ROM_BYTES: usize = 16 * 1024;
/// The wait-state window (soc_pkg::RV_REGION_XMEM, XMEM_WORDS): the 8 MB PSRAM, code and
/// data, the only RAM, every access that misses its caches slower than the ROM; its
/// `XMEM_BYTES` repeat every 8 MB across 0x8xxx_xxxx (the PSRAM's register space is not
/// reachable). Contents are undefined at power-up.
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
