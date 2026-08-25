// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

// MMIO SD Card SPI Driver (Universal SDSC / SDHC / SDXC Driver)

const SD_BASE: usize = 0x4000_2000;
const SD_DATA: *mut u32 = SD_BASE as *mut u32;
const SD_CS: *mut u32 = (SD_BASE + 0x4) as *mut u32;
const SD_STATUS: *const u32 = (SD_BASE + 0x8) as *const u32;

static mut IS_SDHC: bool = false;

pub struct SdCard;

impl SdCard {
    pub fn set_cs(active: bool) {
        unsafe {
            core::ptr::write_volatile(SD_CS, if active { 0 } else { 1 });
        }
    }

    pub fn transfer(byte: u8) -> u8 {
        unsafe {
            while (core::ptr::read_volatile(SD_STATUS) & 0x1) != 0 {}
            core::ptr::write_volatile(SD_DATA, byte as u32);
            while (core::ptr::read_volatile(SD_STATUS) & 0x1) != 0 {}
            (core::ptr::read_volatile(SD_DATA) & 0xFF) as u8
        }
    }

    pub fn send_cmd(cmd: u8, arg: u32, crc: u8) -> u8 {
        Self::set_cs(true);
        Self::transfer(0x40 | cmd);
        Self::transfer((arg >> 24) as u8);
        Self::transfer((arg >> 16) as u8);
        Self::transfer((arg >> 8) as u8);
        Self::transfer(arg as u8);
        Self::transfer(crc);

        for _ in 0..16 {
            let res = Self::transfer(0xFF);
            if (res & 0x80) == 0 {
                return res;
            }
        }
        0xFF
    }

    pub fn init() -> bool {
        Self::set_cs(false);
        // Send >= 74 dummy clock cycles with CS high
        for _ in 0..12 {
            Self::transfer(0xFF);
        }

        // CMD0 (GO_IDLE_STATE)
        let r1 = Self::send_cmd(0, 0, 0x95);
        Self::set_cs(false);
        Self::transfer(0xFF);
        if r1 != 0x01 {
            return false;
        }

        // CMD8 (SEND_IF_COND)
        let r1 = Self::send_cmd(8, 0x000001AA, 0x87);
        let mut is_v2 = false;
        if r1 == 0x01 {
            let _ = Self::transfer(0xFF);
            let _ = Self::transfer(0xFF);
            let _ = Self::transfer(0xFF);
            let _ = Self::transfer(0xFF);
            is_v2 = true;
        }
        Self::set_cs(false);
        Self::transfer(0xFF);

        // ACMD41 loop
        let arg = if is_v2 { 0x4000_0000 } else { 0 };
        let mut ready = false;
        for _ in 0..1000 {
            let _ = Self::send_cmd(55, 0, 0x65);
            Self::set_cs(false);
            Self::transfer(0xFF);

            let res = Self::send_cmd(41, arg, 0x77);
            Self::set_cs(false);
            Self::transfer(0xFF);
            if res == 0x00 {
                ready = true;
                break;
            }
        }
        if !ready {
            return false;
        }

        // Check CCS via CMD58 (READ_OCR)
        let mut sdhc = false;
        if is_v2 {
            let r1 = Self::send_cmd(58, 0, 0xFD);
            if r1 == 0x00 {
                let ocr0 = Self::transfer(0xFF);
                let _ = Self::transfer(0xFF);
                let _ = Self::transfer(0xFF);
                let _ = Self::transfer(0xFF);
                sdhc = (ocr0 & 0x40) != 0;
            }
            Self::set_cs(false);
            Self::transfer(0xFF);
        }

        unsafe {
            IS_SDHC = sdhc;
        }

        // Set block size to 512 bytes (CMD16)
        let _ = Self::send_cmd(16, 512, 0xFF);
        Self::set_cs(false);
        Self::transfer(0xFF);

        true
    }

    pub fn read_block(sector_num: u32, buf: &mut [u8; 512]) -> bool {
        let addr = unsafe { if IS_SDHC { sector_num } else { sector_num << 9 } };

        // CMD17 (READ_SINGLE_BLOCK)
        let r1 = Self::send_cmd(17, addr, 0xFF);
        if r1 != 0x00 {
            Self::set_cs(false);
            Self::transfer(0xFF);
            return false;
        }

        // Wait for Start Block Token (0xFE)
        let mut ready = false;
        for _ in 0..10000 {
            let token = Self::transfer(0xFF);
            if token == 0xFE {
                ready = true;
                break;
            }
        }
        if !ready {
            Self::set_cs(false);
            Self::transfer(0xFF);
            return false;
        }

        // Read 512 bytes
        for i in 0..512 {
            buf[i] = Self::transfer(0xFF);
        }

        // 2 bytes CRC16
        Self::transfer(0xFF);
        Self::transfer(0xFF);

        Self::set_cs(false);
        Self::transfer(0xFF);
        true
    }

    pub fn write_block(sector_num: u32, buf: &[u8; 512]) -> bool {
        let addr = unsafe { if IS_SDHC { sector_num } else { sector_num << 9 } };

        // CMD24 (WRITE_SINGLE_BLOCK)
        let r1 = Self::send_cmd(24, addr, 0xFF);
        if r1 != 0x00 {
            Self::set_cs(false);
            Self::transfer(0xFF);
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
            Self::set_cs(false);
            Self::transfer(0xFF);
            return false;
        }

        // Wait while busy
        for _ in 0..100_000 {
            if Self::transfer(0xFF) != 0x00 {
                Self::set_cs(false);
                Self::transfer(0xFF);
                return true;
            }
        }

        Self::set_cs(false);
        Self::transfer(0xFF);
        false
    }
}
