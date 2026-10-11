// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! Host tests of fw_common::fat on the card images in tests/fat_images/ (made by
//! mkfs.fat and mtools: sim/emu/test_fat_images.py), and on copies of them with one
//! field broken.

extern crate std;
use std::string::String;
use std::vec::Vec;

use fw_common::crc32_update;
use fw_common::fat::{to_short_name, BlockDevice, DirEntry, FatError, Volume, SECTOR};
use fw_common::header::{ImageHeader, HEADER_LEN};

/// A card image in memory. `fail_read`: the sector whose read fails. `far`: an LBA
/// past the image that reads as the image's boot sector (LBA 2048).
pub struct MemDisk {
    pub img: Vec<u8>,
    pub fail_read: Option<u32>,
    pub far: Option<u32>,
}

impl BlockDevice for MemDisk {
    fn read(&mut self, lba: u32, buf: &mut [u8; SECTOR]) -> bool {
        let lba = if Some(lba) == self.far { 2048 } else { lba };
        let at = lba as usize * SECTOR;
        if Some(lba) == self.fail_read || at + SECTOR > self.img.len() {
            return false;
        }
        buf.copy_from_slice(&self.img[at..at + SECTOR]);
        true
    }
    fn write(&mut self, lba: u32, buf: &[u8; SECTOR]) -> bool {
        let at = lba as usize * SECTOR;
        self.img[at..at + SECTOR].copy_from_slice(buf);
        true
    }
}

fn le32(b: &[u8], at: usize) -> u32 {
    u32::from_le_bytes(b[at..at + 4].try_into().unwrap())
}

/// tests/fat_images/<name>.sparse as a whole image.
fn image(name: &str) -> Vec<u8> {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fat_images")
        .join(std::format!("{name}.sparse"));
    let raw = std::fs::read(&path).unwrap();
    assert_eq!(&raw[..8], b"VUXSPRS1", "{}", path.display());
    let mut img = std::vec![0u8; le32(&raw, 8) as usize * SECTOR];
    for rec in raw[12..].chunks(4 + SECTOR) {
        let at = le32(rec, 0) as usize * SECTOR;
        img[at..at + SECTOR].copy_from_slice(&rec[4..]);
    }
    img
}

/// tests/fat_images/<name>.files: (path, size, crc32).
fn manifest(name: &str) -> Vec<(String, u32, u32)> {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fat_images")
        .join(std::format!("{name}.files"));
    std::fs::read_to_string(path)
        .unwrap()
        .lines()
        .map(|l| {
            let mut f = l.rsplitn(3, ' ');
            let crc = u32::from_str_radix(f.next().unwrap(), 16).unwrap();
            let size = f.next().unwrap().parse().unwrap();
            (f.next().unwrap().into(), size, crc)
        })
        .collect()
}

const IMAGES: [&str; 2] = ["mkfs_s1", "mkfs_s8_1fat"];

fn mount(img: Vec<u8>) -> Result<Volume<MemDisk>, FatError> {
    Volume::mount(MemDisk {
        img,
        fail_read: None,
        far: None,
    })
}

/// Read a file twice (sector by sector, and into a buffer); its bytes.
fn read_file(vol: &mut Volume<MemDisk>, path: &str) -> Result<Vec<u8>, FatError> {
    let f = vol.open(path)?;
    let mut streamed = Vec::new();
    vol.for_each_sector(&f, &mut |d| streamed.extend_from_slice(d))?;
    let mut buf = std::vec![0xAAu8; f.size as usize + 3];
    assert_eq!(vol.read_to(&f, &mut buf)?, f.size as usize);
    assert_eq!(&buf[..f.size as usize], &streamed[..]);
    Ok(streamed)
}

#[test]
fn reads_every_file_the_tools_wrote() {
    for name in IMAGES {
        let mut vol = mount(image(name)).unwrap();
        let files = manifest(name);
        assert!(!files.is_empty());
        for (path, size, crc) in files {
            let data = read_file(&mut vol, &path).unwrap();
            assert_eq!(data.len() as u32, size, "{name} {path}");
            assert_eq!(
                crc32_update(&data, 0xFFFF_FFFF) ^ 0xFFFF_FFFF,
                crc,
                "{name} {path}"
            );
        }
    }
}

