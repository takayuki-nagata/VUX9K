// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! The first stage (the bitstream's boot ROM, at 0x0): loads `\VUX9K\STAGE2.BIN` from the
//! card's FAT32 partition into the PSRAM and starts it (`STAGE2.OLD` when `.BIN` is
//! missing or broken), unless S2 is held or a key arrives right after the banner; then
//! it stays at its prompt, which can also start a second stage sent over the UART (a RAM
//! boot). The second stage starts with a0 = 1 on an SDHC card (else 0), a1 = the FAT32
//! partition's LBA (0 if none was mounted), a2 = STAGE1_VERSION.

#![no_std]
#![no_main]

use core::arch::global_asm;
use core::panic::PanicInfo;

global_asm!(include_str!("../bootstrap/start.s"));

use fw_common::boot::{check_stage2, put_boot_error, RamBoot, RAM_BOOT_MAX, STAGE2_WINDOW};
use fw_common::fat::stage2::{self, load_file, select_boot, BootError, Which};
use fw_common::fat::Volume;
use fw_common::header::ImageHeader;
use fw_common::map;
use fw_common::upload::upload_v2;
use fw_drivers::gpio::Gpio;
use fw_drivers::sdcard::SdCard;
use fw_drivers::timer::Timer;
use fw_drivers::uart::Uart;

/// Raise it with every change of the first stage: the second stage gets it in a2, and
/// the tests read it from here.
const STAGE1_VERSION: u32 = 1;
/// After the banner, a key within this keeps the first stage at its prompt (as S2 held
/// does): vux_tool.py sends keys until the prompt answers.
const AUTOBOOT_MS: u32 = 100;
const PROMPT: &str = "s1> ";

fn print_banner() {
    Uart::print_str("\n====================================================\n");
    Uart::print_str("  VUX9K Stage 1 (v");
    Uart::print_dec(STAGE1_VERSION);
    Uart::print_str(")\n");
    Uart::print_str("  Board: Sipeed Tang Nano 9K (Gowin GW1NR-9)\n");
    Uart::print_str("  Clock: 18.0 MHz | UART: 115200 bps | SD: 391 kHz init, 3 MHz\n");
    Uart::print_str("====================================================\n");
}

fn print_help() {
    Uart::print_str("Commands:\n");
    Uart::print_str("  [h] Help\n");
    Uart::print_str("  [b] Boot \\VUX9K\\STAGE2.BIN (STAGE2.OLD if it is broken)\n");
    Uart::print_str("  [u] Boot a second stage sent over the UART (RAM boot)\n");
    Uart::print_str("  [i] SD card, FAT32 and STAGE2 files\n");
    Uart::print_str("  [r] Soft reset (boots again)\n");
    Uart::print_str(PROMPT);
}

/// Where a loaded payload goes: the PSRAM, through the data path.
fn store(addr: u32, data: &[u8]) {
    unsafe { core::ptr::copy_nonoverlapping(data.as_ptr(), addr as *mut u8, data.len()) }
}

/// Start the second stage at `entry` (module comment for a0-a2).
fn start(entry: u32, part_lba: u32) -> ! {
    Uart::print_str("[BOOT] Starting at 0x");
    Uart::print_hex(entry);
    Uart::print_str("\n\n");
    let sdhc = SdCard::is_sdhc() as u32;
    unsafe {
        core::arch::asm!(
            ".option push",
            ".option arch, +zifencei",
            "fence.i",
            ".option pop",
            "jr {entry}",
            entry = in(reg) entry,
            in("a0") sdhc,
            in("a1") part_lba,
            in("a2") STAGE1_VERSION,
            options(noreturn),
        );
    }
}

fn print_error(e: BootError) {
    put_boot_error(&mut Uart, e);
    Uart::print_str("\n");
}

fn print_header(h: &ImageHeader) {
    Uart::print_str("v");
    Uart::print_dec(h.version);
    Uart::print_str(" \"");
    for &b in &h.name[..h.name_len()] {
        Uart::write_byte(b);
    }
    Uart::print_str("\" ");
    Uart::print_dec(h.size);
    Uart::print_str(" B");
}

/// The card's FAT32 volume, after reporting what is missing.
fn mount() -> Option<Volume<SdCard>> {
    if !SdCard::ensure_init() {
        Uart::print_str("No SD card\n");
        return None;
    }
    match Volume::mount(SdCard) {
        Ok(vol) => Some(vol),
        Err(e) => {
            print_error(e.into());
            None
        }
    }
}

/// Load and start the second stage; returns if there is none to start.
fn boot() {
    Uart::print_str("[BOOT] ");
    let Some(mut vol) = mount() else {
        return;
    };
    match select_boot(&mut vol, STAGE2_WINDOW, &mut store) {
        Ok((which, h)) => {
            Uart::print_str(if which == Which::Bin {
                "STAGE2.BIN "
            } else {
                "STAGE2.OLD (STAGE2.BIN missing or broken) "
            });
            print_header(&h);
            Uart::print_str("\n");
            match check_stage2(&h) {
                Ok(()) => start(h.entry_point, vol.partition_lba()),
                Err(e) => {
                    Uart::print_str("[BOOT] ");
                    print_error(e);
                }
            }
        }
        Err((bin, old)) => {
            Uart::print_str("STAGE2.BIN: ");
            put_boot_error(&mut Uart, bin);
            Uart::print_str("; STAGE2.OLD: ");
            print_error(old);
        }
    }
}

