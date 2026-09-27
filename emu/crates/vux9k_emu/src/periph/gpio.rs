// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! GPIO, soc/gpio_controller.veryl.
//!
//! Registers (addr[3:0]):
//! - 0x0 LEDs (6 bits; the pins are active-low, `led_pins`)
//! - 0x4 button S2 (1 = pressed; the active-low pin goes through a 2-flop synchronizer)
//! - 0x8 ISA for the next soft reset: bit 8 valid, bit 0 1 = RV32 / 0 = Hack; valid
//!   clears when the soft-reset pulse ends, so it applies to one soft reset only
//! - 0xC soft reset: writing 0x5A5A_A55A or 0x0000_A55A pulses cpu_soft_rst for 15
//!   cycles; reads 1 while the pulse lasts
//!
//! Reads return the state during the cycle the address is presented (data_out is
//! registered at its end); writes take effect at the end of the write cycle.

pub const SOFT_RESET_KEYS: [u32; 2] = [0x5A5A_A55A, 0x0000_A55A];
/// Cycles cpu_soft_rst stays high.
pub const SOFT_RESET_PULSE: u64 = 15;

#[derive(Clone, Debug, Default)]
pub struct Gpio {
    led: u8,
    /// (cycle from which the button pin is at this level, pressed)
    btn: [(u64, bool); 2],
    mode_valid: bool,
    mode_rv32: bool,
    /// First cycle of the current soft-reset pulse.
    pulse_from: Option<u64>,
    /// A pulse the CPU hasn't reacted to yet (see `take_soft_reset`).
    pending: Option<SoftReset>,
}

/// A soft reset requested through register 0xC.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct SoftReset {
    /// First cycle with cpu_soft_rst low again: the CPU fetches from address 0 here.
    pub release: u64,
    /// ISA given in register 0x8 (Some(true) = RV32), or None to auto-detect.
    pub mode: Option<bool>,
}

impl Gpio {
    /// LED register value (bit set = LED lit).
    pub fn leds(&self) -> u8 {
        self.led
    }

    /// The six LED pins (active-low, like soc_top's `led`).
    pub fn led_pins(&self) -> u8 {
        !self.led & 0x3F
    }

    /// Press (true) or release S2 from cycle `c` on.
    pub fn set_button(&mut self, c: u64, pressed: bool) {
        let cur = self.btn[1];
        if cur.1 != pressed {
            self.btn = [cur, (c, pressed)];
        }
    }

    fn pin_pressed(&self, c: u64) -> bool {
        if c >= self.btn[1].0 {
            self.btn[1].1
        } else {
            self.btn[0].1
        }
    }

    /// Button as the CPU reads it in cycle `c` (btn_sync resets to "released").
    fn pressed(&self, c: u64) -> bool {
        c >= 2 && self.pin_pressed(c - 2)
    }

    fn soft_reset_active(&self, c: u64) -> bool {
        self.pulse_from
            .is_some_and(|f| c >= f && c < f + SOFT_RESET_PULSE)
    }

    pub fn read(&self, addr: u32, c: u64) -> u32 {
        match addr & 0xF {
            0x0 => self.led as u32,
            0x4 => self.pressed(c) as u32,
            0x8 => ((self.mode_valid as u32) << 8) | self.mode_rv32 as u32,
            0xC => self.soft_reset_active(c) as u32,
            _ => 0,
        }
    }

    /// Write at the end of cycle `c`.
    pub fn write(&mut self, addr: u32, v: u32, c: u64) {
        match addr & 0xF {
            0x0 => self.led = (v & 0x3F) as u8,
            0x8 => {
                self.mode_valid = v & 0x100 != 0;
                self.mode_rv32 = v & 1 != 0;
            }
            0xC if SOFT_RESET_KEYS.contains(&v) => {
                let from = c + 1;
                self.pulse_from = Some(from);
                let mode = self.mode_valid.then_some(self.mode_rv32);
                self.pending = Some(SoftReset {
                    release: from + SOFT_RESET_PULSE,
                    mode,
                });
                // The pulse consumes the boot mode (valid clears as the pulse ends)
                self.mode_valid = false;
            }
            _ => {}
        }
    }

    /// The soft reset requested by the last write, if the CPU hasn't taken it yet.
    pub fn take_soft_reset(&mut self) -> Option<SoftReset> {
        self.pending.take()
    }
}
