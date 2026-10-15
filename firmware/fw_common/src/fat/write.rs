// Copyright (c) 2026 Takayuki Nagata
// SPDX-License-Identifier: MIT

//! The writes an update of the second stage needs (feature `fat-write`): create a file in
//! an existing directory, rename and remove one; with feature `fat-full` also make and
//! remove (empty) directories, move a file to another directory and count the free
//! clusters. Every FAT copy gets each change, and
//! FSInfo's free count and next-free hint are marked unknown before the first change, so
//! nothing has to keep them right. Times are fixed at 1980-01-01 00:00 (no clock).
//!
//! Order for power loss: a new file's data and chain reach the card before its directory
//! entry, and a removed file's entry goes before its chain, so an interrupted change
//! leaves at most clusters no entry uses (which fsck reports, and nothing reads).
//! The same for directories: a new one's cluster is zeroed, given `.` and `..` and
//! linked (end of chain) before the parent's entry for it is written; a removed one's
//! entry goes before its cluster chain. A moved file gets its entry in the destination
//! before the source's is removed (its chain stays where it is), so a cut leaves it
//! under both names at worst, never under none.

use super::dir::{DirEntry, ATTR_DIR, DELETED, ENTRY};
#[cfg(feature = "fat-full")]
use super::names::{DOT, DOTDOT};
use super::{le32, read, BlockDevice, FatError, Volume, ENTRY_MASK, EOC_MIN, SECTOR};

const ATTR_ARCHIVE: u8 = 0x20;
/// 1980-01-01 as a FAT date.
const DATE_1980: u16 = (1 << 5) | 1;
const FSINFO_LEAD: u32 = 0x4161_5252;
const FSINFO_STRUCT: u32 = 0x6141_7272;

impl<D: BlockDevice> Volume<D> {
    fn write_sector(&mut self, lba: u32, which: Buf) -> Result<(), FatError> {
        let buf = match which {
            Buf::Data => &self.buf,
            Buf::Fat => &self.fat_buf,
        };
        if self.dev.write(lba, buf) {
            Ok(())
        } else {
            Err(FatError::Io(lba))
        }
    }

    /// Write the cached FAT sector to every FAT copy, if it changed.
    pub(crate) fn flush_fat(&mut self) -> Result<(), FatError> {
        if self.fat_dirty {
            for copy in 0..self.num_fats {
                // num_fats is 1 or 2: the second copy follows the first
                let base = if copy == 0 {
                    self.fat_lba
                } else {
                    self.fat_lba + self.fat_sectors
                };
                self.write_sector(base + self.fat_cached, Buf::Fat)?;
            }
            self.fat_dirty = false;
        }
        Ok(())
    }

    /// Set cluster `c`'s FAT entry (its top 4 bits are reserved and kept).
    fn set_fat(&mut self, c: u32, value: u32) -> Result<(), FatError> {
        self.load_fat(c >> 7)?;
        let at = ((c & 127) << 2) as usize;
        let old = le32(&self.fat_buf, at);
        self.fat_buf[at..at + 4].copy_from_slice(&((old & !ENTRY_MASK) | value).to_le_bytes());
        self.fat_dirty = true;
        Ok(())
    }

    /// Before the first change: FSInfo's free count and next-free hint to unknown (when
    /// the volume has a valid FSInfo sector).
    fn begin_change(&mut self) -> Result<(), FatError> {
        if self.fsinfo_done {
            return Ok(());
        }
        self.fsinfo_done = true;
        let lba = self.fsinfo_lba;
        if lba <= self.part_lba || lba >= self.fat_lba {
            return Ok(());
        }
        read(&mut self.dev, lba, &mut self.buf)?;
        if le32(&self.buf, 0) != FSINFO_LEAD || le32(&self.buf, 484) != FSINFO_STRUCT {
            return Ok(());
        }
        // Free count and next free: 0xFFFFFFFF, unknown
        self.buf[488..496].copy_from_slice(&[0xFF; 8]);
        self.write_sector(lba, Buf::Data)
    }

    /// A free cluster, marked end-of-chain and linked after `prev`.
    fn alloc_cluster(&mut self, prev: Option<u32>) -> Result<u32, FatError> {
        let mut c = self.next_free;
        for _ in 2..self.cluster_end {
            if c >= self.cluster_end {
                c = 2;
            }
            if self.fat_entry(c)? == 0 {
                self.set_fat(c, ENTRY_MASK)?;
                if let Some(p) = prev {
                    self.set_fat(p, c)?;
                }
                self.next_free = c + 1;
                return Ok(c);
            }
            c += 1;
        }
        Err(FatError::DiskFull)
    }

