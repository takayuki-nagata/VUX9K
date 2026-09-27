// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! SD card in SPI mode, at byte level: for every byte the SPI master exchanges, the
//! card is given the MOSI byte and the CS level and returns the MISO byte.
//!
//! It answers like sim/integration/sdcard_model.py (the model the RTL tests use):
//! - CMD0 FF 01; CMD8 FF 01 00 00 01 AA; CMD55 FF 01 (00 once ready); ACMD41 FF 00;
//!   CMD58 FF 00 + OCR (C0|80) FF 80 00; CMD16 FF 00; other commands no answer.
//! - CMD17 FF 00 FF FE, 512 data bytes, CRC 12 34.
//! - CMD24 FF 00, then data token FE, 512 bytes and 2 CRC bytes; data response 05 and a
//!   busy byte 3F. After that the card listens for a new command only once CS has
//!   gone high (the Python model loses byte alignment there until CS rises).
//! - Answers are sent whatever CS does; a partial command, a wait for a data token or
//!   a partial data block is dropped when CS goes high.
//!
//! Addressing (CMD17/CMD24): by default forgiving, `arg < 0x10000 ? arg : arg >> 9`.
//! `strict` uses exactly the card type's rule (SDHC block address, SDSC byte address)
//! and records violations: an SDSC byte address not a multiple of 512, and fewer than
//! 74 SCLK cycles with CS high before the first command.
//!
//! The data is a raw sector image (sector n at byte 512 n); reads past its end return
//! zeros and writes past it grow it.

use std::collections::VecDeque;

pub const SECTOR: usize = 512;
/// SD spec: at least 74 clocks with CS high before the first command.
pub const POWER_UP_CLOCKS: u64 = 74;

/// Faults to inject, for testing firmware error paths.
#[derive(Clone, Debug, Default)]
pub struct Faults {
    /// Commands the card never answers (e.g. 0 = no card behind a working socket).
    pub mute_cmds: Vec<u8>,
    /// ACMD41 keeps answering 01 (initialization never finishes).
    pub never_ready: bool,
    /// CMD17 answers with the data error token 08 instead of a block.
    pub read_error: bool,
    /// CMD24's data response is 0B (CRC error): the block is not written.
    pub write_reject: bool,
}

#[derive(Clone, Debug)]
enum State {
    Idle,
    Cmd {
        buf: [u8; 6],
        n: usize,
    },
    WaitToken {
        lba: u32,
    },
    Data {
        lba: u32,
        buf: Vec<u8>,
    },
    /// After a write: ignore everything until CS goes high.
    Resync,
}

#[derive(Clone, Debug)]
pub struct SdCard {
    pub sdhc: bool,
    pub strict: bool,
    pub faults: Faults,
    image: Vec<u8>,
    state: State,
    resp: VecDeque<u8>,
    ready: bool,
    first_cmd_seen: bool,
    idle_clocks: u64,
    /// (cmd, arg) of every command received.
    pub commands: Vec<(u8, u32)>,
    /// Protocol problems (strict mode).
    pub violations: Vec<String>,
}

impl SdCard {
    /// An SDHC card holding `image`.
    pub fn new(image: Vec<u8>) -> Self {
        SdCard {
            sdhc: true,
            strict: false,
            faults: Faults::default(),
            image,
            state: State::Idle,
            resp: VecDeque::new(),
            ready: false,
            first_cmd_seen: false,
            idle_clocks: 0,
            commands: Vec::new(),
            violations: Vec::new(),
        }
    }

    pub fn image(&self) -> &[u8] {
        &self.image
    }

    pub fn sector(&self, lba: u32) -> [u8; SECTOR] {
        let mut s = [0; SECTOR];
        let off = lba as usize * SECTOR;
        if off < self.image.len() {
            let end = (off + SECTOR).min(self.image.len());
            s[..end - off].copy_from_slice(&self.image[off..end]);
        }
        s
    }

    pub fn set_sector(&mut self, lba: u32, data: &[u8]) {
        let off = lba as usize * SECTOR;
        if self.image.len() < off + SECTOR {
            self.image.resize(off + SECTOR, 0);
        }
        let n = data.len().min(SECTOR);
        self.image[off..off + n].copy_from_slice(&data[..n]);
        self.image[off + n..off + SECTOR].fill(0);
    }

