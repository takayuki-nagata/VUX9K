// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! Host tests of fw_common::launch: the second stage's targets, staging and errors
//! (feature `fat-full`).

#![cfg(feature = "fat-full")]

extern crate std;
use std::string::String;
use std::vec::Vec;

use fw_common::fat::stage2::{BootError, Window};
use fw_common::fat::FatError;
use fw_common::fmt::Sink;
use fw_common::header::{ImageError, ImageHeader, MODE_HACK, MODE_RISCV};
use fw_common::launch::{
    plan, put_run_error, staging, Target, LOAD_WINDOW, SCRATCH, SCRATCH_BYTES,
};
use fw_common::map;

fn header(mode: u32, size: u32, load_addr: u32) -> ImageHeader {
    ImageHeader {
        mode,
        size,
        load_addr,
        entry_point: load_addr,
        crc32: 0,
        version: 1,
        name: [0; 32],
    }
}

fn window(w: Window) -> (u32, u32) {
    (w.base, w.len)
}

#[test]
fn targets_and_their_windows() {
    assert_eq!(window(Target::Psram.window()), (0x8000_0000, 0x70_0000));
    assert_eq!(window(Target::HackSeg.window()), (0, 0x1_0000));
    // Every target's window is inside the one files load with
    for t in [Target::Psram, Target::HackSeg] {
        let w = t.window();
        assert!(w.base >= LOAD_WINDOW.base && w.base + w.len <= LOAD_WINDOW.base + LOAD_WINDOW.len);
    }
    assert_eq!(window(LOAD_WINDOW), (0, map::STAGE2_BASE as u32));
    assert_eq!((SCRATCH, SCRATCH_BYTES), (0x8000_0000, 0x70_0000));
    // A Hack offset stages into Hack's instruction segment
    assert_eq!(SCRATCH as usize, map::HACK_I_BASE);
}

#[test]
fn plan_picks_the_target_from_mode_and_load_address() {
    use ImageError::*;
    const PS: u32 = 0x8000_0000;
    const S2: u32 = 0x8070_0000;
    for (h, want) in [
        (header(MODE_RISCV, 16, PS), Ok(Target::Psram)),
        (header(MODE_RISCV, 16, PS + 0x1000), Ok(Target::Psram)),
        (header(MODE_RISCV, 0x70_0000, PS), Ok(Target::Psram)),
        (header(MODE_RISCV, 4, S2 - 4), Ok(Target::Psram)),
        (header(MODE_RISCV, 8, S2 - 4), Err(OutOfWindow)),
        (header(MODE_RISCV, 16, S2), Err(OutOfWindow)),
        (header(MODE_RISCV, 16, PS - 16), Err(OutOfWindow)),
        (header(MODE_RISCV, 16, 0x2000_0000), Err(OutOfWindow)),
        // RV32 runs from the PSRAM only: in the ROM, at 0 or elsewhere, it is outside
        (header(MODE_RISCV, 16, 0), Err(OutOfWindow)),
        (header(MODE_RISCV, 0x3800, 0), Err(OutOfWindow)),
        (header(MODE_RISCV, 16, 0x100), Err(OutOfWindow)),
        // Hack: an offset in its 64 KB instruction segment, at a word
        (header(MODE_HACK, 16, 0), Ok(Target::HackSeg)),
        (header(MODE_HACK, 0x4002, 0), Ok(Target::HackSeg)),
        (header(MODE_HACK, 0x1_0000, 0), Ok(Target::HackSeg)),
        (header(MODE_HACK, 16, 0xFFF0), Ok(Target::HackSeg)),
        (header(MODE_HACK, 0x1_0002, 0), Err(OutOfWindow)),
        (header(MODE_HACK, 16, 0xFFF4), Err(OutOfWindow)),
        (header(MODE_HACK, 16, 0x1_0000), Err(OutOfWindow)),
        (header(MODE_HACK, 16, 2), Err(OutOfWindow)),
        (header(MODE_HACK, 16, PS), Err(OutOfWindow)),
        (header(2, 16, PS), Err(BadMode(2))),
        (header(MODE_RISCV, 0, PS), Err(Empty)),
        (header(MODE_HACK, 0, 0), Err(Empty)),
    ] {
        assert_eq!(plan(&h), want, "{h:?}");
    }
    let mut h = header(MODE_RISCV, 16, PS);
    h.entry_point = PS + 2;
    assert_eq!(plan(&h), Err(BadEntry(PS + 2)));
    // RV32 at 0 with the entry 0 the old I-RAM target wanted: out of the window
    h.load_addr = 0;
    h.entry_point = 0;
    assert_eq!(plan(&h), Err(OutOfWindow));
}

