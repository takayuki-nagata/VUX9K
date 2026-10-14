// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! Host tests of fw_common::fat on the card images in tests/fat_images/ (made by
//! mkfs.fat and mtools, and by vux_tool: sim/emu/test_fat_images.py), and on copies of
//! them with one field broken.

extern crate std;
use std::string::String;
use std::vec::Vec;

use fw_common::crc32_update;
use fw_common::fat::{to_short_name, BlockDevice, DirEntry, FatError, Volume, SECTOR};
use fw_common::header::{ImageHeader, HEADER_LEN};

/// A card image in memory. `fail_read`: the sector whose read fails. `far`: an LBA
/// past the image that reads as the image's boot sector (LBA 2048). `writes`: how many
/// writes reached the image; `write_limit`: how many may (power loss after that, every
/// later write fails). `corrupt`: a sector whose writes get one byte wrong.
pub struct MemDisk {
    pub img: Vec<u8>,
    pub fail_read: Option<u32>,
    pub far: Option<u32>,
    pub writes: u32,
    pub write_limit: Option<u32>,
    pub corrupt: Option<u32>,
}

fn disk(img: Vec<u8>) -> MemDisk {
    MemDisk {
        img,
        fail_read: None,
        far: None,
        writes: 0,
        write_limit: None,
        corrupt: None,
    }
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
        if self.write_limit.is_some_and(|n| self.writes >= n) {
            return false;
        }
        self.writes += 1;
        let at = lba as usize * SECTOR;
        self.img[at..at + SECTOR].copy_from_slice(buf);
        if Some(lba) == self.corrupt {
            self.img[at] ^= 1;
        }
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

/// mkfs.fat/mtools images, and vux_tool's build_fat32_image ones.
const IMAGES: [&str; 4] = ["mkfs_s1", "mkfs_s8_1fat", "vuxtool_s1", "vuxtool_s8"];

fn mount(img: Vec<u8>) -> Result<Volume<MemDisk>, FatError> {
    Volume::mount(disk(img))
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
    assert_eq!(&listed[listed.len() - 2..], ["SUB", "ARATHE~1.DAT"]);
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
            let d = MemDisk {
                far: Some(0xFFFF_F000),
                ..disk(img)
            };
            assert_eq!(Volume::mount(d).err(), Some(FatError::BadBpb), "past 2^32");
        } else {
            assert_eq!(mount(img).err(), Some(FatError::BadBpb), "root");
        }
    }
}

