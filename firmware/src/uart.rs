// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

// MMIO UART Driver with pure RV32I software conversions

const UART_BASE: usize = 0x4000_0000;
const UART_DATA: *mut u8 = UART_BASE as *mut u8;
const UART_STATUS: *const u32 = (UART_BASE + 0x4) as *const u32;

pub struct Uart;

fn divmod10(mut n: u32) -> (u32, u8) {
    let mut q = 0;
    while n >= 10 {
        n -= 10;
        q += 1;
    }
    (q, n as u8)
}

impl Uart {
    pub fn write_byte(c: u8) {
        unsafe {
            // Wait while TX FIFO is full (bit 1 of status)
            while (core::ptr::read_volatile(UART_STATUS) & 0x2) != 0 {}
            core::ptr::write_volatile(UART_DATA, c);
        }
    }

    pub fn print_str(s: &str) {
        for b in s.bytes() {
            Self::write_byte(b);
        }
    }

    pub fn print_hex(val: u32) {
        const HEX_CHARS: &[u8; 16] = b"0123456789ABCDEF";
        for shift in (0..8).rev() {
            let nibble = ((val >> (shift * 4)) & 0xF) as usize;
            Self::write_byte(HEX_CHARS[nibble]);
        }
    }

    pub fn print_dec(mut val: u32) {
        if val == 0 {
            Self::write_byte(b'0');
            return;
        }
        let mut buf = [0u8; 10];
        let mut i = 0;
        while val > 0 {
            let (q, rem) = divmod10(val);
            buf[i] = b'0' + rem;
            val = q;
            i += 1;
        }
        while i > 0 {
            i -= 1;
            Self::write_byte(buf[i]);
        }
    }
}
