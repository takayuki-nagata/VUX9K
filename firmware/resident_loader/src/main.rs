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

use fw_common::crc32_update;
use fw_common::header::{
    slot_sector, HEADER_LEN, HEADER_VERSION, OFF_CRC32, OFF_HEADER_VERSION, OFF_LOAD_ADDR,
    OFF_MAGIC, OFF_MODE, OFF_SIZE, SECTOR, VUX_MAGIC,
};
use fw_common::mailbox::{self, SLOT_UPDATED_BOOT_MAGIC, SLOT_UPDATE_MAGIC};
use fw_common::map;
use fw_common::update::MAX_IMAGE;

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

fn le32(b: &[u8], at: usize) -> u32 {
    u32::from_le_bytes([b[at], b[at + 1], b[at + 2], b[at + 3]])
}

/// One pass over a slot: check its header, stream its payload through CRC32, and with
/// `write`, store it into I-RAM at the header's load address. Returns the value for
/// GPIO_BOOT_MODE_REG (valid bit and the header's ISA, 1 = RV32 / 0 = Hack), or the
/// error number 1-5 (E1 header sector unreadable, E2 bad header, E3 bad size, E4
/// later sector unreadable, E5 CRC mismatch).
fn pass(is_sdhc: bool, slot_id: u32, write: bool) -> Result<u32, u8> {
    let mut sector = slot_sector(slot_id);
    if !spi_start_block(is_sdhc, sector) {
        return Err(1);
    }
    // Every byte is read below; zero-filling it first would link in a 152-byte memset
    let mut raw = core::mem::MaybeUninit::<[u8; HEADER_LEN]>::uninit();
    let p = raw.as_mut_ptr() as *mut u8;
    for n in 0..HEADER_LEN {
        unsafe { p.add(n).write(spi_transfer(0xFF)) };
    }
    let header = unsafe { raw.assume_init() };
    // Unlike SlotHeader::parse, the valid flag isn't checked (no room): vux_tool always sets it
    let size = le32(&header, OFF_SIZE) as usize;
    let err = if le32(&header, OFF_MAGIC) != VUX_MAGIC
        || (le32(&header, OFF_HEADER_VERSION) & 0xFFFF) != HEADER_VERSION as u32
    {
        2
    } else if size == 0 || size > MAX_IMAGE as usize {
        3
    } else {
        0
    };
    if err != 0 {
        skip_bytes(SECTOR - HEADER_LEN);
        spi_end_block();
        return Err(err);
    }

    let mut dest = le32(&header, OFF_LOAD_ADDR) as *mut u32;
    let mut crc = 0xFFFF_FFFF;
    let mut remaining = size;
    let mut offset = HEADER_LEN; // bytes of this sector already read
    loop {
        // The sector's share of the payload, read in whole words (the slot is padded)
        let chunk = if remaining < SECTOR - offset {
            remaining
        } else {
            SECTOR - offset
        };
        let mut word = 0u32;
        for n in 0..(chunk + 3) & !3 {
            let b = spi_transfer(0xFF);
            if n < chunk {
                crc = crc32_update(&[b], crc);
            }
            word = (word >> 8) | ((b as u32) << 24);
            if n & 3 == 3 && write {
                unsafe {
                    core::ptr::write_volatile(dest, word);
                    dest = dest.add(1);
                }
            }
        }
        skip_bytes(SECTOR - offset - ((chunk + 3) & !3));
        spi_end_block();
        remaining -= chunk;
        if remaining == 0 {
            break;
        }
        sector += 1;
        offset = 0;
        if !spi_start_block(is_sdhc, sector) {
            return Err(4);
        }
    }
    if crc ^ 0xFFFF_FFFF != le32(&header, OFF_CRC32) {
        return Err(5);
    }
    Ok(BOOT_MODE_VALID | (le32(&header, OFF_MODE) & 1))
}

fn report(err: u8) {
    uart_puts(b"[RL] E");
    uart_putc(b'0' + err);
    uart_putc(b'\n');
}

#[no_mangle]
pub extern "C" fn loader_main() -> ! {
    let target = unsafe { core::ptr::read_volatile(MAILBOX_REG) };
    unsafe {
        core::ptr::write_volatile(MAILBOX_REG, 0);
    }

    uart_puts(b"\n[RL] Boot\n");

    // Only a marked request loads anything; any other value (0 included) restarts
    // the Boot Manager below
    let kind = target & mailbox::KIND_MASK;
    let is_slot_update = kind == SLOT_UPDATE_MAGIC;
    let is_sdhc = (target & mailbox::SDHC) != 0;
    let slot_num = if is_slot_update { 0 } else { target & 0xFF };

    if (is_slot_update || kind == mailbox::LAUNCH_MAGIC) && slot_num <= 9 {
        uart_puts(b"\n[RL] Slot ");
        uart_putc(b'0' + slot_num as u8);
        uart_puts(b"\n");

        // Prepare SPI bus
        spi_deselect();
        for _ in 0..8 {
            spi_transfer(0xFF);
        }

        // First read the whole slot without touching I-RAM, which still holds the Boot
        // Manager: any error returns to it intact. Then load it for real.
        match pass(is_sdhc, slot_num, false) {
            Err(e) => report(e),
            Ok(_) => {
                let Ok(boot_mode) = pass(is_sdhc, slot_num, true) else {
                    // Read once, failed the second time: lower I-RAM is half
                    // overwritten, so there is no Boot Manager to return to. Stop.
                    uart_puts(b"[RL] E6: reload the bitstream\n");
                    #[allow(clippy::empty_loop)]
                    loop {}
                };
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
                // preceded it. Leave the loaders' words (0x2000_1FF8-0x2000_1FFF)
                // untouched: the mailbox carries state across this very reset.
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
