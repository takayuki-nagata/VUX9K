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

use fw_common::header::{HEADER_VERSION, VUX_MAGIC};
use fw_common::mailbox::{SLOT_UPDATED_BOOT_MAGIC, SLOT_UPDATE_MAGIC};
use fw_common::map;

const SD_DATA: *mut u32 = map::SD_DATA as *mut u32;
const SD_CS: *mut u32 = map::SD_CS as *mut u32;
const SD_STATUS: *const u32 = map::SD_STATUS as *const u32;

const GPIO_RESET_REG: *mut u32 = map::GPIO_SOFT_RESET as *mut u32;
// ISA the CPU starts in after the next soft reset (gpio_controller 0x8): the loaded
// slot's, rather than a guess from its first instruction
const GPIO_BOOT_MODE_REG: *mut u32 = map::GPIO_BOOT_MODE as *mut u32;
const BOOT_MODE_VALID: u32 = map::BOOT_MODE_VALID;
const BOOT_MODE_RV32: u32 = BOOT_MODE_VALID | 1;
const RESET_MAGIC: u32 = map::SOFT_RESET_KEY;
const SCRATCH_SDHC_REG: *mut u32 = map::LOADER_WORDS as *mut u32;
const MAILBOX_REG: *mut u32 = map::MAILBOX as *mut u32;

const UART_DATA: *mut u8 = map::UART_DATA as *mut u8;
const UART_STATUS: *const u32 = map::UART_STATUS as *const u32;