#[test]
fn staging_puts_hack_offsets_into_the_segment() {
    assert_eq!(staging(0), Some(SCRATCH));
    assert_eq!(staging(0x3E00), Some(SCRATCH + 0x3E00));
    assert_eq!(staging(0xFFFF), Some(SCRATCH + 0xFFFF));
    assert_eq!(staging(0x1_0000), None);
    assert_eq!(staging(0x2000_0000), None);
    assert_eq!(staging(0x7FFF_FFFF), None);
    assert_eq!(staging(0x8000_0000), Some(0x8000_0000));
    assert_eq!(staging(0x806F_FE00), Some(0x806F_FE00));
}

struct Buf(Vec<u8>);
impl Sink for Buf {
    fn put(&mut self, b: u8) {
        self.0.push(b);
    }
}

fn text(e: BootError) -> String {
    let mut b = Buf(Vec::new());
    put_run_error(&mut b, e);
    String::from_utf8(b.0).unwrap()
}

#[test]
fn run_errors() {
    assert_eq!(
        text(BootError::Image(ImageError::BadMode(7))),
        "not RV32 or Hack, mode 7"
    );
    assert_eq!(
        text(BootError::Image(ImageError::OutOfWindow)),
        "loads outside 80000000-806FFFFF (RV32), 0-FFFF (Hack, at a word)"
    );
    // The rest as the first stage words it
    assert_eq!(text(BootError::Crc), "CRC mismatch");
    assert_eq!(
        text(BootError::Image(ImageError::BadEntry(0x8000_0002))),
        "bad entry point 80000002"
    );
    assert_eq!(text(BootError::Fat(FatError::NotFound)), "not found");
}

/// The way the second stage runs a file: `load` (the header within LOAD_WINDOW, `plan`,
/// the payload through `staging`, the CRC).
mod run {
    use super::*;
    use fw_common::crc32_update;
    use fw_common::fat::{BlockDevice, Volume, SECTOR};
    use fw_common::launch::{load, SCRATCH_BYTES};
    use std::collections::BTreeMap;

    /// A card image, and a sector whose reads fail.
    struct Disk(Vec<u8>, Option<u32>);
    impl BlockDevice for Disk {
        fn read(&mut self, lba: u32, buf: &mut [u8; SECTOR]) -> bool {
            if Some(lba) == self.1 {
                return false;
            }
            let at = lba as usize * SECTOR;
            buf.copy_from_slice(&self.0[at..at + SECTOR]);
            true
        }
        fn write(&mut self, lba: u32, buf: &[u8; SECTOR]) -> bool {
            let at = lba as usize * SECTOR;
            self.0[at..at + SECTOR].copy_from_slice(buf);
            true
        }
    }

    fn le32(b: &[u8], at: usize) -> u32 {
        u32::from_le_bytes(b[at..at + 4].try_into().unwrap())
    }

    /// tests/fat_images/vuxtool_s1 (a vux_tool card).
    fn card() -> Volume<Disk> {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fat_images/vuxtool_s1.sparse");
        let raw = std::fs::read(path).unwrap();
        let mut img = std::vec![0u8; le32(&raw, 8) as usize * SECTOR];
        for rec in raw[12..].chunks(4 + SECTOR) {
            let at = le32(rec, 0) as usize * SECTOR;
            img[at..at + SECTOR].copy_from_slice(&rec[4..]);
        }
        Volume::mount(Disk(img, None)).unwrap()
    }

    fn v4(mode: u32, load: u32, payload: &[u8]) -> Vec<u8> {
        let mut f = std::vec![0u8; 64];
        let mut put = |at: usize, v: u32| f[at..at + 4].copy_from_slice(&v.to_le_bytes());
        put(0, 0x5655_5839);
        put(4, 4);
        put(8, mode);
        put(12, payload.len() as u32);
        put(16, load);
        put(20, load);
        put(24, crc32_update(payload, 0xFFFF_FFFF) ^ 0xFFFF_FFFF);
        put(28, 1);
        f.extend_from_slice(payload);
        f
    }

    /// What the PSRAM held before a load.
    const FILL: u8 = 0xA5;

    /// Run `file` from the card: the target or error, and the bytes of the PSRAM that
    /// changed (address -> byte; the payloads below hold no FILL).
    fn run(file: &[u8]) -> (Result<Target, BootError>, BTreeMap<u32, u8>) {
        let mut vol = card();
        let root = vol.root();
        let entry = vol.create(&root, b"APP     BIN", file).unwrap();
        let mut psram = std::vec![FILL; SCRATCH_BYTES as usize];
        let r = load(&mut vol, &entry, &mut psram).map(|(h, t)| {
            assert_eq!(h.size as usize, file.len() - 64);
            t
        });
        let mem = psram
            .iter()
            .enumerate()
            .filter(|&(_, &b)| b != FILL)
            .map(|(i, &b)| (SCRATCH + i as u32, b))
            .collect();
        (r, mem)
    }

