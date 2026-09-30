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

// ----- slot::payload_crc -----------------------------------------------------------
use fw_common::header::SECTOR;
use fw_common::slot::payload_crc;

/// A slot's sectors holding `payload` after a 64-byte header, as vux_tool lays it out.
fn slot_sectors(payload: &[u8]) -> Vec<[u8; SECTOR]> {
    let mut raw = std::vec![0u8; HEADER_LEN];
    raw.extend_from_slice(payload);
    raw.resize(raw.len().div_ceil(SECTOR) * SECTOR, 0);
    raw.chunks(SECTOR).map(|c| c.try_into().unwrap()).collect()
}

fn zlib_crc(data: &[u8]) -> u32 {
    crc32_update(data, 0xFFFF_FFFF) ^ 0xFFFF_FFFF
}

#[test]
fn payload_crc_walks_the_slot() {
    for len in [
        1usize,
        FIRST_SECTOR_PAYLOAD,
        FIRST_SECTOR_PAYLOAD + 1,
        1000,
        14 * 1024,
    ] {
        let payload: Vec<u8> = (0..len).map(|i| (i * 7 + 3) as u8).collect();
        let sectors = slot_sectors(&payload);
        let mut buf = sectors[0];
        let mut reads = Vec::new();
        let crc = payload_crc(len as u32, 128, &mut buf, |s, b| {
            reads.push(s);
            *b = sectors[(s - 128) as usize];
            true
        });
        assert_eq!(crc, Ok(zlib_crc(&payload)), "{len} bytes");
        assert_eq!(reads, (129..128 + sectors.len() as u32).collect::<Vec<_>>());
    }
}

#[test]
fn payload_crc_names_the_unreadable_sector() {
    let sectors = slot_sectors(&[0x55; 2000]);
    let mut buf = sectors[0];
    assert_eq!(payload_crc(2000, 64, &mut buf, |s, _| s != 66), Err(66));
}

// ----- upload (the 'w' command) ------------------------------------------------------
use fw_common::upload::{upload, Disk, Host};
use std::collections::VecDeque;

struct FakeHost {
    input: VecDeque<u8>,
    output: Vec<u8>,
    drained: bool,
}
impl Sink for FakeHost {
    fn put(&mut self, b: u8) {
        self.output.push(b);
    }
}
impl Host for FakeHost {
    /// Everything queued arrives in time; then the host goes quiet (timeout).
    fn recv(&mut self, timeout_ms: u32) -> Option<u8> {
        assert_eq!(timeout_ms, 5000);
        self.input.pop_front()
    }
    fn drain(&mut self) {
        self.drained = true;
    }
}

struct FakeDisk {
    ready: bool,
    bad_sector: Option<u32>,
    written: Vec<(u32, [u8; SECTOR])>,
}
impl Disk for FakeDisk {
    fn init(&mut self) -> bool {
        self.ready
    }
    fn write(&mut self, sector: u32, data: &[u8; SECTOR]) -> bool {
        self.written.push((sector, *data));
        Some(sector) != self.bad_sector
    }
}

fn run_upload(input: &[u8], disk: &mut FakeDisk) -> std::string::String {
    let mut host = FakeHost {
        input: input.iter().copied().collect(),
        output: Vec::new(),
        drained: false,
    };
    upload(&mut host, disk);
    assert!(host.drained, "stale input is dropped first");
    std::string::String::from_utf8(host.output).unwrap()
}

fn disk() -> FakeDisk {
    FakeDisk {
        ready: true,
        bad_sector: None,
        written: Vec::new(),
    }
}

fn sectors_of(slot: u8, count: u8) -> Vec<u8> {
    let mut v = std::vec![slot, count];
    for s in 0..count {
        v.extend((0..SECTOR).map(|i| (i as u8) ^ s));
    }
    v
}

#[test]
fn upload_writes_every_sector() {
    let mut d = disk();
    let out = run_upload(&sectors_of(3, 2), &mut d);
    assert_eq!(
        out,
        "[READY]\n[READY-SLOT:3]\n[READY-COUNT:2]\n[READY-SEC:0]\n[READY-SEC:1]\n\
         [SD] Successfully wrote 2 sectors to Slot 3 (Sector 256)! [OK]\n\n"
    );
    assert_eq!(
        d.written.iter().map(|w| w.0).collect::<Vec<_>>(),
        [256, 257]
    );
    assert_eq!(d.written[1].1[5], 5 ^ 1);
}

#[test]
fn upload_skips_line_ends_before_the_slot_id() {
    let mut input = std::vec![b'\r', b'\n'];
    input.extend(sectors_of(0, 1));
    let out = run_upload(&input, &mut disk());
    assert!(out.contains("[READY-SLOT:0]\n[READY-COUNT:1]\n"), "{out}");
    assert!(out.ends_with("! [OK]\n\n"), "{out}");
}

#[test]
fn upload_takes_every_sector_count() {
    // 10 and 13 are LF and CR: a count, not line ends to skip
    for count in [9u8, 10, 13, 64] {
        let mut d = disk();
        let out = run_upload(&sectors_of(2, count), &mut d);
        assert!(
            out.contains(&std::format!("[READY-COUNT:{count}]")),
            "{count}: {out}"
        );
        assert_eq!(d.written.len(), count as usize);
    }
}

#[test]
fn upload_rejects_what_the_host_should_not_send() {
    for (input, err) in [
        (&[0x0B][..], "[SD-ERR] Invalid slot ID: 0x0B\n"),
        (&[1, 0][..], "[SD-ERR] Invalid sector count: 0x00\n"),
        (&[1, 65][..], "[SD-ERR] Invalid sector count: 0x41\n"),
        (&[][..], "[SD-ERR] Slot ID timeout!\n"),
        (&[1][..], "[SD-ERR] Sector count timeout!\n"),
        (
            &[1, 1, 7, 7, 7, 7, 7][..],
            "[READY-SEC:0]\n[SD-ERR] Timeout at sector 0, byte 5\n",
        ),
    ] {
        let mut d = disk();
        let out = run_upload(input, &mut d);
        assert!(out.ends_with(err), "{input:?}: {out}");
        assert!(d.written.is_empty());
    }
}

#[test]
fn upload_reports_card_errors() {
    let mut d = FakeDisk {
        ready: false,
        ..disk()
    };
    let out = run_upload(&sectors_of(1, 1), &mut d);
    assert!(
        out.ends_with("[READY-COUNT:1]\n[SD-ERR] Failed to initialize SD card!\n"),
        "{out}"
    );

    let mut d = FakeDisk {
        bad_sector: Some(129),
        ..disk()
    };
    let out = run_upload(&sectors_of(1, 3), &mut d);
    assert!(
        out.ends_with("[READY-SEC:1]\n[SD-ERR] Failed to write block at sector 129\n"),
        "{out}"
    );
    assert_eq!(d.written.len(), 2, "stops at the failed sector");
}
