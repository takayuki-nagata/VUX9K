// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! SD card in SPI mode, at byte level: for every byte the SPI master exchanges, the
//! card is given the MOSI byte and the CS level and returns the MISO byte.
//!
//! It answers like sim/integration/sdcard_model.py (the model the RTL tests use):
//! - CMD0 FF 01; CMD8 FF 01 00 00 01 AA; CMD55 FF 01 (00 once ready); ACMD41 and
//!   CMD1 FF 00 (CMD41 also without CMD55);
//!   CMD58 FF 00 + OCR (C0|80) FF 80 00; CMD16 FF 00; CMD59 (CRC_ON_OFF) FF 00;
//!   CMD13 (SEND_STATUS) R2 FF 00 00; CMD12 outside a multiple-block read FF 00;
//!   other commands no answer. A command right after CMD55 is an application command
//!   (ACMD13, ACMD51; ACMD41 as CMD41).
//! - Data blocks: FF FE, the data, and its CRC16 (CCITT, as the SD spec's data CRC):
//!   CMD17 FF 00 + a 512-byte block; CMD9 (CSD) and CMD10 (CID) FF 00 + a 16-byte block;
//!   ACMD51 (SCR) FF 00 + 8 bytes; ACMD13 (SD status) R2 FF 00 00 + 64 bytes. The CSD
//!   is version 2 with the capacity of the image (`csd`); its command classes leave
//!   out switch (CMD6) and erase.
//! - CMD18 FF 00, then one block after the other, from consecutive sectors, while the
//!   card keeps reading MOSI: CMD12 stops it (FF 00 after it: a stuff byte, R1); another
//!   command stops it and is answered as usual. CS going high pauses it (MISO FF, nothing
//!   sent) without ending it: Linux's mmc_spi raises CS after each block it reads.
//! - CMD24 FF 00, then data token FE, 512 bytes and 2 CRC bytes; data response 05 and a
//!   busy byte 3F. After that the card listens for a new command only once CS has
//!   gone high (the Python model loses byte alignment there until CS rises).
//!   CMD25 FF 00, then blocks with token FC, each answered 05 3F, until the stop token
//!   FD (answered FF 00: N(BR) and one busy byte); then the card listens for commands.
//! - CRC: CMD59 with bit 0 set turns it on. A written block whose CRC16 is wrong is then
//!   refused (data response 0B), and strict mode records a command whose CRC7 is wrong.
//! - Faults (`Faults`): unanswered commands, ACMD41 never ready, CMD17/CMD18 error token
//!   (always or for given sectors; CMD18 then stops), CMD24/CMD25 data rejected.
//! - Answers are sent whatever CS does (but during CMD18); a partial command, a wait for
//!   a data token or a partial data block is dropped when CS goes high.
//!
//! Addressing (CMD17/CMD24): by default forgiving, `arg < 0x10000 ? arg : arg >> 9`.
//! `strict` uses exactly the card type's rule (SDHC block address, SDSC byte address)
//! and records violations: an SDSC byte address not a multiple of 512, fewer than
//! 74 SCLK cycles with CS high before the first command, and SCLK above 400 kHz before
//! the card is ready (from power-up or CMD0 until ACMD41/CMD1 answers 00), reported
//! once until the card is ready. The SPI master tells the card its SCLK (`set_sclk_hz`).
//!
//! The data is a raw sector image (sector n at byte 512 n); reads past its end return
//! zeros and writes past it grow it.

use std::collections::VecDeque;

pub const SECTOR: usize = 512;
/// SD spec: at least 74 clocks with CS high before the first command.
pub const POWER_UP_CLOCKS: u64 = 74;
/// SD spec: the most SCLK until the card leaves identification.
pub const INIT_SCLK_HZ: u64 = 400_000;

