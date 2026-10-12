// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

// MMIO Machine Timer Driver (mtime, low word)

use fw_common::map;

const MTIME_LO: *const u32 = map::MTIME_LO as *const u32;

pub struct Timer;

// ms_ticks's shifts are TICKS_PER_MS (18 MHz): change them with the clock
const _: () = assert!((1 << 14) + (1 << 10) + (1 << 9) + (1 << 6) + (1 << 4) == map::TICKS_PER_MS);

/// `ms` in mtime ticks, by shifts: RV32I has no multiply, and fw_common::mul_u32's loop
/// is too slow for a receive loop running from the PSRAM.
#[inline(always)]
pub fn ms_ticks(ms: u32) -> u32 {
    (ms << 14) + (ms << 10) + (ms << 9) + (ms << 6) + (ms << 4)
}

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