#[test]
fn second_stage_is_a_v4_image() {
    for name in IMAGES {
        let mut vol = mount(image(name)).unwrap();
        let data = read_file(&mut vol, "VUX9K/STAGE2.BIN").unwrap();
        let h = ImageHeader::parse(&data).unwrap();
        assert!(h.crc_matches(&data[HEADER_LEN..]), "{name}");
        assert_eq!(h.check(0x8000_0000, 0x80_0000), Ok(()));
    }
}

#[test]
fn geometry() {
    let vol = mount(image("mkfs_s1")).unwrap();
    assert_eq!((vol.partition_lba(), vol.cluster_bytes()), (2048, 512));
    let vol = mount(image("mkfs_s8_1fat")).unwrap();
    assert_eq!(vol.cluster_bytes(), 4096);
}

#[test]
fn paths_ignore_case_and_take_either_separator() {
    let mut vol = mount(image("mkfs_s1")).unwrap();
    let want = vol.open("VUX9K/SUB/X.TXT").unwrap();
    for p in [
        "/vux9k/sub/x.txt",
        "\\VUX9K\\Sub\\x.TXT",
        "VUX9K//SUB/X.TXT",
    ] {
        assert_eq!(vol.open(p).unwrap(), want, "{p}");
    }
    assert_eq!(vol.open("").unwrap(), vol.root());
    assert_eq!(read_file(&mut vol, "readme.txt").unwrap(), b"hello\n");
}

fn names(vol: &mut Volume<MemDisk>, dir: &DirEntry) -> Vec<String> {
    let mut out = Vec::new();
    let found = vol
        .find_entry(dir, &mut |e| {
            let mut buf = [0u8; 12];
            let n = e.display_name(&mut buf);
            out.push(String::from_utf8(buf[..n].to_vec()).unwrap());
            false
        })
        .unwrap();
    assert_eq!(found, None);
    out
}

#[test]
fn lists_directories_without_long_names_labels_or_deleted_entries() {
    let mut vol = mount(image("mkfs_s1")).unwrap();
    let root = vol.root();
    // The volume label and the deleted A.BIN aren't listed
    // FRAG.BIN took the deleted A.BIN's entry
    assert_eq!(
        names(&mut vol, &root),
        ["VUX9K", "FRAG.BIN", "B.BIN", "README.TXT"]
    );
    let dir = vol.open("VUX9K").unwrap();
    assert!(dir.is_dir());
    let listed = names(&mut vol, &dir);
    // More entries than one 512-byte cluster holds: the directory's chain was followed
    assert!(listed.len() > 16);
    assert_eq!(
        &listed[..5],
        [".", "..", "STAGE2.BIN", "STAGE2.OLD", "LONGFI~1.TXT"]
    );
    assert_eq!(listed.last().unwrap(), "SUB");
    let stop = vol
        .find_entry(&dir, &mut |e| e.name == *b"F03     DAT")
        .unwrap();
    assert_eq!(stop.unwrap().size, 1);
}

#[test]
fn short_names() {
    assert_eq!(to_short_name("stage2.bin"), Some(*b"STAGE2  BIN"));
    assert_eq!(to_short_name("VUX9K"), Some(*b"VUX9K      "));
    assert_eq!(to_short_name("A~1.b"), Some(*b"A~1     B  "));
    assert_eq!(to_short_name("12345678.123"), Some(*b"12345678123"));
    for bad in [
        "",
        ".",
        "..",
        ".BIN",
        "A.",
        "123456789",
        "A.1234",
        "A.B.C",
        "A B",
        "A*",
        "Ä",
    ] {
        assert_eq!(to_short_name(bad), None, "{bad}");
    }
    let mut buf = [0u8; 12];
    let e = DirEntryName(*b"READ    ME ");
    let n = e.show(&mut buf);
    assert_eq!(&buf[..n], b"READ.ME");
    let e = DirEntryName(*b"NOEXT      ");
    let n = e.show(&mut buf);
    assert_eq!(&buf[..n], b"NOEXT");
}

/// display_name of an entry with this name (found on an image, renamed).
struct DirEntryName([u8; 11]);
impl DirEntryName {
    fn show(&self, out: &mut [u8; 12]) -> usize {
        let mut vol = mount(image("mkfs_s1")).unwrap();
        let mut e = vol.open("B.BIN").unwrap();
        e.name = self.0;
        e.display_name(out)
    }
}

