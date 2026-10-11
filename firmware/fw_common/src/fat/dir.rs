// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! Directories: 32-byte entries in a cluster chain, looked up by 8.3 name.

use super::{le16, le32, read, BlockDevice, FatError, Volume, SECTOR};

pub const ATTR_VOLUME: u8 = 0x08;
pub const ATTR_DIR: u8 = 0x10;
/// A long-name entry (read-only + hidden + system + volume).
pub const ATTR_LFN: u8 = 0x0F;
pub(crate) const ENTRY: usize = 32;
pub(crate) const DELETED: u8 = 0xE5;

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
    /// Where its long-name entries start (the entry itself when it has none).
    pub(crate) lfn_lba: u32,
    pub(crate) lfn_index: u8,
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
            lfn_lba: 0,
            lfn_index: 0,
        }
    }

    /// `lfn`: where the long-name run before it starts, if it has one.
    fn parse(e: &[u8], lba: u32, index: u8, lfn: Option<(u32, u8)>) -> DirEntry {
        let mut name = [0u8; 11];
        name.copy_from_slice(&e[..11]);
        DirEntry {
            name,
            attr: e[11],
            cluster: (le16(e, 20) << 16) | le16(e, 26),
            size: le32(e, 28),
            lba,
            index,
            lfn_lba: lfn.map_or(lba, |l| l.0),
            lfn_index: lfn.map_or(index, |l| l.1),
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

/// The checksum of an 8.3 name that its long-name entries carry.
fn checksum(name: &[u8]) -> u8 {
    name.iter()
        .fold(0u8, |sum, &c| sum.rotate_right(1).wrapping_add(c))
}

/// `name` as a directory entry's 11-byte 8.3 name (upper case, space-padded), if it is
/// one: 1-8 characters, optionally a dot and 1-3 more, from A-Z, 0-9 and
/// `! # $ % & ' ( ) - @ ^ _ ` { } ~` (lower case is folded).
pub fn to_short_name(name: &str) -> Option<[u8; 11]> {
    short_name(name.as_bytes())
}

/// `to_short_name` on bytes, without `str` slicing (whose UTF-8 checks cost firmware size).
pub(crate) fn short_name(name: &[u8]) -> Option<[u8; 11]> {
    let mut out = [b' '; 11];
    // Base name into 0..8, after the last dot into 8..11
    let dot = name.iter().rposition(|&c| c == b'.');
    let (base, ext) = match dot {
        Some(d) => (&name[..d], &name[d + 1..]),
        None => (name, &name[name.len()..]),
    };
    if base.is_empty() || base.len() > 8 || ext.len() > 3 || (dot.is_some() && ext.is_empty()) {
        return None;
    }
    for (at, &c) in base
        .iter()
        .enumerate()
        .chain(ext.iter().enumerate().map(|(i, c)| (8 + i, c)))
    {
        let c = c.to_ascii_uppercase();
        if !(c.is_ascii_uppercase() || c.is_ascii_digit() || b"!#$%&'()-@^_`{}~".contains(&c)) {
            return None;
        }
        out[at] = c;
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
        // The long-name run before the next entry: where it starts, and its checksum
        let mut lfn: Option<(u32, u8, u8)> = None;
        let mut found = None;
        // Where the walk stopped doesn't matter: `found` says whether it was a match
        let _ = self.walk_slots(dir.cluster, &mut |lba, i, e| {
            if e[0] == 0 {
                return true;
            }
            if e[0] == DELETED {
                lfn = None;
                return false;
            }
            if e[11] == ATTR_LFN {
                // A run starts at its last part (bit 6) and shares one checksum
                if e[0] & 0x40 != 0 || lfn.is_none_or(|l| l.2 != e[13]) {
                    lfn = Some((lba, i, e[13]));
                }
                return false;
            }
            let run = lfn.take().filter(|l| l.2 == checksum(&e[..11]));
            if e[11] & ATTR_VOLUME != 0 {
                return false;
            }
            let entry = DirEntry::parse(e, lba, i, run.map(|l| (l.0, l.1)));
            if f(&entry) {
                found = Some(entry);
                return true;
            }
            false
        })?;
        Ok(found)
    }

    /// Call `f(lba, index, entry)` for each 32-byte slot of the directory whose chain
    /// starts at `first`, until it returns true: Ok(Ok(that slot)), or Ok(Err(the last
    /// cluster)) at the end of the chain.
    pub(crate) fn walk_slots(
        &mut self,
        first: u32,
        f: &mut dyn FnMut(u32, u8, &[u8]) -> bool,
    ) -> Result<Result<(u32, u8), u32>, FatError> {
        let sectors = 1u32 << self.cluster_shift;
        let mut c = first;
        // A directory can't have more clusters than the volume: a looping chain stops here
        for _ in 0..self.cluster_end {
            if !self.is_cluster(c) {
                return Err(FatError::BadChain(c));
            }
            let lba = self.cluster_lba(c);
            for s in 0..sectors {
                read(&mut self.dev, lba + s, &mut self.buf)?;
                for i in 0..SECTOR / ENTRY {
                    if f(lba + s, i as u8, &self.buf[i * ENTRY..(i + 1) * ENTRY]) {
                        return Ok(Ok((lba + s, i as u8)));
                    }
                }
            }
            match self.next_cluster(c)? {
                Some(next) => c = next,
                None => return Ok(Err(c)),
            }
        }
        Err(FatError::BadChain(c))
    }

    /// The entry named `name` (8.3, as `to_short_name` makes it) in directory `dir`.
    pub fn find(&mut self, dir: &DirEntry, name: &[u8; 11]) -> Result<Option<DirEntry>, FatError> {
        self.find_entry(dir, &mut |e| e.name == *name)
    }
}