    /// Free a chain from `first` to its end. A looping chain comes back to a cluster
    /// this has freed already (entry 0, not a cluster), so the loop ends.
    fn free_chain(&mut self, first: u32) -> Result<(), FatError> {
        let mut c = first;
        loop {
            if !self.is_cluster(c) {
                return Err(FatError::BadChain(c));
            }
            let next = self.fat_entry(c)?;
            self.set_fat(c, 0)?;
            if next >= EOC_MIN {
                return self.flush_fat();
            }
            c = next;
        }
    }

    /// The entry slot after (lba, index) in the same directory.
    fn next_slot(&mut self, lba: u32, index: u8) -> Result<(u32, u8), FatError> {
        if (index as usize + 1) < SECTOR / ENTRY {
            return Ok((lba, index + 1));
        }
        let rel = lba - self.data_lba;
        if (rel + 1) & ((1 << self.cluster_shift) - 1) != 0 {
            return Ok((lba + 1, 0));
        }
        let c = (rel >> self.cluster_shift) + 2;
        let next = self.next_cluster(c)?.ok_or(FatError::BadChain(c))?;
        Ok((self.cluster_lba(next), 0))
    }

    /// Mark the slots from `entry`'s long-name run up to `entry` itself deleted, but leave
    /// `entry` when `keep` (a rename).
    fn delete_slots(&mut self, entry: &DirEntry, keep: bool) -> Result<(), FatError> {
        let (mut lba, mut index) = (entry.lfn_lba, entry.lfn_index);
        loop {
            let last = (lba, index) == (entry.lba, entry.index);
            if last && keep {
                return Ok(());
            }
            read(&mut self.dev, lba, &mut self.buf)?;
            self.buf[index as usize * ENTRY] = DELETED;
            self.write_sector(lba, Buf::Data)?;
            if last {
                return Ok(());
            }
            (lba, index) = self.next_slot(lba, index)?;
        }
    }

    /// A free entry slot in `dir`: the first deleted or never-used one, or the first of a
    /// zeroed cluster added to the directory.
    fn free_slot(&mut self, dir: &DirEntry) -> Result<(u32, u8), FatError> {
        let last =
            match self.walk_slots(dir.cluster, &mut |_, _, e| e[0] == 0 || e[0] == DELETED)? {
                Ok(slot) => return Ok(slot),
                Err(last) => last,
            };
        // Directory full: one more cluster, zeroed (all entries unused) before the link to
        // it reaches the card. The link waits in the FAT cache: writing data doesn't touch
        // it, and alloc_cluster sets `last`'s entry last.
        let new = self.alloc_cluster(Some(last))?;
        self.buf = [0; SECTOR];
        let lba = self.cluster_lba(new);
        for s in 0..1u32 << self.cluster_shift {
            self.write_sector(lba + s, Buf::Data)?;
        }
        self.flush_fat()?;
        Ok((lba, 0))
    }

    /// Write `data` into newly allocated clusters; the first one (0 for no data). On
    /// failure the clusters taken so far are given back where the card allows.
    fn write_chain(&mut self, data: &[u8]) -> Result<u32, FatError> {
        let mut first = 0;
        let mut prev = None;
        let result = (|| {
            for cluster in data.chunks(SECTOR << self.cluster_shift) {
                let c = self.alloc_cluster(prev)?;
                if first == 0 {
                    first = c;
                }
                prev = Some(c);
                let lba = self.cluster_lba(c);
                for (s, chunk) in cluster.chunks(SECTOR).enumerate() {
                    self.buf[..chunk.len()].copy_from_slice(chunk);
                    self.buf[chunk.len()..].fill(0);
                    self.write_sector(lba + s as u32, Buf::Data)?;
                }
            }
            self.flush_fat()
        })();
        match result {
            Ok(()) => Ok(first),
            Err(e) => {
                if first != 0 {
                    let _ = self.free_chain(first);
                }
                Err(e)
            }
        }
    }

    /// Create the file `name` (8.3, as `to_short_name` makes it) in directory `dir` with
    /// `data`. The data and its chain are written before the entry.
    pub fn create(
        &mut self,
        dir: &DirEntry,
        name: &[u8; 11],
        data: &[u8],
    ) -> Result<DirEntry, FatError> {
        if self.find(dir, name)?.is_some() {
            return Err(FatError::Exists);
        }
        self.begin_change()?;
        let first = self.write_chain(data)?;
        let (lba, index) = match self.free_slot(dir) {
            Ok(slot) => slot,
            Err(e) => {
                // No entry will point to the data: give it back where the card allows
                if first != 0 {
                    let _ = self.free_chain(first);
                }
                return Err(e);
            }
        };
        self.write_entry(
            lba,
            index,
            &entry_bytes(name, ATTR_ARCHIVE, first, data.len() as u32),
        )?;
        Ok(DirEntry {
            name: *name,
            attr: ATTR_ARCHIVE,
            cluster: first,
            size: data.len() as u32,
            lba,
            index,
            lfn_lba: lba,
            lfn_index: index,
        })
    }

