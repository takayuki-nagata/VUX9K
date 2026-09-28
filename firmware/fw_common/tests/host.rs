// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! Host tests of fw_common. Header vectors come from tools/vux_tool.py's
//! build_vux9_image (the tool that writes the slots), so the parser and the tool
//! are checked against each other.

extern crate std;
use std::vec::Vec;

use fw_common::fmt::Sink;
use fw_common::header::{slot_sector, SlotHeader, FIRST_SECTOR_PAYLOAD, HEADER_LEN};
use fw_common::update::{precheck, Precheck, MAX_IMAGE};
use fw_common::{crc32_update, mailbox, mul_u32};

fn hex(s: &str) -> Vec<u8> {
    (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap())
        .collect()
}

// build_vux9_image(bytes.fromhex("13000000") * 4, slot=0, name="BootMgr", mode="riscv", version=11)
const BOOTMGR_V11: &str = "3958555603000500010000001000000000000000000000008c304ae80b000000\
                           426f6f744d677200000000000000000000000000000000000000000000000000";
// build_vux9_image(bytes.fromhex("13000000") * 4, slot=2, name="Hk", mode="hack", version=1)
const HACK_V1: &str = "395855560300010000000000100000000000000000000000d387e6c601000000\
                       486b000000000000000000000000000000000000000000000000000000000000";

struct Buf(Vec<u8>);
impl Sink for Buf {
    fn put(&mut self, b: u8) {
        self.0.push(b);
    }
}
fn out(f: impl FnOnce(&mut Buf)) -> std::string::String {
    let mut b = Buf(Vec::new());
    f(&mut b);
    std::string::String::from_utf8(b.0).unwrap()
}

#[test]
fn slot_layout() {
    assert_eq!(slot_sector(0), 64);
    assert_eq!(slot_sector(1), 128);
    assert_eq!(slot_sector(9), 640);
    assert_eq!(HEADER_LEN + FIRST_SECTOR_PAYLOAD, 512);
}

#[test]
fn parses_vux_tool_headers() {
    let h = SlotHeader::parse(&hex(BOOTMGR_V11)).unwrap();
    assert_eq!((h.header_version, h.flags), (3, 5), "valid | system slot");
    assert!(h.is_riscv());
    assert_eq!((h.size, h.load_addr, h.entry_point), (16, 0, 0));
    assert_eq!((h.crc32, h.version), (3_897_176_204, 11));
    assert_eq!(&h.name[..h.name_len()], b"BootMgr");

    let h = SlotHeader::parse(&hex(HACK_V1)).unwrap();
    assert!(!h.is_riscv());
    assert_eq!(&h.name[..h.name_len()], b"Hk");
}

#[test]
fn rejects_invalid_headers() {
    let good = hex(BOOTMGR_V11);
    assert!(SlotHeader::parse(&good[..63]).is_none(), "too short");
    assert!(SlotHeader::parse(&[0u8; 512]).is_none(), "empty slot");
    let mut bad = good.clone();
    bad[0] ^= 1;
    assert!(SlotHeader::parse(&bad).is_none(), "magic");
    let mut bad = good.clone();
    bad[4] = 2;
    assert!(SlotHeader::parse(&bad).is_none(), "header version");
    let mut bad = good.clone();
    bad[6] &= !1;
    assert!(SlotHeader::parse(&bad).is_none(), "valid flag");
}

#[test]
fn name_stops_at_nul_or_unprintable() {
    let mut s = hex(BOOTMGR_V11);
    assert_eq!(SlotHeader::parse(&s).unwrap().name_len(), 7);
    s[32 + 3] = 0x07;
    assert_eq!(SlotHeader::parse(&s).unwrap().name_len(), 3);
    for b in &mut s[32..64] {
        *b = b'x';
    }
    assert_eq!(SlotHeader::parse(&s).unwrap().name_len(), 32);
}

#[test]
fn crc32_matches_zlib() {
    let crc = |d: &[u8]| crc32_update(d, 0xFFFF_FFFF) ^ 0xFFFF_FFFF;
    assert_eq!(crc(b"123456789"), 0xCBF4_3926);
    assert_eq!(crc(b""), 0);
    // The payload of BOOTMGR_V11 (four NOPs): the CRC vux_tool put in the header
    let payload = hex("13000000").repeat(4);
    assert_eq!(crc(&payload), 3_897_176_204);
    // Chunked, as the Boot Manager reads it sector by sector
    let data: Vec<u8> = (0..2000u32).map(|i| (i * 7) as u8).collect();
    let chunked = data
        .chunks(448)
        .fold(0xFFFF_FFFF, |c, ch| crc32_update(ch, c))
        ^ 0xFFFF_FFFF;
    assert_eq!(chunked, crc(&data));
}

#[test]
fn multiplies() {
    for (a, b) in [
        (0, 5),
        (5, 0),
        (27, 1000),
        (27_000, 5000),
        (0xFFFF_FFFF, 3),
        (123_457, 98_765),
    ] {
        assert_eq!(mul_u32(a, b), a.wrapping_mul(b), "{a} * {b}");
    }
}

#[test]
fn mailbox_words() {
    assert_eq!(mailbox::launch(3, false), 3);
    assert_eq!(mailbox::launch(9, true), 0x109);
    assert_eq!(mailbox::update(true), 0xA55A_0100);
    assert_eq!(mailbox::update(false), mailbox::SLOT_UPDATE_MAGIC);
}

#[test]
fn update_prechecks_in_order() {
    let base = SlotHeader::parse(&hex(BOOTMGR_V11)).unwrap();
    let nop = 0x13;
    assert_eq!(precheck(&base, 10, nop), Precheck::Candidate);
    assert_eq!(precheck(&base, 11, nop), Precheck::NotNewer);
    assert_eq!(precheck(&base, 10, 0), Precheck::BadEntry(0));
    assert_eq!(
        precheck(&base, 10, 0xFFFF_FFFF),
        Precheck::BadEntry(0xFFFF_FFFF)
    );
    let hack = SlotHeader { mode: 0, ..base };
    assert_eq!(
        precheck(&hack, 10, 0),
        Precheck::NotRiscv,
        "mode is checked first"
    );
    let big = SlotHeader {
        size: MAX_IMAGE + 1,
        ..base
    };
    assert_eq!(
        precheck(&big, 99, 0),
        Precheck::BadSize(MAX_IMAGE + 1),
        "size before version"
    );
    assert_eq!(
        precheck(
            &SlotHeader {
                size: MAX_IMAGE,
                ..base
            },
            10,
            nop
        ),
        Precheck::Candidate
    );
    assert_eq!(
        precheck(&SlotHeader { size: 0, ..base }, 10, nop),
        Precheck::BadSize(0)
    );
}

#[test]
fn formats_numbers() {
    assert_eq!(out(|s| s.put_hex(0x0012_ABCD)), "0012ABCD");
    assert_eq!(out(|s| s.put_hex_byte(0x0F)), "0F");
    for v in [0u32, 7, 10, 100, 270_020, 4_294_967_295] {
        assert_eq!(out(|s| s.put_dec(v)), std::format!("{v}"));
    }
    assert_eq!(out(|s| s.put_str("vux> ")), "vux> ");
}
