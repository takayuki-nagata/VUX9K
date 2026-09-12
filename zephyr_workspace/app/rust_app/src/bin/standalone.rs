// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: Apache-2.0

#![no_std]
#![no_main]

use core::arch::global_asm;

global_asm!(
    r#"
    .section .text.entry
    .global _start
    .type _start, @function
    _start:
        .option push
        .option norelax
        la gp, __global_pointer$
        .option pop
        la sp, _stack_end

        la t0, _sbss
        la t1, _ebss
        bge t0, t1, 2f
    1:
        sw zero, 0(t0)
        addi t0, t0, 4
        blt t0, t1, 1b
    2:
        la t0, _sdata
        la t1, _edata
        la t2, _sidata
        bge t0, t1, 4f
    3:
        lw t3, 0(t2)
        sw t3, 0(t0)
        addi t0, t0, 4
        addi t2, t2, 4
        blt t0, t1, 3b
    4:
        call main
    5:
        j 5b
    "#
);

const UART_BASE: usize = 0x4000_0000;
const UART_DATA: *mut u8 = UART_BASE as *mut u8;
const UART_STATUS: *const u32 = (UART_BASE + 0x4) as *const u32;

fn uart_putc(c: u8) {
    unsafe {
        while (core::ptr::read_volatile(UART_STATUS) & 0x2) != 0 {}
        core::ptr::write_volatile(UART_DATA, c);
    }
}

#[no_mangle]
pub extern "C" fn vux9k_print_str(s: *const u8) {
    if s.is_null() {
        return;
    }
    let mut ptr = s;
    unsafe {
        while *ptr != 0 {
            uart_putc(*ptr);
            ptr = ptr.add(1);
        }
    }
}

#[no_mangle]
pub extern "C" fn vux9k_print_int(mut val: i32) {
    if val == 0 {
        uart_putc(b'0');
        return;
    }
    if val < 0 {
        uart_putc(b'-');
        val = -val;
    }
    let mut uval = val as u32;
    let mut buf = [0u8; 10];
    let mut i = 0;
    while uval > 0 {
        buf[i] = (uval % 10) as u8 + b'0';
        uval /= 10;
        i += 1;
    }
    while i > 0 {
        i -= 1;
        uart_putc(buf[i]);
    }
}

#[no_mangle]
pub extern "C" fn vux9k_k_msleep(_ms: i32) -> i32 {
    0
}

#[no_mangle]
pub extern "C" fn vux9k_k_uptime_get_32() -> u32 {
    0
}

#[no_mangle]
pub extern "C" fn main() -> ! {
    vux9k_rust_app::rust_main();

    vux9k_print_str(b"\n[Rust App] Returning to Boot Manager...\n\0".as_ptr());

    // Clear ISA mode override (GPIO_ISA_MODE_REG at 0x4000_3008)
    unsafe {
        core::ptr::write_volatile(0x4000_3008 as *mut u32, 0);
        // Clear mailbox to 0 (boot Slot 0 / Boot Manager)
        core::ptr::write_volatile(0x2000_1FFC as *mut u32, 0);
        // Jump to Resident Loader at 0x0000_4800
        core::arch::asm!("jr {0}", in(reg) 0x0000_4800usize, options(noreturn));
    }
}
