// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! A provisional second stage, until the file manager and launcher replace it: shows
//! what the first stage handed over (a0-a2), installs a second stage on the card
//! (`fat::stage2::install`, which keeps the previous one as `STAGE2.OLD`) and soft-resets
//! back to the first stage. Runs in the PSRAM at map::STAGE2_BASE.

#![no_std]
#![no_main]

use core::arch::global_asm;
use core::panic::PanicInfo;

global_asm!(include_str!("../bootstrap/start.s"));

use fw_common::boot::{put_boot_error, RAM_BOOT_MAX, STAGE2_WINDOW};
use fw_common::fat::stage2::{self, install, BootError};
use fw_common::fat::{FatError, Volume};
use fw_common::map;
use fw_common::upload::{upload_v2, UploadSink, BLOCK};
use fw_drivers::sdcard::SdCard;
use fw_drivers::uart::Uart;

const PROMPT: &str = "s2> ";
/// Where a received file is kept: the PSRAM below the first stage's data.
const SCRATCH: usize = map::XMEM_BASE;

/// Transfer v2 into SCRATCH.
struct Scratch {
    len: u32,
}

impl UploadSink for Scratch {
    fn begin(&mut self, len: u32) -> bool {
        self.len = len;
        true
    }

    fn block(&mut self, index: u32, data: &[u8]) -> bool {
        let at = SCRATCH + index as usize * BLOCK;
        unsafe { core::ptr::copy_nonoverlapping(data.as_ptr(), at as *mut u8, data.len()) }
        true
    }

    fn finish(&mut self, _ok: bool) -> bool {
        true
    }
}

/// A file over transfer v2, in SCRATCH.
fn receive() -> Option<&'static [u8]> {
    let mut sink = Scratch { len: 0 };
    let len = upload_v2(&mut Uart, &mut sink, RAM_BOOT_MAX)?;
    Some(unsafe { core::slice::from_raw_parts(SCRATCH as *const u8, len as usize) })
}

fn report(what: &str, r: Result<(), BootError>) {
    Uart::print_str(what);
    match r {
        Ok(()) => Uart::print_str("OK\n"),
        Err(e) => {
            put_boot_error(&mut Uart, e);
            Uart::print_str("\n");
        }
    }
}

/// The card the first stage handed over, or else one initialized here (inserted after a
/// RAM boot without one); if that fails, the mount reports the read error.
fn card() -> SdCard {
    SdCard::ensure_init();
    SdCard
}

/// `n`: install the received file as `\VUX9K\STAGE2.BIN`.
fn install_received() {
    let Some(file) = receive() else {
        return;
    };
    let r = Volume::mount(card())
        .map_err(BootError::from)
        .and_then(|mut vol| install(&mut vol, STAGE2_WINDOW, file));
    report("[INSTALL] ", r);
}

/// `x` (tests): write the received file as `\VUX9K\STAGE2.BIN` unchecked, keeping
/// `STAGE2.OLD`, to see the first stage fall back to it.
fn write_unchecked() {
    let Some(file) = receive() else {
        return;
    };
    let r = Volume::mount(card()).and_then(|mut vol| {
        let dir = stage2::dir(&mut vol)?;
        if let Some(bin) = vol.find(&dir, &stage2::BIN)? {
            vol.remove(&bin)?;
        }
        vol.create(&dir, &stage2::BIN, file)?;
        Ok::<(), FatError>(())
    });
    report("[UNCHECKED] ", r.map_err(BootError::from));
}

fn print_help() {
    Uart::print_str("Commands:\n");
    Uart::print_str("  [h] Help\n");
    Uart::print_str("  [n] Install a second stage sent over the UART as STAGE2.BIN\n");
    Uart::print_str("  [x] Write a file sent over the UART as STAGE2.BIN, unchecked (tests)\n");
    Uart::print_str("  [r] Soft reset (back to the first stage)\n");
    Uart::print_str(PROMPT);
}

fn soft_reset() -> ! {
    Uart::print_str("[RESET] Soft reset\n");
    // The rest of the command's line, so the first stage doesn't take it for a key
    while Uart::read_byte_timeout(5).is_some() {}
    unsafe {
        core::ptr::write_volatile(map::GPIO_SOFT_RESET as *mut u32, map::SOFT_RESET_KEY);
    }
    loop {} // cov:exclude(the soft reset takes the CPU before this)
}

#[no_mangle]
pub extern "C" fn main(a0: u32, a1: u32, a2: u32) -> ! {
    // The first stage mounted a card (a1 = its partition): take it as it is
    if a1 != 0 {
        SdCard::adopt(a0 & 1 != 0);
    }
    Uart::print_str("\nVUX9K Stage 2 (provisional)\n[S2] a0=0x");
    Uart::print_hex(a0);
    Uart::print_str(" a1=0x");
    Uart::print_hex(a1);
    Uart::print_str(" a2=0x");
    Uart::print_hex(a2);
    Uart::print_str("\n");
    print_help();
    loop {
        let Some(cmd) = Uart::read_byte() else {
            continue;
        };
        match cmd {
            b'\r' => {
                Uart::print_str("\n");
                Uart::print_str(PROMPT);
                continue;
            }
            b'\n' | 0 => continue,
            _ => {}
        }
        Uart::print_str("[CMD:0x");
        Uart::print_hex_byte(cmd);
        Uart::print_str("]\n");
        match cmd {
            b'h' | b'?' => {
                print_help();
                continue;
            }
            b'n' => install_received(),
            b'x' => write_unchecked(),
            b'r' => soft_reset(),
            _ => Uart::print_str("Unknown command\n"),
        }
        Uart::print_str(PROMPT);
    }
}

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

#[panic_handler]
fn panic(_info: &PanicInfo) -> ! {
    Uart::print_str("\n[PANIC]\n");
    loop {}
}