    /// Put the 32-byte entry `raw` into slot (lba, index).
    fn write_entry(&mut self, lba: u32, index: u8, raw: &[u8; ENTRY]) -> Result<(), FatError> {
        read(&mut self.dev, lba, &mut self.buf)?;
        self.buf[index as usize * ENTRY..(index as usize + 1) * ENTRY].copy_from_slice(raw);
        self.write_sector(lba, Buf::Data)
    }

    /// Rename a file within its directory (`dir`, where `entry` was found) to `name`.
    /// A long name it had is removed.
    pub fn rename(
        &mut self,
        dir: &DirEntry,
        entry: &DirEntry,
        name: &[u8; 11],
    ) -> Result<(), FatError> {
        if entry.attr & ATTR_DIR != 0 {
            return Err(FatError::WrongType);
        }
        if self.find(dir, name)?.is_some() {
            return Err(FatError::Exists);
        }
        self.begin_change()?;
        self.delete_slots(entry, true)?;
        read(&mut self.dev, entry.lba, &mut self.buf)?;
        let at = entry.index as usize * ENTRY;
        self.buf[at..at + 11].copy_from_slice(name);
        self.write_sector(entry.lba, Buf::Data)
    }

    /// Remove a file: its entry (and long name), then its clusters.
    pub fn remove(&mut self, entry: &DirEntry) -> Result<(), FatError> {
        if entry.attr & ATTR_DIR != 0 {
            return Err(FatError::WrongType);
        }
        self.begin_change()?;
        self.delete_slots(entry, false)?;
        if entry.cluster != 0 {
            self.free_chain(entry.cluster)?;
        }
        Ok(())
    }
}

/// The name `Volume::replace` writes a new file under before it takes the old one's place.
#[cfg(feature = "fat-full")]
pub const REPLACE_TMP: [u8; 11] = *b"~PUT    TMP";

#[cfg(feature = "fat-full")]
impl<D: BlockDevice> Volume<D> {
    /// Make the directory `name` (8.3) in directory `parent`: one cluster, zeroed, with
    /// `.` and `..` (cluster 0 for the root), linked before `parent`'s entry for it.
    pub fn mkdir(&mut self, parent: &DirEntry, name: &[u8; 11]) -> Result<DirEntry, FatError> {
        if !parent.is_dir() {
            return Err(FatError::WrongType);
        }
        if self.find(parent, name)?.is_some() {
            return Err(FatError::Exists);
        }
        self.begin_change()?;
        let c = self.alloc_cluster(None)?;
        let up = if parent.cluster == self.root {
            0
        } else {
            parent.cluster
        };
        let slot = (|| {
            self.buf = [0; SECTOR];
            let lba = self.cluster_lba(c);
            for s in 1..1u32 << self.cluster_shift {
                self.write_sector(lba + s, Buf::Data)?;
            }
            self.buf[..ENTRY].copy_from_slice(&entry_bytes(&DOT, ATTR_DIR, c, 0));
            self.buf[ENTRY..2 * ENTRY].copy_from_slice(&entry_bytes(&DOTDOT, ATTR_DIR, up, 0));
            self.write_sector(lba, Buf::Data)?;
            self.flush_fat()?;
            let (lba, index) = self.free_slot(parent)?;
            self.write_entry(lba, index, &entry_bytes(name, ATTR_DIR, c, 0))?;
            Ok((lba, index))
        })();
        match slot {
            Ok((lba, index)) => Ok(DirEntry {
                name: *name,
                attr: ATTR_DIR,
                cluster: c,
                size: 0,
                lba,
                index,
                lfn_lba: lba,
                lfn_index: index,
            }),
            Err(e) => {
                // No entry points to the cluster: give it back where the card allows
                let _ = self.free_chain(c);
                Err(e)
            }
        }
    }

    /// Remove the directory `entry` if it holds nothing but `.` and `..` (`NotEmpty`
    /// else; `BadName` for the root, `.` and `..`): its entry (and long name), then its
    /// clusters.
    pub fn rmdir(&mut self, entry: &DirEntry) -> Result<(), FatError> {
        if !entry.is_dir() {
            return Err(FatError::WrongType);
        }
        // The root has no entry (LBA 0 is the MBR's)
        if entry.lba == 0 || entry.name[0] == b'.' {
            return Err(FatError::BadName);
        }
        if self
            .find_entry(entry, &mut |e| e.name != DOT && e.name != DOTDOT)?
            .is_some()
        {
            return Err(FatError::NotEmpty);
        }
        self.begin_change()?;
        self.delete_slots(entry, false)?;
        self.free_chain(entry.cluster)
    }

