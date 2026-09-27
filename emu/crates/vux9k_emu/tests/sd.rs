// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! SD: the SPI master's busy window and bit-by-bit shift register, and the card's
//! answers (initialization, CMD17/CMD24, SDSC/strict addressing, injected faults).

use vux9k_emu::periph::sdspi::{HALF_PERIOD, TRANSFER};
use vux9k_emu::periph::SdSpi;
use vux9k_emu::sdcard::{SdCard, SECTOR};
use vux9k_emu::{Profile, Soc};

const DATA: u32 = 0x4000_2000;
const CS: u32 = 0x4000_2004;
const BUSY: u32 = 0x4000_2008;

/// Host side of the SPI bus, like the firmware's `transfer`/`set_cs`.
struct Host {
    sd: SdSpi,
    c: u64,
}

impl Host {
    fn new(card: SdCard) -> Self {
        let mut sd = SdSpi::default();
        sd.card = Some(card);
        Host { sd, c: 10 }
    }
    fn xfer(&mut self, b: u8) -> u8 {
        self.sd.write(DATA, b as u32, self.c);
        self.c += TRANSFER + 1;
        assert_eq!(self.sd.read(BUSY, self.c), 0);
        let r = self.sd.read(DATA, self.c) as u8;
        self.c += 5;
        r
    }
    fn cs(&mut self, selected: bool) {
        self.sd.write(CS, !selected as u32, self.c);
        self.c += 3;
    }
    fn cmd(&mut self, cmd: u8, arg: u32) -> u8 {
        self.cs(true);
        self.xfer(0x40 | cmd);
        for b in arg.to_be_bytes() {
            self.xfer(b);
        }
        self.xfer(0x95);
        for _ in 0..200 {
            let r = self.xfer(0xFF);
            if r != 0xFF {
                return r;
            }
        }
        0xFF
    }
    fn deselect(&mut self) {
        self.cs(false);
        self.xfer(0xFF);
    }
    fn bytes(&mut self, n: usize) -> Vec<u8> {
        (0..n).map(|_| self.xfer(0xFF)).collect()
    }
    /// Returns the OCR.
    fn init(&mut self) -> Vec<u8> {
        self.deselect();
        for _ in 0..16 {
            self.xfer(0xFF);
        }
        assert_eq!(self.cmd(0, 0), 0x01);
        self.deselect();
        assert_eq!(self.cmd(8, 0x1AA), 0x01);
        assert_eq!(self.bytes(4), [0, 0, 1, 0xAA]);
        self.deselect();
        assert_eq!(self.cmd(55, 0), 0x01);
        self.deselect();
        assert_eq!(self.cmd(41, 0x4000_0000), 0x00);
        self.deselect();
        assert_eq!(self.cmd(58, 0), 0x00);
        self.bytes(4)
    }
    fn card(&self) -> &SdCard {
        self.sd.card.as_ref().unwrap()
    }
}

#[test]
fn transfer_is_busy_for_16_half_periods_and_shifts_msb_first() {
    let mut sd = SdSpi::default();
    assert_eq!(sd.read(DATA, 0), 0xFF, "shift_rx resets to FF");
    let mut card = SdCard::new(vec![]);
    card.set_sector(0, &[]);
    sd.card = Some(card);
    // Deselected card, no answer queued: MISO stays high
    sd.write(DATA, 0x00, 100);
    assert!(!sd.busy(100));
    assert!(sd.busy(101));
    assert!(sd.busy(100 + TRANSFER));
    assert!(!sd.busy(101 + TRANSFER));
    // Writes while busy are ignored, CS included
    sd.write(CS, 0, 200);
    assert_eq!(sd.read(CS, 700), 1);
    sd.write(CS, 0, 700);
    assert_eq!(sd.read(CS, 701), 0);
}

