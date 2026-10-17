// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! A tiny RV32 application linked into the PSRAM (`0x8000_0000`), the card's
//! `\VUX9K\APPS\HELLO.BIN`: the second stage's `run` starts it in place with a0-a2 as the
//! first stage handed them over (a0 = SDHC, a1 = the FAT32 partition's LBA, a2 = the
//! first stage's version). It prints them, then waits for `r`, on which it soft-resets
//! back to the first stage (which is still in the ROM: a PSRAM application doesn't replace
//! it); other keys are ignored. The tests (emulator, RTL, lockstep, board) check what it
//! prints against the first stage's hand-over.

#![no_std]
#![no_main]

use core::arch::global_asm;
use core::panic::PanicInfo;

global_asm!(include_str!("../bootstrap/start.s"));

use fw_common::map;
use fw_drivers::uart::Uart;

#[no_mangle]
pub extern "C" fn main(a0: u32, a1: u32, a2: u32) -> ! {
    // Uart::write_byte waits for room in the TX FIFO before each byte: the second
    // stage's [RUN] line may still fill it
    Uart::print_str("\nVUX9K hello from the PSRAM\n[HELLO] a0=0x");
    Uart::print_hex(a0);
    Uart::print_str(" a1=0x");
    Uart::print_hex(a1);
    Uart::print_str(" a2=0x");
    Uart::print_hex(a2);
    Uart::print_str("\n[HELLO] Press r for the first stage.\n");
    while Uart::read_byte() != Some(b'r') {}
    Uart::print_str("[HELLO] Soft reset\n");
    // The rest of the line (a CR) would be a key for the first stage, which then stays
    // at its prompt; the 5 ms also let the line above leave the TX FIFO
    while Uart::read_byte_timeout(5).is_some() {}
    unsafe { core::ptr::write_volatile(map::GPIO_SOFT_RESET as *mut u32, map::SOFT_RESET_KEY) }
    loop {} // cov:exclude(the soft reset takes the CPU before this)
}

/// A failed bounds check (the code has none).
#[panic_handler]
#[rustfmt::skip] // keeps the markers on the lines they mark
fn panic(_info: &PanicInfo) -> ! { // cov:exclude(no reachable panic)
    loop {} // cov:exclude(no reachable panic)
}
