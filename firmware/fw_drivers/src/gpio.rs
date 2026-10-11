// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

// Bare-metal GPIO Driver for Tang Nano 9K VUX9K SoC
use core::ptr::{read_volatile, write_volatile};
use fw_common::map;

const GPIO_LED_REG: *mut u32 = map::GPIO_LED as *mut u32;
const GPIO_BTN_REG: *const u32 = map::GPIO_BUTTON as *const u32;

pub struct Gpio;

impl Gpio {
    /// Set the 6 on-board LEDs (1 = ON, 0 = OFF)
    #[inline(always)]
    pub fn set_leds(pattern: u8) {
        unsafe {
            write_volatile(GPIO_LED_REG, (pattern & 0x3F) as u32);
        }
    }

    /// Read the user push button (1 = Pressed, 0 = Released)
    #[inline(always)]
    pub fn get_button() -> bool {
        unsafe { (read_volatile(GPIO_BTN_REG) & 0x1) != 0 }
    }

    /// Toggle specific LED bit (0-5) for activity indicator
    #[inline(always)]
    pub fn toggle_led(bit: u8) {
        unsafe {
            let current = (read_volatile(GPIO_LED_REG) & 0x3F) as u8;
            write_volatile(GPIO_LED_REG, (current ^ (1 << bit)) as u32);
        }
    }
}