/// Faults to inject, for testing firmware error paths.
#[derive(Clone, Debug, Default)]
pub struct Faults {
    /// Commands the card never answers (e.g. 0 = no card behind a working socket).
    pub mute_cmds: Vec<u8>,
    /// ACMD41 keeps answering 01 (initialization never finishes).
    pub never_ready: bool,
    /// CMD17 answers with the data error token 08 instead of a block.
    pub read_error: bool,
    /// Sectors whose CMD17 gets the data error token 08 (others read normally).
    pub bad_sectors: Vec<u32>,
    /// (sector, n): the sector reads normally n times, then gets the error token (a
    /// card that fails between two reads of the same data).
    pub fail_after: Vec<(u32, u32)>,
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
    /// After CMD24 (`multi` false) or CMD25.
    WaitToken {
        lba: u32,
        multi: bool,
    },
    Data {
        lba: u32,
        multi: bool,
        buf: Vec<u8>,
    },
    /// CMD18: blocks from `lba` on, while a command is parsed from MOSI.
    ReadStream {
        lba: u32,
        buf: [u8; 6],
        n: usize,
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
    /// The previous command was CMD55: this one is an application command.
    app: bool,
    /// CMD59: CRC checking on.
    crc_on: bool,
    first_cmd_seen: bool,
    idle_clocks: u64,
    sclk_hz: u64,
    /// A too-fast SCLK was reported while not ready.
    fast_reported: bool,
    /// (cmd, arg) of every command received.
    pub commands: Vec<(u8, u32)>,
    /// Good CMD17 reads so far, per sector (for `Faults::fail_after`).
    reads: std::collections::HashMap<u32, u32>,
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
            app: false,
            crc_on: false,
            first_cmd_seen: false,
            idle_clocks: 0,
            sclk_hz: INIT_SCLK_HZ,
            fast_reported: false,
            commands: Vec::new(),
            reads: std::collections::HashMap::new(),
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

    /// SCLK of the bytes exchanged from now on.
    pub fn set_sclk_hz(&mut self, hz: u64) {
        self.sclk_hz = hz;
    }

    /// CS changed (true = selected).
    pub fn set_cs(&mut self, selected: bool) {
        if !selected {
            if let State::ReadStream { n, .. } = &mut self.state {
                *n = 0;
            } else {
                self.state = State::Idle;
            }
        }
    }

    /// Capacity in sectors, as the CSD gives it: the image, rounded up to whole
    /// sectors (the Python card: its highest sector + 1).
    fn sectors(&self) -> u32 {
        self.image.len().div_ceil(SECTOR) as u32
    }

    /// One byte exchanged with CS at `selected` for the whole byte: returns MISO.
    pub fn exchange(&mut self, mosi: u8, selected: bool) -> u8 {
        if !selected && !self.first_cmd_seen {
            self.idle_clocks += 8;
        }
        if self.strict && !self.ready && self.sclk_hz > INIT_SCLK_HZ && !self.fast_reported {
            self.fast_reported = true;
            self.violations.push(format!(
                "SCLK {} Hz before the card is ready, SD spec allows <= {INIT_SCLK_HZ} Hz",
                self.sclk_hz
            ));
        }
        if !selected && matches!(self.state, State::ReadStream { .. }) {
            return 0xFF;
        }
        if self.resp.is_empty() {
            if let State::ReadStream { lba, .. } = &mut self.state {
                let at = *lba;
                *lba = lba.wrapping_add(1);
                if !self.read_block(at) {
                    self.state = State::Idle;
                }
            }
        }
        let answer = self.resp.pop_front();
        if let State::ReadStream { buf, n, .. } = &mut self.state {
            if *n > 0 || mosi & 0xC0 == 0x40 {
                buf[*n] = mosi;
                *n += 1;
            }
            if *n == 6 {
                let cmd = *buf;
                self.resp.clear();
                self.state = State::Idle;
                self.command(cmd);
            }
            return answer.unwrap_or(0xFF);
        }
        if let Some(b) = answer {
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
                    let cmd = *buf;
                    self.state = State::Idle;
                    self.command(cmd);
                }
            }
            State::WaitToken { lba, multi } => {
                let (lba, multi) = (*lba, *multi);
                if mosi == if multi { 0xFC } else { 0xFE } {
                    self.state = State::Data {
                        lba,
                        multi,
                        buf: Vec::with_capacity(SECTOR + 2),
                    };
                } else if multi && mosi == 0xFD {
                    self.state = State::Idle;
                    self.resp.extend([0xFF, 0x00]);
                }
            }
            State::Data { lba, multi, buf } => {
                buf.push(mosi);
                if buf.len() == SECTOR + 2 {
                    let (lba, multi) = (*lba, *multi);
                    let data = std::mem::take(buf);
                    let crc = u16::from_be_bytes([data[SECTOR], data[SECTOR + 1]]);
                    let bad_crc = self.crc_on && crc != crc16(&data[..SECTOR]);
                    self.state = if multi {
                        State::WaitToken {
                            lba: lba.wrapping_add(1),
                            multi,
                        }
                    } else {
                        State::Resync
                    };
                    if self.faults.write_reject || bad_crc {
                        self.resp.push_back(0x0B);
                    } else {
                        self.set_sector(lba, &data[..SECTOR]);
                        self.resp.extend([0x05, 0x3F]);
                    }
                }
            }
            State::ReadStream { .. } | State::Resync => {}
        }
        0xFF
    }

    /// Queues FF FE, `data` and its CRC16.
    fn data_block(&mut self, data: &[u8]) {
        self.resp.extend([0xFF, 0xFE]);
        self.resp.extend(data);
        self.resp.extend(crc16(data).to_be_bytes());
    }

    /// Queues sector `lba` as a data block, or the error token FF 08 (faults). Returns
    /// false on an error.
    fn read_block(&mut self, lba: u32) -> bool {
        let reads = self.reads.entry(lba).or_insert(0);
        let worn = self
            .faults
            .fail_after
            .iter()
            .any(|&(s, n)| s == lba && *reads >= n);
        if self.faults.read_error || self.faults.bad_sectors.contains(&lba) || worn {
            self.resp.extend([0xFF, 0x08]);
            return false;
        }
        *reads += 1;
        let data = self.sector(lba);
        self.data_block(&data);
        true
    }

    /// CSD version 2.0 (SDHC/SDXC layout) for the image's capacity, 16 bytes with its
    /// CRC7. C_SIZE counts 512 KB units, rounded up; command classes 0, 2, 4, 7, 8.
    pub fn csd(&self) -> [u8; 16] {
        let c_size = self.sectors().div_ceil(1024).max(1) - 1;
        let mut c = [
            0x40, // CSD_STRUCTURE 1
            0x0E, // TAAC
            0x00, // NSAC
            0x32, // TRAN_SPEED: 25 MHz
            0x19, // CCC[11:4] = 0x195 >> 4
            0x59, // CCC[3:0] = 5, READ_BL_LEN 9
            0x00, // READ_BL_PARTIAL, misalign, DSR_IMP: 0
            (c_size >> 16) as u8 & 0x3F,
            (c_size >> 8) as u8,
            c_size as u8,
            0x7F, // ERASE_BLK_EN 1, SECTOR_SIZE[6:1] (0x7F)...
            0x80, // ...SECTOR_SIZE bit 0, WP_GRP_SIZE 0
            0x0A, // WP_GRP_ENABLE 0, R2W_FACTOR 2, WRITE_BL_LEN[3:2] = 2
            0x40, // WRITE_BL_LEN[1:0] = 1, WRITE_BL_PARTIAL 0
            0x00, // FILE_FORMAT_GRP, COPY, PERM/TMP_WRITE_PROTECT, FILE_FORMAT: 0
            0x00,
        ];
        c[15] = crc7(&c[..15]) << 1 | 1;
        c
    }

    /// CID: manufacturer 0x56, OEM "VX", product "VUX9K", revision 1.0, serial 1,
    /// November 2026.
    pub fn cid() -> [u8; 16] {
        let mut c = [
            0x56, b'V', b'X', b'V', b'U', b'X', b'9', b'K', 0x10, 0, 0, 0, 1, 0x01, 0xAB, 0,
        ];
        c[15] = crc7(&c[..15]) << 1 | 1;
        c
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

    fn command(&mut self, buf: [u8; 6]) {
        let cmd = buf[0] & 0x3F;
        let arg = u32::from_be_bytes([buf[1], buf[2], buf[3], buf[4]]);
        let app = std::mem::take(&mut self.app);
        self.commands.push((cmd, arg));
        let want = crc7(&buf[..5]) << 1 | 1;
        if self.strict && self.crc_on && buf[5] != want {
            self.violations.push(format!(
                "CMD{cmd}: CRC7 byte 0x{:02X}, expected 0x{want:02X} (CRC on)",
                buf[5]
            ));
        }
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
            55 => {
                self.app = true;
                r.extend([0xFF, if self.ready { 0x00 } else { 0x01 }]);
            }
            // CMD1 (SEND_OP_COND, MMC): initialization without CMD55/ACMD41
            1 | 41 => {
                self.ready = !self.faults.never_ready;
                self.fast_reported &= !self.ready;
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
            16 | 12 => r.extend([0xFF, 0x00]),
            59 => {
                self.crc_on = arg & 1 != 0;
                r.extend([0xFF, 0x00]);
            }
            13 if app => {
                r.extend([0xFF, 0x00, 0x00]);
                self.data_block(&[0; 64]);
            }
            13 => r.extend([0xFF, 0x00, 0x00]),
            51 if app => {
                r.extend([0xFF, 0x00]);
                // SCR: structure 0, SD spec 2.00, security SDHC, bus widths 1 and 4
                self.data_block(&[0x02, 0x35, 0, 0, 0, 0, 0, 0]);
            }
            9 => {
                r.extend([0xFF, 0x00]);
                let csd = self.csd();
                self.data_block(&csd);
            }
            10 => {
                r.extend([0xFF, 0x00]);
                self.data_block(&Self::cid());
            }
            17 => {
                let lba = self.lba(cmd, arg);
                self.resp.extend([0xFF, 0x00]);
                self.read_block(lba);
            }
            18 => {
                let lba = self.lba(cmd, arg);
                self.resp.extend([0xFF, 0x00]);
                if self.read_block(lba) {
                    self.state = State::ReadStream {
                        lba: lba.wrapping_add(1),
                        buf: [0; 6],
                        n: 0,
                    };
                }
            }
            24 | 25 => {
                let lba = self.lba(cmd, arg);
                self.resp.extend([0xFF, 0x00]);
                self.state = State::WaitToken {
                    lba,
                    multi: cmd == 25,
                };
            }
            _ => {}
        }
    }
}

/// CRC7 of SD commands and registers (x^7 + x^3 + 1), MSB first.
pub fn crc7(data: &[u8]) -> u8 {
    let mut crc = 0u8;
    for &b in data {
        for i in (0..8).rev() {
            let bit = (b >> i) & 1 ^ (crc >> 6) & 1;
            crc = (crc << 1) & 0x7F;
            if bit != 0 {
                crc ^= 0x09;
            }
        }
    }
    crc
}

/// CRC16 of SD data blocks (CCITT x^16 + x^12 + x^5 + 1, initial 0), MSB first.
pub fn crc16(data: &[u8]) -> u16 {
    let mut crc = 0u16;
    for &b in data {
        crc ^= (b as u16) << 8;
        for _ in 0..8 {
            crc = if crc & 0x8000 != 0 {
                crc << 1 ^ 0x1021
            } else {
                crc << 1
            };
        }
    }
    crc
}
