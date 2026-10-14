// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! FAT32 on an SD card, without allocation: the first partition of an MBR, 8.3 names
//! (long-name entries are skipped), files read by following their cluster chains.
//! Feature `fat-write` adds the few writes an update needs (`write`: create, rename and
//! remove files, all FAT copies kept equal, FSInfo's counts marked unknown) and the
//! second stage's install (`stage2::install`). Feature `fat-full` (with `fat-write`) adds
//! what a file manager needs (`names`: long names read, listed and looked up, paths with
//! `.` and `..` resolved from a working directory; `write`: directories made and removed,
//! files moved between them, free space counted); long names are only read, everything
//! written gets an 8.3 name.
//!
//! A volume is FAT32 when its BPB says so (`FATSz16 == 0`), whatever its cluster count,
//! so small test images work; FAT16 and exFAT are refused (`NotFat32`). No multiply or
//! divide (RV32I has neither): sectors per cluster is a power of two, and cluster
//! arithmetic shifts by it. tests/fat_images/ holds images mkfs.fat and mtools made
//! (sim/emu/test_fat_images.py), which the host tests read.

mod dir;
#[cfg(feature = "fat-full")]
mod names;
pub mod stage2;
#[cfg(feature = "fat-write")]
mod write;
pub use dir::{to_short_name, DirEntry, ATTR_DIR, ATTR_LFN, ATTR_VOLUME};

pub const SECTOR: usize = 512;

