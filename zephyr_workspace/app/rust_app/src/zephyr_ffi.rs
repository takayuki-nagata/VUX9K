// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: Apache-2.0

//! Minimal Zephyr RTOS C-ABI FFI wrapper for Rust application

extern "C" {
    pub fn vux9k_print_str(str: *const u8);
    pub fn vux9k_print_int(val: i32);
    pub fn vux9k_k_msleep(ms: i32) -> i32;
    pub fn vux9k_k_uptime_get_32() -> u32;
}

pub fn zephyr_print_c(msg: &[u8]) {
    unsafe {
        vux9k_print_str(msg.as_ptr());
    }
}

pub fn zephyr_print_int(val: i32) {
    unsafe {
        vux9k_print_int(val);
    }
}

pub fn zephyr_sleep_ms(ms: i32) {
    unsafe {
        vux9k_k_msleep(ms);
    }
}

pub fn zephyr_uptime_ms() -> u32 {
    unsafe { vux9k_k_uptime_get_32() }
}
