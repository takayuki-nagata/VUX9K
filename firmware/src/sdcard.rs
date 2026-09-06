// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

use crate::timer::Timer;
use crate::uart::Uart;

const SD_BASE: usize = 0x4000_2000;
const SD_DATA: *mut u32 = SD_BASE as *mut u32;
const SD_CS: *mut u32 = (SD_BASE + 0x4) as *mut u32;
const SD_STATUS: *const u32 = (SD_BASE + 0x8) as *const u32;

static mut IS_SDHC: bool = false;
static mut IS_INITIALIZED: bool = false;

pub struct SdCard;

impl SdCard {
    pub fn set_cs(active: bool) {
        unsafe {
            core::ptr::write_volatile(SD_CS, if active { 0 } else { 1 });
        }
    }

    pub fn is_initialized() -> bool {
        unsafe { IS_INITIALIZED }
    }

    pub fn ensure_init() -> bool {
        if unsafe { IS_INITIALIZED } {
            true
        } else {
            Self::init()
        }
    }

    pub fn force_init() -> bool {
        unsafe {
            IS_INITIALIZED = false;
        }
        Self::init()
    }

    pub fn transfer(byte: u8) -> u8 {
        unsafe {
            while (core::ptr::read_volatile(SD_STATUS) & 0x1) != 0 {}
            core::ptr::write_volatile(SD_DATA, byte as u32);
            while (core::ptr::read_volatile(SD_STATUS) & 0x1) != 0 {}
            (core::ptr::read_volatile(SD_DATA) & 0xFF) as u8
        }
    }

    pub fn deselect() {
        Self::set_cs(false);
        Self::transfer(0xFF);
    }

    pub fn send_cmd(cmd: u8, arg: u32, crc: u8) -> u8 {
        Self::set_cs(true);
        Timer::delay_us(10);

        Self::transfer(0x40 | cmd);
        Self::transfer((arg >> 24) as u8);
        Self::transfer((arg >> 16) as u8);
        Self::transfer((arg >> 8) as u8);
        Self::transfer(arg as u8);
        Self::transfer(crc);

        if cmd == 12 {
            Self::transfer(0xFF);
        }

        // Wait for R1 response (while card is busy, it outputs 0xFF)
        for _ in 0..200 {
            let res = Self::transfer(0xFF);
            if res != 0xFF {
                return res;
            }
        }
        0xFF
    }

    pub fn init() -> bool {
        unsafe {
            IS_INITIALIZED = false;
        }
        Self::deselect();
        for _ in 0..16 {
            Self::transfer(0xFF);
        }
        Timer::delay_ms(10);

        // 2. CMD0 (GO_IDLE_STATE)
        let mut r1 = 0xFF;
        for _ in 0..20 {
            r1 = Self::send_cmd(0, 0, 0x95);
            Self::deselect();
            if r1 == 0x01 {
                break;
            }
            Timer::delay_ms(2);
        }
        if r1 != 0x01 {
            return false;
        }

        // 3. CMD8 (SEND_IF_COND)
        let r8 = Self::send_cmd(8, 0x0000_01AA, 0x87);
        let mut is_v2 = false;
        if r8 == 0x01 {
            let mut ocr = [0u8; 4];
            for b in ocr.iter_mut() {
                *b = Self::transfer(0xFF);
            }
            Self::deselect();
            if ocr[2] == 0x01 && ocr[3] == 0xAA {
                is_v2 = true;
            }
        } else {
            Self::deselect();
        }

        // 4. ACMD41 loop
        let arg = if is_v2 { 0x4000_0000 } else { 0 };
        let mut ready = false;
        for _ in 0..1000 {
            let r55 = Self::send_cmd(55, 0, 0x65);
            Self::deselect();

            let res = if r55 <= 0x01 {
                let r41 = Self::send_cmd(41, arg, 0x77);
                Self::deselect();
                r41
            } else {
                let r1 = Self::send_cmd(1, arg, 0xFF);
                Self::deselect();
                r1
            };

            if res == 0x00 {
                ready = true;
                break;
            }
            Timer::delay_ms(2);
        }
        if !ready {
            return false;
        }

        // 5. CMD58 (READ_OCR) for SDHC/SDXC check
        let mut sdhc = false;
        if is_v2 {
            let r58 = Self::send_cmd(58, 0, 0xFD);
            if r58 == 0x00 {
                let mut ocr = [0u8; 4];
                for b in ocr.iter_mut() {
                    *b = Self::transfer(0xFF);
                }
                sdhc = (ocr[0] & 0x40) != 0;
            }
            Self::deselect();
        }

        unsafe {
            IS_SDHC = sdhc;
            IS_INITIALIZED = true;
        }

        // 6. CMD16 (SET_BLOCKLEN)
        Self::send_cmd(16, 512, 0xFF);
        Self::deselect();

        true
    }

    pub fn read_block(sector_num: u32, buf: &mut [u8; 512]) -> bool {
        let addr = unsafe { if IS_SDHC { sector_num } else { sector_num << 9 } };

        let r1 = Self::send_cmd(17, addr, 0xFF);
        if r1 != 0x00 {
            Self::deselect();
            return false;
        }

        let mut ready = false;
        for _ in 0..50000 {
            let token = Self::transfer(0xFF);
            if token == 0xFE {
                ready = true;
                break;
            }
        }
        if !ready {
            Self::deselect();
            return false;
        }

        for i in 0..512 {
            buf[i] = Self::transfer(0xFF);
        }

        Self::transfer(0xFF);
        Self::transfer(0xFF);

        Self::deselect();
        true
    }

    pub fn write_block(sector_num: u32, buf: &[u8; 512]) -> bool {
        let addr = unsafe { if IS_SDHC { sector_num } else { sector_num << 9 } };

        let r1 = Self::send_cmd(24, addr, 0xFF);
        if r1 != 0x00 {
            Self::deselect();
            return false;
        }

        Self::transfer(0xFF);
        Self::transfer(0xFE);

        for i in 0..512 {
            Self::transfer(buf[i]);
        }

        Self::transfer(0xFF);
        Self::transfer(0xFF);

        let resp = Self::transfer(0xFF);
        if (resp & 0x1F) != 0x05 {
            Self::deselect();
            return false;
        }

        let mut programmed = false;
        for _ in 0..250_000 {
            if Self::transfer(0xFF) == 0xFF {
                programmed = true;
                break;
            }
        }

        Self::deselect();
        programmed
    }
}
