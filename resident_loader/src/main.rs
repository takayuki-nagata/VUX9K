// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

#![no_std]
#![no_main]

use core::arch::global_asm;
use core::panic::PanicInfo;

global_asm!(
    r#"
    .section .text.entry
    .global _start
    .type _start, @function
    _start:
        la sp, 0x20002000
        call loader_main
    1:
        j 1b
    "#
);

const SD_DATA: *mut u32 = 0x4000_2000 as *mut u32;
const SD_CS: *mut u32 = 0x4000_2004 as *mut u32;
const SD_STATUS: *const u32 = 0x4000_2008 as *const u32;

const GPIO_MODE_REG: *mut u32 = 0x4000_3008 as *mut u32;
const MAILBOX_REG: *mut u32 = 0x2000_1FFC as *mut u32;

const UART_DATA: *mut u8 = 0x4000_0000 as *mut u8;
const UART_STATUS: *const u32 = 0x4000_0004 as *const u32;

const VUX_MAGIC: u32 = 0x56555839; // "VUX9"

fn uart_putc(c: u8) {
    unsafe {
        while (core::ptr::read_volatile(UART_STATUS) & 0x2) != 0 {}
        core::ptr::write_volatile(UART_DATA, c);
    }
}

fn uart_puts(s: &[u8]) {
    for &b in s {
        uart_putc(b);
    }
}

fn delay(loops: u32) {
    for _ in 0..loops {
        unsafe { core::arch::asm!("nop"); }
    }
}

fn spi_set_cs(active: bool) {
    unsafe {
        core::ptr::write_volatile(SD_CS, if active { 0 } else { 1 });
    }
}

fn spi_transfer(byte: u8) -> u8 {
    unsafe {
        while (core::ptr::read_volatile(SD_STATUS) & 0x1) != 0 {}
        core::ptr::write_volatile(SD_DATA, byte as u32);
        while (core::ptr::read_volatile(SD_STATUS) & 0x1) != 0 {}
        (core::ptr::read_volatile(SD_DATA) & 0xFF) as u8
    }
}

fn spi_deselect() {
    spi_set_cs(false);
    spi_transfer(0xFF);
}

fn sd_send_cmd(cmd: u8, arg: u32, crc: u8) -> u8 {
    spi_set_cs(true);
    delay(50);

    spi_transfer(0x40 | cmd);
    spi_transfer((arg >> 24) as u8);
    spi_transfer((arg >> 16) as u8);
    spi_transfer((arg >> 8) as u8);
    spi_transfer(arg as u8);
    spi_transfer(crc);

    for _ in 0..200 {
        let res = spi_transfer(0xFF);
        if res != 0xFF {
            return res;
        }
    }
    0xFF
}

fn sd_init() -> Option<bool> {
    spi_deselect();
    for _ in 0..16 {
        spi_transfer(0xFF);
    }
    delay(50_000);

    // CMD0 (GO_IDLE_STATE)
    let mut ok = false;
    for _ in 0..20 {
        if sd_send_cmd(0, 0, 0x95) == 0x01 {
            ok = true;
            break;
        }
        spi_deselect();
        delay(10_000);
    }
    if !ok {
        return None;
    }

    // CMD8 (SEND_IF_COND)
    let is_v2 = sd_send_cmd(8, 0x0000_01AA, 0x87) == 0x01;
    for _ in 0..4 {
        spi_transfer(0xFF);
    }
    spi_deselect();

    // ACMD41 loop
    let arg = if is_v2 { 0x4000_0000 } else { 0 };
    let mut ready = false;
    for _ in 0..1000 {
        sd_send_cmd(55, 0, 0x65);
        spi_deselect();

        if sd_send_cmd(41, arg, 0x77) == 0x00 {
            ready = true;
            break;
        }
        spi_deselect();
        delay(10_000);
    }
    if !ready {
        return None;
    }

    // CMD58 (READ_OCR)
    let mut sdhc = false;
    if is_v2 && sd_send_cmd(58, 0, 0xFD) == 0x00 {
        let b0 = spi_transfer(0xFF);
        for _ in 0..3 {
            spi_transfer(0xFF);
        }
        sdhc = (b0 & 0x40) != 0;
    }
    spi_deselect();

    // CMD16 (SET_BLOCKLEN = 512)
    sd_send_cmd(16, 512, 0xFF);
    spi_deselect();

    Some(sdhc)
}

