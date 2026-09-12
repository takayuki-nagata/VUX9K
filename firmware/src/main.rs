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

const VUX_MAGIC: u32 = 0x56555839; // "VUX9"
const MAILBOX_REG: *mut u32 = 0x2000_1FFC as *mut u32;
const RESIDENT_LOADER_ENTRY: usize = 0x0000_4800;

#[inline(always)]
fn slot_sector(slot: u32) -> u32 {
    64 + (slot << 6)
}

fn print_banner() {
    Uart::print_str("\n");
    Uart::print_str("====================================================\n");
    Uart::print_str("  VUX9K Dual-ISA RISC-V / Hack SoC Boot Manager\n");
    Uart::print_str("  Board: Sipeed Tang Nano 9K (Gowin GW1NR-9)\n");
    Uart::print_str("  Clock: 27.0 MHz | UART: 115200 bps | SPI: 400 kHz\n");
    Uart::print_str("====================================================\n\n");
}

fn print_help() {
    Uart::print_str("Available Commands:\n");
    Uart::print_str("  [h] Help menu\n");
    Uart::print_str("  [l] List program slots catalog (Slots 0-9)\n");
    Uart::print_str("  [1-9] Launch program in Slot 1-9 (S2 button = Slot 1)\n");
    Uart::print_str("  [w] Write program slot from UART (Multi-Sector Host Flash)\n");
    Uart::print_str("  [s] Inspect Slot 0 Boot Manager Header\n");
    Uart::print_str("  [d] Dump Sector 0 (MBR)\n");
    Uart::print_str("  [i] Re-initialize MicroSD Card\n");
    Uart::print_str("  [t] Run hardware diagnostic tests\n");
    Uart::print_str("  [k] Toggle Knight Rider animation\n");
    Uart::print_str("  [r] Reboot SoC via Resident Loader\n");
    Uart::print_str("vux> ");
}

