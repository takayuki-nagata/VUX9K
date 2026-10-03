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

use fw_common::header::{HEADER_LEN, SECTOR};
use fw_common::mailbox::{self, SLOT_UPDATED_BOOT_MAGIC};
use fw_common::update::{precheck, Precheck};
use fw_common::upload::{self, Disk, Host};
use fw_common::{map, mul_u32, slot, slot_sector, SlotHeader};
use gpio::Gpio;
use sdcard::SdCard;
use timer::Timer;
use uart::Uart;

const MAILBOX_REG: *mut u32 = map::MAILBOX as *mut u32;

/// Leave `request` in the mailbox and hand the CPU to the Resident Loader.
fn enter_resident_loader(request: u32) -> ! {
    unsafe {
        core::ptr::write_volatile(MAILBOX_REG, request);
        core::arch::asm!("jr {0}", in(reg) map::RL_ENTRY, options(noreturn));
    }
}

/// The LED sweep of `t` and `k`, ending with LED 0 lit.
fn knight_rider(passes: u32, step_ms: u32) {
    for _ in 0..passes {
        for i in 0..6 {
            Gpio::set_leds(1 << i);
            Timer::delay_ms(step_ms);
        }
        for i in (1..5).rev() {
            Gpio::set_leds(1 << i);
            Timer::delay_ms(step_ms);
        }
    }
    Gpio::set_leds(0x01);
}

fn print_banner() {
    Uart::print_str("\n");
    Uart::print_str("====================================================\n");
    Uart::print_str("  VUX9K Dual-ISA RISC-V / Hack SoC Boot Manager (v");
    Uart::print_dec(BOOT_MGR_VERSION);
    Uart::print_str(")\n");
    Uart::print_str("  Board: Sipeed Tang Nano 9K (Gowin GW1NR-9)\n");
    Uart::print_str("  Clock: 18.0 MHz | UART: 115200 bps | SPI: 400 kHz\n");
    Uart::print_str("====================================================\n\n");
}

fn print_help() {
    Uart::print_str("Available Commands:\n");
    Uart::print_str("  [h] Help menu\n");
    Uart::print_str("  [l] List program slots catalog (Slots 0-9)\n");
    Uart::print_str("  [1-9] Launch program in Slot 1-9 (S2 button = Slot 1)\n");
    Uart::print_str("  [w] Write program slot from UART (Multi-Sector Host Flash)\n");
    Uart::print_str("  [s] Inspect a slot header (s then 0-9; Slot 0 by default)\n");
    Uart::print_str("  [d] Dump Sector 0 (MBR)\n");
    Uart::print_str("  [i] Re-initialize MicroSD Card\n");
    Uart::print_str("  [t] Run hardware diagnostic tests\n");
    Uart::print_str("  [k] Toggle Knight Rider animation\n");
    Uart::print_str("  [r] Reboot SoC via Resident Loader\n");
    Uart::print_str("vux> ");
}

/// The mcycle CSR's low word: CPU clock cycles.
fn mcycle() -> u32 {
    let c: u32;
    unsafe {
        core::arch::asm!(
            ".option push",
            ".option arch, +zicsr",
            "csrr {0}, mcycle",
            ".option pop",
            out(reg) c,
        );
    }
    c
}

fn run_diagnostics() {
    Uart::print_str("[DIAG] Running SoC Diagnostics...\n");

    // 1. and 2. only show something: the LEDs are for the eye, the button is as it is
    Uart::print_str(" 1. GPIO LEDs: ");
    knight_rider(1, 5);
    Uart::print_str("swept 0-5 (check by eye)\n");

    Uart::print_str(" 2. User Button (S2): ");
    if Gpio::get_button() {
        Uart::print_str("PRESSED\n");
    } else {
        Uart::print_str("RELEASED\n");
    }

    // 3. mtime counts at the CPU clock (one tick per cycle at 18 MHz): measure 10 ms of
    // it against mcycle, within 1/64
    Uart::print_str(" 3. Timer (mtime): ");
    let t0 = Timer::get_mtime32();
    let c0 = mcycle();
    Timer::delay_ms(10);
    let ticks = Timer::get_mtime32().wrapping_sub(t0);
    let cycles = mcycle().wrapping_sub(c0);
    Uart::print_str("10ms = ");
    Uart::print_dec(ticks);
    Uart::print_str(" ticks, ");
    Uart::print_dec(cycles);
    Uart::print_str(" CPU cycles ");
    let off = if ticks > cycles {
        ticks - cycles
    } else {
        cycles - ticks
    };
    Uart::print_str(if off <= cycles >> 6 {
        "[PASS]\n"
    } else {
        "[FAIL]\n"
    });

    // 4. MicroSD Card Init
    Uart::print_str(" 4. MicroSD SPI Card: ");
    if SdCard::force_init() {
        Uart::print_str("Detected & Initialized [PASS]\n");
    } else {
        Uart::print_str("No Card / Timeout [FAIL]\n");
    }
    Uart::print_str("[DIAG] Diagnostics Complete.\n\n");
}