/// The card, one 512-byte sector at a time. Both return false on a card error.
pub trait BlockDevice {
    fn read(&mut self, lba: u32, buf: &mut [u8; SECTOR]) -> bool;
    fn write(&mut self, lba: u32, buf: &[u8; SECTOR]) -> bool;
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum FatError {
    /// The card failed to read or write this sector.
    Io(u32),
    /// No 0x55AA signature in sector 0 or the boot sector.
    NoSignature,
    /// The first partition entry is empty.
    NoPartition,
    /// A FAT16, exFAT or other partition, or a FAT16 BPB.
    NotFat32,
    /// A BPB field out of range (sector size, cluster size, FAT count or size).
    BadBpb,
    /// A cluster chain that leaves the volume, ends early, runs past the file's size or
    /// loops; names the cluster where it went wrong.
    BadChain(u32),
    /// A path component that is not a valid 8.3 name (or, for `rmdir`, the root, `.` or
    /// `..`).
    BadName,
    NotFound,
    /// A directory where a file was expected, or the reverse.
    WrongType,
    /// The destination is smaller than the file.
    TooBig,
    /// A file to be created or renamed to already exists.
    Exists,
    /// No free cluster left.
    DiskFull,
    /// A file read back differently from what was written.
    Verify,
    /// A directory to be removed still holds entries besides `.` and `..`. (Only with
    /// `fat-full`: the first stage's messages stay as they are.)
    #[cfg(feature = "fat-full")]
    NotEmpty,
}

/// FAT32 entries are 28 bits; values from here on end a chain.
const EOC_MIN: u32 = 0x0FFF_FFF8;
const ENTRY_MASK: u32 = 0x0FFF_FFFF;
/// Clusters 2..=0x0FFF_FFF6 can exist.
const MAX_CLUSTERS: u32 = 0x0FFF_FFF5;

fn le16(b: &[u8], at: usize) -> u32 {
    u16::from_le_bytes([b[at], b[at + 1]]) as u32
}

fn le32(b: &[u8], at: usize) -> u32 {
    u32::from_le_bytes([b[at], b[at + 1], b[at + 2], b[at + 3]])
}

/// A mounted FAT32 volume on `D`, with a sector buffer for data and directories and a
/// one-sector cache of the FAT.
pub struct Volume<D: BlockDevice> {
    dev: D,
    /// LBA of the boot sector (the partition's first).
    part_lba: u32,
    fat_lba: u32,
    #[cfg_attr(not(feature = "fat-write"), allow(dead_code))]
    fat_sectors: u32,
    #[cfg_attr(not(feature = "fat-write"), allow(dead_code))]
    num_fats: u32,
    data_lba: u32,
    /// log2 of the sectors per cluster.
    cluster_shift: u32,
    /// Clusters 2..cluster_end exist.
    cluster_end: u32,
    root: u32,
    #[cfg_attr(not(feature = "fat-write"), allow(dead_code))]
    fsinfo_lba: u32,
    buf: [u8; SECTOR],
    fat_buf: [u8; SECTOR],
    /// FAT sector (relative to the FAT) in `fat_buf`, or u32::MAX.
    fat_cached: u32,
    /// `fat_buf` holds changes not yet on the card (written to every FAT copy).
    #[cfg(feature = "fat-write")]
    fat_dirty: bool,
    /// Where the next free-cluster search starts.
    #[cfg(feature = "fat-write")]
    next_free: u32,
    /// FSInfo's counts are marked unknown (once, before the first change).
    #[cfg(feature = "fat-write")]
    fsinfo_done: bool,
}

impl<D: BlockDevice> Volume<D> {
    /// Mount the first partition of the card's MBR.
    pub fn mount(mut dev: D) -> Result<Self, FatError> {
        let mut buf = [0u8; SECTOR];
        read(&mut dev, 0, &mut buf)?;
        if buf[510..512] != [0x55, 0xAA] {
            return Err(FatError::NoSignature);
        }
        let entry = &buf[446..462];
        let part_lba = match entry[4] {
            0 => return Err(FatError::NoPartition),
            0x0B | 0x0C => le32(entry, 8),
            _ => return Err(FatError::NotFat32),
        };
        read(&mut dev, part_lba, &mut buf)?;
        if buf[510..512] != [0x55, 0xAA] {
            return Err(FatError::NoSignature);
        }
        let bytes_per_sector = le16(&buf, 11);
        let cluster_sectors = buf[13] as u32;
        let reserved = le16(&buf, 14);
        let num_fats = buf[16] as u32;
        if le16(&buf, 22) != 0 || le16(&buf, 17) != 0 {
            // FATSz16 or a fixed root directory: FAT12/16
            return Err(FatError::NotFat32);
        }
        // TotSec16 when it fits (mkfs.fat writes it so for small volumes), else TotSec32
        let total = match le16(&buf, 19) {
            0 => le32(&buf, 32),
            small => small,
        };
        let fat_sectors = le32(&buf, 36);
        if bytes_per_sector != SECTOR as u32
            || !cluster_sectors.is_power_of_two()
            || reserved == 0
            || !(1..=2).contains(&num_fats)
            || fat_sectors == 0
        {
            return Err(FatError::BadBpb);
        }
        let cluster_shift = cluster_sectors.trailing_zeros();
        // num_fats is 1 or 2: a shift, not a multiply
        let meta = reserved as u64 + ((fat_sectors as u64) << (num_fats - 1));
        if meta >= total as u64 {
            return Err(FatError::BadBpb);
        }
        let clusters = (total - meta as u32) >> cluster_shift;
        // Cluster numbers from 0x0FFFFFF7 on are the bad-cluster and end-of-chain marks;
        // a FAT that isn't mirrored (ExtFlags bit 7: one active copy) isn't supported
        if clusters > MAX_CLUSTERS || buf[40] & 0x80 != 0 {
            return Err(FatError::BadBpb);
        }
        // The FAT must have an entry for every cluster (128 per sector), and the data
        // area must end on the card's 32-bit LBAs
        if (fat_sectors as u64) << 7 < clusters as u64 + 2
            || part_lba as u64 + total as u64 > u32::MAX as u64
        {
            return Err(FatError::BadBpb);
        }
        let vol = Volume {
            part_lba,
            fat_lba: part_lba + reserved,
            fat_sectors,
            num_fats,
            data_lba: part_lba + meta as u32,
            cluster_shift,
            cluster_end: clusters + 2,
            root: le32(&buf, 44),
            fsinfo_lba: part_lba + le16(&buf, 48),
            dev,
            buf,
            fat_buf: [0; SECTOR],
            fat_cached: u32::MAX,
            #[cfg(feature = "fat-write")]
            fat_dirty: false,
            #[cfg(feature = "fat-write")]
            next_free: 2,
            #[cfg(feature = "fat-write")]
            fsinfo_done: false,
        };
        if !vol.is_cluster(vol.root) {
            return Err(FatError::BadBpb);
        }
        Ok(vol)
    }

    /// The card, for what the volume doesn't cover (and to unmount).
    pub fn device(&mut self) -> &mut D {
        &mut self.dev
    }

    /// LBA of the partition's boot sector.
    pub fn partition_lba(&self) -> u32 {
        self.part_lba
    }

    /// Bytes per cluster.
    pub fn cluster_bytes(&self) -> u32 {
        (SECTOR as u32) << self.cluster_shift
    }