    #[test]
    fn files_stage_where_their_target_wants_them() {
        let payload: Vec<u8> = (0..1500u32)
            .map(|i| match (i * 7) as u8 {
                FILL => 0,
                b => b,
            })
            .collect();
        let at = |mem: &BTreeMap<u32, u8>| *mem.keys().next().unwrap();
        let bytes = |mem: &BTreeMap<u32, u8>| mem.values().copied().collect::<Vec<u8>>();
        let (r, mem) = run(&v4(MODE_RISCV, 0x8001_0000, &payload));
        assert_eq!(r, Ok(Target::Psram));
        assert_eq!(at(&mem), 0x8001_0000);
        assert_eq!(*mem.keys().last().unwrap(), 0x8001_0000 + 1499);
        assert_eq!(bytes(&mem), payload);
        // Hack: in its segment, where it runs
        let (r, mem) = run(&v4(MODE_HACK, 0, &payload));
        assert_eq!(r, Ok(Target::HackSeg));
        assert_eq!(at(&mem), map::HACK_I_BASE as u32);
        assert_eq!(bytes(&mem), payload);
        let (r, mem) = run(&v4(MODE_HACK, 0x100, &payload));
        assert_eq!(r, Ok(Target::HackSeg));
        assert_eq!(at(&mem), map::HACK_I_BASE as u32 + 0x100);
        let (r, mem) = run(&v4(MODE_HACK, 0xC000, &payload));
        assert_eq!(r, Ok(Target::HackSeg));
        assert_eq!(at(&mem), map::HACK_I_BASE as u32 + 0xC000);
        // Refused before anything loads: RV32 at 0 (the first stage in the ROM stays), out
        // of every window, past Hack's segment, the second stage's window (not a target's)
        for (mode, load_addr) in [
            (MODE_RISCV, 0),
            (MODE_RISCV, 0x2000_0000),
            (MODE_HACK, 0xFF00),
            (MODE_RISCV, 0x8070_0000),
        ] {
            let (r, mem) = run(&v4(mode, load_addr, &payload));
            assert_eq!(r, Err(BootError::Image(ImageError::OutOfWindow)));
            assert!(mem.is_empty(), "{load_addr:#x}");
        }
        let mut bad = v4(MODE_RISCV, 0, &payload);
        bad[100] ^= 1;
        assert_eq!(
            run(&bad),
            (
                Err(BootError::Image(ImageError::OutOfWindow)),
                BTreeMap::new()
            )
        );
        // A broken payload loads into its window, and is refused there
        let mut bad = v4(MODE_RISCV, 0x8001_0000, &payload);
        bad[100] ^= 1;
        let (r, mem) = run(&bad);
        assert_eq!(r, Err(BootError::Crc));
        assert!(mem
            .keys()
            .all(|&a| (0x8001_0000..0x8001_0000 + 1500).contains(&a)));
    }

    #[test]
    fn headers_refused_before_the_payload() {
        let payload = [1u8, 2, 3, 4];
        let good = v4(MODE_RISCV, 0x8000_0000, &payload);
        // Shorter than a header, not v4, a size that isn't the file's, the header's own
        // checks (mode, size 0, entry)
        let mut wrong_size = good.clone();
        wrong_size.push(0);
        let mut not_v4 = good.clone();
        not_v4[0] ^= 1;
        let mode = v4(2, 0x8000_0000, &payload);
        for (file, want) in [
            (good[..63].to_vec(), BootError::NotV4),
            (Vec::new(), BootError::NotV4),
            (not_v4, BootError::NotV4),
            (wrong_size, BootError::SizeMismatch),
            (mode, BootError::Image(ImageError::BadMode(2))),
            (
                v4(MODE_RISCV, 0x8000_0000, &[]),
                BootError::Image(ImageError::Empty),
            ),
        ] {
            let (r, mem) = run(&file);
            assert_eq!(r, Err(want));
            assert!(mem.is_empty());
        }
        assert_eq!(run(&good).0, Ok(Target::Psram));
    }

    #[test]
    fn card_errors_while_loading() {
        let payload: Vec<u8> = (0..3000u32).map(|i| (i % 251) as u8).collect();
        let file = v4(MODE_RISCV, 0x8000_0000, &payload);
        let mut vol = card();
        let root = vol.root();
        let entry = vol.create(&root, b"APP     BIN", &file).unwrap();
        let mut psram = std::vec![0u8; SCRATCH_BYTES as usize];
        // The file's first sector (its header), and one of its payload's sectors after it
        let first = {
            let mut lba = None;
            for l in 0..vol.device().0.len() as u32 / SECTOR as u32 {
                let at = l as usize * SECTOR;
                if vol.device().0[at..at + 64] == file[..64] {
                    lba = Some(l);
                }
            }
            lba.unwrap()
        };
        assert_eq!(
            load(&mut vol, &entry, &mut psram).map(|r| r.1),
            Ok(Target::Psram)
        );
        for (lba, what) in [(first, "header"), (first + 2, "payload")] {
            vol.device().1 = Some(lba);
            assert_eq!(
                load(&mut vol, &entry, &mut psram).map(|r| r.1),
                Err(BootError::Fat(FatError::Io(lba))),
                "{what}"
            );
        }
    }
}