#[test]
fn errors_on_paths_and_types() {
    let mut vol = mount(image("mkfs_s1")).unwrap();
    assert_eq!(vol.open("VUX9K/NOPE.BIN"), Err(FatError::NotFound));
    assert_eq!(vol.open("VUX9K/STAGE2.BIN/X"), Err(FatError::WrongType));
    assert_eq!(vol.open("VUX9K/Long File Name.txt"), Err(FatError::BadName));
    let dir = vol.open("VUX9K").unwrap();
    assert_eq!(
        vol.for_each_sector(&dir, &mut |_| ()),
        Err(FatError::WrongType)
    );
    let file = vol.open("B.BIN").unwrap();
    assert_eq!(
        vol.find_entry(&file, &mut |_| true),
        Err(FatError::WrongType)
    );
    let mut small = [0u8; 599];
    assert_eq!(vol.read_to(&file, &mut small), Err(FatError::TooBig));
}

// ----- Broken images -------------------------------------------------------------

const PART: usize = 2048 * SECTOR;

fn patch(img: &mut [u8], at: usize, bytes: &[u8]) {
    img[at..at + bytes.len()].copy_from_slice(bytes);
}

/// Byte offset of the directory entry named `name` (the first one in the data area that
/// is neither a long name nor the volume label, which share the volume's name).
fn entry_at(img: &[u8], name: &[u8; 11]) -> usize {
    (PART..img.len())
        .step_by(32)
        .find(|&at| &img[at..at + 11] == name && img[at + 11] & 0x08 == 0)
        .unwrap()
}

/// Byte offset of cluster `c`'s entry in the first FAT.
fn fat_at(img: &[u8], c: u32) -> usize {
    let reserved = u16::from_le_bytes([img[PART + 14], img[PART + 15]]) as usize;
    PART + reserved * SECTOR + 4 * c as usize
}

fn first_cluster(img: &[u8], name: &[u8; 11]) -> u32 {
    let e = entry_at(img, name);
    (u16::from_le_bytes([img[e + 20], img[e + 21]]) as u32) << 16
        | u16::from_le_bytes([img[e + 26], img[e + 27]]) as u32
}

#[test]
fn mount_rejects_what_is_not_fat32() {
    let cases: [(fn(&mut Vec<u8>), FatError); 14] = [
        (|i| patch(i, 510, &[0, 0]), FatError::NoSignature),
        (|i| patch(i, PART + 510, &[0, 0]), FatError::NoSignature),
        (|i| patch(i, 446 + 4, &[0]), FatError::NoPartition),
        (|i| patch(i, 446 + 4, &[0x07]), FatError::NotFat32),
        (|i| patch(i, 446 + 4, &[0x0E]), FatError::NotFat32),
        (|i| patch(i, PART + 22, &[1, 0]), FatError::NotFat32),
        (|i| patch(i, PART + 17, &[0, 2]), FatError::NotFat32),
        (|i| patch(i, PART + 11, &[0, 4]), FatError::BadBpb),
        (|i| patch(i, PART + 13, &[3]), FatError::BadBpb),
        (|i| patch(i, PART + 14, &[0, 0]), FatError::BadBpb),
        (|i| patch(i, PART + 16, &[3]), FatError::BadBpb),
        (|i| patch(i, PART + 36, &[0, 0, 0, 0]), FatError::BadBpb),
        // Total sectors below the FATs' end, and FATs too short for the clusters
        (|i| patch(i, PART + 19, &[16, 0]), FatError::BadBpb),
        (|i| patch(i, PART + 36, &[1, 0, 0, 0]), FatError::BadBpb),
    ];
    for (n, (break_it, want)) in cases.into_iter().enumerate() {
        let mut img = image("mkfs_s1");
        break_it(&mut img);
        assert_eq!(mount(img).err(), Some(want), "case {n}");
    }
    // Root cluster outside the volume, and a partition that ends past LBA 2^32
    for (at, bytes) in [(PART + 44, [1, 0, 0, 0]), (446 + 8, [0, 0xF0, 0xFF, 0xFF])] {
        let mut img = image("mkfs_s1");
        patch(&mut img, at, &bytes);
        if at == 446 + 8 {
            // The entry now points near the end of the 32-bit LBAs, where the disk shows
            // the boot sector again: the partition would end past LBA 2^32
            let disk = MemDisk {
                img,
                fail_read: None,
                far: Some(0xFFFF_F000),
            };
            assert_eq!(
                Volume::mount(disk).err(),
                Some(FatError::BadBpb),
                "past 2^32"
            );
        } else {
            assert_eq!(mount(img).err(), Some(FatError::BadBpb), "root");
        }
    }
}