#[test]
fn shift_register_fills_one_bit_per_rising_edge() {
    let mut h = Host::new(SdCard::new(vec![]));
    h.cs(true);
    for b in [0x40, 0, 0, 0, 0, 0x95] {
        h.xfer(b); // CMD0
    }
    h.xfer(0xFF); // FF
    let w = h.c;
    h.sd.write(DATA, 0xFF, w); // R1 = 0x01, shifted into FF
    let at = |i: u64| w + HALF_PERIOD * (2 * i + 1) + 1; // bit i visible from here
    assert_eq!(h.sd.read(DATA, at(0) - 1), 0xFF);
    assert_eq!(h.sd.read(DATA, at(0)), 0xFE);
    assert_eq!(h.sd.read(DATA, at(6)), 0x80);
    assert_eq!(h.sd.read(DATA, at(7) - 1), 0x80);
    assert_eq!(h.sd.read(DATA, at(7)), 0x01);
}

#[test]
fn initialization_sequence_and_ocr() {
    let mut h = Host::new(SdCard::new(vec![]));
    assert_eq!(h.init(), [0xC0, 0xFF, 0x80, 0x00], "SDHC");
    h.deselect();
    assert_eq!(h.cmd(55, 0), 0x00, "ready now");
    h.deselect();
    // An answer not read to the end is still sent: the next command is lost
    assert_eq!(h.cmd(8, 0x1AA), 0x01);
    h.deselect();
    assert_eq!(h.cmd(55, 0), 0xFF, "the card was still sending CMD8's R7");
    h.deselect();
    assert_eq!(h.cmd(16, 512), 0x00);
    h.deselect();
    assert_eq!(h.cmd(1, 0), 0xFF, "unknown commands get no answer");
    h.deselect();
    let cmds: Vec<u8> = h.card().commands.iter().map(|c| c.0).collect();
    assert_eq!(cmds, [0, 8, 55, 41, 58, 55, 8, 16, 1]);
    assert_eq!(h.card().idle_clocks(), 17 * 8);
}

#[test]
fn read_and_write_blocks() {
    let mut image = vec![0u8; 3 * SECTOR];
    image[SECTOR..2 * SECTOR]
        .iter_mut()
        .enumerate()
        .for_each(|(i, b)| *b = i as u8);
    let mut h = Host::new(SdCard::new(image));
    h.init();
    h.deselect();

    assert_eq!(h.cmd(17, 1), 0x00);
    assert_eq!(h.bytes(1), [0xFF]);
    assert_eq!(h.bytes(1), [0xFE]);
    let data = h.bytes(SECTOR);
    assert!(data.iter().enumerate().all(|(i, &b)| b == i as u8));
    assert_eq!(h.bytes(2), [0x12, 0x34]);
    h.deselect();

    // Write sector 200 (past the image: it grows)
    assert_eq!(h.cmd(24, 200), 0x00);
    h.xfer(0xFF);
    h.xfer(0xFE);
    for i in 0..SECTOR {
        h.xfer(!(i as u8));
    }
    h.xfer(0xFF);
    h.xfer(0xFF);
    assert_eq!(h.xfer(0xFF), 0x05, "data accepted");
    assert_eq!(h.xfer(0xFF), 0x3F, "busy byte");
    assert_eq!(h.xfer(0xFF), 0xFF);
    // Still selected: a command now is lost, as with the Python model
    assert_eq!(h.cmd(17, 200), 0xFF);
    h.deselect();
    assert_eq!(h.card().image().len(), 201 * SECTOR);
    assert_eq!(h.card().sector(200)[1], !1u8);

    // Byte addresses >= 0x10000 are accepted in forgiving mode
    assert_eq!(h.cmd(17, 200 << 9), 0x00);
    let d = h.bytes(2 + 3);
    assert_eq!(d[2..], [0xFF, 0xFE, 0xFD]);
    h.deselect();
}

