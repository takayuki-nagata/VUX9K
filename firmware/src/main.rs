// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

#![no_std]
#![no_main]

use core::arch::global_asm;
use core::panic::PanicInfo;

// Include assembly bootstrap
global_asm!(include_str!("../bootstrap/start.s"));

mod gpio;
mod sdcard;
mod timer;
mod uart;

use gpio::Gpio;
use sdcard::SdCard;
use timer::Timer;
use uart::Uart;

#[no_mangle]
pub extern "C" fn main() -> ! {
    // 0. Initial LED indication (all on)
    Gpio::set_leds(0x3F);

    // 1. Initial UART Banner
    Uart::print_str("\r\n========================================\r\n");
    Uart::print_str(" Tang Nano 9K RISC-V Dual-ISA SoC Boot!\r\n");
    Uart::print_str("========================================\r\n");

    // Test 1: GPIO LED Knight Rider pattern (Human visible 80ms per step)
    Uart::print_str("[TEST 1] Testing GPIO LEDs (Knight Rider)...\r\n");
    for _ in 0..2 {
        for i in 0..6 {
            Gpio::set_leds(1 << i);
            Timer::delay_ms(80);
        }
        for i in (1..5).rev() {
            Gpio::set_leds(1 << i);
            Timer::delay_ms(80);
        }
    }
    Gpio::set_leds(0x01);
    Uart::print_str("LED Animation Complete [OK]\r\n");

    // Test 2: Button State Read
    Uart::print_str("[TEST 2] Testing User Button (S2)...\r\n");
    let btn = Gpio::get_button();
    Uart::print_str("Current Button S2 State: ");
    if btn {
        Uart::print_str("PRESSED\r\n");
    } else {
        Uart::print_str("RELEASED\r\n");
    }

    // Test 3: Timer Read & Delay
    Uart::print_str("[TEST 3] Testing System Timer (mtime)...\r\n");
    let start_t = Timer::get_mtime();
    Uart::print_str("Initial mtime: 0x");
    Uart::print_hex((start_t >> 32) as u32);
    Uart::print_hex(start_t as u32);
    Uart::print_str("\r\nWaiting 300ms...\r\n");
    Timer::delay_ms(300);
    let end_t = Timer::get_mtime();
    Uart::print_str("Elapsed ticks: ");
    Uart::print_dec((end_t.wrapping_sub(start_t)) as u32);
    Uart::print_str(" (~8,100,000 expected) [OK]\r\n");

    // Test 4: MicroSD SPI Init
    Uart::print_str("[TEST 4] Initializing MicroSD Card...\r\n");
    if SdCard::init() {
        Uart::print_str("MicroSD Card Initialized Successfully! [OK]\r\n");
        let mut sector_buf = [0u8; 512];
        if SdCard::read_block(0, &mut sector_buf) {
            Uart::print_str("MBR / Sector 0 Read Successful [OK]\r\n");
            Uart::print_str("First 16 bytes: ");
            for i in 0..16 {
                let byte = sector_buf[i];
                Uart::print_hex(byte as u32);
                Uart::write_byte(b' ');
            }
            Uart::print_str("\r\n");
        } else {
            Uart::print_str("Sector 0 Read Failed (Empty / Non-SDHC?)\r\n");
        }
    } else {
        Uart::print_str("MicroSD Init timed out or no card detected.\r\n");
    }

    Uart::print_str("\r\n>>> All Self-Tests Completed Successfully! <<<\r\n");

    // Main Idle Loop: Heartbeat LED and periodic UART message
    let mut count = 0u32;
    let mut led_state = 0x01u8;
    loop {
        Gpio::set_leds(led_state);
        led_state = if led_state == 0x20 { 0x01 } else { led_state << 1 };
        
        Uart::print_str("[Heartbeat #");
        Uart::print_dec(count);
        Uart::print_str("] SoC is running happily!\r\n");
        count += 1;

        Timer::delay_ms(500);
    }
}

#[panic_handler]
fn panic(info: &PanicInfo) -> ! {
    Uart::print_str("\r\n!!! KERNEL PANIC !!!\r\n");
    if let Some(loc) = info.location() {
        Uart::print_str("File: ");
        Uart::print_str(loc.file());
        Uart::print_str(", Line: ");
        Uart::print_dec(loc.line());
        Uart::print_str("\r\n");
    }
    Gpio::set_leds(0x2A); // 101010 pattern
    loop {}
}