#[test]
fn total_sectors_from_either_field() {
    // TotSec32 instead of TotSec16 (which mkfs.fat uses when the count fits)
    let mut img = image("mkfs_s1");
    let total = u16::from_le_bytes([img[PART + 19], img[PART + 20]]) as u32;
    patch(&mut img, PART + 19, &[0, 0]);
    patch(&mut img, PART + 32, &total.to_le_bytes());
    let mut vol = mount(img).unwrap();
    assert_eq!(read_file(&mut vol, "readme.txt").unwrap(), b"hello\n");
}

#[test]
fn card_errors_name_the_sector() {
    for lba in [0, 2048] {
        let disk = MemDisk {
            img: image("mkfs_s1"),
            fail_read: Some(lba),
            far: None,
        };
        assert_eq!(Volume::mount(disk).err(), Some(FatError::Io(lba)));
    }
    let mut vol = mount(image("mkfs_s1")).unwrap();
    let f = vol.open("FRAG.BIN").unwrap();
    // The image's first data sector, then a FAT sector
    let data_lba = {
        let img = &vol.device().img;
        let reserved = u16::from_le_bytes([img[PART + 14], img[PART + 15]]) as u32;
        2048 + reserved + 2 * le32(img, PART + 36)
    };
    for lba in [data_lba + f.cluster - 2, 2048 + 32] {
        vol.device().fail_read = Some(lba);
        assert_eq!(read_file(&mut vol, "FRAG.BIN"), Err(FatError::Io(lba)));
    }
}

#[test]
fn broken_chains_are_reported() {
    let name = b"FRAG    BIN";
    let img = image("mkfs_s1");
    let c = first_cluster(&img, name);
    let fat = fat_at(&img, c);
    let cases: [(usize, u32, FatError); 4] = [
        // next cluster 1 (reserved), free, past the volume: the chain leaves it
        (fat, 1, FatError::BadChain(c)),
        (fat, 0, FatError::BadChain(c)),
        (fat, 0x0FFF_FFF0, FatError::BadChain(c)),
        // end of chain after the first cluster: too short for the size
        (fat, 0x0FFF_FFFF, FatError::BadChain(c)),
    ];
    for (at, value, want) in cases {
        let mut img = image("mkfs_s1");
        patch(&mut img, at, &value.to_le_bytes());
        let mut vol = mount(img).unwrap();
        assert_eq!(read_file(&mut vol, "FRAG.BIN"), Err(want), "{value:#x}");
    }

    // A size smaller than the chain: the chain runs on past it
    let mut img = image("mkfs_s1");
    let e = entry_at(&img, name);
    patch(&mut img, e + 28, &512u32.to_le_bytes());
    let mut vol = mount(img).unwrap();
    assert_eq!(read_file(&mut vol, "FRAG.BIN"), Err(FatError::BadChain(c)));

    // A loop: the last cluster points back to the first. The size runs out first.
    let mut img = image("mkfs_s1");
    let mut last = c;
    loop {
        let next = le32(&img, fat_at(&img, last)) & 0x0FFF_FFFF;
        if next >= 0x0FFF_FFF8 {
            break;
        }
        last = next;
    }
    let at = fat_at(&img, last);
    patch(&mut img, at, &c.to_le_bytes());
    let mut vol = mount(img).unwrap();
    assert_eq!(
        read_file(&mut vol, "FRAG.BIN"),
        Err(FatError::BadChain(last))
    );

    // Empty file with a cluster; file whose first cluster is outside the volume
    for (size, cluster, want) in [
        (0u32, c, FatError::BadChain(c)),
        (600, 1, FatError::BadChain(1)),
    ] {
        let mut img = image("mkfs_s1");
        let e = entry_at(&img, name);
        patch(&mut img, e + 28, &size.to_le_bytes());
        patch(&mut img, e + 20, &((cluster >> 16) as u16).to_le_bytes());
        patch(&mut img, e + 26, &(cluster as u16).to_le_bytes());
        let mut vol = mount(img).unwrap();
        assert_eq!(read_file(&mut vol, "FRAG.BIN"), Err(want));
    }
}

