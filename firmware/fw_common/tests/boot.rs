// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! Host tests of fw_common::boot: the first stage's RAM boot receiver (through
//! upload_v2, as the first stage runs it) and its error texts.

extern crate std;
use std::collections::{BTreeMap, VecDeque};
use std::string::String;
use std::vec::Vec;

use fw_common::boot::{check_stage2, put_boot_error, RamBoot, RAM_BOOT_MAX, STAGE2_WINDOW};
use fw_common::crc32_update;
use fw_common::fat::stage2::BootError;
use fw_common::fat::FatError;
use fw_common::fmt::Sink;
use fw_common::header::{ImageError, ImageHeader, HEADER_LEN, MODE_HACK, MODE_RISCV};
use fw_common::upload::{upload_v2, Host};

const S2: u32 = 0x8070_0000;

struct FakeHost {
    input: VecDeque<u8>,
    output: Vec<u8>,
}
impl Sink for FakeHost {
    fn put(&mut self, b: u8) {
        self.output.push(b);
    }
}
impl Host for FakeHost {
    fn recv(&mut self, _timeout_ms: u32) -> Option<u8> {
        self.input.pop_front()
    }
    fn drain(&mut self) {}
}

/// A VUX9 v4 file: `n` payload bytes, loading and starting at `load`.
fn v4(n: usize, mode: u32, load: u32) -> Vec<u8> {
    let payload: Vec<u8> = (0..n).map(|i| (i as u8).wrapping_mul(7) ^ 0x5A).collect();
    let mut f = std::vec![0u8; HEADER_LEN];
    let mut put = |at: usize, v: u32| f[at..at + 4].copy_from_slice(&v.to_le_bytes());
    put(0, 0x5655_5839);
    put(4, 4);
    put(8, mode);
    put(12, n as u32);
    put(16, load);
    put(20, load);
    put(24, crc32_update(&payload, 0xFFFF_FFFF) ^ 0xFFFF_FFFF);
    put(28, 3);
    f.extend_from_slice(&payload);
    f
}

/// What a RAM boot of `file` does: upload_v2's result, the header to start, the error,
/// the device's lines and the memory written (address -> byte).
struct Run {
    len: Option<u32>,
    header: Option<ImageHeader>,
    error: Option<BootError>,
    out: String,
    mem: BTreeMap<u32, u8>,
}

fn ram_boot(file: &[u8], crc: Option<u32>) -> Run {
    let mut input: Vec<u8> = (file.len() as u32).to_le_bytes().to_vec();
    input.extend_from_slice(file);
    let crc = crc.unwrap_or(crc32_update(file, 0xFFFF_FFFF) ^ 0xFFFF_FFFF);
    input.extend_from_slice(&crc.to_le_bytes());
    let mut host = FakeHost {
        input: input.into_iter().collect(),
        output: Vec::new(),
    };
    let mut mem = BTreeMap::new();
    let mut write = |addr: u32, data: &[u8]| {
        for (i, &b) in data.iter().enumerate() {
            assert!(mem.insert(addr + i as u32, b).is_none(), "written once");
        }
    };
    let (len, header, error) = {
        let mut sink = RamBoot::new(STAGE2_WINDOW, &mut write);
        let len = upload_v2(&mut host, &mut sink, RAM_BOOT_MAX);
        (len, sink.header(), sink.error)
    };
    Run {
        len,
        header,
        error,
        out: String::from_utf8(host.output).unwrap(),
        mem,
    }
}

#[test]
fn ram_boot_loads_the_payload_at_its_address() {
    for n in [4, 448, 449, 1000, 4096] {
        let file = v4(n, MODE_RISCV, S2);
        let r = ram_boot(&file, None);
        assert_eq!(r.len, Some(file.len() as u32), "{n}: {}", r.out);
        let h = r.header.expect("header");
        assert_eq!((h.size, h.load_addr, h.version), (n as u32, S2, 3));
        assert_eq!(r.error, None);
        let want: BTreeMap<u32, u8> = file[HEADER_LEN..]
            .iter()
            .enumerate()
            .map(|(i, &b)| (S2 + i as u32, b))
            .collect();
        assert_eq!(r.mem, want, "{n}");
    }
    // The last word of the window
    let r = ram_boot(&v4(4, MODE_RISCV, 0x807F_FFFC), None);
    assert!(r.header.is_some(), "{}", r.out);
}

