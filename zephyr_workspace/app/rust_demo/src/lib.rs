// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: Apache-2.0

//! The VUX9K Rust demo: a Zephyr application written in Rust. Zephyr (src/main.c)
//! boots, then calls `rust_main()`, which uses the kernel through the C functions
//! declared in `zephyr_ffi`.

#![no_std]

use core::panic::PanicInfo;

pub mod zephyr_ffi;
use zephyr_ffi::{zephyr_print_c, zephyr_print_int, zephyr_sleep_ms, zephyr_uptime_ms};

/// Sleep of the timer check: k_msleep() must take this long by k_uptime_get_32(),
/// which counts the SoC's mtime at 27 MHz.
const SLEEP_MS: i32 = 100;

#[no_mangle]
pub extern "C" fn rust_main() {
    zephyr_print_c(b"\n[Rust App] Hello from Rust running on Zephyr RTOS!\n\0");
    zephyr_print_c(b"[Rust App] Executing on VUX9K RISC-V RV32I Dual-ISA SoC.\n\0");

    for i in 1..=5 {
        zephyr_print_c(b"[Rust Task] Task iteration \0");
        zephyr_print_int(i);
        zephyr_print_c(b" completed [OK]\n\0");
    }

    let start = zephyr_uptime_ms();
    zephyr_sleep_ms(SLEEP_MS);
    let slept = zephyr_uptime_ms().wrapping_sub(start);
    zephyr_print_c(b"[Rust App] k_msleep(\0");
    zephyr_print_int(SLEEP_MS);
    zephyr_print_c(b") took \0");
    zephyr_print_int(slept as i32);
    zephyr_print_c(b" ms\n\0");

    zephyr_print_c(b"[Rust App] All Rust application tasks finished successfully!\n\0");
}

#[panic_handler]
fn panic(_info: &PanicInfo) -> ! {
    zephyr_print_c(b"\n[Rust Panic] Fatal error in Rust application!\n\0");
    loop {}
}
