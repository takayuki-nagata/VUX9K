// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! Host tests of fw_common::linux: the riscv Image and device tree headers the second
//! stage's `linux` checks, where it loads them, and what it refuses before writing
//! (feature `fat-full`).

#![cfg(feature = "fat-full")]

extern crate std;
use std::string::String;
use std::vec::Vec;

use fw_common::fat::{BlockDevice, FatError, Volume, SECTOR};
use fw_common::fmt::Sink;
use fw_common::linux::{
    check_fdt, check_image, load, put_linux_error, LinuxError, FDT_HEADER_LEN, FDT_MAGIC,
    IMAGE_HEADER_LEN,
};
use fw_common::map;

/// An Image header as head.S writes it: a jump over it, text_offset, image_size, magic2.
fn image_head(text_offset: u64, image_size: u64) -> [u8; IMAGE_HEADER_LEN] {
    let mut h = [0u8; IMAGE_HEADER_LEN];
    h[0..4].copy_from_slice(&0x0400_006Fu32.to_le_bytes()); // j +64
    h[8..16].copy_from_slice(&text_offset.to_le_bytes());
    h[16..24].copy_from_slice(&image_size.to_le_bytes());
    h[0x30..0x35].copy_from_slice(b"RISCV");
    h[0x38..0x3C].copy_from_slice(b"RSC\x05");
    h
}

fn fdt_head(totalsize: u32) -> [u8; 8] {
    let mut h = [0u8; 8];
    h[0..4].copy_from_slice(&FDT_MAGIC.to_be_bytes());
    h[4..8].copy_from_slice(&totalsize.to_be_bytes());
    h
}

#[test]
fn the_limits_are_the_maps() {
    assert_eq!(map::LINUX_IMAGE_BASE, 0x8000_0000);
    assert_eq!(map::LINUX_IMAGE_MAX, 0x70_0000);
    assert_eq!(
        map::LINUX_IMAGE_BASE + map::LINUX_IMAGE_MAX,
        map::STAGE2_BASE
    );
    assert_eq!(map::LINUX_DTB_ADDR, 0x807F_0000);
    // Up to the second stage's stack, its window's top 8 KB
    assert_eq!(map::LINUX_DTB_ADDR + map::LINUX_DTB_MAX, 0x807F_E000);
    assert_eq!(map::LINUX_DTB_MAX, 56 * 1024);
}

#[test]
fn image_headers() {
    use LinuxError::*;
    // image_size may reach the device tree's address, not past it
    let room = (map::LINUX_DTB_ADDR - map::LINUX_IMAGE_BASE) as u64;
    let max = map::LINUX_IMAGE_MAX as u32;
    for (head, size, want) in [
        (image_head(0, 0x33_0A68), 3_244_488, Ok(())),
        (image_head(0, 1), 64, Ok(())),
        (image_head(0, room), max, Ok(())),
        (image_head(0, room + 1), 64, Err(Overlap)),
        // The u64s whole: a high word that isn't 0
        (image_head(0, 1 << 32), 64, Err(Overlap)),
        (image_head(0, (1 << 32) | 1), 64, Err(Overlap)),
        (image_head(1 << 32, 1), 64, Err(TextOffset)),
        (image_head(0x40_0000, 1), 64, Err(TextOffset)),
        (image_head(1, 1), 64, Err(TextOffset)),
        (image_head(0, 1), max + 1, Err(ImageTooBig)),
        (image_head(0, 0), 64, Err(NotImage)),
        // A text_offset that isn't 0 is told before the file's size
        (image_head(1, 1), max + 1, Err(TextOffset)),
        (image_head(0, room + 1), max + 1, Err(ImageTooBig)),
    ] {
        assert_eq!(check_image(&head, size), want, "{:x?}", &head[8..24]);
    }
    // magic2, byte by byte
    for at in 0x38..0x3C {
        let mut head = image_head(0, 1);
        head[at] ^= 0x20;
        assert_eq!(check_image(&head, 64), Err(NotImage), "{at:#x}");
    }
    // "RISCV" at 0x30 is deprecated: magic2 decides
    let mut head = image_head(0, 1);
    head[0x30..0x35].fill(0);
    assert_eq!(check_image(&head, 64), Ok(()));
}

#[test]
fn device_tree_headers() {
    use LinuxError::*;
    let max = map::LINUX_DTB_MAX as u32;
    for (head, size, want) in [
        (fdt_head(1073), 1073, Ok(())),
        (fdt_head(FDT_HEADER_LEN as u32), 40, Ok(())),
        (fdt_head(max), max, Ok(())),
        (fdt_head(max + 1), max + 1, Err(FdtTooBig)),
        (fdt_head(39), 39, Err(NotFdt)),
        (fdt_head(1073), 1074, Err(FdtSize)),
        (fdt_head(1074), 1073, Err(FdtSize)),
        (fdt_head(max + 1), max, Err(FdtSize)),
    ] {
        assert_eq!(check_fdt(&head, size), want, "{size}");
    }
    for at in 0..4 {
        let mut head = fdt_head(64);
        head[at] ^= 1;
        assert_eq!(check_fdt(&head, 64), Err(NotFdt), "{at}");
    }
}

struct Buf(String);
impl Sink for Buf {
    fn put(&mut self, b: u8) {
        self.0.push(b as char);
    }
}