fn dump_sector_0() {
    Uart::print_str("[SD] Reading Sector 0 (MBR)...\n");
    let mut buf = [0u8; SECTOR];
    if !SdCard::ensure_init() {
        Uart::print_str("[SD] Card init failed!\n");
        return;
    }
    if !SdCard::read_block(0, &mut buf) {
        Uart::print_str("[SD] Read sector 0 failed!\n");
        return;
    }

    Uart::print_str("         00 01 02 03 04 05 06 07  08 09 0A 0B 0C 0D 0E 0F\n");
    for row in 0..16 {
        Uart::print_str("  0x");
        Uart::print_hex((row * 16) as u32);
        Uart::print_str(": ");
        for col in 0..16 {
            let byte = buf[row * 16 + col];
            Uart::print_hex_byte(byte);
            Uart::write_byte(b' ');
            if col == 7 {
                Uart::write_byte(b' ');
            }
        }
        Uart::print_str("\n");
    }
    Uart::print_str("[SD] Sector 0 Read Successful. Signature: ");
    Uart::print_hex_byte(buf[510]);
    Uart::write_byte(b' ');
    Uart::print_hex_byte(buf[511]);
    Uart::print_str(" [PASS]\n\n");
}

/// Raise it with every Boot Manager change: boards install a slot-0 image only when
/// its header's version is greater (fw_common::update::precheck).
const BOOT_MGR_VERSION: u32 = 13;
#[inline(never)]
fn check_boot_manager_update() {
    // 0. Update boot check: if newly loaded from an update, skip checks and clear mailbox
    let mailbox = unsafe { core::ptr::read_volatile(MAILBOX_REG) };
    if mailbox == SLOT_UPDATED_BOOT_MAGIC {
        unsafe {
            core::ptr::write_volatile(MAILBOX_REG, 0);
        }
        Uart::print_str("\n[UPDATE] Booted newly updated Boot Manager!\n\n");
        return;
    }

    // 1. Safe Mode check: if S2 is held down during reset/POR, bypass Slot 0 update completely
    if Gpio::get_button() {
        Uart::print_str("\n[SAFE MODE] Button S2 held. Bypassing Slot 0 auto-update.\n\n");
        return;
    }

    // 2. Read Slot 0 header from MicroSD (Sector 64)
    let mut sec_buf = [0u8; SECTOR];
    let info = match read_slot_info(0, &mut sec_buf) {
        Some(info) => info,
        None => return, // No SD card or no valid VUX9 header in Slot 0
    };

    // 3. Pre-Verification: RV32 image, size within lower I-RAM, newer version, and a
    // plausible entry instruction (the payload's first word, after the header)
    let h = HEADER_LEN;
    let entry_instr =
        u32::from_le_bytes([sec_buf[h], sec_buf[h + 1], sec_buf[h + 2], sec_buf[h + 3]]);
    match precheck(&info, BOOT_MGR_VERSION, entry_instr) {
        Precheck::Candidate => {}
        Precheck::NotRiscv | Precheck::NotNewer => return,
        Precheck::BadSize(size) => {
            Uart::print_str("\n[UPDATE] Slot 0 image size invalid (");
            Uart::print_dec(size);
            Uart::print_str(" bytes > 14KB limit). Bypassing.\n\n");
            return;
        }
        Precheck::BadEntry(instr) => {
            Uart::print_str("\n[UPDATE] Slot 0 entry instruction invalid (0x");
            Uart::print_hex(instr);
            Uart::print_str("). Bypassing.\n\n");
            return;
        }
    }

    // 4. Verify Payload CRC32 Checksum
    let final_crc =
        match slot::payload_crc(info.size, slot_sector(0), &mut sec_buf, SdCard::read_block) {
            Ok(crc) => crc,
            Err(sector) => {
                Uart::print_str("\n[UPDATE] Error reading Slot 0 sector ");
                Uart::print_dec(sector);
                Uart::print_str(". Bypassing update.\n\n");
                return;
            }
        };

    if final_crc != info.crc32 {
        Uart::print_str("\n[UPDATE] Slot 0 CRC32 mismatch (computed 0x");
        Uart::print_hex(final_crc);
        Uart::print_str(", expected 0x");
        Uart::print_hex(info.crc32);
        Uart::print_str("). Corrupted payload! Bypassing.\n\n");
        return;
    }

    // 5. All checks passed: Launch update via Resident Loader
    Uart::print_str("\n[UPDATE] Verified valid Boot Manager update (v");
    Uart::print_dec(info.version);
    Uart::print_str(", CRC32: 0x");
    Uart::print_hex(final_crc);
    Uart::print_str("). Auto-updating Lower I-RAM via Resident Loader...\n\n");
    Timer::delay_ms(10);
    enter_resident_loader(mailbox::update(SdCard::is_sdhc()));
}