#[test]
fn mount_rejects_too_many_clusters_and_unmirrored_fats() {
    // 0x100F_BFE0 clusters (over FAT32's 0x0FFF_FFF5) with a FAT big enough for them, on a
    // partition that still ends below LBA 2^32: only the cluster cap refuses it
    let mut img = image("mkfs_s1");
    patch(&mut img, PART + 19, &[0, 0]);
    patch(&mut img, PART + 32, &0x1050_0000u32.to_le_bytes());
    patch(&mut img, PART + 36, &0x0020_2000u32.to_le_bytes());
    assert_eq!(mount(img).err(), Some(FatError::BadBpb));
    // ExtFlags bit 7: only one FAT copy is active
    let mut img = image("mkfs_s1");
    patch(&mut img, PART + 40, &[0x80, 0]);
    assert_eq!(mount(img).err(), Some(FatError::BadBpb));
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
        let d = MemDisk {
            fail_read: Some(lba),
            ..disk(image("mkfs_s1"))
        };
        assert_eq!(Volume::mount(d).err(), Some(FatError::Io(lba)));
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

// ----- Writes (feature fat-write) ------------------------------------------------

#[cfg(feature = "fat-write")]
mod write {
    use super::*;
    use fw_common::fat::stage2::{
        self, install, load_file, select_boot, BootError, Which, Window, BIN, NEW, OLD,
    };
    use fw_common::header::{ImageError, MODE_RISCV};
    use std::collections::BTreeSet;

    const PSRAM: Window = Window {
        base: 0x8000_0000,
        len: 0x80_0000,
    };
    const S2: u32 = 0x8070_0000;

    /// A VUX9 v4 file: `n` payload bytes from `seed`, loading at S2.
    fn v4_image(n: usize, seed: u8, version: u32) -> Vec<u8> {
        let payload: Vec<u8> = (0..n).map(|i| (i as u8).wrapping_mul(13) ^ seed).collect();
        let mut f = std::vec![0u8; HEADER_LEN];
        let mut put = |at: usize, v: u32| f[at..at + 4].copy_from_slice(&v.to_le_bytes());
        put(0, 0x5655_5839);
        put(4, 4);
        put(8, MODE_RISCV);
        put(12, n as u32);
        put(16, S2);
        put(20, S2);
        put(24, crc32_update(&payload, 0xFFFF_FFFF) ^ 0xFFFF_FFFF);
        put(28, version);
        f.extend_from_slice(&payload);
        f
    }

    /// What select_boot starts on this image: which file, its version and payload.
    fn boot(img: Vec<u8>) -> Result<(Which, u32, Vec<u8>), (BootError, BootError)> {
        let mut vol = mount(img).unwrap();
        let mut payload = Vec::new();
        let (which, h) = select_boot(&mut vol, PSRAM, &mut |addr, d| {
            if addr == S2 {
                // .OLD after a .BIN that failed part way
                payload.clear();
            }
            assert_eq!((addr - S2) as usize, payload.len());
            payload.extend_from_slice(d);
        })?;
        Ok((which, h.version, payload))
    }

    fn file(vol: &mut Volume<MemDisk>, name: &[u8; 11]) -> Option<DirEntry> {
        let dir = vol.open("VUX9K").unwrap();
        vol.find(&dir, name).unwrap()
    }

    // The volume's layout, read from the image itself (not through fw_common)
    struct Layout {
        fat: usize,
        fat_bytes: usize,
        fats: usize,
        data: usize,
        cluster: usize,
        clusters: u32,
        root: u32,
    }

    fn layout(img: &[u8]) -> Layout {
        let b = &img[PART..];
        let w = |at: usize| u16::from_le_bytes([b[at], b[at + 1]]) as usize;
        let total = if w(19) != 0 {
            w(19)
        } else {
            le32(b, 32) as usize
        };
        let fat_sectors = le32(b, 36) as usize;
        let meta = w(14) + b[16] as usize * fat_sectors;
        Layout {
            fat: PART + w(14) * SECTOR,
            fat_bytes: fat_sectors * SECTOR,
            fats: b[16] as usize,
            data: PART + meta * SECTOR,
            cluster: b[13] as usize * SECTOR,
            clusters: ((total - meta) / b[13] as usize) as u32,
            root: le32(b, 44),
        }
    }

    fn chain(img: &[u8], l: &Layout, first: u32) -> Vec<u32> {
        let mut out = Vec::new();
        let mut c = first;
        while (2..l.clusters + 2).contains(&c) {
            assert!(!out.contains(&c), "loop at {c}");
            out.push(c);
            c = le32(img, l.fat + 4 * c as usize) & 0x0FFF_FFFF;
        }
        assert!(
            first == 0 || c >= 0x0FFF_FFF8,
            "chain from {first} ends in {c:#x}"
        );
        out
    }

    /// Clusters the directory tree uses (raw directory walk). Every directory but the
    /// root starts with `.` (itself) and `..` (its parent, 0 for the root).
    fn reachable(img: &[u8], l: &Layout, dir: u32, out: &mut BTreeSet<u32>) {
        walk_tree(img, l, dir, 0, out)
    }

    fn walk_tree(img: &[u8], l: &Layout, dir: u32, parent: u32, out: &mut BTreeSet<u32>) {
        let clusters = chain(img, l, dir);
        out.extend(&clusters);
        if dir != l.root {
            let at = l.data + (clusters[0] as usize - 2) * l.cluster;
            let up = if parent == l.root { 0 } else { parent };
            for (slot, name, cluster) in [(0, b".          ", dir), (1, b"..         ", up)] {
                let e = &img[at + 32 * slot..at + 32 * slot + 32];
                let first = (u16::from_le_bytes([e[20], e[21]]) as u32) << 16
                    | u16::from_le_bytes([e[26], e[27]]) as u32;
                assert_eq!((&e[..11], e[11] & 0x10, first), (&name[..], 0x10, cluster));
            }
        }
        for c in clusters {
            let at = l.data + (c as usize - 2) * l.cluster;
            for e in img[at..at + l.cluster].chunks(32) {
                if e[0] == 0 {
                    return;
                }
                if e[0] == 0xE5 || e[11] == 0x0F || e[11] & 0x08 != 0 || e[0] == b'.' {
                    continue;
                }
                let first = (u16::from_le_bytes([e[20], e[21]]) as u32) << 16
                    | u16::from_le_bytes([e[26], e[27]]) as u32;
                if e[11] & 0x10 != 0 {
                    walk_tree(img, l, first, dir, out);
                } else {
                    let ch = chain(img, l, first);
                    let size = le32(e, 28) as usize;
                    assert_eq!(ch.len(), size.div_ceil(l.cluster), "{:?}", &e[..11]);
                    out.extend(ch);
                }
            }
        }
    }

    /// What fsck would check: equal FAT copies, every used cluster reachable exactly once
    /// (no lost clusters), FSInfo's counts unknown once anything changed.
    fn consistent(img: &[u8], changed: bool) {
        let l = layout(img);
        if l.fats == 2 {
            assert!(
                img[l.fat..l.fat + l.fat_bytes]
                    == img[l.fat + l.fat_bytes..l.fat + 2 * l.fat_bytes]
            );
        }
        let used: BTreeSet<u32> = (2..l.clusters + 2)
            .filter(|&c| le32(img, l.fat + 4 * c as usize) & 0x0FFF_FFFF != 0)
            .collect();
        let mut seen = BTreeSet::new();
        reachable(img, &l, l.root, &mut seen);
        assert_eq!(used, seen, "lost or doubly used clusters");
        let fsinfo = PART + u16::from_le_bytes([img[PART + 48], img[PART + 49]]) as usize * SECTOR;
        if changed {
            assert_eq!(&img[fsinfo + 488..fsinfo + 496], &[0xFF; 8]);
        }
    }

    #[test]
    fn install_keeps_the_previous_second_stage() {
        for name in IMAGES {
            let img = image(name);
            consistent(&img, false);
            let (which, first, old_payload) = boot(img.clone()).unwrap();
            assert_eq!(which, Which::Bin);
            // Over 128 clusters on mkfs_s1: the chain crosses FAT sectors
            let new = v4_image(70_000, 1, 7);
            let mut vol = mount(img).unwrap();
            install(&mut vol, PSRAM, &new).unwrap();
            assert_eq!(file(&mut vol, &NEW), None);
            let img = vol.device().img.clone();
            consistent(&img, true);
            assert_eq!(
                boot(img.clone()).unwrap(),
                (Which::Bin, 7, new[HEADER_LEN..].to_vec())
            );
            // The previous .BIN is .OLD now
            let mut vol = mount(img).unwrap();
            let old = file(&mut vol, &OLD).unwrap();
            let h = load_file(&mut vol, &old, PSRAM, &mut |_, _| ()).unwrap();
            assert_eq!(h.version, first, "{name}");
            assert_eq!(old.size as usize, old_payload.len() + HEADER_LEN);

            // Again: the first new one becomes .OLD, the previous .OLD's clusters are freed
            let newer = v4_image(600, 2, 8);
            install(&mut vol, PSRAM, &newer).unwrap();
            let img = vol.device().img.clone();
            consistent(&img, true);
            assert_eq!(boot(img.clone()).unwrap().1, 8);
            let mut vol = mount(img).unwrap();
            let old = file(&mut vol, &OLD).unwrap();
            assert_eq!(
                load_file(&mut vol, &old, PSRAM, &mut |_, _| ())
                    .unwrap()
                    .version,
                7
            );
        }
    }

    /// Install `new` on `img` with the card cut off after `limit` writes (None: never);
    /// the image as the cut left it, and whether the install finished.
    fn cut_install(img: Vec<u8>, new: &[u8], limit: Option<u32>) -> (Vec<u8>, bool) {
        let mut vol = Volume::mount(MemDisk {
            write_limit: limit,
            ..disk(img)
        })
        .unwrap();
        let done = install(&mut vol, PSRAM, new).is_ok();
        (std::mem::take(&mut vol.device().img), done)
    }

    /// The writes a whole install of `new` on `img` makes.
    fn install_writes(img: Vec<u8>, new: &[u8]) -> u32 {
        let mut vol = mount(img).unwrap();
        install(&mut vol, PSRAM, new).unwrap();
        vol.device().writes
    }

    /// After a cut: every file and directory a directory points to is a valid chain of
    /// allocated clusters of the right length (lost clusters are allowed, and FAT copies
    /// may differ if the cut fell between them).
    fn reachable_ok(img: &[u8]) {
        let l = layout(img);
        reachable(img, &l, l.root, &mut BTreeSet::new());
    }

    /// One version of `versions` boots, with its payload.
    fn boots_one_of(img: Vec<u8>, versions: &[(u32, &[u8])], what: &str) {
        let (_, version, payload) = boot(img).unwrap_or_else(|e| panic!("{what}: {e:?}"));
        assert!(
            versions
                .iter()
                .any(|&(v, p)| v == version && p[HEADER_LEN..] == payload[..]),
            "{what}: booted version {version}"
        );
    }

    #[test]
    fn power_loss_at_any_write_leaves_a_bootable_second_stage() {
        for name in IMAGES {
            let img = image(name);
            let (bin_version, _, bin) = boot(img.clone()).unwrap();
            let mut first = std::vec![0u8; HEADER_LEN];
            first.extend_from_slice(&bin);
            let _ = bin_version;
            let new = v4_image(3000, 3, 9);
            let total = install_writes(img.clone(), &new);
            assert!(total > 5 && total < 200, "{name}: {total} writes");
            for limit in 0..total {
                let (cut, done) = cut_install(img.clone(), &new, Some(limit));
                assert!(!done, "{name} {limit}");
                reachable_ok(&cut);
                let before = boot(img.clone()).unwrap().1;
                let (_, version, payload) = boot(cut.clone()).expect("bootable");
                assert!(version == before || (version == 9 && payload == new[HEADER_LEN..]));
                // A later install recovers (removing the stale .NEW)
                let (fixed, done) = cut_install(cut, &new, None);
                assert!(done);
                reachable_ok(&fixed);
                assert_eq!(boot(fixed).unwrap().1, 9, "{name} {limit}");
            }
        }
    }

    #[test]
    fn power_loss_during_the_recovery_install_too() {
        // A cut install, then a second install cut at each of its writes: a bootable
        // image (the old one, or one of the two new ones) survives every pair of cuts.
        // Small images and one card keep the pairs (at most 60 x 60) few.
        let img = image("mkfs_s1");
        let (_, old_version, _) = boot(img.clone()).unwrap();
        let a = v4_image(600, 6, 20);
        let b = v4_image(700, 7, 21);
        let total = install_writes(img.clone(), &a);
        assert!(total < 60, "{total} writes");
        let old = {
            let mut vol = mount(img.clone()).unwrap();
            let f = file(&mut vol, &BIN).unwrap();
            let mut data = std::vec![0u8; f.size as usize];
            vol.read_to(&f, &mut data).unwrap();
            data
        };
        let ok: [(u32, &[u8]); 3] = [(old_version, &old), (20, &a), (21, &b)];
        for first in 0..total {
            let (cut1, _) = cut_install(img.clone(), &a, Some(first));
            let total2 = install_writes(cut1.clone(), &b);
            assert!(total2 < 60, "{total2} writes");
            for second in 0..total2 {
                let (cut2, done) = cut_install(cut1.clone(), &b, Some(second));
                assert!(!done);
                reachable_ok(&cut2);
                boots_one_of(cut2, &ok, &std::format!("cuts {first}, {second}"));
            }
        }
    }

    #[test]
    fn power_loss_while_the_directory_grows() {
        // VUX9K's clusters full to the last slot: .NEW needs one more directory cluster.
        // Free clusters hold old bytes (as after earlier files were removed): a directory
        // cluster linked before it is zeroed reads as entries pointing nowhere.
        let mut img = full_dir_image("mkfs_s1");
        let l = layout(&img);
        for c in 2..l.clusters + 2 {
            if le32(&img, l.fat + 4 * c as usize) == 0 {
                let at = l.data + (c as usize - 2) * l.cluster;
                img[at..at + l.cluster].fill(0x41);
            }
        }
        let dir_len = |img: &[u8]| chain(img, &l, first_cluster(img, b"VUX9K      ")).len();
        let before = dir_len(&img);
        let new = v4_image(600, 8, 30);
        let total = install_writes(img.clone(), &new);
        let (grown, _) = cut_install(img.clone(), &new, None);
        assert_eq!(dir_len(&grown), before + 1);
        let old = boot(img.clone()).unwrap().1;
        for limit in 0..total {
            let (cut, _) = cut_install(img.clone(), &new, Some(limit));
            // A stale cluster linked in would show as garbage entries here
            reachable_ok(&cut);
            let v = boot(cut).unwrap().1;
            assert!(v == old || v == 30, "cut {limit}");
        }
    }

    /// `name` with VUX9K's directory clusters filled to their last slot (empty files).
    fn full_dir_image(name: &str) -> Vec<u8> {
        let fill = |n: u32| {
            let mut vol = mount(image(name)).unwrap();
            let dir = vol.open("VUX9K").unwrap();
            for i in 0..n {
                let f = std::format!("Z{i:03}    DAT");
                vol.create(&dir, f.as_bytes().try_into().unwrap(), b"")
                    .unwrap();
            }
            std::mem::take(&mut vol.device().img)
        };
        let img = image(name);
        let l = layout(&img);
        let dir_len = |img: &[u8]| chain(img, &l, first_cluster(img, b"VUX9K      ")).len();
        let before = dir_len(&img);
        // The first count of files that grows the directory, minus one
        let n = (1..1000).find(|&n| dir_len(&fill(n)) > before).unwrap();
        fill(n - 1)
    }

    #[test]
    fn install_over_a_broken_bin_keeps_old() {
        let mut img = image("mkfs_s1");
        let l = layout(&img);
        let c = first_cluster(&img, b"STAGE2  BIN") as usize;
        img[l.data + (c - 2) * l.cluster + HEADER_LEN] ^= 1;
        assert_eq!(boot(img.clone()).unwrap().0, Which::Old);
        let new = v4_image(500, 9, 40);
        let (img, done) = cut_install(img, &new, None);
        assert!(done);
        consistent(&img, true);
        assert_eq!(boot(img.clone()).unwrap().1, 40);
        let mut vol = mount(img).unwrap();
        let old = file(&mut vol, &OLD).unwrap();
        assert_eq!(
            load_file(&mut vol, &old, PSRAM, &mut |_, _| ())
                .unwrap()
                .version,
            1,
            "the good .OLD stays"
        );
    }

    #[test]
    fn data_given_back_when_the_directory_cannot_grow() {
        // A full directory and one free cluster: the file's data takes it, then the
        // directory can't grow
        let img = full_dir_image("mkfs_s8_1fat");
        let l = layout(&img);
        let free = |img: &[u8]| {
            (2..l.clusters + 2)
                .filter(|&c| le32(img, l.fat + 4 * c as usize) == 0)
                .count()
        };
        let mut vol = mount(img).unwrap();
        let root = vol.root();
        let hog = std::vec![0u8; (free(&vol.device().img) - 1) * l.cluster];
        vol.create(&root, b"HOG     DAT", &hog).unwrap();
        assert_eq!(free(&vol.device().img), 1);
        let dir = vol.open("VUX9K").unwrap();
        assert_eq!(
            vol.create(&dir, b"LAST    DAT", b"x"),
            Err(FatError::DiskFull)
        );
        assert_eq!(free(&vol.device().img), 1, "the data's cluster came back");
        consistent(&vol.device().img, true);
    }

    #[test]
    fn install_checks_the_image_before_writing() {
        let good = v4_image(100, 4, 1);
        let mut short = good.clone();
        short.pop();
        let mut bad_crc = good.clone();
        bad_crc[HEADER_LEN] ^= 1;
        let mut low = good.clone();
        low[16..20].copy_from_slice(&0x2000_0000u32.to_le_bytes());
        for (img, want) in [
            (&good[..10], BootError::NotV4),
            (&short[..], BootError::SizeMismatch),
            (&bad_crc[..], BootError::Crc),
            (&low[..], BootError::Image(ImageError::OutOfWindow)),
        ] {
            let mut vol = mount(image("mkfs_s1")).unwrap();
            assert_eq!(install(&mut vol, PSRAM, img), Err(want));
            assert_eq!(vol.device().writes, 0);
        }
        // No VUX9K directory: fat-full makes one (full::install_makes_the_directory)
        #[cfg(not(feature = "fat-full"))]
        {
            let mut img = image("mkfs_s1");
            let e = entry_at(&img, b"VUX9K      ");
            img[e] = 0xE5;
            let mut vol = mount(img).unwrap();
            assert_eq!(
                install(&mut vol, PSRAM, &good),
                Err(BootError::Fat(FatError::NotFound))
            );
        }
    }

    #[test]
    fn install_reads_back_what_it_wrote() {
        // Find where .NEW's data will go: the first free cluster
        let img = image("mkfs_s1");
        let l = layout(&img);
        let free = (2..l.clusters + 2)
            .find(|&c| le32(&img, l.fat + 4 * c as usize) == 0)
            .unwrap();
        let lba = (l.data / SECTOR) as u32 + (free - 2) * (l.cluster / SECTOR) as u32;
        let mut vol = Volume::mount(MemDisk {
            corrupt: Some(lba),
            ..disk(img)
        })
        .unwrap();
        assert_eq!(
            install(&mut vol, PSRAM, &v4_image(100, 5, 3)),
            Err(BootError::Fat(FatError::Verify))
        );
        assert_eq!(
            boot(vol.device().img.clone()).unwrap().0,
            Which::Bin,
            "untouched"
        );
    }

    #[test]
    fn select_boot_falls_back_to_old() {
        // .BIN corrupt (payload), missing, not v4, size disagreeing, loading outside
        let name = b"STAGE2  BIN";
        let cases: [(fn(&mut Vec<u8>, usize), BootError); 5] = [
            (
                |i, e| {
                    let l = layout(i);
                    let c = first_cluster(i, b"STAGE2  BIN") as usize;
                    let at = l.data + (c - 2) * l.cluster + HEADER_LEN + 5;
                    i[at] ^= 1;
                    let _ = e;
                },
                BootError::Crc,
            ),
            (|i, e| i[e] = 0xE5, BootError::Fat(FatError::NotFound)),
            (
                |i, e| {
                    let l = layout(i);
                    let c = first_cluster(i, b"STAGE2  BIN") as usize;
                    i[l.data + (c - 2) * l.cluster] ^= 1;
                    let _ = e;
                },
                BootError::NotV4,
            ),
            (
                |i, e| {
                    let size = le32(i, e + 28) - 1;
                    i[e + 28..e + 32].copy_from_slice(&size.to_le_bytes());
                },
                BootError::SizeMismatch,
            ),
            (
                |i, e| {
                    let l = layout(i);
                    let c = first_cluster(i, b"STAGE2  BIN") as usize;
                    let at = l.data + (c - 2) * l.cluster + 16;
                    i[at..at + 4].copy_from_slice(&0u32.to_le_bytes());
                    let _ = e;
                },
                BootError::Image(ImageError::OutOfWindow),
            ),
        ];
        for (n, (break_it, want)) in cases.into_iter().enumerate() {
            let mut img = image("mkfs_s1");
            let e = entry_at(&img, name);
            break_it(&mut img, e);
            let (which, version, _) = boot(img.clone()).unwrap();
            assert_eq!((which, version), (Which::Old, 1), "case {n}");
            let mut vol = mount(img).unwrap();
            if let Some(f) = file(&mut vol, &BIN) {
                let got = load_file(&mut vol, &f, PSRAM, &mut |_, _| ());
                assert_eq!(got.err(), Some(want), "case {n}");
            }
        }
    }

    #[test]
    fn select_boot_reports_both_failures() {
        let mut img = image("mkfs_s1");
        for name in [b"STAGE2  BIN", b"STAGE2  OLD"] {
            let e = entry_at(&img, name);
            img[e] = 0xE5;
        }
        assert_eq!(
            boot(img).err(),
            Some((
                BootError::Fat(FatError::NotFound),
                BootError::Fat(FatError::NotFound)
            ))
        );
        // An empty file is not a v4 file
        let mut vol = mount(image("mkfs_s1")).unwrap();
        let empty = file(&mut vol, b"EMPTY   DAT").unwrap();
        assert_eq!(
            load_file(&mut vol, &empty, PSRAM, &mut |_, _| ()),
            Err(BootError::NotV4)
        );
    }

    #[test]
    fn stage2_dir_is_found_by_its_short_name() {
        let mut vol = mount(image("mkfs_s1")).unwrap();
        assert!(stage2::dir(&mut vol).unwrap().is_dir());
        let bin = stage2::find_file(&mut vol, &BIN).unwrap();
        assert_eq!(Some(bin), file(&mut vol, &BIN));
        // \VUX9K\ missing, or a file
        let mut img = image("mkfs_s1");
        let e = entry_at(&img, b"VUX9K      ");
        img[e] = 0xE5;
        let missing = BootError::Fat(FatError::NotFound);
        assert_eq!(boot(img).err(), Some((missing, missing)));
        let mut img = image("mkfs_s1");
        img[e + 11] &= !fw_common::fat::ATTR_DIR;
        let mut vol = mount(img.clone()).unwrap();
        assert_eq!(stage2::dir(&mut vol), Err(FatError::WrongType));
        let file = BootError::Fat(FatError::WrongType);
        assert_eq!(boot(img).err(), Some((file, file)));
    }

    #[test]
    fn create_rename_remove() {
        let mut vol = mount(image("mkfs_s1")).unwrap();
        let dir = vol.open("VUX9K").unwrap();
        let data = std::vec![7u8; 1300];
        let f = vol.create(&dir, b"NEWFILE DAT", &data).unwrap();
        assert_eq!(read_file(&mut vol, "VUX9K/NEWFILE.DAT").unwrap(), data);
        assert_eq!(
            vol.create(&dir, b"NEWFILE DAT", b"x"),
            Err(FatError::Exists)
        );
        assert_eq!(vol.rename(&dir, &f, b"STAGE2  BIN"), Err(FatError::Exists));
        let sub = vol.open("VUX9K/SUB").unwrap();
        assert_eq!(
            vol.rename(&dir, &sub, b"X       Y  "),
            Err(FatError::WrongType)
        );
        assert_eq!(vol.remove(&sub), Err(FatError::WrongType));
        vol.rename(&dir, &f, b"RENAMED DAT").unwrap();
        assert_eq!(read_file(&mut vol, "VUX9K/RENAMED.DAT").unwrap(), data);
        assert_eq!(vol.open("VUX9K/NEWFILE.DAT"), Err(FatError::NotFound));
        let f = vol.open("VUX9K/RENAMED.DAT").unwrap();
        vol.remove(&f).unwrap();
        // An empty file, and removing one (no clusters to free)
        let e = vol.create(&dir, b"ZERO    DAT", b"").unwrap();
        assert_eq!(
            (
                e.cluster,
                read_file(&mut vol, "VUX9K/ZERO.DAT").unwrap().len()
            ),
            (0, 0)
        );
        vol.remove(&e).unwrap();
        let empty = vol.open("VUX9K/EMPTY.DAT").unwrap();
        vol.remove(&empty).unwrap();
        consistent(&vol.device().img, true);
    }

    /// The live long-name entries of the directory at `path` (raw walk).
    fn long_name_entries(img: &[u8], path_cluster: u32) -> usize {
        let l = layout(img);
        chain(img, &l, path_cluster)
            .into_iter()
            .flat_map(|c| {
                let at = l.data + (c as usize - 2) * l.cluster;
                img[at..at + l.cluster]
                    .chunks(32)
                    .map(|e| (e[0], e[11]))
                    .collect::<Vec<_>>()
            })
            .filter(|&(first, attr)| first != 0 && first != 0xE5 && attr == 0x0F)
            .count()
    }

    #[test]
    fn long_names_go_with_their_entry() {
        // mkfs_s8_1fat: the run crosses a sector; mkfs_s1: ARATHE~1 crosses a cluster
        for (name, short) in [
            ("mkfs_s8_1fat", b"LONGFI~1TXT"),
            ("mkfs_s1", b"ARATHE~1DAT"),
            ("mkfs_s1", b"LONGFI~1TXT"),
        ] {
            for rename in [false, true] {
                let mut vol = mount(image(name)).unwrap();
                let dir = vol.open("VUX9K").unwrap();
                let before = long_name_entries(&vol.device().img, dir.cluster);
                let e = vol.find(&dir, short).unwrap().unwrap();
                if rename {
                    vol.rename(&dir, &e, b"SHORT   TXT").unwrap();
                    assert!(vol.find(&dir, b"SHORT   TXT").unwrap().is_some());
                } else {
                    vol.remove(&e).unwrap();
                }
                let after = long_name_entries(&vol.device().img, dir.cluster);
                assert!(after < before, "{name} {short:?}: {before} -> {after}");
                consistent(&vol.device().img, true);
            }
        }
        // A long-name run whose checksum doesn't match its entry belongs to nothing: the
        // entry is found without it, and removing the entry leaves it
        let mut img = image("mkfs_s8_1fat");
        let e = entry_at(&img, b"LONGFI~1TXT");
        img[e - 32 + 13] ^= 1;
        img[e - 64 + 13] ^= 1;
        let mut vol = mount(img).unwrap();
        let dir = vol.open("VUX9K").unwrap();
        let before = long_name_entries(&vol.device().img, dir.cluster);
        let f = vol.find(&dir, b"LONGFI~1TXT").unwrap().unwrap();
        vol.remove(&f).unwrap();
        assert_eq!(long_name_entries(&vol.device().img, dir.cluster), before);
    }

    #[test]
    fn directories_grow_and_reuse_slots() {
        let mut vol = mount(image("mkfs_s8_1fat")).unwrap();
        let dir = vol.open("VUX9K").unwrap();
        let l = layout(&vol.device().img);
        let before = chain(&vol.device().img, &l, dir.cluster).len();
        // 4 KB clusters hold 128 entries: 120 more files need a second cluster
        for i in 0..120u32 {
            let name = std::format!("N{i:03}    DAT");
            vol.create(&dir, name.as_bytes().try_into().unwrap(), &[i as u8])
                .unwrap();
        }
        let img = vol.device().img.clone();
        assert_eq!(chain(&img, &l, dir.cluster).len(), before + 1);
        consistent(&img, true);
        let mut vol = mount(img).unwrap();
        assert_eq!(read_file(&mut vol, "VUX9K/N119.DAT").unwrap(), [119]);
        // A removed file's slot is taken by the next new one
        let g = vol.open("VUX9K/G03.DAT").unwrap();
        vol.remove(&g).unwrap();
        let again = vol.create(&dir, b"REUSE   DAT", b"r").unwrap();
        assert_eq!(again.name, *b"REUSE   DAT");
        assert_eq!(names(&mut vol, &dir)[6], "REUSE.DAT");
    }

    #[test]
    fn full_volume() {
        let mut vol = mount(image("mkfs_s8_1fat")).unwrap();
        let dir = vol.open("VUX9K").unwrap();
        let used_before = {
            let img = &vol.device().img;
            let l = layout(img);
            (2..l.clusters + 2)
                .filter(|&c| le32(img, l.fat + 4 * c as usize) != 0)
                .count()
        };
        // A small file first moves the search past the start; the big one wraps around
        vol.create(&dir, b"SMALL   DAT", b"s").unwrap();
        let huge = std::vec![1u8; 5 * 1024 * 1024];
        assert_eq!(
            vol.create(&dir, b"HUGE    DAT", &huge),
            Err(FatError::DiskFull)
        );
        let img = vol.device().img.clone();
        let l = layout(&img);
        let used = (2..l.clusters + 2)
            .filter(|&c| le32(&img, l.fat + 4 * c as usize) != 0)
            .count();
        assert_eq!(
            used,
            used_before + 1,
            "the big file's clusters were given back"
        );
        consistent(&img, true);
    }

    #[test]
    fn card_errors_while_writing() {
        // A write that fails: create stops and reports it
        let mut vol = Volume::mount(MemDisk {
            write_limit: Some(0),
            ..disk(image("mkfs_s1"))
        })
        .unwrap();
        let dir = vol.open("VUX9K").unwrap();
        assert!(matches!(
            vol.create(&dir, b"X       Y  ", b"x"),
            Err(FatError::Io(_))
        ));
        // Removing a file whose chain is broken
        let mut img = image("mkfs_s1");
        let c = first_cluster(&img, b"FRAG    BIN");
        let at = fat_at(&img, c);
        patch(&mut img, at, &1u32.to_le_bytes());
        let mut vol = mount(img).unwrap();
        let f = vol.open("FRAG.BIN").unwrap();
        assert_eq!(vol.remove(&f), Err(FatError::BadChain(1)));
        // ... and one whose chain loops back to its start
        let mut img = image("mkfs_s1");
        let c = first_cluster(&img, b"B       BIN");
        let next = le32(&img, fat_at(&img, c)) & 0x0FFF_FFFF;
        let at = fat_at(&img, next);
        patch(&mut img, at, &c.to_le_bytes());
        let mut vol = mount(img).unwrap();
        let f = vol.open("B.BIN").unwrap();
        assert_eq!(vol.remove(&f), Err(FatError::BadChain(0)));
    }

    #[test]
    fn fsinfo_left_alone_when_missing_or_invalid() {
        for (at, bytes) in [(PART + 48, &[0u8, 0][..]), (PART + SECTOR, &[0u8; 4][..])] {
            let mut img = image("mkfs_s1");
            patch(&mut img, at, bytes);
            let fsinfo = img[PART + SECTOR..PART + 2 * SECTOR].to_vec();
            let mut vol = mount(img).unwrap();
            let dir = vol.open("VUX9K").unwrap();
            vol.create(&dir, b"X       Y  ", b"x").unwrap();
            assert_eq!(
                &vol.device().img[PART + SECTOR..PART + 2 * SECTOR],
                &fsinfo[..]
            );
        }
    }

    // ----- The file manager's reads and writes (feature fat-full) ---------------------

    #[cfg(feature = "fat-full")]
    mod full {
        use super::*;

        /// Each entry of `dir` as (8.3 name, long name), through `list`; each entry also
        /// as `find` gives it (the same slots for its long name).
        fn listing(vol: &mut Volume<MemDisk>, dir: &DirEntry) -> Vec<(String, Option<String>)> {
            let mut out = Vec::new();
            let mut entries = Vec::new();
            let found = vol
                .list(dir, &mut |e, long| {
                    let mut buf = [0u8; 12];
                    let n = e.display_name(&mut buf);
                    out.push((
                        String::from_utf8_lossy(&buf[..n]).into_owned(),
                        long.map(|l| String::from_utf8(l.to_vec()).unwrap()),
                    ));
                    entries.push(*e);
                    false
                })
                .unwrap();
            assert_eq!(found, None);
            for e in entries {
                assert_eq!(vol.find(dir, &e.name).unwrap(), Some(e));
            }
            out
        }

        fn longs(vol: &mut Volume<MemDisk>, path: &str) -> Vec<Option<String>> {
            let root = vol.root();
            let dir = vol.resolve(&root, path.as_bytes()).unwrap();
            listing(vol, &dir).into_iter().map(|(_, l)| l).collect()
        }

        fn resolve(vol: &mut Volume<MemDisk>, path: &str) -> Result<DirEntry, FatError> {
            let root = vol.root();
            vol.resolve(&root, path.as_bytes())
        }

        fn s(x: &str) -> Option<String> {
            Some(x.into())
        }

        const AVERY: &str = "A Very Long File Name That Needs Several Entries.txt";

        /// Its bytes (test_fat_images.py's data(700, 4)).
        fn avery_data() -> Vec<u8> {
            (0..700u32).map(|i| (i * 7 + 7) as u8).collect()
        }

        /// A file's bytes, by a path `resolve` takes.
        fn read_path(vol: &mut Volume<MemDisk>, path: &str) -> Result<Vec<u8>, FatError> {
            let f = resolve(vol, path)?;
            let mut out = Vec::new();
            vol.for_each_sector(&f, &mut |d| out.extend_from_slice(d))?;
            Ok(out)
        }

        #[test]
        fn reads_every_file_of_the_long_name_image() {
            let mut vol = mount(image("mkfs_lfn")).unwrap();
            for (path, size, crc) in manifest("mkfs_lfn") {
                let f = resolve(&mut vol, &path).unwrap();
                let mut data = std::vec![0u8; f.size as usize];
                vol.read_to(&f, &mut data).unwrap();
                assert_eq!(data.len() as u32, size, "{path}");
                assert_eq!(
                    crc32_update(&data, 0xFFFF_FFFF) ^ 0xFFFF_FFFF,
                    crc,
                    "{path}"
                );
            }
        }

        #[test]
        fn lists_long_names() {
            let mut vol = mount(image("mkfs_lfn")).unwrap();
            // The volume label and the removed long-named file aren't listed; a lowercase
            // 8.3 name has no long name; what isn't ASCII reads as ?; 13 characters fill
            // an entry without an end mark
            assert_eq!(
                longs(&mut vol, ""),
                [
                    s("Projects Folder"),
                    s("ABCDEFGHIJ.KL"),
                    None,
                    s("?n?c?d? file.txt"),
                    s("Mixed Case.Txt"),
                    None
                ]
            );
            let root = vol.root();
            let names: Vec<String> = listing(&mut vol, &root)
                .into_iter()
                .map(|(n, _)| n)
                .collect();
            assert_eq!(names[..3], ["PROJEC~1", "ABCDEF~1.KL", "LOWER.TXT"]);
            // Four entries, 52 characters (no end mark)
            assert_eq!(longs(&mut vol, "PROJEC~1"), [None, None, s(AVERY), None]);
            // On mkfs_s1: a run that crosses into the directory's second cluster
            let mut vol = mount(image("mkfs_s1")).unwrap();
            let l = longs(&mut vol, "VUX9K");
            assert_eq!(l[4], s("Long File Name.txt"));
            assert_eq!(l[l.len() - 1], s("A Rather Long File Name 01.dat"));
            // list stops where f says, and refuses a file
            let dir = resolve(&mut vol, "VUX9K").unwrap();
            let stop = vol
                .list(&dir, &mut |_, l| l == Some(b"Long File Name.txt"))
                .unwrap();
            assert_eq!(stop.unwrap().name, *b"LONGFI~1TXT");
            let file = resolve(&mut vol, "B.BIN").unwrap();
            assert_eq!(vol.list(&file, &mut |_, _| true), Err(FatError::WrongType));
        }

        #[test]
        fn long_name_runs_that_do_not_spell_a_name() {
            // PROJEC~1's run: slot e - 64 (part 2, the last, bit 6) and e - 32 (part 1)
            let img = image("mkfs_lfn");
            let e = entry_at(&img, b"PROJEC~1   ");
            let one = entry_at(&img, b"ABCDEF~1KL ");
            let cases: [(&str, fn(&mut Vec<u8>, usize, usize)); 8] = [
                // The checksum of one part, or of both
                ("one checksum", |i, e, _| i[e - 32 + 13] ^= 1),
                ("both checksums", |i, e, _| {
                    i[e - 32 + 13] ^= 1;
                    i[e - 64 + 13] ^= 1;
                }),
                // Parts out of order, a part number out of range, a run without its start
                ("order", |i, e, _| i[e - 32] = 0x02),
                ("part 21", |i, e, _| i[e - 64] = 0x40 | 21),
                ("part 0", |i, e, _| i[e - 64] = 0x40),
                ("part 0 inside", |i, e, _| i[e - 32] = 0x20),
                ("deleted start", |i, e, _| i[e - 64] = 0xE5),
                // A name of no characters
                ("empty", |i, _, one| {
                    i[one - 32 + 1] = 0;
                    i[one - 32 + 2] = 0;
                }),
            ];
            for (what, break_it) in cases {
                let mut img = image("mkfs_lfn");
                break_it(&mut img, e, one);
                let mut vol = mount(img).unwrap();
                let l = longs(&mut vol, "");
                let want = if what == "empty" {
                    [s("Projects Folder"), None]
                } else {
                    [None, s("ABCDEFGHIJ.KL")]
                };
                assert_eq!(l[..2], want, "{what}");
            }
        }

        #[test]
        fn paths_by_long_name_with_dot_and_dot_dot() {
            let mut vol = mount(image("mkfs_lfn")).unwrap();
            let root = vol.root();
            let x = resolve(&mut vol, "PROJEC~1/SUB/X.TXT").unwrap();
            for p in [
                "Projects Folder/sub/x.txt",
                "/projects folder\\SUB/./x.txt",
                "\\PROJECTS FOLDER//sub/../sub/x.txt",
                "Projects Folder/sub/../../Projects Folder/sub/x.txt",
            ] {
                assert_eq!(resolve(&mut vol, p).unwrap(), x, "{p}");
            }
            let a = resolve(&mut vol, &std::format!("Projects Folder/{AVERY}")).unwrap();
            assert_eq!(a.name, *b"AVERYL~1TXT");
            assert_eq!(
                resolve(
                    &mut vol,
                    &AVERY.to_uppercase().replace("A VERY", "projec~1/a very")
                )
                .unwrap(),
                a
            );
            assert_eq!(resolve(&mut vol, "?N?C?D? FILE.TXT").unwrap().size, 8);
            // From a working directory: relative, `..` (the parent's own entry), absolute
            let projects = resolve(&mut vol, "PROJEC~1").unwrap();
            let sub = resolve(&mut vol, "PROJEC~1/SUB").unwrap();
            for (p, want) in [
                ("x.txt", x),
                ("./X.TXT", x),
                (".", sub),
                ("", sub),
                ("..", projects),
                ("../", projects),
                ("../..", root),
                ("../../../..", root),
                ("/", root),
            ] {
                assert_eq!(vol.resolve(&sub, p.as_bytes()).unwrap(), want, "{p}");
            }
            assert_eq!(
                vol.resolve(&sub, b"/readme.txt"),
                resolve(&mut vol, "README.TXT")
            );
            assert_eq!(vol.resolve(&sub, b"\\lower.txt").unwrap().size, 6);
            for (p, want) in [
                ("nope", FatError::NotFound),
                ("Projects Folderx", FatError::NotFound),
                ("README.TXT/x", FatError::WrongType),
                ("README.TXT/..", FatError::WrongType),
                ("README.TXT/.", FatError::WrongType),
            ] {
                assert_eq!(resolve(&mut vol, p), Err(want), "{p}");
            }
            // mkfs_s1: two levels down
            let mut vol = mount(image("mkfs_s1")).unwrap();
            let vux = resolve(&mut vol, "VUX9K").unwrap();
            assert_eq!(resolve(&mut vol, "VUX9K/SUB/..").unwrap(), vux);
            assert_eq!(resolve(&mut vol, "vux9k/sub/../sub/x.txt").unwrap().size, 7);
        }

        /// Offset of slot `n` of the directory whose first cluster is `c`.
        fn slot(img: &[u8], c: u32, n: usize) -> usize {
            let l = layout(img);
            l.data + (c as usize - 2) * l.cluster + 32 * n
        }

        fn set_cluster(img: &mut [u8], at: usize, c: u32) {
            img[at + 20..at + 22].copy_from_slice(&((c >> 16) as u16).to_le_bytes());
            img[at + 26..at + 28].copy_from_slice(&(c as u16).to_le_bytes());
        }

        #[test]
        fn dot_dot_as_other_tools_write_it_and_broken() {
            let img = image("mkfs_s1");
            let vux = first_cluster(&img, b"VUX9K      ");
            let sub = first_cluster(&img, b"SUB        ");
            let root_cluster = layout(&img).root;
            // `..` naming the root by its cluster instead of 0
            let mut i = img.clone();
            set_cluster(&mut i, slot(&img, vux, 1), root_cluster);
            let mut vol = mount(i).unwrap();
            let d = resolve(&mut vol, "VUX9K").unwrap();
            assert_eq!(vol.resolve(&d, b"..").unwrap(), vol.root());
            assert_eq!(resolve(&mut vol, "VUX9K/SUB/..").unwrap(), d);
            // VUX9K's `..` gone: SUB's parent can't be placed
            let mut i = img.clone();
            i[slot(&img, vux, 1)] = 0xE5;
            let mut vol = mount(i).unwrap();
            assert_eq!(resolve(&mut vol, "VUX9K/SUB/.."), Err(FatError::NotFound));
            // SUB's `..` names SUB: no directory holds an entry for it
            let mut i = img.clone();
            set_cluster(&mut i, slot(&img, sub, 1), sub);
            let mut vol = mount(i).unwrap();
            assert_eq!(resolve(&mut vol, "VUX9K/SUB/.."), Err(FatError::NotFound));
        }

        #[test]
        fn card_errors_in_long_name_lookups() {
            let mut vol = mount(image("mkfs_lfn")).unwrap();
            let sub = resolve(&mut vol, "Projects Folder/sub").unwrap();
            let l = layout(&vol.device().img);
            let root_lba = ((l.data + (l.root as usize - 2) * l.cluster) / SECTOR) as u32;
            vol.device().fail_read = Some(root_lba);
            // A long name (looked up by list alone), and `..` placed in the root
            assert_eq!(
                resolve(&mut vol, "Projects Folder"),
                Err(FatError::Io(root_lba))
            );
            assert_eq!(vol.resolve(&sub, b".."), Err(FatError::Io(root_lba)));
        }

        #[test]
        fn paths_for_new_entries_and_for_changes() {
            let mut vol = mount(image("mkfs_lfn")).unwrap();
            let root = vol.root();
            let projects = resolve(&mut vol, "Projects Folder").unwrap();
            let sub = resolve(&mut vol, "Projects Folder/sub").unwrap();
            for (p, want) in [
                ("Projects Folder/new.txt", (projects, *b"NEW     TXT")),
                ("NEWDIR/", (root, *b"NEWDIR     ")),
                ("/X", (root, *b"X          ")),
                ("projec~1/sub/..\\Y.Z", (projects, *b"Y       Z  ")),
            ] {
                assert_eq!(
                    vol.resolve_parent(&root, p.as_bytes()).unwrap(),
                    want,
                    "{p}"
                );
            }
            assert_eq!(
                vol.resolve_parent(&sub, b"../../T").unwrap(),
                (root, *b"T          ")
            );
            for (p, want) in [
                ("", FatError::BadName),
                ("/", FatError::BadName),
                ("..", FatError::BadName),
                ("Projects Folder/Long Name.txt", FatError::BadName),
                ("nope/X", FatError::NotFound),
                ("README.TXT/X", FatError::WrongType),
            ] {
                assert_eq!(vol.resolve_parent(&root, p.as_bytes()), Err(want), "{p}");
            }
            // locate: the entry and the directory it is in
            let x = resolve(&mut vol, "Projects Folder/sub/x.txt").unwrap();
            assert_eq!(
                vol.locate(&root, b"Projects Folder/sub/x.txt").unwrap(),
                (sub, x)
            );
            let a = resolve(&mut vol, &std::format!("PROJEC~1/{AVERY}")).unwrap();
            let p = std::format!("../{AVERY}");
            assert_eq!(vol.locate(&sub, p.as_bytes()).unwrap(), (projects, a));
            assert_eq!(vol.locate(&root, b"sub/").err(), Some(FatError::NotFound));
            for p in ["", "/", ".", "PROJEC~1/..", "PROJEC~1/./"] {
                assert_eq!(
                    vol.locate(&root, p.as_bytes()),
                    Err(FatError::BadName),
                    "{p}"
                );
            }
            assert_eq!(vol.locate(&root, b"README.TXT/X"), Err(FatError::WrongType));
        }

        fn free_in_fat(img: &[u8]) -> u32 {
            let l = layout(img);
            (2..l.clusters + 2)
                .filter(|&c| le32(img, l.fat + 4 * c as usize) & 0x0FFF_FFFF == 0)
                .count() as u32
        }

        #[test]
        fn free_clusters_counts_the_fat() {
            for name in IMAGES.iter().chain(&["mkfs_lfn"]) {
                let img = image(name);
                let want = free_in_fat(&img);
                let mut vol = mount(img).unwrap();
                assert_eq!(vol.free_clusters().unwrap(), want, "{name}");
                let root = vol.root();
                let n = vol.cluster_bytes() as usize * 3 - 1;
                vol.create(&root, b"THREE   BIN", &std::vec![1u8; n])
                    .unwrap();
                assert_eq!(vol.free_clusters().unwrap(), want - 3, "{name}");
            }
        }

        /// The raw entry at slot `n` of directory cluster `c`: (name, attr, cluster).
        fn raw_slot(img: &[u8], c: u32, n: usize) -> ([u8; 11], u8, u32) {
            let at = slot(img, c, n);
            let e = &img[at..at + 32];
            let first = (u16::from_le_bytes([e[20], e[21]]) as u32) << 16
                | u16::from_le_bytes([e[26], e[27]]) as u32;
            (e[..11].try_into().unwrap(), e[11], first)
        }

        /// The image with every free cluster full of 0x41 (old data).
        fn stale(mut img: Vec<u8>) -> Vec<u8> {
            let l = layout(&img);
            for c in 2..l.clusters + 2 {
                if le32(&img, l.fat + 4 * c as usize) == 0 {
                    let at = l.data + (c as usize - 2) * l.cluster;
                    img[at..at + l.cluster].fill(0x41);
                }
            }
            img
        }

        fn dots() -> Vec<(String, Option<String>)> {
            std::vec![(".".into(), None), ("..".into(), None)]
        }

        #[test]
        fn mkdir_makes_an_empty_directory() {
            for name in ["mkfs_lfn", "mkfs_s8_1fat"] {
                let mut vol = mount(stale(image(name))).unwrap();
                let free = vol.free_clusters().unwrap();
                let root = vol.root();
                let d = vol.mkdir(&root, b"NEWDIR     ").unwrap();
                assert_eq!(resolve(&mut vol, "newdir").unwrap(), d);
                assert_eq!(listing(&mut vol, &d), dots());
                let img = &vol.device().img;
                assert_eq!(
                    raw_slot(img, d.cluster, 0),
                    (*b".          ", 0x10, d.cluster)
                );
                assert_eq!(raw_slot(img, d.cluster, 1), (*b"..         ", 0x10, 0));
                // One level down: `..` names the parent
                let nest = vol.mkdir(&d, b"NEST       ").unwrap();
                let img = &vol.device().img;
                assert_eq!(
                    raw_slot(img, nest.cluster, 1),
                    (*b"..         ", 0x10, d.cluster)
                );
                assert_eq!(vol.resolve(&nest, b"..").unwrap(), d);
                vol.create(&nest, b"F       TXT", b"in nest").unwrap();
                assert_eq!(
                    read_file(&mut vol, "NEWDIR/NEST/F.TXT").unwrap(),
                    b"in nest"
                );
                assert_eq!(vol.free_clusters().unwrap(), free - 3);
                consistent(&vol.device().img, true);
                // Taken names, and a file for a parent
                assert_eq!(vol.mkdir(&root, b"NEWDIR     "), Err(FatError::Exists));
                assert_eq!(vol.mkdir(&nest, b"F       TXT"), Err(FatError::Exists));
                let f = vol.open("NEWDIR/NEST/F.TXT").unwrap();
                assert_eq!(vol.mkdir(&f, b"X          "), Err(FatError::WrongType));
            }
        }

        /// `name` with every free cluster taken by a file `HOG.DAT` in the root, but `keep`.
        fn hogged(img: Vec<u8>, keep: u32) -> Vec<u8> {
            let l = layout(&img);
            let free = free_in_fat(&img);
            let mut vol = mount(img).unwrap();
            let root = vol.root();
            let hog = std::vec![0u8; (free - keep) as usize * l.cluster];
            vol.create(&root, b"HOG     DAT", &hog).unwrap();
            assert_eq!(free_in_fat(&vol.device().img), keep);
            std::mem::take(&mut vol.device().img)
        }

        #[test]
        fn mkdir_on_a_full_card_or_card_errors() {
            // No free cluster
            let mut vol = mount(hogged(image("mkfs_s8_1fat"), 0)).unwrap();
            let root = vol.root();
            assert_eq!(vol.mkdir(&root, b"D          "), Err(FatError::DiskFull));
            consistent(&vol.device().img, true);
            // One: the new directory takes it, then VUX9K can't grow for its entry
            let mut vol = mount(hogged(full_dir_image("mkfs_s8_1fat"), 1)).unwrap();
            let dir = vol.open("VUX9K").unwrap();
            assert_eq!(vol.mkdir(&dir, b"D          "), Err(FatError::DiskFull));
            assert_eq!(vol.free_clusters().unwrap(), 1, "the cluster came back");
            consistent(&vol.device().img, true);
            // The card fails at its second write (the new directory's first cluster)
            let mut vol = Volume::mount(MemDisk {
                write_limit: Some(1),
                ..disk(image("mkfs_s8_1fat"))
            })
            .unwrap();
            let root = vol.root();
            assert!(matches!(
                vol.mkdir(&root, b"D          "),
                Err(FatError::Io(_))
            ));
        }

        #[test]
        fn rmdir_removes_empty_directories_only() {
            let mut vol = mount(image("mkfs_lfn")).unwrap();
            let img0 = vol.device().img.clone();
            let free = vol.free_clusters().unwrap();
            let root = vol.root();
            // A directory over several clusters (16 entries each), emptied
            let d = vol.mkdir(&root, b"BIG        ").unwrap();
            for i in 0..40 {
                let n = std::format!("F{i:02}     DAT");
                vol.create(&d, n.as_bytes().try_into().unwrap(), b"x")
                    .unwrap();
            }
            assert_eq!(vol.rmdir(&d), Err(FatError::NotEmpty));
            let l = layout(&img0);
            assert!(chain(&vol.device().img, &l, d.cluster).len() >= 3);
            for i in 0..40 {
                let f = vol.open(&std::format!("BIG/F{i:02}.DAT")).unwrap();
                vol.remove(&f).unwrap();
            }
            vol.rmdir(&d).unwrap();
            assert_eq!(vol.free_clusters().unwrap(), free);
            assert_eq!(resolve(&mut vol, "BIG"), Err(FatError::NotFound));
            consistent(&vol.device().img, true);
            // A long-named directory goes with its long name, once emptied
            let projects = resolve(&mut vol, "Projects Folder").unwrap();
            assert_eq!(vol.rmdir(&projects), Err(FatError::NotEmpty));
            let sub = resolve(&mut vol, "Projects Folder/sub").unwrap();
            assert_eq!(vol.rmdir(&sub), Err(FatError::NotEmpty));
            let a = std::format!("Projects Folder/{AVERY}");
            for p in ["Projects Folder/sub/x.txt", a.as_str()] {
                let (_, f) = vol.locate(&root, p.as_bytes()).unwrap();
                vol.remove(&f).unwrap();
            }
            vol.rmdir(&sub).unwrap();
            let before = long_name_entries(&vol.device().img, l.root);
            vol.rmdir(&projects).unwrap();
            assert_eq!(long_name_entries(&vol.device().img, l.root), before - 2);
            consistent(&vol.device().img, true);
            // Not a directory, the root, `.` and `..`
            let f = resolve(&mut vol, "README.TXT").unwrap();
            assert_eq!(vol.rmdir(&f), Err(FatError::WrongType));
            assert_eq!(vol.rmdir(&root), Err(FatError::BadName));
            let d = vol.mkdir(&root, b"D          ").unwrap();
            let mut dot_entries = Vec::new();
            vol.list(&d, &mut |e, _| {
                dot_entries.push(*e);
                false
            })
            .unwrap();
            for e in dot_entries {
                assert_eq!(vol.rmdir(&e), Err(FatError::BadName));
            }
            vol.rmdir(&d).unwrap();
            consistent(&vol.device().img, true);
        }

        #[test]
        fn move_to_another_directory_keeps_the_chain() {
            let mut vol = mount(image("mkfs_lfn")).unwrap();
            let root = vol.root();
            let free = vol.free_clusters().unwrap();
            let sub = resolve(&mut vol, "Projects Folder/sub").unwrap();
            let readme = resolve(&mut vol, "README.TXT").unwrap();
            // An entry's bytes after its name, found by name (no long names here)
            let raw = |vol: &mut Volume<MemDisk>, e: &DirEntry| {
                let at = entry_at(&vol.device().img, &e.name);
                vol.device().img[at + 11..at + 32].to_vec()
            };
            let before = raw(&mut vol, &readme);
            let moved = vol.move_to(&root, &readme, &sub, b"MOVED   TXT").unwrap();
            assert_eq!(vol.find(&sub, b"MOVED   TXT").unwrap(), Some(moved));
            assert_eq!((moved.cluster, moved.size), (readme.cluster, readme.size));
            assert_eq!(
                raw(&mut vol, &moved),
                before,
                "attributes, times, chain, size"
            );
            assert_eq!(resolve(&mut vol, "README.TXT"), Err(FatError::NotFound));
            assert_eq!(
                read_file(&mut vol, "PROJEC~1/SUB/MOVED.TXT").unwrap(),
                b"hello\n"
            );
            assert_eq!(vol.free_clusters().unwrap(), free);
            consistent(&vol.device().img, true);
            // A long-named file: its long-name entries go with the old entry
            let projects = resolve(&mut vol, "Projects Folder").unwrap();
            let a = resolve(&mut vol, &std::format!("Projects Folder/{AVERY}")).unwrap();
            let lfn = long_name_entries(&vol.device().img, projects.cluster);
            let m = vol.move_to(&projects, &a, &root, b"AVERY   TXT").unwrap();
            assert_eq!(
                long_name_entries(&vol.device().img, projects.cluster),
                lfn - 4
            );
            assert_eq!(vol.locate(&root, b"avery.txt").unwrap(), (root, m));
            assert_eq!(read_file(&mut vol, "AVERY.TXT").unwrap(), avery_data());
            consistent(&vol.device().img, true);
            // Within one directory: a rename
            let n = vol.move_to(&root, &m, &root, b"RENAMED TXT").unwrap();
            assert_eq!(vol.find(&root, b"RENAMED TXT").unwrap(), Some(n));
            assert_eq!(vol.find(&root, b"AVERY   TXT").unwrap(), None);
            consistent(&vol.device().img, true);
            // Taken names (in another directory and in the same), directories, a file as
            // the destination
            let lower = resolve(&mut vol, "LOWER.TXT").unwrap();
            assert_eq!(
                vol.move_to(&root, &lower, &sub, b"MOVED   TXT"),
                Err(FatError::Exists)
            );
            assert_eq!(
                vol.move_to(&root, &lower, &root, b"RENAMED TXT"),
                Err(FatError::Exists)
            );
            assert_eq!(
                vol.move_to(&root, &projects, &sub, b"P          "),
                Err(FatError::WrongType)
            );
            assert_eq!(
                vol.move_to(&root, &lower, &n, b"P          "),
                Err(FatError::WrongType)
            );
        }

        #[test]
        fn move_into_a_full_directory() {
            // VUX9K full to its last slot: it grows by a cluster; with no cluster free the
            // move fails and the file stays
            let img = stale(full_dir_image("mkfs_s1"));
            let l = layout(&img);
            let vux = first_cluster(&img, b"VUX9K      ");
            let before = chain(&img, &l, vux).len();
            let mut vol = mount(img.clone()).unwrap();
            let (root, f) = vol.locate(&vol.root(), b"README.TXT").unwrap();
            let dir = vol.open("VUX9K").unwrap();
            vol.move_to(&root, &f, &dir, b"README  TXT").unwrap();
            assert_eq!(chain(&vol.device().img, &l, vux).len(), before + 1);
            assert_eq!(read_file(&mut vol, "VUX9K/README.TXT").unwrap(), b"hello\n");
            consistent(&vol.device().img, true);
            let mut vol = mount(hogged(img, 0)).unwrap();
            let (root, f) = vol.locate(&vol.root(), b"README.TXT").unwrap();
            let dir = vol.open("VUX9K").unwrap();
            assert_eq!(
                vol.move_to(&root, &f, &dir, b"README  TXT"),
                Err(FatError::DiskFull)
            );
            assert_eq!(read_file(&mut vol, "README.TXT").unwrap(), b"hello\n");
            consistent(&vol.device().img, true);
        }

        #[test]
        fn install_makes_the_directory() {
            let new = v4_image(3000, 3, 9);
            // mkfs_lfn has no \VUX9K\; on mkfs_s1 its entry is removed (its clusters are
            // then lost: no `consistent` there)
            let mut gone = image("mkfs_s1");
            let e = entry_at(&gone, b"VUX9K      ");
            gone[e] = 0xE5;
            for (what, img) in [("mkfs_lfn", image("mkfs_lfn")), ("no VUX9K", gone)] {
                let mut vol = mount(img).unwrap();
                install(&mut vol, PSRAM, &new).unwrap();
                let img = vol.device().img.clone();
                reachable_ok(&img);
                if what == "mkfs_lfn" {
                    consistent(&img, true);
                }
                assert_eq!(boot(img.clone()).unwrap().1, 9, "{what}");
                let mut vol = mount(img).unwrap();
                let names: Vec<String> = {
                    let d = vol.open("VUX9K").unwrap();
                    listing(&mut vol, &d).into_iter().map(|(n, _)| n).collect()
                };
                assert_eq!(names, [".", "..", "STAGE2.BIN"], "{what}");
            }
            // Cut at every write (the directory first): a later install still succeeds
            let img = image("mkfs_lfn");
            let total = install_writes(img.clone(), &new);
            for limit in 0..total {
                let (cut, done) = cut_install(img.clone(), &new, Some(limit));
                assert!(!done);
                reachable_ok(&cut);
                if let Some(l) = dir_listing(&cut, "VUX9K") {
                    assert!(l.len() <= 3, "cut {limit}");
                }
                let (fixed, done) = cut_install(cut, &new, None);
                assert!(done, "cut {limit}");
                reachable_ok(&fixed);
                assert_eq!(boot(fixed).unwrap().1, 9, "cut {limit}");
            }
            // \VUX9K\ a file: refused, as before
            let mut img = image("mkfs_s1");
            img[e + 11] &= !fw_common::fat::ATTR_DIR;
            let mut vol = mount(img).unwrap();
            assert_eq!(
                install(&mut vol, PSRAM, &new),
                Err(BootError::Fat(FatError::WrongType))
            );
        }

        // ----- Power loss ---------------------------------------------------------

        type Op = fn(&mut Volume<MemDisk>) -> Result<(), FatError>;

        /// Run `op` on `img` with the card cut off after each possible number of writes
        /// (and once without a cut); `check` gets each image left, and whether `op`
        /// finished. Every image keeps every chain a directory points to valid, and every
        /// directory its `.` and `..`.
        fn cut_everywhere(img: &[u8], op: Op, check: &dyn Fn(&[u8], bool)) {
            let total = {
                let mut vol = mount(img.to_vec()).unwrap();
                op(&mut vol).unwrap();
                vol.device().writes
            };
            assert!(total > 2 && total < 200, "{total} writes");
            for limit in (0..total).map(Some).chain([None]) {
                let mut vol = Volume::mount(MemDisk {
                    write_limit: limit,
                    ..disk(img.to_vec())
                })
                .unwrap();
                let done = op(&mut vol).is_ok();
                assert_eq!(done, limit.is_none(), "cut {limit:?}");
                let cut = std::mem::take(&mut vol.device().img);
                reachable_ok(&cut);
                check(&cut, done);
            }
        }

        /// The listing of `path` on `img`, or None if it doesn't resolve.
        fn dir_listing(img: &[u8], path: &str) -> Option<Vec<(String, Option<String>)>> {
            let mut vol = mount(img.to_vec()).unwrap();
            let d = resolve(&mut vol, path).ok()?;
            Some(listing(&mut vol, &d))
        }

        /// The files and directories of mkfs_lfn that no operation here touches.
        fn others_intact(img: &[u8]) {
            let mut vol = mount(img.to_vec()).unwrap();
            for (path, size, _) in manifest("mkfs_lfn") {
                if !path.contains("Several") && path != "README.TXT" {
                    assert_eq!(resolve(&mut vol, &path).unwrap().size, size, "{path}");
                }
            }
        }

        #[test]
        fn power_loss_in_mkdir() {
            fn op(vol: &mut Volume<MemDisk>) -> Result<(), FatError> {
                let root = vol.root();
                let (dir, name) = vol.resolve_parent(&root, b"Projects Folder/NEWDIR")?;
                vol.mkdir(&dir, &name).map(|_| ())
            }
            fn grow(vol: &mut Volume<MemDisk>) -> Result<(), FatError> {
                let dir = vol.open("VUX9K")?;
                vol.mkdir(&dir, b"NEWDIR     ").map(|_| ())
            }
            // There or not, and when there, empty
            cut_everywhere(&stale(image("mkfs_lfn")), op, &|img, done| {
                others_intact(img);
                match dir_listing(img, "Projects Folder/NEWDIR") {
                    Some(l) => assert_eq!(l, dots()),
                    None => assert!(!done),
                }
            });
            // Into a full directory, which grows first
            cut_everywhere(
                &stale(full_dir_image("mkfs_s1")),
                grow,
                &|img, done| match dir_listing(img, "VUX9K/NEWDIR") {
                    Some(l) => assert_eq!(l, dots()),
                    None => assert!(!done),
                },
            );
        }

        #[test]
        fn power_loss_in_rmdir() {
            // A directory of three clusters, emptied
            let img = {
                let mut vol = mount(stale(image("mkfs_lfn"))).unwrap();
                let root = vol.root();
                let d = vol.mkdir(&root, b"BIG        ").unwrap();
                for i in 0..40 {
                    let n = std::format!("F{i:02}     DAT");
                    vol.create(&d, n.as_bytes().try_into().unwrap(), b"x")
                        .unwrap();
                }
                for i in 0..40 {
                    let f = vol.open(&std::format!("BIG/F{i:02}.DAT")).unwrap();
                    vol.remove(&f).unwrap();
                }
                std::mem::take(&mut vol.device().img)
            };
            fn op(vol: &mut Volume<MemDisk>) -> Result<(), FatError> {
                let (_, d) = vol.locate(&vol.root(), b"BIG")?;
                vol.rmdir(&d)
            }
            cut_everywhere(&img, op, &|img, done| {
                others_intact(img);
                if let Some(l) = dir_listing(img, "BIG") {
                    assert_eq!(l, dots());
                }
                if done {
                    consistent(img, true);
                }
            });
        }

        #[test]
        fn power_loss_in_move_to() {
            fn op(vol: &mut Volume<MemDisk>) -> Result<(), FatError> {
                let root = vol.root();
                let src = std::format!("Projects Folder/{AVERY}");
                let (sdir, f) = vol.locate(&root, src.as_bytes())?;
                let (ddir, name) = vol.resolve_parent(&root, b"Projects Folder/sub/AVERY.TXT")?;
                vol.move_to(&sdir, &f, &ddir, &name).map(|_| ())
            }
            fn grow(vol: &mut Volume<MemDisk>) -> Result<(), FatError> {
                let (sdir, f) = vol.locate(&vol.root(), b"README.TXT")?;
                let dir = vol.open("VUX9K")?;
                vol.move_to(&sdir, &f, &dir, b"README  TXT").map(|_| ())
            }
            let src = std::format!("Projects Folder/{AVERY}");
            // Under one name at least, with its bytes; under the new one only when done
            cut_everywhere(&stale(image("mkfs_lfn")), op, &|img, done| {
                others_intact(img);
                let mut vol = mount(img.to_vec()).unwrap();
                let old = read_path(&mut vol, &src).ok();
                let new = read_path(&mut vol, "Projects Folder/sub/AVERY.TXT").ok();
                assert!(old.is_some() || new.is_some());
                for got in [&old, &new].into_iter().flatten() {
                    assert_eq!(*got, avery_data());
                }
                if done {
                    assert!(old.is_none());
                    consistent(img, true);
                }
            });
            cut_everywhere(&stale(full_dir_image("mkfs_s1")), grow, &|img, done| {
                let mut vol = mount(img.to_vec()).unwrap();
                let old = read_file(&mut vol, "README.TXT").ok();
                let new = read_file(&mut vol, "VUX9K/README.TXT").ok();
                assert!(old.is_some() || new.is_some());
                assert!(old.iter().chain(&new).all(|d| d == b"hello\n"));
                if done {
                    assert!(old.is_none());
                }
            });
        }
    }
}
