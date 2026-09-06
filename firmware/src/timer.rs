// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

// MMIO Machine Timer Driver (mtime / mtimecmp)

const TIMER_BASE: usize = 0x4000_1000;
const MTIME_LOW: *const u32 = TIMER_BASE as *const u32;
const MTIME_HIGH: *const u32 = (TIMER_BASE + 0x4) as *const u32;

#[allow(dead_code)]
const FREQ_KHZ: u64 = 27_000; // 27 MHz system clock (27,000 cycles per ms)

pub struct Timer;

impl Timer {
    #[inline(always)]
    pub fn get_mtime() -> u64 {
        unsafe {
            loop {
                let high1 = core::ptr::read_volatile(MTIME_HIGH);
                let low = core::ptr::read_volatile(MTIME_LOW);
                let high2 = core::ptr::read_volatile(MTIME_HIGH);
                if high1 == high2 {
                    return ((high1 as u64) << 32) | (low as u64);
                }
            }
        }
    }

    #[allow(dead_code)]
    pub fn delay_ticks(ticks: u64) {
        let start = Self::get_mtime();
        let target = start.wrapping_add(ticks);
        if target >= start {
            while Self::get_mtime() < target {}
        } else {
            // Target wrapped around 64-bit boundary
            while Self::get_mtime() >= start || Self::get_mtime() < target {}
        }
    }

    pub fn delay_us(us: u32) {
        Self::delay_ticks((us as u64) * 27);
    }

    pub fn delay_ms(ms: u32) {
        Self::delay_ticks((ms as u64) * FREQ_KHZ);
    }
}