    fn is_cluster(&self, c: u32) -> bool {
        c >= 2 && c < self.cluster_end
    }

    fn cluster_lba(&self, c: u32) -> u32 {
        self.data_lba + ((c - 2) << self.cluster_shift)
    }

    /// Bring FAT sector `index` into `fat_buf`.
    fn load_fat(&mut self, index: u32) -> Result<(), FatError> {
        if self.fat_cached != index {
            #[cfg(feature = "fat-write")]
            self.flush_fat()?;
            self.fat_cached = u32::MAX;
            read(&mut self.dev, self.fat_lba + index, &mut self.fat_buf)?;
            self.fat_cached = index;
        }
        Ok(())
    }

    /// The FAT entry of cluster `c` (28 bits).
    fn fat_entry(&mut self, c: u32) -> Result<u32, FatError> {
        self.load_fat(c >> 7)?;
        Ok(le32(&self.fat_buf, ((c & 127) << 2) as usize) & ENTRY_MASK)
    }

    /// The cluster after `c` in its chain: Some(next), or None at the chain's end.
    fn next_cluster(&mut self, c: u32) -> Result<Option<u32>, FatError> {
        let e = self.fat_entry(c)?;
        if e >= EOC_MIN {
            Ok(None)
        } else if self.is_cluster(e) {
            Ok(Some(e))
        } else {
            Err(FatError::BadChain(c))
        }
    }

    /// The root directory.
    pub fn root(&self) -> DirEntry {
        DirEntry::root(self.root)
    }

    /// Follow a `/`-separated path of 8.3 names from the root (a leading `/` or `\` and
    /// either separator are accepted; case doesn't matter).
    pub fn open(&mut self, path: &str) -> Result<DirEntry, FatError> {
        let mut at = self.root();
        // Byte-wise rather than str::split: smaller firmware
        for part in path.as_bytes().split(|&c| c == b'/' || c == b'\\') {
            if part.is_empty() {
                continue;
            }
            if !at.is_dir() {
                return Err(FatError::WrongType);
            }
            let name = dir::short_name(part).ok_or(FatError::BadName)?;
            at = self.find(&at, &name)?.ok_or(FatError::NotFound)?;
        }
        Ok(at)
    }

    /// Call `f` with each sector of a file's data, in order, cut to the file's size: the
    /// chain must hold exactly the clusters the size needs.
    /// (`f` is a trait object, so each caller doesn't get its own copy of the loop.)
    pub fn for_each_sector(
        &mut self,
        file: &DirEntry,
        f: &mut dyn FnMut(&[u8]),
    ) -> Result<(), FatError> {
        if file.is_dir() {
            return Err(FatError::WrongType);
        }
        let mut rest = file.size;
        let mut c = file.cluster;
        if rest == 0 {
            return if c == 0 {
                Ok(())
            } else {
                Err(FatError::BadChain(c))
            };
        }
        let sectors = 1u32 << self.cluster_shift;
        loop {
            if !self.is_cluster(c) {
                return Err(FatError::BadChain(c));
            }
            let lba = self.cluster_lba(c);
            for s in 0..sectors {
                read(&mut self.dev, lba + s, &mut self.buf)?;
                let n = rest.min(SECTOR as u32);
                f(&self.buf[..n as usize]);
                rest -= n;
                if rest == 0 {
                    // The chain must end here
                    return match self.next_cluster(c)? {
                        None => Ok(()),
                        Some(_) => Err(FatError::BadChain(c)),
                    };
                }
            }
            // Every step consumes a cluster of `rest`, so a looping chain runs past the
            // size instead of forever
            c = self.next_cluster(c)?.ok_or(FatError::BadChain(c))?;
        }
    }

    /// Read a whole file into the start of `dst`; returns its size.
    pub fn read_to(&mut self, file: &DirEntry, dst: &mut [u8]) -> Result<usize, FatError> {
        if file.size as usize > dst.len() {
            return Err(FatError::TooBig);
        }
        let mut at = 0;
        self.for_each_sector(file, &mut |data| {
            dst[at..at + data.len()].copy_from_slice(data);
            at += data.len();
        })?;
        Ok(at)
    }
}

fn read<D: BlockDevice>(dev: &mut D, lba: u32, buf: &mut [u8; SECTOR]) -> Result<(), FatError> {
    if dev.read(lba, buf) {
        Ok(())
    } else {
        Err(FatError::Io(lba))
    }
}
