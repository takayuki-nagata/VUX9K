// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: Apache-2.0

//! Minimal Zephyr RTOS C-ABI FFI wrapper for Rust application

extern "C" {
    pub fn printk(fmt: *const u8, ...);
    pub fn k_msleep(ms: i32) -> i32;
    pub fn k_uptime_get_32() -> u32;
}

pub fn zephyr_printk(msg: &str) {
    for byte in msg.bytes() {
        unsafe {
            printk(b"%c\0".as_ptr(), byte as u32);
        }
    }
}

pub fn zephyr_sleep_ms(ms: i32) {
    unsafe {
        k_msleep(ms);
    }
}

pub fn zephyr_uptime_ms() -> u32 {
    unsafe { k_uptime_get_32() }
}