/// `u`: a second stage over transfer v2, started once its CRC checks.
fn ram_boot() {
    let mut write = store;
    let mut sink = RamBoot::new(STAGE2_WINDOW, &mut write);
    upload_v2(&mut Uart, &mut sink, RAM_BOOT_MAX);
    if let Some(e) = sink.error {
        Uart::print_str("[BOOT] ");
        print_error(e);
    }
    if let Some(h) = sink.header() {
        // The card is the second stage's business: hand over what is known of it
        let part_lba = if SdCard::ensure_init() {
            Volume::mount(SdCard).map_or(0, |v| v.partition_lba())
        } else {
            0
        };
        start(h.entry_point, part_lba);
    }
}

/// `i`: one of the second stage's files, checked as a boot would (not stored).
fn show_file(vol: &mut Volume<SdCard>, name: &[u8; 11]) {
    Uart::print_str("[FAT] \\VUX9K\\");
    for (i, &b) in name.iter().enumerate() {
        if b != b' ' {
            if i == 8 {
                Uart::write_byte(b'.');
            }
            Uart::write_byte(b);
        }
    }
    Uart::print_str(": ");
    let mut check = || -> Result<ImageHeader, BootError> {
        let file = stage2::find_file(vol, name)?;
        load_file(vol, &file, STAGE2_WINDOW, &mut |_, _| ())
    };
    match check() {
        Ok(h) => {
            print_header(&h);
            Uart::print_str(" [OK]\n");
        }
        Err(e) => print_error(e),
    }
}

fn sd_info() {
    Uart::print_str("[SD] ");
    if !SdCard::force_init() {
        Uart::print_str("No card / init failed\n");
        return;
    }
    Uart::print_str(if SdCard::is_sdhc() { "SDHC" } else { "SDSC" });
    Uart::print_str(" card\n[FAT] ");
    let mut vol = match Volume::mount(SdCard) {
        Ok(vol) => vol,
        Err(e) => {
            print_error(e.into());
            return;
        }
    };
    Uart::print_str("FAT32 at LBA ");
    Uart::print_dec(vol.partition_lba());
    Uart::print_str(", ");
    Uart::print_dec(vol.cluster_bytes());
    Uart::print_str(" B clusters\n");
    show_file(&mut vol, &stage2::BIN);
    show_file(&mut vol, &stage2::OLD);
}

/// S2 held, or a key right after the banner: stay at the prompt.
fn safe_mode() -> bool {
    let why = if Gpio::get_button() {
        "Button S2 held"
    } else if Uart::read_byte_timeout(AUTOBOOT_MS).is_some() {
        "Key received"
    } else {
        return false;
    };
    Uart::print_str("[SAFE MODE] ");
    Uart::print_str(why);
    Uart::print_str(": staying at the prompt.\n");
    true
}

#[no_mangle]
pub extern "C" fn main() -> ! {
    Gpio::set_leds(0x3F);
    // Bytes from before a soft reset (a command's line end) are not a key press
    while Uart::read_byte().is_some() {}
    print_banner();
    if !safe_mode() {
        boot();
    }
    Uart::print_str("\n");
    print_help();

    let mut loop_count: u32 = 0;
    let mut led_val: u8 = 0x3E;
    loop {
        loop_count = loop_count.wrapping_add(1);
        if loop_count == 500_000 {
            loop_count = 0;
            led_val ^= 0x01;
            Gpio::set_leds(led_val);
        }
        let Some(cmd) = Uart::read_byte() else {
            continue;
        };
        if cmd == b'\r' {
            Uart::print_str("\n");
            Uart::print_str(PROMPT);
            continue;
        }
        if cmd == b'\n' || cmd == 0 {
            continue;
        }
        Uart::print_str("[CMD:0x");
        Uart::print_hex_byte(cmd);
        Uart::print_str("]\n");
        match cmd {
            b'h' | b'?' => {}
            b'b' => boot(),
            b'u' => ram_boot(),
            b'i' => sd_info(),
            b'r' => {
                Uart::print_str("[RESET] Soft reset\n");
                unsafe {
                    core::ptr::write_volatile(
                        map::GPIO_SOFT_RESET as *mut u32,
                        map::SOFT_RESET_KEY,
                    );
                }
                loop {} // cov:exclude(the soft reset takes the CPU before this)
            }
            _ => Uart::print_str("Unknown command\n"),
        }
        if cmd == b'h' || cmd == b'?' {
            print_help();
        } else {
            Uart::print_str(PROMPT);
        }
    }
}

/// start.s's trap_entry: an exception (the first stage enables no interrupts).
/// Report it and stop, blinking LEDs 1/3/5 against 0/2/4.
#[no_mangle]
extern "C" fn trap_report(mcause: u32, mepc: u32, mtval: u32) -> ! {
    Uart::print_str("\n[TRAP] mcause=0x");
    Uart::print_hex(mcause);
    Uart::print_str(" mepc=0x");
    Uart::print_hex(mepc);
    Uart::print_str(" mtval=0x");
    Uart::print_hex(mtval);
    Uart::print_str("\n[TRAP] Stopped. Reset the board.\n");
    let mut pattern = 0x2A;
    loop {
        Gpio::set_leds(pattern);
        Timer::delay_ms(250);
        pattern ^= 0x3F;
    }
}

/// A failed bounds check (the code has none that can fail).
#[panic_handler]
#[rustfmt::skip] // keeps the markers on the lines they mark
fn panic(_info: &PanicInfo) -> ! { // cov:exclude(no reachable panic)
    Uart::print_str("\n[PANIC] Kernel Panic!\n"); // cov:exclude(no reachable panic)
    loop {} // cov:exclude(no reachable panic)
}
