// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

// MMIO Machine Timer Driver (mtime / mtimecmp)

const TIMER_BASE: usize = 0x4000_1000;
const MTIME_LOW: *const u32 = TIMER_BASE as *const u32;

pub struct Timer;

impl Timer {
    #[inline(always)]
    pub fn get_mtime() -> u64 {
        unsafe {
            core::ptr::read_volatile(MTIME_LOW) as u64
        }
    }

    #[inline(always)]
    pub fn get_mtime32() -> u32 {
        unsafe {
            core::ptr::read_volatile(MTIME_LOW)
        }
    }

    pub fn delay_ticks(ticks: u32) {
        let start = Self::get_mtime32();
        while Self::get_mtime32().wrapping_sub(start) < ticks {}
    }

    pub fn delay_us(us: u32) {
        Self::delay_ticks(us * 27);
    }

    pub fn delay_ms(ms: u32) {
        Self::delay_ticks(ms * 27_000);
    }
}
