// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

// MMIO Machine Timer Driver (mtime / mtimecmp)

const TIMER_BASE: usize = 0x4000_1000;
const MTIME_LOW: *const u32 = TIMER_BASE as *const u32;
const MTIME_HIGH: *const u32 = (TIMER_BASE + 0x4) as *const u32;
const MTIMECMP_LOW: *mut u32 = (TIMER_BASE + 0x8) as *mut u32;
const MTIMECMP_HIGH: *mut u32 = (TIMER_BASE + 0xC) as *mut u32;

pub struct Timer;

impl Timer {
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

    pub fn set_mtimecmp(val: u64) {
        unsafe {
            // Prevent spurious triggers while setting 64-bit value
            core::ptr::write_volatile(MTIMECMP_LOW, 0xFFFF_FFFF);
            core::ptr::write_volatile(MTIMECMP_HIGH, (val >> 32) as u32);
            core::ptr::write_volatile(MTIMECMP_LOW, val as u32);
        }
    }

    pub fn delay_ticks(ticks: u64) {
        let start = Self::get_mtime();
        while Self::get_mtime() - start < ticks {}
    }
}
