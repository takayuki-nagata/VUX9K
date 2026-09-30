// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

// MMIO UART Driver with standard formatting and non-blocking RX

use fw_common::fmt::Sink;
use fw_common::map;

const UART_DATA: *mut u8 = map::UART_DATA as *mut u8;
const UART_STATUS: *const u32 = map::UART_STATUS as *const u32;

pub struct Uart;

impl Uart {
    #[inline(always)]
    pub fn write_byte(c: u8) {
        unsafe {
            while (core::ptr::read_volatile(UART_STATUS) & map::UART_TX_FULL) != 0 {}
            core::ptr::write_volatile(UART_DATA, c);
        }
    }

    #[inline(always)]
    pub fn has_rx_data() -> bool {
        unsafe { (core::ptr::read_volatile(UART_STATUS) & map::UART_RX_EMPTY) == 0 }
    }

    #[inline(always)]
    pub fn read_byte() -> Option<u8> {
        if Self::has_rx_data() {
            unsafe { Some(core::ptr::read_volatile(UART_DATA)) }
        } else {
            None
        }
    }

    pub fn print_str(s: &str) {
        Uart.put_str(s);
    }

    pub fn print_hex(val: u32) {
        Uart.put_hex(val);
    }

    pub fn print_hex_byte(val: u8) {
        Uart.put_hex_byte(val);
    }

    pub fn print_dec(val: u32) {
        Uart.put_dec(val);
    }
}

impl Sink for Uart {
    fn put(&mut self, b: u8) {
        Uart::write_byte(b);
    }
}