fn read_slot_info(slot: u32, buf: &mut [u8; SECTOR]) -> Option<SlotHeader> {
    if !SdCard::ensure_init() || !SdCard::read_block(slot_sector(slot), buf) {
        return None;
    }
    SlotHeader::parse(buf)
}

fn print_slot_name(info: &SlotHeader) {
    let len = info.name_len();
    if len == 0 {
        Uart::print_str("(unnamed)");
    } else {
        for &b in &info.name[..len] {
            Uart::write_byte(b);
        }
    }
}

#[inline(never)]
fn list_slots() {
    Uart::print_str("\n--- VUX9 Program Slots Catalog (MBR Gap LBA 64-703) ---\n");
    let mut buf = [0u8; SECTOR];
    for slot in 0..=9 {
        Uart::print_str("  Slot ");
        Uart::print_dec(slot);
        Uart::print_str(" (LBA ");
        Uart::print_dec(slot_sector(slot));
        Uart::print_str("): ");
        if let Some(info) = read_slot_info(slot, &mut buf) {
            Uart::print_str("\"");
            print_slot_name(&info);
            Uart::print_str("\" [");
            if info.mode == 0 {
                Uart::print_str("Hack 16b, ");
            } else {
                Uart::print_str("RV32I, ");
            }
            Uart::print_dec(info.size);
            Uart::print_str(" B]");
            if slot == 0 {
                Uart::print_str(" (Boot Manager)");
            } else if slot == 1 {
                Uart::print_str(" (Default / Button S2)");
            }
            Uart::print_str("\n");
        } else {
            if slot == 0 {
                Uart::print_str("[Empty / Using BRAM Fallback]\n");
            } else {
                Uart::print_str("[Empty]\n");
            }
        }
    }
    Uart::print_str("-------------------------------------------------------\n\n");
}

#[inline(never)]
fn inspect_slot(slot: u32) {
    Uart::print_str("[SD] Inspecting Slot ");
    Uart::print_dec(slot);
    Uart::print_str(" (Sector ");
    Uart::print_dec(slot_sector(slot));
    Uart::print_str(")...\n");
    let mut buf = [0u8; SECTOR];
    if let Some(info) = read_slot_info(slot, &mut buf) {
        Uart::print_str("  Magic: 0x56555839 (\"VUX9\" Valid Header) [OK]\n");
        Uart::print_str("  Name:  \"");
        print_slot_name(&info);
        Uart::print_str("\"\n");
        Uart::print_str("  Mode:  ");
        if info.mode == 0 {
            Uart::print_str("0 (Hack 16-bit ISA)\n");
        } else {
            Uart::print_str("1 (RISC-V 32-bit ISA)\n");
        }
        Uart::print_str("  Size:  ");
        Uart::print_dec(info.size);
        Uart::print_str(" bytes\n");
        Uart::print_str("  Flags: 0x");
        Uart::print_hex(info.flags as u32);
        Uart::print_str("\n");
        Uart::print_str("  Version: ");
        Uart::print_dec(info.version);
        Uart::print_str("\n");
        Uart::print_str("  CRC32: 0x");
        Uart::print_hex(info.crc32);
        Uart::print_str("\n");
    } else {
        Uart::print_str("  [Empty or invalid VUX9 header]\n");
    }
}

