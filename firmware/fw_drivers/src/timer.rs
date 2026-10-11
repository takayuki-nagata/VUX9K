// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

// MMIO Machine Timer Driver (mtime, low word)

use fw_common::map;

const MTIME_LO: *const u32 = map::MTIME_LO as *const u32;

pub struct Timer;

impl Timer {
    #[inline(always)]
    pub fn get_mtime32() -> u32 {
        unsafe { core::ptr::read_volatile(MTIME_LO) }
    }

    pub fn delay_ticks(ticks: u32) {
        let start = Self::get_mtime32();
        while Self::get_mtime32().wrapping_sub(start) < ticks {}
    }

    pub fn delay_us(us: u32) {
        Self::delay_ticks(us * map::TICKS_PER_US);
    }

    pub fn delay_ms(ms: u32) {
        Self::delay_ticks(ms * map::TICKS_PER_MS);
    }
}