    /// SCLK cycles seen with CS high before the first command.
    pub fn idle_clocks(&self) -> u64 {
        self.idle_clocks
    }

    /// CS changed (true = selected).
    pub fn set_cs(&mut self, selected: bool) {
        if !selected {
            self.state = State::Idle;
        }
    }

    /// One byte exchanged with CS at `selected` for the whole byte: returns MISO.
    pub fn exchange(&mut self, mosi: u8, selected: bool) -> u8 {
        if !selected && !self.first_cmd_seen {
            self.idle_clocks += 8;
        }
        if let Some(b) = self.resp.pop_front() {
            return b;
        }
        if !selected {
            return 0xFF;
        }
        match &mut self.state {
            State::Idle => {
                if mosi & 0xC0 == 0x40 {
                    let mut buf = [0; 6];
                    buf[0] = mosi;
                    self.state = State::Cmd { buf, n: 1 };
                }
            }
            State::Cmd { buf, n } => {
                buf[*n] = mosi;
                *n += 1;
                if *n == 6 {
                    let cmd = buf[0] & 0x3F;
                    let arg = u32::from_be_bytes([buf[1], buf[2], buf[3], buf[4]]);
                    self.state = State::Idle;
                    self.command(cmd, arg);
                }
            }
            State::WaitToken { lba } => {
                if mosi == 0xFE {
                    self.state = State::Data {
                        lba: *lba,
                        buf: Vec::with_capacity(SECTOR + 2),
                    };
                }
            }
            State::Data { lba, buf } => {
                buf.push(mosi);
                if buf.len() == SECTOR + 2 {
                    let lba = *lba;
                    let data = std::mem::take(buf);
                    self.state = State::Resync;
                    if self.faults.write_reject {
                        self.resp.push_back(0x0B);
                    } else {
                        self.set_sector(lba, &data[..SECTOR]);
                        self.resp.extend([0x05, 0x3F]);
                    }
                }
            }
            State::Resync => {}
        }
        0xFF
    }

    fn lba(&mut self, cmd: u8, arg: u32) -> u32 {
        if !self.strict {
            return if arg < 0x1_0000 { arg } else { arg >> 9 };
        }
        if self.sdhc {
            return arg;
        }
        if !arg.is_multiple_of(SECTOR as u32) {
            self.violations.push(format!(
                "CMD{cmd}: SDSC byte address 0x{arg:X} is not sector-aligned (block address sent?)"
            ));
        }
        arg >> 9
    }

    fn command(&mut self, cmd: u8, arg: u32) {
        self.commands.push((cmd, arg));
        if !self.first_cmd_seen {
            self.first_cmd_seen = true;
            if self.strict && self.idle_clocks < POWER_UP_CLOCKS {
                self.violations.push(format!(
                    "power-up: only {} SCLK cycles with CS high before the first command \
                     (CMD{cmd}), SD spec requires >= {POWER_UP_CLOCKS}",
                    self.idle_clocks
                ));
            }
        }
        if self.faults.mute_cmds.contains(&cmd) {
            return;
        }
        let r = &mut self.resp;
        match cmd {
            0 => {
                self.ready = false;
                r.extend([0xFF, 0x01]);
            }
            8 => r.extend([0xFF, 0x01, 0x00, 0x00, 0x01, 0xAA]),
            55 => r.extend([0xFF, if self.ready { 0x00 } else { 0x01 }]),
            41 => {
                self.ready = !self.faults.never_ready;
                r.extend([0xFF, if self.ready { 0x00 } else { 0x01 }]);
            }
            58 => r.extend([
                0xFF,
                0x00,
                if self.sdhc { 0xC0 } else { 0x80 },
                0xFF,
                0x80,
                0x00,
            ]),
            16 => r.extend([0xFF, 0x00]),
            17 => {
                let lba = self.lba(cmd, arg);
                let r = &mut self.resp;
                if self.faults.read_error {
                    r.extend([0xFF, 0x00, 0xFF, 0x08]);
                } else {
                    r.extend([0xFF, 0x00, 0xFF, 0xFE]);
                    let data = self.sector(lba);
                    self.resp.extend(data);
                    self.resp.extend([0x12, 0x34]);
                }
            }
            24 => {
                let lba = self.lba(cmd, arg);
                self.resp.extend([0xFF, 0x00]);
                self.state = State::WaitToken { lba };
            }
            _ => {}
        }
    }
}
