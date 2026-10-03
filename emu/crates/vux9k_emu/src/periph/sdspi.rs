// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! SD card SPI master, soc/sdcard_spi.veryl, with the card behind it (`SdCard`).
//!
//! Registers (addr[3:0]): 0x0 write = start exchanging a byte, read = the receive
//! shift register; 0x4 CS_n (bit 0, resets to 1 = deselected); 0x8 busy; 0xC the SCLK
//! half period `d` in clocks (bits 7:0, resets to HALF_PERIOD, writes below MIN_HALF_PERIOD
//! set MIN_HALF_PERIOD). Writes while busy are ignored (all registers). Only a power-on
//! reset resets them, not a CPU soft reset.
//!
//! A transfer written at the end of cycle `w` keeps busy set in cycles w+1..=w+16d:
//! 16 SCLK half periods (368 clocks at reset). MISO bit i (MSB first) is shifted in at
//! the end of cycle w + d (2i + 1), a rising SCLK edge. Reads return the state during
//! the cycle the address is presented (data_out is registered).

use crate::sdcard::SdCard;

/// soc_pkg::SD_CLK_DIV_HALF: ceil(18 MHz / (2 * 400 kHz)), the reset half period.
pub const HALF_PERIOD: u64 = 23;
/// soc_pkg::SD_CLK_DIV_MIN: the shortest half period (3 MHz).
pub const MIN_HALF_PERIOD: u64 = 3;
/// soc_pkg::CLK_HZ.
const CLK_HZ: u64 = 18_000_000;

/// Busy cycles of one transfer at half period `d`.
pub const fn transfer(d: u64) -> u64 {
    16 * d
}

#[derive(Clone, Debug)]
pub struct SdSpi {
    cs_n: bool,
    /// SCLK half period in clocks (register 0xC).
    div: u64,
    /// The last transfer: (write edge, half period, shift register before it, MISO byte).
    xfer: Option<(u64, u64, u8, u8)>,
    /// None: no card in the socket (MISO pulled high).
    pub card: Option<SdCard>,
    /// Bytes exchanged: (write edge, MOSI, MISO, CS selected).
    pub log: Vec<(u64, u8, u8, bool)>,
    pub log_enabled: bool,
}

impl Default for SdSpi {
    fn default() -> Self {
        SdSpi {
            cs_n: true,
            div: HALF_PERIOD,
            xfer: None,
            card: None,
            log: Vec::new(),
            log_enabled: false,
        }
    }
}

impl SdSpi {
    pub fn busy(&self, c: u64) -> bool {
        self.xfer
            .is_some_and(|(w, d, _, _)| c > w && c <= w + transfer(d))
    }

    /// shift_rx during cycle `c`.
    fn shift_rx(&self, c: u64) -> u8 {
        let Some((w, d, prev, miso)) = self.xfer else {
            return 0xFF;
        };
        // Rising edges at the end of cycles w + d (2i + 1) before cycle c
        let elapsed = c.saturating_sub(w + 1);
        let n = if elapsed < d {
            0
        } else {
            ((elapsed - d) / (2 * d) + 1).min(8) as u32
        };
        (((prev as u32) << n | (miso as u32) >> (8 - n)) & 0xFF) as u8
    }

    pub fn cs_selected(&self) -> bool {
        !self.cs_n
    }

    pub fn read(&self, addr: u32, c: u64) -> u32 {
        match addr & 0xF {
            0x0 => self.shift_rx(c) as u32,
            0x4 => self.cs_n as u32,
            0x8 => self.busy(c) as u32,
            0xC => self.div as u32,
            _ => 0,
        }
    }

    /// Write at the end of cycle `c`.
    pub fn write(&mut self, addr: u32, v: u32, c: u64) {
        if self.busy(c) {
            return;
        }
        match addr & 0xF {
            0x0 => {
                let mosi = v as u8;
                let selected = !self.cs_n;
                let sclk_hz = CLK_HZ / (2 * self.div);
                let miso = self.card.as_mut().map_or(0xFF, |card| {
                    card.set_sclk_hz(sclk_hz);
                    card.exchange(mosi, selected)
                });
                if self.log_enabled {
                    self.log.push((c, mosi, miso, selected));
                }
                self.xfer = Some((c, self.div, self.shift_rx(c), miso));
            }
            0x4 => {
                let cs_n = v & 1 != 0;
                if cs_n != self.cs_n {
                    if let Some(card) = self.card.as_mut() {
                        card.set_cs(!cs_n);
                    }
                }
                self.cs_n = cs_n;
            }
            0xC => self.div = ((v & 0xFF) as u64).max(MIN_HALF_PERIOD),
            _ => {}
        }
    }
}
