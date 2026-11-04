// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! Peripherals behind the MMIO pages of soc_addr_decoder (page = addr[15:12]; page 1 is
//! unassigned), and the CLINT (RV32 region 2).

pub mod clint;
pub mod gpio;
pub mod sdspi;
pub mod timer;
pub mod uart;

pub use clint::Clint;
pub use gpio::{Gpio, SoftReset};
pub use sdspi::SdSpi;
pub use timer::{Reg as TimerReg, Timer};
pub use uart::Uart;

pub const PAGE_UART: u32 = 0x0;
/// The v0.3.x bitstreams' TIMER page (`Soc::timer_page`); unassigned in the RTL.
pub const PAGE_TIMER: u32 = 0x1;
pub const PAGE_SD: u32 = 0x2;
pub const PAGE_GPIO: u32 = 0x3;

#[derive(Clone, Debug, Default)]
pub struct Periph {
    pub timer: Timer,
    pub gpio: Gpio,
    pub uart: Uart,
    pub sd: SdSpi,
    /// The CLINT (its msip; mtime/mtimecmp are `timer`'s).
    pub clint: Clint,
}

/// The machine timer's register at the TIMER page's offset (addr[3:0], `Soc::timer_page`).
pub fn timer_page_reg(addr: u32) -> Option<TimerReg> {
    match addr & 0xF {
        0x0 => Some(TimerReg::MtimeLo),
        0x4 => Some(TimerReg::MtimeHi),
        0x8 => Some(TimerReg::MtimecmpLo),
        0xC => Some(TimerReg::MtimecmpHi),
        _ => None,
    }
}
