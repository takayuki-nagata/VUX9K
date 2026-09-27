// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! Peripherals behind the MMIO pages of soc_addr_decoder (page = addr[15:12]).

pub mod gpio;
pub mod timer;

pub use gpio::{Gpio, SoftReset};
pub use timer::Timer;

pub const PAGE_UART: u32 = 0x0;
pub const PAGE_TIMER: u32 = 0x1;
pub const PAGE_SD: u32 = 0x2;
pub const PAGE_GPIO: u32 = 0x3;

#[derive(Clone, Debug, Default)]
pub struct Periph {
    pub timer: Timer,
    pub gpio: Gpio,
}