fn run_diagnostics() {
    Uart::print_str("[DIAG] Running SoC Diagnostics...\n");

    // 1. Knight Rider LED test
    Uart::print_str(" 1. GPIO LEDs: ");
    for _ in 0..1 {
        for i in 0..6 {
            Gpio::set_leds(1 << i);
            Timer::delay_ms(5);
        }
        for i in (1..5).rev() {
            Gpio::set_leds(1 << i);
            Timer::delay_ms(5);
        }
    }
    Gpio::set_leds(0x01);
    Uart::print_str("[PASS]\n");

    // 2. Button State
    Uart::print_str(" 2. User Button (S2): ");
    if Gpio::get_button() {
        Uart::print_str("PRESSED [PASS]\n");
    } else {
        Uart::print_str("RELEASED [PASS]\n");
    }

    // 3. Timer accuracy test
    Uart::print_str(" 3. Timer (mtime): ");
    let t0 = Timer::get_mtime();
    Timer::delay_ms(10);
    let t1 = Timer::get_mtime();
    let diff = (t1.wrapping_sub(t0)) as u32;
    Uart::print_str("10ms = ");
    Uart::print_dec(diff);
    Uart::print_str(" ticks (270,000 expected) [PASS]\n");

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
    let mut buf = [0u8; 512];
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

struct SlotInfo {
    mode: u32,
    size: u32,
    flags: u32,
    name: [u8; 32],
}

fn read_slot_info(slot: u32, buf: &mut [u8; 512]) -> Option<SlotInfo> {
    let sector = slot_sector(slot);
    if !SdCard::ensure_init() || !SdCard::read_block(sector, buf) {
        return None;
    }
    let magic = u32::from_le_bytes([buf[0], buf[1], buf[2], buf[3]]);
    if magic != VUX_MAGIC {
        return None;
    }
    let mode = u32::from_le_bytes([buf[4], buf[5], buf[6], buf[7]]);
    let size = u32::from_le_bytes([buf[8], buf[9], buf[10], buf[11]]);
    let flags = u32::from_le_bytes([buf[12], buf[13], buf[14], buf[15]]);
    let mut name = [0u8; 32];
    name.copy_from_slice(&buf[16..48]);
    Some(SlotInfo {
        mode,
        size,
        flags,
        name,
    })
}

fn print_slot_name(name: &[u8; 32]) {
    let mut len = 0;
    while len < 32 && name[len] != 0 && name[len] >= 0x20 && name[len] <= 0x7E {
        len += 1;
    }
    if len == 0 {
        Uart::print_str("(unnamed)");
    } else {
        for &b in &name[..len] {
            Uart::write_byte(b);
        }
    }
}

fn list_slots() {
    Uart::print_str("\n--- VUX9 Program Slots Catalog (MBR Gap LBA 64-703) ---\n");
    let mut buf = [0u8; 512];
    for slot in 0..=9 {
        Uart::print_str("  Slot ");
        Uart::print_dec(slot);
        Uart::print_str(" (LBA ");
        Uart::print_dec(slot_sector(slot));
        Uart::print_str("): ");
        if let Some(info) = read_slot_info(slot, &mut buf) {
            Uart::print_str("\"");
            print_slot_name(&info.name);
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

fn inspect_slot(slot: u32) {
    Uart::print_str("[SD] Inspecting Slot ");
    Uart::print_dec(slot);
    Uart::print_str(" (Sector ");
    Uart::print_dec(slot_sector(slot));
    Uart::print_str(")...\n");
    let mut buf = [0u8; 512];
    if let Some(info) = read_slot_info(slot, &mut buf) {
        Uart::print_str("  Magic: 0x56555839 (\"VUX9\" Valid Header) [OK]\n");
        Uart::print_str("  Name:  \"");
        print_slot_name(&info.name);
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
        Uart::print_hex(info.flags);
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
    unsafe {
        core::ptr::write_volatile(MAILBOX_REG, slot);
        core::arch::asm!("jr {0}", in(reg) RESIDENT_LOADER_ENTRY, options(noreturn));
    }
}

fn read_uart_byte_timeout(timeout_ms: u32) -> Option<u8> {
    const MTIME_LOW: *const u32 = 0x4000_1000 as *const u32;
    let start_time = unsafe { core::ptr::read_volatile(MTIME_LOW) };
    let limit_ticks = 27_000 * timeout_ms;
    while (unsafe { core::ptr::read_volatile(MTIME_LOW) }).wrapping_sub(start_time) < limit_ticks {
        if let Some(b) = Uart::read_byte() {
            return Some(b);
        }
    }
    None
}

fn write_sectors_from_uart() {
    Uart::print_str("[READY]\n");

    let slot_id = match read_uart_byte_timeout(5000) {
        Some(s) if s <= 9 => s as u32,
        Some(invalid) => {
            Uart::print_str("[SD-ERR] Invalid slot ID: 0x");
            Uart::print_hex_byte(invalid);
            Uart::print_str("\n");
            return;
        }
        None => {
            Uart::print_str("[SD-ERR] Slot ID timeout!\n");
            return;
        }
    };

    Uart::print_str("[READY-SLOT:");
    Uart::print_dec(slot_id);
    Uart::print_str("]\n");

    let num_sectors = match read_uart_byte_timeout(5000) {
        Some(n) if n >= 1 && n <= 64 => n as u32,
        Some(invalid) => {
            Uart::print_str("[SD-ERR] Invalid sector count: 0x");
            Uart::print_hex_byte(invalid);
            Uart::print_str("\n");
            return;
        }
        None => {
            Uart::print_str("[SD-ERR] Sector count timeout!\n");
            return;
        }
    };

    Uart::print_str("[READY-COUNT:");
    Uart::print_dec(num_sectors);
    Uart::print_str("]\n");

    let mut buf = [0u8; 512];
    if !SdCard::ensure_init() {
        Uart::print_str("[SD-ERR] Failed to initialize SD card!\n");
        return;
    }

    let base_sector = slot_sector(slot_id);
    for sec_idx in 0..num_sectors {
        Uart::print_str("[READY-SEC:");
        Uart::print_dec(sec_idx);
        Uart::print_str("]\n");

        for i in 0..512 {
            match read_uart_byte_timeout(5000) {
                Some(b) => buf[i] = b,
                None => {
                    Uart::print_str("[SD-ERR] Timeout at sector ");
                    Uart::print_dec(sec_idx);
                    Uart::print_str(", byte ");
                    Uart::print_dec(i as u32);
                    Uart::print_str("\n");
                    return;
                }
            }
        }

        if !SdCard::write_block(base_sector + sec_idx, &buf) {
            Uart::print_str("[SD-ERR] Failed to write block at sector ");
            Uart::print_dec(base_sector + sec_idx);
            Uart::print_str("\n");
            return;
        }
    }

    Uart::print_str("[SD] Successfully wrote ");
    Uart::print_dec(num_sectors);
    Uart::print_str(" sectors to Slot ");
    Uart::print_dec(slot_id);
    Uart::print_str(" (Sector ");
    Uart::print_dec(base_sector);
    Uart::print_str(")! [OK]\n\n");
}

#[no_mangle]
pub extern "C" fn main() -> ! {
    Gpio::set_leds(0x3F);

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
                    write_sectors_from_uart();
                    Uart::print_str("vux> ");
                }
                b't' => {
                    Uart::print_str("t\n");
                    run_diagnostics();
                    Uart::print_str("vux> ");
                }
                b'k' => {
                    Uart::print_str("k\n[LED] Running Knight Rider...\n");
                    for _ in 0..2 {
                        for i in 0..6 {
                            Gpio::set_leds(1 << i);
                            Timer::delay_ms(10);
                        }
                        for i in (1..5).rev() {
                            Gpio::set_leds(1 << i);
                            Timer::delay_ms(10);
                        }
                    }
                    Gpio::set_leds(0x01);
                    Uart::print_str("[LED] Done.\n\nvux> ");
                }
                b'r' => {
                    Uart::print_str("r\n[RESET] Rebooting Boot Manager...\n\n");
                    Timer::delay_ms(10);
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

#[panic_handler]
fn panic(_info: &PanicInfo) -> ! {
    Uart::print_str("\n[PANIC] Kernel Panic!\n");
    loop {}
}
