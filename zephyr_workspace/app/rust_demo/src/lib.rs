// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: Apache-2.0

#![no_std]

use core::panic::PanicInfo;

pub mod zephyr_ffi;
use zephyr_ffi::{zephyr_print_c, zephyr_print_int};

#[no_mangle]
pub extern "C" fn rust_main() {
    zephyr_print_c(b"\n[Rust App] Hello from Rust running on Zephyr RTOS!\n\0");
    zephyr_print_c(b"[Rust App] Executing on VUX9K RISC-V RV32I Dual-ISA SoC.\n\0");

    // Perform application workload
    for i in 1..=5 {
        zephyr_print_c(b"[Rust Task] Task iteration \0");
        zephyr_print_int(i);
        zephyr_print_c(b" completed [OK]\n\0");
    }

    zephyr_print_c(b"[Rust App] All Rust application tasks finished successfully!\n\0");
}

#[panic_handler]
fn panic(_info: &PanicInfo) -> ! {
    zephyr_print_c(b"\n[Rust Panic] Fatal error in Rust application!\n\0");
    loop {}
}