#[test]
fn error_texts() {
    use LinuxError::*;
    let text = |e| {
        let mut b = Buf(String::new());
        put_linux_error(&mut b, e);
        b.0
    };
    assert_eq!(text(NotImage), "Image: not a RISC-V Linux Image");
    assert_eq!(
        text(TextOffset),
        "Image: text_offset not 0 (it runs at 80000000)"
    );
    assert_eq!(
        text(ImageTooBig),
        "Image: over 7 MB (it must end below the second stage)"
    );
    assert_eq!(
        text(Overlap),
        "Image: image_size reaches the device tree at 807F0000"
    );
    assert_eq!(text(NotFdt), "DTB: not a flattened device tree");
    assert_eq!(text(FdtSize), "DTB: totalsize is not the file's size");
    assert_eq!(text(FdtTooBig), "DTB: over 56 KB");
}

/// `load` from a card into the two windows.
mod from_a_card {
    use super::*;

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

    #[derive(Debug, PartialEq)]
    enum Error {
        Fat(FatError),
        Linux(LinuxError),
    }
    impl From<FatError> for Error {
        fn from(e: FatError) -> Self {
            Error::Fat(e)
        }
    }
    impl From<LinuxError> for Error {
        fn from(e: LinuxError) -> Self {
            Error::Linux(e)
        }
    }

    /// What the memory held before a load.
    const FILL: u8 = 0xA5;

    /// A kernel of `len` bytes (its header, then bytes that aren't FILL).
    fn kernel(len: usize, image_size: u64) -> Vec<u8> {
        let mut k = image_head(0, image_size).to_vec();
        k.extend((64..len).map(|i| (i % 251) as u8));
        k.truncate(len);
        k
    }

    fn dtb(len: usize) -> Vec<u8> {
        let mut d = fdt_head(len as u32).to_vec();
        d.extend((8..len).map(|i| (i % 7) as u8));
        d.truncate(len);
        d
    }

    /// Load `image` and `dtb` from the card (`bad`: a sector of the image that fails); the
    /// result and the two windows' bytes that changed (from their starts).
    fn run(image: &[u8], dtb: &[u8], bad: Option<usize>) -> (Result<(), Error>, Vec<u8>, Vec<u8>) {
        let mut vol = card();
        let root = vol.root();
        let i = vol.create(&root, b"IMAGE      ", image).unwrap();
        let d = vol.create(&root, b"VUX9K   DTB", dtb).unwrap();
        if let Some(n) = bad {
            // The file's clusters are consecutive on this fresh card
            let lba = (0..vol.device().0.len() / SECTOR)
                .find(|&l| {
                    image.len() >= SECTOR
                        && vol.device().0[l * SECTOR..][..SECTOR] == image[..SECTOR]
                })
                .unwrap();
            vol.device().1 = Some((lba + n) as u32);
        }
        let mut kmem = std::vec![FILL; map::LINUX_IMAGE_MAX];
        let mut fmem = std::vec![FILL; map::LINUX_DTB_MAX];
        let r = load::<_, Error>(&mut vol, &i, &d, &mut kmem, &mut fmem);
        let used = |m: &[u8]| {
            let end = m.iter().rposition(|&b| b != FILL).map_or(0, |e| e + 1);
            m[..end].to_vec()
        };
        (r, used(&kmem), used(&fmem))
    }

    #[test]
    fn both_files_land_in_place() {
        let (k, d) = (kernel(5000, 0x2_0000), dtb(1073));
        let (r, kmem, fmem) = run(&k, &d, None);
        assert_eq!(r, Ok(()));
        assert_eq!(kmem, k);
        assert_eq!(fmem, d);
        // The smallest of both
        let (k, d) = (kernel(64, 64), dtb(40));
        assert_eq!(run(&k, &d, None), (Ok(()), k, d));
    }

    #[test]
    fn refusals_write_nothing() {
        use LinuxError::*;
        let (k, d) = (kernel(3000, 0x1_0000), dtb(600));
        let room = (map::LINUX_DTB_ADDR - map::LINUX_IMAGE_BASE) as u64;
        let mut no_magic2 = k.clone();
        no_magic2[0x38] = 0;
        let mut offset = k.clone();
        offset[8] = 1;
        let mut not_fdt = d.clone();
        not_fdt[0] = 0;
        let mut short_total = d.clone();
        short_total[7] ^= 1;
        for (image, dtb, want) in [
            (k[..63].to_vec(), d.clone(), Error::Linux(NotImage)),
            (Vec::new(), d.clone(), Error::Linux(NotImage)),
            (no_magic2, d.clone(), Error::Linux(NotImage)),
            (offset, d.clone(), Error::Linux(TextOffset)),
            (kernel(3000, room + 1), d.clone(), Error::Linux(Overlap)),
            (k.clone(), d[..39].to_vec(), Error::Linux(NotFdt)),
            (k.clone(), Vec::new(), Error::Linux(NotFdt)),
            (k.clone(), not_fdt, Error::Linux(NotFdt)),
            (k.clone(), short_total, Error::Linux(FdtSize)),
            (
                k.clone(),
                dtb(map::LINUX_DTB_MAX + 1),
                Error::Linux(FdtTooBig),
            ),
        ] {
            let (r, kmem, fmem) = run(&image, &dtb, None);
            assert!(kmem.is_empty() && fmem.is_empty(), "{want:?}");
            assert_eq!(r, Err(want));
        }
    }

    #[test]
    fn a_card_error_is_the_cards() {
        // In the Image's header: nothing written; past it: the device tree is in
        let (k, d) = (kernel(5000, 0x2_0000), dtb(700));
        let (r, kmem, fmem) = run(&k, &d, Some(0));
        assert!(matches!(r, Err(Error::Fat(FatError::Io(_)))), "{r:?}");
        assert!(kmem.is_empty() && fmem.is_empty());
        let (r, _, fmem) = run(&k, &d, Some(3));
        assert!(matches!(r, Err(Error::Fat(FatError::Io(_)))), "{r:?}");
        assert_eq!(fmem, d);
    }
}
