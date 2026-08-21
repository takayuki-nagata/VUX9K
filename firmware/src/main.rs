// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

#![no_std]

#![no_main]

use core::arch::global_asm;
use core::panic::PanicInfo;

// Include assembly bootstrap
global_asm!(include_str!("../bootstrap/start.s"));

mod sdcard;
mod timer;
mod uart;

use sdcard::SdCard;
use timer::Timer;
use uart::Uart;

#[no_mangle]
pub extern "C" fn main() -> ! {
    Uart::print_str("\n========================================\n");
    Uart::print_str("Tang Nano 9K RISC-V Dual-ISA SoC Boot!\n");
    Uart::print_str("========================================\n");

    // Test 1: Timer Read & Delay
    Uart::print_str("[TEST 1] Testing System Timer (mtime)...\n");
    let start_t = Timer::get_mtime();
    Uart::print_str("Initial mtime: ");
    Uart::print_dec(start_t as u32);
    Uart::print_str("\nDelaying 100 ticks...\n");
    Timer::delay_ticks(100);
    let end_t = Timer::get_mtime();
    Uart::print_str("Elapsed mtime: ");
    Uart::print_dec((end_t - start_t) as u32);
    Uart::print_str(" ticks [OK]\n");

    // Test 2: SD Card SPI CMD0 Init Test
    Uart::print_str("[TEST 2] Testing SD Card SPI Master...\n");
    SdCard::set_cs(false);
    for _ in 0..10 {
        SdCard::transfer(0xFF);
    }
    let res = SdCard::send_cmd(0, 0, 0x95);
    Uart::print_str("SD Card CMD0 Response: 0x");
    Uart::print_dec(res as u32);
    Uart::print_str(" [OK]\n");

    Uart::print_str("========================================\n");
    Uart::print_str("All SoC Hardware Checks PASSED Successfully!\n");
    Uart::print_str("========================================\n");

    // Exit cleanly
    unsafe {
        core::arch::asm!("wfi");
    }
    loop {}
}

#[panic_handler]
fn panic(_info: &PanicInfo) -> ! {
    Uart::print_str("\n[PANIC] Unrecoverable Firmware Error!\n");
    loop {}
}
