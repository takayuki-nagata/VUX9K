// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: Apache-2.0

#![no_std]

use core::panic::PanicInfo;

pub mod zephyr_ffi;
use zephyr_ffi::{zephyr_printk, zephyr_sleep_ms};

#[no_mangle]
pub extern "C" fn rust_main() {
    zephyr_printk("\n[Rust App] Hello from Rust running on Zephyr RTOS!\n");
    zephyr_printk("[Rust App] Executing on VUX9K RISC-V RV32I Dual-ISA SoC.\n");

    // Perform application workload
    for i in 1..=5 {
        zephyr_printk("[Rust Task] Task iteration ");
        // Print decimal digit
        unsafe {
            zephyr_ffi::printk(b"%d\0".as_ptr(), i);
        }
        zephyr_printk(" completed [OK]\n");
        zephyr_sleep_ms(100);
    }

    zephyr_printk("[Rust App] All Rust application tasks finished successfully!\n");
}

#[panic_handler]
fn panic(_info: &PanicInfo) -> ! {
    zephyr_printk("\n[Rust Panic] Fatal error in Rust application!\n");
    loop {}
}