fn boot_slot(slot: u32) -> ! {
    Uart::print_str("[BOOT] Launching Slot ");
    Uart::print_dec(slot);
    Uart::print_str(" via Resident Loader...\n\n");
    Timer::delay_ms(10);
    SdCard::ensure_init();
    enter_resident_loader(mailbox::launch(slot, SdCard::is_sdhc()));
}

#[inline(never)]
fn read_uart_byte_timeout(timeout_ms: u32) -> Option<u8> {
    let start = Timer::get_mtime32();
    let limit = mul_u32(timeout_ms, map::TICKS_PER_MS);
    while Timer::get_mtime32().wrapping_sub(start) < limit {
        if let Some(b) = Uart::read_byte() {
            return Some(b);
        }
    }
    None
}

impl Host for Uart {
    fn recv(&mut self, timeout_ms: u32) -> Option<u8> {
        read_uart_byte_timeout(timeout_ms)
    }

    fn drain(&mut self) {
        while Uart::read_byte().is_some() {}
    }
}

impl Disk for SdCard {
    fn init(&mut self) -> bool {
        SdCard::ensure_init()
    }

    fn write(&mut self, sector: u32, data: &[u8; SECTOR]) -> bool {
        SdCard::write_block(sector, data)
    }
}

#[no_mangle]
pub extern "C" fn main() -> ! {
    Gpio::set_leds(0x3F);

    SdCard::ensure_init();
    check_boot_manager_update();

    print_banner();
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

        // Poll S2 button (debounced)
        if Gpio::get_button() {
            Timer::delay_ms(20);
            if Gpio::get_button() {
                Uart::print_str("\n[BUTTON] S2 Pressed! Launching Slot 1 (Default App)...\n");
                while Gpio::get_button() {
                    Timer::delay_ms(10);
                }
                boot_slot(1);
            }
        }

        if let Some(cmd) = Uart::read_byte() {
            if cmd != b'\r' && cmd != b'\n' && cmd != 0 {
                Uart::print_str("[CMD:0x");
                Uart::print_hex_byte(cmd);
                Uart::print_str("]\n");
            }
            match cmd {
                b'h' | b'?' => {
                    Uart::print_str("h\n");
                    print_help();
                }
                b'l' => {
                    Uart::print_str("l\n");
                    list_slots();
                    Uart::print_str("vux> ");
                }
                b'1'..=b'9' => {
                    let slot = (cmd - b'0') as u32;
                    Uart::write_byte(cmd);
                    Uart::print_str("\n");
                    boot_slot(slot);
                }
                b'i' => {
                    Uart::print_str("i\n[SD] Initializing...\n");
                    if SdCard::force_init() {
                        Uart::print_str("[SD] Card Ready! [OK]\n");
                    } else {
                        Uart::print_str("[SD] Init Failed / No Card!\n");
                    }
                    Uart::print_str("vux> ");
                }
                b'd' => {
                    Uart::print_str("d\n");
                    dump_sector_0();
                    Uart::print_str("vux> ");
                }
                b's' => {
                    Uart::print_str("s\n");
                    let slot = match read_uart_byte_timeout(50) {
                        Some(b @ b'0'..=b'9') => (b - b'0') as u32,
                        _ => 0,
                    };
                    inspect_slot(slot);
                    Uart::print_str("vux> ");
                }
                b'w' => {
                    upload::upload(&mut Uart, &mut SdCard);
                    Uart::print_str("vux> ");
                }
                b't' => {
                    Uart::print_str("t\n");
                    run_diagnostics();
                    Uart::print_str("vux> ");
                }
                b'k' => {
                    Uart::print_str("k\n[LED] Running Knight Rider...\n");
                    knight_rider(2, 10);
                    Uart::print_str("[LED] Done.\n\nvux> ");
                }
                b'r' => {
                    Uart::print_str("r\n[RESET] Rebooting Boot Manager...\n\n");
                    boot_slot(0);
                }
                b'\r' => {
                    Uart::print_str("\nvux> ");
                }
                b'\n' | 0 => {}
                _ => {}
            }
        }
    }
}

/// start.s's trap_entry: an exception (the Boot Manager enables no interrupts).
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

#[panic_handler]
fn panic(_info: &PanicInfo) -> ! {
    Uart::print_str("\n[PANIC] Kernel Panic!\n");
    loop {}
}