#[test]
fn ram_boot_refuses_before_storing_anything() {
    let mut bad_magic = v4(16, MODE_RISCV, S2);
    bad_magic[0] ^= 1;
    let mut short = v4(16, MODE_RISCV, S2);
    short[12] = 15;
    let cases: [(Vec<u8>, BootError); 5] = [
        (bad_magic, BootError::NotV4),
        (short, BootError::SizeMismatch),
        (
            v4(16, MODE_RISCV, 0x8000_0000),
            BootError::Image(ImageError::OutOfWindow),
        ),
        (
            v4(16, MODE_RISCV, 0x807F_FFFC),
            BootError::Image(ImageError::OutOfWindow),
        ),
        (
            v4(16, MODE_HACK, S2),
            BootError::Image(ImageError::BadMode(MODE_HACK)),
        ),
    ];
    for (file, want) in cases {
        let r = ram_boot(&file, None);
        assert_eq!((r.len, r.header, r.error), (None, None, Some(want)));
        assert!(r.mem.is_empty());
        assert!(r.out.contains("[ERR] Store failed at block 0"), "{}", r.out);
    }
    // No payload: refused at the length
    let r = ram_boot(&v4(0, MODE_RISCV, S2), None);
    assert_eq!((r.len, r.error), (None, Some(BootError::NotV4)));
    assert!(r.out.contains("[ERR] Cannot store 64 bytes"), "{}", r.out);
}

#[test]
fn ram_boot_checks_the_payload_crc() {
    let mut file = v4(600, MODE_RISCV, S2);
    file[24] ^= 1;
    // The transfer's own CRC is right: the header's is not the payload's
    let r = ram_boot(&file, None);
    assert_eq!(
        (r.len, r.header, r.error),
        (None, None, Some(BootError::Crc))
    );
    assert!(r.out.contains("[ERR] Commit failed"), "{}", r.out);
    // A transfer that fails leaves no header to start
    let r = ram_boot(&v4(600, MODE_RISCV, S2), Some(0));
    assert_eq!((r.len, r.header, r.error), (None, None, None));
    assert!(r.out.contains("[ERR] CRC mismatch"), "{}", r.out);
}

#[test]
fn stage2_is_rv32_only() {
    let h = ImageHeader::parse(&v4(4, MODE_RISCV, S2)).unwrap();
    assert_eq!(check_stage2(&h), Ok(()));
    let h = ImageHeader::parse(&v4(4, MODE_HACK, S2)).unwrap();
    assert_eq!(
        check_stage2(&h),
        Err(BootError::Image(ImageError::BadMode(MODE_HACK)))
    );
}

struct Buf(Vec<u8>);
impl Sink for Buf {
    fn put(&mut self, b: u8) {
        self.0.push(b);
    }
}

#[test]
fn boot_errors_read_as_text() {
    let cases: [(BootError, &str); 18] = [
        (FatError::Io(2048).into(), "card error at LBA 2048"),
        (
            FatError::NoSignature.into(),
            "no 55AA signature (MBR or boot sector)",
        ),
        (FatError::NoPartition.into(), "no partition"),
        (
            FatError::NotFat32.into(),
            "not FAT32 (reformat the card as FAT32)",
        ),
        (FatError::BadBpb.into(), "bad FAT32 boot sector"),
        (
            FatError::BadChain(77).into(),
            "bad cluster chain at cluster 77",
        ),
        (FatError::BadName.into(), "bad name"),
        (FatError::NotFound.into(), "not found"),
        (FatError::WrongType.into(), "not a file"),
        (FatError::TooBig.into(), "too big"),
        (FatError::Exists.into(), "exists"),
        (FatError::DiskFull.into(), "card full"),
        (FatError::Verify.into(), "read back differently"),
        (BootError::NotV4, "not a VUX9 v4 file"),
        (BootError::SizeMismatch, "file size is not the header's"),
        (
            BootError::Image(ImageError::BadEntry(0x8070_0002)),
            "bad entry point 80700002",
        ),
        (BootError::Image(ImageError::BadMode(0)), "not RV32, mode 0"),
        (BootError::Crc, "CRC mismatch"),
    ];
    for (e, want) in cases {
        let mut b = Buf(Vec::new());
        put_boot_error(&mut b, e);
        assert_eq!(String::from_utf8(b.0).unwrap(), want);
    }
    #[cfg(feature = "fat-full")]
    {
        let mut b = Buf(Vec::new());
        put_boot_error(&mut b, FatError::NotEmpty.into());
        assert_eq!(String::from_utf8(b.0).unwrap(), "not empty");
    }
    for (e, want) in [
        (ImageError::Empty, "empty"),
        (ImageError::OutOfWindow, "loads outside 80700000-807FFFFF"),
    ] {
        let mut b = Buf(Vec::new());
        put_boot_error(&mut b, BootError::Image(e));
        assert_eq!(String::from_utf8(b.0).unwrap(), want);
    }
}