#[test]
fn strict_sdsc_records_violations() {
    let mut card = SdCard::new(vec![]);
    card.sdhc = false;
    card.strict = true;
    let mut h = Host::new(card);
    h.cs(true); // no power-up clocks with CS high
    assert_eq!(h.cmd(0, 0), 0x01);
    h.deselect();
    assert_eq!(h.cmd(58, 0), 0x00);
    assert_eq!(h.bytes(4)[0], 0x80, "SDSC: CCS clear");
    h.deselect();
    assert_eq!(h.cmd(17, 3), 0x00, "a block address sent to an SDSC card");
    h.deselect();
    let v = &h.card().violations;
    assert_eq!(v.len(), 2, "{v:?}");
    assert!(v[0].starts_with("power-up: only 0 SCLK"));
    assert!(v[1].contains("0x3 is not sector-aligned"));
}

#[test]
fn injected_faults() {
    let mut card = SdCard::new(vec![]);
    card.faults.mute_cmds = vec![0];
    let mut h = Host::new(card);
    assert_eq!(h.cmd(0, 0), 0xFF);
    h.deselect();

    let mut card = SdCard::new(vec![]);
    card.faults.never_ready = true;
    card.faults.read_error = true;
    card.faults.write_reject = true;
    let mut h = Host::new(card);
    assert_eq!(h.cmd(41, 0), 0x01);
    h.deselect();
    assert_eq!(h.cmd(55, 0), 0x01);
    h.deselect();
    assert_eq!(h.cmd(17, 0), 0x00);
    assert_eq!(h.bytes(2), [0xFF, 0x08], "data error token");
    h.deselect();
    assert_eq!(h.cmd(24, 0), 0x00);
    h.xfer(0xFE);
    for _ in 0..SECTOR + 2 {
        h.xfer(0xAA);
    }
    assert_eq!(h.xfer(0xFF), 0x0B, "rejected");
    h.deselect();
    assert_eq!(h.card().image().len(), 0, "not written");
}

#[test]
fn deselect_drops_a_partial_command() {
    let mut h = Host::new(SdCard::new(vec![]));
    h.cs(true);
    h.xfer(0x40);
    h.xfer(0);
    h.deselect();
    assert_eq!(h.cmd(0, 0), 0x01);
    assert_eq!(h.card().commands, [(0, 0)]);
}

fn i(op: u32, imm: i32, rs1: u32, f3: u32, rd: u32) -> u32 {
    ((imm as u32 & 0xFFF) << 20) | (rs1 << 15) | (f3 << 12) | (rd << 7) | op
}
fn addi(rd: u32, rs1: u32, imm: i32) -> u32 {
    i(0x13, imm, rs1, 0, rd)
}
fn lw(rd: u32, rs1: u32, imm: i32) -> u32 {
    i(0x03, imm, rs1, 2, rd)
}
fn sw(rs2: u32, rs1: u32, imm: i32) -> u32 {
    let u = imm as u32;
    ((u >> 5 & 0x7F) << 25) | (rs2 << 20) | (rs1 << 15) | (2 << 12) | ((u & 0x1F) << 7) | 0x23
}
fn lui(rd: u32, imm20: u32) -> u32 {
    (imm20 << 12) | (rd << 7) | 0x37
}

#[test]
fn cpu_sees_busy_from_the_cycle_after_its_store() {
    // sw fetched at 4 (edge 6); lw fetched at 7 reads busy in cycle 8
    let mut soc = Soc::new(Profile::Real);
    let words = [
        lui(1, 0x40002),
        addi(2, 0, 0x5A),
        sw(2, 1, 0),
        lw(3, 1, 8),
        lw(4, 1, 4),
    ];
    let bytes: Vec<u8> = words.iter().flat_map(|w| w.to_le_bytes()).collect();
    soc.load_iram(0, &bytes);
    for _ in 0..5 {
        soc.step();
    }
    assert_eq!((soc.regs[3], soc.regs[4]), (1, 1), "busy, deselected");
}