const MTIME_LOW: *const u32 = map::MTIME_LO as *const u32;

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
    delay_ticks(10 * map::TICKS_PER_US);

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
fn read_word() -> u32 {
    let b0 = spi_transfer(0xFF) as u32;
    let b1 = spi_transfer(0xFF) as u32;
    let b2 = spi_transfer(0xFF) as u32;
    let b3 = spi_transfer(0xFF) as u32;
    b0 | (b1 << 8) | (b2 << 16) | (b3 << 24)
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

/// Loads a slot into I-RAM. Returns the value for GPIO_BOOT_MODE_REG (valid bit and the
/// header's ISA, 1 = RV32 / 0 = Hack), or 0 if the slot couldn't be loaded.
fn load_slot_from_sd(is_sdhc: bool, slot_id: u32) -> u32 {
    let start_sector = 64 + (slot_id << 6);

    if !spi_start_block(is_sdhc, start_sector) {
        uart_puts(b"[RL] E1\n");
        return 0;
    }

    let mut header = [0u8; 64];
    for b in header.iter_mut() {
        *b = spi_transfer(0xFF);
    }

    let magic = (header[0] as u32)
        | ((header[1] as u32) << 8)
        | ((header[2] as u32) << 16)
        | ((header[3] as u32) << 24);
    let ver_flags = (header[4] as u32)
        | ((header[5] as u32) << 8)
        | ((header[6] as u32) << 16)
        | ((header[7] as u32) << 24);
    let mode = (header[8] as u32)
        | ((header[9] as u32) << 8)
        | ((header[10] as u32) << 16)
        | ((header[11] as u32) << 24);
    let size = (header[12] as u32)
        | ((header[13] as u32) << 8)
        | ((header[14] as u32) << 16)
        | ((header[15] as u32) << 24);
    let load_addr = (header[16] as u32)
        | ((header[17] as u32) << 8)
        | ((header[18] as u32) << 16)
        | ((header[19] as u32) << 24);

    if magic != VUX_MAGIC || (ver_flags & 0xFFFF) != HEADER_VERSION as u32 {
        uart_puts(b"[RL] E2\n");
        skip_bytes(448);
        spi_end_block();
        return 0;
    }

    let max_size = 14336;
    if size == 0 || size > max_size {
        uart_puts(b"[RL] E3\n");
        skip_bytes(448);
        spi_end_block();
        return 0;
    }

    let dest_ptr = load_addr as *mut u32;
    let mut word_idx = 0usize;

    let chunk0 = if size > 448 { 448 } else { size as usize };
    let chunk0_words = (chunk0 + 3) / 4;
    for _ in 0..chunk0_words {
        let w = read_word();
        unsafe {
            core::ptr::write_volatile(dest_ptr.add(word_idx), w);
        }
        word_idx += 1;
    }

    skip_bytes(448 - chunk0_words * 4);
    spi_end_block();

    let mut copied = chunk0;
    let mut sector = start_sector + 1;

    while copied < size as usize {
        if !spi_start_block(is_sdhc, sector) {
            uart_puts(b"[RL] E4\n");
            return 0;
        }
        let chunk = if size as usize - copied > 512 {
            512
        } else {
            size as usize - copied
        };
        let chunk_words = (chunk + 3) / 4;
        for _ in 0..chunk_words {
            let w = read_word();
            unsafe {
                core::ptr::write_volatile(dest_ptr.add(word_idx), w);
            }
            word_idx += 1;
        }
        skip_bytes(512 - chunk_words * 4);
        spi_end_block();
        copied += chunk;
        sector += 1;
    }

    BOOT_MODE_VALID | (mode & 1)
}

#[no_mangle]
pub extern "C" fn loader_main() -> ! {
    let target = unsafe { core::ptr::read_volatile(MAILBOX_REG) };
    unsafe {
        core::ptr::write_volatile(MAILBOX_REG, 0);
    }

    uart_puts(b"\n[RL] Boot\n");

    let is_slot_update = (target & 0xFFFF_0000) == SLOT_UPDATE_MAGIC;
    let raw_is_sdhc = (target & 0x100) != 0;
    let is_sdhc = if target == 0 {
        // App returning to Slot 0: restore saved SDHC flag
        unsafe { core::ptr::read_volatile(SCRATCH_SDHC_REG) != 0 }
    } else {
        unsafe {
            core::ptr::write_volatile(SCRATCH_SDHC_REG, if raw_is_sdhc { 1 } else { 0 });
        }
        raw_is_sdhc
    };
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

        let boot_mode = load_slot_from_sd(is_sdhc, slot_num);
        if boot_mode != 0 {
            if is_slot_update {
                unsafe {
                    core::ptr::write_volatile(MAILBOX_REG, SLOT_UPDATED_BOOT_MAGIC);
                }
            }
            // cpu_soft_rst only resets CPU-internal state (PC, pipeline
            // registers) -- it never clears D-RAM (soc_ram.veryl's memory
            // array has no reset path at all, only synthesis-time INIT
            // values loaded by a full bitstream reconfiguration). RISC-V
            // programs zero their own .bss in their own start.s regardless
            // of what's left over, but the Hack 16-bit firmware toolchain
            // has no equivalent step and implicitly assumes RAM starts
            // zeroed. Clear D-RAM here so every newly-launched slot gets a
            // clean start, regardless of ISA or how many soft-resets
            // preceded it. Leave the reserved scratch/mailbox words
            // (0x2000_1FF8-0x2000_1FFF) untouched -- they carry state
            // across this very reset.
            unsafe {
                let mut p = map::DRAM_BASE as *mut u32;
                let end = map::LOADER_WORDS as *mut u32;
                while p < end {
                    core::ptr::write_volatile(p, 0);
                    p = p.add(1);
                }
            }
            // Trigger CPU Soft Reset to launch newly loaded slot at 0x0000_0000
            unsafe {
                core::ptr::write_volatile(GPIO_BOOT_MODE_REG, boot_mode);
                core::ptr::write_volatile(GPIO_RESET_REG, RESET_MAGIC);
                loop {
                    core::arch::asm!("nop"); // cov:exclude(the soft reset stops the CPU first)
                }
            }
        }
        uart_puts(b"[RL] Load failed, returning to Boot Manager\n");
    }

    // Fallback: trigger CPU soft reset (into the RV32 Boot Manager)
    unsafe {
        core::ptr::write_volatile(GPIO_BOOT_MODE_REG, BOOT_MODE_RV32);
        core::ptr::write_volatile(GPIO_RESET_REG, RESET_MAGIC);
        loop {
            core::arch::asm!("nop"); // cov:exclude(the soft reset stops the CPU first)
        }
    }
}

#[panic_handler]
fn panic(_info: &PanicInfo) -> ! {
    loop {}
}