#[test]
fn broken_directory_chains_are_reported() {
    // VUX9K's chain loops back on itself: a lookup that finds nothing stops
    let mut img = image("mkfs_s1");
    let d = first_cluster(&img, b"VUX9K      ");
    let mut last = d;
    loop {
        let next = le32(&img, fat_at(&img, last)) & 0x0FFF_FFFF;
        if next >= 0x0FFF_FFF8 {
            break;
        }
        last = next;
    }
    // Make the directory's end marker unreachable (no free entry before the loop closes)
    let mut vol_img = img.clone();
    patch(&mut vol_img, fat_at(&img, last), &d.to_le_bytes());
    let data_start = {
        let reserved = u16::from_le_bytes([img[PART + 14], img[PART + 15]]) as usize;
        PART + (reserved + 2 * le32(&img, PART + 36) as usize) * SECTOR
    };
    let last_dir = data_start + (last as usize - 2) * SECTOR;
    for at in (last_dir..last_dir + SECTOR).step_by(32) {
        if vol_img[at] == 0 {
            vol_img[at] = 0xE5;
        }
    }
    let mut vol = mount(vol_img).unwrap();
    assert!(matches!(
        vol.open("VUX9K/NOPE.BIN"),
        Err(FatError::BadChain(_))
    ));

    // A directory chain that leaves the volume
    let at = fat_at(&img, d);
    patch(&mut img, at, &1u32.to_le_bytes());
    let mut vol = mount(img).unwrap();
    assert_eq!(vol.open("VUX9K/SUB"), Err(FatError::BadChain(d)));
}

#[test]
fn directories_without_clusters_or_end_marker() {
    // A directory entry whose first cluster is 0 (outside the volume)
    let mut img = image("mkfs_s1");
    let e = entry_at(&img, b"VUX9K      ");
    patch(&mut img, e + 20, &[0, 0]);
    patch(&mut img, e + 26, &[0, 0]);
    let mut vol = mount(img).unwrap();
    assert_eq!(vol.open("VUX9K/STAGE2.BIN"), Err(FatError::BadChain(0)));

    // Every entry of the root's only cluster in use: the chain's end ends the lookup
    let mut img = image("mkfs_s1");
    let root = data_start(&img);
    for at in (root..root + SECTOR).step_by(32) {
        if img[at] == 0 {
            img[at] = 0xE5;
        }
    }
    let mut vol = mount(img).unwrap();
    assert_eq!(vol.open("NOPE.BIN"), Err(FatError::NotFound));
}

/// Byte offset of the data area (cluster 2).
fn data_start(img: &[u8]) -> usize {
    let reserved = u16::from_le_bytes([img[PART + 14], img[PART + 15]]) as usize;
    PART + (reserved + 2 * le32(img, PART + 36) as usize) * SECTOR
}

#[test]
fn errors_at_a_chains_end_and_in_directories() {
    // The last cluster's FAT entry is free instead of end-of-chain
    let mut img = image("mkfs_s1");
    let c = first_cluster(&img, b"FRAG    BIN");
    let mut last = c;
    loop {
        let next = le32(&img, fat_at(&img, last)) & 0x0FFF_FFFF;
        if next >= 0x0FFF_FFF8 {
            break;
        }
        last = next;
    }
    let at = fat_at(&img, last);
    patch(&mut img, at, &0u32.to_le_bytes());
    let mut vol = mount(img).unwrap();
    assert_eq!(
        read_file(&mut vol, "FRAG.BIN"),
        Err(FatError::BadChain(last))
    );

    // A card error in a directory, and in read_to
    let mut vol = mount(image("mkfs_s1")).unwrap();
    let root_lba = (data_start(&vol.device().img) / SECTOR) as u32;
    let f = vol.open("B.BIN").unwrap();
    vol.device().fail_read = Some(root_lba);
    assert_eq!(vol.open("B.BIN"), Err(FatError::Io(root_lba)));
    let lba = root_lba + f.cluster - 2;
    vol.device().fail_read = Some(lba);
    let mut buf = [0u8; 600];
    assert_eq!(vol.read_to(&f, &mut buf), Err(FatError::Io(lba)));
}
