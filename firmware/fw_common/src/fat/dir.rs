// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! Directories: 32-byte entries in a cluster chain, looked up by 8.3 name.

use super::{le16, le32, read, BlockDevice, FatError, Volume, SECTOR};

pub const ATTR_VOLUME: u8 = 0x08;
pub const ATTR_DIR: u8 = 0x10;
/// A long-name entry (read-only + hidden + system + volume).
pub const ATTR_LFN: u8 = 0x0F;
const ENTRY: usize = 32;
const DELETED: u8 = 0xE5;

/// A file or directory, as its directory entry describes it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct DirEntry {
    /// 8.3 name, space-padded, without the dot.
    pub name: [u8; 11],
    pub attr: u8,
    /// First cluster (0 for an empty file).
    pub cluster: u32,
    pub size: u32,
    /// Where the entry is: its sector and index there (the root has none).
    pub(crate) lba: u32,
    pub(crate) index: u8,
}

impl DirEntry {
    pub(crate) fn root(cluster: u32) -> DirEntry {
        DirEntry {
            name: *b"           ",
            attr: ATTR_DIR,
            cluster,
            size: 0,
            lba: 0,
            index: 0,
        }
    }

    fn parse(e: &[u8], lba: u32, index: u8) -> DirEntry {
        let mut name = [0u8; 11];
        name.copy_from_slice(&e[..11]);
        DirEntry {
            name,
            attr: e[11],
            cluster: (le16(e, 20) << 16) | le16(e, 26),
            size: le32(e, 28),
            lba,
            index,
        }
    }

    pub fn is_dir(&self) -> bool {
        self.attr & ATTR_DIR != 0
    }

    /// The name as `NAME.EXT` (no dot without an extension) in `out`; returns its length.
    pub fn display_name(&self, out: &mut [u8; 12]) -> usize {
        let mut n = 0;
        for (i, &c) in self.name.iter().enumerate() {
            if c == b' ' {
                continue;
            }
            if i >= 8 && n > 0 && !out[..n].contains(&b'.') {
                out[n] = b'.';
                n += 1;
            }
            out[n] = c;
            n += 1;
        }
        n
    }
}

/// `name` as a directory entry's 11-byte 8.3 name (upper case, space-padded), if it is
/// one: 1-8 characters, optionally a dot and 1-3 more, from A-Z, 0-9 and
/// `! # $ % & ' ( ) - @ ^ _ ` { } ~` (lower case is folded).
pub fn to_short_name(name: &str) -> Option<[u8; 11]> {
    let mut out = [b' '; 11];
    let (base, ext) = match name.rfind('.') {
        Some(dot) => (&name[..dot], Some(&name[dot + 1..])),
        None => (name, None),
    };
    if base.is_empty() || base.len() > 8 || ext.is_some_and(|e| e.is_empty() || e.len() > 3) {
        return None;
    }
    let (out_base, out_ext) = out.split_at_mut(8);
    for (dst, src) in [(out_base, base), (out_ext, ext.unwrap_or(""))] {
        for (d, c) in dst.iter_mut().zip(src.bytes()) {
            let c = c.to_ascii_uppercase();
            if !(c.is_ascii_uppercase() || c.is_ascii_digit() || b"!#$%&'()-@^_`{}~".contains(&c)) {
                return None;
            }
            *d = c;
        }
    }
    Some(out)
}

impl<D: BlockDevice> Volume<D> {
    /// Call `f` with each entry of directory `dir` (skipping long-name, volume-label and
    /// deleted entries) until it returns true; returns that entry. (`f` is a trait object,
    /// so each caller doesn't get its own copy of the loop.)
    pub fn find_entry(
        &mut self,
        dir: &DirEntry,
        f: &mut dyn FnMut(&DirEntry) -> bool,
    ) -> Result<Option<DirEntry>, FatError> {
        if !dir.is_dir() {
            return Err(FatError::WrongType);
        }
        let sectors = 1u32 << self.cluster_shift;
        let mut c = dir.cluster;
        // A directory can't have more clusters than the volume: a looping chain stops here
        for _ in 0..self.cluster_end {
            if !self.is_cluster(c) {
                return Err(FatError::BadChain(c));
            }
            let lba = self.cluster_lba(c);
            for s in 0..sectors {
                read(&mut self.dev, lba + s, &mut self.buf)?;
                for i in 0..SECTOR / ENTRY {
                    let e = &self.buf[i * ENTRY..(i + 1) * ENTRY];
                    if e[0] == 0 {
                        return Ok(None);
                    }
                    if e[0] == DELETED || e[11] == ATTR_LFN || e[11] & ATTR_VOLUME != 0 {
                        continue;
                    }
                    let entry = DirEntry::parse(e, lba + s, i as u8);
                    if f(&entry) {
                        return Ok(Some(entry));
                    }
                }
            }
            match self.next_cluster(c)? {
                Some(next) => c = next,
                None => return Ok(None),
            }
        }
        Err(FatError::BadChain(c))
    }

    /// The entry named `name` (8.3, as `to_short_name` makes it) in directory `dir`.
    pub fn find(&mut self, dir: &DirEntry, name: &[u8; 11]) -> Result<Option<DirEntry>, FatError> {
        self.find_entry(dir, &mut |e| e.name == *name)
    }
}