fn sd_read_block(is_sdhc: bool, sector_num: u32, buf: &mut [u8; 512]) -> bool {
    let addr = if is_sdhc { sector_num } else { sector_num << 9 };

    if sd_send_cmd(17, addr, 0xFF) != 0x00 {
        spi_deselect();
        return false;
    }

    let mut ready = false;
    for _ in 0..50000 {
        if spi_transfer(0xFF) == 0xFE {
            ready = true;
            break;
        }
    }
    if !ready {
        spi_deselect();
        return false;
    }

    for b in buf.iter_mut() {
        *b = spi_transfer(0xFF);
    }

    spi_transfer(0xFF);
    spi_transfer(0xFF);
    spi_deselect();
    true
}

fn load_slot_from_sd(is_sdhc: bool, slot_id: u32) -> bool {
    let start_sector = 64 + (slot_id << 6);
    let mut buf = [0u8; 512];

    if !sd_read_block(is_sdhc, start_sector, &mut buf) {
        return false;
    }

    let magic = u32::from_le_bytes([buf[0], buf[1], buf[2], buf[3]]);
    if magic != VUX_MAGIC {
        return false;
    }

    let mode = u32::from_le_bytes([buf[4], buf[5], buf[6], buf[7]]);
    let size = u32::from_le_bytes([buf[8], buf[9], buf[10], buf[11]]);

    if size == 0 || size > 18432 {
        return false;
    }

    let i_ram = 0x0000_0000usize as *mut u8;

    let chunk0 = if size > 448 { 448 } else { size as usize };
    unsafe {
        for i in 0..chunk0 {
            core::ptr::write_volatile(i_ram.add(i), buf[64 + i]);
        }
    }

    let mut copied = chunk0;
    let mut sector = start_sector + 1;

    while copied < size as usize {
        if !sd_read_block(is_sdhc, sector, &mut buf) {
            return false;
        }
        let chunk = if size as usize - copied > 512 { 512 } else { size as usize - copied };
        unsafe {
            for i in 0..chunk {
                core::ptr::write_volatile(i_ram.add(copied + i), buf[i]);
            }
        }
        copied += chunk;
        sector += 1;
    }

    unsafe {
        core::ptr::write_volatile(GPIO_MODE_REG, (mode & 1) | 2);
    }

    true
}

#[no_mangle]
pub extern "C" fn loader_main() -> ! {
    let target = unsafe { core::ptr::read_volatile(MAILBOX_REG) };
    unsafe { core::ptr::write_volatile(MAILBOX_REG, 0); }

    let sd_opt = sd_init();

    if target >= 1 && target <= 9 {
        uart_puts(b"\n[RL] Slot ");
        uart_putc(b'0' + target as u8);
        uart_puts(b"\n");

        if let Some(sdhc) = sd_opt {
            if load_slot_from_sd(sdhc, target) {
                unsafe {
                    core::arch::asm!("jr {0}", in(reg) 0x0000_0000usize, options(noreturn));
                }
            }
        }
    }

    if let Some(sdhc) = sd_opt {
        if load_slot_from_sd(sdhc, 0) {
            unsafe {
                core::arch::asm!("jr {0}", in(reg) 0x0000_0000usize, options(noreturn));
            }
        }
    }

    // Fallback: Boot Manager in BRAM
    unsafe {
        core::ptr::write_volatile(GPIO_MODE_REG, 3);
        core::arch::asm!("jr {0}", in(reg) 0x0000_0000usize, options(noreturn));
    }
}

#[panic_handler]
fn panic(_info: &PanicInfo) -> ! {
    loop {}
}
