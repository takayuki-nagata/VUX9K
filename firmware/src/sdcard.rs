// MMIO SD Card SPI Driver

const SD_BASE: usize = 0x4000_2000;
const SD_DATA: *mut u32 = SD_BASE as *mut u32;
const SD_CS: *mut u32 = (SD_BASE + 0x4) as *mut u32;
const SD_STATUS: *const u32 = (SD_BASE + 0x8) as *const u32;

pub struct SdCard;

impl SdCard {
    pub fn set_cs(active: bool) {
        unsafe {
            core::ptr::write_volatile(SD_CS, if active { 0 } else { 1 });
        }
    }

    pub fn transfer(byte: u8) -> u8 {
        unsafe {
            // Wait while busy (bit 0)
            while (core::ptr::read_volatile(SD_STATUS) & 0x1) != 0 {}
            core::ptr::write_volatile(SD_DATA, byte as u32);
            // Wait until transfer completes
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

        // Wait for R1 response (MSB is 0)
        for _ in 0..10 {
            let res = Self::transfer(0xFF);
            if (res & 0x80) == 0 {
                return res;
            }
        }
        0xFF
    }
}
