// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! Text output without core::fmt (which would not fit): a byte sink with hex and
//! decimal printing. The Boot Manager's UART implements it.

const HEX: &[u8; 16] = b"0123456789ABCDEF";

pub trait Sink {
    fn put(&mut self, b: u8);

    fn put_str(&mut self, s: &str) {
        for b in s.bytes() {
            self.put(b);
        }
    }

    /// Eight upper-case hex digits.
    fn put_hex(&mut self, val: u32) {
        for shift in (0..8).rev() {
            self.put(HEX[((val >> (shift * 4)) & 0xF) as usize]);
        }
    }

    /// Two upper-case hex digits.
    fn put_hex_byte(&mut self, val: u8) {
        self.put(HEX[(val >> 4) as usize]);
        self.put(HEX[(val & 0xF) as usize]);
    }

    /// Decimal, no leading zeros (by repeated subtraction: RV32I has no divider).
    fn put_dec(&mut self, mut val: u32) {
        const POWERS: [u32; 10] = [
            1_000_000_000,
            100_000_000,
            10_000_000,
            1_000_000,
            100_000,
            10_000,
            1_000,
            100,
            10,
            1,
        ];
        let mut started = false;
        for &p in POWERS.iter() {
            let mut digit = 0u8;
            while val >= p {
                val -= p;
                digit += 1;
            }
            if digit > 0 || started || p == 1 {
                started = true;
                self.put(b'0' + digit);
            }
        }
    }
}
