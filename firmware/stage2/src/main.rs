// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! The second stage: a file manager and application launcher at a line prompt (`s2> `),
//! started by the first stage from `\VUX9K\STAGE2.BIN` (or a RAM boot) in the PSRAM at
//! map::STAGE2_BASE with a0 = SDHC, a1 = the FAT32 partition's LBA (0: none mounted),
//! a2 = the first stage's version. `shell` reads the lines, `cmds` has the commands,
//! `launch` starts applications (fw_common::launch's targets) and installs a second
//! stage (`fat::stage2::install`, which keeps the previous one as `STAGE2.OLD`).

#![no_std]
#![no_main]

use core::arch::global_asm;
use core::panic::PanicInfo;

global_asm!(include_str!("../bootstrap/start.s"));

mod cmds;
mod launch;
mod shell;

use fw_drivers::sdcard::SdCard;
use fw_drivers::uart::Uart;

/// Raise it with every change of the second stage: its v4 header carries it (the
/// Makefile's mkv4 reads it from here), `info` and the banner show it, and the tests
/// read it from here.
const STAGE2_VERSION: u32 = 4;

#[no_mangle]
pub extern "C" fn main(a0: u32, a1: u32, a2: u32) -> ! {
    // The first stage mounted a card (a1 = its partition): take it as it is
    if a1 != 0 {
        SdCard::adopt(a0 & 1 != 0);
    }
    Uart::print_str("\nVUX9K Stage 2 (v");
    Uart::print_dec(STAGE2_VERSION);
    Uart::print_str(")\n[S2] a0=0x");
    Uart::print_hex(a0);
    Uart::print_str(" a1=0x");
    Uart::print_hex(a1);
    Uart::print_str(" a2=0x");
    Uart::print_hex(a2);
    Uart::print_str("\nType help for the commands.\n");
    shell::run(a2)
}

/// start.s's trap_entry: an exception in the second stage, or in an application it
/// started in the PSRAM that didn't set its own trap vector. Report it and stop.
#[no_mangle]
extern "C" fn trap_report(mcause: u32, mepc: u32, mtval: u32) -> ! {
    Uart::print_str("\n[TRAP] mcause=0x");
    Uart::print_hex(mcause);
    Uart::print_str(" mepc=0x");
    Uart::print_hex(mepc);
    Uart::print_str(" mtval=0x");
    Uart::print_hex(mtval);
    Uart::print_str("\n[TRAP] Stopped. Reset the board.\n");
    loop {}
}

/// A failed bounds check (the code has none that can fail).
#[panic_handler]
#[rustfmt::skip] // keeps the markers on the lines they mark
fn panic(_info: &PanicInfo) -> ! { // cov:exclude(no reachable panic)
    Uart::print_str("\n[PANIC]\n"); // cov:exclude(no reachable panic)
    loop {} // cov:exclude(no reachable panic)
}