    /// Write `data` as the file `name` (8.3) in `dir`, replacing a file of that name.
    /// The old file stays until the new one is on the card: the data is written as
    /// `REPLACE_TMP` (one left by a cut replace is removed first), then the old file is
    /// removed and the new one renamed. A full card fails with the old file intact; a cut
    /// leaves the old file, or the new data as `~PUT.TMP`.
    pub fn replace(
        &mut self,
        dir: &DirEntry,
        name: &[u8; 11],
        data: &[u8],
    ) -> Result<DirEntry, FatError> {
        if self.find(dir, name)?.is_some_and(|e| e.is_dir()) {
            return Err(FatError::WrongType);
        }
        if let Some(stale) = self.find(dir, &REPLACE_TMP)? {
            self.remove(&stale)?;
        }
        if *name == REPLACE_TMP {
            return self.create(dir, name, data);
        }
        let new = self.create(dir, &REPLACE_TMP, data)?;
        if let Some(old) = self.find(dir, name)? {
            self.remove(&old)?;
        }
        self.rename(dir, &new, name)?;
        Ok(DirEntry { name: *name, ..new })
    }

    /// Move the file `entry` from directory `src_dir` (where it was found) to `dst_dir`
    /// as `name` (8.3; a long name it had is dropped). Its clusters stay; the new entry is
    /// written before the old one is removed. Within one directory it is a rename.
    pub fn move_to(
        &mut self,
        src_dir: &DirEntry,
        entry: &DirEntry,
        dst_dir: &DirEntry,
        name: &[u8; 11],
    ) -> Result<DirEntry, FatError> {
        if entry.is_dir() || !dst_dir.is_dir() {
            return Err(FatError::WrongType);
        }
        if src_dir.cluster == dst_dir.cluster {
            self.rename(src_dir, entry, name)?;
            return Ok(DirEntry {
                name: *name,
                lfn_lba: entry.lba,
                lfn_index: entry.index,
                ..*entry
            });
        }
        if self.find(dst_dir, name)?.is_some() {
            return Err(FatError::Exists);
        }
        self.begin_change()?;
        // The entry as it is (attributes, times, chain, size) under the new name
        read(&mut self.dev, entry.lba, &mut self.buf)?;
        let at = entry.index as usize * ENTRY;
        let mut raw = [0u8; ENTRY];
        raw.copy_from_slice(&self.buf[at..at + ENTRY]);
        raw[..11].copy_from_slice(name);
        let (lba, index) = self.free_slot(dst_dir)?;
        self.write_entry(lba, index, &raw)?;
        self.delete_slots(entry, false)?;
        Ok(DirEntry {
            name: *name,
            lba,
            index,
            lfn_lba: lba,
            lfn_index: index,
            ..*entry
        })
    }

    /// The free clusters, counted in the FAT (FSInfo's count isn't kept). A FAT sector at
    /// a time: through `fat_entry` it took ~5,000 cycles a cluster from the PSRAM.
    pub fn free_clusters(&mut self) -> Result<u32, FatError> {
        let mut n = 0;
        let mut c = 2;
        while c < self.cluster_end {
            self.load_fat(c >> 7)?;
            let end = ((c | 127) + 1).min(self.cluster_end);
            let at = ((c & 127) << 2) as usize;
            let len = ((end - c) << 2) as usize;
            for e in self.fat_buf[at..at + len].chunks_exact(4) {
                // A free entry's 28 bits are 0
                if e[0] | e[1] | e[2] | (e[3] & 0x0F) == 0 {
                    n += 1;
                }
            }
            c = end;
        }
        Ok(n)
    }
}

/// A new directory entry: `name`, `attr`, first cluster, size, dated 1980-01-01.
fn entry_bytes(name: &[u8; 11], attr: u8, first: u32, size: u32) -> [u8; ENTRY] {
    let mut e = [0u8; ENTRY];
    e[..11].copy_from_slice(name);
    e[11] = attr;
    for at in [16, 18, 24] {
        // creation, access and write dates
        e[at..at + 2].copy_from_slice(&DATE_1980.to_le_bytes());
    }
    e[20..22].copy_from_slice(&((first >> 16) as u16).to_le_bytes());
    e[26..28].copy_from_slice(&(first as u16).to_le_bytes());
    e[28..32].copy_from_slice(&size.to_le_bytes());
    e
}

/// Which buffer `write_sector` writes.
enum Buf {
    Data,
    Fat,
}
