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
        la sp, 0x20001FE0
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

const MTIME_LOW: *const u32 = 0x4000_1000 as *const u32;

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

fn delay_ticks(ticks: u32) {
    let start = unsafe { core::ptr::read_volatile(MTIME_LOW) };
    while (unsafe { core::ptr::read_volatile(MTIME_LOW) }).wrapping_sub(start) < ticks {}
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
    delay_ticks(270);

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

#[inline(never)]
fn read_word(is_hack: bool) -> u32 {
    let b0 = spi_transfer(0xFF) as u32;
    let b1 = spi_transfer(0xFF) as u32;
    let b2 = spi_transfer(0xFF) as u32;
    let b3 = spi_transfer(0xFF) as u32;
    if is_hack {
        b1 | (b0 << 8) | (b3 << 16) | (b2 << 24)
    } else {
        b0 | (b1 << 8) | (b2 << 16) | (b3 << 24)
    }
}

#[inline(never)]
fn skip_bytes(n: usize) {
    for _ in 0..n {
        spi_transfer(0xFF);
    }
}

fn spi_start_block(is_sdhc: bool, sector_num: u32) -> bool {
    let addr = if is_sdhc { sector_num } else { sector_num << 9 };
    if sd_send_cmd(17, addr, 0xFF) != 0x00 {
        spi_deselect();
        return false;
    }
    for _ in 0..50000 {
        if spi_transfer(0xFF) == 0xFE {
            return true;
        }
    }
    spi_deselect();
    false
}

fn spi_end_block() {
    spi_transfer(0xFF);
    spi_transfer(0xFF);
    spi_deselect();
}

fn load_slot_from_sd(is_sdhc: bool, slot_id: u32) -> bool {
    let start_sector = 64 + (slot_id << 6);

    if !spi_start_block(is_sdhc, start_sector) {
        return false;
    }

    let magic = read_word(false);
    let mode = read_word(false);
    let size = read_word(false);

    skip_bytes(52);

    if magic != VUX_MAGIC {
        spi_end_block();
        return false;
    }

    let max_size = 14336;
    if size == 0 || size > max_size {
        spi_end_block();
        return false;
    }

    let is_hack = mode == 0;
    let i_ram_words = 0x0000_0000usize as *mut u32;
    let mut word_idx = 0usize;

    let chunk0 = if size > 448 { 448 } else { size as usize };
    let mut i = 0usize;
    while i < chunk0 {
        let w = read_word(is_hack);
        unsafe {
            core::ptr::write_volatile(i_ram_words.add(word_idx), w);
        }
        word_idx += 1;
        i += 4;
    }

    skip_bytes(448 - chunk0);
    spi_end_block();

    let mut copied = chunk0;
    let mut sector = start_sector + 1;

    while copied < size as usize {
        if !spi_start_block(is_sdhc, sector) {
            return false;
        }
        let chunk = if size as usize - copied > 512 { 512 } else { size as usize - copied };
        let mut j = 0usize;
        while j < chunk {
            let w = read_word(is_hack);
            unsafe {
                core::ptr::write_volatile(i_ram_words.add(word_idx), w);
            }
            word_idx += 1;
            j += 4;
        }
        skip_bytes(512 - chunk);
        spi_end_block();
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

    uart_puts(b"\n[RL] Boot\n");

    let is_slot_update = (target & 0xFFFF_0000) == 0xA55A_0000;
    let is_sdhc = (target & 0x100) != 0;
    let slot_num = if is_slot_update { 0 } else { target & 0xFF };

    if is_slot_update || slot_num <= 9 {
        uart_puts(b"\n[RL] Slot ");
        uart_putc(b'0' + slot_num as u8);
        uart_puts(b"\n");

        // Prepare SPI bus
        spi_deselect();
        for _ in 0..8 {
            spi_transfer(0xFF);
        }

        if load_slot_from_sd(is_sdhc, slot_num) {
            if is_slot_update {
                unsafe {
                    core::ptr::write_volatile(MAILBOX_REG, 0x5A5A_B002);
                }
            }
            unsafe {
                core::arch::asm!("jr {0}", in(reg) 0x0000_0000usize, options(noreturn));
            }
        }
        uart_puts(b"[RL] Load failed, returning to Boot Manager\n");
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
